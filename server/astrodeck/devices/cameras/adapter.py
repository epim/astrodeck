"""The camera adapter waist: the narrow, stable interface a brand implements,
plus the additive capability descriptor that keeps brand features from being
flattened to a lowest-common-denominator. No SDK, no asyncio, no CameraFrame —
the engine (engine.py) owns all of that and merely drives these hooks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping

from ..base import DeviceError

#: Appended to camera-unavailable errors + logged. USB cameras allow exactly one
#: owner, so the usual cause of "attached but not found/openable" is another app
#: holding the device. Seen at-scope 2026-07-21: the ASCOM Remote/Alpaca server
#: had grabbed both cameras, so native enumeration returned 0 until it was killed.
CAMERA_BUSY_HINT = (
    "If a camera is attached but not found, another application may be holding "
    "it -- USB cameras allow only ONE connection at a time. Common culprits: "
    "ASCOM Remote / Alpaca device server, NINA, SharpCap, or the vendor's own "
    "app (ASIStudio, Player One). Close it and retry."
)


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
    #: Can this adapter run a bounded BURST - many short exposures at a fixed
    #: ROI without re-configuring the sensor between them? False (the default)
    #: means the only way to get N frames is N full start/ready/read cycles,
    #: which on this engine's polling floor tops out near 20 fps. Declared, never
    #: assumed: a brand that has not implemented the hooks below says so here.
    burst_supported: bool = False
    #: Highest frame rate this adapter will honour at full ROI, or None when it
    #: cannot say. Informational: the recorder clamps to it and reports the
    #: clamp rather than silently delivering a slower file than the caller asked
    #: for - the same rule Focuser.is_moving keeps about hopeful answers.
    max_fps: float | None = None
    #: Smallest ROI alignment the sensor will actually apply, in unbinned pixels
    #: (ZWO wants width % 8 == 0 and height % 2 == 0). The recorder rounds to it
    #: BEFORE the request so applied_roi never has to disagree.
    roi_align: tuple[int, int] = (1, 1)
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

    # --- burst / video (stage 1b) -----------------------------------------
    #
    # THE FAST-CADENCE PATH, AND WHY IT IS THREE HOOKS AND NOT ONE.
    #
    # The four exposure primitives above are SNAP mode: configure the sensor,
    # start, poll, download, repeat. Every vendor SDK here also has a streaming
    # mode that keeps the sensor configured and hands frames off a running
    # pipeline (ZWO: ASIStartVideoCapture / ASIGetVideoData / ASIStopVideoCapture;
    # Player One: POAStartExposure(True) / POAGetImageData / POAStopExposure),
    # and that is the difference between ~20 fps and the hundreds of fps a
    # planetary recording wants.
    #
    # Nothing implements them yet. No binding in this tree declares a video entry
    # point at all -- zwo_asi_sdk._SIGNATURES binds only the four exposure calls,
    # and _ASI_CAMERA_INFO.SupportedVideoFormat is read by nothing -- so
    # implementing these for ZWO and Player One is a vendor-verification exercise
    # (devices/vendor_verify.py) against the real DLLs, deferred as stage 1b.
    # Until then imaging/video.py uses its generic fallback and SAYS SO in the
    # recording's clamp_reason, rather than quietly delivering 20 fps to someone
    # who asked for 200.
    #
    # Three hooks rather than one because the recorder must be able to stop: a
    # single blocking "record N frames" call cannot be cancelled, cannot publish
    # progress, and cannot leave a valid short file behind.
    def burst_begin(self, *, roi: ROI, gain: int, offset: int,
                    exposure_s: float) -> None:
        """Configure the sensor once and start the stream."""
        raise DeviceError("camera has no burst/video path")

    def burst_next(self, timeout_s: float) -> bytes | None:
        """One frame's raw bytes, or None when none arrived within the timeout.

        None is a DROPPED frame, not an error and not the end: a stream that
        misses a frame under USB contention is normal, and the recorder counts
        it. Only an exception ends a recording."""
        raise DeviceError("camera has no burst/video path")

    def burst_end(self) -> None:
        """Stop the stream. Must be safe to call after a failed begin."""
        raise DeviceError("camera has no burst/video path")

    def set_target_temp(self, celsius: float) -> None:
        raise DeviceError("camera has no cooler")

    def set_cooler(self, on: bool) -> None:
        raise DeviceError("camera has no cooler")

    def get_temperature(self) -> float | None:
        return None

    def get_cooler_power(self) -> int | None:
        return None

    def get_target_temp(self) -> float | None:
        """The cooler SETPOINT the camera is holding, or None when this brand's
        SDK cannot read it back. Distinct from ``get_temperature`` (the sensor's
        actual temperature) — a frame's header wants both, and the difference
        between them is how you tell a stabilised cooler from a losing one."""
        return None

    def get_cooler_on(self) -> bool | None:
        """Whether the TEC is engaged, or None when unreadable."""
        return None

    def set_dew_heater(self, power: int) -> None:
        raise DeviceError("camera has no dew heater")

    def get_dew_heater(self) -> int | None:
        """The heater's current level (0-100%), or None when this brand's SDK
        has no readable heater register.

        None is UNKNOWN, never OFF — see Camera.get_dew_heater for the bug that
        distinction exists to prevent. Returns rather than raises for the same
        reason get_temperature does: it is polled."""
        return None

    def applied_roi(self) -> "ROI | None":
        """The geometry the sensor ACTUALLY applied for the exposure in flight,
        in the same unbinned-pixel units as the request, or None when this brand
        cannot be asked.

        A camera may legitimately give you a different frame from the one you
        asked for: widths get rounded to an alignment, subframes get clamped to
        the sensor, a bin change resets the size. What it must not do is let the
        caller keep believing the request, because the download is laid out at
        ``w // bin`` pixels per row — lay rows of one width out at another and
        every row starts a constant offset into the last, which is a picture
        sheared diagonally and repeated, produced with no error anywhere
        (rendered from a real sky frame in tests/test_camera_roi_shear.py).

        None means the engine has NOTHING to check the request against, so it
        falls back to it. It does not mean the request was honoured."""
        return None
