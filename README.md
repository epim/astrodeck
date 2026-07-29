# AstroDeck

**Run your whole imaging rig from a phone, tablet, or laptop. Open, vendor
neutral, and yours to modify.**

AstroDeck controls the camera, mount, focuser, filter wheel, guider, rotator and
power box from one web UI, and runs the night for you: slew, centre, focus,
guide, shoot, dither, flip the meridian, park at dawn. It runs on a small
computer at the scope. You open a browser.

![status](https://img.shields.io/badge/status-v0.2-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![tests](https://img.shields.io/badge/tests-2000%2B%20passing-brightgreen)

> **Validated on sky.** AstroDeck has been run end to end against a live rig: a
> Player One Poseidon-M PRO mono camera, a ZWO AM-series harmonic mount, a ZWO
> EAF, a Wanderer filter wheel, and PHD2, with capture, site and optics,
> goto/slew, native autofocus and TPPA polar alignment all working under the
> stars.

---

## Try it in two minutes, with no hardware

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck            # http://localhost:8800
```

Open the page, go to **Equipment**, and press **Simulator rig**. Eleven devices
connect in about three seconds.

The simulator is not a stub. It renders a real star field that responds to where
the mount is pointing, where the focuser is, and which filter is in the way, so
autofocus finds a genuine V curve, plate solving solves, guiding guides, and a
multi-target sequence runs to completion. You can learn the whole app indoors on
a cloudy night.

---

## It fits around what you already have

AstroDeck is not trying to win an argument with the software you already use. It
is a control surface for your rig, and there is more than one sensible way to
point it at one.

**Already running NINA?** Keep it. AstroDeck can fly your existing rig through
NINA's Advanced API, with no drivers re-pointed and no profiles rebuilt. Your
equipment setup, your plate solver, your sequences: untouched. What you add is
the touch UI, the multi-night session ledger, the Sky Atlas planner, remote
access, and a phone dashboard for checking a run at 3am. Install the **Advanced
API** plugin in NINA (default port `1888`) and add it under **Settings, Connect,
Backend Drivers**.

**Own an ASIAIR?** Keep using it. AstroDeck can now talk to the ASIAIR unit
itself over the network: add its IP under **Settings, Connect, Backend Drivers**
and the box keeps driving the hardware while AstroDeck adds the touch UI, the
multi-night session ledger, the Sky Atlas planner, remote access and the phone
dashboard. Say precisely which parts work: **camera** (exposures download as
real linear FITS, plus cooler, temperature and the anti-dew heater), **mount**
(position, slew, sync, tracking and drive rate, park/unpark, manual jog),
**focuser** (position, absolute moves, temperature) and the four **DC power
ports**. Guiding stays in the ASIAIR, where it already works — AstroDeck reads
that the box is guiding and refuses to fight it rather than pretending to take
over. The rotator (CAA) and filter wheel are not driven yet; those two commands
could not be mapped to certainty without an ASIAIR on the bench, and a guess
there would silently rotate to the wrong angle or image through the wrong
filter. This backend needs one extra install step (`pip install -e .[asiair]`,
which pulls in the MIT-licensed [libasi](https://github.com/jewzaam/libasi)) and
is simply absent if you skip it.

If you would rather drive the gear directly, you can: native drivers ship for
ZWO AM-series mounts, EAF focusers, EFW filter wheels and ASI cameras, plus
Player One cameras and Wanderer accessories. Nothing to buy, no driver to
replace, and both routes are supported.

**Or run it on its own.** AstroDeck speaks **ASCOM Alpaca** directly, which ZWO,
Pegasus Astro, QHY, PrimaLuceLab and anything with an ASCOM driver support
(through ASCOM Remote), and brings its own autofocus, star detection, plate
solving, polar alignment and guiding. That is enough to run a rig with nothing
else installed.

All three are supported, permanently. None is a stage on the way to another, and
there is no migration anyone is expected to perform. Use AstroDeck for the parts
where it helps and keep using whatever already works for you.

---

## What it does

**Capture.** Live preview with zoom, pan and a 1:1 magnifier, an adjustable
stretch with an interactive histogram, star overlay, clip mask, and a frame
filmstrip. Full exposure control, cooler with an at-target badge, dew heater,
and a live guide-camera view.

**Focus.** Manual jog, absolute goto, and V-curve autofocus with real HFR star
measurement. The native Rust engine fits a hyperbola with an R² goodness of fit.
There is also a Bahtinov mask aid with a live spike overlay.

**Mount.** Touch slew pad with a dead-man's switch, catalog goto with live
altitude and a below-horizon guard, tracking rates, park and unpark, and plate
solve and sync.

**Guiding.** A native guide engine, or PHD2, or NINA's guider, with an RA/Dec
error graph, RA-vs-Dec scatter, RMS stats and dithering. A Guiding Assistant
measures your seeing and Dec backlash and recommends settings you can apply or
ignore.

**Planning a night.** The Sky Atlas is a pan and zoom survey map with a
draggable, rotatable field-of-view overlay, a mosaic planner, and a visibility
planner that draws the altitude curve, transit, twilight and moon separation.
Tonight ranks what is actually up right now, tagged by difficulty, so a beginner
has somewhere to start.

**Running a night unattended.** The sequencer handles slew, centre, autofocus,
guide, per-filter exposure loops, dithering, thermal refocus, meridian flip, and
park and warm at the end. Conditional rules can refocus, skip a target or abort
on a trigger you choose. Safety-monitor gating, dusk-to-dawn autorun scheduling,
weather holds, and ntfy, webhook or Telegram alerts with a dead-man's-switch
heartbeat.

**Coming back the next night.** Targets accrue frames across as many nights and
reboots as it takes. A per-frame accept/reject ledger tracks HFR, star count and
guide RMS, you can override any call by hand, and a dormant session resumes
itself at dusk when the target's window reopens.

**Getting your data out.** Full FITS headers, per-frame WCS written back after a
plate solve, an end-of-night report, and a stacking bundle: one zip pre-sorted
into PixInsight, Siril or APP layouts with matching calibration masters and a
per-frame quality score.

**Watching from bed.** The Monitor dashboard shows ETA, progress, mount state,
cooler, guiding RMS, meridian countdown, an HFR trend and a live thumbnail. One
tap flips the whole interface to dark-adaptation red, with a brightness dimmer
that remembers day and night separately.

**Sharing the rig.** Three roles (viewer, operator, admin) gate every
capability, with Google sign-in or local accounts and read-only share links.
Precise site coordinates are admin-only. For remote access the scope dials one
outbound connection to a forward-only relay, so there is no port forwarding and
no NAT configuration, and every tunnelled request is re-authenticated at home
rather than trusted from the relay.

**Keeping it current.** Releases are Ed25519 signed and verify fail-closed. A
supervisor applies them, rolls back automatically if one is bad, and never
interrupts a running sequence or a moving mount.

---

## The sky map works without internet

The Atlas renders survey imagery as a smoothly pan and zoomable WebGL tile map,
warped through the exact TAN projection and upsampled from parent tiles so the
view never blanks while you drag. Where WebGL is unavailable it falls back to a
plain image cutout pipeline.

Observing somewhere with no signal is the normal case, so download the offline
pack once (about 250 MB) from **Settings, Connect, Sky Atlas** or on the command
line:

```
cd server && python -m astrodeck.catalog.survey_pack fetch
```

The pack is also the automatic fallback whenever the online service is
unreachable. Online deep-zooms grow it on disk as a side effect. DSS2 imagery
© AAO/STScI, from public CDS/ESA HiPS mirrors.

---

## Hardware

Anything with an **ASCOM Alpaca** endpoint works directly. Anything with a
Windows ASCOM driver works through ASCOM Remote or the bundled COM host. Native
drivers ship for ZWO AM-series mounts (LX200 over serial), ZWO EAF focusers, ZWO
filter wheels and rotators, Wanderer accessories, and ZWO ASI and Player One
cameras, so a common rig can run with no vendor software installed at all.

Guiding can use the built-in engine or PHD2. Plate solving uses ASTAP, which is
auto-detected.

---

## Project state

v0.2, used on a real rig, and not finished.

Working and used under the stars: capture and sequencing, native autofocus,
plate solving, TPPA polar alignment, guiding (built-in engine or PHD2), the sky
atlas, multi-night sessions, and live stacking with constellation registration
and satellite rejection.

Rough edges worth knowing about before you rely on them:

- **Guiding and plate solving work and still have sharp corners.** Both are used
  every session; both can still fail in ways whose message could be clearer.
- **A flats wizard is not built.** Flats are shootable — there is flat
  auto-exposure and a calibration library — but there is no guided walkthrough.
- **The ASIAIR integration has never touched real hardware.** It is written
  against the protocol and tested against a fake.
- **The binaries are not code-signed**, so Windows and macOS will both warn on
  first run.

[`docs/overview.md`](docs/overview.md) tracks the full status.

---

## Installing

| | |
|---|---|
| **A file you run** | [`docs/guide/install-binary.md`](docs/guide/install-binary.md) — download one file for Windows, Linux or Mac. No Python, no Node. |
| **Docker, including a Raspberry Pi** | [`docs/guide/install-docker.md`](docs/guide/install-docker.md) — `docker compose up -d`. Multi-arch, so the same setup runs on a mini-PC or a Pi 4/5. |
| **From source** | the two-minute simulator recipe above. |

---

## Documentation

- [**`docs/guide/`**](docs/guide/README.md) is the user guide: task-focused
  how-tos for getting started, equipment, capture, focus, the Sky Atlas, plans,
  multi-night sessions, the Monitor dashboard, weather, remote access and roles,
  site and locations, safety, and troubleshooting.
- [`docs/quickstart.md`](docs/quickstart.md) covers install, a first simulator
  session, real gear, and connecting NINA.
- [`docs/overview.md`](docs/overview.md) covers purpose, philosophy and
  architecture in depth.
- [`docs/development.md`](docs/development.md) covers dev setup, repo structure,
  testing, and adding a device backend.
- [`docs/ux-review-protocol.md`](docs/ux-review-protocol.md) is how UI reviews
  are run here.

---

## Development

```
server/  Python · FastAPI · :8800     ui/  React 18 · TypeScript · Tailwind v4 · Zustand
  devices/   Alpaca, NINA, native and sim backends behind one device abstraction
  imaging/   stretch · histogram · star detection / HFR · clip mask · FITS
  focus/     V-curve autofocus            solve/  ASTAP · sim solver
  guide/     native engine · PHD2 · NINA guider · dithering
  sequence/  autonomous engine · scheduling · session reports · instructions
  catalog/   coords · objects · survey · framing · visibility
  hub.py     the device orchestrator     api/  REST + WebSocket, serves the UI
native/      Rust extension (PyO3): star detection, HFR/PSF, autofocus, TPPA
relay/       the forward-only remote-access relay
```

```powershell
cd server
.venv\Scripts\python -m pytest -q      # 2,000+ tests

cd ui
npm run dev                            # Vite dev server, proxies to :8800
```

Everything above `devices/base.py` is vendor agnostic. Adding a backend means
implementing a handful of small async methods, and nothing else changes. See
[`docs/development.md`](docs/development.md).
