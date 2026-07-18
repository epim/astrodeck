// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§6.3, §6.4).
// Derived from PHD2 guide_algorithm_lowpass.cpp:42-103 (constants, `reset`,
// `result`) and guide_algorithm_lowpass.cpp:105-153 (`SetMinMove`/
// `SetSlopeWeight` validation, folded into `new`) for [`Lowpass`]; and
// guide_algorithm_lowpass2.cpp:42-123 (constants, `reset`, `result`) and
// guide_algorithm_lowpass2.cpp:125-148 (`SetMinMove`/`SetAggressiveness`
// validation, folded into `new`) for [`Lowpass2`]. The shared windowed
// linear-fit/median machinery is derived from `WindowedAxisStats`/
// `AxisStats` (`guiding_stats.cpp:524-575` linear fit, `:577-693` windowing,
// `:464-496` median) (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code
// copied from PHD2.

//! Lowpass and Lowpass2 guide algorithms (dossier §6.3/§6.4).
//!
//! Both fit a line through a short rolling window of recent errors and blend
//! that fit with the raw input; neither is a shipped default (dossier §17
//! recommends Lowpass2 as a second, predictive Dec option for high-quality
//! mounts). [`Lowpass`] is the older median-plus-slope design; [`Lowpass2`]
//! replaces the median with a pure linear-fit projection and adds outlier
//! and repeated-rejection history resets.

use std::collections::VecDeque;

use super::GuideAlgorithm;

/// Shared rolling `(t, y)` sample store behind both algorithms: O(1)
/// incremental linear fit via running sums (`AxisStats`,
/// `guiding_stats.cpp:524-575`), plus (for [`Lowpass`]) the median
/// (`guiding_stats.cpp:464-496`). [`Lowpass`] manages its own window
/// manually (`WindowedAxisStats(0)`, self-managed per
/// `guide_algorithm_lowpass.cpp:53`); [`Lowpass2`] uses [`add_auto`]
/// (`WindowedAxisStats(HISTORY_SIZE)`, `guide_algorithm_lowpass2.cpp:52`).
///
/// [`add_auto`]: AxisWindow::add_auto
#[derive(Debug, Clone)]
struct AxisWindow {
    /// `(DeltaTime, StarPos)`, oldest first (`guiding_stats.h:110-118`).
    entries: VecDeque<(f64, f64)>,
    sum_x: f64,
    sum_y: f64,
    sum_xy: f64,
    sum_xx: f64,
}

impl AxisWindow {
    fn new() -> Self {
        AxisWindow {
            entries: VecDeque::new(),
            sum_x: 0.0,
            sum_y: 0.0,
            sum_xy: 0.0,
            sum_xx: 0.0,
        }
    }

    /// `AxisStats::ClearAll` (`guiding_stats.cpp` — zeroes every running
    /// sum along with the entry queue).
    fn clear(&mut self) {
        self.entries.clear();
        self.sum_x = 0.0;
        self.sum_y = 0.0;
        self.sum_xy = 0.0;
        self.sum_xx = 0.0;
    }

    /// `AxisStats::AddGuideInfo` (running-sum half): append one sample and
    /// fold its contribution into every running sum.
    fn add(&mut self, t: f64, y: f64) {
        self.entries.push_back((t, y));
        self.sum_x += t;
        self.sum_y += y;
        self.sum_xy += t * y;
        self.sum_xx += t * t;
    }

    /// `WindowedAxisStats::RemoveOldestEntry` (`guiding_stats.cpp:660-682`):
    /// drop the oldest sample and subtract its contribution from every
    /// running sum. A no-op on an empty window.
    fn remove_oldest(&mut self) {
        if let Some((t, y)) = self.entries.pop_front() {
            self.sum_x -= t;
            self.sum_y -= y;
            self.sum_xy -= t * y;
            self.sum_xx -= t * t;
        }
    }

