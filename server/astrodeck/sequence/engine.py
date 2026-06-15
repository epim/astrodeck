"""Sequence engine: runs an imaging plan autonomously.

Per target: slew → (center) → (autofocus) → start guiding → for each step:
set filter (+ focus offset) → expose ×count, with dithering, periodic/thermal
refocus, meridian-flip handling, and guiding-loss recovery. Pause/resume/abort
safe at frame boundaries; progress is persisted so a crashed run can resume.

Calibration targets (darks/bias/flats) skip slewing, centering, focus and
guiding — they just expose.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from statistics import median
from typing import Any

from .. import hub as _hubmod
from ..devices.base import DeviceError
from ..events import bus
from ..focus import run_autofocus
from ..hub import Hub
from .models import SequencePlan, Target

# --- Monitor / ETA shared constants (single source of truth) ---------------
# The cooler "at target" band. Defined ONCE here (master plan §A.7); the hub
# imports it so the Monitor's at_target flag and the engine's cooling-wait gate
# never drift. The mirror is exported to the UI via lib/eta.ts.
COOLER_AT_TARGET_C = 1.0

ETA_MIN_FRAMES = 3          # ETA stays low-confidence (~) below this
OVERHEAD_EMA_ALPHA = 0.1    # per-frame overhead EMA (low α: one slow frame
                            # doesn't whipsaw the finish clock)
DEFAULT_OVERHEAD_S = 12.0   # seed overhead until real frames are measured
DITHER_COST_S = 8.0         # seed event costs; replaced by measured averages
AF_COST_S = 45.0
FLIP_COST_S = 90.0


def _resume_file() -> Path:
    return _hubmod.CAPTURE_DIR / ".sequence_resume.json"


class SequenceEngine:
    def __init__(self, hub: Hub):
        self.hub = hub
        # register so the hub's poll_status can read the active plan's
        # meridian_flip setting (Monitor meridian block) without importing here.
        hub.engine = self
        self.plan: SequencePlan | None = None
        self._task: asyncio.Task | None = None
        self._paused = asyncio.Event()
        self._paused.set()  # set = not paused
        self.state: dict[str, Any] = {"state": "idle"}
        self._frames_done = 0
        self._frames_since_dither = 0
        self._frames_since_focus = 0
        self._last_focus_temp: float | None = None
        self._recent_hfr: list[float] = []
        self._rejected = 0
        self._done: dict[str, int] = {}   # "ti:si" -> frames completed
        self._started_at = 0.0
        # --- paused-aware elapsed + deterministic ETA bookkeeping (spec §5) ---
        self._paused_accum_s = 0.0
        self._pause_started_at: float | None = None
        self._cur_exposure_s = 0.0          # exposure of the in-flight step
        self._frame_started_at = 0.0        # wall time the in-flight exposure began
        self._last_frame_done = 0.0         # wall time the last frame completed
        self._frame_had_event = False       # this frame carried a dither/AF/flip
        self._active_step: tuple[int, int] | None = None
        self._overhead_ema = DEFAULT_OVERHEAD_S
        self._overhead_samples = 0          # real per-frame overhead samples seen
        self._event_costs: dict[str, list[float]] = {}  # measured event durations

    # ----------------------------------------------------------------- control

    def start(self, plan: SequencePlan, resume_done: dict[str, int] | None = None) -> None:
        if self.running:
            raise DeviceError("a sequence is already running")
        self.plan = plan
        self._done = dict(resume_done or {})
        self._frames_done = sum(self._done.values())
        self._frames_since_dither = 0
        self._frames_since_focus = 0
        self._last_focus_temp = None
        self._recent_hfr = []
        self._rejected = 0
        self._paused.set()
        self._started_at = time.time()
        self._paused_accum_s = 0.0
        self._pause_started_at = None
        self._cur_exposure_s = 0.0
        self._frame_started_at = 0.0
        self._last_frame_done = 0.0
        self._frame_had_event = False
        self._active_step = None
        self._overhead_ema = DEFAULT_OVERHEAD_S
        self._overhead_samples = 0
        self._event_costs = {}
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        self._paused.clear()
        if self._pause_started_at is None:
            self._pause_started_at = time.time()
        self._set_state(state="paused")

    def resume(self) -> None:
        if self._pause_started_at is not None:
            self._paused_accum_s += time.time() - self._pause_started_at
            self._pause_started_at = None
        self._paused.set()
        self._set_state(state="running")

    # ------------------------------------------------------- elapsed / ETA math

    def _elapsed_s(self) -> float:
        """Wall-clock seconds since start, EXCLUDING time spent paused, so the
        progress bar's elapsed/rate never ticks through a pause (spec §5.1)."""
        paused = self._paused_accum_s
        if self._pause_started_at is not None:
            paused += time.time() - self._pause_started_at
        return max(0.0, time.time() - self._started_at - paused)

    def _event_cost(self, kind: str, default: float) -> float:
        """Measured rolling average for an event type, or the seed until one is
        observed (so a guessed flip cost is replaced by the real one — spec §5.2)."""
        seen = self._event_costs.get(kind)
        if seen:
            return sum(seen) / len(seen)
        return default

    def _record_event_cost(self, kind: str, seconds: float) -> None:
        if seconds <= 0:
            return
        self._event_costs.setdefault(kind, []).append(float(seconds))
        self._event_costs[kind] = self._event_costs[kind][-10:]

    def _remaining_capture_s(self) -> float:
        """Pure capture seconds still owed, EXCLUDING the in-flight frame (that
        is counted once as ``in_flight``). Off-by-one guard for the ETA assembly
        (spec §5.3): for the step in flight, only frames *after* the current one
        are summed here."""
        if not self.plan:
            return 0.0
        total = 0.0
        for ti, target in enumerate(self.plan.targets):
            for si, step in enumerate(target.steps):
                done = self._done.get(f"{ti}:{si}", 0)
                remaining = max(0, step.count - done)
                # the in-flight frame belongs to the step currently capturing; we
                # subtract one frame's worth there because it is accounted as the
                # `in_flight` term in the ETA (off-by-one guard, spec §5.3).
                # _is_active_step precisely identifies that one step.
                if remaining and self._frame_started_at > 0 \
                        and self._is_active_step(ti, si):
                    remaining -= 1
                total += remaining * step.exposure_s
        return total

    def _is_active_step(self, ti: int, si: int) -> bool:
        """True for the single step whose frame is currently exposing."""
        return getattr(self, "_active_step", None) == (ti, si)

    def compute_eta(self) -> dict:
        """Deterministic ETA assembly (spec §5.2/§5.3). Split into pure capture
        seconds + a low-α per-frame overhead EMA + analytically-counted event
        costs (dither/AF/flip), each measured once observed. Returns the progress
        sub-dict's ETA fields; never raises."""
        if not self.plan:
            return {}
        total = self.plan.total_frames()
        frames_remaining = max(0, total - self._frames_done)

        remaining_capture_s = self._remaining_capture_s()
        in_flight = 0.0
        if self._frame_started_at > 0 and self._cur_exposure_s > 0:
            in_flight = max(0.0, self._cur_exposure_s
                            - (time.time() - self._frame_started_at))

        d_every = self.plan.dither_every or 0
        af_every = self.plan.autofocus_every or 0
        dithers_remaining = (frames_remaining // d_every) if d_every else 0
        refocus_remaining = (frames_remaining // af_every) if af_every else 0
        # only count a flip that actually falls inside the remaining run window
        # (a meridian hours away must not inflate a short run's ETA — spec §5.2).
        remaining_window_s = (remaining_capture_s + in_flight
                              + frames_remaining * self._overhead_ema
                              + dithers_remaining * self._event_cost("dither", DITHER_COST_S)
                              + refocus_remaining * self._event_cost("autofocus", AF_COST_S))
        flip_pending = 1 if self._flip_pending(remaining_window_s) else 0
        events_cost_s = (
            dithers_remaining * self._event_cost("dither", DITHER_COST_S)
            + refocus_remaining * self._event_cost("autofocus", AF_COST_S)
            + flip_pending * self._event_cost("flip", FLIP_COST_S))

        eta_s = (remaining_capture_s + in_flight
                 + frames_remaining * self._overhead_ema + events_cost_s)

        events_measured_ok = not (
            (dithers_remaining and "dither" not in self._event_costs)
            or (refocus_remaining and "autofocus" not in self._event_costs)
            or (flip_pending and "flip" not in self._event_costs))
        eta_confident = (self._overhead_samples >= ETA_MIN_FRAMES
                         and events_measured_ok)
        return {
            "eta_s": round(eta_s),
            "eta_confident": bool(eta_confident),
            "server_now_ms": round(time.time() * 1000),
            "current_exposure_s": round(self._cur_exposure_s, 3),
            "frame_started_at_ms": round(self._frame_started_at * 1000)
            if self._frame_started_at > 0 else None,
            "remaining_capture_s": round(remaining_capture_s),
            "events_cost_s": round(events_cost_s),
        }

    def _flip_pending(self, remaining_window_s: float) -> bool:
        """Whether a meridian flip is owed *within* the remaining run window.

        Reads the hub's last server-computed meridian (sync, no device I/O): a
        flip counts only when the plan enables it, the mount is a GEM, and the
        flip is due before the run would finish — so a meridian hours away never
        inflates a short run's ETA (spec §5.2)."""
        if not (self.plan and self.plan.meridian_flip):
            return False
        mer = getattr(self.hub, "last_meridian", None) or {}
        if not mer.get("flip_enabled"):
            return False
        ttf_h = mer.get("hours_to_flip")
        if ttf_h is None:
            return False
        # Strictly the spec §5.2 window gate: a flip is a *future* cost only when
        # it falls after now and before the run would finish. A flip "due" in the
        # past (ttf < 0, target already west of the meridian) is not a pending
        # cost for this run — the engine flips at most once at the boundary, and
        # only when the device reports a crossing; an already-passed meridian on
        # sim/Alpaca never fires. This keeps a far-away meridian from inflating
        # the ETA of a short run.
        return 0 < (ttf_h * 3600.0) <= remaining_window_s

    async def abort(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._set_state(state="aborted", detail="sequence aborted")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def paused(self) -> bool:
        return not self._paused.is_set()

    @staticmethod
    def load_resume() -> dict | None:
        try:
            return json.loads(_resume_file().read_text())
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------------- state

    def _set_state(self, **kw: Any) -> None:
        if self.plan:
            total = self.plan.total_frames()
            progress = {
                "frames_done": self._frames_done,
                "frames_total": total,
                "percent": round(100 * self._frames_done / total, 1) if total else 0,
                "elapsed_s": round(self._elapsed_s()),   # paused-aware (spec §5.1)
                "rejected": self._rejected,
            }
            # deterministic ETA fields — only while a run is live (idle/complete
            # carry no honest finish). compute_eta never raises.
            if self.running:
                try:
                    progress.update(self.compute_eta())
                except Exception:
                    pass
            kw.setdefault("progress", progress)
            kw.setdefault("plan_name", self.plan.name)
        self.state = {**self.state, **kw}
        bus.publish("sequence", **self.state)

    def _persist(self) -> None:
        if not self.plan:
            return
        try:
            f = _resume_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(
                {"plan": self.plan.model_dump(), "done": self._done, "ts": time.time()}))
        except OSError:
            pass

    def _clear_resume(self) -> None:
        try:
            _resume_file().unlink(missing_ok=True)
        except OSError:
            pass

    async def _checkpoint(self) -> None:
        """Frame-boundary gate: honors pause and cancellation."""
        await self._paused.wait()

    # ------------------------------------------------------------------- run

    async def _run(self) -> None:
        plan = self.plan
        assert plan is not None
        try:
            self._set_state(state="running", detail=f"starting plan '{plan.name}'")
            bus.log("info", f"sequence '{plan.name}' started: {plan.total_frames()} frames, "
                            f"{plan.total_seconds() / 60:.0f} min integration", "sequence")

            if plan.cool_to is not None:
                await self._cool_and_wait(plan.cool_to, plan.cool_timeout_s)

            for ti, target in enumerate(plan.targets):
                await self._checkpoint()
                if self._target_complete(ti, target):
                    bus.log("info", f"{target.name}: already complete — skipping", "sequence")
                    continue
                if target.calibration:
                    await self._run_calibration(ti, target)
                    continue
                await self._setup_target(ti, target)
                for si, step in enumerate(target.steps):
                    await self._run_step(ti, si, target, step)

            self._set_state(state="complete", detail="all targets complete")
            bus.log("info", f"sequence '{plan.name}' complete: {self._frames_done} frames"
                            + (f", {self._rejected} flagged" if self._rejected else ""), "sequence")
            self._clear_resume()
            await self._wind_down(plan.park_when_done, plan.warm_cooler_when_done)
        except asyncio.CancelledError:
            bus.log("warning", "sequence aborted", "sequence")
            await self._safe_stop()
            raise
        except Exception as e:
            bus.log("error", f"sequence failed: {e}", "sequence")
            self._set_state(state="error", detail=str(e))
            await self._safe_stop()

    def _target_complete(self, ti: int, target: Target) -> bool:
        total = sum(s.count for s in target.steps)
        done = sum(self._done.get(f"{ti}:{si}", 0) for si in range(len(target.steps)))
        return total > 0 and done >= total

    async def _setup_target(self, ti: int, target: Target) -> None:
        self._set_state(target=target.name, target_index=ti, detail=f"slewing to {target.name}")
        bus.log("info", f"target {ti + 1}/{len(self.plan.targets)}: {target.name}", "sequence")

        if "telescope" in self.hub.devices:
            if target.center:
                result = await self.hub.goto_and_center(target.ra_hours, target.dec_deg)
                if not result["centered"]:
                    bus.log("warning", f"{target.name}: centering converged to "
                                       f"{result['error_arcmin']:.1f}' — continuing", "sequence")
            else:
                tel = self.hub.require("telescope")
                if await tel.is_parked():
                    await tel.unpark()
                await tel.set_tracking(True)
                await tel.slew(target.ra_hours, target.dec_deg)

        if target.autofocus_first and "focuser" in self.hub.devices:
            await self._autofocus("initial autofocus")

        if self.plan.guide and self.hub.guider and self.hub.guider.connected:
            self._set_state(detail="starting guiding")
            try:
                await self.hub.guider.start_guiding()
            except Exception as e:
                bus.log("warning", f"guiding failed to start: {e} — continuing unguided", "sequence")

    async def _run_calibration(self, ti: int, target: Target) -> None:
        self._set_state(target=target.name, target_index=ti, detail=f"calibration: {target.name}")
        bus.log("info", f"calibration target: {target.name}", "sequence")
        for si, step in enumerate(target.steps):
            key = f"{ti}:{si}"
            for i in range(self._done.get(key, 0), step.count):
                await self._checkpoint()
                self._begin_frame(ti, si, step.exposure_s)
                self._set_state(state="running",
                                detail=f"{target.name}: {step.frame_type} {step.exposure_s:g}s "
                                       f"[{i + 1}/{step.count}]")
                await self.hub.capture(step.exposure_s, step.gain, step.offset, step.binning,
                                       save=True, target=target.name, frame_type=step.frame_type)
                self._record_frame(key, i)

    async def _run_step(self, ti: int, si: int, target: Target, step) -> None:
        plan = self.plan
        assert plan is not None
        key = f"{ti}:{si}"
        if self._done.get(key, 0) >= step.count:
            return
        await self._apply_filter(step)

        for i in range(self._done.get(key, 0), step.count):
            await self._checkpoint()
            await self._maybe_meridian_flip(target)
            await self._maybe_recover_guiding()

            if plan.dither_every and self._frames_since_dither >= plan.dither_every \
                    and self.hub.guider and self.hub.guider.connected:
                self._set_state(detail="dithering")
                _t0 = time.time()
                try:
                    await self.hub.guider.dither(plan.dither_pixels)
                    self._frames_since_dither = 0
                    self._record_event_cost("dither", time.time() - _t0)
                    self._frame_had_event = True
                except Exception as e:
                    bus.log("warning", f"dither failed: {e}", "sequence")

            if await self._refocus_due():
                await self._autofocus("refocus")
                self._frame_had_event = True

            self._begin_frame(ti, si, step.exposure_s)
            self._set_state(state="running",
                            detail=f"{target.name}: {step.filter or 'no filter'} "
                                   f"{step.exposure_s:g}s  [{i + 1}/{step.count}]")
            info = await self.hub.capture(step.exposure_s, step.gain, step.offset, step.binning,
                                          save=True, target=target.name, frame_type=step.frame_type)
            self._check_quality(info)
            self._frames_since_dither += 1
            self._frames_since_focus += 1
            self._record_frame(key, i)

    def _begin_frame(self, ti: int, si: int, exposure_s: float) -> None:
        """Mark the in-flight exposure for the sub-frame bar + ETA off-by-one
        guard (set immediately before ``hub.capture``).

        NB: ``_frame_had_event`` is intentionally NOT reset here. The dither/AF/
        flip blocks set it True *before* this runs, and ``_record_frame`` must
        still see it True so the event wall-time is excluded from the overhead
        EMA (it is accounted analytically). The flag is reset in ``_record_frame``
        AFTER it is read (P2-1)."""
        self._active_step = (ti, si)
        self._cur_exposure_s = float(exposure_s)
        self._frame_started_at = time.time()

    def _record_frame(self, key: str, i: int) -> None:
        now = time.time()
        # Per-frame overhead EMA: cadence minus exposure, EXCLUDING any frame that
        # carried a dither/AF/flip (those are accounted analytically, so folding
        # them into the per-frame overhead would double-count and whipsaw the
        # finish clock). α=0.1 keeps one cloud-slowed frame from whipsawing it
        # (spec §5.2).
        if self._last_frame_done > 0 and self._cur_exposure_s > 0 \
                and not getattr(self, "_frame_had_event", False):
            overhead = (now - self._last_frame_done) - self._cur_exposure_s
            if overhead > 0:
                self._overhead_ema = ((1 - OVERHEAD_EMA_ALPHA) * self._overhead_ema
                                      + OVERHEAD_EMA_ALPHA * overhead)
                self._overhead_samples += 1
        # reset the flag only AFTER reading it above, so the dither/AF/flip set
        # earlier this frame is honored for this frame's overhead and cleared for
        # the next (P2-1). Final order: begin → set-flag → capture → read → reset.
        self._frame_had_event = False
        self._last_frame_done = now
        self._frame_started_at = 0.0   # frame complete — no longer in flight
        self._done[key] = i + 1
        self._frames_done += 1
        self._persist()
        self._set_state()

    # ----------------------------------------------------------- sub-routines

    async def _cool_and_wait(self, target_c: float, timeout_s: int) -> None:
        cam = self.hub.devices.get("camera")
        if not cam or not cam.connected or not getattr(cam, "can_cool", False):
            return
        self._set_state(state="running", detail=f"cooling to {target_c:g}°C")
        bus.log("info", f"cooling camera to {target_c:g}°C", "sequence")
        try:
            await cam.set_cooler(True, target_c)
        except Exception as e:
            bus.log("warning", f"cooler command failed: {e}", "sequence")
            return
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            await self._checkpoint()
            t = await cam.get_temperature()
            if t is not None and abs(t - target_c) <= COOLER_AT_TARGET_C:
                bus.log("info", f"cooler stable at {t:.1f}°C", "sequence")
                return
            self._set_state(detail=f"cooling: {t:.1f}°C → {target_c:g}°C" if t is not None
                            else "cooling…")
            await asyncio.sleep(5.0)
        bus.log("warning", "cooler did not stabilize in time — continuing", "sequence")

    async def _apply_filter(self, step) -> None:
        if not step.filter or "filterwheel" not in self.hub.devices:
            return
        fw = self.hub.require("filterwheel")
        if step.filter not in fw.filter_names:
            bus.log("warning", f"filter '{step.filter}' not in wheel — skipping move", "sequence")
            return
        new_slot = fw.filter_names.index(step.filter)
        old_slot = await fw.get_position()
        if new_slot == old_slot:
            return
        self._set_state(detail=f"filter → {step.filter}")
        await fw.set_position(new_slot)
        # shift focus by the per-filter offset delta (offsets are relative, so
        # an incremental delta keeps focus correct as long as we step through changes)
        offsets = getattr(fw, "filter_offsets", []) or []
        if self.plan.apply_filter_offsets and "focuser" in self.hub.devices \
                and len(offsets) > max(new_slot, old_slot):
            delta = offsets[new_slot] - offsets[old_slot]
            if delta:
                foc = self.hub.require("focuser")
                pos = await foc.get_position()
                await foc.move_to(pos + delta)
                bus.log("info", f"applied filter offset {delta:+d} for {step.filter}", "sequence")

    async def _maybe_meridian_flip(self, target: Target) -> None:
        if not self.plan.meridian_flip:
            return
        tel = self.hub.devices.get("telescope")
        if not tel or not tel.connected:
            return
        try:
            ttf = await tel.time_to_meridian_flip()
        except Exception:
            return
        if ttf is None or ttf > 0:
            return
        self._set_state(detail="meridian flip")
        _t0 = time.time()
        await self.hub.meridian_flip(target.ra_hours, target.dec_deg)
        self._record_event_cost("flip", time.time() - _t0)
        # the flip wall-time is accounted analytically (events_cost_s), so flag
        # this frame to exclude it from the per-frame overhead EMA — matching the
        # dither/AF blocks (P2-1).
        self._frame_had_event = True
        if "focuser" in self.hub.devices:
            await self._autofocus("post-flip autofocus")

    async def _maybe_recover_guiding(self) -> None:
        if not (self.plan.guide and self.plan.recover_guiding):
            return
        g = self.hub.guider
        if not g or not g.connected:
            return
        try:
            if await g.is_active():
                return
        except Exception:
            return
        bus.log("warning", "guiding lost — attempting recovery", "sequence")
        self._set_state(detail="recovering guiding")
        try:
            await g.start_guiding()
        except Exception as e:
            bus.log("warning", f"guiding recovery failed: {e}", "sequence")

    async def _refocus_due(self) -> bool:
        plan = self.plan
        if "focuser" not in self.hub.devices:
            return False
        if plan.autofocus_every and self._frames_since_focus >= plan.autofocus_every:
            return True
        if plan.refocus_on_temp_delta_c > 0 and self._last_focus_temp is not None:
            try:
                t = await self.hub.require("focuser").get_temperature()
            except Exception:
                t = None
            if t is not None and abs(t - self._last_focus_temp) >= plan.refocus_on_temp_delta_c:
                bus.log("info", f"focuser temp drifted to {t:.1f}°C — refocusing", "sequence")
                return True
        return False

    def _check_quality(self, info: dict) -> None:
        factor = self.plan.hfr_reject_factor
        hfr = info.get("hfr") if isinstance(info, dict) else None
        if not factor or hfr is None:
            return
        self._recent_hfr.append(float(hfr))
        self._recent_hfr = self._recent_hfr[-12:]
        if len(self._recent_hfr) >= 5:
            med = median(self._recent_hfr[:-1])
            if med > 0 and hfr > med * factor:
                self._rejected += 1
                bus.log("warning", f"frame HFR {hfr:.2f} >> median {med:.2f} — "
                                   "possible cloud / poor frame", "sequence")

    async def _autofocus(self, label: str) -> None:
        self._set_state(detail=label)
        _t0 = time.time()
        try:
            cam = self.hub.require("camera")
            foc = self.hub.require("focuser")
            result = await run_autofocus(cam, foc)
            if not result.success:
                bus.log("warning", f"{label} failed: {result.message}", "sequence")
            self._frames_since_focus = 0
            self._record_event_cost("autofocus", time.time() - _t0)
            try:
                self._last_focus_temp = await foc.get_temperature()
            except Exception:
                pass
        except Exception as e:
            bus.log("warning", f"{label} error: {e}", "sequence")

    async def _safe_stop(self) -> None:
        """Leave the rig in a safe state after abort/error."""
        try:
            cam = self.hub.devices.get("camera")
            if cam and cam.connected:
                await cam.abort_exposure()
        except Exception:
            pass
        try:
            if self.hub.guider and self.hub.guider.connected:
                await self.hub.guider.stop_guiding()
        except Exception:
            pass

    async def _wind_down(self, park: bool, warm: bool) -> None:
        try:
            if self.hub.guider and self.hub.guider.connected:
                await self.hub.guider.stop_guiding()
        except Exception:
            pass
        if park:
            tel = self.hub.devices.get("telescope")
            if tel and tel.connected:
                bus.log("info", "parking mount", "sequence")
                await tel.park()
        if warm:
            cam = self.hub.devices.get("camera")
            if cam and cam.connected and getattr(cam, "can_cool", False):
                bus.log("info", "warming camera", "sequence")
                await cam.set_cooler(False)
