# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.5 (#1108 part c, member #599): a horizon the rig learns from the nights
it loses to a tree, offered to the operator and applied only on a press.

THE CLASS. A target behind a tree fails to centre, the classifier (WP 2.1) says
the frame shows an obstruction, and the rig then forgets: the next night it
rises through the same tree and holds for the same 25 minutes (#1093). The
obstructed frames are the evidence for the horizon the site does not have.

WHAT IS GRADED
  * ``HorizonLearner``: refusals collect per night by (altitude, azimuth bin);
    a region that the frames agree on becomes a suggestion; a scatter, a bin a
    clear frame contradicts, an altitude no tree reaches, and a region the drawn
    horizon already covers do not.
  * the merged line a suggestion proposes never sits below the line already
    drawn, and is a line the horizon rule accepts.
  * D-07 (the 2026-09-30 backlog ruling the issue cites): NOTHING applies the
    mask until the operator presses accept. Recording frames and reading the
    suggestion change neither the active safety config nor any saved location;
    accept writes both, once, for the suggestion the operator saw.
  * privacy: a viewer is served no suggestion data and cannot accept; the log
    line the learner writes names counts and no angle.

The site below is made up and is nowhere in the world.

MUTANT (named in the work package): the suggestion route applies the mask
without the press. ``test_reading_the_suggestion_applies_nothing`` fails.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck import site_horizon
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.catalog.coords import altaz
from astrodeck.config import ConfigStore
from astrodeck.imaging.frame_class import FrameClass
from astrodeck.locations import LocationStore, normalize_horizon_points
from astrodeck.sequence.schedule import effective_floor, interp_wrap
from astrodeck.site_horizon import HorizonLearner, note_solve_frame

LAT, LON = 12.5, 34.5
NOW = 1_800_000_000.0
NIGHT = 12 * 3600.0
OBSTRUCTED = FrameClass.OBSTRUCTED
STARS = FrameClass.STARS


def _tree(learner, az_from, az_to, alt, *, night=0, step=5.0, cls=OBSTRUCTED):
    """One obstructed frame per ``step`` degrees of azimuth across a tree."""
    az = az_from
    while az <= az_to:
        learner.note(cls, alt, az, NOW + night * 24 * 3600.0)
        az += step


# ------------------------------------------------------------ the learner

def test_a_night_whose_obstructed_frames_cluster_yields_a_suggestion():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    s = learner.suggestion(None)
    assert s is not None
    assert len(s.regions) == 1
    region = s.regions[0]
    assert 120.0 <= region.az_from <= 125.0 and 145.0 <= region.az_to <= 150.0
    assert region.alt_max == 40.0
    assert s.frames == 6 and s.bins == 3 and s.nights == 1


def test_the_suggested_line_blocks_the_tree_and_leaves_the_sky_alone():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    line = learner.suggestion(None).points
    assert interp_wrap(line, 135.0) >= 40.0
    assert interp_wrap(line, 300.0) == 0.0
    assert interp_wrap(line, 5.0) == 0.0


def test_a_scatter_of_far_apart_frames_is_not_a_region():
    learner = HorizonLearner()
    for az in (20.0, 110.0, 200.0, 290.0):
        learner.note(OBSTRUCTED, 37.0, az, NOW)
    assert learner.suggestion(None) is None


def test_two_frames_are_not_yet_a_region():
    learner = HorizonLearner()
    learner.note(OBSTRUCTED, 37.0, 131.0, NOW)
    learner.note(OBSTRUCTED, 37.0, 134.0, NOW)
    assert learner.suggestion(None) is None


def test_a_clear_frame_at_or_below_the_refusal_contradicts_the_bin():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0, step=2.5)
    learner.note(STARS, 36.0, 133.0, NOW)
    s = learner.suggestion(None)
    assert s is not None
    assert s.bins == 2 and len(s.regions) == 2
    assert interp_wrap(s.points, 135.0) == 0.0


