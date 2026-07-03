// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/nina-autofocus.md (§8 backlash: Overshoot
// and Absolute decorators, direction determination, clamping). No code
// copied from NINA.

//! Backlash compensation as a position-transform layer (dossier §8).
//!
//! A focuser move is decomposed into the sequence of *raw* device positions
//! to command. The host performs the physical moves; the pure state here is
//! the direction memory (Overshoot) and the persistent offset (Absolute).
//! Direction: `OUT` = increasing position, `IN` = decreasing; an equal
//! target keeps the previous direction.
//!
//! The dossier's "double settle" note (§Gaps 6) — `FocuserSettleTime` applied
//! after both the overshoot leg and the final approach — is a host timing
//! concern and is not modeled here (this crate is timing-free); a
//! two-element plan signals the host to settle after each leg.

use crate::config::BacklashModel;

/// Move direction relative to the previous position.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    /// Increasing focuser position.
    Out,
    /// Decreasing focuser position.
    In,
    /// Target equals current position — keep the previous direction.
    Keep,
}

fn direction(from: i32, to: i32) -> Direction {
    match to.cmp(&from) {
        std::cmp::Ordering::Greater => Direction::Out,
        std::cmp::Ordering::Less => Direction::In,
        std::cmp::Ordering::Equal => Direction::Keep,
    }
}

/// Backlash compensator holding the strategy and its persistent state.
#[derive(Debug, Clone)]
pub struct Backlash {
    model: BacklashModel,
    backlash_in: i32,
    backlash_out: i32,
    max_step: i32,
    /// Last non-`Keep` direction actually moved (both strategies).
    last_direction: Direction,
    /// Persistent hidden offset (Absolute strategy only).
    offset: i32,
}

impl Backlash {
    /// Create a compensator. `max_step` is the focuser's maximum position;
    /// `initial_direction` seeds the direction memory (`Out` by default is a
    /// safe choice for a fresh sweep that starts by moving out).
    pub fn new(
        model: BacklashModel,
        backlash_in: i32,
        backlash_out: i32,
        max_step: i32,
        initial_direction: Direction,
    ) -> Self {
        Backlash {
            model,
            backlash_in,
            backlash_out,
            max_step,
            last_direction: if initial_direction == Direction::Keep {
                Direction::Out
            } else {
                initial_direction
            },
            offset: 0,
        }
    }

    /// The externally reported position for a given raw device position:
    /// `raw - offset` for Absolute, `raw` otherwise (dossier §8.2).
    pub fn reported_position(&self, raw_position: i32) -> i32 {
        match self.model {
            BacklashModel::Absolute => raw_position - self.offset,
            BacklashModel::Overshoot => raw_position,
        }
    }

    /// Plan a move from raw `from` to logical `target`, returning the raw
    /// device positions to command in order. Updates internal state. For
    /// Overshoot this may be two positions `[overshoot, target]`; for
    /// Absolute it is a single adjusted position (dossier §8.1/§8.2).
    pub fn plan_move(&mut self, from: i32, target: i32) -> Vec<i32> {
        match self.model {
            BacklashModel::Overshoot => self.plan_overshoot(from, target),
            BacklashModel::Absolute => self.plan_absolute(from, target),
        }
    }

    fn plan_overshoot(&mut self, from: i32, target: i32) -> Vec<i32> {
        let dir = match direction(from, target) {
            Direction::Keep => self.last_direction,
            d => d,
        };
        let comp = if dir == Direction::In && self.backlash_in != 0 {
            -self.backlash_in
        } else if dir == Direction::Out && self.backlash_out != 0 {
            self.backlash_out
        } else {
            0
        };
        if dir != Direction::Keep {
            self.last_direction = dir;
        }
        let mut plan = Vec::new();
        if comp != 0 {
            let overshoot = target + comp;
            if (0..=self.max_step).contains(&overshoot) {
                plan.push(overshoot);
            }
            // else: skip overshoot entirely (dossier §8.1).
        }
        plan.push(target);
        plan
    }

    fn plan_absolute(&mut self, from: i32, target: i32) -> Vec<i32> {
        let adjusted = target + self.offset;
        // Clamp branches reset the offset and move straight to the bound,
        // then go through the same base-move direction update as the normal
        // path below (dossier §8.2): with `offset` already reset to 0 here,
        // that is `direction(from - self.offset, raw_target)`.
        if adjusted < 0 {
            self.offset = 0;
            self.last_direction = nonkeep(direction(from - self.offset, 0), self.last_direction);
            return vec![0];
        }
        if adjusted > self.max_step {
            self.offset = 0;
            self.last_direction = nonkeep(
                direction(from - self.offset, self.max_step),
                self.last_direction,
            );
            return vec![self.max_step];
        }
        // Direction of the (pre-compensation) adjusted target vs current raw
        // — used only to pick the compensation on a direction REVERSAL.
        let dir = match direction(from, adjusted) {
            Direction::Keep => self.last_direction,
            d => d,
        };
        // Compensate only on a direction REVERSAL.
        let comp = if dir == Direction::In && self.last_direction == Direction::Out {
            -self.backlash_in
        } else if dir == Direction::Out && self.last_direction == Direction::In {
            self.backlash_out
        } else {
            0
        };
        self.offset += comp; // BEFORE the stored-direction computation below.
        let raw_target = adjusted + comp;
        // Quirk to reproduce exactly (dossier §8.2): the decorator base move
        // recomputes the stored direction from the *offset-adjusted* reported
        // position — using the offset that was JUST updated above — against
        // the raw target, not from the pre-compensation `dir` computed above.
        self.last_direction = nonkeep(
            direction(from - self.offset, raw_target),
            self.last_direction,
        );
        vec![raw_target]
    }
}

