"""The ledger's summaries, and what the Tonight route costs with thousands of
reports on disk (#536, H4 orchestrator ruling 6).

THE DEFECT. ``GET /api/flows/{id}/tonight`` folds the session ledger twice,
BUDGET's hours per filter and CAMPAIGN's frames per target, and since #419 it
did so by calling ``SessionReporter.load`` for every entry of
``list_reports()``. ``list_reports`` itself reads every report file in full to
build its eight scalars, so each open of the Tonight sheet read the whole
archive twice, and on Windows each of those reads first pays the private-ACL
check ``read_json`` makes, about 4 ms a file before a byte is parsed.

THE FIX. ``SessionReporter.finalize`` (every write of a finished report, in
fact: ``_write``) writes a small summary beside the report, in
``captures/reports/summaries``: per filter, per target and over the report,
the accepted frames and accepted light integration (``report_summary``),
stamped with the report file's size and modification time. ``SessionReporter.summaries`` walks
the reports directory once without opening a report, reads each summary whose
stamp is the file's own, and reads a report in full only when it has no
usable summary, writing one then. That is the documented rule for a report
finalised before summaries existed: BUILT ONCE AND WRITTEN, never read as 0.

THE MEASUREMENT (the reproduce command, ``python tests/test_h4_report_summary_
cost.py``, on the tree as submitted). The development box is an Intel Core
Ultra 9 275HX running Windows 11 and Python 3.12.10, loaded by other agents'
suites throughout. Each report is one night of one target with four Ha subs,
its file a real ``SessionReport`` dump. In ms: the first ``summaries()`` read
after the archive was written, then, after one warm-up call, the median /
worst of 7 ``summaries()`` reads and of 3 of #419's reads (``list_reports``,
then ``load`` for each entry):

    reports   first read   summaries()    #419's read
       1000         3000     52 /    96    1146 / 1156
       3000         8909    151 /   187    3531 / 3565

The first read is not the summaries' cost. cProfile put 8 ms of every 8.6 ms
of it in ``open`` of a file written moments before (the antivirus scanning a
new file on its first open); read again, the same files cost the warm column.
On the rig a night's summary is opened for the first time on the next Tonight
read, one new file a night. #419's first read of a cold archive measured 6 s
at 1000 reports and 20 s at 3000 on the same box. The Tonight route itself,
2000 summarised reports on disk, answered in 286 to 521 ms warm (its own
astropy passes included), against 14.7 s for its first read of those files
just after writing them. A report without a summary costs one #419-sized
read, once.

THE BUDGET, pinned below as deterministic assertions:

* ZERO full report reads (``report._read_report_file``, the one function
  every report read goes through) by the Tonight route when every report has
  a current summary, for BUDGET and CAMPAIGN alike, at ``ARCHIVE`` reports;
* ONE summary read per report per request, shared by both folds;
* a report with no summary read in full ONCE, and never again once its
  summary is written.

The wall clock is recorded above, not asserted: a loaded Windows box moves
it by multiples (the #124 class), and the counts are what a regression
changes.

Every named mutant ran in a private copy of ``server/`` under the session
scratchpad (``H4-ROUTES-A-mut``), from a byte backup of the file mutated,
restored and SHA-256 compared after each run, never in the shared tree
(#254). The failure each produced is quoted where it went red.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.report as report_mod
from astrodeck.config import Site
from astrodeck.events import bus
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import (SUMMARY_SCHEMA, FilterBreakdown,
                                       FrameRecord, SessionReport,
                                       SessionReporter, TargetBreakdown,
                                       report_summary)
from test_flows_progress_route import (SITE_A, _restore_provider,  # noqa: F401
                                       api)

#: Reports on disk for the cost test: "thousands".
ARCHIVE = 2000

#: A POOL of M31 and M33 feeding a one-slot FILTER CYCLE of 900 s Ha: the
#: CAMPAIGN fold counts each member's Ha subs, and BUDGET's cycle row banks
#: what the flow's targets hold in Ha. Both folds run on one request.
POOL_HA = {
    "nodes": [
        {"id": "p", "type": "pool", "x": 0, "y": 0,
         "params": {"members": "M31, M33", "quota": 4, "minAlt": 0,
                    "moonSep": 0, "maxHA": 0}},
        {"id": "y", "type": "cycle", "x": 100, "y": 0,
         "params": {"plan": "Ha 900", "cycles": 4, "perCycle": 1,
                    "gain": 100, "bin": "1"}}],
    "edges": [{"from": "p", "fromPort": "target", "to": "y",
               "toPort": "run"}]}


@pytest.fixture(autouse=True)
def _no_warning_remembered(monkeypatch):
    """No unreadable or unwritable warning carried over from another test."""
    monkeypatch.setattr(report_mod, "_REPORTED_UNREADABLE", {})
    monkeypatch.setattr(report_mod, "_REPORTED_UNWRITABLE", {})


# ------------------------------------------------------------------ builders

def _finalized(report_id: str, target: str = "M31",
               frames: list[FrameRecord] | None = None,
               ) -> SessionReporter:
    """A report written and finalized by the engine's own writer, with no
    loop running (each snapshot written inline)."""
    rep = SessionReporter(SequencePlan(name="n"), report_id=report_id,
                          started_at=1_790_000_000.0)
    for fr in frames if frames is not None else [
            FrameRecord(ts=1.0 + i, target=target, filter="Ha",
                        exposure_s=900.0) for i in range(4)]:
        rep.record_frame(fr)
    rep.finalize("complete")
    return rep


def _summary_file(report_id: str) -> Path:
    return report_mod._summaries_dir() / f"{report_id}.json"


def _report_file(report_id: str) -> Path:
    return report_mod._reports_dir() / f"{report_id}.json"


class _Reads:
    """``_read_report_file`` counted: every full report read, with whether
    it was made on a thread running an event loop."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, bool]] = []
        real = report_mod._read_report_file

        def spy(path):
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            self.calls.append((Path(path).name, on_loop))
            return real(path)
        monkeypatch.setattr(report_mod, "_read_report_file", spy)


