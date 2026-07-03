# NINA Star Detection + HFR Measurement — Reimplementation Dossier

Status: extracted 2026-07-03 from the real sources (not from memory). Verified by a second full pass against
the sources on 2026-07-03 (completeness critique); all previously-open gaps that were resolvable from source
are now resolved and folded in.
Primary source: `references/nina` (N.I.N.A., MPL-2.0, © 2016–2024 Stefan Berg and the N.I.N.A. contributors).
Secondary source: Accord/AForge image primitives. **NINA vendors Accord.Imaging inside its own repository**
(`references/nina/Accord.Imaging`, LGPL-2.1, referenced by `NINA.Image.csproj` as a ProjectReference), so the
vendored copy IS the exact code NINA runs — all Accord.Imaging behavior below was verified against that
vendored copy. Only `Accord.Math` (NuGet **3.8.2-alpha**, which is the archived `development` head) is not
in-tree; the two routines NINA uses from it (`SimpleShapeChecker.IsCircle`, `Normal.Kernel2D`) are specified
from the matching upstream source and, for `Kernel2D`, verified numerically. Behavior/math only —
**do not copy Accord code**; reimplement from this spec.

This document is the single source of truth for the Rust implementation. The Rust implementer must NOT read the
C# sources.

---

## 1. Big picture

NINA's "NINA" star detector (`StarDetection` class) computes, per exposure:

- a list of detected stars (position, HFR, brightness stats, bounding box),
- `AverageHFR` (mean HFR over the final star list),
- `HFRStdDev` (sample standard deviation of HFR, N−1 denominator),
- `DetectedStars` (count after all filtering, before optional AF top-N star selection).

Two distinct images are used simultaneously:

1. **Detection image** — the *rendered, auto-stretched* 16-bit image (see §2), converted to 8-bit, optionally
   noise-reduced, downscaled, edge-detected, thresholded, dilated, then blob-labeled. All *structure detection*
   happens here at reduced resolution.
2. **Photometry data** — the *raw, unstretched* `u16` flat array at full resolution (for OSC/bayered images with
   debayer enabled: the debayered luminance channel `Lum = floor((R+G+B)/3)`). All *brightness tests and HFR
   math* happen here at full resolution.

Blob geometry found on the detection image is scaled back to full resolution by `1/resize_factor` before
photometry.

Pipeline:

```
raw u16 frame ──> render + MTF auto-stretch (16-bit) ──┐        (detection path, 8-bit, small)
                                                       ├─> Gray16 (Rgb48→BT.709 gray if debayered)
raw u16 flat array (or debayered Lum) ─────────────────┼─────────────────────────┐
                                                       v                         │ (photometry path,
                        u16 >> 8  →  u8 image                                    │  u16, full res)
                        [noise reduction if W>1552]                              │
                        [bicubic resize if W>1552]                               │
                        Canny(10,80) (blur σ=1.4,5×5 unless already blurred)     │
                        SIS auto-threshold → binary                              │
                        binary 3×3 dilation                                      │
                        connected-component labeling (8-conn)                    │
                        per-blob: size filter → ROI filter → circle/ecc check    │
                        scale rect+center+radius to full res ────────────────────┤
                                                                                 v
                        local-max + bright-pixel star test  (raw u16 data)
                        HFR (flux-weighted mean radius, background-subtracted)
                        radius σ-clip over star population
                        [optional AF top-N star selection]
                        mean HFR + sample σ
```

---

## 2. Input preparation: what image the detector actually sees

This is critical for parity: NINA runs structure detection on the **auto-stretched** image, not on linear data.
Both the imaging path (`RenderedImage.DetectStars`) and the autofocus path (`AutoFocusVM.EvaluateExposure` →
`PrepareImage(autoStretch: true)`) hand the detector a `RenderedImage` whose `Image` is the stretched
`BitmapSource`. Detection contrast therefore depends on the stretch below.

### 2.1 MTF auto-stretch (midtones transfer function)

Source: `NINA.Image/ImageAnalysis/ImageUtility.cs` (`GetStretchMap`, `MidtonesTransferFunction`,
`NormalizeUShort`, `DenormalizeUShort`).

Constants / defaults (profile `ImageSettings`):
- `AutoStretchFactor` (a.k.a. `targetHistogramMedianPercent`) default **0.2**
- `BlackClipping` (a.k.a. `shadowsClipping`) default **−2.8**
- MAD→σ scale factor: **1.4826**

