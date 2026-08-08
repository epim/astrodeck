# What stands between this rig and a full imaging session

Assessed 2026-08-06 against the LIVE rig running 0.2.47, not against memory.
Every claim below is evidence from the rig's own config, status and logs; the
evidence is quoted so a later session does not have to re-derive it.

The question this answers: *if we pointed at a target tonight and asked for a
full unattended session, what would break?*

---

## STATUS after the 2026-08-06 daylight campaign (0.2.54)

The table below was the 0.2.47 assessment. What changed today, all
hardware-verified unless marked:

| Link | Then | Now |
|---|---|---|
| Sequencer | never run | **ran end to end** — 9-frame dark set, 0 rejects, wound down clean |
| Cooling | never engaged | **cool + hold + warm ramp all measured** (27→0 °C in 161 s; held −0.2 °C across 9 frames; ramp 2.00 °C/min) |
| Filter wheel in a sequence | — | **was broken**: calibration targets never changed filter at all. Fixed, proven on the rig (wheel drove L → blackout slot) |
| FITS headers | — | **SET-TEMP was missing from every native-rig frame ever**. Fixed |
| Guiding | never run | **was impossible** — `hub.guider` was None. Two defects fixed; a guider now exists. Calibration still needs stars |
| Meridian flip | believed structurally dead | **`pier_side` works** (ASCOM convention, both sides) and the mount performed a flip. Only the plate-solve half is unproven |
| Safety armed with no monitor | silently permissive | says so once per run + a pre-flight warning; `require_safety_monitor` enforces |
| No-progress watchdog | warn-only, ignored `on_unsafe` | drives `on_unsafe` through the run task |
| Reconnect after a dropout | read by no code | implemented for native drivers; **all five devices survive disconnect→reconnect and still answer** |

