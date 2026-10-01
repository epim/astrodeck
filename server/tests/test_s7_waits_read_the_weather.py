"""The run-start cooling wait, the camera-lane wait and a cloud hold's
release cooler gate read the safety monitor on its own clock and act on an
unsafe verdict (#452; spec 5.8 and 6.17, "safety rides value paths").

The engine has no safety task beside the run. The monitor is read by
`_safety_gate`, which the frame loop, the scheduler's wait (`_wait_until`)
and the setups call, and by the safety releases' own loops. Three waits
called none of them:

* THE RUN-START COOLING WAIT (`_run`'s `_cool_and_wait`), up to
  ``cool_timeout_s`` for the band and ``COOLER_SETTLE_MAX_S`` to settle:
  minutes of a sensor walking down from ambient after a restart or an
  auto-resume;
* THE CAMERA-LANE WAIT AT RUN START (`_await_camera_lane`), up to
  ``_CAMERA_LANE_WAIT_S``;
* A CLOUD HOLD'S RELEASE COOLER GATE (`_hold_for_clear`'s `_cooler_gate`),
  up to the same 25 minutes, with the held target tracked by design.

All three took the idle look at the mount (#202, #236), and none read the
weather: rain that began inside one was acted on only when the wait ended
and the next gate read the monitor, so a closeable roof stayed open, and a
pause or a park waited for the wait. The "safety rides value paths" class:
each wait's checks rode its work (the cooler's settle, the lane's release)
while the hazard, the weather, ran on its own clock.

NOW each reads the monitor every ``SAFETY_PAUSE_POLL_S`` from the moment it
begins, through the scheduler wait's own gate, and hands an unsafe verdict
to `_on_unsafe`. The cooling waits run on a task of their own beside the
reads (`_wait_beside_the_monitor`), since a temperature read can hang for
``COOLER_CMD_TIMEOUT_S``; the lane wait reads inline, on the weather's
cadence rather than its poll's. Before a pause `_on_unsafe` ends the wait,
and once the pause is over the wait begins again, with no idle look at the
mount the pause stopped. A pause the hold's release opens leaves the held
target's acquisition to the release (#241).

THE HARNESSES are test_idle_park_hold's clocked simulator (the run start
and the lane), and test_cloud_hold_watch's night with a cloud hold in it
(the release), with the safety monitor assigned and its reading a script on
the fake clock: safe, then rain from ``RAIN_AT`` fake seconds after the
wait's own reference, then safe again. The safety config pauses on the
first unsafe read (``unsafe_consecutive`` 1), so "within one poll" is the
reads' cadence and not a debounce. A cooling wait's own task is handed to
the driver (test_safe_again_after_cooler_gate's `_clock_the_cooling`).
Every `_on_unsafe` call, every temperature probe, every safety read and
every stop of tracking is recorded with its fake time. The sites are
fixtures, never the real one, and nothing here prints a mount's altitude or
azimuth.

AND ONE WAVE-2 ENGINE DEFECT, #515, at the end of the file: its cases
have no file of their own in this task, and say so there.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S7-ENG-SAFE-r2-mut, in the session scratchpad), never in the
shared tree (#254), and every quote is from that copy's run.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import EscalationConfig, SafetyConfig
from astrodeck.devices.base import SafetyReading
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.session import session_store

from _group_harness import Night, group_hub, group_store, single  # noqa: F401
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _Clocked, _constraint_waiter, _park_lines, _plan, _ra_at, _target,
    sim_hub, temp_store)
from test_cooling_wait_watches_the_mount import _left_tracking
from test_safe_again_after_cooler_gate import _clock_the_cooling
from test_waits_that_watch_the_mount import (RELEASE_S, _cooled_hold_plan,
                                             _a_sensor_that_drifts_at_the_release,
                                             _lane_held_until)
from test_cloud_hold_watch import EXP, _Watched
from test_cloud_hold_watch import _target as _hold_target

pytestmark = pytest.mark.asyncio

POLL_S = engine_mod.SAFETY_PAUSE_POLL_S
HANG_S = engine_mod.COOLER_CMD_TIMEOUT_S
COOL_TO = -5.0
#: The safety config every case here runs under: pause on the first unsafe
#: read, resume on the first safe one, no cap on the pause, nothing standing
#: in for the monitor.
PAUSE_AT_ONCE = dict(enabled=True, on_unsafe="pause", unsafe_consecutive=1,
                     resume_safe_consecutive=1, max_pause_min=0,
                     sky_fallback_hold=False)
SAFE_AGAIN = "conditions safe again — resuming"


def _monitor(run: _Clocked, monkeypatch, *, origin: float,
             rain: tuple[float, float]) -> list[tuple[float, bool]]:
    """The monitor's reading, a script on the fake clock: rain from
    ``rain[0]`` until ``rain[1]`` fake seconds after ``origin``, safe
    outside it. Returns the (fake time, safe) of every read."""
    reads: list[tuple[float, bool]] = []

    async def safety_reading():
        t = run.clock.t - origin
        wet = rain[0] <= t < rain[1]
        reads.append((run.clock.t, not wet))
        if wet:
            return SafetyReading(is_safe=False, reason="rain sensor",
                                 source="script", ts=run.clock.t)
        return SafetyReading(is_safe=True, source="script", ts=run.clock.t)

    monkeypatch.setattr(run.hub, "safety_reading", safety_reading)
    return reads


def _spy_unsafe(run: _Clocked, monkeypatch) -> list[tuple]:
    """Every `_on_unsafe` call, as (fake time, reason, target name)."""
    calls: list[tuple] = []
    engine = run.engine
    real = engine._on_unsafe

    async def on_unsafe(reason, *a, **kw):
        target = kw.get("target")
        calls.append((run.clock.t, reason,
                      None if target is None else target.name))
        return await real(reason, *a, **kw)

    monkeypatch.setattr(engine, "_on_unsafe", on_unsafe)
    return calls


def _spy_lines(run: _Clocked, monkeypatch) -> list[tuple[float, str, str]]:
    """Every bus line, as (fake time, message, source)."""
    from astrodeck import events
    lines: list[tuple[float, str, str]] = []
    real = events.bus.log

    def log(level, message, source="hub", **kw):
        lines.append((run.clock.t, message, source))
        return real(level, message, source, **kw)

    monkeypatch.setattr(events.bus, "log", log)
    return lines


# ------------------------------------------------------ the run-start wait

#: The run-start case, in fake seconds after the start: the rain begins
#: inside the cooling wait, before the idle clock of the target the run was
#: handed runs out, and clears at CLEAR_AT. Off the reads' 5 s grid, so the
#: read that sees it is unambiguous.
START_RAIN_AT, START_CLEAR_AT = 52.5, 200.0
#: The plan's cooling budget: the sensor never comes back here, so each
#: cooling wait gives up at it, under the default "warn", and goes on.
START_BUDGET_S = 300


async def test_rain_in_the_run_start_cooling_wait_reaches_on_unsafe(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """ResumeArm left the mount tracking Alpha and started the resumed run,
    whose first act is the cooling wait, on a sensor that has not come
    back: every temperature read hangs for ``COOLER_CMD_TIMEOUT_S`` and
    times out. Rain begins ``START_RAIN_AT`` in. The wait reads the monitor
    every ``SAFETY_PAUSE_POLL_S`` all the same, and the rain reaches
    `_on_unsafe` within one poll: the run pauses, with the wait ended (no
    probe of the sensor until the pause's own gate). When the rain clears,
    the pause's gate gives up on the sensor at the plan's budget (the
    default "warn"), the pause closes out, and the run-start wait begins
    again, with no idle look at the mount: the pause stopped it, and the
    one ``set_tracking(False)`` of the night is the pause's. The run then
    goes on to its scheduler.

    Mutant "wait reads no weather" (`_run`'s `_cool_and_wait` call given
    ``monitor=False``, so the wait reads nothing, as before #452): RED
    (observed) -
        AssertionError: the rain at 52.5 s reached _on_unsafe at [] s; the
        run-start cooling wait began at 0.0 s and was still waiting
    Mutant "the monitor read on the sensor's clock" (the gate asked
    inside `_cool_and_wait`'s own loop, once per probe, on the run task, in
    place of the watch beside it): RED (observed) -
        AssertionError: the rain at 52.5 s reached _on_unsafe at [70.0] s;
        the run-start cooling wait began at 0.0 s and was still waiting
    (each probe of the sensor that has not come back holds the loop for
    30 s, and the gate waits behind it. The same mutant turns the release
    case below red too, since the hold's gate runs through the same wait.)
    Mutant "look again after a pause" (`_wait_beside_the_monitor` begins
    the wait again with ``looked`` still True): RED (observed) -
        AssertionError: the run-start wait begun again after the pause
        looked at a mount the pause had stopped: set_tracking(False) at
        [55.0, 519.0] s, lines ['Alpha: nothing has been shot for a while
        and the mount is still tracking it — stopping tracking until the
        next target is set up']

    GRADED OVER THE WHOLE NIGHT since #530 (H4-ENG-C): the pause closes the
    idle latch its confirmed stop satisfied, so the scheduler's wait after
    the run-start wait makes no stop either. Mutant "the pause leaves the
    latch open" (`_park_hold_pause`'s ``if not unconfirmed:
    self._idle_hold_open = False`` made ``if False:``), run in the private
    copy H4-ENG-C-mut: RED (observed), the #530 defect -
        AssertionError: the scheduler's wait after the pause looked at a
        mount the pause had stopped: set_tracking(False) at [55.0, 834.0]
        s, lines ['Alpha: nothing has been shot for a while and the mount
        is still tracking it — stopping tracking until the next target is
        set up']
    """
    temp_store.set_safety(SafetyConfig(**PAUSE_AT_ONCE))
    run = _Clocked(sim_hub, monkeypatch, horizon_s=900.0)
    t0 = run.t0
    alpha = _target("Alpha", _ra_at(-3.0, t0), 40.0)
    plan = _plan(_constraint_waiter("Bravo", t0))
    plan.safety_check = True
    plan.cool_to = COOL_TO
    plan.cool_timeout_s = START_BUDGET_S
    reads = _monitor(run, monkeypatch, origin=t0,
                     rain=(START_RAIN_AT, START_CLEAR_AT))
    unsafe = _spy_unsafe(run, monkeypatch)
    lines = _spy_lines(run, monkeypatch)
    cam = sim_hub.devices["camera"]
    probes: list[float] = []

    async def get_temperature():
        probes.append(run.clock.t)
        await run._park(HANG_S)
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cam, "get_temperature", get_temperature)
    waits: list[list] = []
    real_cool = run.engine._cool_and_wait

    async def cool_and_wait(*a, **kw):
        w = [run.clock.t, None, kw.get("state", "running")]
        waits.append(w)
        try:
            return await real_cool(*a, **kw)
        finally:
            w[1] = run.clock.t

    monkeypatch.setattr(run.engine, "_cool_and_wait", cool_and_wait)
    _clock_the_cooling(SimpleNamespace(run=run, engine=run.engine),
                       monkeypatch)
    await _left_tracking(run, alpha)
    try:
        await run.night(plan, tracking=alpha)
        rain = t0 + START_RAIN_AT
        began = [w for w in waits if w[2] == "running"]
        hung = [t for t in probes if t < rain]
        assert began and began[0][0] < rain and len(hung) >= 2, (
            f"premise: the run-start cooling wait began before the rain and "
            f"its sensor reads hung: waits "
            f"{[(run.rel([w[0]], t0), w[2]) for w in waits]}, probes before "
            f"the rain at {run.rel(hung, t0)} s")
        first = [t for t, _why, _who in unsafe]
        assert first and rain <= first[0] <= rain + POLL_S + 1e-6, (
            f"the rain at {START_RAIN_AT:g} s reached _on_unsafe at "
            f"{run.rel(first, t0)} s; the run-start cooling wait began at "
            f"{run.rel([began[0][0]], t0)[0]} s and was still waiting")
        hit = first[0]
        after = [t for t in probes if hit < t < t0 + START_CLEAR_AT]
        assert after == [], (
            f"the run-start wait went on probing the sensor through the "
            f"pause: probes at {run.rel(after, t0)} s after the unsafe verdict "
            f"at {run.rel([hit], t0)[0]} s")
        paused = [t for t, m, _s in lines if m.startswith("UNSAFE")]
        closed = [t for t, m, s in lines if m == SAFE_AGAIN and s == "safety"]
        assert paused and closed and closed[0] >= t0 + START_CLEAR_AT, (
            f"premise: the pause ran and closed out once the rain cleared: "
            f"UNSAFE at {run.rel(paused, t0)} s, safe again at "
            f"{run.rel(closed, t0)} s")
        again = [w for w in waits if w[2] == "running" and w[0] >= closed[0]]
        assert again and again[0][1] is not None, (
            f"the run-start wait did not begin again after the pause: waits "
            f"{[(run.rel([w[0]], t0)[0], w[2]) for w in waits]}")
        done = again[0][1]
        offs = [t for t in run.tracking_off if t < done]
        looks = [m for t, m, _s in lines if t < done
                 and "stopping tracking" in m]
        assert len(offs) == 1 and offs[0] == hit and looks == [], (
            f"the run-start wait begun again after the pause looked at a "
            f"mount the pause had stopped: set_tracking(False) at "
            f"{run.rel(offs, t0)} s, lines {looks}")
        # Each 5 s sleep of the scheduler's wait follows one idle look.
        assert run.ticks and run.ticks[-1] > done, (
            "premise: the run went on to its scheduler, whose wait looked at "
            "the mount")
        # AND OVER THE WHOLE NIGHT (#530, H4-ENG-C). The case was graded
        # only up to the end of the run-start wait begun again, because the
        # scheduler's wait after it took its own look and called the
        # stopped mount still tracking. The pause now closes the idle latch
        # its confirmed stop satisfied, so no wait after it looks again.
        offs = list(run.tracking_off)
        looks = [m for t, m, _s in lines if "stopping tracking" in m]
        assert len(offs) == 1 and offs[0] == hit and looks == [], (
            f"the scheduler's wait after the pause looked at a mount the "
            f"pause had stopped: set_tracking(False) at {run.rel(offs, t0)} "
            f"s, lines {looks}")
        wet = [t for t, ok in reads if not ok]
        assert wet and wet[0] == hit, (
            f"premise: the read that reached _on_unsafe was the first wet "
            f"one: wet reads at {run.rel(wet[:3], t0)} s")
    finally:
        await run.close()


# ------------------------------------------------------- the camera lane

#: The lane case, in fake seconds after the start: a plate solve holds the
#: camera lane until LANE_FREE_S; the rain begins at LANE_RAIN_AT, off the
#: reads' 5 s grid, and clears at LANE_CLEAR_AT, while the lane is still
#: held and after the handed-over target's idle clock has run out.
LANE_RAIN_AT, LANE_CLEAR_AT, LANE_FREE_S = 42.1, 200.0, 260.0


async def test_rain_in_the_camera_lane_wait_reaches_on_unsafe(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """ResumeArm left the mount tracking Alpha and started the resumed run,
    and a plate solve holds the camera lane for its first ``LANE_FREE_S``.
    Rain begins ``LANE_RAIN_AT`` in, inside the lane wait. The wait reads
    the monitor every ``SAFETY_PAUSE_POLL_S`` and the rain reaches
    `_on_unsafe` within one poll: the run pauses there. When the rain
    clears, the pause closes out and the lane wait goes on, with no idle
    look at the mount the pause stopped, though its idle clock has run
    out: the one ``set_tracking(False)`` is the pause's. The run starts
    once the lane is free.

    Mutant "wait reads no weather" (the lane's ``if monitor and
    time.time() >= next_read:`` made ``if False and ...``, as before #452):
    RED (observed) -
        AssertionError: the rain at 42.1 s reached _on_unsafe at [] s; the
        camera-lane wait ran from 0.0 s to 260.0 s
    Mutant "look again after a pause" (the lane's ``looked = False`` after
    a pause taken out): RED (observed) -
        AssertionError: the lane wait looked at a mount the pause had
        stopped: set_tracking(False) at [45.0, 204.0] s, lines ['Alpha:
        nothing has been shot for a while and the mount is still tracking
        it — stopping tracking until the next target is set up']

    GRADED OVER THE WHOLE NIGHT since #530 (H4-ENG-C), the scheduler's wait
    after the lane included. Mutant "the pause leaves the latch open"
    (`_park_hold_pause`'s ``if not unconfirmed: self._idle_hold_open =
    False`` made ``if False:``), run in the private copy H4-ENG-C-mut: RED
    (observed), the #530 defect -
        AssertionError: the scheduler's wait after the lane looked at a
        mount the pause had stopped: set_tracking(False) at [45.0, 260.0]
        s, lines ['Alpha: nothing has been shot for a while and the mount
        is still tracking it — stopping tracking until the next target is
        set up']
    """
    temp_store.set_safety(SafetyConfig(**PAUSE_AT_ONCE))
    run = _Clocked(sim_hub, monkeypatch, horizon_s=LANE_FREE_S + 60.0)
    t0 = run.t0
    alpha = _target("Alpha", _ra_at(-3.0, t0), 40.0)
    plan = _plan(_constraint_waiter("Bravo", t0))
    plan.safety_check = True
    _lane_held_until(run, monkeypatch, t0 + LANE_FREE_S)
    _monitor(run, monkeypatch, origin=t0, rain=(LANE_RAIN_AT, LANE_CLEAR_AT))
    unsafe = _spy_unsafe(run, monkeypatch)
    lines = _spy_lines(run, monkeypatch)
    lane: list[float] = []
    real_lane = run.engine._await_camera_lane

    async def await_camera_lane():
        lane.append(run.clock.t)
        try:
            return await real_lane()
        finally:
            lane.append(run.clock.t)

    monkeypatch.setattr(run.engine, "_await_camera_lane", await_camera_lane)
    await _left_tracking(run, alpha)
    try:
        await run.night(plan, tracking=alpha)
        assert len(lane) == 2 and lane[1] - t0 >= LANE_FREE_S, (
            f"premise: the lane wait lasted until the lane came free: "
            f"{run.rel(lane, t0)} s")
        rain = t0 + LANE_RAIN_AT
        first = [t for t, _why, _who in unsafe]
        assert first and rain <= first[0] <= rain + POLL_S + 1e-6, (
            f"the rain at {LANE_RAIN_AT:g} s reached _on_unsafe at "
            f"{run.rel(first, t0)} s; the camera-lane wait ran from "
            f"{run.rel(lane, t0)[0]} s to {run.rel(lane, t0)[1]} s")
        offs = [t for t in run.tracking_off if t < lane[1]]
        looks = [m for t, m, _s in lines if t < lane[1]
                 and "stopping tracking" in m]
        assert len(offs) == 1 and offs[0] == first[0] and looks == [], (
            f"the lane wait looked at a mount the pause had stopped: "
            f"set_tracking(False) at {run.rel(offs, t0)} s, lines {looks}")
        said = [m for _l, m, s in bus_lines if m == SAFE_AGAIN]
        later = [t for t in run.ticks if t >= lane[1]]
        assert said and later, (
            f"premise: the pause closed out and the run went on to its "
            f"scheduler once the lane was free: safe again {len(said)} "
            f"time(s), 5 s sleeps after the lane at {run.rel(later[:3], t0)} s")
        # AND OVER THE WHOLE NIGHT (#530, H4-ENG-C). The case was graded
        # only up to the end of the lane wait, because the scheduler's wait
        # after it took its own look (a 5 s sleep follows each, ``later``
        # above) and called the stopped mount still tracking. The pause now
        # closes the idle latch its confirmed stop satisfied.
        offs = list(run.tracking_off)
        looks = [m for t, m, _s in lines if "stopping tracking" in m]
        assert len(offs) == 1 and offs[0] == first[0] and looks == [], (
            f"the scheduler's wait after the lane looked at a mount the "
            f"pause had stopped: set_tracking(False) at {run.rel(offs, t0)} "
            f"s, lines {looks}")
    finally:
        await run.close()


# ------------------------------------ which waits have a monitor to read
#
# `_monitor_to_read` decides whether a wait reads the weather beside it: the
# monitor gate armed, and a monitor assigned OR its absence itself an unsafe
# verdict (``require_safety_monitor``). Both arms are graded here on the
# lane wait, the cheapest of the three, with no monitor assigned at all.
# These two cases, and the last #515 case below, were added by the task's
# verifier, and their mutants were run in its own private copy
# (S7-ENG-SAFE-verify-mut, in the session scratchpad), never the shared tree.


def _spy_lane(run: _Clocked, monkeypatch) -> list[float]:
    """The fake time the camera-lane wait began, and the time it ended once
    it has."""
    lane: list[float] = []
    real_lane = run.engine._await_camera_lane

    async def await_camera_lane():
        lane.append(run.clock.t)
        try:
            return await real_lane()
        finally:
            lane.append(run.clock.t)

    monkeypatch.setattr(run.engine, "_await_camera_lane", await_camera_lane)
    return lane


async def test_a_required_monitor_that_is_absent_pauses_the_lane_wait(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """``require_safety_monitor`` is on and no monitor is assigned. An
    absent monitor is then a disconnected one (`_no_safety_source`), an
    unsafe verdict on every read, so the lane wait has a monitor to read
    (`_monitor_to_read`'s second arm): its first read, at its start,
    reaches `_on_unsafe`, and the run pauses there, inside the lane wait,
    and not ``LANE_FREE_S`` later at the scheduler's first gate. Nothing
    can read safe again and ``max_pause_min`` is 0, so the pause holds to
    the horizon with the lane wait still open behind it.

    Mutant "a required monitor is not read" (`_monitor_to_read` without its
    ``or bool(cfg.escalation.require_safety_monitor)``): RED (observed) -
        AssertionError: a required monitor that is absent did not pause the
        lane wait: _on_unsafe at [260.0] s (['no safety monitor is
        assigned, and require_safety_monitor is on']), the lane wait from
        [0.0, 260.0] s
    """
    temp_store.set_safety(SafetyConfig(**PAUSE_AT_ONCE))
    temp_store.set_escalation(EscalationConfig(require_safety_monitor=True))
    monkeypatch.delitem(sim_hub.devices, "safety")
    run = _Clocked(sim_hub, monkeypatch, horizon_s=LANE_FREE_S + 60.0)
    t0 = run.t0
    plan = _plan(_constraint_waiter("Bravo", t0))
    plan.safety_check = True
    _lane_held_until(run, monkeypatch, t0 + LANE_FREE_S)
    unsafe = _spy_unsafe(run, monkeypatch)
    lane = _spy_lane(run, monkeypatch)
    try:
        await run.night(plan)
        first = [(t, why) for t, why, _who in unsafe]
        assert (first and first[0][0] - t0 <= POLL_S + 1e-6
                and "require_safety_monitor" in first[0][1]
                and len(lane) == 1 and lane[0] <= first[0][0]), (
            f"a required monitor that is absent did not pause the lane "
            f"wait: _on_unsafe at {run.rel([t for t, _w in first], t0)} s "
            f"({[w for _t, w in first][:1]}), the lane wait from "
            f"{run.rel(lane, t0)} s")
        assert run.engine.state.get("state") == "paused", (
            f"premise: the pause held to the horizon: {run.engine.state}")
    finally:
        await run.close()


async def test_control_with_no_monitor_to_read_the_lane_wait_asks_no_gate(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The shipped default: safety armed, no monitor assigned, and
    ``require_safety_monitor`` off. The gate would have only the frames'
    sky verdict to stand in for a monitor (`_no_safety_source`), and a wait
    takes no frames, so there is nothing on the weather's clock to read:
    the lane wait asks no gate, as before #452, and the run's first gate is
    the scheduler's, once the lane is free. Nothing is unsafe.

    Mutant "every armed wait reads" (`_monitor_to_read` answering True
    whenever the gate is armed, monitor or not): RED (observed) -
        AssertionError: the lane wait asked the gate with no monitor to
        read: gates at [0.0, 5.0, 10.0, 15.0, 20.0, 25.0] ... s, before the
        lane came free at 260.0 s
    """
    temp_store.set_safety(SafetyConfig(**PAUSE_AT_ONCE))
    monkeypatch.delitem(sim_hub.devices, "safety")
    run = _Clocked(sim_hub, monkeypatch, horizon_s=LANE_FREE_S + 60.0)
    t0 = run.t0
    plan = _plan(_constraint_waiter("Bravo", t0))
    plan.safety_check = True
    # The lane wait asks no gate here (that is the premise under test), so
    # nothing extends its deadline (#299, WP-40's `_Clock.monotonic` fix):
    # widened past LANE_FREE_S so the wait is graded on whether it asks,
    # not on racing its own timeout.
    monkeypatch.setattr(engine_mod, "_CAMERA_LANE_WAIT_S", LANE_FREE_S + 60.0)
    _lane_held_until(run, monkeypatch, t0 + LANE_FREE_S)
    unsafe = _spy_unsafe(run, monkeypatch)
    lane = _spy_lane(run, monkeypatch)
    try:
        await run.night(plan)
        assert len(lane) == 2 and lane[1] - t0 >= LANE_FREE_S, (
            f"premise: the lane wait lasted until the lane came free: "
            f"{run.rel(lane, t0)} s")
        inside = [t for t, _ctx in run.gates if t < lane[1]]
        assert inside == [], (
            f"the lane wait asked the gate with no monitor to read: gates at "
            f"{run.rel(inside[:6], t0)} ... s, before the lane came free at "
            f"{run.rel([lane[1]], t0)[0]} s")
        after = [t for t, _ctx in run.gates if t >= lane[1]]
        assert after and unsafe == [], (
            f"premise: the scheduler's gate ran after the lane, and nothing "
            f"was unsafe: gates after the lane at {run.rel(after[:3], t0)} s, "
            f"_on_unsafe {unsafe}")
    finally:
        await run.close()


# ------------------------------------------------ a cloud hold's release

#: The release case, in fake seconds after the hold's release began its
#: cooler gate (test_waits_that_watch_the_mount's RELEASE_S in): the rain
#: begins inside the gate's wait on a drifted sensor, off the reads' 5 s
#: grid, and clears while the pause's own gate still waits on the sensor.
HOLD_RAIN_AT, HOLD_CLEAR_AT = 52.5, 150.0
#: How long the sensor reads warm once the release's gate begins.
DRIFT_S = 200.0


async def test_rain_in_a_cloud_hold_s_release_gate_reaches_on_unsafe(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A cloud hold on Alpha releases when the sky clears, and the sensor
    has drifted: the release's cooler gate waits ``DRIFT_S`` for it and
    ``COOLER_STABLE_S`` more to settle, with no hold loop and no frame loop
    reading the monitor. Rain begins ``HOLD_RAIN_AT`` into that wait. The
    gate reads the monitor every ``SAFETY_PAUSE_POLL_S`` and the rain
    reaches `_on_unsafe` within one poll, with Alpha as its target. The
    pause re-checks Alpha's floor when the rain clears and leaves its
    acquisition to the release: one `_setup_target` for Alpha after the
    hold, the release's, not two (#241). The run then resumes.

    (The monitor is assigned once the hold has begun: the harness opens
    its hold on the frames' sky verdict, which stands in for a monitor only
    while none is assigned.)

    Mutant "wait reads no weather" (the hold's `_cooler_gate` call without
    ``monitor=True``, so the release's wait reads nothing, as before #452):
    RED (observed) -
        AssertionError: the rain at 52.5 s into the release's gate reached
        _on_unsafe at [] s after the gate began; the gate ran from 0.0 s to
        320.0 s
    Mutant "the pause acquires the held target" (`_wait_beside_the_monitor`
    not marking ``target`` as the acquisition behind its gate): RED
    (observed) -
        AssertionError: Alpha was acquired 2 time(s) after the hold began,
        at [324.0, 324.0] s after the release's gate began; the release's
        acquisition is the one
    """
    mon = sim_hub.devices["safety"]
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=RELEASE_S + 500.0, clears_at_s=EXP + 200.0,
                 safety=dict(on_unsafe="pause", unsafe_consecutive=1,
                             resume_safe_consecutive=1, max_pause_min=0))
    run, engine = w.run, w.engine
    a = _hold_target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    gates = _a_sensor_that_drifts_at_the_release(w, monkeypatch,
                                                 drift_s=DRIFT_S)
    _clock_the_cooling(w, monkeypatch)
    origin = w.t0 + RELEASE_S
    _monitor(run, monkeypatch, origin=origin,
             rain=(HOLD_RAIN_AT, HOLD_CLEAR_AT))
    unsafe = _spy_unsafe(run, monkeypatch)
    real_hold = engine._hold_for_clear

    async def hold_for_clear(*a_, **kw):
        sim_hub.devices["safety"] = mon      # the monitor, once it holds
        return await real_hold(*a_, **kw)

    monkeypatch.setattr(engine, "_hold_for_clear", hold_for_clear)
    setups: list[tuple[float, str]] = []
    real_setup = engine._setup_target

    async def setup_target(ti, target, *a_, **kw):
        setups.append((run.clock.t, target.name))
        return await real_setup(ti, target, *a_, **kw)

    monkeypatch.setattr(engine, "_setup_target", setup_target)
    try:
        await w.night(_cooled_hold_plan(a))
        t_h = w.hold_started()
        assert gates, "premise: the hold released into the cooler gate"
        began, ended, _watch = gates[0]
        assert abs(began - origin) < 1.0, (
            f"premise: the release's gate began {began - w.t0:.1f} s in, at "
            f"the cadence test_cloud_hold_watch's control pins")
        rain = origin + HOLD_RAIN_AT
        assert ended is not None and ended > rain, (
            f"premise: the gate was still waiting on the sensor at the rain: "
            f"ended {ended and ended - began}")
        first = [(t, who) for t, _why, who in unsafe if t >= t_h]
        assert first and rain <= first[0][0] <= rain + POLL_S + 1e-6, (
            f"the rain at {HOLD_RAIN_AT:g} s into the release's gate reached "
            f"_on_unsafe at {[round(t - began, 2) for t, _w in first]} s "
            f"after the gate began; the gate ran from 0.0 s to "
            f"{round(ended - began, 2)} s")
        assert first[0][1] == "Alpha", (
            f"the release's pause was not for the held target: {first}")
        after = [t for t, name in setups if t > t_h and name == "Alpha"]
        assert len(after) == 1, (
            f"Alpha was acquired {len(after)} time(s) after the hold began, "
            f"at {[round(t - began, 2) for t in after]} s after the release's "
            f"gate began; the release's acquisition is the one")
        released = w.released_at()
        assert released is not None and released > origin + HOLD_CLEAR_AT, (
            f"premise: the hold's release went on once the rain cleared: "
            f"released {released and released - began}")
    finally:
        await w.close()


# ------------------------------------- one ledger write per frame (#515)
#
# A WAVE-2 ENGINE DEFECT WITH NO TEST FILE OF ITS OWN in this task (S7-LEDGER
# filed it for the engine's owner, and the ledger's budget file is not this
# task's), so its cases live here, on the group harness's clocked night.
#
# The thumbnail render re-saved the whole session after every render, so a
# camera frame with pixels cost two ledger writes: a full dump and atomic
# write of every frame the project holds, to record one relative path. The
# stamp is made on the run's own session object, so while that run is live
# the next write carries it (the next frame's, or `_finalize_report`'s on
# every ending), and only a render landing after the run's final write
# saves. Not one landing in a CONTINUE of the same session, whose run loaded
# its own copy: saving the older one would roll that ledger back.

#: Frames in the night; the last one's render lands after the run's end.
THUMB_FRAMES = 4


def _thumb_plan(count: int) -> SequencePlan:
    """One target, ``count`` 30 s L frames, nothing after the run."""
    return SequencePlan(name="thumbs", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        targets=[single("Alpha", count=count)])


def _thumbs(night: Night, monkeypatch, *, gate_at: int) -> dict:
    """Pixels on every frame, and every ledger write recorded.

    The group harness's capture sets no frame of its own, so
    ``hub.last_frame`` is given one whose ``data`` is not None: each banked
    frame then spawns a render, as a real camera's does. ``to_jpeg``, the
    render's one thread of work, answers at once, except the ``gate_at``-th
    render, which waits for ``rec["gate"]``. Every `save_run_state` is
    recorded as (caller, whether the engine held a live session, frames in
    the session saved), patched on the class, not on the instance (#522).
    """
    rec: dict = {"writes": [], "renders": 0, "gate": threading.Event()}
    night.hub.last_frame = SimpleNamespace(data=b"\x00" * 16,
                                           temperature_c=None)

    def to_jpeg(data, max_width=512):
        rec["renders"] += 1
        if rec["renders"] == gate_at:
            rec["gate"].wait(20.0)
        return b"\xff\xd8thumb", 4, 4

    monkeypatch.setattr(engine_mod, "to_jpeg", to_jpeg)
    store_cls = type(session_store)
    real = store_cls.save_run_state
    engine = night.engine

    def save_run_state(self, session):
        rec["writes"].append((sys._getframe(1).f_code.co_name,
                              engine._session is not None,
                              len(session.frames)))
        return real(self, session)

    monkeypatch.setattr(store_cls, "save_run_state", save_run_state)
    return rec


async def _land(night: Night, *, leave=()) -> None:
    """Wait (real time, bounded) for every render in flight but ``leave``."""
    pending = [t for t in list(night.engine._thumb_tasks) if t not in leave]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), 10.0)


async def _each_capture_after_the_renders(night: Night, *, leave=(),
                                          stop_at: int = 0) -> None:
    """Drive the run, holding the clock at each capture's start until every
    render already spawned (but ``leave``) has landed, so each lands while
    its run is live and before the next frame. With ``stop_at``, stay held
    at that capture (1-based); else run to the end."""
    night.on_capture = lambda r: night.hold(r["t"])
    n = 0
    while await night.settle():
        n += 1
        await _land(night, leave=leave)
        if n == stop_at:
            return
        night.release()


async def test_a_frame_with_pixels_costs_one_ledger_write(group_hub,
                                                          monkeypatch):
    """Four frames with pixels. Each render but the last lands while the
    run is live, before the next frame, and writes nothing: the ledger is
    written once per banked frame, by the frame. The last render lands
    after the run has ended and its final write is made, and saves, once,
    so every frame's thumb is on disk afterwards.

    Mutant "save from the thumb" (`_render_thumb`'s save made whatever the
    run is doing, ``if live is None or live.id != session.id:`` made ``if
    True:``, as before #515): RED (observed) -
        AssertionError: a banked frame with pixels cost more than one
        ledger write: 7 writes while the run was live, for 4 frames, by
        ['_record_session_frame', '_render_thumb', '_record_session_frame',
        '_render_thumb', '_record_session_frame', '_render_thumb',
        '_record_session_frame']
    Mutant "never save from the thumb" (that condition made ``if False:``):
    RED (observed) -
        AssertionError: the render that landed after the run's final write
        was not saved: frames with no thumb on disk [4] of 4, writes after
        the end []
    """
    night = Night(group_hub, monkeypatch)
    rec = _thumbs(night, monkeypatch, gate_at=THUMB_FRAMES)
    engine = night.engine
    try:
        engine.start(_thumb_plan(THUMB_FRAMES))
        sid = engine._session.id
        await _each_capture_after_the_renders(night)
        assert engine.state.get("state") == "complete", (
            f"premise: the run completed: {engine.state.get('state')}")
        late = list(engine._thumb_tasks)
        assert len(late) == 1, (
            f"premise: the last frame's render was still out when the run "
            f"ended: {len(late)} in flight")
        rec["gate"].set()
        await _land(night)
        assert rec["renders"] == THUMB_FRAMES, (
            f"premise: every frame rendered: {rec['renders']}")
        writes = rec["writes"]
        live = [w for w in writes if w[1]]
        framed = [w for w in live if w[0] == "_record_session_frame"]
        assert len(framed) == THUMB_FRAMES and live == framed, (
            f"a banked frame with pixels cost more than one ledger write: "
            f"{len(live)} writes while the run was live, for {THUMB_FRAMES} "
            f"frames, by {[w[0] for w in live]}")
        after = [w for w in writes if not w[1]]
        stored = session_store.load(sid).frames
        missing = [i for i, f in enumerate(stored, 1)
                   if f.thumb != f"thumbs/{f.id}.jpg"]
        assert len(stored) == THUMB_FRAMES and missing == [] and after == [
            ("_render_thumb", False, THUMB_FRAMES)], (
            f"the render that landed after the run's final write was not "
            f"saved: frames with no thumb on disk {missing} of "
            f"{len(stored)}, writes after the end {after}")
    finally:
        rec["gate"].set()
        await night.close()


async def test_a_render_landing_in_a_continue_does_not_roll_it_back(
        group_hub, monkeypatch):
    """The first run's last render is still out when a CONTINUE of the same
    session starts (two more frames). It lands once the CONTINUE has banked
    its first frame and written it. It writes nothing: the CONTINUE loaded
    its own copy of the session, and saving the first run's would put the
    ledger back to four frames. Its thumb file is written all the same, and
    `_thumb_ids` serves it from the disk.

    Mutant "save unless it is this very object" (the condition made ``if
    live is not session:``): RED (observed) -
        AssertionError: the first run's last render, landing in the
        CONTINUE, rolled the session back: 4 frames on disk, 5 before it
        landed
    ("save from the thumb", above, fails here the same way.)
    """
    night = Night(group_hub, monkeypatch)
    rec = _thumbs(night, monkeypatch, gate_at=THUMB_FRAMES)
    engine = night.engine
    try:
        engine.start(_thumb_plan(THUMB_FRAMES))
        sid = engine._session.id
        await _each_capture_after_the_renders(night)
        gated = set(engine._thumb_tasks)
        assert engine.state.get("state") == "complete" and len(gated) == 1, (
            f"premise: the first run completed with its last render out: "
            f"{engine.state.get('state')}, {len(gated)} in flight")
        last = session_store.load(sid).frames[-1].id
        night.advance(60.0)
        engine.start(_thumb_plan(THUMB_FRAMES + 2),
                     session=session_store.load(sid))
        await _each_capture_after_the_renders(night, leave=gated, stop_at=2)
        before = len(session_store.load(sid).frames)
        assert before == THUMB_FRAMES + 1, (
            f"premise: the CONTINUE banked and wrote one frame: {before} on "
            f"disk")
        rec["gate"].set()
        await asyncio.wait_for(asyncio.gather(*gated), 10.0)
        after = len(session_store.load(sid).frames)
        assert after == before, (
            f"the first run's last render, landing in the CONTINUE, rolled "
            f"the session back: {after} frames on disk, {before} before it "
            f"landed")
        assert (session_store.thumbs_dir(sid) / f"{last}.jpg").exists(), (
            "premise: the render wrote its thumb file")
        await night.finish()
        stored = session_store.load(sid).frames
        assert len(stored) == THUMB_FRAMES + 2, (
            f"premise: the CONTINUE completed: {len(stored)} frames")
    finally:
        rec["gate"].set()
        await night.close()


async def test_a_render_landing_in_another_session_s_run_saves_its_own(
        group_hub, monkeypatch):
    """The first run's last render is still out when a run of ANOTHER
    session starts (a plan started fresh, not a CONTINUE). It lands once
    that run has banked its first frame and written it. The first
    session's run is over and its final write made, so nothing else will
    ever carry the stamp: the render saves its own session, once, with the
    stamp on its last frame, whatever run is live. The live run's ledger is
    not touched.

    Mutant "save only when no run is live" (the condition made ``if live
    is None:``): RED (observed) -
        AssertionError: the first run's last render, landing in another
        session's run, was not saved: its frame's thumb on disk None, the
        renders' writes []
    """
    night = Night(group_hub, monkeypatch)
    rec = _thumbs(night, monkeypatch, gate_at=THUMB_FRAMES)
    engine = night.engine
    try:
        engine.start(_thumb_plan(THUMB_FRAMES))
        sid = engine._session.id
        await _each_capture_after_the_renders(night)
        gated = set(engine._thumb_tasks)
        assert engine.state.get("state") == "complete" and len(gated) == 1, (
            f"premise: the first run completed with its last render out: "
            f"{engine.state.get('state')}, {len(gated)} in flight")
        last = session_store.load(sid).frames[-1].id
        night.advance(60.0)
        engine.start(_thumb_plan(2))
        other = engine._session.id
        assert other != sid, "premise: the second run has a session of its own"
        await _each_capture_after_the_renders(night, leave=gated, stop_at=2)
        held = len(session_store.load(other).frames)
        assert held == 1, (
            f"premise: the second run banked and wrote one frame: {held}")
        rec["gate"].set()
        await asyncio.wait_for(asyncio.gather(*gated), 10.0)
        stamp = session_store.load(sid).frames[-1].thumb
        renders = [w for w in rec["writes"] if w[0] == "_render_thumb"]
        assert stamp == f"thumbs/{last}.jpg" and renders == [
            ("_render_thumb", True, THUMB_FRAMES)], (
            f"the first run's last render, landing in another session's run, "
            f"was not saved: its frame's thumb on disk {stamp!r}, the "
            f"renders' writes {renders}")
        assert len(session_store.load(other).frames) == held, (
            "the render wrote into the live run's ledger")
        await night.finish()
    finally:
        rec["gate"].set()
        await night.close()
