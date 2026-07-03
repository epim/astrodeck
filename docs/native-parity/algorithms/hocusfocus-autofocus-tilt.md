# Hocus Focus — Autofocus Engine + Aberration/Tilt Inspector: Algorithm Dossier

**Purpose**: single source of truth for a clean-room Rust reimplementation. Everything below was
extracted by reading the actual source (not from memory). The Rust implementer must NOT read the
original C#.

**Provenance**:
- Hocus Focus plugin clone: `references/hocusfocus` @ commit `4d93eafb01a158d63493b076eb3e4048eca14189` (MPL-2.0, © George Hilios)
- NINA core clone (for the trendline/parabolic fits HF reuses): `references/nina` @ commit `7c9de0c4202f2d054f58b0cb6b15d561f8b6b4b2` (MPL-2.0)

**Note on lineage**: this clone is significantly ahead of the last public HocusFocus release. It
includes a weighted-fit chain, Huber IRLS, four hyperbolic models + a "Hybrid" auto-selector,
reduced-χ² gates, a re-centered retry, a linear-parameterized paraboloid sensor model with
covariance-propagated standard errors, RANSAC frame alignment with several fallbacks, and a tilt
adapter screw-guidance system. All of it is documented here as it exists in the source.

---

## 1. Core data types

```rust
/// One pooled measurement (HFR or contrast) with its uncertainty.
struct MeasureAndError { measure: f64, stdev: f64 }        // stdev may be NaN = "unknown"

/// A focus-curve point. x = focuser position (integer steps, stored as f64),
/// y = HFR (px) or contrast, error_y = 1σ (star-ensemble scatter, see §4.2).
struct ScatterErrorPoint { x: f64, y: f64, error_x: f64, error_y: f64 }

/// Region of the sensor in *ratio* coordinates (0..1 of width/height).
struct RatioRect { x: f64, y: f64, width: f64, height: f64 }
struct StarDetectionRegion { outer: RatioRect, inner_crop: Option<RatioRect>, index: i32 }

enum AFMethod { StarHFR, ContrastDetection }
enum AFCurveFitting { Trendlines, Parabolic, TrendParabolic, Hyperbolic, TrendHyperbolic }
enum HyperbolicFitModel { Symmetric, UnevenBlend, TiltedHyperbola, SmoothBlend, Hybrid }
enum FitRejectionCriterion { RSquared, ReducedChiSquared }
```

---

## 2. What HF's autofocus changes vs stock NINA (summary)

| Area | Stock NINA | Hocus Focus |
|---|---|---|
| Detection concurrency | serial: expose → detect → move | pipelined: exposures continue while detection of previous frames runs in parallel; bounded by `MaxConcurrent` semaphore (0 = unlimited) and by "measurements-in-flight < offsetSteps" before evaluating stop conditions |
| Initial HFR baseline | none | when `ValidateHfrImprovement` (default on) and method is STARHFR, captures `FramesPerPoint` frames at the *starting* position first; if pooled initial HFR == 0 → `InitialHFRFailedException` → retry attempt |
| Final validation | moves to computed position; optionally validates | always moves to computed point; when `ValidateHfrImprovement`: captures `FramesPerPoint` frames there and requires `final_hfr ≤ initial_hfr × (1 + HFRImprovementThreshold)` (default 0.15) |
| Curve fit | single symmetric hyperbolic (or trend/parabolic) unweighted | 4 hyperbolic models + Hybrid auto-selection; optional 1/σ weighting (default on) with σ-regularization; in-fit Huber IRLS robustness; iterative Grubbs outlier rejection (median/MAD-based) |
| Fit quality gate | R² threshold only | R² **or** reduced χ² criterion (configurable); per-fitting-type gates; final point must lie inside swept range (deferred rejection with diagnostic exposure) |
| Retry | restart from initial position, `TotalNumberOfAttempts` | additionally ONE re-centered retry: if failure mode is HFR-regression or out-of-bounds, re-sweep centered on the just-calculated focus point (independent of attempt budget) |
| Multi-region | none | any number of `StarDetectionRegion`s analyzed concurrently per frame — the basis of the Aberration Inspector |
| Uncertainty | none | per-point σ from star ensemble scatter, SEM pooling across frames, propagated σ(best focus) from fit covariance, leave-one-out stability diagnostic |
| Save/replay | none | full frame + per-region detection JSON persistence, deterministic replay |
| "Fast focus" mode | n/a | **vestigial**: options exist (`FastFocusModeEnabled` etc., §12.1) but nothing in this source consumes them. Do not implement. |

---

## 3. Autofocus run — top-level control flow

Source: `AutoFocusEngine.RunImpl` / `RunAutoFocus` (AutoFocusEngine.cs 1211–1321, 1632–1722).

```rust
fn run_autofocus(options, filter, regions) -> Option<AutoFocusResult> {
    // Process-wide mutual exclusion: an atomic 0/1 guard; a second concurrent run is rejected
    // with an error notification and returns None. Guard is released on EVERY path.
    if !try_claim_af_in_progress() { return None; }
    defer! { release_af_in_progress(); }

    on_started_event();
    let timeout = options.auto_focus_timeout;                    // default 10 min
    // Disable focuser temp-comp if active (restore after); optionally stop guiding
    // (profile FocuserSettings.AutoFocusDisableGuiding), restore after.
    // Change to the AF filter if UseFilterWheelOffsets and a filter is flagged AutoFocusFilter.

    let initial_position = focuser.position();                   // AFTER filter change (offsets!)
    let mut sweep_center = initial_position;
    let mut calculated_point_retry_used = false;
    let max_iterations = max(1, options.total_number_of_attempts) + 1;   // hard backstop
    let mut iteration = 0;

    loop {
        iteration += 1;
        if iteration > max_iterations { break; }                 // defense-in-depth, never in practice
        capture_initial_hfr_if_needed(initial_position);         // §5
        state.on_next_attempt();                                 // clears measurements, ++attempt

        let ok = (|| {
            generate_focus_points(sweep_center)?;                // §6 blind sweep
            validate_calculated_focus_position()                 // §9
        })();
        // TooManyFailedMeasurementsException / InitialHFRFailedException are caught and
        // treated as ok=false (retry-eligible via the standard path).

        if ok { on_completed(); return Some(result); }

        if should_retry_from_calculated_point(
                state.last_failure_mode, state.last_calculated_focus_point,
                sweep_center, calculated_point_retry_used) {
            // ONE re-centered retry, independent of TotalNumberOfAttempts:
            calculated_point_retry_used = true;
            sweep_center = state.last_calculated_focus_point;
            focuser.move_to(sweep_center);
            on_iteration_failed();
            continue;
        } else if state.attempt_number < options.total_number_of_attempts {
            sweep_center = initial_position;                     // standard retry from start
            focuser.move_to(initial_position);
            on_iteration_failed();
            continue;
        }
        break;
    }
    on_failed();                                                 // writes failed replay metadata
    // post actions: restore focuser to initial_position (1-min timeout), restore filter,
    // re-enable temp comp, resume guiding. Each step is individually fault-tolerated.
    Some(result_with_succeeded_false)
}

/// Pure retry decision (AutoFocusEngine.cs 464-475):
fn should_retry_from_calculated_point(mode, calculated, current_center, used) -> bool {
    if used { return false; }
    let eligible = mode == HfrRegression || mode == FinalPointOutOfBounds;
    eligible && calculated >= 0 && calculated != current_center
}
```

**Failure modes** (enum `AutoFocusFailureMode`, AutoFocusEngine.cs 453–460): `None`, `FitQuality`,
`InitialHfrFailed`, `FinalPointOutOfBounds`, `HfrRegression`, `FinalHfrMissing`. Only
`HfrRegression` and `FinalPointOutOfBounds` are re-center-retry eligible (they indicate a sweep
that started far from focus; the others indicate bad data where re-centering will not help).

**Timeout**: whole run is wrapped in a linked cancellation with `AutoFocusTimeout`
(`AutoFocusTimeoutSeconds`, default 600 s). Timeout ⇒ warning + failure path.

---

## 4. Measurement pipeline

### 4.1 Exposure loop (per focus point)

Source: `StartAutoFocusPoint` / `TakeExposure` (AutoFocusEngine.cs 702–760, 995–1051).

- For `i in 0..FramesPerPoint`: acquire the exposure semaphore (`MaxConcurrent` slots; option value
  0 maps to unlimited), take one exposure, then spawn one analysis task **per region** which all
  share the prepared image. Camera stays free to expose the next point while analysis runs.
- Exposure time: profile `AutoFocusExposureTime` (default 4 s), overridden by per-filter
  `AutoFocusExposureTime` if ≥ 0, overridden by `options.OverrideAutoFocusExposureTime` if > 0.
- Binning: per-filter `AutoFocusBinning` else profile `AutoFocusBinning` (square). Per-filter
  gain/offset applied when > −1.
- Sub-sampling (hardware ROI): only when a **single** region, camera `CanSubSample`,
  `AutoFocusInnerCropRatio < 1` and `AutoFocusOuterCropRatio == 1`. The ROI is the centered
  rectangle of `round(sensor × inner_crop_ratio)`. On a camera error with subsample on, retries
  once without subsample. Null image ⇒ retry capture up to 3 total tries.
- Image prep: auto-stretch = true unless contrast-detection+Statistics; star detection off.

### 4.2 Per-frame, per-region HFR measurement

Source: `EvaluateExposure` (AutoFocusEngine.cs 576–700); aggregation in
`HocusFocusStarDetection.cs` 682–707.

