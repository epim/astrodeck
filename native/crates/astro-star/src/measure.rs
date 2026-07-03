// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§5, §6, §7).
// No code copied from NINA/Hocus Focus.

//! Per-candidate measurement and acceptance gates (dossier §5–§7):
//! background annulus, Huber-IRLS background plane, 8-octant contamination gate,
//! clip/flux statistics, 3-pass flux-weighted centroid, and HFR (`MeasureStar`).

use crate::image::{BackgroundPlane, Rect, WorkImage};
use crate::params::{HfrTauPolicy, StarDetectionParams};
use crate::stats::{mad_sigma_true, true_median_sorted, upper_median_sorted};

/// Huber tuning constant for the plane IRLS (dossier §5.2).
pub const HUBER_C: f64 = 1.345;
/// Minimum annulus pixels to estimate a local σ (dossier §5.1).
pub const MIN_PIXELS_FOR_LOCAL_SIGMA: usize = 8;
/// Minimum pixels per contamination sector (dossier §5.2).
pub const MIN_SECTOR_PIXELS: usize = 8;
/// SE-of-a-median factor `sqrt(π/2)` (dossier §5.2).
pub const SE_MEDIAN: f64 = 1.2533;

/// A measured star accepted by the gate sequence (dossier §6.5).
#[derive(Clone, Debug)]
pub struct Star {
    /// flux-weighted centroid `(x, y)` in pixels.
    pub center: (f64, f64),
    /// background-plane value at the centroid.
    pub background: f64,
    /// the local background plane used for all subtraction downstream.
    pub background_plane: BackgroundPlane,
    /// `total_flux / pixel_count` (full-footprint divisor; AF selection only).
    pub mean_brightness: f64,
    /// peak of `(raw − plane)` over clip survivors.
    pub peak_brightness: f64,
    /// `peak − (1 − PeakResponse)·mean_flux`.
    pub normalized_brightness: f64,
    /// candidate bounding box.
    pub bounding_box: Rect,
    /// half-flux radius in pixels.
    pub hfr: f64,
    /// contamination gate tripped (recorded even when the star is kept).
    pub contamination_suspected: bool,
    /// footprint pixel count (`points.len()`).
    pub pixel_count: usize,
    /// clip-survivor count.
    pub unclipped_pixel_count: usize,
    /// bilinear center brightness above background.
    pub center_brightness: f64,
    /// `background + peak >= SaturationThreshold`.
    pub saturated: bool,
    /// PSF fit, if one was attached (dossier §9).
    pub psf: Option<crate::psf::PsfModel>,
}

/// The gate that rejected a candidate (dossier §6 metrics).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RejectReason {
    /// bounding box smaller than the minimum on either axis.
    TooSmall,
    /// bounding box touches a frame border (star assumed clipped).
    OnBorder,
    /// point-cloud eccentricity gate (opt-in).
    TooElongated,
    /// fill ratio below the effective distortion floor.
    TooDistorted,
    /// `ComputeStarParameters` returned null (0/1 survivors or flat).
    Degenerate,
    /// SNR at or below the sensitivity threshold.
    LowSensitivity,
    /// centroid outside the centered-acceptance sub-box.
    NotCentered,
    /// `star_median >= PeakResponse · peak`.
    TooFlat,
    /// `MeasureStar` produced no positive weighted flux.
    HfrAnalysisFailed,
    /// `hfr <= MinHFR`.
    TooLowHfr,
    /// contamination suspected and rejection enabled.
    Contaminated,
}

