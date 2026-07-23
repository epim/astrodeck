"""Star detection and HFR (half-flux radius) measurement.

HFR is the focus metric: the radius containing half a star's flux. Smaller
is sharper. The autofocus routine fits a curve of median HFR vs focuser
position and drives to the minimum.

Detection is a fast classic pipeline: background subtraction, threshold at
k-sigma, local-maximum seeding, centroid + HFR on a small cutout. Good enough
for focusing and star counts; not a photometry tool.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Star:
    x: float
    y: float
    flux: float
    hfr: float
    peak: float
    ecc: float = 0.0      # Pass 2 (unsaturated mid-bright only); 0.0 placeholder in Pass 1
    theta: float = 0.0    # Pass 2 (radians)


#: cap on detected stars (detect_stars) and on overlay marks (star_marks). The
#: marks cap MUST be >= the detect cap so the overlay never silently drops stars
#: the detector already found; star_marks asserts this coupling (P3-5).
DEFAULT_MAX_STARS = 200
DEFAULT_MAX_MARKS = 400


def _ecc_theta(ixx: float, iyy: float, ixy: float) -> tuple[float, float]:
    """Second-moment eccentricity and major-axis position angle.

    ecc in [0,1] (0 = round); theta in radians, (−π/2, π/2], measured from +x.
    Degenerate/negative covariance → (0.0, 0.0)."""
    mean = (ixx + iyy) / 2.0
    common = math.hypot((ixx - iyy) / 2.0, ixy)
    lam1 = mean + common            # major eigenvalue
    lam2 = mean - common            # minor eigenvalue
    if lam1 <= 0.0:
        return 0.0, 0.0
    ratio = min(1.0, max(0.0, lam2 / lam1))
    ecc = math.sqrt(1.0 - ratio)
    theta = 0.5 * math.atan2(2.0 * ixy, ixx - iyy)
    return float(ecc), float(theta)


def detect_stars(data: np.ndarray, k_sigma: float = 5.0,
                 max_stars: int = DEFAULT_MAX_STARS, box: int = 15) -> list[Star]:
    img = data.astype(np.float64)
    # Robust background: median + MAD
    bg = float(np.median(img))
    noise = float(np.median(np.abs(img - bg))) * 1.4826
    if noise <= 0:
        noise = max(1.0, img.std())
    thresh = bg + k_sigma * noise

    h, w = img.shape
    half = box // 2
    sub = img - bg

    # Candidate peaks: strictly local maxima above threshold (coarse grid scan
    # keeps this O(pixels) without scipy).
    ys, xs = np.where(img > thresh)
    if len(ys) == 0:
        return []
    order = np.argsort(img[ys, xs])[::-1]
    ys, xs = ys[order], xs[order]

    stars: list[Star] = []
    used = np.zeros((h, w), dtype=bool)
    for y, x in zip(ys, xs):
        if len(stars) >= max_stars:
            break
        if used[y, x] or y < half or x < half or y >= h - half or x >= w - half:
            continue
        cut = sub[y - half:y + half + 1, x - half:x + half + 1]
        if cut[half, half] < cut.max() * 0.95:
            continue  # not the local peak
        used[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1] = True

        # Local background from the cutout border, then flux-weighted mean
        # radius as the HFR metric — smooth and robust to undersampling,
        # unlike the cumulative half-flux threshold.
        border = np.concatenate([cut[0], cut[-1], cut[1:-1, 0], cut[1:-1, -1]])
        cut = (cut - float(np.median(border))).clip(0)
        total = float(cut.sum())
        if total <= 0:
            continue
        yy, xx = np.mgrid[0:box, 0:box]
        cx = float((xx * cut).sum() / total)
        cy = float((yy * cut).sum() / total)
        r = np.hypot(xx - cx, yy - cy)
        hfr = float((r * cut).sum() / total)
        if hfr <= 0.05 or hfr > half:
            continue
        # Second moments on the same background-subtracted cutout → real
        # eccentricity + major-axis PA (~5 cheap reductions, arrays already
        # in scope). Population policy (unsaturated mid-bright) lives in
        # star_marks; every detection carries a value here.
        dx = xx - cx
        dy = yy - cy
        ixx = float((dx * dx * cut).sum() / total)
        iyy = float((dy * dy * cut).sum() / total)
        ixy = float((dx * dy * cut).sum() / total)
        ecc, theta = _ecc_theta(ixx, iyy, ixy)
        stars.append(Star(
            x=x - half + cx, y=y - half + cy, flux=total,
            hfr=hfr, peak=float(img[y, x]), ecc=ecc, theta=theta,
        ))
    return stars


def _median_hfr_from(stars: list[Star], min_stars: int = 3) -> tuple[float | None, int]:
    """Median HFR over the brightest stars, from an already-detected list.

    Faint detections near the threshold measure the noise floor, not the PSF —
    their flux-weighted radius plateaus at the cutout's noise radius. Bright
    stars are the focus signal, so only the top-flux quartile (5..25 stars)
    votes.
    """
    if len(stars) < min_stars:
        return None, len(stars)
    by_flux = sorted(stars, key=lambda s: -s.flux)
    n = max(min(len(by_flux), 5), min(len(by_flux) // 4, 25))
    return float(np.median([s.hfr for s in by_flux[:n]])), len(stars)


def median_hfr(data: np.ndarray, min_stars: int = 3) -> tuple[float | None, int]:
    """Return (median HFR over the brightest stars, star count)."""
    return _median_hfr_from(detect_stars(data), min_stars)


def star_marks(stars: list[Star], *, full_well: int | None = None,
               max_marks: int = DEFAULT_MAX_MARKS) -> list[dict]:
    """Compact per-star overlay payload: ``[{x, y, hfr[, ecc, theta]}]``.

    Coords are in ``frame.data`` pixel space (the detector ran there); the
    client scales by ``display_width / data_width`` (spec finding #2). Rounded
    to keep the event small. ``ecc``/``theta`` are Pass-2 and only attached for
    the unsaturated, mid-bright population ``median_hfr`` already trusts — never
    on saturated flat-top stars (spec finding #5); in Pass 1 they stay 0.0 and
    are omitted.

    ``max_marks`` MUST be >= ``detect_stars``' ``max_stars`` so the overlay never
    silently drops a star the detector found — otherwise a future bump of
    ``max_stars`` above ``max_marks`` would diverge ``len(marks)`` from the star
    count. The default cap pair (DEFAULT_MAX_MARKS=400 >= DEFAULT_MAX_STARS=200)
    holds this invariant by construction (P3-5).
    """
    assert max_marks >= DEFAULT_MAX_STARS, (
        "star_marks max_marks must be >= detect_stars max_stars so the overlay "
        "never drops detected stars")
    marks: list[dict] = []
    floor = 0.0
    if stars:
        floor = float(np.median([s.peak for s in stars])) * 0.05
    sat = (full_well * 0.9) if full_well else None
    for s in sorted(stars, key=lambda s: -s.flux)[:max_marks]:
        m = {"x": round(float(s.x), 1), "y": round(float(s.y), 1),
             "hfr": round(float(s.hfr), 2)}
        unsaturated_mid = (s.peak > floor and (sat is None or s.peak < sat))
        if s.ecc and unsaturated_mid:
            m["ecc"] = round(float(s.ecc), 3)
            m["theta"] = round(float(s.theta), 3)
        marks.append(m)
    return marks


def frame_eccentricity(marks: list[dict]) -> float | None:
    """Representative frame eccentricity: median of the trusted marks' ``ecc``
    (the mid-bright unsaturated population ``star_marks`` already attached ecc
    to). ``None`` when no star carried an ecc — the gate then abstains."""
    eccs = [m["ecc"] for m in marks if "ecc" in m]
    return float(np.median(eccs)) if eccs else None


# ---------------------------------------------------------------------------
# Sensor-tilt / corner-vs-center optical-aberration inspector (PRO-13).
#
# Purely additive aggregation over the SAME `marks` frame_eccentricity reads —
# no new detection pass, no reject gate. Bins marks into a cols x rows grid by
# `data`-space x,y and classifies the spatial HFR/ecc/theta pattern into one of
# uniform / tilt / coma / tracking. See docs/superpowers/specs/
# 2026-07-23-tilt-inspector-design.md for the full design + rationale.
# ---------------------------------------------------------------------------

#: a zone needs >= this many binned marks to report hfr/ecc/theta (else None).
_TILT_MIN_ZONE_STARS = 3
#: need >= this many populated zones (of grid*grid) to classify at all.
_TILT_MIN_POPULATED = 4
#: relative HFR spread (max-min)/median below this (+ round) => uniform.
_TILT_FLAT_TOL = 0.15
#: mean ecc below this => round.
_TILT_ROUND_TOL = 0.20
#: mean ecc at/above this => elongated enough for tracking.
_TILT_ELONG_TOL = 0.35
#: axis_spread below this => one common elongation direction.
_TILT_AXIS_ALIGN_TOL = 0.25
#: fraction of zones radially aligned => radial (coma).
_TILT_RADIAL_FRAC_TOL = 0.55
#: radians (~29deg): theta-vs-radial alignment tolerance.
_TILT_RADIAL_ANGLE = 0.5
#: (corner_mean - center)/center at/above => corners degraded (coma).
_TILT_RADIAL_EXCESS = 0.25
#: normalized planar HFR gradient at/above => asymmetric tilt.
_TILT_GRAD_TOL = 0.20


def _axis_mean(thetas: list[float]) -> float:
    """Doubled-angle circular mean of axial angles (mod pi), radians."""
    c = float(np.mean(np.cos(2.0 * np.asarray(thetas))))
    s = float(np.mean(np.sin(2.0 * np.asarray(thetas))))
    return 0.5 * math.atan2(s, c)


def _axis_spread(thetas: list[float]) -> float:
    """Doubled-angle circular spread: 0 (all one axis) .. 1 (scattered)."""
    c = float(np.mean(np.cos(2.0 * np.asarray(thetas))))
    s = float(np.mean(np.sin(2.0 * np.asarray(thetas))))
    return float(1.0 - math.hypot(c, s))


def _axis_diff(a: float, b: float) -> float:
    """Acute angle between two axes (mod pi), 0..pi/2."""
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def _bin_zones(marks: list[dict], width: float, height: float,
               cols: int, rows: int) -> list[dict]:
    """Row-major zones: ``[{'hfr','ecc','theta','n'}, ...]`` (len == rows*cols).

    ``hfr`` is the zone's median (present on every mark); ``ecc`` is the mean
    of the trusted subset that carries it; ``theta`` is their doubled-angle
    circular mean. Zones with ``n < _TILT_MIN_ZONE_STARS`` report
    ``hfr/ecc/theta = None`` but keep ``n`` (honest: too few stars to trust).
    """
    buckets: list[list[dict]] = [[] for _ in range(cols * rows)]
    for m in marks:
        x, y = m["x"], m["y"]
        if not (0.0 <= x < width and 0.0 <= y < height):
            continue
        c = min(cols - 1, int(x / width * cols))
        r = min(rows - 1, int(y / height * rows))
        buckets[r * cols + c].append(m)
    zones = []
    for b in buckets:
        n = len(b)
        if n >= _TILT_MIN_ZONE_STARS:
            hfr = float(np.median([m["hfr"] for m in b]))
            eccs = [m["ecc"] for m in b if "ecc" in m]
            thetas = [m["theta"] for m in b if "theta" in m]
            ecc = float(np.mean(eccs)) if eccs else None
            theta = _axis_mean(thetas) if thetas else None
        else:
            hfr = ecc = theta = None
        zones.append({
            "hfr": round(hfr, 2) if hfr is not None else None,
            "ecc": round(ecc, 3) if ecc is not None else None,
            "theta": round(theta, 3) if theta is not None else None,
            "n": n,
        })
    return zones


def _zone_center_frac(i: int, cols: int, rows: int) -> tuple[float, float]:
    """Fractional (x,y) in [0,1] of zone ``i``'s center, row-major."""
    r, c = divmod(i, cols)
    return ((c + 0.5) / cols, (r + 0.5) / rows)


def _classify(zones: list[dict], cols: int, rows: int) -> tuple[str, float, int | None]:
    """Classify the zone map. Returns ``(pattern, severity, worst_zone)``,
    ``pattern in {'uniform','tilt','coma','tracking'}``. Caller guarantees
    >= _TILT_MIN_POPULATED populated zones."""
    pop = [(i, z) for i, z in enumerate(zones) if z["hfr"] is not None]
    hfrs = [z["hfr"] for _, z in pop]
    med = float(np.median(hfrs))
    rel_spread = (max(hfrs) - min(hfrs)) / med if med > 0 else 0.0
    worst = max(pop, key=lambda iz: iz[1]["hfr"])[0]

    eccs = [z["ecc"] for _, z in pop if z["ecc"] is not None]
    mean_ecc = float(np.mean(eccs)) if eccs else 0.0
    thetas = [z["theta"] for _, z in pop if z["theta"] is not None]
    axis_spread = _axis_spread(thetas) if len(thetas) >= 2 else 1.0

    # planar HFR gradient (asymmetry): first/last populated column & row means.
    col_means: list[list[float]] = [[] for _ in range(cols)]
    row_means: list[list[float]] = [[] for _ in range(rows)]
    for i, z in pop:
        r, c = divmod(i, cols)
        col_means[c].append(z["hfr"])
        row_means[r].append(z["hfr"])

    def _span(groups: list[list[float]]) -> float:
        ms = [float(np.mean(g)) for g in groups if g]
        return (ms[-1] - ms[0]) if len(ms) >= 2 else 0.0

    tilt_mag = math.hypot(_span(col_means), _span(row_means)) / med if med > 0 else 0.0

    # radial excess: geometric corners vs the center cell.
    center_i = (rows // 2) * cols + (cols // 2)
    corner_ix = [0, cols - 1, (rows - 1) * cols, rows * cols - 1]
    center = zones[center_i]["hfr"]
    ch = [zones[i]["hfr"] for i in corner_ix if zones[i]["hfr"] is not None]
    radial_excess = ((float(np.mean(ch)) - center) / center
                      if (center and center > 0 and ch) else 0.0)

    # radial alignment of elongation axes.
    aligned = total = 0
    for i, z in enumerate(zones):
        if i == center_i or z["theta"] is None:
            continue
        zx, zy = _zone_center_frac(i, cols, rows)
        radial = math.atan2(zy - 0.5, zx - 0.5)
        total += 1
        if _axis_diff(z["theta"], radial) < _TILT_RADIAL_ANGLE:
            aligned += 1
    radial_frac = aligned / total if total else 0.0

    if rel_spread < _TILT_FLAT_TOL and mean_ecc < _TILT_ROUND_TOL:
        pattern = "uniform"
    elif (mean_ecc >= _TILT_ELONG_TOL and axis_spread < _TILT_AXIS_ALIGN_TOL
          and radial_frac < _TILT_RADIAL_FRAC_TOL):
        pattern = "tracking"
    elif (radial_excess >= _TILT_RADIAL_EXCESS
          and (radial_frac >= _TILT_RADIAL_FRAC_TOL or tilt_mag < _TILT_GRAD_TOL)):
        pattern = "coma"
    elif tilt_mag >= _TILT_GRAD_TOL:
        pattern = "tilt"
    else:
        pattern = "uniform"
    return pattern, rel_spread, worst


def frame_tilt(marks: list[dict], width: float, height: float,
               *, grid: int = 3) -> dict | None:
    """Zone map + pattern classification for the tilt/aberration inspector,
    over the same ``marks`` ``frame_eccentricity`` reads (no new detection
    pass). ``None`` when too few zones have enough stars — the client then
    shows nothing (abstain, exactly like ``frame_eccentricity`` -> ``None``).

    Block shape: ``{'cols','rows','zones':[{hfr,ecc,theta,n}...],'pattern',
    'severity','worst_zone'}``.
    """
    if not marks or width <= 0 or height <= 0:
        return None
    cols = rows = grid
    zones = _bin_zones(marks, width, height, cols, rows)
    if sum(1 for z in zones if z["hfr"] is not None) < _TILT_MIN_POPULATED:
        return None
    pattern, severity, worst = _classify(zones, cols, rows)
    return {"cols": cols, "rows": rows, "zones": zones,
            "pattern": pattern, "severity": round(severity, 3),
            "worst_zone": worst}


def measure_stars(stars: list[Star], *, full_well: int | None = None,
                  min_stars: int = 3) -> tuple[float | None, int, list[dict]]:
    """Derive ``(median_hfr, star_count, star_marks)`` from an already-detected
    star list — so a caller that also needs the raw ``Star`` objects (e.g. cloud
    detection, which inspects per-star peaks) runs ``detect_stars`` exactly once
    and feeds every consumer from that one pass."""
    hfr, count = _median_hfr_from(stars, min_stars)
    return hfr, count, star_marks(stars, full_well=full_well)


def measure_frame(data: np.ndarray, *, full_well: int | None = None,
                  min_stars: int = 3) -> tuple[float | None, int, list[dict]]:
    """One detection pass feeding both the focus metric and the overlay.

    Returns ``(median_hfr, star_count, star_marks)`` so the capture hot path
    never detects twice (spec §6 / §4.6)."""
    return measure_stars(detect_stars(data), full_well=full_well, min_stars=min_stars)
