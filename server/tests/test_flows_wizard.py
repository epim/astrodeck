"""The guided wizard's generator — and the one bar the handoff sets for it.

README §9 ends with a sentence that is the whole feature: "Every generated graph
must pass the doctor." The wizard is how most flows will be born and how the
first one always is, so a generated graph that arrives already warning about
untrailed stars or skipped flats teaches a new operator, on their very first
night, that the doctor's warnings are decoration. That is why the headline test
here is a MATRIX — all three kinds × all 64 subsets of the six automation chips,
every combination the sheet can produce — and not a happy path.

Everything else in this file exists because the graph is a promise about a
night: a dome that nothing closes on rain, a queue that claims flats it will
skip, a phone alert wired to an event that can never fire. Each of those is a
shape this project keeps re-finding, and each has a test below naming what it
would cost in the field.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan, flow_order
from astrodeck.flows.doctor import check
from astrodeck.flows.wizard import (
    AUTOMATION_OPTIONS, DEFAULT_OPTIONS, KIND_DEEP_SKY, KIND_EAA, KIND_POOL,
    KINDS, OPT_CLOUD_DODGE, OPT_DOME, OPT_DUSK_FLATS, OPT_GUIDING, OPT_NOTIFY,
    OPT_WATCHDOG, UNGUIDED_EXPOSURE_DEFAULT, flow_name, generate,
    generate_record)

#: A node card is 188px wide (README §3); the generator's rows are 200px apart.
NODE_W, NODE_H = 188.0, 160.0

#: The three target inputs the sheet's own placeholder advertises: nothing
#: typed, one name, and a comma list ("M16 · or: M16, M17, M8, NGC 6946").
TARGET_INPUTS = ("", "M16", "M16, M17, M8, NGC 6946")

_SHORT = {KIND_DEEP_SKY: "deepsky", KIND_POOL: "pool", KIND_EAA: "eaa",
          OPT_GUIDING: "guide", OPT_DUSK_FLATS: "flats", OPT_DOME: "dome",
          OPT_CLOUD_DODGE: "dodge", OPT_WATCHDOG: "hfr", OPT_NOTIFY: "notify"}


def _subsets(items):
    for mask in range(1 << len(items)):
        yield frozenset(x for i, x in enumerate(items) if mask & (1 << i))


#: Every answer the sheet can produce: 3 kinds × 64 chip subsets.
EVERY_ANSWER = [
    pytest.param(
        kind, opts,
        id="-".join([_SHORT[kind]]
                    + [_SHORT[o] for o in AUTOMATION_OPTIONS if o in opts]))
    for kind in KINDS for opts in _subsets(AUTOMATION_OPTIONS)]


def _types(graph) -> list[str]:
    return [n.type for n in graph.nodes]


def _one(graph, node_type):
    """The single node of a type — and a failure if the graph grew a second.

    Duplicates are a real outcome here: two branches of the generator can both
    want a safety monitor, and two monitors would each fail closed on the same
    rain."""
    found = [n for n in graph.nodes if n.type == node_type]
    assert len(found) == 1, f"expected one {node_type}, got {len(found)}"
    return found[0]


def _legs(graph, from_type: str, to_type: str) -> set[tuple[str, str]]:
    """Every (out port, in port) pair wired between two node types."""
    by_id = {n.id: n for n in graph.nodes}
    return {(e.fromPort, e.toPort) for e in graph.edges
            if by_id[e.from_].type == from_type and by_id[e.to].type == to_type}


def _sources_of(graph, node_type: str) -> set[str]:
    by_id = {n.id: n for n in graph.nodes}
    return {by_id[e.from_].type for e in graph.edges
            if by_id[e.to].type == node_type}


def _overlapping(nodes) -> list[str]:
    """Cards that would land on top of each other on the canvas."""
    return [f"{a.type}@({a.x},{a.y}) overlaps {b.type}@({b.x},{b.y})"
            for i, a in enumerate(nodes) for b in nodes[i + 1:]
            if abs(a.x - b.x) < NODE_W and abs(a.y - b.y) < NODE_H]


class TestEveryGeneratedGraphPassesTheDoctor:
    """§9's stated hard requirement, over the whole answer space.

    ONE TEST PER ANSWER, FOUR QUESTIONS OF EACH GRAPH — not four tests over the
    same answers. The four used to be separate, which generated every graph
    FOUR TIMES (1,536 generations, 768 test ids from one 400-line file, 13% of
    the whole suite's collected count) to ask four questions of it. Asking them
    of one graph is the same assertions on the same objects at 576 generations,
    and every failure is still named individually because they are collected
    rather than raised — a merge that let the first failure hide the other
    three would be trading fidelity for a number, which is the one thing this
    consolidation must not do.
    """

    @pytest.mark.parametrize("kind,opts", EVERY_ANSWER)
    def test_every_generated_graph_is_one_somebody_can_use(self, kind, opts):
        """Four promises about a generated graph, each with its own cost:

        THE DOCTOR IS SILENT. The wizard's output is somebody's first flow. If
        it opens with "120s subs with no GUIDE upstream" or "nothing closes the
        shutter on rain", they learn that the doctor is noise — and then they
        miss the one that matters at 03:00.

        IT IS STRUCTURALLY VALID. A different question from the doctor's: an
        edge naming a missing port or crossing the flow/event boundary is a
        graph that cannot be drawn or compiled at all, so the store refuses to
        save it — the wizard would hand back something unsaveable.

        IT LEAVES A LEDGER. Doctor rule 10 is only a note, so a missing SESSION
        REPORT slips past the bar above — and the night leaves no record of
        what it actually shot.

        NO TWO CARDS OVERLAP. The graph is dropped straight onto the canvas and
        the operator's first act is to read it. Overlapping cards hide each
        other's ports and wires, which is indistinguishable from a graph that
        is missing stages.

        The last two used to be checked only on the DEFAULT target; they are
        checked on all three now, so this is strictly more than it replaced.
        """
        problems: list[str] = []
        for target in TARGET_INPUTS:
            graph = generate(kind, opts, target)
            where = f"{kind} {sorted(opts)} target={target!r}"
            loud = [i.text for i in check(graph) if i.level in ("warn", "danger")]
            if loud:
                problems.append(f"{where}: doctor says {loud}")
            errs = graph.validation_errors()
            if errs:
                problems.append(f"{where}: invalid graph {errs}")
            if "report" not in _types(graph):
                problems.append(f"{where}: no SESSION REPORT — the night keeps "
                                f"no record of what it shot")
            for clash in _overlapping(graph.nodes):
                problems.append(f"{where}: {clash}")
        assert not problems, "\n".join(problems)


class TestTheFlowLane:
    def test_the_lane_runs_in_the_order_the_night_does(self):
        """Not just present — ORDERED. The flow lane compiles to the run
        cursor's itinerary, so a graph that focuses after it starts capturing
        would shoot the first frames out of focus."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING}, "M31")
        assert [n.type for n in flow_order(graph)] == [
            "dusk", "target", "slew", "autofocus", "guide", "capture", "report"]

    def test_dome_and_dusk_flats_take_their_place_before_the_target(self):
        """The shutter has to be open and the twilight flats have to be taken
        while the sky is still bright — both before the mount goes anywhere."""
        graph = generate(KIND_DEEP_SKY, {OPT_DOME, OPT_DUSK_FLATS, OPT_GUIDING})
        assert [n.type for n in flow_order(graph)][:4] == [
            "dusk", "dome", "duskflats", "target"]

    def test_best_of_several_puts_a_pool_where_the_target_would_be(self):
        graph = generate(KIND_POOL, DEFAULT_OPTIONS, "M16, M17")
        assert "pool" in _types(graph) and "target" not in _types(graph)

    def test_eaa_never_guides_even_when_guiding_is_asked_for(self):
        """A 4-second sub does not need a guider, and paying the settle time
        per frame would make a live view stutter."""
        assert "guide" not in _types(generate(KIND_EAA, {OPT_GUIDING}, "M27"))

    def test_eaa_shoots_the_short_subs_the_handoff_specifies(self):
        capture = _one(generate(KIND_EAA, set(), "M27"), "capture")
        assert capture.params["exposure"] == 4
        assert capture.params["gain"] == 300
        assert capture.params["bin"] == "2"
        assert capture.params["count"] == 60
        # goal 0 = no banked-integration target. A live view is watched, not
        # accumulated across nights.
        assert capture.params["goal"] == 0

    def test_an_unguided_deep_sky_night_shortens_its_subs(self):
        """DEVIATION 1 from the prototype. Unpicking Guiding and keeping the
        120s default is how you get 24 frames of trailed stars; the doctor says
        "Add Guide, or shorten the subs", and the wizard cannot add a guider the
        operator just declined."""
        unguided = _one(generate(KIND_DEEP_SKY, set()), "capture")
        guided = _one(generate(KIND_DEEP_SKY, {OPT_GUIDING}), "capture")
        assert unguided.params["exposure"] < 120
        assert guided.params["exposure"] == 120, "guided subs keep the default"

    def test_the_unguided_default_is_30s(self):
        """Owner's call, 2026-08-12. Pinned because it is a DECISION and not a
        derivation — the generator cannot see focal length, polar alignment or
        periodic error, so the number's only justification is that it is
        conservative enough to be defensible at most focal lengths and short
        enough to stay well under the doctor's 120s line. A silent drift would
        turn a stated default into an accident."""
        assert UNGUIDED_EXPOSURE_DEFAULT == 30
        assert _one(generate(KIND_DEEP_SKY, set()), "capture"
                    ).params["exposure"] == 30

    def test_the_operator_can_override_the_unguided_sub_length(self):
        """The whole reason it is an argument: what a mount can hold unguided is
        a property of the rig, and the operator knows theirs."""
        graph = generate(KIND_DEEP_SKY, set(), unguided_exposure_s=90)
        assert _one(graph, "capture").params["exposure"] == 90

    @pytest.mark.parametrize("bad", [0, -30, "sixty", None, ""])
    def test_an_unusable_override_falls_back_to_the_default(self, bad):
        """An exposure of 0 compiles to a step that captures nothing, and the
        wizard's whole promise is that its output runs."""
        graph = generate(KIND_DEEP_SKY, set(), unguided_exposure_s=bad)
        assert _one(graph, "capture").params["exposure"] == UNGUIDED_EXPOSURE_DEFAULT

    def test_the_override_does_not_touch_a_guided_night(self):
        """It answers 'how long can you go WITHOUT a guider'. A guided lane has
        a guider, so the question does not apply and the 120s default stands."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING}, unguided_exposure_s=45)
        assert _one(graph, "capture").params["exposure"] == 120

    def test_the_override_survives_generate_record(self):
        """The route will call generate_record, not generate — a parameter that
        stops at the wrapper is the same dead control this change removed."""
        rec = generate_record(KIND_DEEP_SKY, set(), "M31", unguided_exposure_s=45)
        assert _one(rec.graph, "capture").params["exposure"] == 45

    def test_an_integer_override_stays_an_integer(self):
        """The param is rendered into a field the operator reads. '45' is a sub
        length; '45.0' is a float that escaped."""
        graph = generate(KIND_DEEP_SKY, set(), unguided_exposure_s=45.0)
        assert graph.nodes and _one(graph, "capture").params["exposure"] == 45
        assert isinstance(_one(graph, "capture").params["exposure"], int)

    def test_a_typed_target_names_the_target_node(self):
        graph = generate(KIND_DEEP_SKY, DEFAULT_OPTIONS, "  NGC 6946  ")
        assert _one(graph, "target").params["name"] == "NGC 6946"

    def test_a_comma_list_becomes_the_pools_candidates(self):
        graph = generate(KIND_POOL, set(), "M16, M17, M8, NGC 6946")
        assert _one(graph, "pool").params["members"] == "M16, M17, M8, NGC 6946"
        # And the compiler expands them into four eligible targets.
        plan = compile_plan(graph, "n")
        assert [t["name"] for t in plan["targets"]] == [
            "M16", "M17", "M8", "NGC 6946"]

    def test_one_name_is_not_a_pool_so_the_candidates_stand(self):
        """Transcribed from the prototype: a single name replaces nothing,
        because "best available" with one member has nothing to choose between
        and the night would lose its alternatives."""
        graph = generate(KIND_POOL, set(), "M16")
        assert "," in _one(graph, "pool").params["members"]


class TestTheRulesRow:
    def test_cloud_dodge_wires_both_legs_of_both_events(self):
        """Clouds in must hold the light loop AND start the queue; clouds clear
        must resume AND stop it. A hold with no resume is a night that ends at
        the first cloud with the mount still tracking."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_CLOUD_DODGE})
        assert _legs(graph, "cloudwatch", "holdresume") == {
            ("in", "pause"), ("clear", "resume")}
        assert _legs(graph, "cloudwatch", "calib") == {
            ("in", "do"), ("clear", "stop")}

    def test_dusk_flats_plus_cloud_dodge_wires_a_panel_to_the_queue(self):
        """§9: the flat panel is wired to `panel` iff Dusk flats was picked."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_CLOUD_DODGE, OPT_DUSK_FLATS})
        assert _legs(graph, "flatpanel", "calib") == {("ready", "panel")}
        assert _one(graph, "calib").params["flats"] != "Skip"

    def test_a_queue_with_no_panel_stops_claiming_flats(self):
        """DEVIATION 2. With no Dusk flats chip there is no panel to wire, and
        the queue's default "If stale + panel wired" is then a request the graph
        cannot serve — the operator would find the flats quietly skipped."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_CLOUD_DODGE})
        assert "flatpanel" not in _types(graph)
        assert _one(graph, "calib").params["flats"] == "Skip"

    def test_the_queue_never_runs_without_something_holding_the_light_loop(self):
        """Doctor rule 8: calibration frames taken while the light loop is still
        shooting are calibration frames and lights, both ruined."""
        graph = generate(KIND_DEEP_SKY, {OPT_CLOUD_DODGE})
        assert "holdresume" in _types(graph)

    def test_the_watchdog_watches_the_frame_stream_and_refocuses(self):
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_WATCHDOG})
        assert _legs(graph, "capture", "condition") == {("frame", "events")}
        assert _legs(graph, "condition", "refocus") == {("fire", "do")}

    def test_the_watchdog_fires_below_the_graders_reject_line(self):
        """Doctor rule 6: a threshold above the grader's reject means the frames
        that would trip the rule are thrown away first, so the watchdog never
        fires and the focus drifts all night anyway."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_WATCHDOG})
        assert (float(_one(graph, "condition").params["threshold"])
                <= float(_one(graph, "capture").params["reject"]))

    def test_notify_rides_the_watchdog_when_there_is_one(self):
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_WATCHDOG,
                                         OPT_CLOUD_DODGE, OPT_NOTIFY})
        assert _sources_of(graph, "notify") == {"condition"}

    def test_notify_falls_back_to_the_cloud_watch(self):
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_CLOUD_DODGE, OPT_NOTIFY})
        assert _legs(graph, "cloudwatch", "notify") == {("in", "do")}

    def test_notify_alone_gets_an_event_that_can_actually_fire(self):
        """With no watchdog and no cloud watch, nothing in the graph emits an
        event — the phone would simply never buzz, which is worse than having no
        alerts at all because the operator believes they are covered."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_NOTIFY})
        assert _sources_of(graph, "notify") == {"safety"}

    def test_a_dome_never_arrives_without_something_to_close_it(self):
        """DEVIATION 3, and the only DANGER-level check in the doctor. The
        prototype only ever minted a safety monitor inside the notify branch, so
        picking Dome on its own generated a flow that opens a shutter nothing
        will close on rain."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_DOME})
        assert _legs(graph, "safety", "abort") == {("unsafe", "do")}

    def test_a_safety_monitor_is_never_left_wired_to_nothing(self):
        """SAFETY MONITOR has no inputs, so doctor rule 1 cannot see one whose
        `unsafe` event goes nowhere: it would satisfy "add a monitor" while
        closing nothing."""
        for opts in ({OPT_DOME}, {OPT_NOTIFY}, {OPT_DOME, OPT_NOTIFY}):
            graph = generate(KIND_DEEP_SKY, opts | {OPT_GUIDING})
            if "safety" in _types(graph):
                safety = _one(graph, "safety")
                assert any(e.from_ == safety.id for e in graph.edges), opts

    def test_dome_and_notify_share_one_monitor(self):
        """Two monitors watching the same sky is two things to configure and
        two ways to half-configure it."""
        graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_DOME, OPT_NOTIFY})
        _one(graph, "safety")           # asserts exactly one
        assert _sources_of(graph, "notify") == {"safety"}


class TestTheGeneratedFlowCompiles:
    """A graph that passes the doctor but compiles to nothing runnable would
    still waste the night, so the generator is graded on the plan too."""

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_kind_compiles_to_targets_that_have_steps(self, kind):
        plan = compile_plan(generate(kind, DEFAULT_OPTIONS, "M16, M17"), "n")
        assert plan["targets"]
        for t in plan["targets"]:
            assert t["steps"], f"{kind}: {t['name']} has no steps"

    def test_the_full_service_night_compiles_its_whole_choreography(self):
        graph = generate(KIND_DEEP_SKY, set(AUTOMATION_OPTIONS), "M16")
        plan = compile_plan(graph, "M16")
        rules = {(i["when"], i["action"]) for i in plan["instructions"]}
        for leg in [("on_clouds_in", "holdresume"), ("on_clouds_in", "calib"),
                    ("on_clouds_clear", "holdresume"), ("on_clouds_clear", "calib"),
                    ("on_frame_graded", "condition"), ("on_hfr_above", "refocus"),
                    ("on_hfr_above", "notify"), ("on_unsafe", "abort")]:
            assert leg in rules, f"missing {leg} — got {sorted(rules)}"
        assert plan["automation"]["dome"]["on_unsafe"] == "close"
        assert plan["automation"]["dusk_flats"]["adu_target"] == 28500
        assert plan["schedule"]["start_mode"] == "dusk"

    def test_the_eaa_plan_really_does_shoot_short_frames(self):
        plan = compile_plan(generate(KIND_EAA, set(), "M27"), "n")
        step = plan["targets"][0]["steps"][0]
        assert step["exposure_s"] == 4 and step["binning"] == 2
        assert "integration_goal_h" not in step


class TestTheAnswers:
    def test_an_unknown_kind_is_refused_rather_than_guessed(self):
        """Guessing "eaa quick look" into a deep-sky lane would hand back 120s
        subs for a live view — a plausible-looking flow that is not what was
        asked for."""
        with pytest.raises(ValueError, match="unknown wizard kind"):
            generate("Quick look", DEFAULT_OPTIONS, "M27")

    def test_an_unknown_option_is_refused_rather_than_dropped(self):
        """A dropped chip gives back a night that is missing its dome while
        looking exactly like one that has it."""
        with pytest.raises(ValueError, match="unknown automation option"):
            generate(KIND_DEEP_SKY, {"Guiding ", OPT_WATCHDOG}, "M31")

    def test_the_same_answers_always_generate_the_same_graph(self):
        """Node ids included. Regenerating to compare two option sets should
        show what changed, not renumber every node."""
        a = generate(KIND_DEEP_SKY, DEFAULT_OPTIONS, "M31")
        b = generate(KIND_DEEP_SKY, DEFAULT_OPTIONS, "M31")
        assert a.model_dump() == b.model_dump()

    def test_a_generated_flow_lands_in_my_flows_and_stays_editable(self):
        """§9's toast is "every stage is editable" — a generated flow filed as a
        read-only Example would refuse the first edit."""
        rec = generate_record(KIND_DEEP_SKY, DEFAULT_OPTIONS, "M31")
        assert rec.folder == "My flows" and rec.readonly is False
        assert rec.name == "M31"
        assert "wizard" in rec.tagline

    def test_an_untyped_target_still_gets_a_findable_name(self):
        assert flow_name(KIND_DEEP_SKY, "") == "New deep-sky run"
        assert flow_name(KIND_POOL, "  ") == "Pool night"
        assert flow_name(KIND_EAA, "") == "EAA — quick look"
        assert flow_name(KIND_EAA, "M27") == "EAA — M27"

    def test_a_long_candidate_list_still_produces_a_saveable_record(self):
        """FlowRecord caps a name at 120 characters. A pool of a dozen
        candidates passes that easily, and a ValidationError would throw away a
        graph that is otherwise perfectly good."""
        rec = generate_record(KIND_POOL, DEFAULT_OPTIONS,
                              ", ".join(f"NGC {6900 + i}" for i in range(30)))
        assert len(rec.name) <= 120
        assert rec.graph.nodes

    def test_the_sheet_opens_on_answers_that_generate_a_clean_night(self):
        """The prototype's own defaults (Deep-sky + Guiding + HFR watchdog) are
        what somebody gets by pressing GENERATE FLOW without touching anything
        else."""
        assert check(generate(KIND_DEEP_SKY, DEFAULT_OPTIONS, "M31")) == []
