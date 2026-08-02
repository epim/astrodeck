# Resume After Restart — Design

**Status:** approved 2026-08-02
**Goal:** when the observatory PC restarts mid-night — Windows update, crash, or a
power cut — the interrupted run comes back and continues, without shooting a
frame or moving an axis on a belief the rig cannot verify.

## Why this is small

Most of it already exists and was **proven working on the real rig on
2026-08-02** by rebooting the observatory PC mid-sequence:

| Link | Result |
| --- | --- |
| Box boots, auto-logs in, Scheduled Task starts the supervisor | works, unattended |
| Server binds :8800 | ~3 min after boot |
| All six devices reconnect | automatic (`rig connected (player-one) — camera, telescope, focuser, filterwheel, rotator, guide_camera`) |
| Interrupted session rescued, frames preserved | `boot sweep: 1 orphaned session(s) -> dormant` |
| Session armed to resume | **NO — `auto_resume=False`** |
| Focuser / filter / mount state | survived (clean reboot; USB stayed powered) |

The run sat dormant and idle forever. One flag is the blocker. The rest of this
design exists because a *power cut* is not a clean reboot, and because a mount
with no brake can move while nobody is powering it.

## The governing principle

**Never move, and never expose a frame, on a belief the rig cannot verify.**

Two facts force this:

1. **The AM5 has no brake.** It is a harmonic drive; with the motors unpowered a
   heavy OTA can sag under gravity. The mount's own encoders cannot report a
   shift that happened while it was off. So after ANY restart, where the mount
   *says* it points is a claim, not a measurement.
2. **The EAF forgets its position on power loss** (see the focus-system notes).
   A focuser reporting `9935` after a power cut is reporting a default, not a
   measurement.

The recovery therefore *measures* rather than *remembers*. It never restores a
device to a persisted number: a remembered position is a guess about a device
that has just told us it does not know where it is.

## Components

### 1. Arm-while-active

`api/app.py:2831` currently rejects arming on a running session:

```python
if body.auto_resume and s.status != "dormant":
    raise HTTPException(409, "auto-resume arms only dormant sessions")
```

The rule was written for the feature's original purpose — "I have stopped for
tonight, resume at dusk tomorrow" — where dormant is true by definition. But the
session a restart destroys is `active`, so the one that needs arming is exactly
the one that cannot be armed. After the crash it becomes dormant and unarmed,
and nobody is awake to arm it.

**Change:** permit `auto_resume=true` on `active` as well as `dormant`. The
server-enforced singleton (arming one disarms the others) is unchanged.

**Change:** a run started through `POST /api/sequence/start` arms its session by
default. Disarming stays a one-click UI action. Rationale: an opt-in flag that
must be remembered before every night is a flag that is not set on the night it
was needed.

Nothing downstream changes. `boot_sweep` (`sequence/session.py:188`, called at
`api/app.py:287`) and `ResumeArm` (`sequence/resume_arm.py`) already do the rest,
as the reboot test demonstrated.

### 2. Device fingerprint

New module `devices/fingerprint.py`. A single small JSON file recording
last-known device state, written when state changes:

- focuser position
- mount RA/Dec, parked flag, tracking flag
- filter slot

At boot, after devices connect, the fingerprint is compared with what the
devices now report, producing a **trust verdict** consumed by the recovery
ladder:

- **focus_trusted** — false when the focuser's reported position differs from
  the fingerprint by more than `FOCUS_MATCH_STEPS = 0` steps (an exact match is
  required; the EAF is a stepper and reports integers, so any difference means
  it lost count). That mismatch IS the power-loss tell; no OS event log needed.
- **cooling_trusted** — false when the sensor is more than
  `COOLING_TOLERANCE_C = 1.0` from its configured setpoint. Always evaluated
  live from the device, never from the file.

The file is written on every observed change to a tracked field, coalesced to at
most one write per `FINGERPRINT_WRITE_INTERVAL_S = 10`, via the existing atomic
writer (`persist.write_json_atomic`) so a power cut mid-write cannot corrupt it.
A missing or unreadable fingerprint means **nothing is trusted** — the safe
direction, and the correct reading on a first-ever boot.

Note there is deliberately **no `pointing_trusted`**: see the ladder below —
pointing is re-measured unconditionally, so a verdict about it would only be a
reason to skip a check that must never be skipped.

The fingerprint records; it never restores.

