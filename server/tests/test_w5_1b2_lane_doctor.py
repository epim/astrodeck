# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A lane the compile cannot keep in order is an error naming its nodes, and a
POOL quota the run does not read says so (WP 5.1b2, #1074, #774).

THE CLAIM. The compile kept a chain of FILTER CYCLEs in silence, and a POOL's
`quota` over CAPTURE LOOPs did nothing in silence. Now a lane whose stages the
plan cannot hold in order (two stages with no wire between them, or a chain
under a POOL, which shoots each candidate whole) is a doctor error AND an
unmapped loss, both naming the stages by node id and leaving the block compiled as it always was;
and a POOL whose stages hold no FILTER CYCLE draws a note that its quota is not
read there.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.doctor import check
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import create_params
from astrodeck.flows.to_plan import losses, to_sequence_plan

WHEN = 1788313689.0

TARGET = {"name": "NGC7331", "ra": "22h 37m 04s", "dec": "+34 24 56"}


def graph(nodes, edges, over=None) -> FlowGraph:
    over = over or {}
    return FlowGraph(
        nodes=[FlowNode(id=i, type=t, x=float(x), y=float(y),
                        params={**create_params(t), **over.get(i, {})})
               for i, t, x, y in nodes],
        edges=[FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})
               for a, ap, b, bp in edges])


def branched_lane() -> FlowGraph:
    """A TARGET feeding two FILTER CYCLEs: the flow draws no order between
    them."""
    return graph(
        [("n2", "target", 30, 100), ("cyc_a", "cycle", 300, 60),
         ("cyc_b", "cycle", 300, 200)],
        [("n2", "target", "cyc_a", "run"), ("n2", "target", "cyc_b", "run")],
        {"n2": TARGET, "cyc_a": {"plan": "L 60, R 60", "cycles": 2},
         "cyc_b": {"plan": "S 180, Ha 180", "cycles": 2}})


def pool_chain() -> FlowGraph:
    """A POOL whose lane is a FILTER CYCLE and then a CAPTURE LOOP."""
    return graph(
        [("n20", "pool", 30, 100), ("cyc_1", "cycle", 300, 100),
         ("cap_2", "capture", 600, 100)],
        [("n20", "target", "cyc_1", "run"), ("cyc_1", "complete", "cap_2", "run")],
        {"n20": {"members": "M31, M33"},
         "cyc_1": {"plan": "L 60, R 60", "cycles": 2},
         "cap_2": {"filter": "Ha", "exposure": 120, "count": 3}})


def pool_of(stage: str) -> FlowGraph:
    over = {"n20": {"members": "M31, M33", "quota": 12},
            "s1": {"plan": "L 60, R 60", "cycles": 2}
            if stage == "cycle" else
            {"filter": "L", "exposure": 60, "count": 4}}
    return graph([("n20", "pool", 30, 100), ("s1", stage, 300, 100)],
                 [("n20", "target", "s1", "run")], over)


def compiled(g: FlowGraph):
    return to_sequence_plan(compile_plan(g, "x"), g, flow_id="f-lane",
                            when=WHEN)


def _lane_errors(g: FlowGraph) -> list[str]:
    return [i.text for i in check(g) if i.level == "danger"
            and "node " in i.text]


# ------------------------------------------------------------ the error

def test_two_stages_with_no_wire_between_them_are_an_error_naming_both():
    errors = _lane_errors(branched_lane())
    assert len(errors) == 1, errors
    assert "cyc_a" in errors[0] and "cyc_b" in errors[0], errors


def test_the_unordered_lane_is_an_unmapped_loss_and_compiles_as_it_did():
    plan, unmapped = compiled(branched_lane())
    rows = [u for u in losses(unmapped) if u["key"] == "lane.n2"]
    assert len(rows) == 1 and rows[0]["level"] == "danger", unmapped
    assert "cyc_a" in rows[0]["detail"] and "cyc_b" in rows[0]["detail"]
    assert plan.groups == [] and len(plan.targets) == 1
    assert [(s.filter, s.phase) for s in plan.targets[0].steps] == [
        ("L", 0), ("R", 0), ("S", 0), ("Ha", 0)]


def test_a_chain_under_a_pool_is_an_error_naming_its_stages():
    errors = _lane_errors(pool_chain())
    assert len(errors) == 1, errors
    assert "cyc_1" in errors[0] and "cap_2" in errors[0], errors


def test_a_capture_loop_then_a_cycle_under_a_pool_is_the_same_error():
    g = pool_chain()
    for n in g.nodes:
        if n.id == "cyc_1":
            n.type, n.params = "capture", {
                **create_params("capture"), "filter": "Ha", "exposure": 120,
                "count": 3}
        elif n.id == "cap_2":
            n.type, n.params = "cycle", {
                **create_params("cycle"), "plan": "L 60, R 60", "cycles": 2}
    errors = _lane_errors(g)
    assert len(errors) == 1, errors
    assert "cyc_1" in errors[0] and "cap_2" in errors[0], errors


def test_a_chain_under_a_pool_is_an_unmapped_loss_and_is_not_phased():
    plan, unmapped = compiled(pool_chain())
    rows = [u for u in losses(unmapped) if u["key"] == "lane.n20"]
    assert len(rows) == 1 and rows[0]["level"] == "danger", unmapped
    assert [g.kind for g in plan.groups] == ["pool"]
    assert {s.phase for t in plan.targets for s in t.steps} == {0}


def test_a_chain_the_compile_keeps_is_no_error():
    g = graph(
        [("n2", "target", 30, 100), ("c1", "cycle", 300, 100),
         ("c2", "cycle", 600, 100)],
        [("n2", "target", "c1", "run"), ("c1", "complete", "c2", "run")],
        {"n2": TARGET, "c1": {"plan": "L 60, R 60", "cycles": 2},
         "c2": {"plan": "S 180, Ha 180", "cycles": 2}})
    assert _lane_errors(g) == []
    _plan, unmapped = compiled(g)
    assert not [u for u in unmapped if u["key"].startswith("lane.")]


# ------------------------------------------------------------ the quota note

def _quota_notes(g: FlowGraph) -> list[str]:
    return [i.text for i in check(g) if i.level == "note"
            and "quota" in i.text]


def test_a_pool_over_a_capture_loop_says_its_quota_is_not_read():
    notes = _quota_notes(pool_of("capture"))
    assert len(notes) == 1, notes
    assert "12" in notes[0] and "CAPTURE LOOP" in notes[0], notes


def test_a_pool_over_a_filter_cycle_has_its_quota_read_so_no_note():
    assert _quota_notes(pool_of("cycle")) == []


def test_a_pool_with_no_stage_says_nothing_about_a_quota():
    g = graph([("n20", "pool", 30, 100)], [], {"n20": {"members": "M31"}})
    assert _quota_notes(g) == []