fn nonkeep(d: Direction, fallback: Direction) -> Direction {
    match d {
        Direction::Keep => fallback,
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overshoot_in_move_overshoots_past_target() {
        // backlash_in = 100, move IN from 5000 to 4000 -> overshoot to 3900 then 4000.
        let mut b = Backlash::new(BacklashModel::Overshoot, 100, 0, 100_000, Direction::Out);
        assert_eq!(b.plan_move(5000, 4000), vec![3900, 4000]);
    }

    #[test]
    fn overshoot_out_move_no_out_backlash() {
        // Only IN backlash configured; an OUT move has no compensation.
        let mut b = Backlash::new(BacklashModel::Overshoot, 100, 0, 100_000, Direction::In);
        assert_eq!(b.plan_move(4000, 5000), vec![5000]);
    }

    #[test]
    fn overshoot_out_move_with_out_backlash() {
        let mut b = Backlash::new(BacklashModel::Overshoot, 0, 80, 100_000, Direction::In);
        assert_eq!(b.plan_move(4000, 5000), vec![5080, 5000]);
    }

    #[test]
    fn overshoot_skipped_when_out_of_range() {
        // Overshoot below 0 is skipped; move straight to target.
        let mut b = Backlash::new(BacklashModel::Overshoot, 100, 0, 100_000, Direction::Out);
        assert_eq!(b.plan_move(50, 30), vec![30]);
    }

    #[test]
    fn absolute_compensates_only_on_reversal() {
        let mut b = Backlash::new(BacklashModel::Absolute, 100, 60, 100_000, Direction::Out);
        // Moving OUT (same direction as memory) -> no comp, no offset.
        assert_eq!(b.plan_move(5000, 5100), vec![5100]);
        assert_eq!(b.reported_position(5100), 5100);
        // Now reverse to IN -> comp -100, offset -100.
        assert_eq!(b.plan_move(5100, 5050), vec![4950]);
        // reported = raw - offset = 4950 - (-100) = 5050 (compensation hidden).
        assert_eq!(b.reported_position(4950), 5050);
    }

    #[test]
    fn absolute_stored_direction_uses_post_update_offset_quirk() {
        // Dossier §8.2 worked example: accumulated offset=-140, last_direction=In,
        // raw from=4000, backlash_in=100, backlash_out=60, small OUT move to
        // logical target 4142.
        let mut b = Backlash::new(BacklashModel::Absolute, 100, 60, 100_000, Direction::Out);
        b.offset = -140;
        b.last_direction = Direction::In;

        let plan = b.plan_move(4000, 4142);
        // adjusted = 4142 + (-140) = 4002; dir(4000, 4002) = Out reverses the
        // stored In -> comp = +60; offset_new = -80; raw_target = 4062.
        assert_eq!(plan, vec![4062], "raw_target");
        assert_eq!(b.offset, -80, "offset after the move");
        // Quirk: the stored direction is direction(from - offset_new, raw_target)
        // = direction(4080, 4062) = In — NOT the pre-compensation `dir` (Out).
        assert_eq!(b.last_direction, Direction::In, "stored direction");

        // Follow-up move: NINA's next reversal check must use the (correct)
        // stored In direction, not the wrong Out a buggy implementation would
        // have stored. From the new raw position (4062) to a further-OUT
        // target, dir is Out again, which — against a stored direction of In —
        // is a reversal and re-applies backlash_out. (A buggy implementation
        // that stored Out here would see no reversal and comp=0, moving
        // straight to 4120 instead of 4180.)
        let plan2 = b.plan_move(4062, 4200);
        // adjusted = 4200 + (-80) = 4120; dir(4062, 4120) = Out; reversal vs
        // last_direction=In -> comp = +60; offset -80+60 = -20; raw_target = 4180.
        assert_eq!(plan2, vec![4180], "raw_target of the follow-up move");
        assert_eq!(b.offset, -20, "offset after the follow-up move");
    }

    #[test]
    fn absolute_clamps_below_zero() {
        let mut b = Backlash::new(BacklashModel::Absolute, 100, 60, 100_000, Direction::Out);
        // Force an offset first via a reversal.
        b.plan_move(5000, 4000); // IN reversal -> offset -100
                                 // A target that pushes adjusted below 0 clamps to 0 and resets offset.
        assert_eq!(b.plan_move(100, 50), vec![0]);
        assert_eq!(b.reported_position(0), 0);
    }
}
