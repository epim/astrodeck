# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""End-of-night session report (Batch 4b §1.7).

v1 is a *summary*, not an analytics product (C1-19, C2-12): a header that leads
with **per-filter integration** (the headline number), a per-target/per-filter
breakdown, the safety-event log, and an **append-only** frame list. The three
trend sparklines (HFR / sensor temp / guide RMS) are **derived from the frame
records at read time** — there are no duplicate parallel arrays to drift (C1-19).

Persistence model (resolves the "second blocking full-file rewrite" critique):

* Each report lives at ``captures/reports/<id>.json``.
* :meth:`SessionReporter.record_frame` / :meth:`record_safety` only *append* to
  in-memory lists and schedule a JSON snapshot write through
  :func:`asyncio.to_thread` (never a synchronous in-loop blocking write). The
  snapshot is the whole report — small (frames are downsampled when huge) and
  written atomically via :func:`persist.write_json_atomic`. A snapshot write
  that fails gets one bounded retry, off the loop, a short delay later (#579):
  the old assumption that a failed snapshot is "repaired by the next one"
  held only while a next event was coming soon, and during a long hold (a
  set-aside expiry wait, a cloud hold, a wait for a target to rise) nothing
  writes again until the hold ends, so the report could stay a frame behind
  the ledger for as long as the hold lasted.
* :meth:`finalize` stamps ``ended_at`` + ``end_reason`` and writes one last time,
  on the caller's thread; a retry of that write runs on a thread of its own
  (#477).
* :meth:`attach_existing` re-hydrates a reporter from disk so a crash-resume keeps
  appending to the same report (C2-5).

The header is recomputed from the frame list every snapshot, so a report loaded
mid-run is always internally consistent.

Reading one back tells "not there" from "could not read it now" (#370).
:meth:`SessionReporter.load` answers ``None`` for both, which is what its
callers map to a 404, and :meth:`SessionReporter.read` is the same read with
the reason kept: ``missing`` is the only answer that means the file is not
there. A ``PermissionError`` is retried briefly first, because on Windows that
is what a read gets while another handle holds the file, and an unreadable
file is logged rather than skipped in silence.

Every read blocks, its retry included (``time.sleep``), so a coroutine calls
the readers through :func:`asyncio.to_thread`, never on the loop (#477);
tests/test_s7_report_final_retry_off_loop.py drives every route that reads a
report with the read refusing a thread whose loop is running.

THE LEDGER'S SUMMARIES (#536, H4 orchestrator ruling 6). The Tonight route's
two folds, BUDGET's hours per filter and CAMPAIGN's frames per target, need
only each report's per-filter accepted integration and frames, per target and
over the report. They loaded every report in full on every request, and
``list_reports`` read every file in full once more to list them, so the sheet
slowed with the archive: on Windows the ACL check each read makes costs about
4 ms before a byte is parsed. So :meth:`SessionReporter.finalize` writes a
small summary beside the report (:func:`report_summary`, in
``reports/summaries``), and :meth:`SessionReporter.summaries` reads those
instead. A report with no summary, or one that changed after its summary was
written, is loaded once and its summary built and written then: the rule for
reports finalised before summaries existed, for a report still being written,
and for a crash-resumed night appended to after its first finalize.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from statistics import median
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from .. import hub as _hubmod
from .. import persist as _persist
from ..events import bus
from ..persist import (PrivatePermissionsError, ensure_dir, list_json,
                       read_json, write_json_atomic)
from ..remote import relay_client
from ..windows_acl import PrivateAclError

# Append-only frame cap: once the list passes this, every other frame is dropped
# on the *next* snapshot so the file (and the derived trends) stay bounded on a
# long all-night run. The accepted-frame *counts* in the breakdown are never
# downsampled — only the per-frame detail list (C1-19 "downsampled/append-only").
_MAX_FRAMES = 2000

#: A read that raises ``PermissionError`` is tried this many more times,
#: ``_READ_BACKOFF_S * attempt`` apart, 0.5 s in all (#370). On Windows that is
#: the error a read gets while another handle has the file, and the snapshot
#: writer is one: in #370's reproduction every refused read (20 of 20 traced)
#: opened the file while a worker thread's ``os.replace`` was putting a
#: snapshot over it, ``PermissionError: [Errno 13]``, with the report on disk
#: throughout. An antivirus scan or an indexer can do the same. Before this it
#: was folded into ``None``, the answer for a report that does not exist, so a
#: report on disk read as absent. Under a writer replacing the file back to
#: back, a read needed at most one retry (27 of 17520 reads needed one).
#: Only ``PermissionError``: a missing file is not going to appear by waiting,
#: and a file that does not parse will not parse the second time.
_READ_RETRIES = 4
_READ_BACKOFF_S = 0.05

#: How long a failed FINAL write waits before its one retry. The final write is
#: the one that stamps ``end_reason``, and nothing writes after it, so a failure
#: there loses the report's ending for good; a snapshot that fails is replaced
#: by the next one. ``write_json_atomic`` already retries its ``os.replace``;
#: this covers what it does not, such as the staging file or the directory ACL
#: failing on a transient sharing violation. The wait is made on the retry's
#: own thread whenever ``finalize()`` is called on a running loop (#477).
_FINAL_RETRY_S = 0.25

#: How long a failed NON-final (snapshot) write waits before its one retry
#: (#579). A snapshot is ordinarily "repaired by the next one", but there may
#: be no next one soon: a set-aside expiry wait, a cloud hold or a wait for a
#: target to rise can all hold the night for minutes with no frame, safety
#: event or sky-angle row to schedule another write. Unlike the final retry
#: this one never needs a thread of its own: it only runs for a snapshot
#: built off the loop (:meth:`_write_async`'s own ``asyncio.to_thread``
#: worker), so the sleep is already on a thread with no loop to hold. A
#: snapshot written inline with no loop at all (``_write_sync``, the
#: synchronous/unit-test path) keeps the old single-attempt behaviour: that
#: path exists for tests, and in production every writer reaches this one
#: through the loop.
_SNAPSHOT_RETRY_S = 0.25

#: Shortest span of a report's OWN elapsed time (#521 fix 3) before its
#: drops-per-hour figure shows a number. Below it, one early drop would read
#: as an absurd rate -- one drop 10 s into a run answers 360/h -- so the
#: field stays None, "nothing to report yet" rather than a number nobody
#: would believe.
_DROPS_RATE_MIN_SPAN_S = 300.0

#: Report paths already warned about as unreadable, with the reason that was
#: logged. ``GET /api/reports`` and the Tonight route list the store often, and
#: a file that stays unreadable would otherwise put the same warning into the
#: 200-line log ring on every call and push the night's real lines out of it.
#: A path is warned again when its reason changes, and forgotten once it reads.
_REPORTED_UNREADABLE: dict[str, str] = {}


def _reports_dir() -> Path:
    """``captures/reports`` — resolved live so tests that monkeypatch
    ``hub.CAPTURE_DIR`` redirect the report store too."""
    return _hubmod.CAPTURE_DIR / "reports"


#: The shape of a ledger summary (:func:`report_summary`). A summary of any
#: other shape, an older one included, is one this build does not read: the
#: report is summarised again, rather than keys whose meaning may have moved
#: being folded under the old one.
SUMMARY_SCHEMA = 1


def _summaries_dir() -> Path:
    """``captures/reports/summaries``: a directory BESIDE the reports, not
    files among them. ``list_json`` lists every ``*.json`` in
    ``captures/reports`` as a report, so a summary written there would be a
    night of no frames on the operator's report list."""
    return _reports_dir() / "summaries"


def _slug(text: str) -> str:
    """Filesystem-safe slug for the report id / filename."""
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in (text or "run"))
    return out.strip("_") or "run"


def _shown(path: Path) -> str:
    """``reports/<name>``: the path a log line may carry.

    Never the absolute one. A bus log line reaches the WS stream, ``/api/logs``
    and the night log, and the owner's ruling is that no absolute path leaves
    this process for anybody (tests/test_no_absolute_paths_externally.py): the
    capture root's path names the operator's Windows account. The write
    warning carried it until #421, inside ``str(OSError)``."""
    return f"reports/{path.name}"


_WIN32_CODE = re.compile(r"\((\d+)\)\s*$")


def _described(exc: BaseException) -> str:
    """The error's type and code, without the path its text usually carries.

    ``str(OSError)`` ends with the file name, absolute, and a ``PrivateAclError``
    names the path, or an ancestor of it such as the account's home directory,
    in its message. So an OSError is described by its errno or Windows code
    and ``strerror``, which carry no path, and anything else by its type and
    the Win32 code its message ends with, when it ends with one."""
    name = type(exc).__name__
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror:
            return f"{name}: [WinError {winerror}] {exc.strerror or ''}".rstrip()
        if exc.errno is not None:
            return f"{name}: [Errno {exc.errno}] {exc.strerror or ''}".rstrip()
        return name
    code = _WIN32_CODE.search(str(exc))
    return f"{name} (code {code.group(1)})" if code else name


def _warn_unreadable(path: Path, reason: str, detail: str) -> None:
    """Log a report that could not be read, once per path and reason."""
    key = str(path)
    said = f"{reason}: {detail}"
    if _REPORTED_UNREADABLE.get(key) == said:
        return
    _REPORTED_UNREADABLE[key] = said
    bus.log("warning", f"session report {_shown(path)} could not be read "
                       f"({reason}): {detail}", "report")


class ReportRead(NamedTuple):
    """What reading one report found, and why when it found nothing (#370)."""

    #: The report, or ``None``.
    report: SessionReport | None
    #: ``None`` when ``report`` loaded. Otherwise the one word that says why:
    #: ``missing`` (no such file, the only answer that means "not there"),
    #: ``unreadable`` (an OS error, a ``PermissionError`` only after
    #: ``_READ_RETRIES`` more tries), ``corrupt`` (not JSON) or ``invalid``
    #: (JSON that is not a report).
    reason: str | None
    #: The error's type and code, never its text: see :func:`_described`.
    detail: str
    #: The file that was read. For the caller's own use, never for a log line
    #: or a response body (:func:`_shown`).
    path: Path
    #: How many times the file was opened.
    attempts: int


class ReportScan(NamedTuple):
    """:meth:`SessionReporter.scan_reports`: the summaries, and what was not."""

    summaries: list[dict]
    #: One :class:`ReportRead` per file listed that could not be read.
    unreadable: list[ReportRead]


def _read_report_file(path: Path) -> tuple[Any, str | None, str, int]:
    """``(raw, reason, detail, attempts)`` for one report file.

    ``raw`` is the parsed JSON when ``reason`` is ``None``. The retry is here,
    shared by :meth:`SessionReporter.read` and :meth:`SessionReporter.list_reports`,
    so the list cannot drop a file the single read would have waited for.

    THREAD-ONLY. The backoff is ``time.sleep``, up to half a second over
    ``_READ_RETRIES``, and it stays synchronous: made on the loop thread it
    would stop every coroutine for that long. Every production caller reaches
    it through ``asyncio.to_thread`` (the report routes, and the Tonight
    route's ledgers inside ``resolve_tonight``'s thread), and
    tests/test_s7_report_final_retry_off_loop.py proves it by patching this
    function to raise on a thread whose loop is running (#477)."""
    attempts = 0
    while True:
        attempts += 1
        try:
            return read_json(path), None, "", attempts
        except FileNotFoundError as e:
            return None, "missing", _described(e), attempts
        except PermissionError as e:
            if attempts > _READ_RETRIES:
                return None, "unreadable", _described(e), attempts
            time.sleep(_READ_BACKOFF_S * attempts)
        except ValueError as e:
            return None, "corrupt", _described(e), attempts
        except OSError as e:
            return None, "unreadable", _described(e), attempts


# ------------------------------------------------------------------------ models

class FrameRecord(BaseModel):
    ts: float
    target: str
    filter: str | None = None
    frame_type: str = "Light"
    exposure_s: float = 0.0
    accepted: bool = True
    hfr: float | None = None
    sensor_temp_c: float | None = None
    guide_rms_total: float | None = None
    saved_path: str | None = None
    # --- PRO-10 stacking-bundle fields (all additive; every existing persisted
    # report loads unchanged because each defaults to None) ------------------
    gain: int | None = None
    offset: int | None = None
    binning: int | None = None
    ecc: float | None = None
    altitude_deg: float | None = None


class FilterBreakdown(BaseModel):
    filter: str
    frames: int = 0
    rejected: int = 0
    integration_s: float = 0.0
    hfr_median: float | None = None


class TargetBreakdown(BaseModel):
    name: str
    frames: int = 0
    rejected: int = 0
    integration_s: float = 0.0
    by_filter: list[FilterBreakdown] = Field(default_factory=list)


#: Every key a ``sky_angles`` row carries, and the only ones
#: (:meth:`SessionReporter.record_sky_angle`): when the solve's frame was
#: exposed, which target, the pier side then, the rotator's mechanical angle
#: then (None with no rotator, or none readable), the PA the solve measured,
#: and which solve path measured it. An ALLOW-list, not a filter on the
#: hub's record: no altitude or azimuth, which at a known moment and target
#: is the site's latitude (#19, #140), and nothing the hub's record grows
#: later reaches the report by default.
SKY_ANGLE_KEYS = ("exposed_at", "target", "pier_side", "mechanical_deg",
                  "pa_deg", "source")


class SessionReport(BaseModel):
    id: str
    plan_name: str = ""
    started_at: float = 0.0
    ended_at: float | None = None
    end_reason: str | None = None        # complete|incomplete|aborted|shutdown|error|unsafe|dawn_cutoff
    frames_captured: int = 0             # accepted light/calibration frames
    frames_rejected: int = 0
    integration_s: float = 0.0           # accepted light integration only
    by_filter: list[FilterBreakdown] = Field(default_factory=list)   # headline
    targets: list[TargetBreakdown] = Field(default_factory=list)
    safety_events: list[dict] = Field(default_factory=list)          # {ts,reason,action}
    frames: list[FrameRecord] = Field(default_factory=list)          # downsampled if huge
    #: WHAT THIS NIGHT ACTUALLY RAN UNDER (#239 stage A):
    #: ``{field: {"value": v, "source": "plan"|"rig"}}`` for the twelve settings
    #: that can come from either the plan or the rig's standards.
    #:
    #: Recorded because the plan alone stopped being able to answer it. "Which
    #: gates were on that night?" is asked months later, about frames that
    #: already exist, from this file - and a two-layer setting whose losing
    #: layer is the one on screen is precisely how Polar ran simulated for
    #: weeks. Empty on reports written before this field existed.
    policy: dict[str, dict] = Field(default_factory=dict)
    #: THE SKY ANGLE AFTER EVERY SLEW THAT LEFT THE ROTATOR ALONE (#526 part
    #: 3, H4 orchestrator ruling 3), one row per slew, in the order they
    #: were made: :data:`SKY_ANGLE_KEYS` and nothing else. On 2026-09-28 the
    #: camera's angle moved 2.7 degrees over a plain re-slew with the rotator
    #: not commanded, and several degrees with pointing and pier side at a
    #: fixed mechanical angle; only the night log's lines said so. These rows
    #: are what measures that slip against pier side on the next nights, and
    #: what #145 and S8 wait on. Empty on reports written before this field.
    #: A flip re-slew's ``exposed_at`` is the time of a computed meridian
    #: event (the #166 class), which `GET /api/reports/{id}` serves a viewer
    #: whole until its redaction withholds it (#567).
    sky_angles: list[dict] = Field(default_factory=list)
    #: RELAY TUNNEL DROPS PER HOUR over this report's own span (#521 fix 3):
    #: a bad-network night (the rig's own internet blipping, as the 2026-09-22
    #: cluster's DNS failure showed) is visible here without reading
    #: ``captures/logs/<night>.jsonl`` by hand. Counted from the "relay link
    #: check" line ``relay_client`` logs after every drop
    #: (:func:`relay_client.recent_drop_count`), not re-derived from the log
    #: file: a count kept at the moment those lines are written cannot drift
    #: from what they say. None when the report has not run long enough for a
    #: rate to mean anything (:data:`_DROPS_RATE_MIN_SPAN_S`), or when remote
    #: access has never run in this process -- never 0 for "untried".
    relay_drops_per_hour: float | None = None


# ---------------------------------------------------------------- header builder

_LIGHT_TYPES = {"light"}


def _is_light(frame_type: str | None) -> bool:
    return (frame_type or "Light").strip().lower() in _LIGHT_TYPES


def _filter_key(f: str | None) -> str:
    return f if f else "—"


class _Totals:
    """Running cumulative report totals kept INDEPENDENT of the (possibly
    downsampled) per-frame detail list.

    Every frame is folded in via :meth:`add` *before* the detail list is ever
    downsampled, so the headline counters (``captured``/``rejected``/``integ``)
    and the per-filter / per-target breakdowns always reflect the FULL frame
    stream — downsampling the detail list never changes the totals (C1-19)."""

    def __init__(self) -> None:
        # plan-wide per-filter
        self.pf: dict[str, dict[str, Any]] = {}
        # per-target -> per-filter
        self.pt: dict[str, dict[str, Any]] = {}
        self.captured = 0
        self.rejected = 0
        self.integ = 0.0

    def add(self, fr: FrameRecord) -> None:
        """Fold one frame into the running totals. Integration counts **accepted
        light frames only**; rejects are counted but do not add integration.
        Calibration frames count toward ``captured`` but not integration."""
        light = _is_light(fr.frame_type)
        fk = _filter_key(fr.filter)
        if fr.accepted:
            self.captured += 1
            add_integ = fr.exposure_s if light else 0.0
            self.integ += add_integ
        else:
            self.rejected += 1
            add_integ = 0.0

        pe = self.pf.setdefault(fk, {"frames": 0, "rejected": 0,
                                     "integration_s": 0.0, "hfrs": [],
                                     "seed_median": None})
        te = self.pt.setdefault(fr.target, {"frames": 0, "rejected": 0,
                                            "integration_s": 0.0, "filters": {}})
        tfe = te["filters"].setdefault(fk, {"frames": 0, "rejected": 0,
                                            "integration_s": 0.0, "hfrs": [],
                                            "seed_median": None})
        if fr.accepted:
            pe["frames"] += 1
            pe["integration_s"] += add_integ
            te["frames"] += 1
            te["integration_s"] += add_integ
            tfe["frames"] += 1
            tfe["integration_s"] += add_integ
            if fr.hfr is not None:
                pe["hfrs"].append(fr.hfr)
                tfe["hfrs"].append(fr.hfr)
        else:
            pe["rejected"] += 1
            te["rejected"] += 1
            tfe["rejected"] += 1

    def breakdowns(self) -> tuple[list[FilterBreakdown], list[TargetBreakdown],
                                  int, int, float]:
        def _med(entry: dict[str, Any]) -> float | None:
            # Prefer the median of live HFR samples; fall back to a persisted
            # seed median (set on resume, where per-frame HFRs were already
            # downsampled away and only the stored median survives).
            vals = entry["hfrs"]
            if vals:
                return round(float(median(vals)), 2)
            return entry.get("seed_median")

        by_filter = [
            FilterBreakdown(filter=k, frames=v["frames"], rejected=v["rejected"],
                            integration_s=v["integration_s"],
                            hfr_median=_med(v))
            for k, v in sorted(self.pf.items())
        ]
        targets = []
        for tname, tv in self.pt.items():
            tf = [
                FilterBreakdown(filter=k, frames=fv["frames"],
                                rejected=fv["rejected"],
                                integration_s=fv["integration_s"],
                                hfr_median=_med(fv))
                for k, fv in sorted(tv["filters"].items())
            ]
            targets.append(TargetBreakdown(name=tname, frames=tv["frames"],
                                           rejected=tv["rejected"],
                                           integration_s=tv["integration_s"],
                                           by_filter=tf))
        return by_filter, targets, self.captured, self.rejected, self.integ

    @classmethod
    def from_report(cls, rep: "SessionReport") -> "_Totals":
        """Rehydrate cumulative totals from a persisted report header (the true
        totals) so a resume keeps counting from where it left off — even though
        ``rep.frames`` may already be downsampled. The persisted ``hfr_median``
        is carried as a seed for each filter (per-frame HFRs are not persisted)."""
        t = cls()
        t.captured = rep.frames_captured
        t.rejected = rep.frames_rejected
        t.integ = rep.integration_s
        for fb in rep.by_filter:
            t.pf[fb.filter] = {"frames": fb.frames, "rejected": fb.rejected,
                               "integration_s": fb.integration_s, "hfrs": [],
                               "seed_median": fb.hfr_median}
        for tb in rep.targets:
            filters: dict[str, dict[str, Any]] = {}
            for fb in tb.by_filter:
                filters[fb.filter] = {"frames": fb.frames, "rejected": fb.rejected,
                                      "integration_s": fb.integration_s, "hfrs": [],
                                      "seed_median": fb.hfr_median}
            t.pt[tb.name] = {"frames": tb.frames, "rejected": tb.rejected,
                             "integration_s": tb.integration_s, "filters": filters}
        return t


# ----------------------------------------------------------- ledger summaries

#: Summary paths already warned about as unwritable, with the reason, so a
#: summaries directory that stays unwritable says so once and not on every
#: Tonight request (the ring is 200 lines; see ``_REPORTED_UNREADABLE``).
_REPORTED_UNWRITABLE: dict[str, str] = {}


def _source(st: os.stat_result) -> dict:
    """What a summary was built from: the report file's size and modification
    time, to the nanosecond. A summary is trusted only while its report still
    stats the same. A report written since, whether a snapshot of a night
    still running, the final write or a crash-resume's appends, stats
    differently and is summarised again."""
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def report_summary(report: SessionReport, source: dict) -> dict:
    """The ledger's summary of one report (#536): per target and per filter,
    the ACCEPTED frames and the accepted light integration, and the same two
    per filter over the whole report; nothing else.

    The numbers are the report's own ``targets`` and ``by_filter`` breakdowns
    (``_Totals.add``: every accepted frame counts toward ``frames``, and only
    an accepted LIGHT frame's exposure toward ``integration_s``), so the two
    folds that read a summary (``tonight.banked_hours_from_reports`` and
    ``frames_by_target_from_reports``) answer from it exactly what they
    answered from the report, in every reading. The report-wide rows are
    kept for the fold's archive-wide reading (no ``targets``): without them
    that reading would answer 0 h from a summary where the report holds
    hours, a zero nobody measured. Rejects are left out because neither fold
    counts them: a rejected sub is one the night still owes. ``source`` is
    :func:`_source` of the file the numbers were read from."""
    def rows(breakdown: list[FilterBreakdown]) -> list[dict]:
        return [{"filter": fb.filter, "frames": fb.frames,
                 "integration_s": fb.integration_s} for fb in breakdown]
    return {
        "schema": SUMMARY_SCHEMA,
        "id": report.id,
        "source": dict(source),
        "by_filter": rows(report.by_filter),
        "targets": [{"name": tb.name, "by_filter": rows(tb.by_filter)}
                    for tb in report.targets],
    }


def _summary_path(stem: str) -> Path:
    return _summaries_dir() / f"{stem}.json"


def _read_summary(stem: str) -> dict | None:
    """The summary written for the report file ``<stem>.json``, or None when
    there is none this build can use: missing, unreadable, not JSON, or of
    another :data:`SUMMARY_SCHEMA`. None is never read as zero hours; the
    caller summarises the report instead.

    A PLAIN READ, NOT ``read_json``. ``read_json`` first re-applies the
    private ACL, the repair for a file an older release created with broad
    access, and on Windows that check alone costs about 4 ms a file (3.9 ms
    measured over 500 files on the development box), which over a few
    thousand summaries is the cost this ledger exists to remove. Every
    summary was created by :func:`_write_summary` through
    ``write_json_atomic``'s private staging file, so no older file needs the
    repair, and reading one widens nothing. A ``PermissionError`` is retried
    as a report read is (#370): on Windows it is what a read gets while a
    writer is replacing the file."""
    path = _summary_path(stem)
    attempts = 0
    while True:
        attempts += 1
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            break
        except PermissionError:
            if attempts > _READ_RETRIES:
                return None
            time.sleep(_READ_BACKOFF_S * attempts)
        except (OSError, ValueError):
            return None
    if (not isinstance(raw, dict) or raw.get("schema") != SUMMARY_SCHEMA
            or not isinstance(raw.get("source"), dict)
            or not isinstance(raw.get("by_filter"), list)
            or not isinstance(raw.get("targets"), list)):
        return None
    return raw


def _write_summary(stem: str, summary: dict) -> bool:
    """Write one summary, and say whether it was written.

    Never raises. A summary that could not be written costs the next reader
    one full read of its report and nothing else, so neither the run's
    finalize nor the Tonight route should fail over one. The failure is
    logged once per path and reason, the path shown relative to the capture
    root as every report log line shows it (:func:`_shown`). No ``.bak`` is
    kept: a summary is rebuilt from its report, never restored.

    THROUGH ``persist``'s WRITER, NOT THIS MODULE'S ``write_json_atomic``
    NAME. That name is the REPORT's write, which the #370 and #477 tests
    count and refuse one call at a time (test_report_load_reason.py,
    test_s7_report_final_retry_off_loop.py: the final write, then its one
    retry). A summary written through it would be counted among them and
    move every count without the report's own writes changing; its failures
    are the summary's, graded in tests/test_h4_report_summary_cost.py."""
    path = _summary_path(stem)
    try:
        _persist.write_json_atomic(path, summary, backup=False)
    except (OSError, PrivatePermissionsError, PrivateAclError) as e:
        said = _described(e)
        if _REPORTED_UNWRITABLE.get(str(path)) != said:
            _REPORTED_UNWRITABLE[str(path)] = said
            bus.log("warning", f"session report summary could not be written "
                               f"at reports/summaries/{path.name}: {said}",
                    "report")
        return False
    _REPORTED_UNWRITABLE.pop(str(path), None)
    return True


def _report_files() -> list[tuple[Path, dict]]:
    """Every report file with its :func:`_source`, from ONE walk of the
    directory that opens none of them.

    ``os.scandir`` hands back each entry's size and modification time from
    the listing itself on Windows, so the ledger can tell a fresh summary
    from a stale one without reading a single report (2 ms for 2000 files
    on the development box, against 20 ms for an ``os.stat`` of each). The
    files are the ones ``list_json`` lists: every ``*.json`` that is a file,
    matched without case on Windows as its glob matches them. A file that
    goes between the listing and its stat is not there, and is left out."""
    out: list[tuple[Path, dict]] = []
    try:
        with os.scandir(_reports_dir()) as entries:
            for entry in entries:
                if not os.path.normcase(entry.name).endswith(".json"):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    out.append((Path(entry.path), _source(entry.stat())))
                except FileNotFoundError:
                    continue
    except FileNotFoundError:
        return []
    out.sort(key=lambda item: item[0].name)
    return out


# ------------------------------------------------------------------------ reporter

class SessionReporter:
    """Accumulates frame/safety records for one run and snapshots them to disk.

    Construct with a plan (the engine does this at run start), then call
    :meth:`record_frame` / :meth:`record_safety` per frame/event and
    :meth:`finalize` on any terminal path. Snapshot writes go through
    :func:`asyncio.to_thread` so the event loop never blocks on I/O; the final
    write's first attempt is made on the caller's thread, and its retry on a
    thread of its own (see :meth:`finalize`)."""

    def __init__(self, plan: Any, *, report_id: str | None = None,
                 started_at: float | None = None):
        plan_name = getattr(plan, "name", None) or "Tonight"
        self.started_at = started_at if started_at is not None else time.time()
        self.id = report_id or self._make_id(plan_name, self.started_at)
        self.plan_name = plan_name
        self._frames: list[FrameRecord] = []
        # Cumulative totals kept independent of the (downsampled) _frames list so
        # the headline counts never shrink when the detail list is halved (C1-19).
        self._totals = _Totals()
        self._safety: list[dict] = []
        self._ended_at: float | None = None
        self._end_reason: str | None = None
        #: The resolved rig-standards-vs-plan record (#239 stage A), stamped by
        #: the engine at start via :meth:`record_policy`. A plain dict so a
        #: reporter built in a test without an engine simply carries nothing.
        self._policy: dict[str, dict] = {}
        #: The ``sky_angles`` rows (#526 part 3), see :meth:`record_sky_angle`.
        self._sky_angles: list[dict] = []
        self._lock = asyncio.Lock()
        # Serializes the ACTUAL disk write across threads. record_frame's snapshot
        # runs _persist on a worker thread (asyncio.to_thread) while finalize() runs
        # _persist synchronously on the event-loop thread WITHOUT the asyncio lock —
        # so both could open the identical fixed ``<id>.json.tmp`` at once and
        # os.replace a truncated/interleaved file over the report. A threading.Lock
        # (works across both threads) makes the two _persist calls mutually
        # exclusive on the shared temp path.
        self._persist_lock = threading.Lock()
        #: Snapshots are numbered as they are built, on the loop thread, and
        #: ``_persist`` writes one only if nothing newer is on disk. The lock
        #: above orders the WRITES, not the snapshots: a snapshot built before
        #: finalize() whose worker thread reached the lock after it replaced
        #: the final report with the one before it, and ``end_reason`` read
        #: None again (#420, found while fixing #370; see _persist).
        self._built = 0
        self._written = 0
        #: The thread retrying a failed final write off the loop (#477), or
        #: None when no retry was handed off.
        self._final_retry: threading.Thread | None = None

    # -- ids / paths -----------------------------------------------------------

    @staticmethod
    def _make_id(plan_name: str, started_at: float) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started_at))
        return f"{_slug(plan_name)}-{stamp}"

    def _path(self) -> Path:
        return _reports_dir() / f"{self.id}.json"

    # -- recording -------------------------------------------------------------

    def record_frame(self, rec: FrameRecord) -> None:
        """Append a frame record and schedule a snapshot write (fire-and-forget).

        Synchronous so the engine's per-frame loop never awaits the disk; the
        actual write runs on a worker thread."""
        # Fold into the running totals FIRST (before any downsample) so the
        # headline integration/frame/reject counts reflect every frame — only
        # the per-frame detail list is downsampled, never the totals (C1-19).
        self._totals.add(rec)
        self._frames.append(rec)
        if len(self._frames) > _MAX_FRAMES:
            # keep every other frame (preserves first/last + halves the list)
            self._frames = self._frames[::2]
        self._schedule_write()

    def record_safety(self, reason: str, action: str) -> None:
        self._safety.append({"ts": time.time(), "reason": reason, "action": action})
        self._schedule_write()

    def record_sky_angle(self, *, target: str, exposed_at: float,
                         pier_side: str | None, mechanical_deg: float | None,
                         pa_deg: float, source: str) -> None:
        """Append one row to the report's ``sky_angles`` (#526 part 3) and
        schedule a snapshot write, as :meth:`record_safety` does.

        One call per slew that left the rotator untouched, with the sky angle
        that slew's own solve measured (the engine's `_record_sky_angle`
        decides which slews, and that the record is fresh for the slew).
        Keyword-only and field by field, so the row is exactly
        :data:`SKY_ANGLE_KEYS` whatever the caller holds: a caller cannot
        hand the hub's whole record through, and a key the record grows
        later reaches the report only by being named here. A pier side that
        is not "east" or "west" is stored as None, the answer for "nobody
        could say", as the hub's record itself writes it, and so is a
        mechanical angle that is not a finite number, which the report's
        JSON could not carry."""
        side = str(pier_side).lower() if pier_side is not None else None
        mech = None if mechanical_deg is None else float(mechanical_deg)
        self._sky_angles.append({
            "exposed_at": float(exposed_at),
            "target": str(target),
            "pier_side": side if side in ("east", "west") else None,
            "mechanical_deg": mech if mech is not None and math.isfinite(mech)
            else None,
            "pa_deg": float(pa_deg),
            "source": str(source),
        })
        self._schedule_write()

    def mark_skipped(self, target: Any, reason: str | None = None) -> None:
        """Record a schedule skip (window closed / never rises / set aside /
        skipped by instruction / ...) as a safety-style event so it shows in
        the report timeline (C1-23).

        ``reason`` NAMES WHY (#524, spec 6.7: "The report names every
        set-aside panel and its reason"). Before this the report named only
        the panel: ``_set_panel_aside`` already held the reason — it logs it
        in a warning and publishes it in ``group.set_aside`` — and this
        method threw it away, so the morning-after record said a panel was
        skipped but not why a centring failure, a guider fault, a floor stop,
        a closed window or a pier change told it apart from any other skip.
        Optional, and appended rather than replacing the existing text, so a
        caller with nothing to add (there was none before #524; every call
        site now has a reason to pass) still writes today's bare line."""
        name = getattr(target, "name", str(target))
        line = f"skipped {name}" if reason is None else f"skipped {name}: {reason}"
        self._safety.append({"ts": time.time(), "reason": line,
                             "action": "skip"})
        self._schedule_write()

    def _schedule_write(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # no loop (unit test / sync context) — write inline.
            self._write_sync()
            return
        loop.create_task(self._write_async())

    def _snapshot(self) -> tuple[int, SessionReport]:
        """Build the report and number it. Called on the loop thread only, so
        the numbers follow the order the snapshots were built in."""
        self._built += 1
        return self._built, self.build()

    async def _write_async(self) -> None:
        async with self._lock:
            number, snapshot = self._snapshot()
            # off_loop=True: this call is already inside asyncio.to_thread's
            # own worker, so a failed snapshot's bounded retry (#579) can
            # sleep right here without ever touching the loop.
            await asyncio.to_thread(self._persist, snapshot, number,
                                    off_loop=True)

    def _write_sync(self) -> None:
        number, snapshot = self._snapshot()
        self._persist(snapshot, number)

    def _persist(self, report: SessionReport, number: int, *,
                 final: bool = False, off_loop: bool = False) -> None:
        """Write one snapshot, unless a newer one is already on disk (#420).

        Never raises: a disk hiccup must not kill the run. A failure is logged
        at warning with the report's path (capture-root-relative, see
        :func:`_shown`). A FINAL write that fails is retried once, after
        ``_FINAL_RETRY_S`` (#370): nothing writes after it, so its failure is
        the report's ending lost. A non-final (snapshot) write that fails
        gets its own one bounded retry, after ``_SNAPSHOT_RETRY_S`` (#579),
        when ``off_loop`` says it is safe to -- see below.

        Neither retry blocks THIS method's own thread: it stays the single
        "is it a final or a snapshot write" decision and never itself waits
        out a backoff, which is what every caller is entitled to assume about
        a method named like a one-shot write (#477, S7 orchestrator ruling 5
        -- the rule the owner list's item 47 and test_mosaic_spec_claims.py's
        ``test_the_owner_list_records_the_s7_orchestrator_rulings`` hold this
        method to by inspecting its own source for the stdlib call that
        would wait). ``finalize()`` is called on the loop thread, so a failed
        final write's retry is handed to a thread of its own
        (:meth:`_retry_final`); a failed snapshot write's retry
        (:meth:`_retry_snapshot`) needs no second thread, because ``off_loop``
        is True only when THIS call is already running inside
        :meth:`_write_async`'s own ``asyncio.to_thread`` worker, which has no
        loop to hold either. ``off_loop`` is False for :meth:`_write_sync`'s
        inline, no-loop-at-all callers (unit tests): that path is not reached
        in production, where every writer goes through :meth:`_write_async`,
        so it keeps the single-attempt behaviour -- a long-running retry
        thread spawned from a plain synchronous call would outlive the test
        that started it.

        The ACL errors are caught with ``OSError`` because they come from the
        same write: ``ensure_private_dir`` and ``harden_private_file`` report a
        transient sharing violation as ``PrivateAclError``. Uncaught, one of
        those escaped a snapshot's worker thread into a task nobody awaits,
        which is a failure nobody hears of."""
        path = self._path()
        failed = self._write(path, report, number)
        if failed is None:
            return
        what = "final report, retrying once" if final else "snapshot"
        bus.log("warning", f"session report write failed ({what}) at "
                           f"{_shown(path)}: {_described(failed)}", "report")
        if not final:
            if off_loop:
                # #579: one bounded retry, on this same already-off-loop
                # worker thread -- see :meth:`_retry_snapshot`.
                self._retry_snapshot(path, report, number)
            return
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            self._retry_final(path, report, number, None)
            return
        retry = threading.Thread(
            target=self._retry_final, args=(path, report, number, loop),
            name=f"report-final-retry-{self.id}",
            # Not a daemon: an interpreter exiting right after the wind-down
            # waits the quarter second for the ending rather than dropping it,
            # which is the loss this retry exists to prevent.
            daemon=False)
        self._final_retry = retry
        try:
            retry.start()
        except RuntimeError:
            # No thread to be had (the interpreter is shutting down): the
            # ending is worth the wait on a loop that is going away anyway.
            self._final_retry = None
            self._retry_final(path, report, number, None)

    def _retry_snapshot(self, path: Path, report: SessionReport,
                        number: int) -> None:
        """A failed snapshot's one bounded retry, after ``_SNAPSHOT_RETRY_S``
        (#579).

        Called only with ``off_loop=True`` (:meth:`_persist`), which is to
        say only from :meth:`_write_async`'s own ``asyncio.to_thread``
        worker -- a thread with no loop to hold -- so unlike
        :meth:`_retry_final` this never needs a thread of its own: the sleep
        is made right here, on that same worker. A second failure is logged
        and then dropped: the next real event's snapshot, or finalize(), can
        still repair it, and an unbounded retry loop would just be polling."""
        time.sleep(_SNAPSHOT_RETRY_S)
        failed = self._write(path, report, number)
        if failed is None:
            return
        bus.log("warning", f"session report write failed (snapshot, after "
                           f"one retry) at {_shown(path)}: "
                           f"{_described(failed)}", "report")

    def _write(self, path: Path, report: SessionReport,
               number: int) -> BaseException | None:
        """One attempt at writing snapshot ``number``: the error it failed
        with, or None when it was written or was already out of date.

        The one rule every write follows, a final retry on its own thread
        included (#420): under ``_persist_lock``, and only when nothing newer
        is on disk."""
        try:
            ensure_dir(_reports_dir())
            # hold the cross-thread lock across the whole atomic write so a
            # concurrent finalize() (loop thread), snapshot (worker thread)
            # and final retry (its own thread) can never both be writing the
            # shared staging file.
            with self._persist_lock:
                if number < self._written:
                    # Built before the snapshot now on disk, and late to the
                    # lock: writing it would put the older report back over
                    # the newer, finalize()'s included.
                    return None
                write_json_atomic(path, report.model_dump())
                self._written = number
                if report.ended_at is not None:
                    # A FINISHED report's summary, under the same lock as
                    # its write (#536): finalize's, its retry on a thread of
                    # its own, and a snapshot scheduled before finalize that
                    # lands after it (identical but for its stat) each leave
                    # the summary describing the file on disk, in the order
                    # the files were written. Never raises.
                    self._summarise(path, report)
            return None
        except (OSError, PrivatePermissionsError, PrivateAclError) as e:
            return e

    def _retry_final(self, path: Path, report: SessionReport, number: int,
                     loop: asyncio.AbstractEventLoop | None) -> None:
        """The failed final write's one retry, after ``_FINAL_RETRY_S``.

        Runs on its own thread when ``finalize()`` was called on ``loop``,
        and inline when there is none. A second failure is said on the loop
        when it is still running: ``bus.log`` fans out to ``asyncio.Queue``
        subscribers that belong to it, and a line put in from another thread
        waits for the loop's next wake-up, or raises under asyncio's debug
        mode ("Non-thread-safe operation invoked on an event loop other than
        the current one"). #480 is that class across the codebase; a failed
        snapshot's warning, logged from its worker thread, is still one."""
        time.sleep(_FINAL_RETRY_S)
        failed = self._write(path, report, number)
        if failed is None:
            return
        message = (f"session report write failed (final report, after one "
                   f"retry) at {_shown(path)}: {_described(failed)}")
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(bus.log, "warning", message,
                                          "report")
                return
            except RuntimeError:
                pass                   # closed since: nothing left to race
        bus.log("warning", message, "report")

    # -- snapshot --------------------------------------------------------------

    def _relay_drops_per_hour(self) -> float | None:
        """Relay tunnel drops per hour over this report's own span so far
        (#521 fix 3), counted from the "relay link check" line
        ``relay_client`` logs after every drop
        (:func:`relay_client.recent_drop_count`) rather than by re-reading
        the night's log file: the count is kept at the moment those lines
        are written, so there is nothing to parse back and nothing that can
        drift from what the log says.

        None, never 0, when remote access has never run in this process
        (``relay_client.current_client()`` is None): a LAN-only install
        never dialed a tunnel, so "0 drops/hour" would read as a tunnel that
        stayed up rather than a question that does not apply. None too
        before the report's own elapsed time reaches
        :data:`_DROPS_RATE_MIN_SPAN_S` -- one drop in the first 10 s would
        otherwise answer 360/h, a number nobody would believe."""
        if relay_client.current_client() is None:
            return None
        end = self._ended_at if self._ended_at is not None else time.time()
        span_s = end - self.started_at
        if span_s < _DROPS_RATE_MIN_SPAN_S:
            return None
        return relay_client.recent_drop_count(self.started_at) / (span_s / 3600.0)

    def build(self) -> SessionReport:
        """Recompute the full report. The header (counts + breakdowns) comes from
        the cumulative running totals — NOT from ``self._frames`` (which may be
        downsampled) — so headline integration/frame counts never shrink. The
        ``frames`` detail array is the (possibly downsampled) list, used only for
        the read-time trend sparklines / CSV."""
        by_filter, targets, captured, rejected, integ = self._totals.breakdowns()
        return SessionReport(
            id=self.id, plan_name=self.plan_name, started_at=self.started_at,
            ended_at=self._ended_at, end_reason=self._end_reason,
            frames_captured=captured, frames_rejected=rejected, integration_s=integ,
            by_filter=by_filter, targets=targets,
            safety_events=list(self._safety), frames=list(self._frames),
            policy=dict(self._policy),
            sky_angles=[dict(r) for r in self._sky_angles],
            relay_drops_per_hour=self._relay_drops_per_hour(),
        )

    def record_policy(self, record: dict[str, dict]) -> None:
        """Stamp what the run resolved its twelve settings to, and from where.

        Called once at start rather than at finalize: a run that dies before
        finalizing is exactly the one whose settings someone will want to read.

        AND WRITTEN NOW, the report's first file (#517). ``engine.start``
        names this report's id in the session, and so in the sequence state
        and the flow's progress, the moment it has stamped this; with no
        write here the file appeared only at the first frame or the finalize,
        so for the slew, the centring, a cooling or altitude wait before the
        first exposure, ``GET /api/reports/{id}`` answered "report not found"
        for an id every other surface named, and the stamp above reached no
        disk for a run that died in that stretch, the one this docstring is
        about. The write is the ordinary numbered snapshot (#420), so a
        later snapshot or the final write is never replaced by it.
        """
        self._policy = dict(record)
        self._schedule_write()

    def finalize(self, end_reason: str) -> SessionReport:
        """Stamp the terminal reason + end time and write the final snapshot.

        Synchronous so every engine terminal path (including a shielded wind-down)
        produces a persisted report even if the loop is tearing down. The first
        attempt is made here, so a write that succeeds is on disk when this
        returns. A write that fails is retried once, on a thread of its own
        when this is called on a running loop (#477), so the report may land
        a moment after the return: the retry is under the same snapshot
        numbers and lock (#420), and a snapshot built before this call still
        cannot replace it.

        THE LEDGER'S SUMMARY GOES WITH IT (#536): every write of a finished
        report, this one, its retry, and a snapshot scheduled before this
        call that lands after it, writes the report's summary beside it in
        the same locked step (:meth:`_write`, :meth:`_summarise`), so a
        Tonight request after the night reads a few hundred bytes for it and
        not the report. A write that fails leaves no summary, and the next
        reader summarises the report once."""
        self._ended_at = time.time()
        self._end_reason = end_reason
        number, report = self._snapshot()
        self._persist(report, number, final=True)
        return report

    @staticmethod
    def _summarise(path: Path, report: SessionReport) -> None:
        """Write the summary of ``report``, just written to ``path`` as a
        finished report, stamped with the stat of that file.

        Called by :meth:`_write` while it still holds ``_persist_lock``, so
        the stat is of the write just made and no other write of this report
        can come between the two: the summary on disk describes the report on
        disk, in the order they were written, until something writes the
        report again, and then its stat moves and the next reader summarises
        it afresh. On whichever thread made the write: the loop's for
        finalize's own, a worker's for a snapshot or the final retry. A
        summary is a small fraction of the report's size. Never raises."""
        try:
            source = _source(path.stat())
        except OSError:
            return
        _write_summary(path.stem, report_summary(report, source))

    # -- class-level reads -----------------------------------------------------

    @staticmethod
    def list_reports() -> list[dict]:
        """Summaries (no frame detail) of every persisted report, newest first.

        A file that cannot be read is left out of the list, and logged at
        warning (once per path and reason) rather than skipped in silence
        (#370); :meth:`scan_reports` answers which files those were."""
        return SessionReporter.scan_reports().summaries

    @staticmethod
    def scan_reports() -> "ReportScan":
        """:meth:`list_reports`' summaries, and the files it could not read.

        The summaries are the operator's report list (``GET /api/reports``),
        so a report skipped in silence was a night that vanished from it with
        nothing said, for as long as another handle held its file. A file
        that went missing between the directory listing and the read is not
        "unreadable": it is not there, and it is left out without a word.

        Blocking, one read per file with its retry: a coroutine calls this,
        or :meth:`list_reports`, through ``asyncio.to_thread`` (#477)."""
        out: list[dict] = []
        unreadable: list[ReportRead] = []
        for path in list_json(_reports_dir()):
            raw, reason, detail, attempts = _read_report_file(path)
            if reason is None and not isinstance(raw, dict):
                reason, detail = "invalid", f"a JSON {type(raw).__name__}"
            if reason == "missing":
                continue
            if reason is not None:
                _warn_unreadable(path, reason, detail)
                unreadable.append(ReportRead(None, reason, detail, path,
                                             attempts))
                continue
            _REPORTED_UNREADABLE.pop(str(path), None)
            out.append({
                "id": raw.get("id", path.stem),
                "plan_name": raw.get("plan_name", ""),
                "started_at": raw.get("started_at", 0.0),
                "ended_at": raw.get("ended_at"),
                "end_reason": raw.get("end_reason"),
                "frames_captured": raw.get("frames_captured", 0),
                "frames_rejected": raw.get("frames_rejected", 0),
                "integration_s": raw.get("integration_s", 0.0),
            })
        out.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)
        return ReportScan(out, unreadable)

    @staticmethod
    def load(report_id: str) -> SessionReport | None:
        """The report, or ``None``: :meth:`read`'s ``report``.

        ``None`` for a report that is not there and for one that could not be
        read, which is what the routes need (both are a 404 to them). A caller
        that must tell the two apart, a test asserting a report was written
        among them, calls :meth:`read`, which says which it was."""
        return SessionReporter.read(report_id).report

    @staticmethod
    def read(report_id: str) -> ReportRead:
        """Read one report and say why when there is none (#370).

        Until #370 every failure here was ``None``: a missing file, a file
        another handle held for a moment, a file that did not parse. A test
        that found ``None`` right after an unsafe abort had written the report
        could not say which, and neither could an operator's report link. Now
        a ``PermissionError`` is retried ``_READ_RETRIES`` times over half a
        second before it is given up on, and anything but ``missing`` is also
        logged, once per path and reason.

        A refusal by the ACL layer that ``read_json`` runs first
        (``PrivateAclError``) still raises, as it did before: that is a
        security answer about the file, not a reason it is absent. In #370's
        reproduction that step never refused; the reads that failed were
        refused at the open that follows it.

        Blocking, the retry's sleeps included: a coroutine calls this, or
        :meth:`load`, through ``asyncio.to_thread`` (#477)."""
        return SessionReporter._read_at(
            _reports_dir() / f"{_slug(report_id)}.json")

    @staticmethod
    def _read_at(path: Path) -> ReportRead:
        """:meth:`read`, of the file at ``path``. The ledger reads a report
        by the file it listed (:meth:`summaries`), whose stat its summary
        records, and not by an id that ``_slug`` could send to another."""
        raw, reason, detail, attempts = _read_report_file(path)
        if reason is None:
            try:
                report = SessionReport(**raw)
            except Exception as e:         # a TypeError for a non-dict too
                reason, detail = "invalid", _described(e)
            else:
                _REPORTED_UNREADABLE.pop(str(path), None)
                return ReportRead(report, None, "", path, attempts)
        if reason != "missing":
            _warn_unreadable(path, reason, detail)
        return ReportRead(None, reason, detail, path, attempts)

    @staticmethod
    def summaries() -> list[dict]:
        """Every report's :func:`report_summary`, for the Tonight ledger's
        folds (#536, H4 orchestrator ruling 6), in report file order.

        ONE WALK OF ``captures/reports`` THAT OPENS NO REPORT
        (:func:`_report_files`), then per report:

        * a summary whose recorded stat is the file's own is read and used,
          and the report is not opened: after a night, the summary
          ``finalize`` wrote;
        * any other report is read in full ONCE, here, and its summary built
          and written for the next reader, stamped with the stat taken from
          the listing, before the read. That is the rule for a report
          finalised before summaries existed, for one never finalised (a run
          that died, or tonight's, still being written), for a finalize
          whose summary was not written, and for a night a crash-resume
          appended to after its summary. Stamped with the stat from BEFORE
          the read, a report rewritten during the read carries an older stat
          than its file, so the next reader summarises it again rather than
          trusting numbers older than the file;
        * a report that cannot be read is left out, logged by the read once
          per path and reason (#370), as ``list_reports`` leaves it out. A
          file gone since the listing is not there, and says nothing.

        So a report is never counted as zero for want of a summary: it has
        one, or it is read. The first read after an upgrade pays one full
        read per old report, once, and says so in the log; tonight's report
        is read in full on each call while the run writes it, since each of
        its snapshots moves its stat.

        Blocking, one read per summary and one per report without a usable
        one: a coroutine calls this through ``asyncio.to_thread`` (#477).
        The Tonight route calls it inside ``resolve_tonight``'s thread."""
        out: list[dict] = []
        unsummarised = 0
        for path, source in _report_files():
            summary = _read_summary(path.stem)
            if summary is not None and summary.get("source") == source:
                out.append(summary)
                continue
            got = SessionReporter._read_at(path)
            if got.report is None:
                continue
            if summary is None:
                unsummarised += 1
            summary = report_summary(got.report, source)
            _write_summary(path.stem, summary)
            out.append(summary)
        if unsummarised:
            # Once per report, not per request: its summary is written above,
            # so the next call finds it. Not a warning; nothing is wrong.
            bus.log("info", f"{unsummarised} session report"
                            f"{'' if unsummarised == 1 else 's'} had no ledger "
                            f"summary, so each was read in full once and "
                            f"summarised for the next Tonight read", "report")
        return out

    @staticmethod
    def attach_existing(report_id: str) -> "SessionReporter | None":
        """Re-hydrate a reporter from a persisted report so a crash-resume keeps
        appending to the same file (C2-5). Returns ``None`` if it's gone."""
        rep = SessionReporter.load(report_id)
        if rep is None:
            return None
        r = SessionReporter.__new__(SessionReporter)
        r.id = rep.id
        r.plan_name = rep.plan_name
        r.started_at = rep.started_at
        r._frames = list(rep.frames)
        # Seed cumulative totals from the persisted header (the TRUE totals),
        # not by re-deriving over rep.frames — those may have been downsampled.
        r._totals = _Totals.from_report(rep)
        r._safety = list(rep.safety_events)
        r._ended_at = rep.ended_at
        r._end_reason = rep.end_reason
        # RESTORED FROM DISK, not left empty and not re-resolved. This is a
        # crash-resume appending to the SAME night's file, and the settings that
        # night ran under are the ones already recorded in it - re-resolving
        # would silently rewrite history with whatever Settings says now.
        # `__new__` skips __init__, so every private field has to be set here;
        # this one was the reminder of that.
        r._policy = dict(getattr(rep, "policy", {}) or {})
        # The night's sky angles so far, for the same reason (#526 part 3): a
        # crash-resume appends to them, and one that started the list empty
        # would write the night's first half out of the file at its first
        # snapshot.
        r._sky_angles = [dict(x) for x in getattr(rep, "sky_angles", []) or []]
        r._lock = asyncio.Lock()
        r._persist_lock = threading.Lock()
        r._built = 0
        r._written = 0
        r._final_retry = None
        return r

    @staticmethod
    def trends(report: SessionReport, max_points: int = 200) -> dict[str, list]:
        """Derive ``{hfr:[(ts,v)], temp:[(ts,v)], rms:[(ts,v)]}`` from the report's
        frame records *at read time* — no duplicate arrays are stored (C1-19).

        Only accepted frames contribute; each series is independently downsampled
        to ``max_points`` (a target may have temp on every frame but HFR only on
        light frames)."""
        hfr: list[tuple[float, float]] = []
        temp: list[tuple[float, float]] = []
        rms: list[tuple[float, float]] = []
        for fr in report.frames:
            if not fr.accepted:
                continue
            if fr.hfr is not None:
                hfr.append((fr.ts, fr.hfr))
            if fr.sensor_temp_c is not None:
                temp.append((fr.ts, fr.sensor_temp_c))
            if fr.guide_rms_total is not None:
                rms.append((fr.ts, fr.guide_rms_total))
        return {
            "hfr": _downsample(hfr, max_points),
            "temp": _downsample(temp, max_points),
            "rms": _downsample(rms, max_points),
        }


def _downsample(series: list[tuple[float, float]], max_points: int) -> list[list[float]]:
    """Stride-downsample a series to at most ``max_points`` points, always keeping
    the last point. Returns ``[[ts, v], ...]`` (JSON-friendly)."""
    if max_points <= 0 or len(series) <= max_points:
        return [[t, v] for t, v in series]
    stride = len(series) / max_points
    out: list[list[float]] = []
    i = 0.0
    while int(i) < len(series):
        t, v = series[int(i)]
        out.append([t, v])
        i += stride
    last_t, last_v = series[-1]
    if not out or out[-1][0] != last_t:
        out.append([last_t, last_v])
    return out
