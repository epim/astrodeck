// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§6.1). Derived from
// PHD2 guide_algorithm_hysteresis.cpp:42-91 (constants, `reset`, `result`)
// and guide_algorithm_hysteresis.cpp:93-167 (`SetMinMove`/`SetHysteresis`/
// `SetAggression` validation, folded into `new`) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! Hysteresis guide algorithm — PHD2's default RA algorithm (dossier §6.1).
//!
//! Blends this frame's raw error with the previous frame's *issued*
//! correction (`last_move`, itself already aggression-scaled), then applies
//! aggression and a min-move deadband tested on the raw input.

use super::GuideAlgorithm;

/// `DefaultMinMove` (`guide_algorithm_hysteresis.cpp:42`).
const DEFAULT_MIN_MOVE: f64 = 0.2;
/// `DefaultHysteresis` (`guide_algorithm_hysteresis.cpp:43`).
const DEFAULT_HYSTERESIS: f64 = 0.1;
/// `DefaultAggression` (`guide_algorithm_hysteresis.cpp:44`).
const DEFAULT_AGGRESSION: f64 = 0.7;
/// `MaxAggression` (`guide_algorithm_hysteresis.cpp:45`).
const MAX_AGGRESSION: f64 = 2.0;
/// `MaxHysteresis` (`guide_algorithm_hysteresis.cpp:46`).
const MAX_HYSTERESIS: f64 = 0.99;

/// PHD2's default RA guide algorithm (dossier §6.1;
/// `GuideAlgorithmHysteresis`). Weights this frame's error against the
/// aggression-scaled correction it issued last frame, so a persistent drift
/// accumulates correction while noise averages out.
///
/// **Scope note**: `hysteresis`/`aggression`/`min_move` are plain public
/// fields (this task's frozen interface has no setter methods), so
/// upstream's `SetAggression`-also-zeroes-`last_move` side effect
/// (`guide_algorithm_hysteresis.cpp:143-167`) only happens via [`new`]
/// (constructor time, where `last_move` is already `0.0`) — mutating
/// `.aggression` directly after construction does not reset `last_move`.
#[derive(Debug, Clone, Copy)]
pub struct Hysteresis {
    pub hysteresis: f64,
    pub aggression: f64,
    pub min_move: f64,
    /// `m_lastMove`: the aggression-scaled [`result`](GuideAlgorithm::result)
    /// this instance issued last call (`0.0` when the previous call was
    /// vetoed by the min-move deadband).
    last_move: f64,
}

impl Hysteresis {
    /// Constructs with upstream's constructor-time validation
    /// (`SetMinMove`/`SetHysteresis`/`SetAggression`,
    /// `guide_algorithm_hysteresis.cpp:93-167`): `min_move < 0` falls back
    /// to the default; `hysteresis` outside `[0, MaxHysteresis]` clamps;
    /// `aggression` outside `[0, MaxAggression]` falls back to the default.
    pub fn new(hysteresis: f64, aggression: f64, min_move: f64) -> Self {
        let min_move = if min_move < 0.0 {
            DEFAULT_MIN_MOVE
        } else {
            min_move
        };
        let hysteresis = hysteresis.clamp(0.0, MAX_HYSTERESIS);
        let aggression = if !(0.0..=MAX_AGGRESSION).contains(&aggression) {
            DEFAULT_AGGRESSION
        } else {
            aggression
        };
        Hysteresis {
            hysteresis,
            aggression,
            min_move,
            last_move: 0.0,
        }
    }
}

impl Default for Hysteresis {
    /// §6.1/§15 defaults: hysteresis 0.1, aggression 0.7, min_move 0.2.
    fn default() -> Self {
        Hysteresis::new(DEFAULT_HYSTERESIS, DEFAULT_AGGRESSION, DEFAULT_MIN_MOVE)
    }
}

impl GuideAlgorithm for Hysteresis {
    /// dossier §6.1; `GuideAlgorithmHysteresis::result`,
    /// `guide_algorithm_hysteresis.cpp:75-91`. The min-move deadband is
    /// tested on `input`, not the blended result; a vetoed frame still
    /// stores `0.0` into `last_move` (decaying any prior correction).
    fn result(&mut self, input: f64) -> f64 {
        let mut r = (1.0 - self.hysteresis) * input + self.hysteresis * self.last_move;
        r *= self.aggression;
        if input.abs() < self.min_move {
            r = 0.0;
        }
        self.last_move = r;
        r
    }

    /// `guide_algorithm_hysteresis.cpp:70-73`.
    fn reset(&mut self) {
        self.last_move = 0.0;
    }

    fn min_move(&self) -> f64 {
        self.min_move
    }
}
