# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

import json
from pathlib import Path

import pytest

from astrodeck.devices.base import DEFAULT_SHUTTER_TIMEOUT_S
from astrodeck.flows.compile import (_trigger_for, compile_plan, flow_order,
                                     is_multi_panel)
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode

#: Every Example's compile, captured before the panel lane was written.
LEGACY_EXAMPLES = json.loads(
    (Path(__file__).parent / "fixtures" / "panel_lane_cases.json")
    .read_text(encoding="utf-8"))["legacy_examples"]


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


#: The keys S3's compile adds to an entry, by the node type that emits it.
_S3_KEYS = {"target": ("angle", "mosaic", "loop", "centre", "count_mode",
                       "frame_anchor", "follows"),
            "pool": ("count_mode", "follows")}


def _s3_keys(node: FlowNode) -> dict:
    """What the S3 keys must hold for a node of a graph with NO mosaic: the
    node's own angle and centring, no grid, no loop, no follower, and what
    its `counts` asks for: accepted subs, for all seven Examples.

    "attempts" until the integration of S3, which re-pinned it: S3-W made
    every Example's TARGET and POOL count accepted subs (Revision 2 ruling 2,
    a created block counts them). Typed here, not read from the node, so the
    Examples and the compile are checked by a second hand.

    RED under mutant "every block counts attempts" (in a private scratch copy
    of compile.py, ``count_mode_of`` returning "attempts"), observed for all
    seven, the first (example-campaign, whose entry n20 is a POOL member):

        E   AssertionError: n20
        E   assert {'count_mode': 'attempts'} == {'count_mode': 'accepted'}
    """
    p = node.params
    if node.type == "pool":
        return {"count_mode": "accepted"}
    return {"angle": "any" if float(p["rotation"]) < 0 else "rotate",
            "mosaic": None, "loop": False,
            "centre": {"tol_arcmin": p["centerTol"],
                       "attempts": p["centerTries"]},
            "count_mode": "accepted", "frame_anchor": ""}


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
        """The editor CAN draw one (#149: replace-on-drop turns a back-edge
        into a loop), and validation now refuses it at save and /run. The
        compile routes still compile half-built graphs, so dropping the cycle
        stays the fail-closed reading for whatever reaches here."""
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
        # `node_id` added DELIBERATELY in #189 S1 (item 7): the step names the
        # stage that emitted it, because `to_plan` keys the step's
        # deterministic id on that stage (spec 3.2, 3.3). The rest of the dict
        # is unchanged.
        assert step == {"filter": "Ha", "exposure_s": 180, "gain": 100,
                        "binning": 1, "count": 20, "frame_type": "Light",
                        "node_id": "c"}

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
        """``twilight_deg`` is -18 (backlog WP-09, #191): with no "start"
        param given, ``with_defaults()`` fills "Astro dusk", which
        ``_dusk_schedule`` now compiles to its own Sun altitude."""
        g = FlowGraph(nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30)])
        assert compile_plan(g, "n")["schedule"] == {
            "start_mode": "dusk", "start_offset_min": -30,
            "stop_mode": "dawn", "min_altitude_deg": 30,
            "twilight_deg": -18.0}

    def test_stop_none_is_honoured(self):
        g = FlowGraph(nodes=[_n("d", "dusk", stop="None")])
        assert compile_plan(g, "n")["schedule"]["stop_mode"] == "none"


