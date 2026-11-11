# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A night run from a stored session names its lane nodes (#1231, WP 10.1
part b; item 1 of the issue, for the nodes part b adds).

``Target.lane`` is excluded from every dump, as ``Target.block`` and
``ExposureStep.stage`` are, so a plan read back from a session file would name
no SLEW, AUTOFOCUS or GUIDE node and a resumed night would lose them again.
The session record keeps them beside the plan, as it keeps the stages and
blocks: ``flow_lanes`` ``{target_id: [[node_id, kind], ...]}``, written by
``SessionStore.save`` from the plan being saved and applied when a ``Session``
is built (every reader, so every route and ResumeArm).

A session CREATED on 0.3.49 has no ``flow_lanes``: it loads, runs and names its
stages and blocks as before, names no lane node until a CONTINUE replans it
from its flow, and nothing is rewritten on load.

The clocks: the resumed runs use the simulator's real clock with 50 ms
exposures and ResumeArm's window opened by hand; no test here reads the Sun, a
target's window or the time zone.

MUTANT (named in the brief), from a byte backup of ``sequence/session.py``:

  M1  ``note_stages`` records no ``flow_lanes`` (the lane map dropped from the
      session record): the resumed night names no lane node.
"""
from __future__ import annotations

import json

import pytest

from _flow_night import FlowRig, flow_rig  # noqa: F401
from _group_harness import group_store  # noqa: F401
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.models import LaneNode
from astrodeck.sequence.session import Session, session_store

from test_1231_resumed_night_names_its_stages import (  # noqa: F401
    BLOCK, STAGE_L, STAGE_R, Rig, banked, names_of, rig, staged_plan, store,
    stored_file, wait_for)
from test_w10_1_lane_nodes import lane_flow, night_of, lane_of
from test_w4_2_stage_status import stage_events

LANE = [("nS", "slew"), ("nA", "autofocus"), ("nG", "guide")]
ALL_DONE = {BLOCK: "done", STAGE_L: "done", STAGE_R: "done",
            "nS": "done", "nA": "done", "nG": "done"}


def laned_plan(name: str = "Laned", count: int = 3) -> SequencePlan:
    """``staged_plan`` with the lane a flow's compile leaves on the target."""
    plan = staged_plan(name, count)
    plan.targets[0].lane = [LaneNode(id=i, kind=k) for i, k in LANE]
    return plan


def lanes_of(plan: SequencePlan) -> dict[str, list[tuple[str, str]]]:
    return {t.id: lane_of(t) for t in plan.targets}


# ------------------------------------------------------------- the record

def test_the_session_file_carries_the_lane_and_the_plan_dump_does_not(store):
    """The lane is written beside the plan from the plan being saved, and the
    plan inside the file is exactly what it was before any lane existed."""
    plan = laned_plan()
    s = Session(name="n", created_ts=1.0, status="dormant", plan=plan)
    store.save(s)
    raw = stored_file(s.id)
    assert raw["flow_lanes"] == {
        plan.targets[0].id: [list(p) for p in LANE]}
    for t in raw["plan"]["targets"]:
        assert "lane" not in t
    bare = laned_plan()
    bare.targets[0].lane = []
    bare.targets[0].id = plan.targets[0].id
    for a, b in zip(bare.targets[0].steps, plan.targets[0].steps):
        a.id = b.id
    assert raw["plan"] == json.loads(json.dumps(bare.model_dump(mode="json")))


def test_a_stored_session_is_built_with_its_plan_naming_the_lane(store):
    plan = laned_plan()
    s = Session(name="n", created_ts=1.0, status="dormant", plan=plan)
    store.save(s)
    assert lanes_of(store.load(s.id).plan) == lanes_of(plan)


def test_hand_edited_lanes_cannot_make_the_session_unreadable(store):
    """A value that is not a lane is ignored, as a hand edit of
    ``flow_blocks`` is: a file that fails validation vanishes from every
    scan, ledger and all."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=laned_plan())
    store.save(s)
    tid = s.plan.targets[0].id
    for bad in ({tid: 7}, {tid: [["nS"]]}, {tid: [["nS", "dome"]]},
                {tid: [[1, "slew"]]}, {tid: [["", "slew"]]}, {tid: "slew"},
                {"nobody": [["a", "slew"]]},
                {tid: [["nS", "slew", "extra"]]}):
        raw = stored_file(s.id)
        raw["flow_lanes"] = bad
        store._path(s.id).write_text(json.dumps(raw), encoding="utf-8")
        loaded = store.load(s.id)
        assert loaded is not None, bad
        assert loaded.plan.targets[0].lane == [], bad


def test_a_plan_replaced_by_one_with_no_lane_forgets_the_lane(store):
    """The Plan editor's PATCH replaces the stored plan with one no flow
    compiled: the record is rewritten from the plan it now holds."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=laned_plan())
    store.save(s)
    held = store.load(s.id)
    held.plan = staged_plan()
    store.save(held)
    assert stored_file(s.id)["flow_lanes"] == {}
    assert store.load(s.id).plan.targets[0].lane == []


