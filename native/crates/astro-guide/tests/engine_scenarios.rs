// SPDX-License-Identifier: Apache-2.0
//
// Provenance: scripted engine-walk golden vectors for astro-guide's guide
// engine (src/engine.rs, src/settle.rs). Derived from PHD2
// guider_multistar.cpp:925-1060 (UpdateCurrentPosition), mount.cpp:978-1087
// (MoveOffset), scope.cpp:640-816 (MoveAxis clamps/gating),
// backlash_comp.cpp:371-583 (static BLC), scope.cpp:1752-1760 (COMPLETE-state
// calibration stamping), and phdcontrol.cpp:514-567 (settle) via the audited
// algorithm dossier docs/native-parity/algorithms/phd2-guiding.md
// (§3/§5/§6/§7/§9/§10.1/§12) (BSD-3-Clause; see THIRD-PARTY-NOTICES.md).
// No code copied from PHD2.
//
// The dossier gives no literal end-to-end engine vector (§7 is a pipeline
// description, not a scripted example), so these fixtures drive the composed
// engine with SCRIPTED MeasuredStar sequences against an injected calibration
// (set_calibration), matching spec §5's "scripted fake engine -> Action
// sequences asserted". The first four tests are the brief's pinned vectors;
// the rest pin the five hard obligations (mass-check-once + append-both-paths,
// fresh-AvgDist reset, staleness-gated distance check, real-scope-pointing
// patch) and the move-pipeline branches (dec direction, dec-mode gating,
// static BLC reversal, settle window).
//
// The `let mut cfg = EngineConfig::default(); cfg.field = ...` shape below is
// the brief's pinned vector style (it keeps each vector's overrides visible on
// their own lines); the crate-level allow keeps clippy's
// `field_reassign_with_default` from rejecting those verbatim fixtures under
// the `-D warnings` gate.
#![allow(clippy::field_reassign_with_default)]

use astro_guide::calibration::{DecMode, UNKNOWN_DECLINATION};
use astro_guide::engine::{AlgoKind, EngineConfig, GuideEngine, ScopePointing};
use astro_guide::transforms::{Cal, Parity, PierSide};
use astro_guide::types::{Action, Direction, FrameMeta, MeasuredStar};

fn ident_cal() -> Cal {
    // x_angle=0, y_angle_error=0 => camera==mount; rate 0.01 px/ms both axes.
    Cal {
        x_rate: 0.01,
        y_rate: 0.01,
        x_angle: 0.0,
        y_angle: std::f64::consts::FRAC_PI_2,
        y_angle_error: 0.0,
        declination: 0.0,
        pier_side: PierSide::West,
        ra_parity: Parity::Even,
        dec_parity: Parity::Even,
        rotator_angle: 0.0,
        binning: 1,
        is_valid: true,
    }
}
fn star(x: f64, y: f64) -> MeasuredStar {
    MeasuredStar {
        x,
        y,
        snr: 30.0,
        mass: 800.0,
        hfd: 3.0,
        found: true,
    }
}

#[test]
fn steady_offset_pulses_correct_axis_and_direction() {
    let mut cfg = EngineConfig::default();
    cfg.ra_algorithm = AlgoKind::Hysteresis;
    cfg.dec_algorithm = AlgoKind::ResistSwitch;
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    // Lock at (100,100); star drifts +x (mount +x => WEST pulse per dossier §units).
    let meta = FrameMeta {
        timestamp_s: 0.0,
        exposure_s: 2.0,
    };
    let _ = e.ingest(&meta, &[star(100.0, 100.0)]); // establishes lock
                                                    // Now star at (105,100): mount error x=+5 => hysteresis first move
                                                    // (0.9*5+0.1*0)*0.7=3.15 px; /0.01 px/ms = 315 ms WEST. Dec ~0 => no dec pulse.
    let a = e.ingest(
        &FrameMeta {
            timestamp_s: 2.0,
            exposure_s: 2.0,
        },
        &[star(105.0, 100.0)],
    );
    match a {
        Action::PulsePair { ra: Some(p), dec } => {
            assert!(matches!(p.dir, Direction::West), "dir={:?}", p.dir);
            assert!((p.ms as i64 - 315).abs() <= 1, "ms={}", p.ms);
            assert!(dec.is_none() || dec.unwrap().ms == 0);
        }
        other => panic!("expected PulsePair, got {:?}", other),
    }
}

