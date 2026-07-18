// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for static backlash compensation (BLC) in
// astro-guide's guide engine (src/engine.rs `apply_move`). Derived from PHD2
// `backlash_comp.cpp:371-583` (`BacklashComp::ApplyBacklashComp`: the static
// direction-reversal seed pulse) via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md §10.1 (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2.
//
// Static BLC (D4): on a Dec direction REVERSAL the engine adds a fixed
// `blc_pulse_ms` seed to the reversing Dec pulse; a same-direction correction
// adds nothing; `blc_pulse_ms = 0` (PHD2's shipped default) disables it. The
// ADAPTIVE size controller (dossier §10.2) is explicitly NOT implemented (D4):
// `static_blc_is_invariant_across_reversals` pins that the added amount never
// changes with residual error — a hard behavioural proof that no adaptive
// state exists or engages.
//
// The `let mut cfg = EngineConfig::default(); cfg.field = ...` shape is the
// crate's pinned-vector style; the allow keeps clippy's
// `field_reassign_with_default` off these verbatim fixtures under `-D warnings`.
#![allow(clippy::field_reassign_with_default)]

use astro_guide::engine::{AlgoKind, EngineConfig, GuideEngine};
use astro_guide::transforms::{Cal, Parity, PierSide};
use astro_guide::types::{Action, Direction, FrameMeta, MeasuredStar};

