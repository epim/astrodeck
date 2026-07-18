"""Native (Rust engine) autoguider.

This is the ``astrodeck`` guide provider: it drives the same expose → measure →
correct loop the PHD2 bridge does, but the *policy* (star detection, calibration
state machine, the per-axis guide algorithms, static backlash compensation,
lost-star recovery) lives in the Rust engine (``astrodeck_native.GuideEngine``),
so an Alpaca/native/sim rig gets NINA-parity guiding with no PHD2 and no NINA.

Device I/O stays here in the host (async guide-camera exposures + mount
pulse-guides); the engine is a pure per-frame state machine we call
``process(frame, ts, exp)`` on once per exposed frame and dispatch the returned
``Action`` dict (dossier §7):

    engine = GuideEngine(config)
    engine.begin_calibration(x, y)          # after locating the star
    while calibrating:  process → "cal_step" → mount.pulse_guide(dir, ms)
    # engine auto-transitions to guiding when the calibration completes
    while guiding:      process → "pulse_pair" → mount.pulse_guide(...) ×≤2

We publish the SAME ``GuideStats`` shape on the SAME ``"guide"`` bus channel the
PHD2 path publishes (spec §3.2/§3.5 — the MonitorView/Sparkline/SessionsPanel/
GuideView contract), so the UI works unchanged whichever provider is guiding.

Scope of this task (P1): a working converging loop + minimal sim wiring. Full
calibration REUSE across sessions, the full dither behavior, and the UI provider
wiring land in P2 — the seams are here (calibration is persisted on calibrate;
``dither`` shifts the lock and waits for settle; the guiding-start pier-flip host
contract is discharged) but the richer policy is deferred.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import random
import time

from ..devices.base import Camera, DeviceError, Telescope
from ..events import bus
from ..providers import NATIVE_AVAILABLE
from .base import Guider, GuideStats

# Guarded handle to the Rust wheel. ``NATIVE_AVAILABLE`` (the single source of
# truth) already told us whether the import can succeed; we re-import here only
# to get the module object for the ``GuideEngine`` / ``guide_star_find`` calls.
# When the wheel is absent this stays None and ``connect`` refuses with a clear
# DeviceError rather than NameError-ing deep in the loop (focus/native.py idiom).
try:  # pragma: no cover - covered both ways via NATIVE_AVAILABLE monkeypatch
    import astrodeck_native as _native
except ImportError:  # pragma: no cover
    _native = None


# Exposure defaults for a guide camera when the caller pins nothing. Guide
# cadences are 0.5–5 s; a bright, unambiguous guide star needs little gain.
_DEFAULT_EXPOSURE_S = 2.0
_DEFAULT_GAIN = 100
_DEFAULT_OFFSET = 30

# Calibration step sizing: aim to cross ``calibration_distance`` in about this
# many pulses (PHD2's calstep pattern), derived from the mount's declared guide
# rate + image scale. Clamped so a mis-reported rate can never produce a
# pathologically short/long pulse.
_CAL_TARGET_STEPS = 12
_CAL_MS_MIN = 300
_CAL_MS_MAX = 2500

# Generous wall-clock cap for a full calibration walk (~6 legs). The sim's
# pulse_guide sleeps for the pulse duration, so a real calibration is tens of
# seconds; this only guards a wedged mount that never moves the star.
_CAL_TIMEOUT_S = 180.0

# Consecutive ``star_lost`` frames tolerated before the loop reports itself
# inactive (so the sequence engine's _maybe_recover_guiding sees is_active go
# false on a real loss). Full multi-step reacquire lands in P2.
_REACQUIRE_BUDGET = 8

# Cap on how long ``dither`` waits for the engine's settle window to close.
_SETTLE_TIMEOUT_S = 90.0


class NativeGuider(Guider):
    """The native Rust-engine autoguider (spec §3.2/§5).

    Owns the exposure → ``process`` → ``pulse_guide`` → ``bus.publish("guide")``
    loop over a dedicated guide ``Camera`` and a pulse-guide-capable
    ``Telescope``. Implements the full ``Guider`` ABC; publishes the SAME
    ``GuideStats`` shape as ``guide/phd2.py``.
    """

    name = "AstroDeck native"
    #: The native engine can flip its calibration for a meridian flip (dossier
    #: §9 item 4) — the meridian-flip path (hub.meridian_flip) calls
    #: ``flip_calibration``; the guiding-START flip is the host contract below.
    can_flip_calibration = True

    def __init__(self, guide_camera: Camera, telescope: Telescope, *,
                 config: dict, profile_id: str | None = None) -> None:
        self.cam = guide_camera
        self.tel = telescope
        self.config = dict(config or {})
        self.profile_id = profile_id

        self._engine = None
        self._loop_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._start_lock = asyncio.Lock()

        # Host-side guiding state. ``_active`` is our guiding INTENT (drives
        # stats().guiding together with the engine's own phase); ``_lost`` latches
        # a real, unrecoverable star loss so is_active() goes false (the recovery
        # contract). ``_reacquire`` counts consecutive star_lost frames.
        self._active = False
        self._lost = False
        self._reacquire = 0

        # Dither settle coordination: ``dither`` opens the engine's settle window
        # and awaits ``_settle_done``; the guide loop sets it when the window
        # (which only ``dither`` opens in P1) closes.
        self._settle_open = False
        self._settle_done = asyncio.Event()
        self._settle_error: str | None = None

        self._last_frame = None            # last exposed numpy frame (guide_frame)
        self._last_stats = GuideStats()

        cfg = self.config
        self._exposure_s = float(cfg.get("exposure_s", _DEFAULT_EXPOSURE_S))
        self._gain = int(cfg.get("gain", _DEFAULT_GAIN))
        self._offset = int(cfg.get("offset", _DEFAULT_OFFSET))
        self._binning = int(cfg.get("binning", 1))
        self._image_scale = float(cfg.get("image_scale_arcsec", 1.0))
        # Mount-specific meridian-flip constant (PHD2's CalFlipRequiresDecFlip);
        # default False matches the common GEM. Used by BOTH the guiding-start
        # host contract and the meridian-flip ABC method.
        self._flip_requires_dec_flip = bool(cfg.get("flip_requires_dec_flip", False))

    # --------------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        """Verify prerequisites and mark connected. Refuses (actionable
        ``DeviceError``, spec §4) when the wheel is absent or the mount cannot
        pulse guide — never issues pulses that would silently go nowhere."""
        if not NATIVE_AVAILABLE or _native is None:
            raise DeviceError(
                "native guider unavailable: the astrodeck_native engine is not "
                "installed")
        if not getattr(self.tel, "can_pulse_guide", False):
            raise DeviceError(
                f"native guiding requires a pulse-guide-capable mount "
                f"({getattr(self.tel, 'name', 'mount')} reports CanPulseGuide = "
                f"False)")
        self.connected = True
        bus.log("info", "native guider connected", "guide")

    async def disconnect(self) -> None:
        await self.stop_guiding()
        self.connected = False
        bus.log("info", "native guider disconnected", "guide")

    # ----------------------------------------------------------- start / stop

    async def start_guiding(self) -> None:
        """Select the star, calibrate, and begin guiding; returns once guiding
        is active (calibration complete + the engine in its guiding phase)."""
        async with self._start_lock:
            # Already-active guard INSIDE the lock (milestone review I2): two
            # idle-state initiators (an API start racing a sequence-engine direct
            # call) would otherwise both pass an outside guard, serialize on the
            # lock, and BOTH calibrate — orphaning the first loop task. Checked
            # here, the second starter sees the first's active loop and returns.
            if (self._active and self._loop_task is not None
                    and not self._loop_task.done()):
                return
            self._lost = False
            self._reacquire = 0
            self._settle_open = False
            rates = await self._read_guide_rates()
            self._engine = _native.GuideEngine(self._build_engine_config(rates))

            await self._calibrate()              # blocks; raises on failure
            # HOST CONTRACT (T8 / upstream mount.cpp:1338-1344): auto-flip the
            # calibration at guiding start if the mount's pier side differs from
            # the stored calibration's. A no-op for a fresh calibration (the
            # scope pointing already stamped the current pier); load-bearing once
            # P2 reuses a persisted calibration across a pier-side change.
            await self._maybe_flip_for_pier()
            self._persist_calibration()

            self._stop.clear()
            self._active = True
            self._loop_task = asyncio.create_task(self._guide_loop())
            bus.log("info", "native guider calibrated and guiding", "guide")
            bus.publish("guide", **self.stats().__dict__)

    async def stop_guiding(self) -> None:
        self._active = False
        self._stop.set()
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Unblock any dither waiter so a stop mid-dither raises rather than hangs.
        if not self._settle_done.is_set():
            self._settle_error = self._settle_error or "guiding stopped"
            self._settle_done.set()
        bus.publish("guide", **self.stats().__dict__)
        bus.log("info", "native guider stopped", "guide")

    # ------------------------------------------------------------ calibration

    async def _calibrate(self) -> None:
        """Locate the guide star, run the engine's calibration state machine
        (expose → ``process`` → ``pulse_guide``) until it completes, and stamp
        the mount's real scope pointing (OBLIGATION (e)). Raises ``DeviceError``
        on no-star / calibration-failed / timeout."""
        frame = await self._expose()
        stars, _meta = _native.guide_star_find(frame.data)
        if not stars:
            raise DeviceError(
                "native guider: no guide star found — cannot calibrate")
        x0, y0 = float(stars[0]["x"]), float(stars[0]["y"])
        self._engine.begin_calibration(x0, y0)
        # OBLIGATION (e): stamp real declination/pier onto the pending
        # calibration BEFORE it completes (patch_cal_from_scope applies it at
        # COMPLETE). declination/rotator are RADIANS at the PyO3 surface.
        await self._apply_scope_pointing()

        bus.log("info", "native guider calibrating", "guide")
        deadline = time.monotonic() + _CAL_TIMEOUT_S
        while True:
            if time.monotonic() > deadline:
                raise DeviceError("native guider: calibration timed out")
            frame = await self._expose()
            action = self._engine.process(
                frame.data, frame.timestamp, self._exposure_s)
            kind = action["action"]
            if kind == "cal_step":
                await self.tel.pulse_guide(action["dir"], int(action["ms"]))
                continue
            if kind == "lock_lost":
                reason = action.get("reason") or "calibration failed"
                raise DeviceError(
                    f"native guider: calibration failed ({reason})")
            # Idle (or any non-cal action): calibration is complete once a valid
            # Cal is stored — the engine has already transitioned into its
            # guiding phase (continuous star tracking is kept across the
            # boundary). An Idle with no valid Cal is a momentary lost star
            # mid-leg; keep exposing.
            cal = self._engine.dump_calibration()
            if cal and cal.get("is_valid"):
                break
        for msg in (self._engine.calibration_advisories() or []):
            bus.log("warning", f"native guider calibration: {msg}", "guide")
        bus.log("info", "native guider calibration complete", "guide")

    async def _apply_scope_pointing(self) -> None:
        """Discharge OBLIGATION (e): stamp the mount's real declination + pier
        side onto the engine so the completing calibration carries them (and RA
        dec-compensation, dossier §9 item 6, has a real declination). Parity is
        left ``"unknown"`` — the sim mount does not report it and it is not used
        by the guiding math (only by the flip logic/advisories)."""
        dec_deg = 0.0
        pier = "unknown"
        with contextlib.suppress(Exception):
            _ra, dec_deg = await self.tel.get_position()
        with contextlib.suppress(Exception):
            pier = (await self.tel.pier_side()).value
        self._engine.set_scope_pointing(
            math.radians(float(dec_deg)), pier, "unknown", "unknown",
            0.0, self._binning)

    async def _maybe_flip_for_pier(self) -> None:
        """Guiding-start auto-flip host contract (T8; upstream
        mount.cpp:1338-1344). Compares the stored calibration's pier side with
        the mount's current pier side and flips when they differ so guiding
        never runs the mount away from the star."""
        cal = self._engine.dump_calibration()
        if not cal or not cal.get("is_valid"):
            return
        cal_pier = cal.get("pier_side")
        if cal_pier in (None, "unknown"):
            return
        cur = None
        with contextlib.suppress(Exception):
            cur = (await self.tel.pier_side()).value
        if cur in (None, "unknown"):
            return
        if cur != cal_pier:
            flipped = self._engine.flip_calibration(self._flip_requires_dec_flip)
            bus.log("info",
                    f"native guider: mount pier side changed "
                    f"({cal_pier}->{cur}) since calibration; flipped calibration "
                    f"({flipped})", "guide")

    # -------------------------------------------------------------- guide loop

    async def _guide_loop(self) -> None:
        """Per-frame guide loop: expose → ``process`` → dispatch the Action →
        publish stats. Runs until cancelled (stop_guiding/disconnect) or a real
        star loss latches ``_lost``."""
        try:
            while not self._stop.is_set():
                frame = await self._expose()
                action = self._engine.process(
                    frame.data, frame.timestamp, self._exposure_s)
                await self._dispatch(action)
                self._last_stats = self.stats()
                bus.publish("guide", **self._last_stats.__dict__)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            bus.log("error", f"native guide loop stopped on error: {e}", "guide")
            self._active = False
            self._stop.set()
            # Fail any dither() waiter FAST (milestone review M1): the loop that
            # drives the settle window is dead, so without this a dither in
            # flight would hang out its full settle timeout.
            if not self._settle_done.is_set():
                self._settle_error = self._settle_error or f"guide loop error: {e}"
                self._settle_done.set()
            bus.publish("guide", **self.stats().__dict__)

    async def _dispatch(self, action: dict) -> None:
        kind = action["action"]
        reason = action.get("reason")

        # Dither settle-window bookkeeping (only dither opens one in P1). While
        # the window is open every frame returns "settle"; the first non-settle
        # action after it closes wakes the dither() awaiter.
        if kind == "settle":
            self._settle_open = True
            return
        if self._settle_open:
            self._settle_open = False
            if kind == "lock_lost" and reason == "settle_timeout":
                self._settle_error = "settle timed out"
            self._settle_done.set()
            if kind == "lock_lost" and reason == "settle_timeout":
                # The star itself was never lost — guiding resumes next frame.
                return

        if kind in ("pulse", "pulse_pair"):
            self._reacquire = 0
            await self._pulse(action)
        elif kind == "cal_step":
            # Not expected outside calibration, but honor it defensively.
            await self.tel.pulse_guide(action["dir"], int(action["ms"]))
        elif kind == "idle":
            pass  # lock-establishment / recovering frame — no correction
        elif kind == "lock_lost":
            await self._handle_lock_lost(reason)

    async def _pulse(self, action: dict) -> None:
        """Apply a single-axis pulse or a (RA, Dec) pulse pair (dossier §7:
        up to two pulse-guides per accepted frame)."""
        if action["action"] == "pulse":
            await self.tel.pulse_guide(action["dir"], int(action["ms"]))
            return
        ra = action.get("ra")
        dec = action.get("dec")
        if ra:
            await self.tel.pulse_guide(ra["dir"], int(ra["ms"]))
        if dec:
            await self.tel.pulse_guide(dec["dir"], int(dec["ms"]))

    async def _handle_lock_lost(self, reason: str | None) -> None:
        """Map the engine's ``lock_lost`` reasons to the bus/recovery semantics.
        ``star_lost`` gets a bounded reacquire (the engine self-recovers if the
        star returns); exhausting it — or a ``calibration_failed`` here —
        latches inactive so the sequence engine's recovery sees is_active go
        false (the P1-T7 review contract)."""
        if reason == "star_lost":
            self._reacquire += 1
            bus.log("warning",
                    f"native guider lost the guide star "
                    f"(reacquire {self._reacquire}/{_REACQUIRE_BUDGET})", "guide")
            if self._reacquire >= _REACQUIRE_BUDGET:
                bus.log("error",
                        "native guider: guide star not reacquired — stopping",
                        "guide")
                self._lost = True
                self._active = False
                self._stop.set()
            return
        # calibration_failed (unexpected mid-guiding) — fatal.
        bus.log("error",
                f"native guider: guiding stopped ({reason or 'lock lost'})",
                "guide")
        self._lost = True
        self._active = False
        self._stop.set()

    # -------------------------------------------------------------- exposures

    async def _expose(self):
        frame = await self.cam.expose(
            self._exposure_s, self._gain, self._offset, binning=self._binning)
        self._last_frame = frame.data
        return frame

    async def _read_guide_rates(self) -> tuple[float, float] | None:
        with contextlib.suppress(Exception):
            return await self.tel.guide_rates()
        return None

    def _build_engine_config(self, rates: tuple[float, float] | None) -> dict:
        """Assemble the ``GuideEngine`` config: image scale, per-axis algorithms,
        dec guide mode, plus a calibration pulse duration derived from the
        mount's declared guide rate (so a leg crosses ``calibration_distance`` in
        a sane number of pulses). Any of these may be pinned by the caller's
        config; unset engine tunables take the dossier §15 defaults."""
        cfg = self.config
        engine_cfg: dict = {
            "image_scale_arcsec": self._image_scale,
            "ra_algorithm": cfg.get("ra_algorithm", "hysteresis"),
            "dec_algorithm": cfg.get("dec_algorithm", "resist_switch"),
            "dec_guide_mode": cfg.get("dec_guide_mode", "auto"),
        }
        # Pass through any explicitly-pinned engine tunables verbatim.
        for k in ("calibration_distance", "calibration_duration_ms", "max_steps",
                  "assume_orthogonal", "max_ra_duration_ms", "max_dec_duration_ms",
                  "blc_pulse_ms", "search_region", "min_hfd", "max_hfd", "max_adu",
                  "pedestal", "bits_per_pixel"):
            if k in cfg:
                engine_cfg[k] = cfg[k]

        if "calibration_duration_ms" not in engine_cfg and rates:
            ra_deg_s = abs(float(rates[0]))
            scale = self._image_scale if self._image_scale > 0 else 1.0
            px_s = ra_deg_s * 3600.0 / scale
            # Mirror the engine's default_calibration_distance floor so the step
            # count target is consistent with the distance the legs must cross.
            cal_dist = max(25.0, math.ceil(20.0 / scale))
            if px_s > 0:
                ms = cal_dist / px_s / _CAL_TARGET_STEPS * 1000.0
                engine_cfg["calibration_duration_ms"] = int(
                    max(_CAL_MS_MIN, min(_CAL_MS_MAX, ms)))
        return engine_cfg

    # ------------------------------------------------------------------ dither

    async def dither(self, pixels: float = 3.0) -> None:
        """Dither by ``pixels`` and wait for settle (P1: shift the lock + wait;
        the richer dither-during-settle guiding lands in P2). Delegates to the
        engine's settle window, which the guide loop drives."""
        if self._engine is None or not self._active:
            raise DeviceError("native guider: cannot dither when not guiding")
        ang = random.uniform(0.0, 2 * math.pi)
        dx = pixels * math.cos(ang)
        dy = pixels * math.sin(ang)
        self._settle_error = None
        self._settle_done.clear()
        # The loop flips _settle_open on the first "settle" action; do NOT set it
        # here (a pulse frame already in flight must not prematurely wake us).
        self._engine.dither(dx, dy)
        try:
            await asyncio.wait_for(self._settle_done.wait(),
                                   timeout=_SETTLE_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise DeviceError("native guider: dither settle timed out") from None
        if self._settle_error:
            raise DeviceError(
                f"native guider: dither settle failed ({self._settle_error})")
        bus.log("info", f"native guider dithered {pixels:.1f}px and settled",
                "guide")

    # -------------------------------------------------------- meridian flip

    async def flip_calibration(self) -> bool:
        """Flip the stored calibration across a meridian flip (dossier §9 item 4;
        hub.meridian_flip calls this between stopping and restarting guiding).
        Returns False (logged no-op) when there is no valid calibration to flip
        — the flip still completes and guiding re-calibrates on restart."""
        if self._engine is None:
            bus.log("warning", "native guider: no calibration to flip; will rely "
                    "on a fresh calibration after the flip", "guide")
            return False
        cal = self._engine.dump_calibration()
        if not cal or not cal.get("is_valid"):
            bus.log("warning", "native guider: no valid calibration to flip; will "
                    "rely on a fresh calibration after the flip", "guide")
            return False
        ok = self._engine.flip_calibration(self._flip_requires_dec_flip)
        if ok:
            self._persist_calibration()
            bus.log("info", "native guider: flipped calibration for the meridian "
                    "flip", "guide")
        return ok

    # ------------------------------------------------------------------ stats

    def stats(self) -> GuideStats:
        """Map the engine's stats dict onto the shared ``GuideStats`` bus shape.
        ``guiding`` reflects BOTH our host intent (``_active`` and not latched
        lost) AND the engine's own phase, so is_active() goes false on a real
        loss / after stop (the sequence engine's recovery contract).

        UNITS: the engine reports errors in guide-camera PIXELS (its ``recent``
        is ``(t, ra_err_px, dec_err_px)``), but the ``GuideStats`` bus contract
        is ARCSEC (``base.py``'s recent doc; ``phd2.py`` multiplies PHD2's raw
        px by its pixel scale the same way). Convert every error quantity by
        ``self._image_scale`` (arcsec/px) — the SAME scale this guider was
        constructed with (``config["image_scale_arcsec"]``) and handed to the
        engine as ``image_scale_arcsec``; the sim wiring sources it from
        ``rig.guide_scale_arcsec_px``. ``snr`` is unitless and passes through."""
        if self._engine is None:
            return GuideStats(guiding=False)
        try:
            s = self._engine.stats()
        except Exception:  # pragma: no cover - defensive
            return self._last_stats
        scale = self._image_scale if self._image_scale > 0 else 1.0
        recent = [{"t": round(float(t), 3), "ra": round(float(ra) * scale, 3),
                   "dec": round(float(dec) * scale, 3)}
                  for t, ra, dec in s.get("recent", [])]
        guiding = bool(self._active and not self._lost and s.get("guiding"))
        return GuideStats(
            guiding=guiding,
            rms_ra=round(float(s.get("rms_ra", 0.0)) * scale, 2),
            rms_dec=round(float(s.get("rms_dec", 0.0)) * scale, 2),
            rms_total=round(float(s.get("rms_total", 0.0)) * scale, 2),
            snr=round(float(s.get("snr", 0.0)), 1),
            recent=recent[-120:],
        )

    # ------------------------------------------------------------ persistence

    def _persist_calibration(self) -> None:
        """Persist the current calibration to ``CONFIG_DIR/guider/<profile>.json``
        so P2 can offer calibration REUSE. Best-effort: a write failure is a
        logged warning, never fatal to guiding."""
        if not self.profile_id:
            return
        try:
            cal = self._engine.dump_calibration()
            if not cal:
                return
            from ..config import CONFIG_DIR
            d = CONFIG_DIR / "guider"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{self.profile_id}.json").write_text(
                json.dumps(cal, indent=2), encoding="utf-8")
            bus.log("info",
                    f"native guider: saved calibration for profile "
                    f"{self.profile_id}", "guide")
        except Exception as e:  # pragma: no cover - best effort
            bus.log("warning",
                    f"native guider: could not persist calibration: {e}", "guide")

    # ------------------------------------------------------------ guide frame

    async def guide_frame(self) -> bytes | None:
        """A small auto-stretched PNG of the guide-star region for the live UI.
        Crops a tile around the brightest pixel of the most recent guide frame
        and reuses the main display pipeline. While guiding this is the last
        looped exposure; when connected but idle it grabs one frame on demand so
        the preview works before a session starts. Never raises — returns None on
        any failure so the endpoint answers 404 rather than 500."""
        try:
            data = self._last_frame
            loop_running = self._loop_task is not None and not self._loop_task.done()
            if data is None and self.connected and not loop_running:
                # Idle but connected: the guide camera can produce a frame on
                # demand (don't touch the camera while the loop owns it).
                with contextlib.suppress(Exception):
                    f = await self.cam.expose(self._exposure_s, self._gain,
                                              self._offset, binning=self._binning)
                    data = f.data
                    self._last_frame = data
            if data is None:
                return None
            import numpy as np

            from ..imaging.processing import to_png

            a = np.asarray(data)
            if a.ndim != 2 or a.size == 0:
                return None
            y, x = np.unravel_index(int(np.argmax(a)), a.shape)
            r = 48
            y0, y1 = max(0, int(y) - r), min(a.shape[0], int(y) + r)
            x0, x1 = max(0, int(x) - r), min(a.shape[1], int(x) + r)
            tile = a[y0:y1, x0:x1]
            return to_png(tile, stretch=True, max_width=max(int(tile.shape[1]), 192))
        except Exception:
            return None
