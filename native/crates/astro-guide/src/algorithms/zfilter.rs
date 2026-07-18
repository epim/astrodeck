// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§6.5). Derived from
// PHD2 guide_algorithm_zfilter.cpp:36-114 (constants, `reset`, `result`) and
// guide_algorithm_zfilter.cpp:116-218 (`BuildFilter`/`SetMinMove`/
// `SetExpFactor`, folded into `new`), plus the filter-coefficient generator
// zfilterfactory.cpp:50-260 / zfilterfactory.h:42-115 (`ZFilterFactory`: the
// Bessel pole table, s-plane synthesis, bilinear transform, and recurrence
// coefficients) (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied
// from PHD2.
//
// Additional algorithm provenance (spec §3.6): `ZFilterFactory` is itself a
// C++ port of A.J. Fisher's `mkfilter`/`mkshape`/`gencode` tool (University
// of York, Sept. 1992, <https://www-users.cs.york.ac.uk/~fisher/mkfilter/>),
// as PHD2 cites at `zfilterfactory.cpp:35-38`. The Bessel pole table below
// and the six mkfilter synthesis steps (s-plane poles, prewarp, normalize,
// bilinear z-transform, polynomial expansion, recurrence coefficients) trace
// to that tool; PHD2 redistributes the ported values/steps under its own
// BSD-3-Clause license (Ken Self, 2018) with no separate mkfilter license
// file, so this crate's Apache-2.0 header + the BSD-3 citation above is the
// complete provenance chain. The Chebyshev branch is intentionally omitted
// (dossier §6.5/§17: it reads an uninitialized `chripple` member upstream —
// "dead/buggy", not reachable via any shipped UI — so this is not a partial
// port, it is a deliberate scope cut).

//! ZFilter guide algorithm — an IIR low-pass filter with drift correction
//! (dossier §6.5).
//!
//! Unlike every other algorithm in this crate, ZFilter's min-move deadband
//! tests the *output* correction, not the raw input (dossier §6.7), and its
//! coefficients come from a digital-filter synthesis (Bessel or Butterworth
//! prototype, bilinear-transformed) rather than a simple formula.

use std::collections::VecDeque;
use std::f64::consts::PI;
use std::ops::{Add, Div, Mul, Neg, Sub};

use super::GuideAlgorithm;

// ---------------------------------------------------------------------------
// Minimal complex arithmetic (mkfilter's `std::complex<double>` calls: add,
// subtract, multiply, divide, conjugate, magnitude, unit-circle placement).
// Hand-rolled rather than a new crate dependency — the workspace's only
// shared dependency is `nalgebra`, which has no complex-number support.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq)]
struct C64 {
    re: f64,
    im: f64,
}

impl C64 {
    const fn new(re: f64, im: f64) -> Self {
        C64 { re, im }
    }

    fn conj(self) -> Self {
        C64::new(self.re, -self.im)
    }

    /// `::hypot(dc_gain.imag(), dc_gain.real())` (`zfilterfactory.h:53`).
    fn abs(self) -> f64 {
        self.re.hypot(self.im)
    }

    /// `std::polar(1.0, theta)` (`zfilterfactory.cpp:128`): a unit-magnitude
    /// point at angle `theta`.
    fn polar(theta: f64) -> Self {
        C64::new(theta.cos(), theta.sin())
    }
}

impl Add for C64 {
    type Output = C64;
    fn add(self, rhs: C64) -> C64 {
        C64::new(self.re + rhs.re, self.im + rhs.im)
    }
}

impl Sub for C64 {
    type Output = C64;
    fn sub(self, rhs: C64) -> C64 {
        C64::new(self.re - rhs.re, self.im - rhs.im)
    }
}

impl Neg for C64 {
    type Output = C64;
    fn neg(self) -> C64 {
        C64::new(-self.re, -self.im)
    }
}

impl Mul for C64 {
    type Output = C64;
    fn mul(self, rhs: C64) -> C64 {
        C64::new(
            self.re * rhs.re - self.im * rhs.im,
            self.re * rhs.im + self.im * rhs.re,
        )
    }
}

impl Mul<f64> for C64 {
    type Output = C64;
    fn mul(self, rhs: f64) -> C64 {
        C64::new(self.re * rhs, self.im * rhs)
    }
}

impl Div for C64 {
    type Output = C64;
    fn div(self, rhs: C64) -> C64 {
        let denom = rhs.re * rhs.re + rhs.im * rhs.im;
        C64::new(
            (self.re * rhs.re + self.im * rhs.im) / denom,
            (self.im * rhs.re - self.re * rhs.im) / denom,
        )
    }
}

/// `(2.0 + pz) / (2.0 - pz)` (`zfilterfactory.h:95-98`): the bilinear
/// s-plane-to-z-plane transform (no MZT — the wrapper never requests it).
fn bilinear(s: C64) -> C64 {
    let two = C64::new(2.0, 0.0);
    (two + s) / (two - s)
}

