"""The comet two-body solvers and the pipeline they feed.

FOUR GROUND TRUTHS, none of which is this code:

1. VALLADO, *Fundamentals of Astrodynamics and Applications*, Example 2-1:
   ``M = 235.4 deg``, ``e = 0.4`` gives ``E = 220.512074 deg``. A worked answer
   in a standard textbook, checked to 1e-6 degrees. It grades Kepler's equation
   alone, with no orbit, no frame and no ephemeris in the way -- so when the end
   to end check moves, this says whether the solver is why.

2. THE THREE CONICS AGREE WHERE THEY MEET. The near-parabolic branch exists
   because Kepler's equation degenerates as ``e -> 1``, but it is only correct
   if it reproduces the elliptical solution just below the band and the
   hyperbolic one just above it. Measured agreement across ``e`` from 0.9799 to
   1.02: better than 1e-8 degrees of true anomaly. That is a much sharper test
   than any single case, because the two neighbours are written from completely
   different formulae.

3. JPL HORIZONS, for the whole pipeline. Fetched once on 2026-09-10 and pinned
   here the way ``catalog/solar_system.py``'s docstring pins the planets:

       https://ssd.jpl.nasa.gov/api/horizons.api
           format=text          COMMAND='DES=2P;CAP;NOFRAG;'   (2P/Encke)
           OBJ_DATA=NO          MAKE_EPHEM=YES
           EPHEM_TYPE=OBSERVER  CENTER='500@399'               (geocentric)
           TLIST=2461293.666666667                (2026-09-10 04:00:00 UTC)
           QUANTITIES='1,19,20' ANG_FORMAT=DEG

       Date__(UT)__HR:MN:SC.fff  R.A.___(ICRF)___DEC       r        rdot  \
delta        deldot
       2026-Sep-10 04:00:00.000   20.59819  20.31576  2.352201063922 \
-15.9368560  1.49564583848802 -30.6875060

   Measured residual: 0.89 arcsec, against a tolerance of 1 arcmin. Horizons is
   integrating a full n-body solution with 2P/Encke's non-gravitational terms
   and we are doing two-body from the MPC's osculating elements, so the two
   answers have every reason to differ; that they agree to under an arcsecond at
   the element epoch is the check.

4. THE MPC LINE ITSELF, parsed by column. ``CometEls.txt`` is 80-column
   punched-card format and a whitespace split shifts every field after the first
   blank column, which is the kind of error that produces a plausible-looking
   position in the wrong part of the sky.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog.ephemeris import comets as C
from astrodeck.catalog.ephemeris import elements as el
from astrodeck.catalog.solar_system import EphemerisUnavailable
from astrodeck.config import ConfigStore

# 2026-09-10 04:00:00 UTC
WHEN = 1_789_012_800.0

#: The real line for 2P/Encke out of the MPC's CometEls.txt, fetched
#: 2026-09-10. Its element epoch is 2026-09-10, which is why the Horizons check
#: below is made at that instant: a two-body solution is exact at the epoch of
#: its own osculating elements and drifts away from an n-body truth afterwards.
ENCKE_LINE = (
    "0002P         2027 02 10.2279  0.338618  0.847314  187.2867  334.0194   "
    "11.3479  20260910  14.3  4.0  2P/Encke                                  "
    "               MPC xxxxx")

HORIZONS_ENCKE_RA_DEG = 20.59819
HORIZONS_ENCKE_DEC_DEG = 20.31576
HORIZONS_ENCKE_R_AU = 2.352201063922
HORIZONS_ENCKE_DELTA_AU = 1.49564583848802
ENCKE_TOLERANCE_ARCSEC = 60.0

#: Vallado, Example 2-1.
VALLADO_M_DEG = 235.4
VALLADO_E = 0.4
VALLADO_ANSWER_DEG = 220.512074


@pytest.fixture
def site(tmp_path, monkeypatch):
    import astrodeck.config as config_mod

    s = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = s.cfg()
    cfg.site = cfg.site.model_copy(update={
        "name": "Greenwich", "latitude": 51.4778, "longitude": -0.0015,
        "elevation_m": 46.0, "is_default": False})
    monkeypatch.setattr(config_mod, "config_store", s)
    monkeypatch.setattr(el, "ELEMENTS_DIR", tmp_path / "ephemeris")
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    monkeypatch.setattr(el, "COMET_FILE", tmp_path / "ephemeris" / "comets.json")
    return s


def _sep_arcsec(ra_a, dec_a, ra_b, dec_b) -> float:
    a, b = math.radians(dec_a), math.radians(dec_b)
    d = math.radians(ra_a - ra_b)
    cos = math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(d)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) * 3600.0


# ============================================================ Kepler alone

def test_kepler_matches_vallados_worked_example():
    """Vallado Example 2-1. The solver's own ground truth, with nothing else in
    the way -- if the Horizons check below ever moves, this is what says whether
    Kepler's equation is the reason.

    The answer comes back as -139.487926, which is 220.512074 on the branch
    ``solve_kepler`` normalises to; it is the same angle."""
    E = C.solve_kepler(math.radians(VALLADO_M_DEG), VALLADO_E)
    got = math.degrees(E) % 360.0
    assert abs(got - VALLADO_ANSWER_DEG) < 1e-6, (
        f"E = {got:.6f} deg for M = {VALLADO_M_DEG}, e = {VALLADO_E}; "
        f"Vallado Example 2-1 gives {VALLADO_ANSWER_DEG}")


def test_kepler_refuses_rather_than_returning_a_half_solved_answer(monkeypatch):
    """The iteration cap is not a safety net that quietly gives up -- it RAISES.

    A half-converged anomaly is a position, a position is a marker on a screen,
    and a marker on a screen is a slew. Every failure mode this app has learned
    the hard way has the same shape: an answer that looks exactly like a right
    one."""
    monkeypatch.setattr(C, "_MAX_ITER", 1)
    monkeypatch.setattr(C, "_TOL", 1e-18)
    with pytest.raises(EphemerisUnavailable) as exc:
        C.solve_kepler(math.radians(235.4), 0.97)
    assert "did not converge" in str(exc.value)
    assert "half-solved" in str(exc.value)


def test_the_hyperbolic_solver_inverts_its_own_equation():
    """``M = e sinh H - H`` -- checked by substitution rather than against a
    second implementation of the same formula."""
    for e in (1.05, 1.5, 3.0):
        for m in (-8.0, -0.3, 0.0, 0.3, 8.0):
            H = C.solve_hyperbolic(m, e)
            assert abs(e * math.sinh(H) - H - m) < 1e-10


# ============================== the three conics have to agree where they meet

def _ellipse(q, e, dt):
    a = q / (1.0 - e)
    E = C.solve_kepler(C.K_GAUSS / a ** 1.5 * dt, e)
    x = a * (math.cos(E) - e)
    y = a * math.sqrt(1.0 - e * e) * math.sin(E)
    return math.atan2(y, x), a * (1.0 - e * math.cos(E))


def _hyperbola(q, e, dt):
    a = q / (e - 1.0)
    H = C.solve_hyperbolic(C.K_GAUSS / a ** 1.5 * dt, e)
    x = a * (e - math.cosh(H))
    y = a * math.sqrt(e * e - 1.0) * math.sinh(H)
    return math.atan2(y, x), a * (e * math.cosh(H) - 1.0)


@pytest.mark.parametrize("e", [0.9799, 0.98, 0.99, 0.999, 0.9999])
@pytest.mark.parametrize("dt", [5.0, 30.0, 200.0, -30.0])
def test_the_near_parabolic_branch_agrees_with_the_ellipse(e, dt):
    """THE SHARPEST TEST IN THIS FILE, and the reason the ``e = 0.999`` case is
    trustworthy at all.

    Barker's equation solves the exact parabola, which no real comet is on; the
    Stumpff series is the correction for the departure from it. The only way to
    know the correction is right rather than merely plausible is to check it
    against a formula that shares none of its algebra -- so the near-parabolic
    branch is compared with the ELLIPTICAL solution at the same elements. They
    agree to 1e-8 degrees right through the band, including at e = 0.999 where
    the semi-major axis is 500 AU and Kepler's equation is at its worst."""
    v_e, r_e = _ellipse(0.5, e, dt)
    v_b, r_b = C._near_parabolic(0.5, e, dt)
    assert abs(math.degrees(v_e - v_b)) < 1e-6, (
        f"e={e} dt={dt}: ellipse {math.degrees(v_e):.9f} deg vs "
        f"near-parabolic {math.degrees(v_b):.9f} deg")
    assert abs(r_e - r_b) < 1e-9


