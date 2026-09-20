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
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from . import cooling
from .config import (config_store, f_ratio, fov_deg, frames_payload,
                     image_scale_arcsec_px, redacted)
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
from .align import guide_offset as _guide_offset
from .events import bus
from .guide import Guider, PHD2Guider
from .imaging import (
    FrameMeta,
    SessionStacker,
    auto_levels,
    cloud_score,
    compute_histogram,
    detect_stars,
    display_histogram,
    grade_frame,
    save_fits,
    stretch_with,
    to_jpeg,
    to_png,
    to_thumb,
    write_wcs,
)
from .imaging.processing import frame_stats, to_png
from .imaging.sessionstack import effective_bayer, normalise_bayer
from .imaging.stackbackfill import plan_backfill, run_backfill
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

#: How long a pier-side reading stays quotable after the mount stops answering.
#: Five minutes: long enough to ride out a serial hiccup or a reconnect, short
#: enough that it cannot outlive somebody flipping the mount by hand while the
#: link is down. Past it the honest answer is "nobody can say".
PIER_SIDE_STALE_S = 300.0
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
        # Do NOT read meaning into the LEVEL. These are post-stretch display
        # values, and a stretch with no dynamic range collapses to 0 whatever
        # the sensor did — so a guide camera staring at a bright daylight sky
        # with every pixel railed at saturation arrives here reading 0, and the
        # old wording ("every pixel reads 0") sent you looking for a dead camera
        # or a closed shutter. Measured on the rig 2026-08-06: raw frame was a
        # uniform 65520 (4095 << 4, i.e. full-scale), reported here as 0.
        return (f"{source_name} returned a uniform image — every pixel identical "
                f"after stretching, so there is no guide field in it. A capped "
                f"scope, a sensor saturated by daylight or a stray light source, "
                f"and a camera returning nothing all look like this. This panel "
                f"only forwards {source_name}'s own view, so what is wrong is "
                f"visible in {source_name}, not here."), (lo, hi)
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
#: How long a JNOW->J2000 precession may be reused for the EXACT same reported
#: coordinates (seconds). See ``Hub.from_mount_frame``: a tracking mount reports
#: one position frame after frame, and the transform moves by well under a
#: milliarcsecond over this window (precession is 50 arcsec a YEAR), so the memo
#: returns the same answer rather than a stale one. Short anyway, because a
#: cheap number that is right is worth more than a free number that might not be.
_PRECESS_MEMO_TTL_S = 60.0

#: rate cap (the server clamp in ``/api/mount/move`` imports this) and the
#: move-axis deadman window. Defined ONCE here so the touch surface and the
#: Batch-4 safety surface share one source of truth — both reuse the single
#: ``_move_watchdog`` task below; neither re-creates these values.
#:
#: SINCE D-RIG-4 THIS IS THE FALLBACK, NOT THE CEILING. A driver that can say
#: how fast it will actually slew publishes ``Telescope.max_rate_deg_s`` (the
#: AM5 reports 1.44 deg/s, its highest MEASURED rate), and the server clamp
#: prefers that number: ``getattr(tel, "max_rate_deg_s", None) or
#: TOUCH_MAX_RATE_DEG_S``. 0.6 is what a mount that cannot say gets - a
#: conservative guess about somebody else's gearbox, which is exactly why it
#: should lose to a measurement whenever one exists.
TOUCH_MAX_RATE_DEG_S = 0.6
MOVE_DEADMAN_MS = 1200

#: Centering "the mount did not move" guard (2026-08-06, on the sky: attempt 1
#: 33.7' off, attempt 2 33.7' off — bit-identical, because the correction slew
#: was not being executed, and the loop burned its remaining attempts saying
#: nothing). If a re-slew changed the pointing by less than this, it did not
#: happen: a correction commands the FULL remaining error and solve noise is
#: arcseconds, so consecutive residuals this close mean the mount is inert.
CENTERING_STUCK_ARCMIN = 0.5
#: ...but only accuse when the commanded correction was LARGE. Near the
#: tolerance a mount legitimately hovers (mechanics are ~arcmin), so the guard
#: stays quiet unless the remaining error is at least this many tolerances.
CENTERING_STUCK_MIN_ERR_FACTOR = 5.0

#: How much a rotate attempt must IMPROVE the position-angle error to earn
#: another attempt. ``rotate_to_pa`` is a solve→move→solve loop with no damping,
#: so an attempt that does not shrink the error is evidence the correction is
#: being applied wrongly (sign, wrap, or mechanical-vs-sky frame), and repeating
#: it just turns the camera further. Measured on the rig 2026-08-08 while
#: slewing to M52: five attempts, error 56.8° -> 76.4°, the camera physically
#: swept a full turn and about half of another, and nothing was reported until
#: the loop gave up. 0.5° sits above plate-solve rotation noise so a genuine
#: slow approach is not mistaken for divergence.
ROTATE_MIN_GAIN_DEG = 0.5

#: Bound on the one pier-side read ``meridian_flip`` takes either side of its
#: re-slew. The same 30 s the sequence engine gives every other mount query
#: (``sequence.engine.MOUNT_QUERY_TIMEOUT_S``); a driver that never answers must
#: degrade to "nobody can say" and leave the flip conservative, never hang it.
PIER_SIDE_QUERY_TIMEOUT_S = 30.0

#: how many full display frames the ring keeps (memory cap on the Pi), how many
#: tiny thumbnails it keeps for the filmstrip, and how many linear arrays it
#: retains for /crop and /render (a 6200 frame is ~125 MB, so only the latest
#: 1–2 — live-preview spec finding #18 / §6).
#: Backlog bound for gallery thumbnail warming (#225). Small on purpose: frames
#: arrive every ~60 s and one render takes ~1.5 s, so a backlog this deep only
#: forms if the box is already in trouble — and a dropped warm costs nothing but
#: a lazy render later.
THUMB_WARM_QUEUE_MAX = 8
#: How long the warm worker waits for another frame before retiring. Longer than
#: any realistic sub, so a normal run keeps one worker rather than churning a
#: task per frame.
THUMB_WARM_IDLE_S = 600.0

PREVIEW_DISPLAY_KEEP = 8
PREVIEW_THUMB_KEEP = 50
PREVIEW_LINEAR_KEEP = 2

#: lane -> the word ``busy_label`` (and therefore ``status.busy``) shows while
#: that lane is live. ORDERED: the first live lane wins, so the most
#: consequential operations come first and a run that is slewing between subs
#: says "slewing" rather than "capturing".
#:
#: A lane in this table blocks a restart (``restart_blocker`` is built from it),
#: suppresses the stale-telemetry banner and buys the NINA link its health grace.
#: That is a lot of meaning for a name in a tuple, and on 2026-08-05 it was
#: bought accidentally: ``POST /api/dome/close`` ran in the ``goto`` lane, so a
#: roof close inherited all three. Splitting the roof onto its own lane kept the
#: mutual exclusion (``_LANE_SUPERSEDES`` in api/app.py) and silently dropped
#: these — measured on the sim: a close in flight left ``restart_blocker`` None
#: and ``GET /api/system/factory-reset`` answering ``can_reset: true``, i.e. a
#: reset (and, on a supervised box, an update restart) accepted WHILE the
#: shutter travelled over a just-parked mount.
BUSY_LANE_LABELS: tuple[tuple[str, str], ...] = (
    ("goto", "slewing"),
    # The roof: the longest device-blocking operation the rig performs, and it
    # parks the mount first, so it is mount motion wearing another name.
    ("dome", "closing the roof"),
    # A polar run drives the mount to three positions and solves between them.
    # It has only had a lane since the same 2026-08-05 change (the session owns
    # its task, so nothing published it), which is why it was never in here.
    ("polar", "polar aligning"),
    ("solve", "solving"),
    ("autofocus", "focusing"),
    ("focuser", "focusing"),
    ("filter_offsets", "focusing"),
    # SER video (D-RIG-1). It holds ``exposure_guard`` for the whole file, so
    # it is a capture that lasts minutes rather than seconds; a restart
    # mid-file leaves a truncated .ser.
    ("video", "recording"),
    ("capture", "capturing"),
    ("looping", "capturing"),
    ("egain", "capturing"),
)

#: Lanes deliberately left OUT of ``BUSY_LANE_LABELS``, each with the reason.
#:
#: The point is that "not in the table" stops being indistinguishable from "we
#: forgot". A lane in NEITHER table is a bug, and
#: ``tests/test_busy_lanes_routes.py`` reads every ``_spawn("...")`` literal out
#: of api/app.py and fails on one that appears in neither — which is the check
#: that would have caught the roof the day the lane moved.
UNLABELLED_LANES: dict[str, str] = {
    "system.update": (
        "the update's OWN task runs in this lane, and apply() re-reads "
        "restart_blocker at its point of no return (update/service.py:191). "
        "Labelling it would make every update abort itself."),
    "rotator": (
        "accessory motion. A restart does not leave it unsupervised the way a "
        "moving mount does — it is driving to an absolute mechanical angle and "
        "arrives with or without us."),
    "rotate_to_pa": "as `rotator` — the same device, resolved to a sky angle.",
    "filterwheel": (
        "as `rotator`: a slot change is seconds long and self-completing. "
        "(`filter_offsets` IS labelled: that one runs a focus sweep per "
        "filter.)"),
    "guide": (
        "guiding is a control loop, not a committed motion: losing it costs "
        "the frames in flight, which the update/reset already destroys by "
        "restarting. A sequence that is guiding is blocked by `engine.running` "
        "anyway."),
    "dither": "as `guide` — a sub-arcminute settle inside that same loop.",
    "guide_assistant": (
        "a measurement run: it watches the guider, it does not command the "
        "mount."),
    "video_stack": (
        "pure compute on a file already on disk: it touches no device, and a "
        "restart costs a stack that can be re-run in seconds from the same "
        ".ser. (`video` IS labelled: that one owns the camera.)"),
}


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
    # Which preview these pixels were published as, and how big that array was.
    # Carried so a solve that lands SECONDS after the picture is on screen can
    # patch that exact frame rather than the one now showing (#182): a WCS from
    # the previous frame drawn on this one is the green-while-wrong class.
    preview_id: int | None = None
    data_w: int = 0
    data_h: int = 0


@dataclass
class _FieldSolve:
    """The most recent TRUSTED plate solve, and what it says the rig is looking
    at (#182).

    This is the ONLY source an identification may come from. The mount's own
    report is not one: this rig's AM5 has no brake and has been found 50 degrees
    from where it claimed, so ``status.mount.ra_hours`` is a hint for a human,
    never evidence for a file. ``hub._field_block`` will offer a pointing-derived
    guess, labelled ``source="pointing"`` end to end, and nothing derived that
    way is ever adopted into a header.

    ``mount_ra``/``mount_dec`` are what the mount REPORTED at the moment this
    solve was adopted -- not to be trusted as a position, but perfectly good as a
    change detector: when the mount's report has since moved by more than half a
    field, something slewed and this solve is describing a patch of sky the
    camera is no longer on. That covers the slew/park/home routes in the API
    layer without those routes having to know this feature exists.
    """

    wcs: Any
    solved_at: float
    preview_id: int | None
    data_w: int
    data_h: int
    mount_ra: float | None
    mount_dec: float | None
    #: The computed answer -- identification, placed objects, notes -- cached so
    #: a cone query runs once per solve rather than once per published preview.
    frame: dict


def external_preview(info: dict) -> dict:
    """The `preview` event as it may leave this process.

    ``_publish_preview`` builds ONE dict that serves two audiences: the caller
    (the sequence engine and the bundle builder, which need the real on-disk
    location to record and copy the file) and every WebSocket client on the
    planet. Those wants are not the same, and the absolute path belongs only to
    the first — it names the observatory's account, drive and directory scheme
    to anyone holding ``view.status``, which is the least-privileged thing we
    issue.

    So the absolute path is REPLACED here, at the seam where the event enters
    the bus, rather than stripped on the way out of each of the two /ws lanes.
    That ordering is the point: a value that never reaches the bus cannot be
    forgotten by a redactor, and there is no third lane to remember later.

    ``path`` (capture-root-relative) goes out in its place — strictly more use
    to a client than the absolute one, because it is the handle
    ``/api/gallery/file`` and the sync manifest actually accept. A frame that
    is not in this box's library carries no path at all; ``saved_local: false``
    already says why, and inventing one would be worse than saying nothing.
    """
    from . import gallery as _gallery
    out = dict(info)
    abs_path = out.pop("saved_path", None)
    rel = _gallery.relpath_under_capture(abs_path)
    if rel is not None:
        out["path"] = rel
    return out


@dataclass(frozen=True)
class CaptureSnapshot:
    """EVERYTHING THE HEADER READS OFF THE RIG, FROZEN AT EXPOSURE TIME.

    A FITS header describes the rig that took the frame, not the rig that
    happened to be there when somebody pressed save. That distinction did not
    matter while the only way to write a file was to ask for one up front; it
    is the whole hazard of "promote the last frame" (D-SES-4), where the save
    can land minutes and one slew after the shutter closed. Rebuilding the
    header at promote time would stamp the CURRENT pointing, the CURRENT
    filter and the CURRENT focuser position onto pixels that know nothing
    about any of them - a lie of exactly the kind GN-07 was raised for, only
    worse, because it would look perfectly self-consistent.

    So ``Hub.capture`` reads the rig ONCE, on the exposure's own timeline, and
    hands the result here. ``_save_captured_frame`` writes from this and from
    the frame's pixels alone; it never touches ``self.devices``. Frozen so a
    later caller cannot edit a value into it and quietly re-open that seam -
    ``promote_last_frame`` overrides the operator's target with
    ``dataclasses.replace``, which produces a new snapshot rather than
    mutating this one.
    """
    target: str
    frame_type: str
    gain: int
    offset: int
    exposure_s: float
    binning: int
    filter_name: str
    #: the mount's OWN report at exposure time (may be None: no mount, or a
    #: mount that could not answer). MOUNTRA/MOUNTDEC come from these.
    ra: float | None
    dec: float | None
    #: the BEST KNOWN pointing (``_resolve_pointing``) and which source won it.
    #: OBJCTRA/OBJCTDEC and the numeric RA/DEC cards come from these.
    best_ra: float | None
    best_dec: float | None
    pointing_source: str | None
    meta: FrameMeta
    telescope_name: str
    #: the camera's own name, for INSTRUME
    instrument: str = ""
    #: verdict cards measured against this frame's own pixels
    dark_cards: list = field(default_factory=list)
    beam_cards: list = field(default_factory=list)
    #: what OBJECT should say, and the identification provenance behind it
    object_name: str = ""
    id_cards: list = field(default_factory=list)
    #: the sensor temperature the FILENAME token was built from
    sensor_temp_c: float | None = None


@dataclass(frozen=True)
class PendingSave:
    """One unsaved frame kept for ``promote_last_frame`` (D-SES-4)."""
    #: monotonic within this process, so a client can name the frame it meant
    #: and a stale press cannot save a different one.
    id: int
    frame: Any
    snap: CaptureSnapshot
    ts: float


