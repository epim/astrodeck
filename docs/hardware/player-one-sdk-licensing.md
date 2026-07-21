# Player One Camera SDK — Redistribution Verdict

**Date:** 2026-07-20
**Question:** May AstroDeck bundle `PlayerOneCamera.dll` in-repo (`vendor/playerone/`)
the way it bundles ZWO's MIT `libasi`, to keep the single-install / zero-vendor-software
promise for the Poseidon-M Pro imaging camera?

## Verdict: **UNCLEAR — do NOT bundle yet. Escalate.**

Redistribution is **not confirmed permitted**. Do not vendor the binary until the SDK
package's own license file is read and permits it.

## Evidence

- **Player One's public software page** (https://player-one-astronomy.com/service/software/)
  states **no license terms, no EULA, no redistribution language** — only that the SDK is
  "provided for developers to do secondary development based on Player One cameras." Current
  Windows SDK: **Camera SDK V3.10.1** (released 2026/05/01).
- **The public Rust binding** ([Uriopass/playerone-sdk-rs](https://github.com/Uriopass/playerone-sdk-rs),
  MIT) **deliberately does NOT redistribute the DLL** — its README instructs the user to
  download the SDK separately. In the same ecosystem, ZWO's MIT SDK *is* freely bundled
  (INDI, NINA, and AstroDeck's own `vendor/zwo/`). The contrast is the signal: nobody
  redistributes the Player One binary.
- **The definitive answer lives in the SDK package itself** — the `license`/`readme` file
  inside the downloaded `Camera SDK V3.10.1` Windows zip. That file was **not read** for
  this verdict (the SDK is not installed on the dev box and was not downloaded here).

## Required next step (blocks bundling only)

1. Download Camera SDK V3.10.1 (Windows) from player-one-astronomy.com.
2. Read the license/readme inside the package. Record the exact text + filename here.
3. Branch:
   - **PERMITS redistribution** → vendor `PlayerOneCamera.dll` into `vendor/playerone/`
     with `LICENSE` (verbatim) + provenance `README.md` (mirroring `vendor/zwo/README.md`).
     Update this doc's verdict to PERMITS.
   - **FORBIDS / still unclear** → do NOT bundle. Choose a fallback (user decision):
     - **first-run auto-download** (checksum-pinned fetch into the app data dir), or
     - **require separate install** (loader's known-install search path already supports it).

## What is NOT blocked by this

The Player One **adapter, bindings, and backend** are built regardless (Tasks 9–11): the
loader search order is `ASTRODECK_PLAYERONE_SDK_DIR` env → `vendor/playerone/` →
known installs, so the adapter works whether the DLL is vendored, downloaded, or
system-installed. Only the *in-repo bundling* of the binary waits on this verdict.