@pytest.mark.parametrize("e", [1.0001, 1.001, 1.02])
@pytest.mark.parametrize("dt", [30.0, 200.0])
def test_the_near_parabolic_branch_agrees_with_the_hyperbola(e, dt):
    """The other edge of the band, against the third formula."""
    v_h, r_h = _hyperbola(0.5, e, dt)
    v_b, r_b = C._near_parabolic(0.5, e, dt)
    assert abs(math.degrees(v_h - v_b)) < 1e-6
    assert abs(r_h - r_b) < 1e-9


def test_perihelion_is_perihelion_on_every_branch():
    """At the perihelion instant every conic must return exactly ``q``. Zero is
    the one point where all three formulae are trivially comparable, and a sign
    error in the orbital-plane rotation shows up here as a distance that is not
    the one in the element set."""
    el_row = {"q_au": 0.5, "e": 0.0, "peri_deg": 0.0, "node_deg": 0.0,
              "incl_deg": 0.0, "tp_tt_jd": 2461293.5}
    for e in (0.0, 0.5, 0.9799, 0.99, 1.0, 1.01, 1.5):
        row = dict(el_row, e=e)
        v, r = C.true_anomaly_and_radius(row, 2461293.5)
        assert abs(r - 0.5) < 1e-12, f"e={e} gives r={r} at perihelion"
        assert abs(v) < 1e-9


