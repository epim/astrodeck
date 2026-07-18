// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§6.8.4-§6.8.5).
// Derived from PHD2's bundled MPI_IS_gaussian_process contribution:
//   contributions/MPI_IS_gaussian_process/src/covariance_functions.cpp
//     (the PeriodicSquareExponential / PeriodicSquareExponential2 kernels),
//   contributions/MPI_IS_gaussian_process/src/gaussian_process.cpp
//     (GP::inferSD subset-of-data selection + GP::predict explicit-trend
//     posterior mean),
//   contributions/MPI_IS_gaussian_process/src/gaussian_process_guider.cpp
//     (regularize_dataset, EstimatePeriodLength, the ridge de-trend, the
//     natural<->internal hyperparameter conversion),
//   contributions/MPI_IS_gaussian_process/tools/math_tools.cpp
//     (squareDistance, compute_spectrum, hamming_window).
// This GP toolbox carries Max Planck Society BSD-3-Clause provenance
// (Klenske, Zeilinger, Schölkopf & Hennig, "Gaussian Process Based Predictive
// Control for Periodic Error Correction," IEEE Transactions on Control Systems
// Technology 24(1):110-121, 2016). See THIRD-PARTY-NOTICES.md. No code copied
// from PHD2 or the MPI-IS contribution.
//
// Eigen replacement: all linear algebra uses `nalgebra` (dossier §6.8 note —
// "Eigen does not travel"). Upstream uses Eigen's `.ldlt()` (Bunch-Kaufman)
// robust factorization; every matrix factored in PPEC's data path is
// symmetric positive-definite (the Gram matrix is a PSD kernel plus a strictly
// positive heteroscedastic noise diagonal — measurement variances are
// `sd² > 0` and dark variance is 1e4; the 2×2 trend feature matrix and the
// ridge/FFT normal-equation matrices are PD by construction), so
// `nalgebra`'s Cholesky (LLT) is an exact-for-PD substitute for LDLT. A
// full-pivot-free LU fallback (and finally NaN, which the controller's user
// safeguard converts to hysteresis) guards the never-observed non-PD case.
// ADJUDICATION (upstream `gaussian_process.cpp:206` `.ldlt()` vs dossier
// §6.8.4 "LDLT factorization"): both are followed — LLT and LDLT produce the
// same solve on a PD matrix; the substitution is unobservable at guiding
// tolerances.

//! Pure Gaussian-process math for the predictive-PEC guide algorithm (dossier
//! §6.8.4-§6.8.5): covariance kernels, the fixed-grid regularizer, the ridge
//! de-trend, FFT period identification, and the subset-of-data GP inference /
//! projected-posterior prediction. No controller state lives here — see
//! [`super::gaussian_process`].

use std::f64::consts::PI;

use nalgebra::linalg::{Cholesky, LU};
use nalgebra::{Complex, DMatrix, DVector, Dyn};

use super::GpParams;

/// Errors surfaced by the GP math layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GpError {
    /// The regularizer's running grid index overran its allocation while
    /// dithering (`gaussian_process_guider.cpp:716-723`). The controller
    /// resets its model in response (dossier §6.8.6).
    RegularizeOverrun,
}

// ===========================================================================
// Covariance kernel (dossier §6.8.4; covariance_functions.cpp:139-161)
// ===========================================================================

