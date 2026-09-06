"""Device abstraction layer.

Every backend (Alpaca, simulator, future INDI) implements these interfaces.
The rest of AstroDeck only ever talks to these classes — vendor neutrality
lives here.

All methods are async; long hardware operations must be awaitable and
cancellation-safe.
"""
from __future__ import annotations

import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class DeviceError(RuntimeError):
    """Raised when a device call fails. Message is user-presentable."""


class PierSide(enum.Enum):
    EAST = "east"
    WEST = "west"
    UNKNOWN = "unknown"


@dataclass
class CameraFrame:
    """A downloaded exposure.

    Backends that return raw sensor data (sim, Alpaca) populate ``data`` and
    leave the ``rendered_*`` fields empty — the hub stretches/encodes for
    display. Backends that can only return a pre-rendered image (NINA, which
    serves auto-stretched PNG/JPEG plus computed statistics) populate
    ``rendered_bytes`` + ``rendered_mime`` (used verbatim for the preview) and
    carry NINA's measured ``hfr``/``stars``; ``data`` then holds a decoded
    grayscale copy used only for the histogram and stat readout.
    """

    data: np.ndarray          # 2D uint16 (mono or bayered)
    exposure_s: float
    gain: int
    offset: int
    binning: int
    bayer_pattern: str | None  # e.g. "RGGB", None for mono
    temperature_c: float | None
    timestamp: float
    rendered_bytes: bytes | None = None   # pre-encoded preview (NINA)
    rendered_mime: str = "image/png"
    hfr: float | None = None              # backend-measured HFR, if any
    stars: int | None = None              # backend-measured star count, if any
    saved_path: str | None = None         # path if the backend saved the file
    #: driver-derived saturation ADU so the clip/saturation overlay is honest.
    #: Linear backends (sim, Alpaca) populate this from MaxADU / render
    #: saturation; None leaves the clip mask disabled (never a wrong overlay).
    full_well: int | None = None
    #: whether ``data`` is raw linear sensor data (sim/Alpaca → True) vs a
    #: decoded-from-render 8-bit promotion (NINA → False). The preview gates the
    #: linear histogram + clip mask on this (live-preview spec finding #1).
    data_is_linear: bool = True
    #: e-/ADU at the capture gain, when the backend reports it (Player One
    #: get_egain / ZWO ElecPerADU / sim constant). None -> EGAIN card omitted.
    egain_e_per_adu: float | None = None
    #: Where the focuser was when this frame was exposed, when the backend can
    #: say. Only the SIMULATOR can: it renders star sharpness from the focuser
    #: position, so it knows; a real camera has never heard of the focuser.
    #:
    #: A FRAME'S MEASUREMENT BELONGS TO THE FRAME, not to wherever the focuser
    #: has since moved on to. That distinction did not exist while the sweep
    #: measured each frame before moving again, and six focus tests quietly
    #: relied on it by reading the live rig position inside a metric substitute.
    #: The sweep now exposes the next point WHILE measuring this one
    #: (`focus.pipeline`), so the live position is the NEXT point's, and a
    #: substitute that wants to know which point it is looking at has to read it
    #: off the frame. Production never touches this — the real metric reads
    #: pixels — but a test double that reads live device state is a test double
    #: that can pass while the code under test is wrong.
    focuser_position: int | None = None


class Device(ABC):
    """Common lifecycle for all devices."""

    kind: str = "device"

    #: connection identity — populated by backends that know where the device
    #: lives (Alpaca sets host/port/dev_type/dev_num/backend; the hub sets
    #: ``role`` after construction). These let Profiles replay a connection
    #: intent (host:port + which device) without live handles. Defaults keep
    #: backends that don't carry an address (sim, NINA) describe()-able.
    host: str = ""
    port: int = 0
    dev_type: str = ""
    dev_num: int = 0
    role: str = ""
    backend: str = ""
    #: real hardware? Read by providers to gate fake/sim plate-solves. Default
    #: False (safe); real device classes set True and the orchestrator stamps the
    #: authoritative value from the backend manifest at connect time.
    hardware: bool = False

    def __init__(self, name: str):
        self.name = name
        self.connected = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "dev_type": self.dev_type,
            "dev_num": self.dev_num,
            "role": self.role,
            "backend": self.backend,
        }


