"""FILTER CYCLE: one pass per visit, many passes per night.

The night an imager actually wants is L R G B S Ha O3, forty-five times over,
not 45 L then 45 R. Every filter then samples the same sky, and a night cut
short leaves 60 percent of every channel rather than three finished filters and
four empty ones.

The node applies to the target's whole capture chain rather than to a sub-graph:
the flow model is flat, so there is no container to put a loop body inside. That
is a real limit, and a SECOND cycle node is reported rather than silently
ignored — the shape this codebase keeps finding is a control that accepts a
value and is consulted by nothing.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan

FILTERS = [("L", 60), ("R", 60), ("G", 60), ("B", 60),
           ("S", 180), ("Ha", 180), ("O3", 180)]


def _graph(cycles=45, per_pass=1, with_cycle=True):
    nodes = [FlowNode(id="t", type="target", x=0, y=0,
                      params={"name": "NGC 6946", "ra": "20h 34m 51s",
                              "dec": "+60 09 07"})]
    edges = []
    if with_cycle:
        nodes.append(FlowNode(id="cy", type="cycle", x=1, y=0,
                              params={"cycles": cycles}))
        edges.append(FlowEdge(**{"from": "t", "fromPort": "target",
                                 "to": "cy", "toPort": "run"}))
        first_src, first_port = "cy", "body"
    else:
        first_src, first_port = "t", "target"
    for i, (f, e) in enumerate(FILTERS):
        nodes.append(FlowNode(id=f"c{i}", type="capture", x=2 + i, y=0,
                              params={"filter": f, "exposure": e, "gain": 125,
                                      "bin": "1", "count": per_pass}))
    edges.append(FlowEdge(**{"from": first_src, "fromPort": first_port,
                             "to": "c0", "toPort": "run"}))
    for i in range(len(FILTERS) - 1):
        edges.append(FlowEdge(**{"from": f"c{i}", "fromPort": "complete",
                                 "to": f"c{i+1}", "toPort": "run"}))
    return FlowGraph(nodes=nodes, edges=edges)


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

    def test_a_count_above_one_is_frames_PER_PASS(self):
        """The number on the card never changes meaning for the operator: it is
        always "how many I take here before moving on". 3 per pass x 10 passes
        is 30 in the bank."""
        plan, _ = to_sequence_plan(compile_plan(_graph(10, 3), "n"))
        for s in plan.targets[0].steps:
            assert (s.per_visit, s.count) == (3, 30)


class TestWithoutTheNodeNothingChanges:
    def test_no_cycle_node_means_blocks_and_untouched_counts(self):
        plan, _ = to_sequence_plan(
            compile_plan(_graph(per_pass=12, with_cycle=False), "n"))
        t = plan.targets[0]
        assert t.acquisition == "blocks"
        assert all(s.count == 12 for s in t.steps), "counts were rewritten"
        assert all(s.per_visit == 1 for s in t.steps)

    def test_the_cycle_node_is_not_reported_as_inert(self):
        """It compiles now. Listing it as an inert node would tell the operator
        their loop does nothing, which is the opposite of the truth — and that
        list is only worth reading while every line in it is true."""
        _, un = to_sequence_plan(compile_plan(_graph(45, 1), "n"),
                                 _graph(45, 1))
        assert not [u for u in un if u["key"] == "nodes.cycle"]


class TestTheHonestLimits:
    def test_a_cycles_value_of_zero_or_junk_does_not_erase_the_night(self):
        """_num returns 0 for a garbled param. Multiplying a count by zero
        would produce a step of zero frames that reports complete having shot
        nothing — so the floor is 1 pass, which is exactly block behaviour."""
        for junk in (0, -5, "many", None):
            g = _graph(45, 1)
            for n in g.nodes:
                if n.type == "cycle":
                    n.params["cycles"] = junk
            plan, _ = to_sequence_plan(compile_plan(g, "n"))
            assert all(s.count >= 1 for s in plan.targets[0].steps), junk

    def test_every_step_still_validates_against_the_engine_model(self):
        """per_visit and acquisition are real fields, not extras that
        pydantic's extra="ignore" would drop on the floor."""
        plan, _ = to_sequence_plan(compile_plan(_graph(45, 1), "n"))
        dumped = plan.model_dump()
        t = dumped["targets"][0]
        assert t["acquisition"] == "cycle"
        assert t["steps"][0]["per_visit"] == 1
