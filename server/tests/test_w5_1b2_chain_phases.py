# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A chain of stages is one target shot in phases (WP 5.1b2, #1074).

THE CLAIM. The flow "NGC7331 Preferential Filtering" draws two FILTER CYCLE
nodes in sequence on one target (LRGB 45 cycles, then SHO 45 cycles, the first
node's "all done" feeding the second's "run"). The compile flattened both into
one target of seven steps and the engine round-robined all seven: L R G B S Ha
Oiii L R G B S Ha Oiii, night after night. A lane with a FILTER CYCLE and more
than one stage now stays ONE target (one a panel on a mosaic), and every step
carries ``phase``, the place of its stage in the lane's own order. The engine
shoots only the steps of the lowest phase that still owes frames, so the lane
is shot "cycle 1 complete, then cycle 2". Nothing else of the plan moves: no
target is added or renamed, and the stage node still keys every step id, so a
ledger banked before this change counts every frame.

``phase`` is ABSENT from the dump at 0 (so no stored plan or plan hash moves
for a plan with no chain) and PRESENT when non-zero (so a session file keeps
it and a resumed night still honours it).

Every night runs the REAL scheduler on the clocked simulator at the fixed
instant ``T0`` (tests/_group_harness.py), so none depends on the wall clock,
the hour or the PC's time zone. The compiled coordinates are replaced by a
made-up field that is up at ``T0``; the identity does not read them after the
compile.

MUTANT "ignore the phase in the cycle driver" (``SequenceEngine._current_phase``
returns every owed step as current, so the round robin takes all of them),
red on the first night below:
    AssertionError: a phase-2 frame was shot before phase 1 was complete:
    ['L', 'R', 'G', 'B', 'S', 'Ha', 'Oiii', 'L', 'R', 'G', 'B', 'S', 'Ha',
    'Oiii']
MUTANT "drop phase from the session dump" (the serializer pops it whatever its
value), red on the resume case:
    AssertionError: a resume in phase 1 started phase 2 early: ['L', 'R',
    'G', 'B', 'S', 'Ha', 'Oiii', 'R', 'G', 'B', 'S', 'Ha', 'Oiii']
