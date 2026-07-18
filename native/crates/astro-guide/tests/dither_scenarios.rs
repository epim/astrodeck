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

use astro_guide::calibration::DecMode;
use astro_guide::engine::{AlgoKind, EngineConfig, GuideEngine};
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

    // A persistent 5px offset for several frames (the star is SCRIPTED to
    // stay put — an open-loop fixture): the fast EMA (alpha 0.3) climbs to
    // (and, for a constant series, stays at) 5.0px -- comfortably outside
    // the default 1.5px tolerance. Since the P2-T1 fix round these frames
    // also carry the dwell's ordinary guide corrections
    // (guider.cpp:1517-1521) — the settle state is read from is_settling(),
    // not the Action shape.
    for i in 1..=5 {
        let a = e.ingest(&frame(i as f64 * 2.0), &[star(105.0, 100.0)]);
        assert!(
            matches!(a, Action::PulsePair { .. }),
            "frame {i}: the dwell must keep guiding, got {:?}",
            a
        );
        assert!(e.is_settling(), "frame {i}: must still be settling");
    }

    // ONE frame lands exactly at the lock (0px raw offset) before the next
    // assertion would resume the 5px streak. Fed the RAW instantaneous
    // offset (the P1-T7 review finding), this alone would satisfy the
    // 1.5px tolerance and settle; fed the SMOOTHED avg_dist (still ~3.5px
    // after one EMA step down from 5.0 -- `5.0 + 0.3*(0-5.0) = 3.5`), it
    // must not. (This frame's own correction is min-move-vetoed — raw
    // input 0 — so its Action is the Settle wait signal.)
    let lucky = e.ingest(&frame(12.0), &[star(100.0, 100.0)]);
    assert!(
        matches!(lucky, Action::Settle),
        "a single lucky in-tolerance frame must not settle (P2-T1 punch-list #1), got {:?}",
        lucky
    );
    assert!(
        e.is_settling(),
        "the window must still be open after the lucky frame"
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

// ---- P2-T1 fix round (opus review: 1 Critical + 1 Important + 1 Minor) ----

/// P2-T1 fix round, CRITICAL — guide DURING the settle dwell: upstream keeps
/// the Guider in STATE_GUIDING issuing ordinary MOVEOPTS_GUIDE_STEP
/// corrections on every settling frame (guider.cpp:1517-1521) — settle is a
/// parallel MONITOR, not a phase that suspends guiding. Closed-loop witness:
/// with real drift (~0.5 px/s) across the 10 s dwell, settle can only
/// complete if corrections keep flowing; the pre-fix engine (bare
/// Action::Settle for the whole dwell, zero corrections) drifts out of the
/// 1.5 px tolerance by ~t=5 s and can never re-enter it, so it times out.
#[test]
fn drift_during_dwell_settles_only_with_active_guiding() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(0.0, 0.0); // settle-only window (no recenter): isolates the dwell

    // Closed loop: the star drifts +0.5 px/s in x; every returned RA pulse
    // moves it back by ms * x_rate (0.01 px/ms), like a real mount would.
    // Steady state under guiding: err ~0.74 px (hysteresis fixed point for a
    // 0.5 px/frame drift), comfortably inside the 1.5 px tolerance, so the
    // dwell runs 10 s continuously in-range and completes.
    let mut star_x = 100.0_f64;
    let mut pulses = 0u32;
    let mut settled_at = None;
    for i in 1..=45 {
        let t = i as f64; // 1 s frame cadence
        star_x += 0.5; // drift accumulated since the previous frame
        let a = e.ingest(&frame(t), &[star(star_x, 100.0)]);
        match &a {
            Action::PulsePair { ra, .. } => {
                if let Some(p) = ra {
                    let px = p.ms as f64 * 0.01;
                    match p.dir {
                        Direction::West => star_x -= px,
                        Direction::East => star_x += px,
                        _ => {}
                    }
                    pulses += 1;
                }
            }
            Action::Settle | Action::Idle => {}
            other => panic!("unexpected {:?} at t={}", other, t),
        }
        if !e.is_settling() {
            settled_at = Some(t);
            break;
        }
    }
    assert!(
        settled_at.is_some(),
        "settle must COMPLETE under real drift — only possible when guide \
         corrections keep flowing during the dwell (guider.cpp:1517-1521)"
    );
    assert!(
        pulses > 0,
        "corrections must flow during the dwell, got none"
    );
}

