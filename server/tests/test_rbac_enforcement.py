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

import math

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
        # mount / sequence / polar now allowed too (2026-07-17 decisions wave
        # I1: operator holds control.mount -- 200/409 depending on rig, NOT 403).
        assert c.post("/api/sequence/start", json={"name": "x", "targets": []}
                      ).status_code != 403
        assert c.post("/api/sequence/recover").status_code != 403
        assert c.post("/api/polar/start").status_code != 403
        assert c.post("/api/mount/goto",
                      json={"ra_hours": 1.0, "dec_deg": 1.0}).status_code != 403
        # power / media stay operator-denied.
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


# ==================================== T-RBAC-13 view.site_precise strip-entirely
# view.site_precise (admin-only; EXCLUDED from viewer/operator) gates the
# observatory's EXACT GPS fix. A principal lacking it must see the four precise
# keys (name/latitude/longitude/elevation_m) REMOVED (absent, not nulled) on
# EVERY precise-site surface (REST status/summary/config + the WS hello frame),
# while is_default + horizon_min_deg are retained; a holder sees the full block.

_PRECISE_LAT = 40.123456
_PRECISE_LON = -74.654321
_PRECISE_ELEV = 123.4
_PRECISE_NAME = "Secret Barn"
_STRIP_KEYS = ("name", "latitude", "longitude", "elevation_m")


def _seed_precise_site(store):
    from astrodeck.config import Site
    store.set_site(Site(name=_PRECISE_NAME, latitude=_PRECISE_LAT,
                        longitude=_PRECISE_LON, elevation_m=_PRECISE_ELEV))


def _assert_site_stripped(site):
    for k in _STRIP_KEYS:
        assert k not in site, f"{k} must be ABSENT for a non-holder"
    assert "is_default" in site, "is_default must be retained"
    assert "horizon_min_deg" in site, "horizon_min_deg must be retained"


def test_site_stripped_for_viewer(tmp_path, monkeypatch):
    """A viewer (no view.site_precise) gets the four precise keys REMOVED on
    status, config, summary (both the top-level site block and the duplicate
    copy inside the embedded config) AND the WS hello frame; is_default and
    horizon_min_deg remain."""
    store, app = _make_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        _assert_site_stripped(c.get("/api/status").json()["site"])
        _assert_site_stripped(c.get("/api/config").json()["site"])
        summ = c.get("/api/summary").json()
        _assert_site_stripped(summ["site"])
        _assert_site_stripped(summ["config"]["site"])
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            _assert_site_stripped(hello["data"]["site"])
            _assert_site_stripped(hello["data"]["config"]["site"])


def test_site_full_for_admin(tmp_path, monkeypatch):
    """An admin holds view.site_precise -> the exact name/lat/lon/elevation
    everywhere, untouched."""
    store, app = _make_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        st = c.get("/api/status").json()["site"]
        assert st["latitude"] == _PRECISE_LAT and st["longitude"] == _PRECISE_LON
        assert st["elevation_m"] == _PRECISE_ELEV and st["name"] == _PRECISE_NAME
        cfg = c.get("/api/config").json()["site"]
        assert cfg["latitude"] == _PRECISE_LAT and cfg["name"] == _PRECISE_NAME
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["data"]["site"]["latitude"] == _PRECISE_LAT
            assert hello["data"]["config"]["site"]["latitude"] == _PRECISE_LAT


# ============================== T-RBAC-13b config-write echo strip (bridge review)
# Whole-branch review finding: GET /api/config wrapped its response in
# _redact_site_for, but ``_config_payload()`` itself did NOT strip site
# precision, and every config-WRITE route echoed it back BARE. A plain viewer
# could reach it via an empty-body POST /api/config (``{}`` passes
# ``_require_config_field_caps`` vacuously, floor is only view.status). Fixed
# by moving the strip INSIDE ``_config_payload(principal)`` so every caller
# (read or write) redacts identically. These tests pin the fix at the two
# routes that most directly demonstrate the leak: POST /api/config (the exact
# empty-body reproduction) and PUT /api/site (the documented edge -- a caller
# who may WRITE site coords need not also hold view.site_precise to READ them
# back).

