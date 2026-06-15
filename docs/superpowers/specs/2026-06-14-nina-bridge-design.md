# AstroDeck NINA Bridge — Transition Mode

**Date:** 2026-06-14
**Status:** Implemented & verified (against a mock NINA backed by the sim rig)

## Purpose

Let AstroDeck control a rig through a running **NINA** instance via NINA's
**Advanced API** plugin (`http://<host>:1888/v2/api` REST + `ws://…/v2/socket`
events). This is the on-ramp for NINA users: fly an existing, fully-configured
NINA rig from AstroDeck's UI without redoing ASCOM/Alpaca driver setup, then
migrate to direct Alpaca device by device.

## Key constraint that shapes the design

NINA does **not** expose raw 16-bit frames over HTTP. It returns rendered,
auto-stretched images plus computed statistics (HFR, star count, mean), and it
owns mature autofocus, plate-solve and PHD2-guider routines. So the bridge
**delegates the smart operations to NINA** rather than treating it as a dumb
sensor (which is impossible — no raw pixels):

| Operation | NINA mapping |
|---|---|
| capture | `capture` (await) → `prepared-image?stream=true` (stretched JPEG) → `capture/statistics` (HFR/stars) |
| autofocus | native `focuser/auto-focus`, then `focuser/last-af` → render NINA's V-curve |
| plate-solve & center | `prepared-image/solve` + `mount/sync`, reusing the existing goto-center loop |
| mount / focuser / filter / switch / guider | the matching `equipment/*` endpoints |
| live mirror | subscribe to `/v2/socket`; surface NINA-initiated IMAGE-SAVE / connect events as log lines |

## Seams added (Alpaca & sim backends untouched)

1. `CameraFrame` gains optional `rendered_bytes` + `rendered_mime`, `hfr`,
   `stars`, `saved_path`. When `rendered_bytes` is present the hub uses it
   verbatim for the preview (no re-stretch), reads HFR/stars from the backend,
   and skips its own FITS save (NINA saved on the imaging machine).
2. `Focuser.supports_native_autofocus` + `async native_autofocus()`.
   `run_autofocus` delegates when present, publishing the same `focus` events
   the UI already consumes.
3. Hub: `connect_nina()`, a NINA branch in `solve_and_sync()`, a best-effort
   NINA WebSocket listener, and a `mode` field (`none|sim|alpaca|nina`)
   surfaced in status for the UI badge.

Non-intrusive bridge semantics: disconnecting AstroDeck never tears down
NINA's own equipment connections; a role is registered only if NINA already
reports it connected.

## Verification

`server/tools/mock_nina.py` serves NINA-shaped responses backed by the sim
star-field, so the whole path is exercised by tests (`tests/test_nina.py`, 10
cases) and runnable live (`python -m tools.mock_nina`). Confirmed end to end:
bridge connect, capture with rendered preview + HFR/stars, native autofocus
converging to true focus, NINA plate-solve centering 1.8′→0.1′.

## Known limitations / future

- Manual slew-pad nudging isn't in NINA's REST surface (use Goto); the bridge
  reports a clear error.
- WebSocket mirroring currently logs NINA-initiated activity rather than
  fetching those images into the live preview (avoids double-preview on our own
  captures); full live image mirror is a future enhancement.
- Guide-graph field units vary across NINA versions; parsing is defensive.
