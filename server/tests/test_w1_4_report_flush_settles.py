# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1099, #1119: a report read after ``SessionReporter.flush()`` holds every
event the reporter recorded, whatever became of the write that carried it.

``flush()`` used to wait for the write TASKS ``_schedule_write`` had fired and
then return. A task whose write failed (on Windows an antivirus scan or an
indexer holding the report file, which ``os.replace`` reports as
``PermissionError``) gives up after its one bounded retry (#579), so the
report on disk stayed a frame behind the in-memory record for as long as
nothing else wrote: a set-aside wait, a cloud hold, or a simulated night
held at a fake instant by ``tests/_group_harness.py``. The s7 simulator
scenarios read the report at such an instant and compared its frame counts
with the session ledger's (``assert_the_three_agree``), and their poll for
the report to catch up gave up after two seconds of real time: a count
mismatch that a solo rerun never reproduces (#1099, five tests on 2026-10-19
and eight more on the #1075 branch).

The same gap sits behind ``finalize()``: a failed first attempt at the final
write is retried on a thread of its own a quarter second later, so the report
that says how the night ended landed after the call that was told it was
final had returned.

The fix is in ``flush()``: when no write is still running and the newest
snapshot built is not the one on disk, it writes a fresh snapshot, awaited,
once. Each test here fails the first write attempts on purpose, so the
outcome never depends on the machine's speed.

Named mutant ``flush() does not await the write that settles the report``
(the ``await self._write_async()`` in ``SessionReporter.flush`` deleted):
``test_flush_redoes_a_snapshot_whose_write_failed_twice`` fails with
``AssertionError: the report on disk holds 0 of the 1 frames the reporter
recorded after flush()``.
"""
from __future__ import annotations

import asyncio

import astrodeck.hub as hub_module
import pytest

import astrodeck.sequence.report as report_module
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import FrameRecord, SessionReporter

pytestmark = pytest.mark.asyncio

_BOUND_S = 5.0


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    """Reports land in tmp, never the real captures/ (as test_report.py)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(report_module, "_SNAPSHOT_RETRY_S", 0.0)
    monkeypatch.setattr(report_module, "_FINAL_RETRY_S", 0.05)
    return tmp_path


def _fail_the_first(monkeypatch, attempts: int) -> list[int]:
    """Make the report's disk write raise ``PermissionError`` for its first
    ``attempts`` calls and succeed after. Returns the call counter."""
    real = report_module.write_json_atomic
    calls = [0]

    def write(path, data, **kw):
        calls[0] += 1
        if calls[0] <= attempts:
            raise PermissionError(13, "held by another handle")
        return real(path, data, **kw)

    monkeypatch.setattr(report_module, "write_json_atomic", write)
    return calls


def _frame() -> FrameRecord:
    return FrameRecord(ts=1.0, target="T", filter="L", exposure_s=60.0)


def _frames_on_disk(report_id: str) -> int:
    loaded = SessionReporter.load(report_id)
    return 0 if loaded is None else loaded.frames_captured


async def test_flush_redoes_a_snapshot_whose_write_failed_twice(monkeypatch):
    """A frame is recorded; its snapshot write fails, and so does the one
    bounded retry. Nothing else will write while the night is held. After
    ``flush()`` the report on disk holds the frame."""
    calls = _fail_the_first(monkeypatch, 2)
    r = SessionReporter(SequencePlan(name="N"))
    r.record_frame(_frame())
    await asyncio.wait_for(r.flush(), _BOUND_S)
    assert _frames_on_disk(r.id) == 1, (
        f"the report on disk holds {_frames_on_disk(r.id)} of the 1 frames "
        f"the reporter recorded after flush()")
    assert calls[0] == 3, calls[0]


async def test_flush_lands_a_final_write_whose_first_attempt_failed(
        monkeypatch):
    """``finalize()``'s first write fails; its retry is a quarter second
    away on a thread of its own. After ``flush()`` the report says how the
    night ended, without waiting for that thread."""
    _fail_the_first(monkeypatch, 1)
    r = SessionReporter(SequencePlan(name="N"))
    r.finalize("complete")
    await asyncio.wait_for(r.flush(), _BOUND_S)
    loaded = SessionReporter.load(r.id)
    assert loaded is not None and loaded.end_reason == "complete", (
        "the report on disk does not say how the night ended after flush()")
    retry = r._final_retry
    assert retry is not None
    await asyncio.to_thread(retry.join)
    assert SessionReporter.load(r.id).end_reason == "complete"


async def test_control_flush_writes_nothing_when_the_report_is_current(
        monkeypatch):
    """CONTROL: every write landed, so ``flush()`` makes no further one."""
    calls = _fail_the_first(monkeypatch, 0)
    r = SessionReporter(SequencePlan(name="N"))
    r.record_frame(_frame())
    await asyncio.wait_for(r.flush(), _BOUND_S)
    written = calls[0]
    await asyncio.wait_for(r.flush(), _BOUND_S)
    assert written == 1 and calls[0] == 1, (written, calls[0])
    assert _frames_on_disk(r.id) == 1


async def test_flush_tries_once_more_not_for_ever_when_the_disk_keeps_failing(
        monkeypatch):
    """The disk refuses every write: ``flush()`` returns, never raises, and
    does not loop. A snapshot and its retry, then ``flush()``'s own snapshot
    and its retry: four attempts."""
    calls = _fail_the_first(monkeypatch, 10 ** 6)
    r = SessionReporter(SequencePlan(name="N"))
    r.record_frame(_frame())
    await asyncio.wait_for(r.flush(), _BOUND_S)
    assert calls[0] == 4, calls[0]
