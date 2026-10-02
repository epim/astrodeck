# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A safety pause that closes out with its stop confirmed closes the idle
latch that stop satisfied (#530, H4-ENG-C; spec 5.8 and 6.17, the safety
pause and the idle watch, #236).

THE DEFECT. The open-sky safety pause stops tracking (`_park_hold`), reads
the stop back and asks again while it is unconfirmed (#345). When it closed
out with no target to re-acquire, as a pause opened by the scheduler
wait's gate does (``target=None``), it left the idle watch's bookkeeping as
it found it: the idle latch (`_idle_hold_open`) still open, and
`_tracked_target` still naming the target the mount was left on. The
scheduler wait's next idle look (`_idle_hold_tick`) then found that target
"tracked" and, once ``WAIT_TEARDOWN_S`` had passed since the last frame,
decided a stop: it logged "<target>: nothing has been shot for a while and
the mount is still tracking it" about a mount the pause had stopped and
read back as stopped, and the idle-stop task sent a second
``set_tracking(False)``. The rule #236 wrote for the pause's own cooler
gate, kept by the waits #452 made read the weather, but not by
`_wait_until`. The class: bookkeeping that outlives the fact it describes
(#16, #248).

THE FIX. `_park_hold_pause`'s close-out closes the latch when, and only
when, its stop was read back as confirmed (its ``unconfirmed`` False): the
latch is what says "this spell's stop has been made", and the next
`_setup_target` re-opens it, as it always has. A pause that ends with its
stop still unconfirmed leaves the latch open, so the idle look asking again
is a backstop, not a redundant call.

THE NIGHT is test_idle_park_hold's clocked simulator, as
test_s7_waits_read_the_weather.py runs it: ResumeArm left the mount tracking
Alpha and started a run whose one target waits on its hour-angle
constraint, so the run goes straight into the scheduler's wait, whose gate
reads the monitor every ``SCHEDULE_WAIT_STEP_S``. The monitor's reading is a
script on the fake clock: rain from ``RAIN_AT`` to ``CLEAR_AT``, safe
outside it. The safety config pauses on the first unsafe read and resumes
on the first safe one. Alpha's idle clock started when the run did, so it
runs out at ``WAIT_TEARDOWN_S``, after the pause has closed out. The site
is a fixture, and nothing here prints a mount's altitude or azimuth.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad H4-ENG-C-mut, from a byte
backup), never in the shared tree (#254):

* "the pause leaves the latch open": the close-out's ``if not
  unconfirmed:`` in front of ``self._idle_hold_open = False`` made ``if
  False:``.
* "the latch closed whatever the stop said": that ``if not unconfirmed:``
  made ``if True:``.
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import SafetyConfig

from test_cooling_wait_watches_the_mount import _left_tracking
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _Clocked, _constraint_waiter, _plan, _ra_at, _target, sim_hub, temp_store)
from test_s7_waits_read_the_weather import (PAUSE_AT_ONCE, SAFE_AGAIN,
                                            _monitor, _spy_lines, _spy_unsafe)

pytestmark = pytest.mark.asyncio

TEARDOWN_S = engine_mod.WAIT_TEARDOWN_S
TICK_S = engine_mod.SCHEDULE_WAIT_STEP_S
#: The rain, in fake seconds after the start: inside the scheduler's wait and
#: before Alpha's idle clock runs out, off the gate's 5 s grid so the read
#: that sees it is unambiguous; it clears well before the idle clock ends.
RAIN_AT, CLEAR_AT = 32.5, 60.0
#: How long the night runs: the idle clock's end and three minutes past it.
HORIZON_S = TEARDOWN_S + 180.0
STILL_TRACKING = "nothing has been shot for a while and the mount is still tracking"
UNCONFIRMED = "did not confirm the safety pause's stop of tracking"


def _a_mount_that_answers_the_stop_and_tracks_on(run: _Clocked,
                                                 monkeypatch) -> None:
    """``set_tracking(False)`` returns at once and the mount goes on
    tracking, so every read-back says it is still tracking: a stop the
    mount never takes (#345). ``set_tracking(True)`` works. The harness's
    own recording wrapper stays underneath, so every stop is still in
    ``run.tracking_off``."""
    tel = run.hub.devices["telescope"]
    recorded = tel.set_tracking

    async def set_tracking(on):
        await recorded(on)
        if not on and not run.frozen.is_set():
            tel.rig.tracking = True

    monkeypatch.setattr(tel, "set_tracking", set_tracking)


async def _night(sim_hub, temp_store, monkeypatch, *, confirmed: bool):
    temp_store.set_safety(SafetyConfig(**PAUSE_AT_ONCE))
    run = _Clocked(sim_hub, monkeypatch, horizon_s=HORIZON_S)
    t0 = run.t0
    alpha = _target("Alpha", _ra_at(-3.0, t0), 40.0)
    plan = _plan(_constraint_waiter("Bravo", t0))
    plan.safety_check = True
    _monitor(run, monkeypatch, origin=t0, rain=(RAIN_AT, CLEAR_AT))
    unsafe = _spy_unsafe(run, monkeypatch)
    lines = _spy_lines(run, monkeypatch)
    if not confirmed:
        _a_mount_that_answers_the_stop_and_tracks_on(run, monkeypatch)
    await _left_tracking(run, alpha)
    return run, plan, alpha, unsafe, lines


async def test_a_confirmed_pause_leaves_no_idle_stop_to_make(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Rain at ``RAIN_AT`` reaches the scheduler wait's gate within one
    tick and pauses the run, with no target: the pause stops tracking, and
    the mount reads back stopped. The rain clears at ``CLEAR_AT``, and the
    pause closes out and returns to the wait, re-acquiring nothing. The
    wait goes on past Alpha's idle clock (``WAIT_TEARDOWN_S`` after the
    start) for three minutes, and its idle looks make no stop and say
    nothing: the one ``set_tracking(False)`` of the night is the pause's,
    and no line calls the stopped mount still tracking.

    RED ON THE TREE BEFORE THE FIX (observed), and the same under the
    mutant "the pause leaves the latch open" (observed), the #530 defect:
        AssertionError: after the pause closed out at 64.0 s with its stop
        confirmed, the wait's idle look stopped the mount again:
        set_tracking(False) at [35.0, 124.0] s, lines ['Alpha: nothing has
        been shot for a while and the mount is still tracking it —
        stopping tracking until the next target is set up']
    """
    run, plan, alpha, unsafe, lines = await _night(sim_hub, temp_store,
                                                   monkeypatch, confirmed=True)
    t0 = run.t0
    try:
        await run.night(plan, tracking=alpha)
        first = [t for t, _why, who in unsafe if who is None]
        assert first and t0 + RAIN_AT <= first[0] <= t0 + RAIN_AT + TICK_S, (
            f"premise: the scheduler wait's gate paused the run, with no "
            f"target, within one tick of the rain: {run.rel(first, t0)} s")
        closed = [t for t, m, s in lines if m == SAFE_AGAIN and s == "safety"]
        assert closed and closed[0] < t0 + TEARDOWN_S, (
            f"premise: the pause closed out before Alpha's idle clock ran "
            f"out: {run.rel(closed, t0)} s")
        assert not [m for _t, m, _s in lines if UNCONFIRMED in m], (
            "premise: the pause's stop was read back as confirmed")
        later = [t for t in run.ticks if t >= t0 + TEARDOWN_S + TICK_S]
        assert len(later) >= 10, (
            f"premise: the wait went on past the idle clock: ticks after it "
            f"at {run.rel(later[:3], t0)} s")
        looks = [m for _t, m, _s in lines if STILL_TRACKING in m]
        assert run.tracking_off == [first[0]] and looks == [], (
            f"after the pause closed out at {run.rel(closed, t0)[0]} s with "
            f"its stop confirmed, the wait's idle look stopped the mount "
            f"again: set_tracking(False) at {run.rel(run.tracking_off, t0)} "
            f"s, lines {looks}")
        assert run.tracking() is False, "premise: the mount stayed stopped"
    finally:
        await run.close()


async def test_control_an_unconfirmed_pause_still_gets_the_idle_look_s_stop(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same night on a mount that answers the stop and tracks
    on, so the pause never reads its stop back as confirmed (and says so,
    once). It closes out with the latch still open, and the wait's idle
    look, once Alpha's idle clock has run out, decides the stop again and
    says why: a backstop for a stop nobody saw taken, which the fix keeps.

    MUTANT "the latch closed whatever the stop said": RED (observed), the
    stop nobody saw taken left unasked:
        AssertionError: the pause closed out with its stop unconfirmed and
        the idle look made no stop of its own: set_tracking(False) at
        [35.0] s, lines []
    The tree before the fix keeps this green (observed): it left the latch
    open after every pause.
    """
    run, plan, alpha, unsafe, lines = await _night(sim_hub, temp_store,
                                                   monkeypatch, confirmed=False)
    t0 = run.t0
    try:
        await run.night(plan, tracking=alpha)
        first = [t for t, _why, who in unsafe if who is None]
        closed = [t for t, m, s in lines if m == SAFE_AGAIN and s == "safety"]
        assert first and closed and closed[0] < t0 + TEARDOWN_S, (
            f"premise: the pause opened at {run.rel(first, t0)} s and closed "
            f"out at {run.rel(closed, t0)} s, before the idle clock ran out")
        assert [m for _t, m, _s in lines if UNCONFIRMED in m], (
            "premise: the pause said its stop was not confirmed")
        looks = [t for t, m, _s in lines if STILL_TRACKING in m]
        idle_stops = [t for t, on, who in run.tracking_calls
                      if not on and who == "retry"]
        assert (looks and t0 + TEARDOWN_S <= looks[0]
                <= t0 + TEARDOWN_S + TICK_S and idle_stops
                and idle_stops[0] >= looks[0]), (
            f"the pause closed out with its stop unconfirmed and the idle "
            f"look made no stop of its own: set_tracking(False) at "
            f"{run.rel(run.tracking_off, t0)} s, lines "
            f"{[m for _t, m, _s in lines if STILL_TRACKING in m]}")
    finally:
        await run.close()
