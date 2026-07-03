"""W3 SCOPE-SIDE relay dial-out client + RemoteConfig tests (§T7).

Repo convention (mirrors tests/test_rbac_enforcement.py): in-process fakes via
monkeypatch + a fake in-memory bidi frame channel, NO unittest.mock, NO real
network for the core behaviors. ONE on-wire WSS integration test exercises the
real websockets transport end-to-end.

Coverage:
  * the binary frame codec round-trips byte-for-byte + rejects malformed frames
    and orphan/reserved-stream violations;
  * a tunneled request carries the REMOTE scope flag, so on an OTHERWISE-OPEN
    server (``none`` provider) it is HARD-DENIED without auth -- while a local
    (non-tunneled) request to the same server is unaffected;
  * ``_scope_is_remote`` reads ASGI scope STATE (not a header) and is not
    LAN-spoofable;
  * the client streams a chunked response back, reassembled byte-for-byte;
  * inbound auth headers are stripped at the replay shim;
  * reconnect uses capped + jittered backoff and never raises on a relay outage;
  * the ``device_token`` is redacted in ``redacted()``;
  * one real WSS round-trip (the on-wire integration test).
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (Principal, principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.auth.deps import _scope_is_remote
from astrodeck.config import AppConfig, ConfigStore, RemoteConfig, redacted
from astrodeck.remote.protocol import (CONTROL_STREAM_ID, DEFAULT_MAX_PAYLOAD,
                                       Frame, FrameType, ProtocolError,
                                       decode_frame, encode_frame)
from astrodeck.remote.relay_client import (REMOTE_SCOPE_KEY, RelayClient,
                                           _backoff_delay, run_relay_client,
                                           scope_is_remote)


# ============================================================ harness

def _make_client(tmp_path, monkeypatch, *, token=None):
    """Isolated app + ConfigStore (mirrors tests/test_rbac_enforcement.py)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    if token is None:
        monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(app_module.AUTH_ENV_VAR, token)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    return temp_store, app_module.create_app()


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


