# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

import hashlib
import json

import pytest

from astrodeck.flows.compile import compile_plan, is_multi_panel
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import (
    GraphNotRunnable, LEGAL_ACTIONS, LEGAL_TRIGGERS, NODE_SETTINGS,
    blocking_reasons, to_sequence_plan)


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _one_target(rotation=0, **capture):
    """A minimal runnable graph: dusk -> target -> capture.

    ``rotation`` is passed EXPLICITLY so every test states the angle it means.
    The TARGET node's shipped default was 23.4 (it mirrored the M31 example)
    until #150 made it -1, "any angle"; a test that leaned on the default
    would have changed meaning with it, silently, which is the class of the
    zero-means-no-constraint coercion these tests exist for.
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

    def test_clock_time_start_and_stop_reach_the_target_s_schedule(self):
        """WP-09 follow-up (#191, backlog W1). ``to_plan.SCHEDULE_KEYS``
        left ``start_time``/``stop_time`` out of the tuple it filters the
        compiled ``schedule`` dict through, so even after
        ``compile._dusk_schedule`` learned to emit them for a Clock-time
        Start or Stop, they were dropped right back out before ever
        reaching the running ``Target.schedule`` -- a card that read "Clock
        time" compiled a `start_mode`/`stop_mode` of "time" with no time on
        the plan the engine actually runs.

        RED under mutant "the two keys dropped again" (``SCHEDULE_KEYS``
        reverted to its four original names), observed:

            AssertionError: 'start_time'
        """
        g = FlowGraph(
            nodes=[_n("d", "dusk", start="Clock time", startClock="20:15",
                      offset=-30, stop="Clock time", stopClock="05:30",
                      minAlt=30),
                   _n("t", "target", x=100, name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=200, filter="L", exposure=120,
                      gain=100, bin="1", count=10, goal=0)],
            edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        sched = plan.targets[0].schedule
        assert sched.start_mode == "time"
        assert sched.start_time == "20:15"
        assert sched.stop_mode == "time"
        assert sched.stop_time == "05:30"

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
        assert note[0]["level"] == "note", (
            "this docstring has said 'a note, not a loss' since it was "
            "written, while the assertion under it pinned 'warn' - the level "
            "did not exist in to_plan until the campaign's own loop-back wire "
            "needed it too")
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
        assert note[0]["level"] == "note", "not a loss at all: the darks are taken"
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

    def test_the_campaign_note_does_not_claim_a_loss_that_is_not_one(self):
        """It said the run "images ONE night and stops at dawn", that "the
        capture cursor is not persisted", that "no target is marked done" and
        that the flow "will not re-arm at the next dusk". Three of those four
        were false when they were written.

        `test_campaign_across_nights.py` runs the machinery and shows the
        opposite: dormant + armed, ledger-seeded, finished targets skipped. An
        operator told their campaign will not work does not run it, so an
        over-reported loss costs exactly as much as a hidden one."""
        camp = next(e for e in examples() if "Campaign" in e.name)
        _, un = to_sequence_plan(compile_plan(camp.graph, camp.name), camp.graph)
        note = [u for u in un if u["key"] == "campaign"]
        assert note, "a campaign must still be called out"
        detail = note[0]["detail"]
        assert note[0]["level"] == "warn", (
            "the campaign runs; only its stop condition is approximate")
        for lie in ("cursor is not persisted", "will not re-arm",
                    "images ONE night", "no target is marked done"):
            assert lie not in detail, f"the note still claims {lie!r}"
        assert "picking up from the frame ledger" in detail

    def test_a_calib_fired_by_a_trigger_WITH_NO_LANE_is_still_a_loss(self):
        """The reason HOLD_HONOURED is keyed on the TRIGGER as well as the port:
        the same CALIB node fed from a different edge is a different promise,
        and keying on node type alone would trade one wrong sentence for
        another.

        This used to use `on_shutdown_complete` as its example, because nothing
        covered that edge. The day-darks lane now does - it runs between the
        park and the warm - so the example moved to a trigger with no lane
        behind it rather than the assertion being relaxed. What is being pinned
        is the DISCRIMINATION, not any particular trigger's fate.
        """
        g = _calib_on_cloud()
        compiled = compile_plan(g, "n")
        # Re-point the calib wire at a trigger no lane answers.
        for rule in compiled.get("instructions") or []:
            if rule.get("action") == "calib":
                rule["when"] = "on_frame_rejected"
        _, un = to_sequence_plan(compiled, g)
        rows = [u for u in un
                if u["key"].startswith("instructions[on_frame_rejected")]
        assert rows and "will not run" in rows[0]["detail"], (
            f"a calib wired to a trigger with no lane behind it was called "
            f"honoured: {rows}")

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
    def test_zero_is_a_position_angle_not_a_sentinel(self):
        """PA 0 IS NORTH UP — the angle most people frame at and the one a
        mosaic is planned around. It used to be the "no constraint" sentinel, so
        the one value an operator was most likely to want was the one value the
        field could not express, and every flow carried a permanent advisory
        line because the sentinel WAS the default.

        Changed 2026-08-18 on the operator's call: negative means no
        constraint, everything else is an angle."""
        plan, un = to_sequence_plan(compile_plan(_one_target(rotation=0), "n"))
        assert plan.targets[0].rotation_deg == 0
        assert not any(k.endswith("rotation_deg") for k in _keys(un)), (
            "a plain answer should not cost an advisory")

    def test_negative_is_the_no_constraint_sentinel(self):
        """A rotator cannot be commanded to a negative position angle, so no
        real value is displaced. -1 is what the UI writes for "any angle"."""
        for value in (-1, -0.5, -90):
            plan, un = to_sequence_plan(
                compile_plan(_one_target(rotation=value), "n"))
            assert plan.targets[0].rotation_deg is None, value
            assert not any(k.endswith("rotation_deg") for k in _keys(un)), value

    def test_an_empty_angle_box_does_not_command_the_rotator(self):
        """The trap this change could have introduced. `_num` defaulted to 0, so
        once 0 stopped meaning "unset" an unparseable or blank field would have
        silently commanded PA 0 on every target of every flow. It defaults to -1
        now."""
        for blank in ("", None, "not a number"):
            plan, _ = to_sequence_plan(
                compile_plan(_one_target(rotation=blank), "n"))
            assert plan.targets[0].rotation_deg is None, repr(blank)

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
        """A lost weather rule is a danger the operator must SEE, not a reason
        to refuse a night's imaging. Only the roof blocks.

        NO GRAPH AT ALL, and it took three failures to get here. This leaned on
        M16, then on the campaign example, then on a hand-built graph whose
        second danger was a cloud-wired PARK + CLOSE - and each of the three
        stopped carrying a second danger as the notes got more accurate, so a
        test about `blocking_reasons` kept failing for reasons that had nothing
        to do with `blocking_reasons`.

        `blocking_reasons` takes a list of dicts. Handing it the list directly
        is the only version that cannot drift when some unrelated wire gets
        reclassified, which will keep happening - that is the whole direction of
        travel in this module."""
        un = [
            {"key": "automation.dome", "detail": "roof", "level": "danger"},
            {"key": "instructions[on_unsafe -> abort]", "detail": "weather",
             "level": "danger"},
            {"key": "automation.dusk_flats", "detail": "flats", "level": "warn"},
        ]
        assert [u["key"] for u in blocking_reasons(un, dome_connected=True)] == \
               ["automation.dome"], (
            "a lost weather rule is a danger the operator must SEE, not a "
            "reason to refuse a night's imaging - only the roof blocks")


class TestTheShippedExamples:
    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_each_one_becomes_a_plan_the_engine_could_run(self, ex):
        plan, unmapped = to_sequence_plan(compile_plan(ex.graph, ex.name),
                                          ex.graph)
        assert plan.targets, f"{ex.name} produced no targets"
        for t in plan.targets:
            assert t.steps, f"{ex.name}: {t.name} has no steps"
            assert -90 <= t.dec_deg <= 90 and 0 <= t.ra_hours < 24
        # All three of the doctor's levels. `note` is not a quieter warning: it
        # says the drawn thing IS honoured, somewhere else in the engine.
        assert all(u["key"] and u["detail"]
                   and u["level"] in ("warn", "danger", "note")
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


#: Every Example with no multi-panel block: all but the eighth (S3-W).
_NO_MOSAIC = [e for e in examples()
              if not any(is_multi_panel(n) for n in e.graph.nodes)]


class TestPlansWithNoMosaicMoveOnlyByTheirCentring:
    """THE CONTROL FOR S3's COMPILE PATH (#170, #189). Every TARGET now tells
    the run its centring, and nothing else about a plan with no multi-panel
    block may move: not a step, not a schedule, not an id.

    Each hash below is ``sha256(json.dumps(dump, sort_keys=True))`` of the
    Example's plan, compiled with the flow id ``control-<example id>`` so the
    target and step ids are in it, instruction ids blanked (uuid4 on every
    compile), and ``center_tolerance_arcmin`` / ``center_attempts`` reset to
    None on every target. Captured by running the same code against
    ``compile.py`` and ``to_plan.py`` as they stood BEFORE this change (sha256
    fda9f471... and 24576818..., scratchpad s3-cp/pre), and the dump of the
    changed code matches every one of them.

    Mutant "one more field on every Target" (the plain target also gets
    ``autofocus_skip_if_fresh = True``), observed for every Example with a
    TARGET, the first of the five:

        AssertionError: example-m31 moved beyond its centring
        assert '6d62fa81320b...307a84c65a1bc' == '06c06c24b384...bfb8228a430f7'

    RE-PINNED IN THE INTEGRATION OF S3, deliberately, for two changes S3-W
    made to the Examples and nothing else:

    * THE EIGHTH EXAMPLE, example-m31-mosaic, is a 3x2 block: a plan WITH a
      mosaic, which this class is not about. The corpus is every Example with
      no multi-panel block (``_NO_MOSAIC``), and the one left out is named.
    * EVERY EXAMPLE COUNTS ACCEPTED SUBS (Revision 2 ruling 2: a created block
      counts them, and the Examples are what a person would create). So
      ``count_mode`` is reset to "attempts", its value when the hashes were
      captured, exactly as the centring is reset, and the deliberate half
      below asserts "accepted" on every plan. Checked: with that reset, all
      seven hashes match the current code unchanged.

    RE-PINNED AGAIN IN BACKLOG WP-09 (#191, 2026-09-30), deliberately, for
    one change and nothing else: every Example's DUSK WINDOW picks "Astro
    dusk" (the unset-Start default), which now compiles its own
    ``Schedule.twilight_deg`` (-18) onto every TARGET (`compile.
    _dusk_schedule`, `to_plan.SCHEDULE_KEYS`) — a field the pre-fix dump did
    not have at all, so all seven hashes move together. Checked: regenerated
    from the SAME ``_dump`` below, against the fixed code, with no other
    change to the Examples or the dump's own resets.

    RE-PINNED AGAIN IN BACKLOG WP-34 (#195, 2026-09-30), deliberately, for
    one change and nothing else: ``SequencePlan`` gained
    ``resume_across_nights``, a field every one of these seven dumps now
    carries (``plan.model_dump`` writes every field, not only the ones
    ``compile_plan`` chose to write), so all seven hashes move together
    again, whatever its own value (True for example-campaign and
    example-eaa, whose DUSK WINDOWs are not "Single night"; False for the
    other five, #195: "Single night" means auto-resume does not arm across
    nights). Diffed against the pre-change dump for each of the seven and
    confirmed this one key is the only thing that moved. Regenerated from
    the SAME ``_dump`` below, against the fixed code, with no other change.
    """

    BEFORE = {
        "example-campaign":
            "dc0a0db5532cc3330f71da63dc7319e99c0f27d1ab0a92558d0e6cbb1d9b16f5",
        "example-m31":
            "e8c802454880bea53e7fe9d26674fd3ba2fb6aa8b8f1169b9cadbe89d421c66d",
        "example-m16":
            "e2c5f6ca44a99380183d839c3559678e120c2d5da6e82c326c30fe6cc3e906c6",
        "example-cycle":
            "209ed15c4dc3d14bbd5c9ad33f5899b05a3843e0d9b5b14f49525e9fde09fe07",
        "example-pool":
            "6e9f8d1c78c7f36bd13cdd0d13f6563b10ae3d4afbe63d7f53d55c4b219d97a1",
        "example-nb":
            "35cb51dd58cb87dd9fb0e8a52835a2bf00469344e0c4ae0562deaec4fbabee89",
        "example-eaa":
            "4b05df067fcbb2baa4f627f98d946936faa73cb6dd6d1361ccad5d29cfa25ab0",
    }

    @staticmethod
    def _dump(rec) -> tuple[str, list, str]:
        plan, _ = to_sequence_plan(compile_plan(rec.graph, rec.name),
                                   rec.graph, flow_id="control-" + rec.id)
        got = plan.model_dump(mode="json")
        for ins in got["instructions"]:
            ins["id"] = ""
        centre = []
        for t in got["targets"]:
            centre.append((t.pop("center_tolerance_arcmin"),
                           t.pop("center_attempts")))
            t["center_tolerance_arcmin"] = t["center_attempts"] = None
        count_mode = got["count_mode"]
        got["count_mode"] = "attempts"
        blob = json.dumps(got, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest(), centre, count_mode

    def test_the_corpus_is_every_example_with_no_mosaic(self):
        assert set(self.BEFORE) == {e.id for e in _NO_MOSAIC}
        assert {e.id for e in examples()} - set(self.BEFORE) == {
            "example-m31-mosaic"}

    @pytest.mark.parametrize("ex", _NO_MOSAIC, ids=lambda e: e.id)
    def test_an_example_moves_only_by_its_centring(self, ex):
        digest, _centre, _mode = self._dump(ex)
        assert digest == self.BEFORE[ex.id], (
            f"{ex.id} moved beyond its centring")

    @pytest.mark.parametrize("ex", _NO_MOSAIC, ids=lambda e: e.id)
    def test_every_example_counts_accepted_subs(self, ex):
        """The other deliberate half: the count mode the hash resets.

        Mutant "every plan counts attempts" (in a private scratch copy of
        to_plan.py, the ``if _count_mode(built, unmapped) == "accepted":``
        that sets ``fields["count_mode"]`` made ``if False:``), observed for
        all seven:

            AssertionError: example-campaign counts 'attempts'
            assert 'attempts' == 'accepted'
        """
        _digest, _centre, mode = self._dump(ex)
        assert mode == "accepted", f"{ex.id} counts {mode!r}"

    @pytest.mark.parametrize("ex", _NO_MOSAIC, ids=lambda e: e.id)
    def test_the_centring_is_the_targets_own_and_a_pool_member_has_none(
            self, ex):
        """The deliberate half: every TARGET carries the hub's own 1.2 arcmin
        and 3 tries, which is what it centred to before, and a POOL member
        carries nothing, so its call is the hub's default as it was.

        Mutant "centring never reaches the Target" (``to_plan._centring``
        returns ``{}``), observed:

            AssertionError: assert [(None, None)] == [(1.2, 3)]
        """
        _digest, centre, _mode = self._dump(ex)
        want = [(None, None) if n.type == "pool" else (1.2, 3)
                for n in ex.graph.nodes if n.type in ("target", "pool")
                for _ in (str(n.params.get("members") or "x").split(",")
                          if n.type == "pool" else ["x"])]
        assert centre == want


class TestTheLegacySlewNote:
    def test_the_slew_card_points_at_the_targets_centring(self):
        """Spec 1.7: SLEW + CENTER is part of the TARGET block now, so where
        the real tolerance lives is the TARGET's CENTRING section, not "the
        run's own centring loop", and its own numbers are named as never
        having reached a run.

        Mutant "the note still names the run's loop" (the pre-S3 ``source``
        and ``detail`` restored), observed:

            AssertionError: assert "the TARGET block's CENTRING" in "the run's own centring loop (1.2 arcmin, at most 3 attempts, the rig's configured solver)"
        """
        note = NODE_SETTINGS["slew"]
        detail, _carried, ignored, source = note.render(
            {"tol": 0.5, "retries": 3, "solver": "ASTAP"})
        assert "the TARGET block's CENTRING" in source
        assert "the 0.5 arcmin tolerance" in ignored
        assert "never reached the run" in detail
        assert "set centring on the TARGET" in detail
