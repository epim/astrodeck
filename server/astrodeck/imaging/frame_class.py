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

THE OBSTRUCTION TEST is ``imaging.stars.find_obstruction`` (#1145): the frame
read as a coarse grid of block medians, asking for a smooth dark region with a
sharp edge and stars on the lit side only. It lives there because
``cloud_score`` needs it too: a score over a frame part behind a tree describes
neither the tree nor the sky, so ``cloud_score`` judges the lit blocks alone
and reports ``CloudResult.obstructed``, which is what is read here.

PURE: no I/O, no clock, no camera. ``imaging.stars.detect_stars`` is run here
only when the caller did not hand in the stars it already found.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from ..solve import light
from ..solve.light import Reference
from .clouds import cloud_score
# The obstruction test's constants live in ``stars``; the classifier's tests
# and docs name this one here (#1108).
from .stars import (HFR_BOX_PX, OBSTRUCTION_MIN_LIT_STARS,  # noqa: F401
                    Star, detect_stars)


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
    if cloud.obstructed:
        return FrameClass.OBSTRUCTED
    if light.classify(data, ctx.reference).kind == light.NO_LIGHT:
        return FrameClass.NO_LIGHT
    return FrameClass.CLOUD if cloud.cloudy else FrameClass.UNKNOWN
