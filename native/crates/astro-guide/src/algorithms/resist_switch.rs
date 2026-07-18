// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§6.2). Derived from
// PHD2 guide_algorithm_resistswitch.cpp:42-177 (constants, `reset`, `sign`,
// `result`) and guide_algorithm_resistswitch.cpp:179-233
// (`SetMinMove`/`SetAggression` validation, folded into `new`)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! ResistSwitch guide algorithm — PHD2's default Dec algorithm (dossier
//! §6.2).
//!
//! Only ever corrects on the side of the current drift direction, vetoing
//! reversals unless a run of at least 3 significant same-sign samples shows
//! the error getting worse (backlash-safe); `fast_switch` bypasses that wait
//! for very large deflections.

use super::GuideAlgorithm;

/// Direction-history ring length (dossier §6.2 `HISTORY_SIZE`;
/// `guide_algorithm_resistswitch.h`).
const HISTORY_SIZE: usize = 10;
/// `DefaultMinMove` (`guide_algorithm_resistswitch.cpp:42`).
const DEFAULT_MIN_MOVE: f64 = 0.2;
/// `DefaultAggression` (`guide_algorithm_resistswitch.cpp:43`).
const DEFAULT_AGGRESSION: f64 = 1.0;

/// `sign` (`guide_algorithm_resistswitch.cpp:77-91`).
fn sign(x: f64) -> i32 {
    if x > 0.0 {
        1
    } else if x < 0.0 {
        -1
    } else {
        0
    }
}

/// PHD2's default Dec guide algorithm (dossier §6.2;
/// `GuideAlgorithmResistSwitch`).
#[derive(Debug, Clone, Copy)]
pub struct ResistSwitch {
    pub min_move: f64,
    pub aggression: f64,
    pub fast_switch: bool,
    /// `m_history`: the last `HISTORY_SIZE` raw inputs, oldest first
    /// (index `0`), newest last (index `HISTORY_SIZE - 1`) — matches
    /// upstream's fixed-length FIFO (`m_history.Add(input);
    /// m_history.RemoveAt(0);`, `guide_algorithm_resistswitch.cpp:97-98`).
    history: [f64; HISTORY_SIZE],
    /// `m_currentSide`: -1/0/+1, the side ResistSwitch currently believes
    /// the drift favors.
    current_side: i32,
}

impl ResistSwitch {
    /// Constructs with upstream's constructor-time validation
    /// (`SetMinMove`/`SetAggression`,
    /// `guide_algorithm_resistswitch.cpp:179-233`): `min_move <= 0` falls
    /// back to the default (and forces `current_side` back to `0`, matching
    /// `SetMinMove`'s side effect, `guide_algorithm_resistswitch.cpp:191`);
    /// `aggression` outside `[0, 1]` falls back to the default.
    pub fn new(min_move: f64, aggression: f64, fast_switch: bool) -> Self {
        let min_move = if min_move <= 0.0 {
            DEFAULT_MIN_MOVE
        } else {
            min_move
        };
        let aggression = if !(0.0..=1.0).contains(&aggression) {
            DEFAULT_AGGRESSION
        } else {
            aggression
        };
        ResistSwitch {
            min_move,
            aggression,
            fast_switch,
            history: [0.0; HISTORY_SIZE],
            current_side: 0,
        }
    }
}

impl Default for ResistSwitch {
    /// §6.2/§15 defaults: min_move 0.2, aggression 1.0, fast_switch true.
    fn default() -> Self {
        ResistSwitch::new(DEFAULT_MIN_MOVE, DEFAULT_AGGRESSION, true)
    }
}

impl GuideAlgorithm for ResistSwitch {
    /// dossier §6.2; `GuideAlgorithmResistSwitch::result`,
    /// `guide_algorithm_resistswitch.cpp:93-177`. The min-move deadband and
    /// the direction-vote history are both tested/updated on `input`; a
    /// vetoed frame still pushes `input` into the history ring.
    fn result(&mut self, input: f64) -> f64 {
        // `m_history.Add(input); m_history.RemoveAt(0);` — shift left,
        // newest at the end.
        for i in 0..HISTORY_SIZE - 1 {
            self.history[i] = self.history[i + 1];
        }
        self.history[HISTORY_SIZE - 1] = input;

        let veto = 'v: {
            if input.abs() < self.min_move {
                break 'v true; // "input < m_minMove"
            }

            if self.fast_switch {
                let thresh = 3.0 * self.min_move;
                if sign(input) != self.current_side && input.abs() > thresh {
                    // Large excursion: force an immediate direction switch
                    // (`guide_algorithm_resistswitch.cpp:107-121`).
                    self.current_side = 0;
                    for v in self.history.iter_mut().take(HISTORY_SIZE - 3) {
                        *v = 0.0;
                    }
                    for v in self.history.iter_mut().skip(HISTORY_SIZE - 3) {
                        *v = input;
                    }
                }
            }

            // Vote: sum the signs of every history entry that clears the
            // min-move deadband (`guide_algorithm_resistswitch.cpp:124-132`).
            let dec_history: i32 = self
                .history
                .iter()
                .filter(|v| v.abs() > self.min_move)
                .map(|v| sign(*v))
                .sum();

            if self.current_side == 0 || sign(self.current_side as f64) == -sign(dec_history as f64)
            {
                if dec_history.abs() < 3 {
                    break 'v true; // "not compelling enough"
                }
                let oldest: f64 = self.history[0..3].iter().sum();
                let newest: f64 = self.history[HISTORY_SIZE - 3..].iter().sum();
                if newest.abs() <= oldest.abs() {
                    break 'v true; // "Not getting worse"
                }
                self.current_side = sign(dec_history as f64);
            }

            if self.current_side != sign(input) {
                break 'v true; // "must have overshot -- vetoing move"
            }
            false
        };

        let r = if veto { 0.0 } else { input };
        r * self.aggression
    }

    /// `guide_algorithm_resistswitch.cpp:65-75`.
    fn reset(&mut self) {
        self.history = [0.0; HISTORY_SIZE];
        self.current_side = 0;
    }

    fn min_move(&self) -> f64 {
        self.min_move
    }
}
