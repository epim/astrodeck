# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#899: the active site's door to ``config.safety.horizon`` refuses what the
saved-location door refuses.

One safety floor had two doors with two rules. ``PUT /api/locations/{id}`` ran
``normalize_horizon_points`` (at most 180 points, azimuth 0..360, altitude
-10..90, finite) and 422'd; ``POST /api/config {safety}`` stored whatever
``list[tuple[float, float]]`` would parse, so 501 points, an altitude of 999, an
azimuth of 720 and a NaN literal all reached the engine's obstruction rule.
A point at 999 is a floor no target clears (every slew at that azimuth denied),
and a NaN is silently dropped by ``max()`` in ``effective_floor``, which is a
false OPEN.

Every case below is graded on BEHAVIOUR: the HTTP status at each door, the
machine code and the plain reason, and what ``config.safety.horizon`` holds
afterwards (in memory and re-read from disk). The two doors are driven with the
SAME payloads so a rule that drifts at one of them fails here.

Repo convention (mirrors tests/test_locations_horizon.py): in-process fakes via
monkeypatch + TestClient, NO unittest.mock, an isolated ConfigStore and
LocationStore per test.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_VIEW_STATUS, Principal, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.config import ConfigStore
from astrodeck.locations import LocationStore, MAX_HORIZON_POINTS


# --------------------------------------------------------------------- harness

def _make_client(tmp_path, monkeypatch):
    """Isolated app + ConfigStore + LocationStore."""
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


_JSON = {"Content-Type": "application/json"}
_LOC = {"name": "Backyard", "latitude": 40.5, "longitude": -74.25,
        "elevation_m": 12.0}


def _post_active(c, points):
    """The active site's door. Sent as raw text: ``json.dumps`` writes a NaN as
    the bare literal ``NaN``, which a non-browser client can send and which the
    test client's own encoder would refuse to carry."""
    return c.post("/api/config", content=json.dumps(
        {"safety": {"horizon": points}}), headers=_JSON)


def _put_location(c, loc_id, points):
    """The saved location's door, with the same payload."""
    return c.put(f"/api/locations/{loc_id}", content=json.dumps(
        {**_LOC, "horizon_points": points}), headers=_JSON)


def _alternating(n):
    return [[float(i), -10.0 if i % 2 else 90.0] for i in range(n)]


#: (id, points) the engine could not use. Every one must be refused at BOTH doors.
_REFUSED = [
    pytest.param([[i * 0.5, 5.0] for i in range(501)], id="501-points"),
    pytest.param(_alternating(MAX_HORIZON_POINTS + 1), id="cap-plus-one"),
    pytest.param([[10.0, 999.0]], id="altitude-999"),
    pytest.param([[10.0, 91.0]], id="altitude-91"),
    pytest.param([[10.0, -11.0]], id="altitude-minus-11"),
    pytest.param([[720.0, 10.0]], id="azimuth-720"),
    pytest.param([[360.5, 10.0]], id="azimuth-360.5"),
    pytest.param([[-1.0, 10.0]], id="azimuth-minus-1"),
    pytest.param([[0.0, float("nan")]], id="nan-altitude"),
    pytest.param([[float("nan"), 10.0]], id="nan-azimuth"),
    pytest.param([[0.0, float("inf")]], id="infinite-altitude"),
    pytest.param([[0.0, float("-inf")]], id="negative-infinite-altitude"),
    pytest.param([[10.0, 5.0], [20.0, 999.0]], id="one-bad-point-among-good"),
    pytest.param([[10.0, 5.0, 1.0]], id="three-values"),
    pytest.param([[10.0]], id="one-value"),
]

#: (id, points) that are valid. Every one must be accepted at BOTH doors.
_ACCEPTED = [
    pytest.param([], id="empty-is-the-explicit-clear"),
    pytest.param([[0.0, 12.0]], id="single-point"),
    pytest.param([[0.0, -10.0], [360.0, 90.0]], id="the-bounds-themselves"),
    pytest.param(_alternating(MAX_HORIZON_POINTS), id="exactly-the-cap"),
    pytest.param([[270.0, 30.0], [90.0, 8.0], [0.0, 22.0]], id="unsorted"),
    pytest.param([[10.0, 30.0], [10.0, 12.0]], id="duplicate-azimuth"),
]


# ======================================================== the doors agree