"""
from __future__ import annotations

import hashlib
import json

import pytest

from _group_harness import (T0, Night, group_hub, group_store,  # noqa: F401
                            ra_at)
from astrodeck.events import night_key
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import create_params
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.models import (ExposureStep, SequencePlan,
                                       plan_identity_errors)
from astrodeck.sequence.session import Session, SessionFrame, session_store

#: Any fixed instant: the compile places a typed target by its coordinates,
#: which do not depend on it.
WHEN = 1788313689.0

PHASE_1 = ["L", "R", "G", "B"]
PHASE_2 = ["S", "Ha", "Oiii"]

TARGET = {"name": "M33 - Triangulum", "ra": "01h 33m 51s",
          "dec": "+30 39 37", "rotation": -1}

#: The #1074 flow's step ids as the tree BEFORE this work (origin/main
#: d4549f22, taken in a scratch checkout with the same graph and flow id
#: "f-1074"): the ledger counts frames by these.
BASE_STEP_IDS = [
    ("B", "662abfc0faee5df1a9f0b1ffa4767926"),
    ("G", "2114a5bef4df54108a11127accd08022"),
    ("Ha", "2ec975cd76b1532cb67c35a11e588928"),
    ("L", "062b33b781dc595fa759ab63b2bc0464"),
    ("Oiii", "a1ecbcd575c6523595fcc4ca7d201bcf"),
    ("R", "18cfa195ada852b499bf385c43ed3531"),
    ("S", "fb7bfbd26208515da28144cb0d9c3fbb"),
]
BASE_TARGET_ID = "5642a7a1d72a58108d058223a29eb298"

#: The same for a 2 x 2 mosaic (panel 2-2 skipped) whose lane chains a
#: cycle of L R into a cycle of S Ha, flow id "f-mchain": (row, col, filter,
#: step id), and the id the base tree put in the group's ``skipped_ids``.
BASE_MOSAIC_STEP_IDS = [
    (0, 0, "Ha", "f2de42e6b40c5b638bb1b0acce8c6207"),
    (0, 0, "L", "c14fee54bf4f5c4a893411318279de37"),
    (0, 0, "R", "6514faf9594b5a62b71d881bbc280ea7"),
    (0, 0, "S", "38a0e308434d54389bdc15e92bb72fea"),
    (0, 1, "Ha", "9bd2316fdcab5f3b9d1efb8705124c73"),
    (0, 1, "L", "05986739c95b58ac99ef141aadeec17b"),
    (0, 1, "R", "a34aec663d715ed1abf8ae67bcbe2f43"),
    (0, 1, "S", "ee63d387ef43514882de7b645875f853"),
    (1, 0, "Ha", "c44ca2e64fb153b286ab7ace145ec0a9"),
    (1, 0, "L", "2d47757922195a0cb17c5264fff003de"),
    (1, 0, "R", "c33235aaaf6051fa9abae54c948d00d3"),
    (1, 0, "S", "de5ddd774b38507ab8e2fc3ec0e38013"),
]
BASE_MOSAIC_SKIPPED = ["27e6e4ba744f5ef09a746fbafc076609"]

#: Plan hashes (instruction ids blanked, sorted keys) of flows with NEITHER a
#: chain NOR a POOL, as origin/main d4549f22 compiled them (rule 14: these
#: must not move). Taken with ``flow_id`` as keyed below.
BASE_HASHES = {
    "single_cycle":
        "bad612d971898d33caf7ffb2db59ed2260af20b7ce1cd4fdf0f357a59f5f4783",
    "blocks":
        "ed9187f0295e8eb9a897fb009bd0ae3b2a4a3ada1acd56629e97d3e9e94d0b4d",
    "mosaic":
        "b5159598595b85b1200878ade9035493459eec538b1f4b2ca7d04d3305270eb4",
    "example-m31":
        "fcc1cb6592e284ecb6962a04c87a31d0dff381309740a1dab3a90d12c41958b8",
    "example-m16":
        "d2e333abd343ef3267e84ba82e9d13e765b5cf6f5a82346cae60f311eace95df",
    "example-cycle":
        "8a3c7fb81d1a0b41284d05405c7065e1b6ca623ddb5abe5533ca718fe8ce33cf",
    "example-nb":
        "6567e5999cf9ddf4c700e4f1095f1680d5eb557acc660261a6766990746c308f",
    "example-eaa":
        "b3690db6c0ffbcb23f3fe904a5356591a2b2af75962c26867cff480394d42f21",
    "example-m31-mosaic":
        "f02bd8473abe29e0532ebbef5628b25096403366c40811eb9833ac4aef2f3489",
}


def graph(nodes, edges, over=None) -> FlowGraph:
    over = over or {}
    return FlowGraph(
        nodes=[FlowNode(id=i, type=t, x=float(x), y=float(y),
                        params={**create_params(t), **over.get(i, {})})
               for i, t, x, y in nodes],
        edges=[FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})
               for a, ap, b, bp in edges])


def chained_flow(cycles: int = 45) -> FlowGraph:
    """The #1074 shape: one TARGET, LRGB cycles then SHO cycles, the first
    stage's ``complete`` into the second's ``run``."""
    return graph(
        [("n0", "dusk", 30, 40), ("n2", "target", 30, 100),
         ("n1_musruwjj", "cycle", 300, 100),
         ("n2_musrwj10", "cycle", 600, 100)],
        [("n0", "window", "n2", "arm"), ("n2", "target", "n1_musruwjj", "run"),
         ("n1_musruwjj", "complete", "n2_musrwj10", "run")],
        {"n2": {**TARGET, "name": "NGC7331", "ra": "22h 37m 04s",
                "dec": "+34 24 56"},
         "n1_musruwjj": {"plan": "L 60, R 60, G 60, B 60", "cycles": cycles},
         "n2_musrwj10": {"plan": "S 180, Ha 180, Oiii 180",
                         "cycles": cycles}})


def mosaic_chain_flow(skip: str = "2-2") -> FlowGraph:
    return graph(
        [("n2", "target", 30, 100), ("m1", "cycle", 300, 100),
         ("m2", "cycle", 600, 100)],
        [("n2", "target", "m1", "run"), ("m1", "complete", "m2", "run")],
        {"n2": {**TARGET, "rows": 2, "cols": 2, "overlap": 20, "fovX": 1.3,
                "fovY": 0.9, "angle": "Camera fixed at PA", "rotation": 10,
                "passes": 1, "skip": skip},
         "m1": {"plan": "L 60, R 60", "cycles": 3},
         "m2": {"plan": "S 180, Ha 180", "cycles": 3}})


def cycle_and_captures(*stages: str) -> FlowGraph:
    """A TARGET whose lane is ``stages`` in this order, each wired to the
    next: "cycle" is a FILTER CYCLE of L R (two cycles), "capture A" and
    "capture B" are CAPTURE LOOPs of 3 and 2 frames."""
    kinds = {"cycle": ("cycle", {"plan": "L 60, R 60", "cycles": 2}),
             "capture A": ("capture", {"filter": "Ha", "exposure": 120,
                                       "count": 3}),
             "capture B": ("capture", {"filter": "Oiii", "exposure": 90,
                                       "count": 2})}
    nodes = [("n2", "target", 30, 100)]
    edges = []
    over = {"n2": TARGET}
    prev, port = "n2", "target"
    for k, stage in enumerate(stages):
        kind, params = kinds[stage]
        nid = f"s{k}"
        nodes.append((nid, kind, 300 + 300 * k, 100))
        edges.append((prev, port, nid, "run"))
        over[nid] = params
        prev, port = nid, "complete"
    return graph(nodes, edges, over)


def compiled(g: FlowGraph, flow_id: str):
    return to_sequence_plan(compile_plan(g, "x"), g, flow_id=flow_id,
                            when=WHEN)


def digest(g: FlowGraph, flow_id: str) -> str:
    plan, _ = compiled(g, flow_id)
    got = plan.model_dump(mode="json")
    for ins in got.get("instructions", []):
        ins["id"] = ""
    return hashlib.sha256(json.dumps(got, sort_keys=True).encode()).hexdigest()


def _phases(plan) -> list[tuple[str, int]]:
    return [(s.filter, s.phase) for t in plan.targets for s in t.steps]


def _on_the_made_up_field(plan) -> None:
    """Every target on one made-up field that stands east of the meridian at
    ``T0``, with the extras of a bare night off, so the night is the stages
    and nothing else."""
    for t in plan.targets:
        t.ra_hours = ra_at(-2.0)
        t.dec_deg = 40.0
        t.autofocus_first = False
    plan.guide = False
    plan.dither_every = 0
    plan.autofocus_every = 0
    plan.meridian_flip = False
    plan.park_when_done = False
    plan.warm_cooler_when_done = False
    plan.recover_guiding = False


# ---------------------------------------------------------------- the compile

def test_two_chained_cycles_stay_one_target_with_phased_steps():
    plan, unmapped = compiled(chained_flow(), "f-1074")
    assert plan.groups == [], plan.groups
    (target,) = plan.targets
    assert [(s.filter, s.phase) for s in target.steps] == (
        [(f, 0) for f in PHASE_1] + [(f, 1) for f in PHASE_2])
    assert target.name == "NGC7331" and target.acquisition == "cycle"
    assert plan_identity_errors(plan) == []
    assert not [u for u in unmapped if u["key"].startswith("lane")], unmapped


def test_the_step_ids_and_the_target_id_are_the_base_trees():
    """The stage node keys the ids, exactly as on origin/main d4549f22: every
    id the ledger holds is still a live step."""
    plan, _ = compiled(chained_flow(), "f-1074")
    got = sorted((s.filter, s.id) for t in plan.targets for s in t.steps)
    assert got == BASE_STEP_IDS
    assert [t.id for t in plan.targets] == [BASE_TARGET_ID]


def test_a_chained_mosaic_has_the_panels_and_ids_the_base_tree_gave_it():
    """No panel is added, renamed or re-keyed: each cell is one panel whose
    steps carry the phase of their stage, and the skipped panel's id is the
    base tree's, once."""
    plan, _ = compiled(mosaic_chain_flow(), "f-mchain")
    (group,) = plan.groups
    assert len(plan.targets) == 3
    assert sorted((t.panel_row, t.panel_col, s.filter, s.id)
                  for t in plan.targets for s in t.steps) \
        == BASE_MOSAIC_STEP_IDS
    assert group.skipped_ids == BASE_MOSAIC_SKIPPED
    for t in plan.targets:
        assert [(s.filter, s.phase) for s in t.steps] == [
            ("L", 0), ("R", 0), ("S", 1), ("Ha", 1)]
    assert plan_identity_errors(plan) == []


