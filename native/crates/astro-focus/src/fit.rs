// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§5 fit recomputation, §6.5
// final-point selection incl. combos, §7.1/§7.2 R^2 gate and bounds check).
// No code copied from NINA.

//! Combined fit bundle, final focus-point selection (dossier §6.5), the
//! validation gates (§7.1 R^2, §7.2 bounds), and the [`FitOutcome`] returned
//! to the host for UI rendering.

use crate::config::{AfMethod, CurveFitting};
use crate::gaussian::{self, GaussianFit};
use crate::hyperbolic::{self, HyperbolicFit};
use crate::point::FocusPoint;
use crate::quadratic::{self, QuadraticFit};
use crate::trendline::{self, TrendlineFit};

/// All four fits computed over the current focus points (dossier §5).
#[derive(Debug, Clone)]
pub struct FocusFits {
    /// Trendline fit (always present when there is at least one point).
    pub trend: Option<TrendlineFit>,
    /// Hyperbolic fit (unfit result when degenerate).
    pub hyperbolic: HyperbolicFit,
    /// Quadratic fit (`None` with fewer than three points).
    pub quadratic: Option<QuadraticFit>,
    /// Gaussian fit (`None` with fewer than three points or no `y >= 0.1`).
    pub gaussian: Option<GaussianFit>,
}

/// Compute all fits for `points` under `method` (dossier §5): trendline uses
/// the STARHFR or CONTRASTDETECTION pivot rule; hyperbolic/quadratic/gaussian
/// are computed unconditionally (NINA recomputes all four before selecting).
pub fn compute_all_fits(points: &[FocusPoint], method: AfMethod) -> FocusFits {
    let trend = match method {
        AfMethod::StarHfr => trendline::fit_star_hfr(points),
        AfMethod::ContrastDetection => trendline::fit_contrast(points),
    };
    FocusFits {
        trend,
        hyperbolic: hyperbolic::fit(points),
        quadratic: quadratic::fit(points),
        gaussian: gaussian::fit(points),
    }
}

/// Select the final focus point `(x, y)` per method and fitting strategy
/// (dossier §6.5). Combo variants average the two candidate positions and
/// round the mean with banker's rounding. Returns `None` when a required fit
/// is unavailable (e.g. fewer than three points for a parabola).
pub fn determine_final_focus_point(
    method: AfMethod,
    fitting: CurveFitting,
    fits: &FocusFits,
) -> Option<(f64, f64)> {
    match method {
        AfMethod::ContrastDetection => {
            let g = fits.gaussian.as_ref()?;
            Some((g.maximum.0 as f64, g.maximum.1))
        }
        AfMethod::StarHfr => {
            let trend = fits.trend.as_ref()?;
            let (tx, ty) = (trend.intersection.0 as f64, trend.intersection.1);
            match fitting {
                CurveFitting::Trendlines => Some((tx, ty)),
                CurveFitting::Hyperbolic => {
                    Some((fits.hyperbolic.minimum.0 as f64, fits.hyperbolic.minimum.1))
                }
                CurveFitting::Parabolic => {
                    let q = fits.quadratic.as_ref()?;
                    let m = q.minimum?;
                    Some((m.0 as f64, m.1))
                }
                CurveFitting::TrendParabolic => {
                    let q = fits.quadratic.as_ref()?;
                    let m = q.minimum?;
                    let x = ((tx + m.0 as f64) / 2.0).round_ties_even();
                    Some((x, (ty + m.1) / 2.0))
                }
                CurveFitting::TrendHyperbolic => {
                    let h = fits.hyperbolic.minimum;
                    let x = ((tx + h.0 as f64) / 2.0).round_ties_even();
                    Some((x, (ty + h.1) / 2.0))
                }
            }
        }
    }
}

/// R^2 gate (dossier §7.1): STARHFR only, and only when `threshold > 0`.
/// Returns `true` when the gate PASSES (fit quality acceptable).
pub fn passes_r_squared_gate(
    method: AfMethod,
    fitting: CurveFitting,
    fits: &FocusFits,
    threshold: f64,
) -> bool {
    if method != AfMethod::StarHfr || threshold <= 0.0 {
        return true;
    }
    // Curve gate.
    let curve_ok = match fitting {
        CurveFitting::Hyperbolic | CurveFitting::TrendHyperbolic => {
            fits.hyperbolic.r_squared >= threshold
        }
        CurveFitting::Parabolic | CurveFitting::TrendParabolic => fits
            .quadratic
            .as_ref()
            .map(|q| q.r_squared >= threshold)
            .unwrap_or(false),
        CurveFitting::Trendlines => true,
    };
    if !curve_ok {
        return false;
    }
    // Trendline gate (applies to all TREND* variants).
    let trend_ok = match fitting {
        CurveFitting::Trendlines | CurveFitting::TrendHyperbolic | CurveFitting::TrendParabolic => {
            match fits.trend.as_ref() {
                Some(t) => t.left.r_squared >= threshold && t.right.r_squared >= threshold,
                None => false,
            }
        }
        _ => true,
    };
    trend_ok
}

/// Bounds check (dossier §7.2): the final `x` must lie within the measured
/// position range. Returns `true` when in bounds.
pub fn within_bounds(points: &[FocusPoint], final_x: f64) -> bool {
    if points.is_empty() {
        return false;
    }
    let min = points
        .iter()
        .map(|p| p.position)
        .fold(f64::INFINITY, f64::min);
    let max = points
        .iter()
        .map(|p| p.position)
        .fold(f64::NEG_INFINITY, f64::max);
    final_x >= min && final_x <= max
}

