// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§1.1-§1.4). Derived
// from PHD2 star.cpp:41-70 (state / WasFound), star.cpp:83-124 (`hfr`),
// star.cpp:126-483 (`Star::Find`, FindCentroid path) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! `Star::Find` parity — single-star measurement (dossier §1).
//!
//! [`star_find`] locates and measures one star near an expected position:
//! a 3x3-smoothed peak search, an iteratively 2-sigma-clipped annulus
//! background estimate, a mass-weighted centroid over a thresholded disk,
//! a Simonetti (2004) SNR estimate, and a half-flux-diameter (HFD) size
//! measurement with hot-pixel/oversize rejection and a flat-top saturation
//! heuristic. Only `FindCentroid` mode is ported (`FindPeak` is PHD2's
//! Guiding-Assistant-only mode and is out of scope — dossier §1.3 note).
//!
//! This module holds no state and performs no I/O: every call is a pure
//! function of the frame and the caller-supplied prior position, matching
//! this crate's synchronous, I/O-free contract. PHD2's `Star` is a
//! long-lived object whose `PeakVal`/`Mass`/`SNR`/`HFD` fields persist
//! across calls (and are only zeroed on a hard `STAR_ERROR`); since
//! [`star_find`] has no prior-call state to preserve, the `STAR_ERROR` path
//! here reports `peak_val: 0` rather than a stale prior value.

/// `(minx, miny, maxx, maxy)` — inclusive full-frame pixel bounds (dossier
/// §1.3 step 1). `astro_star::GrayFrame` has no subframe/origin concept, so
/// this is always the whole frame.
type Bounds = (i32, i32, i32, i32);

/// Read one pixel by integer coordinates (bounds are the caller's
/// responsibility, as in the upstream raw-pointer indexing).
#[inline]
fn pixel(frame: &astro_star::GrayFrame, x: i32, y: i32) -> u16 {
    frame.data[y as usize * frame.width + x as usize]
}

/// Result of a single-star measurement (dossier §1.2). Mirrors PHD2's
/// `Star` fields after a `Find` call.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StarFindResult {
    /// star x position, pixels (sub-pixel centroid, or the input `base_x`
    /// if the gates rejected before a centroid was computed).
    pub x: f64,
    /// star y position, pixels.
    pub y: f64,
    /// background-subtracted flux sum over the thresholded aperture.
    pub mass: f64,
    /// Simonetti (2004) signal-to-noise estimate.
    pub snr: f64,
    /// half-flux diameter, pixels (`2 * hfr`).
    pub hfd: f64,
    /// raw (unsmoothed) peak pixel value seen in the search window.
    pub peak_val: u16,
    /// the find outcome (dossier §1.2).
    pub result: FindResult,
}

/// Star-find outcome codes (dossier §1.2). `StarOk`/`StarSaturated` count
/// as "found" ([`was_found`]); the rest do not.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FindResult {
    StarOk,
    StarSaturated,
    StarLowSnr,
    StarLowMass,
    StarLowHfd,
    StarHiHfd,
    StarTooNearEdge,
    StarMassChange,
    StarError,
}

/// `true` for the two "found" outcomes (dossier §1.2 `Star::WasFound`).
pub fn was_found(r: FindResult) -> bool {
    matches!(r, FindResult::StarOk | FindResult::StarSaturated)
}

/// `Star::Find` parameters (dossier §1.1). `Default` matches PHD2's shipped
/// defaults: `search_region` 15 (valid 7..=50), `min_hfd` 1.5, `max_hfd`
/// 20.0, `max_adu` 0 (unknown — use the flat-top heuristic), `pedestal` 0,
/// `bits_per_pixel` 16.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FindParams {
    /// half-width of the square search window, pixels.
    pub search_region: i32,
    /// HFD floor; below this the candidate is rejected as a hot pixel.
    pub min_hfd: f64,
    /// HFD ceiling; above this the candidate is rejected as oversized/diffuse.
    pub max_hfd: f64,
    /// known camera saturation ADU, or 0 to use the flat-top heuristic.
    pub max_adu: u32,
    /// camera-driver ADU offset, subtracted from the raw peak before the
    /// saturation test.
    pub pedestal: u16,
    /// camera bit depth; selects the flat-top heuristic's tolerance.
    pub bits_per_pixel: u32,
}

impl Default for FindParams {
    fn default() -> Self {
        FindParams {
            search_region: 15,
            min_hfd: 1.5,
            max_hfd: 20.0,
            max_adu: 0,
            pedestal: 0,
            bits_per_pixel: 16,
        }
    }
}

