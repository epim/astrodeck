// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§6.8, §6.8.1-§6.8.6).
// Derived from PHD2's bundled MPI_IS_gaussian_process contribution
//   contributions/MPI_IS_gaussian_process/src/gaussian_process_guider.cpp
//     (the GaussianProcessGuider controller: the circular data buffer, the
//     SNR→variance model, the per-frame `result`/`deduceResult` control loop,
//     `UpdateGP`, `PredictGearError`, the hyperparameter get/set conversions,
//     and dither/reset bookkeeping)
// and the PHD2 wrapper
//   src/guide_algorithm_gaussian_process.cpp
//     (GuideAlgorithmGaussianProcess: the RA-only registration, default
//     parameters, and the `result(input, SNR, time_step)` /
//     `deduceResult(time_step)` entry points).
// The GP controller and GP regression carry Max Planck Society BSD-3-Clause
// provenance (Klenske, Zeilinger, Schölkopf & Hennig, "Gaussian Process Based
// Predictive Control for Periodic Error Correction," IEEE Transactions on
// Control Systems Technology 24(1):110-121, 2016); the PHD2 wrapper is
// BSD-3-Clause (Craig Stark / Bret McKee / openphdguiding.org). See
// THIRD-PARTY-NOTICES.md. No code copied from PHD2 or the MPI-IS contribution.

//! Gaussian-process "Predictive PEC" (PPEC) guide algorithm — RA-only
//! (dossier §6.8). Learns and predicts the periodic gear (worm) error with a
//! Gaussian process, folding a feed-forward prediction into an otherwise
//! proportional/hysteresis controller so periodic error is corrected even
//! below the seeing deadband.
//!
//! **Clock model (Eigen/wall-clock replacement).** Upstream reads a
//! `steady_clock` for measurement timestamps and prediction anchors. This
//! crate is I/O-free and deterministic (D1), so the algorithm holds no clock
//! of its own: it advances an internal clock by whatever per-frame `dt` (the
//! `time_step` argument) the engine hands it, reproducing upstream's midpoint
//! timestamps `now − dt/2 + dither_offset` and prediction anchors `now`,
//! `now + dt`. The first measurement frame anchors `now = 0` (upstream sets
//! `start_time_ = last_time_ = now` at that frame). Since A2 (finding M6) the
//! `dt` the engine passes is the GUARDED real frame-timestamp delta (the
//! engine's `gp_clock_dt` helper — a non-monotone or `> 10×` exposure delta
//! falls back to the exposure for that frame), NOT accumulated exposure, so
//! the synthesized clock tracks the real inter-frame period over which the
//! periodic error actually evolves. The algorithm is agnostic to which `dt`
//! it receives; the choice lives entirely in the engine.
//!
//! **Engine integration scope.** The engine drives PPEC through
//! [`GuideAlgorithm::result_with`] (per-frame RA correction) and
//! [`GuideAlgorithm::deduce_result`] (dead reckoning on a lost star, dossier
//! §3.3/§6.8.3). On a dither the engine forwards PHD2's `GuidingDithered`
//! gear-time correction to the algorithm through
//! [`GuideAlgorithm::dither_notify`] (A2): [`GaussianProcessGuider::
//! guiding_dithered`] is called by [`crate::engine::GuideEngine::dither`] via
//! that hook, compensating the gear-time gap IN PLACE so the trained model
//! SURVIVES the dither instead of being reset (the engine skips
//! `ra_algo.reset()` when the hook returns `true`).
//! [`GaussianProcessGuider::guiding_dither_settle_done`] is provided (and
//! unit-covered) for a future settle-completion wiring task but is not yet
//! called by [`crate::engine::GuideEngine`] — see the P4-T1 report.

use std::collections::VecDeque;

use super::gp_math::{self, GpModel, Kernel};
use super::GuideAlgorithm;