/// The composite covariance kernel in its *internal* (kernel-ready) form —
/// the values upstream stores in log space and `exp()`s at evaluation time
/// (`covariance_functions.cpp:142-149`): length scales are linear, signal
/// variances are already **squared** (amplitude px → variance px²), and the
/// periodic length scale is in the internal `4·sin(π·l/P)` notation.
///
/// The inference kernel (`PeriodicSquareExponential2`) is SE0 + Periodic +
/// SE1; the projection kernel (`PeriodicSquareExponential`) drops SE1 — see
/// [`Kernel::eval_pair`]'s `include_se1`.
#[derive(Debug, Clone, Copy)]
pub struct Kernel {
    /// SE0 (long-range) length scale, s.
    pub ls_se0: f64,
    /// SE0 signal variance (amplitude²), px².
    pub sv_se0: f64,
    /// Periodic length scale, **internal** units `4·sin(π·l_nat/P)`.
    pub ls_p: f64,
    /// Periodic signal variance (amplitude²), px².
    pub sv_p: f64,
    /// SE1 (short-range) length scale, s.
    pub ls_se1: f64,
    /// SE1 signal variance (amplitude²), px².
    pub sv_se1: f64,
    /// Periodic period length P, s.
    pub period: f64,
}

impl Kernel {
    /// Build from the seven *natural* hyperparameters in the upstream
    /// `Hyperparameters` enum order (`gaussian_process_guider.h:52-62`):
    /// `[SE0_len, SE0_amp, Per_len, Per_amp, SE1_len, SE1_amp, P]`, applying
    /// `SetGPHyperparameters`'s transforms (`gaussian_process_guider.cpp:489-512`):
    /// length scales floored at 1.0, the periodic length scale converted
    /// natural→internal `4·sin(π·l/P)`, then every value floored at 1e-10 and
    /// (for signal variances) squared.
    pub fn from_natural(h: [f64; 7]) -> Self {
        let mut se0_len = h[0].max(1.0);
        let se0_amp = h[1];
        let mut per_len = h[2].max(1.0);
        let per_amp = h[3];
        let mut se1_len = h[4].max(1.0);
        let se1_amp = h[5];
        let period = h[6];

        // natural → internal (must use the post-floor period)
        per_len = 4.0 * (per_len * PI / period).sin();

        // final 1e-10 floor on all seven (log-conversion safeguard)
        se0_len = se0_len.max(1e-10);
        per_len = per_len.max(1e-10);
        se1_len = se1_len.max(1e-10);
        Kernel {
            ls_se0: se0_len,
            sv_se0: se0_amp.max(1e-10).powi(2),
            ls_p: per_len,
            sv_p: per_amp.max(1e-10).powi(2),
            ls_se1: se1_len,
            sv_se1: se1_amp.max(1e-10).powi(2),
            period: period.max(1e-10),
        }
    }

    /// The §6.8.1 default hyperparameters routed through [`Kernel::from_natural`].
    pub fn from_params(p: &GpParams) -> Self {
        Kernel::from_natural(p.natural_hyperparameters())
    }

    /// Recover the seven *natural* hyperparameters (inverse of
    /// [`Kernel::from_natural`]; `gaussian_process_guider.cpp:474-487`): the
    /// periodic length scale is un-converted `asin(l/4)·P/π`, signal variances
    /// are square-rooted back to amplitudes.
    pub fn to_natural(&self) -> [f64; 7] {
        [
            self.ls_se0,
            self.sv_se0.sqrt(),
            (self.ls_p / 4.0).asin() * self.period / PI,
            self.sv_p.sqrt(),
            self.ls_se1,
            self.sv_se1.sqrt(),
            self.period,
        ]
    }

