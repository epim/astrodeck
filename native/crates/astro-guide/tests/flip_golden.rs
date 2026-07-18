// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector test for astro-guide's meridian-flip calibration
// adjustment (src/engine.rs `GuideEngine::flip_calibration`). Derived from
// PHD2 `mount.cpp:891-957` (`Mount::FlipCalibration`) via the audited
// algorithm dossier docs/native-parity/algorithms/phd2-guiding.md (§9 item 4,
// informally "§9.4") (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code
// copied from PHD2.

//! P2-T2 pinned vectors (brief): an East-side calibration flipped to West
//! adds π to `x_angle` (normalized); `y_angle` gains π ONLY when the mount
//! declares `requires_dec_flip`; dec parity flips unless the dec flip was
//! required (in which case it is unchanged); RA parity NEVER changes
//! (dossier §9 item 4). Exact vector: `x_angle=0.3 -> norm(0.3+PI)`,
//! asserted to `1e-9`.

use astro_guide::engine::{EngineConfig, GuideEngine};
use astro_guide::transforms::{norm_angle, Cal, Parity, PierSide};
use std::f64::consts::{FRAC_PI_2, PI};

/// The brief's pinned East-side calibration: `x_angle = 0.3`.
fn east_cal() -> Cal {
    let x_angle = 0.3;
    let y_angle = x_angle + FRAC_PI_2;
    Cal {
        x_rate: 0.02,
        y_rate: 0.018,
        x_angle,
        y_angle,
        y_angle_error: Cal::y_angle_error_from(x_angle, y_angle),
        declination: 0.1,
        pier_side: PierSide::East,
        ra_parity: Parity::Even,
        dec_parity: Parity::Odd,
        rotator_angle: 0.0,
        binning: 1,
        is_valid: true,
    }
}

#[test]
fn flip_without_dec_flip_adds_pi_to_x_angle_only() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(east_cal());

    let ok = e.flip_calibration(false);
    assert!(
        ok,
        "flip_calibration must report success on a valid calibration"
    );

    let cal = e.calibration().expect("flip must preserve the calibration");

    // x_angle=0.3 -> norm(0.3+PI), 1e-9 (the brief's exact vector).
    let expected_x = norm_angle(0.3 + PI);
    assert!(
        (cal.x_angle - expected_x).abs() < 1e-9,
        "x_angle = {}, expected {}",
        cal.x_angle,
        expected_x
    );
    // y_angle is UNCHANGED when the mount does not require a dec flip.
    let expected_y = 0.3 + FRAC_PI_2;
    assert!(
        (cal.y_angle - expected_y).abs() < 1e-9,
        "y_angle = {}, expected {} (must be unchanged)",
        cal.y_angle,
        expected_y
    );
    // dec parity flips (Odd -> Even) when no dec flip was required.
    assert_eq!(
        cal.dec_parity,
        Parity::Even,
        "dec parity must flip Odd -> Even"
    );
    // RA parity NEVER changes.
    assert_eq!(cal.ra_parity, Parity::Even, "RA parity must never change");
    // Pier side toggles East -> West.
    assert_eq!(cal.pier_side, PierSide::West);
}

#[test]
fn flip_with_dec_flip_adds_pi_to_both_angles_and_keeps_dec_parity() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(east_cal());

    let ok = e.flip_calibration(true);
    assert!(
        ok,
        "flip_calibration must report success on a valid calibration"
    );

    let cal = e.calibration().expect("flip must preserve the calibration");

    let expected_x = norm_angle(0.3 + PI);
    assert!(
        (cal.x_angle - expected_x).abs() < 1e-9,
        "x_angle = {}, expected {}",
        cal.x_angle,
        expected_x
    );
    // y_angle ALSO gains PI when the mount requires a dec flip.
    let expected_y = norm_angle(0.3 + FRAC_PI_2 + PI);
    assert!(
        (cal.y_angle - expected_y).abs() < 1e-9,
        "y_angle = {}, expected {} (dec-flip required)",
        cal.y_angle,
        expected_y
    );
    // dec parity is UNCHANGED when the dec flip was required.
    assert_eq!(
        cal.dec_parity,
        Parity::Odd,
        "dec parity must stay Odd when dec-flip required"
    );
    // RA parity NEVER changes.
    assert_eq!(cal.ra_parity, Parity::Even, "RA parity must never change");
    assert_eq!(cal.pier_side, PierSide::West);
}

#[test]
fn flip_is_a_no_op_without_a_valid_calibration() {
    let mut e = GuideEngine::new(EngineConfig::default());
    assert!(!e.flip_calibration(false), "no calibration -> no-op false");
    assert!(!e.flip_calibration(true), "no calibration -> no-op false");
}
