"""Device abstraction layer.

Every backend (Alpaca, simulator, future INDI) implements these interfaces.
The rest of AstroDeck only ever talks to these classes — vendor neutrality
lives here.

All methods are async; long hardware operations must be awaitable and
cancellation-safe.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    """A downloaded exposure."""

    data: np.ndarray          # 2D uint16 (mono or bayered)
    exposure_s: float
    gain: int
    offset: int
    binning: int
    bayer_pattern: str | None  # e.g. "RGGB", None for mono
    temperature_c: float | None
    timestamp: float


class Device(ABC):
    """Common lifecycle for all devices."""

    kind: str = "device"

    def __init__(self, name: str):
        self.name = name
        self.connected = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "connected": self.connected}


class Camera(Device):
    kind = "camera"

    #: capability metadata populated on connect
    sensor_width: int = 0
    sensor_height: int = 0
    pixel_size_um: float = 0.0
    max_gain: int = 600
    can_cool: bool = False
    bayer_pattern: str | None = None

    @abstractmethod
    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True) -> CameraFrame:
        """Take one exposure and return the frame. Must be cancellable: on
        asyncio.CancelledError implementations abort the exposure in-camera."""

    @abstractmethod
    async def abort_exposure(self) -> None: ...

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        raise DeviceError(f"{self.name} has no cooler")

    async def get_temperature(self) -> float | None:
        return None


class Telescope(Device):
    kind = "telescope"

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

    async def pier_side(self) -> PierSide:
        return PierSide.UNKNOWN

    async def stop(self) -> None:
        """Emergency stop of any motion."""
        await self.move_axis("ra", 0)
        await self.move_axis("dec", 0)


class Focuser(Device):
    kind = "focuser"

    max_position: int = 100_000
    step_size_um: float | None = None

    @abstractmethod
    async def get_position(self) -> int: ...

    @abstractmethod
    async def move_to(self, position: int) -> None:
        """Absolute move; waits for completion."""

    @abstractmethod
    async def halt(self) -> None: ...

    async def get_temperature(self) -> float | None:
        return None


class FilterWheel(Device):
    kind = "filterwheel"

    filter_names: list[str] = []

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
