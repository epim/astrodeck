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
//! The host may also ask what the next step *would* be for a hypothetical
//! measurement: [`FocusSweep::peek_next`] clones the machine, feeds the clone
//! that measurement and returns the `(Step, PendingKind)` the clone would
//! emit, leaving `self` untouched. That is what lets a host expose
//! speculatively straight through the sweep's turn-round instead of guessing
//! with a rule of thumb that only describes the descending half. The
//! [`PendingKind`] rides along on both `peek_next` and
//! [`FocusSweep::pending_kind`] so the host can *recognise* the validation
//! move (and the restore, and the baseline) rather than count points to it.
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

/// What the most recently emitted [`Step::MoveTo`] expects back, reported by
/// [`FocusSweep::pending_kind`] and by [`FocusSweep::peek_next`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PendingKind {
    /// Nothing is outstanding: the machine has not run yet, or the last step
    /// was [`Step::Done`]/[`Step::Failed`].
    None,
    /// The pre-sweep baseline HFR at the start position.
    Baseline,
    /// A sweep or extension point to be added to the curve.
    Point,
    /// The re-measure at the fitted focus that validates the run.
    Validation,
    /// The restore to the start position emitted just before [`Step::Failed`].
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
    pending: PendingKind,
    last_measurement: Option<Meas>,

    sweep_positions: Vec<i32>,
    sweep_idx: usize,

    /// Set once a left-extension move has clamped to and measured position 0
    /// (dossier §3.1/§12: "focuser reached position 0" break). Checked at the
    /// start of the next extension decision so repeated left extensions at
    /// the lower bound cannot loop forever.
    hit_focuser_zero: bool,

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
            pending: PendingKind::None,
            last_measurement: None,
            sweep_positions: Vec::new(),
            sweep_idx: 0,
            hit_focuser_zero: false,
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

    /// What the most recently emitted [`Step::MoveTo`] expects back.
    /// [`PendingKind::None`] before the first [`FocusSweep::next`] and once the
    /// run is terminal.
    pub fn pending_kind(&self) -> PendingKind {
        self.pending
    }

    /// What [`FocusSweep::next`] *would* return if the most recent
    /// [`Step::MoveTo`] position measured `hfr`/`stdev`/`star_count` — without
    /// advancing this machine.
    ///
    /// The hypothetical is run on a clone (`self` is not mutated), so the host
    /// can start the next move and exposure while the current point is still
    /// being measured, and keep doing so across the sweep's turn-round. The
    /// second element is the clone's [`PendingKind`]: what that step would
    /// expect back, which is how a host recognises the validation move rather
    /// than counting points to it. A speculative frame is still only ever
    /// usable for the position the machine actually asks for.
    pub fn peek_next(
        &self,
        position: i32,
        hfr: f64,
        stdev: f64,
        star_count: u32,
    ) -> (Step, PendingKind) {
        let mut probe = self.clone();
        probe.add_measurement(position, hfr, stdev, star_count);
        let step = probe.next();
        (step, probe.pending)
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
                PendingKind::Baseline => {
                    self.initial_hfr = if m.star_count == 0 { 0.0 } else { m.value };
                    self.baseline_done = true;
                }
                PendingKind::Point => self.record_point(m),
                PendingKind::Validation => self.consume_validation(m),
                PendingKind::Restore | PendingKind::None => {}
            }
        }
        self.pending = PendingKind::None;
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
                    self.pending = PendingKind::Baseline;
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
                    self.pending = PendingKind::Point;
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
                self.pending = PendingKind::Validation;
                Step::MoveTo(self.final_move)
            }
            Stage::RestoreFail => {
                self.stage = Stage::EmitFail;
                self.pending = PendingKind::Restore;
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
        // NINA's "focuser reached position 0" break (dossier §3.1/§12): the
        // previous extension move already clamped to and measured position 0.
        // Stop collecting and proceed straight to fitting, even if a quota
        // was not met — checked before anything else so this can't loop.
        if self.hit_focuser_zero {
            self.stage = Stage::Validate;
            return self.decide();
        }
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
            let raw = self.points.first().unwrap().position.round_ties_even() as i32 - step;
            if raw <= 0 {
                // A real focuser clamps to its lower bound; NINA still
                // measures the clamped point and adds it to the curve, only
                // breaking out of the extension loop on the *next* check
                // (dossier §3.1/§12: `focuser.position() == 0` break).
                self.hit_focuser_zero = true;
                0
            } else {
                raw
            }
        } else if right < offset && zeros_right < offset {
            self.points.last().unwrap().position.round_ties_even() as i32 + step
        } else {
            self.stage = Stage::Validate;
            return self.decide();
        };

        self.pending = PendingKind::Point;
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
        self.pending = PendingKind::Validation;
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
            self.hit_focuser_zero = false;
            self.final_point = None;
            self.final_fits = None;
            self.stage = Stage::Init;
        } else {
            self.fail_reason = Some(reason);
            self.stage = Stage::RestoreFail;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{CurveFitting, FocusConfig};

    const STEP: i32 = 350;
    const OFFSET: i32 = 4;

    fn cfg() -> FocusConfig {
        FocusConfig {
            step_size: STEP,
            offset_steps: OFFSET,
            curve_fitting: CurveFitting::Hyperbolic,
            r_squared_threshold: 0.7,
            ..FocusConfig::default()
        }
    }

    /// The V shape the rig actually measures: a hyperbola with a 2.5 px waist
    /// and a 47-steps-per-pixel flank, so `focus +/- offset*step` reaches about
    /// 30 px the way a real nine-point sweep does.
    fn hyperbola(focus: i32) -> impl Fn(i32) -> (f64, f64, u32) {
        move |pos| {
            let d = (pos - focus) as f64 / 47.0;
            ((2.5f64 * 2.5 + d * d).sqrt(), 0.1, 30)
        }
    }

    /// `Step` carries a `FitOutcome` that is not `PartialEq`; the derived
    /// `Debug` is the whole value, so compare that.
    fn label(s: &Step) -> String {
        format!("{s:?}")
    }

    #[test]
    fn peek_does_not_move_the_machine() {
        // Two identical machines, one of which is peeked at every point with
        // both the true measurement and a nonsense one. They must stay in
        // lockstep: same emitted positions, same curve, same terminal step.
        let focus = 12_000;
        let curve = hyperbola(focus);
        let mut peeked = FocusSweep::new(cfg(), focus);
        let mut plain = FocusSweep::new(cfg(), focus);

        let mut a = peeked.next();
        let mut b = plain.next();
        for _ in 0..500 {
            let pos = match (&a, &b) {
                (Step::MoveTo(p), Step::MoveTo(q)) => {
                    assert_eq!(p, q, "the peeked run emitted a different position");
                    *p
                }
                _ => break,
            };
            let (hfr, sd, n) = curve(pos);

            let points_before = peeked.points().to_vec();
            let kind_before = peeked.pending_kind();
            let attempt_before = peeked.attempt();
            let _ = peeked.peek_next(pos, hfr, sd, n);
            let _ = peeked.peek_next(pos, 99.0, 9.0, 0);
            assert_eq!(
                peeked.points(),
                points_before.as_slice(),
                "peek added a point"
            );
            assert_eq!(
                peeked.pending_kind(),
                kind_before,
                "peek changed the pending kind"
            );
            assert_eq!(peeked.attempt(), attempt_before, "peek changed the attempt");

            peeked.add_measurement(pos, hfr, sd, n);
            plain.add_measurement(pos, hfr, sd, n);
            a = peeked.next();
            b = plain.next();
        }

        assert!(matches!(a, Step::Done(_)), "expected Done, got {a:?}");
        assert_eq!(
            label(&a),
            label(&b),
            "the peeked run diverged from the plain one"
        );
        assert_eq!(peeked.points(), plain.points());
    }

    #[test]
    fn peek_agrees_with_the_step_the_engine_actually_takes() {
        // THE PROPERTY THE HOST BETS AN EXPOSURE ON. At every point of a clean
        // sweep, what peek says the engine will ask for is what it then asks
        // for -- the extensions, the validation move and Done included.
        let focus = 12_000;
        let curve = hyperbola(focus);
        let mut sweep = FocusSweep::new(cfg(), focus);

        let mut step = sweep.next();
        let mut checked = 0usize;
        for _ in 0..500 {
            let pos = match &step {
                Step::MoveTo(p) => *p,
                _ => break,
            };
            let (hfr, sd, n) = curve(pos);
            let (peeked, peeked_kind) = sweep.peek_next(pos, hfr, sd, n);
            sweep.add_measurement(pos, hfr, sd, n);
            step = sweep.next();
            assert_eq!(label(&peeked), label(&step), "peek disagreed after {pos}");
            assert_eq!(
                peeked_kind,
                sweep.pending_kind(),
                "peeked kind disagreed after {pos}"
            );
            checked += 1;
        }

        assert!(matches!(step, Step::Done(_)), "expected Done, got {step:?}");
        assert!(
            checked >= 9,
            "only {checked} points checked, expected the full sweep"
        );
    }

    #[test]
    fn peek_sees_the_turn_round_the_descending_rule_of_thumb_cannot() {
        // Focus two steps ABOVE the start: the initial pass and the left
        // extensions all walk down, then the engine turns and extends UP from
        // the highest point measured. A `pos - step` guess is wrong exactly
        // there; peek is not.
        let start = 10_000;
        let focus = start + 2 * STEP;
        let curve = hyperbola(focus);
        let mut sweep = FocusSweep::new(cfg(), start);

        let mut highest = i32::MIN;
        let mut turn: Option<(i32, i32, PendingKind)> = None;
        let mut step = sweep.next();
        for _ in 0..500 {
            let pos = match &step {
                Step::MoveTo(p) => *p,
                _ => break,
            };
            highest = highest.max(pos);
            let (hfr, sd, n) = curve(pos);
            let (peeked, kind) = sweep.peek_next(pos, hfr, sd, n);
            if let Step::MoveTo(next_pos) = peeked {
                if next_pos > pos && turn.is_none() {
                    turn = Some((highest, next_pos, kind));
                }
            }
            sweep.add_measurement(pos, hfr, sd, n);
            step = sweep.next();
        }

        let (highest_at_turn, target, kind) = turn.expect("the sweep never turned round");
        assert_eq!(
            target,
            highest_at_turn + STEP,
            "the right extension must continue one step above the highest point"
        );
        assert_eq!(
            kind,
            PendingKind::Point,
            "the turn-round is still a curve point"
        );
        assert!(matches!(step, Step::Done(_)), "expected Done, got {step:?}");
    }

    #[test]
    fn peek_recognises_the_validation_move() {
        // Once both sides are satisfied the engine fits and moves to the
        // fitted focus. The host must not have to count points to know that:
        // the peeked kind says so, and the position is the focus.
        let focus = 12_000;
        let curve = hyperbola(focus);
        let mut sweep = FocusSweep::new(cfg(), focus);

        let mut validation: Option<i32> = None;
        let mut kinds_before_it: Vec<PendingKind> = Vec::new();
        let mut step = sweep.next();
        for _ in 0..500 {
            let pos = match &step {
                Step::MoveTo(p) => *p,
                _ => break,
            };
            let (hfr, sd, n) = curve(pos);
            let (peeked, kind) = sweep.peek_next(pos, hfr, sd, n);
            if kind == PendingKind::Validation && validation.is_none() {
                match peeked {
                    Step::MoveTo(p) => validation = Some(p),
                    other => panic!("a validation kind must carry a MoveTo, got {other:?}"),
                }
            } else if validation.is_none() {
                kinds_before_it.push(kind);
            }
            sweep.add_measurement(pos, hfr, sd, n);
            step = sweep.next();
        }

        let target = validation.expect("no validation move was ever peeked");
        assert!(
            (target - focus).abs() <= STEP,
            "the validation move went to {target}, more than a step from {focus}"
        );
        assert!(
            kinds_before_it.iter().all(|k| *k == PendingKind::Point),
            "everything before the validation move is a curve point: {kinds_before_it:?}"
        );
        assert!(matches!(step, Step::Done(_)), "expected Done, got {step:?}");
    }

    #[test]
    fn pending_kind_names_every_move_of_a_good_run() {
        let focus = 12_000;
        let curve = hyperbola(focus);

        // The baseline pass exists only when the R^2 gate is off (STARHFR).
        let baseline_cfg = FocusConfig {
            r_squared_threshold: 0.0,
            curve_fitting: CurveFitting::Trendlines,
            ..cfg()
        };
        let mut baseline_run = FocusSweep::new(baseline_cfg, focus);
        assert_eq!(
            baseline_run.pending_kind(),
            PendingKind::None,
            "before the first next()"
        );
        match baseline_run.next() {
            Step::MoveTo(p) => assert_eq!(p, focus, "the baseline is measured at the start"),
            other => panic!("expected the baseline MoveTo, got {other:?}"),
        }
        assert_eq!(baseline_run.pending_kind(), PendingKind::Baseline);

        // The gated config skips the baseline: every swept point is a Point,
        // the last move is the Validation, and Done clears the kind.
        let mut sweep = FocusSweep::new(cfg(), focus);
        let mut kinds: Vec<PendingKind> = Vec::new();
        let mut step = sweep.next();
        for _ in 0..500 {
            let pos = match &step {
                Step::MoveTo(p) => *p,
                _ => break,
            };
            kinds.push(sweep.pending_kind());
            let (hfr, sd, n) = curve(pos);
            sweep.add_measurement(pos, hfr, sd, n);
            step = sweep.next();
        }

        assert!(matches!(step, Step::Done(_)), "expected Done, got {step:?}");
        assert_eq!(
            sweep.pending_kind(),
            PendingKind::None,
            "Done leaves nothing pending"
        );
        assert_eq!(kinds.last(), Some(&PendingKind::Validation));
        assert!(
            kinds[..kinds.len() - 1]
                .iter()
                .all(|k| *k == PendingKind::Point),
            "every swept move is a Point: {kinds:?}"
        );
        assert_eq!(OFFSET, 4, "the shape above assumes the nine-point sweep");
    }

    #[test]
    fn pending_kind_names_the_restore_on_the_failure_path() {
        // A perfectly flat curve has no trend on either side: NotEnoughSpread,
        // preceded by a restore move back to the start.
        let start = 12_000;
        let mut sweep = FocusSweep::new(cfg(), start);

        let mut last_move: Option<(i32, PendingKind)> = None;
        let mut step = sweep.next();
        for _ in 0..500 {
            let pos = match &step {
                Step::MoveTo(p) => *p,
                _ => break,
            };
            last_move = Some((pos, sweep.pending_kind()));
            sweep.add_measurement(pos, 3.0, 0.1, 30);
            step = sweep.next();
        }

        assert!(
            matches!(step, Step::Failed(FailReason::NotEnoughSpread)),
            "expected NotEnoughSpread, got {step:?}"
        );
        assert_eq!(
            last_move,
            Some((start, PendingKind::Restore)),
            "the move before Failed is the restore to the start"
        );
        assert_eq!(
            sweep.pending_kind(),
            PendingKind::None,
            "Failed leaves nothing pending"
        );
    }
}
