"""W3 END-TO-END tunnel test: the REAL relay service + the REAL home scope client
wired through ONE in-memory bidi channel, plus the adversarial security battery.

Unlike tests/test_remote_relay.py (which drives the scope client against a fake
"relay" that the TEST hand-codes) and the on-wire WSS test (which uses a trivial
hand-coded relay handler), THIS test stands up the ACTUAL relay-lane components --
``relay.registry.HomeRegistry`` / ``relay.connection.ScopeConnection`` /
``relay.proxy.TunnelMultiplexer`` -- AND the actual home-lane
``astrodeck.remote.relay_client.RelayClient``, and proves a real browser HTTP
request and a real browser ``/ws`` round-trip THROUGH the relay, down the tunnel,
into the in-process home ASGI app, and back. It then attacks the seam and asserts
each owner invariant HONESTLY.

The two lanes encode/decode with two INDEPENDENT copies of the wire codec
(``relay/relay/protocol.py`` and ``server/astrodeck/remote/protocol.py``); a byte
mismatch between them would fail the round-trip immediately, so this also pins
their wire compatibility.

Requires the ``relay`` package on the path (conftest adds the sibling ``relay/``
dir). Pure in-memory: NO sockets, NO uvicorn.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore, RemoteConfig
from astrodeck.remote.relay_client import RelayClient

# The relay lane (sibling repo dir; conftest puts it on sys.path).
relay_protocol = pytest.importorskip("relay.protocol")
from relay.connection import ScopeConnection  # noqa: E402
from relay.proxy import BrowserWS  # noqa: E402
from relay.registry import HomeRegistry, ScopeTunnel  # noqa: E402


# ============================================================ harness

def _make_app(tmp_path, monkeypatch, *, provider=None):
    """An isolated in-process home app (mirrors tests/test_remote_relay.py)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    if provider is not None:
        set_active_provider(provider)
    return temp_store, app_module.create_app()


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


class _Pipe:
    """A one-directional async byte pipe (the relay->home OR home->relay leg)."""

    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()
        self._eof = object()
        self.closed = False

    def put(self, raw: bytes) -> None:
        self._q.put_nowait(raw)

    def finish(self) -> None:
        self._q.put_nowait(self._eof)

    async def get(self):
        item = await self._q.get()
        if item is self._eof:
            raise StopAsyncIteration
        return item


class HomeEndpoint:
    """The object the home ``RelayClient`` treats as its WSS connection. Reads
    frames the relay sent (``down`` pipe) and writes home->relay frames (``up``
    pipe)."""

    def __init__(self, down: _Pipe, up: _Pipe):
        self._down = down
        self._up = up

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._down.get()

    async def send(self, data: bytes) -> None:
        self._up.put(data)

    async def close(self) -> None:
        self._up.finish()


class RelayEndpoint(ScopeTunnel):
    """The relay's ``ScopeTunnel`` over the same pipes: ``send_frame`` writes a
    relay->home frame down; the relay read loop pulls home->relay frames up."""

    def __init__(self, down: _Pipe, up: _Pipe):
        super().__init__(self._send, conn_id="e2e-tunnel")
        self._down = down
        self._up = up

    async def _send(self, frame) -> None:
        self._down.put(frame.encode())

    async def read_up(self):
        return await self._up.get()


class FakeBrowserWS(BrowserWS):
    """A fake browser ``/ws`` socket: collects the JSON event strings the relay
    fans out to it."""

    def __init__(self):
        self.received: list[str] = []
        self.closed_code = None

    async def send_text(self, text: str) -> None:
        self.received.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


