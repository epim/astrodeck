// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: PyO3 binding layer over astro-star / astro-focus / astro-guide /
// astro-tppa, which are reimplemented from the audited algorithm dossiers in
// docs/native-parity/algorithms/. See those crates' lib.rs for per-crate
// provenance detail. This file adds no algorithm math of its own; it only
// marshals values across the Python boundary per the architecture spec
// docs/superpowers/specs/2026-07-03-native-parity-architecture-design.md §4.4/§5.

//! `astrodeck_native`: PyO3 extension module bundling `astro-star`,
//! `astro-focus`, `astro-guide`, and `astro-tppa` for use from the AstroDeck
//! Python backend.
//!
//! Built with maturin as an abi3 (cp311+) wheel. The public Python surface is:
//! - [`detect_and_measure`] — star detection + measurement over a numpy frame.
//! - [`fit_focus_curve`] — one-shot focus-curve fit.
//! - [`FocusSweep`] — the autofocus sweep state machine.
//! - [`guide_star_find`] — full-frame guide-star search + measurement.
//! - [`GuideEngine`] — the autoguider engine (dossier §7 composition).
//! - [`tppa_from_three`] / [`tppa_update`] — three-point polar alignment.
//! - `__version__` — the crate version string.
//!
//! All algorithm errors surface as Python `ValueError` with the Rust message
//! preserved. Units/conventions follow the underlying crates verbatim.

// The pyo3 `#[pyfunction]`/`#[pymethods]` macros expand to a return-value
// conversion that clippy flags as `useless_conversion` against each function's
// return type; the lint targets generated code, so silence it crate-wide.
#![allow(clippy::useless_conversion)]

use numpy::{PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use astro_focus::{
    AfMethod, BacklashModel, CurveFitting, FailReason, FitOutcome, FocusConfig, Step,
};
use astro_guide::calibration::{default_calibration_distance, DecMode};
use astro_guide::engine::{AlgoKind, AxisAlgoParams, EngineConfig, ScopePointing};
use astro_guide::select::{auto_find, saturation_threshold, SelectParams};
use astro_guide::starfind::FindParams;
use astro_guide::transforms::{Cal, Parity, PierSide};
use astro_guide::types::{Action, Axis, AxisPulse, CalLeg, Direction, FrameMeta};
use astro_star::{
    HfrTauPolicy, MeasurementAverage, PsfFitType, PsfModel, StarDetectionParams,
    StarSensitivityLevel,
};
use astro_tppa::{
    Cardinal, KnobDirection, PolarError, QualityFlags, Site, Solve, TppaModel, TppaOptions,
};

/// Gaussian FWHM factor `2·√(2 ln 2)` (mirrors astro-star's PSF constant), used
/// to derive per-axis FWHM from per-axis σ for the Gaussian PSF family.
const GAUSSIAN_FWHM_FACTOR: f64 = 2.354_820_045_030_949;

/// Unix-epoch Julian Date (1970-01-01T00:00:00Z).
const UNIX_EPOCH_JD: f64 = 2_440_587.5;
/// Seconds per day.
const SECONDS_PER_DAY: f64 = 86_400.0;

// --------------------------------------------------------------------------
// small dict helpers
// --------------------------------------------------------------------------

/// Extract an optional typed value from `d[key]`, treating `None`/missing alike.
fn get_opt<'py, T: FromPyObject<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Option<T>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract()?)),
        _ => Ok(None),
    }
}

/// Extract a required typed value from `d[key]`, erroring if absent.
fn get_req<'py, T: FromPyObject<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<T> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => v.extract(),
        _ => Err(PyValueError::new_err(format!(
            "missing required key '{key}'"
        ))),
    }
}

/// Overwrite `$target` from `$d[$key]` when the key is present.
macro_rules! override_field {
    ($d:expr, $key:literal, $target:expr, $ty:ty) => {
        if let Some(__v) = get_opt::<$ty>($d, $key)? {
            $target = __v;
        }
    };
}

fn median_of(mut v: Vec<f64>) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = v.len();
    if n % 2 == 1 {
        v[n / 2]
    } else {
        0.5 * (v[n / 2 - 1] + v[n / 2])
    }
}

// --------------------------------------------------------------------------
// 1. detect_and_measure
// --------------------------------------------------------------------------

fn build_params(params: Option<&Bound<'_, PyDict>>) -> PyResult<StarDetectionParams> {
    let Some(d) = params else {
        return Ok(StarDetectionParams::default());
    };

    let mut p = match get_opt::<String>(d, "profile")?.as_deref() {
        Some("autofocus") => StarDetectionParams::autofocus(),
        Some("advanced") | Some("advanced_defaults") => StarDetectionParams::advanced_defaults(),
        None | Some("typical") | Some("default") => StarDetectionParams::default(),
        Some(other) => {
            return Err(PyValueError::new_err(format!("unknown profile '{other}'")));
        }
    };

    // image prep
    override_field!(d, "bpp", p.bpp, u32);
    override_field!(d, "hotpixel_filtering", p.hotpixel_filtering, bool);
    override_field!(
        d,
        "hotpixel_thresholding_enabled",
        p.hotpixel_thresholding_enabled,
        bool
    );
    override_field!(d, "hotpixel_threshold", p.hotpixel_threshold, f64);
    override_field!(
        d,
        "star_measurement_noise_reduction_enabled",
        p.star_measurement_noise_reduction_enabled,
        bool
    );
    override_field!(d, "noise_reduction_radius", p.noise_reduction_radius, usize);
    override_field!(
        d,
        "noise_clipping_multiplier",
        p.noise_clipping_multiplier,
        f64
    );
    override_field!(
        d,
        "locally_adaptive_binarization",
        p.locally_adaptive_binarization,
        bool
    );
    override_field!(
        d,
        "adaptive_noise_block_size",
        p.adaptive_noise_block_size,
        usize
    );
    override_field!(d, "structure_layers", p.structure_layers, usize);

    // late gates / measurement
    override_field!(d, "saturation_threshold", p.saturation_threshold, f64);
    override_field!(d, "sensitivity", p.sensitivity, f64);
    override_field!(d, "peak_response", p.peak_response, f64);
    override_field!(d, "max_distortion", p.max_distortion, f64);
    override_field!(
        d,
        "star_clipping_multiplier",
        p.star_clipping_multiplier,
        f64
    );
    override_field!(d, "star_center_tolerance", p.star_center_tolerance, f64);
    override_field!(
        d,
        "background_box_expansion",
        p.background_box_expansion,
        usize
    );
    override_field!(
        d,
        "minimum_star_bounding_box_size",
        p.minimum_star_bounding_box_size,
        usize
    );
    override_field!(d, "min_hfr", p.min_hfr, f64);
    override_field!(d, "analysis_sampling_size", p.analysis_sampling_size, f64);
    override_field!(
        d,
        "contamination_sensitivity",
        p.contamination_sensitivity,
        f64
    );
    override_field!(
        d,
        "reject_contaminated_stars",
        p.reject_contaminated_stars,
        bool
    );

    // post / aggregation
    override_field!(
        d,
        "exclude_saturated_stars_from_hfr",
        p.exclude_saturated_stars_from_hfr,
        bool
    );

    // PSF
    override_field!(d, "model_psf", p.model_psf, bool);
    override_field!(
        d,
        "use_psf_absolute_deviation",
        p.use_psf_absolute_deviation,
        bool
    );
    override_field!(
        d,
        "psf_goodness_of_fit_threshold",
        p.psf_goodness_of_fit_threshold,
        f64
    );
    override_field!(d, "psf_resolution", p.psf_resolution, f64);
    override_field!(d, "psf_pixel_integration", p.psf_pixel_integration, bool);
    override_field!(d, "pixel_scale", p.pixel_scale, f64);

    // enum-valued fields
    if let Some(s) = get_opt::<String>(d, "hfr_tau_policy")? {
        p.hfr_tau_policy = match s.to_lowercase().as_str() {
            "gate_only" | "gateonly" => HfrTauPolicy::GateOnly,
            "subtract_tau" | "subtracttau" => HfrTauPolicy::SubtractTau,
            other => {
                return Err(PyValueError::new_err(format!(
                    "bad hfr_tau_policy '{other}'"
                )))
            }
        };
    }
    if let Some(s) = get_opt::<String>(d, "measurement_average")? {
        p.measurement_average = match s.to_lowercase().as_str() {
            "median" => MeasurementAverage::Median,
            "mean_outliers" | "meanoutliers" => MeasurementAverage::MeanOutliers,
            other => {
                return Err(PyValueError::new_err(format!(
                    "bad measurement_average '{other}'"
                )))
            }
        };
    }
    if let Some(s) = get_opt::<String>(d, "star_sensitivity_level")? {
        p.star_sensitivity_level = match s.to_lowercase().as_str() {
            "normal" => StarSensitivityLevel::Normal,
            "high" => StarSensitivityLevel::High,
            "highest" => StarSensitivityLevel::Highest,
            other => {
                return Err(PyValueError::new_err(format!(
                    "bad star_sensitivity_level '{other}'"
                )))
            }
        };
    }
    if let Some(s) = get_opt::<String>(d, "psf_fit_type")? {
        p.psf_fit_type = match s.to_lowercase().as_str() {
            "gaussian" => PsfFitType::Gaussian,
            "moffat40" | "moffat_4_0" | "moffat" => PsfFitType::Moffat40,
            "moffat25" | "moffat_2_5" => PsfFitType::Moffat25,
            "moffat15" | "moffat_1_5" => PsfFitType::Moffat15,
            "moffat_fittable" | "moffatfittable" => PsfFitType::MoffatFittable,
            other => return Err(PyValueError::new_err(format!("bad psf_fit_type '{other}'"))),
        };
    }

    Ok(p)
}

