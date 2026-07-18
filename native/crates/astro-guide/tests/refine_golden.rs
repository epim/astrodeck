// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's multi-star offset
// refinement port (src/refine.rs). Derived from PHD2
// guider_multistar.cpp:706-921 (`GuiderMultiStar::RefineOffset`) via the
// audited algorithm dossier docs/native-parity/algorithms/phd2-guiding.md
// (§4) (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from
// PHD2.

// Provenance: hand-derived. The dossier has no literal PHD2 test vector for
// RefineOffset (it is not covered by PHD2's own unit-test suite, which has
// no test binary for guider_multistar.cpp), so this file DERIVES a fixed
// primary + 3-secondary fixture by hand-tracing dossier §4's arithmetic
// directly (SNR-weighted sum, primary sigma, excursion/zero-count gates)
// and records the expected weighted average as a literal, per this task's
// brief.

use astro_guide::refine::{refine_offset, SecondaryStar};
use astro_guide::types::MeasuredStar;

fn found(x: f64, y: f64, snr: f64) -> MeasuredStar {
    MeasuredStar {
        x,
        y,
        snr,
        mass: 5000.0,
        hfd: 3.0,
        found: true,
    }
}

fn lost() -> MeasuredStar {
    MeasuredStar {
        x: 0.0,
        y: 0.0,
        snr: 0.0,
        mass: 0.0,
        hfd: 0.0,
        found: false,
    }
}

/// Shared fixture: a primary star plus 3 secondaries at fixed reference
/// points, all well inside the frame, `primary_sigma = 1.0` fixed
/// throughout (as if `primary_dist_stats` had already collected > 5
/// samples with that sample sigma).
fn three_secondaries() -> [SecondaryStar; 3] {
    [
        SecondaryStar::new(100.0, 100.0, 20.0, (0.0, 0.0)), // S1, same SNR as primary
        SecondaryStar::new(200.0, 50.0, 10.0, (100.0, -50.0)), // S2, half primary SNR
        SecondaryStar::new(50.0, 220.0, 5.0, (-50.0, 120.0)), // S3, quarter primary SNR
    ]
}

const PRIMARY_SNR: f64 = 20.0;
const PRIMARY_SIGMA: f64 = 1.0;

/// Hand-traced golden vector for the SNR-weighted median/average offset
/// refinement (dossier §4).
///
/// Primary raw offset: (0.6, 0.0) px (hypot 0.6).
///
/// Secondary displacements from THEIR OWN reference points this frame
/// (deliberately nonzero on BOTH axes for every secondary — an exact-zero
/// component is a SEPARATE, unrelated gate, dossier §4's "zero-counting";
/// see `zero_on_both_axes_secondary_is_flagged_erase` for that path):
///   - S1 (SNR 20): (dx, dy) = (0.4, 0.02)    -> weight 20/20 = 1.0
///   - S2 (SNR 10): (dx, dy) = (0.02, -0.6)   -> weight 10/20 = 0.5
///   - S3 (SNR 5):  (dx, dy) = (-0.2, 0.2)    -> weight 5/20  = 0.25
///
/// All three excursions are well under `2.5 * primary_sigma (1.0) = 2.5px`,
/// so all three are "usable".
///
/// By hand:
///   sum_w = 1 (primary) + 1.0 + 0.5 + 0.25 = 2.75
///   sum_x = 0.6 + 1.0*0.4 + 0.5*0.02 + 0.25*(-0.2)
///         = 0.6 + 0.4 + 0.01 - 0.05 = 0.96      -> avg_x = 0.96 / 2.75
///   sum_y = 0.0 + 1.0*0.02 + 0.5*(-0.6) + 0.25*0.2
///         = 0.02 - 0.3 + 0.05 = -0.23            -> avg_y = -0.23 / 2.75
///   avg_x = 0.349090909..., avg_y = -0.083636363...
///   refined hypot = sqrt(0.349090909^2 + 0.083636363^2)
///                 = sqrt(0.121865 + 0.006995) = sqrt(0.128860) = 0.359--
/// 0.359 < 0.6 (the primary's raw hypot) -> the average SHRINKS the
/// offset, so `refine_offset` must return `Some` with these literals.
#[test]
fn snr_weighted_average_of_three_secondaries_shrinks_offset() {
    let mut secs = three_secondaries();
    let measured = [
        found(100.4, 100.02, 20.0), // S1: dx=0.4, dy=0.02
        found(200.02, 49.4, 10.0),  // S2: dx=0.02, dy=-0.6
        found(49.8, 220.2, 5.0),    // S3: dx=-0.2, dy=0.2
    ];

    let out = refine_offset(
        (0.6, 0.0),
        &mut secs,
        &measured,
        PRIMARY_SNR,
        PRIMARY_SIGMA,
        false,
    );

    let expected_x = 0.96 / 2.75;
    let expected_y = -0.23 / 2.75;
    let (rx, ry) = out.expect("weighted average of 3 well-behaved secondaries must shrink");
    assert!(
        (rx - expected_x).abs() < 1e-9,
        "rx={rx} expected={expected_x}"
    );
    assert!(
        (ry - expected_y).abs() < 1e-9,
        "ry={ry} expected={expected_y}"
    );
    assert!(
        rx.hypot(ry) < 0.6,
        "refined offset {} must be smaller than the primary's raw offset 0.6",
        rx.hypot(ry)
    );

    // All three secondaries were used, none flagged for erasure, none
    // marked lost, and no zero-count/miss-count bookkeeping was triggered
    // (every displacement this frame was nonzero on both axes and well
    // inside the excursion gate).
    for s in &secs {
        assert!(!s.erase);
        assert!(!s.was_lost);
        assert_eq!(s.zero_count, 0);
        assert_eq!(s.miss_count, 0);
    }
}