/// Circular data buffer capacity (dossier §6.8.1;
/// `gaussian_process_guider.cpp:53`).
const CIRCULAR_BUFFER_SIZE: usize = 8192;
/// Regularized-buffer cap (dossier §6.8.1; `:54`).
const REGULAR_BUFFER_SIZE: usize = 2048;
/// FFT zero-pad target (dossier §6.8.1; `:55`).
const FFT_SIZE: usize = 4096;
/// Regularization grid interval, s (dossier §6.8.1; `:56`).
const GRID_INTERVAL: f64 = 5.0;
/// Dither dark-guiding steps (dossier §6.8.6; `:57` `MAX_DITHER_STEPS`).
const MAX_DITHER_STEPS: i32 = 10;
/// Period-length learning rate (dossier §6.8.1; `:59`).
const DEFAULT_LEARNING_RATE: f64 = 0.01;
/// Hybrid-mode hysteresis constant (dossier §6.8.3; `:61`).
const HYSTERESIS: f64 = 0.1;
/// Periods above this (s) are excluded from FFT period identification
/// (`gaussian_process_guider.cpp:598`).
const MAX_PERIOD: f64 = 1500.0;

/// PPEC parameters (dossier §6.8.1 wrapper defaults). Signal variances are the
/// user-facing **amplitudes in px** (squared inside the kernel); length scales
/// and the period are seconds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GpParams {
    /// Reactive (proportional) control gain, `gp_control_gain` (0.6).
    pub control_gain: f64,
    /// Feed-forward prediction gain, `gp_prediction_gain` (0.5).
    pub prediction_gain: f64,
    /// Min-move deadband, px, `gp_min_move` (0.2).
    pub min_move: f64,
    /// SE0 (long-range) length scale, s, `gp_length_scale_se0_kern` (700).
    pub se0_length_scale: f64,
    /// SE0 amplitude, px, `gp_sigvar_se0_kern` (20).
    pub se0_signal_variance: f64,
    /// Periodic length scale, s (natural), `gp_length_scale_per_kern` (10).
    pub periodic_length_scale: f64,
    /// Periodic period length P, s, `gp_period_per_kern` (200).
    pub periodic_period: f64,
    /// Periodic amplitude, px, `gp_sigvar_per_kern` (20).
    pub periodic_signal_variance: f64,
    /// SE1 (short-range) length scale, s, `gp_length_scale_se1_kern` (25).
    pub se1_length_scale: f64,
    /// SE1 amplitude, px, `gp_sigvar_se1_kern` (10).
    pub se1_signal_variance: f64,
    /// Min periods before full GP inference, `gp_period_lengths_inference` (2.0).
    pub min_periods_for_inference: f64,
    /// Min periods before period estimation,
    /// `gp_period_lengths_period_estimation` (2.0).
    pub min_periods_for_period_estimation: f64,
    /// GP subset-approximation point budget, `gp_points_for_approximation` (100).
    pub points_for_approximation: usize,
    /// Auto-adjust the period via FFT, `gp_compute_period` (true).
    pub compute_period: bool,
    /// Retain the trained model after stop, % of period, `noreset_max_pct_period`
    /// (40). Carried for parameter completeness; cross-session model retention
    /// (dossier §6.8.6 `GuidingStarted`) is not wired by the current engine.
    pub retain_max_pct_period: f64,
}

impl Default for GpParams {
    /// The dossier §6.8.1 wrapper defaults
    /// (`guide_algorithm_gaussian_process.cpp` `GetDefault*`).
    fn default() -> Self {
        GpParams {
            control_gain: 0.6,
            prediction_gain: 0.5,
            min_move: 0.2,
            se0_length_scale: 700.0,
            se0_signal_variance: 20.0,
            periodic_length_scale: 10.0,
            periodic_period: 200.0,
            periodic_signal_variance: 20.0,
            se1_length_scale: 25.0,
            se1_signal_variance: 10.0,
            min_periods_for_inference: 2.0,
            min_periods_for_period_estimation: 2.0,
            points_for_approximation: 100,
            compute_period: true,
            retain_max_pct_period: 40.0,
        }
    }
}

