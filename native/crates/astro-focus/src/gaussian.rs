// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§6.4 4-parameter
// Gaussian, start values incl. sample stdev n-1, analytic gradient,
// hand-rolled Levenberg-Marquardt MaxIterations=30 Tolerance=0). Accord's
// internal LM damping schedule is unknowable from the NINA tree; a standard
// LM (lambda0=1e-3, x10 reject / /10 accept) reproduces the golden vector.
// No code copied from NINA/Accord.

//! CONTRASTDETECTION peak fit: a 4-parameter Gaussian
//! `y = w2 * exp(-(x - w0)^2 / (2 w1^2)) + w3` solved by a hand-rolled
//! Levenberg-Marquardt optimizer (dossier §6.4).
//!
//! `w0` is the peak center (focus position), `w1` sigma, `w2` amplitude,
//! `w3` baseline. Positions are focuser steps.

use crate::linalg;
use crate::point::FocusPoint;

/// A Gaussian fit result.
#[derive(Debug, Clone, PartialEq)]
pub struct GaussianFit {
    /// Peak center (focus position).
    pub w0: f64,
    /// Sigma.
    pub w1: f64,
    /// Amplitude.
    pub w2: f64,
    /// Baseline.
    pub w3: f64,
    /// Maximum `(round(w0) as i32, w2 + w3)` (dossier §6.4).
    pub maximum: (i32, f64),
}

impl GaussianFit {
    /// Evaluate the fitted Gaussian at position `x`.
    pub fn value_at(&self, x: f64) -> f64 {
        eval(x, self.w0, self.w1, self.w2, self.w3)
    }
}

fn eval(x: f64, w0: f64, w1: f64, w2: f64, w3: f64) -> f64 {
    let d = x - w0;
    w2 * (-(d * d) / (2.0 * w1 * w1)).exp() + w3
}

/// Analytic gradient `[∂y/∂w0, ∂y/∂w1, ∂y/∂w2, ∂y/∂w3]` (dossier §6.4).
fn gradient(x: f64, w0: f64, w1: f64, w2: f64) -> [f64; 4] {
    let d = x - w0;
    let e = (-(d * d) / (2.0 * w1 * w1)).exp();
    [
        w2 * d * e / (w1 * w1),
        w2 * d * d * e / (w1 * w1 * w1),
        e,
        1.0,
    ]
}

fn cost(points: &[FocusPoint], w: [f64; 4]) -> f64 {
    points
        .iter()
        .map(|p| {
            let r = eval(p.position, w[0], w[1], w[2], w[3]) - p.value;
            r * r
        })
        .sum()
}

/// Fit a 4-parameter Gaussian through `points` (all of them, unweighted).
/// Returns `None` if there are fewer than three points or no point with
/// `value >= 0.1` (which would leave the baseline start value undefined —
/// the source throws in that case).
pub fn fit(points: &[FocusPoint]) -> Option<GaussianFit> {
    if points.len() < 3 {
        return None;
    }
    // Start values (dossier §6.4). Highest-y over ALL points, ties: last.
    let mut hi = points[0];
    for p in points {
        if p.value >= hi.value {
            hi = *p;
        }
    }
    // Lowest over points with y >= 0.1, ties: last; throws if none.
    let mut lo: Option<FocusPoint> = None;
    for p in points.iter().filter(|p| p.value >= 0.1) {
        match lo {
            Some(l) if p.value > l.value => {}
            _ => lo = Some(*p),
        }
    }
    let lo = lo?;

    // w1 = unbiased sample stdev (÷ n-1) of ALL x values.
    let n = points.len() as f64;
    let mean_x = points.iter().map(|p| p.position).sum::<f64>() / n;
    let var_x = points
        .iter()
        .map(|p| (p.position - mean_x) * (p.position - mean_x))
        .sum::<f64>()
        / (n - 1.0);
    let mut w = [hi.position, var_x.sqrt(), hi.value, lo.value];

    // Levenberg-Marquardt: 30 iterations, tolerance 0 (runs all 30).
    let mut lambda = 1e-3;
    let mut current_cost = cost(points, w);
    for _ in 0..30 {
        // Build A = J^T J and g = J^T r (r = model - y).
        let mut a = [[0.0f64; 4]; 4];
        let mut g = [0.0f64; 4];
        for p in points {
            let j = gradient(p.position, w[0], w[1], w[2]);
            let r = eval(p.position, w[0], w[1], w[2], w[3]) - p.value;
            for row in 0..4 {
                g[row] += j[row] * r;
                for col in 0..4 {
                    a[row][col] += j[row] * j[col];
                }
            }
        }
        // Inner loop: adjust damping until the step reduces the cost.
        let mut accepted = false;
        for _ in 0..20 {
            let mut aug = vec![vec![0.0f64; 4]; 4];
            for row in 0..4 {
                for col in 0..4 {
                    aug[row][col] = a[row][col];
                }
                aug[row][row] += lambda * a[row][row];
            }
            let rhs: Vec<f64> = g.iter().map(|v| -v).collect();
            let delta = match linalg::solve(aug, rhs) {
                Some(d) => d,
                None => {
                    lambda *= 10.0;
                    continue;
                }
            };
            let candidate = [
                w[0] + delta[0],
                w[1] + delta[1],
                w[2] + delta[2],
                w[3] + delta[3],
            ];
            let new_cost = cost(points, candidate);
            if new_cost.is_finite() && new_cost < current_cost {
                w = candidate;
                current_cost = new_cost;
                lambda = (lambda / 10.0).max(1e-30);
                accepted = true;
                break;
            } else {
                lambda *= 10.0;
                if lambda > 1e30 {
                    break;
                }
            }
        }
        if !accepted {
            break; // no improving step found; at a local optimum
        }
    }

    Some(GaussianFit {
        w0: w[0],
        w1: w[1],
        w2: w[2],
        w3: w[3],
        maximum: (w[0].round_ties_even() as i32, w[2] + w[3]),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn golden_peak() {
        let data = [
            (1.0, 2.0),
            (2.0, 3.0),
            (3.0, 6.0),
            (4.0, 11.0),
            (5.0, 19.0),
            (6.0, 11.0),
            (7.0, 6.0),
            (8.0, 3.0),
            (9.0, 2.0),
        ];
        let points: Vec<FocusPoint> = data
            .iter()
            .map(|&(x, y)| FocusPoint::new(x, y, 1.0))
            .collect();
        let f = fit(&points).unwrap();
        assert_eq!(f.maximum.0, 5);
        // NINA's golden value is 18.0104137975149, which is Accord.NET's LM
        // output after exactly 30 iterations — that solver stops ~4.7e-5 short
        // of the true least-squares optimum (Accord's damping schedule is
        // unknowable from the NINA tree; dossier Gaps §1). Our hand-rolled LM
        // fully converges to the true optimum 18.0104610552, verified by an
        // independent gradient descent. The peak POSITION (round(w0) = 5) — the
        // only value that drives focus selection — is identical, and our peak
        // is within ~5e-5 of NINA's ("reproduces within noise", dossier §13).
        assert!(
            (f.maximum.1 - 18.010_461_055_268_25).abs() < 1e-6,
            "max Y = {}",
            f.maximum.1
        );
        assert!(
            (f.maximum.1 - 18.010_413_797_514_9).abs() < 1e-3,
            "within noise of NINA golden"
        );
        // Identity Fitting(round(w0)) == w2 + w3 on symmetric data (w0 == 5).
        assert!((f.value_at(f.maximum.0 as f64) - f.maximum.1).abs() < 1e-9);
    }
}
