# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Is the camera at the mosaic's planned angle? A pure verdict on the sky angle
a hop's centring solve recorded (mosaic spec 5.6 step 4, Appendix A.2; U-04,
#189).

A mosaic is laid out at one position angle, and every panel must be shot at it:
a panel turned away from the layout shifts its neighbours sideways by
``step x sin(theta)`` and eats the corner overlap. The S2 group driver checks
the angle on every hop, whatever the rotator state, because a fixed camera
cannot correct itself and a rotator that skipped its move says so only in a
flag. It is the arithmetic, pinned on its own; the engine's hop
(`SequenceEngine._group_angle_check`) and an unframed target's angle lock
(`SequenceEngine._settle_locked_angle`, ruling 9) are its callers since S2.

THE MEASUREMENT is ``hub.last_sky_angle``, the record
``sky_angle.note_solved_rotation`` writes on every imaging-camera solve (the
goto centring included). Two of its fields are read: ``pa_deg`` and
``exposed_at``. The PA is CROTA2-convention on both sides of the comparison
(the planned angle is ``TargetGroup.pa_deg``), so the check compares like with
like and does not depend on how #145 settles the rotator's sign.

FRESHNESS IS JUDGED BY ``exposed_at``, NOT ``solved_at``. The record is the
newest solve of the imaging camera, whichever path made it. A frame exposed on
the previous panel whose solve finishes after this hop began has a ``solved_at``
inside the hop and an angle from the wrong panel. Only the moment the shutter
opened says which pointing the angle describes, so a record counts when it was
exposed at or after ``since_ts`` (the hop start), and not otherwise.

MOD 180. A centred rectangle turned half a turn covers the same sky, so PA 210.4
is the same footprint as PA 30.4. This is the framing equivalence
``rotation.angle_equals_mod180`` already encodes (``rotation.py:26-30``). That
function answers yes or no against a tolerance it reduces ``% 180``; the verdict
needs the error itself, in [0, 90], so it is computed here.