impl GpParams {
    /// The seven natural hyperparameters in the upstream `Hyperparameters`
    /// enum order (`gaussian_process_guider.h:52-62`).
    pub(crate) fn natural_hyperparameters(&self) -> [f64; 7] {
        [
            self.se0_length_scale,
            self.se0_signal_variance,
            self.periodic_length_scale,
            self.periodic_signal_variance,
            self.se1_length_scale,
            self.se1_signal_variance,
            self.periodic_period,
        ]
    }
}

/// One circular-buffer entry (`gaussian_process_guider.h:76-82`). Timestamps
/// are seconds since start (midpoint convention); `measurement`/`variance` are
/// this frame's pointing error (px) and its noise (px²); `control` is the
/// correction applied leading into the NEXT measurement.
#[derive(Debug, Clone, Copy, Default)]
struct DataPoint {
    timestamp: f64,
    measurement: f64,
    variance: f64,
    control: f64,
}

/// Gaussian-process "Predictive PEC" controller (dossier §6.8). One instance
/// guides the RA axis; the engine calls [`GuideAlgorithm::result_with`] each
/// accepted frame and [`GuideAlgorithm::deduce_result`] on a lost star.
pub struct GaussianProcessGuider {
    params: GpParams,
    /// Effective (internal, kernel-ready) hyperparameters — mutated by FFT
    /// period learning (`UpdatePeriodLength`).
    kernel: Kernel,
    /// The raw circular buffer, front = oldest, back = newest. Upstream's
    /// `push_front` appends to the logical back (see `circbuf.h`); this
    /// `VecDeque` uses that logical order directly.
    buffer: VecDeque<DataPoint>,
    gp_model: GpModel,
    learning_rate: f64,

    // Synthesized clock (see the module doc).
    wall: f64,
    start_wall: f64,
    last_wall: f64,
    first_since_start: bool,

    prediction: f64,
    /// The end of the last prediction interval; `< 0` signals "no prediction
    /// yet" (reset state).
    last_prediction_end: f64,

    dither_steps: i32,
    dithering_active: bool,
    /// Accumulated dither correction in seconds of gear time (dossier §6.8.6).
    dither_offset: f64,

    /// The last `snr`/`time_step` seen through [`result_with`], reused by the
    /// no-argument trait entry points (`result`, `deduce_result`) so the
    /// frozen [`GuideAlgorithm`] signatures can drive PPEC — see the module
    /// doc and the P4-T1 report's `deduce_result` adjudication.
    last_snr: f64,
    last_time_step: f64,
}

impl GaussianProcessGuider {
    /// Construct with the given parameters, seeding the buffer with a single
    /// zero-control point (dossier §6.8.2 — measurements are relative to the
    /// previous control, so the first needs a zero-control reference;
    /// `gaussian_process_guider.cpp:69-70`).
    pub fn new(params: GpParams) -> Self {
        let kernel = Kernel::from_params(&params);
        let mut buffer = VecDeque::with_capacity(64);
        buffer.push_back(DataPoint::default()); // seed, control = 0
        GaussianProcessGuider {
            params,
            kernel,
            buffer,
            gp_model: GpModel::empty(),
            learning_rate: DEFAULT_LEARNING_RATE,
            wall: 0.0,
            start_wall: 0.0,
            last_wall: 0.0,
            first_since_start: true,
            prediction: 0.0,
            last_prediction_end: -1.0,
            dither_steps: 0,
            dithering_active: false,
            dither_offset: 0.0,
            last_snr: 10.0,
            last_time_step: 1.0,
        }
    }

    // --- buffer accessors (upstream get_last_point / get_second_last_point /
    //     get_number_of_measurements / add_one_point) --------------------------