@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_post_config_empty_body_echo_stripped_for_non_holder(tmp_path, monkeypatch,
                                                              role):
    """viewer/operator POST /api/config {} -> 200 (empty body passes field-cap
    check vacuously) AND the echoed site (top-level + the embedded config.site,
    if present) has the four precise keys ABSENT, keeping is_default/horizon.
    Same assertion for both roles -- both lack view.site_precise."""
    store, app = _make_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(principal_for_role(role))
    with TestClient(app) as c:
        r = c.post("/api/config", json={})
        assert r.status_code == 200
        body = r.json()
        _assert_site_stripped(body["site"])
        cfg_site = body.get("config", {}).get("site") if isinstance(
            body.get("config"), dict) else None
        if cfg_site is not None:
            _assert_site_stripped(cfg_site)


def test_post_config_empty_body_echo_full_for_admin(tmp_path, monkeypatch):
    """admin POST /api/config {} -> full precise echo, holder untouched (no
    regression: the structural fix must not narrow what a holder sees)."""
    store, app = _make_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.post("/api/config", json={})
        assert r.status_code == 200
        site = r.json()["site"]
        assert site["latitude"] == _PRECISE_LAT
        assert site["longitude"] == _PRECISE_LON
        assert site["elevation_m"] == _PRECISE_ELEV
        assert site["name"] == _PRECISE_NAME


def test_put_site_echo_stripped_for_non_holder_writer(tmp_path, monkeypatch):
    """A principal holding config.site_optics + view.status but NOT
    view.site_precise -- i.e. may WRITE the site but not READ it back precisely
    -- does PUT /api/site with valid coords -> 200 AND the echoed site is
    stripped. This is the spec's documented edge: the write cap and the
    precise-read cap are independent."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with("config.site_optics", "view.status"))
    with TestClient(app) as c:
        r = c.put("/api/site", json={"site": {
            "name": _PRECISE_NAME, "latitude": _PRECISE_LAT,
            "longitude": _PRECISE_LON, "elevation_m": _PRECISE_ELEV}})
        assert r.status_code == 200
        _assert_site_stripped(r.json()["site"])


def test_put_site_version_conflict_body_stripped_for_non_holder(tmp_path,
                                                                monkeypatch):
    """The 409 version-conflict body used to hand back ``e.current.model_dump()``
    RAW -- bypassing both ``redacted`` (secrets) and ``_redact_site_for`` (site
    precision). A config.site_optics-without-view.site_precise caller sending a
    stale ``version`` -> 409 AND the ``current`` payload's site is stripped
    (optics_computed extras intact)."""
    store, app = _make_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(_principal_with("config.site_optics", "view.status"))
    stale = store.cfg().version + 1000
    with TestClient(app) as c:
        r = c.put("/api/site", json={
            "site": {"latitude": 1.0, "longitude": 2.0, "elevation_m": 3.0},
            "version": stale})
        assert r.status_code == 409
        current = r.json()["detail"]["current"]
        _assert_site_stripped(current["site"])
        assert "optics_computed" in current  # the extras still ride the body
        # the store's precise site is untouched by the failed write
        assert store.cfg().site.latitude == _PRECISE_LAT


def test_put_site_version_conflict_body_full_for_admin(tmp_path, monkeypatch):
    """An admin (holds view.site_precise) hitting the same stale-version 409
    gets the full precise site back in ``current`` -- holder behavior identical."""
    store, app = _make_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(principal_for_role("admin"))
    stale = store.cfg().version + 1000
    with TestClient(app) as c:
        r = c.put("/api/site", json={
            "site": {"latitude": 1.0, "longitude": 2.0, "elevation_m": 3.0},
            "version": stale})
        assert r.status_code == 409
        site = r.json()["detail"]["current"]["site"]
        assert site["latitude"] == _PRECISE_LAT
        assert site["longitude"] == _PRECISE_LON
        assert site["elevation_m"] == _PRECISE_ELEV
        assert site["name"] == _PRECISE_NAME


# ==================================== T-RBAC-14 WS periodic re-authentication
# Auth on the long-lived /ws is otherwise checked ONLY at accept, so a revoked
# session / expired token would keep streaming for the whole all-night run. The
# send loop re-resolves the principal every WS_AUTH_RECHECK_S and closes 4401 the
# moment it stops resolving.

class _SwitchProvider:
    """Resolves ``self.principal`` -- flip it mid-connection to model a revoke."""
    name = "fake"

    def __init__(self, principal):
        self.principal = principal

    async def resolve(self, request):
        return self.principal


def test_ws_revoked_principal_closes_4401(tmp_path, monkeypatch):
    """A live socket whose principal STOPS resolving (revoked jti / expired exp)
    is closed with 4401 by the periodic re-auth, not left streaming forever."""
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(app_module, "WS_AUTH_RECHECK_S", 0.05)
    _store, app = _make_client(tmp_path, monkeypatch)
    prov = _SwitchProvider(principal_for_role("viewer"))
    set_active_provider(prov)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            # revocation: the live provider now resolves nobody.
            prov.principal = None
            with pytest.raises(WebSocketDisconnect) as ei:
                for _ in range(500):
                    ws.receive_json()
            assert ei.value.code == 4401


def test_ws_valid_principal_survives_recheck(tmp_path, monkeypatch):
    """A still-valid principal is NOT false-closed by the periodic re-auth: with a
    tiny recheck interval the loop re-resolves many times, then a bus event pushed
    FROM the event loop still reaches the socket -- proving it stayed open."""
    monkeypatch.setattr(app_module, "WS_AUTH_RECHECK_S", 0.02)
    _store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            # Drive a bus event through the app's OWN event loop (portal) so it is
            # enqueued thread-safely on the WS subscriber queue. It arrives only if
            # the socket survived the several rechecks that fired before this ran.
            from astrodeck.events import bus
            c.portal.call(bus.log, "info", "still-alive", "test")
            got = None
            for _ in range(50):
                ev = ws.receive_json()
                if ev.get("type") == "log":
                    got = ev
                    break
            assert got is not None and got["data"]["message"] == "still-alive"


# ============================== site/sky geolocator strip + visibility gating

def test_site_sky_strips_geolocators_for_viewer(tmp_path, monkeypatch):
    """A viewer lacks view.site_precise -> /api/site/sky omits place_hint and
    lst_str but keeps sun_alt_deg + dark_window (ephemeris kept by decision)."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        r = c.get("/api/site/sky").json()
        assert "place_hint" not in r and "lst_str" not in r
        assert "sun_alt_deg" in r and "dark_window" in r


