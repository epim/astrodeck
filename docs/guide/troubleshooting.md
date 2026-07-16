# Troubleshooting

Quick fixes for the things that go wrong, and where to look when they do.

---

## The server won't start

**"Address already in use" / port 8800 is taken.** Another process (often a
previous AstroDeck) is already on the port. Either stop the other process, or
start on a different port:

```powershell
.venv\Scripts\python -m astrodeck --port 8801
```

**Nothing loads in the browser.** Confirm the console shows the server came up on
`http://localhost:8800` (or your `--port`). If you bound loopback with
`--host 127.0.0.1`, only the same machine can reach it — other devices need the
default `0.0.0.0` bind and the machine's LAN address.

**A big "SECURITY WARNING … NO auth" banner on startup.** That's expected when
bound to a non-loopback interface with no sign-in enabled — it's a reminder, not
an error. On a trusted LAN it's fine; before remote exposure see
[remote-access-and-roles.md](remote-access-and-roles.md).

**Import/build errors.** Reinstall the server package into the venv
(`.venv\Scripts\pip install -e .`). You don't need to build the UI — a built copy
ships in `ui/dist`; only rebuild (`npm install && npm run build`) if you edited
the UI.

---

## The simulator or rig won't connect

- **Simulator:** Equipment view → **Rig Actions** → **▶ Simulator rig**. If it
  doesn't respond, check the browser console/network tab and the in-app Event Log
  (below).
- **Real gear:** a device only appears in a role's dropdown when its driver is
  **enabled, reachable, and offers that device**. Use **Probe** on the driver row
  (Settings → Backend Drivers) to force a fresh check, confirm the host/port, and
  make sure the vendor's Alpaca server / ASCOM Remote / NINA Advanced API / PHD2
  is actually running. Slot states like `DRIVER UNREACHABLE` or `DEVICE MISSING`
  tell you which part is wrong. See
  [equipment-and-profiles.md](equipment-and-profiles.md).
- The **Safety monitor** role only accepts an actual `safetymonitor` device — a
  wrong device there is rejected on purpose (it would poll as permanently
  unsafe).

---

## Night mode and screen brightness

- The **NIGHT** toggle in the header flips the whole UI to dark-adaptation red,
  including a red filter over previews and survey/radar imagery.
- The brightness steppers (`−` / `+`) and slider dim the screen; the dimmer floor
  is about 8%. Brightness is remembered separately for day and night, so flipping
  NIGHT restores your last night brightness.
- If the screen is *too* dark to find the controls, the dimmer never goes fully
  black — brighten with the `+` stepper, or toggle NIGHT off.
- On phones, extra display controls (**Lock Screen**, **Keep Awake**, reverse-axis
  toggles) live in the **More** sheet.

---

## Remote access (relay) issues

- Remote access only works when the **relay** is set up and the scope is
  configured to dial it (admin only). When remote is disabled — the default — the
  scope never dials out and there is nothing to reach from outside.
- Over the relay the open "no sign-in" posture is **denied**: remote users must
  authenticate. If a remote user only sees status/previews, check their **role**
  — they may be a viewer.
- Some admin-level writes (auth config, remote config) are intentionally
  **blocked over the tunnel** and must be done on the LAN.
- Full setup and the security model: [remote-access-and-roles.md](remote-access-and-roles.md)
  and [`relay/README.md`](../../relay/README.md).

---

## Where the logs are

**In-app Event Log (primary).** Every AstroDeck component logs to a live event
stream. Open it with the **LOG** button in the header (or **Event Log** in the
mobile **More** sheet); a badge counts unseen errors. It shows each event's
source and message, colour-coded by level. This is backed by:

- the WebSocket `log` event stream (live), and
- **`GET /api/logs`** — the recent log buffer (any viewer, `view.status`).

Weather, safety, sequence, and config subsystems all report here, so it's the
first place to look for *why* something refused or failed (e.g. *"auto-resume
vetoed: …"*, *"config reset to defaults: …"*).

**Server console.** The server also writes startup and runtime lines to its own
standard output (the terminal you launched it from) — the security posture
banner, the bind address, and uvicorn request/error logs. If you run AstroDeck
under a service/supervisor rather than a terminal, that console output is
captured wherever your service configuration directs it; check your launcher for
the exact location.

> The application itself does not write a dedicated log file on disk — the live
> Event Log and `/api/logs` buffer are the in-app record, and the console stream
> is the rest. If your deployment redirects the server console to a file, that
> file is defined by your launcher, not by AstroDeck.

---

## Config got into a bad state

AstroDeck's config, saved locations, profiles, and plans are atomic JSON files
under `server/config/` (or `ASTRODECK_CONFIG_DIR`). If the main config file is
corrupted, the server recovers from a `.bak` copy or resets to defaults and logs
a warning — you won't be left unable to start. If you're locked out of an
authenticated install, reset a local admin from the CLI:

```powershell
.venv\Scripts\python -m astrodeck create-admin <username>
```

---

## Related

- [Getting started](getting-started.md) · [Equipment & profiles](equipment-and-profiles.md)
- [Remote access & roles](remote-access-and-roles.md) · [Safety & automation](safety-and-automation.md)
