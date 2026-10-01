"""The ONE on-wire WSS integration test (W3.5 / §T7 integration gate).

A NAMED fixture: a LOCAL relay (real Starlette/uvicorn) + a LOCAL fake "home"
that dials the relay's ``/scope`` over a REAL websockets client, registers with a
device token, then services tunnelled requests by replaying against a tiny ASGI
stand-in. A real ``httpx`` browser request goes through the relay, down the WSS,
back up, and the test asserts the round-trip body + status.

This is a SEPARATE integration gate (run on demand / a dedicated CI job), NOT
part of the fast off-wire unit loop. It SKIPS cleanly when the server/wire deps
(starlette, uvicorn, websockets, httpx) are not installed.

KEEPALIVE IN THESE TESTS (#438). The relay, the fake home and the browser
client share ONE event loop here, so any stall of that loop counts against the
relay's PONG deadline (``ping_interval_s x ping_max_misses``): the synchronous
SSL-context build inside ``httpx.AsyncClient()`` (0.28 to 0.43 s per client on
this box, measured 2026-09-28), a GC pause, or the OS descheduling the process
on a loaded machine. The relay's ping loop wakes after the stall, finds the
last PONG older than the deadline, and tears the tunnel down; the browser
request then gets a 502 whose body is "home not connected" or, if the drop
lands mid-request, "home tunnel failed". Both are 18 bytes, which is the
"clean 18-byte response" #438 saw on the truncated-body case. No way of
answering PINGs in the home can win that race, because the home did not run
during the stall either: a dedicated reader task would have been descheduled
with everything else. The file failed this way intermittently under load, at
the 0.2 s interval (0.6 s deadline) every home-bearing test used.

So the tests that are not about keepalive run at ``_QUIET_PING_S``, whose
deadline no stall can reach, and the one test that IS about keepalive runs at
``_KEEPALIVE_PING_S`` on purpose, where lateness only makes its teardown more
certain."""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time

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

# See the module docstring. 30 s x 3 misses is a 90 s deadline, longer than any
# test here runs, so no PING is even due; the stall test below holds the loop
# for _STALL_S, which the old 0.6 s deadline could not survive.
_QUIET_PING_S = 30.0
_KEEPALIVE_PING_S = 0.2
_STALL_S = 1.0
# The bound on every wait that only turns a hang into a failure: uvicorn
# coming up, the home registering, a client request. It costs nothing when
# the thing arrives, so it is set for a loaded machine, where the file ran 4x
# slower (28 s against 7 s) on 2026-09-28, not for an idle one.
_WAIT_S = 30.0


def _cfg(relay_port: int, **overrides) -> RelayConfig:
    """A loopback relay config at the quiet keepalive interval."""
    kw = dict(bind_host="127.0.0.1", bind_port=relay_port,
              origin="relay.test", ping_interval_s=_QUIET_PING_S)
    kw.update(overrides)
    return RelayConfig(**kw)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _start(app) -> tuple:
    """Serve ``app`` on its configured loopback port; return the server and
    its task once uvicorn reports started."""
    port = app.state.relay.cfg.bind_port
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical"))
    task = asyncio.ensure_future(server.serve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WAIT_S
    while not server.started and loop.time() < deadline:
        await asyncio.sleep(0.05)
    assert server.started, "relay server did not start"
    return server, task


async def _stop(server, *tasks) -> None:
    """Bounded teardown, as in the 504 case: a parked handler must fail the
    test, never hang it."""
    server.should_exit = True
    for t in tasks:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(t), timeout=5)
        t.cancel()


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
    cfg = _cfg(relay_port)
    # Provision the device token in the app's registry directly (no secret file).
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    server, server_task = await _start(app)

    ready = asyncio.Event()
    stop = asyncio.Event()
    home_task = asyncio.ensure_future(_fake_home(relay_port, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=_WAIT_S)

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/status",
                timeout=_WAIT_S,
            )
        assert r.status_code == 200
        payload = r.json()
        assert payload["ok"] is True
        assert payload["path"] == "/api/status"
        assert payload["remote"] is True
    finally:
        stop.set()
        await _stop(server, server_task, home_task)


