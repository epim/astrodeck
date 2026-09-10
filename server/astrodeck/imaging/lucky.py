"""Lucky imaging: rank a burst by sharpness, keep the best few, stack them.

The whole premise of planetary work is that seeing is not a constant blur but a
lottery: over a few hundred frames a handful arrive through a moment of calm
atmosphere and the rest do not. Averaging all of them buys noise reduction and
throws the detail away; averaging the sharpest 10-25 percent keeps both. This
module is the pure-numpy core of that - no I/O, no hub, no camera.

TWO CHOICES THAT LOOK ARBITRARY AND ARE NOT.

1. Sharpness is GRADIENT ENERGY, not variance-of-Laplacian. Variance-of-
   Laplacian is the usual answer and it is the wrong one for a planetary disc: a
   Laplacian is dominated by the limb, a step edge that stays a step edge in
   every frame including the ruined ones. It therefore ranks frames by how
   crisply the disc's OUTLINE landed, which is mostly a function of where the
   seeing happened to put it, and barely moves with the belt and festoon detail
   that decides whether a stack is worth keeping. Gradient energy integrates
   over the whole disc, so surface structure actually contributes.

2. Alignment is the INTENSITY-WEIGHTED CENTROID, not registration.register.
   That function matches star constellations - it detects point sources and fits
   a translation to the pattern they make (imaging/registration.py). A planetary
   frame has no constellation: it has one extended, saturated-in-the-middle disc
   and usually nothing else in the subframe at all. Star detection on it returns
   either nothing or a scatter of noise peaks on the limb, and a "registration"
   fitted to those is a random walk. The centroid of the above-background flux
   is the disc's own centre of mass and is stable to a fraction of a pixel.

Shifts are INTEGER (imaging/livestack._axis_slices, the same helper the live
stacker uses). Sub-pixel resampling would be better and is deliberately not here
yet: it needs its own interpolation choice and its own tests, and a half-pixel
residual on a 25-frame planetary stack is far below the seeing that survived the
selection.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .livestack import _axis_slices

#: Default fraction of a burst to keep, in percent. 25 is the conventional
#: starting point; a night of good seeing wants less, a poor one more.
DEFAULT_KEEP_PCT = 25.0


def sharpness(frame) -> float:
    """Gradient energy: the mean of dx^2 + dy^2 over the frame.

    Comparable only WITHIN one recording. Gain, exposure and the target's
    altitude all scale it, so a number from one .ser means nothing against a
    number from another - which is all a lucky-imaging ranking ever needs.
    """
    a = np.asarray(frame, dtype=np.float64)
    if a.ndim != 2 or a.size == 0:
        raise ValueError(f"sharpness needs a 2-D frame, got shape {a.shape}")
    if a.shape[1] < 2 or a.shape[0] < 2:
        return 0.0
    dx = np.diff(a, axis=1)
    dy = np.diff(a, axis=0)
    return float(np.mean(dx * dx) + np.mean(dy * dy))


def disc_centroid(frame, *, bg_sigma: float = 3.0) -> tuple[float, float] | None:
    """(x, y) centre of mass of the above-background pixels, or None.

    Background is the median and the noise is the MAD (x 1.4826), the same
    robust pair imaging/stars.detect_stars uses - a mean and a standard
    deviation would both be dragged by the disc itself, which is the brightest
    thing in the frame and often a large fraction of it.

    Weights are the background-SUBTRACTED flux, so a bright sky floor cannot
    pull the centroid toward the geometric centre of the subframe. None when
    nothing rises above the threshold, which is the honest answer for a frame
    that lost the planet.
    """
    a = np.asarray(frame, dtype=np.float64)
    if a.ndim != 2 or a.size == 0:
        return None
    bg = float(np.median(a))
    noise = float(np.median(np.abs(a - bg))) * 1.4826
    if noise <= 0:
        noise = float(a.std()) or 1.0
    weights = a - (bg + bg_sigma * noise)
    np.clip(weights, 0.0, None, out=weights)
    total = float(weights.sum())
    if total <= 0:
        return None

    # A HANDFUL OF PIXELS ABOVE THRESHOLD IS NOISE, NOT A PLANET.
    #
    # A k-sigma cut on pure noise still passes a predictable fraction of the
    # frame - 0.135% at 3 sigma, about 5 pixels in a 64x64 subframe - and a
    # centroid computed from those five is a confident number pointing at
    # nowhere. Since the shift is applied without further checking, that number
    # would drag a whole frame across the stack.
    #
    # The floor is the noise's own expectation rather than a magic constant: the
    # one-sided Gaussian tail at ``bg_sigma``, times the pixel count, times five.
    # A real disc clears it by an order of magnitude (radius 12 is ~450 pixels
    # against a floor of ~28), and a frame that lost the planet says so.
    above = int(np.count_nonzero(weights))
    tail = 0.5 * math.erfc(float(bg_sigma) / math.sqrt(2.0))
    if above <= max(4.0, 5.0 * tail * a.size):
        return None
    ys, xs = np.indices(a.shape)
    return (float((weights * xs).sum() / total),
            float((weights * ys).sum() / total))


@dataclass
class LuckyStack:
    """The stack plus the evidence for how it was chosen."""
    image: np.ndarray
    kept: list[int]
    scores: list[float]
    keep_pct: float
    reference: int
    shifts: list[tuple[int, int]]
    aligned: int


def lucky_stack(frames, *, keep_pct: float = DEFAULT_KEEP_PCT) -> LuckyStack:
    """Rank ``frames`` by sharpness, keep the top ``keep_pct``, mean-stack them.

    ``frames`` is any sequence of same-shaped 2-D arrays. The reference is the
    single sharpest frame, so the stack lands where the best frame put the
    planet rather than where an average of everything put it.

    A frame whose centroid cannot be measured is stacked UNSHIFTED rather than
    dropped: it earned its place on sharpness, and a frame with no measurable
    disc is one where the alignment would be a guess either way. ``aligned``
    reports how many were actually shifted, so a stack that quietly fell back is
    visible.
    """
    frames = [np.asarray(f) for f in frames]
    if not frames:
        raise ValueError("lucky_stack needs at least one frame")
    shape = frames[0].shape
    for i, f in enumerate(frames):
        if f.shape != shape:
            raise ValueError(
                f"frame {i} is {f.shape} but frame 0 is {shape}; a burst is one "
                "fixed ROI by construction, so a different shape means the "
                "frames did not come from the same recording")

    scores = [sharpness(f) for f in frames]
    pct = min(100.0, max(0.1, float(keep_pct)))
    n_keep = max(1, int(round(len(frames) * pct / 100.0)))
    # Descending sharpness; ties resolved by original order so the selection is
    # deterministic and a re-run of the same file gives the same stack.
    order = sorted(range(len(frames)), key=lambda i: (-scores[i], i))
    kept = sorted(order[:n_keep])
    reference = order[0]

    ref_centroid = disc_centroid(frames[reference])
    acc = np.zeros(shape, dtype=np.float64)
    counts = np.zeros(shape, dtype=np.float64)
    shifts: list[tuple[int, int]] = []
    aligned = 0
    h, w = shape
    for idx in kept:
        dx = dy = 0
        cur = disc_centroid(frames[idx]) if ref_centroid is not None else None
        if ref_centroid is not None and cur is not None:
            dx = int(round(cur[0] - ref_centroid[0]))
            dy = int(round(cur[1] - ref_centroid[1]))
            aligned += 1
        shifts.append((dx, dy))
        if abs(dx) >= w or abs(dy) >= h:
            # The disc left the subframe entirely; stacking it would add a
            # zero-overlap slice, which numpy accepts silently as a no-op.
            continue
        dst_y, src_y = _axis_slices(h, dy)
        dst_x, src_x = _axis_slices(w, dx)
        acc[dst_y, dst_x] += frames[idx][src_y, src_x]
        counts[dst_y, dst_x] += 1.0

    # Divide by the per-pixel COUNT, not by len(kept): an integer shift leaves an
    # edge strip that only some frames covered, and dividing that strip by the
    # full count would darken a border into the stack.
    stacked = np.zeros(shape, dtype=np.float64)
    np.divide(acc, counts, out=stacked, where=counts > 0)
    return LuckyStack(image=stacked.astype(np.float32), kept=kept,
                      scores=scores, keep_pct=pct, reference=reference,
                      shifts=shifts, aligned=aligned)
