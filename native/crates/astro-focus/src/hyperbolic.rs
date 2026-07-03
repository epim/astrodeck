// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§6.3 hyperbolic model,
// y>=0.1 filter, initial guess incl. mirror rule, degenerate guards,
// shrinking-grid solver, scaled-error objective, UNWEIGHTED R^2). No code
// copied from NINA.

//! NINA's default focus fit: a hyperbola `y = a * sqrt(1 + (p - x)^2 / b^2)`
//! solved by a deterministic shrinking-grid search (dossier §6.3).
//!
//! `a` is the minimum HFR at perfect focus, `b` is the shape parameter, and
//! `p` is the focuser position of the minimum. Positions are focuser steps;
//! `y` is HFR in pixels. Only points with `y >= 0.1` participate.

use crate::point::FocusPoint;

/// A hyperbolic fit result. When the data is degenerate the fit is unfit:
/// `fitted == false`, `minimum == (0, 0)`, `r_squared == 0`.
#[derive(Debug, Clone, PartialEq)]
pub struct HyperbolicFit {
    /// Whether a curve was fit (`false` for the degenerate no-fit result).
    pub fitted: bool,
    /// Minimum HFR at perfect focus.
    pub a: f64,
    /// Shape parameter (asymptote slopes are `±a/b`).
    pub b: f64,
    /// Focuser position of the minimum.
    pub p: f64,
    /// Minimum `(round(p) as i32, a)` (dossier §6.3).
    pub minimum: (i32, f64),
    /// Unweighted coefficient of determination over the `y >= 0.1` points.
    pub r_squared: f64,
}

impl HyperbolicFit {
    fn unfit() -> Self {
        HyperbolicFit {
            fitted: false,
            a: 0.0,
            b: 0.0,
            p: 0.0,
            minimum: (0, 0.0),
            r_squared: 0.0,
        }
    }

    /// Evaluate the fitted hyperbola at position `x`.
    pub fn value_at(&self, x: f64) -> f64 {
        model(x, self.p, self.a, self.b)
    }
}

/// The hyperbola model `y = a * sqrt(1 + (p - x)^2 / b^2)` (dossier §6.3).
fn model(x: f64, p: f64, a: f64, b: f64) -> f64 {
    let dx = p - x;
    a * (1.0 + (dx * dx) / (b * b)).sqrt()
}

/// Weighted "scaled RMS" objective — actually a root-of-sum, no `÷ n`
/// (dossier §6.3): `E = sqrt( Σ ((model_i - y_i) / err_i)^2 )`.
fn scaled_error(pts: &[(f64, f64, f64)], p: f64, a: f64, b: f64) -> f64 {
    let mut acc = 0.0;
    for &(x, y, err) in pts {
        let r = (model(x, p, a, b) - y) / err;
        acc += r * r;
    }
    acc.sqrt()
}

