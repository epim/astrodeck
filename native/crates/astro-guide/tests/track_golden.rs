// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's frame-to-frame tracking
// port (src/track.rs): MassChecker, DistanceChecker, and the avgDistance
// EMAs. Derived from PHD2 guider_multistar.cpp:52-180 (MassChecker),
// guider_multistar.cpp:601-704 (DistanceChecker), and guider.cpp:1065-1139
// (avgDistance EMAs) via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§3.1, §3.2, §13)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

// Provenance: hand-traced from dossier §3.1/§3.2/§13.
use astro_guide::track::{AvgDist, DistanceChecker, MassChecker, TrackState};

#[test]
fn mass_checker_accepts_until_five_then_gates() {
    // §3.1: history<5 always accepts; threshold 0.5.
    let mut mc = MassChecker::new(22500.0);
    for i in 0..4 {
        assert!(mc.check(1000.0, 0.5));
        mc.append(i as f64 * 1000.0, 1000.0);
    }
    // 5th sample present; steady mass ~ median 1000 -> a 40% drop (600) still
    // within lim0 = low_water*(1-0.5)=500 -> accepted; a 60% drop (350) < 500 -> rejected.
    mc.append(4000.0, 1000.0);
    assert!(mc.check(600.0, 0.5), "40% dip accepted");
    assert!(!mc.check(350.0, 0.5), "65% dip rejected");
}

#[test]
fn distance_checker_waiting_then_recovering() {
    // §3.2: a large jump moves Guiding->Waiting (reject); still-large & expired
    // (>5s) -> Recovering (accept).
    let mut dc = DistanceChecker::new();
    assert_eq!(dc.state(), TrackState::Guiding);
    // small_context=false so tolerance applies; err_smoothed=1.0, tol default 4.0
    // (tolerate-jumps OFF => tolerance 9e99 normally, but activate() forced 2.0).
    dc.activate(100.0); // star lost -> Waiting, forced tolerance 2.0, expiry 105s
    assert_eq!(dc.state(), TrackState::Waiting);
    assert!(
        !dc.check_distance(101.0, 10.0, 1.0, false),
        "large & not expired -> reject"
    );
    assert!(
        dc.check_distance(106.0, 10.0, 1.0, false),
        "large & expired -> Recovering accept"
    );
    assert_eq!(dc.state(), TrackState::Recovering);
    assert!(
        dc.check_distance(107.0, 0.5, 1.0, false),
        "small -> back to Guiding"
    );
    assert_eq!(dc.state(), TrackState::Guiding);
}

#[test]
fn avg_dist_large_distance_when_stale() {
    // §13: current_error()==100.0 when no star found for >20s.
    let mut a = AvgDist::new();
    a.update(0.0, 0.8, 0.5);
    assert!((a.current_error(1.0, true) - 0.8).abs() < 0.3); // fast EMA near sample
    assert_eq!(a.current_error(30.0, true), 100.0); // >20s stale
}

// ---------------------------------------------------------------------------
// Coverage round (P1-T4 review): state-machine edge vectors. All hand-traced
// from dossier §3.1/§3.2/§13 against upstream guider_multistar.cpp /
// guider.cpp at the cited lines.
// ---------------------------------------------------------------------------

#[test]
fn distance_checker_check_driven_jump_enters_waiting_without_forcing_tolerance() {
    // §3.2 / CheckDistance ST_GUIDING large branch
    // (guider_multistar.cpp:664-671): a large offset while Guiding moves the
    // machine to Waiting and rejects — WITHOUT setting m_forceTolerance
    // (only Activate(), guider_multistar.cpp:620-628, forces 2.0).
    //
    // Tolerate-jumps is off in this port (base tolerance 9e99), so "large"
    // while unforced requires err_smoothed = 0.0: threshold = 9e99 * 0.0 =
    // 0.0 and distance 5.0 > 0.0 (upstream _CheckDistance,
    // guider_multistar.cpp:631-647: small iff distance <= threshold).
    let mut dc = DistanceChecker::new();
    assert!(
        !dc.check_distance(0.0, 5.0, 0.0, false),
        "Guiding + large -> reject"
    );
    assert_eq!(
        dc.state(),
        TrackState::Waiting,
        "Guiding + large -> Waiting"
    );

    // Not expired (4.0 < 5.0 expiry) and still large -> reject, stay Waiting
    // (guider_multistar.cpp:673-691).
    assert!(!dc.check_distance(4.0, 5.0, 0.0, false));
    assert_eq!(dc.state(), TrackState::Waiting);

    // Tolerance was NOT forced by this entry path: distance 3.0 with
    // err_smoothed 1.0 would be large under a forced tolerance of 2.0
    // (3.0 > 2.0*1.0) but is small under the unforced 9e99 -> accepted,
    // back to Guiding (guider_multistar.cpp:675-681).
    assert!(
        dc.check_distance(4.5, 3.0, 1.0, false),
        "unforced tolerance stays 9e99 on the check-driven entry path"
    );
    assert_eq!(dc.state(), TrackState::Guiding);
}