def test_phase_is_absent_from_the_dump_at_zero_and_present_when_set():
    assert "phase" not in ExposureStep(filter="L", exposure_s=1,
                                       count=1).model_dump(mode="json")
    assert ExposureStep(filter="L", exposure_s=1, count=1,
                        phase=2).model_dump(mode="json")["phase"] == 2
    plan, _ = compiled(chained_flow(), "f-1074")
    dumped = plan.model_dump(mode="json")["targets"][0]["steps"]
    assert ["phase" in s for s in dumped] == [False] * 4 + [True] * 3
    again = SequencePlan.model_validate(plan.model_dump(mode="json"))
    assert [s.phase for s in again.targets[0].steps] == [0] * 4 + [1] * 3


def test_flows_with_no_chain_and_no_pool_compile_to_the_base_trees_plan():
    """Rule 14: a single cycle, a blocks flow, a mosaic and every Example
    that has neither a chain nor a pool hash exactly as they did on
    d4549f22."""
    single = graph(
        [("n2", "target", 30, 100), ("n7", "cycle", 300, 100)],
        [("n2", "target", "n7", "run")],
        {"n2": TARGET, "n7": {"plan": "L 60, R 60, G 60, B 60", "cycles": 3}})
    blocks = graph(
        [("n2", "target", 30, 100), ("n7", "capture", 300, 100),
         ("n8", "capture", 600, 100)],
        [("n2", "target", "n7", "run"), ("n7", "complete", "n8", "run")],
        {"n2": TARGET, "n7": {"filter": "L", "exposure": 60, "count": 4},
         "n8": {"filter": "Ha", "exposure": 120, "count": 3}})
    mosaic = graph(
        [("n2", "target", 30, 100), ("n7", "cycle", 300, 100)],
        [("n2", "target", "n7", "run")],
        {"n2": {**TARGET, "rows": 2, "cols": 2, "overlap": 20, "fovX": 1.3,
                "fovY": 0.9, "angle": "Camera fixed at PA", "rotation": 10,
                "passes": 1},
         "n7": {"plan": "L 60, R 60", "cycles": 3}})
    got = {"single_cycle": digest(single, "f-single"),
           "blocks": digest(blocks, "f-blocks"),
           "mosaic": digest(mosaic, "f-mosaic")}
    for ex in examples():
        if ex.id in BASE_HASHES:
            got[ex.id] = digest(ex.graph, ex.id)
    assert got == BASE_HASHES


