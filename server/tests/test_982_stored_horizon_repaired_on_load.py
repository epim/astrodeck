# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#982: a horizon already on disk that breaks the horizon rule is repaired at
config load, and the floor never reads a NaN as "no obstruction".

#899 closed the two API doors into ``config.safety.horizon``; the third door is
the config file itself. ``SafetyConfig.horizon`` is a bare ``list[tuple[float,
float]]``, so a config.json written before that rule, restored from its ``.bak``
or edited by hand loaded with a NaN, an altitude of 999 or an azimuth of 720
unchanged. ``GET /api/config`` then answered 500 (the redacted payload cannot be
JSON-encoded) and ``effective_floor`` kept ``min_alt_deg`` against the NaN,
because ``max()`` keeps its first argument: a false OPEN, with the settings UI
that could fix it unable to load.

Every case is graded on BEHAVIOUR: what the store holds after the load, what
was said, what the route answers, and what the floor is. Bad shapes are written
to a real config.json (a NaN as the bare ``NaN`` literal a hand edit or an old
build leaves), never built in memory. Values in the file are chosen so they are
recognisable if they leak into a log line.

Repo convention (mirrors tests/test_899_active_site_horizon_validated.py):
in-process fakes via monkeypatch + TestClient, NO unittest.mock, an isolated
ConfigStore and LocationStore per test.
"""
from __future__ import annotations

import json
import math
import random

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.catalog.ephemeris.passes import floor_at
from astrodeck.config import CONFIG_SCHEMA, ConfigStore
from astrodeck.locations import (MAX_HORIZON_POINTS, LocationStore,
                                 normalize_horizon_points,
                                 sanitize_horizon_points)
from astrodeck.sequence.schedule import effective_floor, interp_wrap

NAN = float("nan")
INF = float("inf")
#: An altitude and an azimuth no horizon can have, and numbers that appear in no
#: rule text, so a line that echoes a stored VALUE is caught by a substring test.
LEAK_ALT = 987.25
LEAK_AZ = 731.5
_LEAK_TEXT = ("987", "731")


# --------------------------------------------------------------------- harness

def _write(path, horizon, **extra):
    """A config.json whose ``safety.horizon`` is exactly ``horizon``. Stamped
    current so the load owes no migration and therefore writes nothing."""
    body = {"schema_version": CONFIG_SCHEMA, "version": 7,
            "safety": {"min_alt_deg": 20.0, "horizon": horizon}, **extra}
    path.write_text(json.dumps(body), encoding="utf-8")


def _pts(horizon):
    return None if horizon is None else [list(p) for p in horizon]


def _config_warnings(bus_lines):
    return [m for (lvl, m, src) in bus_lines
            if src == "config" and lvl == "warning" and "horizon" in m]


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


def _app_on(store, tmp_path, monkeypatch):
    """The real app over an isolated ConfigStore ``store`` (already pointed at
    the file under test) and an isolated LocationStore."""
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.locations as loc_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    locs = LocationStore(path=tmp_path / "locations.json")
    monkeypatch.setattr(loc_mod, "location_store", locs)
    monkeypatch.setattr(app_module, "location_store", locs)
    reset_active_provider()
    set_active_provider(_FakeProvider(principal_for_role("admin")))
    return app_module.create_app()


#: (stored horizon, what it must load as, a phrase of the rule said, how many).
#: Always one good point at 180 degrees (altitude 40, above the floor of 20) so
#: "the usable obstruction is kept" is graded beside "the bad one is dropped".
_GOOD = [180.0, 40.0]
_BAD_SHAPES = [
    pytest.param([[0.0, 10.0], [90.0, NAN], _GOOD], [[0.0, 10.0], _GOOD],
                 "must be finite", 1, id="nan-altitude"),
    pytest.param([[NAN, 10.0], _GOOD], [_GOOD], "must be finite", 1,
                 id="nan-azimuth"),
    pytest.param([[0.0, INF], _GOOD], [_GOOD], "must be finite", 1,
                 id="infinite-altitude"),
    pytest.param([[0.0, -INF], _GOOD], [_GOOD], "must be finite", 1,
                 id="negative-infinite-altitude"),
    pytest.param([[10.0, LEAK_ALT], [200.0, LEAK_ALT], _GOOD], [_GOOD],
                 "altitude must be", 2, id="altitude-out-of-range-twice"),
    pytest.param([[10.0, -11.0], _GOOD], [_GOOD], "altitude must be", 1,
                 id="altitude-minus-11"),
    pytest.param([[LEAK_AZ, 10.0], _GOOD], [_GOOD], "azimuth must be", 1,
                 id="azimuth-out-of-range"),
    pytest.param([[-1.0, 10.0], _GOOD], [_GOOD], "azimuth must be", 1,
                 id="azimuth-minus-1"),
    pytest.param([[10.0, 5.0, 1.0], _GOOD], [_GOOD], "[az_deg, alt_deg] pair", 1,
                 id="three-values"),
    pytest.param([[10.0], _GOOD], [_GOOD], "[az_deg, alt_deg] pair", 1,
                 id="one-value"),
    pytest.param(["abc", None, _GOOD], [_GOOD], "[az_deg, alt_deg] pair", 2,
                 id="not-a-pair"),
    pytest.param([["north", 5.0], _GOOD], [_GOOD], "must be numbers", 1,
                 id="text-value"),
    pytest.param([[0.0, NAN], [90.0, LEAK_ALT]], None, "", 1,
                 id="nothing-usable-remains"),
    pytest.param("oops", None, "must be a list", 1, id="not-a-list-text"),
    pytest.param(5, None, "must be a list", 1, id="not-a-list-number"),
    pytest.param({"0": [0.0, 5.0]}, None, "must be a list", 1,
                 id="not-a-list-object"),
]


# ============================================== the store loads and repairs

@pytest.mark.parametrize("stored, kept, rule, count", _BAD_SHAPES)
def test_a_bad_stored_horizon_loads_repaired_and_says_so(
        tmp_path, bus_lines, stored, kept, rule, count):
    """Each bad shape the issue lists, in a real file: the store loads (it does
    not refuse and stop the server), the usable points survive, the unusable
    ones go, and one warning names the rule and the count."""
    path = tmp_path / "astrodeck.json"
    _write(path, stored)

    cfg = ConfigStore(path=path).cfg()

    assert _pts(cfg.safety.horizon) == kept
    # What it kept is a horizon the API doors would take.
    if kept is not None:
        normalize_horizon_points(cfg.safety.horizon)
    said = _config_warnings(bus_lines)
    assert len(said) == 1, said
    assert "repaired on load" in said[0]
    assert rule in said[0], said
    assert f"(x{count})" in said[0], said
    for needle in _LEAK_TEXT:
        assert needle not in said[0], "the warning echoed a stored value"


@pytest.mark.parametrize("stored, kept, rule, count", _BAD_SHAPES)
def test_the_config_route_answers_200_over_a_bad_stored_horizon(
        tmp_path, monkeypatch, stored, kept, rule, count):
    """The symptom the operator met: the app starts over the bad file and
    ``GET /api/config`` answers 200 with the repaired horizon, so the settings
    UI that needs the route can load to let them fix it. Unrepaired, a NaN made
    the payload un-encodable and the route answered 500."""
    path = tmp_path / "astrodeck.json"
    _write(path, stored)
    app = _app_on(ConfigStore(path=path), tmp_path, monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/config")

    assert r.status_code == 200, r.text[:200]
    assert r.json()["safety"]["horizon"] == kept


def test_a_good_point_beside_a_bad_one_keeps_gating_slews(tmp_path):
    """The repair drops the unusable point, not the obstruction: the tree line
    at 180 degrees still raises the floor, and a horizon the load threw away
    wholesale would have lowered it."""
    path = tmp_path / "astrodeck.json"
    _write(path, [[0.0, 10.0], [90.0, NAN], [180.0, 40.0]])

    safety = ConfigStore(path=path).cfg().safety

    assert effective_floor(safety.min_alt_deg, safety.horizon, 180.0) == 40.0
    assert effective_floor(safety.min_alt_deg, safety.horizon, 0.0) == 20.0


# ========================================== a good horizon is not touched

@pytest.mark.parametrize("stored", [
    pytest.param([[270.0, 30.0], [90.0, 8.0], [360.0, 22.0], [0.0, 22.0],
                  [90.0, 9.0]], id="unsorted-with-duplicates-and-a-360"),
    pytest.param([], id="the-explicit-clear"),
    pytest.param(None, id="no-drawn-horizon"),
    pytest.param([[i * 2.0, 5.0] for i in range(MAX_HORIZON_POINTS)],
                 id="exactly-the-cap"),
])
def test_a_horizon_that_passes_the_rule_loads_exactly_as_stored_and_silently(
        tmp_path, bus_lines, stored):
    """The repair only acts on what the rule refuses. A valid horizon is not
    sorted, folded or deduped on load (the API doors store it as sent too), and
    nothing is said."""
    path = tmp_path / "astrodeck.json"
    _write(path, stored)

    cfg = ConfigStore(path=path).cfg()

    assert _pts(cfg.safety.horizon) == stored
    assert _config_warnings(bus_lines) == []


def test_a_config_with_no_safety_block_loads_with_no_horizon(
        tmp_path, bus_lines):
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({"schema_version": CONFIG_SCHEMA, "version": 1}),
                    encoding="utf-8")
    assert ConfigStore(path=path).cfg().safety.horizon is None
    assert _config_warnings(bus_lines) == []


def test_the_load_does_not_rewrite_the_file_the_next_save_does(tmp_path):
    """The bad file is the evidence, so the load leaves it; the first settings
    save writes the repaired horizon, and nothing in it is non-finite."""
    path = tmp_path / "astrodeck.json"
    _write(path, [[0.0, NAN], _GOOD])
    before = path.read_bytes()

    store = ConfigStore(path=path)
    store.cfg()
    assert path.read_bytes() == before, "the load rewrote the config file"

    store.bump_and_save()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["safety"]["horizon"] == [_GOOD]
    assert on_disk["version"] == 8


# ==================================================== the .bak restore path

@pytest.mark.parametrize("primary", ["{ not json", None],
                         ids=["corrupt-primary", "missing-primary"])
def test_a_backup_holding_a_bad_horizon_is_repaired_before_it_is_restored(
        tmp_path, bus_lines, primary):
    """The other door in config.py: a primary that is corrupt or gone is rebuilt
    from ``astrodeck.json.bak``, and the rebuilt primary is what the next boot
    reads. A repair that ran only on the primary would write the NaN straight
    back out."""
    path = tmp_path / "astrodeck.json"
    bak = tmp_path / "astrodeck.json.bak"
    _write(bak, [[0.0, NAN], [90.0, LEAK_ALT], _GOOD])
    if primary is not None:
        path.write_text(primary, encoding="utf-8")

    cfg = ConfigStore(path=path).cfg()

    assert _pts(cfg.safety.horizon) == [_GOOD]
    assert cfg.version == 7, "the backup was the source"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["safety"]["horizon"] == [_GOOD], "the primary was re-persisted bad"
    said = _config_warnings(bus_lines)
    assert any("repaired on load" in m for m in said), said
    for m in said:
        for needle in _LEAK_TEXT:
            assert needle not in m


# ===================================================== more than the cap

def _jagged(n, seed):
    """``n`` control points at distinct random azimuths with altitudes across
    the whole legal range: the worst case for a thinning that must not lower."""
    rng = random.Random(seed)
    azs = rng.sample(range(0, 36000), n)
    return [[a / 100.0, rng.uniform(-10.0, 90.0)] for a in sorted(azs)]


def test_an_over_cap_horizon_is_thinned_to_the_cap_and_says_so(
        tmp_path, bus_lines):
    path = tmp_path / "astrodeck.json"
    _write(path, _jagged(501, seed=1))

    cfg = ConfigStore(path=path).cfg()

    assert len(cfg.safety.horizon) == MAX_HORIZON_POINTS
    normalize_horizon_points(cfg.safety.horizon)        # the rule takes it
    said = _config_warnings(bus_lines)
    assert len(said) == 1 and f"at most {MAX_HORIZON_POINTS} horizon points" in said[0]
    assert "(x1)" in said[0]


@pytest.mark.parametrize("seed", range(8))
def test_thinning_never_lowers_the_floor_anywhere(seed):
    """The reason it resamples rather than truncating. A thinned line must be at
    or above the original at EVERY azimuth, or repairing the cap would itself
    open the obstruction floor over some stretch of sky. Graded
    at every control point of either line, which is EXACT for two piecewise-
    linear lines (their difference is linear between those azimuths, so its
    minimum is at one of them), on random lines of 181 to 600 points."""
    rng = random.Random(1000 + seed)
    original = _jagged(rng.randint(MAX_HORIZON_POINTS + 1, 600), seed)

    thinned, problems = sanitize_horizon_points(original)

    assert problems and len(thinned) == MAX_HORIZON_POINTS
    for az in sorted({a for a, _ in original} | {a for a, _ in thinned}):
        assert interp_wrap(thinned, az) >= interp_wrap(original, az) - 1e-9, az


def test_thinning_keeps_a_lone_spike_that_truncation_would_lose():
    """A 90 degree wall at 359 among 200 low points: keeping the first 180 by
    azimuth, or the first 180 as written, would drop it and open the sky it
    stood over."""
    low = [[i * 1.7, 2.0] for i in range(200) if i * 1.7 < 358.0]
    original = low + [[359.0, 90.0]]
    assert len(original) > MAX_HORIZON_POINTS

    thinned, _problems = sanitize_horizon_points(original)

    assert interp_wrap(thinned, 359.0) == 90.0
    assert interp_wrap(thinned, 358.5) >= interp_wrap(original, 358.5) - 1e-9


# =================================================== the floor, NaN or not

@pytest.mark.parametrize("horizon", [
    pytest.param([(0.0, NAN), (90.0, 999.0)], id="the-issue-probe"),
    pytest.param([(0.0, NAN)], id="a-single-nan-point"),
    pytest.param([(0.0, 10.0), (120.0, NAN), (240.0, 10.0)], id="nan-in-the-middle"),
    pytest.param([(NAN, 50.0), (10.0, 5.0), (200.0, 5.0)], id="nan-azimuth"),
    pytest.param([(0.0, INF), (180.0, 5.0)], id="infinite-altitude"),
    pytest.param([(0.0, -INF), (180.0, 5.0)], id="negative-infinite-altitude"),
    pytest.param([(INF, 5.0), (180.0, 5.0)], id="infinite-azimuth"),
])
def test_a_horizon_that_cannot_be_read_blocks_the_sky_not_opens_it(horizon):
    """What a hand-built or otherwise unrepaired horizon reads as: the zenith.
    ``max()`` keeps its first argument against a NaN, so a NaN let through gave
    ``effective_floor`` = ``min_alt_deg`` -- no obstruction -- at every azimuth
    the NaN touched."""
    for az in (0.0, 10.0, 45.0, 90.0, 200.0, 300.0, 359.9):
        assert interp_wrap(horizon, az) == 90.0, az
        assert effective_floor(20.0, horizon, az) == 90.0, az


def test_the_passes_floor_reads_an_unreadable_horizon_as_blocked_too():
    """``catalog.ephemeris.passes.floor_at`` is a second reader of the same
    line (``interp_wrap``) and must not be a way around the guard."""
    assert floor_at([(0.0, NAN), (90.0, 10.0)], 20.0, 45.0) == 90.0


def test_a_readable_horizon_interpolates_as_it_always_did():
    h = [(0.0, 10.0), (180.0, 30.0)]
    assert interp_wrap(h, 90.0) == 20.0
    assert interp_wrap(h, 270.0) == 20.0            # and across the 0/360 seam
    assert effective_floor(25.0, h, 180.0) == 30.0
    assert effective_floor(25.0, h, 0.0) == 25.0
    assert interp_wrap([], 10.0) == 0.0 and interp_wrap(None, 10.0) == 0.0


def test_the_load_is_the_only_thing_standing_between_a_bad_file_and_the_floor(
        tmp_path):
    """The loaded horizon fed to the floor, end to end, for the issue's own
    probe: the file holds a NaN; after the load no azimuth reads below the
    configured minimum and the config is JSON-encodable."""
    path = tmp_path / "astrodeck.json"
    _write(path, [[0.0, NAN], [90.0, LEAK_ALT]])

    cfg = ConfigStore(path=path).cfg()

    for az in range(0, 360, 5):
        floor = effective_floor(cfg.safety.min_alt_deg, cfg.safety.horizon, az)
        assert math.isfinite(floor) and floor >= cfg.safety.min_alt_deg
    json.dumps(cfg.model_dump(), allow_nan=False)
