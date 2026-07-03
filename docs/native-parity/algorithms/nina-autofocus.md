# NINA Autofocus — Algorithm Dossier (for clean-room Rust reimplementation)

**Source**: N.I.N.A. (Nighttime Imaging 'N' Astronomy), `https://bitbucket.org/Isbeorn/nina.git`,
commit `7c9de0c4202f2d054f58b0cb6b15d561f8b6b4b2` (2025-06-05, master, shallow clone at
`C:/Users/bear/astro/references/nina`). License: **MPL-2.0**. This dossier documents behavior
extracted from that snapshot so the Rust implementer never needs to read the C# source.
All file/line references are to that snapshot (see Source Map at the end).

Math that lives inside the Accord.NET NuGet package (not vendored in the NINA tree) is
reconstructed here from its published behavior; where the exact internal constant is not
observable from NINA's tree it is flagged in **Gaps** and a safe equivalent is specified.

---

## 1. Data model and conventions

- A **focus point** is `(x, y, err)`:
  - `x` = focuser position in **steps** (integer positions; stored as f64).
  - `y` = the "measure": average star **HFR in pixels** (STARHFR method) or **contrast value**
    (CONTRASTDETECTION method).
  - `err` = 1-sigma error of `y` (see §4.3). Clamped to `>= 0.001`; forced to `1000` when the
    measurement failed (0 stars detected).
- The focus point list is kept **sorted ascending by x** (insertion sort on add;
  `FocusPointComparer` compares only `x`).
- STARHFR curves are **valleys** (minimize HFR); CONTRASTDETECTION curves are **peaks**
  (maximize contrast, fit with a Gaussian).
- Weighted fits use weight `w_i = 1 / err_i²` unless stated otherwise.
- **Rounding**: every `round()` in this dossier is C# `Math.Round`, i.e. **banker's rounding
  (half-to-even)** — NOT Rust's `f64::round` (half-away-from-zero). It matters wherever a
  true `.5` can occur: the TREND* combo averages of two integer candidates (§6.5, e.g.
  `(5+6)/2 = 5.5 → 6` but `(4+5)/2 = 4.5 → 4`) and the temperature-slope step delta (§11).
  Use `f64::round_ties_even` in Rust.
- **Tie-breaking in min/max scans**: the fitting classes select extreme points with LINQ
  `Aggregate((l, r) => l.Y < r.Y ? l : r)` (resp. `>`), which keeps the RIGHT operand on
  equality — so on ties the **last point in enumeration order wins** (§6.1 pivot, §6.3
  initial guess, §6.4 start values). Enumeration order is the focus-point list order,
  i.e. ascending x. (Exception: the HFR-increase trigger's `origin = min HFR` fold keeps
  the accumulator on ties — there the **first** wins.)

```rust
struct FocusPoint { x: f64, y: f64, err: f64 }        // err = stdev of y
struct MeasureAndError { measure: f64, stdev: f64 }
enum AFMethod { StarHFR, ContrastDetection }
enum AFCurveFitting { Trendlines, Parabolic, TrendParabolic, Hyperbolic, TrendHyperbolic }
enum ContrastDetectionMethod { Sobel, Laplace, Statistics }
enum BacklashModel { Absolute, Overshoot }
```

---

## 2. Configuration knobs (profile settings)

From `FocuserSettings.SetDefaultValues()` and property clamps
(`NINA.Profile/FocuserSettings.cs:31-53` and per-property setters):

| Setting | Default | Valid range / clamp | Meaning |
|---|---|---|---|
| `AutoFocusStepSize` | **50** | int, no clamp | focuser steps per sweep step |
| `AutoFocusInitialOffsetSteps` | **4** | clamped **1..=10** | half-width of sweep in steps-of-stepsize |
| `AutoFocusExposureTime` | **4** s | double, no clamp | per-frame exposure (overridable per filter) |
| `AutoFocusMethod` | **STARHFR** | STARHFR \| CONTRASTDETECTION | measurement type |
| `AutoFocusCurveFitting` | **HYPERBOLIC** | 5 variants (§6) | fit used to pick focus (STARHFR only) |
| `ContrastDetectionMethod` | **Statistics** | Sobel \| Laplace \| Statistics | contrast metric |
| `AutoFocusNumberOfFramesPerPoint` | **1** | getter clamps to `>= 1` | exposures averaged per point |
| `AutoFocusTotalNumberOfAttempts` | **1** | clamped **1..=5** (getter and setter) | full-run reattempts on bad AF |
| `AutoFocusDisableGuiding` | **false** | bool | stop guiding during AF |
| `FocuserSettleTime` | **0** s | int | wait after every focuser move (and after overshoot leg) |
| `AutoFocusInnerCropRatio` | **1** | `<1` activates ROI/subsample | central crop for analysis |
| `AutoFocusOuterCropRatio` | **1** | `<1` activates donut ROI | outer crop; with inner forms annulus |
| `AutoFocusUseBrightestStars` | **0** | int; `0` = disabled | limit analysis to N brightest stars |
| `AutoFocusBinning` | **1** | setter clamps to `<= 4` | camera binning for AF frames |
| `BacklashIn` | **0** | int steps | backlash amount for IN moves |
| `BacklashOut` | **0** | int steps | backlash amount for OUT moves |
| `BacklashCompensationModel` | **OVERSHOOT** | ABSOLUTE \| OVERSHOOT | §8 |
| `RSquaredThreshold` | **0.7** | clamped **0..=1**; `0` disables R² gate | fit-quality gate (§7.1) |
| `AutoFocusTimeoutSeconds` | **600** | int | **dead in this snapshot** — defined in profile but referenced nowhere else in the tree |

Related settings read by AF from other profile sections:
- `ImageSettings.StarSensitivity`, `ImageSettings.NoiseReduction` — passed to star/contrast detection.
- `ImageSettings.DebayerImage`, `ImageSettings.AnnotateImage` — pixel format / display only.

Per-filter overrides (`NINA.Core/Model/Equipment/FilterInfo.cs:44-56`, deserialization defaults):
- `AutoFocusExposureTime` default **-1** (sentinel "unset"; a stored `0` is coerced to `-1`);
  used when `> -1`.
- `AutoFocusBinning` default **1×1**; used when non-null.
- `AutoFocusGain` / `AutoFocusOffset` default **-1**; used when `> -1`.
- `AutoFocusFilter` (bool): marks the filter used for all AF runs when
  `UseFilterWheelOffsets == true`.
- `FocusOffset` (int steps): focuser offset applied on filter change (§9).

Hard-coded constants:
- `maximumFocusPoints = FramesPerPoint * InitialOffsetSteps * 10` (AutoFocusVM.cs:144)
  — absolute cap on collected points (default 1·4·10 = 40).
- Stdev floor **0.001**; failed-measure stdev **1000** (AutoFocusVM.cs:563,568).
- Trend-point exclusion band: **min.y + 0.1** HFR (STARHFR), **max.y − 0.01** (contrast) (§6.1).
- Hyperbolic solver: 20 grid steps/parameter, range halved per cycle, stop when
  error improvement `< 1e-4` or error `<= 1e-4` or **30** cycles (§6.3).
