# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Live stage status says `waiting` and `deferred`, and settles at the end
(#1231, items 3 and the chained-lane instance, WP 10.1 part a).

WP 4.2 made the engine the one writer of what each flow stage is doing, with
four words. Three states had no word and read "running": a panel the group
driver left for the next pass, a target the scheduler dropped for tonight, and
a chained lane's later stage held behind an earlier phase. The terminal
payload also kept whatever word was last published. The rule (design.md, 10.1):

* ``deferred``: the scheduler left the panel for now and will take it up again
  tonight (a `PanelDeferred`, a member's plain `StopTarget`, a limit-refused
  hop that waits, a rotating panel skipped for the round).
* ``skipped``: the scheduler dropped the target for tonight with frames owed
  (`StopTarget` or a consumed `JumpTarget`), or everything owed is set aside.
* ``waiting``: every owed step of the stage is held behind an earlier chain
  phase of its target.
* at a terminal state the published map is settled: nothing reads `running` or
  `waiting`; unfinished stages read `deferred`; `failed` is kept.

Clocked nights run through ``POST /api/flows/{id}/run`` on the simulator
(tests/_flow_night.py). No test reads the Sun, a target's window or the PC's
time zone; the hand-built cases touch the engine's own bookkeeping only.

MUTANTS, each from a byte backup of ``engine.py``:

  M1  the reducer returns "running" where it returns "deferred": the deferred
      stream test and the recorded `deferred_frames` go red.
  M2  the terminal settle removed from ``_set_state``: the terminal payload
      keeps the last live word.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from _flow_night import (NAME, FlowRig, flow_rig, fmt_dec, fmt_ra,  # noqa: F401
                         mosaic_flow)
from _group_harness import group_store, ra_at  # noqa: F401
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.models import ExposureStep, Target

from test_w4_2_stage_status import (LANE_FLOW, stage_events, transitions,
                                    run_lane_flow)

STREAM = Path(__file__).parent / "fixtures" / "stage_status_stream.json"
BLOCK, CYCLE = "n2", "n7"
FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=1)


# ------------------------------------------------------- hand-built engine

def chained_plan(targets: int = 2) -> SequencePlan:
    """``targets`` targets under one block, each with two steps in two stages:
    stage ``sA`` (phase 0) then stage ``sB`` (phase 1), the shape a lane that
    chains a FILTER CYCLE and a CAPTURE compiles to."""
    return SequencePlan(
        name="Chained", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name=f"T{i}", ra_hours=5.5, dec_deg=-5.0,
                        center=False, autofocus_first=False, block="blk",
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=3, stage="sA", phase=0),
                               ExposureStep(filter="R", exposure_s=0.05,
                                            count=3, stage="sB", phase=1)])
                 for i in range(targets)])


def pairs(plan: SequencePlan, stage: str | None = None):
    return [(t, s) for t in plan.targets for s in t.steps
            if stage is None or s.stage == stage]


def complete(eng, t, s) -> None:
    eng._done[f"{t.id}:{s.id}"] = s.count


def aside(eng, t, s) -> None:
    eng._set_aside[f"{t.id}:{s.id}"] = "set aside by the reject guard"


@pytest.fixture
async def eng(flow_rig):
    night = await flow_rig.night()
    return night.engine


# ------------------------------------------------------------ the reducer

async def test_the_reducer_truth_table(eng):
    """One word per combination of pair states. Phase 0 is stage sA, phase 1
    stage sB, two targets T0 and T1 (a stage owns a step on each)."""
    plan = chained_plan()
    eng.plan = plan
    t0, t1 = plan.targets
    a, b = (pairs(plan, "sA"), pairs(plan, "sB"))
    both = pairs(plan)

    # nothing owed: done
    for t, s in both:
        complete(eng, t, s)
    assert eng._stage_word(a) == "done"
    eng._done.clear()

    # a live pair beside anything else: running
    assert eng._stage_word(a) == "running"
    assert eng._stage_word(both) == "running"

    # a later phase is held behind an owed earlier one: waiting
    assert eng._stage_word(b) == "waiting"

    # held behind a wholly set-aside earlier phase: the later phase will not
    # start tonight, so it is skipped
    for t, s in a:
        aside(eng, t, s)
    assert eng._stage_word(a) == "skipped"
    assert eng._stage_word(b) == "skipped"
    eng._set_aside.clear()

    # the scheduler dropped a target for tonight: skipped when that is all
    # the stage owes, running while the other target is live
    eng._scheduler_left[t0.id] = "skipped"
    assert eng._stage_word([p for p in a if p[0] is t0]) == "skipped"
    assert eng._stage_word(a) == "running"
    eng._scheduler_left[t1.id] = "skipped"
    assert eng._stage_word(a) == "skipped"

    # left for the next pass: deferred, and deferred beside dropped
    eng._scheduler_left[t1.id] = "deferred"
    assert eng._stage_word(a) == "deferred"
    assert eng._stage_word([p for p in a if p[0] is t1]) == "deferred"
    # a deferred pair beside a set-aside one is still deferred
    eng._scheduler_left.pop(t0.id)
    aside(eng, *[p for p in a if p[0] is t0][0])
    assert eng._stage_word(a) == "deferred"
    eng._set_aside.clear()
    eng._scheduler_left.clear()

    # a whole set-aside TARGET is skipped (the existing rule)
    eng._set_aside_targets[t0.id] = "below its floor"
    eng._set_aside_targets[t1.id] = "below its floor"
    assert eng._stage_word(a) == "skipped"