    fn n_measurements(&self) -> usize {
        self.buffer.len()
    }
    fn last_point(&self) -> &DataPoint {
        self.buffer.back().expect("buffer always holds >= 1 point")
    }
    fn last_point_mut(&mut self) -> &mut DataPoint {
        self.buffer
            .back_mut()
            .expect("buffer always holds >= 1 point")
    }
    /// `get_second_last_point().control`, or 0 when only the seed exists.
    fn second_last_control(&self) -> f64 {
        let n = self.buffer.len();
        if n >= 2 {
            self.buffer[n - 2].control
        } else {
            0.0
        }
    }
    fn add_one_point(&mut self) {
        self.buffer.push_back(DataPoint::default());
        while self.buffer.len() > CIRCULAR_BUFFER_SIZE {
            self.buffer.pop_front();
        }
    }

    // --- synthesized clock (module doc) --------------------------------------

    fn now(&self) -> f64 {
        self.wall - self.start_wall
    }

    /// Anchor the clock at the first measurement frame (upstream
    /// `start_time_ = last_time_ = clock::now()`,
    /// `gaussian_process_guider.cpp:297-301`): `now` becomes 0, the next
    /// `set_timestamp` produces a ~0 timestamp with delta 0.
    fn start_clock(&mut self) {
        self.start_wall = self.wall;
        self.last_wall = self.wall;
        self.first_since_start = true;
    }

    /// Set the newest point's timestamp (upstream `SetTimestamp`,
    /// `gaussian_process_guider.cpp:87-95`): midpoint convention `now − delta/2
    /// + dither_offset`, where the wall clock advances by `dt` per frame except
    /// the first frame after [`start_clock`](Self::start_clock).
    fn set_timestamp(&mut self, dt: f64) {
        if self.first_since_start {
            self.first_since_start = false;
        } else {
            self.wall += dt;
        }
        let delta = self.wall - self.last_wall;
        self.last_wall = self.wall;
        let ts = (self.wall - self.start_wall) - delta / 2.0 + self.dither_offset;
        self.last_point_mut().timestamp = ts;
    }

    // --- measurement handling ------------------------------------------------

    /// Store a real measurement (`HandleGuiding`,
    /// `gaussian_process_guider.cpp:98-107`).
    fn handle_guiding(&mut self, input: f64, snr: f64, dt: f64) {
        self.set_timestamp(dt);
        let var = gp_math::variance_from_snr(snr);
        {
            let lp = self.last_point_mut();
            lp.measurement = input;
            lp.variance = var;
        }
        // Don't predict for what we've measured (dossier §6.8.4 Prediction).
        self.last_prediction_end = self.last_point().timestamp;
    }

    /// Store a blind "dark" measurement with high variance (`HandleDarkGuiding`,
    /// `gaussian_process_guider.cpp:109-114`).
    fn handle_dark_guiding(&mut self, dt: f64) {
        self.set_timestamp(dt);
        let lp = self.last_point_mut();
        lp.measurement = 0.0;
        lp.variance = 1e4;
    }

    /// The period length in play (natural units) — `GetGPHyperparameters()
    /// [PKPeriodLength]`.
    fn period_length(&self) -> f64 {
        self.kernel.period
    }

    // --- GP update / prediction ---------------------------------------------

