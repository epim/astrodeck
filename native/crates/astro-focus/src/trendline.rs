// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§6.1 trendlines: pivot,
// membership band, weighted OLS per side, weighted R^2, intersection). No
// code copied from NINA/Accord.

//! Left/right robust trendline fit and their intersection (dossier §6.1).
//!
//! Positions are focuser steps; values are HFR (pixels) for STARHFR or the
//! contrast metric for CONTRASTDETECTION. Fits are weighted least squares
//! with weight `1 / error^2`.

use crate::point::FocusPoint;

/// A single side's weighted line fit `value = slope * position + offset`.
///
/// When a side has fewer than two points, all three fields are `0.0`
/// (dossier §6.1) — a deliberate sentinel that trips the R^2 gate.
#[derive(Debug, Clone, PartialEq)]
pub struct Trendline {
    /// Slope in value-units per step.
    pub slope: f64,
    /// Intercept in value-units at position 0.
    pub offset: f64,
    /// Weighted coefficient of determination for this side.
    pub r_squared: f64,
    /// The `(position, value)` points that formed this side.
    pub points: Vec<(f64, f64)>,
}

impl Trendline {
    fn empty() -> Self {
        Trendline {
            slope: 0.0,
            offset: 0.0,
            r_squared: 0.0,
            points: Vec::new(),
        }
    }

    /// Evaluate the fitted line at `x`.
    pub fn value_at(&self, x: f64) -> f64 {
        self.slope * x + self.offset
    }
}

/// The full trendline fit: the pivot point, the two side fits, and their
/// intersection.
#[derive(Debug, Clone, PartialEq)]
pub struct TrendlineFit {
    /// Pivot `(position, value)`: the point minimizing `value + error`
    /// (STARHFR) or maximizing `value - error` (CONTRASTDETECTION).
    pub pivot: (f64, f64),
    /// Points strictly left of the pivot and outside the flat-tip band.
    pub left: Trendline,
    /// Points strictly right of the pivot and outside the flat-tip band.
    pub right: Trendline,
    /// Intersection of the two side lines as `(round(x) as i32, y)`;
    /// `(0, 0)` when the sides are parallel (dossier §6.1 sentinel).
    /// Not meaningful for CONTRASTDETECTION (left `(0, 0)`).
    pub intersection: (i32, f64),
}

/// Weighted least-squares fit of `value = slope * x + offset` over `pts`
/// (each `(x, y, weight)`), with the weighted R^2. Fewer than two points
/// yields the `0/0/0` sentinel (dossier §6.1).
fn fit_side(pts: &[(f64, f64, f64)]) -> Trendline {
    if pts.len() < 2 {
        return Trendline::empty();
    }
    let sum_w: f64 = pts.iter().map(|&(_, _, w)| w).sum();
    let mean_x: f64 = pts.iter().map(|&(x, _, w)| w * x).sum::<f64>() / sum_w;
    let mean_y: f64 = pts.iter().map(|&(_, y, w)| w * y).sum::<f64>() / sum_w;
    let sxx: f64 = pts
        .iter()
        .map(|&(x, _, w)| w * (x - mean_x) * (x - mean_x))
        .sum();
    let sxy: f64 = pts
        .iter()
        .map(|&(x, y, w)| w * (x - mean_x) * (y - mean_y))
        .sum();
    let slope = if sxx == 0.0 { 0.0 } else { sxy / sxx };
    let offset = mean_y - slope * mean_x;

    // Weighted R^2 (Accord RSquaredLoss semantics).
    let ss_res: f64 = pts
        .iter()
        .map(|&(x, y, w)| {
            let r = y - (slope * x + offset);
            w * r * r
        })
        .sum();
    let ss_tot: f64 = pts
        .iter()
        .map(|&(_, y, w)| {
            let d = y - mean_y;
            w * d * d
        })
        .sum();
    let r_squared = if ss_tot == 0.0 {
        1.0
    } else {
        1.0 - ss_res / ss_tot
    };

    Trendline {
        slope,
        offset,
        r_squared,
        points: pts.iter().map(|&(x, y, _)| (x, y)).collect(),
    }
}

/// Intersection of two side lines (dossier §6.1). Parallel sides return the
/// `(0, 0)` sentinel; otherwise `x` is rounded to a whole step (banker's
/// rounding) and `y` evaluated on the left line.
fn intersect(left: &Trendline, right: &Trendline) -> (i32, f64) {
    if left.slope == right.slope {
        return (0, 0.0);
    }
    let x = (right.offset - left.offset) / (left.slope - right.slope);
    let y = left.slope * x + left.offset;
    (x.round_ties_even() as i32, y)
}

