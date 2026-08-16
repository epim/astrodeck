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

    def test_the_lane_bridges_are_named_and_each_has_a_reason(self):
        """A node with BOTH a flow output and an event output is a bridge: it
        lets something be observed without the run cursor leaving the lane.

        The set is pinned rather than counted, because every member is a design
        decision and a new one arriving silently is how a graph starts promising
        a night it cannot deliver. Four, and each earns it:

        * ``dusk``     — `window opens` is the lane; `night ends` is the campaign
                         shutdown, which must fire while there is still time.
        * ``capture``  — `complete` is the lane; `frame graded` drives watchdogs.
        * ``cycle``    — same pair, same reason. It is a capture stage.
        * ``pool``     — `best target` is the lane; `floor hit` says the active
                         target sank, which the lane itself cannot express.
        """
        both = {t for t, d in NODE_DEFS.items()
                if any(p.kind == "flow" for p in d.outs)
                and any(p.kind == "event" for p in d.outs)}
        assert both == {"dusk", "capture", "cycle", "pool"}, both

    def test_the_optional_inputs_are_exactly_these(self):
        """An input may be optional only when its absence is HARMLESS, or is
        explained somewhere in the operator's own words. Anything else is a
        graph that is quietly incomplete with nothing saying so.

        The list grew on 2026-08-16 because the doctor had gone stale against
        the engine: it demanded four wires that `to_plan.REDUNDANT_PORTS`
        documents as doing nothing, so an operator who followed the advice
        drew a wire the very next panel called redundant. Each entry below
        names why its absence costs nothing."""
        opt = {(t, p) for t, d in NODE_DEFS.items() for p in d.optional_ins}
        assert opt == {
            ("calib", "panel"),        # rule 7 — flats get skipped, and it says so
            ("calib", "do"),           # quota alone funds the hold darks
            ("calib", "stop"),         # the hold ends when the sky clears
            ("holdresume", "resume"),  # the hold releases itself
            ("parkclose", "do"),       # the night ends parked+shut regardless
            ("pool", "advance"),       # the scheduler advances the pool itself
        }, opt

    def test_every_optional_input_is_a_real_port(self):
        """A typo in `optional_ins` would silently exempt nothing, and the
        doctor would go on demanding a wire the operator cannot see is optional."""
        for t, d in NODE_DEFS.items():
            for pid in d.optional_ins:
                assert d.port(pid, "in") is not None, f"{t}.{pid} is not an input"

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
        assert any("SLEW + CENTER - 'run' input unwired" in i.text for i in out)

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

    def test_11_the_pool_advance_rule_is_GONE(self):
        """RULE REMOVED 2026-08-16, and this assertion is inverted rather
        than deleted so the removal is deliberate and stays that way.

        It told a campaign to wire SESSION REPORT 'target done' ->
        'advance'. `to_plan.REDUNDANT_PORTS` says of that same wire: "the
        scheduler advances the pool itself: a target whose frames are all
        in the ledger is skipped and the next member gets the night, on
        this night and on every night after". An operator who followed
        the advice drew a wire the next panel called redundant."""
        g = FlowGraph(
            nodes=[_n("d", "dusk", repeat="Nightly until pool complete"),
                   _n("p", "pool"), _n("r", "report")],
            edges=[_e("d", "window", "p", "arm")])
        assert not any("advances the POOL" in i.text for i in check(g))

    def test_11b_a_single_night_pool_is_not_nagged_about_advance(self):
        """`advance` is optional, and rule 11 only speaks once the DUSK WINDOW
        says this comes back tomorrow. A one-night pool that never advances is a
        correct graph, not an incomplete one."""
        g = FlowGraph(nodes=[_n("d", "dusk"), _n("p", "pool")],
                      edges=[_e("d", "window", "p", "arm")])
        text = " ".join(i.text for i in check(g))
        assert "advances the POOL" not in text
        assert "'advance' input unwired" not in text

    def test_11c_the_wire_that_settles_it_is_an_event_pointing_BACKWARDS(self):
        """The campaign loop is SESSION REPORT 'target done' -> POOL 'advance'.
        It points back up the graph, which is legal precisely because it is an
        event wire: the flow lane stays acyclic and the cursor never revisits."""
        g = FlowGraph(
            nodes=[_n("d", "dusk", repeat="Nightly until pool complete"),
                   _n("p", "pool"), _n("r", "report"), _n("pc", "parkclose")],
            edges=[_e("d", "window", "p", "arm"),
                   _e("r", "done", "p", "advance"),
                   _e("d", "nightend", "pc", "do")])
        text = " ".join(i.text for i in check(g))
        assert "advances the POOL" not in text
        assert "no shutdown lane" not in text

    def test_12_the_shutdown_lane_rule_is_GONE(self):
        """RULE REMOVED 2026-08-16, same reason as 11. The night already
        ends parked with the dust cover shut whether or not the wire is
        there - every flow's plan carries park-when-done and the wind-down
        closes the cover. The ROOF is the only part that depends on
        anything outside the graph, and rule 9 is what speaks about it."""
        g = FlowGraph(nodes=[_n("d", "dusk", repeat="Nightly ×30"), _n("r", "report")])
        assert not any("shutdown lane" in i.text for i in check(g))

    def test_13_safety_and_cloud_watch_racing_over_the_same_sky(self):
        """Safety aborts and never holds; CLOUD WATCH holds and never aborts. If
        safety is also watching clouds it wins every race, and the hold that
        would have ridden the cloud out never runs."""
        g = FlowGraph(nodes=[_n("s", "safety", watch="Clouds + rain + wind (standalone)"),
                             _n("cw", "cloudwatch")])
        assert any("they race, and safety aborts" in i.text for i in check(g))

    def test_13b_scoping_safety_away_from_clouds_settles_it(self):
        g = FlowGraph(
            nodes=[_n("s", "safety", watch="Rain + wind + power (pair with Cloud Watch)"),
                   _n("cw", "cloudwatch")])
        assert not any("they race" in i.text for i in check(g))

    def test_13c_safety_alone_may_still_watch_clouds(self):
        """With no CLOUD WATCH there is nothing to race, and a standalone safety
        monitor watching clouds is the correct configuration for a rig with no
        transient tier at all."""
        g = FlowGraph(nodes=[_n("s", "safety", watch="Clouds + rain + wind (standalone)")])
        assert not any("they race" in i.text for i in check(g))

    def test_a_judgement_call_explains_itself(self):
        """The handoff calls out the wording tone specifically: a check that
        makes a JUDGEMENT says why, not just what.

        Rule 1 is excluded and that is not a loophole: naming an unwired port is
        a statement of fact with nothing to justify. Every other rule is an
        opinion about the night, and an opinion with no reason attached is one
        nobody acts on at 21:00.
        """
        g = FlowGraph(nodes=[_n("c", "capture", exposure=180), _n("d", "dome")])
        judged = [i for i in check(g) if "input unwired" not in i.text]
        assert judged, "the fixture stopped producing judgement-call issues"
        for issue in judged:
            assert issue.text.startswith("▸"), issue.text
            assert " - " in issue.text or ". " in issue.text, \
                f"no reason given: {issue.text}"

    def test_no_issue_ships_an_em_dash(self):
        """The 2026-08-14 do-not list bans em-dashes from every shipped string,
        and these are shipped twice: the header chip and the compile response.

        Checked across a graph that fires every rule rather than one at a time,
        because a single new rule written with the old separator is exactly the
        way this decays.
        """
        g = FlowGraph(
            nodes=[_n("d", "dusk", repeat="Nightly ×30"),
                   _n("s", "safety", watch="Clouds + rain + wind (standalone)"),
                   _n("cw", "cloudwatch"), _n("dm", "dome"), _n("q", "calib"),
                   _n("p", "pool"), _n("k", "condition", threshold=9.0),
                   _n("c", "capture", exposure=300, reject=3.0),
                   _n("cy", "cycle")],
            edges=[_e("cw", "in", "q", "do"), _e("c", "frame", "k", "events")])
        issues = check(g)
        assert len(issues) > 8, f"the fixture stopped firing most rules: {issues}"
        for issue in issues:
            assert "—" not in issue.text and "–" not in issue.text, issue.text


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