/// Solve a 3×3 linear system by Gauss elimination with partial pivoting;
/// returns `None` if the pivot magnitude drops below `1e-12` (singular).
fn solve3(mut a: [[f64; 3]; 3], mut b: [f64; 3]) -> Option<[f64; 3]> {
    for col in 0..3 {
        // partial pivot
        let mut piv = col;
        let mut best = a[col][col].abs();
        for r in (col + 1)..3 {
            if a[r][col].abs() > best {
                best = a[r][col].abs();
                piv = r;
            }
        }
        if best < 1e-12 {
            return None;
        }
        if piv != col {
            a.swap(col, piv);
            b.swap(col, piv);
        }
        for r in (col + 1)..3 {
            let f = a[r][col] / a[col][col];
            for c in col..3 {
                a[r][c] -= f * a[col][c];
            }
            b[r] -= f * b[col];
        }
    }
    let mut x = [0.0f64; 3];
    for i in (0..3).rev() {
        let mut s = b[i];
        for c in (i + 1)..3 {
            s -= a[i][c] * x[c];
        }
        x[i] = s / a[i][i];
    }
    Some(x)
}

/// Weighted least-squares plane `b0 + b1·dx + b2·dy` via 3×3 normal equations.
fn solve_weighted_plane(dx: &[f64], dy: &[f64], val: &[f64], w: &[f64]) -> Option<[f64; 3]> {
    let mut a = [[0.0f64; 3]; 3];
    let mut b = [0.0f64; 3];
    for i in 0..val.len() {
        let wi = w[i];
        let (xi, yi, vi) = (dx[i], dy[i], val[i]);
        let row = [1.0, xi, yi];
        for r in 0..3 {
            for c in 0..3 {
                a[r][c] += wi * row[r] * row[c];
            }
            b[r] += wi * row[r] * vi;
        }
    }
    solve3(a, b)
}

/// Octant index for an offset `(dx, dy)` with opposite sectors 4 apart
/// (dossier §5.2).
fn octant(dx: f64, dy: f64) -> usize {
    let ax = dx.abs();
    let ay = dy.abs();
    if dx >= 0.0 && dy >= 0.0 {
        if ax >= ay {
            0
        } else {
            1
        }
    } else if dx >= 0.0 {
        // dy < 0
        if ax >= ay {
            7
        } else {
            6
        }
    } else if dy >= 0.0 {
        // dx < 0
        if ax >= ay {
            3
        } else {
            2
        }
    } else if ax >= ay {
        4
    } else {
        5
    }
}

/// Result of the plane fit + contamination test (dossier §5.2).
struct PlaneResult {
    plane: Option<[f64; 3]>,
    suspected: bool,
}

/// Huber-IRLS background plane + 8-octant contamination test (dossier §5.2).
fn gradient_contamination(
    dx: &[f64],
    dy: &[f64],
    val: &[f64],
    fallback_sigma: f64,
    sensitivity: f64,
) -> PlaneResult {
    let n = val.len();
    if n < 12 {
        return PlaneResult {
            plane: None,
            suspected: false,
        };
    }
    let mut w = vec![1.0f64; n];
    let mut coeffs = [0.0f64; 3];
    let mut solved = false;
    for _iter in 0..4 {
        match solve_weighted_plane(dx, dy, val, &w) {
            Some(c) => {
                coeffs = c;
                solved = true;
            }
            None => {
                return PlaneResult {
                    plane: None,
                    suspected: false,
                }
            }
        }
        let mut res: Vec<f64> = (0..n)
            .map(|i| (val[i] - (coeffs[0] + coeffs[1] * dx[i] + coeffs[2] * dy[i])).abs())
            .collect();
        res.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let sigma = 1.4826 * upper_median_sorted(&res);
        if sigma <= 0.0 {
            break;
        }
        for i in 0..n {
            let r = (val[i] - (coeffs[0] + coeffs[1] * dx[i] + coeffs[2] * dy[i])).abs();
            let z = r / sigma;
            w[i] = if z <= HUBER_C { 1.0 } else { HUBER_C / z };
        }
    }
    if !solved {
        return PlaneResult {
            plane: None,
            suspected: false,
        };
    }
    // final robust residual scale
    let mut resid_abs: Vec<f64> = (0..n)
        .map(|i| (val[i] - (coeffs[0] + coeffs[1] * dx[i] + coeffs[2] * dy[i])).abs())
        .collect();
    resid_abs.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mut local_sigma = 1.4826 * upper_median_sorted(&resid_abs);
    if local_sigma <= 0.0 {
        local_sigma = fallback_sigma;
    }

    let mut suspected = false;
    if sensitivity > 0.0 && local_sigma > 0.0 {
        let mut sectors: [Vec<f64>; 8] = Default::default();
        for i in 0..n {
            let r = val[i] - (coeffs[0] + coeffs[1] * dx[i] + coeffs[2] * dy[i]);
            sectors[octant(dx[i], dy[i])].push(r);
        }
        for s in &mut sectors {
            let count = s.len();
            if count < MIN_SECTOR_PIXELS {
                continue;
            }
            s.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let med = upper_median_sorted(s);
            let se = SE_MEDIAN * local_sigma / (count as f64).sqrt();
            if med > sensitivity * se {
                suspected = true;
            }
        }
    }
    PlaneResult {
        plane: Some(coeffs),
        suspected,
    }
}

