# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight's visible passes: the three conditions, and what each one removes.

A SEEDED ELEMENT SET AND A FIXED CLOCK. Every number below comes from one
pinned TLE (NORAD 25544, epoch 2026-09-10 03:26:38 UTC) propagated from one
pinned instant, so "the ISS goes over at 01:17" is a claim about this code
rather than about where the station happens to be while the suite runs. The
propagator itself is graded against the AIAA corpus in
``test_satellite_ephemeris.py``; this file is about the three filters on top of
it.

IN 48 HOURS FROM THAT INSTANT, at the synthetic fixture site 40.0 N / 99.0 W
(well away from the real observatory, #19), the station is above a 10-degree
horizon TWELVE times and VISIBLE four times. The eight it loses are the whole
point of the file:

    2026-09-10 05:22  peak 11.4   0/14 samples sunlit   -> in the Earth's shadow
    2026-09-10 06:58  peak 20.4   0/32 samples sunlit   -> in the Earth's shadow
    2026-09-10 08:34  peak 70.5   0/39 samples sunlit   -> in the Earth's shadow
    2026-09-11 04:34  peak 11.6   0/15 samples sunlit   -> in the Earth's shadow
    2026-09-11 06:11  peak 16.2   0/27 samples sunlit   -> in the Earth's shadow
    2026-09-11 07:47  peak 66.8   0/39 samples sunlit   -> in the Earth's shadow
    2026-09-11 09:25  peak 13.0   0/20 samples sunlit   -> in the Earth's shadow
    2026-09-12 00:30  peak 30.6  36/36 samples sunlit   -> the SUN was up here

The 70-degree pass on the 10th is the one that makes the case: it crosses
high, it is unmistakably "up", and there is nothing to see because the
station is in the Earth's shadow the whole way. A predictor that reported it
would send somebody outside to look at an empty sky.
"""
from __future__ import annotations

import numpy as np
import pytest

from astrodeck.catalog.ephemeris import elements as el
from astrodeck.catalog.ephemeris import passes as P
from astrodeck.catalog.ephemeris import satellites as sats
from astrodeck.config import ConfigStore

# 2026-09-10 04:00:00 UTC
WHEN = 1_789_012_800.0

ISS_ROW = {
    "name": "ISS (ZARYA)", "norad_id": 25544,
    "line1": "1 25544U 98067A   26253.14350205  .00005262  00000+0  "
             "10337-3 0  9999",
    "line2": "2 25544  51.6301 239.6211 0004991 124.3002 235.8459 "
             "15.49065630584920",
}

#: The synthetic fixture site. Nothing here is anybody's observatory (#19).
SITE_LAT = 40.0
SITE_LON = -99.0

#: The pass everything else is measured against: 2026-09-11 01:17:15 UTC,
#: rising in the south-west, culminating 55 degrees up in the south-east,
#: setting in the north-east, sunlit end to end.
BIG_PASS_START = 1_789_089_435.3
BIG_PASS_PEAK = 1_789_089_632.2
BIG_PASS_PEAK_ALT = 54.95

#: The one pass of the four that is visible from the instant it clears the
#: horizon to the instant it sets: 2026-09-12 02:06:49 UTC, 31.9 degrees up,
#: sunlit throughout, under a sky that is already dark. Its two windows agree.
WHOLE_PASS_START = 1_789_178_809.1

#: A low one in the north-west, 23.0 degrees up, that a drawn tree line removes.
LOW_PASS_START = 1_789_095_287.2
LOW_PASS_PEAK = 1_789_095_452.6
LOW_PASS_PEAK_ALT = 22.95

#: Passes are identified across horizon changes by their CULMINATION, not their
#: rise. Lowering the floor makes the same pass start earlier -- the low one
#: below rises 142 seconds sooner over a cleared polyline than over a 10-degree
#: floor -- while the moment of maximum altitude barely moves at all.
_PEAK_MATCH_S = 90.0


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A sited rig with the ISS element set cached, and nothing else."""
    import astrodeck.config as config_mod

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    cfg.site = cfg.site.model_copy(update={
        "name": "Test", "latitude": SITE_LAT, "longitude": SITE_LON,
        "elevation_m": 20.0, "is_default": False, "horizon_min_deg": 10.0})
    cfg.safety = cfg.safety.model_copy(update={"horizon": None})
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(el, "ELEMENTS_DIR", tmp_path / "ephemeris")
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    monkeypatch.setattr(el, "COMET_FILE", tmp_path / "ephemeris" / "comets.json")
    el.write_envelope(el.SATELLITE_FILE, "test", [ISS_ROW], WHEN)
    return store


def _sky_runs(hours=48.0):
    """Every above-the-horizon run in the window, with its sunlit and dark
    sample counts. The RAW truth the reporting rule is graded against -- built
    from the same primitives ``find_passes`` uses, so the test knows which
    passes exist before asking which ones were reported."""
    sky = P._Sky(WHEN, hours)
    sat = sats._satrec(ISS_ROW)
    r = sats.propagate_teme(sat, sky.t)
    itrs = sats.teme_to_itrs_km(r, sky.t)
    gcrs = sats.teme_to_gcrs_km(r, sky.t)
    alt, az, _rng = sats.altaz_from_itrs(itrs, sky.site_km, sky.lat, sky.lon)
    lit = sats.is_sunlit(sats.shadow_state(gcrs, sky.sun_km))
    up = alt > P._floor_lookup(sky.table, az)
    out = []
    for first, last in P._runs(up):
        out.append({
            "start": float(sky.unix[first]),
            "peak_alt": float(alt[first:last + 1].max()),
            "lit": int(lit[first:last + 1].sum()),
            "samples": last - first + 1,
            "visible": int((lit & sky.dark)[first:last + 1].sum()),
        })
    return out


# ==================================================== the known pass, pinned

def test_a_seeded_element_set_and_a_fixed_clock_give_the_known_iss_pass(rig):
    """The regression anchor. If any of the frame chain, the horizon rule, the
    refinement or the reporting order moves, this is the number that says so."""
    out = P.find_passes(48.0, 0.0, None, WHEN)
    assert out["horizon_source"] == "horizon_min_deg"
    assert len(out["passes"]) == 4, (
        f"expected the four visible passes; got "
        f"{[p['start_unix'] for p in out['passes']]}")

    big = next(p for p in out["passes"]
               if abs(p["start_unix"] - BIG_PASS_START) < 2.0)
    assert big["max_alt_deg"] == pytest.approx(BIG_PASS_PEAK_ALT, abs=0.05)
    assert big["norad_id"] == 25544 and big["name"] == "ISS (ZARYA)"
    assert big["start_az"] == pytest.approx(217.1, abs=1.0)
    assert big["peak_az"] == pytest.approx(138.9, abs=1.0)
    assert big["end_az"] == pytest.approx(60.4, abs=1.0)
    assert big["duration_s"] == pytest.approx(395.0, abs=3.0)
    assert big["sunlit_fraction"] == 1.0
    assert big["enters_shadow_unix"] is None
    assert big["elements_age_days"] == pytest.approx(0.023, abs=0.005)
    # Reported in time order, because a list of passes is a plan for an evening.
    starts = [p["start_unix"] for p in out["passes"]]
    assert starts == sorted(starts)


def test_the_reported_boundaries_really_are_the_horizon_crossings(rig):
    """Self-consistency against the propagator, not against a stored number: at
    ``start_unix`` and ``end_unix`` the satellite must be AT the horizon floor,
    and at ``peak_unix`` at ``max_alt_deg``. The bisection is refined to one
    second, and the station climbs about 0.3 degrees a second at its fastest, so
    the bar is a third of a degree."""
    out = P.find_passes(24.0, 0.0, None, WHEN)
    sky = P._Sky(WHEN, 24.0)
    sat = sats._satrec(ISS_ROW)
    for p in out["passes"]:
        alt, az, _r, _l = sky.sample(
            sat, [p["start_unix"], p["peak_unix"], p["end_unix"]])
        floor_start = P.floor_at(sky.poly, sky.floor_deg, float(az[0]))
        floor_end = P.floor_at(sky.poly, sky.floor_deg, float(az[2]))
        assert abs(float(alt[0]) - floor_start) < 0.35, (
            f"start_unix is not on the horizon: alt {alt[0]:.3f} vs floor "
            f"{floor_start:.3f}")
        assert abs(float(alt[2]) - floor_end) < 0.35
        assert float(alt[1]) == pytest.approx(p["max_alt_deg"], abs=0.02)
        assert float(az[1]) == pytest.approx(p["peak_az"], abs=0.2)


# ======================================== the visible window, beside the pass

def test_the_visible_window_is_reported_beside_the_horizon_crossings(rig):
    """TWO WINDOWS, AND A CARD THAT PRINTS THE WRONG ONE SENDS SOMEBODY OUT
    EARLY.

    ``start_unix``/``end_unix`` are the horizon crossings: when the station
    clears the tree line and when it drops back behind it. That is the pass.
    ``visible_start_unix``/``visible_end_unix`` are when there is something to
    SEE -- up AND sunlit AND the observer in the dark.

    Graded against the propagator rather than against a stored number: inside
    the reported visible window all three conditions hold, and one second
    outside it at least one of them does not."""
    out = P.find_passes(48.0, 0.0, None, WHEN)
    sky = P._Sky(WHEN, 48.0)
    sat = sats._satrec(ISS_ROW)
    narrowed = 0
    for p in out["passes"]:
        vs, ve = p["visible_start_unix"], p["visible_end_unix"]
        assert p["start_unix"] - 1.0 <= vs <= ve <= p["end_unix"] + 1.0, (
            f"the visible window is not inside the pass: {p}")
        inside = sky.visible_sign(sat, [vs + 2.0, ve - 2.0])
        assert list(inside) == [1.0, 1.0], (
            f"the reported visible window contains an instant with nothing to "
            f"see: {p}")
        if vs - p["start_unix"] > 2.0:
            narrowed += 1
            before = sky.visible_sign(sat, [vs - 2.0])
            assert float(before[0]) == -1.0, (
                "visible_start is not a boundary: it was already visible two "
                "seconds earlier")
        if p["end_unix"] - ve > 2.0:
            narrowed += 1
            after = sky.visible_sign(sat, [ve + 2.0])
            assert float(after[0]) == -1.0
    assert narrowed >= 1, (
        "no pass in this window was narrowed by the sunlight/darkness rule, so "
        "this test would pass on start/end copied straight across")


def test_a_pass_visible_end_to_end_has_the_two_windows_agree(rig):
    """The 2026-09-12 02:06 UTC pass is sunlit throughout AND happens under a sky
    that is already dark, so its visible window IS its pass -- to within the one
    second both pairs are bisected to. This is the half that stops the new
    fields being an unrelated number that merely sits inside the pass."""
    out = P.find_passes(48.0, 0.0, None, WHEN)
    whole = next(p for p in out["passes"]
                 if abs(p["start_unix"] - WHOLE_PASS_START) < 2.0)
    assert whole["sunlit_fraction"] == 1.0
    assert whole["enters_shadow_unix"] is None
    assert whole["visible_start_unix"] == pytest.approx(whole["start_unix"],
                                                        abs=1.0)
    assert whole["visible_end_unix"] == pytest.approx(whole["end_unix"], abs=1.0)


def test_the_big_pass_rises_before_the_sky_is_dark_and_says_so(rig):
    """WHY THE TWO WINDOWS ARE BOTH ON THE ROW, in one number.

    The 55-degree pass is the best of the night and it clears the horizon 164
    seconds before civil twilight ends here. ``start_unix`` is when it rises;
    there is nothing to look at yet. A card that printed the rise as the time to
    be outside would be minutes early on the only pass anybody cares about."""
    from astrodeck.catalog.coords import sun_altaz

    big = next(p for p in P.find_passes(48.0, 0.0, None, WHEN)["passes"]
               if abs(p["start_unix"] - BIG_PASS_START) < 2.0)
    assert big["sunlit_fraction"] == 1.0, "not the shadow, then"
    assert big["visible_start_unix"] - big["start_unix"] > 30.0, (
        "the twilight-limited start was not reported")
    assert sun_altaz(SITE_LAT, SITE_LON, big["start_unix"])[0] > -6.0, (
        "the sky was already dark at the rise; this pass is not the example")
    assert sun_altaz(SITE_LAT, SITE_LON, big["visible_start_unix"])[0] == \
        pytest.approx(-6.0, abs=0.05), (
        "visible_start is not the moment the observer's sky reached civil "
        "twilight")


def test_a_pass_that_fades_out_ends_visible_where_it_enters_the_shadow(rig):
    """The third condition doing the narrowing, and a cross-check between two
    numbers bisected independently: a pass that goes into the Earth's shadow and
    does not come back out stops being visible exactly there, so
    ``visible_end_unix`` has to equal ``enters_shadow_unix``. It does not
    "fade" -- the 23.0-degree pass on the 11th is visible for 231 seconds of its
    331 and then there is nothing in the eyepiece."""
    fading = [p for p in P.find_passes(48.0, 0.0, None, WHEN)["passes"]
              if p["enters_shadow_unix"] is not None
              and p["leaves_shadow_unix"] is None]
    assert fading, "the fixture changed: no pass fades out in this window"
    for p in fading:
        assert p["visible_end_unix"] == pytest.approx(p["enters_shadow_unix"],
                                                      abs=1.0), (
            f"the pass stayed 'visible' after it entered the shadow: {p}")
        assert p["end_unix"] - p["visible_end_unix"] > 10.0, (
            "it entered the shadow at the moment it set, which makes this a "
            "weaker test than it reads as")


# ============================================ (2) a pass in shadow is not one

def test_a_pass_wholly_in_the_earths_shadow_is_not_reported(rig):
    """SEVEN of the twelve runs in this window are eclipsed end to end,
    including two that go more than 65 degrees up. They are real passes and
    they are not sightings: a satellite is a mirror, and in the Earth's shadow
    there is nothing to reflect."""
    runs = _sky_runs(48.0)
    eclipsed = [r for r in runs if r["lit"] == 0]
    assert len(eclipsed) == 7, f"the fixture changed: {runs}"
    assert any(r["peak_alt"] > 65.0 for r in eclipsed), (
        "the high eclipsed pass is what makes this test worth having")

    reported = {round(p["start_unix"]) for p in
                P.find_passes(48.0, 0.0, None, WHEN)["passes"]}
    for r in eclipsed:
        assert not any(abs(s - r["start"]) < 60.0 for s in reported), (
            f"a pass that was in the Earth's shadow for all "
            f"{r['samples']} samples was reported anyway "
            f"(peak {r['peak_alt']:.1f} deg)")


def test_a_sunlit_pass_in_daylight_is_not_reported_either(rig):
    """The third condition, on its own. The 2026-09-12 00:30 UTC pass is lit for
    all 36 of its samples and still invisible, because the Sun is above -6
    degrees here -- the observer is the one who is not in the dark."""
    runs = _sky_runs(48.0)
    daylit = [r for r in runs if r["lit"] == r["samples"] and r["visible"] == 0]
    assert len(daylit) == 1, f"the fixture changed: {runs}"
    reported = {round(p["start_unix"]) for p in
                P.find_passes(48.0, 0.0, None, WHEN)["passes"]}
    assert not any(abs(s - daylit[0]["start"]) < 60.0 for s in reported)


def test_civil_twilight_is_the_threshold_and_not_the_imaging_dark_window(rig):
    """-6 degrees, deliberately, and deliberately not ``coords.dark_window``.

    The best passes happen IN twilight; waiting for astronomical dark would hide
    most of them. And ``dark_window`` is driven by ``safety.twilight_deg``, an
    operator preference about when to start imaging -- a rig set to a
    conservative -18 would stop reporting passes anyone standing outside could
    see. So changing that setting must not change this answer."""
    from astrodeck.config import config_store

    assert P.PASS_SUN_ALT_MAX_DEG == -6.0
    before = P.find_passes(48.0, 0.0, None, WHEN)["passes"]
    cfg = config_store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"twilight_deg": -18.0})
    after = P.find_passes(48.0, 0.0, None, WHEN)["passes"]
    assert [p["start_unix"] for p in before] == [p["start_unix"] for p in after]


