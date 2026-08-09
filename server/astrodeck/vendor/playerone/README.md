# Vendored Player One Camera SDK

`PlayerOneCamera.dll` (win-x64) from **Player One Camera SDK V3.10.1** (released
2025-04-30), downloaded from https://player-one-astronomy.com/service/software/
(`PlayerOne_Camera_SDK_Windows_V3.10.1.zip`).

## Files
- `PlayerOneCamera.dll` — the x64 SDK runtime (`lib/x64/` in the SDK zip).
- `LICENSE` — the SDK's `license.txt`, verbatim.

## Licensing — READ THIS BEFORE SHIPPING ANY OF THESE FILES

**We do not have a stated right to redistribute these binaries.** Corrected
2026-08-09; the previous version of this paragraph asserted that the licence
"permits redistribution", which is an interpretation and was read as a fact for
weeks while six binaries shipped on it.

What the licence actually says, in its own words:

> This SDK is only used for the secondary development of our company's cameras
> or other equipment. You can use our company's products and this SDK to
> develop any products without any restrictions.

That is the whole grant, and it contains **no distribution verb** — not copy,
publish, distribute, sublicense or sell. The document is MIT-*shaped*: it closes
with MIT's notice-retention clause and MIT's warranty disclaimer word for word,
which is where the earlier reading came from. The middle paragraph is Player
One's own prose and is not MIT.

A reasonable person may well conclude redistribution is intended — a runtime
library is useless unless it ships, and a retained-notice clause presupposes
copies. Intended is not granted.

**So:** releases carry no Player One binary. A machine that needs one fetches
the SDK from the vendor (`server/astrodeck/licensing.py`, remedy `fetch`), which
is squarely inside the grant above. The copies in this directory are for
development on this checkout.

Written confirmation has been requested from support@player-one-astronomy.com.
When it arrives, record it here beside the quote — not instead of it. Full
analysis: `docs/hardware/player-one-sdk-licensing.md`; audit:
`docs/superpowers/backlog/2026-08-08-third-party-licences.md` §1.3.

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
