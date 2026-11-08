# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Live stage status has one writer (#1125, members #464 and #742).

The engine is the one writer of what each flow stage is doing. It names the
stage on every step it runs (``ExposureStep.stage``, the canvas node id the
compile put there) and the block on every target (``Target.block``), and it
publishes the answer as ``stages`` on the ``sequence`` state (the WS event
and the snapshot the monitor reads), at every transition: ``running`` when a
stage's step is entered, ``done`` when every step the stage owns is complete,
``skipped`` when everything it still owes is set aside, ``failed`` when a
step raises. A stage the engine does not run (an AUTOFOCUS node, the REPORT)
is never named, so the canvas shows no word for it through a run.

Two flows, both run through ``POST /api/flows/{id}/run`` on the clocked
simulator (tests/_flow_night.py): the S5 probe's 2x2 (a TARGET block ``n2``
over four panels, a FILTER CYCLE ``n7``), and ``LANE_FLOW``, a TARGET with two
CAPTURE LOOP stages one after the other, an AUTOFOCUS and a REPORT. The
second is the one the UI test replays (``fixtures/stage_status_stream.json``,
ui/src/components/flows/__tests__/w4_2StageStatusReplay.test.tsx).

MUTANTS, each run from a byte backup of ``engine.py``:

  M1  the publish skipped (``_publish_stages`` without its ``_set_state``
      call): no ``sequence`` event carries ``stages``.
  M2  a stage done when THIS target's steps are (``_say_stages`` handing
      ``_stage_word`` only the step's own target): the mosaic's block says
      done after panel one.
  M3  ``failed`` not said (the ``except Exception`` arm of
      ``_run_staged_step`` removed).
  M4  ``skipped`` not said (``_stage_word`` without its set-aside branch).
"""
from __future__ import annotations

import json
from pathlib import Path

from _flow_night import (NAME, FlowRig, flow_rig, fmt_dec, fmt_ra,  # noqa: F401
                         mosaic_flow, steps_by_panel)
from _group_harness import group_store, plain_mosaic_plan, ra_at  # noqa: F401

BLOCK, CYCLE, REPORT = "n2", "n7", "n12"
FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=1)
WORDS = {"running", "done", "skipped", "failed"}
STREAM = Path(__file__).parent / "fixtures" / "stage_status_stream.json"

#: A TARGET, two CAPTURE LOOPs in a row (L then R, two frames each), an
#: AUTOFOCUS and a REPORT. The goal is 0 so the compile has no estimate to
#: warn about, which the run route would refuse.
LANE_FLOW = {
    "nodes": [
        {"id": "n2", "type": "target", "x": 30, "y": 60,
         "params": {"name": "M31", "ra": fmt_ra(ra_at(-2.0)),
                    "dec": fmt_dec(41.0)}},
        {"id": "n3", "type": "capture", "x": 270, "y": 60,
         "params": {"filter": "L", "exposure": 10, "count": 2, "goal": 0}},
        {"id": "n4", "type": "capture", "x": 510, "y": 60,
         "params": {"filter": "R", "exposure": 10, "count": 2, "goal": 0}},
        {"id": "n5", "type": "autofocus", "x": 750, "y": 60, "params": {}},
        {"id": "n12", "type": "report", "x": 990, "y": 60, "params": {}},
    ],
    "edges": [
        {"id": "e1", "from": "n2", "fromPort": "target", "to": "n3",
         "toPort": "run"},
        {"id": "e2", "from": "n3", "fromPort": "complete", "to": "n4",
         "toPort": "run"},
        {"id": "e3", "from": "n4", "fromPort": "complete", "to": "n12",
         "toPort": "session"},
    ],
    "settings": {},
}


def stage_events(night) -> list[dict]:
    """Every ``sequence`` publish of the run that carries ``stages``, as the
    payload the WS client and the snapshot read."""
    return [e["data"] for e in night.events
            if e["type"] == "sequence" and "stages" in e["data"]]


def transitions(events: list[dict]) -> list[tuple[str, str]]:
    """The (node, word) pairs in the order they first appeared or changed."""
    out: list[tuple[str, str]] = []
    last: dict[str, str] = {}
    for data in events:
        for node, word in data["stages"].items():
            if last.get(node) != word:
                last[node] = word
                out.append((node, word))
    return out


async def run_lane_flow(rig: FlowRig):
    """LANE_FLOW saved and run to its end on a clocked night; the night."""
    night = await rig.night()
    rig.as_role(None)
    r = await rig.client.post("/api/flows", json={
        "flow": {"name": NAME, "graph": LANE_FLOW}})
    assert r.status_code == 200, r.text
    started = await rig.run(r.json()["id"])
    assert started.status_code == 200, started.text
    await night.finish()
    return night


async def test_the_plan_names_the_stage_and_block_of_every_step(flow_rig):
    """The carrier: the compile's node ids reach the plan the engine runs."""
    rig: FlowRig = flow_rig
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    assert plan.targets, "premise: a 2x2 compiles to four panels"
    assert {t.block for t in plan.targets} == {BLOCK}
    assert {s.stage for t in plan.targets for s in t.steps} == {CYCLE}
    ids = steps_by_panel(plan)
    assert len(ids) == 8, "premise: four panels, two steps each"


