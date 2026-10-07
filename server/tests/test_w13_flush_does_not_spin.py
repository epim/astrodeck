# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``SessionReporter.flush()`` must not spin on a write that has finished but
whose done callback has not run yet (backlog wave 13 integration).

A finished write leaves ``_pending`` through its done callback, and asyncio
schedules done callbacks with ``call_soon``: they run on a LATER loop turn.
So for a moment a write can be done and still in the set. ``await
asyncio.gather(<only done tasks>)`` completes WITHOUT yielding to the loop,
so the earlier ``while self._pending: await asyncio.gather(...)`` turned that
moment into a synchronous infinite loop: the discard callback never got its
turn, and the whole event loop froze inside ``flush()``.

Found on the merged wave 13 tree, 2026-10-07: the integration's extra
``await self.reporter.flush()`` in ``SequenceEngine._run``'s CancelledError
arm ran at every test teardown that cancelled a run while a report write was
landing. All 24 xdist workers ended up at 100% CPU in ``report.py``'s
``flush`` (py-spy), and the suite never finished.

The test cannot let the mutant hang the suite, so it counts ``gather`` calls
instead of waiting: a correct ``flush()`` gathers nothing when every pending
write is already done.

Named mutant ``flush loops on _pending, not on running writes``: restore
``while self._pending: await asyncio.gather(*list(self._pending), ...)`` in
``SessionReporter.flush``. This test then fails with
``AssertionError: flush() called gather 50 times on a write that had already
finished: it spins instead of returning``.
"""
from __future__ import annotations

import asyncio

import astrodeck.hub as hub_module
import pytest

import astrodeck.sequence.report as report_module
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import SessionReporter

pytestmark = pytest.mark.asyncio

_SPIN_LIMIT = 50


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    """Reports land in tmp, never the real captures/ (as test_report.py)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


async def test_flush_returns_at_once_when_the_only_pending_write_is_done(
        monkeypatch):
    r = SessionReporter(SequencePlan(name="N"))
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    # A finished write still in the set: exactly the window between a task
    # finishing and its `_pending.discard` done callback getting its turn.
    r._pending.add(done)

    real_gather = asyncio.gather
    calls = 0

    def counting_gather(*aws, **kw):
        nonlocal calls
        calls += 1
        if calls >= _SPIN_LIMIT:
            raise AssertionError(
                f"flush() called gather {calls} times on a write that had "
                "already finished: it spins instead of returning")
        return real_gather(*aws, **kw)

    monkeypatch.setattr(report_module.asyncio, "gather", counting_gather)
    await asyncio.wait_for(r.flush(), 2.0)
    assert calls == 0, calls


async def test_control_flush_still_waits_for_a_write_that_is_running():
    """Control: the fix must not turn flush into a no-op. A write that is
    still running when flush() is called has finished by the time it
    returns."""
    r = SessionReporter(SequencePlan(name="N"))
    gate = asyncio.Event()

    async def slow_write():
        await gate.wait()

    task = asyncio.get_running_loop().create_task(slow_write())
    r._pending.add(task)
    task.add_done_callback(r._pending.discard)
    flushing = asyncio.get_running_loop().create_task(r.flush())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not flushing.done(), "flush() returned while a write was running"
    gate.set()
    await asyncio.wait_for(flushing, 2.0)
    assert task.done()
