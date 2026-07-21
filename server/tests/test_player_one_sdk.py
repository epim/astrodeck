import sys
from pathlib import Path
import pytest
from astrodeck.devices.cameras import player_one_sdk as p


def test_signatures_declared():
    assert "POAGetCameraProperties" in p._SIGNATURES
    assert "POAStartExposure" in p._SIGNATURES
    # LRN pinning: the sensor-mode API is bound
    assert "POASetSensorMode" in p._SIGNATURES
    # every globally-declared signature is a verified export
    assert set(p._SIGNATURES) <= set(p._EXPORTS)
    # POASetConfig/POAGetConfig are declared PER-CALL (value is c_int/c_double,
    # not a union struct — matching the vendor binding) but still gate the load
    assert {"POASetConfig", "POAGetConfig"} <= set(p._EXPORTS)


def test_make_seam():
    assert p.make_player_one is p.PlayerOneSdk


def test_construction_is_clean():
    # Constructing either loads the vendored DLL or raises PlayerOneSdkError
    # (absent) — never ImportError/AttributeError (a broken binding table).
    try:
        sdk = p.PlayerOneSdk()
    except p.PlayerOneSdkError:
        return
    assert sdk.count() >= 0


@pytest.mark.skipif(
    sys.platform != "win32" or not (p._VENDOR_DIR / "PlayerOneCamera.dll").is_file(),
    reason="requires win32 + vendored PlayerOneCamera.dll")
def test_real_dll_loads():
    sdk = p.PlayerOneSdk()      # loads the vendored DLL; all 20 exports resolve
    assert sdk.count() >= 0
