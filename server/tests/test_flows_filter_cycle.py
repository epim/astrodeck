"""FILTER CYCLE: one sub per filter per pass, many passes per night.

The night an imager actually wants is L R G B S Ha O3, forty-five times over,
not 45 L then 45 R. Every filter then samples the same sky, and a night cut
short leaves 60 percent of every channel rather than three finished filters and
four empty ones.

THE 2026-08-14 EXPORT RESHAPED THIS NODE, and the earlier reading is worth
recording because it was wrong in an instructive way. It was built as a LOOP
CONTAINER - a `body` port feeding a chain of CAPTURE nodes, with the container
multiplying their counts. The export settles it the other way:

    "no loop construct exists at graph level (the graph stays acyclic - loops
     live inside stages, and campaign loops are event wires)"

So FILTER CYCLE is a CAPTURE STAGE that happens to interleave, carrying its own
slot table. That removes a whole class of question the container shape invited
(what does a second cycle mean, what if the body forks, what if a wire leaves
the loop) by never letting the graph express it.

The seam under test is two hops: the graph compiles to ONE step object holding
the slot table, and `to_plan` expands that into one engine ExposureStep per
filter plus `acquisition="cycle"` on the target.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import parse_cycle_plan
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan

#: The user's night, in the node's own storage format.
PLAN = "L 60, R 60, G 60, B 60, S 180, Ha 180, O3 180"
FILTERS = [("L", 60), ("R", 60), ("G", 60), ("B", 60),
           ("S", 180), ("Ha", 180), ("O3", 180)]


def _graph(cycles=45, per_cycle=1, plan=PLAN, capture_instead=False):
    """A target feeding one capture stage. `capture_instead` swaps the FILTER
    CYCLE for a plain CAPTURE LOOP, which is the control case."""
    nodes = [FlowNode(id="t", type="target", x=0, y=0,
                      params={"name": "NGC 6946", "ra": "20h 34m 51s",
                              "dec": "+60 09 07"})]
    if capture_instead:
        nodes.append(FlowNode(id="cy", type="capture", x=1, y=0,
                              params={"filter": "L", "exposure": 60,
                                      "gain": 125, "bin": "1", "count": 12}))
    else:
        nodes.append(FlowNode(id="cy", type="cycle", x=1, y=0,
                              params={"plan": plan, "cycles": cycles,
                                      "perCycle": per_cycle, "gain": 125,
                                      "bin": "1", "reject": 3.5}))
    return FlowGraph(nodes=nodes, edges=[
        FlowEdge(**{"from": "t", "fromPort": "target",
                    "to": "cy", "toPort": "run"})])


class TestTheSlotTable:
    def test_the_stored_string_decodes_to_filters_and_seconds(self):
        assert parse_cycle_plan(PLAN) == FILTERS

    def test_a_slot_nobody_can_read_is_dropped_not_guessed(self):
        """Inventing an exposure for an unparseable slot would put frames on
        disk under a filter the operator never asked for."""
        assert parse_cycle_plan("L 60, , Ha, R 90") == [("L", 60), ("R", 90)]

    def test_wheel_order_is_preserved(self):
        """The row order IS the shooting order, so a parser that sorted or
        de-duplicated would quietly re-plan the night."""
        assert [f for f, _ in parse_cycle_plan("Ha 180, L 60, Ha 300")] == \
            ["Ha", "L", "Ha"]


class TestTheCompileKeepsTheStageWhole:
    def test_one_step_object_carries_the_whole_table(self):
        """Seven separate steps at compile time would already have said "45 L,
        then 45 R" - the arrangement this node exists to avoid. The interleaving
        belongs to the STAGE, so the stage stays one thing."""
        steps = compile_plan(_graph(), "n")["targets"][0]["steps"]
        assert len(steps) == 1
        step = steps[0]
        assert step["strategy"] == "cycle"
        assert step["cycles"] == 45 and step["per_cycle"] == 1
        assert [(s["filter"], s["exposure_s"]) for s in step["slots"]] == FILTERS

    def test_each_target_gets_its_own_copy_of_the_slots(self):
        """A shared list would make one target's edit rewrite every other's."""
        g = _graph()
        g.nodes.insert(1, FlowNode(id="t2", type="target", x=0, y=1,
                                   params={"name": "M33", "ra": "01h 33m 51s",
                                           "dec": "+30 39 37"}))
        g.edges.append(FlowEdge(**{"from": "t2", "fromPort": "target",
                                   "to": "cy", "toPort": "run"}))
        targets = compile_plan(g, "n")["targets"]
        assert len(targets) == 2
        a, b = (t["steps"][0]["slots"] for t in targets)
        assert a == b and a is not b


class TestTheUsersNight:
    def test_45_cycles_of_one_gives_45_subs_of_every_filter(self):
        plan, _ = to_sequence_plan(compile_plan(_graph(45, 1), "n"))
        t = plan.targets[0]
        assert t.acquisition == "cycle"
        assert [s.filter for s in t.steps] == [f for f, _ in FILTERS]
        assert all(s.count == 45 and s.per_visit == 1 for s in t.steps)
        assert sum(s.count for s in t.steps) == 315

    def test_the_per_filter_exposures_are_kept(self):
        """60s on the broadband four, 180s on the narrowband three."""
        plan, _ = to_sequence_plan(compile_plan(_graph(45, 1), "n"))
        got = {s.filter: s.exposure_s for s in plan.targets[0].steps}
        assert got == {"L": 60, "R": 60, "G": 60, "B": 60,
                       "S": 180, "Ha": 180, "O3": 180}

    def test_subs_per_filter_per_pass_above_one(self):
        """The node's own wording: "subs per filter per pass". 3 per pass x 10
        passes is 30 in the bank, and the pass still visits every filter."""
        plan, _ = to_sequence_plan(compile_plan(_graph(10, 3), "n"))
        for s in plan.targets[0].steps:
            assert (s.per_visit, s.count) == (3, 30)

    def test_the_gain_and_binning_are_the_stages_not_the_slots(self):
        """One camera setting for the whole cycle. A slot carries a filter and
        an exposure; anything else on it would be a second place to set gain."""
        plan, _ = to_sequence_plan(compile_plan(_graph(), "n"))
        assert all(s.gain == 125 and s.binning == 1
                   for s in plan.targets[0].steps)


class TestWithoutTheNodeNothingChanges:
    def test_a_plain_capture_loop_still_shoots_in_blocks(self):
        plan, _ = to_sequence_plan(
            compile_plan(_graph(capture_instead=True), "n"))
        t = plan.targets[0]
        assert t.acquisition == "blocks"
        assert all(s.count == 12 for s in t.steps), "counts were rewritten"
        assert all(s.per_visit == 1 for s in t.steps)

    def test_the_cycle_node_is_not_reported_as_inert(self):
        """It compiles now. Listing it as an inert node would tell the operator
        their cycle does nothing, which is the opposite of the truth - and that
        list is only worth reading while every line in it is true."""
        g = _graph(45, 1)
        _, un = to_sequence_plan(compile_plan(g, "n"), g)
        assert not [u for u in un if u["key"] == "nodes.cycle"]


class TestTheHonestLimits:
    @pytest.mark.parametrize("junk", [0, -5, "many", None])
    def test_a_cycles_value_of_zero_or_junk_does_not_erase_the_night(self, junk):
        """A garbled param reads as 0. Multiplying by it would produce a step of
        zero frames that reports complete having shot nothing, so the floor is
        one pass - which is exactly block behaviour, the safe direction."""
        g = _graph()
        next(n for n in g.nodes if n.type == "cycle").params["cycles"] = junk
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert all(s.count >= 1 for s in plan.targets[0].steps), junk

    def test_an_empty_slot_table_is_refused_not_silently_dropped(self):
        """A cycle with nothing ticked would compile to a target with no steps -
        a plan with no frames, from a graph showing a fully configured capture
        stage. The refusal names the fix."""
        with pytest.raises(GraphNotRunnable, match="no filters selected"):
            to_sequence_plan(compile_plan(_graph(plan=""), "n"))

    def test_a_slot_with_no_exposure_is_refused(self):
        with pytest.raises(GraphNotRunnable, match="exposure"):
            to_sequence_plan(compile_plan(_graph(plan="L 0"), "n"))

    def test_every_step_still_validates_against_the_engine_model(self):
        """per_visit and acquisition are real fields, not extras that
        pydantic's extra="ignore" would drop on the floor."""
        plan, _ = to_sequence_plan(compile_plan(_graph(45, 1), "n"))
        dumped = plan.model_dump()
        t = dumped["targets"][0]
        assert t["acquisition"] == "cycle"
        assert t["steps"][0]["per_visit"] == 1
