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

mod hysteresis;
mod resist_switch;

pub use hysteresis::Hysteresis;
pub use resist_switch::ResistSwitch;

/// Common interface every guide algorithm implements (dossier §6 intro).
/// RA and Dec each hold one instance; the guide engine calls
/// [`result`](Self::result) (or [`result_with`](Self::result_with)) once per
/// accepted frame and [`reset`](Self::reset) on guiding stopped, guiding
/// resumed after a full pause, dither, or guiding re-enabled.
pub trait GuideAlgorithm {
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
