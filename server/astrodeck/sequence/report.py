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
  written atomically via :func:`persist.write_json_atomic`.
* :meth:`finalize` stamps ``ended_at`` + ``end_reason`` and writes one last time.
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
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path
from statistics import median
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from .. import hub as _hubmod
from ..events import bus
from ..persist import (PrivatePermissionsError, ensure_dir, list_json,
                       read_json, write_json_atomic)
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
#: failing on a transient sharing violation.
_FINAL_RETRY_S = 0.25

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
    so the list cannot drop a file the single read would have waited for."""
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


class SessionReport(BaseModel):
    id: str
    plan_name: str = ""
    started_at: float = 0.0
    ended_at: float | None = None
    end_reason: str | None = None        # complete|incomplete|aborted|error|unsafe|dawn_cutoff
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


# ------------------------------------------------------------------------ reporter

class SessionReporter:
    """Accumulates frame/safety records for one run and snapshots them to disk.

    Construct with a plan (the engine does this at run start), then call
    :meth:`record_frame` / :meth:`record_safety` per frame/event and
    :meth:`finalize` on any terminal path. All disk writes go through
    :func:`asyncio.to_thread` so the event loop never blocks on I/O."""

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

    def mark_skipped(self, target: Any) -> None:
        """Record a schedule skip (window closed / never rises) as a safety-style
        event so it shows in the report timeline (C1-23)."""
        name = getattr(target, "name", str(target))
        self._safety.append({"ts": time.time(), "reason": f"skipped {name}",
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
            await asyncio.to_thread(self._persist, snapshot, number)

    def _write_sync(self) -> None:
        number, snapshot = self._snapshot()
        self._persist(snapshot, number)

    def _persist(self, report: SessionReport, number: int, *,
                 final: bool = False) -> None:
        """Write one snapshot, unless a newer one is already on disk (#420).

        Never raises: a disk hiccup must not kill the run. A failure is logged
        at warning with the report's path (capture-root-relative, see
        :func:`_shown`), and a FINAL write that fails is retried once, after
        ``_FINAL_RETRY_S`` (#370): nothing writes after it, so its failure is
        the report's ending lost, while a snapshot's is repaired by the next.

        The ACL errors are caught with ``OSError`` because they come from the
        same write: ``ensure_private_dir`` and ``harden_private_file`` report a
        transient sharing violation as ``PrivateAclError``. Uncaught, one of
        those escaped a snapshot's worker thread into a task nobody awaits,
        which is a failure nobody hears of."""
        path = self._path()
        attempts = 2 if final else 1
        for attempt in range(1, attempts + 1):
            try:
                ensure_dir(_reports_dir())
                # hold the cross-thread lock across the whole atomic write so a
                # concurrent finalize() (loop thread) and snapshot (worker
                # thread) can never both be writing the shared staging file.
                with self._persist_lock:
                    if number < self._written:
                        # Built before the snapshot now on disk, and late to
                        # the lock: writing it would put the older report back
                        # over the newer, finalize()'s included.
                        return
                    write_json_atomic(path, report.model_dump())
                    self._written = number
                return
            except (OSError, PrivatePermissionsError, PrivateAclError) as e:
                if attempt < attempts:
                    what = "final report, retrying once"
                elif final:
                    what = "final report, after one retry"
                else:
                    what = "snapshot"
                bus.log("warning", f"session report write failed ({what}) at "
                                   f"{_shown(path)}: {_described(e)}", "report")
                if attempt < attempts:
                    time.sleep(_FINAL_RETRY_S)

    # -- snapshot --------------------------------------------------------------

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
        )

    def record_policy(self, record: dict[str, dict]) -> None:
        """Stamp what the run resolved its twelve settings to, and from where.

        Called once at start rather than at finalize: a run that dies before
        finalizing is exactly the one whose settings someone will want to read.
        """
        self._policy = dict(record)

    def finalize(self, end_reason: str) -> SessionReport:
        """Stamp the terminal reason + end time and write the final snapshot.

        Synchronous so every engine terminal path (including a shielded wind-down)
        produces a persisted report even if the loop is tearing down."""
        self._ended_at = time.time()
        self._end_reason = end_reason
        number, report = self._snapshot()
        self._persist(report, number, final=True)
        return report

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
        "unreadable": it is not there, and it is left out without a word."""
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
        refused at the open that follows it."""
        path = _reports_dir() / f"{_slug(report_id)}.json"
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
        r._lock = asyncio.Lock()
        r._persist_lock = threading.Lock()
        r._built = 0
        r._written = 0
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
