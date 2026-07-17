"""SPA catch-all API fence (H1).

The static-UI catch-all (`@app.get("/{path:path}")`) used to serve `index.html`
(200 text/html) for ANY unrouted GET -- including unknown paths under `/api/`
(e.g. GET `/api/auth`, which has no route). The client fetched those expecting
JSON and blew up with `Unexpected token '<', "<!doctype "...`.

The fence makes an unrouted path under `/api`, `/auth` or `/ws` return a JSON 404
instead of the HTML shell, while REAL routes (registered above the catch-all)
still match first and the SPA still serves `/` and deep client routes.

These tests monkeypatch `UI_DIST` to a synthetic built bundle so the catch-all is
deterministically mounted (a real `ui/dist` need not be present to run them).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore

INDEX_SENTINEL = "<!doctype html><title>astrodeck-spa-test</title>"


def _make_client(tmp_path, monkeypatch):
    """Isolated app with a synthetic ui/dist so the SPA catch-all is mounted."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)

    # Synthetic built bundle: index.html + an assets/ dir (StaticFiles mount needs
    # the directory to exist) so `UI_DIST.exists()` is true and the catch-all mounts.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_SENTINEL, encoding="utf-8")
    (dist / "assets" / "app.js").write_text("// built asset", encoding="utf-8")
    monkeypatch.setattr(app_module, "UI_DIST", dist)
    return app_module.create_app()


def test_unknown_api_path_is_json_404_not_html(tmp_path, monkeypatch):
    """The bug: GET /api/nope (no route) must be a JSON 404, NEVER the HTML shell."""
    app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/nope")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert "detail" in r.json()
        # Must NOT be the SPA document (that was the JSON-parse-error source).
        assert "<!doctype" not in r.text.lower()


def test_unknown_api_auth_path_is_json_404(tmp_path, monkeypatch):
    """GET /api/auth (the exact path the field report hit) 404s JSON, not HTML."""
    app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/auth")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert "<!doctype" not in r.text.lower()


def test_unknown_auth_path_is_json_404(tmp_path, monkeypatch):
    """An unrouted /auth/* GET is fenced too (auth surface, never a SPA route)."""
    app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/auth/nope")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert "<!doctype" not in r.text.lower()


def test_real_api_route_still_works(tmp_path, monkeypatch):
    """A real /api route (registered above the catch-all) still resolves 200."""
    app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200
        # The open login-signal route also still works and is JSON.
        r = c.get("/api/auth/methods")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")


def test_spa_shell_served_for_root_and_deep_client_routes(tmp_path, monkeypatch):
    """The SPA shell still serves `/` and deep client routes like `/login`."""
    app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        root = c.get("/")
        assert root.status_code == 200
        assert INDEX_SENTINEL in root.text
        # A client-side route with no file extension falls through to index.html.
        login = c.get("/login")
        assert login.status_code == 200
        assert INDEX_SENTINEL in login.text
        # A real static asset is served from disk (not the shell).
        asset = c.get("/assets/app.js")
        assert asset.status_code == 200
        assert "built asset" in asset.text