- Gaussian LM: **30** iterations, tolerance **0** (runs all 30) (§6.4).
- Final-HFR validation factor **1.15** (§7.3).
- Guiding-restart wait timeout: **1 minute** (AutoFocusVM.cs:311).
- Focuser move hard timeout: **10 minutes** (FocuserVM.cs:203).
- Image-acquisition retries per point: up to **3** attempts (AutoFocusVM.cs:659-662).

---

## 3. Top-level routine (`StartAutoFocus`)

Source: `AutoFocusVM.cs:136-322`. Rust-flavored pseudocode; every branch mirrors the source.

```rust
fn start_auto_focus(imaging_filter, token, progress) -> Option<AutoFocusReport> {
    clear_charts();
    let max_points = frames_per_point * initial_offset_steps * 10;
    let mut attempts = 0;
    let mut initial_position = focuser.position();
    let mut initial_hfr = f64::NAN;
    let mut temp_comp_was_on = false;
    let mut guiding_stopped = false;
    let mut completed = false;
    let mut report = None;

    // ---- result of the whole function is `report`; cleanup runs in all paths (finally) ----
    let result = (|| {
        // 1. Disable focuser temperature compensation for the duration of AF
        if focuser.temp_comp_available() && focuser.temp_comp() {
            temp_comp_was_on = true;
            focuser.set_temp_comp(false);
        }
        // 2. Optionally stop guiding
        if settings.auto_focus_disable_guiding {
            guiding_stopped = guider.stop_guiding()?;
        }
        // 3. Switch to the AF filter (may move focuser by filter offset delta, §9)
        let af_filter = set_autofocus_filter(imaging_filter)?;
        initial_position = focuser.position();       // re-read AFTER filter change

        // 4. Baseline HFR — ONLY when method is STARHFR and the R² gate is disabled
        if method == StarHFR && settings.r_squared_threshold <= 0.0 {
            initial_hfr = get_average_measurement(af_filter, frames_per_point)?.measure;
        }

        // 5. Sweep direction: reverse iff OVERSHOOT model with only IN backlash configured
        let reverse = settings.backlash_model == Overshoot
                   && settings.backlash_in > 0 && settings.backlash_out == 0;

        loop {                                        // reattempt loop
            attempts += 1;
            let offset_steps = settings.initial_offset_steps;   // e.g. 4
            run_sweep(af_filter, offset_steps, reverse, max_points, initial_position)?; // §3.1
            // (run_sweep returns Err(NotEnoughSpread) -> move to initial_position, return None)

            let final_point = determine_final_focus_point();     // §6.5
            let last_af_point = ReportPoint { point: final_point,
                temperature: focuser.temperature(), timestamp: now(), filter: af_filter.name };
            let good = validate_calculated_focus_position(final_point, af_filter, initial_hfr)?; // §7

            let duration = stopwatch.elapsed();
            report = Some(generate_report(...));                 // JSON report, §10 — even if bad

            if !good {
                if attempts < settings.total_number_of_attempts {
                    focuser.move_to(initial_position)?;          // restart from initial position
                    clear_all_points_and_fits();
                    continue;                                    // reattempt
                } else {
                    focuser.move_to(initial_position)?;          // give up, restore
                    return Ok(None);                             // AF FAILED (report not returned)
                }
            }
            break;
        }
        completed = true;
        broadcast_successful_af(report.temperature, report.calculated.position,
                                report.filter, report.timestamp);  // feeds triggers, §11
        Ok(report)
    })();

    // ---- finally (always) ----
    if !completed {
        // covers: cancel, exception, failed validation, not-enough-spread
        focuser.move_to(initial_position);            // best-effort, errors logged only
        focus_points.clear();
    }
    filter_wheel.change_filter(imaging_filter);       // restore imaging filter (best-effort)
    if focuser.temp_comp_available() && temp_comp_was_on {
        focuser.set_temp_comp(true);                  // restore temp comp
    }
    if guiding_stopped {
        // restart guiding; warn if it takes > 1 minute
        wait_at_most(1.min, guider.start_guiding(force_calibration=false));
    }
    result   // cancelled -> None; error -> notify + None
}
```

Failure semantics:
- **Cancellation** at any await → position restored, points cleared, filter/tempcomp/guiding
  restored, returns `None`.
- **Any exception** (camera failure after retries, focuser move error, empty-collection
  aggregate in a fit, …) → error notification, same cleanup, returns `None`.
- A **successful** run leaves the focuser at the validated focus position (the validation step
  itself moved there, §7.3) and returns the report.

### 3.1 Sweep strategy (`GetFocusPoints` + extension loop)

Sources: `AutoFocusVM.cs:176-237` (loop) and `AutoFocusVM.cs:531-575` (`GetFocusPoints`).

Initial pass (per attempt):

```rust
// nr_of_steps = offset_steps + 1;  offset = offset_steps;  sign = if reverse {-1} else {+1}
// GetFocusPoints(filter, nr_of_steps, offset, reverse):
fn get_focus_points(filter, nr_of_steps: u32, offset: i32, sign: i32) {
    let step_size = settings.auto_focus_step_size;
    if offset != 0 {
        // one relative move to the sweep start: +offset*step_size (OUT) or IN when reversed
        focus_position = focuser.move_relative(sign * offset * step_size)?;
    }
    for i in 0..nr_of_steps {
        let current = focus_position;
        let measurement_task = start_average_measurement(filter, frames_per_point); // exposures start now
        if i < nr_of_steps - 1 {
            // move happens WHILE the last frame is still being analyzed (pipelined)
            focus_position = focuser.move_relative(sign * -step_size)?;   // step IN (or OUT if reversed)
        }
        let mut m = measurement_task.await?;
        if m.measure == 0.0 { m.stdev = 1000.0; }       // no stars: keep point, poison its weight
        focus_points.insert_sorted(FocusPoint { x: current as f64, y: m.measure,
                                                err: m.stdev.max(0.001) });
        set_curve_fittings(method, fitting);            // fits recomputed after EVERY point (§6)
    }
}
```

So with defaults the first pass measures **5 points**: `P+4s, P+3s, P+2s, P+1s, P`
(s = step size, P = starting position). It does **not** go below P initially — the extension
loop below fills in the left side adaptively. With `reverse == true` all signs flip:
first pass covers `P-4s .. P`, stepping OUT.

Extension loop (runs after the initial pass, using the trendline fit computed so far):

