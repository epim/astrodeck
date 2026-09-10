"""Where a satellite is, right now, from HERE.

SATELLITES ARE SITE-DERIVED, WHOLE. This is the one thing to understand before
reading anything else in this file. A planet's position barely moves with the
observer -- ``solar_system.SITE_DERIVED_BODIES`` measures 0.4 to 12.8 arcsec
between two sites 15,000 km apart, which is why a caller without
``view.site_derived`` is simply given the GEOCENTRIC answer and loses nothing
that matters. That trick does not transfer here. A satellite at 400 km has tens
of DEGREES of topocentric parallax: the geocentric direction to the ISS and the
direction you would point a telescope are different parts of the sky, and two
towns twenty miles apart disagree by more than a fist at arm's length. So there
is no degraded answer to give. The rows are WITHHELD -- and withheld BEFORE the
propagator runs, so a caller cannot time the difference between "we refused"
and "we computed and then dropped it" (``objects._solar_system_hits`` withholds
the Moon the same way, for the same reason).

THE PROPAGATOR IS NOT OURS, DELIBERATELY. SGP4/SDP4 is the model the two-line
element sets are DEFINED by: a TLE is not an osculating state vector, it is a
set of mean elements that only mean anything when fed through this specific
algorithm, deep-space corrections and all. A hand-rolled implementation that is
subtly wrong does not fail loudly -- it points a telescope at empty sky. So the
``sgp4`` package (Brandon Rhodes' port of the AIAA 2006-6753 reference code) is
a hard dependency, and ``tests/test_satellite_ephemeris.py`` checks OUR call of
it against the corpus that ships inside it (``SGP4-VER.TLE`` +
``tcppver.out``), offline, on every run.

THE FRAME CHAIN, because getting it wrong is invisible: SGP4 returns TEME (true
equator, mean equinox) km. TEME -> ITRS is a rotation astropy does exactly;
ITRS -> topocentric alt/az is then arithmetic on the site's own ITRS vector,
done here rather than through astropy's observed frames because the pass search
evaluates ~8,600 samples per satellite and needs it vectorised. TEME -> GCRS
gives RA/Dec on the same ICRS axes as the fixed catalog, so the hub's J2000 ->
JNOW precession applies to it unchanged (see ``solar_system``'s ICRS trap note:
do NOT transform to ICRS, which is barycentric).

NO SATELLITE ROW CARRIES A MAGNITUDE. See the "relevance" block below.
"""
from __future__ import annotations

import logging
import math
import time as _time

import numpy as np

log = logging.getLogger(__name__)

try:  # the propagator is a hard dependency; guarded so an import never 500s
    from sgp4.api import SGP4_ERRORS, WGS72, Satrec
    HAVE_SGP4 = True
except ImportError:  # pragma: no cover - exercised by a masked-import test
    Satrec = None  # type: ignore[assignment]
    SGP4_ERRORS = {}  # type: ignore[assignment]
    WGS72 = None  # type: ignore[assignment]
    HAVE_SGP4 = False

#: WGS-84 equatorial radius (km) and the Sun's photospheric radius (km). Used
#: only by the shadow model.
R_EARTH_KM = 6378.137
R_SUN_KM = 695700.0


class SatellitesUnavailable(RuntimeError):
    """A satellite position could not be produced. Raised instead of returning
    a plausible-looking direction -- the caller drops the row and says why."""


def _require_sgp4() -> None:
    if not HAVE_SGP4:
        raise SatellitesUnavailable(
            "satellite positions need the 'sgp4' package, which ships as a "
            "hard dependency of astrodeck: pip install 'sgp4>=2.23'")


# ------------------------------------------------------------- refusals to say
#: The note a caller without ``view.site_derived`` gets INSTEAD of rows.
WITHHELD_NOTE = (
    "Satellites are not offered here: where one appears in the sky depends "
    "entirely on where you are standing - a low-orbit satellite is tens of "
    "degrees apart from two towns - so its position would give away this rig's "
    "location. Sign in with a role that can see site-derived data to search "
    "for them.")

