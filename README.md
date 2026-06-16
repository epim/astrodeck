# AstroDeck

**An open, vendor-neutral astrophotography controller — a full replacement for
the ASIAIR ecosystem with zero lock-in.**

AstroDeck drives your whole imaging rig — camera, mount, focuser, filter wheel,
guiding, power box, plate solving, polar alignment, and autonomous multi-target
sequencing — from one clean web UI that runs on any tablet, phone, or desktop.
It speaks **ASCOM Alpaca**, the open device protocol supported by ZWO, Pegasus
Astro, QHY, PrimaLuceLab, and every vendor with ASCOM drivers (via ASCOM
Remote). No proprietary box. No app-store gatekeeper. Your gear, your network,
your rules.

![status](https://img.shields.io/badge/status-v0.1-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![tests](https://img.shields.io/badge/tests-251%20passing-brightgreen)

> **Validated on sky.** AstroDeck has been run end-to-end against a live rig —
> a Player One Poseidon-M PRO mono camera, a ZWO AM-series harmonic mount, a ZWO
> EAF, a Wanderer filter wheel, and PHD2 — with capture, site/optics, goto/slew,
> native autofocus, and TPPA polar alignment all working under the stars.

---

## Why AstroDeck

The ASIAIR is a wonderful idea trapped in a closed garden: one vendor's
hardware, one vendor's app, one vendor's roadmap. AstroDeck keeps the
"one tap, whole rig" experience and throws away the walls.

- **Vendor neutral by construction.** Everything above the wire talks to a small
  device abstraction (`Camera`, `Telescope`, `Focuser`, `FilterWheel`, `Switch`,
  `SafetyMonitor`, plus a `Guider`). Backends plug in underneath. Adding INDI or
  a native SDK is implementing a handful of async methods — nothing else changes.
- **The open path is the default.** ASCOM Alpaca is a first-class backend with
  UDP network discovery. Point AstroDeck at your Alpaca server (or ASCOM Remote)
  and assign devices to roles.
- **A real on-ramp for NINA users.** Already imaging with NINA? The NINA bridge
  flies your existing rig through NINA's Advanced API with no driver
  reconfiguration — then you migrate to direct Alpaca device by device. The
  bridge is explicitly a *transition*, not the destination (see below).
- **Works with no hardware at all.** A full deterministic simulator renders a
  real star field that responds to pointing, focus, and filters — so autofocus,
  plate solving, guiding, and sequencing genuinely work offline.

### A note on the NINA bridge

The NINA bridge is a **transition tool**, not where AstroDeck is going. It exists
so people with working NINA rigs can adopt AstroDeck *today* without re-pointing
a single driver. The roadmap is to operate **without** NINA — direct Alpaca plus
native drivers — and, longer term, to reimplement the genuinely useful NINA
engines (autofocus, plate solving, TPPA) in Rust and make them far friendlier.
Think of NINA as the gangway, not the ship.

---

## Features

- **Rig / Connect** — Alpaca UDP network discovery, role-based device
  assignment, one-tap full simulator rig, NINA bridge with subnet discovery,
  and PHD2.
- **Capture** — an overhauled live preview (zoom / pan / fit / 100%, adjustable
  MTF stretch with an interactive histogram, star overlay, clip/saturation mask,
  a frame filmstrip, and a no-flash double-buffered stage), full exposure
  controls, cooler control (on/off + power% + at-target badge), camera dew
  heater, a live guide-camera preview, and single / loop / stop with separate
  exposure and download progress.
- **Focus** — manual jog and absolute goto plus V-curve autofocus (HFR star
  measurement, parabola fit). For backends with their own routine (NINA), it
  delegates to the native autofocus and draws the real V-curve.
- **Mount** — a touch slew pad with fixed-rate control and a move dead-man's
  switch, catalog goto with live altitude and a below-horizon guard,
  sidereal tracking, park/unpark, and plate-solve **Solve & Sync** /
  goto-and-center.
- **Polar alignment** — NINA TPPA over a WebSocket, rendered as a concentric-ring
  bullseye reticle with explicit azimuth/altitude error in arcminutes and a
  live guide-cam view (simulator fallback when NINA isn't present).
- **Guide** — PHD2 (and the NINA guider) with a live RA/Dec error graph, an
  RA-vs-Dec scatter, RMS stats, and dithering.
- **Plan / Sequence** — an autonomous multi-target engine (slew → center →
  autofocus → guide → per-filter exposure loops, with dither, thermal refocus,
  meridian-flip handling, and park/warm when done), plus the automation surface:
  `SafetyMonitor` gating with named presets, autorun scheduling (dusk/dawn/min-alt
  windows with skip-ahead), end-of-night session reports, and
  ntfy / webhook / Telegram alerting with a dead-man's-switch heartbeat.
- **Sky Atlas** — a survey-cutout framing assistant (hips2fits) with a
  draggable/rotatable FOV overlay, a mosaic planner (server-canonical panels),
  and an astropy visibility planner (altitude curve, transit, twilight, moon
  separation, best window).
- **Monitor** — a glanceable live dashboard: ETA, progress, mount state, cooler,
  guiding RMS, meridian countdown, an HFR trend sparkline, and a live thumbnail.
- **Power** — Alpaca `Switch` (Pegasus UPB-style): outputs, dew-heater PWM, and
  voltage/current telemetry.
- **Night mode & touch** — one tap flips the whole UI to dark-adaptation red
  (including a red survey filter over previews), a store-owned brightness dimmer
  with day/night memory, large touch targets, a slide-to-unlock screen guard,
  and reliability chrome (toasts, a reconnect banner, and a log drawer).

> **Rough edges, honestly.** Guiding and plate solving work but still have
> sharp corners; the Settings view is a placeholder (site/optics/safety/alerts
> are edited through the API and the relevant surfaces); live-stacking and a
> flats wizard are not built yet; and there's no Docker or Raspberry-Pi
> packaging yet. See [`docs/overview.md`](docs/overview.md) for the full status.

---

## Quick start

```powershell
# 1. Server (Python 3.11+)
cd server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck            # serves on http://localhost:8800

# 2. UI — only if you change it; a built copy in ui/dist is served by the server
cd ui
npm install
npm run build
```

Open `http://localhost:8800`, hit **Connect Simulator Rig**, and explore. The
sim camera renders a star field that responds to mount pointing, focus position,
and filters, so autofocus, plate solving, and sequencing all genuinely work with
no hardware attached.

For real gear: run your vendor's Alpaca server (or ASCOM Remote on the machine
with the drivers), then **Scan** on the Rig page and tap devices to assign them
to roles. For guiding, start PHD2 with its event server enabled and connect it.

**NINA transition mode:** install the **Advanced API** plugin in NINA (default
port `1888`), then on the Rig page use **NINA Bridge** → **Scan Network** to
auto-discover instances, or enter the host/IP manually. To try it without a NINA
install, run the bundled mock:

```powershell
cd server
.venv\Scripts\python -m tools.mock_nina       # mock NINA on :1888
```

Full walkthrough: [`docs/quickstart.md`](docs/quickstart.md).

---

## Architecture

```
server/  Python · FastAPI · :8800        ui/  React 18 · TypeScript · Tailwind v4 · Zustand
  devices/   Alpaca + NINA + sim backends behind one device abstraction (base.py)
  imaging/   MTF stretch · histogram · star detection / HFR · clip mask · FITS
  focus/     V-curve autofocus (delegates to native AF when a backend has one)
  solve/     ASTAP plugin · sim solver (NINA solves via its own API)
  guide/     PHD2 client · NINA guider · sim guider · dithering
  sequence/  autonomous engine + schedule (autorun) + report (session) + models
  catalog/   coords · objects · survey (hips2fits) · framing (mosaic) · visibility
  polar/     NINA TPPA over a WebSocket (sim fallback)
  config.py  persisted site / optics / safety / alerts (ConfigStore)
  hub.py     the device orchestrator
  api/       FastAPI REST + a WebSocket event bus, and it serves the built UI
```

Everything above `devices/base.py` is vendor-agnostic. The NINA backend
additionally delegates the *smart* operations (autofocus, plate solve, guiding,
TPPA) to NINA through two clean seams: a `supports_native_autofocus` capability
flag and a pre-rendered-frame path on `CameraFrame`. More detail in
[`docs/overview.md`](docs/overview.md) and [`docs/development.md`](docs/development.md).

---

## Development

```powershell
cd server
.venv\Scripts\python -m pytest -q             # 251 tests

cd ui
npm run dev                                   # Vite dev server, proxies to :8800
```

ASTAP is auto-detected for plate solving (set `ASTAP_PATH` to override). Adding a
new device backend means implementing the small async interfaces in
`server/astrodeck/devices/base.py` — see [`docs/development.md`](docs/development.md).

## Documentation

- [`docs/overview.md`](docs/overview.md) — purpose, philosophy, and architecture in depth.
- [`docs/quickstart.md`](docs/quickstart.md) — install, first simulator session, real gear, the NINA bridge.
- [`docs/development.md`](docs/development.md) — dev setup, repo structure, testing, adding a backend.
