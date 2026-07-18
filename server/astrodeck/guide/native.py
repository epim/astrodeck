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

P1 scope: a working converging loop + minimal sim wiring, with calibration
persisted on calibrate and the guiding-start pier-flip host contract
discharged. P2-T1 landed the real ``dither``: a mount-frame lock shift, axis
algorithm reset, fast recenter (dossier §11.2 — the loop dispatches its
pulses exactly like any other correction), and a real settle-dwell wait
(dossier §12), wired end to end via ``stats()["settling"]`` rather than any
single frame's Action shape (see ``_sync_settle_window``). P2-T2 lands the
persistence READ side: ``start_guiding`` now loads any calibration this
profile persisted and reuses it when still compatible (dossier §8.4/§9,
``_cal_reusable``) instead of driving a fresh ~20+ s calibration walk — the
fast path ``_maybe_recover_guiding`` (``sequence/engine.py:1908-1924``)
relies on after a real star loss — plus the guider-level
``flip_calibration`` contract (delegates to the already-P1-verified engine
method) and star-lost recovery hardening (bounded auto-reselect, dossier
§3.3; see ``engine.rs``'s ``ingest_guiding``).
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
# false on a real loss). P2-T2: the engine itself now broadens its search to
# a full-frame auto-reselect once a star is stale (engine.rs's
# ingest_guiding), so every one of these budgeted frames is a genuine
# reacquire attempt, not just a narrow local re-check.
_REACQUIRE_BUDGET = 8

# Cap on how long ``dither`` waits for the engine's settle window to close.
_SETTLE_TIMEOUT_S = 90.0

# The Rust engine's sentinel for "no real declination stamped" (dossier §8.4
# `UNKNOWN_DECLINATION`; astro_guide::calibration::UNKNOWN_DECLINATION).
# Mirrored here — the wheel exposes no Python constant for it — so the P2
# calibration-reuse gate below can recognize a persisted calibration that was
# never really scope-anchored.
_UNKNOWN_DECLINATION = 997.0


def guide_algo_config() -> dict:
    """The persisted per-axis guide-algorithm selection (``AppConfig.guide``)
    as engine-config keys (``ra_algorithm`` / ``dec_algorithm``), for the backend
    guider constructors (P2-T3). Defensive: any failure (no config store, an old
    config without the block) yields ``{}`` so ``_build_engine_config`` falls
    back to its dossier §15 defaults rather than raising during connect."""
    try:
        from ..config import config_store
        g = config_store.cfg().guide
        return {"ra_algorithm": g.ra_algorithm, "dec_algorithm": g.dec_algorithm}
    except Exception:  # pragma: no cover - defensive
        return {}


class NativeGuider(Guider):
    """The native Rust-engine autoguider (spec §3.2/§5).

    Owns the exposure → ``process`` → ``pulse_guide`` → ``bus.publish("guide")``
    loop over a dedicated guide ``Camera`` and a pulse-guide-capable
    ``Telescope``. Implements the full ``Guider`` ABC; publishes the SAME
    ``GuideStats`` shape as ``guide/phd2.py``.
    """

    name = "AstroDeck native"
    #: Native-engine family (see Guider.provider_family): the status badge
    #: reports this guider as ``astrodeck`` (real rig) or ``sim`` (sim rig).
    provider_family = "native"
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
        # and awaits ``_settle_done``; the guide loop's ``_sync_settle_window``
        # sets it when the window (which only ``dither`` opens) closes, tracked
        # via ``stats()["settling"]`` rather than any one frame's Action shape
        # (P2-T1 punch-list #3 — a fast-recenter frame, dossier §11.2, returns
        # an ordinary "pulse_pair" while the window is still open).
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

            # P2-T2 persistence READ side (dossier §8.4/§9): reuse the
            # profile's persisted calibration when it is still trustworthy
            # for THIS session rather than always driving a fresh
            # calibration walk — critical for _maybe_recover_guiding's
            # fast-restart contract after a real star loss
            # (sequence/engine.py:1908-1924), which would otherwise pay a
            # full ~20+ s recalibration on every recovery.
            persisted = self._load_persisted_calibration()
            reused = False
            if persisted is not None and self._cal_reusable(persisted):
                try:
                    # STAR-EXISTENCE PRECONDITION (fix round #2): mirror
                    # _calibrate's one-frame guide_star_find gate. Without
                    # it, a recovery restart during a PERSISTING occlusion
                    # "succeeds" instantly — the engine then sits in
                    # lock-establishment returning Idle forever with
                    # stats().guiding True (the staleness machinery is
                    # unreachable while lock is None), permanently silencing
                    # _maybe_recover_guiding's one-shot retry contract.
                    # Raising here keeps is_active() false so the recovery
                    # loop keeps firing until the star is really back.
                    frame = await self._expose()
                    stars, _meta = _native.guide_star_find(frame.data)
                    if not stars:
                        raise DeviceError(
                            "native guider: no guide star found — cannot "
                            "start guiding")
                    # Strip the image_scale_arcsec SIDECAR key (fix round
                    # #1) before handing the dict to the engine —
                    # dict_to_cal reads required Cal keys only.
                    cal = {k: v for k, v in persisted.items()
                           if k != "image_scale_arcsec"}
                    self._engine.load_calibration(cal)
                    # Live current scope pointing feeds RA dec-compensation
                    # (dossier §9 item 6, never persisted) independently of
                    # the reused Cal's own stored declination/pier — same
                    # call _calibrate() makes internally before completing
                    # a fresh calibration.
                    await self._apply_scope_pointing()
                    self._engine.begin_guiding()
                    reused = True
                    bus.log("info",
                            f"native guider: reusing persisted calibration "
                            f"for profile {self.profile_id}", "guide")
                except DeviceError:
                    # A real refusal (no star) propagates — recalibrating
                    # would fail on the same missing star anyway; the
                    # sequence engine's recovery loop retries later.
                    raise
                except Exception as e:
                    # CORRUPT-PERSISTENCE HARDENING (fix round #3b): a
                    # persisted dict that passes the _cal_reusable gate
                    # fields can still fail the engine's own PyO3 field
                    # conversion (corrupt numerics). Never fatal — fall
                    # back to a fresh calibration.
                    bus.log("warning",
                            f"native guider: could not reuse persisted "
                            f"calibration ({e}); recalibrating", "guide")
            if not reused:
                await self._calibrate()           # blocks; raises on failure
            # HOST CONTRACT (T8 / upstream mount.cpp:1338-1344): auto-flip the
            # calibration at guiding start if the mount's pier side differs from
            # the stored calibration's. A no-op for a fresh calibration (the
            # scope pointing already stamped the current pier); load-bearing
            # for a reused persisted calibration across a pier-side change.
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
        by the guiding math (only by the flip logic/advisories).

        DEC-READ SENTINEL (fix round #4): a failed ``get_position`` stamps the
        engine's ``UNKNOWN_DECLINATION`` sentinel, NOT 0.0 — the engine then
        SKIPS RA dec-compensation entirely (upstream: ``GetDeclinationRadians``
        returns ``UNKNOWN_DECLINATION`` on any pointing failure and dec comp is
        skipped when either declination is unknown, mount.cpp:1380-1381). A
        0.0 default on the REUSE path (where the persisted ``cal.declination``
        is real) would silently boost the RA rate by
        ``cos(cal_dec)/cos(0)`` — 2x at a dec-60° calibration."""
        dec_rad = _UNKNOWN_DECLINATION
        pier = "unknown"
        with contextlib.suppress(Exception):
            _ra, dec_deg = await self.tel.get_position()
            dec_rad = math.radians(float(dec_deg))
        with contextlib.suppress(Exception):
            pier = (await self.tel.pier_side()).value
        self._engine.set_scope_pointing(
            dec_rad, pier, "unknown", "unknown", 0.0, self._binning)

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
                self._sync_settle_window(action)
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

        # Settle-timeout lock_lost (dither only, dossier §12): the star
        # itself was never lost — the guider's settle window blew its
        # deadline. Skip `_handle_lock_lost`'s star-loss/fatal accounting
        # entirely; guiding resumes on the next frame regardless.
        # `_sync_settle_window` (called after this by `_guide_loop`) closes
        # the window and wakes the `dither()` awaiter with this reason.
        if kind == "lock_lost" and reason == "settle_timeout":
            return

        if kind in ("pulse", "pulse_pair"):
            # Applies uniformly whether this pulse is a normal per-axis-
            # algorithm correction or a fast-recenter direct move (dossier
            # §11.2) — the host does not need to tell them apart, it just
            # dispatches the pulse either way.
            self._reacquire = 0
            await self._pulse(action)
        elif kind == "cal_step":
            # Not expected outside calibration, but honor it defensively.
            await self.tel.pulse_guide(action["dir"], int(action["ms"]))
        elif kind == "idle":
            pass  # lock-establishment / recovering frame — no correction
        elif kind == "lock_lost":
            await self._handle_lock_lost(reason)
        # kind == "settle": no host action (dossier §12; the host waits) —
        # falls through with no branch matched.

    def _engine_settling(self) -> bool:
        """Whether the engine's settle window (dither, dossier §12) is
        currently open — the authoritative state, independent of any single
        frame's dispatched Action. A fast-recenter frame (dossier §11.2)
        returns an ordinary ``pulse_pair`` while the window stays open, so
        the Action alone cannot answer this."""
        if self._engine is None:
            return False
        try:
            return bool(self._engine.stats().get("settling", False))
        except Exception:  # pragma: no cover - defensive
            return False

    def _sync_settle_window(self, action: dict) -> None:
        """Wire the ``dither()`` settle-wait handshake (``_settle_open``/
        ``_settle_done``) to the REAL engine settle lifecycle (P2-T1
        punch-list #3) via ``stats()["settling"]``, rather than inferring
        window state from a single frame's Action shape — a fast-recenter
        frame (dossier §11.2) returns an ordinary ``pulse_pair`` while the
        window stays open, which the old ``action == "settle"`` toggle would
        have misread as "settled" on the very first recenter pulse."""
        if self._engine_settling():
            self._settle_open = True
            return
        if not self._settle_open:
            return  # no window was open; nothing to close
        self._settle_open = False
        if (action.get("action") == "lock_lost"
                and action.get("reason") == "settle_timeout"):
            self._settle_error = "settle timed out"
        self._settle_done.set()

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
        # "max_stars" (P3-T1 fix round, review ruling #4): the multi-star
        # tracking knob (dossier §2.6/§4) — absent from this allowlist the
        # engine silently stayed at its single-star default of 1.
        for k in ("calibration_distance", "calibration_duration_ms", "max_steps",
                  "assume_orthogonal", "max_ra_duration_ms", "max_dec_duration_ms",
                  "blc_pulse_ms", "search_region", "min_hfd", "max_hfd", "max_adu",
                  "pedestal", "bits_per_pixel", "max_stars"):
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
        """Dither by ``pixels`` and wait for settle (dossier §11/§12): shifts
        the lock, resets the axis algorithms, and runs a real fast-recenter +
        settle-dwell in the engine (the guide loop dispatches the
        fast-recenter pulses like any other correction). Mirrors
        ``PHD2Guider``'s settle-wait shape (``guide/phd2.py:353-359``): wait
        for the settle handshake or ``_SETTLE_TIMEOUT_S``, whichever comes
        first."""
        if self._engine is None or not self._active:
            raise DeviceError("native guider: cannot dither when not guiding")
        ang = random.uniform(0.0, 2 * math.pi)
        dx = pixels * math.cos(ang)
        dy = pixels * math.sin(ang)
        self._settle_error = None
        self._settle_done.clear()
        # The loop's `_sync_settle_window` flips `_settle_open` from
        # `stats()["settling"]`, not from this call — do NOT set it here (a
        # pulse frame already in flight must not prematurely wake us).
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
            # SIDECAR key (fix round #1): record the image scale this
            # calibration's px/ms rates were measured under, next to (not
            # inside) the engine's Cal fields — stripped again before
            # load_calibration. Upstream clears a calibration outright on a
            # >=1% image-scale change (mount.cpp:1332 ->
            # HandleImageScaleChange -> ClearCalibration, myframe.cpp:2902);
            # _cal_reusable applies the same 1% gate on reuse.
            cal["image_scale_arcsec"] = self._image_scale
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

    def clear_calibration(self) -> bool:
        """Delete this profile's persisted calibration
        (``CONFIG_DIR/guider/<profile>.json``) so the NEXT ``start_guiding``
        drives a fresh calibration walk instead of reusing the stored one
        (dossier §8.4). Best-effort and non-fatal (used by
        ``DELETE /api/guide/calibration``); returns True when a file was
        removed. Does not disturb an in-flight guide loop — a running session
        keeps its live calibration until it is next (re)started."""
        if not self.profile_id:
            return False
        try:
            from ..config import CONFIG_DIR
            p = CONFIG_DIR / "guider" / f"{self.profile_id}.json"
            if p.exists():
                p.unlink()
                bus.log("info",
                        f"native guider: cleared persisted calibration for "
                        f"profile {self.profile_id}", "guide")
                return True
        except Exception as e:  # pragma: no cover - best effort
            bus.log("warning",
                    f"native guider: could not clear calibration: {e}", "guide")
        return False

    def _load_persisted_calibration(self) -> dict | None:
        """Read this profile's persisted calibration
        (``CONFIG_DIR/guider/<profile>.json`` — the file ``_persist_calibration``
        writes), or ``None`` if there is no profile, no file, or the file is
        unreadable/corrupt. Never raises — any failure here just means
        ``start_guiding`` falls back to a fresh calibration."""
        if not self.profile_id:
            return None
        try:
            from ..config import CONFIG_DIR
            p = CONFIG_DIR / "guider" / f"{self.profile_id}.json"
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            # CORRUPT-PERSISTENCE HARDENING (fix round #3a): valid JSON that
            # is not a dict (a list, a string...) must not reach
            # _cal_reusable's ``cal.get()`` and AttributeError out of the
            # reuse decision.
            if not isinstance(data, dict):
                bus.log("warning",
                        f"native guider: persisted calibration for profile "
                        f"{self.profile_id} is not a JSON object; ignoring",
                        "guide")
                return None
            return data
        except Exception as e:  # pragma: no cover - defensive
            bus.log("warning",
                    f"native guider: could not read persisted calibration: "
                    f"{e}", "guide")
            return None

    def _cal_reusable(self, cal: dict) -> bool:
        """P2 reuse-compatibility gate (dossier §8.4 calibration data model +
        §9 items 3/4/6 "calibration adjustments at guide start"): a persisted
        calibration is safe to hand straight to
        ``GuideEngine.load_calibration`` + ``begin_guiding`` only when it is

        1. ``is_valid`` — a partial/failed calibration was never really
           stored as usable in the first place.
        2. recorded at the SAME camera binning as this session — §9 item 3's
           binning rescale (``rate *= old_binning/new_binning``) is not
           implemented here, so reusing a different-binning calibration's
           px/ms rates verbatim would silently misguide.
        3. carries a KNOWN declination (not the ``UNKNOWN_DECLINATION``
           sentinel) — §9 item 6's live RA dec-compensation, and sanity
           check #3 on the next flip, both need a real calibration
           declination to mean anything; a sentinel there means this
           calibration was never really scope-anchored.
        4. carries a KNOWN pier side (not "unknown") — the guiding-start
           auto-flip host contract (§9 item 4, ``_maybe_flip_for_pier``)
           can only detect and correct a pier-side CHANGE since calibration
           when the stored side is actually known; an unknown stored pier
           would silently skip that safety net.
        5. was measured at (within 1% of) THIS session's image scale — the
           ``image_scale_arcsec`` sidecar ``_persist_calibration`` writes
           (fix round #1). Upstream clears a calibration outright on a
           >= 1% image-scale change (mount.cpp:1332 ->
           ``HandleImageScaleChange`` -> ``ClearCalibration``,
           myframe.cpp:2902); a missing sidecar (a pre-fix-round persisted
           file) is treated as not reusable.

        A pier-side MISMATCH (known but different from the mount's current
        side) is deliberately NOT disqualifying here — that is exactly what
        ``_maybe_flip_for_pier`` (called by ``start_guiding`` right after
        this gate, for both the fresh and reused paths) corrects, the same
        way it would for a freshly-measured calibration."""
        if not cal or not cal.get("is_valid"):
            return False
        try:
            if int(cal.get("binning", -1)) != self._binning:
                return False
            dec = cal.get("declination")
            if dec is None or float(dec) == _UNKNOWN_DECLINATION:
                return False
            old_scale = cal.get("image_scale_arcsec")
            if old_scale is None:
                return False
            old_scale = float(old_scale)
            if old_scale <= 0 or self._image_scale <= 0:
                return False
            if abs(1.0 - old_scale / self._image_scale) >= 0.01:
                return False
        except (TypeError, ValueError):
            return False
        if cal.get("pier_side") in (None, "unknown"):
            return False
        return True

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
