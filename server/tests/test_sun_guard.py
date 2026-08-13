"""W1.10 sun-avoidance exclusion-cone gate (solar-scope aware).

THE GAP this closes: ``hub._check_horizon`` is inert on a default site and only
blocks ``alt < 0``. The Sun sits above the horizon all day, so nothing stopped a
daytime slew at the Sun. ``_check_solar`` adds an RA/Dec-based (site-independent)
exclusion cone, ON by default, disarmed only for a deliberate solar-astronomy
session via the admin ``config.solar_override`` capability.

Repo convention (mirrors test_rbac_enforcement.py / test_app_preflight.py):
in-process fakes via monkeypatch + TestClient, NO unittest.mock. The Sun RA/Dec
is computed live from ``sun_radec()`` so targets are built deterministically just
inside / just outside the cone relative to the real current Sun -- no wall-clock
pinning needed, and the default-site case proves site-independence.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_CONFIG_SAFETY, CAP_CONFIG_SOLAR_OVERRIDE,
                            CAP_VIEW_STATUS, Principal, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.catalog.coords import sun_radec
from astrodeck.config import ConfigStore, Site
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub, _ang_sep_deg


# --------------------------------------------------------------------- harness

def _make_client(tmp_path, monkeypatch):
    """Isolated app + ConfigStore (mirrors the RBAC enforcement harness)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    # Don't let the lifespan re-install a provider over a test-installed one.
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    return temp_store, app_module.create_app()


class _FakeAuthProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _install(principal):
    set_active_provider(_FakeAuthProvider(principal))


def _principal_with(*caps):
    return Principal(role="custom", email=None, caps=frozenset(caps), jti=None)


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


# ------------------------------------------------------------------- geometry
# Build a target a fixed angular distance from the current Sun by offsetting in
# Dec (a Dec offset of X deg IS X deg of true separation when RA is unchanged).

def _sun_now(when: float | None = None):
    # `when` exists so a caller that needs the Sun TWICE gets the same Sun both
    # times. sun_radec falls through to the live clock, and the Sun moves
    # 3.5e-6 deg/s — invisible against the 30 deg cone these fixtures usually
    # compare, but not against the one assertion that checks a separation to
    # 1e-6 deg. Measured failure rate there is ~1 in 6 million, so this is
    # hygiene rather than a bug; it is worth taking because it is one argument
    # and because the same double-read shape with no margin at all is what
    # broke test_polar_meridian_guard in CI.
    return sun_radec(when)


def _target_at_sep(sep_deg: float, when: float | None = None
                   ) -> tuple[float, float]:
    """A (ra_hours, dec_deg) exactly ``sep_deg`` from the Sun at ``when``.
    Offset in Dec, clamped to [-89, 89] and flipped if the Sun is near a pole."""
    sun_ra, sun_dec = _sun_now(when)
    dec = sun_dec + sep_deg
    if dec > 89.0:
        dec = sun_dec - sep_deg
    return sun_ra, dec


# A default ConfigStore => SafetyConfig defaults: avoidance ON, cone 30 deg.
IN_CONE = 10.0      # well inside the 30 deg cone
OUT_CONE = 60.0     # well outside


# ============================================================ unit: _check_solar

def test_default_is_on_and_blocks_in_cone(tmp_path, monkeypatch):
    """Default config => solar_avoidance True, 30 deg cone. An in-cone target is
    rejected; the message names the separation + the override path."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "config_store", store)
    assert store.cfg().safety.solar_avoidance is True
    assert store.cfg().safety.solar_exclusion_deg == 30.0
    h = Hub()
    ra, dec = _target_at_sep(IN_CONE)
    with pytest.raises(DeviceError) as ei:
        h._check_solar(ra, dec)
    assert "Sun" in str(ei.value)
    assert "solar_override" in str(ei.value)


def test_out_of_cone_allowed(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "config_store", store)
    h = Hub()
    ra, dec = _target_at_sep(OUT_CONE)
    # no raise
    h._check_solar(ra, dec)


def test_solar_avoidance_off_allows_in_cone(tmp_path, monkeypatch):
    """A deliberate solar session (avoidance False) makes the gate inert even for
    a target right at the Sun."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"solar_avoidance": False})
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "config_store", store)
    h = Hub()
    sun_ra, sun_dec = _sun_now()
    h._check_solar(sun_ra, sun_dec)  # pointed AT the Sun, allowed


