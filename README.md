# AstroDeck

**One browser tab runs the whole observatory. Yours to own, yours to change.**

Point a phone at your rig and AstroDeck slews, centres, focuses, guides, shoots,
dithers, flips the meridian, watches the weather, parks at dawn and writes you a
report. It runs on a mini-PC or a Pi at the scope. You open a page.

[**Overview and documentation →**](https://epim.github.io/astrodeck/)

![version](https://img.shields.io/badge/version-0.3.22-4DD9E8)
![python](https://img.shields.io/badge/python-3.11%2B-4DD9E8)
![platforms](https://img.shields.io/badge/runs%20on-Windows%20%C2%B7%20Linux%20%C2%B7%20macOS%20%C2%B7%20Pi-4DD9E8)
![licence](https://img.shields.io/badge/licence-Apache--2.0-8A97AE)

> **Under real stars.** Whole nights, unattended, on a Player One Poseidon-M PRO,
> a ZWO AM5N harmonic mount, a ZWO EAF, a Wanderer filter wheel and a ZWO guide
> camera. Meridian flips at the limit, dawn cutoff, park and warm, and a session
> that picks itself back up the following night.

---

## Try it in two minutes, with no hardware

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck            # http://localhost:8800
```

Open **Equipment**, press **Simulator rig**. Eleven devices connect in three
seconds.

The simulator renders a real star field that answers to where the mount points,
where the focuser sits and which filter is in the light path. Autofocus walks a
real V curve, the solver solves and the guider guides, so a two-target night
runs to completion while it rains outside. Put the blackout filter in and the
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

**Guiding.** Built-in guide engine, or PHD2, or NINA's. RA/Dec error graph,
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
that shades your whole hemisphere with real GOES satellite cloud. You can see at
a glance which half of your sky is clear, whether the bank moving in will reach
your target before you finish, and which of tonight's candidates will stay in
the open. Picking a target stops being a guess.

**Multi-night sessions.** Set a target once and keep adding to it across as
many nights and reboots as it takes. AstroDeck scores every frame for HFR, star
count and guide RMS, you can overrule any call it makes, and it re-arms itself
at dusk when the target rises again. You never re-count what you already shot.

**Data out.** Full FITS headers, per-frame WCS written back after a solve, an
end-of-night report, and a stacking bundle: one zip pre-sorted into PixInsight,
Siril or APP layouts with matching calibration masters and a quality score per
frame.

**Watching from bed.** Monitor dashboard with ETA, progress, cooler, guide RMS,
meridian countdown, HFR trend and a live thumbnail. One tap turns the entire
interface dark-adaptation red, with day and night brightness remembered
separately.

**Sharing the rig.** Viewer, operator and admin roles gate every capability.
Google sign-in or local accounts, read-only share links, and precise site
coordinates that stay admin-only. Remote access dials one outbound connection to
a forward-only relay, so there is nothing to port-forward, and every tunnelled
request is re-authenticated at home.

**Staying current.** Releases are Ed25519 signed and verify fail-closed. A
supervisor applies them, rolls back on its own if one is bad, and will not
interrupt a running sequence or a moving mount.

---

## Standalone, or alongside what you already run

AstroDeck is a complete observatory controller. It brings its own star
detection, autofocus, plate solving, polar alignment, guiding and sequencing,
and it can run a rig with no other astronomy software installed.

It also plays well with others. If you already have a setup you like, point
AstroDeck at it and keep everything where it is.

| Route | What drives the hardware | What AstroDeck adds |
|---|---|---|
| **Standalone** | AstroDeck, via native drivers or ASCOM Alpaca | Everything below |
| **Alongside NINA** | NINA, via its Advanced API (port `1888`) | Touch UI, Flows, session ledger, Sky Atlas, weather model, remote access, phone dashboard |
| **Alongside ASIAIR** | The ASIAIR box, over its own network protocol | Same, with guiding left to the box |

All three are supported permanently. Use AstroDeck for the parts where it helps
and keep whatever already works.

### Feature matrix

Where a feature depends on the route you chose, this is what each one does.

| | Standalone | With NINA | With ASIAIR |
|---|---|---|---|
| Camera: expose, download linear FITS | Yes | Yes | Yes |
| Camera: cooler and temperature | Yes | Yes | Yes |
| Camera: dew heater | Yes | No | Yes |
| Mount: slew, sync, track, park, jog | Yes | Yes | Yes |
| Mount: drive rate | Yes | Yes | Yes |
| Focuser: absolute move, temperature | Yes | Yes | Yes |
| Filter wheel | Yes | Yes | Not yet |
| Rotator | Yes | Yes | Not yet |
| Power ports | Yes | No | Yes (4 DC) |
| Guiding | Built-in or PHD2 | NINA's guider | Left to the box |
| Plate solving | ASTAP | ASTAP or NINA's | ASTAP |
| Autofocus (V-curve, HFR) | Yes | Yes | Yes |
| Polar alignment (TPPA) | Yes | Yes | Yes |
| Live stacking | Yes | Yes | Yes |
| Astro Flows | Yes | Yes | Yes |
| Multi-night session ledger | Yes | Yes | Yes |
| Sky Atlas and Tonight | Yes | Yes | Yes |
| Weather model and cloud dome | Yes | Yes | Yes |
| Monitor dashboard and alerts | Yes | Yes | Yes |
| Remote access relay | Yes | Yes | Yes |
| Roles and share links | Yes | Yes | Yes |

The ASIAIR filter wheel and rotator are the two commands that could not be
mapped to certainty without a box on the bench. A guess there rotates to the
wrong angle or images through the wrong filter in silence, so AstroDeck declines
to guess. That backend needs one extra install step,
`pip install -e .[asiair]`, which pulls in the MIT-licensed
[libasi](https://github.com/epim/libasi). Skip it and the backend is absent.

AstroDeck sees that the ASIAIR is guiding and does not fight it.

---

## Hardware

**Native drivers.** No vendor software or ASCOM layer to install. Verified
means whole unattended nights on a real rig. Supported means the driver
reaches it through the same SDK or protocol, but nobody has run it through a
night yet.

| Vendor | Device | How | Status |
|---|---|---|---|
| ZWO | AM5 / AM5N mounts | LX200 ASCII over USB serial | Verified |
| ZWO | ASI220MM camera | ASICamera2 SDK | Verified |
| ZWO | Any other uncooled mono ASI camera | ASICamera2 SDK, whichever model it lists | Supported |
| ZWO | Cooled or colour ASI cameras | Exposes and reads temperature; no cooler control and no Bayer tag yet | Use Alpaca for now |
| ZWO | EAF focuser | ZWO USB SDK | Verified |
| ZWO | CAA rotator | ZWO USB SDK | Supported |
| ZWO | ASIAIR (as a backend) | Its own network protocol, via libasi | Untested on hardware |
| Player One | Poseidon-M PRO camera | Player One SDK, gain modes included | Verified |
| Player One | Any other Player One camera | Player One SDK; cooling, dew heater, read modes and Bayer tag all wired | Supported |
| Wanderer Astro | Snowflake filter wheel | Native serial | Verified |

The ZWO EFW has no native driver; like any ASCOM device it connects through
the bundled COM host or Alpaca.

**Everything else.** Anything with an **ASCOM Alpaca** endpoint connects
directly: ZWO, Pegasus Astro, QHY, PrimaLuceLab, Optec, Lakeside, Moonlite and
the rest of the ASCOM world through ASCOM Remote. Anything with a Windows-only
ASCOM driver works through the bundled COM host, which AstroDeck starts and
manages for you.

| Function | Supported |
|---|---|
| Plate solver | ASTAP (auto-detected) |
| Guider | Built-in engine, PHD2, NINA |
| Weather | Open-Meteo, Astrospheric |
| Cloud model | NOAA GOES-18 / GOES-19 |
| Sky survey | DSS2 via HiPS, with a ~250 MB offline pack |
| Alerts | ntfy, Telegram, webhook, dead-man's heartbeat |

**Not on the list?** Ask. Adding a backend means implementing a handful of small
async methods against `devices/base.py`, and nothing else in the codebase
changes. Third-party backends can also ship as separate packages and register
themselves through an entry point, with no fork required. Open an issue with the
gear you have.

---

## The sky map works with no internet

The Atlas renders survey imagery as a smoothly zoomable WebGL tile map, warped
through the exact TAN projection and upsampled from parent tiles so the view
never blanks while you drag.

Observing somewhere without signal is the normal case, so take the sky with you:

```
cd server && python -m astrodeck.catalog.survey_pack fetch
```

About 250 MB, and also the automatic fallback whenever the online service is
unreachable. Online deep-zooms grow it on disk as a side effect. DSS2 imagery
© AAO/STScI, from public CDS/ESA HiPS mirrors.

---

## Vendor neutrality lives in the code

Everything above `devices/base.py` is vendor agnostic. The sequencer does not
know what brand your mount is; it knows a mount can slew, report a pier side and
refuse. Swap a camera vendor and the autofocus routine does not change. Add a
backend and no existing code moves.

That is also why the three routes above are permanent options: they are all
backends behind the same seam.

---

## What is not finished

v0.3. It runs on a real rig most clear nights, and it is not finished.

- **Guiding and plate solving work and still have sharp corners.** Used every
  session; both can still fail with a message that could be clearer.
- **No flats wizard.** You can shoot flats, with auto-exposure and a
  calibration library behind them, but nobody has built the guided walkthrough.
- **The ASIAIR backend has never touched real hardware.** Written against the
  protocol, tested against a fake.
- **The cloud model needs a GOES footprint.** North and South America are
  covered. Europe, Africa, Asia and Oceania are not, and AstroDeck says so
  rather than rendering an empty sky as clear.
- **Binaries are not code-signed**, so Windows and macOS both warn on first run.

---

## Install

| | |
|---|---|
| **One file you run** | [`docs/guide/install-binary.md`](docs/guide/install-binary.md): Windows, Linux or Mac, with no Python or Node to install. |
| **Docker, Raspberry Pi included** | [`docs/guide/install-docker.md`](docs/guide/install-docker.md): `docker compose up -d`, multi-arch. |
| **From source** | the two-minute recipe above. |

---

## Documentation

The full site lives at
**[epim.github.io/astrodeck](https://epim.github.io/astrodeck/)**: overview,
Astro Flows, weather awareness, the hardware matrix and the user guide.

In this repo:

- [`docs/guide/`](docs/guide/README.md): task-focused how-tos: getting started,
  equipment, capture, focus, Sky Atlas, plans, sessions, Monitor, weather,
  remote access and roles, site and locations, safety, troubleshooting.
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
cd server && .venv\Scripts\python -m pytest -q
cd ui      && npm test
cd ui      && npm run dev          # Vite, proxies to :8800
```

Contributions welcome, and so are bug reports from rigs that look nothing like
mine. See [`docs/development.md`](docs/development.md).

---

Apache-2.0. Not affiliated with ZWO, Player One, Wanderer Astro, or the NINA or
PHD2 projects.
