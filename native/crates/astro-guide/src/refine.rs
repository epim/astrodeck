// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§4). Derived from
// PHD2 guider_multistar.cpp:706-921 (`GuiderMultiStar::RefineOffset`, incl.
// the stabilization gate and the secondary-star weighted-average loop),
// star.h:103-117 (`GuideStar` fields: `referencePoint`/`missCount`/
// `zeroCount`/`offsetFromPrimary`/`wasLost`), and guiding_stats.cpp:50-136
// (`DescriptiveStats`'s Welford accumulator and `GetSigma`'s sample-stdev
// denominator `count - 1`, ported here as [`PrimaryDistStats`])
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! Multi-star offset refinement (dossier §4; `GuiderMultiStar::RefineOffset`,
//! `guider_multistar.cpp:706-921`).
//!
//! # Split between this module and the guide engine
//!
//! Upstream's `RefineOffset` is a single method on `GuiderMultiStar`, so it
//! reads and mutates persistent per-session state directly: the running
//! primary-distance statistics (`m_primaryDistStats`), the `m_stabilizing`
//! hysteresis flag (entered at 5σ, only exited at ≤2σ — real hysteresis with
//! memory), and `m_lockPositionMoved` (set by a dither). This task's frozen
//! signature —
//! `refine_offset(primary_offset, secondaries, measured, primary_snr,
//! primary_sigma, lock_moved) -> Option<(f64, f64)>` — has no room for that
//! persistent state (no `&mut stabilizing: bool`, no stats accumulator), so
//! the split is:
//!
//! - **The guide engine** (`engine.rs`) owns [`PrimaryDistStats`] and a
//!   `stabilizing: bool` field, and reproduces the dossier's exact
//!   enter-at-5σ/exit-at-2σ hysteresis with memory across frames. It only
//!   calls [`refine_offset`] once it has already decided (via that
//!   persisted state) that this frame should attempt refinement, passing
//!   the real, just-computed `primary_sigma`.
//! - **This function** additionally re-checks a *stateless* version of the
//!   same gate (`primary_sigma < 0.0` as an "insufficient samples" sentinel
//!   — see below — plus a fresh `primary_dist > 5σ` test) so that
//!   [`refine_offset`] is independently correct and testable when called in
//!   isolation (as this crate's golden-vector tests do), without requiring
//!   a caller-side state machine. Because the engine only ever invokes it
//!   from a state where its own hysteresis has already confirmed
//!   "not stabilizing", this inner check is redundant-but-harmless on the
//!   integrated path and authoritative only for the isolated-call case —
//!   the two never disagree (see `engine.rs`'s `refine_multistar` doc
//!   comment for the full argument).
//!
//! **The `primary_sigma < 0.0` sentinel** (DERIVED — the dossier's pseudocode
//! has no literal encoding for "fewer than 6 samples collected" as a
//! function argument): a real sigma is a standard deviation and therefore
//! never negative, so a negative value unambiguously signals "the caller's
//! `primary_dist_stats.count() <= 5`" (dossier: `else { stabilizing = true
//! }`, guider_multistar.cpp:790). [`refine_offset`] treats it exactly like
//! that branch: immediately returns `None`, without touching any
//! [`SecondaryStar`].
//!
//! **The `lock_moved` parameter** models the OTHER half of the dossier's
//! stabilization-exit branch (guider_multistar.cpp:766-798): when the
//! primary distance drops back through the exit threshold after a dither,
//! upstream re-finds every secondary at `primary + offset_from_primary`
//! (not the weighted-average math) and returns `false` (no offset change)
//! — it only refreshes `reference_point`/`was_lost`. This port asks the
//! CALLER to have already re-measured each secondary using that same
//! "expected location" strategy (this crate's synchronous, I/O-free
//! contract means [`refine_offset`] cannot itself re-invoke `star_find`);
//! `measured` is then just consumed to update `reference_point`/`was_lost`,
//! exactly mirroring upstream's loop body minus the `Find` call itself. See
//! `engine.rs`'s `measure()` doc comment for the one accepted behavioral
//! narrowing this implies (secondaries recover within 1 extra frame in the
//! worst case, rather than instantaneously).
//!
//! # Any panic drops to single-star mode for the session
//!
//! Dossier §4 (guider_multistar.cpp:894-898): any exception inside
//! `RefineOffset` permanently disables multi-star mode for the rest of the
//! session. This module's own arithmetic never panics (no unwraps, no
//! unchecked indexing beyond a `.min()`-bounded loop), so this obligation is
//! discharged at the call site: `engine.rs` wraps every [`refine_offset`]
//! call in `std::panic::catch_unwind` and, on `Err`, permanently clears its
//! secondary list and sets a `multi_star_broken` flag that gates all further
//! calls for the session.

use crate::types::MeasuredStar;

/// `m_maxStars` default (dossier §4/§15 "Max stars used (RefineOffset)"; a
/// fixed constant, not a config knob — `DEFAULT_MAX_STAR_COUNT`,
/// `guider_multistar.cpp:180`). Counts the primary, so at most
/// `DEFAULT_MAX_STAR_COUNT - 1` secondaries are ever averaged in in one
/// frame.
pub const DEFAULT_MAX_STAR_COUNT: usize = 9;

/// Stabilization-entry multiplier (dossier §4 `stability_sigma_x` /
/// `m_stabilitySigmaX`, `DEFAULT_STABILITY_SIGMAX`,
/// `guider_multistar.cpp:181`): a primary excursion beyond this many sigma
/// (while not already stabilizing) begins a stabilization period.
pub const STABILITY_SIGMA_ENTER: f64 = 5.0;

/// Stabilization-exit threshold (dossier §4, literal `2 * primarySigma`,
/// `guider_multistar.cpp:757`): a stabilizing session exits once the
/// primary distance falls to or below this many sigma.
pub const STABILITY_SIGMA_EXIT: f64 = 2.0;

/// Secondary-star excursion gate, in multiples of the primary's sigma
/// (dossier §4, `guider_multistar.cpp:838`).
const EXCURSION_SIGMA: f64 = 2.5;

/// Consecutive-suspicious-frame count at which a secondary is erased as a
/// probable hot pixel (dossier §4 "zero-count DZ at 5",
/// `guider_multistar.cpp:829`).
const ZERO_COUNT_LIMIT: u32 = 5;

/// Consecutive-excursion count beyond which a secondary's reference point is
/// reset to its current position instead of continuing to skip it (dossier
/// §4 "miss-count re-baseline at 10", `guider_multistar.cpp:844`).
const MISS_COUNT_LIMIT: u32 = 10;

/// One tracked secondary guide star (dossier §4; `GuideStar` fields,
/// `star.h:103-117`, minus the base `Star` position/SNR fields this port
/// keeps as plain `x`/`y`/`snr` rather than re-deriving a `Star` base type).
///
/// `x`/`y`/`snr` are this star's last-known measured position/SNR (upstream
/// `Star::X`/`Y`/`SNR`, inherited by `GuideStar`) — the "where we last found
/// it" search origin for a subsequent frame's [`search_position`]. They are
/// only ever written by [`refine_offset`] on a successful find; the initial
/// construction ([`SecondaryStar::new`]) seeds them from the star's first
/// measurement.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SecondaryStar {
    pub x: f64,
    pub y: f64,
    pub snr: f64,
    /// The baseline position this star's per-frame displacement is measured
    /// against (`GuideStar::referencePoint`). Reset on a miss-count
    /// re-baseline ("R") and on a lock-recovery re-find; otherwise fixed
    /// from construction.
    pub reference_point: (f64, f64),
    /// Fixed geometric offset from the primary star at candidate-list
    /// construction time (`GuideStar::offsetFromPrimary`); used to relocate
    /// a lost star (search at `primary_pos + offset_from_primary`).
    pub offset_from_primary: (f64, f64),
    /// `GuideStar::wasLost`: the previous frame's search failed to find
    /// this star.
    pub was_lost: bool,
    /// `GuideStar::zeroCount`: consecutive-ish count of frames where the
    /// displacement was exactly zero on ONE axis (suspicious).
    pub zero_count: u32,
    /// `GuideStar::missCount`: consecutive frames where the displacement
    /// exceeded the excursion gate.
    pub miss_count: u32,
    /// Set by [`refine_offset`] when this star should be dropped from the
    /// list entirely (a hot-pixel "DZ" — dossier §4). [`refine_offset`]
    /// takes `&mut [SecondaryStar]` (a fixed-length slice, not a `Vec`), so
    /// it cannot itself remove list entries; the caller — which owns the
    /// backing `Vec` — must `retain(|s| !s.erase)` after every call. Always
    /// `false` on a star [`refine_offset`] did not decide to erase this
    /// call (never needs resetting: a star with `erase == true` is removed
    /// by the caller before it could ever be passed to another call).
    pub erase: bool,
}

