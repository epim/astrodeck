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

# A1 (final-branch-review I2): a transient guide-camera exposure fault is
# absorbed by bounded retry+backoff around every guide/cal exposure; a
# persistent one dies loudly through the existing honest-death path. Retries
# after the first attempt, backoff seconds between attempts (one per retry),
# and the consecutive-exhausted-frame budget that trips honest death.
_EXPOSE_RETRIES = 3
_EXPOSE_BACKOFF_S = (0.5, 1.0, 2.0)
_FAULT_FRAME_BUDGET = 5

# Cap on how long ``dither`` waits for the engine's settle window to close.
_SETTLE_TIMEOUT_S = 90.0

# How long the cached guide frame stays good enough to serve the IDLE preview
# (``guide_frame``) before it is re-exposed. Deliberately BELOW the panel's
# 2500 ms poll (GuideFramePreview.tsx) so a poll gets a genuinely new frame
# rather than the same array re-encoded — the whole defect this guards was a
# cache with no age at all. While the guide LOOP runs the cache is served
# regardless of age: the loop owns the sensor and its last frame is the truth.
_IDLE_PREVIEW_TTL_S = 2.0

# Guiding Assistant progress copy (review fix): Phase B issues raw N/S pulses —
# the scope MOVES — so neither message may read as passive "watching".
_PHASE_A_MSG = "Watching a star drift (1 of 2)…"
_PHASE_B_MSG = "Nudging the mount up and down to measure slack (2 of 2)…"

# The Rust engine's sentinel for "no real declination stamped" (dossier §8.4
# `UNKNOWN_DECLINATION`; astro_guide::calibration::UNKNOWN_DECLINATION).
# Mirrored here — the wheel exposes no Python constant for it — so the P2
# calibration-reuse gate below can recognize a persisted calibration that was
# never really scope-anchored.
_UNKNOWN_DECLINATION = 997.0


