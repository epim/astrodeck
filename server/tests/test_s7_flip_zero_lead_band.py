"""A flip attempt made at zero lead waits `MERIDIAN_SIDE_MARGIN_S` past the
crossing, whether or not it is a retry (#455, S7-ENG-FLIP; the band is the
retry's, #366, S5 orchestrator ruling 2, spec 5.7, Revision 9 row 16, owner
list item 41).

THE DEFECT. #366 made the flip gate's one retry, for a target in
`_flip_no_op`, wait 15 s past the crossing (`_flip_retry_past_s`), because
a GoTo issued AT the crossing leaves the pier side to the mount's own
reckoning of the hour angle: the simulator's unsynced goto lands a few
seconds of RA east and stays on the pre-flip side. Every other attempt
whose lead is zero still fired at the crossing itself. The common one is
every target after the first on a mount already shown not to flip early
(#127, `_mount_flips_early() is False`): `_flip_lead_s` answers 0 for each,
so each one's first attempt went at the crossing, flipped nothing, spent
the one retry and waited the band to flip: a wasted re-slew, guider stop
and restart per crossing.

THE FIX. `_flip_retry_past_s` keeps its name (Revision 9 row 16 and owner
list item 41 cite it) and answers `MERIDIAN_SIDE_MARGIN_S` for every
attempt whose effective lead, `_flip_lead_s(target)`, is zero: the retry,
a mount known not to flip early, and a plan whose lead is 0.

THE NIGHT is two targets on the clocked simulator (tests/_group_harness.py),
shot one after the other, each crossing the fixture site's meridian while
it is shot. A crosses at 900 s: its lead-time attempt ten minutes before
transit flips nothing on a mount that takes its side from the hour angle,
which teaches the process that this mount cannot flip early (#127), and its
retry past the crossing flips it. B crosses at 2700 s, and is in no
`_flip_no_op`: its first attempt is made with the learned zero lead.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). The mutant was applied in a
private scratch copy of server/ (scratchpad S7-ENG-FLIP-mut, from a byte
backup), never in the shared tree (#254):

* "band only for a retry": `_flip_retry_past_s` answering the margin only
  for a target in `_flip_no_op`, as #366 built it.
* "the band on every attempt": `_flip_retry_past_s` answering the margin
  for every attempt, a lead-time one included, which the control catches.
"""
from __future__ import annotations

from _group_harness import (LON, T0, Night, group_hub, group_store,  # noqa: F401
                            single)
from astrodeck.sequence import SequencePlan, schedule
from astrodeck.sequence.engine import MERIDIAN_SIDE_MARGIN_S

#: A crosses 15 min into the night, B 45 min in.
A_HA, B_HA = -0.25, -0.75


def _plan() -> SequencePlan:
    """A then B, 30 s frames, flips on at the plan's default 10 min lead.
    A has frames enough to run past its crossing and retry, and B past its
    own."""
    return SequencePlan(
        name="two crossings", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=True, park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False,
        targets=[single("A", count=40, ha_h=A_HA),
                 single("B", count=60, ha_h=B_HA)])


def _past_s(plan: SequencePlan, name: str, t: float) -> float:
    """Seconds of hour angle ``name`` is past its meridian at fake time
    ``t`` (negative: before it)."""
    target = next(x for x in plan.targets if x.name == name)
    return -schedule.hours_to_meridian_flip(target.ra_hours, LON, t) * 3600.0


def _attempts(night: Night, name: str) -> list[float]:
    """The fake time of every goto to ``name`` after its first, the setup:
    each is a flip attempt."""
    return [t for t, who in night.gotos if who == name][1:]


async def test_a_zero_lead_first_attempt_waits_out_the_band(group_hub,
                                                            monkeypatch):
    """The trait "cannot flip early" is learned on A's crossing, and B, in
    no `_flip_no_op`, gets its first attempt at the learned zero lead. It
    holds before its goto until `MERIDIAN_SIDE_MARGIN_S` past the crossing,
    lands on the far side and flips at the first attempt: one goto, one
    "meridian flip complete (pier side west -> east)", no "nothing
    flipped".

    CONTROL, the same night: A's first attempt, made with a lead of 10 min
    (the trait not yet learned), fires at its lead point, ten minutes
    before transit, as it always has; its retry waits the band.

    MUTANT "band only for a retry": RED (observed), B's first attempt at its
    crossing, flipping nothing, and the retry the band then made, #455's
    wasted re-slew:
        AssertionError: B's first attempt, at zero lead, fired 0.1 s past
        the meridian, inside the 15 s band: B's attempts at [0.1, 15.0] s
        assert 0.1 >= 15.0
    test_flip_retry_margin.py stays green under it (observed): its nights
    have one target, whose first attempt is made with the plan's lead.
    MUTANT "the band on every attempt" (`_flip_retry_past_s` answering the
    margin whatever the lead): RED on the control (observed), A's
    lead-time attempt pushed 15 s later:
        AssertionError: control: A's first attempt, with a 10 min lead,
        fired 585.0 s before transit, not at its lead point
    """
    plan = _plan()
    night = Night(group_hub, monkeypatch, t0=T0)
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    assert [n for n in ("A", "B") if not _attempts(night, n)] == [], (
        f"premise: both targets make a flip attempt: {night.gotos}")

    # CONTROL: A's lead-time attempt, at its lead point.
    a_first = _attempts(night, "A")[0]
    assert 590.0 <= -_past_s(plan, "A", a_first) <= 610.0, (
        f"control: A's first attempt, with a 10 min lead, fired "
        f"{-_past_s(plan, 'A', a_first):.1f} s before transit, not at its "
        f"lead point")
    assert night.engine._mount_flips_early() is False, (
        "premise: A's lead-time attempt taught the mount cannot flip early")
    assert night.said("A: meridian flip complete (pier side west -> east)"), (
        night.said("A: "))

    b = _attempts(night, "B")
    b_past = [round(_past_s(plan, "B", t), 1) for t in b]
    assert b_past[0] >= MERIDIAN_SIDE_MARGIN_S, (
        f"B's first attempt, at zero lead, fired {b_past[0]} s past the "
        f"meridian, inside the {MERIDIAN_SIDE_MARGIN_S:.0f} s band: B's "
        f"attempts at {b_past} s")
    assert b_past[0] < MERIDIAN_SIDE_MARGIN_S + 5.0, (
        f"B's first attempt waited {b_past[0]} s past the meridian, well "
        f"beyond the band")
    assert len(b) == 1, f"more than one flip attempt for B: {b_past} s"
    assert night.said("B: meridian flip complete (pier side west -> east)"), (
        night.said("B: "))
    assert not night.said("B: the meridian flip re-slewed"), (
        night.said("B: the meridian flip re-slewed"))
