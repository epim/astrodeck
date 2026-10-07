# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What a flip attempt teaches about the mount is learned only from an
attempt made before transit, read at its goto (#489, H4-ENG-C; spec 5.7,
#127, the "margin is the plan's lead" paragraph).

THE DEFECT. `SequenceEngine._maybe_meridian_flip` recorded whether this
mount flips early (#127, `_learn_mount_flips_early`) from every attempt
with ``lead_s > 0`` and both sides readable. ``lead_s`` is the lead the
attempt was ENTITLED to (`_flip_lead_s`), not when it was made. An attempt
whose frame boundary was already past the meridian fired at once and still
carried the plan's lead whenever the process had not learned the trait,
and a mount that picks its side from the hour angle, as the AM5 does, is
flipped by a goto past the meridian: the flip-owed hold's re-slews, on a
night whose lead-time attempt could not read the side, wrote down a lesson
about flipping EARLY from attempts made after transit.

THE FIX. At the goto, the gate's own countdown to the target's meridian
(`FlipPoint.transit_at`, computed by `_flip_point` with the flip point)
says whether the attempt is being made before transit, by more than
`MERIDIAN_SIDE_MARGIN_S`, the band inside which a goto's side is the
mount's own reckoning of the hour angle. Only such an attempt teaches.
``lead_s`` no longer enters it.

THE NIGHTS are the clocked simulator (tests/_group_harness.py): one target,
A, crossing the fixture site's meridian 15 min in, shot in 30 s frames. The
simulator's mount picks its side from the hour angle at each slew, so a
goto before the meridian flips nothing and one past it flips.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad H4-ENG-C-mut, from a byte
backup), never in the shared tree (#254):

* "learning keyed on lead_s again": the learning's condition
  ``before_transit_s > MERIDIAN_SIDE_MARGIN_S`` put back as ``lead_s > 0``.
* "nothing teaches": that condition made ``False``.
* "the countdown read at the gate": ``before_transit_s`` taken when the
  gate computed its flip point, before the hold, instead of at the goto.
* "learning with no band" (added by the H4-ENG-C verifier, in its private
  copy H4-ENG-C-verify-mut): that condition made ``before_transit_s > 0``,
  so an attempt a few seconds before transit teaches.
"""
from __future__ import annotations

from _group_harness import (LON, T0, Night, group_hub, group_store,  # noqa: F401
                            single)
from astrodeck.devices.base import DeviceError
from astrodeck.sequence import SequencePlan, schedule
from astrodeck.sequence.engine import MERIDIAN_SIDE_MARGIN_S

from test_h4_flip_point_has_one_zero import _a_mount_that_reports_its_limit

#: A crosses 15 min into the night.
A_HA = -0.25
HOLDING = "holding for the meridian flip point"
#: A limit the mount reports before A's meridian, in seconds of hour angle,
#: INSIDE `MERIDIAN_SIDE_MARGIN_S`: an attempt aimed at it is made before
#: transit, but by less than the band.
INSIDE_BAND_S = 10.0


def _plan(*, lead_min: float | None = None, count: int = 60) -> SequencePlan:
    """A alone, 30 s frames, flips on; the plan's default 10 min lead unless
    ``lead_min`` says otherwise. Frames enough to run past the crossing and
    through a flip-owed hold."""
    kw = {} if lead_min is None else {"meridian_flip_lead_min": lead_min}
    return SequencePlan(
        name="one crossing", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=True, park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False,
        targets=[single("A", count=count, ha_h=A_HA)], **kw)


def _past_s(plan: SequencePlan, t: float) -> float:
    """Seconds of hour angle A is past its meridian at fake time ``t``."""
    return -schedule.hours_to_meridian_flip(plan.targets[0].ra_hours, LON,
                                            t) * 3600.0


def _attempts(night: Night) -> list[float]:
    """The fake time of every goto to A after its setup: each is a flip
    attempt, the flip-owed hold's re-slews included."""
    return [t for t, who in night.gotos if who == "A"][1:]


def _blind_before_transit(night: Night, plan: SequencePlan) -> list[float]:
    """The mount's side cannot be read while a flip attempt is being made
    before A's meridian: every ``pier_side`` read inside the gate's
    "meridian flip" step, the hub's own reads inside the flip included,
    raises as a dropped link does. Reads anywhere else, and every read once
    A is past its meridian, answer. Returns the fake time of each read
    that failed."""
    tel = night.hub.devices["telescope"]
    real = tel.pier_side
    blinded: list[float] = []

    async def pier_side():
        if (night.engine.state.get("detail") == "meridian flip"
                and _past_s(plan, night.clock.t) < 0):
            blinded.append(night.clock.t)
            raise DeviceError("pier side read timed out")
        return await real()

    night.mp.setattr(tel, "pier_side", pier_side)
    return blinded


async def _run(night: Night, plan: SequencePlan) -> None:
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"


async def test_the_flip_owed_hold_teaches_nothing_about_flipping_early(
        group_hub, monkeypatch):
    """A's lead-time attempt, ten minutes before transit, cannot read the
    side, so it teaches nothing and is taken as a flip (the conservative
    read), and the simulator, which cannot flip before transit, leaves the
    mount where it was. Past the meridian the flip-owed invariant finds A
    still on the side it was seen on before the crossing and holds, and
    each of its re-slews clears `_flip_no_op` and so carries the plan's
    10 min lead, the trait being unlearned. Those re-slews are made after
    transit, and one of them flips the mount; none of them is evidence
    about flipping EARLY, so the trait stays unlearned.

    RED ON THE TREE BEFORE THE FIX (observed), and the same under the
    mutant "learning keyed on lead_s again" (observed): the lesson written
    from the hold's first re-slew, 1.7 s past the meridian, where the
    simulator's goto error of 7 s of RA east left the side unchanged (the
    second re-slew flipped):
        AssertionError: the process learned "flips early: False" from
        attempts made past the meridian: A's attempts at [-600.0, 1.7,
        31.7] s, the lead-time one blind to the side
    """
    plan = _plan()
    night = Night(group_hub, monkeypatch, t0=T0)
    blinded = _blind_before_transit(night, plan)
    await _run(night, plan)
    attempts = [round(_past_s(plan, t), 1) for t in _attempts(night)]
    assert attempts and 590.0 <= -attempts[0] <= 610.0 and blinded, (
        f"premise: A's lead-time attempt came at its lead point and could "
        f"not read the side: attempts at {attempts} s, blind reads at "
        f"{[round(_past_s(plan, t), 1) for t in blinded]} s")
    assert night.said("A: meridian flip complete (pier side unreadable -> "
                      "unreadable)"), (
        f"premise: the blind attempt was taken as a flip: {night.said('A: ')}")
    assert night.said("A: a meridian flip is required"), (
        f"premise: the flip-owed hold held A: {night.said('A: ')}")
    assert night.said("the flip happened, resuming"), (
        f"premise: a re-slew of the hold flipped the mount: "
        f"{night.said('A: ')}")
    # `_enforce_flip_owed` holds only once A has crossed (``ttf_h <= 0``), so
    # every re-slew it makes is at or past the meridian.
    assert all(a >= 0 for a in attempts[1:]) and len(attempts) >= 2, (
        f"premise: every re-slew of the hold was made past the meridian: "
        f"{attempts} s")
    learned = night.engine._mount_flips_early()
    assert learned is None, (
        f"the process learned \"flips early: {learned}\" from attempts made "
        f"past the meridian: A's attempts at {attempts} s, the lead-time one "
        f"blind to the side")


async def test_control_an_attempt_before_transit_still_teaches(
        group_hub, monkeypatch):
    """CONTROL. The same night with the side readable throughout: A's
    lead-time attempt, ten minutes before transit, re-slews and the
    simulator stays on its side, which is what a mount that cannot flip
    early does, and the process learns exactly that (#127).

    MUTANT "nothing teaches": RED (observed):
        AssertionError: A's attempt 600.0 s before transit read the side on
        both sides of its goto and taught nothing: learned None
    """
    plan = _plan()
    night = Night(group_hub, monkeypatch, t0=T0)
    await _run(night, plan)
    attempts = [round(_past_s(plan, t), 1) for t in _attempts(night)]
    assert attempts and 590.0 <= -attempts[0] <= 610.0, (
        f"premise: A's lead-time attempt came at its lead point: "
        f"{attempts} s")
    assert night.said("A: the meridian flip re-slewed but the mount still "
                      "reports pier side west"), (
        f"premise: the lead-time attempt flipped nothing: "
        f"{night.said('A: ')}")
    learned = night.engine._mount_flips_early()
    assert learned is False, (
        f"A's attempt {-attempts[0]} s before transit read the side on both "
        f"sides of its goto and taught nothing: learned {learned}")


async def test_an_attempt_the_gate_saw_coming_before_transit_is_read_at_its_goto(
        group_hub, monkeypatch):
    """A plan lead of 0: A's one attempt is aimed `MERIDIAN_SIDE_MARGIN_S`
    past the meridian (#455). The gate computes that flip point at the
    frame boundary before it, while A is still before its meridian by more
    than the band, and holds; the goto comes past the meridian and flips.
    Whether an attempt is made before transit is read at its goto, so this
    one teaches nothing.

    MUTANT "the countdown read at the gate": RED (observed), the gate's
    view before the hold taken for the goto's:
        AssertionError: A's attempt came 15.0 s past the meridian and the
        process learned "flips early: True" from it; the gate computed its
        flip point 27.6 s before transit
    (It turns the control above RED as well (observed), the retry's
    success past the meridian overwriting the lead-time attempt's lesson:
        AssertionError: A's attempt 600.0 s before transit read the side on
        both sides of its goto and taught nothing: learned True
    .) The tree before the fix keeps this case green: it learned nothing
    from any zero-lead attempt.
    """
    plan = _plan(lead_min=0.0, count=40)
    night = Night(group_hub, monkeypatch, t0=T0)
    await _run(night, plan)
    attempts = [round(_past_s(plan, t), 1) for t in _attempts(night)]
    # The first publish of the hold, in seconds from the night's start: the
    # gate hands over to the hold at the frame boundary it computed at.
    held = [e[0] for e in night.trace
            if e[1] == "state" and e[2].get("detail") == HOLDING]
    assert attempts == [attempts[0]] and attempts[0] >= MERIDIAN_SIDE_MARGIN_S, (
        f"premise: one attempt, past the band: {attempts} s")
    assert night.said("A: meridian flip complete (pier side west -> east)"), (
        f"premise: it flipped: {night.said('A: ')}")
    gate_s = -_past_s(plan, T0 + held[0]) if held else None
    assert gate_s is not None and gate_s > MERIDIAN_SIDE_MARGIN_S, (
        f"premise: the gate computed the flip point while A was more than "
        f"the band before transit: {gate_s} s before it")
    learned = night.engine._mount_flips_early()
    assert learned is None, (
        f"A's attempt came {attempts[0]} s past the meridian and the "
        f"process learned \"flips early: {learned}\" from it; the gate "
        f"computed its flip point {round(gate_s, 1)} s before transit")


async def test_an_attempt_inside_the_band_before_transit_teaches_nothing(
        group_hub, monkeypatch):
    """A plan lead of 0, on a mount that reports its limit ``INSIDE_BAND_S``
    before A's meridian (#505): A's first attempt is aimed at that limit,
    with no band after it, and its goto comes there. That is before
    transit, but inside `MERIDIAN_SIDE_MARGIN_S` of it, where the side a
    goto lands on is the mount's own reckoning of the hour angle (its
    clock, its longitude, the simulator's 7 s of RA of goto error). The
    simulator stays on its side and both reads succeed, and nothing is
    learned: a goto seconds before the crossing says nothing about one ten
    minutes before it. The retry, past the meridian, flips, and teaches
    nothing either.

    MUTANT "learning with no band" (the learning's condition made
    ``before_transit_s > 0``), run by the H4-ENG-C verifier in its private
    copy H4-ENG-C-verify-mut: RED (observed):
        AssertionError: A's attempt came 9.9 s before transit, inside the
        15 s band, and the process learned "flips early: False" from it
        (attempts at [-9.9, 15.0] s)
    The mutant "the countdown read at the gate" turns it RED too, with the
    same line (observed): the gate computed its point further out than the
    band. "learning keyed on lead_s again" and "nothing teaches" keep it
    green (observed): the plan's lead is 0, and neither can learn a thing
    here.
    """
    plan = _plan(lead_min=0.0, count=40)
    night = Night(group_hub, monkeypatch, t0=T0)
    told = _a_mount_that_reports_its_limit(night, plan, INSIDE_BAND_S)
    await _run(night, plan)
    attempts = [round(_past_s(plan, t), 1) for t in _attempts(night)]
    assert told and attempts and abs(attempts[0] + INSIDE_BAND_S) <= 1.5, (
        f"premise: A's first attempt came at the limit the mount reported, "
        f"{INSIDE_BAND_S:.0f} s before transit: attempts at {attempts} s")
    assert night.said("A: the meridian flip re-slewed but the mount still "
                      "reports pier side west"), (
        f"premise: the attempt inside the band read the side on both sides "
        f"of its goto and flipped nothing: {night.said('A: ')}")
    assert night.said("A: meridian flip complete (pier side west -> east)"), (
        f"premise: the retry past the meridian flipped: {night.said('A: ')}")
    learned = night.engine._mount_flips_early()
    assert learned is None, (
        f"A's attempt came {-attempts[0]} s before transit, inside the "
        f"{MERIDIAN_SIDE_MARGIN_S:.0f} s band, and the process learned "
        f"\"flips early: {learned}\" from it (attempts at {attempts} s)")