// ---------------------------------------------------------------------------
// Bessel pole table (dossier §6.5, transcribed verbatim; one member of each
// conjugate pair; index `p = order*order/4`). `zfilterfactory.cpp:52-84`.
// ---------------------------------------------------------------------------

#[rustfmt::skip]
const BESSEL_POLES: [C64; 30] = [
    C64::new(-1.00000000000e+00, 0.00000000000e+00),
    C64::new(-1.10160133059e+00, 6.36009824757e-01),
    C64::new(-1.32267579991e+00, 0.00000000000e+00),
    C64::new(-1.04740916101e+00, 9.99264436281e-01),
    C64::new(-1.37006783055e+00, 4.10249717494e-01),
    C64::new(-9.95208764350e-01, 1.25710573945e+00),
    C64::new(-1.50231627145e+00, 0.00000000000e+00),
    C64::new(-1.38087732586e+00, 7.17909587627e-01),
    C64::new(-9.57676548563e-01, 1.47112432073e+00),
    C64::new(-1.57149040362e+00, 3.20896374221e-01),
    C64::new(-1.38185809760e+00, 9.71471890712e-01),
    C64::new(-9.30656522947e-01, 1.66186326894e+00),
    C64::new(-1.68436817927e+00, 0.00000000000e+00),
    C64::new(-1.61203876622e+00, 5.89244506931e-01),
    C64::new(-1.37890321680e+00, 1.19156677780e+00),
    C64::new(-9.09867780623e-01, 1.83645135304e+00),
    C64::new(-1.75740840040e+00, 2.72867575103e-01),
    C64::new(-1.63693941813e+00, 8.22795625139e-01),
    C64::new(-1.37384121764e+00, 1.38835657588e+00),
    C64::new(-8.92869718847e-01, 1.99832584364e+00),
    C64::new(-1.85660050123e+00, 0.00000000000e+00),
    C64::new(-1.80717053496e+00, 5.12383730575e-01),
    C64::new(-1.65239648458e+00, 1.03138956698e+00),
    C64::new(-1.36758830979e+00, 1.56773371224e+00),
    C64::new(-8.78399276161e-01, 2.14980052431e+00),
    C64::new(-1.92761969145e+00, 2.41623471082e-01),
    C64::new(-1.84219624443e+00, 7.27257597722e-01),
    C64::new(-1.66181024140e+00, 1.22110021857e+00),
    C64::new(-1.36069227838e+00, 1.73350574267e+00),
    C64::new(-8.65756901707e-01, 2.29260483098e+00),
];

/// Filter design/prototype family (dossier §6.5). No `Chebyshev` variant —
/// see this module's header note.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Design {
    Bessel,
    Butterworth,
}

/// `setpole` (`zfilterfactory.h:87-93`): only left-half-plane poles are
/// stable prototype poles.
fn setpole(spoles: &mut Vec<C64>, z: C64) {
    if z.re < 0.0 {
        spoles.push(z);
    }
}

/// `ZFilterFactory::splane` (`zfilterfactory.cpp:107-130`, Bessel/Butterworth
/// branches only).
fn splane(design: Design, order: usize) -> Vec<C64> {
    let mut spoles = Vec::with_capacity(order);
    match design {
        Design::Bessel => {
            let mut p = (order * order) / 4;
            if order % 2 == 1 {
                setpole(&mut spoles, BESSEL_POLES[p]);
                p += 1;
            }
            for _ in 0..order / 2 {
                setpole(&mut spoles, BESSEL_POLES[p]);
                setpole(&mut spoles, BESSEL_POLES[p].conj());
                p += 1;
            }
        }
        Design::Butterworth => {
            for i in 0..2 * order {
                let theta = if order % 2 == 1 {
                    (i as f64) * PI / (order as f64)
                } else {
                    (i as f64 + 0.5) * PI / (order as f64)
                };
                setpole(&mut spoles, C64::polar(theta));
            }
        }
    }
    spoles
}

/// `multin`: multiply the factor `(z - w)` into `coeffs` in place
/// (`zfilterfactory.cpp:245-252`).
fn multin(w: C64, coeffs: &mut [C64]) {
    let nw = -w;
    for i in (1..coeffs.len()).rev() {
        coeffs[i] = nw * coeffs[i] + coeffs[i - 1];
    }
    coeffs[0] = nw * coeffs[0];
}

/// `expand`: the monic polynomial (coefficients indexed by power of `z`)
/// whose roots are `pz` (`zfilterfactory.cpp:221-243`; the upstream
/// real-coefficient assertion is elided — every pole set this module builds
/// is closed under conjugation by construction).
fn expand(pz: &[C64]) -> Vec<C64> {
    let mut coeffs = vec![C64::new(1.0, 0.0)];
    coeffs.resize(pz.len() + 1, C64::new(0.0, 0.0));
    for &w in pz {
        multin(w, &mut coeffs);
    }
    coeffs
}