    /// Evaluate `k(a, b)` for scalar time locations (dossier §6.8.4;
    /// `covariance_functions.cpp:159-161`). `include_se1` selects the
    /// inference kernel (`true`, PeriodicSquareExponential2) vs the projection
    /// kernel (`false`, PeriodicSquareExponential — SE1 modeled but excluded
    /// from predictions). `d = |a − b|`.
    ///
    /// ADJUDICATION (upstream `math_tools.cpp:52-113` `squareDistance` vs
    /// dossier §6.8.4 `d = |t1 − t2|`): upstream computes the squared distance
    /// via a mean-subtracted binomial expansion (numerical-stability trick for
    /// the general D-dimensional case) then `.max(0.0)`. For scalar
    /// timestamps `(a − b)²` is computed directly here — mathematically
    /// identical, matching the dossier's `d` literally and upstream to
    /// floating-point rounding (unobservable at px guiding tolerances). The
    /// `.max(0.0)` is preserved implicitly: `(a − b)²` is non-negative.
    #[inline]
    pub fn eval_pair(&self, a: f64, b: f64, include_se1: bool) -> f64 {
        let d2 = (a - b) * (a - b);
        let d = d2.sqrt();
        let se0 = self.sv_se0 * (-0.5 / (self.ls_se0 * self.ls_se0) * d2).exp();
        let s = (PI / self.period * d).sin() / self.ls_p;
        let per = self.sv_p * (-2.0 * s * s).exp();
        let mut k = se0 + per;
        if include_se1 {
            k += self.sv_se1 * (-0.5 / (self.ls_se1 * self.ls_se1) * d2).exp();
        }
        k
    }

    /// `k(xs, ys)` as an `xs.len() × ys.len()` matrix.
    fn eval_matrix(&self, xs: &[f64], ys: &[f64], include_se1: bool) -> DMatrix<f64> {
        DMatrix::from_fn(xs.len(), ys.len(), |i, j| {
            self.eval_pair(xs[i], ys[j], include_se1)
        })
    }
}

// ===========================================================================
// Measurement noise (dossier §6.8.2; gaussian_process_guider.cpp:121-129)
// ===========================================================================

/// Measurement variance from a star's SNR (empirically fit, dossier §6.8.2):
/// `sd = 2.1752/(max(SNR,3.4) − 3.3) + 0.5`, variance `= sd²`.
pub fn variance_from_snr(snr: f64) -> f64 {
    let snr = snr.max(3.4);
    let sd = 2.1752 / (snr - 3.3) + 0.5;
    sd * sd
}

// ===========================================================================
// Linear-algebra helpers (LDLT-equivalent solves on PD matrices)
// ===========================================================================

/// A cached factorization for reuse across multiple right-hand sides (the
/// Gram matrix is solved once for `alpha`, once for the trend features, and
/// again for `gamma` at prediction time). Cholesky for the PD case (always
/// taken in PPEC's data path), LU as defense.
enum SpdSolve {
    Chol(Cholesky<f64, Dyn>),
    Lu(LU<f64, Dyn, Dyn>),
}

impl SpdSolve {
    fn new(m: DMatrix<f64>) -> Self {
        match m.clone().cholesky() {
            Some(c) => SpdSolve::Chol(c),
            None => SpdSolve::Lu(m.lu()),
        }
    }
    fn solve(&self, b: &DMatrix<f64>) -> DMatrix<f64> {
        match self {
            SpdSolve::Chol(c) => c.solve(b),
            SpdSolve::Lu(l) => l
                .solve(b)
                .unwrap_or_else(|| DMatrix::from_element(b.nrows(), b.ncols(), f64::NAN)),
        }
    }
    fn solve_vec(&self, b: &DVector<f64>) -> DVector<f64> {
        match self {
            SpdSolve::Chol(c) => c.solve(b),
            SpdSolve::Lu(l) => l
                .solve(b)
                .unwrap_or_else(|| DVector::from_element(b.nrows(), f64::NAN)),
        }
    }
}

/// One-shot LDLT-equivalent solve `A x = b` for a (symmetric PD) `A`.
fn ldlt_solve(a: &DMatrix<f64>, b: &DVector<f64>) -> DVector<f64> {
    if let Some(c) = a.clone().cholesky() {
        return c.solve(b);
    }
    if let Some(x) = a.clone().lu().solve(b) {
        return x;
    }
    DVector::from_element(b.nrows(), f64::NAN)
}

// ===========================================================================
// Regularizer (dossier §6.8.4 step 2; gaussian_process_guider.cpp:686-760)
// ===========================================================================