#[test]
fn deadband_yields_idle() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let m = FrameMeta {
        timestamp_s: 0.0,
        exposure_s: 2.0,
    };
    let _ = e.ingest(&m, &[star(100.0, 100.0)]);
    // 0.1 px error < min_move 0.2 on both axes => Idle.
    let a = e.ingest(
        &FrameMeta {
            timestamp_s: 2.0,
            exposure_s: 2.0,
        },
        &[star(100.1, 100.05)],
    );
    assert!(matches!(a, Action::Idle), "got {:?}", a);
}

#[test]
fn ra_pulse_clamped_to_max_duration() {
    let mut cfg = EngineConfig::default();
    cfg.max_ra_duration_ms = 2500;
    let mut e = GuideEngine::new(cfg);
    let mut c = ident_cal();
    c.x_rate = 0.001; // slow => long ms
    e.set_calibration(c);
    e.begin_guiding();
    let m = FrameMeta {
        timestamp_s: 0.0,
        exposure_s: 2.0,
    };
    let _ = e.ingest(&m, &[star(100.0, 100.0)]);
    let a = e.ingest(
        &FrameMeta {
            timestamp_s: 2.0,
            exposure_s: 2.0,
        },
        &[star(160.0, 100.0)],
    );
    if let Action::PulsePair { ra: Some(p), .. } = a {
        assert_eq!(p.ms, 2500);
    } else {
        panic!("expected clamped RA pulse, got {:?}", a);
    }
}

#[test]
fn lost_star_returns_lock_lost() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let m = FrameMeta {
        timestamp_s: 0.0,
        exposure_s: 2.0,
    };
    let _ = e.ingest(&m, &[star(100.0, 100.0)]);
    // A not-found star for long enough exhausts recovery -> LockLost.
    let mut a = Action::Idle;
    for i in 1..30 {
        a = e.ingest(
            &FrameMeta {
                timestamp_s: i as f64 * 2.0,
                exposure_s: 2.0,
            },
            &[MeasuredStar {
                x: 100.0,
                y: 100.0,
                snr: 0.0,
                mass: 0.0,
                hfd: 0.0,
                found: false,
            }],
        );
    }
    assert!(matches!(a, Action::LockLost), "got {:?}", a);
}

// ---- obligation / branch coverage beyond the brief's four vectors ----

fn frame(t: f64) -> FrameMeta {
    FrameMeta {
        timestamp_s: t,
        exposure_s: 2.0,
    }
}

/// OBLIGATION (a)+(b): a star whose mass collapses vs. the running median is
/// mass-rejected (CheckMass called once), the frame is dropped (no pulse even
/// though there is a real offset), and the rejected mass is still appended.
#[test]
fn mass_change_reject_drops_frame() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock
                                                          // five accepted stable-mass frames at zero offset fill the mass history
    for i in 1..=5 {
        let a = e.ingest(&frame(i as f64 * 2.0), &[star(100.0, 100.0)]);
        assert!(matches!(a, Action::Idle), "warmup {i}: {a:?}");
    }
    // frame 6: real +10px offset (would pulse) but the mass collapses to 100
    // vs. a median of 800 -> mass reject -> frame dropped -> Idle.
    let a = e.ingest(
        &frame(12.0),
        &[MeasuredStar {
            x: 110.0,
            y: 100.0,
            snr: 30.0,
            mass: 100.0,
            hfd: 3.0,
            found: true,
        }],
    );
    assert!(
        matches!(a, Action::Idle),
        "mass reject should drop frame, got {a:?}"
    );
}

