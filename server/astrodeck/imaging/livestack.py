"""Live-stacking (EAA "Live View") accumulator core.

A fixed-frame running-MEAN stack with a lightweight brightest-star drift-reject.
This is the pure, tested heart of NOV-1 — no device I/O. The hub owns one
``LiveStacker`` while Live View is armed and feeds it every raw linear sub; the
mean it returns becomes the displayed preview (spec §1.3). Full star-match
registration is a deferred second pass — v1 aligns on a single integer offset.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .stars import Star, detect_stars

DEFAULT_REJECT_FRAC = 0.08
DEFAULT_REANCHOR_AFTER = 3


def brightest_centroid(stars: list[Star]) -> tuple[float, float] | None:
    """(x, y) of the highest-FLUX star — the alignment anchor. None if empty.

    detect_stars appends in descending peak-pixel order, NOT flux order
    (stars.py:73), so select by flux explicitly (mirrors _median_hfr_from)."""
    if not stars:
        return None
    s = max(stars, key=lambda s: s.flux)
    return (float(s.x), float(s.y))


def align_offset(ref: tuple[float, float],
                 cur: tuple[float, float]) -> tuple[float, float]:
    """Pixel drift (dx, dy) of ``cur`` relative to ``ref``: a star at ``ref``
    appears at ``ref + offset`` in the current sub."""
    return (cur[0] - ref[0], cur[1] - ref[1])


def _axis_slices(n: int, d: int) -> tuple[slice, slice]:
    """Destination/source index ranges along one axis for an integer shift ``d``.
    accumulator[dst] += data[src], where src == dst + d, in-bounds both sides."""
    if d >= 0:
        return slice(0, n - d), slice(d, n)
    return slice(-d, n), slice(0, n + d)


@dataclass
class StackOutcome:
    accepted: bool
    frames: int
    integrated_s: float
    rejected: int
    dx: float
    dy: float
    reason: str


class LiveStacker:
    def __init__(self, reject_frac: float = DEFAULT_REJECT_FRAC,
                 reanchor_after: int = DEFAULT_REANCHOR_AFTER) -> None:
        self.reject_frac = float(reject_frac)
        self.reanchor_after = int(reanchor_after)
        self.reset()

    def reset(self) -> None:
        self._sum: np.ndarray | None = None          # float32 running sum
        self._cov: np.ndarray | None = None          # uint16 per-pixel coverage
        self._ref: tuple[float, float] | None = None  # reference brightest centroid
        self._shape: tuple[int, int] | None = None
        self._frames = 0
        self._integrated = 0.0
        self._rejected = 0
        self._consec = 0

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def integrated_s(self) -> float:
        return self._integrated

    @property
    def rejected(self) -> int:
        return self._rejected

    def mean(self) -> np.ndarray | None:
        if self._sum is None or self._cov is None:
            return None
        cov = np.maximum(self._cov, 1)
        return np.clip(np.round(self._sum / cov), 0, 65535).astype(np.uint16)

    def _seed(self, data: np.ndarray, exposure_s: float,
              centroid: tuple[float, float]) -> None:
        self._shape = (int(data.shape[0]), int(data.shape[1]))
        self._sum = data.astype(np.float32)
        self._cov = np.ones(self._shape, dtype=np.uint16)
        self._ref = centroid
        self._frames = 1
        self._integrated = float(exposure_s)
        self._consec = 0

    def _outcome(self, accepted: bool, dx: float, dy: float,
                 reason: str) -> StackOutcome:
        return StackOutcome(accepted, self._frames, self._integrated,
                            self._rejected, dx, dy, reason)

    def add(self, data: np.ndarray, exposure_s: float, *,
            stars: list[Star] | None = None) -> StackOutcome:
        if stars is None:
            stars = detect_stars(data)
        centroid = brightest_centroid(stars)
        if centroid is None:
            self._rejected += 1
            return self._outcome(False, 0.0, 0.0, "no_stars")

        # first sub, or the frame geometry changed (binning) -> (re)seed.
        if self._sum is None or (int(data.shape[0]), int(data.shape[1])) != self._shape:
            reason = "size" if self._sum is not None else ""
            self._seed(data, exposure_s, centroid)
            return self._outcome(True, 0.0, 0.0, reason)

        dx, dy = align_offset(self._ref, centroid)
        h, w = self._shape
        if (dx * dx + dy * dy) ** 0.5 > self.reject_frac * min(h, w):
            self._rejected += 1
            self._consec += 1
            if self._consec >= self.reanchor_after:
                self._seed(data, exposure_s, centroid)   # deliberate re-frame
                return self._outcome(True, dx, dy, "reseed")
            return self._outcome(False, dx, dy, "drift")

        idx = int(round(dx))
        idy = int(round(dy))
        ys_dst, ys_src = _axis_slices(h, idy)
        xs_dst, xs_src = _axis_slices(w, idx)
        self._sum[ys_dst, xs_dst] += data[ys_src, xs_src].astype(np.float32)
        self._cov[ys_dst, xs_dst] += 1
        self._frames += 1
        self._integrated += float(exposure_s)
        self._consec = 0
        return self._outcome(True, dx, dy, "")