def test_site_sky_full_for_admin(tmp_path, monkeypatch):
    """An admin holds view.site_precise -> all four fields present."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.get("/api/site/sky").json()
        assert "place_hint" in r and "lst_str" in r
        assert "sun_alt_deg" in r and "dark_window" in r


def test_visibility_fail_closed_for_unauthenticated(tmp_path, monkeypatch):
    """Both visibility routes now require view.status; an unauthenticated caller
    (provider resolves None) is refused fail-closed (401/403), not served."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(None)
    with TestClient(app) as c:
        assert c.get("/api/visibility?ra=5&dec=10").status_code in (401, 403)
        assert c.post("/api/visibility/order",
                      json={"targets": []}).status_code in (401, 403)


def test_visibility_allows_viewer(tmp_path, monkeypatch):
    """A viewer (view.status) gets 200 from the visibility ephemeris."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.get("/api/visibility?ra=5&dec=10").status_code == 200
        assert c.post("/api/visibility/order",
                      json={"targets": []}).status_code == 200


# ================================================ /api/site/mount-gps read-back

class _FakeTel:
    """A minimal connected Alpaca-like telescope exposing async _get for the
    three site properties (mirrors _AlpacaDevice._get)."""

    def __init__(self, lat, lon, elev):
        self.connected = True
        self._vals = {"sitelatitude": lat, "sitelongitude": lon,
                      "siteelevation": elev}

    async def _get(self, method):
        return self._vals[method]


def test_mount_gps_viewer_forbidden(tmp_path, monkeypatch):
    """A viewer lacks config.site_optics -> 403 (the read-back exposes precise
    coordinates)."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.get("/api/site/mount-gps").status_code == 403


def test_mount_gps_no_mount(tmp_path, monkeypatch):
    """config.site_optics holder, no mount connected -> 200 {available: false}."""
    import astrodeck.hub as hub_mod
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    store, app = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(hub_mod.hub, "devices", {})
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.get("/api/site/mount-gps")
        assert r.status_code == 200 and r.json()["available"] is False