class Camera(Device):
    kind = "camera"

    #: capability metadata populated on connect
    sensor_width: int = 0
    sensor_height: int = 0
    pixel_size_um: float = 0.0
    max_gain: int = 600
    #: Largest supported symmetric bin factor (ASCOM MaxBinX). The UI offers
    #: bins 1..max_bin instead of a hardcoded list (UX-27). Default 4 matches the
    #: prior hardcoded ceiling, so a backend that doesn't probe it is unchanged.
    max_bin: int = 4
    can_cool: bool = False
    has_dew_heater: bool = False
    bayer_pattern: str | None = None
    #: Sensor gain in e-/ADU at the camera's current gain setting (0.0 = unknown
    #: / not reported). Populated only by native adapters that expose it via
    #: ``caps.extra["egain"]`` (photometry/SNR design §1.2); Alpaca/NINA/sim
    #: backends leave this at the inert default. Lets the client photometry
    #: profile (ui/src/store.ts PhotometryProfile) auto-prefill gain without a
    #: manual read-noise-harness entry.
    egain: float = 0.0

    @abstractmethod
    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        """Take one exposure and return the frame. Must be cancellable: on
        asyncio.CancelledError implementations abort the exposure in-camera.

        ``save``/``target`` are honored only by backends that save the file
        themselves (NINA writes to the imaging machine). Backends that return
        raw data ignore them; the hub saves their FITS."""

    @abstractmethod
    async def abort_exposure(self) -> None: ...

    #: capability flag (warm-ramp fix, 2026-08-04) — True only for backends that
    #: run their OWN warm-down ramp when told to switch the cooler off (today:
    #: the NINA bridge, whose ``/equipment/camera/warm`` takes a duration and
    #: ramps internally). The hub's ramp then does NOT step the setpoint itself —
    #: two ramps fighting over one setpoint is worse than either — it hands the
    #: duration down and tracks the ETA so the UI can still show progress.
    self_warms: bool = False

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        raise DeviceError(f"{self.name} has no cooler")

    async def get_temperature(self) -> float | None:
        return None

    async def get_ambient_temperature(self) -> float | None:
        """Ambient (heat-sink / air) temperature in °C, or None when this backend
        cannot measure it — which is the common case, hence the inert default.

        The warm-down ramp climbs the setpoint TOWARD ambient; a backend that
        actually knows the number turns "warming to 20 °C (assumed)" into
        "warming to 11 °C (measured)". Nothing depends on it: with None the ramp
        assumes a warm room and ends itself as soon as the sensor stops following
        the setpoint, which IS the real ambient (see astrodeck/cooling.py)."""
        return None

    async def set_dew_heater(self, power: int) -> None:
        """Set the camera's built-in dew heater (0-100%)."""
        raise DeviceError(f"{self.name} has no dew heater")

    async def get_dew_heater(self) -> int | None:
        """The heater's CURRENT level (0-100%), or None when this backend cannot
        be asked — which is most of them, hence the inert default.

        None means UNKNOWN, and the distinction is the whole reason this exists.
        The heater was write-only, so the Capture slider had nothing to draw
        itself from but its own last write, and a fresh tab's last write is 0.
        With the heater running at 60% the slider sat at 0%, and dragging it up
        to "turn it on" turned it DOWN. A backend that cannot read the register
        must return None so the client can say so; returning 0 would put the
        same lie one layer deeper, where the client can no longer see it.

        Must not raise: this is read on the 2 s status poll. Backends whose
        transport can fail (Alpaca, ASIAIR, an SDK register) swallow the failure
        and return None — a momentarily unreadable heater is unknown, not off."""
        return None


#: The one source of truth for the tracking-rate vocabulary (multi-rate mount
#: tracking, 2026-07-21). The wire/API/UI all use these lowercase names --
#: import this tuple everywhere rather than re-hardcoding the list. ASCOM's
#: "King" rate is out of scope (YAGNI).
TRACKING_RATES: tuple[str, ...] = ("sidereal", "lunar", "solar")


class Telescope(Device):
    kind = "telescope"

    #: capability flag (Batch 4b) — set True only by backends that implement
    #: ``destination_pier_side``. Drives whether the UI even offers pier-limit
    #: enforcement (C1-11); default False keeps non-GEM / unsupported mounts
    #: unaffected and the destination helper inert (returns UNKNOWN).
    reports_destination_pier_side: bool = False

    #: capability flag — set True only by backends that have confirmed the
    #: mount accepts ``PulseGuide`` (ASCOM ``CanPulseGuide``, or the sim's
    #: always-on nudge). Gates whether the native guider will even start:
    #: spec §4 says a pulse-guide-incapable mount must refuse to start with
    #: an actionable error rather than silently issuing pulses that go nowhere.
    can_pulse_guide: bool = False

    #: Longest single pulse (ms) this mount will actually perform, or None when
    #: it has no cap — a guider reads it so it never asks for a move the driver
    #: silently truncates (see the AM5's ``_PULSE_MAX_MS``).
    max_pulse_ms: int | None = None

    #: capability flag (multi-rate mount tracking, 2026-07-21) -- set True only
    #: by backends that have confirmed the mount accepts a lunar/solar drive
    #: rate (AM5N, Alpaca, sim). Gates whether the UI even offers the rate
    #: selector; default False keeps unsupported mounts (incl. NINA) unaffected
    #: and the rate control hidden.
    can_set_tracking_rate: bool = False

    #: capability flag (Home control, 2026-07-30) — set True only by backends
    #: that implement ``find_home``. Gates whether the UI offers the control at
    #: all, so a mount without a home sensor never shows a button that cannot
    #: work (house rule: no dead controls).
    can_find_home: bool = False

    async def find_home(self) -> None:
        """Send the mount to its mechanical home and leave it USABLE there.

        Home is not Park. Home is the known reference position you start a
        session from — counterweight down, pointing at the pole — and the mount
        must come back ready to slew. Park is "stop and stay stopped". Some
        mounts reach both with the same wire command (the ZWO AM5's ``:hP#``
        homes AND parks), so a backend whose home implies a park is expected to
        unpark afterwards; ending parked would make the button a trap that looks
        like it worked.

        Default: refuse. A mount that cannot home says so rather than silently
        doing nothing."""
        raise DeviceError(f"{self.name} cannot find home")

    @abstractmethod
    async def get_position(self) -> tuple[float, float]:
        """Return (ra_hours, dec_degrees), JNow."""

    @abstractmethod
    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        """Slew and wait until the mount settles."""

    @abstractmethod
    async def sync(self, ra_hours: float, dec_deg: float) -> None: ...

    @abstractmethod
    async def set_tracking(self, on: bool) -> None: ...

    @abstractmethod
    async def get_tracking(self) -> bool: ...

    @abstractmethod
    async def park(self) -> None: ...

    @abstractmethod
    async def unpark(self) -> None: ...

    @abstractmethod
    async def is_parked(self) -> bool: ...

    @abstractmethod
    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        """Manual motion. axis: 'ra'|'dec'; rate 0 stops."""

    @abstractmethod
    async def is_slewing(self) -> bool: ...

    async def pulse_guide(self, direction: str, ms: int) -> None:
        raise DeviceError(f"{self.name} cannot pulse guide")

    async def set_tracking_rate(self, rate: str) -> None:
        """Set the drive rate (sidereal/lunar/solar) -- NOT an ephemeris
        follow; the mount just spins its motors at a different constant rate.
        Default raises (mirrors ``pulse_guide``); backends that opt in via
        ``can_set_tracking_rate`` override this and validate ``rate`` against
        ``TRACKING_RATES``."""
        raise DeviceError(f"{self.name} cannot set tracking rate")

    async def get_tracking_rate(self) -> str:
        """The currently-set drive rate name. Default ``"sidereal"`` -- every
        mount starts there and backends that don't support switching never
        leave it."""
        return "sidereal"

    async def guide_rates(self) -> tuple[float, float] | None:
        """(ra_deg_per_s, dec_deg_per_s) at 1x guide speed, or None when the
        mount doesn't report them. Calibration uses actual rates when present,
        else falls back to advisories (dossier §17)."""
        return None

    async def pier_side(self) -> PierSide:
        return PierSide.UNKNOWN

    async def destination_pier_side(self, ra_hours: float, dec_deg: float) -> PierSide:
        """Which side of the pier the mount *would* land on after slewing to
        (ra_hours, dec_deg) — the pre-slew pier-collision guard (Batch 4b).

        Default returns UNKNOWN so mounts that don't report ``DestinationSideOfPier``
        (and the capability flag stays False) are never gated. Backends override
        this and set ``reports_destination_pier_side = True`` after a successful probe."""
        return PierSide.UNKNOWN

    async def time_to_meridian_flip(self) -> float | None:
        """Hours until a meridian flip is due (<=0 means flip now), or None if
        the mount doesn't report it / isn't a German equatorial."""
        return None

    async def stop(self) -> None:
        """Emergency stop of any motion."""
        await self.move_axis("ra", 0)
        await self.move_axis("dec", 0)


