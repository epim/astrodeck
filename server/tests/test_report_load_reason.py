"""A report read says why it found nothing (#370).

``test_unsafe_aborts_and_parks_under_remote_preset`` failed once in a loaded
full-suite run with ``assert None is not None``: ``SessionReporter.load``
answered ``None`` right after an unsafe abort had finalized the report. It
answered ``None`` for a missing file, for a file another handle held for a
moment and for a file that did not parse alike, and ``_persist`` swallowed a
failed write as one warning line, so the failure could not say which of #370's
three causes it was: the write failed, the read hit a transient lock while the
report was on disk, or the file was never there.

So ``SessionReporter.read`` is ``load`` with the reason kept (``missing`` is
the only one that means "not there"), a ``PermissionError`` is retried for half
a second before it is given up on, ``list_reports`` logs a file it cannot read
instead of dropping it in silence, and a failed FINAL write is retried once and
logged with the report's path. The path in a log line is capture-root-relative:
the owner's ruling is that no absolute path leaves the process
(tests/test_no_absolute_paths_externally.py), and the old warning carried
``str(OSError)``, which ends with the absolute file name.

The class is #364's: a transient I/O error read as "absent".

Every mutant below was run in a private copy of ``server/`` under
scratchpad/S5-REPORT-mut, never in the shared tree, from a byte backup of the
pristine ``report.py``, restored and compared by SHA-256 after each run.

The write tests here call ``finalize()`` with no running loop, so a failed
final write is retried inline, which blocks nothing. Called on a running
loop the retry runs on a thread of its own (#477, S7 orchestrator ruling 5),
and tests/test_s7_report_final_retry_off_loop.py grades that path. S7
re-ran #420's and #421's mutants against the new code, under
scratchpad/S7-REPORT-mut; each test below that they turn red says what S7
observed.
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.report as report_mod
from astrodeck.events import bus
from astrodeck.persist import PrivatePermissionsError
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import FrameRecord, SessionReporter


@pytest.fixture(autouse=True)
def captures(tmp_path, monkeypatch):
    """Reports in a root of this test's own, no real sleeping in the retry
    loops (the attempt counts are what is graded), and no warning remembered
    from another test."""
    root = tmp_path / "captures"
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", root)
    monkeypatch.setattr(report_mod, "_READ_BACKOFF_S", 0.0)
    monkeypatch.setattr(report_mod, "_FINAL_RETRY_S", 0.0)
    monkeypatch.setattr(report_mod, "_REPORTED_UNREADABLE", {})
    return root


@pytest.fixture
def warnings():
    """A callable answering the ``report`` warnings logged so far.

    Read off the real bus. Its storm limiter drops the fourth identical line
    inside a second, so the window is closed first and every test uses report
    ids of its own."""
    bus.flush()
    q = bus.subscribe()
    seen: list[str] = []

    def drain() -> list[str]:
        while True:
            try:
                ev = q.get_nowait()
            except asyncio.QueueEmpty:
                return seen
            d = ev.data
            if (ev.type == "log" and d.get("source") == "report"
                    and d.get("level") == "warning"):
                seen.append(d["message"])
    yield drain
    bus.unsubscribe(q)


def _write_report(report_id: str) -> Path:
    """A real report on disk, through the reporter's own final write."""
    r = SessionReporter(SequencePlan(name="t"), report_id=report_id,
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31", filter="Ha",
                               exposure_s=300.0))
    r.finalize("complete")
    return report_mod._reports_dir() / f"{report_id}.json"


def _denied(path: Path) -> PermissionError:
    """The error Windows gives a read while another handle has the file,
    carrying the absolute path in its text as the real one does."""
    return PermissionError(
        13, "The process cannot access the file because it is being used by "
            "another process", str(path), 32)


def _no_absolute_path(lines: list[str], captures: Path) -> None:
    """No spelling of the capture root in any line.

    Graded on the name of the test's own temporary directory, which every
    spelling of the root holds (#421). ``str(captures) not in line`` could
    not fail on Windows: ``str(OSError)`` shows its file name through
    ``repr``, with every backslash doubled, so the mutants that put ``str(e)``
    back in the warning passed it (observed: "16 passed" under both, before
    this)."""
    marker = captures.parent.name
    leaked = [ln for ln in lines if marker in ln]
    assert not leaked, f"an absolute path left the process: {leaked}"