# ================================================ (1) the drawn horizon rules

_TREELINE = [(0.0, 25.0), (90.0, 25.0), (180.0, 25.0), (270.0, 25.0)]
_CLEARED = [(0.0, 0.0), (90.0, 0.0), (180.0, 0.0), (270.0, 0.0)]


def _with_horizon(poly):
    from astrodeck.config import config_store

    cfg = config_store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"horizon": poly})


def test_a_pass_below_a_drawn_horizon_point_is_not_reported(rig):
    """The rig's horizon is a polyline an operator drew around their own tree
    line, and it is interpolated with ``sequence.schedule.interp_wrap`` -- the
    SAME function the sequence engine's obstruction rule uses. If the engine
    would refuse to slew there, this must not promise a pass there."""
    _with_horizon(_TREELINE)
    out = P.find_passes(48.0, 0.0, None, WHEN)
    assert out["horizon_source"] == "site_polyline"
    peaks = [p["peak_unix"] for p in out["passes"]]
    assert not any(abs(t - LOW_PASS_PEAK) < _PEAK_MATCH_S for t in peaks), (
        f"a {LOW_PASS_PEAK_ALT} degree pass was reported over a 25 degree tree "
        f"line: {peaks}")
    # The one that goes 55 degrees up clears the trees and stays.
    assert any(abs(t - BIG_PASS_PEAK) < _PEAK_MATCH_S for t in peaks)


