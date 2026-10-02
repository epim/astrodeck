# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight counts a panel's visits ROUNDED UP when a visit's passes do not
divide its rounds (#338, S4 orchestrator ruling 5; spec 5.3, 5.5).

`tonight._visits_per_panel` prices a rotating block's hops at
``ceil(rounds / visit_passes)`` visits a panel, the engine's own rule and the
Target modal's (``readouts``' ``visits_per_panel``, ``-(-rounds // bound)``):
the last, short visit is a visit too, with a hop before it.

THE GAP THIS FILE CLOSES. Every case of test_flows_tonight_cycle_budget.py
and test_flows_tonight_mosaic.py uses a round count its visit divides
exactly (45 cycles at one pass or three a visit; Ha x 10 at one or two), so
floor and ceiling agree on all of them. Mutant "visits rounded down"
(``-(-rounds // _visit_passes(entry))`` made ``rounds //
_visit_passes(entry)``) passed every test in the S4 set (1867 passed), and
under it a budget prices one hop a panel fewer than the run makes and the
modal prints. Found by the S4 tests-that-cannot-fail review (#414).

The flow is test_flows_tonight_cycle_budget.py's, on its synthetic site
(40 N 105 W, not the observatory's) with its hub pinned there and its panel
stamp stood in for: nothing here is about altitude.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim, run in a private scratch copy of server/ (scratchpad
s4-tcf-review-mut2), from a byte backup restored and compared by SHA-256,
never in the shared tree (#254).
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.readouts import readouts
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import to_sequence_plan

from test_flows_tonight_cycle_budget import (  # noqa: F401 (autouse fixture)
    FLOW, _mosaic, _tonight, synthetic_hub_and_no_ephemeris)

HOP_S = 160.0


def _modal(graph) -> dict:
    """The Target modal's readouts block for the flow's one TARGET."""
    compiled = compile_plan(graph, "n")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
    (block,) = readouts(compiled, plan,
                        RigFacts(hop_cost_s=HOP_S, hop_samples=3)).values()
    return block


def test_a_short_last_visit_is_a_visit_and_its_hop_is_priced():
    """The default cycle (45 cycles, a 13-minute pass) on a 2x2 at one pass
    a visit and a 26 min minimum: a visit is two passes, so a panel takes 23
    visits (22 of two passes and a last of one), 92 in all, 4.09 h of hops
    at 160 s: the modal's ``visits_total`` for the same plan.

    Mutant "visits rounded down" (see the module docstring): RED (observed) -
        AssertionError: the budget prices 3.91 h of hops; the modal's 92
        visits at 160 s are 4.09 h
    """
    graph = _mosaic([("y", "cycle", {})], min_visit=26)
    block = _modal(graph)
    assert (block["pass_s"], block["visit_passes"], block["visits_per_panel"],
            block["visits_total"]) == (780.0, 2, 23, 92), (
        "premise: a 13 minute pass, two a visit, 45 rounds make 23 visits a "
        f"panel, 92 in all: {block}")
    (row,) = _tonight(graph, hop_cost_s=HOP_S)["budget"]
    want = round(block["visits_total"] * HOP_S / 3600, 2)
    assert row["hop_h"] == want, (
        f"the budget prices {row['hop_h']} h of hops; the modal's "
        f"{block['visits_total']} visits at 160 s are {want} h")


def test_control_a_visit_that_divides_the_rounds_is_unchanged():
    """CONTROL: a 30 min minimum makes three passes a visit, which divides
    the 45 rounds, so floor and ceiling agree: 15 visits a panel, 60 in all,
    2.67 h. Green on the code and under the mutant above, which is what
    makes the case above a test of the rounding."""
    graph = _mosaic([("y", "cycle", {})], min_visit=30)
    block = _modal(graph)
    assert (block["visit_passes"], block["visits_total"]) == (3, 60), block
    (row,) = _tonight(graph, hop_cost_s=HOP_S)["budget"]
    assert row["hop_h"] == round(60 * HOP_S / 3600, 2) == 2.67
