"""Live-stacking (EAA "Live View") accumulator.

The hub owns one ``LiveStacker`` while Live View is armed and feeds it every raw
linear sub; the mean it returns becomes the displayed preview. No device I/O
here — this is the pure, tested core.

Three things v1 got wrong, all of which a real session hits inside an hour:

  * it anchored on the single brightest star, so a saturated anchor, a rank swap
    between two similar stars, or an anchor lost to cloud shifted the whole
    stack. Registration now matches a constellation (``registration.register``),
    falling back to the old single-star anchor — reported as ``weak_align`` —
    only when no pattern agrees. So it is never worse than v1, and usually
    much better.
  * it shifted by whole pixels, so a half-pixel drift — the normal case with
    dithering off — smeared every star by construction. Accumulation is now
    bilinear, which costs four adds per pixel and keeps the stars round.
  * it took a plain running mean, so a satellite trail, an aircraft, or a cosmic
    ray hit landed in the stack permanently and could only be cleared by
    starting over. Bright outliers are now clipped against the running estimate.

The rejection is deliberately one-sided. A pixel far ABOVE the accumulated mean
is a trail, a plane, or a cosmic ray; a pixel far below is, at worst, the same
noise on the other tail — and clipping the low side of a faint nebula's own
noise would eat real signal and quietly brighten the background.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .registration import Registration, register
from .stars import Star

#: Reject a sub whose measured drift exceeds this fraction of the short frame
#: axis. Past that the field has genuinely moved (a slew, a bumped tripod) and
#: aligning to it would drag the whole stack.
DEFAULT_REJECT_FRAC = 0.08
#: Consecutive rejects before we accept that the new framing is the real one and
#: re-seed. Without this a bumped scope strands the stack forever.
DEFAULT_REANCHOR_AFTER = 3
#: Clip a pixel this many standard deviations above the running mean.
DEFAULT_CLIP_SIGMA = 4.0
#: Frames needed before the per-pixel variance is worth trusting. Below this,
#: clipping would reject on noise and eat real stars.
MIN_FRAMES_FOR_CLIP = 4


def brightest_centroid(stars: list[Star]) -> tuple[float, float] | None:
    """(x, y) of the highest-FLUX star. None if empty.

    Retained because the "did this frame have anything to align on at all?"
    question is still asked before registration runs. detect_stars appends in
    descending peak-pixel order, NOT flux order (stars.py:73), so select by flux.
    """
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
    #: supporting star pairs behind the measured shift (0 when unregistered)
    support: int = 0
    #: pixels clipped as bright outliers in THIS sub (satellite trails etc.)
    clipped: int = 0


class LiveStacker:
    def __init__(self, reject_frac: float = DEFAULT_REJECT_FRAC,
                 reanchor_after: int = DEFAULT_REANCHOR_AFTER,
                 clip_sigma: float = DEFAULT_CLIP_SIGMA) -> None:
        self.reject_frac = float(reject_frac)
        self.reanchor_after = int(reanchor_after)
        #: <= 0 disarms clipping entirely (a deliberate "stack everything" mode)
        self.clip_sigma = float(clip_sigma)
        self.reset()

    def reset(self) -> None:
        self._sum: np.ndarray | None = None          # float32 running sum
        self._sumsq: np.ndarray | None = None        # float32 running sum of squares
        self._cov: np.ndarray | None = None          # float32 per-pixel coverage
        self._ref_stars: list[Star] = []             # reference constellation
        self._shape: tuple[int, int] | None = None
        self._frames = 0
        self._integrated = 0.0
        self._rejected = 0
        self._clipped = 0
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

    @property
    def clipped(self) -> int:
        """Total pixels clipped as bright outliers across the whole stack."""
        return self._clipped

    def mean(self) -> np.ndarray | None:
        if self._sum is None or self._cov is None:
            return None
        cov = np.maximum(self._cov, 1e-6)
        return np.clip(np.round(self._sum / cov), 0, 65535).astype(np.uint16)

    def _variance(self) -> np.ndarray | None:
        """Per-pixel variance of the accumulated samples, or None while there
        are too few frames for it to mean anything."""
        if (self._sum is None or self._sumsq is None or self._cov is None
                or self._frames < MIN_FRAMES_FOR_CLIP):
            return None
        cov = np.maximum(self._cov, 1e-6)
        mean = self._sum / cov
        # E[x^2] - E[x]^2, floored at 0 (float error can make it slightly
        # negative where every sample is identical).
        return np.maximum(self._sumsq / cov - mean * mean, 0.0)

    def _seed(self, data: np.ndarray, exposure_s: float,
              stars: list[Star]) -> None:
        self._shape = (int(data.shape[0]), int(data.shape[1]))
        f = data.astype(np.float32)
        self._sum = f.copy()
        self._sumsq = f * f
        self._cov = np.ones(self._shape, dtype=np.float32)
        self._ref_stars = list(stars)
        self._frames = 1
        self._integrated = float(exposure_s)
        self._consec = 0

    def _outcome(self, accepted: bool, dx: float, dy: float, reason: str,
                 support: int = 0, clipped: int = 0) -> StackOutcome:
        return StackOutcome(accepted, self._frames, self._integrated,
                            self._rejected, dx, dy, reason, support, clipped)

    def _accumulate(self, data: np.ndarray, dx: float, dy: float) -> int:
        """Add ``data`` into the accumulator shifted by (dx, dy), splitting each
        pixel bilinearly across the four integer positions it straddles.

        Returns the number of pixels clipped as bright outliers.

        The weights are applied to the SAMPLE COUNT as well as the sum, so the
        mean stays correct at the frame edges where only some subs contribute —
        the same reason the coverage plane exists at all.
        """
        assert self._sum is not None and self._sumsq is not None and self._cov is not None
        h, w = self._shape          # type: ignore[misc]
        f = data.astype(np.float32)

        clipped = 0
        var = self._variance()
        if var is not None and self.clip_sigma > 0:
            # Compare the incoming sub against the running estimate AT ITS OWN
            # aligned position, using the whole-pixel part of the shift. A trail
            # is many sigma out, so a pixel of registration slop does not matter
            # for detection — and doing it here, before the bilinear split,
            # means one comparison rather than four.
            iy, ix = int(round(dy)), int(round(dx))
            ys_dst, ys_src = _axis_slices(h, iy)
            xs_dst, xs_src = _axis_slices(w, ix)
            cov = np.maximum(self._cov[ys_dst, xs_dst], 1e-6)
            mean = self._sum[ys_dst, xs_dst] / cov
            limit = mean + self.clip_sigma * np.sqrt(var[ys_dst, xs_dst])
            window = f[ys_src, xs_src]
            hot = window > limit
            clipped = int(np.count_nonzero(hot))
            if clipped:
                # Replace the outlier with the running mean rather than dropping
                # it: a hole in the coverage plane would make the trail show up
                # as a DARK streak instead of vanishing.
                window = np.where(hot, mean, window)
                f = f.copy()
                f[ys_src, xs_src] = window

        # Bilinear split: floor + fractional remainder in each axis.
        fy, fx = int(np.floor(dy)), int(np.floor(dx))
        wy, wx = float(dy - fy), float(dx - fx)
        for oy, ky in ((0, 1.0 - wy), (1, wy)):
            if ky == 0.0:
                continue
            ys_dst, ys_src = _axis_slices(h, fy + oy)
            for ox, kx in ((0, 1.0 - wx), (1, wx)):
                if kx == 0.0:
                    continue
                k = ky * kx
                xs_dst, xs_src = _axis_slices(w, fx + ox)
                chunk = f[ys_src, xs_src]
                self._sum[ys_dst, xs_dst] += k * chunk
                self._sumsq[ys_dst, xs_dst] += k * chunk * chunk
                self._cov[ys_dst, xs_dst] += k
        return clipped

    def add(self, data: np.ndarray, exposure_s: float, *,
            stars: list[Star] | None = None) -> StackOutcome:
        if stars is None:
            from .stars import detect_stars
            stars = detect_stars(data)
        if not stars:
            self._rejected += 1
            return self._outcome(False, 0.0, 0.0, "no_stars")

        # first sub, or the frame geometry changed (binning) -> (re)seed.
        if self._sum is None or (int(data.shape[0]), int(data.shape[1])) != self._shape:
            reason = "size" if self._sum is not None else ""
            self._seed(data, exposure_s, stars)
            return self._outcome(True, 0.0, 0.0, reason, support=len(stars))

        h, w = self._shape       # type: ignore[misc]
        reg: Registration | None = register(self._ref_stars, stars)
        weak = False
        if reg is None:
            # No constellation agreed. Rather than refuse — which would make
            # Live View useless on exactly the fields that need it most, a
            # planetary nebula at long focal length or a sky with three stars
            # above the light pollution — fall back to the single brightest
            # star. That is precisely what v1 always did, so this is never worse
            # than the old behaviour; it is just no longer the FIRST choice, and
            # the outcome says so, so the UI can tell the user the alignment is
            # only as good as one star.
            ref_c = brightest_centroid(self._ref_stars)
            cur_c = brightest_centroid(stars)
            if ref_c is None or cur_c is None:
                self._rejected += 1
                self._consec += 1
                if self._consec >= self.reanchor_after:
                    self._seed(data, exposure_s, stars)
                    return self._outcome(True, 0.0, 0.0, "reseed",
                                         support=len(stars))
                return self._outcome(False, 0.0, 0.0, "no_match")
            odx, ody = align_offset(ref_c, cur_c)
            reg = Registration(odx, ody, 1, 0.0)
            weak = True

        dx, dy = reg.dx, reg.dy
        if (dx * dx + dy * dy) ** 0.5 > self.reject_frac * min(h, w):
            self._rejected += 1
            self._consec += 1
            if self._consec >= self.reanchor_after:
                self._seed(data, exposure_s, stars)   # deliberate re-frame
                return self._outcome(True, dx, dy, "reseed", reg.support)
            return self._outcome(False, dx, dy, "drift", reg.support)

        clipped = self._accumulate(data, dx, dy)
        self._clipped += clipped
        self._frames += 1
        self._integrated += float(exposure_s)
        self._consec = 0
        return self._outcome(True, dx, dy, "weak_align" if weak else "",
                             reg.support, clipped)