def test_mount_gps_reports_coords(tmp_path, monkeypatch):
    """A fake mount reporting valid coords -> the values are echoed."""
    import astrodeck.hub as hub_mod
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    store, app = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(hub_mod.hub, "devices",
                        {"telescope": _FakeTel(40.5, -74.5, 30.0)})
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.get("/api/site/mount-gps").json()
        assert r["available"] is True
        assert r["latitude"] == 40.5 and r["longitude"] == -74.5
        assert r["elevation_m"] == 30.0


@pytest.mark.parametrize(
    "lat, lon, elev, expect_gps_detail",
    [
        pytest.param(0.0, 0.0, 0.0, True,
                    id="zero_zero_is_unset_sentinel"),
        pytest.param(99.0, 181.0, 0.0, False,
                    id="out_of_range_rejected"),
        pytest.param(math.nan, -74.5, 30.0, False,
                    id="nan_latitude_rejected"),
    ],
)
def test_mount_gps_bad_sentinels_rejected(tmp_path, monkeypatch, lat, lon, elev,
                                          expect_gps_detail):
    """Bad sentinel (lat, lon, elev) tuples all -> available: false:
    exactly (0.0, 0.0) is the GPS-unset sentinel; junk out-of-Site-range
    values (99.0/181.0); and a NaN latitude (non-finite guard). The
    zero/zero case additionally surfaces "GPS" in the detail message."""
    import astrodeck.hub as hub_mod
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    store, app = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(hub_mod.hub, "devices",
                        {"telescope": _FakeTel(lat, lon, elev)})
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.get("/api/site/mount-gps").json()
        assert r["available"] is False
        if expect_gps_detail:
            assert "GPS" in r["detail"]


def test_mount_gps_no_get_attribute(tmp_path, monkeypatch):
    """A connected telescope object with NO ``_get`` -> available: false (not an
    Alpaca-backed device we can read GPS from)."""
    import astrodeck.hub as hub_mod
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS

    class _NoGetTel:
        connected = True

    store, app = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(hub_mod.hub, "devices", {"telescope": _NoGetTel()})
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.get("/api/site/mount-gps").json()
        assert r["available"] is False


# ================================================= /api/locations routes + RBAC

def _wire_locations(tmp_path, monkeypatch, app):
    """Point the location_store singleton at a temp file for this test."""
    import astrodeck.locations as loc_mod
    from astrodeck.locations import LocationStore
    temp = LocationStore(path=tmp_path / "locations.json")
    monkeypatch.setattr(loc_mod, "location_store", temp)
    monkeypatch.setattr(app_module, "location_store", temp)
    return temp


def test_locations_rbac_viewer_and_operator_forbidden(tmp_path, monkeypatch):
    """All four routes require config.site_optics: viewer AND operator -> 403."""
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    body = {"name": "Home", "latitude": 40.0, "longitude": -74.0,
            "elevation_m": 12.0}
    for role in ("viewer", "operator"):
        _install(principal_for_role(role))
        with TestClient(app) as c:
            assert c.get("/api/locations").status_code == 403
            assert c.post("/api/locations", json=body).status_code == 403
            assert c.put("/api/locations/x", json=body).status_code == 403
            assert c.delete("/api/locations/x").status_code == 403


def test_locations_holder_full_access_and_error_codes(tmp_path, monkeypatch):
    """A config.site_optics holder gets full CRUD; collision -> 409 name_collision
    + existing id; unknown id -> 404."""
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    body = {"name": "Home", "latitude": 40.0, "longitude": -74.0,
            "elevation_m": 12.0}
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.post("/api/locations", json=body)
        assert r.status_code == 200
        lid = r.json()["id"]
        assert len(c.get("/api/locations").json()) == 1
        # case-insensitive collision -> 409 {code, id}
        rc = c.post("/api/locations", json={**body, "name": "home"})
        assert rc.status_code == 409
        assert rc.json()["detail"]["code"] == "name_collision"
        assert rc.json()["detail"]["id"] == lid
        # rename onto a fresh name -> 200
        assert c.put(f"/api/locations/{lid}",
                     json={**body, "name": "Renamed"}).status_code == 200
        # unknown id -> 404 on PUT and DELETE
        assert c.put("/api/locations/nope", json=body).status_code == 404
        assert c.delete("/api/locations/nope").status_code == 404
        # delete real -> 200, list empty
        assert c.delete(f"/api/locations/{lid}").status_code == 200
        assert c.get("/api/locations").json() == []


