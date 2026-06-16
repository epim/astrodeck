# AstroDeck — Development Guide

This guide covers the dev environment, the repository layout, testing, and the
single most useful extension point: adding a new device backend.

If you haven't yet, read [`overview.md`](overview.md) for the architecture and
the design philosophy first.

---

## Dev environment

### Server (Python 3.11+)

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"        # installs pytest + pytest-asyncio too
.venv\Scripts\python -m astrodeck            # http://localhost:8800
```

The `[dev]` extra adds the test dependencies. Runtime dependencies (declared in
`server/pyproject.toml`) are FastAPI, uvicorn, numpy, astropy, httpx, and pillow.

### UI (Node 18+ / npm)

```powershell
cd ui
npm install
npm run dev                 # Vite dev server with a proxy to the API
```

`npm run dev` serves the UI with hot reload and proxies `/api` and `/ws` to
`http://127.0.0.1:8800` (see `ui/vite.config.ts`), so run the Python server
alongside it. For a production bundle that the server serves directly:

```powershell
npm run build               # tsc -b && vite build → ui/dist
```

The server serves `ui/dist` as a single-page app (`api/app.py` mounts `/assets`
and falls back to `index.html`).

---

## Repository structure

```
astro/
├─ README.md                     front door
├─ docs/                         overview · quickstart · development (this file)
├─ captures/                     saved FITS / preview output (runtime)
├─ server/                       Python · FastAPI · port 8800
│  ├─ pyproject.toml             package + deps + pytest config
│  ├─ astrodeck/
│  │  ├─ __main__.py             `python -m astrodeck` entrypoint (uvicorn)
│  │  ├─ hub.py                  device orchestrator + status aggregator
│  │  ├─ config.py               ConfigStore: site / optics / safety / alerts
│  │  ├─ events.py               in-process event bus (publish over the WS)
│  │  ├─ persist.py              atomic JSON read/write helpers
│  │  ├─ plans.py                plan library (save/load/export/import)
│  │  ├─ profiles.py             connection profiles (replay a rig setup)
│  │  ├─ alerting.py             ntfy / webhook / Telegram + dead-man's-switch
│  │  ├─ api/
│  │  │  └─ app.py               FastAPI: REST surface + WS event bus + SPA host
│  │  ├─ devices/
│  │  │  ├─ base.py              ★ the vendor-neutral device abstraction
│  │  │  ├─ alpaca.py            ASCOM Alpaca backend + UDP discovery
│  │  │  ├─ nina.py              NINA Advanced API bridge + subnet discovery
│  │  │  └─ sim.py               deterministic simulator rig
│  │  ├─ imaging/                stretch · histogram · stars/HFR · FITS
│  │  ├─ focus/                  V-curve autofocus + native delegation
│  │  ├─ solve/                  ASTAP + sim plate solvers (base.py interface)
│  │  ├─ guide/                  PHD2 · NINA guider · sim guider (base.py)
│  │  ├─ sequence/               engine · schedule · report · models
│  │  ├─ catalog/                coords · objects · survey · framing · visibility
│  │  └─ polar/                  NINA TPPA session (sim fallback)
│  ├─ tools/
│  │  └─ mock_nina.py            NINA Advanced API stand-in for tests/demos
│  └─ tests/                     25 pytest files, 251 tests
└─ ui/                           React 18 · TypeScript · Tailwind v4 · Zustand
   ├─ vite.config.ts             dev proxy to :8800
   ├─ dist/                      built bundle (served by the server)
   └─ src/
      ├─ App.tsx                 nav, route gating, brightness/lock chrome
      ├─ main.tsx                React entry
      ├─ store.ts                Zustand store + narrow selector hooks
      ├─ types.ts                domain model (ViewName, RigStatus, …)
      ├─ ws.ts                   WebSocket event bus client
      ├─ api.ts                  REST client
      ├─ views/                  one component per surface (10 files; Settings is
      │                          a placeholder rendered in App.tsx)
      ├─ components/             reusable chrome
      │  ├─ preview/             the live-preview pipeline (stage, toolbar,
      │  │                       histogram, filmstrip, overlays, gestures)
      │  ├─ atlas/               SkyCanvas, FovOverlay, VisibilityPanel, …
      │  └─ …                    SlewPad, Toasts, LogDrawer, TouchGuard, …
      └─ lib/                    eta · framing · slewController · preflight ·
                                 haptics · optics · visibility · …
```

