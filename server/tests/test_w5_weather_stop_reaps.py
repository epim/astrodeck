# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-37 (f) / #628: ``WeatherService.stop`` carried the #252 shape --

    self._task.cancel()
    try:
        await self._task
    except (asyncio.CancelledError, Exception):
        pass
    self._task = None

-- which cannot tell the task's own cancellation from a cancel aimed at the
CALLER of ``stop``: a caller cancelled while it waits inside ``stop`` passes
its cancel to ``self._task`` (``Task.cancel`` cancels the future its task is
waiting on), and the ``except`` swallows both, so the caller runs on past its
own cancel instead of ending with it.

WP-35 (#252) converted thirteen sites of exactly this shape to
``astrodeck.aio.reap``; ``weather.py`` was found widening that scan rather
than in #252's own list, and was left on
``test_no_task_await_eats_its_callers_cancel.py``'s allowlist with a note
that it belongs to WP-37 (this one), which already owns ``weather.py``.

Mirrors ``test_w4_cancel_safe_awaits.py``'s ``DuskArm.stop`` case: the same
race (a task that takes 0.2 s to die once cancelled, the caller of ``stop``
cancelled while it waits), aimed at ``WeatherService.stop`` instead.

Named mutant: ``await reap(self._task)`` put back as the #252 try/except
shape above -- RED, observed verbatim:

    AssertionError: WeatherService.stop ate its caller's cancel: the caller
    ran on (the code after it ran: True) and self._task was cleared: True
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.weather import WeatherService

pytestmark = pytest.mark.asyncio

_DYING_S = 0.2
_BOUND_S = 2.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in work that takes ``seconds`` to tear down once
    cancelled, and does not hurry for a second cancel meanwhile (mirrors
    ``test_no_task_await_eats_its_callers_cancel.py``'s helper of the same
    name, kept local so this file stands on its own)."""
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
async def weather_service():
    """A ``WeatherService`` whose background task takes 0.2 s to die, with
    ``_task`` set directly rather than through ``start()`` -- the fetch loop
    itself (``_run``, Open-Meteo/Astrospheric calls) is not what is under
    test, only the teardown's handling of a task it has just cancelled."""
    svc = WeatherService()
    dying, died = asyncio.Event(), asyncio.Event()
    svc._task = asyncio.create_task(_dies_slowly(dying, died))
    await asyncio.sleep(0)
    yield svc, dying, died
    if svc._task is not None and not svc._task.done():
        svc._task.cancel()
        await asyncio.gather(svc._task, return_exceptions=True)


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


async def test_a_cancelled_weather_service_stop_does_not_run_on(weather_service):
    """``WeatherService.stop``'s caller is cancelled while the task it waits
    on dies: it ends with ``CancelledError`` within the bound, and the code
    after the stop (in production, the lifespan's next teardown step) does
    not run."""
    svc, dying, died = weather_service
    task, ran_after = await _cancel_mid_wait(svc.stop, dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"WeatherService.stop ate its caller's cancel: the caller ran on "
        f"(the code after it ran: {ran_after}) and self._task was cleared: "
        f"{svc._task is None}")
    assert not ran_after
    assert died.is_set(), "the caller heard its cancel before the task died"


async def test_control_an_uncancelled_weather_service_stop_returns(weather_service):
    """CONTROL: nothing cancels the caller. ``stop`` returns once the task is
    dead, and clears it."""
    svc, _dying, died = weather_service
    await asyncio.wait_for(svc.stop(), timeout=_BOUND_S)
    assert died.is_set()
    assert svc._task is None
