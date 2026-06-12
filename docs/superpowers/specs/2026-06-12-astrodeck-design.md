# AstroDeck — Open Multi-Vendor Astrophotography Controller

**Date:** 2026-06-12
**Status:** Approved (autonomous mode — user directed "be autonomous, go go go")

## Purpose

A full replacement for the ZWO ASIAIR ecosystem: a single rich, beautiful, intuitive
controller for an astrophotography rig — camera, mount, focuser, filter wheel, guiding,
power, plate solving, sequencing — **without vendor lock-in**. ZWO, Pegasus Astro, QHY,
PrimaLuceLab, Sky-Watcher… anything.

## Key architectural decision: speak Alpaca, not SDKs

ASIAIR locks you in by talking only to ZWO's proprietary SDK. AstroDeck instead speaks
**ASCOM Alpaca** — the open, cross-platform HTTP/REST device protocol with UDP discovery
(port 32227) that every major vendor supports either natively or via the ASCOM Remote
bridge. One protocol → every camera, mount, focuser, filter wheel, switch (power box),
and weather device on the market.

- **Pegasus Astro** devices (UPBv2/v3, FocusCube) expose Alpaca via Unity/their drivers.
- **ZWO** cameras/EAF/EFW are reachable via ASCOM drivers + Alpaca Remote, or native
  Alpaca on ASIAIR-class hardware.
- **INDI** rigs are reachable via INDI→Alpaca bridges; native INDI is a future backend
  behind the same device interface.

A **full simulator suite** (synthetic star-field camera, mount, focuser with V-curve
response, filter wheel, power box, guider) is built in, so the entire app works with
zero hardware — for development, testing, and demos.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Server | Python 3.12, FastAPI, uvicorn | async-native, astropy/numpy ecosystem for FITS & image math |
| Imaging math | numpy + astropy | FITS I/O, stretch, HFR, debayer |
| Frontend | React 18 + TypeScript + Vite + Tailwind v4 + Zustand | fast, rich, runs on any tablet/phone/desktop browser |
| Transport | REST + one WebSocket event bus | live preview frames, status, sequence progress |
| Guiding | PHD2 JSON event-socket client (port 4400) + built-in sim guider | PHD2 is the industry standard; same approach as NINA |
| Plate solving | Pluggable: ASTAP CLI (auto-detected) / simulator | ASTAP is the standard fast local solver |

## Components

```
astro/
├── server/                    # Python package: astrodeck
│   ├── astrodeck/
│   │   ├── devices/           # device abstraction + backends
│   │   │   ├── base.py        # Camera/Telescope/Focuser/FilterWheel/Switch ABCs
│   │   │   ├── alpaca.py      # Alpaca HTTP client backend + UDP discovery
│   │   │   └── sim.py         # simulator backend (synthetic stars, V-curve focus)
│   │   ├── imaging/           # stretch, histogram, HFR star detection, FITS save
│   │   ├── focus/             # V-curve autofocus routine
│   │   ├── solve/             # plate solver plugins (ASTAP, sim)
│   │   ├── guide/             # PHD2 client + sim guider
│   │   ├── sequence/          # async sequence engine (targets → steps → events)
│   │   ├── catalog/           # Messier + bright NGC catalog, coordinate utils
│   │   ├── hub.py             # equipment hub: profiles, connect/disconnect, state
│   │   ├── events.py          # event bus → WebSocket fanout
│   │   └── api/               # FastAPI routers, WS endpoint, static UI serving
│   └── tests/                 # pytest: sequencer, devices, imaging math
└── ui/                        # Vite + React + TS
    └── src/
        ├── views/             # Capture, Focus, Mount, Guide, Sequence, Power, Connect, Settings
        ├── components/        # histogram, HFR curve, guide graph, slew pad, night-mode shell
        ├── store.ts           # Zustand state fed by WS
        └── api.ts             # REST + WS client
```

## Behaviour (what it does)

1. **Connect** — discover Alpaca devices on the network (UDP) or add manually; pick
   per-role devices (camera, mount, focuser, wheel, switch, guider); save as equipment
   profiles. One-click "Simulator rig".
2. **Capture** — exposure/gain/offset/binning controls, loop & single shot, auto-stretched
   live preview (PNG over WS/HTTP), histogram, FITS saved to library with proper headers.
3. **Focus** — manual focuser control + V-curve autofocus: step through positions,
   measure HFR via star detection, fit parabola, drive to minimum.
4. **Mount** — slew pad (N/S/E/W + rates), goto from catalog (Messier/NGC search),
   coordinates display, tracking toggle, park/unpark, plate-solve → sync ("center here").
5. **Guide** — connect PHD2 (or sim guider), start/stop guiding, dither, live RA/Dec
   error graph, RMS stats.
6. **Sequence** — plan targets: per-target slew→center→focus→loop(filter, exposure ×N),
   dither every N frames, autofocus every N frames/°C, pause/resume/abort, progress %,
   estimated completion. Runs fully autonomously.
7. **Power** — Alpaca Switch UI (Pegasus UPB et al.): toggle ports, dew heater PWM,
   voltage/current readouts.
8. **Night mode** — deep-red UI theme toggle, one tap.

## Data flow

UI ⇄ REST for commands/queries; server pushes `status`, `preview`, `sequence`,
`guide`, `focus`, `log` events over one WebSocket. Device I/O is async; long operations
(exposures, slews, autofocus, sequences) run as cancellable asyncio tasks publishing
progress events.

## Error handling

- Device calls wrapped: failures → event-bus `log` (error level) + API 4xx/5xx with
  detail; never crash the hub.
- Sequence engine: per-step retry policy (configurable), abort-safe (camera stop,
  mount tracking preserved), meridian-flip awareness deferred to v2 (guarded TODO).
- WS reconnect with backoff in UI; stale-state indicators.

## Testing

pytest on the server core: simulator devices, HFR math, stretch math, autofocus
convergence on the sim V-curve, sequence engine ordering/cancellation, catalog lookups.
UI verified by production build + manual run; server smoke-tested by launching uvicorn
and hitting REST endpoints.

## Out of scope (v1)

Live stacking, polar-alignment routine, meridian flip automation, INDI native backend,
multi-camera rigs, mosaics planner. Architecture leaves clean seams for all of these.
