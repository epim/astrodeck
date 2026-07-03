// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: PyO3 binding layer over astro-star / astro-focus / astro-tppa,
// which are reimplemented from the audited algorithm dossiers in
// docs/native-parity/algorithms/. See those crates' lib.rs for per-crate
// provenance detail. This file adds no algorithm math of its own; it only
// marshals values across the Python boundary per the architecture spec
// docs/superpowers/specs/2026-07-03-native-parity-architecture-design.md §4.4/§5.

//! `astrodeck_native`: PyO3 extension module bundling `astro-star`,
//! `astro-focus`, and `astro-tppa` for use from the AstroDeck Python backend.
//!
//! Built with maturin as an abi3 (cp311+) wheel. The public Python surface is:
//! - [`detect_and_measure`] — star detection + measurement over a numpy frame.
//! - [`fit_focus_curve`] — one-shot focus-curve fit.
//! - [`FocusSweep`] — the autofocus sweep state machine.
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
// module init
// --------------------------------------------------------------------------

/// Python module entry point (`import astrodeck_native`).
#[pymodule]
fn astrodeck_native(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(detect_and_measure, m)?)?;
    m.add_function(wrap_pyfunction!(fit_focus_curve, m)?)?;
    m.add_function(wrap_pyfunction!(tppa_from_three, m)?)?;
    m.add_function(wrap_pyfunction!(tppa_update, m)?)?;
    m.add_class::<FocusSweep>()?;
    Ok(())
}