/// Locate and measure one star near `(base_x, base_y)` (dossier §1.3,
/// `FindCentroid` mode). `base_x`/`base_y` are truncated toward zero to
/// integer pixel coordinates, matching PHD2's caller passing a `double`
/// star position into `Star::Find`'s `int base_x, int base_y` parameters
/// (`guider_multistar.cpp`'s `SetCurrentPosition`/`UpdateCurrentPosition`
/// call sites — implicit C++ narrowing truncates, it does not round).
pub fn star_find(
    frame: &astro_star::GrayFrame,
    base_x: f64,
    base_y: f64,
    p: &FindParams,
) -> StarFindResult {
    let base_x = base_x as i32;
    let base_y = base_y as i32;

    let bounds: Bounds = (0, 0, frame.width as i32 - 1, frame.height as i32 - 1);
    let (minx, miny, maxx, maxy) = bounds;

    // 1. clip the search window to the frame.
    let sx = (base_x - p.search_region).max(minx);
    let ex = (base_x + p.search_region).min(maxx);
    let sy = (base_y - p.search_region).max(miny);
    let ey = (base_y + p.search_region).min(maxy);

    if ex <= sx || ey <= sy {
        return StarFindResult {
            x: base_x as f64,
            y: base_y as f64,
            mass: 0.0,
            snr: 0.0,
            hfd: 0.0,
            peak_val: 0,
            result: FindResult::StarError,
        };
    }

    // 2. smoothed-peak search over the interior, 3x3 kernel (weights sum to
    //    16); track the top-3 raw pixel values (max3[0] >= max3[1] >= max3[2])
    //    over the same interior via a running insertion.
    let mut peak_x = 0i32;
    let mut peak_y = 0i32;
    let mut peak_val: u32 = 0;
    let mut max3 = [0u16; 3];

    for y in (sy + 1)..=(ey - 1) {
        for x in (sx + 1)..=(ex - 1) {
            let mut p_raw = pixel(frame, x, y);
            let val = 4 * p_raw as u32
                + pixel(frame, x - 1, y - 1) as u32
                + pixel(frame, x + 1, y - 1) as u32
                + pixel(frame, x - 1, y + 1) as u32
                + pixel(frame, x + 1, y + 1) as u32
                + 2 * pixel(frame, x, y - 1) as u32
                + 2 * pixel(frame, x - 1, y) as u32
                + 2 * pixel(frame, x + 1, y) as u32
                + 2 * pixel(frame, x, y + 1) as u32;

            if val > peak_val {
                peak_val = val;
                peak_x = x;
                peak_y = y;
            }

            if p_raw > max3[0] {
                std::mem::swap(&mut p_raw, &mut max3[0]);
            }
            if p_raw > max3[1] {
                std::mem::swap(&mut p_raw, &mut max3[1]);
            }
            if p_raw > max3[2] {
                std::mem::swap(&mut p_raw, &mut max3[2]);
            }
        }
    }

    let peak_val_raw = max3[0]; // reported PeakVal
    let peak_val_smoothed = peak_val / 16;

    // 3. iteratively 2-sigma-clipped annulus background around the peak.
    let (mean_bg, sigma_bg, sigma2_bg, nbg) = annulus_background(frame, (peak_x, peak_y), bounds);

    // 4. threshold + mass-weighted centroid over the disk aperture.
    let thresh = (mean_bg + 3.0 * sigma_bg + 0.5) as u16;
    let disk = centroid_over_disk(frame, (peak_x, peak_y), bounds, mean_bg, thresh);
    let (cx, cy, mass, n) = (disk.cx, disk.cy, disk.mass, disk.n);
    let mut hfr_pixels = disk.hfr_pixels;

    // 5. Simonetti (2004) SNR estimate, nominal gain 0.5 e-/ADU.
    const GAIN: f64 = 0.5;
    const LOW_SNR: f64 = 3.0;
    let mut snr = if n > 0 {
        mass / (mass / GAIN + sigma2_bg * n as f64 * (1.0 + 1.0 / nbg as f64)).sqrt()
    } else {
        0.0
    };

    // 6. false-positive guard: scattered above-threshold pixels can fake a
    //    star; require the smoothed peak itself to be above the threshold.
    if peak_val_smoothed <= thresh as u32 && snr >= LOW_SNR {
        snr = LOW_SNR - 0.1;
    }

    // 7. acceptance gates, in order. Position stays at the (truncated) input
    //    `base_x`/`base_y` here — PHD2 only advances `newX`/`newY` to the
    //    peak-based centroid after both gates pass (star.cpp:393-394).
    if mass < 10.0 {
        return StarFindResult {
            x: base_x as f64,
            y: base_y as f64,
            mass,
            snr,
            hfd: 0.0,
            peak_val: peak_val_raw,
            result: FindResult::StarLowMass,
        };
    }
    if snr < LOW_SNR {
        return StarFindResult {
            x: base_x as f64,
            y: base_y as f64,
            mass,
            snr,
            hfd: 0.0,
            peak_val: peak_val_raw,
            result: FindResult::StarLowSnr,
        };
    }

    // 8. sub-pixel centroid.
    let new_x = peak_x as f64 + cx / mass;
    let new_y = peak_y as f64 + cy / mass;

    // 9. HFD = 2 * half-flux radius.
    let hfd = 2.0 * hfr(&mut hfr_pixels, new_x, new_y, mass);
    if hfd < p.min_hfd {
        return StarFindResult {
            x: new_x,
            y: new_y,
            mass,
            snr,
            hfd,
            peak_val: peak_val_raw,
            result: FindResult::StarLowHfd,
        };
    }
    if hfd > p.max_hfd {
        return StarFindResult {
            x: new_x,
            y: new_y,
            mass,
            snr,
            hfd,
            peak_val: peak_val_raw,
            result: FindResult::StarHiHfd,
        };
    }

    // 10. saturation detection on the raw peak minus pedestal.
    let mx = (peak_val_raw as u32).saturating_sub(p.pedestal as u32);

    let result = if p.max_adu > 0 {
        if mx >= p.max_adu {
            FindResult::StarSaturated
        } else {
            FindResult::StarOk
        }
    } else {
        // flat-top heuristic when saturation ADU is unknown: the top-3
        // values are within a small fraction of the peak.
        let d = (max3[0] as u32).saturating_sub(max3[2] as u32);
        let saturated = if p.bits_per_pixel < 12 {
            d * 191 < mx
        } else {
            d * 65535 < 32 * mx
        };
        if saturated {
            FindResult::StarSaturated
        } else {
            FindResult::StarOk
        }
    };

    StarFindResult {
        x: new_x,
        y: new_y,
        mass,
        snr,
        hfd,
        peak_val: peak_val_raw,
        result,
    }
}

