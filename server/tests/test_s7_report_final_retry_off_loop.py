"""The report's sleeps stay off the event loop (#477, S7 orchestrator ruling 5).

Two sleeps live in ``sequence/report.py``, and both were blocking calls
waiting to be made on the loop thread.

THE FINAL WRITE. ``finalize()`` is synchronous, because the engine's wind-down
calls it that way, and it runs on the loop thread. Since #370 a failed final
write was retried after ``time.sleep(_FINAL_RETRY_S)``, and the sleep and the
retry's own write (with ``write_json_atomic``'s replace backoff inside it) ran
on the thread that called ``finalize()``. Every coroutine then waited: the
park, the guider stop, the warm ramp and the WS stream, at exactly the moment
a transient sharing violation had just been met. Now the first attempt stays
where it was, and the retry, sleep and write both, runs on a thread of its
own under the same snapshot numbers and ``_persist_lock`` (#420).

THE READS. ``_read_report_file`` backs off with ``time.sleep`` when a read is
refused (#370), and it stays synchronous: it is documented as thread-only, and
every production caller reaches it through ``asyncio.to_thread``. Nothing
proved that last part, so the second half of this file drives every route
that reads a report with the read patched to refuse a thread whose loop is
running, and an inventory of the callers keeps that list whole.

Every mutant below was run in a private copy of ``server/`` under the
session scratchpad's ``S7-REPORT-mut``, never in the shared tree, from a byte
backup of the pristine file, restored and compared by SHA-256 after each run.
"""
from __future__ import annotations

import ast
import asyncio
import threading
import time
import traceback
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck
import astrodeck.api.app as app_module
import astrodeck.sequence.report as report_mod
from astrodeck.config import Site
from astrodeck.events import bus
from astrodeck.flows.store import FlowStore
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.report import FrameRecord, SessionReporter
from astrodeck.sequence.session import Session, SessionFrame, session_store

from _deadline import wait_until


@pytest.fixture(autouse=True)
def _no_warning_remembered(monkeypatch):
    """No unreadable-report warning carried over from another test."""
    monkeypatch.setattr(report_mod, "_REPORTED_UNREADABLE", {})


def _loop_running_here() -> bool:
    """Whether the calling thread is running an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _denied(path: Path) -> PermissionError:
    """The error Windows gives while another handle has the file, carrying
    the absolute path in its text as the real one does."""
    return PermissionError(
        13, "The process cannot access the file because it is being used by "
            "another process", str(path), 32)


class _Clock:
    """``report_mod.time`` with its sleeps recorded: whether each was made
    on a thread running an event loop. Every other attribute is the real
    module's."""

    def __init__(self) -> None:
        self.sleeps: list[tuple[float, bool]] = []

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append((seconds, _loop_running_here()))
        time.sleep(seconds)


