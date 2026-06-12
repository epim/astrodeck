"""Equipment hub: owns connected devices and rig-level operations.

The hub is the single place that knows which physical device fills each role
(imaging camera, mount, focuser, wheel, power, guider). Everything above it
(API, sequencer) works in terms of roles.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .devices import alpaca as alpaca_backend
from .devices.base import Camera, DeviceError, FilterWheel, Focuser, Switch, Telescope
from .devices.sim import build_sim_rig
from .events import bus
from .guide import Guider, PHD2Guider, SimGuider
from .imaging import compute_histogram, save_fits, to_png
from .imaging.processing import frame_stats
from .solve import get_solver

ROLES = ("camera", "telescope", "focuser", "filterwheel", "switch")

CAPTURE_DIR = Path(__file__).resolve().parents[2] / "captures"


class Hub:
    def __init__(self) -> None:
        self.devices: dict[str, Any] = {}     # role -> Device
        self.guider: Guider | None = None
        self.sim_rig = None                    # set when sim profile connected
        self.site = {"latitude": 37.77, "longitude": -122.42}
        self.preview_seq = 0
        self.previews: dict[int, bytes] = {}   # ring buffer of PNG previews
        self.last_frame = None                  # most recent CameraFrame
        self._loop_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._busy: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------ connection

    async def connect_sim(self) -> dict:
        await self.disconnect_all()
        rig = build_sim_rig()
        self.sim_rig = rig.pop("_rig")
        guide_cam = rig.pop("guide_camera")
        for role, dev in rig.items():
            await dev.connect()
            self.devices[role] = dev
        await guide_cam.connect()
        self.devices["guide_camera"] = guide_cam
        self.guider = SimGuider()
        await self.guider.connect()
        bus.log("info", "simulator rig connected", "hub")
        self.ensure_status_poller()
        return self.summary()

    async def connect_alpaca_device(self, role: str, host: str, port: int,
                                    dev_type: str, dev_num: int, name: str) -> dict:
        dev = alpaca_backend.make_device(host, port, dev_type, dev_num, name)
        await dev.connect()
        old = self.devices.get(role)
        if old:
            try:
                await old.disconnect()
            except Exception:
                pass
        self.devices[role] = dev
        bus.log("info", f"{role} connected: {name} (Alpaca {host}:{port})", "hub")
        self.ensure_status_poller()
        return dev.describe()

    async def connect_phd2(self, host: str = "127.0.0.1", port: int = 4400) -> None:
        if self.guider:
            try:
                await self.guider.disconnect()
            except Exception:
                pass
        self.guider = PHD2Guider(host, port)
        await self.guider.connect()

    async def disconnect_all(self) -> None:
        self.stop_loop()
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
        self._status_task = None
        for task in self._busy.values():
            task.cancel()
        self._busy.clear()
        for dev in self.devices.values():
            try:
                await dev.disconnect()
            except Exception:
                pass
        self.devices.clear()
        if self.guider:
            try:
                await self.guider.disconnect()
            except Exception:
                pass
            self.guider = None
        self.sim_rig = None
        bus.log("info", "all equipment disconnected", "hub")

    def require(self, role: str):
        dev = self.devices.get(role)
        if dev is None or not dev.connected:
            raise DeviceError(f"no {role} connected")
        return dev

    def summary(self) -> dict:
        return {
            "devices": {r: d.describe() for r, d in self.devices.items()},
            "guider": {"name": self.guider.name, "connected": self.guider.connected}
            if self.guider else None,
            "sim": self.sim_rig is not None,
            "site": self.site,
        }

    # --------------------------------------------------------------- capture

    async def capture(self, exposure_s: float, gain: int, offset: int,
                      binning: int = 1, save: bool = False, target: str = "",
                      frame_type: str = "Light") -> dict:
        cam: Camera = self.require("camera")
        frame = await cam.expose(exposure_s, gain, offset, binning)
        self.last_frame = frame
        info = await self._publish_preview(frame)
        if save:
            path = self._capture_path(target or "untargeted", frame_type)
            ra = dec = None
            tel = self.devices.get("telescope")
            if tel and tel.connected:
                try:
                    ra, dec = await tel.get_position()
                except Exception:
                    pass
            fw = self.devices.get("filterwheel")
            filt = ""
            if fw and fw.connected:
                try:
                    filt = fw.filter_names[await fw.get_position()]
                except Exception:
                    pass
            save_fits(frame, path, target=target, filter_name=filt,
                      frame_type=frame_type, ra_hours=ra, dec_deg=dec,
                      instrument=cam.name)
            info["saved_path"] = str(path)
            bus.log("info", f"saved {path.name}", "capture")
        return info

    async def _publish_preview(self, frame) -> dict:
        png = await asyncio.to_thread(to_png, frame.data)
        self.preview_seq += 1
        self.previews[self.preview_seq] = png
        for old in [k for k in self.previews if k <= self.preview_seq - 8]:
            del self.previews[old]
        info = {
            "id": self.preview_seq,
            "stats": frame_stats(frame.data),
            "histogram": compute_histogram(frame.data),
            "exposure_s": frame.exposure_s,
            "gain": frame.gain,
            "binning": frame.binning,
            "width": int(frame.data.shape[1]),
            "height": int(frame.data.shape[0]),
        }
        bus.publish("preview", **info)
        return info

    def _capture_path(self, target: str, frame_type: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in target).strip() or "untargeted"
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self._frame_counter = getattr(self, "_frame_counter", 0) + 1
        return CAPTURE_DIR / safe / f"{frame_type}_{safe}_{stamp}_{self._frame_counter:04d}.fits"

    def start_loop(self, exposure_s: float, gain: int, offset: int,
                   binning: int = 1) -> None:
        self.stop_loop()

        async def _loop() -> None:
            while True:
                try:
                    await self.capture(exposure_s, gain, offset, binning)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # keep looping through transient errors
                    bus.log("error", f"loop capture failed: {e}", "capture")
                    await asyncio.sleep(1)

        self._loop_task = asyncio.create_task(_loop())
        bus.publish("capture_loop", running=True)

    def stop_loop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self._loop_task = None
        bus.publish("capture_loop", running=False)

    @property
    def looping(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    # -------------------------------------------------------- solve & center

    async def solve_and_sync(self, exposure_s: float = 3.0) -> dict:
        """Plate-solve the current pointing and sync the mount to it."""
        cam: Camera = self.require("camera")
        tel: Telescope = self.require("telescope")
        ra_hint, dec_hint = await tel.get_position()
        frame = await cam.expose(exposure_s, 200, 30, binning=2)
        await self._publish_preview(frame)
        tmp = CAPTURE_DIR / "_solve" / "solve.fits"
        save_fits(frame, tmp)
        solver = get_solver(self.sim_rig)
        bus.log("info", f"plate solving with {solver.name}…", "solve")
        result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                    fov_deg_hint=1.0)
        if not result.success:
            raise DeviceError(f"plate solve failed: {result.message}")
        await tel.sync(result.ra_hours, result.dec_deg)
        bus.log("info", f"solved & synced: RA {result.ra_hours:.4f}h "
                        f"Dec {result.dec_deg:+.3f}°", "solve")
        return {"ra_hours": result.ra_hours, "dec_deg": result.dec_deg,
                "solver": solver.name, "pixel_scale": result.pixel_scale_arcsec}

    async def goto_and_center(self, ra_hours: float, dec_deg: float,
                              tolerance_deg: float = 0.02,
                              max_attempts: int = 3,
                              solve_exposure_s: float = 3.0) -> dict:
        """Slew, then iterate solve→sync→re-slew until on target."""
        tel: Telescope = self.require("telescope")
        if await tel.is_parked():
            await tel.unpark()
        await tel.set_tracking(True)
        last_err = None
        for attempt in range(1, max_attempts + 1):
            bus.publish("mount", action="centering", attempt=attempt)
            await tel.slew(ra_hours, dec_deg)
            solved = await self.solve_and_sync(solve_exposure_s)
            err = _ang_sep_deg(solved["ra_hours"], solved["dec_deg"], ra_hours, dec_deg)
            last_err = err
            bus.log("info", f"centering attempt {attempt}: {err * 60:.1f}' off target", "solve")
            if err <= tolerance_deg:
                bus.publish("mount", action="centered", error_arcmin=err * 60)
                return {"centered": True, "error_arcmin": err * 60, "attempts": attempt}
        return {"centered": False, "error_arcmin": (last_err or 0) * 60,
                "attempts": max_attempts}

    # ---------------------------------------------------------------- status

    def ensure_status_poller(self) -> None:
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._status_loop())

    async def _status_loop(self) -> None:
        while True:
            try:
                bus.publish("status", **await self.poll_status())
            except Exception:
                pass
            await asyncio.sleep(2.0)

    async def poll_status(self) -> dict:
        out: dict[str, Any] = {"connected": self.summary()["devices"], "looping": self.looping}
        tel = self.devices.get("telescope")
        if tel and tel.connected:
            try:
                ra, dec = await tel.get_position()
                from .catalog import altaz, format_dec, format_ra
                alt, az = altaz(ra, dec, self.site["latitude"], self.site["longitude"])
                out["mount"] = {
                    "ra_hours": ra, "dec_deg": dec,
                    "ra_str": format_ra(ra), "dec_str": format_dec(dec),
                    "alt": round(alt, 1), "az": round(az, 1),
                    "tracking": await tel.get_tracking(),
                    "parked": await tel.is_parked(),
                    "slewing": await tel.is_slewing(),
                }
            except Exception:
                pass
        foc = self.devices.get("focuser")
        if foc and foc.connected:
            try:
                out["focuser"] = {
                    "position": await foc.get_position(),
                    "max": foc.max_position,
                    "temperature": await foc.get_temperature(),
                }
            except Exception:
                pass
        fw = self.devices.get("filterwheel")
        if fw and fw.connected:
            try:
                out["filterwheel"] = {
                    "position": await fw.get_position(),
                    "names": fw.filter_names,
                }
            except Exception:
                pass
        cam = self.devices.get("camera")
        if cam and cam.connected:
            try:
                out["camera"] = {
                    "temperature": await cam.get_temperature(),
                    "can_cool": cam.can_cool,
                    "width": cam.sensor_width, "height": cam.sensor_height,
                    "max_gain": cam.max_gain,
                }
            except Exception:
                pass
        if self.guider and self.guider.connected:
            out["guider"] = self.guider.stats().__dict__ | {"name": self.guider.name}
        return out


def _ang_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    import math
    ra1, ra2 = math.radians(ra1_h * 15), math.radians(ra2_h * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


hub = Hub()