@pytest.mark.asyncio
async def test_on_wire_unconnected_home_returns_502():
    relay_port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=relay_port)
    app = create_app(cfg)
    server, server_task = await _start(app)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/nobody/api/status",
                timeout=_WAIT_S)
        assert r.status_code == 502
    finally:
        await _stop(server, server_task)


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

    The time bound is measured from the request alone and set at half the
    client's timeout (#438). It was ``< 5`` s from before the client was
    built, and under load on 2026-09-28 it failed once in 7 runs with
    ``AssertionError: took 5.1s; the relay must give up at its own
    upstream_timeout_s (1.0s here), not wait on the client`` while the relay
    did answer 504: ``httpx.AsyncClient()`` alone blocks for 0.3 to 0.4 s on
    this box, and a loaded machine runs the relay's 1.0 s timer late. The
    bound exists to catch a relay that waits on the client. Such a relay
    runs to the client's timeout, and half of that timeout still separates
    it from any lateness a loaded machine adds.

    MUTATION "the relay waits on its own long timer" (the head wait's
    ``timeout=state.cfg.upstream_timeout_s`` -> ``timeout=16.0``, past half
    the client's 30 s). Observed, so the widened bound still grades it:
      AssertionError: took 16.0s; the relay must give up at its own
      upstream_timeout_s (1.0s here), not wait on the client
      assert 16.015999999828637 < (30.0 / 2)
    """
    relay_port = _free_port()
    cfg = _cfg(relay_port, upstream_timeout_s=1.0)
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    server, server_task = await _start(app)

    ready = asyncio.Event()
    stop = asyncio.Event()
    home_task = asyncio.ensure_future(_silent_home(relay_port, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=_WAIT_S)
        async with httpx.AsyncClient() as client:
            started = asyncio.get_running_loop().time()
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/status",
                # Far longer than upstream_timeout_s: if the relay does NOT
                # bound its own wait, this raises ReadTimeout instead of
                # returning, which is the bug stated as a failure.
                timeout=_WAIT_S,
            )
            elapsed = asyncio.get_running_loop().time() - started
        assert r.status_code == 504, (
            f"expected 504 from the relay, got {r.status_code} -- the browser "
            "must be told the home did not answer")
        assert elapsed < _WAIT_S / 2, (
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


async def _home_that_stops_mid_body(relay_port: int, ready: asyncio.Event,
                                    stop: asyncio.Event) -> None:
    """Answers with a head promising 40 bytes, sends 10, then goes silent."""
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
            frame = protocol.decode(raw)
            if frame.type == protocol.FrameType.PING:
                await ws.send(protocol.pong(0.0).encode())
            elif frame.type == protocol.FrameType.REQ_OPEN:
                sid = frame.stream_id
                await ws.send(protocol.resp_head(
                    sid, 200, [["content-length", "40"]]).encode())
                await ws.send(protocol.resp_data(sid, b"A" * 10, eof=False).encode())
                # ...and then nothing. Ever.


@pytest.mark.asyncio
async def test_a_body_that_stops_half_way_is_a_FAILED_transfer_not_a_short_one():
    """A TRUNCATED FILE MUST NOT LOOK COMPLETE.

    The body timeout originally `return`ed, which ends a StreamingResponse
    cleanly — the client got a well-formed chunked 200 carrying 10 of the
    promised 40 bytes and raised nothing. A gallery FITS would be saved short and
    a JSON body parsed half, silently. Measured at the wire on 2026-08-19.

    Hanging forever (the behaviour before the timeout existed) was bad; handing
    back a plausible-looking wrong file is worse.
    """
    relay_port = _free_port()
    cfg = _cfg(relay_port, upstream_timeout_s=1.0)
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    server, server_task = await _start(app)

    ready, stop = asyncio.Event(), asyncio.Event()
    home_task = asyncio.ensure_future(_home_that_stops_mid_body(relay_port, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=_WAIT_S)
        failed = False
        status = None
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(
                    f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/big",
                    timeout=_WAIT_S)
                status, body = r.status_code, r.content
            except Exception:
                failed = True          # a broken transfer — the correct outcome
                body = b""
        # The status is in the message because #438's "clean 18-byte
        # response" was a 502 "home not connected" from a dropped tunnel,
        # not a truncation at all, and the message did not say so.
        assert failed or len(body) >= 40, (
            f"got a clean {len(body)}-byte {status} response for a 40-byte "
            f"body — a truncated download that looks complete: {body[:40]!r}")
    finally:
        stop.set()
        await _stop(server, server_task, home_task)


# ------------------------------------------------------------ keepalive (#438)

@pytest.mark.asyncio
async def test_a_stalled_event_loop_does_not_drop_a_healthy_tunnel():
    """#438 MADE DETERMINISTIC. The file failed intermittently under machine
    load with ``assert 502 == 200`` on the round trip. The cause is a stall
    of the loop the relay, the home and the client share (module docstring):
    here ``time.sleep`` blocks it for _STALL_S, exactly as a loaded machine
    did, and then the loop runs for half a second, long enough for any
    keepalive check a sub-second interval would make. A healthy tunnel must
    survive that and serve the request.

    MUTATION "keepalive race restored" (``_QUIET_PING_S = 30.0`` -> ``0.2``,
    the interval every home-bearing test used before). Observed, run from a
    private copy on 2026-09-28, this case red every time:
      AssertionError: a 1.0 s stall of the shared loop dropped a healthy
      tunnel: 502 home not connected
      assert 502 == 200
       +  where 502 = <Response [502 Bad Gateway]>.status_code
    The other home-bearing cases are the intermittency. On a quiet machine
    they stayed green under the mutant (6 passed). With the machine at 100%
    CPU they went red too, and reproduced all three #438 failures:
      test_on_wire_tunnelled_request_round_trip: assert 502 == 200
      test_a_home_that_never_answers...: AssertionError: expected 504 from
        the relay, got 502
      test_a_body_that_stops_half_way...: AssertionError: got a clean
        18-byte 502 response for a 40-byte body ... b'home tunnel failed'
      4 failed, 3 passed

    WHAT THIS GUARDS: THE HARNESS, NOT RELAY CODE (H4, the E item on this
    case). Its one mutant above is a constant of this file, and no relay code
    is graded in the window it tests: at _QUIET_PING_S the relay's
    ``_ping_loop`` is still asleep in its first 30 s interval when the
    request is served, so no keepalive check, lenient or strict, runs during
    the stall or after it. What it holds is that this file's home-bearing
    cases run at an interval no stall of the shared loop can reach, which is
    what made #438 deterministic. The keepalive itself is graded on the wire
    by test_a_home_that_stops_answering_pings_is_torn_down. A relay-side
    mutant confirms it, run from a private copy on 2026-09-29:
    MUTATION "every tunnel is stale" (``ScopeTunnel.is_stale`` in
    relay/registry.py returns True whatever the last PONG). Observed, the
    two cases run together:
      PASSED tests/test_integration_wss.py::test_a_stalled_event_loop_does_not_drop_a_healthy_tunnel
      FAILED tests/test_integration_wss.py::test_a_home_that_stops_answering_pings_is_torn_down
          AssertionError: {'code': 1001, 'pings': 0}
          assert 0 >= 1
      1 failed, 1 passed
    The keepalive case going red shows the mutant was live (the relay hung
    up before its first PING); this case stayed green through it.
    """
    relay_port = _free_port()
    app = create_app(_cfg(relay_port))
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    server, server_task = await _start(app)
    ready, stop = asyncio.Event(), asyncio.Event()
    home_task = asyncio.ensure_future(_fake_home(relay_port, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=_WAIT_S)
        # Blocks the event loop on purpose: nothing runs, not the relay's
        # ping loop, not the home, not the client. This is the load.
        time.sleep(_STALL_S)
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/status",
                timeout=_WAIT_S)
        assert r.status_code == 200, (
            f"a {_STALL_S} s stall of the shared loop dropped a healthy "
            f"tunnel: {r.status_code} {r.text}")
        assert r.json()["path"] == "/api/status"
    finally:
        stop.set()
        await _stop(server, server_task, home_task)


async def _deaf_home(relay_port: int, ready: asyncio.Event,
                     seen: dict) -> None:
    """A home that registers and then never answers a PING: the half-open,
    NAT'd residential socket the keepalive exists to find. Records how many
    PINGs arrived and the close code the relay hung up with."""
    url = f"ws://127.0.0.1:{relay_port}/scope"
    async with websockets.connect(url, max_size=2 * 1024 * 1024) as ws:
        await ws.send(protocol.hello(DEVICE_TOKEN, HOME_ID, generation=1).encode())
        assert protocol.decode(await ws.recv()).header["ok"] is True
        ready.set()
        try:
            while True:
                frame = protocol.decode(await ws.recv())
                if frame.type == protocol.FrameType.PING:
                    seen["pings"] += 1
        except websockets.ConnectionClosed as exc:
            seen["code"] = exc.rcvd.code if exc.rcvd is not None else None


@pytest.mark.asyncio
async def test_a_home_that_stops_answering_pings_is_torn_down():
    """CONTROL for the quiet interval: moving the other tests off a short
    keepalive must not leave the keepalive itself untested on the wire. At
    a short interval, a home that goes deaf is hung up on with 1001 (the
    code only ``_ping_loop`` uses), after at least one PING it never
    answered, its affinity entry is dropped, and the browser gets 502.

    Load cannot make this flaky in the failing direction: a late PONG check
    only finds the home staler. The bound on the teardown is generous for
    that reason, not tight.

    MUTATION "stale tunnels are never torn down" (``if conn.is_stale():``
    -> ``if False:`` in ``_ping_loop``). Observed:
      AssertionError: the relay never hung up on a home that stopped
      answering PINGs (146 PINGs sent in 30 s at a 0.2 s interval)
    """
    relay_port = _free_port()
    app = create_app(_cfg(relay_port, ping_interval_s=_KEEPALIVE_PING_S))
    state = app.state.relay
    state.registry.provision(DEVICE_TOKEN, HOME_ID)
    server, server_task = await _start(app)
    ready = asyncio.Event()
    seen = {"pings": 0, "code": None}
    home_task = asyncio.ensure_future(_deaf_home(relay_port, ready, seen))
    try:
        await asyncio.wait_for(ready.wait(), timeout=_WAIT_S)
        done, _ = await asyncio.wait({home_task}, timeout=_WAIT_S)
        assert home_task in done, (
            "the relay never hung up on a home that stopped answering PINGs "
            f"({seen['pings']} PINGs sent in {_WAIT_S:.0f} s at a "
            f"{_KEEPALIVE_PING_S} s interval)")
        home_task.result()          # surfaces an assertion inside the home
        assert seen["code"] == 1001, (
            f"hung up with {seen['code']}, not the keepalive's 1001: {seen}")
        assert seen["pings"] >= 1, seen

        loop = asyncio.get_running_loop()
        deadline = loop.time() + _WAIT_S
        while HOME_ID in state.connections and loop.time() < deadline:
            await asyncio.sleep(0.02)
        assert HOME_ID not in state.connections, (
            "the torn-down tunnel is still the home's affinity entry")
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{relay_port}/h/{HOME_ID}/api/status",
                timeout=_WAIT_S)
        assert (r.status_code, r.text) == (502, "home not connected")
    finally:
        await _stop(server, server_task, home_task)
