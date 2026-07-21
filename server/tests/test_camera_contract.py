"""The keystone: every camera adapter is subjected to the SAME behavioral
contract, driven by a fake SDK. A new brand can't silently violate it.

Real adapters append their fake-SDK-backed factory to CONTRACT_ADAPTERS from
their own test modules (see test_zwo_asi_adapter / test_player_one_adapter),
so the one suite grows to cover every bundled brand."""
import numpy as np
import pytest
from astrodeck.devices.base import CameraFrame, DeviceError
from astrodeck.devices.cameras.engine import NativeCamera

from test_camera_engine import FakeAdapter

# Start with the reference fake so the suite is never empty; real adapters
# extend this list in their own test modules.
CONTRACT_ADAPTERS = [("reference-fake", lambda: FakeAdapter(w=8, h=6))]


@pytest.fixture(params=CONTRACT_ADAPTERS, ids=lambda p: p[0])
def cam(request):
    _name, factory = request.param
    return NativeCamera(factory())


async def test_contract_expose_wellformed(cam):
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0)
    assert isinstance(f, CameraFrame)
    assert f.data.dtype == np.uint16 and f.data.ndim == 2
    assert f.data.shape == (cam.sensor_height, cam.sensor_width)
    assert f.data_is_linear is True


async def test_contract_capabilities_self_consistent(cam):
    await cam.connect()
    caps = cam._caps
    assert caps.gain_range[0] <= caps.gain_range[1]
    assert 1 in caps.bin_modes
    assert caps.max_adu > 0
    assert caps.bit_depth in (8, 12, 14, 16)
    if not caps.has_cooler:
        with pytest.raises(DeviceError):
            await cam.set_cooler(True)
