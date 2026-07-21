import sys
from pathlib import Path
import pytest
from astrodeck.devices.cameras import zwo_asi_sdk as z


def test_signatures_declared():
    # every bound export appears in the signature table with an argtype list
    assert "ASIGetCameraProperty" in z._SIGNATURES
    assert "ASIStartExposure" in z._SIGNATURES
    assert set(z._EXPORTS) <= set(z._SIGNATURES)


def test_make_asi_seam_exists():
    assert z.make_asi is z.AsiSdk


def test_asi_sdk_construction_is_clean():
    # Constructing AsiSdk either succeeds (a DLL is findable — vendored or a
    # system ASIStudio/ZWO install) or raises AsiSdkError (absent). It must never
    # raise ImportError/AttributeError — that would mean a broken binding table.
    try:
        sdk = z.AsiSdk()
    except z.AsiSdkError:
        return
    # A DLL loaded => all 15 declared exports are present (else load would fail);
    # count() is safe with no camera attached.
    assert sdk.count() >= 0


@pytest.mark.skipif(
    sys.platform != "win32"
    or not (Path(z.__file__).resolve().parent.parent / "vendor" / "zwo"
            / "ASICamera2.dll").is_file(),
    reason="requires win32 + vendored ASICamera2.dll")
def test_real_dll_loads_and_exports():
    sdk = z.AsiSdk()          # raises if DLL absent/incomplete
    assert sdk.count() >= 0
