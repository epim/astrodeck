# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-19: lanes scoped by wires in ordinary multi-target flows (#151's
general case), and one definition of a pool member's "done" (#155).

(a) ``needs_wire_scoping`` (compile.py) used to turn the wire-scoped
owner_of/panel_lane rule on only for a graph with a multi-panel TARGET. A
graph with two or more ordinary TARGET/POOL blocks and no mosaic at all
leaked the same way under the canvas-order rule: a capture or cycle stage
went to every target seen earlier in the flow-order walk, so which filters a
target shoots depended on where its card sat rather than which wires led to
it. This file tests the general case directly; the mosaic case already has
its own suite (test_flows_panel_lane.py).

``tonight._receivers`` (the brief's own prediction of which block a stage is
shot for) duplicated the pre-fix condition too, and so disagreed with
``compile_plan`` on exactly the graphs this file is about; it now reads
``needs_wire_scoping`` as well, which this file also covers.

(b) ``compile_plan`` writes a POOL member's ``quota`` onto its entry as
``quota_cycles``, and nothing used to read it: the engine ran a member's
FILTER CYCLE to the stage's own ``cycles`` count while the Campaign tab
(``tonight._campaign``) judged the same member against the pool's ``quota``
by name — two numbers, nothing reconciling them. ``to_plan._cycle_steps``
now maps a valid ``quota_cycles`` onto the step's own ``cycles``, so the
number the engine stops at and the number the Campaign tab reads are the
same value by construction.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan, needs_wire_scoping
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import _campaign, _receivers


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _steps_by_block(plan: dict) -> dict[str, list[str]]:
    return {t["node_id"]: [s["node_id"] for s in t["steps"]]
            for t in plan["targets"]}


# --------------------------------------------------------- (a) wire scoping

#: Two independent lanes (no mosaic anywhere), laid out so BOTH TARGET nodes
#: have no flow parent and so are released into the walk before either
#: CAPTURE is: under the old canvas-order rule every capture is appended to
#: "every target seen so far", which by the time either capture is reached
#: is both of them.
def _two_lane_graph() -> FlowGraph:
    return FlowGraph(
        nodes=[_n("a", "target", x=0, name="M31",
                  ra="00h 42m 44s", dec="+41 16 09"),
               _n("b", "target", x=10, name="M33",
                  ra="01h 33m 50s", dec="+30 39 37"),
               _n("ca", "capture", x=20, filter="L", exposure=60, count=10),
               _n("cb", "capture", x=30, filter="R", exposure=60, count=5)],
        edges=[_e("a", "target", "ca", "run"), _e("b", "target", "cb", "run")])


class TestWireScopingInOrdinaryMultiTargetFlows:
    def test_two_independent_lanes_do_not_leak_into_each_other(self):
        """#151's own worked example, with ordinary 1x1 TARGETs: A's capture
        must stay A's and B's must stay B's, regardless of which target's
        card the walk reaches first.

        RED under mutant "scope by mosaics alone" (``needs_wire_scoping``
        back to ``any(is_multi_panel(n) for n in graph.nodes)``):
            AssertionError: assert ['ca', 'cb'] == ['ca']
        """
        plan = compile_plan(_two_lane_graph(), "n")
        by = _steps_by_block(plan)
        assert by["a"] == ["ca"], by
        assert by["b"] == ["cb"], by

    def test_moving_the_other_target_s_card_changes_nothing(self):
        """The whole point of #151: compiling twice with B's card moved must
        give A the same steps both times. Three positions of B, each either
        side of both captures in canvas x.

        RED under mutant "scope by mosaics alone": the three results differ
        from each other (and from ``["ca"]``) as B's card crosses each
        capture's x, which is exactly the "depends on where its card sits"
        bug #151 reports.
        """
        results = []
        for bx in (-50, 15, 500):
            g = _two_lane_graph().model_copy(update={"nodes": [
                FlowNode(id="b", type="target", x=bx, y=0.0,
                        params={"name": "M33", "ra": "01h 33m 50s",
                                "dec": "+30 39 37"})
                if n.id == "b" else n
                for n in _two_lane_graph().nodes]})
            by = _steps_by_block(compile_plan(g, "n"))
            results.append(by["a"])
        assert results == [["ca"]] * 3, results

    def test_a_single_target_flow_is_untouched(self):
        """Control: with at most one TARGET/POOL block there is nothing else
        a stage could leak onto, so the switch stays off and the compile
        carries no notes - the premise every Example and every flow saved
        before this fix relies on."""
        g = FlowGraph(
            nodes=[_n("a", "target", x=0, name="M31",
                      ra="00h 42m 44s", dec="+41 16 09"),
                   _n("ca", "capture", x=20, filter="L", exposure=60, count=10)],
            edges=[_e("a", "target", "ca", "run")])
        assert needs_wire_scoping(g) is False
        plan = compile_plan(g, "n")
        assert _steps_by_block(plan) == {"a": ["ca"]}
        assert "notes" not in plan

    def test_needs_wire_scoping_is_the_premise_the_two_tests_above_rely_on(self):
        """Direct unit cover of the predicate itself, so a failure in the two
        behavioural tests above can be told apart from a failure of the
        predicate's own logic."""
        assert needs_wire_scoping(_two_lane_graph()) is True

    def test_the_brief_s_own_receivers_agree_with_the_compile(self):
        """``tonight._receivers`` answers the compile's own question from the
        graph alone (for the brief's sentences), and it duplicated the
        pre-fix mosaic-only condition - so on exactly this shape it used to
        say ``('a', 'b')`` for ``cb`` where the compile said ``('b',)``.

        RED under mutant "the brief's own canvas-order condition"
        (``tonight._receivers``' ``scoped = needs_wire_scoping(g)`` put back
        to ``mosaic = any(is_multi_panel(n) for n in g.nodes)`` and the loop
        reading ``if not mosaic:``):
            AssertionError: {'ca': ('a', 'b'), 'cb': ('a', 'b')}
            assert {'ca': ('a', ...': ('a', 'b')} == {'ca': ('a',), 'cb':
            ('b',)}
        """
        g = _two_lane_graph()
        said = {sid: tuple(n.id for n in blocks)
                for sid, blocks in _receivers(g).items()}
        assert said == {"ca": ("a",), "cb": ("b",)}, said


