"""Equipment hub: owns connected devices and rig-level operations.

The hub is the single place that knows which physical device fills each role
(imaging camera, mount, focuser, wheel, power, guider). Everything above it
(API, sequencer) works in terms of roles.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .config import config_store, fov_deg, image_scale_arcsec_px, redacted
from .devices.base import (
    Camera,
    DeviceError,
    FilterWheel,
    Focuser,
    PierSide,
    SafetyMonitor,
    SafetyReading,
    Switch,
    Telescope,
)
from .devices.backend import ROLES
from .devices.nina import build_nina_rig, pick as nina_pick
from .events import bus
from .guide import Guider, PHD2Guider
from .imaging import (
    auto_levels,
    compute_histogram,
    display_histogram,
    measure_frame,
    save_fits,
    stretch_with,
    to_jpeg,
    to_png,
    to_thumb,
)
from .imaging.processing import frame_stats
from .polar import PolarAlignSession
from .profiles import Profile, ProfileDevice, profiles

if TYPE_CHECKING:  # annotations only -- the harness is imported lazily at runtime
    from .devices.backend import ConnSpec, RigSpec
    from .devices.orchestrator import ConnectResult

#: The device roles a rig fills. Imported from ``devices.backend`` (the single
#: source of truth, a 7-tuple INCLUDING ``guider``) so hub and backend can never
#: drift. ``guider`` is resolved via the guider-role session, NOT a get_device
#: device-loop placement, so the role loops below skip it (it never appears in
#: ``result.rig``).
DEVICE_ROLES = ROLES


def _harness():
    """Lazily import the pluggable-backend harness (Stage A).

    Returns ``(RigSpec, ConnSpec, connect_profile)``. Imported lazily inside the
    connect methods (not at module top) so the device backends register their
    adapters without risking an import cycle through ``hub`` -- importing
    ``devices.backends`` self-registers ``sim``/``nina``/``native``/``phd2`` in the
    registry, and ``devices.backend``/``orchestrator`` import nothing from here."""
    from .devices import backends as _backends  # noqa: F401  (registration side effect)
    from .devices.backend import ConnSpec, RigSpec
    from .devices.orchestrator import connect_profile
    return RigSpec, ConnSpec, connect_profile

#: SafetyMonitor poll cadence and per-read timeout (Batch 4b). The poller runs on
#: its OWN task (NOT the 2s status loop) so a slow/hung sensor never blocks status;
#: a read that exceeds SAFETY_READ_TIMEOUT_S is cached as a STALE reading, which
#: the engine fail-closes to UNSAFE (devices/base.SafetyReading.stale, C1-12/C1-15).
SAFETY_POLL_INTERVAL_S = 5.0
SAFETY_READ_TIMEOUT_S = 8.0
#: Age guard: if the cached reading is older than one poll cycle plus a read
#: timeout plus this slack, the poller has stopped ticking — treat the cached
#: reading as STALE (fail-closed to UNSAFE) instead of trusting it forever. This
#: closes the fail-OPEN seam where a dead poller keeps returning the last SAFE
#: reading all night (C1-12/C1-15).
SAFETY_STALE_SLACK_S = 5.0

_CAPTURE_ENV = (os.environ.get("ASTRODECK_CAPTURE_DIR") or "").strip()
CAPTURE_DIR = Path(_CAPTURE_ENV) if _CAPTURE_ENV else (Path(__file__).resolve().parents[2] / "captures")

#: Touch-safety motion constants (master plan §A.7 / §C-Risk-5). The manual-move
#: rate cap (the server clamp in ``/api/mount/move`` imports this) and the
#: move-axis deadman window. Defined ONCE here so the touch surface and the
#: Batch-4 safety surface share one source of truth — both reuse the single
#: ``_move_watchdog`` task below; neither re-creates these values.
TOUCH_MAX_RATE_DEG_S = 0.6
MOVE_DEADMAN_MS = 1200

#: how many full display frames the ring keeps (memory cap on the Pi), how many
#: tiny thumbnails it keeps for the filmstrip, and how many linear arrays it
#: retains for /crop and /render (a 6200 frame is ~125 MB, so only the latest
#: 1–2 — live-preview spec finding #18 / §6).
PREVIEW_DISPLAY_KEEP = 8
PREVIEW_THUMB_KEEP = 50
PREVIEW_LINEAR_KEEP = 2


def precess_j2000_to_jnow(ra_hours: float, dec_deg: float,
                          when: float | None = None) -> tuple[float, float]:
    """Precess an ICRS/J2000 (RA hours, Dec deg) to topocentric-apparent JNOW.

    The whole app above the device layer works in J2000 (the curated catalog and
    ASTAP both return J2000/ICRS), but real ASCOM/Alpaca mounts are almost
    universally topocentric (JNOW): in mid-2026 the J2000->JNOW offset is ~20
    arcmin and grows ~50 arcsec/yr, so feeding raw J2000 to a JNOW mount lands a
    long-FL rig outside the frame and corrupts its sync/alignment model. We use
    astropy's IAU-2006 precession/nutation (ICRS -> TETE, "true equator true
    equinox of date" = apparent place) at the observation time.

    Imported lazily: astropy is a real dependency but pulling it into the hub's
    import graph at module load slows an unrelated import path."""
    from astropy.coordinates import ICRS, TETE
    from astropy.time import Time
    import astropy.units as u

    t = Time(when if when is not None else time.time(), format="unix")
    c = ICRS(ra=ra_hours * 15.0 * u.deg, dec=dec_deg * u.deg)
    apparent = c.transform_to(TETE(obstime=t))
    return apparent.ra.hourangle % 24.0, float(apparent.dec.deg)


def precess_jnow_to_j2000(ra_hours: float, dec_deg: float,
                          when: float | None = None) -> tuple[float, float]:
    """Inverse of :func:`precess_j2000_to_jnow`: topocentric-apparent JNOW back to
    ICRS/J2000, so a JNOW mount's reported position can be reasoned about in the
    J2000 frame everything else uses."""
    from astropy.coordinates import ICRS, TETE
    from astropy.time import Time
    import astropy.units as u

    t = Time(when if when is not None else time.time(), format="unix")
    c = TETE(ra=ra_hours * 15.0 * u.deg, dec=dec_deg * u.deg, obstime=t)
    icrs = c.transform_to(ICRS())
    return icrs.ra.hourangle % 24.0, float(icrs.dec.deg)


@dataclass
class PreviewEntry:
    """One ring slot. Replaces the old ``tuple[bytes, str]`` — carries the bytes
    each preview route serves plus the published meta dict (the API reads this).
    ``lossless``/``linear`` are held only for the latest 1–2 frames."""

    display: bytes
    mime: str
    thumb: bytes
    lossless: bytes | None = None       # only latest 1–2 (paused/zoom PNG)
    linear: np.ndarray | None = None    # only latest 1–2 (for /crop, /render Pass 2)
    meta: dict = field(default_factory=dict)


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
        self.previews: dict[int, PreviewEntry] = {}   # id -> ring slot
        self.preview_thumbs: dict[int, bytes] = {}    # id -> thumb (kept longer)
        self.last_frame = None                  # most recent CameraFrame
        # the sequence engine registers itself so poll_status can report the
        # active plan's meridian_flip setting without importing the engine.
        self.engine = None
        # last meridian dict from poll_status, so the engine's (sync) ETA can
        # window-gate the flip cost without device I/O.
        self.last_meridian: dict | None = None
        self._loop_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        # --- safety monitor (Batch 4b) -----------------------------------------
        # own-cadence poller task + the latest CACHED SafetyReading. safety_reading()
        # always returns this cache (NEVER an inline is_safe()), so the 2s status
        # loop and the engine gate read it for free. None until the first poll.
        self._safety_task: asyncio.Task | None = None
        self._safety_reading: SafetyReading | None = None
        # connection-replay map: role -> dict the reconnect path needs to rebuild
        # an Alpaca device (host/port/dev_type/dev_num/name). Populated in every
        # connect path; consumed by reconnect_role() (escalation/reconnect_resume).
        self._last_connect: dict[str, dict] = {}
        # the last connect-by-profile / connect-by-rig / boot ConnectResult, retained
        # so the boot-LED grid (backend_links) can report the per-role tri-state that
        # survives a page reload (W1.6). None until the first RigSpec connect / after
        # a manual disconnect. The legacy connect_sim/connect_nina/connect_alpaca_device
        # paths leave it None (they predate this surface and report via self.devices).
        self.last_connect_result: ConnectResult | None = None
        # per-role BackendSession opened by the legacy single-role Alpaca connect
        # path (connect_alpaca_device). Retained so the httpx client each one owns
        # is aclosed when the role is replaced or the rig is torn down, instead of
        # leaking a keep-alive socket pool per reconnect (session-leak fix).
        self._alpaca_sessions: dict[str, Any] = {}
        # single-lane connect serialization (connect-race fix): every connect path
        # AND the teardown acquire this so two overlapping connects can never
        # interleave disconnect_all/connect and leave hub.devices a union of two
        # rigs with orphaned (still-connected) device objects.
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        # single-camera mutual exclusion (capture-interlock fix): the live loop,
        # single capture, autofocus, sequence and solve exposures all acquire this
        # for the duration of the actual expose() so two coroutines can never poll
        # the shared imageready flag at once (cross-downloaded frames / stamped
        # with the wrong exposure metadata / InvalidOperation).
        self._capture_lock: asyncio.Lock = asyncio.Lock()
        self._capture_busy: str | None = None
        # cached EquatorialSystem verdict for the connected Alpaca mount: True when
        # it expects topocentric (JNOW) coordinates and the hub must precess
        # J2000<->JNOW at the slew/sync boundary. None until first probed; reset on
        # teardown. Only consulted in native ("alpaca") mode.
        self._mount_wants_jnow: bool | None = None
        # boot auto-connect background task (boot-serves-immediately fix): the
        # lifespan spawns connect_active here instead of awaiting it inline, so the
        # HTTP/WS surface comes up at once even against an unreachable rig.
        self._boot_connect_task: asyncio.Task | None = None
        self._nina_ws_task: asyncio.Task | None = None
        self._nina_hb_task: asyncio.Task | None = None   # 5s NINA heartbeat
        self._bridge_ready = False              # false until first successful NINA poll
        # --- move-axis deadman (touch safety §C-Risk-5; safety surface reuses) ---
        # last_move_ts is the monotonic timestamp of the most recent manual
        # move/keepalive; _move_rates_seen is the last commanded rate per axis.
        # A dedicated 250ms watchdog task auto-halts both axes when a non-zero
        # move goes un-refreshed for longer than MOVE_DEADMAN_MS — the single
        # deadman the whole program shares.
        self.last_move_ts: float | None = None
        self._move_rates_seen: dict[str, float] = {"ra": 0.0, "dec": 0.0}
        self._move_watchdog_task: asyncio.Task | None = None
        self._busy: dict[str, asyncio.Task] = {}
        # --- motion serialization (W3.7 owner invariant) -----------------------
        # The single mount has ONE motion authority. ``_motion_lock`` serializes
        # the device-touching section of every motion-committing path (slew/park/
        # move/center step) so two commits can never interleave on the wire.
        # ``_motion_epoch`` is a monotonic fence: a long path reads it before its
        # await and re-checks it UNCHANGED immediately before dispatching the
        # device command, abandoning if it advanced. Every STOP/abort/park/
        # on_unsafe/deadman bumps the epoch FIRST (``bump_motion_epoch``), so a
        # stale REMOTE slew accepted just before a LOCAL abort sees the advanced
        # epoch and never reaches the mount. Co-located with the single-mount
        # deadman state above.
        self._motion_lock: asyncio.Lock = asyncio.Lock()
        self._motion_epoch: int = 0
        self.polar = PolarAlignSession(self)
        # cache of the active Profile, keyed by its id, so the 2s status poll's
        # effective_optics() never does a blocking disk read on the event loop.
        # Invalidated on apply/save/delete and on an active-id change.
        self._profile_cache_id: str | None = None
        self._profile_cache: Profile | None = None

    # ------------------------------------------------------------ connection

    async def connect_sim(self) -> dict:
        # Serialize with every other connect / teardown (connect-race fix): the
        # single-lane lock is held across the whole disconnect+connect so an
        # overlapping connect can't interleave and union two rigs together.
        async with self._connect_lock:
            return await self._connect_sim_unlocked()

    async def _connect_sim_unlocked(self) -> dict:
        # Stage A: route through the pluggable harness (RigSpec -> assemble ->
        # SimBackend) instead of calling build_sim_rig() directly. Behavior is
        # preserved exactly: the orchestrator hands out the SAME sim device
        # objects (sharing one SimRig state), and this method still connects each
        # role, the guide camera and the SimGuider, sets self.sim_rig, and derives
        # self.mode = "sim".
        await self._teardown()
        RigSpec, ConnSpec, connect_profile = _harness()  # noqa: N806 (lazy import)
        result = await connect_profile(RigSpec(primary="sim"))
        # the lone sim session owns the shared SimRig state; the guide camera is
        # surfaced through the contracted BackendSession.guide_camera() accessor
        # into ConnectResult.guide_camera (W1.3), not the SimSession-only property.
        session = next(iter(result.sessions.values()))
        self.sim_rig = session.shared_state
        guide_cam = result.guide_camera
        for role in ROLES:
            dev = result.rig.get(role)
            if dev is None:
                continue
            await dev.connect()
            self.devices[role] = dev
            self._last_connect[role] = {"backend": "sim"}
        await guide_cam.connect()
        self.devices["guide_camera"] = guide_cam
        # native guider from the assembled rig (the SimGuider), connected here.
        self.guider = result.guider
        await self.guider.connect()
        self.mode = "sim"                               # derived from primary backend
        bus.log("info", "simulator rig connected", "hub")
        self.ensure_status_poller()
        return self.summary()

    async def connect_nina(self, host: str, port: int = 1888) -> dict:
        """Bridge to a running NINA instance (Advanced API plugin).

        Stage A: routed through the pluggable harness (RigSpec -> assemble ->
        NinaBackend) while preserving exact behavior. The hub's OWN module-level
        ``build_nina_rig`` reference is passed into the backend via
        ``ConnSpec.extra['build_rig']`` so the existing test monkeypatch seam
        (``monkeypatch.setattr(hub, 'build_nina_rig', ...)``) keeps working."""
        async with self._connect_lock:
            return await self._connect_nina_unlocked(host, port)

    async def _connect_nina_unlocked(self, host: str, port: int = 1888) -> dict:
        await self._teardown()
        self._bridge_ready = False             # warming-up until first heartbeat
        RigSpec, ConnSpec, connect_profile = _harness()  # noqa: N806 (lazy import)
        from .devices.backend import get_backend
        # One ConnSpec (this host/port + the monkeypatchable builder) shared by
        # every role NINA can fill, so all roles resolve to a SINGLE NINA
        # session/client. Request exactly NinaBackend.roles (NOT blindly all of
        # ROLES) so the harness never asks NINA for the ``safety`` role it cannot
        # fill; primary="nina" still resolves these to the one NINA session.
        conn = ConnSpec(backend="nina", host=host, port=port,
                        extra={"build_rig": build_nina_rig})
        spec = RigSpec(primary="nina",
                       roles={r: conn for r in get_backend("nina").roles})
        result = await connect_profile(spec)
        # Fail-fast preservation: connect_profile swallows a failed backend.open()
        # into per-role results (no session for that endpoint). If NINA was
        # unreachable no session opened for the primary, so re-raise the recorded
        # reason as a DeviceError (matching build_nina_rig's old propagated
        # "cannot reach NINA ..." error) instead of leaking an opaque
        # StopIteration from the empty-sessions case. ``failures`` is now a
        # derived alias, so read the reason from the not-ok ``results``.
        if not any(key[0] == "nina" for key in result.sessions):
            # Prefer a DEVICE-role (non-guider) error: when open() raises, every
            # device role records the real reason ("cannot reach NINA ..."),
            # whereas the guider role only ever carries the generic "no guider"
            # placeholder (it is resolved post-loop, not from open()). Fall back
            # to any not-ok reason, then a default.
            not_ok = [rr for rr in result.results if not rr.ok and rr.error]
            reason = next(
                (rr.error for rr in not_ok if rr.role != "guider"),
                next((rr.error for rr in not_ok), "could not open NINA session"))
            raise DeviceError(reason)
        session = next(iter(result.sessions.values()))
        self.nina_client = session.client
        self.mode = "nina"                              # derived from primary backend
        # only roles NINA reported connected appear in result.rig (the rest are
        # not-ok RoleResults and are skipped) — same set the old loop populated.
        for role in ROLES:
            dev = result.rig.get(role)
            if dev is not None:
                self.devices[role] = dev
        if result.guider:
            self.guider = result.guider
        connected = [r for r in ROLES if r in self.devices]
        roles = ", ".join(connected) or "no equipment connected in NINA"
        bus.log("info", f"bridged to NINA at {host}:{port} — {roles}", "nina")
        self.ensure_status_poller()
        self._start_nina_ws()
        return self.summary()

    async def connect_alpaca_device(self, role: str, host: str, port: int,
                                    dev_type: str, dev_num: int, name: str) -> dict:
        async with self._connect_lock:
            return await self._connect_alpaca_device_unlocked(
                role, host, port, dev_type, dev_num, name)

    async def _connect_alpaca_device_unlocked(self, role: str, host: str, port: int,
                                              dev_type: str, dev_num: int,
                                              name: str) -> dict:
        # Stage A: route the single-role Alpaca connect through the NativeBackend
        # (RigSpec/registry) instead of calling alpaca.make_device directly. The
        # native session's get_device(role, conn) builds the device against the
        # session's shared AlpacaConnection, sets dev.role and awaits connect(),
        # so behavior is preserved (the connect()/role set below are idempotent).
        _RigSpec, ConnSpec, _connect_profile = _harness()  # noqa: N806 (lazy import)
        from .devices.backend import get_backend
        conn = ConnSpec(backend="native", host=host, port=port,
                        dev_type=dev_type, dev_num=dev_num, role=role,
                        extra={"name": name})
        session = await get_backend("native").open(conn)
        dev = await session.get_device(role, conn)
        await dev.connect()
        dev.role = role                        # device identity for Profiles (A.6)
        old = self.devices.get(role)
        old_session = self._alpaca_sessions.get(role)
        self.devices[role] = dev
        self._mount_wants_jnow = None          # re-probe EquatorialSystem after a mount swap
        # retain the session so its httpx client is aclosed when this role is later
        # replaced or the rig torn down (session-leak fix); close the one we are
        # replacing so its keep-alive sockets don't accumulate per reconnect.
        self._alpaca_sessions[role] = session
        if old:
            try:
                await old.disconnect()
            except Exception:
                pass
        if old_session is not None:
            try:
                await old_session.close()
            except Exception:
                pass
        # record enough to replay this connection (reconnect_role / escalation).
        self._last_connect[role] = {"backend": "alpaca", "host": host, "port": port,
                                    "dev_type": dev_type, "dev_num": dev_num,
                                    "name": name}
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

    # ------------------------------------------------- connect by profile / rig

    async def connect_rigspec(self, spec: "RigSpec", *, set_active: str | None = None,
                              profile: "Profile | None" = None) -> dict:
        """The single low-level connect-by-RigSpec entry: the profile path, the
        ``/api/connect/rig`` route and boot auto-connect all funnel through here.

        Tears down any current rig FIRST (so a failed open never strands the live
        rig mid-flight -- same order as ``connect_sim``), then runs the Stage A
        orchestrator (graceful per-role degrade: a failed role becomes a not-ok
        ``RoleResult`` and is simply absent from ``result.rig``), applies the
        ``ConnectResult`` onto hub state, and -- only on success -- records the
        active-profile pointer. An unexpected raise from ``connect_profile`` (it
        first tears down any sessions it opened, no transport leak) propagates to
        the caller; the API maps it to 4xx/502 and the boot path swallows it.

        NEVER initiates motion: it only opens device connections (no unpark / slew
        / set_tracking)."""
        async with self._connect_lock:
            return await self._connect_rigspec_unlocked(
                spec, set_active=set_active, profile=profile)

    async def _connect_rigspec_unlocked(self, spec: "RigSpec", *,
                                        set_active: str | None = None,
                                        profile: "Profile | None" = None) -> dict:
        await self._teardown()
        RigSpec, ConnSpec, connect_profile = _harness()  # noqa: N806 (lazy import)
        # Phase 2 (spec §3.3): resolve driver_id references to concrete
        # addressing. Missing/disabled drivers pre-fail their role HONESTLY —
        # they surface as attempted+failed RoleResults below, never vanish.
        from . import drivers as drivers_mod
        spec, role_to_driver, prefailed = drivers_mod.resolve_driver_ids(spec)
        result = await connect_profile(spec)
        if prefailed:
            from .devices.orchestrator import RoleResult
            keep = [rr for rr in result.results
                    if rr.role not in {r for r, _ in prefailed}]
            keep.extend(RoleResult(role, ok=False, error=err, attempted=True)
                        for role, err in prefailed)
            result.results = keep
        # Cache honesty (spec §3.2): a driver-backed role that FAILED to
        # connect drops that driver's probe-cache row immediately, so the
        # Equipment surface's next /api/drivers read reflects reality instead
        # of a up-to-15s-stale 'reachable'.
        failed = {rr.role for rr in result.results if rr.attempted and not rr.ok}
        for role, did in role_to_driver.items():
            if role in failed:
                drivers_mod.invalidate(did)
        summary = await self._apply_connect_result(result, primary=spec.primary)
        if set_active is not None:
            await asyncio.to_thread(config_store.set_active_profile, set_active)
            if profile is not None:
                # seed the active-profile cache from the object we already hold so
                # the next effective_optics() is served from memory (no disk read).
                self._profile_cache = profile
                self._profile_cache_id = set_active
        return {
            "summary": summary,
            "results": [vars(rr) for rr in result.results],
            "backend_links": self.backend_links(),
        }

    async def connect_profile_id(self, pid: str) -> dict:
        """Connect the persisted profile ``pid`` (its ``to_rigspec()``) and set it
        active on success. Raises ``KeyError`` if the id is unknown (the API maps
        it to 404). The disk read is offloaded so it never blocks the event loop."""
        prof = await asyncio.to_thread(profiles.get, pid)
        return await self.connect_rigspec(prof.to_rigspec(), set_active=pid,
                                          profile=prof)

    async def connect_active(self) -> dict | None:
        """Connect the active profile if one is set, else a clean no-op (None).

        The single entry the boot path calls. Returns None on first run / when no
        ``active_profile_id`` is configured, so boot connects nothing."""
        pid = config_store.cfg().active_profile_id
        if not pid:
            return None
        return await self.connect_profile_id(pid)

    async def _apply_connect_result(self, result: "ConnectResult", *, primary: str,
                                    last_connect_meta: dict[str, dict] | None = None
                                    ) -> dict:
        """Map a ``ConnectResult`` onto hub state -- the single generalized place
        that does what ``connect_sim``/``connect_nina`` do inline.

        The CALLER tears the current rig down BEFORE building ``result`` (so a
        failed open never tears down a working rig mid-flight). Here we connect
        each device the orchestrator produced (idempotent), wire the guider /
        guide camera, derive ``self.mode`` from the primary backend, set the
        sim/NINA handles only when the primary exposes them, retain the
        ``ConnectResult`` for the boot-LED grid, and start the pollers."""
        # primary "none" (explicit-only rig, spec §4.1): there is no declared
        # primary to derive mode/handles from — pick the strongest connected
        # backend. Preference nina > native > sim: the NINA handle powers the
        # heartbeat/event-stream wiring below, a native session means a real
        # (alpaca-mode) rig, and sim is the weakest signal. `mode` stays the
        # legacy scalar; per-device truth lives in backend_links.
        if primary in ("", "none"):
            primary = self._effective_primary(result)
        meta = last_connect_meta or {}
        for role in ROLES:
            dev = result.rig.get(role)
            if dev is None:
                continue
            await dev.connect()                       # idempotent
            self.devices[role] = dev
            # record enough to replay this connection (reconnect_role); default to
            # the device's own backend label so a native reconnect still works.
            self._last_connect[role] = meta.get(
                role, {"backend": getattr(dev, "backend", primary)})
        # dedicated guide camera (sim only; None for nina/native/phd2).
        if result.guide_camera is not None:
            await result.guide_camera.connect()
            self.devices["guide_camera"] = result.guide_camera
        # guider: the guider-role session's native guider (SimGuider / NinaGuider /
        # PHD2). None when the rig has no guider role / it degraded.
        self.guider = result.guider
        if self.guider is not None:
            await self.guider.connect()
        # sim/NINA handles: set ONLY from the PRIMARY session, read via the
        # Protocol-safe accessors (sim ``shared_state`` / nina ``client``) exactly
        # as the existing connect_* methods do -- never a blanket attribute read.
        self.sim_rig = None
        self.nina_client = None
        if primary == "sim":
            session = self._primary_session(result, "sim")
            if session is not None:
                self.sim_rig = session.shared_state
        elif primary == "nina":
            session = self._primary_session(result, "nina")
            if session is not None:
                self.nina_client = session.client
        # derived rig label. The legacy UI / poll_status / _preview_source key off
        # "alpaca" for the direct path, so map native -> "alpaca" (W1.6 PIN).
        self.mode = "alpaca" if primary == "native" else primary
        # retain the tri-state result for the boot-LED grid (backend_links).
        self.last_connect_result = result
        self.ensure_status_poller()
        if primary == "nina":
            self._start_nina_ws()
        connected = [r for r in ROLES if r in self.devices]
        roles = ", ".join(connected) or "no equipment connected"
        bus.log("info", f"rig connected ({self.mode}) -- {roles}", "hub")
        return self.summary()

    @staticmethod
    def _primary_session(result: "ConnectResult", backend_name: str):
        """The open session for ``backend_name`` (the rig's primary), or None.

        Sessions are keyed by ``(backend, host, port)``; return the first whose
        backend matches so the hub can read its sim ``shared_state`` / nina
        ``client`` without reaching off-Protocol."""
        for key, session in result.sessions.items():
            if key[0] == backend_name:
                return session
        return None

    @staticmethod
    def _effective_primary(result: "ConnectResult") -> str:
        """Derive a primary label for a primary-less rig from its OPEN
        sessions, preference nina > native > sim, else the first session's
        backend, else "sim" (an empty rig behaves like the old default)."""
        names = [k[0] for k in result.sessions]
        for pref in ("nina", "native", "sim"):
            if pref in names:
                return pref
        return names[0] if names else "sim"

    def backend_links(self) -> list[dict]:
        """The per-role boot-LED surface (W1.6): the retained ConnectResult's
        tri-state ``RoleResult`` joined with each role's LIVE ``connected`` state.

        ``[]`` when no RigSpec connect has happened (the legacy connect_* paths
        don't populate ``last_connect_result``)."""
        res = self.last_connect_result
        if res is None:
            return []
        out: list[dict] = []
        for rr in res.results:
            dev = self.devices.get(rr.role)
            out.append({
                "role": rr.role,
                "ok": rr.ok,
                "error": rr.error,
                "attempted": rr.attempted,
                "connected": bool(dev is not None and getattr(dev, "connected", False)),
            })
        return out

    async def disconnect_all(self) -> None:
        """Public teardown entry (routes / shutdown). Serialized with every
        connect path through ``_connect_lock`` so a disconnect can't interleave a
        connect that is mid-flight."""
        async with self._connect_lock:
            await self._teardown()

    async def _teardown(self) -> None:
        """The actual rig teardown. MUST be called with ``_connect_lock`` held (via
        ``disconnect_all`` or from inside a locked connect path) so it never races
        a concurrent connect. Defense-in-depth: skip ``asyncio.current_task()`` in
        the busy-cancel loop so a driver that runs teardown as its first step (the
        legacy apply path) can never cancel itself."""
        self.stop_loop()
        await self.polar.stop()
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
        self._status_task = None
        if self._safety_task and not self._safety_task.done():
            self._safety_task.cancel()
        self._safety_task = None
        self._safety_reading = None
        self._last_connect.clear()
        if self._nina_ws_task and not self._nina_ws_task.done():
            self._nina_ws_task.cancel()
        self._nina_ws_task = None
        if self._nina_hb_task and not self._nina_hb_task.done():
            self._nina_hb_task.cancel()
        self._nina_hb_task = None
        if self._move_watchdog_task and not self._move_watchdog_task.done():
            self._move_watchdog_task.cancel()
        self._move_watchdog_task = None
        self.last_move_ts = None
        self._move_rates_seen = {"ra": 0.0, "dec": 0.0}
        self._bridge_ready = False
        current = asyncio.current_task()
        for task in self._busy.values():
            if task is not current:
                task.cancel()
        self._busy.clear()
        for dev in self.devices.values():
            try:
                await dev.disconnect()
            except Exception:
                pass
        self.devices.clear()
        self._mount_wants_jnow = None
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
        # Close the native httpx clients we still hold, so a profile switch /
        # repeated reconnect leaks no keep-alive socket pool (session-leak fix):
        # the retained RigSpec-connect sessions AND the per-role legacy-Alpaca
        # sessions. Best-effort — one failing aclose must not strand the others.
        if self.last_connect_result is not None:
            for session in self.last_connect_result.sessions.values():
                try:
                    await session.close()
                except Exception:
                    pass
        for session in self._alpaca_sessions.values():
            try:
                await session.close()
            except Exception:
                pass
        self._alpaca_sessions.clear()
        self.sim_rig = None
        self.mode = "none"
        # a manual disconnect clears the boot-LED grid (no stale tri-state).
        self.last_connect_result = None
        bus.log("info", "all equipment disconnected", "hub")

    def require(self, role: str):
        dev = self.devices.get(role)
        if dev is None or not dev.connected:
            raise DeviceError(f"no {role} connected")
        return dev

    @property
    def safety(self) -> SafetyMonitor | None:
        """The connected SafetyMonitor (cloud/rain/roof sensor), or None."""
        return self.devices.get("safety")

    async def reconnect_role(self, role: str) -> bool:
        """Best-effort reconnect of one role from the recorded connection intent
        (Batch 4b escalation ``reconnect_resume`` / a dropped Alpaca link). Only
        Alpaca roles can be replayed from ``_last_connect``; sim/NINA roles return
        False (nothing to replay). Never raises — returns success as a bool."""
        info = self._last_connect.get(role)
        if not info or info.get("backend") != "alpaca":
            return False
        try:
            await self.connect_alpaca_device(
                role, info["host"], info["port"], info["dev_type"],
                info["dev_num"], info["name"])
            bus.log("info", f"reconnected {role} ({info['host']}:{info['port']})", "hub")
            return True
        except Exception as e:
            bus.log("warning", f"reconnect {role} failed: {e}", "hub")
            return False

    async def safety_reading(self) -> SafetyReading | None:
        """The latest CACHED SafetyReading from the own-cadence poller — NEVER an
        inline ``is_safe()`` (that could block the 2s status loop / engine gate,
        C1-12). Returns None when no SafetyMonitor is connected or it hasn't been
        polled yet. The engine fail-closes a ``stale`` reading to UNSAFE.

        Age guard: if the poller has stopped ticking (so the cached reading is
        older than ~2 poll cycles), mark the returned reading ``stale`` so the
        engine fail-closes — never keep trusting an old SAFE reading forever."""
        if self.safety is None:
            return None
        r = self._safety_reading
        if r is not None and not r.stale:
            age = time.time() - r.ts
            if age > SAFETY_POLL_INTERVAL_S + SAFETY_READ_TIMEOUT_S + SAFETY_STALE_SLACK_S:
                return replace(r, is_safe=False, stale=True,
                               reason="safety reading stale (poller stopped)")
        return r

    def _guide_camera_info(self) -> dict | None:
        """Vendor-neutral guide-camera descriptor for the UI's rig list.

        AstroDeck only owns a dedicated ``guide_camera`` *device* in sim mode.
        In NINA/Alpaca/PHD2 mode the guiding device is the guider itself (PHD2
        driving e.g. an ASI220), so derive the guide-camera entry from the
        connected guider — name + connected — rather than reporting "no guide
        camera". Prefer a real ``guide_camera`` device when one exists; else fall
        back to the guider. Returns None when neither is present."""
        dev = self.devices.get("guide_camera")
        if dev is not None:
            return {"name": dev.name, "connected": dev.connected}
        if self.guider is not None:
            return {"name": self.guider.name, "connected": self.guider.connected}
        return None

    def summary(self) -> dict:
        sr = self._safety_reading
        return {
            "devices": {r: d.describe() for r, d in self.devices.items()},
            "guider": {"name": self.guider.name, "connected": self.guider.connected}
            if self.guider else None,
            "guide_camera": self._guide_camera_info(),
            "sim": self.sim_rig is not None,
            "mode": self.mode,
            "site": self.site,
            "optics": self.effective_optics(),
            # latest cached SafetyReading (None until the first poll / no monitor),
            # and the redacted config snapshot (alert tokens blanked).
            "safety": (self._safety_reading_dict(sr) if sr is not None else None),
            # per-role boot-LED tri-state (W1.6); [] for the legacy connect_* paths.
            "backend_links": self.backend_links(),
            "config": redacted(config_store.cfg()),
        }

    @staticmethod
    def _safety_reading_dict(r: SafetyReading) -> dict:
        """Serialize a SafetyReading for WS/REST."""
        return {"is_safe": r.is_safe, "reason": r.reason, "source": r.source,
                "detail": r.detail, "stale": r.stale, "ts": r.ts}

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

    def _check_solar(self, ra_hours: float, dec_deg: float, *,
                     force: bool = False) -> None:
        """Sun-exclusion cone guard (defense in depth, W1.10).

        ON by default to protect normal deep-sky gear from a daytime slew at the
        Sun. Computes the Sun's apparent RA/Dec from the DATE (observer parallax
        ~8.8 arcsec is negligible vs a degrees-wide cone) so the gate works even
        on a default site with no lat/lon -- unlike ``_check_horizon`` it is NOT
        inert on a default site. Inert only when a deliberate solar-astronomy
        session disarms it (``solar_avoidance`` False) or the cone is zeroed.

        ``force`` is threaded for one shared signature with ``_check_horizon`` but
        does NOT bypass the cone: a per-call goto/sequence ``force`` clears only
        the visible-horizon check, never sun avoidance (disarming requires the
        admin ``config.solar_override`` capability + a config write)."""
        safety = config_store.cfg().safety
        if not getattr(safety, "solar_avoidance", True):
            return
        cone = getattr(safety, "solar_exclusion_deg", 30.0)
        if cone <= 0:
            return
        from .catalog.coords import sun_radec
        sun_ra, sun_dec = sun_radec()
        sep = _ang_sep_deg(ra_hours, dec_deg, sun_ra, sun_dec)
        if sep < cone:
            raise DeviceError(
                f"target is within {sep:.0f} deg of the Sun (exclusion "
                f"{cone:.0f} deg) - enable a solar session "
                f"(config.solar_override) to override")

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

    @property
    def restart_blocker(self) -> "str | None":
        """A human reason the controller must NOT restart right now, or None when
        idle. The self-update safety gate consults this so an update can never
        interrupt an exposure, slew, or running sequence (spec section 6)."""
        label = self.busy_label
        if label is not None:
            return f"rig is {label}"
        eng = self.engine
        if eng is not None and getattr(eng, "running", False):
            return "a sequence is running"
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

    # ----------------------------------------------------- motion serialization

    def bump_motion_epoch(self) -> int:
        """Advance the motion fence (W3.7). EVERY abort path -- explicit STOP, a
        safety/on_unsafe halt, park, or the deadman -- calls this FIRST (before it
        cancels the ``goto`` task / calls ``tel.stop()``), so a motion-committing
        path that read the prior epoch and is now awaiting will see the value has
        advanced and ABANDON its device command at the pre-dispatch re-check
        (``_motion_committed_clean``). Returns the new epoch (the value the next
        commit will read at entry). Synchronous + lock-free: a single ``int +=``
        on the event loop, so an abort can fence even while ``_motion_lock`` is
        held by the slew it is racing."""
        self._motion_epoch += 1
        return self._motion_epoch

    def _motion_committed_clean(self, epoch_at_entry: int) -> bool:
        """True iff no abort has bumped the fence since ``epoch_at_entry`` was read.
        A motion path reads ``_motion_epoch`` BEFORE its setup awaits, then calls
        this immediately before dispatching the actual device command; a False
        result means a STOP/abort landed mid-flight and the command MUST be
        abandoned (the stale-remote-slew-after-local-abort race)."""
        return self._motion_epoch == epoch_at_entry

    # ----------------------------------------------------- move-axis deadman

    def note_move(self, axis: str, rate: float) -> None:
        """Stamp a manual move (or its 1Hz keepalive). Records the per-axis rate
        and the time, and arms the deadman task. Called by ``/api/mount/move``
        after every (clamped) manual move and by the client keepalive. This is
        the single arming primitive the touch surface AND the Batch-4 safety
        surface both call — neither re-creates the watchdog task."""
        if axis in self._move_rates_seen:
            self._move_rates_seen[axis] = rate
        self.last_move_ts = time.monotonic()
        self.ensure_move_watchdog()

    def ensure_move_watchdog(self) -> None:
        """Spawn the move-axis watchdog if it isn't already running. Idempotent —
        the single deadman task (master plan §C-Risk-5)."""
        if self._move_watchdog_task is None or self._move_watchdog_task.done():
            self._move_watchdog_task = asyncio.create_task(self._move_watchdog())

    async def _move_watchdog(self) -> None:
        """Dedicated 250ms deadman, NOT the 2.0s status loop (which is far too
        slow to bound uncommanded travel). If a non-zero manual move was issued
        and no refresh/keepalive arrived within ``MOVE_DEADMAN_MS``, halt both
        axes via ``tel.stop()`` (which zeroes MoveAxis) and clear the seen rates.
        A dropped network mid-hold therefore auto-stops within ≤1.2s — worst case
        ≤0.72° travel at the 0.6°/s touch cap."""
        while True:
            await asyncio.sleep(0.25)
            if self.last_move_ts is None:
                continue
            moving = any(r != 0 for r in self._move_rates_seen.values())
            if not moving:
                continue
            stale_ms = (time.monotonic() - self.last_move_ts) * 1000.0
            if stale_ms <= MOVE_DEADMAN_MS:
                continue
            tel = self.devices.get("telescope")
            if tel is None:
                # No mount to halt — nothing can be moving. Disarm so a
                # reconnect doesn't fire against stale state.
                self._move_rates_seen = {"ra": 0.0, "dec": 0.0}
                self.last_move_ts = None
                continue
            # F-A3 (DURABLE HALT — the most dangerous fail-UNSAFE path): only
            # disarm AFTER tel.stop() actually succeeds. On failure (the dropped
            # keepalive that tripped the deadman is exactly the network condition
            # that makes the stop HTTP call fail) leave the seen-rates non-zero
            # and last_move_ts stale, so the next 250ms tick RETRIES the halt
            # rather than silently giving up while an axis may still be driving.
            # Motion fence (W3.7): a deadman halt is an abort -- bump the epoch
            # FIRST so any motion path racing this auto-halt is fenced out before
            # the stop, exactly like an explicit STOP.
            self.bump_motion_epoch()
            try:
                await tel.stop()              # zeroes both axes (alpaca/base)
            except Exception as e:
                bus.log("error",
                        f"manual slew watchdog: auto-halt FAILED, retrying: {e}",
                        "safety")
                continue
            # F-watchdog-disarm: stop succeeded — return to a clean disarmed
            # state (zero rates AND clear last_move_ts) so the watchdog idles
            # instead of being "correct by luck" on the next tick.
            self._move_rates_seen = {"ra": 0.0, "dec": 0.0}
            self.last_move_ts = None
            bus.log("warning",
                    "manual slew watchdog: auto-halt (stale keepalive)", "safety")

    # --------------------------------------------------------------- profiles

    async def apply_profile(self, p: Profile) -> dict:
        """Replay a profile's connection intent, returning a per-device outcome
        (never a lying "all connected" toast). Auto-detected camera optics are
        persisted back so a later disconnected apply still has a real FOV.

        Serialized under ``_connect_lock`` (connect-race fix) and driven via the
        internal ``*_unlocked`` workers so the nested connects don't re-acquire it.
        NOTE: this runs on the ``_spawn_connect`` lane (see the apply route), NOT
        ``_busy``, so the first ``_teardown`` here can never cancel its own driver
        task (the historical apply self-cancel)."""
        async with self._connect_lock:
            return await self._apply_profile_unlocked(p)

    async def _apply_profile_unlocked(self, p: Profile) -> dict:
        await self._teardown()
        results: list[dict] = []
        # Connect NINA FIRST, then the Alpaca rows: connect_nina tears down the
        # whole rig as its first step, so connecting it AFTER the Alpaca devices
        # (as the old order did) wiped every Alpaca device just connected in this
        # same apply while still reporting them ok=True. Alpaca rows go through the
        # single-role path, which replaces only its own role (no teardown), so the
        # NINA devices survive (mixed-rig fix).
        if p.nina_host and any(d.backend == "nina" for d in p.devices):
            try:
                await self._connect_nina_unlocked(p.nina_host, p.nina_port)
                results.append({"role": "nina", "ok": True})
            except Exception as e:
                results.append({"role": "nina", "ok": False, "error": str(e)})
        for d in p.devices:
            if d.backend != "alpaca":
                continue
            try:
                await self._connect_alpaca_device_unlocked(
                    d.role, d.host, d.port, d.dev_type, d.dev_num,
                    d.name or f"{d.dev_type} #{d.dev_num}")
                results.append({"role": d.role, "ok": True})
            except Exception as e:
                results.append({"role": d.role, "ok": False, "error": str(e)})
                bus.log("warning", f"profile '{p.name}': {d.role} failed: {e}", "profile")
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
        # Record the primary explicitly: a captured rig must NEVER fall back to
        # the sim primary, or every role not captured (safety, guider, ...) would
        # silently resolve to a simulator on the next activate (fail-open safety).
        if self.nina_client is not None:
            primary = "nina"
        elif any(d.backend == "alpaca" for d in devs):
            primary = "native"
        else:
            primary = "sim" if self.mode == "sim" else ""
        p = Profile(name=name, devices=devs, primary_backend=primary,
                    nina_host=(self.nina_client.host if self.nina_client else None),
                    site_name=self.site.get("name"))
        profiles.save(p)
        return p

    # --------------------------------------------------------------- capture

    @contextlib.asynccontextmanager
    async def exposure_guard(self, label: str):
        """Serialize the single camera across every capture path (live loop,
        single capture, autofocus, sequence, plate-solve). The lock is held ONLY
        for the duration of the actual ``expose()`` — long enough that two
        exposures can't poll the shared ``imageready`` flag at once, short enough
        that the between-frame work (save, solve, slew) doesn't hold it.

        Non-blocking: if the camera is already exposing for another path, raise
        ``DeviceError`` (which the API maps to 409) with the busy label rather than
        queueing behind it — mirrors the ``_spawn`` busy pattern. Single-threaded
        acquire has no gap between the ``locked()`` check and ``acquire`` (the lock
        grants synchronously when free), so this is race-free on the event loop."""
        if self._capture_lock.locked():
            raise DeviceError(
                f"camera is busy ({self._capture_busy or 'exposing'}); "
                f"{label} refused")
        async with self._capture_lock:
            self._capture_busy = label
            try:
                yield
            finally:
                self._capture_busy = None

    async def _mount_expects_jnow(self, tel) -> bool:
        """True when the connected mount expects topocentric-apparent (JNOW)
        coordinates, so the hub must precess J2000<->JNOW at the slew/sync
        boundary. The gate keys on the mount DEVICE's backend (``devices/
        alpaca.py`` sets ``backend = "alpaca"``), not the global ``hub.mode`` —
        so a mixed rig (e.g. primary NINA with a native Alpaca mount) still
        precesses correctly; sim/NINA mounts (backend ``""``/``"nina"``) stay
        unconverted as before. The ``_mount_wants_jnow`` cache is unchanged,
        reset on device placement in ``_connect_alpaca_device_unlocked`` and
        on teardown in ``_teardown`` (invoked via ``disconnect_all``).

        Best-effort EquatorialSystem probe (cached): ASCOM ``EquatorialSystem`` is
        0=other, 1=topocentric(local/JNOW), 2=J2000, 3=B1950. Default to JNOW when
        unreadable — real ASCOM mounts are overwhelmingly topocentric, and a mount
        that already reports J2000 (==2) is left un-precessed so we never double-
        precess it."""
        if getattr(tel, "backend", "") != "alpaca":
            return False
        if self._mount_wants_jnow is not None:
            return self._mount_wants_jnow
        wants = True
        get = getattr(tel, "_get", None)
        if get is not None:
            try:
                equ = await get("equatorialsystem")
                # only a definitive J2000 (2) / B1950 (3) report disables it.
                wants = int(equ) not in (2, 3)
            except Exception:
                wants = True
        self._mount_wants_jnow = wants
        return wants

    async def to_mount_frame(self, tel, ra_hours: float,
                             dec_deg: float) -> tuple[float, float]:
        """Convert a J2000 target into the frame the mount expects, for slew/sync.
        No-op unless the mount is a JNOW Alpaca mount (see ``_mount_expects_jnow``).

        Fail-safe: if the astropy transform raises (e.g. an IERS hiccup on an
        offline Pi), fall back to the raw coordinates and log — a precession
        failure must never abort an unattended slew. The plate-solve center loop
        still corrects the residual, so worst case is one slightly-off first slew,
        not a dead night."""
        if not await self._mount_expects_jnow(tel):
            return ra_hours, dec_deg
        try:
            return await asyncio.to_thread(precess_j2000_to_jnow, ra_hours, dec_deg)
        except Exception as e:  # noqa: BLE001 - availability over precision here
            bus.log("warning", f"J2000->JNOW precession failed ({e}); "
                               "slewing raw coordinates", "mount")
            return ra_hours, dec_deg

    async def from_mount_frame(self, tel, ra_hours: float,
                               dec_deg: float) -> tuple[float, float]:
        """Convert a mount-reported position back to J2000. No-op unless the mount
        is a JNOW Alpaca mount. Same fail-safe fallback as ``to_mount_frame``."""
        if not await self._mount_expects_jnow(tel):
            return ra_hours, dec_deg
        try:
            return await asyncio.to_thread(precess_jnow_to_j2000, ra_hours, dec_deg)
        except Exception as e:  # noqa: BLE001 - availability over precision here
            bus.log("warning", f"JNOW->J2000 precession failed ({e}); "
                               "using raw coordinates", "mount")
            return ra_hours, dec_deg

    async def capture(self, exposure_s: float, gain: int, offset: int,
                      binning: int = 1, save: bool = False, target: str = "",
                      frame_type: str = "Light") -> dict:
        cam: Camera = self.require("camera")
        # Serialize the exposure against every other capture path (loop / single /
        # autofocus / sequence / solve) so two coroutines can't poll the shared
        # camera imageready flag at once (cross-downloaded frames / mis-stamped
        # metadata / InvalidOperation).
        async with self.exposure_guard(f"capture {frame_type.lower()}"):
            frame = await cam.expose(exposure_s, gain, offset, binning,
                                     light=(frame_type.upper() != "DARK"),
                                     save=save, target=target)
        self.last_frame = frame
        # For local (sim/Alpaca) saves, write the FITS BEFORE publishing the
        # preview so the first `preview` event already carries the correct
        # saved_path/saved_local (P2-2). NINA saves on the imaging host during
        # expose() and the frame already carries its saved_path.
        local_save_path: Path | None = None
        if save and frame.rendered_bytes is None:
            local_save_path = self._capture_path(target or "untargeted", frame_type)
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
            # Offloaded so a 25-120 MB uint16 FITS write to the Pi's SD card never
            # freezes the event loop for seconds every frame (WS/preview stall,
            # queued guide events, delayed STOP) — same as solve_and_sync's write.
            await asyncio.to_thread(
                save_fits, frame, local_save_path, target=target, filter_name=filt,
                frame_type=frame_type, ra_hours=ra, dec_deg=dec,
                instrument=cam.name)
            # carry the path on the frame so _publish_preview reports a correct
            # saved_path/saved_local in the very first event (no stale re-publish).
            frame.saved_path = str(local_save_path)

        info = await self._publish_preview(frame)

        if save and frame.rendered_bytes is not None:
            # The backend (NINA) already saved the file on the imaging machine.
            if frame.saved_path:
                bus.log("info", f"NINA saved {Path(frame.saved_path).name}", "capture")
            else:
                bus.log("info", "NINA saved the frame", "capture")
        elif local_save_path is not None:
            bus.log("info", f"saved {local_save_path.name}", "capture")
        return info

    def _preview_source(self) -> str:
        """The PreviewSource label ("sim"|"alpaca"|"nina") for the event."""
        return self.mode if self.mode in ("sim", "alpaca", "nina") else "sim"

    def _pixel_scale_arcsec(self, binning: int) -> float | None:
        """Arcsec/px for the captured (binned) frame, from the effective optics,
        so HFR can be shown in arcsec and the scale bar drawn. None when optics
        aren't known."""
        opt = self.effective_optics()
        base = opt.get("image_scale_arcsec_px")
        if not base:
            return None
        return round(float(base) * max(1, int(binning or 1)), 3)

    async def _publish_preview(self, frame) -> dict:
        """Build + publish the ``preview`` event = the PreviewInfo contract
        (live-preview spec §4.5/§6). Two corrected paths:

        * **NINA** — pre-rendered (auto-stretched) JPEG used verbatim; histogram
          is display-domain (``data_is_linear=False``); no lossless/linear
          retention; clip mask disabled; no per-star list (NINA gives none).
        * **raw/linear** (sim/Alpaca) — one ``detect_stars`` pass feeds HFR/count
          AND the star overlay; display JPEG via the auto-stretch; both a display
          histogram and a true linear histogram; a lossless PNG + the linear
          array kept for the latest 1–2 frames only (Pi memory)."""
        self.preview_seq += 1
        pid = self.preview_seq
        data = frame.data
        source = self._preview_source()
        binning = int(getattr(frame, "binning", 1) or 1)
        full_well = getattr(frame, "full_well", None)
        is_nina = getattr(frame, "rendered_bytes", None) is not None
        data_is_linear = bool(getattr(frame, "data_is_linear", not is_nina))

        saved_path = getattr(frame, "saved_path", None)
        info: dict[str, Any] = {
            "id": pid,
            "stats": frame_stats(data),
            "exposure_s": frame.exposure_s,
            "gain": frame.gain,
            "binning": binning,
            "data_width": int(data.shape[1]),
            "data_height": int(data.shape[0]),
            "source": source,
            "is_stretched": is_nina,
            "data_is_linear": data_is_linear,
            "full_well": int(full_well) if full_well else None,
            "pixel_scale_arcsec": self._pixel_scale_arcsec(binning),
            "bayer_pattern": getattr(frame, "bayer_pattern", None),
            "saved_path": saved_path,
            "saved_local": self._is_local_save(saved_path),
            "ts": time.time(),
        }

        if is_nina:
            # display = NINA's rendered bytes verbatim; histogram is of the
            # decoded 8-bit copy, honestly labeled display-domain.
            display, mime = frame.rendered_bytes, frame.rendered_mime
            thumb = await asyncio.to_thread(to_thumb, display)
            dw, dh = self._image_dims(display)
            # P3-2: _image_dims returns (0,0) on a PIL decode failure; fall back to
            # the data dims so the client never gets a 0-wide invisible stage /
            # broken ScaleBar (the client also guards with `|| data_width`).
            if not dw or not dh:
                dw, dh = info["data_width"], info["data_height"]
            info.update({
                "histogram": await asyncio.to_thread(compute_histogram, data),
                "histogram_domain": "display",
                "display_width": dw, "display_height": dh,
                "mime": mime, "has_lossless": False,
                "auto_levels": {"black": 0.0, "mid": 0.5, "white": 1.0},
            })
            entry = PreviewEntry(display=display, mime=mime, thumb=thumb, meta=info)
        else:
            black, mid, white = await asyncio.to_thread(auto_levels, data)
            jpeg, dw, dh = await asyncio.to_thread(
                to_jpeg, data, black=black, mid=mid, white=white)
            lossless = await asyncio.to_thread(to_png, data)
            thumb = await asyncio.to_thread(to_thumb, data)
            # display-domain histogram (handles have travel) + the true linear one
            stretched = await asyncio.to_thread(stretch_with, data, black, mid, white)
            hist_display = await asyncio.to_thread(display_histogram, stretched)
            # one detection pass → HFR + count + overlay marks (no double detect)
            hfr, count, marks = await asyncio.to_thread(
                measure_frame, data, full_well=info["full_well"])
            info.update({
                "histogram": hist_display,
                "histogram_linear": await asyncio.to_thread(compute_histogram, data),
                "histogram_domain": "display",
                "display_width": dw, "display_height": dh,
                "mime": "image/jpeg", "has_lossless": True,
                "auto_levels": {"black": round(black, 4), "mid": round(mid, 4),
                                "white": round(white, 4)},
                "star_list": marks,
            })
            # backend-measured HFR/stars (e.g. native) win; else our detection.
            if hfr is not None:
                info.setdefault("hfr", round(float(hfr), 2))
                info.setdefault("stars", int(count))
            # P3-1: do NOT retain the ~125 MB linear uint16 array in Pass 1 —
            # nothing reads entry.linear yet (/crop and /render are 501 stubs and
            # /lossless.png already covers paused/zoom). Re-enable retention
            # (linear=data) when those Pass-2 routes land.
            entry = PreviewEntry(display=jpeg, mime="image/jpeg", thumb=thumb,
                                 lossless=lossless, linear=None, meta=info)

        # backend-measured HFR/stars override (NINA carries its own)
        if getattr(frame, "hfr", None) is not None:
            info["hfr"] = round(float(frame.hfr), 2)
        if getattr(frame, "stars", None) is not None:
            info["stars"] = int(frame.stars)

        self.previews[pid] = entry
        self.preview_thumbs[pid] = entry.thumb
        self._trim_previews()
        bus.publish("preview", **info)
        return info

    @staticmethod
    def _is_local_save(saved_path: str | None) -> bool:
        """True only when ``saved_path`` is a real file under CAPTURE_DIR, so the
        UI never offers a FITS download that will 404 (spec honesty rule #5).
        NINA saves on the imaging host → not local → no FITS download offered."""
        if not saved_path:
            return False
        try:
            p = Path(saved_path).resolve()
            return p.is_relative_to(CAPTURE_DIR.resolve()) and p.exists()
        except (OSError, ValueError):
            return False

    def note_preview_saved(self, pid: int, saved_path: str) -> None:
        """Record a post-publish local save on the ring entry's meta so the
        ``/fits`` route can serve it (the hub saves sim/Alpaca FITS *after* the
        preview event has already gone out)."""
        entry = self.previews.get(pid)
        if entry is None:
            return
        entry.meta["saved_path"] = saved_path
        entry.meta["saved_local"] = self._is_local_save(saved_path)

    @staticmethod
    def _image_dims(buf: bytes) -> tuple[int, int]:
        """(width, height) of an encoded image, best-effort (0,0 on failure)."""
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(buf)) as im:
                return int(im.width), int(im.height)
        except Exception:
            return 0, 0

    def _trim_previews(self) -> None:
        """Enforce the three memory caps: full display for the latest 8, thumbs
        for the latest 50, and the heavy lossless/linear only for the latest 1–2."""
        cur = self.preview_seq
        for k in [k for k in self.previews if k <= cur - PREVIEW_DISPLAY_KEEP]:
            del self.previews[k]
        for k in [k for k in self.preview_thumbs if k <= cur - PREVIEW_THUMB_KEEP]:
            del self.preview_thumbs[k]
        for k, e in self.previews.items():
            if k <= cur - PREVIEW_LINEAR_KEEP:
                e.lossless = None
                e.linear = None

    def _capture_path(self, target: str, frame_type: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in target).strip() or "untargeted"
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self._frame_counter = getattr(self, "_frame_counter", 0) + 1
        return CAPTURE_DIR / safe / f"{frame_type}_{safe}_{stamp}_{self._frame_counter:04d}.fits"

    async def start_loop(self, exposure_s: float, gain: int, offset: int,
                         binning: int = 1) -> None:
        # AWAIT the previous loop's cancellation before spawning the replacement.
        # Without this, the old task's `except CancelledError: await abort_exposure`
        # could land AFTER the new loop's startexposure and abort the new loop's
        # first frame. Awaiting also lets the old expose release the capture lock,
        # so the new loop's first capture doesn't 409 against a still-tearing-down
        # predecessor.
        old = self._loop_task
        self.stop_loop()
        if old is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await old

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

    async def stop_loop_and_wait(self) -> None:
        """Stop the live loop AND await its cancellation, so the caller (sequence
        start / a single capture) knows the loop's in-flight expose has fully
        released the camera + capture lock before it starts its own — no overlap,
        no 409 against a still-tearing-down predecessor."""
        old = self._loop_task
        self.stop_loop()
        if old is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await old

    @property
    def looping(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    # -------------------------------------------------------- solve & center

    async def solve_and_sync(self, exposure_s: float = 3.0) -> dict:
        """Plate-solve the current pointing and sync the mount to it.

        ONE real-solver path for every backend (P0-1). The old NINA branch called
        NINA's ``/prepared-image/solve``, which HANGS on the live rig and left the
        working ``AstapSolver`` orphaned (review 5/5d). Now NINA mode captures a
        frame exactly like sim/Alpaca, writes it to a temp FITS via ``save_fits``,
        and hands it to the real local solver (ASTAP) with ra/dec/fov hints. Solver
        resolution now happens UP FRONT via ``providers.pick_solver`` (spec §3.4),
        so a rig nothing can trustworthily solve for fails in <1 ms with a clear
        ``DeviceError`` instead of wasting an exposure first."""
        cam: Camera = self.require("camera")
        tel: Telescope = self.require("telescope")
        # Resolver-routed (spec §3.4): honors the user's solve override and the
        # motion-keyed sim-solver guard, and raises a clear DeviceError BEFORE
        # an exposure is wasted when nothing trustworthy can solve.
        from . import providers as _providers
        solver = _providers.pick_solver(self)
        # Pointing hint from the mount -- drives ASTAP's near search and lets a
        # refusing SimSolver fail without inventing a centered solution. The mount
        # reports JNOW on a real Alpaca mount, so bring it back to J2000 (the frame
        # ASTAP solves in and the FITS header records); a no-op for sim/NINA.
        try:
            ra_hint, dec_hint = await tel.get_position()
            if ra_hint is not None:
                ra_hint, dec_hint = await self.from_mount_frame(tel, ra_hint, dec_hint)
        except Exception:
            ra_hint = dec_hint = None
        async with self.exposure_guard("plate solve"):
            frame = await cam.expose(exposure_s, 200, 30, binning=2)
        self.last_frame = frame
        await self._publish_preview(frame)
        # Save the captured frame to a temp FITS for the local solver. Works for
        # NINA too: NinaCamera populates ``frame.data`` (a decoded grayscale copy)
        # which is enough for ASTAP star detection, and save_fits writes the
        # RA/Dec hints into the header. Offloaded so the disk write never freezes
        # the event loop on the Windows target.
        tmp = CAPTURE_DIR / "_solve" / "solve.fits"
        await asyncio.to_thread(
            save_fits, frame, tmp,
            ra_hours=ra_hint, dec_deg=dec_hint, instrument=cam.name)
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
        # ASTAP returns J2000. Sync the mount in the frame IT expects (JNOW for a
        # real Alpaca mount, else unchanged) so a plate-solve sync does not corrupt
        # a JNOW mount's alignment model by ~20 arcmin. The returned dict stays
        # J2000 — the caller's centering error math compares against a J2000 target.
        sync_ra, sync_dec = await self.to_mount_frame(
            tel, result.ra_hours, result.dec_deg)
        await tel.sync(sync_ra, sync_dec)
        bus.log("info", f"solved & synced: RA {result.ra_hours:.4f}h "
                        f"Dec {result.dec_deg:+.3f}° (J2000)", "solve")
        return {"ra_hours": result.ra_hours, "dec_deg": result.dec_deg,
                "solver": solver.name, "pixel_scale": result.pixel_scale_arcsec}

    async def goto_and_center(self, ra_hours: float, dec_deg: float,
                              tolerance_deg: float = 0.02,
                              max_attempts: int = 3,
                              solve_exposure_s: float = 3.0) -> dict:
        """Slew, then iterate solve→sync→re-slew until on target."""
        tel: Telescope = self.require("telescope")
        # Sun-exclusion cone (W1.10) at the MOTION boundary, so every re-slew
        # path -- /api/mount/goto?center, each sequence per-target slew, and
        # meridian_flip (which calls back into goto_and_center) -- inherits it.
        # Checked before unpark/track so a daytime target never even starts.
        self._check_solar(ra_hours, dec_deg)
        # Motion fence (W3.7): snapshot the epoch BEFORE the first await. Each
        # device-committing slew below re-checks it under ``_motion_lock`` and
        # abandons if an abort advanced it. The long solve/center loop also polls
        # it between attempts, so a STOP cancels the loop AND fences a slew that
        # was already mid-flight when the abort landed.
        epoch = self._motion_epoch
        async with self._motion_lock:
            if not self._motion_committed_clean(epoch):
                bus.log("warning", "goto abandoned: aborted before motion", "mount")
                return {"centered": False, "error_arcmin": None,
                        "attempts": 0, "aborted": True}
            if await tel.is_parked():
                await tel.unpark()
            await tel.set_tracking(True)
        last_err = None
        for attempt in range(1, max_attempts + 1):
            bus.publish("mount", action="centering", attempt=attempt)
            # Re-acquire the motion lock per slew and re-check the fence at the
            # pre-dispatch point: a STOP/abort that bumped the epoch (and cancels
            # this task) wins the race even if we were already awaiting here.
            async with self._motion_lock:
                if not self._motion_committed_clean(epoch):
                    bus.log("warning",
                            f"goto re-slew abandoned at attempt {attempt}: aborted",
                            "mount")
                    return {"centered": False,
                            "error_arcmin": (last_err or 0) * 60 if last_err else None,
                            "attempts": attempt - 1, "aborted": True}
                # Slew in the mount's own frame: a JNOW Alpaca mount would
                # otherwise interpret the J2000 target as JNOW and land ~20 arcmin
                # off. Converting inside the loop (not once up front) keeps the
                # apparent place current across a long multi-attempt center; a
                # no-op for sim/NINA. The centering error below stays in J2000.
                slew_ra, slew_dec = await self.to_mount_frame(tel, ra_hours, dec_deg)
                await tel.slew(slew_ra, slew_dec)
            # A plate-solve failure or timeout must DEGRADE to a raw GoTo, not
            # hang or propagate (live bug): the mount has already slewed, so we
            # return the un-centered result with a warning rather than aborting.
            # CancelledError is re-raised so a user/engine abort still stops us.
            try:
                solved = await self.solve_and_sync(solve_exposure_s)
            except asyncio.CancelledError:
                raise
            except (DeviceError, Exception) as e:
                bus.log("warning",
                        f"centering: plate solve failed ({e}); using raw GoTo", "solve")
                return {"centered": False, "error_arcmin": None,
                        "attempts": attempt, "solve_failed": True}
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
        # Flip the guider's calibration for the far side of the pier BEFORE
        # restarting guiding (review 7d). On a real GEM the RA/Dec sense reverses
        # across the meridian, so guiding with the pre-flip calibration runs
        # BACKWARDS (runaway). Best-effort + guarded: a guider that can't flip
        # (sim) or errors must not abort the flip. The guider method itself logs
        # its own success/failure; this call is guarded only so a missing method
        # or an unexpected raise can never break the flip.
        if self.guider and self.guider.connected:
            flip_cal = getattr(self.guider, "flip_calibration", None)
            if callable(flip_cal):
                try:
                    await flip_cal()
                except Exception as e:
                    bus.log("warning",
                            f"meridian flip: guider calibration flip failed: {e}",
                            "sequence")
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
        # safety monitor gets its OWN poller (separate from the 2s status loop) so
        # a slow sensor never starves status (C1-12). Idempotent; bootstrapped here
        # alongside the status poller from every connect path.
        self.ensure_safety_poller()

    async def _status_loop(self) -> None:
        while True:
            try:
                bus.publish("status", **await self.poll_status())
            except Exception:
                pass
            await asyncio.sleep(2.0)

    # ------------------------------------------------------------ safety poller

    def ensure_safety_poller(self) -> None:
        """Spawn the own-cadence SafetyMonitor poller if not already running.
        Idempotent — one task for the whole program (Batch 4b)."""
        if self._safety_task is None or self._safety_task.done():
            self._safety_task = asyncio.create_task(self._safety_loop())

    async def _safety_loop(self) -> None:
        """Poll the SafetyMonitor on its OWN cadence and cache a SafetyReading.

        A read that exceeds ``SAFETY_READ_TIMEOUT_S`` (or raises) is cached as a
        STALE reading — fail-closed: ``is_safe=False, stale=True`` — so the engine
        treats a hung/disconnected sensor as UNSAFE, never as safe (C1-12/C1-15).
        When no monitor is connected the cache is cleared (None) and the loop just
        idles until one appears."""
        while True:
            mon = self.safety
            if mon is None or not getattr(mon, "connected", False):
                self._safety_reading = None
            else:
                prev = self._safety_reading
                try:
                    reading = await asyncio.wait_for(
                        mon.reading(), timeout=SAFETY_READ_TIMEOUT_S)
                except asyncio.CancelledError:
                    raise
                except (asyncio.TimeoutError, Exception) as e:
                    reason = ("safety read timed out"
                              if isinstance(e, asyncio.TimeoutError)
                              else f"safety read failed: {e}")
                    reading = SafetyReading(
                        is_safe=False, reason=reason, source=mon.name, stale=True)
                self._safety_reading = reading
                # emit a 'safety' event only on a verdict change (or first reading)
                # so consumers (engine/UI/alerts) react without polling.
                if (prev is None or prev.is_safe != reading.is_safe
                        or prev.stale != reading.stale):
                    bus.publish("safety", **self._safety_reading_dict(reading))
            await asyncio.sleep(SAFETY_POLL_INTERVAL_S)

    # ----------------------------------------------------------- monitor telemetry

    @staticmethod
    def _cooler_at_target_c() -> float:
        """The shared at-target band — imported from the engine so the Monitor's
        ``at_target`` flag and the engine's cooling-wait gate never drift
        (master plan §A.7). Falls back to 1.0 if the engine isn't importable."""
        try:
            from .sequence.engine import COOLER_AT_TARGET_C
            return float(COOLER_AT_TARGET_C)
        except Exception:
            return 1.0

    def _plan_flip_enabled(self) -> bool:
        """Whether the active sequence plan asks for a meridian flip (a GEM with
        the flip turned OFF near the meridian is a pier-collision risk the
        Monitor warns about). False when there is no plan."""
        eng = self.engine
        plan = getattr(eng, "plan", None) if eng else None
        return bool(plan and getattr(plan, "meridian_flip", False))

    @staticmethod
    def _is_gem(side: str) -> bool:
        """Treat a real east/west pier report as a German-equatorial; ``unknown``
        is not determinable (fork mounts report unknown/none)."""
        return side in ("east", "west")

    async def _compute_meridian(self, tel, ra_hours: float | None) -> dict:
        """The MeridianInfo block. Prefers the device's own value (NINA); for
        sim/Alpaca derives hours-to-flip from the hour angle HA = LST − RA."""
        from .catalog.coords import lst_hours
        meridian: dict[str, Any] = {
            "status": "unknown", "hours_to_flip": None,
            "flip_enabled": self._plan_flip_enabled(), "pier_side": "unknown"}
        try:
            ttf = await tel.time_to_meridian_flip()      # NINA → number; others None
        except Exception:
            ttf = None
        try:
            side = (await tel.pier_side()).value
        except Exception:
            side = "unknown"
        meridian["pier_side"] = side
        if ttf is None and ra_hours is not None:
            # HA = LST − RA, wrapped to [−12, 12]; a GEM on the east side tracking
            # west flips when the target crosses the meridian (HA crosses 0).
            lst = lst_hours(self.site["longitude"])
            ha = ((lst - ra_hours + 12) % 24) - 12
            ttf = -ha
        meridian["flip_enabled"] = self._is_gem(side) and self._plan_flip_enabled()
        if not self._is_gem(side):
            meridian["status"] = "n_a_fork" if side != "unknown" else "unknown"
        elif not self._plan_flip_enabled():
            meridian["status"] = "flip_disabled"          # GEM but plan disabled it
        elif ttf is None:
            meridian["status"] = "unknown"
        elif ttf <= 0:
            meridian["status"] = "due"
            meridian["hours_to_flip"] = round(ttf, 4)
        else:
            meridian["status"] = "counting"
            meridian["hours_to_flip"] = round(ttf, 4)
        return meridian

    async def monitor_snapshot(self) -> dict:
        """One-shot cold-load hydration for the Monitor view (monitor spec §8).
        Non-fatal; mirrors what the WS would push in ≤2s."""
        eng = self.engine
        seq = getattr(eng, "state", None) if eng else None
        if not seq:
            seq = {"state": "idle"}
        guide_recent: list = []
        if self.guider and self.guider.connected:
            try:
                guide_recent = list(getattr(self.guider.stats(), "recent", []) or [])
            except Exception:
                guide_recent = []
        return {
            "sequence": seq,
            "status": await self.poll_status(),
            "preview_id": self.preview_seq or None,
            "guide_recent": guide_recent,
        }

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
        # boot-LED grid that survives a page reload (W1.6): the retained per-role
        # tri-state + a single boot-failure flag, riding the existing 2s WS push.
        out["backend_links"] = self.backend_links()
        out["boot_connect_failed"] = bool(
            self.last_connect_result is not None
            and any(not rr.ok and rr.attempted
                    for rr in self.last_connect_result.results))
        # Per-capability provider resolution (native parity) so the UI can badge
        # every panel ("AF · NINA", "TPPA · native"). resolve_all is already
        # non-raising, but wrap it anyway: a resolution bug must NEVER 500 the 2s
        # status poll — degrade to an explicit "unavailable" row instead.
        try:
            from . import providers as _providers
            out["providers"] = _providers.resolve_all(self)
        except Exception as e:
            out["providers"] = {
                cap: {"kind": "unavailable", "label": "Unavailable",
                      "reason": f"resolution error: {e}"}
                for cap in ("autofocus", "polar_align", "solve")}
        try:
            du = shutil.disk_usage(CAPTURE_DIR)
            free_gb = du.free / 1e9
            out["disk"] = {"free_gb": round(free_gb, 1),
                           "low": free_gb < 10, "critical": free_gb < 1}
        except OSError:
            pass
        # CHEAP safety block: the cached reading from the own-cadence poller (no
        # device I/O here — the poller did it). None when no monitor / not yet read.
        sr = self._safety_reading
        out["safety"] = self._safety_reading_dict(sr) if sr is not None else None
        tel = self.devices.get("telescope")
        if tel and tel.connected:
            ra = dec = None
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
            # Server-computed meridian (Monitor): NINA returns a real number; for
            # sim/Alpaca the hub derives it from the hour angle so the Monitor's
            # flip countdown populates on every backend (monitor spec §6.1).
            try:
                meridian = await self._compute_meridian(tel, ra)
                out["meridian"] = meridian
                # stash so the engine's (sync) ETA can window-gate the flip cost
                # without doing device I/O.
                self.last_meridian = meridian
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
                temp = await cam.get_temperature()
                out["camera"] = {
                    "temperature": temp,
                    "can_cool": cam.can_cool,
                    "has_dew_heater": getattr(cam, "has_dew_heater", False),
                    "width": cam.sensor_width, "height": cam.sensor_height,
                    "max_gain": cam.max_gain,
                }
                # Monitor cooler readout — driven by the per-backend get_cooler()
                # (sim power model, Alpaca coolerpower probe, NINA optional). The
                # at_target band is the single shared COOLER_AT_TARGET_C so it
                # never drifts from the engine's cooling-wait gate.
                getc = getattr(cam, "get_cooler", None)
                if callable(getc):
                    cooler = await getc()
                    if cooler is not None:
                        tgt = cooler.get("target_c")
                        cooler["at_target"] = bool(
                            tgt is not None and temp is not None
                            and abs(temp - tgt) <= self._cooler_at_target_c())
                        cooler.setdefault("can_report_power",
                                          getattr(cam, "can_report_cooler_power", False))
                        out["camera"]["cooler"] = cooler
            except Exception:
                pass
        if self.guider and self.guider.connected:
            out["guider"] = self.guider.stats().__dict__ | {"name": self.guider.name}
        # Additive guide-camera descriptor so ConnectView can show the guiding
        # device with a name + connected state in every backend (not just sim's
        # dedicated guide_camera device).
        gc = self._guide_camera_info()
        if gc is not None:
            out["guide_camera"] = gc
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
