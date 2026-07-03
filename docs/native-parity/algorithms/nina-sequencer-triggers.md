# NINA Sequencer Trigger Semantics — Algorithm Dossier

**Purpose**: single source of truth for reimplementing NINA's sequencer trigger semantics in Rust
(AstroDeck native engine) without reading the original C# source.

**Provenance**: extracted from the NINA reference clone at
`C:/Users/bear/astro/references/nina`, commit `7c9de0c4202f2d054f58b0cb6b15d561f8b6b4b2` (2025-06-05),
license MPL-2.0. Exact file/line references are in the [Source Map](#source-map).

Conventions used below:

- All pseudocode is Rust-flavored. `Duration` is a signed duration (NINA's `TimeSpan` is signed —
  negative values occur and are meaningful in the flip math; use `chrono::Duration` or an `f64`
  seconds type, **not** `std::time::Duration` which is unsigned).
- Angles: `ra_hours` in [0,24), `dec_deg`, `lst_hours` = local apparent sidereal time in hours.
- `now()` = local wall clock (NINA uses `DateTime.Now` everywhere in this code).
- "LIGHT" is a literal image-type string; others are "SNAPSHOT", "FLAT", "DARK", "BIAS", "NONE".

---

## 1. Trigger execution model (container engine)

### 1.1 Entities

A **container** holds ordered `items` (instructions or nested containers), a set of `conditions`
(loop predicates) and a set of `triggers`. Containers execute via a strategy:

- **SequentialStrategy** — the normal case (used by `SequentialContainer`, DSO containers, root areas).
- **ParallelStrategy** — runs all non-disabled items concurrently (`futures::join_all`); no triggers or
  conditions are evaluated by the parallel strategy itself.

Every trigger owns a private `trigger_runner: SequentialContainer` holding the instruction(s) it
executes when it fires (e.g. a `RunAutofocus` item, a `Dither` item). Only one trigger with a given
name may exist per container unless the trigger opts into `allow_multiple_per_set` (default
`false`).

Entity status enum: `CREATED, RUNNING, FINISHED, FAILED, SKIPPED, DISABLED`.

### 1.2 Sequential execution loop (exact semantics)

```rust
fn sequential_execute(ctx: &Container, token: CancelToken) {
    let mut previous: Option<Item> = None;
    ctx.iterations = 0;
    // lifecycle: SequenceBlockInitialize on every item, condition, trigger of this container
    initialize_block(ctx);
    // NB: Sequencer.Start() separately calls Initialize() (one-time) on every item/condition/
    // trigger of the WHOLE tree, recursively, before the root container runs.  Triggers use
    // Initialize() to snapshot baselines (temperature, filter).
    let result = (|| {
        loop {
            let (next, can_continue) = get_next_item(ctx, &previous); // first CREATED item + conditions check
            if next.is_none() || !can_continue { break; }
            start_block(ctx); // SequenceBlockStarted on items/conditions/triggers

            let (mut next, mut can_continue) = get_next_item(ctx, &previous);
            while let (Some(n), true) = (&next, can_continue) {
                token.throw_if_cancelled()?;
                // BEFORE-item triggers: this container's triggers, then walk *up* the
                // parent chain calling each ancestor's triggers too.
                run_triggers(ctx, &previous, &n)?;      // ShouldTrigger -> trigger.run()
                n.run()?;                               // the actual instruction
                previous = Some(n.clone());
                (next, can_continue) = get_next_item(ctx, &previous);
                // AFTER-item triggers (ShouldTriggerAfter); same parent-chain walk.
                run_triggers_after(ctx, &previous, &next)?;
            }

            finish_block(ctx); // iterations += 1; SequenceBlockFinished on items/conds/triggers

            if can_continue_conditions(ctx, &previous, &next) {
                // loop again: reset all child progress (containers reset recursively)
                for item in ctx.items { item.reset(); }
            }
        }
        // Anything still CREATED when the loop exits is marked SKIPPED.
        for item in ctx.items.filter(|i| i.status == CREATED) { item.skip(); }
    })();
    teardown_block(ctx); // SequenceBlockTeardown on items/conditions/triggers (always, finally)
}
```

Key facts:

- `get_next_item` returns the **first item with status CREATED** (items already FINISHED/FAILED are
  passed over; a failed item does not stop the container).
- `can_continue`: if the container has ≥1 non-disabled condition, all conditions must pass
  (`RunCheck(previous, next)`); a failing condition is set FINISHED and the block ends.
  The check is NOT short-circuited: every condition's `RunCheck` runs on every evaluation, and
  every failing one is marked FINISHED in the same pass (side effects of `RunCheck` in custom
  conditions therefore always execute).
  With no conditions, the container runs exactly once (`iterations < 1`). Parents' conditions are
  ANDed in recursively — a parent's expired condition also stops the child.
- **Trigger evaluation order**: before each item, triggers of the item's container run first, then
  the parent's, then grandparent's, etc. (bottom-up walk to root). Within one container, triggers
  run in their list order, sequentially.
- `run_triggers` per trigger: skip if `DISABLED`; call `should_trigger(previous, next)`; if true,
  `trigger.run(context)` where `context = next.parent ?? previous.parent ?? this-container`.
  **Any exception thrown by a trigger (evaluation or execution) is caught and logged; the sequence
  continues.** A trigger cannot abort the sequence.
- `trigger.run()` (base class): validates first (`IValidatable::validate`; a validation failure
  marks the trigger FAILED and raises a failure event but, per the previous point, does not stop
  the sequence), resets its `trigger_runner`, calls `execute()`, then if any instruction inside the
  runner has status FAILED the trigger as a whole is marked FAILED. Cancellation resets status to
  CREATED.
- After-item triggers (`ShouldTriggerAfter`) default to `false`; none of the built-in triggers in
  this dossier use them.

### 1.3 Lifecycle hooks summary

| Hook | When |
|---|---|
| `Initialize` | Once, before the root container starts (whole tree, recursive) |
| `SequenceBlockInitialize` | When the owning container's strategy starts executing |
| `SequenceBlockStarted` | At the start of each iteration of the owning container |
| `SequenceBlockFinished` | At the end of each iteration |
| `SequenceBlockTeardown` | When the owning container's strategy exits (finally) |
| `Teardown` | Once, after the root container finishes (whole tree, recursive) |
| `AfterParentChanged` | When the entity is attached/detached from a container (edit-time AND deserialization) |

### 1.4 Common gate: "is the next item a light exposure?"

Every cadence-style trigger begins with:

```rust
fn is_next_light_exposure(next: Option<&Item>) -> bool {
    match next {
        Some(item) => item.as_exposure_item()
                          .map(|e| e.image_type == "LIGHT")
                          .unwrap_or(false),
        None => false,
    }
}
```

If false → `should_trigger` returns false immediately. (Applies to: all 5 autofocus triggers,
DitherAfterExposures, CenterAfterDriftTrigger, RestoreGuiding. Does **not** apply to
MeridianFlipTrigger or SynchronizeDomeTrigger.)

### 1.5 Common gate: "too close to meridian flip"

Used by every trigger that starts a long operation (all AF triggers, dither, center-after-drift):

```rust
/// true  => do NOT run the operation, the flip would interrupt it
/// false => safe to run (or no meridian flip trigger exists anywhere up the tree)
fn is_too_close_to_meridian_flip(context: &Container, estimated_duration: Duration) -> bool {
    // 1.5x safety factor on the estimate
    let estimated_finish = now() + estimated_duration * 1.5;
    // Walk this container and all ancestors; the first MeridianFlipTrigger found supplies
    // its cached `latest_flip_time` (computed on its last ShouldTrigger evaluation).
    let flip_time = find_meridian_flip_trigger_upwards(context)
        .map(|t| t.latest_flip_time)
        .unwrap_or(DATETIME_MIN);
    flip_time > now() && estimated_finish > flip_time
}
```

`estimated_duration` passed by the callers is:
`first_item_of_trigger_runner.estimated_duration() + next_item.estimated_duration()`
(for CenterAfterDrift: `1 minute + next_item.estimated_duration()`). If `next` is `None`, the sum
degrades to `Duration::ZERO` (C# null-propagation quirk; in practice `next` is never None here
because of gate 1.4).

Estimated durations of relevant instructions:

- `TakeExposure`: `exposure_time` seconds.
- `Dither`: `guider_settings.settle_timeout` seconds (default 40).
- `RunAutofocus`:

```rust
fn autofocus_estimated_duration(profile: &Profile, instruction_attempts: i32) -> Duration {
    let filter = current_filter();
    let fs = &profile.focuser_settings;
    let mut exposure_time = fs.auto_focus_exposure_time;                    // default 6 s
    if let Some(f) = filter {
        let per_filter = profile.filters[f.position].auto_focus_exposure_time;
        if per_filter > 0.0 { exposure_time = per_filter; }
    }
    // +2: initial exposure + final validation exposure
    let steps = fs.auto_focus_initial_offset_steps * 2 * fs.auto_focus_number_of_frames_per_point + 2;
    let settle = fs.focuser_settle_time + 2.0; // +2 s assumed focuser movement time
    let attempts = min(10, max(1, fs.auto_focus_total_number_of_attempts) * max(1, instruction_attempts));
    Duration::seconds_f64(attempts as f64 * steps as f64 * (exposure_time + settle))
}
```

### 1.6 Image history model (inputs to cadence triggers)

- Every exposure captured through the imaging pipeline gets a **monotonically increasing integer
  id** (`Interlocked.Increment` on a process-global counter, starts at 1; reset to 0 when the user
  clears the plot/history).
- Only `TakeExposure`/`TakeSubframeExposure` items with `ImageType ∈ {LIGHT, SNAPSHOT}` append an
  entry to `ImageHistory` (an ordered list). Entry fields used by triggers:
  `{ id: i32, image_type: String, filter: String, hfr: f64, temperature: f64 (focuser),
     focuser_position, ... }`. HFR and filter are populated **when the image is saved** (from star
  detection analysis) — an entry may briefly exist with `hfr == 0/NaN`.
- After each successful autofocus run, the *last* image-history entry is annotated and pushed onto
  `AutoFocusPoints` (an ordered list) with
  `{ id: <id of that last image>, af: { temperature: f64 (focuser temp at AF), time: DateTime,
     filter: String, old_position, new_position } }`. If the history is empty a synthetic point
  with `id = 0, image_type = "NONE"` is used.
- "Since last AF" therefore means `entry.id > last_af.id`.

---

## 2. Meridian flip

### 2.1 Core astronomy math (`NINA.Astrometry.MeridianFlip`)

All three functions first transform the coordinates to the current-epoch (JNOW) frame.

**Time to meridian** (result in [0h, 12h)):

```rust
fn time_to_meridian(coords_jnow: Coordinates, lst_hours: f64) -> Duration {
    let mut h = (coords_jnow.ra_hours - lst_hours) % 12.0; // NB: modulo 12, NOT 24
    if h < 0.0 { h += 12.0; }
    Duration::hours_f64(h)
}
```

The mod-12 makes "time to meridian" and "time to anti-meridian" indistinguishable; the pier-side
logic below resolves the ambiguity.

**Expected pier side** — the pier side in which the counterweight is down for these coordinates:

```rust
enum PierSide { East, West, Unknown }

fn expected_pier_side(coords_jnow: Coordinates, lst_hours: f64) -> PierSide {
    let mut h = (coords_jnow.ra_hours - lst_hours) % 24.0;
    if h < 0.0 { h += 24.0; }
    // hour angle HA = lst - ra = -h (mod 24); HA in 0..12  => pier West
    if h < 12.0 { PierSide::West } else { PierSide::East }
}
```

**Time to meridian flip** (result in hours; this is what the UI and trigger consume):

```rust
fn time_to_meridian_flip(
    settings: &MeridianFlipSettings,
    coords: Coordinates,           // transformed to JNOW inside time_to_meridian/expected_pier_side
    lst_hours: f64,
    current_side_of_pier: PierSide,
) -> Duration {
    // Shift LST *back* by MaxMinutesAfterMeridian so the returned time is "time until
    // (meridian + MaxMinutesAfterMeridian)".  Doing it this way (instead of adding the
    // minutes to the result) stays correct when the scope is already past the meridian
    // but not yet past the flip point.
    let projected_lst = euclidian_mod(lst_hours - settings.max_minutes_after_meridian / 60.0, 24.0);
    let mut ttf = time_to_meridian(coords, projected_lst);

    if settings.use_side_of_pier {
        if current_side_of_pier == PierSide::Unknown {
            // log debug; ignore pier side entirely
        } else {
            let ttm = time_to_meridian(coords, lst_hours);
            let expected = expected_pier_side(coords, lst_hours);
            if ttm < Duration::hours(1) && expected != current_side_of_pier {
                // Close to the meridian but the mount is already flipped (e.g. slewed in a
                // flipped state): next flip is ~12h away.
                ttf += Duration::hours(12);
            }
            if ttf < Duration::hours(1)
                && ttm > Duration::hours(12) - Duration::minutes_f64(settings.max_minutes_after_meridian)
                && expected == current_side_of_pier
            {
                // Just passed the meridian recently and already on the correct side:
                // next flip is ~12h away.
                ttf += Duration::hours(12);
            }
        }
    }
    if ttf >= Duration::hours(24) { ttf -= Duration::hours(24); } // safeguard
    ttf
}

fn euclidian_mod(x: f64, y: f64) -> f64 { let r = x % y; if r < 0.0 { r + y } else { r } }
```

The mount layer (see 2.6) exposes `time_to_meridian_flip` in **hours**; it returns `24.0` when
tracking is disabled or on any driver error.

### 2.2 Settings and defaults (`MeridianFlipSettings`)

| Setting | Default | Unit | Notes |
|---|---|---|---|
| `minutes_after_meridian` | **5** | minutes | earliest flip = meridian + this. Setter clamps: raising it above `max_minutes_after_meridian` drags max up |
| `max_minutes_after_meridian` | **10** | minutes | latest flip = meridian + this. Setter clamps: lowering it below `minutes_after_meridian` drags min down |
| `pause_time_before_meridian` | **0** | minutes | ≠0 ⇒ "pause mode": stop tracking this many minutes *before* the meridian and wait through it (for scopes that would hit the pier) |
| `use_side_of_pier` | **true** | bool | consult mount-reported pier side (source comment claims default false; the code sets true — code wins) |
| `recenter` | **true** | bool | plate-solve recenter after flip |
| `settle_time` | **30** | seconds | wait after flip slew (and as a dedicated step near the workflow end) |
| `auto_focus_after_flip` | **false** | bool | run AF after flip |
| `rotate_image_after_flip` | **false** | bool | add 180° to the display/framing image rotation |

### 2.3 `MeridianFlipTrigger.should_trigger` — exact algorithm

Trigger state: `last_flip_time: DateTime` (init `DATETIME_MIN`), `last_flip_coords: Option<Coordinates>`
(both reset in `AfterParentChanged`, i.e. whenever the trigger is (re)attached to a container),
plus published `earliest_flip_time` / `latest_flip_time` (init `DATETIME_MIN`; consumed by
`is_too_close_to_meridian_flip`).

```rust
fn should_trigger(&mut self, _prev: Option<&Item>, next: Option<&Item>) -> bool {
    let tel = telescope.info();
    let s = settings; // MeridianFlipSettings via profile

    // --- Hard guards: publish "no flip planned" and bail ---------------------------------
    if !tel.connected || tel.time_to_meridian_flip_hours.is_nan()
        || tel.at_park            // parked
        || tel.at_home            // at home position
        || !tel.tracking_enabled  // not tracking
    {
        self.earliest_flip_time = DATETIME_MIN;
        self.latest_flip_time  = DATETIME_MIN;
        return false; // (not-connected/NaN is logged as error, the rest as info)
    }

    // --- Recent-flip guard (only when pier side is not used) -----------------------------
    // A flip for the same target can only be needed every ~12h; if we flipped <11h ago and
    // the scope still points within 20' of where we flipped, skip.
    if !s.use_side_of_pier
        && now() - self.last_flip_time < Duration::hours(11)
        && self.last_flip_coords.map_or(false, |c|
              angular_separation(c, tel.coordinates).arcminutes() < 20.0)
    {
        return false;
    }

    let next_duration_s = next.map_or(0.0, |n| n.estimated_duration().as_secs_f64());

    // --- Window math ----------------------------------------------------------------------
    // maximum_time_remaining = time until (meridian + MaxMinutesAfterMeridian)   [from mount]
    let max_remaining = Duration::hours_f64(tel.time_to_meridian_flip_hours);
    // minimum_time_remaining = time until (meridian + MinutesAfterMeridian), clamped >= 0
    let mut min_remaining = max_remaining
        - Duration::minutes_f64(s.max_minutes_after_meridian - s.minutes_after_meridian);
    if min_remaining < Duration::ZERO { min_remaining = Duration::ZERO; }

    let original_max_remaining = max_remaining;
    let mut max_remaining = max_remaining;
    if s.pause_time_before_meridian != 0.0 {
        // Pause mode: the flip window collapses to a single instant
        //   (meridian - PauseTimeBeforeMeridian);
        // note this value may be negative (not clamped).
        min_remaining = min_remaining
            - Duration::minutes_f64(s.minutes_after_meridian)
            - Duration::minutes_f64(s.pause_time_before_meridian);
        max_remaining = min_remaining;
    }

    // --- Publish flip-time window (consumed by IsTooCloseToMeridianFlip) ------------------
    if s.pause_time_before_meridian == 0.0 {
        self.earliest_flip_time = now() + min_remaining;
        self.latest_flip_time  = now() + original_max_remaining;
    } else {
        let t = now() + original_max_remaining
              - Duration::minutes_f64(s.max_minutes_after_meridian)
              - Duration::minutes_f64(s.pause_time_before_meridian);
        self.earliest_flip_time = t;
        self.latest_flip_time  = t;
    }

    // --- Decision --------------------------------------------------------------------------
    if min_remaining <= Duration::ZERO && max_remaining > Duration::ZERO {
        // Inside the flip window (only reachable when pause == 0, since then min==max).
        return true;
    }

    let sop_usable = s.use_side_of_pier && tel.side_of_pier != PierSide::Unknown;
    // (if use_side_of_pier && side unknown: log error "ignoring side of pier")

    if sop_usable {
        let no_remaining_time = max_remaining <= Duration::seconds_f64(next_duration_s);
        if no_remaining_time {
            // Next instruction wouldn't finish before the latest flip time.
            // Project the pier side to *after* the flip time and compare.
            let projected_lst = euclidian_mod(
                tel.lst_hours + original_max_remaining.as_hours_f64(), 24.0);
            let target_sop = expected_pier_side(tel.coordinates, projected_lst);
            return tel.side_of_pier != target_sop; // equal => already flipped => no flip
        } else {
            // Still time before the flip. Double-check pier side *now*.
            let target_sop = expected_pier_side(tel.coordinates, tel.lst_hours);
            if tel.side_of_pier == target_sop {
                return false; // everything consistent, wait
            }
            // Pier side mismatch while the clock says there is time: a flip most likely
            // failed to happen. Allow a "delayed flip" for ~1 hour after it was due:
            let delayed = max_remaining <= Duration::hours(12)
                && max_remaining >= Duration::hours(11)
                    - Duration::minutes_f64(s.max_minutes_after_meridian)
                    - Duration::minutes_f64(s.pause_time_before_meridian);
            return delayed;
        }
    } else {
        // No pier side available: add a 2-minute buffer to the next-instruction estimate.
        let no_remaining_time =
            max_remaining <= Duration::seconds_f64(next_duration_s) + Duration::minutes(2);
        return no_remaining_time;
    }
}
```

Derived behavior (worth encoding in tests):

- With `pause == 0`, the flip window is `[meridian + minutes_after, meridian + max_minutes_after]`;
  the trigger normally fires as soon as `min_remaining` hits 0, i.e. at `meridian + minutes_after`,
  **or earlier** if the next instruction cannot finish before `meridian + max_minutes_after`
  (pier-side permitting).
- With `pause != 0`, the trigger fires when the time until `(meridian − pause)` drops below the
  next instruction's duration (+2 min without pier side); the workflow then stops tracking and
  waits out the remainder (see 2.5), so the mount sits still through the meridian passage.
- `validate()`: telescope must be connected (hard fail); camera-not-connected (when `recenter`) and
  focuser-not-connected (when `auto_focus_after_flip`) are advisory issues only (do not fail
  validation).

### 2.4 `MeridianFlipTrigger.execute` — target resolution and wait computation

```rust
async fn execute(&mut self, context: &Container) {
    // Find target coordinates by walking up the container tree to the first
    // DeepSkyObject container whose Target, Target.InputCoordinates AND Target.DeepSkyObject
    // are all non-null (a DSO container failing that test does NOT stop the walk — the search
    // continues upward). The hit yields (coordinates, position_angle, shift_tracking_rate);
    // only the coordinates are used here.
    let mut target = retrieve_context_coordinates(context).map(|c| c.coordinates);
    if target.is_none() {
        target = Some(telescope.current_position()); // warn: no target info, using scope coords
    }
    if let Some(t) = target {
        if t.ra == 0.0 && t.dec == 0.0 {
            target = Some(telescope.current_position()); // warn: all-zero coords unlikely intended
        }
    }

    // Wait time until the flip may happen = minimum time remaining (see 2.3), i.e. time
    // until (meridian + MinutesAfterMeridian), NOT reduced by pause.
    let mut time_to_flip = calculate_minimum_time_remaining();
    if time_to_flip > Duration::hours(2) {
        // A "delayed flip" scenario (flip overdue by up to ~1h): flip immediately.
        time_to_flip = Duration::ZERO;
    }

    self.last_flip_time = now();
    self.last_flip_coords = target;
    meridian_flip_workflow(target.unwrap(), time_to_flip).await; // section 2.5
}
```

Note the trigger does not inspect the workflow's success; only a thrown exception would mark the
trigger FAILED (and even then the sequence continues, per 1.2).

### 2.5 Flip workflow state machine (`MeridianFlipVM`)

Step list is built fresh per flip. `guider_relevant = guider.connected && guider is not DirectGuider`
(direct guiding through the mount needs no stop/resume/star-selection).

| # | Step id | Included when | Action |
|---|---|---|---|
| 1 | `StopAutoguider` | guider_relevant | `guider.stop_guiding()` |
| 2 | `PassMeridian` | always | stop tracking → count down `time_to_flip` → re-enable tracking |
| 3 | `Flip` | always | mount flip slew (2.6) + settle + dome sync |
| 4 | `Autofocus` | settings.auto_focus_after_flip | full AF run with current filter; result appended to AF history |
| 5 | `Recenter` | settings.recenter | plate-solve centering loop on the pre-flip target coordinates (section 5.3) — **best-effort**: on failure, log error + notification, step still returns success |
| 6 | `SelectNewGuideStar` | guider_relevant | `guider.auto_select_guide_star()` |
| 7 | `ResumeAutoguider` | guider_relevant | `guider.start_guiding(force_calibration=false)` |
| 8 | `Settle` | always | wait `settings.settle_time` seconds (1 s countdown loop) |
| 9 | `RotateImageAfterFlip` | settings.rotate_image_after_flip | display/framing image rotation += 180° |

```rust
async fn meridian_flip_workflow(target: Coordinates, time_to_flip: Duration) -> bool {
    raise_event(BeforeMeridianFlip { target });
    let result: Result<(), Error> = (async {
        for step in build_steps() {
            let ok = step.run().await?;     // exceptions propagate; a `false` result does NOT
            step.finished = ok;             // abort the workflow (success only affects step UI
        }                                   // state) — quirk preserved from AutomatedWorkflow
        Ok(())
    }).await;

    let flip_ok = match result {
        Ok(())                    => true,
        Err(Error::Cancelled)     => true,  // !! NINA returns success on user cancellation
        Err(e) => {
            // failure path: notify, then best-effort recovery
            let _ = guider.start_guiding(false).await;   // resume guiding (errors notified)
            telescope.set_tracking_enabled(true);        // re-enable tracking
            false
        }
    };
    raise_event(AfterMeridianFlip { success: flip_ok, target });
    flip_ok
}
```

**PassMeridian** (step 2):

```rust
async fn pass_meridian(mut remaining: Duration) {
    telescope.set_tracking_enabled(false);
    loop {
        report_status(remaining);
        remaining -= sleep_cancellable(Duration::seconds(1)).await; // returns actual elapsed
        if remaining.as_secs_f64() < 1.0 { break; }                 // do-while: min one second
    }
    telescope.set_tracking_enabled(true);
}
```

**Flip** (step 3):

```rust
async fn do_flip(target: Coordinates) -> bool {
    let ok = telescope.meridian_flip(target).await;   // section 2.6; false is NOT fatal here
    settle(settings.settle_time).await;               // same 1 s countdown loop
    let dome = dome.info();
    if dome.connected && dome.can_set_azimuth {
        if dome_follower.is_following {
            dome_follower.wait_for_dome_synchronization().await;   // errors → warn, continue
        } else if !dome_follower.trigger_telescope_sync().await {  // one-shot sync
            warn_notification("dome sync failure during meridian flip"); // continue
        }
    }
    ok
}
```

**Autofocus** (step 4): resolves the profile filter matching the wheel's current filter position,
runs the standard autofocus routine, appends the report to the AF-point history (this resets the
baselines of the HFR/temperature/time AF triggers).

**SelectNewGuideStar / ResumeAutoguider** (PHD2 backend):

- `auto_select_guide_star`: if PHD2 app-state ≠ `Looping` → send `loop`, wait 5 s; read the guide
  exposure duration, wait `exposure + 1 s`; send `find_star` with an optional ROI. ROI: when profile
  `phd2_roi_pct < 100`, centered box of `pct`× frame dimensions:
  `x = w/2 − (w/2)·pct, y = h/2 − (h/2)·pct, width = w·pct, height = h·pct` (integer truncation).
- `start_guiding(force_calibration=false)` sends PHD2 `guide` with settle params
  (`pixels = settle_pixels`, `time = settle_time`, `timeout = settle_timeout`) and waits for settle
  completion.

### 2.6 Mount-level flip slew (`AscomTelescope::meridian_flip`)

Constants: `RETRY_ATTEMPTS = 20`, `RETRY_WAIT = 60 s`.

```rust
async fn mount_meridian_flip(target: Coordinates) -> bool {
    // any thrown driver error → notify + return false (finally: clear TargetCoordinates/TargetSideOfPier)
    if !tracking_enabled { set_tracking_enabled(true); }

    let target_sop = expected_pier_side(target.to_jnow(), lst_hours());
    if settings.use_side_of_pier && side_of_pier() == target_sop {
        return true; // already on the correct side; no flip required
    }

    let target = target.transform(mount_equatorial_system()); // JNOW or J2000 per driver

    // If the driver can't set pier side, treat that part as already done.
    let mut pier_side_done = !can_set_pier_side();
    let mut check_after_first_slew = false;

    // If the mount is CURRENTLY counterweight-down (expected == actual for current position),
    // do NOT set pier side before slewing — a SideOfPier write now could produce a
    // counterweight-UP slew. Set it only after the first slew, if the mount didn't flip.
    if expected_pier_side(current_coords().to_jnow(), lst_hours()) == side_of_pier() {
        pier_side_done = true;
        check_after_first_slew = true;
    }

    let mut retries = 0;
    loop {
        if !pier_side_done {
            pier_side_done = set_pier_side(target_sop).await; // write SideOfPier; wait 2 s;
        }                                                     // then wait while Slewing
        let slew_ok = slew_to_coordinates(target).await;      // normal slew, 10 min timeout

        if check_after_first_slew && side_of_pier() != target_sop {
            pier_side_done = set_pier_side(target_sop).await;
            check_after_first_slew = false;
        }
        if !pier_side_done { pier_side_done = side_of_pier() == target_sop; }

        if slew_ok && pier_side_done { 
            // if retries > 0: warn user that guiding may need extra care
            return true;
        }
        retries += 1;
        if retries > RETRY_ATTEMPTS { return false; } // notify error
        sleep(RETRY_WAIT).await; // warn notification with retry count
    }
}
```

The mediator layer wraps this with: transform target to the mount's epoch; after the flip, wait for
dome synchronization and one telescope-poll cycle before returning. The mediator's regular slew
(used inside the flip) also: refuses when parked; kicks off a parallel dome sync when dome follows
or `sync_slew_dome_when_mount_slews`; then waits `telescope_settings.settle_time` (default **5 s**)
after the slew completes; overall slew timeout 10 minutes → returns false.

---

## 3. Autofocus triggers

All five share: gate 1.4 (next item must be a LIGHT exposure), gate 1.5 (meridian-flip proximity
veto applied only when the condition math says "trigger"), a `trigger_runner` containing exactly one
`RunAutofocus` instruction, and `validate()` requiring camera + focuser connected (filter-change
trigger additionally requires a filter wheel).

### 3.1 AutofocusAfterExposures (cadence)

Config: `after_exposures: i32`, default **5**.

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    let last_af_id = af_points.last().map_or(0, |p| p.id);
    let lights_since_af = image_history.iter()
        .filter(|x| x.image_type == "LIGHT" && x.id > last_af_id)
        .count();
    let progress = lights_since_af % after_exposures;   // published for UI
    let mut fire = lights_since_af > 0 && progress == 0;
    if fire && is_too_close_to_meridian_flip(parent, af_duration + next_duration) { fire = false; }
    fire
}
```

(Fires on every multiple of `after_exposures` light frames since the last AF.)

Edge case the source does NOT guard: `after_exposures == 0` makes the modulo throw a
divide-by-zero exception inside `should_trigger`; the container's trigger-runner catch (§1.2)
swallows it and logs an error before *every* item, so the trigger silently never fires. (Contrast
DitherAfterExposures, which explicitly treats 0 as "disabled".) A Rust port must not panic here —
treat `after_exposures <= 0` as never-fire (and log once).

### 3.2 AutofocusAfterTimeTrigger (elapsed time)

Config: `amount: f64` minutes, default **30**.
State: `initial_time` — set once at the first `SequenceBlockInitialize` (guarded by an
`initialized` flag; NOT reset on later blocks).

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    let elapsed = match af_points.last() {
        None       => now() - initial_time,
        Some(last) => now() - last.af.time,
    };
    let mut fire = elapsed >= Duration::minutes_f64(amount);
    if fire && is_too_close_to_meridian_flip(parent, af_duration + next_duration) { fire = false; }
    fire
}
```

