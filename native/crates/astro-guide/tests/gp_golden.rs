// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's Gaussian-process
// predictive-PEC port (src/algorithms/gp_math.rs, src/algorithms/
// gaussian_process.rs). Derived from the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§6.8.1-§6.8.6) and from
// PHD2's bundled MPI_IS_gaussian_process contribution
// (src/gaussian_process_guider.cpp, src/gaussian_process.cpp,
// src/covariance_functions.cpp, tools/math_tools.cpp; Max Planck Society
// BSD-3-Clause — Klenske, Zeilinger, Schölkopf & Hennig, "Gaussian Process
// Based Predictive Control for Periodic Error Correction," IEEE Transactions
// on Control Systems Technology 24(1):110-121, 2016) and the PHD2 wrapper
// src/guide_algorithm_gaussian_process.cpp (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2 or the MPI-IS
// contribution.
//
// Provenance for the *literal numbers* below: each fixture is derived
// analytically from the dossier §6.8 pseudocode (the by-hand trace the port
// exception prescribes), shown inline per test. Where a test asserts a
// PROPERTY (a non-zero prediction below the deadband), the property — not a
// magic number — is the dossier claim under test (§6.8.3: "the GP prediction
// is always applied").

use astro_guide::algorithms::gp_math::{
    estimate_period_length, regularize, variance_from_snr, Kernel,
};
use astro_guide::algorithms::{GaussianProcessGuider, GpParams, GuideAlgorithm};

// ---------------------------------------------------------------------------
// 1. Covariance kernel (dossier §6.8.4)
// ---------------------------------------------------------------------------

/// At `d = 0` the inference kernel (PeriodicSquareExponential2) equals the sum
/// of the three signal variances: with the §6.8.1 defaults (SE0 amp 20,
/// periodic amp 20, SE1 amp 10) that is 20² + 20² + 10² = 900. The projection
/// kernel (PeriodicSquareExponential, no SE1) equals 20² + 20² = 800.
/// (`covariance_functions.cpp:139-161`; every `exp(0)` term is exactly 1.0.)
#[test]
fn kernel_at_zero_distance_is_sum_of_signal_variances() {
    let k = Kernel::from_params(&GpParams::default());
    let inf = k.eval_pair(5.0, 5.0, true);
    let proj = k.eval_pair(5.0, 5.0, false);
    assert!(
        (inf - 900.0).abs() < 1e-9,
        "inference k(0) = {inf}, expected 900"
    );
    assert!(
        (proj - 800.0).abs() < 1e-9,
        "projection k(0) = {proj}, expected 800"
    );
}

/// At `d = P` (one full period, 200 s) the periodic term returns to its peak,
/// because `sin(π·d/P) = sin(π) = 0` ⇒ `exp(-2·0) = 1` ⇒ contribution = amp²
/// = 400. The full inference kernel there is SE0(200) + 400 + SE1(200):
///   SE0(200) = 400·exp(-0.5·200²/700²) = 400·exp(-0.040816326…)
///   SE1(200) = 100·exp(-0.5·200²/25²)  = 100·exp(-32)  ≈ 1.27e-12
#[test]
fn kernel_periodic_term_returns_to_peak_at_one_period() {
    let k = Kernel::from_params(&GpParams::default());
    let d = 200.0_f64;
    let se0 = 400.0 * (-0.5 * d * d / (700.0 * 700.0)).exp();
    let se1 = 100.0 * (-0.5 * d * d / (25.0 * 25.0)).exp();
    let expected = se0 + 400.0 + se1; // periodic term back at its 400 peak
    let got = k.eval_pair(0.0, 200.0, true);
    assert!(
        (got - expected).abs() < 1e-6,
        "k(P) = {got}, expected {expected} (periodic term must return to its 400 peak)"
    );
    // Sanity: the SE0 shoulder there is ~384.0, so the whole thing is ~784.
    assert!((se0 - 384.00224).abs() < 1e-3, "SE0(200) = {se0}");
}

// ---------------------------------------------------------------------------
// 2. Measurement variance from SNR (dossier §6.8.2)
// ---------------------------------------------------------------------------

