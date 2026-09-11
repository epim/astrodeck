"""S3 -- ``GET /api/remote/status``: the READ half of the W3 relay seam.

``POST /api/remote/config`` could always write the relay knobs, and nothing
could read back whether the dial-out was actually connected, so a "relay:
connected" badge had nothing to poll (server-routes.md §4.22 "NOT PROVIDED").
This route answers two DIFFERENT questions at once and the tests keep them
apart: ``connected`` is the tunnel's state, ``via`` is how the request in your
hand arrived.

The load-bearing test is the last one: the response must never contain the
``device_token``. That is the credential registering this home with the relay,
and the route reads the very config object holding it.

Repo convention (mirrors tests/test_remote_relay.py): in-process fakes via
monkeypatch + a fake in-memory bidi frame channel, NO unittest.mock, no network.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.remote.relay_client as relay_mod
from astrodeck.auth import (CAP_VIEW_STATUS, Principal, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.config import ConfigStore, RemoteConfig
from astrodeck.remote.protocol import (CONTROL_STREAM_ID, FrameType,
                                       decode_frame, encode_frame)
from astrodeck.remote.relay_client import RelayClient, relay_status

TEST_DEVICE_TOKEN = "t" * 43
RELAY_URL = "wss://relay.example.test/scope"


# --------------------------------------------------------------------- harness

def _make_client(tmp_path, monkeypatch):
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
    return temp_store, app_module.create_app()


class _FakeProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _install(principal):
    set_active_provider(_FakeProvider(principal))


def _principal_with(*caps):
    return Principal(role="custom", email=None, caps=frozenset(caps), jti=None)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Every test ends with the open provider AND no live relay client, so a
    client installed here can never be reported by an unrelated test."""
    yield
    reset_active_provider()
    relay_mod._current_client = None


class FakeChannel:
    """In-memory bidirectional frame channel standing in for the relay WSS
    (lifted from tests/test_remote_relay.py)."""

    def __init__(self, *, auto_ack: bool = True):
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[bytes] = []
        self._closed = False
        self._eof = object()
        if auto_ack:
            self.push_frame(FrameType.HELLO_ACK, CONTROL_STREAM_ID, {"ok": True})

    def push(self, raw: bytes) -> None:
        self._inbox.put_nowait(raw)

    def push_frame(self, type, stream_id=CONTROL_STREAM_ID, header=None, payload=b""):
        self.push(encode_frame(type, stream_id, header or {}, payload))

    def finish(self) -> None:
        self._inbox.put_nowait(self._eof)

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


def _relay_cfg():
    return RemoteConfig(enabled=True, relay_url=RELAY_URL,
                        device_token=TEST_DEVICE_TOKEN, home_id="home-1")


def _make_relay_client(app, channel, cfg=None):
    cfg = cfg or _relay_cfg()

    async def _connect(url):
        return channel

    return RelayClient(app, lambda: cfg, connect=_connect)


async def _tunnel_get(client, channel, path, *, stream_id=3, timeout=5.0):
    """Serve one connection, tunnel a GET, return (status, body-dict)."""
    serve = asyncio.create_task(client._serve_once(client._config()))
    channel.push_frame(FrameType.REQ_OPEN, stream_id,
                       {"method": "GET", "path": path, "query": "",
                        "has_body": False})
    body = bytearray()
    status = None
    try:
        async def _wait():
            nonlocal status
            while True:
                for raw in list(channel.sent):
                    f = decode_frame(raw)
                    if f.stream_id != stream_id:
                        continue
                    if f.type == FrameType.RESP_HEAD:
                        status = f.header["status"]
                    if f.type == FrameType.RESP_DATA and f.eof:
                        return
                await asyncio.sleep(0.005)
        await asyncio.wait_for(_wait(), timeout=timeout)
    finally:
        channel.finish()
        await asyncio.wait_for(serve, timeout=timeout)
    for raw in channel.sent:
        f = decode_frame(raw)
        if f.type == FrameType.RESP_DATA and f.stream_id == stream_id:
            body += f.payload
    return status, (json.loads(body) if body else None)


# ============================================================ disabled relay

def test_disabled_relay_reports_off_and_direct(tmp_path, monkeypatch):
    """The default install: remote off, nothing dialed, and the request came in
    over the LAN. ``connected`` is False rather than unknown -- ``enabled`` is
    what tells "turned off" from "turned on and failing"."""
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.get("/api/remote/status")
        assert r.status_code == 200, r.text
        body = r.json()
    assert body == {"enabled": False, "home_id": None, "relay_host": None,
                    "connected": False, "last_error": None,
                    "since_unix": None, "gen": None, "via": "direct"}


def test_configured_but_not_running_reports_host_without_the_url(
        tmp_path, monkeypatch):
    """Relay configured, client not running (this app never dialed): enabled is
    True, connected is False, and only the HOSTNAME is echoed -- never the url,
    which can carry userinfo credentials."""
    store, app = _make_client(tmp_path, monkeypatch)
    store.set_remote(_relay_cfg())
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        body = c.get("/api/remote/status").json()
    assert body["enabled"] is True
    assert body["home_id"] == "home-1"
    assert body["relay_host"] == "relay.example.test"
    assert body["connected"] is False
    assert body["via"] == "direct"


