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
    f = await cam.expose(0.5, gain=120, offset=10)
    assert isinstance(f, CameraFrame) and f.data.shape == (6, 8)
    # exposure seconds -> microseconds control
    assert any("ctrl:1=500000" in c for c in a._sdk.calls)


async def test_asi_registered_in_registry():
    import astrodeck.devices.cameras.zwo_asi  # noqa: F401 (import registers)
    from astrodeck.devices.cameras import registry
    assert "zwo-asi" in [n for n, _ in registry.iter_adapters()]
