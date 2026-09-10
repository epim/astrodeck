"""S1 -- the per-site horizon POLYLINE: drawn on a saved location, carried into
``config.safety.horizon`` when that location becomes the site.

The bug this exists to prevent is the project's dominant one: a line the user
traces, stored, echoed back, and read by nothing. ``SafetyConfig.horizon`` is
the ONLY horizon the engine's obstruction rule interpolates
(``sequence/engine.py`` -> ``schedule.effective_floor`` -> ``interp_wrap``), so
every test below that matters asserts the points reached THAT field -- not that
the API round-tripped them.

Repo convention (mirrors tests/test_rbac_enforcement.py): in-process fakes via
monkeypatch + TestClient, NO unittest.mock, an isolated ConfigStore and
LocationStore per test.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_CONFIG_SAFETY, CAP_CONFIG_SITE_OPTICS,
                            CAP_VIEW_STATUS, Principal, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.config import ConfigStore
from astrodeck.locations import (LocationStore, MAX_HORIZON_POINTS,
                                 SavedLocation, normalize_horizon_points)


# --------------------------------------------------------------------- harness

def _make_client(tmp_path, monkeypatch):
    """Isolated app + ConfigStore + LocationStore (tests/test_rbac_enforcement)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.locations as loc_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    locs = LocationStore(path=tmp_path / "locations.json")
    monkeypatch.setattr(loc_mod, "location_store", locs)
    monkeypatch.setattr(app_module, "location_store", locs)
    reset_active_provider()
    return temp_store, locs, app_module.create_app()


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
def _reset_provider_after():
    yield
    reset_active_provider()


_BODY = {"name": "Backyard", "latitude": 40.5, "longitude": -74.25,
         "elevation_m": 12.0}
_POINTS = [[90.0, 8.0], [0.0, 22.0], [180.0, 5.5]]
_SORTED = [[0.0, 22.0], [90.0, 8.0], [180.0, 5.5]]


# ============================================================ the store's rule

def test_points_are_sorted_and_deduped_by_azimuth():
    """The engine interpolates BETWEEN control points, so the stored order is
    load-bearing, and two altitudes at one azimuth are not a horizon. A later
    point at the same azimuth wins; 360 folds onto 0 (interp_wrap's own seam)."""
    out = normalize_horizon_points([[180.0, 5.0], [10.0, 30.0], [10.0, 12.0],
                                    [360.0, 4.0]])
    assert out == [[0.0, 4.0], [10.0, 12.0], [180.0, 5.0]]


@pytest.mark.parametrize("bad, why", [
    ([[400.0, 10.0]], "azimuth above 360"),
    ([[-1.0, 10.0]], "azimuth below 0"),
    ([[10.0, 91.0]], "altitude above the zenith"),
    ([[10.0, -11.0]], "altitude below the floor"),
    ([[10.0]], "not a pair"),
    ([[10.0, 5.0, 1.0]], "three values"),
    (["10,5"], "a string is not a point"),
    ([[float("nan"), 5.0]], "non-finite azimuth"),
    ([[10.0, float("inf")]], "non-finite altitude"),
    ([[float(i), 5.0] for i in range(MAX_HORIZON_POINTS + 1)], "over the cap"),
])
def test_invalid_points_raise_valueerror(bad, why):
    with pytest.raises(ValueError):
        normalize_horizon_points(bad)


def test_none_and_empty_are_different_things():
    """None = "this location has no drawn horizon" (apply leaves the configured
    profile alone); [] = "this site has no obstructions" (apply CLEARS it).
    Collapsing the two would leave an erased polyline uneraseable."""
    assert normalize_horizon_points(None) is None
    assert normalize_horizon_points([]) == []


def test_old_json_without_the_field_still_loads(tmp_path):
    """A locations.json written before horizon_points exists must load, with the
    field absent -> None. This is the whole back-compat claim, asserted against
    a hand-written file rather than a model default."""
    p = tmp_path / "locations.json"
    p.write_text(json.dumps({"locations": [{
        "id": "abc", "name": "Old", "latitude": 1.0, "longitude": 2.0,
        "elevation_m": 0.0, "horizon_min_deg": 15.0,
        "created_ts": 1.0, "updated_ts": 1.0}]}), encoding="utf-8")
    rows = LocationStore(path=p).list()
    assert len(rows) == 1
    assert rows[0].name == "Old" and rows[0].horizon_points is None


def test_store_roundtrips_the_polyline(tmp_path):
    s = LocationStore(path=tmp_path / "locations.json")
    loc = s.create("Yard", 1.0, 2.0, 0.0, 15.0, _POINTS)
    assert loc.horizon_points == _SORTED
    assert LocationStore(path=tmp_path / "locations.json").list()[0] \
        .horizon_points == _SORTED


