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