/// The model name and effective β for a fitted PSF. astro-star only stores β
/// for the fittable-β Moffat (NaN otherwise), so the family is resolved from the
/// requested [`PsfFitType`] — a fixed-β Moffat is still a Moffat.
fn psf_family(fit_type: PsfFitType, m: &PsfModel) -> (&'static str, Option<f64>) {
    match fit_type {
        PsfFitType::Gaussian => ("gaussian", None),
        PsfFitType::Moffat40 => ("moffat", Some(4.0)),
        PsfFitType::Moffat25 => ("moffat", Some(2.5)),
        PsfFitType::Moffat15 => ("moffat", Some(1.5)),
        PsfFitType::MoffatFittable => ("moffat", Some(m.beta)),
    }
}

/// Per-axis FWHM factor: `2·√(2 ln 2)` for Gaussian, `2·√(2^(1/β) − 1)` for
/// Moffat (matches astro-star's `sigma_to_fwhm`).
fn fwhm_factor(beta: Option<f64>) -> f64 {
    match beta {
        None => GAUSSIAN_FWHM_FACTOR,
        Some(b) => 2.0 * (2f64.powf(1.0 / b) - 1.0).sqrt(),
    }
}

fn psf_to_dict<'py>(
    py: Python<'py>,
    m: &PsfModel,
    fit_type: PsfFitType,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    let (model, beta) = psf_family(fit_type, m);
    let factor = fwhm_factor(beta);
    d.set_item("model", model)?;
    d.set_item("sigma_x", m.sigma_x)?;
    d.set_item("sigma_y", m.sigma_y)?;
    d.set_item("theta", m.theta)?;
    match beta {
        Some(b) => d.set_item("beta", b)?,
        None => d.set_item("beta", py.None())?,
    }
    d.set_item("fwhm_x", m.sigma_x * factor)?;
    d.set_item("fwhm_y", m.sigma_y * factor)?;
    d.set_item("r2", m.r_squared)?;
    Ok(d)
}

/// Detect and measure stars in a 2D `uint16` frame.
///
/// `frame` is a numpy `uint16` array shaped `(height, width)`, viewed read-only
/// and zero-copy. `params` mirrors `StarDetectionParams` field names (snake
/// case); `None` selects the shipped Typical preset. An optional `"profile"`
/// key (`"typical"`/`"autofocus"`/`"advanced"`) selects the base preset before
/// per-field overrides are applied.
///
/// Returns `(stars, stats)` where each star is
/// `{x, y, hfr, flux, background, eccentricity, psf}` (pixels; `flux` is the
/// summed background-subtracted survivor flux; `psf` is `None` unless a model
/// fit was accepted) and `stats` is
/// `{hfr_median, hfr_mad, star_count, af_score}` (`af_score` is the median
/// per-star AF score `0.3·hfr + 0.7·mean_brightness`).
#[pyfunction]
#[pyo3(signature = (frame, params=None))]
fn detect_and_measure<'py>(
    py: Python<'py>,
    frame: PyReadonlyArray2<'py, u16>,
    params: Option<Bound<'py, PyDict>>,
) -> PyResult<(Bound<'py, PyList>, Bound<'py, PyDict>)> {
    let shape = frame.shape();
    if shape.len() != 2 {
        return Err(PyValueError::new_err("frame must be a 2D array"));
    }
    let height = shape[0];
    let width = shape[1];
    let slice = frame
        .as_slice()
        .map_err(|e| PyValueError::new_err(format!("frame must be C-contiguous: {e}")))?;
    let p = build_params(params.as_ref())?;

    // Heavy compute with the GIL released (large frames).
    let result = py.allow_threads(|| {
        let gf = astro_star::GrayFrame::new(slice, width, height);
        astro_star::detect_and_measure(&gf, &p)
    });

    let stars = PyList::empty_bound(py);
    for s in &result.stars {
        let sd = PyDict::new_bound(py);
        sd.set_item("x", s.center.0)?;
        sd.set_item("y", s.center.1)?;
        sd.set_item("hfr", s.hfr)?;
        sd.set_item("flux", s.mean_brightness * s.pixel_count as f64)?;
        sd.set_item("background", s.background)?;
        match &s.psf {
            Some(m) => {
                sd.set_item("eccentricity", m.eccentricity)?;
                sd.set_item("psf", psf_to_dict(py, m, p.psf_fit_type)?)?;
            }
            None => {
                sd.set_item("eccentricity", py.None())?;
                sd.set_item("psf", py.None())?;
            }
        }
        stars.append(sd)?;
    }

    let af_score = median_of(result.stars.iter().map(astro_star::af_star_score).collect());
    let stats = PyDict::new_bound(py);
    stats.set_item("hfr_median", result.stats.hfr)?;
    stats.set_item("hfr_mad", result.stats.hfr_std_dev)?;
    stats.set_item("star_count", result.stats.star_count)?;
    stats.set_item("af_score", af_score)?;
    // Per-gate rejection tallies. The Rust pipeline has always counted these;
    // nothing could SEE them, so "2 stars on a field with 200" was a black box
    // and the first diagnosis blamed the wrong stage entirely (saturated
    // pixels — there were 21 in 26 million, and removing them changed nothing).
    // A detector that discards candidates must be able to say which gate did it.
    let m = &result.metrics;
    stats.set_item("candidates", m.structure_candidates)?;
    stats.set_item("saturated_pixels", m.saturated_pixel_count)?;
    stats.set_item("hotpixels", m.hotpixel_count)?;
    stats.set_item("rejected_too_small", m.too_small)?;
    stats.set_item("rejected_on_border", m.on_border)?;
    stats.set_item("rejected_too_elongated", m.too_elongated)?;
    stats.set_item("rejected_too_distorted", m.too_distorted)?;
    stats.set_item("rejected_degenerate", m.degenerate)?;
    stats.set_item("rejected_low_sensitivity", m.low_sensitivity)?;
    stats.set_item("rejected_not_centered", m.not_centered)?;
    stats.set_item("rejected_too_flat", m.too_flat)?;
    stats.set_item("rejected_hfr_failed", m.hfr_analysis_failed)?;
    stats.set_item("rejected_too_low_hfr", m.too_low_hfr)?;
    stats.set_item("rejected_contaminated", m.contaminated)?;
    stats.set_item("structure_noise_sigma", m.structure_noise_sigma)?;
    stats.set_item("measurement_noise_sigma", m.measurement_noise_sigma)?;

    Ok((stars, stats))
}

// --------------------------------------------------------------------------
// 2. fit_focus_curve  +  FitOutcome serialization
// --------------------------------------------------------------------------

fn parse_curve_fitting(s: &str) -> Option<CurveFitting> {
    match s.to_lowercase().replace('-', "_").as_str() {
        "trendlines" | "trend" | "trendline" => Some(CurveFitting::Trendlines),
        "parabolic" | "parabola" | "quadratic" => Some(CurveFitting::Parabolic),
        "trend_parabolic" | "trendparabolic" => Some(CurveFitting::TrendParabolic),
        "hyperbolic" | "hyperbola" => Some(CurveFitting::Hyperbolic),
        "trend_hyperbolic" | "trendhyperbolic" => Some(CurveFitting::TrendHyperbolic),
        _ => None,
    }
}