- STARHFR: run the star detector on the region. The per-frame measurement is
  `MeasureAndError { measure: AverageHFR, stdev: HFRStdDev }` where (HF detector defaults):
  - `MeasurementAverage = Median` (default): `AverageHFR = median(HFR of aggregated stars)`,
    `HFRStdDev = 1.483 × MAD` (the `MedianMAD()` helper multiplies raw MAD by **1.483**).
  - `MeanOutliers` mode: first drop stars with `HFR > median + high_sigma×MAD_scaled` or
    `< median − low_sigma×MAD_scaled`, then `AverageHFR = mean`, `HFRStdDev` = sample stddev
    (n−1 denominator).
  - Saturated-star exclusion (`ExcludeSaturatedStarsFromHFR`, default true): stars with
    `background + peak ≥ saturation_threshold` are dropped from the HFR aggregate **only if**
    ≥ 3 unsaturated stars remain (`MinUnsaturatedStarsForHfr = 3`); they stay in the star list.
  - Needs ≥ 2 stars in the aggregate, else AverageHFR stays 0 ⇒ the point is a failure (y = 0).
- Region == None (plain AF pane): applies profile inner/outer crop ratio as detection ROI when
  inner < 1 and no hardware subsample.
- CONTRASTDETECTION + Statistics method: measurement is `100 × stddev(image)/mean(image)`,
  σ = 0.01, no star detection. Other contrast methods use NINA's `ContrastDetection.Measure`.
  Contrast detection is **incompatible with explicit regions** (throws).
- Any exception during evaluation yields `MeasureAndError { measure: 0.0, stdev: NaN }` — a
  failed point that still completes the pipeline.

### 4.3 Multi-frame pooling (`FramesPerPoint > 1`)

Source: `AverageMeasurement` (CvImageUtility.cs 768–797).

```rust
fn average_measurement(subs: &[MeasureAndError]) -> MeasureAndError {
    // mean over frames with measure > 0; frames with measure <= 0 are dropped entirely
    let valid: Vec<_> = subs.iter().filter(|m| m.measure > 0.0).collect();
    if valid.is_empty() { return MeasureAndError { measure: 0.0, stdev: f64::NAN }; }
    let mean = valid.map(|m| m.measure).sum() / valid.len();
    // SEM pooling: RMS of the finite positive per-frame σs, divided by √(valid frame count)
    let sig: Vec<_> = valid.filter(|m| m.stdev.is_finite() && m.stdev > 0.0).collect();
    let stdev = if sig.is_empty() { f64::NAN }
                else { (sig.map(|m| m.stdev*m.stdev).sum() / sig.len()).sqrt() / (valid.len() as f64).sqrt() };
    MeasureAndError { measure: mean, stdev }
}
```

A focuser position is completed exactly once (duplicate completions from replay are ignored).
After each completed point, the curve fit is recomputed on all points so far (live-updating fit).

### 4.4 Display σ sanitation

`SafeDisplayError(stdev) = if stdev.is_finite() { max(0.0, stdev) } else { 0.0 }` — used only for
charts/report error bars. Fits never consume this directly (they get regularized copies, §7.1).
(Historical bug note in source: an old 0.001 floor here became a 1000× weight downstream.)

---

## 5. Initial HFR baseline

Source: `StartInitialFocusPoints` / `InitialHFRMeasurementAction` (AutoFocusEngine.cs 906–917,
1053–1061); `StartBlindFocusPoints` head (1063–1072).

- Only when method == STARHFR and `ValidateHfrImprovement` is on.
- Captures `FramesPerPoint` frames at the starting focuser position **before** the sweep, pools
  them with `average_measurement`, records `InitialHFR` (region 0; on multi-region runs every
  region gets its own InitialHFR).
- Before the sweep begins, the engine waits for all initial-HFR analysis and checks: if
  `InitialHFR.measure == 0.0` ⇒ throw `InitialHFRFailedException` ⇒ counts as a failed attempt
  (standard retry path; failure mode InitialHfrFailed if it hits final validation instead).
- On a reattempt the initial HFR is **not** re-measured if already valid (only reset when null).

---

## 6. Blind sweep — point generation & sweep termination

Source: `StartBlindFocusPoints` (AutoFocusEngine.cs 1063–1194).

Definitions: `offset_steps = AutoFocusInitialOffsetSteps` (profile default 4; inspector may
override), `step = AutoFocusStepSize` (profile default 50).

```rust
fn blind_sweep(center: i32) {
    // Phase 1: walk OUT to +offset_steps then measure coming back DOWN one step at a time.
    // target starts at center + (offset_steps+1)*step; loop moves DOWN `step` each time:
    let mut target = center + (offset_steps + 1) * step;
    let mut leftmost = i32::MAX; let mut rightmost = i32::MIN;
    for _ in 0..offset_steps {
        let prev = target;
        target = focuser.move_to(target - step);       // actual reached position
        if target >= prev { fail!("Focuser reached its limit at {target}"); }
        leftmost = min(leftmost, target); rightmost = max(rightmost, target);
        start_focus_point(target);                     // async measure (FramesPerPoint frames)
    }
    wait_all_analysis();

    // Phase 2: extend one step at a time based on the live trendline fit, until one side
    // has >= offset_steps trend points and the other side has at least 1.
    loop {
        let tl = current_trendline_fit();              // recomputed after each completed point
        let points = measurements_by_position();
        let failures = points.values().filter(|m| m.measure == 0.0).count();
        if failures >= offset_steps { throw TooManyFailedMeasurements(failures); }

        let left_n  = tl.left_trend.map_or(0, |t| t.points.len());
        let right_n = tl.right_trend.map_or(0, |t| t.points.len());

        if left_n >= offset_steps && right_n > 0 {
            // enough left points + established minimum: queue ALL remaining right points
            let failed_right = points.iter().filter(|(x, m)| *x > tl.minimum.x && m.measure == 0.0).count();
            let target_max = tl.minimum.x + ((failed_right + offset_steps) * step) as f64;
            while (rightmost as f64) < target_max {
                let prev = rightmost;
                let actual = focuser.move_to(rightmost + step);
                if actual <= prev { fail!("Focuser reached its limit"); }
                rightmost += step;
                start_focus_point(actual);
            }
            break;
        } else if right_n >= offset_steps && left_n > 0 {
            // mirror image: queue remaining LEFT points down to
            // tl.minimum.x - (failed_left + offset_steps)*step
            ...
            break;
        }

        if left_n < offset_steps {
            leftmost -= step;
            let actual = focuser.move_to(leftmost);
            if actual >= prev_leftmost { fail!("limit"); }
            start_focus_point(actual);
            wait_all_analysis();          // NB: waits after each single extension step
        } else {
            rightmost += step;
            ... // symmetric right extension
        }
        // Backpressure: while measurements_in_progress >= offset_steps, await completion event.
    }
    wait_all_analysis();
}
```

Key details:
- "Left/right trend point counts" come from NINA's `TrendlineFitting` (§7.3) computed over all
  completed points; a point only counts toward a trend if it is on that side of the current
  minimum AND `y > min.y + 0.1` (HFR units).
- Failed points (`measure == 0`) count toward extending the queue range (`failed_right/left`)
  so the sweep still reaches `offset_steps` *usable* points per side.
- Focuser-limit detection: after every move, if the reached position did not advance in the
  requested direction ⇒ hard failure of the run (exception).
- `TooManyFailedMeasurementsException` when total zero-measure points ≥ offset_steps.

---

## 7. Curve fitting

### 7.1 Weight regularization (before every weighted fit)

Source: `WeightRegularization.cs`.

```rust
const MIN_ERROR_Y_FRACTION_OF_MEDIAN: f64 = 0.2;

/// Returns σ-regularized copies. Never mutates the raw points (reports keep raw σ).
fn regularize(points: &[ScatterErrorPoint]) -> Vec<ScatterErrorPoint> {
    let usable = |s: f64| s.is_finite() && s > 0.0;
    let positives: Vec<f64> = points.map(|p| p.error_y).filter(usable).collect();
    if positives.is_empty() {
        return points.map(|p| p.with_error_y(1.0));   // fully unweighted
    }
    let median = median(&positives);                   // plain median (MedianMAD().0)
    let floor = MIN_ERROR_Y_FRACTION_OF_MEDIAN * median;
    points.map(|p| {
        let s = if usable(p.error_y) { p.error_y.max(floor) } else { median };
        p.with_error_y(s)
    })
}
```

Effect: max weight advantage of any single point is 5× (25× squared). Unknown σ gets the median
(average weight), not maximum.

### 7.2 Grubbs outlier rejection test (median/MAD variant)

Source: `MathUtility.RejectionTest` (MathUtility.cs 149–185). Two-tailed, applied iteratively
(refit after each removal).

```rust
/// Returns the single worst outlier or None. `weights(x)` (typically 1/σ(x)) standardizes
/// residuals to match a weighted fit; pass None for unweighted.
fn rejection_test(points, fit: impl Fn(f64)->f64, confidence: f64,
                  weights: Option<impl Fn(f64)->f64>) -> Option<ScatterErrorPoint> {
    if points.len() <= 3 { return None; }
    let errors: Vec<f64> = points.map(|p| {
        let r = p.y - fit(p.x);
        match &weights { Some(w) => w(p.x) * r, None => r }
    });
    let (median, mad) = median_mad(&errors);           // mad = 1.483 * raw MAD
    let mut scale = mad;
    if scale <= 0.0 || scale.is_nan() { scale = sample_stddev(&errors); }   // fallback
    if scale <= 0.0 || scale.is_nan() { return None; } // perfect fit: no outliers possible

    let n = points.len() as f64;
    let p = (1.0 - confidence) / (2.0 * n);            // two-tailed with Bonferroni-style 1/N
    let t = student_t_inv_cdf(df = n - 2.0, p);        // location 0, scale 1
    let t2 = t * t;
    let grubbs_limit = (n - 1.0) / n.sqrt() * (t2 / (t2 + n - 2.0)).sqrt();

    let (z_max, idx) = errors.iter().map(|e| (e - median).abs() / scale).argmax();
    if z_max < grubbs_limit { None } else { Some(points[idx]) }
}
```

`median_mad`: sort; median = middle (or mean of two middles); MAD = median of |x−median|,
returned already scaled by **1.483**. Empty input ⇒ (NaN, NaN).

### 7.3 Trendline fit (NINA core, reused verbatim)

Source: `nina/NINA.WPF.Base/Utility/AutoFocus/TrendlineFitting.cs`, `Trendline.cs`.

