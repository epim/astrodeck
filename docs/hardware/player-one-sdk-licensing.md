# Player One Camera SDK — Redistribution Status

**Date:** 2026-07-20, corrected 2026-10-07
**Question:** May AstroDeck bundle `PlayerOneCamera.dll` in-repo (`vendor/playerone/`)
the way it bundles ZWO's MIT `libasi`, to keep the single-install / zero-vendor-software
promise for the Poseidon-M Pro imaging camera?

## Verdict: **NO GRANT TO REDISTRIBUTE. The owner's ruling is pending in #632.**

The SDK's own `license.txt` (Camera SDK V3.10.1) grants use of the SDK for development
and has **no distribution verb**. The 2026-07-20 version of this page called it
"MIT-style" and read it as allowing redistribution, then vendored the files on that
reading. That was an interpretation, not a grant, and it is withdrawn here. Whether we
may pass the files on is an owner decision, tracked in
[#632](https://github.com/epim/astrodeck/issues/632). Written confirmation from Player One
(support@player-one-astronomy.com) would settle it; none is recorded.

## What the builds do while the ruling is pending

The copies in `server/astrodeck/vendor/playerone/` travel in three artifacts:

| Artifact | Carries the six libraries? | Where that is recorded |
|---|---|---|
| Frozen (PyInstaller) executables | Yes | `packaging/distribution-policy.json`, `playerone` is `pending` and `pending_by_artifact.frozen` is `true` |
| Server wheel | Yes | the same row, `pending_by_artifact.server-wheel` is `true` |
| Docker image | Yes: it runs `pip install ./server` | not covered by any gate yet (#655) |
| Source tarball | No | the same row, `pending_by_artifact.source-tar` is `false` |

The release audit (`tools/licence/audit_release.py`) reports `OWNER_PENDING` for any
frozen or wheel artifact that contains a Player One library, so no tagged release passes
the gate until #632 is ruled. Published releases v0.3.23 to v0.3.28 were built this way
(#632). Changing what published assets or git history carry is the owner's call.

A machine can instead fetch the SDK from the vendor (`server/astrodeck/licensing.py`,
remedy `fetch`) and point `ASTRODECK_PLAYERONE_SDK_DIR` at it. Using the SDK is what the
grant covers.

## What the licence says (from the SDK package's `license.txt`)

- *"This SDK is only used for the secondary development of our company's cameras or
  other equipment. You can use our company's products and this SDK to develop any
  products without any restrictions."* This is the whole grant. It says what the SDK may
  be used for. It names no right to copy, publish, distribute, sublicense or sell it.
- The MIT copyright-notice-retention clause: *"The above copyright notice and this
  permission notice shall be included in all copies or substantial portions of the
  Software."* This presupposes that copies exist. It is a condition on a permission, and
  the permission itself is not in the document.
- The verbatim MIT warranty-disclaimer paragraph ("THE SOFTWARE IS PROVIDED 'AS IS'…").
- Third-party components are stated to comply with their own open-source licenses.

The document is MIT-*shaped*: two of its closing paragraphs are MIT's, word for word, and
the paragraph that does the granting is Player One's own. That is not the same legal basis
as ZWO's MIT SDK (`vendor/zwo/`), whose licence is MIT throughout.

A reasonable person may conclude that redistribution is intended, since a runtime library
is useless unless it ships. Intended is not granted, which is why this is a ruling and
not a reading.

## What would settle it

- **Player One confirms in writing:** record the reply here, beside the quote above and not
  instead of it, and in the licensing registry (`server/astrodeck/licensing.py`). The
  distribution decision then moves out of `pending` in `packaging/distribution-policy.json`.
- **The owner rules that we may not:** the SDK moves to the fetch remedy only, the six
  libraries leave the repository and the packaging, and the gate fails any build that
  contains them.

Either outcome is recorded against #632; this page does not choose.

## Binding verification (done)

The ctypes bindings (`cameras/player_one_sdk.py`) were verified against the SDK's own
`python/pyPOACamera.py` + `include/PlayerOneCamera.h` (V3.10.1): enum values, struct
layouts, the sensor-mode/LRN API, and the POASetConfig/POAGetConfig per-call
convention. The DLL loads from the vendor dir with all 20 called exports resolving
(dev box; `count()=0`, no Poseidon attached — live `get_properties`/`sensor_modes`
verification remains an at-scope step).

## Historical note

Before the package license was read, the public software page stated no terms and a
public Rust binding declined to redistribute the DLL, so the interim verdict was
UNCLEAR / do-not-bundle. Reading the in-package `license.txt` on 2026-07-20 moved that to a
"permits" verdict, and the files were vendored. The text read is the same text quoted
above and it has no distribution verb, so the honest state is the interim one: unclear,
and awaiting the ruling in #632.
