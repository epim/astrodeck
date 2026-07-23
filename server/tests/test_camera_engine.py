import asyncio
import numpy as np
import pytest
from astrodeck.devices.base import CameraFrame
from astrodeck.devices.cameras.adapter import ROI, CameraCapabilities, CameraAdapter
from astrodeck.devices.cameras.engine import NativeCamera


class FakeAdapter(CameraAdapter):
    def __init__(self, w=6, h=4, ready_after=1):
        self.calls: list[str] = []
        self._w, self._h = w, h
        self._ready_after = ready_after
        self._polls = 0
        self.opened = False
        self.last_start = None

    def capabilities(self):
        return CameraCapabilities(
            sensor_width=self._w, sensor_height=self._h, pixel_size_um=3.76,
            bit_depth=16, bayer_pattern=None, gain_range=(0, 600),
            offset_range=(0, 255), bin_modes=(1, 2), roi_supported=True,
            has_cooler=False, has_dew_heater=False, max_adu=65535)

    def open(self, index): self.calls.append(f"open{index}"); self.opened = True
    def close(self): self.calls.append("close"); self.opened = False

    def start_exposure(self, *, seconds, gain, offset, roi, light):
        self.calls.append("start")
        self.last_start = dict(seconds=seconds, gain=gain, offset=offset,
                               roi=roi, light=light)
        self._polls = 0

    def image_ready(self):
        self._polls += 1
        return self._polls >= self._ready_after

    def read_frame(self):
        self.calls.append("read")
        arr = np.arange(self._w * self._h, dtype="<u2")
        return arr.tobytes()

    def abort(self): self.calls.append("abort")


class CoolAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self._cooler = False
        self._target = None
        self._temp = 20.0
        self._read_mode = None

    def capabilities(self):
        c = super().capabilities()
        return CameraCapabilities(**{**c.__dict__,
                                     "has_cooler": True, "has_dew_heater": True,
                                     "read_modes": ("Normal", "LowNoise")})

    def set_read_mode(self, mode): self.calls.append(f"mode:{mode}"); self._read_mode = mode
    def set_target_temp(self, c): self._target = c
    def set_cooler(self, on): self._cooler = on
    def get_temperature(self): return -10.0 if self._cooler else self._temp
    def get_cooler_power(self): return 42 if self._cooler else 0
    def set_dew_heater(self, power): self.calls.append(f"dew:{power}")


# --- Task 2: core exposure lifecycle -------------------------------------

async def test_connect_maps_capabilities_to_camera_fields():
    cam = NativeCamera(FakeAdapter(w=6, h=4))
    await cam.connect()
    assert cam.sensor_width == 6 and cam.sensor_height == 4
    assert cam.pixel_size_um == 3.76
    assert cam.can_cool is False
    assert cam.max_gain == 600
    assert cam.max_bin == 2  # UX-27: max(bin_modes=(1, 2))


# --- Task 7 (photometry/SNR design): egain surfaced from caps.extra ------

async def test_connect_maps_egain_from_caps_extra():
    # Mirrors how Player One / ZWO ASI adapters populate capabilities().extra
    # (player_one.py:63, zwo_asi.py:51) — the engine must copy it onto the
    # Camera so hub.py's status.camera payload (and the client photometry
    # profile prefill) can read a real e-/ADU value.
    class EgainAdapter(FakeAdapter):
        def capabilities(self):
            c = super().capabilities()
            return CameraCapabilities(**{**c.__dict__, "extra": {"egain": 0.25}})

    cam = NativeCamera(EgainAdapter())
    await cam.connect()
    assert cam.egain == 0.25


async def test_connect_defaults_egain_when_absent_from_extra():
    # A camera whose adapter doesn't report egain (sim/Alpaca-style, or a native
    # adapter with no readout for it) must leave egain at the inert 0.0 default
    # — never a stale/garbage value — so the client shows its honest prompt.
    cam = NativeCamera(FakeAdapter())
    await cam.connect()
    assert cam.egain == 0.0


async def test_expose_returns_shaped_linear_frame():
    fa = FakeAdapter(w=6, h=4)
    cam = NativeCamera(fa)
    await cam.connect()
    frame = await cam.expose(0.01, gain=100, offset=10, binning=1)
    assert isinstance(frame, CameraFrame)
    assert frame.data.dtype == np.uint16
    assert frame.data.shape == (4, 6)          # (height, width) row-major
    assert frame.data_is_linear is True
    assert frame.full_well == 65535
    assert frame.gain == 100 and frame.offset == 10
    assert fa.calls == ["open0", "start", "read"]
    assert fa.last_start["light"] is True


async def test_expose_cancel_aborts_in_camera():
    fa = FakeAdapter(ready_after=10_000)     # never ready
    cam = NativeCamera(fa)
    await cam.connect()
    task = asyncio.create_task(cam.expose(5.0, gain=0, offset=0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "abort" in fa.calls


# --- Task 3: cooling / read-mode / dew delegation ------------------------

async def test_cooling_delegates_to_adapter():
    ca = CoolAdapter()
    cam = NativeCamera(ca)
    await cam.connect()
    assert cam.can_cool is True
    await cam.set_cooler(True, target_c=-10.0)
    assert ca._cooler is True and ca._target == -10.0
    assert await cam.get_temperature() == -10.0
    assert await cam.cooler_power() == 42


async def test_expose_applies_read_mode_before_start():
    ca = CoolAdapter()
    cam = NativeCamera(ca)
    await cam.connect()
    await cam.expose(0.01, gain=0, offset=0, read_mode="LowNoise")
    assert ca.calls.index("mode:LowNoise") < ca.calls.index("start")
    assert ca._read_mode == "LowNoise"


async def test_dew_heater_delegates():
    ca = CoolAdapter()
    cam = NativeCamera(ca)
    await cam.connect()
    await cam.set_dew_heater(75)
    assert "dew:75" in ca.calls


async def test_connect_is_idempotent():
    # the hub's _apply_connect_result re-connects a device the orchestrator
    # already connected; a second open() would error, so connect() must no-op.
    fa = FakeAdapter(w=4, h=3)
    cam = NativeCamera(fa)
    await cam.connect()
    await cam.connect()          # second connect = no-op
    assert fa.calls.count("open0") == 1
    assert cam.connected is True


async def test_low_bit_depth_still_decodes_raw16():
    # A sensor reporting <=8-bit ADC still arrives as RAW16 (adapters always
    # download RAW16), so the engine must decode uint16, not uint8 (review #2).
    class EightBit(FakeAdapter):
        def capabilities(self):
            c = super().capabilities()
            return CameraCapabilities(**{**c.__dict__, "bit_depth": 8})

    fa = EightBit(w=4, h=3)
    cam = NativeCamera(fa)
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0)
    assert f.data.dtype == np.uint16 and f.data.shape == (3, 4)
    # read_frame returns a <u2 ramp 0..11 -> decoded values must match, not be
    # the byte-misaligned garbage a uint8 read would produce.
    assert f.data[0, 0] == 0 and f.data[0, 1] == 1 and f.data[2, 3] == 11
