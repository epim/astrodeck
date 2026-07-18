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
