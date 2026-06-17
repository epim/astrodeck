"""W2 RBAC ENFORCEMENT tests -- the require(capability) dependency wired onto
the live route surface in ``api/app.py``, the field-level POST /api/config +
PUT /api/site gating, the WS accept-time capability gate, the /api/me identity
surface, and the admin.users auth/revoke routes.

Repo convention (mirrors tests/test_auth.py): in-process fakes via monkeypatch +
TestClient, NO unittest.mock. A ``FakeAuthProvider`` installs a fixed Principal
through the CORE active-provider slot, so a single test can assert a viewer is
403'd on a control route while a 200 on a view route -- without any real OIDC.

HARD CONSTRAINT under test: with NO provider + NO token configured the server is
byte-for-byte today (open, caller == admin). T_default_* assert exactly that.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.auth.deps as deps
from astrodeck.auth import (ALL_CAPS, Principal, caps_for_role,
                            get_active_provider, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.config import AuthConfig, ConfigStore


# --------------------------------------------------------------------- harness

def _make_client(tmp_path, monkeypatch, *, token: str | None = None):
    """Isolated app + ConfigStore (mirrors tests/test_auth.py). The active auth
    provider is reset to the open default before each app build so a leaked
    provider from a prior test can't bleed in."""
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
    # The app lifespan re-installs the provider from AuthConfig on startup, which
    # would clobber a test-installed FakeAuthProvider the moment TestClient(app)
    # enters its context. Neutralize that re-install so a ``_install(...)`` BEFORE
    # the client survives. (Tests that WANT the configured provider call set_auth
    # + assert via the actual provider, not the fake.)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    return temp_store, app_module.create_app()


class FakeAuthProvider:
    """Returns a FIXED principal for every request (the test's identity). ``None``
    => unauthenticated (fail-closed). Installed via ``set_active_provider``."""

    name = "fake"

    def __init__(self, principal: "Principal | None"):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _install(principal):
    set_active_provider(FakeAuthProvider(principal))


@pytest.fixture(autouse=True)
def _reset_provider_after():
    """Every test ends with the open default restored (singleton hygiene)."""
    yield
    reset_active_provider()


# ===================================================== T-RBAC-1 non-breaking default

def test_default_open_no_provider_no_token(tmp_path, monkeypatch):
    """No provider, no token -> every surface reachable, caller is admin. The
    HARD CONSTRAINT: byte-for-byte today."""
    _store, app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200
        assert c.post("/api/connect/sim").status_code == 200
        # a control.mount route is reachable (admin) -- 409 (no mount) is fine,
        # the point is it is NOT 401/403.
        r = c.post("/api/mount/goto", json={"ra_hours": 5.0, "dec_deg": 10.0})
        assert r.status_code not in (401, 403)
        c.post("/api/disconnect")


def test_default_open_ws_hello(tmp_path, monkeypatch):
    """WS hello delivered with no creds under the open default."""
    _store, app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"


# ===================================================== T-RBAC-2 token still works

def test_token_composes_with_rbac(tmp_path, monkeypatch):
    """ASTRODECK_TOKEN set: token-less 401 (middleware), valid-token caller
    resolves admin (none provider) and reaches a control.mount route."""
    _store, app = _make_client(tmp_path, monkeypatch, token="s3cret")
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 401
        h = {"X-Auth-Token": "s3cret"}
        assert c.get("/api/status", headers=h).status_code == 200
        r = c.post("/api/mount/goto", headers=h,
                   json={"ra_hours": 5.0, "dec_deg": 10.0})
        assert r.status_code not in (401, 403)


# ===================================================== T-RBAC-3 viewer 403s

def test_viewer_blocked_on_control_and_media(tmp_path, monkeypatch):
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        # view surfaces OK
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/preview/1").status_code in (200, 404)  # 404 = expired, NOT 403
        # control / config / media => 403
        assert c.post("/api/mount/goto",
                      json={"ra_hours": 1.0, "dec_deg": 1.0}).status_code == 403
        assert c.post("/api/capture", json={}).status_code == 403
        assert c.post("/api/switch/set",
                      json={"port_id": 0, "value": 1.0}).status_code == 403
        assert c.post("/api/connect/sim").status_code == 403
        # raw FITS needs view.media which viewer lacks
        assert c.get("/api/preview/1/fits").status_code == 403


