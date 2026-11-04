# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.6 (#1117): a saved horizon carries what each of its bins is.

A scan answers for azimuth bins, and until now the saved line forgot which of
them anything measured: a bin the scanner could not answer for was saved as a
point at 90 and was indistinguishable from a wall. The saved location and the
active safety config now carry ``horizon_bins`` (``[{alt, status}]``) beside
``horizon_points``, and the rules that keep it honest are:

* it round-trips, through the store, the routes and a restart;
* a new line sent without bins drops the stored bins, because bins describe
  one particular line;
* a status the rule refuses is a 422 at the door and a dropped note at the load
  door, never a lost row or a lost config;
* applying a location copies its bins to the safety config beside its points.

The made-up site below is nowhere in the world.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_CONFIG_SAFETY, CAP_CONFIG_SITE_OPTICS,
                            Principal, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.config import ConfigStore, SafetyConfig
from astrodeck.locations import (HORIZON_BIN_STATUSES, MAX_HORIZON_BINS,
                                 LocationStore, normalize_horizon_bins)

_BODY = {"name": "Test field", "latitude": 12.5, "longitude": 34.5,
         "elevation_m": 10.0}
_POINTS = [[0.0, 90.0], [6.02, 5.0], [11.98, 5.0], [12.0, 90.0], [23.98, 90.0]]
_BINS = [{"alt": 90.0, "status": "unknown"}, {"alt": 5.0, "status": "measured"}]


def _make_client(tmp_path, monkeypatch):
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


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


# ------------------------------------------------------------------ the rule

def test_a_valid_status_list_is_kept_as_written():
    assert normalize_horizon_bins(_BINS) == _BINS


def test_none_and_empty_both_mean_no_status():
    assert normalize_horizon_bins(None) is None
    assert normalize_horizon_bins([]) is None


@pytest.mark.parametrize("bad", [
    [{"alt": 5.0, "status": "guessed"}],
    [{"alt": 5.0}],
    [{"status": "measured"}],
    [{"alt": float("nan"), "status": "measured"}],
    [{"alt": 120.0, "status": "measured"}],
    [{"alt": "high", "status": "measured"}],
    ["measured"],
    "measured",
    [{"alt": 5.0, "status": "measured"}] * (MAX_HORIZON_BINS + 1),
])
def test_a_status_the_rule_refuses_raises_valueerror(bad):
    with pytest.raises(ValueError):
        normalize_horizon_bins(bad)


def test_every_status_the_scanner_writes_is_accepted():
    bins = [{"alt": 5.0, "status": s} for s in HORIZON_BIN_STATUSES]
    assert [b["status"] for b in normalize_horizon_bins(bins)] == list(HORIZON_BIN_STATUSES)


# ------------------------------------------------------------- the store

def test_the_store_round_trips_bins_through_a_restart(tmp_path):
    path = tmp_path / "locations.json"
    LocationStore(path=path).create("Yard", 1.0, 2.0, 0.0, 15.0, _POINTS, _BINS)
    assert LocationStore(path=path).list()[0].horizon_bins == _BINS


def test_a_row_with_damaged_bins_loads_without_them_and_keeps_its_line(tmp_path):
    path = tmp_path / "locations.json"
    path.write_text(json.dumps({"locations": [{
        "id": "abc", "name": "Old", "latitude": 1.0, "longitude": 2.0,
        "elevation_m": 0.0, "horizon_points": [[0.0, 5.0]],
        "horizon_bins": [{"alt": 5.0, "status": "invented"}],
        "created_ts": 1.0, "updated_ts": 1.0}]}), encoding="utf-8")
    rows = LocationStore(path=path).list()
    assert len(rows) == 1
    assert rows[0].horizon_points == [[0.0, 5.0]]
    assert rows[0].horizon_bins is None


def test_a_new_line_without_bins_drops_the_stored_bins(tmp_path):
    store = LocationStore(path=tmp_path / "locations.json")
    loc = store.create("Yard", 1.0, 2.0, 0.0, 15.0, _POINTS, _BINS)
    drawn = store.update(loc.id, "Yard", 1.0, 2.0, 0.0, horizon_points=[[0.0, 40.0]])
    assert drawn.horizon_points == [[0.0, 40.0]]
    assert drawn.horizon_bins is None


def test_the_same_line_without_bins_keeps_them(tmp_path):
    store = LocationStore(path=tmp_path / "locations.json")
    loc = store.create("Yard", 1.0, 2.0, 0.0, 15.0, _POINTS, _BINS)
    echoed = store.update(loc.id, "Yard", 1.0, 2.0, 0.0, horizon_points=_POINTS)
    assert echoed.horizon_bins == _BINS