fn curve_fitting_label(f: CurveFitting) -> &'static str {
    match f {
        CurveFitting::Trendlines => "trendlines",
        CurveFitting::Parabolic => "parabolic",
        CurveFitting::TrendParabolic => "trend_parabolic",
        CurveFitting::Hyperbolic => "hyperbolic",
        CurveFitting::TrendHyperbolic => "trend_hyperbolic",
    }
}

/// Resolve a `method` string to `(AfMethod, CurveFitting)`. Contrast-detection
/// aliases select the Gaussian-peak model (the curve-fitting arg is unused for
/// contrast but a placeholder is returned).
fn parse_method(s: &str) -> PyResult<(AfMethod, CurveFitting)> {
    let low = s.to_lowercase().replace('-', "_");
    match low.as_str() {
        "gaussian" | "contrast" | "contrast_detection" | "contrastdetection" => {
            Ok((AfMethod::ContrastDetection, CurveFitting::Trendlines))
        }
        _ => match parse_curve_fitting(&low) {
            Some(cf) => Ok((AfMethod::StarHfr, cf)),
            None => Err(PyValueError::new_err(format!("unknown fit method '{s}'"))),
        },
    }
}

/// Serialize a completed [`FitOutcome`] into the public dict shape.
fn outcome_to_dict<'py>(
    py: Python<'py>,
    outcome: &FitOutcome,
    method_label: &str,
    valid: bool,
    failure: Option<&str>,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("method", method_label)?;
    d.set_item("best_position", outcome.best_position)?;
    d.set_item("best_value", outcome.best_value)?;

    let r2s = PyDict::new_bound(py);
    match outcome.hyperbolic_r_squared {
        Some(v) => r2s.set_item("hyperbolic", v)?,
        None => r2s.set_item("hyperbolic", py.None())?,
    }
    match outcome.quadratic_r_squared {
        Some(v) => r2s.set_item("quadratic", v)?,
        None => r2s.set_item("quadratic", py.None())?,
    }
    r2s.set_item("left_trend", outcome.left_trend_r_squared)?;
    r2s.set_item("right_trend", outcome.right_trend_r_squared)?;
    d.set_item("r2s", r2s)?;

    let curve = PyList::empty_bound(py);
    for &(x, y) in &outcome.curve {
        curve.append(PyList::new_bound(py, [x, y]))?;
    }
    d.set_item("curve", curve)?;

    let trend = PyDict::new_bound(py);
    let left = PyDict::new_bound(py);
    left.set_item("slope", outcome.left_trend_slope)?;
    left.set_item("r2", outcome.left_trend_r_squared)?;
    trend.set_item("left", left)?;
    let right = PyDict::new_bound(py);
    right.set_item("slope", outcome.right_trend_slope)?;
    right.set_item("r2", outcome.right_trend_r_squared)?;
    trend.set_item("right", right)?;
    match outcome.trend_intersection {
        Some((x, y)) => trend.set_item("intersection", PyList::new_bound(py, [x as f64, y]))?,
        None => trend.set_item("intersection", py.None())?,
    }
    d.set_item("trendlines", trend)?;

    d.set_item("valid", valid)?;
    match failure {
        Some(f) => d.set_item("failure", f)?,
        None => d.set_item("failure", py.None())?,
    }
    Ok(d)
}

/// An invalid/failed fit dict (no curve produced).
fn failed_fit_dict<'py>(
    py: Python<'py>,
    method_label: &str,
    failure: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("method", method_label)?;
    d.set_item("best_position", py.None())?;
    d.set_item("best_value", py.None())?;
    d.set_item("r2s", PyDict::new_bound(py))?;
    d.set_item("curve", PyList::empty_bound(py))?;
    d.set_item("trendlines", PyDict::new_bound(py))?;
    d.set_item("valid", false)?;
    d.set_item("failure", failure)?;
    Ok(d)
}

/// Fit a focus curve to `(position, value, error)` points with the given method.
///
/// `method` is one of `trendlines`, `parabolic`, `hyperbolic`,
/// `trend_parabolic`, `trend_hyperbolic` (star-HFR valley fits) or `gaussian`
/// /`contrast` (contrast-detection peak). Returns the [`FitOutcome`] dict:
/// `{method, best_position, best_value, r2s, curve, trendlines, valid,
/// failure}`. Positions are focuser steps; values are HFR (pixels) for the
/// star-HFR methods or the contrast metric otherwise.
#[pyfunction]
fn fit_focus_curve<'py>(
    py: Python<'py>,
    points: Vec<(f64, f64, f64)>,
    method: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let (af_method, fitting) = parse_method(method)?;
    let label = if af_method == AfMethod::ContrastDetection {
        "gaussian"
    } else {
        curve_fitting_label(fitting)
    };

    let pts: Vec<astro_focus::FocusPoint> = points
        .iter()
        .map(|&(pos, val, err)| astro_focus::FocusPoint::new(pos, val, err))
        .collect();

    let (outcome, valid, failure) = py.allow_threads(|| {
        let fits = astro_focus::fit::compute_all_fits(&pts, af_method);
        match astro_focus::fit::determine_final_focus_point(af_method, fitting, &fits) {
            Some((fx, fy)) => {
                let best_pos = fx.round() as i32;
                let outcome =
                    astro_focus::fit::build_outcome(af_method, fitting, &fits, best_pos, fy, &pts);
                (Some(outcome), true, None)
            }
            None => (None, false, Some("fit unavailable")),
        }
    });

    match outcome {
        Some(o) => outcome_to_dict(py, &o, label, valid, failure),
        None => failed_fit_dict(py, label, failure.unwrap_or("fit unavailable")),
    }
}

// --------------------------------------------------------------------------
// 3. FocusSweep class
// --------------------------------------------------------------------------

fn build_focus_config(d: &Bound<'_, PyDict>) -> PyResult<FocusConfig> {
    let mut c = FocusConfig::default();
    override_field!(d, "step_size", c.step_size, i32);
    override_field!(d, "offset_steps", c.offset_steps, i32);
    override_field!(d, "frames_per_point", c.frames_per_point, u32);
    override_field!(d, "r_squared_threshold", c.r_squared_threshold, f64);
    override_field!(d, "backlash_in", c.backlash_in, i32);
    override_field!(d, "backlash_out", c.backlash_out, i32);
    override_field!(
        d,
        "total_number_of_attempts",
        c.total_number_of_attempts,
        u32
    );
    override_field!(d, "max_step", c.max_step, i32);

    if let Some(s) = get_opt::<String>(d, "method")? {
        c.method = match s.to_lowercase().replace('-', "_").as_str() {
            "star_hfr" | "starhfr" | "hfr" => AfMethod::StarHfr,
            "contrast_detection" | "contrastdetection" | "contrast" => AfMethod::ContrastDetection,
            other => return Err(PyValueError::new_err(format!("bad method '{other}'"))),
        };
    }
    if let Some(s) = get_opt::<String>(d, "curve_fitting")? {
        c.curve_fitting = parse_curve_fitting(&s)
            .ok_or_else(|| PyValueError::new_err(format!("bad curve_fitting '{s}'")))?;
    }
    if let Some(s) = get_opt::<String>(d, "backlash_model")? {
        c.backlash_model = match s.to_lowercase().as_str() {
            "absolute" => BacklashModel::Absolute,
            "overshoot" => BacklashModel::Overshoot,
            other => {
                return Err(PyValueError::new_err(format!(
                    "bad backlash_model '{other}'"
                )))
            }
        };
    }
    Ok(c)
}

fn fail_reason_label(r: FailReason) -> &'static str {
    match r {
        FailReason::NotEnoughSpread => "not_enough_spread",
        FailReason::RSquaredBelowThreshold => "r_squared_below_threshold",
        FailReason::OutOfBounds => "out_of_bounds",
        FailReason::HfrWorseThanStart => "hfr_worse_than_start",
        FailReason::FitUnavailable => "fit_unavailable",
    }
}

/// The autofocus sweep state machine (dossier `nina-autofocus.md`).
///
/// Construct with a `config` dict (mirrors `FocusConfig` field names; missing
/// keys take NINA defaults) and an integer `start_position`. Drive it by
/// alternating [`FocusSweep::next`] and [`FocusSweep::add_measurement`].
#[pyclass]
struct FocusSweep {
    inner: astro_focus::FocusSweep,
    cfg: FocusConfig,
}

#[pymethods]
impl FocusSweep {
    #[new]
    fn new(config: Bound<'_, PyDict>, start_position: i32) -> PyResult<Self> {
        let cfg = build_focus_config(&config)?;
        Ok(FocusSweep {
            inner: astro_focus::FocusSweep::new(cfg, start_position),
            cfg,
        })
    }