#: And the note when the rig itself does not know where it is. There is no
#: geocentric stand-in to fall back on here (see the module docstring), so this
#: is a refusal rather than a degraded row.
SITE_UNSET_NOTE = (
    "Satellites need this rig's location before they can be placed: a "
    "low-orbit satellite is tens of degrees apart from two towns, so a "
    "position computed without a site would not be wrong by a little, it would "
    "be a different part of the sky. Set a location in Settings > Site.")


# ------------------------------------------------------------------ relevance
#
# WHAT "RELEVANT" MEANS, AND WHY NO ROW CARRIES A MAGNITUDE.
#
# There are ~11,000 tracked objects bright enough to have an element set and
# perhaps a hundred anyone would walk outside to look at. CelesTrak's
# ``GROUP=visual`` IS that curated list -- Dr Kelso's own selection of the
# objects visible to the naked eye -- so this app takes it as given rather than
# inventing a second opinion, capped at ``MAX_SATELLITES`` in CelesTrak's own
# order, with ISS, Tiangong and Hubble fetched individually so the three names
# a beginner types are present even when the group fetch failed.
#
# There is NO open magnitude source to rank them by. The GP element sets carry
# none. SATCAT carries a radar cross-section BAND (SMALL/MEDIUM/LARGE), which
# is a radar measurement of a shape, not an optical brightness, and converting
# one to the other needs an albedo and an attitude nobody publishes. The
# McCants "quicksat" file is the only widely-used intrinsic-magnitude set and it
# is unmaintained and carries no licence. So every row's ``mag`` is null --
# present as a field, because the row shape has to match the rest of the
# catalog, and NEVER a number, because a number here would be invented. Passes
# rank by computed maximum elevation and sunlit state, which are things this
# app actually measures.


# ------------------------------------------------------------ the element sets

def _satrec(row: dict):
    """A ``Satrec`` for one cached element row.

    Two shapes, because CelesTrak's GP JSON is OMM (named mean-element fields)
    while its ``FORMAT=tle`` output and some GP records carry the two lines. A
    row with lines goes through ``twoline2rv``; one with OMM fields goes through
    ``sgp4.omm``. Both end at the same object, so nothing downstream branches."""
    _require_sgp4()
    l1, l2 = row.get("line1"), row.get("line2")
    sat = None
    try:
        if isinstance(l1, str) and isinstance(l2, str) and l1 and l2:
            sat = Satrec.twoline2rv(l1, l2, WGS72)
        else:
            omm_fields = row.get("omm")
            if isinstance(omm_fields, dict):
                from sgp4 import omm as _omm

                sat = Satrec()
                _omm.initialize(sat, omm_fields)
    except Exception as e:              # noqa: BLE001 - any sgp4 parse failure
        raise SatellitesUnavailable(
            f"{row.get('name', 'satellite')}: unreadable element set "
            f"({type(e).__name__})") from e
    if sat is None:
        raise SatellitesUnavailable(
            f"{row.get('name', 'satellite')}: element row carries neither TLE "
            f"lines nor OMM fields")
    # ``twoline2rv`` DOES NOT RAISE on nonsense. It parses whatever it is given
    # and records the problem in ``sat.error`` -- a line of noise comes back as
    # a perfectly ordinary Satrec with error 2 (mean motion below zero), and an
    # eccentricity of 0.9999991 as error 4. Left unchecked that object
    # propagates happily and returns a vector, so the refusal has to be made
    # HERE, at parse, rather than discovered later as a wrong direction.
    code = int(getattr(sat, "error", 0) or 0)
    if code:
        raise SatellitesUnavailable(
            f"{row.get('name', 'satellite')}: SGP4 refused this element set "
            f"({SGP4_ERRORS.get(code, code)})")
    return sat


def epoch_unix(sat) -> float:
    """The unix time the element set was measured for.

    This -- not the day we downloaded the file -- is what an SGP4 position
    drifts away from, so it is what ``elements_age_days`` on a row reports."""
    from astropy.time import Time

    return float(Time(sat.jdsatepoch, sat.jdsatepochF,
                      format="jd", scale="utc").unix)


# ----------------------------------------------------------------- propagation

