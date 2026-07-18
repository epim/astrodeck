// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's guide-algorithm port
// (src/algorithms/lowpass.rs, src/algorithms/zfilter.rs): Lowpass2 and
// ZFilter. Derived from PHD2 guide_algorithm_lowpass2.cpp:42-123,
// guide_algorithm_zfilter.cpp:36-114, and zfilterfactory.cpp:50-260 via the
// audited algorithm dossier docs/native-parity/algorithms/phd2-guiding.md
// (§6.4, §6.5) (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied
// from PHD2.
//
// Provenance for the *literal numbers* below: the Lowpass2 traces are
// computed step-by-step from the dossier §6.4 pseudocode (shown inline);
// the ZFilter coefficient/gain/trace literals are cross-checked against an
// independent from-scratch Python re-implementation of the mkfilter steps
// (splane/prewarp/normalize/zplane/expandpoly) and of `ZFilter::result`,
// written solely to generate these regression literals — not shared code
// with, and not derived from, the Rust port under test. That script also
// verified a differential case: an implementation that (incorrectly)
// folds a vetoed frame's PRE-deadband value into `sum_corr` produces a
// different, divergent output trace from frame 3 onward — confirming the
// literals below actually pin the "leaves sum_corr unchanged" behavior
// rather than passing vacuously.

use astro_guide::algorithms::{Design, Filter, GuideAlgorithm, Lowpass2, ZFilter};

// ---------------------------------------------------------------------------
// Lowpass2 (dossier §6.4)
// ---------------------------------------------------------------------------

#[test]
fn lowpass2_step_response_aggr80_minmove02() {
    // aggressiveness=80 (att=0.8), min_move=0.2. Warm-up (n<4): r=input*att.
    // Hand-computed transition at n=4/n=5 (dossier §6.4 least-squares:
    // slope = (n*Sxy - Sx*Sy) / (n*Sxx - Sx^2)):
    //  f0 in=0.4 n=1 warmup -> 0.4*0.8 = 0.32
    //  f1 in=0.4 n=2 warmup -> 0.32
    //  f2 in=0.4 n=3 warmup -> 0.32
    //  f3 in=0.6 n=4: x=[0,1,2,3] y=[.4,.4,.4,.6]; Sx=6 Sy=1.8 Sxy=3.0 Sxx=14
    //      slope=(4*3.0-6*1.8)/(4*14-36)=1.2/20=0.06
    //      r = slope*n*att = 0.06*4*0.8 = 0.192; same sign as input (0.6),
    //      |r|=0.192 < |input|=0.6 -> no clamp; |input| >= min_move -> 0.192
    //  f4 in=0.6 n=5: x=[0..4] y=[.4,.4,.4,.6,.6]; Sx=10 Sy=2.4 Sxy=5.4 Sxx=30
    //      slope=(5*5.4-10*2.4)/(5*30-100)=3/50=0.06
    //      r = 0.06*5*0.8 = 0.24
    //  f5 in=0.1 n=6: x=[0..5] y=[.4,.4,.4,.6,.6,.1]; Sx=15 Sy=2.5 Sxy=5.9 Sxx=55
    //      slope=(6*5.9-15*2.5)/(6*55-225)=(-2.1)/105=-0.02
    //      r_pre = -0.02*6*0.8 = -0.096; input*r_pre = 0.1*-0.096 < 0
    //      -> "wrong direction" veto -> r=0; also |0.1|<min_move(0.2) -> 0
    let mut lp = Lowpass2::new(0.2, 80.0);
    let ins = [0.4, 0.4, 0.4, 0.6, 0.6, 0.1];
    let exp = [0.32, 0.32, 0.32, 0.192, 0.24, 0.0];
    for (i, (input, e)) in ins.iter().zip(exp.iter()).enumerate() {
        let got = lp.result(*input);
        assert!(
            (got - e).abs() < 1e-9,
            "frame {} in={} got={} exp={}",
            i,
            input,
            got,
            e
        );
    }
}

