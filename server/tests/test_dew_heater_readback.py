"""The dew heater's level has to be READ BACK, not remembered by the browser.

``set_dew_heater`` was write-only across the entire device contract, so after a
page reload the Capture screen had nothing to draw its slider from but its own
last write — which is 0 in a fresh tab. The heater was running at 60%, the
slider said 0%, and dragging it up to "turn it on" turned it DOWN.

So the thing these tests guard is not "a getter exists". It is the DIFFERENCE
BETWEEN UNKNOWN AND ZERO at every layer: a camera that cannot be asked must
publish no value at all. Defaulting to 0 anywhere in this chain reproduces the
original bug one layer further down, where the client can no longer tell.

Read-back for the ASIAIR backend lives with its fake box in
tests/test_asiair_backend.py (test_dew_heater_reads_back).
"""
from __future__ import annotations

import pytest

from astrodeck.devices.base import Camera, DeviceError
from astrodeck.devices.cameras.adapter import CameraAdapter, CameraCapabilities
from astrodeck.devices.cameras.engine import NativeCamera
from astrodeck.devices.cameras.player_one import PlayerOneAdapter
from astrodeck.devices.cameras.player_one_sdk import POA_HEATER_POWER
from astrodeck.devices.sim import SimCamera, SimRig
from astrodeck.hub import Hub


# --------------------------------------------------------------- the contract

class _WriteOnlyCamera(Camera):
    """A backend of the shape EVERY camera had before this fix: it can be told
    a level and cannot be asked one."""

    def __init__(self) -> None:
        super().__init__("Write-only Camera")
        self.has_dew_heater = True
        self.written: int | None = None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def expose(self, *a, **k):
        raise NotImplementedError

    async def abort_exposure(self) -> None:
        pass

    async def set_dew_heater(self, power: int) -> None:
        self.written = int(power)


async def test_camera_contract_default_is_unknown_not_off():
    cam = _WriteOnlyCamera()
    await cam.set_dew_heater(60)
    assert cam.written == 60          # precondition: the write half works
    assert await cam.get_dew_heater() is None


def test_adapter_default_is_unknown_not_off():
    class _Bare(CameraAdapter):
        def capabilities(self): raise NotImplementedError
        def open(self, index): pass
        def close(self): pass
        def start_exposure(self, **k): pass
        def image_ready(self): return True
        def read_frame(self): return b""
        def abort(self): pass

    a = _Bare()
    with pytest.raises(DeviceError):
        a.set_dew_heater(60)          # precondition: still write-only by default
    assert a.get_dew_heater() is None


# ------------------------------------------------------------------ the engine

class _HeaterAdapter(CameraAdapter):
    """Minimal adapter whose heater is a real, readable register."""

    def __init__(self, *, has_dew_heater: bool = True) -> None:
        self._level = 0
        self._has = has_dew_heater

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            sensor_width=8, sensor_height=6, pixel_size_um=3.76, bit_depth=16,
            bayer_pattern=None, gain_range=(0, 600), offset_range=(0, 100),
            bin_modes=(1,), roi_supported=True, has_cooler=False,
            has_dew_heater=self._has, max_adu=65535)

    def open(self, index): pass
    def close(self): pass
    def start_exposure(self, **k): pass
    def image_ready(self): return True
    def read_frame(self): return b"\x00" * (8 * 6 * 2)
    def abort(self): pass

    def set_dew_heater(self, power: int) -> None:
        self._level = int(power)

    def get_dew_heater(self) -> int | None:
        return self._level


async def test_native_camera_reads_the_level_back():
    cam = NativeCamera(_HeaterAdapter())
    await cam.connect()
    assert cam.has_dew_heater is True          # precondition
    await cam.set_dew_heater(60)
    assert await cam.get_dew_heater() == 60


async def test_native_camera_without_a_heater_says_unknown():
    a = _HeaterAdapter(has_dew_heater=False)
    cam = NativeCamera(a)
    await cam.connect()
    # Precondition that keeps this from passing vacuously: the ADAPTER would
    # answer. None below is therefore the engine's capability decision, not an
    # unimplemented hook returning its default.
    assert a.get_dew_heater() == 0
    assert cam.has_dew_heater is False
    assert await cam.get_dew_heater() is None


