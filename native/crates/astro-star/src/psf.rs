// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§9). No code
// copied from NINA/Hocus Focus.

//! PSF model fitting (dossier §9): elliptical Gaussian and Moffat models with
//! analytic gradients, second-moment seeds, box-constrained LM (+ optional
//! Huber IRLS), θ canonicalization, and R²/reduced-χ² goodness of fit.
//!
//! Angles are radians; σ and offsets are pixels; FWHM in `fwhm_arcsec` uses the
//! `pixel_scale` (arcsec/px) passed via params.

use crate::image::{BackgroundPlane, Rect, WorkImage};
use crate::lm::{lm_minimize, LmConfig};
use crate::params::{PsfFitType, StarDetectionParams};

/// Gaussian FWHM factor `2·√(2 ln 2)` (dossier §9.3).
pub const GAUSSIAN_FWHM_FACTOR: f64 = 2.354_820_045_030_949;
/// Minimum unsaturated PSF samples (dossier §9.1, `MinUnsaturatedPixels`).
pub const MIN_UNSATURATED_PIXELS: usize = 10;
/// Huber IRLS δ multiplier (dossier §9.4).
pub const HUBER_IRLS_MULT: f64 = 1.5;
/// IRLS / clamped LM iteration budget (dossier §9.4).
pub const LM_BUDGET: usize = 20;

/// A fitted PSF (dossier §9.5). `beta` is NaN for the Gaussian model.
#[derive(Clone, Debug)]
pub struct PsfModel {
    /// amplitude above background at the fitted center.
    pub amplitude: f64,
    /// constant background `B`.
    pub background: f64,
    /// fitted center offset x relative to the detector centroid.
    pub offset_x: f64,
    /// fitted center offset y relative to the detector centroid.
    pub offset_y: f64,
    /// major-axis σ (px) — always ≥ `sigma_y` after canonicalization.
    pub sigma_x: f64,
    /// minor-axis σ (px).
    pub sigma_y: f64,
    /// position angle (rad), canonicalized to `(−π/2, π/2]`.
    pub theta: f64,
    /// Moffat β (NaN for Gaussian).
    pub beta: f64,
    /// `sqrt(σx·σy)`.
    pub sigma: f64,
    /// `sqrt(fwhm_x·fwhm_y)` (px).
    pub fwhm_px: f64,
    /// FWHM in arcsec (`fwhm_px · pixel_scale`; NaN if scale unknown).
    pub fwhm_arcsec: f64,
    /// `sqrt(1 − b²/a²)` on the FWHM axes.
    pub eccentricity: f64,
    /// unweighted R² at the canonical parameters.
    pub r_squared: f64,
    /// `rss / (n · σ_noise²)`.
    pub reduced_chi_sq: f64,
}

#[derive(Clone, Copy)]
enum Family {
    Gaussian,
    MoffatFixed(f64),
    MoffatFittable,
}

impl Family {
    fn from(t: PsfFitType) -> Self {
        match t {
            PsfFitType::Gaussian => Family::Gaussian,
            PsfFitType::Moffat40 => Family::MoffatFixed(4.0),
            PsfFitType::Moffat25 => Family::MoffatFixed(2.5),
            PsfFitType::Moffat15 => Family::MoffatFixed(1.5),
            PsfFitType::MoffatFittable => Family::MoffatFittable,
        }
    }
    fn n_params(&self) -> usize {
        match self {
            Family::MoffatFittable => 8,
            _ => 7,
        }
    }
    fn analytic(&self) -> bool {
        !matches!(self, Family::MoffatFittable)
    }
}

#[inline]
fn rotated(dx: f64, dy: f64, x0: f64, y0: f64, theta: f64) -> (f64, f64) {
    let (s, c) = theta.sin_cos();
    let ddx = dx - x0;
    let ddy = dy - y0;
    let xr = ddx * c + ddy * s;
    let yr = -ddx * s + ddy * c;
    (xr, yr)
}

