"""ctypes bindings for the Player One Camera SDK (Poseidon-M Pro imaging camera).

VERIFIED against the official Player One binding (python/pyPOACamera.py) and
header (include/PlayerOneCamera.h) from Camera SDK V3.10.1 (Windows), which is
bundled in ``vendor/playerone/`` under the SDK's MIT-style license (LICENSE
there; verdict docs/hardware/player-one-sdk-licensing.md). Enum values, struct
layouts, the sensor-mode API, and the POASetConfig/POAGetConfig calling
convention (value passed per-call as c_int or c_double — NOT a union struct)
all mirror the vendor binding.

Loader search order: ``ASTRODECK_PLAYERONE_SDK_DIR`` env → vendored
``astrodeck/vendor/playerone/`` → known installs. The module imports + declares
WITHOUT loading a DLL (load happens in PlayerOneSdk.__init__), so it stays
importable + unit-testable on a box with no SDK.

LRN (Low Read Noise) is selected via the SDK's SENSOR MODE API
(POAGetSensorModeCount / POAGetSensorModeInfo / POASetSensorMode).
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import os

_VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "playerone"

# --- POAConfig ids (verified vs pyPOACamera.py) ------------------------------
POA_EXPOSURE = 0          # microseconds (int)
POA_GAIN = 1
POA_TEMPERATURE = 3       # sensor temp, deg C (double, read-only)
POA_OFFSET = 7
POA_EGAIN = 15           # e-/ADU (double, read-only)
POA_COOLER_POWER = 16     # 0..100 (int, read-only)
POA_TARGET_TEMP = 17      # deg C (int)
POA_COOLER = 18           # on/off (bool)
POA_HEATER_POWER = 20     # dew heater 0..100 (int)
# POAImgFormat
POA_RAW8 = 0
POA_RAW16 = 1
# configs whose value member is the double, not the int
_FLOAT_CONFIGS = frozenset({POA_TEMPERATURE, POA_EGAIN})

ERROR_NAMES: dict[int, str] = {
    0: "OK", 1: "INVALID_INDEX", 2: "INVALID_ID", 3: "INVALID_CONFIG",
    4: "INVALID_ARGU", 5: "NOT_OPENED", 6: "DEVICE_NOT_FOUND", 7: "OUT_OF_LIMIT",
    8: "EXPOSURE_FAILED", 9: "TIMEOUT", 10: "SIZE_LESS", 11: "EXPOSING",
    12: "POINTER", 13: "CONF_CANNOT_WRITE", 14: "CONF_CANNOT_READ",
    15: "ACCESS_DENIED", 16: "OPERATION_FAILED", 17: "MEMORY_FAILED",
}

#: exports the bindings CALL — the load gate verifies every one is present.
_EXPORTS = [
    "POAGetCameraCount", "POAGetCameraProperties", "POAOpenCamera",
    "POAInitCamera", "POACloseCamera", "POASetConfig", "POAGetConfig",
    "POAGetConfigsCount", "POAGetConfigAttributesByConfigID",
    "POASetImageSize", "POASetImageBin", "POASetImageFormat",
    "POASetImageStartPos", "POAStartExposure", "POAStopExposure",
    "POAImageReady", "POAGetImageData", "POAGetSensorModeCount",
    "POAGetSensorModeInfo", "POASetSensorMode",
]

_DLL_SPECS: dict[str, tuple[list[str], list[str]]] = {
    "PlayerOneCamera.dll": (
        [r"C:\Program Files\PlayerOne\PlayerOneCamera.dll",
         r"C:\Program Files\Common Files\PlayerOne\PlayerOneCamera.dll"],
        _EXPORTS,
    ),
}


class PlayerOneSdkError(Exception):
    """A non-zero POAErrors from an SDK call (or a load failure)."""

    def __init__(self, code: int, fn: str):
        name = ERROR_NAMES.get(code, "?")
        super().__init__(f"Player One SDK {fn} failed ({name}, code {code})")
        self.code = code
        self.fn = fn
        self.code_name = name


class POAConfigValue(ctypes.Union):
    """union { long intValue; double floatValue; int boolValue; } — used only to
    reinterpret the config-attribute min/max bytes (as the vendor binding does)."""
    _fields_ = [("intValue", ctypes.c_long), ("floatValue", ctypes.c_double),
                ("boolValue", ctypes.c_int)]


class POACameraProperties(ctypes.Structure):
    """Verified vs pyPOACamera.py POACameraProperties."""
    _fields_ = [
        ("cameraModelName", ctypes.c_char * 256),
        ("userCustomID", ctypes.c_char * 16),
        ("cameraID", ctypes.c_int),
        ("maxWidth", ctypes.c_int),
        ("maxHeight", ctypes.c_int),
        ("bitDepth", ctypes.c_int),
        ("isColorCamera", ctypes.c_int),
        ("isHasST4Port", ctypes.c_int),
        ("isHasCooler", ctypes.c_int),
        ("isUSB3Speed", ctypes.c_int),
        ("bayerPattern", ctypes.c_int),
        ("pixelSize", ctypes.c_double),
        ("SN", ctypes.c_char * 64),
        ("sensorModelName", ctypes.c_char * 32),
        ("localPath", ctypes.c_char * 256),
        ("bins", ctypes.c_int * 8),
        ("imgFormats", ctypes.c_int * 8),
        ("isSupportHardBin", ctypes.c_int),
        ("pID", ctypes.c_int),
        ("reserved", ctypes.c_char * 248),
    ]


class POAConfigAttributes(ctypes.Structure):
    """Verified vs pyPOACamera.py POAConfigAttributes. min/max/default are stored
    as the union's bytes but declared double here — reinterpret via POAConfigValue."""
    _fields_ = [
        ("isSupportAuto", ctypes.c_int),
        ("isWritable", ctypes.c_int),
        ("isReadable", ctypes.c_int),
        ("configID", ctypes.c_int),
        ("valueType", ctypes.c_int),
        ("maxValue", ctypes.c_double),
        ("minValue", ctypes.c_double),
        ("defaultValue", ctypes.c_double),
        ("szConfName", ctypes.c_char * 64),
        ("szDescription", ctypes.c_char * 128),
        ("reserved", ctypes.c_char * 64),
    ]


class POASensorModeInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 64), ("desc", ctypes.c_char * 128)]


_BAYER = {0: "RG", 1: "BG", 2: "GR", 3: "GB"}  # POABayerPattern; -1 = mono


@dataclass
class PoaProperty:
    camera_id: int
    name: str
    width: int
    height: int
    pixel_size_um: float
    is_color: bool
    bayer: str | None
    bit_depth: int
    is_cooled: bool
    max_bin: int
    max_gain: int
    max_offset: int


_I, _PI = ctypes.c_int, ctypes.POINTER(ctypes.c_int)
_PU = ctypes.POINTER(ctypes.c_ubyte)

#: fn -> argtypes, restype c_int (POAErrors) / count. POASetConfig + POAGetConfig
#: are declared PER-CALL (the value is c_int or c_double by value/pointer, not a
#: union struct — matching the vendor binding), so they are absent here.
_SIGNATURES: dict[str, list] = {
    "POAGetCameraCount": [],
    "POAGetCameraProperties": [_I, ctypes.POINTER(POACameraProperties)],
    "POAOpenCamera": [_I], "POAInitCamera": [_I], "POACloseCamera": [_I],
    "POAGetConfigsCount": [_I, _PI],
    "POAGetConfigAttributesByConfigID":
        [_I, _I, ctypes.POINTER(POAConfigAttributes)],
    "POASetImageSize": [_I, _I, _I], "POASetImageBin": [_I, _I],
    "POASetImageFormat": [_I, _I], "POASetImageStartPos": [_I, _I, _I],
    "POAStartExposure": [_I, _I], "POAStopExposure": [_I],
    "POAImageReady": [_I, _PI],
    "POAGetImageData": [_I, _PU, ctypes.c_long, _I],
    "POAGetSensorModeCount": [_I, _PI],
    "POAGetSensorModeInfo": [_I, _I, ctypes.POINTER(POASensorModeInfo)],
    "POASetSensorMode": [_I, _I],
}