def _one_night(i: int) -> SessionReport:
    """Night ``i`` of the archive: four accepted 900 s Ha subs, of M31 on
    even nights and of M16 on odd ones, as the engine's writer dumps it."""
    target = "M31" if i % 2 == 0 else "M16"
    ha = FilterBreakdown(filter="Ha", frames=4, integration_s=3600.0,
                         hfr_median=2.1)
    return SessionReport(
        id=f"night-{i:05d}", plan_name="archive",
        started_at=1_700_000_000.0 + 86400.0 * i,
        ended_at=1_700_000_000.0 + 86400.0 * i + 3600.0,
        end_reason="complete", frames_captured=4, integration_s=3600.0,
        by_filter=[ha], targets=[TargetBreakdown(
            name=target, frames=4, integration_s=3600.0, by_filter=[ha])],
        frames=[FrameRecord(ts=1_700_000_000.0 + 86400.0 * i + 900.0 * k,
                            target=target, filter="Ha", exposure_s=900.0,
                            hfr=2.1, sensor_temp_c=-10.0)
                for k in range(4)])


def _archive(n: int, *, summarised: bool = True) -> None:
    """``n`` finished reports on disk, and their current summaries when
    ``summarised``. Written as plain files: the reports' own writer takes
    about 5 ms a file on Windows (the ACL work), and a summary that the
    reader accepts is exactly ``report_summary`` of the report stamped with
    the file's stat, which is what ``_write`` writes."""
    rdir, sdir = report_mod._reports_dir(), report_mod._summaries_dir()
    rdir.mkdir(parents=True, exist_ok=True)
    sdir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        rep = _one_night(i)
        path = rdir / f"{rep.id}.json"
        path.write_text(json.dumps(rep.model_dump()), encoding="utf-8")
        if summarised:
            (sdir / path.name).write_text(json.dumps(report_summary(
                rep, report_mod._source(path.stat()))), encoding="utf-8")


async def _tonight(api, fid: str) -> dict:
    api.store.set_site(Site(name="fixture", latitude=SITE_A[0],
                            longitude=SITE_A[1], elevation_m=10.0,
                            is_default=False))
    r = await api.client.get(f"/api/flows/{fid}/tonight")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ok"] is True, f"premise: the site is set: {got['reason']}"
    return got


# ============================================================ the summary