async def test_the_stage_names_stay_out_of_every_dump(flow_rig):
    """The names ride the plan the engine runs and nothing else: no dump of
    it, so no stored plan, plan response or plan hash moves."""
    rig: FlowRig = flow_rig
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    assert {s.stage for t in plan.targets for s in t.steps} == {CYCLE}, (
        "premise: the plan names its stages")
    dumped = plan.model_dump(mode="json")
    for t in dumped["targets"]:
        assert "block" not in t
        assert all("stage" not in s for s in t["steps"])


async def test_a_run_publishes_each_stage_running_then_done(flow_rig):
    """The block and the cycle each say running first, and say done only once
    every frame of the plan is in (the block over four panels, not after
    the first); the REPORT is never named.

    RED under M1 (the publish skipped), observed:

        AssertionError: no sequence event of the run carried stages

    RED under M2 (a stage done when this target's steps are), observed:

        AssertionError: ['n2'] said done at 2 of 8 frames
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    await night.finish()

    events = stage_events(night)
    assert events, "no sequence event of the run carried stages"
    moves = transitions(events)
    assert set(moves[:2]) == {(BLOCK, "running"), (CYCLE, "running")}, moves
    assert (CYCLE, "done") in moves and (BLOCK, "done") in moves, moves
    assert {w for _n, w in moves} <= WORDS, moves
    assert {n for n, _w in moves} == {BLOCK, CYCLE}, (
        f"only the stages the engine runs are named: {moves}")
    assert REPORT not in events[-1]["stages"]

    for data in events:
        done = [n for n, w in data["stages"].items() if w == "done"]
        if done:
            progress = data["progress"]
            assert progress["frames_done"] == progress["frames_total"], (
                f"{done} said done at {progress['frames_done']} of "
                f"{progress['frames_total']} frames")
    assert events[-1]["stages"] == {BLOCK: "done", CYCLE: "done"}


async def test_two_stages_in_a_row_run_one_after_the_other(flow_rig):
    """Block acquisition: the first CAPTURE LOOP is done before the second is
    running, the TARGET block runs through both, and the AUTOFOCUS and the
    REPORT, which the engine does not run as stages, are never named."""
    night = await run_lane_flow(flow_rig)
    moves = transitions(stage_events(night))
    assert moves.index(("n3", "done")) < moves.index(("n4", "running")), moves
    assert moves.index(("n2", "running")) < moves.index(("n4", "running"))
    assert {n for n, _w in moves} == {"n2", "n3", "n4"}, moves
    last = stage_events(night)[-1]["stages"]
    assert last == {"n2": "done", "n3": "done", "n4": "done"}, last


async def test_the_recorded_stream_is_what_the_engine_publishes(flow_rig):
    """``fixtures/stage_status_stream.json`` is the sequence stream of a
    LANE_FLOW night as recorded from this harness, reduced and
    de-duplicated; the UI replays it. This holds the recording to the
    engine: when the engine's words or their order change, this goes red
    until the stream is recorded again, and the UI test with it.

    RED under M1, observed: the recorded frames name stages and the night
    published none."""
    night = await run_lane_flow(flow_rig)
    frames: list[dict] = []
    for e in night.events:
        if e["type"] != "sequence":
            continue
        d = e["data"]
        frame = {"state": d.get("state")}
        if "stages" in d:
            frame["stages"] = d["stages"]
        if not frames or frames[-1] != frame:
            frames.append(frame)
    recorded = [{k: v for k, v in f.items() if k != "session"}
                for f in json.loads(STREAM.read_text(encoding="utf-8"))["frames"]]
    assert frames == recorded


async def test_a_step_that_raises_is_failed_and_the_state_says_so(flow_rig):
    """A device error inside a step fails the stage and the block that own
    it, in the publish made before the run's terminal state.

    RED under M3 (``failed`` not said), observed:

        AssertionError: [('n7', 'running'), ('n2', 'running')]
    """
    from astrodeck.devices.base import DeviceError
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)

    async def wheel_stuck(step):
        raise DeviceError("filter wheel stuck")

    rig.mp.setattr(night.engine, "_apply_filter", wheel_stuck)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    await night.finish()

    moves = transitions(stage_events(night))
    assert (CYCLE, "failed") in moves, moves
    assert (BLOCK, "failed") in moves, moves
    states = [e["data"].get("state") for e in night.events
              if e["type"] == "sequence"]
    assert states[-1] == "error", states


async def test_a_stage_with_everything_owed_set_aside_is_skipped(flow_rig):
    """The word a stage says is decided by what it still owes: running while
    a step is owed and not set aside, skipped once everything owed is set
    aside for tonight (the ledger keeps it owed), done when nothing is owed.

    RED under M4 (``skipped`` not said), observed:

        AssertionError: assert 'running' == 'skipped'
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    eng = night.engine
    eng.plan = plan
    owned = [(t, s) for t in plan.targets for s in t.steps]
    assert eng._stage_word(owned) == "running"
    for t, s in owned:
        eng._set_aside[f"{t.id}:{s.id}"] = "set aside by the reject guard"
    assert eng._stage_word(owned) == "skipped"
    for t, s in owned:
        eng._done[f"{t.id}:{s.id}"] = s.count
    assert eng._stage_word(owned) == "done"


async def test_a_plan_with_no_stage_ids_publishes_no_stages(flow_rig):
    """A plan not made from a flow has no ``stage`` on its steps: nothing is
    named, so the key never appears (every hand-built plan publishes as it
    always did)."""
    rig: FlowRig = flow_rig
    night = await rig.night()
    plan = plain_mosaic_plan()
    assert all(s.stage is None for t in plan.targets for s in t.steps)
    assert await night.run(plan)
    assert stage_events(night) == []
