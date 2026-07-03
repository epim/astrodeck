# PHD2 Core Guiding Stack — Algorithm Dossier

**Purpose**: single source of truth for reimplementing PHD2's guiding stack in Rust for
AstroDeck's in-process guider, without the implementer reading the original C++.

**Provenance**: extracted from the PHD2 source clone at
`C:/Users/bear/astro/references/phd2` (commit `4a13cf245d7e485e79533697f87b032b304df952`,
2026-06-07). PHD2 is BSD-3-Clause (per-file headers, Craig Stark / Bret McKee /
openphdguiding.org). A clean-room Rust reimplementation from this dossier carries no
copyleft obligation; keep the BSD attribution note in AstroDeck's third-party notices.

**Units used throughout** (same as PHD2 internals):
- Star positions / offsets: **pixels** (binned camera pixels), sub-pixel floats.
- Guide pulses: **milliseconds** of guide-rate motor motion.
- Calibration rates (`xRate`, `yRate`): **pixels per millisecond**.
- Angles: **radians**, normalized to (−π, π] by `norm_angle`.
- `x` axis == RA axis, `y` axis == Dec axis (mount coordinates).
- Guide directions: `UP == NORTH` (Dec+), `DOWN == SOUTH` (Dec−), `RIGHT == EAST` (RA−),
  `LEFT == WEST` (RA+). In `MoveOffset`: mount-x error > 0 ⇒ pulse WEST; mount-y error > 0
  ⇒ pulse SOUTH.

---

## Table of contents