def test_the_same_pass_over_a_cleared_polyline_is_reported(rig):
    """The other half, so the test above cannot pass by the pass having been
    lost for some other reason. Same instant, same elements, same site -- only
    the drawn line changes."""
    _with_horizon(_CLEARED)
    out = P.find_passes(48.0, 0.0, None, WHEN)
    assert out["horizon_source"] == "site_polyline"
    peaks = [p["peak_unix"] for p in out["passes"]]
    assert any(abs(t - LOW_PASS_PEAK) < _PEAK_MATCH_S for t in peaks), (
        f"the low pass did not come back over a cleared horizon: {peaks}")
    low = next(p for p in out["passes"]
               if abs(p["peak_unix"] - LOW_PASS_PEAK) < _PEAK_MATCH_S)
    assert low["max_alt_deg"] == pytest.approx(LOW_PASS_PEAK_ALT, abs=0.1)
    # It rises EARLIER over a cleared line than over the 10-degree floor, which
    # is the horizon rule doing its job rather than a coincidence of naming.
    assert low["start_unix"] < LOW_PASS_START - 60.0


def test_horizon_source_names_the_line_that_was_used(rig):
    """"No pass tonight" and "no pass above your tree line tonight" are
    different answers, and an empty list cannot tell them apart."""
    _with_horizon(None)
    assert P.find_passes(1.0, 0.0, None, WHEN)["horizon_source"] \
        == "horizon_min_deg"
    _with_horizon(_TREELINE)
    assert P.find_passes(1.0, 0.0, None, WHEN)["horizon_source"] \
        == "site_polyline"

    from astrodeck.config import config_store

    cfg = config_store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"horizon": None})
    cfg.site = cfg.site.model_copy(update={"horizon_min_deg": 0.0})
    assert P.find_passes(1.0, 0.0, None, WHEN)["horizon_source"] == "none"


