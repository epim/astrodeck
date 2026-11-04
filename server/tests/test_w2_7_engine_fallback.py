# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The engine's angle check judges a corrected group by ``allowed_error``
(#787, #828; #1116 C-10).

``test_w2_7_allowed_error.py`` pins the function. This pins that
``SequenceEngine._group_angle_check`` reads it: a 4x1 of 2.0 x 1.33 deg panels
at 10 percent overlap at Dec 75 (made-up centre), compiled by the real
``to_plan``, whose camera reads 1.0 deg off the layout angle.

* A rotator corrects: the group's own tolerance (2.12 deg) stands and the hop
  passes.
* Rotation is off for the night (D-05): the camera is a fixed one, held to the
  0.47 deg convergence leaves it, so the hop sets the whole group aside.

Named mutant: ``_group_angle_check`` reading ``group.angle_tolerance_deg``
again. The fallback case then passes the camera (it raises nothing).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.engine import GroupSetAside, SequenceEngine

SINCE = 100.0
OFF_BY = 1.0


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _plan():
    params = {"name": "Probe", "ra": "06h 00m 00s", "dec": "+75 00 00",
              "fovX": 2.0, "fovY": 1.33, "rows": 1, "cols": 4,
              "overlap": 10, "angle": "Rotate to PA", "rotation": 0}
    graph = FlowGraph(
        nodes=[_n("d", "dusk"), _n("t", "target", x=100, **params),
               _n("s0", "cycle", x=200)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "s0", "run"),
               _e("s0", "pass", "t", "next")])
    plan, _ = to_sequence_plan(compile_plan(graph, "n"), graph,
                               flow_id="flow-w2-7e", rig=None)
    return plan


def _engine(plan, *, rotation_trusted):
    eng = SequenceEngine.__new__(SequenceEngine)
    eng._plan = plan
    eng._groups = {g.id: g for g in plan.groups}
    eng._group_runs = {}
    eng._group_angle_read = {}
    eng.hub = SimpleNamespace(_rotation_trusted=rotation_trusted)
    return eng


async def _hop(eng, plan, *, planned_pa):
    group = plan.groups[0]
    member = plan.targets[0]
    rec = {"pa_deg": planned_pa + OFF_BY, "exposed_at": SINCE + 1.0}
    await eng._group_angle_check(member, group, None, rec, SINCE)


async def test_a_rotator_that_corrects_passes_one_degree_off():
    """The corrected 2.12 deg tolerance stands while the rotator corrects."""
    plan = _plan()
    group, member = plan.groups[0], plan.targets[0]
    assert group.rotate and member.rotation_deg != group.pa_deg
    eng = _engine(plan, rotation_trusted=True)
    await _hop(eng, plan, planned_pa=member.rotation_deg)
    assert eng._group_angle_read[group.id][0] == pytest.approx(
        member.rotation_deg + OFF_BY)


async def test_with_rotation_off_one_degree_off_sets_the_group_aside():
    """The fixed camera's 0.47 deg: the same offset from the layout angle is
    off, and a fixed camera cannot turn itself, so the group is set aside.

    Mutant "engine reads group.angle_tolerance_deg": nothing is raised and the
    camera is passed (``Failed: DID NOT RAISE``).
    """
    plan = _plan()
    eng = _engine(plan, rotation_trusted=False)
    with pytest.raises(GroupSetAside, match="beyond the 0.5 deg tolerance"):
        await _hop(eng, plan, planned_pa=plan.groups[0].pa_deg)