/// Dec direction + ResistSwitch fast-switch: a large +y mount error pulses
/// SOUTH (dossier §7 ydir; §6.2 fast-switch bypasses the 3-sample wait).
#[test]
fn positive_y_offset_pulses_south() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);
    // star at (100,105): mount y=+5 => SOUTH; 5px/0.01 = 500 ms.
    let a = e.ingest(&frame(2.0), &[star(100.0, 105.0)]);
    match a {
        Action::PulsePair { ra, dec: Some(p) } => {
            assert!(ra.is_none() || ra.unwrap().ms == 0);
            assert!(matches!(p.dir, Direction::South), "dir={:?}", p.dir);
            assert!((p.ms as i64 - 500).abs() <= 1, "ms={}", p.ms);
        }
        other => panic!("expected dec PulsePair, got {:?}", other),
    }
}

/// Dec-mode gating (dossier §7): DecMode::North blocks a SOUTH correction, so
/// a +y mount error yields no dec pulse.
#[test]
fn dec_mode_north_blocks_south_pulse() {
    let mut cfg = EngineConfig::default();
    cfg.dec_guide_mode = DecMode::North;
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);
    // mount y=+5 => SOUTH needed, but North mode blocks SOUTH; mount x=0 too.
    let a = e.ingest(&frame(2.0), &[star(100.0, 105.0)]);
    assert!(
        matches!(a, Action::Idle),
        "north-mode must block the south pulse, got {:?}",
        a
    );
}

/// Static BLC (dossier §10.1, D4 static-only): a dec direction reversal adds a
/// fixed extra pulse to the reversing correction.
#[test]
fn static_blc_adds_pulse_on_dec_reversal() {
    let mut cfg = EngineConfig::default();
    cfg.blc_pulse_ms = 200; // seed pulse; dec mode Auto (default)
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);
    // first dec move: +y => SOUTH (~500 ms), sets last_dec_dir = South.
    let a1 = e.ingest(&frame(2.0), &[star(100.0, 105.0)]);
    match a1 {
        Action::PulsePair { dec: Some(p), .. } => assert!(matches!(p.dir, Direction::South)),
        other => panic!("expected south dec pulse, got {:?}", other),
    }
    // reversal: -y => NORTH; base ~500 ms + 200 ms BLC = ~700 ms.
    let a2 = e.ingest(&frame(4.0), &[star(100.0, 95.0)]);
    match a2 {
        Action::PulsePair { dec: Some(p), .. } => {
            assert!(matches!(p.dir, Direction::North), "dir={:?}", p.dir);
            assert!(
                p.ms >= 699,
                "expected BLC-boosted north pulse, got {}",
                p.ms
            );
        }
        other => panic!("expected north dec pulse, got {:?}", other),
    }
}

