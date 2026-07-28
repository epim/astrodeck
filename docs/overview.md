# AstroDeck — Overview

AstroDeck is an open, vendor-neutral, web-based controller for an
astrophotography rig. It runs your camera, mount, focuser, filter wheel,
guiding, power box, plate solving, polar alignment, and a complete autonomous
sequencing engine from a single web UI that works on any tablet, phone, or
desktop on your network.

This document goes deeper than the README: it explains *why* AstroDeck is built
the way it is, the architecture that makes vendor neutrality real, and where the
project is honestly at today.

---

## Philosophy

### Vendor neutrality is the whole point

A single "control my whole rig from a tablet" surface is the right shape for
this job. AstroDeck aims at that experience while staying open: any vendor's
hardware, any device protocol we can speak, and a codebase you can read and
change.

That principle is not a slogan — it is enforced in the code. Every part of
AstroDeck above the wire talks only to a small set of abstract device interfaces
(`server/astrodeck/devices/base.py`). Capture, focus, sequencing, the API, and
the UI never know whether a camera is an Alpaca device, a NINA-bridged camera, or
the simulator. A backend is a self-contained adapter that implements those
interfaces; adding one changes nothing else.

### The open path is the default; NINA is the on-ramp

AstroDeck ships three backends today:

1. **ASCOM Alpaca** — the open path. Network-discoverable over UDP, vendor-broad
   (ZWO, Pegasus, QHY, PrimaLuceLab, and anything reachable through ASCOM
   Remote). This is the destination.
2. **NINA Advanced API bridge** — the transition. It flies an *existing* NINA
   rig with no driver reconfiguration, delegating the smart operations to NINA's
   own engines. It is explicitly temporary.
3. **Simulator** — a deterministic virtual rig for development, demos, and
   offline practice. It renders a coherent star field so the full pipeline
   genuinely runs with no hardware.

AstroDeck supports two equally valid setups, and neither is a stage on the way
to the other.

Running **alongside NINA** flies an existing rig through NINA's Advanced API,
with no drivers re-pointed and no profiles rebuilt. Someone who likes their NINA
setup should be able to keep it and still get the touch UI, the multi-night
session ledger, the Atlas planner, remote access and the phone dashboard.

Running **standalone** talks straight to hardware over Alpaca and native
drivers, using AstroDeck's own engines for autofocus, star detection, plate
solving, polar alignment and guiding (implemented in Rust from audited algorithm
dossiers, not ported code). This is what lets a rig run with no other software
installed.

Both are maintained. Which one suits you depends on the rig in front of you, and
you can change your mind later.

### Honest software for a technical audience

AstroDeck is built for astrophotographers and developers who know what an HFR
curve, a meridian flip, and a fail-closed safety monitor are. The UI favors
glanceable truth over decoration, the engine fails closed on safety, and the
docs (including this one) call out rough edges rather than hiding them.

---

## Architecture

### The big picture

```
        ┌─────────────────────────────────────────────────────────────┐
        │  Browser UI  (React 18 · TS · Tailwind v4 · Zustand)          │
        │  views/ + components/ + a single Zustand store                │
        └───────────────┬──────────────────────────┬──────────────────┘
                  REST commands                WebSocket event stream
                        │                          │
        ┌───────────────▼──────────────────────────▼──────────────────┐
        │  FastAPI app  (server/astrodeck/api/app.py)  ·  port 8800     │
        │  • REST command surface   • WS event bus   • serves ui/dist   │
        └───────────────┬──────────────────────────────────────────────┘
                        │
        ┌───────────────▼──────────────────────────────────────────────┐
        │  Hub (hub.py) — device orchestrator + status aggregator        │
        │  SequenceEngine · PolarAlignSession · ConfigStore · EventBus   │
        └───────────────┬──────────────────────────────────────────────┘
                        │  vendor-neutral device abstraction (base.py)
        ┌───────────────▼──────────────────────────────────────────────┐
        │   Alpaca backend  │  NINA bridge  │  Simulator                 │
        │   (UDP discovery, │  (Advanced    │  (deterministic            │
        │    REST/ImageBytes)│   API + TPPA WS) │   star field)           │
        └───────────────────────────────────────────────────────────────┘
```

The server is the single authority. The UI is a thin, event-driven view: it
issues REST commands for actions and renders state pushed over one WebSocket.
Long operations (slews, autofocus, sequences, centering) run as named background
tasks on the server and stream progress; the UI never blocks on them.

### The device abstraction (`devices/base.py`)

This file is the load-bearing seam. It defines:

- **`Device`** — common lifecycle (`connect`/`disconnect`, `describe()`) plus
  connection identity (host/port/dev_type/dev_num/role/backend) so a saved
  profile can replay a connection intent without a live handle.
