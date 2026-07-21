"""ZWO ASI camera adapter — maps the ASICamera2 SDK onto the CameraAdapter waist.
On this rig the ASI220MM is the GUIDE camera (uncooled mono, no read modes); the
same adapter serves an imaging ASI just as well. Vendor-specific logic lives here
only; the engine (engine.py) owns the exposure loop and buffer assembly."""
from __future__ import annotations

from ..base import DeviceError
from .adapter import ROI, CameraAdapter, CameraCapabilities
from . import registry
from .zwo_asi_sdk import (
    AsiProperty, make_asi, ASI_GAIN, ASI_EXPOSURE, ASI_OFFSET, ASI_TEMPERATURE,
    ASI_IMG_RAW16, ASI_EXP_SUCCESS, ASI_EXP_FAILED,
)

__all__ = ["AsiCameraAdapter", "AsiProperty"]


class AsiCameraAdapter(CameraAdapter):
    def __init__(self, sdk=None, index: int = 0):
        self._sdk = sdk if sdk is not None else make_asi()
        self._index = index
        self._cam_id = 0
        self._prop: AsiProperty | None = None
        self._gain_max = 0
        self._offset_max = 0
        self._nbytes = 0

    def _basic(self) -> AsiProperty:
        # count() first: the SDK's camera list must be enumerated before
        # get_property(index), or it returns INVALID_INDEX (at-scope lesson).
        self._sdk.count()
        if self._prop is None:
            self._prop = self._sdk.get_property(self._index)
            self._cam_id = self._prop.camera_id
        return self._prop

    def capabilities(self) -> CameraCapabilities:
        p = self._basic()
        return CameraCapabilities(
            sensor_width=p.width, sensor_height=p.height,
            pixel_size_um=p.pixel_size_um, bit_depth=p.bit_depth,
            bayer_pattern=p.bayer, gain_range=(0, self._gain_max),
            offset_range=(0, self._offset_max), bin_modes=tuple(p.bin_modes),
            roi_supported=True, has_cooler=p.has_cooler, has_dew_heater=False,
            max_adu=65535, read_modes=(), hcg_threshold_gain=None,
            extra={"egain": p.egain} if p.egain else {})

    def open(self, index: int) -> None:
        self._index = index
        p = self._basic()
        self._sdk.open(p.camera_id)
        # gain/offset ranges live in the control caps -> only valid once OPEN.
        self._gain_max = self._sdk.control_range(self._cam_id, ASI_GAIN)[1]
        self._offset_max = self._sdk.control_range(self._cam_id, ASI_OFFSET)[1]

    def close(self) -> None:
        if self._prop is not None:
            self._sdk.close(self._cam_id)

    def start_exposure(self, *, seconds: float, gain: int, offset: int,
                       roi: ROI, light: bool) -> None:
        self._sdk.set_control(self._cam_id, ASI_EXPOSURE, int(round(seconds * 1e6)))
        self._sdk.set_control(self._cam_id, ASI_GAIN, int(gain))
        self._sdk.set_control(self._cam_id, ASI_OFFSET, int(offset))
        w, h = roi.w // roi.bin, roi.h // roi.bin
        self._sdk.set_roi(self._cam_id, w, h, roi.bin, ASI_IMG_RAW16)
        self._nbytes = w * h * 2
        self._sdk.start_exposure(self._cam_id, not light)

    def image_ready(self) -> bool:
        st = self._sdk.exp_status(self._cam_id)
        if st == ASI_EXP_FAILED:
            raise DeviceError("ASI exposure failed")
        return st == ASI_EXP_SUCCESS

    def read_frame(self) -> bytes:
        return self._sdk.get_data(self._cam_id, self._nbytes)

    def abort(self) -> None:
        self._sdk.stop_exposure(self._cam_id)

    def get_temperature(self) -> float | None:
        try:
            return self._sdk.get_control(self._cam_id, ASI_TEMPERATURE) / 10.0
        except Exception:  # noqa: BLE001 - not all ASI report temperature
            return None


try:
    registry.register_adapter("zwo-asi", AsiCameraAdapter)
except ValueError:
    pass