def test_a_session_file_from_0_3_49_runs_as_it_did(store):
    """R: a file written by 0.3.49 has ``flow_blocks`` and ``flow_stages`` and
    no ``flow_lanes``. It loads, its plan names its stage and block as before
    and no lane node, the file is not rewritten by reading it, and saving it
    writes ``flow_lanes: {}``.

    RED if loading demanded the key, or rewrote the file, or the new key
    broke the stage names the old record carries."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=laned_plan())
    store.save(s)
    raw = stored_file(s.id)
    del raw["flow_lanes"]
    path = store._path(s.id)
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()

    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.plan.targets[0].lane == []
    assert names_of(loaded.plan) == names_of(s.plan)
    assert loaded.plan.targets[0].block == BLOCK
    assert {x.stage for x in loaded.plan.targets[0].steps} == {
        STAGE_L, STAGE_R}
    assert store.load_all() and path.read_bytes() == before, (
        "reading a 0.3.49 session must not rewrite it")

    store.save(loaded)
    after = stored_file(s.id)
    assert after["flow_lanes"] == {}
    assert after["flow_blocks"] == raw["flow_blocks"]
    assert after["flow_stages"] == raw["flow_stages"]


# ---------------------------------------------------------- the resumed run

async def test_a_resumed_night_names_its_lane_nodes(rig):
    """Night one runs a laned plan and is stopped by hand; night two is
    ResumeArm's auto-resume of the stored session, on a fresh engine. Night
    two publishes the lane nodes night one did, and says each done at the
    end.

    RED under M1 (the lane map dropped from the record), observed:

        AssertionError: the resumed night named no lane node: {...}
    """
    sid, first = await rig.first_night(laned_plan(count=4))
    assert {"nS", "nA", "nG"} <= set(first), (
        f"premise: night one names its lane nodes: {first}")
    engine = rig.next_engine()
    await rig.resume_arm(engine).tick()
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete"), (
        "premise: night two ran the plan to its end")
    assert await wait_for(lambda: not engine.running)
    stages = engine.state.get("stages")
    assert stages and {"nS", "nA", "nG"} <= set(stages), (
        f"the resumed night named no lane node: {stages}")
    assert stages == ALL_DONE


async def test_the_resume_route_names_the_lane_nodes_too(rig):
    """``POST /api/sessions/{id}/resume`` builds its run from the stored
    session by another door, and names the lane the same way."""
    sid, _ = await rig.first_night(laned_plan(count=4))
    engine = rig.next_engine()
    r = await rig.client.post(f"/api/sessions/{sid}/resume", json={})
    assert r.status_code == 200, r.text
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete")
    assert await wait_for(lambda: not engine.running)
    assert engine.state.get("stages") == ALL_DONE


async def test_a_crash_resumed_night_names_the_lane_nodes(rig):
    """A process that died mid-run leaves its session file ACTIVE; the next
    boot's sweep makes it dormant (counting the death), and ResumeArm resumes
    it from that record, naming the lane."""
    sid, _first = await rig.first_night(laned_plan(count=4))
    raw = stored_file(sid)
    raw["status"] = "active"
    session_store._path(sid).write_text(json.dumps(raw), encoding="utf-8")
    assert session_store.boot_sweep() == 1
    assert session_store.load(sid).crash_resumes == 1
    engine = rig.next_engine()
    await rig.resume_arm(engine).tick()
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete")
    assert await wait_for(lambda: not engine.running)
    assert engine.state.get("stages") == ALL_DONE


# --------------------------------------------------------- the real compile

async def test_a_flow_runs_session_file_names_what_the_compile_named(flow_rig):
    """The lane the real compile put on a flow's plan reaches the file the
    flow run's session is saved to, and a session read back from it names the
    same lane as the plan the run was started with."""
    rig: FlowRig = flow_rig
    night, _sweeps = await night_of(rig, lane_flow())
    (stored,) = session_store.load_all()
    ran = lanes_of(night.engine.plan)
    assert list(ran.values()) == [[("n3", "slew"), ("n4", "autofocus"),
                                   ("n5", "guide")]]
    assert lanes_of(stored.plan) == ran
    raw = stored_file(stored.id)
    assert raw["flow_lanes"] == {t: [list(p) for p in lane]
                                 for t, lane in ran.items()}