/// Full per-candidate measurement (`ComputeStarParameters`, dossier §5).
#[derive(Clone, Debug)]
pub struct StarParams {
    /// the local background plane (tilted or flat).
    pub plane: BackgroundPlane,
    /// upper-median annulus background.
    pub background_median: f64,
    /// Σ `(raw − plane)` over clip survivors.
    pub total_flux: f64,
    /// max `(raw − plane)` over clip survivors.
    pub peak: f64,
    /// true median of survivor fluxes.
    pub star_median: f64,
    /// `total_flux / survivors`.
    pub mean_flux: f64,
    /// `peak − (1 − PeakResponse)·mean_flux`.
    pub normalized_brightness: f64,
    /// footprint pixel count.
    pub pixel_count: usize,
    /// clip-survivor count.
    pub unclipped_count: usize,
    /// 3-pass flux-weighted centroid.
    pub centroid: (f64, f64),
    /// plane value at the centroid.
    pub background_at_center: f64,
    /// bilinear center brightness above background.
    pub center_brightness: f64,
    /// contamination gate tripped.
    pub contamination_suspected: bool,
}

/// The effective per-pixel clip multiplier (dossier §5.3; donut cap ignored in
/// the default mono path).
pub fn effective_clip_multiplier(p: &StarDetectionParams, _candidate_size: usize) -> f64 {
    p.star_clipping_multiplier
}