class Focuser(Device):
    kind = "focuser"

    max_position: int = 100_000
    step_size_um: float | None = None
    #: backends that expose a native autofocus routine set this True and
    #: implement ``async def native_autofocus(self) -> dict``; run_autofocus
    #: then delegates instead of running its own V-curve sweep.
    supports_native_autofocus: bool = False

    @abstractmethod
    async def get_position(self) -> int: ...

    @abstractmethod
    async def move_to(self, position: int) -> None:
        """Absolute move; waits for completion."""

    @abstractmethod
    async def halt(self) -> None: ...

    async def get_temperature(self) -> float | None:
        return None

    #: Can this focuser be told "you are at position N" without moving?
    #: The re-anchoring primitive for open-loop steppers whose count has been
    #: lost; see set_position_reference.
    can_set_position_reference: bool = False

    async def set_position_reference(self, position: int) -> None:
        """DECLARE the current position to be ``position``. Moves nothing.

        A stepper focuser's position is a count with no physical meaning until
        something anchors it. When that count is lost (the EAF resets to 0 when
        it loses power), the only safe repair is for a human to put the drawtube
        somewhere known and say so — driving into a mechanical stop to find one
        is not safe on hardware with no limit switches.
        """
        raise DeviceError(
            f"{self.name}: this focuser cannot have its position reference set")

    async def is_moving(self) -> bool:
        """Is the drawtube actually in motion right now?

        Default False. A backend that cannot answer must not claim motion: the
        UI uses this to tell "your move is under way" apart from "your move was
        silently refused", and a hopeful True turns the second into the first —
        which is the exact failure that cost 2026-07-30/31 (the EAF ignored
        every move above its enforced limit and nothing anywhere said so).
        """
        return False


class Rotator(Device):
    """Camera rotator / angle adjuster (e.g. ZWO CAA).

    Driver I/O is MECHANICAL-space only; the sky↔mechanical sync offset lives
    HERE, client-side (offset = mechanical − sky), exactly like NINA's VM
    layer (parity §11.2). Never call a driver's own Sync — this behaves
    identically across Alpaca IRotatorV2/V3, the NINA bridge, and sim.
    An unsynced rotator is not an error: offset 0 means sky == mechanical.
    """

    kind = "rotator"
    can_reverse: bool = False
    sync_offset_deg: float = 0.0     # mechanical − sky; set by sync()
    synced: bool = False
    MOVE_TIMEOUT_S: float = 180.0    # a full CAA revolution is minutes-slow

    @abstractmethod
    async def get_mechanical_position(self) -> float: ...  # deg [0, 360)

    @abstractmethod
    async def move_mechanical(self, mech_deg: float) -> None: ...
    # absolute mechanical move; waits for completion; halts on CancelledError

    @abstractmethod
    async def halt(self) -> None: ...

    async def is_moving(self) -> bool:
        return False

    async def get_reverse(self) -> bool:
        return False

    async def set_reverse(self, value: bool) -> None:
        raise DeviceError("this rotator does not support reverse")

    # ---- sky-space layer (shared by every backend) ----
    async def get_position(self) -> float:
        """Sync-adjusted sky position angle, deg [0, 360)."""
        from ..rotation import mod360
        return mod360(await self.get_mechanical_position() - self.sync_offset_deg)

    async def sync(self, sky_deg: float) -> None:
        """Declare that the CURRENT mechanical position is this sky PA."""
        from ..rotation import mod360
        self.sync_offset_deg = mod360(
            await self.get_mechanical_position() - sky_deg)
        self.synced = True

    async def move_to(self, sky_deg: float) -> None:
        """Absolute sky-PA move through the current offset."""
        from ..rotation import mod360
        await self.move_mechanical(mod360(sky_deg + self.sync_offset_deg))