# ===================================================== T-RBAC-4 operator boundary

def test_operator_boundary(tmp_path, monkeypatch):
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("operator"))
    with TestClient(app) as c:
        # capture + guide allowed (200/409 depending on rig, NOT 403)
        assert c.post("/api/capture", json={}).status_code != 403
        assert c.post("/api/guide/dither", json={"pixels": 3.0}).status_code != 403
        # mount / power / sequence / polar / media => 403
        assert c.post("/api/sequence/start", json={"name": "x", "targets": []}
                      ).status_code == 403
        assert c.post("/api/sequence/recover").status_code == 403
        assert c.post("/api/polar/start").status_code == 403
        assert c.post("/api/mount/goto",
                      json={"ra_hours": 1.0, "dec_deg": 1.0}).status_code == 403
        assert c.post("/api/switch/set",
                      json={"port_id": 0, "value": 1.0}).status_code == 403
        assert c.get("/api/preview/1/fits").status_code == 403
        assert c.post("/api/connect/sim").status_code == 403


# ============================================ T-RBAC-7/8 field-level POST /api/config

def _principal_with(*caps):
    return Principal(role="custom", email=None, caps=frozenset(caps), jti=None)


def test_config_field_level_atomic_reject(tmp_path, monkeypatch):
    """A config.alerts-only principal posting {alerts, safety} => 403 AND the
    persisted config is UNCHANGED (no partial write, version identical)."""
    from astrodeck.auth import CAP_VIEW_STATUS, CAP_CONFIG_ALERTS
    store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_ALERTS))
    with TestClient(app) as c:
        before = store.cfg().version
        r = c.post("/api/config",
                   json={"alerts": [], "safety": {"min_alt_deg": 5}})
        assert r.status_code == 403
        assert store.cfg().version == before  # nothing merged


def test_config_escalation_rides_alerts(tmp_path, monkeypatch):
    """escalation is gated by config.alerts (NOT config.safety)."""
    from astrodeck.auth import (CAP_VIEW_STATUS, CAP_CONFIG_ALERTS,
                                CAP_CONFIG_SAFETY)
    store, app = _make_client(tmp_path, monkeypatch)
    # config.alerts principal -> escalation-only post succeeds
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_ALERTS))
    with TestClient(app) as c:
        assert c.post("/api/config", json={"escalation": {}}).status_code == 200
    # config.safety (but NOT alerts) principal -> escalation post is 403
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SAFETY))
    with TestClient(app) as c:
        assert c.post("/api/config", json={"escalation": {}}).status_code == 403


def test_config_unknown_block_rejected(tmp_path, monkeypatch):
    """A body carrying an unknown/auth/remote key => REJECTED, merge NOTHING.

    ConfigPatchBody is ``extra="forbid"`` so a stray ``auth`` block is rejected at
    binding (422) and never reaches the merge -- fail-closed. Separately, a viewer
    (no config caps) posting a real block is 403 and the store is untouched."""
    from astrodeck.auth import CAP_VIEW_STATUS, CAP_ADMIN_USERS
    store, app = _make_client(tmp_path, monkeypatch)
    # Even an admin-everything principal cannot smuggle an auth block through the
    # config merge -- the body is rejected before any handler logic runs.
    _install(_principal_with(*ALL_CAPS))
    with TestClient(app) as c:
        before = store.cfg().version
        r = c.post("/api/config", json={"auth": {"revoked_jti": []}})
        assert r.status_code == 422  # forbidden extra block -> rejected
        assert store.cfg().version == before
    # Floor-only principal posting a real block => 403, store untouched.
    _install(_principal_with(CAP_VIEW_STATUS))
    with TestClient(app) as c:
        before = store.cfg().version
        assert c.post("/api/config", json={"safety": {"min_alt_deg": 5}}
                      ).status_code == 403
        assert store.cfg().version == before


# ============================================ T-RBAC-9 field-level PUT /api/site

