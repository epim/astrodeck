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
