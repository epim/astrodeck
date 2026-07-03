// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§12, §12.1).
// No code copied from NINA/Hocus Focus.

//! Detector configuration knobs.
//!
//! [`StarDetectionParams::default`] is the **Simple-mode "Typical" preset** as
//! actually shipped by the Hocus Focus plugin (dossier §12.1): the effective
//! `noise_reduction_radius` is **4** (the thresholded-hotpixel `+= 1` fires) and
//! the effective `sensitivity` is `2.0` (`10.0 * sensitivity_scale`, scale 0.2).
//! Other presets are exposed as constructors. All thresholds/σ are in the
//! normalized image units `adu / (1 << bpp)` unless noted.

/// How the noise floor τ is applied in the HFR flux sum (dossier §7).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HfrTauPolicy {
    /// τ gates a pixel in/out but the full flux is summed (default, unbiased).
    GateOnly,
    /// legacy soft threshold `flux - τ` (biases HFR low).
    SubtractTau,
}

/// PSF model family + fixed/fittable β choice (dossier §9.3, §12).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PsfFitType {
    /// Elliptical Gaussian.
    Gaussian,
    /// Elliptical Moffat, fixed β = 4.0 (default).
    Moffat40,
    /// Elliptical Moffat, fixed β = 2.5.
    Moffat25,
    /// Elliptical Moffat, fixed β = 1.5.
    Moffat15,
    /// Elliptical Moffat, β fitted (8-parameter, seed 4.0, bounds [1,10]).
    MoffatFittable,
}

/// Frame-level HFR aggregation mode (dossier §8.5).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MeasurementAverage {
    /// median HFR with `1.483·MAD` dispersion (default).
    Median,
    /// MAD-bound outlier rejection then arithmetic mean / sample stddev.
    MeanOutliers,
}

/// NINA star-sensitivity setting; only affects the upper MAD bound in
/// [`MeasurementAverage::MeanOutliers`] rejection (dossier §8.2: high = 3.0 when
/// Normal else 4.0).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StarSensitivityLevel {
    /// upper MAD bound 3.0.
    Normal,
    /// upper MAD bound 4.0.
    High,
    /// upper MAD bound 4.0.
    Highest,
}

/// Simple-mode "noise level" preset axis (dossier §12.1).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NoiseLevel {
    /// no hotpixel filter, no noise reduction, sensitivity scale 1.0.
    None,
    /// radius 3, measurement NR off, sensitivity scale 0.2.
    Low,
    /// radius 3, measurement NR off, sensitivity scale 0.2 (default).
    Typical,
    /// radius 5, measurement NR on, sensitivity scale 1.0.
    High,
}

/// Simple-mode "pixel scale" preset axis (dossier §12.1).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PixelScalePreset {
    /// wide field (short focal length / large pixels).
    WideField,
    /// typical (default).
    Typical,
    /// long focal length / small pixels.
    LongFocalLength,
}

/// Simple-mode "focus range" preset axis (dossier §12.1).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FocusRange {
    /// typical (default).
    Typical,
    /// wide range (extra wavelet layer, lower sensitivity).
    WideRange,
}

/// The full detector parameter set (dossier §12). `Default` is the Simple-mode
/// Typical preset (dossier §12.1) — the out-of-box shipped configuration.
#[derive(Clone, Debug)]
pub struct StarDetectionParams {
    // --- image prep (early) ---
    /// bit depth used for normalization `adu / (1 << bpp)`.
    pub bpp: u32,
    /// 3×3 median hotpixel removal.
    pub hotpixel_filtering: bool,
    /// replace only pixels deviating strictly more than the threshold.
    pub hotpixel_thresholding_enabled: bool,
    /// hotpixel threshold as a fraction of full scale (normalized units).
    pub hotpixel_threshold: f64,
    /// blur the measurement image too (default off).
    pub star_measurement_noise_reduction_enabled: bool,
    /// Gaussian kernel radius (kernel = `2r+1`) for the structure-map source.
    pub noise_reduction_radius: usize,
    /// κ-σ clip multiplier AND binarization `k`.
    pub noise_clipping_multiplier: f64,
    /// per-block adaptive threshold surface instead of the scalar threshold.
    pub locally_adaptive_binarization: bool,
    /// block side (px) for the local median/σ grid.
    pub adaptive_noise_block_size: usize,
    /// à-trous wavelet layers; post-blur kernel = `2n+1`.
    pub structure_layers: usize,

    // --- late gates / measurement ---
    /// saturation tally / PSF masking / HFR-aggregation exclusion threshold.
    pub saturation_threshold: f64,
    /// minimum (peak-based) SNR; reject when `sensitivity <= this`.
    pub sensitivity: f64,
    /// TooFlat gate + NormalizedBrightness correction.
    pub peak_response: f64,
    /// minimum fill ratio (`π/4 ≈ 0.79` = perfect disk).
    pub max_distortion: f64,
    /// per-pixel clip τ multiplier (flux / centroid / HFR).
    pub star_clipping_multiplier: f64,
    /// how τ applies in the HFR sum.
    pub hfr_tau_policy: HfrTauPolicy,
    /// centered-acceptance sub-box ratio.
    pub star_center_tolerance: f64,
    /// annulus width (px).
    pub background_box_expansion: usize,
    /// TooSmall gate: reject when a bbox side `< this`.
    pub minimum_star_bounding_box_size: usize,
    /// TooLowHFR gate: reject when `hfr <= this`.
    pub min_hfr: f64,
    /// HFR sampling step.
    pub analysis_sampling_size: f64,
    /// sector-median SE multiplier for the contamination gate (`0` = off).
    pub contamination_sensitivity: f64,
    /// reject contaminated stars (vs flag only).
    pub reject_contaminated_stars: bool,

