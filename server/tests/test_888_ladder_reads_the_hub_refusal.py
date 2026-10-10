# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The resume ladder reads ``hub.goto_and_center``'s position-unknown
refusal (#888).

The hub now asks the one gate (``devices.base.position_known_for_motion``)
under its first motion lock and returns the aborted shape with
``position_unknown`` beside it. The ladder's step 3 asks the driver right
before the call, so the shape comes back only when the latch was set in
between; it holds in its position-unknown words and does not record the
target as re-centred (``tick`` would hand that target to the engine as the
one the mount is tracking, and nothing moved).

A file of its own because the ladder's harness (test_850_resume_sync_
refused.py) brings autouse fixtures the rest of #888's cases do not want.
The mutant was applied to a byte backup of resume_arm.py, run under the
suite's normal command, and the file restored with its sha256 checked.
"""
from __future__ import annotations

import astrodeck.sequence.resume_arm as ra
from astrodeck.mount_offset import POSITION_UNKNOWN_MOTION_DETAIL

from test_850_resume_sync_refused import (  # noqa: F401 (fixtures too)
    _Connected, _Hub, _arm, _isolated_sessions, _session, fp)

_REFUSED = {"centered": False, "error_arcmin": None, "attempts": 0,
            "aborted": True, "rotation": None, "position_unknown": True,
            "reason": POSITION_UNKNOWN_MOTION_DETAIL}


class _Known(_Connected):
    position_known = True


def _hub(centring) -> _Hub:
    hub = _Hub(solve_raises=None, centring=centring)
    hub.devices["telescope"] = _Known()
    return hub


async def test_the_ladder_holds_on_the_hub_s_position_refusal(monkeypatch):
    """The hub refused the re-centre for an unknown position: the ladder
    holds in its position-unknown words, and the target is not taken as
    re-centred.

    MUTANT RA1 "the ladder ignores the shape" (the ``if isinstance(centring,
    dict) and centring.get("position_unknown"): return POSITION_UNKNOWN_
    RECENTRE_WORDS`` in `ResumeArm._recover` removed): RED -
        AssertionError: None
    """
    hub = _hub(dict(_REFUSED))
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.POSITION_UNKNOWN_RECENTRE_WORDS, reason
    assert hub.calls == ["solve", "center"], hub.calls
    assert arm._recentred is None


async def test_control_a_centred_re_centre_resumes(monkeypatch):
    """CONTROL. A centred answer from the same call resumes, re-centred."""
    hub = _hub({"centered": True, "error_arcmin": 0.3, "attempts": 1})
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) is None
    assert arm._recentred is not None