def test_the_floor_is_the_engines_own_interp_wrap(rig):
    """Not a reimplementation of it. Two functions that agree today drift apart
    by the end of the year, and the failure would be silent in both
    directions."""
    from astrodeck.sequence.schedule import interp_wrap

    poly = [(0.0, 5.0), (90.0, 30.0), (180.0, 5.0), (270.0, 30.0)]
    for az in (0.0, 45.0, 123.4, 269.9, 359.9):
        assert P.floor_at(poly, 0.0, az) == pytest.approx(
            float(interp_wrap(poly, az)))


def test_the_coarse_lookup_table_matches_the_exact_function(rig):
    """The scan uses a 0.1-degree table because ``interp_wrap`` sorts its
    control points on every call and the scan asks 8,640 times per satellite.
    The table therefore has to BE that function, sampled -- and every reported
    boundary is bisected against the exact one anyway."""
    poly = [(0.0, 5.0), (90.0, 30.0), (180.0, 5.0), (270.0, 30.0)]
    table = P._floor_table(poly, 0.0)
    az = np.linspace(0.0, 359.99, 500)
    approx = P._floor_lookup(table, az)
    exact = np.array([P.floor_at(poly, 0.0, float(a)) for a in az])
    assert float(np.max(np.abs(approx - exact))) < 0.02