def propagate_teme(sat, t):
    """TEME position (km) for an astropy ``Time`` (scalar or array).

    Returns an ``(N, 3)`` array (``(1, 3)`` for a scalar time). RAISES on any
    non-zero SGP4 error code rather than returning the garbage vector the
    library hands back alongside it -- a decayed or mis-parsed object must not
    reach a mount as a direction."""
    _require_sgp4()
    jd1 = np.atleast_1d(np.asarray(t.jd1, dtype=float))
    jd2 = np.atleast_1d(np.asarray(t.jd2, dtype=float))
    err, r, _v = sat.sgp4_array(jd1, jd2)
    err = np.atleast_1d(err)
    bad = np.flatnonzero(err != 0)
    if bad.size:
        code = int(err[bad[0]])
        raise SatellitesUnavailable(
            f"SGP4 refused this element set: {SGP4_ERRORS.get(code, code)}")
    return np.atleast_2d(np.asarray(r, dtype=float))


def teme_to_itrs_km(r_teme_km, t):
    """Rotate an ``(N, 3)`` TEME vector into ITRS, in km.

    Uses astropy's own TEME -> ITRS transform, on an ARRAY ``Time``: the
    rotation matrices are computed once for the whole grid and cached on the
    ``Time`` object, which is what makes a 24-hour pass search over 200
    satellites affordable (measured: 0.25 s for the first satellite, 1.5 ms for
    every one after it)."""
    import astropy.units as u
    from astropy.coordinates import ITRS, TEME, CartesianRepresentation

    r = np.atleast_2d(np.asarray(r_teme_km, dtype=float))
    teme = TEME(CartesianRepresentation(r.T * u.km), obstime=t)
    return np.atleast_2d(
        teme.transform_to(ITRS(obstime=t)).cartesian.xyz.to_value(u.km).T)


def teme_to_gcrs_km(r_teme_km, t):
    """Rotate an ``(N, 3)`` TEME vector onto the ICRS axes, geocentric, in km.

    GCRS -- not ICRS. ICRS is BARYCENTRIC, and for an object with a real
    distance astropy dutifully re-plots it as seen from the solar-system
    barycentre; ``solar_system``'s module docstring records the afternoon that
    cost when it moved the Moon 17 degrees. GCRS's axes ARE the ICRS/J2000 axes
    with the observer at the origin, so this RA/Dec is directly comparable to
    the fixed catalog's."""
    import astropy.units as u
    from astropy.coordinates import GCRS, TEME, CartesianRepresentation

    r = np.atleast_2d(np.asarray(r_teme_km, dtype=float))
    teme = TEME(CartesianRepresentation(r.T * u.km), obstime=t)
    return np.atleast_2d(
        teme.transform_to(GCRS(obstime=t)).cartesian.xyz.to_value(u.km).T)


# ------------------------------------------------------------ site arithmetic

def site_itrs_km(location) -> np.ndarray:
    """The observer's ITRS vector (km). Constant in ITRS -- that is the point of
    the frame -- so it is computed once and reused across the whole grid."""
    import astropy.units as u

    return np.asarray(location.get_itrs().cartesian.xyz.to_value(u.km),
                      dtype=float)


def altaz_from_itrs(r_itrs_km, site_km, lat_deg: float, lon_deg: float):
    """``(alt_deg, az_deg, range_km)`` arrays for an ``(N, 3)`` ITRS vector.

    Done here rather than through astropy's observed frames because it is exact
    arithmetic (one subtraction and one rotation into the site's east/north/up
    basis) and because the pass search runs it over thousands of samples per
    satellite. The subtraction is the whole reason satellites are site-derived:
    for a target at 400 km it moves the answer by tens of degrees."""
    d = np.atleast_2d(np.asarray(r_itrs_km, dtype=float)) - np.asarray(site_km,
                                                                       float)
    rng = np.linalg.norm(d, axis=1)
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sla, cla = math.sin(lat), math.cos(lat)
    slo, clo = math.sin(lon), math.cos(lon)
    east = -slo * d[:, 0] + clo * d[:, 1]
    north = -sla * clo * d[:, 0] - sla * slo * d[:, 1] + cla * d[:, 2]
    up = cla * clo * d[:, 0] + cla * slo * d[:, 1] + sla * d[:, 2]
    with np.errstate(invalid="ignore", divide="ignore"):
        alt = np.degrees(np.arcsin(np.clip(up / rng, -1.0, 1.0)))
    az = np.degrees(np.arctan2(east, north)) % 360.0
    return alt, az, rng


