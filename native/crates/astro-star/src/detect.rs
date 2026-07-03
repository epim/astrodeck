// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§0–§9). No
// code copied from NINA/Hocus Focus.

//! Top-level detection pipeline (dossier §0): image preparation, dual κ-σ noise
//! estimation, structure map + binarization, flood-fill candidate collection,
//! the per-candidate gate sequence + measurement, PSF fitting, and frame-level
//! aggregation.
//!
//! Donut/defocus recovery, CFA/OSC paths and ROI cropping are deferred (mono
//! cameras first); their master switches are off in the shipped default params.

use crate::aggregate::{aggregate, FrameStats};
use crate::filters::{gaussian_blur, hotpixel_filter};
use crate::floodfill::collect_candidates;
use crate::image::{GrayFrame, WorkImage};
use crate::measure::{evaluate_candidate, RejectReason, Star};
use crate::noise::kappa_sigma_noise_estimate;
use crate::params::StarDetectionParams;
use crate::psf::fit_psf;
use crate::structure::{
    binarize_adaptive, binarize_scalar, build_structure_map, histogram_median, scalar_threshold,
};

/// κ-σ absolute convergence tolerance (dossier §2).
pub const KAPPA_SIGMA_ALLOWED_ERROR: f64 = 1e-5;
/// κ-σ iteration cap (dossier §2).
pub const KAPPA_SIGMA_MAX_ITER: u32 = 5;

/// Per-run diagnostic counters (dossier §6 metrics). Every rejected candidate is
/// tallied under exactly one reason.
#[derive(Clone, Debug, Default)]
pub struct DetectionMetrics {
    /// connected structures found by the flood-fill scan.
    pub structure_candidates: usize,
    /// measurement-image pixels `>= SaturationThreshold`.
    pub saturated_pixel_count: usize,
    /// hot pixels replaced by the mono filter.
    pub hotpixel_count: usize,
    /// κ-σ σ on the structure-map source.
    pub structure_noise_sigma: f64,
    /// κ-σ σ on the measurement image (equals structure σ when they coincide).
    pub measurement_noise_sigma: f64,
    /// rejections by gate.
    pub too_small: usize,
    /// rejections by gate.
    pub on_border: usize,
    /// rejections by gate.
    pub too_elongated: usize,
    /// rejections by gate.
    pub too_distorted: usize,
    /// rejections by gate.
    pub degenerate: usize,
    /// rejections by gate.
    pub low_sensitivity: usize,
    /// rejections by gate.
    pub not_centered: usize,
    /// rejections by gate.
    pub too_flat: usize,
    /// rejections by gate.
    pub hfr_analysis_failed: usize,
    /// rejections by gate.
    pub too_low_hfr: usize,
    /// rejections by gate.
    pub contaminated: usize,
    /// PSF fits that failed sample-starvation / R² / solver checks.
    pub psf_fit_failed: usize,
}

impl DetectionMetrics {
    fn tally(&mut self, reason: RejectReason) {
        match reason {
            RejectReason::TooSmall => self.too_small += 1,
            RejectReason::OnBorder => self.on_border += 1,
            RejectReason::TooElongated => self.too_elongated += 1,
            RejectReason::TooDistorted => self.too_distorted += 1,
            RejectReason::Degenerate => self.degenerate += 1,
            RejectReason::LowSensitivity => self.low_sensitivity += 1,
            RejectReason::NotCentered => self.not_centered += 1,
            RejectReason::TooFlat => self.too_flat += 1,
            RejectReason::HfrAnalysisFailed => self.hfr_analysis_failed += 1,
            RejectReason::TooLowHfr => self.too_low_hfr += 1,
            RejectReason::Contaminated => self.contaminated += 1,
        }
    }
}

/// The full detection result.
#[derive(Clone, Debug)]
pub struct DetectionResult {
    /// accepted stars, in deterministic raster (candidate) order.
    pub stars: Vec<Star>,
    /// frame-level aggregation.
    pub stats: FrameStats,
    /// diagnostic counters.
    pub metrics: DetectionMetrics,
}

/// Prepared images: the sharp measurement image and the (possibly blurred)
/// structure-map source (dossier §1.4, §1.6).
struct Prepared {
    measurement: WorkImage,
    structure_source: WorkImage,
    /// whether the two images differ (drives the dual-σ scheme, §2).
    images_differ: bool,
    hotpixel_count: usize,
}

