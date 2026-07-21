# Player One Camera SDK — Redistribution Verdict

**Date:** 2026-07-20
**Question:** May AstroDeck bundle `PlayerOneCamera.dll` in-repo (`vendor/playerone/`)
the way it bundles ZWO's MIT `libasi`, to keep the single-install / zero-vendor-software
promise for the Poseidon-M Pro imaging camera?

## Verdict: **PERMITS redistribution — bundled.** (resolved 2026-07-20)

The SDK's own `license.txt` (Camera SDK V3.10.1) is **MIT-style and permits
redistribution**. `PlayerOneCamera.dll` is now vendored in
`server/astrodeck/vendor/playerone/` with the license verbatim as `LICENSE`.

## Evidence (from the SDK package's `license.txt`)

- *"You can use our company's products and this SDK to develop **any products
  without any restrictions**."*
- The standard MIT copyright-notice-retention clause: *"The above copyright notice
  and this permission notice shall be included in **all copies or substantial
  portions of the Software**."* — this explicitly contemplates redistributing copies.
- The verbatim MIT warranty-disclaimer paragraph ("THE SOFTWARE IS PROVIDED 'AS IS'…").
- Third-party components are stated to comply with their own open-source licenses.

This is the same legal basis as ZWO's MIT SDK (`vendor/zwo/`). Bundling `.dll`
copies is allowed provided the `LICENSE` travels with them (it does — `vendor/
playerone/LICENSE` + provenance `README.md`). SDK V3.10.1, released 2025-04-30.

## Binding verification (done)

The ctypes bindings (`cameras/player_one_sdk.py`) were verified against the SDK's own
`python/pyPOACamera.py` + `include/PlayerOneCamera.h` (V3.10.1): enum values, struct
layouts, the sensor-mode/LRN API, and the POASetConfig/POAGetConfig per-call
convention. The DLL loads from the vendor dir with all 20 called exports resolving
(dev box; `count()=0`, no Poseidon attached — live `get_properties`/`sensor_modes`
verification remains an at-scope step).

## Historical note (superseded)

Before the package license was read, the public software page stated no terms and a
public Rust binding declined to redistribute the DLL, so the interim verdict was
UNCLEAR / do-not-bundle. Reading the in-package `license.txt` resolved it to PERMITS.