```rust
let mut left  = trendline.left_trend.points.len();
let mut right = trendline.right_trend.points.len();
loop {
    if left == 0 && right == 0 {
        // no trend points at all on either side: hopeless data
        warn("not enough spread points"); focuser.move_to(initial_position);
        return Err(NotEnoughSpread);            // whole AF run fails, no reattempt
    }
    // zero-Y points on a side count toward that side's quota
    let zeros_left  = count(p in focus_points where p.x < trend_min.x && p.y == 0.0);
    let zeros_right = count(p in focus_points where p.x > trend_min.x && p.y == 0.0);

    if left < offset_steps && zeros_left < offset_steps {
        // need more points left of the minimum: go to leftmost measured point, then 1 step further IN
        if focuser.position() != round(focus_points.first().x) {
            focuser.move_to(round(focus_points.first().x))?;
        }
        get_focus_points(filter, 1, offset = -1, sign = +1);   // note: reverse NOT applied here
    } else if right < offset_steps && zeros_right < offset_steps {
        // need more points right of the minimum: go to rightmost point, then 1 step further OUT
        if focuser.position() != round(focus_points.last().x) {
            focuser.move_to(round(focus_points.last().x))?;
        }
        get_focus_points(filter, 1, offset = 1, sign = +1);
    }
    left  = trendline.left_trend.points.len();
    right = trendline.right_trend.points.len();

    if focus_points.len() > max_points {   // zigzag / runaway guard
        error("maximum number of focus points exceeded"); break;   // proceeds to fitting anyway!
    }
    if focuser.position() == 0 {
        error("focuser reached position 0"); break;                // proceeds to fitting anyway!
    }
    check_cancelled()?;

    // continue until BOTH sides have offset_steps points (trend points + zero-Y points)
    if right + zeros_right >= offset_steps && left + zeros_left >= offset_steps { break; }
}
```

Key properties to preserve:
- The left side is extended first, one step-size at a time, until `offset_steps` left-trend
  points exist; then the right side. The trendline "minimum" (§6.1) is recomputed after every
  new point, so the sides re-balance as the estimated minimum shifts.
- Points with `y == 0` (no stars) are never removed; they count toward side quotas and carry
  weight `1/1000² = 1e-6` in weighted fits.
- Both break-outs (max points, position 0) still fall through to fitting + validation —
  validation is what ultimately fails the run.
- `reverse` only affects the *initial* pass; extension moves use `sign = +1` semantics
  (offset −1 = IN, +1 = OUT).

---

## 4. Per-point measurement

### 4.1 Exposure (`TakeExposure`, AutoFocusVM.cs:614-665)

- Exposure time: filter's `AutoFocusExposureTime` if `> -1`, else profile
  `AutoFocusExposureTime` (default 4 s). Image type `SNAPSHOT`.
- Binning: filter's `AutoFocusBinning` if set, else profile `AutoFocusBinning` (square).
- Gain/Offset: filter's `AutoFocusGain`/`AutoFocusOffset` if `> -1`, else camera defaults.
- **Sub-sampling**: if (`InnerCropRatio < 1` or `OuterCropRatio < 1`) and camera supports
  sub-sampling, capture only a centered rectangle of the sensor:
  `ratio = if outer >= 1 {inner} else {outer}`; `w = round(sensor_w * ratio)`,
  `h = round(sensor_h * ratio)`, origin `((sensor_w - w)/2, (sensor_h - h)/2)` (rounded).
  (Source quirk: the guard is `inner < 1 || outer < 1 && can_subsample` — `&&` binds tighter,
  so a rect is produced when `inner < 1` even without sub-sample support; a capture failure
  then propagates because the fallback only strips the subsample when `IsSubSampleEnabled()`.)
- On camera error **with** sub-sampling active: retry once without sub-sample.
  On `null` image: retry, max 3 total attempts, then the run fails with an exception upstream.

### 4.2 Evaluation (`EvaluateExposure`, AutoFocusVM.cs:380-462)

- Image is auto-stretched before analysis **except** for CONTRASTDETECTION + Statistics.
- **STARHFR**: run star detection with
  `IsAutoFocus = true`, `Sensitivity`, `NoiseReduction`, `NumberOfAFStars = AutoFocusUseBrightestStars`;
  ROI parameters:
  - if `inner < 1` and NOT sub-sampled: `use_roi = true; inner_crop = inner`.
  - if `outer < 1`: `use_roi = true`; if sub-sampled: donut mode
    `outer_crop = 0.0; inner_crop = inner / outer` (frame is already cropped to `outer`);
    else `outer_crop = outer`.
  - Result: `measure = AverageHFR`, `stdev = HFRStdDev` (NaN → 0).
- **CONTRASTDETECTION**:
  - Statistics: `measure = 100 * stdev(all pixels) / mean(all pixels)`, `stdev = 0.01`
    (unstretched data).
  - Sobel / Laplace (§4.4): `measure = AverageContrast`, `stdev = ContrastStdev` (NaN → 0).

HFR statistics over detected stars (`StarDetection.cs:237-248`):
`AverageHFR = mean(HFR_i)`; `HFRStdDev = sqrt( Σ(HFR_i − mean)² / (n − 1) )` (sample stdev;
`0` if n ≤ 1). Star radius outlier rejection before this: with
`avg = Σr_i/n` and `σ = sqrt( (Σr_i² − n·avg²) / n )` (**population** stdev of radii, ÷n —
unlike the HFR σ), keep stars with `avg−1.5σ <= radius <= avg+1.5σ`
(or `avg−1.5σ .. avg+2σ` at Highest sensitivity) (`StarDetection.cs:364-374`).

**Brightest-star mode** (`AutoFocusUseBrightestStars = N > 0`, `StarDetection.cs:380-395`):
rank stars by `0.3*radius + 0.7*mean_brightness` descending, keep top N. The source also has
a "match previous positions" mode (`MatchStarPositions`) that picks, for each remembered
position, the nearest detected star — but core NINA never populates `MatchStarPositions`
during AF in this snapshot, so every AF frame independently re-picks its N brightest stars.

### 4.3 Averaging multiple frames per point (`EvaluateAllExposures`, AutoFocusVM.cs:517-528)

With `n = AutoFocusNumberOfFramesPerPoint` frames per position:

- `measure = (1/n) Σ measure_i`
- `stdev = sqrt( (1/n) Σ stdev_i² )`  — RMS of the per-frame stdevs (NOT the standard error
  of the mean; there is no ÷n² — reproduce exactly).

Then (in `GetFocusPoints`): `if measure == 0 { stdev = 1000 }` and `err = max(0.001, stdev)`.
There is **no other outlier rejection at the point level** — bad points are handled purely by
weighting (`w = 1/err²`) and by the trend-point selection band (§6.1).

### 4.4 Contrast detection details (`ContrastDetection.cs:33-126`)

Pipeline: convert 16→8 bpp; crop to `InnerCropRatio` if ROI; optional median noise reduction;
resize so width ≤ 1552 px; then:
- **Laplace**: convolve with Laplacian-of-Gaussian kernel — size/σ by noise reduction:
  None/Median → 7×7 σ=1.0; Normal → 9×9 σ=1.4; High → 11×11 σ=1.8; Highest → 13×13 σ=2.2.
  `AverageContrast = mean of non-black pixels of the convolved image`; `ContrastStdev = 0.01`.
- **Sobel**: optional Gaussian blur (Normal=1, High=2, Highest=3 passes), convolve with the
  fixed 5×5 kernel `[[-1,-2,0,2,1],[-2,-4,0,4,2],[0,0,0,0,0],[2,4,0,-4,-2],[1,2,0,-2,-1]]`;
  same statistics. `ContrastStdev = 0.01` always (a fixed nominal error).

---

## 5. Curve-fitting recomputation policy

`SetCurveFittings(method, fitting)` (AutoFocusVM.cs:119-134) runs after **every** point:
- Trendline fit: always (both methods; used by the sweep-extension logic).
- STARHFR: quadratic fit if `points >= 3` and fitting ∈ {PARABOLIC, TRENDPARABOLIC};
  hyperbolic fit if `points >= 3` and fitting ∈ {HYPERBOLIC, TRENDHYPERBOLIC}.