impl SecondaryStar {
    /// A freshly found secondary star (dossier §2.6/§2.7: `reference_point`
    /// is set to the just-measured position at candidate-list construction
    /// time, `star.cpp:1052-1053`/`1108`).
    pub fn new(x: f64, y: f64, snr: f64, offset_from_primary: (f64, f64)) -> Self {
        SecondaryStar {
            x,
            y,
            snr,
            reference_point: (x, y),
            offset_from_primary,
            was_lost: false,
            zero_count: 0,
            miss_count: 0,
            erase: false,
        }
    }

    /// Where a caller should search for this star this frame, given the
    /// primary's position (dossier §4: "search where last seen; if
    /// was_lost, search at primary + offset_from_primary",
    /// `guider_multistar.cpp:802-810`).
    pub fn search_position(&self, primary_pos: (f64, f64)) -> (f64, f64) {
        if self.was_lost {
            (
                primary_pos.0 + self.offset_from_primary.0,
                primary_pos.1 + self.offset_from_primary.1,
            )
        } else {
            (self.x, self.y)
        }
    }
}

/// Running (non-windowed) mean/variance of the primary star's per-frame
/// distance-from-lock (dossier §4 `m_primaryDistStats`; PHD2's
/// `DescriptiveStats`, `guiding_stats.cpp:50-136`). Welford's algorithm;
/// [`sigma`](Self::sigma) is the SAMPLE standard deviation (`GetSigma`'s
/// `sqrt(runningS / (count - 1))`, `guiding_stats.cpp:121-127`) — NOT the
/// population sigma PHD2 also exposes (`GetPopulationSigma`) but
/// `RefineOffset` never calls.
#[derive(Debug, Clone, Copy)]
pub struct PrimaryDistStats {
    count: u64,
    mean: f64,
    /// `runningS`: sum of squared deltas from the running mean.
    m2: f64,
}

