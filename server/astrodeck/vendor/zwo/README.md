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

`linux-arm64/` carries the aarch64 builds of all three libraries we load —
`libASICamera2.so`, `libEAFFocuser.so`, `libCAARotator.so` — taken byte-for-byte
from `indilib/indi-3rdparty` `libasi/armv8/`, where they ship renamed to `.bin`
so distribution tooling does not strip them. Verified as AArch64 ELF and loaded
on an RK3588S (Orange Pi 5 Pro) 2026-08-11.

> An earlier version of this file said no Linux build existed, because "the
> upstream reference tree carries only `libASICamera2.a`, a static archive". That
> was true of *one* upstream tree and was read for months as though it were true
> of ZWO's Linux support generally. It is the same shape as the Player One
> "permits redistribution" claim: a conclusion about a source, recorded as a fact
> about the world. The shared objects were always there.

Two integration details that are not obvious from the files:

* **`libCAARotator.so` needs libudev already loaded.** It calls
  `udev_device_get_devnode` without declaring libudev in its `DT_NEEDED`, so
  `ldd` reports every dependency satisfied and `dlopen` still fails with
  `undefined symbol`. `zwo_sdk._preload_libudev()` loads it `RTLD_GLOBAL` first.
* **The focuser is the aliased name.** ZWO's Windows `EAF_focuser.dll` is
  `libEAFFocuser.so` on Linux; `sdk_paths._POSIX_STEM_ALIASES` carries that, and
  `candidates()` must join the alias to the per-platform directory or the file is
  invisible where it sits.

`vendor/zwo/*.dll` remain the Windows builds. There is no ZWO filter-wheel
library here because nothing in this codebase loads one.

**Linux needs `libusb-1.0-0` installed** for any of these once added.
