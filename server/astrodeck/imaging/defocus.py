"""Measuring DEFOCUS, when there are no stars to measure.

Far from focus a star is not a point — it is an annulus. On this rig at
2026-07-31 the annulus was ~880 px across, with the secondary obstruction and
four spider vanes plainly visible. Nothing in the star-detection path can cope
with that, and both detectors failed in opposite directions:

* ``imaging.stars.detect_stars`` reported 130-713 "stars" — fragments of the
  donut RING. Worse, its measurement box is 15 px, so it cannot span an 880 px
  donut at all and its HFR read ~4-5 on every frame regardless of true focus.
  A metric that returns the same number at every focuser position is not a
  focus metric.
* the native Rust detector correctly reported 0-2: there genuinely are no stars.

So coarse focus must not ask "how many stars", it must ask "how big is the
blob". That question has an answer at every focuser position from the far end of
the travel down to perfect focus, which is exactly the property a coarse-focus
metric needs.

The measurement is a radial profile around the brightest source and the radius
enclosing 80% of its flux. Deliberately NOT a threshold-and-segment approach:
thresholding breaks a donut ring into arcs (measured — a 900 px donut came back
as a 44x312 px fragment), which is the same failure that fooled the star
detector.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Work at 1/4 resolution. A donut is hundreds of pixels across, so quarter-res
#: costs nothing real and makes a 26 MP frame cheap to measure.
BIN = 4

#: Half-width of the window searched around the brightest point, in FULL-frame
#: pixels. Must comfortably exceed the largest blob worth measuring; beyond this
#: the measurement saturates and reports "at least this big", which is still the
#: right answer for "you are miles out".
WINDOW_PX = 1200

#: Below this r80 the blob is small enough that real star detection can take
#: over — hand off to the V-curve autofocus rather than keep bisecting.
HANDOVER_R80_PX = 12.0

#: A pixel counts toward the source only this far above background.
#: 5 sigma, the same bar imaging.stars uses. At 2 sigma about 2% of a
#: 360k-pixel window survives and that noise outweighs a point source
#: entirely — a FOCUSED star measured r80=316px, i.e. the metric read
#: maximum defocus exactly when it was in focus. At 5 sigma the expected
#: noise survivors are a fraction of one pixel, while the real donut ring
#: (measured peak SNR 104) clears it with room to spare.
SOURCE_THRESHOLD_SIGMA = 5.0


@dataclass
class BlobSize:
    """One defocus measurement."""
    r80: float          #: radius enclosing 80% of the source's flux, full-frame px
    peak: float         #: background-subtracted peak, ADU
    snr: float          #: peak / robust background sigma
    x: int              #: brightest-source centre, full-frame px
    y: int
    background: float
    sigma: float

    @property
    def diameter(self) -> float:
        return 2.0 * self.r80

    @property
    def ready_for_autofocus(self) -> bool:
        return self.r80 <= HANDOVER_R80_PX


def _binned(a: np.ndarray, k: int = BIN) -> np.ndarray:
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    return a[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def measure_blob(data: np.ndarray, *, bin_: int = BIN,
                 window_px: int = WINDOW_PX) -> BlobSize | None:
    """Size of the brightest source. None when there is no source at all.

    Works on a donut and on a star with the same code and the same units, which
    is what lets ONE number track focus across the whole range.
    """
    a = np.asarray(data, dtype=np.float32)
    if a.ndim != 2 or a.shape[0] < 8 * bin_ or a.shape[1] < 8 * bin_:
        return None
    b = _binned(a, bin_)
    bg = float(np.median(b))
    sigma = float(1.4826 * np.median(np.abs(b - bg))) or 1e-6

    # Find the brightest REGION, not the brightest pixel: a hot pixel is a
    # single cell and would drag the window off the actual blob.
    pad = np.pad(b, 1, mode="edge")
    sm = sum(pad[i:i + b.shape[0], j:j + b.shape[1]]
             for i in range(3) for j in range(3)) / 9.0
    cy, cx = np.unravel_index(int(np.argmax(sm)), sm.shape)
    if sm[cy, cx] - bg < 3.0 * sigma:
        return None                       # nothing above the noise anywhere

    r = max(4, window_px // (2 * bin_))
    y0, y1 = max(0, cy - r), min(b.shape[0], cy + r + 1)
    x0, x1 = max(0, cx - r), min(b.shape[1], cx + r + 1)
    cut = b[y0:y1, x0:x1] - bg
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rad = np.hypot(yy - cy, xx - cx).ravel()
    # Count only pixels meaningfully ABOVE the background.
    #
    # Clipping raw residuals at zero instead lets the window's own noise
    # dominate: at quarter-res a 1200px window is ~360k pixels, and the positive
    # half of the noise sums to far more "flux" than a small source contains. A
    # FOCUSED star then measured r80=410px — the metric reporting maximum
    # defocus exactly when it was in focus, which would drive the search away
    # from the answer.
    resid = cut.ravel()
    val = np.where(resid > SOURCE_THRESHOLD_SIGMA * sigma, resid, 0.0)
    total = float(val.sum())
    if total <= 0.0:
        return None
    order = np.argsort(rad)
    cum = np.cumsum(val[order])
    idx = int(np.searchsorted(cum, 0.8 * total))
    idx = min(idx, len(order) - 1)
    r80 = float(rad[order][idx] * bin_)
    return BlobSize(r80=r80, peak=float(cut.max()),
                    snr=float(cut.max() / sigma),
                    x=int(cx * bin_), y=int(cy * bin_),
                    background=bg, sigma=sigma)


def focus_from_two(p1: int, r1: float, p2: int, r2: float) -> float | None:
    """Extrapolate the in-focus position from two blob measurements.

    A defocused star's diameter grows LINEARLY with distance from focus,
    ``D = k * |x - x_focus|``, so two points give both the slope and the
    crossing — with no knowledge of the aperture, the f-ratio or the step size.
    That is a handful of exposures instead of a blind sweep of the whole travel.

    None when the two measurements are indistinguishable (the step was too small
    to see) or the geometry is degenerate.
    """
    if p1 == p2:
        return None
    slope = (r2 - r1) / (p2 - p1)
    if abs(slope) < 1e-9:
        return None
    x = p1 - r1 / slope
    if not np.isfinite(x):
        return None
    return float(x)


def shrinking(r1: float, r2: float, *, noise_px: float = 2.0) -> bool | None:
    """Did the blob get smaller? None when the change is within measurement
    noise, which means "move further before deciding" rather than "no"."""
    d = r2 - r1
    if abs(d) <= noise_px:
        return None
    return d < 0.0
