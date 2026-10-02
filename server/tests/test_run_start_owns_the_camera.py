# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A run does not start on top of somebody else's camera operation (#44).

2026-09-18, `captures/logs/2026-09-17.jsonl`. A plate solve began at 03:17:56 at
the park position, after the previous run completed. The next run started at
03:18:10, and every camera-owning step of it lost to that solve in turn: the
centring degraded to a raw GoTo, the initial autofocus was skipped, and the
third refusal - "capture light refused" - crashed the night at 03:18:52. The
lingering solve finally let go at 03:19:17, 81 seconds after the run started.

Each of those three steps reported the busy lane correctly. What nobody did was
ask before starting.

These drive `_await_camera_lane` against the hub's REAL capture lane
(`hub._capture_lock` / `_capture_busy`, the same pair `_exposing` sets), because
the whole defect is about that lock being held by someone else.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.engine import SafetyAbort


class _Hub:
    """Just the capture lane. The wait reads two attributes and nothing else,
    and a real Hub drags a rig behind it."""

    def __init__(self):
        self._capture_lock = asyncio.Lock()
        self._capture_busy: str | None = None


def _engine() -> SequenceEngine:
    return SequenceEngine(_Hub())


async def test_a_free_camera_does_not_delay_the_start():
    """The ordinary path, and the one a clumsy guard makes expensive: nothing
    holds the lane, so this returns without waiting at all.

    MUTATION: drop the `not lock.locked()` early return. Observed: the loop is
    entered, and with the lock free it still exits at once - so this case is
    timed to catch the version that sleeps anyway.
    """
    engine = _engine()
    started = asyncio.get_running_loop().time()
    await engine._await_camera_lane()
    assert asyncio.get_running_loop().time() - started < 0.2, (
        "a run with a free camera was delayed at the gate")


async def test_a_run_waits_for_a_solve_that_is_already_in_flight():
    """The incident. The lane is held when the run starts and is released
    while it waits; the run proceeds rather than losing its first three steps.

    MUTATION: delete the `await self._await_camera_lane()` call from `_run`.
    Observed: this case still passes (it calls the method directly), and
    `test_the_run_actually_calls_the_gate` below is the one that fails - which
    is why that case exists.
    """
    engine = _engine()
    hub = engine.hub
    await hub._capture_lock.acquire()
    hub._capture_busy = "plate solve"

    async def release_later():
        await asyncio.sleep(0.3)
        hub._capture_busy = None
        hub._capture_lock.release()

    asyncio.ensure_future(release_later())
    await asyncio.wait_for(engine._await_camera_lane(), 10)
    assert not hub._capture_lock.locked()


async def test_a_camera_that_never_comes_free_refuses_the_run(monkeypatch):
    """The other half. A run that cannot have the camera says so at the gate,
    with the holder named, instead of reporting "capture light refused" two
    minutes later - and it raises SafetyAbort, so the night tears down through
    the shielded park/warm path rather than leaving a half-started run.

    MUTATION: `return` instead of `raise` on the deadline. Observed: no
    exception, and the run proceeds into exactly the three failures the issue
    records.
    """
    import astrodeck.sequence.engine as eng
    monkeypatch.setattr(eng, "_CAMERA_LANE_WAIT_S", 0.5)
    engine = _engine()
    hub = engine.hub
    await hub._capture_lock.acquire()
    hub._capture_busy = "plate solve"
    try:
        with pytest.raises(SafetyAbort) as excinfo:
            await asyncio.wait_for(engine._await_camera_lane(), 10)
    finally:
        hub._capture_lock.release()
    said = str(excinfo.value)
    assert "plate solve" in said, (
        f"the refusal does not name what is holding the camera: {said}")
    assert "camera" in said, said


async def test_a_hub_with_no_capture_lane_is_not_a_reason_to_refuse():
    """`getattr`, not attribute access: several tests and at least one backend
    double stand in for the hub, and a run must not fail to start because the
    object in front of it has no lane to check.

    MUTATION: read `self.hub._capture_lock` directly. Observed: AttributeError
    out of run start, which turns a missing attribute into a dead night.
    """
    class _Bare:
        pass

    await SequenceEngine(_Bare())._await_camera_lane()


async def test_the_run_actually_calls_the_gate(monkeypatch):
    """The wiring, which no amount of testing the method proves. `_run` has to
    reach it BEFORE anything touches a camera - the whole point is that the
    centring solve is already too late.

    Asserted by source order rather than by driving a run: the first camera
    step is several hundred lines and a rig into `_run`, and what matters here
    is only that the gate precedes it.

    MUTATION: move the call below `self._start_watchdog()`... still passes; the
    watchdog is not a camera step. Move it below `_cool_and_wait` and this
    fails, because the cooler decision is the first thing that talks to the
    camera.
    """
    import inspect
    src = inspect.getsource(SequenceEngine._run)
    gate = src.find("_await_camera_lane")
    cool = src.find("_cool_and_wait")
    assert gate != -1, "run start no longer waits for the camera lane at all"
    assert cool != -1, "this case can no longer find the first camera step"
    assert gate < cool, (
        "the camera-lane wait has moved below the first camera step, so a "
        "lingering solve reaches it again")
