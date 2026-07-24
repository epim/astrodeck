"""ASCOM registry enumeration (COM-T1) — the native "scan".

Import-light (stdlib only) so the bundled COM host (astrodeck.comhost) can
reuse it without pulling the server app. Reads installed ASCOM drivers per
type from HKLM\\SOFTWARE\\[WOW6432Node\\]ASCOM\\<Type> Drivers (both 32- and
64-bit views), assigns a DETERMINISTIC per-type device number (sorted ProgID
index), and exposes the list. Determinism is load-bearing: the server
advertises (dev_type, dev_num) and the comhost independently resolves the same
(dev_type, dev_num) -> ProgID from the SAME enumeration, so no handshake and
no new Alpaca client are needed (spec §3.1/§3.2).

Windows-only source of truth; every entry point degrades to empty off Windows.
"""
from __future__ import annotations

from dataclasses import dataclass

try:  # Windows-only; absent on Mac/Linux and in the portable test path.
    import winreg
    _WINREG_OK = True
except ImportError:  # pragma: no cover - exercised via monkeypatch on non-win
    winreg = None  # type: ignore
    _WINREG_OK = False

#: The ASCOM registry type words (the "<Type> Drivers" subkeys). dev_type is
#: the Alpaca device-type string = the word lowercased.
ASCOM_TYPES: tuple[str, ...] = (
    "Telescope", "Camera", "Focuser", "FilterWheel", "Rotator", "Switch",
    "SafetyMonitor", "ObservingConditions", "CoverCalibrator", "Dome",
)

#: Alpaca dev_type -> AstroDeck role (inverse of native_backend._ROLE_TO_DEV_TYPE;
#: mirrors drivers._DEV_TYPE_TO_ROLE). Types absent here have no AstroDeck role
#: and are host-side only (dome/covercalibrator/observingconditions).
_DEV_TYPE_TO_ROLE: dict[str, str] = {
    "camera": "camera", "telescope": "telescope", "focuser": "focuser",
    "filterwheel": "filterwheel", "switch": "switch",
    "safetymonitor": "safety", "rotator": "rotator",
    # Real Alpaca clients now exist (PRO-4 dome / PRO-5 cover) so an ASCOM Dome
    # or CoverCalibrator served by the COM host is offered, not skipped.
    "dome": "dome", "covercalibrator": "covercalibrator",
}


# Capture the builtin BEFORE this module defines a public `enumerate` that
# shadows it in the module globals — so the implementation can still index with
# the real builtin without recursing into itself.
_builtin_enumerate = enumerate


@dataclass(frozen=True)
class AscomDriver:
    ascom_type: str   # registry word, e.g. "Camera"
    dev_type: str     # Alpaca lowercase, e.g. "camera"
    progid: str
    name: str
    dev_num: int


def _read_type_drivers(ascom_type: str) -> dict[str, str]:
    """Read {progid: display_name} for one ASCOM type, merging the 64- and
    32-bit registry views (64-bit wins on a duplicate ProgID). Each ProgID is a
    subkey under "<Type> Drivers"; its DEFAULT value is the display name. A
    missing key / value degrades to empty (a box with no drivers of a type)."""
    if not _WINREG_OK:
        return {}
    out: dict[str, str] = {}
    base = fr"SOFTWARE\ASCOM\{ascom_type} Drivers"
    # 32-bit view first, then 64-bit, so 64-bit overwrites (wins).
    for flag in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                                 winreg.KEY_READ | flag)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    progid = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    sub = winreg.OpenKey(key, progid, 0,
                                         winreg.KEY_READ | flag)
                    try:
                        name, _ = winreg.QueryValueEx(sub, None)  # default value
                    finally:
                        sub.Close()
                except OSError:
                    name = ""
                out[progid] = str(name) if name else progid
        finally:
            key.Close()
    return out


def enumerate(read=_read_type_drivers) -> list[AscomDriver]:
    """Enumerate installed ASCOM drivers, per type, with deterministic dev_nums.
    ``read(ascom_type) -> {progid: name}`` is injectable for tests. ProgIDs are
    sorted case-insensitively and indexed so the (dev_type, dev_num) -> ProgID
    map is stable across runs (the comhost independently reproduces it)."""
    if not _WINREG_OK and read is _read_type_drivers:
        return []
    drivers: list[AscomDriver] = []
    for ascom_type in ASCOM_TYPES:
        found = read(ascom_type)
        for dev_num, progid in _builtin_enumerate(sorted(found, key=str.lower)):
            drivers.append(AscomDriver(
                ascom_type=ascom_type, dev_type=ascom_type.lower(),
                progid=progid, name=found[progid], dev_num=dev_num))
    return drivers


def progid_for(dev_type: str, dev_num: int,
               drivers: "list[AscomDriver] | None" = None) -> "str | None":
    """Reverse lookup used by the comhost to instantiate a device slot."""
    for d in (drivers if drivers is not None else enumerate()):
        if d.dev_type == dev_type and d.dev_num == dev_num:
            return d.progid
    return None


def enumerate_offers_with(read) -> list[dict]:
    """enumerate_offers over an injected reader (test seam)."""
    offers: list[dict] = []
    for d in enumerate(read=read):
        role = _DEV_TYPE_TO_ROLE.get(d.dev_type)
        if role is None:
            continue
        entry = {"role": role, "name": d.name,
                 "dev_type": d.dev_type, "dev_num": d.dev_num,
                 "progid": d.progid}
        offers.append(entry)
        if role == "camera":  # D6 dual-offer (mirrors drivers._probe_alpaca)
            offers.append(dict(entry) | {"role": "guide_camera"})
    return offers


def enumerate_offers() -> list[dict]:
    """Role-tagged offers for the discovery/offers surface (real registry)."""
    return enumerate_offers_with(_read_type_drivers)