/// Identity calibration: camera == mount, 0.01 px/ms both axes (so a Dec pulse
/// duration is exactly `|correction_px| / 0.01` before any BLC seed).
fn ident_cal() -> Cal {
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

/// Drive the engine (RA + Dec both Hysteresis — Hysteresis is deterministic and
/// always issues, so Dec reversals are clean, unlike ResistSwitch which vetoes
/// them) with `blc_pulse_ms = blc` over a script of Dec offsets from the lock
/// (X held at the lock, so no RA pulse ever fires). Returns the per-frame Dec
/// pulse `(dir, ms)` (or `None` when the Dec axis is idle that frame).
///
/// Because the star positions are SCRIPTED (not a closed loop off the mount
/// response), the Dec algorithm sees identical inputs for any `blc` — so its
/// pre-BLC correction is identical frame-for-frame across two `blc` values, and
/// the ONLY difference between a `blc = 0` run and a `blc = N` run is the `+N`
/// seed the reversal frames carry. That makes the reversal delta EXACT.
fn dec_pulses(blc: u32, dec_offsets: &[f64]) -> Vec<Option<(Direction, u32)>> {
    let mut cfg = EngineConfig::default();
    cfg.blc_pulse_ms = blc; // Dec mode Auto (default) — BLC's precondition
    cfg.ra_algorithm = AlgoKind::Hysteresis;
    cfg.dec_algorithm = AlgoKind::Hysteresis;
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();

    // Lock frame: establishes the lock at (100, 100); issues no correction.
    let _ = e.ingest(&frame(0.0), &[star(100.0, 100.0)]);

    let mut out = Vec::with_capacity(dec_offsets.len());
    let mut t = 0.0;
    for &dy in dec_offsets {
        t += 2.0;
        let a = e.ingest(&frame(t), &[star(100.0, 100.0 + dy)]);
        out.push(match a {
            Action::PulsePair { dec: Some(p), .. } => Some((p.dir, p.ms)),
            Action::PulsePair { dec: None, .. } | Action::Idle => None,
            other => panic!("unexpected action {other:?}"),
        });
    }
    out
}

fn dir_ms(p: Option<(Direction, u32)>) -> (Direction, u32) {
    p.expect("expected a Dec pulse this frame")
}

/// A Dec direction reversal adds EXACTLY `blc_pulse_ms` to the reversing pulse;
/// the first Dec move (no prior direction) and a same-direction move add
/// nothing. Offsets +5 → −5 → −5 give South, then North (reversal), then North
/// (same direction). Hand trace (Hysteresis h=0.1, aggr=0.7; 0.01 px/ms):
///   f0 input +5: r = (0.9·5 + 0.1·0)·0.7 = 3.15 px → 315 ms SOUTH (first move)
///   f1 input −5: r = (0.9·−5 + 0.1·3.15)·0.7 = −2.9295 px → 293 ms NORTH (rev)
///   f2 input −5: r = (0.9·−5 + 0.1·−2.9295)·0.7 = −3.355 px → 336 ms NORTH
/// (`backlash_comp.cpp:371-583`; dossier §10.1.)
#[test]
fn blc_reversal_adds_exact_pulse() {
    let base = dec_pulses(0, &[5.0, -5.0, -5.0]);
    let blc = dec_pulses(200, &[5.0, -5.0, -5.0]);

    // f0 — first Dec move: SOUTH 315 ms, no BLC (no prior direction to reverse).
    assert_eq!(dir_ms(base[0]), (Direction::South, 315));
    assert_eq!(
        dir_ms(blc[0]),
        (Direction::South, 315),
        "first move never seeds"
    );

    // f1 — REVERSAL (North after South): base 293 ms, seeded run 293 + 200 = 493.
    let (bdir, bms) = dir_ms(base[1]);
    let (sdir, sms) = dir_ms(blc[1]);
    assert_eq!(bdir, Direction::North);
    assert_eq!(sdir, Direction::North);
    assert_eq!(bms, 293, "un-seeded reversal base ms");
    assert_eq!(sms, 493, "reversal ms = base 293 + BLC 200");
    assert_eq!(sms - bms, 200, "the reversal delta is EXACTLY blc_pulse_ms");

    // f2 — SAME direction (North after North): no seed, both runs identical.
    let (_, bms2) = dir_ms(base[2]);
    let (dir2, sms2) = dir_ms(blc[2]);
    assert_eq!(dir2, Direction::North);
    assert_eq!(bms2, 336, "same-direction base ms");
    assert_eq!(sms2, bms2, "same-direction move adds nothing");
    assert_eq!(sms2 - bms2, 0);
}

/// `blc_pulse_ms = 0` (PHD2's shipped default) disables BLC entirely: a Dec
/// reversal carries only the pure algorithm base, never a seed. (dossier §10.1;
/// `EngineConfig::default().blc_pulse_ms == 0`.)
#[test]
fn blc_zero_disables_entirely() {
    assert_eq!(
        EngineConfig::default().blc_pulse_ms,
        0,
        "shipped default is 0"
    );
    let out = dec_pulses(0, &[5.0, -5.0]);
    assert_eq!(dir_ms(out[0]), (Direction::South, 315));
    // The reversal is the pure base with NO +200 seed.
    assert_eq!(dir_ms(out[1]), (Direction::North, 293));
}

/// A same-direction Dec correction never seeds, however large `blc_pulse_ms`
/// is: two SOUTH moves in a row (offsets +5 → +7) — the second adds nothing.
#[test]
fn blc_same_direction_adds_nothing() {
    let base = dec_pulses(0, &[5.0, 7.0]);
    let blc = dec_pulses(500, &[5.0, 7.0]);
    assert_eq!(dir_ms(base[0]).0, Direction::South);
    assert_eq!(dir_ms(blc[0]), dir_ms(base[0]), "first move never seeds");
    // Second SOUTH move: same direction, so no seed even at blc_pulse_ms=500.
    assert_eq!(dir_ms(base[1]).0, Direction::South);
    assert_eq!(
        dir_ms(blc[1]),
        dir_ms(base[1]),
        "same direction adds nothing"
    );
}

/// D4 — STATIC ONLY: the adaptive size controller (dossier §10.2) is NOT
/// implemented and no adaptive state exists. Behavioural proof: across a run of
/// repeated Dec reversals of identical magnitude, EVERY reversal adds exactly
/// the same fixed seed (200 ms) — the amount never grows, shrinks, or adapts to
/// residual error the way an adaptive backlash controller would. Same-direction
/// frames add nothing throughout.
#[test]
fn static_blc_is_invariant_across_reversals() {
    // Alternating offsets: South, North(rev), South(rev), North(rev), ... —
    // every frame after the first is a reversal.
    let offsets = [5.0, -5.0, 5.0, -5.0, 5.0, -5.0, 5.0, -5.0];
    let base = dec_pulses(0, &offsets);
    let blc = dec_pulses(200, &offsets);

    // First move never seeds; every subsequent (reversal) frame adds EXACTLY
    // 200 ms — a constant, never an adapted amount.
    for (i, (b, s)) in base.iter().zip(blc.iter()).enumerate() {
        let (bdir, bms) = dir_ms(*b);
        let (sdir, sms) = dir_ms(*s);
        assert_eq!(bdir, sdir, "direction must match at frame {i}");
        let delta = sms as i64 - bms as i64;
        if i == 0 {
            assert_eq!(delta, 0, "first move never seeds (frame {i})");
        } else {
            assert_eq!(
                delta, 200,
                "every reversal adds a CONSTANT 200 ms seed, not an adapted \
                 amount (frame {i}: base {bms}, seeded {sms})"
            );
        }
    }
}