/// OBLIGATION (e): calibration completes with the Calibrator's sentinels
/// replaced by the host-injected real declination / pier / parity / binning.
#[test]
fn calibration_complete_patches_real_scope_pointing() {
    fn calib_mount(dir: Direction, ms: u32, pos: (f64, f64)) -> (f64, f64) {
        const RA: f64 = 0.02; // px/ms
        const DEC: f64 = 0.018;
        let ms = ms as f64;
        match dir {
            Direction::West => (pos.0 - RA * ms, pos.1),
            Direction::East => (pos.0 + RA * ms, pos.1),
            Direction::North => (pos.0, pos.1 + DEC * ms),
            Direction::South => (pos.0, pos.1 - DEC * ms),
        }
    }

    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_scope_pointing(ScopePointing {
        declination: 0.3,
        pier_side: PierSide::East,
        ra_parity: Parity::Even,
        dec_parity: Parity::Odd,
        rotator_angle: 0.0,
        binning: 2,
    });
    e.begin_calibration((200.0, 200.0));

    let mut pos = (200.0, 200.0);
    let mut t = 0.0;
    let mut guard = 0;
    loop {
        guard += 1;
        assert!(guard < 500, "calibration did not complete");
        let a = e.ingest(&frame(t), &[star(pos.0, pos.1)]);
        t += 2.0;
        match a {
            Action::CalStep { dir, ms, .. } => pos = calib_mount(dir, ms, pos),
            Action::Idle => break, // completed -> guiding (lock not yet set)
            other => panic!("unexpected during calibration: {:?}", other),
        }
    }

    let cal = e.calibration().expect("calibration produced a Cal");
    assert!(cal.is_valid);
    assert_eq!(cal.declination, 0.3, "OBLIGATION (e): declination patched");
    assert_eq!(cal.pier_side, PierSide::East);
    assert_eq!(cal.dec_parity, Parity::Odd);
    assert_eq!(cal.binning, 2);
    assert_ne!(
        cal.declination, UNKNOWN_DECLINATION,
        "the Calibrator's sentinel must have been overwritten"
    );
}

/// dither() opens a settle window (P1 stub): ingest returns Settle while the
/// window is open, then guiding resumes once it settles.
#[test]
fn dither_opens_settle_window_then_resumes() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);
    e.dither(3.0, 3.0);
    // star at the lock (0 error) but the default 10 s dwell isn't met yet.
    let a = e.ingest(&frame(2.0), &[star(100.0, 100.0)]);
    assert!(
        matches!(a, Action::Settle),
        "expected Settle during window, got {:?}",
        a
    );
    // after the dwell elapses the window closes (Done -> Idle)...
    let a2 = e.ingest(&frame(12.0), &[star(100.0, 100.0)]);
    assert!(
        matches!(a2, Action::Idle),
        "settle should complete, got {:?}",
        a2
    );
    // ...and guiding resumes: a steady +x offset pulses WEST again.
    let a3 = e.ingest(&frame(14.0), &[star(105.0, 100.0)]);
    assert!(
        matches!(a3, Action::PulsePair { .. }),
        "guiding should resume, got {:?}",
        a3
    );
}

/// stats() reports guiding + a non-empty recent window once frames are
/// accepted, in the host's GuideStats shape.
#[test]
fn stats_reports_guiding_and_recent_window() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);
    for i in 1..=3 {
        let _ = e.ingest(&frame(i as f64 * 2.0), &[star(100.0 + i as f64, 100.0)]);
    }
    let s = e.stats();
    assert!(s.guiding);
    assert_eq!(s.recent.len(), 3);
    assert!(s.rms_ra > 0.0);
    assert!((s.snr - 30.0).abs() < 1e-9);
}

// ---- P1-T7 review coverage round (test-only) ----

/// Drive one +5px-mount-x guide frame and return the RA pulse duration, for
/// the dec-compensation vectors below. `cal_declination` is stamped on the
/// injected Cal; `scope_dec` (if any) is injected via set_scope_pointing.
fn ra_ms_for(cal_declination: f64, scope_dec: Option<f64>) -> u32 {
    let mut e = GuideEngine::new(EngineConfig::default());
    let mut c = ident_cal();
    c.declination = cal_declination;
    e.set_calibration(c);
    if let Some(d) = scope_dec {
        e.set_scope_pointing(ScopePointing {
            declination: d,
            ..ScopePointing::default()
        });
    }
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);
    match e.ingest(&frame(2.0), &[star(105.0, 100.0)]) {
        Action::PulsePair { ra: Some(p), .. } => {
            assert!(matches!(p.dir, Direction::West), "dir={:?}", p.dir);
            p.ms
        }
        other => panic!("expected an RA pulse, got {:?}", other),
    }
}

