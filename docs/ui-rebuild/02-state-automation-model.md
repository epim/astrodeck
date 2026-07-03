# AstroDeck UI Rebuild — 02 · State & Automation Model

> **Purpose.** Every persona reviewer called the state model "the single most important thing to get right" for a product that runs unattended for hours. This enumerates the runtime entities, their states and transitions, what's automated vs manual, and the automation knobs — all from the backend (`hub.py`, `sequence/engine.py`, `polar/session.py`, `devices/`, `guide/phd2.py`, `config.py`). **Visual design is states.** Build the status system around this, not around the happy path.
>
> Companion: [`01-screen-ia-map.md`](01-screen-ia-map.md) (where each state renders), [`04-failure-2am-ux-spec.md`](04-failure-2am-ux-spec.md) (failure transitions in depth).

---

## 1. The status snapshot (the UI's source of truth)

The hub assembles `poll_status()` and pushes it on the `status` topic **every ~2 s**. The store does a wholesale replace each tick — so anything live must live here (not in the connect-time `summary()`). Top-level keys:

| Key | Type | Live? | Meaning |
|---|---|---|---|
| `connected` | `{role → device.describe()}` | live | per-role device dicts (`name,kind,connected,host,role,backend`) |
| `looping` | bool | live | free-run capture loop active |
| `mode` | `"none"\|"sim"\|"alpaca"\|"nina"` | semi-static | active backend |
| `busy` | `"slewing"\|"solving"\|"focusing"\|"capturing"\|null` | live | the one global "rig is mid-action" flag |
| `backend_links[]` | list | live | **tri-state per role** (see §2.1): `{role, attempted, ok, error, connected}` |
| `boot_connect_failed` | bool | live | a boot auto-connect role failed |
| `mount` | dict | live | `{ra_hours,dec_deg,ra_str,dec_str,alt,az,tracking,parked,slewing}` (only if telescope connected) |
| `meridian` | dict | live | `{status,hours_to_flip,flip_enabled,pier_side}` |
| `focuser` | dict | live | `{position,max,temperature}` |
| `filterwheel` | dict | live | `{position,names[]}` |
| `camera` | dict | live | `{temperature,can_cool,has_dew_heater,width,height,max_gain, cooler:{on,power,target_c,can_report_power,at_target}}` |
| `guider` | dict | live | `GuideStats`: `{guiding,rms_ra,rms_dec,rms_total,snr,recent[],name}` |
| `guide_camera` | dict\|null | live | `{name,connected}` |
| `safety` | dict\|null | live | `{is_safe,reason,source,detail,stale,ts}` |
| `disk` | dict | live | `{free_gb, low(<10), critical(<1)}` |
| `site` | dict | static | `{name,latitude,longitude,elevation_m,is_default,horizon_min_deg}` |
| `optics` | dict | static | `{focal_length_mm,pixel_size_um,…,image_scale_arcsec_px,fov_w_deg,fov_h_deg}` |
| `nina_link` | dict | live | NINA-mode only: `{active,last_ok_age_s,last_error,healthy,warming_up}` |

**Separate topics** (not in `status`): `sequence` (full engine state), `preview` (per frame), `guide` (per guide step), `polar`, `focus`, `safety` (edge-only on change), `mount` (discrete actions), `log`. Cold-load hydration: `GET /api/monitor/snapshot` → `{sequence,status,preview_id,guide_recent}`.

> **`at_target` is a server-computed flag** (`abs(temp−target) ≤ 1.0 °C`) shared between the Monitor readout and the engine's cooling gate so they never disagree. Render it, don't recompute it.

---

## 2. Entity state machines

Most "states" are composed booleans, not enums. The UI must derive a single legible status from them per entity — that derivation is a core design task. Below: the real states and their triggers.

### 2.1 Device connection (per role) — **tri-state, not boolean**
`backend_links[]` gives each role: `attempted` (was requested in the rig spec), `ok` (connected successfully), live `connected`.

```
not-attempted ──request──▶ attempted ──fail──▶ attempted+failed (ok=false)
                                       └─ok──▶ connected (ok=true, connected=true)
                                                     └─drop──▶ dropped (ok=true, connected=false)
```
Roles: camera, telescope, focuser, filterwheel, switch, safety, guider (guider resolved via session, not the device loop). The UI's `BackendLinkGrid` already renders this as **CONNECTED / DEGRADED / FAILED / NOT REQUESTED** — keep that vocabulary.

