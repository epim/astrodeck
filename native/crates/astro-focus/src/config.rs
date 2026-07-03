// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§2 configuration knobs,
// §1 enums). No code copied from NINA.

//! Autofocus configuration knobs and enumerations.
//!
//! All positions are focuser encoder **steps** (integers stored as `i32`);
//! HFR "values" are in pixels. Defaults mirror NINA's
//! `FocuserSettings.SetDefaultValues()` (dossier §2).

/// Measurement type for a focus sweep (dossier §1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AfMethod {
    /// Minimize average star HFR (a valley curve).
    StarHfr,
    /// Maximize a contrast metric (a peak curve, fit with a Gaussian).
    ContrastDetection,
}

/// The curve-fitting strategy that selects the final focus point (dossier §6.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CurveFitting {
    /// Left/right robust trendline intersection.
    Trendlines,
    /// Quadratic (parabola) minimum.
    Parabolic,
    /// Mean of trendline intersection and quadratic minimum.
    TrendParabolic,
    /// Hyperbola minimum (NINA's default).
    Hyperbolic,
    /// Mean of trendline intersection and hyperbola minimum (recommended).
    TrendHyperbolic,
}

/// Backlash compensation model (dossier §8).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BacklashModel {
    /// Persistent offset hides compensation; compensates only on reversal.
    Absolute,
    /// Overshoot past target then approach; final approach direction constant.
    Overshoot,
}

/// Full sweep + fit configuration.
///
/// Field defaults follow NINA (dossier §2). `max_step` is the focuser's
/// maximum encoder position, used to clamp moves and by the backlash layer;
/// pass `i32::MAX` when unbounded.
#[derive(Debug, Clone, Copy)]
pub struct FocusConfig {
    /// Focuser steps per sweep step (`AutoFocusStepSize`, default 50).
    pub step_size: i32,
    /// Half-width of the sweep in steps-of-stepsize
    /// (`AutoFocusInitialOffsetSteps`, clamped 1..=10, default 4).
    pub offset_steps: i32,
    /// Exposures averaged per point (`AutoFocusNumberOfFramesPerPoint`, >=1).
    pub frames_per_point: u32,
    /// Measurement type.
    pub method: AfMethod,
    /// Fitting strategy used for STARHFR (ignored for contrast).
    pub curve_fitting: CurveFitting,
    /// R^2 gate threshold (`RSquaredThreshold`, clamped 0..=1, default 0.7);
    /// `0` disables the gate and switches on the 1.15x baseline-HFR fallback.
    pub r_squared_threshold: f64,
    /// Backlash model (default OVERSHOOT).
    pub backlash_model: BacklashModel,
    /// Backlash steps for IN (decreasing) moves.
    pub backlash_in: i32,
    /// Backlash steps for OUT (increasing) moves.
    pub backlash_out: i32,
    /// Full-run reattempts on bad AF (`AutoFocusTotalNumberOfAttempts`, 1..=5).
    pub total_number_of_attempts: u32,
    /// Focuser maximum encoder position (clamp bound).
    pub max_step: i32,
}

impl Default for FocusConfig {
    fn default() -> Self {
        FocusConfig {
            step_size: 50,
            offset_steps: 4,
            frames_per_point: 1,
            method: AfMethod::StarHfr,
            curve_fitting: CurveFitting::Hyperbolic,
            r_squared_threshold: 0.7,
            backlash_model: BacklashModel::Overshoot,
            backlash_in: 0,
            backlash_out: 0,
            total_number_of_attempts: 1,
            max_step: i32::MAX,
        }
    }
}

impl FocusConfig {
    /// Absolute cap on collected sweep points:
    /// `frames_per_point * offset_steps * 10` (dossier §2, AutoFocusVM.cs:144).
    pub fn max_points(&self) -> u32 {
        self.frames_per_point * (self.offset_steps.max(0) as u32) * 10
    }

    /// Whether a baseline-HFR measurement is taken before the sweep: only for
    /// STARHFR with the R^2 gate disabled (dossier §3 step 4).
    pub fn baseline_needed(&self) -> bool {
        self.method == AfMethod::StarHfr && self.r_squared_threshold <= 0.0
    }

    /// Reverse-sweep rule (dossier §3/§8.3): OVERSHOOT model with only IN
    /// backlash configured runs the initial pass IN-first, stepping OUT.
    pub fn reverse_sweep(&self) -> bool {
        self.backlash_model == BacklashModel::Overshoot
            && self.backlash_in > 0
            && self.backlash_out == 0
    }
}