- Minimum point selection (STARHFR): the point minimizing `y + error_y` (never a 0-HFR point;
  penalizes high-σ lows).
- Left trend points: `x < min.x && y > min.y + 0.1`; right trend: `x > min.x && y > min.y + 0.1`.
- Each trend is a **weighted** simple linear regression (OLS) with weights `1/error_y²` (this is
  why σ must never be 0 — see §7.1). `RSquared` = weighted coefficient of determination.
- Intersection of left/right lines: `x = (b2−b1)/(m1−m2)` (parallel ⇒ DataPoint(0,0));
  the returned intersection x is **rounded to int**.

### 7.4 Parabolic fit (NINA core, reused verbatim)

Source: `nina/NINA.WPF.Base/Utility/AutoFocus/QuadraticFitting.cs`.
Weighted (1/σ²) degree-2 polynomial least squares; minimum at `x = round(−b/(2a))`,
`y = f(x_rounded)`. Weighted R².

### 7.5 Hyperbolic fits — shared solver frame

Source: `AlglibHyperbolicFitting.cs` (base class).

All four concrete models share:
- **Input filter**: only points with `y ≥ 0.1` are fit (HFR floor; a no-op for real data).
- **Weights**: `w_i = 1 / max(|σ_i|, 1e-6)` when weighting enabled, else 1. (σ_i already
  regularized by §7.1 at the AF entry points.)
- **Solver**: Levenberg–Marquardt (alglib `minlm`) with:
  - box constraints (per model, below), per-parameter scale (per model),
  - stopping: `tolerance = 1e-6`, `max_iterations = 1000`,
  - analytic Jacobian for Symmetric/Tilted/SmoothBlend (with `acctype=1` secant acceleration);
    numeric differentiation (`h = 1e-6`) for UnevenBlend (kinked model),
  - residual function: `f_i = w_eff_i × (model(x_i) − y_i)`; Jacobian rows carry the same weight.
  - Failure: alglib termination type < 0 (except −5) ⇒ fit failed. (−8 = NaN/Inf in
    function/Jacobian, −3 = inconsistent constraints.)
- **Huber IRLS** (default ON, `HuberIrlsEnabled = true`, `HuberSigmaMultiplier = 1.5`):

```rust
fn solve_huber_irls(seed, lo, hi, scale) -> Option<Vec<f64>> {
    const MAX_ITER: usize = 10; const TOL: f64 = 1e-6;
    let mut w_eff = base_weights.clone();
    let mut guess = seed; let mut last_good = None;
    let mut prev_sum_abs = f64::INFINITY;
    for _ in 0..MAX_ITER {
        let sol = solve_once(&guess, lo, hi, scale, &w_eff)?;   // on failure: return last_good
        last_good = Some(sol.clone());
        guess = sol.clone();                                    // warm start
        let residuals: Vec<f64> = inputs.map(|x,y| model(&sol, x) - y);
        let sum_abs = residuals.map(f64::abs).sum();
        let (r_med, mad) = median_mad(&residuals);              // 1.483-scaled MAD
        if mad <= 0.0 || mad.is_nan() { break; }                // already tight
        let delta = 1.5 * mad;
        for i in 0..n {
            let ar = (residuals[i] - r_med).abs();              // centered on residual median
            let huber = if ar <= delta { 1.0 } else { delta / ar };
            w_eff[i] = base_weights[i] * huber;
        }
        if (prev_sum_abs - sum_abs).abs() <= TOL * sum_abs.max(1.0) { break; }
        prev_sum_abs = sum_abs;
    }
    last_good
}
```

- **Weighted R²**: computed over base weights via a weighted R² loss (`Accord RSquaredLoss` with
  weights = w). For parity, use the standard weighted R² = 1 − Σw(y−ŷ)²/Σw(y−ȳ_w)².
- **χ² / reduced χ²** (base weights, NOT Huber-modified):
  `χ² = Σ (w_i (model(x_i) − y_i))²`; `dof = max(1, n − p)`; `χ²_red = χ²/dof`.
- **σ(best focus)** — `ComputeMinimumStdError`:
  `Cov = s²·(JᵀWJ)⁻¹` with `W = diag(w_i²)`, J the analytic model gradient at the solution,
  `s² = Σ w_i²(model−y)² / (n − p)` (weighted RSS / dof). Then delta method:
  `var(x_min) = g·Cov·gᵀ = s²·(g·(JᵀWJ)⁻¹·gᵀ)` with `g = ∂x_min/∂θ` (per model).
  **Apply `s²` exactly once** — in source the matrix built is the raw `(JᵀWJ)⁻¹` (no s²),
  `variance = g·(JᵀWJ)⁻¹·gᵀ`, then `variance *= s²` a single time. NaN when n ≤ p, when the model
  has no analytic gradient support, on any numeric failure, or **when σ(x_min) > sampled x-span**
  (ill-conditioned suppression).
- **Leave-one-out best-focus stability** (`ComputeLeaveOneOutBestFocusStdError`): NaN if < 5
  points; refit dropping each point; collect finite minima in ascending drop-index order;
  NaN if < 2 succeeded; result = sample stddev (n−1) of the predicted best-focus positions.
  Diagnostic only, never a gate.

**Shared seed recipe** (all models; `TryComputeInitialState`):
```text
(x_lo, y_lo)  = point with the LOWEST y;  (x_hi, y_hi) = point with the HIGHEST y
if x_hi < x_lo { x_hi = 2*x_lo - x_hi }                  // reflect so Δx > 0
a0 = y_lo
b0 = sqrt( Δx² · a0² / (y_hi² − a0²) )   where Δx = x_hi − x_lo
x0_seed = x_lo ; y0_seed = 0
fail (return no-fit) if a0 or b0 is NaN/0 or Δx == 0
bounds: x0 ∈ [min x, max x]; y0 ∈ [−y_lo, +y_lo]; a ∈ [0.001, 2·y_lo]; b ∈ [0.001, ∞)
scale:  x0-scale = max(1, x_lo / y_lo) (or x_lo if y_lo ≤ 0); all others 1
```

### 7.6 Model 1 — Symmetric hyperbola (4 params {x0, y0, a, b})

Source: `HyperbolicFittingAlglib.cs`.

```text
y(x) = (a/b)·√((x−x0)² + b²) + y0
minimum: (x0, a + y0)
∂/∂x0 = −a·u / (b·r);  ∂/∂y0 = 1;  ∂/∂a = r/b;  ∂/∂b = −a·u² / (b²·r)
        with u = x−x0, r = √(u²+b²)
x_min gradient = [1, 0, 0, 0]  (minimum is exactly x0)
```

### 7.7 Model 2 — Tilted hyperbola (5 params {x0, y0, a, b, σ})

Source: `TiltedHyperbolicFittingAlglib.cs`. This is the model the plugin renders live and the
fallback when nothing survives Hybrid selection.

```text
y(x) = y0 + k·(√(u² + b²) + σ·u),   k = a/b, u = x − x0
Asymptote slopes are k·(1 ± σ); σ = 0 recovers the symmetric hyperbola.
Constraint constants: |σ| ≤ 0.9 (MaxRelativeSkew); b ≤ 2.0 × (x-span) (MaxBToSpanRatio);
seed b clamped into [0.001, 2·span]; extra fail if 2·span ≤ 0.001. Seed σ = 0.
Gradient (u, r = √(u²+b²), k = a/b):
  ∂/∂x0 = −k·(u/r + σ);  ∂/∂y0 = 1;  ∂/∂a = (r + σ·u)/b
  ∂/∂b  = −(a/b²)·(u²/r + σ·u);      ∂/∂σ = k·u
Best focus (closed form):  u* = −σ·b / D,  D = √(max(1−σ², 1e-12));  x_min = x0 + u*
x_min gradient: [1, 0, 0, −σ/D, −b/D³]
```

### 7.8 Model 3 — Smooth blend (5 params {x0, y0, a, b, c})

Source: `SmoothBlendHyperbolicFittingAlglib.cs`.

```text
t(u) = 1/(1 + e^{u/w})  (clamped: t=0 for u/w>40, t=1 for u/w<−40)
y(x) = y0 + t·(a/b)·√(u²+b²) + (1−t)·(a/c)·√(u²+c²)
w (blend width) is NOT fitted: w = max(0.25 × median consecutive x-spacing, 1e-6)
   (spacing from sorted distinct positive gaps; fallback = max(x-span, 1))
Gradient: with rb=√(u²+b²), rc=√(u²+c²), dt/du = −t(1−t)/w, gb=(a/b)rb, gc=(a/c)rc:
  df/du = t·(a/b)(u/rb) + (1−t)(a/c)(u/rc) + (dt/du)(gb − gc);  ∂/∂x0 = −df/du
  ∂/∂y0 = 1;  ∂/∂a = t·rb/b + (1−t)·rc/c
  ∂/∂b = t·(−a·u²/(b²·rb));  ∂/∂c = (1−t)·(−a·u²/(c²·rc))
Seed: c0 = b0; bounds b,c ∈ [0.001, ∞).
Minimum: golden-section search on [min x, max x], inv_phi = 0.6180339887498949,
  100 iterations max or bracket < 1e-7·max(1,|b|); x_min = midpoint of final bracket.
x_min gradient: central finite differences of the golden-section minimizer,
  h_j = max(1e-3·|θ_j|, 1e-4).
```

### 7.9 Model 4 — Uneven blend (5 params {x0, y0, a, b, c}; legacy)

Source: `HyperbolicUnevenFittingAlglib.cs`. Requires the focuser `step_size` (≠ 0, else error).

