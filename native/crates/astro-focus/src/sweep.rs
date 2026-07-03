// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§3 StartAutoFocus, §3.1
// sweep + extension loop, §7 validation, §12 edge cases). No code copied
// from NINA.

//! The autofocus sweep as a pure policy state machine (dossier §3/§3.1/§7).
//!
//! The host drives it: call [`FocusSweep::next`] to get the next [`Step`];
//! on [`Step::MoveTo`] move the focuser (applying backlash — see
//! [`crate::backlash`]), expose, measure, then call
//! [`FocusSweep::add_measurement`] with the result and call `next` again.
//! [`Step::Done`] carries the [`FitOutcome`]; [`Step::Failed`] carries a
//! [`FailReason`] after the focuser has been told to restore to the start.
//!
//! All positions are focuser encoder steps. This machine emits **absolute**
//! target positions: NINA's relative "reposition then step" moves are folded
//! into the equivalent absolute measurement position (the intermediate stops
//! only served backlash direction, which the backlash layer now handles).
//! Pipelined move-while-analyzing timing is a host concern; the machine
//! preserves the measurement positions and their order exactly.

use crate::config::{AfMethod, FocusConfig};
use crate::fit::{
    build_outcome, compute_all_fits, determine_final_focus_point, passes_r_squared_gate,
    within_bounds, FitOutcome, FocusFits,
};
use crate::point::{insert_sorted, FocusPoint};
use crate::trendline::{self, TrendlineFit};

/// The next action the host must perform, returned by [`FocusSweep::next`].
#[derive(Debug, Clone)]
pub enum Step {
    /// Move the focuser to this absolute position, expose, measure, then call
    /// [`FocusSweep::add_measurement`] and [`FocusSweep::next`] again.
    MoveTo(i32),
    /// The run succeeded; the focuser is at the validated focus position.
    Done(FitOutcome),
    /// The run failed. A restore [`Step::MoveTo`] to the start was emitted
    /// immediately before this; the run is terminal.
    Failed(FailReason),
}

/// Why an autofocus run failed (dossier §7/§12).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailReason {
    /// No trend points on either side of the minimum after the initial pass.
    NotEnoughSpread,
    /// A fit's R^2 was below the configured threshold (§7.1).
    RSquaredBelowThreshold,
    /// The selected focus position fell outside the measured range (§7.2).
    OutOfBounds,
    /// Re-measured HFR was worse than the start by more than 1.15x (§7.3).
    HfrWorseThanStart,
    /// The required fit could not be produced (too few usable points).
    FitUnavailable,
}

/// A buffered per-point measurement from the host.
#[derive(Debug, Clone, Copy)]
struct Meas {
    position: f64,
    value: f64,
    stdev: f64,
    star_count: u32,
}

/// What the most recently emitted `MoveTo` expects back.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Pending {
    None,
    Baseline,
    Point,
    Validation,
    Restore,
}

/// High-level stage of the run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stage {
    Init,
    InitialSweep,
    Extending,
    Validate,
    AwaitValidation,
    RestoreFail,
    EmitFail,
    DoneOk,
    Terminal,
}

/// The autofocus sweep policy state machine.
#[derive(Debug, Clone)]
pub struct FocusSweep {
    cfg: FocusConfig,
    start: i32,
    reverse: bool,
    attempts: u32,
    baseline_done: bool,
    initial_hfr: f64,

    points: Vec<FocusPoint>,
    trend: Option<TrendlineFit>,

    stage: Stage,
    pending: Pending,
    last_measurement: Option<Meas>,

    sweep_positions: Vec<i32>,
    sweep_idx: usize,

    final_point: Option<(f64, f64)>,
    final_fits: Option<FocusFits>,
    final_move: i32,
    fail_reason: Option<FailReason>,
    outcome: Option<FitOutcome>,
}

impl FocusSweep {
    /// Create a sweep for `cfg` starting at focuser position `start_position`.
    pub fn new(cfg: FocusConfig, start_position: i32) -> Self {
        let reverse = cfg.reverse_sweep();
        FocusSweep {
            cfg,
            start: start_position,
            reverse,
            attempts: 1,
            baseline_done: false,
            initial_hfr: 0.0,
            points: Vec::new(),
            trend: None,
            stage: Stage::Init,
            pending: Pending::None,
            last_measurement: None,
            sweep_positions: Vec::new(),
            sweep_idx: 0,
            final_point: None,
            final_fits: None,
            final_move: start_position,
            fail_reason: None,
            outcome: None,
        }
    }

