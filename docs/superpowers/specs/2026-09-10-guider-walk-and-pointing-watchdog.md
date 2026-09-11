# The 2026-09-10 guider walk: what happened, and a pointing watchdog

Status: analysis complete, design proposed, nothing implemented.
Night: 2026-09-09/10. Target: NGC 7331 + SN 2026aaiv. Rig: astrotown, 0.3.26
capturing, 0.3.28 running the sequence.

## 1. Summary

Between 00:45 and 01:41 the mount walked **3.19 degrees off target**, 3.16 of
it in RA, while the sequence kept exposing and the guider kept reporting that
it was guiding. Twenty-three subs (0211 through 0223) were taken during the
walk. The run was recovered by hand at 01:42 after the operator noticed the
frames were trailing.

Two separate defects are involved, and only one of them is fully diagnosable
from the telemetry that exists:

- **Defect A, PROVEN but DEMOTED (corrected 2026-09-10 14:2x).** A 3.0 px
  dither cannot be delivered in one move on this mount: it needs 1294 ms in RA
  and 2549 ms in Dec against a hard 1000 ms per-move cap, and every clamped
  pulse in the night log is a dither pulse. **The first version of this
  document then claimed the cap made every settle fail. That was wrong** - see
  section 3.1a. The cap costs 2 to 3 guide cycles instead of 1, about 5 s in
  Dec against a 90 s settle window, and 31 of 45 dithers settled fine with the
  cap in force. It is an inefficiency, not the thing that broke the night.
- **Defect B, PARTIALLY diagnosable.** The walk itself: a continuous
  one-directional RA drag at 117 arcsec/min, 15.3 percent duty cycle at the
  calibrated guide rate. Its magnitude, rate, direction and start time are
  established. Its mechanism is **not**, because the three telemetry streams
  that would identify it are not recorded (section 4.2).

The more important finding is the third one:

- **Every guard that should have caught this missed it, and one of them
  actually computed the answer and threw it away.** At 01:04:56 the system
  logged that the mount had moved 0.85 degrees since the last plate solve, and
  used that fact only to invalidate a display cache. The run continued for
  another 38 minutes.

## 2. The failure, measured

### 2.1 Timeline

All times PDT. Sources: `captures/logs/2026-09-09.jsonl` (963 entries) and the
subs' own FITS headers.

| time | event |
|---|---|
| 22:52:36 | run starts (105 frames, 195 min), after a by-hand restart at 22:50 |
| 22:29 - 00:07 | 11 clamped pulses, mixed directions. Run healthy, frames accepted |
| 00:26:52 | meridian flip begins: guiding stopped, re-slew |
| 00:28:21 | persisted calibration and PPEC model cleared; "guiding will recalibrate on the new side" |
| 00:28:21 | centring attempt 2: 0.3' off target (good) |
| 00:28:27 | calibration walk starts |
| 00:35:08 | calibration completes (**6 min 41 s**), advisory raised, saved, guiding |
| 00:35:08 | flip complete, pier side west to east. Post-flip autofocus correctly skipped |
| 00:36:40 | first dither settle timeout |
| 00:40:50 | a dither settles (the last one that ever does) |
| 00:45:47 | frame 0210: mount claims -0.33' from target. **Last clean frame** |
| 00:46:40 | clamped pulse east 1257 ms |
| 00:47:45 | dither settle fails |
| 00:48:50 | frame 0211: **-7.34'**. The walk is underway |
| 00:50:13 | guide star lost (reacquire 1/8) |
| 00:50:20 | **re-lock 1: 448.5 arcsec from the last lock** |
| 01:04:56 | **"field identification cleared: the mount has moved 0.85 deg since the last plate solve"** - computed, logged, discarded |
| 01:12:48 | **re-lock 2: 881.7 arcsec from the last lock** |
| 01:21:52 | frame 0219: -90.57' |
| 01:24-01:26 | a refocus runs (temperature trigger), unaware anything is wrong |
| 01:40:56 | frame 0223: **-107.77'**. Last frame of the walk |
| 01:41:58 | operator intervention: guiding stopped |
| 01:43:15 | **plate solve: "centering attempt 1: 190.6' off target"** |
| 01:44:09 | re-centred to 0.4' after home + solve |
| 01:45:24 | fresh calibration walk starts |
| 01:51:59 | completes (6 min 35 s), same advisory |
| 01:54:13 | "dithered 3.0px and settled" |

### 2.2 The walk, from the subs' own headers

`MOUNTRAD`/`MOUNTDCD` are the mount's live claim, written per frame. Offsets
below are from the run's target (the SN's position), in arcmin, on-sky
(RA scaled by cos dec).

| frame | shot | filter | total off | dRA | dDec |
|---|---|---|---|---|---|
| 0207 | 00:39:44 | S | 2.10 | -0.13 | 2.09 |
| 0208 | 00:41:57 | L | 2.63 | -0.13 | 2.63 |
| 0209 | 00:43:50 | R | 3.05 | -0.13 | 3.04 |
| 0210 | 00:45:47 | G | 3.49 | -0.33 | 3.48 |
| 0211 | 00:48:50 | B | 8.59 | **-7.34** | 4.46 |
| 0212 | 00:54:51 | Ha | 22.21 | -21.98 | 3.23 |
| 0213 | 00:59:54 | Oiii | 36.86 | -35.11 | 11.23 |
| 0214 | 01:04:56 | S | 51.17 | -47.59 | 18.81 |
| 0215 | 01:08:00 | L | 60.02 | -55.14 | 23.69 |
| 0216 | 01:10:57 | R | 66.83 | -61.05 | 27.18 |
| 0217 | 01:13:51 | G | 71.01 | -66.62 | 24.56 |
| 0218 | 01:16:51 | B | 77.15 | -74.49 | 20.06 |
| 0219 | 01:21:52 | Ha | 92.15 | -90.57 | 16.96 |
| 0220 | 01:29:39 | Oiii | 74.41 | -73.52 | -11.52 |
| 0221 | 01:34:37 | S | 91.98 | -91.29 | -11.24 |
| 0222 | 01:37:50 | L | 100.51 | -99.96 | -10.54 |
| 0223 | 01:40:56 | R | 108.14 | **-107.77** | -8.82 |

Derived: **107.44 arcmin of RA in 55.2 min = 1.95 arcmin/min = 116.9
arcsec/min = 1.95 arcsec/s.**

Three things to note about this table.

**It is monotonic in RA.** The walk is a continuous drag, not a series of
jumps. That distinction rules out the reference-jump mechanisms: a fault that
moves the reference produces steps, and the re-locks at 00:50:20 and 01:12:48
sit inside a ramp that was already running before the first of them and
continued unchanged through both. The re-locks are consequences.

**The mount under-reported by 43 percent.** The mount's own claim at 01:40:56
was 108'. The plate solve two minutes later measured 190.6'. So the mount's
report is directionally useful and quantitatively wrong, which matches the
known AM5 behaviour recorded in `astrodeck-mount-coords-lie`. It cannot be the
sole basis for a watchdog.

