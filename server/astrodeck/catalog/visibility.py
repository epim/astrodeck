"""Tonight's visibility for a target: altitude curve, transit, astro-dark
window, moon track/phase/separation, best imaging window, and a group-atomic
recommended order.

All ephemeris math is astropy (a hard dependency — ``pyproject.toml``): an
``EarthLocation`` from ``hub.site``, the ``AltAz`` frame, ``get_body("moon")``,
``get_sun``, ``astropy.time.Time``. There is no hand-rolled moon/twilight code
(design spec §9 — astropy is strictly more correct and adds no weight).

Returned shapes mirror the frozen TS contracts (``ui/src/types.ts``):
``VisibilityNight`` / ``VisibilitySample`` / ``MoonInfo`` / ``VisibilityTarget``.
Field names downstream lanes (the mosaic reality-check, Owner C, and the
``VisibilityPanel``/``lib/visibility.ts``, Owner D) rely on:

  VisibilityNight: date, transit_unix, transit_alt, transit_in_daylight,
    dark_start_unix, dark_end_unix, darkness_kind ("astronomical"|"nautical"|
    "none"), samples[{t_unix,alt,moon_alt,sun_alt}], moon, best_window
    {start_unix,end_unix,mean_alt}|null, alt_limit_deg, never_rises_above_limit.
  MoonInfo: illumination, phase_name, alt, az, separation_deg, rise_unix|null,
    set_unix|null.
  VisibilityTarget: name, ra_hours, dec_deg, transit_unix, max_alt,
    best_window {start_unix,end_unix}|null, moon_sep_deg.
"""
from __future__ import annotations

import datetime as _dt
import math
import threading
import time
from collections import OrderedDict
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

import astropy.units as u
import warnings

from astropy.coordinates import (
    AltAz,
    EarthLocation,
    SkyCoord,
    get_body,
    get_sun,
)
from astropy.coordinates.baseframe import NonRotationTransformationWarning
from astropy.time import Time

from ..auth import CAP_VIEW_STATUS, require
from ..auth.rbac import declare
from ..hub import hub

router = APIRouter()


