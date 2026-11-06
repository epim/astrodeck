# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The recovery ladder for a mount that is lost or halted during a run
(#1109, with #1098, #857 and #880).

A pure unit: states and transitions, no I/O, no clock and no mount. The
engine reads the hub's answers (a centring result, a motion epoch that moved)
into the five events below and does what the returned action says; the
words the operator reads live there too.

THE STATES
    ``trusted``            the last measurement said the field is on target.
    ``suspect``            a centring missed (the solve landed off-field, or
                           the mount did not carry out the correction). One
                           retry from where the mount stands.
    ``lost``               the retry missed too, or the mount would not take a
                           sync. Home, then the target; or nothing more.
    ``paused_by_operator`` a STOP, a manual move or the manual-move deadman
                           halted the mount during the run's goto. The only
                           exit is an operator resume (D-A, #880): until then
                           nothing automatic moves the mount, picks the next
                           target or restarts the run.

THE HOME LEG, AND WHAT IT CAN AND CANNOT REPAIR (#1098, #857)
The owner's ruling of 2026-10-19: lost between targets, slew home and then to
the target, and never gate a slew from home or park on a solve at home (the
park view may be obstructed; the goto's own solve is at the target). On the
AM5 ``find_home`` is ``:hP#``, a goto to the MODEL's home, so home re-
establishes a known slew path, cable wrap and pier side, and does NOT reset a
pointing model that is wrong (#857). A model error is repaired by a sync away
from the pole, or by a person: ``sync_not_taken`` therefore gives up at once
with no home leg, and a miss that survives the home leg gives up too.

BOUND: one home leg per target per night (the engine passes ``_budget_key``),
so a target the mount cannot reach does not shuttle home and back all night.
"""
from __future__ import annotations

from enum import Enum
from typing import Hashable

#: Home legs a target is allowed per budget key (the engine keys it on the
#: observing night and the target).
HOME_LEGS_PER_TARGET = 1


class LadderState(str, Enum):
    TRUSTED = "trusted"
    SUSPECT = "suspect"
    LOST = "lost"
    PAUSED_BY_OPERATOR = "paused_by_operator"


class Action(str, Enum):
    #: Nothing to do: the pointing is good, or the ladder has nothing to add.
    CONTINUE = "continue"
    #: Centre again from where the mount stands.
    RETRY_IN_PLACE = "retry_in_place"
    #: Slew home, then to the target, and centre from there.
    HOME_THEN_TARGET = "home_then_target"
    #: The ladder has nothing left to try; the caller's own miss handling
    #: (stop or defer the target) takes the result as it stands.
    GIVE_UP = "give_up"
    #: Hold. Nothing moves the mount until the operator resumes.
    WAIT_FOR_OPERATOR = "wait_for_operator"


class RecoveryLadder:
    def __init__(self) -> None:
        self._state = LadderState.TRUSTED
        self._home_legs: dict[Hashable, int] = {}

    @property
    def state(self) -> LadderState:
        return self._state

    @property
    def paused(self) -> bool:
        return self._state is LadderState.PAUSED_BY_OPERATOR

    def begin(self) -> None:
        """A new acquisition starts trusted. A pause is not an acquisition's
        to clear: only ``resumed`` ends it."""
        if not self.paused:
            self._state = LadderState.TRUSTED

    def centred(self) -> Action:
        if self.paused:
            return Action.WAIT_FOR_OPERATOR
        self._state = LadderState.TRUSTED
        return Action.CONTINUE

    def missed(self, key: Hashable) -> Action:
        """A centring that must not be imaged on. ``trusted`` earns one retry
        in place, ``suspect`` earns the home leg once per ``key``, and after
        that the ladder gives up."""
        if self.paused:
            return Action.WAIT_FOR_OPERATOR
        if self._state is LadderState.TRUSTED:
            self._state = LadderState.SUSPECT
            return Action.RETRY_IN_PLACE
        legs = self._home_legs.get(key, 0)
        if self._state is LadderState.SUSPECT and legs < HOME_LEGS_PER_TARGET:
            self._state = LadderState.LOST
            self._home_legs[key] = legs + 1
            return Action.HOME_THEN_TARGET
        self._state = LadderState.LOST
        return Action.GIVE_UP

    def sync_not_taken(self) -> Action:
        """The mount refused a sync, or nobody could confirm it: its model is
        in doubt, and home does not repair a model."""
        if self.paused:
            return Action.WAIT_FOR_OPERATOR
        self._state = LadderState.LOST
        return Action.GIVE_UP

    def halted(self) -> Action:
        """The motion epoch moved under the run's own goto: the operator
        (or the deadman) stopped the mount."""
        self._state = LadderState.PAUSED_BY_OPERATOR
        return Action.WAIT_FOR_OPERATOR

    def resumed(self) -> bool:
        """The operator resumed. True when the ladder was paused. The mount
        was moved by hand or stopped short, so the acquisition starts again
        trusted and measures."""
        if not self.paused:
            return False
        self._state = LadderState.TRUSTED
        return True
