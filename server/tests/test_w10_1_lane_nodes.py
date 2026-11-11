# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A flow's SLEW, AUTOFOCUS and GUIDE nodes are named in live stage status
(#1231, WP 10.1 part b).

WP 4.2 made the engine the one writer of what each flow stage is doing, but
only a node that compiles to a step could be named. A SLEW, an AUTOFOCUS or a
GUIDE node under a TARGET compiles to NO step (the compile folds their effect
into the target's fields), so nothing carried their canvas ids and the canvas
showed no word for them through a run. The rule (design.md, 10.1b):

* the compile puts them on the plan in LANE order: ``Target.lane``, a list of
  ``{id, kind}``, excluded from every dump and so from the plan hash, as
  ``Target.block`` is. Every panel of a mosaic and every member of a pool
  carries its block's lane. A lane node wired to nothing is in no lane;
* the engine publishes ``running``, ``done`` or ``failed`` for each as it
  performs the action: the SLEW node is `running` through the slew and the
  centring and `done` after, the AUTOFOCUS node `running` through a sweep, the
  GUIDE node `running` through the guide start. A node whose action was not
  due (a hop whose focus is fresh) reads `done`, never `skipped`. A node whose
  action failed reads `failed` (a guide that would not start, a sweep that
  found no focus), including the default path where the run goes on.

Clocked nights run through ``POST /api/flows/{id}/run`` on the simulator
(tests/_flow_night.py): no test reads the Sun, a target's window or the PC's
time zone.

MUTANTS, each from a byte backup of the file named:

  M1  ``engine.py``: ``_say_lane`` publishes nothing (the lane words never
      leave the engine): the run tests go red.
  M2  ``engine.py``: the end of ``_setup_target`` no longer finishes the lane:
      a node whose action was not due stays unnamed.
  M3  ``to_plan.py``: the lane not put on the target: every compile test and
      every run test goes red.
  M4  ``session.py``: ``note_stages`` records no ``flow_lanes`` (the resumed
      night's test, in test_w10_1_resumed_night_names_lane_nodes.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import astrodeck.sequence.engine as engine_mod
from _flow_night import (NAME, FlowRig, flow_rig, fmt_dec, fmt_ra,  # noqa: F401
                         mosaic_flow)
from _group_harness import group_store, ra_at  # noqa: F401
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.models import ExposureStep, Target

from test_group_plain_stop_defers import _Focuser
from test_w4_2_stage_status import LANE_FLOW, stage_events, transitions

STREAM = Path(__file__).parent / "fixtures" / "stage_status_stream.json"
SLEW, FOCUS, GUIDE, CAPTURE = "n3", "n4", "n5", "n6"


def lane_flow() -> dict:
    """A TARGET, then SLEW, AUTOFOCUS, GUIDE and one CAPTURE LOOP in a row (two
    frames), and a REPORT. Goal 0 so the compile has no estimate to warn
    about, which the run route would refuse."""
    return {
        "nodes": [
            {"id": "n2", "type": "target", "x": 30, "y": 60,
             "params": {"name": "M31", "ra": fmt_ra(ra_at(-2.0)),
                        "dec": fmt_dec(41.0)}},
            {"id": SLEW, "type": "slew", "x": 270, "y": 60, "params": {}},
            {"id": FOCUS, "type": "autofocus", "x": 510, "y": 60,
             "params": {}},
            {"id": GUIDE, "type": "guide", "x": 750, "y": 60, "params": {}},
            {"id": CAPTURE, "type": "capture", "x": 990, "y": 60,
             "params": {"filter": "L", "exposure": 10, "count": 2,
                        "goal": 0}},
            {"id": "n12", "type": "report", "x": 1230, "y": 60,
             "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "n2", "fromPort": "target", "to": SLEW,
             "toPort": "run"},
            {"id": "e2", "from": SLEW, "fromPort": "centered", "to": FOCUS,
             "toPort": "run"},
            {"id": "e3", "from": FOCUS, "fromPort": "focused", "to": GUIDE,
             "toPort": "run"},
            {"id": "e4", "from": GUIDE, "fromPort": "guiding", "to": CAPTURE,
             "toPort": "run"},
            {"id": "e5", "from": CAPTURE, "fromPort": "complete", "to": "n12",
             "toPort": "session"},
        ],
        "settings": {},
    }


def mosaic_lane_flow() -> dict:
    """The 2x2 of ``mosaic_flow`` with a SLEW, an AUTOFOCUS and a GUIDE
    between the TARGET and its FILTER CYCLE."""
    g = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=1)
    g["nodes"] += [
        {"id": SLEW, "type": "slew", "x": 150, "y": 160, "params": {}},
        {"id": FOCUS, "type": "autofocus", "x": 210, "y": 160, "params": {}},
        {"id": GUIDE, "type": "guide", "x": 250, "y": 160, "params": {}},
    ]
    g["edges"] = [
        {"id": "e1", "from": "n2", "fromPort": "target", "to": SLEW,
         "toPort": "run"},
        {"id": "e1b", "from": SLEW, "fromPort": "centered", "to": FOCUS,
         "toPort": "run"},
        {"id": "e1c", "from": FOCUS, "fromPort": "focused", "to": GUIDE,
         "toPort": "run"},
        {"id": "e1d", "from": GUIDE, "fromPort": "guiding", "to": "n7",
         "toPort": "run"},
        {"id": "e2", "from": "n7", "fromPort": "complete", "to": "n12",
         "toPort": "session"},
        {"id": "e3", "from": "n7", "fromPort": "pass", "to": "n2",
         "toPort": "next"},
    ]
    return g


def pool_lane_flow() -> dict:
    """A POOL of two members, an AUTOFOCUS and a CAPTURE LOOP in its lane."""
    return {
        "nodes": [
            {"id": "n20", "type": "pool", "x": 30, "y": 60,
             "params": {"members": "M31, M33"}},
            {"id": "n8", "type": "autofocus", "x": 270, "y": 60,
             "params": {}},
            {"id": "n7", "type": "capture", "x": 510, "y": 60,
             "params": {"filter": "L", "exposure": 60, "count": 2}},
        ],
        "edges": [
            {"id": "e1", "from": "n20", "fromPort": "target", "to": "n8",
             "toPort": "run"},
            {"id": "e2", "from": "n8", "fromPort": "focused", "to": "n7",
             "toPort": "run"},
        ],
        "settings": {},
    }


def plan_of(graph: dict) -> SequencePlan:
    g = FlowGraph.model_validate(graph)
    plan, _ = to_sequence_plan(compile_plan(g, NAME), g, flow_id="f-lanes")
    return plan


def lane_of(target: Target) -> list[tuple[str, str]]:
    return [(n.id, n.kind) for n in target.lane]


# ------------------------------------------------------------ the compile

def test_the_compile_names_the_lane_nodes_in_lane_order():
    """SLEW, AUTOFOCUS and GUIDE under a TARGET reach ``Target.lane``, ids and
    kinds, in the order the wires put them. A CAPTURE is a stage, not a lane
    node, and is not in it.

    RED on the unchanged compile, observed:

        AttributeError: 'Target' object has no attribute 'lane'
    """
    plan = plan_of(lane_flow())
    (target,) = plan.targets
    assert lane_of(target) == [(SLEW, "slew"), (FOCUS, "autofocus"),
                               (GUIDE, "guide")]


def test_every_panel_of_a_mosaic_and_every_pool_member_carries_the_lane():
    plan = plan_of(mosaic_lane_flow())
    assert len(plan.targets) == 4, "premise: a 2x2 is four panels"
    want = [(SLEW, "slew"), (FOCUS, "autofocus"), (GUIDE, "guide")]
    assert [lane_of(t) for t in plan.targets] == [want] * 4

    pool = plan_of(pool_lane_flow())
    assert len(pool.targets) == 2, "premise: two members"
    assert [lane_of(t) for t in pool.targets] == [[("n8", "autofocus")]] * 2


def test_an_unwired_autofocus_is_in_no_lane():
    """LANE_FLOW's AUTOFOCUS (n5) is wired to nothing: ``owner_of`` gives it
    to no block, so it is on no target and stays unnamed through a run."""
    plan = plan_of(LANE_FLOW)
    assert plan.targets, "premise: the flow compiles a target"
    assert all(t.lane == [] for t in plan.targets)


def test_lane_names_stay_out_of_every_dump():
    """The lane rides the plan the engine runs and nothing else: the dump of
    the plan is the dump of the same plan with its lane cleared, so no stored
    plan, plan response or plan hash moves."""
    for graph in (lane_flow(), mosaic_lane_flow(), pool_lane_flow()):
        plan = plan_of(graph)
        assert any(t.lane for t in plan.targets), "premise: it names a lane"
        dumped = plan.model_dump(mode="json")
        text = plan.model_dump_json()
        bare = plan.model_copy(deep=True)
        for t in bare.targets:
            t.lane = []
        assert dumped == bare.model_dump(mode="json")
        assert text == bare.model_dump_json()
        assert all("lane" not in t for t in dumped["targets"])


# --------------------------------------------------- the run, on a night

def words_of(night, node: str) -> list[str]:
    return [w for n, w in transitions(stage_events(night)) if n == node]


def order_of(moves: list[tuple[str, str]], *pairs: tuple[str, str]) -> None:
    at = [moves.index(p) for p in pairs]
    assert at == sorted(at), f"out of order, {pairs} at {at}: {moves}"


async def night_of(rig: FlowRig, graph: dict, *, goto=None, patch=None,
                   mosaic: bool = False):
    """``graph`` saved and run to its end on a clocked night with a focuser
    whose sweep always finds focus; the night and the sweeps taken. A mosaic
    is saved through ``save_flow``, which tells the night the angle its group
    is laid out at."""
    night = await rig.night(goto=goto) if goto else await rig.night()
    night.hub.devices["focuser"] = _Focuser()
    sweeps: list[str] = []

    async def run_autofocus(cam, foc, **kw):
        sweeps.append(str(night.engine.state.get("target", "")))
        return AutofocusResult(True, 30000, 2.0, [], "ok")

    rig.mp.setattr(engine_mod, "run_autofocus", run_autofocus)
    if patch is not None:
        patch(rig, night)
    if mosaic:
        fid = await rig.save_flow(graph)
    else:
        r = await rig.client.post("/api/flows", json={
            "flow": {"name": NAME, "graph": graph}})
        assert r.status_code == 200, r.text
        fid = r.json()["id"]
    started = await rig.run(fid)
    assert started.status_code == 200, started.text
    await night.finish()
    return night, sweeps


async def test_a_run_publishes_the_lane_nodes_in_order(flow_rig):
    """The slew is `running` before the sweep, the sweep before the guide
    start; each is `done` before the first CAPTURE stage is `running`; and the
    TARGET block reads `running` from the start to the end of the run.

    RED on the unchanged engine, observed:

        ValueError: ('n3', 'running') is not in list
    """
    night, sweeps = await night_of(flow_rig, lane_flow())
    assert sweeps, "premise: the night swept"
    moves = transitions(stage_events(night))
    order_of(moves, (SLEW, "running"), (SLEW, "done"), (FOCUS, "running"),
             (GUIDE, "running"), (CAPTURE, "running"))
    order_of(moves, (FOCUS, "done"), (CAPTURE, "running"))
    order_of(moves, (GUIDE, "done"), (CAPTURE, "running"))
    assert words_of(night, "n2") == ["running", "done"], (
        "the TARGET block is named from its first step, as it was")
    last = stage_events(night)[-1]["stages"]
    assert last == {"n2": "done", SLEW: "done", FOCUS: "done",
                    GUIDE: "done", CAPTURE: "done"}, last


async def test_a_hop_whose_focus_is_fresh_still_ends_done(flow_rig):
    """A mosaic's second panel reuses the focus the first swept: the sweep was
    not due, and the node reads `done` after that hop, never `skipped`. The
    slew ran on every hop, the sweep on fewer.

    RED under M2 (the end of the setup no longer finishes the lane), observed:

        AssertionError: ... 'n4' ... 'running' ... no 'done' after the last hop
    """
    night, sweeps = await night_of(flow_rig, mosaic_lane_flow(),
                                  mosaic=True)
    slews = words_of(night, SLEW).count("running")
    assert slews >= 4, "premise: four panels, a hop each"
    assert 1 <= len(sweeps) < slews, (
        f"premise: a fresh focus is reused ({len(sweeps)} sweeps, "
        f"{slews} hops)")
    for node in (SLEW, FOCUS, GUIDE):
        seq = words_of(night, node)
        assert seq[-1] == "done", (node, seq)
        assert "skipped" not in seq, (node, seq)
    assert words_of(night, FOCUS).count("running") == len(sweeps)


async def test_an_action_that_was_not_due_ends_done_not_skipped(flow_rig):
    """A rig with no focuser never sweeps, and the AUTOFOCUS node still reads
    `done` once the target's setup is over: an action that was not due is done,
    never skipped (skipped draws amber, and reusing a focus is the normal
    case), and never left unnamed beside the nodes that did run.

    RED under M2 (the end of the setup no longer finishes the lane), observed:

        AssertionError: ('n4', ...) the AUTOFOCUS node was never named: [...]
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    assert "focuser" not in night.hub.devices, "premise: no focuser, no sweep"
    r = await rig.client.post("/api/flows", json={
        "flow": {"name": NAME, "graph": lane_flow()}})
    assert r.status_code == 200, r.text
    started = await rig.run(r.json()["id"])
    assert started.status_code == 200, started.text
    await night.finish()
    moves = transitions(stage_events(night))
    assert (FOCUS, "done") in moves, (
        f"the AUTOFOCUS node was never named: {moves}")
    assert (FOCUS, "running") not in moves, moves
    assert all(w != "skipped" for _n, w in moves), moves
    order_of(moves, (FOCUS, "done"), (CAPTURE, "running"))


async def test_a_failed_guide_start_reads_failed_and_the_run_goes_on(flow_rig):
    """The default action is warn: the guide would not start, the run shoots
    unguided, and the GUIDE node reads `failed` through to the end (the node
    did fail), beside a run that completes.

    RED on the unchanged engine, observed:

        AssertionError: ... ('n5', 'failed') not in ...
    """
    def wrecked(rig: FlowRig, night) -> None:
        async def refuse():
            raise RuntimeError("no guide star")
        rig.mp.setattr(night.hub.guider, "start_guiding", refuse)

    night, _sweeps = await night_of(flow_rig, lane_flow(), patch=wrecked)
    moves = transitions(stage_events(night))
    assert (GUIDE, "running") in moves and (GUIDE, "failed") in moves, moves
    order_of(moves, (GUIDE, "running"), (GUIDE, "failed"),
             (CAPTURE, "running"))
    assert words_of(night, GUIDE)[-1] == "failed", moves
    assert [e["data"].get("state") for e in night.events
            if e["type"] == "sequence"][-1] == "complete"


@pytest.mark.parametrize("action", ["warn", "skip"])
async def test_a_failed_sweep_reads_failed(flow_rig, action):
    """A sweep that finds no focus reads `failed`: under warn the run goes on
    at the focus it has, under skip the target is dropped (a `StopTarget`
    out of the sweep), and the node says failed either way.

    RED on the unchanged engine, observed:

        AssertionError: ... ('n4', 'failed') not in ...
    """
    def wrecked(rig: FlowRig, night) -> None:
        async def run_autofocus(cam, foc, **kw):
            return AutofocusResult(False, None, None, [], "no stars")

        rig.mp.setattr(engine_mod, "run_autofocus", run_autofocus)
        rig.mp.setattr(rig.store.cfg().escalation, "af_failure_action",
                       action)

    night, _sweeps = await night_of(flow_rig, lane_flow(), patch=wrecked)
    moves = transitions(stage_events(night))
    order_of(moves, (FOCUS, "running"), (FOCUS, "failed"))
    assert words_of(night, FOCUS)[-1] == "failed", moves
    if action == "warn":
        assert words_of(night, CAPTURE)[-1] == "done", moves


def never_centred(who: str, n: int, result: dict) -> dict:
    """Every centring ends 255 arcminutes off target: the field the mount
    landed on is not the target's (#852), and a lone target is stopped."""
    return {**result, "centered": False, "error_arcmin": 255.0}


async def test_a_slew_that_misses_its_target_reads_failed(flow_rig):
    """The centring ends far off target and the lone target is stopped
    (`StopTarget` out of `_setup_target`, after the slew returned): the SLEW
    node reads `failed`, not the `running` it was published as when the slew
    began, and the sweep and the guide start that a field nobody could centre
    would waste never begin.

    RED without the scheduler's departure failing a lane node that is still
    running, observed:

        AssertionError: ... 'running' ... expected 'failed'
    """
    night, _sweeps = await night_of(flow_rig, lane_flow(),
                                    goto=never_centred)
    moves = transitions(stage_events(night))
    assert (SLEW, "running") in moves, moves
    assert words_of(night, SLEW)[-1] == "failed", moves
    assert (FOCUS, "running") not in moves, moves
    assert (GUIDE, "running") not in moves, moves


async def test_a_mid_run_sweep_names_the_autofocus_node(flow_rig):
    """A refocus the frame loop owes (``autofocus_every``) runs through
    `_autofocus` too, not the hop: the node reads `running` after the first
    frame's stage is already running, and `done` after it.

    RED on the unchanged engine, observed:

        AssertionError: [('sA', 'running'), ('blk', 'running'), ('sA', 'done'), ...]
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    night.hub.devices["focuser"] = _Focuser()
    sweeps: list[int] = []

    async def run_autofocus(cam, foc, **kw):
        sweeps.append(night.engine._frames_done)
        return AutofocusResult(True, 30000, 2.0, [], "ok")

    rig.mp.setattr(engine_mod, "run_autofocus", run_autofocus)
    plan = SequencePlan(
        name="Refocus", guide=False, dither_every=0, autofocus_every=2,
        meridian_flip=False,
        targets=[Target(name="T", ra_hours=ra_at(-2.0), dec_deg=41.0,
                        center=False, autofocus_first=False, block="blk",
                        lane=[{"id": "nA", "kind": "autofocus"}],
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=5, stage="sA")])])
    assert await night.run(plan)
    moves = transitions(stage_events(night))
    assert len(sweeps) >= 1 and min(sweeps) >= 2, (
        f"premise: the sweep is the frame loop's, after frame two: {sweeps}")
    words = words_of(night, "nA")
    assert words.count("running") == len(sweeps), words
    assert words[-1] == "done", words
    assert moves.index(("nA", "running")) > moves.index(("sA", "running")), (
        f"the sweep is mid-run, after the stage is shooting: {moves}")


async def test_the_unwired_autofocus_in_the_recorded_flow_stays_unnamed(
        flow_rig):
    """LANE_FLOW has an AUTOFOCUS wired to nothing: no lane carries it, so a
    run never names it (the recorded ``frames`` stream still holds)."""
    rig: FlowRig = flow_rig
    night, _sweeps = await night_of(rig, LANE_FLOW)
    named = {n for n, _w in transitions(stage_events(night))}
    assert "n5" not in named, named


# ------------------------------------------------- the recorded stream

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


async def test_the_recorded_lane_stream_is_what_the_engine_publishes(
        flow_rig):
    """``lane_frames`` in ``fixtures/stage_status_stream.json`` is the
    sequence stream of ``lane_flow()`` (SLEW, AUTOFOCUS, GUIDE, CAPTURE) on a
    clocked night, reduced and de-duplicated; the UI replays it
    (w10_1LaneNodes.test.ts). This holds the recording to the engine.

    RED on the unchanged engine, observed: the recording names the lane nodes
    and the engine published none."""
    night, _sweeps = await night_of(flow_rig, lane_flow())
    recorded = [{k: v for k, v in f.items() if k != "session"}
                for f in json.loads(
                    STREAM.read_text(encoding="utf-8"))["lane_frames"]]
    assert frames_of(night) == recorded