    /// The attempt number currently in progress (1-based).
    pub fn attempt(&self) -> u32 {
        self.attempts
    }

    /// The focus points collected so far (position-sorted).
    pub fn points(&self) -> &[FocusPoint] {
        &self.points
    }

    /// Supply the measurement for the position of the most recent
    /// [`Step::MoveTo`]. `hfr` is the (already frame-averaged) measure,
    /// `stdev` its RMS error, and `star_count` the detected star count
    /// (`0` triggers the no-star sentinel). Buffered until the next
    /// [`FocusSweep::next`].
    pub fn add_measurement(&mut self, position: i32, hfr: f64, stdev: f64, star_count: u32) {
        self.last_measurement = Some(Meas {
            position: position as f64,
            value: hfr,
            stdev,
            star_count,
        });
    }

    /// Advance the state machine and return the next [`Step`]. Processes any
    /// buffered measurement first, then decides the next action.
    ///
    /// Named `next` per the sweep protocol; it is not an [`Iterator`].
    #[allow(clippy::should_implement_trait)]
    pub fn next(&mut self) -> Step {
        if let Some(m) = self.last_measurement.take() {
            match self.pending {
                Pending::Baseline => {
                    self.initial_hfr = if m.star_count == 0 { 0.0 } else { m.value };
                    self.baseline_done = true;
                }
                Pending::Point => self.record_point(m),
                Pending::Validation => self.consume_validation(m),
                Pending::Restore | Pending::None => {}
            }
        }
        self.pending = Pending::None;
        self.decide()
    }

    fn record_point(&mut self, m: Meas) {
        let value = if m.star_count == 0 { 0.0 } else { m.value };
        let fp = FocusPoint::new(m.position, value, m.stdev);
        insert_sorted(&mut self.points, fp);
        self.trend = match self.cfg.method {
            AfMethod::StarHfr => trendline::fit_star_hfr(&self.points),
            AfMethod::ContrastDetection => trendline::fit_contrast(&self.points),
        };
    }

    fn setup_initial_sweep(&mut self) {
        let offset = self.cfg.offset_steps;
        let step = self.cfg.step_size;
        self.sweep_positions = (0..=offset)
            .map(|i| {
                let d = (offset - i) * step;
                if self.reverse {
                    self.start - d
                } else {
                    self.start + d
                }
            })
            .collect();
        self.sweep_idx = 0;
    }

    fn decide(&mut self) -> Step {
        match self.stage {
            Stage::Init => {
                if self.cfg.baseline_needed() && !self.baseline_done {
                    self.pending = Pending::Baseline;
                    return Step::MoveTo(self.start);
                }
                self.setup_initial_sweep();
                self.stage = Stage::InitialSweep;
                self.decide()
            }
            Stage::InitialSweep => {
                if self.sweep_idx < self.sweep_positions.len() {
                    let pos = self.sweep_positions[self.sweep_idx];
                    self.sweep_idx += 1;
                    self.pending = Pending::Point;
                    Step::MoveTo(pos)
                } else {
                    self.stage = Stage::Extending;
                    self.decide()
                }
            }
            Stage::Extending => self.decide_extension(),
            Stage::Validate => self.decide_validate(),
            Stage::AwaitValidation => {
                // Reached only if the host calls next() without add_measurement
                // after a validation move; re-emit the validation move.
                self.pending = Pending::Validation;
                Step::MoveTo(self.final_move)
            }
            Stage::RestoreFail => {
                self.stage = Stage::EmitFail;
                self.pending = Pending::Restore;
                Step::MoveTo(self.start)
            }
            Stage::EmitFail => {
                self.stage = Stage::Terminal;
                Step::Failed(self.fail_reason.unwrap_or(FailReason::FitUnavailable))
            }
            Stage::DoneOk => {
                self.stage = Stage::Terminal;
                Step::Done(self.outcome.take().expect("outcome present at DoneOk"))
            }
            Stage::Terminal => Step::Failed(self.fail_reason.unwrap_or(FailReason::FitUnavailable)),
        }
    }