```text
t = clamp((x0 − x)/step_size, 0, 1)                        // hard C⁰ ramp, kinks at x0, x0−step
y(x) = t·(a/b)·√(u²+b²) + (1−t)·(a/c)·√(u²+c²) + y0
minimum: exactly (x0, a + y0); x_min gradient = [1,0,0,0,0]
Optimizer uses NUMERIC differentiation (kinks); covariance still uses the analytic gradient:
  tp = 1/step_size when 0 < (x0−x)/step < 1 strictly, else 0
  ∂/∂x0 = tp·(L − R) − t·(a/b)(u/sb) − (1−t)(a/c)(u/sc)     (L=(a/b)sb, R=(a/c)sc)
  ∂/∂y0 = 1;  ∂/∂a = t·sb/b + (1−t)·sc/c
  ∂/∂b = −t·a·u²/(b²·sb);  ∂/∂c = −(1−t)·a·u²/(c²·sc)
Seed/bounds as shared recipe with c0 = b0.
```

### 7.10 Hybrid model selection

Source: `AlglibHyperbolicFitting.SelectBestModel` (AlglibHyperbolicFitting.cs 305–437) +
`FitWithOutlierRejection` (491–524) + `IsAsymmetryJustified` (542–552) + `RankBest` (446–480).

Candidates (fixed order): `[Symmetric, UnevenBlend, TiltedHyperbola, SmoothBlend]`.
Constant: `TiltSignificanceConfidence = 0.95` (hardcoded).

```rust
fn select_best_model(points, step, use_weights, max_rejections, confidence)
        -> (HyperbolicFitModel, Option<Fit>, Vec<ScatterErrorPoint> /*consensus rejects*/) {
    let points = sort_by_x(points);                 // determinism
    let (min_x, max_x) = x_range(&points);

    // PASS 1 (parallelizable): each model independently fits + proposes its own Grubbs
    // outliers via reject-and-refit (each rejection must survive a successful refit;
    // budget = max_rejections; weighted residuals when use_weights).
    let proposals[k] = fit_with_outlier_rejection(model_k, &points, ...);

    // CONSENSUS: intersect the proposed reject sets (keyed by round(x) as i32) across ALL
    // solved models; need >= 2 solved models, else reject nothing. Materialize from the first
    // solved model's list order; cap at max_rejections (defensive).
    let consensus = ...;
    let cleaned = points minus consensus;

    // PASS 2: fit every model fresh on the SAME cleaned set.
    // Survivors: solved AND finite minimum.x AND min_x <= minimum.x <= max_x.
    // If none survive: return (TiltedHyperbola, its pass-2 fit or None, consensus).

    // F-TEST GATE (parsimony): if Symmetric survived,
    //   for each 5-param survivor: justified iff
    //     n − 5 >= 1, χ²_sym and χ²_asym finite, χ²_asym > 0,
    //     improvement = χ²_sym − χ²_asym > 0,
    //     F = (improvement / 1) / (χ²_asym / (n − 5))  >  F_crit(1, n−5; 0.95)
    //   allowed = justified set, or {Symmetric} if none justified.
    // If Symmetric did not survive, all survivors are allowed.

    // RANKING (RankBest): tier 1 = ascending finite σ(focus) (MinimumStdError).
    // If NO allowed candidate has a finite σ(focus): tier 2 = ascending finite LOO std
    // (computed lazily per candidate). If none finite there either: everyone ties at 0.
    // Tie-break: ascending reduced χ² (NaN last), then descending R² (NaN last).
    (winner_model, winner_fit, consensus)
}
```

Live per-run use: `SelectBestHyperbolicModel()` runs at finalization only when the configured
model is `Hybrid` (during the run, Hybrid renders as TiltedHyperbola). It replaces the region's
fit and rejected-point set with the winner's. For non-Hybrid, the configured model is fitted
directly with iterative Grubbs rejection (§7.11).

### 7.11 The per-point live fit + rejection loop (CurveFittingResult.Calculate)

Source: AutoFocusEngine.cs 95–168.

```rust
fn calculate_fittings(method, fitting_type, focus_points, opts) -> Option<Fittings> {
    let focus_points = sort_by_x(focus_points);
    // y <= 0 points go straight to the rejected list (never fitted)
    let valid = regularize(focus_points.filter(|p| p.y > 0.0));
    if valid.len() < 3 { return None; }
    let mut rejected = focus_points.filter(|p| p.y <= 0.0).to_vec();
    let mut n_rejected = 0;
    loop {
        let mut fits = Fittings::default();
        let mut reject: Option<Point> = None;
        if method == StarHFR {
            if valid.len() >= 2 { fits.trendline = trendline_fit(&valid); }   // ALWAYS (drives sweep)
            if valid.len() >= 3 {
                match fitting_type {
                    Parabolic | TrendParabolic => {
                        fits.quadratic = quadratic_fit(&valid);
                        // quadratic is always 1/σ²-weighted -> weighted rejection ALWAYS:
                        reject = rejection_test(&valid, fits.quadratic.f, opts.confidence,
                                                Some(residual_weights(&valid, true)));
                    }
                    Hyperbolic | TrendHyperbolic => {
                        let hf = hyperbolic_create(opts.model, &valid, opts.step, opts.weighted);
                        if hf.solve() {
                            fits.hyperbolic = hf;
                            reject = rejection_test(&valid, hf.f, opts.confidence,
                                                    residual_weights(&valid, opts.weighted));
                        } // else: log error, keep going without hyperbolic
                    }
                    _ => {}
                }
            }
        } else if valid.len() >= 3 {   // contrast detection
            fits.trendline = trendline_fit(&valid);
            fits.gaussian = gaussian_fit(&valid);
            reject = rejection_test(&valid, fits.gaussian.f, opts.confidence, None);
        }
        match reject {
            Some(p) if n_rejected < opts.max_outlier_rejections => {
                n_rejected += 1; rejected.push(p); valid.remove(p);
            }
            _ => return Some((fits, rejected)),
        }
    }
}
// residual_weights: map x -> 1/max(|σ(x)|, 1e-6); unknown x -> 1.0; None if unweighted.
```

### 7.12 Final focus point per fitting type

Source: `DetermineFinalFocusPoint` (AutoFocusEngine.cs 340–380).

| Fitting | Final point |
|---|---|
| TRENDLINES | trendline intersection |
| HYPERBOLIC | hyperbolic minimum |
| PARABOLIC | quadratic minimum |
| TRENDPARABOLIC | `x = round((trend_x + quad_x)/2)`, `y = (trend_y + quad_y)/2` |
| TRENDHYPERBOLIC | `x = round((trend_x + hyp_x)/2)`, `y = (trend_y + hyp_y)/2` |
| Contrast (any) | gaussian maximum |

---

## 8. (reserved — merged into §7)

## 9. Final validation & completion criteria

Source: `ValidateCalculatedFocusPosition` (AutoFocusEngine.cs 1421–1594),
`IsHyperbolicFitAcceptable` (1406–1419).

Order of operations (STARHFR; contrast skips the fit-quality block):

1. **Per-region fit-quality gates** (every region must pass):
   - Hyperbolic/TrendHyperbolic: configurable criterion:
     - `RSquared` (default): reject iff `r_squared_threshold > 0 && hyperbolic.r2 < threshold`
       (`RSquaredThreshold` lives in the NINA profile; NINA default 0.7).
     - `ReducedChiSquared`: reject iff `threshold > 0 && χ²_red is finite && χ²_red > threshold`
       (default threshold 5.0; non-finite χ²_red never rejects).
   - If `r_squared_threshold > 0`:
     - Parabolic/TrendParabolic: reject if `quadratic.r2 < threshold`.
     - Trendlines/TrendHyperbolic/TrendParabolic: reject if `left.r2 < threshold` OR
       `right.r2 < threshold`.
   - Any rejection ⇒ failure mode `FitQuality`, return false.
2. For each region: finalize (Hybrid model selection §7.10, final focus point §7.12, LOO §7.5).
   If `round(final.x) < 0` ⇒ `FitQuality` failure ("not enough data points with stars").
3. **Bounds check** per region: `min ≤ final_position ≤ max` where min/max are the swept
   positions. First violation is **deferred** (recorded, loop breaks) so a diagnostic
   final-validation image can still be captured.
4. `calculated_focus_point` = region-0 final position **before** `FocuserOffset` is applied;
   then `final_position += FocuserOffset` (default 0).
5. **Move + final validation exposure**: skip the move entirely if out-of-bounds AND HFR
   validation is off. Otherwise move to the target — clamped into `[out_min, out_max]` when
   out-of-bounds — and, when `ValidateHfrImprovement`, capture `FramesPerPoint` frames there
   (FinalHFR pooled per region).
6. Apply the deferred out-of-bounds rejection ⇒ failure mode `FinalPointOutOfBounds`,
   `last_calculated_focus_point = calculated_focus_point`, return false. (Retry-eligible.)
7. **HFR improvement check** (per region, when validation on):
   - FinalHFR missing/0 ⇒ `FinalHfrMissing`, false.
   - InitialHFR missing/0 ⇒ `InitialHfrFailed`, false.
   - `final_hfr > initial_hfr × (1 + HFRImprovementThreshold)` ⇒ `HfrRegression`,
     `last_calculated_focus_point = calculated_focus_point`, false. (Retry-eligible.)
8. Otherwise: success.

---

## 10. Save / replay (behavioral contract only)

- Save folder layout: `AutoFocus_<yyyyMMdd_HHmmss>/{initial|attemptNN|final}/`.
- Frame filename: `{image:00}_Frame{frame:00}_BitDepth{n}_Bayered{0|1}_Focuser{pos}` (+ optional `_HFR{x}`).
- Per-region detection JSON: `{image:00}_Frame{frame:00}_Region{reg:00}_star_detection_result.json`.
- `metadata.json` written on completion and terminal failure (settings snapshot, region
  geometry, result summary, failure reason).
- Replay: `FramesPerPoint` inferred as min frames across image numbers; step size inferred as
  the difference of the two smallest distinct focuser positions; detection-result reuse only
  when detector version + params cache key match exactly, any error ⇒ silent re-detect.

---

## 11. Aberration Inspector

### 11.1 Region layout (the "grid")

Source: `InspectorVM.GetStarDetectionRegions` (InspectorVM.cs 1369–1390);
tiling helper `SensorAberrationCalculator.CreateFullRegionSet` (rows×cols row-major, indices
from 1 — available but the inspector uses the explicit 6/7-region layout below).