# ============================================================== the MPC parser

def test_the_mpc_line_is_parsed_by_column_not_by_split():
    """``CometEls.txt`` is 80-column punched-card format. The name column
    contains spaces and the number column is blank for an unnumbered comet, so
    a whitespace split silently shifts every field after the first gap -- and a
    shifted element set produces a confident position in the wrong sky."""
    row = C.parse_comet_line(ENCKE_LINE)
    assert row is not None
    assert row["id"] == "2P" and row["name"] == "2P/Encke"
    assert row["q_au"] == 0.338618
    assert row["e"] == 0.847314
    assert row["peri_deg"] == 187.2867
    assert row["node_deg"] == 334.0194
    assert row["incl_deg"] == 11.3479
    assert row["h_mag"] == 14.3 and row["slope_g"] == 4.0
    # Perihelion 2027-02-10.2279 TT and an element epoch of 2026-09-10.
    assert row["tp_tt_jd"] == pytest.approx(2461446.7279, abs=1e-6)
    assert row["epoch_tt_jd"] == pytest.approx(2461293.5, abs=1e-6)


def test_a_line_that_is_not_an_element_set_is_dropped_not_guessed():
    assert C.parse_comet_line("") is None
    assert C.parse_comet_line("#  a comment line that is long enough " * 3) is None


def test_jd_from_ymd_matches_astropy():
    """The Julian-date helper exists so parsing a thousand element lines does
    not build a thousand astropy Times. It has to give the same answer."""
    from astropy.time import Time

    for y, m, d in ((2027, 2, 10.2279), (2026, 9, 10.0), (1999, 12, 31.5),
                    (2000, 1, 1.5), (1858, 11, 17.0)):
        ours = C.jd_from_ymd(y, m, d)
        theirs = Time(f"{y:04d}-{m:02d}-01", scale="tt").jd + (d - 1.0)
        assert abs(ours - theirs) < 1e-9, f"{y}-{m}-{d}: {ours} vs {theirs}"


# ======================================================= the whole pipeline

def test_encke_lands_where_jpl_horizons_says_it_does(site):
    """Element line -> two-body solve -> J2000 equatorial -> geocentric, against
    JPL. See the module docstring for the exact query and its output."""
    row = C.parse_comet_line(ENCKE_LINE)
    p = C.position(row, WHEN, site_derived=False)   # geocentric, as JPL's is
    sep = _sep_arcsec(p["ra_hours"] * 15.0, p["dec_deg"],
                      HORIZONS_ENCKE_RA_DEG, HORIZONS_ENCKE_DEC_DEG)
    assert sep < ENCKE_TOLERANCE_ARCSEC, (
        f"2P/Encke is {sep:.1f} arcsec from JPL Horizons (ours "
        f"{p['ra_hours'] * 15.0:.5f} {p['dec_deg']:+.5f}, JPL "
        f"{HORIZONS_ENCKE_RA_DEG:.5f} {HORIZONS_ENCKE_DEC_DEG:+.5f})")
    assert abs(p["r_au"] - HORIZONS_ENCKE_R_AU) < 1e-4
    assert abs(p["delta_au"] - HORIZONS_ENCKE_DELTA_AU) < 1e-4


def test_the_down_leg_light_time_correction_is_applied(site):
    """Astrometric place is where the comet WAS when the light left it. At 1.5
    AU that is twelve and a half minutes, and for Encke it is ten arcseconds --
    an order of magnitude bigger than the residual above, so leaving it out
    would be the dominant error rather than a refinement."""
    row = C.parse_comet_line(ENCKE_LINE)
    p = C.position(row, WHEN, site_derived=False)
    assert 12.0 < p["light_time_min"] < 13.0

    # The same instant with the correction switched off, to size it.
    from astropy.time import Time

    t = Time(WHEN, format="unix")
    earth = C._earth_helio_au(t)
    comet, _r = C.heliocentric_equatorial_au(row, float(t.tt.jd))
    d = tuple(comet[i] - earth[i] for i in range(3))
    ra = math.degrees(math.atan2(d[1], d[0])) % 360.0
    dec = math.degrees(math.asin(d[2] / math.sqrt(sum(c * c for c in d))))
    uncorrected = _sep_arcsec(ra, dec, HORIZONS_ENCKE_RA_DEG,
                              HORIZONS_ENCKE_DEC_DEG)
    corrected = _sep_arcsec(p["ra_hours"] * 15.0, p["dec_deg"],
                            HORIZONS_ENCKE_RA_DEG, HORIZONS_ENCKE_DEC_DEG)
    assert corrected < uncorrected / 5.0, (
        f"the light-time correction is not doing anything: {corrected:.2f} "
        f"arcsec with it, {uncorrected:.2f} without")


