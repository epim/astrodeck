# Getting started

This walks you from a fresh copy of AstroDeck to your first image against the
built-in simulator — no telescope required. If you already know the app and just
want a task done, jump to the [guide index](README.md).

**Jargon note.** AstroDeck talks to gear over **ASCOM Alpaca**, an open,
network-based device protocol. A **rig** is your whole set of devices (camera,
mount, focuser, filter wheel, etc.) assigned to **roles**. The **simulator rig**
is a complete fake rig that renders a real star field, so everything below works
with no hardware attached.

---

## 1. Install and run the server

You need **Python 3.11 or newer**. From the repository root:

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m astrodeck          # serves on http://localhost:8800
```

The last line starts the server. By default it binds `0.0.0.0:8800` (reachable
from other machines on your LAN). Flags:

- `--host 127.0.0.1` — bind loopback only, for a local-only rig.
- `--port 8800` — change the port.

On start it prints a one-line security posture. If it is bound to a non-loopback
interface with no authentication, it prints a **loud warning** — on your own LAN
that's expected; before any remote exposure, read
[remote-access-and-roles.md](remote-access-and-roles.md).

> **You do not need to build the UI.** A pre-built copy in `ui/dist` is served by
> the server. Only rebuild if you change the UI source:
>
> ```powershell
> cd ui
> npm install
> npm run build
> ```

---

## 2. First launch

Open **http://localhost:8800** in a browser (desktop, tablet, or phone).

- The top of the screen has a **NIGHT** toggle (flips the whole UI to
  dark-adaptation red, and defaults new sessions to full 100% brightness) and
  a brightness slider (floor 50%, so you can never dim the screen to
  unreadable) — handy at the scope, ignore them for now. Below the `lg`
  breakpoint the slider moves into a small overflow popover behind a sun icon
  so it doesn't crowd the header.
- Navigation: on desktop a left rail; on a phone a bottom bar with **Equipment,
  Align, Mount, Focus, Capture** and a **More** button holding **Guide, Atlas,
  Plan, Power, Monitor, Settings**. "Equipment" is the exact label everywhere
  — the internal icon name is "rig", but nothing in the UI ever shows that
  word to you.

If you enabled a sign-in method you'll get a login screen first; a fresh open LAN
install shows the app directly. See
[remote-access-and-roles.md](remote-access-and-roles.md).

---

## 3. Connect the simulator rig

Go to the **Equipment** view (that's the literal label, on the left rail and
the bottom bar alike). In the **Rig Actions** panel, press
**▶ Simulator rig**. This assigns every device role to the simulator *and*
connects it in one step, so the toast (*"Rig connected — N/N roles up"*), the
**Devices** panel, and the **Link Status** panel all agree on the same
connected rig — there's no separate "assign, then connect" dance for the
simulator.

(For real gear instead: declare your backends under **Settings → Connect →
Backend Drivers**, assign each device slot to a driver in the **Devices**
panel on the **Equipment** view, then press **Connect Rig** — see
[equipment-and-profiles.md](equipment-and-profiles.md).)

---

## 4. Set your observing site

Before anything sky-related is correct, set your location. Go to **Settings →
Connect → Observing Site**, enter your latitude/longitude (magnitude + N/S,
E/W) and elevation, and press **Set site**. Until you do, AstroDeck uses a
default (0, 0) site and every altitude, transit, and visibility number is wrong
— and the panel's persistent **"Active site: ..."** line will keep reading
*Not set*. Full details: [site-and-locations.md](site-and-locations.md).

---

## 5. Take your first image

Go to the **Capture** view. In the **Exposure** panel:

- **Exposure (s)** defaults to `2`. Leave it.
- Leave **Gain**, **Offset**, **Binning** at their defaults.
- Press **Single**.

The button reads **Exposing…** then **Reading…**, and the **Live Preview** panel
shows the frame — a simulated star field that responds to where the mount points
and how the focuser is set. Try:

- **Loop** to expose continuously (**Stop** to end it).
- The preview toolbar: **Fit**, **100%**, zoom `−`/`+`, and overlay toggles
  (**Stars**, **Clip**, **Reticle**, **Center**).
- Turn on **save FITS to library** and set a **Target name** to keep frames.

From here the simulator supports the whole workflow — [focus](focus.md),
[framing in the Atlas](sky-atlas.md), [building a plan](plan-and-sequences.md),
and [multi-night sessions](sessions-multi-night.md) — all without hardware.

---

## Where things live

- The server persists site, optics, profiles, plans, auth, and weather config
  under `server/config/` (or the directory named by `ASTRODECK_CONFIG_DIR`), so
  they survive restarts and updates.
- Saved images and session ledgers live under the capture directory.

## Next steps

- [Equipment & profiles](equipment-and-profiles.md) — connect real gear, save a
  profile.
- [Capture](capture.md) · [Focus](focus.md) · [Sky Atlas](sky-atlas.md)
- [Plan & sequences](plan-and-sequences.md) · [Sessions](sessions-multi-night.md)
- [Troubleshooting](troubleshooting.md) if the server won't start.

For a high-level tour of the project, see the root
[`README.md`](../../README.md) and [`docs/quickstart.md`](../quickstart.md).
