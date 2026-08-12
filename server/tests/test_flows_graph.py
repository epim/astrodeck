"""Flows — the node vocabulary, graph validation, and the ten doctor rules.

The handoff is a SCOPED IMPLEMENTATION task: the design is finished, and the
prototype's ``DEFS`` / ``issues()`` are the contract. So these tests are mostly
transcription checks — they assert this build says what the handoff says, not
what seemed reasonable while writing it. A test that agreed with the
implementation instead of with the spec would let a plausible invention through,
and "never resolve ambiguity by inventing" is the first ground rule.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import NODE_DEFS, PALETTE_GROUPS, check, port_kind
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode, FlowRecord


def _n(nid: str, ntype: str, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


class TestVocabulary:
    def test_every_palette_entry_is_a_real_node(self):
        listed = [t for _group, types in PALETTE_GROUPS for t in types]
        assert set(listed) == set(NODE_DEFS), (
            f"palette and table disagree: only in palette "
            f"{set(listed) - set(NODE_DEFS)}, only in table "
            f"{set(NODE_DEFS) - set(listed)}")
        assert len(listed) == len(set(listed)), "a node appears twice in the palette"

    def test_the_five_palette_groups_are_the_readmes(self):
        assert [g for g, _ in PALETTE_GROUPS] == [
            "SOURCES", "EQUIPMENT", "RIG OPS", "LOGIC", "ACTIONS + SINKS"]

    @pytest.mark.parametrize("ntype,port,direction,kind", [
        # The port kinds that decide the whole grammar — flow is the run cursor,
        # event is "whenever". Transcribed from DEFS.
        ("dusk", "window", "out", "flow"),
        ("target", "target", "out", "flow"),
        ("safety", "unsafe", "out", "event"),
        ("cloudwatch", "in", "out", "event"),
        ("cloudwatch", "clear", "out", "event"),
        ("capture", "complete", "out", "flow"),
        ("capture", "frame", "out", "event"),      # the one node with both
        ("calib", "do", "in", "event"),
        ("calib", "panel", "in", "event"),
        ("holdresume", "pause", "in", "event"),
        ("holdresume", "resume", "in", "event"),
        ("report", "session", "in", "flow"),
        ("dome", "open", "out", "flow"),
        ("flatpanel", "ready", "out", "event"),
    ])
    def test_port_kinds_match_the_handoff(self, ntype, port, direction, kind):
        assert port_kind(ntype, port, direction) == kind

    def test_capture_is_the_node_that_bridges_the_two_lanes(self):
        """CAPTURE LOOP is the only node with a flow output AND an event output.
        That is what lets a frame grade drive a rule without the run cursor
        leaving the lane."""
        both = [t for t, d in NODE_DEFS.items()
                if any(p.kind == "flow" for p in d.outs)
                and any(p.kind == "event" for p in d.outs)]
        assert both == ["capture"], both

    def test_panel_is_the_only_optional_input(self):
        opt = {(t, p) for t, d in NODE_DEFS.items() for p in d.optional_ins}
        assert opt == {("calib", "panel")}

    def test_an_unknown_node_type_types_no_port(self):
        """Fail-closed: an older or newer client naming a type we lack must get
        'I cannot type that edge', not a crash."""
        assert port_kind("scriptnode", "in", "in") is None
        assert port_kind("capture", "nonesuch", "out") is None


class TestGraphValidation:
    def test_a_kind_mismatch_is_refused_in_the_uis_words(self):
        g = FlowGraph(nodes=[_n("a", "cloudwatch"), _n("b", "slew")],
                      edges=[_e("a", "in", "b", "run")])
        errs = g.validation_errors()
        assert errs and "can't feed" in errs[0], errs

    def test_every_error_is_reported_at_once(self):
        """A client that fixes one error and is then told about the next needs N
        round trips to learn what it did wrong."""
        g = FlowGraph(nodes=[_n("a", "dusk")],
                      edges=[_e("a", "window", "ghost", "arm"),
                             _e("nobody", "window", "a", "arm")])
        assert len(g.validation_errors()) == 2

    def test_one_wire_per_input(self):
        g = FlowGraph(
            nodes=[_n("d", "dusk"), _n("t1", "target"), _n("t2", "target"),
                   _n("s", "slew")],
            edges=[_e("t1", "target", "s", "run"), _e("t2", "target", "s", "run")])
        assert any("wired twice" in e for e in g.validation_errors())

    def test_a_good_graph_has_no_structural_errors(self):
        g = FlowGraph(nodes=[_n("d", "dusk"), _n("t", "target"), _n("s", "slew")],
                      edges=[_e("d", "window", "t", "arm"),
                             _e("t", "target", "s", "run")])
        assert g.validation_errors() == []

    def test_defaults_fill_in_for_a_graph_saved_by_an_older_build(self):
        g = FlowGraph(nodes=[FlowNode(id="c", type="capture", params={"filter": "Ha"})])
        n = g.with_defaults().nodes[0]
        assert n.params["filter"] == "Ha", "the operator's value was overwritten"
        assert n.params["exposure"] == 120, "a missing param did not get its default"

    def test_an_unknown_node_type_is_refused_at_the_door(self):
        with pytest.raises(ValueError, match="unknown node type"):
            FlowNode(id="x", type="scriptnode")


class TestTheDoctor:
    """One test per rule, each named for what it protects."""

    def test_1_an_unwired_required_input_is_named(self):
        out = check(FlowGraph(nodes=[_n("s", "slew")]))
        assert any("SLEW + CENTER — 'run' input unwired" in i.text for i in out)

    def test_1b_the_panel_input_is_exempt(self):
        out = check(FlowGraph(nodes=[_n("q", "calib")]))
        assert not any("'panel' input unwired" in i.text for i in out)

    def test_2_long_subs_with_no_guider(self):
        out = check(FlowGraph(nodes=[_n("c", "capture", exposure=180)]))
        assert any("stars will trail" in i.text for i in out)

    def test_3_60s_subs_with_no_autofocus(self):
        out = check(FlowGraph(nodes=[_n("c", "capture", exposure=60)]))
        assert any("focus drift goes uncorrected" in i.text for i in out)

    def test_4_a_loop_that_shoots_wherever_the_mount_points(self):
        out = check(FlowGraph(nodes=[_n("c", "capture", exposure=30)]))
        assert any("wherever the mount happens to point" in i.text for i in out)

    def test_4b_a_slew_upstream_silences_it(self):
        g = FlowGraph(nodes=[_n("s", "slew"), _n("c", "capture", exposure=30)],
                      edges=[_e("s", "centered", "c", "run")])
        assert not any("wherever the mount" in i.text for i in check(g))

    def test_4c_an_EVENT_wire_does_not_count_as_upstream(self):
        """The question is 'did the run cursor pass through a slew', and an event
        wire is not a thing the cursor travelled."""
        g = FlowGraph(
            nodes=[_n("cw", "cloudwatch"), _n("s", "slew"), _n("c", "capture", exposure=30)],
            edges=[_e("cw", "in", "c", "run")])   # not even same-kind, but the
        # point is that no FLOW path exists to the capture
        assert any("wherever the mount" in i.text for i in check(g))

    def test_5_a_condition_that_fires_into_nothing(self):
        out = check(FlowGraph(nodes=[_n("k", "condition")]))
        assert any("fires into nothing" in i.text for i in out)

    def test_6_a_watchdog_above_the_graders_reject_can_never_fire(self):
        g = FlowGraph(
            nodes=[_n("c", "capture", reject=3.0, exposure=30),
                   _n("k", "condition", when="HFR above", threshold=4.0),
                   _n("r", "refocus")],
            edges=[_e("c", "frame", "k", "events"), _e("k", "fire", "r", "do")])
        assert any("before the rule can ever fire" in i.text for i in check(g))

    def test_7_a_queue_that_wants_flats_with_no_panel(self):
        out = check(FlowGraph(nodes=[_n("q", "calib")]))
        assert any("flats will be skipped" in i.text for i in out)

    def test_8_a_queue_that_runs_while_the_light_loop_does_too(self):
        g = FlowGraph(nodes=[_n("cw", "cloudwatch"), _n("q", "calib")],
                      edges=[_e("cw", "in", "q", "do")])
        assert any("nothing HOLDS the light loop" in i.text for i in check(g))

    def test_9_a_dome_with_no_safety_monitor_is_DANGER(self):
        out = check(FlowGraph(nodes=[_n("d", "dome")]))
        hit = [i for i in out if "closes the shutter on rain" in i.text]
        assert hit and hit[0].level == "danger", out

    def test_10_no_session_report_is_only_a_NOTE(self):
        out = check(FlowGraph(nodes=[_n("d", "dusk")]))
        hit = [i for i in out if "leaves no ledger" in i.text]
        assert hit and hit[0].level == "note", out

    def test_every_issue_explains_why(self):
        """The handoff calls out the wording tone specifically: each check says
        why, not just what. An em-dash clause is how each of them does it."""
        g = FlowGraph(nodes=[_n("c", "capture", exposure=180), _n("d", "dome")])
        for issue in check(g):
            assert issue.text.startswith("▸"), issue.text
            assert "—" in issue.text, f"no reason given: {issue.text}"


class TestLibraryRecord:
    def test_a_card_carries_no_graph(self):
        """The library lists every flow; shipping each one's nodes to draw a
        name and a stage count would send megabytes to render a grid."""
        r = FlowRecord(name="M16", graph=FlowGraph(nodes=[_n("a", "dusk")]))
        card = r.card()
        assert card["stages"] == 1 and "graph" not in card and "nodes" not in card

    def test_a_folder_cannot_be_a_path(self):
        with pytest.raises(ValueError):
            FlowRecord(name="x", folder="../../etc")

    def test_an_empty_folder_falls_back_to_my_flows(self):
        assert FlowRecord(name="x", folder="  ").folder == "My flows"