def test_locations_library_full_409(tmp_path, monkeypatch):
    """A create beyond MAX_LOCATIONS -> 409 {code: library_full}."""
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    from astrodeck.locations import MAX_LOCATIONS
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        for i in range(MAX_LOCATIONS):
            assert c.post("/api/locations",
                          json={"name": f"L{i}", "latitude": 1.0,
                                "longitude": 2.0, "elevation_m": 0.0}
                          ).status_code == 200
        r = c.post("/api/locations",
                   json={"name": "over", "latitude": 1.0, "longitude": 2.0,
                         "elevation_m": 0.0})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "library_full"


def test_locations_out_of_range_body_422(tmp_path, monkeypatch):
    """Out-of-range coordinates are rejected AT THE BOUNDARY by LocationBody's
    Field constraints -> 422 (never a 500 from an uncaught ValidationError
    inside the store): POST with latitude 999 and PUT with longitude -999."""
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.post("/api/locations",
                   json={"name": "Bad", "latitude": 999.0, "longitude": 2.0,
                         "elevation_m": 0.0})
        assert r.status_code == 422
        # a valid location to target with the bad PUT
        lid = c.post("/api/locations",
                     json={"name": "Good", "latitude": 1.0, "longitude": 2.0,
                           "elevation_m": 0.0}).json()["id"]
        r = c.put(f"/api/locations/{lid}",
                  json={"name": "Good", "latitude": 1.0, "longitude": -999.0,
                        "elevation_m": 0.0})
        assert r.status_code == 422


def test_locations_empty_name_422(tmp_path, monkeypatch):
    """A name that is empty after trimming is rejected AT THE BOUNDARY (spec
    §4): POST with '' -> 422; PUT renaming a real location to '   ' -> 422."""
    from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.post("/api/locations",
                   json={"name": "", "latitude": 1.0, "longitude": 2.0,
                         "elevation_m": 0.0})
        assert r.status_code == 422
        lid = c.post("/api/locations",
                     json={"name": "Good", "latitude": 1.0, "longitude": 2.0,
                           "elevation_m": 0.0}).json()["id"]
        r = c.put(f"/api/locations/{lid}",
                  json={"name": "   ", "latitude": 1.0, "longitude": 2.0,
                        "elevation_m": 0.0})
        assert r.status_code == 422


def test_locations_absent_from_all_payloads(tmp_path, monkeypatch):
    """The library is served ONLY by /api/locations — never in status/summary/
    config or the WS hello (the §2 strip seam needs no change for it)."""
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        assert "locations" not in c.get("/api/status").json()
        assert "locations" not in c.get("/api/config").json()
        summ = c.get("/api/summary").json()
        assert "locations" not in summ
        assert "locations" not in summ.get("config", {})
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert "locations" not in hello["data"]
            assert "locations" not in hello["data"].get("config", {})