class TestNodeIdsRideTheCompile:
    """#189 S1 item 7 (spec 3.2): every TARGET entry, every POOL member and
    every capture or cycle step names the node that emitted it. ``to_plan``
    keys the deterministic ids on those names, so an entry that lost its node
    id would quietly fall back to a random id and a flow could not continue
    its ledger - nothing else in the compile would look wrong."""

    def test_a_target_entry_names_its_node(self):
        """Mutant "no node_id on the TARGET entry" failed:
            KeyError: 'node_id'
        """
        g = FlowGraph(nodes=[_n("t7", "target", name="M31", ra="00h", dec="+41")])
        assert compile_plan(g, "n")["targets"][0]["node_id"] == "t7"

    def test_every_pool_member_names_the_pool(self):
        """Mutant "no node_id on a POOL member" failed:
            KeyError: 'node_id'
        """
        g = FlowGraph(nodes=[_n("p3", "pool", members="M16, M17, M8")])
        assert [t["node_id"] for t in compile_plan(g, "n")["targets"]] == \
            ["p3", "p3", "p3"]

    def test_a_capture_step_names_its_stage_not_its_target(self):
        """After a pool every member gets the capture's step; the step names
        the CAPTURE node, which is what tells two stages apart on one target.

        Mutant "no node_id on a capture step" failed:
            KeyError: 'node_id'
        """
        g = FlowGraph(nodes=[_n("p", "pool", members="A, B"),
                             _n("c9", "capture", x=100, count=5)],
                      edges=[_e("p", "target", "c9", "run")])
        steps = [t["steps"][0] for t in compile_plan(g, "n")["targets"]]
        assert [s["node_id"] for s in steps] == ["c9", "c9"]

    def test_a_cycle_step_names_its_stage(self):
        """Mutant "no node_id on a cycle step" failed:
            KeyError: 'node_id'
        """
        g = FlowGraph(nodes=[_n("t", "target"),
                             _n("y4", "cycle", x=100, plan="L 60, R 60")],
                      edges=[_e("t", "target", "y4", "run")])
        step = compile_plan(g, "n")["targets"][0]["steps"][0]
        assert step["strategy"] == "cycle" and step["node_id"] == "y4"


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
        # `to_port` rides along now: a HOLD/RESUME node is two different actions
        # depending on which input a rule lands on, and the node type alone
        # cannot tell them apart.
        assert rule == {"when": "on_hfr_above", "action": "refocus",
                        "to_port": "do", "threshold": 3.2}

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

    @pytest.mark.parametrize("ntype", ["capture", "cycle"])
    def test_a_pass_wire_names_its_own_trigger(self, ntype):
        """Spec 1.3 item 1: a stage's ``pass`` output is structural. Before
        the branch, capture and cycle answered ``on_frame_graded`` for ANY
        port, so a pass wire would have compiled to a rule that fires on
        every graded frame. ``<type>.pass`` is no engine trigger, which is the
        point: ``to_plan`` reports a pass wire that is not the loop as "this
        rule will not run".

        CHANGED IN S3 (the compile task, spec 1.4 item 3): the LEGAL loop
        wire (the tail of a multi-panel lane into its own block's ``next``)
        is now consumed as the block's ``loop`` and never reaches the
        instructions, so this case grades the trigger on a pass wire that is
        NOT the loop: the same wire on a block of one panel, which has
        nothing to rotate between (M4). Its original 3x2 graph now compiles
        to no rule at all, which ``test_flows_compile_entry_s3.py`` grades.
        The direct call then pins the branch itself, with the graded frame
        as its control.

        Mutant "no pass branch" failed (S3-LANE, on the 3x2 graph; the same
        assertion, re-run on this graph in scratchpad s3-cp-mut):
            AssertionError: assert ['on_frame_graded'] == ['capture.pass']
            AssertionError: assert ['on_frame_graded'] == ['cycle.pass']

        DELIBERATE PIN CHANGE (mosaic S4, S4 orchestrator ruling 3, #349;
        re-pinned by the S4 integration, #389): the one-panel wire above is
        now structure too. A pass wire into a ONE-PANEL block's ``next`` from
        a stage of its own lane is consumed and emits no rule
        (``compile.one_panel_pass_wires``, which
        ``test_flows_one_panel_pass_wire.py`` grades), so that graph compiles
        to no instruction and could no longer show which trigger a pass wire
        names. This case now grades a pass wire that is STILL emitted as a
        rule: the stage's ``pass`` into a NOTIFY's ``do``, which no rule of
        the compile consumes and which ``to_plan`` reports as a rule that
        will not run. The direct calls on ``_trigger_for`` are unchanged.

        Re-observed on this graph in scratchpad/s4-integrate-q7m2. Mutant "no
        pass branch" (the ``PASS_TYPES`` branch of ``_trigger_for`` made
        ``if False:``):
            E       AssertionError: assert ['on_frame_graded'] == ['capture.pass']
            E       AssertionError: assert ['on_frame_graded'] == ['cycle.pass']

        Mutant "a one-panel lane's pass wire is consumed wherever it goes"
        (``one_panel_pass_wires`` without its ``NEXT_PORT`` and TARGET
        checks, taking any pass wire whose source a one-panel block owns),
        under which the wire into the NOTIFY vanishes with no loss reported:
            E       AssertionError: assert [] == ['capture.pass']
            E       AssertionError: assert [] == ['cycle.pass']
        """
        g = FlowGraph(
            nodes=[_n("t", "target", rows=1, cols=1), _n("s", ntype, x=200),
                   _n("n", "notify", x=400)],
            edges=[_e("t", "target", "s", "run"), _e("s", "pass", "n", "do")])
        instructions = compile_plan(g, "n")["instructions"]
        assert [r["when"] for r in instructions] == [f"{ntype}.pass"]
        assert [r["action"] for r in instructions] == ["notify"]
        node = _n("s", ntype)
        assert _trigger_for(node, "pass") == f"{ntype}.pass"
        # Control: the graded-frame event keeps its engine trigger.
        assert _trigger_for(node, "frame") == "on_frame_graded"


