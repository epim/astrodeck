"""Graph → plan. The function that decides what a flow actually runs.

The README makes the compile load-bearing twice over: the PLAN tab shows this
output verbatim, and the Tonight timeline is "a rendering of the compile, never
a separate truth". So a compile that could give two answers for one graph would
let the timeline and the run disagree with neither being wrong — which is why
determinism gets its own test here rather than being assumed.

The five Examples are the acceptance corpus again: each must compile to
something the engine could actually be handed.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan, flow_order
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


class TestFlowOrder:
    def test_it_follows_the_wires(self):
        g = FlowGraph(
            nodes=[_n("d", "dusk"), _n("t", "target"), _n("s", "slew"),
                   _n("c", "capture")],
            edges=[_e("d", "window", "t", "arm"), _e("t", "target", "s", "run"),
                   _e("s", "centered", "c", "run")])
        assert [n.id for n in flow_order(g)] == ["d", "t", "s", "c"]

    def test_pure_event_nodes_are_not_on_the_cursors_path(self):
        """A cloud watcher is not somewhere the run cursor goes. Including it
        would put a rule in the middle of the sequence."""
        g = FlowGraph(nodes=[_n("d", "dusk"), _n("cw", "cloudwatch"),
                             _n("nf", "notify")])
        assert [n.id for n in flow_order(g)] == ["d"]

    def test_independent_stages_come_out_in_SCREEN_order(self):
        """Two stages with no ordering between them must compile in the order
        the operator reads them, not in whatever order the dict iterated."""
        g = FlowGraph(nodes=[_n("b", "target", x=500), _n("a", "target", x=30)])
        assert [n.id for n in flow_order(g)] == ["a", "b"]

    def test_a_cycle_does_not_hang(self):
        """Not expressible in the editor, so a graph containing one came from
        somewhere else; dropping the cycle is the fail-closed reading."""
        g = FlowGraph(nodes=[_n("a", "slew"), _n("b", "autofocus")],
                      edges=[_e("a", "centered", "b", "run"),
                             _e("b", "focused", "a", "run")])
        assert flow_order(g) == []          # returns, does not spin


class TestCompile:
    def test_a_target_and_a_capture_become_a_step(self):
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h", dec="+41"),
                   _n("c", "capture", x=100, filter="Ha", exposure=180, gain=100,
                      bin="1", count=20, goal=0)],
            edges=[_e("t", "target", "c", "run")])
        plan = compile_plan(g, "n")
        assert len(plan["targets"]) == 1
        step = plan["targets"][0]["steps"][0]
        assert step == {"filter": "Ha", "exposure_s": 180, "gain": 100,
                        "binning": 1, "count": 20, "frame_type": "Light"}

    def test_an_integration_goal_rides_along_only_when_set(self):
        """`goal` is hours of banked integration across nights, and 0 means "no
        goal" — the EAA example sets it to 0 deliberately. A key that appears
        with the value 0 would read as a goal of zero hours, which is a
        different (and unmeetable) claim from having none."""
        def step_of(goal):
            g = FlowGraph(nodes=[_n("t", "target"), _n("c", "capture", x=100, goal=goal)],
                          edges=[_e("t", "target", "c", "run")])
            return compile_plan(g, "n")["targets"][0]["steps"][0]
        assert step_of(12)["integration_goal_h"] == 12
        assert "integration_goal_h" not in step_of(0)

    def test_a_pool_expands_to_one_target_per_candidate(self):
        g = FlowGraph(nodes=[_n("p", "pool", members="M16, M17, M8",
                                minAlt=30, moonSep=40, maxHA=4)])
        plan = compile_plan(g, "n")
        assert [t["name"] for t in plan["targets"]] == ["M16", "M17", "M8"]
        assert [t["pool_rank"] for t in plan["targets"]] == [1, 2, 3]
        assert plan["targets"][0]["min_moon_sep_deg"] == 40

    def test_a_capture_after_a_pool_applies_to_every_candidate(self):
        """The only reading under which 'best available' can substitute one
        candidate for another mid-night: they are all shot the same way."""
        g = FlowGraph(
            nodes=[_n("p", "pool", members="A, B"),
                   _n("c", "capture", x=100, filter="Ha", count=10)],
            edges=[_e("p", "target", "c", "run")])
        plan = compile_plan(g, "n")
        assert all(len(t["steps"]) == 1 for t in plan["targets"])
        assert len(plan["targets"]) == 2

    def test_each_target_gets_its_OWN_step_dict(self):
        """Shared dicts would make one target's later edit rewrite them all."""
        g = FlowGraph(nodes=[_n("p", "pool", members="A, B"),
                             _n("c", "capture", x=100, count=5)],
                      edges=[_e("p", "target", "c", "run")])
        plan = compile_plan(g, "n")
        a, b = plan["targets"]
        a["steps"][0]["count"] = 999
        assert b["steps"][0]["count"] == 5

    def test_no_dusk_node_means_run_now_not_never(self):
        """A flow with no window is one the operator starts by hand — the EAA
        example exactly."""
        plan = compile_plan(FlowGraph(nodes=[_n("t", "target")]), "n")
        assert plan["schedule"] == {"start_mode": "now"}

    def test_a_dusk_node_becomes_a_schedule(self):
        g = FlowGraph(nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30)])
        assert compile_plan(g, "n")["schedule"] == {
            "start_mode": "dusk", "start_offset_min": -30,
            "stop_mode": "dawn", "min_altitude_deg": 30}

    def test_stop_none_is_honoured(self):
        g = FlowGraph(nodes=[_n("d", "dusk", stop="None")])
        assert compile_plan(g, "n")["schedule"]["stop_mode"] == "none"