class FilterWheel(Device):
    kind = "filterwheel"

    filter_names: list[str] = []
    #: per-filter focuser offsets (steps), parallel to filter_names; empty = none
    filter_offsets: list[int] = []
    #: per-slot "this slot is opaque" flags, parallel to filter_names; empty = none.
    #: A blackout/dark slot carries no glass at all — it blocks the light path so
    #: darks and bias can be shot without capping the scope. Marked by the user
    #: (no wheel reports it) and persisted per profile alongside names/offsets.
    #: A list rather than a single index: nothing about a carousel says there is
    #: at most one, and this reuses every loop that already walks the parallel
    #: name/offset arrays.
    filter_opaque: list[bool] = []
    #: per-slot "this slot is narrowband" flags, parallel to filter_names.
    #: Like the blackout flags: nothing on the wire reports it, the user marks
    #: it, and it is persisted per profile — a wheel's filters do not change
    #: often, and re-ticking three boxes every time you learn offsets is a
    #: setting pretending to be a question. What it changes is the EXPOSURE and
    #: GAIN a focus sweep uses on that slot: a 3-7 nm passband delivers a star
    #: 40-100x fainter than luminance does, and the 2026-08-08 offsets run
    #: measured L/R/G/B and could not focus S, Ha or Oiii at the one setting it
    #: had. See focus/filter_offsets.narrowband_sweep_settings.
    filter_narrowband: list[bool] = []
    #: Per-slot capture settings, parallel to filter_names. ``None`` in a slot
    #: means "not pinned" — NOT zero, which is a real gain and would be a real
    #: (if useless) exposure. See ``config._opt_num``.
    #:
    #: WHAT THESE ARE AND ARE NOT. They are DEFAULTS, consumed where a value is
    #: being CHOSEN: the camera dial seeds from them when the filter changes,
    #: and a new plan step created with this filter is filled in from them. They
    #: are NOT applied at capture time, and the sequence engine never reads
    #: them. A plan is a reviewable artifact; rewriting its exposures underneath
    #: the operator would make the plan on screen stop describing the night,
    #: which is the same invisible-wrong-config shape as the profile provider
    #: override that ran a simulated polar aligner for twelve days.
    #:
    #: The ONE authoritative consumer is a focus sweep (``focus/filter_offsets``)
    #: — there is no plan there to look at, and a sweep at the wrong exposure is
    #: how the 2026-08-08 offsets run measured L/R/G/B and failed on S/Ha/Oiii.
    filter_exposures: list[float | None] = []
    filter_gains: list[int | None] = []

    def slot_capture_settings(self, slot: int) -> tuple[float | None, int | None]:
        """``(exposure_s, gain)`` pinned for ``slot``, either half possibly None.

        Out-of-range is ``(None, None)`` rather than an error, matching
        ``is_opaque``: a wheel that reports fewer slots than the store holds is
        a reconnect artifact, not a caller mistake."""
        def pick(seq, i):
            return seq[i] if 0 <= i < len(seq or []) else None
        return pick(self.filter_exposures, slot), pick(self.filter_gains, slot)

    def dark_slot(self) -> int | None:
        """The slot to shoot darks/bias through — the first opaque one, or None
        when the wheel has no blackout slot (then darks shoot through whatever
        filter is loaded, which is what every wheel did before this existed)."""
        for i, op in enumerate(self.filter_opaque or []):
            if op:
                return i
        return None

    def is_opaque(self, slot: int) -> bool:
        """True iff ``slot`` blocks the light path. Out-of-range is False, not an
        error: callers ask about slots that may predate an opaque list."""
        flags = self.filter_opaque or []
        return 0 <= slot < len(flags) and bool(flags[slot])

    def is_narrowband(self, slot: int) -> bool:
        """True iff ``slot`` is marked narrowband. Out-of-range is False for the
        same reason ``is_opaque`` says so: every wheel predates the flag."""
        flags = self.filter_narrowband or []
        return 0 <= slot < len(flags) and bool(flags[slot])

    @abstractmethod
    async def get_position(self) -> int: ...

    @abstractmethod
    async def set_position(self, slot: int) -> None:
        """Move to slot (0-based); waits for completion."""

    async def is_moving(self) -> bool:
        """Is the carousel actually turning right now?

        Default False, and for exactly the reason ``Focuser.is_moving`` defaults
        False: the Capture screen pulses the slot you asked for until this goes
        quiet, so a backend that returns a hopeful True it cannot substantiate
        would make a wheel that ignored the command look identical to one that
        obeyed it. That is the same confusion that cost 2026-07-30/31 on the
        focuser, transplanted onto the filter wheel.

        Saying False when you cannot tell is safe here because the position
        readout is the second witness: the UI only trusts the flag while the
        wheel has not yet reached the requested slot, and a wheel that never
        arrives and never claims motion is reported as stuck rather than as
        still turning.
        """
        return False


@dataclass
class SwitchPort:
    id: int
    name: str
    can_write: bool
    is_boolean: bool
    value: float
    min: float = 0.0
    max: float = 1.0
    unit: str = ""