#[test]
fn lowpass2_reject_streak_resets_on_the_fourth_not_the_third() {
    // Same params (min_move=0.2, aggressiveness=80). Demonstrates the
    // "replicate code not comment" reject-streak quirk (dossier §17): the
    // upstream comment says "3-in-a-row" but the code checks
    // `m_rejects > 3`, i.e. reset fires on the FOURTH consecutive
    // over-projection, not the third.
    //  f0-2 in=0.2 warmup -> 0.2*0.8=0.16 each (n=1,2,3)
    //  f3 in=-0.237 n=4: slope-projection overshoots -> CLAMP, rejects=1,
    //     r=input*att=-0.1896 (not vetoed by deadband: |-.237|>=0.2)
    //  f4 in=-0.25  n=5: CLAMP again, rejects=2, r=-0.25*0.8=-0.2
    //  f5 in=-0.18  n=6: CLAMP again, rejects=3 (NOT reset yet); r=-0.18*0.8
    //     =-0.144, but |-0.18| < min_move(0.2) -> final deadband zeroes it
    //     to 0.0 (the reject count still advances even though the RETURNED
    //     value is masked by the deadband — deadband is the LAST step)
    //  f6 in=-0.77  n=7: CLAMP again, rejects=4 -> `4 > 3` -> reset() fires
    //     (history/time_base/rejects cleared); this frame's own return
    //     value is unaffected by that reset (computed before it):
    //     r=-0.77*0.8=-0.616
    //  f7 in=-0.3: post-reset, fresh warm-up (n=1<4) -> -0.3*0.8=-0.24
    let mut lp = Lowpass2::new(0.2, 80.0);
    let ins = [0.2, 0.2, 0.2, -0.237, -0.25, -0.18, -0.77, -0.3];
    let exp = [0.16, 0.16, 0.16, -0.1896, -0.2, 0.0, -0.616, -0.24];
    for (i, (input, e)) in ins.iter().zip(exp.iter()).enumerate() {
        let got = lp.result(*input);
        assert!(
            (got - e).abs() < 1e-9,
            "frame {} in={} got={} exp={}",
            i,
            input,
            got,
            e
        );
    }
}

// ---------------------------------------------------------------------------
// ZFilter (dossier §6.5)
// ---------------------------------------------------------------------------

#[test]
fn zfilter_bessel4_corner8_coefficients_match_pole_table_trace() {
    // Default filter: exp_factor=2.0 -> corner=8.0 (>= the 6.0 Butterworth
    // fallback threshold) -> Bessel order 4. Poles from the transcribed
    // table (dossier §6.5) at p=order*order/4=4: bessel_poles[4]/[5] and
    // their conjugates. Coefficients cross-checked via the independent
    // Python mkfilter re-implementation described in this file's header.
    let f = ZFilter::build(Design::Bessel, 4, 8.0);
    assert_eq!(f.xcoeffs.len(), 5);
    assert_eq!(f.ycoeffs.len(), 5);
    // xcoeffs are the expansion of (z+1)^4 (all four z-plane zeros are -1,
    // dossier §6.5 step 4) -- exactly the binomial coefficients, for BOTH
    // designs (design only changes the poles, never the zeros).
    let want_x = [1.0, 4.0, 6.0, 4.0, 1.0];
    for (got, want) in f.xcoeffs.iter().zip(want_x.iter()) {
        assert!((got - want).abs() < 1e-6, "xcoeffs got={:?}", f.xcoeffs);
    }
    let want_y = [
        -1.0,
        1.0156103580077445,
        -0.6166927902287863,
        0.1849849896690857,
        -0.023641293440889,
    ];
    for (got, want) in f.ycoeffs.iter().zip(want_y.iter()) {
        assert!(
            (got - want).abs() < 1e-6,
            "ycoeffs got={:?} want={:?}",
            f.ycoeffs,
            want_y
        );
    }
    assert!((f.gain - 36.38524125893774).abs() < 1e-6, "gain={}", f.gain);
}

#[test]
fn zfilter_build_is_reachable_from_zfilter_namespace() {
    // The task's frozen interface names this `ZFilter::build` (a thin
    // forward to `Filter::build`, dossier interfaces section).
    let a = ZFilter::build(Design::Butterworth, 4, 4.0);
    let b = Filter::build(Design::Butterworth, 4, 4.0);
    assert_eq!(a, b);
}