- **`Camera`** — `expose(...)` returning a `CameraFrame`, `abort_exposure()`,
  optional cooler and dew-heater control, capability metadata (sensor size,
  pixel size, `can_cool`, bayer pattern).
- **`Telescope`** — position, slew, sync, tracking, park/unpark, `move_axis`
  (manual motion), pier-side and destination-pier-side (the GEM collision
  guard), and `time_to_meridian_flip`.
- **`Focuser`** — absolute moves, halt, temperature, and a
  `supports_native_autofocus` flag with a `native_autofocus()` delegation hook.
- **`FilterWheel`** — slot control plus `filter_names` and per-filter focuser
  `filter_offsets`.
- **`Switch`** — power boxes and dew heaters as a list of `SwitchPort`s
  (boolean outputs and PWM dimmers with min/max/unit).
- **`SafetyMonitor`** — `is_safe()` wrapped into a `SafetyReading`. A read that
  times out or disconnects is marked `stale`, and the engine treats stale as
  **unsafe** (fail-closed) — never as safe.
- **`Guider`** (`guide/base.py`) — start/stop, dither, `GuideStats` (RMS in
  arcsec, SNR, recent samples), and an optional guide-star thumbnail.

The clever part is **`CameraFrame`**. Linear backends (sim, Alpaca) fill in raw
`uint16` `data` and let the hub stretch and encode it for display. A backend that
can only return a pre-rendered image — NINA serves an auto-stretched PNG/JPEG
plus measured statistics — fills in `rendered_bytes`, carries NINA's `hfr` and
`stars`, and sets `data_is_linear = False`. The preview pipeline gates the linear
histogram and the clip mask on that flag, so a NINA frame never gets a wrong
overlay. This single dataclass is what lets a "dumb sensor" backend and a "smart
pre-rendered" backend share one display path.

### Backends in detail

**Alpaca** (`devices/alpaca.py`). Discovery is the standard ASCOM Alpaca UDP
broadcast on port **32227** (`alpacadiscovery1`), followed by a query to
`/management/v1/configureddevices`. Manual host/port entry is guarded against
SSRF and DNS rebinding (`validate_scan_host` rejects loopback/private/link-local
addresses). The camera downloads frames via the binary ImageBytes protocol with
a JSON fallback, probes `MaxADU` to set an honest saturation level for the clip
mask, and the telescope probes `DestinationSideOfPier` once so the pier-limit UI
only appears on mounts that actually report it.

**NINA bridge** (`devices/nina.py`). Talks to NINA's Advanced API over REST at
`http://host:1888/v2/api` and a WebSocket for TPPA. It reflects whatever
equipment NINA has connected and drives it through NINA's routines: captures show
NINA's stretched frames with its measured HFR/star counts, the focuser reports
`supports_native_autofocus = True` and delegates to NINA's autofocus (returning
the real V-curve), and centering uses NINA's plate solver. Slewing is
position-convergence based (poll until the reported position settles), which is
robust to ASCOM drivers that report `Slewing` non-standardly. Disconnecting
AstroDeck never disconnects NINA's own equipment.

**Simulator** (`devices/sim.py`). A shared `SimRig` holds coherent state — RA/Dec,
tracking, focuser position with a defined best-focus, filter slot, a pointing
error that resolves on sync, and a sensor temperature. `SimCamera` renders a
deterministic synthetic star field whose sharpness varies with focuser distance
from best focus and includes Poisson and read noise, plus a believable cooler
power model. The result is that autofocus, plate solving, centering, guiding, and
full sequencing all run end-to-end against the sim exactly as they do on
hardware.

### Imaging pipeline (`imaging/`)

`processing.py` implements the display pipeline: an MTF (midtones transfer
function) stretch — the same screen-stretch family PixInsight and NINA use — with
auto-derived black/mid/white levels, a linear 16-bit histogram and a display-domain
histogram for the interactive handles, and PNG/JPEG encoders. `stars.py` does
background-subtracted star detection and flux-weighted HFR measurement, emitting a
compact overlay payload (capped) with eccentricity/angle for unsaturated stars.
`fitsio.py` writes proper FITS with standard headers (EXPTIME, GAIN, XBINNING,
CCD-TEMP, BAYERPAT, OBJECT, FILTER, RA/DEC, etc.).

### Focus, solve, guide

- **Focus** (`focus/autofocus.py`) — if the focuser supports native autofocus, it
  delegates; otherwise it sweeps the focuser, measures median HFR at each step,
  fits a parabola, and moves to the minimum (approaching from below for backlash
  consistency). Either way it publishes `focus` events with the points and best.
- **Solve** (`solve/`) — a `PlateSolver` interface with an ASTAP CLI backend
  (auto-detected; `ASTAP_PATH` override) and a simulator solver. NINA solves
  through its own API.