def test_mosaic_transit_alt_needs_a_principal(tmp_path, monkeypatch):
    """POST /api/framing/mosaic is closed to a caller with NO principal, and its
    answers match /api/visibility -- the route it is a computational alias for.

    Peak altitude for a given declination IS the observing latitude: sweep dec,
    read off where transit_alt maximises, and you have the site. That is why this
    route cannot be an unauthenticated one. It WAS, under a route-capability
    exemption reading "pure stateless compute ... read-equivalent" -- a rationale
    written about mutation, which silently stopped covering the response body the
    day transit_alt was added to it. The 2026-08-01 cross-cut review recovered the
    latitude to a tenth of a degree through this route with no session at all.

    Asserted against the SAME fail-closed provider that closes /api/visibility, so
    the two read surfaces cannot drift apart again.
    """
    from astrodeck.auth import CAP_VIEW_STATUS
    _store, app = _make_client(tmp_path, monkeypatch)
    body = {"ra_hours": 5.0, "dec_deg": 10.0, "rows": 2, "cols": 2,
            "fov_x_deg": 1.0, "fov_y_deg": 1.0, "overlap": 0.1,
            "transit_alt": True}

    # The body must be VALID, or this proves nothing. Route dependencies run
    # BEFORE body validation, so a misspelled field still yields 401 with the
    # gate and 422 without it -- a test that passes both ways for the wrong
    # reason. Establish first that this exact body really does produce
    # altitudes for a caller who IS entitled to them.
    _install(_principal_with(CAP_VIEW_STATUS))
    with TestClient(app) as c:
        ok = c.post("/api/framing/mosaic", json=body)
    assert ok.status_code == 200, ok.text
    alts = [p.get("transit_alt") for p in ok.json()["panels"]]
    assert any(a is not None for a in alts), (
        f"precondition: this body must yield site-derived altitudes, got {alts}")

    _install(None)  # fail-closed: no principal at all
    with TestClient(app) as c:
        assert c.get("/api/visibility").status_code == 401, (
            "precondition: the sibling read surface is closed")
        r = c.post("/api/framing/mosaic", json=body)
    assert r.status_code == 401, (
        "site-derived transit altitudes were served to a caller with no "
        f"principal (got {r.status_code}); the observing latitude is "
        "recoverable from these numbers")


def test_no_precise_coords_in_logs(tmp_path, monkeypatch, bus_lines):
    """After a /api/site save, a mount-gps read, and a full locations save/
    update/delete cycle against the seeded precise site, the NEW log entries
    contain no precise coordinate strings (spec §8; /api/logs is viewer-visible)."""
    import json as _json
    from astrodeck.auth import (CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                                CAP_CONFIG_SAFETY)
    store, app = _make_client(tmp_path, monkeypatch)
    _wire_locations(tmp_path, monkeypatch, app)
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                             CAP_CONFIG_SAFETY))
    lat_s, lon_s = "40.123456", "-74.654321"
    with TestClient(app) as c:
        c.put("/api/site", json={"site": {
            "name": "Secret Barn", "latitude": 40.123456,
            "longitude": -74.654321, "elevation_m": 123.4}})
        c.get("/api/site/mount-gps")
        r = c.post("/api/locations", json={
            "name": "Barn", "latitude": 40.123456, "longitude": -74.654321,
            "elevation_m": 123.4})
        lid = r.json()["id"]
        c.put(f"/api/locations/{lid}", json={
            "name": "Barn2", "latitude": 40.123456, "longitude": -74.654321,
            "elevation_m": 123.4})
        c.delete(f"/api/locations/{lid}")
    # bus_lines, NOT ``bus.log_history[before:]``. This assertion is the reason
    # the slice idiom had to go: _history is a deque(maxlen=200) shared by the
    # whole process, so once the ring is at cap ``before`` pins at 200 and the
    # slice is EMPTY FOREVER. Elsewhere that idiom made tests flake red; HERE it
    # failed OPEN -- blob became "[]" and both assertions passed while the
    # coordinates really were in the log. A green vacuous privacy test is worse
    # than no test: it certifies the site is not leaking no matter what starts
    # leaking it. Capturing the call intercepts each line at its source, so
    # nothing that ran earlier in the process can hide one.
    blob = _json.dumps(bus_lines)
    assert lat_s not in blob, "precise latitude leaked into bus.log"
    assert lon_s not in blob, "precise longitude leaked into bus.log"


# ===================================================== weather (spec §7/§8/§14;
# gate split 2026-07-17 decisions wave I2)
# All four weather routes are @declare-gated (the boot assertion covers them at
# create_app time); the GET + tile routes and the WS `weather` event drop rule
# are gated on view.weather (operator + admin -- NOT view.site_precise, split
# off in I2 so operators see weather without the precise site fix); ignore-
# tonight stays control.capture and config.weather stays config.site_optics,
# both unchanged. WS `weather` events are DROPPED entirely (not stripped) for
# non-holders of view.weather on the LAN lane; the astrospheric key is
# absent from redacted config, config echoes, and 409 bodies (T-RBAC-13b
# family). The relay-lane drop test lives in tests/test_remote_relay.py.