### 3. Recovery ladder

Runs inside `ResumeArm` before `engine.start`, in this order. Every step fails
safe: on failure it alerts, leaves the session dormant, and re-arms the existing
`RETRY_INTERVAL_S` (600 s) backoff — the same discipline the weather veto and
unbounded-quota guard already use.

1. **Cool to setpoint.** Wait until the sensor is within `COOLING_TOLERANCE_C`
   (1.0 °C) of its configured setpoint, polling every 10 s, giving up after
   `COOLING_TIMEOUT_S = 900` (15 min — a 30 °C pull-down from ambient takes about
   10 min on this camera, so the timeout catches a failed cooler rather than a
   slow one). After a restart the sensor sits at ambient; 36.8 °C was measured on
   the rig immediately after the test reboot. Resuming without this shoots light
   frames at ambient that no dark in the library matches — a silent
   data-quality loss, which is worse than a loud delay.

   Skipped entirely when cooling is not configured (no setpoint, or a camera
   with `can_cool` false), so an uncooled rig is not blocked for 15 minutes.

2. **Restore focus when untrusted.** If `focus_trusted` is false, run autofocus
   before the first light frame. Do NOT drive the focuser to the remembered
   number.

3. **Re-establish pointing — ALWAYS, unconditionally.** Blind plate-solve where
   the OTA is actually pointing, sync the mount to that solution, then re-center
   on the target.

   This is unconditional *because the mount has no brake*. A restart that
   preserved every byte of software state still cannot rule out that the tube
   drooped while the motors were unpowered. A trust check here would be a check
   on the wrong thing: the fingerprint can only tell us what the mount *believed*,
   and belief is precisely what sagging invalidates.

   If the blind solve fails — too few stars, heavy cloud — **do not move.** Alert
   and stay dormant. A cloudy power cut therefore ends the night, which is the
   correct trade: the alternative is slewing an OTA whose true position is
   unknown, toward a pier.

4. **Resume.** Call the existing `engine.start(plan, session=...)`. Per-target
   `center` handling proceeds as it does today; step 3 has already guaranteed
   the mount's model matches the sky.

Calibration targets (`Target.calibration=True`) skip steps 2 and 3 — they never
slew, center, focus or guide — but still honour step 1, because a dark's whole
purpose is to match a light's temperature.

## Error handling

Every failure path is alert-and-retry, never crash and never proceed:

- `ResumeArm._run` already wraps `tick()` in a broad except so the service cannot
  die; new steps inherit that.
- Cooling timeout, autofocus failure and blind-solve failure each log a distinct
  human-readable reason, arm the 600 s backoff, and leave the session dormant and
  armed so the next tick retries.
- The existing give-up-at-dawn behaviour is unchanged: when the window closes
  mid-backoff the service alerts once and goes quiet until the window reopens.
- The existing `quota_unbounded` refusal and weather veto run before the ladder,
  unchanged — they are cheaper and must not be preceded by a ten-minute cooldown.

## Testing

The end-to-end harness already exists and was used to find the gap:

- **Daylight, zero-risk, full chain:** a plan whose single target is
  `calibration=True` passes `_window_open()` at any hour and never moves the
  mount. Start it, restart the server mid-run, and the whole boot-sweep →
  ResumeArm → resume path is exercised in daylight. This is how the
  `auto_resume=False` gap was demonstrated rather than argued.
- **Power cut:** simulated by injecting a fingerprint mismatch in a fixture, not
  by cutting real power. The code under test consumes the verdict, so the verdict
  is the seam.
- **Ladder steps:** each failure mode (cooling timeout, autofocus failure, solve
  failure) asserts the session stays dormant AND armed, the backoff is set, and
  no device was commanded to move.
- **The no-brake rule:** a test asserts the blind solve runs even when the
  fingerprint matches perfectly. Without it, a later optimisation that skips
  verification "because nothing changed" would silently reintroduce the hazard.

## Out of scope

- Restoring a focuser to a remembered position (measure, do not guess).
- Distinguishing a power cut from a reboot via the Windows event log — the
  fingerprint tests the condition that matters and works for a yanked USB hub too.
- Changing the Scheduled Task's logon trigger. It was verified working: the box
  auto-logs in and starts the server unattended. Converting it to a boot-triggered
  service is a separate hardening question, and would be worth revisiting for the
  appliance platform.
