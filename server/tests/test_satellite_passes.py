"""Tonight's visible passes: the three conditions, and what each one removes.

A SEEDED ELEMENT SET AND A FIXED CLOCK. Every number below comes from one
pinned TLE (NORAD 25544, epoch 2026-09-10 03:26:38 UTC) propagated from one
pinned instant, so "the ISS goes over at 02:49" is a claim about this code
rather than about where the station happens to be while the suite runs. The
propagator itself is graded against the AIAA corpus in
``test_satellite_ephemeris.py``; this file is about the three filters on top of
it.

IN 48 HOURS FROM THAT INSTANT, at 37.5 N / 122.3 W, the station is above a
10-degree horizon TEN times and VISIBLE four times. The six it loses are the
whole point of the file:

    2026-09-10 08:32  peak 14.4   0/23 samples sunlit   -> in the Earth's shadow
    2026-09-10 10:08  peak 81.0   0/40 samples sunlit   -> in the Earth's shadow
    2026-09-11 07:45  peak 11.2   0/13 samples sunlit   -> in the Earth's shadow
    2026-09-11 09:20  peak 45.3   0/39 samples sunlit   -> in the Earth's shadow
    2026-09-11 10:58  peak 16.1   0/26 samples sunlit   -> in the Earth's shadow
    2026-09-12 02:02  peak 43.5  38/38 samples sunlit   -> the SUN was up here

The 81-degree pass on the 10th is the one that makes the case: it crosses
almost overhead, it is unmistakably "up", and there is nothing to see because
the station is in the Earth's shadow the whole way. A predictor that reported it
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

#: The pass everything else is measured against: 2026-09-11 02:49:41 UTC,
#: rising in the south-west, culminating 83 degrees up, setting in the
#: north-east, sunlit end to end.
BIG_PASS_START = 1_789_094_981.6
BIG_PASS_PEAK = 1_789_095_182.6
BIG_PASS_PEAK_ALT = 83.23

#: A low one in the north-west, 15.8 degrees up, that a drawn tree line removes.
LOW_PASS_START = 1_789_100_883.4
LOW_PASS_PEAK = 1_789_101_013.3
LOW_PASS_PEAK_ALT = 15.84

#: Passes are identified across horizon changes by their CULMINATION, not their
#: rise. Lowering the floor makes the same pass start earlier -- the low one
#: below rises 161 seconds sooner over a cleared polyline than over a 10-degree
#: floor -- while the moment of maximum altitude barely moves at all.
_PEAK_MATCH_S = 90.0


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A sited rig with the ISS element set cached, and nothing else."""
    import astrodeck.config as config_mod

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    cfg.site = cfg.site.model_copy(update={
        "name": "Test", "latitude": 37.5, "longitude": -122.3,
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
    assert big["start_az"] == pytest.approx(226.5, abs=1.0)
    assert big["peak_az"] == pytest.approx(139.4, abs=1.0)
    assert big["end_az"] == pytest.approx(51.3, abs=1.0)
    assert big["duration_s"] == pytest.approx(403.0, abs=3.0)
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


# ============================================ (2) a pass in shadow is not one

def test_a_pass_wholly_in_the_earths_shadow_is_not_reported(rig):
    """FIVE of the ten runs in this window are eclipsed end to end, including
    one that goes 81 degrees up. They are real passes and they are not
    sightings: a satellite is a mirror, and in the Earth's shadow there is
    nothing to reflect."""
    runs = _sky_runs(48.0)
    eclipsed = [r for r in runs if r["lit"] == 0]
    assert len(eclipsed) == 5, f"the fixture changed: {runs}"
    assert any(r["peak_alt"] > 80.0 for r in eclipsed), (
        "the near-overhead eclipsed pass is what makes this test worth having")

    reported = {round(p["start_unix"]) for p in
                P.find_passes(48.0, 0.0, None, WHEN)["passes"]}
    for r in eclipsed:
        assert not any(abs(s - r["start"]) < 60.0 for s in reported), (
            f"a pass that was in the Earth's shadow for all "
            f"{r['samples']} samples was reported anyway "
            f"(peak {r['peak_alt']:.1f} deg)")


def test_a_sunlit_pass_in_daylight_is_not_reported_either(rig):
    """The third condition, on its own. The 2026-09-12 02:02 UTC pass is lit for
    all 38 of its samples and still invisible, because the Sun is above -6
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
    # The one that goes 83 degrees up clears the trees and stays.
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
    assert P.find_passes(24.0, 0.0, [25544], WHEN)["passes"]
    empty = P.find_passes(24.0, 0.0, [99999], WHEN)
    assert empty["passes"] == []
    assert any("have been downloaded yet" in n for n in empty["notes"]), (
        "an id that matches no cached element set must say so rather than "
        "return an empty list that reads as 'no passes tonight'")


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
        "latitude": 37.5, "longitude": -122.3, "is_default": False})
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    out = P.find_passes(24.0, 0.0, None, WHEN)
    assert out["passes"] == []
    assert out["horizon_source"] == "none"
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