def test_model_rejects_a_bad_point_on_construction():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SavedLocation(name="X", latitude=1.0, longitude=2.0, elevation_m=0.0,
                      horizon_points=[[400.0, 10.0]])


# ============================================================ the routes

def test_create_and_read_back_the_polyline(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS})
        assert r.status_code == 200, r.text
        assert r.json()["horizon_points"] == _SORTED
        assert c.get("/api/locations").json()[0]["horizon_points"] == _SORTED


def test_create_without_points_leaves_them_null(tmp_path, monkeypatch):
    """The field is additive: a body that omits it behaves exactly as before."""
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.post("/api/locations", json=_BODY)
        assert r.status_code == 200
        assert r.json()["horizon_points"] is None


def test_update_replaces_the_polyline(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY))
    with TestClient(app) as c:
        lid = c.post("/api/locations", json=_BODY).json()["id"]
        r = c.put(f"/api/locations/{lid}",
                  json={**_BODY, "horizon_points": [[270.0, 30.0]]})
        assert r.status_code == 200
        assert r.json()["horizon_points"] == [[270.0, 30.0]]


@pytest.mark.parametrize("route, payload", [
    pytest.param("post", {**_BODY, "horizon_points": [[400.0, 10.0]]},
                 id="az-400-on-create"),
    pytest.param("put", {**_BODY, "horizon_points": [[10.0, 95.0]]},
                 id="alt-95-on-update"),
])
def test_out_of_range_points_422_at_the_boundary(tmp_path, monkeypatch,
                                                 route, payload):
    """Validated in LocationBody, so it is a 422 -- never a 500 out of the
    store's post-model_copy re-validate, which is what an unguarded boundary
    would have produced."""
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        if route == "post":
            r = c.post("/api/locations", json=payload)
        else:
            lid = c.post("/api/locations", json=_BODY).json()["id"]
            r = c.put(f"/api/locations/{lid}", json=payload)
        assert r.status_code == 422, r.text


# ============================================================ apply -> safety

def test_apply_copies_points_into_safety_horizon(tmp_path, monkeypatch):
    """THE assertion this feature exists for: after apply, the drawn line is in
    ``config.safety.horizon`` -- the field the engine's slew guard reads -- as
    (az, alt) pairs, and the site is the location's."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_min_deg": 20.0,
                           "horizon_points": _POINTS}).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
    cfg = cfg_store.cfg()
    assert [list(p) for p in cfg.safety.horizon] == _SORTED
    assert cfg.site.name == "Backyard"
    assert cfg.site.latitude == 40.5 and cfg.site.longitude == -74.25
    assert cfg.site.horizon_min_deg == 20.0
    assert cfg.site.is_default is False


def test_apply_without_points_leaves_a_configured_horizon_alone(
        tmp_path, monkeypatch):
    """A location predating the field must not silently erase a horizon someone
    configured through POST /api/config."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    cfg_store.set_safety(cfg_store.cfg().safety.model_copy(
        update={"horizon": [(0.0, 12.0)]}))
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations", json=_BODY).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
    assert [list(p) for p in cfg_store.cfg().safety.horizon] == [[0.0, 12.0]]


def test_apply_with_an_empty_list_clears_the_horizon(tmp_path, monkeypatch):
    """[] is the explicit "no obstructions here" -- the only way to erase a line
    that belongs to a different site."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    cfg_store.set_safety(cfg_store.cfg().safety.model_copy(
        update={"horizon": [(0.0, 12.0)]}))
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_points": []}).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
    assert cfg_store.cfg().safety.horizon == []


def test_apply_unknown_id_404s(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        assert c.post("/api/locations/nope/apply").status_code == 404


def test_apply_rbac(tmp_path, monkeypatch):
    """config.site_optics gates the route (viewer and operator hold neither);
    a location carrying a horizon ALSO needs config.safety, refused BEFORE any
    write -- the same field-level rule PUT /api/site keeps."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_points": _POINTS}).json()["id"]
    before_version = cfg_store.cfg().version
    for role in ("viewer", "operator"):
        _install(principal_for_role(role))
        with TestClient(app) as c:
            assert c.post(f"/api/locations/{lid}/apply").status_code == 403
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))       # no config.safety
    with TestClient(app) as c:
        assert c.post(f"/api/locations/{lid}/apply").status_code == 403
    assert cfg_store.cfg().version == before_version   # nothing was written
    assert cfg_store.cfg().safety.horizon is None