    /// Rebuild the GP from the completed (N−1) points (dossier §6.8.4;
    /// `UpdateGP`, `gaussian_process_guider.cpp:131-234`): reconstruct the
    /// gear error `Σ control + measurement`, regularize onto the 5 s grid,
    /// (conditionally) re-estimate the period via FFT, then run subset-of-data
    /// inference anchored at `prediction_point`.
    fn update_gp(&mut self, prediction_point: f64) -> Result<(), gp_math::GpError> {
        let n = self.n_measurements();
        let mut timestamps = Vec::with_capacity(n - 1);
        let mut variances = Vec::with_capacity(n - 1);
        let mut gear_error = Vec::with_capacity(n - 1);
        let mut sum_control = 0.0;
        for i in 0..n - 1 {
            let p = self.buffer[i];
            sum_control += p.control;
            timestamps.push(p.timestamp);
            variances.push(p.variance);
            gear_error.push(sum_control + p.measurement);
        }

        let (reg_ts, reg_ge, reg_var) = gp_math::regularize(
            &timestamps,
            &gear_error,
            &variances,
            GRID_INTERVAL,
            REGULAR_BUFFER_SIZE,
            self.dithering_active,
        )?;

        // Period estimation (only when enabled and enough gear time has
        // elapsed). The ridge de-trend feeds ONLY the FFT, so it is computed
        // here rather than unconditionally as upstream does
        // (`gaussian_process_guider.cpp:184-197`) — a pure function whose
        // output is consumed only inside this branch, so the placement is
        // unobservable.
        let period_length = self.period_length();
        if self.params.compute_period
            && self.last_point().timestamp
                > self.params.min_periods_for_period_estimation * period_length
        {
            let detrended = gp_math::detrend(&reg_ts, &reg_ge);
            let p_measured =
                gp_math::estimate_period_length(&reg_ts, &detrended, FFT_SIZE, MAX_PERIOD);
            self.update_period_length(p_measured);
        }

        // Subset-of-data inference on the (un-detrended) regularized series.
        self.gp_model.infer_sd(
            &reg_ts,
            &reg_ge,
            self.params.points_for_approximation,
            &reg_var,
            prediction_point,
            &self.kernel,
        );
        Ok(())
    }

    /// Filter + apply a freshly measured period length (`UpdatePeriodLength`,
    /// `gaussian_process_guider.cpp:666-683`): a NaN keeps the old value, then
    /// a slow learning-rate blend, applied through the natural↔internal
    /// round-trip so the periodic length scale is re-derived for the new P.
    fn update_period_length(&mut self, period_length: f64) {
        let mut h = self.kernel.to_natural();
        let p = if period_length.is_nan() {
            h[6]
        } else {
            period_length
        };
        h[6] = (1.0 - self.learning_rate) * h[6] + self.learning_rate * p;
        self.kernel = Kernel::from_natural(h);
    }

    /// Posterior gear-error increment over `[last_prediction_end,
    /// prediction_location + dither_offset]` (dossier §6.8.4 Prediction;
    /// `PredictGearError`, `gaussian_process_guider.cpp:236-258`).
    fn predict_gear_error(&mut self, prediction_location: f64) -> f64 {
        if self.last_prediction_end < 0.0 {
            self.last_prediction_end = self.now();
        }
        let x0 = self.last_prediction_end;
        let x1 = prediction_location + self.dither_offset;
        let pred = self.gp_model.predict_projected(&[x0, x1]);
        let (p0, p1) = (pred[0], pred[1]);
        self.last_prediction_end = x1;
        p1 - p0
    }

    // --- control loop --------------------------------------------------------

    /// The per-frame control law (dossier §6.8.3; upstream `result`,
    /// `gaussian_process_guider.cpp:260-370`). `dt` is `time_step` (exposure s).
    fn result_impl(&mut self, input: f64, snr: f64, dt: f64) -> f64 {
        // Dithering: trust the control, distrust the measurement — dark-guide
        // the model but output proportional-only.
        if self.dithering_active {
            self.dither_steps -= 1;
            if self.dither_steps <= 0 {
                self.dithering_active = false;
            }
            if self.deduce_result_impl(dt).is_err() {
                self.reset_impl();
            }
            return self.params.control_gain * input;
        }

        if self.n_measurements() == 1 {
            self.start_clock();
        }
        self.handle_guiding(input, snr, dt);

        // Hybrid hysteresis fallback (constant 0.1) on the control history.
        let last_control = self.second_last_control();
        let mut hysteresis_control =
            ((1.0 - HYSTERESIS) * input + HYSTERESIS * last_control) * self.params.control_gain;

        // Reactive (proportional) term with the min-move deadband. The
        // deadband zeroes ONLY the reactive+hysteresis terms — the GP
        // prediction below is always applied (dossier §6.8.3 / §6.7 table).
        let mut control = self.params.control_gain * input;
        if input.abs() < self.params.min_move {
            control = 0.0;
            hysteresis_control = 0.0;
        }

        if self.n_measurements() > 10 {
            let now = self.now();
            // Max accuracy between now and the next frame. `update_gp` only
            // errors on the dithering path (regularizer overrun); off it the
            // guard is unobservable defense — it cannot fail here.
            if self.update_gp(now + 0.5 * dt).is_ok() {
                self.prediction = self.predict_gear_error(now + dt);
                control += self.params.prediction_gain * self.prediction;

                // Smooth blend hysteresis → GP over the first N periods.
                let p = self.period_length();
                let t = self.last_point().timestamp;
                if t < self.params.min_periods_for_inference * p {
                    let pct = (t / (self.params.min_periods_for_inference * p)).min(1.0);
                    control = pct * control + (1.0 - pct) * hysteresis_control;
                }
            }
        }

        // User safeguard: a NaN control falls back to hysteresis.
        if control.is_nan() {
            control = hysteresis_control;
        }

        self.add_one_point(); // control applies to the next interval
        self.last_point_mut().control = control;
        control
    }

