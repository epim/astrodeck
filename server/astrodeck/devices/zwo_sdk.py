"""ctypes bindings for the ZWO EAF/CAA SDKs (native accessory drivers).

The SDKs are closed binaries under ZWO's MIT-style license (bundleable —
``vendor/zwo/LICENSE.txt``); the C headers are public (indi-3rdparty
``libasi``). This module owns DLL location + typed bindings ONLY — no device
logic, no asyncio (callers wrap in ``asyncio.to_thread`` under a lock; the
SDKs are not documented thread-safe).

Search order for a DLL: ``ASTRODECK_ZWO_SDK_DIR`` env override → the vendored
``astrodeck/vendor/zwo/`` dir → known vendor installs. A candidate only counts
if it LOADS and exports the API (filenames lie: ZWO's ``CAA_ASCOM_x64.dll``
exports the full CAA SDK while ``CAA_SRC.dll`` lacks the mechanical API).
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "zwo"

#: canonical basename -> (known alternative install paths, verifying exports).
#: The verify list covers EVERY symbol the bindings call (review C-minor 3), so
#: a partial DLL is rejected at load, not at connect.
_DLL_SPECS: dict[str, tuple[list[str], list[str]]] = {
    "EAF_focuser.dll": (
        [r"C:\Program Files\ASIStudio\EAF_focuser.dll"],
        ["EAFGetNum", "EAFGetID", "EAFOpen", "EAFClose", "EAFGetProperty",
         "EAFMove", "EAFStop", "EAFIsMoving", "EAFGetPosition", "EAFGetTemp",
         "EAFGetFirmwareVersion"],
    ),
    "CAARotator.dll": (
        [r"C:\Program Files (x86)\Common Files\ASCOM\ZWO\CAA_ASCOM_x64.dll",
         r"C:\Program Files\ASIStudio\CAA_SRC.dll"],
        ["CAAGetNum", "CAAGetID", "CAAOpen", "CAAClose", "CAAGetProperty",
         "CAAMoveToMechanical", "CAAGetDegree", "CAAStop", "CAAIsMoving",
         "CAAGetTemp", "CAAGetReverse", "CAASetReverse", "CAAGetType",
         "CAAGetFirmwareVersion"],
    ),
}

#: Named error codes (CAA_API.h / EAF_focuser.h enum order) so a cable-wrap
#: stall reads "STALL", not "code 12" (review C-minor 2). Shared enum family.
ERROR_NAMES: dict[int, str] = {
    0: "SUCCESS", 1: "INVALID_INDEX", 2: "INVALID_ID", 3: "INVALID_VALUE",
    4: "REMOVED", 5: "MOVING", 6: "ERROR_STATE", 7: "GENERAL_ERROR",
    8: "NOT_SUPPORTED", 9: "CLOSED", 10: "OUT_RANGE", 11: "OVER_LIMIT",
    12: "STALL", 13: "TIMEOUT", 14: "INVALID_LENGTH",
}


class ZwoSdkError(Exception):
    """A non-zero ``*_ERROR_CODE`` from an SDK call (or a load failure)."""

    def __init__(self, code: int, fn: str):
        name = ERROR_NAMES.get(code, "?")
        super().__init__(f"ZWO SDK {fn} failed ({name}, code {code})")
        self.code = code
        self.fn = fn
        self.code_name = name


class _Info(ctypes.Structure):
    """EAF_INFO / CAA_INFO share the layout {int ID; char Name[64]; int MaxStep}."""

    _fields_ = [("ID", ctypes.c_int), ("Name", ctypes.c_char * 64),
                ("MaxStep", ctypes.c_int)]


class _Type16(ctypes.Structure):
    """CAA_TYPE {char type[16]}."""

    _fields_ = [("type", ctypes.c_char * 16)]


class _EafErrorMsg(ctypes.Structure):
    """EAF_ERROR_MSG {char motor_error_code[3]; char battery_error_code[3];}.

    Two TWO-CHARACTER codes, each NUL-terminated — not integers. Verified
    against EAF_focuser.h (indi-3rdparty/libasi), because guessing a ctypes
    layout for a native struct is how you corrupt a stack on someone's rig.
    """

    _fields_ = [("motor_error_code", ctypes.c_char * 3),
                ("battery_error_code", ctypes.c_char * 3)]


def _loads_with_exports(path: Path, exports: list[str]):
    """Return the loaded DLL when it loads AND exports everything, else None.

    ``CDLL``, not ``WinDLL``: the headers are plain ``__cdecl`` (identical to
    stdcall on x64 anyway), and CDLL exists on every platform — a Linux host
    simply fails the load with OSError and degrades to None instead of
    AttributeError-ing on a missing WinDLL (review C-critical)."""
    from .vendor_verify import verify_if_vendored
    # OPEN-007: refuse a tampered/swapped BUNDLED binary before it executes
    # in-process; a user's own installed SDK is not hash-pinned.
    verify_if_vendored(path)
    try:
        dll = ctypes.CDLL(str(path))
    except OSError:
        return None
    if all(hasattr(dll, e) for e in exports):
        return dll
    return None


def _preload_libudev() -> None:
    """Put libudev's symbols in the process before a ZWO accessory library loads.

    ZWO's Linux ``libCAARotator.so`` calls ``udev_device_get_devnode`` but does
    NOT list libudev in its DT_NEEDED, so ``ldd`` reports every dependency
    satisfied and ``dlopen`` then fails with ``undefined symbol``. Loading
    libudev RTLD_GLOBAL first satisfies it. Verified on an RK3588S: without this
    the rotator is simply absent; with it, ``CAAGetNum`` resolves.

    Best-effort and silent -- on Windows and macOS there is nothing to do, and a
    missing libudev must not stop the camera library from loading."""
    if not sys.platform.startswith("linux"):
        return
    for soname in ("libudev.so.1", "libudev.so.0", "libudev.so"):
        try:
            ctypes.CDLL(soname, mode=ctypes.RTLD_GLOBAL)
            return
        except OSError:
            continue


def _find_dll(basename: str):
    """Locate + load the SDK for THIS platform. `basename` stays the Windows
    filename because it keys _DLL_SPECS; the resolver derives the real name."""
    from .sdk_paths import candidates
    _preload_libudev()
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
    """Locate + load the named SDK DLL per the search order; None if absent."""
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
        raise ZwoSdkError(code, fn)


_I, _PI = ctypes.c_int, ctypes.POINTER(ctypes.c_int)
_PF, _PB = ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_bool)
_PU = ctypes.POINTER(ctypes.c_ubyte)

#: fn name -> argtypes. restype stays c_int (every bound fn returns an error
#: code or a count). Declared per spec §1 (review C-minor 1): defensive against
#: a future raw-scalar miscall silently crossing the ABI.
_SIGNATURES: dict[str, list] = {
    "EAFGetNum": [], "EAFGetID": [_I, _PI], "EAFOpen": [_I], "EAFClose": [_I],
    "EAFGetProperty": [_I, ctypes.POINTER(_Info)], "EAFMove": [_I, _I],
    "EAFStop": [_I], "EAFIsMoving": [_I, _PB, _PB],
    "EAFGetPosition": [_I, _PI], "EAFGetTemp": [_I, _PF],
    "EAFGetFirmwareVersion": [_I, _PU, _PU, _PU],
    # OPTIONAL extras — bound when the DLL has them, never required.
    # EAFGetMaxStep is the firmware's STORED travel limit and is the number the
    # device actually enforces; EAF_INFO.MaxStep is only the hardware ceiling.
    # On 2026-07-31 those read 360 and 600000, and because only the latter was
    # bound, every move above 360 was silently clamped while the driver believed
    # it had 600000 steps. Deliberately NOT added to the required-export list:
    # an older or macOS SDK missing one would fail the whole load and the
    # focuser would disappear entirely, which is worse than the bug it fixes.
    "EAFGetMaxStep": [_I, _PI],
    "EAFSetMaxStep": [_I, _I],
    "EAFStepRange": [_I, _PI],
    # EAFResetPostion (ZWO's spelling) DECLARES the current position — it moves
    # nothing. This is the safe way to re-anchor a focuser whose counter has
    # been lost, as opposed to driving the drawtube into a mechanical stop to
    # find one: the EAF is open-loop with no limit switches, so a hard stop is
    # not something it can be relied on to notice.
    "EAFResetPostion": [_I, _I],
    # What the device says when something went wrong. `motor_error_code` is the
    # one worth reading after a refused move. Older firmware answers
    # NOT_SUPPORTED — hence optional, like the rest of this block.
    "EAFGetErrorCode": [_I, ctypes.POINTER(_EafErrorMsg)],
    # NOT a stall reason: this is the POWER-OFF reason (0 normal, 1 shipping
    # mode). It earns its place because the EAF losing power is exactly what
    # wipes its position counter and its travel limit.
    "EAFGetReason": [_I, _PI],
    "CAAGetNum": [], "CAAGetID": [_I, _PI], "CAAOpen": [_I], "CAAClose": [_I],
    "CAAGetProperty": [_I, ctypes.POINTER(_Info)],
    "CAAMoveToMechanical": [_I, ctypes.c_float], "CAAGetDegree": [_I, _PF],
    "CAAStop": [_I], "CAAIsMoving": [_I, _PB, _PB], "CAAGetTemp": [_I, _PF],
    "CAAGetReverse": [_I, _PB], "CAASetReverse": [_I, ctypes.c_bool],
    "CAAGetType": [_I, ctypes.POINTER(_Type16)],
    "CAAGetFirmwareVersion": [_I, _PU, _PU, _PU],
}


def _declare(dll) -> None:
    """Stamp argtypes/restype on every bound export the DLL carries."""
    for fn_name, argtypes in _SIGNATURES.items():
        fn = getattr(dll, fn_name, None)
        if fn is not None:
            fn.argtypes = argtypes
            fn.restype = ctypes.c_int


class EafSdk:
    """Typed wrapper over the EAF C API (one process-wide DLL handle)."""

    def __init__(self):
        self._d = _find_dll("EAF_focuser.dll")
        if self._d is None:
            raise ZwoSdkError(-1, "EAF_focuser.dll load (not found/invalid)")
        _declare(self._d)

    def count(self) -> int:
        return int(self._d.EAFGetNum())

    def get_id(self, index: int) -> int:
        v = ctypes.c_int()
        _check(self._d.EAFGetID(index, ctypes.byref(v)), "EAFGetID")
        return v.value

    def open(self, dev_id: int) -> None:
        _check(self._d.EAFOpen(dev_id), "EAFOpen")

    def close(self, dev_id: int) -> None:
        _check(self._d.EAFClose(dev_id), "EAFClose")

    def get_property(self, dev_id: int) -> tuple[str, int]:
        info = _Info()
        _check(self._d.EAFGetProperty(dev_id, ctypes.byref(info)),
               "EAFGetProperty")
        return info.Name.decode("ascii", "replace"), int(info.MaxStep)

    def move(self, dev_id: int, step: int) -> None:
        _check(self._d.EAFMove(dev_id, int(step)), "EAFMove")

    def stop(self, dev_id: int) -> None:
        _check(self._d.EAFStop(dev_id), "EAFStop")

    def is_moving(self, dev_id: int) -> tuple[bool, bool]:
        moving, hand = ctypes.c_bool(), ctypes.c_bool()
        _check(self._d.EAFIsMoving(dev_id, ctypes.byref(moving),
                                   ctypes.byref(hand)), "EAFIsMoving")
        return moving.value, hand.value

    def get_position(self, dev_id: int) -> int:
        v = ctypes.c_int()
        _check(self._d.EAFGetPosition(dev_id, ctypes.byref(v)),
               "EAFGetPosition")
        return v.value

    def get_temp(self, dev_id: int) -> float:
        v = ctypes.c_float()
        _check(self._d.EAFGetTemp(dev_id, ctypes.byref(v)), "EAFGetTemp")
        return float(v.value)

    def firmware(self, dev_id: int) -> str:
        a, b, c = ctypes.c_ubyte(), ctypes.c_ubyte(), ctypes.c_ubyte()
        _check(self._d.EAFGetFirmwareVersion(
            dev_id, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c)),
            "EAFGetFirmwareVersion")
        return f"{a.value}.{b.value}.{c.value}"

    def get_max_step(self, dev_id: int) -> int | None:
        """The firmware's STORED travel limit — the one it enforces.

        None when the SDK does not export it, so a caller can fall back to
        EAF_INFO.MaxStep rather than losing the focuser."""
        fn = getattr(self._d, "EAFGetMaxStep", None)
        if fn is None:
            return None
        v = ctypes.c_int()
        _check(fn(dev_id, ctypes.byref(v)), "EAFGetMaxStep")
        return int(v.value)

    def set_max_step(self, dev_id: int, value: int) -> None:
        """Write the stored travel limit. Does NOT reliably survive the device
        losing power — see EafFocuser.connect, which re-applies it every time."""
        fn = getattr(self._d, "EAFSetMaxStep", None)
        if fn is None:
            raise ZwoSdkError(-1, "EAFSetMaxStep (not exported)")
        _check(fn(dev_id, int(value)), "EAFSetMaxStep")

    def reset_position(self, dev_id: int, value: int) -> None:
        """DECLARE the current position to be ``value``. Moves nothing.

        The re-anchoring primitive: park the drawtube somewhere you know by
        hand, then tell the driver where it is. ZWO spells it ``ResetPostion``.
        """
        fn = getattr(self._d, "EAFResetPostion", None)
        if fn is None:
            raise ZwoSdkError(-1, "EAFResetPostion (not exported)")
        _check(fn(dev_id, int(value)), "EAFResetPostion")

    def error_codes(self, dev_id: int) -> tuple[str, str] | None:
        """``(motor, battery)`` two-character device error codes.

        None when this SDK/firmware does not answer — several EAF firmwares
        return NOT_SUPPORTED, and a focuser that cannot self-report is not an
        error, just a quieter one.
        """
        fn = getattr(self._d, "EAFGetErrorCode", None)
        if fn is None:
            return None
        msg = _EafErrorMsg()
        try:
            _check(fn(dev_id, ctypes.byref(msg)), "EAFGetErrorCode")
        except ZwoSdkError:
            return None
        return (msg.motor_error_code.decode("ascii", "replace").strip(),
                msg.battery_error_code.decode("ascii", "replace").strip())

    def power_off_reason(self, dev_id: int) -> int | None:
        """Why the device last powered down: 0 normal, 1 shipping mode.

        None when unsupported. Worth reading at connect — the EAF losing power
        is what wipes its position counter and its stored travel limit."""
        fn = getattr(self._d, "EAFGetReason", None)
        if fn is None:
            return None
        v = ctypes.c_int()
        try:
            _check(fn(dev_id, ctypes.byref(v)), "EAFGetReason")
        except ZwoSdkError:
            return None
        return int(v.value)


class CaaSdk:
    """Typed wrapper over the CAA C API (one process-wide DLL handle)."""

    def __init__(self):
        self._d = _find_dll("CAARotator.dll")
        if self._d is None:
            raise ZwoSdkError(-1, "CAARotator.dll load (not found/invalid)")
        _declare(self._d)

    def count(self) -> int:
        return int(self._d.CAAGetNum())

    def get_id(self, index: int) -> int:
        v = ctypes.c_int()
        _check(self._d.CAAGetID(index, ctypes.byref(v)), "CAAGetID")
        return v.value

    def open(self, dev_id: int) -> None:
        _check(self._d.CAAOpen(dev_id), "CAAOpen")

    def close(self, dev_id: int) -> None:
        _check(self._d.CAAClose(dev_id), "CAAClose")

    def get_property(self, dev_id: int) -> tuple[str, int]:
        info = _Info()
        _check(self._d.CAAGetProperty(dev_id, ctypes.byref(info)),
               "CAAGetProperty")
        return info.Name.decode("ascii", "replace"), int(info.MaxStep)

    def move_to_mechanical(self, dev_id: int, deg: float) -> None:
        _check(self._d.CAAMoveToMechanical(dev_id, ctypes.c_float(deg)),
               "CAAMoveToMechanical")

    def stop(self, dev_id: int) -> None:
        _check(self._d.CAAStop(dev_id), "CAAStop")

    def is_moving(self, dev_id: int) -> tuple[bool, bool]:
        moving, hand = ctypes.c_bool(), ctypes.c_bool()
        _check(self._d.CAAIsMoving(dev_id, ctypes.byref(moving),
                                   ctypes.byref(hand)), "CAAIsMoving")
        return moving.value, hand.value

    def get_degree(self, dev_id: int) -> float:
        v = ctypes.c_float()
        _check(self._d.CAAGetDegree(dev_id, ctypes.byref(v)), "CAAGetDegree")
        return float(v.value)

    def get_temp(self, dev_id: int) -> float:
        v = ctypes.c_float()
        _check(self._d.CAAGetTemp(dev_id, ctypes.byref(v)), "CAAGetTemp")
        return float(v.value)

    def get_reverse(self, dev_id: int) -> bool:
        v = ctypes.c_bool()
        _check(self._d.CAAGetReverse(dev_id, ctypes.byref(v)), "CAAGetReverse")
        return v.value

    def set_reverse(self, dev_id: int, value: bool) -> None:
        _check(self._d.CAASetReverse(dev_id, ctypes.c_bool(value)),
               "CAASetReverse")

    def get_type(self, dev_id: int) -> str:
        t = _Type16()
        _check(self._d.CAAGetType(dev_id, ctypes.byref(t)), "CAAGetType")
        return t.type.decode("ascii", "replace")

    def firmware(self, dev_id: int) -> str:
        a, b, c = ctypes.c_ubyte(), ctypes.c_ubyte(), ctypes.c_ubyte()
        _check(self._d.CAAGetFirmwareVersion(
            dev_id, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c)),
            "CAAGetFirmwareVersion")
        return f"{a.value}.{b.value}.{c.value}"


#: Factory seams — the zwo-usb session builds SDKs through these so tests can
#: inject fakes without touching real DLLs.
make_eaf = EafSdk
make_caa = CaaSdk