class PromoteRefused(RuntimeError):
    """A refusal from ``promote_last_frame``, carrying its wire shape.

    ``status``/``code`` are on the exception rather than decided at the route
    so the two refusals stay distinguishable all the way out: "there is
    nothing to save" (404 ``nothing_to_promote``) and "you already saved that
    one" (409 ``already_saved``) look identical from the buffer alone once the
    entry has been popped, and a client that cannot tell them apart cannot
    tell the operator which happened.
    """

    def __init__(self, detail: str, code: str, *, status: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status = status


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
        # ONE frame per role, replaced on every capture: a 26 MP uint16 frame
        # is ~50 MB on a Pi, so a queue of them is a queue of out-of-memory
        # kills. The bound is the feature's shape, not a limitation of it -
        # "promote the LAST frame" is what the operator asks for when a
        # throw-away exposure turns out to be worth keeping.
        self._promotable: dict[str, PendingSave] = {}
        self._promote_seq = 0
        # role -> the id of the last frame actually promoted, so a second press
        # can answer "already saved" instead of "nothing to save". See
        # PromoteRefused.
        self._promoted_ids: dict[str, int] = {}
        # Live View (NOV-1): the single EAA running-mean accumulator, non-None while
        # armed. Fed each raw linear sub in _publish_preview; None = feature off.
        self.live_stacker = None
        # Session stack: the monitor page's colour composite over the WHOLE run,
        # one running mean per filter. Always allocated (the object is a few
        # bytes until something is stacked) so the user's on/off choice survives
        # a run ending; `enabled` is the switch and `stop()` frees the planes.
        self.session_stack = SessionStacker()
        # Bahtinov focus aid (NOV-12): None = off; {"tol_px","invert"} = armed. When
        # armed, each raw linear sub gets an additive preview.bahtinov analysis.
        self.bahtinov: dict | None = None
        # the sequence engine registers itself so poll_status can report the
        # active plan's meridian_flip setting without importing the engine.
        self.engine = None
        # last meridian dict from poll_status, so the engine's (sync) ETA can
        # window-gate the flip cost without device I/O.
        self.last_meridian: dict | None = None
        #: (side, unix_time) of the last REAL pier-side reading, or None. See
        #: `_note_pier_side` -- a serial read that times out must not turn a
        #: fact nobody disputes into "unknown".
        self._pier_side_seen: tuple[str, float] | None = None
        #: ((ra, dec), taken_at_monotonic, (ra_j2000, dec_j2000)) - see
        #: ``from_mount_frame``. One entry, because a mount points at one place.
        self._precess_memo: tuple[tuple[float, float], float,
                                  tuple[float, float]] | None = None
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
        # Gallery thumbnail warming (#225). Same shape as the WCS queue and for
        # the same reasons; see _enqueue_thumb. The task retires when idle, so a
        # rig that never captures never carries one.
        self._thumb_queue: asyncio.Queue | None = None
        self._thumb_task: asyncio.Task | None = None
        # --- what the rig is LOOKING AT (#182) ---------------------------------
        # The most recent trusted solve, or None. Set by solve_and_sync (which
        # every goto already pays for) and by the per-frame WCS worker; cleared
        # by invalidate_field_solve and by the pointing-moved check below. None
        # is the normal state on a rig that has not solved since it last slewed,
        # and the UI says exactly that rather than "unknown".
        self.field_solve: _FieldSolve | None = None
        # (ra_hours, dec_deg, unix) -- the mount's own last report, recorded from
        # reads other code was already paying for (the capture header, the solve
        # hint) so identification never adds a device round-trip to the hot path.
        self._last_pointing: tuple[float, float, float] | None = None
        # GN-07: (ra_hours, dec_deg, unix) of the last PLATE SOLVE result, kept
        # separate from `_last_pointing` above (the mount's own, possibly-lying
        # report -- GN-10 measured it walking 50' across a run while the star
        # field held). Consulted by the capture-metadata builder (`_frame_meta`
        # / `_resolve_pointing`) only while `_pointing_verified` is also still
        # True; both are cleared together by `note_pointing_moved()` and by
        # `note_pointing_verified(False, ...)`.
        self._solved_pointing: tuple[float, float, float] | None = None
        #: WAS THE POINTING ACTUALLY VERIFIED? A centering that fell back to a
        #: raw GoTo used to leave no trace but one log line, so the screen showed
        #: a confident TRACKING and a panel full of coordinates nobody had
        #: checked (reported 2026-08-19). Published on mount status so the UI can
        #: say "not verified" instead of implying it was.
        self._pointing_verified: bool = False
        self._pointing_reason: str = "not plate solved since the last move"
        self._pointing_error_arcmin: float | None = None
        # (rounded ra, rounded dec) -> the pointing-derived guess, so a 60-frame
        # loop on one target runs one cone query rather than sixty.
        self._pointing_field_cache: tuple[tuple[float, float], dict | None] | None = None
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
        await self.restore_cooling()                    # #204
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
        # A fresh camera object has a cooler that is OFF and a target of 0.0,
        # whatever the operator asked for before. Put it back (#204).
        await self.restore_cooling()
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
        # D-SES-4: the held frame goes with the rig. Its snapshot describes a
        # camera, a wheel and a mount that are no longer connected, and a
        # reconnect can bring back different ones (#182) - so there is nothing
        # honest left to promote, and ~50 MB of pixels are released with it.
        self._promotable.clear()
        self._promoted_ids.clear()
        # #182: a reconnect can bring back a DIFFERENT camera, a different mount,
        # or the same mount somewhere else entirely. Nothing solved before the
        # rig went away describes the rig that comes back.
        self.invalidate_field_solve("the rig disconnected")
        self._last_pointing = None
        self._solved_pointing = None
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
        (escalation ``reconnect_resume`` / a dropped link). Never raises —
        returns success as a bool.

        Two replay shapes, because the two backends lose a device differently:

        * ALPACA — the device is a network client, and the far end may be a
          restarted process, so the connection is rebuilt from scratch out of
          the recorded host/port/type/number.
        * NATIVE (and anything else) — the device object owns an open USB
          handle. Rebuilding the whole rig to recover one role would drop the
          guider and reset the cooler for the sake of a filter wheel, so the
          object re-opens itself: disconnect (best-effort — it is already gone,
          which is the point) then connect, which every driver implements
          idempotently. This branch is what makes the setting mean anything on a
          native rig, and a native rig is what this product is for.
        """
        info = self._last_connect.get(role)
        if not info:
            return False
        if info.get("backend") == "alpaca":
            try:
                await self.connect_alpaca_device(
                    role, info["host"], info["port"], info["dev_type"],
                    info["dev_num"], info["name"])
                bus.log("info", f"reconnected {role} ({info['host']}:{info['port']})",
                        "hub")
                return True
            except Exception as e:
                bus.log("warning", f"reconnect {role} failed: {e}", "hub")
                return False
        dev = self.devices.get(role)
        if dev is None:
            return False
        try:
            with contextlib.suppress(Exception):
                await dev.disconnect()
            await dev.connect()
        except Exception as e:
            bus.log("warning", f"reconnect {role} failed: {e}", "hub")
            return False
        if not getattr(dev, "connected", False):
            # A driver that returns without raising and without connecting has
            # not reconnected. Reporting True here would let the run carry on
            # into the next exposure against a dead handle.
            bus.log("warning", f"reconnect {role} did not take", "hub")
            return False
        bus.log("info", f"reconnected {role} ({getattr(dev, 'name', role)})", "hub")
        return True

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
            # Every frame-setting scope, so the WS `hello` COLD-SEEDS each
            # client (#176). Half of the R-vs-Oiii defect was that no client
            # ever learned the server's values: the Align screen's numbers
            # arrived only on a `polar` event, so a reload over a live pin
            # showed mirrored defaults while the engine used something else.
            # A screen that has to be told by an event it may never receive is
            # a screen that shows a constant.
            "frames": frames_payload(self.guider),
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
        aperture = float(getattr(o, "aperture_mm", 0.0) or 0.0)
        reducer = float(getattr(o, "reducer", 1.0) or 1.0)
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
            # D-SET-1. getattr seams so this readout survives a config schema
            # that has not grown the fields yet.
            "aperture_mm": aperture,
            "reducer": reducer,
            # DERIVED, NEVER STORED. Nothing writes f_ratio: it is focal length
            # over aperture and nothing else, so it cannot drift from the two
            # numbers it is made of. None when the aperture was never filled in
            # - there is no camera fallback and none is possible, and an f/5.3
            # printed next to a frame is read as a fact about the rig.
            #
            # THE REDUCER IS NOT APPLIED HERE, deliberately: if USE THE REDUCED
            # FOCAL LENGTH was pressed then focal_length_mm already carries it,
            # and applying it here would double-count.
            #
            # THROUGH ``config.f_ratio``, which is where all of that is written
            # down. This used to re-derive the division inline, and the function
            # existed with no caller but its own tests - so the ONE rule about
            # this number (None is not a fallback, the reducer is not applied)
            # was documented in a place the running code did not read, and two
            # copies of a rule are two rules.
            "f_ratio": f_ratio(o.focal_length_mm, aperture),
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

    def _live_lanes(self) -> set[str]:
        """Every long operation in flight RIGHT NOW, by its own lane name.

        The one source both `busy_label` (which collapses it to a word) and
        `busy_lanes` (which does not) read, so the coarse answer and the precise
        one can never disagree about what is running.
        """
        live = {n for n, t in self._busy.items() if t and not t.done()}
        if self.looping:
            live.add("looping")
        return live

    def busy_lanes(self) -> list[str]:
        """The live lane names, sorted — "goto", "solve", "autofocus", "polar".

        What a UI control needs to answer "is MY operation still running", which
        `busy_label` cannot: it maps goto/solve/autofocus/capture and four more
        onto four words. Sorted so the status payload is stable and a diff of two
        frames means something.
        """
        return sorted(self._live_lanes())

    @property
    def busy_label(self) -> str | None:
        """A short phrase for the current long backend op (or None). Drives the
        telemetry-stale suppression — a slew/solve/AF/capture/roof-close
        legitimately starves the 2s status poll, so "busy" means "not stalled" —
        and, through `restart_blocker`, the update/factory-reset idle gate.

        Deliberately lossy; see `busy_lanes` for the unreduced set. The mapping
        (and the list of lanes deliberately left out of it) is
        ``BUSY_LANE_LABELS`` / ``UNLABELLED_LANES`` at the top of this module."""
        live = self._live_lanes()
        for name, label in BUSY_LANE_LABELS:
            if name in live:
                return label
        return None

    @property
    def restart_blocker(self) -> "str | None":
        """A human reason the controller must NOT restart right now, or None when
        idle. The self-update safety gate consults this so an update can never
        interrupt an exposure, slew, roof close, or running sequence (spec
        section 6); ``/api/system/factory-reset`` reuses it for the same reason,
        and its POST additionally tears the rig down on the way past."""
        label = self.busy_label
        if label is not None:
            return f"rig is {label}"
        eng = self.engine
        if eng is not None and getattr(eng, "running", False):
            return "a sequence is running"
        # The backstop for motion that has NO lane. ``_motion_lock`` is held for
        # exactly as long as a device-level motion command is on the wire, by
        # every path that moves the mount — including the ones that publish no
        # lane at all: the dawn-park daemon holds it across its whole park (up to
        # PARK_TIMEOUT_S), and ``/api/mount/move`` holds it across each jog
        # dispatch. It is a fact read off the hub rather than a name in a table,
        # so unlike the labels above it cannot be dropped by a lane rename.
        #
        # Last, so every sentence above it is unchanged: a lane or a run always
        # gets to describe itself in its own words.
        lock = getattr(self, "_motion_lock", None)
        if lock is not None and lock.locked():
            return "the mount is moving"
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
        is a JNOW Alpaca mount. Same fail-safe fallback as ``to_mount_frame``.

        MEMOISED ON THE EXACT INPUT, briefly. Every status poll and every frame
        of a live loop runs this, and on an Alpaca rig each one is a thread hop
        plus the whole apparent-place transform. A TRACKING mount reports the
        same RA/Dec frame after frame - that is what tracking IS - so the cache
        key is the coordinate pair itself: the memo can only ever answer the
        precession of the coordinates it was asked for, and a slew changes the
        key and recomputes. Over ``_PRECESS_MEMO_TTL_S`` the transform itself
        moves by well under a milliarcsecond (precession is 50 arcsec a YEAR),
        so the entry is not a stale answer, it is the same answer.
        """
        if not await self._mount_expects_jnow(tel):
            return ra_hours, dec_deg
        key = (float(ra_hours), float(dec_deg))
        hit = self._precess_memo
        if hit is not None and hit[0] == key and (
                time.monotonic() - hit[1]) < _PRECESS_MEMO_TTL_S:
            return hit[2]
        try:
            out = await asyncio.to_thread(precess_jnow_to_j2000,
                                          ra_hours, dec_deg)
        except Exception as e:  # noqa: BLE001 - availability over precision here
            bus.log("warning", f"JNOW->J2000 precession failed ({e}); "
                               "using raw coordinates", "mount")
            return ra_hours, dec_deg
        self._precess_memo = (key, time.monotonic(), out)
        return out

    def _judge_dark_frame(self, frame, frame_type: str, filter_name: str,
                          opaque_slot: int | None
                          ) -> list[tuple[str, object, str]] | None:
        """Judge a DARK/BIAS frame against its own pixels and return the FITS
        cards recording the verdict (``None`` for a light or a flat, and for
        anything that goes wrong — a header check must never fail a save).

        Runs on the save thread, alongside the FITS write it feeds, because
        ``detect_stars`` on a full frame is not free and the event loop has a
        guide loop on it.

        The verdict reaches THREE places, and it needs all three: the file (the
        calibration library reads headers and nothing else, so an uncarded
        reject is stacked as if it passed), a log line for the operator that
        night, and — when the frame contradicts a slot the operator ticked
        opaque — the sentence naming that flag. Finding out in March that
        January's darks were white frames is not a recovery."""
        if (frame_type or "").upper() not in ("DARK", "BIAS"):
            return None
        try:
            from .imaging.darks import judge_dark, opaque_claim_refuted
            data = getattr(frame, "data", None)
            if data is None:
                return None
            # ``opaque_slot`` is resolved by the CALLER, on the event loop: the
            # wheel's position read is async and this method runs on a worker
            # thread. Telling judge_dark the slot ONLY when the operator flagged
            # it opaque is what turns "light reached the sensor" into "the flag
            # you ticked is contradicted", the sentence that closes the loop.
            result = judge_dark(
                data,
                full_well=getattr(frame, "full_well", None),
                exposure_s=getattr(frame, "exposure_s", None),
                opaque_slot=opaque_slot,
                filter_name=filter_name)
            if not result.is_dark:
                bus.log("warning", result.reason, "capture")
                if opaque_claim_refuted(result) is not None:
                    bus.log("warning",
                            "this frame contradicts the blackout flag on that "
                            "filter slot — until it is retracted or the slot is "
                            "blanked, every dark and bias shot through it is "
                            "suspect", "capture")
            return result.fits_cards()
        except Exception as e:  # noqa: BLE001 — never fail a save over a check
            bus.log("warning", f"dark check skipped: {e}", "capture")
            return None

    def _blackout_light_cards(self, frame_type: str, opaque_slot: int | None,
                              filter_name: str) -> list[tuple]:
        """Cards for a LIGHT taken through a slot flagged blackout.

        The dark check asks "is this dark frame actually dark". This asks the
        mirror question nobody was asking: is this LIGHT frame actually going
        through glass. A light exposure commanded with a blackout slot in the
        beam is wrong however it happened - either the wheel is not where the
        run thinks it is, or the slot is not what the operator ticked - and
        unlike a white dark it is not visible in the pixels: on a slot that is
        an empty carrier rather than a blanked one, the frame looks like a
        perfectly good unfiltered sub.

        MEASURED, 2026-08-13. A cloud hold drove the wheel to slot 7 for its
        darks and nothing put it back, so eighteen 180 s subs of NGC 6946 were
        taken through it and written FILTER='Dark'. Those frames sit on the
        dark floor - median 247 against 263 for a real frame through the step's
        own filter twenty minutes earlier at the same sensor temperature.

        The headers were not even wrong: the wheel really was on slot 7. That is
        exactly why nothing downstream objected - a true header gives the
        pipeline no reason to. The engine seam is fixed
        (``SequenceEngine._restore_beam``); this is the card that would have
        said so the same night, and catches the next cause of it.

        A CARD RATHER THAN A REFUSAL. The run is already exposing when this is
        known, and aborting a night on a wheel-position read is a worse failure
        than carding the frames: ``BEAMOK=False`` makes them findable forever,
        which is what the operator actually needs at 3 a.m. and in March.
        """
        if opaque_slot is None or frame_type.upper() in ("DARK", "BIAS"):
            return []
        from .imaging.darks import _ascii_card
        why = (f"{frame_type} exposed with slot {opaque_slot} "
               f"({filter_name or 'unnamed'}) in the beam, and that slot is "
               f"flagged blackout")
        bus.log("error",
                f"{why} - this frame has no usable signal. Either the wheel is "
                f"not where the run believes, or the blackout flag on that slot "
                f"is wrong", "capture")
        return [
            ("BEAMOK", False, "Light path was clear of a blackout slot"),
            ("BEAMWHY", _ascii_card(why), "Blackout-slot check evidence"),
        ]

    async def _wheel_slot(self) -> int | None:
        """The slot the wheel is physically on, or None. Never raises.

        ONE READ, SHARED. The capture path asks two questions of the wheel per
        exposure - what is the filter called (``_active_filter_name``) and is
        this slot opaque (``_opaque_slot_in_beam``) - and both used to issue
        their own ``get_position``. That is two round trips on a bus that is
        also carrying the guide camera, on every frame of a live loop whose
        frames are otherwise free. The answer is the same answer: it is the same
        wheel at the same instant.

        NOT CACHED ACROSS EXPOSURES, and that is the point of passing the slot
        down rather than memoising it here. A TTL cache would put the wheel's
        position a second or two in the past, and the one thing this rig has
        already been burned by is a FILTER card naming the wrong slot (every
        frame before 2026-08-02 is off by one). Two questions about one instant
        get one read; two instants get two.
        """
        try:
            fw = self.devices.get("filterwheel")
            if not fw or not getattr(fw, "connected", False):
                return None
            pos = await fw.get_position()
            return None if pos is None else int(pos)
        except Exception:  # noqa: BLE001
            return None

    async def _opaque_slot_in_beam(self, slot: int | None = ...) -> int | None:
        """The wheel's current slot when the operator has flagged it opaque,
        else None. Never raises: no wheel, an unreadable position or a missing
        flag list all mean "we cannot say which slot", which is not the same as
        "the slot is fine" — the frame is still judged on its pixels, the
        rejection just cannot name a flag to retract.

        ``slot`` is an ALREADY-READ position (``_wheel_slot``), so a caller that
        has one does not buy a second round trip for the same instant. The
        sentinel default means "read it yourself"; ``None`` is a real value and
        means "the wheel could not be read", which is why the default is not
        ``None``."""
        try:
            fw = self.devices.get("filterwheel")
            if not fw or not getattr(fw, "connected", False):
                return None
            flags = list(getattr(fw, "filter_opaque", []) or [])
            pos = await self._wheel_slot() if slot is ... else slot
            if pos is None or pos < 0 or pos >= len(flags):
                return None
            return int(pos) if flags[pos] else None
        except Exception:  # noqa: BLE001
            return None

    def _resolve_pointing(self, ra_hours: float | None,
                          dec_deg: float | None
                          ) -> tuple[float | None, float | None, str | None]:
        """The BEST KNOWN pointing for a captured frame's header (GN-07), and
        which source it came from -- "solved", or (failing that) "mount".

        A plate solve recorded while `_pointing_verified` is still current
        outranks the mount's own report: the AM5's reported RA/Dec has been
        measured walking up to 50 arcmin across a single run while the star
        field itself held to a dither (GN-10). `_solved_pointing` and
        `_pointing_verified` are cleared together by `note_pointing_moved()`
        and by `note_pointing_verified(False, ...)`, so neither can outlive
        the centre it describes.

        Deliberately no third "target" branch: Hub does not keep the target of
        an in-flight goto anywhere independent of a solve, so there is nothing
        honest to fall back to between "solved" and the mount's raw report --
        adding one here would be inventing state the rest of Hub does not
        keep (see the GN-07 landing report)."""
        if ra_hours is None or dec_deg is None:
            return ra_hours, dec_deg, None
        if self._pointing_verified and self._solved_pointing is not None:
            solved_ra, solved_dec, _at = self._solved_pointing
            return solved_ra, solved_dec, "solved"
        return ra_hours, dec_deg, "mount"

    async def _frame_meta(self, frame, ra_hours: float | None,
                          dec_deg: float | None, best_ra: float | None = None,
                          best_dec: float | None = None,
                          pointing_source: str | None = None) -> "FrameMeta":
        """Best-effort telemetry snapshot for the FITS header (spec §8/§9). Every
        read is individually guarded: an absent/hung device or a failed read
        leaves its value None (its card omitted) and never blocks or fails the
        save. Only Hub.capture builds this; save_fits stays device-free.

        ``ra_hours``/``dec_deg`` are the MOUNT's own report (unchanged from
        before GN-07); ``best_ra``/``best_dec``/``pointing_source`` are the
        resolved best-known pointing the caller already computed via
        ``_resolve_pointing`` -- passed in rather than recomputed here so a
        capture's header and its ``save_fits`` RA/DEC cards can never disagree
        with each other about which pointing won (see ``Hub.capture``). When
        the caller omits them (no other production caller does; kept as
        defaults so this stays callable in isolation) they fall back to the
        mount's own report, i.e. the pre-GN-07 behaviour."""
        from .catalog import coords
        if best_ra is None and best_dec is None and pointing_source is None:
            best_ra, best_dec, pointing_source = ra_hours, dec_deg, (
                "mount" if ra_hours is not None and dec_deg is not None else None)
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
            # GN-07: OBJCTRA/OBJCTDEC (and OBJCTALT/AIRMASS below) carry the
            # BEST KNOWN pointing (`best_ra`/`best_dec`), not necessarily the
            # mount's raw report -- see `_resolve_pointing`. The mount's own
            # report rides along too, always, as MOUNTRA/MOUNTDEC/MOUNTRAD/
            # MOUNTDCD, so a reader can see both and PNTGSRC says which one
            # OBJCTRA/OBJCTDEC actually is.
            try:
                meta.objctra = coords.format_ra_fits(best_ra)
                meta.objctdec = coords.format_dec_fits(best_dec)
                meta.pointing_source = pointing_source
                meta.mount_ra_hours = float(ra_hours)
                meta.mount_dec_deg = float(dec_deg)
                meta.mountra = coords.format_ra_fits(ra_hours)
                meta.mountdec = coords.format_dec_fits(dec_deg)
            except Exception:
                pass
            if lat is not None and lon is not None:
                try:
                    alt, _az = coords.altaz(best_ra, best_dec, lat, lon,
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

    async def _active_filter_name(self, slot: int | None = ...) -> str:
        """The name of the filter the wheel is PHYSICALLY on right now, or ``""``
        when there is no connected wheel (or it can't be read).

        THE single source of filter identity (UX #1). ``capture`` stamps this into
        the FITS ``FILTER`` card, the ``$$FILTER$$`` filename token and the
        ``info["filter"]`` the sequence engine records — so the header, the
        filename, the session report, the stacking-bundle folder and the CSV can
        never disagree again. Never raises.

        ``slot`` is an ALREADY-READ position (see ``_wheel_slot``); the sentinel
        default reads one."""
        fw = self.devices.get("filterwheel")
        if not fw or not getattr(fw, "connected", False):
            return ""
        try:
            names = list(getattr(fw, "filter_names", []) or [])
            pos = await self._wheel_slot() if slot is ... else slot
            if pos is None or pos < 0 or pos >= len(names):
                return ""
            return str(names[pos] or "")
        except Exception:
            return ""

    async def capture(self, exposure_s: float, gain: int, offset: int,
                      binning: int = 1, save: bool = False, target: str = "",
                      frame_type: str = "Light", request_id: str | None = None) -> dict:
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
        # One source, one answer.
        #
        # PAID FOR AN UNSAVED LOCAL FRAME TOO, since D-SES-4. It used to be
        # resolved only when saving, on the grounds that the live loop's
        # throw-away frames must not buy a wheel read per exposure. But a frame
        # that can be PROMOTED later is not throw-away, and the FILTER card it
        # would then carry has to name the slot that was in the beam when the
        # shutter opened, not the slot the wheel has moved to by the time
        # somebody presses save. A rig with no wheel still pays nothing
        # (``_active_filter_name`` answers "" without any I/O).
        remote_save = frame.rendered_bytes is not None
        # ONE wheel read for this exposure, threaded into both consumers (the
        # FILTER card's name and the opaque-slot judgement) so the live loop
        # pays one round trip a frame instead of two. Read at the same moment
        # either would have read it, so nothing about WHEN is different.
        # The sentinel (not None) when nothing wanted it: None is a real value
        # here and means "the wheel could not be read", which would take the
        # blackout-slot check out of the cloud gate on the one path that does
        # not need a filter name (a NINA remote save on the live loop).
        wheel_slot = ...
        filt = ""
        if save or not remote_save:
            wheel_slot = await self._wheel_slot()
            filt = await self._active_filter_name(wheel_slot)
        # For local (sim/Alpaca) saves, write the FITS BEFORE publishing the
        # preview so the first `preview` event already carries the correct
        # saved_path/saved_local (P2-2). NINA saves on the imaging host during
        # expose() and the frame already carries its saved_path.
        local_save_path: Path | None = None
        snap: CaptureSnapshot | None = None
        if not remote_save:
            # THE RIG, READ ONCE, ON THE EXPOSURE'S OWN TIMELINE - for the
            # unsaved frame as well, because that is the frame
            # ``promote_last_frame`` may be asked to write minutes and one slew
            # later (D-SES-4). The cost is a handful of guarded device reads per
            # unsaved frame, which is exactly what a saved frame has always
            # paid; the alternative is a promoted header describing wherever the
            # mount, wheel and focuser have got to since.
            #
            # ``note_pointing`` stays on the SAVING path only: recording the
            # mount's last report drives the field-identification staleness
            # check, and an unsaved throw-away frame is not evidence about
            # anything. It costs the header nothing - the snapshot already
            # carries the read.
            snap = await self._capture_snapshot(
                frame, target=target, frame_type=frame_type, gain=gain,
                offset=offset, exposure_s=exposure_s, binning=binning,
                filter_name=filt, note_pointing=save, wheel_slot=wheel_slot)
        if save and snap is not None:
            local_save_path = await self._save_captured_frame(frame, snap)

        info = await self._publish_preview(frame, wheel_slot=wheel_slot,
                                          **({"capture_request_id": request_id} if request_id else {}))
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
        elif local_save_path is not None and snap is not None:
            self._after_frame_saved(local_save_path, snap,
                                    info if isinstance(info, dict) else None)

        if snap is not None:
            # THE PROMOTE BUFFER (D-SES-4). One slot for the imaging camera,
            # replaced every capture. A saved frame CLEARS it: there is nothing
            # left to promote once the file is on disk, and leaving the previous
            # unsaved frame behind would let a later press write a stale one
            # under a fresh operator's assumption that "last" meant the frame
            # they are looking at.
            if save:
                self._promotable.pop("camera", None)
                self._promoted_ids.pop("camera", None)
            else:
                self._promote_seq += 1
                self._promotable["camera"] = PendingSave(
                    id=self._promote_seq, frame=frame, snap=snap,
                    ts=time.time())
        return info

    # ------------------------------------------------------ promote the last
    #
    # WHY THE FRAME IS HELD AT ALL. A test exposure is how an operator decides
    # whether the framing, the focus and the guiding are worth committing to,
    # and until now the answer "yes, keep that one" meant taking a second
    # exposure and hoping the sky had not moved. The pixels were already in
    # memory; only the decision to write them was missing.

    async def promote_last_frame(self, *, role: str = "camera",
                                 frame_id: int | None = None,
                                 target: str | None = None) -> dict:
        """Write the held unsaved frame to disk (D-SES-4).

        RE-RESOLVES NOTHING. The mount, the wheel, the focuser and the rotator
        are not consulted: every header value comes from the snapshot frozen
        when the shutter closed, so a promote after a slew still describes the
        sky the pixels came from. ``target`` is the ONE thing the operator may
        change after the fact, because it is the one thing that was never a
        measurement - it is what they meant to call the field, and naming it
        correctly is often the reason they are saving at all. Overriding it
        re-runs ``_object_cards`` so OBJECT and the identification cards stay
        consistent with each other; nothing else moves.

        Raises :class:`PromoteRefused` with the wire shape attached."""
        pending = self._promotable.get(role)
        if pending is None:
            last = self._promoted_ids.get(role)
            if last is not None and (frame_id is None or int(frame_id) == last):
                raise PromoteRefused("that frame has already been saved",
                                     "already_saved", status=409)
            raise PromoteRefused("no unsaved frame to save",
                                 "nothing_to_promote", status=404)
        if frame_id is not None and int(frame_id) != pending.id:
            # A DISTINCT REFUSAL, not "already saved": the operator is looking
            # at one frame and the buffer holds a newer one, so saving what is
            # held would write pixels they never chose.
            raise PromoteRefused(
                f"that frame is no longer the one being held (asked for "
                f"{int(frame_id)}, holding {pending.id})",
                "frame_id_mismatch", status=409)
        snap = pending.snap
        override = (target or "").strip()
        if override and override != snap.target:
            object_name, id_cards = self._object_cards(override,
                                                       snap.frame_type)
            snap = replace(snap, target=override, object_name=object_name,
                           id_cards=list(id_cards))
        # Popped BEFORE the write so two presses in flight cannot both save,
        # and put back if the write fails so a full disk costs the operator a
        # retry rather than the frame.
        self._promotable.pop(role, None)
        try:
            path = await self._save_captured_frame(pending.frame, snap)
        except Exception:
            # ``setdefault``, not ``[role] =``. The write is awaited, and the
            # live loop keeps taking frames while it runs - so a slow or failing
            # save (a full disk, a USB drive that went away) can finish AFTER a
            # newer exposure has already claimed the slot, and putting this one
            # back unconditionally would hand the operator an OLDER frame under
            # "save the last one". The retry they are about to press must write
            # the frame they are looking at.
            self._promotable.setdefault(role, pending)
            raise
        self._promoted_ids[role] = pending.id
        self._after_frame_saved(path, snap, None)
        from . import gallery as _gallery
        return {
            "saved": True,
            # capture-root-relative, the only form of a frame's location that
            # may leave the process (see gallery.relpath_under_capture).
            "path": _gallery.relpath_under_capture(path),
            "filter": snap.filter_name,
            "id": pending.id,
            "target": snap.target,
            "ts": pending.ts,
        }

    def promotable_summary(self, role: str = "camera") -> dict:
        """What ``GET /api/capture/last`` reports: the held frame's identity and
        settings, or an explicit "nothing held".

        ``saved`` is always False, and is in the payload rather than implied so
        a client rendering the row never has to infer it from ``available``."""
        p = self._promotable.get(role)
        if p is None:
            return {"available": False, "id": None, "ts": None,
                    "exposure_s": None, "gain": None, "binning": None,
                    "frame_type": None, "target": None, "filter": None,
                    "saved": False}
        s = p.snap
        return {"available": True, "id": p.id, "ts": p.ts,
                "exposure_s": s.exposure_s, "gain": s.gain,
                "binning": s.binning, "frame_type": s.frame_type,
                "target": s.target, "filter": s.filter_name, "saved": False}

    # ----------------------------------------------- one frame, written once
    #
    # THREE PIECES, AND THE SPLIT IS THE POINT (D-SES-4):
    #   ``_capture_snapshot``   reads the rig, on the exposure's own timeline
    #   ``_save_captured_frame`` writes the file, touching NO device
    #   ``_after_frame_saved``  the tail every locally saved frame shares
    # ``capture()`` runs all three in a row; ``promote_last_frame`` runs the
    # last two, minutes later, against the snapshot the first one froze. That
    # is what makes a promoted frame's header describe the rig that took it
    # rather than the rig that happens to be there now.

    async def _capture_snapshot(self, frame, *, target: str, frame_type: str,
                                gain: int, offset: int, exposure_s: float,
                                binning: int, filter_name: str,
                                note_pointing: bool = True,
                                wheel_slot: int | None = ...) -> CaptureSnapshot:
        """Read the rig ONCE and freeze what the header depends on.

        Every device read the FITS header needs lives here and nowhere else,
        which is exactly what lets ``_save_captured_frame`` be device-free.
        Total by construction: every read is individually guarded, as it was
        inline, because spec §9 says a header write never fails a capture."""
        cam = self.devices.get("camera")
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
        if note_pointing:
            # The identification's staleness check and its pointing fallback both
            # ride THIS read — the one the header was already paying for — so
            # naming the field never adds a device round-trip to the capture path.
            self._note_pointing(ra, dec)
        # NOT self.note_pointing_moved() here (GN-07/GN-10). This call used
        # to run unconditionally on every saved frame, which invalidated
        # `_pointing_verified`/`_solved_pointing` before the header for THIS
        # SAME frame was even built -- a plate-solved centre could never
        # survive past the first sub of a run, defeating the whole point of
        # carrying a solved pointing forward. Capturing (even right after a
        # dither) is not evidence the tube left a solved centre; only an
        # actual slew/park/sync/unpark/home is, and those already run
        # through `note_pointing_verified(False, ...)` (a failed re-centre)
        # or overwrite it with a fresh `note_pointing_verified(True, ...)`.
        #
        # Resolved ONCE, here, and threaded into both the meta builder and
        # the save_fits call below so OBJCTRA/OBJCTDEC and the numeric
        # RA/DEC cards can never disagree about which pointing won.
        best_ra, best_dec, pointing_source = self._resolve_pointing(ra, dec)
        # Gather header telemetry (best-effort; never fails the save) and the
        # OTA name for TELESCOP (omitted when blank). Wrapping the whole meta
        # build keeps spec §9 (a header write never fails a capture) structural,
        # not dependent on CameraFrame's field set staying non-raising.
        try:
            meta = await self._frame_meta(frame, ra, dec, best_ra, best_dec,
                                          pointing_source)
        except Exception:
            meta = FrameMeta()
        try:
            telescope_name = (self.effective_optics().get("telescope_name")
                              or "").strip()
        except Exception:
            telescope_name = ""
        # IS THIS "DARK" ACTUALLY DARK? Measured here, against the pixels,
        # and stamped into the file. ``imaging.darks`` was written for the
        # 2026-08-01 incident — a wheel slot ticked ``filter_opaque`` that
        # was EMPTY, and three daylight darks at median 65535 filed as a
        # dark library — and until now nothing called it. A detector with no
        # caller protects nothing; this is that caller.
        in_beam = await self._opaque_slot_in_beam(wheel_slot)
        opaque_slot = (in_beam
                       if frame_type.upper() in ("DARK", "BIAS") else None)
        dark_cards = await asyncio.to_thread(
            self._judge_dark_frame, frame, frame_type, filter_name, opaque_slot)
        # AND THE OTHER DIRECTION, which cost fifty-four minutes of a clear
        # night before anyone looked. See _blackout_light_cards.
        beam_cards = self._blackout_light_cards(frame_type, in_beam, filter_name)
        # WHAT THE SKY SAYS THIS IS (#182). ``object_name`` is what reaches
        # the OBJECT card; the capture PATH is built from the operator's string
        # alone (``snap.target``) and is never built from this - that ordering
        # is the invariant, not a coincidence.
        object_name, id_cards = self._object_cards(target, frame_type)
        return CaptureSnapshot(
            target=target, frame_type=frame_type, gain=gain, offset=offset,
            exposure_s=exposure_s, binning=binning, filter_name=filter_name,
            ra=ra, dec=dec, best_ra=best_ra, best_dec=best_dec,
            pointing_source=pointing_source, meta=meta,
            telescope_name=telescope_name,
            # INSTRUME is a rig read too: a promote that ran `self.require(
            # "camera").name` could stamp a camera that was swapped in after
            # the exposure.
            instrument=(getattr(cam, "name", "") or ""),
            dark_cards=list(dark_cards or []), beam_cards=list(beam_cards),
            object_name=object_name, id_cards=list(id_cards),
            sensor_temp_c=getattr(frame, "temperature_c", None))

    async def _save_captured_frame(self, frame, snap: CaptureSnapshot) -> Path:
        """Write ONE frame into the capture library from a frozen snapshot.

        Reads no device and consults no live rig state: the frame's pixels and
        ``snap`` are the whole input, which is what makes this replayable by
        ``promote_last_frame`` long after the exposure."""
        path = self._capture_path(
            # The operator's string, NOT ``snap.object_name`` - the filename
            # follows what they typed even when a solve identified the field.
            snap.target or "untargeted", snap.frame_type, snap.filter_name,
            gain=snap.gain, exposure_s=snap.exposure_s, binning=snap.binning,
            sensor_temp_c=snap.sensor_temp_c)
        # Offloaded so a 25-120 MB uint16 FITS write to the Pi's SD card never
        # freezes the event loop for seconds every frame (WS/preview stall,
        # queued guide events, delayed STOP) — same as solve_and_sync's write.
        await asyncio.to_thread(
            save_fits, frame, path, target=snap.object_name,
            filter_name=snap.filter_name,
            # BEST KNOWN pointing (GN-07), not necessarily the mount's raw
            # `ra`/`dec` -- see `_resolve_pointing`. `meta` carries the
            # mount's own report separately as MOUNTRA/MOUNTDEC.
            frame_type=snap.frame_type, ra_hours=snap.best_ra,
            dec_deg=snap.best_dec, telescope=snap.telescope_name,
            instrument=snap.instrument, meta=snap.meta,
            extra_cards=(list(snap.dark_cards) + list(snap.beam_cards)
                         + list(snap.id_cards)))
        # carry the path on the frame so _publish_preview reports a correct
        # saved_path/saved_local in the very first event (no stale re-publish).
        frame.saved_path = str(path)
        return path

    def _after_frame_saved(self, path: Path, snap: CaptureSnapshot,
                           info: dict | None = None) -> None:
        """The tail every LOCALLY saved frame shares: log it, warm its
        thumbnail, tell the sync runner, and (for a light, when the feature is
        on) hand it to the background WCS worker.

        ``info`` is the preview event this frame produced, when there is one.
        A promoted frame has none - its preview was published minutes ago, at
        capture time - so the star gate and the preview back-reference simply
        go unset rather than being invented from a second detection pass."""
        bus.log("info", f"saved {path.name}", "capture")
        # WARM THE GALLERY THUMBNAIL NOW, while nobody is waiting for it.
        # Rendering one costs ~1.5 s (auto_stretch over 26 megapixels), and
        # a desktop grid asks for forty at once — measured 2026-08-10: the
        # relay's per-IP bucket answered 19 of 41 with 429 and the gallery
        # showed nothing at all. Doing it here turns every later view into
        # a small disk read. Fire-and-forget by design: a gap is harmless
        # because the route still renders on demand.
        self._enqueue_thumb(path)
        # And tell the file-sync runner a frame landed. Two assignments and
        # no I/O — it decides on its own time whether that warrants a pass,
        # and does nothing at all unless a destination is configured. Only
        # on the LOCAL-save branch, because the push reads from this box's
        # capture root and a NINA/remote save is not on it.
        self._note_frame_saved()
        # Opt-in (default OFF): hand the saved light to the BACKGROUND WCS worker
        # so its plate solve stamps astrometry into the header without the
        # capture path ever waiting on it (per-frame-wcs spec §2.1 — an inline
        # 2-10 s ASTAP run would delay the next sub by its whole duration, every
        # frame). Only ever reached on the LOCAL-save branch: a NINA/remote save
        # lives on the imaging host and cannot be reopened here (decision D5).
        # Enqueue is non-blocking, bounded and total: it can neither await nor
        # raise into the capture.
        # LIGHT only, as the config field is named: a dark/bias/flat has no stars
        # to solve, so enqueuing one only burns a full ASTAP run (and its 60 s
        # kill timeout) per frame — a 50-frame dark library would peg a core of
        # the Pi for the whole unattended run and flood the log with failures.
        if (snap.frame_type.upper() == "LIGHT"
                and config_store.cfg().solve_saved_lights):
            i = info or {}
            # star count from the preview's SINGLE detection pass (info["stars"]),
            # not frame.stars — the latter is only ever set by a backend that
            # measured it (NINA), so the min-stars gate would be a silent no-op
            # on exactly the local frames it exists to filter.
            self._enqueue_wcs_stamp(
                path, snap.ra, snap.dec, i.get("stars"),
                # so the solution can be published back onto THIS frame's pixels
                # when it lands, seconds after the picture is already on screen.
                preview_id=i.get("id"),
                data_w=int(i.get("data_width") or 0),
                data_h=int(i.get("data_height") or 0))

    # ------------------------------------------------- per-frame WCS stamping
    # (per-frame-wcs spec §2; the mechanism — solvers, WcsSolution, write_wcs —
    #  shipped with PRO-2 F-B. What lives here is the OFF-the-hot-path plumbing.)

    def _enqueue_wcs_stamp(self, path: Path, ra: float | None, dec: float | None,
                           star_count: int | None, *,
                           preview_id: int | None = None,
                           data_w: int = 0, data_h: int = 0) -> None:
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
                                 star_count=star_count, preview_id=preview_id,
                                 data_w=data_w, data_h=data_h))
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
            # ...and it stops being landlocked. The solution used to be written
            # into the file and dropped on the floor: nothing published it,
            # nothing stored it, and the browser had never seen one. This is the
            # publish (#182). Markers only reach the frame this WCS was solved
            # FROM, which is why the job carries the preview id.
            if job.data_w and job.data_h:
                await self.note_field_solve(res.wcs, preview_id=job.preview_id,
                                            data_w=job.data_w, data_h=job.data_h)

    def stop_wcs_worker(self) -> None:
        """Cancel the background WCS worker and drop any pending backlog. Called
        from ``_teardown`` alongside ``stop_loop`` so the task can never outlive
        its hub (risk R1)."""
        if self._wcs_task and not self._wcs_task.done():
            self._wcs_task.cancel()
        self._wcs_task = None
        self._wcs_queue = None
        self._wcs_drop_logged = False

    # ------------------------------------------- what the rig is looking at (#182)
    #
    # THE LINE THIS WHOLE SECTION EXISTS TO HOLD: an identification derived here
    # never becomes a path component, a folder, or a key of the persisted
    # per-target frame counter. ``_capture_path`` still takes the operator's (or
    # the plan's) string and nothing else. A derived name can change between
    # frame 3 and frame 4 -- the solve drifts onto a neighbour, the mount is
    # nudged -- and the counter is keyed on the sanitized string, so letting one
    # through would split a night across two folders with two overlapping
    # ``0001...`` runs and report nothing. What a derived name MAY reach is the
    # screen, the log, and provenance cards in the header (OBJCTID/OBJIDSRC/
    # OBJIDSEP), plus OBJECT itself under the narrow rule in ``capture``.

    #: How far the mount's OWN report may drift from what it said when a solve
    #: was adopted, as a fraction of the field, before that solve is treated as
    #: describing sky the camera has left. Half a frame: past that, the object
    #: that named the field may not even be in the picture any more.
    _FIELD_STALE_FOV_FRAC = 0.5
    #: Floor for the same test, in degrees, for a rig with no usable optics
    #: (``have_optics`` false) or a very narrow field. Comfortably above dither
    #: and centering nudges, far below any real slew.
    _FIELD_STALE_MIN_DEG = 0.25
    #: How long the mount's last report may be reused as a pointing hint. It is
    #: recorded from reads other paths already made, so it can be arbitrarily
    #: old; past this it is not evidence of anything.
    _POINTING_MAX_AGE_S = 300.0

    def _note_pointing(self, ra_hours: float | None, dec_deg: float | None) -> None:
        """Record the mount's own report, from a read somebody else already paid
        for. Never triggers device I/O of its own."""
        if ra_hours is None or dec_deg is None:
            return
        self._last_pointing = (float(ra_hours), float(dec_deg), time.time())

    def invalidate_field_solve(self, reason: str) -> None:
        """Drop the current identification because the sky under the camera may
        have changed.

        Call this from anything that moves the mount or redefines what its
        coordinates mean -- slew, park, unpark, find home, sync, a reconnect. A
        solve older than the last slew is stale BY DEFINITION, and an
        identification that outlives its slew is the most confidently wrong thing
        this feature could produce.

        Deliberately public and deliberately cheap (no I/O, no raise) so callers
        outside this module -- the mount routes in ``api/app.py``, the sequence
        engine's slew step -- can hold the invariant without owning any of it.
        """
        if self.field_solve is None:
            return
        self.field_solve = None
        self._pointing_field_cache = None
        bus.log("info", f"field identification cleared: {reason}", "solve")
        bus.publish("preview_field", preview_id=None, field=None, reason=reason)

    def _field_stale_threshold_deg(self) -> float:
        try:
            opt = self.effective_optics()
            fov = max(opt.get("fov_w_deg") or 0.0, opt.get("fov_h_deg") or 0.0)
        except Exception:                       # pragma: no cover - defensive
            fov = 0.0
        return max(self._FIELD_STALE_MIN_DEG, fov * self._FIELD_STALE_FOV_FRAC)

    def _current_field_solve(self) -> "_FieldSolve | None":
        """The current solve, or None once the mount's own report says the rig
        has moved off it.

        This is the catch-all that does not require every motion path in the
        codebase to remember to call ``invalidate_field_solve``: the API's slew
        and park routes change what the mount reports, and comparing that report
        against the one recorded at solve time notices. It is NOT a substitute
        for the explicit invalidation -- a mount that loses steps keeps
        reporting the old position, which is exactly the AM5 failure this rig
        has already had -- so both exist and the explicit one is the primary.
        """
        fs = self.field_solve
        if fs is None:
            return None
        if fs.mount_ra is None or self._last_pointing is None:
            return fs
        from .catalog.coords import angular_sep_deg

        ra, dec, _at = self._last_pointing
        moved = angular_sep_deg(fs.mount_ra, fs.mount_dec, ra, dec)
        if moved > self._field_stale_threshold_deg():
            self.invalidate_field_solve(
                f"the mount has moved {moved:.2f}° since the last plate solve")
            return None
        return fs

    async def note_field_solve(self, wcs, *, preview_id: int | None,
                               data_w: int, data_h: int) -> dict | None:
        """Adopt a plate solve as THE answer to "what is the rig looking at",
        and publish it.

        Returns the wire block (see ``_field_block``) or None when the solution
        carries no scale / the catalog work fails -- both of which leave the
        previous state alone rather than replacing it with a worse one. Never
        raises into a caller: a solve that cannot be turned into a name is still
        a perfectly good solve for everything else it was run for.
        """
        try:
            from .catalog.region import objects_in_frame

            frame = await asyncio.to_thread(
                objects_in_frame, wcs, int(data_w), int(data_h))
        except Exception as e:  # noqa: BLE001 - identification is never load-bearing
            bus.log("warning",
                    f"could not identify the solved field ({e}); the solve "
                    "itself is unaffected", "solve")
            return None
        ra = dec = None
        if self._last_pointing is not None:
            ra, dec, _at = self._last_pointing
        self.field_solve = _FieldSolve(
            wcs=wcs, solved_at=time.time(), preview_id=preview_id,
            data_w=int(data_w), data_h=int(data_h),
            mount_ra=ra, mount_dec=dec, frame=frame)
        self._pointing_field_cache = None
        ident = frame.get("identification")
        if ident:
            bus.log("info",
                    f"field identified as {ident['id']} "
                    f"({ident['sep_arcmin']:.1f}' off centre"
                    + ("" if ident["confident"] else
                       f", not clearly {ident['id']} rather than {ident['runner_up']}")
                    + ")", "solve")
        block = self._field_block(preview_id)
        # A LATE SOLVE PATCHES, IT DOES NOT RE-PUBLISH. _solve_and_stamp finishes
        # seconds after the picture is already on screen; re-publishing the whole
        # preview to carry a name would push the JPEG again over field WiFi for a
        # 200-byte change.
        bus.publish("preview_field", preview_id=preview_id, field=block)
        return block

    def _pointing_field(self) -> dict | None:
        """A PROVISIONAL identification from the mount's own report -- offered,
        never recorded.

        Worth showing: it genuinely helps a human decide whether the scope is
        roughly where they meant it to be, and on a rig with per-frame solving
        off (the default) it is the only answer available. Worth distrusting:
        this mount has been found 50 degrees from where it claimed. So it carries
        ``source: "pointing"`` all the way to the pixel that renders it, it is
        worded as what the MOUNT SAYS rather than what the sky IS, and
        ``capture`` will not adopt it into any header card.

        No markers come with it. Placing an object on the picture needs a plate,
        and reported pointing is not one.
        """
        if self._last_pointing is None:
            return None
        ra, dec, at = self._last_pointing
        if time.time() - at > self._POINTING_MAX_AGE_S:
            return None
        key = (round(ra, 3), round(dec, 3))
        if self._pointing_field_cache and self._pointing_field_cache[0] == key:
            return self._pointing_field_cache[1]
        try:
            opt = self.effective_optics()
            fov = max(opt.get("fov_w_deg") or 0.0, opt.get("fov_h_deg") or 0.0)
            diag = opt.get("fov_diag_deg") or 0.0
        except Exception:                       # pragma: no cover - defensive
            fov = diag = 0.0
        if not fov:
            # No optics configured: there is no field to ask about, so there is
            # nothing honest to say. Silence beats a guessed field width.
            self._pointing_field_cache = (key, None)
            return None
        from .catalog.region import identify_field, scored_region_rows

        rows, _notes, _tr = scored_region_rows(
            ra, dec, max(diag, fov) / 2.0, fov_deg=fov, limit=60,
            kinds=("dso", "star"), site_derived=False)
        ident = identify_field(rows, fov)
        block = None if ident is None else {
            "source": "pointing",
            "solved_at": at,
            "id": ident,
            "objects": [],
        }
        self._pointing_field_cache = (key, block)
        return block

    def _field_block(self, preview_id: int | None) -> dict | None:
        """The ``PreviewInfo.field`` block for one published preview, or None.

        TWO FACTS WITH TWO DIFFERENT LIFETIMES, in one block on purpose:

        * ``id`` names THE SKY THE RIG IS ON. It survives past the frame that was
          solved, because that is what makes a goto's own solve pay for the
          identification of every sub that follows it with per-frame solving
          still off. It carries ``solved_at`` so the UI can age it, and it is
          dropped the moment anything moves.
        * ``wcs`` and ``objects`` describe THIS FRAME'S PIXELS, and are present
          ONLY when this preview IS the frame that was solved. A WCS from the
          previous frame drawn over this one would be markers that look right and
          are not, which is the failure class this codebase pays for most often.
        """
        fs = self._current_field_solve()
        if fs is None:
            return self._pointing_field()
        frame = fs.frame
        block: dict[str, Any] = {
            "source": "solve",
            "solved_at": fs.solved_at,
            "id": frame.get("identification"),
            "center": frame.get("center"),
            "fov_w_deg": frame.get("fov_w_deg"),
            "fov_h_deg": frame.get("fov_h_deg"),
            "catalog_degraded": frame.get("catalog_degraded"),
            "objects": [],
        }
        notes = list(frame.get("notes") or [])
        # F4, and free: the one condition that cost this rig a night. When the
        # mount's report and the plate disagree by more than a field, SAY SO --
        # the annotations must not silently agree with a pointing readout the
        # solve contradicts.
        if self._last_pointing is not None and frame.get("center"):
            from .catalog.coords import angular_sep_deg

            ra, dec, _at = self._last_pointing
            c = frame["center"]
            off = angular_sep_deg(ra, dec, c["ra_hours"], c["dec_deg"])
            if off > self._field_stale_threshold_deg():
                block["pointing_disagrees_deg"] = round(off, 3)
        if preview_id is not None and fs.preview_id == preview_id:
            w = fs.wcs
            block["wcs"] = {
                "crval1": float(w.crval1), "crval2": float(w.crval2),
                "crpix1": float(w.crpix1), "crpix2": float(w.crpix2),
                "cd11": w.cd11, "cd12": w.cd12, "cd21": w.cd21, "cd22": w.cd22,
                "cdelt1": w.cdelt1, "cdelt2": w.cdelt2, "crota2": w.crota2,
            }
            block["objects"] = frame.get("objects") or []
            block["data_width"] = fs.data_w
            block["data_height"] = fs.data_h
        if notes:
            block["notes"] = notes
        return block

    def _object_cards(self, target: str, frame_type: str
                      ) -> tuple[str, list[tuple[str, object, str]]]:
        """``(what OBJECT should say, the provenance cards)`` for one frame.

        THE ADOPTION RULE, in one place so it can be read in one breath:

          * The operator's string ALWAYS wins OBJECT. It is never overwritten and
            never merged with. If they typed "Veil east" over a field that solves
            as NGC 6992, the file says "Veil east" and ``OBJCTID`` says NGC 6992
            — the disagreement is recorded, not resolved.
          * A derived identification may fill OBJECT only when all three hold:
            the operator left it EMPTY, the identification is ``confident``
            (§ ``region.identify_field``), and it came from a PLATE SOLVE. Today
            an empty name writes no OBJECT card at all, so this replaces nothing
            — it fills a hole that every stacker downstream currently reads as
            "unknown target".
          * ``OBJCTID``/``OBJIDSRC``/``OBJIDSEP`` are written whenever there is an
            identification, adopted or not, so the provenance is IN THE FILE and
            not only in a log. Omitted entirely when there is none: an absent card
            beats a wrong one, the same rule ``_solve_and_stamp`` already applies
            to the WCS itself.
          * A DARK, BIAS or FLAT gets no identification of any kind. There is no
            sky in it to have solved, and the last field's name stamped on a dark
            is a lie the calibration library would believe.
        """
        typed = (target or "").strip()
        if frame_type.upper() != "LIGHT":
            return target, []
        ident = self.field_identification()
        if not ident:
            return target, []
        cards: list[tuple[str, object, str]] = [
            ("OBJCTID", ident["id"], "Field identified as (derived, informational)"),
            ("OBJIDSRC", "solve", "How OBJCTID was derived"),
            ("OBJIDSEP", round(float(ident["sep_arcmin"]), 2),
             "OBJCTID offset from field centre (arcmin)"),
        ]
        if not typed and ident["confident"]:
            return ident["id"], cards
        return target, cards

    def field_identification(self) -> dict | None:
        """The identification a FITS header may record, or None.

        Solve-derived only. ``_pointing_field`` is deliberately not consulted:
        a header card is permanent metadata that every stacker downstream reads,
        and this mount's report is not evidence.
        """
        fs = self._current_field_solve()
        if fs is None:
            return None
        ident = fs.frame.get("identification")
        return dict(ident) if ident else None

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

    async def _publish_preview(self, frame, *,
                               wheel_slot: int | None = ...,
                               capture_request_id: str | None = None) -> dict:
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
        # OFF THE LOOP (#109). frame_stats makes six full passes over the array
        # including np.median, which sorts; a frame from this camera is
        # 26,108,352 pixels, so inline in the dict literal this blocked the
        # event loop for the whole of it, once per published frame.
        stats = await asyncio.to_thread(frame_stats, data, full_well)
        info: dict[str, Any] = {
            "id": pid,
            "stats": stats,
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
        # What the rig is looking at (#182). Absent when nothing has solved since
        # the last slew and the mount has said nothing recent either — an absence
        # the UI reads as "not identified yet" and explains, rather than a block
        # saying "unknown". Cached on the solve, so this costs a dict copy.
        field = self._field_block(pid)
        if field is not None:
            info["field"] = field

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
                    # Carry the well depth here too, or `clipped` vanishes for
                    # every frame of a Live View session while `max` keeps
                    # lighting the CLIP chip — the alarm without the advice.
                    #
                    # NOT a duplicate of the `stats` computed above: `data` was
                    # rebound to the stacked mean on the line before this, so
                    # this measures the stack the UI is being shown, while the
                    # first measured the sub. Only reachable while Live View is
                    # armed AND a running mean exists.
                    info["stats"] = await asyncio.to_thread(
                        frame_stats, data, full_well)
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
            # EVERY per-frame quality number, from the one detection pass above
            # (`stars`), in the one place the engine's gate can also be pointed
            # at: hfr / stars / star_list / star_flux_median / ecc / tilt. See
            # `imaging.stars.grade_frame` for why this is a function and not
            # six inline blocks.
            grade = await asyncio.to_thread(
                grade_frame, sub, full_well=info["full_well"], stars=stars)
            hfr, count = grade.get("hfr"), grade.get("stars")
            info.update({
                "histogram": hist_display,
                "histogram_linear": await asyncio.to_thread(compute_histogram, sub),
                "histogram_domain": "display",
                "display_width": dw, "display_height": dh,
                "mime": "image/jpeg", "has_lossless": True,
                "auto_levels": {"black": round(black, 4), "mid": round(mid, 4),
                                "white": round(white, 4)},
                "star_list": grade["star_list"],
            })
            # Absolute per-SUB SNR input (polish grab-bag (b)): the median
            # background-subtracted flux of the SAME trusted mid-bright stars the
            # marks come from, over the same single detection pass. The client
            # multiplies by its own e-/ADU gain to show a real "this sub" SNR.
            # Omitted entirely when no star passes the gate — honest abstain.
            if "star_flux_median" in grade:
                info["star_flux_median"] = grade["star_flux_median"]
            # Representative frame eccentricity = median of the trusted marks'
            # ecc (no second detection pass). setdefault so a backend-supplied
            # ecc (native/NINA) wins, mirroring hfr/stars below.
            if "ecc" in grade:
                info.setdefault("ecc", grade["ecc"])
            # Sensor-tilt / corner-vs-center inspector (PRO-13) — additive zone
            # map + pattern classification over the same trusted marks, no new
            # detection pass. None (too sparse) => key omitted entirely.
            if "tilt" in grade:
                info["tilt"] = grade["tilt"]
            # Image-derived cloud verdict, reusing the star count from the single
            # detection pass above (no second detect). Linear frames only — the
            # contrast metric needs unstretched pixels. Complements the
            # forecast-based cloud cover in weather.py with what the camera sees.
            if data_is_linear:
                # A FRAME TAKEN THROUGH A BLACKOUT SLOT SAYS NOTHING ABOUT THE
                # SKY, and this is knowable exactly rather than by heuristic:
                # the wheel is parked on a slot the operator flagged as carrying
                # no glass, so no photon from the sky reached this sensor.
                #
                # MEASURED, 2026-08-13. A cloud hold's probe frames went through
                # slot 7 because the hold had parked the wheel there. They are
                # black - median 241 against a 240 dark floor - and `cloud_score`
                # judged them "clear (12 bright stars, 9x noise)", twice. That
                # verdict is what released the hold and sent the run back out.
                #
                # Omitting the key is the honest answer, not a fabricated
                # "cloudy": `cloudstate.verdict_from_info` returns None when
                # `cloud` is absent, and the evaluator treats None as
                # indeterminate - fires nothing, re-arms nothing. A hold whose
                # probes are blind therefore keeps holding, which is the only
                # safe reading of "I cannot see".
                # THE SLOT THE CAPTURE ALREADY READ, when the caller has one.
                # It is also the more correct question: what mattered is the
                # slot that was in the beam when this frame was exposed, not
                # where the wheel has got to by the time the preview is built.
                blocked = await self._opaque_slot_in_beam(wheel_slot)
                if blocked is None:
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
            #
            # `measure_defocus`, not `measure_blob`: the blob is only a defocus
            # reading on a frame whose stars are NOT resolved. On 2026-09-07 at
            # 01:17 this line published r80 1134 on an in-focus 60 s L sub of
            # NGC 604 (grader HFR 3.32, 1294 stars) because the dominant source
            # in the frame was M33, and the panel read "far out of focus - blob
            # is 2268 px across - run coarse focus first". The star list from
            # the single detection pass above is handed over so the wrapper's
            # screen costs no second scan.
            if data_is_linear:
                from .imaging.defocus import measure_defocus
                blob = await asyncio.to_thread(measure_defocus, sub, stars=stars)
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

        if capture_request_id:
            info["capture_request_id"] = capture_request_id
            info["capture_saved"] = bool(saved_path)
        self.previews[pid] = entry
        self.preview_thumbs[pid] = entry.thumb
        self._trim_previews()
        bus.publish("preview", **external_preview(info))
        return info

    @staticmethod
    def _is_local_save(saved_path: str | None) -> bool:
        """True only when ``saved_path`` is a real file under CAPTURE_DIR, so the
        UI never offers a FITS download that will 404 (spec honesty rule #5).
        NINA saves on the imaging host → not local → no FITS download offered.

        Containment is decided by ``gallery.relpath_under_capture`` so that "is
        it in the library?" and "what is its relative path?" can never disagree
        — they were two separate `is_relative_to` calls, which is one edit away
        from a path this says is local and the relpath helper cannot express."""
        if not saved_path:
            return False
        from . import gallery as _gallery
        if _gallery.relpath_under_capture(saved_path) is None:
            return False
        try:
            return Path(saved_path).resolve().exists()
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
        # Narrowband flags restore on exactly the same terms as the blackout
        # ones — user-assigned, no hardware fallback, padded to the wheel's real
        # slot count. Without this the marking would survive the file and not
        # the reconnect, and a learn run the morning after would sweep three
        # narrowband slots at the broadband exposure again.
        narrowband = saved.get("narrowband")
        if isinstance(narrowband, list) and fw.filter_names:
            n = len(fw.filter_names)
            fw.filter_narrowband = [
                bool(narrowband[i]) if i < len(narrowband) else False
                for i in range(n)]
        # Per-filter capture settings restore on the same terms as the two flag
        # lists: user-assigned, no hardware fallback, padded to the wheel's real
        # slot count. Without this they would survive the file and not the
        # reconnect — and the reconnect is the event that erases everything else
        # the wheel does not report, which is exactly why this function exists.
        from .config import _opt_num
        for key, attr, cast in (("exposures", "filter_exposures", float),
                                ("gains", "filter_gains", int)):
            pinned = saved.get(key)
            if isinstance(pinned, list) and fw.filter_names:
                n = len(fw.filter_names)
                setattr(fw, attr, [
                    _opt_num(pinned[i], cast) if i < len(pinned) else None
                    for i in range(n)])

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
        screen would show a target the camera was not holding.

        Records the target as the STANDING request (``cooling.setpoint_c``) so a
        reconnect or a process restart can put it back. Recorded only after the
        camera accepts it — a target the hardware refused is not a promise worth
        keeping across a restart."""
        cam: Camera = self.require("camera")
        await self.cancel_warm("cooling was requested", finalize=False)
        await cam.set_cooler(True, target_c)
        self._remember_cooling(target_c)

    def _remember_cooling(self, target_c: float | None) -> None:
        """Persist (or clear) the standing cooling request. Never raises: a
        config write that fails must not fail the cooling command that already
        succeeded on the hardware."""
        try:
            config_store.set_cooling_setpoint(target_c)
        except Exception as e:      # noqa: BLE001 - the TEC is already set
            bus.log("warning", f"could not record the cooling setpoint "
                               f"({e}); it will not survive a restart", "camera")

    async def restore_cooling(self) -> bool:
        """Re-apply the standing cooling request after a connect. True if it did.

        WHY THIS EXISTS. On 2026-08-09 a reconnect — issued to recover a dead
        mount link — took the camera from cooler-on/-10.0 °C to cooler-off with
        the target reset to 0.0 °C, and by 08:39 the sensor read +26.6 °C.
        Nothing logged the change and nothing restored it, so the *target itself*
        was lost: even a later "resume cooling" would have aimed at the wrong
        number, and any run started afterwards would have shot warm frames
        against a stale SET-TEMP (#153, the same failure one layer down).

        The camera cannot be asked what it was doing before it was reopened, so
        the operator's intent has to come from config. This is the one place that
        reads it back.

        SAYS SO EITHER WAY. Silence is what made the original event invisible:
        a restore that happens is worth a line, and a restore that FAILS is worth
        a louder one, because the alternative is a night of warm frames nobody
        was told about.
        """
        cam = self.devices.get("camera")
        # RECORD THE CAPABILITY BEFORE THE EARLY RETURNS. `can_cool` exists only
        # while a camera is connected, and the Plan tab needs the answer at
        # 19:00 with the rig unplugged (AppConfig.camera_can_cool_seen). This is
        # the one function that already runs on every connect with the camera in
        # hand. It must stay ABOVE the `target is None` return, because the run
        # with no setpoint is precisely the run the advisory is about.
        if cam is not None and getattr(cam, "connected", False):
            config_store.remember_camera_can_cool(
                bool(getattr(cam, "can_cool", False)))
        target = getattr(config_store.cfg().cooling, "setpoint_c", None)
        if target is None:
            return False
        if cam is None or not getattr(cam, "connected", False):
            return False
        if not hasattr(cam, "set_cooler"):
            return False
        try:
            await asyncio.wait_for(cam.set_cooler(True, float(target)),
                                   cooling.WARM_CMD_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:      # noqa: BLE001 - report it, never crash connect
            bus.log("error",
                    f"cooling was NOT restored to {target:g} °C after connecting "
                    f"({e}) — the camera is warm and any frames taken now will "
                    f"carry the wrong SET-TEMP", "camera")
            return False
        bus.log("info", f"cooling restored to {target:g} °C after connecting",
                "camera")
        return True

    async def cancel_warm(self, reason: str, *, finalize: bool) -> bool:
        """Stop an in-flight warm ramp. Returns True if one was actually running.

        ``finalize=True`` finishes the warm the fast way (cooler OFF) — for "stop
        the ramp, I want it off now" and for rig teardown. ``finalize=False`` is
        for a caller taking ownership of the cooler in the very next statement
        (``cool_camera``); it is the ONLY case where leaving the TEC on is a
        defined state, because the caller is about to define it.

        NEITHER MODE TOUCHES ``cooling.setpoint_c``. Stopping the cooler is a
        live command; what temperature this rig images at is a standing
        preference. See the comment below for the night that distinction cost."""
        # THIS USED TO CLEAR THE STANDING SETPOINT, AND IT ERASED IT ON EVERY
        # CONNECT. The reasoning was the same as warm_camera's: finalize=True
        # means "off, now" — teardown, or an operator stopping the ramp — so the
        # standing request goes with it. But the operator half of that sentence
        # was never true: the ONLY caller that passes finalize=True is
        # ``_teardown``, which every connect path runs FIRST (and which
        # /api/disconnect and the lifespan shutdown run too). So the clear was
        # never an operator decision — it was a side effect of the rig going
        # away, and it fired on the way IN as well as the way out.
        #
        # Measured 2026-08-23: POST /api/config setpoint -15, then
        # POST /api/connect/sim, and the setpoint reads null. That also made
        # ``restore_cooling`` — the #153/#204 fix whose whole job is to put the
        # camera back on the operator's number after a reconnect — permanently
        # dead code, because _teardown had cleared the value it reads moments
        # earlier in the same call. It has apparently never once fired.
        #
        # Two different facts, the same split warm_camera already makes: "stop
        # the cooler now" is a live command and is what the set_cooler(False)
        # below carries out; "image at -10" is a standing preference that only
        # the operator changes. Teardown owns the first and must not touch the
        # second. The preference is cleared deliberately by POSTing
        # {"cooling": {"setpoint_c": null}} to /api/config, which is a real
        # operator path and the only one that should be.
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
        # WARMING STOPS THE COOLER. IT DOES NOT CHANGE WHAT TEMPERATURE THIS RIG
        # IMAGES AT.
        #
        # This used to clear `cooling.setpoint_c` here, reasoning that "warm is
        # the moment somebody stops asking for cooling" and that keeping it
        # would leave a stale order to re-cool at 08:00. But the dawn wind-down
        # warms EVERY night, so the operator's setpoint survived exactly one
        # session: set to -10 and confirmed on 2026-08-19 at 23:27, gone by the
        # next night, and 63 frames were shot at ~17C ambient before anyone
        # looked. The same thing had already cost 35 frames the night before.
        #
        # Two different facts. "Stop the cooler now" is a live command and is
        # what every `set_cooler(False)` below carries out. "Image at -10" is a
        # standing preference that only the operator changes — and the run
        # re-asserts it per frame (`SequenceEngine._enforce_cooling`), so a
        # stale order cannot outlive the intent that reads it.
        #
        # The preference is cleared deliberately by POSTing
        # {"cooling": {"setpoint_c": null}} to /api/config. It is NOT cleared by
        # any device path any more: the one that remained (cancel_warm's
        # finalize branch) turned out to be reachable only from _teardown, so it
        # erased the setpoint on every connect instead of on any decision.
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
        # FROM THE STATE, because it is computed in the routine that STARTS
        # the warm, not here. It was used below as if it were a local and
        # was not one, so the ramp raised NameError the instant it reached
        # its assumed ambient and tried to extend - killing the whole ramp
        # and falling back to switching the TEC off, which is precisely the
        # plunge #134 and #154 exist to prevent. Caught on the rig
        # 2026-08-16 09:48, never by a test: the extend logic is covered
        # thoroughly as a PURE FUNCTION and had no caller-level test.
        ambient_from = str(state.get("ambient_from") or "assumed")
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
                    # An ASSUMED ambient the sensor is still tracking was simply
                    # too low — extend and let the lead check above end the ramp
                    # at the real air temperature, instead of switching the TEC
                    # off here and handing the sensor the rest of the climb in
                    # one jump (measured 2026-08-06: assumed 20 °C, air 32 °C).
                    higher = cooling.warm_extend_ambient_c(
                        ambient_c, ambient_from, temp, setpoint)
                    if higher is not None:
                        if ambient_from == "assumed":
                            bus.log("info",
                                    f"warm ramp: the sensor is still following "
                                    f"at {temp:.1f} °C, so the assumed "
                                    f"{ambient_c:.0f} °C ambient is low — "
                                    f"climbing to {higher:.0f} °C", "camera")
                        ambient_c = higher
                        state["ambient_c"] = ambient_c
                        continue
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
                                   step: int | None = None,
                                   steps_each_side: int = 4,
                                   binning: int = 2,
                                   narrowband: list[bool] | None = None,
                                   nb_exposure_s: float | None = None,
                                   nb_gain: int | None = None) -> dict:
        """Autofocus every filter slot and persist the ref-relative offsets.

        A slot whose autofocus FAILS (a starless narrowband slot is the usual
        cause) KEEPS its prior offset and is reported in ``kept`` — writing a
        bogus 0 there would defocus that filter on every future exposure.

        NARROWBAND SLOTS GET THEIR OWN EXPOSURE AND GAIN. On 2026-08-08 this
        run measured L, R, G and B and could not focus S, Ha or Oiii, because
        one exposure was used for the whole wheel and a 3-7 nm passband
        delivers a star 40-100x fainter than luminance does. ``narrowband``
        (when given) marks the slots and is PERSISTED before the run starts —
        the wheel does not change often, and a marking that evaporated when the
        run was cancelled would have to be re-entered every time. ``nb_exposure_s``
        / ``nb_gain`` override the derived pair (see
        ``focus.filter_offsets.narrowband_sweep_settings``)."""
        from .focus.autofocus import run_autofocus
        from .focus.filter_offsets import (default_ref_slot,
                                           narrowband_sweep_settings,
                                           offsets_from_positions)
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

        # The marking is persisted BEFORE anything is swept. It is a property of
        # the wheel, not of this run, and the operator who ticked three boxes
        # and then cancelled should not have to tick them again.
        if narrowband is not None:
            await self.set_filter_names(names, None, None, list(narrowband))
        nb_exp, nb_g = narrowband_sweep_settings(
            exposure_s, gain,
            hcg_threshold_gain=getattr(cam, "hcg_threshold_gain", None))
        if nb_exposure_s is not None:
            nb_exp = float(nb_exposure_s)
        if nb_gain is not None:
            nb_g = int(nb_gain)
        # ---- what each slot will actually sweep at -------------------------
        #
        # Three sources, most specific first:
        #   1. the slot's OWN pinned exposure/gain, when the operator has set
        #      one. This is the only place in the product where a per-filter pin
        #      is AUTHORITATIVE rather than a default, and the reason is that
        #      there is no plan here to read it off — a sweep is not a reviewable
        #      artifact, it is a measurement that either works or wastes the
        #      night. The 2026-08-08 run measured L/R/G/B and could not focus S,
        #      Ha or Oiii at the single setting it had.
        #   2. the narrowband heuristic (or this run's override of it), for a
        #      slot marked narrowband with no pin of its own.
        #   3. the run's broadband exposure/gain.
        #
        # Computed UP FRONT rather than inside the loop so the summary below can
        # describe what will really happen. A line claiming every narrowband
        # slot sweeps at nb_exp, while three of them quietly used their own
        # pins, is the defect class this repo keeps finding in its own copy.
        def _slot_settings(i: int) -> tuple[float, int, bool]:
            """``(exposure_s, gain, from_pin)`` for slot ``i``."""
            p_exp, p_gain = fw.slot_capture_settings(i)
            nb = fw.is_narrowband(i)
            base_exp, base_gain = (nb_exp, nb_g) if nb else (exposure_s, gain)
            # Either HALF may be pinned independently: an operator who knows Ha
            # needs 30 s but is happy with the run's gain pins only the exposure.
            return (float(p_exp) if p_exp is not None else base_exp,
                    int(p_gain) if p_gain is not None else base_gain,
                    p_exp is not None or p_gain is not None)

        pinned = [i for i in range(n_slots) if _slot_settings(i)[2]]
        nb_slots = [i for i in range(n_slots)
                    if fw.is_narrowband(i) and i not in pinned]
        if nb_slots:
            bus.log("info",
                    f"filter offsets: {len(nb_slots)} narrowband slot"
                    f"{'' if len(nb_slots) == 1 else 's'} "
                    f"({', '.join(names[i] or str(i) for i in nb_slots)}) will "
                    f"sweep at {nb_exp:g}s / gain {nb_g} instead of "
                    f"{exposure_s:g}s / gain {gain}", "filter_offsets")
        if pinned:
            bus.log("info",
                    "filter offsets: "
                    + "; ".join(
                        f"{names[i] or i} at {_slot_settings(i)[0]:g}s / gain "
                        f"{_slot_settings(i)[1]}" for i in pinned)
                    + " — each from that filter's own saved settings",
                    "filter_offsets")

        best_by_slot: dict[int, int] = {}
        bus.publish("filter_offsets", state="running", slot=None, of=n_slots,
                    done_slots=[], narrowband=list(fw.filter_narrowband),
                    nb_exposure_s=nb_exp, nb_gain=nb_g)
        try:
            # Reference first: without it there is nothing to measure against,
            # so a failed reference aborts before burning time on the rest.
            for i in [ref_slot] + [s for s in range(n_slots)
                                   if s != ref_slot and s not in blackout]:
                nb = fw.is_narrowband(i)
                slot_exp, slot_gain, _ = _slot_settings(i)
                bus.publish("filter_offsets", state="running", slot=i,
                            of=n_slots, name=names[i],
                            done_slots=sorted(best_by_slot),
                            narrowband=nb, exposure_s=slot_exp, gain=slot_gain)
                await fw.set_position(i)
                try:
                    res = await run_autofocus(
                        cam, foc, exposure_s=slot_exp, gain=slot_gain, step=step,
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
                               opaque: list[bool] | None = None,
                               narrowband: list[bool] | None = None,
                               exposures: list | None = None,
                               gains: list | None = None) -> dict:
        """Apply + persist user filter slot names (and optional focuser offsets,
        blackout flags, narrowband flags and per-filter capture settings) for the
        active profile (UX-05). Blank names keep the hardware fallback for that
        slot. Returns the resulting names/offsets/opaque/narrowband/exposures/
        gains."""
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
        if narrowband is not None:
            # Sent whole, like ``opaque``: the caller owns the full list, so
            # un-marking the last narrowband slot has to be expressible.
            fw.filter_narrowband = [
                bool(narrowband[i]) if i < len(narrowband) else False
                for i in range(len(base))]
        if fw.filter_narrowband:
            # A slot with no light path is not narrowband, whatever was ticked:
            # the two flags would otherwise combine into a longer exposure of
            # nothing. Applied on every save, not only when narrowband is sent,
            # so marking a slot blackout later also clears it.
            fw.filter_narrowband = [False if fw.is_opaque(i) else n
                                    for i, n in enumerate(fw.filter_narrowband)]
        # Per-filter capture settings. Sent whole like the two flag lists, and
        # for the same reason: the caller owns the list, so CLEARING a pin has
        # to be expressible. A cleared slot is None, not 0 — 0 is a real gain.
        from .config import _opt_num
        if exposures is not None:
            fw.filter_exposures = [
                _opt_num(exposures[i], float) if i < len(exposures) else None
                for i in range(len(base))]
        if gains is not None:
            fw.filter_gains = [
                _opt_num(gains[i], int) if i < len(gains) else None
                for i in range(len(base))]
        if fw.filter_exposures or fw.filter_gains:
            # A blackout slot carries no light path, so an exposure and gain
            # "for that filter" describe nothing — cleared on every save, on the
            # same terms as its focus offset above and its narrowband flag,
            # so marking a slot blackout later also clears these.
            n = len(base)
            def _blank(seq):
                seq = list(seq) + [None] * (n - len(seq))
                return [None if fw.is_opaque(i) else v
                        for i, v in enumerate(seq[:n])]
            fw.filter_exposures = _blank(fw.filter_exposures)
            fw.filter_gains = _blank(fw.filter_gains)
        from .config import config_store, save_filter_config
        save_filter_config(config_store.cfg().active_profile_id,
                           fw.filter_names, fw.filter_offsets,
                           list(fw.filter_opaque) or None,
                           list(fw.filter_narrowband) or None,
                           list(fw.filter_exposures) or None,
                           list(fw.filter_gains) or None)
        return {"names": fw.filter_names, "offsets": fw.filter_offsets,
                "opaque": list(fw.filter_opaque),
                "narrowband": list(fw.filter_narrowband),
                "exposures": list(fw.filter_exposures),
                "gains": list(fw.filter_gains)}

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

        # The settings, ON THE HUB and not only in the closure above.
        # ``yield_camera_for`` documents "restarting is not reliably possible:
        # the loop's exposure/gain/offset/binning live in the ``start_loop``
        # closure, not on the hub, so 'resume what was running' would mean
        # inventing settings". This is that objection answered — nothing
        # resumes automatically because of it (see ``resume_loop``), but the
        # one caller that has a good reason to can now do it with the REAL
        # settings instead of a guess.
        self._last_loop_settings = {
            "exposure_s": exposure_s, "gain": gain, "offset": offset,
            "binning": binning, "frame_type": frame_type,
        }
        self._loop_task = asyncio.create_task(_loop())
        bus.publish("capture_loop", running=True)

    async def resume_loop(self) -> bool:
        """Restart the live loop with the settings it was last started with.

        NOT a general undo for ``yield_camera_for``, which stays deliberate
        about leaving the loop stopped: after a goto the frames would be of a
        DIFFERENT field, and a silently-resumed Live View would read as the old
        target drifting. A ROTATE is the case where that reasoning does not
        apply — the tube has not moved, only the camera angle has, so the
        frames that come back are the same field the user was already watching,
        which is precisely what they wanted to see rotate.

        False when there is nothing to resume, so a caller cannot mistake
        "never had a loop" for "resumed one"."""
        settings = getattr(self, "_last_loop_settings", None)
        if not settings:
            return False
        await self.start_loop(**settings)
        bus.log("info", "live view resumed", "capture")
        return True

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

    # ------------------------------------------- Session stack (run composite)
    # Live View above stacks what the camera is looking at RIGHT NOW, in one
    # channel, for as long as the loop runs. This stacks what the SEQUENCE has
    # accepted, per filter, for as long as the target lasts, and composites the
    # filters into colour. Different question, different lifetime, different
    # object -- see imaging/sessionstack.py.

    def start_session_stack(self, *, backfill: bool = False) -> dict:
        """Switch the stack on, optionally folding in what the run already shot.

        ``backfill`` DEFAULTS OFF, and that is a decision rather than caution.
        The pass reads every accepted sub of the run back off disk, debayers or
        bins it and registers it; on this rig's 26-megapixel frames that is
        roughly a second each, so an eight-hour night is several minutes of a
        Pi's CPU spent while a sequence is running. A switch labelled "stack the
        subs" must not be able to do minutes of unannounced work to somebody who
        only wanted the next frame stacked. The UI ticks the box for them --
        with the count of what it will read shown next to it -- because that is
        an informed press; a bare POST keeps the behaviour it has always had.
        """
        self.session_stack.start()
        bus.log("info", "Session stack on: accepted subs stack per filter",
                "capture")
        if backfill:
            return self.session_stack_backfill()
        # Through session_stack_status, not the stacker's own status: every one
        # of these four routes answers the SAME shape, so the client has one
        # type for the reply and never has to ask which call it came from.
        return self.session_stack_status()

    def stop_session_stack(self) -> dict:
        """Off, pixels released, and any backfill in flight abandoned (``stop``
        resets, which moves the generation the worker watches)."""
        self.session_stack.stop()
        return self.session_stack_status()

    def reset_session_stack(self) -> dict:
        """Throw the pixels away, keep the switch AND the identity. Dropping the
        target/run here would make the next accepted frame reset a second time,
        which is harmless but means the count the user just cleared briefly
        comes back."""
        st = self.session_stack
        st.reset(st.target, st.session)
        return self.session_stack_status()

    # ------------------------------------------------- Session stack backfill

    def _live_session(self):
        """The run's session ledger, or None when no run is live.

        The ledger is the ONLY record of which subs the quality gate accepted --
        a rejected frame is written to disk under the same name as an accepted
        one (``hfr_reject_action`` defaults to ``warn``) -- so with no live run
        there is nothing to backfill, however many FITS are sitting in the
        capture directory.
        """
        return getattr(getattr(self, "engine", None), "_session", None)

    def session_stack_backfill_items(self) -> list:
        """The subs the backfill would fold in, right now. Cheap: the ledger is
        already in memory and no file is opened."""
        session = self._live_session()
        if session is None:
            return []
        try:
            return plan_backfill(session, stacker=self.session_stack)
        except Exception as e:                      # pragma: no cover - guard
            bus.log("warning", f"session stack backfill plan failed: {e}",
                    "capture")
            return []

    def session_stack_backfill(self) -> dict:
        """Start the backfill on a worker thread. Returns the status at once.

        A thread rather than a task: the work is a blocking FITS read followed
        by a numpy accumulation, neither of which yields, so on the event loop
        it would stall the frame loop, the WebSocket and the whole API for as
        long as it ran. The stacker's lock is what makes the two paths safe --
        see ``imaging/stackbackfill.run_backfill``.
        """
        st = self.session_stack
        if not st.enabled:
            return st.status()
        if st.backfill.running:
            return self.session_stack_status()     # one pass at a time
        items = self.session_stack_backfill_items()
        st.backfill_begin(len(items))
        if not items:
            bus.log("info", "Session stack: nothing earlier in this run to "
                            "stack", "capture")
            return self.session_stack_status()

        def _work() -> None:
            def _note(msg: str) -> None:
                bus.log("warning", f"session stack backfill: {msg}", "capture")
            p = run_backfill(st, items, on_error=_note)
            bus.log("info", f"Session stack backfill done: {p.added} of "
                            f"{p.total} subs stacked"
                            + (f", {p.skipped} already in" if p.skipped else "")
                            + (f", {p.failed} unusable" if p.failed else ""),
                    "capture")

        bus.log("info", f"Session stack: reading {len(items)} earlier subs of "
                        "this run", "capture")
        try:
            threading.Thread(target=_work, name="session-stack-backfill",
                             daemon=True).start()
        except RuntimeError as e:               # out of threads
            # The counter is already armed, so failing to start the worker
            # without closing it would leave the panel reading "running, 0 of
            # 90" for the rest of the night.
            st.backfill_finish(f"could not start: {e}")
            bus.log("warning", f"session stack backfill did not start: {e}",
                    "capture")
        return self.session_stack_status()

    def session_stack_status(self) -> dict:
        """The stacker's own status plus what a backfill COULD still fold in.

        ``backfill.available`` is the stacker's blind spot: it knows which
        frames it has consumed and nothing about which exist. Counting them is a
        list comprehension over an in-memory ledger, so it is honest to compute
        it on every poll rather than caching a number that goes stale one
        exposure later.
        """
        st = self.session_stack.status()
        block = st.get("backfill")
        if isinstance(block, dict):
            block["available"] = len(self.session_stack_backfill_items())
        return st

    def session_stack_preview(self, size: int = 1600):
        """(jpeg, meta) for the composite, or None when nothing is stacked."""
        return self.session_stack.rgb_preview(size)

    def session_stack_add(self, info: dict, *, target: str = "") -> str | None:
        """Fold the light frame ``info`` describes into the session stack.

        Called from the sequence engine's frame loop with the frames its quality
        gate ACCEPTED, so what the composite shows is exactly what the run is
        keeping. The pixels are not in ``info`` -- they are the linear sub the
        preview ring retained for this frame id (``PREVIEW_LINEAR_KEEP`` == 2,
        and this runs one statement after the capture that filled it).

        TOTAL BY CONSTRUCTION. A preview feature must not be able to end a
        night: every failure path here returns None, and the one that could
        surprise us (a numpy/PIL error deep in the stacker) is logged once per
        run rather than raised into the frame loop.

        The sub's own ``saved_path`` is passed as its identity so that a later
        backfill skips it instead of stacking the same photons twice, and its
        Bayer pattern is passed so a one-shot-colour rig gets a colour composite
        rather than the grey one a block mean over a mosaic produces.
        """
        if not self.session_stack.enabled or not isinstance(info, dict):
            return None
        entry = self.previews.get(info.get("id"))
        sub = getattr(entry, "linear", None) if entry is not None else None
        if sub is None:
            # A NINA frame carries no linear pixels (display-domain bytes only),
            # and a frame that has aged out of the linear window has none left.
            return None
        try:
            # The run's report id is its identity: a second run on the same
            # target starts a new picture rather than resuming the last one.
            run = getattr(getattr(self.engine, "reporter", None), "id", "") or ""
            return self.session_stack.add(
                sub, info.get("filter"), float(info.get("exposure_s") or 0.0),
                target=target, session=str(run),
                bayer_pattern=effective_bayer(info.get("bayer_pattern"),
                                              info.get("binning")),
                key=info.get("saved_path"))
        except Exception as e:                      # pragma: no cover - guard
            if not getattr(self, "_session_stack_warned", False):
                self._session_stack_warned = True
                bus.log("warning", f"session stack disabled for this frame: {e}",
                        "capture")
            return None

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

    def _enqueue_thumb(self, path: "Path") -> None:
        """Queue one saved frame for background thumbnail rendering.

        Never blocks, never raises, never grows without bound — the same three
        promises ``_enqueue_wcs_stamp`` makes, for the same reason: this runs
        inside a capture, and a capture must not be able to fail because a
        convenience did.

        Overflow is DROP-OLDEST, and here that is close to free: a dropped warm
        is not a lost thumbnail, it is a thumbnail that renders on demand the
        first time someone scrolls to it. The lazy route is the fallback this
        whole path is an optimisation over.
        """
        try:
            if self._thumb_queue is None:
                self._thumb_queue = asyncio.Queue()
            q = self._thumb_queue
            while q.qsize() >= THUMB_WARM_QUEUE_MAX:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:      # pragma: no cover - defensive
                    break
                q.task_done()
            q.put_nowait(Path(path))
            if self._thumb_task is None or self._thumb_task.done():
                self._thumb_task = asyncio.create_task(self._thumb_worker())
        except Exception:  # noqa: BLE001 - warming must never fail a capture
            pass

    def _note_frame_saved(self) -> None:
        """Poke the file-sync push runner. Never blocks, never raises.

        Imported at call time rather than at module scope: ``sync.runner`` reads
        the config store and this module is imported by half the server, so a
        top-level import here would add a cycle for the sake of one attribute.
        """
        try:
            from .sync.runner import runner as _sync_runner
            _sync_runner.note_saved()
        except Exception:  # noqa: BLE001 - a capture must never fail for this
            pass

    async def _thumb_worker(self) -> None:
        """Render queued thumbnails off the event loop, one at a time.

        Serial on purpose. The work is CPU-bound (a 26-megapixel stretch), the
        box also has to guide, and a pool would win nothing on a Pi while
        costing the whole machine's responsiveness at exactly the wrong moment.
        Frames arrive every 60 s and one takes ~1.5 s.
        """
        from . import gallery as _gallery
        q = self._thumb_queue
        if q is None:                           # pragma: no cover - defensive
            return
        while True:
            try:
                path = await asyncio.wait_for(q.get(), timeout=THUMB_WARM_IDLE_S)
            except asyncio.TimeoutError:
                return                          # idle: let the task retire
            except asyncio.CancelledError:      # pragma: no cover
                raise
            try:
                rel = Path(path).resolve().relative_to(
                    _gallery.capture_root().resolve()).as_posix()
                made = await asyncio.to_thread(_gallery.precompute, rel)
                if made:
                    bus.log("debug", f"warmed {made} thumbnail(s) for "
                                     f"{Path(path).name}", "gallery")
            except Exception:                   # noqa: BLE001 - warming is optional
                pass                            # the lazy route still covers it
            finally:
                q.task_done()

    async def _borrow_wheel_for_solve(self) -> int | None:
        """Drive the wheel to a slot a plate solve can actually see through.

        Returns the slot to RESTORE afterwards, or None when nothing moved.

        The wheel is borrowed rather than taken: a centring solve fires in the
        middle of a filter cycle, and the sequence engine's ``_apply_filter``
        derives its focuser offset delta from the wheel's REAL position at the
        start of each frame. Moving the wheel and leaving it moved would make
        that delta cross a slot boundary the focuser never travelled, and the
        focuser would end the frame offset by one filter's worth of steps. So
        the borrow is symmetric and the engine sees no change at all.

        Best-effort throughout: a wheel that will not answer must not turn a
        solve — the thing that RECOVERS pointing — into an exception. Every
        failure path here leaves the solve to run on whatever is loaded, which
        is exactly what it did before this existed.
        """
        from .focus.filter_offsets import solve_filter_slot
        fw = self.devices.get("filterwheel")
        if fw is None or not getattr(fw, "connected", False):
            return None
        try:
            names = list(getattr(fw, "filter_names", []) or [])
            if not names:
                return None
            current = await fw.get_position()
            configured = (frames_payload()["solve"] or {}).get("filter")
            want = solve_filter_slot(
                names,
                narrowband=getattr(fw, "filter_narrowband", []),
                opaque=getattr(fw, "filter_opaque", []),
                current_slot=int(current),
                configured=configured)
            if want is None or want == current:
                # Say so when the wheel is parked somewhere a solve cannot see
                # through and there is no better slot to move to. Silence here
                # is what made the 66' miss look like a solver problem.
                if want is None and names:
                    blocked = (fw.is_narrowband(int(current))
                               or fw.is_opaque(int(current)))
                    if blocked:
                        bus.log("warning",
                                f"plate solve is shooting through "
                                f"{names[int(current)]!r}, which passes little "
                                f"or no light, and this wheel has no "
                                f"luminance-class slot to move to — expect the "
                                f"solve to fail", "solve")
                return None
            bus.log("info", f"plate solve: filter {names[int(current)]!r} → "
                            f"{names[want]!r}", "solve")
            await fw.set_position(want)
            return int(current)
        except Exception as e:  # noqa: BLE001 — a solve must still be attempted
            bus.log("warning", f"plate solve: could not choose a filter ({e}); "
                               f"solving through whatever is loaded", "solve")
            return None

    async def _return_wheel_after_solve(self, slot: int | None) -> None:
        """Put the wheel back where ``_borrow_wheel_for_solve`` found it."""
        if slot is None:
            return
        fw = self.devices.get("filterwheel")
        if fw is None or not getattr(fw, "connected", False):
            return
        try:
            await fw.set_position(int(slot))
        except Exception as e:  # noqa: BLE001
            names = list(getattr(fw, "filter_names", []) or [])
            label = names[slot] if 0 <= slot < len(names) else f"slot {slot}"
            bus.log("warning", f"plate solve: could not return the wheel to "
                               f"{label} ({e}) — the next frame's filter move "
                               f"will correct it", "solve")

    async def measure_guide_offset(self, *, exposure_s: float = 4.0,
                                   guide_exposure_s: float = 4.0) -> dict:
        """Plate-solve BOTH cameras where the mount is now, and diff the centres.

        The whole measurement: the guide scope is bolted to the OTA and points
        somewhere else, so solve a frame through each and the difference is the
        offset. No star-hopping, no reticle, no tape measure.

        NEITHER FRAME IS SYNCED and the mount is never commanded. This reads
        the sky twice and returns arithmetic; a measurement that moved the
        mount between its two exposures would be measuring the mount.

        THE TWO SCOPES NEED DIFFERENT FOV HINTS. ASTAP's `-fov` narrows the
        scale search, and the guide scope's focal length is a different number
        entirely -- 150 mm against 801 mm here. Handing the main scope's hint
        to the guide frame is how a solvable field comes back "no solution",
        so the guide hint is scaled by the focal-length ratio from the same
        optics block that already stores both.

        Returns a plain dict, including both raw solves, so a caller can see
        WHY it failed and a human can sanity-check the geometry rather than
        being handed one number to trust.
        """
        # Imported in-function, as every other caller in this file does: the
        # providers module reaches back into hub for capability routing and a
        # module-level import here is a cycle.
        from . import providers as _providers

        cam: Camera = self.require("camera")
        guide_cam = self.devices.get("guide_camera")
        if guide_cam is None:
            raise DeviceError("no guide camera is connected, so there is "
                              "nothing to measure the offset against")
        tel = self.devices.get("telescope")
        solver = _providers.pick_solver(self)
        await self.yield_camera_for("guide-scope offset")

        ra_hint = dec_hint = None
        if tel is not None:
            try:
                ra_hint, dec_hint = await tel.get_position()
                if ra_hint is not None:
                    ra_hint, dec_hint = await self.from_mount_frame(
                        tel, ra_hint, dec_hint)
            except Exception:
                ra_hint = dec_hint = None

        opt = self.effective_optics()
        main_fov = opt["fov_h_deg"] or None
        # THE GUIDE CAMERA'S OWN SENSOR, not the main one scaled.
        #
        # The first version computed `main_fov * (main_fl / guide_fl)`, which
        # silently assumes both cameras have the SAME SENSOR. They do not: the
        # imaging camera here is 6248 x 4176 and the guide camera a fraction of
        # that, so the hint came out about four times too wide and ASTAP
        # answered "no solution" on a field that solves easily. Measured on the
        # rig 2026-08-26: main solved, guide did not, first attempt.
        #
        # Read off the DEVICE, which populates sensor size and pixel pitch on
        # connect, so it needs nothing configured and cannot disagree with the
        # camera actually attached. The focal length still comes from
        # `effective_optics`, which resolves through the active profile -- the
        # global block an active profile overrides is the wrong layer, and
        # `effective_optics`'s own comment records that the native guider
        # already shipped that bug on this exact field.
        #
        # No sensor metadata -> None -> ASTAP searches. Slower and right beats
        # fast and wrong: a bad hint FAILS the solve, an absent one only costs
        # seconds.
        guide_fov = None
        guide_fl = opt.get("guide_focal_length_mm")
        g_h = getattr(guide_cam, "sensor_height", 0) or 0
        g_px = getattr(guide_cam, "pixel_size_um", 0.0) or 0.0
        if guide_fl and g_h and g_px:
            guide_fov = (g_h * g_px * 206.265 / guide_fl) / 3600.0

        async def _solve(device, seconds, path_name, fov, binning):
            async with self.exposure_guard("guide-scope offset"):
                frame = await device.expose(seconds, 200, 30, binning=binning)
            tmp = CAPTURE_DIR / "_solve" / path_name
            await asyncio.to_thread(save_fits, frame, tmp, ra_hours=ra_hint,
                                    dec_deg=dec_hint, instrument=device.name)
            bus.log("info", f"guide-offset: solving {device.name} "
                            f"(fov hint {fov or 'auto'})…", "solve")
            return await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                      fov_deg_hint=fov)

        # MAIN FIRST, and the order is not arbitrary: the imaging frame supplies
        # the position angle the offset is stored against, so a run that dies
        # after one solve has produced the more useful half.
        main = await _solve(cam, exposure_s, "guide_offset_main.fits", main_fov, 2)
        guide = await _solve(guide_cam, guide_exposure_s,
                             "guide_offset_guide.fits", guide_fov, 1)

        out = {
            "main": {"ok": main.success, "ra_hours": main.ra_hours,
                     "dec_deg": main.dec_deg, "rotation_deg": main.rotation_deg,
                     "scale": main.pixel_scale_arcsec, "message": main.message},
            "guide": {"ok": guide.success, "ra_hours": guide.ra_hours,
                      "dec_deg": guide.dec_deg, "rotation_deg": guide.rotation_deg,
                      "scale": guide.pixel_scale_arcsec, "message": guide.message},
            "camera": cam.name, "guide_camera": guide_cam.name,
        }
        if not (main.success and guide.success):
            out["offset"] = None
            which = "main" if not main.success else "guide"
            out["reason"] = (f"the {which} frame did not solve, so there is no "
                             f"pair to difference: {out[which]['message']}")
            # STORED AND PUBLISHED ON FAILURE TOO. Two solves take about forty
            # seconds, so this runs in a lane and the caller collects the
            # result later -- a failure that is not recorded is a button that
            # spins and then says nothing.
            self._last_guide_offset = out
            bus.publish("align", action="guide_offset", measurement=out)
            return out

        off = _guide_offset.offset_from_solves(
            main_ra_hours=main.ra_hours, main_dec_deg=main.dec_deg,
            main_pa_deg=main.rotation_deg, guide_ra_hours=guide.ra_hours,
            guide_dec_deg=guide.dec_deg, measured_ts=time.time(),
            camera=cam.name, guide_camera=guide_cam.name,
            note=f"main {main.pixel_scale_arcsec:.2f}\"/px, "
                 f"guide {guide.pixel_scale_arcsec:.2f}\"/px")
        out["offset"] = {
            "sep_arcsec": off.sep_arcsec, "pa_deg": off.pa_deg,
            "measured_ts": off.measured_ts, "measured_pa_deg": off.measured_pa_deg,
            "camera": off.camera, "guide_camera": off.guide_camera,
            "note": off.note,
        }
        bus.log("info",
                f"guide-scope offset: {off.sep_arcsec / 60.0:.2f} arcmin at "
                f"instrument PA {off.pa_deg:.1f} deg", "solve")
        self._last_guide_offset = out
        bus.publish("align", action="guide_offset", measurement=out)
        return out

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
        # Narrate the frame (2026-08-07, mirrors the polar driver's `activity`):
        # a centering solve is up to 3 s of shutter and then 5-15 s of ASTAP,
        # and every goto pays it up to three times — dead air the UI rendered
        # as a stuck busy button. `solve_activity` rides the `mount` channel so
        # every consumer of this one solve path (goto centering, meridian flip,
        # resume re-center) narrates for free. Cleared in the finally: a THROWN
        # solve must not leave "solving" blinking over an idle rig.
        bus.publish("mount", action="solve_activity", activity="exposing",
                    exposure_s=exposure_s)
        # A solve needs STARS, so it must not inherit whatever filter the run
        # happens to be on (#222). Borrowed and returned around the exposure
        # only — see ``_borrow_wheel_for_solve`` for why it is symmetric.
        borrowed_slot = await self._borrow_wheel_for_solve()
        try:
            try:
                async with self.exposure_guard("plate solve"):
                    frame = await cam.expose(exposure_s, 200, 30, binning=2)
            finally:
                await self._return_wheel_after_solve(borrowed_slot)
            self.last_frame = frame
            solve_preview = await self._publish_preview(frame)
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
            bus.publish("mount", action="solve_activity", activity="solving")
            result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                        fov_deg_hint=fov_hint)
        finally:
            bus.publish("mount", action="solve_activity", activity=None)
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
        # THE SOLVE THAT WAS ALREADY BEING PAID FOR (#182). Every goto centres by
        # calling this, so adopting its WCS here identifies the field for free on
        # a rig with per-frame solving still off — which is the default and, on a
        # Pi, the right default. Per-frame solving then buys only one extra thing:
        # markers that stay accurate as the mount drifts.
        #
        # AFTER the sync, deliberately. ``_current_field_solve`` invalidates on a
        # change in the mount's report, and a sync can move that report by degrees
        # (measured: 4 degrees, after a restart) — recording the pointing before
        # it would make this solve stale the instant it was adopted.
        self._note_pointing(result.ra_hours, result.dec_deg)
        # GN-07: this IS a plate-solve result (ASTAP/SimSolver), the same kind
        # of measurement goto_and_center's success branch records -- keep it
        # even when this call did not run through goto_and_center (rotator
        # sync, a bare solve_and_sync from the API), so `_solved_pointing` is
        # never staler than the freshest solve actually on record. It only
        # feeds a header while `_pointing_verified` is ALSO True (see
        # `_resolve_pointing`); recording it here does not by itself claim the
        # pointing is verified.
        self._solved_pointing = (result.ra_hours, result.dec_deg, time.time())
        if result.wcs is not None and isinstance(solve_preview, dict):
            await self.note_field_solve(
                result.wcs, preview_id=solve_preview.get("id"),
                data_w=int(solve_preview.get("data_width") or 0),
                data_h=int(solve_preview.get("data_height") or 0))
        return {"ra_hours": result.ra_hours, "dec_deg": result.dec_deg,
                "solver": solver.name, "pixel_scale": result.pixel_scale_arcsec}

    async def sync_rotator_to_sky(self, exposure_s: float = 3.0) -> dict:
        """Measure the sky position angle and tell the rotator where it is —
        WITHOUT moving anything.

        The sky↔mechanical offset already existed (``Rotator.sync_offset_deg``)
        and ``rotate_to_pa`` already established it, but only ever INSIDE a
        rotation: the four rotator routes were move/halt/reverse/rotate-to-pa,
        so the only way to learn the current angle was to command a rotation
        you might not want, and the offset was lost on reconnect with no way to
        re-establish it (operator, 2026-08-07 22:02).

        Returns the measured sky PA, the mechanical position it corresponds to,
        and the resulting offset. Raises DeviceError when there is no rotator,
        no camera, or the sky will not solve — never a silent no-op, because a
        rotator that quietly stays unsynced points every later framing wrong.
        """
        rot = self.require("rotator")
        cam: Camera = self.require("camera")
        from . import providers as _providers
        solver = _providers.pick_solver(self)
        await self.yield_camera_for("rotator sync")
        tel = self.devices.get("telescope")
        ra_hint = dec_hint = None
        if tel is not None and tel.connected:
            with contextlib.suppress(Exception):
                ra_hint, dec_hint = await tel.get_position()
                if ra_hint is not None:
                    ra_hint, dec_hint = await self.from_mount_frame(
                        tel, ra_hint, dec_hint)
        bus.publish("mount", action="solve_activity", activity="exposing",
                    exposure_s=exposure_s)
        try:
            async with self.exposure_guard("rotator sync"):
                frame = await cam.expose(exposure_s, 200, 30, binning=2)
            self.last_frame = frame
            await self._publish_preview(frame)
            tmp = CAPTURE_DIR / "_solve" / "rotsync.fits"
            await asyncio.to_thread(save_fits, frame, tmp, ra_hours=ra_hint,
                                    dec_deg=dec_hint, instrument=cam.name)
            opt = self.effective_optics()
            bus.publish("mount", action="solve_activity", activity="solving")
            result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                        fov_deg_hint=opt["fov_h_deg"] or None)
        finally:
            bus.publish("mount", action="solve_activity", activity=None)
        if not result.success:
            raise DeviceError(f"rotator sync: plate solve failed: {result.message}")
        orientation = _rotation.mod360(result.rotation_deg)
        await rot.sync(orientation)
        mech = await rot.get_mechanical_position()
        bus.log("info",
                f"rotator synced to the sky: PA {orientation:.1f}° at "
                f"mechanical {mech:.1f}° (offset {rot.sync_offset_deg:.1f}°)",
                "rotator")
        bus.publish("rotator", action="synced", pa_deg=orientation,
                    mechanical_deg=mech, offset_deg=rot.sync_offset_deg)
        return {"synced": True, "pa_deg": orientation, "mechanical_deg": mech,
                "offset_deg": rot.sync_offset_deg}

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
        prev_error = None
        trail: list[tuple] = []
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
        # RESUME THE LOOP AFTERWARDS — only when THIS call is what stopped it.
        # A rotate is the one yield-class caller whose frames afterwards are of
        # the SAME field: the tube has not moved, only the camera angle has. So
        # the general "leave it stopped, the field changed" rule in
        # ``yield_camera_for`` does not apply, and the user watching Live View
        # rotate should get their view back rather than a dead panel and a
        # button to re-press. ``stopped_loop`` is False when ``goto_and_center``
        # already yielded on its way in — that run repoints the tube and owns
        # the decision not to resume.
        stopped_loop = await self.yield_camera_for("rotate to PA")
        try:
            return await self._rotate_to_pa_attempts(
                rot, cam, solver, rcfg, target, exposure_s, max_attempts,
                epoch, adjusted_to, moved, error, prev_error, trail,
                orientation)
        finally:
            if stopped_loop:
                with contextlib.suppress(Exception):
                    await self.resume_loop()

    async def _rotate_to_pa_attempts(self, rot, cam, solver, rcfg, target,
                                     exposure_s, max_attempts, epoch,
                                     adjusted_to, moved, error, prev_error,
                                     trail, orientation) -> dict:
        """The solve→move→solve attempts themselves. Split out only so
        ``rotate_to_pa`` can wrap them in the loop-resume ``finally`` above
        without indenting the whole body."""
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
            # EVERY ATTEMPT, NAMED (2026-08-08). On the rig this loop ran all
            # five attempts with the error GROWING (56.8° -> 76.4°) and said
            # only "failed to converge after 5 attempts (last error 76.4°)" —
            # while physically spinning the camera through a full turn and most
            # of another. One line per attempt is what makes a sign or wrap
            # error diagnosable at all: solved PA, where we are trying to get
            # to, and the move being commanded to close it.
            bus.log("info",
                    f"rotator attempt {attempt}/{max_attempts}: solved PA "
                    f"{orientation:.1f}°, target {target:.1f}°, error "
                    f"{error:.1f}°, commanding {distance:+.1f}° to "
                    f"{_rotation.mod360(orientation + distance):.1f}°",
                    "rotator")
            trail.append((attempt, round(orientation, 1), round(target, 1),
                          round(error, 1), round(distance, 1)))
            bus.publish("rotator", action="rotating", attempt=attempt,
                        orientation_deg=round(orientation, 2),
                        target_deg=round(target, 2),
                        error_deg=round(error, 2),
                        commanded_deg=round(distance, 2))
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
            # ABANDON ON THE FIRST ATTEMPT THAT DID NOT HELP. This is a
            # solve→move→solve loop with no damping: if a move does not shrink
            # the error, repeating it cannot either, and each repeat is a real
            # rotation of a real camera. The rig's five attempts moved the
            # camera through more than 360° in total while getting further from
            # the target every time — an operator watched it happen (2026-08-08,
            # slewing to M52) and the rig reported nothing until the end.
            #
            # Compared against the PREVIOUS attempt's error, not the best-ever:
            # the question is whether the move we just made helped.
            if prev_error is not None and error >= prev_error - ROTATE_MIN_GAIN_DEG:
                raise DeviceError(
                    f"rotator is not converging, so it has been stopped after "
                    f"{attempt} attempts rather than turned further: the error "
                    f"went {prev_error:.1f}° -> {error:.1f}° across the last "
                    f"move. Attempts (attempt, solved PA, target, error, "
                    f"commanded): {trail}")
            prev_error = error
            await rot.move_to(_rotation.mod360(orientation + distance))
            moved = True
        last_error = f"(last error {error:.1f}°)" if error is not None else "(no attempts ran)"
        raise DeviceError(
            f"rotator failed to converge after {max_attempts} attempts "
            f"{last_error}. Attempts (attempt, solved PA, target, error, "
            f"commanded): {trail}")

    def note_pointing_verified(self, ok: bool, *, error_arcmin: float | None = None,
                               reason: str = "") -> None:
        """Record whether the tube's position was CONFIRMED against the sky.

        Called by every path that tries to centre. `ok=False` carries the reason
        forward to the UI, because "we pointed where the mount thinks that is"
        and "we solved the field and it agrees" are different claims and the
        operator asked for the second one.
        """
        self._pointing_verified = bool(ok)
        self._pointing_error_arcmin = error_arcmin if ok else None
        if ok:
            self._pointing_reason = (
                f"plate solved to {error_arcmin:.1f}' of target"
                if error_arcmin is not None else "plate solved on target")
        else:
            self._pointing_reason = (
                (reason or "centering failed")
                + " — the mount went to raw GoTo coordinates, so the pointing "
                  "is only as good as its model")
            # GN-07: a verdict of "not verified" must not leave a stale solved
            # centre standing behind it -- the capture-metadata builder only
            # ever trusts `_solved_pointing` while `_pointing_verified` is also
            # True, but clearing both here (rather than relying on that AND)
            # means a caller that later reads `_solved_pointing` directly finds
            # it honestly empty instead of a value nothing vouches for any more.
            self._solved_pointing = None

    def note_pointing_moved(self) -> None:
        """Invalidate a previous verdict. A 'verified' that outlives the pointing
        it described is worse than none — it is a green tick about somewhere the
        tube no longer is.

        Call this from an actual slew/park/sync/unpark/home -- something that
        can move the tube off a solved centre. A capture (even right after a
        dither) is deliberately NOT one of those: a 2-3 px dither does not
        invalidate a solved centre, and GN-07's header fix depends on that
        centre surviving every frame of an untouched imaging run, not just the
        first one."""
        self._pointing_verified = False
        self._pointing_error_arcmin = None
        self._pointing_reason = "the mount has moved since the last plate solve"
        self._solved_pointing = None

    async def goto_and_center(self, ra_hours: float, dec_deg: float,
                              tolerance_deg: float = 0.02,
                              max_attempts: int = 3,
                              solve_exposure_s: float | None = None,
                              rotation_deg: float | None = None) -> dict:
        """Slew, then iterate solve→sync→re-slew until on target.

        ``solve_exposure_s`` None (the default) means "the ``solve`` scope" —
        the same persisted setting the Align screen's dial edits. It was a
        frozen ``3.0`` that GotoStrip rendered read-only and nothing could
        change: a centring solve and a polar solve are the same frame off the
        same camera, and there is no reason for the rig to hold two answers."""
        if solve_exposure_s is None:
            solve_exposure_s = float(frames_payload()["solve"]["exposure_s"])
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
        # The old field's name must not outlive the slew that leaves it (#182).
        # Dropped BEFORE the mount moves, not after it lands: between the two
        # there is a window in which the app would be naming a patch of sky the
        # camera is actively swinging away from, and that is the one moment the
        # user is most likely to be looking at the label.
        self.invalidate_field_solve("the mount is slewing to a new target")
        # The solved centre goes with it (GN-07): a commanded slew is the
        # motion that retires it, and the success branch below records a
        # fresh one from the solve that lands.
        self.note_pointing_moved()
        async with self._motion_lock:
            if not self._motion_committed_clean(epoch):
                bus.log("warning", "goto abandoned: aborted before motion", "mount")
                self.note_pointing_verified(False, reason=str("centering did not converge"))
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
                    self.note_pointing_verified(False, reason=str("centering did not converge"))
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
                    self.note_pointing_verified(False, reason=str("centering did not converge"))
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
                self.note_pointing_verified(False, reason=str("centering did not converge"))
                return {"centered": False, "error_arcmin": None,
                        "attempts": attempt, "solve_failed": True} | _rot_keys
            err = _ang_sep_deg(solved["ra_hours"], solved["dec_deg"], ra_hours, dec_deg)
            bus.log("info", f"centering attempt {attempt}: {err * 60:.1f}' off target", "solve")
            if err <= tolerance_deg:
                bus.publish("mount", action="centered", error_arcmin=err * 60)
                # GN-07: record the SOLVE's own coordinates, not the goto target
                # -- `solved` is what the sky actually measured at, `ra_hours`/
                # `dec_deg` are only where we asked the mount to point. They
                # agree to within `tolerance_deg` here, but the header should
                # carry the measurement, not the request.
                self._solved_pointing = (
                    solved["ra_hours"], solved["dec_deg"], time.time())
                self.note_pointing_verified(True, error_arcmin=err * 60)
                return {"centered": True, "error_arcmin": err * 60, "attempts": attempt} | _rot_keys
            # The correction slew commanded the FULL remaining error; if the
            # pointing barely changed, the slew did not happen. Iterating on an
            # inert mount converges on nothing — and syncs a growing pile of
            # identical solves into it while looking like honest work. Stop and
            # say what is actually wrong (2026-08-06: two bit-identical 33.7'
            # residuals, and this loop's own log was the only witness).
            if (last_err is not None
                    and err > CENTERING_STUCK_MIN_ERR_FACTOR * tolerance_deg
                    and abs(err - last_err) * 60.0 < CENTERING_STUCK_ARCMIN):
                bus.log("warning",
                        f"centering: the correction slew changed nothing "
                        f"({last_err * 60:.1f}' → {err * 60:.1f}' off target) — "
                        f"the mount is not executing slews. Stopping rather "
                        f"than repeating. Check it is unparked, tracking, and "
                        f"clear of its limits; a mount that refuses motion in "
                        f"its current state looks exactly like this.", "solve")
                bus.publish("mount", action="centering_stuck",
                            error_arcmin=err * 60)
                self.note_pointing_verified(False, reason=str("centering did not converge"))
                return {"centered": False, "error_arcmin": err * 60,
                        "attempts": attempt, "did_not_move": True} | _rot_keys
            last_err = err
        self.note_pointing_verified(False, reason=str("centering did not converge"))
        return {"centered": False, "error_arcmin": (last_err or 0) * 60,
                "attempts": max_attempts} | _rot_keys

    async def pier_side_now(self) -> str | None:
        """The mount's pier side as a lower-case string, or ``None``.

        ``None`` means NOBODY CAN SAY — no mount, a dropped link, a driver that
        raised. ``"unknown"`` means the driver answered and the answer was
        "unknown"; both are kept distinct from a real side so two failures to
        read can never be compared and called "unchanged".

        Deliberately the same read, with the same tri-state, that
        ``SequenceEngine._pier_side_now`` makes: ``meridian_flip`` and the
        engine's flip step must not be able to disagree about what the mount
        said, and the hub cannot import the engine to borrow it.
        """
        tel = self.devices.get("telescope")
        if tel is None or not getattr(tel, "connected", False):
            return None
        try:
            side = await asyncio.wait_for(tel.pier_side(),
                                          PIER_SIDE_QUERY_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception:                # noqa: BLE001
            return None
        return getattr(side, "value", None) or None

    async def meridian_flip(self, ra_hours: float, dec_deg: float) -> dict:
        """Flip a German equatorial mount across the meridian: stop guiding,
        re-slew (the mount chooses the far side of the pier), plate-solve
        re-center, and restart guiding.

        THE RE-SLEW DOES NOT ALWAYS FLIP, and everything expensive here used to
        be spent as if it always did. The AM5 picks its pier side from the HOUR
        ANGLE, so the engine's lead-time attempt — issued while the target is
        still east of the meridian — is answered by staying exactly where it is.
        Measured 2026-09-07/08: that no-op re-slew still discarded the guider
        calibration and paid 4.7 minutes to walk a fresh one, on a side whose
        old calibration was still perfectly valid, and the real flip twelve
        minutes later paid for it all again.

        So the side is read either side of the slew. Unchanged AND readable
        means nothing flipped: guiding restarts on the calibration it already
        has (``needs_calibration`` reuses it), and the result says
        ``flipped: False`` so the caller can skip the rest of the post-flip
        programme too. A side that CHANGED — or that could not be read, which
        is not evidence of anything — keeps today's conservative behaviour:
        discard the calibration and recalibrate on the new side (GN-01).
        """
        bus.publish("mount", action="meridian_flip")
        bus.log("info", "meridian flip: stopping guiding and re-slewing", "sequence")
        was_guiding = False
        if self.guider and self.guider.connected:
            try:
                was_guiding = await self.guider.is_active()
                await self.guider.stop_guiding()
            except Exception:
                pass
        side_before = await self.pier_side_now()
        result = await self.goto_and_center(ra_hours, dec_deg)
        side_after = await self.pier_side_now()
        # Both reads have to have SUCCEEDED for "unchanged" to mean anything.
        flipped = not (side_before not in (None, "unknown")
                       and side_after == side_before)
        # Flip the guider's calibration for the far side of the pier BEFORE
        # restarting guiding (review 7d). On a real GEM the RA/Dec sense reverses
        # across the meridian, so guiding with the pre-flip calibration runs
        # BACKWARDS (runaway). Best-effort + guarded: a guider that can't flip
        # (sim) or errors must not abort the flip. The guider method itself logs
        # its own success/failure; this call is guarded only so a missing method
        # or an unexpected raise can never break the flip.
        if flipped and self.guider and self.guider.connected:
            flip_cal = getattr(self.guider, "flip_calibration", None)
            if callable(flip_cal):
                try:
                    await flip_cal()
                except Exception as e:
                    bus.log("warning",
                            f"meridian flip: guider calibration flip failed: {e}",
                            "sequence")
        elif not flipped:
            bus.log("info",
                    f"meridian flip: the mount still reports pier side "
                    f"{side_after} after the re-slew — nothing flipped, so the "
                    f"calibration for this side is kept rather than discarded "
                    f"and re-walked", "sequence")
        if was_guiding:
            try:
                await self.guider.start_guiding()
            except Exception as e:
                bus.log("warning", f"meridian flip: guiding restart failed: {e}", "sequence")
        bus.log("info", "meridian flip complete", "sequence")
        return dict(result or {}, flipped=flipped,
                    pier_side_before=side_before, pier_side_after=side_after)

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

    def _engine_flip_owed(self) -> bool:
        """Is the sequence engine refusing to expose because a flip is owed?

        Read through ``getattr`` and defaulted False: this block is built on
        every status poll, including with no engine, no run and no plan."""
        return bool(getattr(self.engine, "flip_owed", False))

    def _note_pier_side(self, side: str) -> dict:
        """Fold this poll's pier-side reading into the cache, and say what the
        status block should report: ``pier_side``, ``pier_side_source`` and
        ``pier_side_age_s``.

        WHY A CACHE AT ALL. ``:Gm#`` is one round trip on a serial link that
        this rig has twice watched go quiet mid-night, and the read is wrapped
        in a bare except that turns any hiccup into ``"unknown"``. A tube does
        not change sides because a serial read timed out, so reporting
        "unknown" on that evidence tells the operator, the UI's pier tile and
        the flip-owed invariant that a fact nobody disputes has become
        unknowable. The last real answer, with its age attached, is strictly
        more information -- and the age is what lets a reader decide for
        themselves whether to trust it.

        The cache NEVER manufactures a side it was not told: it only repeats a
        reading this mount actually gave, and only for ``PIER_SIDE_STALE_S``.
        Past that the answer really is "none", because a link that has been
        quiet for five minutes may well have had a mount flipped by hand
        underneath it.
        """
        now = time.time()
        if side in ("east", "west"):
            self._pier_side_seen = (side, now)
            return {"pier_side": side, "pier_side_source": "mount",
                    "pier_side_age_s": 0.0}
        seen = getattr(self, "_pier_side_seen", None)
        if seen is not None:
            last, t = seen
            age = now - t
            if age <= PIER_SIDE_STALE_S:
                return {"pier_side": last, "pier_side_source": "cached",
                        "pier_side_age_s": round(age, 1)}
            self._pier_side_seen = None
        return {"pier_side": "unknown", "pier_side_source": "none",
                "pier_side_age_s": None}

    async def _compute_meridian(self, tel, ra_hours: float | None,
                                dec_deg: float | None = None) -> dict:
        """The MeridianInfo block. Prefers the device's own value (NINA); for
        sim/Alpaca derives hours-to-flip from the hour angle HA = LST − RA.

        ``dec_deg`` is what tells this apart from a countdown to nothing: a
        target whose lower culmination clears the horizon never swings its tube
        down toward the pier, so the engine declines the flip (see
        ``schedule.flip_unnecessary_over_pole``). Counting down to a flip that
        will not happen is the same broken promise as any other — the strip has
        to say what the run will actually do."""
        from .catalog.coords import lst_hours
        meridian: dict[str, Any] = {
            "status": "unknown", "hours_to_flip": None,
            "flip_enabled": self._plan_flip_enabled(), "pier_side": "unknown",
            # WHERE THE SIDE CAME FROM, and how old it is. "mount" is a fresh
            # `:Gm#` this poll; "cached" is the last one this mount gave, still
            # inside PIER_SIDE_STALE_S, reported because a serial read that
            # times out once does not move the tube; "none" is nobody can say.
            "pier_side_source": "none", "pier_side_age_s": None,
            # Is the engine refusing to expose because a flip is owed and the
            # mount has not performed it? See `SequenceEngine.flip_owed`.
            "flip_owed": self._engine_flip_owed()}
        try:
            ttf = await tel.time_to_meridian_flip()      # NINA → number; others None
        except Exception:
            ttf = None
        try:
            side = (await tel.pier_side()).value
        except Exception:
            side = "unknown"
        meridian.update(self._note_pier_side(side))
        # AND THE REST OF THIS FUNCTION READS THE RESOLVED SIDE, not the raw
        # one. `_is_gem` below decides `status` and `flip_enabled` from it, so
        # leaving `side` as the unknown a single timed-out serial read produced
        # would flip a GEM to "unknown"/not-a-GEM for one poll and take the
        # flip countdown down with it -- the caching would then be visible in
        # one field and contradicted by two others.
        side = meridian["pier_side"]
        if ttf is None and ra_hours is not None:
            # HA = LST − RA, wrapped to [−12, 12]; a GEM on the east side tracking
            # west flips when the target crosses the meridian (HA crosses 0).
            lst = lst_hours(self.site["longitude"])
            ha = ((lst - ra_hours + 12) % 24) - 12
            ttf = -ha
        from .sequence.schedule import flip_unnecessary_over_pole
        try:
            over_pole = flip_unnecessary_over_pole(dec_deg, self.site["latitude"])
        except Exception:
            over_pole = False
        # SAME RULE AS THE ENGINE, or the strip promises a flip the run will not
        # take (or vice versa). `over_pole` only excuses a flip when the mount is
        # NOT a GEM — see `schedule.flip_can_be_skipped` for why a GEM flips
        # whatever the tube geometry says.
        meridian["flip_enabled"] = (self._is_gem(side)
                                    and self._plan_flip_enabled())
        if not self._is_gem(side):
            meridian["status"] = "n_a_fork" if side != "unknown" else "unknown"
        elif not self._plan_flip_enabled():
            meridian["status"] = "flip_disabled"          # GEM but plan disabled it
            # AND THE COUNTDOWN STILL RIDES ALONG. Switching the flip off does
            # not stop a GEM from reaching its meridian; it means nobody will
            # move the tube when it does, which is precisely when the operator
            # most needs to know how long they have. The number was being
            # withheld from the person who had turned the automation off - the
            # one case where the strip is the only warning. `status` still says
            # `flip_disabled`, so nothing reads it as a promise to flip, and the
            # redaction seam still nulls it for a caller without
            # `view.site_derived` (it is derived from the hour angle, and the
            # hour angle is derived from the longitude - redact.py).
            if ttf is not None:
                meridian["hours_to_flip"] = round(ttf, 4)
        elif over_pole:
            # A GEM, a plan that asks for a flip — and a target that does not
            # need one. Not `flip_disabled`: nothing is switched off and there is
            # no pier risk to warn about, which is exactly why it needs its own
            # word rather than borrowing one that means something else.
            meridian["status"] = "n_a_over_pole"
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
        # The SAME set busy_label collapses into one word, published unreduced.
        #
        # `busy` answers "is the rig doing something long" — enough to suppress a
        # stale-telemetry banner, which is all it was built for. It cannot answer
        # "is THIS button's operation still running", because it maps goto ->
        # "slewing", solve -> "solving", autofocus/focuser/filter_offsets ->
        # "focusing" and four more onto "capturing". A UI control that owns one
        # lane needs the lane.
        #
        # Without it, ~20 controls audited on 2026-08-05 derived their in-flight
        # state from the POST promise instead — and those routes are `_spawn`,
        # which returns {"started": name} the instant the task is CREATED. So
        # "Solving…" flickered for 40ms while ASTAP ground for 30s, and the
        # button sat there looking ready and re-pressable. The truth was already
        # on the wire every 2s; it was just reduced past the point of use.
        # monitor_snapshot has published exactly this list since 2026-07-30 —
        # it simply never rode the status frame every client already receives.
        #
        # Additive: old clients ignore an unknown key.
        out["busy_lanes"] = self.busy_lanes()
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
        # THE DEW LOOP'S OWN VIEW OF ITSELF (D-RIG-3), cached by its own tick -
        # no weather fetch and no device read happen here. TOP LEVEL rather than
        # inside `camera`, because the loop drives camera window heaters AND
        # switch ports, and hanging it off one of the two devices it commands
        # would have hidden the other. ABSENT (not null) until the controller
        # exists and has ticked once: a node full of nulls reads as "the loop
        # ran and found nothing", which is a different thing from "the loop has
        # not run". Read through getattr so hub.py carries no import of the
        # controller and works identically on a build without it.
        try:
            _dew = getattr(self, "dew_controller", None)
            _snap = _dew.snapshot() if _dew is not None else None
            if _snap is not None:
                out["dew"] = _snap
        except Exception:
            pass
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
                    # HOW FAST THIS MOUNT WILL ACTUALLY SLEW (D-RIG-4), deg/s,
                    # or null when the driver cannot say. The manual-move clamp
                    # prefers it over TOUCH_MAX_RATE_DEG_S, and the slew pad
                    # reads it so the fastest button on screen is a rate the
                    # mount really has rather than a guess about somebody
                    # else's gearbox.
                    "max_rate_deg_s": getattr(tel, "max_rate_deg_s", None),
                    # Was this pointing CONFIRMED against the sky, or is it the
                    # mount's own opinion? See `note_pointing_verified`.
                    "pointing": {
                        "verified": self._pointing_verified,
                        "reason": self._pointing_reason,
                        "error_arcmin": self._pointing_error_arcmin,
                    },
                }
            except Exception:
                pass
            # Server-computed meridian (Monitor): NINA returns a real number; for
            # sim/Alpaca the hub derives it from the hour angle so the Monitor's
            # flip countdown populates on every backend (monitor spec §6.1).
            try:
                meridian = await self._compute_meridian(tel, ra, dec)
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
                }
            except Exception:
                pass
            else:
                # TEMPERATURE IS ITS OWN READ. It used to share the try above,
                # so an EAF with an unplugged probe (or any driver that raises
                # rather than returning None) took the POSITION off the status
                # frame with it - the one number the focus screen cannot work
                # without. The key stays present and null on a failure, which
                # is what every client already renders as "cannot say"; only
                # the coupling is gone.
                try:
                    out["focuser"]["temperature"] = await foc.get_temperature()
                except Exception:
                    out["focuser"]["temperature"] = None
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
                # WHAT COMPENSATION WOULD DO AT THE NEXT FRAME BOUNDARY
                # (D-RIG-2), so the Focus screen can show the move before it
                # happens instead of only reporting it afterwards. Its OWN try,
                # like `moving` above and for the same reason: this is the
                # newest thing in the block and must never cost the position
                # readout the operator is actually watching. Read-only - it
                # calls the same pure decision the engine calls and commands
                # nothing.
                try:
                    _eng = self.engine
                    _pos = out["focuser"].get("position")
                    _temp = out["focuser"].get("temperature")
                    _tc = getattr(_eng, "temp_comp_status", None)
                    if callable(_tc):
                        out["focuser"]["temp_comp"] = _tc(
                            _temp, _pos, foc.max_position)
                    else:
                        # No run loaded: the config still has an answer, and a
                        # screen that showed nothing until a plan started would
                        # be hiding the setting from the person configuring it.
                        from .focus.tempcomp import TempCompConfig, status_node
                        _cfg = (getattr(config_store.cfg().focus, "temp_comp",
                                        None) or TempCompConfig())
                        out["focuser"]["temp_comp"] = status_node(
                            _cfg, temperature_c=_temp, position=_pos,
                            focuser_max=foc.max_position)
                except Exception:
                    pass
                # WHAT A SWEEP WOULD ACTUALLY DO, so the Focus screen can print
                # it before the tap. The width is no longer a constant the UI
                # can derive for itself — it is sized from this focuser's
                # measured defocus slope — and a screen that kept deriving its
                # own number would be describing a sweep that does not happen.
                # Its own try: a calibration file is the least important thing
                # in this block and must never cost the position readout.
                try:
                    from .focus.autofocus import resolve_sweep
                    g = resolve_sweep(foc, None, 4)
                    out["focuser"]["sweep"] = {
                        "step": g.step, "steps_each_side": g.steps_each_side,
                        "basis": g.basis, "measured": g.measured}
                except Exception:
                    pass
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
                    # Narrowband flags, parallel to names. The offsets dialog
                    # reads these back so the marking it persisted is visible
                    # and editable where filters are configured, rather than
                    # being a setting only the run that wrote it can see.
                    "narrowband": list(fw.filter_narrowband or []),
                    # Per-filter capture settings, parallel to names, with None
                    # in every unpinned slot. These are the DEFAULTS the camera
                    # dial seeds from when the filter changes and the plan
                    # editor fills a new step with — so they have to be on the
                    # status the client already polls, not behind a second
                    # fetch that a filter change would have to wait for.
                    "exposures": list(fw.filter_exposures or []),
                    "gains": list(fw.filter_gains or []),
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
                    "can_bind": bool(getattr(dome, "can_bind", False)),
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
                _bayer = normalise_bayer(getattr(cam, "bayer_pattern", None))
                # CAN THIS CAMERA RECORD VIDEO AT ALL (D-RIG-1), answered
                # BEFORE the press. Without it the only way to find out is to
                # start a recording and read a 409 `no_video_path` back, which
                # is a control that looks live, costs a round trip, and then
                # explains it was never available.
                #
                # MIRRORED, not imported: the authority is the refusal in
                # `imaging/video_routes.py` (the `no_video_path` branch, which
                # reads `caps.burst_supported or isinstance(cam,
                # NativeCamera)`). Kept in step by hand because the route
                # raises rather than returning a verdict; if that predicate
                # ever earns a name, this should call it.
                #
                # Guarded and fail-CLOSED: an import that fails leaves
                # "none", which disables a control rather than offering one
                # that cannot work.
                _vcaps = None
                _native_cam = False
                try:
                    from .devices.cameras.engine import NativeCamera
                    from .imaging.video import camera_capabilities
                    _vcaps = camera_capabilities(cam)
                    _native_cam = isinstance(cam, NativeCamera)
                except Exception:
                    pass
                _burst = bool(getattr(_vcaps, "burst_supported", False))
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
                    # The gain at which this sensor drops its read noise (high
                    # conversion gain). Additive, null when the backend cannot
                    # say. Published because the offsets dialog derives a
                    # narrowband sweep gain from it and must show the SAME
                    # number the server would use — see
                    # focus/filter_offsets.narrowband_sweep_settings.
                    "hcg_threshold_gain": getattr(cam, "hcg_threshold_gain",
                                                  None),
                    # Additive: MEASURED e-/ADU per gain setting (auto-learn).
                    # Advanced-UI only; the driver value above always wins.
                    "egain_learned": {str(g): v
                                      for g, v in self._egain_learned.items()},
                    # WHAT COLOUR THIS SENSOR IS, published as the four-letter
                    # pattern and as the one boolean most callers actually
                    # want.
                    #
                    # NORMALISED, not raw: the FITS card and the Alpaca/NINA
                    # paths give the full four letters, while the native ZWO
                    # and Player One bindings report only the TOP-LEFT PAIR
                    # ("RG"), and a client comparing against "RGGB" would
                    # decide the same camera was mono on one backend and
                    # colour on another.
                    #
                    # BINNING IS DELIBERATELY NOT CONSIDERED. A sensor keeps
                    # its colour filter array whatever the readout does;
                    # `effective_bayer` answers the DIFFERENT question of
                    # whether a particular frame can still be debayered (a 2x2
                    # bin has already mixed the colours in the pixels that
                    # arrive), and that is a property of the frame, not of the
                    # camera this node describes. Deriving this from the
                    # current bin would make a camera stop being colour when
                    # somebody changed a dropdown.
                    #
                    # None means MONO OR UNREPORTED, and the two are not worth
                    # separating here: the honest consumer behaviour is the
                    # same (do not debayer, do not offer colour controls), and
                    # fail-closed is the right direction - a mono rig wrongly
                    # told it is colour gets three planes of the same grey,
                    # which is the failure that is hard to see.
                    #
                    # It is also the answer for a rig with no filter wheel at
                    # all, which is the case this exists for: a mono camera
                    # with no wheel and an OSC camera both shoot every frame
                    # through "no filter", so the filter name cannot tell them
                    # apart and only the sensor can.
                    "bayer_pattern": _bayer,
                    "is_color": _bayer is not None,
                    # Video capability (D-RIG-1). All four are additive and
                    # inert for a client that does not ask.
                    #
                    # ``roi_align`` is the (x, y) grid a subframe must land on,
                    # (1, 1) when nothing constrains it - the ROI picker rounds
                    # to it so the server does not have to round underneath the
                    # operator and hand back a different rectangle.
                    "roi_align": list(getattr(_vcaps, "roi_align", (1, 1))),
                    "burst_supported": _burst,
                    # frames per second the driver will sustain, null when it
                    # does not say (which is not the same as "slow").
                    "max_fps": getattr(_vcaps, "max_fps", None),
                    "video_path": "native" if (_burst or _native_cam) else "none",
                }
                # Dew-heater LEVEL, in its own try for the same reason the warm
                # block below is outside this one: it is the newest and least
                # supported reading here, and it must never be able to cost the
                # temperature and geometry the Monitor is actually watching.
                #
                # ABSENT means "this camera cannot be asked", and that is not the
                # same as 0. The heater was write-only, so the Capture slider had
                # only its own last write to draw from — 0 in a fresh tab — and
                # with the heater running at 60% it read 0% and dragging it up
                # turned the heater DOWN. Publishing 0 as a fallback would move
                # that same lie server-side, where the client cannot detect it.
                #
                # STRIPPED FOR A NON-HOLDER OF `view.weather`, BUT ONLY WHILE
                # THE DEW LOOP IS DRIVING IT (redact.py::_strip_camera_dew).
                # The argument below is right exactly half the time, and the
                # half it is wrong about was a leak: with the loop FOLLOWING,
                # this register carries the ramp's output, and the ramp is a
                # published function of four config numbers - so inverting it
                # recovers the dew margin to about 0.2 C, which is the whole
                # quantity `view.weather` withholds.
                #
                # With the loop off or paused it stays, and that is the part
                # that was always true: this is a DEVICE READOUT - what a knob
                # is set to, because somebody set it by hand - and it predates
                # the dew loop by a year. It is then 60% whether the dew point
                # is -10 C or 14 C, and withholding it would break a control an
                # operator has always had to hide a number that is not a
                # reading. The `dew` node one level up is what tells the two
                # apart, which is why the rule lives at the redaction seam
                # (where both nodes are in hand) and not here.
                try:
                    getd = getattr(cam, "get_dew_heater", None)
                    dew = await getd() if callable(getd) else None
                    if dew is not None:
                        out["camera"]["dew_heater"] = int(dew)
                except Exception:
                    pass
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
        #
        # OFF THE LOOP (#97). Coalescing bounds how OFTEN this writes, not how
        # LONG a write takes, and the write goes through the private-ACL path
        # on a directory holding the night's images: py-spy caught this stack
        # on the loop thread on 2026-09-19 and the write was measured at 7 s.
        # record() takes its own lock, because this now runs on a worker thread
        # and poll_status is entered both from _status_loop and from
        # /api/status.
        try:
            from .devices import fingerprint as _fp
            _m = out.get("mount") or {}
            _f = out.get("focuser") or {}
            _w = out.get("filterwheel") or {}
            await asyncio.to_thread(
                _fp.record, focuser_position=_f.get("position"),
                filter_slot=_w.get("position"),
                ra_hours=_m.get("ra_hours"), dec_deg=_m.get("dec_deg"),
                parked=_m.get("parked"), tracking=_m.get("tracking"))
            # The worker records a slow write, the loop says it: bus.publish is
            # loop-affine (see fingerprint._slow_write_notice).
            _slow = _fp.take_slow_write_notice()
            if _slow:
                bus.log("warning", _slow, "fingerprint")
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