def test_every_stage_of_a_chain_is_a_phase_in_the_lanes_own_order():
    """A cycle then a CAPTURE LOOP, a CAPTURE LOOP then a cycle, and a cycle
    then two CAPTURE LOOPs are all chains: one target, the phase the stage's
    place in the lane."""
    for stages, want in [
            (("cycle", "capture A"),
             [("L", 0), ("R", 0), ("Ha", 1)]),
            (("capture A", "cycle"),
             [("Ha", 0), ("L", 1), ("R", 1)]),
            (("cycle", "capture A", "capture B"),
             [("L", 0), ("R", 0), ("Ha", 1), ("Oiii", 2)])]:
        plan, unmapped = compiled(cycle_and_captures(*stages), "f-lane")
        assert plan.groups == [] and len(plan.targets) == 1, stages
        assert _phases(plan) == want, stages
        assert not [u for u in unmapped if u["key"].startswith("lane")]


def test_a_phase_follows_the_wires_whatever_order_the_stages_were_drawn_in():
    """The wires say the second cycle (drawn to the right) runs first: the
    compile lists the steps in the wires' order, and so do the phases."""
    g = graph(
        [("n2", "target", 30, 100), ("c1", "cycle", 300, 100),
         ("c2", "cycle", 600, 100)],
        [("n2", "target", "c2", "run"), ("c2", "complete", "c1", "run")],
        {"n2": TARGET, "c1": {"plan": "L 60, R 60", "cycles": 2},
         "c2": {"plan": "S 180, Ha 180", "cycles": 2}})
    plan, _ = compiled(g, "f-swapped")
    assert _phases(plan) == [("S", 0), ("Ha", 0), ("L", 1), ("R", 1)]