impl PrimaryDistStats {
    pub fn new() -> Self {
        PrimaryDistStats {
            count: 0,
            mean: 0.0,
            m2: 0.0,
        }
    }

    /// `AddValue` (`guiding_stats.cpp:58-83`).
    pub fn add(&mut self, v: f64) {
        self.count += 1;
        if self.count == 1 {
            self.mean = v;
        } else {
            let new_mean = self.mean + (v - self.mean) / self.count as f64;
            self.m2 += (v - self.mean) * (v - new_mean);
            self.mean = new_mean;
        }
    }

    /// `GetCount`.
    pub fn count(&self) -> u64 {
        self.count
    }

    /// `GetSigma`: sample standard deviation, `0.0` when fewer than 2
    /// samples have been added (`guiding_stats.cpp:121-127`).
    pub fn sigma(&self) -> f64 {
        if self.count > 1 {
            (self.m2 / (self.count - 1) as f64).sqrt()
        } else {
            0.0
        }
    }
}

impl Default for PrimaryDistStats {
    fn default() -> Self {
        Self::new()
    }
}

/// Refine the primary star's camera-frame offset using secondary stars
/// (dossier §4; `GuiderMultiStar::RefineOffset`,
/// `guider_multistar.cpp:706-921`). See the module doc for the split of
/// responsibility between this function and the guide engine's persisted
/// stabilization state machine, the `primary_sigma < 0.0` sentinel, and the
/// `lock_moved` recovery mode.
///
/// `secondaries` and `measured` must be the SAME LENGTH and index-aligned
/// (`measured[i]` is this frame's re-measurement of `secondaries[i]`, at the
/// search position [`SecondaryStar::search_position`] would have reported
/// for it) — a caller that passes mismatched lengths gets the shorter of
/// the two processed and the rest silently ignored (defensive, not a
/// panic — this function must never be the panic source the module doc's
/// "drops to single-star mode" guard is protecting against).
///
/// Returns `Some(refined_camera_offset)` only when secondary stars were
/// averaged in AND the result strictly shrinks the primary's raw offset
/// (dossier: "only accept if it SHRINKS the offset"); `None` otherwise
/// (stabilizing, no movement, no usable secondaries, or no shrink) — in
/// every `None` case except a `lock_moved` recovery frame, the caller
/// should keep using its own pre-call `primary_offset`.
pub fn refine_offset(
    primary_offset: (f64, f64),
    secondaries: &mut [SecondaryStar],
    measured: &[MeasuredStar],
    primary_snr: f64,
    primary_sigma: f64,
    lock_moved: bool,
) -> Option<(f64, f64)> {
    let n = secondaries.len().min(measured.len());

    if lock_moved {
        // Lock-recovery frame (dossier §4, guider_multistar.cpp:766-791):
        // refresh reference points from the caller's expected-location
        // re-find; no weighted average this frame.
        for i in 0..n {
            let m = &measured[i];
            if m.found {
                secondaries[i].reference_point = (m.x, m.y);
                secondaries[i].x = m.x;
                secondaries[i].y = m.y;
                secondaries[i].was_lost = false;
            } else {
                secondaries[i].was_lost = true;
            }
        }
        return None;
    }

    if n == 0 {
        // Precondition "list len > 1" (guider_multistar.cpp:734): no
        // secondaries to refine with.
        return None;
    }

    // Stabilization gate (dossier §4). `primary_sigma < 0.0` is the
    // "count() <= 5, still collecting stats" sentinel (see module doc); the
    // `> 5.0 * sigma` test is the stateless half of upstream's enter/exit
    // hysteresis (the engine owns the stateful half — see module doc).
    if primary_sigma < 0.0 {
        return None;
    }
    let primary_dist = primary_offset.0.hypot(primary_offset.1);
    if primary_dist > STABILITY_SIGMA_ENTER * primary_sigma {
        return None;
    }
    if primary_offset.0 == 0.0 && primary_offset.1 == 0.0 {
        return None;
    }

    let mut sum_w = 1.0; // primary weight = 1
    let mut sum_x = primary_offset.0;
    let mut sum_y = primary_offset.1;
    let mut averaged = false;
    let mut used = 1usize; // primary counts toward m_starsUsed

    for i in 0..n {
        if used >= DEFAULT_MAX_STAR_COUNT {
            break;
        }
        let sec = &mut secondaries[i];
        let m = &measured[i];

        if !m.found {
            sec.was_lost = true;
            continue;
        }

        let dx = m.x - sec.reference_point.0;
        let dy = m.y - sec.reference_point.1;
        sec.was_lost = false;
        sec.x = m.x;
        sec.y = m.y;
        used += 1;

        if dx == 0.0 && dy == 0.0 {
            // Exactly zero on both axes: probably a hot pixel.
            sec.erase = true;
            continue;
        }

        // Zero-counting: exactly zero on ONE axis is suspicious.
        if dx == 0.0 || dy == 0.0 {
            sec.zero_count += 1;
        } else if sec.zero_count > 0 {
            sec.zero_count -= 1;
        }
        if sec.zero_count == ZERO_COUNT_LIMIT {
            sec.erase = true;
            continue;
        }

        // Excursion check vs. the primary's sigma.
        let sec_dist = dx.hypot(dy);
        if sec_dist > EXCURSION_SIGMA * primary_sigma {
            sec.miss_count += 1;
            if sec.miss_count > MISS_COUNT_LIMIT {
                sec.reference_point = (m.x, m.y);
                sec.miss_count = 0;
            }
            continue;
        } else if sec.miss_count > 0 {
            sec.miss_count -= 1;
        }

        // Usable: SNR-relative weight.
        let w = m.snr / primary_snr;
        sum_x += w * dx;
        sum_y += w * dy;
        sum_w += w;
        averaged = true;
    }

    if averaged {
        let ax = sum_x / sum_w;
        let ay = sum_y / sum_w;
        if ax.hypot(ay) < primary_dist {
            return Some((ax, ay));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn found(x: f64, y: f64, snr: f64) -> MeasuredStar {
        MeasuredStar {
            x,
            y,
            snr,
            mass: 1000.0,
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

    #[test]
    fn insufficient_samples_sentinel_returns_none() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        let measured = [found(10.5, 10.0, 20.0)];
        let out = refine_offset((0.5, 0.0), &mut secs, &measured, 20.0, -1.0, false);
        assert_eq!(out, None);
    }

    #[test]
    fn no_secondaries_returns_none() {
        let mut secs: [SecondaryStar; 0] = [];
        let measured: [MeasuredStar; 0] = [];
        let out = refine_offset((0.5, 0.3), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None);
    }

    #[test]
    fn zero_primary_offset_returns_none() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        let measured = [found(10.0, 10.0, 20.0)];
        let out = refine_offset((0.0, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None);
    }

    #[test]
    fn primary_beyond_five_sigma_returns_none() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        let measured = [found(10.5, 10.0, 20.0)];
        // primary_dist = 1.0, sigma = 0.1 -> 5*sigma = 0.5 < 1.0
        let out = refine_offset((1.0, 0.0), &mut secs, &measured, 20.0, 0.1, false);
        assert_eq!(out, None);
    }

    #[test]
    fn hot_pixel_secondary_erased() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        // Measured EXACTLY at its own reference point -> dx=dy=0.
        let measured = [found(10.0, 10.0, 20.0)];
        let out = refine_offset((0.5, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None); // no usable secondaries -> not averaged
        assert!(secs[0].erase, "hot-pixel secondary must be flagged erase");
    }

    #[test]
    fn excursion_beyond_2_5_sigma_skips_and_bumps_miss_count() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        // primary_sigma = 1.0 -> excursion gate = 2.5px. Secondary jumps
        // (3.0, 0.1) -> hypot ~3.0017 > 2.5. Nonzero on BOTH axes so the
        // zero-counting branch (a separate, unrelated gate) never fires.
        let measured = [found(13.0, 10.1, 20.0)];
        let out = refine_offset((0.5, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None);
        assert_eq!(secs[0].miss_count, 1);
        assert!(!secs[0].erase);
        assert_eq!(
            secs[0].reference_point,
            (10.0, 10.0),
            "single excursion must not re-baseline yet"
        );
    }

    #[test]
    fn excursion_rebaselines_after_11_consecutive_misses() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        let measured = [found(13.0, 10.1, 20.0)];
        for expected_miss in 1..=10u32 {
            let out = refine_offset((0.5, 0.0), &mut secs, &measured, 20.0, 1.0, false);
            assert_eq!(out, None);
            assert_eq!(secs[0].miss_count, expected_miss);
        }
        // 11th consecutive excursion: miss_count would become 11 > 10 -> rebaseline.
        let out = refine_offset((0.5, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None);
        assert_eq!(secs[0].miss_count, 0);
        assert_eq!(secs[0].reference_point, (13.0, 10.1));
    }

    #[test]
    fn lock_moved_refreshes_reference_points_without_averaging() {
        let mut secs = [
            SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0)),
            SecondaryStar::new(-5.0, 5.0, 15.0, (-15.0, -5.0)),
        ];
        let measured = [found(12.0, 11.0, 20.0), lost()];
        let out = refine_offset((3.0, 1.0), &mut secs, &measured, 20.0, 1.0, true);
        assert_eq!(out, None);
        assert_eq!(secs[0].reference_point, (12.0, 11.0));
        assert!(!secs[0].was_lost);
        assert!(secs[1].was_lost);
        // No averaging bookkeeping touched on a lock-recovery frame.
        assert_eq!(secs[0].zero_count, 0);
        assert_eq!(secs[0].miss_count, 0);
    }

    #[test]
    fn lost_secondary_marked_and_not_counted_toward_star_cap() {
        let mut secs = [SecondaryStar::new(10.0, 10.0, 20.0, (10.0, 0.0))];
        let measured = [lost()];
        let out = refine_offset((0.5, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None);
        assert!(secs[0].was_lost);
    }

    /// Hand-traced golden vector for the SNR-weighted average
    /// (dossier §4's `wt = SNR/primarySNR; sumX += wt*dx; ...`).
    ///
    /// Fixture: primary offset (0.6, 0.0) px, `primary_sigma = 1.0`
    /// (well inside 5σ, so no stabilization gate fires). Two secondaries,
    /// both usable (small excursions, well inside `2.5 * 1.0 = 2.5px`):
    ///   - secondary A: SNR 20 (primary's SNR), displacement (dx, dy) =
    ///     (0.4, 0.0) from its reference point -> weight 20/20 = 1.0.
    ///   - secondary B: SNR 10 (half primary's), displacement (0.0, -0.6)
    ///     -> weight 10/20 = 0.5.
    ///
    /// By hand:
    ///   sum_w = 1 (primary) + 1.0 (A) + 0.5 (B) = 2.5
    ///   sum_x = 0.6 + 1.0*0.4 + 0.5*0.0 = 1.0     -> avg_x = 1.0/2.5 = 0.4
    ///   sum_y = 0.0 + 1.0*0.0 + 0.5*(-0.6) = -0.3  -> avg_y = -0.3/2.5 = -0.12
    ///   refined hypot = sqrt(0.4^2 + 0.12^2) = sqrt(0.1744) = 0.41761...
    ///   primary hypot = 0.6
    /// 0.41761 < 0.6 -> the refined offset SHRINKS the raw offset, so
    /// `refine_offset` must return `Some((0.4, -0.12))`.
    #[test]
    fn snr_weighted_average_shrinks_offset() {
        let mut secs = [
            SecondaryStar::new(100.0, 100.0, 20.0, (0.0, 0.0)), // A, reference (100,100)
            SecondaryStar::new(200.0, 50.0, 10.0, (100.0, -50.0)), // B, reference (200,50)
        ];
        let measured = [
            found(100.4, 100.0, 20.0), // A: dx=0.4, dy=0.0, SNR 20
            found(200.0, 49.4, 10.0),  // B: dx=0.0, dy=-0.6, SNR 10
        ];
        let out = refine_offset((0.6, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        let (rx, ry) = out.expect("weighted average must shrink the offset");
        assert!((rx - 0.4).abs() < 1e-9, "rx={rx}");
        assert!((ry - (-0.12)).abs() < 1e-9, "ry={ry}");
        assert!(rx.hypot(ry) < 0.6);
    }

    /// Same fixture, but the primary's own raw offset is already tiny
    /// (0.05px) — smaller than any weighted blend the secondaries could
    /// produce given their displacements — so the shrink test must reject
    /// the average and `refine_offset` reports single-star (`None`).
    #[test]
    fn weighted_average_rejected_when_it_does_not_shrink() {
        let mut secs = [SecondaryStar::new(100.0, 100.0, 20.0, (0.0, 0.0))];
        let measured = [found(100.4, 100.0, 20.0)]; // dx=0.4 dominates a tiny primary offset
        let out = refine_offset((0.05, 0.0), &mut secs, &measured, 20.0, 1.0, false);
        assert_eq!(out, None);
    }

    /// `search_position` (dossier §4, guider_multistar.cpp:802-810): a
    /// tracked star is searched where last seen; a lost star is searched
    /// at `primary + offset_from_primary` instead (the dither/occlusion
    /// recovery path).
    #[test]
    fn search_position_normal_vs_was_lost() {
        let mut s = SecondaryStar::new(100.0, 50.0, 10.0, (70.0, -50.0));
        // Not lost: own last position, primary's position irrelevant.
        assert_eq!(s.search_position((40.0, 90.0)), (100.0, 50.0));
        // Lost: expected location = primary + offset_from_primary.
        s.was_lost = true;
        assert_eq!(s.search_position((40.0, 90.0)), (110.0, 40.0));
        // Recovered (was_lost cleared): back to own last position.
        s.was_lost = false;
        assert_eq!(s.search_position((0.0, 0.0)), (100.0, 50.0));
    }

    #[test]
    fn primary_dist_stats_matches_hand_computed_sample_sigma() {
        let mut s = PrimaryDistStats::new();
        for v in [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0] {
            s.add(v);
        }
        assert_eq!(s.count(), 8);
        // mean = 5.0, sample variance = 32/7 -> sigma = sqrt(32/7)
        assert!((s.sigma() - (32.0f64 / 7.0).sqrt()).abs() < 1e-9);
    }
}
