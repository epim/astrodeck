"""Optional shared-token auth (P0-4).

Auth is OFF by default — when ``ASTRODECK_TOKEN`` is unset the server behaves
EXACTLY as before (fully open), so the live LAN tablet keeps working. When the
token IS set, every REST control route and the WebSocket require it; the UI shell
+ static assets stay open so the browser can load the bundle.

These tests assert BOTH directions: open-when-unset (the non-breaking guarantee)
and required-when-set.
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore

TOKEN = "s" * 32


def _make_client(tmp_path, monkeypatch, token: str | None, *,
                 bind_host: str | None = None, allowed_hosts: str | None = None):
    """Build an isolated app with (or without) ASTRODECK_TOKEN set. The token is
    read live by the auth middleware, so it must be set BEFORE create_app()."""
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
    return app_module.create_app(bind_host=bind_host,
                                 allowed_hosts=allowed_hosts)


# ----------------------------------------------------- OFF by default (open)

def test_auth_disabled_by_default_is_open(tmp_path, monkeypatch):
    """No token set → identical to today: every route is reachable, no header."""
    app = _make_client(tmp_path, monkeypatch, token=None)
    assert app_module.auth_enabled() is False
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/config").status_code == 200
        # a control route works with no credentials
        assert c.post("/api/connect/sim").status_code == 200
        c.post("/api/disconnect")


def test_empty_token_is_treated_as_disabled(tmp_path, monkeypatch):
    """An empty/whitespace ASTRODECK_TOKEN must NOT silently lock everyone out —
    it counts as 'no token' so a stray var in a launcher can't brick LAN access."""
    app = _make_client(tmp_path, monkeypatch, token="   ")
    assert app_module.auth_enabled() is False
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200


def test_weak_direct_token_is_rejected_not_accepted(tmp_path, monkeypatch):
    _make_client(tmp_path, monkeypatch, token="guessable")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        app_module.auth_enabled()


# ----------------------------------------------------- ON when token set

