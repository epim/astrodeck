# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The pure pieces of "Measure at the first solve" (WP 6.1, #187): the choice
itself, how it compiles, the angle fold, the angle rule, the group model and
the doctor.

Each case names the mutant it was shown RED under.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog import framing
from astrodeck.flows import RigFacts, check
from astrodeck.flows.compile import angle_code, compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import TARGET_ANGLES, create_params, target_angle
from astrodeck.flows.to_plan import _angles, to_sequence_plan
from astrodeck.sequence.group_rules import angle_decision
from astrodeck.sequence.models import SequencePlan, TargetGroup

MEASURE = "Measure at the first solve"
RA, DEC = "00h 42m 44s", "+41° 16′ 09″"


# ------------------------------------------------------------- the choice

def test_the_choice_is_the_fourth_and_is_never_a_default():
    """MUTANT "offered as the default" (created_as writes it): the second
    assertion fails."""
    assert TARGET_ANGLES == ("Any angle", "Rotate to PA", "Camera fixed at PA",
                             MEASURE)
    assert create_params("target")["angle"] == "Any angle"
    assert target_angle({"rotation": -1}) == "Any angle"
    assert target_angle({"rotation": 12}) == "Rotate to PA"
    assert target_angle({"angle": MEASURE, "rotation": -1}) == MEASURE


def _graph(rows: int = 3, cols: int = 2, **over) -> FlowGraph:
    params = {"name": "M31", "ra": RA, "dec": DEC, "rows": rows, "cols": cols,
              "overlap": 25, "fovX": 2.0, "fovY": 1.33, "rotation": -1,
              "angle": MEASURE, "counts": "Accepted subs", **over}
    nodes = [FlowNode(id="t", type="target", x=0.0, y=0.0, params=params),
             FlowNode(id="c", type="cycle", x=200.0, y=0.0,
                      params={"plan": "L 60, R 60", "cycles": 2})]
    edges = [FlowEdge(**{"from": "t", "fromPort": "target", "to": "c",
                         "toPort": "run"}),
             FlowEdge(**{"from": "c", "fromPort": "pass", "to": "t",
                         "toPort": "next"})]
    return FlowGraph(nodes=nodes, edges=edges)


def _plan(g: FlowGraph, flow_id: str = "f-measure"):
    return to_sequence_plan(compile_plan(g, "x"), g, flow_id=flow_id,
                            when=1788313689.0)[0]


def test_the_compile_reads_the_word_as_measure_not_as_a_rotation():
    """MUTANT "no ANGLE_CODES entry": an unknown word falls back to the
    rotation-derived code, so a stored rotation of 12 reads "rotate" and a
    rotator is commanded. RED: ``assert 'rotate' == 'measure'``."""
    assert angle_code({"angle": MEASURE, "rotation": 12}) == "measure"
    assert angle_code({"angle": MEASURE, "rotation": -1}) == "measure"
    entry = compile_plan(_graph(rotation=12), "x")["targets"][0]
    assert entry["angle"] == "measure"


def test_a_measure_block_is_laid_out_at_its_rotation_or_provisionally_at_0():
    grid = {"rows": 3, "cols": 2}
    assert _angles({"angle": "measure", "rotation_deg": 37.0,
                    "mosaic": grid})[1] == 37.0
    assert _angles({"angle": "measure", "rotation_deg": -1,
                    "mosaic": grid})[1] == 0.0
    assert _angles({"angle": "measure", "rotation_deg": None,
                    "mosaic": grid})[1] == 0.0
    angle, layout, commanded = _angles({"angle": "measure",
                                        "rotation_deg": 37.0, "mosaic": grid})
    assert (angle, commanded) == ("measure", None)
    # One panel has no grid to lay out: it reads as "any angle" would.
    assert _angles({"angle": "measure", "rotation_deg": -1,
                    "mosaic": None}) == ("measure", None, None)


def test_the_grid_compiles_with_no_rotator_command_and_the_group_says_so():
    """M2 does not refuse it; the group is a fixed camera that measures. MUTANT
    "M2 refuses measure" turns the first line red (GraphNotRunnable)."""
    plan = _plan(_graph(rotation=-1))
    (group,) = plan.groups
    assert group.measure_angle is True and group.node_id == "t"
    assert group.rotate is False and group.pa_deg == 0.0
    assert group.angle_tolerance_deg is not None
    assert all(t.rotation_deg is None for t in plan.targets)
    assert len(plan.targets) == 6


def test_the_group_dump_is_unchanged_unless_the_new_fields_are_set():
    """Rule 14 at the model: three optional fields, absent at their defaults
    (so a stored session and every other plan dump as they did). MUTANT
    "dump the defaults" (no pop) fails the first assertion."""
    bare = TargetGroup(id="g").model_dump(mode="json")
    assert not {"measure_angle", "node_id", "measured"} & set(bare)
    full = TargetGroup(id="g", measure_angle=True, node_id="t",
                       measured={"pa_deg": 37.0}).model_dump(mode="json")
    assert full["measure_angle"] is True and full["node_id"] == "t"
    assert full["measured"] == {"pa_deg": 37.0}
    again = SequencePlan.model_validate(SequencePlan(
        targets=[], groups=[TargetGroup(
            id="g", measure_angle=True, node_id="t",
            measured={"pa_deg": 37.0})]).model_dump(mode="json"))
    assert again.groups[0].measured == {"pa_deg": 37.0}