/// One secondary (S2) jumps 2.5x-sigma+ beyond its reference point this
/// frame (dossier §4's excursion gate: `secondaryDistance > 2.5 *
/// primarySigma`). It must be SKIPPED from the weighted average entirely
/// (not merely down-weighted) and its `miss_count` incremented; the other
/// two well-behaved secondaries still refine the offset using only their
/// own contributions.
#[test]
fn secondary_excursion_beyond_2_5_sigma_is_skipped_and_miss_counted() {
    let mut secs = three_secondaries();
    let measured = [
        found(100.4, 100.0, 20.0), // S1: dx=0.4, dy=0.0 (usable)
        found(203.0, 50.1, 10.0),  // S2: dx=3.0, dy=0.1 -> hypot ~3.0017 > 2.5
        found(49.8, 220.2, 5.0),   // S3: dx=-0.2, dy=0.2 (usable)
    ];

    let out = refine_offset(
        (0.6, 0.0),
        &mut secs,
        &measured,
        PRIMARY_SNR,
        PRIMARY_SIGMA,
        false,
    );

    // sum_w = 1 + 1.0(S1) + 0.25(S3) = 2.25
    // sum_x = 0.6 + 0.4 + 0.25*(-0.2) = 0.6 + 0.4 - 0.05 = 0.95 -> /2.25
    // sum_y = 0.0 + 0.0 + 0.25*0.2 = 0.05 -> /2.25
    let expected_x = 0.95 / 2.25;
    let expected_y = 0.05 / 2.25;
    let (rx, ry) = out.expect("the two well-behaved secondaries must still shrink the offset");
    assert!(
        (rx - expected_x).abs() < 1e-9,
        "rx={rx} expected={expected_x}"
    );
    assert!(
        (ry - expected_y).abs() < 1e-9,
        "ry={ry} expected={expected_y}"
    );

    assert_eq!(secs[1].miss_count, 1, "S2's excursion must bump miss_count");
    assert!(!secs[1].erase, "a single excursion must not erase the star");
    assert_eq!(
        secs[1].reference_point,
        (200.0, 50.0),
        "reference point unchanged until miss_count > 10"
    );
    // S1/S3 unaffected.
    assert_eq!(secs[0].miss_count, 0);
    assert_eq!(secs[2].miss_count, 0);
}