THREE VERDICTS, because "no measurement" is not "off". What the engine does
with a missing measurement depends on the mode (5.6 step 4: a fixed camera
already verified tonight shoots, one never verified defers, a calibrated
rotator's own read may stand in). Folding it into "off" would set a group aside
over a failed solve; folding it into "ok" would lay tiles blind. The verdict
says which case it is and why, in words, and leaves the policy to the caller.

NaN NEVER PASSES. Every comparison with NaN is false, so a NaN anywhere in
``error > tolerance`` or ``exposed_at < since_ts`` would pass every angle or
every stale record. A record whose PA or exposure time is not a finite number
is ``no_measurement``; an argument that is not a finite number is a caller bug
and raises.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

VerdictKind = Literal["ok", "off", "no_measurement"]


@dataclass(frozen=True)
class AngleVerdict:
    """What :func:`angle_verdict` concluded."""

    #: ``"ok"`` within tolerance, ``"off"`` beyond it, ``"no_measurement"`` when
    #: no sky angle from this hop exists.
    kind: VerdictKind
    #: The PA this hop measured, as recorded; ``None`` for ``no_measurement``,
    #: even when a stale record exists, so an old number cannot be mistaken for
    #: this hop's.
    measured_deg: float | None
    #: Unsigned distance from the planned angle mod 180, in [0, 90]; ``None``
    #: for ``no_measurement``.
    error_deg: float | None
    #: The verdict in words, naming the numbers it was reached on.
    reason: str


def _finite(value: Any) -> float | None:
    """``value`` as a finite float, or ``None`` for anything else."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _finite_arg(name: str, value: Any) -> float:
    f = _finite(value)
    if f is None:
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return f


def _no_measurement(reason: str) -> AngleVerdict:
    return AngleVerdict(kind="no_measurement", measured_deg=None,
                        error_deg=None, reason=reason)


def angle_verdict(record: Mapping[str, Any] | None, *, planned_pa_deg: float,
                  since_ts: float, tolerance_deg: float) -> AngleVerdict:
    """Judge the sky angle in ``record`` against ``planned_pa_deg``.

    ``record`` is ``hub.last_sky_angle`` (or ``None`` before any imaging solve
    recorded one). ``since_ts`` is the hop start on the ``time.time()`` clock
    the record's ``exposed_at`` uses. ``tolerance_deg`` is the group's
    ``angle_tolerance_deg`` (:func:`angle_tolerance_deg`); an error equal to it
    is ``ok``, because 5.6 acts only beyond it.

    Raises ``ValueError`` when ``planned_pa_deg``, ``since_ts`` or
    ``tolerance_deg`` is not a finite number, or ``tolerance_deg`` is negative.
    A group with no planned angle or no tolerance has no angle check, and must
    not call this.
    """
    planned = _finite_arg("planned_pa_deg", planned_pa_deg)
    since = _finite_arg("since_ts", since_ts)
    tol = _finite_arg("tolerance_deg", tolerance_deg)
    if tol < 0.0:
        raise ValueError(f"tolerance_deg must not be negative, got {tol!r}")

    if record is None:
        return _no_measurement(
            "no sky angle has been recorded: no imaging-camera solve has "
            "measured one")
    measured = _finite(record.get("pa_deg"))
    if measured is None:
        return _no_measurement(
            f"the newest sky-angle record has no finite PA "
            f"({record.get('pa_deg')!r})")
    exposed = _finite(record.get("exposed_at"))
    if exposed is None:
        return _no_measurement(
            f"the newest sky angle (PA {measured:.1f}) carries no exposure "
            f"time, so nothing shows it was measured on this hop")
    if exposed < since:
        return _no_measurement(
            f"the newest sky angle (PA {measured:.1f}) was exposed "
            f"{since - exposed:.1f} s before this hop started, so this hop "
            f"measured no angle")

    d = (measured - planned) % 180.0
    error = min(d, 180.0 - d)
    # Name the half-turn twin when the reading is nearer the planned angle's
    # opposite: "reads PA 210.4, 0.4 deg from 30.0" otherwise looks like a typo.
    twin = ""
    if abs(((measured - planned + 180.0) % 360.0) - 180.0) > 90.0:
        twin = (f" (the same footprint as PA {(measured + 180.0) % 360.0:.1f},"
                f" turned 180 deg)")
    head = (f"the camera reads PA {measured:.1f}{twin} and this mosaic is laid "
            f"out at {planned:.1f}: {error:.1f} deg apart")
    if error > tol:
        return AngleVerdict(kind="off", measured_deg=measured, error_deg=error,
                            reason=f"{head}, beyond the {tol:.1f} deg tolerance")
    return AngleVerdict(kind="ok", measured_deg=measured, error_deg=error,
                        reason=f"{head}, within the {tol:.1f} deg tolerance")


def fresh_sky_angle(record: Mapping[str, Any] | None,
                    since_ts: float) -> Mapping[str, Any] | None:
    """``record`` when it is a sky angle measured at or after ``since_ts``,
    otherwise ``None``.

    The freshness rule :func:`angle_verdict` applies, for a caller that needs
    the record itself rather than a verdict against a planned angle: an
    unframed target's angle lock (mosaic spec Revision 2, ruling 9) takes the
    first fresh record of the acquisition, whatever angle it reads. Fresh
    means a finite PA and a finite ``exposed_at`` not before ``since_ts``,
    judged by the exposure and never the solve, for the reason the module
    docstring gives. Raises ``ValueError`` when ``since_ts`` is not a finite
    number, since a NaN there would pass every record."""
    since = _finite_arg("since_ts", since_ts)
    if not isinstance(record, Mapping):
        return None
    if _finite(record.get("pa_deg")) is None:
        return None
    exposed = _finite(record.get("exposed_at"))
    if exposed is None or exposed < since:
        return None
    return record


def angle_tolerance_deg(fov_x: float, fov_y: float, overlap: float,
                        k: float = 0.5) -> float:
    """The camera angle error, in degrees, that uses the fraction ``k`` of the
    corner overlap (Appendix A.2).

    Turning every panel by theta about its own centre, on a grid laid out at
    the planned angle, shifts each neighbour perpendicular to the line between
    them by ``step x sin(theta)``, with ``step = f x (1 - overlap)`` along that
    line. The corner overlap shrinks by that shift, and the binding neighbour is
    the one across the short side, so::

        sin(theta) = k * min(ov*fy / (fx*(1-ov)), ov*fx / (fy*(1-ov)))

    ``fov_x`` and ``fov_y`` are the panel's field in degrees (any one unit, the
    formula takes their ratio); ``overlap`` is the fraction in [0, 1) that
    ``framing.MosaicSpec`` uses, so 25% is 0.25.

    ``k`` is the share of the overlap the angle may spend. A.2 budgets half the
    overlap for convergence and angle error together, leaving the other half
    for pointing error, so the caller passes ``k = 0.5 - c`` with ``c`` the
    convergence share from A.1. A ``k`` at or below zero (convergence alone
    uses half the overlap, M15), or one that is not a finite number, is zero
    tolerance: an infinite ``k`` times a zero overlap is NaN, and ``min(1.0,
    nan)`` is 1.0, so it would come back as 90 deg. A budget no turn can spend
    saturates at 90 deg, the largest error there is mod 180.

    For a single row or column no hole can open between panels, so the same
    number is conservative there.

    Raises ``ValueError`` for a field that is not a finite positive number or
    an overlap outside [0, 1).
    """
    fx = _finite_arg("fov_x", fov_x)
    fy = _finite_arg("fov_y", fov_y)
    ov = _finite_arg("overlap", overlap)
    if fx <= 0.0 or fy <= 0.0:
        raise ValueError(f"fov_x and fov_y must be positive, got {fx!r} x {fy!r}")
    if not 0.0 <= ov < 1.0:
        raise ValueError(
            f"overlap is a fraction in [0, 1) (25% is 0.25), got {overlap!r}")
    if not (k > 0.0 and math.isfinite(k)):
        return 0.0
    s = k * min(ov * fy / (fx * (1.0 - ov)), ov * fx / (fy * (1.0 - ov)))
    return math.degrees(math.asin(min(1.0, s)))