/// Fit a hyperbola through `points`. Only points with `value >= 0.1`
/// participate; degenerate data yields the unfit result (dossier §6.3).
pub fn fit(points: &[FocusPoint]) -> HyperbolicFit {
    // y >= 0.1 filter.
    let pts: Vec<(f64, f64, f64)> = points
        .iter()
        .filter(|p| p.value >= 0.1)
        .map(|p| (p.position, p.value, p.error))
        .collect();
    if pts.is_empty() {
        return HyperbolicFit::unfit();
    }

    // Lowest / highest by y; on ties the LAST in list order wins.
    let mut lowest = pts[0];
    let mut highest = pts[0];
    for &q in &pts {
        if q.1 <= lowest.1 {
            lowest = q;
        }
        if q.1 >= highest.1 {
            highest = q;
        }
    }

    // Mirror the highest point to the right of the lowest if needed.
    let mut highest_x = highest.0;
    if highest_x < lowest.0 {
        highest_x = 2.0 * lowest.0 - highest_x;
    }

    let a0 = lowest.1;
    let denom = highest.1 * highest.1 - a0 * a0;
    let dx = highest_x - lowest.0;
    let b0 = ((dx * dx) * a0 * a0 / denom).sqrt();
    let p0 = lowest.0;
    let p_range0 = highest_x - lowest.0;

    // Degenerate-data guard (dossier §6.3): a0/b0 NaN or 0, or p_range 0.
    if a0.is_nan() || a0 == 0.0 || b0.is_nan() || b0 == 0.0 || p_range0 == 0.0 {
        return HyperbolicFit::unfit();
    }

    // Shrinking-grid search: 21 values per parameter, ranges halve per cycle.
    let (mut a, mut b, mut p) = (a0, b0, p0);
    let (mut a_rng, mut b_rng, mut p_rng) = (a0, b0, p_range0);
    let mut lowest_err = f64::MAX;
    let mut old_err = f64::MAX;
    let mut cycles = 0;
    loop {
        let (pc, ac, bc) = (p, a, b);
        a_rng *= 0.5;
        b_rng *= 0.5;
        p_rng *= 0.5;
        let (p_step, a_step, b_step) = (p_rng * 0.1, a_rng * 0.1, b_rng * 0.1);
        for i in 0..=20u32 {
            let p1 = pc - p_rng + i as f64 * p_step;
            for j in 0..=20u32 {
                let a1 = ac - a_rng + j as f64 * a_step;
                for k in 0..=20u32 {
                    let b1 = bc - b_rng + k as f64 * b_step;
                    let e = scaled_error(&pts, p1, a1, b1);
                    if e < lowest_err {
                        old_err = lowest_err;
                        lowest_err = e;
                        a = a1;
                        b = b1;
                        p = p1;
                    }
                }
            }
        }
        cycles += 1;
        if !(old_err - lowest_err >= 1e-4 && lowest_err > 1e-4 && cycles < 30) {
            break;
        }
    }

    // Unweighted R^2 over the y >= 0.1 points (weights commented out in source).
    let mean_y = pts.iter().map(|&(_, y, _)| y).sum::<f64>() / pts.len() as f64;
    let mut ss_res = 0.0;
    let mut ss_tot = 0.0;
    for &(x, y, _) in &pts {
        let r = y - model(x, p, a, b);
        ss_res += r * r;
        let d = y - mean_y;
        ss_tot += d * d;
    }
    let r_squared = if ss_tot == 0.0 {
        1.0
    } else {
        1.0 - ss_res / ss_tot
    };

    HyperbolicFit {
        fitted: true,
        a,
        b,
        p,
        minimum: (p.round_ties_even() as i32, a),
        r_squared,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pts(v: &[(f64, f64)]) -> Vec<FocusPoint> {
        v.iter().map(|&(x, y)| FocusPoint::new(x, y, 1.0)).collect()
    }

    #[test]
    fn golden_parabola_samples() {
        // (x-5)^2 + 2 at x = 1..=9 -> hyperbola minimum X ~ 5, Y ~ 1.2.
        let points: Vec<FocusPoint> = (1..=9)
            .map(|x| {
                let xf = x as f64;
                FocusPoint::new(xf, (xf - 5.0) * (xf - 5.0) + 2.0, 1.0)
            })
            .collect();
        let f = fit(&points);
        assert!(f.fitted);
        assert_eq!(f.minimum.0, 5);
        assert!((f.minimum.1 - 1.2).abs() < 1e-3, "min Y = {}", f.minimum.1);
        // Identity Fitting(min.x) == min.y on symmetric data (p lands on 5.0).
        assert!((f.value_at(f.minimum.0 as f64) - f.minimum.1).abs() < 1e-9);
    }

    #[test]
    fn golden_no_fit_cases() {
        let cases: Vec<Vec<(f64, f64)>> = vec![
            vec![(1000.0, 18.0), (1100.0, 0.0), (1200.0, 0.0)],
            vec![
                (1000.0, 18.0),
                (1000.0, 18.0),
                (1000.0, 18.0),
                (1100.0, 0.0),
                (1200.0, 0.0),
            ],
            vec![
                (900.0, 18.0),
                (1000.0, 18.0),
                (1000.0, 18.0),
                (1100.0, 0.0),
                (1200.0, 0.0),
            ],
            vec![
                (800.0, 18.0),
                (900.0, 0.0),
                (1000.0, 0.0),
                (1000.0, 18.0),
                (1000.0, 18.0),
                (1100.0, 0.0),
                (1200.0, 0.0),
            ],
        ];
        for (i, c) in cases.into_iter().enumerate() {
            let f = fit(&pts(&c));
            assert!(!f.fitted, "case {i} should be unfit");
            assert_eq!(f.minimum, (0, 0.0), "case {i}");
            assert_eq!(f.r_squared, 0.0, "case {i}");
        }
    }
}