class FakeChannel:
    """An in-memory bidirectional frame channel standing in for the relay WSS.

    The TEST acts as the relay: it ``push(...)``es raw frames the client will
    receive, and ``sent`` collects raw frames the client emits. Exposes the
    async-iterable + ``send``/``close`` surface the client expects."""

    def __init__(self):
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent: list[bytes] = []
        self._closed = False
        self._eof = object()

    # -- relay side (the test drives these) ------------------------------------

    def push(self, raw: bytes) -> None:
        self._inbox.put_nowait(raw)

    def push_frame(self, type, stream_id=CONTROL_STREAM_ID, header=None, payload=b""):
        self.push(encode_frame(type, stream_id, header or {}, payload))

    def finish(self) -> None:
        """Signal end-of-stream so the client's recv loop ends (socket closed)."""
        self._inbox.put_nowait(self._eof)

    def sent_frames(self) -> list[Frame]:
        return [decode_frame(r) for r in self.sent]

    # -- client side (the client calls these) ----------------------------------

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._inbox.get()
        if item is self._eof:
            raise StopAsyncIteration
        return item

    async def send(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionError("channel closed")
        self.sent.append(data)

    async def close(self) -> None:
        self._closed = True


def _make_relay_client(app, channel, cfg=None):
    """A RelayClient wired to a fake channel (one connect, then EOF)."""
    cfg = cfg or RemoteConfig(enabled=True, relay_url="wss://relay.test/scope",
                              device_token="tok", home_id="home-1")

    async def _connect(url):
        return channel

    return RelayClient(app, lambda: cfg, connect=_connect)


async def _drain_request(client: RelayClient, channel: FakeChannel, *,
                         stream_id: int, timeout=5.0):
    """Serve the connection, wait until the response for ``stream_id`` completes
    (its terminal eof RESP_DATA is on the wire), then EOF the channel so the serve
    loop unwinds. This models the relay holding the connection open until the
    response is delivered (a premature disconnect would cancel the in-flight task,
    which is the correct production behavior but not what these tests assert)."""
    serve_task = asyncio.create_task(client._serve_once(client._config()))

    async def _wait_for_response():
        while True:
            for raw in list(channel.sent):
                f = decode_frame(raw)
                if (f.type == FrameType.RESP_DATA and f.stream_id == stream_id
                        and f.eof):
                    return
            await asyncio.sleep(0.005)

    try:
        await asyncio.wait_for(_wait_for_response(), timeout=timeout)
    finally:
        channel.finish()
        await asyncio.wait_for(serve_task, timeout=timeout)
    return channel.sent_frames()


# ============================================================ protocol codec

def test_frame_roundtrip_byte_for_byte():
    payload = b"\x00\x01\x02hello-FITS\xff" * 100
    raw = encode_frame(FrameType.RESP_DATA, 7, {"eof": False, "n": 3}, payload)
    f = decode_frame(raw)
    assert f.type == FrameType.RESP_DATA
    assert f.stream_id == 7
    assert f.header == {"eof": False, "n": 3}
    assert f.payload == payload
    assert f.eof is False


def test_frame_header_only_and_empty_payload():
    raw = encode_frame(FrameType.PING, 0, {})
    f = decode_frame(raw)
    assert f.type == FrameType.PING
    assert f.stream_id == 0
    assert f.header == {}
    assert f.payload == b""


def test_frame_rejects_unknown_type():
    raw = encode_frame(FrameType.PING, 0, {})
    bad = bytes([0xEE]) + raw[1:]
    with pytest.raises(ProtocolError):
        decode_frame(bad)


def test_frame_rejects_oversize_payload():
    with pytest.raises(ProtocolError):
        encode_frame(FrameType.RESP_DATA, 1, {}, b"x" * (DEFAULT_MAX_PAYLOAD + 1))


def test_frame_rejects_truncated_header():
    raw = encode_frame(FrameType.REQ_OPEN, 1, {"method": "GET"}, b"")
    with pytest.raises(ProtocolError):
        decode_frame(raw[:-3])  # chop into the header bytes


def test_frame_rejects_data_on_reserved_stream0():
    # A data-class frame on stream 0 is a protocol violation.
    raw = bytes([int(FrameType.REQ_OPEN)]) + (0).to_bytes(8, "big") + (2).to_bytes(4, "big") + b"{}"
    with pytest.raises(ProtocolError):
        decode_frame(raw)


def test_frame_rejects_control_on_nonzero_stream():
    raw = bytes([int(FrameType.PING)]) + (9).to_bytes(8, "big") + (2).to_bytes(4, "big") + b"{}"
    with pytest.raises(ProtocolError):
        decode_frame(raw)


# ============================================================ remote flag

def test_scope_is_remote_reads_state_not_header():
    # The flag is ASGI scope STATE, never a header -- not LAN-spoofable.
    remote_scope = {"type": "http", "state": {REMOTE_SCOPE_KEY: True},
                    "headers": []}
    local_scope = {"type": "http", "state": {}, "headers": []}
    spoof_header_scope = {
        "type": "http", "state": {},
        # an attacker setting a header cannot flip the flag
        "headers": [(b"x-astrodeck-remote", b"true"),
                    (b"astrodeck_remote", b"true")],
    }

    class _Req:
        def __init__(self, scope):
            self.scope = scope

    assert _scope_is_remote(_Req(remote_scope)) is True
    assert _scope_is_remote(_Req(local_scope)) is False
    assert _scope_is_remote(_Req(spoof_header_scope)) is False
    # the relay_client convenience reader agrees
    assert scope_is_remote(remote_scope) is True
    assert scope_is_remote(local_scope) is False
    assert scope_is_remote({"type": "http"}) is False  # no state at all


# ============================================================ tunneled request denial

def test_scope_builder_marks_remote(tmp_path, monkeypatch):
    _store, app = _make_client(tmp_path, monkeypatch)
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    frame = Frame(type=FrameType.REQ_OPEN, stream_id=3,
                  header={"method": "GET", "path": "/api/status", "query": ""})
    scope = client._build_http_scope(frame)
    assert scope["state"][REMOTE_SCOPE_KEY] is True
    assert _scope_is_remote(type("R", (), {"scope": scope})) is True


def test_tunneled_request_denied_on_open_server(tmp_path, monkeypatch):
    """A tunneled (remote-flagged) request to an OTHERWISE-OPEN server (``none``
    provider) is HARD-DENIED without auth: the open default never leaks remotely."""
    _store, app = _make_client(tmp_path, monkeypatch)
    # ensure the open NoneAuthProvider is active (default)
    reset_active_provider()
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    # tunnel a GET to a view route that would be 200 locally under the open default
    channel.push_frame(FrameType.REQ_OPEN, 3,
                       {"method": "GET", "path": "/api/status",
                        "query": "", "has_body": False})
    frames = asyncio.run(_drain_request(client, channel, stream_id=3))
    heads = [f for f in frames if f.type == FrameType.RESP_HEAD]
    assert heads, "expected a RESP_HEAD"
    # remote + none-provider => resolve_principal returns None => 401
    assert heads[0].header["status"] == 401


def test_local_request_unaffected_on_open_server(tmp_path, monkeypatch):
    """The SAME open server still serves a normal (non-tunneled) local request --
    proving the remote flag, not the route, is what denies."""
    _store, app = _make_client(tmp_path, monkeypatch)
    reset_active_provider()
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200


def test_tunneled_request_allowed_for_authenticated_admin(tmp_path, monkeypatch):
    """With a real provider installed that resolves an admin, the tunneled request
    succeeds -- the denial is specifically the OPEN-default-over-remote case."""
    _store, app = _make_client(tmp_path, monkeypatch)

    class _AdminProvider:
        name = "fake"

        async def resolve(self, request):
            return principal_for_role("admin")

    set_active_provider(_AdminProvider())
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    channel.push_frame(FrameType.REQ_OPEN, 5,
                       {"method": "GET", "path": "/api/status",
                        "query": "", "has_body": False})
    frames = asyncio.run(_drain_request(client, channel, stream_id=5))
    heads = [f for f in frames if f.type == FrameType.RESP_HEAD]
    assert heads and heads[0].header["status"] == 200


# ============================================================ response streaming

def test_response_chunks_reassemble_byte_for_byte(tmp_path, monkeypatch):
    """A large response body streams back as multiple bounded RESP_DATA frames
    that reassemble byte-for-byte, with exactly one terminal eof."""
    _store, app = _make_client(tmp_path, monkeypatch)
    reset_active_provider()
    channel = FakeChannel()
    client = _make_relay_client(app, channel)

    big = b"Z" * (DEFAULT_MAX_PAYLOAD * 2 + 123)

    async def _send_big(message):
        pass

    # Drive the _RequestStream send shim directly to assert chunking + reassembly.
    from astrodeck.remote.relay_client import _RequestStream
    sent: list[Frame] = []

    async def _capture(frame):
        sent.append(frame)

    stream = _RequestStream(9, _capture)
    asyncio.run(_run_response(stream, big))
    datas = [f for f in sent if f.type == FrameType.RESP_DATA]
    assert len(datas) >= 3  # chunked
    assert sum(len(f.payload) for f in datas) == len(big)
    assert b"".join(f.payload for f in datas) == big
    assert datas[-1].eof is True
    assert all(not f.eof for f in datas[:-1])


async def _run_response(stream, body):
    await stream.send({"type": "http.response.start", "status": 200, "headers": []})
    await stream.send({"type": "http.response.body", "body": body, "more_body": False})


# ============================================================ header stripping

def test_inbound_auth_headers_stripped(tmp_path, monkeypatch):
    _store, app = _make_client(tmp_path, monkeypatch)
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    frame = Frame(type=FrameType.REQ_OPEN, stream_id=3, header={
        "method": "GET", "path": "/api/status", "query": "",
        "headers": [["Authorization", "Bearer x"],
                    ["X-Auth-Token", "y"],
                    ["Cookie", "ad_session=z"],
                    ["X-Custom", "keep-me"]],
    })
    scope = client._build_http_scope(frame)
    names = {n for n, _ in scope["headers"]}
    assert b"authorization" not in names  # forgeable bearer carriers stripped
    assert b"x-auth-token" not in names
    # cookie PASSES THROUGH: home-terminated auth (e.g. Google OIDC over the relay)
    # needs it; it is HMAC-signed by the home, so a forged/unsigned cookie reaches
    # the home but never validates (verified, not stripped).
    assert b"cookie" in names
    assert b"x-custom" in names  # non-auth headers pass through


def test_inbound_query_token_stripped(tmp_path, monkeypatch):
    """The shared ASTRODECK_TOKEN also rides as ``?token=`` and is accepted from
    the query by the transport middleware / TokenAdminProvider (neither named
    ``none``, so the remote hard-deny interlock does NOT fire). The header strip
    alone leaves that carrier open, so the tunnel MUST strip ``token`` from the
    query too -- otherwise a tunneled ``?token=<ASTRODECK_TOKEN>`` escalates to
    full admin. Other params survive untouched."""
    _store, app = _make_client(tmp_path, monkeypatch)
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    frame = Frame(type=FrameType.REQ_OPEN, stream_id=3, header={
        "method": "GET", "path": "/api/status",
        "query": "foo=1&token=breakglass-tok&bar=two", "headers": [],
    })
    scope = client._build_http_scope(frame)
    qs = scope["query_string"].decode("latin-1")
    from urllib.parse import parse_qs
    parsed = parse_qs(qs, keep_blank_values=True)
    assert "token" not in parsed          # shared-token carrier closed
    assert parsed["foo"] == ["1"]         # siblings preserved
    assert parsed["bar"] == ["two"]


def test_inbound_bare_token_query_stripped(tmp_path, monkeypatch):
    """A query that is ONLY ``token=...`` reduces to an empty query (no leak)."""
    _store, app = _make_client(tmp_path, monkeypatch)
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    frame = Frame(type=FrameType.REQ_OPEN, stream_id=4, header={
        "method": "GET", "path": "/api/status",
        "query": "token=breakglass-tok", "headers": [],
    })
    scope = client._build_http_scope(frame)
    assert scope["query_string"] == b""


# ============================================================ backoff / resilience

def test_backoff_capped_and_jittered():
    # full jitter -> in [0, ceiling]; ceiling caps at ~15s
    for attempt in range(0, 12):
        d = _backoff_delay(attempt)
        assert 0.0 <= d <= 15.0


def test_backoff_never_overflows_at_high_attempt():
    """A long relay outage drives ``attempt`` into the thousands (it only resets
    on a clean session). ``2 ** attempt`` must not materialize a huge int that
    overflows the float conversion inside min() -- previously _backoff_delay(1024)
    raised OverflowError, which escaped the never-raising run() supervisor and
    permanently killed reconnection. The clamped exponent keeps it bounded."""
    # The historical crash point and well beyond it must all stay within the cap.
    for attempt in (1023, 1024, 4096, 100_000, 2 ** 20):
        d = _backoff_delay(attempt)
        assert 0.0 <= d <= 15.0


def test_run_loop_survives_high_attempt_backoff():
    """Regression for the overflow: run() must keep retrying (not crash) even after
    thousands of consecutive failed dials. We seed a high attempt count via a
    connect that always fails and a near-zero cap so it spins fast, and assert the
    supervisor is still alive and dialing after crossing the old 1024 crash point."""
    attempts = {"n": 0}

    async def _bad_connect(url):
        attempts["n"] += 1
        raise ConnectionError("relay down")

    cfg = RemoteConfig(enabled=True, relay_url="wss://relay.test/scope",
                       device_token="tok")

    async def _scenario():
        client = RelayClient(_noop_app, lambda: cfg, connect=_bad_connect)
        # Force the run loop straight into the historical crash region: a backoff
        # that computes the REAL (now overflow-safe) delay for a huge attempt, then
        # collapses it to ~0 so the loop spins fast. If _backoff_delay overflowed,
        # this would raise out of run() and the task would be done() with an exc.
        import astrodeck.remote.relay_client as rc
        orig = rc._backoff_delay

        def _fast_but_real(attempt):
            _ = rc.__dict__  # keep ref
            real = orig(attempt + 5000)  # exercise the real math past attempt=1024
            assert 0.0 <= real <= 15.0
            return 0.001

        rc._backoff_delay = _fast_but_real
        try:
            task = asyncio.create_task(client.run())
            await asyncio.sleep(0.05)
            assert attempts["n"] >= 2  # still dialing, not crashed
            assert not task.done()     # supervisor alive
            client.stop()
            await asyncio.wait_for(task, timeout=2.0)
            assert task.exception() is None  # ended cleanly, never raised
        finally:
            rc._backoff_delay = orig

    asyncio.run(_scenario())


def test_run_loop_never_raises_on_connect_failure():
    """A relay that refuses every connect -> the run loop retries (backoff) and
    never raises; stop() ends it cleanly. Proves local-only degradation."""
    attempts = {"n": 0}

    async def _bad_connect(url):
        attempts["n"] += 1
        raise ConnectionError("relay down")

    cfg = RemoteConfig(enabled=True, relay_url="wss://relay.test/scope",
                       device_token="tok")

    async def _scenario():
        client = RelayClient(_noop_app, lambda: cfg, connect=_bad_connect)
        # patch the backoff to ~0 so the test runs fast
        import astrodeck.remote.relay_client as rc
        orig = rc._backoff_delay
        rc._backoff_delay = lambda attempt: 0.001
        try:
            task = asyncio.create_task(client.run())
            await asyncio.sleep(0.05)  # let it retry a few times
            assert attempts["n"] >= 2
            client.stop()
            await asyncio.wait_for(task, timeout=2.0)  # ends cleanly, no raise
        finally:
            rc._backoff_delay = orig

    asyncio.run(_scenario())


async def _noop_app(scope, receive, send):  # pragma: no cover - never reached
    pass


def test_run_relay_client_disabled_returns_none():
    """OPT-IN: disabled or no relay_url => returns None, never dials."""
    async def _scenario():
        assert await run_relay_client(_noop_app, lambda: RemoteConfig()) is None
        assert await run_relay_client(
            _noop_app, lambda: RemoteConfig(enabled=True, relay_url="")) is None
        assert await run_relay_client(
            _noop_app, lambda: RemoteConfig(enabled=False,
                                            relay_url="wss://r/x")) is None

    asyncio.run(_scenario())


def test_generation_increments_on_each_dial(tmp_path, monkeypatch):
    """Each (re)dial bumps generation (fencing) and HELLO carries the token+gen."""
    _store, app = _make_client(tmp_path, monkeypatch)
    reset_active_provider()

    async def _scenario():
        channel = FakeChannel()
        client = _make_relay_client(app, channel)
        g0 = client.generation
        channel.finish()
        # _serve_once does not bump generation (run() does); bump here to mirror run
        client._generation += 1
        await asyncio.wait_for(client._serve_once(client._config()), timeout=5.0)
        hellos = [f for f in channel.sent_frames() if f.type == FrameType.HELLO]
        assert hellos, "HELLO must be sent on connect"
        assert hellos[0].header["device_token"] == "tok"
        assert hellos[0].header["generation"] == g0 + 1
        assert hellos[0].header["proto_version"] >= 1

    asyncio.run(_scenario())


# ============================================================ tunneled /ws fanout

def test_tunneled_ws_streams_bus_events(tmp_path, monkeypatch):
    """WS_OPEN -> the client subscribes to the bus and streams a hello + each
    published event down as WS_DATA with monotonic per-ws seq (send-only)."""
    _store, app = _make_client(tmp_path, monkeypatch)
    reset_active_provider()

    async def _scenario():
        channel = FakeChannel()
        client = _make_relay_client(app, channel)
        # open one tunneled ws then publish an event then EOF the connection
        channel.push_frame(FrameType.WS_OPEN, 4,
                           {"path": "/ws", "query": "", "ws_id": 4})

        async def _serve():
            await client._serve_once(client._config())

        task = asyncio.create_task(_serve())
        await asyncio.sleep(0.05)  # let the ws subscribe
        from astrodeck.events import bus
        bus.publish("status", foo="bar")
        await asyncio.sleep(0.05)
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)

        ws_data = [f for f in channel.sent_frames() if f.type == FrameType.WS_DATA]
        assert ws_data, "expected WS_DATA frames"
        assert all(f.header["ws_id"] == 4 for f in ws_data)
        seqs = [f.header["seq"] for f in ws_data]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # monotonic
        first = json.loads(ws_data[0].payload)
        assert first["type"] == "hello"
        types = [json.loads(f.payload)["type"] for f in ws_data]
        assert "status" in types

    asyncio.run(_scenario())


