"""Cloud occlusion geometry (stage 1) -- pure spherical coordinate geometry.

No I/O, no config, no network, no rig, no numpy. Every function total and
deterministic; stdlib ``math`` only.

Coordinate contract (design section 5), pinned because every plausible bug in
this module is a convention error:

- azimuth: degrees from TRUE NORTH, increasing toward EAST (0=N, 90=E, 180=S,
  270=W). Matches ``mount.az`` in status.
- altitude: degrees above the horizon; valid domain ``0 < alt <= 90``.
- longitude: degrees, east positive, normalised to ``(-180, +180]``.
- heights: kilometres above MEAN SEA LEVEL, for both the site and cloud layers.
  Ceilometers report AGL; convert explicitly with :func:`agl_to_msl_km`. No
  function here silently accepts AGL.
- distances: kilometres, every name suffixed ``_km``.
- angles: degrees, every name suffixed ``_deg``.
- earth: SPHERE, R = 6371.0088 km (IUGG mean). The local ellipsoid radius at
  40 deg is ~6369.4 km, a 2.5e-4 relative error (8.6 m over a 34 km slant)
  against a >=2000 m satellite pixel. WGS84 buys nothing here.

The design is one primitive and one constrained inverse that validate each
other: :func:`look_from` turns any (site -> target) pair into (alt, az, slant),
:func:`pierce_point` inverts it against a spherical shell. The satellite is not
a special case in code -- :func:`satellite_look` is :func:`look_from` with the
target placed at the geostationary radius over the equator, which removes the
class of bug where a separate satellite routine disagrees with the main one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "EARTH_RADIUS_KM",
    "GEOSTATIONARY_RADIUS_KM",
    "GEOSTATIONARY_ALT_KM",
    "Site",
    "GeoPoint",
    "LookVector",
    "look_from",
    "pierce_point",
    "slant_to_layer_km",
    "satellite_look",
    "apparent_point",
    "deparallax",
    "beam_footprint_km",
    "agl_to_msl_km",
]

EARTH_RADIUS_KM: float = 6371.0088
GEOSTATIONARY_RADIUS_KM: float = 42164.0
GEOSTATIONARY_ALT_KM: float = GEOSTATIONARY_RADIUS_KM - EARTH_RADIUS_KM


@dataclass(frozen=True)
class Site:
    """An observing location. ``elev_km`` is above mean sea level."""

    lat_deg: float
    lon_deg: float
    elev_km: float = 0.0


@dataclass(frozen=True)
class GeoPoint:
    """A point in the atmosphere (or above it), height above mean sea level."""

    lat_deg: float
    lon_deg: float
    height_msl_km: float


@dataclass(frozen=True)
class LookVector:
    """A line of sight from a site: horizon coordinates plus range."""

    alt_deg: float
    az_deg: float
    slant_km: float


def _clamp_unit(x: float) -> float:
    """Clamp to [-1, 1] so rounding cannot push asin out of its domain.

    Every argument this guards is mathematically in [-1, 1] already -- a
    direction cosine, or ``cos`` of the angle between two unit vectors -- so
    this is a float-rounding guard, NOT the silent clamping of an
    out-of-domain input that design section 8.7 forbids. It never engages at
    any tested input; if it ever did, the caller has a real bug upstream.
    """
    if x < -1.0:
        return -1.0
    if x > 1.0:
        return 1.0
    return x


def _normalise_lon_deg(lon_deg: float) -> float:
    """Normalise a longitude to the contract range ``(-180, +180]``."""
    wrapped = (lon_deg + 180.0) % 360.0 - 180.0
    if wrapped == -180.0:
        return 180.0
    return wrapped


def _require_altitude(alt_deg: float) -> None:
    if not alt_deg > 0.0 or alt_deg > 90.0:
        raise ValueError(
            "alt_deg must satisfy 0 < alt <= 90, got "
            + repr(alt_deg)
            + " degrees"
        )


def _require_layer_above(layer_msl_km: float, observer_elev_km: float) -> None:
    if layer_msl_km <= observer_elev_km:
        raise ValueError(
            "layer_msl_km "
            + repr(layer_msl_km)
            + " is at or below the observer at "
            + repr(observer_elev_km)
            + " km MSL"
        )


def _ecef_km(
    lat_deg: float, lon_deg: float, height_msl_km: float
) -> tuple[float, float, float]:
    """Spherical earth-centred cartesian position, kilometres."""
    r = EARTH_RADIUS_KM + height_msl_km
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    return (
        r * math.cos(phi) * math.cos(lam),
        r * math.cos(phi) * math.sin(lam),
        r * math.sin(phi),
    )


def slant_to_layer_km(
    alt_deg: float, layer_msl_km: float, site_elev_km: float = 0.0
) -> float:
    """Range along the line of sight from the site out to a spherical shell.

    With ``Rs = R + site_elev_km`` and ``top = R + layer_msl_km``, this is the
    positive root of ``s^2 + 2*Rs*sin(a)*s + Rs^2 - top^2 = 0``::

        s = -Rs*sin(a) + sqrt((Rs*sin(a))^2 + top^2 - Rs^2)

    Exact on a sphere. Two wrong forms, and they are different mistakes: the
    flat-earth SLANT is ``h / sin(alt)``, while ``h / tan(alt)`` is the
    horizontal ground track and not a range along the line of sight at all.
    At alt 15 deg under a 9 km layer the true slant is 34.438 km, the
    flat-earth slant 34.773 km and the ground track 33.588 km.
    """
    _require_altitude(alt_deg)
    _require_layer_above(layer_msl_km, site_elev_km)

    rs = EARTH_RADIUS_KM + site_elev_km
    top = EARTH_RADIUS_KM + layer_msl_km
    rs_sin_a = rs * math.sin(math.radians(alt_deg))
    return -rs_sin_a + math.sqrt(rs_sin_a * rs_sin_a + top * top - rs * rs)


def pierce_point(
    site: Site, alt_deg: float, az_deg: float, layer_msl_km: float
) -> GeoPoint:
    """Where the line of sight (alt, az) crosses a cloud layer.

    The returned point sits on the shell at ``layer_msl_km``; its ground track
    can be a long way downrange -- 15.6 km for cirrus at alt 30 deg, which is
    the whole reason this module exists.
    """
    _require_altitude(alt_deg)
    _require_layer_above(layer_msl_km, site.elev_km)

    s = slant_to_layer_km(alt_deg, layer_msl_km, site.elev_km)
    a = math.radians(alt_deg)
    top = EARTH_RADIUS_KM + layer_msl_km

    # Earth-central angle between the site and the pierce point.
    psi = math.asin(_clamp_unit(s * math.cos(a) / top))

    phi = math.radians(site.lat_deg)
    lam = math.radians(site.lon_deg)
    az = math.radians(az_deg)

    lat2 = math.asin(
        _clamp_unit(
            math.sin(phi) * math.cos(psi)
            + math.cos(phi) * math.sin(psi) * math.cos(az)
        )
    )
    lon2 = lam + math.atan2(
        math.sin(az) * math.sin(psi) * math.cos(phi),
        math.cos(psi) - math.sin(phi) * math.sin(lat2),
    )
    return GeoPoint(
        lat_deg=math.degrees(lat2),
        lon_deg=_normalise_lon_deg(math.degrees(lon2)),
        height_msl_km=layer_msl_km,
    )


def look_from(site: Site, target: GeoPoint) -> LookVector:
    """Horizon coordinates and range of ``target`` as seen from ``site``.

    Both points go to ECEF; the difference vector is projected onto the local
    ENU basis at the site. Exact inverse of :func:`pierce_point`.
    """
    sx, sy, sz = _ecef_km(site.lat_deg, site.lon_deg, site.elev_km)
    tx, ty, tz = _ecef_km(
        target.lat_deg, target.lon_deg, target.height_msl_km
    )
    vx, vy, vz = tx - sx, ty - sy, tz - sz

    # A target coincident with the site has no bearing; the ZeroDivisionError
    # that follows is deliberate. Design section 8.3 specifies no guard here,
    # and inventing az=0 for a zero-length vector would be indistinguishable
    # from a genuine due-north look.
    slant_km = math.sqrt(vx * vx + vy * vy + vz * vz)
    ux, uy, uz = vx / slant_km, vy / slant_km, vz / slant_km

    phi = math.radians(site.lat_deg)
    lam = math.radians(site.lon_deg)
    up = (
        math.cos(phi) * math.cos(lam),
        math.cos(phi) * math.sin(lam),
        math.sin(phi),
    )
    east = (-math.sin(lam), math.cos(lam), 0.0)
    north = (
        -math.sin(phi) * math.cos(lam),
        -math.sin(phi) * math.sin(lam),
        math.cos(phi),
    )

    d_up = ux * up[0] + uy * up[1] + uz * up[2]
    d_east = ux * east[0] + uy * east[1] + uz * east[2]
    d_north = ux * north[0] + uy * north[1] + uz * north[2]

    return LookVector(
        alt_deg=math.degrees(math.asin(_clamp_unit(d_up))),
        az_deg=math.degrees(math.atan2(d_east, d_north)) % 360.0,
        slant_km=slant_km,
    )


def satellite_look(site: Site, sat_lon_deg: float) -> LookVector:
    """Look vector to a geostationary satellite at ``sat_lon_deg``.

    Deliberately one line: the satellite is not a special case, so there is no
    second geometry implementation that can drift out of agreement with
    :func:`look_from`.
    """
    return look_from(site, GeoPoint(0.0, sat_lon_deg, GEOSTATIONARY_ALT_KM))


#: Newton steps for :func:`apparent_point`. The displacement is smooth and
#: about 5 km against a 111 km degree, so two steps reach sub-millimetre and
#: this is a runaway guard rather than a budget.
_APPARENT_MAX_STEPS = 8


def deparallax(
    reported_lat_deg: float,
    reported_lon_deg: float,
    cloud_msl_km: float,
    sat_lon_deg: float,
) -> GeoPoint:
    """Correct a satellite cloud pixel for its parallax displacement.

    The satellite product geolocates a pixel by intersecting its line of sight
    with the GROUND, so a cloud at height h is reported displaced AWAY from the
    sub-satellite point. The correction moves the point back TOWARD the
    satellite -- that sign is the likely bug, and a named test pins it.

    The reported ground point, the true cloud and the satellite are collinear
    by construction: the ground point IS where the ray through the cloud meets
    the sphere. So walking back up that same ray to the cloud shell recovers
    the cloud exactly, and that is one call to :func:`pierce_point` -- no
    second geometry, and no approximation to budget for.

    DEVIATION FROM DESIGN SECTION 8.5, deliberate. The design specifies a
    shift of ``h * tan(zenith)`` along a great circle, which is the flat-earth
    form of this ray walk. It leaves a residual that grows with zenith angle:
    20 m at 46 deg, 42 m at 57 deg, 108 m at 67 deg, 283 m at 74 deg, against
    the design's stated ~20 m. Verified by forward ray-trace -- place a cloud
    at height h, cast the ray from the geostationary position through it, take
    the ground intersection as the reported pixel, and correct it back. The
    form below returns the cloud to within 1e-9 m at every zenith angle; the
    magnitude still agrees with ``h * tan(zenith)`` to better than 0.3 percent,
    so the design's intuition for the size of the shift stands. This also
    inherits :func:`pierce_point`'s domain guards: a satellite below the
    horizon at the reported point, or a negative cloud height, now raise
    instead of silently sliding the point backwards along the bearing.
    Design section 9.4's h=9.0 golden vector must be amended to match.
    """
    reported = Site(reported_lat_deg, reported_lon_deg, 0.0)
    look = satellite_look(reported, sat_lon_deg)
    return pierce_point(reported, look.alt_deg, look.az_deg, cloud_msl_km)


def apparent_point(
    true_lat_deg: float,
    true_lon_deg: float,
    cloud_msl_km: float,
    sat_lon_deg: float,
) -> GeoPoint:
    """Where the satellite REPORTS a cloud that is really at this position.

    The exact inverse of :func:`deparallax`, and the direction stage 4 needs.
    ``deparallax`` answers "the product says a pixel is here, where is the
    cloud"; this answers "the cloud is here, which pixel do I read". They move
    opposite ways -- deparallax TOWARD the sub-satellite point, this one AWAY
    -- and using the wrong one does not merely fail to correct the error, it
    DOUBLES it.

    THIS WAS THE BUG. ``occlusion_at`` took ``pierce_point``'s output -- the
    true ground position under the cloud -- and handed it straight to
    ``lonlat_to_index``, reading the pixel at the cloud's true position rather
    than at its imaged one. Measured on the rig 2026-08-26 with GOES-18 at
    43.8 deg altitude: a 5.2 km cloud deck is displaced 5.4 km, about two mask
    cells, always away from the satellite. The operator reported it as "we're
    offset a bit", which is exactly the shape of a missing parallax term.

    ``deparallax`` was written, exported and covered by two named tests --
    including one pinning its magnitude as h*tan(zenith) -- and called by
    nothing outside those tests. A correction that ships in the package and
    never runs on the path it exists for.

    SOLVED BY ITERATING THE EXACT WALK, not by a closed form. ``deparallax``
    deliberately rejects the design's ``h * tan(zenith)`` shift because the
    flat-earth residual grows with zenith angle (283 m at 74 deg), and writing
    the inverse in that form would reintroduce precisely the error its own
    docstring measured. Newton on a displacement this smooth converges in two
    or three steps; each one is a single call to the proven walk, so this
    inherits its exactness and its domain guards instead of restating them.
    """
    # SEEDED WITH THE ANALYTIC SHIFT, then Newton'd. Starting from the true
    # point instead costs three iterations of the exact walk per lookup, and
    # this runs once per ray per rung: measured, that put a full dome at 5.0 s
    # against its own 3.0 s budget. The flat-earth h*tan(zenith) displacement
    # is within about 0.4 percent, so one step lands on the answer and the
    # loop below exits immediately -- exactness kept, two thirds of the work
    # gone. The seed being approximate does not matter: it is only where the
    # iteration STARTS, and the convergence test is unchanged.
    look = satellite_look(Site(true_lat_deg, true_lon_deg, 0.0), sat_lon_deg)
    shift_km = cloud_msl_km * math.tan(math.radians(90.0 - look.alt_deg))
    away_rad = math.radians((look.az_deg + 180.0) % 360.0)
    lat = true_lat_deg + (shift_km * math.cos(away_rad)) / 110.574
    lon = true_lon_deg + (shift_km * math.sin(away_rad)) / (
        111.320 * max(1e-6, math.cos(math.radians(true_lat_deg))))
    for _ in range(_APPARENT_MAX_STEPS):
        back = deparallax(lat, lon, cloud_msl_km, sat_lon_deg)
        dlat = true_lat_deg - back.lat_deg
        dlon = true_lon_deg - back.lon_deg
        lat += dlat
        lon += dlon
        # A degree is ~111 km, so this is sub-metre in both axes. Tested by
        # round-trip rather than trusted: see test_apparent_point_round_trips.
        if abs(dlat) < 1e-9 and abs(dlon) < 1e-9:
            break
    return GeoPoint(lat, lon, cloud_msl_km)


def beam_footprint_km(slant_km: float, fov_deg: float) -> float:
    """Diameter of the telescope beam at range ``slant_km``.

    HALF ANGLE: ``2 * slant * tan(fov/2)``. ``slant * tan(fov)`` is wrong by
    about 2x and nearly indistinguishable at the ~1.7 deg fields this module is
    used with, so only a large-fov test catches it.
    """
    if not fov_deg > 0.0 or fov_deg >= 180.0:
        raise ValueError(
            "fov_deg must satisfy 0 < fov < 180, got "
            + repr(fov_deg)
            + " degrees"
        )
    return 2.0 * slant_km * math.tan(math.radians(fov_deg) / 2.0)


def agl_to_msl_km(agl_km: float, ground_elev_km: float) -> float:
    """Convert an above-ground-level height (as ceilometers report) to MSL."""
    return agl_km + ground_elev_km