async def test_one_deferred_panel_does_not_defer_its_block(eng):
    """A block owns every target compiled from it: with one panel of two
    deferred and the other live the block is still running."""
    plan = chained_plan()
    eng.plan = plan
    t0, _t1 = plan.targets
    eng._scheduler_left[t0.id] = "deferred"
    assert eng._stage_word(pairs(plan)) == "running"
    eng._scheduler_left[plan.targets[1].id] = "deferred"
    assert eng._stage_word(pairs(plan)) == "deferred"


async def test_entering_a_target_forgets_that_it_was_left(eng):
    """The map says where the scheduler is NOT: a deferred target that is
    entered again is live, so `_say_stages` clears its entry."""
    plan = chained_plan()
    eng.plan = plan
    t0 = plan.targets[0]
    eng._scheduler_left[t0.id] = "deferred"
    eng._say_stages(t0, t0.steps[0])
    assert t0.id not in eng._scheduler_left


async def test_a_run_start_forgets_what_the_scheduler_left(flow_rig):
    """A restart tonight is a new run that retries everything: the map starts
    empty (it is declared in ``__init__`` and reset at run start)."""
    night = await flow_rig.night()
    assert night.engine._scheduler_left == {}
    night.engine._scheduler_left["x"] = "skipped"
    await run_lane_flow(flow_rig)
    assert "x" not in flow_rig.nights[-1].engine._scheduler_left


# --------------------------------------------------- clocked night streams

def all_miss_once(who: str, n: int, result: dict) -> dict:
    """Every panel's first centring fails as a solve that never lands does;
    later attempts centre (the harness's own answer)."""
    if n == 1:
        return {**result, "centered": False, "error_arcmin": None}
    return result


async def deferred_night(rig: FlowRig):
    """The 2x2 of ``FLOW`` with every panel deferred on its first visit."""
    night = await rig.night(goto=all_miss_once)
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    await night.finish()
    return night


async def test_a_deferred_panel_reads_deferred_then_running_again(flow_rig):
    """Pass one defers every panel (a centring miss, retried on the next
    pass): the block and the cycle read `deferred`, and `running` again once
    a panel is entered, and `done` at the end. Never `running` while every
    owed panel is deferred.

    RED under M1 (the reducer returning running for deferred), observed:

        AssertionError: [('n2', 'running'), ('n7', 'running'), ('n2', 'done'), ...]
    """
    night = await deferred_night(flow_rig)
    moves = transitions(stage_events(night))
    words = {n: [w for m, w in moves if m == n] for n in (BLOCK, CYCLE)}
    for node, seq in words.items():
        assert "deferred" in seq, moves
        i = seq.index("deferred")
        assert "running" in seq[i + 1:], moves
        assert seq[-1] == "done", moves
    assert {w for _n, w in moves} <= {"running", "done", "skipped", "failed",
                                      "deferred", "waiting"}, moves


async def test_a_scheduler_skip_reads_skipped_in_the_final_payload(flow_rig):
    """A `StopTarget` out of a step drops the target for tonight: the engine's
    own log says skipped, and so now does the stage, in the publish made when
    the scheduler moves on and in the terminal payload, where it read
    `running` until the run ended.

    RED on the unchanged engine, observed:

        AssertionError: the terminal payload keeps 'running' for ['n2', 'n3']
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    r = await rig.client.post("/api/flows", json={
        "flow": {"name": NAME, "graph": LANE_FLOW}})
    assert r.status_code == 200, r.text

    async def window_closed(*_a, **_kw):
        raise StopTarget("window closed")

    rig.mp.setattr(night.engine, "_run_step", window_closed)
    started = await rig.run(r.json()["id"])
    assert started.status_code == 200, started.text
    await night.finish()
    moves = transitions(stage_events(night))
    assert ("n3", "skipped") in moves and ("n2", "skipped") in moves, moves
    final = [e["data"] for e in night.events if e["type"] == "sequence"][-1]
    assert final["state"] in ("complete", "aborted", "error"), final["state"]
    stale = [n for n, w in final["stages"].items() if w == "running"]
    assert not stale, f"the terminal payload keeps 'running' for {stale}"
    assert final["stages"]["n3"] == "skipped"


async def test_the_terminal_payload_settles_every_word(flow_rig):
    """An abort mid-run leaves a stage that was running: the terminal event's
    ``stages`` (and so the state a poll reads) has no `running` or `waiting`,
    the stage that owes frames reads `deferred`, and the settle rides the
    terminal event itself (no extra terminal publish).

    RED under M2 (the settle removed), observed:

        AssertionError: the terminal payload keeps 'running' for ['n2', 'n3']
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    r = await rig.client.post("/api/flows", json={
        "flow": {"name": NAME, "graph": LANE_FLOW}})
    aborts: list[asyncio.Task] = []

    def abort_on_first_frame(rec: dict) -> None:
        if not aborts:
            aborts.append(asyncio.get_running_loop().create_task(
                night.engine.abort()))

    night.on_capture = abort_on_first_frame
    started = await rig.run(r.json()["id"])
    assert started.status_code == 200, started.text
    await night.finish()
    # The terminal state is published by `abort()` itself, which can finish
    # after the engine task has stopped running: wait for it, or a loaded
    # machine reads the events before they are there.
    await asyncio.gather(*aborts)
    terminals = [e["data"] for e in night.events if e["type"] == "sequence"
                 and e["data"].get("state") in ("complete", "aborted", "error")]
    assert len(terminals) == 1, [t.get("state") for t in terminals]
    final = terminals[0]
    assert final["state"] == "aborted", final["state"]
    stale = [n for n, w in final["stages"].items()
             if w in ("running", "waiting")]
    assert not stale, f"the terminal payload keeps 'running' for {stale}"
    assert final["stages"]["n3"] == "deferred", final["stages"]
    assert night.engine.state["stages"] == final["stages"]


