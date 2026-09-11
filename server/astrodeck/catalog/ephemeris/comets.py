"""Comets, from the Minor Planet Center's orbital elements.

WHY THIS IS NOT THE SATELLITE FILE OVER AGAIN. A comet is far away. The
parallax between two towns -- or between the centre of the Earth and a rooftop
-- is sub-arcsecond at a tenth of an astronomical unit and unmeasurable at one
astronomical unit, which is where every comet anyone can see actually is. So a
comet row is NOT a location oracle, and it takes the PLANETS' path rather than
the satellites': it is served to everybody, computed through
``solar_system._observer(site_derived)``, and a caller without
``view.site_derived`` gets the geocentric answer with the same
``geocentric_reason`` field a planet row carries. Nothing is withheld, because
nothing in the row is a function of where this rig stands to any precision
worth hiding.

WHY THE ORBIT IS SOLVED HERE AND NOT BY A LIBRARY. The MPC publishes ELEMENTS,
not positions: perihelion distance, eccentricity, the three angles and a
perihelion date. Turning those into a place in the sky is a two-body solution,
and the only reason it is not one line is that comets span the whole range of
conic sections and one solver does not cover them:

* ``e < 0.98`` -- an ellipse. Kepler's equation, Newton from a Danby start.
* ``0.98 <= e <= 1.02`` -- near-parabolic. Kepler's equation degenerates here
  (the eccentric anomaly's coefficient ``a = q/(1-e)`` runs away to infinity),
  so this band uses Barker's equation with the Stumpff-series correction for
  the small departure from a true parabola -- Meeus, *Astronomical Algorithms*,
  chapter 35.
* ``e > 1.02`` -- a hyperbola. ``M = e sinh H - H``, Newton again.

Every solver RAISES rather than returning a half-converged answer. A comet
placed by a solve that did not converge is a marker on a screen that looks
exactly like a real one.

MAGNITUDE IS REAL HERE, unlike the satellites'. The MPC publishes ``H`` and the
slope ``G`` for each comet and the standard model is
``m = H + 5 log10(delta) + 2.5 G log10(r)``. It is the MPC's model, not a
measurement, and it is routinely a magnitude or two out for an active comet --
so the row's own prose says whose model it is.

CHECKED AGAINST JPL HORIZONS. ``tests/test_comet_ephemeris.py`` pins one
Horizons query and its answer for 2P/Encke, exactly the way
``solar_system.py``'s docstring pins the planets' residuals, and Vallado's
worked Kepler example guards the solver on its own.
"""
from __future__ import annotations

import logging
import math
import time as _time

from ..solar_system import EphemerisUnavailable

log = logging.getLogger(__name__)

#: Gaussian gravitational constant, radians/day. The defining constant of the
#: two-body solution for a massless body about the Sun.
K_GAUSS = 0.01720209895
#: Speed of light in AU per day, for the down-leg light-time correction.
C_AU_PER_DAY = 173.144632674240

#: Convergence: 1e-12 radians is ~0.2 microarcseconds of anomaly, far below
#: anything the elements themselves are good for, and the cap is what turns a
#: non-converging case into a refusal instead of a silent wrong answer.
_TOL = 1e-12
_MAX_ITER = 60

#: The eccentricity band where neither the elliptical nor the hyperbolic form
#: is numerically usable, and Barker's near-parabolic solution is.
NEAR_PARABOLIC_LOW = 0.98
NEAR_PARABOLIC_HIGH = 1.02




# --------------------------------------------------------------- MPC parsing
#
# CometEls.txt is the MPC's 80-column punched-card format, which is why every
# field below is a fixed slice rather than a split: the name column contains
# spaces, the number column is blank for a comet that has not been numbered,
# and a whitespace split silently shifts every field after the first gap.
#
#   col 1-4    periodic comet number        col 81-88   epoch of the elements
#   col 5      orbit type (C / P / D / X)   col 92-95   absolute magnitude H
#   col 15-18  year of perihelion (TT)      col 97-100  slope parameter G
#   col 20-21  month                        col 103-158 designation and name
#   col 23-29  day, fractional (TT)         col 160-168 reference
#   col 31-39  perihelion distance q, AU
#   col 41-49  eccentricity e
#   col 51-59  argument of perihelion, J2000
#   col 61-69  longitude of ascending node, J2000
#   col 71-79  inclination, J2000