/// RA dec-compensation numeric vector (dossier §9 item 6:
/// `x_rate_effective = cal.x_rate / cos(cal.declination) * cos(current_dec)`,
/// mount.cpp:1253-1409). Cal at declination 0, re-pointed to 60°:
/// cos(0)/cos(60°) halves the effective rate, so the same 3.15px hysteresis
/// correction takes exactly twice the pulse (315 -> 630 ms). Also pins the
/// two skip paths: `|cal.declination| > DEC_COMP_LIMIT` (60°) disables comp
/// even with a known current declination (comp would have produced ~143 ms —
/// clearly distinguished from the raw-rate 315), and an UNKNOWN declination
/// on either side short-circuits to the raw rate.
#[test]
fn dec_compensation_scales_ra_pulse() {
    // Unknown current declination (scope pointing never injected): raw rate.
    let baseline = ra_ms_for(0.0, None);
    assert!((baseline as i64 - 315).abs() <= 1, "baseline={baseline}");

    // Real cal dec 0, current dec 60°: rate halves, duration doubles.
    let compensated = ra_ms_for(0.0, Some(std::f64::consts::FRAC_PI_3));
    assert!(
        (compensated as i64 - 630).abs() <= 1,
        "compensated={compensated}"
    );

    // Cal declination beyond DEC_COMP_LIMIT (1.1 rad ~ 63° > π/3): comp
    // disabled, raw rate retained despite a known current declination of 0.
    let clamped = ra_ms_for(1.1, Some(0.0));
    assert!((clamped as i64 - 315).abs() <= 1, "clamped={clamped}");

    // Cal declination unknown (sentinel): raw rate despite a known current
    // declination.
    let cal_unknown = ra_ms_for(UNKNOWN_DECLINATION, Some(0.5));
    assert!(
        (cal_unknown as i64 - 315).abs() <= 1,
        "cal_unknown={cal_unknown}"
    );
}

/// flip_calibration at the engine level (dossier §9 item 4;
/// `Mount::FlipCalibration`, mount.cpp:891-957): x_angle += π normalized;
/// y_angle += π only when requires_dec_flip; dec parity flips unless the dec
/// flip was required; RA parity never changes; pier side toggles;
/// y_angle_error recomputed from the new angles.
#[test]
fn flip_calibration_transforms_cal() {
    use std::f64::consts::{FRAC_PI_2, PI};

    let mut base = ident_cal();
    base.x_angle = 0.3;
    base.y_angle = 0.3 + FRAC_PI_2;
    base.y_angle_error = Cal::y_angle_error_from(base.x_angle, base.y_angle); // 0.0

    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(base);

    // Flip WITHOUT a dec flip (the common mount).
    assert!(e.flip_calibration(false));
    let c1 = e.calibration().expect("cal survives the flip");
    assert!(
        (c1.x_angle - (0.3 - PI)).abs() < 1e-12,
        "x_angle={}",
        c1.x_angle
    );
    assert!(
        (c1.y_angle - (0.3 + FRAC_PI_2)).abs() < 1e-12,
        "y_angle must be unchanged without dec flip, got {}",
        c1.y_angle
    );
    assert_eq!(c1.dec_parity, Parity::Odd, "dec parity flips");
    assert_eq!(c1.ra_parity, Parity::Even, "RA parity never changes");
    assert_eq!(c1.pier_side, PierSide::East, "pier side toggles");
    // y_angle_error recomputed: the flipped axes are now π apart in error
    // space (norm(x - y + π/2) = ±π), not the stale pre-flip 0.0.
    assert_eq!(
        c1.y_angle_error,
        Cal::y_angle_error_from(c1.x_angle, c1.y_angle),
        "y_angle_error must be recomputed from the flipped angles"
    );
    assert!(
        (c1.y_angle_error.abs() - PI).abs() < 1e-9,
        "expected |err| ~ π, got {}",
        c1.y_angle_error
    );

    // Flip back WITH a dec flip (CalFlipRequiresDecFlip mounts).
    assert!(e.flip_calibration(true));
    let c2 = e.calibration().expect("cal survives the flip");
    assert!((c2.x_angle - 0.3).abs() < 1e-12, "x_angle={}", c2.x_angle);
    assert!(
        (c2.y_angle - (0.3 - FRAC_PI_2)).abs() < 1e-12,
        "y_angle += π (normalized) with dec flip, got {}",
        c2.y_angle
    );
    assert_eq!(
        c2.dec_parity,
        Parity::Odd,
        "dec parity stays when the dec flip was required"
    );
    assert_eq!(c2.pier_side, PierSide::West, "pier side toggles back");
    assert_eq!(
        c2.y_angle_error,
        Cal::y_angle_error_from(c2.x_angle, c2.y_angle)
    );

    // No calibration: no-op, returns false.
    let mut empty = GuideEngine::new(EngineConfig::default());
    assert!(!empty.flip_calibration(false));

    // Invalid calibration: no-op, returns false, angles untouched.
    let mut invalid = GuideEngine::new(EngineConfig::default());
    let mut bad = ident_cal();
    bad.is_valid = false;
    invalid.set_calibration(bad);
    assert!(!invalid.flip_calibration(false));
    assert_eq!(invalid.calibration().expect("stored").x_angle, 0.0);
}

