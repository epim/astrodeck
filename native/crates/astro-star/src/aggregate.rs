// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§8). No code
// copied from NINA/Hocus Focus.

//! Frame-level aggregation (dossier §8): HFR outlier rejection, frame HFR
//! median/MAD with the saturated-star exclusion rule, PSF statistics, and the
//! AutoFocus star score.

use crate::measure::Star;
use crate::params::{MeasurementAverage, StarDetectionParams, StarSensitivityLevel};
use crate::stats::{median_mad_1483, true_median};

/// Minimum unsaturated stars required before the saturated-exclusion subset is
/// used for HFR aggregation (dossier §8.5).
pub const MIN_UNSATURATED_FOR_HFR: usize = 3;

/// Frame-level statistics (dossier §8.3, §8.5). Values that require ≥ 2 stars
/// (≥ 2 PSF fits for the PSF quantities) are `NaN`/`0` when unavailable.
#[derive(Clone, Debug)]
pub struct FrameStats {
    /// number of accepted stars.
    pub star_count: usize,
    /// frame HFR (median in `Median` mode, mean in `MeanOutliers` mode).
    pub hfr: f64,
    /// HFR dispersion (`1.483·MAD` in `Median` mode, sample stddev otherwise).
    pub hfr_std_dev: f64,
    /// median PSF σ (px) over stars with an accepted fit (NaN if < 2).
    pub psf_sigma_median: f64,
    /// median PSF R² over stars with an accepted fit (NaN if < 2).
    pub psf_r_squared_median: f64,
    /// median PSF FWHM (arcsec) over fitted stars (NaN if < 2).
    pub fwhm_median: f64,
    /// FWHM `1.483·MAD` dispersion (arcsec).
    pub fwhm_mad: f64,
    /// median PSF eccentricity over fitted stars (NaN if < 2).
    pub eccentricity_median: f64,
    /// eccentricity `1.483·MAD` dispersion.
    pub eccentricity_mad: f64,
}

impl FrameStats {
    /// Empty stats (no stars).
    pub fn empty() -> Self {
        Self {
            star_count: 0,
            hfr: 0.0,
            hfr_std_dev: 0.0,
            psf_sigma_median: f64::NAN,
            psf_r_squared_median: f64::NAN,
            fwhm_median: f64::NAN,
            fwhm_mad: f64::NAN,
            eccentricity_median: f64::NAN,
            eccentricity_mad: f64::NAN,
        }
    }
}

/// HFR outlier rejection for `MeanOutliers` mode (dossier §8.2). With ≤ 1 star
/// the input is returned unchanged. Keeps stars whose HFR lies in
/// `[median − 3·σ, median + high·σ]`, `σ = 1.483·MAD`, `high = 3` for the Normal
/// sensitivity level else `4`.
pub fn reject_hfr_outliers<'a>(stars: &[&'a Star], level: StarSensitivityLevel) -> Vec<&'a Star> {
    if stars.len() <= 1 {
        return stars.to_vec();
    }
    let hfrs: Vec<f64> = stars.iter().map(|s| s.hfr).collect();
    let (median, mad_sigma) = median_mad_1483(&hfrs);
    let high = match level {
        StarSensitivityLevel::Normal => 3.0,
        StarSensitivityLevel::High | StarSensitivityLevel::Highest => 4.0,
    };
    let lo = median - 3.0 * mad_sigma;
    let hi = median + high * mad_sigma;
    stars
        .iter()
        .copied()
        .filter(|s| s.hfr >= lo && s.hfr <= hi)
        .collect()
}

/// The star subset used for HFR aggregation (dossier §8.5): the unsaturated
/// subset when `ExcludeSaturatedStarsFromHFR` and ≥ 3 unsaturated remain, else
/// all stars.
pub fn stars_for_hfr_aggregation(stars: &[Star], p: &StarDetectionParams) -> Vec<usize> {
    if p.exclude_saturated_stars_from_hfr {
        let unsat: Vec<usize> = (0..stars.len()).filter(|&i| !stars[i].saturated).collect();
        if unsat.len() >= MIN_UNSATURATED_FOR_HFR {
            return unsat;
        }
    }
    (0..stars.len()).collect()
}