async def test_failed_survives_the_settle(flow_rig):
    """A step that raised is `failed`, and the terminal payload keeps it."""
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
    final = [e["data"] for e in night.events if e["type"] == "sequence"][-1]
    assert final["state"] == "error"
    assert final["stages"][CYCLE] == "failed", final["stages"]
    assert not [n for n, w in final["stages"].items()
                if w in ("running", "waiting")], final["stages"]


CHAIN_FLOW = {
    "nodes": [
        {"id": "n2", "type": "target", "x": 30, "y": 60,
         "params": {"name": "M31", "ra": fmt_ra(ra_at(-2.0)),
                    "dec": fmt_dec(41.0), "rows": 1, "cols": 2,
                    "overlap": 25, "fovX": 0.494, "fovY": 0.371,
                    "angle": "Rotate to PA", "rotation": 55}},
        {"id": "n7", "type": "cycle", "x": 270, "y": 60,
         "params": {"plan": "L 10, R 10", "cycles": 1, "perCycle": 1}},
        {"id": "n8", "type": "capture", "x": 510, "y": 60,
         "params": {"filter": "G", "exposure": 10, "count": 2, "goal": 0}},
        {"id": "n12", "type": "report", "x": 750, "y": 60, "params": {}},
    ],
    "edges": [
        {"id": "e1", "from": "n2", "fromPort": "target", "to": "n7",
         "toPort": "run"},
        {"id": "e2", "from": "n7", "fromPort": "complete", "to": "n8",
         "toPort": "run"},
        {"id": "e3", "from": "n8", "fromPort": "complete", "to": "n12",
         "toPort": "session"},
    ],
    "settings": {},
}


async def test_a_chained_lane_holds_the_later_stage_waiting(flow_rig):
    """A two-panel block whose lane chains a FILTER CYCLE (phase 0) and a
    CAPTURE (phase 1), shot panel by panel: the capture is entered on panel
    one, and while panel two owes its cycle the capture's only owed steps are
    held behind that phase. It reads `waiting` there, not `running`.

    RED on the unchanged engine, observed:

        AssertionError: [('n2', 'running'), ('n7', 'running'), ('n8', 'running'), ...]
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    rig.pa = 55.0
    r = await rig.client.post("/api/flows", json={
        "flow": {"name": NAME, "graph": CHAIN_FLOW}})
    assert r.status_code == 200, r.text
    started = await rig.run(r.json()["id"])
    assert started.status_code == 200, started.text
    await night.finish()
    moves = transitions(stage_events(night))
    seq = [w for n, w in moves if n == "n8"]
    assert "waiting" in seq, moves
    assert seq[-1] == "done", moves


# ------------------------------------------------- the recorded streams

def frames_of(night) -> list[dict]:
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
    return frames


def recorded(key: str) -> list[dict]:
    return [{k: v for k, v in f.items() if k != "session"}
            for f in json.loads(STREAM.read_text(encoding="utf-8"))[key]]


async def test_the_recorded_deferred_stream_is_what_the_engine_publishes(
        flow_rig):
    """``deferred_frames`` in ``fixtures/stage_status_stream.json`` is the
    sequence stream of ``deferred_night`` (the 2x2 with every panel deferred
    on its first visit), reduced and de-duplicated; the UI replays it
    (w10_1StageWords.test.ts). This holds the recording to the engine: when
    the engine's words or their order change, this goes red until the stream
    is recorded again, and the UI test with it.

    RED under M1, observed: the recording says `deferred`, the engine says
    `running`."""
    night = await deferred_night(flow_rig)
    assert frames_of(night) == recorded("deferred_frames")

