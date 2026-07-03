// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: property test for the hyperbolic solver from
// docs/native-parity/algorithms/nina-autofocus.md (§6.3). Uses a small seeded
// LCG for noise (no rand dependency; deterministic).

//! Over 200 seeded trials, a noisy true-hyperbola V-curve must recover its
//! minimum position to within one focuser step.

use astro_focus::hyperbolic;
use astro_focus::point::FocusPoint;

/// Deterministic 64-bit LCG (Knuth MMIX constants) yielding f64 in `[0, 1)`.
struct Lcg(u64);
impl Lcg {
    fn new(seed: u64) -> Self {
        Lcg(seed ^ 0x9E37_79B9_7F4A_7C15)
    }
    fn next_f64(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        // Use the top 53 bits for a uniform double.
        ((self.0 >> 11) as f64) / ((1u64 << 53) as f64)
    }
    /// Centered noise in `[-mag, mag)`.
    fn noise(&mut self, mag: f64) -> f64 {
        (self.next_f64() * 2.0 - 1.0) * mag
    }
}

/// True hyperbola `a * sqrt(1 + (p - x)^2 / b^2)`.
fn hyperbola(x: f64, p: f64, a: f64, b: f64) -> f64 {
    let dx = p - x;
    a * (1.0 + dx * dx / (b * b)).sqrt()
}

#[test]
fn noisy_v_curves_recover_minimum_within_one_step() {
    let step = 100.0_f64;
    let trials = 200;
    let mut rng = Lcg::new(0xA5F0_1234_DEAD_BEEF);
    let mut worst = 0.0_f64;

    for _ in 0..trials {
        // Random ground truth within realistic ranges.
        let p_true = 4000.0 + (rng.next_f64() * 4000.0).round(); // 4000..8000
        let a = 1.5 + rng.next_f64() * 1.5; // min HFR 1.5..3.0
        let b = (1.5 + rng.next_f64() * 1.5) * step; // curvature 150..300 steps

        // 9-point sweep bracketing the true minimum, at whole steps.
        let base = (p_true / step).round() * step;
        let points: Vec<FocusPoint> = (-4..=4)
            .map(|k| {
                let x = base + k as f64 * step;
                let clean = hyperbola(x, p_true, a, b);
                // ~3% multiplicative noise on HFR.
                let y = (clean + rng.noise(0.03 * clean)).max(0.1);
                FocusPoint::new(x, y, 1.0)
            })
            .collect();

        let fit = hyperbolic::fit(&points);
        assert!(fit.fitted, "fit should succeed for p={p_true}");
        let err = (fit.p - p_true).abs();
        worst = worst.max(err);
        assert!(
            err <= step,
            "recovered p={} vs true {} (err {} > {} step)",
            fit.p,
            p_true,
            err,
            step
        );
    }
    // Sanity: typically far better than one step.
    assert!(worst <= step, "worst-case error {worst} within one step");
}