class TestAutomation:
    def test_a_dome_is_always_fail_closed(self):
        g = FlowGraph(nodes=[_n("d", "dome")])
        assert compile_plan(g, "n")["automation"]["dome"] == {
            "bind": True, "on_unsafe": "close",
            "shutter_timeout_s": DEFAULT_SHUTTER_TIMEOUT_S}

    def test_the_dome_nodes_own_settings_reach_the_plan(self):
        """The prototype hardcoded {slave: true, on_unsafe: close}, so the
        node's Azimuth dropdown and its shutter timeout never left the editor —
        two controls that looked live and did nothing. Owner authorised wiring
        them through on 2026-08-12."""
        g = FlowGraph(nodes=[_n("d", "dome", bind="Manual", timeout=45)])
        dome = compile_plan(g, "n")["automation"]["dome"]
        assert dome["bind"] is False
        assert dome["shutter_timeout_s"] == 45.0

    @pytest.mark.parametrize("params", [
        {"bind": "Manual", "timeout": 45},         # everything overridden
        {"bind": "Bind to mount"},                 # the default, said out loud
        {"timeout": 0},                            # a timeout that cannot work
        {"on_unsafe": "leave open"},               # what an edited plan may say
        {},                                        # nothing set at all
    ])
    def test_on_unsafe_is_a_constant_whatever_the_node_says(self, params):
        """DomePolicy has no on_unsafe FIELD on purpose: a value that can arrive
        is a value that can say 'don't close', and the roof would still be open
        in the rain having been talked out of shutting by an old client or a
        hand-edited plan."""
        g = FlowGraph(nodes=[_n("d", "dome", **params)])
        assert compile_plan(g, "n")["automation"]["dome"]["on_unsafe"] == "close"

    @pytest.mark.parametrize("timeout", [0, -5, "soon", None])
    def test_an_unusable_timeout_becomes_the_default_not_itself(self, timeout):
        """Zero would make open_and_confirm give up before the roof could
        physically have moved, turning 'the sky is above you' into a coin toss."""
        g = FlowGraph(nodes=[_n("d", "dome", timeout=timeout)])
        assert compile_plan(g, "n")["automation"]["dome"][
            "shutter_timeout_s"] == DEFAULT_SHUTTER_TIMEOUT_S

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

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_every_example_targets_coordinates_can_actually_be_READ(self, ex):
        """A compiled target carries ra/dec as the operator typed them, and the
        run turns those strings into numbers. So "it compiles" is not the bar —
        the strings have to survive parse_ra/parse_dec too.

        This is here because they did not. All four example decs shipped with
        typographic primes and a Unicode minus (they were authored in a design
        tool that substitutes as you type), which meant every example compiled
        to a plan that could not be pointed at. The compile was clean; the night
        would have failed at the slew.
        """
        from astrodeck.catalog.coords import parse_dec, parse_ra
        for t in compile_plan(ex.graph, ex.name)["targets"]:
            if t.get("ra") is None:      # a pool names members, not coordinates
                continue
            assert -90.0 <= parse_dec(t["dec"]) <= 90.0, t
            assert 0.0 <= parse_ra(t["ra"]) < 24.0, t

    @pytest.mark.parametrize("ex_id", sorted(LEGACY_EXAMPLES))
    def test_with_no_mosaic_every_example_compiles_byte_identically(self, ex_id):
        """Spec 1.5: the canvas-order rule is kept byte for byte for any graph
        with no multi-panel block, so every Example (and every saved flow)
        compiles exactly as it did before the panel lane existed. Compared as
        serialised text, so key order and int-versus-float count too.

        An Example that later gains a mosaic fails the guard below rather than
        being skipped: taking it out of the corpus has to be a decision.

        All seven compile the same under the wire rule too (checked by forcing
        it), so this control cannot see "the wire rule in every graph"; the
        1x1 case in test_flows_panel_lane.py is what catches that one.

        Mutant "an empty notes list on every compile" failed all seven:
            AssertionError: example-campaign changed its compile
            assert '{"name": "Ca... "notes": []}' == '{"name": "Ca...": "cursor"}}'
              -  "cursor"}}
              +  "cursor"}, "notes": []}

        RE-PINNED ON PURPOSE IN S3 (the compile task; spec 3.2, #189, #170):
        every TARGET entry gained ``angle``, ``mosaic``, ``loop``, ``centre``,
        ``count_mode`` and, with no grid, ``frame_anchor``, and every POOL
        entry ``count_mode``. BOUNDED FROM BOTH SIDES rather than regenerated:
        the new keys must hold exactly what the Example's node means
        (``_s3_keys``), and with them taken off the compile is the capture
        byte for byte. The fixture belongs to S3-LANE and is not edited.

        Mutant "one more key on every entry" (``_target_entry`` also writes
        ``"panels": 1``), observed for the five Examples with a TARGET:
            AssertionError: example-cycle changed its compile beyond the S3 keys
            assert '{"name": "M3...shold": 40}]}' == '{"name": "M3...shold": 40}]}'

        Mutant "a mosaic on every block" (``mosaic`` is ``{}`` for a 1x1),
        observed for the same five:
            AssertionError: n2
            assert {'angle': 'an...hor': '', ...} == {'angle': 'an...hor': '', ...}
              Omitting 5 identical items, use -vv to show
              Differing items:
              {'mosaic': {}} != {'mosaic': None}

        BOUNDED THE SAME WAY, AGAIN, FOR BACKLOG WP-09's ``twilight_deg``
        (#191, 2026-09-30): every Example with a DUSK WINDOW leaves its
        Start unset, which ``with_defaults()`` reads as "Astro dusk", so
        ``_dusk_schedule`` now writes ``schedule["twilight_deg"] = -18.0`` -
        a key the fixture predates. Popped off and checked here, not folded
        into the fixture: this file's own S3 keys were bounded rather than
        regenerated for the same reason, and a fixture that could be
        regenerated to match whatever the compile currently does would stop
        being a control.

        Mutant "the angle not bounded" (this pop deleted, the fixture left
        alone), observed for the same six (every Example but example-eaa,
        which has no DUSK node):
            AssertionError: example-campaign changed its compile beyond the
            S3 keys
            assert '{"auto...ge": 30}}' == '{"auto...ge": 30}}'

        BOUNDED THE SAME WAY, AGAIN, FOR BACKLOG WP-34 (#195, 2026-09-30):
        every Example whose DUSK WINDOW has no explicit ``repeat`` (so it
        defaults to "Single night") now compiles ``resume_across_nights:
        false`` (#195: "Single night" means auto-resume does not arm across
        nights) -- example-m31, example-m16, example-cycle, example-pool and
        example-nb, five of the six with a DUSK node; example-campaign's own
        explicitly repeats, so it carries no opinion here and the key stays
        absent, exactly as before. Popped off and checked here, not folded
        into the fixture, for the same reason ``twilight_deg`` is above.

        Mutant "resume_across_nights not bounded" (this pop deleted, the
        fixture left alone), observed for the five:
            AssertionError: example-cycle changed its compile beyond the S3
            keys
            assert '{"name": "M3...ghts": false}' == '{"name": "M3...shold": 40}]}'
              Skipping 1420 identical leading characters in diff, use -v to show
              - old": 40}]}
              + old": 40}], "resume_across_nights": false}
        """
        ex = next((e for e in examples() if e.id == ex_id), None)
        assert ex is not None, f"{ex_id} is no longer an Example"
        assert not any(is_multi_panel(n) for n in ex.graph.nodes), (
            f"{ex_id} now has a mosaic, so the wire rule compiles it; remove "
            "it from legacy_examples in panel_lane_cases.json deliberately")
        compiled = compile_plan(ex.graph, ex.name)
        nodes = {n.id: n for n in ex.graph.with_defaults().nodes}
        for entry in compiled["targets"]:
            node = nodes[entry["node_id"]]
            taken = {k: entry.pop(k) for k in _S3_KEYS[node.type]
                     if k in entry}
            assert taken == _s3_keys(node), entry["node_id"]
        sched = compiled.get("schedule") or {}
        dusk_nodes = [n for n in ex.graph.with_defaults().nodes
                     if n.type == "dusk"]
        if "twilight_deg" in sched:
            angle = sched.pop("twilight_deg")
            assert dusk_nodes and dusk_nodes[0].params.get("start") not in (
                "Clock time",), (ex_id, dusk_nodes)
            assert angle == -18.0, (
                f"{ex_id}'s DUSK WINDOW defaults to Astro dusk (-18); "
                f"got {angle}")
        else:
            assert not dusk_nodes or dusk_nodes[0].params.get(
                "start") == "Clock time", (
                f"{ex_id} has a sun-based DUSK WINDOW with no twilight_deg")
        if "resume_across_nights" in compiled:
            resume = compiled.pop("resume_across_nights")
            repeat = str((dusk_nodes[0].params.get("repeat") if dusk_nodes
                         else None) or "Single night")
            assert resume is False and dusk_nodes and repeat == "Single night", (
                f"{ex_id} compiles resume_across_nights although its DUSK "
                f"WINDOW is not Single night: {resume}, {repeat}")
        else:
            repeat = str((dusk_nodes[0].params.get("repeat") if dusk_nodes
                         else None) or "Single night")
            assert not dusk_nodes or repeat != "Single night", (
                f"{ex_id} has a Single-night DUSK WINDOW with no "
                f"resume_across_nights")
        got = json.dumps(compiled, ensure_ascii=False)
        want = json.dumps(LEGACY_EXAMPLES[ex_id], ensure_ascii=False)
        assert got == want, f"{ex_id} changed its compile beyond the S3 keys"

    def test_the_legacy_corpus_is_the_seven_examples(self):
        """The control above is only as wide as its corpus; a capture that
        missed an Example would grade six and say seven.

        Mutant of the FIXTURE (a scratch copy) "drop example-eaa" failed:
            AssertionError: assert 6 == 7
        """
        assert len(LEGACY_EXAMPLES) == 7
        assert set(LEGACY_EXAMPLES) <= {e.id for e in examples()}

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


class TestTheDomeParamRename:
    """`slave` became `bind` on 2026-08-14, and a flow is stored as the graph the
    operator drew - so the rename is a data migration whether or not anyone calls
    it one."""

    def test_a_graph_saved_before_the_rename_still_means_Manual(self):
        # The failure this prevents: `with_defaults` merges the NEW key's
        # default over the top of the OLD key's value, the default wins, and a
        # dome the operator set to Manual in July comes back bound to the mount.
        g = FlowGraph(nodes=[_n("d", "dome", slave="Manual", timeout=45)])
        dome = compile_plan(g, "n")["automation"]["dome"]
        assert dome["bind"] is False, "a July flow silently re-bound its dome"
        assert dome["shutter_timeout_s"] == 45.0

    def test_the_new_key_wins_when_a_graph_carries_both(self):
        """Only possible from a hand-edited file, and the newer name is the one
        the editor writes - so it is the one the operator last saw."""
        g = FlowGraph(nodes=[_n("d", "dome", slave="Manual", bind="Bind to mount")])
        assert compile_plan(g, "n")["automation"]["dome"]["bind"] is True
