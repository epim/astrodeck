# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""CONTINUE across nights for a block that measures its angle (WP 6.1, #187).

Night two compiles the flow, whose block carries the angle the relay wrote
and the anchor ruling 3 kept or replaced, so a carried measurement finds
every frame (test_w6_1_measure_night.py proves the ids). What CONTINUE adds
for a RESTARTED one is the sentence: the session's old plan holds the record
of what the relay did, the replace discards the old plan, and the operator is
told the counts restarted before it does.

The route half runs through ``test_flows_continue.py``'s ``rig``: the real
app over ASGI and a real engine whose imaging loop alone is replaced.

Each case names the mutant it was shown RED under.
"""
from __future__ import annotations

import pytest

import astrodeck.events as events_module
from astrodeck.flows.continuation import layout_restarts
from astrodeck.sequence.models import SequencePlan, TargetGroup
from astrodeck.sequence.session import session_store
from test_flows_continue import _capture, rig  # noqa: F401 (fixture)

MEASURE = "Measure at the first solve"


def _record(*, carried: bool, orphaned: int, night: str = "2026-11-08") -> dict:
    return {"pa_deg": 37.0, "from_pa_deg": 30.0, "night": night,
            "carried": carried, "max_move_deg": 0.26, "threshold_deg": 0.17,
            "reason": "move", "orphaned_frames": orphaned}


def _plan(*records: dict | None) -> SequencePlan:
    return SequencePlan(targets=[], groups=[
        TargetGroup(id=f"g{i}", name=f"Block {i}", measure_angle=True,
                    node_id=f"t{i}", measured=r)
        for i, r in enumerate(records)])


def test_a_restart_with_frames_left_behind_is_said_once_per_block():
    """MUTANT "say every record": the carried block and the restart that lost
    nothing are listed too. RED: ``assert 3 == 1``."""
    said = layout_restarts(_plan(_record(carried=False, orphaned=4),
                                 _record(carried=True, orphaned=0),
                                 _record(carried=False, orphaned=0), None))
    assert len(said) == 1, said
    assert said[0].startswith("Block 0: the camera was measured at PA 37.0")
    assert "2026-11-08" in said[0] and "4 subs" in said[0]
    assert "restarted" in said[0] and "stay on disk" in said[0]


def test_one_sub_is_one_sub():
    (said,) = layout_restarts(_plan(_record(carried=False, orphaned=1)))
    assert "1 sub " in said or "1 sub shot" in said


def test_a_plan_with_no_measuring_group_says_nothing():
    plan = SequencePlan(targets=[], groups=[TargetGroup(id="g")])
    assert layout_restarts(plan) == []
    assert layout_restarts(SequencePlan(targets=[])) == []


def _grid_flow() -> dict:
    target = {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28",
              "rows": 1, "cols": 2, "overlap": 25, "fovX": 1.0, "fovY": 0.7,
              "angle": MEASURE, "rotation": 10}
    nodes = [{"id": "t", "type": "target", "x": 0, "y": 0, "params": target},
             _capture("c1", "L", count=2)]
    edges = [{"from": "t", "fromPort": "target", "to": "c1",
              "toPort": "run"}]
    return {"nodes": nodes, "edges": edges}


@pytest.fixture
def lines(monkeypatch):
    got: list[tuple[str, str]] = []
    real = events_module.bus.log

    def log(level, message, *a, **kw):
        got.append((level, message))
        return real(level, message, *a, **kw)

    monkeypatch.setattr(events_module.bus, "log", log)
    return got


async def test_continue_names_a_restart_and_says_nothing_after_a_carry(
        rig, lines):
    """Night one leaves a dormant session whose group says its counts were
    restarted at the measured angle. CONTINUE answers with the sentence and
    logs it as a warning, before the plan is replaced. A session whose record
    says carried continues in silence. MUTANT "read the new plan": the old
    plan's record is gone by then, and the answer has no key. RED:
    ``KeyError: 'layout_restarts'``."""
    fid = await rig.save_flow(_grid_flow())
    one = await rig.night_one(fid, [0])
    (group,) = one.plan.groups
    assert group.measure_angle is True
    group.measured = _record(carried=False, orphaned=3)
    session_store.save(one)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    out = r.json()["session"]
    assert out["continued"] is True
    assert len(out["layout_restarts"]) == 1, out
    assert "3 subs" in out["layout_restarts"][0]
    assert [m for lvl, m in lines
            if lvl == "warning" and "counts restarted" in m
            or lvl == "warning" and "restarted at that angle" in m], lines
    rig.night.end("incomplete")
    await rig.engine._task
    again = session_store.load(out["id"])
    again.plan.groups[0].measured = _record(carried=True, orphaned=0)
    session_store.save(again)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    assert "layout_restarts" not in r.json()["session"]