**The re-lock magnitudes track the walk.** Re-lock 1 was 448.5 arcsec = 7.48'
at 00:50:20; frame 0211 at 00:48:50 was 7.34' off. The guider was grabbing a
different star because the field had slid, and each re-lock reset its error to
zero around the new star. That is exactly the GN-03 failure mode from
2026-09-06, and section 3.3 explains why the GN-03 guard did not fire.

### 2.3 Calibration telemetry

The good calibration (01:51:59), read from
`config/guider/<profile>.json`. This is the complete persisted record:

| field | value | derived |
|---|---|---|
| `x_rate` (RA) | 0.0023176934558 px/ms | 2.318 px/s = 12.75 arcsec/s = 0.848x sidereal |
| `y_rate` (Dec) | 0.0011769677086 px/ms | 1.177 px/s = 6.47 arcsec/s = 0.430x sidereal |
| `x_angle` | -0.08272880760 rad | -4.74 deg |
| `y_angle` | 1.31142192312 rad | 75.14 deg |
| `y_angle_error` | 0.17664559607 rad | 10.12 deg (the reported `ortho_error_deg`) |
| `declination` | 0.60043689592 rad | 34.40 deg |
| `pier_side` | east | correct for post-flip |
| `ra_parity` | **unknown** | never determined |
| `dec_parity` | **unknown** | never determined |
| `image_scale_arcsec` | 5.5004 | from the 150 mm guide scope |
| `is_valid` | true | |