#[test]
fn distance_checker_waiting_small_clears_forced_tolerance() {
    // §3.2 / CheckDistance ST_WAITING small branch
    // (guider_multistar.cpp:675-681): back to Guiding AND
    // m_forceTolerance = 0. Observable: after the clear, a distance that
    // would be large under the forced 2.0 must be small under the restored
    // base 9e99.
    let mut dc = DistanceChecker::new();
    dc.activate(0.0); // Waiting, forced tolerance 2.0, expiry 5.0
    assert_eq!(dc.state(), TrackState::Waiting);
    // small under forced 2.0: 0.5 <= 2.0*1.0 -> accept, Guiding, cleared.
    assert!(dc.check_distance(1.0, 0.5, 1.0, false));
    assert_eq!(dc.state(), TrackState::Guiding);
    // 3.0 > 2.0*1.0 would reject (-> Waiting) had the forced tolerance
    // survived; with it cleared, 3.0 <= 9e99*1.0 -> accept, stays Guiding.
    assert!(
        dc.check_distance(2.0, 3.0, 1.0, false),
        "forced tolerance cleared on Waiting -> Guiding"
    );
    assert_eq!(dc.state(), TrackState::Guiding);
}

#[test]
fn distance_checker_recovering_large_stays_recovering_and_accepts() {
    // §3.2 / CheckDistance ST_RECOVERING (guider_multistar.cpp:695-700):
    // every frame is accepted; only a small offset leaves the state.
    let mut dc = DistanceChecker::new();
    dc.activate(0.0); // Waiting, forced 2.0, expiry 5.0
    assert!(
        dc.check_distance(6.0, 10.0, 1.0, false),
        "expired + large -> Recovering, accept"
    );
    assert_eq!(dc.state(), TrackState::Recovering);
    // still large (10.0 > 2.0*1.0): accepted, state unchanged.
    assert!(
        dc.check_distance(7.0, 10.0, 1.0, false),
        "Recovering + large -> accept"
    );
    assert_eq!(dc.state(), TrackState::Recovering);
}

#[test]
fn distance_checker_activate_is_noop_outside_guiding() {
    // §3.2 / Activate's `if (m_state == ST_GUIDING)` guard
    // (guider_multistar.cpp:620-628): re-activation must NOT re-arm the
    // 5s expiry while Waiting, nor perturb Recovering.
    let mut dc = DistanceChecker::new();
    dc.activate(0.0); // Waiting, expiry 5.0
    dc.activate(3.0); // no-op: expiry must stay 5.0, not become 8.0
    assert_eq!(dc.state(), TrackState::Waiting);
    // At 5.5 the ORIGINAL expiry (5.0) has passed: large -> Recovering,
    // accept. Had the second activate re-armed (expiry 8.0), this would
    // reject instead.
    assert!(
        dc.check_distance(5.5, 10.0, 1.0, false),
        "expiry not re-armed by redundant activate"
    );
    assert_eq!(dc.state(), TrackState::Recovering);
    // Activate from Recovering: also a no-op (state stays Recovering; a
    // transition to Waiting would make the next large frame reject).
    dc.activate(6.0);
    assert_eq!(dc.state(), TrackState::Recovering);
    assert!(dc.check_distance(7.0, 10.0, 1.0, false));
}

#[test]
fn mass_checker_low_water_drift_moves_between_checks() {
    // §3.1's low-water 0.05 drift (guider_multistar.cpp:149-151: "let the
    // low water mark drift to follow the median so that it moves back up
    // after a period of intermittent clouds has brought it down") — the
    // behavior that motivates check()'s interior-mutability water marks.
    //
    // Hand-trace (threshold 0.5, window 22500 -> 45s drop horizon, so no
    // pruning below):
    //   append 5 x 1000 @ t=0..4000
    //   check#1(1000): med=1000; high=1000, low=1000 (drift 0). accept.
    //   append 6 x 600 @ t=5000..10000  (11 entries)
    //   check#2(600): sorted 600 x6,1000 x5; mid=11/2=5 -> med=600
    //     (nth_element upper-middle, guider_multistar.cpp:136-139);
    //     high stays 1000; low -> 600 (drift 0: low==med). accept
    //     (lim0=300, lim2=1500, lim3=1200).
    //   append 2 x 1000 @ t=11000,12000  (13 entries)
    //   check#3(315): sorted 600 x6,1000 x7; mid=6 -> med=1000; high stays
    //     1000; low: 1000 !< 600, then drift low = 600 + 0.05*(1000-600)
    //     = 620 -> lim0 = 620*0.5 = 310. 315 > 310 -> ACCEPT.
    //   check#4(315): same med; drift again low = 620 + 0.05*380 = 639
    //     -> lim0 = 319.5. 315 < 319.5 -> REJECT.
    // Same mass, opposite verdicts, no appends in between: only the drift
    // moved the gate.
    let mut mc = MassChecker::new(22500.0);
    for i in 0..5 {
        mc.append(i as f64 * 1000.0, 1000.0);
    }
    assert!(mc.check(1000.0, 0.5), "check#1 latches water marks at 1000");
    for i in 5..11 {
        mc.append(i as f64 * 1000.0, 600.0);
    }
    assert!(mc.check(600.0, 0.5), "check#2: med down to 600, low->600");
    mc.append(11000.0, 1000.0);
    mc.append(12000.0, 1000.0);
    assert!(mc.check(315.0, 0.5), "check#3: lim0=310 after first drift");
    assert!(
        !mc.check(315.0, 0.5),
        "check#4: lim0=319.5 after second drift — same mass now rejected"
    );
}

