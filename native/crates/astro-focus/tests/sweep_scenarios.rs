// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: orchestration acceptance shapes and property tests derived from
// docs/native-parity/algorithms/nina-autofocus.md (§3.1 sweep counts, §13
// end-to-end acceptance shapes, §15 recommended defaults).

//! End-to-end sweep orchestration pins and a noisy-recovery property test.

use astro_focus::config::{CurveFitting, FocusConfig};
use astro_focus::sweep::{FailReason, FocusSweep, Step};

/// Drive a sweep to completion against a measurement closure
/// `measure(position) -> (hfr, stdev, star_count)`. Returns the terminal
/// step, the number of `MoveTo` captures, and the number of measured points.
fn drive<F>(mut sweep: FocusSweep, mut measure: F) -> (Step, usize, usize)
where
    F: FnMut(i32) -> (f64, f64, u32),
{
    let mut captures = 0usize;
    // Guard against runaway loops in a broken machine.
    for _ in 0..10_000 {
        match sweep.next() {
            Step::MoveTo(pos) => {
                captures += 1;
                let (hfr, sd, n) = measure(pos);
                sweep.add_measurement(pos, hfr, sd, n);
            }
            done @ Step::Done(_) => {
                let pts = sweep.points().len();
                return (done, captures, pts);
            }
            failed @ Step::Failed(_) => {
                let pts = sweep.points().len();
                return (failed, captures, pts);
            }
        }
    }
    panic!("sweep did not terminate");
}

/// Symmetric V: HFR minimized at `center`, slope `k` per step.
fn v_curve(center: i32, k: f64) -> impl Fn(i32) -> (f64, f64, u32) {
    move |pos| {
        let hfr = 1.0 + (pos - center).abs() as f64 * k;
        (hfr, 0.1, 30)
    }
}

#[test]
fn five_points_seven_captures_ideal_v() {
    // Dossier §13: offset_steps=2, step=100, TRENDLINES, baseline mode
    // (threshold 0). Ideal V -> 5 measure points, 7 captures (1 baseline + 5
    // sweep + 1 validation), final position == initial (5000).
    let cfg = FocusConfig {
        step_size: 100,
        offset_steps: 2,
        curve_fitting: CurveFitting::Trendlines,
        r_squared_threshold: 0.0,
        ..FocusConfig::default()
    };
    let sweep = FocusSweep::new(cfg, 5000);
    let (step, captures, points) = drive(sweep, v_curve(5000, 0.01));

    assert_eq!(points, 5, "measure points");
    assert_eq!(
        captures, 7,
        "captures = 1 baseline + 5 sweep + 1 validation"
    );
    match step {
        Step::Done(o) => {
            assert_eq!(o.best_position, 5000);
            assert_eq!(o.trend_intersection, Some((5000, 1.0)));
        }
        other => panic!("expected Done, got {other:?}"),
    }
}

#[test]
fn extends_right_when_right_quota_unmet() {
    // Minimum to the RIGHT of the initial sweep (center 5300, start 5000):
    // the initial OUT-side pass sits entirely left of the true minimum, so
    // the extension loop must add points on the right.
    let cfg = FocusConfig {
        step_size: 100,
        offset_steps: 2,
        curve_fitting: CurveFitting::Trendlines,
        r_squared_threshold: 0.0,
        ..FocusConfig::default()
    };
    let sweep = FocusSweep::new(cfg, 5000);
    let (step, _captures, points) = drive(sweep, v_curve(5300, 0.01));

    match step {
        Step::Done(o) => {
            assert!(points >= 5, "extended to bracket the minimum: {points} pts");
            assert!(
                (o.best_position - 5300).abs() <= 100,
                "recovered near 5300, got {}",
                o.best_position
            );
        }
        other => panic!("expected Done, got {other:?}"),
    }
}

#[test]
fn extends_left_when_left_quota_unmet() {
    // Ideal V centered on the start: the initial pass is all right-of-min, so
    // the left side must be extended (twice for offset_steps=2).
    let cfg = FocusConfig {
        step_size: 100,
        offset_steps: 2,
        curve_fitting: CurveFitting::TrendHyperbolic,
        r_squared_threshold: 0.7,
        ..FocusConfig::default()
    };
    let sweep = FocusSweep::new(cfg, 5000);
    let (step, _captures, points) = drive(sweep, v_curve(5000, 0.01));
    match step {
        Step::Done(o) => {
            assert_eq!(points, 5);
            assert!((o.best_position - 5000).abs() <= 1);
        }
        other => panic!("expected Done, got {other:?}"),
    }
}

