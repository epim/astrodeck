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

_VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "zwo"

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
    # A DLL that cannot be asked where the ROI ended up cannot place a subframe
    # either, and the download would be laid out at a width nothing confirmed.
    # Both have been in ASICamera2 since the first public SDK and both are
    # exported by the vendored build (checked 2026-08-01), so gating on them
    # costs nothing and keeps a crippled DLL from being selected silently.
    "ASISetStartPos", "ASIGetStartPos",
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
    "ASISetStartPos": [_I, _I, _I], "ASIGetStartPos": [_I, _PI, _PI],
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
    """Locate + load the SDK for THIS platform. `basename` stays the Windows
    filename because it keys _DLL_SPECS; the resolver derives the real name."""
    from ..sdk_paths import candidates
    alternatives, exports = _DLL_SPECS[basename]
    stem = basename.rsplit(".", 1)[0]
    for c in candidates("zwo", stem, env_var="ASTRODECK_ZWO_SDK_DIR",
                        extra=alternatives):
        if c.is_file():
            dll = _loads_with_exports(c, exports)
            if dll is not None:
                return dll
    return None


def _find_dll_legacy(basename: str):
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
        # gain/offset ranges live in the CONTROL CAPS, which require an OPEN
        # camera (verified at-scope: pre-open they read 0). The adapter reads
        # them via control_range() after open; 0 here means "not known yet".
        return AsiProperty(
            name=info.Name.decode("ascii", "replace"),
            width=int(info.MaxWidth), height=int(info.MaxHeight),
            pixel_size_um=float(info.PixelSize), is_color=is_color, bayer=bayer,
            bit_depth=int(info.BitDepth), bin_modes=bins or (1,),
            max_gain=0, max_offset=0,
            has_cooler=bool(info.IsCoolerCam), camera_id=int(info.CameraID),
            egain=float(info.ElecPerADU))

    def control_range(self, cam_id: int, ctrl: int) -> tuple[int, int]:
        """Min/max for a control — REQUIRES the camera to be open first."""
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
        try:
            _check(self._d.ASIInitCamera(cam_id), "ASIInitCamera")
        except AsiSdkError:
            # init failed AFTER open took the exclusive USB handle -> close it, or
            # we leak a handle that later masquerades as "held by another app".
            try:
                self._d.ASICloseCamera(cam_id)
            except Exception:  # noqa: BLE001
                pass
            raise

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

    #: WHAT ASISetROIFormat DOES TO A GEOMETRY IT DOES NOT LIKE, read out of the
    #: vendored DLL rather than assumed: it REJECTS. There is no rounding
    #: anywhere in the path, so a ZWO cannot quietly hand back rows shorter than
    #: the ones asked for the way Player One can (PlayerOneSdk.ALIGN_W) — the
    #: two brands are opposite answers and the difference is measured, not
    #: inferred from the fact that the log strings begin "Failed to set".
    #:
    #: astrodeck/vendor/zwo/ASICamera2.dll (2,852,352 bytes, sha256
    #: 0c8778c3cce2012961b079e3c7d0d834...). The export ASISetROIFormat (RVA
    #: 0x4170) checks only the id, the open state and the image type itself —
    #: its own immediates are 4 CAMERA_CLOSED at 0x41f9 and 9 INVALID_IMGTYPE at
    #: 0x4270 — and hands the geometry to a virtual call at 0x43a0
    #: (``ff 50 18``: call [rax+18h]) returning a BOOL. The CALLER turns that
    #: bool into the error code, which is why grepping the export's body for
    #: ``mov eax, 8`` finds nothing and concludes wrongly that the outcome is
    #: unknown:
    #:
    #:     0x43c0   45 84 db            test   r11b, r11b   ; the validator's bool
    #:     0x43c3   bb 08 00 00 00      mov    ebx, 8       ; ASI_ERROR_INVALID_SIZE
    #:     0x43c8   41 0f 45 de         cmovne ebx, r14d    ; ...only if it passed
    #:     0x43e7   8b c3               mov    eax, ebx     ; and that is the return
    #:
    #: The validator is the function at 0x63860. After the bin-table and bounds
    #: checks it applies THREE alignment tests, each a ``jne`` straight to the
    #: function's single ``xor al,al`` at 0x6389c — and there is not one
    #: mask-and-store anywhere in it:
    #:
    #:     0x638dc   (height * bin) % 2 != 0  -> false, silently
    #:     0x638e6   (width  * bin) % 8 != 0  -> false, silently
    #:     0x638f6    height        % 8 != 0  -> false, after logging
    #:               "Failed to set height: %d, the height must be multiple of 8"
    #:
    #: NOTE WHICH AXIS CARRIES WHICH RULE, and that the third is on the BINNED
    #: height while the first two are on the unbinned dimensions. The third is
    #: the one that bites: any sensor whose row count is not a multiple of 8
    #: after binning is refused outright. The only other alignment string in the
    #: DLL ("the width must be multiple of 24, height must be multiple of 4")
    #: is guarded by hardware bin, which AstroDeck never enables.
    ALIGN_W_UNBINNED, ALIGN_H_UNBINNED, ALIGN_H_BINNED = 8, 2, 8

    @classmethod
    def rejects_roi(cls, w: int, h: int, bin: int) -> bool:
        """Whether the vendored DLL will refuse this (BINNED w, h, bin) with
        ASI_ERROR_INVALID_SIZE, by the three rules cited above. Exposed so the
        arithmetic lives beside its citation instead of being redone by hand in
        a comment — which is how the ASI220MM came to be described as safe at
        every bin when it is refused at two of them."""
        return (h * bin % cls.ALIGN_H_UNBINNED != 0
                or w * bin % cls.ALIGN_W_UNBINNED != 0
                or h % cls.ALIGN_H_BINNED != 0)

    def set_roi(self, cam_id: int, w: int, h: int, bin: int, img_type: int) -> None:
        _check(self._d.ASISetROIFormat(cam_id, w, h, bin, img_type),
               "ASISetROIFormat")

    def get_roi(self, cam_id: int) -> tuple[int, int, int, int]:
        """(width, height, bin, img_type) the camera is ACTUALLY set to, in
        binned pixels. This is the only authority on the download's row length:
        the values passed to set_roi are a request, and nothing in the SDK
        promises they come back untouched. On the vendored build a violation is
        refused rather than rounded (see ALIGN_H_BINNED above), so here the
        read-back is a net under the NEXT ZWO build rather than a live catch —
        but "refused" is itself only true of the DLL that was read."""
        w, h, b, t = (ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int())
        _check(self._d.ASIGetROIFormat(cam_id, ctypes.byref(w), ctypes.byref(h),
                                       ctypes.byref(b), ctypes.byref(t)),
               "ASIGetROIFormat")
        return int(w.value), int(h.value), int(b.value), int(t.value)

    def set_start_pos(self, cam_id: int, x: int, y: int) -> None:
        """Place the subframe's top-left corner (binned pixels). MUST run after
        set_roi: ASISetROIFormat re-centres the ROI on the sensor, so a caller
        that only sets the format gets a frame from wherever the SDK put it and
        is told nothing about it."""
        _check(self._d.ASISetStartPos(cam_id, x, y), "ASISetStartPos")

    def get_start_pos(self, cam_id: int) -> tuple[int, int]:
        x, y = ctypes.c_int(), ctypes.c_int()
        _check(self._d.ASIGetStartPos(cam_id, ctypes.byref(x), ctypes.byref(y)),
               "ASIGetStartPos")
        return int(x.value), int(y.value)

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
