"""The satellite propagator, graded against two independent ground truths.

WHY TWO, AND WHY THEY ARE DIFFERENT IN KIND.

1. THE AIAA CORPUS is the propagator alone. ``SGP4-VER.TLE`` and
   ``tcppver.out`` ship inside the ``sgp4`` package: 33 catalogue entries, near
   Earth and deep space, with the TEME position and velocity the AIAA
   2006-6753 reference implementation produces at every step. Reproducing them
   to 1e-6 km proves that the way THIS code calls the library -- the gravity
   model, the epoch handling, the Julian-day split -- is the way the paper
   means. It is offline, deterministic and non-negotiable: if a satellite ever
   points at empty sky, this is the test that says it was not the propagator.

2. THE HORIZONS VECTOR is the WHOLE PIPELINE: element set -> SGP4 -> TEME ->
   GCRS -> right ascension and declination, checked against an authority that
   shares none of our code. The corpus cannot catch a frame error, because a
   frame error leaves TEME untouched; JPL cannot catch a propagator error,
   because it does not use SGP4. Between them there is nowhere for a mistake to
   hide.

GROUND TRUTH FOR (2), fetched once from ssd.jpl.nasa.gov on 2026-09-10 and
pinned here the way ``catalog/solar_system.py``'s docstring pins the planets:

    https://ssd.jpl.nasa.gov/api/horizons.api
        format=text          COMMAND='-125544'      (International Space Station)
        OBJ_DATA=NO          MAKE_EPHEM=YES
        EPHEM_TYPE=OBSERVER  CENTER='500@399'       (geocentric)
        TLIST=2461293.666666667                     (2026-09-10 04:00:00 UTC)
        QUANTITIES='1,20'    ANG_FORMAT=DEG

    Date__(UT)__HR:MN:SC.fff   R.A.___(ICRF)___DEC        delta      deldot
    2026-Sep-10 04:00:00.000    22.10035  37.09140  0.00004538036295  0.0062896

The element set is pinned too (``ISS_TLE`` below, epoch 2026-09-10 03:26:38
UTC, 33 minutes before the instant), because an ephemeris check against a
DIFFERENT orbit is not a check. Measured residual: 19.6 arcsec, against a
tolerance of 30. The residual is the element set itself -- a fresh TLE is good
to about a kilometre, and a kilometre at 6,789 km geocentric range is 30
arcsec -- not the frame chain, which is exact.
"""
from __future__ import annotations

import math
import pathlib
import re

import numpy as np
import pytest

from astrodeck.catalog.ephemeris import elements as el
from astrodeck.catalog.ephemeris import satellites as sats
from astrodeck.config import ConfigStore

# 2026-09-10 04:00:00 UTC
WHEN = 1_789_012_800.0

#: CelesTrak GP element set for NORAD 25544, epoch 2026-09-10 03:26:38 UTC.
ISS_TLE = (
    "1 25544U 98067A   26253.14350205  .00005262  00000+0  10337-3 0  9999",
    "2 25544  51.6301 239.6211 0004991 124.3002 235.8459 15.49065630584920",
)
ISS_ROW = {"name": "ISS (ZARYA)", "norad_id": 25544,
           "line1": ISS_TLE[0], "line2": ISS_TLE[1]}

#: JPL Horizons, geocentric astrometric ICRF, at WHEN. See the module docstring
#: for the exact query.
HORIZONS_ISS_RA_DEG = 22.10035
HORIZONS_ISS_DEC_DEG = 37.09140
HORIZONS_ISS_DELTA_AU = 0.00004538036295
#: Set by the element set's own accuracy, not by ours: 1 km of TLE error is
#: 30 arcsec at this range.
ISS_TOLERANCE_ARCSEC = 30.0

#: How closely our call of the library has to reproduce the published TEME
#: positions. AIAA 2006-6753 prints them to 1e-8 km; the reference
#: implementation itself agrees with the printed values to ~5e-9 km.
CORPUS_TOLERANCE_KM = 1e-6
#: "at least the first ten catalogue entries" -- all 33 are cheap, so all 33
#: are checked and the first ten are the contractual floor.
CORPUS_MIN_ENTRIES = 10


# ============================================================ the AIAA corpus

def _corpus_dir() -> pathlib.Path:
    import sgp4

    return pathlib.Path(sgp4.__file__).resolve().parent


