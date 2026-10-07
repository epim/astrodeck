# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-59 (continues WP-35, #252): ``ResumeArm.stop`` carried the #252
try/except shape --

    if self._task is not None:
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

-- which cannot tell the ladder task's own cancellation from a cancel aimed
at the CALLER of ``stop``: a caller cancelled while it waits inside ``stop``
passes its cancel to ``self._task`` (``Task.cancel`` cancels the future its
task is waiting on), and the ``except`` swallows both, so the caller runs on
past its own cancel instead of ending with it. ``resume_arm.py`` was one of
the three "hot" files WP-35 (#252) deferred; WP-59 converts it, to
``astrodeck.aio.reap``.

Mirrors ``test_w5_weather_stop_reaps.py``'s ``WeatherService.stop`` case and
``test_no_task_await_eats_its_callers_cancel.py``'s hub sites: the same race
(a task that takes 0.2 s to die once cancelled, the caller of ``stop``
cancelled while it waits), aimed at ``ResumeArm.stop`` instead. ``engine``
and ``hub`` are never touched by ``stop()``, so bare placeholders stand in
for them.

Named mutant: ``await reap(self._task)`` put back as the #252 try/except
shape above -- RED, observed verbatim:

    AssertionError: ResumeArm.stop ate its caller's cancel: the caller ran
    on (the code after it ran: True) and self._task was cleared: True
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.sequence.resume_arm import ResumeArm

pytestmark = pytest.mark.asyncio

_DYING_S = 0.2
_BOUND_S = 2.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in work that takes ``seconds`` to tear down once
    cancelled, and does not hurry for a second cancel meanwhile (mirrors
    test_w5_weather_stop_reaps.py's helper of the same name)."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        dying.set()
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (left := end - loop.time()) > 0:
            try:
                await asyncio.sleep(left)
            except asyncio.CancelledError:
                pass
        died.set()
        raise


@pytest.fixture
async def resume_arm():
    """A ``ResumeArm`` whose ladder task takes 0.2 s to die, set directly on
    ``_task`` rather than through a real ``tick()`` loop -- ``stop()``'s
    handling of a task it has just cancelled is what is under test, not the
    ladder itself. ``engine``/``hub`` are never read by ``stop()``."""
    arm = ResumeArm(engine=None, hub=None)
    dying, died = asyncio.Event(), asyncio.Event()
    arm._task = asyncio.create_task(_dies_slowly(dying, died))
    await asyncio.sleep(0)
    yield arm, dying, died
    if arm._task is not None and not arm._task.done():
        arm._task.cancel()
        await asyncio.gather(arm._task, return_exceptions=True)


async def _cancel_mid_wait(call, dying: asyncio.Event):
    """Run ``await call()`` in a caller, cancel the caller while the task it
    waits on is dying, and wait up to ``_BOUND_S`` for it. Returns
    ``(caller_task, ran_after)``."""
    ran_after: list[bool] = []

    async def caller() -> None:
        await call()
        ran_after.append(True)

    task = asyncio.create_task(caller())
    await asyncio.wait_for(dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    return task, bool(ran_after)


async def test_a_cancelled_resume_arm_stop_does_not_run_on(resume_arm):
    """``ResumeArm.stop``'s caller is cancelled while the ladder task it
    waits on dies: it ends with ``CancelledError`` within the bound, and the
    code after the stop (in production, the lifespan's next teardown step)
    does not run."""
    arm, dying, died = resume_arm
    task, ran_after = await _cancel_mid_wait(arm.stop, dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"ResumeArm.stop ate its caller's cancel: the caller ran on (the "
        f"code after it ran: {ran_after}) and self._task was cleared: "
        f"{arm._task is None}")
    assert not ran_after
    assert died.is_set(), "the caller heard its cancel before the task died"


async def test_control_an_uncancelled_resume_arm_stop_returns(resume_arm):
    """CONTROL: nothing cancels the caller. ``stop`` returns once the task
    is dead, and clears it."""
    arm, _dying, died = resume_arm
    await asyncio.wait_for(arm.stop(), timeout=_BOUND_S)
    assert died.is_set()
    assert arm._task is None