### 3.3 AutofocusAfterTemperatureChangeTrigger (Δ°C)

Config: `amount: f64` °C, default **5**.
State: `initial_temperature` — focuser temperature captured at `Initialize()` (sequence start);
`NaN` if unavailable.

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    if image_history.is_empty() { return false; }        // no trigger before the first image
    let t = focuser.info().temperature;                  // NaN if sensorless
    if t.is_nan() { return false; }
    let last_af = af_points.last();
    if last_af.is_none() && initial_temperature.is_nan() {
        initial_temperature = t;                         // late baseline capture
    }
    let delta = match last_af {
        None       => (initial_temperature - t).abs(),
        Some(af)   => (af.af.temperature - t).abs(),     // focuser temp recorded at last AF
    };
    // published: delta_t = round(delta, 2)
    let mut fire = delta >= amount;
    if fire && is_too_close_to_meridian_flip(parent, af_duration + next_duration) { fire = false; }
    fire
}
```

### 3.4 AutofocusAfterFilterChange

No numeric config. State: `last_af_filter: Option<String>` — set to the current wheel filter name
at `Initialize()` AND at every `SequenceBlockInitialize()`.

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    // If the profile designates a dedicated "autofocus filter" (any filter with
    // auto_focus_filter == true), the AF history's filter is meaningless for comparison
    // (AF always uses that filter), so keep the locally tracked name.  Otherwise sync the
    // baseline to the filter recorded in the last AF report:
    let has_af_filter = profile.filters.iter().any(|f| f.auto_focus_filter);
    if !has_af_filter {
        if let Some(last_af) = af_points.last() {
            last_af_filter = last_af.af.filter.clone();
        }
    }
    let current = filter_wheel.info().selected_filter.map(|f| f.name);
    let mut fire = false;
    match &last_af_filter {
        None    => { last_af_filter = current; }              // first observation: baseline only
        Some(p) => if Some(p) != current.as_ref() {
                       last_af_filter = current;              // update baseline immediately,
                       fire = true;                           // even if the flip veto cancels below
                   }
    }
    if fire && is_too_close_to_meridian_flip(parent, af_duration + next_duration) { fire = false; }
    fire
}
```