The `★` line — `devices/base.py` — is the seam everything else is built on.

---

## How a request flows

1. The UI issues a REST command (`ui/src/api.ts` → `api/app.py`).
2. Quick reads answer inline. Long operations (slew, autofocus, sequence,
   centering) start a **named background task** via `_spawn(...)` and return
   immediately — one task per name, so a second start while one is running
   returns `409`.
3. The task drives the **Hub**, which calls the role-assigned device through the
   `devices/base.py` interface — the backend (Alpaca / NINA / sim) does the work.
4. Progress and state are published on the **event bus** (`events.py`) and
   streamed to every browser over the single `/ws` WebSocket. On connect, the
   server sends a `hello` snapshot (`hub.summary()`).
5. The UI's `ws.ts` feeds events into `store.handleEvent(...)`, mutating exactly
   the Zustand slice involved; **narrow selector hooks** ensure only the
   components subscribed to that slice re-render.

This is why a 1 Hz guide tick doesn't re-render the whole tree, and why the UI is
robust to reconnects — the server is authoritative and re-pushes state.

---

## Testing

### Backend (pytest)

```powershell
cd server
.venv\Scripts\python -m pytest -q            # 251 tests across 25 files
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so async tests need no decorator.
The suite covers, among others:

- `test_sim_devices.py` — simulator device lifecycle
- `test_nina.py` — the NINA bridge, exercised against `tools/mock_nina.py`
- `test_autofocus.py` — V-curve fitting + native-AF delegation
- `test_imaging.py` — stretch, histogram, encode
- `test_sequence.py`, `test_engine_safety.py` — engine state machine + the
  fail-closed safety gate (stale reading ⇒ unsafe)
- `test_schedule.py`, `test_report.py` — autorun windows + session reports
- `test_framing.py`, `test_visibility.py`, `test_survey.py` — the Sky Atlas
- `test_config.py`, `test_config_automation.py`, `test_persist.py`,
  `test_profiles.py`, `test_plans.py` — config/persistence/versioning
- `test_polar.py`, `test_guide_frame.py`, `test_move_watchdog.py`,
  `test_monitor_telemetry.py`, `test_hub_solve.py`, `test_automation_api.py`,
  `test_alerting.py`, `test_app_preflight.py`, `test_catalog.py`

**`tools/mock_nina.py`** is worth knowing: it's a FastAPI app that wraps the
simulator rig and serves NINA-Advanced-API-shaped responses (version, camera
capture, autofocus, slew, guider, TPPA WebSocket) on port 1888. It backs the
NINA tests and lets you run the real NINA bridge with no NINA install
(`python -m tools.mock_nina`).

### Frontend

The UI has test files under `ui/src/__tests__/` and `ui/src/lib/__tests__/`
(`eta`, `framing`, `slewController`, `preflight-gate`, `haptics`, `store`, etc.).
They currently use a lightweight in-repo harness — **vitest/jest are not wired
in yet** — so there is no `npm test` script. Type checking via `tsc -b` (part of
`npm run build`) is the standing frontend gate; wiring a proper test runner is a
good first contribution.

---

## Adding a device backend

This is the highest-leverage thing you can build, and the architecture is
designed for it: **implement the interfaces in `server/astrodeck/devices/base.py`
and nothing above the device layer changes.**

### 1. Implement the interfaces

Create a module under `server/astrodeck/devices/` (e.g. `indi.py`) with classes
that subclass the relevant abstract devices. Each device is async and must be
cancellation-safe:

```python
from .base import Camera, CameraFrame, DeviceError