def _read_json_failing(monkeypatch, path: Path, exc, times: int | None):
    """Make ``read_json`` raise ``exc(path)`` for ``path``, ``times`` times
    (``None``: always), and count every call made for it."""
    real = report_mod.read_json
    calls = {"n": 0}

    def fake(p):
        if Path(p) == path:
            calls["n"] += 1
            if times is None or calls["n"] <= times:
                raise exc(path)
        return real(p)
    monkeypatch.setattr(report_mod, "read_json", fake)
    return calls


# ---------------------------------------------------------------- read / load

def test_a_report_that_is_not_there_reads_as_missing(warnings):
    """The control: the one answer that means "not there", with no retry and
    no warning, and ``load`` still ``None`` as its routes expect.

    Unchanged under every mutant named in this file, as it must be: none of
    them touches a missing file."""
    got = SessionReporter.read("never-written")
    assert got.report is None
    assert got.reason == "missing"
    assert got.attempts == 1
    assert "FileNotFoundError" in got.detail
    assert SessionReporter.load("never-written") is None
    assert warnings() == []


def test_a_transient_permission_error_is_retried_and_the_report_loads(
        monkeypatch, warnings):
    """Two refused opens and then a clean one: the report loads, on the third
    attempt, and nothing is logged.

    RED under the mutant "PermissionError read as absent" (#370's shape:
    ``_read_report_file`` answers a ``PermissionError`` as it answers
    ``FileNotFoundError``, at once), observed:
        E   AssertionError: ('missing', 'PermissionError: [WinError 32] The
            process cannot access the file because it is being used by
            another process', 1)
        E   assert None is not None
    """
    path = _write_report("transient-read")
    calls = _read_json_failing(monkeypatch, path, _denied, times=2)
    got = SessionReporter.read("transient-read")
    assert got.report is not None, (got.reason, got.detail, got.attempts)
    assert got.report.end_reason == "complete"
    assert (got.reason, got.attempts, calls["n"]) == (None, 3, 3)
    assert warnings() == []


def test_a_lasting_permission_error_is_unreadable_not_missing(
        monkeypatch, warnings, captures):
    """A file refused on every try is "unreadable", after ``_READ_RETRIES``
    more tries, and says so on the bus, by its capture-root-relative path.
    ``load`` stays ``None``: to a route both are a 404.

    RED under the mutant "PermissionError read as absent", observed:
        E   AssertionError: ('missing', 'PermissionError: [WinError 32] The
            process cannot access the file because it is being used by
            another process')
        E   assert 'missing' == 'unreadable'

    RED under the mutant "the read detail carries str(e)" (``_described``
    returning ``{type}: {exc}``): the warning names the absolute path.
    """
    path = _write_report("lasting-read")
    calls = _read_json_failing(monkeypatch, path, _denied, times=None)
    got = SessionReporter.read("lasting-read")
    assert got.report is None
    assert got.reason == "unreadable", (got.reason, got.detail)
    assert got.attempts == calls["n"] == 1 + report_mod._READ_RETRIES
    assert got.detail.startswith("PermissionError: "), got.detail
    assert SessionReporter.load("lasting-read") is None
    lines = warnings()
    assert lines and "reports/lasting-read.json" in lines[0], lines
    assert "(unreadable)" in lines[0], lines
    _no_absolute_path(lines, captures)


@pytest.mark.skipif(os.name != "nt", reason="a Windows sharing violation")
def test_a_real_sharing_violation_is_waited_out(monkeypatch):
    """The real thing, not a fake: another handle opens the report with no
    sharing at all, as a writer mid-replace or a scanner can, and lets go
    150 ms later. The read is refused by the OS and then loads.

    ``attempts > 1`` is the premise that the OS really refused the first
    read; without it this test would pass on a machine where the handle did
    not block anything. The error the OS gives here, ``[Errno 13]``, is the
    one the #370 reproduction's failing reads carried.

    RED under the mutant "PermissionError read as absent", observed:
        E   AssertionError: ('missing', 'PermissionError: [Errno 13]
            Permission denied', 1)
        E   assert None is not None
    """
    import ctypes
    from ctypes import wintypes

    monkeypatch.setattr(report_mod, "_READ_BACKOFF_S", 0.05)
    path = _write_report("shared-read")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    generic_read, share_none, open_existing = 0x80000000, 0, 3
    handle = kernel32.CreateFileW(str(path), generic_read, share_none, None,
                                  open_existing, 0, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), \
        ctypes.get_last_error()
    release = threading.Timer(0.15, kernel32.CloseHandle, args=(handle,))
    release.start()
    try:
        got = SessionReporter.read("shared-read")
    finally:
        release.join()
    assert got.report is not None, (got.reason, got.detail, got.attempts)
    assert got.attempts > 1, "premise: the OS refused the first read"


