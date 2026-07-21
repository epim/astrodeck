import numpy as np
import pytest
from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import ROI, CameraCapabilities, CameraAdapter


def _caps(**kw):
    base = dict(sensor_width=100, sensor_height=80, pixel_size_um=3.76,
                bit_depth=16, bayer_pattern=None, gain_range=(0, 600),
                offset_range=(0, 255), bin_modes=(1, 2), roi_supported=True,
                has_cooler=False, has_dew_heater=False, max_adu=65535)
    base.update(kw)
    return CameraCapabilities(**base)


class _Min(CameraAdapter):
    """Implements ONLY the required hooks; optional ones inherit defaults."""
    def capabilities(self): return _caps()
    def open(self, index): pass
    def close(self): pass
    def start_exposure(self, *, seconds, gain, offset, roi, light): pass
    def image_ready(self): return True
    def read_frame(self): return b"\x00" * (100 * 80 * 2)
    def abort(self): pass


def test_capabilities_defaults():
    c = _caps()
    assert c.read_modes == ()
    assert c.hcg_threshold_gain is None
    assert dict(c.extra) == {}


def test_capabilities_frozen():
    c = _caps()
    with pytest.raises(Exception):
        c.sensor_width = 999  # frozen


def test_roi_fields():
    r = ROI(x=1, y=2, w=3, h=4, bin=2)
    assert (r.x, r.y, r.w, r.h, r.bin) == (1, 2, 3, 4, 2)


def test_optional_hooks_default_behavior():
    a = _Min()
    assert a.get_temperature() is None
    assert a.get_cooler_power() is None
    for call in (lambda: a.set_read_mode("x"),
                 lambda: a.set_target_temp(-10.0),
                 lambda: a.set_cooler(True),
                 lambda: a.set_dew_heater(50)):
        with pytest.raises(DeviceError):
            call()


def test_read_modes_declared_survives():
    c = _caps(read_modes=("Normal", "LowNoise"), hcg_threshold_gain=125)
    assert c.read_modes == ("Normal", "LowNoise")
    assert c.hcg_threshold_gain == 125