### 2.2 Mount — composed booleans + a motion fence
Surfaced in `mount`: `slewing`, `tracking`, `parked`, `pier_side` (`east|west|unknown`).
- `park()` → slews to RA 0 / Dec +89.5° then `parked=true, tracking=false`. `unpark()` clears it.
- **Flipping is not a mount state** — it's an engine *action* (`meridian_flip`): stop guiding → re-center on the far side → flip guide calibration → restart guiding.
- **Motion epoch fence** (invariant, not a state): every abort/STOP/park/deadman bumps `_motion_epoch` first; an in-flight slew re-checks just before dispatch and abandons if the epoch moved. *Design implication: STOP is always honored even mid-slew — show it as instant.*

**Suggested UI mount status:** `PARKED · SLEWING · TRACKING · IDLE` (already in MountView), plus a flip sub-state on Monitor.

### 2.3 Cooler — three implicit states
From the nested `cooler` dict + `at_target`:
```
OFF (on=false) ──Cool──▶ COOLING (|temp−target|>1°C) ──stabilize──▶ AT-TARGET / "LOCKED" (at_target=true)
```
Driven by `set_cooler(on,target_c)`.

### 2.4 Autofocus — a per-run coroutine (no persisted machine)
Surfaces only via the `focus` topic: `state ∈ running | done | failed`, plus `points` (V-curve) and `best`. Failure reasons are explicit: *"not enough measurable points"*, *"no V-curve minimum found"*, *"minimum not bracketed — widen the sweep"*. In a sequence, an AF failure follows `af_failure_action` (warn/abort/skip).

### 2.5 Polar-align session — a real enum
`polar.state ∈`:
```
idle ──start──▶ running ──pause──▶ paused ──resume──▶ running
                  │                                      │
                  ├──(sim) converge──▶ done              │
                  └──error (NINA/ws fail)──▶ error ◀──────┘
```
Payload: `{state, az_error, alt_error, total_error, progress, message, source:"nina"|"sim"|null}`. `total_error = hypot(az,alt)`.

### 2.6 Guiding — PHD2's `AppState`, mirrored
`guider.guiding` is true only when PHD2's `_app_state == "Guiding"`. PHD2's machine, passed through verbatim:
```
Stopped ──▶ Calibrating ──▶ Looping ──▶ Settling ──▶ Guiding ──▶ (StarLost / LostLock) ──▶ recover…
```
- **Settling** is tracked separately (`SettleDone`, pixels 1.5 / time 8 s / timeout 60 s); start/dither await it.
- **`StarLost`** logs a warning and republishes stats but does **not** change `_app_state` by itself.
- **`CalibrationFailed`** unblocks the settle waiter so start raises a clear error instead of hanging.
- Lost-star recovery is the *engine's* job (`recover_guiding`), not the guider's.

### 2.7 Sequence/run engine — the central machine
`sequence.state ∈`:
```
idle ──start──▶ running ──pause()/safety-pause──▶ paused ──resume──▶ running
                  │
                  ├─ all targets / dawn-cutoff / cooling-skip ──▶ complete
                  ├─ abort() / SafetyAbort / unsafe ──────────────▶ aborted
                  └─ unhandled exception ─────────────────────────▶ error
```
- **Scheduler sub-state** in `state.schedule.state ∈ ready | waiting | window_closed | never_rises`.
- **Terminal reason** `end_reason ∈ complete | dawn_cutoff | cooling_skip | unsafe | aborted | error`.
- `StopTarget` skips the current target **without** aborting the night.
- Full payload also carries `progress{frames_done,frames_total,percent,elapsed_s,rejected, …ETA}`, `plan_name`, `target`, `target_index`, `detail`, `live`.
- **Crash resume:** progress persisted per frame to `.sequence_resume.json`; `GET /api/sequence/recoverable` + `POST /api/sequence/recover` re-attach the same report. *Requires a user action to resume* — surface it (the UI already has "Resume Interrupted Run").

---

## 3. Automation options (the plan's "Automation" panel)

`SequencePlan` fields. Note the **backend default ≠ UI default** in several places — the UI's `defaultPlan()` is more conservative.

