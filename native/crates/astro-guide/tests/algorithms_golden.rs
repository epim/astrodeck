// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's guide-algorithm port
// (src/algorithms/): Hysteresis and ResistSwitch. Derived from PHD2
// guide_algorithm_hysteresis.cpp:42-91 and
// guide_algorithm_resistswitch.cpp:42-177 via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§6.1, §6.2)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

// Provenance: step responses hand-computed from dossier §6.1 (Hysteresis) and
// §6.2 (ResistSwitch), defaults from §15.
use astro_guide::algorithms::{GuideAlgorithm, Hysteresis, ResistSwitch};

#[test]
fn hysteresis_default_step_response() {
    // h=0.1, aggression=0.7, min_move=0.2. last_move stores the aggression-scaled
    // output (§6.1). Input [1,1,1,0.1,-0.5]:
    //  1.0 -> (0.9*1 + 0.1*0)*0.7 = 0.63
    //  1.0 -> (0.9*1 + 0.1*0.63)*0.7 = 0.6741
    //  1.0 -> (0.9*1 + 0.1*0.6741)*0.7 = 0.677187
    //  0.1 -> |0.1|<0.2 -> 0.0 (last_move decays to 0)
    // -0.5 -> (0.9*-0.5 + 0.1*0)*0.7 = -0.315
    let mut h = Hysteresis::new(0.1, 0.7, 0.2);
    let ins = [1.0, 1.0, 1.0, 0.1, -0.5];
    let exp = [0.63, 0.6741, 0.677187, 0.0, -0.315];
    for (i, e) in ins.iter().zip(exp.iter()) {
        let got = h.result(*i);
        assert!((got - e).abs() < 1e-9, "in={} got={} exp={}", i, got, e);
    }
}

#[test]
fn resist_switch_requires_three_and_worsening() {
    // min_move=0.2, aggression=1.0, fast_switch=true, thresh=0.6. Constant 0.3:
    // frame1: dec_history=+1 (<3) -> veto 0.0
    // frame2: dec_history=+2 (<3) -> veto 0.0
    // frame3: dec_history=+3, newest3(0.9) worsening vs oldest3(0) -> side=+1 -> 0.3
    // frame4: side +1 == sign(input) -> 0.3
    // frame5: 0.3
    let mut r = ResistSwitch::default();
    let exp = [0.0, 0.0, 0.3, 0.3, 0.3];
    for e in exp.iter() {
        let got = r.result(0.3);
        assert!((got - e).abs() < 1e-9, "got={} exp={}", got, e);
    }
}

#[test]
fn resist_switch_deadband_and_reset() {
    let mut r = ResistSwitch::default();
    assert_eq!(r.result(0.1), 0.0); // below min_move 0.2
    r.reset();
    assert_eq!(r.result(0.1), 0.0);
}

// ---------------------------------------------------------------------------
// Coverage round (review PASSED; test-only). Hand-traced from dossier
// §6.1/§6.2 + guide_algorithm_hysteresis.cpp / guide_algorithm_resistswitch.cpp
// (line citations per vector).
// ---------------------------------------------------------------------------

#[test]
fn hysteresis_input_exactly_at_min_move_not_vetoed() {
    // Deadband is STRICT less-than (`fabs(input) < m_minMove`,
    // guide_algorithm_hysteresis.cpp:81): |input| == min_move is NOT vetoed.
    // Defaults h=0.1 agg=0.7 min_move=0.2, fresh (last_move=0):
    //  0.2 -> (0.9*0.2 + 0.1*0)*0.7 = 0.126  (nonzero!)
    let mut h = Hysteresis::new(0.1, 0.7, 0.2);
    let got = h.result(0.2);
    assert!((got - 0.126).abs() < 1e-9, "at-boundary got={}", got);
    // Same on the negative side: -0.2 -> (0.9*-0.2)*0.7 = -0.126.
    let mut h2 = Hysteresis::new(0.1, 0.7, 0.2);
    let got2 = h2.result(-0.2);
    assert!((got2 + 0.126).abs() < 1e-9, "neg at-boundary got={}", got2);
}