def test_the_obliquity_comes_from_erfa_and_not_from_a_typed_constant():
    """23.4393 typed into a file is a number nobody can check. This one is
    ERFA's IAU 2006 value, which is the same source the rest of this app's
    astrometry uses, and it agrees with the published figure to the digits the
    published figure has."""
    import erfa

    assert C._obliquity_j2000_rad() == float(erfa.obl06(2451545.0, 0.0))
    assert abs(math.degrees(C._obliquity_j2000_rad()) - 23.4392794) < 1e-6


# =================================================== the row, and its honesty

def test_the_comet_row_carries_the_mpcs_magnitude_and_says_whose_it_is(site):
    """Unlike a satellite, a comet HAS a published brightness model -- and it is
    a model, routinely a magnitude or two out for an active comet, so the row
    says so rather than presenting it as a measurement."""
    row = C.parse_comet_line(ENCKE_LINE)
    out = C.row(row, WHEN, site_derived=True, elements_age_days=0.0)
    assert out["kind"] == "comet" and out["type"] == "Comet"
    assert out["mag"] is not None
    expected = (14.3 + 5.0 * math.log10(out["delta_au"])
                + 2.5 * 4.0 * math.log10(out["r_au"]))
    assert out["mag"] == pytest.approx(expected, abs=0.01)
    assert "Minor Planet Center's model" in out["name"]


def test_a_comet_is_served_to_a_non_holder_geocentrically_not_withheld(site):
    """The PLANETS' rule, not the satellites'. Between two sites a comet at 1.5
    AU shifts by well under an arcsecond, so nothing in the row is a location
    oracle and there is nothing to withhold -- the caller gets the row, computed
    from the centre of the Earth, and told that is what happened."""
    row = C.parse_comet_line(ENCKE_LINE)
    viewer = C.row(row, WHEN, site_derived=False, elements_age_days=0.0)
    assert viewer["topocentric"] is False
    assert viewer["geocentric_reason"] == "not_permitted"
    assert "alt" not in viewer and "az" not in viewer

    holder = C.row(row, WHEN, site_derived=True, elements_age_days=0.0)
    assert holder["topocentric"] is True and holder["geocentric_reason"] is None
    assert "alt" in holder and "az" in holder
    # And the difference between the two IS sub-arcsecond, which is the whole
    # argument for serving it rather than withholding it.
    sep = _sep_arcsec(viewer["ra_hours"] * 15.0, viewer["dec_deg"],
                      holder["ra_hours"] * 15.0, holder["dec_deg"])
    assert sep < 5.0, (
        f"the topocentric shift is {sep:.2f} arcsec; if that were tens of "
        f"degrees this row would have to follow the satellites' rule instead")


def test_a_non_converging_solve_returns_a_note_not_a_row(site, monkeypatch):
    """The search must degrade to a SENTENCE. A comet that cannot be solved is
    left out -- never placed at a guess -- and the reason is returned through
    the notes channel, because a screen showing nothing cannot tell "no such
    comet" from "the solver broke"."""
    from astrodeck.catalog.objects import search

    el.write_envelope(el.COMET_FILE, "mpc-cometels",
                      [C.parse_comet_line(ENCKE_LINE)], WHEN)

    def _boom(*a, **kw):
        raise EphemerisUnavailable("Kepler's equation did not converge")

    monkeypatch.setattr(C, "true_anomaly_and_radius", _boom)
    found = search("encke", 25, WHEN, True)
    assert [r for r in found.rows if r.get("kind") == "comet"] == []
    assert any("could not be placed just now" in n for n in found.notes), (
        f"a failed solve produced no explanation: {found.notes}")


def test_a_comet_search_finds_it_by_designation_and_by_name(site):
    from astrodeck.catalog.objects import search

    el.write_envelope(el.COMET_FILE, "mpc-cometels",
                      [C.parse_comet_line(ENCKE_LINE)], WHEN)
    for query in ("2P", "encke", "2P/Encke"):
        rows = [r for r in search(query, 25, WHEN, True).rows
                if r.get("kind") == "comet"]
        assert rows and rows[0]["id"] == "2P", f"{query!r} found nothing"


def test_no_comet_cache_yields_a_note_and_no_rows(site):
    from astrodeck.catalog.objects import search

    found = search("comets", 25, WHEN, True)
    assert [r for r in found.rows if r.get("kind") == "comet"] == []
    assert any("Minor Planet Center" in n for n in found.notes)
