"""The altitude gate's rise estimate is the rise, not the next 600 s step
(#434; spec 5.1, the soonest waiter, and 5.10, the published ``eta_s``).

THE DEFECT. `schedule._time_to_gate` found when a target below its altitude
gate reaches it by scanning forward in ``_PEAK_STEP_S`` (600 s) steps from
the moment it was asked, and answered the first step at or above the gate:
the rise rounded UP to the next 600 s, up to 600 s late. The scheduler's
wait branch sleeps until ``max(start_ts, now + eta_s)`` in one
`_wait_until`, which ticks the safety gate and the idle clock but does not
ask the gating again before its deadline, so the overshoot was slept in
full, and the published ``schedule.eta_s`` overstated the wait by as much.

THE FIX. `_time_to_gate` bisects the step the crossing falls in until the
bracket is ``_GATE_RISE_TOL_S`` wide and answers its upper end: the first
instant it saw the target at or above the gate, so the answer is at most
one tolerance after the rise and a wake at it finds the gate open.

THE NIGHT is #434's, and test_scheduler_waiter_effective_wake.py's (its
``_plan("altitude")`` and ``_night``, imported so the two files cannot
drift): a 1x2 at the harness's fixture site whose panel 1-2 has its
``min_altitude_deg`` set to its own altitude 1800 s into the night, less
0.01 deg, so it truly clears its gate 1796.9 s in. 1-1's first centring
misses, pass 1 defers it and waits 300 s, 1-1 is shot at 300, 330 and
360 s, and at 390 s the run begins to wait for 1-2. Asked then, the 600 s
scan read 1800 s (390 + 3 x 600 = 2190), and 1-2 was first visited at
2190 s: 390 s of sky lost.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants
were applied in a private scratch copy of server/
(scratchpad/S7-SCHED-mut, under the session's scratch root), never in the
shared tree (#254):

* "600 s step restored": `_time_to_gate`'s bisection taken out, so it
  answers the first 600 s step at or above the gate, as it did before the
  fix. ``now`` is still asked first, so the two controls stay GREEN under
  it, as they must.
* "now not asked first": the ``alt(now) >= gate`` answer of 0 taken out.
* "a scan with no crossing answers its last step": after a scan that found
  no step at or above the gate, the last step is answered in place of
  ``None``.

Two VARIANTS, not defects, were run the same way to show what the tolerance
decides: "tolerance 1 s" and "tolerance 10 s", ``_GATE_RISE_TOL_S`` set to
each. The cases that quote them say what each did.
"""
from __future__ import annotations

import re

import astrodeck.sequence.engine as engine_mod
from _group_harness import LAT, LON, T0, group_hub, group_store  # noqa: F401
from astrodeck.sequence import schedule
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.resume_arm import ResumeArm
from test_scheduler_waiter_effective_wake import (RISES, _name, _night,
                                                  _plan, _published)

TOL = schedule._GATE_RISE_TOL_S
#: One engine tick: the scheduler's wait re-reads the gating at this cadence
#: when it knows nothing better (`SequenceEngine._wait_until`).
TICK = engine_mod.SCHEDULE_WAIT_STEP_S
#: When #434's run began to wait for 1-2, seconds into the night.
WAIT_BEGINS = 390.0


def _panel_1_2() -> tuple[Target, float]:
    """#434's panel 1-2, and its gate."""
    p12 = next(t for t in _plan("altitude").targets if t.name == _name("1-2"))
    return p12, float(p12.schedule.min_altitude_deg)


def _alt(target: Target, t: float) -> float:
    return schedule.target_altitude(target.ra_hours, target.dec_deg, LAT, LON,
                                    t)


def _rise(target: Target, gate: float, after: float) -> float:
    """The instant ``target`` first reaches ``gate`` after ``after``, found
    here and not by the code under test: a 10 s scan, then a bisection of
    its bracket to a millisecond."""
    lo = after
    while _alt(target, lo + 10.0) < gate:
        lo += 10.0
        assert lo < after + 86400.0, "premise: the target reaches its gate"
    hi = lo + 10.0
    while hi - lo > 1e-3:
        mid = (lo + hi) / 2.0
        if _alt(target, mid) >= gate:
            hi = mid
        else:
            lo = mid
    return hi