def _f(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def jd_from_ymd(year: int, month: int, day: float) -> float:
    """Julian Date for a proleptic-Gregorian calendar date with a FRACTIONAL
    day -- which is how the MPC states a perihelion instant."""
    y, m = year, month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + day + b - 1524.5)


def parse_comet_line(line: str) -> dict | None:
    """One row of ``CometEls.txt``, or None when the line is not one."""
    if len(line) < 80:
        return None
    year, month = _f(line[14:18]), _f(line[19:21])
    day = _f(line[22:29])
    q, e = _f(line[30:39]), _f(line[40:49])
    peri, node, incl = _f(line[50:59]), _f(line[60:69]), _f(line[70:79])
    if None in (year, month, day, q, e, peri, node, incl):
        return None
    if q <= 0.0 or e < 0.0:
        return None
    name = line[102:158].strip() if len(line) > 102 else ""
    number = line[0:4].strip()
    orbit_type = line[4:5].strip()
    designation = (f"{int(number)}{orbit_type}" if number.isdigit()
                   else (line[5:12].strip() or name))
    epoch_raw = line[81:89].strip() if len(line) > 81 else ""
    epoch_jd = None
    if len(epoch_raw) == 8 and epoch_raw.isdigit():
        epoch_jd = jd_from_ymd(int(epoch_raw[0:4]), int(epoch_raw[4:6]),
                               float(epoch_raw[6:8]))
    h = _f(line[91:95]) if len(line) > 91 else None
    g = _f(line[96:100]) if len(line) > 96 else None
    return {
        "id": designation or (name or "comet"),
        "name": name or designation,
        "q_au": float(q),
        "e": float(e),
        "peri_deg": float(peri),
        "node_deg": float(node),
        "incl_deg": float(incl),
        "tp_tt_jd": jd_from_ymd(int(year), int(month), float(day)),
        "epoch_tt_jd": epoch_jd,
        "h_mag": h,
        "slope_g": g if g is not None else 4.0,
    }


def parse_comet_els(text: str) -> list[dict]:
    """Every usable row in a ``CometEls.txt`` body."""
    out: list[dict] = []
    for line in text.splitlines():
        row = parse_comet_line(line)
        if row is not None:
            out.append(row)
    return out


# ------------------------------------------------------------- the conic solve

def solve_kepler(mean_anomaly: float, e: float) -> float:
    """Eccentric anomaly ``E`` (radians) from ``M`` and ``e < 1``.

    Newton-Raphson from Danby's starter ``E0 = M + 0.85 e sign(sin M)``, which
    is what keeps the iteration out of the slow corner near ``E = pi`` for a
    high-eccentricity orbit. RAISES rather than returning the last iterate: a
    half-converged anomaly is a position, and a position is a slew.

    ``M`` is normalised to ``[-pi, pi)`` first, so the returned ``E`` is on the
    same branch -- Vallado's worked answer of 220.512074 degrees comes back
    here as -139.487926, which is the same angle."""
    m = (mean_anomaly + math.pi) % (2.0 * math.pi) - math.pi
    E = m + 0.85 * e * (1.0 if math.sin(m) >= 0.0 else -1.0)
    for _ in range(_MAX_ITER):
        f = E - e * math.sin(E) - m
        fp = 1.0 - e * math.cos(E)
        if fp == 0.0:
            break
        step = -f / fp
        E += step
        if abs(step) < _TOL:
            return E
    raise EphemerisUnavailable(
        f"Kepler's equation did not converge for e={e:.6f} in {_MAX_ITER} "
        f"iterations; refusing to place this comet at a half-solved position")