class IndiCamera(Camera):
    kind = "camera"

    async def connect(self) -> None:
        ...
        self.connected = True

    async def disconnect(self) -> None:
        ...
        self.connected = False

    async def expose(self, seconds, gain, offset, binning=1,
                     light=True, save=False, target="") -> CameraFrame:
        data = ...  # 2D uint16 numpy array
        return CameraFrame(
            data=data, exposure_s=seconds, gain=gain, offset=offset,
            binning=binning, bayer_pattern=self.bayer_pattern,
            temperature_c=await self.get_temperature(),
            timestamp=time.time(),
            full_well=...,            # drives the clip mask honestly; None to disable
            data_is_linear=True,      # raw sensor data ⇒ hub stretches it
        )

    async def abort_exposure(self) -> None:
        ...
```

Implement whichever of `Camera`, `Telescope`, `Focuser`, `FilterWheel`,
`Switch`, `SafetyMonitor` (and the `Guider` in `guide/base.py`) your backend
covers. You don't have to implement all of them.

### 2. Know the two clean seams

These let "dumb sensor" and "smart pre-rendered" backends share one path:

- **`CameraFrame.data_is_linear`** — `True` for raw sensor data (sim, Alpaca):
  the hub stretches and encodes it, and the linear histogram + clip mask are
  active. Set `False` if you can only return a pre-rendered image: populate
  `rendered_bytes` + `rendered_mime`, carry your measured `hfr`/`stars`, and the
  preview will use your image verbatim (no wrong overlays). This is exactly how
  the NINA backend works.
- **`Focuser.supports_native_autofocus`** — set `True` and implement
  `async def native_autofocus(self) -> dict` to let `focus.run_autofocus`
  delegate to your hardware's own routine instead of running its own V-curve
  sweep. Return `{success, best_position, best_hfr, points: [{position, hfr}],
  message}`.

Other capability flags to be honest about: `Camera.can_cool` /
`has_dew_heater`, `Telescope.reports_destination_pier_side` (gates the GEM
pier-collision guard), and the `SafetyMonitor` `stale`-as-unsafe contract.

### 3. Wire it into the hub / API

Devices are constructed by the connection endpoints in `api/app.py` (look at how
`/api/connect/alpaca`, `/api/connect/sim`, and `/api/connect/nina` build their
devices) and assigned to roles on the **Hub**. Add a connect path and a discovery
endpoint if your transport supports discovery (Alpaca's UDP and NINA's subnet
sweep are the two examples).

### 4. Test it against the harness

Model your tests on `tests/test_sim_devices.py` (and `test_nina.py` for a
bridge-style backend). Because the rest of AstroDeck only sees `base.py`, a
backend that satisfies the interfaces and passes those shapes "just works" in
capture, focus, mount, guide, and the sequence engine.

---

## Conventions worth respecting

- **Longitude is signed, East-positive** everywhere (`config.py`, `coords.py`).
  The UI collects magnitude + E/W and converts at the boundary. Flipping this
  silently breaks transit and the polar compass.
- **Fail closed on safety.** A stale or timed-out `SafetyReading` is unsafe, not
  safe. The engine's safety gate depends on this.
- **The store is the single writer** of the brightness CSS variables and the
  plan/framing state in the UI; mutate through the store actions, not directly.
- **One constant, mirrored, not duplicated logic.** `ARCSEC_PER_RAD = 206.265`
  lives in `config.py` and is mirrored in `ui/src/lib/optics.ts`; share the
  constant, never fork the math.
- **Long operations are named background tasks** (`_spawn`) that stream progress
  over the WS — never block a request on hardware.

---

See [`overview.md`](overview.md) for the architecture rationale and
[`quickstart.md`](quickstart.md) for running against the simulator, real Alpaca
gear, or the NINA bridge.