class TestFinalizeWritesTheSummary:
    def test_it_holds_the_accepted_frames_and_integration_per_target_and_filter(
            self):
        """Three accepted and one rejected 900 s Ha of M31, two accepted
        300 s L of M31, and one accepted Ha of M16: the summary is the
        report's breakdown of accepted work, per target and over the report,
        the reject left out, stamped with the stat of the report file as
        written.

        RED under mutant "finalize writes no summary" (``_write``'s
        summary step removed), observed:

            AssertionError: finalize wrote no summary
            assert False
        """
        frames = ([FrameRecord(ts=1.0 + i, target="M31", filter="Ha",
                               exposure_s=900.0) for i in range(3)]
                  + [FrameRecord(ts=9.0, target="M31", filter="Ha",
                                 exposure_s=900.0, accepted=False)]
                  + [FrameRecord(ts=10.0 + i, target="M31", filter="L",
                                 exposure_s=300.0) for i in range(2)]
                  + [FrameRecord(ts=20.0, target="M16", filter="Ha",
                                 exposure_s=900.0)])
        _finalized("summary-shape", frames=frames)
        path = _summary_file("summary-shape")
        assert path.is_file(), "finalize wrote no summary"
        got = json.loads(path.read_text(encoding="utf-8"))
        st = _report_file("summary-shape").stat()
        assert got == {
            "schema": SUMMARY_SCHEMA, "id": "summary-shape",
            "source": {"size": st.st_size, "mtime_ns": st.st_mtime_ns},
            "by_filter": [
                {"filter": "Ha", "frames": 4, "integration_s": 3600.0},
                {"filter": "L", "frames": 2, "integration_s": 600.0}],
            "targets": [
                {"name": "M31", "by_filter": [
                    {"filter": "Ha", "frames": 3, "integration_s": 2700.0},
                    {"filter": "L", "frames": 2, "integration_s": 600.0}]},
                {"name": "M16", "by_filter": [
                    {"filter": "Ha", "frames": 1, "integration_s": 900.0}]}],
        }

    def test_a_summary_is_not_a_report_on_the_list(self):
        """The summaries live in a directory beside the reports, so the
        operator's report list (``list_reports``, ``GET /api/reports``)
        still lists one night.

        RED under mutant "summary among the reports" (``_summary_path``
        answering ``_reports_dir() / f"{stem}.summary.json"``), observed:

            AssertionError: assert ['list-once', 'list-once'] ==
            ['list-once']

        (the summary file, listed as a night, carries the report's id).
        """
        _finalized("list-once")
        assert [s["id"] for s in SessionReporter.list_reports()] == [
            "list-once"]

    async def test_a_snapshot_that_lands_after_finalize_moves_the_summary(
            self, monkeypatch):
        """The engine records its last frame and finalizes on the loop; the
        last frame's snapshot, scheduled as a task, runs after finalize and
        rewrites the finished report (identical but for its stat). The
        summary follows the file, so the next Tonight read opens no report.

        RED under mutant "summary at finalize only" (the summary written by
        ``finalize`` after its write, not by ``_write`` for every write of a
        finished report), observed:

            AssertionError: the late snapshot left the summary stale:
            [('late-snap.json', False)]
            assert [('late-snap.json', False)] == []
        """
        rep = SessionReporter(SequencePlan(name="n"), report_id="late-snap",
                              started_at=1_790_000_000.0)
        rep.record_frame(FrameRecord(ts=1.0, target="M31", filter="Ha",
                                     exposure_s=900.0))
        rep.finalize("complete")
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task()]
        assert pending, "premise: the last frame's snapshot was scheduled"
        await asyncio.wait(pending, timeout=5)
        assert rep._written > 1, "premise: the late snapshot was written"
        reads = _Reads(monkeypatch)
        got = await asyncio.to_thread(SessionReporter.summaries)
        assert reads.calls == [], (
            f"the late snapshot left the summary stale: {reads.calls}")
        assert got[0]["targets"][0]["by_filter"][0]["integration_s"] == 900.0

    def test_a_summary_that_cannot_be_written_breaks_nothing(self,
                                                             monkeypatch):
        """The summaries directory refuses every write: finalize still
        returns and the report is on disk, the failure is said once, and the
        reader still counts the night, from the report.

        RED under mutant "summary failures raise" (``_write_summary``'s
        ``except`` made one no write raises), observed, out of finalize:

            PermissionError: [WinError 5] Access is denied:
            '...\\reports\\summaries\\no-summary-dir.json'
        """
        lines: list[str] = []
        real_log = bus.log

        def log(level, message, source="hub", **kw):
            if source == "report" and level == "warning":
                lines.append(message)
            return real_log(level, message, source, **kw)
        monkeypatch.setattr(bus, "log", log)

        def refuse(path, data, **kw):
            raise PermissionError(13, "Access is denied", str(path), 5)
        monkeypatch.setattr(report_mod._persist, "write_json_atomic", refuse)

        rep = _finalized("no-summary-dir")
        assert SessionReporter.load("no-summary-dir") is not None
        assert rep._written >= 1
        assert not _summary_file("no-summary-dir").exists()
        got = SessionReporter.summaries()
        assert [s["id"] for s in got] == ["no-summary-dir"]
        SessionReporter.summaries()
        assert len(lines) == 1, lines
        assert lines[0].startswith(
            "session report summary could not be written at "
            "reports/summaries/no-summary-dir.json: PermissionError"), lines