    /// Dead reckoning without a measurement (dossier §6.8.3; upstream
    /// `deduceResult`, `gaussian_process_guider.cpp:372-406`): store a dark
    /// point, output 0 unless a trained model is engaged past
    /// `min_periods_for_inference·P`, in which case output the RAW predicted
    /// gear-error increment (prediction gain NOT applied). Errors only on the
    /// dithering-path regularizer overrun.
    fn deduce_result_impl(&mut self, dt: f64) -> Result<f64, gp_math::GpError> {
        self.handle_dark_guiding(dt);

        let mut control = 0.0;
        if self.n_measurements() > 10
            && self.last_point().timestamp
                > self.params.min_periods_for_inference * self.period_length()
        {
            let now = self.now();
            self.update_gp(now + 0.5 * dt)?;
            self.prediction = self.predict_gear_error(now + dt);
            control += self.prediction;
        }

        self.add_one_point();
        // Upstream stores the (possibly-NaN) control before the safeguard,
        // which only rewrites the RETURN value.
        self.last_point_mut().control = control;
        if control.is_nan() {
            control = 0.0;
        }
        Ok(control)
    }

    fn reset_impl(&mut self) {
        self.buffer.clear();
        self.gp_model = GpModel::empty();
        self.buffer.push_back(DataPoint::default()); // re-seed, control 0
        self.last_prediction_end = -1.0;
        self.wall = 0.0;
        self.start_wall = 0.0;
        self.last_wall = 0.0;
        self.first_since_start = true;
        self.dither_offset = 0.0;
        self.dither_steps = 0;
        self.dithering_active = false;
        // The learned period (`self.kernel`) and `self.prediction` are
        // intentionally retained — upstream `reset()` clears only the buffer,
        // GP data, clock and dither state (`gaussian_process_guider.cpp:408-425`).
    }

    // --- dither API (not yet wired by the engine; see module doc) ------------

    /// Notify the guider of a dither (dossier §6.8.6 `GuidingDithered`):
    /// accumulate the gear-time offset and enter the 10-step dark-guiding
    /// window. `rate` is px per second of worm motion at 1× sidereal.
    pub fn guiding_dithered(&mut self, amt: f64, rate: f64) {
        self.dither_offset += amt / rate;
        self.dithering_active = true;
        self.dither_steps = MAX_DITHER_STEPS;
    }

    /// Notify the guider that a dither settle finished (dossier §6.8.6
    /// `GuidingDitherSettleDone`): on success, force exactly one more dithering
    /// pass through `result` (the time-difference correction).
    pub fn guiding_dither_settle_done(&mut self, success: bool) {
        if success {
            self.dither_steps = 1;
        }
    }
}

impl GuideAlgorithm for GaussianProcessGuider {
    /// The no-SNR/dt trait entry: reuses the last `snr`/`time_step` seen
    /// through [`result_with`](GuideAlgorithm::result_with) (the engine always
    /// calls `result_with`; this exists only for direct/legacy callers).
    fn result(&mut self, input: f64) -> f64 {
        self.result_impl(input, self.last_snr, self.last_time_step)
    }

