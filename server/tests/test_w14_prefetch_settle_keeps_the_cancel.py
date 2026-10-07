# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``Prefetch.settle`` does not eat its caller's cancel (#710, found by #681).

``settle`` waits for the focus sweep's speculative exposure and hands back its
frame. It was ``try: return await self.task / except BaseException: return
None``, and ``BaseException`` takes ``CancelledError`` with it. A caller
cancelled while it waited there had its cancel delivered to the exposure it
awaits (``Task.cancel`` cancels the future its task is waiting on); the
``CancelledError`` that came back was the task's and the caller's at once, the
handler ate both, and the sweep ran on past a halt the operator had asked for.
It is the shape of #235 and #252 in a place the guard could not see until
#681 widened it, and it is intermittent by construction: it needs the cancel
to land while the exposure is in flight or dying, so a fix is validated by
cancelling INSIDE that window, not by cancelling a task that has finished.

The fix waits through ``astrodeck.aio.reap`` and reads the task's result
afterwards. The documented return value for the speculative exposure is kept
(the frame, or None for a guess that raised or was cancelled), and a guess
still never raises out of ``settle``.

Every case names the mutant it was shown RED under, run from a byte-for-byte
backup of ``focus/pipeline.py`` and restored byte-identical, with the failure
observed quoted.
"""
from __future__ import annotations

import asyncio

from astrodeck.focus.pipeline import Prefetch

_DYING_S = 0.05
_BOUND_S = 2.0


async def _exposure(started: asyncio.Event, died: asyncio.Event | None = None,
                    dying_s: float = _DYING_S):
    """A speculative exposure parked in device I/O. Cancelled, it takes
    ``dying_s`` to die: the window the caller's cancel has to land in."""
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.sleep(dying_s)
        if died is not None:
            died.set()
        raise


async def test_a_caller_cancelled_mid_settle_ends_cancelled():
    """THE CASE #710 IS. The caller is cancelled while it waits in ``settle``
    for an exposure that is still in flight. It must end cancelled, and not
    before the exposure it was waiting for has finished dying (the devices are
    free again when it hears the cancel, which ``Prefetch``'s own docstring
    promises on every path).

    MUTANT "the old except BaseException form" (``settle`` restored to ``try:
    return await self.task`` / ``except BaseException: return None``): RED -
        AssertionError: the caller was cancelled while it waited in settle()
        and ran on: it returned None, so the sweep goes on past a halt the
        operator asked for
    """
    started, died = asyncio.Event(), asyncio.Event()
    pre = Prefetch(11835, asyncio.create_task(_exposure(started, died)))
    caller = asyncio.create_task(pre.settle())
    await asyncio.wait_for(started.wait(), _BOUND_S)
    await asyncio.sleep(0)              # the caller is parked in settle()
    assert not caller.done(), "premise: the caller is waiting"

    caller.cancel()
    await asyncio.wait({caller}, timeout=_BOUND_S)

    assert caller.done(), "the caller never ended after its cancel"
    assert caller.cancelled(), (
        "the caller was cancelled while it waited in settle() and ran on: it "
        f"returned {caller.result()!r}, so the sweep goes on past a halt the "
        "operator asked for")
    assert died.is_set() and pre.task.done(), (
        "the caller heard its cancel before the exposure had finished dying, "
        "so the focuser and the camera were not free")


async def test_a_caller_cancelled_mid_take_ends_cancelled():
    """``take`` is the other door to the same wait (the frame if it was
    exposed at the position asked, else None), and the sweep calls it, so a
    halt that lands there is the real case.

    MUTANT "the old except BaseException form": RED, the same defect from
    the caller of ``take``:
        AssertionError: the caller was cancelled while it waited in take()
        and ran on: None
    """
    started = asyncio.Event()
    pre = Prefetch(11835, asyncio.create_task(_exposure(started)))
    caller = asyncio.create_task(pre.take(11835))
    await asyncio.wait_for(started.wait(), _BOUND_S)
    await asyncio.sleep(0)

    caller.cancel()
    await asyncio.wait({caller}, timeout=_BOUND_S)

    assert caller.cancelled(), (
        "the caller was cancelled while it waited in take() and ran on: "
        f"{caller.result() if caller.done() and not caller.cancelled() else 'still waiting'!r}")


async def test_control_the_frame_of_a_finished_exposure_is_returned():
    """CONTROL: the documented return value is kept. A task that finished
    hands back its frame, and ``take`` keeps the position check.

    MUTANT "the result is not read" (``settle`` returns None whatever the task
    produced): RED -
        AssertionError: assert None == 'frame at 11835'
    """
    async def expose():
        return "frame at 11835"

    assert await Prefetch(11835, asyncio.create_task(expose())).settle() == (
        "frame at 11835")
    assert await Prefetch(11835, asyncio.create_task(expose())).take(11835) == (
        "frame at 11835")
    assert await Prefetch(11835, asyncio.create_task(expose())).take(11485) is (
        None)


async def test_control_a_guess_that_raised_is_none_and_never_raises():
    """CONTROL: a speculative failure is not the run's failure (the in-line
    path that follows will hit the same device error properly), so ``settle``
    returns None for a task that raised, and the caller is NOT cancelled.

    MUTANT "the task's exception propagates" (``settle`` ends with ``return
    self.task.result()`` and no ``exception()`` test): RED -
        RuntimeError: focuser said no
    """
    async def boom():
        raise RuntimeError("focuser said no")

    assert await Prefetch(11835, asyncio.create_task(boom())).settle() is None


async def test_control_an_exposure_cancelled_by_someone_else_is_none_not_a_cancel():
    """CONTROL, and the over-correction the fix must not make: a speculative
    exposure that ends cancelled by somebody ELSE (a teardown cancelling the
    task, not the caller of ``settle``) is a guess that did not land, so
    ``settle`` returns None and the caller carries on, uncancelled. Only a
    cancel aimed at the CALLER may end the caller.

    MUTANT "the task's own cancel ends the caller" (``settle`` ends with
    ``self.task.result()`` after the ``exception()`` test, with no
    ``cancelled()`` test, so a cancelled task raises its CancelledError into
    the caller): RED, and the teardown control below with it (it cancels the
    task itself) -
        asyncio.exceptions.CancelledError
    """
    started = asyncio.Event()
    pre = Prefetch(11835, asyncio.create_task(_exposure(started)))
    await asyncio.wait_for(started.wait(), _BOUND_S)
    pre.task.cancel()

    assert await pre.settle() is None
    assert asyncio.current_task().cancelling() == 0, (
        "settle() left the caller marked as cancelled")


async def test_control_the_teardown_flag_cancels_the_exposure_and_returns_none():
    """CONTROL: ``settle(cancel=True)``, the teardown path, kills the
    exposure and returns None without waiting it out.

    MUTANT "cancel=True is ignored" (the ``if cancel: self.task.cancel()``
    removed): RED, by timeout - the exposure never ends, so the wait is the
    bound's:
        asyncio.exceptions.TimeoutError
    """
    started = asyncio.Event()
    pre = Prefetch(11835, asyncio.create_task(_exposure(started, dying_s=0.01)))
    await asyncio.wait_for(started.wait(), _BOUND_S)

    assert await asyncio.wait_for(pre.settle(cancel=True), _BOUND_S) is None
    assert pre.task.cancelled()