# ============================================================ config / redaction

def test_remote_config_default_does_not_dial():
    assert RemoteConfig().enabled is False
    assert RemoteConfig().relay_url == ""


def test_device_token_redacted():
    cfg = AppConfig()
    cfg.remote = RemoteConfig(enabled=True, relay_url="wss://relay.example/scope",
                              device_token="SUPER-SECRET", home_id="h1")
    r = redacted(cfg)["remote"]
    assert r["device_token"] == ""
    assert r["remote_token_configured"] is True
    assert r["remote_configured"] is True
    assert r["relay_url"] == "wss://relay.example/scope"
    assert r["home_id"] == "h1"
    # source cfg is never mutated
    assert cfg.remote.device_token == "SUPER-SECRET"


def test_redacted_blank_token_markers_false():
    cfg = AppConfig()  # default remote: disabled, no token
    r = redacted(cfg)["remote"]
    assert r["device_token"] == ""
    assert r["remote_token_configured"] is False
    assert r["remote_configured"] is False


def test_set_remote_setter_persists(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    before = store.cfg().version
    cfg = store.set_remote(RemoteConfig(enabled=True,
                                        relay_url="wss://r/scope",
                                        device_token="tk"))
    assert cfg.remote.enabled is True
    assert cfg.remote.relay_url == "wss://r/scope"
    assert cfg.remote.device_token == "tk"
    assert cfg.version == before + 1
    # reload from disk -> persisted
    store2 = ConfigStore(path=tmp_path / "astrodeck.json")
    assert store2.cfg().remote.relay_url == "wss://r/scope"


# ============================================================ on-wire integration

@pytest.mark.asyncio
async def test_on_wire_wss_roundtrip(tmp_path, monkeypatch):
    """ONE real WSS round-trip: a websockets relay server accepts the scope
    connection, tunnels a REQ_OPEN for /api/status, and reads back the RESP_HEAD/
    RESP_DATA over the actual wire. Exercises the real transport end-to-end."""
    websockets = pytest.importorskip("websockets")
    _store, app = _make_client(tmp_path, monkeypatch)
    reset_active_provider()

    got: dict = {}
    server_ready = asyncio.Event()
    done = asyncio.Event()

    async def relay_handler(conn):
        # read HELLO
        hello = decode_frame(await conn.recv())
        got["hello_type"] = hello.type
        # tunnel a request
        await conn.send(encode_frame(
            FrameType.REQ_OPEN, 1,
            {"method": "GET", "path": "/api/status", "query": "",
             "has_body": False}, b""))
        # collect response frames until eof
        body = bytearray()
        status = None
        while True:
            frame = decode_frame(await conn.recv())
            if frame.type == FrameType.RESP_HEAD:
                status = frame.header["status"]
            elif frame.type == FrameType.RESP_DATA:
                body.extend(frame.payload)
                if frame.eof:
                    break
        got["status"] = status
        got["body"] = bytes(body)
        done.set()

    server = await websockets.serve(relay_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/scope"
    cfg = RemoteConfig(enabled=True, relay_url=url, device_token="tok",
                       home_id="h1")
    client = RelayClient(app, lambda: cfg)
    serve_task = asyncio.create_task(client._serve_once(cfg))
    try:
        await asyncio.wait_for(done.wait(), timeout=10.0)
    finally:
        client.stop()
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass
        server.close()
        await server.wait_closed()

    assert got["hello_type"] == FrameType.HELLO
    # open server + remote flag => 401 (the open default never served remotely)
    assert got["status"] == 401
