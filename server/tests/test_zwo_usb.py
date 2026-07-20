"""ZWO native accessories: SDK loader/bindings, EafFocuser, CaaRotator,
zwo-usb backend + framework integration. Hardware-free (fake SDK doubles);
the vendored-DLL export test runs only where the DLLs exist."""
from __future__ import annotations

import pytest

from astrodeck.devices import zwo_sdk
from astrodeck.devices.zwo_sdk import ZwoSdkError


def test_sdk_error_carries_code_and_fn():
    e = ZwoSdkError(7, "CAAMoveToMechanical")
    assert e.code == 7 and e.fn == "CAAMoveToMechanical"
    assert "CAAMoveToMechanical" in str(e) and "7" in str(e)


_VENDORED = [p for p in (zwo_sdk._VENDOR_DIR / n for n in zwo_sdk._DLL_SPECS)
             if p.is_file()]


@pytest.mark.skipif(len(_VENDORED) < 2,
                    reason="vendored ZWO DLLs absent (CI without binaries)")
def test_vendored_dlls_export_api():
    """Filenames lie; exports don't. The vendored DLLs must carry the full API
    (the ASIStudio CAA_SRC.dll rejection story — see vendor/zwo/README.md)."""
    for basename, (_alts, exports) in zwo_sdk._DLL_SPECS.items():
        dll = zwo_sdk._loads_with_exports(zwo_sdk._VENDOR_DIR / basename, exports)
        assert dll is not None, f"{basename} failed load/export verification"


# ------------------------------------------------------- transport="local"

def test_driverentry_local_needs_no_addressing():
    from astrodeck.config import DriverEntry
    e = DriverEntry(id="zwo-usb-ab12", type="zwo-usb", transport="local")
    assert e.transport == "local" and e.host == "" and e.port_path == ""


def test_add_driver_local(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    e = config_store.add_driver("zwo-usb", transport="local")
    assert e.id.startswith("zwo-usb-") and e.transport == "local"
    assert e.label == "ZWO-USB (USB)"