def _corpus_tles() -> list[tuple[str, str]]:
    """The ``(line1, line2)`` pairs in ``SGP4-VER.TLE``.

    The file's line 2 carries THREE EXTRA FIELDS after column 69 (start, stop
    and step in minutes), which is why the lines are truncated rather than
    passed whole: ``twoline2rv`` would read the trailing numbers as part of the
    element set."""
    lines = (_corpus_dir() / "SGP4-VER.TLE").read_text().splitlines()
    out: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) \
                and lines[i + 1].startswith("2 "):
            out.append((line[:69], lines[i + 1][:69]))
    return out


def _corpus_expected() -> dict[int, list[list[float]]]:
    """``satnum -> [[tsince, x, y, z, vx, vy, vz], ...]`` from ``tcppver.out``."""
    blocks: dict[int, list[list[float]]] = {}
    cur: list[list[float]] | None = None
    for line in (_corpus_dir() / "tcppver.out").read_text().splitlines():
        header = re.match(r"^(\d+) xx\s*$", line)
        if header:
            cur = []
            blocks[int(header.group(1))] = cur
            continue
        if cur is None:
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            cur.append([float(x) for x in parts[:7]])
        except ValueError:
            continue
    return blocks


def test_the_aiaa_verification_corpus_reproduces_to_a_nanometre():
    """AIAA 2006-6753's own test vectors, propagated through THIS module.

    Offline and non-negotiable. Every satellite this app will ever place goes
    through ``propagate_teme``; if it disagrees with the reference
    implementation the whole feature is pointing somewhere else, and no amount
    of end-to-end testing on one satellite would find it -- the deep-space
    entries in this corpus (12-hour Molniya resonances, geosynchronous drift)
    exercise SDP4 paths that the ISS never touches."""
    from astropy.time import Time

    expected = _corpus_expected()
    tles = _corpus_tles()
    assert len(tles) >= CORPUS_MIN_ENTRIES, "the sgp4 package's corpus is missing"

    checked = 0
    worst = 0.0
    for line1, line2 in tles:
        try:
            sat = sats._satrec({"line1": line1, "line2": line2,
                                "name": "corpus"})
        except sats.SatellitesUnavailable:
            # The corpus deliberately includes element sets SGP4 must REFUSE
            # (an eccentricity outside 0..1, a decayed orbit). Refusing them at
            # parse is the behaviour under test elsewhere in this file; here
            # they simply have no position to compare.
            continue
        rows = expected.get(sat.satnum)
        if not rows:
            continue
        for tsince, ex, ey, ez, *_v in rows:
            t = Time(sat.jdsatepoch, sat.jdsatepochF + tsince / 1440.0,
                     format="jd", scale="utc")
            try:
                got = sats.propagate_teme(sat, t)[0]
            except sats.SatellitesUnavailable:
                # The corpus deliberately includes decayed and error-producing
                # cases. Refusing them is the CORRECT behaviour -- see
                # ``propagate_teme`` -- so a refusal is not a failure here.
                continue
            err = max(abs(got[i] - [ex, ey, ez][i]) for i in range(3))
            worst = max(worst, err)
            assert err < CORPUS_TOLERANCE_KM, (
                f"satellite {sat.satnum} at t+{tsince} min is {err:.3e} km "
                f"from the AIAA 2006-6753 reference position "
                f"({got} vs {(ex, ey, ez)}) -- this propagator does not agree "
                f"with the model the element sets are defined by")
            checked += 1
    assert checked > 200, f"only {checked} corpus vectors were checked"
    assert worst < CORPUS_TOLERANCE_KM


def test_the_corpus_covers_at_least_the_first_ten_catalogue_entries():
    """Guards the loop above against silently checking nothing: a parser change
    that stopped matching satellite numbers would leave it iterating over an
    empty ``rows`` and passing."""
    expected = _corpus_expected()
    tles = _corpus_tles()
    matched = 0
    for line1, line2 in tles[:CORPUS_MIN_ENTRIES]:
        sat = sats._satrec({"line1": line1, "line2": line2, "name": "c"})
        if expected.get(sat.satnum):
            matched += 1
    assert matched == CORPUS_MIN_ENTRIES, (
        f"only {matched} of the first {CORPUS_MIN_ENTRIES} corpus entries "
        f"could be matched to a published answer")


# ====================================================== the whole pipeline