/// Regularize a heteroscedastic irregular time series onto a fixed
/// `grid_interval`-second grid by trapezoidal integration per cell (dossier
/// §6.8.4 step 2). Cell value = ∫ over the cell / `grid_interval`; cell-center
/// timestamps are `cell_start + grid_interval/2`; cell boundaries are linearly
/// interpolated. The output is capped at `reg_buffer_size` cells
/// (`head(min(j, REGULAR_BUFFER_SIZE))`).
///
/// While `dithering_active`, a running index that would overrun the grid
/// allocation throws [`GpError::RegularizeOverrun`] (the controller resets in
/// response, dossier §6.8.6). Off the dithering path a growable buffer is used
/// (Rust `Vec` — the latent upstream fixed-size write past `grid_size` cannot
/// occur here; output is identical whenever `grid_size` was adequate, which it
/// is by construction: `grid_size = ceil(t_last/grid_interval)+1`).
#[allow(clippy::type_complexity)]
pub fn regularize(
    timestamps: &[f64],
    gear_error: &[f64],
    variances: &[f64],
    grid_interval: f64,
    reg_buffer_size: usize,
    dithering_active: bool,
) -> Result<(Vec<f64>, Vec<f64>, Vec<f64>), GpError> {
    let n = timestamps.len();
    debug_assert!(n > 0 && gear_error.len() == n && variances.len() == n);

    let mut last_cell_end = -grid_interval;
    let mut last_timestamp = -grid_interval;
    let mut last_gear_error = 0.0;
    let mut last_variance = 0.0;
    let mut gear_error_sum = 0.0;
    let mut variance_sum = 0.0;

    // grid_size mirrors upstream's allocation size — only used as the
    // dithering overrun bound.
    let grid_size = (timestamps[n - 1] / grid_interval).ceil() as i64 + 1;

    let mut reg_timestamps: Vec<f64> = Vec::new();
    let mut reg_gear_error: Vec<f64> = Vec::new();
    let mut reg_variances: Vec<f64> = Vec::new();
    let mut j: i64 = 0;

    for i in 0..n {
        if timestamps[i] < last_cell_end + grid_interval {
            gear_error_sum +=
                (timestamps[i] - last_timestamp) * 0.5 * (last_gear_error + gear_error[i]);
            variance_sum += (timestamps[i] - last_timestamp) * 0.5 * (last_variance + variances[i]);
            last_timestamp = timestamps[i];
        } else {
            while timestamps[i] >= last_cell_end + grid_interval {
                if dithering_active && j >= grid_size {
                    return Err(GpError::RegularizeOverrun);
                }
                let inter_timestamp = last_cell_end + grid_interval;

                let proportion =
                    (inter_timestamp - last_timestamp) / (timestamps[i] - last_timestamp);
                let inter_gear_error =
                    proportion * gear_error[i] + (1.0 - proportion) * last_gear_error;
                let inter_variance = proportion * variances[i] + (1.0 - proportion) * last_variance;

                gear_error_sum +=
                    (inter_timestamp - last_timestamp) * 0.5 * (last_gear_error + inter_gear_error);
                variance_sum +=
                    (inter_timestamp - last_timestamp) * 0.5 * (last_variance + inter_variance);

                reg_timestamps.push(last_cell_end + 0.5 * grid_interval);
                reg_gear_error.push(gear_error_sum / grid_interval);
                reg_variances.push(variance_sum / grid_interval);

                last_timestamp = inter_timestamp;
                last_gear_error = inter_gear_error;
                last_variance = inter_variance;
                last_cell_end = inter_timestamp;

                gear_error_sum = 0.0;
                variance_sum = 0.0;
                j += 1;
            }
        }
    }

    // head(min(j, REGULAR_BUFFER_SIZE)) — upstream caps with a strict `>`
    // (j may equal reg_buffer_size and survive).
    let mut jj = j as usize;
    if jj > reg_buffer_size {
        jj = reg_buffer_size;
    }
    reg_timestamps.truncate(jj);
    reg_gear_error.truncate(jj);
    reg_variances.truncate(jj);
    Ok((reg_timestamps, reg_gear_error, reg_variances))
}

