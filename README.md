# AstroDeck

**An open, multi-vendor astrophotography controller — a full replacement for the
ASIAIR ecosystem with zero vendor lock-in.**

AstroDeck drives your whole rig — camera, mount, focuser, filter wheel, guiding,
power box, plate solving, autonomous sequencing — from one beautiful web UI that
works on any tablet, phone, or desktop. It speaks **ASCOM Alpaca**, the open
device protocol supported by ZWO, Pegasus Astro, QHY, PrimaLuceLab, and every
vendor with ASCOM drivers (via ASCOM Remote).

![status](https://img.shields.io/badge/status-v0.1-blue)

## Features

- **Equipment hub** — Alpaca network discovery (UDP), role-based device
  assignment, one-click full simulator rig for offline use
- **Capture** — exposure/gain/offset/binning, single & loop, MTF auto-stretched
  live preview, histogram, FITS library with proper headers
- **Focus** — manual control + V-curve autofocus (HFR star measurement,
  two-pass parabola fit)
- **Mount** — slew pad, catalog goto (Messier + bright NGC/IC with live
  altitude), plate-solve **Solve & Sync**, goto-and-center loop (ASTAP if
  installed, simulator otherwise)
- **Guiding** — PHD2 integration (JSON event socket) or built-in sim guider;
  live RA/Dec error graph, scatter plot, RMS stats, dithering
- **Sequencer** — multi-target plans: slew → center → autofocus → guide →
  filter loops, dither every N frames, refocus cadence, pause/resume/abort,
  park & warm-down when done
- **Power** — Alpaca Switch support (Pegasus UPB-style): outputs, dew heater
  PWM, voltage/current telemetry
- **Night mode** — one tap flips the entire UI to dark-adaptation red,
  including a red filter over image previews

## Quick start

```powershell
# server (Python 3.11+)
cd server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck            # http://localhost:8800

# UI (only needed if you change it — a built copy is served by the server)
cd ui
npm install
npm run build
```

Open `http://localhost:8800`, hit **Connect Simulator Rig**, and explore —
the sim camera renders a real star field that responds to mount pointing,
focus position, and filters, so autofocus / plate solving / sequencing all
genuinely work with no hardware.

For real gear: run your vendor's Alpaca server (or ASCOM Remote on the machine
with the drivers), then **Scan** on the Rig page and click devices to assign
them. For guiding, start PHD2 with its event server enabled and hit
**Connect PHD2**.

## Architecture

```
server/  Python · FastAPI       ui/  React · TypeScript · Tailwind v4
  devices/   Alpaca + simulator backends behind one device abstraction
  imaging/   stretch · histogram · star detection/HFR · FITS
  focus/     V-curve autofocus
  solve/     ASTAP plugin · sim solver
  guide/     PHD2 client · sim guider
  sequence/  autonomous plan engine
  api/       REST + WebSocket event bus (serves the built UI)
```

Everything above the device layer is vendor-agnostic. Adding INDI or a native
SDK backend means implementing five small interfaces in `devices/base.py`.

## Development

```powershell
cd server; .venv\Scripts\python -m pytest    # 24 tests
cd ui; npm run dev                            # Vite dev server w/ proxy to :8800
```

ASTAP is auto-detected for plate solving (`ASTAP_PATH` env var to override).