```rust
fn inspector_regions(sensor_roi: f64, corners_roi: f64, curve_model: bool) -> Vec<Region> {
    let third = 1.0/3.0;
    let w = third * corners_roi * sensor_roi;            // corner box edge (ratio units)
    let inner = (1.0 - sensor_roi) / 2.0;                // distance from image edge
    let outer = 1.0 - inner - w;
    vec![
        af_region(),                                             // index 0: plain AF ROI (whole frame or profile crop)
        Region(RatioRect(third, third, third, third), 1),        // index 1: CENTER (middle ninth)
        Region(RatioRect(inner, inner, w, w), 2),                // index 2: TOP-LEFT
        Region(RatioRect(outer, inner, w, w), 3),                // index 3: TOP-RIGHT
        Region(RatioRect(inner, outer, w, w), 4),                // index 4: BOTTOM-LEFT
        Region(RatioRect(outer, outer, w, w), 5),                // index 5: BOTTOM-RIGHT
    ]
    // when the per-star sensor curve model is enabled, add:
    // index 6: FULL-SENSOR ROI = RatioRect(r, r, 1−r, 1−r) with r = (1 − sensor_roi)/2
    // NOTE (source quirk): width/height are stored as (1−r), not (1−2r).
}
```

The AF engine is then run with `RunWithRegions` — one shared sweep, all regions measured on
every frame, each region getting its own curve fit and final focus point.

### 11.2 Inspector AF-run option overrides

Source: `GetAutoFocusEngineOptions` + `ApplySignalAmplification` (InspectorVM.cs 1110–1153).

- `FramesPerPoint`, `AutoFocusInitialOffsetSteps` (StepCount), `AutoFocusTimeout`, exposure time
  overridden when the inspector option > 0 (−1 = use profile).
- StepSize override applies **only to replays**.
- **Signal amplification** (live captures only, factor `amp = max(1, SignalAmplification)`,
  default 2): `offset_steps *= amp; step_size = max(1, round(step_size / amp))` — same sweep
  range, `2·offset_steps·amp + 1` finer-spaced points.
- Optional pre-run centering AF (`CenterFocuserBeforeRun`, default false): a plain
  single-region AF with profile options, never saved; failure is non-fatal.

### 11.3 Backfocus estimate (region-based)

Source: `UpdateBackfocusMeasurements` (InspectorVM.cs 1550–1582).

```text
inner  = region[1] (center) estimated final focuser position / HFR
outer  = mean over regions[2..5] of estimated final focuser position / HFR
critical_focus_um = 2.44 · f_ratio² · 0.55          // CFZ, λ = 0.55 µm
backfocus_steps   = outer_pos − inner_pos
direction         = if backfocus_steps > 0 { "TOWARDS" } else { "AWAY FROM" }   // the objective
backfocus_um      = backfocus_steps × MicronsPerFocuserStep    (NaN if step size unset)
within_cfz        = |backfocus_um| < critical_focus_um
backfocus_hfr     = outer_hfr − inner_hfr
```

Sign semantics: corners focusing **farther** (higher focuser position) than center ⇒ move the
sensor TOWARD the objective (reduce backfocus). Displayed in µm only when
`MicronsPerFocuserStep > 0`.

### 11.4 Four-corner tilt plane (the classic tilt table)

Source: `TiltModel.cs` / `TiltPlaneModel` (13–166).

- Inputs: regions 1..5 final focuser positions (center, TL, TR, BL, BR).
- Plane fit: ordinary least squares **with intercept** on exactly 4 points, model space
  coordinates `(x̂, ŷ) ∈ {−0.5, +0.5}²` where `x̂ = x_px/width − 0.5`, `ŷ = y_px/height − 0.5`:
  `focuser(x̂, ŷ) = A·x̂ + B·ŷ + C`. (With 4 symmetric points OLS gives
  `A = ((TR+BR)−(TL+BL))/2`, `B = ((BL+BR)−(TL+TR))/2`, `C = mean`, but implement as OLS.)
- `mean = mean(4 corner focuser positions)` (center NOT included).
- Per-corner adjustment: `steps = corner_pos − mean`; `microns = steps × MicronsPerFocuserStep`
  (NaN when step size unset). Center row shows its focuser position, adjustment NaN.
- Each corner row also carries the R² of that region's curve fit (`GetRSquared`: quadratic R²
  for parabolic types, hyperbolic R² for hyperbolic types, NaN otherwise).
- `f_ratio` fallback: NaN ⇒ 5.0.
- Errors: throws if image width/height ≤ 0; model x/y accessors throw if pixel out of bounds.

### 11.5 Per-star sensor model (paraboloid) — pipeline overview

Source: `SensorModel.cs` (`UpdateModel` → `RegisterStarsAndFit` → `MatchStarsUsingKdTree` →
`FitImages` → `FitParaboloidModel`).

Inputs: for every frame of the sweep, region 6's full star-detection result (captured via the
sub-measurement event), the frame's focuser position, `MicronsPerFocuserStep`
(`focuser_size_microns`; warn + assume 1.0 if unset), pixel size (µm), image size, f-ratio,
region-0 final focus position, step size.

**Stage A — normalize brightness** (per frame):
`normalized = (avg_brightness − frame_min) / (frame_max − frame_min)` per star; original
position/bbox preserved for reset.

**Stage B — RANSAC frame alignment** (optional, `UseRANSAC` default true):
- Reference frame = the one with the most detected stars.
- Build "star triangles" in the reference with a search box that starts at
  `0.0055 × min(w,h)` px and is grown/shrunk by `0.005` steps until the (one-triangle-per-star)
  triangle count lands in [100, 200] (bounds `0.001..0.1` of min dimension).
- For every other frame: build dense triangles at the same box; putative matches = triangles
  whose similarity-invariant shape descriptor (sorted side-length ratios) is within Euclidean
  distance **0.02** (strict); if < 20 matches, retry relaxed at **0.05**.
- Transform: 4-DOF similarity (default) or 6-DOF affine (`UseAffineAlignment`, diagnostics
  only), estimated with RANSAC; applied to star positions and bounding boxes.
- Fallback ladder for frames that fail: (1) dense-reference retry (one-per-point off) with a
  plate-scale sanity guard `0.9 ≤ √|det| ≤ 1.1`; (2) box escalation: re-triangulate frame +
  dense reference at boxes growing ×1.5 up to `0.35 × min(w,h)`, capped at the brightest 120
  stars per set and a `2×10⁸` match-work budget, needs ≥ 12 frame triangles and ≥ 6 putative
  matches, same scale guard; (3) neighbor chaining: align to the nearest (by focuser distance)
  already-aligned frame (which is already in reference space), iterated until no progress.
  A frame that still fails stays unaligned (it just matches with the wider radius below).

**Stage C — star matching across frames** (KdTree, greedy nearest-first):
- Search radius: **10 px** if RANSAC aligned all frames, else **30 px**.
- Global registry seeded with the reference frame's stars. For each other frame: for every star,
  find registry stars within the radius; optionally filter candidates by
  `|Δnormalized_brightness| < tolerance`; enqueue all candidate pairs by ascending distance²;
  greedily accept one-to-one matches. Source stars with **no** registry neighbor at all are
  added as new registry entries (frame union); ambiguous unmatched stars are not.
- Duplicate positions in a frame tree are skipped (overlapping donuts after alignment).

**Stage D — brightness-tolerance search** (only when `RejectBadBrightnessMatches`, default
false): bidirectional search over the tolerance, pivot = `StartingBrightnessDiff` (−1 ⇒ 0.1),
multiply/divide by 1.5 per step, direction switches when fit quality stops improving; bounds:
up-exhausted at tolerance ≥ 3 or zero rejections, down-exhausted at ≤ 0.01; hard cap **12**
iterations. Keeps the best paraboloid fit seen (`IsBetterFit`: higher R² wins; R²
indistinguishable from 1 (`1 − R² < 0.005`) is "too good" = suspicious and loses). Acceptance
to stop early: ≥ 10 stars in model AND model acceptable (§11.7). With the flag off: single pass.

**Stage E — per-star focus curves** (parallel per star; `FitImages`):
```rust
for star in registered_stars {
    if star.matched.len() < 5 { skip; }                      // minStarCountForFitting = 5
    let points = regularize(star.matched.map(|m|
        ScatterErrorPoint { x: m.focuser_position, y: m.hfr, error_x: 0,
                            error_y: estimate_hfr_stddev(m.star) }));
    // rejection budget: min(MaxOutlierRejections, points.len() − 5), 0 when
    // RejectBadlyFittingMatches (default true) is off
    // Hybrid model: SelectBestModel(points, budget, confidence)   (§7.10, sequential inner)
    // Fixed model: create + solve, then iterative Grubbs reject-and-refit within budget
    if !solved { discard star; }
    if fit.r2 < 0.90 { discard star; }                       // PerStarAcceptableRSquared
    data_point = SensorParaboloidDataPoint {
        x: (reg_x − width/2)  × pixel_um,                    // sensor µm from center
        y: (reg_y − height/2) × pixel_um,
        focuser_position: fit.minimum.x × microns_per_step,  // best focus in µm
        r_squared: fit.r2,
        focuser_position_std_dev: fit.minimum_std_error × microns_per_step, // NaN if unknown
    };
}
// Unknown per-star σ resolved to the MEDIAN of the known σs (or 1.0 if none known).
```

Per-detection HFR σ estimate (`EstimateHfrStdDev`, SensorModel.cs 602–614):
```text
signal = avg_brightness − background
noise  = sqrt(max(avg_brightness, 1))
snr    = signal > 0 ? signal/noise : 0
σ      = hfr / max(snr, 0.2)                 // HfrSigmaSnrFloor = 0.2
if contamination_suspected { σ *= 3.0 }      // HfrSigmaContaminationFactor
σ = max(σ, 1e-3)
```