def solve_hyperbolic(mean_anomaly: float, e: float) -> float:
    """Hyperbolic anomaly ``H`` from ``M = e sinh H - H``."""
    m = mean_anomaly
    H = math.asinh(m / e) if e > 0 else 0.0
    for _ in range(_MAX_ITER):
        f = e * math.sinh(H) - H - m
        fp = e * math.cosh(H) - 1.0
        if fp == 0.0:
            break
        step = -f / fp
        H += step
        if abs(step) < _TOL:
            return H
    raise EphemerisUnavailable(
        f"the hyperbolic anomaly did not converge for e={e:.6f} in "
        f"{_MAX_ITER} iterations; refusing to place this comet")


def _near_parabolic(q: float, e: float, dt_days: float) -> tuple[float, float]:
    """``(true anomaly, r)`` for the near-parabolic band, by Barker's equation
    with the Stumpff-series correction (Meeus, chapter 35).

    Barker alone solves the PARABOLA (``e == 1`` exactly), and no real comet has
    that. The series in ``q3`` below is the correction for the small departure
    from parabolic, in powers of ``g = (1-e)/(1+e)``; it is what makes this
    branch correct at ``e = 0.999`` rather than merely close."""
    fail = EphemerisUnavailable
    q1 = K_GAUSS * math.sqrt((1.0 + e) / q) / (2.0 * q)
    g = (1.0 - e) / (1.0 + e)
    q2 = q1 * dt_days
    if q2 == 0.0:
        return 0.0, q
    s = 2.0 / (3.0 * abs(q2))
    s = 2.0 / math.tan(2.0 * math.atan(math.tan(math.atan(s) / 2.0) ** (1.0 / 3.0)))
    if dt_days < 0.0:
        s = -s
    for _outer in range(_MAX_ITER):
        s0 = s
        z = 1.0
        y = s * s
        g1 = -y * s
        q3 = q2 + 2.0 * g * s * y / 3.0
        for _series in range(_MAX_ITER):
            z += 1.0
            g1 = -g1 * g * y
            z1 = (z - (z + 1.0) * g) / (2.0 * z + 1.0)
            f = z1 * g1
            q3 += f
            if not math.isfinite(f) or abs(f) > 1e30:
                raise fail("the near-parabolic series diverged; refusing to "
                           "place this comet")
            if abs(f) <= 1e-12:
                break
        else:
            raise fail("the near-parabolic series did not converge; refusing "
                       "to place this comet")
        for _inner in range(_MAX_ITER):
            s1 = s
            s = (2.0 * s * s * s / 3.0 + q3) / (s * s + 1.0)
            if abs(s - s1) <= 1e-12:
                break
        else:
            raise fail("the near-parabolic iteration did not converge; "
                       "refusing to place this comet")
        if abs(s - s0) <= 1e-12:
            break
    else:
        raise fail("the near-parabolic solution did not settle in "
                   f"{_MAX_ITER} passes; refusing to place this comet")
    v = 2.0 * math.atan(s)
    r = q * (1.0 + e) / (1.0 + e * math.cos(v))
    return v, r


def true_anomaly_and_radius(el: dict, jd_tt: float) -> tuple[float, float]:
    """``(true anomaly in radians, heliocentric distance in AU)``.

    The one place the three conic branches meet, so everything downstream is
    written once."""
    q = float(el["q_au"])
    e = float(el["e"])
    dt = jd_tt - float(el["tp_tt_jd"])
    if e < NEAR_PARABOLIC_LOW:
        a = q / (1.0 - e)
        n = K_GAUSS / (a ** 1.5)
        E = solve_kepler(n * dt, e)
        x = a * (math.cos(E) - e)
        y = a * math.sqrt(1.0 - e * e) * math.sin(E)
        return math.atan2(y, x), a * (1.0 - e * math.cos(E))
    if e <= NEAR_PARABOLIC_HIGH:
        return _near_parabolic(q, e, dt)
    a = q / (e - 1.0)
    n = K_GAUSS / (a ** 1.5)
    H = solve_hyperbolic(n * dt, e)
    x = a * (e - math.cosh(H))
    y = a * math.sqrt(e * e - 1.0) * math.sinh(H)
    return math.atan2(y, x), a * (e * math.cosh(H) - 1.0)