# ------------------------------------------------------------- the shadow cone

def sun_gcrs_km(t):
    """The Sun's geocentric position (km) on ICRS axes, as an ``(N, 3)`` array.

    From astropy's built-in ephemeris (ERFA), so no download -- the same source
    ``solar_system`` uses, and the same reason: this has to work on a field Pi
    with no network."""
    import astropy.units as u
    from astropy.coordinates import get_body_barycentric

    sun = get_body_barycentric("sun", t).xyz.to_value(u.km)
    earth = get_body_barycentric("earth", t).xyz.to_value(u.km)
    return np.atleast_2d((np.asarray(sun) - np.asarray(earth)).T)


def shadow_state(r_gcrs_km, sun_km) -> np.ndarray:
    """``"sunlit"`` / ``"penumbra"`` / ``"umbra"`` for each sample.

    THE CONE, NOT A CYLINDER. Modelling the Earth's shadow as a cylinder of the
    Earth's own radius is the easy version and it is wrong at both ends of a
    pass: the umbra is a converging cone (the Sun is not a point) and the
    penumbra a diverging one, and at ISS altitude the difference between the
    cylinder's boundary and the real umbra boundary is worth close to a minute
    of pass time at each end -- which is the difference between "you will see it
    fade out overhead" and "you will see it fade out down at 20 degrees".

    Vallado, *Fundamentals of Astrodynamics and Applications*, algorithm SHADOW:
    the umbra half-angle is ``asin((Rsun - Rearth) / d_sun)`` and the penumbra's
    ``asin((Rsun + Rearth) / d_sun)``; a satellite on the anti-sunward side is
    tested against each cone's radius at its own distance down the axis."""
    r = np.atleast_2d(np.asarray(r_gcrs_km, dtype=float))
    s = np.atleast_2d(np.asarray(sun_km, dtype=float))
    if s.shape[0] == 1 and r.shape[0] > 1:
        s = np.repeat(s, r.shape[0], axis=0)
    d_sun = np.linalg.norm(s, axis=1)
    r_mag = np.linalg.norm(r, axis=1)
    a_umb = np.arcsin(np.clip((R_SUN_KM - R_EARTH_KM) / d_sun, -1.0, 1.0))
    a_pen = np.arcsin(np.clip((R_SUN_KM + R_EARTH_KM) / d_sun, -1.0, 1.0))
    # Angle between the satellite and the anti-solar direction.
    with np.errstate(invalid="ignore", divide="ignore"):
        cos_zeta = np.clip(np.einsum("ij,ij->i", r, -s) / (r_mag * d_sun),
                           -1.0, 1.0)
    zeta = np.arccos(cos_zeta)
    sat_horiz = r_mag * np.cos(zeta)
    sat_vert = r_mag * np.sin(zeta)
    pen_vert = np.tan(a_pen) * (R_EARTH_KM / np.sin(a_pen) + sat_horiz)
    umb_vert = np.tan(a_umb) * (R_EARTH_KM / np.sin(a_umb) - sat_horiz)
    out = np.full(r.shape[0], "sunlit", dtype="<U9")
    behind = sat_horiz > 0.0            # anti-sunward hemisphere only
    in_pen = behind & (sat_vert <= pen_vert)
    in_umb = in_pen & (sat_vert <= umb_vert)
    out[in_pen] = "penumbra"
    out[in_umb] = "umbra"
    return out


def is_sunlit(state) -> np.ndarray:
    """Is the satellite lit enough to be seen?

    TRUE in the penumbra. A satellite in partial eclipse is dimmed, not dark --
    at ISS altitude the penumbra crossing lasts seconds -- so treating it as
    unlit would clip a real pass at both ends. Only the umbra is dark."""
    return np.asarray(state) != "umbra"


