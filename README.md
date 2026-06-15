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
- **NINA bridge (transition mode)** — already running [NINA](https://nighttime-imaging.eu/)?
  Point AstroDeck at NINA's Advanced API plugin and fly your existing rig with
  no driver reconfiguration. AstroDeck delegates capture, autofocus,
  plate-solving and guiding to NINA's own routines, then you migrate to direct
  Alpaca device by device

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

### NINA transition mode

If you already image with NINA, install the **Advanced API** plugin in NINA
(enable it; default port 1888), then on the Rig page use **NINA Bridge**.
Click **Scan Network** to auto-discover NINA instances (AstroDeck sweeps your
local subnet for the Advanced API signature and lists each instance with its
NINA version and connected equipment), then click **Bridge** — or enter the
host/IP manually. AstroDeck reflects whatever equipment NINA has connected and
drives it through NINA's API: captures show NINA's stretched frames with its
measured HFR/star counts, the Focus page runs NINA's native autofocus and draws
its real V-curve, and centering uses NINA's plate solver. Slewing is
position-convergence based, so it's robust to ASCOM drivers that report
`Slewing` non-standardly.

To try NINA mode without a NINA install, run the bundled mock — it serves
NINA-shaped responses backed by the simulator star-field:

```powershell
cd server
.venv\Scripts\python -m tools.mock_nina     # mock NINA on :1888
```

Then bridge AstroDeck to `127.0.0.1:1888`.

## Architecture

```
server/  Python · FastAPI       ui/  React · TypeScript · Tailwind v4
  devices/   Alpaca + NINA + simulator backends behind one device abstraction
  imaging/   stretch · histogram · star detection/HFR · FITS
  focus/     V-curve autofocus (delegates to native AF when a backend has one)
  solve/     ASTAP plugin · sim solver (NINA solves via its own API)
  guide/     PHD2 client · NINA guider · sim guider
  sequence/  autonomous plan engine
  api/       REST + WebSocket event bus (serves the built UI)
  tools/     mock_nina.py — a NINA API stand-in for tests and demos
```

Everything above the device layer is vendor-agnostic. Adding INDI or a native
SDK backend means implementing the small interfaces in `devices/base.py`. The
NINA backend additionally delegates the smart operations (autofocus, plate
solve, guiding) to NINA via two clean seams: a `supports_native_autofocus`
capability flag and a pre-rendered-frame path on `CameraFrame`.

## Development

```powershell
cd server; .venv\Scripts\python -m pytest    # 24 tests
cd ui; npm run dev                            # Vite dev server w/ proxy to :8800
```

ASTAP is auto-detected for plate solving (`ASTAP_PATH` env var to override).