**Stage F — paraboloid surface fit** (`FitParaboloidModel` + `SensorParaboloidSolver` +
`NonLinearLeastSquaresSolver.SolveWinsorizedResiduals`):
- Needs ≥ 9 data points (else exception / next tolerance iteration).
- Model (all coordinates in sensor µm from center; z in focuser µm):

```text
isotropic  (6 params [x0, y0, z0, gx, gy, k]):
   z(x,y) = gx·(x−x0) + gy·(y−y0) + k·((x−x0)² + (y−y0)²) + z0
astigmatic (7 params [x0, y0, z0, gx, gy, kx, ky], AstigmaticCurvatureEnabled, default off):
   z(x,y) = gx·(x−x0) + gy·(y−y0) + kx·(x−x0)² + ky·(y−y0)² + z0

Gradient: ∂/∂x0 = −gx − 2·kx·(x−x0);  ∂/∂y0 = −gy − 2·ky·(y−y0);  ∂/∂z0 = 1
          ∂/∂gx = x−x0;  ∂/∂gy = y−y0
          isotropic ∂/∂k = (x−x0)² + (y−y0)²;  astigmatic ∂/∂kx = (x−x0)², ∂/∂ky = (y−y0)²

Seed: x0=y0=0, z0 = final_focus_position × microns_per_step, gx=gy=k(=ky)=0
Bounds: x0,y0 pinned to 0 when FixedSensorCenter (default TRUE); else ±sensor_size/2.
        Everything else unbounded.
Scale: [1, 1, 1, 1e-3, 1e-3, 1e-6(, 1e-6)]
Weights: w_i = 1/max(σ_i, 1e-9), σ_i = per-star best-focus σ in µm (χ² weighting).
LM: analytic Jacobian (weighted rows), tolerance 1e-8, unlimited iterations (alglib maxits=0).
```

- **Winsorized-residual outer loop** (`SolveWinsorizedResiduals`, ≤ 10 iterations,
  `winsorization_sigma = 2.5`): solve → if weighted R² did not improve vs previous iteration,
  return the previous solution → compute residuals (estimated − observed) over enabled points →
  `(median, mad)` (1.483-scaled) → disable every point with residual outside
  `±2.5 × mad` (NOTE: bounds are NOT centered on the median in the source —
  `upper = +2.5·mad`, `lower = −2.5·mad`) → repeat until no new points disabled.
- Fit statistics over **enabled** points: weighted R² with weighted mean
  (`ȳ_w = Σw²y/Σw²`, `R² = 1 − Σ(w(ŷ−y))²/Σ(w(y−ȳ_w))²`); unweighted RMS error (µm);
  `χ² = Σ(w(ŷ−y))²`, `dof = max(1, enabled − p)`, `χ²_red = χ²/dof`; upper-tail p-value from the
  χ² distribution.
- **Parameter covariance**: `Cov = s²(JᵀWJ)⁻¹` over free (non-pinned) parameters only,
  `s² = weighted RSS/(enabled − free_count)`; null on singular/ill-conditioned/insufficient
  data. Propagated by delta method to:
  - tilt angle `θ = atan(g)`, `g = √(gx²+gy²)`: `∂θ/∂gx = gx/(g(1+g²))` etc.;
    undefined (NaN) at g = 0.
  - curvature radius `R_mm = 1/(2000·|K|)` (`K = (kx+ky)/2`): `σ_R = R·σ_K/|K|`;
    astigmatic `var(K) = ¼(var kx + var ky + 2 cov)`.

### 11.6 Derived aberration metrics

Source: `SensorParaboloidModel` (derived props) + `SensorModelAberrationResult.Update`
(SensorModelAberrationResult.cs 270–320) + analyses (322–430).

```text
θ (tilt angle)      = atan(√(gx² + gy²))               [radians]
φ (tilt azimuth)    = atan2(gy, gx)
C                   = sign(K)·√|K|
critical_focus_um   = 2.44 · f_ratio² · 0.55
curvature_radius_mm = 1 / (2·C²·1000)  ≡  1/(2000·|K|)
center_offset_x/y   = x0, y0 (µm)
curvature_effect_um = CurvatureAt(w_um/2, h_um/2) = kx·(w/2)² + ky·(h/2)²      // corner-of-axis
tilt_corners        = TiltAt(±w/2, ±h/2) = gx·x + gy·y at the 4 corners        // (about origin!)
tilt_effect_um      = (max(tilt_corners) − min(tilt_corners)) / 2
sensor_volume       = ∫∫ z dA over [−w/2,w/2]×[−h/2,h/2]  (closed form):
     −w·h·(gx·x0 + gy·y0) + w·h·(kx·x0² + ky·y0²) + (w·h/12)·(kx·w² + ky·h²) + z0·w·h
sensor_mean_elev_um = volume / (w·h)
sensor_mean_pos     = mean_elev / microns_per_step        [focuser steps]
af_mean_offset      = sensor_mean_pos − final_focus_position   // suggested AF offset
```

Analysis verdicts (UI rows):
- Model fit: rejected only when `R² < AcceptableRSquaredMin` (default 0.05) **AND**
  reduced χ² unacceptable (> 5.0 fixed cap `AcceptableReducedChiSquared`, or non-finite).
  Low R² alone is fine (flat sensor). This same rule gates the whole model
  (`IsModelAcceptable`) — total failure throws "Sensor modeling failed".
- Tilt acceptable: `|tilt_effect_um| < 0.25 × critical_focus_um`.
- Curvature acceptable: `|curvature_effect_um| < 1.5 × critical_focus_um`; guidance text says
  try REMOVING spacers when curvature_effect > 0 else ADDING.
- Centering: informational warning when `|x0| ≥ 10 × pixel_um` (same for y0).
- < 10 stars in model: reliability warning (not fatal).

Tilt-plane view derived **from the paraboloid** (SensorModelAberrationResult.cs 446–462):
`center = z(x0, y0)/steps`; each corner = `center + TiltAt(±w/2, ±h/2)/steps`; then feed the
standard 4-corner OLS plane of §11.4 (so both models share one display path).

### 11.7 Optional RBF interpolation grid (default OFF, hard-disabled)

`InspectorOptions.InterpolationEnabled` is `false` and not settable in this build. When on:
mean nearest-neighbor distance defines a grid (odd counts, ≥ 3); alglib RBF
(Hierarchical / ThinPlateSpline / MultiQuadric [default] / BiHarmonic) with λ per
`InterpolationAmount` (Small 1e-6/1e-6, Medium 1e-3/1e-4 [default], Large 1.0/1e-2); grid nodes
kept only where a real point is within mean distance. Not recommended for the port.

### 11.8 Single-exposure analysis (FWHM contour + eccentricity vectors)

Source: `AnalyzeExposureImpl` / `AnalyzeStarDetectionResult` (InspectorVM.cs 896–1066).

- One snapshot (exposure = `SimpleExposureSeconds` if ≥ 0 else AF exposure time), full-frame
  detection with `ModelPSF = true` (PSF fitting on).
- Grid: `num_cols = NumRegionsWide` (default 7); `region_px = width / num_cols`;
  `num_rows = height / region_px`, incremented by 1 if even (odd count covers the center row).
- Stars without a fitted PSF are ignored. Row index is computed on the **flipped** y
  (`(height − y − 1)/height × num_rows`) so the plot renders top-down.
- Per cell: `eccentricity = median(PSF eccentricity)`;
  `rotation = Σ(θ_i·e_i)/Σ(e_i)` (eccentricity-weighted mean PSF angle);
  vector = `(cos θ̄, −sin θ̄) × e_med² × 2.5`. Cells with no stars = NaN.
- FWHM contour: per-star `PSF.FWHMArcsecs` rendered by a kriging/contour control (display only;
  frame-level FWHM = median, MAD via `MedianMAD`).
- Requires ≥ 1 PSF-modeled star, else the panels are hidden with an explanatory error.

### 11.9 Tilt-adapter screw guidance (math only)

Source: `TiltScrewGeometry.cs`; arrows in `InspectorVM.RebuildTiltGuidance` (1875–2056).

```text
Screw at clock-angle θ (degrees CW from 12 o'clock, image space, +y down):
   position (µm) p = (R·sin θ, −R·cos θ),  R = screw circle radius in µm

Forward (single screw axial moves d_i -> plane gradient): G = (2/(n·R²)) · Σ d_i·p_i
Inverse (cancel a gradient): δ_i = −(G · p_i) = −(gx·p_x + gy·p_y)          [µm, per screw]
Backfocus (curvature) correction at screw i:  δ_bf,i = −(kx·(p_x−x0)² + ky·(p_y−y0)²)
Total axial per screw = δ_tilt + sign·δ_bf   (sign = rig curvature-direction, ±1, default +1)
Turns/steps = total_µm / pitch_µm (thread pitch or stepper step size)
Calibration inverse (recover pitch from one applied move): |ΔG|·leverage,
   leverage = 1.5·R (3-screw single-screw move) or R (4-screw push-pull move)
Tilt-plane (A,B in steps per normalized [−0.5,0.5] coord) <-> physical gradient:
   gx = A·step_µm/width_µm ; gy = B·step_µm/height_µm   (and the exact inverse)
Arrow thresholds: per-screw turn share t_i = (2/n)(−A·sinθ + B·cosθ); noise |max|<0.005 ⇒ "—";
   ratio ≥ 0.5 big arrow, ≥ 0.1 small arrow (motion arrows are −sign·t_i / max|t|).
Backfocus arrow: |curvature_effect_µm| < 10 ⇒ "—", ≥ 50 ⇒ big arrow; direction toward
   objective iff curvature_effect_µm > 0.
Pitch mismatch warning when |active − measured|/measured > 0.15.
```

---

## 12. Configuration knobs

### 12.1 HF AutoFocus options (`AutoFocusOptions.cs`)