def _loads_with_exports(path: Path, exports: list[str]):
    try:
        dll = ctypes.CDLL(str(path))
    except OSError:
        return None
    if all(hasattr(dll, e) for e in exports):
        return dll
    return None


def _find_dll(basename: str):
    alternatives, exports = _DLL_SPECS[basename]
    candidates: list[Path] = []
    env = os.environ.get("ASTRODECK_PLAYERONE_SDK_DIR")
    if env:
        candidates.append(Path(env) / basename)
    candidates.append(_VENDOR_DIR / basename)
    candidates.extend(Path(p) for p in alternatives)
    for c in candidates:
        if c.is_file():
            dll = _loads_with_exports(c, exports)
            if dll is not None:
                return dll
    return None


def _check(code: int, fn: str) -> None:
    if code != 0:
        raise PlayerOneSdkError(code, fn)


def _declare(dll) -> None:
    for fn_name, argtypes in _SIGNATURES.items():
        fn = getattr(dll, fn_name, None)
        if fn is not None:
            fn.argtypes = argtypes
            fn.restype = ctypes.c_int


def _attr_int(dval: float) -> int:
    """Reinterpret a config-attribute double's bytes as the int the SDK stored."""
    u = POAConfigValue()
    u.floatValue = dval
    return int(u.intValue)


