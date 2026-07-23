"""Coordinate utilities: parsing, formatting, LST, alt/az."""
from __future__ import annotations

import math
import re
import time


def parse_ra(text: str) -> float:
    """Parse RA to hours. Accepts '5h 35m 17s', '05:35:17', '5.5883' (hours)."""
    text = text.strip()
    m = re.match(r"^(\d+)[h:\s]+(\d+)[m:\s]+([\d.]+)s?$", text)
    if m:
        h, mn, s = float(m[1]), float(m[2]), float(m[3])
        return h + mn / 60 + s / 3600
    m = re.match(r"^(\d+)[h:\s]+([\d.]+)m?$", text)
    if m:
        return float(m[1]) + float(m[2]) / 60
    return float(text)


def parse_dec(text: str) -> float:
    """Parse Dec to degrees. Accepts \"-5° 23' 28\"\", '-05:23:28', '-5.391'."""
    text = text.strip().replace("°", " ").replace("'", " ").replace('"', " ")
    sign = -1.0 if text.lstrip().startswith("-") else 1.0
    body = text.strip("+- \t")
    m = re.match(r"^(\d+)[:\s]+(\d+)[:\s]+([\d.]+)$", body)
    if m:
        d, mn, s = float(m[1]), float(m[2]), float(m[3])
        return sign * (d + mn / 60 + s / 3600)
    m = re.match(r"^(\d+)[:\s]+([\d.]+)$", body)
    if m:
        return sign * (float(m[1]) + float(m[2]) / 60)
    return float(text)


def format_ra(hours: float) -> str:
    hours = hours % 24
    h = int(hours)
    m = int((hours - h) * 60)
    s = ((hours - h) * 60 - m) * 60
    return f"{h:02d}h {m:02d}m {s:04.1f}s"


def format_dec(deg: float) -> str:
    sign = "-" if deg < 0 else "+"
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = ((deg - d) * 60 - m) * 60
    return f"{sign}{d:02d}° {m:02d}' {s:04.1f}\""


def format_ra_fits(hours: float) -> str:
    """FITS OBJCTRA convention: space-separated 'HH MM SS.s' (J2000).

    Rollover-safe (mirrors format_dec_fits): quantize to tenths of a second of
    time BEFORE splitting, so a value like 05:35:59.97 carries into '05 36 00.0'
    instead of emitting the invalid '05 35 60.0', and 23:59:59.97 wraps to
    '00 00 00.0' rather than '24 00 00.0'."""
    tenths = int(round((hours % 24.0) * 3600.0 * 10.0))   # tenths of a second of time
    tenths %= 24 * 3600 * 10                               # 24h wrap after rounding
    h, rem = divmod(tenths, 3600 * 10)
    m, rem = divmod(rem, 60 * 10)
    return f"{h:02d} {m:02d} {rem // 10:02d}.{rem % 10:01d}"


def format_dec_fits(deg: float) -> str:
    """FITS OBJCTDEC convention: space-separated '+DD MM SS' (J2000)."""
    sign = "-" if deg < 0 else "+"
    total = int(round(abs(deg) * 3600.0))      # arcsec, rollover-safe
    d, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{d:02d} {m:02d} {s:02d}"


def airmass(alt_deg: float) -> float | None:
    """Relative optical airmass from apparent altitude, Kasten & Young (1989).
    Returns None at or below the horizon (the formula diverges) so the caller
    omits the AIRMASS card rather than write a bogus value."""
    if alt_deg is None or alt_deg <= 0.0:
        return None
    h = float(alt_deg)
    return 1.0 / (math.sin(math.radians(h)) + 0.50572 * (6.07995 + h) ** -1.6364)


def lst_hours(longitude_deg: float, unix_time: float | None = None) -> float:
    """Local sidereal time in hours (good to ~1s, fine for goto/transit UX)."""
    t = unix_time if unix_time is not None else time.time()
    jd = t / 86400.0 + 2440587.5
    d = jd - 2451545.0
    gmst = (18.697374558 + 24.06570982441908 * d) % 24.0
    return (gmst + longitude_deg / 15.0) % 24.0


def altaz(ra_hours: float, dec_deg: float, lat_deg: float, lon_deg: float,
          unix_time: float | None = None) -> tuple[float, float]:
    """Return (altitude_deg, azimuth_deg)."""
    lst = lst_hours(lon_deg, unix_time)
    ha = math.radians((lst - ra_hours) * 15.0)
    dec = math.radians(dec_deg)
    lat = math.radians(lat_deg)
    sin_alt = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(ha)
    alt = math.asin(max(-1, min(1, sin_alt)))
    cos_az = (math.sin(dec) - math.sin(alt) * math.sin(lat)) / max(1e-9, math.cos(alt) * math.cos(lat))
    az = math.acos(max(-1, min(1, cos_az)))
    if math.sin(ha) > 0:
        az = 2 * math.pi - az
    return math.degrees(alt), math.degrees(az)