@pytest.mark.parametrize("points", _REFUSED)
def test_what_the_location_door_refuses_the_active_door_refuses(
        tmp_path, monkeypatch, points):
    """The issue's table, row by row, at both doors: 422 at each, and the active
    door leaves the horizon it already had (nothing stored, nothing cleared)."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    cfg_store.set_safety(cfg_store.cfg().safety.model_copy(
        update={"horizon": [(0.0, 12.0), (180.0, 20.0)]}))
    version = cfg_store.cfg().version
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        loc_id = c.post("/api/locations", json=_LOC).json()["id"]
        location = _put_location(c, loc_id, points)
        active = _post_active(c, points)
    assert location.status_code == 422, ("location door", location.text[:200])
    assert active.status_code == 422, ("active door", active.text[:200])
    assert [list(p) for p in cfg_store.cfg().safety.horizon] == [
        [0.0, 12.0], [180.0, 20.0]]
    assert cfg_store.cfg().version == version, "a refused write bumped the version"
    assert [list(p) for p in cfg_store.reload().safety.horizon] == [
        [0.0, 12.0], [180.0, 20.0]], "the refused horizon reached the disk"


@pytest.mark.parametrize("points", _ACCEPTED)
def test_what_the_location_door_accepts_the_active_door_accepts(
        tmp_path, monkeypatch, points):
    """The other half of agreeing: the new rule must not refuse a horizon the
    saved-location door takes, or the same scan would save to a location and
    fail on the active site (the photosphere cases in the issue)."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        loc_id = c.post("/api/locations", json=_LOC).json()["id"]
        location = _put_location(c, loc_id, points)
        active = _post_active(c, points)
    assert location.status_code == 200, ("location door", location.text[:200])
    assert active.status_code == 200, ("active door", active.text[:200])


# ================================================== the refusal, in words

@pytest.mark.parametrize("points, hint", [
    pytest.param([[i * 0.5, 5.0] for i in range(501)], "at most 180",
                 id="501-points"),
    pytest.param([[10.0, 999.0]], "altitude", id="altitude-999"),
    pytest.param([[720.0, 10.0]], "azimuth", id="azimuth-720"),
    pytest.param([[0.0, float("nan")]], "finite", id="nan"),
])
def test_the_refusal_carries_a_code_and_a_plain_reason(
        tmp_path, monkeypatch, points, hint):
    """A caller reading the 422 must learn WHICH rule it broke: a code to branch
    on, and a sentence the UI can show as it stands."""
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = _post_active(c, points)
    assert r.status_code == 422, r.text[:200]
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_horizon", detail
    assert detail["detail"].startswith("horizon not saved: "), detail
    assert hint in detail["detail"], detail


# ============================================ nothing half-written, ever

def test_a_refused_horizon_leaves_every_other_block_of_the_body_unwritten(
        tmp_path, monkeypatch):
    """``_persist_config_patch`` writes block by block (site first), so a
    refusal found at the safety block's turn would leave the blocks before it
    saved under an error the caller reads as 'nothing happened'."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    cfg_store.set_safety(cfg_store.cfg().safety.model_copy(
        update={"horizon": [(0.0, 12.0)]}))
    before_name = cfg_store.cfg().site.name
    version = cfg_store.cfg().version
    _install(principal_for_role("admin"))
    site = {"name": "Somewhere Else", "latitude": 10.0, "longitude": 20.0,
            "elevation_m": 5.0}
    with TestClient(app) as c:
        refused = c.post("/api/config", content=json.dumps(
            {"site": site, "safety": {"horizon": [[10.0, 999.0]]}}),
            headers=_JSON)
        assert refused.status_code == 422, refused.text[:200]
        assert cfg_store.cfg().site.name == before_name, "the site was written"
        assert cfg_store.cfg().version == version
        assert [list(p) for p in cfg_store.cfg().safety.horizon] == [[0.0, 12.0]]
        # and the same body with a usable horizon is saved whole
        saved = c.post("/api/config", content=json.dumps(
            {"site": site, "safety": {"horizon": [[10.0, 45.0]]}}),
            headers=_JSON)
        assert saved.status_code == 200, saved.text[:200]
    assert cfg_store.cfg().site.name == "Somewhere Else"
    assert [list(p) for p in cfg_store.cfg().safety.horizon] == [[10.0, 45.0]]


def test_a_caller_without_the_cap_is_told_403_not_what_the_rule_is(
        tmp_path, monkeypatch):
    """Authorisation first: a principal that may not write the safety block
    learns nothing about its validation from the answer."""
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(Principal(role="custom", email=None,
                       caps=frozenset({CAP_VIEW_STATUS}), jti=None))
    with TestClient(app) as c:
        r = _post_active(c, [[10.0, 999.0]])
    assert r.status_code == 403, r.text[:200]


# ====================================== a valid horizon saves as it did

def test_a_valid_horizon_is_stored_exactly_as_sent(tmp_path, monkeypatch):
    """The rule refuses; it does not rewrite. The location door canonicalises
    (sorts, folds 360 onto 0, dedupes) and the active door must not start to:
    ``interp_wrap`` sorts and wraps for itself, and what a horizon saved as
    before this change is what it saves as after."""
    cfg_store, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    sent = [[270.0, 30.0], [90.0, 8.0], [360.0, 22.0], [0.0, 22.0],
            [90.0, 9.0]]
    with TestClient(app) as c:
        r = _post_active(c, sent)
        assert r.status_code == 200, r.text[:200]
        assert [list(p) for p in cfg_store.cfg().safety.horizon] == sent
        assert [list(p) for p in cfg_store.reload().safety.horizon] == sent
        assert c.get("/api/site").json()["site"]["horizon_points"] == sent