    /// Advance the state machine and return the next action:
    /// `{action: "move_to", position}`, `{action: "done", outcome}`, or
    /// `{action: "failed", reason}`.
    fn next<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let step = self.inner.next();
        let d = PyDict::new_bound(py);
        match step {
            Step::MoveTo(pos) => {
                d.set_item("action", "move_to")?;
                d.set_item("position", pos)?;
            }
            Step::Done(outcome) => {
                d.set_item("action", "done")?;
                let label = if self.cfg.method == AfMethod::ContrastDetection {
                    "gaussian"
                } else {
                    curve_fitting_label(self.cfg.curve_fitting)
                };
                d.set_item("outcome", outcome_to_dict(py, &outcome, label, true, None)?)?;
            }
            Step::Failed(reason) => {
                d.set_item("action", "failed")?;
                d.set_item("reason", fail_reason_label(reason))?;
            }
        }
        Ok(d)
    }

    /// Supply the measurement for the most recent `move_to` position: the
    /// frame-averaged `hfr` (pixels), its `stdev`, and the detected
    /// `star_count` (`0` triggers the no-star sentinel).
    fn add_measurement(&mut self, position: i32, hfr: f64, stdev: f64, star_count: u32) {
        self.inner.add_measurement(position, hfr, stdev, star_count);
    }
}

// --------------------------------------------------------------------------
// 4/5. TPPA
// --------------------------------------------------------------------------

fn knob_label(k: KnobDirection) -> &'static str {
    match k {
        KnobDirection::MoveUp => "up",
        KnobDirection::MoveDown => "down",
        KnobDirection::MoveLeft(Cardinal::West) => "left_west",
        KnobDirection::MoveLeft(Cardinal::East) => "left_east",
        KnobDirection::MoveRight(Cardinal::West) => "right_west",
        KnobDirection::MoveRight(Cardinal::East) => "right_east",
    }
}

fn flags_list<'py>(py: Python<'py>, f: &QualityFlags) -> PyResult<Bound<'py, PyList>> {
    let list = PyList::empty_bound(py);
    if f.position_angle_spread_large {
        list.append("position_angle_spread_large")?;
    }
    if f.initial_error_large {
        list.append("initial_error_large")?;
    }
    if f.initial_error_huge {
        list.append("initial_error_huge")?;
    }
    if f.degenerate_geometry {
        list.append("degenerate_geometry")?;
    }
    Ok(list)
}

fn error_to_dict<'py>(py: Python<'py>, e: &PolarError) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("az_arcmin", e.az_arcmin)?;
    d.set_item("alt_arcmin", e.alt_arcmin)?;
    d.set_item("total_arcmin", e.total_arcmin)?;
    d.set_item("az_direction", knob_label(e.az_direction))?;
    d.set_item("alt_direction", knob_label(e.alt_direction))?;
    d.set_item("flags", flags_list(py, &e.flags)?)?;
    // Surface the raw spread magnitude too (not a boolean flag).
    d.set_item(
        "position_angle_spread_deg",
        e.flags.position_angle_spread_deg,
    )?;
    Ok(d)
}

fn model_to_dict<'py>(py: Python<'py>, m: &TppaModel) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("init_ra_deg", m.init_ra_deg)?;
    d.set_item("init_dec_deg", m.init_dec_deg)?;
    d.set_item("init_pa_deg", m.init_pa_deg)?;
    d.set_item("init_time_jd", m.init_time_jd)?;
    d.set_item("initial_alt_err_deg", m.initial_alt_err_deg)?;
    d.set_item("initial_az_err_deg", m.initial_az_err_deg)?;
    d.set_item("initial_total_err_deg", m.initial_total_err_deg)?;
    d.set_item("lat_deg", m.lat_deg)?;
    d.set_item("lon_deg", m.lon_deg)?;
    d.set_item("elev_m", m.elev_m)?;
    d.set_item("pressure_hpa", m.pressure_hpa)?;
    d.set_item("temperature_c", m.temperature_c)?;
    d.set_item("relative_humidity", m.relative_humidity)?;
    d.set_item("wavelength_um", m.wavelength_um)?;
    d.set_item("correct_for_refraction", m.correct_for_refraction)?;
    d.set_item("northern", m.northern)?;
    d.set_item("arcsec_per_pixel", m.arcsec_per_pixel)?;
    d.set_item("image_width_px", m.image_width_px)?;
    d.set_item("image_height_px", m.image_height_px)?;
    d.set_item("position_angle_spread_deg", m.position_angle_spread_deg)?;
    Ok(d)
}

fn dict_to_model(d: &Bound<'_, PyDict>) -> PyResult<TppaModel> {
    Ok(TppaModel {
        init_ra_deg: get_req(d, "init_ra_deg")?,
        init_dec_deg: get_req(d, "init_dec_deg")?,
        init_pa_deg: get_req(d, "init_pa_deg")?,
        init_time_jd: get_req(d, "init_time_jd")?,
        initial_alt_err_deg: get_req(d, "initial_alt_err_deg")?,
        initial_az_err_deg: get_req(d, "initial_az_err_deg")?,
        initial_total_err_deg: get_req(d, "initial_total_err_deg")?,
        lat_deg: get_req(d, "lat_deg")?,
        lon_deg: get_req(d, "lon_deg")?,
        elev_m: get_req(d, "elev_m")?,
        pressure_hpa: get_req(d, "pressure_hpa")?,
        temperature_c: get_req(d, "temperature_c")?,
        relative_humidity: get_req(d, "relative_humidity")?,
        wavelength_um: get_req(d, "wavelength_um")?,
        correct_for_refraction: get_req(d, "correct_for_refraction")?,
        northern: get_req(d, "northern")?,
        arcsec_per_pixel: get_req(d, "arcsec_per_pixel")?,
        image_width_px: get_req(d, "image_width_px")?,
        image_height_px: get_req(d, "image_height_px")?,
        position_angle_spread_deg: get_req(d, "position_angle_spread_deg")?,
    })
}

/// Parse one solve dict `{ra_hours, dec_deg, timestamp_unix_s[, position_angle_deg]}`.
fn parse_solve(d: &Bound<'_, PyDict>) -> PyResult<Solve> {
    let ra_hours: f64 = get_req(d, "ra_hours")?;
    let dec_deg: f64 = get_req(d, "dec_deg")?;
    let ts: f64 = get_req(d, "timestamp_unix_s")?;
    // Position angle is optional (defaults to 0); accept a couple of key names.
    let pa = get_opt::<f64>(d, "position_angle_deg")?
        .or(get_opt::<f64>(d, "pa_deg")?)
        .unwrap_or(0.0);
    Ok(Solve {
        ra_deg: ra_hours * 15.0,
        dec_deg,
        position_angle_deg: pa,
        time_jd_utc: ts / SECONDS_PER_DAY + UNIX_EPOCH_JD,
    })
}

fn parse_site(d: &Bound<'_, PyDict>) -> PyResult<Site> {
    Ok(Site {
        latitude_deg: get_req(d, "latitude_deg")?,
        longitude_deg: get_req(d, "longitude_deg")?,
        elevation_m: get_opt(d, "elevation_m")?.unwrap_or(0.0),
    })
}

fn parse_options(opts: Option<&Bound<'_, PyDict>>) -> PyResult<TppaOptions> {
    let mut o = TppaOptions::default();
    let Some(d) = opts else {
        return Ok(o);
    };
    override_field!(d, "correct_for_refraction", o.correct_for_refraction, bool);
    override_field!(d, "target_distance_deg", o.target_distance_deg, f64);
    override_field!(d, "arcsec_per_pixel", o.arcsec_per_pixel, f64);
    override_field!(d, "image_width_px", o.image_width_px, f64);
    override_field!(d, "image_height_px", o.image_height_px, f64);
    override_field!(d, "pressure_hpa", o.refraction.pressure_hpa, f64);
    override_field!(d, "temperature_c", o.refraction.temperature_c, f64);
    override_field!(d, "relative_humidity", o.refraction.relative_humidity, f64);
    override_field!(d, "wavelength_um", o.refraction.wavelength_um, f64);
    Ok(o)
}

