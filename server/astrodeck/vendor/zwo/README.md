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