def test_lanes_that_are_no_chain_get_no_phase():
    """One stage, a lane of CAPTURE LOOPs alone, a lane that branches, a
    block with a loop wire and a POOL's lane: nothing to order, or nothing
    that can be, and each compiles as it always did."""
    alone = cycle_and_captures("cycle")
    loops = cycle_and_captures("capture A", "capture B")
    branched = graph(
        [("n2", "target", 30, 100), ("c1", "cycle", 300, 60),
         ("c2", "cycle", 300, 200)],
        [("n2", "target", "c1", "run"), ("n2", "target", "c2", "run")],
        {"n2": TARGET, "c1": {"plan": "L 60, R 60", "cycles": 2},
         "c2": {"plan": "S 180, Ha 180", "cycles": 2}})
    pool = graph(
        [("n20", "pool", 30, 100), ("c1", "cycle", 300, 100),
         ("k2", "capture", 600, 100)],
        [("n20", "target", "c1", "run"), ("c1", "complete", "k2", "run")],
        {"n20": {"members": "M31, M33"},
         "c1": {"plan": "L 60, R 60", "cycles": 2},
         "k2": {"filter": "Ha", "exposure": 120, "count": 3}})
    for name, g in [("alone", alone), ("loops", loops),
                    ("branched", branched), ("pool", pool)]:
        plan, _ = compiled(g, "f-none")
        assert {s.phase for t in plan.targets for s in t.steps} == {0}, name


def test_a_flow_with_no_flow_id_still_compiles_its_phases():
    plan, _ = to_sequence_plan(compile_plan(chained_flow(), "x"),
                               chained_flow(), when=WHEN)
    assert [p for _f, p in _phases(plan)] == [0, 0, 0, 0, 1, 1, 1]
    assert plan_identity_errors(plan) == []


# ------------------------------------------------------------------ the night

