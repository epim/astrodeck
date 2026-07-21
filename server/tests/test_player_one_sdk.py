import sys
from pathlib import Path
import pytest
from astrodeck.devices.cameras import player_one_sdk as p


def test_signatures_declared():
    assert "POAGetCameraProperties" in p._SIGNATURES
    assert "POAStartExposure" in p._SIGNATURES
    # LRN pinning: the sensor-mode API is bound
    assert "POASetSensorMode" in p._SIGNATURES
    assert set(p._EXPORTS) <= set(p._SIGNATURES)


def test_make_seam():
    assert p.make_player_one is p.PlayerOneSdk


def test_construction_is_clean():
    # No SDK on the dev box -> raises PlayerOneSdkError, never ImportError.
    try:
        p.PlayerOneSdk()
    except p.PlayerOneSdkError:
        return


@pytest.mark.skipif(
    sys.platform != "win32"
    or not (Path(p.__file__).resolve().parent.parent / "vendor" / "playerone"
            / "PlayerOneCamera.dll").is_file(),
    reason="requires win32 + vendored PlayerOneCamera.dll")
def test_real_dll_loads():
    sdk = p.PlayerOneSdk()
    assert sdk.count() >= 0