def test_zero_cone_is_inert(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"solar_exclusion_deg": 0.0})
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "config_store", store)
    h = Hub()
    sun_ra, sun_dec = _sun_now()
    h._check_solar(sun_ra, sun_dec)  # cone <= 0 => inert


def test_force_does_not_bypass(tmp_path, monkeypatch):
    """force is signature-parity with _check_horizon ONLY -- it does NOT disarm
    the cone (disarming needs a solar session)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "config_store", store)
    h = Hub()
    ra, dec = _target_at_sep(IN_CONE)
    with pytest.raises(DeviceError):
        h._check_solar(ra, dec, force=True)


def test_site_independent_default_site_still_protected(tmp_path, monkeypatch):
    """A site-less / default-site rig (no lat/lon saved) is STILL protected: the
    Sun RA/Dec is date-based, so the cone does not depend on the site -- unlike
    _check_horizon which is inert on a default site."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "config_store", store)
    assert store.cfg().site.is_default is True  # never configured
    h = Hub()
    # the horizon check is inert on a default site...
    ra, dec = _target_at_sep(IN_CONE)
    h._check_horizon(ra, dec)  # no raise (default site)
    # ...but the solar cone still fires.
    with pytest.raises(DeviceError):
        h._check_solar(ra, dec)


def test_sep_matches_ang_sep_deg(tmp_path):
    """Sanity: the Dec-offset target really is the intended separation from the
    Sun (so the in/out fixtures are geometrically honest)."""
    when = time.time()          # one Sun, read once, used by both calls
    sun_ra, sun_dec = _sun_now(when)
    ra, dec = _target_at_sep(IN_CONE, when)
    sep = _ang_sep_deg(ra, dec, sun_ra, sun_dec)
    assert abs(sep - IN_CONE) < 1e-6


# ============================================================ route: goto

def _sun_code(r):
    """The sun_exclusion code if this response is the sun block, else None.
    Tolerates a string detail (the no-mount 409 uses ``detail=str(e)``)."""
    if r.status_code != 409:
        return None
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        return detail.get("code")
    return None


def test_plain_goto_in_cone_409(tmp_path, monkeypatch):
    """The plain (non-centered) goto path 409s on an in-cone target. A sim rig is
    connected so the route reaches the sun gate (not the no-mount 409)."""
    store, app = _make_client(tmp_path, monkeypatch)
    ra, dec = _target_at_sep(IN_CONE)
    with TestClient(app) as c:
        assert c.post("/api/connect/sim").status_code == 200
        r = c.post("/api/mount/goto",
                   json={"ra_hours": ra, "dec_deg": dec})
        assert r.status_code == 409, r.text
        assert _sun_code(r) == "sun_exclusion"
        c.post("/api/disconnect")


def test_goto_force_does_not_bypass_cone(tmp_path, monkeypatch):
    """force bypasses the horizon check but NOT the sun cone -> still 409."""
    store, app = _make_client(tmp_path, monkeypatch)
    ra, dec = _target_at_sep(IN_CONE)
    with TestClient(app) as c:
        assert c.post("/api/connect/sim").status_code == 200
        r = c.post("/api/mount/goto",
                   json={"ra_hours": ra, "dec_deg": dec, "force": True})
        assert r.status_code == 409, r.text
        assert _sun_code(r) == "sun_exclusion"
        c.post("/api/disconnect")


def test_goto_out_of_cone_not_blocked(tmp_path, monkeypatch):
    """An out-of-cone target is NOT sun-blocked (it spawns 200)."""
    store, app = _make_client(tmp_path, monkeypatch)
    ra, dec = _target_at_sep(OUT_CONE)
    with TestClient(app) as c:
        assert c.post("/api/connect/sim").status_code == 200
        r = c.post("/api/mount/goto",
                   json={"ra_hours": ra, "dec_deg": dec})
        assert _sun_code(r) != "sun_exclusion"
        c.post("/api/disconnect")


def test_goto_solar_session_allows_in_cone(tmp_path, monkeypatch):
    """With avoidance disarmed (solar session) an in-cone goto is NOT sun-blocked
    -- it spawns instead of 409'ing with the sun code."""
    store, app = _make_client(tmp_path, monkeypatch)
    cfg = store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"solar_avoidance": False})
    store.bump_and_save()
    ra, dec = _target_at_sep(IN_CONE)
    with TestClient(app) as c:
        assert c.post("/api/connect/sim").status_code == 200
        r = c.post("/api/mount/goto",
                   json={"ra_hours": ra, "dec_deg": dec})
        assert _sun_code(r) != "sun_exclusion"
        c.post("/api/disconnect")