# ================================================================ the reader

class TestTheReaderBuildsWhatIsMissing:
    def test_a_report_with_no_summary_is_read_once_and_summarised(
            self, monkeypatch):
        """A report finalised before summaries existed (its summary removed
        here) is read in full once, counted, and summarised; the second read
        opens no report.

        RED under mutant "no summary, no report" (the reader leaving out a
        report with no usable summary), observed:

            AssertionError: assert [] == ['old-night']

        RED under mutant "built but not written" (``_write_summary`` not
        called by the reader), observed:

            AssertionError: the second read opened the report again:
            [('old-night.json', False), ('old-night.json', False)]
        """
        _finalized("old-night")
        _summary_file("old-night").unlink()
        reads = _Reads(monkeypatch)
        got = SessionReporter.summaries()
        assert [s["id"] for s in got] == ["old-night"]
        assert got[0]["targets"] == [{"name": "M31", "by_filter": [
            {"filter": "Ha", "frames": 4, "integration_s": 3600.0}]}]
        assert reads.calls == [("old-night.json", False)]
        SessionReporter.summaries()
        assert reads.calls == [("old-night.json", False)], (
            f"the second read opened the report again: {reads.calls}")
        assert _summary_file("old-night").is_file()

    def test_a_report_written_after_its_summary_is_read_again(
            self, monkeypatch):
        """A report whose file changed after its summary (here rewritten
        with a fifth sub, as a crash-resume's appends would) is read afresh:
        its summary's stamp is not the file's.

        RED under mutant "a summary is trusted whatever the stamp" (the
        reader's ``summary.get("source") == source`` test dropped), observed:

            assert 3600.0 == 4500.0
        """
        _finalized("grown")
        raw = json.loads(_report_file("grown").read_text(encoding="utf-8"))
        rep = SessionReport(**raw)
        rep.targets[0].by_filter[0].frames = 5
        rep.targets[0].by_filter[0].integration_s = 4500.0
        time.sleep(0.02)
        _report_file("grown").write_text(json.dumps(rep.model_dump()),
                                         encoding="utf-8")
        reads = _Reads(monkeypatch)
        (got,) = SessionReporter.summaries()
        assert got["targets"][0]["by_filter"][0]["integration_s"] == 4500.0
        assert reads.calls == [("grown.json", False)]

    def test_a_summary_of_another_schema_is_not_read(self, monkeypatch):
        """A summary whose ``schema`` is not this build's is not folded,
        whatever its numbers say: the report is read and summarised again.

        RED under mutant "any schema" (``_read_summary``'s schema check
        dropped), observed:

            assert 99999.0 == 3600.0
        """
        _finalized("old-shape")
        path = _summary_file("old-shape")
        stale = json.loads(path.read_text(encoding="utf-8"))
        stale["schema"] = SUMMARY_SCHEMA + 1
        stale["targets"][0]["by_filter"][0]["integration_s"] = 99999.0
        path.write_text(json.dumps(stale), encoding="utf-8")
        reads = _Reads(monkeypatch)
        (got,) = SessionReporter.summaries()
        assert got["targets"][0]["by_filter"][0]["integration_s"] == 3600.0
        assert reads.calls == [("old-shape.json", False)]

    def test_a_report_that_cannot_be_read_is_left_out_and_said(self):
        """A report file that does not parse, with no summary, is left out
        (and logged by the read, once per path and reason, #370), and every
        other night is still counted. The CONTROL for the build rule: an
        unreadable report is the one kind never counted, and it is said.
        Green under every mutant of the summaries above but one; it can
        fail: RED under mutant "no summary, no report", which leaves every
        unsummarised report out unread and so says nothing, observed:

            AssertionError: the unreadable report was not said: ''
        """
        _finalized("good-night")
        _report_file("broken-night").write_text("{not json",
                                                encoding="utf-8")
        got = SessionReporter.summaries()
        assert [s["id"] for s in got] == ["good-night"]
        said = report_mod._REPORTED_UNREADABLE.get(
            str(_report_file("broken-night")), "")
        assert said.startswith("corrupt: "), (
            f"the unreadable report was not said: {said!r}")
        assert not _summary_file("broken-night").exists()