# --------------------------------------------------------------- solar position

def sun_radec(unix_time: float | None = None) -> tuple[float, float]:
    """Apparent (RA hours, Dec degrees) of the Sun.

    Low-precision NOAA solar-position algorithm (good to ~0.1°, plenty for the
    twilight / dark-window novice sanity check). No external deps.
    """
    t = unix_time if unix_time is not None else time.time()
    jd = t / 86400.0 + 2440587.5
    d = jd - 2451545.0                       # days since J2000.0
    # mean longitude and mean anomaly of the Sun (degrees)
    g = math.radians((357.529 + 0.98560028 * d) % 360.0)
    q = (280.459 + 0.98564736 * d) % 360.0
    # ecliptic longitude (degrees)
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360.0)
    # obliquity of the ecliptic (degrees)
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))   # radians
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra_hours = (math.degrees(ra) % 360.0) / 15.0
    return ra_hours, math.degrees(dec)


def sun_altaz(lat_deg: float, lon_deg: float,
              unix_time: float | None = None) -> tuple[float, float]:
    """Return the Sun's (altitude_deg, azimuth_deg) for a site.

    Drives the Settings "Sun: −14° · nautical twilight" readout — a sanity check
    that visibly breaks if a longitude sign is flipped (the Sun lands on the
    wrong continent). Works with zero devices connected.
    """
    ra, dec = sun_radec(unix_time)
    return altaz(ra, dec, lat_deg, lon_deg, unix_time)


# ---------------------------------------------------------------- lunar position

def angular_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    """Angular separation (deg) between two (RA hours, Dec deg) points — the
    shared spherical-law-of-cosines helper (promoted from hub._ang_sep_deg)."""
    ra1, ra2 = math.radians(ra1_h * 15.0), math.radians(ra2_h * 15.0)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def moon_radec(unix_time: float | None = None) -> tuple[float, float]:
    """Apparent (RA hours, Dec degrees) of the Moon — low precision (≲0.5°).

    Truncated lunar theory (Schlyter orbital elements + the main longitude /
    latitude perturbation terms). Sufficient for a degrees-wide moon-separation
    gate; the pure scheduler path stays astropy-free (one sky source = coords)."""
    t = unix_time if unix_time is not None else time.time()
    d = t / 86400.0 + 2440587.5 - 2451543.5      # days since 2000 Jan 0.0 (Schlyter epoch)

    def rad(x: float) -> float:
        return math.radians(x % 360.0)

    # Moon orbital elements (degrees).
    N = 125.1228 - 0.0529538083 * d              # long. ascending node
    i = 5.1454                                    # inclination
    w = 318.0634 + 0.1643573223 * d              # arg. of perigee
    a = 60.2666                                   # mean distance (Earth radii)
    e = 0.054900                                  # eccentricity
    M = 115.3654 + 13.0649929509 * d             # mean anomaly
    # Sun elements needed for perturbations.
    Ms = 356.0470 + 0.9856002585 * d
    ws = 282.9404 + 4.70935e-5 * d
    Ls = (ws + Ms) % 360.0                        # Sun mean longitude
    Lm = (N + w + M) % 360.0                       # Moon mean longitude
    D = Lm - Ls                                    # mean elongation
    F = Lm - N                                      # argument of latitude

    # Eccentric anomaly (two iterations — ample at this precision).
    Mr = rad(M)
    E = Mr + e * math.sin(Mr) * (1.0 + e * math.cos(Mr))
    for _ in range(2):
        E = E - (E - e * math.sin(E) - Mr) / (1.0 - e * math.cos(E))

    # Position in the orbital plane, then true anomaly + radius (Earth radii).
    xv = a * (math.cos(E) - e)
    yv = a * (math.sqrt(1.0 - e * e) * math.sin(E))
    v = math.atan2(yv, xv)
    r = math.hypot(xv, yv)

    # Geocentric ecliptic rectangular coords.
    vN, vi, vw = rad(N), math.radians(i), math.radians(w)
    xh = r * (math.cos(vN) * math.cos(v + vw) - math.sin(vN) * math.sin(v + vw) * math.cos(vi))
    yh = r * (math.sin(vN) * math.cos(v + vw) + math.cos(vN) * math.sin(v + vw) * math.cos(vi))
    zh = r * (math.sin(v + vw) * math.sin(vi))

    lon = math.degrees(math.atan2(yh, xh))
    lat = math.degrees(math.atan2(zh, math.hypot(xh, yh)))

    # Main perturbations (degrees) — Schlyter.
    Dr, Mr2, Msr, Fr = rad(D), rad(M), rad(Ms), rad(F)
    lon += (-1.274 * math.sin(Mr2 - 2 * Dr) + 0.658 * math.sin(2 * Dr)
            - 0.186 * math.sin(Msr) - 0.059 * math.sin(2 * Mr2 - 2 * Dr)
            - 0.057 * math.sin(Mr2 - 2 * Dr + Msr) + 0.053 * math.sin(Mr2 + 2 * Dr)
            + 0.046 * math.sin(2 * Dr - Msr) + 0.041 * math.sin(Mr2 - Msr)
            - 0.035 * math.sin(Dr) - 0.031 * math.sin(Mr2 + Msr)
            - 0.015 * math.sin(2 * Fr - 2 * Dr) + 0.011 * math.sin(Mr2 - 4 * Dr))
    lat += (-0.173 * math.sin(Fr - 2 * Dr) - 0.055 * math.sin(Mr2 - Fr - 2 * Dr)
            - 0.046 * math.sin(Mr2 + Fr - 2 * Dr) + 0.033 * math.sin(Fr + 2 * Dr)
            + 0.017 * math.sin(2 * Mr2 + Fr))

    # Ecliptic -> equatorial.
    lam, bet = math.radians(lon), math.radians(lat)
    eps = math.radians(23.4393 - 3.563e-7 * d)
    xe = math.cos(lam) * math.cos(bet)
    ye = math.sin(lam) * math.cos(bet) * math.cos(eps) - math.sin(bet) * math.sin(eps)
    ze = math.sin(lam) * math.cos(bet) * math.sin(eps) + math.sin(bet) * math.cos(eps)
    ra = math.atan2(ye, xe)
    dec = math.atan2(ze, math.hypot(xe, ye))
    return (math.degrees(ra) % 360.0) / 15.0, math.degrees(dec)


