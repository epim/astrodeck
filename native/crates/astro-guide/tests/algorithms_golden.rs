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
