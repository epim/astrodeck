# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-59 (continues WP-35, #252): ``SequenceEngine._flip_bounded``'s shielded
flip-wait ``finally`` carried the #252 ``suppress`` shape --

    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

-- which cannot tell the flip task's own cancellation from a cancel aimed at
the CALLER of ``_flip_bounded``: a caller cancelled while it waits inside
that ``finally`` passes its cancel to ``task`` too (``Task.cancel`` cancels
the future its task is waiting on), and the ``suppress`` swallows both, so
the caller runs on past its own cancel instead of ending with it.
``engine.py`` was one of the three "hot" files WP-35 (#252) deferred; WP-59
converts this site to ``astrodeck.aio.reap``.

THE RACE, PRECISELY. The ``finally`` is reached with ``task`` still running
(not done) only on the TIMEOUT path: the first bound (``FLIP_TIMEOUT_S``)
expires on its own, ``_flip_bound()`` (no guider connected, so it answers at
once) does not grow the total, and ``_timeout_abort`` is raised while the
flip (shielded, so the expired ``wait_for`` never touched it) is still
running. THAT raise is what puts the caller inside the ``finally``'s own
``task.cancel(); await reap(task)`` -- no external cancellation needed to
get there, exactly as a real meridian flip that overran its bound would
reach it. From there, ONE cancellation of the caller, landing while that
reap is in flight, is the #235/#252 shape this test drives (mirrors
``test_w5_weather_stop_reaps.py``'s ``WeatherService.stop`` case and the
guard's own hub fixtures).

``FLIP_TIMEOUT_S`` is monkeypatched down to a few milliseconds so the first
bound expires at once rather than after real minutes; the flip itself
(``task``) is a synthetic coroutine that takes 0.2 s to die once cancelled,
standing in for ``hub.meridian_flip`` -- real mount/guider timing is not
this test's concern (LESSON #669/#675), only the ``finally``'s own reap.

Named mutant: ``await reap(task)`` put back as the #252
``suppress(BaseException)`` shape above -- RED, observed verbatim (the
caller's cancellation is spent cancelling the already-dying flip instead,
the flip's own resulting ``CancelledError`` is swallowed by the
``suppress``, and the ``SafetyAbort`` already propagating from the
``raise _timeout_abort(...)`` above the ``finally`` wins through
unchanged -- exactly as if the cancel had never been asked for):

    AssertionError: _flip_bounded ate its caller's cancel: the caller ran on
    (the code after it ran: False, ended with: SafetyAbort('meridian flip
    timed out after 0s'))
    assert False
     +  where False = <built-in method cancelled of ...>()
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine

pytestmark = pytest.mark.asyncio

_DYING_S = 0.2
_BOUND_S = 2.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in work that takes ``seconds`` to tear down once
    cancelled, standing in for ``hub.meridian_flip`` (mirrors
    test_w5_weather_stop_reaps.py's helper)."""
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
def engine(monkeypatch):
    """A ``SequenceEngine`` over a bare, guider-less ``Hub`` (so
    ``_flip_bound`` answers at once, no device I/O), with ``FLIP_TIMEOUT_S``
    cut to a few milliseconds so the first bound expires immediately -- the
    natural, no-external-cancel way to reach the ``finally`` with the flip
    still running."""
    monkeypatch.setattr(engine_module, "FLIP_TIMEOUT_S", 0.01)
    return SequenceEngine(Hub())


async def _cancel_mid_wait(call, dying: asyncio.Event):
    """Run ``await call()`` in a caller, cancel the caller while the task it
    waits on is dying, and wait up to ``_BOUND_S`` for it. Returns
    ``(caller_task, ran_after)`` (mirrors the guard file's helper of the
    same name)."""
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


async def test_a_cancelled_flip_bounded_does_not_run_on(engine):
    """``_flip_bounded``'s caller is cancelled while the ``finally``'s own
    reap of the overrun flip is in flight: it ends with ``CancelledError``
    within the bound, and nothing after the ``finally`` (in production, the
    flip's ``flipped`` handling and the next step of the run) runs."""
    dying, died = asyncio.Event(), asyncio.Event()
    flip = _dies_slowly(dying, died)

    task, ran_after = await _cancel_mid_wait(
        lambda: engine._flip_bounded(flip), dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"_flip_bounded ate its caller's cancel: the caller ran on (the "
        f"code after it ran: {ran_after}, ended with: "
        f"{task.exception() if not ran_after else None!r})")
    assert not ran_after
    assert died.is_set(), "the caller heard its cancel before the flip died"


async def test_control_an_uncancelled_flip_bounded_raises_the_timeout(engine):
    """CONTROL: nothing cancels the caller. The overrun flip is reaped to
    completion and ``_flip_bounded`` raises the timeout it was always going
    to -- not swallowed, not replaced by the flip's own ``CancelledError``."""
    dying, died = asyncio.Event(), asyncio.Event()
    flip = _dies_slowly(dying, died)

    with pytest.raises(engine_module.SafetyAbort):
        await asyncio.wait_for(engine._flip_bounded(flip), timeout=_BOUND_S)
    assert died.is_set()