/// Compute all per-candidate parameters (dossier §5). Returns `None` when the
/// candidate is degenerate (0/1 clip survivors or a flat `max <= min`).
pub fn compute_star_parameters(
    img: &WorkImage,
    bounds: &Rect,
    points: &[(usize, usize)],
    sigma: f64,
    p: &StarDetectionParams,
) -> Option<StarParams> {
    let cx = bounds.cx();
    let cy = bounds.cy();
    let exp = p.background_box_expansion;
    let ex0 = bounds.x.saturating_sub(exp);
    let ey0 = bounds.y.saturating_sub(exp);
    let ex1 = (bounds.right() + exp).min(img.width);
    let ey1 = (bounds.bottom() + exp).min(img.height);

    // annulus = expanded box minus candidate bbox
    let (mut dxs, mut dys, mut vals) = (Vec::new(), Vec::new(), Vec::new());
    let mut annulus_vals = Vec::new();
    for y in ey0..ey1 {
        for x in ex0..ex1 {
            let inside =
                x >= bounds.x && x < bounds.right() && y >= bounds.y && y < bounds.bottom();
            if inside {
                continue;
            }
            let v = img.at(x, y);
            dxs.push(x as f64 - cx);
            dys.push(y as f64 - cy);
            vals.push(v);
            annulus_vals.push(v);
        }
    }

    // background median (upper median) + local MAD σ (TRUE median)
    let background_median = if annulus_vals.is_empty() {
        0.0
    } else {
        let mut s = annulus_vals.clone();
        s.sort_by(|a, b| a.partial_cmp(b).unwrap());
        upper_median_sorted(&s)
    };
    let local_background_sigma = if annulus_vals.len() >= MIN_PIXELS_FOR_LOCAL_SIGMA {
        mad_sigma_true(&annulus_vals, background_median)
    } else {
        0.0
    };
    let contamination_sigma = if local_background_sigma > 0.0 {
        local_background_sigma
    } else {
        sigma
    };

    // robust plane + contamination
    let pr = gradient_contamination(
        &dxs,
        &dys,
        &vals,
        contamination_sigma,
        p.contamination_sensitivity,
    );
    let plane = match pr.plane {
        Some(c) => BackgroundPlane {
            b0: c[0],
            b1: c[1],
            b2: c[2],
            cx,
            cy,
        },
        None => BackgroundPlane::flat(background_median, cx, cy),
    };

    // clip margin & flux over structure points
    let candidate_size = bounds.w.max(bounds.h);
    let clip_margin = effective_clip_multiplier(p, candidate_size) * sigma;

    let mut survivors = 0usize;
    let mut min_r = 1.0f64;
    let mut max_r = 0.0f64;
    for &(x, y) in points {
        let raw = img.at(x, y);
        let pl = plane.value_at(x as f64, y as f64);
        if raw > pl + clip_margin {
            let r = raw - pl;
            if survivors == 0 {
                min_r = r;
                max_r = r;
            } else {
                if r < min_r {
                    min_r = r;
                }
                if r > max_r {
                    max_r = r;
                }
            }
            survivors += 1;
        }
    }
    if survivors <= 1 || max_r <= min_r {
        return None; // degenerate
    }

    let mut total_flux = 0.0f64;
    let peak = max_r;
    let mut survivor_fluxes = Vec::with_capacity(survivors);
    for &(x, y) in points {
        let raw = img.at(x, y);
        let pl = plane.value_at(x as f64, y as f64);
        if raw > pl + clip_margin {
            let f = raw - pl;
            total_flux += f;
            survivor_fluxes.push(f);
        }
    }
    survivor_fluxes.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let star_median = true_median_sorted(&survivor_fluxes);
    let mean_flux = total_flux / survivors as f64;
    let normalized_brightness = peak - (1.0 - p.peak_response) * mean_flux;

    // 3-pass flux-weighted centroid
    let aperture_radius = (bounds.w.min(bounds.h) as f64) / 2.0;
    let survivor_pts: Vec<(f64, f64, f64)> = points
        .iter()
        .filter_map(|&(x, y)| {
            let raw = img.at(x, y);
            let pl = plane.value_at(x as f64, y as f64);
            if raw > pl + clip_margin {
                Some((x as f64, y as f64, raw - pl))
            } else {
                None
            }
        })
        .collect();

    let mut centroid = if survivor_pts.is_empty() {
        if points.is_empty() {
            (0.0, 0.0)
        } else {
            let sx: f64 = points.iter().map(|&(x, _)| x as f64).sum();
            let sy: f64 = points.iter().map(|&(_, y)| y as f64).sum();
            (sx / points.len() as f64, sy / points.len() as f64)
        }
    } else {
        let sw: f64 = survivor_pts.iter().map(|&(_, _, w)| w).sum();
        let sx: f64 = survivor_pts.iter().map(|&(x, _, w)| w * x).sum();
        let sy: f64 = survivor_pts.iter().map(|&(_, y, w)| w * y).sum();
        (sx / sw, sy / sw)
    };
    if !survivor_pts.is_empty() {
        for _pass in 1..3 {
            let r2 = aperture_radius * aperture_radius;
            let (mut sw, mut sx, mut sy) = (0.0, 0.0, 0.0);
            let mut any = false;
            for &(x, y, w) in &survivor_pts {
                let ddx = x - centroid.0;
                let ddy = y - centroid.1;
                if ddx * ddx + ddy * ddy <= r2 {
                    sw += w;
                    sx += w * x;
                    sy += w * y;
                    any = true;
                }
            }
            if any && sw > 0.0 {
                centroid = (sx / sw, sy / sw);
            } else {
                break;
            }
        }
    }

    let background_at_center = plane.value_at(centroid.0, centroid.1);
    let center_brightness = img.bilinear(centroid.0, centroid.1) - background_at_center;

    Some(StarParams {
        plane,
        background_median,
        total_flux,
        peak,
        star_median,
        mean_flux,
        normalized_brightness,
        pixel_count: points.len(),
        unclipped_count: survivors,
        centroid,
        background_at_center,
        center_brightness,
        contamination_suspected: pr.suspected,
    })
}