class Switch(Device):
    """Power boxes (Pegasus UPB, ...), dew heaters, anything switchable."""

    kind = "switch"

    @abstractmethod
    async def get_ports(self) -> list[SwitchPort]: ...

    @abstractmethod
    async def set_port(self, port_id: int, value: float) -> None: ...


@dataclass
class SafetyReading:
    """A single observation-safety verdict (Batch 4b).

    ``stale`` is set when the read timed out or the device disconnected: the
    engine treats a stale reading as UNSAFE (fail-closed), never as safe (C1-12,
    C1-15). ``detail`` carries optional backend specifics (cloud %, wind, etc.)."""

    is_safe: bool
    reason: str = ""              # human string when unsafe, e.g. "cloud sensor"
    source: str = ""             # device name
    detail: dict[str, Any] = field(default_factory=dict)
    stale: bool = False          # set when the read timed out / device disconnected
    ts: float = field(default_factory=time.time)


class SafetyMonitor(Device):
    """Observing-condition monitor (cloud/rain/wind sensor, roof switch, ...).

    A backend implements ``is_safe()``; the default ``reading()`` wraps it into a
    ``SafetyReading``. The engine polls ``reading()`` on its own cadence/task so a
    slow sensor never blocks the status loop (C1-12)."""

    kind = "safety"

    @abstractmethod
    async def is_safe(self) -> bool:
        """True when conditions are safe to keep imaging."""

    async def reading(self) -> SafetyReading:
        safe = await self.is_safe()
        return SafetyReading(
            is_safe=safe,
            source=self.name,
            reason="" if safe else "unsafe condition reported",
        )


class CoverState(enum.Enum):
    """Motorized-cover state (PRO-5 / F-F). AstroDeck-style string states, like
    ``PierSide`` — a flat panel without a cover reports ``NOT_PRESENT``."""

    NOT_PRESENT = "not_present"
    CLOSED = "closed"
    MOVING = "moving"
    OPEN = "open"
    UNKNOWN = "unknown"
    ERROR = "error"


class CoverCalibrator(Device):
    """Flat-field light panel (+ optional motorized cover) — the F-F role.

    Vendor-neutral, modeled on ASCOM ICoverCalibratorV1 but with AstroDeck-style
    string states. ``get_brightness``/``calibrator_on``/``calibrator_off`` are the
    required core (a panel is at minimum an on/off light); ``get_calibrator_state``
    defaults to a level-derived string, and every cover method degrades gracefully
    for a panel with no cover (``has_cover=False`` → default raise / NOT_PRESENT),
    exactly the ``Focuser.get_temperature`` / ``Rotator.set_reverse`` idiom."""

    kind = "covercalibrator"

    #: the panel reports its own peak level; an on/off-only panel = 1.
    max_brightness: int = 1
    #: not every flat panel has a motorized cover.
    has_cover: bool = False

    @abstractmethod
    async def get_brightness(self) -> int: ...

    @abstractmethod
    async def calibrator_on(self, brightness: int) -> None:
        """Turn the panel on at ``brightness`` (0..max_brightness)."""

    @abstractmethod
    async def calibrator_off(self) -> None:
        """Turn the panel off."""

    async def get_calibrator_state(self) -> str:
        """One of "off" | "ready" | "not_present" | "unknown" | "error"."""
        return "off"

    async def get_cover_state(self) -> CoverState:
        return CoverState.NOT_PRESENT

    async def open_cover(self) -> None:
        raise DeviceError(f"{self.name} has no cover")

    async def close_cover(self) -> None:
        raise DeviceError(f"{self.name} has no cover")

    def describe(self) -> dict[str, Any]:
        # static capabilities only (sync — no await); live state comes from the
        # hub's ``calibrator_status`` accessor.
        return {
            **super().describe(),
            "max_brightness": self.max_brightness,
            "has_cover": self.has_cover,
        }


class DomeShutterState(enum.Enum):
    """Roll-off-roof / dome shutter state (PRO-4). AstroDeck-style string states,
    like ``PierSide``/``CoverState`` — an unknown/unreachable shutter reports
    ``UNKNOWN``; a shutter that has faulted (e.g. the sim's collision guard)
    reports ``ERROR``."""

    OPEN = "open"
    CLOSED = "closed"
    OPENING = "opening"
    CLOSING = "closing"
    UNKNOWN = "unknown"
    ERROR = "error"