/// Sample standard deviation (divisor `n − 1`); `0` for `n ≤ 1`.
fn sample_std_dev(values: &[f64]) -> f64 {
    let n = values.len();
    if n <= 1 {
        return 0.0;
    }
    let mean = values.iter().sum::<f64>() / n as f64;
    let var = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (n as f64 - 1.0);
    var.sqrt()
}

fn median_or_nan(values: &[f64]) -> f64 {
    if values.len() < 2 {
        return f64::NAN;
    }
    let mut v = values.to_vec();
    true_median(&mut v)
}

/// Aggregate the accepted stars into [`FrameStats`] (dossier §8.3, §8.5).
pub fn aggregate(stars: &[Star], p: &StarDetectionParams) -> FrameStats {
    let mut stats = FrameStats::empty();
    stats.star_count = stars.len();
    if stars.is_empty() {
        return stats;
    }

    // --- frame HFR (§8.5) ---
    let subset_idx = stars_for_hfr_aggregation(stars, p);
    let subset: Vec<&Star> = subset_idx.iter().map(|&i| &stars[i]).collect();
    if subset.len() > 1 {
        match p.measurement_average {
            MeasurementAverage::Median => {
                let hfrs: Vec<f64> = subset.iter().map(|s| s.hfr).collect();
                let (median, mad_sigma) = median_mad_1483(&hfrs);
                stats.hfr = median;
                stats.hfr_std_dev = mad_sigma;
            }
            MeasurementAverage::MeanOutliers => {
                let kept = reject_hfr_outliers(&subset, p.star_sensitivity_level);
                let hfrs: Vec<f64> = kept.iter().map(|s| s.hfr).collect();
                if !hfrs.is_empty() {
                    stats.hfr = hfrs.iter().sum::<f64>() / hfrs.len() as f64;
                    stats.hfr_std_dev = sample_std_dev(&hfrs);
                }
            }
        }
    } else if let Some(s) = subset.first() {
        stats.hfr = s.hfr;
    }

    // --- PSF aggregation (§8.3): needs ≥ 2 fitted stars ---
    let sigmas: Vec<f64> = stars
        .iter()
        .filter_map(|s| s.psf.as_ref().map(|m| m.sigma))
        .collect();
    if sigmas.len() >= 2 {
        let r2: Vec<f64> = stars
            .iter()
            .filter_map(|s| s.psf.as_ref().map(|m| m.r_squared))
            .collect();
        let fwhm: Vec<f64> = stars
            .iter()
            .filter_map(|s| s.psf.as_ref().map(|m| m.fwhm_arcsec))
            .collect();
        let ecc: Vec<f64> = stars
            .iter()
            .filter_map(|s| s.psf.as_ref().map(|m| m.eccentricity))
            .collect();
        stats.psf_sigma_median = median_or_nan(&sigmas);
        stats.psf_r_squared_median = median_or_nan(&r2);
        let (fm, fmad) = median_mad_1483(&fwhm);
        stats.fwhm_median = fm;
        stats.fwhm_mad = fmad;
        let (em, emad) = median_mad_1483(&ecc);
        stats.eccentricity_median = em;
        stats.eccentricity_mad = emad;
    }

    stats
}

/// AutoFocus star score (dossier §8.4, task item 8): `0.3·hfr + 0.7·mean_brightness`.
pub fn af_star_score(star: &Star) -> f64 {
    0.3 * star.hfr + 0.7 * star.mean_brightness
}

/// Select the top-`n` stars by [`af_star_score`] (dossier §8.4, no-prior path),
/// returning their indices in descending score order.
pub fn select_af_stars(stars: &[Star], n: usize) -> Vec<usize> {
    let mut idx: Vec<usize> = (0..stars.len()).collect();
    idx.sort_by(|&a, &b| {
        af_star_score(&stars[b])
            .partial_cmp(&af_star_score(&stars[a]))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    idx.truncate(n);
    idx
}