Note the baseline updates *before* the meridian-flip veto — a filter change suppressed by the veto
will NOT fire later (matches NINA behavior; arguably a bug, preserved here for parity).

### 3.5 AutofocusAfterHFRIncreaseTrigger (HFR trend)

Config: `amount: f64` percent, default **5**; `sample_size: i32`, default **10**, setter rejects
values `< 3` (silently keeps the old value).

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    let last_af = af_points.last();

    // 1) Candidate set: LIGHT frames with a valid HFR (non-NaN and > 0), after the last AF,
    //    and (if a filter wheel is connected with a selected filter) matching the CURRENT filter.
    //    NB: the source tests only `!IsNaN(hfr) && hfr > 0` — a hypothetical +infinity HFR would
    //    pass; do NOT tighten to is_finite() if bit-exact parity matters (in practice HFR is
    //    always finite).
    let mut hist: Vec<&HistoryPoint> = image_history.iter()
        .filter(|x| x.image_type == "LIGHT" && !x.hfr.is_nan() && x.hfr > 0.0)
        .collect();
    if let Some(af) = last_af { hist.retain(|x| x.id > af.id); }
    if let Some(f) = current_filter_name() { hist.retain(|x| x.filter == f); }

    if hist.is_empty() { /* original_hfr = 0 */ return false; }

    // 2) Baseline: the *minimum* HFR over the whole candidate set (not just the window).
    let original_hfr = hist.iter().map(|x| x.hfr).fold(f64::INFINITY, f64::min);

    // 3) Trend window: the last `sample_size` points.
    let start = hist.len().saturating_sub(sample_size as usize);
    if hist.len() <= start + 2 { /* trend = 0 */ return false; }   // need >= 3 points in window
    let data: Vec<f64> = hist[start..].iter().map(|x| x.hfr).collect();
    if data.len() < 3 { return false; }

    // 4) Ordinary least squares simple linear regression y = a + b*x over
    //    x = 1..=n (1-based sample index), y = HFR.
    //    b = Σ(x-x̄)(y-ȳ) / Σ(x-x̄)² ;  a = ȳ - b·x̄
    let n = data.len() as f64;
    let xbar = (n + 1.0) / 2.0;
    let ybar = data.iter().sum::<f64>() / n;
    let mut sxy = 0.0; let mut sxx = 0.0;
    for (i, y) in data.iter().enumerate() {
        let x = (i + 1) as f64;
        sxy += (x - xbar) * (y - ybar);
        sxx += (x - xbar) * (x - xbar);
    }
    let b = sxy / sxx;
    let a = ybar - b * xbar;
    let hfr_trend = a + b * n;                 // smoothed "current" HFR = fit evaluated at x = n

    // 5) Percentage increase of the trend over the best HFR:
    let trend_pct = round2((1.0 - original_hfr / hfr_trend) * 100.0);

    let mut fire = trend_pct > amount;         // strictly greater
    if fire && is_too_close_to_meridian_flip(parent, af_duration + next_duration) { fire = false; }
    fire
}
```

Edge cases handled by the source: NaN/zero HFR entries are excluded everywhere; a negative trend
(improving focus) gives `trend_pct < 0` → never fires; `hfr_trend` ≤ 0 can produce a huge/negative
percentage — no special handling (kept for parity); when the filter wheel is disconnected the
filter filter is skipped entirely (all lights count).

---

## 4. Dither triggers

### 4.1 DitherAfterExposures

Config: `after_exposures: i32`, default **1** (dither before every light). `0` disables (trigger
never runs its action; `ProgressExposures` reports 0; validation skips the guider check).
State: `last_trigger_id: i32 = 0` — the image-history count at the moment the dither last ran.

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    let count = image_history.len() as i32;    // ALL history entries (LIGHT + SNAPSHOT)
    if last_trigger_id > count { last_trigger_id = 0; }   // history was cleared
    let progress = if after_exposures > 0 { count % after_exposures } else { 0 };
    let mut fire = last_trigger_id < count && count > 0 && progress == 0;
    if fire && is_too_close_to_meridian_flip(parent,
            settle_timeout + next_duration) { fire = false; }
    fire
}

async fn execute() {
    if after_exposures > 0 {
        last_trigger_id = image_history.len() as i32;   // BEFORE the dither runs
        trigger_runner.run().await;                     // single `Dither` instruction
    }
}
```