/// 2-sigma-clipped background mean/sigma over the annulus (inner radius
/// `A = 7`, outer `B = 12`, `r^2` in `(A^2, B^2]`) around `peak`, clipped to
/// `bounds`. Up to 9 Welford-accumulated iterations; breaks early on
/// `nbg < 10` (too few points, only possible after the first iteration) or
/// `|mean_bg - prev_mean_bg| < 0.5` (converged). Returns
/// `(mean_bg, sigma_bg, sigma2_bg, nbg)` (dossier §1.3 step 3).
fn annulus_background(
    frame: &astro_star::GrayFrame,
    peak: (i32, i32),
    bounds: Bounds,
) -> (f64, f64, f64, u32) {
    const A: i32 = 7;
    const B: i32 = 12;
    const A2: i32 = A * A;
    const B2: i32 = B * B;
    let (peak_x, peak_y) = peak;
    let (minx, miny, maxx, maxy) = bounds;

    let start_x = (peak_x - B).max(minx);
    let end_x = (peak_x + B).min(maxx);
    let start_y = (peak_y - B).max(miny);
    let end_y = (peak_y + B).min(maxy);

    let mut mean_bg = 0.0f64;
    let mut sigma_bg = 0.0f64;
    let mut sigma2_bg = 0.0f64;
    let mut nbg: u32 = 0;

    for iter in 0..9 {
        let mut sum = 0.0f64;
        let mut running_mean = 0.0f64;
        let mut q = 0.0f64;
        nbg = 0;

        for y in start_y..=end_y {
            let dy = y - peak_y;
            let dy2 = dy * dy;
            for x in start_x..=end_x {
                let dx = x - peak_x;
                let r2 = dx * dx + dy2;
                if r2 <= A2 || r2 > B2 {
                    continue;
                }

                let val = pixel(frame, x, y) as f64;
                if iter > 0 && (val < mean_bg - 2.0 * sigma_bg || val > mean_bg + 2.0 * sigma_bg) {
                    continue;
                }

                sum += val;
                nbg += 1;
                let k = nbg as f64;
                let prev_running_mean = running_mean;
                running_mean += (val - running_mean) / k;
                q += (val - prev_running_mean) * (val - running_mean);
            }
        }

        if nbg < 10 {
            break;
        }

        let prev_mean_bg = mean_bg;
        mean_bg = sum / nbg as f64;
        sigma2_bg = q / (nbg - 1) as f64;
        sigma_bg = sigma2_bg.sqrt();

        if iter > 0 && (mean_bg - prev_mean_bg).abs() < 0.5 {
            break;
        }
    }

    (mean_bg, sigma_bg, sigma2_bg, nbg)
}

