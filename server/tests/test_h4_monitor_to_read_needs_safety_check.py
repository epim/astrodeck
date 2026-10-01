"""A monitor assigned is not enough for a wait to read the weather: the
plan's own safety check must be on too (the S7 review's E item on
`_monitor_to_read`, #452; spec 5.8, 6.17).

WHAT WAS UNTESTED. `_monitor_to_read` decides whether the run-start cooling
wait, the camera-lane wait and a cloud hold's release gate read the safety
monitor beside them: the monitor gate armed, as `_safety_gate` arms it
(``safety.enabled`` AND the plan's ``safety_check``), and a monitor assigned
or ``require_safety_monitor`` on. Every #452 case in
test_s7_waits_read_the_weather.py sets both halves of the first test true,
and no case had a monitor assigned with the plan's safety check off, so the
mutant ``and`` -> ``or`` survived all 109 of them: a plan that asked for no
safety checks would have had its waits reading the monitor, and pausing or
parking on its verdict, beside a scheduler whose own gate stands down.

THE CASE is test_s7_waits_read_the_weather.py's lane control, with the
monitor assigned (the simulator's own) and ``safety.enabled`` on, but the
plan's ``safety_check`` off. The lane wait asks no gate and reads no monitor,
as the scheduler's gate after it asks nothing of the monitor either. The
harness is test_idle_park_hold's clocked simulator; the site is its
fixture, never the real one, and nothing here prints a mount's position.

The mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-B-mut``), from a byte backup restored and sha256-checked after it,
never in the shared tree (#254). The observed failure is quoted verbatim
(the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import pytest

from astrodeck.config import SafetyConfig
from astrodeck.devices.base import SafetyReading

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _Clocked, _constraint_waiter, _plan, sim_hub, temp_store)
from test_s7_waits_read_the_weather import (LANE_FREE_S, PAUSE_AT_ONCE,
                                            _spy_lane, _spy_unsafe)
from test_waits_that_watch_the_mount import _lane_held_until

pytestmark = pytest.mark.asyncio


async def test_a_plan_with_its_safety_check_off_reads_no_monitor_in_a_wait(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Safety armed in the config, the simulator's monitor assigned and
    reading safe, and the plan's ``safety_check`` off. A plate solve holds
    the camera lane for ``LANE_FREE_S``: the lane wait asks no gate and
    takes no monitor read beside it, and nothing is unsafe. The run then
    goes on to its scheduler, whose waits ask the gate, which stands down
    for the plan, reading no monitor either.

    RED under mutant "and -> or" (`_monitor_to_read`'s ``not
    (cfg.safety.enabled and plan.safety_check)`` made ``not
    (cfg.safety.enabled or plan.safety_check)``), observed:

        AssertionError: a plan with its safety check off had the lane wait
        ask the safety gate 52 time(s), from 0.0 s, before the lane came
        free at 260.0 s

    (What the mutant changes is the READ BESIDE THE WAIT: the lane wait
    asking the gate every ``SAFETY_PAUSE_POLL_S``. The gate itself then
    stands down for the plan's check at each ask, so the device is not
    read even under it, and ``reads`` is empty either way; the gate calls
    are what grade the rule this case is about.)
    """
    temp_store.set_safety(SafetyConfig(**PAUSE_AT_ONCE))
    assert "safety" in sim_hub.devices, "premise: a monitor is assigned"
    run = _Clocked(sim_hub, monkeypatch, horizon_s=LANE_FREE_S + 60.0)
    t0 = run.t0
    plan = _plan(_constraint_waiter("Bravo", t0))
    assert plan.safety_check is False, "premise: the plan's check is off"
    _lane_held_until(run, monkeypatch, t0 + LANE_FREE_S)
    reads: list[float] = []

    async def safety_reading():
        reads.append(run.clock.t)
        return SafetyReading(is_safe=True, source="script", ts=run.clock.t)

    monkeypatch.setattr(sim_hub, "safety_reading", safety_reading)
    unsafe = _spy_unsafe(run, monkeypatch)
    lane = _spy_lane(run, monkeypatch)
    try:
        await run.night(plan)
        assert len(lane) == 2 and lane[1] - t0 >= LANE_FREE_S, (
            f"premise: the lane wait lasted until the lane came free: "
            f"{run.rel(lane, t0)} s")
        inside = [t for t, _ctx in run.gates if t < lane[1]]
        assert inside == [], (
            f"a plan with its safety check off had the lane wait ask the "
            f"safety gate {len(inside)} time(s), from "
            f"{run.rel(inside[:1], t0)[0]} s, before the lane came free at "
            f"{run.rel([lane[1]], t0)[0]} s")
        assert reads == [], (
            f"the monitor was read with the plan's safety check off: at "
            f"{run.rel(reads[:6], t0)} s")
        after = [t for t, _ctx in run.gates if t >= lane[1]]
        assert after and unsafe == [], (
            f"premise: the scheduler's gate ran after the lane, and nothing "
            f"was unsafe: gates after the lane at {run.rel(after[:3], t0)} s, "
            f"_on_unsafe {unsafe}")
    finally:
        await run.close()
