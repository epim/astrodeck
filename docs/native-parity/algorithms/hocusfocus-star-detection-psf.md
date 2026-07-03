# Hocus Focus Star Detection + PSF Fitting — Algorithm Dossier

**Purpose**: single source of truth for reimplementing the Hocus Focus (NINA plugin) star-detection
and PSF-model-fitting pipeline in Rust, without reading the original C#.

**Provenance**: extracted from the reference clone at
`references/hocusfocus` = https://github.com/ghilios/joko.nina.plugins.git, commit `4d93eaf`
(© 2021–2026 George Hilios, **MPL-2.0**). Every algorithm below has an exact file/line source in
the [Source map](#source-map). All code was read directly; nothing here is from memory.

**Conventions used throughout**

- Images are single-channel `f32`, values normalized to `[0, 1)` by dividing raw ADU by `2^bit_depth`
  (`value = adu / (1 << bpp)`, note: **not** `2^bpp − 1`). All thresholds/σ below are in these
  normalized units unless stated otherwise.
- Pixel coordinates: x → column, y → row; index = `y * width + x`.
- "Upper median" = `sorted[count >> 1]` (the upper of the two middle elements for even counts).
  Some sites instead use the true median (average of both middles) — noted per site.
- `MAD→σ` factor = **1.4826** (one site uses 1.483; noted).
- Border handling for every convolution / morphology: **reflect**.

---

## 0. Pipeline overview

```
source image (f32, normalized)
 └─ [optional] CFA hotpixel filter on raw bayer data + debayer (bayered images only)
 └─ ROI crop (outer boundary)                                    ┐
 └─ hotpixel filter (3×3 median, optionally thresholded)         │
 └─ [optional] Gaussian noise reduction of measurement image     │  EARLY stage
 └─ structure map = noise-reduced copy                           │  (candidate
     └─ subtract à-trous B3-spline wavelet residual (N layers)   │   formation;
     └─ Gaussian blur (kernel 2N+1)                              │   depends only on
     └─ [optional] dilation                                      │   "early" params)
     └─ binarize: global median + k·σ  (or adaptive local grid)  │
     └─ clear inner-crop ROI                                     │
     └─ [optional] donut morphological close                     │
 └─ flood-fill scan → candidate regions (bbox + pixel list)      ┘
 └─ per-candidate gates + measurement (parallel):                ┐
     TooSmall → OnBorder → TooElongated → TooDistorted →         │
     ComputeStarParameters (annulus, background plane, IRLS,     │  LATE stage
       contamination, flux, peak, iterative centroid) →          │  (pure reads of
     Degenerate → LowSensitivity → NotCentered → TooFlat →       │   the early
     HFR (MeasureStar) → TooLowHFR → Contaminated →              │   context)
     bloom suppression                                            ┘
 └─ [optional] PSF fit per star (Gaussian / Moffat; ALGLIB LM) → R² gate
 └─ ROI offset add-back, deterministic ordering
 └─ frame-level aggregation (median/MAD HFR, FWHM, eccentricity; AF star selection)
```

Two independent noise σ estimates exist:

- **StructureNoiseSigma** — κ-σ on the noise-reduced structure-map source. Used only for the
  binarization threshold.
- **MeasurementNoiseSigma** — κ-σ on the image actually sampled for measurement. Used for the
  sensitivity gate, all clip margins, HFR τ, PSF noise floor, contamination fallback σ. Equal to
  StructureNoiseSigma when the two images are the same (no noise reduction configured, or
  measurement noise reduction enabled).

---

## 1. Source image preparation

### 1.1 Normalization

```rust
fn to_f32(image: &[u16], bpp: u32) -> Vec<f32> {
    let scale = (1u32 << bpp) as f32;          // e.g. 65536 for 16-bit
    image.iter().map(|&v| v as f32 / scale).collect()
}
```

### 1.2 CFA (bayered) hotpixel path

Only when the input is a debayered image AND `HotpixelFiltering && HotpixelThresholdingEnabled`:

1. Copy the **raw (pre-debayer) u16** frame.
2. `threshold_adu = (HotpixelThreshold * (1 << bit_depth)) as u16` (default 0.001 ⇒ 65 ADU @16-bit).
3. For every pixel: choose comparison distance from the bayer pattern — green pixels compare with
   same-color **diagonal neighbors at distance 1** ("adjacent"), non-green at **distance 2**.
   Whether a row starts green depends on the pattern (RGGB: row0 starts non-green `firstRowG=1`,
   row1 starts green `secondRowG=0`; BGGR same; GBRG/GRBG rows start green then non-green; etc.).
   The flag alternates every pixel along the row.
4. Median of {center, 4 same-color diagonal neighbors} (5-way median; at edges 3-way; at corners
   `min(center, single diagonal)`).
5. If `|median − center| >= threshold_adu`, replace the pixel with the median; count it.
6. Debayer (luminance channel) and normalize per §1.1. The CFA hotpixel count overrides the
   mono-path hotpixel count in the metrics.

Exact medians:
```
median3(a,b,c) = max(min(a,b), min(c, max(a,b)))
median5(a,b,c,d,e): f = max(min(a,b), min(c,d)); g = min(max(a,b), max(c,d)); median3(e,f,g)
```

### 1.3 ROI crop

If `Region.OuterBoundary` is not the full frame (`width < 1.0 || height < 1.0`, expressed as ratios):

```
roi = Rect { x: floor(cols * startX), y: floor(rows * startY),
             w: (cols * width) as int, h: (rows * height) as int }   // truncation, not rounding
```
The pipeline then operates on the cropped image; **all outputs get `(roi.x, roi.y)` added back at
the end** (star centers, bboxes, metric bounds lists, diagnostics).

### 1.4 Mono hotpixel filter + noise reduction (measurement image)

Applied when `HotpixelFiltering == true` **or** (`NoiseReductionRadius > 0 &&
StarMeasurementNoiseReductionEnabled`) — i.e. noise reduction implies hotpixel filtering first,
otherwise hot pixels would be smeared into neighbors.

- **Plain filter** (`HotpixelThresholdingEnabled == false`): 3×3 median blur, in place. Count = 0.
- **Thresholded filter** (default): `blurred = median3x3(img)`; `diff = |img − blurred|`;
  `mask = diff > HotpixelThreshold` (strictly greater — OpenCV binary threshold); replace masked
  pixels with the blurred value; hotpixel count = number of masked pixels.
- Only `HotpixelFilterRadius == 1` is supported; any other value ⇒ `NotImplemented` error.

Then, if `NoiseReductionRadius > 0 && StarMeasurementNoiseReductionEnabled` (default: radius 3 but
flag **false**, so the measurement image is NOT blurred by default):
`gaussian_blur(img, kernel = 2*radius + 1)` in place.

### 1.5 Gaussian convolution (exact spec)

```rust
fn convolve_gaussian(src, dst, kernel_size /* odd, >= 3 */, sigma /* <= 0 => default */) {
    let sigma = if sigma <= 0.0 { 0.159758 * kernel_size as f64 } else { sigma };
    // 1-D normalized Gaussian kernel of length kernel_size (OpenCV getGaussianKernel semantics:
    // k[i] = exp(-(i - (n-1)/2)^2 / (2 sigma^2)), normalized to sum 1)
    // applied separably in x then y, border = reflect
}
```
The constant `0.159758` makes σ ≈ kernel_size/6.26 (slightly wider than OpenCV's own default).

### 1.6 Structure-map source

```
if hotpixel_filtering_applied || noise_reduction_applied || NoiseReductionRadius <= 0 {
    noise_reduced = copy(measurement_image)       // already prepared
} else {
    noise_reduced = copy(measurement_image); apply_hotpixel_filter(noise_reduced)
}
if NoiseReductionRadius > 0 && !noise_reduction_applied {
    gaussian_blur(noise_reduced, 2*NoiseReductionRadius + 1)   // structure-map-only blur
}
structure_map = copy(noise_reduced)
```
With defaults (HotpixelFiltering=true, NoiseReductionRadius=3, measurement NR off): the measurement
image is hotpixel-filtered but sharp; the structure map source is additionally Gaussian-blurred
with kernel 7.

---

## 2. Kappa-sigma noise estimation

Iteratively clipped mean/σ. Defaults: `clipping_multiplier = NoiseClippingMultiplier` (2.0),
`allowed_error = 1e-5`, `max_iterations = 5`.

```rust
fn kappa_sigma_noise_estimate(img: &Image, k: f64, allowed_error: f64, max_iter: u32)
    -> (sigma: f64, background_mean: f64, iterations: u32)
{
    let mut threshold = f32::MAX;
    let mut last_sigma = 1.0; let mut last_mean = 1.0;
    let mut n = 0;
    while n < max_iter {
        // mask: pixels in [f32::EPSILON, threshold - f32::EPSILON]
        //   -> zero/negative pixels are excluded even on iteration 0 (calibrated frames can
        //      carry exact-zero borders that would bias the initial mean/σ)
        let (mean, sigma) = mean_stddev_masked(img, |v| v >= f32::EPSILON && v <= threshold - f32::EPSILON);
        n += 1;
        if n > 1 && (sigma - last_sigma).abs() <= allowed_error {
            last_sigma = sigma;           // NOTE: mean is NOT updated on the convergence break —
            break;                        // BackgroundMean returned lags one iteration (as-coded)
        }
        threshold = (mean + k * sigma) as f32;
        last_sigma = sigma; last_mean = mean;
    }
    (last_sigma, last_mean, n)
}
```

Convergence test is **absolute** σ difference (comment in source: PixInsight uses absolute too).
σ here is the population-style stddev over the masked pixels (OpenCV `MeanStdDev`, i.e. `sqrt(E[x²] − E[x]²)`, divisor N).

Run twice, in parallel tasks:
- on `noise_reduced` (structure-map source) → **StructureNoiseSigma**;
- on the measurement image **only when it differs** (`NoiseReductionRadius > 0 && !noise_reduction_applied`)
  → **MeasurementNoiseSigma**; otherwise `MeasurementNoiseSigma = StructureNoiseSigma`.
  Rationale (σ-consistency "F4"): thresholds applied to an image must use that image's σ; a blurred
  image's white-noise σ is ~4× smaller than the sharp one at the default radius.

---

## 3. Structure map

### 3.1 À-trous B3-spline dyadic wavelet residual

Separable 1-D scaling kernel per dyadic layer `i` (à-trous zero-padding, layer 0 taps are the B3
spline `[1/16, 1/4, 3/8, 1/4, 1/16]`):

```
size(i) = (1 << (i + 2)) + 1                    // 5, 9, 17, 33, ...
kernel(i) = zeros(size);
kernel[0] = kernel[size-1] = 0.0625
kernel[1 << i] = kernel[size - (1 << i) - 1] = 0.25
kernel[size >> 1] = 0.375
```

```rust
fn residual_atrous_b3(src, num_layers) -> Image {
    let mut prev = src;                          // NOT copied; layer 0 convolves src itself
    let mut tmp = Image::new();
    for i in 0..num_layers {
        tmp = sep_filter_2d(prev, kernel(i), kernel(i), border = Reflect);
        prev = tmp;                              // successive smoothing, no differencing
    }
    prev                                          // the residual (coarsest smooth) layer
}
```

Effective layer count:
```
effective_layers = if DefocusAwareStructure { max(1, StructureLayers + StructureLayerBoost) }
                   else if DefocusAwareDonutDetection { max(1, StructureLayers + 2) }   // donut default boost
                   else { StructureLayers }                                             // default 4
```

Then: `structure_map = clamp(structure_map − residual, 0.0, 1.0)` (per-pixel subtract + clamp).
This removes structures larger than ~2^layers px (nebulae, gradients) and keeps star-scale detail.

### 3.2 Post-wavelet blur

`gaussian_blur(structure_map, kernel = StructureLayers * 2 + 1)` — always keyed to the *base*
`StructureLayers` (not the boosted count). Smooths ring/hole artifacts on big/defocused stars.

### 3.3 Structure-map statistics (histogram median)

Median via a 65 536-bucket **linear** histogram over `[0,1)` (log-histogram variant exists but is
disabled: "not worth the cost"):

```
bucket(v) = clamp(floor(v * 65536), 0, 65535)
target = num_pixels / 2.0
scan cumulative count; at the first bucket i where cum >= target:
  ratio = (cum - target) / histogram[i]
  next  = (i < 65535) ? lower_bound[i+1] : 1.0
  median = lower_bound[i] + (next - lower_bound[i]) * ratio     // interpolated (approximate)
```

### 3.4 Scalar binarization threshold (legacy / default-off path)

```
binarize_threshold = structure_map_median + NoiseClippingMultiplier * StructureNoiseSigma
```

### 3.5 Dilation (optional; default off)

If `StructureDilationCount > 0`: morphological **dilate** with an ellipse structuring element of
size `StructureDilationSize × StructureDilationSize` (default 3), `iterations =
StructureDilationCount` (default 0 ⇒ skipped), border reflect. Boosts small structures.

### 3.6 Binarization

- **Scalar** (when `LocallyAdaptiveBinarization == false`):
  `dst = (src > threshold) ? 1.0 : 0.0` (strictly greater).
- **Spatially adaptive** (default **ON**): per-pixel threshold surface
  `T(x,y) = local_median(x,y) + NoiseClippingMultiplier * local_sigma(x,y)`, built from two coarse
  block grids and bilinearly upsampled. `dst = (src − T) > 0 ? 1 : 0` (same strict-greater).

  **Local background grid** (`ComputeLocalBackgroundGrid`, block size = `AdaptiveNoiseBlockSize`,
  default 128 px, floor `sigma_floor = 1e-6`):
  - grid is ceil-sized: `grid_cols = ceil(w / block)`, trailing blocks are partial (use only their
    own pixels; every pixel contributes to exactly one block);
  - per block: `median = sorted[count >> 1]` (upper median);
    `sigma = max(1.4826 * upper_median(|v − median|), sigma_floor)`;
  - blocks computed independently (parallel by grid row).

  **σ-consistency**: the σ grid is sampled on the **noise-reduced structure-map source** (the same
  image the global σ comes from), the median grid on the **structure map after wavelet+blur but
  BEFORE dilation** (the same image the global median comes from). The combination
  `median + NC·σ` is formed at grid resolution, then the small grid is upsampled once
  (bilinear resize to full resolution) — linearity makes this equivalent to upsampling each grid.
  If the two grids' dimensions mismatch ⇒ hard error. With constant grids this reduces exactly to
  the scalar formula.

### 3.7 Inner-crop clearing

If `Region.InnerCropBoundary != null` (donut ROI for NINA stock AF), zero the structure map inside
the inner rect (`floor` for x/y, truncation for w/h, same as the outer ROI computation). Candidates
cannot form there; a *second* pass later drops any star whose bbox intersects the inner region
(§8.1).

### 3.8 Donut morphological close (opt-in)

If `DefocusAwareDonutDetection && DonutMorphCloseSize > 1`: morphological **close** (dilate then
erode) with ellipse kernel `DonutMorphCloseSize` (default 5), border reflect. Reconnects
fragmented donut-ring arcs into one candidate without filling the central hole (kernel ≪ hole).

---

## 4. Candidate collection (flood-fill scan)

Also computed here: `SaturatedPixelCount` = count of measurement-image pixels `>=
SaturationThreshold` (single full-image pass).

The scan collects **all** connected structures (no gates — gates are late-stage). Structure pixels
are `>= ZERO_THRESHOLD = 0.001` in the binarized map; visited pixels are zeroed.

```rust
const ZERO: f32 = 0.001;
// scan rows top->bottom (yTop in 0..height-1), cols left->right (xLeft in 0..width-1);
// note: last row and last column are never scan STARTS (loop bounds y < height-1, x < width-1)
for y_top in 0..height-1 {
    for x_left in 0..width-1 {
        if map[y_top][x_left] < ZERO { continue; }
        let mut points: Vec<Point> = vec![];
        let mut bounds = Rect { x: x_left, y: y_top, w: 1, h: 1 };
        let (mut y, mut x) = (y_top, x_left);
        loop {
            let mut row_added = 0;
            if map[y][x] >= ZERO { points.push((x, y)); row_added += 1; }
            // extend LEFT from x while pixels are foreground — but only if the anchor pixel was foreground
            let mut row_start = x;
            if row_added > 0 {
                while row_start > 0 && map[y][row_start - 1] >= ZERO {
                    row_start -= 1; points.push((row_start, y)); row_added += 1;
                }
            }
            // extend RIGHT: while next pixel is foreground add it; if background AND we have
            // added nothing on this row yet AND we're still left of the current bounds.right,
            // keep walking right (searching for the row's first foreground pixel inside bounds)
            let mut row_end = x;
            while row_end < width - 1 {
                if map[y][row_end + 1] < ZERO {
                    if row_added > 0 || row_end >= bounds.right() { break; }
                    row_end += 1;                       // skip gap while searching
                } else {
                    row_end += 1; points.push((row_end, y)); row_added += 1;
                }
            }
            // grow bbox
            if row_start < bounds.x { bounds.w += bounds.x - row_start; bounds.x = row_start; }
            if row_end > bounds.right() - 1 { bounds.w += row_end - bounds.right() + 1; }
            if row_added == 0 { bounds.h = y - y_top; break; }         // finished (row below empty)
            if y == height - 1 { bounds.h = y - y_top + 1; break; }     // hit bottom
            y += 1;                                                     // next row, same anchor x
        }
        candidates.push(Candidate { bounds, points });
        metrics.structure_candidates += 1;
        // zero the WHOLE bounding box (not just the points) so nothing inside is revisited
        for yy in bounds.y..bounds.bottom() { for xx in bounds.x..bounds.right() { map[yy][xx] = 0.0; } }
    }
}
```

Properties worth preserving: grows down/right only; the point list can contain pixels outside the
final structure for concave shapes but the bbox is correct; zeroing the full bbox merges anything
inside a candidate's bbox into that candidate (no separate detection).

---

## 5. Per-candidate measurement (`ComputeStarParameters`)

Inputs: measurement image, candidate `bounds` + `points`, `MeasurementNoiseSigma` (`σ`), params.

### 5.1 Background annulus

Expanded box = bounds grown by `BackgroundBoxExpansion` (default 3) on each side, clipped to the
image. Annulus = expanded box minus the candidate bbox. For each annulus pixel record
`(dx, dy, value)` where `(dx, dy)` are offsets from the **bbox center** `(cx, cy) = (bounds.x +
w/2, bounds.y + h/2)`.

- `background_median` = **upper median** of annulus values (`sorted[count >> 1]`).
- `local_background_sigma` = `1.4826 * median(|v − background_median|)` if annulus count ≥ 8
  (const `MinPixelsForLocalSigma = 8`), else `0` ⇒ fall back to global `σ` for the contamination scale.
  **Note the median convention here is the TRUE median** (`ComputeMedian`: average of the two middle
  deviations for even counts), NOT the upper median — this differs from the plane-fit residual scale in
  §5.2, the adaptive grid in §3.6, and the flux `star_median`-vs-annulus sites which use the upper median.
  Concretely: sort `dev[i] = |v_i − background_median|` ascending; `mad = (count odd) ? dev[count/2]
  : (dev[count/2 − 1] + dev[count/2]) / 2`; `local_background_sigma = 1.4826 * mad`.
  `contamination_sigma = local_background_sigma > 0 ? local_background_sigma : σ`.

### 5.2 Robust local background plane (IRLS, Huber)

Model: `B(x, y) = b0 + b1·dx + b2·dy`, `dx = x − cx`, `dy = y − cy`.

```rust
fn gradient_contamination(dx, dy, val, n, fallback_sigma, sensitivity /* ContaminationSensitivity */,
                          min_sector_pixels /* = 8 */) -> Result {
    if n < 12 { return NO_PLANE; }               // 3-param plane needs margin
    let mut w = vec![1.0; n];
    const HUBER_C: f64 = 1.345;
    for _iter in 0..4 {
        // weighted LS plane via 3×3 normal equations, Gaussian elimination w/ partial pivoting;
        // pivot magnitude < 1e-12 => singular geometry => NO_PLANE
        (b0, b1, b2) = solve_weighted_plane(dx, dy, val, &w)?;
        let sigma = 1.4826 * upper_median(|r_i|);           // r_i = val - plane
        if sigma <= 0.0 { break; }                           // exact fit
        for i in 0..n { let z = |r_i| / sigma; w[i] = if z <= HUBER_C { 1.0 } else { HUBER_C / z }; }
    }
    // final robust residual scale
    let mut local_sigma = 1.4826 * upper_median(|r_i|);
    if local_sigma <= 0.0 { local_sigma = fallback_sigma; }

    // contamination decision — only when sensitivity > 0
    if sensitivity <= 0.0 || local_sigma <= 0.0 { return plane_only; }
    // 8 angular octants of (dx,dy), arranged so octant s and s+4 are opposite:
    //   dx>=0,dy>=0: |dx|>=|dy| -> 0 else 1 ; dx>=0,dy<0: |dx|>=|dy| -> 7 else 6
    //   dx<0, dy>=0: |dx|>=|dy| -> 3 else 2 ; dx<0, dy<0: |dx|>=|dy| -> 4 else 5
    for s in 0..8 {
        let count = sector[s].len();  if count < min_sector_pixels { continue; }
        let med = sector_sorted[s][count >> 1];              // upper median of residuals
        let se = 1.2533 * local_sigma / sqrt(count);         // 1.2533 = sqrt(pi/2), SE of a median
        if med > sensitivity * se { suspected = true; }      // one-sided POSITIVE excess only
    }
}
```

- The plane (when the fit is non-singular and n ≥ 12) becomes the **local background** everywhere
  downstream (clipping, flux, centroid, HFR, PSF), anchored at `(cx, cy)`. Otherwise a **flat**
  plane at `background_median`.
- Contamination = a contaminant *adds* light in one direction; smooth gradients are removed by the
  plane and edge-clip deficits are negative, so only positive sector medians trip it.
  Default `ContaminationSensitivity = 5.0`; `0` disables the test.

### 5.3 Clip margin & flux statistics

```
candidate_size = max(bounds.w, bounds.h)
clip_mult = if DefocusAwareDonutDetection && DefocusDistortionSizeReference > 0
               && candidate_size >= DefocusDistortionSizeReference
            { min(StarClippingMultiplier, 2.0) }             // donut cap = 2.0
            else { StarClippingMultiplier }                   // default 2.0
clip_margin = clip_mult * MeasurementNoiseSigma
```

Over the candidate's **structure points** (not the whole bbox): a pixel *survives the clip* iff
`raw > plane(x, y) + clip_margin` (strictly greater).

- Pass 1 counts survivors and tracks min/max of `(raw − plane)`.
  **Degenerate** ⇒ return null (rejected) when `survivors == 1 || max <= min`
  (this also catches `survivors == 0`, since min starts at 1.0 and max at 0.0).
- Pass 2: `total_flux = Σ (raw − plane)`; `peak = max`; collect survivor fluxes; sort;
  `star_median` = true median (average of middles for even counts);
  `mean_flux = total_flux / survivors`  ← divisor is **clip-survivor count**, not footprint size;
- `normalized_brightness = peak − (1 − PeakResponse) * mean_flux` (PeakResponse default 0.75).
- Carried separately: `PixelCount = points.len()` (full footprint) and
  `UnclippedPixelCount = survivors`.

### 5.4 Iterative flux-weighted centroid (3 passes)

`aperture_radius = min(bounds.w, bounds.h) / 2`.

```rust
// pass 1: all structure points that survive the clip (raw > plane + clip_margin), weight = raw - plane
cx = Σ flux·x / Σ flux ; cy = Σ flux·y / Σ flux
// if no pixel survives: fall back to the UNWEIGHTED mean of the structure points
//   (or (0,0) if the point list is empty — cannot happen in practice)
// passes 2..3: same sums restricted to points within aperture_radius (squared test) of the
//   previous estimate; if a pass excludes everything, keep the previous estimate and stop
```

- `background_at_center = plane(cx, cy)` → `Star.Background`.
- `center_brightness = bilinear_sample(image, cx, cy) − background_at_center` (stored on the
  candidate; used later as the PSF amplitude seed indirectly — see §9.2 note).

**Bilinear sampling** (used by centroid brightness, HFR, PSF): floor to `(x0, y0)`, clamp
`x1 = min(w−1, x0+1)`, `y1 = min(h−1, y0+1)`, standard bilinear on the 4 taps. No bounds check
below 0 (callers stay inside bboxes).

---

## 6. Acceptance gates (exact order and formulas)

Run per candidate, in parallel; a candidate stops at the first failing gate. Every rejection is
tallied under exactly one metric.

| # | Gate | Condition to REJECT | Metric |
|---|------|---------------------|--------|
| 1 | TooSmall | `bounds.w < MinimumStarBoundingBoxSize \|\| bounds.h < MinimumStarBoundingBoxSize` (default 5) | TooSmall |
| 2 | OnBorder | `bounds.x == 0 \|\| bounds.y == 0 \|\| bounds.right == width \|\| bounds.bottom == height` (star assumed clipped) | OnBorder |
| 3 | TooElongated (opt-in) | master ON && `DonutMaxStreakEccentricity < 1.0` && `points.len() >= 8` && point-cloud eccentricity `>=` threshold | TooElongated |
| 4 | TooDistorted | `fill_ratio < effective_max_distortion` (see below) | TooDistorted |
| 5 | Degenerate | `ComputeStarParameters` returned null (§5.3) | Degenerate |
| — | Saturated (tally only) | `background + peak >= SaturationThreshold` ⇒ record in SaturatedBounds; **not rejected** (PSF fit masks saturated pixels instead) | Saturated |
| 6 | LowSensitivity | `sensitivity <= Sensitivity` (default 2.0; see below) | LowSensitivity |
| 7 | NotCentered | centroid outside the acceptance sub-box (see below) | NotCentered |
| 8 | TooFlat | `star_median >= PeakResponse * peak` (default PeakResponse 0.75). Intentionally active during AF too. | TooFlat |
| 9 | HFRAnalysisFailed | `MeasureStar` returned false (`Σ weighted flux <= 0`) | HFRAnalysisFailed |
| 10 | TooLowHFR | `hfr <= MinHFR` (default 1.2 px) | TooLowHFR |
| 11 | Contaminated | contamination suspected (§5.2) && `RejectContaminatedStars` (default true). Bounds recorded even when kept. | ContaminationSuspected |
| 12 | BloomSuppressed (opt-in, post-pass) | master ON && `DonutSaturationBloomRadius > 0` && star not itself saturated && centroid within radius of any saturated star's bbox center | BloomSuppressed |

### 6.1 Point-cloud eccentricity (gate 3)

Second moments of the raw structure points (unweighted):
`e = sqrt(1 − λ2/λ1)` where λ1 ≥ λ2 are eigenvalues of the 2×2 covariance
(`λ = tr/2 ± sqrt(max(0, tr²/4 − det))`). Degenerate inputs (n < 3, λ1 ≤ 0) ⇒ 0.
A line → e → 1 (spike/satellite trail); rings/disks are small. Default threshold 1.0 = disabled.

### 6.2 Fill ratio / TooDistorted (gate 4)

```
d = max(bounds.w, bounds.h)
hole = if master && DonutMinAnnularityHoleFraction > 0 { count_enclosed_hole(...) } else { 0 }
fill_ratio = (points.len() + hole) / (d * d)          // perfect disk ≈ π/4 ≈ 0.79
effective_max_distortion =
    if !DefocusAwareDistortion && !DefocusAwareDonutDetection { MaxDistortion }        // default 0.5
    else if d <= 0 || SizeRef <= 0 { MaxDistortion }
    else { MaxDistortion * clamp(SizeRef / d, DefocusDistortionMinFactor, 1.0) }
```
`count_enclosed_hole`: rasterize points into a bbox-sized bool mask; flood-fill background from all
border cells (4-connectivity); enclosed hole = background cells not reached; return hole count if
`hole >= DonutMinAnnularityHoleFraction * w * h` else 0. Bboxes with `w <= 2 || h <= 2` ⇒ 0.

### 6.3 Sensitivity (gate 6)

```
sensitivity = normalized_brightness / MeasurementNoiseSigma
if master && σ > 0 && survivors > 0 && d >= DefocusDistortionSizeReference {
    integrated_snr = total_flux / (σ * sqrt(survivors))     // matched-filter statistic
    sensitivity = max(sensitivity, integrated_snr)
}
reject if sensitivity <= Sensitivity                         // note: <=, not <
```

### 6.4 Centering (gate 7)

```
tol = if DefocusAwareCentering || master {
          min(1.0, StarCenterTolerance * clamp(max(w,h) / SizeRef, 1.0, DefocusCenteringToleranceFactor))
      } else { StarCenterTolerance }                          // default 0.3
sub_w = bounds.w * tol; sub_h = bounds.h * tol               // sub-box concentric with bbox
accept iff centroid ∈ [bbox_center ± sub/2] on both axes (inclusive)
```

### 6.5 Result assembly

Accepted star fields: `center` (centroid), `background` (plane at centroid), `background_plane`,
`mean_brightness = total_flux / points.len()` (**full footprint** divisor — intentional, feeds only
AF brightest-star selection), `peak_brightness = peak`, `bounding_box`, `hfr`,
`contamination_suspected`, `relaxation_admitted` (informational: passed a relaxed gate that the
strict threshold would have failed).

Stars are assembled in candidate (raster) order regardless of thread completion order; all metric
bounds lists are sorted by (Y, X) for determinism.

---

## 7. HFR measurement (`MeasureStar`)

Flux-weighted mean radius over a circular aperture, sampled on a grid of step
`AnalysisSamplingSize` (default 1.0) with per-pixel background-plane subtraction:

```rust
let plane = star.background_plane.unwrap_or(flat_plane(star.center, star.background));
let aperture_radius = min(bbox.w, bbox.h) as f64 / 2.0;
// grid aligned so the centroid is exactly one of the samples:
let start_x = cx - step * floor((cx - bbox.left) / step);
let start_y = cy - step * floor((cy - bbox.top)  / step);
let tau = effective_clip_multiplier(p, max(bbox.w, bbox.h)) * measurement_noise_sigma;  // §5.3
let (mut num, mut den) = (0.0, 0.0);
for y in (start_y ..= bbox.bottom).step(step) {
    for x in (start_x ..= bbox.right).step(step) {         // inclusive end (<=)
        let dist = hypot(x - cx, y - cy);
        if dist > aperture_radius + 0.5 { continue; }       // fully outside
        let flux = bilinear(img, x, y) - plane.value_at(x, y);
        if flux > tau {
            let v = match hfr_tau_policy {                  // default GateOnly
                GateOnly => flux,
                SubtractTau => flux - tau,                  // legacy soft-threshold (biases low)
            };
            let w = 1.0 - max(0.0, dist - (aperture_radius - 0.5));   // partial-pixel edge weight
            num += w * v * dist; den += w * v;
        }
    }
}
if den > 0.0 { star.hfr = num / den; true } else { false }   // false => HFRAnalysisFailed
```

Saturated pixels are **not** masked in HFR (masking would bias further; the frame-level median and
the saturated-exclusion aggregation limit the damage).

---

## 8. Post-detection processing (frame level)

### 8.1 Inner-ROI star crop

Only when the region is **not full-frame** AND has an inner crop boundary
(`!Region.IsFull() && InnerCropBoundary != null`): drop stars whose bbox is **contained in or
intersects** the inner rect (rounded via `round(ratio * size)` here, unlike the detector's
floor/truncate); count dropped as `OutsideROI`.

### 8.2 HFR outlier rejection (only `MeasurementAverage == MeanOutliers`)

With >1 star: `(median, mad_sigma) = median_mad(hfrs)` where `mad_sigma = 1.483 * MAD` (median of
absolute deviations, true-median convention); keep stars with
`hfr ∈ [median − 3.0·mad_sigma, median + high·mad_sigma]`, `high = 3.0` when NINA's star
sensitivity is Normal else `4.0`.

### 8.3 PSF aggregation (only if `ModelPSF`)

Over stars with an accepted PSF (need ≥ 2): `Sigma = median(psf.sigma)`,
`PSFRSquared = median(psf.r_squared)`, `FWHM/FWHMMAD = median_mad(psf.fwhm_arcsec)`,
`Eccentricity/EccentricityMAD = median_mad(psf.eccentricity)`.

### 8.4 AF star selection (`NumberOfAFStars > 0`)

- No prior positions: take top `N` by score `0.3·hfr + 0.7·mean_brightness`; export their centers.
- With prior positions (subsequent AF exposures): for each prior position pick the nearest detected
  star (no distance cap).

### 8.5 Frame HFR aggregation

`hfr_stars` = stars with `background + peak_brightness < SaturationThreshold` if
`ExcludeSaturatedStarsFromHFR` (default true) **and** at least 3 unsaturated remain; else all stars.
With > 1 star:
- `Median` mode (default): `AverageHFR = median(hfr)`, `HFRStdDev = 1.483·MAD`.
- `MeanOutliers` mode: arithmetic mean and sample stddev (divisor n−1) over the post-rejection list.

Star list is finally ordered by `position.y * image_width + position.x`.

Pixel scale (arcsec/px) fed to PSF: `pixel_size_um / focal_length_mm * 206.2648...` (=
`180/π · 3600 / 1000`) `* binning`; NaN triggers a one-time warning only.

---

## 9. PSF model fitting

Runs only when `ModelPSF == true` (AutoFocus path **forces it off**; a "Review Frames" run re-enables
it). Executed after gating, per star, partitioned into batches of `PSFParallelPartitionSize`
(default 100; ≤ 0 ⇒ single batch), one task per batch.

### 9.1 Sample extraction (`PSFModeler::create`)

```
nominal_width  = sqrt(bbox.w * bbox.h)
sampling_size  = nominal_width / PSFResolution              // default PSFResolution = 10
start_x = cx - s * floor((cx - bbox.left) / s)              // centroid on-grid, same as HFR
start_y = cy - s * floor((cy - bbox.top)  / s)
for y in (start_y .. bbox.bottom).step(s) {                  // EXCLUSIVE end (<) — unlike HFR
  for x in (start_x .. bbox.right).step(s) {
    v = bilinear(img, x, y);
    if v >= SaturationThreshold { continue; }                // mask saturated (raw value test)
    if plane is non-flat { v -= plane.value_at(x,y) - background; }  // remove tilt only:
        // zero at the star center, so the constant background stays for the model's B
    inputs.push([x - cx, y - cy]); outputs.push(v);
  }
}
if inputs.len() < 10 { return None; }                        // MinUnsaturatedPixels = 10 -> PSFFitFailed
centroid_brightness = bilinear(img, cx, cy)                  // NOT background-subtracted here
```

### 9.2 Parameterization, initial guess, bounds, scaling

Parameters (7; fittable-β Moffat has 8): `[A, B, x0, y0, σx, σy, θ]` (+ `β`).
`A` = amplitude above background at the centroid; `B` = constant background; `(x0, y0)` = center
offset **relative to the detector centroid** (inputs are centroid-relative).

- **Init**: `A = max(0, centroid_brightness − star.background)`; `B = star.background`;
  `x0 = y0 = 0`; `θ = 0`; `β = 4.0` (fittable type).
- **σ seed** — flux-weighted second moments of the samples:
  `w_i = output_i − B` (only `> 0` contribute); `σx = sqrt(Σ w·dx² / Σ w)`, same for y.
  Fallback `bbox/3.0` per axis when Σw ≤ 0 or a variance ≤ 0. Clamp both to
  `[0.5, max(bbox.w, bbox.h)]`. Enforce `σx ≥ σy` (swap), then `σx *= 1.001` (asymmetric nudge so
  LM starts unambiguously in the σx>σy basin; prevents ±π/2 θ flips between frames).
- **Bounds** (box constraints):
  `A ∈ [0, 2]`, `B ∈ [0, 1]`, `x0 ∈ [−w/2, w/2]`, `y0 ∈ [−h/2, h/2]`,
  `σx, σy ∈ [0, sqrt(w² + h²)/2]`, `θ ∈ [−π/2, π/2]`, `β ∈ [1, 10]`.
- **Variable scaling** (LM `set_scale`): `[0.01, 0.01, 0.1, 0.1, 1, 1, 1(, 1)]`.

### 9.3 Models (exact)

Rotated frame: `X = (x−x0)cosθ + (y−y0)sinθ`, `Y = −(x−x0)sinθ + (y−y0)cosθ`.

**Elliptical Gaussian** (PixInsight DynamicPSF convention):
```
E = X²/(2σx²) + Y²/(2σy²)
model(x,y) = B + A · exp(−E)
FWHM = σ · 2√(2 ln 2)  ≈ σ · 2.35482
```
Analytic gradient (used when `pixel_integration == false`):
```
∂/∂A = e^{−E}                    ∂/∂B = 1
∂/∂x0 = A (cosθ·X/σx² − sinθ·Y/σy²) e^{−E}
∂/∂y0 = A (sinθ·X/σx² + cosθ·Y/σy²) e^{−E}
∂/∂σx = A X²/σx³ · e^{−E}        ∂/∂σy = A Y²/σy³ · e^{−E}
∂/∂θ  = A · XY · (1/σy² − 1/σx²) · e^{−E}
```

**Elliptical Moffat** (fixed β ∈ {4.0, 2.5, 1.5} or fittable):
```
D = 1 + X²/σx² + Y²/σy²
model(x,y) = B + A · D^{−β}
FWHM = σ · 2·sqrt(2^{1/β} − 1)
```
Analytic gradient (fixed-β only; fittable-β always uses finite differences):
```
∂/∂A = D^{−β}                    ∂/∂B = 1
∂/∂x0 = −Aβ (2sinθ·Y/σy² − 2cosθ·X/σx²) D^{−β−1}
∂/∂y0 = −Aβ (−2sinθ·X/σx² − 2cosθ·Y/σy²) D^{−β−1}
∂/∂σx = (2Aβ/σx³)·X² · D^{−β−1}  ∂/∂σy = (2Aβ/σy³)·Y² · D^{−β−1}
∂/∂θ  = −Aβ · 2XY (1/σx² − 1/σy²) D^{−β−1}
```

**Pixel-area integration** (opt-in, `PSFPixelIntegration`, default false; for undersampled rigs):
- Gaussian: separable erf integral in the rotated frame with half-extents
  `hx = (|cosθ| + |sinθ|)/2`, `hy` likewise;
  `ΔΦ_axis = [erf((c+h)/(σ√2)) − erf((c−h)/(σ√2))]/2` at rotated-frame center `c`;
  normalization `norm_axis = erf(h/(σ√2))`; value = `B + A·(ΔΦx/normx)·(ΔΦy/normy)`
  (falls back to the point sample if a norm is 0). Peak-normalized so A keeps its point-sample
  meaning. Forces finite-difference Jacobian.
- Moffat: 2×2 subsample average at offsets ±0.25 in x and y. Forces finite-difference Jacobian.

### 9.4 Solver — Levenberg-Marquardt (ALGLIB `minlm`, managed `alglib.net 3.19.0`)

Residual vector: `f_i(p) = model(p, input_i) − output_i` (one per sample). The LM engine minimizes
`Σ f_i²` subject to the box constraints.

- Analytic-Jacobian mode (`minlmcreatevj` + `minlmsetacctype(1)`) when the model has an analytic
  gradient (Gaussian/fixed-β Moffat, no pixel integration).
- Finite-difference mode (`minlmcreatev`, `diffstep = 1e-4`) otherwise.
- Termination: `minlmsetcond(epsx = tolerance, maxits = max_iterations)` — called with
  `tolerance = 1e-8`, `max_iterations = 0` (0 = ALGLIB "automatic/unbounded" iteration cap).
- Failure: ALGLIB termination type < 0 raises an error (−8 = NaN/Inf in function or Jacobian,
  −3 = inconsistent constraints); the caller catches any error and counts the star as PSFFitFailed.

**Robust mode** (`UsePSFAbsoluteDeviation == true`, default false) — IRLS with Huber weights around
the LM core:

```
δ = 1.5 * MeasurementNoiseSigma                  // HuberThresholdMultiplier = 1.5
weights = [1.0; n]; prev_sum = +inf; iters = 0;
lm_budget = (max_iterations > 0) ? min(max_iterations, 20) : 20;
while sum_delta > 1e-6 && iters++ < lm_budget {          // toleranceIRLS = 1e-6
    // inner LM is set up fresh each round with minlmsetcond(epsx = toleranceLM, maxits = lm_budget)
    // — i.e. in IRLS mode the inner LM's iteration cap is the SAME clamped budget (20 by default),
    // NOT the 0/unbounded cap the plain Solve path uses
    run LM on weighted residuals: f_i' = sqrt(w_i) * f_i (jacobian rows scaled the same);
    seed next round with this solution;
    for each i: r = |model(sol, x_i) − y_i|; sum += r;
                w_i = if r <= δ { 1.0 } else { δ / r };  // Huber
    sum_delta = |sum − prev_sum|; prev_sum = sum;
}
```
(As coded, the `maxIterationsIRLS = 10` argument is **ignored** — verified: `SolveIRLS` never reads
it; the outer loop bound AND the inner LM `maxits` are both the clamped LM budget of 20.
`toleranceIRLS = 1e-6` comes from the `PSFModeler.Solve` call site. Reproduce as-is or fix
knowingly. The fittable-β variant overrides `SolveIRLS` with the identical structure over 8
parameters, finite-difference mode, `diffstep = 1e-4`.)

### 9.5 Solution canonicalization & goodness of fit

```
if σx.is_nan() || σy.is_nan() { return None; }
θ = euclidean_mod(θ, π); if θ > π/2 { θ -= π; }   // wrap to (−π/2, π/2]
θ = −θ;                                            // sign convention: rotate star back to axes
if σy > σx { swap(σx, σy); θ += if θ < 0 { π/2 } else { −π/2 }; }   // σx = major axis, always
fwhm_x = sigma_to_fwhm(σx); fwhm_y = sigma_to_fwhm(σy)
// UNWEIGHTED R² over all samples at the canonical params:
rss = Σ (model − y)²; tss = Σ (y − mean(y))²; r² = 1 − rss/tss
reduced_χ² = rss / (n * σ_noise²)                   // NaN if σ_noise == 0 or n == 0
```

Derived (PSFModel): `sigma = sqrt(σx·σy)`; `fwhm_px = sqrt(fwhm_x·fwhm_y)`;
`fwhm_arcsec = fwhm_px · pixel_scale`; `eccentricity = sqrt(1 − b²/a²)` with
`a = max(fwhm_x, fwhm_y)`, `b = min(...)`; `offset_x/offset_y` = fitted `(x0, y0)` (relative to the
detector centroid); β = fixed or fitted value (NaN for Gaussian).

**Acceptance**: the fit is attached to the star iff `r² >= PSFGoodnessOfFitThreshold` (default
0.9); otherwise `PSFFitFailed += 1` (atomically — partitions run in parallel) and the star keeps
`psf = None` (it still counts as detected).

---

## 10. Parallelization structure

- **Early stage**: sequential except (a) the two κ-σ noise estimates run as two concurrent tasks
  while the main thread does the wavelet work; (b) the adaptive-binarization block grids
  parallelize by grid row. OpenCV kernels use their own internal threading.
- **Candidate collection**: strictly sequential (mutates the structure map).
- **Late stage** (gates + measurement): `parallel for` over candidates with
  `max_parallelism = MaxStarEvaluationParallelism` (≤0 ⇒ logical CPU count; 1 ⇒ sequential
  kill-switch), all inner loops share one process-wide scheduler capped at `ProcessorCount` so
  concurrent detections cannot multiply threads. Per-thread metrics are merged (summed /
  concatenated) afterward; results are written to an index-addressed array so output order is the
  deterministic raster order; bounds lists sorted (Y, X).
- **PSF stage**: stars partitioned into `PSFParallelPartitionSize`-sized batches, one task each;
  `PSFFitFailed` incremented atomically.
- **Determinism**: everything except quickselect pivots (perf-only) is deterministic; identical
  inputs + params ⇒ byte-identical outputs. The upstream code keys result caches on a SHA-256 of
  all output-affecting params + a detector version int — worth replicating in AstroDeck if
  detection results are ever persisted/reused.

## 11. Native / third-party calls

The C# implementation calls **OpenCV** (native, via OpenCvSharp 4.6) for these primitives — in Rust
each is replaceable with `opencv` crate calls or hand-rolled loops:

| Call | Used for | Semantics to reproduce |
|---|---|---|
| `MedianBlur(3)` | hotpixel filter | 3×3 median, in place; border replicate (OpenCV medianBlur ksize 3 uses border replicate internally) |
| `Absdiff`, `Threshold(Binary)`, `CountNonZero`, masked `CopyTo` | thresholded hotpixel filter | strict `>` threshold ⇒ {0,1}; copy blurred→img where mask |
| `GetGaussianKernel(n, σ)` + `SepFilter2D(reflect)` | noise reduction, post-wavelet blur, wavelet layers (custom kernel) | separable convolution, reflect border (`BORDER_REFLECT`, edge pixel not duplicated? OpenCV `Reflect` = `fedcba|abcdefgh|hgfedcb`) |
| `InRange` + masked `MeanStdDev` | κ-σ estimate | mean/σ over masked pixels, σ divisor N |
| `Subtract` + `Max`/`Min` | wavelet residual subtraction + clamp [0,1] | element-wise |
| `GetStructuringElement(Ellipse, k)` + `MorphologyEx(Dilate/Close, reflect)` | structure dilation; donut close | OpenCV ellipse SE |
| `Threshold(Binary, 1.0)` | binarization | `dst = src > t ? 1.0 : 0.0` |
| `Resize(Linear)` | adaptive threshold surface upsample (grid → full res) | bilinear |
| `MeanStdDev`/`Mean` | statistics helper | — |

**ALGLIB** (`alglib.net` 3.19.0 — pure managed C#, *not* native): `minlm*` family = box-constrained
Levenberg-Marquardt (`minlmcreatevj`/`minlmcreatev`, `minlmsetbc`, `minlmsetcond`, `minlmsetscale`,
`minlmsetacctype(1)`, `minlmoptimize`, `minlmresults`). Rust equivalent: any bounded LM (e.g.
`levenberg-marquardt` crate + parameter clamping, or a port of MPFIT/MINPACK with box constraints);
must support (a) user Jacobian and finite-difference modes, (b) per-variable scaling, (c) `epsx`
step-size convergence, (d) box bounds. **MathNet.Numerics** supplies `erf` (Gaussian pixel
integration only).

---

## 12. Configuration knobs (defaults + valid ranges)

Detector params (single struct; "early" params change candidate formation, "late" params only gate/measure):

| Knob | Default | Range (options validation) | Stage | Meaning |
|---|---|---|---|---|
| HotpixelFiltering | true | bool | early | 3×3 median hotpixel removal |
| HotpixelThresholdingEnabled | true | bool | early | replace only pixels deviating > threshold |
| HotpixelThreshold | 0.001 | (0, 1] | early | fraction of full scale |
| HotpixelFilterRadius | 1 | only 1 supported | early | any other value ⇒ error |
| StarMeasurementNoiseReductionEnabled | false | bool | early | blur the measurement image too |
| NoiseReductionRadius | 3 | ≥ 0 | early | Gaussian kernel = 2r+1 (structure-map source; also measurement if flag on) |
| NoiseClippingMultiplier | 2.0 | ≥ 0 | early | κ-σ clip AND binarize k (was 4.0 historically; 2.0 per recall audit) |
| LocallyAdaptiveBinarization | **true** | bool | early | per-block threshold surface instead of scalar |
| AdaptiveNoiseBlockSize | 128 | [16, 1024] | early | block side (px) for local median/σ grid |
| StructureLayers | 4 | ≥ 1 | early | wavelet layers; also post-blur kernel = 2n+1 |
| StructureDilationSize | 3 | ≥ 3 | early | ellipse SE size |
| StructureDilationCount | 0 | ≥ 0 | early | 0 ⇒ no dilation |
| Region (outer/inner ROI) | full | ratios [0,1) | early | ROI crop + inner clear |
| SaturationThreshold | 0.99 | (0, 1] | early metric + late | saturation tally, PSF masking, HFR-aggregation exclusion |
| DefocusAwareDonutDetection (master) | false | bool | early | gates ALL donut behavior below |
| DonutMorphCloseSize | 5 | [1, 25] | early | close kernel; ≤1 ⇒ off |
| DefocusAwareStructure | false | bool | early | extra wavelet layers |
| StructureLayerBoost | 0 | [0, 6] | early | added layers when above flag on (master-on w/o flag ⇒ implicit +2) |
| Sensitivity | 2.0 | ≥ 0 | late | min (peak-based or integrated) SNR |
| PeakResponse | 0.75 | > 0 | late | TooFlat gate + NormalizedBrightness correction |
| MaxDistortion | 0.5 | [0, 1] | late | min fill-ratio (π/4 ≈ 0.79 = perfect disk) |
| StarClippingMultiplier | 2.0 | ≥ 0 | late | per-pixel clip τ multiplier (flux/centroid/HFR) |
| HfrTauPolicy | GateOnly | GateOnly \| SubtractTau | late | how τ applies in the HFR sum |
| StarCenterTolerance | 0.3 | (0, 1] | late | centered acceptance sub-box ratio |
| BackgroundBoxExpansion | 3 | ≥ 1 | late | annulus width (px) |
| MinimumStarBoundingBoxSize | 5 | ≥ 1 | late | TooSmall gate |
| MinHFR | 1.2 | ≥ 0 | late | TooLowHFR gate (was 1.5 pre gate-only-τ) |
| AnalysisSamplingSize | 1.0 | (0, 1]* | late | HFR sampling step (*options expose ≤ 1.0) |
| ContaminationSensitivity | 5.0 | ≥ 0 (0 = off) | late | sector-median SE multiplier |
| RejectContaminatedStars | true | bool | late | reject vs flag-only |
| DefocusAwareDistortion / Centering | false | bool (forced off unless master on) | late | gate relaxations |
| DefocusDistortionSizeReference | 30.0 | [1, 1000] | late | bbox max-dim (px) defocus proxy |
| DefocusDistortionMinFactor | 0.25 | [0.01, 1] | late | distortion relaxation floor |
| DefocusCenteringToleranceFactor | 2.0 | [1, 10] | late | centering relaxation ceiling |
| DonutMinAnnularityHoleFraction | 0.15 | [0.02, 0.6] | late | hole-fill threshold |
| DonutMaxStreakEccentricity | 1.0 (off) | [0.8, 1.0] | late | spike gate |
| DonutSaturationBloomRadius | 0.0 (off) | [0, 100] px | late | bloom suppression radius |
| ExcludeSaturatedStarsFromHFR | true | bool | post | needs ≥ 3 unsaturated remaining |
| MeasurementAverage | Median | Median \| MeanOutliers | post | frame HFR aggregation |
| ModelPSF | true (AF forces false) | bool | psf | |
| PSFFitType | Moffat_40 | Gaussian \| Moffat_40/25/15 \| MoffatFittable | psf | |
| UsePSFAbsoluteDeviation | false | bool | psf | Huber IRLS mode |
| PSFGoodnessOfFitThreshold | 0.9 | (0, 1] | psf | R² acceptance |
| PSFResolution | 10 | ≥ 0 | psf | samples across √(w·h) |
| PSFPixelIntegration | false | bool | psf | pixel-area integral models |
| PSFParallelPartitionSize | 100 | ≥ 0 (≤0 ⇒ 1 batch) | psf | |
| PixelScale (arcsec/px) | computed | — | psf | pixelSize/focalLength · 206.265 · binning |
| MaxStarEvaluationParallelism | 0 (auto) | int | perf | 1 = sequential |
| Donut clip cap (const) | 2.0 | — | late | `DonutClipMultiplierCap` |
| Adaptive σ floor (const) | 1e-6 | — | early | `AdaptiveBinarizationSigmaFloor` |
| Huber IRLS δ multiplier (const) | 1.5 | — | psf | |
| Huber plane-fit c (const) | 1.345 | — | late | |
| Contamination min sector px (const) | 8 | — | late | |
| PSF min samples (const) | 10 | — | psf | `MinUnsaturatedPixels` |
| Min unsaturated stars for HFR aggregation (const) | 3 | — | post | |

Additional knobs verified in source but outside the detector-params struct:

| Knob | Default | Effect |
|---|---|---|
| UseAutoFocusCrop | true | when **false** and the detection is not a NINA-stock-AF run, `UseROI` is forced off — the AF inner/outer crop region is NOT applied to regular exposures |
| CollectContaminationDiagnostics | false | fills per-star sector-median diagnostic records (§5.2 `fillSectors`); no effect on accept/reject |
| UseAdvanced | **false** | selects Simple mode (preset-derived values, below) vs the advanced knob values |
| UseOptimizedSettings | false | Simple mode only: applies a persisted optimizer snapshot of the curated knobs (BrightnessSensitivity, Star/NoiseClippingMultiplier, StarPeakResponse, MaxDistortion, MinHFR, StarCenterTolerance, StructureLayers, NoiseReductionRadius, MinStarBoundingBoxSize, hotpixel + defocus/donut/adaptive axes) on top of the preset baseline |

AutoFocus overrides (applied on top of options): `ModelPSF = false`, no intermediate saves; the
TooFlat gate stays **active** during AF (deliberate — relaxing it admits flat noise blobs).
The `DefocusAwareDonutDetection` master forces `DefocusAwareStructure/Distortion/Centering` off
when it is off. A "Review Frames" run re-enables `ModelPSF` to the options value after the AF
override.

### 12.1 Simple mode (preset → parameter derivation) — the SHIPPED default path

`UseAdvanced` defaults to **false**, so out of the box the effective detector values come from
`DerivePresetSettings()`, re-run whenever a `Simple_*` preset changes (and once at options load).
It overwrites the live advanced properties as follows (options → params mapping then reads those):

```rust
// enums: NoiseLevel { None, Low, Typical, High }  (default Typical)
//        PixelScale { WideField, Typical, LongFocalLength }  (default Typical)
//        FocusRange { Typical, WideRange }  (default Typical)
hotpixel_filtering = noise_level != None;
let mut sensitivity_scale = 1.0;
match noise_level {
    None    => { measurement_nr = false; noise_reduction_radius = 0; }
    Low     => { measurement_nr = false; noise_reduction_radius = 3; sensitivity_scale = 0.2; }
    Typical => { measurement_nr = false; noise_reduction_radius = 3; sensitivity_scale = 0.2; }
    High    => { measurement_nr = true;  noise_reduction_radius = 5; }
}
noise_clipping_multiplier = 2.0; star_clipping_multiplier = 2.0;
structure_layers = 4;
sensitivity = 10.0 * sensitivity_scale;                    // Typical => 2.0; None/High => 10.0
if focus_range == WideRange { structure_layers += 1; sensitivity -= 2.0 * sensitivity_scale; }
min_star_bbox = 5; analysis_sampling_size = 1.0;
match pixel_scale {
    WideField       => { structure_layers -= 1; min_star_bbox -= 1; analysis_sampling_size = 0.5; }
    LongFocalLength => { structure_layers += 1; min_star_bbox += 1;
                         sensitivity -= 2.0 * sensitivity_scale; }
    Typical => {}
}
if hotpixel_thresholding_enabled && hotpixel_filtering {   // both default true
    noise_reduction_radius += 1;                           // Typical => radius 4 (kernel 9)!
}
peak_response = 0.75; max_distortion = 0.5; star_center_tolerance = 0.3;
background_box_expansion = 3; min_hfr = 1.2; dilation = (size 3, count 0);
psf_fit_type = Moffat_40; psf_resolution = 10; psf_fit_threshold = 0.9;
hotpixel_threshold = 0.001;
```

Consequences worth internalizing:

- The **effective out-of-box** `NoiseReductionRadius` is **4** (not the advanced-default 3),
  because the thresholded-hotpixel `+= 1` fires on the Typical preset. All other Typical values
  coincide with the advanced defaults in the table above.
- `sensitivity_scale` (0.2 on Low/Typical) exists for σ-consistency ("F4"): on those presets the
  measurement image is sharp while σ used to be measured on a blurred copy (~4× understated), so
  the preset compensates to keep the effective bar unchanged. On None/High the measured σ is
  honest (no blur / blur applied to measurement too) so the scale is 1.0.
- Presets do **not** touch: `HotpixelThresholdingEnabled`, `ContaminationSensitivity`,
  `RejectContaminatedStars`, `LocallyAdaptiveBinarization`/`AdaptiveNoiseBlockSize`, all
  defocus/donut knobs, `SaturationThreshold`, `ExcludeSaturatedStarsFromHFR`,
  `MeasurementAverage`, `ModelPSF`, `PSFPixelIntegration`, `UsePSFAbsoluteDeviation`,
  `PSFParallelPartitionSize` — those keep their persisted/advanced values.

---

## 13. Edge cases & failure paths (complete list)

1. **Zero/negative pixels** excluded from κ-σ from iteration 0 (exact-zero calibration borders).
2. **κ-σ non-convergence**: capped at 5 iterations; last values returned. `BackgroundMean` lags one
   iteration on the convergence break (as-coded quirk).
3. **Hotpixel radius ≠ 1** ⇒ NotImplemented error before any work.
4. **ROI**: floor/truncate math (§1.3); all outputs offset back; inner-crop is cleared in the map
   AND re-filtered per star at the end (round-based rect).
5. **Flood fill**: last row/column can't start a candidate; whole-bbox zeroing merges enclosed
   structures; candidate point lists may include gap-bridged pixels on bbox-expanding rows.
6. **Degenerate candidate** (§5.3): 0/1 clip survivors or flat (max ≤ min) ⇒ reject (common near
   borders where the annulus resembles the structure).
7. **Annulus too small** (< 12 pts) or singular plane geometry (pivot < 1e-12) ⇒ no plane ⇒ flat
   background at annulus median; contamination test silently off for that star.
8. **Plane exact fit** (residual σ ≤ 0): IRLS stops; local residual σ falls back to
   annulus MAD σ, then to global σ.
9. **Sector with < 8 px**: skipped in the contamination test (no decision from that sector).
10. **Centroid fallback**: no clip survivors ⇒ unweighted point mean; empty point list ⇒ (0,0);
    aperture pass excluding everything ⇒ keep previous pass estimate.
11. **HFR failure**: Σ weighted flux ≤ 0 ⇒ HFRAnalysisFailed (star dropped).
12. **Saturated stars**: never rejected for saturation; tracked, PSF masks saturated samples,
    HFR aggregation may exclude them (≥ 3 unsaturated rule).
13. **PSF sample starvation** (< 10 unsaturated samples) ⇒ no fit ⇒ PSFFitFailed.
14. **PSF NaN σ** ⇒ null fit. ALGLIB termination < 0 ⇒ exception swallowed ⇒ PSFFitFailed.
15. **PSF near-circular ambiguity**: seed swap + 1.001 nudge; post-solve unconditional swap with
    ±π/2 θ rotation keeps σx the major axis and θ continuous across frames.
16. **Gaussian pixel-integration norm underflow** (erf → 0 for tiny σ) ⇒ point-sample fallback.
17. **R² gate**: reject fit only (star survives without PSF).
18. **PSF aggregation** requires ≥ 2 stars with fits, else frame FWHM/Sigma/Eccentricity stay NaN.
19. **Frame HFR** requires ≥ 2 stars, else AverageHFR/HFRStdDev stay at defaults (0).
20. **Pixel scale NaN** (missing profile data): warn once, proceed (FWHM arcsec becomes NaN).
21. **MeanOutliers with ≤ 1 star**: outlier rejection skipped.
22. **Adaptive binarization grid mismatch** (differing block geometry) ⇒ invariant violation error.
23. **Dead-flat block** in adaptive grid: σ floored at 1e-6 so the threshold never collapses to the
    median (which would binarize noise).
24. **Cancellation**: checked per scan row, per candidate, per PSF star; aborts cleanly.
25. **OpenCV load failure** (Windows N SKU without Media Pack): fatal with user guidance —
    irrelevant for a pure-Rust port but explains why everything routes through one library.
26. **PSFType label quirk**: `MoffatPSFAlglibType.PSFType` always reports `Moffat_40` regardless of
    the β it was constructed with, so `PSFModel.psfType` for Moffat 2.5/1.5 fits is mislabeled
    `Moffat_40` (β itself is carried correctly). As-coded; harmless but visible in results.
27. **Fittable-β R²**: computed via the 8-parameter `ComputeRSS8` with the fitted β (the 7-param
    base `ComputeRSS` would evaluate the wrong model); its `GoodnessOfFit` also returns 0 when
    `tss <= 0` instead of dividing by zero.

---

## 14. Source map (MPL-2.0 provenance)

All paths relative to `references/hocusfocus/Joko.NINA.Plugins/Joko.NINA.Plugins.HocusFocus/`,
commit `4d93eaf` of https://github.com/ghilios/joko.nina.plugins.

| Algorithm / item | File | Lines |
|---|---|---|
| Pipeline orchestration (early stage: prep, wavelet, binarize, collect) | `StarDetection/StarDetector.cs` | 424–714 |
| Late stage orchestration (gates, metrics, ROI add-back) | `StarDetection/StarDetector.cs` | 716–813 |
| CFA/debayer hotpixel path | `StarDetection/StarDetector.cs` | 302–323 |
| Hotpixel filters (3×3 median, thresholded, CFA medians) | `Utility/HotpixelFiltering.cs` | 54–195 |
| Gaussian convolution (σ = 0.159758·k default) | `Utility/CvImageUtility.cs` | 69–88 |
| κ-σ noise estimate | `Utility/CvImageUtility.cs` | 512–560 |
| B3-spline à-trous kernel + residual layer | `Utility/CvImageUtility.cs` | 426–486 |
| Subtract+clamp | `Utility/CvImageUtility.cs` | 452–474 |
| Histogram statistics (65536-bucket median/MAD/mean/σ) | `Utility/CvImageUtility.cs` | 268–424 |
| Scalar binarize | `Utility/CvImageUtility.cs` | 562–568 |
| Local background grid (block median / 1.4826·MAD, floor) | `Utility/CvImageUtility.cs` | 570–667 |
| Adaptive binarize (surface compare) | `Utility/CvImageUtility.cs` | 675–688 |
| Adaptive threshold surface assembly (grid combine + bilinear resize) | `StarDetection/StarDetector.cs` | 882–902 |
| Bilinear sampling | `Utility/CvImageUtility.cs` | 488–510 |
| Structure layers boost / donut default boost | `StarDetection/StarDetector.cs` | 574–585 (+ const 114) |
| Donut morph close | `StarDetection/StarDetector.cs` | 664–670 |
| Saturated pixel metric scan | `StarDetection/StarDetector.cs` | 1161–1174 |
| Candidate flood-fill scan | `StarDetection/StarDetector.cs` | 1265–1362 |
| Parallel candidate evaluation + bloom suppression + assembly | `StarDetection/StarDetector.cs` | 1192–1263 |
| Gate sequence (TooSmall … Contaminated) | `StarDetection/StarDetector.cs` | 1573–1797 |
| Effective max distortion (defocus relaxation) | `StarDetection/StarDetector.cs` | 1377–1400 |
| Effective clip multiplier (donut cap 2.0) | `StarDetection/StarDetector.cs` | 1414–1420 (+ const 124) |
| Effective center tolerance | `StarDetection/StarDetector.cs` | 1440–1459 |
| Enclosed-hole (annularity) test | `StarDetection/StarDetector.cs` | 1470–1502 |
| Point-cloud eccentricity | `StarDetection/StarDetector.cs` | 1510–1535 |
| Centering test | `StarDetection/StarDetector.cs` | 1799–1841 |
| Local background σ (annulus MAD, TRUE median via ComputeMedian) | `StarDetection/StarDetector.cs` | 1856–1868 |
| `MedianInPlace` (upper median, used by plane-fit residual σ) | `StarDetection/StarDetector.cs` | 1074–1079 |
| `ComputeMedian` overloads (TRUE median; even → avg of two middles) | `StarDetection/StarDetector.cs` | 1874–1899 |
| IRLS Huber plane fit + octant contamination test | `StarDetection/StarDetector.cs` | 943–1096 |
| Iterative centroid (3-pass) | `StarDetection/StarDetector.cs` | 1920–1987 |
| Star parameters (annulus scan, clip, flux, NormalizedBrightness) | `StarDetection/StarDetector.cs` | 1989–2221 |
| HFR (MeasureStar, τ policy, partial-pixel aperture weight) | `StarDetection/StarDetector.cs` | 1107–1159 |
| PSF partitioned execution + R² gate | `StarDetection/StarDetector.cs` | 824–872 |
| Local background plane type | `Interfaces/IStarDetector.cs` | 642–658 |
| Params + defaults + TauClipPolicy + cache-key machinery | `Interfaces/IStarDetector.cs` | 222–634 |
| Star / metrics / rejection records | `Interfaces/IStarDetector.cs` | 660–912 |
| Region / RatioRect geometry | `Interfaces/IStarDetector.cs` | 52–214 |
| PSF sampling, second-moment seeds, LM setup, IRLS, canonicalization, R²/χ² | `StarDetection/PSFModeler.cs` | 33–609 |
| Gaussian model + gradient + pixel integration | `StarDetection/GaussianPSFType.cs` | 20–255 |
| Moffat model + gradient + fittable β | `StarDetection/MoffatPSFType.cs` | 22–445 |
| PSFModel derived quantities (σ, FWHM, eccentricity) | `StarDetection/PSFModel.cs` | 19–90 |
| Options → params mapping + AF overrides + defaults | `StarDetection/HocusFocusStarDetection.cs` | 299–501 |
| Post-detection aggregation (ROI crop, outliers, PSF stats, AF stars, HFR) | `StarDetection/HocusFocusStarDetection.cs` | 578–711 |
| MedianMAD (1.483), ArcsecPerPixel | `Utility/MathUtility.cs` | 22–101 |
| Parallelism governor | `Utility/ParallelExecution.cs` | 34–77 |
| ALGLIB API surface used | `Utility/AlglibAPI.cs` | 18–43 |
| Option defaults (ResetDefaults) + validation ranges | `StarDetection/StarDetectionOptions.cs` | 274–333, 443–1140 |
| Simple-mode preset derivation (`DerivePresetSettings`, `ConfigureSimpleSettings`, optimized-snapshot apply) | `StarDetection/StarDetectionOptions.cs` | 54–201 |
| UseAutoFocusCrop ROI gating + Review-Frames ModelPSF re-enable | `StarDetection/HocusFocusStarDetection.cs` | 256–280 |
| Options→params mapping (`BuildStarDetectorParams`) + AF overrides (`ApplyDetectionImageContext`) | `StarDetection/HocusFocusStarDetection.cs` | 282–501 |
| HFR-aggregation saturated-star subset (`StarsForHfrAggregation`, min-3 rule) | `StarDetection/HocusFocusStarDetection.cs` | 578–604 |

License note: reproduce the MPL-2.0 attribution for any file whose logic is ported; PHD2-style
BSD does not apply here. The pseudocode in this dossier is a re-expression of the algorithms, but
AstroDeck's Rust files implementing them should carry a provenance comment pointing at this dossier.

---

## 15. Recommended for AstroDeck

**Adopt the current Hocus Focus defaults verbatim** — they are the product of documented empirical
audits (golden-set recall audit, σ-consistency work, AF-bank validation) and are self-consistent as
a bundle (several defaults compensate each other):

- Hotpixel: thresholded filter ON, threshold 0.001.
- Note the two default bundles in upstream: the advanced/ResetDefaults bundle (radius 3) and the
  Simple-Typical preset bundle actually shipped to users (radius **4** — §12.1's `+= 1` for
  thresholded hotpixel filtering). Either is defensible; pick ONE and record it. Radius 4 is what
  the out-of-box plugin runs; radius 3 is what the AF-bank/optimizer work validated against.
- NoiseReductionRadius 3 with **measurement NR OFF** (sharp measurement image) + the honest
  dual-σ scheme (§2) + `Sensitivity 2.0` + `StarClippingMultiplier 2.0` + `GateOnly` τ policy +
  `MinHFR 1.2`. Do **not** mix these with the older values (Sensitivity 10 / MinHFR 1.5 /
  SubtractTau) — they were calibrated against a smoothed σ and a biased HFR.
- `NoiseClippingMultiplier 2.0` with **LocallyAdaptiveBinarization ON** (block 128): validated to
  improve both recall@SNR≥12 and precision. The scalar path is worth keeping as a debug/regression
  switch since it is the bit-identical legacy behavior.
- StructureLayers 4, dilation off, MaxDistortion 0.5, PeakResponse 0.75, StarCenterTolerance 0.3,
  BackgroundBoxExpansion 3, MinimumStarBoundingBoxSize 5.
- Contamination gate ON (sensitivity 5.0, reject = true) — it keeps HFR/PSF statistics clean near
  nebulosity and is cheap (only annulus math).
- Local background **plane** (not scalar median) everywhere — it is what keeps HFR/PSF unbiased on
  gradients; a scalar-median port would silently regress accuracy.
- **PSF**: `Moffat_40` default, point-sampled, least-squares (no IRLS), `PSFResolution 10`,
  R² ≥ 0.9, reduced-χ² reported for diagnostics. Moffat β=4 matches real seeing-limited profiles
  far better than Gaussian in the wings (Gaussian systematically underestimates FWHM); keep
  Gaussian + Moffat 2.5/1.5 + fittable-β as options. Enable `PSFPixelIntegration` only for
  undersampled rigs (FWHM ≲ 1.5 px).
- **AutoFocus profile**: `ModelPSF = false` (HFR-only, big speed win), TooFlat gate active, median
  HFR aggregation (`Median` mode, 1.483·MAD spread), `ExcludeSaturatedStarsFromHFR = true`.
- **Donut/defocus features**: implement but ship default-OFF exactly as upstream (master switch
  `DefocusAwareDonutDetection` forcing the sub-flags). Turn the master ON for AF routines with
  large travel (donut-heavy sweeps); its constants (close 5, hole 0.15, size-ref 30, min-factor
  0.25, centering ×2, clip cap 2.0, +2 wavelet layers) were tuned on real wide-range AF runs.
- **Rust-specific guidance**:
  - Keep the two-stage (early context / late gate+measure) split — it enables cheap parameter
    sweeps and an optimizer later, and the boundary is precisely specified by the "early" params
    column in §12.
  - Solver: any box-constrained LM works; match the seeds/bounds/scales in §9.2 exactly — they, not
    the solver brand, determine result parity. Verify with the θ-canonicalization tests
    (near-circular stars must not flip ±π/2 between frames).
  - Replicate the strict-greater conventions (`>` for binarize/clip, `<=` reject for sensitivity
    and MinHFR) — off-by-one comparisons change star counts on real frames.
  - Parity testing: run both implementations over one FITS with `SaveIntermediateFilesPath`-style
    dumps at each stage (01-source … 07-binarized, star list, metrics) and diff; upstream's stage
    numbering in §0 mirrors its debug dumps.
