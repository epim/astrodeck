# AstroDeck

**One browser tab runs the whole observatory. Yours to own, yours to change.**

Point a phone at your rig and AstroDeck slews, centres, focuses, guides, shoots,
dithers, flips the meridian, watches the weather, parks at dawn and writes you a
report. It runs on a mini-PC or a Pi at the scope. You open a page.

[**Overview and documentation →**](https://epim.github.io/astrodeck/)

![version](https://img.shields.io/badge/version-0.3.41-4DD9E8)
![python](https://img.shields.io/badge/python-3.11%2B-4DD9E8)
![platforms](https://img.shields.io/badge/runs%20on-Windows%20%C2%B7%20Linux%20%C2%B7%20macOS%20%C2%B7%20Pi-4DD9E8)
![licence](https://img.shields.io/badge/licence-Apache--2.0-8A97AE)

> **Under real stars.** Recorded device checks include a Player One Poseidon-M Pro,
> a ZWO AM5N mount, a ZWO EAF, a Wanderer filter wheel and a ZWO guide camera.
> The [hardware page](https://epim.github.io/astrodeck/hardware.html) says what was
> checked. Driver support is not a claim that every control or unattended night
> has been validated.

---

<a id="try-it-in-two-minutes-with-no-hardware"></a>

## Try it with no hardware

From a source checkout, build the UI with Node.js 24 before starting Python:

```powershell
cd ui
npm ci
npm run build
cd ../server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck            # http://localhost:8800
```

Open **Equipment** in the classic interface, then press **Simulator**.
For a numbered first-light walkthrough in the alternative interface, use
[getting started](docs/guide/getting-started.md). The bare root opens classic;
`/#/next` opens the alternative interface.

The simulator renders a real star field that answers to where the mount points,
where the focuser sits and which filter is in the light path. The simulator lets you exercise imaging and planning without hardware. Native
autofocus and native guiding need `astrodeck_native`, which published releases
do not include yet (#630); standalone ASTAP solving needs a separate executable
and star database. Put the blackout filter in and the
frame goes dark, because the simulator knows what a blackout filter does.

You can learn the whole app on a cloudy Tuesday and be fluent before your next
clear night.

---

## What you get

**Capture.** Live preview with zoom, pan and a 1:1 loupe. Interactive histogram
and stretch, star overlay, clip mask, frame filmstrip. Cooler with an at-target
badge, dew heater, live guide view.

**Focus.** V-curve autofocus against real HFR measurements, hyperbola fit with
an R² you can see. Per-filter offsets. Refocus on temperature drift or frame
count. Bahtinov mask aid with a live spike overlay.

**Mount.** Touch slew pad with a dead-man's switch. Catalog goto with live
altitude and a below-horizon guard. Plate solve and sync. Park, unpark, tracking
rates, and a meridian flip that re-reads the pier side afterwards instead of
believing the mount's first answer.

**Guiding.** Native guide engine when installed, or PHD2, or NINA's. RA/Dec error graph,
scatter plot, RMS, dithering. A Guiding Assistant measures your seeing and Dec
backlash and suggests settings you are free to ignore.

**Planning.** Sky Atlas: a pan-and-zoom WebGL survey map with a draggable,
rotatable field-of-view overlay, mosaic planner, and altitude curves with
transit, twilight and moon separation. Tonight ranks what is up right now,
tagged by difficulty, so you always have somewhere to point.

**Astro Flows.** Planning a complicated night is the part that usually needs a
spreadsheet and a lot of squinting. Draw it instead. Drag a target pool onto the
canvas, wire a cloud watcher into a hold, hang a refocus off a bad frame, and
the shape of the night becomes something you can see and reason about. A month
of imaging takes about as long to lay out as one night. Before anything moves,
the canvas tells you which of your rules it cannot run, so you find out at your
desk instead of at 3am.

**Weather awareness.** Forecast from Open-Meteo or Astrospheric, and a sky dome
that projects GOES satellite cloud estimates within its coverage footprint.
The layer reports stale or unavailable data and can help compare directions and
short-term cloud motion. It is advisory, not a guarantee of clear sky.

**Multi-night sessions.** Set a target once and keep adding to it across as
many nights and reboots as it takes. AstroDeck scores every frame for HFR, star
count and guide RMS when available, and you can review frame grades. An armed
session can resume remaining work in its next observing window after its
restart checks pass. You never re-count what you already shot.

**Data out.** FITS originals, optional WCS stamping after a solve, and an
end-of-night report. Download selected originals from the gallery. The stacking
bundle supplies a manifest and build script for PixInsight, Siril or APP layouts;
the photos stay on the rig until you download them.

**Watching from bed.** Monitor dashboard with ETA, progress, cooler, guide RMS,
meridian countdown, HFR trend and a live thumbnail. One tap turns the entire
interface dark-adaptation red, with day and night brightness remembered
separately.

**Sharing the rig.** Viewer, syncer, operator and admin roles gate capabilities.
Google sign-in or local accounts, viewer accounts for watching, and precise site
coordinates that stay admin-only. Remote access dials one outbound connection to
a forward-only relay, so there is nothing to port-forward, and every tunnelled
request is re-authenticated at home.

**Staying current.** The supervised source-release path verifies Ed25519
signatures, checks whether it can apply an update, and supports health-probe
rollback. Standalone executables use a separate download-and-replace path.
Release signing is distinct from operating-system executable code signing.

---

## Standalone, or alongside what you already run

AstroDeck can control a standalone rig through supported drivers. It supplies
imaging, planning and sequencing, with task providers for focus, solving,
polar alignment and guiding. Native focus/guiding need the optional native
engine; ASTAP is a separate install.

It also plays well with others. If you already have a setup you like, point
AstroDeck at it and keep everything where it is.

| Route | What drives the hardware | What AstroDeck adds |
|---|---|---|
| **Standalone** | AstroDeck, via native drivers or ASCOM Alpaca | Imaging, planning and automation with available devices and task providers |
| **Alongside NINA** | NINA, via its Advanced API (port `1888`) | Touch UI, Flows, session logs, Sky Atlas, weather model, remote access, phone dashboard |
| **Alongside ASIAIR** | The ASIAIR app retains capture and guiding | Atlas planning and weather; optional experimental backend described below |

Use AstroDeck for the parts where it helps and keep whatever already works.
The backend capability sets differ; the ASIAIR integration is experimental.

### Feature matrix

Where a feature depends on the route you chose, this is what each one does.

| | Standalone | With NINA | Alongside ASIAIR |
|---|---|---|---|
| Hardware control | Implemented native or Alpaca device capabilities | Advanced API device/task offers | Keep control in ASIAIR; optional backend unverified on hardware |
| Guiding | Native engine when installed, or PHD2 | NINA guider path | Keep guiding in ASIAIR |
| Plate solving | Separately installed ASTAP | ASTAP or available NINA provider | Planning does not require taking over solving |
| Flows and sessions | Requires the selected devices and providers | Requires available bridge capabilities | No unattended-control parity claim |
| Atlas and weather | Available with required site/data configuration | Same planning tools | Same planning tools |
| Monitor and access roles | AstroDeck state and account capabilities | AstroDeck's bridged state | Planning use does not imply box telemetry parity |

The optional ASIAIR backend has fake-transport tests, not real-hardware
validation. Filter-wheel and rotator control are not established. It needs
one extra install step,
`pip install -e .[asiair]`, which pulls in the MIT-licensed
[libasi](https://github.com/epim/libasi). Skip it and the backend is absent.

The backend includes a busy-state guard. That implementation is not a
substitute for validation against a real ASIAIR.

---

## Hardware

**Native drivers.** These use vendor SDKs or serial protocols without an ASCOM
layer. SDK availability still matters. Verified means a named check is recorded
for that device; Supported means an implemented driver path. Neither certifies
every control or an unattended night.

| Vendor | Device | How | Status |
|---|---|---|---|
| ZWO | AM5N mount | LX200 ASCII over USB serial | Recorded movement/tracking checks |
| ZWO | Other AM5-family mounts | Family serial driver | Supported |
| ZWO | ASI220MM camera | ASICamera2 SDK | Verified |
| ZWO | Other SDK-listed uncooled ASI cameras | ASICamera2 SDK, whichever model it lists | Supported |
| ZWO | Cooled ASI cameras | Exposes and reads temperature; no cooler control yet | Use Alpaca for now |
| ZWO | EAF focuser | ZWO USB SDK | Supported; recorded inventory only |
| ZWO | CAA rotator | ZWO USB SDK | Supported |
| ZWO | ASIAIR (as a backend) | Its own network protocol, via libasi | Untested on hardware |
| Player One | Poseidon-M PRO camera | Player One SDK, gain modes included | Verified |
| Player One | Other SDK-listed Player One cameras | Player One SDK; cooling, dew heater and read modes all wired | Supported |
| Wanderer Astro | Snowflake filter wheel | Native serial | Verified |

The ZWO EFW has no native driver. Use a compatible filter-wheel driver through
Alpaca or the optional Windows COM host, and check the controls it exposes.

**Other drivers.** **ASCOM Alpaca** connects supported roles through an Alpaca
server, including ASCOM Remote. On Windows, the optional COM host exposes
supported ASCOM driver roles when the ASCOM Platform and device driver are
installed. Check the capabilities offered by your backend and device; a protocol
connection does not establish that every control works.

| Function | Supported |
|---|---|
| Plate solver | ASTAP (auto-detected) |
| Guider | Built-in engine, PHD2, NINA |
| Weather | Open-Meteo, Astrospheric |
| Cloud model | NOAA GOES-18 / GOES-19 |
| Sky survey | Offline schematic sky; optional personally fetched HiPS tiles |
| Alerts | ntfy, Telegram, webhook, dead-man's heartbeat |

**Not on the list?** Ask. A backend implements the device interfaces in `devices/base.py`;
its capabilities determine which operations the app can offer. Third-party backends can also ship as separate packages and register
themselves through an entry point, with no fork required. Open an issue with the
gear you have.

---

## The sky map works with no internet

The Atlas has a schematic sky when offline. Where survey tiles have been
fetched, it can render imagery with framing overlays.

Observing somewhere without signal is the normal case. Fetch the tiles you
need inside Atlas for your own use before leaving, then check the planned area
offline. AstroDeck distributes no survey tiles or DSS2 pack. Do not assume every
zoom level was cached. DSS2 imagery (c) AAO/STScI, served from CDS/ESA HiPS mirrors.

---

## Vendor neutrality lives in the code

Everything above `devices/base.py` is vendor agnostic. The sequencer does not
know what brand your mount is; it knows a mount can slew, report a pier side and
refuse. Swap a camera vendor and the autofocus routine does not change. A backend can add device support through the same interface.

That seam lets standalone drivers and software bridges use the same device
roles while keeping their capability differences visible.

---

## What is not finished

v0.3. It runs on a real rig most clear nights, and it is not finished.

- **Guiding and plate solving work and still have sharp corners.** Used every
  session; both can still fail with a message that could be clearer.
- **Native engine packaging is incomplete (#630).** Published releases omit
  `astrodeck_native`; native autofocus and guiding need a separate build.
- **The ASIAIR backend has never touched real hardware.** Written against the
  protocol, tested against a fake.
- **The cloud model needs a GOES footprint.** Coverage is principally the Americas, within the selected satellite footprint. Europe, Africa, Asia and Oceania are not, and AstroDeck says so
  rather than rendering an empty sky as clear.
- **Binaries are not code-signed**, so Windows and macOS both warn on first run.

---

## Install

| | |
|---|---|
| **One file you run** | [`docs/guide/install-binary.md`](docs/guide/install-binary.md): Windows, Linux or Mac, with no Python or Node to install. |
| **Docker, Raspberry Pi included** | [`docs/guide/install-docker.md`](docs/guide/install-docker.md): build, create an administrator, then start the stack. |
| **From source** | the source recipe above and [getting started](docs/guide/getting-started.md). |

---

## Documentation

The full site lives at
**[epim.github.io/astrodeck](https://epim.github.io/astrodeck/)**: overview,
Astro Flows, weather awareness, hardware evidence, and getting-started links.

In this repo:

- [`docs/guide/`](docs/guide/README.md): task-focused how-tos: getting started,
  equipment, capture, focus, Sky Atlas, plans, sessions, Monitor, weather,
  remote access and roles, site and locations, safety, troubleshooting.
- [Windows rig](docs/guide/windows-rig.md) and [Orange Pi appliance](docs/guide/orange-pi-appliance.md): platform setup.
- [`docs/quickstart.md`](docs/quickstart.md): install, first simulator session,
  real gear, connecting NINA.
- [`docs/overview.md`](docs/overview.md): purpose, philosophy, architecture.
- [`docs/development.md`](docs/development.md): dev setup, repo structure,
  testing, adding a device backend.

---

## Development

```
server/  Python · FastAPI · :8800        ui/  React 18 · TypeScript · Tailwind v4
  devices/   Alpaca, NINA, ASIAIR, native and sim backends behind one abstraction
  imaging/   stretch · histogram · star detection / HFR · clip mask · FITS
  focus/     V-curve autofocus              solve/     ASTAP · sim solver
  guide/     native engine · PHD2 · NINA    polar/     TPPA
  sequence/  autonomous engine · scheduling · sessions · instructions
  flows/     the node graph, its compiler and its doctor
  cloudmap/  GOES ABI grid · occlusion geometry · motion · the sky dome
  catalog/   coords · objects · survey · framing · visibility
  hub.py     the device orchestrator       api/  REST + WebSocket, serves the UI
native/      Rust extension (PyO3): star detection, HFR/PSF, autofocus, TPPA
relay/       the forward-only remote-access relay
```

```powershell
cd server
.venv/Scripts/python -m pytest -q
cd ../ui
npm test
npm run dev          # Vite, proxies to :8800
```

Contributions welcome, and so are bug reports from rigs that look nothing like
mine. See [`docs/development.md`](docs/development.md).

---

Apache-2.0 for the top-level project; native components and third-party materials
have their own terms in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Not affiliated with ZWO, Player One, Wanderer Astro, or the NINA or
PHD2 projects.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