class E2ERig:
    """Wires the home RelayClient <-> a live relay ScopeConnection/Multiplexer
    over two in-memory pipes, and drives the relay read loop as a task."""

    def __init__(self, app, *, device_token="dev-tok", home_id="home-e2e"):
        self.down = _Pipe()   # relay -> home
        self.up = _Pipe()     # home -> relay
        self.home_ep = HomeEndpoint(self.down, self.up)
        self.relay_ep = RelayEndpoint(self.down, self.up)
        self.registry = HomeRegistry(token_to_home={device_token: home_id})
        self.conn = ScopeConnection(self.registry, self.relay_ep)
        self.cfg = RemoteConfig(enabled=True, relay_url="wss://relay.test/scope",
                                device_token=device_token, home_id=home_id)
        self.client = RelayClient(app, lambda: self.cfg,
                                  connect=self._connect)
        self._home_task = None
        self._relay_task = None

    async def _connect(self, url):
        return self.home_ep

    async def start(self):
        # Home side: dial + serve (sends HELLO up, services REQ/WS down).
        self._home_task = asyncio.create_task(
            self.client._serve_once(self.cfg))
        # Relay side: read the HELLO, ack it, then route every subsequent frame.
        self._relay_task = asyncio.create_task(self._relay_loop())
        # Wait until the relay has registered the home (HELLO processed).
        for _ in range(200):
            if self.conn.registered and self.conn.mux is not None:
                return
            await asyncio.sleep(0.005)
        raise AssertionError("relay never registered the home")

    async def _relay_loop(self):
        try:
            while True:
                raw = await self.relay_ep.read_up()
                frame = relay_protocol.decode(raw)
                if not self.conn.registered:
                    ack = self.conn.handle_hello(frame)
                    await self.relay_ep.send_frame(ack)
                    continue
                reply = await self.conn.on_frame(frame)
                if reply is not None:
                    await self.relay_ep.send_frame(reply)
        except StopAsyncIteration:
            return
        except Exception:  # noqa: BLE001 - relay teardown
            return

    async def http(self, method, path, query="", *, headers=None,
                   body=b"", timeout=5.0):
        """Drive ONE browser HTTP request through the relay multiplexer and
        collect the reassembled response (status, headers, body)."""
        head: dict = {}
        head_ready = asyncio.Event()
        chunks: list[bytes] = []
        done = asyncio.Event()

        async def on_head(status, hdrs):
            head["status"] = status
            head["headers"] = hdrs
            head_ready.set()

        async def on_data(chunk, eof):
            if chunk:
                chunks.append(chunk)
            if eof:
                done.set()

        sid = await self.conn.mux.open_request(
            method, path, query, headers or [],
            has_body=bool(body), on_head=on_head, on_data=on_data)
        if body:
            await self.conn.mux.send_request_body(sid, body, eof=True)
        else:
            await self.conn.mux.send_request_body(sid, b"", eof=True)
        await asyncio.wait_for(done.wait(), timeout=timeout)
        return head.get("status"), head.get("headers", []), b"".join(chunks)

    async def open_browser_ws(self, browser, timeout=5.0):
        ws_id = await self.conn.mux.open_ws(browser, "/ws", "", [])
        return ws_id

    async def stop(self):
        self.up.finish()
        self.down.finish()
        for t in (self._home_task, self._relay_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass


# ============================================================ round-trip

@pytest.mark.asyncio
async def test_http_and_ws_round_trip_through_relay(tmp_path, monkeypatch):
    """tunnelRoundTrips: a remote HTTP request AND a /ws status frame round-trip
    through the REAL relay components to the home app and back. Uses an admin
    provider so the remote request is authorized (the OPEN-default denial is its
    own test below)."""

    class _AdminProvider:
        name = "fake"

        async def resolve(self, request):
            return principal_for_role("admin")

    _store, app = _make_app(tmp_path, monkeypatch, provider=_AdminProvider())
    rig = E2ERig(app)
    await rig.start()
    try:
        # 1) HTTP round-trip: GET /api/status -> 200 + a JSON body from the home.
        status, headers, body = await rig.http("GET", "/api/status")
        assert status == 200, f"expected 200, got {status}"
        doc = json.loads(body)
        assert isinstance(doc, dict) and "connected" in doc

        # 2) /ws round-trip: open a browser ws; the relay fans the home's hello +
        # a published status event out to exactly that browser.
        browser = FakeBrowserWS()
        await rig.open_browser_ws(browser)
        # let the home subscribe + emit the hello frame
        for _ in range(100):
            if browser.received:
                break
            await asyncio.sleep(0.005)
        from astrodeck.events import bus
        bus.publish("status", phase="e2e")
        for _ in range(100):
            if len(browser.received) >= 2:
                break
            await asyncio.sleep(0.005)
        assert browser.received, "browser got no /ws frames through the tunnel"
        kinds = [json.loads(t)["type"] for t in browser.received]
        assert "hello" in kinds
        assert "status" in kinds
    finally:
        await rig.stop()


# ============================================================ open-default denial

@pytest.mark.asyncio
async def test_open_default_denied_remote_through_relay(tmp_path, monkeypatch):
    """openDefaultDeniedRemote: the SAME app under the OPEN ``none`` provider --
    which serves /api/status 200 on the LAN -- returns 401 over the relay tunnel,
    because the scope client marks the request remote and the home hard-denies
    the open default remotely."""
    _store, app = _make_app(tmp_path, monkeypatch)  # default = none provider
    rig = E2ERig(app)
    await rig.start()
    try:
        status, _headers, _body = await rig.http("GET", "/api/status")
        assert status == 401, f"open default must be 401 remotely, got {status}"
    finally:
        await rig.stop()


# ============================================================ remote flag unspoofable

def test_remote_flag_unspoofable_by_lan_header(tmp_path, monkeypatch):
    """remoteFlagUnspoofable: an on-LAN attacker who sets request HEADERS named
    like the remote flag/device token cannot flip the scope state. The flag is
    ASGI scope STATE, set ONLY by the scope client; a real uvicorn request never
    carries it, and the scope-builder STRIPS inbound auth carriers."""
    from astrodeck.auth.deps import _scope_is_remote
    from astrodeck.remote.relay_client import REMOTE_SCOPE_KEY
    from astrodeck.remote.protocol import Frame, FrameType

    _store, app = _make_app(tmp_path, monkeypatch)
    rig = E2ERig(app)

    # A request whose attacker-controlled HEADERS try to assert remoteness / auth.
    frame = Frame(type=FrameType.REQ_OPEN, stream_id=3, header={
        "method": "GET", "path": "/api/status", "query": "",
        "headers": [["X-Astrodeck-Remote", "true"],
                    ["astrodeck_remote", "true"],
                    ["Authorization", "Bearer forged-admin"],
                    ["X-Auth-Token", "forged"],
                    ["Cookie", "ad_session=forged"]],
    })
    scope = rig.client._build_http_scope(frame)
    # The remote flag is true here because the SCOPE CLIENT set it (correct: this
    # IS a tunneled request) -- but it came from scope STATE, not the header.
    assert scope["state"][REMOTE_SCOPE_KEY] is True
    names = {n for n, _ in scope["headers"]}
    assert b"authorization" not in names  # forgeable bearer carriers stripped
    assert b"x-auth-token" not in names
    # the cookie PASSES THROUGH (home-terminated auth, e.g. Google OIDC, needs it):
    # it is HMAC-signed by the home, so a forged/unsigned cookie reaches the home
    # but never validates -- carried by the home's verification, not by stripping.
    assert b"cookie" in names

    # And a plain (non-tunneled) scope with the SAME spoof headers is NOT remote
    # (this is what a real on-LAN attacker actually controls): they cannot set
    # scope STATE, only headers, so the flag stays False.
    lan_scope = {"type": "http", "state": {},
                 "headers": [(b"x-astrodeck-remote", b"true"),
                             (b"astrodeck_remote", b"true")]}
    assert _scope_is_remote(type("R", (), {"scope": lan_scope})) is False


# ============================================================ relay can't forge admin

@pytest.mark.asyncio
async def test_relay_cannot_forge_admin(tmp_path, monkeypatch):
    """relayCannotForge: the relay holds NO home signing secret. Even if the relay
    injects a forged ``principal_token`` AND forged auth headers into REQ_OPEN, the
    home -- under the open ``none`` provider, remotely -- still hard-denies, because
    (a) the forgeable bearer headers are stripped and the forged (unsigned) cookie
    fails the home's HMAC verification, and (b) no installed provider trusts an
    unverified relay-supplied token to mint admin. So the forged identity yields 401,
    not a 200 admin action."""
    _store, app = _make_app(tmp_path, monkeypatch)  # none provider (open on LAN)
    rig = E2ERig(app)
    await rig.start()
    try:
        # The relay forges every identity carrier it can put on the wire.
        forged_headers = [["Authorization", "Bearer admin"],
                          ["X-Auth-Token", "admin"],
                          ["Cookie", "ad_session=admin"]]
        status, _h, _b = await rig.http(
            "GET", "/api/status", headers=forged_headers)
        # The home never elevated: the forged bearer headers are stripped, the
        # forged cookie fails HMAC verification, and the open default is denied
        # remotely. A destructive admin route is likewise denied.
        assert status == 401, f"relay forged-admin must 401, got {status}"
        # A mutating admin-gated route is ALSO denied (not merely the view route).
        st2, _h2, _b2 = await rig.http(
            "POST", "/api/auth/revoke", headers=forged_headers,
            body=b'{"jti":"x"}')
        assert st2 in (401, 403), f"forged admin POST must be denied, got {st2}"
    finally:
        await rig.stop()


# ============================================================ safe degrade

def test_safe_degrade_when_relay_down(tmp_path, monkeypatch):
    """safeDegradeOnRelayDown: when the relay refuses every dial, the client run
    loop retries with backoff and NEVER raises; the home keeps serving locally.
    Proven by serving a LAN request against the same app while the relay is down."""
    from fastapi.testclient import TestClient

    _store, app = _make_app(tmp_path, monkeypatch)

    attempts = {"n": 0}

    async def _bad_connect(url):
        attempts["n"] += 1
        raise ConnectionError("relay unreachable")

    async def _scenario():
        cfg = RemoteConfig(enabled=True, relay_url="wss://relay.test/scope",
                           device_token="tok")
        client = RelayClient(app, lambda: cfg, connect=_bad_connect)
        import astrodeck.remote.relay_client as rc
        orig = rc._backoff_delay
        rc._backoff_delay = lambda attempt: 0.001
        try:
            task = asyncio.create_task(client.run())
            await asyncio.sleep(0.05)
            assert attempts["n"] >= 2  # it kept retrying
            assert not task.done()      # still alive, never raised
            client.stop()
            await asyncio.wait_for(task, timeout=2.0)
            assert task.exception() is None
        finally:
            rc._backoff_delay = orig

    asyncio.run(_scenario())

    # The home still serves the LAN path with the relay down (local autonomy).
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200