def test_the_estimate_is_the_rise_wherever_it_is_asked_on_the_grid():
    """Asked every 23 s from the night's start to just before 1-2's rise,
    the estimate lands at or after the rise and no more than
    ``_GATE_RISE_TOL_S`` after it, and the target is at or above its gate at
    the instant it names, so a wake there finds the gate open. The 600 s
    scan's overshoot depended on where the question fell on its grid, so
    one moment of asking is not enough to show it is gone.

    MUTANT "600 s step restored": RED (observed; each moment of asking but
    0 s and 598 s, whose 600 s steps fell 3.1 s and 1.1 s after the rise,
    answered a whole number of steps, up to 600 s late):
        AssertionError: 76 of 78 moments of asking missed [rise, rise +
        5.0 s]: [(23, 1773.934, 1800.0), (46, 1750.934, 1800.0), (69,
        1727.934, 1800.0), (92, 1704.934, 1800.0), (115, 1681.934, 1800.0)]
        assert 76 == 0
         +  where 76 = len([(23, 1773.934, 1800.0), (46, 1750.934,
        1800.0), (69, 1727.934, 1800.0), (92, 1704.934, 1800.0), (115,
        1681.934, 1800.0), (138, 1658.934, 1800.0), ...])
    """
    assert TOL <= 10.0, f"premise: the named tolerance is 10 s or less: {TOL}"
    p12, gate = _panel_1_2()
    rise = _rise(p12, gate, T0)
    assert abs((rise - T0) - RISES) < 5.0, (
        f"premise: 1-2 rises near {RISES:.0f} s: {rise - T0:.3f}")
    missed = []
    asks = range(0, int(rise - T0) - 5, 23)
    for ask in asks:
        now = T0 + ask
        eta = schedule._time_to_gate(p12, LAT, LON, gate, now, None)
        true = rise - now
        if eta is None or not (true - 1e-6 <= eta <= true + TOL + 1e-6):
            missed.append((ask, round(true, 3), eta))
        elif _alt(p12, now + eta) < gate:
            missed.append((ask, round(true, 3), eta, "below the gate"))
    assert len(missed) == 0, (
        f"{len(missed)} of {len(asks)} moments of asking missed [rise, rise "
        f"+ {TOL} s]: {missed[:5]}")


def test_a_target_already_at_its_gate_answers_0():
    """CONTROL. A target at or above its gate now has no wait: 0, from
    ``now`` itself. GREEN under "600 s step restored" (observed), which
    keeps the question asked of ``now``.

    MUTANT "now not asked first": RED (observed; the first step's bracket
    then has no low end below the gate, and the bisection walked down it to
    one tolerance past ``now``):
        AssertionError: 1-2 is 0.2 deg above its gate
        assert 4.6875 == 0.0
    """
    p12, gate = _panel_1_2()
    now = _rise(p12, gate, T0) + 60.0
    above = _alt(p12, now) - gate
    assert above > 0.0, f"premise: 1-2 is above its gate: {above}"
    eta = schedule._time_to_gate(p12, LAT, LON, gate, now, None)
    assert eta == 0.0, f"1-2 is {above:.1f} deg above its gate"


def test_a_target_that_never_rises_within_the_scan_answers_none():
    """CONTROL. No crossing inside the scan answers ``None``, as before:
    1-2 with the window closing 1200 s after 390 s, two steps short of its
    rise, and a target 60 deg south of the equator that never clears 10 deg
    at the fixture site in a sidereal day. GREEN under "600 s step
    restored" (observed).

    MUTANT "a scan with no crossing answers its last step": RED
    (observed; pytest's "where" line, which prints the call's arguments,
    cut):
        AssertionError: 1-2's window closes before it rises
        assert 1200.0 is None
    """
    p12, gate = _panel_1_2()
    now = T0 + WAIT_BEGINS
    assert _rise(p12, gate, now) > now + 1200.0, "premise: it rises later"
    assert schedule._time_to_gate(p12, LAT, LON, gate, now,
                                  now + 1200.0) is None, (
        "1-2's window closes before it rises")
    south = Target(name="deep south", ra_hours=p12.ra_hours, dec_deg=-60.0,
                   schedule=Schedule(min_altitude_deg=10.0),
                   steps=[ExposureStep(filter="L", exposure_s=30.0, count=1)])
    peak = schedule.target_max_altitude(south.ra_hours, south.dec_deg, LAT, LON,
                                        now, None)
    assert peak < 10.0, f"premise: it never clears 10 deg: {peak}"
    assert schedule._time_to_gate(south, LAT, LON, 10.0, now, None) is None, (
        "a target that never clears its gate in a day")


