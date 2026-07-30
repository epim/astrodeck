# Vendored Player One Camera SDK

`PlayerOneCamera.dll` (win-x64) from **Player One Camera SDK V3.10.1** (released
2025-04-30), downloaded from https://player-one-astronomy.com/service/software/
(`PlayerOne_Camera_SDK_Windows_V3.10.1.zip`).

## Files
- `PlayerOneCamera.dll` — the x64 SDK runtime (`lib/x64/` in the SDK zip).
- `LICENSE` — the SDK's `license.txt`, verbatim.

## Licensing
The SDK license is MIT-style and **permits redistribution**: it grants use "to
develop any products without any restrictions" and carries the standard MIT
copyright-notice-retention clause and warranty disclaimer. Bundling `.dll`
copies is allowed provided this LICENSE travels with them. Full analysis:
`docs/hardware/player-one-sdk-licensing.md`.

## Bindings + verification
`astrodeck/devices/cameras/player_one_sdk.py` binds this DLL via ctypes. The
enum values, struct layouts (`POACameraProperties`, `POAConfigAttributes`,
`POASensorModeInfo`), the sensor-mode API (LRN), and the POASetConfig/
POAGetConfig per-call calling convention were verified against the SDK's own
`python/pyPOACamera.py` and `include/PlayerOneCamera.h` (V3.10.1).

## Updating
Replace the DLL from a newer SDK zip's `lib/x64/`, refresh LICENSE, re-verify
the bindings against that release's `pyPOACamera.py`, and bump the version note
above.

## Per-platform libraries

`linux-arm64/`, `linux-x86_64/`, `linux-arm32/`, `linux-x86/` and `macos/` hold
the same SDK (v3.10.0) built for each target. `devices/sdk_paths.py` picks the
directory matching the running machine; the flat `PlayerOneCamera.dll` above is
the historical Windows location and still resolves unchanged.

The Linux files are fully versioned (`libPlayerOneCamera.so.3.10.0`) because the
vendor ships the short names as symlinks, and a symlink survives neither a
Windows checkout nor a wheel build.

**Linux needs `libusb-1.0-0` installed.** Without it the library resolves and
then fails to load with `libusb-1.0.so.0: cannot open shared object file`, which
surfaces only as the camera not being offered. The container image installs it.
