# AstroDeck — Quickstart

This guide gets you from a clean checkout to a working AstroDeck: install, your
first simulator session, then connecting real gear and the NINA bridge.

- **Server:** Python 3.11+
- **UI build (optional):** Node.js 18+ / npm (a built copy is committed under
  `ui/dist`, so you only need this if you change the UI)
- **OS:** developed and validated on Windows 11; the server is cross-platform
  Python (paths below use PowerShell)

---

## 1. Install and run the server

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck            # serves on http://localhost:8800
```

`-e .` installs AstroDeck and its dependencies (FastAPI, uvicorn, numpy, astropy,
httpx, pillow). The server hosts both the API and the built UI, so once it's up,
open **http://localhost:8800** in any browser on the same network — phone,
tablet, or desktop.

To bind a specific interface or port:

```powershell
.venv\Scripts\python -m astrodeck --host 0.0.0.0 --port 8800
```

### Rebuilding the UI (only if you change it)

```powershell
cd ui
npm install
npm run build            # outputs ui/dist, which the server serves
```

For live UI development, see [`development.md`](development.md) — `npm run dev`
gives you a Vite dev server that proxies to the API.

---

## 2. Your first session — the simulator

You do not need any hardware to learn AstroDeck. The simulator is a complete
virtual rig that renders a real star field.

1. Open **http://localhost:8800**.
2. On the **Rig** page, click **Connect Simulator Rig**. AstroDeck connects a
   simulated camera, mount, focuser, filter wheel, power switch, and guider.
3. Go to **Capture** and take a **Single** exposure. You'll get a stretched
   live preview you can zoom, pan, fit, and view at 100%. Drag the histogram
   handles to adjust the stretch, toggle the star overlay and clip mask, and
   scrub the frame filmstrip.
4. Go to **Mount**, search the catalog (e.g. *M42*), and **GOTO** a target. The
   simulated sky field shifts to match where the mount points. A below-horizon
   target triggers the altitude guard.
5. Go to **Focus** and click **Run** autofocus. The sim's star sharpness depends
   on focuser position, so you'll see a real V-curve form and the focuser move to
   the HFR minimum.
6. Go to **Mount** → **Solve & Sync**. The sim plate solver returns the true
   pointing, demonstrating goto-and-center.
7. Go to **Guide** and **Start**. Watch the live RA/Dec error graph, the scatter
   plot, and the RMS. Try **Dither**.
8. Build a plan in **Plan**, add a target and an exposure step or two, and
   **Run** it. Then open **Monitor** to watch ETA, progress, cooler, and guiding
   on one glanceable dashboard.

Because the simulator is coherent — pointing, focus, and filters all affect the
rendered frame — autofocus, plate solving, centering, guiding, and full
sequencing genuinely work. It's the best way to learn the workflow before a
cold, dark night.

### Set your site (recommended even in sim)

The observing site drives every altitude, transit, meridian, and polar
calculation. AstroDeck ships with a placeholder "default" site; until you set a
real one, the below-horizon goto guard stays inert. The site is configured
through the API (`PUT /api/site` with signed, East-positive longitude) — for
example with a quick HTTP call or the interactive docs at
`http://localhost:8800/docs`. (A dedicated Settings screen is on the roadmap; for
now site/optics/safety/alerts are edited via the API and the surfaces that use
them.)

---

## 3. Connecting real gear (ASCOM Alpaca)

Alpaca is the open, vendor-neutral path and the recommended way to run real
hardware.

1. **Start an Alpaca server** for your devices:
   - Many vendors ship native Alpaca servers (ZWO, Pegasus Astro, QHY, etc.).
   - For ASCOM-only drivers, run **ASCOM Remote** on the Windows machine that has
     the drivers — it exposes them over Alpaca.
2. On the AstroDeck **Rig** page, click **Scan**. AstroDeck sends the standard
   Alpaca UDP discovery broadcast (port 32227) and lists the devices each server
   reports. You can also enter a host/IP and port manually.