# ================================================================ the route

class TestTheRouteReadsTheSummaries:
    async def test_thousands_of_reports_and_no_full_report_read(
            self, api, monkeypatch):
        """``ARCHIVE`` finished reports, every one summarised: the Tonight
        request answers BUDGET and CAMPAIGN from the summaries, opening no
        report, and reads each summary once for both folds.

        RED under mutant "route loads every report" (the route's cached
        reader answering, as #419 built it, ``tuple(r for r in
        (SessionReporter.load(s["id"]) for s in
        SessionReporter.list_reports()) if r is not None)``), observed:

            AssertionError: full report reads: 4000
            assert 4000 == 0
        """
        _archive(ARCHIVE)
        fid = await api.save_flow(POOL_HA)
        reads = _Reads(monkeypatch)
        summary_reads: list[str] = []
        real_summary = report_mod._read_summary

        def count(stem):
            summary_reads.append(stem)
            return real_summary(stem)
        monkeypatch.setattr(report_mod, "_read_summary", count)
        got = await _tonight(api, fid)
        assert len(reads.calls) == 0, f"full report reads: {len(reads.calls)}"
        assert len(summary_reads) == ARCHIVE, (
            f"summary reads: {len(summary_reads)}, not one per report")
        (row,) = got["budget"]
        assert (row["has_ledger"], row["banked_h"]) == (True, ARCHIVE / 2), (
            "premise: BUDGET folded M31's nights and not M16's")
        members = {m["name"]: m["banked"] for m in got["campaign"]["members"]}
        assert members == {"M31": 4 * ARCHIVE // 2, "M33": 0}, (
            "premise: CAMPAIGN folded the same summaries")

    async def test_the_one_time_read_is_made_off_the_loop(self, api,
                                                          monkeypatch):
        """A report with no summary is read in full by the route, once, on
        ``resolve_tonight``'s worker thread, never on the event loop, and
        counted.

        RED under mutant "summaries read on the loop" (the route calling
        ``SessionReporter.summaries()`` before ``asyncio.to_thread`` and
        handing both folds the answer), observed:

            AssertionError: assert [('old-m31.json', True)] ==
            [('old-m31.json', False)]
        """
        # On a worker thread, where no loop runs: every snapshot is written
        # inline, so none is still rewriting the report (and its summary)
        # when the summary is removed or the route reads.
        await asyncio.to_thread(_finalized, "old-m31")
        _summary_file("old-m31").unlink()
        fid = await api.save_flow(POOL_HA)
        reads = _Reads(monkeypatch)
        got = await _tonight(api, fid)
        assert reads.calls == [("old-m31.json", False)]
        (row,) = got["budget"]
        assert row["banked_h"] == 1.0, "the old report did not count"
        got = await _tonight(api, fid)
        assert reads.calls == [("old-m31.json", False)], (
            "the summary the first request built was not used")


# ============================================================ the reproduce

def _timed(fn, reps: int) -> tuple[float, float]:
    """Median and worst of ``reps`` calls, in ms."""
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples), max(samples)


def _legacy_read() -> tuple:
    """#419's read: ``list_reports``, then ``load`` for each entry."""
    return tuple(r for r in (SessionReporter.load(s["id"])
                             for s in SessionReporter.list_reports())
                 if r is not None)


def main(sizes=(1000, 3000), reps: int = 7, legacy_reps: int = 3) -> None:
    """Print the table the module docstring records: the first summaries read
    after the archive was written, then the warm median / worst of each read
    after one warm-up call."""
    for n in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            saved = hub_module.CAPTURE_DIR
            hub_module.CAPTURE_DIR = Path(tmp) / "captures"
            try:
                _archive(n)
                first = _timed(SessionReporter.summaries, 1)[0]
                assert len(SessionReporter.summaries()) == n
                new = _timed(SessionReporter.summaries, reps)
                _legacy_read()
                old = _timed(_legacy_read, legacy_reps)
            finally:
                hub_module.CAPTURE_DIR = saved
        print(f"    {n:>7}  {first:9.0f}   {new[0]:7.0f} / {new[1]:5.0f}"
              f"   {old[0]:8.0f} / {old[1]:6.0f}")


if __name__ == "__main__":
    main()
