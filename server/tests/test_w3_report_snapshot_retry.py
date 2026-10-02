# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A failed non-final (snapshot) report write gets one bounded retry (#579).

``SessionReporter._persist`` used to log a failed snapshot write and return,
on the stated assumption that "a snapshot's is repaired by the next" one.
That held only while a next event (a frame, a safety event, a sky-angle row)
was coming soon. During a long hold -- a set-aside expiry wait, a cloud
hold, a wait for a target to rise -- nothing schedules another write until
the hold ends, so the report on disk could stay a frame behind the ledger
for as long as the hold lasted (observed in a stress run of
tests/test_s7_sim_continue_second_night.py: 21 failures in 600 runs, always
the same ``assert_the_three_agree`` check, always a report exactly one
snapshot short).

THE FIX: a snapshot write that fails is retried once, ``_SNAPSHOT_RETRY_S``
later, on the SAME thread that already ran it -- ``_write_async`` always
dispatches ``_persist`` through ``asyncio.to_thread``, so that thread has no
loop to hold, and the retry's sleep costs the loop nothing. A write made
with no loop at all (``_write_sync``, the synchronous/unit-test path that
``test_a_snapshot_write_is_not_retried`` in test_report_load_reason.py
pins) is unchanged: production never reaches it, every real writer goes
through the loop.

Both mutants below were applied in a private copy of ``server/`` under the
session scratchpad (``w3-WP-28-mut``), restored byte-for-byte and verified
with a SHA-256 compare afterwards, never in the shared tree.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import astrodeck.sequence.report as report_mod
from astrodeck.events import bus
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import FrameRecord, SessionReporter


def _denied(path: Path) -> PermissionError:
    """The error Windows gives while another handle has the file, carrying
    the absolute path in its text as the real one does (matches the shape
    quoted in #579: a reader's poll racing the snapshot's own replace)."""
    return PermissionError(
        13, "The process cannot access the file because it is being used by "
            "another process", str(path), 5)


def _write_failing(monkeypatch, exc, times: int | None):
    """Make ``write_json_atomic`` raise ``exc(path)`` ``times`` times
    (``None``: always), and count every call."""
    real = report_mod.write_json_atomic
    calls = {"n": 0}

    def fake(path, data, **kw):
        calls["n"] += 1
        if times is None or calls["n"] <= times:
            raise exc(Path(path))
        return real(path, data, **kw)
    monkeypatch.setattr(report_mod, "write_json_atomic", fake)
    return calls


def _warnings(monkeypatch) -> list[str]:
    """Every ``report`` warning logged from here on, captured at ``bus.log``
    itself so the storm limiter and the 200-entry ring play no part."""
    out: list[str] = []
    real = bus.log

    def log(level, message, source="hub", **kw):
        if source == "report" and level == "warning":
            out.append(message)
        return real(level, message, source, **kw)
    monkeypatch.setattr(bus, "log", log)
    return out


async def _landed_with_a_frame(report_id: str, timeout: float = 5.0):
    """The report once its ``by_filter`` header shows a frame, read off a
    thread with no loop as every production reader does, or the last read
    after ``timeout``. Polling, not a single read: the retry runs on its own
    schedule and this waits for it rather than racing it."""
    deadline = time.monotonic() + timeout
    while True:
        got = await asyncio.to_thread(SessionReporter.read, report_id)
        if (got.report is not None and got.report.by_filter) \
                or time.monotonic() > deadline:
            return got
        await asyncio.sleep(0.01)


async def test_a_snapshot_write_that_fails_once_is_retried_with_no_next_event(
        monkeypatch):
    """One ``record_frame`` call is the ONLY event. Its first snapshot write
    is refused; nothing else is ever recorded (the hold this bug lived in has
    no next event), yet the report still lands with the frame counted,
    because the retry -- not a rerun -- repaired it.

    RED under the mutant "off_loop retry removed" (``_persist``'s snapshot
    branch put back to its pre-#579 shape: the warning logged and an
    unconditional ``return`` right after, dropping the ``off_loop`` retry),
    observed:
        E   AssertionError: the report never landed: reason='missing', write
            calls={'n': 1}
        E   assert None is not None
    """
    monkeypatch.setattr(report_mod, "_SNAPSHOT_RETRY_S", 0.01)
    calls = _write_failing(monkeypatch, _denied, times=1)
    r = SessionReporter(SequencePlan(name="t"), report_id="snap-retry-once",
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31", filter="L",
                               exposure_s=60.0))

    got = await _landed_with_a_frame("snap-retry-once")
    assert got.report is not None, (
        f"the report never landed: reason={got.reason!r}, "
        f"write calls={calls}")
    assert [bf.filter for bf in got.report.by_filter] == ["L"], (
        f"no next event came, and the report never caught up: "
        f"by_filter={got.report.by_filter}")
    assert calls["n"] == 2, f"premise: a failure then a retry, calls={calls}"


async def test_a_snapshot_write_that_keeps_failing_is_retried_exactly_once(
        monkeypatch):
    """The bound: a snapshot that fails on every attempt is written twice,
    never more -- the retry is not a loop. Both failures are logged, the
    first exactly as before #579 (``test_a_snapshot_write_is_not_retried``
    in test_report_load_reason.py pins that same text for the one-shot,
    no-loop path), the second naming the retry so the two are told apart on
    the bus.

    RED under the mutant "off_loop retry removed" (as above), observed:
        E   AssertionError: the retry is not bounded to one attempt: {'n': 1}
        E   assert 1 == 2
    """
    monkeypatch.setattr(report_mod, "_SNAPSHOT_RETRY_S", 0.0)
    lines = _warnings(monkeypatch)
    calls = _write_failing(monkeypatch, _denied, times=None)
    r = SessionReporter(SequencePlan(name="t"), report_id="snap-retry-twice",
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31", filter="L",
                               exposure_s=60.0))

    deadline = time.monotonic() + 5.0
    while len(lines) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    assert calls["n"] == 2, f"the retry is not bounded to one attempt: {calls}"
    assert [ln.split(" at ")[0] for ln in lines] == [
        "session report write failed (snapshot)",
        "session report write failed (snapshot, after one retry)"], lines
    assert all("reports/snap-retry-twice.json" in ln for ln in lines), lines
    # #579's retry never replaces #420's numbering/staleness rule: a report
    # that never wrote still answers "missing", not a half-written file.
    got = SessionReporter.read("snap-retry-twice")
    assert got.reason == "missing", (got.reason, got.report)