/// HFR via flux-weighted mean radius over a circular aperture (`MeasureStar`,
/// dossier §7). Returns `Some(hfr)` or `None` when Σ weighted flux ≤ 0.
pub fn measure_hfr(
    img: &WorkImage,
    bounds: &Rect,
    centroid: (f64, f64),
    plane: &BackgroundPlane,
    sigma: f64,
    p: &StarDetectionParams,
) -> Option<f64> {
    let (cx, cy) = centroid;
    let step = p.analysis_sampling_size;
    let aperture_radius = (bounds.w.min(bounds.h) as f64) / 2.0;
    let left = bounds.x as f64;
    let top = bounds.y as f64;
    let right = (bounds.x + bounds.w - 1) as f64;
    let bottom = (bounds.y + bounds.h - 1) as f64;
    let start_x = cx - step * ((cx - left) / step).floor();
    let start_y = cy - step * ((cy - top) / step).floor();
    let tau = effective_clip_multiplier(p, bounds.w.max(bounds.h)) * sigma;

    let mut num = 0.0f64;
    let mut den = 0.0f64;
    let mut y = start_y;
    while y <= bottom + 1e-9 {
        let mut x = start_x;
        while x <= right + 1e-9 {
            let dist = ((x - cx).powi(2) + (y - cy).powi(2)).sqrt();
            if dist > aperture_radius + 0.5 {
                x += step;
                continue;
            }
            let flux = img.bilinear(x, y) - plane.value_at(x, y);
            if flux > tau {
                let v = match p.hfr_tau_policy {
                    HfrTauPolicy::GateOnly => flux,
                    HfrTauPolicy::SubtractTau => flux - tau,
                };
                let w = 1.0 - (dist - (aperture_radius - 0.5)).max(0.0);
                num += w * v * dist;
                den += w * v;
            }
            x += step;
        }
        y += step;
    }
    if den > 0.0 {
        Some(num / den)
    } else {
        None
    }
}

/// Fill ratio for the TooDistorted gate (dossier §6.2, default mono path — no
/// donut hole term). `d = max(w, h)`; a perfect disk ≈ `π/4 ≈ 0.79`.
pub fn fill_ratio(bounds: &Rect, point_count: usize) -> f64 {
    let d = bounds.w.max(bounds.h) as f64;
    if d <= 0.0 {
        return 0.0;
    }
    point_count as f64 / (d * d)
}