/// `variance_from_snr(10)`: sd = 2.1752/(10−3.3) + 0.5 = 0.8246567…,
/// variance = sd² (`gaussian_process_guider.cpp:121-129`).
#[test]
fn variance_from_snr_matches_empirical_formula() {
    let sd: f64 = 2.1752 / (10.0 - 3.3) + 0.5;
    let expected = sd * sd;
    assert!((sd - 0.8246567164179).abs() < 1e-12, "sd = {sd}");
    let got = variance_from_snr(10.0);
    assert!(
        (got - expected).abs() < 1e-15,
        "variance = {got}, expected {expected}"
    );

    // The SNR floor: anything below 3.4 is clamped to 3.4 first.
    let floored = variance_from_snr(1.0);
    let sd_floor = 2.1752 / (3.4 - 3.3) + 0.5;
    assert!(
        (floored - sd_floor * sd_floor).abs() < 1e-9,
        "SNR<3.4 must clamp to 3.4: got {floored}"
    );
}

// ---------------------------------------------------------------------------
// 3. Regularizer (dossier §6.8.4 step 2)
// ---------------------------------------------------------------------------

/// A linear ramp `y = t` sampled at t = 0,5,10,15,20 regularizes onto the 5 s
/// grid to trapezoidal cell means. Hand trace (grid_interval 5, cell centers
/// `last_cell_end + 2.5`, boundary interpolation `proportion = 1` at every
/// on-grid sample):
///   cell0 (center −2.5): boundary artifact, gear 0, var 0.5
///   cell1 (center  2.5): ∫[0,5] of the ramp / 5 = 2.5
///   cell2 (center  7.5): 7.5
///   cell3 (center 12.5): 12.5
///   cell4 (center 17.5): 17.5
/// (`gaussian_process_guider.cpp:686-760`.)
#[test]
fn regularizer_recovers_trapezoidal_cell_means_of_a_ramp() {
    let ts = [0.0, 5.0, 10.0, 15.0, 20.0];
    let ge = [0.0, 5.0, 10.0, 15.0, 20.0];
    let var = [1.0, 1.0, 1.0, 1.0, 1.0];
    let (rt, rg, rv) = regularize(&ts, &ge, &var, 5.0, 2048, false).expect("no overrun");

    let exp_t = [-2.5, 2.5, 7.5, 12.5, 17.5];
    let exp_g = [0.0, 2.5, 7.5, 12.5, 17.5];
    let exp_v = [0.5, 1.0, 1.0, 1.0, 1.0];
    assert_eq!(rt.len(), 5, "expected 5 grid cells, got {}", rt.len());
    for i in 0..5 {
        assert!(
            (rt[i] - exp_t[i]).abs() < 1e-9,
            "ts[{i}] = {}, exp {}",
            rt[i],
            exp_t[i]
        );
        assert!(
            (rg[i] - exp_g[i]).abs() < 1e-9,
            "gear[{i}] = {}, exp {}",
            rg[i],
            exp_g[i]
        );
        assert!(
            (rv[i] - exp_v[i]).abs() < 1e-9,
            "var[{i}] = {}, exp {}",
            rv[i],
            exp_v[i]
        );
    }
}

// ---------------------------------------------------------------------------
// 4. FFT period identification (dossier §6.8.5)
// ---------------------------------------------------------------------------

/// A pure sinusoid of period 200 s sampled on the 5 s grid (200 samples over
/// 1000 s = 5 periods) recovers P ≈ 200 within the 3-point quadratic
/// interpolation tolerance. (`gaussian_process_guider.cpp:582-664`,
/// `math_tools.cpp:174-219`.)
#[test]
fn fft_recovers_the_period_of_a_pure_sinusoid() {
    let n = 200usize;
    let ts: Vec<f64> = (0..n).map(|k| 2.5 + 5.0 * k as f64).collect();
    let data: Vec<f64> = ts
        .iter()
        .map(|&t| (2.0 * std::f64::consts::PI * t / 200.0).sin())
        .collect();
    let p = estimate_period_length(&ts, &data, 4096, 1500.0);
    assert!(
        (p - 200.0).abs() < 5.0,
        "recovered period {p}, expected ~200"
    );
}

// ---------------------------------------------------------------------------
// 5. Controller warm-up + deadband (dossier §6.8.3)
// ---------------------------------------------------------------------------