/// `erf` via Abramowitz & Stegun 7.1.26 (max abs error ≈ 1.5e-7) — the PSF
/// pixel-integration path needs `erf` but the crate stays dependency-free
/// (dossier §11: MathNet supplies `erf` upstream; we hand-roll it).
fn erf(x: f64) -> f64 {
    let sign = if x < 0.0 { -1.0 } else { 1.0 };
    let x = x.abs();
    let t = 1.0 / (1.0 + 0.3275911 * x);
    let y = 1.0
        - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
            + 0.254829592)
            * t
            * (-x * x).exp();
    sign * y
}

/// Point-sample model value at sample `(dx, dy)` (dossier §9.3).
fn model_point(fam: Family, p: &[f64], dx: f64, dy: f64) -> f64 {
    let (a, b, x0, y0, sx, sy, th) = (p[0], p[1], p[2], p[3], p[4], p[5], p[6]);
    let (xr, yr) = rotated(dx, dy, x0, y0, th);
    match fam {
        Family::Gaussian => {
            let e = xr * xr / (2.0 * sx * sx) + yr * yr / (2.0 * sy * sy);
            b + a * (-e).exp()
        }
        Family::MoffatFixed(beta) => {
            let d = 1.0 + xr * xr / (sx * sx) + yr * yr / (sy * sy);
            b + a * d.powf(-beta)
        }
        Family::MoffatFittable => {
            let beta = p[7];
            let d = 1.0 + xr * xr / (sx * sx) + yr * yr / (sy * sy);
            b + a * d.powf(-beta)
        }
    }
}

/// Pixel-area-integrated Gaussian (dossier §9.3): separable erf integral in the
/// rotated frame with half-extents `(|cosθ|+|sinθ|)/2`, peak-normalized so `A`
/// keeps its point-sample meaning. Falls back to the point sample if a
/// normalization underflows (`erf → 0` for tiny σ, §13.16).
fn gaussian_integrated(p: &[f64], dx: f64, dy: f64) -> f64 {
    let (a, b, x0, y0, sx, sy, th) = (p[0], p[1], p[2], p[3], p[4], p[5], p[6]);
    let (s, c) = th.sin_cos();
    let (xr, yr) = rotated(dx, dy, x0, y0, th);
    let h = (c.abs() + s.abs()) / 2.0;
    let root2 = std::f64::consts::SQRT_2;
    let nx = erf(h / (sx * root2));
    let ny = erf(h / (sy * root2));
    if nx == 0.0 || ny == 0.0 {
        let e = xr * xr / (2.0 * sx * sx) + yr * yr / (2.0 * sy * sy);
        return b + a * (-e).exp();
    }
    let dphi_x = (erf((xr + h) / (sx * root2)) - erf((xr - h) / (sx * root2))) / 2.0;
    let dphi_y = (erf((yr + h) / (sy * root2)) - erf((yr - h) / (sy * root2))) / 2.0;
    b + a * (dphi_x / nx) * (dphi_y / ny)
}

/// Evaluate the model value at sample `(dx, dy)`, applying pixel-area
/// integration when requested (dossier §9.3): erf-integral for the Gaussian,
/// a 2×2 subsample average (offsets ±0.25) for the Moffat.
fn model_value(fam: Family, p: &[f64], dx: f64, dy: f64, pixel_integration: bool) -> f64 {
    if !pixel_integration {
        return model_point(fam, p, dx, dy);
    }
    match fam {
        Family::Gaussian => gaussian_integrated(p, dx, dy),
        _ => {
            let mut acc = 0.0;
            for &ox in &[-0.25, 0.25] {
                for &oy in &[-0.25, 0.25] {
                    acc += model_point(fam, p, dx + ox, dy + oy);
                }
            }
            acc / 4.0
        }
    }
}