// ===========================================================================
// Ridge de-trend (dossier §6.8.4 step 3; gaussian_process_guider.cpp:183-197)
// ===========================================================================

/// Subtract the ridge-regularized linear fit `w = (F·Fᵀ + 1e-3·I)⁻¹ F·y`
/// (`F = [1; t]`) from the data — the de-trended series the FFT period search
/// consumes. Only the de-trend is needed downstream, so this returns
/// `y − Fᵀw`.
pub fn detrend(timestamps: &[f64], gear_error: &[f64]) -> Vec<f64> {
    let n = timestamps.len();
    // F·Fᵀ = [[Σ1, Σt],[Σt, Σt²]]; F·y = [Σy, Σt·y].
    let (mut s0, mut s1, mut s2) = (0.0f64, 0.0f64, 0.0f64);
    let (mut fy0, mut fy1) = (0.0f64, 0.0f64);
    for i in 0..n {
        let (t, y) = (timestamps[i], gear_error[i]);
        s0 += 1.0;
        s1 += t;
        s2 += t * t;
        fy0 += y;
        fy1 += t * y;
    }
    let a = DMatrix::from_row_slice(2, 2, &[s0 + 1e-3, s1, s1, s2 + 1e-3]);
    let b = DVector::from_row_slice(&[fy0, fy1]);
    let w = ldlt_solve(&a, &b);
    let (w0, w1) = (w[0], w[1]);
    (0..n)
        .map(|i| gear_error[i] - (w0 + w1 * timestamps[i]))
        .collect()
}

// ===========================================================================
// FFT period identification (dossier §6.8.5; math_tools.cpp:174-219)
// ===========================================================================

/// Smallest power of two `≥ n` (`math_tools.cpp:183`
/// `pow(2, ceil(log2(N)))`); floored at 1.
fn next_pow2(n: usize) -> usize {
    let mut p = 1usize;
    while p < n {
        p <<= 1;
    }
    p
}

/// In-place iterative radix-2 Cooley-Tukey forward DFT of a real signal
/// (length a power of two). Magnitudes are convention-independent, so the
/// forward sign matches upstream's Eigen FFT for the `|·|²` spectrum used
/// here. Zero external dependencies (dossier §6.8 FFT note — direct-DFT
/// acceptable at ≤4096 points; this radix-2 kernel is O(N log N) and stays
/// within the crate's no-new-deps preference).
fn fft_forward(input: &[f64]) -> Vec<Complex<f64>> {
    let n = input.len();
    let mut a: Vec<Complex<f64>> = input.iter().map(|&x| Complex::new(x, 0.0)).collect();

    // bit-reversal permutation
    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            a.swap(i, j);
        }
    }

    let mut len = 2usize;
    while len <= n {
        let ang = -2.0 * PI / len as f64; // forward FFT
        let wlen = Complex::new(ang.cos(), ang.sin());
        let mut i = 0usize;
        while i < n {
            let mut w = Complex::new(1.0, 0.0);
            for k in 0..len / 2 {
                let u = a[i + k];
                let v = a[i + k + len / 2] * w;
                a[i + k] = u + v;
                a[i + k + len / 2] = u - v;
                w *= wlen;
            }
            i += len;
        }
        len <<= 1;
    }
    a
}

/// Hamming window of length `n` (`math_tools.cpp:210-219`):
/// `w(x) = 0.54 − 0.46·cos(2πx)`, `x = linspace(0, 1, n)`.
fn hamming_window(n: usize) -> Vec<f64> {
    if n == 1 {
        return vec![0.54 - 0.46]; // linspace(0,1,1) = [0]
    }
    (0..n)
        .map(|i| {
            let x = i as f64 / (n as f64 - 1.0);
            0.54 - 0.46 * (2.0 * PI * x).cos()
        })
        .collect()
}