class TestInstructions:
    def test_the_two_additive_cloud_triggers(self):
        """README: 'New TriggerKinds are additive to the existing closed enum:
        on_clouds_in, on_clouds_clear'. This is the only place they are minted."""
        g = FlowGraph(
            nodes=[_n("cw", "cloudwatch"), _n("h", "holdresume")],
            edges=[_e("cw", "in", "h", "pause"), _e("cw", "clear", "h", "resume")])
        whens = [i["when"] for i in compile_plan(g, "n")["instructions"]]
        assert set(whens) == {"on_clouds_in", "on_clouds_clear"}

    def test_a_condition_names_its_predicate_and_carries_its_threshold(self):
        g = FlowGraph(
            nodes=[_n("k", "condition", when="HFR above", threshold=3.2),
                   _n("r", "refocus")],
            edges=[_e("k", "fire", "r", "do")])
        rule = compile_plan(g, "n")["instructions"][0]
        assert rule == {"when": "on_hfr_above", "action": "refocus", "threshold": 3.2}

    def test_safety_and_capture_map_to_their_situations(self):
        g = FlowGraph(
            nodes=[_n("s", "safety"), _n("a", "abort"),
                   _n("c", "capture"), _n("k", "condition")],
            edges=[_e("s", "unsafe", "a", "do"), _e("c", "frame", "k", "events")])
        whens = {i["when"] for i in compile_plan(g, "n")["instructions"]}
        assert whens == {"on_unsafe", "on_frame_graded"}

    def test_flow_edges_do_not_become_instructions(self):
        """The two lanes mean different things; a 'then' is not a 'whenever'."""
        g = FlowGraph(nodes=[_n("d", "dusk"), _n("t", "target")],
                      edges=[_e("d", "window", "t", "arm")])
        assert compile_plan(g, "n")["instructions"] == []


class TestAutomation:
    def test_a_dome_is_always_fail_closed(self):
        g = FlowGraph(nodes=[_n("d", "dome")])
        assert compile_plan(g, "n")["automation"]["dome"] == {
            "slave": True, "on_unsafe": "close"}

    def test_the_queue_states_its_order_and_that_flats_need_a_panel(self):
        g = FlowGraph(nodes=[_n("q", "calib", quota=20)])
        cq = compile_plan(g, "n")["automation"]["calibration_queue"]
        assert cq["order"] == ["dark", "bias", "flat"]
        assert cq["quota"] == 20 and cq["flats_require_panel"] is True

    def test_absent_equipment_leaves_the_key_out_entirely(self):
        """Not `None` — a key that is present and null reads as 'configured to
        nothing', which is a different claim from 'not in this flow'."""
        auto = compile_plan(FlowGraph(nodes=[_n("t", "target")]), "n")["automation"]
        assert "dome" not in auto and "dusk_flats" not in auto


class TestTheExamplesCompile:
    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_each_example_compiles_to_something_runnable(self, ex):
        plan = compile_plan(ex.graph, ex.name)
        assert plan["name"] == ex.name
        assert plan["targets"], f"{ex.name} compiled to no targets"
        for t in plan["targets"]:
            assert t["steps"], f"{ex.name}: target {t['name']} has no steps"

    def test_the_compile_is_deterministic(self):
        """The timeline is a rendering of THIS; two answers would let the
        timeline and the run disagree with neither being wrong."""
        for ex in examples():
            assert compile_plan(ex.graph, ex.name) == compile_plan(ex.graph, ex.name)

    def test_m16_compiles_the_whole_cloud_dodge(self):
        m16 = next(e for e in examples() if e.id == "example-m16")
        plan = compile_plan(m16.graph, m16.name)
        rules = {(i["when"], i["action"]) for i in plan["instructions"]}
        for leg in [("on_clouds_in", "holdresume"), ("on_clouds_in", "calib"),
                    ("on_clouds_in", "notify"), ("on_clouds_clear", "holdresume"),
                    ("on_clouds_clear", "calib"), ("on_unsafe", "abort")]:
            assert leg in rules, f"missing {leg} — got {sorted(rules)}"
        assert plan["automation"]["dome"]["on_unsafe"] == "close"
        assert plan["automation"]["dusk_flats"]["adu_target"] == 28500
        assert plan["automation"]["calibration_queue"]["quota"] == 20

    def test_the_eaa_example_runs_now_and_shoots_short_subs(self):
        eaa = next(e for e in examples() if e.id == "example-eaa")
        plan = compile_plan(eaa.graph, eaa.name)
        assert plan["schedule"] == {"start_mode": "now"}
        step = plan["targets"][0]["steps"][0]
        assert step["exposure_s"] == 4 and step["gain"] == 300 and step["binning"] == 2
