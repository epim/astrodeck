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
    can_cool: bool = False
    has_dew_heater: bool = False
    bayer_pattern: str | None = None

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

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        raise DeviceError(f"{self.name} has no cooler")

    async def get_temperature(self) -> float | None:
        return None

    async def set_dew_heater(self, power: int) -> None:
        """Set the camera's built-in dew heater (0-100%)."""
        raise DeviceError(f"{self.name} has no dew heater")


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

    @abstractmethod
    async def get_position(self) -> int: ...

    @abstractmethod
    async def set_position(self, slot: int) -> None:
        """Move to slot (0-based); waits for completion."""


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