def _obliquity_j2000_rad() -> float:
    """The mean obliquity of the ecliptic at J2000, IAU 2006.

    Read out of ERFA (which astropy carries and which IS the IAU's own
    reference implementation) rather than typed in as 23.4393, so the constant
    cannot drift from the model the rest of this app's astrometry uses, and so
    nobody has to trust a number in a comment."""
    import erfa

    return float(erfa.obl06(2451545.0, 0.0))


def heliocentric_equatorial_au(el: dict, jd_tt: float):
    """The comet's heliocentric position on the J2000 EQUATORIAL axes, in AU.

    Elements are referred to the J2000 ECLIPTIC, so the orbit is built in its
    own plane, rotated by the three Euler angles into ecliptic coordinates, and
    then tilted by the obliquity onto the equatorial axes the rest of the
    catalog uses."""
    v, r = true_anomaly_and_radius(el, jd_tt)
    x, y = r * math.cos(v), r * math.sin(v)
    w = math.radians(float(el["peri_deg"]))
    om = math.radians(float(el["node_deg"]))
    i = math.radians(float(el["incl_deg"]))
    cw, sw = math.cos(w), math.sin(w)
    co, so = math.cos(om), math.sin(om)
    ci, si = math.cos(i), math.sin(i)
    ex = (co * cw - so * sw * ci) * x + (-co * sw - so * cw * ci) * y
    ey = (so * cw + co * sw * ci) * x + (-so * sw + co * cw * ci) * y
    ez = (sw * si) * x + (cw * si) * y
    eps = _obliquity_j2000_rad()
    ce, se = math.cos(eps), math.sin(eps)
    return (ex, ey * ce - ez * se, ey * se + ez * ce), r


# ----------------------------------------------------------------- the position

def elements_age_days(el: dict, jd_tt: float,
                      fallback: float | None = None) -> float | None:
    """How old THIS comet's elements are at ``jd_tt``, in days.

    The MPC's own epoch, not the day we downloaded the file. A set republished
    last week can carry an epoch six months old, and it is the epoch that the
    two-body solution drifts away from -- so that is the number the row
    reports. ``fallback`` (the cache age) is used only when the element line
    carried no epoch at all."""
    epoch = el.get("epoch_tt_jd")
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool):
        return fallback
    return max(0.0, jd_tt - float(epoch))


def _observer_offset_au(location, t):
    """The observer's position relative to the GEOCENTRE, in AU on ICRS axes.

    Zero for the geocentric observer ``solar_system._observer`` hands back to a
    caller without ``view.site_derived``, which is exactly the point: the site
    is then not an INPUT, so no field of the row can carry it."""
    import astropy.units as u

    pos = location.get_gcrs_posvel(t)[0]
    return tuple(float(c) for c in pos.xyz.to_value(u.AU))


def position(el: dict, when: float | None = None, *,
             site_derived: bool = True) -> dict:
    """Everything measurable about one comet at ``when``.

    Astrometric: the comet's position at the instant the light left it, seen
    from where the observer is NOW. At 1.5 AU that is twelve minutes of travel
    and about ten arcseconds of sky for a fast comet, so it is not optional.
    """
    from astropy.time import Time

    from ..solar_system import _observer

    t_unix = _time.time() if when is None else float(when)
    t = Time(t_unix, format="unix")
    loc, geocentric_reason = _observer(site_derived)
    return _position_at(el, t, t_unix, loc, geocentric_reason)


