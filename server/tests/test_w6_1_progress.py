# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The run-mode line's source (WP 6.1, #187): ``measured_angle`` on a mosaic
block of the progress answer, read off the group of the plan the session
froze. Numbers and a night key only, present only where the relay has run.

Each case names the mutant it was shown RED under.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.session import Session

FLOW = "f-progress"
MEASURE = "Measure at the first solve"
KEYS = {"pa_deg", "from_pa_deg", "night", "carried", "max_move_deg",
        "threshold_deg", "orphaned_frames"}
WHEN = 1788313689.0


def _graph(angle: str = MEASURE, rotation: float = 10) -> FlowGraph:
    return FlowGraph.model_validate({
        "nodes": [
            {"id": "t", "type": "target", "x": 0, "y": 0, "params": {
                "name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28",
                "rows": 1, "cols": 2, "overlap": 25, "fovX": 1.0,
                "fovY": 0.7, "angle": angle, "rotation": rotation}},
            {"id": "c", "type": "capture", "x": 200, "y": 0,
             "params": {"filter": "L", "exposure": 60, "count": 2}}],
        "edges": [{"from": "t", "fromPort": "target", "to": "c",
                   "toPort": "run"}]})


def _answer(graph: FlowGraph, record: dict | None):
    compiled = compile_plan(graph, "x")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW, when=WHEN)
    frozen, _ = to_sequence_plan(compiled, graph, flow_id=FLOW, when=WHEN)
    if record is not None:
        frozen.groups[0].measured = record
    session = Session(name="s", status="dormant", plan=frozen,
                      origin="flow", origin_id=FLOW)
    return flow_progress(compiled, plan, session, flow_id=FLOW)["blocks"][0]


RECORD = {"pa_deg": 37.0, "from_pa_deg": 30.0, "night": "2026-11-08",
          "carried": False, "max_move_deg": 0.26, "threshold_deg": 0.17,
          "reason": "move", "orphaned_frames": 4}


def test_the_block_carries_what_the_relay_did_and_nothing_else():
    """MUTANT "never read": the key is absent. RED: ``KeyError``."""
    block = _answer(_graph(), RECORD)
    assert set(block["measured_angle"]) == KEYS
    assert block["measured_angle"]["pa_deg"] == 37.0
    assert block["measured_angle"]["carried"] is False
    assert block["measured_angle"]["orphaned_frames"] == 4
    assert "reason" not in block["measured_angle"]


def test_no_record_and_no_measuring_block_means_no_key():
    assert "measured_angle" not in _answer(_graph(), None)
    assert "measured_angle" not in _answer(
        _graph("Camera fixed at PA", 30), RECORD)


def test_a_record_whose_angle_is_no_number_is_no_record():
    assert "measured_angle" not in _answer(
        _graph(), {**RECORD, "pa_deg": float("nan")})
    assert "measured_angle" not in _answer(
        _graph(), {**RECORD, "pa_deg": True})


def test_a_carried_record_says_so():
    block = _answer(_graph(), {**RECORD, "carried": True,
                               "orphaned_frames": 0})
    assert block["measured_angle"]["carried"] is True