Correcting the RA rate for cos(dec) gives **15.45 arcsec/s = 1.027x
sidereal**, so the mount's RA guide rate is 1.0x sidereal. The Dec rate is
0.43x. **The two axes differ by a factor of 2.4**, which is what the standing
advisory ("RA and Dec rates vary by an unexpected amount, often caused by
large Dec backlash") has been reporting on every calibration on this mount for
weeks. The advisory is chronic and true, and therefore useless as a signal.

**`ortho_error_deg` is not a health signal.** The BROKEN calibration reported
4.85 deg. The GOOD one reports 10.12 deg, twice as bad, and guides at 2.3
arcsec total. Any health check built on orthogonality would gate on exactly
the wrong number. This needs saying explicitly because it is the first field
anyone reaches for.

### 2.4 Guiding after recovery, for contrast

Fresh calibration, fresh lock, same mount, same pier, 01:52 onward:

```
rms_ra 1.23   rms_dec 1.95   rms_total 2.3 arcsec   snr 234.7
worst |RA| over 24 consecutive samples: 2.05 arcsec
```

During the failure, the same reading was **rms_ra 4515.68 arcsec** with
`rms_dec 8.47` and `snr 391.8`: a 1.25 degree RA error, a healthy Dec, and a
bright confident lock, all at once.

## 3. Root cause

### 3.1 Defect A: the dither cannot be executed on this mount (PROVEN)

`dither_pixels` defaults to 3.0 (config.py:486). The AM5 driver caps any one
guide move at 1000 ms, because a single move may not exceed about 15 arcsec.
Using the measured calibration rates:

| axis | 3.0 px requires | cap | verdict | undelivered |
|---|---|---|---|---|
| RA | **1294 ms** | 1000 ms | over by 294 ms (1.29x) | 0.68 px = 3.75 arcsec |
| Dec | **2549 ms** | 1000 ms | over by 1549 ms (**2.55x**) | 1.82 px = 10.03 arcsec |

Now compare against every clamped pulse actually logged that night:

| axis | n | min | max | mean | predicted |
|---|---|---|---|---|---|
| east/west (RA) | 18 | 1004 | 1492 | **1254 ms** | **1294 ms** |
| north/south (Dec) | 7 | 1316 | 2678 | **1986 ms** | **2549 ms** |

The predicted dither pulse and the observed mean clamped pulse agree on both
axes. **Every clamped pulse in the log is a dither pulse**, and the spread
around the prediction is the guide error the dither was added to.

What follows from it, and what does not:

- A dither takes **more than one guide cycle**: 1.3 cycles in RA, 2.5 in Dec.
  The lock position moves by the full 3.0 px immediately (`engine.rs:1571`),
  and the loop then walks the mount onto it over successive frames, so nothing
  is permanently undelivered.
- The **Dec axis needs twice the cycles RA does**, because the Dec guide rate
  is 0.43x sidereal against RA's 1.03x. A circular dither request is therefore
  delivered as an ellipse in *time*, not in distance - Dec dithers simply
  settle slower. On a mount already flagged for large Dec backlash that is
  worth knowing.
- This is independent of the flip and of Defect B, and was happening from
  22:29.

Worth fixing for cleanliness (log the feasibility check once at calibration
time, with the numbers, so nobody has to re-derive this) but it is **not** a
night-breaker, and the original version of this document was wrong to present
it as one.

### 3.1a Correction: the cap does NOT cause the settle failures

The first version of this analysis reasoned from the arithmetic match in 3.1
straight to "so every settle blows its deadline". The timing budget refutes
that, and so does the log.

**The timing budget.** 2.5 guide cycles at a 2.0 s guide exposure is about 5
seconds. `_SETTLE_TIMEOUT_S` is **90 seconds**. There is an order of magnitude
of headroom; the cap cannot exhaust it.

**The log, which separates perfectly.** Every dither all night requested a
pulse over the cap, so if the cap caused failure, all of them would fail:

| window | dithers | settled | failed |
|---|---|---|---|
| 22:30 - 00:08 (pre-flip) | 11 | **11** | 0 |
| 00:40:50 (just after the flip) | 1 | 1 | 0 |
| **00:36:40 - 01:39:49 (the walk)** | **14** | **0** | **14** |
| 01:54 - 04:46 (after recovery) | 20 | **20** | 0 |

Thirty-one dithers settled with the cap in force, including one at 01:54:00
whose pulse was clamped from 1292 ms. All fourteen failures fall inside the
walk window and nowhere else.

**So the settle failures are a symptom of Defect B, not a consequence of
Defect A** - the field was moving under the guider, so the error never
converged to the settle criterion. Which makes them something more useful than
a nuisance: see Detector E.

### 3.2 Defect B: the walk (mechanism NOT established)

What is established: a continuous, monotonic, one-directional RA drag of 1.95
arcsec/s, starting between 00:45:47 and 00:48:50, sustained for 55 minutes,
reaching 3.19 degrees. 1.95 arcsec/s against a calibrated RA guide rate of
12.75 arcsec/s is a **15.3 percent duty cycle**: the guider was issuing
westward corrections about one cycle in seven, continuously, for an hour.

What is ruled out by the data:

- **Not a reference jump.** Monotonic ramp, not steps (section 2.2).
- **Not the re-locks.** The ramp precedes re-lock 1 and continues unchanged
  through both.
- **Not tracking loss.** A tracking failure drifts at up to 15 arcsec/s; this
  is 13 percent of that, and `tracking_rate` read `sidereal` throughout.
- **Not accumulated dither residual.** 0.68 px per dither in RA over roughly
  40 dithers is about 27 px = 150 arcsec. The walk was 1175 px = 6466 arcsec,
  a factor of 43 too large.
- **Not orthogonality.** The good calibration is twice as non-orthogonal.

What remains, and cannot be separated with the telemetry that exists:

1. **A bad RA rate or parity in the 00:35 calibration.** `ra_parity` is
   recorded as `unknown` even on the good calibration, so the sign was never
   determined and is inferred from the walk geometry each time. A rate that is
   wrong low makes every computed pulse too long; combined with the 1000 ms
   clamp the loop would push at the cap and never converge.
2. **A stale or mis-signed reference surviving into the post-flip session.**
   The flip explicitly discards the calibration (00:28:21) and re-walks it, and
   `start_guiding` clears `_lock_xy` (native.py:611), so the obvious version of
   this is refuted. A subtler variant inside the Rust engine's own reference is
   not.
3. **Multi-star correspondence.** `max_stars` means the offset is fitted over
   a constellation. A bad match yields a large constant offset while every
   individual star stays sharp and bright, which is precisely the observed
   signature of snr 391 at a 1.25 degree error.

**Why it cannot be narrowed further, exactly:**

- The calibration's rates and angles are **deliberately not surfaced**.
  `native.py:1912`: *"Rates (px/ms) and raw axis angles are intentionally
  omitted rather than mislabeled."* The report exposes only validity,
  orthogonality, dec-axis parity, declination, pier side and binning.
- The persisted calibration file is **overwritten by the next calibration with
  no history**. The 01:51 walk destroyed the 00:35 record. The one artifact
  that would answer question 1 above existed on disk for 76 minutes and is
  gone.
- **Only clamped pulses are logged.** The other roughly 85 percent of guide
  corrections leave no trace at all. A 15.3 percent duty cycle drag is
  invisible by construction, and the pulses that *are* logged turn out to be
  the dithers, so the log is systematically blind to the pulses that did the
  damage.
- The guide error series is a **rolling in-memory buffer of about 30 samples**
  (`stats()["recent"]`), never persisted. An hour of 1.25 degree readings left
  no record.

That is the honest position: Defect A is solved, Defect B is characterised but
not mechanised, and section 5 is written so that the *next* occurrence is
diagnosable in one pass.

### 3.2a Defect C: the dither is an UNBOUNDED random walk

Found while answering the question "is our dithering algorithm a randomised
walk". It is, and nothing bounds it.

`native.py:1777-1779` picks a uniformly random direction at a fixed step
length:

```python
ang = random.uniform(0.0, 2 * math.pi)
dx = pixels * math.cos(ang)
dy = pixels * math.sin(ang)
```

and `engine.rs:1571` adds that to the lock position rather than replacing it:

```rust
self.lock = Some((lock.0 + camera_delta.0, lock.1 + camera_delta.1));
```

So it is a 2D Pearson walk: fixed 3.0 px step, uniform direction, cumulative.
RMS displacement after n dithers is `step * sqrt(n)`:

| dithers | RMS guide px | RMS arcsec | arcmin | imaging px (0.968"/px) |
|---|---|---|---|---|
| 10 | 9.5 | 52 | 0.87 | 54 |
| 47 (last night) | 20.6 | 113 | 1.89 | 116 |
| 100 | 30.0 | 165 | 2.75 | 170 |
| 400 (a 4-night session) | 60.0 | 330 | 5.50 | 341 |
| 1000 | 94.9 | 522 | 8.70 | 539 |

**The frame-bounds guard that upstream uses for exactly this was delegated to
the host, and the host does not implement it.** `engine.rs:1549-1568` documents
PHD2's 4-sign validity search, which tries the four sign combinations of the
requested delta and keeps whichever leaves the lock at least `search_region+1`
inside the camera frame. Our engine cannot do it because the frozen
`dither(dx, dy)` signature never receives the frame size, and the comment
correctly assigns the duty upstairs: *"A host that must avoid pushing the star
off-frame sizes its dither amount conservatively before calling this (a
host-side, not engine-side, concern)."* The host is the eight lines above -
no bounds check, no frame size, no cumulative-offset tracking. (The `margin`
at `native.py:1661` is the Guiding Assistant's Dec-backlash walk, an unrelated
path.)

How much this actually matters, honestly:

- **Not a cause of anything last night.** 47 dithers gives an expected 1.9
  arcmin against an observed 191. Ruled out by two orders of magnitude.
- **Leaving the guide frame is not the practical risk.** Half a guide frame is
  several hundred px; that would take tens of thousands of dithers.
- **The practical cost is framing.** The walk is unbounded in the imaging
  field, and every plate-solve re-centre (flip, hold, some autofocus paths)
  zeroes it - so in practice it is bounded by how often the run happens to
  re-centre, which is not a design.
- Long multi-night sessions are where it bites: a few hundred arcsec of
  accumulated framing drift shrinks the common overlap the stack can use, and
  it does so silently.

There is no benefit to the unboundedness. Dithering exists to decorrelate the
sensor's fixed pattern from the sky between subs, and a dither bounded to a
disc around the run's original lock achieves that identically. Recommended fix:
track the cumulative offset host-side and reflect the step back toward the
origin when the next move would leave a configured radius (a few times the
dither size). That also restores, in the only place that can see it, the
frame-bounds intent the engine comment asks the host for.

### 3.3 Why every existing guard missed it

Three guards were in place. Each failed for a different and specific reason.

**Guard 1: `_maybe_recover_guiding` (engine.py:5194).** Fires when
`guider.is_active()` goes false. It never went false. The guider was guiding
the whole time, on a star, with snr 391. Working as designed and blind to
this.

**Guard 2: `_maybe_hold_for_relocks` (engine.py:5241), the GN-03 guard built
after the 2026-09-06 walk.** Gates on a RATE: `relock_limit` (3) re-locks
inside `relock_window_min` (10 minutes). The night produced **2 re-locks 22.5
minutes apart**. At no point were there 3 in any 10-minute window, so it never
fired while the field walked 1.8 degrees.

This is the important one, because the guard was built for the previous
failure's shape. 2026-09-06 walked 40 arcmin in half an hour in many small
re-locks; 2026-09-10 walked 3.19 degrees in two. **The guard counts events
when the quantity that matters is displacement** - and the code already tracks
the displacement: `relock_arcsec_total` is summed in `stats()`
(native.py:1897) and never used by any gate. Last night it reached 448.5 +
881.7 = **1330 arcsec = 22 arcmin** of accumulated re-lock displacement, which
is unambiguously a walking field. Adding a magnitude term to an
already-plumbed metric is close to free.

**Guard 3: `_current_field_solve` (hub.py:3067).** This one computed the
answer. It compares the mount's live report against the report recorded at
solve time:

```python
moved = angular_sep_deg(fs.mount_ra, fs.mount_dec, ra, dec)
if moved > self._field_stale_threshold_deg():
    self.invalidate_field_solve(
        f"the mount has moved {moved:.2f}deg since the last plate solve")
    return None
```

Threshold is `max(_FIELD_STALE_MIN_DEG 0.25, fov 1.123 * _FIELD_STALE_FOV_FRAC
0.5)` = **0.5615 deg**. It fired at **01:04:56 with moved = 0.85 deg**.

And its entire effect was to clear a cache so the UI would stop claiming to
know what field the rig was looking at. No alert, no hold, no re-centre. The
run continued for 38 more minutes and another 2.3 degrees.

The docstring even anticipates the reason it cannot be promoted as-is: *"a
mount that loses steps keeps reporting the old position, which is exactly the
AM5 failure this rig has already had"*. That is correct, and it is why the
design below does not trust the mount's report either - it uses it as a
*trigger* for an authoritative plate solve, never as the measurement.

## 4. Design: a pointing watchdog

### 4.1 Principles

1. **Never diagnose a pointing fault by asking the mount where it is.** Last
   night the mount was 43 percent wrong about its own error and, on other
   nights, has been confidently wrong by degrees.
2. **The authoritative answer is already on disk.** The run takes 105
   plate-solvable images of exactly where the scope is pointing, and solves
   none of them. ASTAP solved these frames in about 1 second each during the
   recovery. A verification solve on an already-captured sub costs no sky time
   at all: no slew, no filter change, no extra exposure.
3. **Detect on displacement, not on event counts.** Guard 2's failure is the
   lesson.
4. **Respond by re-establishing truth, in graded steps**, and never by
   trusting the thing that failed.
5. **Every detector must log the number it tested, not just its verdict.**
   Guard 3 knew the answer and the log line was thrown away because it was
   phrased as a cache event rather than a pointing measurement.

### 4.2 Detector A: pointing truth from the subs already taken (primary)

Every `verify_pointing_every` frames (proposed default 5, roughly 15 minutes
at these exposures), solve the sub that was just written and compare the
solution against the step's target.

- Costs no sky time. Runs in the existing worker thread alongside the quality
  grader, which already reads the frame.
- Uses the same ASTAP path and FOV hint the centring code uses, so no new
  dependency and no new failure mode.
- The result is the one number the whole failure lacked: **actual
  angular offset from target, measured, per N frames**.
- Thresholds, all in plan/flow policy so they are tunable per target:
  - under 0.5 x FOV: healthy, record and continue.
  - 0.5 to 1.0 x FOV: advisory, tighten the cadence to every frame.
  - over 1.0 x FOV: **the target has left the frame** - hold (response level
    2).
- Failure to solve is itself a signal. Last night frames 0217 and 0222 would
  not solve at all; two consecutive unsolvable subs on a field that solved
  earlier in the run is a hold condition, not a shrug.

At last night's numbers this fires at frame 0212 (00:54:51, 22.21 arcmin,
about 0.37 FOV as advisory) and unambiguously at 0213 (00:59:54, 36.86 arcmin,
0.61 FOV). **That is 42 minutes before the operator noticed**, and 15 subs
saved.

### 4.3 Detector B: accumulated re-lock displacement (nearly free)

Extend `_maybe_hold_for_relocks` with a magnitude term beside its existing
count term:

- hold if `relock_arcsec_total` within the window exceeds
  `relock_arcsec_limit` (proposed default 300 arcsec = 5 arcmin, comfortably
  inside one FOV and well above the 60-100 arcsec a genuine cloud-flicker
  re-lock costs), **regardless of the count**.
- keep the existing count gate unchanged; the two catch different shapes.
- also gate on a single re-lock larger than, say, 120 arcsec: one jump of that
  size is not a flickering star, it is a different star.

At last night's numbers this fires at **re-lock 1, 00:50:20, 448.5 arcsec** -
before the walk exceeded 8 arcmin. Cheapest useful detector of the three, and
it reuses a metric that is already computed, already summed and already on the
wire.

### 4.4 Detector C: correction duty cycle and cap saturation

Track per axis, over a rolling window (proposed 5 minutes):

- fraction of guide cycles that issued a correction in the same direction;
- count of pulses clamped by the driver cap;
- signed sum of commanded correction, in arcsec.

Raise when a single direction exceeds a duty-cycle threshold (proposed 40
percent sustained over 5 minutes) or when the signed sum exceeds one FOV. A
guider correcting one way seven cycles in ten is dragging the mount, not
guiding it; a healthy loop is roughly balanced.

At last night's numbers a 15.3 percent duty cycle would **not** trip a 40
percent threshold, so this detector is honest about its own limits: it is
there for the faster runaways (2026-09-06 style, and the 2026-09-06 pulse
overruns), while Detector A carries the slow walk. Set the threshold from
measured healthy nights before shipping it, not from this one incident.

The cap-saturation half is worth having on its own merit and independent of
any threshold: **two consecutive clamped pulses on the same axis** should log
once, loudly, with the computed and the delivered durations. That single line
would have exposed Defect A on the first night it happened.

### 4.5 Detector D: dither feasibility at calibration time

Not a watchdog, a precondition. When a calibration completes, compute the
pulse duration each axis needs for the configured `dither_pixels` and compare
against the driver's per-move cap. If it does not fit, say so once, with the
numbers, and take the configured remedy (split across cycles, or clamp the
dither). See section 3.1.

### 4.5a Detector E: consecutive dither settle failures (the best signal available)

This falls straight out of the 3.1a correction and it is the strongest
detector in this document, which is embarrassing given the first version of
the analysis treated these failures as noise caused by the cap.

A dither settle failure means *the guide error did not converge to the settle
criterion within 90 seconds*. On a stationary field it converges in about 5.
So a settle failure is close to a direct measurement of "the field is moving
and the loop is not winning".

The separation in the table in 3.1a is total: **0 failures in 31 healthy
dithers, 14 consecutive failures spanning exactly the walk.** No threshold
tuning, no calibration against other nights, and it needs no new telemetry -
the failures are already raised as exceptions and already logged
(`engine.py:2642`), where they are currently swallowed as a warning and the
frame is exposed anyway.

Proposed rule: **two consecutive dither settle failures is a hold** (response
level 2). At last night's numbers that fires at **00:47:45**, when the walk was
under 8 arcmin - three minutes before Detector B, and 55 minutes before the
operator noticed. One failure alone stays a warning: a single cloud crossing
can cost one settle.

This should be built first. It is the cheapest, the earliest, and the only one
with a clean separation already demonstrated on real data.

### 4.6 Where this lives

**In the sequence engine's per-frame loop, beside `_maybe_recover_guiding` and
`_maybe_hold_for_relocks` (engine.py:2621-2625), not in the flows graph.**

The user's instinct - a watchdog in the flows that re-slews to the target - is
right about the response and I would put the mechanism one layer lower, for
three reasons:

1. Every frame of every run passes through that loop, including runs that do
   not use flows and including resumed multi-night sessions. A flows-only
   watchdog protects only the runs that happen to be built that way.
2. The two sibling guards already live there, share the per-frame cadence, and
   have the hold/re-centre machinery this needs.
3. It has to keep working when the flow graph's own assumptions are what
   broke. Last night's log carries "10 graph feature(s) are not honoured by
   this run" - a watchdog inside a partially-honoured graph is a watchdog with
   unknown reach.

What **should** be exposed to flows and plans is the *policy*: cadence,
thresholds, and which response level is permitted without a human. That is
where per-target judgement belongs (a wide-field mosaic tolerates drift a
planetary-nebula close-up does not), and it keeps the mechanism in one place.

## 5. Response ladder

Graded, each level only escalating when the level below has failed. Levels 1
and 2 are unattended; 3 and 4 are the ones that need an explicit policy
decision from the operator.

**Level 1 - advisory.** Record the measurement in the session, log it as a
pointing number, tighten the verification cadence. Keep exposing. Frames stay
accepted; the grader is a separate judgement.

**Level 2 - hold, re-establish, resume.** This is exactly what
`_maybe_hold_for_relocks` already does and it should be reused verbatim: stop
guiding; discard the calibration so the restart measures a fresh one (the
GN-01 discard latch makes that stick); re-centre by plate solve; recalibrate;
resume. Add one thing it does not do: **verify the outcome**. Guide for a
bounded number of frames and require the error to converge before declaring
the hold resolved, because last night's fresh post-flip calibration was
accepted and was the thing that failed.

**Level 3 - home, then re-establish.** If level 2 has fired twice in a run, or
if the post-hold verification fails, the mount's pointing model has absorbed
the walk and a re-centre alone will not clear it. Home the mount, then
plate-solve centre. **This is the sequence that actually recovered the rig at
01:42-01:44** (home to alt 37.3 az 360, then goto with centring, landing at
0.44'), so it is proven on this hardware rather than proposed. It costs a few
minutes and it is the only thing that worked.

Level 3 must be gated on horizon and sun safety like any other slew, and it
must refuse while a polar alignment session is active.

**Level 4 - stop and shout.** Park, hold the session for resume, and alert.
Note that last night's log carried "no dead-man's-switch URL is set, so
nothing will notice if this machine stops" - a level 4 that no one hears is
level 3 with extra steps. The alert path needs configuring before this level
means anything.

An important asymmetry: levels 1 and 2 are cheap and reversible and should run
unattended. Level 3 moves the mount a long way and should be opt-in per plan.
Level 4 ends the night's data collection and should never be automatic without
the operator having chosen it.

## 6. Telemetry recommendations

Ordered by how much they would have shortened last night's diagnosis. The
first three are the reason section 3.2 has no answer.

### 6.1 Persist calibration rates and angles, with history

Surface `x_rate`, `y_rate`, `x_angle`, `y_angle`, the raw `y_angle_error`, and
both parities in `calibration_report()`. The comment at native.py:1912 says
they are "intentionally omitted rather than mislabeled" - the right fix is to
label them correctly, not to withhold them. They are the primary evidence
about a calibration's quality.

**And keep the previous ones.** Write each completed calibration to an
append-only per-profile history (timestamp, pier side, declination, all rates
and angles, the advisories, and the run id) instead of overwriting a single
file. Last night's broken calibration was destroyed by the good one 76 minutes
later. With a history, comparing the 00:35 and 01:51 rate vectors would have
answered question 1 of section 3.2 in one line.

Storage is trivial: one small JSON object per calibration, a handful per
night.

### 6.2 Log all guide corrections, not only the clamped ones

Today the only pulses in the record are those the driver had to clamp, which
this analysis shows are the dithers. Everything the guide loop actually did to
the mount is unrecorded.

Recommended: a per-minute aggregate per axis, at debug or into a dedicated
guide log - cycles, corrections issued, signed sum in arcsec, clamped count,
mean and max duration. That is about 120 rows an hour, cheap, and it makes a
duty-cycle drag (Detector C) computable after the fact instead of only live.

### 6.3 Persist the guide error series

`stats()["recent"]` is roughly 30 samples in memory. Append the per-frame
guide error (RA, Dec, total, snr, lock position, re-lock count) to the night
log or a per-run CSV. One row per guide cycle at 2 s exposures is 1800 rows an
hour, which is nothing, and it is the series that would show whether the error
was a standing offset or a growing one.

### 6.4 Enrich the frame headers

Headers today carry a good pointing pair - `RA`/`DEC` (frozen at the last
solve) alongside `MOUNTRAD`/`MOUNTDCD` (the mount's live claim), with
`PNTGSRC` saying which source `OBJCTRA` came from. That pair is what made
section 2.2 possible and it should be kept exactly as it is.

Missing, and all of it computed already and thrown away:

- `GUIDERMS`, `GDRMSRA`, `GDRMSDEC` - guide RMS at exposure time. The grader
  and the guider both have these; no sub records them. A trailing frame
  currently carries no evidence of why it trailed.
- `PIERSIDE` - noted as absent in `nightstack.py`'s own docstring, and it
  means a stack spanning a flip cannot tell a 180 degree rotation from a
  drift.
- `ECCENTR` and `HFR` - the quality grader computes both per frame, logs them
  as prose ("frame eccentricity median 0.81 above ceiling 0.65") and discards
  the numbers. Putting them in the header makes the whole archive queryable
  for exactly this kind of forensics.
- A per-frame verification solve result, when Detector A ran on that frame.

### 6.5 Make the pointing discrepancy a first-class signal

`_current_field_solve` already computes `moved`. Publish it on the status
block and log it as a pointing measurement with its threshold, every time it
is computed - not only when it crosses the line, and not phrased as a cache
event. A number that is computed on a live path every few seconds and never
recorded is the cheapest telemetry win available here.

### 6.6 Two things noticed in passing

**The dead-man's-switch is unset.** Logged as a warning every run start.
Levels 4 and arguably 3 of the response ladder are meaningless until it is
configured.

**The subs carry the observatory's exact latitude and longitude in
`SITELAT`/`SITELONG`.** Given the standing rule that the site location must
not appear in code, tests, docs or output, and that the git history was
rewritten four times on 2026-09-09 specifically to purge those values, every
FITS file written by this rig currently exports them - and these are the files
most likely to be shared or uploaded for plate solving and processing. Worth a
decision: keep them (they are genuinely useful for airmass and for some
processing tools), round them to a coarse grid, or make them opt-in. This is
noted here because it surfaced while reading headers for this analysis; it is
not part of the guiding defect. The values are deliberately not reproduced in
this document.

## 7. Suggested implementation order

Each item is independently shippable and independently testable.

1. **Detector E** (two consecutive dither settle failures is a hold). Earliest
   signal, cleanest separation on real data (0 of 31 healthy, 14 of 14 during
   the walk), needs no new telemetry, and the failures are already raised and
   already logged. Fires at 00:47:45. Test: two consecutive settle failures
   trigger the hold; one does not.
2. **Detector B** (re-lock displacement gate). Smallest change, reuses a
   metric already on the wire, would have fired at 00:50:20. Test: synthesise
   a re-lock event stream and assert the hold fires on magnitude with a count
   below the limit.
3. **Bound the dither** (Defect C) and log the feasibility check once at
   calibration time (Detector D). Test: the cumulative offset never exceeds
   the configured radius over a long synthetic run; and the feasibility warning
   fires for a 3.0 px dither at the measured rates against a 1000 ms cap.
4. **Telemetry 6.1, 6.2, 6.3** (calibration history, pulse aggregate, error
   series). No behaviour change; this is what makes the next occurrence
   diagnosable in one pass instead of a night of forensics.
5. **Detector A** (verification solve) and **response levels 1 and 2**. The
   substantive change, and the one that catches the slow walk.
6. **Response level 3** (home then re-centre), opt-in per plan.
7. **Detector C** thresholds, calibrated against several healthy nights of the
   telemetry from step 4 rather than against this one incident.

A note on testing all of this: the failure signature to reproduce is *"the
guider reports it is guiding, with a good SNR and a healthy Dec, while the
field walks in RA"*. Any test double that reports a loss of lock, or an
inactive guider, or a bad RMS, is testing a different failure - and every
existing guard already catches those. The sim needs to be able to lie the way
the real rig lied.

## 8. Appendix: complete clamped-pulse record, 2026-09-09/10

Every clamped pulse the night produced. Pre-flip entries are above the rule,
post-flip below.

| time | direction | asked | delivered | over by |
|---|---|---|---|---|
| 22:29:52 | east | 1207 | 1000 | 207 |
| 22:40:08 | south | 1316 | 1000 | 316 |
| 22:48:17 | east | 1217 | 1000 | 217 |
| 23:07:45 | north | 2000 | 1000 | 1000 |
| 23:17:44 | east | 1179 | 1000 | 179 |
| 23:25:53 | north | 1511 | 1000 | 511 |
| 23:33:56 | north | 1755 | 1000 | 755 |
| 23:43:40 | east | 1010 | 1000 | 10 |
| 23:49:59 | west | 1226 | 1000 | 226 |
| 00:01:58 | north | 2006 | 1000 | 1006 |
| 00:07:57 | west | 1233 | 1000 | 233 |
| --- flip 00:26-00:35 --- | | | | |
| 00:40:37 | east | 1492 | 1000 | 492 |
| 00:46:40 | east | 1257 | 1000 | 257 |
| 00:50:43 | west | 1004 | 1000 | 4 |
| 00:55:45 | east | 1182 | 1000 | 182 |
| 01:00:46 | west | 1475 | 1000 | 475 |
| 01:05:54 | east | 1393 | 1000 | 393 |
| 01:08:53 | south | 2678 | 1000 | 1678 |
| 01:11:53 | east | 1414 | 1000 | 414 |
| 01:14:42 | west | 1328 | 1000 | 328 |
| 01:17:43 | west | 1389 | 1000 | 389 |
| 01:22:40 | west | 1209 | 1000 | 209 |
| 01:30:32 | north | 2633 | 1000 | 1633 |
| 01:35:30 | west | 1051 | 1000 | 51 |
| 01:38:48 | west | 1185 | 1000 | 185 |
| --- recovery 01:42-01:52 --- | | | | |
| 01:54:00 | west | 1292 | 1000 | 292 |

Dither settle failures: 00:36:40, 00:47:45, 00:51:46, 00:56:45, 01:01:51,
01:06:55, 01:09:52, 01:12:46, 01:15:46, 01:18:44, 01:23:41, 01:31:34,
01:36:32, 01:39:49. Dithers that settled: 00:40:50, and 01:54:13 after
recovery.

Quality warnings during the walk: 00:50:11 ecc median 0.74; 01:08:33 27
percent of 199 stars over 0.80; 01:17:24 median 0.68; 01:22:25 median 0.75, 26
percent over; 01:35:14 median 0.81, **60 percent over**; 01:38:34 median 0.79,
44 percent over; 01:42:09 median 0.69.

Scripts used for this analysis, on the box:
`guide_probe.py`, `walk_csv.py`, `home_and_reslew.py`, `guide_restart.py`.
Local: `scratchpad/forensics.py`, `scratchpad/walk.csv`.

---

# Part 2: the night of 2026-09-10/11

Written 2026-09-11. The same run, the same target, the next night. It failed
again, differently, and it ended with the scope pointed west at **10 degrees
altitude** -- nearly at the horizon -- unparked, with the camera still at
-9.9 C, for about four hours past sunrise.

This part supersedes Part 1's design. Part 1 put four detectors in the
sequence engine's per-frame loop. **Last night proved that is the wrong place,
and Part 1's own Detector E inherited the defect it was meant to fix.**

## 10. What happened

### 10.1 Timeline

| time | event |
|---|---|
| 21:52:23 | run starts, 105 frames. Rotator fails to reach PA 23 (`rotate: plate solve failed: no solution`) and the run continues unrotated |
| 22:16 | I raise a false alarm: the mount reports 245 arcmin off while the sub exposing at that moment solves to 3.16 arcmin |
| 00:13:06 | **the meridian flip is REFUSED**: `the meridian flip was refused by a mount that is not tracking (ZWO AM5 (native serial): tracking on rejected (reply '0'))` |
| 00:13:06 | the engine falls back: `the mount refuses to track - parking, unparking and re-acquiring the target, once` |
| 00:15:31 | `recovered in 145s - the mount is tracking again and the target is re-centred`, 0.5' off |
| 00:15:45 | the rotator finally engages: `solved PA 96.6 deg, target 23.4, error 73.2, commanding -73.2` |
| 00:20:42 | frame 0326: mount reports **1.20 arcmin** off target. The park reset its coordinate error |
| 00:34-00:39 | two dither settle failures; guide error median 747 then 1731 arcsec; frame 0328 **will not plate solve** |
| 00:40 | I pause the run, home the mount, re-centre to 0.3' |
| 00:43:48 | I start a fresh guider calibration |
| **00:50:37** | **the calibration completes and guiding STARTS** -- I had told the operator I stopped it |
| 00:50-04:56 | guiding holds the field at 1-2 arcsec RMS, on a PAUSED run taking no frames, unattended |
| 04:56-05:29 | dawn twilight: **12+ re-locks**, each 530 to **6141 arcsec** away, **~230600 arcsec (64 degrees) accumulated** |
| 05:29:49 | `guide star not reacquired - stopping` |
| **06:19:55** | **`dawn park held off at Sun -6.0: a sequence run is in progress and owns its own wind-down - racing it would be worse`** |
| 09:41 | I park the mount and start the warm ramp. 44 of 105 frames, 3 rejected |

### 10.2 The walk, measured

`MOUNTRAD`/`MOUNTDCD` per frame, offsets from target in arcmin, on-sky:

| frame | shot | filter | mount off | dRA | dDec |
|---|---|---|---|---|---|
| 0320 | 23:53:44 | L | 243.44 | -243.44 | 1.38 |
| 0323 | 23:59:49 | B | 241.15 | -241.14 | 1.96 |
| 0325 | 00:07:50 | Ha | 237.22 | -237.21 | 2.41 |
| 0326 | 00:20:42 | Oiii | **1.20** | 0.90 | 0.79 |
| 0327 | 00:35:57 | L | 13.03 | -11.04 | 6.91 |
| 0328 | 00:39:01 | R | 21.10 | -17.83 | 11.29 |

Two separate things are visible here and they must not be conflated.

**The 4-degree standing offset (0320-0325) is the mount lying, not a walk.**
It reports ~240 arcmin off while the subs solve to 2-3 arcmin of target. It
even *decreases* slowly. This is the same fiction that produced my 22:16 false
alarm, and the **park at 00:13 reset it** -- which is why 0326 reads 1.20.
Anything built on the mount's reported position inherits this.

**The real walk is 0326 to 0328**: 1.20 to 21.10 arcmin in 18.3 minutes =
**1.09 arcmin/min = 65 arcsec/min**, both axes. It is real because the park
had just zeroed the mount's reference, and because the guider agreed (median
747 arcsec at 00:34, 1731 at 00:39 -- 12.4 and 28.9 arcmin) and because frame
0328 would not solve.

### 10.3 The flip never happened

Field rotation from the plate solves, with the rotator **static** (it failed at
21:52 and did not move until 00:15:45):

| when | field rotation |
|---|---|
| 23:59:49, frame 0323, before the meridian | 96.74 deg |
| 00:15:45, after the recovery, before the rotator moved | 96.6 deg |

A real flip changes field rotation by 180 degrees. It did not change. **The
mount was still on the same side of the pier**, tracking past the meridian,
and the 65 arcsec/min walk is that drive.

Two cautions on this method, both learned the hard way here:

- **It is only valid with a known rotator angle.** The invariant is
  `PA_sky - rotator_mechanical`, not `PA_sky`. I nearly drew the wrong
  conclusion from frames after 00:15:45, where a 73 degree rotator correction
  is folded into the same number.
- It is nonetheless the *only* pier-side measurement available from the data
  products, and see 10.6 F3 for why that matters.

### 10.4 What the engine got right, and the one thing it did not

The flip code is better than Part 1 gave it credit for. It has an explicit
"nothing flipped" branch (`engine.py:5021`) that re-arms the latch when the
mount reports the same side after a flip, and the refusal path
(`engine.py:4985-4989`) deliberately leaves the latch armed with the comment
*"the next frame's gate is the right place to decide whether a flip is still
owed"*. The countdown is correctly signed negative past the meridian and its
docstring calls out the wrap bug I went looking for. The AM5 driver really does
report pier side, over LX200 `:Gm#`.

And yet: **two frames and 24 minutes passed after the recovery with an armed
latch and no flip attempt appears in the log at all.** I have not found the
suppressing condition inside the gate. That is an open item (10.7), and it is
the reason the fix below is an INVARIANT rather than a better retry: an
invariant fails safe without depending on why a state machine did not fire.

## 11. The thesis

> Every safety mechanism in this system is a passenger on a value-producing
> path. When the work stops, the check stops. The hazard does not.

Five instances, found on five different nights, all the same shape:

1. `_maybe_recover_guiding`, `_maybe_hold_for_relocks`,
   `_maybe_hold_for_dither_failures`, `_enforce_tracking`, `_enforce_cooling`
   are all called from the per-frame loop. No frames, no checks -- and during
   a 180 s exposure, checks at most every three minutes.
2. `_current_field_solve` computes a real pointing discrepancy and spends it
   invalidating a **display cache** (Part 1, section 3.3).
3. The dawn park defers to "a sequence run is in progress", which is true of a
   paused run that will never progress. **This is the 06:19:55 line.**
4. Memory `astrodeck-no-dawn-park`: every park path hangs off the RUN
   lifecycle, so a night ending without a run tracks through sunrise.
5. Memory `astrodeck-resume-never-armed`: auto-resume only ever armed FRESH
   runs.

This keeps happening because a run is the natural home for orchestration, and
a safety check feels like orchestration. But a run is a **workload**. The
hazards are properties of the equipment and the world: a motorised mount under
power, a cooled sensor, a rising sun. None of them pause.

### 11.1 The principle

**A safety check must be driven by the clock of the hazard it guards, never by
the progress of the work.**

| hazard | its own clock | where the check belongs |
|---|---|---|
| the guider dragging the mount | the guide loop tick, ~2 s | **inside the guider**, as a self-limit |
| the field walked off target | frame completion | the engine (Part 1 Detector A is correctly placed) |
| a flip owed but not performed | the flip's own completion, and every frame after | the engine, as an invariant |
| mount unparked at sunrise | the wall clock | the safety/dawn tick |
| sensor cooled past sunrise | the wall clock | the safety/dawn tick |
| guiding running with nobody driving | the wall clock | the safety tick |

Note what this rescues: Part 1's Detector A (a verification solve every N
frames) is **correctly** frame-driven, because its hazard -- wasting subs --
only exists while subs are being taken. The error in Part 1 was not "frames
are a bad clock"; it was giving *every* check the frame clock regardless of
hazard.

### 11.2 The corollary about the guider

The guider already computes, in its own loop, every number these checks want:
re-lock count, accumulated re-lock displacement, pulse durations, whether the
driver capped them. Today it **reports** them and waits to be judged by the
engine. It should be able to **refuse**.

A guider whose accumulated re-lock displacement exceeds a threshold should stop
itself and say why, with no reference to any sequence. That is:

- independent of the run entirely, so a paused run cannot disable it;
- free, because the numbers are already there;
- correct for standalone guiding, which is a real use case with no run at all;
- and it is what would have stopped last night at about 5 arcmin instead of 64
  degrees.

## 12. The fixes

In the order they should be built. F1 and F2 together fully prevent last
night; F3 prevents the night before.

**F1 -- the guider stops itself.** `guide.relock_arcsec_limit` (default 300
arcsec) and `guide.relock_jump_limit` (default 120 arcsec): accumulated
re-lock displacement inside the window, and any single re-lock, either of
which stops guiding with a stated reason. In `guide/native.py`, driven by the
guide loop, no dependency on the sequence. Would have fired at the FIRST
re-lock last night (573.6 arcsec).

**F2 -- the dawn park stops accepting a veto from a run that is not
progressing.** A run vetoes the dawn park only while it is `running` AND has
completed a frame recently (two exposure lengths, floor 15 min). A `paused`
run gets no veto, and at dawn the park should end it, because resuming a run
into daylight is never right. This also restores the warm ramp, which is why
the camera sat at -9.9 C for four hours.

**F3 -- an invariant, not a retry: do not expose while a flip is owed.**
Before each frame, if the target is past the meridian, the mount is a GEM, the
flip is not skippable, and the measured pier side still equals the pre-flip
side, then refuse to expose and hold. Fails safe whatever the flip state
machine did or did not do.

**F4 -- guiding with nobody driving.** On the wall-clock safety tick: guiding
active, no run `running`, for more than 10 minutes, stop guiding. This is the
narrow, direct fix for the 4.5 hours of unsupervised guiding.

**F5 -- move Part 1's detectors off the frame clock** per the table in 11.1.
Detector A stays. Detectors B, C and E move to the guide loop or the safety
tick. `dither_settle_fail_limit` (shipped as ce84fea3) keeps its frame-driven
placement, because a dither only happens between frames -- but it must be
documented as protecting a running sequence only.

**F6 -- pier side into the status block, and pier limits made real.** The AM5
answers `:Gm#`, but `/api/status` carries no pier side at all, so the UI and
every external check are blind to it. Publish it. Then
`safety.enforce_pier_limits` (set true 2026-09-11 on the operator's word)
becomes testable instead of unproven; and where a mount will not answer,
`PA_sky - rotator_mechanical`, calibrated once per side, supplies it from a
plate solve.

## 13. Corrections to Parts 1 and 2 of this document

Recorded because two of them were wrong in ways that changed what was built.

- Part 1 claimed the 1000 ms pulse cap made every dither settle fail. **False**
  -- corrected in section 3.1a. 31 of 45 dithers settled with the cap in force.
- Part 1 placed all four detectors in the per-frame loop. **Wrong place**, per
  section 11.
- On the night itself I twice reported an AM5 hardware tracking fault. One
  tracking refusal at 00:13 was real; the 6.1 arcsec/s figure was the
  wrong-pier-side drive, and the horizon excursion was the re-lock storm.
  **Prefer the explanation where the software did something over the one where
  the hardware went bad.**
- I reported that I had stopped the guider calibration. I had killed my own ssh
  client; the rig completed the calibration and started guiding. **Killing a
  local client is not stopping a remote operation** -- verify the rig's state,
  not the exit code of your transport.

## 14. Open items

- **Why the armed flip latch produced no retry** across two frames and 24
  minutes after the 00:13 recovery. Evidence in 10.4; mechanism unknown. F3
  makes it non-fatal but does not explain it.
- **Part 1's Defect B** (the 2026-09-10 walk, 117 arcsec/min with a healthy
  Dec) is still not mechanised, and the telemetry gaps in Part 1 section 6
  remain the reason.
- The frames of 2026-09-10/11 are **two framing groups**: the rotator failed at
  21:52 and engaged at 00:15:45 with a 73 degree correction, so subs either
  side of that will not stack as one set.
- `SITELAT`/`SITELONG` in every FITS header, still undecided (Part 1, 6.6).

## 15. What was built, 2026-09-11 (F3, F4, F6)

F1 and F2 shipped in 0.3.30 (`aeb2a038`, `ce84fea3`). F3, F4 and F6 were built
the same afternoon and target 0.3.32, after the UI session's 0.3.31.

### F3 -- the flip-owed invariant

`SequenceEngine._enforce_flip_owed`, called from the frame loop after the
SECOND `_maybe_meridian_flip` and before `_begin_frame`. It refuses to open the
shutter, holds for `safety.flip_owed_hold_min` (default 20 min) re-arming the
flip on each pass, and raises `StopTarget` if the side never changes.

**The test is measured, not assumed**, and this is the part worth keeping in
mind. The obvious rule -- "past the meridian, so the pier side ought to be
east" -- bakes in a convention, and a mount whose east/west sense is the
opposite of ours would then be held forever after a flip that worked perfectly:
the invariant becoming the outage. So the engine records the side the mount
ACTUALLY REPORTED while the target was still east of the meridian
(`_pre_flip_side`) and trips only when the side after the crossing is that same
one. Nothing to get backwards, and `TestItReasonsFromMeasurementNotConvention`
runs the whole thing with an inverted mount and expects no hold.

A target acquired already west of the meridian has no pre-flip reading and is
never guarded. That is correct: a mount that slewed there landed on the side it
chose and owes nothing.

### F4 -- guiding with nobody driving

`DawnPark._check_unattended_guiding`, called from `tick()` **above** the Sun
gate and above the site gate. Guiding active, no run `running` (a PAUSED run
does not count), no hands-off lane, for `safety.unattended_guide_min` (default
10 min) -> stop guiding. A failed stop re-arms rather than latching.

The module docstring was rewritten: `dawn_park.py` is now the rig's wall-clock
safety tick, of which the dawn park is one duty. The two hazards share a timer
and must not share a clock -- putting the guiding check below the Sun gate
would turn it back into a dawn check, which is the defect it exists to fix.

### F6 -- pier side published, and the toggle made real

Two halves, and the first one is worse than section 12 recorded.

**`safety.enforce_pier_limits` was not merely unproven, it was INERT.**
`engine._enforce_mount_floor` gates the whole pier-collision check on
`tel.reports_destination_pier_side`, and of the drivers this rig can load, only
`sim.py` and `alpaca.py` ever set it. The toggle the operator switched on at
09:52 on 2026-09-11 was in the Settings panel, answered True over the API, and
guarded nothing on the only mount this rig owns. A safety control that reports
itself armed while doing nothing is worse than an absent one.

Fixed by implementing `destination_pier_side` on `ZwoAm5Telescope`. The AM5 has
no `DestinationSideOfPier` command, so the side is predicted from hour-angle
geometry -- and **checked before it is trusted**: the rule is first applied to
where the mount is pointing now and compared against `:Gm#`. Agreement means
the convention holds and the same rule is applied to the destination;
disagreement means nobody knows which is right and the answer is UNKNOWN, which
the guard passes. That disagreement is exactly the flip-owed state, where a
slew guard's opinion is worth nothing anyway.

**The status block.** Section 12 said `/api/status` carries no pier side. That
was wrong: `status.meridian.pier_side` has been there all along, and
`redact.py` deliberately preserves it for a non-holder because it is a fact
about the mount rather than about where it stands. What was wrong is that one
timed-out serial read collapsed it to `"unknown"` -- and took `status` and
`flip_enabled` with it, because `_is_gem` reads the same variable. Three fields
added:

| field | values | note |
|---|---|---|
| `pier_side` | `east` / `west` / `unknown` | now falls back to the cache |
| `pier_side_source` | `mount` / `cached` / `none` | `cached` is a real reading inside `PIER_SIDE_STALE_S` (300 s) |
| `pier_side_age_s` | number / null | 0.0 when fresh |
| `flip_owed` | boolean / **null** | F3's refusal, surfaced |

`flip_owed` is NULLED for a caller without `view.site_derived`, for the same
reason the `due` status was: it is true only after the crossing, so it is
another reading of the sign of an hour angle. Null and not False -- False is a
claim that no flip is owed, and a redaction seam must never answer a safety
question on behalf of a caller it is withholding the answer from.

### Two things found while building it

- **One copy of the pier-side rule.** `coords.pier_side_for_hour_angle` is now
  the only statement of "which side does a GEM's tube belong on", read by the
  simulator, the AM5 driver and the engine's invariant. `sim.py` had a private
  copy; that class had already contradicted ITSELF at 8 of 24 RA hours once.
  `hour_angle_h` moved to `coords` with `schedule` delegating, because two
  hour-angle functions is how there came to be two pier-side oracles.
- **A swallowed ImportError.** The first draft of `destination_pier_side` wrote
  `from ..config import config_store` (one dot short) inside a
  `try/except Exception: return UNKNOWN`. The prediction never worked once, and
  reported itself as a mount that would not say. The import now sits outside
  the try: a wrong import is a programming error and must crash, and only the
  DEVICE reads are allowed to degrade. Section 12's own F1/F2 review should
  have caught that this file's failure style can hide defects.

### Still owed after this

- **F5** -- move Part 1's detectors B, C and E off the frame clock per 11.1.
- Section 14's open items are unchanged. In particular F3 makes the missing
  flip retry non-fatal and still does not explain it.