def _earth_helio_au(t):
    """Earth's heliocentric position (AU, J2000 equatorial axes).

    ``get_body_barycentric`` differences rather than a direct call, because the
    barycentre is the only origin astropy's built-in ephemeris offers and the
    Sun itself moves around it by more than a solar radius."""
    import astropy.units as u
    from astropy.coordinates import get_body_barycentric

    sun = get_body_barycentric("sun", t).xyz.to_value(u.AU)
    earth = get_body_barycentric("earth", t).xyz.to_value(u.AU)
    return tuple(float(earth[i]) - float(sun[i]) for i in range(3))


def _position_at(el: dict, t, t_unix: float, loc, geocentric_reason) -> dict:
    earth = _earth_helio_au(t)
    off = _observer_offset_au(loc, t)
    origin = tuple(earth[i] + off[i] for i in range(3))
    jd_tt = float(t.tt.jd)
    tau = 0.0
    comet = (0.0, 0.0, 0.0)
    r_au = 0.0
    for _ in range(3):                  # down-leg light time, converges in two
        comet, r_au = heliocentric_equatorial_au(el, jd_tt - tau)
        d = tuple(comet[i] - origin[i] for i in range(3))
        delta = math.sqrt(sum(c * c for c in d))
        tau = delta / C_AU_PER_DAY
    d = tuple(comet[i] - origin[i] for i in range(3))
    delta = math.sqrt(sum(c * c for c in d))
    if delta <= 0.0:
        raise EphemerisUnavailable(
            f"{el.get('name', 'comet')}: degenerate geometry")
    ra_hours = (math.degrees(math.atan2(d[1], d[0])) % 360.0) / 15.0
    dec_deg = math.degrees(math.asin(max(-1.0, min(1.0, d[2] / delta))))
    h = el.get("h_mag")
    g = float(el.get("slope_g") or 4.0)
    mag = None
    if h is not None and r_au > 0.0 and delta > 0.0:
        mag = float(h) + 5.0 * math.log10(delta) + 2.5 * g * math.log10(r_au)
    return {
        "ra_hours": ra_hours,
        "dec_deg": dec_deg,
        "r_au": r_au,
        "delta_au": delta,
        "mag": mag,
        "light_time_min": tau * 1440.0,
        "topocentric": geocentric_reason is None,
        "geocentric_reason": geocentric_reason,
        "when_unix": t_unix,
    }


# ------------------------------------------------------------------- the row

def describe(el: dict, p: dict, age_days: float | None) -> str:
    """The row's line. Says how far away it is, how bright the MPC's model
    thinks it will be, and -- because it is the whole reason a comet row can be
    wrong -- whose model that is."""
    parts = [f"{p['delta_au']:.2f} AU from Earth",
             f"{p['r_au']:.2f} AU from the Sun"]
    if p["mag"] is not None:
        parts.append(f"about magnitude {p['mag']:.1f} on the Minor Planet "
                     f"Center's model, which an active comet can beat by a "
                     f"magnitude or two")
    else:
        parts.append("no published brightness, so this row cannot say how "
                     "faint it will look")
    if age_days is not None and age_days > 30.0:
        parts.append(f"elements {age_days:.0f} days old")
    why = p.get("geocentric_reason")
    if why == "site_unset":
        parts.append("geocentric until you set your site (moves it well under "
                     "an arcsecond at this distance)")
    elif why == "not_permitted":
        parts.append("geocentric for your role, which at this distance moves "
                     "it well under an arcsecond")
    return f"{el.get('name') or el.get('id')} - " + ", ".join(parts)