| Option | Default | Valid range / validation | Consumed by |
|---|---|---|---|
| `MaxConcurrent` | 0 (= unlimited) | int ≥ 0 | analysis semaphore |
| `FastFocusModeEnabled` | false | bool | **NOTHING (vestigial)** |
| `FastStepSize` | 1 | int | vestigial |
| `FastOffsetSteps` | 4 | ≥ 2 (setter throws) | vestigial |
| `FastThreshold_Seconds` | 3600 | ≥ 0 | vestigial |
| `FastThreshold_Celcius` | 5 | ≥ 0 | vestigial |
| `FastThreshold_FocuserPosition` | 100 | ≥ 0 | vestigial |
| `AutoFocusTimeoutSeconds` | 600 | > 0 | run timeout |
| `ValidateHfrImprovement` | true | bool | §5, §9 |
| `HFRImprovementThreshold` | 0.15 | finite | §9.7 |
| `Save` / `SavePath` | false / "" | — | §10 |
| `KeepFramesForReview` | false | bool | review feature |
| `FocuserOffset` | 0 | int | §9.4 |
| `MaxOutlierRejections` | 1 | ≥ 0 | §7.11, §7.10, per-star fits |
| `OutlierRejectionConfidence` | 0.90 | (0.5, 1.0) exclusive | Grubbs test |
| `WeightedHyperbolicFitEnabled` | true | bool | 1/σ weighting |
| `HyperbolicFitModel` | **Hybrid** | enum §1 | §7.10 |
| `FitRejectionCriterion` | RSquared | enum | §9.1 |
| `ReducedChiSquaredRejectionThreshold` | 5.0 | finite | §9.1 |
| `RSquaredRejectionThreshold` | proxy for NINA profile `RSquaredThreshold` (NINA default 0.7) | finite | §9.1 |

### 12.2 Relevant NINA profile settings (defaults from `nina/NINA.Profile/FocuserSettings.cs`)

`AutoFocusStepSize` 50 · `AutoFocusInitialOffsetSteps` 4 · `AutoFocusExposureTime` 4 s ·
`AutoFocusNumberOfFramesPerPoint` 1 · `AutoFocusTotalNumberOfAttempts` 1 ·
`AutoFocusUseBrightestStars` 0 (=all) · `AutoFocusInnerCropRatio` 1 · `AutoFocusOuterCropRatio` 1 ·
`RSquaredThreshold` 0.7 · `AutoFocusMethod` STARHFR · `AutoFocusCurveFitting` TRENDLINES
(HF users typically set HYPERBOLIC/TRENDHYPERBOLIC) · `AutoFocusDisableGuiding` false ·
`UseFilterWheelOffsets` false · `FocalRatio` (telescope settings; used for CFZ).

### 12.3 Inspector options (`InspectorOptions.cs`)

| Option | Default | Range / notes |
|---|---|---|
| `StepCount` | −1 (profile) | sanity ≤ 50 (corrupt-store heal) |
| `StepSize` | −1 (profile) | replay-only override |
| `SignalAmplification` | 2 | clamped ≥ 1 |
| `CenterFocuserBeforeRun` | false | live only |
| `FramesPerPoint` | −1 (profile) | |
| `TimeoutSeconds` | −1 (profile) | |
| `SimpleExposureSeconds` | −1 (AF exposure) | snapshot analysis |
| `DetailedAnalysisExposureSeconds` | −1 (AF exposure) | AF sweep override |
| `NumRegionsWide` | 7 | exposure-analysis grid |
| `MicronsPerFocuserStep` | −1 (unset ⇒ warn, assume 1.0 in sensor model; NaN µm in tables) | |
| `SensorROI` | 1.0 | clamped [0.1, 1.0] |
| `CornersROI` | 1.0 | clamped [0.1, 1.0] |
| `SensorCurveModelEnabled` | false | paraboloid model on/off |
| `AcceptableRSquaredMin` | 0.05 | model R² gate (§11.6) |
| `FixedSensorCenter` | true | pins x0=y0=0 |
| `AstigmaticCurvatureEnabled` | false | 7-param model |
| `UseRANSAC` | true | frame alignment |
| `UseAffineAlignment` | false | diagnostics only |
| `RejectBadBrightnessMatches` | false | tolerance search (§11.5 D) |
| `StartingBrightnessDiff` | −1 (auto ⇒ 0.1) | |
| `RejectBadlyFittingMatches` | true | per-star Grubbs budget |
| `MaxStarsPerRegion` | −1 (unlimited) | |
| `InterpolationEnabled` | false (hard-off) | §11.7 |
| `InterpolationAlgo` / `Amount` | MultiQuadric / Medium | §11.7 |
| `EccentricityColorMapEnabled` | true | display |
| `FrameReviewEnabled` | false | retains frames |
| `LoopingExposureAnalysisEnabled` | false | repeat snapshot |

### 12.4 Hardcoded constants (not user-tunable)

| Constant | Value | Where |
|---|---|---|
| Trend-point inclusion margin | min.y + 0.1 | TrendlineFitting |
| Hyperbolic input y floor | 0.1 | all `Create`s |
| Weight σ floor | 1e-6 (`1/max(|σ|,1e-6)`) | all fits |
| σ regularization floor fraction | 0.2 × median | WeightRegularization |
| Huber multiplier / iters / tol | 1.5 / 10 / 1e-6 | AlglibHyperbolicFitting |
| LM tol / max iters (hyperbolic) | 1e-6 / 1000 | SolveOnce |
| Tilted skew bound / b-span cap | 0.9 / 2.0 | TiltedHyperbolic |
| SmoothBlend width fraction | 0.25 × median spacing | SmoothBlend |
| Hybrid F-test confidence | 0.95 | SelectBestModel |
| LOO minimum points / refits | 5 / 2 | ComputeLeaveOneOut… |
| MAD scale factor | 1.483 | MedianMAD |
| CFZ formula | 2.44·f²·0.55 µm | inspector |
| Tilt OK factor / Curvature OK factor | 0.25× / 1.5× CFZ | analyses |
| Per-star R² gate | 0.90 | SensorAberrationCalculator |
| Model reduced-χ² cap | 5.0 | SensorAberrationCalculator |
| "fit too good" | 1−R² < 0.005 | SensorAberrationCalculator |
| Min stars for paraboloid | 9 (points), 10 (quality warn/search) | SensorModel |
| Min matched frames per star | 5 | FitImages |
| Winsorization σ / iters | 2.5 / 10 | SolveWinsorizedResiduals |
| Paraboloid LM tol | 1e-8 | NonLinearLeastSquaresSolver |
| HFR σ SNR floor / contamination × | 0.2 / 3.0 | EstimateHfrStdDev |
| Match radii RANSAC/non | 10 / 30 px | SensorModel |
| Shape distance strict/relaxed | 0.02 / 0.05 | RANSAC align |
| Ref triangle target / warn floor | 100–200 / 30 | RANSAC align |
| Scale sanity band | [0.9, 1.1] | all alignment retries |
| Brightness search: factor/caps | ×÷1.5, up ≥ 3, down ≤ 0.01, 12 iters | RegisterStarsAndFit |
| Saturated-HFR min unsaturated | 3 | HFR aggregation |
| Backfocus arrow noise/large | 10 / 50 µm | tilt guidance |
| Tilt arrow noise/small/large | 0.005 / 0.1 / 0.5 | tilt guidance |
| Pitch mismatch fraction | 0.15 | tilt guidance |

---

## 13. Edge cases & failure paths (checklist)

1. Second AF started while one runs ⇒ rejected (atomic guard); guard released on all paths.
2. Timeout (default 10 min) cancels everything; focuser restored to initial position.
3. Filter change failure ⇒ warn, continue with imaging filter.
4. Capture returns null ⇒ up to 3 tries; subsample capture error ⇒ retry without subsample.
5. Detection/analysis exception ⇒ point recorded as (0.0, NaN) — a failure point, not a crash.
6. `y ≤ 0` points: excluded from all fits, listed as rejected, count toward
   `TooManyFailedMeasurements` (threshold = offset_steps) and extend sweep target ranges.