@pytest.mark.parametrize("text, reason", [
    ("{not json", "corrupt"),
    ("[]", "invalid"),
    ('{"id": 1, "frames": "x"}', "invalid"),
])
def test_a_file_that_is_not_a_report_says_which(text, reason, captures,
                                                warnings):
    """Not JSON is "corrupt", JSON that is not a report "invalid", both read
    once (waiting will not make them parse) and both logged.

    RED under the mutant "a bad file read as absent" (``corrupt`` answered as
    ``missing``), for the first case only, the other two passing; observed:
        E   AssertionError: assert (None, 'missing', 1) == (None, 'corrupt', 1)
        E     At index 1 diff: 'missing' != 'corrupt'
    """
    rid = f"bad-{reason}-{len(text)}"
    reports = captures / "reports"
    reports.mkdir(parents=True)
    (reports / f"{rid}.json").write_text(text, encoding="utf-8")
    got = SessionReporter.read(rid)
    assert (got.report, got.reason, got.attempts) == (None, reason, 1)
    lines = warnings()
    assert len(lines) == 1 and f"({reason})" in lines[0], lines


# -------------------------------------------------------------- the writes

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


def test_a_final_write_that_fails_once_is_retried(monkeypatch, warnings,
                                                  captures):
    r"""The final write is refused once and succeeds on its one retry: the
    report is on disk with its ending, and the failure was logged at warning
    naming ``reports/<id>.json``, never the absolute path (#421).

    RED under the mutant "final write not retried" (``attempts = 1`` for a
    final write as for a snapshot), observed (the mutant also mislabels its
    only attempt "after one retry"):
        E   AssertionError: ('missing', 1, ['session report write failed
            (final report, after one retry) at reports/final-once.json:
            PermissionError: [WinError 32] The process cannot access the file
            because it is being used by another process'])
        E   assert None is not None

    RED under the mutant "the write warning carries str(e)" (the warning's
    error written as ``{type(e).__name__}: {e}``, the old ``{e}`` with the
    type kept so that only the path differs), observed, the temporary
    directory elided as <tmp>:
        E   AssertionError: an absolute path left the process: ["session
            report write failed (final report, retrying once) at
            reports/final-once.json: PermissionError: [WinError 32] The
            process cannot access the file because it is being used by
            another process: '<tmp>\\\\captures\\\\reports\\\\final-once.json'"]

    Re-run by S7 (#477), where two warnings now say a write failed, the
    first in ``_persist`` and the retry's in ``_retry_final``, with the
    mutant applied to both ("str(e) in the warning"): the same line,
    observed with only the elided directory differing.
    """
    calls = _write_failing(monkeypatch, _denied, times=1)
    r = SessionReporter(SequencePlan(name="t"), report_id="final-once",
                        started_at=1.0)
    r.finalize("unsafe")
    got = SessionReporter.read("final-once")
    assert got.report is not None, (got.reason, calls["n"], warnings())
    assert got.report.end_reason == "unsafe"
    assert calls["n"] == 2
    lines = warnings()
    assert len(lines) == 1, lines
    assert lines[0].startswith("session report write failed (final report, "
                               "retrying once) at reports/final-once.json: "
                               "PermissionError: "), lines
    _no_absolute_path(lines, captures)


def test_a_final_write_that_keeps_failing_says_so_twice_and_never_raises(
        monkeypatch, warnings, captures):
    r"""Refused on both tries: finalize() still returns the report (a disk
    hiccup must not kill a wind-down), and the bus has both failures, the
    second saying it came after the retry. Nothing is on disk, and ``read``
    says "missing", which is now the truth and not a guess.

    RED under the mutant "final write not retried", observed:
            assert calls["n"] == 2
        E   assert 1 == 2

    RED under the mutant "the write warning carries str(e)" as well, both
    lines named in the assertion. Re-run by S7 (#477) at the retry's
    warning alone ("str(e) in the retry's warning only"), observed, the
    temporary directory elided as <tmp>:
        E   AssertionError: an absolute path left the process: ["session
            report write failed (final report, after one retry) at
            reports/final-never.json: PermissionError: [WinError 32] The
            process cannot access the file because it is being used by
            another process: '<tmp>\\\\test_a_final_write_that_keeps_0\\\\
            captures\\\\reports\\\\final-never.json'"]
    """
    calls = _write_failing(monkeypatch, _denied, times=None)
    r = SessionReporter(SequencePlan(name="t"), report_id="final-never",
                        started_at=1.0)
    rep = r.finalize("unsafe")
    assert rep.end_reason == "unsafe"
    assert calls["n"] == 2
    lines = warnings()
    assert [ln.split(" at ")[0] for ln in lines] == [
        "session report write failed (final report, retrying once)",
        "session report write failed (final report, after one retry)"], lines
    assert all("reports/final-never.json" in ln for ln in lines), lines
    _no_absolute_path(lines, captures)
    assert SessionReporter.read("final-never").reason == "missing"