# ============================================================ route: sequence

def _plan_in_cone():
    ra, dec = _target_at_sep(IN_CONE)
    return {
        "name": "sun-target", "guide": False,
        "targets": [{
            "name": "M-sun", "ra_hours": ra, "dec_deg": dec,
            "steps": [{"exposure_s": 1.0, "count": 1, "frame_type": "Light"}],
        }],
    }


def test_forced_sequence_still_sun_blocked(tmp_path, monkeypatch):
    """A FORCED sequence (force bypasses horizon) is STILL rejected by the sun
    cone before engine.start is reached."""
    store, app = _make_client(tmp_path, monkeypatch)
    started = {"n": 0}
    monkeypatch.setattr(app_module.engine, "start",
                        lambda plan, **kw: started.__setitem__("n", started["n"] + 1))
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())
    plan = _plan_in_cone()
    plan["force"] = True
    with TestClient(app) as c:
        r = c.post("/api/sequence/start", json=plan)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "sun_exclusion"
    assert started["n"] == 0  # never reached engine.start


# ============================================================ config cap gate

@pytest.mark.parametrize("field, value", [
    pytest.param("solar_avoidance", False, id="toggle_solar_avoidance_requires_cap"),
    pytest.param("solar_exclusion_deg", 5.0, id="toggle_solar_exclusion_deg_requires_cap"),
])
def test_toggle_solar_field_requires_override_cap(tmp_path, monkeypatch, field, value):
    """Toggling solar_avoidance via POST /api/config needs BOTH config.safety AND
    config.solar_override. A config.safety-only principal is 403; nothing merged.

    Changing the cone half-angle (solar_exclusion_deg) is equally gated on
    config.solar_override."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SAFETY))
    with TestClient(app) as c:
        before = store.cfg().version
        r = c.post("/api/config",
                   json={"safety": {field: value}})
        assert r.status_code == 403, r.text
        assert store.cfg().version == before               # nothing merged
        assert store.cfg().safety.solar_avoidance is True   # still armed


def test_toggle_solar_avoidance_with_override_cap_succeeds(tmp_path, monkeypatch):
    """With config.safety + config.solar_override the disarm persists."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SAFETY,
                             CAP_CONFIG_SOLAR_OVERRIDE))
    with TestClient(app) as c:
        r = c.post("/api/config",
                   json={"safety": {"solar_avoidance": False}})
        assert r.status_code == 200, r.text
    assert store.cfg().safety.solar_avoidance is False


def test_safety_post_without_solar_fields_does_not_need_override(tmp_path, monkeypatch):
    """A safety write that does NOT touch the solar fields still only needs
    config.safety (the nested rule is solar-field-scoped, not whole-block)."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SAFETY))
    with TestClient(app) as c:
        r = c.post("/api/config", json={"safety": {"min_alt_deg": 5.0}})
        assert r.status_code == 200, r.text


def test_admin_can_toggle_solar_avoidance(tmp_path, monkeypatch):
    """An admin (ALL_CAPS) holds config.solar_override and can disarm."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.post("/api/config",
                   json={"safety": {"solar_avoidance": False}})
        assert r.status_code == 200, r.text
    assert store.cfg().safety.solar_avoidance is False


def test_operator_cannot_toggle_solar_avoidance(tmp_path, monkeypatch):
    """An operator never holds config.solar_override (nor config.safety) -> 403,
    cone stays armed."""
    store, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("operator"))
    with TestClient(app) as c:
        r = c.post("/api/config",
                   json={"safety": {"solar_avoidance": False}})
        assert r.status_code == 403
    assert store.cfg().safety.solar_avoidance is True


# ============================================================ legacy config load

def test_legacy_config_without_solar_keys_defaults_on(tmp_path, monkeypatch):
    """A config file written before W1.10 (no solar_* keys) deserializes with the
    protective defaults -- avoidance ON, 30 deg cone -- so existing rigs are
    auto-protected without a migration."""
    import json
    p = tmp_path / "astrodeck.json"
    # a minimal legacy config: a safety block with NONE of the solar keys
    legacy = {
        "version": 7,
        "safety": {"enabled": True, "preset": "backyard", "min_alt_deg": 0.0},
    }
    p.write_text(json.dumps(legacy), encoding="utf-8")
    store = ConfigStore(path=p)
    assert store.cfg().safety.solar_avoidance is True
    assert store.cfg().safety.solar_exclusion_deg == 30.0