# ============================================ editing the ACTIVE site's horizon

def test_editing_the_active_sites_location_updates_safety_horizon(
        tmp_path, monkeypatch):
    """Re-drawing the horizon of the site the rig is standing at takes effect
    without re-applying. Without this, the engine keeps interpolating the OLD
    line and the redrawn one gates nothing."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_points": _POINTS}).json()["id"]
        c.post(f"/api/locations/{lid}/apply")
        assert [list(p) for p in cfg_store.cfg().safety.horizon] == _SORTED
        r = c.put(f"/api/locations/{lid}",
                  json={**_BODY, "horizon_points": [[45.0, 33.0]]})
        assert r.status_code == 200
    assert [list(p) for p in cfg_store.cfg().safety.horizon] == [[45.0, 33.0]]


def test_editing_a_DIFFERENT_location_does_not_touch_safety_horizon(
        tmp_path, monkeypatch):
    """The write-through is scoped to the ACTIVE site. A second saved location
    is a place the rig is not standing; its horizon must stay in the library."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_points": _POINTS}).json()["id"]
        c.post(f"/api/locations/{lid}/apply")
        other = {"name": "Dark site", "latitude": 36.0, "longitude": -117.0,
                 "elevation_m": 1200.0}
        oid = c.post("/api/locations", json=other).json()["id"]
        assert c.put(f"/api/locations/{oid}",
                     json={**other, "horizon_points": [[45.0, 33.0]]}
                     ).status_code == 200
    assert [list(p) for p in cfg_store.cfg().safety.horizon] == _SORTED


def test_active_site_write_through_needs_config_safety(tmp_path, monkeypatch):
    """A site_optics-only principal may keep the library, but not move a safety
    floor. Refused before the store write: the library is unchanged too."""
    cfg_store, locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_points": _POINTS}).json()["id"]
        c.post(f"/api/locations/{lid}/apply")
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    with TestClient(app) as c:
        r = c.put(f"/api/locations/{lid}",
                  json={**_BODY, "horizon_points": [[45.0, 33.0]]})
        assert r.status_code == 403
    assert [list(p) for p in cfg_store.cfg().safety.horizon] == _SORTED
    assert locs.reload()[0].horizon_points == _SORTED


# ============================================================ GET /api/site

def test_get_site_echoes_the_active_horizon_points(tmp_path, monkeypatch):
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        assert c.get("/api/site").json()["site"]["horizon_points"] is None
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_points": _POINTS}).json()["id"]
        c.post(f"/api/locations/{lid}/apply")
        body = c.get("/api/site").json()
        assert body["site"]["horizon_points"] == _SORTED
        assert body["site"]["name"] == "Backyard"
        assert body["version"] == cfg_store.cfg().version


def test_get_site_redacts_coordinates_but_keeps_the_horizon(tmp_path, monkeypatch):
    """The redaction decision, asserted: horizon_points is treated like
    horizon_min_deg (retained -- redact.py's _SITE_STRIP_KEYS names neither),
    while the four precise keys are ABSENT for a caller lacking
    view.site_precise. Both halves matter: dropping the polyline would break the
    horizon editor for an operator; leaking a coordinate would be the 2.9 km
    audit again."""
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations",
                     json={**_BODY, "horizon_min_deg": 20.0,
                           "horizon_points": _POINTS}).json()["id"]
        c.post(f"/api/locations/{lid}/apply")
    _install(principal_for_role("operator"))       # no view.site_precise
    with TestClient(app) as c:
        site = c.get("/api/site").json()["site"]
    for k in ("name", "latitude", "longitude", "elevation_m"):
        assert k not in site, f"{k} leaked to a non-holder"
    assert site["horizon_points"] == _SORTED
    assert site["horizon_min_deg"] == 20.0
    assert site["is_default"] is False


def test_get_site_needs_view_status(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_principal_with())                    # a principal with no caps
    with TestClient(app) as c:
        assert c.get("/api/site").status_code == 403
    _install(_principal_with(CAP_VIEW_STATUS))
    with TestClient(app) as c:
        assert c.get("/api/site").status_code == 200


# ============================================================ boot assertion

def test_the_rbac_boot_assertion_still_passes(tmp_path, monkeypatch):
    """create_app() runs assert_route_capabilities LAST; the new routes must not
    trip it (a @declare above what a dependency enforces is a boot failure)."""
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    served = {(m, r.path) for r in app.routes
              for m in (getattr(r, "methods", None) or ())}
    assert ("POST", "/api/locations/{loc_id}/apply") in served
    assert ("GET", "/api/site") in served