/// The full result of a completed autofocus run, for host consumption and UI.
///
/// Positions are focuser steps; values are HFR (pixels) for STARHFR or the
/// contrast metric for CONTRASTDETECTION.
#[derive(Debug, Clone)]
pub struct FitOutcome {
    /// Measurement method used.
    pub method: AfMethod,
    /// Fitting strategy used to select the focus point.
    pub fitting: CurveFitting,
    /// Selected focus position (the truncated integer the focuser moves to).
    pub best_position: i32,
    /// Fitted value at the selected position.
    pub best_value: f64,
    /// Hyperbolic R^2 (if a hyperbola was fit).
    pub hyperbolic_r_squared: Option<f64>,
    /// Quadratic R^2 (if a parabola was fit).
    pub quadratic_r_squared: Option<f64>,
    /// Left trendline R^2.
    pub left_trend_r_squared: f64,
    /// Right trendline R^2.
    pub right_trend_r_squared: f64,
    /// Left trendline slope (value-units per step).
    pub left_trend_slope: f64,
    /// Right trendline slope.
    pub right_trend_slope: f64,
    /// Trendline intersection `(x, y)`; `None` when parallel/unavailable.
    pub trend_intersection: Option<(i32, f64)>,
    /// Sampled fitted-curve polyline `(position, value)` for UI rendering.
    pub curve: Vec<(f64, f64)>,
    /// The measured focus points (position-sorted).
    pub points: Vec<FocusPoint>,
}

/// Number of samples in the rendered curve polyline.
const CURVE_SAMPLES: usize = 64;

/// Build the [`FitOutcome`] from computed fits, the selected point, and the
/// truncated move target.
pub fn build_outcome(
    method: AfMethod,
    fitting: CurveFitting,
    fits: &FocusFits,
    best_position: i32,
    best_value: f64,
    points: &[FocusPoint],
) -> FitOutcome {
    let (min_x, max_x) = if points.is_empty() {
        (0.0, 0.0)
    } else {
        (
            points
                .iter()
                .map(|p| p.position)
                .fold(f64::INFINITY, f64::min),
            points
                .iter()
                .map(|p| p.position)
                .fold(f64::NEG_INFINITY, f64::max),
        )
    };
    let curve = sample_curve(method, fitting, fits, min_x, max_x);

    let (lslope, rslope, lr2, rr2, intersection) = match fits.trend.as_ref() {
        Some(t) => {
            let inter = if t.intersection == (0, 0.0) {
                None
            } else {
                Some(t.intersection)
            };
            (
                t.left.slope,
                t.right.slope,
                t.left.r_squared,
                t.right.r_squared,
                inter,
            )
        }
        None => (0.0, 0.0, 0.0, 0.0, None),
    };

    FitOutcome {
        method,
        fitting,
        best_position,
        best_value,
        hyperbolic_r_squared: fits.hyperbolic.fitted.then_some(fits.hyperbolic.r_squared),
        quadratic_r_squared: fits.quadratic.as_ref().map(|q| q.r_squared),
        left_trend_r_squared: lr2,
        right_trend_r_squared: rr2,
        left_trend_slope: lslope,
        right_trend_slope: rslope,
        trend_intersection: intersection,
        curve,
        points: points.to_vec(),
    }
}

/// Sample the primary selection model across `[min_x, max_x]` for the UI.
fn sample_curve(
    method: AfMethod,
    fitting: CurveFitting,
    fits: &FocusFits,
    min_x: f64,
    max_x: f64,
) -> Vec<(f64, f64)> {
    if max_x <= min_x {
        return Vec::new();
    }
    let sample = |f: &dyn Fn(f64) -> f64| -> Vec<(f64, f64)> {
        (0..CURVE_SAMPLES)
            .map(|i| {
                let t = i as f64 / (CURVE_SAMPLES - 1) as f64;
                let x = min_x + t * (max_x - min_x);
                (x, f(x))
            })
            .collect()
    };
    match method {
        AfMethod::ContrastDetection => match fits.gaussian.as_ref() {
            Some(g) => sample(&|x| g.value_at(x)),
            None => Vec::new(),
        },
        AfMethod::StarHfr => match fitting {
            CurveFitting::Hyperbolic | CurveFitting::TrendHyperbolic => {
                if fits.hyperbolic.fitted {
                    let h = &fits.hyperbolic;
                    sample(&|x| h.value_at(x))
                } else {
                    Vec::new()
                }
            }
            CurveFitting::Parabolic | CurveFitting::TrendParabolic => match fits.quadratic.as_ref()
            {
                Some(q) => sample(&|x| q.value_at(x)),
                None => Vec::new(),
            },
            CurveFitting::Trendlines => match fits.trend.as_ref() {
                // Two-segment V through the pivot.
                Some(t) => {
                    let px = t.pivot.0;
                    let mut v = Vec::new();
                    if t.left.points.len() >= 2 {
                        v.push((min_x, t.left.value_at(min_x)));
                        v.push((px, t.left.value_at(px)));
                    }
                    if t.right.points.len() >= 2 {
                        v.push((px, t.right.value_at(px)));
                        v.push((max_x, t.right.value_at(max_x)));
                    }
                    v
                }
                None => Vec::new(),
            },
        },
    }
}