async def _night(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0, **kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _filters(night: Night) -> list[str]:
    return [f for _t, f in night.shots()]


async def test_no_phase_two_frame_is_shot_before_the_phase_one_cycles_complete(
        group_hub, monkeypatch):
    """The #1074 flow on the simulator, two cycles a stage: every L R G B
    frame comes before the first S Ha Oiii frame, the order is exactly the
    two cycles in turn, and the night completes."""
    plan, _ = compiled(chained_flow(cycles=2), "f-1074")
    _on_the_made_up_field(plan)
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _filters(night) == PHASE_1 * 2 + PHASE_2 * 2, (
        f"a phase-2 frame was shot before phase 1 was complete: "
        f"{_filters(night)}")
    assert night.stored.owed() == 0 and night.stored.status == "complete"


async def test_a_capture_loop_after_a_cycle_shoots_in_blocks_once_it_completes(
        group_hub, monkeypatch):
    """A cycle of L R (two cycles), then a CAPTURE LOOP of 3 Ha, then one of
    2 Oiii: the cycle round-robins, then each loop runs to completion in
    turn. Before, the three stages were one round robin of one frame each."""
    plan, _ = compiled(cycle_and_captures("cycle", "capture A", "capture B"),
                       "f-lane")
    _on_the_made_up_field(plan)
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _filters(night) == ["L", "R"] * 2 + ["Ha"] * 3 + ["Oiii"] * 2, (
        _filters(night))
    assert night.stored.owed() == 0


async def test_a_capture_loop_before_a_cycle_shoots_first(group_hub,
                                                          monkeypatch):
    plan, _ = compiled(cycle_and_captures("capture A", "cycle"), "f-lane")
    _on_the_made_up_field(plan)
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _filters(night) == ["Ha"] * 3 + ["L", "R"] * 2, _filters(night)


async def test_a_chained_mosaic_panel_completes_its_first_phase_before_its_second(
        group_hub, monkeypatch):
    """A 2 x 2 mosaic, its lane L R (three cycles) then S Ha (three): every
    panel shoots all of its L and R before any S or Ha of its own, whichever
    order the panels are visited in."""
    h = ra_at(-2.0)
    hms = f"{int(h):02d}h {int(h % 1 * 60):02d}m {int(h * 3600 % 60):02d}s"
    g = mosaic_chain_flow(skip="")
    node = next(n for n in g.nodes if n.id == "n2")
    node.params.update({"ra": hms, "dec": "+40 00 00"})
    plan, _ = compiled(g, "f-mchain")
    for t in plan.targets:
        t.autofocus_first = False
    plan.guide = False
    plan.dither_every = plan.autofocus_every = 0
    plan.meridian_flip = plan.park_when_done = False
    plan.warm_cooler_when_done = plan.recover_guiding = False
    assert len(plan.targets) == 4
    night = Night(group_hub, monkeypatch, sky=lambda who, n: 10.0)
    try:
        done = await night.run(plan, wall_s=90.0)
    finally:
        await night.close()
    assert done, night.trace[-3:]
    shots = night.shots()
    assert len(shots) == 4 * 12, len(shots)
    for panel in {who for who, _f in shots}:
        own = [f for who, f in shots if who == panel]
        assert own == ["L", "R"] * 3 + ["S", "Ha"] * 3, (panel, own)
    assert session_store.load(night.session_id).status == "complete"


def _one_l_banked(plan) -> Session:
    first = plan.targets[0]
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    session.frames.append(SessionFrame(
        ts=T0 - 3600.0, night="n0", target_id=first.id,
        step_id=first.steps[0].id, auto_accepted=True))
    return session


async def test_a_resume_in_phase_one_stays_in_phase_one_from_the_stored_plan(
        group_hub, monkeypatch):
    """One L is banked and the session goes to disk and back, as a resume
    reads it: the plan it loads is the file's, so the phases must be in the
    file. Phase 1 still owes seven of its eight frames, and phase 2 starts
    only after the last of them."""
    plan, _ = compiled(chained_flow(cycles=2), "f-1074")
    _on_the_made_up_field(plan)
    session = _one_l_banked(plan)
    session_store.save(session)
    stored = session_store.load(session.id)
    night = await _night(group_hub, monkeypatch, stored.plan, session=stored)
    assert night.done, night.trace[-3:]
    assert _filters(night) == ["L", "R", "G", "B", "R", "G", "B",
                               *PHASE_2, *PHASE_2], (
        f"a resume in phase 1 started phase 2 early: {_filters(night)}")
    assert night.stored.owed() == 0
    assert [s.phase for s in stored.plan.targets[0].steps] == [0] * 4 + [1] * 3


async def test_a_session_banked_before_the_change_resumes_as_it_did(
        group_hub, monkeypatch):
    """A plan stored before chains had phases has none, and it resumes as it
    always did: one round robin of every step."""
    plan, _ = compiled(chained_flow(cycles=2), "f-1074")
    _on_the_made_up_field(plan)
    for s in plan.targets[0].steps:
        s.phase = 0
    stored_json = json.dumps(plan.model_dump(mode="json"))
    assert '"phase"' not in stored_json
    old = SequencePlan.model_validate(json.loads(stored_json))
    night = await _night(group_hub, monkeypatch, old)
    assert night.done, night.trace[-3:]
    assert _filters(night) == [*PHASE_1, *PHASE_2] * 2, _filters(night)


async def test_a_continue_counts_the_frames_the_flattened_plan_banked(
        group_hub, monkeypatch):
    """The flattened plan banked two of each of LRGB under the ids above. The
    CONTINUE compiles the flow again (same ids, now with phases): phase 1 is
    complete in the ledger, so the night shoots only phase 2."""
    plan, _ = compiled(chained_flow(cycles=2), "f-1074")
    _on_the_made_up_field(plan)
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    for flt, step_id in BASE_STEP_IDS:
        if flt in PHASE_1:
            for _ in range(2):
                session.frames.append(SessionFrame(
                    ts=T0 - 3600.0, night="n0", target_id=BASE_TARGET_ID,
                    step_id=step_id, auto_accepted=True))
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert _filters(night) == PHASE_2 * 2, _filters(night)


async def test_a_phase_set_aside_tonight_holds_the_later_phases_for_tonight(
        group_hub, monkeypatch):
    """The reject guard set L aside earlier tonight. R G B still shoot, but L
    stays owed, so phase 1 never completes tonight and phase 2 does not start:
    the target ends with the line that says why, and everything stays owed to
    the next night."""
    plan, _ = compiled(chained_flow(cycles=2), "f-1074")
    _on_the_made_up_field(plan)
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    session.note_set_aside(plan.targets[0].id, "rejects", night=night_key(T0),
                           step_id=plan.targets[0].steps[0].id)
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert _filters(night) == ["R", "G", "B"] * 2, _filters(night)
    held = [m for _t, lvl, m in night.lines
            if "later stages" in m and "do not start tonight" in m]
    assert len(held) == 1 and "(L)" in held[0], night.lines[-6:]
    assert night.stored.owed() > 0


async def test_every_step_of_the_first_phase_set_aside_leaves_nothing_tonight(
        group_hub, monkeypatch):
    plan, _ = compiled(chained_flow(cycles=2), "f-1074")
    _on_the_made_up_field(plan)
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    for s in plan.targets[0].steps[:4]:
        session.note_set_aside(plan.targets[0].id, "rejects",
                               night=night_key(T0), step_id=s.id)
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert _filters(night) == [], night.shots()
    assert night.stored.owed() == 14
