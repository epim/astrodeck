# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#684: the native guide loop yields to the event loop on EVERY frame.

Under ``ASTRODECK_FAST_TEST`` the sim guide camera's exposure dwell is 0, so
``_expose`` never suspends, and a frame whose Action is not a pulse (``idle``,
``settle``, a ``lock_lost`` the loop absorbs) has no await of its own either.
``await self._pulse(action)`` -> ``asyncio.sleep(_sim_delay(...))`` was the ONE
await in a frame that gave the event loop a turn, so a stretch of non-pulse
frames (or a pulse branch replaced with ``pass``, which is how wave 13 found
it) ran forever without any other coroutine getting a turn and the whole test
PROCESS hung: no traceback, no failure text, a CI runner eaten.

The fix is one ``await asyncio.sleep(0)`` at the top of ``_guide_loop``'s while
body. These tests bound the spin from INSIDE the camera double, so a regression
turns the suite red with a message instead of hanging it, and they use no
watchdog thread: the double raises a private ``BaseException`` (so neither
``_expose``'s ``except Exception`` retry nor the loop's own ``except Exception``
swallows it) once the loop has run a few hundred frames without a sibling
coroutine getting a turn. A Task stores such an exception without killing the
process, so the test then reports how many frames ran.

NAMED MUTANT "yield removed" (delete ``await asyncio.sleep(0)`` from the top of
``_guide_loop``'s while body): ``test_idle_frames_let_a_sibling_run`` fails
fast, observed verbatim:

    AssertionError: the guide loop ran 201 frames before a sibling coroutine
    got a turn (cap 200); #684: it must yield on every frame, whatever Action
    the frame dispatched

``test_pulse_frames_still_yield`` (the control) stays green under that mutant,
which is what shows the harness trips on a loop that does not yield and on
nothing else.

NAMED MUTANT "pulse branch passes" (the issue's original: ``_dispatch``'s
``kind in ("pulse", "pulse_pair")`` branch calls ``pass`` instead of
``await self._pulse(action)``), run with the yield in place against the guider
convergence tests that hung in wave 13, test_native_guider_algorithms.py: both
now FAIL by their own assertions in 64 s total instead of hanging the process,
observed verbatim:

    AssertionError: rms_total=10.28
    assert 10.28 < 2.0
"""
import asyncio
import os
import time

import pytest

from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.native import NativeGuider

# How many frames the loop may run with no sibling having had a turn before the
# camera double gives up on it. The fixed loop yields before its first frame, so
# the sibling sees 0; a few hundred is far past anything legitimate and still
# costs well under a second of sim rendering.
_FRAME_CAP = 200


class _Starved(BaseException):
    """Raised by the camera double when the loop has starved the event loop.
    A BaseException on purpose: ``_expose`` retries on ``Exception`` and the
    loop's defensive handler catches ``Exception``, and either would eat it and
    keep spinning."""


class _StubEngine:
    """Minimal engine stand-in (a copy of the one in
    test_native_guider_expose_retry.py, which this file does not import): every
    frame returns ``action``, ``stats`` reports a steady guiding phase."""

    def __init__(self, action):
        self.frames = 0
        self._action = action

    def process(self, data, ts, exposure_s):
        self.frames += 1
        return dict(self._action)

    def stats(self):
        return {"guiding": True, "settling": False, "recent": []}


async def _wait_until(cond, timeout_s=15.0):
    """Poll ``cond`` against a deadline (never a fixed sleep: Windows' 15.6 ms
    timer makes a fixed margin a coin toss, #669 / #675)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return False


async def _run_loop_with_sibling(action):
    """Start ``_guide_loop`` over a stub engine that returns ``action`` every
    frame, then a sibling coroutine, and report how many frames the camera had
    served when the sibling first ran. Returns ``(frames_before_sibling,
    loop_task, guider, tel_pulses)``; the caller stops the loop."""
    # FAST_TEST is the whole premise: at real dwell the exposure itself
    # suspends and the loop never starved. conftest's autouse fixture sets it;
    # assert it so this cannot silently run at real dwell and pass for the
    # wrong reason.
    assert os.environ.get("ASTRODECK_FAST_TEST") == "1"

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    guider = NativeGuider(cam, tel, config={"exposure_s": 0.05}, profile_id=None)
    guider._engine = _StubEngine(action)
    guider._active = True
    guider._stop.clear()

    pulses: list[tuple[str, int]] = []
    real_pulse = tel.pulse_guide

    async def recording_pulse(direction, ms):
        pulses.append((direction, int(ms)))
        return await real_pulse(direction, ms)

    tel.pulse_guide = recording_pulse

    sibling_ran = asyncio.Event()
    seen = {"frames": 0, "at_sibling": None}
    real_expose = cam.expose

    async def counting_expose(*args, **kwargs):
        seen["frames"] += 1
        if seen["frames"] > _FRAME_CAP and not sibling_ran.is_set():
            raise _Starved(seen["frames"])
        return await real_expose(*args, **kwargs)

    cam.expose = counting_expose

    async def sibling():
        seen["at_sibling"] = seen["frames"]
        sibling_ran.set()

    loop_task = asyncio.create_task(guider._guide_loop())
    sibling_task = asyncio.create_task(sibling())
    try:
        await asyncio.wait_for(sibling_ran.wait(), 5)
    finally:
        await asyncio.gather(sibling_task, return_exceptions=True)
    return seen["at_sibling"], loop_task, guider, pulses


async def _stop_loop(loop_task, guider):
    """Stop the loop the way Stop does. Returns None when it ended on its own,
    or a message when it did not: the caller asserts on it AFTER its own
    assertions, so a starved loop reports the frame count first, not this."""
    guider._stop.set()
    try:
        await asyncio.wait_for(asyncio.shield(loop_task), 5)
    except _Starved as e:
        return (f"the guide loop was killed by the starvation cap after "
                f"{e.args[0]} frames")
    except asyncio.TimeoutError:
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)
        return "the guide loop did not stop within 5 s of _stop being set"
    return None


@pytest.mark.asyncio
async def test_idle_frames_let_a_sibling_run():
    """A loop that only ever dispatches ``idle`` (no pulse, so no await of its
    own under FAST_TEST) must still give a sibling coroutine a turn within a
    frame. RED under the "yield removed" mutant (see the module docstring)."""
    frames, loop_task, guider, pulses = await _run_loop_with_sibling(
        {"action": "idle"})
    try:
        assert frames <= 1, (
            f"the guide loop ran {frames} frames before a sibling coroutine "
            f"got a turn (cap {_FRAME_CAP}); #684: it must yield on every "
            f"frame, whatever Action the frame dispatched")
        assert pulses == []   # nothing but idle frames: no pulse was the yield
    finally:
        outcome = await _stop_loop(loop_task, guider)
    assert outcome is None, outcome


@pytest.mark.asyncio
async def test_pulse_frames_still_yield():
    """Control: with a pulse on every frame the loop yields inside the pulse
    dispatch too, so the sibling still gets its turn and the pulses are
    delivered. Passes with or without the per-frame yield; it exists so the
    harness above is shown to trip ONLY on a loop that never yields."""
    frames, loop_task, guider, pulses = await _run_loop_with_sibling(
        {"action": "pulse", "dir": "north", "ms": 20})
    try:
        assert frames <= 1, (
            f"the guide loop ran {frames} frames before a sibling coroutine "
            f"got a turn (cap {_FRAME_CAP}) with a pulse on every frame")
        assert await _wait_until(lambda: len(pulses) >= 3), (
            "no pulse was delivered: the pulse path this control exists to "
            "exercise never ran")
        assert pulses[0] == ("north", 20)
    finally:
        outcome = await _stop_loop(loop_task, guider)
    assert outcome is None, outcome