/// Fit the mount axis and initial polar error from three plate solves.
///
/// `solves` is a list of exactly three dicts
/// `{ra_hours, dec_deg, timestamp_unix_s}` (optional `position_angle_deg`);
/// `site` is `{latitude_deg, longitude_deg}` (optional `elevation_m`);
/// `options` mirrors [`TppaOptions`] (`correct_for_refraction`,
/// `arcsec_per_pixel`, `image_width_px`, `image_height_px`,
/// `target_distance_deg`, and the refraction params). Returns
/// `{model, error}` where `model` is an opaque serializable dict for
/// [`tppa_update`] and `error` is the polar-error dict.
#[pyfunction]
#[pyo3(signature = (solves, site, options=None))]
fn tppa_from_three<'py>(
    py: Python<'py>,
    solves: Vec<Bound<'py, PyDict>>,
    site: Bound<'py, PyDict>,
    options: Option<Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyDict>> {
    if solves.len() != 3 {
        return Err(PyValueError::new_err(format!(
            "expected exactly 3 solves, got {}",
            solves.len()
        )));
    }
    let s0 = parse_solve(&solves[0])?;
    let s1 = parse_solve(&solves[1])?;
    let s2 = parse_solve(&solves[2])?;
    let site = parse_site(&site)?;
    let opts = parse_options(options.as_ref())?;

    let (model, err) = astro_tppa::tppa_from_three(&[s0, s1, s2], site, &opts)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;

    let out = PyDict::new_bound(py);
    out.set_item("model", model_to_dict(py, &model)?)?;
    out.set_item("error", error_to_dict(py, &err)?)?;
    Ok(out)
}

/// Re-estimate the live polar error from a new solve during the adjustment
/// phase, using the frozen `model` from [`tppa_from_three`]. The axis is not
/// re-fit. `solve` is one `{ra_hours, dec_deg, timestamp_unix_s}` dict. Returns
/// the same error dict shape as [`tppa_from_three`].
#[pyfunction]
fn tppa_update<'py>(
    py: Python<'py>,
    model: Bound<'py, PyDict>,
    solve: Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyDict>> {
    let m = dict_to_model(&model)?;
    let s = parse_solve(&solve)?;
    let err = astro_tppa::tppa_update(&m, &s).map_err(|e| PyValueError::new_err(e.to_string()))?;
    error_to_dict(py, &err)
}

// --------------------------------------------------------------------------
// 6. guide_star_find  +  GuideEngine class
// --------------------------------------------------------------------------

fn parse_algo_kind(s: &str) -> PyResult<AlgoKind> {
    match s.to_lowercase().replace('-', "_").as_str() {
        "hysteresis" => Ok(AlgoKind::Hysteresis),
        "resist_switch" | "resistswitch" => Ok(AlgoKind::ResistSwitch),
        "lowpass" => Ok(AlgoKind::Lowpass),
        "lowpass2" => Ok(AlgoKind::Lowpass2),
        "z_filter" | "zfilter" => Ok(AlgoKind::ZFilter),
        "ppec" | "gaussian_process" | "predictive_pec" => Ok(AlgoKind::Ppec),
        other => Err(PyValueError::new_err(format!(
            "unknown guide algorithm '{other}'"
        ))),
    }
}

fn parse_dec_mode(s: &str) -> PyResult<DecMode> {
    match s.to_lowercase().as_str() {
        "auto" => Ok(DecMode::Auto),
        "off" => Ok(DecMode::Off),
        "north" => Ok(DecMode::North),
        "south" => Ok(DecMode::South),
        other => Err(PyValueError::new_err(format!(
            "unknown dec_guide_mode '{other}'"
        ))),
    }
}

fn pier_side_label(p: PierSide) -> &'static str {
    match p {
        PierSide::East => "east",
        PierSide::West => "west",
        PierSide::Unknown => "unknown",
    }
}

fn parse_pier_side(s: &str) -> PyResult<PierSide> {
    match s.to_lowercase().as_str() {
        "east" => Ok(PierSide::East),
        "west" => Ok(PierSide::West),
        "unknown" => Ok(PierSide::Unknown),
        other => Err(PyValueError::new_err(format!("bad pier_side '{other}'"))),
    }
}

fn parity_label(p: Parity) -> &'static str {
    match p {
        Parity::Even => "even",
        Parity::Odd => "odd",
        Parity::Unknown => "unknown",
    }
}

fn parse_parity(s: &str) -> PyResult<Parity> {
    match s.to_lowercase().as_str() {
        "even" => Ok(Parity::Even),
        "odd" => Ok(Parity::Odd),
        "unknown" => Ok(Parity::Unknown),
        other => Err(PyValueError::new_err(format!("bad parity '{other}'"))),
    }
}

fn axis_label(a: Axis) -> &'static str {
    match a {
        Axis::Ra => "ra",
        Axis::Dec => "dec",
    }
}

fn direction_label(d: Direction) -> &'static str {
    match d {
        Direction::North => "north",
        Direction::South => "south",
        Direction::East => "east",
        Direction::West => "west",
    }
}

fn cal_leg_label(l: CalLeg) -> &'static str {
    match l {
        CalLeg::GoWest => "go_west",
        CalLeg::GoEast => "go_east",
        CalLeg::ClearBacklash => "clear_backlash",
        CalLeg::GoNorth => "go_north",
        CalLeg::GoSouth => "go_south",
        CalLeg::NudgeSouth => "nudge_south",
    }
}

fn cal_to_dict<'py>(py: Python<'py>, c: &Cal) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("x_rate", c.x_rate)?;
    d.set_item("y_rate", c.y_rate)?;
    d.set_item("x_angle", c.x_angle)?;
    d.set_item("y_angle", c.y_angle)?;
    d.set_item("y_angle_error", c.y_angle_error)?;
    d.set_item("declination", c.declination)?;
    d.set_item("pier_side", pier_side_label(c.pier_side))?;
    d.set_item("ra_parity", parity_label(c.ra_parity))?;
    d.set_item("dec_parity", parity_label(c.dec_parity))?;
    d.set_item("rotator_angle", c.rotator_angle)?;
    d.set_item("binning", c.binning)?;
    d.set_item("is_valid", c.is_valid)?;
    Ok(d)
}

fn dict_to_cal(d: &Bound<'_, PyDict>) -> PyResult<Cal> {
    Ok(Cal {
        x_rate: get_req(d, "x_rate")?,
        y_rate: get_req(d, "y_rate")?,
        x_angle: get_req(d, "x_angle")?,
        y_angle: get_req(d, "y_angle")?,
        y_angle_error: get_req(d, "y_angle_error")?,
        declination: get_req(d, "declination")?,
        pier_side: parse_pier_side(&get_req::<String>(d, "pier_side")?)?,
        ra_parity: parse_parity(&get_req::<String>(d, "ra_parity")?)?,
        dec_parity: parse_parity(&get_req::<String>(d, "dec_parity")?)?,
        rotator_angle: get_req(d, "rotator_angle")?,
        binning: get_req(d, "binning")?,
        is_valid: get_req(d, "is_valid")?,
    })
}

/// Parse an optional per-axis algorithm-parameter sub-dict (Tier 2) into an
/// [`AxisAlgoParams`]. A missing or `None` sub-dict yields
/// [`AxisAlgoParams::default`] (every field `None`), which the engine
/// reconstructs at each algorithm's dossier §15 default — the no-regression
/// invariant. Each present key is read as an optional `f64`; range validation
/// is deliberately NOT done here — the engine's clamping constructors
/// (astro-guide `make_algo`) own the clamp/fallback so the Rust and Python
/// layers cannot disagree on it.
fn parse_axis_params(d: &Bound<'_, PyDict>, key: &str) -> PyResult<AxisAlgoParams> {
    let Some(sub) = get_opt::<Bound<'_, PyDict>>(d, key)? else {
        return Ok(AxisAlgoParams::default());
    };
    Ok(AxisAlgoParams {
        min_move: get_opt::<f64>(&sub, "min_move")?,
        aggression: get_opt::<f64>(&sub, "aggression")?,
        hysteresis: get_opt::<f64>(&sub, "hysteresis")?,
        slope_weight: get_opt::<f64>(&sub, "slope_weight")?,
        aggressiveness: get_opt::<f64>(&sub, "aggressiveness")?,
        exp_factor: get_opt::<f64>(&sub, "exp_factor")?,
    })
}

