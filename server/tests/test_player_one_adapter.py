import numpy as np
import pytest
from astrodeck.devices.cameras.engine import NativeCamera


class FakePoaSdk:
    def __init__(self, w=6252, h=4176):
        self.calls = []
        self._w, self._h = w, h
        self._mode = "Normal"
        self._ready = True

    def count(self): return 1

    def get_properties(self, i):
        from astrodeck.devices.cameras.player_one import PoaProperty
        return PoaProperty(camera_id=0, name="Poseidon-M Pro", width=self._w,
                           height=self._h, pixel_size_um=3.76, is_color=False,
                           bayer=None, bit_depth=16, is_cooled=True,
                           max_bin=4, max_gain=600, max_offset=1000)

    def open(self, cid): self.calls.append("open")
    def close(self, cid): self.calls.append("close")
    def config_range(self, cid, config):
        from astrodeck.devices.cameras.player_one_sdk import POA_GAIN, POA_OFFSET
        return {POA_GAIN: (0, 600), POA_OFFSET: (0, 1000)}.get(config, (0, 0))
    def sensor_modes(self, cid): return ["Normal", "LowNoise"]
    def set_sensor_mode(self, cid, m): self.calls.append(f"mode:{m}"); self._mode = m
    def get_egain(self, cid): return 0.25
    def set_config(self, cid, k, v, is_auto=False): self.calls.append(f"cfg:{k}={v}")
    def get_config(self, cid, k): return 2000
    def set_image_format(self, cid, w, h, b, fmt): self.calls.append(f"fmt:{w}x{h}/{b}")
    def start_exposure(self, cid, is_single=True): self.calls.append("start")
    def image_ready(self, cid): return self._ready
    def get_image_data(self, cid, nbytes, timeout_ms=5000):
        return np.zeros(self._w * self._h, dtype="<u2").tobytes()
    def stop_exposure(self, cid): self.calls.append("stop")


def test_poa_capabilities_expose_lrn_and_hcg():
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter
    a = PlayerOneAdapter(sdk=FakePoaSdk(8, 6))
    a.open(0)   # ranges + sensor modes (LRN) + egain are read AFTER open
    caps = a.capabilities()
    assert caps.read_modes == ("Normal", "LowNoise")
    assert caps.hcg_threshold_gain == 125
    assert caps.has_cooler and caps.has_dew_heater
    assert abs(caps.extra["egain"] - 0.25) < 1e-9
    assert caps.bin_modes == (1, 2, 3, 4)
    assert caps.gain_range == (0, 600)   # from config_range, post-open


async def test_poa_lrn_applied_via_engine():
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter
    a = PlayerOneAdapter(sdk=FakePoaSdk(8, 6))
    cam = NativeCamera(a)
    await cam.connect()
    await cam.expose(0.01, gain=125, offset=10, read_mode="LowNoise")
    assert "mode:LowNoise" in a._sdk.calls
    assert a._sdk.calls.index("mode:LowNoise") < a._sdk.calls.index("start")


async def test_poa_exposure_clamped_no_overflow():
    # 1e5 s -> 1e11 us must clamp to the 32-bit max, not OverflowError at the
    # POASetConfig c_int boundary (review #3).
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter, _MAX_EXPOSURE_US
    from astrodeck.devices.cameras.player_one_sdk import POA_EXPOSURE
    fa = FakePoaSdk(8, 6)
    cam = NativeCamera(PlayerOneAdapter(sdk=fa))
    await cam.connect()
    await cam.expose(100000, gain=0, offset=0)
    assert f"cfg:{POA_EXPOSURE}={_MAX_EXPOSURE_US}" in fa.calls


async def test_poa_cooling_via_engine():
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter
    a = PlayerOneAdapter(sdk=FakePoaSdk(8, 6))
    cam = NativeCamera(a)
    await cam.connect()
    await cam.set_cooler(True, target_c=-10.0)
    assert any("cfg:" in c for c in a._sdk.calls)
    await cam.set_dew_heater(60)
    assert any("cfg:" in c for c in a._sdk.calls)