    fn decide_extension(&mut self) -> Step {
        let trend = match self.trend.clone() {
            Some(t) => t,
            None => {
                self.fail_reason = Some(FailReason::NotEnoughSpread);
                self.stage = Stage::RestoreFail;
                return self.decide();
            }
        };
        let left = trend.left.points.len() as i32;
        let right = trend.right.points.len() as i32;

        // No spread on either side -> hard fail, no reattempt (dossier §3.1).
        if left == 0 && right == 0 {
            self.fail_reason = Some(FailReason::NotEnoughSpread);
            self.stage = Stage::RestoreFail;
            return self.decide();
        }

        let pivot_x = trend.pivot.0;
        let zeros_left = self
            .points
            .iter()
            .filter(|p| p.position < pivot_x && p.value == 0.0)
            .count() as i32;
        let zeros_right = self
            .points
            .iter()
            .filter(|p| p.position > pivot_x && p.value == 0.0)
            .count() as i32;
        let offset = self.cfg.offset_steps;

        // Both sides satisfied (trend + zero points) -> proceed to fitting.
        if right + zeros_right >= offset && left + zeros_left >= offset {
            self.stage = Stage::Validate;
            return self.decide();
        }

        // Runaway guard: exceeded max points -> stop collecting, still fit.
        if self.points.len() as u32 > self.cfg.max_points() {
            self.stage = Stage::Validate;
            return self.decide();
        }

        let step = self.cfg.step_size;
        let target = if left < offset && zeros_left < offset {
            self.points.first().unwrap().position.round_ties_even() as i32 - step
        } else if right < offset && zeros_right < offset {
            self.points.last().unwrap().position.round_ties_even() as i32 + step
        } else {
            self.stage = Stage::Validate;
            return self.decide();
        };

        // Focuser would reach/pass position 0 -> stop collecting, still fit.
        if target <= 0 {
            self.stage = Stage::Validate;
            return self.decide();
        }

        self.pending = Pending::Point;
        Step::MoveTo(target)
    }

    fn decide_validate(&mut self) -> Step {
        let fits = compute_all_fits(&self.points, self.cfg.method);
        let final_pt = determine_final_focus_point(self.cfg.method, self.cfg.curve_fitting, &fits);
        let (fx, fy) = match final_pt {
            Some(p) => p,
            None => {
                self.fail_or_reattempt(FailReason::FitUnavailable);
                return self.decide();
            }
        };

        if !passes_r_squared_gate(
            self.cfg.method,
            self.cfg.curve_fitting,
            &fits,
            self.cfg.r_squared_threshold,
        ) {
            self.fail_or_reattempt(FailReason::RSquaredBelowThreshold);
            return self.decide();
        }
        if !within_bounds(&self.points, fx) {
            self.fail_or_reattempt(FailReason::OutOfBounds);
            return self.decide();
        }

        // Passed the pre-move gates: move to the final point (truncated toward
        // zero, dossier §7.3) and re-measure to validate.
        self.final_point = Some((fx, fy));
        self.final_fits = Some(fits);
        self.final_move = fx.trunc() as i32;
        self.stage = Stage::AwaitValidation;
        self.pending = Pending::Validation;
        Step::MoveTo(self.final_move)
    }

    fn consume_validation(&mut self, m: Meas) {
        let new_hfr = if m.star_count == 0 { 0.0 } else { m.value };
        let baseline_mode =
            self.cfg.method == AfMethod::StarHfr && self.cfg.r_squared_threshold <= 0.0;
        let ok = if baseline_mode {
            !(self.initial_hfr != 0.0 && new_hfr > self.initial_hfr * 1.15)
        } else {
            true
        };

        if ok {
            let (_, fy) = self.final_point.expect("final point present");
            let fits = self.final_fits.take().expect("fits present");
            let outcome = build_outcome(
                self.cfg.method,
                self.cfg.curve_fitting,
                &fits,
                self.final_move,
                fy,
                &self.points,
            );
            self.outcome = Some(outcome);
            self.stage = Stage::DoneOk;
        } else {
            self.fail_or_reattempt(FailReason::HfrWorseThanStart);
        }
    }

    /// Reattempt from the start (points cleared, baseline retained) while
    /// attempts remain, else restore to start and fail (dossier §3/§7).
    fn fail_or_reattempt(&mut self, reason: FailReason) {
        if self.attempts < self.cfg.total_number_of_attempts {
            self.attempts += 1;
            self.points.clear();
            self.trend = None;
            self.sweep_positions.clear();
            self.sweep_idx = 0;
            self.final_point = None;
            self.final_fits = None;
            self.stage = Stage::Init;
        } else {
            self.fail_reason = Some(reason);
            self.stage = Stage::RestoreFail;
        }
    }
}