/// Analytic gradient of the model w.r.t. parameters (Gaussian / fixed-β Moffat).
fn model_grad(fam: Family, p: &[f64], dx: f64, dy: f64, out: &mut [f64]) {
    let (a, _b, x0, y0, sx, sy, th) = (p[0], p[1], p[2], p[3], p[4], p[5], p[6]);
    let (s, c) = th.sin_cos();
    let (xr, yr) = rotated(dx, dy, x0, y0, th);
    let sx2 = sx * sx;
    let sy2 = sy * sy;
    match fam {
        Family::Gaussian => {
            let e = xr * xr / (2.0 * sx2) + yr * yr / (2.0 * sy2);
            let ee = (-e).exp();
            out[0] = ee;
            out[1] = 1.0;
            out[2] = a * (c * xr / sx2 - s * yr / sy2) * ee;
            out[3] = a * (s * xr / sx2 + c * yr / sy2) * ee;
            out[4] = a * xr * xr / (sx2 * sx) * ee;
            out[5] = a * yr * yr / (sy2 * sy) * ee;
            out[6] = a * xr * yr * (1.0 / sy2 - 1.0 / sx2) * ee;
        }
        Family::MoffatFixed(beta) => {
            let d = 1.0 + xr * xr / sx2 + yr * yr / sy2;
            let dp = d.powf(-beta);
            let dp1 = d.powf(-beta - 1.0);
            out[0] = dp;
            out[1] = 1.0;
            out[2] = -a * beta * (2.0 * s * yr / sy2 - 2.0 * c * xr / sx2) * dp1;
            out[3] = -a * beta * (-2.0 * s * xr / sx2 - 2.0 * c * yr / sy2) * dp1;
            out[4] = (2.0 * a * beta / (sx2 * sx)) * xr * xr * dp1;
            out[5] = (2.0 * a * beta / (sy2 * sy)) * yr * yr * dp1;
            out[6] = -a * beta * 2.0 * xr * yr * (1.0 / sx2 - 1.0 / sy2) * dp1;
        }
        Family::MoffatFittable => unreachable!("fittable-β uses finite differences"),
    }
}

fn sigma_to_fwhm(fam: Family, sigma: f64, beta_fitted: f64) -> f64 {
    match fam {
        Family::Gaussian => sigma * GAUSSIAN_FWHM_FACTOR,
        Family::MoffatFixed(beta) => sigma * 2.0 * (2f64.powf(1.0 / beta) - 1.0).sqrt(),
        Family::MoffatFittable => sigma * 2.0 * (2f64.powf(1.0 / beta_fitted) - 1.0).sqrt(),
    }
}