/// Power spectrum + frequency axis of a zero-padded signal
/// (`math_tools.cpp:174-208`): pad to `max(n_min, len)` rounded up to a power
/// of two, forward-FFT, drop the low `low = ceil(N/len)` padding-artifact bins
/// and everything above `N/2`, return `(|FFT|², frequencies/N)`.
fn compute_spectrum(data: &[f64], n_min: usize) -> (Vec<f64>, Vec<f64>) {
    let n_data = data.len();
    let n = next_pow2(n_min.max(n_data));
    let mut padded = vec![0.0f64; n];
    padded[..n_data].copy_from_slice(data);

    let spec = fft_forward(&padded);

    let low_index = (n as f64 / n_data as f64).ceil() as usize;
    let hi = n / 2;
    let count = hi + 1 - low_index;
    let mut spectrum = Vec::with_capacity(count);
    let mut frequencies = Vec::with_capacity(count);
    for (offset, c) in spec[low_index..=hi].iter().enumerate() {
        let k = low_index + offset;
        spectrum.push(c.norm_sqr());
        frequencies.push(k as f64 / n as f64);
    }
    (spectrum, frequencies)
}

/// Estimate the dominant period of a (regularized, de-trended) series via FFT
/// (dossier §6.8.5; `gaussian_process_guider.cpp:582-664`). Applies a Hamming
/// window, computes the padded spectrum, rescales frequencies by the average
/// grid step, zeros amplitudes for periods above `max_period`, then refines
/// the peak with a 3-point quadratic interpolation (normalized normal
/// equations). Returns the period length `1/f*`.
pub fn estimate_period_length(
    timestamps: &[f64],
    data: &[f64],
    fft_size: usize,
    max_period: f64,
) -> f64 {
    let n = data.len();
    let window = hamming_window(n);
    let windowed: Vec<f64> = data.iter().zip(&window).map(|(d, w)| d * w).collect();

    let (spectrum, frequencies) = compute_spectrum(&windowed, fft_size);

    // average grid step dt = (t_end − t_0)/(n − 1)
    let dt = (timestamps[n - 1] - timestamps[0]) / (n as f64 - 1.0);
    let freqs: Vec<f64> = frequencies.iter().map(|f| f / dt).collect();

    // zero out amplitudes for periods that are too long
    let mut amps = spectrum.clone();
    for (i, a) in amps.iter_mut().enumerate() {
        let period = 1.0 / freqs[i];
        if period > max_period {
            *a = 0.0;
        }
    }

    // argmax
    let mut max_index = 0usize;
    let mut max_val = f64::NEG_INFINITY;
    for (i, &a) in amps.iter().enumerate() {
        if a > max_val {
            max_val = a;
            max_index = i;
        }
    }
    let mut max_frequency = freqs[max_index];

    // 3-point quadratic interpolation (only if the peak has both neighbours)
    if max_index > 0 && max_index < freqs.len() - 1 {
        let spread = (freqs[max_index - 1] - freqs[max_index + 1]).abs();

        let interp_loc = [
            (freqs[max_index - 1] - max_frequency) / spread,
            (freqs[max_index] - max_frequency) / spread,
            (freqs[max_index + 1] - max_frequency) / spread,
        ];
        let denom = amps[max_index];
        let interp_dat = [
            amps[max_index - 1] / denom,
            amps[max_index] / denom,
            amps[max_index + 1] / denom,
        ];

        let dat_max = interp_dat.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let dat_min = interp_dat.iter().cloned().fold(f64::INFINITY, f64::min);
        if dat_max - dat_min >= 1e-10 {
            // phi rows = [x², x, 1] over the three points (3×3); solve
            // (phi·phiᵀ) w = phi·interp_dat.
            let phi = DMatrix::from_fn(3, 3, |r, c| match r {
                0 => interp_loc[c] * interp_loc[c],
                1 => interp_loc[c],
                _ => 1.0,
            });
            let dat = DVector::from_row_slice(&interp_dat);
            let a = &phi * phi.transpose();
            let rhs = &phi * dat;
            let w = ldlt_solve(&a, &rhs);
            // de-normalize: f* = f_peak − w1/(2 w0)·spread
            max_frequency -= w[1] / (2.0 * w[0]) * spread;
        }
    }

    1.0 / max_frequency
}

