"""The seam between a compiled flow and a plan the engine can run.

Every test here exists because of one property of the models on the other side:
``SequencePlan``, ``Target``, ``ExposureStep`` and ``Instruction`` set no
``model_config``, so pydantic's default ``extra="ignore"`` applies and **unknown
keys are dropped in silence**.

That makes the interesting failures invisible by construction. Hand the compiled
dict straight to ``SequencePlan`` and the dusk window, the altitude floor, the
dawn stop, the dome policy and every cloud rule vanish — and what you get back
is a plan that VALIDATES CLEAN and starts a run immediately, in daylight,
forever. Nothing raises. Nothing logs.

So the assertions below are mostly of the form "this survived" and "the operator
was TOLD about that". A test that only checked the plan validates would pass on
the broken version, which is the shape this project has been bitten by often
enough to name.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import (
    GraphNotRunnable, LEGAL_ACTIONS, LEGAL_TRIGGERS, blocking_reasons,
    to_sequence_plan)


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _one_target(rotation=0, **capture):
    """A minimal runnable graph: dusk -> target -> capture.

    ``rotation`` is passed EXPLICITLY because the TARGET node's shipped default
    is 23.4 (it mirrors the M31 example), so a graph that simply omits it does
    not exercise the unset case at all — which is the whole point of the
    zero-means-no-constraint coercion.
    """
    params = {"filter": "L", "exposure": 120, "gain": 100, "bin": "1",
              "count": 10, "goal": 0}
    params.update(capture)
    g = FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name="M31", ra="00h 42m 44s",
                  dec="+41 16 09", rotation=rotation),
               _n("c", "capture", x=200, **params)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])
    return g


def _calib_on_cloud(quota: int = 20) -> FlowGraph:
    """The M16 shape, reduced to the wire under test: clouds-in starts the
    calibration queue, clouds-clear stops it."""
    return FlowGraph(
        nodes=[_n("t", "target", name="M31", ra="00h 42m 44s", dec="+41 16 09"),
               _n("c", "capture", x=100, exposure=60, count=5),
               _n("w", "cloudwatch", x=200),
               _n("q", "calib", x=300, quota=quota)],
        edges=[_e("t", "target", "c", "run"), _e("w", "in", "q", "do"),
               _e("w", "clear", "q", "stop")])


def _keys(unmapped):
    return {u["key"] for u in unmapped}


class TestTheSilentLosses:
    """Each of these would be dropped without a word by a naive hand-off."""

    def test_the_dusk_window_reaches_the_target_and_is_not_lost(self):
        """THE WORST ONE. ``SequencePlan`` has no ``schedule`` field at all, and
        ``Schedule``'s defaults are not conservative — they are "run now,
        forever, at any altitude". A dropped dusk window is a daylight slew."""
        plan, _ = to_sequence_plan(compile_plan(_one_target(), "n"))
        sched = plan.targets[0].schedule
        assert sched.start_mode == "dusk"
        assert sched.start_offset_min == -30
        assert sched.stop_mode == "dawn"
        assert sched.min_altitude_deg == 30

    def test_run_now_stays_run_now(self):
        g = _one_target()
        g = FlowGraph(nodes=[n for n in g.nodes if n.type != "dusk"],
                      edges=[e for e in g.edges if e.from_ != "d"])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert plan.targets[0].schedule.start_mode == "now"

    def test_coordinates_become_numbers(self):
        plan, _ = to_sequence_plan(compile_plan(_one_target(), "n"))
        t = plan.targets[0]
        assert t.ra_hours == pytest.approx(0 + 42 / 60 + 44 / 3600)
        assert t.dec_deg == pytest.approx(41 + 16 / 60 + 9 / 3600)

    def test_the_automation_blocks_are_all_reported(self):
        g = FlowGraph(nodes=[_n("d", "dome"), _n("f", "duskflats"),
                             _n("q", "calib"),
                             _n("t", "target", x=100, name="M31",
                                ra="00h 42m 44s", dec="+41 16 09"),
                             _n("c", "capture", x=200, exposure=60, count=5)],
                      edges=[_e("t", "target", "c", "run")])
        _, un = to_sequence_plan(compile_plan(g, "n"))
        assert {"automation.dome", "automation.dusk_flats",
                "automation.calibration_queue"} <= _keys(un)

    def test_the_dome_is_the_only_automation_loss_rated_danger(self):
        """A lost flat panel costs frames. A lost dome policy means a shutter
        the graph promised would close on unsafe does not exist at run time."""
        g = FlowGraph(nodes=[_n("d", "dome"), _n("f", "duskflats"),
                             _n("q", "calib"),
                             _n("t", "target", x=100, name="M31",
                                ra="00h 42m 44s", dec="+41 16 09"),
                             _n("c", "capture", x=200, exposure=60, count=5)],
                      edges=[_e("t", "target", "c", "run")])
        _, un = to_sequence_plan(compile_plan(g, "n"))
        danger = {u["key"] for u in un if u["level"] == "danger"}
        assert danger == {"automation.dome"}

    def test_an_integration_goal_is_reported_not_turned_into_a_frame_count(self):
        """Mapping 12 hours onto ``count`` would silently change the number of
        frames the operator typed, which is a worse lie than dropping it."""
        plan, un = to_sequence_plan(compile_plan(_one_target(goal=12), "n"))
        assert plan.targets[0].steps[0].count == 10, "the typed count survives"
        assert any(k.endswith("integration_goal_h") for k in _keys(un))


