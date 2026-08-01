"""Sun, Moon and the planets — computed for a time and a place, never stored.

WHY THIS IS NOT A TABLE. Every other row in this package is a fixed J2000
coordinate that is true forever. A planet's is true for an instant: the Moon
moves its own diameter in an hour, Mars crosses a finder field in a day. A
static row for "Mars" would look exactly like a real target and point the mount
at empty sky — a wrong position is worse than a missing one, so these rows are
built from an ephemeris on every search and carry the timestamp they were built
for.

WHAT IS COMPUTED, AND HOW GOOD IT IS. astropy's built-in ephemeris (ERFA
plan94 / moon98 — no download, works on a field Pi with no network). Checked
against JPL Horizons for 2026-07-31 04:00 UTC at Greenwich:

    body      position error   apparent diameter   magnitude (ours / Horizons)
    Moon           17"          1822.33" exact       -12.29 / -12.19
    Mercury        19"             8.12" exact         0.45 /   0.45
    Venus          16"            20.68" exact        -4.28 /  -4.28
    Mars           17"             4.68" exact         1.31 /   1.36
    Jupiter        38"            31.29" exact        -1.78 /  -1.78
    Saturn         19"            18.45" exact         0.88 /   0.64
    Uranus         14"             3.54" exact         5.83 /   5.77
    Neptune        15"             2.33" exact         7.71 /   7.71

The residual is annual aberration plus light deflection, both of which are
below any pointing error this rig can achieve. Saturn is the one loose end: the
magnitude model is the GLOBE only, so with the rings open it reads up to ~0.5
mag too faint. It is labelled, not silently wrong.

THE ICRS TRAP, because it cost an afternoon: do NOT ``transform_to(ICRS())``
these coordinates. ICRS is BARYCENTRIC, so for an object with a real distance
astropy dutifully re-plots it as seen from the solar-system barycentre — which
moved the Moon 17 DEGREES in testing. ``get_body`` already returns GCRS, whose
axes ARE the ICRS/J2000 axes with the observer at the origin, so its ra/dec is
already in the same frame as the fixed catalog and the hub's J2000 -> JNOW
precession applies to it unchanged.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

log = logging.getLogger(__name__)

AU_KM = 149597870.7


class EphemerisUnavailable(RuntimeError):
    """The ephemeris could not be evaluated. Raised instead of returning a
    plausible-looking position — the caller drops the body and says nothing
    rather than pointing a telescope at a guess."""


@dataclass(frozen=True)
class Body:
    key: str                    # the name astropy's get_body() knows
    label: str                  # what the row is called, e.g. "Mars"
    type_name: str              # "Planet" / "Moon" / "Sun"
    radius_km: float            # equatorial, for the apparent-diameter figure
    abs_mag: float | None       # H in V = H + 5log10(r*delta) + phase(alpha)
    aliases: tuple[str, ...] = ()


# Mercury..Neptune are what ERFA's plan94 covers; Pluto needs a downloaded JPL
# kernel, so it is deliberately absent rather than approximated.
#
# H values and the phase polynomials below are Mallama & Hilton (2018), the same
# model the Astronomical Almanac publishes. Every one of them is checked in
# test_catalog_solar_system against JPL Horizons.
BODIES: tuple[Body, ...] = (
    Body("sun", "Sun", "Sun", 695700.0, None, ("sol",)),
    Body("moon", "Moon", "Moon", 1737.4, None, ("luna",)),
    Body("mercury", "Mercury", "Planet", 2439.7, -0.613),
    Body("venus", "Venus", "Planet", 6051.8, -4.384),
    Body("mars", "Mars", "Planet", 3396.2, -1.601),
    Body("jupiter", "Jupiter", "Planet", 71492.0, -9.395),
    Body("saturn", "Saturn", "Planet", 60268.0, -8.914),
    Body("uranus", "Uranus", "Planet", 25559.0, -7.110),
    Body("neptune", "Neptune", "Planet", 24764.0, -7.00),
)

BY_KEY: dict[str, Body] = {b.key: b for b in BODIES}

SUN_KEY = "sun"


# Bodies a user WILL type that this ephemeris deliberately does not carry, each
# with the reason it does not. Saying nothing is what put the wrong words on the
# screen in the first place: an empty result set was explained by the browser
# guessing from the query text, and "Pluto" fell through to "the catalog is
# deep-sky only" — which stopped being true the moment this module shipped. The
# rule for tonight's whole repair pass: a component that cannot answer names the
# question it could not answer.
NOT_CARRIED: dict[str, str] = {
    "pluto": ("Pluto is not carried: its position needs a JPL kernel this rig "
              "does not download, and the approximation that works for the "
              "eight planets is wrong for Pluto by more than a finder field."),
    "earth": ("Earth is not a target — you are standing on it. To see where "
              "the mount is pointing, use the Mount screen."),
    "ceres": ("Minor planets are not carried: an asteroid's position needs "
              "orbital elements that go stale, and a stale element set points "
              "the mount at empty sky without saying so."),
}


def not_carried_reason(name: str) -> str | None:
    """Why we have nothing for ``name`` (already lowercased/squashed), or None
    when it is not one of the bodies we deliberately skip."""
    if len(name) < 3:
        return None
    for key, reason in NOT_CARRIED.items():
        if key.startswith(name) or name == key:
            return reason
    return None


# ------------------------------------------------------------------ the Sun gate

def sun_is_offered() -> bool:
    """Is the Sun a legitimate search result right now?

    ONLY when the rig is in a deliberate solar-astronomy session — which is
    exactly the state ``Hub._check_solar`` treats as disarmed: sun avoidance
    switched off, or the exclusion cone zeroed. Both take the admin
    ``config.solar_override`` capability to set. This reads the same two config
    fields the gate reads so there is ONE solar policy, not a search policy and
    a slew policy that can drift apart: if the mount would refuse the slew, the
    search does not offer the target.
    """
    from ..config import config_store

    safety = config_store.cfg().safety
    if not getattr(safety, "solar_avoidance", True):
        return True
    return getattr(safety, "solar_exclusion_deg", 30.0) <= 0


def sun_block_reason() -> str | None:
    """Why the Sun is not in the results, or None when it is.

    A blocked control has to be able to say why it is blocked. The search API
    can only return target rows, so this is the string the caller shows instead
    of a row (and the exact reason the mount would give if the slew were tried
    anyway)."""
    if sun_is_offered():
        return None
    from ..config import config_store

    cone = getattr(config_store.cfg().safety, "solar_exclusion_deg", 30.0)
    return (f"The Sun is not offered as a target: sun avoidance is armed and "
            f"the mount refuses anything within {cone:.0f}° of it. Solar "
            f"observing needs a filtered scope and a solar session "
            f"(Settings → Safety, admin only).")


def offered_bodies() -> tuple[Body, ...]:
    """The bodies a search may return. The Sun is in this list only during a
    solar session; every other body is always in it."""
    return tuple(b for b in BODIES if b.key != SUN_KEY or sun_is_offered())


# --------------------------------------------------------------------- ephemeris

def _observer():
    """(EarthLocation, topocentric?) for the configured site.

    An unconfigured site is lat 0 / lon 0 — a point in the Atlantic, not a
    guess we are entitled to make. For the Moon it is not a harmless one
    either: horizontal parallax reaches ~1°, twice the Moon's own diameter. So
    an unconfigured rig gets the honest GEOCENTRIC position and the row says
    so. For everything further away than the Moon the difference is under an
    arcsecond and the distinction is cosmetic."""
    import astropy.units as u
    from astropy.coordinates import EarthLocation
    from ..config import config_store

    site = config_store.cfg().site
    if getattr(site, "is_default", False):
        return EarthLocation.from_geocentric(0.0, 0.0, 0.0, unit=u.m), False
    return (EarthLocation(lat=site.latitude * u.deg,
                          lon=site.longitude * u.deg,
                          height=site.elevation_m * u.m), True)


def _phase_magnitude(body: Body, r_km: float, delta_km: float,
                     alpha_deg: float) -> float | None:
    """V magnitude from heliocentric distance r, geocentric distance delta and
    phase angle alpha (Mallama & Hilton 2018 polynomials)."""
    a = alpha_deg
    if body.key == "sun":
        return -26.74 + 5.0 * math.log10(delta_km / AU_KM)
    if body.key == "moon":
        # Allen (1976): full moon -12.73, dimming steeply with phase.
        return -12.73 + 0.026 * a + 4.0e-9 * a ** 4
    if body.abs_mag is None:
        return None
    if body.key == "mercury":
        phase = (6.3280e-02 * a - 1.6336e-03 * a ** 2 + 3.3644e-05 * a ** 3
                 - 3.4265e-07 * a ** 4 + 1.6893e-09 * a ** 5
                 - 3.0334e-12 * a ** 6)
    elif body.key == "venus":
        phase = (-1.044e-03 * a + 3.687e-04 * a ** 2 - 2.814e-06 * a ** 3
                 + 8.938e-09 * a ** 4) if a <= 163.7 else (
            -2.81914e00 * a + 8.39034e-03 * a ** 2)
    elif body.key == "mars":
        phase = 0.02267 * a - 0.0001302 * a ** 2
    elif body.key == "jupiter":
        phase = -3.7e-04 * a + 6.16e-04 * a ** 2
    elif body.key == "saturn":
        phase = 0.026 * a          # globe only; the rings are not modelled
    else:
        phase = 0.0                # Uranus / Neptune: alpha never exceeds ~3°
    return body.abs_mag + 5.0 * math.log10(r_km * delta_km / AU_KM ** 2) + phase


def position(key: str, when: float | None = None) -> dict:
    """Everything measurable about one body at ``when`` (unix seconds, now if
    None): J2000-axes RA/Dec, distance, apparent diameter, phase angle,
    illuminated fraction, V magnitude, and whether the site was known.

    Raises :class:`EphemerisUnavailable` rather than inventing a position."""
    import time as _time

    body = BY_KEY[key]
    t_unix = when if when is not None else _time.time()
    try:
        import astropy.units as u
        from astropy.coordinates import (get_body, get_body_barycentric,
                                         get_constellation)
        from astropy.time import Time

        t = Time(t_unix, format="unix")
        loc, topocentric = _observer()
        # GCRS: ICRS/J2000 axes, observer at the origin. See the module docstring
        # on why this must NOT be transformed to ICRS.
        c = get_body(body.key, t, loc)
        ra_hours = float(c.ra.hourangle) % 24.0
        dec_deg = float(c.dec.deg)
        delta_km = float(c.distance.to_value(u.km))

        # Phase angle from the Sun-body-Earth triangle, in barycentric vectors
        # (the only place a heliocentric distance is available at all).
        sun_b = get_body_barycentric("sun", t)
        r_km = float((get_body_barycentric(body.key, t) - sun_b).norm()
                     .to_value(u.km)) if body.key != "sun" else 0.0
        earth_km = float((get_body_barycentric("earth", t) - sun_b).norm()
                         .to_value(u.km))
        if r_km > 0.0 and delta_km > 0.0:
            cos_a = ((r_km ** 2 + delta_km ** 2 - earth_km ** 2)
                     / (2.0 * r_km * delta_km))
            alpha = math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))
        else:
            alpha = 0.0

        constellation = str(get_constellation(c))
        # Waxing vs waning is a longitude comparison, not a brightness one:
        # the Moon's ecliptic longitude leads the Sun's for the whole waxing
        # half (mirrors visibility._moon_info).
        waxing = None
        if body.key == "moon":
            sun_c = get_body("sun", t, loc)
            delta_lon = (c.geocentrictrueecliptic.lon.to_value(u.deg)
                         - sun_c.geocentrictrueecliptic.lon.to_value(u.deg)) % 360.0
            waxing = delta_lon < 180.0
    except EphemerisUnavailable:
        raise
    except Exception as e:                     # noqa: BLE001 - any astropy failure
        raise EphemerisUnavailable(f"{body.label}: {e}") from e

    diam_arcmin = math.degrees(
        2.0 * math.asin(min(1.0, body.radius_km / delta_km))) * 60.0
    return {
        "key": body.key,
        "ra_hours": ra_hours,
        "dec_deg": dec_deg,
        "distance_km": delta_km,
        "size_arcmin": diam_arcmin,
        "phase_angle_deg": alpha,
        "illumination": (1.0 + math.cos(math.radians(alpha))) / 2.0,
        "waxing": waxing,
        "mag": _phase_magnitude(body, r_km, delta_km, alpha),
        "constellation": constellation,
        "topocentric": topocentric,
        "when_unix": t_unix,
    }


# ------------------------------------------------------------------- the row

def _sun_separation_deg(ra_hours: float, dec_deg: float,
                        when: float | None) -> float:
    """Separation from the Sun using the SAME solar position ``Hub._check_solar``
    uses, so a row that says "slews blocked" is stating the gate's own verdict
    rather than a second opinion that could disagree with it."""
    from .coords import angular_sep_deg, sun_radec

    sun_ra, sun_dec = sun_radec(when)
    return angular_sep_deg(ra_hours, dec_deg, sun_ra, sun_dec)


def _exclusion_cone_deg() -> float:
    from ..config import config_store

    safety = config_store.cfg().safety
    if not getattr(safety, "solar_avoidance", True):
        return 0.0
    return float(getattr(safety, "solar_exclusion_deg", 30.0))


def _size_phrase(size_arcmin: float) -> str:
    """A disc a beginner can picture: arcminutes for the Moon and Sun, arcsec
    for the planets (Jupiter is 0.5' — "0.5 arcmin wide" tells nobody
    anything)."""
    if size_arcmin >= 1.0:
        return f"{size_arcmin:.1f}' wide"
    return f'{size_arcmin * 60.0:.1f}" disc'


def describe(body: Body, p: dict, sun_sep_deg: float, cone_deg: float) -> str:
    """The line the Atlas row shows. Every clause is something the user cannot
    see for themselves: where the body is tonight, how big it will look, how
    much of it is lit, and whether the mount is going to refuse to go there."""
    parts: list[str] = []
    if body.key == "moon" and p["waxing"] is not None:
        parts.append(f"{p['illumination'] * 100:.0f}% lit and "
                     f"{'waxing' if p['waxing'] else 'waning'}")
    parts.append(f"in {p['constellation']}")
    parts.append(_size_phrase(p["size_arcmin"]))
    if body.key == "sun":
        parts.append("solar session — needs a filtered scope")
    elif cone_deg > 0.0 and sun_sep_deg < cone_deg:
        # Not a warning about glare: the mount will actually refuse this slew.
        parts.append(f"{sun_sep_deg:.0f}° from the Sun — the mount will refuse "
                     f"this slew")
    if body.key == "moon" and not p["topocentric"]:
        parts.append("geocentric until you set your site (moves it up to 1°)")
    return " · ".join(parts)


def row(key: str, when: float | None = None) -> dict:
    """A catalog row for one body, in the shape /api/catalog returns."""
    body = BY_KEY[key]
    p = position(key, when)
    sun_sep = 0.0 if key == SUN_KEY else _sun_separation_deg(
        p["ra_hours"], p["dec_deg"], when)
    return {
        "id": body.label,
        "name": describe(body, p, sun_sep, _exclusion_cone_deg()),
        "type": body.type_name,
        "kind": "solar_system",
        "ra_hours": p["ra_hours"],
        "dec_deg": p["dec_deg"],
        # A planet has no fixed magnitude; this one is computed for `when_unix`
        # and moves by whole magnitudes over a synodic period.
        "mag": round(p["mag"], 2) if p["mag"] is not None else 0.0,
        "size_arcmin": round(p["size_arcmin"], 3),
        "constellation": p["constellation"],
        "illumination": round(p["illumination"], 3),
        "distance_km": round(p["distance_km"]),
        "sun_separation_deg": round(sun_sep, 1),
        # The two facts that make this row falsifiable: WHEN it was true, and
        # whether we knew where the observer was standing.
        "ephemeris_unix": p["when_unix"],
        "topocentric": p["topocentric"],
    }


def search_keys(body: Body) -> list[str]:
    """Squashed spellings that resolve to this body."""
    from .objects import squash_designation

    return [squash_designation(s) for s in (body.label, *body.aliases)]
