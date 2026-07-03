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
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import BaseModel, Field

from .. import hub as _hubmod
from ..events import bus
from ..persist import ensure_dir, list_json, read_json, write_json_atomic

# Append-only frame cap: once the list passes this, every other frame is dropped
# on the *next* snapshot so the file (and the derived trends) stay bounded on a
# long all-night run. The accepted-frame *counts* in the breakdown are never
# downsampled — only the per-frame detail list (C1-19 "downsampled/append-only").
_MAX_FRAMES = 2000


def _reports_dir() -> Path:
    """``captures/reports`` — resolved live so tests that monkeypatch
    ``hub.CAPTURE_DIR`` redirect the report store too."""
    return _hubmod.CAPTURE_DIR / "reports"


def _slug(text: str) -> str:
    """Filesystem-safe slug for the report id / filename."""
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in (text or "run"))
    return out.strip("_") or "run"


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
    end_reason: str | None = None        # complete|aborted|error|unsafe|dawn_cutoff
    frames_captured: int = 0             # accepted light/calibration frames
    frames_rejected: int = 0
    integration_s: float = 0.0           # accepted light integration only
    by_filter: list[FilterBreakdown] = Field(default_factory=list)   # headline
    targets: list[TargetBreakdown] = Field(default_factory=list)
    safety_events: list[dict] = Field(default_factory=list)          # {ts,reason,action}
    frames: list[FrameRecord] = Field(default_factory=list)          # downsampled if huge


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


def build_breakdowns(frames: list[FrameRecord]) -> tuple[
        list[FilterBreakdown], list[TargetBreakdown], int, int, float]:
    """Derive the plan-wide per-filter totals, per-target breakdown, and the
    headline counters from a frame list. Pure.

    Used to re-derive totals from a FULL (un-downsampled) frame list — e.g. when
    re-hydrating a reporter. The live reporter instead folds each frame into a
    running :class:`_Totals` before downsampling, so its headline never depends on
    the downsampled detail list."""
    totals = _Totals()
    for fr in frames:
        totals.add(fr)
    return totals.breakdowns()


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
        self._lock = asyncio.Lock()
        # Serializes the ACTUAL disk write across threads. record_frame's snapshot
        # runs _persist on a worker thread (asyncio.to_thread) while finalize() runs
        # _persist synchronously on the event-loop thread WITHOUT the asyncio lock —
        # so both could open the identical fixed ``<id>.json.tmp`` at once and
        # os.replace a truncated/interleaved file over the report. A threading.Lock
        # (works across both threads) makes the two _persist calls mutually
        # exclusive on the shared temp path.
        self._persist_lock = threading.Lock()

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

    async def _write_async(self) -> None:
        async with self._lock:
            snapshot = self.build()
            await asyncio.to_thread(self._persist, snapshot)

    def _write_sync(self) -> None:
        self._persist(self.build())

    def _persist(self, report: SessionReport) -> None:
        try:
            ensure_dir(_reports_dir())
            # hold the cross-thread lock across the whole atomic write so a
            # concurrent finalize() (loop thread) and snapshot (worker thread) can
            # never both be writing the shared ``<id>.json.tmp`` at the same time.
            with self._persist_lock:
                write_json_atomic(self._path(), report.model_dump())
        except OSError as e:  # never let a disk hiccup kill the run
            bus.log("warning", f"session report write failed: {e}", "report")

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
        )

    def finalize(self, end_reason: str) -> SessionReport:
        """Stamp the terminal reason + end time and write the final snapshot.

        Synchronous so every engine terminal path (including a shielded wind-down)
        produces a persisted report even if the loop is tearing down."""
        self._ended_at = time.time()
        self._end_reason = end_reason
        report = self.build()
        self._persist(report)
        return report

    # -- class-level reads -----------------------------------------------------

    @staticmethod
    def list_reports() -> list[dict]:
        """Summaries (no frame detail) of every persisted report, newest first."""
        out: list[dict] = []
        for path in list_json(_reports_dir()):
            try:
                raw = read_json(path)
            except (ValueError, OSError):
                continue
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
        return out

    @staticmethod
    def load(report_id: str) -> SessionReport | None:
        path = _reports_dir() / f"{_slug(report_id)}.json"
        try:
            raw = read_json(path)
        except (FileNotFoundError, ValueError, OSError):
            return None
        try:
            return SessionReport(**raw)
        except Exception:
            return None

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
        r._lock = asyncio.Lock()
        r._persist_lock = threading.Lock()
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