/// Fit STARHFR trendlines: pivot minimizes `value + error` (ties: last in
/// list order), flat-tip band excludes points within `0.1` of the pivot
/// value, and the intersection is computed (dossier §6.1).
pub fn fit_star_hfr(points: &[FocusPoint]) -> Option<TrendlineFit> {
    if points.is_empty() {
        return None;
    }
    // Pivot: argmin(value + error); on ties the LAST point wins.
    let mut pivot_idx = 0usize;
    let mut best = f64::INFINITY;
    for (i, p) in points.iter().enumerate() {
        let key = p.value + p.error;
        if key <= best {
            best = key;
            pivot_idx = i;
        }
    }
    let pivot = points[pivot_idx];
    let band = pivot.value + 0.1;

    let mut left_pts = Vec::new();
    let mut right_pts = Vec::new();
    for p in points {
        if p.value <= band {
            continue; // flat tip: within 0.1 HFR of the minimum
        }
        let entry = (p.position, p.value, p.weight());
        if p.position < pivot.position {
            left_pts.push(entry);
        } else if p.position > pivot.position {
            right_pts.push(entry);
        }
    }

    let left = fit_side(&left_pts);
    let right = fit_side(&right_pts);
    let intersection = intersect(&left, &right);

    Some(TrendlineFit {
        pivot: (pivot.position, pivot.value),
        left,
        right,
        intersection,
    })
}

/// Fit CONTRASTDETECTION trendlines: pivot maximizes `value - error` (ties:
/// last), band excludes points within `0.01` of the peak value, and no
/// intersection is computed (Gaussian picks focus). Used only for
/// sweep-side counting and the chart (dossier §6.1).
pub fn fit_contrast(points: &[FocusPoint]) -> Option<TrendlineFit> {
    if points.is_empty() {
        return None;
    }
    let mut pivot_idx = 0usize;
    let mut best = f64::NEG_INFINITY;
    for (i, p) in points.iter().enumerate() {
        let key = p.value - p.error;
        if key >= best {
            best = key;
            pivot_idx = i;
        }
    }
    let pivot = points[pivot_idx];
    let band = pivot.value - 0.01;

    let mut left_pts = Vec::new();
    let mut right_pts = Vec::new();
    for p in points {
        if p.value >= band {
            continue;
        }
        let entry = (p.position, p.value, p.weight());
        if p.position < pivot.position {
            left_pts.push(entry);
        } else if p.position > pivot.position {
            right_pts.push(entry);
        }
    }

    Some(TrendlineFit {
        pivot: (pivot.position, pivot.value),
        left: fit_side(&left_pts),
        right: fit_side(&right_pts),
        intersection: (0, 0.0),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pts(v: &[(f64, f64)]) -> Vec<FocusPoint> {
        // Golden vectors use err_y = 1 (weight 1).
        v.iter().map(|&(x, y)| FocusPoint::new(x, y, 1.0)).collect()
    }

    #[test]
    fn golden_v_curve_intersection() {
        let p = pts(&[
            (1.0, 10.0),
            (2.0, 8.0),
            (3.0, 6.0),
            (4.0, 4.0),
            (5.0, 2.0),
            (6.0, 4.0),
            (7.0, 6.0),
            (8.0, 8.0),
            (9.0, 10.0),
        ]);
        let f = fit_star_hfr(&p).unwrap();
        assert_eq!(f.pivot, (5.0, 2.0));
        assert_eq!(f.left.points.len(), 4);
        assert_eq!(f.right.points.len(), 4);
        assert_eq!(f.intersection.0, 5);
        assert!((f.intersection.1 - 2.0).abs() < 1e-12);
    }

    #[test]
    fn golden_flat_tip() {
        let p = pts(&[
            (1.0, 10.0),
            (2.0, 8.0),
            (3.0, 6.0),
            (4.0, 4.0),
            (5.0, 2.1),
            (6.0, 2.0),
            (7.0, 2.1),
            (8.0, 4.0),
            (9.0, 6.0),
            (10.0, 8.0),
            (11.0, 10.0),
        ]);
        let f = fit_star_hfr(&p).unwrap();
        assert_eq!(f.pivot, (6.0, 2.0));
        // 2.1 points are excluded (2.1 not > 2.0 + 0.1).
        assert_eq!(f.left.points.len(), 4);
        assert_eq!(f.right.points.len(), 4);
        assert_eq!(f.intersection.0, 6);
        assert!((f.intersection.1 - 0.0).abs() < 1e-12);
    }
}
