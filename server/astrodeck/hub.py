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

from . import cooling
from .config import config_store, fov_deg, image_scale_arcsec_px, redacted
from .persist import read_json_or, write_json_atomic
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
    FrameMeta,
    auto_levels,
    cloud_score,
    compute_histogram,
    detect_stars,
    display_histogram,
    frame_eccentricity,
    frame_tilt,
    measure_stars,
    save_fits,
    star_flux_median,
    stretch_with,
    to_jpeg,
    to_png,
    to_thumb,
    write_wcs,
)
from .imaging.processing import frame_stats, to_png
from .polar import PolarAlignSession
from .profiles import Profile, ProfileDevice, profiles, resolve_optics
from . import rotation as _rotation

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

#: Guide-cam PREVIEW exposure (``guide_preview_png``), used only when no guider
#: owns the guide camera. Short and hot on purpose: this answers "is the guide
#: field clear / is there dew on the guide scope", so it wants to beat the
#: panel's 2.5s poll, not to be a good guiding sub. A real guide loop, when one
#: exists, always wins — see the ordering in ``guide_preview_png``.
GUIDE_PREVIEW_EXPOSURE_S = 1.0
#: Preview gain WISH, not a hardware fact — clamped to the camera's own reported
#: ceiling before it is sent. An ASI120MM Mini, the commonest ZWO guide camera
#: there is, tops out at 100, and the ZWO SDK RAISES on an out-of-range control
#: value instead of clipping it, so an unclamped 200 would answer "could not
#: deliver a frame" forever on exactly the rigs this preview exists for.
GUIDE_PREVIEW_GAIN = 200
#: How long a preview outcome stays worth publishing on ``status.guide_camera``.
#: The panel polls the image every 2.5 s while it is open, so a note older than
#: this is from a panel nobody is looking at any more — and a reason from five
#: minutes ago describing a camera that has since been reconnected is precisely
#: the kind of confident stale claim this run is about.
GUIDE_PREVIEW_NOTE_TTL_S = 10.0


def _guide_preview_defect(data: np.ndarray, cam_name: str) -> tuple[str, int, int]:
    """``(why this array is not a picture of the guide field, min ADU, max ADU)``.

    Measured on the rig 2026-08-01: ``/api/guide/frame.png`` answered HTTP 200
    with a 223-byte 512x288 PNG (sha acff576d3edc), byte-identical across three
    calls four seconds apart, and nothing about the request in the run log.

    What those bytes prove is exactly one thing: the source array was CONSTANT.
    Re-encoded offline, ``to_png(a, stretch=True, max_width=512)`` returns those
    same 223 bytes for a constant array of 0, of 700 and of 60000 alike, and for
    1920x1080 and 512x288 alike — ``auto_stretch`` collapses on median 0 / MAD 0
    whatever the level, and the resize maps every 16:9-ish shape onto 512x288.
    So the constant's VALUE and the frame's SHAPE are NOT recoverable from the
    response. (The one value the bytes do exclude is full scale: a constant
    65535 stretches to white and encodes as 768 quite different bytes.) The
    first repair read this evidence as "all zero, 1920x1080, therefore a buffer
    nothing wrote"; that was a reconstruction wearing a measurement's clothes,
    and it is retracted here.

    Constant is enough to refuse on, and it is all we get to say. A frame with
    no variation contains no picture whatever produced it, and this function
    cannot see which: a covered sensor whose read noise clips against a black
    level of 0 (see ``_expose_guide_preview`` — ZWO maps the offset argument
    onto ASI_OFFSET, so 0 really does clip), a sensor saturated by daylight, and
    a download that handed back its own allocation all arrive here identical.
    Naming one of them and sending the user to reconnect working hardware is
    #114 rebuilt on the guide panel.

    Deliberately NOT a brightness test: a genuinely faint guide field at low
    gain is a legitimate frame and must still be served. The signal is variance,
    not level.
    """
    a = np.asarray(data)
    if a.ndim != 2 or a.size == 0:
        return (f"{cam_name} returned {a.size} values shaped {a.shape}, which is "
                "not an image"), 0, 0
    lo, hi = int(a.min()), int(a.max())
    if lo == hi:
        return (f"{cam_name} returned a frame in which every one of its "
                f"{a.shape[1]}x{a.shape[0]} pixels reads {lo} — no variation at "
                "all, so there is no image in it. The frame does not say why: a "
                "covered or swamped sensor and a download that handed back an "
                "empty buffer arrive here identical. Uncover the guide scope and "
                "retry; if it stays flat, reconnect the guide camera under "
                "Equipment."), lo, hi
    return "", lo, hi


def _guide_preview_encoded_defect(
        png: bytes, source_name: str) -> tuple[str, tuple[int, int] | None]:
    """``(why these encoded bytes are not a picture, the levels we saw or None)``.

    The camera branch judges ADU before encoding; a guider hands back a PNG it
    encoded itself, so the same question has to be asked one step later. Review
    of the first repair built a fake guider whose ``guide_frame()`` returned an
    all-black PNG: the hub served it, published ``preview_ok: True`` and wrote
    no log line — the same black rectangle, surviving untouched on every rig
    that runs PHD2, NINA, the sim or the native guider, now with a positive
    claim on top of it.

    Failing to DECODE is a fact about us, not about the image, so that returns
    ``None`` levels and no refusal: the caller serves the bytes (a browser may
    well render what Pillow would not open) and simply declines to claim a frame
    it never looked at.

    The levels are the guider's already-stretched 8-bit display values, not ADU
    — enough to answer "is this a picture", which is the only question here.
    """
    try:
        import io as _io

        from PIL import Image
        with Image.open(_io.BytesIO(png)) as im:
            lo, hi = im.convert("L").getextrema()
    except Exception:  # noqa: BLE001 — our blindness is not the guider's defect
        return "", None
    if lo == hi:
        return (f"{source_name} returned an image in which every pixel reads "
                f"{lo} — no variation at all, so there is no guide field in it. "
                f"This panel only forwards {source_name}'s own view, so what is "
                f"wrong is visible in {source_name}, not here."), (lo, hi)
    return "", (lo, hi)


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