def test_auth_required_when_token_set(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    assert app_module.auth_enabled() is True
    with TestClient(app) as c:
        # no token → 401 on the control surface
        assert c.get("/api/status").status_code == 401
        assert c.post("/api/connect/sim").status_code == 401
        # wrong token → 401
        assert c.get("/api/status",
                     headers={"X-Auth-Token": "nope"}).status_code == 401


def test_auth_accepts_all_carriers(tmp_path, monkeypatch):
    """X-Auth-Token header, Authorization: Bearer, and ?token= all unlock."""
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        assert c.get("/api/status",
                     headers={"X-Auth-Token": TOKEN}).status_code == 200
        assert c.get("/api/status",
                     headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert c.get(f"/api/status?token={TOKEN}").status_code == 200


def test_ui_shell_open_even_with_token(tmp_path, monkeypatch):
    """Static assets / SPA shell must stay open so the browser can load the bundle
    and THEN attach the token to its API calls. Only /api + /ws are gated."""
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        # An SPA deep link (no extension, not /api) falls through to the shell.
        # Without a built ui/dist this 404s, but it must NOT 401 (it's open).
        assert c.get("/some/spa/route").status_code != 401


def test_cross_origin_browser_mutations_are_denied(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        denied = c.post(
            "/api/config", json={}, headers={"Origin": "https://evil.example"})
        assert denied.status_code == 403
        assert denied.json()["code"] == "invalid_origin"
        # A same-site but different-origin page (for example another service on
        # the same hostname) is also denied by Fetch Metadata.
        assert c.post(
            "/api/config", json={},
            headers={"Origin": "http://testserver",
                     "Sec-Fetch-Site": "same-site"}).status_code == 403
        assert c.post(
            "/api/config", json={},
            headers={"Origin": "http://testserver",
                     "Sec-Fetch-Site": "same-origin"}).status_code != 403


def test_loopback_listener_rejects_dns_rebinding_host(tmp_path, monkeypatch):
    """Matching hostile Host+Origin is not same-origin authorization.

    A page on evil.example can DNS-rebind that name to 127.0.0.1.  The Host
    allowlist must reject it before the open-default admin provider is reached.
    """
    app = _make_client(tmp_path, monkeypatch, token=None,
                       bind_host="127.0.0.1")
    with TestClient(app) as c:
        headers = {
            "Host": "evil.example:8800",
            "Origin": "http://evil.example:8800",
            "Sec-Fetch-Site": "same-origin",
        }
        denied = c.post("/api/config", json={}, headers=headers)
        assert denied.status_code == 421
        assert denied.json()["code"] == "invalid_host"
        assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 421
        assert c.get("/api/status", headers={"Host": "127.0.0.1:8800"}).status_code == 200


def _tunneled(app):
    """Stamp every http/websocket scope exactly as the relay client does (ASGI
    scope STATE, never a header), so the app sees a relay-tunneled request."""
    async def _wrapped(scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            scope.setdefault("state", {})["astrodeck_remote"] = True
        await app(scope, receive, send)
    return _wrapped


def test_relay_tunneled_requests_bypass_the_listener_host_allowlist(
        tmp_path, monkeypatch):
    """The Host allowlist guards the listener. A tunneled request carries the
    relay's public hostname, which is never a listener name: 0.3.23 checked it
    first and answered 421 to every relay request, so the remote display
    "kept disconnecting" while the LAN worked. The relay client marks the scope
    in ASGI state (not forgeable over the wire), and that mark, not the Host,
    is what says the request did not arrive on the listener."""
    app = _make_client(tmp_path, monkeypatch, token=None, bind_host="127.0.0.1")
    relay_host = {"Host": "astrodeck-relay.fly.dev"}
    with TestClient(app) as c:
        # On the listener the relay's name is rebinding-shaped and stays refused.
        assert c.get("/healthz", headers=relay_host).status_code == 421
    with TestClient(_tunneled(app)) as t:
        assert t.get("/healthz", headers=relay_host).status_code == 200
        # A custom domain in front of the relay is equally fine on the tunnel:
        # the dial address and the public name need not match.
        assert t.get("/healthz", headers={"Host": "scope.example.org"}).status_code == 200

        # The websocket gate must agree. Its host denial and its auth denial
        # both close 1008, so grade the mechanism: the listener allowlist must
        # not be consulted at all for a tunneled socket.
        def _not_for_tunnels(headers, allowed):
            raise AssertionError("listener Host allowlist consulted for a tunneled scope")
        monkeypatch.setattr(app_module, "_host_allowed", _not_for_tunnels)
        with contextlib.suppress(WebSocketDisconnect):
            with t.websocket_connect("/ws", headers=relay_host):
                pass


def test_explicit_public_host_is_exact_not_wildcard(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None,
                       bind_host="0.0.0.0", allowed_hosts="rig.example")
    with TestClient(app) as c:
        assert c.get("/healthz", headers={"Host": "rig.example:8443"}).status_code == 200
        assert c.get("/healthz", headers={"Host": "sub.rig.example"}).status_code == 421


def test_security_headers_cover_ui_and_api(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app, base_url="https://rig.test") as c:
        r = c.get("/healthz")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["strict-transport-security"] == "max-age=31536000"


def test_declared_and_chunked_request_bodies_are_bounded(
        tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        r = c.post(
            "/api/config", content=b"{}",
            headers={"Content-Length": str(app_module.MAX_REQUEST_BODY_BYTES + 1),
                     "Content-Type": "application/json"})
        assert r.status_code == 413, r.text

        monkeypatch.setattr(app_module, "MAX_REQUEST_BODY_BYTES", 8)

        def chunks():
            yield b'{"x":"'
            yield b'0123456789"}'

        r = c.post(
            "/api/config", content=chunks(),
            headers={"Content-Type": "application/json"})
        assert r.status_code == 413, r.text


# ----------------------------------------------------- websocket gate

def test_ws_open_when_no_token(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"


def test_ws_rejected_without_token_when_set(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()


def test_ws_accepts_token_query_when_set(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        with c.websocket_connect(f"/ws?token={TOKEN}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"


def test_ws_rejects_cross_origin_browser(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                    "/ws", headers={"Origin": "https://evil.example"}) as ws:
                ws.receive_json()
