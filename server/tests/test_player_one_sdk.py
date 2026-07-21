import sys
import threading
from pathlib import Path
import pytest
from astrodeck.devices.cameras import player_one_sdk as p


class _FakeFn:
    """A ctypes-fn stand-in: callable, with settable restype/argtypes."""
    def __init__(self, impl):
        self._impl = impl
        self.restype = None
        self.argtypes = None

    def __call__(self, *a):
        return self._impl(*a)


class _FakeDll:
    def __init__(self, init_rc=0):
        self.calls = []

        def _set(cam, cfg, val, auto): self.calls.append(("set", cfg, val)); return 0
        def _open(cam): self.calls.append(("open", cam)); return 0
        def _init(cam): self.calls.append(("init", cam)); return init_rc
        def _close(cam): self.calls.append(("close", cam)); return 0

        self.POASetConfig = _FakeFn(_set)
        self.POAOpenCamera = _FakeFn(_open)
        self.POAInitCamera = _FakeFn(_init)
        self.POACloseCamera = _FakeFn(_close)


def _sdk_with(dll):
    s = object.__new__(p.PlayerOneSdk)   # bypass DLL loading
    s._d = dll
    s._cfg_lock = threading.Lock()
    return s


def test_set_target_temp_rounds_not_truncates():
    dll = _FakeDll()
    _sdk_with(dll).set_config(0, p.POA_TARGET_TEMP, -9.7)
    assert ("set", p.POA_TARGET_TEMP, -10) in dll.calls   # -10, not truncated -9


def test_open_closes_camera_on_init_failure():
    dll = _FakeDll(init_rc=5)             # POAInitCamera fails after open succeeds
    with pytest.raises(p.PlayerOneSdkError):
        _sdk_with(dll).open(0)
    assert [c[0] for c in dll.calls] == ["open", "init", "close"]


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
