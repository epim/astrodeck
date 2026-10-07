# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-59 (continues WP-35, #252): ``SequenceEngine.abort`` carried the #252
try/except shape --

    self._task.cancel()
    try:
        await self._task
    except (asyncio.CancelledError, Exception):
        pass

-- which cannot tell the run task's own cancellation from a cancel aimed at
the CALLER of ``abort()`` (the REST route's 15 s cap, or the lock screen's
emergency STOP, see ``abort``'s own docstring): a caller cancelled while it
waits there passes its cancel to ``self._task``, and the ``except`` swallows
both, so the caller runs on past its own cancel -- draining thumbnails,
finishing the idle stop, finalizing the report a SECOND time -- instead of
ending with it. ``engine.py`` was one of the three "hot" files WP-35 (#252)
deferred; WP-59 converts this site to ``astrodeck.aio.reap``.

``self._task`` is set directly to a synthetic task that takes 0.2 s to die
once cancelled, bypassing a real run (``engine.start``) entirely -- the same
technique ``test_no_task_await_eats_its_callers_cancel.py``'s ``warm_hub``/
``loop_hub`` fixtures use for ``Hub`` -- so the race is driven by a clock
this test controls rather than real device I/O or the sim rig's own
teardown timing (LESSON #669/#675: never lean on real timing for a cancel
race when a synthetic one says the same thing deterministically).

Named mutant: ``await reap(self._task)`` put back as the #252 try/except
shape above -- RED, observed verbatim:

    AssertionError: SequenceEngine.abort ate its caller's cancel: the caller
    ran on (the code after it ran: True) and _drain_thumb_tasks ran: True
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine

pytestmark = pytest.mark.asyncio

_DYING_S = 0.2
_BOUND_S = 2.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in work that takes ``seconds`` to tear down once
    cancelled (mirrors test_w5_weather_stop_reaps.py's helper)."""
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
async def aborting_engine(monkeypatch):
    """A ``SequenceEngine`` whose ``_task`` (standing in for a live run)
    takes 0.2 s to die once cancelled. ``_drain_thumb_tasks`` is replaced
    with a spy: it is the FIRST thing ``abort()`` does after the reap, so
    whether it ran is this test's signal for "the caller ran on past its
    cancel" without needing a real run's full teardown (idle stop, report
    finalize, session store) behind it. Yields ``(engine, dying, died,
    drained)``."""
    h = Hub()
    engine = SequenceEngine(h)
    dying, died = asyncio.Event(), asyncio.Event()
    engine._task = asyncio.create_task(_dies_slowly(dying, died))
    drained: list[bool] = []

    async def _spy_drain() -> None:
        drained.append(True)

    monkeypatch.setattr(engine, "_drain_thumb_tasks", _spy_drain)
    await asyncio.sleep(0)
    yield engine, dying, died, drained
    if engine._task is not None and not engine._task.done():
        engine._task.cancel()
        await asyncio.gather(engine._task, return_exceptions=True)


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


async def test_a_cancelled_abort_does_not_run_on_to_draining_thumbs(
        aborting_engine):
    """``abort()``'s caller is cancelled while the run task it waits on
    dies: it ends with ``CancelledError`` within the bound, and the code
    after the reap (``_drain_thumb_tasks``, in production followed by the
    idle-stop finish and the report finalize) never runs."""
    engine, dying, died, drained = aborting_engine
    task, ran_after = await _cancel_mid_wait(engine.abort, dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"SequenceEngine.abort ate its caller's cancel: the caller ran on "
        f"(the code after it ran: {ran_after}) and _drain_thumb_tasks ran: "
        f"{bool(drained)}")
    assert not ran_after and not drained
    assert died.is_set()
    # `_aborting` is cleared in the `finally`, which DOES still run on a
    # cancelled exit (a bare `finally` always does) -- this is not the bug
    # #252 is about, and a stuck-True flag here would wedge the NEXT abort
    # (see `abort`'s own "ALREADY TEARING DOWN -> NO-OP" guard), so it is
    # worth pinning as a control.
    assert engine._aborting is False


async def test_control_an_uncancelled_abort_drains_thumbs(aborting_engine):
    """CONTROL: nothing cancels the caller. ``abort()`` returns once the run
    task is dead, and goes on to drain thumbnails."""
    engine, _dying, died, drained = aborting_engine
    await asyncio.wait_for(engine.abort(), timeout=_BOUND_S)
    assert died.is_set()
    assert drained == [True]
    assert engine._aborting is False
