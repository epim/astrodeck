"""Sequence engine: runs an imaging plan autonomously.

Per target: slew → (center via plate solve) → (autofocus) → start guiding →
for each step: set filter → expose ×count, dithering every N frames and
re-focusing every M frames. Pause/resume/abort safe at frame boundaries.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..devices.base import DeviceError
from ..events import bus
from ..focus import run_autofocus
from ..hub import Hub
from .models import SequencePlan


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
        self._started_at = 0.0

    # ----------------------------------------------------------------- control

    def start(self, plan: SequencePlan) -> None:
        if self.running:
            raise DeviceError("a sequence is already running")
        self.plan = plan
        self._frames_done = 0
        self._frames_since_dither = 0
        self._frames_since_focus = 0
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

    # ------------------------------------------------------------------- run

    def _set_state(self, **kw: Any) -> None:
        if self.plan:
            total = self.plan.total_frames()
            elapsed = time.time() - self._started_at
            kw.setdefault("progress", {
                "frames_done": self._frames_done,
                "frames_total": total,
                "percent": round(100 * self._frames_done / total, 1) if total else 0,
                "elapsed_s": round(elapsed),
            })
            kw.setdefault("plan_name", self.plan.name)
        self.state = {**self.state, **kw}
        bus.publish("sequence", **self.state)

    async def _checkpoint(self) -> None:
        """Frame-boundary gate: honors pause and cancellation."""
        await self._paused.wait()

    async def _run(self) -> None:
        plan = self.plan
        assert plan is not None
        try:
            self._set_state(state="running", detail=f"starting plan '{plan.name}'")
            bus.log("info", f"sequence '{plan.name}' started: "
                            f"{plan.total_frames()} frames, "
                            f"{plan.total_seconds() / 60:.0f} min integration", "sequence")

            for ti, target in enumerate(plan.targets):
                await self._checkpoint()
                self._set_state(target=target.name, target_index=ti,
                                detail=f"slewing to {target.name}")
                bus.log("info", f"target {ti + 1}/{len(plan.targets)}: {target.name}", "sequence")

                if "telescope" in self.hub.devices:
                    if target.center:
                        result = await self.hub.goto_and_center(target.ra_hours, target.dec_deg)
                        if not result["centered"]:
                            bus.log("warning",
                                    f"{target.name}: centering converged to "
                                    f"{result['error_arcmin']:.1f}' — continuing", "sequence")
                    else:
                        tel = self.hub.require("telescope")
                        if await tel.is_parked():
                            await tel.unpark()
                        await tel.set_tracking(True)
                        await tel.slew(target.ra_hours, target.dec_deg)

                if target.autofocus_first and "focuser" in self.hub.devices:
                    await self._autofocus("initial autofocus")

                if plan.guide and self.hub.guider and self.hub.guider.connected:
                    self._set_state(detail="starting guiding")
                    try:
                        await self.hub.guider.start_guiding()
                    except Exception as e:
                        bus.log("warning", f"guiding failed to start: {e} — "
                                           "continuing unguided", "sequence")

                for step in target.steps:
                    await self._run_step(target, step)

            self._set_state(state="complete", detail="all targets complete")
            bus.log("info", f"sequence '{plan.name}' complete: "
                            f"{self._frames_done} frames", "sequence")
            await self._wind_down(park=plan.park_when_done,
                                  warm=plan.warm_cooler_when_done)
        except asyncio.CancelledError:
            bus.log("warning", "sequence aborted", "sequence")
            await self._safe_stop()
            raise
        except Exception as e:
            bus.log("error", f"sequence failed: {e}", "sequence")
            self._set_state(state="error", detail=str(e))
            await self._safe_stop()

    async def _run_step(self, target, step) -> None:
        plan = self.plan
        assert plan is not None
        if step.filter and "filterwheel" in self.hub.devices:
            fw = self.hub.require("filterwheel")
            if step.filter in fw.filter_names:
                self._set_state(detail=f"filter → {step.filter}")
                await fw.set_position(fw.filter_names.index(step.filter))
            else:
                bus.log("warning", f"filter '{step.filter}' not in wheel — skipping move",
                        "sequence")

        for i in range(step.count):
            await self._checkpoint()

            if plan.dither_every and self._frames_since_dither >= plan.dither_every \
                    and self.hub.guider and self.hub.guider.connected:
                self._set_state(detail="dithering")
                try:
                    await self.hub.guider.dither(plan.dither_pixels)
                    self._frames_since_dither = 0
                except Exception as e:
                    bus.log("warning", f"dither failed: {e}", "sequence")

            if plan.autofocus_every and self._frames_since_focus >= plan.autofocus_every \
                    and "focuser" in self.hub.devices:
                await self._autofocus("periodic autofocus")

            self._set_state(state="running",
                            detail=f"{target.name}: {step.filter or 'no filter'} "
                                   f"{step.exposure_s:g}s  [{i + 1}/{step.count}]")
            await self.hub.capture(step.exposure_s, step.gain, step.offset,
                                   step.binning, save=True, target=target.name,
                                   frame_type=step.frame_type)
            self._frames_done += 1
            self._frames_since_dither += 1
            self._frames_since_focus += 1
            self._set_state()

    async def _autofocus(self, label: str) -> None:
        self._set_state(detail=label)
        try:
            cam = self.hub.require("camera")
            foc = self.hub.require("focuser")
            result = await run_autofocus(cam, foc)
            if not result.success:
                bus.log("warning", f"{label} failed: {result.message}", "sequence")
            self._frames_since_focus = 0
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
            if cam and cam.connected and cam.can_cool:
                bus.log("info", "warming camera", "sequence")
                await cam.set_cooler(False)