#[test]
fn zfilter_butterworth4_corner4_closed_loop_dc_gain_is_unity() {
    // exp_factor=1.0 -> corner=4.0 (< 6.0) -> Butterworth-4 fallback
    // (dossier §6.5). `gain` (the raw |dc_gain|) is generally NOT 1.0 for
    // this synthesis (e.g. a hand-derived order-1/corner-4 sanity check
    // gives dc_gain=2.0 exactly) -- but `result()`'s per-frame division by
    // `gain` before filtering is exactly what normalizes the CLOSED-LOOP
    // reconstruction to unity DC gain (dossier §6.5: "a constant input
    // eventually yields the same steady output through `result`
    // reconstruction"). Simulate a real guiding loop correcting a constant
    // D=1.0px drift: each frame's `input` is the residual AFTER every prior
    // correction (input_n = D - sum_corr_{n-1}), mirroring how the star's
    // measured error actually behaves under real guiding. If `gain` were
    // mis-scaled, this residual would converge to a nonzero fixed point (or
    // diverge) instead of ~0. min_move=0.0 so the deadband never masks the
    // raw convergence.
    let mut f = ZFilter::new(0.0, 1.0);
    let d = 1.0;
    let mut sum_corr = 0.0;
    let mut input = d;
    for _ in 0..100 {
        let r = f.result(input);
        sum_corr += r;
        input = d - sum_corr;
    }
    assert!(
        input.abs() < 1e-6,
        "residual did not converge to ~0: {}",
        input
    );
}

#[test]
fn zfilter_min_move_applies_to_output_and_leaves_sum_corr_unchanged_on_veto() {
    // Default filter (Bessel-4, corner=8.0, min_move=0.1). A constant
    // input=0.5 warms the filter up (frames 0-1 vetoed: the raw filtered
    // delta starts too small against 5-tap zero-filled history), then
    // clears the deadband (frames 2-4 pass through, growing `sum_corr` to a
    // NONZERO established value), then two more frames (0.02, then 0.5)
    // whose raw filtered delta is small enough to be vetoed AGAIN -- proving
    // "leaves sum_corr unchanged" from an already-nonzero baseline, not just
    // trivially from 0.0. (An implementation that folded the pre-deadband
    // value into `sum_corr` on veto would diverge starting at frame 2 — see
    // this file's header note.)
    let mut f = ZFilter::default();
    let ins = [0.5, 0.5, 0.5, 0.5, 0.5, 0.02, 0.02, 0.5];
    let exp = [
        0.0,
        0.0,
        0.22664166607733058,
        0.16745723322694592,
        0.13696718297263927,
        0.1071948273573432,
        0.0,
        0.0,
    ];
    for (i, (input, e)) in ins.iter().zip(exp.iter()).enumerate() {
        let got = f.result(*input);
        assert!(
            (got - e).abs() < 1e-9,
            "frame {} in={} got={} exp={}",
            i,
            input,
            got,
            e
        );
    }
}

#[test]
fn zfilter_reset_clears_history_and_sum_corr() {
    let mut f = ZFilter::default();
    for x in [0.5, 0.5, 0.5, 0.5, 0.5] {
        f.result(x);
    }
    f.reset();
    // Post-reset, the very first frame behaves exactly like a fresh
    // instance's first frame (zero-filled xv/yv, sum_corr=0.0).
    let mut fresh = ZFilter::default();
    assert_eq!(f.result(0.5), fresh.result(0.5));
}

#[test]
fn zfilter_constructor_fallbacks() {
    // min_move < 0 -> default 0.1; exp_factor < 1.0 -> default 2.0
    // (guide_algorithm_zfilter.cpp:168-218).
    let a = ZFilter::new(-1.0, 2.0);
    assert_eq!(a.min_move, 0.1);
    let b = ZFilter::new(0.1, 0.5);
    assert_eq!(b.exp_factor, 2.0);
    let c = ZFilter::new(0.3, 3.0);
    assert_eq!((c.min_move, c.exp_factor), (0.3, 3.0));
    assert_eq!(c.min_move(), 0.3);
}
