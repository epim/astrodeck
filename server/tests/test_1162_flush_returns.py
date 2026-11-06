# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1162: ``FlightRecorder.flush`` must always return.

CI's server job reached 99% and then printed nothing until the 45-minute cut.
``faulthandler_timeout`` named the test: a worker sat inside
``FlightRecorder.flush`` with the event loop running (not in its selector) and
one idle executor thread. Python 3.12's ``asyncio.gather`` runs the done
callbacks of children that are already done synchronously, so a gather over
done futures returns an already-done outer future and ``await`` does not
yield. A write future that has finished but whose own done callback (the one
that removes it from ``_pending``) is still queued in the loop is exactly
that: ``flush`` looped ``while self._pending: await gather(...)`` without ever
letting the loop run that callback, so ``_pending`` never emptied. Timing
dependent, which is why it was one run in four.

A spin that never yields cannot be cancelled by ``asyncio.wait_for``, so the
tests here carry a watchdog thread: after the bound it empties ``_pending``
(which is what ends the spin) and the test then fails because it had to.

MUTANT (flush put back to ``while self._pending: await gather(*pending)``):
RED, both tests, in 3 s each, with "flush did not return".
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.events import night_key
from astrodeck.guide.flight_recorder import FlightRecorder

BOUND_S = 3.0


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return tmp_path


async def _flush_within_bound(recorder: FlightRecorder) -> bool:
    """True when ``flush`` returned by itself inside the bound. A flush that
    spins without yielding is released by a thread, and then reported."""
    rescued = threading.Event()

    def _release() -> None:
        rescued.set()
        recorder._pending.clear()

    watchdog = threading.Timer(BOUND_S, _release)
    watchdog.start()
    try:
        await asyncio.wait_for(recorder.flush(), BOUND_S)
    finally:
        watchdog.cancel()
    return not rescued.is_set()


@pytest.mark.asyncio
async def test_flush_returns_when_a_write_finished_before_its_callback_ran():
    """A write future that is done while its done callback is still queued."""
    recorder = FlightRecorder()
    finished = asyncio.get_running_loop().create_future()
    recorder._pending.add(finished)
    finished.set_result(None)
    assert await _flush_within_bound(recorder), (
        "flush did not return: it spun on a finished write")
    assert not recorder._pending


@pytest.mark.asyncio
async def test_flush_returns_for_a_real_write_that_lands_just_before_it(
        _isolated):
    """The real interleaving: the executor thread finishes the write, its
    completion is queued ahead of the flush's own step, and the flush runs
    first in the next loop iteration, before the write's done callback."""
    recorder = FlightRecorder()
    recorder.begin_cycle(measured=False)
    written = recorder.dump("x")
    assert written is not None
    folder = _isolated / "captures" / "guide" / night_key()
    deadline = time.monotonic() + BOUND_S
    while not list(folder.glob("*.json")) and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    await asyncio.sleep(0)
    assert await _flush_within_bound(recorder), (
        "flush did not return: it spun on a finished write")
    assert len(list(folder.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_flush_gives_up_on_a_write_that_never_completes():
    """A write that can never finish does not hold ``flush`` forever: it
    raises ``TimeoutError`` that counts the writes still running.

    MUTANT (``flush`` without a timeout): RED, ``TypeError`` for the
    ``timeout`` argument, or the 3 s bound's ``TimeoutError`` with another
    message."""
    recorder = FlightRecorder()
    recorder._pending.add(asyncio.get_running_loop().create_future())
    with pytest.raises(TimeoutError, match=r"1 write\(s\) still running"):
        await asyncio.wait_for(recorder.flush(timeout=0.05), BOUND_S)