#[test]
fn mass_checker_high_water_upper_reject() {
    // §3.1 lim2 = high_water*(1+threshold) upper gate
    // (guider_multistar.cpp:153, 161-162). Steady 1000 history: high=low=
    // med=1000, lim2=1500, lim3=2000 -> 1400 accepted, 1600 rejected by
    // lim2 (the binding limit: 1600 < lim3=2000).
    let mut mc = MassChecker::new(22500.0);
    for i in 0..5 {
        mc.append(i as f64 * 1000.0, 1000.0);
    }
    assert!(mc.check(1400.0, 0.5), "below lim2=1500 -> accept");
    assert!(!mc.check(1600.0, 0.5), "above lim2=1500 -> reject");
}

#[test]
fn mass_checker_spike_guard_lim3_binds_when_median_depressed() {
    // §3.1 lim3 = med*(1+2*threshold) spike guard
    // (guider_multistar.cpp:154-159: "when mass is depressed by sky
    // conditions, we still want to trigger a rejection when there is a
    // large spike in mass, even if it is still below the high water
    // mark-based threshold").
    //
    // Hand-trace (threshold 0.5): latch high=1000 on steady history, then
    // depress the median to 600 (six 600s, 11 entries, mid=5 -> med=600):
    //   lim2 = 1000*1.5 = 1500 (high-water based), lim3 = 600*2 = 1200.
    //   1150 < both -> accept; 1350 < lim2 but > lim3 -> reject: lim3 is
    //   the binding limit.
    let mut mc = MassChecker::new(22500.0);
    for i in 0..5 {
        mc.append(i as f64 * 1000.0, 1000.0);
    }
    assert!(mc.check(1000.0, 0.5), "latch high water at 1000");
    for i in 5..11 {
        mc.append(i as f64 * 1000.0, 600.0);
    }
    assert!(mc.check(1150.0, 0.5), "below lim3=1200 -> accept");
    assert!(
        !mc.check(1350.0, 0.5),
        "1350 < lim2=1500 but > lim3=1200 -> spike-guard reject"
    );
}

#[test]
fn mass_checker_prunes_history_older_than_45s_window() {
    // §3.1: entries older than window*2 = 45s are dropped on append
    // (AppendData, guider_multistar.cpp:108-119: trim `time < oldest`,
    // then push).
    let mut mc = MassChecker::new(22500.0);
    for i in 0..5 {
        mc.append(i as f64 * 1000.0, 500.0);
    }
    // Control: gate is active with 5 samples (med=500, high=low=500,
    // lim2=750) — a huge mass is rejected.
    assert!(
        !mc.check(1.0e9, 0.5),
        "control: 5-sample gate rejects spike"
    );
    // Append at t=50000: cutoff = 50000-45000 = 5000 prunes all five
    // t<=4000 entries; history is now just this one sample.
    mc.append(50_000.0, 1000.0);
    assert!(
        mc.check(1.0e9, 0.5),
        "history pruned to 1 entry -> below-5 always-accept"
    );
}

#[test]
fn mass_checker_reset_clears_history_and_water_marks() {
    // §3.1 Reset (guider_multistar.cpp:174-179): clears history AND both
    // water marks (high 0, low 9e99).
    let mut mc = MassChecker::new(22500.0);
    for i in 0..5 {
        mc.append(i as f64 * 1000.0, 1000.0);
    }
    assert!(mc.check(1000.0, 0.5)); // latch high=1000, low=1000
    assert!(!mc.check(350.0, 0.5), "gate active before reset");
    mc.reset();
    // History cleared: below-5 always-accept.
    assert!(mc.check(350.0, 0.5), "empty history accepts after reset");
    // Water marks cleared: rebuild with mass 300 -> med=300 latches
    // high=300 (not the stale 1000), so lim2 = 450 and 500 is rejected.
    // With a stale high water of 1000, lim2 would be 1500 and 500 would
    // pass.
    for i in 0..5 {
        mc.append(10_000.0 + i as f64 * 1000.0, 300.0);
    }
    assert!(mc.check(400.0, 0.5), "below fresh lim2=450 -> accept");
    assert!(
        !mc.check(500.0, 0.5),
        "above fresh lim2=450 -> reject (stale high water would have passed it)"
    );
}

