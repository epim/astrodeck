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