def _moon_target_sep_deg(moon, target: SkyCoord):
    """Angular separation (deg) between the (GCRS) moon body and the J2000 target.

    Transforming the topocentric-GCRS moon to ICRS emits astropy's
    ``NonRotationTransformationWarning`` (the transform involves a non-rotation
    component, so "separation can depend on direction"). The dependence is
    sub-arcsecond and irrelevant for a moon-glow advisory, so silence just that
    warning rather than spamming the server log on every visibility call.
    Returns a scalar deg float or a numpy array, mirroring the moon's shape.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NonRotationTransformationWarning)
        return moon.transform_to("icrs").separation(target).to_value(u.deg)

# ----------------------------------------------------------------- twilight ladder
# Sun-altitude ceilings for each darkness grade. astro-dark is the gold standard
# (sun ≤ −18°); when the sun never gets that low tonight (high latitude in June)
# fall back to nautical (≤ −12°), then to the single darkest interval so the
# panel never shows a blank window (design spec §9 / critique C2-#6).
ASTRO_DARK_DEG = -18.0
NAUTICAL_DARK_DEG = -12.0

DEFAULT_STEP_MIN = 10
DEFAULT_ALT_LIMIT = 30.0

# Cap how far we hunt for the night boundaries / moon rise-set so a polar site
# (where the sun never crosses a horizon) can never spin forever (design spec §9
# polar guard). One sidereal-ish day of coarse samples is plenty.
_NIGHT_SEARCH_HOURS = 24.0


# ----------------------------------------------------------------- request models

class OrderTarget(BaseModel):
    name: str
    # Constrain coords at the boundary so astropy never sees |dec|>90 (a bare
    # ValueError that would 500 the whole asyncio.gather batch); pydantic 422s.
    ra_hours: float = Field(ge=0, lt=24)
    dec_deg: float = Field(ge=-90, le=90)
    # A mosaic group keeps its panels atomic in the recommended order (never
    # interleaved with another group); optional so single targets omit it.
    mosaic_group: str | None = None


class OrderBody(BaseModel):
    # Cap the list so a single request can't fan out unbounded compute_night
    # threads (CPU/thread-pool DoS on the Pi; throttled further in post_order).
    targets: list[OrderTarget] = Field(max_length=200)
    date: str | None = None

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: str | None) -> str | None:
        return check_night_date(v)


def check_night_date(date: str | None) -> str | None:
    """Validate a ``YYYY-MM-DD`` night date, or return None for "tonight".

    Constrained at the boundary for the same reason the coords above are: the
    parser underneath (``_night_anchor_unix``) swallows a bad date and falls
    back to TONIGHT, so ``?date=2026-13-45`` used to return tonight's sky
    labelled as the caller's requested night — a silent wrong answer, which is
    worse than an error. An off-by-one month in any client produced plausible
    numbers for the wrong date. Empty string means "unset" (a cleared date
    input posts ``""``), which is the tonight case, not a malformed one.

    Raises ``HTTPException(422)`` so the GET handlers can call it inline;
    pydantic turns the same failure into a 422 for the POST body above.
    """
    if date is None or date == "":
        return None
    # fromisoformat alone would accept "20260724"; the shape check keeps the
    # wire format to the one the docs and the UI's date input both use.
    ok = len(date) == 10 and date[4] == "-" and date[7] == "-"
    if ok:
        try:
            _dt.date.fromisoformat(date)
        except ValueError:
            ok = False
    if not ok:
        raise HTTPException(
            422, f"date must be a real calendar date as YYYY-MM-DD, got {date!r}")
    return date


# ----------------------------------------------------------------- time helpers

def _site_location(site: dict) -> EarthLocation:
    return EarthLocation(
        lat=float(site["latitude"]) * u.deg,
        lon=float(site["longitude"]) * u.deg,
        height=float(site.get("elevation_m", 0.0)) * u.m,
    )


def _night_anchor_unix(date: str | None, lon_deg: float) -> float:
    """Unix epoch of local solar midnight for the requested night.

    ``date`` (``YYYY-MM-DD``, the local civil date of the *evening*) anchors the
    night; when omitted we anchor on "tonight" — the local solar midnight that
    follows the current instant. Longitude gives the local-solar offset so we
    don't need a tz database (sky math is longitude-only, matching coords.py).
    """
    lon_offset_s = lon_deg / 15.0 * 3600.0  # +E ⇒ local solar time ahead of UTC
    if date:
        try:
            y, m, d = (int(x) for x in date.split("-"))
            # Local civil midnight that STARTS the named evening's date, then the
            # night runs into the following solar midnight (~24h later). Anchor on
            # the solar midnight at the END of that civil day (the deep night).
            civil_midnight_utc = _dt.datetime(
                y, m, d, 0, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
            # local solar midnight nearest the following 24h: civil day start in
            # local solar terms is shifted by -lon_offset; the deep night is +24h.
            return civil_midnight_utc - lon_offset_s + 24.0 * 3600.0
        except (ValueError, AttributeError):
            pass
    # tonight: the local-solar midnight of the CURRENT night.
    now = time.time()
    local_now = now + lon_offset_s
    day_frac = (local_now % 86400.0) / 86400.0
    if day_frac < 0.5:
        # local morning/early-afternoon (00:00–12:00) → the current night's
        # midnight is the one that JUST passed, not the next one.
        secs_to_midnight = -day_frac * 86400.0
    else:
        # afternoon/evening (12:00–24:00) → tonight's midnight is ahead.
        secs_to_midnight = (1.0 - day_frac) * 86400.0
    return now + secs_to_midnight


def _sun_alt_series(loc: EarthLocation, times: Time) -> np.ndarray:
    sun = get_sun(times).transform_to(AltAz(obstime=times, location=loc))
    return np.asarray(sun.alt.to_value(u.deg))


def _find_dark_window(
    loc: EarthLocation, anchor_unix: float, lon_deg: float,
) -> tuple[float | None, float | None, str]:
    """The night's usable dark window + its grade.

    Scans a ±12h band around ``anchor_unix`` (local solar midnight) at 5-min
    resolution and returns ``(start_unix, end_unix, kind)``:

    * ``"astronomical"`` — the contiguous run with sun ≤ −18° spanning midnight.
    * ``"nautical"`` — same with sun ≤ −12° when astro-dark never occurs.
    * ``"none"`` — neither grade is reached (high-latitude summer / polar day);
      we fall back to the darkest contiguous interval (lowest sun) so callers
      always get a non-empty window, just honestly labelled.
    """
    half = 12.0 * 3600.0
    step = 300.0
    n = int(2 * half / step) + 1
    unix = anchor_unix - half + np.arange(n) * step
    times = Time(unix, format="unix")
    sun_alt = _sun_alt_series(loc, times)
    mid_idx = n // 2

    def _run_around_mid(threshold: float) -> tuple[int, int] | None:
        if sun_alt[mid_idx] > threshold:
            return None
        lo = mid_idx
        while lo > 0 and sun_alt[lo - 1] <= threshold:
            lo -= 1
        hi = mid_idx
        while hi < n - 1 and sun_alt[hi + 1] <= threshold:
            hi += 1
        return lo, hi

    for thr, kind in ((ASTRO_DARK_DEG, "astronomical"),
                      (NAUTICAL_DARK_DEG, "nautical")):
        run = _run_around_mid(thr)
        if run is not None:
            lo, hi = run
            return float(unix[lo]), float(unix[hi]), kind

    # No graded darkness tonight — pick the darkest contiguous band as the window
    # (a fixed dip below the per-night sun maximum), so the panel still shows
    # *something* and labels it "none" (no astro/nautical dark).
    sun_min = float(sun_alt.min())
    sun_max = float(sun_alt.max())
    if sun_max <= NAUTICAL_DARK_DEG:
        # Pathological (already handled above) — guard anyway.
        return float(unix[0]), float(unix[-1]), "none"
    # band = within 3° of the night's darkest point, contiguous through midnight.
    band_thr = sun_min + 3.0
    run = _run_around_mid(band_thr)
    if run is None:
        # darkest point isn't near midnight (polar day): just bracket the minimum.
        dark_i = int(np.argmin(sun_alt))
        lo = max(0, dark_i - 6)
        hi = min(n - 1, dark_i + 6)
        return float(unix[lo]), float(unix[hi]), "none"
    lo, hi = run
    return float(unix[lo]), float(unix[hi]), "none"


# ----------------------------------------------------------------- moon helpers

def _moon_phase_name(illum: float, waxing: bool) -> str:
    """Human phase name from illuminated fraction + waxing/waning sign."""
    if illum < 0.02:
        return "New Moon"
    if illum > 0.98:
        return "Full Moon"
    if abs(illum - 0.5) < 0.06:
        return "First Quarter" if waxing else "Last Quarter"
    if illum < 0.5:
        return "Waxing Crescent" if waxing else "Waning Crescent"
    return "Waxing Gibbous" if waxing else "Waning Gibbous"


def _moon_info(
    loc: EarthLocation, t_unix: float,
    target: SkyCoord, dark_start: float | None, dark_end: float | None,
    lon_deg: float,
) -> dict[str, Any]:
    """MoonInfo at ``t_unix`` (session time): illumination, signed-phase name,
    alt/az, separation from the target, and moonrise/moonset *within the night*.
    """
    t = Time(t_unix, format="unix")
    frame = AltAz(obstime=t, location=loc)
    moon = get_body("moon", t, loc)
    sun = get_sun(t)
    # Illuminated fraction from the sun–moon elongation (phase angle proxy good to
    # well under a percent for UX). illum = (1 + cos(180° − elong)) / 2. The
    # moon (topocentric GCRS) → sun (geocentric GCRS) separation trips astropy's
    # NonRotationTransformationWarning; the effect is negligible for illumination,
    # so silence just that warning.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NonRotationTransformationWarning)
        elong = moon.separation(sun).to_value(u.rad)
    illum = float((1.0 + math.cos(math.pi - elong)) / 2.0)
    illum = max(0.0, min(1.0, illum))
    # Waxing vs waning: the moon's ecliptic longitude leads the sun's while waxing
    # (0..180° ahead). Compare apparent geocentric longitudes.
    moon_lon = moon.geocentrictrueecliptic.lon.to_value(u.deg)
    sun_lon = sun.geocentrictrueecliptic.lon.to_value(u.deg)
    delta = (moon_lon - sun_lon) % 360.0
    waxing = delta < 180.0

    moon_altaz = moon.transform_to(frame)
    alt = float(moon_altaz.alt.to_value(u.deg))
    az = float(moon_altaz.az.to_value(u.deg))
    # J2000 ICRS separation (target is J2000) — registration invariant.
    sep = float(_moon_target_sep_deg(moon, target))

    rise_unix, set_unix = _moon_rise_set(loc, dark_start, dark_end)

    return {
        "illumination": round(illum, 3),
        "phase_name": _moon_phase_name(illum, waxing),
        "alt": round(alt, 1),
        "az": round(az, 1),
        "separation_deg": round(sep, 1),
        "rise_unix": rise_unix,
        "set_unix": set_unix,
    }


def _moon_info_at(sc: dict, i: int, sep_deg: float) -> dict[str, Any]:
    """The same ``MoonInfo`` as ``_moon_info``, read out of a cached night
    scaffold at sample ``i`` instead of recomputed from scratch.

    Every field is target-independent except the separation, which the caller
    passes in from its own (already vectorised) moon-separation series. Sample
    ``i`` is the target's transit sample, i.e. the identical instant the scalar
    path evaluated at — so this is a lookup, not an approximation.
    """
    illum = float(sc["illum"][i])
    return {
        "illumination": round(illum, 3),
        "phase_name": _moon_phase_name(illum, bool(sc["waxing"][i])),
        "alt": round(float(sc["moon_alt"][i]), 1),
        "az": round(float(sc["moon_az"][i]), 1),
        "separation_deg": round(sep_deg, 1),
        "rise_unix": sc["moon_rise_unix"],
        "set_unix": sc["moon_set_unix"],
    }


def _moon_rise_set(
    loc: EarthLocation, dark_start: float | None, dark_end: float | None,
) -> tuple[float | None, float | None]:
    """Moonrise / moonset crossings of the horizon *within the dark window*.

    Returns ``(rise_unix, set_unix)``, either ``None`` when no crossing happens
    in the window (moon stays up or down the whole night) — exactly the decision
    input the panel needs ("dark sky after the moon sets at 23:14"). Coarse
    10-min sampling: a crossing is an alt sign change between samples, refined to
    the midpoint. Polar-safe (bounded sample count).
    """
    if dark_start is None or dark_end is None or dark_end <= dark_start:
        return None, None
    span = min(dark_end - dark_start, _NIGHT_SEARCH_HOURS * 3600.0)
    step = 600.0
    n = int(span / step) + 1
    unix = dark_start + np.arange(n) * step
    times = Time(unix, format="unix")
    moon = get_body("moon", times, loc).transform_to(
        AltAz(obstime=times, location=loc))
    alt = np.asarray(moon.alt.to_value(u.deg))
    rise_unix: float | None = None
    set_unix: float | None = None
    for i in range(1, n):
        if alt[i - 1] < 0.0 <= alt[i] and rise_unix is None:
            rise_unix = float((unix[i - 1] + unix[i]) / 2.0)
        elif alt[i - 1] >= 0.0 > alt[i] and set_unix is None:
            set_unix = float((unix[i - 1] + unix[i]) / 2.0)
    return rise_unix, set_unix


# ----------------------------------------------------------------- best window

def _moon_factor(sep_deg: float, illumination: float, moon_up: bool) -> float:
    """Moon penalty multiplier for a candidate window.

    ``moon_up=False`` ⇒ 1.0: a 95%-illuminated moon that has set is irrelevant —
    moon *altitude* gates whether separation matters at all (design spec §9 /
    critique C2-#5). When the moon is up, penalise small separations from a
    bright moon: full strength at the moon, easing to ~1.0 far away, scaled by
    illumination. Clamped to [0.3, 1.0] so a window is never zeroed out.
    """
    if not moon_up:
        return 1.0
    # 0 at the moon → 1 at ~120° away; weighted by how bright the moon is.
    proximity = max(0.0, 1.0 - sep_deg / 120.0)
    penalty = proximity * max(0.0, illumination)
    return max(0.3, min(1.0, 1.0 - 0.7 * penalty))


def _best_window(
    samples: list[dict], dark_start: float | None, dark_end: float | None,
    alt_limit: float, illumination: float,
) -> dict | None:
    """Longest contiguous run of samples with ``alt ≥ alt_limit`` AND in the dark
    window, scored ``mean_alt × moon_factor``. Returns the best-scoring such run
    (ties → longer), or ``None`` when the target never clears the limit in dark.
    """
    if dark_start is None or dark_end is None:
        return None
    runs: list[tuple[int, int]] = []
    cur_start: int | None = None
    for i, s in enumerate(samples):
        in_dark = dark_start <= s["t_unix"] <= dark_end
        ok = in_dark and s["alt"] >= alt_limit
        if ok and cur_start is None:
            cur_start = i
        elif not ok and cur_start is not None:
            runs.append((cur_start, i - 1))
            cur_start = None
    if cur_start is not None:
        runs.append((cur_start, len(samples) - 1))
    if not runs:
        return None

    best: dict | None = None
    best_score = -1.0
    for a, b in runs:
        seg = samples[a:b + 1]
        mean_alt = sum(s["alt"] for s in seg) / len(seg)
        # moon up at any point in the window ⇒ separation matters; otherwise the
        # moon is irrelevant for this window.
        moon_up = any(s["moon_alt"] > 0.0 for s in seg)
        # representative separation = at the run's mid-sample (panel shows one #).
        score = mean_alt * _moon_factor(
            samples[(a + b) // 2].get("moon_sep_deg", 180.0),
            illumination, moon_up)
        length = b - a
        # prefer higher score; break ties toward the longer window.
        if score > best_score or (
                abs(score - best_score) < 1e-6 and best is not None
                and length > (best["_len"])):
            best_score = score
            best = {
                "start_unix": seg[0]["t_unix"],
                "end_unix": seg[-1]["t_unix"],
                "mean_alt": round(mean_alt, 1),
                "_len": length,
            }
    if best is not None:
        best.pop("_len", None)
    return best


# ----------------------------------------------------------------- night scaffold
#
# Everything a night needs that does NOT depend on which target you point at:
# the twilight scan, the sample grid, the sun-altitude series, the moon's
# alt/az/illumination track and its rise/set. That is ~85% of compute_night's
# cost, and /api/catalog/tonight used to pay it 64 times over — once per catalog
# object — for one identical night. On a fast desktop that made the endpoint a
# 4.9 s call; a Pi-class rig (the actual deployment target) is several times
# slower, past the client's 15 s budget, and the beginner's "what can I image
# tonight?" panel died on a spinner. Compute it once per (site, night, step) and
# hand every target the same scaffold.
#
# NOT a correctness shortcut: the per-target answer is unchanged. The moon
# readout is still taken at that target's own transit sample — the moon arrays
# are evaluated on the very grid the transit index points into, so the inputs are
# the same instant they always were.

_SCAFFOLD_MAX = 4          # a couple of sites × tonight/next-night is plenty
_scaffold_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_scaffold_lock = threading.Lock()


def _build_scaffold(loc: EarthLocation, anchor: float, lon: float,
                    step_min: int) -> dict:
    dark_start, dark_end, darkness_kind = _find_dark_window(loc, anchor, lon)

    # Sample from local sunset→sunrise. We bracket the dark window by ±2.5h so the
    # altitude curve shows the object rising into / setting out of the dark, but
    # only mark in-dark samples for the window math.
    if dark_start is not None and dark_end is not None:
        pad = 2.5 * 3600.0
        t0, t1 = dark_start - pad, dark_end + pad
    else:  # pragma: no cover - _find_dark_window always returns a window
        t0, t1 = anchor - 6 * 3600.0, anchor + 6 * 3600.0
    step = step_min * 60.0
    n = int((t1 - t0) / step) + 1
    unix = t0 + np.arange(n) * step
    times = Time(unix, format="unix")
    frame = AltAz(obstime=times, location=loc)

    sun = get_sun(times)
    sun_alt = np.asarray(sun.transform_to(frame).alt.to_value(u.deg))

    moon_body = get_body("moon", times, loc)
    moon_altaz = moon_body.transform_to(frame)
    moon_alt = np.asarray(moon_altaz.alt.to_value(u.deg))
    moon_az = np.asarray(moon_altaz.az.to_value(u.deg))

    # Illumination + waxing sign along the whole night, so the per-target readout
    # can be *indexed* at that target's transit instead of recomputed there. Same
    # formulae as _moon_info (see there for the elongation rationale).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NonRotationTransformationWarning)
        elong = np.asarray(moon_body.separation(sun).to_value(u.rad))
        # Hoisted out of the per-target separation: the GCRS→ICRS transform is
        # the expensive half, and it does not depend on the target at all.
        moon_icrs = moon_body.transform_to("icrs")
    illum = np.clip((1.0 + np.cos(math.pi - elong)) / 2.0, 0.0, 1.0)
    delta = (np.asarray(moon_body.geocentrictrueecliptic.lon.to_value(u.deg))
             - np.asarray(sun.geocentrictrueecliptic.lon.to_value(u.deg))) % 360.0
    waxing = delta < 180.0

    rise_unix, set_unix = _moon_rise_set(loc, dark_start, dark_end)

    return {
        "anchor": anchor, "unix": unix, "n": n, "frame": frame,
        "dark_start": dark_start, "dark_end": dark_end,
        "darkness_kind": darkness_kind,
        "sun_alt": sun_alt, "moon_alt": moon_alt, "moon_az": moon_az,
        "moon_icrs": moon_icrs, "illum": illum, "waxing": waxing,
        "moon_rise_unix": rise_unix, "moon_set_unix": set_unix,
    }


def _night_scaffold(site: dict, loc: EarthLocation, anchor: float, lon: float,
                    step_min: int) -> dict:
    """Cached ``_build_scaffold``. Keyed on the things that change the answer —
    site, the night's solar-midnight anchor, and the sample step. A site edit or
    a new night simply misses and rebuilds; nothing is ever served for the wrong
    place or date.

    The lock is held across the build so a burst of concurrent targets (the
    catalog fan-out runs 8 wide) computes the night ONCE rather than eight times,
    and so every reader sees fully-materialised arrays.
    """
    key = (round(float(site["latitude"]), 9), round(lon, 9),
           round(float(site.get("elevation_m", 0.0)), 6),
           round(anchor, 3), step_min)
    with _scaffold_lock:
        hit = _scaffold_cache.get(key)
        if hit is not None:
            _scaffold_cache.move_to_end(key)
            return hit
        built = _build_scaffold(loc, anchor, lon, step_min)
        _scaffold_cache[key] = built
        while len(_scaffold_cache) > _SCAFFOLD_MAX:
            _scaffold_cache.popitem(last=False)
        return built


# ----------------------------------------------------------------- core compute

def compute_night(
    ra_hours: float, dec_deg: float, *,
    date: str | None = None, step_min: int = DEFAULT_STEP_MIN,
    alt_limit: float = DEFAULT_ALT_LIMIT, site: dict | None = None,
) -> dict:
    """The full ``VisibilityNight`` for one target. Pure (no I/O beyond astropy's
    in-process ephemerides), so the mosaic module and tests can call it directly.
    """
    site = site or hub.site
    lon = float(site["longitude"])
    loc = _site_location(site)
    step_min = max(1, int(step_min or DEFAULT_STEP_MIN))
    alt_limit = float(alt_limit if alt_limit is not None else DEFAULT_ALT_LIMIT)

    # ra_hours → degrees with the explicit *15 (a 15× slip lands a random field).
    target = SkyCoord(
        ra=ra_hours * 15.0 * u.deg, dec=dec_deg * u.deg, frame="icrs")

    anchor = _night_anchor_unix(date, lon)
    sc = _night_scaffold(site, loc, anchor, lon, step_min)
    dark_start, dark_end = sc["dark_start"], sc["dark_end"]
    darkness_kind = sc["darkness_kind"]
    unix, n, frame = sc["unix"], sc["n"], sc["frame"]
    sun_alt, moon_alt = sc["sun_alt"], sc["moon_alt"]

    tgt_alt = np.asarray(target.transform_to(frame).alt.to_value(u.deg))
    moon_sep = np.asarray(sc["moon_icrs"].separation(target).to_value(u.deg))

    samples: list[dict] = []
    for i in range(n):
        samples.append({
            "t_unix": float(unix[i]),
            "alt": round(float(tgt_alt[i]), 2),
            "moon_alt": round(float(moon_alt[i]), 2),
            "sun_alt": round(float(sun_alt[i]), 2),
            # carried internally for moon scoring; not part of the TS sample shape
            # but harmless extra key on the JSON (client ignores it).
            "moon_sep_deg": round(float(moon_sep[i]), 2),
        })

    # Transit = the sampled max-alt timestamp WITHIN the dark window (not the
    # closed-form 90−|lat−dec|, which is wrong for circumpolar objects and
    # useless when the geometric transit is in daylight). If the geometric
    # (full-curve) peak is outside the dark window, flag transit_in_daylight and
    # report the in-darkness peak instead.
    geo_i = int(np.argmax(tgt_alt))
    geo_in_dark = (dark_start is not None and dark_end is not None
                   and dark_start <= float(unix[geo_i]) <= dark_end)
    if geo_in_dark or dark_start is None:
        transit_i = geo_i
        transit_in_daylight = False
    else:
        dark_mask = (unix >= dark_start) & (unix <= dark_end)
        if dark_mask.any():
            masked = np.where(dark_mask, tgt_alt, -999.0)
            transit_i = int(np.argmax(masked))
        else:  # pragma: no cover
            transit_i = geo_i
        transit_in_daylight = True

    transit_unix = float(unix[transit_i])
    transit_alt = round(float(tgt_alt[transit_i]), 1)

    # never_rises_above_limit: the object never clears alt_limit at ANY in-dark
    # sample (so a Send-to-Plan must force an override).
    in_dark_alts = [s["alt"] for s in samples
                    if dark_start is not None and dark_end is not None
                    and dark_start <= s["t_unix"] <= dark_end]
    peak_in_dark = max(in_dark_alts) if in_dark_alts else float(tgt_alt.max())
    never_rises_above_limit = peak_in_dark < alt_limit

    # session time = the dark-window transit (a representative "now" for the moon
    # readout); falls back to the anchor when there is no window.
    if dark_start is not None:
        # transit_unix IS unix[transit_i], a grid point, so the scaffold's moon
        # arrays hold the values for exactly the instant this used to evaluate
        # scalar-wise — same answer, none of the per-target astropy work.
        moon = _moon_info_at(sc, transit_i, float(moon_sep[transit_i]))
    else:  # pragma: no cover - _find_dark_window always returns a window
        moon = _moon_info(loc, anchor, target, dark_start, dark_end, lon)

    best = _best_window(
        samples, dark_start, dark_end, alt_limit, moon["illumination"])

    out_date = date or _dt.datetime.fromtimestamp(
        anchor, _dt.timezone.utc).date().isoformat()

    return {
        "date": out_date,
        "transit_unix": transit_unix,
        "transit_alt": transit_alt,
        "transit_in_daylight": transit_in_daylight,
        "dark_start_unix": dark_start,
        "dark_end_unix": dark_end,
        "darkness_kind": darkness_kind,
        "samples": samples,
        "moon": moon,
        "best_window": best,
        "alt_limit_deg": alt_limit,
        "never_rises_above_limit": never_rises_above_limit,
    }


def transit_alt_for(
    ra_hours: float, dec_deg: float, *, date: str | None = None,
    site: dict | None = None,
) -> float:
    """Peak (transit) altitude tonight for a target — the per-panel chip the
    mosaic module stamps (a coarse 20-min sample is plenty for a chip). Cheap
    enough to call per panel; importable by ``catalog/framing.py``."""
    night = compute_night(ra_hours, dec_deg, date=date, step_min=20,
                           alt_limit=DEFAULT_ALT_LIMIT, site=site)
    return night["transit_alt"]


# ----------------------------------------------------------------- routes

@router.get("/api/visibility", dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def get_visibility(
    ra: float = Query(..., ge=0, lt=24),
    dec: float = Query(..., ge=-90, le=90),
    date: str | None = None,
    step_min: int = Query(DEFAULT_STEP_MIN, ge=1, le=240),
    alt_limit: float = Query(DEFAULT_ALT_LIMIT, ge=-90, le=90),
):
    """Tonight's visibility for ``ra`` (hours) / ``dec`` (deg) from ``hub.site``.

    Coords are constrained at the boundary so astropy never raises a bare
    ValueError (which would 500 with a stack-trace leak) — pydantic 422s first.
    ``date`` is checked for the same reason (see ``check_night_date``: an
    unchecked bad date silently answers for TONIGHT instead).
    Heavy astropy vectorised transforms run off the event loop.
    """
    import asyncio
    date = check_night_date(date)
    return await asyncio.to_thread(
        compute_night, ra, dec, date=date, step_min=step_min,
        alt_limit=alt_limit)


@router.post("/api/visibility/order",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def post_order(body: OrderBody):
    """Annotate each target with its visibility and return a group-atomic
    ``recommended_order`` (indices into the request list).

    Mosaic groups are kept atomic: panels sharing a ``mosaic_group`` are sorted
    as one unit by the group's earliest transit, and never interleaved with
    another group (design spec §7 / critique C2/C1-C4). Tie-break: group max-alt.
    """
    import asyncio

    # Throttle the per-target fan-out so a large batch can't saturate the shared
    # default executor (which also serves config/plan/profile disk writes).
    sem = asyncio.Semaphore(8)

    async def _night(t: OrderTarget) -> dict:
        async with sem:
            return await asyncio.to_thread(
                compute_night, t.ra_hours, t.dec_deg,
                date=body.date, step_min=20)

    nights = await asyncio.gather(*[_night(t) for t in body.targets])

    targets_out: list[dict] = []
    for t, night in zip(body.targets, nights):
        bw = night["best_window"]
        targets_out.append({
            "name": t.name,
            "ra_hours": t.ra_hours,
            "dec_deg": t.dec_deg,
            "transit_unix": night["transit_unix"],
            "max_alt": night["transit_alt"],
            "best_window": ({"start_unix": bw["start_unix"],
                             "end_unix": bw["end_unix"]} if bw else None),
            "moon_sep_deg": night["moon"]["separation_deg"],
        })

    order = _group_atomic_order(body.targets, targets_out)
    return {"targets": targets_out, "recommended_order": order}


def _group_atomic_order(
    targets: list[OrderTarget], annotated: list[dict],
) -> list[int]:
    """Order indices so each mosaic group is contiguous, groups (and ungrouped
    singletons) are sorted by earliest transit, tie-broken by group max-alt, and
    the original (snake) order is preserved *within* a group.
    """
    # bucket original indices by group key (ungrouped → unique per-index key so
    # each is its own "group of one").
    groups: dict[str, list[int]] = {}
    order_seen: list[str] = []
    for i, t in enumerate(targets):
        key = t.mosaic_group if t.mosaic_group else f"__single_{i}"
        if key not in groups:
            groups[key] = []
            order_seen.append(key)
        groups[key].append(i)

    def group_sort_key(key: str) -> tuple[float, float]:
        idxs = groups[key]
        transit = min(annotated[i]["transit_unix"] for i in idxs)
        max_alt = max(annotated[i]["max_alt"] for i in idxs)
        # earliest transit first; tie-break HIGHER max-alt first (negate).
        return (transit, -max_alt)

    ordered_keys = sorted(order_seen, key=group_sort_key)
    out: list[int] = []
    for key in ordered_keys:
        out.extend(groups[key])  # preserve in-group (snake) order
    return out