def test_a_snapshot_write_is_not_retried(monkeypatch, warnings):
    """The control for the retry: a snapshot that fails is written once and
    logged once, because the next snapshot or the final write replaces it.
    ``record_frame`` outside a running loop writes inline (``_write_sync``).

    Unchanged under every mutant named in this file, "final write not
    retried" included, as it must be."""
    calls = _write_failing(monkeypatch, _denied, times=None)
    r = SessionReporter(SequencePlan(name="t"), report_id="snap-once",
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31"))
    assert calls["n"] == 1
    lines = warnings()
    assert len(lines) == 1 and "(snapshot)" in lines[0], lines


def test_an_acl_refusal_on_the_final_write_is_caught_and_retried(
        monkeypatch, warnings):
    r"""``ensure_private_dir`` and ``harden_private_file`` raise their own
    RuntimeErrors, a transient sharing violation included. On a snapshot's
    worker thread that escaped into a task nobody awaits; on the final write
    it escaped finalize(). Now it is caught, logged and retried like an
    OSError.

    RED under the mutant "OSError only" (``_persist`` catching ``OSError``
    alone, as before), observed, the temporary directory elided as <tmp>:
        E   astrodeck.persist.PrivatePermissionsError: cannot secure private
            file <tmp>\captures\reports\final-acl.json

    And under "final write not retried" (``E   assert 1 == 2``).
    """
    calls = _write_failing(
        monkeypatch,
        lambda p: PrivatePermissionsError(f"cannot secure private file {p}"),
        times=1)
    r = SessionReporter(SequencePlan(name="t"), report_id="final-acl",
                        started_at=1.0)
    r.finalize("complete")
    assert calls["n"] == 2
    assert SessionReporter.read("final-acl").report is not None
    lines = warnings()
    assert len(lines) == 1 and "PrivatePermissionsError" in lines[0], lines


async def test_a_late_snapshot_does_not_replace_the_final_report(
        monkeypatch):
    """A snapshot built before finalize() whose worker thread reaches the
    write after it must not put the older report back over the final one.

    The worker is held at ``ensure_dir``, which it passes before taking the
    persist lock, until finalize() has written: the order a busy thread pool
    gives. #420, found while fixing #370 (a probe of the pristine code
    printed ``after finalize: complete`` and ``after the late snapshot:
    None``).

    RED under the mutant "no snapshot numbers" (``_persist`` writing whatever
    reaches the lock), observed:
        E   AssertionError: the snapshot built before finalize() was written
            after it
        E   assert None == 'complete'

    Re-run by S7 (#477) against the rule's new home, ``_write``, which the
    snapshot, the final write and its retry all go through: the same two
    lines, observed. test_s7_report_final_retry_off_loop.py holds the same
    rule for a retry made on its own thread.
    """
    gate = threading.Event()
    real_ensure = report_mod.ensure_dir
    main = threading.current_thread()

    def late_worker(p):
        if threading.current_thread() is not main:
            gate.wait(5)
        real_ensure(p)
    monkeypatch.setattr(report_mod, "ensure_dir", late_worker)

    r = SessionReporter(SequencePlan(name="t"), report_id="late-snap",
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31"))
    await asyncio.sleep(0.05)          # the snapshot is built and waiting
    r.finalize("complete")
    assert SessionReporter.load("late-snap").end_reason == "complete"
    gate.set()
    # let the late snapshot's task, and so its worker thread, finish
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending, "premise: the snapshot's task was still waiting"
    await asyncio.wait(pending, timeout=5)
    got = SessionReporter.read("late-snap")
    assert got.report is not None, got
    assert got.report.end_reason == "complete", (
        "the snapshot built before finalize() was written after it")


# ------------------------------------------------------------------ the list

def test_list_reports_logs_the_files_it_cannot_read(monkeypatch, warnings,
                                                    captures):
    """A good report, one refused on every read, one not JSON and one JSON
    list: the list holds the good one, and each of the other three is named
    on the bus and in ``scan_reports().unreadable`` instead of vanishing.

    Before #370 the first two were skipped in silence and the JSON list
    raised ``AttributeError`` out of ``list_reports`` (``raw.get`` on a list),
    a 500 on ``GET /api/reports`` from one stray file.

    RED under the mutant "list_reports as before #370" (its read and skip
    restored), observed:
        E   AttributeError: 'list' object has no attribute 'get'

    RED under "PermissionError read as absent", observed:
        E   AssertionError: assert [('junk-one.j...', 'invalid')] ==
            [('held-one.j...', 'invalid')]
        E     At index 0 diff: ('junk-one.json', 'corrupt') !=
            ('held-one.json', 'unreadable')

    RED under "a bad file read as absent" (``At index 1 diff:
    ('list-one.json', 'invalid') != ('junk-one.json', 'corrupt')``), and
    under "the read detail carries str(e)" (``_described`` returning
    ``{type}: {exc}``), whose line names the absolute path.
    """
    _write_report("good-one")
    held = _write_report("held-one")
    reports = captures / "reports"
    (reports / "junk-one.json").write_text("{not json", encoding="utf-8")
    (reports / "list-one.json").write_text("[]", encoding="utf-8")
    _read_json_failing(monkeypatch, held, _denied, times=None)

    scan = SessionReporter.scan_reports()
    assert [s["id"] for s in scan.summaries] == ["good-one"]
    assert sorted((u.path.name, u.reason) for u in scan.unreadable) == [
        ("held-one.json", "unreadable"), ("junk-one.json", "corrupt"),
        ("list-one.json", "invalid")]
    lines = warnings()
    assert sorted(ln.split(" could not")[0] for ln in lines) == [
        "session report reports/held-one.json",
        "session report reports/junk-one.json",
        "session report reports/list-one.json"], lines
    _no_absolute_path(lines, captures)
    assert [s["id"] for s in SessionReporter.list_reports()] == ["good-one"]


def test_list_reports_waits_out_a_transient_permission_error(monkeypatch,
                                                             warnings):
    """The list shares the single read's retry: a report refused twice is in
    the list, and nothing is logged.

    RED under the mutant "list_reports as before #370", and under
    "PermissionError read as absent", each observed as:
        E   AssertionError: assert [] == ['held-briefly']
        E     Right contains one more item: 'held-briefly'
    """
    held = _write_report("held-briefly")
    calls = _read_json_failing(monkeypatch, held, _denied, times=2)
    assert [s["id"] for s in SessionReporter.list_reports()] == [
        "held-briefly"]
    assert calls["n"] == 3
    assert warnings() == []


def test_a_file_gone_between_listing_and_reading_is_left_out_quietly(
        monkeypatch, warnings):
    """The control for the list's warning: a file deleted after the directory
    was listed is not there, and it is left out without a word.

    Unchanged under every mutant named in this file, "list_reports as before
    #370" included, as it must be: the old code skipped it too."""
    gone = _write_report("gone-one")
    _read_json_failing(monkeypatch, gone, FileNotFoundError, times=None)
    scan = SessionReporter.scan_reports()
    assert scan.summaries == [] and scan.unreadable == []
    assert warnings() == []


def test_an_unreadable_file_is_logged_once_not_on_every_list(monkeypatch,
                                                             warnings):
    """The list is read on every ``GET /api/reports`` and Tonight request, and
    the log ring holds 200 lines: a file that stays unreadable is logged once.
    It is logged again when it has read in between, since that is news.

    RED under the mutant "logged on every list" (``_warn_unreadable`` without
    its memory), observed:
        E   AssertionError: ['session report reports/held-long.json could not
            be read (unreadable): PermissionError: [WinError 32] The process
            can...
        E   assert 3 == 1

    And under "list_reports as before #370", which logged nothing at all
    (``E   assert 0 == 1``).
    """
    held = _write_report("held-long")
    real = report_mod.read_json
    refuse = {"on": True}

    def fake(p):
        if Path(p) == held and refuse["on"]:
            raise _denied(held)
        return real(p)
    monkeypatch.setattr(report_mod, "read_json", fake)

    for _ in range(3):
        assert SessionReporter.list_reports() == []
    assert len(warnings()) == 1, warnings()

    refuse["on"] = False
    assert [s["id"] for s in SessionReporter.list_reports()] == ["held-long"]
    refuse["on"] = True
    assert SessionReporter.list_reports() == []
    assert len(warnings()) == 2, warnings()
