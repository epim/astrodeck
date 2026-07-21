"""ctypes bindings for the Player One Camera SDK (Poseidon-M Pro imaging camera).

!!! UNVERIFIED CTYPES INTERNALS — complete against the real ``PlayerOneCamera.h``
(Camera SDK V3.10.1, Windows) on a box that has the SDK. The Player One SDK is
NOT installed on the dev box, so unlike the ASI bindings these export names,
struct field layouts, enum integer values, and the POAConfigValue union COULD
NOT be load-verified here. The method INTERFACE below is stable (the adapter and
its tests depend only on it); the C-binding details are what need the on-box
pass. The module imports + declares WITHOUT loading a DLL (load happens in
PlayerOneSdk.__init__), so it is importable + unit-testable DLL-less.

Loader search order: ``ASTRODECK_PLAYERONE_SDK_DIR`` env → vendored
``astrodeck/vendor/playerone/`` → known installs. Bundling of the DLL is gated
on the licensing verdict (docs/hardware/player-one-sdk-licensing.md).

LRN (Low Read Noise) sampling mode is exposed through the SDK's SENSOR MODE API
(POAGetSensorModeCount / POAGetSensorModeInfo / POASetSensorMode) — verify these
exist and their exact names/signatures against the header (older SDKs may expose
it differently).
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import os

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "playerone"

# --- POAConfig ids (VERIFY exact enum order against PlayerOneCamera.h) --------
POA_EXPOSURE = 0          # microseconds
POA_GAIN = 1
POA_HARDWARE_BIN = 2
POA_TEMPERATURE = 3       # read-only sensor temp (deg C, float)
POA_OFFSET = 7
POA_EGAIN = 15           # e-/ADU (read-only, float)
POA_COOLER_POWER = 16     # read-only
POA_TARGET_TEMP = 17
POA_COOLER = 18           # on/off (bool)
POA_HEATER_POWER = 20     # dew heater (0..100)
# POAImgFormat
POA_RAW8 = 0
POA_RAW16 = 1             # (VERIFY: some headers order RAW8, RGB24, RAW16, MONO8)

ERROR_NAMES: dict[int, str] = {
    0: "OK", 1: "INVALID_INDEX", 2: "INVALID_ID", 3: "INVALID_CONFIG",
    4: "INVALID_ARGU", 5: "NOT_OPENED", 6: "DEVICE_NOT_FOUND", 7: "OUT_OF_LIMIT",
    8: "EXPOSURE_FAILED", 9: "TIMEOUT", 10: "SIZE_LESS", 11: "EXPOSING",
    12: "POINTER", 13: "CONF_CANNOT_WRITE", 14: "CONF_CANNOT_READ",
    15: "ACCESS_DENIED", 16: "OPERATION_FAILED", 17: "MEMORY_FAILED",
}

_EXPORTS = [
    "POAGetCameraCount", "POAGetCameraProperties", "POAOpenCamera",
    "POAInitCamera", "POACloseCamera", "POASetConfig", "POAGetConfig",
    "POASetImageSize", "POASetImageBin", "POASetImageFormat", "POAStartExposure",
    "POAStopExposure", "POAImageReady", "POAGetImageData",
    "POAGetSensorModeCount", "POAGetSensorModeInfo", "POASetSensorMode",
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
    """union { long intValue; double floatValue; int boolValue; }"""
    _fields_ = [("intValue", ctypes.c_long), ("floatValue", ctypes.c_double),
                ("boolValue", ctypes.c_int)]


class POACameraProperties(ctypes.Structure):
    """VERIFY the FULL layout against PlayerOneCamera.h — a partial/misordered
    struct misreads every field. Best-effort layout for V3.10.x below."""
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


class POASensorModeInfo(ctypes.Structure):
    """VERIFY against header — name + description strings for a sensor mode."""
    _fields_ = [("name", ctypes.c_char * 64),
                ("desc", ctypes.c_char * 128)]


_BAYER = {0: "RG", 1: "BG", 2: "GR", 3: "GB"}


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

#: fn -> argtypes; every bound fn returns c_int (POAErrors) except
#: POAGetCameraCount (returns the count as c_int).
_SIGNATURES: dict[str, list] = {
    "POAGetCameraCount": [],
    "POAGetCameraProperties": [_I, ctypes.POINTER(POACameraProperties)],
    "POAOpenCamera": [_I], "POAInitCamera": [_I], "POACloseCamera": [_I],
    "POASetConfig": [_I, _I, POAConfigValue, _I],
    "POAGetConfig": [_I, _I, ctypes.POINTER(POAConfigValue), _PI],
    "POASetImageSize": [_I, _I, _I], "POASetImageBin": [_I, _I],
    "POASetImageFormat": [_I, _I],
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
        gmin, gmax = self._range(p.cameraID, POA_GAIN)
        omin, omax = self._range(p.cameraID, POA_OFFSET)
        return PoaProperty(
            camera_id=int(p.cameraID),
            name=p.cameraModelName.decode("ascii", "replace"),
            width=int(p.maxWidth), height=int(p.maxHeight),
            pixel_size_um=float(p.pixelSize), is_color=is_color,
            bayer=_BAYER.get(p.bayerPattern) if is_color else None,
            bit_depth=int(p.bitDepth), is_cooled=bool(p.isHasCooler),
            max_bin=max(bins), max_gain=int(gmax), max_offset=int(omax))

    def _range(self, cam_id: int, config: int) -> tuple[int, int]:
        # POAGetConfigValueRange exists in the header; kept simple here — the
        # adapter only needs the max. VERIFY the range API when wiring on-box.
        return (0, 0)

    def open(self, cam_id: int) -> None:
        _check(self._d.POAOpenCamera(cam_id), "POAOpenCamera")
        _check(self._d.POAInitCamera(cam_id), "POAInitCamera")

    def close(self, cam_id: int) -> None:
        _check(self._d.POACloseCamera(cam_id), "POACloseCamera")

    def _set(self, cam_id: int, config: int, value, is_float=False, is_bool=False,
             is_auto=False) -> None:
        v = POAConfigValue()
        if is_bool:
            v.boolValue = int(bool(value))
        elif is_float:
            v.floatValue = float(value)
        else:
            v.intValue = int(value)
        _check(self._d.POASetConfig(cam_id, config, v, int(is_auto)), "POASetConfig")

    def _get(self, cam_id: int, config: int, is_float=False) -> float:
        v = POAConfigValue()
        auto = ctypes.c_int()
        _check(self._d.POAGetConfig(cam_id, config, ctypes.byref(v),
                                    ctypes.byref(auto)), "POAGetConfig")
        return float(v.floatValue) if is_float else float(v.intValue)

    def set_config(self, cam_id: int, config: int, value, is_auto=False) -> None:
        self._set(cam_id, config, value, is_auto=is_auto)

    def get_config(self, cam_id: int, config: int) -> float:
        return self._get(cam_id, config)

    def get_egain(self, cam_id: int) -> float:
        return self._get(cam_id, POA_EGAIN, is_float=True)

    def set_image_format(self, cam_id: int, w: int, h: int, bin: int, fmt: int) -> None:
        _check(self._d.POASetImageBin(cam_id, bin), "POASetImageBin")
        _check(self._d.POASetImageSize(cam_id, w, h), "POASetImageSize")
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
            modes = self.sensor_modes(cam_id)
            idx = modes.index(name_or_index)
        _check(self._d.POASetSensorMode(cam_id, int(idx)), "POASetSensorMode")


#: factory seam — the adapter builds the SDK through this so tests inject fakes.
make_player_one = PlayerOneSdk