def row(el: dict, when: float | None = None, *,
        site_derived: bool = True, elements_age_days: float | None = None,
        _t=None, _loc=None, _reason=None) -> dict:
    """A catalog row for one comet, in the shape /api/catalog returns."""
    if _t is None:
        p = position(el, when, site_derived=site_derived)
    else:
        p = _position_at(el, _t, float(_t.unix), _loc, _reason)
    out = {
        "id": str(el.get("id") or el.get("name")),
        "name": describe(el, p, elements_age_days),
        "type": "Comet",
        "kind": "comet",
        "ra_hours": p["ra_hours"],
        "dec_deg": p["dec_deg"],
        "mag": round(p["mag"], 2) if p["mag"] is not None else None,
        "size_arcmin": 0.0,
        "r_au": round(p["r_au"], 6),
        "delta_au": round(p["delta_au"], 6),
        "elements_age_days": (None if elements_age_days is None
                              else round(elements_age_days, 3)),
        "ephemeris_unix": p["when_unix"],
        "topocentric": p["topocentric"],
        "geocentric_reason": p["geocentric_reason"],
    }
    if p["topocentric"]:
        # Only for a site_derived caller, and only because a row used outside
        # the /api/catalog route (which adds its own alt/az) should still be
        # self-consistent. A non-holder never reaches this branch: _observer
        # stood at the centre of the Earth, so there is no horizon to be above.
        from ..coords import altaz, round_az_deg
        from ...config import config_store

        site = config_store.cfg().site
        alt, az = altaz(p["ra_hours"], p["dec_deg"], site.latitude,
                        site.longitude, p["when_unix"])
        out["alt"] = round(alt, 1)
        out["az"] = round_az_deg(az)
    return out


# ---------------------------------------------------------------- the searches

#: The most comets one query will ever place. CometEls.txt carries close to a
#: thousand rows and a one-letter query is a substring of hundreds of their
#: names; without this cap a keystroke would cost a thousand two-body solves to
#: then throw all but 25 away (the same lesson ``objects.search`` learned about
#: rendering deep-sky rows before the sort).
MAX_COMET_HITS = 25


def cached_rows() -> list[dict]:
    from . import elements

    env = elements.load(elements.COMETS)
    return list(env["rows"]) if env else []


def search_hits(q: str, qs: str, when: float | None,
                site_derived: bool = True) -> tuple[list[tuple[int, dict]],
                                                    list[str]]:
    """Comet rows for a query, and the reasons for the ones not returned."""
    from astropy.time import Time

    from ..objects import RANK_TYPE, _best, _rank_keys, squash_designation
    from ..solar_system import EphemerisUnavailable, _observer
    from . import elements

    notes: list[str] = []
    if not q:
        return [], notes
    names_kind = len(q) >= 3 and "comets".startswith(q)
    rows = cached_rows()
    if not rows:
        if names_kind:
            state = elements.cache_state(elements.COMETS, when)
            notes.append(state["note"] or elements.NO_COMETS_NOTE)
        return [], notes

    matched: list[tuple[int, float, dict]] = []
    for el in rows:
        keys = [squash_designation(str(el.get("id") or "")),
                squash_designation(str(el.get("name") or ""))]
        rank = _best(_rank_keys(qs, keys), RANK_TYPE if names_kind else None)
        if rank is None:
            continue
        h = el.get("h_mag")
        matched.append((rank, 99.0 if h is None else float(h), el))
    if not matched:
        return [], notes
    # Brightest-first inside each rank, so a wide query answers with the comets
    # somebody might actually see rather than the first thousand alphabetically.
    matched.sort(key=lambda m: (m[0], m[1], str(m[2].get("id"))))
    matched = matched[:MAX_COMET_HITS]

    state = elements.cache_state(elements.COMETS, when)
    t_unix = _time.time() if when is None else float(when)
    t = Time(t_unix, format="unix")
    jd_tt = float(t.tt.jd)
    loc, reason = _observer(site_derived)

    hits: list[tuple[int, dict]] = []
    for rank, _h, el in matched:
        try:
            hits.append((rank, row(
                el, t_unix, site_derived=site_derived,
                elements_age_days=elements_age_days(el, jd_tt,
                                                    state["age_days"]),
                _t=t, _loc=loc, _reason=reason)))
        except EphemerisUnavailable as e:
            log.warning("dropping comet %s from search: %s", el.get("id"), e)
            notes.append(
                f"{el.get('name') or el.get('id')} could not be placed just "
                f"now ({e}). It is left out rather than shown at a guessed "
                f"position.")
    if hits and state["stale"] and state["note"]:
        notes.append(state["note"])
    return hits, notes