/// A secondary measured EXACTLY at its own reference point on both axes
/// (dx == dy == 0.0) is treated as a probable hot pixel and erased
/// immediately (dossier §4: "a zero-on-both-axes secondary is erased").
/// `refine_offset` cannot itself shrink the caller's `Vec` (it only sees a
/// `&mut [SecondaryStar]` slice), so it signals this via
/// [`SecondaryStar::erase`] — the caller is responsible for
/// `retain(|s| !s.erase)` afterward.
#[test]
fn zero_on_both_axes_secondary_is_flagged_erase() {
    let mut secs = three_secondaries();
    let measured = [
        found(100.4, 100.0, 20.0), // S1: usable
        found(200.0, 50.0, 10.0),  // S2: EXACT match to its reference point -> hot pixel
        found(49.8, 220.2, 5.0),   // S3: usable
    ];

    let out = refine_offset(
        (0.6, 0.0),
        &mut secs,
        &measured,
        PRIMARY_SNR,
        PRIMARY_SIGMA,
        false,
    );

    assert!(out.is_some(), "S1/S3 alone still refine the offset");
    assert!(secs[1].erase, "S2 must be flagged for erasure (hot pixel)");
    assert!(!secs[0].erase);
    assert!(!secs[2].erase);
}

/// A secondary that goes missing (not found this frame) is marked
/// `was_lost` and simply excluded from the average — not erased, not
/// miss-counted (dossier §4's separate "L" outcome).
#[test]
fn missing_secondary_marked_was_lost_and_excluded() {
    let mut secs = three_secondaries();
    let measured = [
        found(100.4, 100.0, 20.0), // S1: usable
        lost(),                    // S2: not found
        found(49.8, 220.2, 5.0),   // S3: usable
    ];

    let out = refine_offset(
        (0.6, 0.0),
        &mut secs,
        &measured,
        PRIMARY_SNR,
        PRIMARY_SIGMA,
        false,
    );

    assert!(out.is_some());
    assert!(secs[1].was_lost);
    assert!(!secs[1].erase);
    assert_eq!(secs[1].miss_count, 0);
}

/// The stabilization gate: while the caller's running primary-distance
/// statistics have collected 5 or fewer samples, `refine_offset` must
/// return `None` regardless of how favorable the secondary geometry is
/// (dossier §4: `if primary_dist_stats.count() <= 5 { stabilizing = true }`
/// then `if stabilizing { return false }`). This crate's frozen
/// `refine_offset` signature has no sample-count parameter, so a caller
/// signals "5 or fewer samples collected" via a negative `primary_sigma`
/// sentinel (see `refine.rs`'s module doc) — a real standard deviation is
/// never negative. This test drives that sentinel directly across a
/// simulated 5-frame ramp before "enough samples" (a real sigma) unlocks
/// refinement, using the SAME favorable 3-secondary geometry the first
/// test proved shrinks the offset once unlocked.
#[test]
fn stabilization_gate_returns_none_until_more_than_five_samples_collected() {
    let mut secs = three_secondaries();
    let measured = [
        found(100.4, 100.0, 20.0),
        found(200.0, 49.4, 10.0),
        found(49.8, 220.2, 5.0),
    ];

    // Frames 1-5: caller's primary_dist_stats.count() is 1..=5 -> "still
    // collecting" -> sentinel sigma -> None every time, and no secondary
    // bookkeeping is touched (the gate returns before the per-star loop).
    for frame in 1..=5 {
        let out = refine_offset((0.6, 0.0), &mut secs, &measured, PRIMARY_SNR, -1.0, false);
        assert_eq!(out, None, "frame {frame}: must stay gated with < 6 samples");
        for s in &secs {
            assert_eq!(s.miss_count, 0);
            assert_eq!(s.zero_count, 0);
            assert!(!s.was_lost);
        }
    }

    // Frame 6: caller's count() is now > 5, sigma is a real (non-negative)
    // value -> the gate opens and the same favorable geometry as the first
    // test refines the offset.
    let out = refine_offset(
        (0.6, 0.0),
        &mut secs,
        &measured,
        PRIMARY_SNR,
        PRIMARY_SIGMA,
        false,
    );
    assert!(
        out.is_some(),
        "once unlocked, the same favorable geometry must refine"
    );
}