/// `eval`: Horner evaluation of a polynomial in `z` (`zfilterfactory.cpp:254-260`).
fn eval(coeffs: &[C64], z: C64) -> C64 {
    let mut sum = C64::new(0.0, 0.0);
    for &c in coeffs.iter().rev() {
        sum = sum * z + c;
    }
    sum
}

/// A synthesized digital filter's runtime coefficients: `xcoeffs`/`ycoeffs`
/// in the newest-first order [`ZFilter::result`] consumes directly, plus the
/// unnormalized DC gain magnitude used to pre-scale the reconstructed input.
#[derive(Debug, Clone, PartialEq)]
pub struct Filter {
    pub xcoeffs: Vec<f64>,
    pub ycoeffs: Vec<f64>,
    pub gain: f64,
}

impl Filter {
    /// Port of `ZFilterFactory`'s constructor pipeline
    /// (`zfilterfactory.cpp:50-105`: `splane` → `prewarp` → `normalize` →
    /// `zplane` → `expandpoly`), MZT and Chebyshev omitted (see this
    /// module's header note; upstream's wrapper never requests MZT).
    ///
    /// `corner_period` is the corner period in guide-exposure units
    /// (`exp_factor * 4.0` at the call site, dossier §6.5).
    pub fn build(design: Design, order: usize, corner_period: f64) -> Filter {
        assert!(
            order >= 1 && corner_period >= 2.0,
            "invalid filter order/corner_period"
        );
        // 1. s-plane prototype poles.
        let spoles = splane(design, order);
        // 2. prewarp (bilinear transform only — MZT is never requested).
        let raw_alpha1 = 1.0 / corner_period;
        let warped_alpha1 = (PI * raw_alpha1).tan() / PI;
        // 3. normalize: scale every pole by 2*PI*warped_alpha1.
        let w1 = 2.0 * PI * warped_alpha1;
        let spoles: Vec<C64> = spoles.into_iter().map(|s| s * w1).collect();
        // 4. bilinear z-transform; zeros fill with -1 until counts match.
        let zpoles: Vec<C64> = spoles.into_iter().map(bilinear).collect();
        let zzeros: Vec<C64> = std::iter::repeat_n(C64::new(-1.0, 0.0), zpoles.len()).collect();
        // 5. expand (z - root) products into top/bot polynomials; DC gain.
        let topcoeffs = expand(&zzeros);
        let botcoeffs = expand(&zpoles);
        let dc_gain = eval(&topcoeffs, C64::new(1.0, 0.0)) / eval(&botcoeffs, C64::new(1.0, 0.0));
        let gain = dc_gain.abs();
        // 6. recurrence coefficients, newest-first order.
        let leading = botcoeffs.last().expect("botcoeffs is never empty").re;
        let xcoeffs: Vec<f64> = topcoeffs.iter().rev().map(|c| c.re / leading).collect();
        let ycoeffs: Vec<f64> = botcoeffs.iter().rev().map(|c| -(c.re / leading)).collect();
        Filter {
            xcoeffs,
            ycoeffs,
            gain,
        }
    }
}

// ---------------------------------------------------------------------------
// ZFilter (dossier §6.5 runtime)
// ---------------------------------------------------------------------------

/// `DefaultMinMove` (`guide_algorithm_zfilter.cpp:36`).
const DEFAULT_MIN_MOVE: f64 = 0.1;
/// `DefaultExpFactor` (`guide_algorithm_zfilter.cpp:37`).
const DEFAULT_EXP_FACTOR: f64 = 2.0;
/// `m_order = 4` (`guide_algorithm_zfilter.cpp:43`): fixed at construction,
/// never exposed via `GetParamNames`/`SetParam`.
const ORDER: usize = 4;
/// `corner = m_expFactor * 4.0` (`guide_algorithm_zfilter.cpp:132`).
const CORNER_PERIOD_FACTOR: f64 = 4.0;
/// Below this corner period, `BuildFilter` silently substitutes Butterworth
/// for the requested (always-Bessel) design (`guide_algorithm_zfilter.cpp:133`).
const BUTTERWORTH_FALLBACK_CORNER: f64 = 6.0;