/// Accumulated result of [`centroid_over_disk`] (dossier §1.3 step 4).
struct DiskCentroid {
    /// mass-weighted x offset from `peak` (pre-division by `mass`).
    cx: f64,
    /// mass-weighted y offset from `peak` (pre-division by `mass`).
    cy: f64,
    /// background-subtracted flux sum over the kept pixels.
    mass: f64,
    /// count of kept pixels.
    n: u32,
    /// `(x, y, background-subtracted value)` for every pixel kept — the
    /// input to [`hfr`].
    hfr_pixels: Vec<(i32, i32, f64)>,
}

/// Mass-weighted centroid offset (relative to `peak`) over the disk of
/// radius `A = 7` around `peak`, clipped to `bounds`, keeping only pixels
/// at or above `thresh` (dossier §1.3 step 4).
fn centroid_over_disk(
    frame: &astro_star::GrayFrame,
    peak: (i32, i32),
    bounds: Bounds,
    mean_bg: f64,
    thresh: u16,
) -> DiskCentroid {
    const A: i32 = 7;
    const A2: i32 = A * A;
    let (peak_x, peak_y) = peak;
    let (minx, miny, maxx, maxy) = bounds;

    let start_x = (peak_x - A).max(minx);
    let end_x = (peak_x + A).min(maxx);
    let start_y = (peak_y - A).max(miny);
    let end_y = (peak_y + A).min(maxy);

    let mut cx = 0.0f64;
    let mut cy = 0.0f64;
    let mut mass = 0.0f64;
    let mut n: u32 = 0;
    let mut hfr_pixels: Vec<(i32, i32, f64)> = Vec::new();

    for y in start_y..=end_y {
        let dy = y - peak_y;
        let dy2 = dy * dy;
        if dy2 > A2 {
            continue;
        }
        for x in start_x..=end_x {
            let dx = x - peak_x;
            if dx * dx + dy2 > A2 {
                continue;
            }

            let val = pixel(frame, x, y);
            if val < thresh {
                continue;
            }

            let d = val as f64 - mean_bg;
            cx += dx as f64 * d;
            cy += dy as f64 * d;
            mass += d;
            n += 1;
            hfr_pixels.push((x, y, d));
        }
    }

    DiskCentroid {
        cx,
        cy,
        mass,
        n,
        hfr_pixels,
    }
}

/// Half-flux radius (dossier §1.4): the radius from `(cx, cy)` at which
/// cumulative mass, walked in ascending `r^2` order, first exceeds half the
/// total mass — linearly interpolated in radius between the straddling
/// pixels. A single-pixel input (a hot pixel) returns `0.25` directly.
fn hfr(pixels: &mut [(i32, i32, f64)], cx: f64, cy: f64, mass: f64) -> f64 {
    if pixels.len() == 1 {
        return 0.25; // hot pixel
    }

    let mut by_r2: Vec<(f64, f64)> = pixels
        .iter()
        .map(|&(x, y, m)| {
            let dx = x as f64 - cx;
            let dy = y as f64 - cy;
            (dx * dx + dy * dy, m)
        })
        .collect();
    by_r2.sort_by(|a, b| a.0.partial_cmp(&b.0).expect("pixel r^2 is never NaN"));

    let halfm = 0.5 * mass;
    let (mut r20, mut r21, mut m0, mut m1) = (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    for &(r2, m) in by_r2.iter() {
        r20 = r21;
        m0 = m1;
        r21 = r2;
        m1 += m;
        if m1 > halfm {
            break;
        }
    }

    if m1 > m0 {
        let (r0, r1) = (r20.sqrt(), r21.sqrt());
        r0 + (r1 - r0) / (m1 - m0) * (halfm - m0)
    } else {
        0.25
    }
}