1. [Star detection & sub-pixel centroid (`Star::Find`)](#1-star-detection--sub-pixel-centroid)
2. [Auto-find & multi-star selection (`GuideStar::AutoFind`)](#2-auto-find--multi-star-selection)
3. [Frame-to-frame tracking, mass check, jump rejection](#3-frame-to-frame-tracking)
4. [Multi-star offset refinement (`RefineOffset`)](#4-multi-star-offset-refinement)
5. [Camera ⇄ mount coordinate transforms](#5-camera--mount-transforms)
6. [Guide algorithms (per-axis transfer functions)](#6-guide-algorithms)
7. [Move pipeline & pulse limiting (`MoveOffset` / `MoveAxis`)](#7-move-pipeline--pulse-limiting)
8. [Calibration procedure](#8-calibration)
9. [Calibration adjustments at guide start (dec comp, pier flip, rotator, binning)](#9-calibration-adjustments-at-guide-start)
10. [Backlash compensation (BLC)](#10-backlash-compensation)
11. [Dither & fast recenter](#11-dither--fast-recenter)
12. [Settling](#12-settling)
13. [Guide-error statistics (`avgDistance`)](#13-guide-error-statistics)
14. [RA vs Dec asymmetries — summary](#14-ra-vs-dec-asymmetries)
15. [Configuration knobs (defaults & ranges)](#15-configuration-knobs)
16. [Source map (BSD-3 provenance)](#16-source-map)
17. [Recommended for AstroDeck](#17-recommended-for-astrodeck)

---

## 1. Star detection & sub-pixel centroid

`Star::Find(img, search_region, base_x, base_y, mode, min_hfd, max_hfd, max_adu)`
— the single-star measurement used every guide frame and during selection.

### 1.1 Inputs

- `img`: u16 grayscale frame (optionally a subframe with origin/size), plus `pedestal`
  (u16 offset added by the camera driver) and `bits_per_pixel`.
- `search_region`: half-width of the square search window, pixels.
  Default **15**, valid **7..=50**.
- `base_x, base_y`: expected star position (prior frame position, or click/auto-find pos).
- `mode`: `FindCentroid` (normal) or `FindPeak` (used by the Guiding Assistant only).
- `min_hfd`: default **1.5** px, floor **0.1** (config `/guider/StarMinHFD`).
- `max_hfd`: default **20.0** px (config `/guider/StarMaxHFD`).
- `max_adu`: camera saturation ADU if known, else 0 → use flat-top heuristic.

### 1.2 Result codes

```rust
enum FindResult {
    StarOk, StarSaturated,             // both count as "found"
    StarLowSnr, StarLowMass, StarLowHfd, StarHiHfd,
    StarTooNearEdge, StarMassChange, StarError,   // all "not found"
}
fn was_found(r: FindResult) -> bool { matches!(r, StarOk | StarSaturated) }
```

On any not-found result the star's `mass/snr/hfd` are zeroed; position is still updated
to the best estimate (so the search window follows).

### 1.3 Algorithm (FindCentroid mode)

```rust
// 1. clip search window to image / subframe bounds
let (minx, miny, maxx, maxy) = subframe_or_full_bounds(img);
let sx = max(base_x - search_region, minx);
let ex = min(base_x + search_region, maxx);
let sy = max(base_y - search_region, miny);
let ey = min(base_y + search_region, maxy);
if ex <= sx || ey <= sy { return StarError; }

// 2. smoothed peak search over interior (sx+1 ..= ex-1, sy+1 ..= ey-1)
//    3x3 kernel, integer weights (sum = 16):
//        1 2 1
//        2 4 2
//        1 2 1
//    peak = argmax of the weighted sum. Also track the top-3 RAW pixel values
//    max3[0] >= max3[1] >= max3[2] over the same interior region.
peak_val /= 16;                 // smoothed peak value
star.peak_val = max3[0];        // raw peak reported

// 3. background estimate: annulus around (peak_x, peak_y),
//    inner radius A = 7, outer radius B = 12 (r^2 in (A^2, B^2]),
//    window clipped to bounds. Iterative 2-sigma clip, up to 9 iterations:
let (mut mean_bg, mut sigma_bg, mut sigma2_bg) = (0.0, 0.0, 0.0);
for iter in 0..9 {
    // Welford accumulation over annulus pixels; on iter > 0 exclude
    // values outside mean_bg ± 2*sigma_bg
    let (sum, nbg, q) = welford_over_annulus(...);
    if nbg < 10 { break; }                       // too few points (only possible iter>0)
    let prev = mean_bg;
    mean_bg  = sum / nbg as f64;
    sigma2_bg = q / (nbg - 1) as f64;
    sigma_bg  = sigma2_bg.sqrt();
    if iter > 0 && (mean_bg - prev).abs() < 0.5 { break; }   // converged
}

// 4. threshold and centroid over circular aperture radius A = 7 around peak
let thresh = (mean_bg + 3.0 * sigma_bg + 0.5) as u16;
let (mut cx, mut cy, mut mass, mut n) = (0.0, 0.0, 0.0, 0u32);
let mut hfr_pixels: Vec<(i32, i32, f64)> = vec![];   // (x, y, bg-subtracted value)
for (x, y) in disk(peak, A, bounds) {
    let val = img[y][x];
    if val < thresh { continue; }
    let d = val as f64 - mean_bg;
    cx += (x - peak_x) as f64 * d;
    cy += (y - peak_y) as f64 * d;
    mass += d;
    n += 1;
    hfr_pixels.push((x, y, d));
}

// 5. SNR (Simonetti 2004, "Measuring the Signal-to-Noise Ratio S/N of the
//    CCD Image of a Star or Nebula"); nominal gain = 0.5 e-/ADU:
const GAIN: f64 = 0.5;
star.snr = if n > 0 {
    mass / (mass / GAIN + sigma2_bg * n as f64 * (1.0 + 1.0 / nbg as f64)).sqrt()
} else { 0.0 };

// 6. false-positive guard: scattered above-threshold pixels can fake a star.
//    Require the smoothed peak to itself be above the threshold:
const LOW_SNR: f64 = 3.0;
if peak_val <= thresh as u32 && star.snr >= LOW_SNR { star.snr = LOW_SNR - 0.1; }

// 7. acceptance gates (in this order):
if mass < 10.0        { return StarLowMass; }
if star.snr < LOW_SNR { return StarLowSnr; }

// 8. sub-pixel centroid:
star.x = peak_x as f64 + cx / mass;
star.y = peak_y as f64 + cy / mass;

// 9. HFD = 2 * half-flux radius (see 1.4)
star.hfd = 2.0 * hfr(&mut hfr_pixels, star.x, star.y, mass);
if star.hfd < min_hfd { return StarLowHfd; }   // hot-pixel rejection
if star.hfd > max_hfd { return StarHiHfd; }

// 10. saturation detection on raw peak minus pedestal:
let mx = max3[0].saturating_sub(img.pedestal) as u32;
if max_adu > 0 {
    if mx >= max_adu as u32 { return StarSaturated; }  // still "found"
    return StarOk;
}
// flat-top heuristic when saturation ADU unknown: top-3 values nearly equal
let d = (max3[0] - max3[2]) as u32;
if img.bits_per_pixel < 12 {
    if d * 191   < mx      { return StarSaturated; }   // 8-bit: within 1/191 of peak
} else {
    if d * 65535 < 32 * mx { return StarSaturated; }   // 16-bit: within 32/65535
}
StarOk
```

**FindPeak mode** differs only in step 2 (raw max instead of smoothed; no max3 tracking),
skips thresholding/centroid (`mass = peak_val`, `n = 1`, `thresh = 0`), and skips HFD
constraints. Position stays at the integer peak.

### 1.4 Half-flux radius (`hfr`)

```rust
fn hfr(v: &mut Vec<(x, y, m)>, cx: f64, cy: f64, mass: f64) -> f64 {
    if v.len() == 1 { return 0.25; }              // single pixel: hot pixel
    // r2 = squared distance of each pixel center from the centroid
    v.sort_by(r2_ascending);
    let half = 0.5 * mass;
    // walk cumulative mass m1; keep previous (r20, m0)
    let (mut r20, mut r21, mut m0, mut m1) = (0.0, 0.0, 0.0, 0.0);
    for &(.., m) in v.iter() {
        r20 = r21; m0 = m1;
        r21 = r2_of_this_pixel; m1 += m;
        if m1 > half { break; }
    }
    if m1 > m0 {
        let (r0, r1) = (r20.sqrt(), r21.sqrt());
        r0 + (r1 - r0) / (m1 - m0) * (half - m0)   // linear interpolation in radius
    } else { 0.25 }
}
```

---

## 2. Auto-find & multi-star selection

`GuideStar::AutoFind(image, extra_edge_allowance, search_region, roi, out_found_stars, max_stars)`
— full-frame star selection. Fails immediately if the image is a subframe.

### 2.1 Pipeline

```text
median3x3 (hot-pixel removal, restricted to ROI if given; ROI must be
           >= search_region in each dimension or fail)
→ convert to f32
→ optional downsample (box average): config /guider/AutoSelDownsample:
     0 = auto → 1x if pixel scale > 0.6 "/px else 2x;  else literal 1/2/3...
→ 9x9 PSF matched-filter convolution (below)
→ local-maximum scan → candidate peaks (top 100 by response)
→ merge peaks closer than 5 px (drop the dimmer)
→ drop pairs that fit in one search box (unless 5x brightness gap)
→ drop peaks near frame edge
→ saturation-level inference
→ candidate list for multi-star + 3-pass primary selection
```

### 2.2 PSF convolution kernel

For each pixel (interior margin 4), classify the 9×9 neighborhood into rings and
compute ring sums:

- `A` = center (1 px); `B1` = 4 edge-adjacent; `B2` = 4 diagonal-adjacent;
- `C1` = 4 at distance 2 straight; `C2` = 8 knight-like at (±1,±2)/(±2,±1);
  `C3` = 4 at (±2,±2); `D1` = 4 at distance 3 straight;
  `D2` = 8 at (±1,±3)/(±3,±1); `D3` = the remaining 44 border cells of the 9×9 block.

With `mean = (A+B1+B2+C1+C2+C3+D1+D2+D3)/81`:

```text
response = 0.906*(A - mean)      + 0.584*(B1 - 4*mean)  + 0.365*(B2 - 4*mean)
         + 0.117*(C1 - 4*mean)   + 0.049*(C2 - 8*mean)  + (-0.05)*(C3 - 4*mean)
         + (-0.064)*(D1 - 4*mean)+ (-0.074)*(D2 - 8*mean)+ (-0.094)*(D3 - 44*mean)
```

### 2.3 Peak acceptance

- Local max test: `val > 0` and strictly ≥ all pixels in a ±4 window (any greater
  neighbor disqualifies).
- Significance: `h = (val − local_mean) / global_stdev` where `local_mean` is over a
  15×15 window (±7) clipped to the valid conv region, `global_stdev` over the whole valid
  region. Accept if `h ≥ 0.1`.
- Map to original coords: `imgx = x*downsample + downsample/2`.
- Keep the brightest **100** (`TOP_N`), by `h`.

### 2.4 Duplicate/edge filtering

- **Merge**: any two peaks with `d² < 25` (5 px) → erase the dimmer one; repeat to fixpoint.
- **Search-box conflict**: for any pair with `|dx| ≤ search_region+5` and
  `|dy| ≤ search_region+5`: if `brighter/dimmer ≥ 5.0` keep both (log only); else erase
  **both**.
- **Edge**: erase peaks within `search_region + extra_edge_allowance` of any edge.
  `extra_edge_allowance = max over uncalibrated mounts of CalibrationTotDistance()`
  (i.e. the calibration distance in px), else 0.

### 2.5 Saturation level

- If camera reports saturation-by-ADU: `sat_level = satADU + pedestal`
  (`satADU` defaults to `(1<<bpp)−1` if unset).
- Else: find the overall max pixel `maxVal`; re-measure candidate stars brightest-first
  with `Star::Find`; if a found star reports `StarSaturated` and
  `(maxVal − star.peak_val) * 255 ≤ maxVal` (within 1/255 of max) →
  `sat_level = maxVal`; otherwise `sat_level = ((1<<bpp)−1) + pedestal`.
- Near-saturation threshold: `sat_thresh = pedestal + 9*(sat_level−pedestal)/10`
  (clamped to 65535) — i.e. 90% of full range.

### 2.6 Candidate list (multi-star mode, `max_stars > 1`)

For each peak, brightest response first: run `Star::Find(FindCentroid)` at the peak;
accept if found and `snr ≥ af_min_snr` (default **6.0**, config `/guider/StarMinSNR`);
reject duplicates within **25 px** of an already-accepted star; store
`reference_point = (x, y)`. Callers use `max_stars = 12` (`MAX_LIST_SIZE`) when
multi-star is on and no subframes; `1` otherwise.

### 2.7 Primary star selection — three passes

Iterate peaks brightest-first, first pass that yields a star wins:

1. **Pass 1**: found ∧ `peak_val ≤ sat_thresh` ∧ not saturated ∧ `snr ≥ af_min_snr`.
2. **Pass 2**: found ∧ not saturated ∧ `snr ≥ af_min_snr`.
3. **Pass 3**: any found star (even saturated / low SNR).

After picking the primary (multi-star): locate it in the candidate list by exact (x, y);
compute `offset_from_primary = reference_point − primary_ref` for all others; erase all
candidates *ahead of* the primary (they were rejected as saturated/degraded); truncate
the list to `max_stars`. If the primary isn't in the list, clear it and insert the
primary alone.

---

## 3. Frame-to-frame tracking

Per guide frame, `GuiderMultiStar::UpdateCurrentPosition`:

```rust
// 1. no star selected? → error "No star selected"
// 2. Star::Find at last position with the current search region.
//    Not found → record error, activate DistanceChecker, frame dropped.
// 3. star-mass check (if enabled) — see 3.1. Reject → STAR_MASSCHANGE, frame dropped,
//    DistanceChecker activated, the new mass IS still appended to the history.
// 4. distance from lock position:
//    raOnly (dec guide mode == None) ? |x - lock.x| : euclidean distance
// 5. DistanceChecker.check_distance (see 3.2). Reject → frame dropped ("Recovering").
// 6. accept: primary_star = new_star; append mass to MassChecker history.
// 7. offset.camera = star - lock_position
//    if multi-star: RefineOffset may replace offset.camera (see §4)
//    offset.mount = camera_to_mount(offset.camera)
//    update avg-distance stats with (distance, |offset.mount.x|)  (see §13)
```

### 3.1 MassChecker

Rolling time-window median filter on star mass.

- Window: `DefaultTimeWindowMs = 22500` ms, stored ×2 ⇒ entries older than **45 s**
  are dropped (median responds to a real change after ~window/2).
- Auto-exposure mode: masses are normalized by exposure (`mass/exposure_ms`); changing
  exposure resets history in fixed-exposure mode.
- `check_mass(mass, threshold)` (threshold default **0.5**, config
  `/guider/onestar/MassChangeThreshold`; enabled flag
  `/guider/onestar/MassChangeThresholdEnabled`, default `threshold != 1.0` ⇒ true):

```rust
if history.len() < 5 { return Accept; }
let med = median(history);
high_water = max(high_water, med);
low_water  = min(low_water, med);
low_water += 0.05 * (med - low_water);       // drift back up after clouds
let lim0 = low_water * (1.0 - threshold);
let lim2 = high_water * (1.0 + threshold);
let lim3 = med * (1.0 + 2.0 * threshold);    // spike guard while mass depressed
reject = mass < lim0 || mass > lim2 || mass > lim3;
```

Reset (clears history and water marks) on: star (re)selection, auto-find,
exposure-mode change.

### 3.2 DistanceChecker (jump rejection / lost-star recovery)

State machine: `Guiding → Waiting → Recovering → Guiding`. `WAIT_INTERVAL_MS = 5000`.

- `tolerance = tolerate_jumps_enabled ? tolerate_jumps_threshold : 9e99`
  (enabled default **false**, threshold default **4.0**;
  configs `/guider/onestar/TolerateJumpsEnabled`, `.../TolerateJumpsThreshold`).
- A frame is a "small offset" if any of: not guiding / paused / settling /
  fewer than **10** accepted frames since reset — else
  `distance ≤ tolerance * current_error_smoothed(ra_only)`.
- `activate()` (called on star lost or mass-change reject): if `Guiding` →
  `Waiting`, expiry = now + 5 s, and forces `tolerance = 2.0` until deactivated.
- `check_distance`:
  - `Guiding`: small → accept. Large → `Waiting` (expiry now+5 s), reject.
  - `Waiting`: small → back to `Guiding` (clear forced tolerance), accept.
    Large and not expired → reject. Large and expired → `Recovering`, accept.
  - `Recovering`: accept; small offset returns state to `Guiding`.

### 3.3 Lost-star behavior in the guide loop

When `UpdateCurrentPosition` fails during guiding, PHD2 still schedules a
**dead-reckoning move**: `MoveOffset` with `MOVEOPTS_DEDUCED_MOVE`
(`ALGO_DEDUCE | USE_BLC | GRAPH`), which asks each axis algorithm for
`deduce_result()`. Only the Gaussian-Process algorithm returns non-zero; all others
return 0.0 (no move). Same happens for every frame while paused ("looping" pause).

---

## 4. Multi-star offset refinement

`GuiderMultiStar::RefineOffset(image, &mut offset)` — refines the camera-frame offset
using secondary stars. Constants: `DEFAULT_MAX_STAR_COUNT = 9` (`m_maxStars`),
`stability_sigma_x = 5`, list capacity 12.

```rust
// preconditions: guiding, list len > 1, guiding output enabled, not settling
let mut sum_w = 1.0;                          // primary weight = 1
let mut sum_x = offset.camera.x;
let mut sum_y = offset.camera.y;
let primary_dist = hypot(sum_x, sum_y);
primary_dist_stats.add(primary_dist);         // running (non-windowed) sigma

// --- stabilization gate ---
if primary_dist_stats.count() > 5 {
    let sigma = primary_dist_stats.sigma();
    if !stabilizing && primary_dist > 5.0 * sigma { stabilizing = true; }
    else if stabilizing && primary_dist <= 2.0 * sigma {
        stabilizing = false;
        if lock_position_moved {              // i.e. after a dither
            lock_position_moved = false;
            // re-find every secondary at primary + offset_from_primary
            // (or at its own last position if the expected loc is off-frame);
            // found → reference_point = new pos, was_lost = false
            // not found → was_lost = true (recovery via offset_from_primary later)
            return false;                     // no refinement this frame
        }
    }
} else { stabilizing = true; }                // still collecting stats

if stabilizing || (sum_x == 0.0 && sum_y == 0.0) { return false; }

// --- secondary star loop (stars_used capped at m_maxStars = 9 incl. primary) ---
for star in secondaries {
    // search where last seen; if was_lost, search at primary + offset_from_primary
    let found = star.find(...);
    if !found { star.was_lost = true; continue; }        // "L"
    let dx = star.x - star.reference_point.x;
    let dy = star.y - star.reference_point.y;
    star.was_lost = false;
    stars_used += 1;
    if dx == 0.0 && dy == 0.0 { erase(star); continue; } // hot pixel ("DZ")
    // zero-counting: exactly zero on ONE axis is suspicious
    if dx == 0.0 || dy == 0.0 { star.zero_count += 1; }
    else if star.zero_count > 0 { star.zero_count -= 1; }
    if star.zero_count == 5 { erase(star); continue; }   // "DZ"
    // excursion check vs primary sigma
    let sec_dist = hypot(dx, dy);
    if sec_dist > 2.5 * primary_sigma {
        star.miss_count += 1;
        if star.miss_count > 10 {                        // re-baseline ("R")
            star.reference_point = (star.x, star.y);
            star.miss_count = 0;
        }
        continue;                                        // skip this frame ("M")
    } else if star.miss_count > 0 { star.miss_count -= 1; }
    // usable: SNR-relative weight
    let w = star.snr / primary.snr;
    sum_x += w * dx;  sum_y += w * dy;  sum_w += w;
    averaged = true;
}

if averaged {
    sum_x /= sum_w;  sum_y /= sum_w;
    if hypot(sum_x, sum_y) < primary_dist {   // only if it SHRINKS the offset
        offset.camera = (sum_x, sum_y);
        return true;
    }
}
false
```

Any exception in this path permanently drops back to single-star mode for the session.
A dither sets `lock_position_moved = true` and `stabilizing = true`
(via `SetLockPosition` override).

---

## 5. Camera ⇄ mount transforms

Calibration stores `xAngle` (RA axis angle in camera frame), `yAngle`, and rates. The
runtime keeps `y_angle_error = norm_angle(xAngle − yAngle + π/2)` — the deviation of the
measured axes from perfect orthogonality (0 or π for a perfect mount, depending on
motion direction).

```rust
fn norm_angle(a: f64) -> f64 { /* wrap to (-PI, PI] */ }

fn camera_to_mount(cam: Vec2, cal: &Cal) -> Vec2 {
    let hyp = cam.len();
    let theta = cam.angle();                    // atan2(y, x)
    let x_angle = theta - cal.x_angle;
    let y_angle = theta - (cal.x_angle + cal.y_angle_error);
    Vec2 { x: x_angle.cos() * hyp, y: y_angle.sin() * hyp }
}

fn mount_to_camera(mnt: Vec2, cal: &Cal) -> Vec2 {
    let hyp = mnt.len();
    let mut theta = mnt.angle();
    if cal.y_angle_error.abs() > PI / 2.0 { theta = -theta; }   // axis-reversal case
    let x_angle = theta + cal.x_angle;
    Vec2 { x: x_angle.cos() * hyp, y: x_angle.sin() * hyp }
}
```

Note: when axes are non-orthogonal the reverse transform is not an exact inverse; error
grows with `y_angle_error`. PHD2 accepts this.

---

## 6. Guide algorithms

Each mount axis owns one algorithm instance; input is the **mount-frame error in
pixels** for that axis (positive = star displaced in axis-positive direction); output is
the desired correction distance in pixels (converted to ms by the caller using the axis
rate). Defaults: **RA = Hysteresis**, **Dec = ResistSwitch**
(`scope.cpp` `DefaultRaGuideAlgorithm` / `DefaultDecGuideAlgorithm`).

Common interface details:

- `reset()` clears history. Called on: guiding stopped, guiding resumed after full pause,
  **dither** (`GuidingDithered`), guiding re-enabled.
- `deduce_result()` → 0.0 for all algorithms except Gaussian Process (see §6.8).
- `GetMinMove()` returns −1.0 for algorithms without a min-move (Identity); BLC clamps
  it to ≥ 0.
- **Smart default min-move** (used when profile is built via wizard / ResetParams):
  `max(0.1515 + 0.1548 / image_scale_arcsec_per_px, 0.15)` px; fallback 0.2 when focal
  length unknown.

### 6.1 Hysteresis (default RA)

Constants: `DefaultMinMove 0.2`, `DefaultHysteresis 0.1`, `DefaultAggression 0.7`,
`MaxAggression 2.0`, `MaxHysteresis 0.99`.

```rust
struct Hysteresis { hysteresis: f64, aggression: f64, min_move: f64, last_move: f64 }

fn result(&mut self, input: f64) -> f64 {
    let mut r = (1.0 - self.hysteresis) * input + self.hysteresis * self.last_move;
    r *= self.aggression;
    if input.abs() < self.min_move { r = 0.0; }   // min-move tested on INPUT
    self.last_move = r;                            // vetoed moves store 0 → decay
    r
}
fn reset(&mut self) { self.last_move = 0.0; }
```

Validation: `min_move ≥ 0` (else default), `0 ≤ hysteresis ≤ 0.99` (else clamp),
`0 ≤ aggression ≤ 2.0` (else default; setting aggression also zeroes `last_move`).

### 6.2 ResistSwitch (default Dec)

Constants: `HISTORY_SIZE 10`, `DefaultMinMove 0.2` (must be > 0),
`DefaultAggression 1.0` (range 0..=1), `fastSwitch` default **true**.

State: `history: [f64; 10]` (ring, init 0), `current_side: i32` (−1/0/+1).

```rust
fn sign(x: f64) -> i32 { if x > 0.0 {1} else if x < 0.0 {-1} else {0} }

fn result(&mut self, input: f64) -> f64 {
    self.history.push_back(input); self.history.pop_front();
    let veto = 'v: {
        if input.abs() < self.min_move { break 'v true; }        // deadband
        if self.fast_switch {
            let thresh = 3.0 * self.min_move;
            if sign(input) != self.current_side && input.abs() > thresh {
                // large excursion: force an immediate direction switch
                self.current_side = 0;
                for i in 0..HISTORY_SIZE-3 { self.history[i] = 0.0; }
                for i in HISTORY_SIZE-3..HISTORY_SIZE { self.history[i] = input; }
            }
        }
        // vote: count signs of history entries exceeding min_move
        let dec_history: i32 = self.history.iter()
            .filter(|v| v.abs() > self.min_move).map(|v| sign(**v)).sum();
        if self.current_side == 0 || sign(self.current_side as f64) == -sign(dec_history as f64) {
            if dec_history.abs() < 3 { break 'v true; }          // "not compelling"
            // require error to be worsening: |sum newest 3| > |sum oldest 3|
            let oldest: f64 = self.history[0..3].iter().sum();
            let newest: f64 = self.history[HISTORY_SIZE-3..].iter().sum();
            if newest.abs() <= oldest.abs() { break 'v true; }   // "not getting worse"
            self.current_side = sign(dec_history as f64);
        }
        if self.current_side != sign(input) { break 'v true; }   // overshoot veto
        false
    };
    let r = if veto { 0.0 } else { input };
    r * self.aggression
}
fn reset(&mut self) { self.history = [0.0; 10]; self.current_side = 0; }
```

Key behavior: only ever guides on the side of the current drift direction; suppresses
reversals (backlash-safe) unless 3+ significant same-sign samples AND worsening trend;
fast-switch bypasses the wait for deflections > 3×min-move.

### 6.3 Lowpass (classic; not a default, included for completeness)

Constants: `HISTORY_SIZE 10`, `DefaultMinMove 0.2`, `DefaultSlopeWeight 5.0` (≥ 0).
Window is zero-filled at reset, manually trimmed to 10 entries.

```rust
fn result(&mut self, input) -> f64 {
    stats.add(t, input); t += 1;
    let median = stats.median();
    stats.remove_oldest();
    let (slope, _icpt) = stats.linear_fit();
    let mut r = median + self.slope_weight * slope;
    if r.abs() > input.abs() { r = input; }        // clamp to input magnitude
    if input.abs() < self.min_move { r = 0.0; }    // min-move on INPUT
    r
}
```

### 6.4 Lowpass2 (linear-fit predictor)

Constants: `HISTORY_SIZE 10` (auto-windowed), `DefaultMinMove 0.2`,
`DefaultAggressiveness 80.0` (percent, ≥ 0; UI 0..100).

```rust
fn result(&mut self, input: f64) -> f64 {
    stats.add(self.time_base, input, 0.0);          // x = frame counter 0,1,2,...
    self.time_base += 1;
    let n = stats.count();
    let att = self.aggressiveness / 100.0;
    let mut r;
    if n < 4 {
        r = input * att;                            // warm-up: act like proportional
    } else if input.abs() > 4.0 * self.min_move {
        r = input * att;                            // outlier: dump history
        self.reset();
    } else {
        let (slope, _icpt) = stats.linear_fit();    // least squares, see below
        r = slope * n as f64 * att;                 // predicted cumulative drift
        if input * r < 0.0 { r = 0.0; }             // never push the wrong way
    }
    if r.abs() > input.abs() {                      // keep pulses <= deflection
        r = input * att;
        self.rejects += 1;
        if self.rejects > 3 { self.reset(); }       // slope isn't useful
    } else { self.rejects = 0; }
    if input.abs() < self.min_move { r = 0.0; }     // min-move on INPUT
    r
}
```

Least-squares over the window (running sums maintained incrementally, oldest entry's
contributions subtracted when the window slides):

```text
slope     = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)
intercept = (Σy − slope·Σx) / n
```

### 6.5 ZFilter (IIR low-pass with drift correction)

Constants: `DefaultMinMove 0.1`, `DefaultExpFactor 2.0` (range 1.0..=20.0, UI step 1.0).
Design: **Bessel, order 4**; corner period `corner = exp_factor * 4.0` (in units of
guide exposures); if `corner < 6.0` the design silently switches to **Butterworth**
(so exp_factor 1.0 → Butterworth-4 with corner 4.0). MZT off (bilinear transform).

Runtime (`m_gain = |H(z=1)|` of the unnormalized filter, i.e. DC gain):

```rust
// xv[0] newest input sample, yv[0] newest output; lengths = xcoeffs.len(), ycoeffs.len()
fn result(&mut self, input: f64) -> f64 {
    // reconstruct the UNGUIDED waveform: measured error + total correction applied
    self.xv.push_front((input + self.sum_corr) / self.gain); self.xv.pop_back();
    self.yv.push_front(0.0); self.yv.pop_back();
    let mut y0 = 0.0;
    for i in 0..self.xcoeffs.len() { y0 += self.xv[i] * self.xcoeffs[i]; }
    for i in 1..self.ycoeffs.len() { y0 += self.yv[i] * self.ycoeffs[i]; }
    self.yv[0] = y0;
    let mut r = y0 - self.sum_corr;      // filtered position minus already-corrected
    if r.abs() < self.min_move { r = 0.0; }   // NOTE: min-move applies to the OUTPUT
    self.sum_corr += r;                  // integrate issued corrections
    r
}
fn reset(&mut self) { self.xv.fill(0.0); self.yv.fill(0.0); self.sum_corr = 0.0; }
```

Filter synthesis (port of A.J. Fisher's `mkfilter`, bilinear low-pass):

```rust
fn build(design: Design, order: usize, corner_period: f64) -> Filter {
    assert!(order >= 1 && corner_period >= 2.0);
    let alpha = 1.0 / corner_period;                    // normalized corner frequency
    // 1. s-plane prototype poles:
    //    Bessel: from the table below; index p = order*order/4; if order odd take
    //    bessel_poles[p++] (real pole), then order/2 conjugate pairs from p on.
    //    Butterworth: for i in 0..2*order: theta = (order odd)? i*PI/order
    //                 : (i+0.5)*PI/order; pole = e^{i*theta}; keep only Re < 0.
    // 2. prewarp:  warped = tan(PI * alpha) / PI
    // 3. normalize: multiply all poles by 2*PI*warped
    // 4. bilinear z-transform: z = (2 + s) / (2 - s); zeros: fill with -1
    //    until #zeros == #poles
    // 5. expand (z - root) products into polynomials top (zeros) / bot (poles)
    //    dc_gain = top(1)/bot(1)
    // 6. recurrence coefficients, newest-first order as used by result():
    //    xcoeffs[k] = +Re(top[n-k]) / Re(bot[n])   for k = 0..=n
    //    ycoeffs[k] = -Re(bot[n-k]) / Re(bot[n])   for k = 0..=n   (ycoeffs[0] unused)
    //    gain = |dc_gain|
}
```

Bessel pole table (one member of each conjugate pair; index `p = order²/4`):

```text
(-1.00000000000e+00, 0)                     // order 1 starts at p=0
(-1.10160133059e+00, 6.36009824757e-01)     // order 2 p=1
(-1.32267579991e+00, 0)                     // order 3 p=2 (real), then p=3
(-1.04740916101e+00, 9.99264436281e-01)
(-1.37006783055e+00, 4.10249717494e-01)     // order 4 p=4..5
(-9.95208764350e-01, 1.25710573945e+00)
(-1.50231627145e+00, 0)                     // order 5 p=6 (real), 7..8
(-1.38087732586e+00, 7.17909587627e-01)
(-9.57676548563e-01, 1.47112432073e+00)
(-1.57149040362e+00, 3.20896374221e-01)     // order 6 p=9..11
(-1.38185809760e+00, 9.71471890712e-01)
(-9.30656522947e-01, 1.66186326894e+00)
(-1.68436817927e+00, 0)                     // order 7 p=12 (real), 13..15
(-1.61203876622e+00, 5.89244506931e-01)
(-1.37890321680e+00, 1.19156677780e+00)
(-9.09867780623e-01, 1.83645135304e+00)
(-1.75740840040e+00, 2.72867575103e-01)     // order 8 p=16..19
(-1.63693941813e+00, 8.22795625139e-01)
(-1.37384121764e+00, 1.38835657588e+00)
(-8.92869718847e-01, 1.99832584364e+00)
(-1.85660050123e+00, 0)                     // order 9 p=20 (real), 21..24
(-1.80717053496e+00, 5.12383730575e-01)
(-1.65239648458e+00, 1.03138956698e+00)
(-1.36758830979e+00, 1.56773371224e+00)
(-8.78399276161e-01, 2.14980052431e+00)
(-1.92761969145e+00, 2.41623471082e-01)     // order 10 p=25..29
(-1.84219624443e+00, 7.27257597722e-01)
(-1.66181024140e+00, 1.22110021857e+00)
(-1.36069227838e+00, 1.73350574267e+00)
(-8.65756901707e-01, 2.29260483098e+00)
```

Known source quirks (reproduce or fix consciously): the Chebyshev branch reads an
**uninitialized ripple** member (`chripple`) — Chebyshev is unreachable via the UI, so
just don't implement it. `SetMinMove`/`SetExpFactor` both rebuild the filter and reset
state.

### 6.6 Identity

Pass-through: `result(x) = x`, no min-move. Used for AO step-guider axes.

### 6.7 Min-move semantics summary

| Algorithm    | Deadband tested on | Below deadband → | History still updated? |
|--------------|--------------------|------------------|------------------------|
| Hysteresis   | input              | output 0         | yes (`last_move = 0`)  |
| ResistSwitch | input              | output 0         | yes (input in ring)    |
| Lowpass      | input              | output 0         | yes                    |
| Lowpass2     | input              | output 0         | yes                    |
| ZFilter      | **output**         | output 0         | yes (`sum_corr` unchanged) |
| PPEC (§6.8)  | input (reactive part only) | reactive 0; **prediction still applied** | yes |

### 6.8 Gaussian Process — "Predictive PEC" (PPEC)

RA-only in PHD2's UI (present in `RA_ALGORITHMS`, absent from `DEC_ALGORITHMS` /
`AO_ALGORITHMS`, `mount.cpp:227-240`). Two layers: a PHD2 wrapper
(`GuideAlgorithmGaussianProcess`) and a self-contained controller
(`GaussianProcessGuider`, BSD-licensed, Max Planck Society) plus a small GP regression
class (Eigen). Everything below is exact.

#### 6.8.1 Parameters (wrapper defaults, config keys under the algo's config path)

| Parameter | Default | UI range | Config key |
|---|---|---|---|
| Control gain (reactive) | **0.6** | 0..1 (UI 0..100%) | `gp_control_gain` |
| Prediction gain | **0.5** | 0..1 (UI 0..100%) | `gp_prediction_gain` |
| Min move (px) | **0.2** | 0..5 step 0.01 | `gp_min_move` |
| SE0 (long-range) length scale (s) | **700** | 100..5000 | `gp_length_scale_se0_kern` |
| SE0 amplitude (px) | **20** | 0..100 | `gp_sigvar_se0_kern` |
| Periodic length scale (s, natural units) | **10** | 1..50 | `gp_length_scale_per_kern` |
| Periodic period length P (s) | **200** | 10..2000 | `gp_period_per_kern` |
| Periodic amplitude (px) | **20** | 0..100 | `gp_sigvar_per_kern` |
| SE1 (short-range) length scale (s) | **25** | 1..100 | `gp_length_scale_se1_kern` |
| SE1 amplitude (px) | **10** | 0..100 | `gp_sigvar_se1_kern` |
| Min periods for full prediction | **2.0** | 0..10 step 0.1 | `gp_period_lengths_inference` |
| Min periods before period estimation | **2.0** | 0..10 step 0.1 | `gp_period_lengths_period_estimation` |
| Points for GP approximation | **100** | 0..2000 | `gp_points_for_approximation` |
| Auto-adjust period (FFT) | **true** | bool | `gp_compute_period` |
| Retain model after stop, % of period | **40** | 0..80 | `noreset_max_pct_period` |

Controller constants: circular buffer **8192** points; regularized buffer **2048**;
FFT zero-pad size **4096** (min; grows to next pow2 ≥ data length); grid interval
**5.0 s**; dither dark-guiding steps **10**; period-length learning rate **0.01**;
hybrid hysteresis constant **0.1**.

#### 6.8.2 Data model & measurement noise

Circular buffer of `data_point { timestamp, measurement, variance, control }`. On
construction/reset one point is pushed with `control = 0` (measurements are relative to
the previous control). Timestamp of a measurement = seconds since start **minus half the
gap since the previous frame** (midpoint convention) **plus `dither_offset`** (gear-time
correction, see 6.8.6).

```rust
fn variance_from_snr(snr: f64) -> f64 {
    let snr = snr.max(3.4);
    let sd = 2.1752 / (snr - 3.3) + 0.5;   // determined by simulated experiments
    sd * sd
}
// dark guiding (no measurement): measurement = 0, variance = 1e4
```

#### 6.8.3 Per-frame `result(input, snr, time_step)` (time_step = exposure seconds)

```rust
fn result(&mut self, input: f64, snr: f64, dt: f64) -> f64 {
    if self.dithering_active {
        self.dither_steps -= 1;
        if self.dither_steps <= 0 { self.dithering_active = false; }
        // feed the model as if dark guiding; on regularize-overrun error: reset()
        match self.deduce_result(dt) { Err(_) => { self.reset(); } _ => {} }
        return self.control_gain * input;          // proportional only while dithering
    }
    if self.n_measurements() == 1 { self.start_clock(); }
    self.handle_guiding(input, snr);               // store measurement + variance

    // hybrid fallback: classic hysteresis (constant 0.1) on the control history
    let last_control = self.second_last_point().control;   // 0 if n <= 1
    let mut hyst = ((1.0 - 0.1) * input + 0.1 * last_control) * self.control_gain;

    let mut control = self.control_gain * input;   // reactive term
    if input.abs() < self.min_move { control = 0.0; hyst = 0.0; }

    if self.n_measurements() > 10 {
        let now = self.seconds_since_start();
        self.update_gp(now + 0.5 * dt);            // max accuracy between now and next frame
        self.prediction = self.predict_gear_error(now + dt);
        control += self.prediction_gain * self.prediction;   // NOTE: bypasses min-move

        // smooth blend from hysteresis to GP over the first N periods
        let p = self.period_length();
        let t = self.last_point().timestamp;
        if t < self.min_periods_for_inference * p {
            let pct = (t / (self.min_periods_for_inference * p)).min(1.0);
            control = pct * control + (1.0 - pct) * hyst;
        }
    }
    if control.is_nan() { control = hyst; }        // user safeguard
    self.add_point();                              // open next data point
    self.last_point().control = control;           // control applies to next interval
    control
}
```

Warm-up (≤ 10 measurements): pure proportional `control_gain * input` with min-move.
**Min-move zeroes only the reactive+hysteresis terms — the GP prediction is always
applied**, which is what keeps PPEC correcting PE below the seeing deadband.

`deduce_result(dt)` (dead reckoning: star lost, paused, dithering): store a dark point
(measurement 0, variance 1e4); output **0** unless `n > 10` AND
`last_timestamp > min_periods_for_inference * P`, in which case output = raw
`predict_gear_error(now + dt)` (prediction gain **not** applied); store as control.

#### 6.8.4 Model update `update_gp(prediction_point)`

1. Take the `N−1` completed points (all but the just-measured current one):
   `gear_error[i] = Σ_{j≤i} control[j] + measurement[i]` — the reconstructed unguided
   worm error in px; heteroscedastic `variances[i]`.
2. **Regularize** onto a fixed 5 s grid (trapezoidal integral per cell / 5.0; grid cell
   center timestamps `cell_start + 2.5`; linear interpolation at cell boundaries).
   Output capped at 2048 cells (`head(j)`). If the running index overflows the grid
   allocation while dithering → throw (caller resets model).
3. **De-trend for FFT only**: ridge linear fit `w = (F·Fᵀ + 1e-3·I)⁻¹ F·y` with
   `F = [1; t]` (LDLT solve); `detrended = y − Fᵀw`.
4. **Period estimation** (only if `compute_period` and
   `last_timestamp > min_periods_for_period_estimation * P`): see 6.8.5; then
   `P ← 0.99·P + 0.01·P_measured` (learning rate 0.01, NaN keeps old); re-set
   hyperparameters (this re-runs the natural→internal conversion).
5. **GP inference, subset-of-data approximation** (`inferSD`): evaluate the full kernel
   between every regularized point and the single prediction point; sort descending by
   covariance; keep the top `points_for_approximation` (**100**) points. (SE kernels
   decay with |Δt| and the periodic kernel repeats, so this selects recent points plus
   points at the same worm phase.) Then exact GP on the subset:
   - Gram `K = k(X,X) + diag(variances)` (+`JITTER = 1e-6` only in the homoscedastic
     path, which PPEC never uses; the GP-level noise parameter is fixed at 1.0 → log 0
     and is irrelevant here).
   - LDLT factorization; `alpha = K⁻¹ y`.
   - Explicit linear trend basis `phi(x) = [1, x]`:
     `feature_matrix = Φ K⁻¹ Φᵀ` (LDLT), `beta = feature_matrix⁻¹ Φ alpha`.

**Kernels.** Inference kernel (PeriodicSquareExponential2), with `d = |t1 − t2|`:

```text
k(d) = σ_SE0² · exp(−d² / (2·l_SE0²))
     + σ_P²   · exp(−2 · sin²(π·d / P) / l_P'²)
     + σ_SE1² · exp(−d² / (2·l_SE1²))
```

Prediction/output-projection kernel (PeriodicSquareExponential) is the **same minus the
SE1 term** — short-term noise is modeled (so it doesn't corrupt the fit) but excluded
from predictions (it is unpredictable).

Hyperparameter handling (`SetGPHyperparameters`): the three length scales are floored at
**1.0** (natural units), all parameters floored at **1e-10**; the periodic length scale
is converted natural→internal by `l_P' = 4·sin(π·l_P / P)` (getter inverts:
`l_P = asin(l_P'/4)·P/π`); everything is stored as `ln(value)` and the kernel evaluates
`ls = exp(θ)`, `σ² = exp(2θ)` — i.e. the user-facing "signal variance" values (20/20/10)
are **amplitudes in px**, squared inside the kernel.

**Prediction** (`predict_gear_error(target)`): posterior mean at the two locations
`[last_prediction_end, target + dither_offset]` using the projection kernel and trend:

```text
m(x*) = k*(x*, X)·alpha + R(x*)ᵀ·beta,   R = phi(x*) − Φ·K⁻¹·k(X, x*)
return m(x1) − m(x0)          // predicted gear-error increment over the interval
last_prediction_end = x1      // (reset to "now" when negative, i.e. after reset;
                              //  set to the measurement timestamp on every real frame)
```

#### 6.8.5 FFT period identification

```text
windowed = detrended .* hamming(n)         // w(x) = 0.54 − 0.46·cos(2πx), x = linspace(0,1,n)
Npad = next_pow2(max(4096, n)); zero-pad
spectrum = |FFT(padded)|²  for k = low..Npad/2,  low = ceil(Npad / n)   // drop padding artifacts
freq_k = k / Npad / dt,    dt = (t_end − t_0)/(n−1)                     // ≈ 5 s grid
zero out amplitudes where period = 1/freq > 1500 s
k* = argmax(spectrum)
if 0 < k* < end:   // 3-point quadratic interpolation (normalized for stability)
    loc = (freq[k*−1..k*+1] − freq[k*]) / spread,  spread = |freq[k*−1] − freq[k*+1]|
    amp = spectrum[k*−1..k*+1] / spectrum[k*]
    if max(amp) − min(amp) ≥ 1e-10:
        fit parabola w2·x² + w1·x + w0 by least squares (3×3 normal equations, LDLT)
        f* = freq[k*] − w1/(2·w2) · spread
return 1 / f*
```

#### 6.8.6 Dither, pause, resume, and model retention

- **Dither** (`GuidingDithered(amt_px)` from the mount, converted in the wrapper):
  `dither_offset += amt / gear_rate` where `gear_rate` = px per second of worm motion at
  1× sidereal: `1000·x_rate / (3600·ra_guide_speed_deg_per_sec / 15)` (ASCOM deg/s;
  falls back to speed multiple 1.0 if unknown). Sets `dithering_active = true`,
  `dither_steps = 10`. While active, `result()` returns proportional-only and feeds dark
  points; settle-done (success) sets `dither_steps = 1` so exactly one more dithering
  pass runs. Rationale: during a dither the control is trusted but the measurement is
  not; the lock-position shift moves the reference by a known amount of *gear time*.
- **`DirectMoveApplied`** (fast-recenter steps): intentionally a no-op (upstream TODO).
- **Reset** clears buffer + GP, re-adds the zero-control seed point, clears dither
  state, `last_prediction_end = −1`.
- **Guiding stopped**: persist current (possibly FFT-adjusted) period length to
  `gp_period_per_kern`; record wall-clock stop time.
- **Guiding started** (`GuidingStarted`): retain the trained model iff pier side is
  unchanged (and known) and RA was known at stop and start and
  `|worm_offset| < retain_pct/100 · P`, where

```text
ra_offset  = norm(prev_ra − cur_ra, −12, 12) · 3600 / 0.9973   // RA hours → SI seconds of gear time
worm_offset = elapsed_wall_seconds + ra_offset
```

  A negative `worm_offset` (worm moved backwards; e.g. slewed east) always forces a
  reset (the model requires monotonically increasing gear time). On retain, the gap is
  injected as a pseudo-dither: `GuidingDithered(ra_offset, 1.0)` (adds to
  `dither_offset` and runs the 10-step dark-guiding window).
- **Guiding disabled** (`block_updates_`): `result()` returns 0 and feeds nothing.
- `dark_tracking_mode_` (debug toggle, default false): reroutes `result()` →
  `deduce_result()`.
- Exceptions from the regularizer during dithering → model reset + proportional output.

---

## 7. Move pipeline & pulse limiting

`Mount::MoveOffset(offset, move_options)` — runs on the worker thread each frame.

```rust
enum MoveOption { AlgoResult = 1, AlgoDeduce = 2, UseBlc = 4, Graph = 8, Manual = 16 }
// combos:
//   CALIBRATION_MOVE = 0
//   GUIDE_STEP     = AlgoResult | UseBlc | Graph
//   DEDUCED_MOVE   = AlgoDeduce | UseBlc | Graph
//   RECOVERY_MOVE  = UseBlc                       (dither fast-recenter)

fn move_offset(ofs: &mut GuiderOffset, opts: u32) -> MoveResult {
    let (mut xd, mut yd);
    if opts & ALGO_DEDUCE != 0 {
        xd = x_algo.deduce_result(); yd = y_algo.deduce_result();
        if xd == 0.0 && yd == 0.0 { return Ok; }
        ofs.mount = (xd, yd);
    } else {
        if !ofs.mount.is_valid() { ofs.mount = camera_to_mount(ofs.camera)?; }
        xd = ofs.mount.x; yd = ofs.mount.y;
        if let Some(blc) = &mut backlash_comp { blc.track_blc_results(opts, yd); } // raw dec offset
        if opts & ALGO_RESULT != 0 {
            xd = x_algo.result(xd);
            yd = y_algo.result(yd);
        }
    }
    let xdir = if xd > 0.0 { West } else { East };   // LEFT / RIGHT
    let ydir = if yd > 0.0 { South } else { North }; // DOWN / UP
    let x_ms = ((xd / x_rate).abs()).round() as i32;     // x_rate: dec-compensated
    let result = move_axis(xdir, x_ms, opts);
    if result != ErrSlewing && result != ErrAoLimit {
        let mut y_ms = ((yd / cal.y_rate).abs()).round() as i32;
        if let Some(blc) = &mut backlash_comp { blc.apply(opts, yd, &mut y_ms); }
        move_axis(ydir, y_ms, opts);
    }
    // record GuideStepInfo for logs/event server (durations actually issued,
    // whether limited, star mass/snr/hfd, avg error)
}
```

`Scope::MoveAxis(direction, duration_ms, opts)` clamps:

- Guiding disabled and not `Manual` → no move.
- **Dec axis** (N/S), for `AlgoResult|AlgoDeduce` moves:
  - `dec_guide_mode == None` → duration = 0.
  - `mode == North` blocks SOUTH; `mode == South` blocks NORTH (uni-directional guiding).
  - clamp to `max_dec_duration` (default **2500** ms, range 50..8000).
- **RA axis** (E/W), for algo moves: clamp to `max_ra_duration` (default **2500** ms).
- If clamped ("limit reached") **5 consecutive times in the same direction**
  (`LIMIT_REACHED_WARN_COUNT`), raise a user alert (suppressible; deferred 120 s after
  certain events).
- Returns actual duration issued and a `limited` flag.

---

## 8. Calibration

### 8.1 Step-size & distance calculator (`CalstepDialog`)

- **Calibration distance** (px): `max(25, ceil(20.0 / image_scale_arcsec))`
  (`DEFAULT_DISTANCE = 25` px ≈ minimum; nominal target 20 arc-sec).
  UI range 10..200 px.
- **Step duration** (ms), for `distance` px, `steps` target steps
  (`DEFAULT_STEPS = 12`, range 6..60), guide speed `g` (× sidereal, default 0.5,
  range 0.1..2.0), declination `δ` (|δ| ≤ 60° enforced by UI):

```text
total_duration_s = distance_px * image_scale / (15.0 * g)      // 15 "/s = sidereal
pulse_ms  = total_duration_s / steps * 1000                     // at dec = 0
max_pulse = total_duration_s / 6 * 1000                         // keep >= 6 steps
pulse_ms  = min(max_pulse, pulse_ms / cos(δ))
step_ms   = ceil(pulse_ms / 50) * 50                            // round UP to 50 ms
```

- Default step duration if never calculated: **750 ms** (`DefaultCalibrationDuration`).
- At calibration start, `CheckCalibrationDuration` recomputes distance on binning change
  and recomputes step size if binning changed or reported RA guide speed changed > 5%.

### 8.2 State machine (`Scope::UpdateCalibrationState`, one step per frame)

States: `GO_WEST → GO_EAST → CLEAR_BACKLASH → GO_NORTH → GO_SOUTH → NUDGE_SOUTH →
COMPLETE`. `MAX_CALIBRATION_STEPS = 60` per measured leg. Calibration moves use
`MOVEOPTS_CALIBRATION_MOVE = 0` (no algorithms, no BLC, no dec-mode gating? — note:
dec-mode gating only applies to algo moves, so calibration always moves both axes).

**GO_WEST** (measures RA):

```text
repeat: pulse WEST for calibration_duration until
        dist(start, current) >= calibration_distance
        (fail after 60 steps: "RA Calibration Failed: star did not move enough")
x_angle = angle(start → current)                 // atan2 in camera frame
x_rate  = dist / (steps * calibration_duration)  // px per ms
ra_parity: compare mount-reported RA before/after (if pointing info available):
    ΔRA < −1 arcsec (in hours: 24/(360·60·60)) → ParityEven   (west decreases RA)
    ΔRA > +1 arcsec → ParityOdd; else Unknown
```

**GO_EAST** (re-center, also sanity check):

```text
remaining_ms = steps * calibration_duration
recenter_duration = fast_recenter_enabled
    ? clamp(floor(max_move_px / x_rate), calibration_duration, max_ra_duration)
    : calibration_duration
pulse EAST in chunks of recenter_duration until remaining_ms == 0
if !can_pulse_guide (ST-4):   // cable check
    require east_dist >= 0.25 * west_dist AND
            |norm(east_angle − (x_angle + π))| <= 30°, else alert
```

**CLEAR_BACKLASH** (Dec; skipped entirely if `dec_guide_mode == None`, in which case
`y_angle = norm(x_angle + π/2)`, `y_rate = CALIBRATION_RATE_UNCALIBRATED (1.0)`, dec
parity unknown, calibration completes):

```text
expected_step_px = x_rate * calibration_duration * 0.6
if mount reports guide rates and RA != Dec rate:
    expected_step_px *= dec_speed / ra_speed
max_pulses = max(8, 60000 / calibration_duration)      // BL_MAX_CLEARING_TIME = 60 s
goal: 3 consecutive NORTH moves each >= expected_step_px with no direction
      reversal (BL_BACKLASH_MIN_COUNT = 3)
loop: pulse NORTH calibration_duration;
      delta = dist(marker, current); cum = dist(leg_start, current)
      delta >= expected && (first || cum > last_cum) → accepted += 1
      delta >= expected && cum <= last_cum          → accepted = 0   (reversal)
      delta <  expected && cum <  last_cum          → accepted = 0
      stop when accepted == 3, or pulses == max_pulses, or cum >= calibration_distance
on exhaustion: if cum >= 3 px (BL_MIN_CLEARING_DISTANCE) proceed anyway
               else FAIL "Backlash Clearing Failed: star did not move enough"
on success: the last clearing move becomes NORTH calibration step #1
            (leg start = marker point before that move)
```

**GO_NORTH** (measures Dec): same loop as GO_WEST (60-step limit, fail message
"DEC Calibration Failed"). Completion math:

```text
measured_angle = angle(current → leg_start)      // note reversed endpoints vs RA
if assume_orthogonal (config, default false):
    a1 = norm(x_angle + π/2); a2 = norm(x_angle − π/2)
    y_angle = whichever of a1/a2 is closer (min |norm(a − measured_angle)|)
    dec_dist = dist * cos(measured_angle − y_angle)   // project onto chosen axis
    y_rate = dec_dist / (steps * calibration_duration)
else:
    y_angle = measured_angle
    y_rate  = dist / (steps * calibration_duration)
dec_parity: Δdec > +1 arcsec → ParityEven (north increases dec);
            Δdec < −1 arcsec → ParityOdd; else Unknown
```

**GO_SOUTH**: re-center with `recenter_duration = clamp(floor(0.8 * max_move_px /
y_rate), calibration_duration, max_dec_duration)`; then south-retrace advisory:
`south_dist < 0.25 * north_dist` or angle off by > 30° ⇒ log advisory (alert text
exists but is currently not shown — code path commented out).

**NUDGE_SOUTH** (return to start): up to `MAX_NUDGES = 3` extra SOUTH pulses to bring
the star within `NUDGE_TOLERANCE = 2.0` px of the initial location:

```text
nudge_amt = dist(current, initial_location)
direction check: angle between (current→initial) and the north-move direction
                 cosine vector must be within 40° of 180° — else stop nudging
size check:  NUDGE_TOLERANCE < nudge_amt < calibration_distance + backlash_clearing_dist
dec_amt = mount_coords(current − initial).y     // must have same sign as prior south moves
pulse = min(floor(min(|dec_amt|, max_move_px) / y_rate), calibration_duration)
```

**COMPLETE**: stamp calibration with current declination (radians), pier side, rotator
angle, binning; persist; run sanity checks (8.3). Note `m_yAngleError =
norm(x_angle − y_angle + π/2)` is derived on `SetCalibration`.

### 8.3 Post-calibration sanity checks (alert only, first failure wins)

1. **Steps**: `ra_steps < 4` or (`0 < dec_steps < 4`) → "few steps" (CAL_ALERT_MINSTEPS=4).
2. **Orthogonality**: `ortho_err_deg = |norm(x_angle − y_angle)| deviation from 90°`;
   alert if > **12.5°** (`CAL_ALERT_ORTHOGONALITY_TOLERANCE`).
3. **Rates vs cos(dec)** (only if dec known, dec-guiding calibrated, |dec| ≤ 60°, dec
   comp enabled): `expected = cos(dec)`; `actual = x_rate * (dec_speed/ra_speed) /
   y_rate`; alert if `|expected − actual| > 0.20`.
4. **Different from last calibration** (same config: image scale within 0.1 "/px and
   x_angle within 5°): alert if `|1 − old_y_rate/new_y_rate| > 0.20` (usually backlash).

### 8.4 Calibration data model

```rust
struct Calibration {
    x_rate: f64, y_rate: f64,        // px/ms (y_rate == 1.0 sentinel: dec uncalibrated)
    x_angle: f64, y_angle: f64,      // radians, camera frame
    declination: f64,                // radians; UNKNOWN_DECLINATION = 997.0 sentinel
    pier_side: PierSide,             // East / West / Unknown
    ra_guide_parity: Parity, dec_guide_parity: Parity, // Even / Odd / Unknown
    rotator_angle: f64, binning: u16, is_valid: bool,
}
```

---

## 9. Calibration adjustments at guide start

`Mount::AdjustCalibrationForScopePointing()` runs when guiding starts:

1. **Guide-speed change alert**: if stored calibration guide speeds exist and current
   reported speeds differ by > 5% on either axis → unavoidable alert.
2. **Pixel-size mismatch**: |camera-reported pixel size − profile pixel size| ≥ 1.0 µm →
   alert; scale ratio noted; if net scale change ≥ 1% → calibration cleared.
3. **Binning change**: rates rescaled `rate *= old_binning / new_binning`, stored.
4. **Pier-side flip** (`FlipCalibration`, when current side is opposite the calibration
   side and both known):
   - `x_angle += π` (normalized).
   - `y_angle += π` only if the mount declares `CalibrationFlipRequiresDecFlip`
     (config `/scope/CalFlipRequiresDecFlip`, default false — e.g. mounts that
     don't reverse dec output after flip).
   - dec parity flips, unless dec-flip was required (then it stays).
   - RA parity never changes.
5. **Rotator**: if rotator position known now and at calibration, and Δ > 0.05°:
   `x_angle -= Δ; y_angle -= Δ` (radians, normalized). If it was unknown at
   calibration → alert "recalibration needed".
6. **Declination compensation** (RA only, never persisted; enabled by
   `/scope/UseDecComp`, default true, and mount `DecCompensationEnabled`):
   - Skip if calibration dec unknown or current dec unknown.
   - If `|cal.declination| > 60°` (`DEC_COMP_LIMIT = π·⅓`): alert "calibration too far
     from equator", no comp.
   - Else clamp current dec to ±89° and set
     `x_rate_effective = cal.x_rate / cos(cal.declination) * cos(current_dec)`.
   - Without comp, `x_rate_effective = cal.x_rate`.

`Scope::IsCalibrated()` additionally requires `y_rate != 1.0` sentinel unless dec guide
mode is Off.

---

## 10. Backlash compensation

Dec-only. Adds a fixed extra pulse when the dec guide direction reverses, with an
adaptive size controller. **Only allowed in DEC_AUTO mode** (disabled automatically when
switching to Off/North/South, because uni-directional guiding can't recover overshoot).

Constants: `MIN_COMP_AMOUNT = 20` ms, `MAX_COMP_AMOUNT = 8000` ms, history depth 10
events, 3 entries per event.

### 10.1 Applying the pulse

```rust
fn apply(&mut self, opts: u32, y_guide_dist_px: f64, y_ms: &mut i32) {
    if opts & USE_BLC == 0 { return; }
    if !self.active || self.pulse_ms <= 0 || y_guide_dist_px == 0.0 { return; }
    let dir = if y_guide_dist_px > 0.0 { South } else { North };
    let is_algo = opts & ALGO_RESULT != 0;
    if self.last_dir != None && dir != self.last_dir {
        *y_ms += self.pulse_ms;                       // the compensation itself
        if is_algo { self.history.record_new_blc(now, y_guide_dist_px); } // open window
        else       { self.history.close_window(); }
    }
    self.last_dir = dir;
}
```

The BLC pulse-size setter clamps to [0, 8000]; floor defaults to 20 ms; ceiling defaults
to `min(1.5 × pulse, 8000)` when unset/invalid; `fixed_size` when
`|ceiling − floor| < 20 ms` (adaptive controller disabled). If the pulse is larger than
`max_dec_duration`, `max_dec_duration` is raised to match. A manual change > 100 ms
clears history.

### 10.2 Tracking results (adaptive controller)

Every guide frame, **before** the algorithms see the dec offset, the raw dec offset is
fed to `track_blc_results`:

```rust
fn track_blc_results(&mut self, opts: u32, y_raw_px: f64) {
    if !self.active { return; }
    if opts & USE_BLC == 0 { self.reset_state(); return; }   // calibration-type move
    if opts & ALGO_RESULT == 0 { self.history.close_window(); return; }
    if !self.history.window_open() || self.fixed_size { return; }
    // miss sign: + means we needed MORE same-direction correction (under-shoot),
    //            − means we overshot (direction now opposite last commanded)
    let dir = if y_raw_px > 0.0 { South } else { North };
    let miss = if dir == self.last_dir { y_raw_px.abs() } else { -y_raw_px.abs() };
    let min_move = f64::max(dec_algo.get_min_move(), 0.0);
    self.history.add_deflection(now, miss, min_move);   // up to 2 follow-ons per event
    if let Some(adj) = self.history.adjustment_needed(miss, min_move, cal.y_rate) {
        let nominal = self.pulse_ms as f64 + adj;       // adj in ms, signed
        let new = if nominal > self.pulse_ms as f64 {
            min(self.pulse_ms as f64 * 1.1, nominal).round().min(self.ceiling as f64)
        } else {
            max(self.pulse_ms as f64 * 0.8, nominal).round().max(self.floor as f64)
        };
        self.set_pulse(new as i32);
    }
}
```

Event bookkeeping: each BLC event stores the triggering deflection plus up to 2
follow-on deflections. From follow-on #1 (if |amt| > min_move): `initial_undershoot`
(amt>0) or `initial_overshoot` (amt<0). Follow-on #2 flags **stiction** when it is
negative while follow-on #1 was positive (mount stuck then broke free).

`adjustment_needed(miss, min_move, y_rate)` over the last 10 events
(`avg_init_miss` = mean of follow-on #1 across events; `corr_ms =
round(|avg_init_miss| / y_rate)`):

- `|miss| < min_move` → no change.
- **Under-shoot** (`miss > 0`): require `avg_init_miss > 0`, current event complete
  (3 entries), `stiction_count ≤ 2`, `overshoot_count < 2` → **increase** by `corr_ms`.
- **Over-shoot** (`miss < 0`):
  - current event has stiction and `stiction_count > 1` → **decrease** by
    `round(|avg_stiction| / y_rate)`; forget oldest stiction event.
  - else `overshoot_count > shortfall_count` and ≥ 5 events → **decrease** by
    `corr_ms`; forget 2 oldest overshoot events.
  - else `avg_init_miss ≤ −0.1` → **decrease** by `corr_ms`.
  - else close window, no change.
- Any adjustment closes the window (stop tracking this event).

### 10.3 Backlash measurement tool (`BacklashTool`)

Guided assistant that measures the seed backlash pulse (feeds §10.1's `pulse_ms`).
Invoked from the Guiding Assistant; runs one step per guide frame. Not required for guiding
parity, but AstroDeck needs it to *populate* the BLC pulse (PHD2 ships BLC disabled with
pulse 0). All moves use `MOVEOPTS_CALIBRATION_MOVE` (raw pulses, no algorithms, no BLC).

Constants (`backlash_comp.h:101-107`):

```text
BACKLASH_MIN_COUNT        = 3       // consecutive good north clearing moves required
BACKLASH_EXPECTED_DISTANCE = 4  px  // a north move must reach this to count as "moved"
BACKLASH_EXEMPTION_DISTANCE = 40 px // cumulative north travel that lets a bad-cal mount skip clearing
MAX_CLEARING_STEPS        = 100     // clearing-pulse cap
NORTH_PULSE_SIZE          = 500 ms  // floor for the measurement north pulse width
MAX_NORTH_PULSES          = 8000 ms // ~8 s total north travel target
TRIAL_TOLERANCE_AS        = 2  arcsec // acceptable residual after the trial south correction
```

Inputs: `drift_per_sec = drift_per_min / 60` (sky drift the GA measured beforehand, used to
de-trend the north/south runs); `y_rate = last calibration yRate` (px/ms, must be > 0 or the
tool aborts with "re-run calibration"). All positions are transformed camera→mount and only
the **mount-frame Y (dec)** component is used.

State machine (`BacklashTool::DecMeasurementStep`, `backlash_comp.cpp:881-1259`):

```text
INITIALIZE:
    marker = start = current_mount_pos
    pulse_width = BACKLASH_EXPECTED_DISTANCE * 1.25 / y_rate     // clearing pulse, ms
    accepted_moves = 0; cum_clearing = 0; exemption = false
    -> CLEAR_NORTH  (enable guiding + measurement mode)

CLEAR_NORTH:  // clear dec backlash: want BACKLASH_MIN_COUNT (3) consecutive north moves
              // each >= BACKLASH_EXPECTED_DISTANCE px with no reversal
    dec_delta = current.Y - marker.Y;  cum_clearing += dec_delta   // signed
    first frame: pulse NORTH pulse_width, step=1, return
    if |dec_delta| >= 4:
        if accepted==0 OR sign matches last accepted -> accepted++
        else accepted = 0                                          // reversal resets
    if accepted < 3:
        if step < MAX_CLEARING_STEPS:
            if |cum_clearing| > BACKLASH_EXEMPTION_DISTANCE (40): exemption = true  // bad cal, proceed
            else if not OutOfRoom(margin = max_move_px):
                pulse NORTH pulse_width; step++; marker = current; last_rslt = dec_delta; return
        else: FAIL "Could not clear North backlash" (MEASUREMENT_BL_NOT_CLEARED)
    if accepted >= 3 OR exemption OR OutOfRoom:
        // switch to measurement pulse width:
        total_cleared = step * pulse_width
        pulse_width = clamp(max(NORTH_PULSE_SIZE, calibration_duration),
                            .., floor(0.7 * max_move_px / y_rate))  // >=500 ms, <=70% of ROI
        north_pulse_count = max( ceil(MAX_NORTH_PULSES / pulse_width),   // >= ~8 s total
                                 total_cleared * 1.5 / pulse_width )     // or 1.5x cleared
        msmt_start = now();  step = 0
        -> STEP_NORTH (fall through)

STEP_NORTH:  // record dec position after each measured north pulse
    if step < north_pulse_count AND not OutOfRoom:
        if step >= 1: northStats.add(step, current.Y - north_steps.back())
        else marker = current
        north_steps.push(current.Y);  pulse NORTH pulse_width;  step++;  return
    else:  // done or ran out of room
        msmt_end = now()
        if step >= 1: northStats.add(step, current.Y - north_steps.back())
        north_steps.push(current.Y)
        if step < north_pulse_count:
            if step < 0.5*north_pulse_count: FAIL "Star too close to edge" (TOO_FEW_NORTH)
            // else just truncate
        north_pulse_count = step;  step = 0
        -> STEP_SOUTH (fall through)

STEP_SOUTH:  // retrace the same number of south pulses
    if step < north_pulse_count:
        south_steps.push(current.Y);  pulse SOUTH pulse_width;  step++;  return
    south_steps.push(current.Y);  end_south = current
    -> TEST_CORRECTION (fall through)

TEST_CORRECTION:
    if step == 0:
        rslt = ComputeBacklashPx(&bl_px, &bl_ms, &north_rate)   // see below
        on MEASUREMENT_SANITY  -> FAIL "Dec movements too erratic"
        on MEASUREMENT_TOO_FEW_SOUTH -> FAIL "never established consistent south moves"
        on MEASUREMENT_TOO_FEW_NORTH -> keep going, flag result inaccurate in UI
        if bl_ms > 0:
            if bl_px < max_move_px:  pulse SOUTH bl_ms; step++; return   // trial correction
            else: pulse SOUTH floor(0.8*max_move_px/north_rate); -> RESTORE  // too big to fine-tune
        else: -> RESTORE
    // evaluate trial: tol = TRIAL_TOLERANCE_AS / pixel_scale
    // log over/under-shoot (target_delta/pulse_delta); no adaptive change is actually applied here
    -> RESTORE

RESTORE:  // walk the star back near the starting dec without losing it
    if step == 0:
        amt = |current.Y - start.Y|
        if amt > max_move_px: restore_count = min(floor((amt/north_rate)/pulse_width), 10)
        else -> WRAPUP
    if step < restore_count: pulse SOUTH pulse_width; step++; return
    -> WRAPUP

WRAPUP:  CleanUp() (reset BLC state, leave measurement mode) -> COMPLETED
ABORTED: CleanUp(); status "Measurement halted"  // set by StopMeasurement() or any thrown error
```

`OutOfRoom(margin)` = star camera position within `margin` px of any frame edge
(`margin = guider.GetMaxMovePixels()`, i.e. the search region).

`ComputeBacklashPx` (`backlash_comp.cpp:791-879`) — the actual backlash estimate:

```rust
if north_steps.len() <= 3 { return TOO_FEW_NORTH; }
let drift_px   = drift_per_sec * (msmt_end - msmt_start) / 1000.0;      // total drift over north run
let step_count = northStats.count();
let north_delta = northStats.sum();
let north_rate  = ((north_delta - drift_px) / (step_count * pulse_width)).abs();  // px/ms, drift-corrected
let drift_per_frame = drift_px / step_count;
let expected = 0.9 * northStats.median();     // 90% of median north step
let expected_mag = expected.abs();
let (mut good, mut last_south, mut smoothing, mut early_south) = (0, 0.0, false, 0.0);
let mut bl_px = 0.0;
for step in 1..south_steps.len() {
    let south_move = south_steps[step] - south_steps[step-1];
    early_south += south_move;
    // count a "good" south move: negative (southward) and big enough, allowing a
    // short-long smoothing average with the previous move:
    if south_move < 0.0 && (south_move.abs() >= expected_mag
                            || (south_move + last_south/2.0).abs() > expected_mag) {
        if south_move.abs() < expected_mag { smoothing = true; }
        good += 1;
        if good == 2 {   // two consecutive good south moves = mount is moving; measure the lag
            bl_px = step as f64 * expected_mag
                    - (early_south - step as f64 * drift_per_frame).abs();  // drift-corrected
            rslt = if bl_px * north_rate < -200.0 { MEASUREMENT_SANITY }     // wild negative
                   else if bl_px >= 0.7 * north_delta { MEASUREMENT_TOO_FEW_NORTH }
                   else { MEASUREMENT_VALID };
            if bl_px < 0.0 { bl_px = 0.0; }     // clamp small negatives to zero
            break;
        }
    } else if good > 0 { good -= 1; }
    last_south = south_move;
}
if good < 2 { rslt = MEASUREMENT_TOO_FEW_SOUTH; }
bl_ms = (bl_px / north_rate) as i32;      // the seed pulse written to DecBacklashPulse
```

Sigma (`GetBacklashSigma`, `backlash_comp.cpp:1261`): `sigma_px = sqrt(var/count + 2·var/(count−1))`
(north sample-mean sigma plus two south measurements in quadrature), `sigma_ms = sigma_px / north_rate`.
`northStats` is a windowed descriptive-stats accumulator (mean/variance/median/sum over the north steps).

Result codes: `MEASUREMENT_VALID`, `MEASUREMENT_TOO_FEW_NORTH` (result usable but flagged
low-confidence), `MEASUREMENT_TOO_FEW_SOUTH`, `MEASUREMENT_BL_NOT_CLEARED`, `MEASUREMENT_SANITY`.

---

## 11. Dither & fast recenter

### 11.1 Dither offset generation (`MyFrame::Dither`)

- `amount_px *= dither_scale_factor` (config `/DitherScaleFactor`, default **1.0**).
- Mode `/DitherMode`: **Random (default)** or Spiral.
  - Random: `d_ra = amount * uniform(−1, 1)`; `d_dec = ra_only ? 0 : amount * uniform(−1, 1)`.
  - Spiral: square spiral on lattice; state `(x, y, dx, dy)`, start `(0,0,−1,0)`:

```rust
fn rot(dx: &mut i32, dy: &mut i32) { let t = -*dx; *dx = *dy; *dy = t; }
// full 2-D spiral step:
if x == y || (x > 0 && x == -y) || (x <= 0 && y == 1 - x) { rot(&mut dx, &mut dy); }
x += dx; y += dy;
d_ra = dx as f64 * amount;  d_dec = dy as f64 * amount;
// RA-only variant walks x through 0,1,-1,-2,2,3,-3,-4,4,... ; d_dec = 0
```

  Spiral state resets when guiding starts or when switching raOnly mode.
- `ra_only` from `/DitherRaOnly` (default false), forced true if dec guide mode makes
  dec dithering impossible (see §12 note).
- The dither is applied by **moving the lock position** (star physically drifts to it):
  `MoveLockPosition(mount_delta)`.

### 11.2 `MoveLockPosition` (mount-frame delta in px)

- Tries the 4 sign combinations `(±dx, ±dy)` of the requested mount delta; converts each
  to camera coords; first one that keeps the lock position valid
  (`search_region+1` inside frame) wins. If none valid, picks the one whose resulting
  point is farthest from the nearest edge.
- Adds the camera delta to the lock position; guide algorithms get
  `GuidingDithered(mount_dx)/(mount_dy)` → **reset()** for the standard algorithms.
- Immediately inflates the error statistics by the dither distance (so settle logic sees
  the displacement at once).
- **Fast recenter** (config `/guider/FastRecenter`, default **true**), if dither
  distance > 0:

```rust
recenter_remaining = (|mount_dx|, |mount_dy|);
recenter_dir = (-sign(mount_dx), -sign(mount_dy));
f = 0.7 * max_move_px / hypot(remaining);        // max_move_px == search_region
recenter_step = (f * remaining.x, f * remaining.y);
```

Then, each guiding frame while `recenter_remaining` is valid:
`step = min(remaining, step_size)` per axis; issue as `MOVEOPTS_RECOVERY_MOVE`
(bypasses guide algorithms; BLC still applies; algorithms are notified via
`DirectMoveApplied`); `remaining -= step`; done when both axes < 0.5 px → reset the
avg-distance filter.

---

## 12. Settling

`PhdController` settle logic — used for both dither and guide-start settling.

```rust
struct SettleParams {
    tolerance_px: f64,      // "pixels": error must be <= this
    settle_time_sec: i32,   // "time": continuous seconds within tolerance
    timeout_sec: i32,       // "timeout": overall deadline
    frames: i32,            // max frames; event-server default 99999
}
// SETTLING_TIME_DISABLED = 9999 (legacy dither uses time/timeout = 9999, frames = 10)
```

Per guide frame while settling (`STATE_SETTLE_WAIT`):

```rust
let locked = guider.is_locked();
let err = current_guide_error();          // avgDistance; 100.0 if star lost > 20 s
let in_range = locked && err <= p.tolerance_px;
frame_count += 1;
if !locked { dropped_count += 1; }
if frame_count >= p.frames { succeed(); }
if in_range {
    if !prior_in_range {
        if p.settle_time_sec <= 0 { succeed(); }
        in_range_timer.restart();          // first in-range frame
    } else if in_range_timer.secs() >= p.settle_time_sec && !ao_bump_in_progress {
        succeed();
    }
}
if total_timer.secs() >= p.timeout_sec { fail("timed-out waiting for guider to settle"); }
prior_in_range = in_range;                 // any out-of-range frame restarts the clock
```

Notes:

- The in-range timer restarts whenever a frame goes out of range (via
  `prior_in_range = false` and restart on the next in-range frame).
- Multi-star `RefineOffset` is disabled while settling; the DistanceChecker also treats
  settling frames as automatically acceptable.
- **Dec-mode override for dither**: if dec guide mode is North or South (uni-directional)
  and settling is enabled, PHD2 temporarily sets DEC_AUTO for the settle and restores
  after; if mode is Off (or no settle), the dither is forced RA-only.
- Settle completion notifies mounts → algorithms `GuidingDitherSettleDone(success)`.

---

## 13. Guide-error statistics

`Guider::UpdateCurrentDistance(distance, distance_ra)` per accepted frame:

```rust
// fast EMA (used for settling / "CurrentGuideError"):
const ALPHA: f64 = 0.3;
avg_dist    += ALPHA * (dist    - avg_dist);
avg_dist_ra += ALPHA * (dist_ra - avg_dist_ra);
// slow EMA (used by DistanceChecker tolerance):
count += 1;
if count < 10 {                       // seed with running mean of first 10
    avg_long    += (dist - avg_long) / count as f64;
    avg_long_ra += (dist_ra - avg_long_ra) / count as f64;
} else {
    const ALPHA_LONG: f64 = 0.045;    // ~15-frame half-life
    avg_long    += ALPHA_LONG * (dist - avg_long);
    avg_long_ra += ALPHA_LONG * (dist_ra - avg_long_ra);
}
// not guiding yet → all four set to the sample; counter = 1
// "need reset" flag (set after fast-recenter completes / resume from pause /
// guiding starts) → same reinitialization
```

`current_error()` returns `avg_dist` (or the RA variant when dec guiding is off), except
returns **100.0** (`LARGE_DISTANCE`) when no star has been found for > 20 s
(`THRESHOLD_SECONDS`) or never.

---

## 14. RA vs Dec asymmetries

| Aspect | RA (x) | Dec (y) |
|---|---|---|
| Default algorithm | Hysteresis (agg 0.7, hys 0.1) | ResistSwitch (agg 1.0, fast-switch on) |
| Rate used for ms conversion | `x_rate` (dec-compensated live) | `cal.y_rate` (fixed) |
| Dec compensation | `x_rate = cal.x_rate·cos(dec_now)/cos(dec_cal)` | n/a |
| Guide mode gating | none | Off / Auto / North-only / South-only |
| Backlash compensation | none | direction-reversal pulse + adaptive size |
| Calibration | GO_WEST measures; GO_EAST re-centers | backlash clearing first; GO_NORTH measures; GO_SOUTH + nudges re-center |
| Uncalibrated sentinel | never | `y_rate == 1.0` when dec guiding disabled during cal |
| Pier flip | x_angle += π; parity unchanged | parity flips (unless dec-flip mounts); y_angle += π only for dec-flip mounts |
| Dither | always dithers RA | dec dithers only in Auto (or temp-override North/South during settle) |
| Max pulse defaults | 2500 ms | 2500 ms (raised automatically if BLC pulse exceeds it) |

---

## 15. Configuration knobs

| Knob (PHD2 config path) | Default | Range / notes |
|---|---|---|
| Search region `/guider/onestar/SearchRegion` | 15 px | 7..50 |
| Star mass change enabled `/guider/onestar/MassChangeThresholdEnabled` | true (thr≠1.0) | bool |
| Star mass change threshold `/guider/onestar/MassChangeThreshold` | 0.5 | ≥ 0 |
| Tolerate jumps enabled/threshold `/guider/onestar/TolerateJumps*` | false / 4.0 | px multiple of smoothed error |
| Multi-star enabled `/guider/multistar/enabled` | false (UI wizard sets true) | bool |
| Max stars used (RefineOffset) | 9 | fixed constant |
| Candidate list size | 12 | fixed constant |
| Min star HFD `/guider/StarMinHFD` | 1.5 px | floor 0.1 |
| Max star HFD `/guider/StarMaxHFD` | 20.0 px | |
| Auto-find min SNR `/guider/StarMinSNR` | 6.0 | |
| Auto-select downsample `/guider/AutoSelDownsample` | 0 (auto) | 0..3; auto: 2× if scale ≤ 0.6″ |
| Fast recenter `/guider/FastRecenter` | true | bool |
| Hysteresis: minMove / hysteresis / aggression | 0.2 / 0.1 / 0.7 | ≥0 / 0..0.99 / 0..2.0 |
| ResistSwitch: minMove / aggression / fastSwitch | 0.2 / 1.0 / true | >0 / 0..1 / bool |
| Lowpass: minMove / slopeWeight | 0.2 / 5.0 | ≥0 / ≥0 |
| Lowpass2: minMove / aggressiveness | 0.2 / 80 (%) | ≥0 / 0..100 |
| ZFilter: minMove / expFactor | 0.1 / 2.0 | ≥0 / 1.0..20.0 |
| Smart min-move formula | max(0.1515 + 0.1548/scale, 0.15) | fallback 0.2 |
| Calibration step `/scope/CalibrationDuration` | 750 ms | > 0; calculator: §8.1 |
| Calibration distance `/scope/CalibrationDistance` | 25 px | 10..200; 20″ nominal |
| Calibration target steps `/CalStepCalc/NumSteps` | 12 | 6..60 |
| Max RA duration `/scope/MaxRaDuration` | 2500 ms | 50..8000 |
| Max Dec duration `/scope/MaxDecDuration` | 2500 ms | 50..8000 |
| Dec guide mode `/scope/DecGuideMode` | Auto | Off/Auto/North/South |
| Assume orthogonal `/scope/AssumeOrthogonal` | false | bool |
| Dec compensation `/<mount>/UseDecComp` | true | bool; limit 60° |
| Cal-flip-requires-dec-flip `/scope/CalFlipRequiresDecFlip` | false | bool |
| BLC enabled `/<mount>/BacklashCompEnabled` | false | needs measured pulse |
| BLC pulse / floor / ceiling `/<mount>/DecBacklash{Pulse,Floor,Ceiling}` | 0 / 20 / 1.5×pulse | 20..8000 ms |
| Dither scale `/DitherScaleFactor` | 1.0 | |
| Dither RA-only `/DitherRaOnly` | false | |
| Dither mode `/DitherMode` | Random | Random/Spiral |
| Settle params (per request) | tol px, time s, timeout s | frames default 99999 |
| Calibration sanity: min steps / ortho / rate ratios | 4 / 12.5° / 0.20 | alert thresholds |

---

## 16. Source map

All paths relative to `references/phd2/src/`, at commit `4a13cf24`.

| Algorithm / section | File : lines |
|---|---|
| §1 Star find, background, SNR, HFR | `star.cpp:126-483` (Find), `star.cpp:83-124` (hfr), `star.cpp:41-70` (state) |
| §2 AutoFind pipeline | `star.cpp:718-1154`; PSF kernel `star.cpp:574-656`; downsample `star.cpp:658-680`; stats `star.cpp:515-544` |
| §3 MassChecker | `guider_multistar.cpp:52-180` |
| §3 DistanceChecker | `guider_multistar.cpp:601-704` |
| §3 UpdateCurrentPosition | `guider_multistar.cpp:925-1060` |
| §3 lost-star / paused dead-reckoning | `guider.cpp:1340-1359, 1382-1393` |
| §4 RefineOffset, stabilization | `guider_multistar.cpp:706-921`; GuideStar fields `star.h:103-117` |
| §5 Transforms | `mount.cpp:1135-1223`; `y_angle_error` `mount.cpp:1570`; MountCoords helper `scope.cpp:1163-1171` |
| §6.1 Hysteresis | `guide_algorithm_hysteresis.cpp:42-91` (constants+result), 93-167 (setters) |
| §6.2 ResistSwitch | `guide_algorithm_resistswitch.cpp:42-177`; HISTORY_SIZE `guide_algorithm_resistswitch.h:45` |
| §6.3 Lowpass | `guide_algorithm_lowpass.cpp:42-103`; `guide_algorithm_lowpass.h:47` |
| §6.4 Lowpass2 | `guide_algorithm_lowpass2.cpp:42-123`; `guide_algorithm_lowpass2.h:47`; linear fit `guiding_stats.cpp:524-575`; windowing `guiding_stats.cpp:577-693` |
| §6.5 ZFilter runtime | `guide_algorithm_zfilter.cpp:36-114` (result), 116-167 (BuildFilter) |
| §6.5 mkfilter port + Bessel poles | `zfilterfactory.cpp:50-260`; `zfilterfactory.h:42-115` |
| §6 base class (deduce, smart min-move, dither reset) | `guide_algorithm.cpp:42-134`; `guide_algorithm.h:85,104` |
| §7 MoveOffset | `mount.cpp:978-1087`; move options `mount.h:127-140`; directions `mount.h:46-56` |
| §7 MoveAxis clamps & alerts | `scope.cpp:571-816` |
| §8.1 Calc distance/step | `calstep_dialog.cpp:206-241`; defaults `calstep_dialog.h:73-78`, `calstep_dialog.cpp:41-50` |
| §8.2 Calibration state machine | `scope.cpp:1202-1784`; BeginCalibration `scope.cpp:1050-1087`; constants `scope.cpp:44-69`, `scope.h:124-126` |
| §8.3 Sanity checks | `scope.cpp:868-985` |
| §8.4 SetCalibration persistence | `mount.cpp:1544-1648` |
| §9 AdjustCalibrationForScopePointing | `mount.cpp:1253-1409`; FlipCalibration `mount.cpp:891-957`; CheckCalibrationDuration `scope.cpp:995-1048` |
| §10 BLC apply/track/adjust | `backlash_comp.cpp:41-583` (history model 44-369, comp 371-583) |
| §10.3 BacklashTool measurement | `backlash_comp.cpp:722-1293` (state machine 881-1259, ComputeBacklashPx 791-879, sigma 1261-1277); constants `backlash_comp.h:99-107` |
| §11 Dither generation | `myframe.cpp:2018-2137` (spiral 2018-2067); defaults `myframe.cpp:59-61` |
| §11 MoveLockPosition + fast recenter | `guider.cpp:838-929`; recenter consumption `guider.cpp:1484-1512` |
| §12 Settle state machine | `phdcontrol.cpp:40-582` (settle wait 514-567); SettleParams `phdcontrol.h:38-44`; event-server parsing `event_server.cpp:1618-1651` |
| §13 avgDistance EMAs | `guider.cpp:1065-1139`; dither inflation `guider.cpp:903-908` |
| Guider profile defaults | `guider.cpp:217-252`; `guider.h:418-436`; `guider_multistar.cpp:182-192, 259-278` |
| Guide loop dispatch | `guider.cpp:1261-1553` |

---

## 17. Recommended for AstroDeck

**Adopt as default (parity with stock PHD2 behavior, well-proven):**

- **Star measurement**: implement `Star::Find` exactly as §1 (A=7/B=12 annulus, 2σ
  iterative clip, 3σ threshold, Simonetti SNR with gain 0.5, HFR interpolation, both
  saturation paths). This is the whole foundation — get it bit-accurate first and unit
  test against synthetic Gaussians + hot pixels.
- **Selection**: §2 auto-find with multi-star ON by default (candidate list 12, use 9),
  since AstroDeck has no legacy single-star users. Keep the SNR-weighted refinement of
  §4 unchanged — the guards (zero-count, miss-count, stabilization) are load-bearing.
- **Algorithms**: RA = Hysteresis (0.7 / 0.1), Dec = ResistSwitch (1.0, fast-switch on),
  min-move from the smart formula. These are PHD2's defaults for good reason: RS makes
  dec guiding robust to backlash without modeling it.
- **Calibration**: full §8 state machine including backlash clearing, `assume_orthogonal
  = false`, sanity checks as advisories surfaced through AstroDeck notifications. Use
  the §8.1 calculator (20″ or 25 px, 12 steps, round-up-to-50 ms) driven by Alpaca
  `GuideRateRightAscension/Declination` when available.
- **Runtime adjustments**: dec compensation ON (cos-dec RA rate scaling), pier-flip
  handling via SideOfPier, guide-speed-change detection. These remove the most common
  "guiding broke after meridian flip" failures.
- **Dither**: random mode, scale 1.0, with fast recenter (0.7 × search region steps) and
  the §12 settle machine — the JSON settle params map 1:1 onto AstroDeck's existing
  guider API (`pixels`, `time`, `timeout`).

**Skip or defer:**

- **ZFilter / Lowpass / Lowpass2**: implement behind the same trait later; not needed
  for parity. If a smoother dec is wanted for high-quality mounts, Lowpass2 is the
  better second dec option (predictive, self-limiting).
- **Gaussian Process (PPEC)**: large dependency (Eigen-based GP regression), defer;
  design the axis-algorithm trait with `deduce_result()` from day one so it can slot in.
- **Chebyshev z-filter branch**: dead/buggy in upstream; do not port.
- **BLC**: implement `apply` + direction tracking early (cheap), but ship the adaptive
  size controller (§10.2) OFF until we have a backlash measurement tool; default pulse 0
  = disabled, like PHD2. The §10.3 `BacklashTool` state machine is the way to *seed* a
  non-zero pulse; port it as a guided one-shot routine (it drives raw N/S calibration
  pulses and reads back dec drift) so AstroDeck can offer "measure backlash" without the
  user hand-entering a pulse. Requires a prior GA drift measurement to feed
  `drift_per_min`; a valid dec calibration rate is a hard precondition.
- **ST-4-specific paths** (`CanPulseGuide() == false` advisories): AstroDeck drives
  Alpaca PulseGuide, so implement the pulse-guide branch only, but keep the east/south
  retrace checks as calibration-quality advisories.

**Deliberate deviations worth making:**

- Replace wall-clock `wxGetUTCTimeMillis` couplings with injected clocks for testability.
- MassChecker: keep the 45 s window but expose it; PHD2 hardcodes it.
- Surface `DistanceChecker` state ("recovering") in the guider status event stream —
  PHD2 buries it in the status bar.
- The `m_rejects > 3` reset in Lowpass2 fires on the 4th consecutive rejection although
  the comment says 3; replicate the code (condition `> 3`), not the comment.
