"""ctypes bindings for the ZWO ASICamera2 SDK (native guide/imaging camera).

Same MIT-licensed ``libasi`` family already vendored for CAA/EAF
(``vendor/zwo/LICENSE.txt``); the C headers are public (indi-3rdparty
``libasi/ASICamera2.h``). This module owns DLL location + typed bindings ONLY —
no device logic, no asyncio (the adapter wraps calls; the engine serializes).

Search order for the DLL: ``ASTRODECK_ZWO_SDK_DIR`` env override → the vendored
``astrodeck/vendor/zwo/`` dir → known installs. A candidate counts only if it
LOADS and exports the API.

!!! ON-BOX VERIFICATION REQUIRED before hardware use: confirm every export name
and struct field/order below against the exact ``ASICamera2.h`` shipped with the
DLL you vendor. ``long`` is bound as ``c_long`` (32-bit on Windows, 64-bit on
64-bit Linux) which MATCHES the C ``long`` on each platform. The module imports
and declares signatures WITHOUT loading a DLL (load happens in AsiSdk.__init__),
so it is importable + unit-testable on a box with no SDK present.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from pathlib import Path
import os

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "zwo"

# ASI_CONTROL_TYPE ids used by the adapter (ASICamera2.h enum order).
ASI_GAIN = 0
ASI_EXPOSURE = 1          # microseconds
ASI_OFFSET = 5            # a.k.a. BRIGHTNESS
ASI_TEMPERATURE = 8       # tenths of a degree C
# ASI_IMG_TYPE
ASI_IMG_RAW8 = 0
ASI_IMG_RAW16 = 2
# ASI_EXPOSURE_STATUS
ASI_EXP_IDLE, ASI_EXP_WORKING, ASI_EXP_SUCCESS, ASI_EXP_FAILED = 0, 1, 2, 3

ERROR_NAMES: dict[int, str] = {
    0: "SUCCESS", 1: "INVALID_INDEX", 2: "INVALID_ID", 3: "INVALID_CONTROL_TYPE",
    4: "CAMERA_CLOSED", 5: "CAMERA_REMOVED", 6: "INVALID_PATH",
    7: "INVALID_FILEFORMAT", 8: "INVALID_SIZE", 9: "INVALID_IMGTYPE",
    10: "OUTOF_BOUNDARY", 11: "TIMEOUT", 12: "INVALID_SEQUENCE",
    13: "BUFFER_TOO_SMALL", 14: "VIDEO_MODE_ACTIVE", 15: "EXPOSURE_IN_PROGRESS",
    16: "GENERAL_ERROR", 17: "INVALID_MODE",
}

_EXPORTS = [
    "ASIGetNumOfConnectedCameras", "ASIGetCameraProperty", "ASIOpenCamera",
    "ASIInitCamera", "ASICloseCamera", "ASIGetNumOfControls",
    "ASIGetControlCaps", "ASISetControlValue", "ASIGetControlValue",
    "ASISetROIFormat", "ASIGetROIFormat", "ASIStartExposure", "ASIStopExposure",
    "ASIGetExpStatus", "ASIGetDataAfterExp",
]

#: canonical basename -> (known install paths, verifying exports).
_DLL_SPECS: dict[str, tuple[list[str], list[str]]] = {
    "ASICamera2.dll": (
        [r"C:\Program Files\ASIStudio\ASICamera2.dll",
         r"C:\Program Files\ZWO\ASICamera2.dll"],
        _EXPORTS,
    ),
}


class AsiSdkError(Exception):
    """A non-zero ASI_ERROR_CODE from an SDK call (or a load failure)."""

    def __init__(self, code: int, fn: str):
        name = ERROR_NAMES.get(code, "?")
        super().__init__(f"ASI SDK {fn} failed ({name}, code {code})")
        self.code = code
        self.fn = fn
        self.code_name = name


class ASI_CAMERA_INFO(ctypes.Structure):
    _fields_ = [
        ("Name", ctypes.c_char * 64),
        ("CameraID", ctypes.c_int),
        ("MaxHeight", ctypes.c_long),
        ("MaxWidth", ctypes.c_long),
        ("IsColorCam", ctypes.c_int),
        ("BayerPattern", ctypes.c_int),
        ("SupportedBins", ctypes.c_int * 16),
        ("SupportedVideoFormat", ctypes.c_int * 8),
        ("PixelSize", ctypes.c_double),
        ("MechanicalShutter", ctypes.c_int),
        ("ST4Port", ctypes.c_int),
        ("IsCoolerCam", ctypes.c_int),
        ("IsUSB3Host", ctypes.c_int),
        ("IsUSB3Camera", ctypes.c_int),
        ("ElecPerADU", ctypes.c_float),
        ("BitDepth", ctypes.c_int),
        ("IsTriggerCam", ctypes.c_int),
        ("Unused", ctypes.c_char * 16),
    ]


class ASI_CONTROL_CAPS(ctypes.Structure):
    _fields_ = [
        ("Name", ctypes.c_char * 64),
        ("Description", ctypes.c_char * 128),
        ("MaxValue", ctypes.c_long),
        ("MinValue", ctypes.c_long),
        ("DefaultValue", ctypes.c_long),
        ("IsAutoSupported", ctypes.c_int),
        ("IsWritable", ctypes.c_int),
        ("ControlType", ctypes.c_int),
        ("Unused", ctypes.c_char * 32),
    ]


_BAYER = {0: "RG", 1: "BG", 2: "GR", 3: "GB"}  # ASI_BAYER_PATTERN -> 2-char top-left


@dataclass
class AsiProperty:
    """The subset of ASI_CAMERA_INFO (+ control ranges) the adapter needs."""
    name: str
    width: int
    height: int
    pixel_size_um: float
    is_color: bool
    bayer: str | None
    bit_depth: int
    bin_modes: tuple[int, ...]
    max_gain: int
    max_offset: int
    has_cooler: bool
    camera_id: int = 0
    egain: float = 0.0


_I, _PI = ctypes.c_int, ctypes.POINTER(ctypes.c_int)
_PL = ctypes.POINTER(ctypes.c_long)
_PU = ctypes.POINTER(ctypes.c_ubyte)

#: fn name -> argtypes; every bound fn returns c_int (ASI_ERROR_CODE), except
#: ASIGetNumOfConnectedCameras which returns the count directly (c_int too).
_SIGNATURES: dict[str, list] = {
    "ASIGetNumOfConnectedCameras": [],
    "ASIGetCameraProperty": [ctypes.POINTER(ASI_CAMERA_INFO), _I],
    "ASIOpenCamera": [_I], "ASIInitCamera": [_I], "ASICloseCamera": [_I],
    "ASIGetNumOfControls": [_I, _PI],
    "ASIGetControlCaps": [_I, _I, ctypes.POINTER(ASI_CONTROL_CAPS)],
    "ASISetControlValue": [_I, _I, ctypes.c_long, _I],
    "ASIGetControlValue": [_I, _I, _PL, _PI],
    "ASISetROIFormat": [_I, _I, _I, _I, _I],
    "ASIGetROIFormat": [_I, _PI, _PI, _PI, _PI],
    "ASIStartExposure": [_I, _I], "ASIStopExposure": [_I],
    "ASIGetExpStatus": [_I, _PI],
    "ASIGetDataAfterExp": [_I, _PU, ctypes.c_long],
}


def _loads_with_exports(path: Path, exports: list[str]):
    """Return the loaded DLL when it loads AND exports everything, else None.
    ``CDLL`` (cdecl, Linux-safe) — a non-Windows host fails the load with OSError
    and degrades to None rather than AttributeError-ing on WinDLL."""
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
    env = os.environ.get("ASTRODECK_ZWO_SDK_DIR")
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
        raise AsiSdkError(code, fn)


def _declare(dll) -> None:
    for fn_name, argtypes in _SIGNATURES.items():
        fn = getattr(dll, fn_name, None)
        if fn is not None:
            fn.argtypes = argtypes
            fn.restype = ctypes.c_int


class AsiSdk:
    """Typed wrapper over the ASICamera2 C API (one process-wide DLL handle)."""

    def __init__(self):
        self._d = _find_dll("ASICamera2.dll")
        if self._d is None:
            raise AsiSdkError(-1, "ASICamera2.dll load (not found/invalid)")
        _declare(self._d)

    def count(self) -> int:
        return int(self._d.ASIGetNumOfConnectedCameras())

    def get_property(self, index: int) -> AsiProperty:
        info = ASI_CAMERA_INFO()
        _check(self._d.ASIGetCameraProperty(ctypes.byref(info), index),
               "ASIGetCameraProperty")
        bins = tuple(b for b in info.SupportedBins if b)
        is_color = bool(info.IsColorCam)
        bayer = _BAYER.get(info.BayerPattern) if is_color else None
        gmin, gmax = self._control_range(info.CameraID, ASI_GAIN)
        omin, omax = self._control_range(info.CameraID, ASI_OFFSET)
        return AsiProperty(
            name=info.Name.decode("ascii", "replace"),
            width=int(info.MaxWidth), height=int(info.MaxHeight),
            pixel_size_um=float(info.PixelSize), is_color=is_color, bayer=bayer,
            bit_depth=int(info.BitDepth), bin_modes=bins or (1,),
            max_gain=int(gmax), max_offset=int(omax),
            has_cooler=bool(info.IsCoolerCam), camera_id=int(info.CameraID),
            egain=float(info.ElecPerADU))

    def _control_range(self, cam_id: int, ctrl: int) -> tuple[int, int]:
        n = ctypes.c_int()
        if self._d.ASIGetNumOfControls(cam_id, ctypes.byref(n)) != 0:
            return (0, 0)
        caps = ASI_CONTROL_CAPS()
        for i in range(n.value):
            if self._d.ASIGetControlCaps(cam_id, i, ctypes.byref(caps)) == 0 \
                    and caps.ControlType == ctrl:
                return (int(caps.MinValue), int(caps.MaxValue))
        return (0, 0)

    def open(self, cam_id: int) -> None:
        _check(self._d.ASIOpenCamera(cam_id), "ASIOpenCamera")
        _check(self._d.ASIInitCamera(cam_id), "ASIInitCamera")

    def close(self, cam_id: int) -> None:
        _check(self._d.ASICloseCamera(cam_id), "ASICloseCamera")

    def set_control(self, cam_id: int, ctrl: int, value: int, auto: bool = False) -> None:
        _check(self._d.ASISetControlValue(cam_id, ctrl, int(value), int(auto)),
               "ASISetControlValue")

    def get_control(self, cam_id: int, ctrl: int) -> int:
        val, auto = ctypes.c_long(), ctypes.c_int()
        _check(self._d.ASIGetControlValue(cam_id, ctrl, ctypes.byref(val),
                                          ctypes.byref(auto)), "ASIGetControlValue")
        return int(val.value)

    def set_roi(self, cam_id: int, w: int, h: int, bin: int, img_type: int) -> None:
        _check(self._d.ASISetROIFormat(cam_id, w, h, bin, img_type),
               "ASISetROIFormat")

    def start_exposure(self, cam_id: int, dark: bool) -> None:
        _check(self._d.ASIStartExposure(cam_id, int(dark)), "ASIStartExposure")

    def stop_exposure(self, cam_id: int) -> None:
        _check(self._d.ASIStopExposure(cam_id), "ASIStopExposure")

    def exp_status(self, cam_id: int) -> int:
        st = ctypes.c_int()
        _check(self._d.ASIGetExpStatus(cam_id, ctypes.byref(st)), "ASIGetExpStatus")
        return int(st.value)

    def get_data(self, cam_id: int, nbytes: int) -> bytes:
        buf = (ctypes.c_ubyte * nbytes)()
        _check(self._d.ASIGetDataAfterExp(
            cam_id, ctypes.cast(buf, _PU), nbytes), "ASIGetDataAfterExp")
        return bytes(buf)


#: factory seam — the adapter/backend build the SDK through this so tests inject
#: fakes without touching real DLLs.
make_asi = AsiSdk