Definitions (all in normalized [0,1] space; `bit_depth` is the image's stated bit depth):

```
normalize(v)   = v / (2^bit_depth − 1)
denormalize(x) = u16( x * 65535 + (if x < 0.5 { 0.5 } else { 0.0 }) )   // note asymmetric rounding
MTF(m, x)      = 0                                   if x <= 0
               = 1                                   if x >= 1
               = ((m − 1) * x) / ((2m − 1) * x − m)  if 0 < x < 1
```

Stretch-map construction (`median`, `MAD` computed over the full image at native bit depth):

```
nm  = normalize(median)
nmad = normalize(MAD)
if nm > 0.5 {                      // inverted / overexposed image
    shadows    = 0.0
    highlights = nm − shadowsClipping * nmad * 1.4826      // shadowsClipping = −2.8 ⇒ nm + 2.8·1.4826·nmad
    midtones   = MTF(targetFactor, 1.0 − (highlights − nm))
} else {                           // normal astro image
    shadows    = nm + shadowsClipping * nmad * 1.4826      // = nm − 2.8·1.4826·nmad
    midtones   = MTF(targetFactor, nm − shadows)
    highlights = 1.0
}
// lookup table over all 2^16 input codes:
map[i] = denormalize( MTF(midtones, 1 − highlights + normalize(i) − shadows) )
```

Bayered images with `DebayerImage=true` (default) are debayered (bilinear, `BayerFilter16bpp`) to Rgb48 and
stretched (default `UnlinkedStretch=true`: per-channel stretch maps).

**Median and MAD are now specified exactly** (`ImageStatistics.Create`, `ImageStatistics.cs:46–170`), computed
over the **raw u16 `FlatArray`** (full resolution, unstretched, one channel), via a full 65536-bin histogram
`counts[v]` (`counts` has length `65536`; every pixel increments `counts[pixel]`). Let `N = array.len()` and
`half = N as f64 / 2.0` (a **float**; the comparisons below are integer-cumsum vs this float):

```rust
// ---- median (histogram cumulative walk) ----
let (mut occ, mut m1, mut m2) = (0i64, 0i32, 0i32);
for v in 0u32..=65534 {                     // NOTE: loop stops at 65534 (i < ushort.MaxValue)
    occ += counts[v as usize] as i64;
    if (occ as f64) > half {                // strictly greater
        m1 = v as i32; m2 = v as i32; break;
    } else if (occ as f64) == half {        // exact tie ⇒ average with next nonempty bin
        m1 = v as i32;
        m2 = ((v+1)..=65535).find(|&j| counts[j as usize] > 0).map(|j| j as i32).unwrap_or(m1);
        break;
    }
}
let median = (m1 + m2) as f64 / 2.0;

// ---- MAD (walk outward from the median bins until cumulative > half) ----
let mut mad = 0.0f64;
let (mut occ2, mut down, mut up) = (0i64, m1, m2);
loop {
    if down >= 0 && down != up { occ2 += counts[down as usize] as i64 + counts[up as usize] as i64; }
    else                       { occ2 += counts[up as usize] as i64; }
    if (occ2 as f64) > half { mad = (up as f64 - median).abs(); break; }
    up += 1; down -= 1;
    if up > 65535 { break; }                // mad stays 0.0 if we run off the top
}
```

Edge cases: if `N` is odd, `occ > half` triggers first and `median = m1 = m2` (an integer). If `N` is even and
a bin's cumulative count exactly equals `half`, the median is the average of that bin and the next nonempty
bin (so it can be a `.5` value). `mad` is an **integer distance in ADU** (or `.5` when `median` is a half),
NOT scaled by 1.4826 here — the 1.4826 σ-scaling is applied later in the stretch (§2.1 formulas). Both feed
`normalize(median)` and `normalize(MAD)` using `bit_depth = imageProperties.BitDepth`. `variance/StDev`
(`(squareSum − N·mean²)/N`, population) are also computed but are **not** used by the stretch or the detector.

### 2.2 Grayscale conversion of color (Rgb48) detection input

`StarDetection.GetInitialState` (StarDetection.cs:109–116): if the rendered pixel format is Rgb48, convert to
Gray16 with BT.709 weights via Accord `Grayscale(0.2125, 0.7154, 0.0721)`:

```
gray = 0.2125*R + 0.7154*G + 0.0721*B      // per 16-bit channel
```

### 2.3 Photometry array selection

`GetInitialState` (StarDetection.cs:56–64): photometry uses `imageData.Data.FlatArray` (`u16`, row-major,
index = `x + width*y`, full resolution, **unstretched**). If the image `IsBayered` and was debayered and a
luminance channel exists, use the debayered luminance instead:

```
Lum[i] = floor((R[i] + G[i] + B[i]) / 3)   // BayerFilter16bpp.cs:151, u16
```

---

## 3. Stage 0 — initial state: resize factor, star size bounds

Source: `StarDetection.cs:35, 51–121`.

Constant: `MAX_WIDTH = 1552` (px). Everything below only differs from 1.0 when `image_width > 1552`.

```rust
fn resize_factor(width: u32, sensitivity: Sensitivity, pixel_size_um: f64, focal_length_mm: f64) -> f64 {
    if width <= 1552 { return 1.0; }
    match sensitivity {
        Sensitivity::Highest => f64::max(2.0/3.0, 1552.0 / width as f64),
        Sensitivity::High => {
            // image scale in arcsec/pixel; NaN if pixel size or focal length unknown
            let image_scale = (pixel_size_um / focal_length_mm) * 206.264_806_247_096_36; // (180/π)*3600/1000
            if image_scale.is_nan() {
                f64::max(0.5, 1552.0 / width as f64)          // + log warning "image scale unknown"
            } else {
                // NOTE: faithful reproduction of NINA's sequential ifs, including the quirk that
                // the (1.5, 2.5] branch is immediately overridden by the `> 1.5` branch:
                let mut r = 1.0;
                if image_scale < 0.5                        { r = 0.25;      }
                if image_scale >= 0.5 && image_scale <= 1.5 { r = 1.0/3.0;   }
                if image_scale >= 1.5 && image_scale <= 2.5 { r = 0.5;       }
                if image_scale > 1.5 { r = f64::max(2.0/3.0, 1552.0 / width as f64); }
                r
                // effective mapping: <0.5 → 1/4 ; [0.5,1.5) → 1/3 ; ==1.5 → 1/2 ;
                //                    >1.5  → max(2/3, 1552/width)
            }
        }
        Sensitivity::Normal => 1552.0 / width as f64,
    }
}
```

Derived values (StarDetection.cs:100–108):

```
inverse_resize_factor = 1.0 / resize_factor
min_star_size = max(2, floor(5.0 * resize_factor))     // px in the *resized* image; ≥2 rejects hot pixels
max_star_size = ceil(150.0 * resize_factor)            // px in the resized image
```

---

## 4. Stage 1 — 16-bit → 8-bit conversion

Source: `ImageUtility.Convert16BppTo8Bpp` → Accord `Image.Convert16bppTo8bpp` (AccordImage.cs:502–538).

Exact: per pixel, **take the high byte**:

```
u8 = (u16_value >> 8) as u8
```

Applied to the stretched Gray16 detection image. No dithering, no rounding.

---

## 5. Stage 2 — optional noise reduction (full-size, before resize)

Source: `StarDetection.cs:216–218, 444–470`. **Only applied if `bitmap_width > 1552`** — for small sensors the
`NoiseReduction` setting is effectively ignored here (but still changes which Canny variant runs, see §7 —
a real NINA quirk: small image + Gaussian NR setting ⇒ no blur at all before Canny).

| `NoiseReductionEnum` | operation |
|---|---|
| `None` | nothing |
| `Median` | 3×3 median filter |
| `Normal` (default label in code path: `default:`) | FastGaussianBlur, radius param 1 |
| `High` | FastGaussianBlur, radius param 2 |
| `Highest` | FastGaussianBlur, radius param 3 |

### 5.1 FastGaussianBlur (NINA's own, FastGaussianBlur.cs)

Gaussian approximation by three successive box blurs (Ivan Kutskir / "Fastest Gaussian blur" method), on the 8-bit
grayscale buffer, `u8` arithmetic with `floor` division.

```rust
fn boxes_for_gauss(sigma: i32, n: usize /* = 3 */) -> Vec<i32> {
    let w_ideal = ((12.0 * (sigma*sigma) as f64 / n as f64) + 1.0).sqrt();
    let mut wl = w_ideal.floor() as i32;
    if wl % 2 == 0 { wl -= 1; }                         // force odd
    let wu = wl + 2;
    let m_ideal = (12.0*(sigma*sigma) as f64 − (n as f64)*(wl*wl) as f64 − 4.0*(n as f64)*wl as f64 − 3.0*n as f64)
                  / (−4.0*wl as f64 − 4.0);
    let m = m_ideal.round() as i32;
    (0..n as i32).map(|i| if i < m { wl } else { wu }).collect()
}

fn gauss_blur(src: &mut [u8], dst: &mut [u8], w: usize, h: usize, radial: i32) {
    let bxs = boxes_for_gauss(radial, 3);
    box_blur(src, dst, w, h, (bxs[0]-1)/2);   // each box_blur: copy src→dst, then
    box_blur(dst, src, w, h, (bxs[1]-1)/2);   //   horizontal running-sum pass, then vertical pass
    box_blur(src, dst, w, h, (bxs[2]-1)/2);   // result in dst
}
```

Box blur passes are the standard running-sum sliding window of full width `2r+1`, edges handled by clamping to
the first/last pixel of the row/column (`fv`, `lv` extension); each output pixel is
`floor(window_sum / (2r+1))` as `u8`.

### 5.2 Median (Accord Median.cs, size = 3)

For each pixel, collect the 3×3 neighborhood **clipped to the image bounds** (corner pixels use 4 samples, edge
pixels 6), sort, output element at index `count >> 1`.

---

## 6. Stage 3 — resize (bicubic)

Source: `DetectionUtility.ResizeForDetection` (DetectionUtility.cs:52–61) → Accord `ResizeBicubic`
(ResizeBicubic.cs:102–172) with `Interpolation.BiCubicKernel` (Interpolation.cs:31–50).

Only if `width > 1552`. New size: `floor(W * r) × floor(H * r)`.

Mapping and kernel (Catmull-Rom-family cubic with a = −0.5, but note the non-standard piecewise form Accord uses):

```rust
fn bicubic_kernel(mut x: f64) -> f64 {
    if x < 0.0 { x = -x; }
    if x <= 1.0      { (1.5 * x - 2.5) * x * x + 1.0 }
    else if x < 2.0  { ((-0.5 * x + 2.5) * x - 4.0) * x + 2.0 }
    else             { 0.0 }
}

// for each destination pixel (x, y):
x_factor = src_w as f64 / new_w as f64;   y_factor = src_h as f64 / new_h as f64;
oy = y as f64 * y_factor - 0.5;  oy1 = oy as i64 /* trunc */;  dy = oy - oy1 as f64;
ox = x as f64 * x_factor - 0.5;  ox1 = ox as i64;              dx = ox - ox1 as f64;
g = Σ_{n=-1..2} Σ_{m=-1..2}  bicubic_kernel(dy - n) * bicubic_kernel(m - dx)
      * src[clamp(oy1+n, 0, src_h-1)][clamp(ox1+m, 0, src_w-1)];
dst = clamp(g, 0.0, 255.0) as u8;
```

---

## 7. Stage 4 — edge detection (Canny), auto-threshold (SIS), dilation

Source: `StarDetection.PrepareForStructureDetection` (StarDetection.cs:419–442).

Order (all in-place on the 8-bit resized image):
1. Canny edge detector, **low threshold = 10, high threshold = 80**.
   - If `NoiseReduction ∈ {None, Median}` → full Canny including its internal Gaussian pre-blur
     (**σ = 1.4, 5×5 kernel** — Accord defaults).
   - Else (Gaussian NR was requested) → `NoBlurCannyEdgeDetector` — identical Canny minus the pre-blur.
     (Remember §5: if the image was ≤1552 px wide, the Gaussian NR never actually ran, so this path can run
     Canny on completely unblurred data. Reproduce faithfully.)
2. SIS threshold (auto, image-dependent) → binary image {0, 255}.
3. Binary 3×3 dilation.

### 7.1 Canny (Accord CannyEdgeDetector.cs:175–364; NINA's NoBlurCannyEdgeDetector.cs:97–265 is the same
minus blur)

