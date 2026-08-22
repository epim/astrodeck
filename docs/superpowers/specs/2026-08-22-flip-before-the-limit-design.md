# The mount quits before the meridian — design

**Status:** specified to implementation precision.

## 1. The measurement that changes everything

Four nights have ended at the same place. The assumption underneath all four
was that a German equatorial's meridian limit sits *past* the meridian, so a
flip triggered at the crossing arrives in time.

**On this AM5 it does not.** Measured from the logs, with the hour angle
computed independently of the engine:

| night | target | tracking refused at | hour angle |
|---|---|---|---|
| 2026-08-21 | NGC 7129 | 00:44 | **−7.6 min** (east of meridian) |
| 2026-08-22 | NGC 6946 | 23:35 | **−4.7 min** (east of meridian) |

Both times the mount then refused `:Te#` outright, answering `0` — a limit
state, not a transient. Both times a human had to park, unpark, re-assert
tracking and recover the run.

**The mount stops between four and eight minutes BEFORE transit.** The engine
waits for the crossing. The mount always wins.

Why it stops early is not established and does not need to be for this fix.
The most likely explanation is the coordinate drift recorded in
`2026-08-21-tracking-stopped-and-nothing-noticed.md` section "Defect 5": the
mount's reported position walks degrees away from truth during a run, so its
internal hour angle — and therefore its own idea of where the meridian is — is
wrong by an amount that varies. A limit computed on a bad hour angle fires at
the wrong true time. **Fixing the drift is a separate and harder job; flipping
early is correct regardless of the cause**, because the cost of an early flip
is four minutes and the cost of a late one is the rest of the night.

## 2. Why the existing fix never engaged

`schedule.flip_can_be_skipped` was changed on 2026-08-20 so that a GEM always
flips, whatever the tube geometry says. Its docstring ends:

> UNKNOWN PIER SIDE KEEPS THE OLD BEHAVIOUR. It is genuinely ambiguous — a fork
> mount and a GEM whose driver is quiet look the same from here — so this falls
> back to the tube geometry rather than guessing in either direction.

That was the right instinct and the wrong default for this rig, but it is not
what failed here. `tel.pier_side()` **works** on the AM5 — the native guider
reads it successfully twice a night ("mount pier side changed (east->west)").
The engine reads it too.

What failed is timing, not detection. Neither night logged a flip decision at
all, because the engine's decline path only logs `if self._flip_armed`, and by
the time anything looked, the mount had already stopped.

## 3. The four changes

### 3.1 Flip on a lead, not on the crossing

```python
#: Minutes before meridian transit at which a GEM flip is triggered.
#:
#: NOT zero, and this is the whole fix. Measured on the rig's AM5: tracking was
#: refused at -7.6 min (NGC 7129, 2026-08-21) and -4.7 min (NGC 6946,
#: 2026-08-22), both BEFORE transit, both answering :Te# with 0. A flip
#: triggered at the crossing is always too late by several minutes.
#:
#: 10 minutes clears the worse of the two measurements with a 2.4 minute margin
#: and costs, when it was not strictly needed, one re-slew and one re-centre -
#: about four minutes. The asymmetry is the argument: four minutes against the
#: rest of the night.
MERIDIAN_FLIP_LEAD_MIN = 10.0
```

The flip fires when `hours_to_meridian_flip(...) <= MERIDIAN_FLIP_LEAD_MIN / 60`
and the mount is a GEM, rather than when the countdown reaches zero.

`SequencePlan` gains `meridian_flip_lead_min: float = MERIDIAN_FLIP_LEAD_MIN`,
bounded `[0, 60]`, so a mount with a genuinely permissive limit can set it to 0
and get the old behaviour deliberately rather than by accident.

**The arming test must move with it.** `_flip_armed` is currently set at target
setup from `hours_to_meridian_flip(...) > 0`. A target that starts inside the
lead window — 6946 started at 22:05, transit 23:40, so it did not, but a later
start would — must still arm. Arm when the countdown is `> -1.0` hours, so a
target already just past the meridian does not arm a flip it does not need.

### 3.2 Unknown pier side assumes GEM

`flip_can_be_skipped` currently falls back to tube geometry when the pier side
is unknown. Reverse the default: **unknown means flip.**