def guide_algo_config() -> dict:
    """The persisted per-axis guide-algorithm selection (``AppConfig.guide``)
    as engine-config keys (``ra_algorithm`` / ``dec_algorithm`` /
    ``dec_guide_mode`` / ``blc_pulse_ms``), for the backend guider constructors
    (P2-T3; PRO-12 Tier 1 added the latter two). Defensive: any failure (no
    config store, an old config without the block) yields ``{}`` so
    ``_build_engine_config`` falls back to its dossier §15 defaults rather than
    raising during connect."""
    try:
        from ..config import config_store
        g = config_store.cfg().guide
        return {"ra_algorithm": g.ra_algorithm, "dec_algorithm": g.dec_algorithm,
                 "dec_guide_mode": g.dec_guide_mode, "blc_pulse_ms": g.blc_pulse_ms,
                 # PRO-12 Tier 2 (T6): per-axis tunable overrides, exclude_none so
                 # an unset param stays an engine default (never sent as null) —
                 # an all-default GuideConfig emits empty sub-dicts here.
                 "ra_params": g.ra_params.model_dump(exclude_none=True),
                 "dec_params": g.dec_params.model_dump(exclude_none=True)}
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
                 config: dict, profile_id: str | None = None,
                 profile_id_resolver=None,
                 shares_the_imaging_sensor: bool = False) -> None:
        self.cam = guide_camera
        self.tel = telescope
        #: True when ``guide_camera`` IS the rig's imaging camera (the OAG-style
        #: fallback native_backend allows when no guide camera is assigned).
        #: Only the caller that chose the camera can know this, so it is passed
        #: in rather than guessed. Read by ``guide_frame``: the idle preview
        #: must not repeatedly steal a sensor the imaging train is using.
        self.shares_the_imaging_sensor = bool(shares_the_imaging_sensor)
        self.config = dict(config or {})
        # Either a literal id (the sim's "sim", a test's fixed key) or a
        # zero-arg callable read lazily — see the ``profile_id`` property.
        self._profile_id = profile_id
        self._profile_id_resolver = profile_id_resolver

        self._engine = None
        self._loop_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._start_lock = asyncio.Lock()

        # Guiding Assistant (design 2026-07-24): a one-shot measurement session
        # that runs in the EXCLUSIVE mount window (refused while guiding). Its
        # own stop-event (polled between measurement pulses) + the last cached
        # report (read back by GET /api/guide/assistant/report).
        self._assistant_stop = asyncio.Event()
        self._last_assistant_report: dict | None = None
        # MUTUAL EXCLUSION (review fix): the exclusive mount window is now
        # two-directional. ``run_guiding_assistant`` holds ``_start_lock`` for
        # its WHOLE duration (structural — a would-be starter cannot slip
        # between checks) and additionally raises this flag so an initiator that
        # arrives mid-run is REFUSED loudly instead of silently blocking for
        # minutes. Without it a sequence-engine ``start_guiding()`` (which
        # bypasses the HTTP lane entirely) could drive its calibration walk
        # while the assistant is pulsing N/S on the same mount and exposing the
        # same guide camera — garbage calibration rates for the rest of the night.
        self._assistant_active = False

        # Host-side guiding state. ``_active`` is our guiding INTENT (drives
        # stats().guiding together with the engine's own phase); ``_lost`` latches
        # a real, unrecoverable star loss so is_active() goes false (the recovery
        # contract). ``_reacquire`` counts consecutive star_lost frames.
        self._active = False
        self._lost = False
        self._reacquire = 0
        self._fault_frames = 0

        # NOV-7: a small host hint set during the finding/calibrating steps
        # (which precede ``_active`` going True and are otherwise invisible to
        # ``_current_phase()``), and cleared once the guide loop owns the
        # phase. See ``_current_phase()`` for the full composition table.
        self._phase_hint: str | None = None

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
        # ...and WHEN it was exposed (monotonic). ``guide_frame``'s idle branch
        # asks how old the cache is, not whether it exists: nothing ever empties
        # ``_last_frame`` (stop_guiding keeps it on purpose), so an emptiness
        # test froze the preview on the first frame the guider ever took.
        self._last_frame_at = 0.0
        # Single-flight for the idle preview exposure (see _idle_preview_frame).
        self._preview_task: asyncio.Task | None = None
        self._last_stats = GuideStats()

        cfg = self.config
        self._exposure_s = float(cfg.get("exposure_s", _DEFAULT_EXPOSURE_S))
        self._gain = int(cfg.get("gain", _DEFAULT_GAIN))
        self._offset = int(cfg.get("offset", _DEFAULT_OFFSET))
        self._binning = int(cfg.get("binning", 1))
        self._image_scale = float(cfg.get("image_scale_arcsec", 1.0))
        # Whether that scale is a REAL arcsec/px value (guide-scope focal length
        # + pixel size known, or the sim's declared plate scale) vs the 1:1
        # fallback the native backend uses when the guide-scope focal length is
        # unset. Drives GuideStats.is_arcsec so the UI never labels raw pixels as
        # arcsec (UX-15). Callers that know their scale is real set it True.
        self._image_scale_known = bool(cfg.get("image_scale_known", False))
        # Mount-specific meridian-flip constant (PHD2's CalFlipRequiresDecFlip);
        # default False matches the common GEM. Used by BOTH the guiding-start
        # host contract and the meridian-flip ABC method.
        self._flip_requires_dec_flip = bool(cfg.get("flip_requires_dec_flip", False))

    # ------------------------------------------------------------- profile key

    @property
    def profile_id(self) -> str | None:
        """The profile this guider's persisted calibration + PPEC model are
        keyed on, or None for "persist nothing" (every persistence path starts
        with that gate, and a profile-less rig still guides — it just
        recalibrates every start).

        Resolved LAZILY when the constructor was handed a resolver instead of a
        literal, which is what the native backend has to do: a session — and
        with it this guider — is built inside ``connect_profile()``, and
        ``set_active_profile`` runs only AFTER that returns (hub.py:754 then
        :771), so an id read at construction belongs to the profile being
        switched AWAY from.
        Memoized on the first non-None answer: a session lives for exactly one
        connect, so that answer is the profile that opened it, and a mid-session
        switch (which tears the session down anyway) cannot re-key files
        underneath a running guide loop."""
        pid = getattr(self, "_profile_id", None)
        if pid:
            return pid
        resolve = getattr(self, "_profile_id_resolver", None)
        if resolve is None:
            return pid
        # Never raise into a caller: this sits on the guiding-start path and on
        # the clear-calibration route, and a config store that cannot answer is
        # a "no profile", not a failure to guide.
        with contextlib.suppress(Exception):
            resolved = resolve()
            if resolved:
                self._profile_id = resolved
                return resolved
        return None

    @profile_id.setter
    def profile_id(self, value: str | None) -> None:
        # An explicit assignment wins outright — drop the resolver so it cannot
        # come back and overwrite what the caller pinned.
        self._profile_id = value
        self._profile_id_resolver = None

    def _profile_path(self, suffix: str = ".json"):
        """``CONFIG_DIR/guider/<profile><suffix>`` for the CURRENT profile.

        Through ``safe_id_path`` (the same guard ``profiles.py`` resolves its
        own store with), which raises ``KeyError`` for anything that is not a
        single contained filename component on either platform. Profile ids are
        uuid4-derived so nothing hostile can reach here today; this keeps that
        true the day an id becomes user-chosen. Every caller already treats a
        raise as "no persistence", so the refusal degrades the same way a
        missing file does."""
        from ..config import CONFIG_DIR
        from ..persist import safe_id_path
        return safe_id_path(CONFIG_DIR / "guider", str(self.profile_id), suffix)

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
        is active (calibration complete + the engine in its guiding phase).

        Refuses while the Guiding Assistant owns the mount (the other half of
        the exclusive-window contract): the assistant is driving raw N/S pulses
        and exposing the guide camera, so a calibration walk started on top of
        it would measure nonsense rates. Checked BEFORE the lock so the common
        case fails fast and loud (a direct sequence-engine call would otherwise
        simply block behind the assistant's lock for minutes)."""
        if self._assistant_active:
            raise DeviceError(
                "native guider: the Guiding Assistant is using the mount — "
                "stop it before starting guiding")
        async with self._start_lock:
            if self._assistant_active:  # pragma: no cover - lock makes this rare
                raise DeviceError(
                    "native guider: the Guiding Assistant is using the mount — "
                    "stop it before starting guiding")
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
            self._fault_frames = 0
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
                    # NOV-7: one "finding" tick before the star-find so the
                    # client has something to narrate during this otherwise
                    # silent step (D2 — one tick per phase transition).
                    self._phase_hint = "finding"
                    bus.publish("guide", **self.stats().__dict__)
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
                    # A5 (P4-T1 ruling B): restore the persisted PPEC model
                    # window ONLY on the calibration-REUSE path (same profile +
                    # same calibration). A fresh calibration means the geometry
                    # changed, so the trained gear-time model no longer applies.
                    # No-op for a non-PPEC RA algorithm.
                    self._restore_gp_window()
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

            # NOV-7: the guide loop owns the phase from here on (via the
            # engine dict / _active) — clear the hint so a stale
            # "finding"/"calibrating" never outlives the transition it named.
            self._phase_hint = None
            self._stop.clear()
            self._active = True
            self._loop_task = asyncio.create_task(self._guide_loop())
            bus.log("info", "native guider calibrated and guiding", "guide")
            bus.publish("guide", **self.stats().__dict__)

    async def stop_guiding(self) -> None:
        self._active = False
        self._phase_hint = None
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
        self._persist_gp_window()  # A5: save the trained PPEC model on stop
        bus.publish("guide", **self.stats().__dict__)
        bus.log("info", "native guider stopped", "guide")

    # ------------------------------------------------------------ calibration

    async def _calibrate(self) -> None:
        """Locate the guide star, run the engine's calibration state machine
        (expose → ``process`` → ``pulse_guide``) until it completes, and stamp
        the mount's real scope pointing (OBLIGATION (e)). Raises ``DeviceError``
        on no-star / calibration-failed / timeout."""
        # NOV-7: one "finding" tick before the star-find (D2 — one tick per
        # phase transition; the guide loop already publishes per frame once
        # guiding).
        self._phase_hint = "finding"
        bus.publish("guide", **self.stats().__dict__)
        frame = await self._expose()
        stars, _meta = _native.guide_star_find(frame.data)
        if not stars:
            raise DeviceError(
                "native guider: no guide star found — cannot calibrate")
        x0, y0 = float(stars[0]["x"]), float(stars[0]["y"])
        self._engine.begin_calibration(x0, y0)
        # NOV-7: one "calibrating" tick right after the engine enters its
        # calibration state machine.
        self._phase_hint = "calibrating"
        bus.publish("guide", **self.stats().__dict__)
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
                try:
                    frame = await self._expose()
                except DeviceError as e:
                    # A1 (final-branch-review I2): a fully-exhausted exposure is
                    # ONE lost frame. Consecutive exhaustions past the budget mean
                    # a wedged camera — die loudly through the existing honest-death
                    # path (bus error names the device fault; guiding=False;
                    # is_active goes false so the sequence engine's recovery sees
                    # it). The engine's own star-lost _REACQUIRE_BUDGET is unchanged.
                    self._fault_frames += 1
                    if self._fault_frames >= _FAULT_FRAME_BUDGET:
                        bus.log("error",
                                f"native guider: guide camera fault — stopping "
                                f"({e})", "guide")
                        self._lost = True
                        self._active = False
                        self._stop.set()
                        # Refresh the cached snapshot before publishing, like
                        # every other loop exit (the star-lost fatal path falls
                        # through to the loop bottom's `_last_stats = stats()`)
                        # — so stats()'s defensive cached-fallback can never
                        # hand a caller a stale guiding=True from before this
                        # death (a-t2-review Minor).
                        self._last_stats = self.stats()
                        bus.publish("guide", **self._last_stats.__dict__)
                        break
                    bus.log("warning",
                            f"native guider: lost frame to camera fault "
                            f"({self._fault_frames}/{_FAULT_FRAME_BUDGET})", "guide")
                    continue
                self._fault_frames = 0
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

    def _current_phase(self, engine_stats: dict | None = None) -> str:
        """Compose the plain-language narration phase (NOV-7 design doc §1.3)
        from state already tracked host-side — first match wins:

            lost -> settling -> (active & engine guiding) -> _phase_hint ->
            active (loop up, lock not yet established, == "finding") -> idle

        ``engine_stats`` lets ``stats()`` pass its already-fetched engine dict
        so this doesn't re-query the engine on the hot per-frame path; callers
        without one (the finding/calibrating transition ticks) leave it None
        and this fetches its own when it actually needs the ``guiding`` key."""
        if self._lost:
            return "lost"
        if self._engine_settling():
            return "settling"
        if self._active and self._engine is not None:
            s = engine_stats
            if s is None:
                try:
                    s = self._engine.stats()
                except Exception:  # pragma: no cover - defensive
                    s = {}
            if bool(s.get("guiding", False)):
                return "guiding"
        if self._phase_hint:
            return self._phase_hint
        if self._active:
            return "finding"
        return "idle"

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
        """Expose one guide frame, absorbing a transient camera fault (A1;
        final-branch-review I2): retry up to _EXPOSE_RETRIES times with
        _EXPOSE_BACKOFF_S backoff. Exhausted retries raise DeviceError, which
        the guide loop counts as one lost frame and the calibration path
        surfaces as a clean calibration abort.

        ANY ``Exception`` from the camera is retried, not just ``DeviceError``
        (#28). The promise here is about transient FAULTS, and the adapters
        under us leak the whole transport family — ``httpx.HTTPError`` from an
        Alpaca camera, ``OSError`` from a serial/USB read, ``struct.error`` from
        a short packet (alpaca.py catches that same triple in ten places) — plus
        whatever an out-of-tree driver raises. Every one of those used to walk
        straight past this envelope and out through ``_guide_loop``'s defensive
        handler, which stops guiding WITHOUT latching ``_lost``, so the sequence
        engine's recovery was never told anything was wrong.
        ``asyncio.CancelledError`` is a ``BaseException``, so the "a stop
        mid-exposure is NOT retried" contract survives the widening untouched.
        """
        last_err: Exception | None = None
        for attempt in range(_EXPOSE_RETRIES + 1):
            try:
                frame = await self.cam.expose(
                    self._exposure_s, self._gain, self._offset,
                    binning=self._binning)
                # ``Camera.expose`` is ANNOTATED ``-> CameraFrame`` and nothing
                # enforces it; a driver that returns None on failure made the
                # next line an AttributeError — unretried and unhandled. Make it
                # a retryable fault by construction rather than by trusting an
                # inventory of today's adapters (the hub guards its own preview
                # path the same way).
                if frame is None:
                    raise DeviceError(f"{self.cam.name} returned no frame")
                self._last_frame = frame.data
                self._last_frame_at = time.monotonic()
                return frame
            except Exception as e:
                last_err = e
                if attempt < _EXPOSE_RETRIES:
                    bus.log("warning",
                            f"native guider: guide exposure failed ({e}); "
                            f"retry {attempt + 1}/{_EXPOSE_RETRIES}", "guide")
                    await asyncio.sleep(_EXPOSE_BACKOFF_S[attempt])
        raise DeviceError(
            f"native guider: guide exposure failed after {_EXPOSE_RETRIES} "
            f"retries: {last_err}")

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
                  "pedestal", "bits_per_pixel", "max_stars",
                  # PRO-12 Tier 2 (T6): per-axis algorithm-tunable sub-dicts.
                  # Forwarding these to the CURRENT wheel is harmless — its
                  # PyO3 parser ignores keys it doesn't know — until the wheel
                  # is rebuilt against the T5 engine change.
                  "ra_params", "dec_params"):
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

    # ------------------------------------------------------- guiding assistant

    async def run_guiding_assistant(self, opts: dict | None = None,
                                    on_progress=None) -> dict:
        """Guiding Assistant one-shot (design 2026-07-24 §3.2): measure drift /
        periodic error / seeing (Phase A, mount idle) then Dec backlash (Phase B,
        raw N/S pulses), reduce, ``recommend`` guide params, cache + return the
        report. Drives ``self._expose`` + ``_native.guide_star_find`` +
        ``self.tel.pulse_guide`` directly — NO engine calibration state machine,
        NO correction algorithms (the same raw-pulse regime PHD2's GA uses).

        Runs ONLY in the exclusive mount window, and that window is now
        MUTUAL: it refuses while guiding (``_active``), while the mount is parked
        or slewing (MANDATORY SAFETY GUARD 2), AND it holds ``self._start_lock``
        plus ``self._assistant_active`` for its whole duration so a guide start
        arriving from any lane (HTTP or a direct sequence-engine call) is
        refused rather than calibrating on top of the assistant's raw pulses.
        Each measurement pulse is capped at ``MAX_PULSE_MS`` (~1 s) and the
        Phase-B walk halts cleanly on the ``OutOfRoom`` edge guard (SAFETY GUARD
        1). Cancellable via ``self._assistant_stop`` (POST
        /api/guide/assistant/stop), polled between pulses.

        Every failure path (refusal, cancel, star loss, device error) publishes a
        TERMINAL ``{phase: "error", message}`` progress tick before re-raising —
        the panel is spawned as a background task, so without it a failed run
        leaves the progress bar running forever with no message."""
        def _progress(phase: str, pct: float, message: str) -> None:
            payload = {"phase": phase, "pct": round(float(pct), 1),
                       "message": message}
            bus.publish("guide_assistant", **payload)
            if on_progress is not None:
                with contextlib.suppress(Exception):
                    on_progress(payload)

        try:
            # Fast, lock-free refusal for the obvious "already guiding" case so
            # the caller isn't parked on _start_lock behind a live guide start.
            if self._active or (self._loop_task is not None
                                and not self._loop_task.done()):
                raise DeviceError("native guider: stop guiding before running "
                                  "the Guiding Assistant")
            # STRUCTURAL EXCLUSION: hold the same lock ``start_guiding`` takes
            # for the ENTIRE run — there is no window between check and use.
            async with self._start_lock:
                if self._active or (self._loop_task is not None
                                    and not self._loop_task.done()):
                    raise DeviceError(
                        "native guider: stop guiding before running the "
                        "Guiding Assistant")
                self._assistant_active = True
                try:
                    return await self._run_guiding_assistant_locked(
                        opts, _progress)
                finally:
                    self._assistant_active = False
        except asyncio.CancelledError:
            _progress("error", 100.0, "Guiding Assistant cancelled.")
            raise
        except Exception as e:
            _progress("error", 100.0, str(e))
            raise

    async def _run_guiding_assistant_locked(self, opts: dict | None,
                                            _progress) -> dict:
        """The assistant body, run with ``_start_lock`` held and
        ``_assistant_active`` raised (see ``run_guiding_assistant``)."""
        from . import assistant as ga

        # SAFETY: exclusive mount window only.
        try:
            parked = await self.tel.is_parked()
        except Exception:  # pragma: no cover - defensive
            parked = False
        if parked:
            raise DeviceError(
                "native guider: unpark the mount before running the Guiding "
                "Assistant")
        try:
            slewing = await self.tel.is_slewing()
        except Exception:  # pragma: no cover - defensive
            slewing = False
        if slewing:
            raise DeviceError(
                "native guider: wait for the slew to finish before running the "
                "Guiding Assistant")

        opts = dict(opts or {})
        include_backlash = bool(opts.get("include_backlash", True))
        try:
            duration_s = float(opts.get("duration_s") or 75.0)
        except (TypeError, ValueError):
            duration_s = 75.0
        duration_s = max(20.0, min(240.0, duration_s))

        self._assistant_stop.clear()
        scale = self._image_scale if self._image_scale > 0 else 1.0
        known = self._image_scale_known

        bus.log("info", "native guider: Guiding Assistant started", "guide")
        _progress("phase_a", 2.0, _PHASE_A_MSG)

        # ---- Phase A: uncalibrated drift / periodic error / seeing ----
        # One centroid per exposure: in production each exposure takes
        # ``exposure_s`` so the target-sample count IS the ~1-2 min watch window;
        # under a faked dwell (tests) it just runs fast. Wall timestamps still
        # drive the drift slope, so the reduction is cadence-independent.
        cadence = self._exposure_s if self._exposure_s > 0 else 0.5
        target_samples = int(max(20, min(3000, round(duration_s / cadence))))
        samples: list[tuple[float, float, float]] = []
        t0: float | None = None
        no_star_streak = 0
        while len(samples) < target_samples:
            if self._assistant_stop.is_set():
                raise DeviceError("native guider: Guiding Assistant cancelled")
            frame = await self._expose()
            stars, _meta = _native.guide_star_find(frame.data)
            if not stars:
                no_star_streak += 1
                if no_star_streak >= _REACQUIRE_BUDGET:
                    raise DeviceError(
                        "native guider: Guiding Assistant found no guide star")
                continue
            no_star_streak = 0
            ts = float(frame.timestamp)
            if t0 is None:
                t0 = ts
            samples.append((ts - t0, float(stars[0]["x"]), float(stars[0]["y"])))
            _progress("phase_a",
                      2.0 + 55.0 * min(1.0, len(samples) / target_samples),
                      _PHASE_A_MSG)

        phase_a = ga.reduce_phaseA(samples, scale, known)

        # ---- Phase B: Dec backlash (raw N/S pulses) ----
        backlash = ga.BacklashResult()
        if include_backlash and self._last_frame is not None:
            _progress("phase_b", 60.0, _PHASE_B_MSG)
            data = self._last_frame
            frame_h, frame_w = int(data.shape[0]), int(data.shape[1])
            margin = float(self.config.get("search_region", 15))

            # D2: prefer the real calibration yRate when one is loaded, else the
            # declared guide rate × image scale. Surface which was used.
            rates = await self._read_guide_rates()
            y_rate, y_src = self._assistant_y_rate(scale, rates)
            run = ga.BacklashRun(
                y_rate, phase_a.drift_per_min_px / 60.0,
                frame_w=frame_w, frame_h=frame_h, margin=margin,
                y_rate_source=y_src)

            guard = 0
            max_iters = 2 * ga.MAX_MEASUREMENT_STEPS + ga.MAX_CLEARING_STEPS + 10
            while guard < max_iters:
                guard += 1
                if self._assistant_stop.is_set():
                    raise DeviceError(
                        "native guider: Guiding Assistant cancelled")
                # Keep the bar honest during the multi-minute walk (it used to
                # sit at 60% for the whole of Phase B).
                _progress("phase_b", 60.0 + 35.0 * (guard / max_iters),
                          _PHASE_B_MSG)
                frame = await self._expose()
                stars, _meta = _native.guide_star_find(frame.data)
                if not stars:
                    # A dropped star mid-walk: treat as out-of-data, stop the
                    # walk and estimate from what we have.
                    break
                x, y = float(stars[0]["x"]), float(stars[0]["y"])
                cmd = run.step(x, y, float(frame.timestamp))
                if cmd.kind == "done":
                    break
                # SAFETY GUARD 1 (belt-and-suspenders): never dispatch a pulse
                # that would walk a near-edge star further off-sensor.
                if run._out_of_room(x, y):
                    break
                # SAFETY GUARD 2: cap every raw pulse.
                ms = int(min(ga.MAX_PULSE_MS, max(0, cmd.ms or 0)))
                if ms > 0:
                    await self.tel.pulse_guide(cmd.direction, ms)
            backlash = run.compute()

        _progress("reducing", 96.0, "Crunching the numbers…")

        current = guide_algo_config()
        recs = ga.recommend(phase_a, backlash, current)
        report = ga.report_dict(phase_a, backlash, recs, current, samples)
        self._last_assistant_report = report
        _progress("done", 100.0, "Recommended settings are ready.")
        bus.log("info",
                f"native guider: Guiding Assistant complete "
                f"({len(recs)} recommendations, backlash "
                f"{backlash.result_code})", "guide")
        return report

    def _assistant_y_rate(self, scale: float,
                          rates: tuple[float, float] | None) -> tuple[float, str]:
        """Dec rate (px/ms) for the backlash phase (open decision D2). Prefers a
        loaded, valid calibration's ``y_rate``; otherwise derives it from the
        mount's DECLARED guide rate × image scale. A wrong declared rate only
        mis-scales the seed, which the user reviews before applying."""
        if self._engine is not None:
            try:
                cal = self._engine.dump_calibration()
                if cal and cal.get("is_valid"):
                    yr = float(cal.get("y_rate", 0.0))
                    if yr > 0:
                        return yr, "calibration"
            except Exception:  # pragma: no cover - defensive
                pass
        if rates:
            dec_deg_s = abs(float(rates[1]))
            if dec_deg_s > 0 and scale > 0:
                return dec_deg_s * 3600.0 / scale / 1000.0, "declared"
        return 0.0, "declared"

    def run_assistant_report(self) -> dict | None:
        """The last cached Guiding Assistant report (GET /api/guide/assistant/
        report), or None when the assistant has not run this session."""
        return self._last_assistant_report

    def stop_guiding_assistant(self) -> None:
        """Cancel an in-flight Guiding Assistant run (POST /api/guide/assistant/
        stop). Sets the stop event the measurement loop polls between pulses."""
        self._assistant_stop.set()

    # ------------------------------------------------------------------ dither

    async def dither(self, pixels: float = 3.0, settle=None) -> None:
        """Dither by ``pixels`` and wait for settle (dossier §11/§12): shifts
        the lock, resets the axis algorithms, and runs a real fast-recenter +
        settle-dwell in the engine (the guide loop dispatches the
        fast-recenter pulses like any other correction). Mirrors
        ``PHD2Guider``'s settle-wait shape (``guide/phd2.py:353-359``): wait
        for the settle handshake or the timeout, whichever comes first.

        UX-24: the engine self-manages the settle pixels/time criteria, so a
        caller-supplied ``settle`` only overrides the WAIT timeout here (the one
        knob honored Python-side); pixels/time are ignored for the native path."""
        # Exclusive-window contract (review fix): never dispatch a dither offset
        # while the Guiding Assistant owns the mount. Unreachable in practice
        # (the assistant refuses to start while guiding, and dither needs an
        # active guide loop) — kept as the explicit third guard alongside
        # start_guiding's, so a future caller cannot re-open the hole.
        if self._assistant_active:
            raise DeviceError(
                "native guider: the Guiding Assistant is using the mount — "
                "cannot dither")
        if self._engine is None or not self._active:
            raise DeviceError("native guider: cannot dither when not guiding")
        timeout_s = _SETTLE_TIMEOUT_S
        if settle and settle.get("timeout"):
            try:
                timeout_s = max(1.0, float(settle["timeout"]))
            except (TypeError, ValueError):
                timeout_s = _SETTLE_TIMEOUT_S
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
                                   timeout=timeout_s)
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
        ``rig.guide_scale_arcsec_px``. ``snr`` is unitless and passes through.

        ``phase`` (NOV-7): the plain-language narration phase composed by
        ``_current_phase()`` (design doc §1.3) — additive to the wire shape,
        flows through ``hub.py``'s ``stats().__dict__`` poll and the
        ``bus.publish("guide", **stats().__dict__)`` calls for free."""
        if self._engine is None:
            return GuideStats(guiding=False, phase=self._current_phase())
        try:
            s = self._engine.stats()
        except Exception:  # pragma: no cover - defensive
            return self._last_stats
        scale = self._image_scale if self._image_scale > 0 else 1.0
        # is_arcsec only when we have a genuine scale to convert BY; otherwise
        # scale==1.0 leaves the engine's raw pixels unchanged and calling them
        # arcsec would mislead (UX-15).
        arcsec = self._image_scale_known and self._image_scale > 0
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
            is_arcsec=arcsec,
            image_scale=round(self._image_scale, 3) if arcsec else 0.0,
            phase=self._current_phase(s),
        )

    def calibration_report(self) -> dict | None:
        """Surface the engine's calibration geometry + advisories (UX-23) so a
        bad/flipped calibration is visible BEFORE it runs the mount away from the
        star. ``y_angle_error`` is the orthogonality deviation (radians →
        degrees); ``declination`` is radians (997.0 = unknown sentinel). Rates
        (px/ms) and raw axis angles are intentionally omitted rather than
        mislabeled. None when there is no engine / calibration."""
        if self._engine is None:
            return None
        try:
            cal = self._engine.dump_calibration()
        except Exception:  # pragma: no cover - defensive
            return None
        if not cal:
            return None
        try:
            advisories = [str(m) for m in (self._engine.calibration_advisories() or [])]
        except Exception:  # pragma: no cover - defensive
            advisories = []
        dec_rad = float(cal.get("declination", _UNKNOWN_DECLINATION))
        dec_deg = math.degrees(dec_rad)
        return {
            "is_valid": bool(cal.get("is_valid")),
            "ortho_error_deg": round(abs(math.degrees(float(cal.get("y_angle_error", 0.0)))), 2),
            "declination_deg": round(dec_deg, 1) if abs(dec_deg) <= 90.5 else None,
            "pier_side": cal.get("pier_side"),
            "binning": int(cal.get("binning", 1)),
            "advisories": advisories,
            "source": "native",
        }

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
            # Resolve BEFORE mkdir so a refused id creates nothing at all.
            p = self._profile_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cal, indent=2), encoding="utf-8")
            bus.log("info",
                    f"native guider: saved calibration for profile "
                    f"{self.profile_id}", "guide")
        except Exception as e:  # pragma: no cover - best effort
            bus.log("warning",
                    f"native guider: could not persist calibration: {e}", "guide")

    def clear_calibration(self) -> bool:
        """Delete this profile's persisted calibration AND its persisted PPEC
        model (``<profile>.json`` + ``<profile>-gp.json``) so the NEXT
        ``start_guiding`` drives a fresh calibration walk and a fresh model
        (dossier §8.4/§6.8.6). Best-effort and non-fatal (used by
        ``DELETE /api/guide/calibration``); returns True when a file was
        removed. Does not disturb an in-flight guide loop."""
        if not self.profile_id:
            return False
        removed = False
        try:
            for p in (self._profile_path(), self._profile_path("-gp.json")):
                if p.exists():
                    p.unlink()
                    removed = True
            if removed:
                bus.log("info",
                        f"native guider: cleared persisted calibration + PPEC "
                        f"model for profile {self.profile_id}", "guide")
        except Exception as e:  # pragma: no cover - best effort
            bus.log("warning",
                    f"native guider: could not clear calibration: {e}", "guide")
        return removed

    def _load_persisted_calibration(self) -> dict | None:
        """Read this profile's persisted calibration
        (``CONFIG_DIR/guider/<profile>.json`` — the file ``_persist_calibration``
        writes), or ``None`` if there is no profile, no file, or the file is
        unreadable/corrupt. Never raises — any failure here just means
        ``start_guiding`` falls back to a fresh calibration."""
        if not self.profile_id:
            return None
        try:
            p = self._profile_path()
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

    def _persist_gp_window(self) -> None:
        """Persist the trained PPEC gear-time model to
        ``CONFIG_DIR/guider/<profile>-gp.json`` on guiding stop (A5, dossier
        §6.8.6) as ``{"dumped_at": <wall epoch s>, "window": [[t, m, v, c],
        ...]}`` — ``dumped_at`` feeds the restore-side retain-or-reset
        downtime gate (amended spec §3-A5). Best-effort; nothing to save for a
        non-PPEC RA algorithm or an untrained model (the dump — completed
        measurements only, no pending row — is empty or a single point)."""
        if not self.profile_id or self._engine is None:
            return
        try:
            window = self._engine.dump_gp_window()
            if not window or len(window) < 2:
                return
            p = self._profile_path("-gp.json")     # resolve before mkdir
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"dumped_at": time.time(), "window": window}),
                         encoding="utf-8")
            bus.log("info",
                    f"native guider: saved PPEC model for profile "
                    f"{self.profile_id}", "guide")
        except Exception as e:  # pragma: no cover - best effort
            bus.log("warning",
                    f"native guider: could not persist PPEC model: {e}", "guide")

    def _load_gp_window(self) -> tuple[float, list] | None:
        """Read this profile's persisted GP window
        (``CONFIG_DIR/guider/<profile>-gp.json``) as ``(dumped_at, points)``
        where ``points`` is a list of ``(t, measurement, variance, control)``
        tuples, or None when absent / corrupt. Never raises — a corrupt file
        logs and yields None (fresh model), mirroring the
        calibration-persistence hardening."""
        if not self.profile_id:
            return None
        try:
            p = self._profile_path("-gp.json")
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(
                    data.get("window"), list):
                bus.log("warning",
                        f"native guider: persisted GP window for profile "
                        f"{self.profile_id} has an unexpected shape; ignoring",
                        "guide")
                return None
            dumped_at = float(data["dumped_at"])
            points = [(float(t), float(m), float(v), float(c))
                      for t, m, v, c in data["window"]]
            return dumped_at, points
        except Exception as e:  # pragma: no cover - defensive
            bus.log("warning",
                    f"native guider: could not read persisted GP window "
                    f"({e}); starting fresh", "guide")
            return None

    def _restore_gp_window(self) -> None:
        """Restore the persisted PPEC model into the live engine on the
        calibration-reuse path (A5). The engine applies the upstream
        retain-or-reset gate (amended spec §3-A5; ``GuidingStarted``,
        dossier §6.8.6): the ENTIRE window is restored — re-phased by the
        downtime since the dump — only when that downtime is within
        ``GpParams::retain_max_pct_period`` (40%) of one period (the threshold
        lives in the Rust engine; this side passes only the downtime).
        Otherwise the model starts fresh (logged). No-op for a non-PPEC RA
        algorithm or when there is no persisted window."""
        if not self.profile_id or self._engine is None:
            return
        loaded = self._load_gp_window()
        if not loaded:
            return
        dumped_at, points = loaded
        downtime_s = time.time() - dumped_at
        try:
            if self._engine.restore_gp_window(points, downtime_s):
                bus.log("info",
                        f"native guider: restored PPEC model for profile "
                        f"{self.profile_id} (downtime {downtime_s:.0f}s)",
                        "guide")
            else:
                bus.log("info",
                        f"native guider: PPEC model for profile "
                        f"{self.profile_id} not restored (downtime "
                        f"{downtime_s:.0f}s outside the retention window); "
                        f"starting fresh", "guide")
        except Exception as e:  # pragma: no cover - defensive
            bus.log("warning",
                    f"native guider: could not restore PPEC model ({e}); "
                    f"starting fresh", "guide")

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
        looped exposure; when connected but idle it grabs a frame on demand —
        again once the cached one is older than ``_IDLE_PREVIEW_TTL_S``, so the
        panel keeps getting new pictures instead of the first one forever. Never
        raises — returns None on any failure so the endpoint answers 404 rather
        than 500."""
        try:
            data = self._last_frame
            loop_running = self._loop_task is not None and not self._loop_task.done()
            # AGE, not emptiness: nothing empties the cache, so `data is None`
            # alone stopped asking the camera after the very first frame.
            stale = (data is None
                     or (time.monotonic() - self._last_frame_at) > _IDLE_PREVIEW_TTL_S)
            if self.shares_the_imaging_sensor:
                # OAG rig: our "guide camera" IS the imaging camera. Re-exposing
                # it on AGE would take the sensor out from under the imaging
                # train, mid-sequence, every couple of seconds, for a panel
                # nobody is guiding with. Emptiness still grabs, so this rig
                # keeps exactly the one-grab-per-panel-open behaviour it had
                # before the age gate existed — the fix above is for rigs with a
                # camera of their own to expose.
                #
                # The hub refuses this case on the guide-CAMERA path
                # (_guide_preview_source), but reaches that test only when no
                # guider answered, so a native guider walks straight past it.
                # Deliberately NOT re-deriving the hub's source order here: two
                # copies of that order silently diverged once already
                # (test_guide_preview_source.py). This asks the only question
                # this object can answer about itself.
                stale = data is None
            if stale and self.connected and not loop_running:
                # Idle but connected: the guide camera can produce a frame on
                # demand (don't touch the camera while the loop owns it). A
                # failed grab leaves ``data`` on the previous frame — old-but-real
                # beats blanking a panel that had a picture a moment ago.
                with contextlib.suppress(Exception):
                    fresh = await self._idle_preview_frame()
                    if fresh is not None:
                        data = fresh
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

    async def _idle_preview_frame(self):
        """ONE on-demand preview exposure, SHARED by every poller that asks
        while it is in flight.

        The panel starts a new request every 2.5 s whether or not the previous
        one landed (GuideFramePreview.tsx), and a guide exposure plus USB
        readout plus a PNG encode can outlast that — so once the idle branch
        fires on age rather than on emptiness, two overlapping pollers would
        both reach ``expose()`` and race two readouts over one buffer. The
        hub's single-flight (``Hub.guide_preview_png``) guards only its own
        guide-CAMERA branch and never reaches this one.

        Shielded: a client that drops the ``<img>`` load cancels its own WAIT,
        never the exposure — a camera killed mid-readout stays wedged for the
        run that follows. A COMPLETED task is replaced rather than re-awaited,
        so sequential polls each reach the sensor (that is the whole point)."""
        task = self._preview_task
        if task is None or task.done():
            task = asyncio.ensure_future(self._expose_preview_frame())
            self._preview_task = task
        return await asyncio.shield(task)

    async def _expose_preview_frame(self):
        """The preview exposure itself, in its own coroutine so overlapping
        callers can share one in-flight frame. Stamps the cache so the next
        poll's age test is measured from THIS frame."""
        frame = await self.cam.expose(self._exposure_s, self._gain,
                                      self._offset, binning=self._binning)
        data = frame.data
        self._last_frame = data
        self._last_frame_at = time.monotonic()
        return data