def _make_weather_client(tmp_path, monkeypatch):
    """_make_client + a FRESH WeatherService bound to the temp store (the
    module singleton would otherwise leak ignore/alert state across tests and
    read the global config store). Routes and the lifespan look the singleton
    up as an app-module global at call time, so monkeypatching it works."""
    store, app = _make_client(tmp_path, monkeypatch)
    import astrodeck.weather as weather_mod
    monkeypatch.setattr(weather_mod, "config_store", store)
    monkeypatch.setattr(app_module, "weather_service",
                        weather_mod.WeatherService())
    return store, app


def test_weather_routes_gated_for_viewer(tmp_path, monkeypatch):
    """Viewer holds view.status only: 403 on the weather GET (view.weather),
    ignore-tonight (control.capture), and config (config.site_optics)."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.get("/api/weather").status_code == 403
        r = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r.status_code == 403
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 403


def test_operator_ignore_tonight_and_weather_get_allowed_config_denied(
        tmp_path, monkeypatch):
    """Operator holds control.capture (ignore-tonight passes the gate; a
    default site then yields the 409 no_night contract) AND view.weather
    (2026-07-17 decisions wave I2: GET /api/weather now 200 for operator) but
    NOT config.site_optics (config write stays 403)."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("operator"))
    with TestClient(app) as c:
        r = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r.status_code == 409                 # gate passed; no night (default site)
        assert r.json()["detail"]["code"] == "no_night"
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 403
        r2 = c.get("/api/weather")
        assert r2.status_code == 200
        assert r2.json()["site_lat"] is None         # default site -> null, not 403


def test_admin_weather_get_and_ignore_roundtrip(tmp_path, monkeypatch):
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _seed_precise_site(store)                       # real site -> night resolves
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.get("/api/weather")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"enabled", "fetched_ts", "stale", "ignore_tonight",
                             "threshold_pct", "sustain_minutes", "site_lat",
                             "site_lon", "forecast", "astrospheric", "alert"}
        assert body["enabled"] is False and body["forecast"] is None
        # site_lat/site_lon ride this payload for a view.weather holder (I2)
        assert body["site_lat"] == _PRECISE_LAT and body["site_lon"] == _PRECISE_LON
        r2 = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r2.status_code == 200
        assert r2.json()["ignore_tonight"] is True
        assert c.get("/api/weather").json()["ignore_tonight"] is True


def test_operator_weather_get_matches_admin_shape(tmp_path, monkeypatch):
    """An operator's GET /api/weather is the SAME payload an admin gets
    (view.weather, not view.site_precise, is the gate) -- including
    site_lat/site_lon, the deliberate I2 exception."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _seed_precise_site(store)
    _install(principal_for_role("operator"))
    with TestClient(app) as c:
        r = c.get("/api/weather")
        assert r.status_code == 200
        body = r.json()
        assert body["site_lat"] == _PRECISE_LAT and body["site_lon"] == _PRECISE_LON


def test_ignore_tonight_requires_view_weather_not_just_capture(
        tmp_path, monkeypatch):
    """Defense-in-depth (I2 review): POST /api/weather/ignore-tonight echoes
    the full weather payload, which carries site_lat/site_lon since I2, so
    the route requires view.weather IN ADDITION to control.capture. For the
    three fixed roles this is unobservable (every control.capture holder also
    holds view.weather), but that implication is not a stated invariant --
    a custom/split-role principal holding control.capture WITHOUT
    view.weather must be denied outright, never reading coordinates off a
    write route's echo."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _seed_precise_site(store)                       # real coords at stake
    capture_only = Principal(
        role="operator", email=None,
        caps=frozenset({"view.status", "view.preview", "control.capture"}),
        jti=None)
    _install(capture_only)
    with TestClient(app) as c:
        r = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r.status_code == 403
        assert "site_lat" not in r.text             # no payload echo on deny


def test_ws_weather_event_dropped_for_viewer_kept_for_admin(tmp_path, monkeypatch):
    """LAN lane (spec §8): type=='weather' + non-holder -> the frame is NEVER
    sent (dropped, not stripped). A later marker event proves ordering."""
    from astrodeck.events import bus
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            bus.publish("weather", enabled=True, stale=False)
            bus.publish("safety", is_safe=True)     # ordered marker
            # drain until the marker: bus ordering guarantees the weather frame
            # (if it leaked) would arrive BEFORE the safety marker; unrelated
            # background events may interleave and are ignored.
            seen = []
            while True:
                ev = ws.receive_json()
                seen.append(ev["type"])
                if ev["type"] == "safety":
                    break
            assert "weather" not in seen, f"weather frame leaked: {seen}"
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()                        # hello
            bus.publish("weather", enabled=True, stale=False)
            while True:                              # holder receives verbatim
                ev = ws.receive_json()
                if ev["type"] == "weather":
                    break
            assert ev["data"]["enabled"] is True