    // --- post / aggregation ---
    /// exclude saturated stars from frame HFR (needs ≥ 3 unsaturated remaining).
    pub exclude_saturated_stars_from_hfr: bool,
    /// frame HFR aggregation mode.
    pub measurement_average: MeasurementAverage,
    /// NINA star-sensitivity level (MeanOutliers upper bound only).
    pub star_sensitivity_level: StarSensitivityLevel,

    // --- PSF ---
    /// run PSF model fitting (AF path forces this off).
    pub model_psf: bool,
    /// PSF model family.
    pub psf_fit_type: PsfFitType,
    /// Huber-IRLS robust PSF mode (default off).
    pub use_psf_absolute_deviation: bool,
    /// R² acceptance threshold for a PSF fit.
    pub psf_goodness_of_fit_threshold: f64,
    /// number of PSF samples across `sqrt(w·h)`.
    pub psf_resolution: f64,
    /// pixel-area integration PSF models (default off).
    pub psf_pixel_integration: bool,
    /// arcsec/px scale fed to PSF FWHM (NaN if unknown).
    pub pixel_scale: f64,
}

impl Default for StarDetectionParams {
    /// Simple-mode Typical preset (dossier §12.1) — the shipped default.
    fn default() -> Self {
        Self::simple(
            NoiseLevel::Typical,
            PixelScalePreset::Typical,
            FocusRange::Typical,
        )
    }
}

impl StarDetectionParams {
    /// Build the parameter set from the three Simple-mode preset axes exactly as
    /// `DerivePresetSettings` does (dossier §12.1).
    pub fn simple(noise: NoiseLevel, scale: PixelScalePreset, range: FocusRange) -> Self {
        let hotpixel_filtering = noise != NoiseLevel::None;
        let mut sensitivity_scale = 1.0_f64;
        let (measurement_nr, mut noise_reduction_radius) = match noise {
            NoiseLevel::None => (false, 0usize),
            NoiseLevel::Low => {
                sensitivity_scale = 0.2;
                (false, 3)
            }
            NoiseLevel::Typical => {
                sensitivity_scale = 0.2;
                (false, 3)
            }
            NoiseLevel::High => (true, 5),
        };
        let mut structure_layers = 4isize;
        let mut sensitivity = 10.0 * sensitivity_scale;
        if range == FocusRange::WideRange {
            structure_layers += 1;
            sensitivity -= 2.0 * sensitivity_scale;
        }
        let mut min_star_bbox = 5isize;
        let mut analysis_sampling_size = 1.0_f64;
        match scale {
            PixelScalePreset::WideField => {
                structure_layers -= 1;
                min_star_bbox -= 1;
                analysis_sampling_size = 0.5;
            }
            PixelScalePreset::LongFocalLength => {
                structure_layers += 1;
                min_star_bbox += 1;
                sensitivity -= 2.0 * sensitivity_scale;
            }
            PixelScalePreset::Typical => {}
        }
        let hotpixel_thresholding_enabled = true;
        if hotpixel_thresholding_enabled && hotpixel_filtering {
            noise_reduction_radius += 1; // Typical => radius 4 (kernel 9)
        }

        Self {
            bpp: 16,
            hotpixel_filtering,
            hotpixel_thresholding_enabled,
            hotpixel_threshold: 0.001,
            star_measurement_noise_reduction_enabled: measurement_nr,
            noise_reduction_radius,
            noise_clipping_multiplier: 2.0,
            locally_adaptive_binarization: true,
            adaptive_noise_block_size: 128,
            structure_layers: structure_layers.max(1) as usize,
            saturation_threshold: 0.99,
            sensitivity,
            peak_response: 0.75,
            max_distortion: 0.5,
            star_clipping_multiplier: 2.0,
            hfr_tau_policy: HfrTauPolicy::GateOnly,
            star_center_tolerance: 0.3,
            background_box_expansion: 3,
            minimum_star_bounding_box_size: min_star_bbox.max(1) as usize,
            min_hfr: 1.2,
            analysis_sampling_size,
            contamination_sensitivity: 5.0,
            reject_contaminated_stars: true,
            exclude_saturated_stars_from_hfr: true,
            measurement_average: MeasurementAverage::Median,
            star_sensitivity_level: StarSensitivityLevel::Normal,
            model_psf: true,
            psf_fit_type: PsfFitType::Moffat40,
            use_psf_absolute_deviation: false,
            psf_goodness_of_fit_threshold: 0.9,
            psf_resolution: 10.0,
            psf_pixel_integration: false,
            pixel_scale: f64::NAN,
        }
    }

    /// The advanced/`ResetDefaults` bundle (dossier §12, §15): identical to the
    /// Typical preset except `noise_reduction_radius` is 3 (the AF-bank/optimizer
    /// validated configuration).
    pub fn advanced_defaults() -> Self {
        let mut p = Self::default();
        p.noise_reduction_radius = 3;
        p
    }

    /// AutoFocus profile: HFR-only (dossier §12 AF overrides — `model_psf` off,
    /// TooFlat gate stays active, median HFR aggregation).
    pub fn autofocus() -> Self {
        let mut p = Self::default();
        p.model_psf = false;
        p
    }
}