def test_site_field_level(tmp_path, monkeypatch):
    """site coords need config.site_optics; a present horizon_min_deg ALSO needs
    config.safety."""
    from astrodeck.auth import (CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                                CAP_CONFIG_SAFETY)
    store, app = _make_client(tmp_path, monkeypatch)
    site = {"latitude": 40.0, "longitude": -74.0, "elevation_m": 10.0}
    # site_optics only + body carries horizon_min_deg => 403 (needs config.safety)
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.put("/api/site", json={"site": site, "horizon_min_deg": 20.0})
        assert r.status_code == 403
        # coords-only => 200
        assert c.put("/api/site", json={"site": site}).status_code == 200
    # with BOTH caps, horizon_min_deg is allowed
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                             CAP_CONFIG_SAFETY))
    with TestClient(app) as c:
        r = c.put("/api/site", json={"site": site, "horizon_min_deg": 20.0})
        assert r.status_code == 200


# ============================================ T-RBAC-10 revoked_jti not shrinkable

def test_revoked_jti_not_shrinkable_via_config(tmp_path, monkeypatch):
    """A config.* but NOT admin.users principal cannot touch auth at all via
    POST /api/config (auth is an unmapped block) -- the registry survives."""
    from astrodeck.auth import CAP_VIEW_STATUS, CAP_CONFIG_SAFETY
    store, app = _make_client(tmp_path, monkeypatch)
    store.set_auth(AuthConfig(revoked_jti=["x"]))
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SAFETY))
    with TestClient(app) as c:
        r = c.post("/api/config", json={"auth": {"revoked_jti": []}})
        assert r.status_code == 422  # auth is a forbidden block on ConfigPatchBody
        # registry still intact
        assert "x" in store.cfg().auth.revoked_jti


# ============================================ T-RBAC-11 /api/me fail-closed

def test_me_fail_closed_and_roles(tmp_path, monkeypatch):
    _store, app = _make_client(tmp_path, monkeypatch)
    # unauthenticated provider -> 401 (never default-admin)
    _install(None)
    with TestClient(app) as c:
        assert c.get("/api/me").status_code == 401
    # viewer principal -> role viewer, caps == viewer set, NOT admin
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        r = c.get("/api/me")
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "viewer"
        assert set(body["caps"]) == set(caps_for_role("viewer"))
        assert set(body["caps"]) != set(ALL_CAPS)


def test_me_under_none_provider_is_admin(tmp_path, monkeypatch):
    """Open default (none provider) -> /api/me returns admin/ALL_CAPS."""
    _store, app = _make_client(tmp_path, monkeypatch)  # reset_active_provider -> none
    with TestClient(app) as c:
        body = c.get("/api/me").json()
        assert body["role"] == "admin"
        assert set(body["caps"]) == set(ALL_CAPS)


# ============================================ T-RBAC-12 WS accept gate

def test_ws_viewer_allowed_status(tmp_path, monkeypatch):
    """A viewer holds view.status -> WS hello delivered."""
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_ws_without_status_cap_rejected(tmp_path, monkeypatch):
    """A principal lacking view.status is closed 1008 before accept."""
    from starlette.websockets import WebSocketDisconnect
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with("view.preview"))  # NO view.status
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()


def test_ws_unauthenticated_rejected(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(None)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()


# ============================================ admin.users auth routes

def test_auth_config_admin_only(tmp_path, monkeypatch):
    """POST /api/auth/config requires admin.users; a viewer is 403, an admin can
    write + the provider re-installs."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.post("/api/auth/config",
                      json={"provider": "none"}).status_code == 403
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.post("/api/auth/config",
                   json={"provider": "none", "admin_token": "sekret"})
        assert r.status_code == 200
        # the response is the REDACTED auth block -- secret blanked, flag surfaced
        assert r.json().get("admin_token") == ""
        assert r.json().get("admin_token_configured") is True


def test_auth_revoke_append_only(tmp_path, monkeypatch):
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.post("/api/auth/revoke", json={"jti": "abc"})
        assert r.status_code == 200
        assert "abc" in r.json()["revoked"]
        assert "abc" in store.cfg().auth.revoked_jti
        # unrevoke removes it (the only path that may shrink the registry)
        r2 = c.post("/api/auth/unrevoke", json={"jti": "abc"})
        assert r2.status_code == 200
        assert "abc" not in store.cfg().auth.revoked_jti


def test_auth_revoke_viewer_forbidden(tmp_path, monkeypatch):
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.post("/api/auth/revoke", json={"jti": "abc"}).status_code == 403