def test_a_flow_that_does_not_choose_it_compiles_with_none_of_the_fields():
    g = _graph(angle="Camera fixed at PA", rotation=37)
    (group,) = _plan(g).groups
    dumped = group.model_dump(mode="json")
    assert not {"measure_angle", "node_id", "measured"} & set(dumped)


# ---------------------------------------------------------------- the fold

@pytest.mark.parametrize("measured, current, want", [
    (37.0, 0.0, 37.0),           # nearest to north up
    (217.0, 37.0, 37.0),         # the half-turn reading folds back
    (37.0, 217.0, 217.0),        # and a layout at 217 keeps 217
    (150.0, 0.0, 330.0),         # 150 is the same footprint as -30
    (359.5, 0.0, 359.5),
    (180.0, 0.0, 0.0),           # equidistant: the lower representative
    (-10.0, 0.0, 350.0),
])
def test_the_fold_picks_the_representative_nearest_the_current_layout(
        measured, current, want):
    """MUTANT "no fold" (returns the reading mod 360): (217, 37) comes back
    217 and every label of the grid swaps. RED: ``217.0 == 37.0``."""
    got = framing.fold_measured_angle(measured, current)
    assert got == pytest.approx(want)
    assert 0.0 <= got < 360.0


# ------------------------------------------------------------ angle_decision

def _decide(kind: str, **kw):
    base = dict(rotate=False, angle_verified=False, rotator_evidence=False,
                shoot_anyway=False, single_panel=False)
    base.update(kw)
    return angle_decision(kind, **base)


@pytest.mark.parametrize("kind", ["ok", "off"])
def test_a_pending_measurement_relays_whatever_the_reading_is(kind):
    """MUTANT "off still sets the group aside while pending": the ``off``
    case answers set_group_aside. RED: ``'set_group_aside' == 'relay'``."""
    assert _decide(kind, measure_pending=True).action == "relay"


def test_a_pending_measurement_with_no_reading_defers_never_lays_blind():
    assert _decide("no_measurement", measure_pending=True).action == "defer"
    assert _decide("no_measurement", measure_pending=True,
                   shoot_anyway=True).action == "defer"


def test_without_a_pending_measurement_every_answer_is_what_it_was():
    assert _decide("off").action == "set_group_aside"
    assert _decide("ok").action == "shoot"
    assert _decide("no_measurement").action == "defer"
    assert _decide("off", measure_pending=False).action == "set_group_aside"


# ------------------------------------------------------------------- doctor

def _texts(g: FlowGraph, **kw):
    return [(i.level, i.text) for i in check(g, **kw)]


def test_m2_is_silent_for_a_measure_grid_and_the_doctor_says_what_it_does():
    """MUTANT "M2 reads measure as any angle": the danger line appears."""
    got = _texts(_graph(rotation=-1))
    assert not [t for _l, t in got if "laid out at one camera angle" in t]
    note = [(lv, t) for lv, t in got if "angle the camera reads" in t]
    assert len(note) == 1 and note[0][0] == "note", got


def test_a_one_panel_measure_block_is_told_it_keeps_its_first_angle_anyway():
    got = _texts(_graph(rows=1, cols=1, rotation=-1))
    one = [t for _l, t in got if "one panel" in t and "first solve" in t]
    assert len(one) == 1, got
    assert not [t for _l, t in got if "laid out at one camera angle" in t]


def test_a_rig_with_a_rotator_is_told_this_choice_never_turns_it():
    rig = RigFacts(fov_deg=(2.0, 1.33), has_rotator=True,
                   reject_guards_off=False, hop_cost_s=30.0, hop_samples=5)
    got = _texts(_graph(rotation=-1), rig=rig)
    assert [t for _l, t in got if "never turns it" in t], got
    no_rotator = RigFacts(fov_deg=(2.0, 1.33), has_rotator=False,
                          reject_guards_off=False, hop_cost_s=30.0,
                          hop_samples=5)
    got = _texts(_graph(rotation=-1), rig=no_rotator)
    assert not [t for _l, t in got if "never turns it" in t], got
    assert not [t for _l, t in got if "by hand to PA" in t], got


# ------------------------------------------------------------------- the brief

def test_the_brief_says_a_measuring_grid_is_laid_out_at_what_the_solve_reads():
    """MUTANT "no branch": the grid is said to be at no set angle and unable
    to run. RED: the first assertion."""
    from test_flows_campaign_brief import _mosaic, brief
    b = brief(_mosaic(angle=MEASURE))
    assert ("laid out at the angle its camera reads on the first centring "
            "solve of each night, the camera fixed and never turned by the "
            "run.") in b, b
    assert "cannot run" not in b and "PA -1" not in b, b
