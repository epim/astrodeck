// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§2). No code
// copied from NINA/Hocus Focus.

//! Iteratively-clipped κ-σ background/noise estimation (dossier §2).

use crate::image::WorkImage;

/// Mask lower bound = `f32::EPSILON` (excludes exact-zero calibration borders).
pub const EPS: f64 = f32::EPSILON as f64;

/// Result of a κ-σ estimate.
#[derive(Clone, Copy, Debug)]
pub struct KappaSigma {
    /// robust noise σ (population stddev, divisor N) over the surviving mask.
    pub sigma: f64,
    /// background mean; **lags one iteration** on the convergence break, exactly
    /// as coded upstream (dossier §2, §13.2).
    pub background_mean: f64,
    /// iterations actually run.
    pub iterations: u32,
}

/// Iteratively-clipped mean/σ (dossier §2).
///
/// Mask keeps pixels in `[EPS, threshold − EPS]`; convergence test is the
/// **absolute** σ difference `<= allowed_error`; capped at `max_iter`. On the
/// convergence break the returned `background_mean` is the previous iteration's
/// mean (reproduced quirk).
pub fn kappa_sigma_noise_estimate(
    img: &WorkImage,
    k: f64,
    allowed_error: f64,
    max_iter: u32,
) -> KappaSigma {
    let mut threshold = f32::MAX as f64;
    let mut last_sigma = 1.0f64;
    let mut last_mean = 1.0f64;
    let mut n: u32 = 0;

    while n < max_iter {
        let hi = threshold - EPS;
        let mut sum = 0.0f64;
        let mut sum_sq = 0.0f64;
        let mut count = 0usize;
        for &v in &img.data {
            if v >= EPS && v <= hi {
                sum += v;
                sum_sq += v * v;
                count += 1;
            }
        }
        let (mean, sigma) = if count == 0 {
            (0.0, 0.0)
        } else {
            let m = sum / count as f64;
            let var = (sum_sq / count as f64 - m * m).max(0.0);
            (m, var.sqrt())
        };
        n += 1;
        if n > 1 && (sigma - last_sigma).abs() <= allowed_error {
            last_sigma = sigma; // mean NOT updated here (documented lag)
            break;
        }
        threshold = mean + k * sigma;
        last_sigma = sigma;
        last_mean = mean;
    }

    KappaSigma {
        sigma: last_sigma,
        background_mean: last_mean,
        iterations: n,
    }
}