#[test]
fn avg_dist_smoothed_seed_then_alpha_switch() {
    // §13 slow EMA (guider.cpp:1077-1090): mean-of-first-10 seed
    // (m_avgDistanceLong += (dist - avgLong)/cnt for cnt < 10), then
    // alpha_long = 0.045 from the 10th sample on.
    //
    // Samples: dist = 2.0 then nine 1.0s (dist_ra = half of dist). The
    // running-mean seed after n samples is (2 + (n-1))/n = (n+1)/n:
    //   u2: avg_long = 2 + (1-2)/2       = 1.5      (= 3/2)
    //   u9: avg_long                     = 10/9
    //   u10 (cnt=10, NOT <10): avg_long = 10/9 + 0.045*(1 - 10/9)
    //      = 10/9 - 0.005 = 1.1061111...
    //   (a continued mean seed would give 11/10 = 1.1 — distinguishable.)
    let mut a = AvgDist::new();
    a.update(0.0, 2.0, 1.0);
    a.update(1.0, 1.0, 0.5);
    assert!(
        (a.current_error_smoothed(1.0, true) - 1.5).abs() < 1e-9,
        "count=2 seed is the running mean (divisor = count)"
    );
    for i in 2..9 {
        a.update(i as f64, 1.0, 0.5);
    }
    let seed9 = 10.0 / 9.0;
    assert!(
        (a.current_error_smoothed(8.0, true) - seed9).abs() < 1e-9,
        "count=9 seed = mean of first 9 samples"
    );
    a.update(9.0, 1.0, 0.5);
    let expect10 = seed9 + 0.045 * (1.0 - seed9);
    assert!(
        (a.current_error_smoothed(9.0, true) - expect10).abs() < 1e-9,
        "count=10 switches to alpha_long=0.045"
    );
    // RA slow EMA runs the same recurrence on dist_ra: seed mean of nine
    // samples (1.0 + 8*0.5)/9 = 5/9, then alpha step toward 0.5.
    let ra9 = 5.0 / 9.0;
    let ra10 = ra9 + 0.045 * (0.5 - ra9);
    assert!(
        (a.current_error_smoothed(9.0, false) - ra10).abs() < 1e-9,
        "RA-only slow EMA tracks dist_ra independently"
    );
}

#[test]
fn avg_dist_fast_ema_alpha_point_three() {
    // §13 fast EMA (guider.cpp:1072-1074): avg += 0.3*(dist - avg).
    //   u1: avg = 2.0 (reinit); u2: 2.0 + 0.3*(1-2) = 1.7;
    //   u3: 1.7 + 0.3*(1-1.7) = 1.49.
    let mut a = AvgDist::new();
    a.update(0.0, 2.0, 1.0);
    a.update(1.0, 1.0, 0.5);
    assert!((a.current_error(1.0, true) - 1.7).abs() < 1e-12);
    a.update(2.0, 1.0, 0.5);
    assert!((a.current_error(2.0, true) - 1.49).abs() < 1e-12);
}

#[test]
fn avg_dist_never_updated_reports_large_distance() {
    // §13 / guider.cpp:1118-1121: a zero (never-set) star-found timestamp
    // alone forces LARGE_DISTANCE, for both EMAs and both axis selections.
    let a = AvgDist::new();
    assert_eq!(a.current_error(0.0, true), 100.0);
    assert_eq!(a.current_error(0.0, false), 100.0);
    assert_eq!(a.current_error_smoothed(0.0, true), 100.0);
    assert_eq!(a.current_error_smoothed(0.0, false), 100.0);
}

#[test]
fn avg_dist_ra_only_selection() {
    // §13 / guider.cpp:1131-1138: raOnly (dec guiding off ->
    // dec_guiding=false here) selects the RA variants.
    let mut a = AvgDist::new();
    a.update(0.0, 2.0, 1.0); // reinit: avg=2.0, avg_ra=1.0 (both EMAs)
    assert!((a.current_error(0.0, true) - 2.0).abs() < 1e-12);
    assert!((a.current_error(0.0, false) - 1.0).abs() < 1e-12);
    assert!((a.current_error_smoothed(0.0, true) - 2.0).abs() < 1e-12);
    assert!((a.current_error_smoothed(0.0, false) - 1.0).abs() < 1e-12);
}