Cadence note: the modulo is over the **global image-history count**, not a per-target counter, so
snapshots (e.g. plate-solve exposures recorded by TakeExposure with type SNAPSHOT) advance the
counter. `last_trigger_id` prevents double-dithers when no new image arrived between evaluations.

### 4.2 Dither instruction / PHD2 semantics

Guider settings (profile defaults): `dither_pixels = 5.0`, `dither_ra_only = false`,
`settle_pixels = 1.5`, `settle_time = 10` s, `settle_timeout = 40` s.

PHD2 backend `dither()`:

1. If app-state ≠ `Guiding` → warn ("skipped: not guiding" / "lost lock") and return `false`.
2. Wait for any in-flight settle to finish.
3. Send `dither { amount: dither_pixels, ra_only: dither_ra_only, settle: { pixels, time, timeout } }`.
4. On RPC error → return false. Otherwise mark `settling = true` and wait for the PHD2
   `SettleDone` event; failsafe: if no event within `settle_timeout + 10 s`, warn and proceed
   (500 ms poll loop).

### 4.3 RestoreGuiding

Fires before **every** LIGHT exposure (no other condition); its runner holds
`StartGuiding { force_calibration: false }`, which is a no-op when the guider is already guiding.
Purpose: automatically resume guiding that something else stopped. Not part of the default
template; validation requires a connected guider. (No meridian-flip veto.)