def test_a_clear_frame_above_the_refusal_caps_the_mask_below_it():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    learner.note(STARS, 38.0, 133.0, NOW)
    line = learner.suggestion(None).points
    assert 37.0 <= interp_wrap(line, 133.0) <= 38.0


def test_an_altitude_no_tree_reaches_is_not_suggested():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 75.0)
    assert learner.suggestion(None) is None


@pytest.mark.parametrize("cls", [FrameClass.NO_LIGHT, FrameClass.CLOUD,
                                 FrameClass.UNKNOWN, FrameClass.SOLVER_MISSING])
def test_only_an_obstruction_is_evidence_of_a_horizon(cls):
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0, cls=cls)
    assert learner.suggestion(None) is None


def test_a_region_the_drawn_horizon_already_covers_is_not_offered_again():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    drawn = [(0.0, 0.0), (110.0, 0.0), (120.0, 45.0), (150.0, 45.0),
             (160.0, 0.0)]
    assert learner.suggestion(drawn) is None


def test_the_merged_line_is_never_below_the_drawn_one_and_passes_the_rule():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    drawn = [(0.0, 12.0), (90.0, 20.0), (200.0, 8.0), (300.0, 15.0)]
    line = learner.suggestion(drawn).points
    assert normalize_horizon_points(line) == line
    for az in range(0, 360):
        assert interp_wrap(line, float(az)) >= interp_wrap(drawn, float(az)) - 1e-9
    assert interp_wrap(line, 135.0) >= 40.0


def test_a_tree_across_north_is_one_region():
    learner = HorizonLearner()
    _tree(learner, 351.0, 359.0, 30.0)
    _tree(learner, 1.0, 14.0, 30.0)
    s = learner.suggestion(None)
    assert len(s.regions) == 1
    assert s.regions[0].az_from > s.regions[0].az_to


def test_refusals_are_collected_per_night_and_the_store_is_bounded():
    learner = HorizonLearner()
    for night in range(site_horizon.MAX_NIGHTS + 3):
        _tree(learner, 121.0, 149.0, 37.0, night=night)
    assert learner.nights() == site_horizon.MAX_NIGHTS
    assert learner.suggestion(None).nights == site_horizon.MAX_NIGHTS


def test_the_suggestion_id_follows_the_line_it_proposes():
    a, b = HorizonLearner(), HorizonLearner()
    _tree(a, 121.0, 149.0, 37.0)
    _tree(b, 121.0, 149.0, 37.0)
    assert a.suggestion(None).id == b.suggestion(None).id
    _tree(b, 201.0, 229.0, 37.0)
    assert a.suggestion(None).id != b.suggestion(None).id


def test_note_solve_frame_files_the_frame_under_its_own_altitude_and_azimuth():
    learner = HorizonLearner()
    alt, az = 36.5, 133.0
    ra, dec = _radec_at(alt, az)
    for _ in range(3):
        note_solve_frame(OBSTRUCTED, ra, dec, latitude=LAT, longitude=LON,
                         now=NOW, learner=learner)
    s = learner.suggestion(None)
    assert s is not None
    assert s.regions[0].az_from <= az <= s.regions[0].az_to
    assert s.regions[0].alt_max == 40.0


def _radec_at(alt, az):
    """The (ra_hours, dec_deg) that stands at (alt, az) from the made-up site at
    NOW, found by search over the same ``altaz`` the production code reads."""
    best = None
    for ra10 in range(0, 240):
        for dec in range(-60, 90):
            a, z = altaz(ra10 / 10.0, float(dec), LAT, LON, NOW)
            err = abs(a - alt) + abs(((z - az) + 180) % 360 - 180)
            if best is None or err < best[0]:
                best = (err, ra10 / 10.0, float(dec))
    assert best[0] < 6.0
    return best[1], best[2]


# ------------------------------------------------------- the hold's wording

def test_a_hold_with_no_horizon_says_what_the_operator_can_do():
    hint = site_horizon.hold_hint(None, HorizonLearner())
    assert "no horizon is drawn" in hint and "Site page" in hint
    assert "suggestion" not in hint


