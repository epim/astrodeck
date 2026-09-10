"""Focus follows temperature, and the tube tells you by how much (#D-RIG-2).

A refractor's focus walks as the tube cools, and the walk is close to linear in
temperature. That makes it the one focus error the run can correct for FREE:
between two frames, with the shutter closed, in one short move, with no frame
spent measuring anything. Everything here is that move and the arithmetic
behind it.

WHY THIS IS NOT `standards.refocus_on_temp_delta_c`, and why turning that one
off would be a mistake. The two settings sound like the same idea and are not:

    compensation is an OFFSET.  Every frame boundary, nudge the focuser by the
                               drift since the reference. Cost: one short move.
    the trigger is a TRIGGER.   When the temperature has moved this far, stop
                               and run a whole autofocus sweep. Cost: minutes,
                               and the sky it was pointing at.

They answer different questions. Compensation tracks the part of the drift we
already know the shape of; the trigger catches everything compensation cannot -
a coefficient that is slightly wrong and accumulating, a change in seeing, a
mis-measured filter offset, a tube that has not reached equilibrium and is
therefore not on the line at all. Compensation never measures itself against a
star; only the sweep does. So a rig that switched the trigger off because
compensation was on would be a rig whose accumulated error is never measured
against anything, and the error would grow all night with nothing able to
notice. Both run.

TEMP_COMP_PRECEDENCE, the whole rule in one sentence:

    "Temperature compensation moves between frames; the refocus trigger stops
    and re-measures. When both are on, compensation runs first and the trigger
    still fires."

Compensation goes first because it is cheap and it leaves the focuser
near-correct; if the trigger then fires, the sweep starts from a better place.
The trigger is NOT suppressed by a compensating move.

ONE BASELINE FOR BOTH. `SequenceEngine._capture_focus_temp()` re-anchors the
trigger's baseline after every autofocus attempt, and it re-anchors THIS
module's reference in the same call, from the same reading. That is deliberate:
two clocks that are supposed to agree about "when was focus last found" will
eventually disagree if they are set separately, and the disagreement is silent.

SECOND-ORDER, and intended: with compensation on, the trigger fires LESS often.
The focuser is being kept near the right place, so the sweep that would have
been called to fix the linear drift is not needed, and what the trigger catches
is the residue. Fewer sweeps is the saving; the sweeps that do run are the ones
that were actually worth their minutes.

THE SIGN CONVENTION, stated once so nothing has to infer it. The coefficient is
steps per degree of TEMPERATURE, not per degree of cooling, and the correction
is the industry one that ASCOM-family software uses:

    position = reference_position + steps_per_c * (temperature - reference_temp)

So a POSITIVE `steps_per_c` moves the drawtube IN as the night cools (which is
what a refractor whose focus shortens with temperature wants) and a NEGATIVE
one moves it OUT. Measure it with the sign attached: focus at 12 C, focus again
at 4 C, and divide (position at 4 C minus position at 12 C) by (4 minus 12).
Getting the sign backwards doubles the error instead of removing it, which is
why `max_step_per_move` exists and why the refocus trigger is never turned off.

This module is PURE. No devices, no config store, no I/O - it takes numbers and
returns a decision, so the whole rule table is testable without a focuser and
the engine's copy of it cannot drift from the tested one.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

#: The precedence rule, kept as a string so the docs and the UI can quote the
#: same sentence the engine implements rather than paraphrasing it.
TEMP_COMP_PRECEDENCE = (
    "Temperature compensation moves between frames; the refocus trigger stops "
    "and re-measures. When both are on, compensation runs first and the "
    "trigger still fires.")


class TempCompConfig(BaseModel):
    """Move the focuser between frames as the tube cools (#D-RIG-2).

    A refractor's focus walks with temperature at a rate that is a property of
    the OTA and the focuser together, and it is measurable: focus at 12 C,
    focus again at 4 C, divide the step difference by the degrees. That number
    is `steps_per_c`, and it is signed - a tube that shrinks as it cools needs
    the drawtube to come IN, which on this rig is negative.

    THIS IS NOT `standards.refocus_on_temp_delta_c`, and the two must not be
    confused. That one is a TRIGGER: when the temperature has moved this far,
    stop and run a whole autofocus sweep. This is an OFFSET: every frame
    boundary, nudge the focuser by the drift since the reference, costing one
    short move and no frames. See TEMP_COMP_PRECEDENCE below for which wins.
    """
    enabled: bool = False
    #: SIGNED; 0 disables the loop even when enabled
    steps_per_c: float = Field(0.0, ge=-500, le=500)
    #: None is NOT 0
    reference_temp_c: float | None = None
    reference_position: int | None = None
    #: backstop under a wrong coefficient / a glitching thermometer
    max_step_per_move: int = Field(200, ge=1, le=5000)
    #: under the EAF's backlash a 3-step move turns the motor, not the tube
    deadband_steps: int = Field(5, ge=0, le=500)


@dataclass(frozen=True)
class TempCompDecision:
    """What to do at this frame boundary, and WHY in words.

    ``reason`` is not decoration: every one of the nine rules below can end a
    night's worth of boundaries in "nothing happened", and a no-op with no
    explanation is indistinguishable from a feature that is not wired up. The
    status bus carries this string.
    """
    #: Absolute focuser position to move to, or None for "do not move".
    move_to: int | None
    #: Signed steps the move covers; 0 whenever ``move_to`` is None.
    delta_steps: int
    reason: str
    #: The move was shortened - by the per-move backstop or by the focuser's
    #: own travel limits - so it does NOT fully correct the drift.
    clamped: bool
    #: The caller should adopt the current reading as the new reference.
    rebased: bool


def decide(*, cfg: TempCompConfig, current_temp_c: float | None,
           current_position: int, focuser_max: int) -> TempCompDecision:
    """The whole rule table, in order. Pure.

    ``focuser_max`` is the focuser's own top of travel (``Focuser.max_position``).
    A target outside 0..max is clamped rather than refused: the drawtube being
    at the end of its travel is a fact about the rig, not an error, and the
    partial correction is still better than none.
    """
    if not cfg.enabled:
        return TempCompDecision(None, 0, "temperature compensation is off",
                                False, False)
    if cfg.steps_per_c == 0:
        return TempCompDecision(None, 0, "no coefficient set", False, False)
    if current_temp_c is None:
        # NEVER GUESS. A focuser with no thermometer reports None, and an
        # assumed ambient would move the drawtube on the strength of a number
        # nothing measured.
        return TempCompDecision(None, 0, "this focuser has no thermometer",
                                False, False)
    if cfg.reference_temp_c is None or cfg.reference_position is None:
        # SEED ON FIRST USE, the same shape as the refocus trigger's baseline:
        # "compensate from where it is now" is what an operator who focused by
        # hand means. The caller persists it; this module does not own state.
        return TempCompDecision(None, 0,
                                "first reading - anchored the reference here",
                                False, True)

    current_position = int(current_position)
    d_temp = float(current_temp_c) - float(cfg.reference_temp_c)
    target = int(cfg.reference_position) + int(round(cfg.steps_per_c * d_temp))
    delta = target - current_position

    if abs(delta) < cfg.deadband_steps:
        # Under the EAF's slack a short move turns the motor and not the tube,
        # so it costs a move and buys nothing.
        return TempCompDecision(
            None, 0,
            f"drift is {delta} steps, under the {cfg.deadband_steps}-step "
            f"deadband", False, False)

    clamped = False
    if abs(delta) > cfg.max_step_per_move:
        capped = cfg.max_step_per_move if delta > 0 else -cfg.max_step_per_move
        reason = (f"drift asks for {delta} steps; clamped to {capped}")
        delta = capped
        target = current_position + delta
        clamped = True
    else:
        reason = (f"temperature is {d_temp:+.1f} C from the reference; "
                  f"moving {delta} steps")

    if target < 0:
        target = 0
        delta = target - current_position
        reason = f"the focuser's travel stops at 0; clamped to {target}"
        clamped = True
    elif target > int(focuser_max):
        target = int(focuser_max)
        delta = target - current_position
        reason = (f"the focuser's travel stops at {focuser_max}; "
                  f"clamped to {target}")
        clamped = True

    if delta == 0:
        # The clamp landed exactly where the focuser already is.
        return TempCompDecision(None, 0, reason, clamped, False)
    return TempCompDecision(target, delta, reason, clamped, False)


def predict(cfg: TempCompConfig, temp: float | None, position: int,
            max_position: int) -> TempCompDecision:
    """``decide`` under a read-only name, for the status bus.

    Identical arithmetic, positional arguments, and the same guarantee of no
    side effects - the point of the alias is that a caller showing the operator
    "where compensation would put the focuser right now" is visibly not the
    caller that moves it.
    """
    return decide(cfg=cfg, current_temp_c=temp, current_position=position,
                  focuser_max=max_position)


def status_node(cfg: TempCompConfig, *, temperature_c: float | None,
                position: int | None, focuser_max: int,
                last_move_steps: int | None = None,
                last_reason: str | None = None) -> dict:
    """The `focuser.temp_comp` status node (S7c).

    ``predicted_position`` is ``predict`` run against the LIVE reading, so the
    operator can see what the next frame boundary would do before it does it.
    None when there is no position to predict from or the rules say no move.
    """
    d = None
    if position is not None:
        d = predict(cfg, temperature_c, int(position), int(focuser_max))
    return {
        "enabled": bool(cfg.enabled),
        "steps_per_c": float(cfg.steps_per_c),
        "reference_temp_c": cfg.reference_temp_c,
        "reference_position": cfg.reference_position,
        "predicted_position": (d.move_to if d is not None else None),
        "last_move_steps": last_move_steps,
        "last_reason": (last_reason if last_reason is not None
                        else (d.reason if d is not None else None)),
    }
