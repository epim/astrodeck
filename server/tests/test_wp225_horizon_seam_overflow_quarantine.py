# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1029, #1033, #1034: three faults in the saved-location and horizon doors.

#1029  A horizon point at azimuth 360 is the point at 0, and the rule used to
       fold it onto 0 with "a later point wins". A 360 sorts last, so a point
       drawn at 360 silently replaced a drawn point at 0. Two points at the seam
       with DIFFERENT altitudes are now refused at every door (and counted at the
       load doors); with the same altitude they are one point.
#1033  ``normalize_horizon_points`` raised OverflowError on an integer too large
       for a float. The rule is fixed (WP-213); these tests pin the DOORS it
       reaches: the config load, ``POST /api/config``, and both location routes.
#1034  A row skipped at load survived only in the ``.bak`` and only until the
       second write. It is now kept verbatim in a ``.rejected`` sidecar that a
       save never rewrites.

Every case is graded on BEHAVIOUR (what is stored, what a route answers, what
is on disk, what is said). Coordinates and names are invented, and no warning
may echo one.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import CONFIG_SCHEMA, ConfigStore
from astrodeck.locations import (LocationStore, normalize_horizon_points,
                                 sanitize_horizon_points)

HUGE = 10 ** 400
_SITE = dict(id="s1", name="Alpha Field", latitude=11.1111, longitude=22.2222)
_NEEDLES = ("11.1111", "22.2222", "Alpha Field", "Zulu Hollow", "999.125",
            "88.8888")


def _row(site, **extra):
    return {**site, "elevation_m": 5.0, "created_ts": 1.0, "updated_ts": 1.0,
            **extra}


def _said(bus_lines):
    return [m for (lvl, m, src) in bus_lines
            if src == "config" and lvl == "warning"]


def _no_value_said(lines):
    for line in lines:
        for needle in _NEEDLES:
            assert needle not in line, "a warning echoed a stored value"