---

## 5. CenterAfterDriftTrigger (drift re-centering)

### 5.1 Configuration

| Knob | Default | Constraint |
|---|---|---|
| `distance_arc_minutes` | **10.0** | setter ignores values ≤ 0 |
| `after_exposures` (solve every N lights) | **1** | — |
| `coordinates` | inherited from parent DSO container | if not inherited → validation issue "no target" |

Published helper: `distance_pixels = distance_arc_minutes * 60 / arcsec_per_pixel`, where
`arcsec_per_pixel = (pixel_size_um / focal_length_mm) * (180/π * 3600 / 1000)` (i.e. `206.2648...
per mm/µm unit mix`).

### 5.2 Passive drift measurement (`PlatesolvingImageFollower`)

Created at `SequenceBlockInitialize` (and torn down at `SequenceBlockTeardown` /
detach; also created on attach if the parent is already RUNNING). It hooks the global
**BeforeImageSaved** event:

```rust
fn on_before_image_saved(image) {
    if image.image_type != "LIGHT" { return; }
    progress_exposures += 1;
    if progress_exposures < after_exposures { return; }
    if solver_task_running { return; }        // never queue two solves; skip this frame
    spawn(solve_last_image(image));           // background, does NOT delay the save
}

async fn solve_last_image(image) {
    // Hint coordinates: image header telescope coords, else current mount position.
    let params = PlateSolveParameter {
        coordinates: image.meta.telescope_coords.or(mount_position_if_connected()),
        binning: image.meta.bin_x,
        // remaining fields from profile plate-solve settings (see 5.4)
        disable_notifications: true,
        ..from_profile()
    };
    let result = image_solver.solve(image, params).await;   // with blind failover, 5.4
    if !result.success { warn_notification("platesolve failed"); return; }
    last_coordinates = Some(result.coordinates);            // fires observer below
    progress_exposures = 0;                                 // reset only after SUCCESS
}
```