7. Focuser hits travel limit (move didn't advance) ⇒ run fails immediately.
8. < 3 valid points ⇒ no fit (region result NaN; validation fails as FitQuality).
9. Hyperbolic solve failure ⇒ logged; trendline may still complete; hyperbolic gates skipped
   (null fitting is not gated), but a null final focus point fails validation.
10. Grubbs: needs > 3 points; zero/NaN MAD falls back to stddev; zero/NaN stddev ⇒ no outlier.
11. Regularization: no positive finite σ ⇒ all σ = 1 (unweighted); unknown σ ⇒ median.
12. Huber IRLS: solve failure mid-loop returns last good solution; zero MAD stops reweighting.
13. σ(x_min) larger than the sweep span ⇒ reported NaN (not localized).
14. Hybrid: < 2 solved models ⇒ no consensus rejection; nothing survives ⇒ TiltedHyperbola
    fallback (fit may be null).
15. Out-of-bounds final point: deferred failure; move clamped to swept range (or skipped when
    HFR validation off); diagnostic exposure captured; retry-eligible.
16. HFR regression / out-of-bounds get ONE extra re-centered retry (never repeated, never if
    the calculated center equals the current center or is negative).
17. Initial HFR = 0 ⇒ InitialHFRFailedException ⇒ attempt retry; at validation time missing
    initial/final HFR are distinct failure modes.
18. Retry loop hard backstop: attempts + 1 iterations max.
19. Failed run: focuser restored, failed metadata.json written, report saved with focus point (−1, 0).
20. Inspector: regions that fail to fit ⇒ warning "<n> regions failed to produce a focus curve";
    NaN corner in tilt table.
21. Sensor model: no detected stars ⇒ ArgumentException; < 9 points ⇒ retry at other brightness
    tolerance or fail; model unacceptable (R² < min AND χ²_red > 5) ⇒ hard failure with message.
22. `MicronsPerFocuserStep` unset: sensor model assumes 1 µm/step (one-time warning); tilt table
    micron columns are NaN; backfocus µm NaN (steps still shown).
23. Alignment: every failure path leaves the frame unaligned rather than wrongly aligned
    (scale guard 0.9–1.1); duplicate star positions skipped; frames failing all passes matched
    at 30 px radius.
24. Winsorized loop: iteration that does not improve weighted R² returns the previous solution.
25. Covariance: pinned params excluded from the normal matrix; singular ⇒ null ⇒ NaN std errors
    (UI dash) — never a crash.
26. FWHM/eccentricity panels hidden when no PSF-fitted stars; "final" folder missing on replay ⇒
    panels skipped with explanation (needs Validate HFR Improvement to have been on).
27. Region 6 duplicate completion at same focuser position (replay) ⇒ ignored, no throw.

---

## 14. Source map (MPL-2.0 provenance)

All paths relative to `references/hocusfocus/Joko.NINA.Plugins/Joko.NINA.Plugins.HocusFocus/`
unless prefixed `nina:` (= `references/nina/`).

| Algorithm / section | File | Lines |
|---|---|---|
| Run control flow, retries, guard (§3) | `AutoFocus/AutoFocusEngine.cs` | 448–475, 1196–1321, 1616–1737 |
| Exposure loop, capture, subsample (§4.1) | `AutoFocus/AutoFocusEngine.cs` | 576–760, 995–1051, 1368–1396 |
| Per-frame HFR aggregation (§4.2) | `StarDetection/HocusFocusStarDetection.cs` | 577–710 |
| SEM pooling (§4.3) | `Utility/CvImageUtility.cs` | 757–797 |
| SafeDisplayError (§4.4) | `AutoFocus/AutoFocusEngine.cs` | 810–819 |
| Point completion / dedup | `AutoFocus/AutoFocusEngine.cs` | 762–808 |
| Initial HFR baseline (§5) | `AutoFocus/AutoFocusEngine.cs` | 906–917, 1053–1072 |
| Blind sweep (§6) | `AutoFocus/AutoFocusEngine.cs` | 1063–1194 |
| Live fit + Grubbs loop (§7.11) | `AutoFocus/AutoFocusEngine.cs` | 86–168 |
| Final focus point (§7.12) | `AutoFocus/AutoFocusEngine.cs` | 340–380 |
| Weight regularization (§7.1) | `StarDetection/WeightRegularization.cs` | 31–74 |
| Grubbs test, MedianMAD (§7.2) | `Utility/MathUtility.cs` | 80–101, 133–185 |
| Hyperbolic base: LM, Huber, R², χ², σ(min), LOO (§7.5) | `StarDetection/AlglibHyperbolicFitting.cs` | 35–247, 560–782 |
| Hybrid selection, F-test, ranking (§7.10) | `StarDetection/AlglibHyperbolicFitting.cs` | 249–552 |
| Symmetric hyperbola (§7.6) | `StarDetection/HyperbolicFittingAlglib.cs` | 27–122 |
| Tilted hyperbola (§7.7) | `StarDetection/TiltedHyperbolicFittingAlglib.cs` | 35–180 |
| Smooth blend (§7.8) | `StarDetection/SmoothBlendHyperbolicFittingAlglib.cs` | 32–216 |
| Uneven blend (§7.9) | `StarDetection/HyperbolicUnevenFittingAlglib.cs` | 36–172 |
| Trendline fit (§7.3) | `nina:NINA.WPF.Base/Utility/AutoFocus/TrendlineFitting.cs` + `Trendline.cs` | 91–114; 27–64 |
| Quadratic fit (§7.4) | `nina:NINA.WPF.Base/Utility/AutoFocus/QuadraticFitting.cs` | 71–89 |
| Validation & gates (§9) | `AutoFocus/AutoFocusEngine.cs` | 1398–1594 |
| Fit-model finalization hooks | `AutoFocus/AutoFocusEngine.cs` | 249–338 |
| Save/replay (§10) | `AutoFocus/AutoFocusEngine.cs` | 821–904, 1739–2025, 2275–2359 |
| Options + defaults (§12.1) | `AutoFocus/AutoFocusOptions.cs` | 51–97 (defaults), rest setters |
| Engine options struct | `Interfaces/IAutoFocusEngine.cs` | 66–117 |
| GetRSquared | `Interfaces/IAutoFocusEngine.cs` | 196–206 |
| NINA profile defaults (§12.2) | `nina:NINA.Profile/FocuserSettings.cs` | 30–60 |
| Inspector regions (§11.1) | `AutoFocus/InspectorVM.cs` | 1369–1390 |
| Inspector run + overrides (§11.2) | `AutoFocus/InspectorVM.cs` | 411–536, 1110–1184 |
| Backfocus (§11.3) | `AutoFocus/InspectorVM.cs` | 1550–1582 |
| 4-corner tilt plane (§11.4) | `AutoFocus/TiltModel.cs` | 25–166 |
| Region tiling helper | `Inspection/SensorAberrationCalculator.cs` | 124–141 |
| Acceptance rules, constants (§11.6) | `Inspection/SensorAberrationCalculator.cs` | 26–117 |
| Sensor model pipeline (§11.5) | `Inspection/SensorModel.cs` | 118–583 (search), 585–810 (per-star fits), 850–1351 (alignment+matching) |
| Paraboloid model + solver (§11.5 F) | `Inspection/SensorParaboloidModel.cs` | 70–308 (model), 311–432 (solver) |
| NL least squares, winsorized, covariance | `Utility/NonLinearLeastSquaresSolver.cs` | 61–142 (winsorized), 190–279 (LM), 328–515 (stats/cov) |
| Aberration result + analyses (§11.6) | `Inspection/SensorModelAberrationResult.cs` | 270–462 |
| Snapshot grid analysis (§11.8) | `AutoFocus/InspectorVM.cs` | 896–1066 |
| Screw geometry (§11.9) | `TiltAdapterWizard/TiltScrewGeometry.cs` | 30–192 |
| Guidance arrows (§11.9) | `AutoFocus/InspectorVM.cs` | 1875–2056 |
| Inspector options + defaults (§12.3) | `AutoFocus/InspectorOptions.cs` | 53–125 |
| Tilt domain conventions | `.claude/docs/tilt-domain.md` | whole file |

License note: everything above is MPL-2.0 (HocusFocus © George Hilios; NINA core © Stefan Berg
and contributors). A Rust reimplementation from this dossier is a derived work of the
*algorithms*, not the files; keep this dossier's provenance section with the port and preserve
MPL attribution in NOTICE files.

---

## 15. Recommended for AstroDeck

**Autofocus (adopt, in this priority):**
1. **Sweep**: HF blind sweep (§6) with profile defaults step=50 (rig-specific), offset_steps=4,
   FramesPerPoint=1. Pipelined detection is worth it; a simple `tokio` semaphore reproduces
   `MaxConcurrent`.
2. **Measurement**: median HFR + 1.483·MAD per frame (Median mode) — more robust than NINA's
   mean; SEM pooling for FramesPerPoint > 1; failed frames → (0, NaN).
3. **Fit**: implement Symmetric + TiltedHyperbola first (Tilted covers the asymmetric case with
   a closed-form minimum and is HF's live/fallback model), weighted (default ON) with the §7.1
   regularization, Huber IRLS ON, Grubbs rejection budget 1 @ 0.90 confidence. Add
   SmoothBlend + UnevenBlend + full Hybrid selection later — Hybrid is the source default and
   worth reaching parity on, but Tilted-only is a sound v1 (that is literally what Hybrid shows
   live). Skip Gaussian/contrast detection initially (STARHFR only).
4. **Defaults**: TRENDHYPERBOLIC as the curve fitting default (robust: averages trendline
   intersection with hyperbolic minimum), `RSquaredThreshold = 0.7`, criterion = RSquared,
   `ValidateHfrImprovement = true`, `HFRImprovementThreshold = 0.15`, timeout 600 s,
   `TotalNumberOfAttempts = 1` + the single re-centered retry (§3) — that retry is cheap
   insurance and a genuine HF improvement over stock NINA.
5. **Diagnostics to surface**: σ(best focus) (§7.5 covariance) and reduced χ² — trivially
   computed and hugely useful in a UI; LOO stability optional (n refits, do it post-run only).
6. **Persist runs** in HF's folder format (§10) — free replay/debugging, and AstroDeck can
   re-analyze saved NINA/HF runs for validation against the reference implementation.

**Inspector (adopt):**
1. **Region layout** §11.1 exactly (center ninth + 4 corner boxes, SensorROI/CornersROI = 1.0
   defaults), backfocus = corners-mean − center in steps × µm/step, CFZ = 2.44·f²·0.55.
2. **4-corner tilt plane** (§11.4) as the v1 tilt display — trivial and matches user
   expectations from ASIAIR/HF.
3. **Per-star paraboloid model** (§11.5–11.6) as v2: it is the genuinely differentiating
   feature (separates tilt from curvature, gives µm screw corrections). Implement with
   `FixedSensorCenter = true`, isotropic curvature, winsorized 2.5σ loop, per-star R² ≥ 0.90,
   1/σ² weighting via the covariance-propagated per-star σ. The RANSAC alignment ladder is the
   most complex part; for a tracking mount taking a short AF sweep, start with alignment OFF
   (30 px match radius) and add similarity-RANSAC later — the source works without alignment,
   just less reliably at long focal lengths.
4. **Signal amplification ×2** for inspector sweeps (finer steps, same range) — cheap and
   materially improves per-star fits.
5. **Skip**: RBF interpolation grid (dead code upstream), affine alignment (diagnostics only),
   the brightness-tolerance search (`RejectBadBrightnessMatches` defaults off upstream; the
   simple pass is deterministic and adequate), fast-focus options (vestigial).
6. **Screw guidance** (§11.9) is pure geometry with no dependencies — adopt once tilt-adapter
   configuration exists in AstroDeck; the µm-per-screw numbers are the actionable output.

**Numeric stack mapping**: alglib `minlm` → `levenberg-marquardt` crate or hand-rolled LM
(bounded, scaled — bounds/scale matter; box-project each step); Student-t / F / χ² inverse
CDFs → `statrs`; the OLS plane fit is 4 points — solve the 3×3 normal equations directly.
Determinism requirements that matter for testing parity: sort points by x before fitting,
fixed candidate order in Hybrid, index-ordered reduction in LOO and per-star loops.
