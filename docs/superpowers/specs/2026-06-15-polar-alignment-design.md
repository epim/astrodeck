# AstroDeck Polar Alignment — Bullseye Reticle (Treatment A)

**Date:** 2026-06-15
**Status:** Approved (user picked treatment A from interactive mockups)

## Purpose

A polar-alignment view: concentric arcminute rings with the mount's pole shown
as a dot, a skew vector giving direction + magnitude of the error, and per-axis
adjustment guidance. Driven by NINA's Three-Point Polar Alignment (TPPA) on the
live rig, with a simulator so it's fully demoable with no sky.

## Data model

A `polar` event / `PolarState`, errors in **arcminutes**:

```
state:       idle | running | paused | done | error
az_error:    signed arcmin   (− = East, + = West)
alt_error:   signed arcmin   (+ = scope too high → lower; − = raise)
total_error: arcmin (hypot)
progress:    0..1
message:     human status ("measuring point 2/3", "adjust the mount", …)
source:      nina | sim
```

## Backend

`server/astrodeck/polar/session.py` — `PolarAlignSession(hub)`, owned by the hub
(`hub.polar`), so a reconnect/disconnect stops any run.

- `start()` dispatches by `hub.mode`: NINA if bridged, else sim.
- **NINA driver**: opens `ws://<host>:<port>/v2/tppa`, sends
  `{"Action":"start-alignment"}`, and translates messages — `AzimuthError`/
  `AltitudeError`/`TotalError` (NINA reports **degrees**; we ×60 → arcmin) and
  `Status`/`Progress` — onto the `polar` event. `stop/pause/resume` send the
  matching `*-alignment` actions.
- **Sim driver**: a brief 3-point "measurement" phase, then a live error that
  converges (with jitter) to <1′ and ends `done` — mirrors the real flow so the
  whole UI is exercisable offline.

`hub.disconnect_all()` stops a running session.

REST (`api/app.py`): `POST /api/polar/{start,stop,pause,resume}`,
`GET /api/polar/state`. Live updates ride the existing `polar` WebSocket event.

## Frontend

- New nav entry **Align**; `PolarView` + a `PolarReticle` SVG component.
- `PolarReticle(az, alt)`: arcminute rings (auto-zoom in as error shrinks),
  green center target, crosshair, ALT±/AZ E·W axis labels, tolerance fills
  (green <1′, amber 1–5′, red >5′), the error dot, and a colored skew vector
  with arrowhead from center to dot.
- Readout: big total error (zone-colored), state + progress, and two rows —
  Azimuth (◀/▶ + direction + arcmin) and Altitude (▲/▼ + arcmin) with the
  knob/scope action. Start / Pause / Stop controls.

## Known caveat (to validate under the stars)

NINA's TPPA error fields carry no explicit E/W·up/down labels and unspecified
units; this design assumes **degrees** (×60 → arcmin) and derives direction from
sign. Both the unit factor and the sign→direction mapping are single constants,
easy to flip once validated against a real TPPA run (the live rig is parked/
capped now, so a full on-sky run isn't possible this session — the `/tppa`
socket reachability is verified instead, and the sim validates the full flow).

## Testing

pytest: sim session converges to <1′ and ends `done`; NINA message parsing
(`0.05°` → `3.0′`, signs → direction). UI via build + live sim run + screenshot.

## Out of scope (seam left)

A native 3-point routine for direct-Alpaca rigs without NINA (rotate in RA,
plate-solve 3 points, solve for pole error) — future; the `PolarAlignSession`
dispatch is the seam.
