# AstroDeck UI Rebuild — 04 · Failure & "2am" UX Spec

> **Purpose.** Maya called the failure screen *"arguably the single most important screen in this product,"* and the onboarding docs only describe what breaks *physically*, never what the app does about it. This turns every real failure path into a **"what the app does / what the user sees / what the user can do"** spec, grounded in the backend (`hub.py`, `sequence/engine.py`, `devices/`, `guide/phd2.py`, `polar/session.py`, `config.py`). It is the design brief for the alerting, banner, and recovery layer.
>
> Companion: [`02-state-automation-model.md`](02-state-automation-model.md) (the states), [`03-personas-roles-attention.md`](03-personas-roles-attention.md) §5 (the attention tiers this maps onto).

---

## 1. The error model (three surfaces, by design)

The same failure can surface in up to three places. The rebuild must route each consistently:

| Surface | What it is | Use for |
|---|---|---|
| **HTTP 409 + `code`** | synchronous guard refusal on a command (`{detail, code}`) | the user tried something blocked: `below_horizon`, `sun_exclusion`, `running`, `name_collision`, version conflict |
| **`log` event** (`{level, message, source}`) | a started background op failed; `level ∈ info\|warning\|error`, 200-entry replay buffer, per-source tag | in-flight failures (slew/AF/solve/capture/sequence). The op already returned `{"started": …}`, so the *only* signal is this event |
| **`status` / topic field** | an ongoing condition that's true until it isn't | `safety.is_safe`, `safety.stale`, `disk.critical`, `nina_link.healthy`, `meridian.status`, `cooler.at_target`, `boot_connect_failed` |