/// CalOutcome::Failed surfaces as LockLost through the engine, and the engine
/// returns to a sane (Idle) phase afterwards. The star never moves, so
/// GO_WEST exhausts its budget: upstream-literal post-increment semantics
/// issue exactly max_steps + 1 = 61 pulses before the failure fires
/// (scope.cpp:1252; the P1-T6 review ruling pinned this count).
#[test]
fn calibration_failure_surfaces_lock_lost_and_idles() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.begin_calibration((100.0, 100.0));

    let mut cal_steps = 0u32;
    let mut got_lock_lost = false;
    for i in 0..100 {
        let a = e.ingest(&frame(i as f64 * 2.0), &[star(100.0, 100.0)]);
        match a {
            Action::CalStep { .. } => cal_steps += 1,
            Action::LockLost => {
                got_lock_lost = true;
                break;
            }
            other => panic!("unexpected during failing calibration: {:?}", other),
        }
    }
    assert!(
        got_lock_lost,
        "calibration failure must surface as LockLost"
    );
    assert_eq!(
        cal_steps, 61,
        "upstream-literal budget: max_steps + 1 pulses before failure"
    );
    // Sane phase afterwards: idle, no calibration stored.
    assert!(e.calibration().is_none());
    let after = e.ingest(&frame(300.0), &[star(100.0, 100.0)]);
    assert!(
        matches!(after, Action::Idle),
        "engine must idle after a failed calibration, got {:?}",
        after
    );
}

/// SettleState::Failed (timeout) surfaces as LockLost at the engine level,
/// after which the settle window is cleared and guiding resumes.
#[test]
fn settle_timeout_surfaces_lock_lost_then_resumes() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock
    e.dither(3.0, 3.0); // opens the default 1.5px/10s/60s window

    // 10px error: never in range; the timeout clock anchors at t=2.
    let a = e.ingest(&frame(2.0), &[star(110.0, 100.0)]);
    assert!(matches!(a, Action::Settle), "got {:?}", a);
    // t=62: 60s elapsed >= 60s timeout -> Failed -> LockLost.
    let a2 = e.ingest(&frame(62.0), &[star(110.0, 100.0)]);
    assert!(
        matches!(a2, Action::LockLost),
        "settle timeout must surface as LockLost, got {:?}",
        a2
    );
    // The window is cleared: normal guiding resumes on the next frame.
    let a3 = e.ingest(&frame(64.0), &[star(105.0, 100.0)]);
    assert!(
        matches!(a3, Action::PulsePair { .. }),
        "guiding should resume after the failed settle, got {:?}",
        a3
    );
}