/// Build an [`EngineConfig`] from a `config` dict (snake_case field names;
/// missing keys take the dossier §15 defaults from [`EngineConfig::default`]).
/// `image_scale_arcsec`, when present, derives `cal.calibration_distance` via
/// the dossier §8.1 formula ([`default_calibration_distance`]) before any
/// explicit `calibration_distance` override is applied, so a caller can still
/// hand-tune it directly.
fn build_engine_config(d: &Bound<'_, PyDict>) -> PyResult<EngineConfig> {
    let mut c = EngineConfig::default();

    if let Some(scale) = get_opt::<f64>(d, "image_scale_arcsec")? {
        c.cal.calibration_distance = default_calibration_distance(scale);
    }
    override_field!(d, "calibration_distance", c.cal.calibration_distance, f64);
    override_field!(
        d,
        "calibration_duration_ms",
        c.cal.calibration_duration_ms,
        u32
    );
    override_field!(d, "max_steps", c.cal.max_steps, u32);
    override_field!(d, "assume_orthogonal", c.cal.assume_orthogonal, bool);

    override_field!(d, "search_region", c.find.search_region, i32);
    override_field!(d, "min_hfd", c.find.min_hfd, f64);
    override_field!(d, "max_hfd", c.find.max_hfd, f64);
    override_field!(d, "max_adu", c.find.max_adu, u32);
    override_field!(d, "pedestal", c.find.pedestal, u16);
    override_field!(d, "bits_per_pixel", c.find.bits_per_pixel, u32);

    override_field!(d, "max_ra_duration_ms", c.max_ra_duration_ms, u32);
    override_field!(d, "max_dec_duration_ms", c.max_dec_duration_ms, u32);
    override_field!(d, "blc_pulse_ms", c.blc_pulse_ms, u32);
    // Multi-star tracking (dossier §2.6/§4; P3-T1). `1` (default) keeps
    // `process()` on the single-star path end to end; `> 1` enables
    // `GuideEngine::measure`'s multi-star acquisition/tracking and
    // `ingest`'s `RefineOffset` call.
    override_field!(d, "max_stars", c.max_stars, usize);

    if let Some(s) = get_opt::<String>(d, "ra_algorithm")? {
        c.ra_algorithm = parse_algo_kind(&s)?;
    }
    if let Some(s) = get_opt::<String>(d, "dec_algorithm")? {
        c.dec_algorithm = parse_algo_kind(&s)?;
        // PPEC (Gaussian-process predictive PEC) is RA-only (dossier §6.8;
        // PHD2 `mount.cpp:227-240` — present in `RA_ALGORITHMS`, absent from
        // `DEC_ALGORITHMS`/`AO_ALGORITHMS`). Reject a Dec `ppec` config here,
        // at the validation layer, rather than in `engine::make_algo` (whose
        // never-fails contract is binding); the engine keeps a ResistSwitch
        // fallback as defense in depth for a string that bypasses this check.
        if c.dec_algorithm == AlgoKind::Ppec {
            return Err(PyValueError::new_err(
                "PPEC (gaussian_process) is RA-only and cannot be used as a Dec \
                 guide algorithm",
            ));
        }
    }
    if let Some(s) = get_opt::<String>(d, "dec_guide_mode")? {
        c.dec_guide_mode = parse_dec_mode(&s)?;
    }

    // Tier 2: optional per-axis algorithm-parameter sub-dicts. Absent => each
    // stays AxisAlgoParams::default() (all None) from EngineConfig::default().
    c.ra_params = parse_axis_params(d, "ra_params")?;
    c.dec_params = parse_axis_params(d, "dec_params")?;

    Ok(c)
}

/// Build the `(FindParams, SelectParams)` pair [`guide_star_find`] needs from
/// an optional `params` dict (snake_case; union of both structs' field
/// names). `search_region` (when present) sets both — `SelectParams`'s copy
/// governs candidate generation, `FindParams`'s copy governs the
/// [`saturation_threshold`] probe.
fn build_guide_search_params(
    params: Option<&Bound<'_, PyDict>>,
) -> PyResult<(FindParams, SelectParams)> {
    let mut find = FindParams::default();
    let mut sel = SelectParams::default();
    let Some(d) = params else {
        return Ok((find, sel));
    };

    override_field!(d, "search_region", find.search_region, i32);
    override_field!(d, "min_hfd", find.min_hfd, f64);
    override_field!(d, "max_hfd", find.max_hfd, f64);
    override_field!(d, "max_adu", find.max_adu, u32);
    override_field!(d, "pedestal", find.pedestal, u16);
    override_field!(d, "bits_per_pixel", find.bits_per_pixel, u32);
    sel.search_region = find.search_region;

    override_field!(d, "af_min_snr", sel.af_min_snr, f64);
    override_field!(d, "extra_edge_allowance", sel.extra_edge_allowance, i32);
    override_field!(d, "max_stars", sel.max_stars, usize);

    Ok((find, sel))
}

/// Full-frame guide-star search (dossier §2; [`auto_find`] +
/// [`saturation_threshold`]). Mirrors [`detect_and_measure`]'s calling
/// convention: `frame` is a numpy `uint16` `(H,W)` array, viewed read-only
/// and zero-copy; `params` mirrors the union of `FindParams`'s and
/// `SelectParams`'s field names (snake case); `None` selects the shipped
/// defaults (GIL released for the heavy scan).
///
/// Returns `(stars, meta)`: each star is `{x, y, snr, mass, hfd, peak}`
/// (pixels; `peak` is the raw peak ADU seen in the search window),
/// brightest-first; `meta` is `{sat_thresh}` (the near-saturation ADU cutoff
/// a primary-star selection pass would use).
///
/// **Multi-star (dossier §2.6; P3-T1)**: when `params["max_stars"]` is `> 1`,
/// `stars` is the SNR-gated, 25px-deduped multi-star candidate list
/// [`auto_find`] builds instead of the unfiltered single-star list — see
/// `astro_guide::select`'s module doc for exactly what that gate excludes.
/// A UI overlay can render `stars[0]` as the presumptive primary and the
/// rest as candidate secondaries (final primary/secondary assignment is a
/// `GuideEngine::process` acquisition-frame concern, not this function's —
/// see [`GuideEngine::stats`]'s `"secondaries"` key for the CURRENTLY
/// TRACKED set during live guiding).
#[pyfunction]
#[pyo3(signature = (frame, params=None))]
fn guide_star_find<'py>(
    py: Python<'py>,
    frame: PyReadonlyArray2<'py, u16>,
    params: Option<Bound<'py, PyDict>>,
) -> PyResult<(Bound<'py, PyList>, Bound<'py, PyDict>)> {
    let shape = frame.shape();
    if shape.len() != 2 {
        return Err(PyValueError::new_err("frame must be a 2D array"));
    }
    let height = shape[0];
    let width = shape[1];
    let slice = frame
        .as_slice()
        .map_err(|e| PyValueError::new_err(format!("frame must be C-contiguous: {e}")))?;
    let (find_params, sel_params) = build_guide_search_params(params.as_ref())?;

    // Heavy compute with the GIL released (full-frame PSF scan), mirroring
    // detect_and_measure.
    let (candidates, sat_thresh) = py.allow_threads(|| {
        let gf = astro_star::GrayFrame::new(slice, width, height);
        let cands = auto_find(&gf, &sel_params);
        let peaks: Vec<(i32, i32)> = cands.iter().map(|c| (c.x as i32, c.y as i32)).collect();
        let sat = saturation_threshold(&gf, &peaks, &find_params);
        (cands, sat)
    });

    let stars = PyList::empty_bound(py);
    for c in &candidates {
        let sd = PyDict::new_bound(py);
        sd.set_item("x", c.x)?;
        sd.set_item("y", c.y)?;
        sd.set_item("snr", c.snr)?;
        sd.set_item("mass", c.mass)?;
        sd.set_item("hfd", c.hfd)?;
        sd.set_item("peak", c.peak_val)?;
        stars.append(sd)?;
    }
    let meta = PyDict::new_bound(py);
    meta.set_item("sat_thresh", sat_thresh)?;
    Ok((stars, meta))
}

// `astro_guide::engine::GuideEngine` is `Send` (the `GuideAlgorithm` trait
// carries a `Send` supertrait precisely so boxed algorithm objects — and the
// engine holding them — cross the `py.allow_threads` boundary in
// [`GuideEngine::process`] without any `unsafe`), so it is stored directly in
// the pyclass below; no wrapper newtype is needed.

/// Which [`Action::LockLost`] source a `process()` call just reported (T8
/// obligation, binding per the P1-T7 review): the `Action` enum overloads one
/// variant for three distinct guide failures (star-lost recovery exhausted,
/// a calibration run failing outright, and a settle window timing out —
/// in P1 only [`GuideEngine::dither`] opens a settle window; guiding-start
/// settling arrives with P2's settle params), so [`GuideEngine::process`]
/// tracks which is live and surfaces it as the returned dict's `"reason"`
/// string.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LostReason {
    StarLost,
    CalibrationFailed,
    SettleTimeout,
}