    /// `WindowedAxisStats::AddGuideInfo` (`guiding_stats.cpp:685-693`): add,
    /// then trim the oldest entry once the count exceeds `cap`.
    fn add_auto(&mut self, t: f64, y: f64, cap: usize) {
        self.add(t, y);
        if self.entries.len() > cap {
            self.remove_oldest();
        }
    }

    fn count(&self) -> usize {
        self.entries.len()
    }

    /// `AxisStats::GetMedian` (`guiding_stats.cpp:464-496`): even counts
    /// average the two entries adjacent to center.
    fn median(&self) -> f64 {
        let n = self.entries.len();
        if n == 0 {
            return 0.0;
        }
        let mut ys: Vec<f64> = self.entries.iter().map(|&(_, y)| y).collect();
        ys.sort_by(|a, b| a.partial_cmp(b).expect("guide error is never NaN"));
        let mid = n / 2;
        if n % 2 == 1 {
            ys[mid]
        } else {
            (ys[mid] + ys[mid - 1]) / 2.0
        }
    }

    /// `AxisStats::GetLinearFitResults` (`guiding_stats.cpp:524-575`),
    /// slope/intercept only — no caller here needs R²/sigma. `count() <= 1`
    /// returns `(0.0, 0.0)` (`:528-535`).
    fn linear_fit(&self) -> (f64, f64) {
        let n = self.entries.len() as f64;
        if n <= 1.0 {
            return (0.0, 0.0);
        }
        let slope = (n * self.sum_xy - self.sum_x * self.sum_y)
            / (n * self.sum_xx - self.sum_x * self.sum_x);
        let intercept = (self.sum_y - slope * self.sum_x) / n;
        (slope, intercept)
    }
}

// ---------------------------------------------------------------------------
// Lowpass (dossier §6.3)
// ---------------------------------------------------------------------------

/// `HISTORY_SIZE` (dossier §6.3; `guide_algorithm_lowpass.h:47`).
const LOWPASS_HISTORY_SIZE: usize = 10;
/// `DefaultMinMove` (`guide_algorithm_lowpass.cpp:42`).
const LOWPASS_DEFAULT_MIN_MOVE: f64 = 0.2;
/// `DefaultSlopeWeight` (`guide_algorithm_lowpass.cpp:43`).
const LOWPASS_DEFAULT_SLOPE_WEIGHT: f64 = 5.0;

/// The older PHD2 guide algorithm (dossier §6.3; `GuideAlgorithmLowpass`),
/// "not a default, included for completeness". Blends the window's median
/// with a slope-weighted linear-fit projection, clamped to the raw input's
/// magnitude.
#[derive(Debug, Clone)]
pub struct Lowpass {
    pub min_move: f64,
    pub slope_weight: f64,
    window: AxisWindow,
    /// `m_timeBase`: monotonic frame counter fed as each sample's `x`.
    time_base: f64,
}

impl Lowpass {
    /// Constructs with upstream's constructor-time validation
    /// (`SetMinMove`/`SetSlopeWeight`, `guide_algorithm_lowpass.cpp:105-153`):
    /// `min_move < 0` and `slope_weight < 0` each fall back to their default.
    pub fn new(min_move: f64, slope_weight: f64) -> Self {
        let min_move = if min_move < 0.0 {
            LOWPASS_DEFAULT_MIN_MOVE
        } else {
            min_move
        };
        let slope_weight = if slope_weight < 0.0 {
            LOWPASS_DEFAULT_SLOPE_WEIGHT
        } else {
            slope_weight
        };
        let mut lp = Lowpass {
            min_move,
            slope_weight,
            window: AxisWindow::new(),
            time_base: 0.0,
        };
        lp.reset();
        lp
    }
}

impl Default for Lowpass {
    /// §6.3/§15 defaults: min_move 0.2, slope_weight 5.0.
    fn default() -> Self {
        Lowpass::new(LOWPASS_DEFAULT_MIN_MOVE, LOWPASS_DEFAULT_SLOPE_WEIGHT)
    }
}

