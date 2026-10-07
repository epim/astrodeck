# Vendored Player One Camera SDK

`PlayerOneCamera.dll` (win-x64) from **Player One Camera SDK V3.10.1** (released
2025-04-30), downloaded from https://player-one-astronomy.com/service/software/
(`PlayerOne_Camera_SDK_Windows_V3.10.1.zip`).

## Files
- `PlayerOneCamera.dll` — the x64 SDK runtime (`lib/x64/` in the SDK zip).
- `LICENSE` — the SDK's `license.txt`, verbatim.

## Licensing — READ THIS BEFORE SHIPPING ANY OF THESE FILES

**We do not have a stated right to redistribute these binaries, and the owner
has not yet ruled whether we may. The ruling is pending in #632.** This
paragraph has been corrected twice. On 2026-08-09 it stopped asserting that the
licence "permits redistribution", an interpretation that was read as a fact for
weeks while six binaries shipped on it. On 2026-10-07 it stopped telling
readers that releases ship without any Player One file, because the build
contradicts that.

What the licence actually says, in its own words:

> This SDK is only used for the secondary development of our company's cameras
> or other equipment. You can use our company's products and this SDK to
> develop any products without any restrictions.

That is the whole grant. It covers use of the SDK for development, and it
contains **no distribution verb** — not copy, publish, distribute, sublicense
or sell. The document is MIT-*shaped*: it closes with MIT's notice-retention
clause and MIT's warranty disclaimer word for word, which is where the earlier
reading came from. The middle paragraph is Player One's own prose and is not
MIT.

A reasonable person may well conclude redistribution is intended — a runtime
library is useless unless it ships, and a retained-notice clause presupposes
copies. Intended is not granted.

**What the builds do while the ruling is pending.** The files in this
directory travel in three of our artifacts:

- The frozen (PyInstaller) executables and the server wheel carry them.
  `packaging/distribution-policy.json` records both under `pending`
  (`pending_by_artifact`), and the release audit reports OWNER_PENDING for any
  of them that contains a Player One library.
- The Docker image runs `pip install ./server`, so it carries them too. No gate
  covers the image yet (#655).
- The source tarball does not: its `pending_by_artifact` entry is `false`.

A machine can instead fetch the SDK from the vendor
(`server/astrodeck/licensing.py`, remedy `fetch`) and point
`ASTRODECK_PLAYERONE_SDK_DIR` at it; using the SDK is what the grant covers.

Written confirmation from support@player-one-astronomy.com would settle the
question, and none is recorded. When it arrives, record it here beside the
quote — not instead of it. Full analysis:
`docs/hardware/player-one-sdk-licensing.md`; audit:
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
