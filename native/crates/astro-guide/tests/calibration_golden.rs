// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's calibration state
// machine (src/calibration.rs). Derived from PHD2 calstep_dialog.cpp:206-241,
// scope.cpp:1202-1784, scope.cpp:44-69/scope.h:120-127, scope.cpp:868-985,
// and mount.cpp:1544-1593 via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§8.1-§8.4)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.
//
// Provenance: the dossier gives no literal end-to-end calibration vector
// (§8.2 is a state-machine description, not a scripted example), so this
// fixture is hand-derived per this task's brief: drive `Calibrator` against
// a synthetic linear mount model (`move_star`) inside the test itself, and
// assert the recovered x_rate/y_rate/x_angle/y_angle match the injected
// model within tolerance. The full 25-`step()`-call trace for the happy-path
// fixture (every GoWest/GoEast/ClearBacklash/GoNorth/GoSouth/NudgeSouth
// pulse, in order, with the exact position after each) is hand-traced in
// p1-t6-report.md; this is the by-hand-trace the port exception prescribes.

use std::f64::consts::PI;

use astro_guide::calibration::{
    default_calibration_distance, sanity_advisories, CalConfig, CalOutcome, Calibrator, DecMode,
    CALIBRATION_RATE_UNCALIBRATED,
};
use astro_guide::transforms::{norm_angle, Cal, Parity, PierSide};
use astro_guide::types::{CalLeg, Direction};

/// Drive `cal` to completion, applying every returned `Pulse` to `pos` via
/// `move_star` and calling `step` again, until `Done`/`Failed`. Panics if
/// calibration runs past a generous iteration cap (a real bug would
/// otherwise hang the test instead of failing it).
fn drive(
    cal: &mut Calibrator,
    mut pos: (f64, f64),
    mut move_star: impl FnMut(Direction, u32, (f64, f64)) -> (f64, f64),
) -> CalOutcome {
    for _ in 0..500 {
        let outcome = cal.step(pos);
        match &outcome {
            CalOutcome::Pulse { dir, ms, .. } => pos = move_star(*dir, *ms, pos),
            CalOutcome::Done(_) | CalOutcome::Failed(_) => return outcome,
        }
    }
    panic!("calibration did not terminate within 500 step() calls");
}

/// A straight-line mount model: WEST/EAST move purely along x (dossier
/// units: LEFT==WEST, i.e. WEST decreases x), NORTH/SOUTH move along a
/// Dec axis tilted `tilt` radians off vertical (the "small orthogonality
/// tilt" the brief calls for).
fn linear_mount(
    ra_rate: f64,
    dec_rate: f64,
    tilt: f64,
) -> impl FnMut(Direction, u32, (f64, f64)) -> (f64, f64) {
    let dec_axis = PI / 2.0 - tilt;
    let (dcx, dcy) = (dec_axis.cos(), dec_axis.sin());
    move |dir, ms, pos| {
        let ms = f64::from(ms);
        match dir {
            Direction::West => (pos.0 - ra_rate * ms, pos.1),
            Direction::East => (pos.0 + ra_rate * ms, pos.1),
            Direction::North => (pos.0 + dec_rate * ms * dcx, pos.1 + dec_rate * ms * dcy),
            Direction::South => (pos.0 - dec_rate * ms * dcx, pos.1 - dec_rate * ms * dcy),
        }
    }
}

#[test]
fn happy_path_linear_mount_recovers_injected_rates_and_angles() {
    const RA_RATE: f64 = 0.02; // px/ms
    const DEC_RATE: f64 = 0.018; // px/ms
    const TILT: f64 = 0.01; // rad, small orthogonality tilt off vertical

    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let outcome = drive(&mut calr, start, linear_mount(RA_RATE, DEC_RATE, TILT));

    let cal = match outcome {
        CalOutcome::Done(cal) => cal,
        other => panic!("expected Done, got {other:?}"),
    };

    // recovered rates within 5% of injected; x_angle within 0.02 rad of 0;
    // y_angle within 0.02 rad of PI/2; is_valid true; y_rate != 1.0 (dec calibrated).
    assert!(
        (cal.x_rate - RA_RATE).abs() / RA_RATE < 0.05,
        "x_rate = {}",
        cal.x_rate
    );
    assert!(
        (cal.y_rate - DEC_RATE).abs() / DEC_RATE < 0.05,
        "y_rate = {}",
        cal.y_rate
    );
    assert!(cal.x_angle.abs() < 0.02, "x_angle = {}", cal.x_angle);
    assert!(
        (cal.y_angle - PI / 2.0).abs() < 0.02,
        "y_angle = {}",
        cal.y_angle
    );
    assert!(cal.is_valid);
    assert!((cal.y_rate - CALIBRATION_RATE_UNCALIBRATED).abs() > 1e-9);

    // Hand-traced step counts (see p1-t6-report.md for the full trace):
    // 5 WEST pulses (30px >= 25px distance), 5 NORTH pulses in the GO_NORTH
    // leg proper (27px >= 25px from the backlash-clearing handoff point).
    assert_eq!(calr.ra_steps(), 5);
    assert_eq!(calr.dec_steps(), 5);

    // A clean calibration with >=4 steps on both axes and a rate ratio near
    // cos(declination) draws no advisory.
    assert!(sanity_advisories(&cal, calr.ra_steps(), calr.dec_steps()).is_empty());
}