| Field | Backend default | UI default | Runtime behavior |
|---|---|---|---|
| `guide` | true | true | start guiding per target; gates dither/recover |
| `dither_every` (frames) | 3 | 0 | dither when `frames_since_dither ≥ N` |
| `dither_pixels` | 3.0 | 3 | dither amplitude |
| `autofocus_every` (frames) | 0 | 0 | refocus when `frames_since_focus ≥ N` (0 = at target start only) |
| `apply_filter_offsets` | true | false | on filter change, shift focuser by offset delta |
| `refocus_on_temp_delta_c` | 0 | 0 | refocus when focuser temp drifts ≥ N |
| `recover_guiding` | true | false | each frame, if not guiding, restart it |
| `meridian_flip` | true | true | auto-flip at `time_to_flip ≤ 0`; gates pier-collision abort |
| `hfr_reject_factor` | 0 | 0 | reject frame if `hfr > median × factor` (needs ≥4 samples) |
| `cool_to` (°C) | null | -10 | cool + wait before lights |
| `cool_timeout_s` | 600 | 600 | cooling wait deadline |
| `park_when_done` | false | false | park mount at wind-down |
| `warm_cooler_when_done` | false | false | warm cooler at wind-down |
| `safety_check` | true | — | master safety-gate toggle |

**Escalation & safety are server config, not the plan** (`EscalationConfig`, `SafetyConfig`): `require_cooling/guiding` + `*_action` (warn/abort/skip), `af_failure_action`, `hfr_reject_action` (warn/discard/retake, retake limit 4), `no_progress_watchdog_s`; safety `min_alt_deg`, `enforce_pier_limits` (default off), `twilight_deg=-12`, `solar_avoidance=true`, `solar_exclusion_deg=30`, `on_unsafe` (default `pause`), `unsafe_consecutive=3`, `resume_safe_consecutive=3`, `max_pause_min=120`. Presets: `backyard` (pause/120 min) vs `remote` (abort_park_warm/0).

---

## 4. Manual vs automated, per nightly step

| Step | Classification | Notes |
|---|---|---|
| Setup / power | **Manual-only** | connect is user/profile/boot-driven; boot auto-connect never initiates motion |
| Cool | **Automated w/ override** | engine cools only if `cool_to` set; also manual via cooler panel |
| Slew | **Automated w/ override** | per-target goto+center; manual GoTo / slew pad; horizon+solar+floor guarded |
| Focus | **Automated w/ override** | AF at target start + `autofocus_every` / temp-delta / post-flip; manual focuser too |
| Polar | **Manual (assisted)** | TPPA measures error; the *user turns the knobs* — software never moves bolts; not part of the run |
| Guide | **Automated w/ override** | auto start + dither + auto-recover; PHD2 also drivable by hand |
| Filters | **Automated** | per-step filter + focus-offset shift, plan-driven |
| Plan-run | **Automated w/ override** | engine runs autonomously; pause/resume/abort at frame boundaries |

**Cross-cutting safety is always automatic and mostly not overridable mid-run:** fail-closed safety gate, mount-altitude floor (even with *no* safety device), sun-exclusion cone, meridian-flip pier guard, motion-epoch fence, move-axis deadman, no-progress watchdog, per-call bounded timeouts. (Details in doc 04.)

---

## 5. What this means for the UI

1. **Derive one status per entity from composed booleans.** The mount isn't "slewing OR tracking OR parked" as three checkboxes — it's one legible state. Do that derivation centrally (a selector), once, and render the result.
2. **Three liveness tiers, three visual treatments:** *live* (updates on `status`/topics), *set* (config/targets the user chose), *stale* (`telemetryStale`, `safety.stale`, `nina_link.healthy=false`). The current UI already distinguishes these — keep it.
3. **`at_target`, `meridian.status`, `schedule.state`, `end_reason`, `boot_connect_failed` are pre-chewed states** — render them directly; don't reimplement the logic client-side.
4. **The engine state + scheduler sub-state + end_reason together tell the unattended-run story.** Monitor should narrate it: *"running · target 2/4 · waiting for M31 to clear the trees · flip in 38 min."*
5. **Automation defaults diverge (backend vs UI).** A rebuild should pick one source of truth for defaults and document it, or beginners and the engine will disagree about what "default" means.