3. Click a discovered device to **assign it to a role** (camera, telescope,
   focuser, filter wheel, switch, safety monitor). Roles are how AstroDeck knows
   which device is your main camera vs. guide camera, and so on.
4. Head to **Capture**, **Mount**, **Focus**, etc. — they now drive your real
   gear.

### Guiding with PHD2

1. Launch **PHD2** and enable its event server (the JSON socket on port 4400 —
   on by default in recent PHD2).
2. On the **Rig** page, connect PHD2.
3. **Guide** now shows live RA/Dec error, RMS, and dithering against your real
   guide camera.

### Plate solving with ASTAP

Install **ASTAP** and its star database. AstroDeck auto-detects it on common
install paths; to point at a custom location, set the `ASTAP_PATH` environment
variable before starting the server. Plate solving powers **Solve & Sync**,
goto-and-center, and sequence centering. (When bridged to NINA, solving uses
NINA's own plate solver instead.)

---

## 4. Running alongside NINA

Already imaging with NINA? Connect your existing rig with no drivers re-pointed
and no profiles rebuilt: your equipment setup and plate solver stay exactly as
they are, and AstroDeck adds the touch UI, the multi-night session ledger, the
Atlas planner, remote access and the phone dashboard on top.

This is a supported way to run AstroDeck, not a waiting room. If you would
rather AstroDeck talk to the hardware directly, it can do that too (section 3),
and you can switch either way whenever it suits you.

1. In **NINA**, install the **Advanced API** plugin and enable it (default port
   **1888**).
2. On the AstroDeck **Rig** page, open **NINA Bridge** and click **Scan
   Network**. AstroDeck sweeps your subnet for the Advanced API signature and
   lists each NINA instance with its version and connected equipment. Or enter
   the host/IP manually.
3. Click **Bridge**. AstroDeck reflects whatever equipment NINA already has
   connected and drives it through NINA's API:
   - **Capture** shows NINA's stretched frames with its measured HFR/star counts.
   - **Focus** runs NINA's *native* autofocus and draws its real V-curve.
   - **Mount** centering uses NINA's plate solver; slewing converges on the
     reported position (robust to non-standard `Slewing` reporting).
   - **Polar alignment** runs NINA's **TPPA** over a WebSocket, shown as a
     bullseye reticle with arcminute azimuth/altitude error.

Disconnecting AstroDeck never disconnects NINA's own equipment.

### Try the bridge without a NINA install

A bundled mock serves NINA-shaped responses backed by the simulator star field:

```powershell
cd server
.venv\Scripts\python -m tools.mock_nina       # mock NINA on :1888
```

Then bridge AstroDeck to `127.0.0.1:1888`. This is how the NINA path is tested
and demoed without any NINA install.

---

## 5. Where things are saved

- **Captures / FITS:** under the `captures/` directory at the repo root.
- **Config (site, optics, safety, alerts):** `server/config/astrodeck.json`
  (atomic, versioned).
- **Plans and profiles:** `server/config/plans/` and `server/config/profiles/`.
- **Session reports:** generated by the sequence engine and downloadable via the
  API (a Session Report view is on the roadmap).

---

## Troubleshooting

- **UI doesn't load at :8800** — make sure `ui/dist` exists; if you pulled a
  source-only tree, run `npm install && npm run build` in `ui/`.
- **`Scan` finds nothing (Alpaca)** — confirm your Alpaca server / ASCOM Remote
  is running and on the same subnet; some networks block UDP broadcast, in which
  case enter the host/IP manually.
- **NINA bridge can't connect** — confirm the Advanced API plugin is installed
  and enabled in NINA and that port 1888 is reachable; try the mock first.
- **Autofocus/solve behave oddly on real gear** — guiding and plate solving still
  have sharp corners; check the log drawer (the LOG button in the header) for the
  server's account of what happened.

Next: [`overview.md`](overview.md) for the architecture, or
[`development.md`](development.md) to work on the code.