The docstring's ambiguity argument is real — a fork mount and a quiet GEM do
look alike — but the two errors are not symmetric. Treating a GEM as a fork
costs a night, four times now. Treating a fork as a GEM costs one unnecessary
re-slew per meridian crossing, and a fork user who minds can set
`SequencePlan.meridian_flip = False`, which already exists.

This is "unreadable is not a verdict" (see `astrodeck-dead-link-recovery`): an
absent pier side is a failure to measure, and the code was reading it as a
measurement of "no pier".

### 3.3 A refused flip recovers itself

This is the intervention a human performed twice. When `set_tracking(True)` is
refused and the mount is at or near its limit, the engine currently sets the
target aside and parks, ending the night.

Instead, once, per target:

```
park  ->  unpark  ->  set_tracking(True)  ->  re-slew to the target
      ->  plate solve and re-centre  ->  resume guiding  ->  continue
```

That exact sequence recovered the run by hand on both nights and put it back
0.5 arcmin from target. It must be bounded: **one attempt per target per
crossing**, a hard timeout, and on failure the existing set-aside-and-park path
runs unchanged. A recovery that can loop is worse than no recovery.

It must also refuse to run when the sun is up or the plan's window has closed,
because "recover" at dawn means slewing a parked mount into daylight.

### 3.4 An autofocus sweep needs a tracking mount

`focus/autofocus.py` contains no reference to tracking. A sweep is ten
exposures over five minutes; on 2026-08-21 one ran to completion on a mount
that had stopped, consumed the drift as data, and returned "focus" at
HFR 8.40 px against 3.05-3.71 earlier the same night — then applied it.

The sweep asserts tracking before the first point and again before each point,
through the same seam `_enforce_tracking` uses. A sweep that loses tracking
mid-way aborts and returns the focuser to `start_pos`, which the failure path
already does for every other exception.

## 4. Tests

`server/tests/test_flip_before_the_limit.py`, plus additions to the existing
focus and engine suites.

1. `test_the_flip_fires_before_the_mount_reaches_its_limit` — drive the engine
   with a fake clock from 30 minutes out; assert the flip is requested at or
   before `MERIDIAN_FLIP_LEAD_MIN`, and specifically before −4.7 minutes, the
   worse of the two measured refusals.
2. `test_the_measured_failures_would_now_be_caught` — parametrised over the two
   real events (NGC 7129 at HA −7.6 min, NGC 6946 at HA −4.7 min): assert the
   flip would already have fired by then. **These are the regression, named
   after the nights they cost.**
3. `test_a_zero_lead_restores_the_old_behaviour` — the escape hatch works.
4. `test_the_lead_is_bounded` — outside [0, 60] is a ValueError.
5. `test_a_target_starting_inside_the_lead_window_still_arms`.
6. `test_a_target_already_past_the_meridian_does_not_arm` — the countdown at
   −2 h must not arm a flip.
7. `test_unknown_pier_side_now_flips` — the inverse of the current behaviour,
   asserted directly, with a comment naming the four nights.
8. `test_a_fork_mount_can_still_opt_out` — `meridian_flip=False` wins.
9. `test_a_refused_flip_parks_unparks_and_recovers` — the whole 3.3 sequence,
   with a fake telescope refusing `set_tracking` once and accepting after
   unpark.
10. `test_the_recovery_runs_at_most_once_per_target` — a telescope that always
    refuses gets exactly one attempt, then the set-aside path.
11. `test_the_recovery_is_refused_after_the_window_closes` — no slewing into
    daylight.
12. `test_the_recovery_restores_the_old_path_on_failure` — the existing
    set-aside-and-park behaviour is unchanged when recovery cannot help.
13. `test_a_sweep_refuses_to_start_without_tracking`.
14. `test_a_sweep_that_loses_tracking_aborts_and_restores_the_focuser` — assert
    the focuser is back at `start_pos`, because a sweep abandoned mid-way is
    how a night gets shot at 2450 steps out.
15. `test_a_sweep_does_not_apply_a_result_measured_while_stopped` — the 8.40 px
    case, asserted as a refusal rather than a value.

## 5. What this does not fix

The coordinate drift itself. The mount's reported position still walks degrees
during a run and the detector at `hub.py:2998` still only clears a caption.
This design routes around that by not trusting the mount's timing, which is
worth doing on its own, but the drift will keep costing pointing accuracy and
will still mislead any future code that reads `mount.ra_hours`.

That fix wants a bench test first — park, sync, drive a known pulse count per
axis with the guider idle, read back — and it is the next thing after this.