#[test]
fn left_extension_clamps_to_position_zero_and_measures_once() {
    // Dossier §3.1/§12: a real focuser can't go below position 0. When the
    // true minimum sits at/near 0 there is no reachable "left of the
    // minimum" data (the initial sweep and every left extension stay >= 0),
    // so NINA's left-extension step clamps its target to 0, still MEASURES
    // there, adds the point to the curve, and only then breaks out of the
    // extension loop (`focuser.position() == 0`) rather than repeatedly
    // trying to push further left forever.
    let cfg = FocusConfig {
        step_size: 30,
        offset_steps: 2,
        curve_fitting: CurveFitting::Hyperbolic,
        r_squared_threshold: 0.0,
        ..FocusConfig::default()
    };
    // Initial pass (non-reverse, offset=2, step=30) measures start+60,
    // start+30, start -> 100, 70, 40: all strictly right of the true
    // minimum at 0, so the left side starts (and stays) starved.
    let start = 40;
    let mut sweep = FocusSweep::new(cfg, start);

    let mut zero_moves = 0usize;
    let mut result = None;
    for _ in 0..10_000 {
        match sweep.next() {
            Step::MoveTo(pos) => {
                assert!(pos >= 0, "focuser target went negative: {pos}");
                if pos == 0 {
                    zero_moves += 1;
                }
                let (hfr, sd, n) = v_curve(0, 0.01)(pos);
                sweep.add_measurement(pos, hfr, sd, n);
            }
            step => {
                result = Some(step);
                break;
            }
        }
    }

    assert_eq!(
        zero_moves, 1,
        "the clamped position 0 must be visited (and measured) exactly once"
    );
    let zero_points = sweep.points().iter().filter(|p| p.position == 0.0).count();
    assert_eq!(
        zero_points, 1,
        "exactly one point at position 0 in the curve"
    );
    assert!(
        result.is_some(),
        "sweep must terminate (not loop forever re-clamping to 0)"
    );
}

#[test]
fn fails_when_max_points_exhausted_monotonic() {
    // A monotonic (no-minimum) curve never brackets a minimum: the machine
    // keeps extending the low side until the max-points cap, then fits and
    // fails the R^2 gate (the starved side has R^2 = 0). Dossier §12.
    let cfg = FocusConfig {
        step_size: 50,
        offset_steps: 2,
        curve_fitting: CurveFitting::TrendHyperbolic,
        r_squared_threshold: 0.7,
        ..FocusConfig::default()
    };
    // max_points = frames(1) * offset(2) * 10 = 20.
    assert_eq!(cfg.max_points(), 20);
    let start = 100_000;
    // Monotonic increasing HFR with position; adjacent gap 0.5 > 0.1 band.
    let measure = |pos: i32| -> (f64, f64, u32) { (5.0 + (pos - 99_000) as f64 * 0.01, 0.1, 30) };
    let sweep = FocusSweep::new(cfg, start);
    let (step, _captures, points) = drive(sweep, measure);

    assert!(
        points > 20,
        "collected past the cap before stopping: {points}"
    );
    match step {
        Step::Failed(reason) => assert!(
            matches!(
                reason,
                FailReason::RSquaredBelowThreshold | FailReason::OutOfBounds
            ),
            "unexpected reason {reason:?}"
        ),
        other => panic!("expected Failed, got {other:?}"),
    }
}

#[test]
fn not_enough_spread_fails_without_reattempt() {
    // A perfectly flat curve leaves no trend points on either side -> the
    // NotEnoughSpread hard failure (no reattempt), with a restore move first.
    let cfg = FocusConfig {
        step_size: 100,
        offset_steps: 2,
        curve_fitting: CurveFitting::Trendlines,
        r_squared_threshold: 0.0,
        total_number_of_attempts: 3,
        ..FocusConfig::default()
    };
    let sweep = FocusSweep::new(cfg, 5000);
    let (step, _c, _p) = drive(sweep, |_pos| (3.0, 0.1, 30));
    assert!(matches!(step, Step::Failed(FailReason::NotEnoughSpread)));
}

#[test]
fn reattempt_then_succeed() {
    // Attempt 1 is a monotonic (no-minimum) curve that exhausts the max-points
    // cap and fails the R^2 gate -> reattempt. Attempt 2 (attempts == 2) is a
    // clean V and succeeds. This exercises the reattempt counter and the
    // "clear points, keep baseline" restart (dossier §3/§7).
    use std::cell::Cell;
    let start = 100_000;
    let cfg = FocusConfig {
        step_size: 50,
        offset_steps: 2,
        curve_fitting: CurveFitting::TrendHyperbolic,
        r_squared_threshold: 0.7,
        total_number_of_attempts: 2,
        ..FocusConfig::default()
    };
    let mut sweep = FocusSweep::new(cfg, start);
    let saw_attempt2 = Cell::new(false);
    let mut result = None;
    for _ in 0..10_000 {
        match sweep.next() {
            Step::MoveTo(pos) => {
                let attempt = sweep.attempt();
                if attempt >= 2 {
                    saw_attempt2.set(true);
                }
                let (hfr, sd, n) = if attempt >= 2 {
                    // Clean V centered on the start position.
                    (1.0 + (pos - start).abs() as f64 * 0.01, 0.1, 30)
                } else {
                    // Monotonic increasing HFR -> starved side, gate failure.
                    (5.0 + (pos - 99_000) as f64 * 0.01, 0.1, 30)
                };
                sweep.add_measurement(pos, hfr, sd, n);
            }
            step => {
                result = Some(step);
                break;
            }
        }
    }
    assert!(saw_attempt2.get(), "a second attempt occurred");
    match result {
        Some(Step::Done(o)) => assert!((o.best_position - start).abs() <= 1),
        other => panic!("expected Done after reattempt, got {other:?}"),
    }
}
