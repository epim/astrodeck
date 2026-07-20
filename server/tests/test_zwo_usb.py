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