class _FakeProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The real app over an isolated ConfigStore and LocationStore."""
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.locations as loc_mod
    cfg = ConfigStore(path=tmp_path / "astrodeck.json")
    locs = LocationStore(path=tmp_path / "locations.json")
    monkeypatch.setattr(config_mod, "config_store", cfg)
    monkeypatch.setattr(hub_mod, "config_store", cfg)
    monkeypatch.setattr(app_module, "config_store", cfg)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    monkeypatch.setattr(loc_mod, "location_store", locs)
    monkeypatch.setattr(app_module, "location_store", locs)
    reset_active_provider()
    set_active_provider(_FakeProvider(principal_for_role("admin")))
    with TestClient(app_module.create_app()) as c:
        c.locs = locs
        c.cfg = cfg
        yield c


def _loc_body(points):
    return {"name": "Beta Ridge", "latitude": 33.3333, "longitude": 44.4444,
            "elevation_m": 10.0, "horizon_points": points}


# ================================================== #1029: the 0 / 360 seam

@pytest.mark.parametrize("points", [
    pytest.param([[0.0, 10.0], [360.0, 30.0]], id="360-sorts-last"),
    pytest.param([[360.0, 30.0], [0.0, 10.0]], id="360-sent-first"),
    pytest.param([[0.0, 10.0], [180.0, 20.0], [360.0, 30.0]], id="with-middle"),
])
def test_a_point_at_360_that_differs_from_the_point_at_0_is_refused(points):
    """The silent overwrite: 360 used to replace 0 whichever the caller meant."""
    with pytest.raises(ValueError, match="azimuth 0 and azimuth 360"):
        normalize_horizon_points(points)


def test_a_point_at_360_with_the_same_altitude_as_0_is_one_point():
    """Closing the loop on purpose is not a conflict."""
    assert normalize_horizon_points([[0.0, 10.0], [360.0, 10.0]]) == [[0.0, 10.0]]
    assert normalize_horizon_points([[360.0, 10.0], [0.0, 10.0]]) == [[0.0, 10.0]]


def test_a_lone_point_at_360_is_still_the_point_at_0():
    assert normalize_horizon_points([[360.0, 30.0], [90.0, 5.0]]) == \
        [[0.0, 30.0], [90.0, 5.0]]


def test_the_location_routes_refuse_a_360_that_would_replace_the_0(client):
    """Both API doors 422 and the stored point at 0 is untouched."""
    made = client.post("/api/locations", json=_loc_body([[0.0, 10.0]]))
    assert made.status_code == 200, made.text[:200]
    loc_id = made.json()["id"]
    clash = [[0.0, 10.0], [360.0, 30.0]]

    r = client.put(f"/api/locations/{loc_id}", json=_loc_body(clash))
    assert r.status_code == 422, r.text[:200]
    assert [x.horizon_points for x in client.locs.list()] == [[[0.0, 10.0]]]

    r = client.post("/api/locations", json={**_loc_body(clash),
                                            "name": "Gamma Bay"})
    assert r.status_code == 422, r.text[:200]
    assert len(client.locs.list()) == 1


def test_the_config_route_refuses_a_360_that_would_replace_the_0(client):
    r = client.post("/api/config",
                    json={"safety": {"horizon": [[0.0, 10.0], [360.0, 30.0]]}})
    assert r.status_code == 422, r.text[:200]
    assert r.json()["detail"]["code"] == "invalid_horizon"


def test_the_load_doors_count_the_seam_clash_and_keep_the_point_at_0(
        tmp_path, bus_lines):
    """A stored horizon with the clash loads (refusing would stop the server),
    keeps the point written at 0, and says so once with a count."""
    pts, problems = sanitize_horizon_points(
        [[0.0, 10.0], [180.0, 20.0], [360.0, 30.0]])
    assert pts == [[0.0, 10.0], [180.0, 20.0]]
    assert list(problems.values()) == [1] and "azimuth 360" in list(problems)[0]
    pts, problems = sanitize_horizon_points([[360.0, 30.0], [0.0, 10.0]])
    assert pts == [[0.0, 10.0]] and len(problems) == 1

    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA, "version": 7,
        "safety": {"min_alt_deg": 20.0,
                   "horizon": [[0.0, 10.0], [360.0, 30.0]]}}), encoding="utf-8")
    cfg = ConfigStore(path=path).cfg()
    assert [list(p) for p in cfg.safety.horizon] == [[0.0, 10.0]]
    said = [m for m in _said(bus_lines) if "repaired on load" in m]
    assert len(said) == 1 and "azimuth 360" in said[0]


def test_a_stored_location_with_the_clash_loads_with_its_point_at_0(
        tmp_path, bus_lines):
    path = tmp_path / "locations.json"
    path.write_text(json.dumps({"locations": [
        _row(_SITE, horizon_points=[[0.0, 10.0], [360.0, 30.0]])]}),
        encoding="utf-8")
    store = LocationStore(path=path)
    assert [r.horizon_points for r in store.list()] == [[[0.0, 10.0]]]
    said = _said(bus_lines)
    assert len(said) == 1 and "repaired on load" in said[0]
    _no_value_said(said)


# ============================== #1033: an oversize integer at every door

def test_the_rule_and_the_load_repair_take_an_oversize_integer_as_valueerror():
    with pytest.raises(ValueError, match="must be numbers"):
        normalize_horizon_points([[HUGE, 5.0]])
    with pytest.raises(ValueError, match="must be numbers"):
        normalize_horizon_points([[10.0, -HUGE]])
    pts, problems = sanitize_horizon_points([[HUGE, 5.0], [90.0, 20.0]])
    assert pts == [[90.0, 20.0]]
    assert problems == {"horizon point values must be numbers": 1}


def test_the_config_load_repairs_an_oversize_integer_instead_of_failing(
        tmp_path, bus_lines):
    """The reported door: ``ConfigStore._load`` caught the escaping error as
    "configuration is invalid" (restore from .bak, else do not start)."""
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA, "version": 7,
        "safety": {"min_alt_deg": 20.0,
                   "horizon": [[HUGE, 5.0], [180.0, 40.0]]}}), encoding="utf-8")

    cfg = ConfigStore(path=path).cfg()

    assert [list(p) for p in cfg.safety.horizon] == [[180.0, 40.0]]
    said = [m for m in _said(bus_lines) if "repaired on load" in m]
    assert len(said) == 1 and "must be numbers" in said[0]
    assert not any("restored from backup" in m for m in _said(bus_lines))


@pytest.mark.parametrize("points", [
    pytest.param([[HUGE, 5.0]], id="huge-azimuth"),
    pytest.param([[10.0, HUGE]], id="huge-altitude"),
    pytest.param([[10.0, -HUGE]], id="huge-negative-altitude"),
])
def test_the_api_doors_answer_422_not_500_for_an_oversize_integer(
        client, points):
    made = client.post("/api/locations", json=_loc_body([[0.0, 10.0]]))
    assert made.status_code == 200, made.text[:200]
    loc_id = made.json()["id"]

    assert client.post("/api/locations", json={
        **_loc_body(points), "name": "Gamma Bay"}).status_code == 422
    assert client.put(f"/api/locations/{loc_id}",
                      json=_loc_body(points)).status_code == 422
    assert client.post("/api/config",
                       json={"safety": {"horizon": points}}).status_code == 422
    assert [x.horizon_points for x in client.locs.list()] == [[[0.0, 10.0]]]


# ====================== #1034: a skipped row is kept across later writes

def _unusable():
    return _row(dict(id="x1", name="Zulu Hollow", latitude=999.125,
                     longitude=88.8888))


def _write_locations(path, rows):
    path.write_text(json.dumps({"locations": rows}), encoding="utf-8")


def _rejected(path):
    return json.loads(path.with_suffix(".json.rejected").read_text(
        encoding="utf-8"))["rejected"]


def test_a_skipped_row_outlives_two_writes(tmp_path, bus_lines):
    """The reported loss: the first write put the row in the .bak, the second
    overwrote the .bak. The row is still recoverable after both."""
    path = tmp_path / "locations.json"
    bad = _unusable()
    _write_locations(path, [_row(_SITE), bad])
    store = LocationStore(path=path)
    assert [r.id for r in store.list()] == ["s1"]

    store.create("Delta Bay", 5.5, 6.5, 1.0)
    store.create("Echo Cove", 6.5, 7.5, 1.0)

    assert bad not in json.loads(path.read_text(encoding="utf-8"))["locations"]
    bak = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
    assert bad not in bak["locations"], "the .bak no longer holds it"
    assert _rejected(path) == [bad], "the sidecar holds it verbatim"


def test_the_load_says_how_many_rows_were_set_aside_and_never_a_value(
        tmp_path, bus_lines):
    path = tmp_path / "locations.json"
    _write_locations(path, [_row(_SITE), _unusable(),
                            {"id": "x2", "name": "Zulu Hollow"}, "not a row"])

    LocationStore(path=path).list()

    said = _said(bus_lines)
    assert len(said) == 1, said
    assert "3 saved location(s)" in said[0]
    assert "kept verbatim in locations.json.rejected" in said[0]
    _no_value_said(said)
    assert len(_rejected(path)) == 3


def test_the_sidecar_accumulates_and_a_reload_does_not_duplicate(tmp_path):
    path = tmp_path / "locations.json"
    first = _unusable()
    _write_locations(path, [_row(_SITE), first])
    store = LocationStore(path=path)
    store.list()
    store.reload()
    store.reload()
    assert _rejected(path) == [first]

    # A different unusable row appears later (a hand edit): both are kept.
    second = _row(dict(id="x3", name="Yankee Pass", latitude=-999.5,
                       longitude=1.0))
    _write_locations(path, [_row(_SITE), second])
    LocationStore(path=path).list()
    assert _rejected(path) == [first, second]


def test_a_library_with_no_skipped_row_writes_no_sidecar(tmp_path):
    path = tmp_path / "locations.json"
    _write_locations(path, [_row(_SITE)])
    store = LocationStore(path=path)
    store.create("Delta Bay", 5.5, 6.5, 1.0)
    assert not path.with_suffix(".json.rejected").exists()


def test_a_sidecar_that_cannot_be_written_is_said_and_the_load_goes_on(
        tmp_path, bus_lines):
    """A directory where the sidecar belongs: the load must not fail, and must
    not claim the row was kept."""
    path = tmp_path / "locations.json"
    _write_locations(path, [_row(_SITE), _unusable()])
    path.with_suffix(".json.rejected").mkdir()

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["s1"]
    said = _said(bus_lines)
    assert len(said) == 1 and "NOT kept" in said[0]
    assert "kept verbatim" not in said[0]


def test_a_factory_reset_removes_the_sidecar_with_the_library(tmp_path):
    """The sidecar holds site coordinates, so it goes with locations.json."""
    from astrodeck.factory_reset import factory_reset
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    path = cfg_dir / "locations.json"
    _write_locations(path, [_row(_SITE), _unusable()])
    LocationStore(path=path).list()
    assert path.with_suffix(".json.rejected").exists()

    factory_reset(ConfigStore(path=cfg_dir / "astrodeck.json"), cfg_dir,
                  tmp_path / "captures")

    assert not path.with_suffix(".json.rejected").exists()
    assert not path.exists()