def moon_altaz(lat_deg: float, lon_deg: float,
               unix_time: float | None = None) -> tuple[float, float]:
    """The Moon's (altitude_deg, azimuth_deg) for a site."""
    ra, dec = moon_radec(unix_time)
    return altaz(ra, dec, lat_deg, lon_deg, unix_time)


def moon_illumination(unix_time: float | None = None) -> float:
    """Illuminated fraction of the Moon's disk, 0..1, from the geocentric
    Sun–Moon elongation: k = (1 - cos elong)/2 (new=0, full=1)."""
    sra, sdec = sun_radec(unix_time)
    mra, mdec = moon_radec(unix_time)
    elong = math.radians(angular_sep_deg(sra, sdec, mra, mdec))
    return (1.0 - math.cos(elong)) / 2.0


def dark_window(lat_deg: float, lon_deg: float,
                unix_time: float | None = None,
                sun_below_deg: float = -18.0) -> dict | None:
    """Next astronomical-dark interval (Sun < ``sun_below_deg``) within ~24h.

    Returns ``{"start_iso", "end_iso"}`` (UTC, ``Z`` suffix) or ``None`` when the
    Sun never drops that low in the next day (high-latitude summer). Coarse
    10-minute sampling — precision is irrelevant for the "Astro-dark 22:48 →
    04:12" readout.
    """
    now = unix_time if unix_time is not None else time.time()
    step = 600.0                              # 10-minute samples
    horizon = 24 * 3600.0
    start_t: float | None = None
    n = int(horizon / step) + 1
    for i in range(n):
        ts = now + i * step
        alt, _ = sun_altaz(lat_deg, lon_deg, ts)
        if alt < sun_below_deg:
            if start_t is None:
                start_t = ts
        elif start_t is not None:
            return {"start_iso": _iso_utc(start_t), "end_iso": _iso_utc(ts)}
    if start_t is not None:
        # dark right to the end of our window — report what we have
        return {"start_iso": _iso_utc(start_t), "end_iso": _iso_utc(now + horizon)}
    return None


def _iso_utc(unix_time: float) -> str:
    import datetime as _dt
    return (_dt.datetime.fromtimestamp(unix_time, _dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def place_hint(lat_deg: float, lon_deg: float) -> str:
    """A coarse, offline hemisphere/region label from lat/lon sign + magnitude.

    The cheap, network-free novice sanity check that catches a flipped sign — no
    map-tile fetch (a field Pi is often offline). E.g.
    "N hemisphere · W longitude · ~N. America".
    """
    ns = "N hemisphere" if lat_deg >= 0 else "S hemisphere"
    ew = "E longitude" if lon_deg >= 0 else "W longitude"
    region = _region_hint(lat_deg, lon_deg)
    parts = [ns, ew]
    if region:
        parts.append(f"~{region}")
    return " · ".join(parts)


def _region_hint(lat: float, lon: float) -> str:
    """Very coarse continental guess from a lat/lon box. Decorative; never
    load-bearing for any sky math."""
    if -170 <= lon <= -50 and 15 <= lat <= 72:
        return "N. America"
    if -85 <= lon <= -30 and -56 <= lat < 15:
        return "S. America"
    if -25 <= lon <= 45 and 35 <= lat <= 72:
        return "Europe"
    if -20 <= lon <= 52 and -36 <= lat < 35:
        return "Africa"
    if 45 <= lon <= 150 and 5 <= lat <= 75:
        return "Asia"
    if 110 <= lon <= 180 and -50 <= lat < 5:
        return "Australia / Oceania"
    return ""