The trigger observes `last_coordinates` changes:

```rust
fn on_new_solved_coordinates(solved: Coordinates) {
    if let Some(target) = coordinates {
        last_distance_arcmin = angular_separation(solved, target).arcminutes();
    }
}
```

Angular separation (also used by the flip trigger's 20′ check) — standard spherical law of
cosines; coordinates first brought to a common epoch:

```
distance = acos( sin(dec_a)·sin(dec_b) + cos(dec_a)·cos(dec_b)·cos(ra_a − ra_b) )
```

### 5.3 Trigger + recenter action

```rust
fn should_trigger(next) -> bool {
    if !is_next_light_exposure(next) { return false; }
    if last_distance_arcmin >= distance_arc_minutes {
        if is_too_close_to_meridian_flip(parent, Duration::minutes(1) + next_duration) {
            return false;
        }
        return true;    // + info notification "Centering after drift"
    }
    false
}

async fn execute() {
    // Build and run a standard `Center` instruction on the stored target coordinates:
    // 1) stop guiding FIRST (remember whether it was actually guiding);
    // 2) refuse if mount parked (throws -> hard fail; note guiding is already stopped by then
    //    and is NOT restarted on this path);
    // 3) slew to target; 4) one-shot dome sync if dome connected, can set azimuth, not following;
    // 5) run the centering loop (below);
    // 6) restart guiding if step 1 stopped it — this happens BEFORE the success check, so
    //    guiding resumes even when centering failed;
    // 7) if centering failed -> throw -> instruction FAILED (trigger marked FAILED; sequence
    //    continues).
    center(coordinates).await;
    last_distance_arcmin = 0.0;
    follower.last_coordinates = None;
}
```

**Centering loop** (`CenteringSolver::center`) — shared by recenter-after-flip and center-after-drift:

```rust
async fn center(seq: CaptureSpec, p: CenterSolveParameter) -> PlateSolveResult {
    assert!(p.coordinates.is_some());
    assert!(p.threshold > 0.0);           // arcminutes
    // optional temporary filter change for the solve exposures (restored in `finally`,
    // with a 5-minute failsafe timeout on the restore)
    let mut offset = Separation::ZERO;    // RA/Dec offset model for mounts that refuse Sync
    for attempt in 0..10 {                // maxSlewAttempts = 10
        let result = capture_and_solve(seq, p).await;      // 5.4 (has its own retry loop)
        if !result.success { return result; }              // solve failed after retries: give up

        // Work in the mount's epoch:
        let scope = mount.current_position();
        let solved = result.coordinates.transform(scope.epoch);
        let target = p.coordinates.transform(scope.epoch);
        let separation = target - solved;                  // Separation{ra,dec,distance,bearing}

        if separation.distance.arcminutes().abs() <= p.threshold {
            return result;                                 // centered
        }

        // Try to sync the mount to the solved position:
        if p.no_sync || !mount.sync(solved).await {
            offset = scope - solved;                       // sync unavailable → offset model
        } else {
            let after = mount.current_position();
            if (after - scope).distance.arcseconds().abs() < 1.0 {
                offset = after - solved;                   // silent sync failure → offset model
            } else {
                offset = Separation::ZERO;                 // sync worked
            }
        }
        mount.slew(target + offset).await;                 // re-slew (coords + Separation adds
                                                           // RA mod 360 and Dec component-wise)
        one_shot_dome_sync_if_needed().await;
    }
    result.success = false;                                // 10 attempts exhausted
    result
}
```

### 5.4 Capture-and-solve + blind failover

`CaptureSolver::solve` (per centering iteration): loop up to `attempts` (profile
`number_of_attempts`, default **10** — note the recenter path passes this same value) —
capture snapshot (no star detection), restore filter in parallel, run `ImageSolver::solve`; on
solve failure wait `reattempt_delay` (default **2 min**) and retry. Two capture quirks: if the
capture returns no image the attempt counts as failed but the retry is **immediate** (the delay
sits in the solve branch only), and the "restore filter" task is awaited before the retry
decision.

`ImageSolver::solve`: run the primary solver with hint coordinates; if it fails and
`blind_failover_enabled` (default **true**) and a hint was present, re-run with `coordinates =
None` → blind solver. Prerequisite check: focal length must be a positive finite number (else
error).

Plate-solve profile defaults (used by all paths above): solver ASTAP (blind: ASTAP), `search_radius = 30`°,
`exposure_time = 2 s`, `threshold = 1.0` arcmin, `rotation_tolerance = 1.0`°, `reattempt_delay = 2` min,
`number_of_attempts = 10`, `filter = None`, `downsample_factor = 0`, `max_objects = 500`,
`gain = -1` (camera default), `binning = 1`, `sync = false` (i.e. `no_sync = telescope_settings.no_sync`,
default false), `regions = 5000` (Platesolve3), `blind_failover_enabled = true`.

---

## 6. SynchronizeDomeTrigger (for completeness)

Fires before any item when: telescope not parked, dome-following disabled, and the dome azimuth is
outside the follower's tolerance of the azimuth computed from the current mount pointing
(`get_synchronized_dome_coordinates(telescope_info)`); executes `dome.slew_to_azimuth(target_az)`.
All exceptions during evaluation → log + return false. Validation: dome connected + can set
azimuth + telescope connected + following disabled.

---

## 6b. UnknownSequenceTrigger (deserialization placeholder)

When a saved sequence references a trigger type that is not installed (e.g. a plugin trigger),
NINA deserializes it as `UnknownSequenceTrigger` carrying the original type token as its name.
Behavior: `should_trigger` always returns `false`; `validate()` returns `false` with a fixed
"unknown instruction" issue; `execute()` throws a skip exception (it can never be reached via
`should_trigger`, only by a manual run). The sequence still loads and runs — unknown triggers are
inert, not fatal. An AstroDeck importer of NINA sequence JSON needs the same tolerance: preserve
the unknown trigger (round-trip its JSON) but never fire it, and surface a validation warning.

---

## 7. Edge cases & failure paths (consolidated checklist)

Meridian flip:
- Telescope disconnected / `time_to_meridian_flip` NaN → no trigger, flip times cleared, error log.
- Parked / at-home / not tracking → no trigger (info log).
- Pier side `Unknown` with `use_side_of_pier` → falls back to the no-pier-side logic (2-min buffer).
- Flip already done (pier side matches projection) → suppressed.
- Flip missed (mismatch while time says "already flipped") → "delayed flip" for a window of
  `[11h − max_minutes − pause, 12h]` of remaining time, else suppressed.
- Repeat-flip suppression without pier side: 11 h + 20 arcmin proximity to last flip target.
- Trigger `execute` with no target context, or target RA=Dec=0 → uses current mount position (warn).
- `time_to_flip > 2 h` at execute time → treated as overdue → flip immediately.
- Workflow exception → notification, resume guider (best-effort), re-enable tracking, result false.
- Workflow cancellation → result **true** (NINA quirk; see Recommendations).
- Individual step returning `false` does not abort the workflow (only exceptions do).
- Recenter step failure → error notification, workflow continues (best-effort).
- Dome sync failures anywhere → warn + continue.
- Mount flip slew: 20 retries × 60 s wait; counterweight-up avoidance defers `SideOfPier` writes
  until after the first slew when the mount is still CW-down; drivers without `CanSetPierSide`
  skip pier-side writes entirely; total failure → notification + false.

Autofocus triggers:
- All: next item not a LIGHT exposure → false; flip proximity (1.5× duration) vetoes a firing.
- AfterExposures: `after_exposures == 0` → unguarded modulo-by-zero exception, swallowed by the
  trigger-runner catch (error logged per item, trigger never fires). Port as never-fire, no panic.
- HFR: empty candidate history → false; <3 points in window → false; NaN/≤0 HFR excluded;
  filter-wheel-disconnected → no filter filtering.
- Temperature: empty image history → false; focuser temp NaN → false; baseline falls back from
  last-AF temp to sequence-start temp to first-evaluation temp.
- Filter change: first evaluation only establishes baseline; dedicated AF filter changes the
  baseline source; veto consumes the change (no retrigger).
- Time: baseline set once at first block init, replaced by last AF time thereafter.

Dither:
- `after_exposures == 0` → disabled (no validation demands, never fires).
- Image-history cleared (count shrank) → internal counter reset to 0.
- PHD2 not guiding / lost lock → dither skipped with warning, returns false (sequence continues).
- Settle-done never received → proceed after `settle_timeout + 10 s`.

Center after drift:
- Solve failures in the follower → warning, `progress_exposures` NOT reset (next light retries).
- A solve still running when the next frame arrives → frame skipped for solving.
- No target coordinates inherited → validation issue; separation never computed.
- Distance check is `>=` threshold; after a recenter both the distance and the follower's last
  coordinates are cleared (no immediate refire).
- Centering: solve failure → abort with failure; sync refused/silent-failure → RA/Dec offset
  compensation model; 10 slew iterations max.

---

## 8. Configuration knobs (defaults & ranges)

| Area | Knob | Default | Valid range (as enforced in code) |
|---|---|---|---|
| Flip | minutes_after_meridian | 5 min | coupled: ≤ max_minutes_after_meridian |
| Flip | max_minutes_after_meridian | 10 min | coupled: ≥ minutes_after_meridian |
| Flip | pause_time_before_meridian | 0 min | any; ≠0 switches to pause mode |
| Flip | use_side_of_pier | true | bool |
| Flip | recenter | true | bool |
| Flip | settle_time | 30 s | int |
| Flip | auto_focus_after_flip | false | bool |
| Flip | rotate_image_after_flip | false | bool |
| Flip (mount) | slew retry attempts / wait | 20 / 60 s | const |
| Mount | telescope settle after slew | 5 s | int |
| AF | AutofocusAfterExposures.after_exposures | 5 | int; 0 → unguarded div-by-zero (see §3.1/§7) |
| AF | AutofocusAfterTime.amount | 30 min | f64 |
| AF | AutofocusAfterTemperature.amount | 5 °C | f64 |
| AF | AutofocusAfterHFRIncrease.amount | 5 % | f64 |
| AF | AutofocusAfterHFRIncrease.sample_size | 10 | int ≥ 3 (setter rejects less) |
| Dither | after_exposures | 1 | int; 0 disables |
| Guider | dither_pixels | 5.0 px | f64 |
| Guider | dither_ra_only | false | bool |
| Guider | settle_pixels | 1.5 px | f64 |
| Guider | settle_time | 10 s | int |
| Guider | settle_timeout | 40 s | int (failsafe = +10 s) |
| Guider | phd2_roi_pct | 100 % | <100 activates centered ROI for find_star |
| Drift | distance_arc_minutes | 10′ | f64 > 0 |
| Drift | after_exposures | 1 | int |
| Solve | threshold (centering tolerance) | 1.0′ | f64 > 0 (centering asserts) |
| Solve | number_of_attempts | 10 | int |
| Solve | reattempt_delay | 2 min | f64 |
| Solve | exposure_time | 2 s | f64 |
| Solve | search_radius | 30° | f64 |
| Solve | binning / gain / downsample / max_objects | 1 / −1 / 0 / 500 | — |
| Solve | blind_failover_enabled | true | bool |
| Centering | max slew attempts | 10 | const |
| Centering | silent-sync detection | 1.0″ | const |
| Flip trigger | repeat-flip guard | 11 h & 20′ | const |
| Flip trigger | delayed-flip immediate threshold | 2 h | const |
| Flip trigger | no-pier-side buffer | 2 min | const |
| IsTooClose | duration safety factor | 1.5× | const |

Most numeric profile settings have no range validation in the model layer (only in WPF UI); ranges
marked "—" are unconstrained in code.

---

## 9. Source map (MPL-2.0 provenance; NINA commit `7c9de0c4`)

| Algorithm / fact | File | Lines |
|---|---|---|
| Sequential execution loop, trigger walk, lifecycle hooks | `NINA.Sequencer/Container/ExecutionStrategy/SequentialStrategy.cs` | 37–211 |
| Parallel strategy | `NINA.Sequencer/Container/ExecutionStrategy/ParallelStrategy.cs` | 33–50 |
| RunTriggers/RunTriggersAfter (exception swallowing, context choice) | `NINA.Sequencer/Container/SequenceContainer.cs` | 509–543 |
| Conditions check (CheckConditions) | `NINA.Sequencer/Container/SequenceContainer.cs` | 252–271 |
| Trigger base `Run` (validate → execute → runner-failure check) | `NINA.Sequencer/Trigger/SequenceTrigger.cs` | 107–147, 158–181 |
| One-time Initialize/Teardown of tree | `NINA.Sequencer/Sequencer.cs` | 76–174 |
| Context coordinates lookup; root lookup | `NINA.Sequencer/Utility/ItemUtility.cs` | 32–61 |
| GetMeridianFlipTime / IsTooCloseToMeridianFlip (1.5×) | `NINA.Sequencer/Utility/ItemUtility.cs` | 68–105 |
| time_to_meridian / expected_pier_side / time_to_meridian_flip | `NINA.Astrometry/MeridianFlip.cs` | 24–98 |
| EuclidianModulus | `NINA.Astrometry/AstroUtil.cs` | 103–121 |
| arcsec-per-pixel conversion | `NINA.Astrometry/AstroUtil.cs` | 39, 727–731 |
| Separation (spherical distance, +/− operators) | `NINA.Astrometry/Coordinates.cs` | 540–596 |
| MeridianFlipTrigger: settings passthrough, ShouldTrigger, window publish, Execute, validate | `NINA.Sequencer/Trigger/MeridianFlip/MeridianFlipTrigger.cs` | 93–341 |
| MeridianFlipSettings defaults + min/max coupling | `NINA.Profile/MeridianFlipSettings.cs` | 30–84 |
| Flip workflow steps, PassMeridian, Settle, Recenter, error/cancel paths | `NINA.WPF.Base/ViewModel/MeridianFlipVM.cs` | 120–394 |
| AutomatedWorkflow.Process (failures don't abort) | `NINA.WPF.Base/ViewModel/MeridianFlipVM.cs` | 441–453, 507–511 |
| Mount flip slew, CW-up avoidance, retries; SetPierSide | `NINA.Equipment/Equipment/MyTelescope/AscomTelescope.cs` | 40–41, 297–412 |
| HoursToMeridian / TimeToMeridianFlip mount property (24 fallback) | `NINA.Equipment/Equipment/MyTelescope/AscomTelescope.cs` | 645–676 |
| Mediator MeridianFlip wrapper; slew + settle + dome sync + 10-min timeout | `NINA.WPF.Base/ViewModel/Equipment/Telescope/TelescopeVM.cs` | 900–990 |
| AutofocusAfterExposures | `NINA.Sequencer/Trigger/Autofocus/AutofocusAfterExposures.cs` | 59–132 |
| AutofocusAfterTimeTrigger | `NINA.Sequencer/Trigger/Autofocus/AutofocusAfterTimeTrigger.cs` | 59–145 |
| AutofocusAfterTemperatureChangeTrigger | `NINA.Sequencer/Trigger/Autofocus/AutofocusAfterTemperatureChangeTrigger.cs` | 59–155 |
| AutofocusAfterFilterChange | `NINA.Sequencer/Trigger/Autofocus/AutofocusAfterFilterChange.cs` | 59–140 |
| AutofocusAfterHFRIncreaseTrigger (OLS trend) | `NINA.Sequencer/Trigger/Autofocus/AutofocusAfterHFRIncreaseTrigger.cs` | 61–240 |
| RunAutofocus duration estimate | `NINA.Sequencer/SequenceItem/Autofocus/RunAutofocus.cs` | 120–144 |
| DitherAfterExposures | `NINA.Sequencer/Trigger/Guider/DitherAfterExposures.cs` | 52–124 |
| Dither instruction (duration = settle_timeout) | `NINA.Sequencer/SequenceItem/Guider/Dither.cs` | 66–90 |
| PHD2 dither / settle failsafe / auto-select star / ROI | `NINA.Equipment/Equipment/MyGuider/PHD2/PHD2Guider.cs` | 295–438 |
| Guider settings defaults | `NINA.Profile/GuiderSettings.cs` | 33–61 |
| RestoreGuiding | `NINA.Sequencer/Trigger/Guider/RestoreGuiding.cs` | 46–97 |
| CenterAfterDriftTrigger | `NINA.Sequencer/Trigger/Platesolving/CenterAfterDriftTrigger.cs` | 69–283 |
| PlatesolvingImageFollower | `NINA.Sequencer/Trigger/Platesolving/PlatesolvingImageFollower.cs` | 47–157 |
| Center instruction (stop/restart guiding, park check) | `NINA.Sequencer/SequenceItem/Platesolving/Center.cs` | 127–204 |
| CenteringSolver (offset model, 10 attempts, 1″ silent-sync) | `NINA.Platesolving/CenteringSolver.cs` | 58–173 |
| CaptureSolver retry loop | `NINA.Platesolving/CaptureSolver.cs` | 47–93 |
| ImageSolver blind failover + focal-length prerequisite | `NINA.Platesolving/ImageSolver.cs` | 37–90 |
| PlateSolveSettings defaults | `NINA.Profile/PlateSolveSettings.cs` | 36–79 |
| TelescopeSettings defaults (settle 5 s, no_sync false) | `NINA.Profile/TelescopeSettings.cs` | 31–43 |
| SynchronizeDomeTrigger | `NINA.Sequencer/Trigger/Dome/SynchronizeDomeTrigger.cs` | 112–168 |
| Image history ids / AF points / clear semantics | `NINA/ViewModel/ImageHistory/ImageHistoryVM.cs` | 236–315 |
| ImageHistoryPoint fields (HFR, temp, AF point) | `NINA.WPF.Base/Model/ImageHistoryPoint.cs` | 61–140 |
| History appended only for LIGHT/SNAPSHOT | `NINA.Sequencer/SequenceItem/Imaging/TakeExposure.cs` | 192–194, 243–245, 290–292 |

The HFR regression uses Accord.NET `OrdinaryLeastSquares`/`SimpleLinearRegression` (LGPL library,
standard closed-form simple OLS as written out in §3.5 — no need to port Accord).

---

## 10. Recommended for AstroDeck

Adopt NINA behavior as the default, with these deliberate deviations:

1. **Flip trigger**: implement §2.1–§2.4 verbatim, defaults as in §2.2
   (`use_side_of_pier = true` when the Alpaca driver reports `CanSetPierSide`/valid `SideOfPier`,
   auto-fallback to the pier-side-less path otherwise — NINA already degrades gracefully on
   `Unknown`). Keep `pause_time_before_meridian = 0` default; it exists only for
   obstruction-limited rigs.
2. **Fix the cancellation quirk**: NINA returns *success* when the flip workflow is cancelled
   (§2.5). AstroDeck should return a distinct `Cancelled` outcome and mark the sequence step
   accordingly — do not report a flip that didn't finish as done.
3. **Fix the step-failure quirk**: NINA's workflow continues after a step returns `false` (e.g. the
   flip slew itself failing → it still autofocuses/recenters and reports success). AstroDeck should
   abort the workflow (with guider-resume + tracking-re-enable recovery, as NINA's exception path
   already does) when `Flip` fails; keep best-effort semantics for `Recenter`, dome sync, and
   guider steps (matching NINA's intent).
4. **Recenter after flip: on** (NINA default) with threshold 1′, attempts 10, blind failover on.
   The offset-compensation fallback in the centering loop (§5.3) is essential for mounts that
   refuse `Sync` — implement it exactly, including the 1″ silent-sync detection.
5. **AF triggers**: ship all five with NINA defaults (5 exposures / 30 min / 5 °C / filter change /
   5 % HFR with window 10). The HFR trigger's OLS trend (not raw last-HFR) is the right default —
   it is robust to single bad frames. Consider (as an AstroDeck extension, off by default)
   retriggering a veto-suppressed filter-change AF after the flip completes; NINA loses it (§3.4).
6. **Dither**: default `after_exposures = 1`; expose PHD2 settle params with NINA defaults
   (5 px dither, 1.5 px / 10 s settle, 40 s timeout, +10 s failsafe). Keep the "history cleared"
   counter reset. If AstroDeck keeps per-target image counters instead of a global one, dither
   cadence should use the global count for parity.
7. **CenterAfterDrift**: worth shipping (ASIAIR has nothing comparable); default off in templates,
   10′ threshold / solve every light frame. Run the solver on the saved LIGHT frame in the
   background exactly as §5.2 (never block the imaging train; skip when a solve is in flight;
   only reset the exposure counter on success).
8. **IsTooCloseToMeridianFlip** with the 1.5× factor must be shared by every long-running trigger —
   this interlock is the core of NINA's "never straddle the flip" reputation.
9. Keep trigger-evaluation semantics: bottom-up container walk, sequential order, exceptions
   logged-not-fatal, and the LIGHT-exposure gate — third-party sequence JSON imported from NINA
   will then behave identically.
10. Meridian flip mount fallback for drivers without `SideOfPier` write support: NINA's
    slew-retry loop (20 × 60 s) doubles as a "wait until the mount decides to flip" mechanism for
    GEMs that only flip once the target is past the meridian — keep it, but make attempts/wait
    configurable (20 × 60 s defaults).

---

## 11. Gaps / things the source does not make clear

- `use_side_of_pier` default: the code sets `true` while the adjacent comment claims the default is
  `false` "to preserve prior behavior" — the comment is stale; verified against
  `MeridianFlipSettings.SetDefaultValues()`.
- `TimeToMeridianFlip` when tracking is disabled returns 24 h at the driver layer, but the trigger
  separately refuses to evaluate when not tracking, so the 24 h value is only visible in the UI.
- The temperature trigger reads the *focuser* temperature; NINA falls back to weather-data
  temperature only for the image-history display, not for the trigger. Rigs without a focuser
  temperature sensor never fire it (returns false on NaN) — not documented in NINA UI.
- Whether `RunTriggers` can fire the same trigger twice for the same upcoming item when both a
  child and parent container hold an equally-named trigger: the dedup is only at drop/insert time
  per container, so duplicates across nesting levels are possible and both will run (order:
  innermost first). Intent unclear; parity implementation should replicate.
- `MeridianFlipTrigger` in pause mode publishes `earliest == latest == meridian − pause`, which
  makes `IsTooCloseToMeridianFlip` veto anything that would cross the pause point — but the
  un-clamped negative `min_remaining` means after the pause instant passes, `latest_flip_time`
  moves into the past and the veto disarms; the exact interplay with very long next-instructions
  is untested in the source (no unit test covers pause + veto).
- The Accord OLS: behavior when all x are identical (`sxx == 0`) is division-by-zero → NaN trend →
  comparison false; cannot happen here since x = 1..n with n ≥ 3, noted only for completeness.
- The dither cadence counts SNAPSHOT frames (any entry in image history), which means plate-solve
  snapshots taken via `TakeExposure`-type items advance the dither counter. Solve exposures taken
  through the internal plate-solve capture path do NOT enter image history. Whether the SNAPSHOT
  inclusion is intended is unclear; parity implementation replicates it.
- UI-layer range validation for profile settings (e.g. negative settle times) is not present in the
  model code; Rust implementation should define sane clamps.