/// With ≤10 measurements the controller is pure proportional:
/// `result_with = control_gain · input` (0.6·input); below `min_move`
/// (0.2) the reactive term is zeroed. (`gaussian_process_guider.cpp:296-370`,
/// the `get_number_of_measurements() > 10` branch not yet taken.)
#[test]
fn warmup_is_proportional_with_deadband() {
    let mut gp = GaussianProcessGuider::new(GpParams::default());
    // Ten frames of above-deadband error: each is exactly 0.6·input.
    for i in 0..10 {
        let out = gp.result_with(1.0, 10.0, 3.0);
        assert!(
            (out - 0.6).abs() < 1e-12,
            "warm-up frame {i}: got {out}, expected 0.6 (= control_gain·1.0)"
        );
    }

    // A fresh guider: a below-deadband first frame yields exactly 0.
    let mut gp2 = GaussianProcessGuider::new(GpParams::default());
    let out = gp2.result_with(0.1, 10.0, 3.0);
    assert_eq!(out, 0.0, "below min_move in warm-up must be exactly 0");
}

/// After warm-up, on a strongly trending gear error, a frame whose measured
/// error is BELOW `min_move` still returns a NON-ZERO correction: the reactive
/// term is deadbanded to 0 but the GP prediction is always applied (dossier
/// §6.8.3 / §6.7 table: "reactive 0; prediction still applied"). This is the
/// property that keeps PPEC correcting periodic error below the seeing floor.
#[test]
fn prediction_is_applied_below_the_deadband() {
    let mut gp = GaussianProcessGuider::new(GpParams::default());
    // 24 above-deadband frames of a steady positive drift build a clear
    // trend (the GP's linear basis + SE0 kernel), engaging the GP after the
    // 10th measurement. dt = 3 s.
    for _ in 0..24 {
        gp.result_with(0.6, 20.0, 3.0);
    }
    // Now a below-min_move frame: reactive term is 0, but the prediction is
    // not — the drift is still being extrapolated.
    let out = gp.result_with(0.05, 20.0, 3.0);
    assert!(
        out.abs() > 1e-9,
        "below deadband after warm-up must still move (GP prediction); got {out}"
    );
}

/// Dead reckoning (`deduce_result`, dossier §6.8.3): with a trained model the
/// star-lost path predicts a non-zero move (prediction gain NOT applied — raw
/// gear-error increment). A fresh guider with no model deduces exactly 0.
#[test]
fn deduce_result_predicts_when_trained_and_is_zero_when_cold() {
    // Cold: no measurements → deduce is 0.
    let mut cold = GaussianProcessGuider::new(GpParams::default());
    assert_eq!(cold.deduce_result(), 0.0, "cold deduce must be 0");

    // Trained on a long, clearly periodic run so that the last timestamp
    // exceeds min_periods_for_inference·P and the model is engaged.
    let mut gp = GaussianProcessGuider::new(GpParams::default());
    for k in 0..220 {
        let t = 5.0 * k as f64;
        let err = 3.0 * (2.0 * std::f64::consts::PI * t / 200.0).sin();
        gp.result_with(err, 20.0, 5.0);
    }
    let deduced = gp.deduce_result();
    assert!(
        deduced.abs() > 1e-9,
        "a trained model must dead-reckon a non-zero move; got {deduced}"
    );
}

/// `min_move` is surfaced through the trait accessor (dossier §6.8.1 default
/// 0.2) and `reset` returns the controller to its cold state.
#[test]
fn min_move_accessor_and_reset() {
    let mut gp = GaussianProcessGuider::new(GpParams::default());
    assert!((gp.min_move() - 0.2).abs() < 1e-12, "default min_move 0.2");
    for _ in 0..15 {
        gp.result_with(0.5, 20.0, 3.0);
    }
    gp.reset();
    // After reset the model is cold again: deduce is 0 and warm-up is
    // proportional from the first frame.
    assert_eq!(gp.deduce_result(), 0.0, "reset clears the model");
    let out = gp.result_with(1.0, 10.0, 3.0);
    assert!(
        (out - 0.6).abs() < 1e-12,
        "post-reset warm-up proportional; got {out}"
    );
}
