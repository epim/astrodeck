// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§6.2 weighted degree-2
// least squares, weighted R^2, minimum at round(-b/2a)). No code copied
// from NINA/Accord.

//! Weighted quadratic (parabola) fit `y = a x^2 + b x + c` over all points
//! (dossier §6.2). Weights are `1 / error^2`. Positions are focuser steps.

use crate::linalg;
use crate::point::FocusPoint;

/// A quadratic fit result.
#[derive(Debug, Clone, PartialEq)]
pub struct QuadraticFit {
    /// Leading coefficient `a` of `a x^2 + b x + c`.
    pub a: f64,
    /// Linear coefficient `b`.
    pub b: f64,
    /// Constant coefficient `c`.
    pub c: f64,
    /// Weighted coefficient of determination.
    pub r_squared: f64,
    /// Minimum `(round(-b/2a) as i32, y(round(-b/2a)))` — `y` is evaluated at
    /// the rounded position (dossier §6.2). `None` when `a == 0`.
    pub minimum: Option<(i32, f64)>,
}

impl QuadraticFit {
    /// Evaluate the fitted parabola at `x`.
    pub fn value_at(&self, x: f64) -> f64 {
        (self.a * x + self.b) * x + self.c
    }
}

/// Fit a weighted parabola through `points` (all of them, including zero-Y
/// points whose weight is `1e-6`). Returns `None` if there are fewer than
/// three points or the weighted normal equations are singular.
pub fn fit(points: &[FocusPoint]) -> Option<QuadraticFit> {
    if points.len() < 3 {
        return None;
    }
    // Weighted normal equations (X^T W X) beta = X^T W y with basis
    // [x^2, x, 1]. Accumulate the symmetric 3x3 and the 3-vector rhs.
    let mut sw = [0.0f64; 5]; // Σ w x^k for k = 0..=4
    let mut swy = [0.0f64; 3]; // Σ w x^k y for k = 0..=2
    for p in points {
        let w = p.weight();
        let x = p.position;
        let mut xk = w;
        for s in sw.iter_mut() {
            *s += xk;
            xk *= x;
        }
        let mut xk = w;
        for s in swy.iter_mut() {
            *s += xk * p.value;
            xk *= x;
        }
    }
    // Rows for basis order [x^2, x, 1]:
    // [Σwx^4 Σwx^3 Σwx^2][a]   [Σwx^2 y]
    // [Σwx^3 Σwx^2 Σwx  ][b] = [Σwx   y]
    // [Σwx^2 Σwx   Σw   ][c]   [Σw    y]
    let m = vec![
        vec![sw[4], sw[3], sw[2]],
        vec![sw[3], sw[2], sw[1]],
        vec![sw[2], sw[1], sw[0]],
    ];
    let rhs = vec![swy[2], swy[1], swy[0]];
    let beta = linalg::solve(m, rhs)?;
    let (a, b, c) = (beta[0], beta[1], beta[2]);

    // Weighted R^2.
    let sum_w: f64 = points.iter().map(|p| p.weight()).sum();
    let mean_y: f64 = points.iter().map(|p| p.weight() * p.value).sum::<f64>() / sum_w;
    let mut ss_res = 0.0;
    let mut ss_tot = 0.0;
    for p in points {
        let w = p.weight();
        let pred = (a * p.position + b) * p.position + c;
        let r = p.value - pred;
        ss_res += w * r * r;
        let d = p.value - mean_y;
        ss_tot += w * d * d;
    }
    let r_squared = if ss_tot == 0.0 {
        1.0
    } else {
        1.0 - ss_res / ss_tot
    };

    let minimum = if a == 0.0 {
        None
    } else {
        let x = (-b / (2.0 * a)).round_ties_even();
        let y = (a * x + b) * x + c;
        Some((x as i32, y))
    };

    Some(QuadraticFit {
        a,
        b,
        c,
        r_squared,
        minimum,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn golden_parabola() {
        // (x-5)^2 + 2 sampled at x = 1..=9 -> minimum (5, 2), R^2 == 1.
        let points: Vec<FocusPoint> = (1..=9)
            .map(|x| {
                let xf = x as f64;
                FocusPoint::new(xf, (xf - 5.0) * (xf - 5.0) + 2.0, 1.0)
            })
            .collect();
        let f = fit(&points).unwrap();
        let (mx, my) = f.minimum.unwrap();
        assert_eq!(mx, 5);
        assert!((my - 2.0).abs() < 1e-9);
        // Exact fit -> R^2 == 1 (relaxed to 1e-9 for FP accumulation).
        assert!((f.r_squared - 1.0).abs() < 1e-9);
        // Identity: fit(round(x_min)) == y_min by construction.
        assert!((f.value_at(mx as f64) - my).abs() < 1e-12);
    }
}
