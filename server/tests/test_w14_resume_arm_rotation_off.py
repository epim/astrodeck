# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""D-05, the auto-resume half (backlog ruling D-05, owner-approved
2026-09-30; #648): the re-centre after a restart commands no angle once the
nightly rotator self-test has failed.

``resume_arm.commanded_rotation`` is the ladder's twin of
``SequenceEngine._commanded_rotation``: the planned angle first, else a lock a
connected rotator can turn to. Once ``Hub._rotation_trusted`` is False the hub
refuses every ``rotate_to_pa``, so asking for an angle only provokes a refused
rotate and a warning in the middle of a recovery that is racing the dawn.
With the trust False it answers None, the call is today's two-argument goto,
and the frames are shot at a fixed angle like every other panel that night.

Pure arithmetic on a session double and a hub double; no rig, no clock.

NAMED MUTANT (run from a byte backup of resume_arm.py in this worktree and
restored byte for byte): "trust ignored" (the early ``return None`` for a
hub whose ``_rotation_trusted`` is False deleted); the failure it produced is
quoted on the case that caught it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrodeck.sequence.resume_arm import commanded_rotation


def _hub(trusted, *, rotator: bool = True):
    rot = SimpleNamespace(connected=True) if rotator else None
    return SimpleNamespace(_rotation_trusted=trusted,
                           devices={"rotator": rot} if rotator else {})


def _session(lock_pa=None):
    lock = None if lock_pa is None else {"pa_deg": lock_pa}
    return SimpleNamespace(locked_angle=lambda tid: lock)


def _target(rotation_deg=None):
    return SimpleNamespace(id="t1", rotation_deg=rotation_deg)


def test_a_planned_angle_is_not_commanded_once_rotation_is_off():
    """The panel's own angle (a group member carries the group's PA) is
    withheld when the hub has measured the coupling bad.

    RED under mutant "trust ignored", observed:

        AssertionError: assert 30.0 is None
    """
    assert commanded_rotation(_session(), _target(30.0), _hub(False)) is None


def test_a_locked_angle_is_not_commanded_once_rotation_is_off():
    """A lock a connected rotator could turn to is withheld too.

    RED under mutant "trust ignored", observed:

        AssertionError: assert 41.0 is None
    """
    assert commanded_rotation(_session(41.0), _target(None),
                              _hub(False)) is None


@pytest.mark.parametrize("trusted", [True, None])
def test_a_trusted_or_untested_rotator_is_commanded_as_before(trusted):
    """Only a MEASURED failure gates anything (hub.py's own rule for None):
    a passed self-test and a night that has not had one yet both command the
    planned angle, and a lock to a connected rotator."""
    assert commanded_rotation(_session(), _target(30.0),
                              _hub(trusted)) == 30.0
    assert commanded_rotation(_session(), _target(0.0), _hub(trusted)) == 0.0
    assert commanded_rotation(_session(41.0), _target(None),
                              _hub(trusted)) == 41.0


def test_a_caller_with_no_hub_is_unchanged():
    """``hub=None`` skips every rig check (a direct test of the lock
    arithmetic), the trust included."""
    assert commanded_rotation(_session(), _target(30.0), None) == 30.0
    assert commanded_rotation(_session(41.0), _target(None), None) == 41.0


def test_a_hub_double_with_no_trust_attribute_reads_as_untested():
    """The recovery paths are reached with duck-typed hubs; one that has no
    ``_rotation_trusted`` has measured nothing and refuses nothing."""
    hub = SimpleNamespace(devices={"rotator": SimpleNamespace(connected=True)})
    assert commanded_rotation(_session(), _target(30.0), hub) == 30.0