class PlayerOneSdk:
    """Typed wrapper over the Player One C API (one process-wide DLL handle)."""

    def __init__(self):
        self._d = _find_dll("PlayerOneCamera.dll")
        if self._d is None:
            raise PlayerOneSdkError(-1, "PlayerOneCamera.dll load (not found/invalid)")
        _declare(self._d)

    def count(self) -> int:
        return int(self._d.POAGetCameraCount())

    def get_properties(self, index: int) -> PoaProperty:
        p = POACameraProperties()
        _check(self._d.POAGetCameraProperties(index, ctypes.byref(p)),
               "POAGetCameraProperties")
        is_color = bool(p.isColorCamera)
        bins = tuple(b for b in p.bins if b) or (1,)
        _, gmax = self._range(p.cameraID, POA_GAIN)
        _, omax = self._range(p.cameraID, POA_OFFSET)
        return PoaProperty(
            camera_id=int(p.cameraID),
            name=p.cameraModelName.decode("ascii", "replace"),
            width=int(p.maxWidth), height=int(p.maxHeight),
            pixel_size_um=float(p.pixelSize), is_color=is_color,
            bayer=_BAYER.get(p.bayerPattern) if is_color else None,
            bit_depth=int(p.bitDepth), is_cooled=bool(p.isHasCooler),
            max_bin=max(bins), max_gain=int(gmax), max_offset=int(omax))

    def _range(self, cam_id: int, config: int) -> tuple[int, int]:
        attr = POAConfigAttributes()
        if self._d.POAGetConfigAttributesByConfigID(
                cam_id, config, ctypes.byref(attr)) != 0:
            return (0, 0)
        return (_attr_int(attr.minValue), _attr_int(attr.maxValue))

    def open(self, cam_id: int) -> None:
        _check(self._d.POAOpenCamera(cam_id), "POAOpenCamera")
        _check(self._d.POAInitCamera(cam_id), "POAInitCamera")

    def close(self, cam_id: int) -> None:
        _check(self._d.POACloseCamera(cam_id), "POACloseCamera")

    # POASetConfig/POAGetConfig: value is c_int (or c_double for temp/egain),
    # passed per-call, matching the vendor binding.
    def _set(self, cam_id: int, config: int, value, is_auto=False) -> None:
        fn = self._d.POASetConfig
        fn.restype = ctypes.c_int
        if config in _FLOAT_CONFIGS:
            fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.c_int]
            _check(fn(cam_id, config, ctypes.c_double(float(value)), int(is_auto)),
                   "POASetConfig")
        else:
            fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            _check(fn(cam_id, config, int(value), int(is_auto)), "POASetConfig")

    def _get(self, cam_id: int, config: int) -> float:
        fn = self._d.POAGetConfig
        fn.restype = ctypes.c_int
        auto = ctypes.c_int()
        if config in _FLOAT_CONFIGS:
            fn.argtypes = [ctypes.c_int, ctypes.c_int,
                           ctypes.POINTER(ctypes.c_double), _PI]
            val = ctypes.c_double()
            _check(fn(cam_id, config, ctypes.byref(val), ctypes.byref(auto)),
                   "POAGetConfig")
        else:
            fn.argtypes = [ctypes.c_int, ctypes.c_int,
                           ctypes.POINTER(ctypes.c_long), _PI]
            val = ctypes.c_long()
            _check(fn(cam_id, config, ctypes.byref(val), ctypes.byref(auto)),
                   "POAGetConfig")
        return float(val.value)

    def set_config(self, cam_id: int, config: int, value, is_auto=False) -> None:
        self._set(cam_id, config, value, is_auto=is_auto)

    def get_config(self, cam_id: int, config: int) -> float:
        return self._get(cam_id, config)

    def get_egain(self, cam_id: int) -> float:
        return self._get(cam_id, POA_EGAIN)

    def set_image_format(self, cam_id: int, w: int, h: int, bin: int, fmt: int) -> None:
        _check(self._d.POASetImageBin(cam_id, bin), "POASetImageBin")
        _check(self._d.POASetImageSize(cam_id, w, h), "POASetImageSize")
        _check(self._d.POASetImageStartPos(cam_id, 0, 0), "POASetImageStartPos")
        _check(self._d.POASetImageFormat(cam_id, fmt), "POASetImageFormat")

    def start_exposure(self, cam_id: int, is_single: bool = True) -> None:
        _check(self._d.POAStartExposure(cam_id, int(is_single)), "POAStartExposure")

    def stop_exposure(self, cam_id: int) -> None:
        _check(self._d.POAStopExposure(cam_id), "POAStopExposure")

    def image_ready(self, cam_id: int) -> bool:
        ready = ctypes.c_int()
        _check(self._d.POAImageReady(cam_id, ctypes.byref(ready)), "POAImageReady")
        return bool(ready.value)

    def get_image_data(self, cam_id: int, nbytes: int, timeout_ms: int = 5000) -> bytes:
        buf = (ctypes.c_ubyte * nbytes)()
        _check(self._d.POAGetImageData(cam_id, ctypes.cast(buf, _PU), nbytes,
                                       timeout_ms), "POAGetImageData")
        return bytes(buf)

    # --- sensor / read modes (LRN) ---------------------------------------
    def sensor_modes(self, cam_id: int) -> list[str]:
        n = ctypes.c_int()
        if self._d.POAGetSensorModeCount(cam_id, ctypes.byref(n)) != 0:
            return []
        out: list[str] = []
        for i in range(n.value):
            info = POASensorModeInfo()
            if self._d.POAGetSensorModeInfo(cam_id, i, ctypes.byref(info)) == 0:
                out.append(info.name.decode("ascii", "replace"))
        return out

    def set_sensor_mode(self, cam_id: int, name_or_index) -> None:
        idx = name_or_index
        if isinstance(name_or_index, str):
            idx = self.sensor_modes(cam_id).index(name_or_index)
        _check(self._d.POASetSensorMode(cam_id, int(idx)), "POASetSensorMode")


#: factory seam — the adapter builds the SDK through this so tests inject fakes.
make_player_one = PlayerOneSdk