def test_ws_weather_event_delivered_to_operator(tmp_path, monkeypatch):
    """LAN lane (spec §8; 2026-07-17 decisions wave I2): an operator holds
    view.weather (NOT view.site_precise) and still receives the weather event
    verbatim -- the drop rule tracks view.weather, independently of the
    site-precision cap that gates everything else."""
    from astrodeck.events import bus
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("operator"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()                        # hello
            bus.publish("weather", enabled=True, stale=False)
            while True:                              # holder receives verbatim
                ev = ws.receive_json()
                if ev["type"] == "weather":
                    break
            assert ev["data"]["enabled"] is True


def test_ws_downgrade_midstream_operator_to_viewer_drops_weather(
        tmp_path, monkeypatch):
    """LAN lane mid-stream re-tighten (I2 review fix round): an operator
    whose role is downgraded to viewer mid-socket loses view.weather at the
    next WS_AUTH_RECHECK_S re-auth, and weather events published AFTER that
    are DROPPED entirely -- the LAN-lane twin of
    test_remote_relay.test_tunneled_ws_downgrade_midstream_operator_to_viewer
    _drops_weather. The published payloads carry site_lat/site_lon (the real
    I2 payload shape), so the coordinate-bearing frame literally exercises
    the drop path -- the post-downgrade viewer never sees the coordinates."""
    import time
    from astrodeck.events import bus
    monkeypatch.setattr(app_module, "WS_AUTH_RECHECK_S", 0.05)
    store, app = _make_weather_client(tmp_path, monkeypatch)
    prov = _SwitchProvider(principal_for_role("operator"))
    set_active_provider(prov)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            # weather BEFORE downgrade -> delivered verbatim, coords included
            bus.publish("weather", enabled=True, stale=False,
                        site_lat=_PRECISE_LAT, site_lon=_PRECISE_LON)
            while True:
                ev = ws.receive_json()
                if ev["type"] == "weather":
                    break
            assert ev["data"]["site_lat"] == _PRECISE_LAT     # operator sees coords
            # downgrade: operator -> viewer (keeps view.status, loses
            # view.weather); let >=1 recheck refresh the cached principal
            prov.principal = principal_for_role("viewer")
            time.sleep(0.2)
            # weather AFTER downgrade -> dropped entirely; the LATER safety
            # marker proves the loop is alive (bus ordering would put a
            # leaked weather frame before the marker).
            bus.publish("weather", enabled=True, stale=True,
                        site_lat=_PRECISE_LAT, site_lon=_PRECISE_LON)
            bus.publish("safety", is_safe=True)               # ordered marker
            seen = []
            while True:
                ev = ws.receive_json()
                seen.append(ev["type"])
                if ev["type"] == "safety":
                    break
            assert "weather" not in seen, f"post-downgrade weather leaked: {seen}"


def test_astrospheric_key_scrubbed_everywhere(tmp_path, monkeypatch):
    """T-RBAC-13b family (spec §8): key absent from the config-route echo, the
    generic GET /api/config echo, AND the 409 conflict body."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 60,
                        "sustain_minutes": 45,
                        "astrospheric_api_key": "SECRET-KEY-XYZ"},
            "version": None})
        assert r.status_code == 200
        assert "SECRET-KEY-XYZ" not in r.text
        w = r.json()["weather"]
        assert w["astrospheric_api_key"] is None
        assert w["astrospheric_configured"] is True
        assert store.cfg().weather.astrospheric_api_key == "SECRET-KEY-XYZ"
        assert "SECRET-KEY-XYZ" not in c.get("/api/config").text
        r2 = c.post("/api/config/weather", json={
            "weather": {"enabled": False, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": 1})                           # stale token -> 409
        assert r2.status_code == 409
        assert "SECRET-KEY-XYZ" not in r2.text