def test_a_rename_that_sends_no_horizon_keeps_both(tmp_path):
    store = LocationStore(path=tmp_path / "locations.json")
    loc = store.create("Yard", 1.0, 2.0, 0.0, 15.0, _POINTS, _BINS)
    renamed = store.update(loc.id, "Yard two", 1.0, 2.0, 0.0)
    assert renamed.horizon_points == _POINTS and renamed.horizon_bins == _BINS


# ------------------------------------------------------------- the routes

def test_create_and_read_back_bins(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(Principal(role="custom", email=None, caps=frozenset({CAP_CONFIG_SITE_OPTICS}), jti=None))
    with TestClient(app) as c:
        r = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": _BINS})
        assert r.status_code == 200, r.text
        assert r.json()["horizon_bins"] == _BINS
        assert c.get("/api/locations").json()[0]["horizon_bins"] == _BINS


def test_a_put_with_new_bins_replaces_them(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    other = [{"alt": 5.0, "status": "measured"}, {"alt": 90.0, "status": "unknown"}]
    with TestClient(app) as c:
        lid = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": _BINS}).json()["id"]
        r = c.put(f"/api/locations/{lid}", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": other})
        assert r.status_code == 200, r.text
        assert r.json()["horizon_bins"] == other


def test_a_put_that_redraws_the_line_without_bins_clears_them(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": _BINS}).json()["id"]
        r = c.put(f"/api/locations/{lid}", json={**_BODY, "horizon_points": [[0.0, 30.0]]})
        assert r.status_code == 200, r.text
        assert r.json()["horizon_bins"] is None


def test_a_put_that_only_renames_keeps_the_bins(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": _BINS}).json()["id"]
        r = c.put(f"/api/locations/{lid}", json={**_BODY, "name": "Renamed"})
        assert r.status_code == 200, r.text
        assert r.json()["horizon_bins"] == _BINS


@pytest.mark.parametrize("route", ["post", "put"])
def test_a_refused_status_is_a_422_at_the_door(tmp_path, monkeypatch, route):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    bad = {**_BODY, "horizon_bins": [{"alt": 5.0, "status": "guessed"}]}
    with TestClient(app) as c:
        if route == "post":
            r = c.post("/api/locations", json=bad)
        else:
            lid = c.post("/api/locations", json=_BODY).json()["id"]
            r = c.put(f"/api/locations/{lid}", json=bad)
        assert r.status_code == 422, r.text


def test_apply_copies_the_bins_into_the_safety_config(tmp_path, monkeypatch):
    """The active site's status is what the editor reads for the line the engine
    is gating with, so applying a location must carry it beside the points."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        lid = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": _BINS}).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
    safety = cfg_store.cfg().safety
    assert [list(p) for p in safety.horizon] == _POINTS
    assert safety.horizon_bins == _BINS


def test_a_put_to_the_active_site_writes_the_new_bins_through(tmp_path, monkeypatch):
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    other = [{"alt": 5.0, "status": "measured"}, {"alt": 90.0, "status": "unknown"}]
    with TestClient(app) as c:
        lid = c.post("/api/locations", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": _BINS}).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
        r = c.put(f"/api/locations/{lid}", json={**_BODY, "horizon_points": _POINTS, "horizon_bins": other})
        assert r.status_code == 200, r.text
    assert cfg_store.cfg().safety.horizon_bins == other


# --------------------------------------------------------- the safety config

def test_the_safety_config_round_trips_bins_through_a_restart(tmp_path):
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    safety = store.cfg().safety.model_copy(update={"horizon": [(0.0, 5.0)], "horizon_bins": _BINS})
    store.set_safety(safety)
    assert ConfigStore(path=path).cfg().safety.horizon_bins == _BINS


def test_a_new_horizon_saved_with_the_old_bins_drops_them(tmp_path):
    """A writer that knows nothing of bins edits the line and echoes the rest of
    the safety block back, bins included. They no longer describe the line."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_safety(store.cfg().safety.model_copy(update={"horizon": [(0.0, 5.0)], "horizon_bins": _BINS}))
    echoed = store.cfg().safety.model_copy(update={"horizon": [(0.0, 40.0)]})
    store.set_safety(echoed)
    assert store.cfg().safety.horizon_bins is None


def test_damaged_bins_in_a_safety_block_load_as_none():
    safety = SafetyConfig(horizon=[(0.0, 5.0)], horizon_bins=[{"alt": 5.0, "status": "invented"}])
    assert safety.horizon == [(0.0, 5.0)] and safety.horizon_bins is None