#[test]
fn dec_guide_mode_off_completes_with_uncalibrated_dec_sentinel() {
    const RA_RATE: f64 = 0.02;

    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Off,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let move_star = move |dir: Direction, ms: u32, pos: (f64, f64)| -> (f64, f64) {
        let ms = f64::from(ms);
        match dir {
            Direction::West => (pos.0 - RA_RATE * ms, pos.1),
            Direction::East => (pos.0 + RA_RATE * ms, pos.1),
            Direction::North | Direction::South => {
                panic!("dec_guide_mode::Off must never issue a Dec pulse")
            }
        }
    };
    let outcome = drive(&mut calr, start, move_star);

    let cal = match outcome {
        CalOutcome::Done(cal) => cal,
        other => panic!("expected Done, got {other:?}"),
    };

    assert_eq!(cal.y_rate, CALIBRATION_RATE_UNCALIBRATED);
    assert_eq!(cal.y_angle, norm_angle(cal.x_angle + PI / 2.0));
    assert_eq!(calr.dec_steps(), 0, "Dec was never calibrated");
    assert!(cal.is_valid);
}

#[test]
fn west_leg_never_moving_fails_after_max_steps() {
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);

    let mut pulses = 0u32;
    let outcome = loop {
        let outcome = calr.step(start); // star never moves
        match outcome {
            CalOutcome::Pulse { leg, dir, .. } => {
                assert_eq!(leg, CalLeg::GoWest);
                assert_eq!(dir, Direction::West);
                pulses += 1;
                assert!(pulses <= 60, "expected failure at or before 60 pulses");
            }
            CalOutcome::Done(_) => panic!("a non-moving mount must never complete calibration"),
            CalOutcome::Failed(_) => break outcome,
        }
    };

    match outcome {
        CalOutcome::Failed(ref msg) => {
            assert_eq!(pulses, 60, "expected exactly 60 pulses before failure");
            assert!(msg.contains("did not move enough"), "message: {msg}");
            assert!(msg.starts_with("RA Calibration Failed"), "message: {msg}");
        }
        _ => unreachable!(),
    }

    // Failed is sticky: calling step() again returns the same outcome.
    assert_eq!(
        calr.step(start),
        CalOutcome::Failed("RA Calibration Failed: star did not move enough".to_string())
    );
}

#[test]
fn default_calibration_distance_matches_dossier_formula() {
    // max(25, ceil(20.0 / image_scale)).
    assert_eq!(default_calibration_distance(2.0), 25.0); // ceil(10)=10 -> floor 25 wins
    assert_eq!(default_calibration_distance(0.5), 40.0); // ceil(40)=40 -> 40 wins
}

fn base_cal() -> Cal {
    Cal {
        x_rate: 0.02,
        y_rate: 0.018,
        x_angle: 0.0,
        y_angle: PI / 2.0,
        y_angle_error: Cal::y_angle_error_from(0.0, PI / 2.0),
        declination: 0.0,
        pier_side: PierSide::Unknown,
        ra_parity: Parity::Unknown,
        dec_parity: Parity::Unknown,
        rotator_angle: 0.0,
        binning: 1,
        is_valid: true,
    }
}

#[test]
fn sanity_advisories_clean_calibration_is_silent() {
    let cal = base_cal();
    assert!(sanity_advisories(&cal, 12, 12).is_empty());
}

#[test]
fn sanity_advisories_flags_few_steps() {
    let cal = base_cal();
    let msgs = sanity_advisories(&cal, 3, 12);
    assert_eq!(msgs.len(), 1);
    assert!(msgs[0].contains("few guide steps"));
}

#[test]
fn sanity_advisories_flags_non_orthogonality() {
    let mut cal = base_cal();
    // 75 degrees apart -> 15 degrees off the nearest multiple of 90, over
    // the 12.5 degree tolerance.
    cal.y_angle = (90.0_f64 - 15.0).to_radians();
    let msgs = sanity_advisories(&cal, 12, 12);
    assert_eq!(msgs.len(), 1);
    assert!(msgs[0].contains("axis angles"));
}

#[test]
fn sanity_advisories_flags_axis_rate_ratio_mismatch() {
    let mut cal = base_cal();
    cal.declination = 0.0;
    cal.x_rate = 0.02;
    cal.y_rate = 0.005; // ratio far from cos(0) == 1.0
    let msgs = sanity_advisories(&cal, 12, 12);
    assert_eq!(msgs.len(), 1);
    assert!(msgs[0].contains("RA and Dec rates"));
}
