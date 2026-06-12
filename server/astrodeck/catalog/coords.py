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