def test_userinfo_credentials_never_reach_relay_host(tmp_path, monkeypatch):
    """A relay_url with embedded credentials yields the hostname alone. urlsplit
    .hostname excludes userinfo; a naive netloc would have shipped the password."""
    store, app = _make_client(tmp_path, monkeypatch)
    store.set_remote(RemoteConfig(
        enabled=True, relay_url="wss://user:hunter2@relay.example.test:8443/scope",
        device_token=TEST_DEVICE_TOKEN, home_id="home-1"))
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.get("/api/remote/status")
    assert r.json()["relay_host"] == "relay.example.test"
    assert "hunter2" not in r.text and "user:" not in r.text


# ============================================================ live client state

def test_status_follows_the_live_client(tmp_path, monkeypatch):
    """connected/gen/since come from the RelayClient object, not from config: a
    configured-but-dead relay and a connected one must not read the same."""
    store, app = _make_client(tmp_path, monkeypatch)
    store.set_remote(_relay_cfg())
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    relay_mod._current_client = client
    _install(principal_for_role("admin"))

    assert relay_status()["connected"] is False       # never dialed

    async def _drive():
        serve = asyncio.create_task(client._serve_once(client._config()))
        for _ in range(400):                          # until the HELLO is ACKed
            if client.status()["connected"]:
                break
            await asyncio.sleep(0.005)
        snap = client.status()
        channel.finish()
        await asyncio.wait_for(serve, timeout=5.0)
        return snap

    client._generation = 7
    live = asyncio.run(_drive())
    assert live["connected"] is True
    assert live["gen"] == 7
    assert live["since_unix"] and live["since_unix"] > 0
    # the socket closed -> connected drops back, so a badge cannot go stale
    assert client.status()["connected"] is False
    assert client.status()["since_unix"] is None


def test_a_dial_failure_is_reported_as_last_error(tmp_path, monkeypatch):
    """The run loop degrades to local-only instead of raising, so the failure
    would otherwise be visible ONLY in the log. last_error is the reason the
    badge is red."""
    _store, app = _make_client(tmp_path, monkeypatch)

    async def _boom(url):
        raise ConnectionRefusedError("relay unreachable")

    client = RelayClient(app, _relay_cfg, connect=_boom)

    async def _one_pass():
        task = asyncio.create_task(client.run())
        for _ in range(400):
            if client.status()["last_error"]:
                break
            await asyncio.sleep(0.005)
        client.stop()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(_one_pass())
    st = client.status()
    assert st["connected"] is False
    assert st["last_error"] and "ConnectionRefusedError" in st["last_error"]


def test_run_relay_client_publishes_and_clears_the_singleton(tmp_path, monkeypatch):
    """The route reads a module singleton; a boot with remote DISABLED must
    clear it, or a stopped client from a previous app reads as the live one."""
    _store, app = _make_client(tmp_path, monkeypatch)

    class _Stale:
        def status(self):
            return {"connected": True, "last_error": None,
                    "since_unix": 1.0, "gen": 99}

    relay_mod._current_client = _Stale()
    assert relay_status()["connected"] is True
    out = asyncio.run(relay_mod.run_relay_client(
        app, lambda: RemoteConfig(enabled=False)))
    assert out is None
    assert relay_mod._current_client is None
    assert relay_status() == {"connected": False, "last_error": None,
                              "since_unix": None, "gen": None}


# ============================================================ via: direct/relay

def test_a_tunneled_request_reports_via_relay(tmp_path, monkeypatch):
    """``via`` is derived from the ASGI scope flag the relay client stamps, not
    from a header and not from ``connected`` -- the same request served over the
    LAN says "direct"."""
    store, app = _make_client(tmp_path, monkeypatch)
    store.set_remote(_relay_cfg())
    _install(principal_for_role("admin"))
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    status, body = asyncio.run(_tunnel_get(client, channel, "/api/remote/status"))
    assert status == 200, body
    assert body["via"] == "relay"
    with TestClient(app) as c:
        assert c.get("/api/remote/status").json()["via"] == "direct"


def test_the_device_token_never_appears_in_the_response(tmp_path, monkeypatch):
    """The route reads the config object that HOLDS the token. Asserted on the
    raw text of both transports, so a future field addition that echoes the
    RemoteConfig wholesale goes red here."""
    store, app = _make_client(tmp_path, monkeypatch)
    store.set_remote(_relay_cfg())
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.get("/api/remote/status")
    assert TEST_DEVICE_TOKEN not in r.text
    assert "device_token" not in r.text
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    _status, body = asyncio.run(_tunnel_get(client, channel, "/api/remote/status"))
    assert TEST_DEVICE_TOKEN not in json.dumps(body)


# ============================================================ RBAC

def test_status_requires_view_status(tmp_path, monkeypatch):
    """view.status, so a viewer looking at the rig THROUGH the relay can see the
    link is up; a principal holding nothing cannot."""
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with())
    with TestClient(app) as c:
        assert c.get("/api/remote/status").status_code == 403
    for role in ("viewer", "operator", "admin"):
        _install(principal_for_role(role))
        with TestClient(app) as c:
            assert c.get("/api/remote/status").status_code == 200, role
    _install(_principal_with(CAP_VIEW_STATUS))
    with TestClient(app) as c:
        assert c.get("/api/remote/status").status_code == 200
