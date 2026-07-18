// SPDX-License-Identifier: Apache-2.0
//
// Provenance: scripted engine-walk golden vectors for astro-guide's real
// dither + fast-recenter + settle behavior (src/engine.rs, src/settle.rs,
// src/track.rs). Derived from PHD2 `guider.cpp:838-929`
// (`Guider::MoveLockPosition`: the dither lock-shift + average-distance
// inflate + fast-recenter arming), `guider.cpp:1485-1511` (the per-frame
// fast-recenter step, bypassing the guide algorithms), and
// `phdcontrol.cpp:514-567` (`PhdController::UpdateControllerState`
// STATE_SETTLE_WAIT) via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§11/§12/§13)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.
//
// This is the P2-T1 task's brief-mandated dither_scenarios.rs: the primary
// vector (recenter -> settle -> resume, algorithms reset) plus two targeted
// regressions for the P1-T7/T10 review's P2 punch-list items #1 (settle
// input fix: feed the SMOOTHED avg_dist, not a frame's raw offset) and #2
// (a fresh AvgDist at the post-fast-recenter boundary), plus a zero-distance
// dither edge case (dossier §11.2's fast-recenter div-by-zero guard).
//
// The `let mut cfg = EngineConfig::default(); cfg.field = ...` shape below
// matches engine_scenarios.rs's established pinned-vector style; the
// crate-level allow keeps clippy's `field_reassign_with_default` from
// rejecting it under the `-D warnings` gate.
#![allow(clippy::field_reassign_with_default)]

use astro_guide::engine::{EngineConfig, GuideEngine};
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

fn frame(t: f64) -> FrameMeta {
    FrameMeta {
        timestamp_s: t,
        exposure_s: 2.0,
    }
}

/// The brief's primary vector: after `dither(5.0, 0.0)` the lock shifts, the
/// axis algorithms reset (`last_move == 0`, proven black-box below), the
/// next few frames emit fast-recenter pulses (dossier §11.2), and
/// `evaluate()` reports `Settle` until the star is within tolerance for the
/// settle time, then guiding resumes (dossier §12). A small `search_region`
/// (3px) makes even this literal 5px dither need multiple recenter steps
/// (step size `0.7*3=2.1px`: `5.0 -> 2.9 -> 0.8 -> 0`), matching the "next
/// FEW frames" framing.
#[test]
fn dither_recenters_then_settles_then_resumes() {
    let mut cfg = EngineConfig::default();
    cfg.find.search_region = 3;
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(5.0, 0.0); // new lock (105,100); GuidingDithered -> algorithms reset

    // Fast recenter (dossier §11.2): 3 frames of a direct, open-loop East
    // pulse. The scripted star position never changes during these frames
    // and yet they still pulse with a shrinking, pre-planned magnitude --
    // proof the moves are NOT algorithm-driven (a Hysteresis/ResistSwitch
    // result would depend on the star's actual measured offset, not a fixed
    // step schedule).
    for (i, want_ms) in [(1u32, 210u32), (2, 210), (3, 80)] {
        let a = e.ingest(&frame(i as f64 * 2.0), &[star(100.0, 100.0)]);
        match a {
            Action::PulsePair { ra: Some(p), dec } => {
                assert_eq!(p.dir, Direction::East, "frame {i}: dir={:?}", p.dir);
                assert_eq!(p.ms, want_ms, "frame {i}: ms={}", p.ms);
                assert!(
                    dec.is_none(),
                    "frame {i}: an RA-only dither must not move dec"
                );
            }
            other => panic!("frame {i}: expected a recenter PulsePair, got {:?}", other),
        }
    }

    // Recenter is now exhausted (P2-T1 punch-list #2: AvgDist is fresh
    // here). The star has arrived at the new lock: an immediate in-range
    // reading starts the settle dwell clock right away.
    let a4 = e.ingest(&frame(8.0), &[star(105.0, 100.0)]);
    assert!(matches!(a4, Action::Settle), "got {:?}", a4);
    let a5 = e.ingest(&frame(18.0), &[star(105.0, 100.0)]); // +10s dwell
    assert!(
        matches!(a5, Action::Idle),
        "settle should complete, got {:?}",
        a5
    );

    // Guiding resumes with a FRESH algorithm state (last_move == 0, proven
    // black-box): a steady +5px offset from the NEW lock reproduces exactly
    // the P1-T1 first-move Hysteresis formula
    // ((0.9*5 + 0.1*0)*0.7 = 3.15px -> 315ms), which only holds if
    // last_move is truly 0 -- a stale nonzero last_move surviving the
    // dither would shift this.
    let a6 = e.ingest(&frame(20.0), &[star(110.0, 100.0)]);
    match a6 {
        Action::PulsePair { ra: Some(p), dec } => {
            assert_eq!(p.dir, Direction::West, "dir={:?}", p.dir);
            assert!((p.ms as i64 - 315).abs() <= 1, "ms={}", p.ms);
            assert!(dec.is_none() || dec.unwrap().ms == 0);
        }
        other => panic!("expected guiding to resume, got {:?}", other),
    }
}