async def _landed(report_id: str, timeout: float = 5.0):
    """The report once it carries an ``end_reason``, read off the loop, or
    the last read after ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        got = await asyncio.to_thread(SessionReporter.read, report_id)
        if (got.report is not None and got.report.end_reason is not None) \
                or time.monotonic() > deadline:
            return got
        await asyncio.sleep(0.01)


def _warning_lines(monkeypatch) -> list[tuple[str, bool]]:
    """Each ``report`` warning as ``(message, logged on a loop thread)``.

    Captured at ``bus.log`` itself, so the shared ring and its storm limiter
    play no part."""
    out: list[tuple[str, bool]] = []
    real = bus.log

    def log(level, message, source="hub", **kw):
        if source == "report" and level == "warning":
            out.append((message, _loop_running_here()))
        return real(level, message, source, **kw)
    monkeypatch.setattr(bus, "log", log)
    return out


# ------------------------------------------------------------ the final write

async def test_a_failed_final_write_retries_while_the_loop_runs(monkeypatch):
    """The first final write is refused. A callback put on the loop during
    ``finalize()`` runs before the retry has written, and the report still
    lands with its ``end_reason``. No sleep in ``report.py`` is made on a
    thread running a loop.

    The retry's write waits for the callback (bounded at 3 s), so the order
    is decided by the code under test and not by the scheduler: on the loop,
    the retry cannot see the callback until it has itself finished.

    RED under the mutant "time.sleep on the loop" (``report.py`` as it was
    at b73b67f4: the sleep and the retry inline in ``_persist``), observed:
        E   AssertionError: the loop was held through the retry: its
            callback ran after 1 writes
        E   assert 1 == 0

    RED under the mutant "sleep before the hand-off" (``_persist`` making
    the sleep itself and handing only the write to the thread, which the
    callback alone cannot see), observed:
        E   AssertionError: a sleep was made on a thread running the loop:
            [(0.05, True)]
        E   assert [(0.05, True)] == []
    """
    monkeypatch.setattr(report_mod, "_FINAL_RETRY_S", 0.05)
    clock = _Clock()
    monkeypatch.setattr(report_mod, "time", clock)
    loop = asyncio.get_running_loop()
    ran = threading.Event()
    seen: dict[str, int] = {}
    writes = {"calls": 0, "done": 0}
    real = report_mod.write_json_atomic

    def callback() -> None:
        seen["writes_done"] = writes["done"]
        ran.set()

    def write(path, data, **kw):
        writes["calls"] += 1
        if writes["calls"] == 1:
            loop.call_soon(callback)
            raise _denied(Path(path))
        ran.wait(3)
        real(path, data, **kw)
        writes["done"] += 1
    monkeypatch.setattr(report_mod, "write_json_atomic", write)

    r = SessionReporter(SequencePlan(name="t"), report_id="retry-off-loop",
                        started_at=1.0)
    r.finalize("unsafe")
    # A wall-clock deadline (#610): 300 x sleep(0.01) is 3 s on Linux but
    # 4.7 s on Windows (sleep rounds up to the 15.6 ms timer there), so a
    # round count gives the two platforms different real patience.
    await wait_until(lambda: "writes_done" in seen, timeout_s=6.0,
                     interval_s=0.01)
    assert seen.get("writes_done") == 0, (
        f"the loop was held through the retry: its callback ran after "
        f"{seen.get('writes_done')} writes")
    got = await _landed("retry-off-loop")
    assert got.report is not None, (got.reason, writes)
    assert got.report.end_reason == "unsafe"
    assert writes == {"calls": 2, "done": 1}
    assert clock.sleeps, "premise: the retry waited _FINAL_RETRY_S"
    assert [s for s in clock.sleeps if s[1]] == [], (
        f"a sleep was made on a thread running the loop: {clock.sleeps}")


async def test_the_first_final_write_stays_on_the_callers_thread(
        monkeypatch):
    """The control, and the other half of the ruling: the first attempt
    stays in ``finalize()``. When it succeeds, the report is on disk by the
    time ``finalize()`` returns, written on the caller's thread, and nothing
    else is written.

    Unchanged under "time.sleep on the loop", as it must be: no write fails.

    RED under the mutant "the whole final write on a thread" (``finalize()``
    handing its first attempt to the retry's thread as well), observed (the
    thread had not written yet; had it, the list would read ``[False]``):
        E   AssertionError: the first attempt left finalize(): []
        E   assert [] == [True]
    """
    on_loop: list[bool] = []
    real = report_mod.write_json_atomic

    def write(path, data, **kw):
        on_loop.append(_loop_running_here())
        real(path, data, **kw)
    monkeypatch.setattr(report_mod, "write_json_atomic", write)

    r = SessionReporter(SequencePlan(name="t"), report_id="first-stays",
                        started_at=1.0)
    r.finalize("complete")
    assert on_loop == [True], f"the first attempt left finalize(): {on_loop}"
    got = SessionReporter.read("first-stays")
    assert got.report is not None and got.report.end_reason == "complete"
    await asyncio.sleep(0.05)
    assert on_loop == [True], "a write that succeeded was written again"


async def test_a_snapshot_older_than_the_retry_does_not_replace_it(
        monkeypatch):
    """#420 across the new hand-off: a snapshot built before ``finalize()``
    whose worker thread reaches the lock after the retried final report
    must not put the older report back.

    The snapshot's worker is held at ``ensure_dir``, which it passes before
    it takes the persist lock, until the retry has landed. The retry's own
    thread is let through (only the first thread off the loop is held).

    RED under the mutant "the retry outside the rule" (the retry writing
    with ``write_json_atomic`` directly, not under ``_persist_lock`` and
    the snapshot numbers), observed:
        E   AssertionError: the snapshot built before finalize() was
            written after its retry
        E   assert None == 'unsafe'

    And under #420's own mutant "no snapshot numbers" (the check removed
    from ``_write``), the same two lines, beside the #420 test's own
    failure in test_report_load_reason.py.
    """
    monkeypatch.setattr(report_mod, "_FINAL_RETRY_S", 0.0)
    gate = threading.Event()
    held: dict[str, int] = {}
    main = threading.get_ident()
    real_ensure = report_mod.ensure_dir

    def ensure(p):
        me = threading.get_ident()
        if me != main and held.setdefault("thread", me) == me:
            gate.wait(5)
        real_ensure(p)
    monkeypatch.setattr(report_mod, "ensure_dir", ensure)

    writes = {"n": 0}
    real_write = report_mod.write_json_atomic

    def write(path, data, **kw):
        writes["n"] += 1
        if data.get("end_reason") == "unsafe" and writes["n"] == 1:
            raise _denied(Path(path))
        real_write(path, data, **kw)
    monkeypatch.setattr(report_mod, "write_json_atomic", write)

    r = SessionReporter(SequencePlan(name="t"), report_id="late-vs-retry",
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31"))
    # The snapshot is built and held, on a wall-clock deadline (#610): 500 x
    # sleep(0.01) is 5 s on Linux but 7.8 s on Windows (sleep rounds up to
    # the 15.6 ms timer there), so a round count gives the two platforms
    # different real patience.
    await wait_until(lambda: "thread" in held, timeout_s=10.0,
                     interval_s=0.01)
    assert "thread" in held, "premise: the snapshot's worker is held"
    r.finalize("unsafe")
    got = await _landed("late-vs-retry")
    assert got.report is not None and got.report.end_reason == "unsafe", (
        "premise: the retry landed", got)
    gate.set()
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending, "premise: the snapshot's task was still waiting"
    await asyncio.wait(pending, timeout=5)
    got = SessionReporter.read("late-vs-retry")
    assert got.report is not None, got
    assert got.report.end_reason == "unsafe", (
        "the snapshot built before finalize() was written after its retry")


async def test_a_retry_that_fails_again_is_said_on_the_loop(monkeypatch):
    r"""Refused on both tries under a running loop: ``finalize()`` still
    returns the report, and both failures reach the bus, the second from the
    loop thread rather than the retry's own.

    ``bus.log`` fans out to ``asyncio.Queue`` subscribers, which belong to
    the loop; a line put in from another thread is not delivered until the
    loop next wakes, and under asyncio's debug mode it raises
    "Non-thread-safe operation invoked on an event loop other than the
    current one" (probed 2026-09-28, #480). The retry's line is handed to
    the loop with ``call_soon_threadsafe`` for that reason.

    RED under the mutant "the retry logs from its own thread" (its warning
    through ``bus.log`` directly), observed:
        E   AssertionError: [('session report write failed (final report,
            retrying once) at reports/retry-twice.json: PermissionError:
            [WinError 3...PermissionError: [WinError 32] The process cannot
            access the file because it is being used by another process',
            False)]
        E   assert [True, False] == [True, True]

    RED under #421's mutant "str(e) in the warning" (both warnings written
    as ``{type(e).__name__}: {e}``), where both lines carry the root and
    the first is named, observed (re-run by the S7 verifier), the temporary
    directory elided as <tmp>:
        E   assert not ["session report write failed (final report,
            retrying once) at reports/retry-twice.json: PermissionError:
            [WinError 32...<tmp>\\\\astrodeck-test-captures-r980jw9r\\\\t3
            \\\\reports\\\\retry-twice.json'"]
    And under "str(e) in the retry's warning only", where only the retry's
    line does, observed:
        E   assert not ["session report write failed (final report, after
            one retry) at reports/retry-twice.json: PermissionError:
            [WinError ...<tmp>\\\\astrodeck-test-captures-thqkt9j7\\\\t3
            \\\\reports\\\\retry-twice.json'"]
    """
    monkeypatch.setattr(report_mod, "_FINAL_RETRY_S", 0.0)
    lines = _warning_lines(monkeypatch)
    calls = {"n": 0}

    def write(path, data, **kw):
        calls["n"] += 1
        raise _denied(Path(path))
    monkeypatch.setattr(report_mod, "write_json_atomic", write)

    r = SessionReporter(SequencePlan(name="t"), report_id="retry-twice",
                        started_at=1.0)
    rep = r.finalize("unsafe")
    assert rep.end_reason == "unsafe"
    # A wall-clock deadline (#610): see the first case in this file for why
    # a round count of sub-0.1 s sleeps is platform-dependent.
    await wait_until(lambda: len(lines) >= 2, timeout_s=6.0, interval_s=0.01)
    assert calls["n"] == 2
    assert [m.split(" at ")[0] for m, _ in lines] == [
        "session report write failed (final report, retrying once)",
        "session report write failed (final report, after one retry)"], lines
    assert all("reports/retry-twice.json" in m for m, _ in lines), lines
    # #421 on the retry's own line: no spelling of the capture root. Graded
    # on the name of the directory conftest makes the per-test roots in
    # (``<that>/t<n>/reports``), which every spelling holds, since
    # ``str(OSError)`` shows its path through ``repr`` with doubled
    # backslashes.
    marker = report_mod._reports_dir().parent.parent.name
    assert len(marker) > 8, f"premise: a distinctive root name, {marker!r}"
    assert not [m for m, _ in lines if marker in m], lines
    assert [where for _, where in lines] == [True, True], lines


# ------------------------------------------------------------------ the reads

#: What ``_read_report_file``'s guard found: every call's thread, whether it
#: was running a loop, and the ``app.py`` frames that led to it.
class _ReadGuard:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on_loop(self) -> list[dict]:
        return [c for c in self.calls if c["on_loop"]]


@pytest.fixture
def read_guard(monkeypatch):
    """``_read_report_file`` patched to record every call and to raise on a
    thread whose loop is running, then read as it would.

    It records as well as raising because not every caller lets the raise
    through: the Tonight route's campaign fold catches ``Exception`` and
    reads a refused ledger as no ledger, which would hide an on-loop read.
    """
    guard = _ReadGuard()
    real = report_mod._read_report_file

    def guarded(path):
        frames = [f"{Path(f.filename).name}:{f.name}"
                  for f in traceback.extract_stack()
                  if Path(f.filename).name in ("app.py", "report.py",
                                               "tonight.py")]
        call = {"thread": threading.current_thread().name,
                "on_loop": _loop_running_here(), "frames": frames}
        guard.calls.append(call)
        if call["on_loop"]:
            raise RuntimeError(
                f"a report read on the event loop thread: {frames}")
        return real(path)
    monkeypatch.setattr(report_mod, "_read_report_file", guarded)
    return guard


#: A POOL armed by a DUSK, so Tonight reads both ledgers: the budget's
#: ``banked`` (every flow) and the campaign's ``frames_by_target`` (a flow
#: with a pool). The capture has a goal, which is what gives it a budget
#: row to carry ``has_ledger``.
_POOL_GRAPH = {
    "nodes": [
        {"id": "d", "type": "dusk", "x": 30, "y": 60,
         "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
        {"id": "p", "type": "pool", "x": 260, "y": 60,
         "params": {"members": "M42, M31", "quota": 10}},
        {"id": "c", "type": "capture", "x": 490, "y": 60,
         "params": {"filter": "L", "exposure": 120, "gain": 100, "bin": "1",
                    "count": 12, "goal": 2}},
    ],
    "edges": [
        {"from": "d", "fromPort": "window", "to": "p", "toPort": "arm"},
        {"from": "p", "fromPort": "target", "to": "c", "toPort": "run"},
    ],
}

_RID = "offloop-20260928-010101"

#: Every request that reads a report, with the reads it makes when one
#: report is on disk (``(fewest, most)``, None for no bound) and the
#: statuses it may answer. The materialize route answers 400 here, after
#: its read, because the seeded frame is not a local file. Tonight's count
#: is a floor: how its two ledgers share their reads is the route's
#: business (#419 made them load each report once for both), and where the
#: reads run is this file's. ``{fid}`` is the stored flow's id.
#
# SINCE H4 (re-pinned by the H4 integration):
#   * Tonight folds the reports' ledger SUMMARIES (#536), and reads a report
#     in full only when it has no current summary. ``finalize`` writes one, so
#     against this fixture's finalized report Tonight made no read at all and
#     its floor of one failed ("reached the read 0 times"). The fixture now
#     removes the summary, so Tonight makes its one-time read, the only read
#     of a report that route still makes, and it is that read the exercise
#     puts off the loop. test_h4_report_summary_cost.py grades the summary
#     path itself, the read count and its own off-loop case.
#   * The recoverable route reads the dormant session's last report to say
#     why it stopped (#487), a new report reader, which the inventory below
#     found undriven. The fixture seeds a dormant session with a frame whose
#     last run is the report on disk, so the route reaches the read once.
_EXERCISE = [
    ("GET", "/api/reports", (1, 1), {200}),
    ("GET", f"/api/reports/{_RID}", (1, 1), {200}),
    ("GET", f"/api/reports/{_RID}/frames.csv", (1, 1), {200}),
    ("GET", f"/api/reports/{_RID}/bundle", (1, 1), {200}),
    ("GET", f"/api/reports/{_RID}/bundle.zip", (1, 1), {200}),
    ("POST", f"/api/reports/{_RID}/bundle/materialize", (1, 1), {200, 400}),
    ("GET", "/api/sequence/recoverable", (1, 1), {200}),
    ("GET", "/api/flows/{fid}/tonight", (1, None), {200}),
]


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """conftest's ``isolated_config`` with a site of the test's own (a
    fixture latitude, not the rig's), a flow library of its own and one
    report on disk. Server errors come back as responses, so a guard that
    raised is graded by the guard's record and not by a traceback."""
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    isolated_config.store.set_site(
        Site(name="fixture", latitude=40.0, longitude=-74.0,
             elevation_m=10.0, is_default=False),
        expected_version=None)
    r = SessionReporter(SequencePlan(name="offloop"), report_id=_RID,
                        started_at=1.0)
    r.record_frame(FrameRecord(ts=1.0, target="M31", filter="L",
                               exposure_s=120.0))
    r.finalize("complete")
    # No ledger summary (#536): Tonight then reads the report itself, once,
    # which is the read this file puts off the loop. Written inline by the
    # finalize above (no loop runs here), so it is there to remove.
    summary = report_mod._summary_path(_RID)
    assert summary.is_file(), f"premise: finalize wrote {summary.name}"
    summary.unlink()
    # A dormant session with a frame whose last run is that report, so the
    # recoverable route reads it to say why the run stopped (#487).
    target = Target(name="M31", ra_hours=0.7, dec_deg=41.3,
                    steps=[ExposureStep(filter="L", exposure_s=120.0,
                                        count=1)])
    s = Session(name="offloop", created_ts=1.0, updated_ts=2.0,
                status="dormant", plan=SequencePlan(name="offloop",
                                                    targets=[target]),
                nights=[_RID],
                frames=[SessionFrame(night=_RID, target_id=target.id,
                                     step_id=target.steps[0].id,
                                     auto_accepted=True)])
    write_json_atomic(session_store._path(s.id), s.model_dump(), backup=False)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_every_route_that_reads_a_report_reads_it_off_the_loop(client,
                                                               read_guard):
    """Each request in ``_EXERCISE`` reaches ``_read_report_file`` as many
    times as it should, and never on a thread running the loop.

    The counts are the premise: a request that never reached the read (a
    404 before it, a Tonight answer that stopped at "no site") would pass
    the loop check by reading nothing. Tonight's reads are its two ledgers,
    ``banked`` and ``frames_by_target``, called by ``resolve_tonight`` on
    its worker thread; the next test shows both were answered.

    RED under the mutant "one route calls SessionReporter.load on the loop"
    (``get_report`` calling ``SessionReporter.load(report_id)`` directly),
    observed:
        E   AssertionError: GET /api/reports/offloop-20260928-010101 read a
            report on the loop: [{'thread': 'asyncio-portal-28e27515a90',
            'on_loop': True, 'frames': ['app.py:get_report',
            'report.py:load', 'report.py:read']}]

    RED under the mutant "Tonight's ledger read on the loop" (the route
    calling its cached ``reports()`` before ``asyncio.to_thread`` and
    handing the budget's lambda the answer), observed:
        E   AssertionError: GET /api/flows/5ca9db4df6da4186b386c6c7643caeb1/
            tonight read a report on the loop: [{'thread':
            'asyncio-portal-1a6d7e235c0', 'on_loop': True, 'frames':
            ['app.py:flow_tonight', 'app.py:reports',
            'report.py:list_reports', 'report.py:scan_reports']}]
    It was red the same way against the route before #419 gave it
    ``reports()``, when each lambda listed the reports itself and the
    mutant listed them on the loop instead.

    RE-PINNED FOR H4 (the fixture's summary removed, the recoverable route
    added; see ``_EXERCISE``), and re-run against the H4 routes:

    RED under H4-ROUTES-A's mutant "summaries read on the loop" (the route
    calling ``SessionReporter.summaries()`` before ``asyncio.to_thread`` and
    handing both folds the answer), observed:
        E   AssertionError: GET /api/flows/93e1d16a921f46c4b3bd6bd8dacd19ce/
            tonight read a report on the loop: [{'thread':
            'asyncio-portal-19b56e16870', 'on_loop': True, 'frames':
            ['app.py:flow_tonight', 'report.py:summaries',
            'report.py:_read_at']}]

    RED under "recoverable reads on the loop" (``sequence_recoverable``
    calling ``SessionReporter.read(s.nights[-1])`` directly), observed:
        E   AssertionError: GET /api/sequence/recoverable read a report on
            the loop: [{'thread': 'asyncio-portal-216fd29bef0', 'on_loop':
            True, 'frames': ['app.py:sequence_recoverable',
            'report.py:read', 'report.py:_read_at']}]
    """
    fid = client.post("/api/flows", json={"flow": {
        "name": "Off the loop", "folder": "My flows",
        "graph": _POOL_GRAPH}}).json()["id"]
    for method, path, (fewest, most), statuses in _EXERCISE:
        path = path.format(fid=fid)
        before = len(read_guard.calls)
        resp = client.request(method, path)
        mine = read_guard.calls[before:]
        on_loop = [c for c in mine if c["on_loop"]]
        assert not on_loop, f"{method} {path} read a report on the loop: {on_loop}"
        assert resp.status_code in statuses, (method, path, resp.status_code,
                                              resp.text[:300])
        assert fewest <= len(mine) and (most is None or len(mine) <= most), (
            f"premise: {method} {path} reached the read {len(mine)} times, "
            f"not {fewest} to {most}: {mine}")
    assert read_guard.calls and not read_guard.on_loop()


def test_tonight_really_read_both_ledgers(client, read_guard):
    """The Tonight request answered both ledgers, off the loop: every
    budget row and the campaign say a ledger was read, and every read the
    request made ran on a thread with no loop. Without this the floor above
    could be met by one ledger with the other never called.

    A ledger that raised would say so here as well: the budget catches an
    ``OSError`` or ``ValueError`` and the campaign any ``Exception``, and
    each then answers ``has_ledger`` False.

    Unchanged under "one route calls SessionReporter.load on the loop", as
    it must be: Tonight is not that route. Under "Tonight's ledger read on
    the loop" it is red too, the guard's raise turning the answer into a
    500 with a plain-text body, observed:
        E   json.decoder.JSONDecodeError: Expecting value: line 1 column 1
            (char 0)

    Since H4 the two ledgers fold the reports' summaries (#536), and the
    fixture's report has none, so the one read here is the one-time read of
    the report itself. Under H4-ROUTES-A's "summaries read on the loop" it is
    red the same way (re-run by the H4 integration), observed:
        E   json.decoder.JSONDecodeError: Expecting value: line 1 column 1
            (char 0)
    """
    fid = client.post("/api/flows", json={"flow": {
        "name": "Both ledgers", "folder": "My flows",
        "graph": _POOL_GRAPH}}).json()["id"]
    body = client.get(f"/api/flows/{fid}/tonight").json()
    assert body.get("ok"), body
    assert body["budget"], "premise: the pool's members have budget rows"
    assert all(row["has_ledger"] is True for row in body["budget"]), (
        body["budget"])
    assert body["campaign"]["has_ledger"] is True, body["campaign"]
    assert read_guard.calls and not read_guard.on_loop(), read_guard.calls


# ------------------------------------------------------- the inventory of callers

#: ``SessionReporter``'s methods that read a report file. ``summaries`` since
#: H4 (#536): it reads every report that has no current summary, and it is
#: the only reader the Tonight route names now.
_READERS = {"read", "load", "list_reports", "scan_reports", "attach_existing",
            "summaries"}


def _callers() -> set[tuple[str, tuple[str, ...]]]:
    """``(module, enclosing defs)`` of every reference to a report reader
    outside ``sequence/report.py``, the defs outermost first: a route's
    reference in ``app.py`` is ``("create_app", "<route>", ...)``, with any
    helper the route nests after it (#419's ``reports()`` inside
    ``flow_tonight``).

    A reference, not only a call: ``asyncio.to_thread(SessionReporter.load,
    rid)`` passes the method without calling it, and is the shape every
    route uses."""
    root = Path(astrodeck.__file__).parent
    out: set[tuple[str, tuple[str, ...]]] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "sequence/report.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, where: tuple[str, ...]) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                where = (*where, node.name)
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert not (alias.name == "SessionReporter"
                                and alias.asname), (
                        f"{rel} imports SessionReporter as {alias.asname}, "
                        f"which this inventory would not see")
            hit = (
                (isinstance(node, ast.Attribute) and node.attr in _READERS
                 and isinstance(node.value, ast.Name)
                 and node.value.id == "SessionReporter")
                or (isinstance(node, ast.Attribute)
                    and node.attr == "_read_report_file")
                or (isinstance(node, ast.Name)
                    and node.id == "_read_report_file"))
            if hit:
                out.add((rel, where))
            for child in ast.iter_child_nodes(node):
                visit(child, where)
        visit(tree, ())
    return out


def test_the_exercise_covers_every_production_reader():
    """Every place outside ``report.py`` that names a report reader is
    inside a route the exercise above drives, found by the route table from
    the exercise's own paths, and every route driven names one. A new
    caller fails here until it is driven there.

    ``attach_existing`` has no production caller today; a caller that
    appears must be added to the exercise too.

    RED under the mutant "a reader nobody drives" (a ``SessionReporter.load``
    added to the engine's ``_finalize_report``), observed:
        E   AssertionError: report readers the exercise does not drive:
            ['sequence/engine.py:_finalize_report']

    The scan is by name, so a reader reached through an alias would slip
    it; an aliased import of ``SessionReporter`` fails here instead.

    It did its job at H4: the recoverable route's new read of the dormant
    session's last report (#487) was found undriven before the exercise
    named it, observed on the shared tree:
        E   AssertionError: report readers the exercise does not drive:
            ['api/app.py:create_app.sequence_recoverable']
    """
    app = app_module.create_app()
    driven: set[str] = set()
    for method, path, _reads, _statuses in _EXERCISE:
        template = (path.replace(_RID, "{report_id}")
                    .replace("{fid}", "{flow_id}"))
        endpoint = next(
            (r.endpoint for r in app.routes
             if getattr(r, "path", None) == template
             and method in getattr(r, "methods", ())), None)
        assert endpoint is not None, f"no route {method} {template}"
        driven.add(endpoint.__name__)
    found = _callers()
    assert found, "premise: the scan found the routes' readers"
    undriven = sorted(f"{rel}:{'.'.join(where) or '<module>'}"
                      for rel, where in found
                      if not (rel == "api/app.py" and driven & set(where)))
    assert not undriven, (
        f"report readers the exercise does not drive: {undriven}")
    reading = {name for rel, where in found if rel == "api/app.py"
               for name in where}
    assert not driven - reading, (
        f"the exercise drives routes that read no report: "
        f"{sorted(driven - reading)}")