async def test_native_camera_survives_an_adapter_that_throws():
    class _Angry(_HeaterAdapter):
        def get_dew_heater(self):
            raise OSError("SDK says CONF_CANNOT_READ")

    cam = NativeCamera(_Angry())
    await cam.connect()
    assert cam.has_dew_heater is True          # precondition
    assert await cam.get_dew_heater() is None


# -------------------------------------------------------------- Player One SDK

class _ConfigStoreSdk:
    """FakePoaSdk's shape, but with a config store instead of a constant, so a
    read-back test cannot pass on a fake that answers the same number to every
    key it is asked."""

    def __init__(self) -> None:
        self.cfg: dict[int, object] = {}

    def count(self): return 1

    def get_properties(self, i):
        from astrodeck.devices.cameras.player_one import PoaProperty
        return PoaProperty(camera_id=0, name="Poseidon-M Pro", width=8, height=6,
                           pixel_size_um=3.76, is_color=False, bayer=None,
                           bit_depth=16, is_cooled=True, max_bin=4, max_gain=600,
                           max_offset=1000)

    def open(self, cid): pass
    def close(self, cid): pass
    def config_range(self, cid, config): return (0, 600)
    def sensor_modes(self, cid): return ["Normal"]
    def get_egain(self, cid): return 0.25
    def set_config(self, cid, k, v, is_auto=False): self.cfg[k] = v
    def get_config(self, cid, k): return self.cfg[k]


async def test_player_one_reads_poa_heater_power_back():
    sdk = _ConfigStoreSdk()
    a = PlayerOneAdapter(sdk=sdk)
    a.open(0)
    a.set_dew_heater(60)
    assert sdk.cfg[POA_HEATER_POWER] == 60     # precondition: the right register
    assert a.get_dew_heater() == 60


async def test_player_one_degrades_to_unknown_when_the_sdk_refuses():
    sdk = _ConfigStoreSdk()
    a = PlayerOneAdapter(sdk=sdk)
    a.open(0)
    # Nothing was ever written, so the store raises KeyError — the same shape a
    # real POA_ERROR_CONF_CANNOT_READ takes through the bindings.
    assert a.get_dew_heater() is None


# ----------------------------------------------------------------- hub publish

class _ReadableSimCamera(SimCamera):
    async def get_dew_heater(self) -> int | None:
        return self._dew_power


class _ThrowingSimCamera(SimCamera):
    asked = 0

    async def get_dew_heater(self) -> int | None:
        type(self).asked += 1
        raise DeviceError("camera is mid-download")


async def _status_camera(cam) -> dict:
    hub = Hub()
    await cam.connect()
    hub.devices["camera"] = cam
    try:
        status = await hub.poll_status()
    finally:
        await hub.disconnect_all()
    assert "camera" in status              # precondition: the block was built
    assert status["camera"]["has_dew_heater"] is True
    return status["camera"]


async def test_hub_publishes_the_level_when_the_camera_can_answer():
    cam = _ReadableSimCamera(SimRig())
    await cam.set_dew_heater(60)
    assert cam._dew_power == 60             # precondition: the camera holds 60
    block = await _status_camera(cam)
    assert block["dew_heater"] == 60


async def test_hub_omits_the_level_when_the_camera_cannot_answer():
    # A plain SimCamera is the "write-only" case: it has a heater and no getter.
    block = await _status_camera(SimCamera(SimRig()))
    assert "dew_heater" not in block


async def test_a_throwing_getter_costs_the_level_and_nothing_else():
    cam = _ThrowingSimCamera(SimRig())
    _ThrowingSimCamera.asked = 0
    block = await _status_camera(cam)
    # Precondition: the hub REACHED the getter. Without this the assertion below
    # would also hold on a hub that never asks anything.
    assert _ThrowingSimCamera.asked == 1
    assert "dew_heater" not in block
    # The 2 s status poll must survive it: the rest of the camera block — the
    # readings the Monitor is actually watching — is still there.
    assert block["width"] == cam.sensor_width
    assert "temperature" in block and "cooler" in block