/// P2-T1 fix round, IMPORTANT — recovery moves are unclamped: recenter
/// pulses are RECOVERY moves (MOVEOPTS_RECOVERY_MOVE = MOVEOPT_USE_BLC
/// alone, mount.h:139), and upstream applies the max-duration clamps ONLY
/// inside the `moveOptions & (MOVEOPT_ALGO_RESULT | MOVEOPT_ALGO_DEDUCE)`
/// guard (scope.cpp:761-768; dec twin :736-741) — the deliberately large
/// recenter steps go out unclamped. An ALGO correction during the same
/// dwell still clamps.
#[test]
fn recenter_pulses_bypass_duration_clamps_algo_moves_still_clamp() {
    let mut cfg = EngineConfig::default();
    cfg.max_ra_duration_ms = 500; // 5 px at the 0.01 px/ms test rate
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(20.0, 0.0); // recenter step 0.7*15 = 10.5 px = 1050 ms > clamp

    // Two recenter frames: 10.5 px then 9.5 px — both above the 500 ms ALGO
    // clamp threshold, both must go out UNCLAMPED, and the px accounting
    // stays honest (remaining 20 -> 9.5 -> 0, i.e. 1050 ms then 950 ms).
    for (t, want_ms) in [(2.0, 1050u32), (4.0, 950u32)] {
        let a = e.ingest(&frame(t), &[star(100.0, 100.0)]);
        match a {
            Action::PulsePair { ra: Some(p), .. } => {
                assert_eq!(p.dir, Direction::East, "t={t}: dir={:?}", p.dir);
                assert_eq!(
                    p.ms, want_ms,
                    "t={t}: recovery pulse must be unclamped (scope.cpp:761)"
                );
            }
            other => panic!("t={t}: expected a recenter PulsePair, got {:?}", other),
        }
    }

    // Recenter exhausted. The dwell's ordinary guide correction (ALGO) for a
    // large error still clamps: star 25 px west of the new lock (120,100)
    // -> hysteresis (0.9*25)*0.7 = 15.75 px = 1575 ms -> clamped to 500.
    let a = e.ingest(&frame(6.0), &[star(95.0, 100.0)]);
    match a {
        Action::PulsePair { ra: Some(p), .. } => {
            assert_eq!(p.dir, Direction::East, "dir={:?}", p.dir);
            assert_eq!(p.ms, 500, "ALGO move during the dwell must still clamp");
        }
        other => panic!("expected the clamped ALGO PulsePair, got {:?}", other),
    }
}

/// P2-T1 fix round, MINOR — dec-mode handling during a dither (both halves):
/// (1) a recovery recenter dec pulse bypasses dec-mode gating (the same
/// scope.cpp:727 ALGO-only guard), so a SOUTH recenter pulse fires even in
/// DEC_NORTH mode; (2) the dwell's ordinary guide corrections run under the
/// temporary DEC_AUTO override upstream applies for the whole dither settle
/// (dossier §12 note; phdcontrol.cpp:181-187 arms it, :501-505 sets
/// DEC_AUTO, :570-575 restores), so the ALGO dec correction ALSO fires
/// South while the window is open — and normal DEC_NORTH gating returns
/// once the window closes.
#[test]
fn dec_north_mode_dither_souths_recenter_and_dwell_corrections() {
    let mut cfg = EngineConfig::default();
    cfg.dec_guide_mode = DecMode::North;
    cfg.dec_algorithm = AlgoKind::Hysteresis; // deterministic first-move dec
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]); // lock (100,100)
    e.dither(0.0, -5.0); // lock -> (100,95); recenter must pulse SOUTH

    // (1) the one-step recenter fires SOUTH despite DecMode::North.
    let a1 = e.ingest(&frame(2.0), &[star(100.0, 100.0)]);
    match a1 {
        Action::PulsePair { ra, dec: Some(p) } => {
            assert!(ra.is_none(), "a pure-dec dither must not pulse RA");
            assert_eq!(
                p.dir,
                Direction::South,
                "recovery move bypasses dec-mode gating (scope.cpp:727)"
            );
            assert_eq!(p.ms, 500);
        }
        other => panic!("expected the South recenter pulse, got {:?}", other),
    }

    // (2) the dwell's ALGO correction also fires South (temporary DEC_AUTO):
    // star still 5 px south-of-lock -> hysteresis (0.9*5)*0.7 = 3.15 px.
    let a2 = e.ingest(&frame(4.0), &[star(100.0, 100.0)]);
    match a2 {
        Action::PulsePair { dec: Some(p), .. } => {
            assert_eq!(
                p.dir,
                Direction::South,
                "dwell corrections use DEC_AUTO semantics (dossier §12)"
            );
            assert_eq!(p.ms, 315);
        }
        other => panic!("expected the DEC_AUTO dwell correction, got {:?}", other),
    }

    // Let the star reach the new lock; the smoothed error decays into
    // tolerance (5 -> 3.5 -> 2.45 -> 1.72 -> 1.2) and the dwell completes.
    for t in [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0] {
        let a = e.ingest(&frame(t), &[star(100.0, 95.0)]);
        assert!(
            matches!(a, Action::Settle | Action::Idle),
            "t={t}: got {:?}",
            a
        );
    }
    let done = e.ingest(&frame(22.0), &[star(100.0, 95.0)]);
    assert!(matches!(done, Action::Idle), "got {:?}", done);
    assert!(!e.is_settling(), "window must be closed");

    // Window closed: DEC_NORTH gating is BACK — a south error draws no pulse.
    let gated = e.ingest(&frame(24.0), &[star(100.0, 97.0)]);
    assert!(
        matches!(gated, Action::Idle),
        "DEC_NORTH gating must be restored after settle (phdcontrol.cpp:570-575), got {:?}",
        gated
    );
}