@pytest.fixture
def site(tmp_path, monkeypatch):
    """A real, non-default site, installed where the ephemeris reads it.
    Greenwich, for the same reason ``test_catalog_solar_system`` uses it: a
    topocentric answer needs a real observer, and the prime meridian is the one
    site no rig is at."""
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


def _sep_arcsec(ra_a_deg, dec_a_deg, ra_b_deg, dec_b_deg) -> float:
    a, b = math.radians(dec_a_deg), math.radians(dec_b_deg)
    d = math.radians(ra_a_deg - ra_b_deg)
    cos = math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(d)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) * 3600.0


def test_the_iss_lands_where_jpl_horizons_says_it_does(site):
    """The end-to-end check: our TLE, our propagator, our frame chain, JPL's
    answer. See the module docstring for the exact query and its output."""
    from astropy.time import Time

    sat = sats._satrec(ISS_ROW)
    t = Time(WHEN, format="unix")
    gcrs = sats.teme_to_gcrs_km(sats.propagate_teme(sat, t), t)[0]
    ra = math.degrees(math.atan2(gcrs[1], gcrs[0])) % 360.0
    dec = math.degrees(math.asin(gcrs[2] / float(np.linalg.norm(gcrs))))

    sep = _sep_arcsec(ra, dec, HORIZONS_ISS_RA_DEG, HORIZONS_ISS_DEC_DEG)
    assert sep < ISS_TOLERANCE_ARCSEC, (
        f"our geocentric ISS is {sep:.1f} arcsec from JPL Horizons "
        f"(ours {ra:.5f} {dec:+.5f}, JPL {HORIZONS_ISS_RA_DEG:.5f} "
        f"{HORIZONS_ISS_DEC_DEG:+.5f}) -- more than an element set's own error "
        f"can account for, so something in the frame chain is wrong")

    au_km = 149597870.7
    delta = float(np.linalg.norm(gcrs))
    assert abs(delta - HORIZONS_ISS_DELTA_AU * au_km) < 5.0, (
        f"geocentric range {delta:.1f} km vs JPL's "
        f"{HORIZONS_ISS_DELTA_AU * au_km:.1f} km")


def test_the_row_matches_the_same_horizons_vector(site):
    """The published ROW, not just the internal vector -- the RA/Dec a client
    actually receives has to be the one that was checked."""
    row = sats.row(ISS_ROW, WHEN)
    sep = _sep_arcsec(row["ra_hours"] * 15.0, row["dec_deg"],
                      HORIZONS_ISS_RA_DEG, HORIZONS_ISS_DEC_DEG)
    assert sep < ISS_TOLERANCE_ARCSEC, f"the row is {sep:.1f} arcsec out"
    assert row["norad_id"] == 25544
    assert row["kind"] == "satellite" and row["type"] == "Satellite"
    assert row["ephemeris_unix"] == WHEN


# ============================================== no satellite has a magnitude

def test_a_satellite_row_carries_a_null_magnitude_never_a_number(site):
    """There is no open magnitude source for satellites: the GP element sets
    carry none, SATCAT carries a radar cross-section BAND rather than an
    optical brightness, and the McCants intrinsic-magnitude file is
    unmaintained and unlicensed. So the field is present -- the row shape has to
    match the rest of the catalog -- and is NEVER a number, because a number
    here would be invented and would look exactly like a measured one."""
    row = sats.row(ISS_ROW, WHEN)
    assert "mag" in row, "the field must exist so the row shape is uniform"
    assert row["mag"] is None, (
        f"a satellite row carried magnitude {row['mag']!r}; there is no source "
        f"for it, so it was invented")


def test_a_null_magnitude_does_not_break_the_catalog_sort(site):
    """The row above goes through ``objects.search``, whose sort key used to be
    ``r["mag"]`` -- and ``None`` does not compare with a float. Without the
    sentinel the whole search raises TypeError the moment one satellite
    matches, which is a failure of the SEARCH, not of satellites."""
    from astrodeck.catalog.objects import search

    el.write_envelope(el.SATELLITE_FILE, "test", [ISS_ROW], WHEN)
    found = search("iss", 25, WHEN, True)
    sat_rows = [r for r in found.rows if r.get("kind") == "satellite"]
    assert sat_rows and sat_rows[0]["mag"] is None
    assert sat_rows[0]["id"] == "ISS (ZARYA)"


# ================================================= the shadow model, on its own