// ===========================================================================
// GP inference + projected prediction
// (dossier §6.8.4 step 5 / §6.8.4 Prediction; gaussian_process.cpp:186-423)
// ===========================================================================

/// A trained subset-of-data GP with an explicit linear trend, holding exactly
/// what the projected-posterior mean needs (`gaussian_process.cpp:388-423`):
/// the selected data locations, the cached Gram factorization (reused for
/// `gamma`), `alpha = K⁻¹y`, the 2×n trend feature vectors, `beta`, and the
/// kernel (for evaluating the projection covariance).
pub(crate) struct GpModel {
    empty: bool,
    data_loc: Vec<f64>,
    alpha: DVector<f64>,
    gram: SpdSolve,
    feature_vectors: DMatrix<f64>,
    beta: DVector<f64>,
    kernel: Kernel,
}

impl GpModel {
    pub(crate) fn empty() -> Self {
        GpModel {
            empty: true,
            data_loc: Vec::new(),
            alpha: DVector::zeros(0),
            gram: SpdSolve::Chol(DMatrix::<f64>::identity(0, 0).cholesky().unwrap()),
            feature_vectors: DMatrix::zeros(2, 0),
            beta: DVector::zeros(2),
            kernel: Kernel::from_natural([1.0; 7]),
        }
    }