# ============================================================ query handling

def test_ids_narrows_the_search_and_an_unknown_id_finds_nothing(rig):
    """AN ID THAT MATCHES NOTHING IS A FACT ABOUT THE FILTER, NOT ABOUT THE RIG.

    This used to answer "No satellite elements have been downloaded yet" and
    ``horizon_source: "none"`` -- on a rig with a fresh element cache and a
    10-degree floor configured. Every word of it was false, and it pointed the
    operator at Sky settings to fix a cache that was already there.

    The cache state is on the payload either way, so the two cases can be told
    apart without reading the sentence."""
    assert P.find_passes(24.0, 0.0, [25544], WHEN)["passes"]

    empty = P.find_passes(24.0, 0.0, [99999], WHEN)
    assert empty["passes"] == []
    assert empty["elements"]["present"] is True and empty["elements"]["count"] == 1
    assert empty["horizon_source"] == "horizon_min_deg", (
        "the drawn horizon is a fact about the rig, not about this search")
    note = empty["notes"][0]
    assert "99999" in note, (
        f"the note must name the id that matched nothing: {note}")
    assert "have been downloaded yet" not in note, (
        "a rig with a cached element set was told its cache was empty")


def test_an_empty_cache_still_says_nothing_has_been_downloaded(rig, monkeypatch):
    """The other half of the branch above: when the cache really is missing,
    the sentence is still the one that sends the operator to the refresh."""
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        el.SATELLITE_FILE.parent / "absent.json")
    out = P.find_passes(24.0, 0.0, [25544], WHEN)
    assert out["passes"] == []
    assert out["elements"]["present"] is False
    assert "have been downloaded yet" in out["notes"][0]
    assert out["horizon_source"] == "horizon_min_deg", (
        "a rig with a horizon floor has one whether or not it has elements")