/// Prepare the measurement image and structure-map source (dossier §1.4, §1.6).
fn prepare(frame: &GrayFrame, p: &StarDetectionParams) -> Prepared {
    // TODO(deferred): CFA/OSC debayer hotpixel path (dossier §1.2) — mono first.
    // TODO(deferred): ROI outer-crop + offset-add-back (dossier §1.3, §8.1).
    let mut measurement = frame.to_working(p.bpp);

    // §1.4: hotpixel filter when HotpixelFiltering OR (NR radius > 0 && measurement NR).
    let want_hotpixel = p.hotpixel_filtering
        || (p.noise_reduction_radius > 0 && p.star_measurement_noise_reduction_enabled);
    let mut hotpixel_applied = false;
    let mut hotpixel_count = 0;
    if want_hotpixel {
        let r = hotpixel_filter(
            &mut measurement,
            p.hotpixel_thresholding_enabled,
            p.hotpixel_threshold,
        );
        hotpixel_count = r.count;
        hotpixel_applied = true;
    }
    // optional measurement-image noise reduction
    let mut nr_applied = false;
    if p.noise_reduction_radius > 0 && p.star_measurement_noise_reduction_enabled {
        gaussian_blur(&mut measurement, 2 * p.noise_reduction_radius + 1);
        nr_applied = true;
    }

    // §1.6: structure-map source.
    let mut structure_source = measurement.clone();
    if !(hotpixel_applied || nr_applied || p.noise_reduction_radius == 0) {
        // measurement was left untouched but a structure blur is wanted: filter first
        hotpixel_filter(
            &mut structure_source,
            p.hotpixel_thresholding_enabled,
            p.hotpixel_threshold,
        );
    }
    let mut structure_blurred = false;
    if p.noise_reduction_radius > 0 && !nr_applied {
        gaussian_blur(&mut structure_source, 2 * p.noise_reduction_radius + 1);
        structure_blurred = true;
    }

    // The two images differ only when the structure source got its own blur.
    let images_differ = structure_blurred;
    Prepared {
        measurement,
        structure_source,
        images_differ,
        hotpixel_count,
    }
}

/// Run the full star-detection + measurement pipeline (dossier §0).
pub fn detect_and_measure(frame: &GrayFrame, p: &StarDetectionParams) -> DetectionResult {
    let mut metrics = DetectionMetrics::default();
    if frame.width < 2 || frame.height < 2 {
        return DetectionResult {
            stars: Vec::new(),
            stats: FrameStats::empty(),
            metrics,
        };
    }

    let prep = prepare(frame, p);
    metrics.hotpixel_count = prep.hotpixel_count;

    // --- dual κ-σ noise estimates (§2) ---
    let structure_ks = kappa_sigma_noise_estimate(
        &prep.structure_source,
        p.noise_clipping_multiplier,
        KAPPA_SIGMA_ALLOWED_ERROR,
        KAPPA_SIGMA_MAX_ITER,
    );
    let structure_sigma = structure_ks.sigma;
    let measurement_sigma = if prep.images_differ {
        kappa_sigma_noise_estimate(
            &prep.measurement,
            p.noise_clipping_multiplier,
            KAPPA_SIGMA_ALLOWED_ERROR,
            KAPPA_SIGMA_MAX_ITER,
        )
        .sigma
    } else {
        structure_sigma
    };
    metrics.structure_noise_sigma = structure_sigma;
    metrics.measurement_noise_sigma = measurement_sigma;

    // --- structure map + binarization (§3) ---
    // TODO(deferred): donut/defocus structure boosts + morphological close
    // (dossier §3.1, §3.8) and the associated late-stage gate relaxations (§6).
    let effective_layers = p.structure_layers; // donut/defocus boosts deferred
    let structure_map =
        build_structure_map(&prep.structure_source, effective_layers, p.structure_layers);
    let mut binarized = if p.locally_adaptive_binarization {
        binarize_adaptive(
            &structure_map,
            &structure_map,
            &prep.structure_source,
            p.adaptive_noise_block_size,
            p.noise_clipping_multiplier,
        )
    } else {
        let median = histogram_median(&structure_map);
        let t = scalar_threshold(median, p.noise_clipping_multiplier, structure_sigma);
        binarize_scalar(&structure_map, t)
    };

    // --- saturated-pixel metric (§4) ---
    metrics.saturated_pixel_count = prep
        .measurement
        .data
        .iter()
        .filter(|&&v| v >= p.saturation_threshold)
        .count();

    // --- candidate collection (§4) ---
    let candidates = collect_candidates(&mut binarized);
    metrics.structure_candidates = candidates.len();

    // --- per-candidate gates + measurement + PSF (§6, §9) ---
    let outcomes = evaluate_all(&prep.measurement, &candidates, measurement_sigma, p);

    let mut stars = Vec::new();
    for outcome in outcomes {
        match outcome {
            Ok(star) => stars.push(star),
            Err(reason) => metrics.tally(reason),
        }
    }
    // PSF fit-failure tally (stars kept but psf == None while ModelPSF on)
    if p.model_psf {
        metrics.psf_fit_failed = stars.iter().filter(|s| s.psf.is_none()).count();
    }

    let stats = aggregate(&stars, p);
    DetectionResult {
        stars,
        stats,
        metrics,
    }
}

type Outcome = Result<Star, RejectReason>;

/// Evaluate every candidate (gate sequence + optional PSF fit), preserving raster
/// order. Uses rayon when the `parallel` feature is enabled (dossier §10 — late
/// stage parallelizes per candidate; PSF fit rides along per star).
fn evaluate_all(
    img: &WorkImage,
    candidates: &[crate::floodfill::Candidate],
    sigma: f64,
    p: &StarDetectionParams,
) -> Vec<Outcome> {
    let eval_one = |cand: &crate::floodfill::Candidate| -> Outcome {
        let mut star = evaluate_candidate(img, &cand.bounds, &cand.points, sigma, p)?;
        if p.model_psf {
            star.psf = fit_psf(
                img,
                &star.bounding_box,
                star.center,
                &star.background_plane,
                sigma,
                p,
            );
        }
        Ok(star)
    };

    #[cfg(feature = "parallel")]
    {
        use rayon::prelude::*;
        candidates.par_iter().map(eval_one).collect()
    }
    #[cfg(not(feature = "parallel"))]
    {
        candidates.iter().map(eval_one).collect()
    }
}
