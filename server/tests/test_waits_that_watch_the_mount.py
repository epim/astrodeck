# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The two waits #236 left unwatched now watch the mount.

The idle watch (#165: the idle clock, the floor, the zenith keep-out, the
flip point) looks at a mount that is tracking with nothing else looking at
it. H2 made the run-start cooling wait take that look (#202); it left two
waits that did not, and this is them:

* THE CAMERA-LANE WAIT AT RUN START (`_await_camera_lane`, #44). The very
  first thing a run does, before the cooling wait: up to
  ``_CAMERA_LANE_WAIT_S`` for a camera operation already in flight, with the
  target `start(tracking=...)` handed the run tracked and nothing looking.
  Every poll now takes the look, seeking the flip point one
  ``_CAMERA_LANE_POLL_S`` ahead.
* THE COOLER GATE AFTER A CLOUD HOLD (`_cooler_gate`). A hold keeps its
  target tracked by design, and once the sky clears the gate is all that
  stands between the release and `_setup_target`: up to ``cool_timeout_s``
  on a drifted sensor, with no hold loop and no frame loop. The gate now
  watches when the hold releases. The hold has usually spent the idle clock
  by then, so the first look stops tracking; that costs nothing, because the
  release re-slews. The SAFETY PAUSE's gate still does not watch: the pause
  stopped tracking itself.

THE HARNESS is test_idle_park_hold's clocked simulator; the hold cases use
test_cloud_hold_watch's night with a cloud hold in it, whose sky is a script.
The camera lane is held by a double whose ``locked()`` answers on the fake
clock. The site is a fixture, never the real one, and nothing here prints a
mount's altitude or azimuth.
"""
from __future__ import annotations

import asyncio
import re

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.catalog import altaz
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.sequence import SequenceEngine, SequencePlan, Target
from astrodeck.sequence import schedule

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    LAT, LON, TEARDOWN, _Clocked, _constraint_waiter, _lst_h, _park_lines,
    _plan, _ra_at, _target, sim_hub, temp_store)
from test_cooling_wait_watches_the_mount import COOL_TO, _left_tracking
from test_cloud_hold_watch import EXP, _Watched
from test_cloud_hold_watch import _plan as _hold_plan
from test_cloud_hold_watch import _target as _hold_target

pytestmark = pytest.mark.asyncio

LANE_POLL = engine_mod._CAMERA_LANE_POLL_S
PROBE = engine_mod.COOLER_PROBE_EVERY_S
STABLE = engine_mod.COOLER_STABLE_S
LEAD_S = 60.0 * schedule.MERIDIAN_FLIP_LEAD_MIN
#: When the held camera lane comes free, in fake seconds after the start:
#: after the hazard, before the idle clock's WAIT_TEARDOWN_S.
LANE_FREE_S = 90.0
#: When the hazard comes, in fake seconds after the start. Off the lane
#: wait's 0.25 s grid, so the look that meets it is unambiguous: the flip
#: point is seen by the look at +42.0 (0.1 s ahead, inside one poll) and not
#: by the one at +41.75 (0.35 s ahead); the floor is first below at +42.25.
HAZARD_S = 42.1


# ------------------------------------------------------ the camera-lane wait

def _lane_held_until(run: _Clocked, monkeypatch, t_free: float) -> None:
    """Someone else's camera operation holds the lane until ``t_free`` of
    fake time. Only ``locked()`` is answered on the clock: the plan here
    takes no exposure while the lane is held, and after it nothing asks."""
    lock = run.hub._capture_lock
    monkeypatch.setattr(lock, "locked", lambda: run.clock.t < t_free)
    monkeypatch.setattr(run.hub, "_capture_busy", "plate solve",
                        raising=False)


def _handed_over(run: _Clocked, temp_store, reason: str
                 ) -> tuple[Target, float, bool]:
    """The target ResumeArm left tracking, and the fake time of its hazard,
    ``HAZARD_S`` in, as test_cooling_wait_watches_the_mount places them.
    Returns (target, hazard time, whether the plan flips)."""
    t0 = run.t0
    t_hazard = t0 + HAZARD_S
    if reason == "the floor":
        # West and setting through a floor set to its own altitude at the
        # hazard time.
        a = _target("Alpha", _ra_at(+3.0, t0), 0.0)
        floor = altaz(a.ra_hours, a.dec_deg, LAT, LON, t_hazard)[0]
        assert altaz(a.ra_hours, a.dec_deg, LAT, LON, t0)[0] > floor, "premise"
        temp_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=floor))
        return a, t_hazard, False
    # The plan's flip point at the hazard time, by the engine's arithmetic:
    # the hour angle reaches minus the lead exactly then.
    a = _target("Alpha", (_lst_h(t_hazard) + LEAD_S / 3600.0) % 24.0, 20.0)
    return a, t_hazard, True


@pytest.mark.parametrize("reason", ["the flip point", "the floor"])
async def test_the_camera_lane_wait_stops_a_handed_over_target_at_its_hazard(
        sim_hub, temp_store, monkeypatch, bus_lines, reason):
    """ResumeArm left the mount tracking Alpha and started the resumed run,
    and a plate solve holds the camera lane for the run's first 90 s. Alpha
    reaches its hazard 42.1 s in, inside that wait. It is stopped at the
    lane poll that meets it (the flip point one poll ahead), during the
    wait, and not at the scheduler's first tick after it.

    Mutant "no idle look in the camera-lane wait" (the `_idle_hold_tick`
    call in `_await_camera_lane` deleted): RED, both (observed) -
        [the flip point] AssertionError: the flip point came 42.10 s into the
        camera-lane wait but Alpha was stopped at 90.00 s, after the lane wait
        ended at 90.00 s
        [the floor] AssertionError: the floor came 42.10 s into the camera-lane
        wait but Alpha was stopped at 90.00 s, after the lane wait ended at
        90.00 s
    Mutant "no look-ahead in the camera-lane wait" (the lane wait's look
    passes ``ahead_s=0.0``): RED, the flip point only, one poll late
    (observed) -
        AssertionError: the flip point came 42.10 s into the camera-lane wait
        but Alpha was stopped at 42.25 s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    alpha, t_hazard, flip = _handed_over(run, temp_store, reason)
    bravo = _constraint_waiter("Bravo", t0)
    _lane_held_until(run, monkeypatch, t0 + LANE_FREE_S)
    lane: list[float] = []
    real_lane = run.engine._await_camera_lane

    async def await_camera_lane():
        try:
            return await real_lane()
        finally:
            lane.append(run.clock.t)

    monkeypatch.setattr(run.engine, "_await_camera_lane", await_camera_lane)
    await _left_tracking(run, alpha)
    try:
        await run.night(_plan(bravo, flip=flip), tracking=alpha)
        assert lane and lane[0] - t0 >= LANE_FREE_S, (
            f"premise: the lane wait lasted until the lane came free: "
            f"{run.rel(lane, t0)} s")
        assert run.slews == [], (
            f"premise: the run set nothing up: slews {run.rel(run.slews, t0)}")
        offs = run.tracking_off
        assert offs, (
            f"{reason}: Alpha was never stopped; set_tracking(False) at "
            f"{run.rel(offs, t0)} s")
        if reason == "the flip point":
            ok = t_hazard - LANE_POLL < offs[0] <= t_hazard
        else:
            ok = t_hazard <= offs[0] < t_hazard + LANE_POLL
        assert ok, (
            f"{reason} came {t_hazard - t0:.2f} s into the camera-lane wait "
            f"but Alpha was stopped at {offs[0] - t0:.2f} s"
            + (f", after the lane wait ended at {lane[0] - t0:.2f} s"
               if offs[0] >= lane[0] else ""))
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Alpha" in lines[0], lines
        assert not re.search(r"\d", lines[0]), lines[0]
    finally:
        await run.close()


async def test_control_a_lane_wait_with_nothing_handed_over_stops_nothing(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same held lane and the mount left tracking Alpha by hand,
    through its flip point, but `start()` is not told: the mount is the
    operator's, and the lane wait stops nothing.

    Mutant "the lane wait watches the plan's target" (the lane wait's look
    asks `_idle_hold_reason` of ``self.plan.targets[0]`` and park-holds on
    its answer, instead of going through `_idle_hold_tick` and its
    tracked-target guard): RED (observed) -
        AssertionError: a run not told what the mount is tracking stopped it at
        [0.0] s: ['Bravo: nothing has been shot for a while and the mount is
        still tracking it — stopping tracking until the next target is set up']
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=LANE_FREE_S + 20.0)
    t0 = run.t0
    alpha, _t_hazard, _flip = _handed_over(run, temp_store, "the flip point")
    bravo = _constraint_waiter("Bravo", t0)
    _lane_held_until(run, monkeypatch, t0 + LANE_FREE_S)
    await _left_tracking(run, alpha)
    try:
        await run.night(_plan(bravo, flip=True))
        assert run.tracking_off == [], (
            f"a run not told what the mount is tracking stopped it at "
            f"{run.rel(run.tracking_off, t0)} s: {_park_lines(bus_lines)}")
        assert run.tracking() is True
    finally:
        await run.close()


# ------------------------------------------------ the cooler gate after a hold

#: How long the sensor reads warm once the hold releases, in fake seconds;
#: the gate then holds COOLER_STABLE_S more in band.
DRIFT_S = 200.0
#: The hold's cadence, from test_cloud_hold_watch's control: the hold begins
#: at the second frame boundary, EXP in, the sky clears 200 s later, and the
#: hold releases on the second clear check, 450 s into the hold.
RELEASE_S = EXP + 450.0
#: Alpha's flip point, in fake seconds after the start: inside the gate's
#: wait, 120 s after the release, and 150 s after the hold's last look before
#: its release check, well outside the flip gate's reach from there.
FLIP_S = RELEASE_S + 120.0


def _a_sensor_that_drifts_at_the_release(w: _Watched, monkeypatch, *,
                                         drift_s: float) -> list[tuple]:
    """In band from the run's start; warm for ``drift_s`` from the moment the
    cooler gate after the hold begins, then back in band. Returns the
    (start, end) fake times of every cooler gate, the end None while it
    runs."""
    cam = w.hub.devices["camera"]
    assert getattr(cam, "can_cool", False), "premise: the sim camera cools"
    gates: list[list] = []

    async def get_temperature():
        if gates and gates[0][1] is None \
                and w.run.clock.t < gates[0][0] + drift_s:
            return 15.0
        return COOL_TO

    real_gate = w.engine._cooler_gate

    async def cooler_gate(why, **kw):
        rec = [w.run.clock.t, None, kw.get("watch")]
        gates.append(rec)
        try:
            return await real_gate(why, **kw)
        finally:
            rec[1] = w.run.clock.t

    monkeypatch.setattr(cam, "get_temperature", get_temperature)
    monkeypatch.setattr(w.engine, "_cooler_gate", cooler_gate)
    return gates


def _cooled_hold_plan(*targets) -> SequencePlan:
    plan = _hold_plan(*targets)
    plan.cool_to = COOL_TO
    plan.cool_timeout_s = 900
    return plan


async def test_the_cooler_gate_after_a_hold_stops_a_target_reaching_its_flip_point(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A cloud hold on Alpha releases when the sky clears, and the sensor has
    drifted: the cooler gate waits DRIFT_S for it to come back and
    COOLER_STABLE_S more to settle. Alpha reaches its flip point 120 s into
    that wait, still tracked, with neither the hold loop nor the frame loop
    looking. The gate's wait must stop tracking, before the flip point; here
    it does so at its first look, because the hold spent the idle clock. The
    night stops in the gate's wait, past the flip point, so nothing after it
    (the setup's re-slew) is graded.

    Mutant "the cooler gate after a hold does not watch" (the hold's
    `_cooler_gate` call without ``watch=True``): RED (observed) -
        AssertionError: the mount tracked Alpha through its flip point 120 s
        into the cooler gate's wait, with nothing looking: set_tracking(False)
        at [] s into the gate
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=FLIP_S + 60.0,
                 clears_at_s=EXP + 200.0)
    t_flip = w.t0 + FLIP_S
    a = _hold_target("Alpha", (_lst_h(t_flip) + LEAD_S / 3600.0) % 24.0,
                     20.0)
    gates = _a_sensor_that_drifts_at_the_release(w, monkeypatch,
                                                 drift_s=DRIFT_S)
    try:
        await w.night(_cooled_hold_plan(a))
        t_h = w.hold_started()
        assert gates, "premise: the hold released into the cooler gate"
        began, ended, _watch = gates[0]
        assert abs((began - w.t0) - RELEASE_S) < 1.0, (
            f"premise: the hold released {began - t_h:.1f} s into the hold, "
            f"at the cadence test_cloud_hold_watch's control pins")
        assert ended is None and t_flip < w.run.horizon, (
            f"premise: the gate was still waiting on the sensor at the "
            f"horizon, past the flip point: ended "
            f"{ended and ended - began}")
        before = [t for t in w.stops(t_h) if t < began]
        assert before == [], (
            f"premise: the hold itself stopped nothing: {w.rel(before, t_h)}")
        stops = [t for t in w.stops(began)]
        assert stops and stops[0] <= t_flip, (
            f"the mount tracked Alpha through its flip point "
            f"{t_flip - began:.0f} s into the cooler gate's wait, with "
            f"nothing looking: set_tracking(False) at "
            f"{w.rel(stops, began)} s into the gate")
        assert stops[0] - began < PROBE, (
            f"the first look after a hold that spent the idle clock stops "
            f"tracking: it came {stops[0] - began:.1f} s into the gate")
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Alpha" in lines[0], lines
    finally:
        await w.close()


async def test_control_a_sensor_already_in_its_band_adds_no_wait(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same hold and release, the sensor never out of its band:
    the watched gate takes no time at all, one probe and no sleep, and the
    release goes straight on to the setup, at the instant the unwatched gate
    released it (test_cloud_hold_watch's control).

    Mutant "the watched gate waits a probe first" (`_cooler_gate` sleeps
    one COOLER_PROBE_EVERY_S before its wait when asked to watch): RED
    (observed) -
        AssertionError: a sensor already in its band held the watched cooler
        gate for 5.0 s
        A mutant that sleeps at the top of `_cool_and_wait`'s watched pass
        delays the run-start cooling wait too, which moves the whole hold past
        the horizon: it goes red on the premise that the gate was reached, not
        on this claim, so it is not the one quoted.
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=RELEASE_S + 30.0,
                 clears_at_s=EXP + 200.0)
    a = _hold_target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    gates = _a_sensor_that_drifts_at_the_release(w, monkeypatch, drift_s=0.0)
    try:
        await w.night(_cooled_hold_plan(a))
        assert gates and gates[0][2] is True, (
            f"premise: the hold's gate was asked to watch: {gates}")
        began, ended, _watch = gates[0]
        assert ended is not None and ended - began == 0.0, (
            f"a sensor already in its band held the watched cooler gate for "
            f"{(ended - began) if ended is not None else 'the rest of the'}"
            f" s")
        released = w.released_at()
        assert released is not None and released == ended, (
            f"the release did not go straight on from the gate: gate ended "
            f"{ended and ended - w.t0}, released {released and released - w.t0}")
    finally:
        await w.close()


# ------------------------------------------ the safety pause's gate does not

async def test_control_the_safety_pause_gate_does_not_watch(sim_hub,
                                                            monkeypatch,
                                                            bus_lines):
    """CONTROL. The safety pause stops tracking itself as it opens
    (`_park_hold`), and its release goes through the same cooler gate, on a
    drifted sensor here, before `_setup_target`. Its gate does not watch:
    the pause's own stop is the only ``set_tracking(False)``, no idle stop is
    decided, no line says the mount is "still tracking" (it is not), and the
    latch is left CLOSED by the pause's close-out.

    RE-PINNED FOR H4 (#530). This case said "the latch is left open for the
    setup" and asserted ``engine._idle_hold_open`` after the pause. Since
    H4-ENG-C a pause whose stop was read back as confirmed closes the latch
    that stop satisfied, before it re-acquires, and it is the REAL
    `_setup_target` that re-opens it when a target is acquired. Here the
    setup is a spy, so nothing re-opens it, and the pause's close-out is what
    the last line sees. RED under H4-ENG-C's mutant "the pause leaves the
    latch open" (engine.py: the close-out's ``if not unconfirmed:`` made
    ``if False:``), re-run by the H4 integration. Observed:

        assert (not True)
         +  where True = <astrodeck.sequence.engine.SequenceEngine object at
         0x0000018F39619C70>._idle_hold_open

    The real `_park_hold_pause`, driven directly: every safety read is safe,
    the setup is a spy, and the pause and cooling cadences are cut to real
    hundredths of a second.

    Mutant "the safety pause's gate watches too" (the pause's
    `_cooler_gate` call given ``watch=True``): RED (observed) -
        AssertionError: the safety pause's cooler gate watched a mount the
        pause had already stopped: set_tracking(False) 2 time(s) (['Task-4',
        'idle-stop-retry']), lines ['Alpha: nothing has been shot for a while
        and the mount is still tracking it — stopping tracking until the next
        target is set up']
        (the first name is the test's own task, however asyncio numbered it.)
    """
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.01)
    monkeypatch.setattr(engine_mod, "COOLER_PROBE_EVERY_S", 0.01)
    monkeypatch.setattr(engine_mod, "COOLER_STABLE_S", 0.05)
    engine = SequenceEngine(sim_hub)
    alpha = _target("Alpha", _ra_at(-3.0, engine_mod.time.time()), 40.0)
    plan = _plan(alpha)
    plan.cool_to = COOL_TO
    engine.plan = plan
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    tel = sim_hub.devices["telescope"]
    await tel.slew(alpha.ra_hours, alpha.dec_deg)
    await tel.set_tracking(True)
    engine._tracked_target = alpha
    engine._idle_since = engine_mod.time.time() - 10 * TEARDOWN
    engine._idle_hold_open = True
    assert await engine._idle_hold_reason(alpha), (
        "premise: the idle clock has run out, so a watch would stop it")

    class _Safe:
        stale = False
        is_safe = True

    async def read_safety():
        return _Safe()

    setups: list[str] = []

    async def setup_target(ti, target):
        setups.append(target.name)

    cam = sim_hub.devices["camera"]
    probes = {"n": 0}

    async def get_temperature():
        probes["n"] += 1
        return 15.0 if probes["n"] <= 3 else COOL_TO

    stops: list[str] = []
    real_set = tel.set_tracking

    async def set_tracking(on):
        if not on:
            stops.append(asyncio.current_task().get_name())
        await real_set(on)

    monkeypatch.setattr(engine, "_read_safety", read_safety)
    monkeypatch.setattr(engine, "_setup_target", setup_target)
    monkeypatch.setattr(cam, "get_temperature", get_temperature)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    try:
        await asyncio.wait_for(engine._park_hold_pause("rain", alpha), 10.0)
        await asyncio.sleep(0.05)          # a task it started would have asked
        assert probes["n"] > 3, "premise: the gate waited on the sensor"
        assert setups == ["Alpha"], f"premise: the pause re-acquired: {setups}"
        assert len(stops) == 1 and _park_lines(bus_lines) == [], (
            f"the safety pause's cooler gate watched a mount the pause had "
            f"already stopped: set_tracking(False) {len(stops)} time(s) "
            f"({stops}), lines {_park_lines(bus_lines)}")
        # Closed by the close-out's confirmed stop (#530); the spy setup
        # re-opens nothing.
        assert not engine._idle_hold_open and engine._idle_stop_task is None
    finally:
        await engine._cancel_idle_stop_retry()
