# Safety & automation

AstroDeck is built to run unattended, which means it also has to *stop* on its
own. This page covers the guards: sun avoidance, the safety monitor, altitude
and meridian limits, and the alerting that tells you when something happened.

---

## Sun avoidance (daytime guard)

**Settings → Safety** hosts the **Sun avoidance** panel. This is a server-side
**sun-exclusion cone** that blocks any slew — and stops tracking — toward the
Sun, protecting your camera and optics from solar damage. It is **on by default**
and works **even before you set a location** (it is computed from the Sun's
RA/Dec, not your site).

- **Sun avoidance** toggle — shows **Armed** / **Disarmed**.
- **Exclusion angle (deg)** — the half-angle of the no-go cone, default **30**
  (range 0–90; 0 disables the cone). A slew is blocked when the target is within
  this many degrees of the Sun. Press **Save angle** to apply.

Turning avoidance **off** is a solar-astronomy setting — only for a solar scope
with proper filtration that *wants* to point at the Sun. It is a destructive
action: disabling it requires a deliberate **hold-to-confirm** that names the
consequence (the mount may slew at and track the Sun; unfiltered optics can be
destroyed and anyone looking through the gear blinded). A loud persistent banner
stays up while disarmed. Re-arming is always immediate and needs no confirm.

Changing sun avoidance needs **admin** access (`config.solar_override`). For
other roles the toggle is disabled and the current setting is shown for
reference.

---

## The safety monitor

A **safety monitor** is a device (an Alpaca `SafetyMonitor` — a cloud sensor,
rain detector, etc.) assigned to the `safety` role. When present, AstroDeck
polls it and can pause, park, or abort a run when conditions go unsafe.

Safety behaviour is configured through named **presets** (server config;
`SafetyConfig`):

| Preset | On unsafe | Trips after | Auto-resume when safe | Max pause |
|--------|-----------|-------------|-----------------------|-----------|
| **backyard** (default) | pause | 3 readings | yes (after 3 safe) | 120 min |
| **remote** | abort, park, warm | 2 readings | no | none |
| **custom** | your own numeric values | — | — | — |

Other safety knobs (defaults):

- **enabled** — safety monitoring on/off (default on).
- **poll_each_frame** — re-check safety every frame (default on).
- **twilight_deg** — the twilight angle used for dusk/dawn windows, default
  **-12°** (nautical).

The safety monitor is **fail-closed**: a wrong device in the `safety` role, or a
stale/unreadable reading, is treated as *unsafe*. That's why AstroDeck refuses to
put a non-`safetymonitor` device into the `safety` role.

- **`GET /api/safety/state`** reports the current state (any viewer).
- **`POST /api/safety/simulate`** forces a safe/unsafe state for testing
  (`config.safety`, admin).

> **Note.** Today the **Settings → Safety** UI panel exposes only sun avoidance.
> The safety monitor presets, altitude floors, horizon, and escalation knobs
> below live in the server config (`server/config/astrodeck.json`) and are set
> through the config API. The *plan-level* toggles that reference them are on
> the Plan view (see below).

---

## Altitude floors, horizon, and pier limits

Two different altitude concepts exist — don't confuse them:

- **Per-target start gate** (`min alt` on the Plan schedule) — a target only
  *begins* once **it** climbs above this altitude. Set per target. See
  [plan-and-sequences.md](plan-and-sequences.md).
- **Global pier-collision floor** (`SafetyConfig.min_alt_deg`) — watches the
  **mount's** altitude and stops motion below it. Off by default (0); the UI sets
  10° when you enable the floor. A full **horizon profile** (az/alt control
  points for trees and ridgelines) and an optional **no-go box** pier guard can
  raise the effective floor per azimuth. Pier limits can only be enforced when
  the mount actually reports pier side (`enforce_pier_limits`).

A saved location can carry its own horizon profile — see
[site-and-locations.md](site-and-locations.md).

---

## Meridian flip and in-sequence guards

A plan carries its own safety-related toggles (Plan view → automation), all on
by default where it matters:

- **meridian flip (German mount)** — flip the mount past the meridian, with a
  **meridian warn lead (min)** (default 15) heads-up before it happens.
- **safety monitor gate** — honour the configured safety monitor + floor during
  the run.
- **recover guiding if lost** — restart guiding if the star is lost.

The autonomous engine also enforces the mount-altitude floor and the fail-closed
safety gate on every frame, and grades frame quality before recording. See the
plan guide for the full target lifecycle.

---

## Escalation policy

Beyond the hard safety monitor, a softer **escalation** policy (server config,
`EscalationConfig`) decides what a run does about degraded conditions. Every
default is the gentle **warn** — AstroDeck never silently downgrades or aborts by
default. Configurable actions include:

- require cooling / guiding before lights (warn, abort, or skip),
- autofocus-failure action, and HFR-reject action (warn, discard, or retake —
  with a per-target retake cap, default 4),
- a no-progress watchdog (off by default),
- Alpaca reconnect-and-resume (off by default).

---

## Alerts and the dead-man's-switch

AstroDeck can push notifications to **ntfy**, a **webhook**, or **Telegram**.
Each alert sink has a minimum level (warning/error), a set of events it cares
about (`run_start`, `run_end`, `safety`, `error`), and an optional **heartbeat**
(a periodic progress ping). There is also a **dead-man's-switch** URL
(`deadman_url`) — an external healthcheck you ping so a *silent* rig (one that
crashed hard enough to stop pinging) is noticed.

The [high-cloud weather warning](weather.md) reaches these same sinks.

Alert sinks are managed through the alert API:

- **`GET /api/alerts`**, **`POST /api/alerts`**, **`DELETE /api/alerts/{id}`** —
  list / add / remove sinks (`config.alerts`, admin).
- **`POST /api/alerts/{id}/test`** — send a round-trip test; a sink is only
  marked *verified* after one succeeds.

Secrets in these settings (a Telegram bot token, credentials embedded in a
webhook/ntfy URL, a per-ping token in the deadman URL) are **scrubbed** from
every config payload sent over the wire — the UI only ever sees a "configured"
marker.

---

## Automatic resume

Arming a dormant session for **auto-resume at dusk** is the main unattended
automation. Its arming flow, the **no-safety-monitor warning**, and the
**fail-open weather veto** are documented in
[sessions-multi-night.md](sessions-multi-night.md). The short version: it runs
the same guarded start path as a manual resume, so sun avoidance, the safety
monitor, and the horizon preflight all still apply — and it refuses a plan that
could loop forever.
