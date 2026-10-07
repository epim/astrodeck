# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flip gate's one retry waits past the meridian, and a retry that flips
nothing is never logged as a completed flip (#366, S5 orchestrator ruling 2;
#367).

#366: the lead-time attempt on a mount that picks its pier side from the
hour angle changes nothing, as it cannot (#127), and the target gets ONE
retry with its lead dropped. That retry fired AT the crossing, where a GoTo
leaves the side to the mount's own reckoning of the hour angle: the
simulator's unsynced goto lands 7 s of RA east, still on the pre-flip side,
so the retry flipped nothing, spent the latch, and was logged at info as
"meridian flip complete (pier side west -> west)". The flip-owed invariant
then held the frame, and its re-slew 30 s later made the flip. The retry now
waits `MERIDIAN_SIDE_MARGIN_S` past the crossing, the band the group rule
already keeps, and a retry that flips nothing says so at warning, with what
the flip-owed invariant will do about the next frame.

#367: the lead-time attempt's warning named the lead it was dropping as
"0 min", because it read the lead after the attempt had zeroed it.

The nights here are the golden flow plan from the harness's ``T0``, which
crosses NGC 7331's meridian 180 min into its 195 (tests/_group_harness.py).

Each case names the mutants it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/, never in the shared tree (#254).
"""
from __future__ import annotations

import pytest

from _group_harness import (LON, T0, Night, close_night_hub,  # noqa: F401
                            group_store, night_hub)
from _simhub import sim_hub  # noqa: F401
from test_group_rotation import _golden_as_recorded

from astrodeck.devices.base import PierSide
from astrodeck.sequence import SequenceEngine, SequencePlan, schedule
from astrodeck.sequence.engine import MERIDIAN_SIDE_MARGIN_S
from astrodeck.sequence.models import ExposureStep, Target

#: The golden flow plan's target and its RA.
GOLDEN = "NGC 7331"


def _golden_ra() -> float:
    (target,) = _golden_as_recorded().targets
    return target.ra_hours


def _past_s(t: float) -> float:
    """Seconds of hour angle NGC 7331 is past its meridian at fake time
    ``t`` at the fixture longitude (negative: before it)."""
    return -schedule.hours_to_meridian_flip(_golden_ra(), LON, t) * 3600.0


async def _crossing_night(monkeypatch, *, never_flips: bool = False) -> Night:
    """The golden flow plan's night from ``T0``, run to its end on a fresh
    clocked hub. ``never_flips`` makes the mount report the west side
    whatever its gotos do: a mount no re-slew ever flips."""
    hub, popped = await night_hub(monkeypatch)
    try:
        night = Night(hub, monkeypatch, t0=T0)
        if never_flips:
            async def west():
                return PierSide.WEST

            monkeypatch.setattr(hub.devices["telescope"], "pier_side", west)
        try:
            night.done = await night.run(_golden_as_recorded(), wall_s=120.0)
        finally:
            await night.close()
    finally:
        await close_night_hub(hub, popped)
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    return night


def _flip_gotos(night: Night) -> list[float]:
    """The fake time of every goto after the night's first, the setup: on
    this one-target night each is a flip attempt."""
    return [t for t, who in night.gotos[1:] if who == GOLDEN]


# ------------------------------------------------ the crossing night, #366

async def test_the_crossing_night_flips_at_its_first_retry(group_store,
                                                           monkeypatch):
    """The lead-time attempt ten minutes before transit flips nothing, as it
    cannot on a mount that takes its side from the hour angle, and names
    the 10 min lead it drops (#367). The one retry then waits until
    `MERIDIAN_SIDE_MARGIN_S` past the crossing, lands east, and is the
    flip: three gotos in all, one "meridian flip complete (pier side west
    -> east)", and no flip-owed hold. The control is that real flip's own
    line, which still says complete.

    MUTANT "retry at the crossing" (`_flip_retry_past_s` answering 0.0, so
    the retry fires at the crossing itself): RED (observed), the retry at
    10770.644 s, the flip-owed hold's re-slew at the same instant, and the
    flip 30 s later, which is #366's night:
        AssertionError: the retry fired 0.0 s past the meridian, inside the
        15 s band where a goto leaves the side to the mount: [10172.28,
        10770.644, 10770.644, 10800.644]
        assert 0.00920791411118671 >= 15.0
    MUTANT "the hold waits for the crossing" (`_wait_for_flip_point`
    leaving the margin out, the gate keeping it): RED, the same failure
    verbatim (observed).
    MUTANT "lead read after the add" (the warning reading
    ``self._flip_lead_s()`` after the attempt added the target to
    `_flip_no_op` and learned the mount cannot flip early, the code before
    #367): RED (observed):
        AssertionError: the lead-time warning does not name the 10 min lead
        it dropped: ['NGC 7331: the meridian flip re-slewed but the mount
        still reports pier side west — nothing flipped. Holding the
        flip armed and dropping the 0 min lead, so the retry waits until 15
        s past the meridian itself']
    """
    night = await _crossing_night(monkeypatch)
    gotos = _flip_gotos(night)
    assert len(gotos) >= 2, f"premise: the night makes a flip attempt: {gotos}"
    lead_attempt, retry = gotos[0], gotos[1]
    assert 590.0 <= -_past_s(lead_attempt) <= 610.0, (
        f"premise: the first attempt is the lead-time one, 10 min before "
        f"transit: {-_past_s(lead_attempt):.1f} s")
    assert _past_s(retry) >= MERIDIAN_SIDE_MARGIN_S, (
        f"the retry fired {_past_s(retry):.1f} s past the meridian, inside "
        f"the {MERIDIAN_SIDE_MARGIN_S:.0f} s band where a goto leaves the "
        f"side to the mount: {[round(night.rel(t), 3) for t in gotos]}")
    assert _past_s(retry) < MERIDIAN_SIDE_MARGIN_S + 5.0, (
        f"the retry waited {_past_s(retry):.1f} s past the meridian, well "
        f"beyond the band")
    assert len(gotos) == 2, (
        f"more flip attempts than the lead-time one and its retry: "
        f"{[round(night.rel(t), 3) for t in gotos]}")
    dropped = night.said("nothing flipped. Holding the flip armed")
    assert len(dropped) == 1 and "dropping the 10 min lead" in dropped[0], (
        f"the lead-time warning does not name the 10 min lead it dropped: "
        f"{dropped}")
    complete = night.said("meridian flip complete (pier side")
    assert complete == [f"{GOLDEN}: meridian flip complete (pier side west "
                        f"-> east)"], complete
    assert not night.said("a meridian flip is required"), (
        night.said("a meridian flip is required"))
    assert not night.said("nothing flipped this time either")


async def test_a_retry_that_flips_nothing_is_not_logged_complete(
        group_store, monkeypatch):
    """On a mount no re-slew flips, the retry past the meridian flips
    nothing either. It says so at warning, with what the flip-owed
    invariant will do, and the invariant then does it: the next line is its
    refusal to expose on the west side. Nothing in the night says "meridian
    flip complete", neither the engine's line nor the hub's.

    MUTANT "a no-op retry logged complete" (the retry's own branch removed,
    so a second no-op falls into the completed-flip line, the code before
    #366): RED (observed):
        AssertionError: a flip that moved nothing was logged complete:
        ['NGC 7331: meridian flip complete (pier side west -> west)']
    MUTANT "the hub says complete after a no-op" (`Hub.meridian_flip`
    ending on "meridian flip complete" whether or not the side changed, the
    code before #366): RED (observed), one line for each of the 42 re-slews
    that moved nothing, the lead-time attempt, the retry and the flip-owed
    hold's 40:
        AssertionError: a flip that moved nothing was logged complete:
        ['meridian flip complete', 'meridian flip complete', 'meridian flip
        complete', ...
        Left contains 42 more items, first extra item: 'meridian flip
        complete'
    """
    night = await _crossing_night(monkeypatch, never_flips=True)
    assert len(_flip_gotos(night)) >= 2, (
        "premise: the night made the lead-time attempt and its retry")
    complete = night.said("meridian flip complete")
    assert complete == [], (
        f"a flip that moved nothing was logged complete: {complete}")
    either = night.said("nothing flipped this time either")
    assert len(either) == 1, either
    (t_retry, level, line) = next(entry for entry in night.lines
                                  if entry[2] == either[0])
    assert level == "warning", (level, line)
    assert _past_s(t_retry) >= MERIDIAN_SIDE_MARGIN_S, (
        f"premise: the retry waited out the band: {_past_s(t_retry):.1f} s")
    assert ("The meridian flip safety check now holds the frame: nothing is "
            "exposed past the meridian while the mount stays on the west "
            "side, for up to 20 min") in line, line
    after = [m for t, _lvl, m in night.lines if t >= t_retry]
    owed = [m for m in after if "a meridian flip is required" in m]
    assert owed and "still on the west side -- refusing to expose" in owed[0], (
        f"the line said the invariant holds the frame, and it did not: "
        f"{after[:6]}")


@pytest.mark.parametrize("case", ["flipped", "nothing", "unreadable"])
async def test_the_hub_says_complete_only_for_a_flip(sim_hub, monkeypatch,
                                                     bus_lines, case):
    """`Hub.meridian_flip`'s closing line says "complete" for a re-slew
    that changed the side, and "nothing flipped" for one that did not. A
    side that could not be read keeps "complete", as it keeps the
    recalibration: unreadable is not evidence that nothing moved, and the
    engine's own check reads it the same way.

    MUTANT "the hub reads unreadable as nothing flipped" (the closing line
    saying "complete" only when both sides were readable): RED on
    unreadable, the other two green (observed, S5-ENG-FLIP verifier):
        AssertionError: unreadable: ['meridian flip attempt finished:
        nothing flipped']
    MUTANT "the hub says complete after a no-op" (as in the case above):
    RED on nothing, the other two green (observed, S5-ENG-FLIP verifier):
        AssertionError: nothing: ['meridian flip complete']
    """
    sides = {"flipped": ["west", "east"], "nothing": ["west", "west"],
             "unreadable": [None, None]}[case]
    reads = iter(sides)

    async def side_now():
        return next(reads)

    async def goto(ra_h, dec_d, **kw):
        return {"centered": True, "error_arcmin": 0.2}

    monkeypatch.setattr(sim_hub, "pier_side_now", side_now)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    result = await sim_hub.meridian_flip(1.0, 20.0)
    closing = [m for _lvl, m, _s in bus_lines
               if m.startswith("meridian flip complete")
               or m.startswith("meridian flip attempt finished")]
    want = ("meridian flip attempt finished: nothing flipped"
            if case == "nothing" else "meridian flip complete")
    assert closing == [want], f"{case}: {closing}"
    assert result["flipped"] is (case != "nothing"), result


# ------------------------------------------------- the gate, unit by unit

async def _noop_gate(context="", target=None):
    return None


def _engine(sim_hub, monkeypatch, *, ttf_s: float, flips: bool = False):
    """An engine armed for a target ``ttf_s`` seconds of hour angle before
    its transit (negative: past it), with a plan lead of 10 min, on the
    real `_maybe_meridian_flip`. The goto is the one fake: it is where a
    mount decides its side, so ``flips`` says whether this one changes it.
    `_wait_for_flip_point` records the lead it was asked to hold for and
    returns at once."""
    tel = sim_hub.devices["telescope"]
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + ttf_s / 3600.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False,
                          meridian_flip_lead_min=10.0)
    e._cfg = None
    e._flip_armed = True
    e._safety_gate = _noop_gate
    st = {"side": "west", "flips": flips, "gotos": [], "holds": [],
          "order": []}

    async def pier():
        return {"east": PierSide.EAST,
                "west": PierSide.WEST}.get(st["side"], PierSide.UNKNOWN)

    async def no_countdown():
        return None

    async def goto(ra_h, dec_d, **kw):
        st["gotos"].append((ra_h, dec_d))
        st["order"].append("goto")
        if st["flips"]:
            st["side"] = "east" if st["side"] == "west" else "west"
        return {"centered": True, "error_arcmin": 0.2}

    async def hold(target, lead_s=0.0):
        st["holds"].append(lead_s)
        st["order"].append("hold")

    async def autofocus(label, **kw):
        # A flip that moved the tube owes a post-flip sweep; this file is
        # not about focus, so the sweep is recorded rather than run.
        st["order"].append(label)
        return True

    monkeypatch.setattr(tel, "pier_side", pier)
    monkeypatch.setattr(tel, "time_to_meridian_flip", no_countdown)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    e._wait_for_flip_point = hold
    e._autofocus = autofocus
    return e, t, st


async def test_a_retry_due_inside_the_band_holds_before_its_goto(
        sim_hub, monkeypatch):
    """A frame boundary 5 s past the crossing, for a target owed its one
    retry: the gate holds before the goto, since the flip point is
    `MERIDIAN_SIDE_MARGIN_S` past the crossing and not the crossing. The
    wait itself is `_wait_for_flip_point`'s, which reads the same margin
    (the night above holds it for real). Control: an attempt that is not a
    retry, the same 5 s past, is taken at once with no hold, as before. It
    carries the plan's 10 min lead, the mount having taught nothing, which
    is why: since #455 (S7-ENG-FLIP) a first attempt at ZERO lead holds for
    the band as the retry does (test_s7_flip_zero_lead_band.py), and #455
    cited this control as the evidence it did not.

    MUTANT "the gate reads the crossing" (the window in
    `_maybe_meridian_flip` computed without the margin, `_wait_for_flip_point`
    left reading it): RED on the retry, the control green (observed):
        AssertionError: the retry due 5 s past the crossing slewed without
        holding for the band: ['goto', 'post-flip autofocus']
        assert ['goto', 'pos...ip autofocus'] == ['hold', 'goto']
    The crossing night above stays green under this mutant (observed): its
    frame boundaries meet the retry's window before the crossing, where the
    gate hands over to the wait, and the wait keeps the margin. This case
    is the frame boundary that falls inside the band.
    """
    e, t, st = _engine(sim_hub, monkeypatch, ttf_s=-5.0, flips=True)
    e._flip_no_op.add(t.id)
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)
    assert st["order"][:2] == ["hold", "goto"], (
        f"the retry due 5 s past the crossing slewed without holding for "
        f"the band: {st['order']}")

    e2, t2, st2 = _engine(sim_hub, monkeypatch, ttf_s=-5.0, flips=True)
    await e2._maybe_meridian_flip(t2, next_exposure_s=60.0)
    assert st2["order"][:1] == ["goto"], (
        f"control: an attempt that is not a retry held: {st2['order']}")


@pytest.mark.parametrize("case", ["plan-lead", "no-lead"])
async def test_the_no_op_warning_names_the_lead_it_dropped(
        sim_hub, monkeypatch, bus_lines, case):
    """The first attempt that changes nothing names the lead it drops, as
    the attempt used it: the plan's 10 min for an attempt 9 min before
    transit (#367). An attempt with no lead to drop, on a mount already
    known not to flip early (#127) and so made 30 s past the crossing, says
    the retry waits for the band and names no lead at all.

    MUTANT "lead read after the add" (the line reading
    ``self._flip_lead_s()`` after the target joined `_flip_no_op` and the
    mount was learned not to flip early, the code before #367): RED on
    plan-lead (observed):
        AssertionError: plan-lead: 'T: the meridian flip re-slewed but the
        mount still reports pier side west — nothing flipped. Holding
        the flip armed and dropping the 0 min lead, so the retry waits
        until 15 s past the meridian itself'
    MUTANT "a zero lead named" (the "dropping the N min lead" words used
    whatever the lead was): RED on no-lead, plan-lead green (observed):
        AssertionError: no-lead: 'T: the meridian flip re-slewed but the
        mount still reports pier side west — nothing flipped. Holding
        the flip armed and dropping the 0 min lead, so the retry waits
        until 15 s past the meridian itself'
    """
    if case == "plan-lead":
        e, t, st = _engine(sim_hub, monkeypatch, ttf_s=540.0)
        want = ("Holding the flip armed and dropping the 10 min lead, so the "
                "retry waits until 15 s past the meridian itself")
    else:
        e, t, st = _engine(sim_hub, monkeypatch, ttf_s=-30.0)
        e._flips_early_by_mount[e._mount_name()] = False
        assert e._flip_lead_s(t) == 0.0, "premise: no lead to drop"
        want = ("Holding the flip armed and the retry waits until 15 s past "
                "the meridian itself")
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)
    assert st["gotos"], "premise: the attempt re-slewed"
    (line,) = [m for lvl, m, _s in bus_lines
               if lvl == "warning" and "nothing flipped." in m]
    assert line.endswith(want), f"{case}: {line!r}"
    assert e._flip_armed is True and t.id in e._flip_no_op, (
        "premise: the one retry is owed")


@pytest.mark.parametrize("case", ["holds", "hold-off", "no-record",
                                  "other-side", "unreadable"])
async def test_the_retry_line_says_what_the_invariant_will_do(
        sim_hub, monkeypatch, case):
    """What the no-op retry's warning says about the next frame is what
    `_enforce_flip_owed` will do with it: it holds the frame only while the
    hold is on, only on a side it can read, and only against a side it saw
    before the meridian.

    MUTANT "the words ignore the hold setting" (the ``hold_min <= 0``
    branch removed): RED on hold-off (observed):
        AssertionError: hold-off: 'The flip-owed invariant now holds the
        frame: nothing is exposed past the meridian while the mount stays
        on the west side, for up to 0 min'
    MUTANT "the words ignore the record" (the ``pre is None`` branch
    removed): RED on no-record (observed):
        AssertionError: no-record: 'The mount is not on the None side it
        was seen on before the meridian, so the flip-owed invariant lets
        the frame through'
    MUTANT "the words ignore an unreadable side" (the unreadable-side
    branch removed): RED on unreadable (observed):
        AssertionError: unreadable: 'The mount is not on the west side it
        was seen on before the meridian, so the flip-owed invariant lets
        the frame through'
    """
    e = SequenceEngine(sim_hub)
    e._cfg = None
    hold = 0.0 if case == "hold-off" else 20.0
    monkeypatch.setattr(e, "_flip_owed_hold_min", lambda: hold)
    if case != "no-record":
        e._pre_flip_side["k"] = "east" if case == "other-side" else "west"
    words = e._flip_owed_words("k", None if case == "unreadable" else "west")
    want = {
        "holds": ("The meridian flip safety check now holds the frame: nothing is "
                  "exposed past the meridian while the mount stays on the "
                  "west side, for up to 20 min"),
        "hold-off": ("The meridian flip hold is off (safety.flip_owed_hold_min "
                     "is 0), so nothing stops the next frame being exposed "
                     "on this side past the meridian"),
        "no-record": ("No pier side was seen for this target before the "
                      "meridian, so the meridian flip safety check has nothing to "
                      "compare against and will not hold the frame"),
        "other-side": ("The mount is not on the east side it was seen on "
                       "before the meridian, so the meridian flip safety check "
                       "lets the frame through"),
        "unreadable": ("The mount's side cannot be read, so the meridian flip safety check cannot hold the frame"),
    }[case]
    assert words == want, f"{case}: {words!r}"