class Dome(Device):
    """A roll-off roof / dome — the observatory-close role (PRO-4).

    Vendor-neutral, modeled on ASCOM IDomeV2's shutter/slaved surface (their
    word, kept only where it names their wire) but with
    AstroDeck-style string states. ``shutter_state``/``open_shutter``/
    ``close_shutter`` are the required core; ``abort`` (halt motion),
    ``set_bound``/``get_bound`` (dome-follows-mount) degrade gracefully for a
    roll-off roof that cannot bind (``can_bind=False`` → default raise /
    False), exactly the ``Focuser.get_temperature`` / ``Rotator.set_reverse``
    idiom. ``is_open``/``is_closed`` derive off ``shutter_state``.

    ``requires_park_before_close`` is the safety-critical capability flag: a
    roll-off roof whose travel passes THROUGH the mount's volume MUST have the
    mount parked clear before the roof may close. True is the FAIL-SAFE default
    (assume a collision is possible); a classic rotating dome whose shutter
    clears the OTA at any orientation may set it False. The close-ordering guard
    (``sequence/roof.close_observatory``) reads this to decide whether to require
    a confirmed-parked mount before moving the shutter."""

    kind = "dome"

    #: A roll-off roof whose travel passes THROUGH the mount's volume: the mount
    #: MUST be parked clear before the roof may close. True is the FAIL-SAFE
    #: default (assume a collision is possible). A classic rotating dome whose
    #: shutter clears the OTA at any orientation may set this False.
    requires_park_before_close: bool = True
    #: whether the dome can BIND its azimuth to the mount (rotating domes only).
    #: Named `bind`, not `slave`: the 2026-08-14 do-not list covers code
    #: identifiers and API fields, not only labels. The ASCOM/Alpaca WIRE
    #: words stay as the vendor spells them - see alpaca.py.
    can_bind: bool = False

    @abstractmethod
    async def shutter_state(self) -> DomeShutterState: ...

    @abstractmethod
    async def open_shutter(self) -> None:
        """Open the shutter/roof. Waits until motion is commanded (not necessarily
        complete); ``shutter_state`` reports progress."""

    @abstractmethod
    async def close_shutter(self) -> None:
        """Close the shutter/roof. SAFETY: the caller
        (``sequence/roof.close_observatory``) must have confirmed the mount parked
        first when ``requires_park_before_close`` — this method does NOT itself
        park."""

    async def abort(self) -> None:
        """Halt any in-progress shutter motion. Default no-op."""
        return None

    async def set_bound(self, on: bool) -> None:
        raise DeviceError(f"{self.name} cannot bind to the mount")

    async def get_bound(self) -> bool:
        return False

    async def is_closed(self) -> bool:
        return (await self.shutter_state()) is DomeShutterState.CLOSED

    async def is_open(self) -> bool:
        return (await self.shutter_state()) is DomeShutterState.OPEN

    def describe(self) -> dict[str, Any]:
        # static capabilities only (sync — no await); live shutter state comes
        # from the ``/api/dome/state`` accessor.
        return {
            **super().describe(),
            "can_bind": self.can_bind,
            "requires_park_before_close": self.requires_park_before_close,
        }


# ---------------------------------------------------------------------------
# Flows equipment: DOME CONTROL and FLAT PANEL
# (design_handoff_astrodeck_flows, README §"Node vocabulary", backend item 6)
#
# RECONCILED, NOT DUPLICATED. Item 6 asks for "new device roles Dome, FlatPanel";
# both are already above — ``Dome`` (PRO-4) and ``CoverCalibrator`` (PRO-5), each
# with an Alpaca client and a sim. Minting a second pair would give the hub two
# keys for one piece of hardware, and the night where the calibration queue holds
# one panel handle while the dusk-flats stage holds the other is not a night
# anyone would enjoy debugging. So ``FlatPanel`` is an alias, and what is
# genuinely new lives here: the two nodes' PARAMETERS, as values the server can
# act on. The roles could always open a shutter and light a panel; nothing could
# say "confirm it opened within 120 s" or "this panel is not in the light path".
#
# These are policy VALUES over the vendor-neutral roles, so they work unchanged
# over Alpaca (``AlpacaDome`` / ``AlpacaCoverCalibrator``) and the simulator —
# there is no transport-specific work left to do for either node.
# ---------------------------------------------------------------------------

#: The FLAT PANEL node's role. Not a subclass and not a second ABC: a flat panel
#: with an optional motorized cover IS ``CoverCalibrator``. The alias exists so
#: the Flows vocabulary can name the thing it wires without a parallel class to
#: keep in step. Backends: ``sim.SimCoverCalibrator``,
#: ``alpaca.AlpacaCoverCalibrator``, and the hub key stays ``"covercalibrator"``.
FlatPanel = CoverCalibrator

#: DOME CONTROL's "Shutter timeout" default, seconds (prototype ``DEFS.dome``).
DEFAULT_SHUTTER_TIMEOUT_S: float = 120.0
#: How often ``DomePolicy.open_and_confirm`` re-reads a travelling shutter. Read
#: through the module global on every wait so a test can shorten it.
SHUTTER_POLL_S: float = 0.5


class FlatPanelPlacement(enum.Enum):
    """Where the panel sits relative to the light path — the FLAT PANEL node's
    ``position`` field ("Dust-cover panel" / "Dome-mounted" / "Handheld").

    Not cosmetic: it is the whole of what "panel ready" MEANS. A dust-cover panel
    illuminates the aperture only with the cover SHUT; a dome-mounted one only
    with the cover OPEN and the mount aimed at it; a handheld one only while a
    person is holding it there. Three different questions over one identical
    ICoverCalibratorV1 surface, and the device cannot tell them apart on its own.
    """

    DUST_COVER = "dust_cover"
    DOME_MOUNTED = "dome_mounted"
    HANDHELD = "handheld"

    @classmethod
    def parse(cls, text: Any) -> "FlatPanelPlacement":
        """Read the node's ``position`` string.

        Anything unrecognised — a graph saved by another build, a hand-edited
        flow — becomes ``DUST_COVER``, which is the STRICTEST of the three: it is
        the only placement that refuses to call the panel ready while the cover
        is open. An unknown placement must not resolve to the reading that lets
        the queue photograph the open sky and file the result as a flat.
        """
        t = str(text or "").strip().lower()
        if "dome" in t:
            return cls.DOME_MOUNTED
        if "hand" in t:
            return cls.HANDHELD
        return cls.DUST_COVER


@dataclass(frozen=True)
class FlatPanelReadiness:
    """Whether flats can be shot through the panel right now.

    ``reason`` carries the sentence an operator reads when ``ready`` is False.
    ``unverifiable`` is the other half of honesty: it names the part of "ready"
    that no sensor on this device reports (a person holding a panel, a mount
    aimed at a dome wall), so a ready-but-blind verdict is never mistaken for a
    measured one. Both empty on a fully-confirmed panel."""

    ready: bool
    reason: str = ""
    unverifiable: str = ""