/// Fit a PSF model to one star (dossier §9). Returns `Some(model)` only when the
/// fit is well-formed and `r² >= PSFGoodnessOfFitThreshold`.
///
/// `plane` is the star's local background plane (§5.2). Per §9.1, when the plane
/// is tilted the sample values have the tilt removed (`v -= plane(x,y) −
/// background`) while the constant `background` is retained for the model's `B`.
pub fn fit_psf(
    img: &WorkImage,
    bounds: &Rect,
    centroid: (f64, f64),
    plane: &BackgroundPlane,
    sigma_noise: f64,
    p: &StarDetectionParams,
) -> Option<PsfModel> {
    let fam = Family::from(p.psf_fit_type);
    let m = fam.n_params();
    let (cx, cy) = centroid;
    let star_background = plane.value_at(cx, cy);
    let plane_tilted = plane.b1 != 0.0 || plane.b2 != 0.0;

    // --- sample extraction (§9.1) ---
    let nominal_width = ((bounds.w * bounds.h) as f64).sqrt();
    let s = nominal_width / p.psf_resolution;
    // Negated form is deliberate: also rejects a NaN sampling step.
    #[allow(clippy::neg_cmp_op_on_partial_ord)]
    if !(s > 0.0) {
        return None;
    }
    let left = bounds.x as f64;
    let top = bounds.y as f64;
    let right = bounds.right() as f64;
    let bottom = bounds.bottom() as f64;
    let start_x = cx - s * ((cx - left) / s).floor();
    let start_y = cy - s * ((cy - top) / s).floor();
    // Per §9.1: mask saturated (raw value test); remove only the plane TILT so
    // the constant background stays for the model's B term.
    let mut inputs: Vec<(f64, f64)> = Vec::new();
    let mut outputs: Vec<f64> = Vec::new();
    let mut y = start_y;
    while y < bottom {
        let mut x = start_x;
        while x < right {
            let raw = img.bilinear(x, y);
            if raw < p.saturation_threshold {
                let v = if plane_tilted {
                    raw - (plane.value_at(x, y) - star_background)
                } else {
                    raw
                };
                inputs.push((x - cx, y - cy));
                outputs.push(v);
            }
            x += s;
        }
        y += s;
    }
    if inputs.len() < MIN_UNSATURATED_PIXELS {
        return None;
    }
    let n = inputs.len();
    let centroid_brightness = img.bilinear(cx, cy);

    // --- seeds (§9.2) ---
    let amp = (centroid_brightness - star_background).max(0.0);
    let bg = star_background;
    let max_dim = bounds.w.max(bounds.h) as f64;
    // flux-weighted second moments
    let (mut sw, mut sxx, mut syy) = (0.0f64, 0.0f64, 0.0f64);
    for i in 0..n {
        let w = outputs[i] - bg;
        if w > 0.0 {
            let (ddx, ddy) = inputs[i];
            sw += w;
            sxx += w * ddx * ddx;
            syy += w * ddy * ddy;
        }
    }
    let fallback = max_dim / 3.0;
    let mut sig_x = if sw > 0.0 && sxx / sw > 0.0 {
        (sxx / sw).sqrt()
    } else {
        fallback
    };
    let mut sig_y = if sw > 0.0 && syy / sw > 0.0 {
        (syy / sw).sqrt()
    } else {
        fallback
    };
    sig_x = sig_x.clamp(0.5, max_dim);
    sig_y = sig_y.clamp(0.5, max_dim);
    if sig_x < sig_y {
        std::mem::swap(&mut sig_x, &mut sig_y);
    }
    sig_x *= 1.001;

    let mut x = vec![0.0f64; m];
    x[0] = amp;
    x[1] = bg;
    x[2] = 0.0;
    x[3] = 0.0;
    x[4] = sig_x;
    x[5] = sig_y;
    x[6] = 0.0;
    if m == 8 {
        x[7] = 4.0;
    }

    // --- bounds & scales (§9.2) ---
    let w2 = bounds.w as f64 / 2.0;
    let h2 = bounds.h as f64 / 2.0;
    let sig_hi = ((bounds.w * bounds.w + bounds.h * bounds.h) as f64).sqrt() / 2.0;
    let half_pi = std::f64::consts::FRAC_PI_2;
    let mut lower = vec![0.0, 0.0, -w2, -h2, 0.0, 0.0, -half_pi];
    let mut upper = vec![2.0, 1.0, w2, h2, sig_hi, sig_hi, half_pi];
    let mut scale = vec![0.01, 0.01, 0.1, 0.1, 1.0, 1.0, 1.0];
    if m == 8 {
        lower.push(1.0);
        upper.push(10.0);
        scale.push(1.0);
    }

    // --- solve (§9.4) ---
    let analytic = fam.analytic() && !p.psf_pixel_integration;
    let integ = p.psf_pixel_integration;
    let diffstep = 1e-4;
    let inp = &inputs;
    let outp = &outputs;

    let make_eval = |weights: Option<&[f64]>| {
        let inp = inp.clone();
        let outp = outp.clone();
        let weights = weights.map(|w| w.to_vec());
        move |px: &[f64], r: &mut [f64]| {
            for i in 0..inp.len() {
                let (ddx, ddy) = inp[i];
                let val = model_value(fam, px, ddx, ddy, integ) - outp[i];
                r[i] = match &weights {
                    Some(w) => w[i].sqrt() * val,
                    None => val,
                };
            }
        }
    };
    let make_jac = |weights: Option<&[f64]>| {
        let inp = inp.clone();
        let outp = outp.clone();
        let weights = weights.map(|w| w.to_vec());
        move |px: &[f64], j: &mut [f64]| {
            let mut g = vec![0.0f64; m];
            for i in 0..inp.len() {
                let (ddx, ddy) = inp[i];
                let sw = match &weights {
                    Some(w) => w[i].sqrt(),
                    None => 1.0,
                };
                if analytic {
                    model_grad(fam, px, ddx, ddy, &mut g);
                } else {
                    let base = model_value(fam, px, ddx, ddy, integ) - outp[i];
                    for k in 0..m {
                        let mut pp = px.to_vec();
                        pp[k] += diffstep;
                        let f2 = model_value(fam, &pp, ddx, ddy, integ) - outp[i];
                        g[k] = (f2 - base) / diffstep;
                    }
                }
                for k in 0..m {
                    j[i * m + k] = sw * g[k];
                }
            }
        }
    };

    let sol = if p.use_psf_absolute_deviation {
        // Huber IRLS around the LM core (§9.4)
        let delta = HUBER_IRLS_MULT * sigma_noise;
        let mut weights = vec![1.0f64; n];
        let mut prev_sum = f64::INFINITY;
        let mut cur = x.clone();
        let mut iters = 0;
        loop {
            let cfg = LmConfig {
                n_resid: n,
                lower: lower.clone(),
                upper: upper.clone(),
                scale: scale.clone(),
                max_iter: LM_BUDGET,
                epsx: 1e-8,
            };
            cur = lm_minimize(
                &cur,
                &cfg,
                make_eval(Some(&weights)),
                make_jac(Some(&weights)),
            );
            let mut sum = 0.0;
            for i in 0..n {
                let (ddx, ddy) = inputs[i];
                let r = (model_value(fam, &cur, ddx, ddy, integ) - outputs[i]).abs();
                sum += r;
                weights[i] = if delta <= 0.0 || r <= delta {
                    1.0
                } else {
                    delta / r
                };
            }
            let sum_delta = (sum - prev_sum).abs();
            prev_sum = sum;
            iters += 1;
            if sum_delta <= 1e-6 || iters >= LM_BUDGET {
                break;
            }
        }
        cur
    } else {
        let cfg = LmConfig {
            n_resid: n,
            lower: lower.clone(),
            upper: upper.clone(),
            scale: scale.clone(),
            max_iter: 300,
            epsx: 1e-8,
        };
        lm_minimize(&x, &cfg, make_eval(None), make_jac(None))
    };

    // --- canonicalization (§9.5) ---
    let mut sigx = sol[4];
    let mut sigy = sol[5];
    if sigx.is_nan() || sigy.is_nan() {
        return None;
    }
    let mut theta = sol[6].rem_euclid(std::f64::consts::PI);
    if theta > half_pi {
        theta -= std::f64::consts::PI;
    }
    theta = -theta;
    if sigy > sigx {
        std::mem::swap(&mut sigx, &mut sigy);
        theta += if theta < 0.0 { half_pi } else { -half_pi };
    }
    let beta_fitted = if m == 8 { sol[7] } else { f64::NAN };

    // R² at canonical params (quirk: recompute model with canonical values)
    let mut canon = sol.clone();
    canon[4] = sigx;
    canon[5] = sigy;
    canon[6] = theta;
    let mean_y: f64 = outputs.iter().sum::<f64>() / n as f64;
    let mut rss = 0.0;
    let mut tss = 0.0;
    for i in 0..n {
        let (ddx, ddy) = inputs[i];
        let mv = model_value(fam, &canon, ddx, ddy, integ);
        rss += (mv - outputs[i]).powi(2);
        tss += (outputs[i] - mean_y).powi(2);
    }
    let r_squared = if tss <= 0.0 { 0.0 } else { 1.0 - rss / tss };
    let reduced_chi_sq = if sigma_noise == 0.0 || n == 0 {
        f64::NAN
    } else {
        rss / (n as f64 * sigma_noise * sigma_noise)
    };

    // Negated form is deliberate: a NaN R² must fail the gate (§9.5, §13.14).
    #[allow(clippy::neg_cmp_op_on_partial_ord)]
    if !(r_squared >= p.psf_goodness_of_fit_threshold) {
        return None;
    }

    let fwhm_x = sigma_to_fwhm(fam, sigx, beta_fitted);
    let fwhm_y = sigma_to_fwhm(fam, sigy, beta_fitted);
    let a = fwhm_x.max(fwhm_y);
    let b = fwhm_x.min(fwhm_y);
    let eccentricity = if a > 0.0 {
        (1.0 - b * b / (a * a)).max(0.0).sqrt()
    } else {
        0.0
    };

    Some(PsfModel {
        amplitude: sol[0],
        background: sol[1],
        offset_x: sol[2],
        offset_y: sol[3],
        sigma_x: sigx,
        sigma_y: sigy,
        theta,
        beta: beta_fitted,
        sigma: (sigx * sigy).sqrt(),
        fwhm_px: (fwhm_x * fwhm_y).sqrt(),
        fwhm_arcsec: (fwhm_x * fwhm_y).sqrt() * p.pixel_scale,
        eccentricity,
        r_squared,
        reduced_chi_sq,
    })
}