class TestNodesThatReachNothing:
    def test_inert_node_params_are_reported_from_the_graph(self):
        """They leave NO trace in the compiled dict — ``compile_plan`` branches
        on target/pool/capture and walks past the rest — so without the graph
        this whole class is unreportable."""
        g = _one_target()
        g = FlowGraph(nodes=[*g.nodes,
                             _n("g", "guide", x=300, settle=2.5),
                             _n("p", "abort", x=400, park="Yes", warm="Yes")],
                      edges=g.edges)
        _, un = to_sequence_plan(compile_plan(g, "n"), g)
        assert {"nodes.guide", "nodes.abort"} <= _keys(un)

    def test_without_the_graph_nothing_can_be_said_about_them(self):
        g = _one_target()
        g = FlowGraph(nodes=[*g.nodes, _n("g", "guide", x=300, settle=2.5)],
                      edges=g.edges)
        _, un = to_sequence_plan(compile_plan(g, "n"))          # no graph
        assert "nodes.guide" not in _keys(un)


class TestInstructions:
    def test_a_supported_rule_survives_with_its_threshold(self):
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=60, count=5),
                   _n("k", "condition", x=200, when="HFR above", threshold=3.2),
                   _n("r", "refocus", x=300)],
            edges=[_e("t", "target", "c", "run"), _e("k", "fire", "r", "do")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert len(plan.instructions) == 1
        assert plan.instructions[0].trigger == "on_hfr_above"
        assert plan.instructions[0].action == "refocus"
        assert plan.instructions[0].threshold == 3.2

    def test_holdresume_pause_becomes_the_SELF_RELEASING_hold(self):
        """It used to map to `pause`, and that was the wrong engine capability
        rather than merely the wrong word — see the note on PORTED_ACTIONS."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=60, count=5),
                   _n("k", "condition", x=200, when="HFR above", threshold=3),
                   _n("h", "holdresume", x=300)],
            edges=[_e("t", "target", "c", "run"), _e("k", "fire", "h", "pause")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert [i.action for i in plan.instructions] == ["hold_for_clear"]

    def test_a_cloud_rule_now_REACHES_THE_ENGINE(self):
        """This test used to assert the opposite, and the change is the feature.

        Until the engine learned the sky, `on_clouds_in` was refused by
        `Instruction` and this adapter dropped it with a danger note. The
        trigger exists now, `TriggerContext` carries a tri-state `cloudy`, and
        the hold is a real engine action - so the rule the operator drew is the
        rule that runs."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=60, count=5),
                   _n("w", "cloudwatch", x=200), _n("h", "holdresume", x=300)],
            edges=[_e("t", "target", "c", "run"), _e("w", "in", "h", "pause")])
        plan, un = to_sequence_plan(compile_plan(g, "n"))
        assert [(i.trigger, i.action) for i in plan.instructions] \
            == [("on_clouds_in", "hold_for_clear")]
        # Scoped to the RULE, not to the string "on_clouds_in" anywhere in a
        # key. The broad version also swallowed the note about the CLOUD WATCH
        # node's dead threshold dial — a different and still-true loss — so it
        # would have kept that quiet as the price of asserting this one.
        assert not [u for u in un if u["key"] == "instructions[on_clouds_in]"
                    or "will not run" in u.get("detail", "")], \
            "nothing should still be warning that this rule does not run"

    def test_the_hold_is_NOT_compiled_to_a_plain_pause(self):
        """`pause()` blocks the frame loop above the dawn boundary, the safety
        gate and the dead-man ping, and a paused loop has no frame boundaries
        for a resume rule to fire at. A cloud rule compiled to `pause` would
        sit through sunrise with the watchdog silent."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=60, count=5),
                   _n("w", "cloudwatch", x=200), _n("h", "holdresume", x=300)],
            edges=[_e("t", "target", "c", "run"), _e("w", "in", "h", "pause")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert plan.instructions[0].action != "pause"

    def test_the_resume_wire_is_reported_as_REDUNDANT_not_broken(self):
        """The hold releases itself. Telling an operator their resume wire "will
        not run" would be a lie - the run does resume, the hold does it - so it
        is a note, not a loss."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=60, count=5),
                   _n("w", "cloudwatch", x=200), _n("h", "holdresume", x=300)],
            edges=[_e("t", "target", "c", "run"),
                   _e("w", "clear", "h", "resume")])
        _, un = to_sequence_plan(compile_plan(g, "n"))
        note = [u for u in un if "holdresume.resume" in u["key"]]
        assert note, "the wire must be acknowledged, not ignored"
        assert note[0]["level"] == "warn"
        assert "releases itself" in note[0]["detail"]

    def test_a_cloud_wired_calib_is_HONOURED_by_the_hold_not_lost(self):
        """It was reported at DANGER as "this rule will not run", and that
        sentence was false on the shipped M16 example.

        The queue's quota reaches the plan as `cloud_hold_darks`, and the hold
        spends its dead time shooting darks matched to the step it interrupted.
        So the operator's wire IS answered - by the hold rather than by a rule -
        and calling it broken sends someone to debug a feature that works."""
        g = _calib_on_cloud()
        plan, un = to_sequence_plan(compile_plan(g, "n"))
        assert plan.cloud_hold_darks > 0, "the darks the note is about"
        note = [u for u in un if u["key"] == "instructions[on_clouds_in -> calib.do]"]
        assert note, "the wire must still be acknowledged, not silently dropped"
        assert note[0]["level"] == "warn", "not a danger: the darks are taken"
        assert "will not run" not in note[0]["detail"]
        assert "bias and flat legs are not" in note[0]["detail"], (
            "the half that IS lost has to stay in the sentence")

    def test_the_same_wire_with_NO_QUOTA_is_still_a_loss(self):
        """The redundancy claim is only true because darks get taken. At quota 0
        the hold shoots nothing, and repeating "already honoured" there would be
        the same overclaim in the other direction."""
        g = _calib_on_cloud(quota=0)
        plan, un = to_sequence_plan(compile_plan(g, "n"))
        assert plan.cloud_hold_darks == 0
        note = [u for u in un if "-> calib" in u["key"]]
        assert note and "will not run" in note[0]["detail"]

    def test_a_calib_fired_by_something_OTHER_than_cloud_is_still_a_loss(self):
        """The campaign's day-darks lane hangs off `on_shutdown_complete`, and
        no hold covers that. Keying the redundancy on the node type alone would
        have traded one wrong sentence for another."""
        camp = next(e for e in examples() if "Campaign" in e.name)
        _, un = to_sequence_plan(compile_plan(camp.graph, camp.name), camp.graph)
        shutdown = [u for u in un
                    if u["key"].startswith("instructions[on_shutdown_complete")]
        assert shutdown and "will not run" in shutdown[0]["detail"]

    def test_a_condition_passthrough_row_is_NOT_reported(self):
        """The capture->condition edge compiles to a bogus ``action:"condition"``
        row that duplicates edges already represented. Listing it would train
        operators to ignore the list."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=60, count=5),
                   _n("k", "condition", x=200, when="HFR above", threshold=3),
                   _n("r", "refocus", x=300)],
            edges=[_e("t", "target", "c", "run"), _e("c", "frame", "k", "events"),
                   _e("k", "fire", "r", "do")])
        _, un = to_sequence_plan(compile_plan(g, "n"))
        assert not [u for u in un if "-> condition" in u["key"]]

    def test_the_legal_vocabularies_are_READ_from_the_engine(self):
        """Not retyped. A hand-copied list is a claim that stops being true the
        day the enum is widened — and it was widened, which is exactly why this
        assertion changed rather than the code around it.

        The earlier version asserted `on_clouds_in` was ABSENT and carried a
        message telling its future reader to delete the adapter's reporting when
        that stopped being true. It stopped being true today."""
        assert "on_hfr_above" in LEGAL_TRIGGERS
        assert {"on_clouds_in", "on_clouds_clear", "on_unsafe",
                "on_panel_ready"} <= LEGAL_TRIGGERS, (
            "the sky triggers must be read from TriggerKind, never restated")
        assert {"pause", "refocus", "abort", "notify",
                "hold_for_clear"} <= LEGAL_ACTIONS


class TestRefusals:
    def test_a_capture_with_no_exposure_is_a_GRAPH_error(self):
        """``_num`` returns 0 for a missing param and ``ExposureStep`` has
        ``gt=0``, so a half-filled node arrives here. The editor can point at it;
        a 500 cannot."""
        with pytest.raises(GraphNotRunnable) as exc:
            to_sequence_plan(compile_plan(_one_target(exposure=0), "n"))
        assert exc.value.code == "invalid_graph"

    def test_a_capture_with_no_frames_is_too(self):
        with pytest.raises(GraphNotRunnable):
            to_sequence_plan(compile_plan(_one_target(count=0), "n"))

    def test_an_unresolvable_target_is_DROPPED_never_given_fake_coordinates(self):
        """(0, 0) validates fine and is below the horizon at most sites, so the
        operator would get a horizon refusal naming a target they never typed."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="", ra="not a coordinate", dec="??"),
                   _n("c", "capture", x=100, exposure=60, count=5)],
            edges=[_e("t", "target", "c", "run")])
        with pytest.raises(GraphNotRunnable):
            to_sequence_plan(compile_plan(g, "n"))

    def test_one_bad_target_does_not_take_the_good_one_with_it(self):
        g = FlowGraph(
            nodes=[_n("a", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("b", "target", x=50, name="", ra="??", dec="??"),
                   _n("c", "capture", x=100, exposure=60, count=5)],
            edges=[_e("a", "target", "c", "run")])
        plan, un = to_sequence_plan(compile_plan(g, "n"))
        assert [t.name for t in plan.targets] == ["M31"]
        assert any(u["level"] == "danger" and u["key"].startswith("targets[")
                   for u in un)


class TestRotation:
    def test_a_zero_rotation_becomes_no_constraint(self):
        """``Target.rotation_deg=0`` commands the rotator to PA 0 and adds 300 s
        of goto timeout on every target, every night — for a field the operator
        never touched."""
        plan, un = to_sequence_plan(compile_plan(_one_target(), "n"))
        assert plan.targets[0].rotation_deg is None
        assert any(k.endswith("rotation_deg") for k in _keys(un)), (
            "the coercion must be reported — an operator who genuinely meant "
            "PA 0 has to be able to see it was read as 'unset'")

    def test_a_real_rotation_survives_untouched(self):
        plan, un = to_sequence_plan(compile_plan(_one_target(rotation=23.4), "n"))
        assert plan.targets[0].rotation_deg == 23.4
        assert not any(k.endswith("rotation_deg") for k in _keys(un))


class TestPools:
    def test_members_resolve_by_name_and_carry_their_constraints(self):
        g = FlowGraph(
            nodes=[_n("p", "pool", members="M16, M17", minAlt=30, moonSep=40,
                      maxHA=4),
                   _n("c", "capture", x=100, exposure=60, count=5)],
            edges=[_e("p", "target", "c", "run")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert [t.name for t in plan.targets] == ["M16", "M17"]
        assert all(t.ra_hours > 0 for t in plan.targets), "resolved by name"
        s = plan.targets[0].schedule
        assert (s.min_altitude_deg, s.min_moon_sep_deg, s.max_hour_angle_h) == \
               (30, 40, 4)

    def test_pool_members_skip_a_missed_window_instead_of_blocking_the_night(self):
        """Under the default ``wait``, member 1 blocks the whole night waiting
        for a window it may never get, and members 2-4 — the entire point of a
        pool — never run."""
        g = FlowGraph(
            nodes=[_n("p", "pool", members="M16, M17"),
                   _n("c", "capture", x=100, exposure=60, count=5)],
            edges=[_e("p", "target", "c", "run")])
        plan, un = to_sequence_plan(compile_plan(g, "n"))
        assert all(t.schedule.on_missed == "skip" for t in plan.targets)
        assert "targets[*].pool_rank" in _keys(un), (
            "the approximation is worth having; pretending it is a pool is not")

    def test_a_plain_target_still_waits(self):
        plan, _ = to_sequence_plan(compile_plan(_one_target(), "n"))
        assert plan.targets[0].schedule.on_missed == "wait"

    def test_the_pools_own_altitude_floor_beats_the_nights(self):
        """The more specific statement wins."""
        g = FlowGraph(
            nodes=[_n("d", "dusk", minAlt=30),
                   _n("p", "pool", x=100, members="M16", minAlt=45),
                   _n("c", "capture", x=200, exposure=60, count=5)],
            edges=[_e("d", "window", "p", "arm"), _e("p", "target", "c", "run")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert plan.targets[0].schedule.min_altitude_deg == 45


class TestBlockingReasons:
    def test_a_dome_policy_blocks_only_when_a_dome_is_actually_connected(self):
        """The refusal is about a physical shutter. With no dome attached there
        is no roof to leave open — and refusing anyway would stop the shipped
        M16 example running on the simulator, which the handoff's definition of
        done explicitly requires."""
        g = FlowGraph(nodes=[_n("d", "dome"),
                             _n("t", "target", x=100, name="M31",
                                ra="00h 42m 44s", dec="+41 16 09"),
                             _n("c", "capture", x=200, exposure=60, count=5)],
                      edges=[_e("t", "target", "c", "run")])
        _, un = to_sequence_plan(compile_plan(g, "n"))
        assert blocking_reasons(un, dome_connected=False) == []
        assert len(blocking_reasons(un, dome_connected=True)) == 1

    def test_nothing_else_blocks_however_bad_it_is(self):
        """A month-long campaign the engine cannot run is a danger the operator
        must SEE, not a reason to refuse tonight's imaging.

        This used the M16 example until the cloud-wired calib rules stopped
        being reported as losses - correctly, since the hold takes their darks -
        which left M16 with a single danger and nothing for this test to prove.
        The campaign example carries two unrelated ones, so the property is
        still demonstrated by a graph rather than by a contrivance."""
        camp = next(e for e in examples() if "Campaign" in e.name)
        _, un = to_sequence_plan(compile_plan(camp.graph, camp.name), camp.graph)
        dangers = [u["key"] for u in un if u["level"] == "danger"]
        assert len(dangers) > 1 and "campaign" in dangers
        assert [u["key"] for u in blocking_reasons(un, dome_connected=True)] == \
               ["automation.dome"]


class TestTheShippedExamples:
    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_each_one_becomes_a_plan_the_engine_could_run(self, ex):
        plan, unmapped = to_sequence_plan(compile_plan(ex.graph, ex.name),
                                          ex.graph)
        assert plan.targets, f"{ex.name} produced no targets"
        for t in plan.targets:
            assert t.steps, f"{ex.name}: {t.name} has no steps"
            assert -90 <= t.dec_deg <= 90 and 0 <= t.ra_hours < 24
        assert all(u["key"] and u["detail"] and u["level"] in ("warn", "danger")
                   for u in unmapped)

    def test_the_m16_example_holds_for_cloud_and_aborts_on_unsafe(self):
        """The handoff's definition of done asks for this choreography to run
        from real engine events. The weather half does now.

        The calibration-during-hold half is answered too, by the hold rather
        than by a rule: the queue's quota becomes `cloud_hold_darks` and the
        hold shoots darks matched to the step it interrupted. This assertion
        used to demand those wires be reported as LOSSES, which made a false
        sentence a test-enforced requirement. What is genuinely still missing -
        the queue's order, and its bias and flat legs - is reported once, on the
        automation block, where it belongs."""
        m16 = next(e for e in examples() if e.id == "example-m16")
        plan, un = to_sequence_plan(compile_plan(m16.graph, m16.name), m16.graph)
        got = {(i.trigger, i.action) for i in plan.instructions}
        assert ("on_clouds_in", "hold_for_clear") in got, "the hold runs"
        assert ("on_unsafe", "abort") in got, "the safety abort runs"
        assert ("on_clouds_in", "notify") in got, "the alert runs"
        assert plan.cloud_hold_darks > 0, "the hold has darks to shoot"
        assert not [u for u in un
                    if "-> calib" in u["key"] and "will not run" in u["detail"]], (
            "no cloud-wired calib rule may claim it will not run while the hold "
            "is taking its darks")
        assert [u for u in un if u["key"] == "automation.calibration_queue"], (
            "the bias and flat legs are still lost and must keep saying so")

    def test_the_eaa_example_starts_now_and_keeps_its_short_subs(self):
        eaa = next(e for e in examples() if e.id == "example-eaa")
        plan, _ = to_sequence_plan(compile_plan(eaa.graph, eaa.name), eaa.graph)
        assert plan.targets[0].schedule.start_mode == "now"
        step = plan.targets[0].steps[0]
        assert step.exposure_s == 4 and step.gain == 300 and step.binning == 2
