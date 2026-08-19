"""The ONE on-wire WSS integration test (W3.5 / §T7 integration gate).

A NAMED fixture: a LOCAL relay (real Starlette/uvicorn) + a LOCAL fake "home"
that dials the relay's ``/scope`` over a REAL websockets client, registers with a
device token, then services tunnelled requests by replaying against a tiny ASGI
stand-in. A real ``httpx`` browser request goes through the relay, down the WSS,
back up, and the test asserts the round-trip body + status.

This is a SEPARATE integration gate (run on demand / a dedicated CI job), NOT
part of the fast off-wire unit loop. It SKIPS cleanly when the server/wire deps
(starlette, uvicorn, websockets, httpx) are not installed."""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import pytest

# Gate the whole module on the wire deps being importable.
pytest.importorskip("starlette")
pytest.importorskip("uvicorn")
pytest.importorskip("websockets")
pytest.importorskip("httpx")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402

from relay import protocol  # noqa: E402
from relay.config import RelayConfig  # noqa: E402
from relay.server import create_app  # noqa: E402

DEVICE_TOKEN = "integration-tok"
HOME_ID = "home-int"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _fake_home(relay_port: int, ready: asyncio.Event,
                     stop: asyncio.Event) -> None:
    """A minimal home: dial /scope, HELLO, then for each REQ_OPEN reply with a
    canned RESP_HEAD + RESP_DATA echoing the requested path."""
    url = f"ws://127.0.0.1:{relay_port}/scope"
    async with websockets.connect(url, max_size=2 * 1024 * 1024) as ws:
        await ws.send(protocol.hello(DEVICE_TOKEN, HOME_ID, generation=1).encode())
        ack = protocol.decode(await ws.recv())
        assert ack.header["ok"] is True, ack.header
        ready.set()
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            frame = protocol.decode(raw)
            if frame.type == protocol.FrameType.PING:
                await ws.send(protocol.pong(0.0).encode())
                continue
            if frame.type == protocol.FrameType.REQ_OPEN:
                sid = frame.stream_id
                body = json.dumps({
                    "ok": True,
                    "method": frame.header["method"],
                    "path": frame.header["path"],
                    # prove the scope marks the replayed request remote=True
                    "remote": True,
                }).encode()
                await ws.send(protocol.resp_head(
                    sid, 200, [["content-type", "application/json"]]).encode())
                await ws.send(protocol.resp_data(sid, body, eof=True).encode())
            elif frame.type == protocol.FrameType.REQ_DATA:
                continue  # body chunks ignored by this echo home