- CONTRASTDETECTION: Gaussian fit if `points >= 3`.

`DetermineFinalFocusPoint()` (AutoFocusVM.cs:338-378) recomputes **all four** fits
unconditionally (trendline, hyperbolic, quadratic, Gaussian) before selecting the answer —
note the Gaussian fit runs even on STARHFR data; if a fit throws (e.g. no nonzero points),
the whole run fails through the generic exception path.

---

## 6. The fitting methods (exact math)

### 6.1 Trendlines (`TrendlineFitting.cs:91-114`, `Trendline.cs:27-64`)

**Pivot selection** (STARHFR): the pivot ("Minimum") is the point minimizing `y + err`
(ties: **last** in list order wins — see §1 tie-breaking note). This avoids picking `y == 0`
no-star points (err = 1000) and low-HFR/high-error noise.

**Trend membership** (STARHFR):
- left trend: all points with `x < min.x` **and** `y > min.y + 0.1`
- right trend: all points with `x > min.x` **and** `y > min.y + 0.1`
- i.e. points within 0.1 HFR of the minimum are treated as "flat tip" and excluded.

CONTRASTDETECTION variant: pivot is the point maximizing `y − err` (ties: last); left/right trends take
points with `y < max.y − 0.01`; no intersection is computed (Gaussian picks focus instead);
the trends are used only for the sweep-side counting and the chart.