Still open and needing sky: guide calibration (#141), polar adjust (#149),
autofocus V-curve (#143), filter offsets (#148), the flip's solve half (#145).

Still open and needing a decision: alerts + deadman have no destination
(#146) — without them no unattended failure is observable at all.

Measured and worth knowing: the guide scope has **no cap**, so its sensor
rails in daylight (uniform 65520). Guide camera is 1920×1080 at 4.0 µm →
**5.50 arcsec/px at the 150 mm guide FL**. The warm ramp assumes 20 °C
ambient when today's was 32 °C (#154); the focuser thermometer was measured
as an alternative and rejected at 43.2 °C.

---

## The short answer

The imaging chain has never been run end to end on this hardware. Individual
links are in wildly different states of proof:

| Link | State | Evidence |
|---|---|---|
| Connect / device discovery | **proven** | all six devices connect, repeatedly |
| Mount goto + plate solve + centre | **proven** | centering attempts in the 2026-08-05 log |
| Polar alignment | **fixed, partly proven** | 0.2.47; two clean measurement points on sky, third clouded out |
| Autofocus | **1 success in 13 attempts** | see §3 |
| Cooling | **never engaged** | camera sat at 31.2 °C; no setpoint in config |
| Filter wheel | connects, 8 slots | **all focus offsets are 0** |
| Guiding | **NEVER RUN** | 0 log lines ever; no calibration; guide FL unset |
| Dithering | **NEVER RUN** | 0 log lines ever |
| Sequencer | **essentially never run** | 24 engine log lines in the rig's whole history |
| Meridian flip | **disabled and never exercised** | `flip_enabled: false`, `pier_side: unknown` |
| Unattended failure handling | **all escalation off** | no watchdog, no alerts |

Only 29 light frames exist on the rig, all named `Light_untargeted_*` — manual
captures, never a sequence with a target.

---

## 1. Guiding has never run. This is the biggest gap.

Evidence, from the rig:

```
"guiding started"  -> 0 log lines across every log file ever written
"guide star"       -> 0
"dither"           -> 0
GET /api/guide/calibration -> {"report": null}
config.optics.guide_focal_length_mm -> null
```

Three separate problems stacked:

1. **`guide_focal_length_mm` is null.** The guider needs it to convert guide
   camera pixels into arcseconds. Without it every guiding number the UI shows
   is in the wrong unit or a made-up scale. **This needs a value from the owner
   — I do not know the guide scope's focal length.**
2. **No calibration exists.** The guider has never measured which way the mount
   moves for a given pulse, at this declination, on this rig.
3. **The native guider has never seen photons.** It is extensively sim-tested
   (the whole W4 programme) and was never run on sky. Related: the pulse-guide
   path on the AM5 is INERT over serial — the driver falls back to timed R1
   moves, which is a fallback that has never been validated under a closed loop.

Also: `guide.ra_algorithm` is `hysteresis`, but the P4 CI gate measured GP-PPEC
at 0.31 px against hysteresis at 0.68 px. PPEC is the better algorithm and is
not the default.

## 2. The sequencer has essentially never run

24 engine/sequence log lines exist in the rig's entire history. No session has
run a target list, honoured a schedule, dithered between frames, refocused on
temperature change, or wound down at dawn.

**This is the single most testable thing in daylight.** The filter wheel has an
opaque `Dark` slot (index 7, `opaque: true`), so a full sequence of dark frames
exercises: the engine, the scheduler, target selection, filter changes, cooling
gates, frame naming and storage, the dither call path, the safety poll, the
no-progress watchdog, pause/resume, and wind-down — with the mount parked and
no sky required.

## 3. Autofocus succeeds about 1 time in 13

Every autofocus outcome ever logged:

```
2026-07-29  warning  native autofocus failed: not_enough_spread
2026-07-29  warning  native autofocus failed: r_squared_below_threshold
2026-07-29  error    autofocus failed: ZWO EAF: EAFMove failed (MOVING, code 5)   x5
2026-07-31  warning  native autofocus failed: r_squared_below_threshold           x2
2026-07-31  warning  native autofocus failed: not_enough_spread
2026-08-01  warning  native autofocus failed: r_squared_below_threshold
2026-08-01  info     native autofocus complete: position 9935, HFR 3.55 (hyperbolic)
```

One success, on 2026-08-01. The focuser is *still* at 9935 today, so nothing has
re-focused since.

Two distinct failure families:

- **`EAFMove failed (MOVING, code 5)` — a driver defect.** The code issues a
  move while the focuser is still moving. The SDK refuses. This is
  deterministic and fixable, and is the same shape as audit finding #15 (the
  ASIAIR focuser returning SUCCESS on absence of motion) and #17.
- **`r_squared_below_threshold` / `not_enough_spread` — the curve fit.** The
  V-curve sweep is not producing a usable curve. Note `"V-curve": 0 log lines`,
  so the sweep's own diagnostics are not reaching the log at all. Needs the
  per-step HFR series recorded before it can be diagnosed — the same
  "make the failure explain itself" gap that cost a night on TPPA.

Focuser travel: `max: 18000` (enforced), `hardware_max_position: 600000`.
Current position 9935, temperature 26.6 °C.

## 4. Cooling has never been engaged

Camera reports 31.2 °C with `can_cool: true`, and there is no cooling setpoint
anywhere in the config. `escalation.require_cooling: false`,
`cooling_action: "warn"`.

A full session needs a setpoint, a cool-down before the first light frame, and
the warm ramp on wind-down. The warm ramp bug was fixed (#134) but the whole
path has never run on this camera. **Fully testable in daylight.**

## 5. Filter focus offsets are all zero

```
names   : L R G B S Ha Oiii Dark
offsets : 0 0 0 0 0 0 0 0
```

Every filter change either needs a full refocus or produces defocused subs.
Measuring real offsets needs stars; deciding the policy (refocus-per-filter vs
measured offsets) does not.

## 6. Meridian flip is disabled and has never happened

```
meridian: {"status":"unknown","hours_to_flip":null,"flip_enabled":false,"pier_side":"unknown"}
safety.enforce_pier_limits: false
```

Zero flip/pier log lines in the rig's history. A target tracked across the
meridian will either stop or drive into the pier. The AM5 *does* flip — that is
what corrupted the 2026-08-06 TPPA run — so the hardware behaviour is real and
unmodelled by our config.

`pier_side: unknown` also means the flip logic has no input to work from.

## 7a. AUDIT VERDICT (2026-08-06): several guards are not merely off, they are inert

`server/tests/test_unattended_night_guards_audit.py` pins what these settings
ACTUALLY do today rather than asserting they are good. Seven of its tests
document a GAP and each names what would have to change for it to be deleted.
The findings, by test name:

**Dead config — read by no production code at all:**
- `test_reconnect_resume_and_retries_are_read_by_no_production_code`
- `test_hub_reconnect_role_has_no_caller`
- `test_nothing_ever_publishes_the_reconnect_alert_event`

So `reconnect_resume` / `reconnect_retries` are the project's dominant defect
class again: stored, echoed by the API, rendered in the UI, consumed by nothing
(same shape as audit findings #14 `on_missed`, #22, #25 `SAFETY_PRESETS`).

**Guards that exist but cannot bite as configured:**
- `test_watchdog_is_not_started_at_the_rigs_zero_setting` — 0 means no watchdog
- `test_watchdog_trip_is_warn_only_and_ignores_on_unsafe` — a trip does not
  honour `on_unsafe`, so even set non-zero it only warns
- `test_watchdog_is_blind_while_frames_are_not_expected` — the blind window
- `test_require_cooling_is_inert_without_a_plan_setpoint`
- `test_require_guiding_is_inert_when_the_plan_does_not_ask_to_guide`
- `test_guiding_action_warn_is_indistinguishable_from_the_guard_being_off`

**Silence:**
- `test_empty_alert_list_swallows_a_dying_run_in_silence` (and its positive
  control, `test_one_configured_sink_does_receive_the_dying_run`)
- `test_empty_deadman_url_pings_nothing_and_says_nothing` (positive control
  `test_configured_deadman_url_is_actually_pinged`)

**Safety armed with no source:**
- `test_a_whole_run_completes_with_safety_armed_and_no_monitor` — a whole run
  completes with `safety.enabled: true`, `on_unsafe: "pause"` and
  `connected: false`. The gate reads as protection and permits everything.

Fix priority for an unattended night: alerts + deadman first (without them no
other failure is even observable), then the watchdog honouring `on_unsafe`,
then the safety-monitor honesty. `reconnect_resume` is dead config — either
implement it or delete it, but it must stop advertising a behaviour.

## 7. Unattended failure handling is entirely off

```
escalation.require_cooling      false      cooling_action    warn
escalation.require_guiding      false      guiding_action    warn
escalation.af_failure_action    warn
escalation.no_progress_watchdog_s   0      <- no watchdog at all
escalation.reconnect_resume     false      reconnect_retries 1
alerts: []                                 <- nothing notifies anyone
```

Nothing stops a session that has silently stopped progressing, and nothing tells
the owner. `deadman_url` is empty.

## 8. The safety monitor is disconnected

```
GET /api/safety/state -> {"connected": false, "reading": null, "streak": 0, "stale": false}
config.safety.enabled: true, on_unsafe: "pause", unsafe_consecutive: 3
```

Safety is enabled with a pause action but has no source. Needs a decision:
either wire a source (the weather service is already there) or make the absence
explicit rather than a silently-inert gate. Note `min_alt_deg: 0.0` while the
site's `horizon_min_deg` is 15.0 — two different floors, and the imaging one is
the horizon itself.

## 9. Polar: the adjust phase has still never run on sky

0.2.47 fixed the measurement. The *adjusting* phase — where the owner turns
bolts and watches the number move — has never completed on this hardware,
because every attempt so far died before or at the fit. It now has tests and a
tracking guard, but no photons.

---

## What can be validated TODAY, in daylight

1. **A full dark-frame sequence, end to end** (§2). The highest-value test
   available without sky. Mount stays parked.
2. **Cooling: cool to setpoint, hold, warm ramp** (§4). Measure the real ramp
   rate and compare against the recorded 5.0 -> 1.72 C/min.
3. **The `EAFMove (MOVING, code 5)` defect** (§3). Reproducible by commanding
   focuser moves back to back; no stars needed.
4. **Focuser travel and mechanics** — full sweep across 0..18000, verify
   position reporting and that a move-while-moving is now serialised.
5. **Filter wheel: every slot, both directions, with position verification.**
6. **Meridian flip logic and the mount's real flip behaviour** — a slew across
   the meridian in daylight, sun-avoidance permitting.
7. **Escalation + alert config** (§7) — pure configuration, then prove the
   watchdog fires by stalling a dark sequence.
8. **Autofocus instrumentation** (§3) — record the per-step HFR series so the
   next on-sky failure is diagnosable instead of a one-word reason.
9. **Guider plumbing up to the point of needing stars** — settings, focal
   length, provider selection, calibration refusal messages.

## What genuinely needs stars

- Guide calibration and a closed loop (§1)
- Autofocus V-curve quality (§3)
- Filter focus offsets (§5)
- Polar adjust phase (§9)
- Plate-solve-dependent centring at the session level

## STATUS AT 2026-08-06 14:20 — what is done and what is next

**Deployed and live: 0.2.48.** Rig parked at Dec +90, tracking off, all
scheduled tasks armed. Nine local commits, NOTHING PUSHED (the owner asked me
to stop pushing: CI runs on every push to main and it burns their runner
minutes). Full backend suite 3705 passed, 19 skipped.

DONE TODAY, hardware-verified:
- Guide focal length set to **150 mm** (ZWO 30 mm f/5 guider), config v143 —
  unblocks every guiding number being in real arcsec (§1)
- Solar avoidance on slews, including the disarm (5a, 5c) — see below
- **Sun-approach watchdog built and it parked the tube on hardware** (#150, 5b)
- **Park-after-stop fixed and re-verified** (#151)
- Autofocus `EAFMove` serialisation + V-curve instrumentation (#143, code side)
- Unattended-guards audit landed — see §7a, the verdict is worse than "off"

STILL TO DO IN DAYLIGHT, in the order I would take them:
1. **#142 dark-frame sequence end to end** — still the highest-value test
   available without sky; nothing else exercises the whole chain
2. **#144 cooling** — set a setpoint, cool, hold, warm ramp; measure the real
   rate against the recorded 5.0 -> 1.72 C/min
3. **#146/#147** — alerts + deadman FIRST (without them no other failure is
   observable), then the watchdog honouring `on_unsafe`
4. **#145 meridian flip** — owner chose "enable and validate in daylight first";
   `pier_side` currently reads `unknown`, which the flip logic needs

TONIGHT (owner decisions): clear window **21:49 to about 23:00**, then the
marine layer returns. Point Dec +20..+68 within ~1.5 h of the meridian —
roughly **RA 18h15m / Dec +40** at the start. Slew off the park position first
(Dec +90 is inside the pole guard). Then polar-align, then **attempt a
first-light guide calibration** (#141, #149).

## Hardware validation results, 2026-08-06 (daylight, cap on, Dark filter closed)

### Solar avoidance on slews: PASSES, including the disarm

Run against the live mount with the opaque Dark filter selected. Sun was at
alt +67.7. Four legs, all as they should be:

| Leg | Result |
|---|---|
| goto AT the sun, cone armed | **409 refused** |
| goto 20 deg from the sun (inside the 30 deg cone), armed | **409 refused** |
| goto 43 deg away (positive control) | **200 accepted** |
| the SAME inside-cone goto with `solar_avoidance: false` | **200 accepted** |
| re-armed | **409 refused** again |

The positive control is what makes this meaningful: without it a refusal proves
only that something was refused, not that the cone is what refused it. The
disarm works end to end, so solar astronomy remains possible.

This covers owner requirement 5(a) and 5(c). **5(b) — the sun arriving at a
stationary tube — is not covered by anything and is being built (task #150).**

### Sun watch: BUILT, and it moved the tube on hardware (0.2.48)

Requirement 5(b). `Hub._check_solar` is a pre-slew gate, so nothing watched the
Sun arrive at a stationary tube. `sun_watch.py` now samples pointing against the
projected solar position every 60 s, 30 minutes ahead, and parks.

FIRST HARDWARE TEST WAS MINE AND IT WAS WRONG, which is worth recording. I
pointed a TRACKING tube 35 deg from the Sun and expected the watchdog to fire,
inferring a "cone + 7.5 deg" threshold from a comment. It did nothing, correctly:
the trigger is "does the Sun get inside the 30 deg cone within the next 30
minutes", and a tracking tube holds its RA/Dec, so its separation stays 35 deg
indefinitely. The 7.5 deg in the docstring is what the 30-minute lead buys for a
tube that is NOT tracking (1800 s x 15 deg/hr), not a second threshold.

The real hazard is a STOPPED, UNPARKED mount, and the second test used it:

```
target: 32.96 deg from the Sun now
  if STOPPED : closest approach 25.79 deg in 1800 s   <- triggers
  if TRACKING: closest approach 32.96 deg             <- correctly ignored
```

Tube parked at 33 deg out, tracking stopped, hands off. RA drifted 6.833 ->
6.836 -> 6.839 (about 15 deg/hr, as predicted) and at the next tick:

```
[error] SUN WATCH: the tube is pointing where the Sun will be in 30 min
        (26 deg at closest, exclusion 30 deg, tracking is OFF so the sky is
        turning the tube toward it at 15 deg/h). Parking now.
[error] SUN WATCH: mount parked, pointing at the celestial pole. Check the
        optics and the dust cap before the next session
```

Parked in about 40 s. Disarm sabotage-checked separately: forcing
`safety.solar_avoidance` past the check turns
`test_a_deliberate_solar_session_disarms_it` red, so 5(c) holds.

### Park fails when issued straight after a stop: REPRODUCED, then FIXED (0.2.48)

Fixed and re-verified on hardware. The identical sequence that failed
(goto -> stop -> park, no pause) now parks in 15 s:

```
STOP        : 200
PARK (immediately, no pause) : 200
  +  5s  parked False tracking False lanes [goto]
  + 15s  RA 3.693 Dec 90.00  parked True  tracking False  lanes []
```

Root cause was a chain, not a single mistake: `:Q#` is fire-and-forget and the
axes have mass, so park's own tracking-off (`:Td#`, ack-class) landed in the
halt window where the mount cannot answer; that timeout was swallowed by a bare
`except Exception: pass`; and the `:hP#` that followed went out with tracking
still ON, which this driver already knew from 2026-07-30 makes park a silent
no-op. Fix: drain the halt window on evidence, verify tracking actually stopped,
and retry the park once, naming tracking in the error when both attempts fail.

#### The original failure, for the record

Falling out of the test above, and more serious than the thing it was testing.

```
[mount] mount unparked
[goto]  goto cancelled            <- stop
[goto]  goto cancelled
[goto]  goto failed: ZWO AM5 (native serial): park did not complete
        within 60s (mount still reports unparked)
```

The mount was left unparked and tracking. The identical park from an IDLE mount
succeeded in about 12 s (Dec 90.000, parked, tracking off), so the defect is
specifically *park issued while the tube is still decelerating* — the window
zwo_am5.py already documents ("the AM5 has NO BRAKE"; a cancelled goto stops
being `slewing` by the driver's bookkeeping several seconds before it stops by
physics).

stop-then-park is the EMERGENCY shape: safety abort, dawn park, the sun watchdog
being built, and any aborted session. Tracked as task #151, along with the
second defect on the same path — `POST /api/mount/park` returns
`200 {"started":"goto"}` and the failure reaches the operator only as a log line.


## NIGHT OF 2026-08-08 — the guider calibrated, and four loops that never ended

**GUIDING WORKS.** First closed loop this rig has ever run.

RA 23h Dec +30 had no guide star the camera could find at ANY setting tried
(2 s/g100 through 6 s/g80 bin 2; 4 s/g400 saturated). The main camera plate-
solved that same field in 0.4', so the sky was fine — the guide scope simply
reaches only bright stars. Moved to **Deneb**, where it calibrated:

```
go_west         19 steps  ->  -25.8 px
go_east         19 steps  ->  back to 0.1 px
clear_backlash   6 steps
go_north        25 steps  ->  +27.5 px
go_south/nudge  31 steps  ->  home
100 steps, orthogonality error 1.81 deg, saved to the profile
guiding: RMS 1.29" RA / 0.67" Dec / 1.45" total at SNR ~340, steady over 90 s
```

Advisory raised: "RA and Dec rates vary by an unexpected amount". EXPECTED on
this mount — pulse-guide is emulated (east = tracking-suspend, west = R2+Mw,
N/S = R1 moves), so the axes genuinely move at different rates.

**Open for the owner:** the guide scope reaches only bright fields. Either it
needs focusing, or guide targets must be chosen near a bright star. Deneb worked;
a mag-5 field did not.

### Filter focus offsets (#148): LRGB measured, narrowband not

`offsets = [L 0, R -59, G -30, B -28, S 0, Ha 0, Oiii 0]` at 10 s / gain 300 /
bin 2 on a Cygnus field. The three narrowband slots could not be focused and
KEPT their prior offsets, which the run said out loud ("filter offsets learned
against L; kept prior for 3 slot(s)"). Oiii's V-curve was actually a clean V
(HFR 1.67 at 11173 with 321 stars, rising to 44.70 at both ends) and was
rejected on R^2 0.743 against a 0.70 gate — worth a look under #143, because the
curve a human would accept was thrown away.

Narrowband offsets need either much longer sweeps or a brighter field.

### Four unbounded loops, all the same shape

Each could neither finish nor fail, and each reported nothing while it hung:

1. **Guide calibration wall clock** was 180 s, sized against the SIM. A real
   walk is ~94 steps at one exposure-plus-pulse each — ~220 s at a 2 s guide
   exposure. It killed a healthy calibration 18 steps from the end. Now 600 s.
2. **Star-lost during calibration**: the engine returns Idle without advancing
   when the star is not found, and the host kept exposing with no bound —
   measured at 174 s of silence after three good steps. Bounded at 30 s, and
   both exits now carry pulses/leg/walk/starless-frames.
3. **Autofocus at an unmeasurable position**: the sweep engine only advances on
   a measurement, so a point it cannot measure is re-exposed forever. Seen on
   the SII slot at position 9077 — fourteen consecutive drops, focuser
   stationary, and no route that can cancel a `filter_offsets` lane. Bounded at
   3 tries.
4. **rotate_to_pa** ran all five attempts with the error GROWING and reported
   one number at the end (#174). Now abandons on the first non-improving
   attempt and carries the whole per-attempt trail.

### And the dials never worked at all

`PUT /api/guide/camera-settings` answered `422 {"loc":["query","body"]}` to
every request ever made to it: its body model was the only one declared inside
`create_app()`, and under `from __future__ import annotations` FastAPI resolves
route signatures against MODULE globals, so it never saw a BaseModel. The guide
dials shipped 2026-08-07 have never once applied a setting. There is now a
detector that fails if any body model is declared in `create_app` again, and a
second one asserting every mutating route is named somewhere in `ui/src` — it
found ten orphans on its first run, five of them real gaps (`/api/auth/revoke`,
`/api/auth/unrevoke`, and all three `/api/calibrator/*`).

### Config changed on the rig

`escalation.no_progress_watchdog_s` 0 -> **1800**. The watchdog honours
`on_unsafe` since 0.2.48 but was never armed. Alerts and the dead-man's-switch
still have NO destination — that needs a channel and a URL from the owner, and
until then a run now says so at start instead of failing silently.

## Owner decisions, 2026-08-06

1. **Guide scope focal length: 150 mm** (ZWO 30 mm f/5 guider). Unblocks §1.
2. **Guiding tonight: attempt first-light calibration.** After polar alignment,
   run a real calibration and a short closed loop.
3. **Meridian flip: enable, but validate in daylight first** — confirm
   `pier_side` reporting works and the flip executes before trusting it on sky.
4. **Daylight scope: all four** — dark-frame sequence, autofocus EAFMove fix +
   V-curve instrumentation, cooling cool/hold/warm, and watchdog/alerts/deadman.
5. **NEW REQUIREMENT — validate solar avoidance on the real hardware**, with the
   lens cap on AND the opaque Dark filter selected:
   - a) slew TOWARD the sun and confirm the guard refuses the slew;
   - b) park the tube where the sun will shortly be, and confirm the rig moves
        the scope AWAY rather than waiting to be cooked;
   - c) confirm the whole behaviour can be DISABLED — solar astronomy is a
        legitimate use of this software.
   (b) is the one to check for existence first: refusing a slew into the cone is
   not the same feature as noticing the sun coming to a stationary tube.