impl GuideAlgorithm for Lowpass {
    /// dossier §6.3; `GuideAlgorithmLowpass::result`,
    /// `guide_algorithm_lowpass.cpp:77-103`. The median is read on the
    /// *untrimmed* 11-sample window (this frame's sample plus the prior 10),
    /// then the oldest sample is dropped back to 10 before the linear fit —
    /// replicating that exact ordering, not just its effect, matters (dossier
    /// §17 "replicate code not comment").
    fn result(&mut self, input: f64) -> f64 {
        self.window.add(self.time_base, input);
        self.time_base += 1.0;
        let median = self.window.median();
        self.window.remove_oldest();
        let (slope, _intercept) = self.window.linear_fit();
        let mut r = median + self.slope_weight * slope;
        if r.abs() > input.abs() {
            r = input; // "input is < calculated value, using input"
        }
        if input.abs() < self.min_move {
            r = 0.0;
        }
        r
    }

    /// `guide_algorithm_lowpass.cpp:65-75`: clears the window, then
    /// zero-fills it back to [`LOWPASS_HISTORY_SIZE`] entries so the window
    /// is never short for the median/linear-fit calls in [`result`](Self::result).
    fn reset(&mut self) {
        self.window.clear();
        self.time_base = 0.0;
        while self.window.count() < LOWPASS_HISTORY_SIZE {
            self.window.add(self.time_base, 0.0);
            self.time_base += 1.0;
        }
    }

    fn min_move(&self) -> f64 {
        self.min_move
    }
}

// ---------------------------------------------------------------------------
// Lowpass2 (dossier §6.4)
// ---------------------------------------------------------------------------

/// `HISTORY_SIZE` (dossier §6.4; `guide_algorithm_lowpass2.h:47`).
const LOWPASS2_HISTORY_SIZE: usize = 10;
/// `DefaultMinMove` (`guide_algorithm_lowpass2.cpp:42`).
const LOWPASS2_DEFAULT_MIN_MOVE: f64 = 0.2;
/// `DefaultAggressiveness` (`guide_algorithm_lowpass2.cpp:43`).
const LOWPASS2_DEFAULT_AGGRESSIVENESS: f64 = 80.0;
/// Outlier-deflection multiple of `min_move` that dumps history immediately
/// (`guide_algorithm_lowpass2.cpp:84`).
const OUTLIER_MIN_MOVE_MULTIPLE: f64 = 4.0;
/// Consecutive rejected corrections before the history is dumped as "not
/// useful" (`guide_algorithm_lowpass2.cpp:108`). Note the STRICT `>`: reset
/// fires on the *fourth* consecutive reject, not the third — the upstream
/// comment says "3-in-a-row" but the code checks `m_rejects > 3` (dossier
/// §17 "replicate code not comment").
const REJECT_STREAK_LIMIT: u32 = 3;

/// PHD2's linear-fit predictor (dossier §6.4; `GuideAlgorithmLowpass2`;
/// dossier §17's recommended second Dec option for high-quality mounts).
/// Projects the window's fitted slope forward by the window length itself
/// (`slope * n`, not just `slope`), self-limits to the current deflection's
/// magnitude, and dumps its history on an outlier deflection or a run of
/// rejected (over-large) projections.
#[derive(Debug, Clone)]
pub struct Lowpass2 {
    pub min_move: f64,
    pub aggressiveness: f64,
    window: AxisWindow,
    /// `m_timeBase`: monotonic frame counter fed as each sample's `x`.
    time_base: f64,
    /// `m_rejects`: consecutive "projection exceeded the input" events.
    rejects: u32,
}

