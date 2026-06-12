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


def detect_stars(data: np.ndarray, k_sigma: float = 5.0, max_stars: int = 200,
                 box: int = 15) -> list[Star]:
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


def median_hfr(data: np.ndarray, min_stars: int = 3) -> tuple[float | None, int]:
    """Return (median HFR over the brightest stars, star count).

    Faint detections near the threshold measure the noise floor, not the PSF —
    their flux-weighted radius plateaus at the cutout's noise radius. Bright
    stars are the focus signal, so only the top-flux quartile (5..25 stars)
    votes.
    """
    stars = detect_stars(data)
    if len(stars) < min_stars:
        return None, len(stars)
    by_flux = sorted(stars, key=lambda s: -s.flux)
    n = max(min(len(by_flux), 5), min(len(by_flux) // 4, 25))
    return float(np.median([s.hfr for s in by_flux[:n]])), len(stars)