@dataclass(frozen=True)
class FlatPanelPolicy:
    """The FLAT PANEL node's three parameters, as something the server can act on.

    ``solve_per_filter`` is the node's "Brightness" select: solve the panel level
    against ``adu_target`` for EVERY filter, versus hold one fixed level. Ha and
    L are two orders of magnitude apart in throughput, so a fixed level that
    lands mid-well on L clips on nothing and barely registers on Ha; the default
    is to solve, and the flag exists for panels too coarse to be solved.
    """

    placement: FlatPanelPlacement = FlatPanelPlacement.DUST_COVER
    adu_target: int = 28500
    solve_per_filter: bool = True

    @classmethod
    def from_node_params(cls, params: dict) -> "FlatPanelPolicy":
        """Build from a FLAT PANEL node's ``params`` dict.

        A garbled or missing ADU target falls back to the default rather than to
        zero. Zero is a number the solver would happily chase: it would drive the
        panel to black, converge, and hand back a stack of flats that divide real
        frames by noise."""
        try:
            adu = int(float(params.get("adu")))
        except (TypeError, ValueError):
            adu = 28500
        if adu <= 0:
            adu = 28500
        # The select's two options are "Solve per filter" and "Fixed"; only the
        # explicit "fixed" turns solving off, so an unrecognised value keeps the
        # measuring behaviour rather than silently freezing the brightness.
        solve = "fixed" not in str(params.get("solve") or "").strip().lower()
        return cls(
            placement=FlatPanelPlacement.parse(params.get("position")),
            adu_target=adu,
            solve_per_filter=solve,
        )

    async def readiness(self, panel: CoverCalibrator | None) -> FlatPanelReadiness:
        """Can the calibration queue shoot flats through ``panel`` right now?

        This is the node's "panel ready" event, COMPUTED rather than assumed. No
        panel at all is a legitimate answer — the queue's ``panel`` input is
        optional and a queue without one simply skips flats (doctor rule 7) — so
        every failure here is a skip rather than an error. It has to be a loud
        skip, though: a flat taken with the light off or the path blocked is not
        a poor flat, it is a WRONG one, and it will keep dividing real data by
        garbage for as long as the library holds it.
        """
        if panel is None or not getattr(panel, "connected", False):
            return FlatPanelReadiness(
                False, "no flat panel connected — flats will be skipped")
        state = await panel.get_calibrator_state()
        if state != "ready":
            return FlatPanelReadiness(
                False, f"flat panel reports '{state}', not lit")
        cover = await panel.get_cover_state()
        if cover in (CoverState.MOVING, CoverState.UNKNOWN, CoverState.ERROR):
            # Mid-travel or unreadable is NOT ready. The queue can ask again in a
            # second; a frame taken through a half-open cover cannot be un-taken.
            return FlatPanelReadiness(
                False, f"flat panel cover is {cover.value} — not settled")

        if self.placement is FlatPanelPlacement.DUST_COVER:
            if cover is CoverState.CLOSED:
                return FlatPanelReadiness(True)
            if cover is CoverState.NOT_PRESENT:
                # A translucent flat CAP is a dust-cover panel with no motor —
                # the DUSK FLATS node's default method. It is genuinely ready,
                # and genuinely unwitnessed.
                return FlatPanelReadiness(
                    True, unverifiable="nothing motorised holds this panel over "
                    "the aperture — someone has to have capped the scope")
            return FlatPanelReadiness(
                False, "dust-cover panel is lit but the cover is open — a flat "
                       "taken now is a picture of the sky")

        # Dome-mounted and handheld panels both sit OUTSIDE the tube, so a shut
        # cover is a lid between them and the sensor.
        if cover is CoverState.CLOSED:
            return FlatPanelReadiness(
                False, "the cover is shut between the scope and the panel")
        if self.placement is FlatPanelPlacement.DOME_MOUNTED:
            return FlatPanelReadiness(
                True, unverifiable="the mount has to be aimed at the dome panel "
                "— nothing here can see where it is pointed")
        return FlatPanelReadiness(
            True, unverifiable="someone has to be holding the panel over the "
            "aperture — no sensor reports that")