**Design rule (the reviewers' #1 ask):**
- Guard refusals → inline, at the control, explaining the consequence + the next action.
- In-flight `log` errors → transient toast + the log feed.
- **Ongoing conditions → persistent banners/chips, NOT transient toasts.** A stale safety reading is a *state*, not an event; it must stay loud until cleared.

Backend exception vocabulary: `DeviceError` (user-presentable message → HTTP 409 via `_err`), `SafetyAbort` (tears down the run with shielded park/warm), `StopTarget` (skips one target, night continues). Background tasks never 500 — they swallow into a `log` error.

---

## 2. Failure catalog — what the app does / what the user sees / what they can do

Grouped by domain. "Auto" = backend handles it; "User" = needs a person.

### Connection
| Failure | App does (auto/user) | Surface | UI should show | User action |
|---|---|---|---|---|
| Command needs a role that's absent | 409 `"no {role} connected"` (user) | 409 | inline error on the control | connect the device |
| NINA unreachable on connect | no session; `DeviceError` (user) | 409/502 | Rig view error on the NINA row | check NINA/host |
| Boot auto-connect role unreachable | degrades; flags it (auto) | `status`: `boot_connect_failed`, `backend_links` | **persistent banner** "Rig partially connected: focuser failed" | retry that role |
| Live Alpaca link drops | best-effort `reconnect_role` replay (auto) | `backend_links` flips to DEGRADED | tri-state chip DEGRADED | watch; reconnect if persists |
| NINA bridge heartbeat fails | `nina_link.healthy=false` after ~45 s (auto, non-fatal) | `status.nina_link` | amber "NINA link unhealthy" chip | check NINA app |

### Mount / pointing
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| Slew while parked | raises "mount is parked" (user) | 409 | inline; offer Unpark | unpark |
| Target below horizon (configured site) | 409 `code:"below_horizon"`; `force` bypasses | 409 | confirm dialog: "M31 is at 8° — below your horizon. Slew anyway?" | confirm or pick another |
| Target inside sun cone | 409 `code:"sun_exclusion"`; **`force` does NOT bypass** | 409 | hard block: "within 30° of the Sun — refused" | needs `config.solar_override` to disarm |
| Plate-solve fails during GoTo+center | degrades to raw GoTo, continues (auto) | `mount` action + `log` warn | Notice chip "centering: solve failed, using raw GoTo" | re-solve manually if needed |
| Centering never converges (3 tries) | returns not-centered; engine continues (auto) | `log` warn | Notice chip | check focus/exposure |
| Manual jog un-refreshed >1.2 s | 250 ms deadman auto-halts both axes (auto) | (motion stops) | nothing alarming; jog just stops | re-press to continue |
| STOP races an in-flight slew | motion-epoch fence abandons stale slew (auto) | (slew stops) | STOP feels instant | — |

### Cooling
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| Camera has no cooler | "{name} has no cooler" (user) | 409 | hide cooler panel when `!can_cool` | — |
| Cooler won't stabilize within timeout | `cooling_action`: **default `warn` shoots warm**; `abort`/`skip` protect | `log` + `sequence` | Notice chip "cooler didn't reach −10 °C; shooting at −6 °C" | tighten set-point or set `require_cooling` |
| **No frost/dew guard exists** | nothing automatic | — | (gap) consider a dew-point hint from ambient | pick a safe set-point |

### Focus
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| Too few stars / no V-minimum / not bracketed | AF fails loudly, returns focuser to start (user) | `focus` `state:"failed"` + message | Focus view: "✗ autofocus failed — widen the sweep or recentre" | adjust step/exposure, re-run |
| AF fails inside a run | `af_failure_action`: warn(default)/abort/skip | `log` + `sequence` | Notice (warn) or Act (abort) chip | depends on policy |
| Focuser move past limits | silently clamped (auto) | — | clamp the input visibly | — |

### Polar
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| `websockets` lib missing | `polar` `state:"error"` | `polar` | Align view error banner | install dep / use sim |
| NINA TPPA connect/stream fails | `polar` `state:"error"` + msg | `polar` | Align view error | check NINA TPPA |
| Start while already running | "already running" (user) | 409 | disable Start while running | — |

### Guiding
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| PHD2 loses guide star | warns; `is_active` reflects it (auto-surfaced) | `log` warn + `guide` | Notice chip "guide star lost" | watch for recovery |
| PHD2 socket drop mid-settle | unblocks waiter; start raises clear error; reconnects w/ backoff (auto) | `log` + 409 on start | "PHD2 settle failed" not a 90 s hang | re-start guiding |
| Calibration failed | settle fails fast (user) | `log`/409 | clear error | recalibrate |
| Guiding fails to start in run | `guiding_action`: warn(default = unguided)/abort/skip | `log` + `sequence` | Notice or Act per policy | decide tolerance |
| Lost mid-run + `recover_guiding` | auto-restart; failure = warn only (auto) | `log` | Notice "guiding lost — recovering" | — |

### Sequence
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| Plan has no frames | 422 | 422 | inline on Run button | add steps |
| Any device op exceeds its bounded timeout | `SafetyAbort` → shielded park/warm wind-down (auto) | `sequence` `aborted`, `end_reason` | **Act:** sticky "Run aborted: GoTo timed out — mount parked" | inspect, restart |
| Frame HFR ≫ median | `hfr_reject_action`: warn/discard/retake (auto) | `log` + `progress.rejected` | Notice chip + rejected count | check clouds/focus |
| No frame within watchdog | warns; **never aborts** (auto) | `safety` action:"warn" + `log` | Notice "capture stalled?" | investigate |
| Unhandled exception | `state:"error"`, safe-stop (auto) | `sequence` `error` | **Act:** sticky error | inspect log, restart |
| User abort | safe-stop, report "aborted" | `sequence` `aborted` | confirm via hold-to-abort | — |

### Environment / safety / disk
| Failure | App does | Surface | UI should show | User action |
|---|---|---|---|---|
| Safety monitor unsafe (≥3 reads) | `on_unsafe` policy: pause(default)/park/abort_park_warm/warn | `safety` + `sequence` | **Act:** sticky "Unsafe (clouds) — paused, tracking stopped" | wait / abort |
| Safety read times out / sensor gone | **fail-closed = UNSAFE**, `stale=true` (auto) | `safety.stale` | **Act:** "Safety reading stale — treating as unsafe" | check sensor |
| Mount-altitude floor (even w/o sensor) | `SafetyAbort` before any sub-horizon slew (auto) | `sequence` aborted | **Act:** "Aborted: target below safety floor — parked" | re-plan |
| Pier guard (flip disabled, would cross) | `SafetyAbort` (auto, if `enforce_pier_limits`) | `sequence` aborted | **Act:** "Slew would hit the pier; flip disabled — aborted" | enable flip / re-plan |
| Disk < 10 GB / < 1 GB | `disk.low` / `disk.critical` (informational; **no auto-stop**) | `status.disk` | Notice (low) → **Act** (critical) banner | free space |
| Dawn cutoff | scheduler completes the night (auto) | `sequence` `complete`, `end_reason:"dawn_cutoff"` | calm "Night complete — dawn" | — |

---

## 3. Safety system (the always-on layer)

- **SafetyMonitor:** backend `is_safe()` → `SafetyReading{is_safe, reason, source, detail, stale, ts}`. Polled on its own 5 s task (8 s read timeout). **Stale = unsafe, always** (fail-closed). Emits `safety` only on verdict change.
- **Engine safety gate** (when `safety.enabled && plan.safety_check`): disconnected monitor → unsafe; missing/stale → unsafe; `unsafe_consecutive` bad reads → `on_unsafe`; **plus a mount-altitude floor checked on every slew even with no safety device** (projects 180 s ahead so a setting target isn't accepted as it drops).
- **`on_unsafe` actions:** `warn` (continue), `pause` (default — park-hold: stop guiding+tracking, poll until `resume_safe_consecutive` clean reads or `max_pause_min` → then abort+park), `park` / `abort_park_warm` (SafetyAbort → shielded wind-down).
- **Sun avoidance:** on by default, site-independent, enforced at every motion boundary, **not** force-bypassable (needs `config.solar_override`).
- Presets: `backyard` (pause/120 min) vs `remote` (abort_park_warm/0).

---

## 4. Guards & confirmations (what already protects the gear)

| Guard | What it prevents | UI implication |
|---|---|---|
| **Motion-epoch fence** | stale slew completing after STOP/park | STOP/Park are always honored — render as instant, never "pending" |
| **Move-axis deadman (1.2 s)** + 0.6°/s clamp | runaway jog after a network drop | jog needs a held control / repeat; ≤0.72° worst-case travel |
| **Pre-slew pier guard** | mount crashing into the pier on a missed flip | only active if `enforce_pier_limits` on AND mount reports destination side — surface when it's *off* |
| **Sun-exclusion cone** | slewing at/near the Sun | hard block; disarm is a `DESTRUCTIVE_CAP` double-confirm |
| **`force` flags** (goto/sequence) | — | bypass below-horizon only, never the sun cone; treat as an explicit, logged override |
| **STOP / Park / Halt / Abort** | — | every long action has a kill: `/mount/stop`, `/mount/park`, `/focuser/halt`, `/guide/stop`, `/polar/stop`, `/sequence/abort`, `/capture/stop`, `/disconnect` |
| **Restart/update gate** | self-update interrupting a slew/run | "update blocked: rig busy" reason already provided |
| **Shielded wind-down** | a UI abort orphaning a park mid-slew during an unsafe teardown | trust the backend ordering; don't race it from the UI |

**Honest gaps in protection** (call these out in the UI, don't pretend they're covered):
- Default `cooling_action="warn"` **shoots warm** unless `require_cooling` is set.
- **No automatic frost/dew-point guard.**
- Pier guard is **off by default** and needs the mount to report destination side.
- Below-horizon check is **inert on an unconfigured (default) site** — the mount-altitude floor is the backstop, but configuring the site matters.

---

## 5. The 2am scenarios (design these explicitly)

Narrative walkthroughs the rebuild should be able to handle gracefully. Each is a Tier-2 (Act/Wake) event from doc 03.

1. **Clouds roll in.** Safety goes unsafe → `on_unsafe="pause"` → guiding+tracking stop, state `paused`. *Local:* sticky banner "Paused — unsafe (clouds), will resume after 3 clear reads." *Remote (P3):* push. If it stays unsafe past `max_pause_min` → abort+park, escalate. The user should be able to see *why* (the `reason`/`source`) and the resume countdown.
2. **Guide star lost during a sub.** `StarLost` warn → if `recover_guiding`, auto-restart (Notice chip). If it can't recover and `guiding_action="abort"` → SafetyAbort, parked, Act banner. Beginner needs: "guiding stopped, frames may trail — the run paused/recovered."
3. **Missed meridian flip / pier risk.** Flip disabled + slew would cross → SafetyAbort, parked: "Aborted before a pier collision — enable meridian flip." This is the crash-the-mount nightmare; make the prevention legible and the fix obvious.
4. **Disk fills mid-run.** `disk.low` Notice → `disk.critical` Act banner. There's **no auto-stop**, so the UI must escalate hard before frames silently fail to save.
5. **Link drops to a remote owner.** WS down → `telemetryStale` / ConnectionBanner; the *run continues at home*. Reassure: "Lost connection to the rig — the sequence is still running locally." On reconnect, hydrate from `/api/monitor/snapshot`.
6. **A device drops mid-run.** `backend_links` DEGRADED + best-effort reconnect; if a required device, the bounded timeout → SafetyAbort. Show which role, and whether it recovered.

---

## 6. Recommendations for the rebuild

1. **Build a single "Health" model** that folds `safety`, `disk`, `backend_links`, `nina_link`, `meridian`, and the engine `end_reason` into one Tier-0/1/2 verdict, rendered as a persistent strip. This is the *"is my night OK?"* answer.
2. **Pair every BREAKS with a GUARD.** For each scary condition, show what the software is doing about it (this is exactly the doc-fix being applied to the onboarding workflow — see `docs/onboarding/`).
3. **Translate numbers to verdicts** for beginners (RMS 0.6″ → "guiding: good") while keeping the raw number for experts. The UI already has `RmsVerdict`/`FocusVerdict` — extend the pattern.
4. **Never hide STOP from someone who can act, never show it to someone who can't.** Viewers: hidden. Operators/admin: always reachable, always instant.
5. **Route by surface, not by convenience:** guard refusals inline, in-flight failures to toast+log, ongoing conditions to banners. Don't let a stale-safety *state* decay into a missed toast.
6. **Make overrides explicit and logged.** `force`, sun-cone disarm, disabling auth — all `DESTRUCTIVE`; double-confirm and leave a trail.