impl Lowpass2 {
    /// Constructs with upstream's constructor-time validation folded in
    /// (`SetMinMove`/`SetAggressiveness`,
    /// `guide_algorithm_lowpass2.cpp:125-207`): `min_move < 0` falls back to
    /// the default (upstream-faithful: `m_minMove = DefaultMinMove` in the
    /// catch, `guide_algorithm_lowpass2.cpp:138-143`).
    ///
    /// ADJUDICATION — `aggressiveness < 0` falls back to the default, which
    /// is a **deliberate divergence from upstream's literal code**:
    /// `SetAggressiveness`'s catch assigns the *local parameter*, not the
    /// member (`aggressiveness = DefaultAggressiveness;`,
    /// `guide_algorithm_lowpass2.cpp:201` — contrast `SetMinMove`'s correct
    /// `m_minMove = DefaultMinMove` at `:142`), so upstream leaves
    /// `m_aggressiveness` untouched on invalid input — stale at runtime, and
    /// *uninitialized memory* on the constructor path — while persisting the
    /// default to the profile (`:204`), which then masks the bug on the next
    /// restart. Replicating that would mean constructing with an undefined
    /// aggressiveness; assigning the default to the real field is what
    /// upstream's own `SetMinMove` pattern (and its profile write) shows was
    /// intended. Safer-than-upstream, consciously chosen — the P1-T1/P1-T5
    /// adjudication idiom.
    ///
    /// Unlike `min_move`, upstream's `SetAggressiveness` has no
    /// code-enforced upper bound (the 0..100 range is a UI spin-control
    /// limit only, `guide_algorithm_lowpass2.cpp:231-232`), so values above
    /// 100 pass through unclamped here too.
    pub fn new(min_move: f64, aggressiveness: f64) -> Self {
        let min_move = if min_move < 0.0 {
            LOWPASS2_DEFAULT_MIN_MOVE
        } else {
            min_move
        };
        let aggressiveness = if aggressiveness < 0.0 {
            LOWPASS2_DEFAULT_AGGRESSIVENESS
        } else {
            aggressiveness
        };
        Lowpass2 {
            min_move,
            aggressiveness,
            window: AxisWindow::new(),
            time_base: 0.0,
            rejects: 0,
        }
    }
}

impl Default for Lowpass2 {
    /// §6.4/§15 defaults: min_move 0.2, aggressiveness 80 (%).
    fn default() -> Self {
        Lowpass2::new(LOWPASS2_DEFAULT_MIN_MOVE, LOWPASS2_DEFAULT_AGGRESSIVENESS)
    }
}

impl GuideAlgorithm for Lowpass2 {
    /// dossier §6.4; `GuideAlgorithmLowpass2::result`,
    /// `guide_algorithm_lowpass2.cpp:72-123`. The reject-streak accounting
    /// (`|r| > |input|` clamp) runs for *every* branch including warm-up,
    /// though warm-up's `r = input * att` can only trip it when
    /// `aggressiveness > 100`.
    fn result(&mut self, input: f64) -> f64 {
        self.window
            .add_auto(self.time_base, input, LOWPASS2_HISTORY_SIZE);
        self.time_base += 1.0;
        let n = self.window.count();
        let att = self.aggressiveness / 100.0;

        let mut r;
        if n < 4 {
            r = input * att; // warm-up: act like proportional
        } else if input.abs() > OUTLIER_MIN_MOVE_MULTIPLE * self.min_move {
            r = input * att; // outlier deflection: dump the history
            self.reset();
        } else {
            let (slope, _intercept) = self.window.linear_fit();
            r = slope * n as f64 * att; // predicted cumulative drift
            if input * r < 0.0 {
                r = 0.0; // never push the wrong way
            }
        }

        if r.abs() > input.abs() {
            // Keep pulses <= the magnitude of the last deflection.
            r = input * att;
            self.rejects += 1;
            if self.rejects > REJECT_STREAK_LIMIT {
                self.reset(); // slope isn't useful
            }
        } else {
            self.rejects = 0;
        }

        if input.abs() < self.min_move {
            r = 0.0;
        }
        r
    }

    /// `guide_algorithm_lowpass2.cpp:65-70`.
    fn reset(&mut self) {
        self.window.clear();
        self.time_base = 0.0;
        self.rejects = 0;
    }

    fn min_move(&self) -> f64 {
        self.min_move
    }
}
