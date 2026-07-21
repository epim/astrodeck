"""The camera adapter waist: the narrow, stable interface a brand implements,
plus the additive capability descriptor that keeps brand features from being
flattened to a lowest-common-denominator. No SDK, no asyncio, no CameraFrame —
the engine (engine.py) owns all of that and merely drives these hooks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping

from ..base import DeviceError


@dataclass(frozen=True)
class ROI:
    """A subframe + binning request in unbinned sensor pixels."""
    x: int
    y: int
    w: int
    h: int
    bin: int


@dataclass(frozen=True)
class CameraCapabilities:
    """What a camera can do, DECLARED (never assumed). Consumers render what is
    present; unsupported features are simply absent, not clamped away.

    ``read_modes`` generalizes discrete sampling modes (Player One LRN/Normal,
    QHY ReadMode, ...); () means none. ``hcg_threshold_gain`` is informational
    (the gain at/above which conversion gain lowers read noise); None when N/A.
    ``extra`` carries typed brand-unique data without polluting the core."""
    sensor_width: int
    sensor_height: int
    pixel_size_um: float
    bit_depth: int
    bayer_pattern: str | None
    gain_range: tuple[int, int]
    offset_range: tuple[int, int]
    bin_modes: tuple[int, ...]
    roi_supported: bool
    has_cooler: bool
    has_dew_heater: bool
    max_adu: int
    read_modes: tuple[str, ...] = ()
    hcg_threshold_gain: int | None = None
    extra: Mapping[str, object] = field(default_factory=dict)


class CameraAdapter(ABC):
    """One brand's camera, reduced to primitive synchronous hooks. Required
    hooks are the irreducible minimum; optional hooks default to raising
    DeviceError (or returning None) so a brand implements ONLY what it has —
    exactly as Camera.set_cooler raises today."""

    # --- required ---------------------------------------------------------
    @abstractmethod
    def capabilities(self) -> CameraCapabilities: ...
    @abstractmethod
    def open(self, index: int) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def start_exposure(self, *, seconds: float, gain: int, offset: int,
                       roi: ROI, light: bool) -> None: ...
    @abstractmethod
    def image_ready(self) -> bool: ...
    @abstractmethod
    def read_frame(self) -> bytes: ...
    @abstractmethod
    def abort(self) -> None: ...

    # --- optional (implement only if the hardware has it) -----------------
    def set_read_mode(self, mode: str) -> None:
        raise DeviceError("camera has no selectable read modes")

    def set_target_temp(self, celsius: float) -> None:
        raise DeviceError("camera has no cooler")

    def set_cooler(self, on: bool) -> None:
        raise DeviceError("camera has no cooler")

    def get_temperature(self) -> float | None:
        return None

    def get_cooler_power(self) -> int | None:
        return None

    def set_dew_heater(self, power: int) -> None:
        raise DeviceError("camera has no dew heater")
