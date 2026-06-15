"""Equipment hub: owns connected devices and rig-level operations.

The hub is the single place that knows which physical device fills each role
(imaging camera, mount, focuser, wheel, power, guider). Everything above it
(API, sequencer) works in terms of roles.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from .config import config_store, fov_deg, image_scale_arcsec_px
from .devices import alpaca as alpaca_backend
from .devices.base import Camera, DeviceError, FilterWheel, Focuser, Switch, Telescope
from .devices.nina import build_nina_rig, pick as nina_pick
from .devices.sim import build_sim_rig
from .events import bus
from .guide import Guider, PHD2Guider, SimGuider
from .imaging import compute_histogram, save_fits, to_png
from .imaging.processing import frame_stats
from .polar import PolarAlignSession
from .profiles import Profile, ProfileDevice, profiles
from .solve import get_solver

ROLES = ("camera", "telescope", "focuser", "filterwheel", "switch")

CAPTURE_DIR = Path(__file__).resolve().parents[2] / "captures"


class Hub:
    def __init__(self) -> None:
        self.devices: dict[str, Any] = {}     # role -> Device
        self.guider: Guider | None = None
        self.sim_rig = None                    # set when sim profile connected
        self.nina_client = None                # set when bridged to NINA
        self.mode = "none"                     # none | sim | alpaca | nina
        # site is no longer hardcoded — it is a property backed by config_store
        # (fixes the San-Francisco P0). See the `site` property below.
        self.preview_seq = 0
        self.previews: dict[int, tuple[bytes, str]] = {}  # id -> (bytes, mime)
        self.last_frame = None                  # most recent CameraFrame
        self._loop_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._nina_ws_task: asyncio.Task | None = None
        self._nina_hb_task: asyncio.Task | None = None   # 5s NINA heartbeat
        self._bridge_ready = False              # false until first successful NINA poll
        self._busy: dict[str, asyncio.Task] = {}
        self.polar = PolarAlignSession(self)
        # cache of the active Profile, keyed by its id, so the 2s status poll's
        # effective_optics() never does a blocking disk read on the event loop.
        # Invalidated on apply/save/delete and on an active-id change.
        self._profile_cache_id: str | None = None
        self._profile_cache: Profile | None = None

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
        self.mode = "sim"
        bus.log("info", "simulator rig connected", "hub")
        self.ensure_status_poller()
        return self.summary()

    async def connect_nina(self, host: str, port: int = 1888) -> dict:
        """Bridge to a running NINA instance (Advanced API plugin)."""
        await self.disconnect_all()
        self._bridge_ready = False             # warming-up until first heartbeat
        rig = await build_nina_rig(host, port)
        self.nina_client = rig["client"]
        self.mode = "nina"
        for role, dev in rig["devices"].items():
            self.devices[role] = dev
        if rig["guider"]:
            self.guider = rig["guider"]
        roles = ", ".join(rig["devices"]) or "no equipment connected in NINA"
        bus.log("info", f"bridged to NINA at {host}:{port} — {roles}", "nina")
        self.ensure_status_poller()
        self._start_nina_ws()
        return self.summary()

    async def connect_alpaca_device(self, role: str, host: str, port: int,
                                    dev_type: str, dev_num: int, name: str) -> dict:
        dev = alpaca_backend.make_device(host, port, dev_type, dev_num, name)
        await dev.connect()
        dev.role = role                        # device identity for Profiles (A.6)
        old = self.devices.get(role)
        if old:
            try:
                await old.disconnect()
            except Exception:
                pass
        self.devices[role] = dev
        self.mode = "alpaca"
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
        await self.polar.stop()
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
        self._status_task = None
        if self._nina_ws_task and not self._nina_ws_task.done():
            self._nina_ws_task.cancel()
        self._nina_ws_task = None
        if self._nina_hb_task and not self._nina_hb_task.done():
            self._nina_hb_task.cancel()
        self._nina_hb_task = None
        self._bridge_ready = False
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
        if self.nina_client is not None:
            try:
                await self.nina_client.close()
            except Exception:
                pass
            self.nina_client = None
        self.sim_rig = None
        self.mode = "none"
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
            "mode": self.mode,
            "site": self.site,
            "optics": self.effective_optics(),
        }

    # ------------------------------------------------------------ site & optics

    @property
    def site(self) -> dict:
        """The observing site as a plain dict — the single reconciliation point
        read by altaz/polar/catalog/meridian. Backed by ``config_store`` so it is
        persisted and no longer hardcoded. Carries the merged onboarding fields
        (``is_default``/``horizon_min_deg``) every later batch only reads."""
        s = config_store.cfg().site
        return {"name": s.name, "latitude": s.latitude, "longitude": s.longitude,
                "elevation_m": s.elevation_m, "is_default": s.is_default,
                "horizon_min_deg": s.horizon_min_deg}

    def mark_site_configured(self, horizon_min_deg: float | None = None) -> None:
        """Flip a deliberately-saved site off ``is_default`` and record the
        per-site minimum altitude (onboarding). Called by the API after a
        successful ``PUT /api/site``."""
        cfg = config_store.cfg()
        update: dict = {"is_default": False}
        if horizon_min_deg is not None:
            update["horizon_min_deg"] = horizon_min_deg
        cfg.site = cfg.site.model_copy(update=update)
        config_store.bump_and_save()

    def invalidate_profile_cache(self) -> None:
        """Drop the cached active Profile so the next ``effective_optics`` re-reads
        from disk. Call after any save/delete/apply that may change the active
        profile's content."""
        self._profile_cache_id = None
        self._profile_cache = None

    def _active_profile(self) -> Profile | None:
        """Active Profile, served from an in-memory cache so the 2s status poll
        never blocks the event loop on a disk read. The cache is keyed by the
        active-profile id, so flipping the active profile auto-invalidates; an
        in-place save/delete of the active profile invalidates explicitly via
        ``invalidate_profile_cache``."""
        active_id = config_store.cfg().active_profile_id
        if not active_id:
            return None
        if active_id != self._profile_cache_id:
            self._profile_cache = profiles.active(active_id)
            self._profile_cache_id = active_id
        return self._profile_cache

    def effective_optics(self) -> dict:
        """Resolve config-override-or-camera optics with an explicit source +
        availability flag, plus the computed image scale / FOV (bin-1). The
        per-active-profile override wins at READ time and never stomps global."""
        o = config_store.cfg().optics
        prof = self._active_profile()
        if prof and prof.optics:
            o = prof.optics
        cam = self.devices.get("camera")
        cam_on = bool(cam and cam.connected)
        px = o.pixel_size_um or (getattr(cam, "pixel_size_um", 0.0) if cam_on else 0.0)
        w = o.sensor_width_px or (getattr(cam, "sensor_width", 0) if cam_on else 0)
        h = o.sensor_height_px or (getattr(cam, "sensor_height", 0) if cam_on else 0)
        have = bool(px and w and h)
        fw, fh, diag = fov_deg(o.focal_length_mm, px, w, h) if have else (0.0, 0.0, 0.0)
        if o.pixel_size_um and o.sensor_width_px:
            src = "config"
        elif not o.pixel_size_um and not o.sensor_width_px and cam_on:
            src = "camera"
        elif have:
            src = "mixed"
        else:
            src = "none"
        return {
            "focal_length_mm": o.focal_length_mm,
            "pixel_size_um": px,
            "sensor_width_px": int(w),
            "sensor_height_px": int(h),
            "have_optics": have,
            "source": src,
            "image_scale_arcsec_px":
                round(image_scale_arcsec_px(o.focal_length_mm, px), 2) if have else None,
            "fov_w_deg": round(fw, 3) if have else None,
            "fov_h_deg": round(fh, 3) if have else None,
            "fov_diag_deg": round(diag, 3) if have else None,
        }

    async def push_site_to_mount(self) -> None:
        """Best-effort: push the saved site to a connected Alpaca telescope so
        the app-side and mount-side LST agree. No-op for backends without
        setters (NINA/sim); never fatal."""
        tel = self.devices.get("telescope")
        if not (tel and tel.connected):
            return
        put = getattr(tel, "_put", None)
        if put is None:
            return
        s = self.site
        try:
            await put("sitelatitude", SiteLatitude=s["latitude"])
            await put("sitelongitude", SiteLongitude=s["longitude"])
            await put("siteelevation", SiteElevation=s["elevation_m"])
            bus.log("info", "pushed observing site to mount", "config")
        except Exception as e:
            bus.log("warning", f"could not push site to mount: {e}", "config")

    def _check_horizon(self, ra_hours: float, dec_deg: float, *, force: bool = False) -> None:
        """Server-side below-horizon guard (defense in depth). Inert on a default
        site; blocks only ``alt < 0`` (the visible horizon) on a real site. Called
        from user-initiated GOTO / sequence-start paths only — never from
        ``goto_and_center`` (shared by meridian_flip)."""
        s = self.site
        if s.get("is_default"):
            return
        from .catalog import altaz
        alt, _ = altaz(ra_hours, dec_deg, s["latitude"], s["longitude"])
        if alt < 0 and not force:
            raise DeviceError(
                f"target is below the visible horizon (alt {alt:.0f}°)")

    # ----------------------------------------------------------- reliability

    @property
    def busy_label(self) -> str | None:
        """One word for the current long backend op (or None). Drives the
        telemetry-stale suppression — a slew/solve/AF/capture legitimately
        starves the 2s status poll, so "busy" means "not stalled"."""
        live = {n for n, t in self._busy.items() if t and not t.done()}
        if self.looping:
            live.add("looping")
        for name, label in (("goto", "slewing"), ("solve", "solving"),
                            ("autofocus", "focusing"), ("focuser", "focusing"),
                            ("capture", "capturing"), ("looping", "capturing")):
            if name in live:
                return label
        return None

    async def _nina_heartbeat(self) -> None:
        """Every 5s, ping NINA ``/version`` so ``last_ok`` stays honest even when
        a multi-minute capture means no other NINA traffic. Never fatal."""
        while self.nina_client is not None:
            try:
                await self.nina_client.get("/version", timeout=8.0)
                # guard the disconnect race: a teardown between the await above
                # and here must not flip _bridge_ready true after the client is
                # gone.
                if self.nina_client is None:
                    return
                self._bridge_ready = True
            except Exception:
                pass
            await asyncio.sleep(5.0)

    # --------------------------------------------------------------- profiles

    async def apply_profile(self, p: Profile) -> dict:
        """Replay a profile's connection intent, returning a per-device outcome
        (never a lying "all connected" toast). Auto-detected camera optics are
        persisted back so a later disconnected apply still has a real FOV."""
        await self.disconnect_all()
        results: list[dict] = []
        for d in p.devices:
            if d.backend != "alpaca":
                continue
            try:
                await self.connect_alpaca_device(
                    d.role, d.host, d.port, d.dev_type, d.dev_num,
                    d.name or f"{d.dev_type} #{d.dev_num}")
                results.append({"role": d.role, "ok": True})
            except Exception as e:
                results.append({"role": d.role, "ok": False, "error": str(e)})
                bus.log("warning", f"profile '{p.name}': {d.role} failed: {e}", "profile")
        if p.nina_host and any(d.backend == "nina" for d in p.devices):
            try:
                await self.connect_nina(p.nina_host, p.nina_port)
                results.append({"role": "nina", "ok": True})
            except Exception as e:
                results.append({"role": "nina", "ok": False, "error": str(e)})
        if p.phd2_host:
            try:
                await self.connect_phd2(p.phd2_host, p.phd2_port)
                results.append({"role": "phd2", "ok": True})
            except Exception as e:
                results.append({"role": "phd2", "ok": False, "error": str(e)})
        cam = self.devices.get("camera")
        if cam and cam.connected and config_store.cfg().optics.auto_from_camera:
            o = config_store.cfg().optics
            new_optics = o.model_copy(update={
                "pixel_size_um": o.pixel_size_um or getattr(cam, "pixel_size_um", 0.0),
                "sensor_width_px": o.sensor_width_px or getattr(cam, "sensor_width", 0),
                "sensor_height_px": o.sensor_height_px or getattr(cam, "sensor_height", 0),
            })
            # offload the blocking disk write (with its time.sleep retry) so it
            # never freezes the event loop on the Windows target.
            await asyncio.to_thread(
                config_store.set_optics, new_optics, None)
        await asyncio.to_thread(config_store.set_active_profile, p.id)
        # seed the active-profile cache from the object we already hold (no disk
        # read), so the next effective_optics() is served from memory.
        self._profile_cache = p
        self._profile_cache_id = p.id
        bus.publish("config", version=config_store.cfg().version)
        ok = sum(1 for r in results if r["ok"])
        # summary must be a real RigStatus shape (connected/looping/mode/site/
        # optics/busy) — poll_status emits exactly that; devices are connected
        # at this point so the device I/O is fine.
        return {"summary": await self.poll_status(), "results": results,
                "connected": ok, "total": len(results)}

    async def capture_profile(self, name: str) -> Profile:
        """Build a profile from the currently-connected devices (uses the
        device-identity contract) and save it."""
        devs: list[ProfileDevice] = []
        for role, d in self.devices.items():
            if role not in ROLES:
                continue
            if getattr(d, "backend", None) == "alpaca":
                devs.append(ProfileDevice(
                    role=role, backend="alpaca", host=getattr(d, "host", ""),
                    port=getattr(d, "port", 0), dev_type=getattr(d, "dev_type", ""),
                    dev_num=getattr(d, "dev_num", 0), name=d.name))
            elif self.mode == "nina":
                devs.append(ProfileDevice(role=role, backend="nina", name=d.name))
        p = Profile(name=name, devices=devs,
                    nina_host=(self.nina_client.host if self.nina_client else None),
                    site_name=self.site.get("name"))
        profiles.save(p)
        return p

    # --------------------------------------------------------------- capture

    async def capture(self, exposure_s: float, gain: int, offset: int,
                      binning: int = 1, save: bool = False, target: str = "",
                      frame_type: str = "Light") -> dict:
        cam: Camera = self.require("camera")
        frame = await cam.expose(exposure_s, gain, offset, binning,
                                 light=(frame_type.upper() != "DARK"),
                                 save=save, target=target)
        self.last_frame = frame
        info = await self._publish_preview(frame)
        if save and frame.rendered_bytes is not None:
            # The backend (NINA) already saved the file on the imaging machine.
            if frame.saved_path:
                info["saved_path"] = frame.saved_path
                bus.log("info", f"NINA saved {Path(frame.saved_path).name}", "capture")
            else:
                bus.log("info", "NINA saved the frame", "capture")
        elif save:
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
        # NINA frames arrive pre-rendered (auto-stretched) — use them verbatim;
        # raw frames (sim/Alpaca) get our screen stretch.
        if getattr(frame, "rendered_bytes", None) is not None:
            png, mime = frame.rendered_bytes, frame.rendered_mime
        else:
            png = await asyncio.to_thread(to_png, frame.data)
            mime = "image/png"
        self.preview_seq += 1
        self.previews[self.preview_seq] = (png, mime)
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
        if getattr(frame, "hfr", None) is not None:
            info["hfr"] = round(float(frame.hfr), 2)
        if getattr(frame, "stars", None) is not None:
            info["stars"] = int(frame.stars)
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
        if self.nina_client is not None:
            return await self._nina_solve_and_sync(exposure_s)
        cam: Camera = self.require("camera")
        tel: Telescope = self.require("telescope")
        ra_hint, dec_hint = await tel.get_position()
        frame = await cam.expose(exposure_s, 200, 30, binning=2)
        await self._publish_preview(frame)
        tmp = CAPTURE_DIR / "_solve" / "solve.fits"
        save_fits(frame, tmp)
        solver = get_solver(self.sim_rig)
        # FOV hint from the configured optics (bin-1, bin-independent — correct
        # even though the solve frame is binned 2×). None → ASTAP radius search,
        # preserving the old behavior when optics aren't known.
        opt = self.effective_optics()
        # ASTAP's -fov expects the VERTICAL (height) field, not the diagonal —
        # the diagonal is ~1.2–1.8× larger and over-widens the scale search.
        fov_hint = opt["fov_h_deg"] or None
        bus.log("info",
                f"plate solving with {solver.name} (fov hint {fov_hint or 'auto'})…",
                "solve")
        result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                    fov_deg_hint=fov_hint)
        if not result.success:
            raise DeviceError(f"plate solve failed: {result.message}")
        await tel.sync(result.ra_hours, result.dec_deg)
        bus.log("info", f"solved & synced: RA {result.ra_hours:.4f}h "
                        f"Dec {result.dec_deg:+.3f}°", "solve")
        return {"ra_hours": result.ra_hours, "dec_deg": result.dec_deg,
                "solver": solver.name, "pixel_scale": result.pixel_scale_arcsec}

    async def _nina_solve_and_sync(self, exposure_s: float) -> dict:
        """Capture through NINA and let NINA plate-solve the prepared image,
        then sync the mount to the solution."""
        cam: Camera = self.require("camera")
        tel: Telescope = self.require("telescope")
        frame = await cam.expose(exposure_s, 200, 30, binning=2)
        self.last_frame = frame
        await self._publish_preview(frame)
        bus.log("info", "plate solving with NINA…", "solve")
        res = await self.nina_client.get("/prepared-image/solve") or {}
        coords = nina_pick(res, "Coordinates", default=res)
        ra = nina_pick(coords, "RA", "RAHours", "RightAscension")
        dec = nina_pick(coords, "Dec", "Declination")
        if ra is None and nina_pick(coords, "RADegrees") is not None:
            ra = float(nina_pick(coords, "RADegrees")) / 15.0
        if ra is None or dec is None:
            raise DeviceError("NINA plate solve did not return coordinates")
        ra, dec = float(ra), float(dec)
        await tel.sync(ra, dec)
        bus.log("info", f"solved & synced (NINA): RA {ra:.4f}h Dec {dec:+.3f}°", "solve")
        return {"ra_hours": ra, "dec_deg": dec, "solver": "NINA",
                "pixel_scale": float(nina_pick(res, "Pixscale", "PixelScale", default=0) or 0)}

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

    async def meridian_flip(self, ra_hours: float, dec_deg: float) -> dict:
        """Flip a German equatorial mount across the meridian: stop guiding,
        re-slew (the mount chooses the far side of the pier), plate-solve
        re-center, and restart guiding."""
        bus.publish("mount", action="meridian_flip")
        bus.log("info", "meridian flip: stopping guiding and re-slewing", "sequence")
        was_guiding = False
        if self.guider and self.guider.connected:
            try:
                was_guiding = await self.guider.is_active()
                await self.guider.stop_guiding()
            except Exception:
                pass
        result = await self.goto_and_center(ra_hours, dec_deg)
        if was_guiding:
            try:
                await self.guider.start_guiding()
            except Exception as e:
                bus.log("warning", f"meridian flip: guiding restart failed: {e}", "sequence")
        bus.log("info", "meridian flip complete", "sequence")
        return result

    # ------------------------------------------------------------ NINA events

    def _start_nina_ws(self) -> None:
        if self._nina_ws_task is None or self._nina_ws_task.done():
            self._nina_ws_task = asyncio.create_task(self._nina_ws_loop())

    async def _nina_ws_loop(self) -> None:
        """Subscribe to NINA's event stream so AstroDeck reflects activity that
        NINA itself initiates (e.g. its own sequence running) — surfaced as log
        lines. Reconnects with backoff; never fatal."""
        try:
            import websockets
        except ImportError:
            return
        backoff = 3.0
        while self.nina_client is not None:
            try:
                async with websockets.connect(self.nina_client.ws_url,
                                               ping_interval=20, open_timeout=10) as ws:
                    bus.log("info", "subscribed to NINA event stream", "nina")
                    backoff = 3.0
                    async for raw in ws:
                        try:
                            self._handle_nina_event(json.loads(raw))
                        except (ValueError, TypeError):
                            continue
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.6, 30.0)

    def _handle_nina_event(self, msg: dict) -> None:
        resp = msg.get("Response", msg) if isinstance(msg, dict) else {}
        event = nina_pick(resp, "Event", default="")
        if not event:
            return
        if event == "IMAGE-SAVE":
            s = nina_pick(resp, "ImageStatistics", default={}) or {}
            bits = []
            if nina_pick(s, "Filter"):
                bits.append(str(nina_pick(s, "Filter")))
            if nina_pick(s, "HFR") is not None:
                bits.append(f"HFR {float(nina_pick(s, 'HFR')):.2f}")
            if nina_pick(s, "Stars") is not None:
                bits.append(f"{nina_pick(s, 'Stars')} stars")
            bus.log("info", "NINA saved an image" + (f" ({', '.join(bits)})" if bits else ""),
                    "nina")
        elif event.endswith("-CONNECTED") or event.endswith("-DISCONNECTED"):
            bus.log("info", f"NINA: {event.lower().replace('-', ' ')}", "nina")
        elif event in ("AUTOFOCUS-FINISHED", "ERROR-AF"):
            bus.log("info", f"NINA: {event.lower().replace('-', ' ')}", "nina")

    # ---------------------------------------------------------------- status

    def ensure_status_poller(self) -> None:
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._status_loop())
        if self.mode == "nina" and (self._nina_hb_task is None or self._nina_hb_task.done()):
            self._nina_hb_task = asyncio.create_task(self._nina_heartbeat())

    async def _status_loop(self) -> None:
        while True:
            try:
                bus.publish("status", **await self.poll_status())
            except Exception:
                pass
            await asyncio.sleep(2.0)

    async def poll_status(self) -> dict:
        out: dict[str, Any] = {"connected": self.summary()["devices"],
                               "looping": self.looping, "mode": self.mode}
        # These must live in poll_status (not just summary): the store does a
        # wholesale set({status}) every 2s, so anything absent here flickers.
        s = self.site
        out["site"] = {"name": s["name"], "latitude": s["latitude"],
                       "longitude": s["longitude"], "elevation_m": s["elevation_m"],
                       "is_default": s["is_default"],
                       "horizon_min_deg": s["horizon_min_deg"]}
        out["optics"] = self.effective_optics()        # in-process, no device I/O
        out["busy"] = self.busy_label                  # reliability: busy-aware stale
        try:
            du = shutil.disk_usage(CAPTURE_DIR)
            free_gb = du.free / 1e9
            out["disk"] = {"free_gb": round(free_gb, 1),
                           "low": free_gb < 10, "critical": free_gb < 1}
        except OSError:
            pass
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
                    "has_dew_heater": getattr(cam, "has_dew_heater", False),
                    "width": cam.sensor_width, "height": cam.sensor_height,
                    "max_gain": cam.max_gain,
                }
            except Exception:
                pass
        if self.guider and self.guider.connected:
            out["guider"] = self.guider.stats().__dict__ | {"name": self.guider.name}
        if self.mode == "nina" and self.nina_client is not None:
            c = self.nina_client
            age = (time.monotonic() - c.last_ok) if c.last_ok is not None else None
            busy = self.busy_label is not None
            # busy-aware: a long exposure/solve/AF legitimately starves the
            # heartbeat window, so don't call it unhealthy while we know we're busy.
            healthy = (not self._bridge_ready) or busy or (age is not None and age <= 45.0)
            out["nina_link"] = {
                "active": True,
                "last_ok_age_s": round(age, 1) if age is not None else None,
                "last_error": c.last_error,
                "healthy": healthy,
                "warming_up": not self._bridge_ready,
            }
        return out


def _ang_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    import math
    ra1, ra2 = math.radians(ra1_h * 15), math.radians(ra2_h * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


hub = Hub()