Processing region excludes a 1-px border; after processing, the 1-px frame is painted black.

**Step 0 (blur variant only)** — integer-kernel Gaussian convolution. Kernel construction and application are
now **fully verified against the vendored Accord source** (`GaussianBlur.cs:159–187`, `Convolution.cs:342–428`)
and numerically reproduced — the 5×5 kernel / divisor 71 below are exact, not "to be verified":

*Kernel construction* (`GaussianBlur.CreateFilter`, `Normal.Kernel2D(sigma*sigma, size)`):
- The float 2-D Gaussian is `G(x,y) = exp(−(x²+y²) / (2·σ²))` over integer offsets `x,y ∈ {−2..2}`, with
  **σ² = sigma·sigma = 1.4² = 1.96** (note: `Kernel2D`'s first argument is the *variance*, not σ; NINA passes
  `sigma*sigma`). Accord's `Normal.Kernel2D` normalizes by the Gaussian's own scale factor, but that constant
  cancels in the next step, so only the relative tap ratios matter.
- Integer kernel: `intKernel[i,j] = (int)( G[i,j] / G_min )` where `G_min` is the corner tap `G[0,0]`
  (the smallest). **`(int)` truncates toward zero** (floor for these non-negative ratios) — NOT rounding.
- `divisor = Σ intKernel[i,j]`.

Computed relative taps (`G/G_min`, corner distance² = 8, so `G_min = exp(−8/3.92)`): center 7.69→7,
edge-mid 5.96→5, etc. Yields exactly:

```
1 2 2 2 1
2 4 5 4 2
2 5 7 5 2      divisor = Σ = 71   (8+17+21+17+8)
2 4 5 4 2
1 2 2 2 1
```

*Kernel application* (`Convolution.Process8bppImage`): accumulate `g = Σ(intKernel·pixel)` and
`div = Σ(intKernel)` in `long`. For an **interior** pixel `div == divisor == 71`; then `g /= div` is C#
**integer division (truncation toward zero; = floor for non-negative g)**, then `g += threshold` (threshold
is 0 for GaussianBlur), then clamp to `[0,255]` cast to u8. There is no rounding — Rust: `((g / 71) as i64).clamp(0,255) as u8`.

*Edge pixels* — `DynamicDivisorForEdges` defaults **true** (`Convolution.cs:64`): for pixels whose kernel
window overhangs the image, taps outside the image are simply **not summed** and `div` becomes the partial sum
of only the in-bounds taps (so `g / div` still normalizes correctly). Accord's convolution does NOT clamp/mirror
edge samples — it shrinks both numerator and divisor. This only affects the outermost 2-px ring; in the Canny
pipeline the outermost 1-px frame is later force-painted black and the Sobel step skips the 1-px border, so the
only observable effect of the dynamic divisor is on the second ring of pixels. Reproduce it faithfully:
for each in-window tap with source coords inside `[0,w)×[0,h)`, add `k·pixel` to `g` and `k` to `div`; then
`out = (g / div).clamp(0,255)` (guard `div==0 → out=0`, though that cannot occur for a positive kernel).

**Step 1 — gradients (Sobel, integer):** for each interior pixel

```
gx = p(x+1,y−1) + p(x+1,y+1) − p(x−1,y−1) − p(x−1,y+1) + 2*(p(x+1,y) − p(x−1,y))
gy = p(x−1,y−1) + p(x+1,y−1) − p(x−1,y+1) − p(x+1,y+1) + 2*(p(x,y−1) − p(x,y+1))
grad(x,y) = sqrt(gx² + gy²)  as f32; track max_gradient
```

Orientation quantized to {0, 45, 90, 135}°:

```
if gx == 0 { orient = if gy == 0 {0} else {90} }
else {
    div = gy as f64 / gx as f64;
    ang = if div < 0 { 180 − atan(−div)·180/π } else { atan(div)·180/π };
    orient = if ang < 22.5 {0} else if ang < 67.5 {45} else if ang < 112.5 {90}
             else if ang < 157.5 {135} else {0};
}
```