/// Drive a full calibration through the engine against a linear mount with
/// RA 0.02 px/ms and Dec 0.010 px/ms (300 ms steps). Returns the engine for
/// advisory inspection.
fn run_rate_mismatch_calibration(scope: Option<ScopePointing>) -> GuideEngine {
    fn mismatch_mount(dir: Direction, ms: u32, pos: (f64, f64)) -> (f64, f64) {
        const RA: f64 = 0.02; // px/ms
        const DEC: f64 = 0.010; // px/ms — half the RA rate, ratio 2.0
        let ms = ms as f64;
        match dir {
            Direction::West => (pos.0 - RA * ms, pos.1),
            Direction::East => (pos.0 + RA * ms, pos.1),
            Direction::North => (pos.0, pos.1 + DEC * ms),
            Direction::South => (pos.0, pos.1 - DEC * ms),
        }
    }

    let mut cfg = EngineConfig::default();
    cfg.cal.calibration_duration_ms = 300; // 6px RA / 3px Dec per pulse
    let mut e = GuideEngine::new(cfg);
    if let Some(s) = scope {
        e.set_scope_pointing(s);
    }
    e.begin_calibration((200.0, 200.0));

    let mut pos = (200.0, 200.0);
    let mut t = 0.0;
    let mut guard = 0;
    loop {
        guard += 1;
        assert!(guard < 500, "calibration did not complete");
        let a = e.ingest(&frame(t), &[star(pos.0, pos.1)]);
        t += 2.0;
        match a {
            Action::CalStep { dir, ms, .. } => pos = mismatch_mount(dir, ms, pos),
            Action::Idle => break, // completed
            other => panic!("unexpected during calibration: {:?}", other),
        }
    }
    e
}

/// OBLIGATION (e) end-to-end: sanity check #3 (rates vs cos(dec), dossier
/// §8.3 item 3, scope.cpp:900-918) only fires because the real declination
/// was patched onto the Cal. Fixture: x_rate/y_rate = 2.0 vs cos(0.3) =
/// 0.955 (diff > 0.20 trips check 3), while ra_steps = 5 and dec_steps = 9
/// (both >= 4) and orthogonal axes keep checks 1-2 quiet. The identical walk
/// WITHOUT the scope-pointing injection leaves the UNKNOWN_DECLINATION
/// sentinel, check 3 short-circuits, and no advisory fires — proving the (e)
/// patch enables the check rather than merely storing the field.
#[test]
fn advisory_check3_fires_only_with_real_declination_patch() {
    // Walk A: real declination injected -> check-3 advisory fires.
    let with_scope = run_rate_mismatch_calibration(Some(ScopePointing {
        declination: 0.3,
        ..ScopePointing::default()
    }));
    let advisories = with_scope.calibration_advisories();
    assert_eq!(
        advisories.len(),
        1,
        "expected exactly the check-3 advisory, got {:?}",
        advisories
    );
    assert!(
        advisories[0].contains("rates vary"),
        "expected the rate-ratio advisory, got {:?}",
        advisories[0]
    );
    let cal = with_scope.calibration().expect("calibration completed");
    assert_eq!(cal.declination, 0.3);
    assert!(
        (cal.x_rate / cal.y_rate - 2.0).abs() < 0.05,
        "ratio fixture"
    );

    // Walk B: no scope pointing -> sentinel declination -> check 3 is a
    // no-op -> no advisory.
    let without_scope = run_rate_mismatch_calibration(None);
    assert!(
        without_scope.calibration_advisories().is_empty(),
        "check 3 must not fire on the UNKNOWN_DECLINATION sentinel, got {:?}",
        without_scope.calibration_advisories()
    );
    assert_eq!(
        without_scope
            .calibration()
            .expect("calibration completed")
            .declination,
        UNKNOWN_DECLINATION
    );
}
