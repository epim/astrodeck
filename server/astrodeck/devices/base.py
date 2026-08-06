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

    Vendor-neutral, modeled on ASCOM IDomeV2's shutter/slaved surface but with
    AstroDeck-style string states. ``shutter_state``/``open_shutter``/
    ``close_shutter`` are the required core; ``abort`` (halt motion),
    ``set_slaved``/``get_slaved`` (dome-follows-mount) degrade gracefully for a
    roll-off roof that doesn't slave (``can_slave=False`` → default raise /
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
    #: whether the dome can slave its azimuth to the mount (rotating domes only).
    can_slave: bool = False

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

    async def set_slaved(self, on: bool) -> None:
        raise DeviceError(f"{self.name} cannot slave to the mount")

    async def get_slaved(self) -> bool:
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
            "can_slave": self.can_slave,
            "requires_park_before_close": self.requires_park_before_close,
        }
