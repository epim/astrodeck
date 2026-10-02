# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flip gate and its hold measure the flip point from one zero (#505,
H4-ENG-C; spec 5.7, the S5 and S7 paragraphs on the band, S5 orchestrator
ruling 2).

THE DEFECT. `SequenceEngine._maybe_meridian_flip` swapped in the mount's own
figure when the mount reported a limit nearer than the meridian
(``time_to_meridian_flip``, today only NINA's), and built its frame window
from it. When the flip point fell inside the next frame, it handed over to
`_wait_for_flip_point`, which computed its own countdown from the TRUE
meridian and never saw the mount's figure. So the gate stopped exposing for
a limit the mount had stated, and the hold then waited for the meridian: no
frames and no flip for the whole gap, on a mount that may have stopped at
its limit. And since #455 every zero-lead attempt added
`MERIDIAN_SIDE_MARGIN_S` (15 s) past its zero, the mount's limit included,
where the band belongs only to the crossing itself: at the crossing a
goto's side is the mount's own reckoning of the hour angle, and a limit the
mount reports before the meridian is not the crossing.

THE FIX. `SequenceEngine._flip_point` computes the flip point once: the
mount's substitution, the lead, and the band only when the zero is the
meridian itself. The gate hands it to the hold (`_flip_point_handed`, set
around the call, as the flip-owed hold tells the gate whose attempt it is,
#456), and the hold waits for it.

THE NIGHT is the clocked simulator (tests/_group_harness.py): one target,
A, crossing the fixture site's meridian 15 min in, shot in 30 s frames under
a plan lead of 0, so its first attempt is a zero-lead one. The telescope
double reports a limit ``LIMIT_S`` before A's meridian, as NINA's
``TimeToMeridianFlip`` does: hours to the limit while it is ahead, and
nothing once it has passed. It is computed from A's catalogue RA on the
night's clock, so the limit is exact to the second.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad H4-ENG-C-mut, from a byte
backup), never in the shared tree (#254):

* "hold reads the true meridian": `_wait_for_flip_point` ignoring the
  handed point (``handed = None``), so it computes its own from the
  meridian, `_flip_point(target, lead_s)` with no mount figure.
* "band applied whatever ttf came from": `_flip_point`'s
  ``past_s = self._flip_retry_past_s(target)`` taken out of its ``else``,
  so the band is added whichever zero it chose.
* "no band at all": that ``past_s`` made 0.0 at the meridian too.
"""
from __future__ import annotations

from _group_harness import (LON, T0, Night, group_hub, group_store,  # noqa: F401
                            single)
from astrodeck.sequence import SequencePlan, schedule
from astrodeck.sequence.engine import MERIDIAN_SIDE_MARGIN_S

#: A crosses 15 min into the night.
A_HA = -0.25
#: The mount's own limit, in seconds of hour angle before A's meridian.
LIMIT_S = 240.0
#: How far an attempt may land from where it is aimed, in seconds of hour
#: angle: the hold's last sleep has a 0.2 s floor, and the engine counts the
#: countdown's sidereal seconds as clock seconds over the last frame window
#: (0.27 % of about a minute). A tenth of the band.
SLACK_S = 1.5


def _plan() -> SequencePlan:
    """A alone, 30 s frames, flips on at a plan lead of 0: every attempt is
    a zero-lead one. Frames enough to run well past the crossing."""
    return SequencePlan(
        name="one crossing", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=True, meridian_flip_lead_min=0.0,
        park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False,
        targets=[single("A", count=40, ha_h=A_HA)])


def _past_s(plan: SequencePlan, name: str, t: float) -> float:
    """Seconds of hour angle ``name`` is past its meridian at fake time
    ``t`` (negative: before it)."""
    target = next(x for x in plan.targets if x.name == name)
    return -schedule.hours_to_meridian_flip(target.ra_hours, LON, t) * 3600.0


def _attempts(night: Night, name: str) -> list[float]:
    """The fake time of every goto to ``name`` after its first, the setup:
    each is a flip attempt."""
    return [t for t, who in night.gotos if who == name][1:]


def _a_mount_that_reports_its_limit(night: Night, plan: SequencePlan,
                                    limit_s: float | None) -> list[float]:
    """The telescope double: ``time_to_meridian_flip`` answers the hours to
    a limit ``limit_s`` seconds of hour angle before A's meridian while it
    is ahead, and None once it has passed, or always None with
    ``limit_s`` None (the AM5's and Alpaca's answer). Returns the fake time
    of every answer that was a number."""
    tel = night.hub.devices["telescope"]
    a = next(x for x in plan.targets if x.name == "A")
    told: list[float] = []

    async def time_to_meridian_flip():
        if limit_s is None:
            return None
        h = (schedule.hours_to_meridian_flip(a.ra_hours, LON, night.clock.t)
             - limit_s / 3600.0)
        if h <= 0:
            return None
        told.append(night.clock.t)
        return h

    night.mp.setattr(tel, "time_to_meridian_flip", time_to_meridian_flip)
    return told


async def _night(group_hub, monkeypatch, limit_s: float | None):
    plan = _plan()
    night = Night(group_hub, monkeypatch, t0=T0)
    told = _a_mount_that_reports_its_limit(night, plan, limit_s)
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    return plan, night, told


async def test_a_zero_lead_attempt_goes_at_the_limit_the_mount_reports(
        group_hub, monkeypatch):
    """The mount reports its limit ``LIMIT_S`` before A's meridian. A's
    first attempt, at zero lead, is aimed at that limit, with no band after
    it, and its goto comes there: the gate stopped exposing for the limit
    and the hold waited for the same limit. (On the simulator, which picks
    its side from the hour angle, that attempt flips nothing, and the
    retry, with the limit behind it, waits out the band past the meridian
    and flips; the case grades the first attempt.)

    RED ON THE TREE BEFORE THE FIX (observed), which is both defects:
        AssertionError: A's first attempt came 15.0 s past the meridian; the
        mount reported its limit 240 s before it, where the gate stopped
        exposing (attempts at [15.0] s)

    MUTANT "hold reads the true meridian": RED (observed), the attempt held
    to the band past the meridian, the whole gap after the limit the gate
    stopped for:
        AssertionError: A's first attempt came 15.0 s past the meridian; the
        mount reported its limit 240 s before it, where the gate stopped
        exposing (attempts at [15.0] s)
    MUTANT "band applied whatever ttf came from": RED (observed), the band
    added past a limit the mount stated:
        AssertionError: A's first attempt came -224.9 s past the meridian;
        the mount reported its limit 240 s before it, where the gate
        stopped exposing (attempts at [-224.9, 15.0] s)
    """
    plan, night, told = await _night(group_hub, monkeypatch, LIMIT_S)
    attempts = [round(_past_s(plan, "A", t), 1) for t in _attempts(night, "A")]
    assert attempts, f"premise: A made a flip attempt: {night.gotos}"
    assert told and min(told) < night.gotos[1][0], (
        "premise: the double reported its limit before A's first attempt")
    assert abs(attempts[0] + LIMIT_S) <= SLACK_S, (
        f"A's first attempt came {attempts[0]} s past the meridian; the "
        f"mount reported its limit {LIMIT_S:.0f} s before it, where the "
        f"gate stopped exposing (attempts at {attempts} s)")
    assert night.said("A: meridian flip complete (pier side west -> east)"), (
        f"premise: A flipped in the end: {night.said('A: ')}")


async def test_control_with_no_reported_limit_the_band_still_applies(
        group_hub, monkeypatch):
    """CONTROL. The same night on a mount that reports no limit, as the AM5
    and Alpaca do: the zero is the meridian itself, so A's first attempt
    waits out `MERIDIAN_SIDE_MARGIN_S` past it (#366, #455), lands on the
    far side and flips at once, with no retry.

    Both mutants above keep this green (observed), since the zero here is
    the meridian and the hold's own point is the gate's. The mutant "no
    band at all" (`_flip_point` answering no band at the meridian either)
    turns it RED (observed), the attempts at the crossing flipping nothing
    until the flip-owed hold's re-slew past it:
        AssertionError: A's first attempt, at zero lead with no limit
        reported, came 0.0 s past the meridian, inside the 15 s band
        (attempts at [0.0, 0.0, 0.0, 30.1] s)
    """
    plan, night, told = await _night(group_hub, monkeypatch, None)
    assert told == [], "premise: the double reported nothing"
    attempts = [round(_past_s(plan, "A", t), 1) for t in _attempts(night, "A")]
    assert attempts, f"premise: A made a flip attempt: {night.gotos}"
    assert (MERIDIAN_SIDE_MARGIN_S - 0.05 <= attempts[0]
            <= MERIDIAN_SIDE_MARGIN_S + SLACK_S), (
        f"A's first attempt, at zero lead with no limit reported, came "
        f"{attempts[0]} s past the meridian, inside the "
        f"{MERIDIAN_SIDE_MARGIN_S:.0f} s band (attempts at {attempts} s)")
    assert len(attempts) == 1, f"more than one attempt: {attempts} s"
    assert night.said("A: meridian flip complete (pier side west -> east)"), (
        night.said("A: "))
