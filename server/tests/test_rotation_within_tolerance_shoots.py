# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A rotation that stopped short of converging is not a failed visit when the
hop's measured angle is already within the group's angle tolerance (#526, #534;
release 0.3.37 hotfix).

The night this comes from: 2026-09-28 23:13, the rotator on astrotown moved
-5.7 deg on command and the camera's sky angle did not follow (+0.5 deg), so
``rotate_to_pa`` stopped "not converging" and set ``rotation_skipped``. For a
rotating mosaic ``_group_hop_checks`` turns ``rotation_skipped`` into a
``PanelDeferred`` BEFORE ``_group_angle_check`` looks at the angle the hop
measured, so a camera sitting 3 deg from a 6.1 deg tolerance was struck as a
failed visit, and three such visits set the panel aside for the night. What a
mosaic needs is that its panels share one angle within the tolerance, which
``_group_angle_check`` already judges from the measurement. So a stopped-short
rotation defers only when the measured angle is outside the tolerance, stale,
or missing, or when the group has no tolerance to judge by.

Runs the REAL `_run_scheduled`, `_setup_target`, `_run_steps` and `_run_step`
on the clocked simulator (tests/_group_harness.py). The fixture mosaic is a 2x2
of L and R visited 1-1 1-2 2-2 2-1; its group is "M31".

NAMED MUTANTS, each run in this release worktree's private copy of the file
and restored byte for byte:
  M1 "a skipped rotation defers whatever the angle" (`_group_hop_checks` raises
     on ``rotation_skipped`` without reading ``angle_ok``, the code as it stood
     before this fix). Observed:
       AssertionError: 1-2 did not shoot on its first visit though its measured
       angle was 3 deg inside the tolerance: [('M31 1-1', ('L', 'R')),
       ('M31 1-2', ()), ...]
  M2 "any measurement is within" (`_hop_angle_within` returns True whenever
     the group has a tolerance). Observed (3 failed, 4 passed): the
     outside, stale and missing-measurement cases, e.g.
       AssertionError: assert not True
        +  where True = <function SequenceEngine._hop_angle_within ...>(
       TargetGroup(... pa_deg=30.0, rotate=True, angle_tolerance_deg=6.0 ...),
       None, 99.0)
     The harness control still passes under M2: `_group_angle_check` judges
     the same 40 deg measurement on its own and defers the panel (kind
     "angle"), so an out-of-tolerance camera is refused twice over.
"""
from __future__ import annotations

import pytest

from _group_harness import GROUP_NAME, Night, grid_plan, group_hub, group_store  # noqa: F401
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import TargetGroup
from astrodeck.sequence.session import session_store


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


async def _night(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _goto_skips_1_2_once(who, n, result):
    if who == _name("1-2") and n == 1:
        return {**result, "rotation_skipped": True}
    return result


PLAN_KW = {"rotate": True, "pa_deg": 30.0, "angle_tolerance_deg": 6.0}


async def test_a_short_rotation_inside_the_tolerance_shoots(group_hub, monkeypatch):
    """1-2's first hop reports ``rotation_skipped`` but measures 33 deg against
    a 30 deg layout with a 6 deg tolerance: it shoots at once."""
    plan = grid_plan(group_kw=dict(PLAN_KW))
    night = await _night(group_hub, monkeypatch, plan,
                         goto=_goto_skips_1_2_once,
                         sky=lambda who, n: 33.0)
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert visits[1] == (_name("1-2"), ("L", "R")), (
        f"1-2 did not shoot on its first visit though its measured angle was "
        f"3 deg inside the tolerance: {visits[:5]}")


async def test_control_a_short_rotation_outside_the_tolerance_still_defers(
        group_hub, monkeypatch):
    """The same skipped rotation measuring 40 deg (10 off) defers as before;
    every other hop measures 30 so only 1-2's first visit is in question."""
    def sky(who, n):
        return 40.0 if who == _name("1-2") and n == 1 else 30.0
    plan = grid_plan(group_kw=dict(PLAN_KW))
    night = await _night(group_hub, monkeypatch, plan,
                         goto=_goto_skips_1_2_once, sky=sky)
    visits = night.visits()
    assert visits[1] == (_name("1-2"), ()), (
        "1-2 shot on its first visit though its measured angle was 10 deg "
        "outside the tolerance")


def _group(**kw) -> TargetGroup:
    base = {"id": "g", "name": "M31", "rotate": True, "pa_deg": 30.0,
            "angle_tolerance_deg": 6.0}
    base.update(kw)
    return TargetGroup(**base)


def _rec(pa: float, exposed_at: float) -> dict:
    return {"pa_deg": pa, "exposed_at": exposed_at, "solved_at": exposed_at}


class TestHopAngleWithin:
    def test_inside_the_tolerance_mod_180(self):
        assert SequenceEngine._hop_angle_within(_group(), _rec(213.0, 100.0), 99.0)

    def test_outside_the_tolerance(self):
        assert not SequenceEngine._hop_angle_within(_group(), _rec(40.0, 100.0), 99.0)

    def test_a_stale_measurement_is_not_evidence(self):
        """Exposed before the hop began: it measured the previous pointing."""
        assert not SequenceEngine._hop_angle_within(_group(), _rec(30.0, 98.0), 99.0)

    def test_no_measurement_is_not_evidence(self):
        assert not SequenceEngine._hop_angle_within(_group(), None, 99.0)

    def test_a_group_with_no_tolerance_has_nothing_to_judge_by(self):
        """None disables the angle check (spec 3.4), so it cannot waive a
        deferral either: the pre-0.3.37 behaviour, which the S2 test
        test_a_rotation_that_did_not_happen_defers_the_panel still pins."""
        assert not SequenceEngine._hop_angle_within(
            _group(angle_tolerance_deg=None), _rec(30.0, 100.0), 99.0)
