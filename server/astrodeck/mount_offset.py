"""Relative mount nudges: the absolute target a "move 5 arcmin east" asks for.

D-RIG-4. Pure geometry, no device and no I/O, so the route in ``api/app.py``
stays the thin thing it is - it reads the mount, converts to J2000, calls
``nudge_target`` and slews the answer through the same guards a plain goto
already passes.

The whole reason this is a module and not three lines inside the route is the
``cos(dec)`` division below. It is the one part of a nudge that is easy to
leave out and impossible to notice from the desk chair: a nudge written without
it works perfectly at the equator and is silently, progressively wrong the
closer the mount gets to the pole.
"""
from __future__ import annotations

import math

#: A nudge smaller than this is not a pointing correction, it is a typo or a
#: stray touch: one arcminute is already well inside the pointing error of every
#: mount here, and below it the mount's own backlash and settle noise are larger
#: than the move being asked for.
MIN_NUDGE_ARCMIN = 1.0
#: 10 degrees, the design's own upper stop. Past this a relative nudge is not
#: what the operator meant - a goto is, and the goto route is right there with
#: the target search in front of it.
MAX_NUDGE_ARCMIN = 600.0

#: Arcminutes of sky per hour of right ascension at dec 0: 15 degrees x 60.
_ARCMIN_PER_RA_HOUR = 900.0

#: An RA offset is never taken further than half a turn. Past 12 hours the
#: "shorter way round" is the other direction, so a bigger number is not a
#: bigger move - it is the same move described the long way.
#:
#: This is also the ONLY thing standing between the pole and a nonsense answer.
#: At dec 90 the cos(dec) division is by 6.12e-17, so an unbounded offset comes
#: out around 1e16 hours - a number that survives the mod-24 wrap and reaches
#: the mount looking perfectly ordinary. Saturating instead means an RA nudge at
#: the pole lands half a turn away, which is the same point in the sky.
_MAX_RA_OFFSET_HOURS = 12.0

_AXES = ("ra", "dec")


def parse_nudge(axis: str, arcmin: float) -> float:
    """Validate one nudge request and return its signed size in arcminutes.

    Raises ``ValueError`` for an unknown axis, for a non-finite size, and for a
    size outside ``MIN_NUDGE_ARCMIN..MAX_NUDGE_ARCMIN`` IN ABSOLUTE VALUE - the
    bounds are about how far the tube moves, and a tap west is exactly as large
    a move as the same tap east. The route turns the ValueError into a 422.
    """
    if axis not in _AXES:
        raise ValueError(f"a nudge axis is 'ra' or 'dec', not {axis!r}")
    size = float(arcmin)
    # NaN/inf FIRST and on its own line. The bound below is written as two
    # explicit comparisons rather than a chained ``MIN <= x <= MAX``, because
    # the chained form silently doubles as a NaN filter (every NaN comparison
    # is False) and a guard nobody can see is a guard nobody keeps.
    if not math.isfinite(size):
        raise ValueError("a nudge is 1 to 600 arcminutes")
    if abs(size) < MIN_NUDGE_ARCMIN or abs(size) > MAX_NUDGE_ARCMIN:
        raise ValueError("a nudge is 1 to 600 arcminutes")
    return size


def nudge_target(ra_hours: float, dec_deg: float, axis: str,
                 arcmin: float) -> tuple[float, float]:
    """The absolute (ra_hours, dec_deg) a relative nudge asks for.

    RA IS NOT A GREAT CIRCLE. ``arcmin`` is an angle ON THE SKY, so an RA offset
    has to be divided by cos(dec) before it becomes an offset in right
    ascension: at dec 60 a 10-arcmin nudge east is 20 arcmin of RA, and at dec
    89 it is 573. Skipping this makes the pad progressively useless the closer
    the target is to the pole, in the exact region where a user is most likely
    to be nudging (polar alignment, a circumpolar target).

    Dec is clamped at the poles rather than wrapped: a nudge that would take
    the mount past +90 has no meaningful continuation, and wrapping it would
    swing the RA by 12 hours in one tap.
    """
    if axis not in _AXES:
        raise ValueError(f"a nudge axis is 'ra' or 'dec', not {axis!r}")
    ra = float(ra_hours)
    dec = float(dec_deg)
    size = float(arcmin)

    if axis == "dec":
        # North is positive. The clamp is the whole behaviour at the top: the
        # RA the caller passed in comes back untouched, so a tap that runs into
        # the pole stops there instead of flipping the mount to the far side.
        return ra % 24.0, max(-90.0, min(90.0, dec + size / 60.0))

    # East is positive, and this is the cos(dec) division the docstring is about.
    # No floor on cos_dec: dec is clamped into [-90, 90] first, and math.cos
    # never returns 0.0 across that range (cos(radians(90)) is 6.12e-17), so the
    # division cannot fail. What DOES need bounding is the answer, and
    # _MAX_RA_OFFSET_HOURS below is what bounds it.
    cos_dec = math.cos(math.radians(max(-90.0, min(90.0, dec))))
    offset_hours = size / cos_dec / _ARCMIN_PER_RA_HOUR
    offset_hours = max(-_MAX_RA_OFFSET_HOURS,
                       min(_MAX_RA_OFFSET_HOURS, offset_hours))
    return (ra + offset_hours) % 24.0, dec