/// IIR low-pass with drift correction (dossier §6.5;
/// `GuideAlgorithmZFilter`). Reconstructs the *uncorrected* waveform each
/// frame (`input + sum_corr`), filters it, and returns the delta between the
/// filtered estimate and the correction already issued — so the min-move
/// deadband tests the *output*, uniquely among this crate's algorithms
/// (dossier §6.7).
#[derive(Debug, Clone)]
pub struct ZFilter {
    pub min_move: f64,
    pub exp_factor: f64,
    xcoeffs: Vec<f64>,
    ycoeffs: Vec<f64>,
    gain: f64,
    /// `m_xv`: reconstructed-input history, newest at index 0.
    xv: VecDeque<f64>,
    /// `m_yv`: filtered-output history, newest at index 0.
    yv: VecDeque<f64>,
    /// `m_sumCorr`: running sum of every correction this instance has
    /// issued (`fabs(dReturn) < m_minMove` frames add `0.0`, i.e. leave it
    /// unchanged — `guide_algorithm_zfilter.cpp:95-99`).
    sum_corr: f64,
}

impl ZFilter {
    /// Port of `ZFilterFactory`'s coefficient synthesis
    /// (`zfilterfactory.cpp:50-105`). Exposed under `ZFilter`'s own
    /// namespace (this task's frozen interface) as a thin forward to
    /// [`Filter::build`].
    pub fn build(design: Design, order: usize, corner_period: f64) -> Filter {
        Filter::build(design, order, corner_period)
    }

    /// Constructs with upstream's constructor-time validation folded in
    /// (`SetMinMove`/`SetExpFactor`, both of which also rebuild the filter
    /// and reset state — `guide_algorithm_zfilter.cpp:168-218`): `min_move
    /// < 0` falls back to the default; `exp_factor < 1.0` falls back to the
    /// default. The design is always requested as Bessel, order 4
    /// (`guide_algorithm_zfilter.cpp:42-43`); `BuildFilter` substitutes
    /// Butterworth whenever the resulting corner period is below
    /// [`BUTTERWORTH_FALLBACK_CORNER`] (`:132-133`) — so `exp_factor 1.0`
    /// (corner 4.0) yields Butterworth-4, and the default `exp_factor 2.0`
    /// (corner 8.0) yields Bessel-4.
    pub fn new(min_move: f64, exp_factor: f64) -> Self {
        let min_move = if min_move < 0.0 {
            DEFAULT_MIN_MOVE
        } else {
            min_move
        };
        let exp_factor = if exp_factor < 1.0 {
            DEFAULT_EXP_FACTOR
        } else {
            exp_factor
        };
        let corner = exp_factor * CORNER_PERIOD_FACTOR;
        let design = if corner < BUTTERWORTH_FALLBACK_CORNER {
            Design::Butterworth
        } else {
            Design::Bessel
        };
        let filter = Filter::build(design, ORDER, corner);
        let n_x = filter.xcoeffs.len();
        let n_y = filter.ycoeffs.len();
        ZFilter {
            min_move,
            exp_factor,
            xcoeffs: filter.xcoeffs,
            ycoeffs: filter.ycoeffs,
            gain: filter.gain,
            xv: VecDeque::from(vec![0.0; n_x]),
            yv: VecDeque::from(vec![0.0; n_y]),
            sum_corr: 0.0,
        }
    }
}

impl Default for ZFilter {
    /// §6.5/§15 defaults: min_move 0.1, exp_factor 2.0 (Bessel-4, corner 8.0).
    fn default() -> Self {
        ZFilter::new(DEFAULT_MIN_MOVE, DEFAULT_EXP_FACTOR)
    }
}

impl GuideAlgorithm for ZFilter {
    /// dossier §6.5; `GuideAlgorithmZFilter::result`,
    /// `guide_algorithm_zfilter.cpp:71-114`. `xv`/`yv` are always shifted
    /// (push-front/pop-back) before the deadband check, so history keeps
    /// updating even on a vetoed frame — only `sum_corr`'s increment (`0.0`
    /// on veto) is affected.
    fn result(&mut self, input: f64) -> f64 {
        self.xv.push_front((input + self.sum_corr) / self.gain);
        self.xv.pop_back();
        self.yv.push_front(0.0);
        self.yv.pop_back();

        let mut y0 = 0.0;
        for i in 0..self.xcoeffs.len() {
            y0 += self.xv[i] * self.xcoeffs[i];
        }
        for i in 1..self.ycoeffs.len() {
            y0 += self.yv[i] * self.ycoeffs[i];
        }
        self.yv[0] = y0;

        let mut r = y0 - self.sum_corr;
        if r.abs() < self.min_move {
            r = 0.0; // NOTE: min-move applies to the OUTPUT, not the input.
        }
        self.sum_corr += r;
        r
    }

    /// `guide_algorithm_zfilter.cpp:62-69`.
    fn reset(&mut self) {
        for v in self.xv.iter_mut() {
            *v = 0.0;
        }
        for v in self.yv.iter_mut() {
            *v = 0.0;
        }
        self.sum_corr = 0.0;
    }

    fn min_move(&self) -> f64 {
        self.min_move
    }
}
