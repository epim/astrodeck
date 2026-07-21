# Multi-rate mount tracking (sidereal / lunar / solar) — Design

**Date:** 2026-07-21
**Status:** approved (brainstorming, 2026-07-21)

## Goal
Let the mount track at the **lunar** and **solar** drive rates in addition to
sidereal, surfaced as a tri-state (XOR) rate selector in the Mount UI. This is a
**drive-rate** change only — it does NOT auto-slew to or ephemeris-follow the
Moon/Sun (the conventional meaning of "lunar/solar tracking"; the user points at
the body themselves).

## Model (decided)
- **Generic + capability-gated.** The rate concept lives in the base `Telescope`
  contract behind a `can_set_tracking_rate` flag; backends that support it opt in
  (AM5N, Alpaca, sim). Mounts that don't advertise it never show the control.
- **Separate on/off + rate selector.** Mirrors ASCOM's `Tracking` (bool) +
  `TrackingRate` (enum). The existing Tracking on/off toggle is unchanged; the new
  control only picks the *rate*. Picking a rate while tracking is ON applies live;
  while OFF it stages the rate the mount will use when tracking is next enabled.
- **Rate vocabulary:** the wire/API/UI use the lowercase strings
  `"sidereal" | "lunar" | "solar"`. (ASCOM "King" rate is out of scope — YAGNI.)

## Contract (`server/astrodeck/devices/base.py`, `Telescope`)
Follows the existing capability-flag + default-method pattern (`can_pulse_guide`,
`reports_destination_pier_side`):
- `can_set_tracking_rate: bool = False`
- `async def set_tracking_rate(self, rate: str) -> None` — default raises
  `DeviceError` (like `pulse_guide`); validates `rate` ∈ the three names.
- `async def get_tracking_rate(self) -> str` — default returns `"sidereal"`.

A module-level `TRACKING_RATES = ("sidereal", "lunar", "solar")` tuple is the one
source of the vocabulary (imported by the API validator and backends).

## Backends
- **AM5N** (`devices/backends/zwo_am5.py`): `can_set_tracking_rate = True`; set via
  LX200 `:TQ#` (sidereal) / `:TL#` (lunar) / `:TS#` (solar). The AM5N's rate
  read-back is unreliable, so the driver **caches** the last-set rate and
  `get_tracking_rate` returns the cache (initialised `"sidereal"`). At-scope
  validation confirms the mount ACKs `:TL`/`:TS`; a rate change is inert (no slew),
  so it is safe to test in daylight with tracking off.
- **Alpaca** (`devices/alpaca.py`): `can_set_tracking_rate` derived from the mount's
  advertised `TrackingRates` collection containing lunar+solar; `set` writes the
  `TrackingRate` property (Sidereal=0/Lunar=1/Solar=2), `get` reads it back and maps
  to the name.
- **Sim** (the sim telescope): stores the rate in memory; `can_set_tracking_rate = True`.

## API / status
- **New route** `POST /api/mount/tracking_rate?rate=<name>` in `api/app.py` beside
  `/api/mount/tracking` (line ~2628), gated `CAP_CONTROL_MOUNT`,
  `@declare(CAP_CONTROL_MOUNT, reaches={"Telescope.set_tracking_rate"})`; 422 on an
  unknown rate; `DeviceError` → the usual `_err`.
- **Status** (`hub.py` mount dict, ~2214): add
  `"tracking_rate": await tel.get_tracking_rate()` and
  `"can_set_tracking_rate": tel.can_set_tracking_rate`. Only read when the mount is
  connected (inside the existing try block).

## UI (`ui/src/views/MountView.tsx`)
- `types.ts` `MountState`: add `tracking_rate?: "sidereal" | "lunar" | "solar"` and
  `can_set_tracking_rate?: boolean`.
- A **tri-state segmented pill** (`Sidereal · Lunar · Solar`, XOR) rendered directly
  under the Tracking toggle block (lines 111-121), **only** when
  `m?.can_set_tracking_rate`. Disabled (read-only) when `!canMount`, matching every
  other motion control. Posts `POST /api/mount/tracking_rate?rate=<name>`; optimistic
  active-state from `m.tracking_rate`. Modern, night-mode-safe styling from the
  design system (active segment uses the accent; segments are ≥44px tap targets).
- A small reusable `SegmentedControl` component under `ui/src/components/ui` so the
  pattern is testable and reusable.

## Non-goals
Ephemeris body-following, custom/King rates, per-axis custom rates, and any
NINA-backend rate control (NINA's mount tracking-rate is not exposed by the bridge;
its `can_set_tracking_rate` stays False → control hidden).

## Testing
- Backend: contract default-method behaviour; AM5N sends the right LX200 command per
  rate + caches for `get`; sim round-trips; Alpaca maps enum↔name and gates
  capability off `TrackingRates`. API: 422 on bad rate, cap-gated, reaches-declared.
- UI: `SegmentedControl` XOR behaviour + a11y; MountView shows/hides on capability,
  disables for viewer, posts the right rate.
- At-scope: AM5N ACKs `:TL`/`:TS`; status reflects the change; no unintended slew.