@dataclass(frozen=True)
class DomePolicy:
    """The DOME CONTROL node's parameters, as something the server can act on.

    THERE IS NO on-unsafe FIELD, and that is the design. The README calls the
    behaviour non-negotiable ("dome closes on unsafe regardless of flow state"),
    and the prototype's "On unsafe" select accordingly offers exactly one option.
    A field would be a place for a "don't close" to arrive from — an older
    client, a hand-edited plan JSON, a config merge — and the roof would still be
    open in the rain, having been talked out of shutting by a value it should
    never have accepted. ``ON_UNSAFE`` is a constant; ``from_plan`` reads plans
    that carry the key and ignores what it says.

    Closing itself is NOT done here: ``sequence/roof.close_observatory`` owns it,
    because a roll-off roof must never travel through an unparked mount and that
    ordering has one implementation. This type owns the OPEN half (which had
    none) and the binding decision.
    """

    bind_to_mount: bool = True
    shutter_timeout_s: float = DEFAULT_SHUTTER_TIMEOUT_S

    #: Not a field (deliberately un-annotated): what an unsafe — or STALE —
    #: safety reading does to the shutter, whatever the flow is doing.
    ON_UNSAFE = "close"

    # TODO(flows-handoff): the engine half of this invariant is backend item 7.
    # When it lands, the unsafe path must route EVERY connected dome through
    # ``sequence/roof.close_observatory``, whether or not the running flow
    # contains a DOME CONTROL node — a dome that is absent from the graph still
    # gets rained on, and doctor rule 9 only warns about the graph.

    @classmethod
    def from_node_params(cls, params: dict) -> "DomePolicy":
        """Build from a DOME CONTROL node's ``params`` dict.

        A missing, zero, negative or garbled timeout becomes the 120 s default
        rather than being honoured. Zero would make ``open_and_confirm`` give up
        before the roof could possibly have moved — turning the one promise this
        stage makes ("the sky is above you") into a coin toss on every run.
        """
        try:
            timeout = float(params.get("timeout"))
        except (TypeError, ValueError):
            timeout = DEFAULT_SHUTTER_TIMEOUT_S
        if timeout <= 0:
            timeout = DEFAULT_SHUTTER_TIMEOUT_S
        # "Azimuth" has two options, "Bind to mount" and "Manual". Only an
        # explicit Manual gives up binding: an unrecognised value keeps the
        # node's own default, and an unbound dome vignettes the night rather
        # than endangering anything.
        #
        # BOTH KEYS ARE READ. The param was called `slave` until 2026-08-14 and
        # every flow saved before then still carries that name on disk. Reading
        # only the new one would silently re-bind a dome the operator had set to
        # Manual - a saved graph must not change meaning because a word did.
        azimuth = params.get("bind", params.get("slave"))
        manual = str(azimuth or "").strip().lower().startswith("manual")
        return cls(bind_to_mount=not manual, shutter_timeout_s=timeout)

    @classmethod
    def from_plan(cls, block: dict) -> "DomePolicy":
        """Build from a compiled plan's ``automation["dome"]`` block.

        Any ``on_unsafe`` the block carries is READ AND DISCARDED — see the class
        docstring. The plan is data that arrives from disk and from clients; it
        does not get a vote on whether the roof shuts in the rain."""
        try:
            timeout = float(block.get("shutter_timeout_s"))
        except (TypeError, ValueError):
            timeout = DEFAULT_SHUTTER_TIMEOUT_S
        if timeout <= 0:
            timeout = DEFAULT_SHUTTER_TIMEOUT_S
        # Same back-compat as `from_node_params`: a plan compiled before
        # 2026-08-14 spells the key `slave`.
        bind = block.get("bind", block.get("slave", True))
        return cls(bind_to_mount=bool(bind),
                   shutter_timeout_s=timeout)

    def to_plan(self) -> dict:
        """The ``automation["dome"]`` block for a compiled plan."""
        return {"bind": self.bind_to_mount,
                "on_unsafe": self.ON_UNSAFE,
                "shutter_timeout_s": self.shutter_timeout_s}

    async def open_and_confirm(self, dome: Dome) -> None:
        """Open the shutter and do not return until it is CONFIRMED open.

        ``Dome.open_shutter`` is fire-and-forget by contract — it waits until the
        motion is commanded, not until it finishes — and the DOME CONTROL node's
        only flow output is "shutter open", which the run cursor leaves through
        on its way to SLEW + CENTER. Without a confirmation the cursor departs
        the moment the driver ACCEPTS the command, and a stalled motor produces a
        night of black frames with nothing anywhere saying the roof never moved.

        Already-open is a no-op: the stage may be re-entered, and re-commanding a
        shutter sends some drivers through another full travel cycle.

        Raises ``DeviceError`` on timeout or a faulted shutter. Raising is the
        fail-closed answer — every stage after this one assumes sky, so an
        unconfirmed roof has to stop the flow rather than be assumed open.
        """
        import asyncio

        if (await dome.shutter_state()) is DomeShutterState.OPEN:
            return
        await dome.open_shutter()
        deadline = time.monotonic() + self.shutter_timeout_s
        while True:
            state = await dome.shutter_state()
            if state is DomeShutterState.OPEN:
                return
            if state is DomeShutterState.ERROR:
                # Fail fast rather than spend the whole timeout on a shutter that
                # has already reported it cannot do this. A driver that reports a
                # stale fault for a moment after the command costs one refused
                # stage; assuming the fault is stale costs the night.
                raise DeviceError(
                    f"{dome.name}: shutter faulted while opening — the roof did "
                    f"not open")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeviceError(
                    f"{dome.name}: shutter did not confirm open within "
                    f"{self.shutter_timeout_s:g}s (last state: {state.value}) — "
                    f"refusing to image under a roof that may be shut")
            await asyncio.sleep(min(SHUTTER_POLL_S, remaining))

    async def apply_binding(self, dome: Dome) -> str:
        """Put the dome's azimuth under the mount, and return the log line.

        A dome that CANNOT bind is not an error. A roll-off roof has no azimuth
        to bind and reports ``can_bind = False`` truthfully — including the
        simulator's, which is the rig the whole Flows surface has to demo on. If
        the node's DEFAULT parameter refused to run against the default sim dome,
        every example flow with a dome in it would fail on a machine with no
        hardware. So: honest-disabled, and the returned line says the setting had
        no effect rather than pretending it took.

        A dome that CLAIMS ``can_bind`` and then refuses the write is a
        different animal, and its ``DeviceError`` propagates — the alternative is
        an OTA that spends the night photographing the inside of the dome wall
        while every status readout says bound.
        """
        if not self.bind_to_mount:
            return "dome azimuth left in manual"
        if not getattr(dome, "can_bind", False):
            return (f"{dome.name} has no bindable azimuth (roll-off roof) - "
                    f"'Slave to mount' has no effect")
        await dome.set_bound(True)
        return "dome azimuth bound to the mount"