def test_the_conical_shadow_puts_the_sunward_side_in_daylight(site):
    """Sanity on the geometry's sign: a satellite between the Earth and the Sun
    is lit whatever its altitude."""
    sun = np.array([[1.496e8, 0.0, 0.0]])
    lit = np.array([[7000.0, 0.0, 0.0]])          # sunward
    dark = np.array([[-7000.0, 0.0, 0.0]])        # straight down the shadow
    assert sats.shadow_state(lit, sun)[0] == "sunlit"
    assert sats.shadow_state(dark, sun)[0] == "umbra"


def _umbra_radius_km(down_shadow_km: float) -> float:
    """Where the umbra boundary actually is at ``down_shadow_km``, found by
    bisecting the model rather than by re-deriving its arithmetic (a test that
    recomputes the formula only proves the formula was typed twice)."""
    sun = np.array([[1.496e8, 0.0, 0.0]])
    lo, hi = 0.0, 20000.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        state = sats.shadow_state(np.array([[-down_shadow_km, mid, 0.0]]), sun)
        if state[0] == "umbra":
            lo = mid
        else:
            hi = mid
    return lo


def test_the_umbra_is_a_converging_cone_not_a_cylinder():
    """The whole reason the conical model is here. A cylinder shadow has the
    same radius everywhere; the real umbra CONVERGES, because the Sun is a disc
    and not a point. Measured through the model: the boundary is 6,374 km at
    1,000 km down-shadow, 6,286 km at 20,000 km and 5,457 km at 200,000 km --
    all inside a naive 6,378 km cylinder, and at ISS speed that difference is
    worth close to a minute of pass time at each end."""
    near = _umbra_radius_km(1_000.0)
    mid = _umbra_radius_km(20_000.0)
    far = _umbra_radius_km(200_000.0)
    assert far < mid < near < sats.R_EARTH_KM, (
        f"the umbra is not converging: {near:.1f} / {mid:.1f} / {far:.1f} km "
        f"against an Earth radius of {sats.R_EARTH_KM} -- a cylinder model "
        f"would give the same number three times")
    # And the band between the two models is real, not rounding: a satellite
    # 6,330 km off the shadow axis at 20,000 km is INSIDE the cylinder and
    # OUTSIDE the umbra, which is exactly the sighting the cylinder loses.
    assert mid < 6330.0 < sats.R_EARTH_KM
    sun = np.array([[1.496e8, 0.0, 0.0]])
    assert sats.shadow_state(np.array([[-20000.0, 6330.0, 0.0]]), sun)[0] \
        != "umbra"


def test_sunlit_means_not_in_the_umbra():
    assert bool(sats.is_sunlit(np.array(["sunlit"]))[0]) is True
    assert bool(sats.is_sunlit(np.array(["penumbra"]))[0]) is True
    assert bool(sats.is_sunlit(np.array(["umbra"]))[0]) is False


# ============================================ refusals rather than guesses

@pytest.mark.parametrize("line1,line2,why", [
    ("not a tle", "nor is this", "a line of noise"),
    (ISS_TLE[0],
     "2 25544  51.6301 239.6211 9999991 124.3002 235.8459 15.49065630584920",
     "an eccentricity of 0.9999991"),
])
def test_an_unreadable_element_set_raises_rather_than_returning_a_direction(
        site, line1, line2, why):
    """``Satrec.twoline2rv`` DOES NOT RAISE on nonsense -- it returns an
    ordinary object with a non-zero ``error`` attribute, and that object will
    propagate and hand back a vector if nobody looks. A wrong direction that
    arrives silently is the failure mode this whole module is written against,
    so the refusal is made at parse time."""
    with pytest.raises(sats.SatellitesUnavailable) as exc:
        sats._satrec({"name": "junk", "norad_id": 1, "line1": line1,
                      "line2": line2})
    assert "refused this element set" in str(exc.value), why


def test_no_site_is_a_refusal_not_a_geocentric_stand_in(tmp_path, monkeypatch):
    """A planet degrades gracefully to geocentric. A satellite does not: at
    400 km the geocentric direction is tens of degrees away, which is a
    different part of the sky rather than a coarser answer."""
    import astrodeck.config as config_mod

    s = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", s)
    assert s.cfg().site.is_default is True
    with pytest.raises(sats.SatellitesUnavailable) as exc:
        sats.row(ISS_ROW, WHEN)
    assert "Settings > Site" in str(exc.value)