**Line fit** (per side, only when the side has **≥ 2 points**; otherwise slope = offset =
R² = 0): weighted least squares of `y = slope·x + offset` with weights `w_i = 1/err_i²`.
Closed form (this is the unique WLS minimizer of `Σ w_i (y_i − s·x_i − o)²`, which is what
Accord's `OrdinaryLeastSquares.Learn(x, y, w)` computes):

```
x̄ = Σ w_i x_i / Σ w_i          ȳ = Σ w_i y_i / Σ w_i
slope  = Σ w_i (x_i − x̄)(y_i − ȳ) / Σ w_i (x_i − x̄)²
offset = ȳ − slope · x̄
```

**R² per side** (weighted coefficient of determination, Accord `RSquaredLoss` semantics):

```
SS_res = Σ w_i (y_i − ŷ_i)²         ŷ_i = slope·x_i + offset
SS_tot = Σ w_i (y_i − ȳ_w)²         ȳ_w = weighted mean of y
R² = 1 − SS_res / SS_tot
```

**Intersection** (`Trendline.Intersect`):

```
if left.slope == right.slope { return (0, 0) }          // parallel -> sentinel
x = (right.offset − left.offset) / (left.slope − right.slope)
y = left.slope * x + left.offset
return ( round(x) as int, y )                            // x rounded to whole step
```

### 6.2 Parabolic / Quadratic (`QuadraticFitting.cs:71-89`)

Model: `y = a·x² + b·x + c`, fit over **all** points (including zero-Y ones, whose weight is
1e-6), weights `w_i = 1/err_i²`. This is weighted polynomial least squares of degree 2:
minimize `Σ w_i (y_i − a x_i² − b x_i − c)²`. Solve the 3×3 weighted normal equations
`(XᵀWX)β = XᵀWy` with `X` rows `[x_i², x_i, 1]` (or QR/SVD — any exact WLS solver; Accord's
`PolynomialLeastSquares{Degree=2}` does the same).

- `R²`: same weighted formula as §6.1 with `ŷ_i = a x_i² + b x_i + c`.
- Minimum: `x_min = round(−b / (2a))` (rounded to int), `y_min = f(x_min)`
  — note `y_min` is evaluated at the **rounded** x.

### 6.3 Hyperbolic (`HyperbolicFitting.cs:75-190`) — NINA's default

Model (parameters `a`, `b`, `p`; no `d` offset term in NINA):

```
x_rel = p − x                                  // p = perfect focus position
t     = asinh(x_rel / b)                       // asinh(z) = ln(z + sqrt(z²+1))
y     = a · cosh(t)                            // cosh(z) = (e^z + e^−z)/2
     ⇔ y = a · sqrt(1 + (p − x)²/b²)
```

- `a` = minimum HFR at perfect focus (units: HFR).
- `b` = shape parameter; asymptote slopes are `±a/b` (HFR per step).
- `p` = focuser position of the minimum.

**Input filtering**: only points with `y >= 0.1` participate (both in the fit and in R²).
If none → return unfit result (`Fitting = None`, `Minimum = (0,0)`, `RSquared = 0`).

**Error function** (weighted, "scaled RMS" — actually root-of-sum, no ÷n):

```
E(p, a, b) = sqrt( Σ_i ((model(x_i; p,a,b) − y_i) / err_i)² )
```

**Initial guess**:

```
lowest  = point with smallest y among y>=0.1 (ties: LAST in list order)
highest = point with largest  y among y>=0.1 (ties: LAST in list order)
if highest.x < lowest.x { highest_x = 2*lowest.x − highest.x }   // mirror so it's to the right
a0 = lowest.y
b0 = sqrt( (highest_x − lowest.x)² · a0² / (highest.y² − a0²) )  // from a·sqrt(1+Δx²/b²)=y_high
p0 = lowest.x
```

**Degenerate-data guard** (before iterating): if `a_range` (=a0) or `b_range` (=b0) is NaN or
0, or `p_range` (= highest_x − lowest.x) is 0 → return unfit result. (This is what makes the
"BadData" cases terminate: with ≤1 distinct nonzero point, lowest == highest → p_range = 0.)

**Solver — shrinking grid search** (not gradient-based; deterministic):

```rust
let (mut a, mut b, mut p) = (a0, b0, p0);
let (mut a_rng, mut b_rng, mut p_rng) = (a0, b0, highest_x - lowest_x);
let mut lowest_err = f64::MAX; let mut old_err = f64::MAX;
let mut cycles = 0;
loop {
    let (pc, ac, bc) = (p, a, b);          // centers for this cycle
    a_rng *= 0.5; b_rng *= 0.5; p_rng *= 0.5;
    // scan p in [pc-p_rng ..= pc+p_rng] step p_rng*0.1   (21 values)
    //   scan a in [ac-a_rng ..= ac+a_rng] step a_rng*0.1 (21 values)
    //     scan b in [bc-b_rng ..= bc+b_rng] step b_rng*0.1 (21 values)
    //       e = E(p1, a1, b1);
    //       if e < lowest_err { old_err = lowest_err; lowest_err = e; (a,b,p)=(a1,b1,p1); }
    cycles += 1;
    if !(old_err - lowest_err >= 1e-4 && lowest_err > 1e-4 && cycles < 30) { break; }
}
```

Notes: `old_err` is the *previous best* (updated only when a new best is found), so the loop
stops one cycle after improvement stalls; max 30 cycles ≈ 30·21³ ≈ 278k evaluations worst
case. Ranges halve every cycle, so precision after k cycles ≈ initial_range / 2^k / 10.

**Outputs**: `Minimum = (round(p) as int, a)`;
`R²` computed **unweighted** (weights deliberately commented out in source) over the same
nonzero points: `R² = 1 − Σ(y_i−ŷ_i)²/Σ(y_i−ȳ)²`.

### 6.4 Gaussian (`GaussianFitting.cs:61-95`) — used for CONTRASTDETECTION

Model (4 parameters `w0..w3`): `y = w2 · exp( −(x − w0)² / (2 w1²) ) + w3`
- `w0` = peak center (focus position), `w1` = sigma, `w2` = amplitude, `w3` = baseline.

Start values:
```
w0 = x of the highest-y point            w2 = y of the highest-y point
w1 = stdev of ALL x values               w3 = y of the lowest point among y >= 0.1
```
`w1` is `Accord.Statistics.Measures.StandardDeviation(x)` whose default is the **unbiased
sample stdev** (÷(n−1)) — use `sqrt(Σ(x_i−x̄)²/(n−1))`. (Accord source is not in the NINA
tree; the n−1 denominator is Accord's documented default and only affects the LM start
value — the §13 golden vector pins the end result.)
(The lowest-point lookup is over points with `y >= 0.1` and throws if there are none → run
fails. The highest-point lookup runs over ALL points. Ties in both: **last** in list order
wins. The LM fit itself uses ALL points, zero-Y included, unweighted.)

Analytic gradient used by the solver (as in source):
```
∂y/∂w0 = w2 (x−w0) exp(−(x−w0)²/(2w1²)) / w1²
∂y/∂w1 = w2 (x−w0)² exp(−(x−w0)²/(2w1²)) / w1³
∂y/∂w2 = exp(−(x−w0)²/(2w1²))
∂y/∂w3 = 1
```

Solver: **Levenberg–Marquardt**, `MaxIterations = 30`, `Tolerance = 0` → convergence test is
disabled; it always runs the full 30 iterations. Unweighted residuals (no per-point weights).
(Accord's internal λ damping schedule is not part of the NINA tree — see Gaps; any standard
LM with λ0 = 1e-3, ×10 on rejected step, ÷10 on accepted step reproduces it to within noise
given the good start values.)

Outputs: `Maximum = (round(w0) as int, w2 + w3)`. **No R² is computed** for the Gaussian fit
(and none is checked for contrast AF).

### 6.5 Final focus point selection (`DetermineFinalFocusPoint`, AutoFocusVM.cs:338-378)

```rust
match method {
    ContrastDetection => gaussian.maximum,
    StarHFR => match fitting {
        Trendlines      => trendline.intersection,
        Hyperbolic      => hyperbolic.minimum,
        Parabolic       => quadratic.minimum,
        TrendParabolic  => ( round((trend.x + quad.x) / 2),  (trend.y + quad.y) / 2 ),
        TrendHyperbolic => ( round((trend.x + hyp.x)  / 2),  (trend.y + hyp.y)  / 2 ),
    }
}
```

The combo variants are plain arithmetic means of the two candidate positions (x rounded).
Since both candidate x's are already integers, their mean is often exactly `.5` — the round
here is banker's rounding (half-to-even, §1), which a naive `f64::round` port gets wrong
half the time.

---

## 7. Validation and failure criteria (`ValidateCalculatedFocusPosition`, AutoFocusVM.cs:672-724)

Run once per attempt, in this order:

### 7.1 R² gate (STARHFR only, and only if `RSquaredThreshold > 0`; default 0.7)

- fitting ∈ {HYPERBOLIC, TRENDHYPERBOLIC} and `hyperbolic.R² < threshold` → **fail**.
- fitting ∈ {PARABOLIC, TRENDPARABOLIC} and `quadratic.R² < threshold` → **fail**.
- fitting ∈ {TRENDLINES, TRENDHYPERBOLIC, TRENDPARABOLIC} and
  (`left.R² < threshold` **or** `right.R² < threshold`) → **fail**.
  (Note: TRENDHYPERBOLIC/TRENDPARABOLIC must pass **both** their curve gate and the trendline gate.)

### 7.2 Bounds check (both methods, always)

`final.x` must satisfy `min(points.x) <= final.x <= max(points.x)`, else **fail**
("focus point outside of measurement bounds").

### 7.3 Move + re-measure (both methods, always)

- Move focuser to `final.x` **truncated toward zero** (`(int)focusPoint.X` — trunc, not round;
  the upstream selections already rounded x, so this matters only for the .5-averaging combos).
- Take `FramesPerPoint` frames at that position; compute the average measure.
- If STARHFR **and** `RSquaredThreshold <= 0`: fail when `initial_hfr != 0` and
  `new_hfr > initial_hfr * 1.15` ("significantly worse than start"). (This check and the
  baseline HFR measurement are mutually exclusive with the R² gate.)
- Otherwise pass. The re-measured point is **not** added to the curve.

On failure: reattempt (up to `AutoFocusTotalNumberOfAttempts`, clamped 1..=5, from the
original `initial_position` with all points cleared), else restore `initial_position` and
return failure (§3).

---

## 8. Backlash compensation

Selected by `BacklashCompensationModel` (default **OVERSHOOT**), implemented as a decorator
around every focuser move (AF and otherwise). Direction: `OUT` = increasing position,
`IN` = decreasing; equal target keeps the previous direction (`FocuserDecorator.cs:97-105`).

### 8.1 OVERSHOOT (`OvershootBacklashCompensationDecorator.cs:32-73`)

On every `move(target)`:

```
comp = if direction == IN  && backlash_in  != 0 { -backlash_in }
       else if direction == OUT && backlash_out != 0 { +backlash_out }
       else { 0 }
if comp != 0 {
    overshoot = target + comp
    if 0 <= overshoot <= max_step {
        move_raw(overshoot)
        settle(FocuserSettleTime)          // extra settle after the overshoot leg
    }                                       // else skip overshoot entirely (logged)
}
move_raw(target)                            // final approach
```

So a move IN first goes `backlash_in` steps *past* the target (further in), then approaches
the target moving OUT; the final approach direction is always opposite the configured
backlash side. Position reads are unmodified.

### 8.2 ABSOLUTE (`AbsoluteBacklashCompensationDecorator.cs:41-80`)

Maintains a persistent `offset` so reported position hides the compensation:

```
reported_position = raw_position − offset
move(target):
    adjusted = target + offset
    if adjusted < 0        { move_raw(0);        offset = 0; return }
    if adjusted > max_step { move_raw(max_step); offset = 0; return }
    comp = if dir(adjusted) == IN  && last_direction == OUT { −backlash_in }
           else if dir(adjusted) == OUT && last_direction == IN { +backlash_out }
           else { 0 }                       // only on direction REVERSAL
    offset += comp                          // BEFORE the move
    move_raw(adjusted + comp)
```

`dir(adjusted)` compares raw current position vs `adjusted` (pre-compensation target); equal →
previous direction. Quirk to reproduce exactly: `move_raw` (the decorator base move) updates
`last_direction = dir_of(reported_position → raw_target)` where `reported_position` is the
offset-adjusted Position property — and since `offset` was already updated above, the state
used for the *next* reversal detection is computed from `raw_pos − new_offset` vs
`adjusted + comp`, not from the raw pair. (Source: `FocuserDecorator.Move` line 93 uses the
virtual `this.Position`, which `AbsoluteBacklashCompensationDecorator` overrides.) The clamp
branches (`adjusted < 0` / `> max_step`) also go through the same base move and update
`last_direction` the same way.

### 8.3 Interaction with the AF sweep

`reverse = (model == OVERSHOOT && backlash_in > 0 && backlash_out == 0)`
(AutoFocusVM.cs:173). When reversed, the initial sweep runs IN-first then steps OUT, so
every measured approach shares the direction of the overshoot decorator's final approach
(OUT), keeping backlash out of the measurements.

### 8.4 Focuser move semantics (`FocuserVM.cs:166-255`)

- Target clamped to `[0, max_step]` (with warnings).
- Temp comp is toggled OFF for the duration of any move and restored after.
- Loop `while position != target { driver.move(target) }` with a 10-minute hard timeout.
- After reaching target: wait `FocuserSettleTime` seconds (default 0), and wait for the next
  device poll before reporting completion.
- Cancellation calls the driver's `Halt()`.

---

## 9. Filter handling

- `SetAutofocusFilter` (AutoFocusVM.cs:577-595): if `UseFilterWheelOffsets`, switch to the
  first filter flagged `AutoFocusFilter` (if none, stay on the imaging filter). On filter
  change failure: warn and continue with the imaging filter.
- Filter changes themselves apply focus offsets (`FilterWheelVM.cs:91-160`): when
  `UseFilterWheelOffsets`, moving from filter A to B issues
  `move_relative(B.FocusOffset − A.FocusOffset)` concurrently with the wheel move (and, if
  `DisableGuidingOnFilterChange`, stops guiding for the duration when the offset ≠ 0).
  The whole filter change (wheel + offset move) has a **5-minute** hard timeout.
- The AF X axis is therefore always in the AF filter's frame; when the imaging filter is
  restored in the finally block, the offset delta is re-applied automatically, translating
  the found focus to the imaging filter.
- Per-filter AF exposure/binning/gain/offset overrides: §4.1.

---

## 10. Report

`GenerateReport` (AutoFocusVM.cs:469-496, `AutoFocusReport.cs:117-182`) writes JSON to
`%LOCALAPPDATA%/NINA/AutoFocus/<yyyy-MM-dd--HH-mm-ss>--<profileId>.json` (reports older than
180 days are cleaned at startup). Contents (Version = 2): timestamps, filter, temperature,
method + fitting names, initial/calculated/previous focus points, all measure points
(position, value, error), the four fit expressions, the trendline intersection and each fit's
extremum, R² values (hyperbolic, quadratic, left/right trend), backlash settings, duration.
A successful run also broadcasts `AutoFocusInfo { temperature, position, filter, timestamp }`.

---

## 11. Temperature compensation and re-focus hooks

- During AF: driver-level temp comp is disabled (§3 step 1) and restored in the finally block.
- Every focuser move also suspends temp comp (§8.4).
- Slope-based compensation between AF runs (`FocuserVM.cs:135-164`):
  `delta = last_roundoff + (T_now − T_lastFocused) · slope` steps; move by `round(delta)`;
  carry `last_roundoff = delta − round(delta)` to avoid drift; `T_lastFocused` is set from the
  successful-AF broadcast (`SetFocusedTemperature` also resets `last_roundoff` to 0) and
  updated after every slope move. First call after connect (`T_lastFocused == -1000`
  sentinel): move 0 steps, just record the current temperature.
- Sequencer triggers that start an AF run (all fire only before LIGHT exposures and are
  suppressed when a meridian flip is imminent):
  - **After temperature change** (`AutofocusAfterTemperatureChangeTrigger.cs:116-155`):
    `|T_now − T_ref| >= Amount` °C, default **Amount = 5**. `T_ref` is the last AF's
    temperature; before any AF it is the focuser temperature captured at sequence
    `Initialize()` (re-captured on first evaluation if that was NaN). Never triggers while
    the focuser temperature reads NaN or the image history is empty.
  - **After time** (`AutofocusAfterTimeTrigger.cs:116-145`): elapsed since last AF ≥ Amount
    minutes, default **Amount = 30**; before any AF the reference is the time of the first
    `SequenceBlockInitialize()` (set once).
  - **After exposures** (`AutofocusAfterExposures.cs:107-132`): count of LIGHT frames since
    last AF is a nonzero multiple of N, default **AfterExposures = 5**.
  - **After HFR increase** (`AutofocusAfterHFRIncreaseTrigger.cs:159-240`): over LIGHT frames
    since the last AF (current filter only, HFR > 0), let `origin = min HFR`; fit an
    unweighted OLS line through the last `SampleSize` HFR values indexed 1..k and
    extrapolate `trend = line(k)`; trigger when `(1 − origin/trend) · 100 > Amount`%
    (percentage rounded to 2 decimals before comparison). Defaults **Amount = 5** (%),
    **SampleSize = 10**; the SampleSize setter silently rejects values `< 3`. Needs at least
    3 qualifying frames since the last AF.
  - **After filter change** (`AutofocusAfterFilterChange.cs`): on filter switch.
- Other AF entry points (same `StartAutoFocus` core):
  - **Sequencer instruction `RunAutofocus`** (`RunAutofocus.cs:79-101`): runs AF with the
    current filter-wheel filter (resolved to the profile's filter by wheel position); a
    `null` report (failed/cancelled AF) throws a sequence-entity failure; a good report is
    appended to the image history (which the triggers above read). No timeout wrapper here
    either — confirms `AutoFocusTimeoutSeconds` is dead in this snapshot.
  - **Meridian flip** (`MeridianFlipVM.cs:228-248`): when the AF-after-flip step is enabled,
    a full AF run is executed as a flip step; the report is appended to history (even when
    `null`) and the step reports success regardless of the AF outcome — an AF failure never
    fails the flip.
  - Manual AF from the imaging tool (`AutoFocusToolVM.cs:109`).

---

## 12. Edge cases and failure paths (consolidated)

| Situation | Behavior |
|---|---|
| 0 stars in a frame (measure == 0) | keep point, stdev := 1000 (weight 1e-6); counts toward its side's quota |
| No trend points on either side after initial pass | warn, restore initial position, **fail run** (no reattempt) |
| `points > FramesPerPoint·OffsetSteps·10` | error, stop collecting, still fit + validate |
| Focuser reaches position 0 during sweep | error, stop collecting, still fit + validate |
| Camera returns null image | retry (≤ 3 attempts), then exception → cleanup + fail |
| Camera error while sub-sampling | retry once without sub-sample |
| Fit gets < 2 trend points on a side | that side: slope=offset=R²=0 (likely fails R² gate) |
| Parallel trendlines | intersection = (0,0) → fails bounds check |
| Hyperbolic: no points with y ≥ 0.1, or p_range/a0/b0 = 0/NaN | unfit result (min=(0,0), R²=0) → fails gate/bounds |
| Gaussian: no point with y ≥ 0.1 | exception → run fails via generic handler |
| Invalid/unhandled `AutoFocusCurveFitting` enum in `DetermineFinalFocusPoint` | logs error, returns `new DataPoint()` = (0,0) (unreachable — all 5 enum values are handled — but if a 6th were added it would fall through to the bounds check and fail) (`AutoFocusVM.cs:372-373`) |
| R² below threshold / out of bounds / HFR worse ×1.15 | reattempt from initial position (≤ attempts), else restore + fail |
| Cancellation / any exception | restore initial position, clear points, restore filter + temp comp + guiding |
| Focuser move timeout | 10-minute cap per move; error notification |
| Report JSON write fails (disk error) | `GenerateReport` catches + returns `null`; `completed` is already `true`, so the null-deref at the success broadcast is caught by the generic handler → `StartAutoFocus` returns `None` (run "failed") but the focuser **stays at the found focus position** (no restore) |
| `AutoFocusTimeoutSeconds` | present in profile (600) but not enforced anywhere in this snapshot |

---

## 13. Golden test vectors (from NINA.Test, for the Rust test suite)

All points `(x, y, err_x=1, err_y=1)`.

**Trendline** (`TrendlineFittingTest.cs`):
1. V-curve `{(1,10),(2,8),(3,6),(4,4)} ∪ {(5,2)} ∪ {(6,4),(7,6),(8,8),(9,10)}` →
   intersection ≈ (5, 2) (tol 1e-12); left trend = the 4 left points, right = the 4 right.
2. Flat tip `{(1,10),(2,8),(3,6),(4,4)} ∪ {(5,2.1),(6,2),(7,2.1)} ∪ {(8,4),(9,6),(10,8),(11,10)}`
   → pivot (6,2); band excludes 2.1 points (2.1 ≯ 2.1); intersection ≈ (6, 0).

**Quadratic** (`QuadraticFittingTest.cs`): `(x−5)²+2` sampled at x=1..9 →
minimum (5, 2), R² exactly 1 (`Should().Be(1)` — pins the SS_tot==0-free weighted R² path).

**Hyperbolic** (`HyperbolicFittingTest.cs`):
1. Same 9 samples of `(x−5)²+2` → minimum X ≈ 5, minimum Y ≈ **1.2** (tol 1e-12) — pins the
   grid solver's exact behavior.
2. Degenerate sets that must return "no fit" (Minimum=(0,0), no fitting function):
   `{(1000,18),(1100,0),(1200,0)}`; `{(1000,18)×3,(1100,0),(1200,0)}`;
   `{(900,18),(1000,18)×2,(1100,0),(1200,0)}`;
   `{(800,18),(900,0),(1000,0),(1000,18)×2,(1100,0),(1200,0)}`.

**Gaussian** (`GaussianFittingTest.cs`): peak curve
`{(1,2),(2,3),(3,6),(4,11),(5,19),(6,11),(7,6),(8,3),(9,2)}` →
maximum ≈ (5, 18.0104137975149) (tol 1e-12).

**End-to-end runs** (`AutofocusVMTest.cs`, image-based — uses a real XISF test frame blurred
1×/2×/3×, so not portable as numeric vectors, but they pin orchestration counts): with
`offset_steps = 2`, `step = 100`, TRENDLINES, threshold unset (0 → baseline-HFR mode):
ideal V gives exactly **5 measure points and 7 captures** (1 baseline + 5 sweep + 1
validation), final position = initial (5000). With two null-image download failures
interleaved: same 5 points, **10 captures** (the nulls are retried). A right-shifted V with
TRENDHYPERBOLIC: **6 points, 8 captures**, final position 5006 from 5100 (one left-extension
step). Useful as orchestration-level acceptance shapes for the Rust port.

Extra identity assertions in the tests: hyperbolic, quadratic and Gaussian tests all also
assert `Fitting(extremum.X) == extremum.Y` with **exact** double equality. For the quadratic
this is true by construction (`y_min = f(round(x_min))`). For the hyperbolic and Gaussian it
implies the solver lands on the symmetric center **exactly** (`p == 5.0`, `w0 == 5.0`) on
this symmetric data — both start at exactly 5.0, so a faithful reimplementation passes; if
your solver differs bit-wise, relax these two identities to ~1e-12 and keep the extremum
tolerances, which are the behaviorally meaningful pins.

---

## 14. Source map (MPL-2.0 provenance)

All paths relative to `references/nina/`, commit `7c9de0c4202f2d054f58b0cb6b15d561f8b6b4b2`.

| Algorithm / behavior | File : lines |
|---|---|
| AF orchestration, reattempts, cleanup | `NINA.WPF.Base/ViewModel/AutoFocus/AutoFocusVM.cs:136-322` |
| Max-points cap, reverse-sweep rule | `AutoFocusVM.cs:144, 173` |
| Sweep extension loop (left/right quotas, breakouts) | `AutoFocusVM.cs:189-235` |
| `GetFocusPoints` (stepping, pipelined measure, zero-star stdev, sorted insert) | `AutoFocusVM.cs:531-575` |
| Fit recomputation policy | `AutoFocusVM.cs:119-134` (SetCurveFittings) |
| Final point selection incl. combos | `AutoFocusVM.cs:338-378` |
| Exposure eval, ROI/donut params, HFR/contrast dispatch | `AutoFocusVM.cs:380-462` |
| Frame averaging math | `AutoFocusVM.cs:498-528` |
| AF filter selection | `AutoFocusVM.cs:577-595` |
| Sub-sample rectangle | `AutoFocusVM.cs:597-612, 667-670` |
| Exposure capture, retries, per-filter overrides | `AutoFocusVM.cs:614-665` |
| Validation (R² gate, bounds, 1.15× HFR check) | `AutoFocusVM.cs:672-724` |
| Trendline pivot/membership, intersection use | `NINA.WPF.Base/Utility/AutoFocus/TrendlineFitting.cs:91-114` |
| Weighted line fit, R², intersection math | `NINA.WPF.Base/Utility/AutoFocus/Trendline.cs:27-64` |
| Quadratic fit + minimum | `NINA.WPF.Base/Utility/AutoFocus/QuadraticFitting.cs:71-89` |
| Hyperbolic model, init, grid solver, R² | `NINA.WPF.Base/Utility/AutoFocus/HyperbolicFitting.cs:75-190` |
| Gaussian model, start values, gradient, LM config | `NINA.WPF.Base/Utility/AutoFocus/GaussianFitting.cs:61-95` |
| cosh/asinh definitions | `NINA.Core/Utility/MathHelper.cs:22-44` |
| Sorted insertion | `NINA.Core/Utility/AsyncObservableCollection.cs:68-77` |
| Point ordering | `NINA.WPF.Base/ViewModel/AutoFocus/FocusPointComparer.cs:20-31` |
| Measure struct | `NINA.WPF.Base/ViewModel/AutoFocus/MeasureAndError.cs:17-20` |
| Profile defaults + clamps | `NINA.Profile/FocuserSettings.cs:31-344` |
| Per-filter AF overrides + sentinels | `NINA.Core/Model/Equipment/FilterInfo.cs:37-151` |
| Enums (method/fitting/backlash/contrast/direction) | `NINA.Core/Enum/{AFMethodEnum,AFCurveFittingEnum,BacklashCompensationModel,ContrastDetectionMethodEnum,OvershootDirection}.cs` |
| Overshoot backlash | `NINA.WPF.Base/ViewModel/Equipment/Focuser/OvershootBacklashCompensationDecorator.cs:32-73` |
| Absolute backlash | `.../AbsoluteBacklashCompensationDecorator.cs:41-80` |
| Direction determination, decorator base | `.../FocuserDecorator.cs:92-105` |
| Move clamping/settle/timeout/tempcomp toggle, decorator selection, temp-slope moves | `.../FocuserVM.cs:98-272` |
| Filter-change focus offsets + guiding pause | `NINA.WPF.Base/ViewModel/Equipment/FilterWheel/FilterWheelVM.cs:95-145` |
| HFR mean/σ, radius outlier filter, brightest-N | `NINA.Image/ImageAnalysis/StarDetection.cs:237-248, 364-395` |
| StarDetectionParams fields | `NINA.Image/ImageAnalysis/IStarDetection.cs:36-45` |
| Contrast metrics (Statistics/Sobel/Laplace) | `NINA.Image/ImageAnalysis/ContrastDetection.cs:33-126` (Statistics path: `AutoFocusVM.cs:396-398`) |
| Report schema | `NINA.WPF.Base/Utility/AutoFocus/AutoFocusReport.cs:27-255` |
| AF triggers (temp/time/exposures/HFR) | `NINA.Sequencer/Trigger/Autofocus/*.cs` (lines cited in §11) |
| Trigger defaults (5 °C / 30 min / 5 exp / 5 % + SampleSize 10) | `AutofocusAfterTemperatureChangeTrigger.cs:66`, `AutofocusAfterTimeTrigger.cs:66`, `AutofocusAfterExposures.cs:67`, `AutofocusAfterHFRIncreaseTrigger.cs:68-69, 108-117` |
| RunAutofocus sequencer instruction (null-report → failure, history append) | `NINA.Sequencer/SequenceItem/Autofocus/RunAutofocus.cs:79-101` |
| AF step of the meridian flip | `NINA.WPF.Base/ViewModel/MeridianFlipVM.cs:228-248` |
| Golden vectors | `NINA.Test/Autofocus/{TrendlineFittingTest,QuadraticFittingTest,HyperbolicFittingTest,GaussianFittingTest}.cs` |
| End-to-end orchestration tests (capture/point counts) | `NINA.Test/Autofocus/AutofocusVMTest.cs:96-286` |

---

## 15. Recommended for AstroDeck

**Adopt as default:**
- **Method STARHFR + TRENDHYPERBOLIC fitting, R² threshold 0.7.** NINA ships HYPERBOLIC as
  default, but TRENDHYPERBOLIC is the community-recommended setting: averaging the trendline
  intersection with the hyperbola minimum makes the pick robust when one model is skewed by a
  flat tip or one bad wing point, and it forces *both* R² gates (trendlines and hyperbola),
  which catches more bad runs. Keep plain HYPERBOLIC selectable for very noisy data (fewer
  gates to trip).
- Sweep: step size and initial-offset-steps=4 as user-per-telescope settings (step size has
  no sane universal default — expose a "step size wizard" later); keep the NINA adaptive
  sweep exactly (initial `offset+1` points on the OUT side, extend IN one step at a time,
  zero-point quota rules) — it minimizes total moves and its behavior is validated by years
  of field use.
- Frames per point = 1 default; implement the exact averaging math (RMS-of-stdevs) so
  multi-frame users get identical numbers.
- Backlash: **OVERSHOOT** model default with the `reverse` sweep rule; it is measurement-side
  robust (each approach in a constant direction) and needs no persistent state. Implement
  ABSOLUTE too (trivial) for focusers whose drivers dislike overshooting.
- Weighted fits exactly as specified (weights 1/σ², σ floor 0.001, no-star σ 1000) — this is
  NINA's real outlier strategy; do not add ad-hoc point rejection or parity breaks.
- Hyperbolic solver: reimplement the shrinking grid search verbatim (deterministic, no LM
  library needed, ~10 ms for 40 points) and pin it with the §13 vectors, including the
  minimum-Y ≈ 1.2 case and the four no-fit cases.
- Validation: keep all three gates (R², bounds, and the ×1.15 fallback when R² gate is
  disabled), reattempt semantics (1 attempt default, max 5), and restore-position-on-failure.
- Write the same JSON report schema (Version 2) — existing NINA report analyzers (e.g. AF
  report viewers) then work against AstroDeck out of the box.

**Skip / de-prioritize:**
- CONTRASTDETECTION method (Sobel/Laplace/Statistics + Gaussian fit): rarely used, weak error
  model (fixed σ 0.01); implement later behind the same trait if demand appears.
- `AutoFocusUseBrightestStars` star-matching across frames: dead in NINA core (positions never
  fed back); implement only the per-frame top-N selection if at all.
- `AutoFocusTimeoutSeconds`: dead in NINA; if we want a timeout, enforce it around the whole
  run ourselves (recommended: do enforce, 600 s default — cheap insurance NINA forgot to wire).
- PARABOLIC-only fitting (quadratic systematically overshoots the minimum on true hyperbolic
  V-curves; keep for parity, not default).

**Deliberate deviations worth making (flag in UI as non-NINA):**
- Fix the sub-sample precedence quirk (§4.1): require `can_subsample` for any sub-sampling.
- Use `round()` instead of C# `(int)` truncation when moving to the final focus position
  (§7.3) — max error 1 step, but truncation is clearly unintended.
- Enforce the AF timeout (above).

---

## Gaps (things the source does not make explicit)

1. **Accord LM internals** (Gaussian fit): initial damping λ, its up/down factors, and the
   step-acceptance rule live inside the Accord.NET package, not in NINA's tree. NINA sets only
   `MaxIterations=30, Tolerance=0`. Spec: any standard LM (λ0=1e-3, ×10/÷10) with the given
   analytic gradient and start values; verify against the Gaussian golden vector (§13).
   Likewise `Accord.Statistics.Measures.StandardDeviation` (the `w1` start value) is out of
   tree; its documented default is the unbiased sample stdev (÷(n−1)) — also pinned by the
   golden vector.
2. **Accord weighted OLS/polynomial solver details**: the closed forms in §6.1/§6.2 are the
   unique WLS minimizers, so results match regardless of Accord's internal decomposition
   (SVD vs QR); exact division-by-zero guard of Accord's `RSquaredLoss` when `SS_tot == 0`
   (believed to return 1.0) was not verifiable from the tree — pin with the R²=1 quadratic
   golden vector.
3. **`ScatterErrorPoint`/`DataPoint` are OxyPlot types**; their semantics beyond
   (x, y, errX, errY) tuples are irrelevant, but ErrorX is always 0 in AF (unused).
4. **`AutoFocusTimeoutSeconds`** exists in the profile with default 600 but is referenced
   nowhere else in this snapshot (grep over the whole tree hits only `FocuserSettings.cs`
   and its interface; neither `RunAutofocus` nor `MeridianFlipVM` wraps a timeout) —
   vestigial, or consumed by an out-of-tree plugin.
5. **Star detection / HFR computation itself** (blob detection, HFR radius math, sensitivity
   presets) is a separate subsystem — covered by the HocusFocus dossier, not this one; this
   dossier documents only the statistics AF consumes (mean, sample σ, top-N selection).
6. **Focuser settle interplay**: `FocuserSettleTime` is applied both inside `FocuserVM` after
   every move *and* inside the overshoot decorator after the overshoot leg — i.e. an
   overshoot move settles twice. Presumed intentional; reproduce as-is.