    /// Subset-of-data inference (`GP::inferSD`, `gaussian_process.cpp:237-302`):
    /// rank the `data_loc` points by their inference-kernel covariance with the
    /// single `prediction_point`, keep the top `n_points`, then run exact GP
    /// inference on that subset with the heteroscedastic noise diagonal.
    pub(crate) fn infer_sd(
        &mut self,
        data_loc: &[f64],
        data_out: &[f64],
        n_points: usize,
        data_var: &[f64],
        prediction_point: f64,
        kernel: &Kernel,
    ) {
        let n_data = data_loc.len();
        if n_data == 0 {
            self.empty = true;
            return;
        }
        let pred_loc = if prediction_point.is_nan() {
            data_loc[n_data - 1]
        } else {
            prediction_point
        };

        // covariance between each data point and the prediction point
        // (inference kernel), for point selection.
        let cov: Vec<f64> = data_loc
            .iter()
            .map(|&x| kernel.eval_pair(x, pred_loc, true))
            .collect();

        // sort indices by DESCENDING covariance. ADJUDICATION
        // (`gaussian_process.cpp:52-56/:265` std::sort, unstable, vs a stable
        // sort here): ties in covariance require identical timestamps, which
        // the regularizer's distinct grid-cell centers preclude — the tie
        // path is unreachable, so stable vs unstable is unobservable.
        let mut index: Vec<usize> = (0..n_data).collect();
        index.sort_by(|&a, &b| {
            cov[b]
                .partial_cmp(&cov[a])
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        if n_points < n_data {
            let idx = &index[..n_points];
            let loc: Vec<f64> = idx.iter().map(|&i| data_loc[i]).collect();
            let out: Vec<f64> = idx.iter().map(|&i| data_out[i]).collect();
            let var: Vec<f64> = idx.iter().map(|&i| data_var[i]).collect();
            self.infer(&loc, &out, &var, kernel);
        } else {
            self.infer(data_loc, data_out, data_var, kernel);
        }
    }

    /// Exact GP inference on a (sub)set with an explicit linear trend
    /// (`GP::infer`, `gaussian_process.cpp:186-223`): Gram `K = k(X,X) +
    /// diag(var)` (heteroscedastic — no jitter, dossier §6.8.4 note),
    /// `alpha = K⁻¹y`, `feature_matrix = Φ K⁻¹ Φᵀ`, `beta =
    /// feature_matrix⁻¹ Φ alpha`.
    fn infer(&mut self, loc: &[f64], out: &[f64], var: &[f64], kernel: &Kernel) {
        let n = loc.len();
        let mut gram = kernel.eval_matrix(loc, loc, true);
        for i in 0..n {
            gram[(i, i)] += var[i];
        }
        let solver = SpdSolve::new(gram);

        let y = DVector::from_row_slice(out);
        let alpha = solver.solve_vec(&y);

        // Φ = [1; loc]  (2×n)
        let fv = DMatrix::from_fn(2, n, |r, c| if r == 0 { 1.0 } else { loc[c] });
        // feature_matrix = Φ K⁻¹ Φᵀ  (2×2)
        let k_inv_fvt = solver.solve(&fv.transpose());
        let feature_matrix = &fv * &k_inv_fvt;
        // beta = feature_matrix⁻¹ (Φ alpha)  (2)
        let fv_alpha = &fv * &alpha;
        let beta = ldlt_solve(&feature_matrix, &fv_alpha);

        self.data_loc = loc.to_vec();
        self.alpha = alpha;
        self.gram = solver;
        self.feature_vectors = fv;
        self.beta = beta;
        self.kernel = *kernel;
        self.empty = false;
    }

    /// Projected-posterior mean at `locations` (`GP::predictProjected` +
    /// `GP::predict`, `gaussian_process.cpp:343-423`): uses the **projection**
    /// kernel (no SE1). Returns `k*·alpha + Rᵀ·beta`, `R = Φ* − Φ K⁻¹ k(X,x*)`.
    /// Empty model → zeros (`gaussian_process.cpp:362-369`).
    pub(crate) fn predict_projected(&self, locations: &[f64]) -> Vec<f64> {
        let m = locations.len();
        if self.empty {
            return vec![0.0; m];
        }
        // mixed_cov = k_proj(locations, data_loc)  (m × n)
        let mixed = self.kernel.eval_matrix(locations, &self.data_loc, false);
        // mean from alpha
        let mut mean = &mixed * &self.alpha; // m
                                             // gamma = K⁻¹ mixedᵀ  (n × m)
        let gamma = self.gram.solve(&mixed.transpose());
        // Φ* = [1; locations]  (2 × m)
        let phi = DMatrix::from_fn(2, m, |r, c| if r == 0 { 1.0 } else { locations[c] });
        // R = Φ* − Φ·gamma  (2 × m)
        let r = &phi - &self.feature_vectors * &gamma;
        mean += r.transpose() * &self.beta;
        mean.iter().copied().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn next_pow2_rounds_up() {
        assert_eq!(next_pow2(1), 1);
        assert_eq!(next_pow2(2), 2);
        assert_eq!(next_pow2(3), 4);
        assert_eq!(next_pow2(200), 256);
        assert_eq!(next_pow2(4096), 4096);
        assert_eq!(next_pow2(4097), 8192);
    }

    #[test]
    fn fft_of_impulse_is_flat() {
        // FFT of [1,0,0,0] is all ones (magnitude).
        let s = fft_forward(&[1.0, 0.0, 0.0, 0.0]);
        for c in &s {
            assert!((c.norm_sqr() - 1.0).abs() < 1e-12);
        }
    }

    #[test]
    fn to_natural_inverts_from_natural() {
        let h = [700.0, 20.0, 10.0, 20.0, 25.0, 10.0, 200.0];
        let k = Kernel::from_natural(h);
        let back = k.to_natural();
        for (a, b) in h.iter().zip(back.iter()) {
            assert!((a - b).abs() < 1e-9, "roundtrip {a} vs {b}");
        }
    }
}