# ------------------------------------------------------------------- the rows

def _orbit_words(alt_km: float, period_min: float) -> str:
    if alt_km < 2000.0:
        return "low Earth orbit"
    if 34000.0 <= alt_km <= 37500.0 and 1350.0 <= period_min <= 1500.0:
        return "geostationary belt"
    if alt_km < 35000.0:
        return "medium Earth orbit"
    return "high orbit"


def _crossing_minutes(alt_km: float, period_min: float) -> float:
    """Roughly how long an overhead pass lasts, horizon to horizon.

    Measured to a 10-degree elevation mask rather than to the geometric horizon,
    because a satellite in the last degrees before it sets is behind trees,
    houses and air -- and because the number a user compares this against is the
    length of a pass they have actually stood outside and watched. Depends only
    on the ORBIT, never on the site, so the sentence is the same wherever the
    rig is."""
    r = R_EARTH_KM + max(alt_km, 1.0)
    mask = math.radians(10.0)
    arg = R_EARTH_KM * math.cos(mask) / r
    if arg >= 1.0:
        return 0.0
    half = math.acos(arg) - mask
    return period_min * (2.0 * half) / (2.0 * math.pi)


def describe(name: str, alt_km: float, period_min: float, state: str,
             elements_age_days: float) -> str:
    """The line the Atlas row shows. Every clause is something the user cannot
    work out by looking up."""
    parts = [f"{_orbit_words(alt_km, period_min)}",
             f"{alt_km:.0f} km up"]
    mins = _crossing_minutes(alt_km, period_min)
    if mins > 0.0:
        parts.append(f"crosses the sky in about {mins:.0f} minutes")
    parts.append("in sunlight" if state == "sunlit"
                 else ("in the Earth's shadow" if state == "umbra"
                       else "in the Earth's penumbra"))
    if elements_age_days > 1.0:
        parts.append(f"elements {elements_age_days:.0f} days old")
    return f"{name} - " + ", ".join(parts)


def _site_location():
    """``(EarthLocation, latitude, longitude)`` for the configured site.

    Raises :class:`SatellitesUnavailable` when no site is set. There is no
    geocentric fallback for a satellite (module docstring), so this is the one
    place the refusal has to be made rather than papered over."""
    import astropy.units as u
    from astropy.coordinates import EarthLocation

    from ...config import config_store

    site = config_store.cfg().site
    if getattr(site, "is_default", False):
        raise SatellitesUnavailable(SITE_UNSET_NOTE)
    loc = EarthLocation(lat=site.latitude * u.deg, lon=site.longitude * u.deg,
                        height=site.elevation_m * u.m)
    return loc, float(site.latitude), float(site.longitude)


def row(el_row: dict, when: float | None = None) -> dict:
    """A catalog row for one satellite, in the shape /api/catalog returns.

    There is no ``site_derived`` parameter, and that absence is the design: a
    row this function could produce for a non-holder does not exist. Callers
    withhold BEFORE they get here."""
    from astropy.time import Time

    t_unix = _time.time() if when is None else float(when)
    loc, lat, lon = _site_location()
    sat = _satrec(el_row)
    t = Time(t_unix, format="unix")
    r_teme = propagate_teme(sat, t)
    itrs = teme_to_itrs_km(r_teme, t)
    gcrs = teme_to_gcrs_km(r_teme, t)
    alt, az, rng = altaz_from_itrs(itrs, site_itrs_km(loc), lat, lon)
    state = shadow_state(gcrs, sun_gcrs_km(t))[0]

    g = gcrs[0]
    ra_hours = (math.degrees(math.atan2(g[1], g[0])) % 360.0) / 15.0
    dec_deg = math.degrees(math.asin(g[2] / float(np.linalg.norm(g))))
    height_km = float(np.linalg.norm(r_teme[0])) - R_EARTH_KM
    period_min = (2.0 * math.pi / sat.no_kozai) if sat.no_kozai > 0 else 0.0
    age_days = max(0.0, (t_unix - epoch_unix(sat)) / 86400.0)
    name = str(el_row.get("name") or f"NORAD {el_row.get('norad_id')}")
    return {
        "id": name,
        "name": describe(name, height_km, period_min, str(state), age_days),
        "type": "Satellite",
        "kind": "satellite",
        "norad_id": int(el_row.get("norad_id", 0)),
        "ra_hours": ra_hours,
        "dec_deg": dec_deg,
        "alt": round(float(alt[0]), 2),
        "az": round(float(az[0]), 2),
        "range_km": round(float(rng[0]), 1),
        "sunlit": bool(is_sunlit(state)),
        "shadow": str(state),
        "elements_age_days": round(age_days, 3),
        "ephemeris_unix": t_unix,
        # NEVER a number. See _relevance above: there is no open magnitude
        # source for satellites, and an invented one would be indistinguishable
        # from a measured one on screen.
        "mag": None,
        "size_arcmin": 0.0,
    }