impl LostReason {
    fn label(self) -> &'static str {
        match self {
            LostReason::StarLost => "star_lost",
            LostReason::CalibrationFailed => "calibration_failed",
            LostReason::SettleTimeout => "settle_timeout",
        }
    }
}

fn axis_pulse_to_dict<'py>(py: Python<'py>, p: &AxisPulse) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("dir", direction_label(p.dir))?;
    d.set_item("ms", p.ms)?;
    Ok(d)
}

/// Serialize one [`Action`] plus its (possibly absent) [`LostReason`] into
/// the frozen dict shape: `{"action": ..., "reason": ..., ...action-specific
/// fields...}`. `"reason"` is `None` for every action except `"lock_lost"`,
/// where it is one of `"star_lost"`, `"calibration_failed"`, or
/// `"settle_timeout"` (T8 obligation — see [`LostReason`]).
fn action_to_dict<'py>(
    py: Python<'py>,
    action: &Action,
    reason: Option<LostReason>,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    match action {
        Action::Idle => {
            d.set_item("action", "idle")?;
        }
        Action::Pulse { axis, dir, ms } => {
            d.set_item("action", "pulse")?;
            d.set_item("axis", axis_label(*axis))?;
            d.set_item("dir", direction_label(*dir))?;
            d.set_item("ms", *ms)?;
        }
        Action::PulsePair { ra, dec } => {
            d.set_item("action", "pulse_pair")?;
            match ra {
                Some(p) => d.set_item("ra", axis_pulse_to_dict(py, p)?)?,
                None => d.set_item("ra", py.None())?,
            }
            match dec {
                Some(p) => d.set_item("dec", axis_pulse_to_dict(py, p)?)?,
                None => d.set_item("dec", py.None())?,
            }
        }
        Action::CalStep { leg, dir, ms } => {
            d.set_item("action", "cal_step")?;
            d.set_item("leg", cal_leg_label(*leg))?;
            d.set_item("dir", direction_label(*dir))?;
            d.set_item("ms", *ms)?;
        }
        Action::Settle => {
            d.set_item("action", "settle")?;
        }
        Action::LockLost => {
            d.set_item("action", "lock_lost")?;
        }
    }
    match reason {
        Some(r) => d.set_item("reason", r.label())?,
        None => d.set_item("reason", py.None())?,
    }
    Ok(d)
}

/// The autoguider engine (dossier §7 composition). Construct with a `config`
/// dict (snake_case, mirrors [`EngineConfig`]; missing keys take the dossier
/// §15 defaults — see [`build_engine_config`]), drive it with
/// [`GuideEngine::process`] once per exposed guide frame.
///
/// `process()`'s returned dict's `"reason"` key (T8 obligation, binding per
/// the P1-T7 review) is `None` except when `"action"` is `"lock_lost"`,
/// where it is `"star_lost"` (recovery exhausted — re-find the star),
/// `"calibration_failed"` (the calibration state machine gave up —
/// recalibrate), or `"settle_timeout"` (a settle window blew its deadline —
/// the star itself was never actually lost; only [`dither`](Self::dither)
/// opens a settle window in this version, so this reason can only follow a
/// dither — guiding-start settling is a possible future task). This class
/// tracks which of the three `LockLost` sources is live via its own
/// calibrating/settling shadow of the engine's phase, and every phase
/// transition happens through this class's own methods
/// ([`begin_calibration`](Self::begin_calibration),
/// [`begin_guiding`](Self::begin_guiding), [`dither`](Self::dither)). The
/// settling half of the shadow additionally cross-checks the engine's own
/// [`is_settling`](astro_guide::engine::GuideEngine::is_settling) on every
/// non-`LockLost` frame (P2-T1 punch-list #3) rather than inferring "the
/// window closed" from the frame's `Action` shape alone — a fast-recenter
/// frame (dossier §11.2) returns an ordinary `pulse_pair` while the window
/// stays open, which a pure shape-based shadow would misclassify.
#[pyclass]
struct GuideEngine {
    inner: astro_guide::engine::GuideEngine,
    calibrating: bool,
    settling: bool,
}

#[pymethods]
impl GuideEngine {
    #[new]
    fn new(config: Bound<'_, PyDict>) -> PyResult<Self> {
        let cfg = build_engine_config(&config)?;
        Ok(GuideEngine {
            inner: astro_guide::engine::GuideEngine::new(cfg),
            calibrating: false,
            settling: false,
        })
    }

    /// Begin measuring a fresh calibration starting from the primary star's
    /// camera-frame `(x, y)` position (dossier §8.2). Subsequent
    /// [`process`](Self::process) calls return `"cal_step"` actions until the
    /// state machine completes (or fails), at which point guiding begins (or
    /// the engine idles — see the `"calibration_failed"` reason).
    fn begin_calibration(&mut self, x: f64, y: f64) {
        self.inner.begin_calibration((x, y));
        self.calibrating = true;
        self.settling = false;
    }

    /// Begin guiding with the current calibration (from
    /// [`load_calibration`](Self::load_calibration) or a prior
    /// [`begin_calibration`](Self::begin_calibration) run). Raises
    /// `ValueError` instead of the underlying engine's panic when no valid
    /// calibration is present.
    fn begin_guiding(&mut self) -> PyResult<()> {
        let valid = self
            .inner
            .calibration()
            .map(|c| c.is_valid)
            .unwrap_or(false);
        if !valid {
            return Err(PyValueError::new_err(
                "begin_guiding requires a valid calibration (load_calibration, or a completed begin_calibration, first)",
            ));
        }
        self.inner.begin_guiding();
        self.calibrating = false;
        self.settling = false;
        Ok(())
    }

    /// Inject the live scope pointing (dossier §9 item 6 / calibration-
    /// complete OBLIGATION (e)): declination and rotator angle in radians;
    /// `pier_side` (`"east"`/`"west"`/`"unknown"`) and `ra_parity`/
    /// `dec_parity` (`"even"`/`"odd"`/`"unknown"`); camera binning factor.
    /// Used live for RA dec-compensation and patched onto the next completed
    /// calibration's sentinel fields. See the P1-T8 report's host-contract
    /// notes on why this is exposed beyond the frozen method list.
    #[pyo3(signature = (declination, pier_side, ra_parity, dec_parity, rotator_angle, binning))]
    fn set_scope_pointing(
        &mut self,
        declination: f64,
        pier_side: &str,
        ra_parity: &str,
        dec_parity: &str,
        rotator_angle: f64,
        binning: u16,
    ) -> PyResult<()> {
        self.inner.set_scope_pointing(ScopePointing {
            declination,
            pier_side: parse_pier_side(pier_side)?,
            ra_parity: parse_parity(ra_parity)?,
            dec_parity: parse_parity(dec_parity)?,
            rotator_angle,
            binning,
        });
        Ok(())
    }

    /// Post-calibration sanity advisories from the last completed calibration
    /// (dossier §8.3); empty until one completes. Distinct from the
    /// `"calibration_failed"` `process()` reason — these are quality
    /// warnings on a calibration that still succeeded.
    fn calibration_advisories(&self) -> Vec<String> {
        self.inner.calibration_advisories().to_vec()
    }

