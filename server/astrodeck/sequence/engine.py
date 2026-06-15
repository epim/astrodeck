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


def _resume_file() -> Path:
    return _hubmod.CAPTURE_DIR / ".sequence_resume.json"


class SequenceEngine:
    def __init__(self, hub: Hub):
        self.hub = hub
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
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        self._paused.clear()
        self._set_state(state="paused")

    def resume(self) -> None:
        self._paused.set()
        self._set_state(state="running")

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
            elapsed = time.time() - self._started_at
            kw.setdefault("progress", {
                "frames_done": self._frames_done,
                "frames_total": total,
                "percent": round(100 * self._frames_done / total, 1) if total else 0,
                "elapsed_s": round(elapsed),
                "rejected": self._rejected,
            })
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
                try:
                    await self.hub.guider.dither(plan.dither_pixels)
                    self._frames_since_dither = 0
                except Exception as e:
                    bus.log("warning", f"dither failed: {e}", "sequence")

            if await self._refocus_due():
                await self._autofocus("refocus")

            self._set_state(state="running",
                            detail=f"{target.name}: {step.filter or 'no filter'} "
                                   f"{step.exposure_s:g}s  [{i + 1}/{step.count}]")
            info = await self.hub.capture(step.exposure_s, step.gain, step.offset, step.binning,
                                          save=True, target=target.name, frame_type=step.frame_type)
            self._check_quality(info)
            self._frames_since_dither += 1
            self._frames_since_focus += 1
            self._record_frame(key, i)

    def _record_frame(self, key: str, i: int) -> None:
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
            if t is not None and abs(t - target_c) <= 1.0:
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
        await self.hub.meridian_flip(target.ra_hours, target.dec_deg)
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
        try:
            cam = self.hub.require("camera")
            foc = self.hub.require("focuser")
            result = await run_autofocus(cam, foc)
            if not result.success:
                bus.log("warning", f"{label} failed: {result.message}", "sequence")
            self._frames_since_focus = 0
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
