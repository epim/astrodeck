# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The scheduler sleeps on the soonest EFFECTIVE wake (#380; spec 5.1
selection and pass boundary item 2, 1.6).

THE DEFECT. `SequenceEngine._schedule_loop` picked the waiter it sleeps on by
comparing two kinds of time. A target the gating calls "waiting" joined
``earliest`` with ``gs["start_ts"]``, when its time window OPENED, which for a
target below its altitude gate with its window already open is in the past.
A group member a group rule holds joined with ``e.wake_ts``, a real future
wake: the end of a deferral wait (#304), a reach recheck, a meridian
crossing. The past opening always sorted first, and the wait branch then
slept until ``max(start_ts, now + eta_s)``, that target's rise, so every
group-rule wake before it was slept through. The deferral wait's own rule
went with it: the wait published was the other target's, so the "long wait"
park-hold ran at once, where a deferral wait is left to the idle clock.

THE FIX. A gating waiter joins ``earliest`` at the time the wait branch would
really wait until, ``max(start_ts, now + eta_s)``: its opening when that is
still ahead, its estimated rise when the opening is past.

THE NIGHT, the issue's shape on the clocked simulator
(tests/_group_harness.py; the real `_run_scheduled`, `_eligibility_now`,
`_close_group_pass` and `_wait_until`): a 1x2 of three L frames a panel, one
frame a visit. Panel 1-2 carries its own ``min_altitude_deg``, its altitude
``RISES`` seconds in, so its window is open from the start and the gating
holds it on its altitude until then. Panel 1-1's first centring misses (a
scripted goto result, 30 arcmin), so pass 1 defers it, takes no exposure,
and the group waits ``DEFER_WAIT_S`` before pass 2.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants were
applied in a private scratch copy of server/ (scratchpad/S5-ENG-SCHED-mut),
never in the shared tree (#254):

* "a gating waiter joins earliest with start_ts": the waiting branch of the
  selection putting ``gs["start_ts"]`` into ``earliest`` again, in place of
  the effective wake. It is the code before the fix.

S7 (#434) refined the altitude gate's rise estimate inside its 600 s step
and re-pinned one line here, "1-2 was shot once it rose", which had read
the old estimate's rounding (the case's own comment has its old failure).
Its mutants were run in a private copy of server/ (scratchpad/S7-SCHED-mut):

* "the gate read 2 deg low": `schedule.gating_status` taking 2 deg off
  ``min_altitude_deg`` before it gates, so 1-2 counts as risen, and is
  visited, about ten minutes before it clears its real gate.
"""
from __future__ import annotations

import time as _time

from _group_harness import (GROUP_NAME, LAT, LON, T0, Night, grid_plan,
                            group_hub, group_store)  # noqa: F401
from astrodeck.sequence import schedule
from astrodeck.sequence.group_rules import DEFER_WAIT_S
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.session import session_store

#: Seconds into the night at whose altitude panel 1-2's gate is set, less the
#: 0.01 deg `_plan` takes off it, so 1-2 truly clears the gate 3.1 s sooner,
#: 1796.9 s in. Since #434 the gating's rise estimate is that rise, to
#: ``schedule._GATE_RISE_TOL_S`` (test_s7_time_to_gate_refined.py), where it
#: was the next 600 s step after it.
RISES = 1800.0
#: A whole local minute 171 s into the night (``T0`` is 51 s short of one),
#: before the deferral wait's end: the opening the control's 1-2 waits for.
OPENS_SOON = T0 + 171.0
LONG_WAIT = "the next target is a long wait away"


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _plan(second: str) -> SequencePlan:
    """The 1x2, with panel 1-2 held by its altitude gate (``"altitude"``) or
    by a clock window opening at ``OPENS_SOON`` (``"clock"``)."""
    plan = grid_plan(rows=1, cols=2, panel_kw={"filters": ("L",), "count": 3})
    p12 = next(t for t in plan.targets if t.name == _name("1-2"))
    if second == "altitude":
        alt = schedule.target_altitude(p12.ra_hours, p12.dec_deg, LAT, LON,
                                       T0 + RISES)
        p12.schedule = Schedule(min_altitude_deg=round(alt - 0.01, 3))
    else:
        p12.schedule = Schedule(
            start_mode="time",
            start_time=_time.strftime("%H:%M", _time.localtime(OPENS_SOON)))
    return plan


def _miss_once(who: str, n: int, result: dict) -> dict:
    """1-1's first centring misses by 30 arcmin; every other goto centres."""
    if who == _name("1-1") and n == 1:
        return dict(result, centered=False, error_arcmin=30.0)
    return result


async def _night(hub, monkeypatch, plan: SequencePlan) -> Night:
    night = Night(hub, monkeypatch, goto=_miss_once)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _published(night: Night) -> list[tuple[float, dict]]:
    """Every published state, with its time from the start of the night."""
    return [(e[0], e[2]) for e in night.trace if e[1] == "state"]


async def test_a_deferral_wait_is_not_slept_through_to_another_panels_rise(
        group_hub, monkeypatch):
    """At 0 s the group's deferral wait ends at ``DEFER_WAIT_S`` and 1-2
    rises at ``RISES``: 1-1's second visit starts at the end of the deferral
    wait, not at 1-2's rise, and no long-wait park-hold is made at 0 s (the
    deferral wait is left to the idle clock, #304). The run publishes the
    deferral wait, not "waiting for M31 1-2".

    MUTANT "a gating waiter joins earliest with start_ts": RED (observed; the
    run slept until 1-2 rose, visited 1-2 at 1800 s and 1-1 after it, the
    issue's 1860 s):
        AssertionError: 1-1's visits at [0.0, 1830.0, 1890.0, 1950.0]: its
        second must start at the end of the 300 s deferral wait
        assert 1830.0 == 300.0

    (Observed again after S7's refinement (#434), in S7's copy: the same
    lines. Asked at 0 s the refined estimate's last probe is 1800.0 s.)

    MUTANT "the gate read 2 deg low": RED (observed; S7):
        AssertionError: 1-2 first visited at 1168.125 s, 2.0 deg under its
        73.158 deg gate: it was shot before it rose
        assert 71.16299245057304 >= 73.158
    """
    plan = _plan("altitude")
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.said("centring failed on 1-1"), (
        f"premise: 1-1's first centring missed: {night.lines[:6]}")
    assert night.said("pass 1 took no exposures and deferred 1 visits"), (
        f"premise: pass 1 deferred and waited: {night.said('pass 1')}")
    visits = [night.rel(t) for t, who in night.gotos if who == _name("1-1")]
    assert len(visits) >= 2, f"premise: 1-1 was visited again: {visits}"
    assert visits[1] == DEFER_WAIT_S, (
        f"1-1's visits at {visits}: its second must start at the end of the "
        f"{DEFER_WAIT_S:.0f} s deferral wait")
    long_at = [night.rel(t) for t, _lv, m in night.lines if LONG_WAIT in m]
    assert 0.0 not in long_at, (
        f"a long-wait park-hold at 0 s, inside the deferral wait: {long_at}")
    at_start = [v.get("detail") for t, v in _published(night) if t == 0.0]
    assert "waiting for M31 1-2" not in at_start, at_start
    assert at_start[-1] == ("M31: every panel was deferred this pass; waiting "
                            "before the next"), at_start
    # 1-2 was shot all the same, once it rose: the fix changes which wait is
    # slept on, never whether the other panel is waited for.
    #
    # DELIBERATE PIN CHANGE (S7, #434). This read ``min(risen) >= RISES``.
    # 1-2 truly clears its gate at 1796.9 s (``RISES``), so that bound held
    # only while the rise estimate came late: the 600 s step woke the run at
    # 2190 s, and the refined one wakes it at 1800.938 s, its last probe at
    # the 5 s tolerance, by where the probes fall. At a 1 s tolerance the
    # estimate is 1797.4 s, and right, and the old pin went red (observed,
    # the variant "tolerance 1 s" in S7's copy):
    #     AssertionError: [1797.422, 1827.422, 1857.422]
    #     assert ([1797.422, 1827.422, 1857.422] and 1797.422 >= 1800.0)
    #      +  where 1797.422 = min([1797.422, 1827.422, 1857.422])
    # So it is held against the rise itself: 1-2 at or above its own gate at
    # its first visit.
    risen = [night.rel(t) for t, who in night.gotos if who == _name("1-2")]
    assert risen, f"premise: 1-2 was visited: {night.gotos}"
    p12 = next(t for t in plan.targets if t.name == _name("1-2"))
    gate = float(p12.schedule.min_altitude_deg)
    first_alt = schedule.target_altitude(p12.ra_hours, p12.dec_deg, LAT, LON,
                                         T0 + min(risen))
    assert first_alt >= gate, (
        f"1-2 first visited at {min(risen)} s, {gate - first_alt:.1f} deg "
        f"under its {gate} deg gate: it was shot before it rose")
    assert night.stored.status == "complete", night.stored.status


async def test_a_waiter_whose_window_opens_later_still_sorts_by_its_opening(
        group_hub, monkeypatch):
    """CONTROL. The same night with 1-2 waiting on the CLOCK instead, its
    window opening at ``OPENS_SOON``, before the deferral wait ends. An
    opening still ahead is the effective wake itself, so it still sorts
    first: at 0 s the run publishes "waiting for M31 1-2" with that opening
    and wakes at it (171 s), where the deferral holds 1-2 too; pass 2 then
    begins at the deferral wait's end, with 1-2 (never visited, so first by
    the group's order) and 1-1 after it. GREEN under the mutant "a gating
    waiter joins earliest with start_ts", as it must be (observed): with the
    opening ahead, ``start_ts`` and the effective wake are one number."""
    night = await _night(group_hub, monkeypatch, _plan("clock"))
    assert night.done, night.lines[-4:]
    published = _published(night)
    waits = [(t, v) for t, v in published
             if v.get("detail") == "waiting for M31 1-2"]
    assert waits and waits[0][0] == 0.0, [
        (t, v.get("detail")) for t, v in published[:8]]
    sched = waits[0][1]["schedule"]
    assert sched["start_ts"] == OPENS_SOON, sched
    assert sched["eta_s"] == round(OPENS_SOON - T0), sched
    woke = sorted({t for t, _v in published if 0.0 < t < DEFER_WAIT_S})
    assert woke and woke[0] == OPENS_SOON - T0, woke
    hops = [(night.rel(t), who) for t, who in night.gotos]
    assert hops[:3] == [(0.0, _name("1-1")), (DEFER_WAIT_S, _name("1-2")),
                        (DEFER_WAIT_S + 30.0, _name("1-1"))], hops[:4]
    assert night.stored.status == "complete", night.stored.status