# ---------------------------------------------------------------- the searches

def cached_rows() -> list[dict]:
    """Every cached element row, or [] when nothing has been downloaded."""
    from . import elements

    env = elements.load(elements.SATELLITES)
    return list(env["rows"]) if env else []


def rows_by_id(norad_ids) -> list[dict]:
    wanted = {int(i) for i in norad_ids}
    return [r for r in cached_rows() if int(r.get("norad_id", -1)) in wanted]


def search_hits(q: str, qs: str, when: float | None,
                site_derived: bool = True) -> tuple[list[tuple[int, dict]],
                                                    list[str]]:
    """Satellite rows for a query, and the reasons for the ones not returned.

    THE ORDER OF THE FIRST TWO CHECKS IS THE SECURITY PROPERTY. The
    ``site_derived`` refusal happens before a single element set is read, let
    alone propagated: a withheld row must not be computable-and-then-dropped,
    because the TIME that takes says what the row would not."""
    from ..objects import RANK_TYPE, _best, _rank_keys, squash_designation
    from . import elements

    notes: list[str] = []
    if not q:
        return [], notes

    # 1. The capability, before anything is read from disk or propagated.
    names_kind = len(q) >= 3 and ("satellites".startswith(q) or q == "sat")
    matched_any = names_kind
    rows = cached_rows() if site_derived else []
    if not site_derived:
        # Match on NAMES ONLY -- a name is in the cache file, not in the sky --
        # so the note is offered when the user actually asked about satellites.
        for el in cached_rows():
            name = str(el.get("name") or "")
            if _best(_rank_keys(qs, [squash_designation(name)]),
                     _rank_keys(qs, [str(el.get("norad_id"))])) is not None:
                matched_any = True
                break
        if matched_any:
            notes.append(WITHHELD_NOTE)
        return [], notes

    # 2. The cache. No elements is a SENTENCE, not an empty list.
    state = elements.cache_state(elements.SATELLITES, when)
    if not rows:
        if names_kind:
            notes.append(state["note"] or elements.NO_SATELLITES_NOTE)
        return [], notes

    # 3. The site. A satellite with no site is not a coarse answer, it is a
    # different part of the sky -- so it is a refusal, said once, rather than a
    # per-row failure message repeated two hundred times.
    matched = []
    for el in rows:
        name = str(el.get("name") or "")
        rank = _best(_rank_keys(qs, [squash_designation(name)]),
                     _rank_keys(qs, [str(el.get("norad_id"))]),
                     RANK_TYPE if names_kind else None)
        if rank is not None:
            matched.append((rank, el, name))
    if not matched:
        return [], notes
    try:
        _site_location()
    except SatellitesUnavailable as e:
        notes.append(str(e))
        return [], notes

    hits: list[tuple[int, dict]] = []
    stale_said = False
    for rank, el, name in matched:
        try:
            hits.append((rank, row(el, when)))
        except SatellitesUnavailable as e:
            log.warning("dropping %s from search: %s", name, e)
            notes.append(
                f"{name} could not be placed just now ({e}). It is left out "
                f"rather than shown at a guessed position.")
            continue
        if state["stale"] and not stale_said:
            notes.append(state["note"])
            stale_said = True
    return hits, notes