def test_min_alt_deg_drops_the_low_ones(rig):
    high = P.find_passes(48.0, 50.0, None, WHEN)["passes"]
    assert high and all(p["max_alt_deg"] >= 50.0 for p in high)
    assert len(high) < len(P.find_passes(48.0, 0.0, None, WHEN)["passes"])


def test_hours_is_clamped_to_the_documented_maximum(rig):
    """72 hours, because the search is thousands of frame transforms per
    satellite and an unbounded ``hours`` is a denial of service with a query
    string."""
    assert P.MAX_HOURS == 72.0
    long = P.find_passes(1000.0, 0.0, [25544], WHEN)
    span = max(p["end_unix"] for p in long["passes"]) - WHEN
    assert span <= P.MAX_HOURS * 3600.0 + 60.0


def test_no_elements_is_a_sentence_not_an_empty_list(tmp_path, monkeypatch):
    import astrodeck.config as config_mod

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    cfg.site = cfg.site.model_copy(update={
        "latitude": SITE_LAT, "longitude": SITE_LON, "is_default": False})
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    out = P.find_passes(24.0, 0.0, None, WHEN)
    assert out["passes"] == []
    # The rig's own floor, reported whether or not there are elements to search:
    # this site carries the default 15-degree minimum altitude, and answering
    # "none" here told the operator their horizon was unconfigured.
    assert out["horizon_source"] == "horizon_min_deg"
    assert "will not guess" in out["notes"][0]


def test_stale_elements_are_reported_with_the_passes_not_instead_of_them(rig):
    """An old element set still predicts passes -- roughly. So the passes come
    back AND the note comes with them, rather than the answer being withheld:
    a minute of error on a pass time is worth knowing about and is not worth
    refusing over."""
    late = WHEN + (el.SATELLITE_STALE_DAYS + 4.0) * 86400.0
    out = P.find_passes(24.0, 0.0, None, late)
    assert out["elements"]["stale"] is True
    assert any("days old" in n and "kilometre a day" in n for n in out["notes"])
