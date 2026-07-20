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
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "zwo"

#: canonical basename -> (known alternative install paths, verifying exports)
_DLL_SPECS: dict[str, tuple[list[str], list[str]]] = {
    "EAF_focuser.dll": (
        [r"C:\Program Files\ASIStudio\EAF_focuser.dll"],
        ["EAFGetNum", "EAFOpen", "EAFMove", "EAFGetPosition", "EAFStop",
         "EAFIsMoving", "EAFGetProperty", "EAFGetTemp"],
    ),
    "CAARotator.dll": (
        [r"C:\Program Files (x86)\Common Files\ASCOM\ZWO\CAA_ASCOM_x64.dll",
         r"C:\Program Files\ASIStudio\CAA_SRC.dll"],
        ["CAAGetNum", "CAAOpen", "CAAMoveToMechanical", "CAAGetDegree",
         "CAAStop", "CAAIsMoving", "CAAGetProperty", "CAAGetReverse"],
    ),
}


class ZwoSdkError(Exception):
    """A non-zero ``*_ERROR_CODE`` from an SDK call (or a load failure)."""

    def __init__(self, code: int, fn: str):
        super().__init__(f"ZWO SDK {fn} failed (code {code})")
        self.code = code
        self.fn = fn


class _Info(ctypes.Structure):
    """EAF_INFO / CAA_INFO share the layout {int ID; char Name[64]; int MaxStep}."""

    _fields_ = [("ID", ctypes.c_int), ("Name", ctypes.c_char * 64),
                ("MaxStep", ctypes.c_int)]


class _Type16(ctypes.Structure):
    """CAA_TYPE {char type[16]}."""

    _fields_ = [("type", ctypes.c_char * 16)]


def _loads_with_exports(path: Path, exports: list[str]):
    """Return the loaded DLL when it loads AND exports everything, else None."""
    try:
        dll = ctypes.WinDLL(str(path))
    except OSError:
        return None
    if all(hasattr(dll, e) for e in exports):
        return dll
    return None


def _find_dll(basename: str):
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


class EafSdk:
    """Typed wrapper over the EAF C API (one process-wide DLL handle)."""

    def __init__(self):
        self._d = _find_dll("EAF_focuser.dll")
        if self._d is None:
            raise ZwoSdkError(-1, "EAF_focuser.dll load (not found/invalid)")

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


class CaaSdk:
    """Typed wrapper over the CAA C API (one process-wide DLL handle)."""

    def __init__(self):
        self._d = _find_dll("CAARotator.dll")
        if self._d is None:
            raise ZwoSdkError(-1, "CAARotator.dll load (not found/invalid)")

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