@pytest.mark.asyncio
async def test_on_wire_tunnelled_request_round_trip():
    relay_port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=relay_port,
                      origin="relay.test", ping_interval_s=0.2)
    # Provision the device token in the app's registry directly (no secret file).
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
                                           port=relay_port, log_level="warning"))
    server_task = asyncio.ensure_future(server.serve())
    # Wait for uvicorn to come up.
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "relay server did not start"

    ready = asyncio.Event()
    stop = asyncio.Event()
    home_task = asyncio.ensure_future(_fake_home(relay_port, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/status",
                timeout=5,
            )
        assert r.status_code == 200
        payload = r.json()
        assert payload["ok"] is True
        assert payload["path"] == "/api/status"
        assert payload["remote"] is True
    finally:
        stop.set()
        home_task.cancel()
        server.should_exit = True
        await asyncio.gather(server_task, return_exceptions=True)
        await asyncio.gather(home_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_on_wire_unconnected_home_returns_502():
    relay_port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=relay_port)
    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
                                           port=relay_port, log_level="warning"))
    server_task = asyncio.ensure_future(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/nobody/api/status", timeout=5)
        assert r.status_code == 502
    finally:
        server.should_exit = True
        await asyncio.gather(server_task, return_exceptions=True)


async def _silent_home(relay_port: int, ready: asyncio.Event,
                       stop: asyncio.Event) -> None:
    """A home that registers and then NEVER answers a request -- the shape of a
    tunnel that has wedged or died between REQ_OPEN and RESP_HEAD."""
    url = f"ws://127.0.0.1:{relay_port}/scope"
    async with websockets.connect(url, max_size=2 * 1024 * 1024) as ws:
        await ws.send(protocol.hello(DEVICE_TOKEN, HOME_ID, generation=1).encode())
        assert protocol.decode(await ws.recv()).header["ok"] is True
        ready.set()
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if protocol.decode(raw).type == protocol.FrameType.PING:
                await ws.send(protocol.pong(0.0).encode())
            # every other frame, including REQ_OPEN, is dropped on the floor


@pytest.mark.asyncio
async def test_a_home_that_never_answers_gets_a_504_not_a_hung_handler():
    """A SILENT HOME MUST NOT PARK THE RELAY HANDLER FOREVER.

    `await head_ready.wait()` had no timeout, and on a tunnel drop
    TunnelMultiplexer.shutdown() clears `_exchanges`/`req_routes` so `on_head`
    can NEVER fire for a request that was in flight. Every such request leaked
    its task, its fully-buffered body and its chunk queue for the life of the
    process -- on one 512MB shared-cpu machine -- and the browser got no error
    at all, just a tab that spun until the user gave up.
    """
    relay_port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=relay_port,
                      origin="relay.test", ping_interval_s=0.2,
                      upstream_timeout_s=1.0)
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
                                           port=relay_port, log_level="warning"))
    server_task = asyncio.ensure_future(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "relay server did not start"

    ready = asyncio.Event()
    stop = asyncio.Event()
    home_task = asyncio.ensure_future(_silent_home(relay_port, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        started = asyncio.get_running_loop().time()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/status",
                # Comfortably longer than upstream_timeout_s: if the relay does
                # NOT bound its own wait, this raises ReadTimeout instead of
                # returning, which is the bug stated as a failure.
                timeout=10,
            )
        elapsed = asyncio.get_running_loop().time() - started
        assert r.status_code == 504, (
            f"expected 504 from the relay, got {r.status_code} -- the browser "
            "must be told the home did not answer")
        assert elapsed < 5, (
            f"took {elapsed:.1f}s; the relay must give up at its own "
            "upstream_timeout_s (1.0s here), not wait on the client")
    finally:
        stop.set()
        home_task.cancel()
        server.should_exit = True
        # BOUND THE TEARDOWN. Without the fix this test does not fail -- it
        # HANGS: uvicorn's graceful shutdown waits on the parked handler, which
        # by construction never returns. A test that stalls CI forever is worse
        # than one that fails, so give the shutdown a deadline and let the
        # assertion above be the thing that speaks.
        for t in (server_task, home_task):
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(t), timeout=5)
            t.cancel()


@pytest.mark.asyncio
async def test_the_rate_limiter_buckets_are_pruned():
    """RateLimiter.prune() documents itself as "call periodically from the
    server's housekeeping loop" and had NO production caller -- the only
    reference in the repo was its own unit test. Both limiters are keyed by
    client IP, so on a public relay the dicts grew for the life of the process.
    """
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=_free_port())
    app = create_app(cfg)
    state = app.state.relay
    pruned = {"n": 0}
    for limiter in (state.http_limiter, state.ws_limiter):
        orig = limiter.prune

        def _counted(_orig=orig):
            pruned["n"] += 1
            return _orig()

        limiter.prune = _counted

    import relay.server as rs
    real_sleep = asyncio.sleep

    async def _fast_sleep(_d):          # collapse the 60s housekeeping period
        await real_sleep(0)

    rs.asyncio.sleep = _fast_sleep
    try:
        async with app.router.lifespan_context(app):
            for _ in range(50):
                if pruned["n"] >= 2:
                    break
                await real_sleep(0.01)
    finally:
        rs.asyncio.sleep = real_sleep
    assert pruned["n"] >= 2, (
        "the housekeeping loop never pruned either limiter — the IP-keyed "
        "buckets grow unbounded")