@dataclass(frozen=True)
class _WcsJob:
    """One queued per-frame-WCS stamp: an already-closed local FITS plus the
    pointing/quality context captured at save time (so a config change or a
    mount slew between enqueue and solve can't retroactively alter the hints)."""

    path: Path
    ra: float | None
    dec: float | None
    fov_deg: float | None
    star_count: int | None


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
        # Live View (NOV-1): the single EAA running-mean accumulator, non-None while
        # armed. Fed each raw linear sub in _publish_preview; None = feature off.
        self.live_stacker = None
        # Bahtinov focus aid (NOV-12): None = off; {"tol_px","invert"} = armed. When
        # armed, each raw linear sub gets an additive preview.bahtinov analysis.
        self.bahtinov: dict | None = None
        # the sequence engine registers itself so poll_status can report the
        # active plan's meridian_flip setting without importing the engine.
        self.engine = None
        # last meridian dict from poll_status, so the engine's (sync) ETA can
        # window-gate the flip cost without device I/O.
        self.last_meridian: dict | None = None
        self._loop_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        # --- per-frame WCS stamping (per-frame-wcs spec §2.1) -------------------
        # Saved lights are handed to a SINGLE background consumer so a 2-10 s
        # ASTAP solve never sits on the capture hot path. The queue is created
        # lazily on the first enqueue (feature is OFF by default => neither the
        # queue nor the task ever exists), trimmed drop-oldest at enqueue time,
        # and the task is cancelled in _teardown alongside the capture loop.
        self._wcs_queue: asyncio.Queue | None = None
        self._wcs_task: asyncio.Task | None = None
        # log-once latch for the drop-oldest notice; cleared when the backlog
        # drains, so a later backlog episode is reported again (not spammed).
        self._wcs_drop_logged = False
        # --- safety monitor (Batch 4b) -----------------------------------------
        # own-cadence poller task + the latest CACHED SafetyReading. safety_reading()
        # always returns this cache (NEVER an inline is_safe()), so the 2s status
        # loop and the engine gate read it for free. None until the first poll.
        self._safety_task: asyncio.Task | None = None
        self._safety_reading: SafetyReading | None = None
        # last (is_safe, stale, reason) actually PUBLISHED on the bus, so the two
        # producers of a safety verdict (this poller + the engine's debounced
        # _on_unsafe) can't announce the same trip twice (UX #33).
        self._last_safety_key: tuple | None = None
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
        # the RigSpec this rig was actually connected with, retained so
        # ``capture_profile`` can write down HOW each role connects. Nothing else
        # knows: a live native device object carries its driver's own backend name
        # and its display name, but not the serial port / driver_id / dev_num it
        # was opened on. Held PRE-driver-resolution so a captured profile keeps
        # the symbolic ``driver_id`` (edit the driver in Equipment and the profile
        # follows) instead of freezing tonight's host/port. None until the first
        # RigSpec connect and after any teardown.
        self._last_rigspec: "RigSpec | None" = None
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
        # --- cooler warm-down ramp (2026-08-04) ---------------------------------
        # The background task that walks the cooler setpoint up to ambient before
        # switching the TEC off, and the state dict the Capture screen renders.
        # The task lives on the HUB, not on the sequence engine, on purpose: the
        # unattended ``abort_park_warm`` path fires when something is already
        # wrong, so it must be able to START the warm and walk away — a wind-down
        # that waited ten minutes for a ramp would delay the park and the roof
        # close. _warm_lock serialises start/cancel so two warms cannot race and
        # a "Cool" pressed mid-ramp wins cleanly instead of being overwritten by
        # the next scheduled setpoint step.
        self._warm_task: asyncio.Task | None = None
        self._warm_state: dict | None = None
        self._warm_lock: asyncio.Lock = asyncio.Lock()
        # --- guide-cam preview (guide_preview_png) ------------------------------
        # The exposure in flight, shared between overlapping callers: the panel
        # swaps a cache-busted <img src> every 2.5s whether or not the previous
        # one has landed, and a preview that takes longer than that would
        # otherwise start a second exposure on a camera already exposing.
        self._guide_preview_task: asyncio.Task | None = None
        # WHICH camera object that task is exposing. A reconnect replaces the
        # device object, and joining an exposure started on the old (now closed)
        # handle would answer for a camera that no longer exists.
        self._guide_preview_task_cam: Any = None
        # (reason, monotonic time) of the last preview outcome, for status. An
        # <img> cannot read a 404 body, so the reason has to travel some other
        # way or the panel is back to saying "unavailable" about everything.
        self._guide_preview_note: tuple[str, float] = ("", 0.0)
        # (were the pixels CHECKED, monotonic time) of the last preview that
        # returned a picture; None once nothing recent has. THIRD state, and the
        # reason it exists: a refusal publishes a reason and a success published
        # nothing, so a delivered frame and a camera nobody had asked yet looked
        # identical on status — which is how an all-black rectangle passed for a
        # preview (2026-08-01).
        #
        # The bool is a fourth outcome the first version of this collapsed back
        # into the third: bytes we forwarded but could not decode published
        # exactly what an unasked camera published. That is the same defect one
        # level down — "we looked and made no claim" and "nobody looked" are
        # different facts, and only one of them is a reason to distrust the
        # picture on screen.
        #
        # The name rides along because it is the SOURCE's name, and the source
        # order here is the inverse of the one ``_guide_camera_info`` uses for
        # ``status.guide_camera.name``: that prefers the guide-camera DEVICE,
        # this prefers the connected GUIDER. On a rig with both — which is every
        # sim rig — a UI that qualified the picture using the status name said
        # "ZWO ASI sent bytes we could not decode" about bytes PHD2 sent and a
        # camera nobody asked. Naming the wrong instrument is the same defect as
        # naming a state nobody measured.
        self._guide_preview_frame: tuple[bool, float, str] | None = None
        # the outcome we last wrote to the run log, so the 2.5s poll logs one
        # line per CHANGE instead of burying the log or (as on 2026-08-01)
        # leaving no trace of the exposure at all.
        self._guide_preview_logged: str | None = None
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
        # --- learned camera EGAIN (measured e-/ADU per gain setting) ------------
        # In-memory mirror of the per-profile egain store so the 2s status poll
        # never touches disk. A DRIVER-REPORTED egain always wins; this map is
        # consulted ONLY when the camera reports 0.0, and only on an EXACT gain
        # match (conversion gain is not linear across an HCG transition, so v1
        # deliberately does not interpolate).
        self._egain_learned: dict[int, float] = {}
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
        self._seed_filter_config()  # UX-05: user slot names over hardware letters
        self._seed_egain_config()   # learned e-/ADU (driver value still wins)
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

    def _candidate_guiders(self) -> list:
        """The guiders CONSTRUCTIBLE on the currently-connected rig: the active
        one plus each open backend session's ``native_guider()`` (deduped by
        identity). Used to honor the guide-provider override at guiding start
        without a reconnect. Cheap: ``native_guider()`` only INSTANTIATES a
        guider (no I/O); the connect happens later, only for the one we pick."""
        cands: list = []
        if self.guider is not None:
            cands.append(self.guider)
        res = self.last_connect_result
        sessions = getattr(res, "sessions", None) if res is not None else None
        if isinstance(sessions, dict):
            for session in sessions.values():
                get = getattr(session, "native_guider", None)
                if not callable(get):
                    continue
                try:
                    g = get()
                except Exception:
                    g = None
                if g is not None and all(g is not c for c in cands):
                    cands.append(g)
        return cands

    async def select_guide_provider(self) -> None:
        """Reconcile ``self.guider`` with the guide-provider override for the
        NEXT guiding start (fix round C1). Chooses among the guiders
        CONSTRUCTIBLE on the connected rig; an override whose family is not
        constructible DEGRADES to the connect-time guider (never a crash). NINA
        rigs are left untouched (D5). Never hot-swaps a guider that is CURRENTLY
        guiding — the switch takes effect only when guiding is (re)started."""
        from . import providers as _providers
        current = self.guider
        if current is None:
            return
        if self.nina_client is not None:
            return                                   # D5: NINA owns its guiding
        # never yank a running guider out from under a live session.
        try:
            if await current.is_active():
                return
        except Exception:
            pass
        want = _providers.guide_override_family(self)   # auto/backend/astrodeck/sim
        candidates = self._candidate_guiders()
        if want == "backend":
            target = "backend"
        elif want in ("astrodeck", "sim"):
            target = "native"
        else:  # auto: native-first, the resolver's own auto preference
            target = "native" if any(
                _providers.actual_guide_family(g) == "native" for g in candidates
            ) else "backend"
        if _providers.actual_guide_family(current) == target:
            return                                   # already serving the target
        for g in candidates:
            if _providers.actual_guide_family(g) == target and g is not current:
                try:
                    await g.connect()                # idempotent; may refuse
                except Exception as exc:  # noqa: BLE001 - degrade, keep current
                    bus.log("warning",
                            f"guide provider '{want}' unavailable ({exc}); "
                            f"keeping {getattr(current, 'name', 'current guider')}",
                            "guide")
                    return
                self.guider = g
                if current is not g:
                    try:
                        await current.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                bus.log("info",
                        f"guide provider -> {getattr(g, 'name', 'guider')} "
                        f"(override: {want})", "guide")
                return
        # target family not constructible on this rig: degrade — keep current.

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
        # retained BEFORE resolution, so a captured profile keeps the symbolic
        # driver_id rather than tonight's resolved address (see _last_rigspec).
        self._last_rigspec = spec
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
        # Boot is UNATTENDED, so an all-simulator rig has to announce itself
        # here. A profile with no device rows resolves every role to the sim
        # backend — including a SafetyMonitor that reports safe from nothing —
        # while the console shows a connected rig. Saving such a profile is now
        # prevented at the source (capture_profile), but one already on disk
        # still activates, and silence is what made that dangerous.
        with contextlib.suppress(Exception):
            prof = await asyncio.to_thread(profiles.get, pid)
            if not prof.devices and (prof.primary_backend or
                                     prof._derived_primary()) == "sim":
                bus.log("warning",
                        f"profile '{prof.name}' has no equipment saved in it, so "
                        f"this rig is coming up SIMULATED — nothing you see on it "
                        f"is your hardware. Connect the rig on Equipment and save "
                        f"the profile again.", "hub")
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
        self._seed_filter_config()  # UX-05: user slot names over hardware letters
        self._seed_egain_config()   # learned e-/ADU (driver value still wins)
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

    #: Roles served by an ENGINE handle on the hub instead of a ``self.devices``
    #: device object: role -> hub attribute holding that engine. ``guider`` is the
    #: only one today — the orchestrator sources it from the guider-role session's
    #: ``native_guider()`` (AstroDeck native / NinaGuider / PHD2) and it is NEVER
    #: entered in ``self.devices`` (``devices/sim.py`` has no "guider" key at all).
    #: The engine still answers ``connected`` exactly like a device does.
    _ROLE_ENGINE_ATTR: dict[str, str] = {"guider": "guider"}

    def _role_live_connected(self, role: str) -> bool:
        """Is ``role`` LIVE right now?

        A device role reads its device's ``connected``. A role served by an
        ENGINE (``_ROLE_ENGINE_ATTR``: ``guider`` -> ``self.guider``) reads the
        engine, because it has no ``self.devices`` entry to read — a device-only
        join reported a perfectly healthy DEFAULT native guider as a dropped link
        on every sim/native rig (UX #52: an orange GUIDING · DEGRADED with a null
        ``error``, so no cause was even shown)."""
        dev = self.devices.get(role)
        if dev is not None:
            return bool(getattr(dev, "connected", False))
        attr = self._ROLE_ENGINE_ATTR.get(role)
        engine = getattr(self, attr, None) if attr else None
        return bool(engine is not None and getattr(engine, "connected", False))

    def backend_links(self) -> list[dict]:
        """The per-role boot-LED surface (W1.6): the retained ConnectResult's
        tri-state ``RoleResult`` joined with each role's LIVE ``connected`` state
        (:meth:`_role_live_connected` — device OR engine, see UX #52).

        ``[]`` when no RigSpec connect has happened (the legacy connect_* paths
        don't populate ``last_connect_result``)."""
        res = self.last_connect_result
        if res is None:
            return []
        out: list[dict] = []
        for rr in res.results:
            out.append({
                "role": rr.role,
                "ok": rr.ok,
                "error": rr.error,
                "attempted": rr.attempted,
                "connected": self._role_live_connected(rr.role),
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
        self.stop_wcs_worker()              # per-frame-wcs R1: never outlive the hub
        self.live_stacker = None            # NOV-1: release the accumulator on teardown
        self.bahtinov = None                # NOV-12: disarm the focus aid on teardown
        # Warm ramp: FINALIZE (cooler off), do not merely cancel. The devices are
        # about to be disconnected a few lines below, so a cancelled ramp would
        # leave the camera holding whatever mid-ramp setpoint it happened to be
        # on — a temperature nobody chose, on a camera nothing is talking to any
        # more. Switching off IS the warm's intended destination, so completing
        # it early is the only defined end state available here. Best-effort and
        # bounded inside cancel_warm; a teardown must not be blockable by a
        # wedged cooler.
        with contextlib.suppress(Exception):
            await self.cancel_warm("the rig is disconnecting", finalize=True)
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
        self._last_rigspec = None
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

    @property
    def calibrator(self):
        """The connected CoverCalibrator (flat panel + optional cover), or None."""
        return self.devices.get("covercalibrator")

    async def calibrator_on(self, brightness: int) -> None:
        await self.require("covercalibrator").calibrator_on(int(brightness))

    async def calibrator_off(self) -> None:
        await self.require("covercalibrator").calibrator_off()

    async def open_cover(self) -> None:
        await self.require("covercalibrator").open_cover()

    async def close_cover(self) -> None:
        await self.require("covercalibrator").close_cover()

    async def calibrator_status(self) -> dict | None:
        """Live flat-panel state, or None when no CoverCalibrator is connected."""
        cc = self.calibrator
        if cc is None or not cc.connected:
            return None
        return {"state": await cc.get_calibrator_state(),
                "brightness": await cc.get_brightness(),
                "max_brightness": cc.max_brightness,
                "has_cover": cc.has_cover,
                "cover_state": (await cc.get_cover_state()).value}

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

    async def guide_preview_png(self) -> tuple[bytes | None, str]:
        """A PNG of the guide camera's current view, or (None, why-not).

        Why this is not just ``self.guider.guide_frame()``: on 2026-07-31 the
        Capture screen's "Guide cam" toggle did nothing on a rig whose ZWO ASI
        guide camera was connected and idle. The route gated on ``hub.guider``,
        and ``hub.guider`` is None on a native rig unless a profile explicitly
        overrides the ``guider`` role — ``NativeBackend.roles`` deliberately
        excludes it (the native engine is offered through ``native_guider()``,
        and the Equipment screen even tells the user there is "no guider device
        to assign here"). So the one device that could have answered was sitting
        there connected and was never asked.

        Order matters. The guider first: while a guide loop is running its last
        looped frame IS the truth, and grabbing our own exposure would fight it
        for the sensor. Only when there is no guider do we drive the dedicated
        guide camera ourselves.

        Never raises — the caller turns (None, reason) into a 404, never a 500.
        That promise is kept by the guard below, not by hope: before it,
        ``_expose_guide_preview`` guarded only ``expose()`` and ``to_png()``, so a
        camera adapter that answered ``None`` instead of raising (measured here:
        ``AttributeError: 'NoneType' object has no attribute 'data'``) escaped as
        a 500 with no log line. A crash and a camera nobody asked are not allowed
        to look the same.

        That crash is NOT a candidate for the 2026-08-01 rig defect, and an
        earlier draft of this docstring said it was, on the grounds that both
        leave ``status.guide_camera`` carrying exactly ``{name, connected}``. So
        did a SUCCESSFUL delivery on that build — it published a reason only when
        one was live — so the signature separates nothing, and the measurement
        (HTTP 200, 223 bytes) rules a crash out anyway, since a crash returns 500
        with no body. The guard earns its place by making good a docstring
        promise that demonstrably did not hold; it does not need a rig defect to
        its name, and giving it one on a coincidence is exactly the reasoning
        that cost #110 three wrong diagnoses.

        Every exit records its outcome for ``status.guide_camera``, because a 404
        body reaches only the code that reads it and the panel reads an ``<img>``.
        FOUR outcomes, published as three keys and a silence:
        ``preview_reason`` = a refusal we can explain; ``preview_ok: True`` = a
        frame whose pixels we checked; ``preview_ok: False`` = bytes we forwarded
        without being able to inspect them; neither key = nobody has asked this
        camera recently. ``preview_ok`` is deliberately narrower than "we
        returned bytes" — publishing a verdict on pixels nobody inspected would
        be the same defect pointing the other way. Alongside the verdict rides
        ``preview_source``: the name of the device that ACTUALLY answered, which
        is not always the one ``status.guide_camera.name`` carries (see
        ``_guide_camera_info`` — the two source orders are inverses).
        """
        try:
            png, reason, verified, src = await self._guide_preview_source()
        except Exception as exc:  # noqa: BLE001 — the promise above is the point
            src = self._guide_preview_source_name()
            png, reason, verified = None, (
                f"the server failed while getting a frame from "
                f"{src or 'the guide camera'}: {exc}"), False
            self._log_guide_preview(f"crash:{exc}", "warning",
                                    f"guide preview: {reason}")
        now = time.monotonic()
        self._guide_preview_note = (reason, now)
        # Set on EVERY delivery, cleared on every non-delivery. Latching it only
        # on success would let a checked frame from 9s ago vouch for the
        # truncated bytes the panel is showing now — a stale claim inside the
        # TTL, which is the shape of bug this whole slice removes.
        self._guide_preview_frame = (verified, now, src) if png else None
        return png, reason

    def _guide_preview_source_name(self) -> str:
        """Which device ``_guide_preview_source`` would ask right now, by name.

        ONE copy of the source order, because the crash path needs the name of a
        device it never got to and the two orderings in this file already differ
        on purpose (``_guide_camera_info`` prefers the guide-camera device; the
        preview prefers a connected guider). A third hand-rolled copy is how the
        orderings drift apart without anything failing loudly.

        Guarded throughout: reading a name off a device that has just failed is
        not the place to acquire a second way to raise. "" when nothing answers.
        """
        try:
            g = self.guider
            if g is not None and getattr(g, "connected", False):
                return str(getattr(g, "name", "") or "")
            return str(getattr(self.devices.get("guide_camera"), "name", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    async def _guide_preview_source(self) -> tuple[bytes | None, str, bool, str]:
        """``(png, refusal, were the pixels checked, who answered)`` for
        ``guide_preview_png``, which owns the note. The third element exists
        because only some sources can be inspected, and a claim is only allowed
        where one was.

        The fourth is the name of the device this function CHOSE. It is returned
        rather than re-derived by the caller because ``status.guide_camera.name``
        is chosen by the opposite rule, so on any rig carrying both a guider and
        a guide camera the two names disagree — and the state that most needs a
        name (``preview_ok: False``, reachable only from the guider branch below)
        is exactly where they disagree."""
        g = self.guider
        if g is not None and getattr(g, "connected", False):
            try:
                png = await g.guide_frame()
            except Exception:  # noqa: BLE001 — a preview must not 500 the panel
                png = None
            if not png:
                reason = f"{g.name} is connected but exposes no image"
                self._log_guide_preview(f"guider-empty:{g.name}", "warning",
                                        f"guide preview: {reason}")
                return None, reason, False, g.name
            # The guider path used to end at ``if png: return png`` — no check,
            # no log line — so an all-black guide frame reached the panel exactly
            # as the camera's empty buffer did. Same test, one step later.
            defect, levels = _guide_preview_encoded_defect(png, g.name)
            if defect:
                self._log_guide_preview(f"guider-flat:{g.name}", "warning",
                                        f"guide preview: {defect}")
                return None, defect, True, g.name
            if levels is None:
                self._log_guide_preview(
                    f"guider-unchecked:{g.name}", "info",
                    f"guide preview: {len(png)} bytes from {g.name}, served "
                    "unchecked — they would not decode here, so whether they "
                    "carry an image is not something this server knows")
                return png, "", False, g.name
            self._log_guide_preview(
                f"guider-ok:{g.name}", "info",
                f"guide preview: {len(png)} bytes from {g.name}, display levels "
                f"{levels[0]}..{levels[1]}")
            return png, "", True, g.name

        cam = self.devices.get("guide_camera")
        if cam is None or not getattr(cam, "connected", False):
            return None, ("no guide camera and no guider are connected — assign "
                          "a guide camera under Equipment, or start PHD2/NINA"), False, ""
        # One physical camera filling both roles (the OAG-style fallback
        # native_guider() also allows). Exposing it here would take the sensor
        # out from under the imaging train mid-sequence.
        if cam is self.devices.get("camera"):
            return None, (f"{cam.name} is also the imaging camera — its frames "
                          "show in the capture preview, not here"), False, cam.name
        # NO busy_label gate here, deliberately. busy_label is a HUB-WIDE label
        # for the IMAGING train (goto/solve/autofocus/capture/looping), and this
        # line is only reached once we know the guide camera is a different
        # device from the imaging one — so refusing on it said "ZWO ASI is busy
        # (capturing)" about a camera that was idle, and disabled the panel at
        # the one moment it is wanted: checking the guide field for dew or cloud
        # is something you do WHILE a sequence runs. Nothing busy_label can ever
        # report refers to this camera. The only contender for this sensor is
        # another preview, and that is what the single-flight below is for.
        task = self._guide_preview_task
        if task is None or task.done() or self._guide_preview_task_cam is not cam:
            task = asyncio.create_task(self._expose_guide_preview(cam))
            self._guide_preview_task = task
            self._guide_preview_task_cam = cam
        # shield: the panel re-requests every 2.5s and a 1s exposure plus a
        # full-sensor USB readout plus a PNG encode can outlast that, so a second
        # request must JOIN the exposure in flight rather than call start_exposure
        # on a camera that is already exposing (two read_frame calls racing one
        # buffer = torn frames, which the panel would render as the same
        # uninformative "unavailable"). Shielded so a client that navigates away
        # mid-frame cancels its own wait, never the exposure.
        return await asyncio.shield(task)

    async def _expose_guide_preview(self, cam) -> tuple[bytes | None, str, bool, str]:
        """ONE preview exposure, encoded. Separate coroutine so overlapping
        callers can share a single in-flight frame. Never raises. Returns
        ``(png, refusal, checked, who answered)``; ``checked`` is always True on
        the delivering exit because this branch has the raw ADU and always looks
        at them, and the name is always this camera's — it is returned anyway so
        the two source branches answer the same shape."""
        # Clamp to the ceiling the device itself reported (Camera.max_gain, set
        # from the SDK's gain_range at connect). 0 means "this backend does not
        # report a ceiling" — NINA without GainMax, an Alpaca camera without
        # gainmax — and inventing one there would be its own guess, so the wish
        # goes through unchanged and the driver gets to refuse it by name.
        ceiling = int(getattr(cam, "max_gain", 0) or 0)
        gain = min(GUIDE_PREVIEW_GAIN, ceiling) if ceiling > 0 else GUIDE_PREVIEW_GAIN
        # offset 0 is the black level, not a no-op: ZWO writes this argument
        # straight to ASI_OFFSET, so a covered sensor's read noise clips against
        # zero and comes back genuinely flat. That is a real way to reach the
        # refusal below WITHOUT anything being broken, which is why the refusal
        # does not get to say what went wrong. The requested settings go in the
        # log line so the next reader can tell that case from the others.
        try:
            frame = await cam.expose(GUIDE_PREVIEW_EXPOSURE_S, gain, 0, binning=1)
        except Exception as exc:  # noqa: BLE001
            # NAME the failure. "unavailable" was the whole complaint: the panel
            # could not distinguish a camera that refused from one nobody asked.
            reason = f"{cam.name} could not deliver a frame: {exc}"
            self._log_guide_preview(f"error:{exc}", "warning",
                                    f"guide preview: {reason}")
            return None, reason, False, cam.name
        # A SUCCESSFUL call is not the same thing as a frame. On 2026-08-01 this
        # branch encoded a CONSTANT array (see _guide_preview_defect for what the
        # served bytes do and do not pin down) and served it as HTTP 200 — a black
        # rectangle with no explanation, indistinguishable from the "nothing
        # happens" the user reported in the first place.
        data = np.asarray(frame.data)
        defect, lo, hi = _guide_preview_defect(data, cam.name)
        if defect:
            self._log_guide_preview(
                "defect", "warning",
                f"guide preview: {defect} (requested {GUIDE_PREVIEW_EXPOSURE_S}s "
                f"at gain {gain}, offset 0)")
            return None, defect, True, cam.name
        try:
            png = to_png(data, stretch=True, max_width=512)
        except Exception as exc:  # noqa: BLE001
            reason = f"{cam.name} returned a frame that would not encode: {exc}"
            self._log_guide_preview("encode", "warning", f"guide preview: {reason}")
            return None, reason, False, cam.name
        self._log_guide_preview(
            "ok", "info",
            f"guide preview: {cam.name} {data.shape[1]}x{data.shape[0]}, "
            f"{lo}..{hi} ADU at gain {gain}")
        return png, "", True, cam.name

    def _log_guide_preview(self, key: str, level: str, message: str) -> None:
        """Log a preview outcome ONCE per change of outcome.

        The panel re-requests every 2.5s, so logging every attempt would bury
        the run log — but logging NONE of them is why the all-black preview of
        2026-08-01 left no trace whatsoever (no exposure, no error, nothing) and
        had to be diagnosed from the response bytes. One line per transition is
        what answers "was the camera even asked?"."""
        if key == self._guide_preview_logged:
            return
        self._guide_preview_logged = key
        bus.log(level, message, "guide")

    def guide_preview_verdict(self) -> bool | None:
        """What the last recent preview delivery was worth, in three values.

        ``True``  — a picture whose pixels we decoded and found to vary.
        ``False`` — bytes we forwarded and could NOT inspect, so we make no claim
                    about them; the browser may render what Pillow would not open.
        ``None``  — nobody has asked this camera recently (or the last answer is
                    older than the note TTL, or it was a refusal, which travels as
                    ``preview_reason`` instead).

        Three-valued rather than a bool because the bool version collapsed the
        middle case into the last one: "we looked and could not tell" published
        exactly what "nobody looked" published, and a panel showing a rectangle
        could not tell which it was holding. Success originally published nothing
        at all, which is how an unexplained black frame reached the screen on
        2026-08-01 with no way to check it against anything."""
        seen = self._guide_preview_frame
        if seen is None or time.monotonic() - seen[1] > GUIDE_PREVIEW_NOTE_TTL_S:
            return None
        return seen[0]

    def guide_preview_source(self) -> str:
        """Which device produced the frame ``guide_preview_verdict`` is about.

        Recorded at delivery, not derived at read time, and NOT the same thing as
        ``status.guide_camera.name``: that one comes from ``_guide_camera_info``,
        which prefers the guide-camera DEVICE, while the preview prefers a
        connected GUIDER. Every sim rig has both, so the UI sentence that names
        the instrument it will not vouch for — ``preview_ok: False``, which only
        the guider branch can reach — named the wrong one until this existed.

        "" when nothing recent has delivered, so the caller must not print it
        bare; the copy it feeds falls back to a phrase that names no device."""
        seen = self._guide_preview_frame
        if seen is None or time.monotonic() - seen[1] > GUIDE_PREVIEW_NOTE_TTL_S:
            return ""
        return seen[2]

    def guide_preview_note(self) -> str:
        """The most recent preview refusal, while it is still current (or "").

        Published on status so the panel can say WHICH nothing this is. It
        expires: an old reason describing a camera that has since been fixed is
        the confident stale claim the whole run is against."""
        reason, at = self._guide_preview_note
        if not reason or time.monotonic() - at > GUIDE_PREVIEW_NOTE_TTL_S:
            return ""
        return reason

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

    def publish_safety(self, payload: dict) -> bool:
        """THE single seam for publishing a ``safety`` verdict edge (UX #33).

        Two producers used to publish the same trip: the hub's own-cadence poller
        (on the verdict edge) and the engine's ``_on_unsafe`` (after its debounce).
        Each event raises a sticky UNSAFE toast, so one rain trip produced two
        identical notices — and the engine's payload carried only
        ``{is_safe, reason, action, stale}``, so applying it CLOBBERED the sensor
        name and detail readouts the poller's payload had put in the UI's safety
        state.

        This method fixes both: the cached reading fills in any field the caller
        omitted (never a partial overwrite), and a payload whose
        ``(is_safe, stale, reason)`` matches the last published one is SUPPRESSED
        — the second announcement of a verdict nobody's changed. A genuinely new
        reason (e.g. the engine's no-progress watchdog) always publishes.

        Returns True when the event was published."""
        sr = self._safety_reading
        base = self._safety_reading_dict(sr) if sr is not None else {}
        merged = {**base, **{k: v for k, v in payload.items() if v is not None}}
        merged.setdefault("is_safe", payload.get("is_safe"))
        merged.setdefault("reason", "")
        merged.setdefault("stale", False)
        merged.setdefault("ts", time.time())
        key = (bool(merged.get("is_safe")), bool(merged.get("stale")),
               str(merged.get("reason") or ""))
        if key == self._last_safety_key:
            return False
        self._last_safety_key = key
        bus.publish("safety", **merged)
        return True

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
        per-active-profile override wins at READ time and never stomps global.

        This is the ONE profile-aware readout in the whole /api/config payload,
        so anything that wants the value the rig is really using has to come
        through here — which is why the return dict below now carries every
        ``Optics`` field, not just the ones that feed the image-scale maths."""
        o = resolve_optics(self._active_profile())
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
            "telescope_name": o.telescope_name,
            "pixel_size_um": px,
            "sensor_width_px": int(w),
            "sensor_height_px": int(h),
            # These two rode INSIDE the profile-swapped Optics block but never
            # came back out of this function, so every caller that wanted the
            # value actually in force had to re-read GLOBAL config and got the
            # LOSING layer. The native guider did exactly that with
            # guide_focal_length_mm. No camera fallback applies to either (a
            # camera knows nothing about the guide scope, and auto_from_camera is
            # a preference, not a measurement), so they pass through verbatim.
            "guide_focal_length_mm": o.guide_focal_length_mm,
            "auto_from_camera": o.auto_from_camera,
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
            bus.log("warning", f"could not push site to mount: {type(e).__name__}", "config")

    async def read_site_from_mount(self) -> dict:
        """Best-effort READ-BACK of the mount's GPS fix (site lat/lon/elevation)
        from a connected Alpaca telescope — the first read on the site<->mount
        channel (push_site_to_mount is push-only). ASSIST ONLY: the result fills
        the Settings form draft; nothing is persisted here.

        Always returns a dict; NEVER raises. ``available: false`` with a human
        ``detail`` when: no mount connected, the mount is not Alpaca-backed (no
        ``_get``), any property read fails, the mount reports exactly (0.0, 0.0)
        (unset-GPS sentinel), or any value is non-finite (NaN/inf) or out of the
        Site model's ranges (lat +-90, lon +-180, elevation -430..9000 — some
        mounts return junk like 99.0/181.0 when unset). Logs OUTCOME ONLY, never
        coordinate values (spec §8)."""
        import math
        tel = self.devices.get("telescope")
        if not (tel and tel.connected):
            return {"available": False, "detail": "No mount connected"}
        get = getattr(tel, "_get", None)
        if get is None:
            return {"available": False,
                    "detail": "Mount does not support GPS read-back"}
        try:
            lat = float(await get("sitelatitude"))
            lon = float(await get("sitelongitude"))
            elev = float(await get("siteelevation"))
        except Exception:
            bus.log("warning", "could not read site from mount", "config")
            return {"available": False,
                    "detail": "Could not read GPS from mount"}
        if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(elev)):
            return {"available": False,
                    "detail": "Mount returned invalid (non-finite) coordinates"}
        if lat == 0.0 and lon == 0.0:
            return {"available": False,
                    "detail": "Mount reports 0,0 — GPS likely unset"}
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0
                and -430.0 <= elev <= 9000.0):
            return {"available": False,
                    "detail": "Mount returned out-of-range coordinates"}
        bus.log("info", "read observing site from mount", "config")
        return {"available": True, "latitude": lat, "longitude": lon,
                "elevation_m": elev}

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
                            ("filter_offsets", "focusing"),
                            ("capture", "capturing"), ("looping", "capturing"),
                            ("egain", "capturing")):
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
        if cam and cam.connected:
            # BOTH the gate and the write target used to be GLOBAL config, under
            # a profile whose own optics block shadows global on every read (see
            # effective_optics). On a rig with a profile optics override that
            # meant: the profile's own auto_from_camera flag was never consulted,
            # the camera-seeded numbers landed in a block nothing reads, and the
            # override then hid them — the user watched the Optics panel fill in
            # from the camera and not one of those values reached the rig.
            #
            # Resolve against the profile BEING CONNECTED (``p``), NOT
            # ``_active_profile()``: set_active_profile is still several lines
            # below us, so the cached active profile here is the PREVIOUS rig's
            # and would seed this camera's pixels into the wrong layer.
            o = resolve_optics(p)
            if o.auto_from_camera:
                new_optics = o.model_copy(update={
                    "pixel_size_um": o.pixel_size_um or getattr(cam, "pixel_size_um", 0.0),
                    "sensor_width_px": o.sensor_width_px or getattr(cam, "sensor_width", 0),
                    "sensor_height_px": o.sensor_height_px or getattr(cam, "sensor_height", 0),
                })
                # offload the blocking disk write (with its time.sleep retry) so
                # it never freezes the event loop on the Windows target.
                if p.optics is not None:
                    # The profile is the layer that WINS, so the seed has to land
                    # there or the very next read discards it. Drop the cache too:
                    # _profile_cache may still hold the pre-write copy of ``p``.
                    p.optics = new_optics
                    await asyncio.to_thread(profiles.save, p)
                    self.invalidate_profile_cache()
                else:
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

    @staticmethod
    def _capturable_extra(conn: "ConnSpec") -> dict:
        """``conn.extra`` reduced to what a PROFILE may hold: JSON-able values
        only, minus the derived ``name`` key (``Profile.to_rigspec`` folds the
        display name in there on the way out; the row carries it in its own
        field, so writing it back would duplicate it and let the two disagree).

        A runtime injection like NINA's ``build_rig`` callable lives in ``extra``
        and must never reach disk — dropping non-serializable values here is what
        keeps ``profiles.save`` from raising on a bridged rig."""
        out: dict = {}
        for k, v in (conn.extra or {}).items():
            if k == "name":
                continue
            try:
                json.dumps(v)
            except (TypeError, ValueError):
                continue
            out[k] = v
        return out

    def _capture_from_rigspec(self, spec: "RigSpec") -> tuple[list[ProfileDevice], str]:
        """Device rows + primary for a rig connected through a ``RigSpec``.

        The spec is the ONLY place the connection coordinates survive: a live
        native device object knows its driver's backend name and its display
        name, but not the serial port, ``dev_num`` or ``driver_id`` it was opened
        on. Reading the devices instead is what made this capture write an EMPTY
        profile on a rig of native drivers.

        Only roles that are LIVE are recorded — via ``_role_live_connected``, so
        an engine-served role (the native guider, which has no ``self.devices``
        entry) is captured too rather than silently dropped."""
        devs: list[ProfileDevice] = []
        for role in ROLES:
            if not self._role_live_connected(role):
                continue
            conn = spec.resolve(role)
            dev = self.devices.get(role)
            dev_name = getattr(dev, "name", "") if dev is not None else ""
            devs.append(ProfileDevice(
                role=role,
                backend=conn.backend,
                host=conn.host or "",
                port=conn.port or 0,
                dev_type=conn.dev_type or "",
                dev_num=conn.dev_num or 0,
                name=dev_name or (conn.extra or {}).get("name", "") or "",
                driver_id=conn.driver_id or "",
                transport=conn.transport,
                port_path=conn.port_path or "",
                extra=self._capturable_extra(conn),
            ))
        # Say what was left out. A role the operator explicitly assigned but that
        # is not up right now is NOT written down (a profile must not claim
        # hardware the rig does not have) — but a profile that silently came back
        # narrower than the one being replaced is the same defect in a new
        # costume, so it is reported rather than inferred from a diff.
        missing = sorted(r for r in spec.roles if not self._role_live_connected(r))
        if missing:
            bus.log("warning",
                    f"not saved to the profile — {', '.join(missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} not connected right "
                    f"now, so there is no working configuration to record",
                    "hub")
        return devs, spec.primary

    def _capture_legacy(self) -> tuple[list[ProfileDevice], str]:
        """Device rows + primary for the pre-RigSpec connect paths
        (``connect_alpaca_device`` per role, ``connect_nina``, ``connect_sim``),
        which retain no spec. Unchanged behavior for those rigs."""
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
        return devs, primary

    async def capture_profile(self, name: str) -> Profile:
        """Build a profile from the currently-connected rig and save it."""
        if self._last_rigspec is not None:
            devs, primary = self._capture_from_rigspec(self._last_rigspec)
        else:
            devs, primary = self._capture_legacy()
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

    async def _frame_meta(self, frame, ra_hours: float | None,
                          dec_deg: float | None) -> "FrameMeta":
        """Best-effort telemetry snapshot for the FITS header (spec §8/§9). Every
        read is individually guarded: an absent/hung device or a failed read
        leaves its value None (its card omitted) and never blocks or fails the
        save. Only Hub.capture builds this; save_fits stays device-free."""
        from .catalog import coords
        meta = FrameMeta()
        # optics (config; FOCALLEN always available, pixel size only when known)
        try:
            opt = self.effective_optics()
            fl = opt.get("focal_length_mm")
            if fl:
                meta.focal_length_mm = float(fl)
            if opt.get("have_optics") and opt.get("pixel_size_um"):
                meta.pixel_size_um = float(opt["pixel_size_um"])   # UNBINNED
        except Exception:
            pass
        # site (only when a real, non-default site is configured)
        lat = lon = None
        try:
            s = self.site
            if not s.get("is_default", True):
                lat, lon = float(s["latitude"]), float(s["longitude"])
                meta.site_lat_deg, meta.site_lon_deg = lat, lon
                meta.site_elev_m = float(s.get("elevation_m", 0.0))
        except Exception:
            lat = lon = None
        # pointing geometry (needs J2000 RA/Dec; alt/airmass also need a real site)
        if ra_hours is not None and dec_deg is not None:
            try:
                meta.objctra = coords.format_ra_fits(ra_hours)
                meta.objctdec = coords.format_dec_fits(dec_deg)
            except Exception:
                pass
            if lat is not None and lon is not None:
                try:
                    alt, _az = coords.altaz(ra_hours, dec_deg, lat, lon,
                                            frame.timestamp)
                    if alt > 0:
                        meta.obj_alt_deg = alt
                        meta.airmass = coords.airmass(alt)
                except Exception:
                    pass
        # cooler setpoint (only when a cooler is present AND on)
        try:
            cam = self.devices.get("camera")
            getc = getattr(cam, "get_cooler", None)
            if callable(getc):
                cooler = await getc()
                if cooler and cooler.get("on") and cooler.get("target_c") is not None:
                    meta.set_temp_c = float(cooler["target_c"])
        except Exception:
            pass
        # focuser position + optional thermometer
        try:
            foc = self.devices.get("focuser")
            if foc and getattr(foc, "connected", False):
                meta.focuser_pos = int(await foc.get_position())
                t = await foc.get_temperature()
                if t is not None:
                    meta.focuser_temp_c = float(t)
        except Exception:
            pass
        # rotator sky position angle
        try:
            rot = self.devices.get("rotator")
            if rot and getattr(rot, "connected", False):
                meta.rotator_angle_deg = float(await rot.get_position())
        except Exception:
            pass
        # EGAIN (populated on the frame by the backend in Task 5; getattr keeps
        # this task decoupled from that field's existence)
        eg = getattr(frame, "egain_e_per_adu", None)
        if not eg:
            # Driver reported nothing (Alpaca/NINA): fall back to a MEASURED
            # value for exactly this gain, when the user has learned one. The
            # driver's own number always wins the branch above.
            eg = self.learned_egain(getattr(frame, "gain", -1))
        if eg:
            meta.egain_e_per_adu = float(eg)
        # quality (already measured on the frame)
        if frame.hfr is not None:
            meta.hfr = float(frame.hfr)
        if frame.stars is not None:
            meta.star_count = int(frame.stars)
        return meta

    async def _active_filter_name(self) -> str:
        """The name of the filter the wheel is PHYSICALLY on right now, or ``""``
        when there is no connected wheel (or it can't be read).

        THE single source of filter identity (UX #1). ``capture`` stamps this into
        the FITS ``FILTER`` card, the ``$$FILTER$$`` filename token and the
        ``info["filter"]`` the sequence engine records — so the header, the
        filename, the session report, the stacking-bundle folder and the CSV can
        never disagree again. Never raises."""
        fw = self.devices.get("filterwheel")
        if not fw or not getattr(fw, "connected", False):
            return ""
        try:
            names = list(getattr(fw, "filter_names", []) or [])
            pos = await fw.get_position()
            if pos is None or pos < 0 or pos >= len(names):
                return ""
            return str(names[pos] or "")
        except Exception:
            return ""

    async def capture(self, exposure_s: float, gain: int, offset: int,
                      binning: int = 1, save: bool = False, target: str = "",
                      frame_type: str = "Light") -> dict:
        cam: Camera = self.require("camera")
        # Serialize the exposure against every other capture path (loop / single /
        # autofocus / sequence / solve) so two coroutines can't poll the shared
        # camera imageready flag at once (cross-downloaded frames / mis-stamped
        # metadata / InvalidOperation).
        async with self.exposure_guard(f"capture {frame_type.lower()}"):
            # Dark AND Bias are shutter-closed (no light); Light/Flat expose the
            # sensor. (UX-22 made Bias user-selectable, so honor its shutter.)
            frame = await cam.expose(exposure_s, gain, offset, binning,
                                     light=(frame_type.upper() not in ("DARK", "BIAS")),
                                     save=save, target=target)
        self.last_frame = frame
        # UX #1 (filter identity): resolve the WHEEL'S ACTUAL POSITION once, here,
        # and let it feed EVERY consumer — the FITS FILTER card, the filename
        # token, and (returned on ``info``) the session report / bundle grouping /
        # frames.csv. Those used to key off the PLAN's step.filter text, so a wheel
        # sitting at L while the step said "no filter" wrote FILTER=L into the file
        # and ``filter: null`` into the report, and the stacking bundle grouped the
        # night under ``NoFilter/`` — silently handing the stacker the wrong flats.
        # One source, one answer. Only resolved when we're SAVING (the live loop's
        # throw-away frames must not pay a wheel read per exposure).
        filt = await self._active_filter_name() if save else ""
        # For local (sim/Alpaca) saves, write the FITS BEFORE publishing the
        # preview so the first `preview` event already carries the correct
        # saved_path/saved_local (P2-2). NINA saves on the imaging host during
        # expose() and the frame already carries its saved_path.
        local_save_path: Path | None = None
        if save and frame.rendered_bytes is None:
            local_save_path = self._capture_path(
                target or "untargeted", frame_type, filt,
                gain=gain, exposure_s=exposure_s, binning=binning,
                sensor_temp_c=getattr(frame, "temperature_c", None))
            ra = dec = None
            tel = self.devices.get("telescope")
            if tel and tel.connected:
                try:
                    ra, dec = await tel.get_position()
                    # Bring a JNOW Alpaca mount's report to J2000 (the frame ASTAP
                    # and the catalog use); no-op for sim/NINA. Best-effort guarded
                    # (supervisor ruling 3).
                    if ra is not None:
                        ra, dec = await self.from_mount_frame(tel, ra, dec)
                except Exception:
                    pass
            # Gather header telemetry (best-effort; never fails the save) and the
            # OTA name for TELESCOP (omitted when blank). Wrapping the whole meta
            # build keeps spec §9 (a header write never fails a capture) structural,
            # not dependent on CameraFrame's field set staying non-raising.
            try:
                meta = await self._frame_meta(frame, ra, dec)
            except Exception:
                meta = FrameMeta()
            try:
                telescope_name = (self.effective_optics().get("telescope_name")
                                  or "").strip()
            except Exception:
                telescope_name = ""
            # Offloaded so a 25-120 MB uint16 FITS write to the Pi's SD card never
            # freezes the event loop for seconds every frame (WS/preview stall,
            # queued guide events, delayed STOP) — same as solve_and_sync's write.
            await asyncio.to_thread(
                save_fits, frame, local_save_path, target=target, filter_name=filt,
                frame_type=frame_type, ra_hours=ra, dec_deg=dec,
                telescope=telescope_name, instrument=cam.name, meta=meta)
            # carry the path on the frame so _publish_preview reports a correct
            # saved_path/saved_local in the very first event (no stale re-publish).
            frame.saved_path = str(local_save_path)

        info = await self._publish_preview(frame)
        if save and isinstance(info, dict):
            # UX #1: hand the RESOLVED filter (same value the FITS card carries)
            # back to the caller. The sequence engine records THIS, not the plan's
            # step text, so report/bundle/CSV agree with the header. "" means "no
            # wheel / unreadable", which the engine falls back from.
            info["filter"] = filt

        if save and frame.rendered_bytes is not None:
            # The backend (NINA) already saved the file on the imaging machine.
            if frame.saved_path:
                bus.log("info", f"NINA saved {Path(frame.saved_path).name}", "capture")
            else:
                bus.log("info", "NINA saved the frame", "capture")
        elif local_save_path is not None:
            bus.log("info", f"saved {local_save_path.name}", "capture")

        # Opt-in (default OFF): hand the saved light to the BACKGROUND WCS worker
        # so its plate solve stamps astrometry into the header without the
        # capture path ever waiting on it (per-frame-wcs spec §2.1 — an inline
        # 2-10 s ASTAP run would delay the next sub by its whole duration, every
        # frame). Guarded on local_save_path: a local save ran => ra/dec are
        # bound AND the file is on this box (decision D5 — a NINA/remote save
        # lives on the imaging host and cannot be reopened here). Enqueue is
        # non-blocking, bounded and total: it can neither await nor raise into
        # the capture.
        # LIGHT only, as the config field is named: a dark/bias/flat has no stars
        # to solve, so enqueuing one only burns a full ASTAP run (and its 60 s
        # kill timeout) per frame — a 50-frame dark library would peg a core of
        # the Pi for the whole unattended run and flood the log with failures.
        if (local_save_path is not None and frame_type.upper() == "LIGHT"
                and config_store.cfg().solve_saved_lights):
            # star count from the preview's SINGLE detection pass (info["stars"]),
            # not frame.stars — the latter is only ever set by a backend that
            # measured it (NINA), so the min-stars gate would be a silent no-op
            # on exactly the local frames it exists to filter.
            self._enqueue_wcs_stamp(local_save_path, ra, dec, info.get("stars"))
        return info

    # ------------------------------------------------- per-frame WCS stamping
    # (per-frame-wcs spec §2; the mechanism — solvers, WcsSolution, write_wcs —
    #  shipped with PRO-2 F-B. What lives here is the OFF-the-hot-path plumbing.)

    def _enqueue_wcs_stamp(self, path: Path, ra: float | None, dec: float | None,
                           star_count: int | None) -> None:
        """Queue one saved light for background solve+stamp. Never blocks, never
        raises (the capture must survive any failure here), and never grows
        without bound.

        Overflow policy (decision D2) is **drop-oldest**: when the backlog is at
        ``wcs_stamp.queue_max`` we discard the stalest pending job so the NEWEST
        frames stay tagged, memory stays capped, and a slow solver can never
        wedge capture. Dropped frames simply ship without WCS — downstream can
        always re-solve. Logged once per backlog episode, not per frame."""
        try:
            cfg = config_store.cfg()
            stamp = getattr(cfg, "wcs_stamp", None)
            bound = max(1, int(getattr(stamp, "queue_max", 4) or 4))
            if self._wcs_queue is None:
                self._wcs_queue = asyncio.Queue()
            q = self._wcs_queue
            dropped = 0
            while q.qsize() >= bound:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:      # pragma: no cover - defensive
                    break
                q.task_done()
                dropped += 1
            fov_hint = None
            try:
                fov_hint = self.effective_optics().get("fov_h_deg") or None
            except Exception:                   # pragma: no cover - defensive
                fov_hint = None
            q.put_nowait(_WcsJob(path=path, ra=ra, dec=dec, fov_deg=fov_hint,
                                 star_count=star_count))
            if dropped and not self._wcs_drop_logged:
                self._wcs_drop_logged = True
                bus.log("warning",
                        "WCS tagging is falling behind — the oldest untagged "
                        "frames are being skipped (capture is never delayed)",
                        "solve")
            if self._wcs_task is None or self._wcs_task.done():
                self._wcs_task = asyncio.create_task(self._wcs_worker())
        except Exception as e:  # noqa: BLE001 - enqueue must never fail a capture
            bus.log("warning",
                    f"could not queue WCS tagging ({e}); frame saved without WCS",
                    "solve")

    async def _wcs_worker(self) -> None:
        """Single long-lived consumer: one solve in flight at a time (ASTAP is
        CPU-heavy — parallel solves would thrash a Pi). Each job is fully
        isolated: a failure/timeout is logged and the worker moves on, and a
        cancel mid-solve ends the worker silently (no error log, no half-written
        header — ``write_wcs`` is itself non-fatal)."""
        q = self._wcs_queue
        assert q is not None
        while True:
            job = await q.get()
            try:
                await self._solve_and_stamp(job)
            except asyncio.CancelledError:
                raise                       # teardown asked us to stop: obey it
            except Exception as e:  # noqa: BLE001 - best-effort, per spec §9
                bus.log("warning",
                        f"WCS tagging failed for {job.path.name} ({e}); "
                        "frame saved without WCS", "solve")
            finally:
                q.task_done()
                if q.empty():
                    self._wcs_drop_logged = False   # backlog drained: re-arm

    def _wcs_solver(self, stamp):
        """The solver for the WCS-stamp path. "auto" defers to
        ``providers.pick_solver`` — the single authority on ASTAP-vs-sim
        precedence, including the real-rig fake-solve refusal. "astap" is an
        advanced override that degrades to auto (with a log) when ASTAP is not
        installed. There is deliberately no "force sim" (decision D4)."""
        if getattr(stamp, "solver", "auto") == "astap":
            from .solve import AstapSolver, find_astap
            exe = find_astap()
            if exe:
                return AstapSolver(exe)
            bus.log("warning",
                    "WCS tagging is set to ASTAP but ASTAP was not found — "
                    "using automatic solver selection", "solve")
        # module attribute (never `from . import pick_solver`) so tests can
        # monkeypatch the precedence owner.
        from . import providers as _providers
        return _providers.pick_solver(self)

    async def _solve_and_stamp(self, job: "_WcsJob") -> None:
        """Solve one saved light and merge its WCS into the header in place.
        The file is already closed and every job targets its OWN path (the next
        capture writes a different one), so there is no same-file contention."""
        from .config import WcsStampConfig
        from .solve import wcs_should_solve
        stamp = getattr(config_store.cfg(), "wcs_stamp", None) or WcsStampConfig()
        if not wcs_should_solve(job.star_count, stamp):
            return
        solver = self._wcs_solver(stamp)
        kwargs = {}
        # Only pass the knob when the user actually set one: 0 == "solver's own
        # automatic choice" == today's behaviour, and omitting it keeps every
        # pre-existing/monkeypatched solver stand-in callable unchanged.
        down = max(0, int(getattr(stamp, "downsample", 0) or 0))
        if down:
            kwargs["downsample"] = down
        res = await solver.solve(job.path, ra_hint=job.ra, dec_hint=job.dec,
                                 fov_deg_hint=job.fov_deg, **kwargs)
        # A failed solve, or a solve whose WCS was REJECTED upstream (ASTAP's
        # scale-less-result guard returns wcs=None rather than a bogus ~1°/px
        # solution), stamps nothing. An absent card beats a wrong one.
        if res.success and res.wcs is not None:
            await asyncio.to_thread(write_wcs, job.path, res.wcs)
            bus.log("info", f"stamped WCS on {job.path.name}", "solve")

    def stop_wcs_worker(self) -> None:
        """Cancel the background WCS worker and drop any pending backlog. Called
        from ``_teardown`` alongside ``stop_loop`` so the task can never outlive
        its hub (risk R1)."""
        if self._wcs_task and not self._wcs_task.done():
            self._wcs_task.cancel()
        self._wcs_task = None
        self._wcs_queue = None
        self._wcs_drop_logged = False

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
            sub = data                                   # the raw linear sub
            # one detection pass on the SUB → alignment anchor + HFR + count +
            # overlay marks + cloud verdict (all honest per-sub metrics)
            stars = await asyncio.to_thread(detect_stars, sub)
            # Live View (NOV-1): feed the armed accumulator this sub and, if a
            # running mean exists, swap the pixels the IMAGE pipeline renders from
            # the sub to the stacked mean. detect_stars / measure_stars /
            # cloud_score stay on the sub below. No-op when unarmed.
            ls_info = None
            if self.live_stacker is not None:
                outcome = await asyncio.to_thread(
                    self.live_stacker.add, sub, frame.exposure_s, stars=stars)
                mean = self.live_stacker.mean()
                if mean is not None:
                    data = mean                          # image pipeline shows the stack
                    info["stats"] = await asyncio.to_thread(frame_stats, data)
                ls_info = {"frames": outcome.frames,
                           "integrated_s": round(outcome.integrated_s, 1),
                           "rejected": outcome.rejected,
                           "accepted": outcome.accepted,
                           # Why this sub landed where it did: "" = a clean
                           # constellation match, "weak_align" = fell back to a
                           # single star, "drift"/"no_match"/"no_stars" =
                           # rejected, "reseed" = adopted a new framing.
                           "reason": outcome.reason,
                           # Star pairs backing the shift, and the pixels clipped
                           # out of THIS sub as bright outliers (a satellite).
                           "support": outcome.support,
                           "clipped": outcome.clipped,
                           "dx": round(outcome.dx, 2),
                           "dy": round(outcome.dy, 2)}
            black, mid, white = await asyncio.to_thread(auto_levels, data)
            jpeg, dw, dh = await asyncio.to_thread(
                to_jpeg, data, black=black, mid=mid, white=white)
            lossless = await asyncio.to_thread(to_png, data)
            thumb = await asyncio.to_thread(to_thumb, data)
            # display-domain histogram (handles have travel) + the true linear one
            stretched = await asyncio.to_thread(stretch_with, data, black, mid, white)
            hist_display = await asyncio.to_thread(display_histogram, stretched)
            hfr, count, marks = measure_stars(stars, full_well=info["full_well"])
            info.update({
                "histogram": hist_display,
                "histogram_linear": await asyncio.to_thread(compute_histogram, sub),
                "histogram_domain": "display",
                "display_width": dw, "display_height": dh,
                "mime": "image/jpeg", "has_lossless": True,
                "auto_levels": {"black": round(black, 4), "mid": round(mid, 4),
                                "white": round(white, 4)},
                "star_list": marks,
            })
            # Absolute per-SUB SNR input (polish grab-bag (b)): the median
            # background-subtracted flux of the SAME trusted mid-bright stars the
            # marks come from, over the same single detection pass. The client
            # multiplies by its own e-/ADU gain to show a real "this sub" SNR.
            # Omitted entirely when no star passes the gate — honest abstain.
            fmed = star_flux_median(stars, full_well=info["full_well"])
            if fmed is not None:
                info["star_flux_median"] = round(fmed, 1)
            # Representative frame eccentricity = median of the trusted marks'
            # ecc (no second detection pass). setdefault so a backend-supplied
            # ecc (native/NINA) wins, mirroring hfr/stars below.
            fecc = frame_eccentricity(marks)
            if fecc is not None:
                info.setdefault("ecc", round(fecc, 3))
            # Sensor-tilt / corner-vs-center inspector (PRO-13) — additive zone
            # map + pattern classification over the same trusted marks, no new
            # detection pass. None (too sparse) => key omitted entirely.
            tilt = frame_tilt(marks, info["data_width"], info["data_height"])
            if tilt is not None:
                info["tilt"] = tilt
            # Image-derived cloud verdict, reusing the star count from the single
            # detection pass above (no second detect). Linear frames only — the
            # contrast metric needs unstretched pixels. Complements the
            # forecast-based cloud cover in weather.py with what the camera sees.
            if data_is_linear:
                cloud = await asyncio.to_thread(
                    cloud_score, sub, stars=stars)
                info["cloud"] = cloud.to_dict()
            # NOV-12: additive Bahtinov focus verdict, only while the aid is armed
            # and only on linear subs (the Radon fit needs unstretched pixels).
            # Reuses the single detect_stars pass above (center on the brightest
            # star); a no-op when disarmed so the preview path stays byte-identical.
            if self.bahtinov is not None and data_is_linear:
                from .imaging import analyze_bahtinov
                res = await asyncio.to_thread(
                    lambda: analyze_bahtinov(sub, None, stars=stars,
                                             tol_px=self.bahtinov["tol_px"],
                                             invert=self.bahtinov["invert"]))
                info["bahtinov"] = res.to_dict()
            # backend-measured HFR/stars (e.g. native) win; else our detection.
            if hfr is not None:
                info.setdefault("hfr", round(float(hfr), 2))
                info.setdefault("stars", int(count))
            # DEFOCUS SIZE, so the UI can tell "in focus" from "so far out that
            # HFR is meaningless". detect_stars measures inside a 15px box, so
            # its HFR saturates around 5-7px no matter how defocused the frame
            # is: on 2026-07-31 an 880px donut field reported "median HFR 4.5px"
            # and 1393 "stars", and the Focus panel called it FAIR. A number
            # that cannot exceed 7 cannot report a 440px blob, so the honest
            # signal has to come from a measurement that has no such ceiling.
            if data_is_linear:
                from .imaging.defocus import measure_blob
                blob = await asyncio.to_thread(measure_blob, sub)
                if blob is not None:
                    info["defocus_r80"] = round(blob.r80, 1)
                    info["defocus_snr"] = round(blob.snr, 1)
            # NOV-1: additive live-stacking readout (only when Live View is armed).
            if ls_info is not None:
                info["livestack"] = ls_info
            # /crop + /render (the former Pass-2 501 stubs) now read
            # entry.linear, so retain the raw linear sub for linear frames.
            # Bounded to the latest PREVIEW_LINEAR_KEEP frames by _trim_previews
            # (same memory policy as `lossless`); NINA display-domain frames have
            # no linear pixels, so they keep linear=None.
            entry = PreviewEntry(display=jpeg, mime="image/jpeg", thumb=thumb,
                                 lossless=lossless,
                                 linear=(sub if data_is_linear else None),
                                 meta=info)

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

    def _seed_filter_config(self) -> None:
        """Overlay the active profile's saved filter slot names + focuser offsets
        onto the just-connected filter wheel, keeping the hardware-derived value
        as the per-slot fallback (UX-05). Best-effort: an absent/corrupt store or
        no filter wheel leaves the hardware names untouched. Generic — runs on
        every connect path, not just Wanderer."""
        fw = self.devices.get("filterwheel")
        if fw is None or not getattr(fw, "connected", False):
            return
        try:
            from .config import config_store, load_filter_config
            saved = load_filter_config(config_store.cfg().active_profile_id)
        except Exception:  # pragma: no cover - defensive
            return
        names = saved.get("names") or []
        offsets = saved.get("offsets") or []
        if fw.filter_names:
            new_names = list(fw.filter_names)
            for i in range(len(new_names)):
                if i < len(names) and str(names[i]).strip():
                    new_names[i] = str(names[i]).strip()
            fw.filter_names = new_names
        if offsets and fw.filter_names:
            cur = list(fw.filter_offsets) if fw.filter_offsets else [0] * len(fw.filter_names)
            for i in range(len(cur)):
                if i < len(offsets):
                    try:
                        cur[i] = int(offsets[i])
                    except (TypeError, ValueError):
                        pass
            fw.filter_offsets = cur
        # Blackout flags are user-assigned — no wheel reports them — so unlike
        # names there is no hardware fallback to preserve: the stored list IS the
        # truth, padded/truncated to the wheel's real slot count.
        opaque = saved.get("opaque")
        if isinstance(opaque, list) and fw.filter_names:
            n = len(fw.filter_names)
            fw.filter_opaque = [bool(opaque[i]) if i < len(opaque) else False
                                for i in range(n)]

    # ------------------------------------------------- learned camera EGAIN
    def _seed_egain_config(self) -> None:
        """Load the active profile's learned per-gain EGAIN map into memory.
        Best-effort: a missing/corrupt store just leaves the map empty (the
        driver-reported value, or nothing, still applies)."""
        try:
            from .config import config_store, load_egain_config
            self._egain_learned = load_egain_config(
                config_store.cfg().active_profile_id)
        except Exception:  # pragma: no cover - defensive
            self._egain_learned = {}

    def learned_egain(self, gain: int) -> float | None:
        """The MEASURED e-/ADU for exactly this gain, or None.

        Exact-match only — conversion gain changes across a camera's HCG
        transition, so interpolating between learned points would invent a
        number. Callers must prefer any driver-reported value over this."""
        try:
            v = self._egain_learned.get(int(gain))
        except (TypeError, ValueError):
            return None
        return float(v) if v else None

    async def learn_egain(self, gain: int, count: int = 4,
                          exposure_s: float = 2.0, offset: int = 10,
                          binning: int = 1) -> dict:
        """Measure e-/ADU by mean-variance: ``count`` bias frames then ``count``
        flats at ``gain``, all under the shared exposure guard.

        The result is PERSISTED per gain and stamped onto the camera ONLY when
        the driver reports no egain — a real hardware number is never overridden
        by an estimate."""
        from .imaging.egain import measure_egain
        cam: Camera = self.require("camera")
        n = max(2, int(count))
        total = 2 * n
        bus.publish("egain", state="running", step=0, of=total)
        bus.log("info", f"measuring e-/ADU at gain {gain} "
                        f"({n} bias + {n} flat frames)…", "egain")
        try:
            biases: list = []
            flats: list = []
            for i in range(n):
                async with self.exposure_guard("egain bias"):
                    fr = await cam.expose(0.0, int(gain), int(offset),
                                          int(binning), light=False)
                biases.append(np.asarray(fr.data))
                bus.publish("egain", state="running", step=len(biases), of=total)
            for i in range(n):
                async with self.exposure_guard("egain flat"):
                    fr = await cam.expose(float(exposure_s), int(gain),
                                          int(offset), int(binning), light=True)
                flats.append(np.asarray(fr.data))
                bus.publish("egain", state="running",
                            step=n + len(flats), of=total)
            value = measure_egain(flats, biases)
        except asyncio.CancelledError:
            bus.publish("egain", state="failed", step=0, of=total,
                        error="cancelled")
            raise
        except Exception as e:
            bus.publish("egain", state="failed", step=0, of=total, error=str(e))
            bus.log("error", f"e-/ADU measurement failed: {e}", "egain")
            raise
        self._egain_learned[int(gain)] = float(value)
        try:
            from .config import config_store, save_egain_config
            save_egain_config(config_store.cfg().active_profile_id,
                              self._egain_learned)
        except Exception:  # pragma: no cover - persistence is best-effort
            pass
        # Driver-reported EGAIN always wins: only stamp a camera that reports 0.
        applied = False
        if not getattr(cam, "egain", 0.0):
            cam.egain = float(value)
            applied = True
        bus.publish("egain", state="done", step=total, of=total,
                    gain=int(gain), egain=float(value), applied=applied)
        bus.log("info", f"measured {value:.3f} e-/ADU at gain {gain}"
                        + ("" if applied else " (driver value kept)"), "egain")
        return {"gain": int(gain), "egain": float(value), "applied": applied,
                "learned": dict(self._egain_learned)}

    # --------------------------------------------------- cooler warm-down ramp
    #
    # The bug this replaces (2026-08-04): "Warm" was one ``set_cooler(False)``.
    # The owner heard the TEC stop and measured 8.3 → 11.8 °C in ~40 s on the
    # live camera — ~5 °C/min, which is a cold sensor equalising with the room,
    # not a controlled warm-up. Thermal shock across the sensor/cold-finger and
    # condensation inside the chamber are the costs, and the path that did it
    # most often (``abort_park_warm``) runs with nobody watching.
    #
    # Everything below is built around three constraints:
    #   * NON-BLOCKING — the safety path fires when something is already wrong,
    #     so ``warm_camera`` spawns and returns; it must never delay a park or a
    #     roof close.
    #   * INTERRUPTIBLE with a DEFINED end state — a cancel either hands the
    #     cooler to a new owner (Cool) or completes the warm the fast way
    #     (switch off). The camera is never left mid-ramp holding a setpoint
    #     nobody chose.
    #   * LOUD ON FALLBACK — if the sensor cannot be read, or config turned the
    #     ramp off, we still switch the cooler off, but the log SAYS the ramp did
    #     not run. A silent fallback to the bug is worse than the bug, because
    #     SafetyLimitsPanel goes on promising a safe ramp.

    async def _warm_read_temp(self, cam: Any) -> float | None:
        """Bounded sensor read. None on timeout/driver error — the ramp treats
        "cannot measure" as a hard reason NOT to pretend it is ramping."""
        try:
            value = await asyncio.wait_for(cam.get_temperature(),
                                           cooling.WARM_CMD_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return None if value is None else float(value)

    async def _warm_read_ambient(self, cam: Any) -> float | None:
        """Rig-measured ambient, when the backend has it (most do not). Optional
        seam via getattr so a device object that predates the hook — every test
        fake, for one — is simply "cannot measure" rather than an AttributeError
        on the safety path."""
        getter = getattr(cam, "get_ambient_temperature", None)
        if not callable(getter):
            return None
        try:
            value = await asyncio.wait_for(getter(), cooling.WARM_CMD_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return None if value is None else float(value)

    def warm_state(self) -> dict | None:
        """The warm-ramp state for ``poll_status``, or None when there is nothing
        to report. A FINISHED warm lingers for ``WARM_STATE_RETAIN_S`` so the
        Capture screen can say "warm complete" — without it the panel snaps back
        to a plain "Off" that looks identical to the old cut-it-dead bug, which
        is exactly the ambiguity this whole change exists to remove."""
        state = self._warm_state
        if state is None:
            return None
        if not state.get("active"):
            done_at = state.get("_finished_monotonic")
            if done_at is None or (time.monotonic() - done_at) > cooling.WARM_STATE_RETAIN_S:
                return None
        # strip private bookkeeping; round so the WS diff is stable poll to poll
        out = {k: v for k, v in state.items() if not k.startswith("_")}
        for key in ("start_c", "ambient_c", "setpoint_c", "temp_c", "rate_c_per_min"):
            if isinstance(out.get(key), (int, float)):
                out[key] = round(float(out[key]), 2)
        for key in ("elapsed_s", "eta_s"):
            if isinstance(out.get(key), (int, float)):
                out[key] = int(round(out[key]))
        return out

    async def cool_camera(self, target_c: float | None = None) -> None:
        """Cool to ``target_c``. Cancels any warm ramp FIRST so the two cannot
        fight: without the cancel, the ramp's next scheduled step (≤15 s away)
        would quietly overwrite the setpoint the user just chose, and the Capture
        screen would show a target the camera was not holding."""
        cam: Camera = self.require("camera")
        await self.cancel_warm("cooling was requested", finalize=False)
        await cam.set_cooler(True, target_c)

    async def cancel_warm(self, reason: str, *, finalize: bool) -> bool:
        """Stop an in-flight warm ramp. Returns True if one was actually running.

        ``finalize=True`` finishes the warm the fast way (cooler OFF) — for "stop
        the ramp, I want it off now" and for rig teardown. ``finalize=False`` is
        for a caller taking ownership of the cooler in the very next statement
        (``cool_camera``); it is the ONLY case where leaving the TEC on is a
        defined state, because the caller is about to define it."""
        async with self._warm_lock:
            was_running = await self._cancel_warm_locked(reason)
            if was_running and finalize:
                cam = self.devices.get("camera")
                if cam is not None and getattr(cam, "connected", False):
                    try:
                        await asyncio.wait_for(cam.set_cooler(False),
                                               cooling.WARM_CMD_TIMEOUT_S)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        bus.log("warning", f"could not switch the cooler off after "
                                           f"stopping the warm ramp: {e}", "camera")
            return was_running

    async def _cancel_warm_locked(self, reason: str) -> bool:
        """Cancel the ramp task and await its death. Caller holds ``_warm_lock``.

        The CANCELLER does the device work (see ``cancel_warm``), never the
        cancelled task: a task being torn down cannot be trusted to complete an
        await against a USB driver, and "the cooler is off" is too important to
        hang off that."""
        task = self._warm_task
        self._warm_task = None
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        state = self._warm_state
        if state is not None:
            # Unconditionally, NOT gated on state["active"]: the task's own
            # ``finally`` has already flipped that False on its way out and
            # stamped its default "warm complete" note. Gating here left every
            # cancelled ramp claiming, in the UI and the log, that it had
            # finished — the one sentence a cancel must never produce.
            state["active"] = False
            state["note"] = f"stopped: {reason}"
            state["_finished_monotonic"] = time.monotonic()
        bus.log("info", f"camera warm ramp stopped — {reason}", "camera")
        return True

    async def warm_camera(self, *, source: str = "user", ramp: bool = True) -> dict:
        """Warm the camera and return IMMEDIATELY with the warm state.

        ``ramp=False`` is the deliberate escape hatch: switch the cooler off now,
        no ramp, logged as such. Everything else runs the ramp in the background.

        Never raises for "nothing to do" — a camera with no cooler, or one that is
        already at ambient, returns an inactive state with the reason in ``note``.
        It DOES raise DeviceError when there is no camera at all, so the route can
        answer 400 rather than silently succeeding at nothing."""
        cam: Camera = self.require("camera")
        if not getattr(cam, "can_cool", False):
            return self._warm_finished_state(source, f"{cam.name} has no cooler",
                                             ramped=False)
        async with self._warm_lock:
            if self._warm_task is not None and not self._warm_task.done():
                if ramp:
                    # Two warms must not race. The second one JOINS the first
                    # rather than starting a rival stepper on the same setpoint.
                    bus.log("info", "warm already in progress — leaving it running",
                            "camera")
                    return self.warm_state() or {}
                await self._cancel_warm_locked("an immediate warm was requested")

            cfg = config_store.cfg()
            if not ramp or not cooling.warm_ramp_enabled(cfg):
                why = ("at the caller's request" if not ramp
                       else "cooling.warm_ramp is turned off in config")
                return await self._warm_now(cam, source, why, level="warning")

            start_c = await self._warm_read_temp(cam)
            if start_c is None:
                # The measurement IS the ramp: without a starting temperature we
                # cannot pick a setpoint schedule, and a made-up one could command
                # the cooler COLDER than it is. Fall back — and say so, because
                # the alternative is the product quietly doing the old thing.
                return await self._warm_now(
                    cam, source,
                    "this camera cannot report its sensor temperature, so the "
                    "setpoint ramp has nothing to follow", level="warning")

            measured_ambient = await self._warm_read_ambient(cam)
            ambient_c, ambient_from = cooling.warm_ambient_c(cfg, start_c,
                                                             measured_ambient)
            rate = cooling.warm_rate_c_per_min(cfg)
            if cooling.warm_is_pointless(start_c, ambient_c):
                return await self._warm_now(
                    cam, source,
                    f"the sensor is already at {start_c:.1f} °C, at or above "
                    f"ambient — nothing to ramp", level="info")

            duration_s = cooling.warm_duration_s(start_c, ambient_c, rate)
            delegated = bool(getattr(cam, "self_warms", False))
            self._warm_state = {
                "active": True,
                "source": source,
                "ramped": True,
                "delegated": delegated,
                "start_c": start_c,
                "ambient_c": ambient_c,
                "ambient_from": ambient_from,
                "setpoint_c": start_c,
                "temp_c": start_c,
                "rate_c_per_min": rate,
                "elapsed_s": 0,
                "eta_s": duration_s,
                "note": "",
                "_started_monotonic": time.monotonic(),
                "_finished_monotonic": None,
            }
            if delegated:
                # The backend owns the ramp (NINA). Hand it the DURATION our rate
                # implies and track the clock so the UI still has a progress bar —
                # two rampers stepping one setpoint would fight.
                minutes = cooling.warm_minutes(start_c, ambient_c, rate)
                warm_fn = getattr(cam, "warm", None)
                try:
                    if callable(warm_fn):
                        await asyncio.wait_for(warm_fn(minutes),
                                               cooling.WARM_CMD_TIMEOUT_S)
                    else:
                        await asyncio.wait_for(cam.set_cooler(False),
                                               cooling.WARM_CMD_TIMEOUT_S)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._warm_state = None
                    return await self._warm_now(
                        cam, source, f"the backend refused the timed warm ({e})",
                        level="warning")
                bus.log("info", f"warming camera — {cam.name} is running its own "
                                f"{minutes} min ramp from {start_c:.1f} °C toward "
                                f"{ambient_c:.1f} °C ({ambient_from})", "camera")
            else:
                bus.log("info", f"warming camera — ramping the setpoint "
                                f"{start_c:.1f} → {ambient_c:.1f} °C ({ambient_from}) "
                                f"at {rate:g} °C/min, about "
                                f"{duration_s / 60.0:.0f} min, in the background",
                        "camera")
            self._warm_task = asyncio.create_task(
                self._warm_ramp(cam, delegated=delegated))
            return self.warm_state() or {}

    def _warm_finished_state(self, source: str, note: str, *,
                             ramped: bool) -> dict:
        """Record a warm that is already over (no ramp ran, or nothing to ramp)
        so the UI and the log agree on what happened."""
        self._warm_state = {
            "active": False, "source": source, "ramped": ramped,
            "delegated": False, "start_c": None, "ambient_c": None,
            "ambient_from": None, "setpoint_c": None, "temp_c": None,
            "rate_c_per_min": None, "elapsed_s": 0, "eta_s": None,
            "note": note,
            "_started_monotonic": time.monotonic(),
            "_finished_monotonic": time.monotonic(),
        }
        return self.warm_state() or {}

    async def _warm_now(self, cam: Any, source: str, why: str,
                        *, level: str = "warning") -> dict:
        """The un-ramped path: switch the cooler off immediately, and SAY SO.

        This is the pre-fix behaviour, kept because there are real reasons to
        reach it (no temperature readout, ramp disabled, already at ambient) —
        but never silently. ``level="warning"`` routes through the alert
        dispatcher, so an unattended rig that fell back to cutting the TEC dead
        actually tells somebody."""
        # A backend that owns its ramp needs to be told explicitly NOT to ramp:
        # NinaCamera.set_cooler(False) now sends a computed duration, so calling
        # it here would start a ten-minute warm on the "cut it now" path. 0 is
        # NINA's own warm-IMMEDIATELY sentinel — the value that caused this bug,
        # used here on purpose because immediately is what was asked for.
        warm_fn = getattr(cam, "warm", None)
        try:
            if getattr(cam, "self_warms", False) and callable(warm_fn):
                await asyncio.wait_for(warm_fn(0), cooling.WARM_CMD_TIMEOUT_S)
            else:
                await asyncio.wait_for(cam.set_cooler(False),
                                       cooling.WARM_CMD_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            bus.log("error", f"could not switch the cooler off: {e}", "camera")
            return self._warm_finished_state(source, f"cooler command failed: {e}",
                                             ramped=False)
        if level == "info":
            bus.log("info", f"cooler off — {why}", "camera")
        else:
            bus.log(level, f"cooler switched off WITHOUT a warm ramp — {why}. The "
                           "sensor will equalise with ambient on its own (~5 °C/min "
                           "measured on this rig)", "camera")
        return self._warm_finished_state(source, why, ramped=False)

    async def _warm_ramp(self, cam: Any, *, delegated: bool) -> None:
        """The background ramp. Steps the SETPOINT toward ambient and switches
        the TEC off only at the end (or when the sensor stops following, which is
        the same thing: the cooler has nothing left to do).

        Cancellation is expected, not exceptional — see ``_cancel_warm_locked``
        for who does the device work in that case."""
        state = self._warm_state or {}
        start_c = float(state.get("start_c") or 0.0)
        ambient_c = float(state.get("ambient_c") or 0.0)
        rate = float(state.get("rate_c_per_min") or cooling.WARM_RATE_C_PER_MIN)
        setpoint = start_c
        lagging = 0
        note = "warm complete"
        # Absolute budget so a camera that never converges cannot leave a task
        # (and a "warming…" chip) alive all night. 3x the schedule, floor 10 min,
        # ceiling 1 h — generous, because ending EARLY is the failure mode that
        # hurts hardware.
        planned_s = cooling.warm_duration_s(start_c, ambient_c, rate)
        budget_s = min(3600.0, max(600.0, planned_s * 3.0))
        started = time.monotonic()
        try:
            while True:
                await asyncio.sleep(cooling.WARM_STEP_S)
                elapsed = time.monotonic() - started
                temp = await self._warm_read_temp(cam)
                if temp is not None:
                    state["temp_c"] = temp
                if delegated:
                    # The backend is ramping; we only track time so the UI has a
                    # bar and the log has an end. Its own setpoint is not ours to
                    # step, and reading it back per-poll would cost an API call
                    # per 15 s for no decision we would make differently.
                    state["elapsed_s"] = elapsed
                    state["eta_s"] = max(0.0, planned_s - elapsed)
                    if elapsed >= planned_s:
                        note = "warm complete (backend ramp)"
                        break
                    if elapsed > budget_s:
                        note = "backend ramp overran its schedule"
                        break
                    continue
                # The sensor no longer following the setpoint means the TEC has
                # stopped being the thing setting the temperature — i.e. we have
                # climbed past the REAL ambient. That is the ramp's natural end,
                # and switching off there is thermally a no-op. Requiring two
                # consecutive breaches rides out a stale reading right after a
                # setpoint change.
                if temp is not None and (setpoint - temp) > cooling.WARM_MAX_LEAD_C:
                    lagging += 1
                    if lagging >= cooling.WARM_LEAD_CHECKS:
                        note = (f"sensor stopped following the setpoint at "
                                f"{temp:.1f} °C — already at ambient")
                        break
                    continue          # hold the schedule; do not open the gap wider
                lagging = 0
                setpoint = cooling.warm_next_setpoint_c(setpoint, ambient_c, rate)
                try:
                    # ``True`` here is not a mistake: every step RE-ASSERTS the
                    # cooler as on at the new setpoint. That is the whole
                    # mechanism — the TEC stays engaged and does the warming,
                    # under control, right up until the last line of this
                    # routine. It also means a warm started against an
                    # already-off cooler that is still cold re-engages the TEC to
                    # walk it up, which is deliberate: a cold sensor is a cold
                    # sensor however it got that way.
                    await asyncio.wait_for(cam.set_cooler(True, setpoint),
                                           cooling.WARM_CMD_TIMEOUT_S)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # A driver that will not take a setpoint cannot be ramped.
                    # Finish the warm rather than sit here holding it cold.
                    note = f"cooler refused a setpoint ({e}) — finishing the warm"
                    bus.log("warning", f"warm ramp: {note}", "camera")
                    break
                state["setpoint_c"] = setpoint
                state["elapsed_s"] = elapsed
                state["eta_s"] = max(0.0, (ambient_c - setpoint) / rate * 60.0)
                if setpoint >= ambient_c - 1e-6:
                    if temp is None or temp >= ambient_c - cooling.WARM_MAX_LEAD_C:
                        break
                    # setpoint is at ambient but the sensor is still well below
                    # it: keep polling — the lead check above ends this within
                    # two more steps, and the budget backstops that.
                if elapsed > budget_s:
                    note = "warm ramp ran out of time — switching the cooler off"
                    bus.log("warning", note, "camera")
                    break
            # Only NOW does the TEC actually stop. Everything above exists so
            # that this line is a no-op thermally instead of a 5 °C/min plunge
            # into the room.
            try:
                await asyncio.wait_for(cam.set_cooler(False),
                                       cooling.WARM_CMD_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                note = f"ramp finished but the cooler would not switch off: {e}"
                bus.log("error", note, "camera")
            else:
                bus.log("info", f"camera warm finished — {note}; cooler off",
                        "camera")
        except asyncio.CancelledError:
            # The canceller owns the end state (cool_camera re-cools; cancel_warm
            # with finalize switches off). Just record that we stopped.
            raise
        except Exception as e:
            # Anything unexpected in here would otherwise die as an unretrieved
            # task exception and leave the camera holding a mid-ramp setpoint
            # forever, with the UI still saying "warming". The warm still ends.
            note = f"warm ramp failed: {e}"
            bus.log("error", f"{note} — switching the cooler off", "camera")
            with contextlib.suppress(Exception):
                await asyncio.wait_for(cam.set_cooler(False),
                                       cooling.WARM_CMD_TIMEOUT_S)
        finally:
            if self._warm_state is state and state.get("active"):
                state["active"] = False
                state["note"] = note
                state["eta_s"] = 0
                state["_finished_monotonic"] = time.monotonic()

    async def learn_filter_offsets(self, ref_slot: int | None = None,
                                   exposure_s: float = 2.0, gain: int = 120,
                                   step: int = 350, steps_each_side: int = 4,
                                   binning: int = 2) -> dict:
        """Autofocus every filter slot and persist the ref-relative offsets.

        A slot whose autofocus FAILS (a starless narrowband slot is the usual
        cause) KEEPS its prior offset and is reported in ``kept`` — writing a
        bogus 0 there would defocus that filter on every future exposure."""
        from .focus.autofocus import run_autofocus
        from .focus.filter_offsets import default_ref_slot, offsets_from_positions
        fw = self.require("filterwheel")
        foc = self.require("focuser")
        cam: Camera = self.require("camera")
        names = list(fw.filter_names or [])
        n_slots = len(names)
        if n_slots == 0:
            raise DeviceError("filter wheel reports no slots")
        # A blackout slot has no light path, so autofocus through it can only
        # fail — skip it outright rather than burn a full sweep discovering that.
        blackout = [i for i in range(n_slots) if fw.is_opaque(i)]
        if len(blackout) == n_slots:
            raise DeviceError("every slot on this wheel is marked blackout — "
                              "there is nothing to focus through")
        if ref_slot is None:
            ref_slot = default_ref_slot(names, await fw.get_position())
            if ref_slot in blackout:      # current position IS the dark slot
                ref_slot = next(s for s in range(n_slots) if s not in blackout)
        ref_slot = int(ref_slot)
        if not 0 <= ref_slot < n_slots:
            raise DeviceError(
                f"reference slot {ref_slot} is outside the wheel (0..{n_slots - 1})")
        if ref_slot in blackout:
            raise DeviceError(
                f"{names[ref_slot] or ref_slot} is a blackout slot — it passes no "
                "light, so offsets cannot be measured against it")
        prior = list(fw.filter_offsets) if fw.filter_offsets else [0] * n_slots

        best_by_slot: dict[int, int] = {}
        bus.publish("filter_offsets", state="running", slot=None, of=n_slots,
                    done_slots=[])
        try:
            # Reference first: without it there is nothing to measure against,
            # so a failed reference aborts before burning time on the rest.
            for i in [ref_slot] + [s for s in range(n_slots)
                                   if s != ref_slot and s not in blackout]:
                bus.publish("filter_offsets", state="running", slot=i,
                            of=n_slots, name=names[i],
                            done_slots=sorted(best_by_slot))
                await fw.set_position(i)
                try:
                    res = await run_autofocus(
                        cam, foc, exposure_s=exposure_s, gain=gain, step=step,
                        steps_each_side=steps_each_side, binning=binning,
                        expose_guard=self.exposure_guard, hub=self)
                except asyncio.CancelledError:
                    raise
                except Exception as e:   # one bad slot must not kill the loop
                    bus.log("warning", f"autofocus failed on "
                                       f"{names[i] or i}: {e} — keeping its "
                                       "existing offset", "filter_offsets")
                    if i == ref_slot:
                        raise DeviceError(
                            f"autofocus failed on the reference filter "
                            f"{names[i] or i}: {e}") from e
                    continue
                if getattr(res, "success", False):
                    best_by_slot[i] = int(res.best_position)
                else:
                    if i == ref_slot:
                        raise DeviceError(
                            "autofocus did not succeed on the reference filter "
                            f"{names[i] or i} — offsets cannot be measured")
                    bus.log("warning", f"no focus on {names[i] or i} — keeping "
                                       "its existing offset", "filter_offsets")
            offsets, kept = offsets_from_positions(best_by_slot, ref_slot,
                                                   n_slots, prior)
        except asyncio.CancelledError:
            bus.publish("filter_offsets", state="failed", slot=None,
                        of=n_slots, error="cancelled")
            raise
        except Exception as e:
            bus.publish("filter_offsets", state="failed", slot=None,
                        of=n_slots, error=str(e))
            raise
        # Re-send the blackout flags so set_filter_names zeroes their offsets:
        # `offsets_from_positions` carries a skipped slot's PRIOR value forward,
        # and a stale offset on a slot that passes no light is one autofocus
        # would still apply.
        result = await self.set_filter_names(names, offsets,
                                             list(fw.filter_opaque) or None)
        kept = [k for k in kept if k not in blackout]
        bus.publish("filter_offsets", state="done", slot=None, of=n_slots,
                    ref_slot=ref_slot, offsets=list(result["offsets"]),
                    kept=kept, blackout=blackout, done_slots=sorted(best_by_slot))
        bus.log("info", f"filter offsets learned against "
                        f"{names[ref_slot] or ref_slot}"
                        + (f"; kept prior for {len(kept)} slot(s)" if kept else ""),
                "filter_offsets")
        return {"ref_slot": ref_slot, "offsets": result["offsets"],
                "names": result["names"], "kept": kept}

    async def set_filter_names(self, names: list[str],
                               offsets: list[int] | None = None,
                               opaque: list[bool] | None = None) -> dict:
        """Apply + persist user filter slot names (and optional focuser offsets
        and blackout flags) for the active profile (UX-05). Blank names keep the
        hardware fallback for that slot. Returns the resulting names/offsets/
        opaque flags."""
        fw = self.require("filterwheel")
        base = list(fw.filter_names) if fw.filter_names else [""] * len(names)
        for i in range(len(base)):
            if i < len(names) and str(names[i]).strip():
                base[i] = str(names[i]).strip()
        fw.filter_names = base
        if offsets:
            cur = list(fw.filter_offsets) if fw.filter_offsets else [0] * len(base)
            for i in range(len(cur)):
                if i < len(offsets):
                    try:
                        cur[i] = int(offsets[i])
                    except (TypeError, ValueError):
                        pass
            fw.filter_offsets = cur
        if opaque is not None:
            # Sent whole (not merged per-index like names): the caller owns the
            # full list, so clearing the last blackout flag has to be expressible.
            fw.filter_opaque = [bool(opaque[i]) if i < len(opaque) else False
                                for i in range(len(base))]
            # A blackout slot has no light path, so a focus offset through it is
            # meaningless — zero it rather than leave a stale number that
            # autofocus would apply.
            if fw.filter_offsets:
                fw.filter_offsets = [0 if fw.is_opaque(i) else o
                                     for i, o in enumerate(fw.filter_offsets)]
        from .config import config_store, save_filter_config
        save_filter_config(config_store.cfg().active_profile_id,
                           fw.filter_names, fw.filter_offsets,
                           list(fw.filter_opaque) or None)
        return {"names": fw.filter_names, "offsets": fw.filter_offsets,
                "opaque": list(fw.filter_opaque)}

    def _counter_file(self) -> Path:
        # under CAPTURE_DIR (the persistent image library; auto-isolated by the
        # CAPTURE_DIR monkeypatch every test already applies). Resolved live so
        # the monkeypatch is honored.
        return CAPTURE_DIR / ".frame_counters.json"

    def _next_frame_counter(self, key: str) -> int:
        """Persisted, per-target, monotonic frame number. Captures are serialized
        by the exposure guard, so no lock is needed."""
        data = read_json_or(self._counter_file(), {})
        if not isinstance(data, dict):
            data = {}
        n = int(data.get(key, 0) or 0) + 1
        data[key] = n
        write_json_atomic(self._counter_file(), data)
        return n

    def _capture_path(self, target: str, frame_type: str, filter_name: str = "",
                      *, gain: int | None = None, exposure_s: float | None = None,
                      binning: int | None = None,
                      sensor_temp_c: float | None = None) -> Path:
        from .naming import capture_tokens, render_relative_path, sanitize_component
        # "untargeted" fallback keyed off the SANITIZED target (legacy parity,
        # hub.py old :1681); sanitize is idempotent so the engine re-sanitize is a
        # no-op.
        safe_target = sanitize_component(target, "loose") or "untargeted"
        n = self._next_frame_counter(safe_target)
        t = time.localtime()
        night = time.localtime(time.time() - 12 * 3600)   # noon-rollover night date
        fields = {
            "TARGET": safe_target,
            "FRAMETYPE": frame_type,
            "FILTER": filter_name or "",
            "DATE": time.strftime("%Y-%m-%d", t),
            "TIME": time.strftime("%H%M%S", t),
            "DATETIME": time.strftime("%Y-%m-%d_%H%M%S", t),
            "NIGHT": time.strftime("%Y-%m-%d", night),
            "FRAMENR": f"{n:04d}",
            # Capture-settings tokens ($$GAIN$$/$$EXPOSURE$$/$$BINNING$$/
            # $$SENSORTEMP$$). Passed in from capture() rather than re-read off
            # the camera so the name always describes THIS frame. Omitted (None)
            # -> empty -> the token drops out, which is what every non-capture
            # caller gets.
            **capture_tokens(gain=gain, exposure_s=exposure_s, binning=binning,
                             sensor_temp_c=sensor_temp_c),
        }
        template = config_store.cfg().naming.template
        return CAPTURE_DIR / render_relative_path(template, fields)

    async def start_loop(self, exposure_s: float, gain: int, offset: int,
                         binning: int = 1, frame_type: str = "Light") -> None:
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
                    await self.capture(exposure_s, gain, offset, binning,
                                       frame_type=frame_type)
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

    async def yield_camera_for(self, what: str) -> bool:
        """Take the camera off the live preview loop so ``what`` — a path that
        must plate-solve — can expose. Returns True if anything was stopped.

        THE FAILURE THIS EXISTS FOR (observed on the rig, server log verbatim):

            00:48:01  Live View on - stacking subs
            00:50:45  goto cancelled
            00:51:36  loop capture failed: camera is busy (plate solve); capture light refused
            00:51:37  loop capture failed: camera is busy (plate solve); capture light refused
            00:51:41  solve cancelled

        ``exposure_guard`` is deliberately NON-blocking: whoever finds the lock
        held gets a ``DeviceError`` instead of queueing behind it. That is the
        right rule for two one-shot callers, but a free-running live loop is not
        a one-shot caller — it comes back every few seconds, forever. So a
        centering run and the loop simply trade the refusal back and forth, and
        in ``goto_and_center`` a losing solve DEGRADES to "using raw GoTo": the
        centering loop returns early and the slew stops wherever it happened to
        be. The mount was left at Dec +10 deg, alt 24 deg — not the home
        position the user had asked for, not the target, nowhere anyone chose.
        A telescope parked on an arbitrary patch of sky is the harm; the log
        lines above are only how it announced itself.

        Plain motion is NOT affected and is deliberately left alone: with the
        loop running, ``POST /api/mount/home`` returned 200 and logged "mount
        homed" on the same rig, because homing never touches the camera. Only
        the camera-owning paths need this.

        THE LOOP DOES NOT RESTART AFTERWARDS — deliberately. Reasons, in order:

        * It matches the one existing precedent for "a long job is taking the
          camera now": ``POST /api/sequence/start`` (api/app.py) calls
          ``stop_loop_and_wait()`` and never restarts the loop when the sequence
          ends. Two different answers to the same question would be worse than
          either answer.
        * Restarting is not obviously right. ``goto_and_center`` repoints the
          telescope; frames streaming in after it are of a DIFFERENT field, so a
          silently-resumed Live View would look like the old target still
          drifting. Handing the user a stopped loop and letting them press Live
          again is the honest end state.
        * Restarting is also not reliably possible: the loop's exposure/gain/
          offset/binning live in the ``start_loop`` closure, not on the hub, so
          "resume what was running" would mean inventing settings.

        Because it stays stopped, it has to be VISIBLE — a live view that dies
        without saying so is its own complaint. Two things make it visible:
        ``stop_loop`` publishes ``capture_loop running=False`` (the Loop button
        drops out immediately), and Live View is disarmed too. That second part
        is load-bearing: the Capture screen's Live toggle renders from
        ``status.live_stack_active`` (server truth), so leaving the stacker
        armed while nothing feeds it would leave the button lit over a stack
        that never grows again. This is exactly what ``POST
        /api/capture/livestack/stop`` does — ``stop_loop`` then
        ``stop_live_stack`` — so the user lands in a state the UI already knows
        how to render. The log line below is the third: it names what took the
        camera, so the disappearance has a stated cause.

        THE BAHTINOV FOCUS AID IS YIELDED TOO, AND THAT WAS A CLOSE CALL (carry-
        in from the review of 797588b, which left it out). ``POST
        /api/focuser/bahtinov/start`` starts the capture loop if one is not
        already running, so the aid is a camera-owning path exactly like Live
        View — but the user's relationship to it is the opposite. Live View is
        scenery someone forgets is running; the Bahtinov aid is a number they
        are staring at with a hand on the focuser, and stopping that under them
        is not obviously a kindness. The alternative was the answer autofocus
        and coarse focus give: refuse the OTHER job with a 409 and let the aid
        keep the camera. It was rejected for four reasons, in order of weight:

        * The yield-class callers are not all interactive. ``goto_and_center``
          is called by the sequence engine and by ``meridian_flip``, both
          unattended and both mid-night. Refusing there is not a polite "not
          now" — it aborts a target, or strands the tube on the wrong side of
          the pier at the flip. A focus aid somebody forgot to disarm must never
          be able to cost a night; that is the same harm 797588b and b670856
          were written to stop, and protecting the aid would have reintroduced
          it on the paths that matter most.
        * Refusing would not even be a smaller action. The aid has no camera of
          its own — it rides the live loop's frames — so anything that stops the
          loop stops the aid regardless. The choice was never "stop the aid or
          not"; it was "stop the whole run, or stop the aid and say so".
        * An aid left ARMED while nothing feeds it is worse here than for Live
          View. A frozen picture looks frozen; a frozen focus verdict looks like
          a live reading that has stopped responding to the focuser, and that is
          a number the user acts on. ``status.bahtinov_active`` staying true
          would be the lie.
        * The 409 answer already exists where it belongs, and the aid already
          wins it: autofocus and coarse focus refuse on ``hub.looping``, and an
          armed aid is looping. Nothing is given up by not adding a second one.

        (A Bahtinov MASK on the aperture would very likely make the solve that
        takes the camera fail anyway. That is not a reason to refuse: nothing
        here can tell whether the mask is physically on the scope, only that the
        aid is armed, and a failed solve already degrades to a raw GoTo.)"""
        was_looping = self.looping
        had_stack = self.live_stacker is not None
        had_bahtinov = self.bahtinov is not None
        # The aid is included in the "is there anything to do" test, not just in
        # the teardown: it can outlive the loop that fed it (POST
        # /api/capture/stop leaves it armed), and in that state it is already
        # reporting active over a dead frame source.
        if not (was_looping or had_stack or had_bahtinov):
            return False
        if was_looping:
            # AWAITED, not fire-and-forget: the loop's in-flight ``expose`` has
            # to release the capture lock before we take it, or the very first
            # solve races the corpse of the loop and 409s — which is the bug.
            await self.stop_loop_and_wait()
        if had_stack:
            self.stop_live_stack()
        if had_bahtinov:
            self.disarm_bahtinov()
        # Name everything that stopped. "Live View stopped" while a Bahtinov
        # readout also went dark would be a half-truth, and the half it leaves
        # out is the one the user's hand was on.
        names = [n for n, on in (("Bahtinov focus aid", had_bahtinov),
                                 ("Live View", had_stack)) if on] or ["Loop capture"]
        # How to get it back. The aid is armed over the API (there is no button
        # for it on the Focus screen today), so it gets "arm it again" rather
        # than the name of a control that is not on screen to be pressed.
        back = ("arm the Bahtinov focus aid again" if had_bahtinov
                else "press Live again" if had_stack else "press Loop again")
        bus.log("warning",
                f"{' and '.join(names)} stopped: {what} needs the camera to "
                f"plate-solve — {back} when it finishes.",
                # Routed to the focus channel when the aid was the casualty, so
                # the explanation lands next to "Bahtinov focus aid on".
                "focus" if had_bahtinov else "capture")
        return True

    @property
    def looping(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    # -------------------------------------------------------- Live View (NOV-1)
    def start_live_stack(self, reject_frac: float = 0.08,
                         clip_sigma: float = 4.0) -> dict:
        from .imaging import LiveStacker
        self.live_stacker = LiveStacker(reject_frac=reject_frac,
                                        clip_sigma=clip_sigma)
        bus.log("info", "Live View on — stacking subs", "capture")
        return {"active": True, "clip_sigma": clip_sigma}

    def reset_live_stack(self) -> dict:
        if self.live_stacker is not None:
            self.live_stacker.reset()
        return {"active": self.live_stacker is not None,
                "frames": getattr(self.live_stacker, "frames", 0)}

    def stop_live_stack(self) -> dict:
        self.live_stacker = None
        return {"active": False}

    # ---------------------------------------------------- Bahtinov focus aid

    def arm_bahtinov(self, tol_px: float = 1.5, invert: bool = False) -> dict:
        """NOV-12: arm the per-frame Bahtinov analysis (additive preview.bahtinov)."""
        self.bahtinov = {"tol_px": float(tol_px), "invert": bool(invert)}
        bus.log("info", "Bahtinov focus aid on", "focus")
        return {"active": True}

    def disarm_bahtinov(self) -> dict:
        self.bahtinov = None
        return {"active": False}

    # -------------------------------------------------------- solve & center

    async def solve_and_sync(self, exposure_s: float = 3.0, *,
                             blind: bool = False) -> dict:
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
        # Only NOW take the camera off the live loop. Ordering is deliberate:
        # ``pick_solver`` raises in <1 ms on a rig that has nothing trustworthy
        # to solve with, and killing the user's Live View for a solve that was
        # never going to run would be a pointless amputation. Everything above
        # this line either raises or is free.
        await self.yield_camera_for("plate solve")
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
        if blind:
            # DELIBERATELY THROW THE MOUNT'S HINT AWAY.
            #
            # The hint drives ASTAP's NEAR search, which is right for centering:
            # the mount is already close, and a tight search is fast. It is
            # exactly wrong after a restart, where the premise is that the
            # mount's idea of where it points may be false. Hinting the search
            # with a wrong position makes the solve fail precisely when it is
            # needed most, and a failed solve is read as "cannot verify the sky".
            #
            # Measured on the rig 2026-08-02: after the server was killed while
            # tracking, the mount reported RA 18h53.6m Dec +33d01', while ASTAP
            # solved the very same frame at RA 18h35.2m Dec +33d39' -- about 4
            # degrees out. The hinted solve had been failing with "no solution"
            # while a hintless run on that identical file solved instantly.
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

    async def rotate_to_pa(self, target_pa_deg: float,
                           exposure_s: float = 3.0,
                           max_attempts: int = 5) -> dict:
        """Solve→sync→rotate loop (NINA §11.3 parity, bounded): physically
        enforce a sky position angle. Syncs the ROTATOR only (never the mount).
        The solver is resolved up front (motion-guarded — a sim solver can
        never drive a real rotator) and a solve failure raises DeviceError;
        goto_and_center degrades it to rotation_skipped."""
        rot = self.require("rotator")
        cam: Camera = self.require("camera")
        from . import providers as _providers
        solver = _providers.pick_solver(self)
        rcfg = config_store.cfg().rotator
        target = _rotation.mod360(target_pa_deg)
        adjusted_to = None
        moved = False
        error = None
        orientation = None
        epoch = self._motion_epoch
        # Same camera conflict as solve_and_sync: every attempt below exposes
        # through ``exposure_guard``, so a free-running live loop would refuse
        # them one by one and the rotate would raise "plate solve failed" with a
        # perfectly healthy solver. Placed AFTER the epoch snapshot on purpose —
        # snapshotting after an await would read an epoch an abort had already
        # bumped, and the fence check at the top of the loop would then wave the
        # aborted rotation through. (Normally a no-op: goto_and_center yields the
        # camera before it calls us; this covers the direct /api/rotator route.)
        await self.yield_camera_for("rotate to PA")
        for attempt in range(1, max_attempts + 1):
            # This fence gates only the NEXT attempt's dispatch below; it does
            # NOT cancel an in-flight ``rot.move_to`` from a PRIOR attempt —
            # that relies on task cancellation -> device halt-on-cancel, the
            # same contract ``tel.slew`` uses (W3.7).
            if not self._motion_committed_clean(epoch):
                bus.log("warning", "rotate abandoned: aborted", "rotator")
                return {"rotated": False, "aborted": True,
                        "pa_deg": orientation, "adjusted_to": adjusted_to,
                        "attempts": attempt - 1, "error_deg": error}
            tel = self.devices.get("telescope")
            ra_hint = dec_hint = None
            if tel is not None and tel.connected:
                try:
                    ra_hint, dec_hint = await tel.get_position()
                    if ra_hint is not None:
                        ra_hint, dec_hint = await self.from_mount_frame(
                            tel, ra_hint, dec_hint)
                except Exception:
                    ra_hint = dec_hint = None
            async with self.exposure_guard("rotate to PA"):
                frame = await cam.expose(exposure_s, 200, 30, binning=2)
            self.last_frame = frame
            await self._publish_preview(frame)
            tmp = CAPTURE_DIR / "_solve" / "rotate.fits"
            await asyncio.to_thread(
                save_fits, frame, tmp,
                ra_hours=ra_hint, dec_deg=dec_hint, instrument=cam.name)
            opt = self.effective_optics()
            result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                        fov_deg_hint=opt["fov_h_deg"] or None)
            if not result.success:
                raise DeviceError(f"rotate: plate solve failed: {result.message}")
            orientation = _rotation.mod360(result.rotation_deg)
            await rot.sync(orientation)
            mech = await rot.get_mechanical_position()
            prev = target
            target = _rotation.map_sky_target(prev, mech, rot.sync_offset_deg,
                                              rcfg.range_type,
                                              rcfg.range_start_deg)
            if not _rotation.angle_equals(target, prev, 0.1):
                # a ±90°/±270° adjustment genuinely changes framing (only ±180
                # is equivalent) — surface it, never silently (spec §3.3).
                adjusted_to = target
                bus.log("warning",
                        f"rotator: target PA {prev:.1f}° adjusted to "
                        f"{target:.1f}° by the {rcfg.range_type} mechanical "
                        f"range", "rotator")
            distance = _rotation.shortest_rotation(target, orientation,
                                                   rcfg.range_type)
            error = abs(((distance + 90.0) % 180.0) - 90.0)  # mod-180 magnitude
            bus.publish("rotator", action="rotating", attempt=attempt,
                        orientation_deg=round(orientation, 2),
                        target_deg=round(target, 2))
            if _rotation.angle_equals_mod180(distance, 0.0, rcfg.tolerance_deg):
                bus.publish("rotator", action="rotated",
                            pa_deg=round(orientation, 2))
                if moved and self.guider and self.guider.connected:
                    bus.log("warning",
                            "camera rotated — guide calibration may be stale; "
                            "PHD2 will re-calibrate or flip as needed", "guide")
                return {"rotated": True, "pa_deg": orientation,
                        "adjusted_to": adjusted_to, "attempts": attempt,
                        "error_deg": round(error, 2)}
            await rot.move_to(_rotation.mod360(orientation + distance))
            moved = True
        last_error = f"(last error {error:.1f}°)" if error is not None else "(no attempts ran)"
        raise DeviceError(
            f"rotator failed to converge after {max_attempts} attempts "
            f"{last_error}")

    async def goto_and_center(self, ra_hours: float, dec_deg: float,
                              tolerance_deg: float = 0.02,
                              max_attempts: int = 3,
                              solve_exposure_s: float = 3.0,
                              rotation_deg: float | None = None) -> dict:
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
        # Take the camera off the live loop BEFORE the mount moves, not lazily
        # at the first solve. This is the fix for the half-finished slew: the
        # centering loop treats a failed solve as "degrade to a raw GoTo and
        # return", so if the loop is still holding the camera when attempt 1
        # solves, ``goto_and_center`` gives up after one uncorrected slew and
        # leaves the tube pointed wherever that landed. Stopping first means the
        # solve fails only for real reasons (clouds, no stars, no solver).
        #
        # Deliberately AFTER the epoch snapshot above: snapshotting after this
        # await would capture an epoch that an abort landing mid-teardown had
        # already bumped, and the fence immediately below would then read
        # "clean" and slew a mount the user had just stopped. Snapshot first,
        # yield, then let the existing fence catch the abort. Deliberately after
        # ``_check_solar`` too — a goto that is about to be refused for pointing
        # near the Sun has no business killing the user's Live View first.
        await self.yield_camera_for("centering (goto & plate solve)")
        async with self._motion_lock:
            if not self._motion_committed_clean(epoch):
                bus.log("warning", "goto abandoned: aborted before motion", "mount")
                return {"centered": False, "error_arcmin": None,
                        "attempts": 0, "aborted": True, "rotation": None}
            if await tel.is_parked():
                await tel.unpark()
            await tel.set_tracking(True)
        # Rotate BEFORE centering (NINA CenterAndRotate order, spec §3.4): slew
        # once so the solved field is the target's, run the rotate loop, then
        # fall through to the normal centering attempts (which re-slew anyway).
        # A rotate failure DEGRADES — never abort a slew that already happened.
        rotation_result: dict | None = None
        rotation_skipped = False
        rot = self.devices.get("rotator")
        if rotation_deg is not None and rot is not None and rot.connected:
            async with self._motion_lock:
                if not self._motion_committed_clean(epoch):
                    bus.log("warning", "goto abandoned: aborted before rotation",
                            "mount")
                    return {"centered": False, "error_arcmin": None,
                            "attempts": 0, "aborted": True, "rotation": None}
                slew_ra, slew_dec = await self.to_mount_frame(tel, ra_hours, dec_deg)
                await tel.slew(slew_ra, slew_dec)
            try:
                rotation_result = await self.rotate_to_pa(
                    rotation_deg, exposure_s=solve_exposure_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                bus.log("warning",
                        f"rotation to PA {rotation_deg:.0f}° failed ({e}); "
                        f"continuing without rotation", "rotator")
                rotation_skipped = True
        _rot_keys = {"rotation": rotation_result,
                     **({"rotation_skipped": True} if rotation_skipped else {})}
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
                            "attempts": attempt - 1, "aborted": True} | _rot_keys
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
                        "attempts": attempt, "solve_failed": True} | _rot_keys
            err = _ang_sep_deg(solved["ra_hours"], solved["dec_deg"], ra_hours, dec_deg)
            last_err = err
            bus.log("info", f"centering attempt {attempt}: {err * 60:.1f}' off target", "solve")
            if err <= tolerance_deg:
                bus.publish("mount", action="centered", error_arcmin=err * 60)
                return {"centered": True, "error_arcmin": err * 60, "attempts": attempt} | _rot_keys
        return {"centered": False, "error_arcmin": (last_err or 0) * 60,
                "attempts": max_attempts} | _rot_keys

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
            # NOTE: deliberately NOT routed through the sim fast-path knob. This
            # is an UNBOUNDED background loop; zeroing its cadence turns it into a
            # tight ``await asyncio.sleep(0)`` busy-spin that pegs a core for the
            # whole life of any test that starts the poller (and no test awaits
            # this cadence, so faking it saves no runtime). Real cadence only.
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
                    self.publish_safety(self._safety_reading_dict(reading))
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
            # The long-running operations actually in flight RIGHT NOW, by name
            # ("autofocus", "goto", "polar", ...). Without this the client has no
            # way to learn that something it saw start has since ended: progress
            # is delivered only as live events, so a WebSocket that drops across
            # the terminal tick — routine on a phone over a relay at night —
            # leaves the UI showing a sweep that finished forty minutes ago,
            # with Halt as the only way out. Observed 2026-07-30.
            "busy": sorted(k for k, t in (self._busy or {}).items()
                           if t is not None and not t.done()),
        }

    async def poll_status(self) -> dict:
        out: dict[str, Any] = {"connected": self.summary()["devices"],
                               "looping": self.looping, "mode": self.mode}
        out["live_stack_active"] = self.live_stacker is not None   # NOV-1 server truth
        out["bahtinov_active"] = self.bahtinov is not None         # NOV-12 server truth
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
            # Additive: the guide row carries the override values actually
            # SELECTABLE on this rig, so the Guide view offers only what applies
            # (review I1), never a no-op vocabulary option.
            #
            # ``options`` is the same answer with the BLOCKED values kept and a
            # reason attached, which is what the client needs to render the house
            # honest-disabled row ("AstroDeck native needs a guide camera
            # assigned and connected") instead of just omitting the option. Both
            # keys come from ONE server-side predicate — an offer list computed
            # separately from the resolver is exactly how the dropdown came to
            # offer a provider that `_resolve_guide` then threw away.
            guide_row = out["providers"].get("guide")
            if isinstance(guide_row, dict):
                guide_row["options"] = _providers.guide_provider_options(self)
                guide_row["eligible"] = _providers.guide_eligible_providers(self)
        except Exception as e:
            out["providers"] = {
                cap: {"kind": "unavailable", "label": "Unavailable",
                      "reason": f"resolution error: {e}"}
                for cap in ("autofocus", "polar_align", "solve")}
        try:
            du = shutil.disk_usage(CAPTURE_DIR)
            free_gb = du.free / 1e9
            # UX #32: NAME the volume. "88 GB free" of what, on a box with an SD
            # card and a USB disk, is not an answer — and the capture root is an
            # env var (ASTRODECK_CAPTURE_DIR) with no other readout in the product.
            out["disk"] = {"free_gb": round(free_gb, 1),
                           "low": free_gb < 10, "critical": free_gb < 1,
                           "capture_dir": str(CAPTURE_DIR),
                           "total_gb": round(du.total / 1e9, 1)}
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
                # alt/az (and the meridian hour-angle below) are of-date quantities,
                # so they stay on the raw apparent (JNOW) position the mount reports.
                alt, az = altaz(ra, dec, self.site["latitude"], self.site["longitude"])
                # The RA/Dec PUBLISHED in status are canonical J2000: Atlas survey
                # tiles, the FOV overlay and Send-to-Plan all consume them as J2000.
                # A real Alpaca mount reports JNOW, so bring it back here (UX-13);
                # a no-op for sim / NINA / J2000-reporting mounts.
                ra_j2000, dec_j2000 = await self.from_mount_frame(tel, ra, dec)
                out["mount"] = {
                    "ra_hours": ra_j2000, "dec_deg": dec_j2000,
                    "ra_str": format_ra(ra_j2000), "dec_str": format_dec(dec_j2000),
                    "alt": round(alt, 1), "az": round(az, 1),
                    "tracking": await tel.get_tracking(),
                    "parked": await tel.is_parked(),
                    "slewing": await tel.is_slewing(),
                    "tracking_rate": await tel.get_tracking_rate(),
                    "can_set_tracking_rate": tel.can_set_tracking_rate,
                    # Home control (2026-07-30): the client gates its Home
                    # button on this, so a mount with no home sensor never shows
                    # a control that would 400.
                    "can_find_home": getattr(tel, "can_find_home", False),
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
            else:
                # Its OWN try, deliberately: `moving` is the newest and least
                # universally-supported reading here, and it must never be able
                # to cost the position/max/temperature readouts the user is
                # actually looking at. Absent key == "this backend cannot say".
                try:
                    out["focuser"]["moving"] = bool(await foc.is_moving())
                except Exception:
                    pass
                # Static capability, not a reading — the UI needs it to decide
                # whether to offer re-anchoring at all.
                out["focuser"]["can_set_position"] = bool(
                    getattr(foc, "can_set_position_reference", False))
        fw = self.devices.get("filterwheel")
        if fw and fw.connected:
            try:
                pos = await fw.get_position()
                names = list(fw.filter_names or [])
                out["filterwheel"] = {
                    "position": pos,
                    "names": names,
                    "offsets": fw.filter_offsets,
                    # UX #1: the RESOLVED name of the slot the wheel is on — the
                    # same string the FITS FILTER card and the report get. Preflight
                    # reads this to fail an unfiltered Light step on a rig that
                    # HAS a wheel, instead of reporting "Filters — NOT NEEDED".
                    "current": (str(names[pos] or "")
                                if pos is not None and 0 <= pos < len(names) else ""),
                    # Blackout flags, parallel to names. The UI needs these to
                    # keep an opaque slot out of Light/Flat pickers and to say
                    # why the current frame is unfiltered.
                    "opaque": list(fw.filter_opaque or []),
                    "dark_slot": fw.dark_slot(),
                }
            except Exception:
                pass
            else:
                # Its OWN try, for the same reason focuser.moving has one above:
                # `moving` is the newest reading here and the least universally
                # supported, and it must never be able to cost the slot names
                # and position the user is actually looking at. Absent key ==
                # "this backend cannot say", which the client treats as
                # "watch the position instead" rather than as "not moving".
                try:
                    out["filterwheel"]["moving"] = bool(await fw.is_moving())
                except Exception:
                    pass
        # UX #27: roof/dome state on the status surface. The roof closing was
        # visible only in Settings -> Safety, so the dashboard said nothing while
        # the observatory shut itself. Cheap: one cached-ish shutter read, fully
        # guarded like every other block here.
        dome = self.devices.get("dome")
        if dome is not None and getattr(dome, "connected", False):
            try:
                st = await dome.shutter_state()
                out["dome"] = {
                    "name": dome.name,
                    "shutter": st.value,
                    "requires_park_before_close": bool(
                        getattr(dome, "requires_park_before_close", True)),
                    "can_slave": bool(getattr(dome, "can_slave", False)),
                }
            except Exception:
                pass
        rot = self.devices.get("rotator")
        if rot and rot.connected:
            try:
                out["rotator"] = {
                    "name": rot.name,
                    "sky_deg": round(await rot.get_position(), 2),
                    "mech_deg": round(await rot.get_mechanical_position(), 2),
                    "moving": await rot.is_moving(),
                    "synced": rot.synced,
                    "can_reverse": rot.can_reverse,
                    "reverse": await rot.get_reverse(),
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
                    "max_bin": getattr(cam, "max_bin", 4),
                    # photometry/SNR design Task 7: e-/ADU at the current gain, when
                    # the backend knows it (native adapters only); 0.0 = unknown.
                    # Additive/default-inert — old clients simply ignore the field.
                    "egain": getattr(cam, "egain", 0.0),
                    # Additive: MEASURED e-/ADU per gain setting (auto-learn).
                    # Advanced-UI only; the driver value above always wins.
                    "egain_learned": {str(g): v
                                      for g, v in self._egain_learned.items()},
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
            # Warm-down ramp progress (2026-08-04). Deliberately OUTSIDE the try
            # above: the cooler probe is the flakiest call in this block, and the
            # one moment the user most needs to see "warming, 6 min to go" is the
            # moment a camera is mid-teardown and answering slowly. Attached only
            # when a camera dict was actually built, so the UI never receives a
            # half-populated camera object.
            warm = self.warm_state()
            if warm is not None and "camera" in out:
                out["camera"]["warm"] = warm
        if self.guider and self.guider.connected:
            out["guider"] = self.guider.stats().__dict__ | {"name": self.guider.name}
        # Additive guide-camera descriptor so ConnectView can show the guiding
        # device with a name + connected state in every backend (not just sim's
        # dedicated guide_camera device).
        gc = self._guide_camera_info()
        if gc is not None:
            # Why the reason rides status and not just the 404: the preview panel
            # renders an <img>, so the only thing it can observe about a failure
            # is that the load errored — every named refusal the route produces
            # ("no guide camera assigned", "could not deliver a frame: …")
            # collapses into one generic sentence on the way. Absent key == no
            # recent refusal to report.
            note = self.guide_preview_note()
            if note:
                gc = gc | {"preview_reason": note}
            else:
                # The other two states a delivery can be in. `True` = pixels the
                # server decoded and found to vary; `False` = bytes it forwarded
                # without being able to look at them, which is a real thing to
                # know while a rectangle is on screen. Neither key = nobody has
                # asked this camera recently — different again, and only honest
                # as long as a delivered frame says something.
                verdict = self.guide_preview_verdict()
                if verdict is not None:
                    gc = gc | {"preview_ok": verdict}
                    # WHO answered, which the `name` two lines up does not say:
                    # that comes from _guide_camera_info (guide-camera device
                    # first), the picture comes from _guide_preview_source
                    # (connected guider first). A rig with both — every sim rig
                    # — makes them different devices, and the one line the UI
                    # composes itself is the `False` case, reachable only from
                    # the guider. Named from status.name it read "ZWO ASI sent
                    # bytes we could not decode" about a camera nobody asked.
                    src = self.guide_preview_source()
                    if src:
                        gc = gc | {"preview_source": src}
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
        # Record last-known device state so a power cut is DETECTABLE on the way
        # back up. Read off ``out`` rather than re-querying: these values were
        # just measured, and a second round of device reads on the status path
        # would cost more than the feature. Coalesced to one write per 10s and
        # swallows its own errors, so it is safe on this hot path.
        try:
            from .devices import fingerprint as _fp
            _m = out.get("mount") or {}
            _f = out.get("focuser") or {}
            _w = out.get("filterwheel") or {}
            _fp.record(focuser_position=_f.get("position"),
                       filter_slot=_w.get("position"),
                       ra_hours=_m.get("ra_hours"), dec_deg=_m.get("dec_deg"),
                       parked=_m.get("parked"), tracking=_m.get("tracking"))
        except Exception:  # noqa: BLE001 — never break status over bookkeeping
            pass
        return out


def _ang_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    import math
    ra1, ra2 = math.radians(ra1_h * 15), math.radians(ra2_h * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


hub = Hub()
