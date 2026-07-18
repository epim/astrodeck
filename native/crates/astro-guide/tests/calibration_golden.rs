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
    pos: (f64, f64),
    move_star: impl FnMut(Direction, u32, (f64, f64)) -> (f64, f64),
) -> CalOutcome {
    drive_recorded(cal, pos, move_star).0
}

/// Like [`drive`] but also returns every issued pulse in order, so tests
/// can assert exact per-leg pulse counts (the review's boundary vectors
/// pin these).
fn drive_recorded(
    cal: &mut Calibrator,
    mut pos: (f64, f64),
    mut move_star: impl FnMut(Direction, u32, (f64, f64)) -> (f64, f64),
) -> (CalOutcome, Vec<(CalLeg, Direction, u32)>) {
    let mut pulses = Vec::new();
    for _ in 0..500 {
        let outcome = cal.step(pos);
        match &outcome {
            CalOutcome::Pulse { leg, dir, ms } => {
                pulses.push((*leg, *dir, *ms));
                pos = move_star(*dir, *ms, pos);
            }
            CalOutcome::Done(_) | CalOutcome::Failed(_) => return (outcome, pulses),
        }
    }
    panic!("calibration did not terminate within 500 step() calls");
}

/// Count the pulses in a [`drive_recorded`] trace belonging to `leg`.
fn leg_count(pulses: &[(CalLeg, Direction, u32)], leg: CalLeg) -> usize {
    pulses.iter().filter(|p| p.0 == leg).count()
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
    // Provenance note (P1-T6 review, fix round 1): the brief's fixture text
    // said "Failed after 60 steps", but the review + controller ruling
    // ratified the upstream-literal post-increment count instead
    // (scope.cpp:1252, `m_calibrationSteps++ > MAX_CALIBRATION_STEPS`):
    // pre-increment values 0..=60 all pass, so 61 pulses are issued and
    // the failure fires on the 62nd evaluation. The "60" in the brief was
    // a loose restatement of the constant, not deliberate intent.
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
                assert!(pulses <= 61, "expected failure at or before 61 pulses");
            }
            CalOutcome::Done(_) => panic!("a non-moving mount must never complete calibration"),
            CalOutcome::Failed(_) => break outcome,
        }
    };

    match outcome {
        CalOutcome::Failed(ref msg) => {
            assert_eq!(
                pulses, 61,
                "upstream-literal: exactly 61 pulses before failure"
            );
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

// ---------------------------------------------------------------------------
// Fix round (P1-T6 review ruling): upstream-literal post-increment boundary
// vectors + coverage for the assume_orthogonal / GO_NORTH-failure /
// CLEAR_BACKLASH-failure / proceed-anyway paths. All hand-traced from
// scope.cpp:1202-1784 at the cited lines; per-leg pulse counts below are
// the upstream-literal post-increment counts the controller ratified.
// ---------------------------------------------------------------------------

#[test]
fn west_leg_reaching_distance_on_exactly_61st_pulse_succeeds() {
    // Boundary vector (a): pins the post-increment divergence directly.
    // Per-pulse west movement 0.41px (rate 0.41/300 px/ms, duration 300):
    // after 60 pulses dist = 24.6 < 25 (a 61st pulse must be permitted --
    // pre-increment value 60 passes `> 60`, scope.cpp:1252); after 61
    // pulses dist = 25.01 >= 25 -> the leg SUCCEEDS with ra_steps = 61.
    // Under the rejected clean-bound reading this fixture would have
    // failed at the 61st evaluation instead.
    const RA_RATE: f64 = 0.41 / 300.0;
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Off, // shortest path to Done after GO_EAST
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let (outcome, pulses) = drive_recorded(&mut calr, start, linear_mount(RA_RATE, 0.018, 0.0));

    let cal = match outcome {
        CalOutcome::Done(cal) => cal,
        other => panic!("expected Done, got {other:?}"),
    };
    assert_eq!(leg_count(&pulses, CalLeg::GoWest), 61);
    assert_eq!(calr.ra_steps(), 61);
    // x_rate = dist/(61*300) recovers the injected rate in the linear model.
    assert!(
        (cal.x_rate - RA_RATE).abs() / RA_RATE < 1e-9,
        "x_rate = {}",
        cal.x_rate
    );
    // GO_EAST recenter budget mirrors the outbound leg: 61 east pulses.
    assert_eq!(leg_count(&pulses, CalLeg::GoEast), 61);
}

#[test]
fn nudge_south_issues_fourth_nudge_upstream_literal() {
    // Boundary vector (b): scope.cpp:1712 checks `m_calibrationSteps <=
    // MAX_NUDGES` (counter incremented after each issued nudge), so
    // pre-values 0..=3 pass and FOUR nudges are issued. Mount: symmetric
    // RA 0.02; Dec north 0.018, Dec south 0.009 (weak south response, the
    // classic Dec-backlash signature that makes nudging do real work).
    // Hand trace: clearing 3 pulses (5.4px each >= expected 3.6), handoff
    // marker at 10.8; GO_NORTH ends at 37.8 (dec_steps=5, y_rate=0.018);
    // GO_SOUTH's 5 pulses move only 2.7px each -> ends at 24.3px residual;
    // each clamped 300ms nudge moves 2.7px: 24.3 -> 21.6 -> 18.9 -> 16.2
    // -> 13.5 after nudge 4; attempt 5 sees pre-value 4 > 3 and completes.
    // All four nudges clamp to calibration_duration (floor(amt/0.018) >>
    // 300). Under the rejected clean bound only 3 nudges would be issued.
    const RA_RATE: f64 = 0.02;
    const DEC_NORTH: f64 = 0.018;
    const DEC_SOUTH: f64 = 0.009;
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let move_star = |dir: Direction, ms: u32, pos: (f64, f64)| -> (f64, f64) {
        let ms = f64::from(ms);
        match dir {
            Direction::West => (pos.0 - RA_RATE * ms, pos.1),
            Direction::East => (pos.0 + RA_RATE * ms, pos.1),
            Direction::North => (pos.0, pos.1 + DEC_NORTH * ms),
            Direction::South => (pos.0, pos.1 - DEC_SOUTH * ms),
        }
    };
    let (outcome, pulses) = drive_recorded(&mut calr, start, move_star);

    let cal = match outcome {
        CalOutcome::Done(cal) => cal,
        other => panic!("expected Done, got {other:?}"),
    };
    let nudges: Vec<_> = pulses
        .iter()
        .filter(|p| p.0 == CalLeg::NudgeSouth)
        .collect();
    assert_eq!(nudges.len(), 4, "upstream-literal: exactly 4 nudges");
    for n in &nudges {
        assert_eq!(n.1, Direction::South);
        assert_eq!(n.2, 300, "every nudge clamps to calibration_duration");
    }
    assert!((cal.y_rate - DEC_NORTH).abs() / DEC_NORTH < 1e-9);
    assert_eq!(calr.dec_steps(), 5);
}

#[test]
fn assume_orthogonal_projects_dec_onto_nearest_perpendicular() {
    // Coverage vector (c): the assume_orthogonal branch
    // (scope.cpp:1543-1556). Dec axis tilted 0.05 rad off vertical; RA
    // motion pure x so x_angle = 0 exactly. a1 = norm(0 + pi/2) = pi/2,
    // a2 = -pi/2; measured north angle = pi/2 - 0.05 is closer to a1 ->
    // y_angle = pi/2 exactly (the snapped perpendicular, NOT the measured
    // angle). dec_dist = dist * cos(measured - y_angle) = 27 * cos(0.05)
    // -> y_rate = 27*cos(0.05)/(5*300) = 0.018*cos(0.05), strictly less
    // than the unprojected 0.018.
    const RA_RATE: f64 = 0.02;
    const DEC_RATE: f64 = 0.018;
    const TILT: f64 = 0.05;
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: true,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let outcome = drive(&mut calr, start, linear_mount(RA_RATE, DEC_RATE, TILT));

    let cal = match outcome {
        CalOutcome::Done(cal) => cal,
        other => panic!("expected Done, got {other:?}"),
    };
    assert!(cal.x_angle.abs() < 1e-12, "x_angle = {}", cal.x_angle);
    assert!(
        (cal.y_angle - PI / 2.0).abs() < 1e-12,
        "y_angle snapped to a1 = pi/2, got {}",
        cal.y_angle
    );
    let expected_y_rate = DEC_RATE * TILT.cos();
    assert!(
        (cal.y_rate - expected_y_rate).abs() < 1e-9,
        "projected y_rate: expected {expected_y_rate}, got {}",
        cal.y_rate
    );
    assert!(
        cal.y_rate < DEC_RATE,
        "projection must shrink the rate below the unprojected value"
    );
    assert_eq!(calr.dec_steps(), 5);
}

#[test]
fn north_leg_jamming_after_clearing_fails_dec_calibration() {
    // Coverage vector (d): the GO_NORTH failure path (scope.cpp:1526-1534).
    // Mount moves RA fine; Dec moves 5.4px for each of the first 3 north
    // pulses (clearing succeeds: 3 accepted moves, handoff with the last
    // clearing move adopted as north step 1, steps = 1, scope.cpp:1497),
    // then jams. GO_NORTH entry steps = 1, so post-increment pre-values
    // 1..=60 issue 60 GO_NORTH pulses; pre-value 61 fails on the next
    // evaluation with the DEC message.
    const RA_RATE: f64 = 0.02;
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 300,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let mut north_pulses_moved = 0u32;
    let move_star = move |dir: Direction, ms: u32, pos: (f64, f64)| -> (f64, f64) {
        let ms = f64::from(ms);
        match dir {
            Direction::West => (pos.0 - RA_RATE * ms, pos.1),
            Direction::East => (pos.0 + RA_RATE * ms, pos.1),
            Direction::North => {
                if north_pulses_moved < 3 {
                    north_pulses_moved += 1;
                    (pos.0, pos.1 + 0.018 * ms)
                } else {
                    pos // jammed
                }
            }
            Direction::South => panic!("must fail before any south pulse"),
        }
    };
    let (outcome, pulses) = drive_recorded(&mut calr, start, move_star);

    match outcome {
        CalOutcome::Failed(ref msg) => {
            assert!(msg.starts_with("DEC Calibration Failed"), "message: {msg}");
            assert!(msg.contains("did not move enough"), "message: {msg}");
        }
        other => panic!("expected Failed, got {other:?}"),
    }
    assert_eq!(leg_count(&pulses, CalLeg::ClearBacklash), 3);
    assert_eq!(
        leg_count(&pulses, CalLeg::GoNorth),
        60,
        "entry steps=1 (adopted clearing move) leaves budget for 60 pulses"
    );
}

#[test]
fn clear_backlash_fails_when_cumulative_below_three_px() {
    // Coverage vector (e1): the CLEAR_BACKLASH hard-failure path
    // (scope.cpp:1477-1485). Duration 7500ms makes the clearing budget
    // exactly max(8, 60000/7500) = 8 pulses (scope.cpp:1393). Dec never
    // moves: after the budget is exhausted, cumulative 0 <
    // BL_MIN_CLEARING_DISTANCE (3px) -> hard fail. RA at 0.02 px/ms
    // covers 150px in one 7500ms pulse, so GO_WEST/GO_EAST take 1 pulse
    // each.
    const RA_RATE: f64 = 0.02;
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 7500,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let move_star = |dir: Direction, ms: u32, pos: (f64, f64)| -> (f64, f64) {
        let ms = f64::from(ms);
        match dir {
            Direction::West => (pos.0 - RA_RATE * ms, pos.1),
            Direction::East => (pos.0 + RA_RATE * ms, pos.1),
            Direction::North | Direction::South => pos, // Dec dead
        }
    };
    let (outcome, pulses) = drive_recorded(&mut calr, start, move_star);

    match outcome {
        CalOutcome::Failed(ref msg) => {
            assert!(
                msg.starts_with("Backlash Clearing Failed"),
                "message: {msg}"
            );
            assert!(msg.contains("did not move enough"), "message: {msg}");
        }
        other => panic!("expected Failed, got {other:?}"),
    }
    assert_eq!(leg_count(&pulses, CalLeg::GoWest), 1);
    assert_eq!(leg_count(&pulses, CalLeg::GoEast), 1);
    assert_eq!(leg_count(&pulses, CalLeg::ClearBacklash), 8);
}

#[test]
fn clear_backlash_proceeds_anyway_when_cumulative_at_least_three_px() {
    // Coverage vector (e2): the proceed-anyway path (scope.cpp:1465-1476).
    // Same 8-pulse budget (duration 7500); Dec moves 1.5px per full pulse
    // (delta 1.5 << expected 90 -> never accepted), so the budget
    // exhausts with cumulative 12px >= 3px -> proceed with a FRESH
    // GO_NORTH start (steps reset to 0, leg_start = current,
    // scope.cpp:1469-1470). GO_NORTH then needs ceil(25/1.5) = 17 pulses
    // (post-increment pre-values 0..=16), y_rate = 25.5/(17*7500);
    // GO_SOUTH recenters 17 pulses back to 12px residual; bl_marker sits
    // at 10.5 (position before the last clearing pulse) so the nudge size
    // gate is 25 + 10.5; four 7500ms nudges (upstream-literal cap) each
    // move 1.5px, leaving 6px residual at completion.
    const RA_RATE: f64 = 0.02;
    let cfg = CalConfig {
        calibration_distance: 25.0,
        calibration_duration_ms: 7500,
        max_steps: 60,
        dec_guide_mode: DecMode::Auto,
        assume_orthogonal: false,
    };
    let start = (0.0, 0.0);
    let mut calr = Calibrator::new(cfg, start);
    let move_star = |dir: Direction, ms: u32, pos: (f64, f64)| -> (f64, f64) {
        let frac = f64::from(ms) / 7500.0;
        match dir {
            Direction::West => (pos.0 - RA_RATE * f64::from(ms), pos.1),
            Direction::East => (pos.0 + RA_RATE * f64::from(ms), pos.1),
            Direction::North => (pos.0, pos.1 + 1.5 * frac),
            Direction::South => (pos.0, pos.1 - 1.5 * frac),
        }
    };
    let (outcome, pulses) = drive_recorded(&mut calr, start, move_star);

    let cal = match outcome {
        CalOutcome::Done(cal) => cal,
        other => panic!("expected Done, got {other:?}"),
    };
    assert_eq!(leg_count(&pulses, CalLeg::ClearBacklash), 8);
    assert_eq!(
        leg_count(&pulses, CalLeg::GoNorth),
        17,
        "fresh start (steps=0) after proceed-anyway"
    );
    assert_eq!(calr.dec_steps(), 17);
    let expected_y_rate = 25.5 / (17.0 * 7500.0);
    assert!(
        (cal.y_rate - expected_y_rate).abs() < 1e-12,
        "y_rate: expected {expected_y_rate}, got {}",
        cal.y_rate
    );
    assert_eq!(leg_count(&pulses, CalLeg::GoSouth), 17);
    assert_eq!(leg_count(&pulses, CalLeg::NudgeSouth), 4);
}
