# AstroDeck User Guide

Task-focused how-to guides for running AstroDeck — the open, vendor-neutral
astrophotography rig controller. For a high-level tour of the project, see the
root [`README.md`](../../README.md); for install/architecture background, see
[`docs/overview.md`](../overview.md) and [`docs/quickstart.md`](../quickstart.md).

---

## Start here

New to AstroDeck? Follow this path:

1. **[Getting started](getting-started.md)** — install, run the server, open the
   UI, connect the simulator rig, and take a first image (no hardware needed).
2. **[Site & locations](site-and-locations.md)** — set your observing site (do
   this early — everything sky-related depends on it).
3. **[Equipment & profiles](equipment-and-profiles.md)** — connect real gear and
   save a profile.
4. **[Capture](capture.md)**, **[Focus](focus.md)**, and **[Guiding](guiding.md)**
   — take exposures, get the stars sharp, and keep the mount tracking true.
5. **[Sky Atlas](sky-atlas.md)** → **[Plan & sequences](plan-and-sequences.md)**
   → **[Sessions](sessions-multi-night.md)** — frame a target, build an
   autonomous run, and accumulate it across nights.
6. **[Monitor](monitor.md)** — the live dashboard to leave up once a
   sequence is running.

---

## All guides

| Guide | What it covers |
|-------|----------------|
| [Getting started](getting-started.md) | Install, first launch, simulator rig, first image |
| [Equipment & profiles](equipment-and-profiles.md) | Drivers, device assignment, task providers, rotator, profiles |
| [Capture](capture.md) | Exposure, live preview, histogram/stretch, cooler, filter wheel |
| [Focus](focus.md) | Manual focus, autofocus, reading the V-curve, HFR |
| [Guiding](guiding.md) | Native autoguider, calibration, algorithm selection, PHD2 fallback, same-night RMS compare |
| [Sky Atlas](sky-atlas.md) | Search, framing, mosaics, visibility, offline survey pack |
| [Plan & sequences](plan-and-sequences.md) | Targets, steps, automation, scheduling, plan library |
| [Sessions & multi-night](sessions-multi-night.md) | Sessions, accepted-frame quotas, review/regrade, resume |
| [Monitor](monitor.md) | Live dashboard, stall detection, pause/recovery, weather panels (operator + admin) |
| [Weather](weather.md) | Cloud forecast, night warning, auto-resume veto, radar map, Astrospheric |
| [Remote access & roles](remote-access-and-roles.md) | Relay, sign-in, viewer/operator/admin, site privacy |
| [Site & locations](site-and-locations.md) | Observing site, hemispheres, GPS, saved locations |
| [Safety & automation](safety-and-automation.md) | Sun avoidance, safety monitor, floors, meridian, alerts |
| [Troubleshooting](troubleshooting.md) | Server/port, sim connect, night mode, relay, logs |

---

## Find it by task

- **"How do I make it resume at dusk?"** →
  [Sessions](sessions-multi-night.md#resuming--manual-and-auto-at-dusk)
- **"Why can't I see the coordinates / weather?"** →
  [Remote access & roles](remote-access-and-roles.md#site-privacy-for-remote-and-low-role-users)
- **"How do I stop it imaging in clouds?"** → [Weather](weather.md)
- **"What can a viewer / operator do?"** →
  [Remote access & roles](remote-access-and-roles.md#the-three-roles-and-what-they-actually-gate)
- **"How do I connect my camera / mount?"** →
  [Equipment & profiles](equipment-and-profiles.md)
- **"Server won't start."** → [Troubleshooting](troubleshooting.md)
- **"My capture looks stalled — what do I check?"** →
  [Monitor](monitor.md#stall-detection--what-you-actually-see) and
  [Troubleshooting](troubleshooting.md#the-run-looks-stuck-stall-diagnosis)
- **"What do RMS / HFR actually mean?"** → **HFR** (half-flux radius, lower
  is sharper) is explained on [Capture](capture.md#reading-the-live-preview)
  and [Focus](focus.md#the-verdict); guide **RMS** shows up live on
  [Monitor](monitor.md#last-frame-guiding-thermal).
- **"The radar map is blank — is that clear sky?"** → No — see
  [Weather](weather.md#the-radar-map); a blank/failed tile layer always shows
  a **"loading…"** or **"tiles unavailable"** badge, never silence.
- **"A run got interrupted — how do I get it back?"** →
  [Sessions](sessions-multi-night.md#what-survives-a-reboot) and
  [Monitor](monitor.md#recovering-an-interrupted-run)
- **"Can I search for a planet in the Atlas?"** → Not yet — see
  [Sky Atlas](sky-atlas.md#searching-for-a-target)
- **"How do I switch guiding back to PHD2 / NINA?"** →
  [Guiding](guiding.md#provider--phd2-fallback)
- **"Is native guiding actually better than PHD2 on my rig?"** →
  [Guiding](guiding.md#same-night-rms-native-vs-phd2) — a same-night
  head-to-head RMS comparison once you've guided under both.

---

## Who this is written for

- **First light?** Start at [Getting started](getting-started.md) — every step is
  followable cold.
- **Mid-session and tired?** The task index above and each page's headings get you
  straight to the answer.
- **Remote, maybe limited role?** [Remote access & roles](remote-access-and-roles.md).
- **Back after a while?** [Sessions](sessions-multi-night.md) and
  [Site & locations](site-and-locations.md) explain naming, finding, and what
  survives a reboot.

Every UI label, route, and default in these guides is drawn from the AstroDeck
source. When the app and a guide disagree, the app is right — please open an
issue.
