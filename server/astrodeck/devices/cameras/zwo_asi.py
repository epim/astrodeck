"""ZWO ASI camera adapter — maps the ASICamera2 SDK onto the CameraAdapter waist.
On this rig the ASI220MM is the GUIDE camera (uncooled mono, no read modes); the
same adapter serves an imaging ASI just as well. Vendor-specific logic lives here
only; the engine (engine.py) owns the exposure loop and buffer assembly.

COOLING IS NOT IMPLEMENTED HERE YET. Exposure, gain, offset, ROI, binning, the
Bayer tag and the sensor temperature are; the cooler, its setpoint and the
anti-dew heater are not, so ``capabilities()`` reports ``has_cooler=False`` even
for a cooled model and the engine never calls a hook that would raise. A cooled
ASI runs uncooled natively, or through Alpaca. The Player One adapter is the
template when this lands."""
from __future__ import annotations

from ..base import DeviceError
from .adapter import ROI, CameraAdapter, CameraCapabilities
from . import registry
from .zwo_asi_sdk import (
    AsiProperty, make_asi, ASI_GAIN, ASI_EXPOSURE, ASI_OFFSET, ASI_TEMPERATURE,
    ASI_IMG_RAW16, ASI_EXP_SUCCESS, ASI_EXP_FAILED,
)

__all__ = ["AsiCameraAdapter", "AsiProperty"]

#: ASISetControlValue's value is a 32-bit c_long; clamp exposure microseconds so a
#: very long exposure can't OverflowError at the ctypes boundary (the SDK then
#: applies its own per-camera max). 2**31-1 us ~= 2147 s.
_MAX_EXPOSURE_US = 2_147_483_647


class AsiCameraAdapter(CameraAdapter):
    def __init__(self, sdk=None, index: int = 0):
        self._sdk = sdk if sdk is not None else make_asi()
        self._index = index
        self._cam_id = 0
        self._prop: AsiProperty | None = None
        self._gain_max = 0
        self._offset_max = 0
        self._nbytes = 0
        self._applied: ROI | None = None

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
            # What the DRIVER can do, not what the camera has: see the module
            # docstring. Advertising the SDK's IsCoolerCam here let a night
            # connect cleanly and then fail its cool-down step.
            roi_supported=True, has_cooler=False, has_dew_heater=False,
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
        us = min(int(round(seconds * 1e6)), _MAX_EXPOSURE_US)
        self._sdk.set_control(self._cam_id, ASI_EXPOSURE, us)
        self._sdk.set_control(self._cam_id, ASI_GAIN, int(gain))
        self._sdk.set_control(self._cam_id, ASI_OFFSET, int(offset))
        w, h = roi.w // roi.bin, roi.h // roi.bin
        self._sdk.set_roi(self._cam_id, w, h, roi.bin, ASI_IMG_RAW16)
        self._place(roi)
        # THE DOWNLOAD IS SIZED FROM WHAT THE CAMERA APPLIED, NOT WHAT WE ASKED.
        # Sizing it from the request is what makes a mismatch invisible: the
        # buffer is ours, ASIGetDataAfterExp fills the front of it and returns
        # SUCCESS for any buffer that is big ENOUGH, and read_frame hands back
        # exactly len == request. So the length can never disagree with the
        # request no matter what the sensor did, and the engine lays rows of the
        # applied width out at the requested one — the sheared, tiled picture of
        # 2026-07-31. Reading the geometry back is the only thing that can see it.
        #
        # ON ZWO SPECIFICALLY THIS IS A NET, NOT A LIVE CATCH — but for a
        # narrower reason than this comment used to give, and the difference
        # matters because a wrong reason scopes the next search wrongly.
        #
        # The vendored ASICamera2.dll answers a misaligned geometry by FAILING
        # it with ASI_ERROR_INVALID_SIZE, so a violation surfaces through _check
        # as a loud error rather than as a quietly different frame. That is now
        # read out of the DLL — the bool-to-error-code conversion and all three
        # alignment tests are cited by address at AsiSdk.ALIGN_H_BINNED — and no
        # longer inferred from the fact that its log strings say "Failed to set".
        # (Contrast Player One, which rounds down and reports POA_OK: see
        # PlayerOneSdk.ALIGN_W. Opposite answers; do not conflate them.)
        #
        # WHAT IS NOT TRUE, and was asserted here until it was checked: that
        # this rig's ASI is nowhere near the limit. The rule that bites is on
        # the BINNED HEIGHT (h % 8), not on the width, and the ASI220MM is
        # 1920x1080 — 1080/540/360/270 at bins 1-4, of which 540 and 270 are not
        # multiples of 8. So a full-frame bin-2 or bin-4 exposure on THIS camera
        # is refused by the SDK. Nothing asks for one today: the guide preview
        # is hard-coded to binning=1 and the native guider is built with no
        # binning key, so NativeGuider's default of 1 stands. It would be an
        # unexplained SDK error the day something does, which is a different
        # bug from #110 and a real one — see the ASI220MM row of
        # test_camera_roi_readback.py's per-bin table.
        self._applied = self._read_back(roi)
        aw, ah = self._binned(self._applied or roi)
        self._nbytes = aw * ah * 2
        self._sdk.start_exposure(self._cam_id, not light)

    @staticmethod
    def _binned(r: ROI) -> tuple[int, int]:
        return r.w // r.bin, r.h // r.bin

    def _place(self, roi: ROI) -> None:
        """Put the subframe where the caller asked. ASISetROIFormat re-centres
        the ROI, so without this an (x, y) request is silently discarded and the
        frame is of a different patch of sky than the one requested."""
        sp = getattr(self._sdk, "set_start_pos", None)
        if sp is None:
            if roi.x or roi.y:
                raise DeviceError(
                    f"this ASI SDK cannot place a subframe, so the requested "
                    f"origin ({roi.x}, {roi.y}) could not be applied; the frame "
                    "would be of a different part of the sensor than requested")
            return
        sp(self._cam_id, roi.x // roi.bin, roi.y // roi.bin)

    def _read_back(self, roi: ROI) -> ROI | None:
        """What ASIGetROIFormat/ASIGetStartPos say the camera settled on, in the
        request's unbinned units. None when the injected SDK cannot be asked."""
        get = getattr(self._sdk, "get_roi", None)
        if get is None:
            return None
        w, h, b, fmt = get(self._cam_id)
        if fmt != ASI_IMG_RAW16:
            # _shape decodes little-endian uint16 unconditionally. A RAW8 frame
            # decoded that way pairs adjacent pixels into one — refuse it here
            # rather than deliver a half-width picture of the same sky.
            raise DeviceError(
                f"camera applied image type {fmt}, not RAW16 ({ASI_IMG_RAW16}); "
                "the download would be decoded as 16-bit and come out wrong")
        b = b or roi.bin
        x, y = 0, 0
        gsp = getattr(self._sdk, "get_start_pos", None)
        if gsp is not None:
            x, y = gsp(self._cam_id)
        return ROI(x=x * b, y=y * b, w=w * b, h=h * b, bin=b)

    def applied_roi(self) -> ROI | None:
        return self._applied

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