/// P2-T1 punch-list #1 (SETTLE INPUT FIX, binding per the P1-T7 review): the
/// settle evaluator must be fed the SMOOTHED fast-EMA `avg_dist`
/// (`current_error`), never a frame's raw instantaneous offset -- a single
/// lucky in-tolerance frame must not declare settled. A zero-distance
/// dither (dossier §11.2's fast-recenter div-by-zero guard) isolates this
/// from the recenter machinery: settling starts immediately on the
/// unmoved lock.
#[test]
fn settle_uses_smoothed_error_a_single_lucky_frame_does_not_settle() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(0.0, 0.0); // zero-distance dither: settle-only, no recenter

    // A persistent 5px offset for several frames: the fast EMA (alpha 0.3)
    // climbs to (and, for a constant series, stays at) 5.0px -- comfortably
    // outside the default 1.5px tolerance.
    for i in 1..=5 {
        let a = e.ingest(&frame(i as f64 * 2.0), &[star(105.0, 100.0)]);
        assert!(matches!(a, Action::Settle), "frame {i}: got {:?}", a);
    }

    // ONE frame lands exactly at the lock (0px raw offset) before the next
    // assertion would resume the 5px streak. Fed the RAW instantaneous
    // offset (the P1-T7 review finding), this alone would satisfy the
    // 1.5px tolerance and settle; fed the SMOOTHED avg_dist (still ~3.5px
    // after one EMA step down from 5.0 -- `5.0 + 0.3*(0-5.0) = 3.5`), it
    // must not.
    let lucky = e.ingest(&frame(12.0), &[star(100.0, 100.0)]);
    assert!(
        matches!(lucky, Action::Settle),
        "a single lucky in-tolerance frame must not settle (P2-T1 punch-list #1), got {:?}",
        lucky
    );
}

/// P2-T1 punch-list #2 (fresh AvgDist at the post-fast-recenter boundary):
/// after recenter finishes, the very next frame's `AvgDist` must be a HARD
/// reinit on that frame's true distance, not an EMA still carrying the
/// large pre-recenter transient forward. Discriminating design: a 20px
/// dither (default search_region 15px -> 2-frame recenter, step size
/// 0.7*15=10.5px: `20 -> 9.5 -> 0`) followed by 0px frames. A fresh
/// AvgDist reaches the 1.5px tolerance on the very first post-recenter
/// frame and so completes the 10s dwell in exactly one settle_time_sec
/// window; a stale, non-reset EMA (~20px, decaying at alpha 0.3 per frame)
/// would still be memoryfully above tolerance for many more frames and
/// could not possibly finish this fast.
#[test]
fn fresh_avg_dist_after_fast_recenter_lets_settle_start_immediately() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(20.0, 0.0); // new lock (120,100); 2-frame recenter (step 10.5px)

    // Two recenter frames (star not moving -- open loop, dossier §11.2).
    let r1 = e.ingest(&frame(2.0), &[star(100.0, 100.0)]);
    assert!(matches!(r1, Action::PulsePair { .. }), "got {:?}", r1);
    let r2 = e.ingest(&frame(4.0), &[star(100.0, 100.0)]);
    assert!(matches!(r2, Action::PulsePair { .. }), "got {:?}", r2);

    // Recenter is now exhausted. Star exactly at the new lock (0px raw
    // offset): only settles this fast if AvgDist was truly reconstructed.
    let s1 = e.ingest(&frame(6.0), &[star(120.0, 100.0)]);
    assert!(matches!(s1, Action::Settle), "got {:?}", s1);
    let s2 = e.ingest(&frame(16.0), &[star(120.0, 100.0)]); // +10s dwell
    assert!(
        matches!(s2, Action::Idle),
        "expected settle to complete on a fresh AvgDist, got {:?}",
        s2
    );
}

/// Zero-distance dither (dossier §11.2: "Zero-length dithers result in
/// div-by-zero in calculation of 'f'. We can still allow zero-length
/// dithers as a way of triggering a settling period if we temporarily
/// ignore the 'fast re-center' option"): recenter is skipped entirely (no
/// panic, no PulsePair), and the settle window still opens and completes
/// normally.
#[test]
fn zero_distance_dither_opens_settle_only_no_recenter() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(0.0, 0.0); // zero-length dither: settle-only, no recenter

    let a1 = e.ingest(&frame(2.0), &[star(100.0, 100.0)]); // 0px error immediately
    assert!(matches!(a1, Action::Settle), "got {:?}", a1);
    let a2 = e.ingest(&frame(12.0), &[star(100.0, 100.0)]); // +10s dwell
    assert!(matches!(a2, Action::Idle), "got {:?}", a2);
}
