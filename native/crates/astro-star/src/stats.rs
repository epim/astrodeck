// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§Conventions,
// §5.1, §8.2). No code copied from NINA/Hocus Focus.

//! Median / MAD helpers with the dossier's two distinct median conventions.
//!
//! - **Upper median** = `sorted[count >> 1]` (dossier §Conventions) — used by
//!   the annulus background, adaptive grid, plane-fit residual scale, flux
//!   `star_median` sort sites.
//! - **True median** = average of the two middle elements for even counts —
//!   used by the annulus MAD σ (§5.1) and the frame MAD dispersion (§8.2).

/// Upper median of an unsorted slice, `sorted[count >> 1]` (dossier §Conventions).
/// Panics on empty input.
pub fn upper_median(values: &mut [f64]) -> f64 {
    assert!(!values.is_empty());
    values.sort_by(|a, b| a.total_cmp(b));
    values[values.len() >> 1]
}

/// Upper median of an already-sorted slice.
pub fn upper_median_sorted(sorted: &[f64]) -> f64 {
    sorted[sorted.len() >> 1]
}

/// True median (average of the two middles for even counts) of an unsorted
/// slice (dossier §5.1 `ComputeMedian`). Panics on empty input.
pub fn true_median(values: &mut [f64]) -> f64 {
    assert!(!values.is_empty());
    values.sort_by(|a, b| a.total_cmp(b));
    true_median_sorted(values)
}

/// True median of an already-sorted slice.
pub fn true_median_sorted(sorted: &[f64]) -> f64 {
    let n = sorted.len();
    if n % 2 == 1 {
        sorted[n / 2]
    } else {
        (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
    }
}

/// `1.4826·upper_median(|v − center|)` — the upper-median MAD→σ used by the
/// plane-fit residual scale and the adaptive grid (dossier §5.2, §3.6).
pub fn mad_sigma_upper(values: &[f64], center: f64) -> f64 {
    let mut dev: Vec<f64> = values.iter().map(|&v| (v - center).abs()).collect();
    1.4826 * upper_median(&mut dev)
}

/// `1.4826·true_median(|v − center|)` — the annulus local-σ convention
/// (dossier §5.1, TRUE median).
pub fn mad_sigma_true(values: &[f64], center: f64) -> f64 {
    let mut dev: Vec<f64> = values.iter().map(|&v| (v - center).abs()).collect();
    1.4826 * true_median(&mut dev)
}

/// `(median, 1.483·MAD)` with the true-median convention (dossier §8.2, §8.5).
pub fn median_mad_1483(values: &[f64]) -> (f64, f64) {
    let mut v = values.to_vec();
    let median = true_median(&mut v);
    let mut dev: Vec<f64> = values.iter().map(|&x| (x - median).abs()).collect();
    let mad = true_median(&mut dev);
    (median, 1.483 * mad)
}