#[test]
fn hysteresis_aggression_extremes_zero_and_max() {
    // aggression 0.0 and 2.0 are both VALID (SetAggression rejects only
    // aggression < 0 or > MaxAggression(2.0),
    // guide_algorithm_hysteresis.cpp:143-167 + :45).
    // agg=0.0: every output is 0 (r *= 0, cpp:79), last_move stays 0.
    let mut z = Hysteresis::new(0.1, 0.0, 0.2);
    assert_eq!(z.aggression, 0.0, "0.0 must not fall back to default");
    assert_eq!(z.result(1.0), 0.0);
    assert_eq!(z.result(-2.0), 0.0);
    // agg=2.0 (the MaxAggression boundary):
    //  1.0 -> (0.9*1 + 0.1*0)*2.0 = 1.8
    //  1.0 -> (0.9*1 + 0.1*1.8)*2.0 = 2.16
    let mut m = Hysteresis::new(0.1, 2.0, 0.2);
    assert_eq!(m.aggression, 2.0, "2.0 must not fall back to default");
    let a = m.result(1.0);
    assert!((a - 1.8).abs() < 1e-9, "got={}", a);
    let b = m.result(1.0);
    assert!((b - 2.16).abs() < 1e-9, "got={}", b);
}

#[test]
fn hysteresis_constructor_clamps_and_fallbacks() {
    // Constructor-time validation (adjudicated fold-in of upstream's
    // setters, guide_algorithm_hysteresis.cpp:93-167):
    // hysteresis out of [0, 0.99] -> CLAMP (wxClip, cpp:135);
    // aggression out of [0, 2.0] -> DEFAULT 0.7 (cpp:160);
    // min_move < 0 -> DEFAULT 0.2 (cpp:110).
    assert_eq!(Hysteresis::new(1.5, 0.7, 0.2).hysteresis, 0.99);
    assert_eq!(Hysteresis::new(-0.3, 0.7, 0.2).hysteresis, 0.0);
    assert_eq!(Hysteresis::new(0.1, 2.5, 0.2).aggression, 0.7);
    assert_eq!(Hysteresis::new(0.1, -0.1, 0.2).aggression, 0.7);
    assert_eq!(Hysteresis::new(0.1, 0.7, -1.0).min_move, 0.2);
    // In-range values pass through untouched.
    let ok = Hysteresis::new(0.5, 1.2, 0.35);
    assert_eq!(
        (ok.hysteresis, ok.aggression, ok.min_move),
        (0.5, 1.2, 0.35)
    );
    // min_move() trait accessor reports the configured value.
    assert_eq!(ok.min_move(), 0.35);
}

#[test]
fn hysteresis_reset_clears_last_move_mid_sequence() {
    // reset() zeroes m_lastMove (guide_algorithm_hysteresis.cpp:70-73), so
    // the next blend (cpp:77) uses last_move=0 again:
    //  1.0 -> 0.63, 1.0 -> 0.6741, reset, 1.0 -> 0.63 (not 0.677187).
    let mut h = Hysteresis::new(0.1, 0.7, 0.2);
    assert!((h.result(1.0) - 0.63).abs() < 1e-9);
    assert!((h.result(1.0) - 0.6741).abs() < 1e-9);
    h.reset();
    let got = h.result(1.0);
    assert!((got - 0.63).abs() < 1e-9, "post-reset got={}", got);
}

#[test]
fn resist_switch_constructor_fallbacks() {
    // Constructor-time validation (adjudicated fold-in of upstream's
    // setters): min_move <= 0 -> DEFAULT 0.2 (note <=, not < — SetMinMove
    // rejects 0.0 too, guide_algorithm_resistswitch.cpp:185);
    // aggression out of [0, 1] -> DEFAULT 1.0 (cpp:213).
    assert_eq!(ResistSwitch::new(-0.5, 1.0, true).min_move, 0.2);
    assert_eq!(ResistSwitch::new(0.0, 1.0, true).min_move, 0.2);
    assert_eq!(ResistSwitch::new(0.2, 1.5, true).aggression, 1.0);
    assert_eq!(ResistSwitch::new(0.2, -0.1, true).aggression, 1.0);
    let ok = ResistSwitch::new(0.3, 0.5, false);
    assert_eq!(
        (ok.min_move, ok.aggression, ok.fast_switch),
        (0.3, 0.5, false)
    );
    assert_eq!(ok.min_move(), 0.3);
}

