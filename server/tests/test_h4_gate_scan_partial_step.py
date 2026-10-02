# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The altitude gate's rise scan covers exactly the window (#498; spec 5.1,
the soonest waiter, and 5.10, the published ``eta_s``).

THE DEFECT. `schedule._time_to_gate` scanned ``max(1, int(span / 600))``
whole ``_PEAK_STEP_S`` steps from the moment it was asked, where ``span``
runs to the window's ``stop_ts``. That span is not the window, in two ways:

1. The partial last step was never scanned. A target that crosses its gate
   between the last whole step and ``stop_ts`` answered ``None``, although it
   rises inside its window, and `gating_status` published ``eta_s`` 0 (the
   Monitor's "unknown") for a wait that ends in minutes.
2. A window shorter than one step was scanned a whole step past ``stop_ts``,
   so a crossing after the window has closed could be answered.

It is the class #434 belongs to: a coarse scan grid whose edges decide the
answer.

THE FIX. Each step is ``min(now + i * _PEAK_STEP_S, horizon)``, with a last
step at ``horizon`` when the span is not a whole number of steps, so the scan
ends at the window's close exactly, and the #434 bisection refines a crossing
inside that partial step as it does inside any other.

THE NIGHT is #498's probe, which is #434's: panel 1-2 of
test_scheduler_waiter_effective_wake.py's ``_plan("altitude")`` at the
harness's fixture site, asked at 390 s into the night, when that run begins
to wait for it. It clears its gate 1406.9 s after that (1796.9 s in). The
helpers are imported from test_s7_time_to_gate_refined.py, so the three files
cannot drift apart on the night.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim (pytest's own lines, long ones wrapped). The mutants were applied in
a private scratch copy of server/ (scratchpad/H4-SCHED-mut, under the
session's scratch root), never in the shared tree (#254):

* "the partial last step not scanned": the step count back to
  ``max(1, int(span / _PEAK_STEP_S))``, as before the fix, with each step
  still clamped to the horizon.
* "scan a whole step past stop_ts": each step back to
  ``now + i * _PEAK_STEP_S``, unclamped, as before the fix, with the step
  count still rounded up.
* "a sub-step window scans nothing": the step count as
  ``int(span / _PEAK_STEP_S)``, the ``max(1, ...)`` dropped and nothing
  rounded up, which is the first fix that stops a scan running past a short
  window's close.

A FOURTH CASE, #586. None of the above ever gave the scan a horizon at or
before ``now``: every case above asks about a window that is still open. The
comment at the step count says "nothing is scanned once the horizon is not
after now", but nothing had asked that. Added here, 2026-10-01:

* "SC1, the closed-window clamp lost": line 908's
  ``steps = max(0, math.ceil((horizon - now) / _PEAK_STEP_S))`` back to
  ``max(1, ...)``, the same shape as "the partial last step not scanned"
  above but applied to a horizon that has already passed, not one still
  ahead. It forces one step, clamped to the horizon, so the single step
  lands AT OR BEFORE ``now`` instead of after it; if the target was above
  the gate there (true of any window closed after a target has set, since
  it was above the gate to set through it), the bracket the bisection is
  handed is backwards (``below`` is ``now``, the scan's one point is
  earlier), the halving loop's `t - below > tol` is never true, and the
  function returns ``t - now``, negative. A caller that slept on it would
  read a closed window as due right now.
"""
from __future__ import annotations

from _group_harness import LAT, LON, T0
from astrodeck.sequence import schedule
from astrodeck.sequence.models import Target
from test_s7_time_to_gate_refined import (TOL, WAIT_BEGINS, _alt, _panel_1_2,
                                          _rise)

#: The site `gating_status` reads, the harness's fixture site, saved.
SITE = {"latitude": LAT, "longitude": LON, "is_default": False}


def _asked_at_390() -> tuple[object, float, float, float]:
    """1-2, its gate, the moment of asking and how long after it 1-2 rises."""
    p12, gate = _panel_1_2()
    now = T0 + WAIT_BEGINS
    return p12, gate, now, _rise(p12, gate, now) - now


def _first_set(target: Target, gate: float, after: float) -> float:
    """The instant ``target`` first drops below ``gate`` after ``after``,
    given it is above the gate at ``after``: a coarse scan, then a
    bisection of its bracket to a millisecond. Mirrors ``_rise`` for the
    descending crossing, which #586's case needs and no helper file has."""
    assert _alt(target, after) >= gate, "premise: above the gate at 'after'"
    lo = after
    while _alt(target, lo + 60.0) >= gate:
        lo += 60.0
        assert lo < after + 86400.0, "premise: the target sets within a day"
    hi = lo + 60.0
    while hi - lo > 1e-3:
        mid = (lo + hi) / 2.0
        if _alt(target, mid) >= gate:
            lo = mid
        else:
            hi = mid
    return hi


def test_a_rise_in_the_partial_last_step_is_found():
    """#498's own probe. The window closes 1500 s after asking: two whole
    steps and a 300 s partial one, and 1-2 rises 1406.9 s after asking,
    inside the partial step. The answer is that rise, to the #434
    tolerance, not ``None``; the target is at or above its gate at the
    instant it names; and it is not past the window's close.

    MUTANT "the partial last step not scanned": RED (observed):
        AssertionError: 1-2 rises 1406.9 s after asking, inside a window
        that closes 1500 s after asking
        assert None is not None

    The same lines under "a sub-step window scans nothing" (observed),
    which scans no part step either. GREEN under "scan a whole step past
    stop_ts" (observed): its unclamped third step, at 1800 s, finds the
    same rise and the bisection brings it back inside the window.
    """
    p12, gate, now, true = _asked_at_390()
    assert 1200.0 < true < 1500.0, (
        f"premise: 1-2 rises in the partial step, {true:.1f} s after asking")
    eta = schedule._time_to_gate(p12, LAT, LON, gate, now, now + 1500.0)
    assert eta is not None, (
        f"1-2 rises {true:.1f} s after asking, inside a window that closes "
        f"1500 s after asking")
    assert true - 1e-3 <= eta <= true + TOL + 1e-3, (eta, true)
    assert _alt(p12, now + eta) >= gate, f"below the gate at {eta} s"
    assert eta <= 1500.0, f"answered {eta} s, past the window's close"


def test_the_wait_published_for_that_window_reads_the_rise():
    """The same wait as `gating_status` publishes it, the number the
    Monitor's countdown reads (spec 5.10): the engine's frozen window opened
    at the night's start and closes 1500 s after asking, so 1-2 is waiting
    on its altitude, and ``eta_s`` is the rise, where it read 0.

    MUTANT "the partial last step not scanned": RED (observed):
        AssertionError: {'state': 'waiting', 'reason': 'below start
        altitude (73.158 deg)', 'eta_s': 0.0, 'start_ts': 1788313689.0,
        'stop_ts': 1788315579.0}; 1-2 clears its gate 1406.9 s after asking
        assert (1406.93359375 - 0.001) <= 0.0

    The same lines under "a sub-step window scans nothing" (observed).
    """
    p12, gate, now, true = _asked_at_390()
    gs = schedule.gating_status(p12, SITE, -12.0, now,
                                window=(T0, now + 1500.0))
    assert gs["state"] == "waiting", gs
    assert gs["reason"].startswith("below start altitude"), gs
    assert true - 1e-3 <= gs["eta_s"] <= true + TOL + 1e-3, (
        f"{gs}; 1-2 clears its gate {true:.1f} s after asking")


def test_a_window_that_closes_before_the_rise_still_answers_none():
    """CONTROL. The window closes 1400 s after asking, 6.9 s before 1-2
    rises, with a 200 s partial step: ``None``, as before. GREEN under "the
    partial last step not scanned" (observed), which never scans that step.

    It is RED under "scan a whole step past stop_ts" (observed), whose last
    step lands at 1800 s and finds the rise after the close:
        AssertionError: 1-2 rises 1406.9 s after asking, after a window
        that closes 1400 s after asking
        assert 1410.9375 is None
    (pytest's "where" line, which prints the call's arguments, cut).
    """
    p12, gate, now, true = _asked_at_390()
    assert 1400.0 < true, f"premise: 1-2 rises after the close: {true:.1f}"
    assert schedule._time_to_gate(p12, LAT, LON, gate, now,
                                  now + 1400.0) is None, (
        f"1-2 rises {true:.1f} s after asking, after a window that closes "
        f"1400 s after asking")


def test_a_sub_step_window_is_not_scanned_past_its_close():
    """CONTROL. Asked 400 s before 1-2 rises, with the window closing 300 s
    after asking: shorter than one step, and closed 100 s before the rise.
    ``None``: the one step there is lands on the close, not 600 s on.

    MUTANT "scan a whole step past stop_ts": RED (observed; the rise 100 s
    after the close answered, which is the code before the fix too):
        AssertionError: the window closes 300 s after asking and 1-2 rises
        400 s after it
        assert 403.125 is None
    (the "where" line cut). GREEN under "the partial last step not scanned"
    and "a sub-step window scans nothing" (observed).
    """
    p12, gate = _panel_1_2()
    now = _rise(p12, gate, T0) - 400.0
    assert _alt(p12, now) < gate, "premise: 1-2 is below its gate"
    assert schedule._time_to_gate(p12, LAT, LON, gate, now,
                                  now + 300.0) is None, (
        "the window closes 300 s after asking and 1-2 rises 400 s after it")


def test_a_sub_step_window_that_holds_the_rise_finds_it():
    """The clamp does not lose a rise: asked 400 s before 1-2 rises, with
    the window closing 450 s after asking, the answer is the rise, to the
    tolerance, and within the window. GREEN under "scan a whole step past
    stop_ts" (observed), whose one step at 600 s finds the same rise.

    MUTANT "a sub-step window scans nothing": RED (observed):
        AssertionError: 1-2 rises 400 s after asking, inside the window
        assert None is not None
    """
    p12, gate = _panel_1_2()
    now = _rise(p12, gate, T0) - 400.0
    eta = schedule._time_to_gate(p12, LAT, LON, gate, now, now + 450.0)
    assert eta is not None, "1-2 rises 400 s after asking, inside the window"
    assert 400.0 - 1e-3 <= eta <= min(400.0 + TOL, 450.0) + 1e-3, eta


def test_the_answer_is_the_window_wherever_it_closes():
    """The invariant on a grid, since one window is not enough to show a
    grid's edges no longer decide (#434's lesson): asked at 390 s, for
    windows closing every 23 s from 20 s to 2000 s after asking, and asked
    400 s before the rise, for windows from 20 s to 590 s. A window that
    closes before the rise answers ``None``; one that holds it answers the
    rise, to the tolerance, and never past its close.

    MUTANT "the partial last step not scanned": RED (observed; every window
    closing in the partial step after the rise answered None):
        AssertionError: 17 of 112 windows answered wrongly (asked at s into
        the night, window s, answer): [(390.0, 1423, None), (390.0, 1446,
        None), (390.0, 1469, None), (390.0, 1492, None), (390.0, 1515,
        None)]
        assert 17 == 0

    MUTANT "scan a whole step past stop_ts": RED (observed; every window
    closing before the rise but after the last whole step answered the rise
    after its close):
        AssertionError: 26 of 112 windows answered wrongly (asked at s into
        the night, window s, answer): [(390.0, 1216, 1410.9375), (390.0,
        1239, 1410.9375), (390.0, 1262, 1410.9375), (390.0, 1285,
        1410.9375), (390.0, 1308, 1410.9375)]
        assert 26 == 0

    MUTANT "a sub-step window scans nothing": RED (observed):
        AssertionError: 25 of 112 windows answered wrongly (asked at s into
        the night, window s, answer): [(390.0, 1423, None), (390.0, 1446,
        None), (390.0, 1469, None), (390.0, 1492, None), (390.0, 1515,
        None)]
        assert 25 == 0
    (each "where" line, which repeats the list, cut).
    """
    p12, gate = _panel_1_2()
    rise = _rise(p12, gate, T0)
    asks = [(T0 + WAIT_BEGINS, range(20, 2001, 23)),
            (rise - 400.0, range(20, 591, 23))]
    wrong = []
    n = 0
    for now, windows in asks:
        true = rise - now
        for w in windows:
            n += 1
            eta = schedule._time_to_gate(p12, LAT, LON, gate, now, now + w)
            if w < true:
                ok = eta is None
            else:
                ok = (eta is not None
                      and true - 1e-3 <= eta <= min(true + TOL, w) + 1e-3)
            if not ok:
                wrong.append((round(now - T0, 1), w, eta))
    assert len(wrong) == 0, (
        f"{len(wrong)} of {n} windows answered wrongly (asked at s into the "
        f"night, window s, answer): {wrong[:5]}")


def test_a_horizon_at_or_before_now_answers_none_not_a_negative_wait():
    """#586. Every case above asks about a window still open at ``now``; none
    gives the scan a ``stop_ts`` (the window's horizon) that has already
    passed. 1-2 clears its 73.158 deg gate near the start of the night and
    stays above it for hours, setting back below it only at 12348.3 s in, so
    a window that closed earlier, back while 1-2 was still above the gate,
    but is asked about only after 1-2 has since set, is exactly the case the
    scan's "nothing is scanned once the horizon is not after now" comment
    promises and nothing had tried: ``stop_ts`` (2396.9 s in) before ``now``
    (12648.3 s in), with the target above the gate AT the horizon and below
    it now.

    RED under MUTANT "SC1, the closed-window clamp lost" (observed):
        AssertionError: a window that closed 2396.9 s in, asked about at
        12648.3 s in (1-2 set at 12348.3 s in, well before), must answer
        None, not a negative wait
        assert -10251.405029296875 is None

    CONTROL, same line: ``stop_ts == now`` is the boundary the comment
    describes most literally, and it answers ``None`` under SC1 too (the
    one forced step then lands on ``now`` itself, where 1-2 is already
    below the gate, so the scan still finds no crossing) -- it is not a
    kill, which is why the case above needs a ``stop_ts`` where the target
    was ABOVE the gate, not merely a ``stop_ts`` before ``now``.
    """
    p12, gate = _panel_1_2()
    rise = _rise(p12, gate, T0)
    set_time = _first_set(p12, gate, rise)
    stop_ts = rise + 600.0          # comfortably inside the long pass above
    now = set_time + 300.0          # comfortably after 1-2 has set again
    assert stop_ts < now, "premise: the window closed before now"
    assert _alt(p12, stop_ts) >= gate, (
        "premise: 1-2 was above its gate when the window closed")
    assert _alt(p12, now) < gate, "premise: 1-2 is below its gate now"

    eta = schedule._time_to_gate(p12, LAT, LON, gate, now, stop_ts)
    assert eta is None, (
        f"a window that closed {stop_ts - T0:.1f} s in, asked about at "
        f"{now - T0:.1f} s in (1-2 set at {set_time - T0:.1f} s in, well "
        f"before), must answer None, not a negative wait")

    # CONTROL (see docstring): the boundary does not kill SC1 on its own.
    assert schedule._time_to_gate(p12, LAT, LON, gate, now, now) is None