/// Run the full acceptance-gate sequence against one candidate (dossier §6),
/// returning the measured [`Star`] or the first [`RejectReason`] that fires.
///
/// `img` is the measurement image; `sigma` is the **MeasurementNoiseSigma**.
/// Donut/defocus, TooElongated and BloomSuppressed paths are not evaluated here
/// (deferred — mono cameras first); their master switch is off by default.
pub fn evaluate_candidate(
    img: &WorkImage,
    bounds: &Rect,
    points: &[(usize, usize)],
    sigma: f64,
    p: &StarDetectionParams,
) -> Result<Star, RejectReason> {
    // 1. TooSmall
    if bounds.w < p.minimum_star_bounding_box_size || bounds.h < p.minimum_star_bounding_box_size {
        return Err(RejectReason::TooSmall);
    }
    // 2. OnBorder
    if bounds.x == 0
        || bounds.y == 0
        || bounds.right() == img.width
        || bounds.bottom() == img.height
    {
        return Err(RejectReason::OnBorder);
    }
    // 3. TooElongated — opt-in (master + DonutMaxStreakEccentricity < 1.0); off
    //    by default, deferred with the donut path.

    // 4. TooDistorted (default path: effective_max_distortion = MaxDistortion)
    if fill_ratio(bounds, points.len()) < p.max_distortion {
        return Err(RejectReason::TooDistorted);
    }

    // 5. Degenerate (ComputeStarParameters returned null)
    let sp = match compute_star_parameters(img, bounds, points, sigma, p) {
        Some(sp) => sp,
        None => return Err(RejectReason::Degenerate),
    };

    // saturated tally (not a rejection)
    let saturated = sp.background_at_center + sp.peak >= p.saturation_threshold;

    // 6. LowSensitivity (default path: peak-based only)
    let sensitivity = if sigma > 0.0 {
        sp.normalized_brightness / sigma
    } else {
        f64::INFINITY
    };
    if sensitivity <= p.sensitivity {
        return Err(RejectReason::LowSensitivity);
    }

    // 7. NotCentered (default path: tol = StarCenterTolerance)
    let tol = p.star_center_tolerance;
    let sub_w = bounds.w as f64 * tol;
    let sub_h = bounds.h as f64 * tol;
    let cx = bounds.cx();
    let cy = bounds.cy();
    if (sp.centroid.0 - cx).abs() > sub_w / 2.0 || (sp.centroid.1 - cy).abs() > sub_h / 2.0 {
        return Err(RejectReason::NotCentered);
    }

    // 8. TooFlat
    if sp.star_median >= p.peak_response * sp.peak {
        return Err(RejectReason::TooFlat);
    }

    // 9. HFR (MeasureStar)
    let hfr = match measure_hfr(img, bounds, sp.centroid, &sp.plane, sigma, p) {
        Some(h) => h,
        None => return Err(RejectReason::HfrAnalysisFailed),
    };

    // 10. TooLowHFR
    if hfr <= p.min_hfr {
        return Err(RejectReason::TooLowHfr);
    }

    // 11. Contaminated
    if sp.contamination_suspected && p.reject_contaminated_stars {
        return Err(RejectReason::Contaminated);
    }

    let mean_brightness = sp.total_flux / sp.pixel_count.max(1) as f64;
    Ok(Star {
        center: sp.centroid,
        background: sp.background_at_center,
        background_plane: sp.plane,
        mean_brightness,
        peak_brightness: sp.peak,
        normalized_brightness: sp.normalized_brightness,
        bounding_box: *bounds,
        hfr,
        contamination_suspected: sp.contamination_suspected,
        pixel_count: sp.pixel_count,
        unclipped_pixel_count: sp.unclipped_count,
        center_brightness: sp.center_brightness,
        saturated,
        psf: None,
    })
}

/// Point-cloud eccentricity from unweighted second moments (dossier §6.1).
pub fn point_cloud_eccentricity(points: &[(usize, usize)]) -> f64 {
    let n = points.len();
    if n < 3 {
        return 0.0;
    }
    let nf = n as f64;
    let mx: f64 = points.iter().map(|&(x, _)| x as f64).sum::<f64>() / nf;
    let my: f64 = points.iter().map(|&(_, y)| y as f64).sum::<f64>() / nf;
    let (mut sxx, mut syy, mut sxy) = (0.0, 0.0, 0.0);
    for &(x, y) in points {
        let dx = x as f64 - mx;
        let dy = y as f64 - my;
        sxx += dx * dx;
        syy += dy * dy;
        sxy += dx * dy;
    }
    sxx /= nf;
    syy /= nf;
    sxy /= nf;
    let tr = sxx + syy;
    let det = sxx * syy - sxy * sxy;
    let disc = (tr * tr / 4.0 - det).max(0.0).sqrt();
    let l1 = tr / 2.0 + disc;
    let l2 = tr / 2.0 - disc;
    if l1 <= 0.0 {
        return 0.0;
    }
    (1.0 - l2 / l1).max(0.0).sqrt()
}
