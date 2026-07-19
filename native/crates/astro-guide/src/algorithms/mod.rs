// SPDX-License-Identifier: Apache-2.0
//
// Provenance: crate-internal module wiring + the `GuideAlgorithm` trait,
// derived from the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§6 intro, §6.7) — the
// common interface PHD2's `GuideAlgorithm` base class exposes
// (`guide_algorithm.h`, `reset`/`deduce_result`/`GetMinMove`). No code
// copied from PHD2.

//! Per-axis guide algorithms (dossier §6): pluggable correction functions
//! that turn one frame's mount-frame pixel error (px) into a correction
//! distance (px). RA and Dec each own one instance.

mod gaussian_process;
pub mod gp_math;
mod hysteresis;
mod lowpass;
mod resist_switch;
mod zfilter;

pub use gaussian_process::{GaussianProcessGuider, GpParams};
pub use hysteresis::Hysteresis;
pub use lowpass::{Lowpass, Lowpass2};
pub use resist_switch::ResistSwitch;
pub use zfilter::{Design, Filter, FilterError, ZFilter};

/// Common interface every guide algorithm implements (dossier §6 intro).
/// RA and Dec each hold one instance; the guide engine calls
/// [`result`](Self::result) (or [`result_with`](Self::result_with)) once per
/// accepted frame and [`reset`](Self::reset) on guiding stopped, guiding
/// resumed after a full pause, dither, or guiding re-enabled.
///
/// `Send` supertrait: the engine (holding boxed algorithm objects) is driven
/// from Python through a PyO3 layer that releases the GIL around the
/// per-frame work, so every implementation must be transferable across
/// threads — a compiler-enforced invariant here rather than a promise (all
/// implementations are plain data structs, so the bound costs nothing).
pub trait GuideAlgorithm: Send {
    /// This frame's correction (px) for `input` px of measured mount-frame
    /// error.
    fn result(&mut self, input: f64) -> f64;

    /// GP-aware variant: `snr` (this measurement's signal-to-noise ratio)
    /// and `dt` (the frame's exposure time, seconds) let a predictive
    /// algorithm fold in more than the raw offset (dossier §6.8.3's
    /// `result(input, snr, time_step)` — the Gaussian-process predictor is
    /// the only algorithm that overrides this). Every other algorithm
    /// ignores both extra arguments and delegates to
    /// [`result`](Self::result).
    fn result_with(&mut self, input: f64, snr: f64, dt: f64) -> f64 {
        let _ = (snr, dt);
        self.result(input)
    }

    /// Notify a predictive algorithm of a dither so it compensates the
    /// gear-time gap IN PLACE (keeping its trained model) instead of being
    /// reset (A2; P4-T1 rulings A+C). `ra_amt_px` is the RA dither magnitude
    /// (px); `ra_rate` is the calibration-derived RA rate the engine holds.
    /// Returns `true` if the algorithm handled it and the engine must SKIP
    /// its `reset()`. Defaulted `false` (no-op) for every reactive algorithm;
    /// only the Gaussian-process predictor overrides it (upstream
    /// `GuidingDithered`, gaussian_process_guider.cpp:427-434, dossier §6.8.6).
    fn dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool {
        let _ = (ra_amt_px, ra_rate);
        false
    }

    /// Serialize the trained predictive-model window for cross-session
    /// persistence (A5; dossier §6.8.6). Each row is
    /// `(timestamp, measurement, variance, control)`. Non-predictive
    /// algorithms hold no model — default empty; only PPEC overrides.
    fn dump_gp_window(&self) -> Vec<(f64, f64, f64, f64)> {
        Vec::new()
    }

    /// Restore a persisted predictive-model window with `GuidingStarted`
    /// retention (A5; keep the newest `retain_pct`% of one period). Default
    /// no-op; only PPEC overrides.
    fn restore_gp_window(&mut self, points: &[(f64, f64, f64, f64)], retain_pct: f64) {
        let _ = (points, retain_pct);
    }

    /// Clear all history (dossier §6 intro: guiding stopped, guiding
    /// resumed after a full pause, dither, guiding re-enabled).
    fn reset(&mut self);

    /// Non-reactive/predictive contribution independent of a fresh
    /// measurement — `0.0` for every algorithm except the Gaussian-process
    /// predictor (dossier §6 intro; §6.8).
    fn deduce_result(&mut self) -> f64 {
        0.0
    }

    /// The configured min-move deadband (px); algorithms without one
    /// (Identity) return `-1.0` (dossier §6 intro `GetMinMove`) — not
    /// applicable to any algorithm this crate implements yet.
    fn min_move(&self) -> f64;
}