- **Guide** (`guide/`) — a PHD2 JSON-socket client (RMS in arcsec, SNR, settle and
  dither support, a rolling sample window, and a guide-star thumbnail), a NINA
  guider that drives PHD2 through NINA's endpoints, and a sim guider that
  generates a believable error stream with dither kicks.

### Sequencing & automation (`sequence/`)

The autonomous engine (`engine.py`) runs multi-target plans: per target it slews,
optionally centers via plate solve, runs autofocus, starts guiding, applies
per-filter focuser offsets, and loops the exposures — handling dither cadence,
thermal refocus, meridian-flip timing, frame-quality rejection (HFR), and
park/warm-down at the end. It carries a unified, fail-closed safety gate, a
mount-altitude floor, a live and honest ETA, a no-progress watchdog, and
crash-resume (it persists progress so a restart can keep appending to the same
session).

Around the engine:

- **`schedule.py`** — pure, unit-tested autorun-window resolution: sun-altitude
  math, dusk/dawn event finding, horizon-profile interpolation, and the
  distinction between a per-target minimum altitude and a per-mount safety floor.
- **`report.py`** — an append-only session report with per-target and per-filter
  integration breakdowns, accepted/rejected counts, median HFR, safety events,
  and end reason, snapshotted to JSON without blocking the loop.
- **`models.py`** — the Pydantic plan model (`SequencePlan`, `Target`,
  `ExposureStep`, `Schedule`) with validation bounds.

Safety presets (e.g. *Backyard* / *Remote*), escalation rules, and alert sinks
(ntfy / webhook / Telegram) plus a dead-man's-switch heartbeat live in
`config.py` and `alerting.py`.

### Sky Atlas (`catalog/`)

- **`coords.py`** — dependency-free RA/Dec parsing/formatting, local sidereal
  time (East-positive longitude), and alt/az.
- **`survey.py`** — a hips2fits proxy (`/api/survey/cutout.jpg`) with a disk cache
  and a graceful 503 → schematic fallback, so the framing canvas shows a real sky
  survey cutout.
- **`framing.py`** — the mosaic engine: inverse-gnomonic deprojection and
  boustrophedon (snake-order) panel generation, computed server-side so the
  panels the sequencer slews to are authoritative.
- **`visibility.py`** — an astropy-backed visibility planner: tonight's altitude
  curve, transit, twilight grades, moon illumination/phase/separation, and the
  best observing window.

### Polar alignment (`polar/session.py`)

`PolarAlignSession` drives NINA's TPPA (three-point polar alignment) over a
WebSocket, converting NINA's degree errors to arcminutes and publishing `polar`
events (azimuth/altitude/total error, progress, message). When NINA isn't
present it falls back to a believable simulator. The UI renders this as a
concentric-ring bullseye reticle with explicit knob-direction guidance.

### Configuration (`config.py`)

One atomic JSON file (`server/config/astrodeck.json`) is the single source of
truth for the observing **site** (signed, East-positive longitude — load-bearing
for every transit and polar calculation), the **optics** (focal length and pixel
size that seed the plate-solver FOV hint and the framing overlay), and the
automation config (safety presets, escalation, alert sinks). Writes are atomic
with optimistic-concurrency versioning. A site stays flagged `is_default` until
the user saves a real one, which keeps the below-horizon goto guard inert until
there's a real location to guard against. Secrets (an optional Telegram token)
are redacted before config crosses the wire.

### The UI (`ui/src/`)

React 18 + TypeScript + Tailwind v4, state in a single Zustand store with **narrow
selector hooks** so a high-frequency event (a guide tick) re-renders only its
consumers, not the tree. The eleven surfaces (Rig, Align, Mount, Focus, Capture,
Guide, Atlas, Plan, Power, Monitor, Settings) are driven by one WebSocket event
bus. The store also owns the brightness dimmer (a single writer of the CSS
variables) and night mode, which tints both the UI and the image previews red for
dark adaptation and switches the Atlas survey to a red DSS2 layer.

---

## Project status (honest)

**Solid and validated.** Connect/discovery, simulator, capture with the live
preview overhaul, focus (manual + V-curve + native delegation), mount control,
TPPA polar alignment, the Sky Atlas (framing + mosaic + visibility), the
autonomous sequence engine with safety/autorun/reporting/alerting, the Monitor
dashboard, and Power are all built and exercised by 251 backend tests plus a
frontend test suite. A full session has been run on real hardware.

**Rough edges.** Guiding and plate solving work but still have sharp corners.

**Not built yet.** The Settings view is a placeholder (site, optics, safety
presets, and alerts are configured via the API and the surfaces that need them);
a Session Report view is stubbed (reports are generated and downloadable via the
API). Live-stacking and a flats/calibration wizard do not exist yet. There is no
Docker image and no Raspberry-Pi packaging yet.

See [`development.md`](development.md) to dig into the code or add a backend, and
[`quickstart.md`](quickstart.md) to get running.
