// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§1 data model, §4.3
// frame averaging, §4.2 no-star sentinel). No code copied from NINA.

//! Focus points, per-frame measurements, and their aggregation.
//!
//! A **focus point** is `(position, value, error)` where `position` is a
//! focuser encoder step (integer stored as `f64`), `value` is the per-point
//! measure (average star HFR in pixels for STARHFR, or a contrast metric),
//! and `error` is the 1-sigma error of `value`, clamped to `>= 0.001` and
//! forced to `1000.0` when the measurement failed (0 stars detected).

/// A single measurement of the focus metric with its 1-sigma error.
///
/// `measure` is HFR in pixels (STARHFR) or the contrast value; `stdev` is the
/// standard deviation of that measure over the detected stars.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MeasureAndError {
    /// The focus metric value (HFR in pixels, or contrast).
    pub measure: f64,
    /// The 1-sigma standard deviation of `measure`.
    pub stdev: f64,
}

impl MeasureAndError {
    /// Construct a measurement.
    pub fn new(measure: f64, stdev: f64) -> Self {
        MeasureAndError { measure, stdev }
    }
}

/// A point on the focus curve: focuser `position` (steps), measured `value`,
/// and its `error` (1-sigma, floored at `0.001`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FocusPoint {
    /// Focuser position in encoder steps (integer value stored as `f64`).
    pub position: f64,
    /// The measured focus metric (HFR in pixels, or contrast).
    pub value: f64,
    /// 1-sigma error of `value`; `>= 0.001`, `1000.0` when no stars.
    pub error: f64,
}

impl FocusPoint {
    /// Build a focus point directly from explicit fields, applying the
    /// no-star sentinel and error floor: if `value == 0` the error is forced
    /// to `1000.0`, then the error is clamped to `>= 0.001` (dossier §4.3).
    pub fn new(position: f64, value: f64, stdev: f64) -> Self {
        let stdev = if value == 0.0 { 1000.0 } else { stdev };
        FocusPoint {
            position,
            value,
            error: stdev.max(0.001),
        }
    }

    /// Build a focus point from a (possibly averaged) measurement at
    /// `position`, applying the no-star sentinel and error floor.
    pub fn from_measurement(position: f64, m: MeasureAndError) -> Self {
        FocusPoint::new(position, m.measure, m.stdev)
    }

    /// Weighted-fit weight `1 / error^2` (dossier §1).
    pub fn weight(&self) -> f64 {
        1.0 / (self.error * self.error)
    }
}

/// Average `n` per-frame measurements into one point measure (dossier §4.3):
/// `measure = (1/n) Σ measure_i`, `stdev = sqrt((1/n) Σ stdev_i^2)` — the
/// RMS of the per-frame stdevs (NOT the standard error of the mean; there is
/// no `÷ n^2`). An empty slice yields `(0, 0)`.
pub fn average_measurements(frames: &[MeasureAndError]) -> MeasureAndError {
    let n = frames.len();
    if n == 0 {
        return MeasureAndError::new(0.0, 0.0);
    }
    let sum: f64 = frames.iter().map(|f| f.measure).sum();
    let sum_var: f64 = frames.iter().map(|f| f.stdev * f.stdev).sum();
    let inv_n = 1.0 / n as f64;
    MeasureAndError::new(sum * inv_n, (sum_var * inv_n).sqrt())
}

/// Insert `p` into a position-ascending list, preserving sort order
/// (`FocusPointComparer` compares only position). Ties are appended after
/// existing equal-position points (dossier §1 sorted insertion).
pub fn insert_sorted(points: &mut Vec<FocusPoint>, p: FocusPoint) {
    let idx = points.partition_point(|q| q.position <= p.position);
    points.insert(idx, p);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn averaging_is_rms_of_stdevs() {
        let frames = [
            MeasureAndError::new(2.0, 0.3),
            MeasureAndError::new(4.0, 0.4),
        ];
        let m = average_measurements(&frames);
        assert!((m.measure - 3.0).abs() < 1e-12);
        // sqrt((0.09 + 0.16)/2) = sqrt(0.125)
        assert!((m.stdev - 0.125_f64.sqrt()).abs() < 1e-12);
    }

    #[test]
    fn no_star_sentinel_and_floor() {
        let p = FocusPoint::from_measurement(1000.0, MeasureAndError::new(0.0, 0.0));
        assert_eq!(p.value, 0.0);
        assert_eq!(p.error, 1000.0);
        let q = FocusPoint::new(1.0, 5.0, 0.0);
        assert_eq!(q.error, 0.001);
    }

    #[test]
    fn sorted_insertion() {
        let mut v = Vec::new();
        for x in [3.0, 1.0, 2.0, 2.0] {
            insert_sorted(&mut v, FocusPoint::new(x, 1.0, 1.0));
        }
        let xs: Vec<f64> = v.iter().map(|p| p.position).collect();
        assert_eq!(xs, vec![1.0, 2.0, 2.0, 3.0]);
    }
}