def test_a_hold_with_no_horizon_and_a_suggestion_points_at_the_suggestion():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    hint = site_horizon.hold_hint(None, learner)
    assert "no horizon is drawn" in hint and "suggestion" in hint


def test_a_hold_with_a_horizon_and_nothing_waiting_says_nothing():
    assert site_horizon.hold_hint([(0.0, 5.0)], HorizonLearner()) is None


def test_a_hold_with_a_horizon_and_a_suggestion_names_only_the_suggestion():
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    hint = site_horizon.hold_hint([(0.0, 5.0)], learner)
    assert "suggestion" in hint and "no horizon is drawn" not in hint


# --------------------------------------------------------------- the log

def test_the_log_line_names_counts_and_no_angle(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(site_horizon.bus, "log",
                        lambda level, message, source="hub", **kw: lines.append(message))
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    assert lines, "the learner announced nothing when a region formed"
    text = " ".join(lines)
    for token in ("37", "40", "121", "149", "133", "135"):
        assert token not in text, f"the log line carries {token}: {text!r}"
    assert "3 bins from 5 obstructed frames" in lines[-1]


def test_the_log_announces_a_region_once_not_per_frame(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(site_horizon.bus, "log",
                        lambda level, message, source="hub", **kw: lines.append(message))
    learner = HorizonLearner()
    _tree(learner, 121.0, 149.0, 37.0)
    after_tree = len(lines)
    for _ in range(10):
        learner.note(OBSTRUCTED, 37.0, 133.0, NOW)
    assert len(lines) == after_tree


# --------------------------------------------------------------- the routes

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


def _as(role):
    set_active_provider(_FakeProvider(principal_for_role(role)))


@pytest.fixture(autouse=True)
def _clean():
    site_horizon.LEARNER.clear()
    yield
    site_horizon.LEARNER.clear()
    reset_active_provider()


_SITE = {"name": "Test field", "latitude": LAT, "longitude": LON,
         "elevation_m": 10.0}
SUGGEST = "/api/locations/horizon-suggestion"
ACCEPT = "/api/locations/horizon-suggestion/accept"


def _learn_a_tree():
    _tree(site_horizon.LEARNER, 121.0, 149.0, 37.0)


def _active_site(client):
    """Save and apply a location carrying a horizon line of its own."""
    r = client.post("/api/locations", json={
        **_SITE, "horizon_points": [[0.0, 12.0], [180.0, 12.0]]})
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    assert client.post(f"/api/locations/{lid}/apply").status_code == 200
    return lid


def test_reading_the_suggestion_applies_nothing(tmp_path, monkeypatch):
    """D-07: the rig learns, offers, and waits for the press. After every read
    the floor the engine judges a slew against, the active safety config and the
    saved location are exactly what they were before the frames were learned."""
    cfg, locs, app = _make_client(tmp_path, monkeypatch)
    _as("admin")
    with TestClient(app) as c:
        _active_site(c)

        def state():
            safety = cfg.cfg().safety
            return ([list(p) for p in safety.horizon],
                    locs.list()[0].horizon_points, cfg.cfg().version,
                    effective_floor(15.0, safety.horizon, 135.0))

        before = state()
        _learn_a_tree()
        assert state() == before, "learning frames applied a mask"
        for _ in range(3):
            body = c.get(SUGGEST)
            assert body.status_code == 200, body.text
            assert state() == before, "reading the suggestion applied the mask"
            assert body.json()["suggestion"] is not None


def test_accept_stores_the_suggestion_as_the_site_horizon(tmp_path, monkeypatch):
    cfg, locs, app = _make_client(tmp_path, monkeypatch)
    _as("admin")
    with TestClient(app) as c:
        _active_site(c)
        _learn_a_tree()
        offered = c.get(SUGGEST).json()["suggestion"]
        r = c.post(ACCEPT, params={"suggestion_id": offered["id"]})
        assert r.status_code == 200, r.text
        assert c.get(SUGGEST).json()["suggestion"] is None
    line = [tuple(p) for p in cfg.cfg().safety.horizon]
    assert interp_wrap(line, 135.0) >= 40.0
    assert interp_wrap(line, 300.0) >= 12.0
    assert cfg.cfg().safety.horizon_bins is None
    saved = [tuple(p) for p in locs.list()[0].horizon_points]
    assert saved == line


def test_accept_without_a_suggestion_changes_nothing(tmp_path, monkeypatch):
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _as("admin")
    with TestClient(app) as c:
        _active_site(c)
        before = [list(p) for p in cfg.cfg().safety.horizon]
        r = c.post(ACCEPT, params={"suggestion_id": "deadbeef0000"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "no_suggestion"
    assert [list(p) for p in cfg.cfg().safety.horizon] == before


def test_accept_of_a_suggestion_that_moved_changes_nothing(tmp_path, monkeypatch):
    """The operator accepts the line they saw, not the one that formed after."""
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _as("admin")
    with TestClient(app) as c:
        _active_site(c)
        _learn_a_tree()
        seen = c.get(SUGGEST).json()["suggestion"]["id"]
        _tree(site_horizon.LEARNER, 201.0, 229.0, 37.0)
        before = [list(p) for p in cfg.cfg().safety.horizon]
        r = c.post(ACCEPT, params={"suggestion_id": seen})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "suggestion_changed"
    assert [list(p) for p in cfg.cfg().safety.horizon] == before


def test_the_offer_says_whether_a_horizon_is_drawn(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _as("admin")
    with TestClient(app) as c:
        body = c.get(SUGGEST).json()
        assert body == {"drawn": False, "suggestion": None}
        _active_site(c)
        assert c.get(SUGGEST).json()["drawn"] is True


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_a_cleared_principal_is_served_the_suggestion(tmp_path, monkeypatch, role):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _as(role)
    _learn_a_tree()
    with TestClient(app) as c:
        r = c.get(SUGGEST)
        assert r.status_code == 200, r.text
        assert r.json()["suggestion"]["regions"]


@pytest.mark.parametrize("role", ["viewer", "syncer"])
def test_an_uncleared_principal_gets_no_suggestion_data(tmp_path, monkeypatch, role):
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _as(role)
    _learn_a_tree()
    with TestClient(app) as c:
        r = c.get(SUGGEST)
        assert r.status_code == 403
        for needle in ("regions", "suggestion", "135", "121", "az_from"):
            assert needle not in r.text.replace('"detail"', ""), r.text
        before = cfg.cfg().safety.horizon
        offered = site_horizon.LEARNER.suggestion(None).id
        r = c.post(ACCEPT, params={"suggestion_id": offered})
        assert r.status_code == 403
    assert cfg.cfg().safety.horizon == before


def test_nothing_the_viewer_may_read_carries_the_suggestion(tmp_path, monkeypatch):
    _cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _as("viewer")
    _learn_a_tree()
    with TestClient(app) as c:
        for path in ("/api/status", "/api/site", "/api/config"):
            r = c.get(path)
            assert "suggestion" not in r.text, path
            assert "az_from" not in r.text, path


def test_the_accept_route_is_behind_the_relay_fence():
    """It writes the line every slew is judged against, so a tunnelled cookie
    must not reach it: it sits under the ``/api/locations`` prefix that the
    relay fence already covers."""
    assert any(ACCEPT.startswith(p) for p in app_module._REMOTE_LOCAL_ONLY_MUTATION_PREFIXES)


def test_the_two_routes_are_declared_for_site_derived_readers():
    from astrodeck.auth import CAP_VIEW_SITE_DERIVED
    from astrodeck.auth.rbac import CAP_ATTR
    app = app_module.create_app()
    declared = {}
    for route in app.routes:
        if getattr(route, "path", "") in (SUGGEST, ACCEPT):
            for method in route.methods - {"HEAD"}:
                declared[(method, route.path)] = route.endpoint
    assert set(declared) == {("GET", SUGGEST), ("POST", ACCEPT)}
    for endpoint in declared.values():
        assert CAP_VIEW_SITE_DERIVED in getattr(endpoint, CAP_ATTR)