    /// Run one guide frame: `measure` (locate/measure the guide star) then
    /// `ingest` (the per-frame decision), composed as a single GIL-released
    /// step. `frame` is a numpy `uint16` `(H,W)` array; `timestamp_s` is the
    /// frame's wall-clock timestamp (seconds, host clock); `exposure_s` is
    /// its exposure duration (seconds).
    ///
    /// Returns the serialized [`Action`] — see [`action_to_dict`] for the
    /// exact shape, and the class doc comment for the `"reason"` field.
    #[pyo3(signature = (frame, timestamp_s, exposure_s))]
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        frame: PyReadonlyArray2<'py, u16>,
        timestamp_s: f64,
        exposure_s: f64,
    ) -> PyResult<Bound<'py, PyDict>> {
        let shape = frame.shape();
        if shape.len() != 2 {
            return Err(PyValueError::new_err("frame must be a 2D array"));
        }
        let height = shape[0];
        let width = shape[1];
        let slice = frame
            .as_slice()
            .map_err(|e| PyValueError::new_err(format!("frame must be C-contiguous: {e}")))?;
        let meta = FrameMeta {
            timestamp_s,
            exposure_s,
        };

        // Composition per the plan's ambiguity resolution #1: measure() (the
        // frame scan — the expensive part, GIL released like
        // detect_and_measure) then ingest() (the cheap per-frame decision)
        // run as one atomic step under one GIL release.
        let engine = &mut self.inner;
        let action = py.allow_threads(move || {
            let gf = astro_star::GrayFrame::new(slice, width, height);
            let measured = engine.measure(&gf);
            engine.ingest(&meta, &measured)
        });

        let reason = self.classify_lock_lost(&action);
        action_to_dict(py, &action, reason)
    }

    /// Current guide-error statistics in the host's `GuideStats` bus shape
    /// (spec §3.2/§3.5): `{guiding, rms_ra, rms_dec, rms_total, snr,
    /// recent:[[t,ra,dec],...], secondaries:[[x,y],...]}`.
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let s = self.inner.stats();
        let d = PyDict::new_bound(py);
        d.set_item("guiding", s.guiding)?;
        // P2-T1 Produces line: the authoritative settle-window state, valid
        // for every settling frame including fast-recenter frames (dossier
        // §11.2), whose Action is an ordinary "pulse_pair" and so cannot be
        // told apart from normal guiding by the "action" key alone. See
        // `classify_lock_lost` below for why the host wiring needs this.
        d.set_item("settling", s.settling)?;
        d.set_item("rms_ra", s.rms_ra)?;
        d.set_item("rms_dec", s.rms_dec)?;
        d.set_item("rms_total", s.rms_total)?;
        d.set_item("snr", s.snr)?;
        let recent = PyList::empty_bound(py);
        for &(t, ra, dec) in &s.recent {
            recent.append(PyList::new_bound(py, [t, ra, dec]))?;
        }
        d.set_item("recent", recent)?;
        // Multi-star (dossier §2.6/§4; P3-T1): currently tracked secondary
        // guide stars' last-known camera-frame positions, for a UI
        // overlay. Empty in single-star mode.
        let secondaries = PyList::empty_bound(py);
        for &(x, y) in &s.secondaries {
            secondaries.append(PyList::new_bound(py, [x, y]))?;
        }
        d.set_item("secondaries", secondaries)?;
        Ok(d)
    }

    /// Dither by a mount-frame `(dx, dy)` offset (px): shifts the lock,
    /// resets the axis algorithms, and opens a settle window overlaid with a
    /// fast recenter (dossier §11.2/§12). While the window is open,
    /// [`process`](Self::process) returns fast-recenter `"pulse_pair"`
    /// actions, then keeps GUIDING through the settle dwell (P2-T1 fix
    /// round; guider.cpp:1517-1521 — settle is a parallel monitor, not a
    /// phase that suspends guiding): ordinary `"pulse_pair"` corrections
    /// keep flowing, with `"settle"` standing in for frames whose
    /// correction is empty. Dispatch every pulse like any other — the host
    /// does not need to distinguish them; `stats()["settling"]` is `true`
    /// for the whole window regardless of which action a given frame
    /// carries.
    fn dither(&mut self, dx: f64, dy: f64) {
        self.inner.dither(dx, dy);
        self.settling = true;
    }

    /// Adjust the stored calibration for a meridian flip (dossier §9 item 4).
    /// Returns `false` (no-op) when there is no valid calibration. Host
    /// contract: auto-flip at guiding-start is a HOST responsibility (the
    /// host compares the mount's current pier side against the stored
    /// calibration's — via [`dump_calibration`](Self::dump_calibration)'s
    /// `"pier_side"` — and calls this when they differ); the engine never
    /// flips on its own. See the P1-T8 report's host-contract notes.
    fn flip_calibration(&mut self, requires_dec_flip: bool) -> bool {
        self.inner.flip_calibration(requires_dec_flip)
    }

    /// The current calibration as a dict (see [`cal_to_dict`] for the exact
    /// shape, including `"pier_side"`), or `None` if no calibration is
    /// stored. Serializable for persistence across sessions.
    fn dump_calibration<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        match self.inner.calibration() {
            Some(cal) => Ok(cal_to_dict(py, &cal)?.into()),
            None => Ok(py.None()),
        }
    }

    /// Install a calibration dict (e.g. one persisted from a prior session or
    /// round-tripped from [`dump_calibration`](Self::dump_calibration)).
    /// Stored verbatim; follow with [`begin_guiding`](Self::begin_guiding).
    fn load_calibration(&mut self, cal: Bound<'_, PyDict>) -> PyResult<()> {
        let c = dict_to_cal(&cal)?;
        self.inner.set_calibration(c);
        Ok(())
    }

    /// Dump the RA algorithm's trained GP window (A5, dossier §6.8.6) for
    /// cross-session persistence: a list of `[timestamp, measurement,
    /// variance, control]` rows — completed measurements only, the trailing
    /// pending point excluded (amended spec §3-A5). Empty for a non-PPEC RA
    /// algorithm or an untrained model. Serializable beside the calibration.
    fn dump_gp_window<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty_bound(py);
        for (t, m, v, c) in self.inner.dump_gp_window() {
            out.append(PyList::new_bound(py, [t, m, v, c]))?;
        }
        Ok(out)
    }

    /// Restore a persisted GP window (from `dump_gp_window`) into the RA
    /// algorithm, applying the upstream `GuidingStarted` retain-or-reset gate
    /// (A5, amended spec §3-A5; dossier §6.8.6): the ENTIRE window is
    /// restored — its gear clock advanced by `downtime_s`, the wall seconds
    /// between dump and restore — iff the downtime is within
    /// `retain_max_pct_period`% (default 40) of one period; otherwise the
    /// model stays fresh. Returns True iff restored (False for a non-PPEC RA
    /// algorithm, a too-long or negative downtime, or a < 2-point window).
    fn restore_gp_window(&mut self, points: Vec<(f64, f64, f64, f64)>, downtime_s: f64) -> bool {
        self.inner.restore_gp_window(&points, downtime_s)
    }
}

// Plain (non-`#[pymethods]`) impl block: `classify_lock_lost` takes `&Action`,
// which has no `FromPyObject`/`IntoPy` conversion and so cannot itself be a
// `#[pymethods]` entry — it is a Rust-only helper `process()` calls.
impl GuideEngine {
    /// Update the calibrating/settling shadow from this frame's [`Action`]
    /// and report which `LockLost` source (if any) just fired (T8
    /// obligation). See the class doc comment.
    fn classify_lock_lost(&mut self, action: &Action) -> Option<LostReason> {
        if self.calibrating {
            return match action {
                Action::CalStep { .. } => None,
                Action::Idle => {
                    if self
                        .inner
                        .calibration()
                        .map(|c| c.is_valid)
                        .unwrap_or(false)
                    {
                        self.calibrating = false; // completed -> guiding
                    }
                    None
                }
                Action::LockLost => {
                    self.calibrating = false;
                    Some(LostReason::CalibrationFailed)
                }
                _ => None,
            };
        }
        if self.settling {
            return match action {
                Action::LockLost => {
                    self.settling = false;
                    Some(LostReason::SettleTimeout)
                }
                _ => {
                    // P2-T1 punch-list #3: do NOT infer "the window closed"
                    // from this frame's Action shape alone — fast-recenter
                    // frames (dossier §11.2) AND the dwell's ordinary guide
                    // corrections (P2-T1 fix round; guider.cpp:1517-1521)
                    // both return an ordinary `Action::PulsePair` while the
                    // settle window stays open, which the old
                    // `Action::Settle => None, _ => settled` toggle would
                    // have misread as "settled" on the very first such
                    // pulse. Ask the engine directly: it already cleared
                    // its own `settle` field (Done or Failed) exactly when
                    // the window really closed, so `is_settling()` is
                    // authoritative here.
                    self.settling = self.inner.is_settling();
                    None
                }
            };
        }
        match action {
            Action::LockLost => Some(LostReason::StarLost),
            _ => None,
        }
    }
}

// --------------------------------------------------------------------------
// module init
// --------------------------------------------------------------------------

/// Python module entry point (`import astrodeck_native`).
#[pymodule]
fn astrodeck_native(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(detect_and_measure, m)?)?;
    m.add_function(wrap_pyfunction!(fit_focus_curve, m)?)?;
    m.add_function(wrap_pyfunction!(guide_star_find, m)?)?;
    m.add_function(wrap_pyfunction!(tppa_from_three, m)?)?;
    m.add_function(wrap_pyfunction!(tppa_update, m)?)?;
    m.add_class::<FocusSweep>()?;
    m.add_class::<GuideEngine>()?;
    Ok(())
}
