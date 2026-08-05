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

`preset` is a **derived label**, not a separate switch: the server checks the
underlying numeric fields (`on_unsafe`, `unsafe_consecutive`, `resume_when_safe`,
`resume_safe_consecutive`, `max_pause_min`, and — for **remote** —
`close_dome_on_unsafe`) on every read against the table above, and reports
whichever preset's values they match exactly, or **custom** if they match none.
Hand-editing any of those fields (the Advanced fields on the same panel) always
leaves an honest label behind — there is no way to save "backyard" numerics
under a "remote" label, or vice versa.

Other safety knobs (defaults):

- **enabled** — safety monitoring on/off (default on).
- **poll_each_frame** — re-check safety every frame (default on).
- **twilight_deg** — the twilight angle used for dusk/dawn windows, default
  **-12°** (nautical).

### Warming the camera

"Park and warm" does not switch the cooler off. It walks the **set-point** back
up toward ambient and only then cuts the TEC, because a cooled sensor released
straight to room temperature is a thermal-shock and in-chamber condensation risk
— measured on an AM5N rig at about **5 °C/min** of uncontrolled equalisation.

The ramp runs on a **background task**, so an unsafe trip parks the mount and
closes the roof immediately and warms afterwards; nothing waits ten minutes for
a cooler. Pressing **Cool** during a ramp cancels it and the new set-point wins;
the Capture screen shows progress and offers **Stop ramp** (which switches the
cooler off at once, the old behaviour, deliberately).

Config lives in `cooling` (`config.safety` to change, next to the preset it
belongs to on *Settings → Safety → Safety limits*):

- **warm_rate_c_per_min** — default **2**. About 12 minutes from −10 °C to a
  +15 °C ambient. Chosen to sit under both the ~5 °C/min free-running rate and
  the ~2.5 °C/min implied by NINA's 10-minute warm default; it is a policy
  default, not a measured per-sensor limit.
- **warm_ambient_c** — `null` (default) means work it out: a backend-reported
  ambient if there is one, otherwise assume a warm room and stop early when the
  sensor stops following the set-point (which is the real ambient).
- **warm_ramp** — set `false` to restore the old cut-it-dead behaviour. Every
  warm then logs a warning, because the sentence above would otherwise be
  false.

The safety monitor is **fail-closed**: a wrong device in the `safety` role, or a
stale/unreadable reading, is treated as *unsafe*. That's why AstroDeck refuses to
put a non-`safetymonitor` device into the `safety` role.

- **`GET /api/safety/state`** reports the current state (any viewer).
- **`POST /api/safety/simulate`** forces a safe/unsafe state for testing
  (`config.safety`, admin).

Everything below is on **Settings → Safety**, in three panels: *Sun avoidance*,
*Safety limits* (the presets, floors, twilight and unsafe response) and *When
something fails* (the escalation policy). The *plan-level* toggles that
reference them are on the Plan view — see below.

Changing safety limits needs **admin** (`config.safety`). An operator sees the
current values, read-only, with the reason stated.

---

## Altitude floors, horizon, and pier limits

Two different altitude concepts exist — don't confuse them:

- **Per-target start gate** (`min alt` on the Plan schedule) — a target only
  *begins* once **it** climbs above this altitude. Set per target. See
  [plan-and-sequences.md](plan-and-sequences.md).
- **Global pier-collision floor** (`min_alt_deg`) — watches the **mount's**
  altitude and stops motion below it. Off by default. Settings → Safety →
  *Safety limits* → **Altitude floor**; turning it on starts at 10°, which suits
  most piers. This is a "will the OTA hit something" rule, not a "is this target
  worth shooting" one.

The floor that actually applies at any moment is the **highest** of three
things: that global floor, the site horizon, and any obstruction wedge.

- **Obstructions** — azimuth wedges with hard edges, for a pier, a wall, a
  chimney or the neighbour's tree. Settings → Safety → *Safety limits* →
  **Obstructions**: azimuth from, azimuth to, minimum altitude. A wedge may wrap
  through north (350 → 10 is the 20° span across due north). Edges are
  deliberately hard — one degree outside the wedge the floor is gone — which is
  what distinguishes this from a horizon profile, whose control points
  interpolate and would slope a floor across the whole sky from a single pier.
- **Site horizon** — a **saved location** can carry a stored `horizon_min_deg`,
  which the Site panel re-applies on save if you hold `config.safety` (see
  [site-and-locations.md](site-and-locations.md)). There is still no graphical
  horizon-profile editor: a full az/alt profile is set through the config API.
- **Pier limits** — `enforce_pier_limits` under *Advanced*. Only has any effect
  when the mount actually reports pier side; on a mount that doesn't, it is
  inert rather than wrong.

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

Beyond the hard safety monitor, a softer **escalation** policy decides what a run
does about failures that are not weather. Settings → Safety → **When something
fails** (needs `config.alerts`).

Every default is the gentle **warn** — AstroDeck never silently downgrades or
aborts on its own. That is the right answer while you are sitting next to the rig
and the wrong one for an unattended night, which is the whole reason to visit
this panel before leaving one running.

Before the run starts:

- **require the camera at temperature** — darks only match lights taken at the
  same sensor temperature, so an unsettled cooler means the calibration library
  will not match. Warn, skip the target, or end the run.
- **require guiding** — refuse long exposures unguided. Leave off for short subs
  on a well-aligned mount, or a rig with no guide camera.

During it:

- **autofocus failed** — usually thin cloud or a starless narrowband field.
  Carrying on keeps the previous focus position.
- **a frame failed the quality gate** — keep it, discard it, or shoot a
  replacement, with a per-target retake cap (default 4) so a windy night cannot
  burn the whole session on replacements that also fail.
- **no-progress watchdog** — end the run if nothing has been saved for N
  minutes. Off by default. This is the one that catches silent hangs a
  per-failure rule cannot see: a wedged filter wheel, a mount that never
  finishes slewing.
- **reconnect and resume after a dropout** — network (Alpaca) devices only. A
  USB device that vanishes is not recoverable this way.

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