    /// PPEC's real per-frame entry (dossier §6.8.3): `snr` sets the
    /// measurement variance, `dt` (exposure s) drives the clock and prediction
    /// horizon.
    fn result_with(&mut self, input: f64, snr: f64, dt: f64) -> f64 {
        self.last_snr = snr;
        self.last_time_step = dt;
        self.result_impl(input, snr, dt)
    }

    fn reset(&mut self) {
        self.reset_impl();
    }

    /// A2 (P4-T1 rulings A+C; upstream GuidingDithered
    /// gaussian_process_guider.cpp:427-434, dossier §6.8.6): compensate the
    /// dither's gear-time gap and KEEP the trained model. Returns true so the
    /// engine skips `reset()` on the RA axis.
    fn dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool {
        self.guiding_dithered(ra_amt_px, ra_rate);
        true
    }

    /// Dead reckoning (dossier §3.3/§6.8.3): uses the last exposure seen
    /// through [`result_with`](GuideAlgorithm::result_with) as the frozen trait
    /// signature carries no `dt`. A regularizer overrun (dithering path only)
    /// resets the model and yields 0.
    fn deduce_result(&mut self) -> f64 {
        match self.deduce_result_impl(self.last_time_step) {
            Ok(v) => v,
            Err(_) => {
                self.reset_impl();
                0.0
            }
        }
    }