#[test]
fn resist_switch_direction_reversal_overshoot_then_switch() {
    // Establish current_side=+1 with five 0.3 frames (per
    // resist_switch_requires_three_and_worsening), then feed -0.3 (below
    // the 3*min_move=0.6 fast-switch threshold, so the slow path decides;
    // guide_algorithm_resistswitch.cpp:110). History after establishment:
    // [0,0,0,0,0,.3,.3,.3,.3,.3], side=+1. Opposing frames (dec = vote sum,
    // cpp:124-132; gate = side==0 || sign(side)==-sign(dec), cpp:134):
    //  f6  -0.3: dec=+5-1=+4, gate false (+1 != -sign(+4)=-1)
    //            -> overshoot veto (side +1 != sign(-0.3), cpp:161-164) -> 0
    //  f7  -0.3: dec=+3, same -> 0        f8 -0.3: dec=+2 -> 0
    //  f9  -0.3: dec=+1 -> 0              f10 -0.3: dec=0, -sign(0)=0 -> 0
    //  f11 -0.3: dec=4-6=-2, gate TRUE (+1 == -sign(-2)); |dec|<3
    //            -> "not compelling" veto (cpp:136-139) -> 0
    //  f12 -0.3: dec=3-7=-4, gate TRUE; oldest3=0.9, newest3=-0.9,
    //            |newest| <= |oldest| (equality!) -> "not getting worse"
    //            veto (cpp:150-153) -> 0
    //  f13 -0.3: dec=2-8=-6, gate TRUE; oldest3=0.3+0.3-0.3=0.3,
    //            newest3=-0.9, 0.9 > 0.3 -> side=sign(-6)=-1 (cpp:158);
    //            overshoot check passes (-1 == sign(-0.3)) -> -0.3
    let mut r = ResistSwitch::default();
    for e in [0.0, 0.0, 0.3, 0.3, 0.3] {
        assert!((r.result(0.3) - e).abs() < 1e-9);
    }
    let exp = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.3];
    for (i, e) in exp.iter().enumerate() {
        let got = r.result(-0.3);
        assert!(
            (got - e).abs() < 1e-9,
            "frame {} got={} exp={}",
            i + 6,
            got,
            e
        );
    }
}

#[test]
fn resist_switch_fast_switch_forces_immediate_reversal() {
    // Fast-switch force path (guide_algorithm_resistswitch.cpp:107-122):
    // sign(input) != current_side AND |input| > 3*min_move=0.6 ->
    // current_side=0, history rewritten to 7 zeros + 3 copies of input
    // (cpp:115-120). Establish side=+1 (five 0.3s), then one -0.7:
    //   after rewrite: dec = 3*sign(-0.7) = -3 (exactly the 3 copies);
    //   gate (side==0): |dec|>=3; oldest3=0 (the zero-fill), newest3=-2.1,
    //   2.1 > 0 -> side=-1; overshoot passes -> -0.7 SAME FRAME.
    // Distinguishing arithmetic: without the rewrite the frame's history
    // would be [0,0,0,0,.3,.3,.3,.3,.3,-.7] -> dec=+4 -> (even with side
    // forced 0) side would flip to +1 -> overshoot veto -> 0.0, and with
    // side left +1 the gate is skipped -> overshoot veto -> 0.0 either way.
    let mut r = ResistSwitch::default();
    for _ in 0..5 {
        r.result(0.3);
    }
    let got = r.result(-0.7);
    assert!((got + 0.7).abs() < 1e-9, "fast-switch frame got={}", got);
    // Post-switch, same-side moves flow: -0.25 -> dec=-4, gate false
    // (sign(-1) != -sign(-4)=+1), overshoot passes -> -0.25.
    let follow = r.result(-0.25);
    assert!((follow + 0.25).abs() < 1e-9, "follow-up got={}", follow);

    // Differential control: identical sequence with fast_switch=false must
    // NOT switch on the -0.7 frame (dec=+5-1=+4, gate false, overshoot
    // veto -> 0.0) — proving the force path is load-bearing.
    let mut slow = ResistSwitch::new(0.2, 1.0, false);
    for _ in 0..5 {
        slow.result(0.3);
    }
    assert_eq!(slow.result(-0.7), 0.0, "no fast switch when disabled");
}

#[test]
fn resist_switch_not_getting_worse_vetoes_decaying_reversal() {
    // The |newest3| <= |oldest3| veto (guide_algorithm_resistswitch.cpp:
    // 141-153) with a strictly DECAYING opposing drift. Establish side=+1
    // (five 0.3s; history [0,0,0,0,0,.3,.3,.3,.3,.3]), then decaying
    // negatives (all |v| < 0.6 so fast-switch never fires):
    //  f6  -0.5 : dec=+5-1=+4, gate false -> overshoot veto -> 0
    //  f7  -0.4 : dec=+3 -> 0             f8 -0.35: dec=+2 -> 0
    //  f9  -0.3 : dec=+1 -> 0             f10 -0.25: dec=0 -> 0
    //  f11 -0.25: dec=4-6=-2, gate TRUE, |dec|<3 -> not compelling -> 0
    //  f12 -0.25: dec=3-7=-4, gate TRUE; oldest3=.3+.3+.3=0.9,
    //             newest3=-0.75; 0.75 <= 0.9 (strictly less: the error is
    //             DECAYING) -> "not getting worse" veto -> 0
    //  f13 -0.25: dec=2-8=-6, gate TRUE; oldest3=.3+.3-.5=0.1,
    //             newest3=-0.75; 0.75 > 0.1 -> side=-1 -> -0.25
    let mut r = ResistSwitch::default();
    for _ in 0..5 {
        r.result(0.3);
    }
    let ins = [-0.5, -0.4, -0.35, -0.3, -0.25, -0.25, -0.25, -0.25];
    let exp = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.25];
    for (i, (input, e)) in ins.iter().zip(exp.iter()).enumerate() {
        let got = r.result(*input);
        assert!(
            (got - e).abs() < 1e-9,
            "frame {} got={} exp={}",
            i + 6,
            got,
            e
        );
    }
}

