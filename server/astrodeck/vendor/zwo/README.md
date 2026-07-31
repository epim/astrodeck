# Vendored ZWO SDK binaries (win-x64)

Redistributed under ZWO's MIT-style license (`LICENSE.txt`, verbatim from the
`libasi` package in `indilib/indi-3rdparty`, which bundles these SDKs the same
way — as does NINA). See
`docs/superpowers/specs/2026-07-20-accessory-native-drivers-design.md` §1 and
`docs/hardware/rotator-focuser-filterwheel-native.md` for the audit trail.

| File | API | SDK version | Origin | Verified |
|---|---|---|---|---|
| `EAF_focuser.dll` | EAF (focuser) C API | **1.8.1** (ZWO's current) | ASIStudio 1.20 install | ctypes load + full export check, 2026-07-20 |
| `CAARotator.dll` | CAA (rotator) C API | **1.5.6** | ZWO ASCOM Driver 6.5.35 (`CAA_ASCOM_x64.dll` — despite the name it exports the complete CAA SDK incl. `CAAMoveToMechanical`) | same |
| `ASICamera2.dll` | ASICamera2 (camera) C API | **1.41.0.0** | ASIStudio install (`ASIGetSDKVersion`) | ctypes load + full 15-export check, 2026-07-20 (dev box; live `get_property` at scope pending) |

`EAF_focuser.h` is vendored alongside the binaries — the C headers are the ONLY
authority for a ctypes signature, and a wrong struct layout does not fail
loudly, it corrupts a stack on someone's telescope. Consult it before binding
anything new (`devices/zwo_sdk.py:_SIGNATURES`). Two things in it are easy to
get wrong and cost real time on 2026-07-31:

- `EAF_ERROR_MSG` is two **2-character strings**, not integers. (`EAF_ALL_INFO`
  has same-named fields sized `[2]` rather than `[3]` — a different struct.)
- The motor codes are `E0 no error` and `E5 motor stall`; the battery codes are
  `E6`/`E7`/`E8`. **E0 is the healthy one** — treating it as a fault attaches an
  invented hardware error to every unrelated failure.

Notes:
- `ASIStudio\CAA_SRC.dll` was REJECTED: it lacks `CAAMoveToMechanical` (pre-
  mechanical-API build). Filenames lie; exports don't — always re-verify with
  `tests/test_zwo_usb.py::test_vendored_dlls_export_api` after any update.
- ZWO's current CAA SDK is 1.5.9; 1.5.6 carries every function we bind. Consider
  refreshing from the official developer download when convenient.
- Update procedure: drop new DLLs here under the SAME canonical names, run the
  export test, note versions above.
- Loader/search order: `ASTRODECK_ZWO_SDK_DIR` env → this dir → known installs
  (`devices/zwo_sdk.py:_find_dll`).

## Per-platform libraries

`macos/libASICamera2.dylib` is the macOS build of the camera SDK.

**There is no Linux `libASICamera2.so` here yet.** The upstream reference tree
carries only `libASICamera2.a`, a *static* archive, which `ctypes.CDLL` cannot
load at runtime — it exists for projects that link at build time. ZWO's own SDK
download does ship `libASICamera2.so` for armv7/armv8/x64; that file is what is
needed to make ZWO cameras work on Linux and the Raspberry Pi.

`EAF_focuser` (focuser) and `CAARotator` (rotator) are Windows-only here for the
same reason: the upstream tree we vendored from does not carry them at all, since
it is a guiding application that drives neither. ZWO ships separate EAF and CAA
Linux SDKs.

**Linux needs `libusb-1.0-0` installed** for any of these once added.
