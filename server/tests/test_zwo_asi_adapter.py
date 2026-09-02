import numpy as np
import pytest
from astrodeck.devices.base import CameraFrame
from astrodeck.devices.cameras.engine import NativeCamera


class FakeAsiSdk:
    def __init__(self, w=8, h=6):
        self.calls = []
        self._w, self._h = w, h
        self._status = 0

    def count(self): return 1

    def get_property(self, cam_id):
        from astrodeck.devices.cameras.zwo_asi import AsiProperty
        return AsiProperty(name="ZWO ASI220MM", width=self._w, height=self._h,
                           pixel_size_um=4.0, is_color=False, bayer=None,
                           bit_depth=16, bin_modes=(1, 2), max_gain=300,
                           max_offset=255, has_cooler=False, camera_id=0,
                           egain=1.0)

    def open(self, cam_id): self.calls.append("open")
    def close(self, cam_id): self.calls.append("close")
    def control_range(self, cam_id, ctrl):
        from astrodeck.devices.cameras.zwo_asi_sdk import ASI_GAIN, ASI_OFFSET
        return {ASI_GAIN: (0, 300), ASI_OFFSET: (0, 255)}.get(ctrl, (0, 0))
    def set_control(self, cam_id, ctrl, value, auto=False):
        self.calls.append(f"ctrl:{ctrl}={value}")
    def get_control(self, cam_id, ctrl): return 200  # temp 0.1C
    def set_roi(self, cam_id, w, h, bin, img_type):
        self.calls.append(f"roi:{w}x{h}/{bin}")
    def start_exposure(self, cam_id, dark):
        self.calls.append(f"start:dark={dark}"); self._status = 2
    def exp_status(self, cam_id): return self._status
    def get_data(self, cam_id, nbytes):
        return np.arange(self._w * self._h, dtype="<u2").tobytes()
    def stop_exposure(self, cam_id): self.calls.append("stop")


async def test_asi_adapter_exposes_via_engine():
    from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter
    a = AsiCameraAdapter(sdk=FakeAsiSdk(8, 6), index=0)
    cam = NativeCamera(a)
    await cam.connect()
    assert cam.sensor_width == 8 and cam.bayer_pattern is None
    assert cam.can_cool is False
    assert cam.max_gain == 300          # gain range read AFTER open (control caps)
    f = await cam.expose(0.5, gain=120, offset=10)
    assert isinstance(f, CameraFrame) and f.data.shape == (6, 8)
    # exposure seconds -> microseconds control
    assert any("ctrl:1=500000" in c for c in a._sdk.calls)


async def test_asi_exposure_clamped_no_overflow():
    # a huge exposure (1e5 s -> 1e11 us) must clamp to the 32-bit max, not
    # OverflowError at the ctypes c_long boundary (review #3).
    from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter, _MAX_EXPOSURE_US
    from astrodeck.devices.cameras.zwo_asi_sdk import ASI_EXPOSURE
    fa = FakeAsiSdk(8, 6)
    cam = NativeCamera(AsiCameraAdapter(sdk=fa))
    await cam.connect()
    await cam.expose(100000, gain=0, offset=0)
    assert f"ctrl:{ASI_EXPOSURE}={_MAX_EXPOSURE_US}" in fa.calls


async def test_asi_registered_in_registry():
    import astrodeck.devices.cameras.zwo_asi  # noqa: F401 (import registers)
    from astrodeck.devices.cameras import registry
    assert "zwo-asi" in [n for n, _ in registry.iter_adapters()]


async def test_cooled_asi_reports_no_cooler_until_cooling_is_implemented():
    # The SDK says IsCoolerCam for a cooled ASI, but this adapter implements
    # none of the cooling hooks, so the waist's defaults raise "camera has no
    # cooler" on the first set_target_temp. Advertising the cooler lets a night
    # connect cleanly and then fail its cool-down step with a message that
    # blames the camera. Report what the DRIVER can do, not what the camera has.
    from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter, AsiProperty

    class CooledAsiSdk(FakeAsiSdk):
        def get_property(self, cam_id):
            return AsiProperty(name="ZWO ASI2600MM Pro", width=self._w,
                               height=self._h, pixel_size_um=3.76, is_color=False,
                               bayer=None, bit_depth=16, bin_modes=(1, 2),
                               max_gain=460, max_offset=255, has_cooler=True,
                               camera_id=0, egain=1.0)

    cam = NativeCamera(AsiCameraAdapter(sdk=CooledAsiSdk(8, 6)))
    await cam.connect()
    assert cam.can_cool is False