async def test_1_2_is_first_visited_within_one_engine_tick_of_its_rise(
        group_hub, monkeypatch):
    """#434's night: the run begins to wait for 1-2 at 390 s and visits it
    within one engine tick (``SCHEDULE_WAIT_STEP_S``, 5 s) of 1800 s, and
    not before it clears its gate.

    MUTANT "600 s step restored": RED (observed; the issue's 2190 s):
        AssertionError: 1-2 first visited at 2190.0 s: it clears its gate
        at 1796.934 s, and the run must be there within one 5.0 s tick of
        1800 s
        assert 390.0 <= 5.0
         +  where 390.0 = abs((2190.0 - 1800.0))

    It is also why the tolerance is 5 s and not the 10 s the issue floated.
    Under the variant "tolerance 10 s" the bisection stops a halving
    sooner, at 9.4 s, and its upper end was past the tick (observed):
        AssertionError: 1-2 first visited at 1805.625 s: it clears its gate
        at 1796.934 s, and the run must be there within one 5.0 s tick of
        1800 s
        assert 5.625 <= 5.0
         +  where 5.625 = abs((1805.625 - 1800.0))
    """
    night = await _night(group_hub, monkeypatch, _plan("altitude"))
    assert night.done, night.lines[-4:]
    waits = [t for t, v in _published(night)
             if v.get("detail") == "waiting for M31 1-2"]
    assert waits and waits[0] == WAIT_BEGINS, (
        f"premise: the run began to wait for 1-2 at {WAIT_BEGINS:.0f} s: "
        f"{waits}")
    p12, gate = _panel_1_2()
    rise = _rise(p12, gate, T0) - T0
    visits = [night.rel(t) for t, who in night.gotos if who == _name("1-2")]
    assert visits, f"premise: 1-2 was visited: {night.gotos}"
    first = visits[0]
    assert abs(first - RISES) <= TICK, (
        f"1-2 first visited at {first} s: it clears its gate at {rise:.3f} "
        f"s, and the run must be there within one {TICK} s tick of "
        f"{RISES:.0f} s")
    assert first >= rise, f"1-2 visited at {first} s, before its rise"
    assert night.stored.status == "complete", night.stored.status


async def test_the_published_eta_at_the_390_s_wait_is_the_rise(
        group_hub, monkeypatch):
    """The wait published at 390 s says 1-2 is 1410 s away, within the
    tolerance, and within the tolerance of the rise it really waits for,
    1406.9 s. The Monitor's countdown reads this number (spec 5.10).

    TWO NUMBERS, AND THE SECOND IS THE CLAIM. 1410 s is RISES less 390 s,
    the number the task was set with; the gate is 0.01 deg under the
    altitude at RISES, so the rise is truly 3.1 s sooner. At the 5 s
    tolerance the estimate lands in [1406.9, 1411.9] s (it published 1411),
    within 5 s of both. A tolerance finer than 3.1 s would fail the first
    line with an estimate that is right: under the variant "tolerance 1 s"
    it published 1407 and read (observed)
        AssertionError: ... 'eta_s': 1407, ... 1-2 clears its gate 1406.9
        s later
        assert 3.0 <= 1.0
    so narrowing the tolerance takes that line out, and keeps the second.

    MUTANT "600 s step restored": RED (observed; 390 s long):
        AssertionError: the wait published at 390 s: {'state': 'waiting',
        'reason': 'below start altitude (73.158 deg)', 'eta_s': 1800,
        'start_ts': 1788313689.0, 'stop_ts': None}; 1-2 clears its gate
        1406.9 s later
        assert 390.0 <= 5.0
         +  where 390.0 = abs((1800 - (1800.0 - 390.0)))
    """
    night = await _night(group_hub, monkeypatch, _plan("altitude"))
    assert night.done, night.lines[-4:]
    waits = [(t, v["schedule"]) for t, v in _published(night)
             if v.get("detail") == "waiting for M31 1-2"]
    assert waits and waits[0][0] == WAIT_BEGINS, (
        f"premise: the run began to wait for 1-2 at {WAIT_BEGINS:.0f} s: "
        f"{[t for t, _s in waits]}")
    sched = waits[0][1]
    p12, gate = _panel_1_2()
    true = _rise(p12, gate, T0 + WAIT_BEGINS) - (T0 + WAIT_BEGINS)
    eta = sched["eta_s"]
    said = (f"the wait published at {WAIT_BEGINS:.0f} s: {sched}; 1-2 clears "
            f"its gate {true:.1f} s later")
    assert abs(eta - (RISES - WAIT_BEGINS)) <= TOL, said
    # Published in whole seconds (``round``), so half a second more.
    assert true - 0.5 <= eta <= true + TOL + 0.5, said


async def test_the_resume_floor_hold_inherits_the_refined_rise(group_hub):
    """`ResumeArm._floor_eta_note`, the start-floor hold's "(it reaches N
    deg in about M min)", calls `_time_to_gate` itself and was not edited:
    asked 1330 s before 1-2's rise, it says 22 min, the rise to the minute.
    The 600 s scan put the rise on its third step from there, 1800 s, and
    the hold said 30 (#434's Impact names this call). The note is site
    derived and rides the hold's ``site_detail`` alone (#233); this reads it
    straight from the call.

    MUTANT "600 s step restored": RED (observed):
        AssertionError: assert ' (it reaches...about 30 min)' == ' (it
        reaches...about 22 min)'
          -  (it reaches 73 deg in about 22 min)
          ?                              ^^
          +  (it reaches 73 deg in about 30 min)
          ?                              ^^
    """
    p12, gate = _panel_1_2()
    ask = _rise(p12, gate, T0) - 1330.0
    arm = ResumeArm(SequenceEngine(group_hub), group_hub, clock=lambda: ask)
    note = arm._floor_eta_note(p12, gate)
    minutes = re.findall(r"in about (\d+) min\)", note)
    assert minutes, f"premise: the note names minutes: {note!r}"
    assert note == f" (it reaches {gate:.0f} deg in about 22 min)"