**Step 2 — non-maximum suppression + normalization:** compare `grad(x,y)` with its two neighbors along the
quantized orientation (0°: left/right; 45°: (x−1,y+1)/(x+1,y−1); 90°: (x,y+1)/(x,y−1);
135°: (x+1,y+1)/(x−1,y−1)). If strictly smaller than either → output 0, else output
`(grad / max_gradient * 255) as u8`. **Note the image-dependent normalization by the maximum gradient** —
thresholds 10/80 are applied to *normalized* magnitudes.

**Step 3 — hysteresis (single pass, simplified — no flood fill):** in-place on the output of step 2:

```
if p < high {                       // high = 80
    if p < low { p = 0 }            // low = 10
    else if all 8 neighbors < high { p = 0 }   // kept only if touching a strong edge pixel
}
```

### 7.2 SIS threshold (Accord SISThreshold.cs:158–212 + Threshold filter)

Weighted-gradient mean threshold over interior pixels (border rows/cols excluded):

```
ex = |I(x+1, y) − I(x−1, y)|
ey = |I(x, y+1) − I(x, y−1)|
w  = max(ex, ey)
threshold = (Σ w·I(x,y)) / (Σ w)        // double division; 0 if Σw == 0; cast to byte ⇒ truncated to u8
binarize: pixel = if pixel >= threshold { 255 } else { 0 }
```

**Verified** against the vendored source (`SISThreshold.cs:158–212` + `Threshold.cs:104–149`): the running
sums are `double` (`weightTotal`, `total`); the returned threshold is `(byte)(total / weightTotal)` — a
double→byte cast, which **truncates toward zero** (the `weightTotal==0` guard returns `(byte)0`). Border rows
and columns are excluded (`ex`/`ey` need both neighbors). The binarization is the standard AForge `Threshold`
filter with `>= threshold → 255` (confirmed for both the 8-bpp and 16-bpp branches; NINA runs the 8-bpp path):
pixels **exactly equal to** the threshold become 255 (white / object). No off-by-one ambiguity remains.

### 7.3 Binary dilation 3×3 (Accord BinaryDilation3x3.cs:99–184)

`dst(x,y) = OR of src over the 3×3 neighborhood`, with the neighborhood clipped at the image border (corners OR
4 values, edges 6). Requires image ≥ 3×3. Since input is {0,255}, OR == max.

---

## 8. Stage 5 — blob detection (connected component labeling)

Source: Accord `BlobCounter.BuildObjectsMap` (BlobCounter.cs:167–636) + `BlobCounterBase`
(GetObjectsInformation :592, CollectObjectsInfo :1168, GetBlobsEdgePoints :1062). NINA calls
`ProcessImage(bitmap)` then `GetObjectsInformation()` (StarDetection.cs:407–417, 270).

- Background: pixels with value `<= background_threshold` (**default 0**); object pixels are `> 0`. On the
  binary dilated image, object pixels are the 255s.
- Labeling: raster-scan two-pass CCL with a label-equivalence map, checking neighbors
  (left, above-left, above, above-right) — i.e. **8-connectivity**. After the scan, equivalences are collapsed
  and labels renumbered 1..objects_count in first-appearance order. A union-find CCL with 8-connectivity
  produces identical blobs; only the label order must remain raster-scan first-appearance to keep downstream
  ordering identical (NINA does not sort blobs; default `ObjectsOrder = None`).
