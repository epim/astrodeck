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
  (Settings → Connect → Backend Drivers) to force a fresh check, confirm the host/port, and
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
  including a red filter over previews and survey/radar imagery. Night mode
  **starts at 100% brightness** by default (a deliberate product decision —
  it never opens on a nearly-black screen) and text/labels are tuned to stay
  legible at every brightness level down to the floor.
- Brightness is a single **slider**, not steppers — the `−`/`+` buttons were
  removed. On wide screens it sits inline in the header; on narrower ones it
  collapses behind a small sun-icon button that opens a popover with the same
  slider. The dimmer floor is **50%** — it can never be dragged down to
  unreadable.
- Brightness is remembered separately for day and night, so flipping NIGHT
  restores your last night brightness.
- On phones, extra display controls (**Lock Screen**, **Keep Awake**, reverse-axis
  toggles) live in the **More** sheet.

---

## The run looks stuck (stall diagnosis)

Checklist, roughly in the order to check them:

1. **Look at [Monitor](monitor.md) first**, not Plan — it has the live stall
   read, and it only ever escalates while the sequence state is
   **RUNNING**. The **"last frame N ago"** line turns amber past ~2× the
   current exposure time and red with **"CAPTURE STALLED?"** past ~3×. If
   it's still dim/grey text, the engine likely isn't actually stalled —
   you're probably just between frames (plate-solving, a filter change, a
   meridian flip, a dither settle), **or** the run isn't in the running
   state at all (see the next point).
2. **Check whether it's paused, not stalled.** The header badge and the run
   banner both read **PAUSED** honestly — if you (or someone else) hit
   Pause, that's the whole explanation, and once the badge actually reads
   **PAUSED** the stall warning **cannot fire**: it's gated to the running
   state alone, so a paused run's frame gap is never mistaken for a stall.
   The same is true once a run reaches **COMPLETE**, **ABORTED**, or
   **ERROR** — none of those can show **"CAPTURE STALLED?"** either. (Right
   at the moment you press Pause there's a brief, honest window where the
   last in-flight frame is still genuinely running until its **frame
   boundary** — a real stall can still surface in that narrow gap; see
   [plan-and-sequences.md](plan-and-sequences.md#running-a-sequence).)
3. **Open the Event Log** (below) and look at the last few lines — a stuck
   plate solve, a guiding-recovery loop, or a device timeout usually logs
   something explanatory right before the stall becomes visible.
4. **Check Link Status** (Settings → Connect, or the Equipment view) — a
   device that silently dropped (`DRIVER UNREACHABLE`, `DEVICE MISSING`) can
   leave a step waiting on a response that will never come.
5. **Check Weather**, if enabled — a high-cloud warning doesn't stop a
   running sequence by itself, but it's worth ruling out as a correlated
   cause before you assume it's a device problem.
6. If none of the above explains it and the rig genuinely seems wedged,
   **Abort** (hold-to-confirm) from Monitor or Plan, then use the **recover**
   banner / **Resume from frame N** on [Plan & sequences](plan-and-sequences.md)
   to pick it back up. The underlying session is never lost either way — see
   [sessions-multi-night.md](sessions-multi-night.md#what-survives-a-reboot).

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
- **Getting signed out doesn't stop a run.** If your session expires or you
  get logged out mid-sequence, only your browser is affected — the imaging
  engine runs server-side and keeps going regardless. Sign back in and
  you'll see the same run still in progress; see
  [remote-access-and-roles.md](remote-access-and-roles.md#signing-in).
- Full setup and the security model: [remote-access-and-roles.md](remote-access-and-roles.md)
  and [`relay/README.md`](../../relay/README.md).

---

## Where the logs are

**In-app Event Log (primary).** Every AstroDeck component logs to a live event
stream. Open it with the **LOG** button in the header (or **Event Log** in the
mobile **More** sheet); a badge counts unseen errors. Each line shows a local
**HH:MM:SS** timestamp, a severity word (**Error / Warning / Info / Debug**)
and source in brackets, colour-coded by level, then the message. This is
backed by:

- the WebSocket `log` event stream (live), and
- **`GET /api/logs`** — the recent log buffer (any viewer, `view.status`).

Weather, safety, sequence, and config subsystems all report here, so it's the
first place to look for *why* something refused or failed (e.g. *"auto-resume
vetoed: …"*, *"config reset to defaults: …"*).

**What it still doesn't do.** The log is a flat, reverse-chronological list —
there's no filter by session, target, or severity, and no way to jump to "the
log lines from run X" short of scrolling and reading timestamps against the
session card's own history. For anything beyond "what happened recently and
how bad was it," cross-reference the timestamp against
[Sessions](sessions-multi-night.md) or the Plan's own run detail rather than
expecting the log itself to correlate them for you.

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
- [Monitor](monitor.md) — the live stall/recovery read.
- [Remote access & roles](remote-access-and-roles.md) · [Safety & automation](safety-and-automation.md)