    fn min_move(&self) -> f64 {
        self.params.min_move
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seed_point_present_and_warmup_proportional() {
        let mut gp = GaussianProcessGuider::new(GpParams::default());
        assert_eq!(gp.n_measurements(), 1, "seed point present");
        let out = gp.result_with(1.0, 10.0, 2.0);
        assert!((out - 0.6).abs() < 1e-12);
        assert_eq!(gp.n_measurements(), 2, "one point added per frame");
    }

    #[test]
    fn first_frame_timestamp_is_zero() {
        let mut gp = GaussianProcessGuider::new(GpParams::default());
        gp.result_with(1.0, 10.0, 2.0);
        // The just-measured point is now the second-last; its timestamp ~0.
        let ts0 = gp.buffer[0].timestamp;
        assert!(
            ts0.abs() < 1e-12,
            "first measurement timestamp ~0, got {ts0}"
        );
        gp.result_with(1.0, 10.0, 2.0);
        // Second measurement: midpoint => dt/2 = 1.0.
        let ts1 = gp.buffer[1].timestamp;
        assert!(
            (ts1 - 1.0).abs() < 1e-12,
            "second timestamp dt/2=1.0, got {ts1}"
        );
    }

    #[test]
    fn control_stored_on_the_next_point() {
        // The control computed at a frame is stored on the freshly added point
        // (upstream add_one_point then HandleControls).
        let mut gp = GaussianProcessGuider::new(GpParams::default());
        let c = gp.result_with(1.0, 10.0, 2.0);
        assert!((gp.last_point().control - c).abs() < 1e-15);
    }

    /// The hysteresis→GP blend weight (dossier §6.8.3; `gaussian_process_guider
    /// .cpp:337-345`) is `pct = min(t / (min_periods_for_inference · P), 1)`,
    /// applied as `control = pct·control + (1 − pct)·hysteresis_control`. This
    /// pins that formula at an INTERIOR weight (0 < pct < 1) — the P4-T1 review
    /// M8 noted it was previously verified only by inspection, with no fixture.
    ///
    /// Recipe: `compute_period = false` freezes `P` at the configured value so
    /// the blend denominator `min_periods·P` is exactly known; a short `P` (60 s)
    /// keeps the observed frame inside the window `t < min_periods·P`. The
    /// pre-blend `control` and the `hysteresis_control` are reconstructed from
    /// the guider's own post-call state (`kernel.period`, the measured point's
    /// timestamp, the stored `prediction`, and the second-last control the
    /// hysteresis term reads), so the assertion pins the WEIGHTING, not a magic
    /// number. `input = 1.0` is above `min_move` (0.2) so neither the reactive
    /// term nor the hysteresis term is deadbanded to zero.
    #[test]
    fn blend_weight_is_t_over_min_periods_times_period() {
        // compute_period=false freezes P; a short P keeps the frame interior;
        // min_periods_for_inference stays at its default 2.0.
        let params = GpParams {
            compute_period: false,
            periodic_period: 60.0,
            ..GpParams::default()
        };
        let dt = 5.0;
        let u = 1.0; // > min_move ⇒ reactive + hysteresis terms both live
        let snr = 20.0;
        let mut gp = GaussianProcessGuider::new(params);
        // Frames 1..=11: the GP engages at the 11th (n_measurements > 10).
        for _ in 0..11 {
            gp.result_with(u, snr, dt);
        }
        // The 12th frame is a blend frame; capture the hysteresis input first.
        let last_control_before = gp.second_last_control();
        let out = gp.result_with(u, snr, dt);

        // Reconstruct the blend from the guider's own post-call state.
        let n = gp.buffer.len();
        let t = gp.buffer[n - 2].timestamp; // this frame's measured point
        let period = gp.kernel.period; // frozen == 60.0
        let min_p = gp.params.min_periods_for_inference; // 2.0
        assert!(
            t < min_p * period,
            "frame must be inside the blend window: t={t}, min_periods·P={}",
            min_p * period
        );
        let pct = (t / (min_p * period)).min(1.0);
        assert!(
            pct > 0.0 && pct < 1.0,
            "pct={pct} must be strictly interior"
        );
        // Pre-blend control = control_gain·input + prediction_gain·prediction
        // (both terms above the deadband); reconstructed from the stored
        // prediction so the test is exact regardless of the GP's numeric output.
        assert!(gp.prediction.is_finite(), "prediction must be finite here");
        let control_raw = params.control_gain * u + params.prediction_gain * gp.prediction;
        let hyst =
            ((1.0 - HYSTERESIS) * u + HYSTERESIS * last_control_before) * params.control_gain;
        let expected = pct * control_raw + (1.0 - pct) * hyst;
        assert!(
            (out - expected).abs() < 1e-12,
            "blend at pct={pct}: out={out}, expected {expected} \
             (control_raw={control_raw}, hyst={hyst}, t={t}, P={period})"
        );
    }

    /// A2 (P4-T1 rulings A+C / M6; upstream GuidingDithered
    /// gaussian_process_guider.cpp:427-434, dossier §6.8.6): dither_notify
    /// applies the gear-time compensation (dither_offset += amt/rate) and
    /// PRESERVES the trained buffer — the whole point of A2 (no reset). It
    /// returns true so the engine skips ra_algo.reset().
    #[test]
    fn dither_notify_shifts_gear_time_and_preserves_buffer() {
        let mut gp = GaussianProcessGuider::new(GpParams::default());
        for _ in 0..15 {
            gp.result_with(1.0, 20.0, 5.0);
        }
        let n_before = gp.buffer.len();
        assert!(n_before > 10, "buffer trained: {n_before}");
        let off_before = gp.dither_offset;

        let handled = gp.dither_notify(3.0, 2.0); // amt=3px, rate=2px/s
        assert!(handled, "PPEC handles the dither (engine must skip reset)");
        assert!(
            (gp.dither_offset - (off_before + 3.0 / 2.0)).abs() < 1e-12,
            "gear time shifted by amt/rate"
        );
        assert!(gp.dithering_active, "dark-guiding window opened");
        assert_eq!(gp.dither_steps, MAX_DITHER_STEPS);
        assert_eq!(
            gp.buffer.len(),
            n_before,
            "dither_notify must NOT reset the trained buffer"
        );
    }

    /// A2: a reactive algorithm keeps the defaulted dither_notify (false, no
    /// state change) so the engine resets it on dither as before.
    #[test]
    fn default_dither_notify_is_false_for_reactive_algo() {
        use crate::algorithms::Hysteresis;
        let mut h = Hysteresis::default();
        assert!(!h.dither_notify(3.0, 2.0));
    }
}
