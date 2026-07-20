"""Star detection and HFR (half-flux radius) measurement.

HFR is the focus metric: the radius containing half a star's flux. Smaller
is sharper. The autofocus routine fits a curve of median HFR vs focuser
position and drives to the minimum.

Detection is a fast classic pipeline: background subtraction, threshold at
k-sigma, local-maximum seeding, centroid + HFR on a small cutout. Good enough
for focusing and star counts; not a photometry tool.
"""
from __future__ import annotations

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
        stars.append(Star(
            x=x - half + cx, y=y - half + cy, flux=total,
            hfr=hfr, peak=float(img[y, x]),
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
