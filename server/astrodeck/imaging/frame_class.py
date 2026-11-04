# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Why does this frame hold no usable stars? One answer, for every caller.

THE DEFECT (#1108). Four consumers each decided on their own why a frame had
no stars, and each decided wrong in a different way: the centring hold blamed
"the sky or the geometry" behind a tree (#1093), the no-light hold read a
missing plate solver as "no light yet" and retried for the whole window
(#721), autofocus discarded a correct slope because a lightless sky starved
the sweep (#1077), and the self-shot no-light reference was never held against
the frames that look like it (#308). ``classify`` is the one function that
names the cause, from the frame's pixels and the little the caller knows.

THE CLASSES (``FrameClass``):

  * ``STARS``: the frame holds enough stars to measure; nothing is wrong with
    the light path.
  * ``OBSTRUCTED``: something in the foreground blocks part of the view: a
    smooth dark region with a SHARP edge against a lit sky, with the stars in
    the sky side only (a tree line, a roof, a wall). The #1093 signature.
  * ``NO_LIGHT``: the frame reads at the level this camera reads with no
    light on it (a cap, a closed cover, a blind filter slot). Needs a
    reference; ``solve.light`` is the authority and this reuses its verdict.
  * ``CLOUD``: starless and low contrast: ``imaging.clouds.cloud_score`` says
    cloudy.
  * ``SOLVER_MISSING``: not an image class. The solver layer reported that no
    plate solver exists on this rig (``hub.SOLVE_REASON_SOLVER_MISSING``), a
    fault that never clears, so the pixels are not even read.
  * ``UNKNOWN``: none of the above can be shown, or the frame cannot be
    judged (no pixels, a constant buffer, a rendered preview).

THE ORDER IS THE DESIGN. The solver fault first (it is about the rig, and the
image is irrelevant to it); then an unreadable frame; then ``STARS`` (a frame
with stars is not a problem frame, whatever else is in it); then
``OBSTRUCTED`` BEFORE ``NO_LIGHT``, because a frame mostly behind a tree reads
at the no-light level in its median and would be called a capped optic, the
one error the horizon cannot be learned from; then ``NO_LIGHT``, before
``CLOUD``, because a frame with no light in it also reads cloudy to
``cloud_score`` (its documented calibration: a black frame scores 0.59-0.67
against the 0.5 threshold); then ``CLOUD``; else ``UNKNOWN``.

WITHOUT A REFERENCE there is no ``NO_LIGHT``, exactly as ``solve.light`` says:
a median alone cannot tell a cap from a dark sky. The same frame then reads
``CLOUD`` (it is starless and flat) or ``UNKNOWN``. Callers that hold a
verdict from ``solve.light.failed_solve_error`` pass its reference:
``FailedSolveError.verdict.reference``.

A NO_LIGHT THROUGH A NARROWBAND FILTER IS NOT A CAP (#531). This function does
not know the filter. A caller that does maps ``NO_LIGHT`` on a narrowband
frame to what ``solve.light.NARROWBAND`` means, as ``failed_solve_error`` does.

THE OBSTRUCTION TEST reads the frame as a coarse grid of block medians, which
stars (a few pixels each) cannot move, and asks four things of it: some blocks
are DARKER than the sky by far more than a median's own noise; the dark blocks
are SMOOTH among themselves (a wall of tree is one level, not texture); the
drop from sky to dark happens in ONE block step (a SHARP edge: cloud bank
edges, vignetting and sky glow ramp over many blocks, so they never qualify);
and the lit side holds stars, while a bright starless patch is cloud and not a
sky. Every threshold below is named and says where its number came from.

PURE: no I/O, no clock, no camera. ``imaging.stars.detect_stars`` is run here
only when the caller did not hand in the stars it already found.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from ..solve import light
from ..solve.light import Reference
from .clouds import cloud_score
from .stars import HFR_BOX_PX, Star, detect_stars


class FrameClass(str, Enum):
    """The one answer. A ``str``, so it logs and serialises as its value."""
    STARS = "stars"
    OBSTRUCTED = "obstructed"
    NO_LIGHT = "no_light"
    CLOUD = "cloud"
    SOLVER_MISSING = "solver_missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FrameContext:
    """What a caller knows beyond the pixels. Every field is optional.

    ``solver_missing``: the solve layer reported
    ``hub.SOLVE_REASON_SOLVER_MISSING`` for this frame. Decides the class
    alone; the pixels are not read.

    ``reference``: the no-light reference for this frame's readout
    (``solve.light.Reference``), usually ``FailedSolveError.verdict.reference``
    or the first value of ``solve.light.reference_for``. Without it there is
    no ``NO_LIGHT``.

    ``stars``: the ``imaging.stars.detect_stars`` result, when the caller
    already ran it on this frame. Saves a second detection pass."""
    solver_missing: bool = False
    reference: Reference | None = None
    stars: list[Star] | None = None


#: Bright stars (``cloud_score``'s measure: peak at least 8 sigma over the
#: background) a frame needs to be ``STARS``. The sparse-field line the
#: autofocus and the engine share (``focus.native.SPARSE_FIELD_WARN``): a
#: frame under it is a sparse field, which no consumer treats as good stars.
#: Restated here because ``imaging`` does not import ``focus``.
STARS_MIN_BRIGHT = 15

#: The smallest frame, pixels per side, that is judged at all: two star
#: cutouts (``imaging.stars.HFR_BOX_PX``). A frame smaller than that cannot
#: hold a measurable star, so no class would mean anything.
MIN_FRAME_PX = 2 * HFR_BOX_PX

#: The grid the obstruction test reads, blocks per side. 8 x 8 puts a block
#: at 1/64 of the frame, big enough for a median to ignore stars and small
#: enough that a tree line crossing the field occupies several blocks.
OBSTRUCTION_GRID = 8

#: A block is not measured below this many pixels per side: its median would
#: be noise. A thumbnail is no basis for a horizon.
OBSTRUCTION_MIN_BLOCK_PX = 16

#: The block level that stands for "the sky": this percentile of the block
#: medians. 90 reads the lit side of a frame that is up to 90 % covered, and
#: it is not a maximum, so one bright block cannot set it.
OBSTRUCTION_SKY_PERCENTILE = 90.0

#: How far under the sky the DARKEST block must sit for the frame to hold
#: any structure at all, in standard errors of a block median
#: (``MEDIAN_SE_FACTOR x pixel sigma / sqrt(block pixels)``, the statistic
#: ``solve.light`` builds its no-light band from). Pure noise spreads 64 block
#: medians over about 4 standard errors, so 10 never fires on a flat frame,
#: while a real obstruction differs from the sky by many ADU against a median
#: that is known to a fraction of one.
OBSTRUCTION_SIGNIFICANCE_SE = 10.0

#: The smallest fraction of blocks that may be dark, and of blocks that must
#: stay lit, for the frame to hold a boundary at all. Both come from the grid:
#: 10 % is 6.4 blocks, the least that is a region and not a stray block.
OBSTRUCTION_MIN_DARK_FRACTION = 0.10
OBSTRUCTION_MIN_LIT_FRACTION = 0.10

#: The edge is SHARP when its largest dark-to-lit block step is at least this
#: fraction of the whole drop from sky to the dark region. A ramp across the
#: eight-block grid steps about 1/8 of its drop per block (vignetting, sky
#: glow, a cloud edge); a treeline steps nearly all of it. Half sits between
#: with room on both sides.
OBSTRUCTION_EDGE_FRACTION = 0.5

#: The dark blocks are SMOOTH when their levels spread by less than this
#: fraction of the drop (population standard deviation of the dark block
#: medians over the drop). An obstruction is one surface; a quarter of the
#: drop allows a lit edge and a gradient across it, and rejects a frame whose
#: "dark" blocks are really many levels of cloud.
OBSTRUCTION_SMOOTH_FRACTION = 0.25

#: Detected stars the lit side must hold. ``autofocus.MIN_STARS_PER_POINT``:
#: three stars is the least this project fits anything to. A bright region
#: with fewer is cloud lit from the ground, not a sky.
OBSTRUCTION_MIN_LIT_STARS = 3

#: The floor on one pixel's noise, in ADU: the rounding of integer ADU,
#: ``1 / sqrt(12)``, as ``solve.light`` derives it for ``OFFSET_STABILITY_ADU``.
#: Keeps a nearly flat frame from reading every block as infinitely different.
_MIN_PIXEL_NOISE_ADU = 1.0 / math.sqrt(12.0)


def _pixels(frame: Any) -> Any | None:
    """The frame's linear pixels, or None when it holds none that can be
    judged: no frame, a rendered preview (``data_is_linear`` False), or an
    object with no ``data``. An ndarray is its own pixels (it has a ``data``
    buffer attribute that is not them)."""
    if isinstance(frame, np.ndarray):
        return frame
    if frame is None or not getattr(frame, "data_is_linear", True):
        return None
    return getattr(frame, "data", None)


def _pixel_noise(img: np.ndarray) -> float:
    """One pixel's robust sigma from neighbour differences, in ADU. The
    differences cancel smooth structure (an obstruction's level, a gradient),
    so unlike the frame's own MAD it is not inflated by the thing the test is
    looking for; ``sqrt(2)`` because a difference of two pixels has twice the
    variance. MAD to sigma as ``solve.light`` does."""
    d = img[:, 1:] - img[:, :-1]
    mad = float(np.median(np.abs(d - np.median(d))))
    sigma = mad * light.MAD_TO_SIGMA / math.sqrt(2.0)
    return max(sigma, _MIN_PIXEL_NOISE_ADU)


def _block_medians(img: np.ndarray, grid: int) -> tuple[np.ndarray, int, int]:
    """``(medians, block_h, block_w)``: the median of each block of a
    ``grid`` x ``grid`` tiling (the remainder rows and columns are dropped)."""
    bh, bw = img.shape[0] // grid, img.shape[1] // grid
    tiles = img[:bh * grid, :bw * grid].reshape(grid, bh, grid, bw)
    return np.median(tiles.transpose(0, 2, 1, 3).reshape(grid, grid, -1),
                     axis=2), bh, bw


def _has_sharp_edge(levels: np.ndarray, dark: np.ndarray,
                    drop: float) -> bool:
    """Does the dark region meet the lit one in a single block step of at
    least ``OBSTRUCTION_EDGE_FRACTION`` of ``drop``? Steps are read between
    horizontally and vertically adjacent blocks that are on opposite sides."""
    steps = []
    for axis in (0, 1):
        across = np.diff(dark.astype(np.int8), axis=axis) != 0
        steps.append(np.abs(np.diff(levels, axis=axis))[across])
    steps = np.concatenate(steps)
    return bool(steps.size and steps.max() >= OBSTRUCTION_EDGE_FRACTION * drop)


def _lit_stars(stars: list[Star], dark: np.ndarray, bh: int, bw: int) -> int:
    """How many of ``stars`` sit in lit blocks."""
    grid = dark.shape[0]
    count = 0
    for s in stars:
        row = min(max(int(s.y // bh), 0), grid - 1)
        col = min(max(int(s.x // bw), 0), grid - 1)
        count += not dark[row, col]
    return count


def _obstructed(data: Any, stars: list[Star]) -> bool:
    """The obstruction test (see the module docstring)."""
    img = np.asarray(data).astype(np.float32)
    grid = OBSTRUCTION_GRID
    if min(img.shape) // grid < OBSTRUCTION_MIN_BLOCK_PX:
        return False
    levels, bh, bw = _block_medians(img, grid)
    se = light.MEDIAN_SE_FACTOR * _pixel_noise(img) / math.sqrt(bh * bw)
    sky = float(np.percentile(levels, OBSTRUCTION_SKY_PERCENTILE))
    lowest = float(levels.min())
    if sky - lowest < OBSTRUCTION_SIGNIFICANCE_SE * se:
        return False
    # Dark is the lower half of the range, so a block the edge crosses, whose
    # median sits near the sky, stays lit and does not roughen the dark region.
    dark = levels < (sky + lowest) / 2.0
    dark_fraction = float(dark.mean())
    if (dark_fraction < OBSTRUCTION_MIN_DARK_FRACTION
            or 1.0 - dark_fraction < OBSTRUCTION_MIN_LIT_FRACTION):
        return False
    drop = sky - float(np.median(levels[dark]))
    if float(np.std(levels[dark])) > OBSTRUCTION_SMOOTH_FRACTION * drop:
        return False
    if not _has_sharp_edge(levels, dark, drop):
        return False
    return _lit_stars(stars, dark, bh, bw) >= OBSTRUCTION_MIN_LIT_STARS


def classify(frame: Any, context: FrameContext | None = None) -> FrameClass:
    """Name why ``frame`` does or does not hold usable stars.

    ``frame`` is a 2D linear array or a ``CameraFrame`` (anything with
    ``data`` and, optionally, ``data_is_linear``). ``context`` carries what
    the caller already knows (``FrameContext``); None is an empty one.

    Pure and blocking (a median of the frame, and a star detection unless
    ``context.stars`` was given): call it off the event loop, as the other
    frame measurements are. Never raises for a frame it cannot judge; that is
    ``UNKNOWN``."""
    ctx = context or FrameContext()
    if ctx.solver_missing:
        return FrameClass.SOLVER_MISSING
    data = _pixels(frame)
    if (data is None or np.ndim(data) != 2
            or min(np.shape(data)) < MIN_FRAME_PX):
        return FrameClass.UNKNOWN
    stats = light.frame_stats(data)
    if stats is None or stats[3]:
        return FrameClass.UNKNOWN
    stars = ctx.stars if ctx.stars is not None else detect_stars(data)
    cloud = cloud_score(data, stars=stars)
    if not cloud.cloudy and cloud.bright_stars >= STARS_MIN_BRIGHT:
        return FrameClass.STARS
    if _obstructed(data, stars):
        return FrameClass.OBSTRUCTED
    if light.classify(data, ctx.reference).kind == light.NO_LIGHT:
        return FrameClass.NO_LIGHT
    return FrameClass.CLOUD if cloud.cloudy else FrameClass.UNKNOWN
