"""Optional shared-token auth (P0-4).

Auth is OFF by default — when ``ASTRODECK_TOKEN`` is unset the server behaves
EXACTLY as before (fully open), so the live LAN tablet keeps working. When the
token IS set, every REST control route and the WebSocket require it; the UI shell
+ static assets stay open so the browser can load the bundle.

These tests assert BOTH directions: open-when-unset (the non-breaking guarantee)
and required-when-set.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


def _make_client(tmp_path, monkeypatch, token: str | None):
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
    return app_module.create_app()


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


# ----------------------------------------------------- ON when token set

def test_auth_required_when_token_set(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token="s3cret")
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
    app = _make_client(tmp_path, monkeypatch, token="s3cret")
    with TestClient(app) as c:
        assert c.get("/api/status",
                     headers={"X-Auth-Token": "s3cret"}).status_code == 200
        assert c.get("/api/status",
                     headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/status?token=s3cret").status_code == 200


def test_ui_shell_open_even_with_token(tmp_path, monkeypatch):
    """Static assets / SPA shell must stay open so the browser can load the bundle
    and THEN attach the token to its API calls. Only /api + /ws are gated."""
    app = _make_client(tmp_path, monkeypatch, token="s3cret")
    with TestClient(app) as c:
        # An SPA deep link (no extension, not /api) falls through to the shell.
        # Without a built ui/dist this 404s, but it must NOT 401 (it's open).
        assert c.get("/some/spa/route").status_code != 401


# ----------------------------------------------------- websocket gate

def test_ws_open_when_no_token(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"


def test_ws_rejected_without_token_when_set(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    app = _make_client(tmp_path, monkeypatch, token="s3cret")
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()


def test_ws_accepts_token_query_when_set(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token="s3cret")
    with TestClient(app) as c:
        with c.websocket_connect("/ws?token=s3cret") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
