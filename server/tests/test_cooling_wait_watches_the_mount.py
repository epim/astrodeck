# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The run-start cooling wait watches a target a caller left the mount
tracking (a #202 follow-up, made in the second hardening round, H2).

`start(tracking=...)` tells a run that the mount is already tracking a target:
ResumeArm re-centres one seconds before it starts the resumed run. The idle
watch (#165: the idle clock, the floor, the zenith keep-out, the flip point)
starts on it at once, but it ran only from `_wait_until`'s tick. The first
thing a run does is cool the camera (`_cool_and_wait`), and after a restart
that is minutes of a sensor walking down from ambient, with the re-centred
target tracked and nothing looking at it. So the cooling loop now takes the
same look on every probe, looking one ``COOLER_PROBE_EVERY_S`` ahead for the
flip point, the longest it sleeps between two looks.

THE COOLER GATE AFTER A CLOUD HOLD WATCHES TOO (#236), AND ONLY THAT ONE.
`_cooler_gate` also comes through `_cool_and_wait`, after a cloud hold and
after a safety pause. After a hold the held target is still tracked, and the
gate's wait on a drifted sensor, up to ``cool_timeout_s``, had nothing
looking at it, so the hold's release now asks the gate to watch; the hold
has usually spent the idle clock, so the first look stops tracking, which
costs nothing because the release re-slews. After a safety pause the pause
stopped tracking itself, and a look would say the mount is "still tracking"
when it is not, so that gate does not watch. The last case here pins the
gate's half of that; test_waits_that_watch_the_mount drives both call sites.

A COOLING WAIT THAT ENDS THE RUN ENDS THE SPELL. A stop the watch decides
during cooling is made by the idle-stop task (#216), and a run can end
straight out of cooling: `cooling_action` "skip" winds down, "abort" raises
SafetyAbort into the unsafe wind-down. The task used to be cancelled only
around `_run_scheduled`, which neither path reaches, so its retries went on
beside the wind-down, and after a skip, which does not park, they asked the
operator's idle rig for good.

THE HARNESS is test_idle_park_hold's clocked simulator. The camera settles
slowly: it reads warm until ``reach_s`` of fake time after the start and at
the setpoint from then on, so the cooling loop then holds ``COOLER_STABLE_S``
in band before it returns. The site is a fixture, never the real one.
"""
from __future__ import annotations

import asyncio
import re

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.catalog import altaz
from astrodeck.config import AppConfig, EscalationConfig, SafetyConfig
from astrodeck.sequence import SequenceEngine, SequencePlan, Target
from astrodeck.sequence import schedule

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    LAT, LON, TEARDOWN, _a_mount_that_will_not_stop, _Clocked,
    _constraint_waiter, _lst_h, _park_lines, _ra_at, _target, sim_hub,
    temp_store)

PROBE = engine_mod.COOLER_PROBE_EVERY_S
STABLE = engine_mod.COOLER_STABLE_S
COOL_TO = -5.0
#: When the slow camera first reads in band, in fake seconds after the start.
REACH_S = 200.0


def _cooled_plan(*targets: Target, flip: bool = False,
                 cool_timeout_s: int | None = None) -> SequencePlan:
    return SequencePlan(name="cooling", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=flip,
                        safety_check=False, targets=list(targets),
                        cool_to=COOL_TO, cool_timeout_s=cool_timeout_s)


def _a_camera_that_settles_slowly(run: _Clocked, monkeypatch, *,
                                  reach_s: float | None = REACH_S) -> None:
    """Warm until ``reach_s`` after the night starts, at the setpoint after;
    ``None`` never reaches it."""
    cam = run.hub.devices["camera"]
    assert getattr(cam, "can_cool", False), "premise: the sim camera cools"

    async def get_temperature():
        if reach_s is not None and run.clock.t >= run.t0 + reach_s:
            return COOL_TO
        return 15.0

    monkeypatch.setattr(cam, "get_temperature", get_temperature)


def _spy_cooling(run: _Clocked, monkeypatch) -> list[float]:
    """The fake time the run-start cooling wait returned or raised."""
    ended: list[float] = []
    real = run.engine._cool_and_wait

    async def cool_and_wait(*a, **kw):
        try:
            return await real(*a, **kw)
        finally:
            ended.append(run.clock.t)

    monkeypatch.setattr(run.engine, "_cool_and_wait", cool_and_wait)
    return ended


async def _left_tracking(run: _Clocked, target: Target) -> None:
    """What ResumeArm's re-centre did: the mount on ``target``, tracking.
    Then the harness's records are cleared, so only the run's calls count."""
    tel = run.hub.devices["telescope"]
    await tel.slew(target.ra_hours, target.dec_deg)
    await tel.set_tracking(True)
    run.slews.clear()
    run.tracking_calls.clear()
    run.tracking_off.clear()


def _recentred(run: _Clocked, temp_store, reason: str
               ) -> tuple[Target, float, bool]:
    """The target the mount was left on, and when its hazard comes: the idle
    clock, the floor or the flip point, all inside the cooling wait. Returns
    (target, fake time of the hazard, whether the plan flips)."""
    t0 = run.t0
    if reason == "the idle clock":
        # High, east, flip hours off: only the idle clock can stop it.
        return _target("Alpha", _ra_at(-3.0, t0), 40.0), t0 + TEARDOWN, False
    if reason == "the floor":
        # West and setting through a floor set to its own altitude at the
        # crossing, 42 s in: the probes at +40 and +45 straddle it.
        a = _target("Alpha", _ra_at(+3.0, t0), 0.0)
        t_cross = t0 + 42.0
        floor = altaz(a.ra_hours, a.dec_deg, LAT, LON, t_cross)[0]
        assert altaz(a.ra_hours, a.dec_deg, LAT, LON, t0)[0] > floor, "premise"
        temp_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=floor))
        return a, t_cross, False
    # The flip point 42 s in: the probe at +40 sees it 2 s ahead, the one at
    # +35 sees it 7 s ahead, more than one probe gap.
    lead_h = schedule.MERIDIAN_FLIP_LEAD_MIN / 60.0
    t_flip = t0 + 42.0
    a = _target("Alpha", (_lst_h(t_flip) + lead_h) % 24.0, 20.0)
    return a, t_flip, True


@pytest.mark.parametrize("reason", ["the idle clock", "the floor",
                                    "the flip point"])
async def test_the_cooling_wait_park_holds_a_recentred_target_at_that_probe(
        sim_hub, temp_store, monkeypatch, bus_lines, reason):
    """ResumeArm left the mount tracking Alpha, and the resumed run opens on
    a camera that takes REACH_S to reach its setpoint and COOLER_STABLE_S
    more to settle. Alpha reaches its hazard well inside that wait: the idle
    clock's WAIT_TEARDOWN_S, the mount's floor 42 s in, or the plan's flip
    point 42 s in. It is park-held at the first cooling probe that sees it
    (the flip point one probe ahead), not when the wait ends.

    Mutant "no idle watch in the cooling wait" (the `_idle_hold_tick` call in
    `_cool_and_wait` deleted): RED, all three, park-held only by the wait
    loop once the cooling is over (observed) -
        [the idle clock] AssertionError: the idle clock came 120.0 s into
        the cooling wait but Alpha was park-held at 320.0 s, after the
        cooling wait ended at 320.0 s
        [the floor] AssertionError: the floor came 42.0 s into the cooling
        wait but Alpha was park-held at 320.0 s, after the cooling wait ended
        at 320.0 s
        [the flip point] AssertionError: the flip point came 42.0 s into the
        cooling wait but Alpha was park-held at 320.0 s, after the cooling
        wait ended at 320.0 s
    Mutant "no look-ahead in the cooling wait" (the cooling loop's look
    passes ``ahead_s=0.0``): RED, the flip point only, one probe late
    (observed) -
        AssertionError: the flip point came 42.0 s into the cooling wait but
        Alpha was park-held at 45.0 s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    alpha, t_hazard, flip = _recentred(run, temp_store, reason)
    bravo = _constraint_waiter("Bravo", t0)
    _a_camera_that_settles_slowly(run, monkeypatch)
    cooled = _spy_cooling(run, monkeypatch)
    await _left_tracking(run, alpha)
    try:
        await run.night(_cooled_plan(bravo, flip=flip), tracking=alpha)
        assert cooled and cooled[0] - t0 >= REACH_S + STABLE, (
            f"premise: the cooling wait lasted past REACH_S + COOLER_STABLE_S: "
            f"{run.rel(cooled, t0)} s")
        assert t_hazard < cooled[0] - 2 * PROBE, (
            "premise: the hazard comes well inside the cooling wait")
        assert run.slews == [], (
            f"premise: the run set nothing up: slews {run.rel(run.slews, t0)}")
        offs = run.tracking_off
        assert offs, (
            f"{reason}: Alpha was never park-held; set_tracking(False) at "
            f"{run.rel(offs, t0)} s")
        # The probe that sees it: the first at or after the floor or the idle
        # clock, the last one before the flip point (it looks a probe ahead).
        if reason == "the flip point":
            ok = t_hazard - PROBE < offs[0] <= t_hazard
        else:
            ok = t_hazard <= offs[0] < t_hazard + PROBE
        assert ok, (
            f"{reason} came {t_hazard - t0:.1f} s into the cooling wait but "
            f"Alpha was park-held at {offs[0] - t0:.1f} s"
            + (f", after the cooling wait ended at {cooled[0] - t0:.1f} s"
               if offs[0] >= cooled[0] else ""))
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Alpha" in lines[0], lines
        assert not re.search(r"\d", lines[0]), lines[0]
    finally:
        await run.close()


async def test_the_flip_look_ahead_is_the_cooling_loops_own_gap(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The cooling loop looks one COOLER_PROBE_EVERY_S ahead for the flip
    point, the longest it sleeps between two looks, and not `_wait_until`'s
    SCHEDULE_WAIT_STEP_S. Both are 5 s today, so the flip case above cannot
    tell them apart: a look-ahead of the wait step passes it. Here the probe
    gap is stretched to three wait steps, 15 s, so the looks come at +0,
    +15, +30, +45, +60, and the flip point is put at +57: the look at +45
    sees it 12 s ahead, which only a look-ahead of a whole probe gap covers.
    Any shorter one first sees it at +60, 3 s past it.

    Mutant "cooling look-ahead is the wait step" (the cooling loop's look
    calls `_idle_hold_tick()` with no ``ahead_s``, so its default,
    SCHEDULE_WAIT_STEP_S): RED, one probe late (observed) -
        AssertionError: the flip point came 57.0 s into the cooling wait,
        and the looks are 15 s apart, but Alpha was park-held at 60.0 s
    """
    gap = 3 * engine_mod.SCHEDULE_WAIT_STEP_S
    monkeypatch.setattr(engine_mod, "COOLER_PROBE_EVERY_S", gap)
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    lead_h = schedule.MERIDIAN_FLIP_LEAD_MIN / 60.0
    t_flip = t0 + 57.0
    alpha = _target("Alpha", (_lst_h(t_flip) + lead_h) % 24.0, 20.0)
    bravo = _constraint_waiter("Bravo", t0)
    _a_camera_that_settles_slowly(run, monkeypatch)
    cooled = _spy_cooling(run, monkeypatch)
    await _left_tracking(run, alpha)
    try:
        await run.night(_cooled_plan(bravo, flip=True), tracking=alpha)
        assert cooled and t_flip < cooled[0] - 2 * gap, (
            f"premise: the flip point comes well inside the cooling wait: "
            f"{run.rel(cooled, t0)} s")
        assert run.slews == [], (
            f"premise: the run set nothing up: slews {run.rel(run.slews, t0)}")
        offs = run.tracking_off
        assert offs, "premise: Alpha was park-held at all"
        assert t_flip - gap < offs[0] <= t_flip, (
            f"the flip point came {t_flip - t0:.1f} s into the cooling wait, "
            f"and the looks are {gap:.0f} s apart, but Alpha was park-held "
            f"at {offs[0] - t0:.1f} s")
        assert len(_park_lines(bus_lines)) == 1, _park_lines(bus_lines)
    finally:
        await run.close()


async def test_control_a_start_without_tracking_stops_nothing_while_cooling(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same slow camera and the mount left tracking Alpha by
    hand, but `start()` is not told: the mount is the operator's, and
    neither the cooling wait nor the wait after it stops it, however long
    it tracks.

    Mutant "the cooling wait watches the plan's target" (the cooling loop's
    look asks `_idle_hold_reason` of ``self.plan.targets[0]`` and park-holds
    on its answer, instead of going through `_idle_hold_tick` and its
    tracked-target guard): RED (observed) -
        AssertionError: a run not told what the mount is tracking stopped it
        at [0.0] s: ['Bravo: nothing has been shot for a while and the mount
        is still tracking it — stopping tracking until the next target is set
        up']
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    alpha = _target("Alpha", _ra_at(-3.0, t0), 40.0)
    bravo = _constraint_waiter("Bravo", t0)
    _a_camera_that_settles_slowly(run, monkeypatch)
    cooled = _spy_cooling(run, monkeypatch)
    await _left_tracking(run, alpha)
    try:
        await run.night(_cooled_plan(bravo))
        assert cooled and cooled[0] - t0 >= REACH_S + STABLE, (
            f"premise: a long cooling wait: {run.rel(cooled, t0)} s")
        assert run.tracking_off == [], (
            f"a run not told what the mount is tracking stopped it at "
            f"{run.rel(run.tracking_off, t0)} s: {_park_lines(bus_lines)}")
        assert run.tracking() is True
    finally:
        await run.close()


@pytest.mark.parametrize("action", ["skip", "abort"])
async def test_a_run_that_ends_in_its_cooling_wait_leaves_no_retry_behind(
        sim_hub, temp_store, monkeypatch, bus_lines, action):
    """The cooling wait decides a stop on the idle clock, the mount does not
    take it, and the idle-stop task retries. Then the camera never reaches
    its setpoint and ``require_cooling`` ends the run from inside the cooling
    wait: ``skip`` through its own wind-down, ``abort`` through SafetyAbort's.
    When the wind-down begins the task is over, cancelled and awaited, as
    `_setup_target` has it before its slew: the wind-down talks to the mount
    (the unsafe one parks it), and nothing may still be asking for a stop
    beside it. And from then on nothing asks: after the run the rig is the
    operator's.

    Both, because each action shows it one way. ``skip`` does not park, so
    a task left alive asks the operator's idle mount for good. ``abort``
    parks, and the park is what ends a task left alive: its read-back finds
    the mount stopped. Only the first half sees that one.

    AFTER THE WIND-DOWN BEGINS IS AN EVENT, the index into the harness's
    ``tracking_calls`` when `_wind_down` is entered, not a fake time (#223).

    Mutant "the spell ends only with the scheduler" (the ``try``/``finally``
    that cancels the task put back around `_run_scheduled` alone, as before
    the cooling wait watched): RED, both actions (observed) -
        [abort] AssertionError: the idle-stop task was still asking for the
        stop when the cooling wait ended the run (abort) and the wind-down
        began to talk to the mount: asks at [-280.0, -190.0, -100.0, -10.0] s
        [skip] AssertionError: the idle-stop task was still asking for the
        stop when the cooling wait ended the run (skip) and the wind-down
        began to talk to the mount: asks at [-280.0, -190.0, -100.0, -10.0] s
    Before the first half was written, the same mutant went RED for ``skip``
    only, on the second half: a task asking the operator's idle mount after
    the run (observed) -
        AssertionError: the idle-stop task went on asking after the cooling
        wait ended the run (skip): set_tracking(False) at [(80.0, 'retry'),
        (170.0, 'retry'), (260.0, 'retry'), (350.0, 'retry')] s after the
        wind-down began
    and ``abort`` goes green: its park is what ends the task.
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=4 * 3600.0)
    t0 = run.t0
    alpha = _target("Alpha", _ra_at(-3.0, t0), 40.0)
    bravo = _constraint_waiter("Bravo", t0)
    temp_store.set_escalation(EscalationConfig(require_cooling=True,
                                               cooling_action=action))
    _a_camera_that_settles_slowly(run, monkeypatch, reach_s=None)
    _a_mount_that_will_not_stop(run, readback="still tracking")
    await _left_tracking(run, alpha)
    #: (index into tracking_calls, fake time, the idle-stop task still
    #: alive) when the wind-down began
    wound: list[tuple[int, float, bool]] = []
    real_wind = run.engine._wind_down

    async def wind_down(*a, **kw):
        task = run.engine._idle_stop_task
        wound.append((len(run.tracking_calls), run.clock.t,
                      task is not None and not task.done()))
        return await real_wind(*a, **kw)

    monkeypatch.setattr(run.engine, "_wind_down", wind_down)
    run.engine.start(_cooled_plan(bravo, cool_timeout_s=400), tracking=alpha)
    loop = asyncio.get_running_loop()
    end = loop.time() + 60.0
    while run.engine.running and loop.time() < end:
        await run._real_sleep(0.01)
    try:
        assert not run.engine.running and not run.frozen.is_set(), (
            f"premise: the run ended in its cooling wait, before the "
            f"horizon: {run.engine.state}")
        want = "cooling_skip" if action == "skip" else "unsafe"
        assert run.engine.state.get("end_reason") == want, (
            f"premise: {action} ended it: {run.engine.state}")
        assert len(wound) == 1, f"premise: one wind-down: {wound}"
        at, began, alive = wound[0]
        before = [t for t, on, who in run.tracking_calls[:at]
                  if not on and who == "retry"]
        assert len(before) >= 2, (
            f"premise: the task made the stop and retried it during the "
            f"cooling wait: {run.rel(before, t0)} s")
        assert not alive, (
            f"the idle-stop task was still asking for the stop when the "
            f"cooling wait ended the run ({action}) and the wind-down began "
            f"to talk to the mount: asks at {run.rel(before, began)} s")
        # With the engine idle the driver would still advance a live task,
        # the only engine task left: give it the real time to show itself.
        await run._real_sleep(0.3)
        after = [(round(t - began, 1), who)
                 for t, on, who in run.tracking_calls[at:]
                 if not on and who == "retry"]
        assert after == [], (
            f"the idle-stop task went on asking after the cooling wait ended "
            f"the run ({action}): set_tracking(False) at {after[:4]} s after "
            f"the wind-down began")
    finally:
        await run.close()


@pytest.mark.parametrize("gate", ["after a hold", "after a safety pause"])
async def test_the_cooler_gate_watches_only_when_asked(sim_hub, monkeypatch,
                                                       gate):
    """The gate's half of #236, FLIPPED from what H2 pinned
    (``test_the_cooler_gate_after_a_hold_does_not_watch``, which asserted
    that no gate watched). `_cooler_gate` runs the same `_cool_and_wait`
    after a cloud hold and after a safety pause, with the last target still
    in ``_tracked_target``, the latch open and its idle clock long run out.
    Asked to watch, as the hold's release asks it, it takes the idle look:
    the mount is stopped once, by the idle-stop task, and the latch is
    closed. Not asked, as the safety pause's release is not, it stops
    nothing and leaves the latch open for the `_setup_target` that follows.
    Which call site asks is test_waits_that_watch_the_mount's.

    A direct call, with the cooling constants cut to real hundredths of a
    second: the camera reads warm for its first probes, then settles.

    Mutant "the gate does not pass watch on" (`_cooler_gate` calls
    `_cool_and_wait` without ``watch=``): RED, after a hold (observed) -
        AssertionError: the cooler gate after a hold did not watch the tracked
        target: set_tracking(False) [], latch open
        and test_waits_that_watch_the_mount's hold case with it.
    Mutant "the gate always watches" (`_cooler_gate` passes ``watch=True``
    whatever it is asked, H2's own recorded mutant): RED, after a safety
    pause (observed) -
        AssertionError: the cooler gate after a safety pause watched:
        set_tracking(False) ['idle-stop-retry'], latch closed
        and test_waits_that_watch_the_mount's safety-pause control with it.
    """
    watch = gate == "after a hold"
    monkeypatch.setattr(engine_mod, "COOLER_PROBE_EVERY_S", 0.01)
    monkeypatch.setattr(engine_mod, "COOLER_STABLE_S", 0.05)
    engine = SequenceEngine(sim_hub)
    alpha = _target("Alpha", _ra_at(-3.0, engine_mod.time.time()), 40.0)
    engine.plan = _cooled_plan(alpha)
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    tel = sim_hub.devices["telescope"]
    await tel.slew(alpha.ra_hours, alpha.dec_deg)
    await tel.set_tracking(True)
    engine._tracked_target = alpha
    engine._idle_since = engine_mod.time.time() - 10 * TEARDOWN
    engine._idle_hold_open = True
    assert await engine._idle_hold_reason(alpha), (
        "premise: the idle clock has run out, so a watch would stop it")
    cam = sim_hub.devices["camera"]
    probes = {"n": 0}

    async def get_temperature():
        probes["n"] += 1
        return 15.0 if probes["n"] <= 3 else COOL_TO

    monkeypatch.setattr(cam, "get_temperature", get_temperature)
    stops: list[str] = []
    real_set = tel.set_tracking

    async def set_tracking(on):
        if not on:
            stops.append(asyncio.current_task().get_name())
        await real_set(on)

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    why = ("resumed after 20 min of cloud" if watch
           else "resumed after a safety pause")
    try:
        await asyncio.wait_for(engine._cooler_gate(why, watch=watch), 10.0)
        await asyncio.sleep(0.05)         # a task it started would have asked
        assert probes["n"] > 3, "premise: the gate waited on the sensor"
        if watch:
            assert stops == ["idle-stop-retry"] and not engine._idle_hold_open, (
                f"the cooler gate after a hold did not watch the tracked "
                f"target: set_tracking(False) {stops}, latch "
                f"{'open' if engine._idle_hold_open else 'closed'}")
            assert tel.rig.tracking is False
        else:
            assert stops == [] and engine._idle_hold_open, (
                f"the cooler gate after a safety pause watched: "
                f"set_tracking(False) {stops}, latch "
                f"{'open' if engine._idle_hold_open else 'closed'}")
            assert tel.rig.tracking is True
    finally:
        await engine._cancel_idle_stop_retry()