#[test]
fn resist_switch_reset_from_established_state() {
    // reset() empties the history ring and zeroes current_side
    // (guide_algorithm_resistswitch.cpp:65-75). From an established state
    // (full history of 0.3s, side=+1, moves flowing), a post-reset 0.3
    // must be vetoed "not compelling" again (dec=+1 < 3) — if either the
    // ring or the side survived, the move would flow (side +1 aligned).
    let mut r = ResistSwitch::default();
    for _ in 0..5 {
        r.result(0.3);
    }
    assert!((r.result(0.3) - 0.3).abs() < 1e-9, "flowing before reset");
    r.reset();
    let exp = [0.0, 0.0, 0.3]; // the requires-three ramp starts over
    for (i, e) in exp.iter().enumerate() {
        let got = r.result(0.3);
        assert!((got - e).abs() < 1e-9, "post-reset frame {} got={}", i, got);
    }
}

#[test]
fn resist_switch_entry_at_min_move_neither_vetoed_nor_counted() {
    // Two strict inequalities meet at |v| == min_move == 0.2:
    // the deadband veto is `fabs(input) < m_minMove` (STRICT,
    // guide_algorithm_resistswitch.cpp:102) so 0.2 is NOT vetoed, while
    // the vote filter is `fabs(m_history[i]) > m_minMove` (STRICT,
    // cpp:128) so 0.2 is NOT counted.
    //
    // Part 1 — not counted: fresh, inputs [0.3, 0.3, 0.2, 0.3]:
    //  f1 0.3: dec=+1 -> not compelling -> 0
    //  f2 0.3: dec=+2 -> 0
    //  f3 0.2: dec=+2 (the 0.2 is excluded!) -> not compelling -> 0
    //          (if 0.2 counted: dec=+3, oldest3=0, newest3=0.8 -> side=+1
    //           -> output 0.2 — the 0.0 here pins the strict `>`)
    //  f4 0.3: dec=+3 (three 0.3s) -> oldest3=0, newest3=.3+.2+.3=0.8
    //          -> side=+1 -> 0.3
    let mut r = ResistSwitch::default();
    let ins = [0.3, 0.3, 0.2, 0.3];
    let exp = [0.0, 0.0, 0.0, 0.3];
    for (i, (input, e)) in ins.iter().zip(exp.iter()).enumerate() {
        let got = r.result(*input);
        assert!(
            (got - e).abs() < 1e-9,
            "frame {} got={} exp={}",
            i + 1,
            got,
            e
        );
    }
    // Part 2 — not deadband-vetoed: established side=+1, then exactly 0.2:
    // deadband passes (0.2 < 0.2 false), dec=+5 (0.2 excluded), gate
    // false, overshoot passes (+1 == sign(0.2)) -> output 0.2. A `<=`
    // deadband would output 0.0 instead.
    let mut r2 = ResistSwitch::default();
    for _ in 0..5 {
        r2.result(0.3);
    }
    let got = r2.result(0.2);
    assert!((got - 0.2).abs() < 1e-9, "at-boundary got={}", got);
}

#[test]
fn trait_result_with_default_delegates_to_result() {
    // The trait's default `result_with(input, snr, dt)` ignores snr/dt and
    // delegates to `result(input)` (plan ambiguity resolution #2; only the
    // GP predictor overrides it, dossier §6.8.3). result() mutates state,
    // so compare twin instances advanced in lockstep.
    let mut a = Hysteresis::new(0.1, 0.7, 0.2);
    let mut b = Hysteresis::new(0.1, 0.7, 0.2);
    assert_eq!(a.result_with(1.0, 37.0, 2.5), b.result(1.0)); // 0.63
    assert_eq!(a.result_with(-0.5, 1.0, 0.1), b.result(-0.5)); // uses last_move
    let mut c = ResistSwitch::default();
    let mut d = ResistSwitch::default();
    for _ in 0..3 {
        assert_eq!(c.result_with(0.3, 5.0, 1.0), d.result(0.3));
    }
    // Pin the final value too (frame 3 of the requires-three ramp).
    assert!((c.result_with(0.3, 5.0, 1.0) - 0.3).abs() < 1e-9);
    assert!((d.result(0.3) - 0.3).abs() < 1e-9);
}