# --------------------------------------------------- (b) one pool quota

def _pool_cycle_graph(quota=3, cycles=45, per_cycle=1) -> FlowGraph:
    return FlowGraph(
        nodes=[_n("p", "pool", members="M16", quota=quota),
               _n("cy", "cycle", x=100, plan="L 10", cycles=cycles,
                  perCycle=per_cycle)],
        edges=[_e("p", "target", "cy", "run")])


class TestThePoolQuotaGovernsTheCycleStepsTheEngineRuns:
    def test_the_member_s_step_count_reflects_the_quota_not_the_cycle_node(self):
        """#155's own test: a pool with quota 3 feeding a FILTER CYCLE of 45
        cycles. The member's compiled step must stop at 3 cycles, not 45.

        RED under mutant "quota_cycles never overrides" (``_cycle_steps``'s
        ``cycles`` line put back to ``max(1, int(step.get("cycles") or
        1)))``, ignoring the ``quota_cycles`` argument):
            AssertionError: assert 45 == 3
        """
        plan, _unmapped = to_sequence_plan(
            compile_plan(_pool_cycle_graph(quota=3, cycles=45, per_cycle=1), "n"))
        (member,) = plan.targets
        (step,) = member.steps
        assert step.count == 3
        assert step.per_visit == 1

    def test_the_campaign_tab_and_the_engine_read_one_number(self):
        """The Campaign tab (``tonight._campaign``) must read the same
        number the engine stops at - the issue's own complaint was that the
        pool's quota changed the tab's verdict and nothing about when the
        night actually moved on.

        RED under the same mutant as above: the engine's step holds 45,
        the tab's ``quota`` holds 3, and the assertion that they agree fails:
            AssertionError: assert 45 == 3
        """
        g = _pool_cycle_graph(quota=3, cycles=45, per_cycle=1)
        plan, _unmapped = to_sequence_plan(compile_plan(g, "n"))
        (step,) = plan.targets[0].steps
        campaign = _campaign(g, frames_by_target=None)
        assert campaign["quota"] == step.count

    def test_an_unreadable_quota_leaves_the_cycle_node_s_own_count(self):
        """Zero, negative and non-finite quotas are refused, not clamped
        (the #362 class): a quota of 0 is "nothing was ever read", not
        "stop immediately", so the FILTER CYCLE's own count keeps
        governing, exactly as a flow with no pool at all."""
        for bad in (0, -1, float("nan"), float("inf")):
            plan, _unmapped = to_sequence_plan(
                compile_plan(_pool_cycle_graph(quota=bad, cycles=45,
                                               per_cycle=1), "n"))
            (step,) = plan.targets[0].steps
            assert step.count == 45, (bad, step.count)

    def test_an_ordinary_target_s_cycle_step_is_unaffected(self):
        """Control: an entry with no ``quota_cycles`` key at all (every
        ordinary TARGET) must compile exactly as it always has."""
        g = FlowGraph(
            nodes=[_n("t", "target", x=0, name="M31",
                      ra="00h 42m 44s", dec="+41 16 09"),
                   _n("cy", "cycle", x=100, plan="L 10", cycles=45,
                      perCycle=1)],
            edges=[_e("t", "target", "cy", "run")])
        plan, _unmapped = to_sequence_plan(compile_plan(g, "n"))
        (step,) = plan.targets[0].steps
        assert step.count == 45