- Per blob (`CollectObjectsInfo`): bounding rectangle `x1..x2, y1..y2` inclusive →
  `Rectangle { x: x1, y: y1, w: x2−x1+1, h: y2−y1+1 }` (in resized-image coordinates). (Area, center of
  gravity, mean/stddev are also computed by Accord but NINA's detector only uses `Rectangle` and `ID`.)

### 8.1 Edge points of a blob (`GetBlobsEdgePoints`)

Used as the point set for the circle check. For each row `y` of the blob's bounding box: the leftmost pixel with
this blob's label and the rightmost (skipped if identical to leftmost). Then for each column `x`: the topmost
and bottommost labeled pixels, each added **only if** its x is neither that row's recorded leftmost nor
rightmost x. Result: unordered perimeter point list without duplicates.

---

## 9. Stage 6 — star candidate filtering (size, ROI, shape)

Source: `StarDetection.IdentifyStars` (StarDetection.cs:267–398), `CalculateEccentricity` (:400–405),
`DetectionUtility.InROI` (:63–107).

Per blob, in order:

1. **Size filter** (resized px): reject unless
   `min_star_size <= rect.w <= max_star_size && min_star_size <= rect.h <= max_star_size`.

2. **ROI filter** (only when `UseROI == true`): evaluated on the resized image with the blob's resized
   rectangle; see §13.

3. **Rect upscale** to full resolution (mixed floor/ceil — reproduce exactly):

   ```
   rect.x = floor(blob.rect.x * inv_r);  rect.y = floor(blob.rect.y * inv_r)
   rect.w = ceil (blob.rect.w * inv_r);  rect.h = ceil (blob.rect.h * inv_r)
   ```

4. **Background box** ("large rect", 3× the star box, clipped to image):

   ```
   lx = max(rect.x − rect.w, 0);          ly = max(rect.y − rect.h, 0)
   lw = rect.w * 3; if lx + lw > image_w { lw = image_w − lx }
   lh = rect.h * 3; if ly + lh > image_h { lh = image_h − ly }
   ```

5. **Shape check** — Accord `SimpleShapeChecker.IsCircle(edge_points)` (SimpleShapeChecker.cs:243–276),
   defaults `min_acceptable_distortion = 0.5` (px), `relative_distortion_limit = 0.03`:

   ```
   if points.len() < 8 { center = (0,0); radius = 0; return false }
   (min_xy, max_xy) = bounding_rect(points);   cloud = max_xy − min_xy;
   center = min_xy + cloud/2  (as f32 point);  radius = (cloud.x + cloud.y) as f32 / 4.0;
   mean_dev = mean over points of | dist(center, p) − radius |;
   max_dist = max(0.5, (cloud.x + cloud.y) as f32 / 2.0 * 0.03);
   is_circle = mean_dev <= max_dist
   ```

   - **Circle:** star position = `center * inv_r` (f32), star radius = `radius * inv_r`
     (full-res px), bounding box = upscaled rect.
   - **Not a circle (elongated):** compute eccentricity from the *full-res* rect:
     `x = max(w,h); y = min(w,h); e = sqrt(x² − y²) / x`. **Reject if `e > 0.8`.** Otherwise accept with
     position = `center * inv_r` (center is still the out-param from `IsCircle`, i.e. the resized bounding-box
     center — or `(0,0)` when the blob had <8 edge points; such stars then fail photometry with NaN and get
     dropped, see §10), radius = `max(rect.w, rect.h)/2` (full-res px, no `inv_r` — rect is already full-res).
     **`rect.w`/`rect.h` are `i32` and C# computes `Math.Max(int,int) / 2` as integer division (truncation)
     before assigning to the `double radius` field** — so an elongated star's radius is an integer ADU-pixel
     value (e.g. width 15 → radius 7, not 7.5). Reproduce as `((rect.w.max(rect.h)) / 2) as f64` (integer `/`),
     NOT `rect.w.max(rect.h) as f64 / 2.0`. (The circle branch, by contrast, keeps the fractional
     `radius * inv_r` in f32.)

6. **No saturation filter exists.** NINA's detector never rejects saturated stars; `max_pixel_value` is recorded
   per star but only exported as annotation data.

---

## 10. Stage 7 — photometry: local-max test and bright-pixel test (raw u16 data)

Source: `StarDetection.cs:317–356`. Iterate every full-res pixel of the large rect
(`x` in `[lx, lx+lw)`, `y` in `[ly, ly+lh)`), reading `v = flat[x + width*y]` from the raw array (§2.3):

```
if pixel is inside the small rect (rect):
    if (x−px)² + (y−py)² <= star_radius²:              // "inner sanctum" circle around star position
        star_pixel_sum += v; star_pixel_count += 1
        inner_values.push(v)
    max_pixel_value = max(max_pixel_value, v)          // NOTE: only updated for inner-circle pixels
    pixel_data.push({x, y, v})                          // ALL small-rect pixels feed the HFR later
else:                                                   // rectangular annulus = large rect minus small rect
    bg_sum  += v
    bg_sum2 += v*v
```

Then:

```
mean_brightness = star_pixel_sum / star_pixel_count            // f64; NaN if count==0 → star rejected below
bg_count  = lw*lh − rect.w*rect.h                              // (small rect is always inside large rect)
bg_mean   = bg_sum / bg_count
bg_stdev  = sqrt( (bg_sum2 − bg_count*bg_mean²) / bg_count )   // population σ
min_bright_pixels = ceil( max(full_width, full_height) / 1000 )  // e.g. 4144px → 5
```

**Acceptance test** (both must hold; comparisons on raw u16 values):

```
mean_brightness >= bg_mean + min(0.1 * bg_mean, bg_stdev)                  // local maximum test
AND  count(inner_values where v > bg_mean + 1.5 * bg_stdev) > min_bright_pixels   // strictly greater
```

If accepted: accumulate `sum_radius += radius; sum_squares += radius²`, compute HFR (§11), add to star list.
If `star_pixel_count == 0` (e.g. degenerate center from §9.5), `mean_brightness` is NaN and the comparison is
false ⇒ star dropped (in Rust: guard explicitly; NaN `>=` is false, matching C#).

---

## 11. Stage 8 — HFR computation (exact flux-radius formula)

Source: `Star.CalculateHfr` (StarDetection.cs:143–175). Operates on `pixel_data` = **every** pixel of the
full-res small rect (not only the inner circle), with the star's `position` (f32 → f64) and `radius`
(full-res px):

```rust
fn calculate_hfr(star: &mut Star) {
    let mut hfr = 0.0f64;
    if !star.pixel_data.is_empty() {
        let outer_radius = star.radius * 1.2;
        let (mut sum, mut sum_dist, mut all_sum) = (0.0f64, 0.0f64, 0.0f64);
        for px in &mut star.pixel_data {
            // background-subtract, round to nearest, clamp at 0 (stored back as u16)
            let mut v = (px.value as f64 - star.surrounding_mean).round();
            if v < 0.0 { v = 0.0; }
            px.value = v.round() as u16;                     // double-round, matches C#
            all_sum += px.value as f64;
            let dx = px.x as f64 - star.position.x as f64;
            let dy = px.y as f64 - star.position.y as f64;
            if dx*dx + dy*dy <= outer_radius * outer_radius {  // inside 1.2·radius circle
                sum      += px.value as f64;
                sum_dist += px.value as f64 * (dx*dx + dy*dy).sqrt();
            }
        }
        hfr = if sum > 0.0 { sum_dist / sum } else { 2f64.sqrt() * outer_radius };  // fallback: √2 · 1.2·r
        star.average = all_sum / star.pixel_data.len() as f64;   // mean bg-subtracted flux over small rect
    }
    star.hfr = hfr;   // 0.0 if pixel_data was empty
    star.pixel_data.clear();
}
```

Notes:
- **Rounding mode:** C# `Math.Round(x)` (lines 153, 157) uses **banker's rounding**
  (`MidpointRounding.ToEven`): exact halves round to the nearest even integer (`2.5→2`, `3.5→4`). Rust's
  `f64::round()` rounds half **away from zero** (`2.5→3`). `data.value − SurroundingMean` is a `double` and only
  rarely lands exactly on `x.5`, but for bit-exact parity use a to-even rounding helper here, e.g.
  `(x - (x - x.round()).abs().eq(&0.5).then(|| if (x.round() as i64) % 2 != 0 { x.signum() } else {0.0}).unwrap_or(0.0))`
  — or simpler, `round_ties_even(x)` (Rust 1.77+: `f64::round_ties_even`). The clamp-at-0 happens *before* the
  second round, and the second round is a no-op on an already-integer value.
- This is a *flux-weighted mean radial distance* HFR (not a "half of flux inside radius" solver):
  `HFR = Σᵢ vᵢ·dᵢ / Σᵢ vᵢ` over background-subtracted pixels within `1.2·radius` of the centroid.
- The "centroid" is **not** recomputed from flux — it is the geometric circle/bbox center from §9.5.
- Units: full-resolution pixels.

---

## 12. Stage 9/10 — population radius filter, AF star selection, aggregation

Source: `StarDetection.cs:359–397, 237–249`.

**Radius σ-clip** (population statistics over accepted stars' radii):

```
avg = sum_radius / n
sd  = sqrt( (sum_squares − n·avg²) / n )         // population σ (÷ n, not n−1)
Normal | High : keep stars with avg − 1.5·sd <= r <= avg + 1.5·sd
Highest      : keep stars with avg − 1.5·sd <= r <= avg + 2·sd    // permissive on the large end
```

`detected_stars = starlist.len()` **after** this filter, **before** AF selection.

**AF star selection** (only when `NumberOfAFStars > 0`):

- First AF exposure (`MatchStarPositions` empty/None):
  - if `n <= NumberOfAFStars`: `BrightestStarPositions = all positions`; return the full list.
  - else: sort descending by score `0.3·radius + 0.7·mean_brightness`, take the first `NumberOfAFStars`;
    `BrightestStarPositions =` their positions; return that subset.
- Subsequent exposures: for each stored position, select the star minimizing Euclidean distance to it
  (`argmin over starlist`; the same star may be selected for multiple positions — duplicates are possible);
  return that list.

**Aggregation** (`Detect`, over the *returned* star list — i.e., the AF subset when AF selection ran):

```
if star_list.len() > 0 {
    mean = Σ hfr / n
    sd   = if n > 1 { sqrt( Σ (hfrᵢ − mean)² / (n − 1) ) } else { 0.0 }   // sample σ
    result.average_hfr = mean; result.hfr_std_dev = sd; result.detected_stars = detected_stars;
}
// else: result keeps defaults AverageHFR=0.0, HFRStdDev=0.0, DetectedStars=0, StarList=[]
```

---

## 13. ROI handling

### 13.1 The geometric predicate (`DetectionUtility.InROI`, DetectionUtility.cs:63–107)

Evaluated per blob on the **resized** image (`image_size` = resized W×H, `blob` = resized bounding rect).
Supports a full-frame center crop and a "donut" (exclude a central hole):

```rust
fn in_roi(image: Size, blob: Rect, outer_crop: f64, inner_crop: f64) -> bool {
    // outer boundary rectangle
    let outside = if inner_crop >= 1.0 || outer_crop <= 0.0 {
        Rect { x: 0, y: 0, w: image.w, h: image.h }         // no outer restriction
    } else {
        let ratio = if outer_crop >= 1.0 { inner_crop } else { outer_crop };
        let start = (1.0 - ratio) / 2.0;
        Rect { x: floor(image.w as f64 * start), y: floor(image.h as f64 * start),
               w: (image.w as f64 * ratio) as i32, h: (image.h as f64 * ratio) as i32 } // trunc
    };
    // inner exclusion rectangle (donut hole)
    let inside = if outer_crop >= 1.0 {
        Rect { x: image.w/2, y: image.h/2, w: 0, h: 0 }     // zero-size ⇒ nothing excluded
    } else {
        let start = (1.0 - inner_crop) / 2.0;
        Rect { x: floor(image.w as f64 * start), y: floor(image.h as f64 * start),
               w: (image.w as f64 * inner_crop) as i32, h: (image.h as f64 * inner_crop) as i32 }
    };
    fully_inside(blob, outside) && !fully_inside(blob, inside)
}

fn fully_inside(a: Rect, b: Rect) -> bool {
    if a.x < b.x || a.y < b.y || a.x >= b.x + b.w || a.y >= b.y + b.h { return false; }
    a.x + a.w <= b.x + b.w && a.y + a.h <= b.y + b.h
}
```

Semantics:
- `inner < 1`, `outer >= 1`: keep only blobs fully inside the central `inner` crop.
- `inner < 1`, `outer < 1`: keep blobs fully inside the `outer` crop but **not** fully inside the `inner`
  crop (donut).
- `outer <= 0`: sentinel — no outer restriction, pure central exclusion (used with camera sub-sampling).

### 13.2 How callers set the parameters

`RenderedImage.DetectStars` (RenderedImage.cs:87–102): `UseROI=true` and `InnerCropRatio` /
`OuterCropRatio` copied from `FocuserSettings.AutoFocusInnerCropRatio` / `...OuterCropRatio` whenever either
is < 1.

`AutoFocusVM.EvaluateExposure` (AutoFocusVM.cs:408–429): same, except when the camera already sub-sampled the
sensor to the outer crop: then `OuterCropRatio = 0.0` (sentinel) and
`InnerCropRatio = inner / outer` (rescaled into the subframe).

---

## 14. Detected-star annotation data

Per star exported as `DetectedStar` (IStarDetection.cs:47–54):

| field | value | units |
|---|---|---|
| `HFR` | §11 | full-res px |
| `Position` | (f32, f32) star center | full-res px |
| `AverageBrightness` | `Average` = mean background-subtracted flux over small rect | raw ADU (u16 scale) |
| `MaxBrightness` | max raw pixel value inside the inner circle | raw ADU |
| `Background` | `bg_mean` of the rectangular annulus | raw ADU |
| `BoundingBox` | full-res upscaled blob rect | px |

`StarDetectionResult`: `AverageHFR`, `DetectedStars`, `HFRStdDev`, `StarList`, `BrightestStarPositions`
(AF), `Params`. `StarDetectionAnalysis` defaults when nothing ran: `HFR = NaN`, `HFRStDev = NaN`,
`DetectedStars = -1`, empty list.

Annotator (`StarAnnotator.cs`, UI-only): draws an ellipse on each star's bounding box and the HFR value
(format `"##.##"`) offset (−10−15, −10+25) px from the position; if more than `maxStars` (default 200, −1 =
unlimited via `AnnotateUnlimitedStars`), keeps the top 200 by `AverageBrightness`. Draws the inner (and outer,
if < 1) ROI rectangles when `UseROI`.

---

## 15. Configuration knobs

| knob | location | default | valid range | effect |
|---|---|---|---|---|
| `Sensitivity` | `ImageSettings.StarSensitivity` | **High** | Normal / High / Highest | resize factor (§3) + radius clip bounds (§12) |
| `NoiseReduction` | `ImageSettings.NoiseReduction` | **None** | None / Median / Normal / High / Highest | §5 + Canny variant (§7) |
| `UseROI` | param | false | bool | §13 |
| `InnerCropRatio` | `FocuserSettings.AutoFocusInnerCropRatio` | 1.0 | (0, 1] | §13 |
| `OuterCropRatio` | `FocuserSettings.AutoFocusOuterCropRatio` | 1.0 | [0, 1] (0 = sentinel) | §13 |
| `NumberOfAFStars` | `FocuserSettings.AutoFocusUseBrightestStars` | 0 (off) | ≥ 0 | §12 AF selection |
| `MatchStarPositions` | param | empty | list of points | §12 AF re-match |
| `IsAutoFocus` | param | false | bool | **unused** by this detector (consumed by plugin detectors) |
| `AutoStretchFactor` | `ImageSettings` | 0.2 | (0, 1) | stretch shaping detection input (§2.1) |
| `BlackClipping` | `ImageSettings` | −2.8 | ≤ 0 | shadow clip in stretch (§2.1) |
| `DebayerImage` / `DebayeredHFR` | `ImageSettings` | true / true | bool | use debayered Lum for photometry (§2.3) |
| `UnlinkedStretch` | `ImageSettings` | true | bool | per-channel stretch for OSC |
| `AnnotateUnlimitedStars` | `ImageSettings` | false | bool | annotation cap 200 vs unlimited |
| `MAX_WIDTH` | hard-coded | 1552 | const | resize/NR/downscale trigger |
| Canny low/high | hard-coded | 10 / 80 | const (u8) | §7.1 |
| Canny blur | hard-coded | σ=1.4, 5×5 | const | §7.1 |
| circle distortion | hard-coded (Accord defaults) | 0.5 px min, 3% relative | const | §9.5 |
| eccentricity limit | hard-coded | 0.8 | const | §9.5 |
| HFR aperture | hard-coded | 1.2 × radius | const | §11 |
| local-max margin | hard-coded | min(0.1·bg, σ_bg) | const | §10 |
| bright-pixel bar | hard-coded | bg + 1.5·σ_bg, count > ceil(max(W,H)/1000) | const | §10 |
| radius clip | hard-coded | ±1.5σ (+2σ high side for Highest) | const | §12 |
| min/max star size | derived | max(2, ⌊5r⌋) / ⌈150r⌉ | const | §3 |

---

## 16. Edge cases and failure paths (as the source handles them)

1. **Cancellation**: checked between every stage and inside the blob loop; `OperationCanceledException` is
   swallowed in `Detect` — a *partially empty* result object is returned (defaults: 0 stars, HFR 0).
2. **Image ≤ 1552 px wide**: no noise reduction, no resize; `resize_factor = 1.0`;
   `min_star_size = floor(5·1.0) = 5` (the ≥2 clamp is inactive), `max_star_size = 150`.
3. **Image scale metadata missing (High sensitivity)**: `arcsec/px = NaN` → fallback
   `max(0.5, 1552/W)` + warning log.
4. **`imageScale == 1.5` exactly** hits the dead `1/2` branch (§3 quirk) — reproduce for parity.
5. **Blob with < 8 edge points**: `IsCircle` returns false with center (0,0) → elongated branch → position
   (0,0) → inner circle empty → `mean_brightness = NaN` → acceptance test false → dropped.
6. **`sum <= 0` in HFR** (all flux ≤ background): `HFR = √2 · 1.2 · radius` (finite sentinel, not an error).
7. **Empty `pixel_data`**: HFR = 0 (cannot occur in practice; rect always ≥1 px).
8. **No blobs / no stars survive**: returns empty list; result keeps `AverageHFR = 0`, `HFRStdDev = 0`,
   `DetectedStars = 0`; no error.
9. **Exactly one star**: `HFRStdDev = 0` (guard against N−1 = 0).
10. **AF stdev NaN**: `AutoFocusVM` maps NaN `HFRStdDev` to 0 before use.
11. **Star at image border**: large rect clipped to the image; small rect can touch the border (ceil-upscale)
    but iteration is bounded by the clipped large rect, so no out-of-bounds reads. Background annulus becomes
    asymmetric — accepted as-is.
12. **Saturated stars**: not rejected (no saturation logic).
13. **Hot pixels**: suppressed by `min_star_size >= 2` (resized px) and by the ≥8-edge-point circle rule plus
    the bright-pixel count test.
14. **1-px-wide images**: Accord CCL throws; NINA never hits this (camera frames). Guard in Rust anyway.
15. **Blob count overflow**: Accord's merge map is sized `(W/2+1)*(H/2+1)+1` — the theoretical max labels in a
    checkerboard; a Rust union-find has no such cap.
16. **Rgb48 input**: converted to gray (BT.709) before analysis; photometry uses debayered Lum when available,
    else the raw CFA array (mono semantics on mosaiced data — matches NINA when `DebayerImage=false`).

---

## 17. Source map (MPL-2.0 provenance; line numbers from the shallow clones read on 2026-07-03)

| algorithm / item | file | lines |
|---|---|---|
| Detector state, resize factor, star size bounds, Rgb48→gray | `references/nina/NINA.Image/ImageAnalysis/StarDetection.cs` | 35, 41–121 |
| `Star` model, `CalculateHfr`, `InsideCircle`, `ToDetectedStar` | same | 123–201 |
| `Detect` orchestration + mean/σ aggregation | same | 203–257 |
| `IdentifyStars` (size/ROI/shape filters, photometry, radius clip, AF selection) | same | 259–398 |
| Eccentricity | same | 400–405 |
| Blob counting call | same | 407–417 |
| Structure prep (Canny/SIS/dilation choice) | same | 419–442 |
| Noise reduction dispatch | same | 444–470 |
| Analysis update | same | 472–481 |
| Params/result/DetectedStar shapes | `references/nina/NINA.Image/ImageAnalysis/IStarDetection.cs` | 36–63 |
| ROI predicate, crop rects, `FullyInsideRect`, resize call | `references/nina/NINA.Image/ImageAnalysis/DetectionUtility.cs` | 24–107 |
| No-blur Canny (verbatim Canny math incl. Sobel/NMS/hysteresis) | `references/nina/NINA.Image/ImageAnalysis/NoBlurCannyEdgeDetector.cs` | 97–265 |
| FastGaussianBlur (3× box blur) | `references/nina/NINA.Image/ImageAnalysis/FastGaussianBlur.cs` | 48–144 |
| 16→8 bit, stretch map, MTF, debayer helpers | `references/nina/NINA.Image/ImageAnalysis/ImageUtility.cs` | 75–133, 193–209, 221–283 |
| Debayer luminance `floor((R+G+B)/3)` | `references/nina/NINA.Image/ImageAnalysis/BayerFilter16bpp.cs` | 151 |
| Stretched-image detection entry, ROI wiring | `references/nina/NINA.Image/ImageData/RenderedImage.cs` | 76–113 |
| AF caller (params, donut/subsample logic, NaN σ guard) | `references/nina/NINA.WPF.Base/ViewModel/AutoFocus/AutoFocusVM.cs` | 380–462 |
| Sensitivity / noise-reduction enums | `references/nina/NINA.Core/Enum/StarSensitivityEnum.cs`, `NoiseReductionEnum.cs` | all |
| Arcsec/px | `references/nina/NINA.Astrometry/AstroUtil.cs` | 34, 39, 727–731 |
| Profile defaults (stretch 0.2/−2.8, sensitivity High, NR None, debayer true) | `references/nina/NINA.Profile/ImageSettings.cs` | 33–45 |
| Profile defaults (crop ratios 1, AF stars 0) | `references/nina/NINA.Profile/FocuserSettings.cs` | 31–53 |
| Annotation drawing | `references/nina/NINA.Image/ImageAnalysis/StarAnnotator.cs` | 39–92 |
| Analysis defaults (NaN/−1) | `references/nina/NINA.Image/ImageData/StarDetectionAnalysis.cs` | 22–62 |
| ImageStatistics median/MAD/histogram (stretch inputs) | `references/nina/NINA.Image/ImageData/ImageStatistics.cs` | 46–170 |

Accord.NET (LGPL-2.1 — behavior extracted, code not to be copied). **`Accord.Imaging` is vendored in-tree** at
`references/nina/Accord.Imaging/` (ProjectReference from `NINA.Image.csproj`) so these paths/lines ARE the exact
compiled code and were read directly. `Accord.Math` (last two rows) is external NuGet 3.8.2-alpha:

| primitive | file (relative to `references/nina/Accord.Imaging/`) |
|---|---|
| Canny (blur variant, defaults σ=1.4 size 5, thresholds 20/100 unused — NINA passes 10/80) | `AForge.Imaging/Filters/Edge Detectors/CannyEdgeDetector.cs` (:52–364) |
| Gaussian kernel construction (int kernel via `(int)(G/Gmin)` trunc, divisor=Σ) — **read & numerically verified** | `AForge.Imaging/Filters/Convolution/GaussianBlur.cs` (:159–187) |
| Convolution application (`long g/=div` integer trunc, dynamic edge divisor default true) — **read** | `AForge.Imaging/Filters/Convolution/Convolution.cs` (:342–428) |
| SIS threshold (`(byte)(total/weightTotal)` trunc) — **read** | `AForge.Imaging/Filters/Adaptive Binarization/SISThreshold.cs` (:158–212) |
| Threshold filter (`>= threshold → 255`, both 8/16 bpp) — **read** | `AForge.Imaging/Filters/Binarization/Threshold.cs` (:104–149) |
| Binary dilation 3×3 | `AForge.Imaging/Filters/Morphology/Specific Optimizations/BinaryDilation3x3.cs` (:99–184) |
| CCL labeling (8-conn, merge map, renumber) | `Blob Processing/BlobCounter.cs` (:78–80, 167–636) |
| Blob info + edge points | `Blob Processing/BlobCounterBase.cs` (:592–605, 1062–1150, 1168–…) |
| Bicubic resize + kernel | `AForge.Imaging/Filters/Transform/ResizeBicubic.cs` (:102–232), `.../Interpolation.cs` (:31–50) |
| Median 3×3 | `AForge.Imaging/Filters/Smooting/Median.cs` (:48, 107–190) |
| 16→8 bpp (high byte) | `AForge.Imaging/Image.cs` (:440–538) |
| IsCircle (center/radius/tolerance) — **Accord.Math NuGet, not in-tree** | `AForge.Math/Geometry/SimpleShapeChecker.cs` (:94–95, 243–276) |
| `Normal.Kernel2D(variance, size)` — **Accord.Math NuGet, not in-tree** (numerically verified) | `Accord.Math/Distributions/.../Normal.cs` |

---

## 18. Recommended for AstroDeck

- **Adopt as the "NINA-parity" HFR backend** with defaults: `Sensitivity = High`, `NoiseReduction = None`,
  stretch `factor = 0.2`, `black_clipping = −2.8`, unlinked stretch for OSC, photometry on debayered
  luminance. These are NINA's shipped defaults, so AstroDeck's AF numbers will be directly comparable to
  users' NINA history.
- **Keep the two-image design** (stretched 8-bit for structure, raw u16 for photometry). It is the single
  most parity-critical property; running detection on linear data changes the detected star set drastically.
- **`High` sensitivity needs pixel size + focal length** from the profile/camera metadata; implement the NaN
  fallback path (`max(0.5, 1552/W)`), and reproduce the `1.5"` dead-branch quirk only if bit-exact parity is
  wanted — otherwise document the deviation (recommended: reproduce faithfully; it is one line).
- **Determinism**: keep raster-scan blob order and stable sorts (AF selection sort must be stable-descending
  on the 0.3/0.7 score) so repeated runs and cross-machine runs agree.
- **Report both `DetectedStars` (post-clip count) and the AF-subset mean** exactly as NINA does — AF fit code
  upstream expects mean HFR over the AF subset when `NumberOfAFStars > 0`.
- **HFR σ**: expose `HFRStdDev` (sample σ) — AstroDeck's AF fitter should use it as the per-point weight
  exactly like NINA (`MeasureAndError`).
- Prefer `f64` throughout photometry; positions may stay `f32` (NINA uses `Accord.Point` = f32) but keeping
  `f64` is harmless as all comparisons are ≤/≥ with wide margins except `IsCircle`'s f32 mean-deviation —
  compute that one in `f32` if bit-exactness matters.
- For images ≤ 1552 px wide, honor NINA's behavior (no NR, no resize, min star size 5 px) — this affects
  guide-cam-sized frames.
- Longer term, prefer the Hocus Focus detector (separate dossier) as AstroDeck's default and keep this one as
  the compatibility/verification backend, since this detector has no saturation handling, a geometric (not
  flux) centroid, and an image-normalized Canny threshold that makes sensitivity content-dependent.

## 19. Gaps (things the source did not make clear)

Previously-open gaps 1, 2, 3, 6 and 7 are **now resolved from the vendored in-tree source** (verified in the
2026-07-03 completeness pass) and folded into §2.1, §7.1, §7.2, §9.5, §11:

- ~~Convolution rounding~~ **RESOLVED** — `Convolution.cs:342–428` + `GaussianBlur.cs:159–187`: integer kernel
  via `(int)(G/G_min)` truncation, application via `long g /= long div` integer truncation, dynamic edge
  divisor default true. 5×5 kernel / divisor 71 numerically reproduced (σ²=1.96 passed as variance). See §7.1.
- ~~Threshold comparison direction~~ **RESOLVED** — `Threshold.cs:127,145`: `>= threshold → 255` for both 8/16
  bpp; SIS threshold `(byte)(total/weightTotal)` truncates. No off-by-one. See §7.2.
- ~~ImageStatistics median/MAD~~ **RESOLVED** — `ImageStatistics.cs:46–170`: exact 65536-bin histogram
  cumulative-walk median (with even-N tie averaging) and outward-walk MAD, over the raw u16 FlatArray. See §2.1.
- ~~`StarDetectionResult.Params`~~ **RESOLVED (confirmed unused)** — `Detect` (StarDetection.cs:203–257) never
  sets `result.Params`; no code path in the base detector reads it back. Safe to leave `None`/default in Rust.
- ~~Accord version pin~~ **RESOLVED for Accord.Imaging** — NINA vendors `Accord.Imaging` in-tree
  (`references/nina/Accord.Imaging`, a ProjectReference), so all Imaging line numbers cited ARE the exact code
  NINA compiles. Only `Accord.Math` (`Normal.Kernel2D`, `SimpleShapeChecker.IsCircle`) is external NuGet, and
  both used routines are specified/verified above (Kernel2D numerically; IsCircle math per §9.5).

Still open (none block HFR-number parity):

1. **`FlatArray` bit-depth scaling**: whether camera drivers scale 12/14-bit data to 16-bit before it reaches
   `FlatArray` depends on NINA camera settings ("bit scaling") outside the files read. HFR math is
   scale-invariant (background-subtracted ratios and the stretch normalizes by `2^bit_depth−1`), so this does
   NOT affect HFR/detection; only the exported `MaxBrightness`/`Background` ADU magnitudes depend on it.
2. **Bayer demosaic interpolation**: debayer supports RGGB/RGBG/GRGB/GRBG/GBGR/GBRG/BGRG/BGGR
   (ImageUtility.cs:240–275); `BayerFilter16bpp`'s bilinear interpolation weights were only skimmed (the
   `Lum = floor((R+G+B)/3)` luminance formula IS verified, BayerFilter16bpp.cs:151). Only matters for exact
   OSC-luminance photometry parity; mono and the detection-path gray conversion are fully specified.
3. **`Normal.Kernel2D` interior scale constant**: the Accord.Math (external NuGet) source for `Normal.Kernel2D`
   was not read; the derived integer kernel is verified numerically (divisor 71) and the leading Gaussian
   normalization constant provably cancels in `G/G_min`, so this is a documentation gap only, not a numeric one.
