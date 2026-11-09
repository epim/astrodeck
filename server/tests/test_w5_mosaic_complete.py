# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``on_mosaic_complete``: a REPORT's 'target done' after a mosaic fires once
for the mosaic, not once per panel (#184, WP 5.2).

THE DEFECT. REPORT 'target done' compiled to ``on_target_complete`` with no
target gate, and the engine ran those rules at every target's completion,
every mosaic panel included. A NOTIFY drawn after a 2x2 mosaic sent four
notifications, and one drawn after a single target M33, in a graph that also
held a 3x2 mosaic, fired seven times.

THE FIX, in three parts, each held below:

* the engine raises ``on_mosaic_complete`` ONCE, when the last member of a
  `TargetGroup` completes (`SequenceEngine._fire_target_complete`), and a
  panel's own completion no longer runs ``on_target_complete`` rules;
* the compile turns a REPORT 'target done' wire that leaves a multi-panel
  TARGET block's lane into ``on_mosaic_complete`` (`compile._trigger_for`),
  and every other one stays ``on_target_complete``;
* the doctor's M14 note, which warned about the per-panel firing, is gone.

THE NAMED MUTANT: "fire per panel" (``on_mosaic_complete`` raised at every
member's completion, not the last's). `test_a_2x2_mosaic_notifies_once_when
_its_last_panel_completes` turns red: four notifications, expected one.

The runs are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py). Nothing here reads the wall clock: the night's
time is the harness's fake clock, and the compile tests read no clock.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store, plain_mosaic_plan, single)
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import LEGAL_TRIGGERS, to_sequence_plan
from astrodeck.sequence.instructions import TriggerContext, evaluate_instructions
from astrodeck.sequence.models import Instruction
from astrodeck.sequence.session import session_store

PANEL_DONE = "a panel is complete"
MOSAIC_DONE = "the mosaic is complete"
TARGET_DONE = "a target is complete"


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _rules() -> list[Instruction]:
    return [
        Instruction(id="per-target", trigger="on_target_complete",
                    action="notify", message=TARGET_DONE, level="info"),
        Instruction(id="per-mosaic", trigger="on_mosaic_complete",
                    action="notify", message=MOSAIC_DONE, level="info"),
    ]


async def _run(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    return night


def _last_frame_end(night: Night, who: str) -> float:
    return max(c["t"] + c["exposure_s"] for c in night.captures
               if c["target"] == who)


# ------------------------------------------------------------------ the engine


async def test_a_2x2_mosaic_notifies_once_when_its_last_panel_completes(
        group_hub, monkeypatch):
    """The issue's first case: a REPORT 'target done' -> NOTIFY after a 2x2
    mosaic. The rule the flow compiles is taken from the real compile, so the
    two halves are held together: one notification, at the fake instant the
    last panel's last frame ended, and no ``on_target_complete`` rule ran for
    any panel.

    MUTANT "fire per panel" (`_fire_target_complete` raising
    ``on_mosaic_complete`` at every member's completion, without asking
    whether the group is complete): RED (observed):
        AssertionError: ['the mosaic is complete', 'the mosaic is complete',
        'the mosaic is complete', 'the mosaic is complete']
        assert 4 == 1
    """
    plan = grid_plan(instructions=_rules())
    night = await _run(group_hub, monkeypatch, plan)
    fired = [t for t, _lvl, m in night.lines if m == MOSAIC_DONE]
    assert len(fired) == 1, night.said(MOSAIC_DONE)
    last = max(_last_frame_end(night, _name(lb))
               for lb in ("1-1", "1-2", "2-1", "2-2"))
    assert fired == [last], (night.rel(fired[0]), night.rel(last))
    assert night.said(TARGET_DONE) == [], (
        "a panel is not a target the operator drew: it fires no "
        "on_target_complete rule")


async def test_the_flow_compiled_rule_notifies_once_for_a_2x2_mosaic(
        group_hub, monkeypatch):
    """The same night with the rule coming out of `compile_plan` and
    `to_sequence_plan` for a flow that draws REPORT 'target done' -> NOTIFY
    after a 2x2 block, as the user draws it."""
    compiled = compile_plan(_mosaic_flow(rows=2, cols=2), "n")
    flow_plan, _unmapped = to_sequence_plan(compiled, _mosaic_flow(rows=2,
                                                                   cols=2))
    assert [i.trigger for i in flow_plan.instructions] == ["on_mosaic_complete"]
    rule = flow_plan.instructions[0].model_copy(
        update={"message": MOSAIC_DONE, "level": "info"})
    night = await _run(group_hub, monkeypatch, grid_plan(instructions=[rule]))
    assert len(night.said(MOSAIC_DONE)) == 1, night.said(MOSAIC_DONE)


async def test_a_single_target_beside_a_mosaic_fires_once_for_itself(
        group_hub, monkeypatch):
    """The issue's second case: M33 and a mosaic in one plan. The
    ``on_target_complete`` rule fires once, for M33 (not once per panel and
    once for M33), and the mosaic's fires once, after its last panel."""
    plan = grid_plan(rows=1, cols=2, before=[single("M33")],
                     instructions=_rules())
    night = await _run(group_hub, monkeypatch, plan)
    assert len(night.said(TARGET_DONE)) == 1, night.said(TARGET_DONE)
    assert len(night.said(MOSAIC_DONE)) == 1, night.said(MOSAIC_DONE)
    solo = next(t for t, _lvl, m in night.lines if m == TARGET_DONE)
    assert solo == _last_frame_end(night, "M33")
    mosaic = next(t for t, _lvl, m in night.lines if m == MOSAIC_DONE)
    assert mosaic == max(_last_frame_end(night, _name("1-1")),
                         _last_frame_end(night, _name("1-2")))


async def test_a_mosaic_with_a_panel_set_aside_fires_nothing_tonight(
        group_hub, monkeypatch):
    """A 1x2 whose guider never starts on 1-1: 1-1 is set aside after three
    deferred visits, 1-2 completes. A mosaic with a member set aside tonight
    is not complete tonight, so ``on_mosaic_complete`` does not fire; and 1-2,
    a panel, fires no ``on_target_complete`` rule either."""
    plan = grid_plan(rows=1, cols=2, guide=True, instructions=_rules())
    night = await _run(group_hub, monkeypatch, plan,
                       guide=lambda who, n: who != _name("1-1"))
    assert {t for t, _f in night.shots()} == {_name("1-2")}, night.shots()
    assert night.said("set aside for tonight"), night.lines[-6:]
    assert night.said(MOSAIC_DONE) == [], night.said(MOSAIC_DONE)
    assert night.said(TARGET_DONE) == [], night.said(TARGET_DONE)


async def test_a_classic_plan_mosaic_keeps_per_target_firing(
        group_hub, monkeypatch):
    """A ``mosaic_group`` with no `TargetGroup` entry (a mosaic the classic
    Plan made) is no group: each of its three targets is a target, and
    ``on_target_complete`` fires at each, as it always did."""
    plan = plain_mosaic_plan()
    plan = plan.model_copy(update={"instructions": _rules()})
    night = await _run(group_hub, monkeypatch, plan)
    assert len(night.said(TARGET_DONE)) == 3, night.said(TARGET_DONE)
    assert night.said(MOSAIC_DONE) == []


async def test_two_panels_reaching_a_complete_group_run_its_rules_once():
    """Two members can reach `_fire_target_complete` with the group already
    complete: one owed its rules after a jump (``_completion_owed``) and the
    group's last. The group's rules run once. A stand-in for the engine over
    `_fire_mosaic_complete` alone, as the S4 spec tests do, because no night
    orders the two without the owed panel going first.

    MUTANT "no once guard" (the ``_mosaic_completion_fired`` test deleted from
    `_fire_mosaic_complete`): RED, ``assert 2 == 1`` (two runs)."""
    from types import SimpleNamespace

    from astrodeck.sequence.group_rules import GroupRun
    from astrodeck.sequence.engine import SequenceEngine
    from astrodeck.sequence.models import Target, TargetGroup

    run = GroupRun({"a": "1-1", "b": "1-2"}, max_failed_visits=3)
    ran: list[dict] = []

    async def context(**event):
        return event

    async def run_instructions(ctx, target, step):
        ran.append(ctx)

    stand_in = SimpleNamespace(
        _group_runs={"g": run}, _mosaic_completion_fired=set(),
        _boundary_context=context, _run_instructions=run_instructions)
    group = TargetGroup(id="g", name="G", geometry={"rows": 1, "cols": 2})
    member = Target(id="a", name="G 1-1", ra_hours=1.0, dec_deg=10.0)
    fire = SequenceEngine._fire_mosaic_complete

    run.completed.add("a")
    await fire(stand_in, group, member)
    assert ran == [], "one panel of two is not a complete mosaic"
    run.completed.add("b")
    await fire(stand_in, group, member)
    await fire(stand_in, group, member)
    assert ran == [{"mosaic_complete": True, "active_target": "G"}], ran


# ------------------------------------------------------------ the evaluator


def test_on_mosaic_complete_is_a_trigger_the_engine_can_raise():
    """The enum member exists because the engine raises it: the flow layer's
    legality set reads the tuple."""
    assert "on_mosaic_complete" in LEGAL_TRIGGERS


def test_the_evaluator_fires_on_mosaic_complete_for_the_named_group_only():
    rule = Instruction(id="m", trigger="on_mosaic_complete", action="notify",
                       only_target="M31")
    on = TriggerContext(now_ts=0.0, mosaic_complete=True, active_target="M31")
    other = TriggerContext(now_ts=0.0, mosaic_complete=True,
                           active_target="M81")
    quiet = TriggerContext(now_ts=0.0, active_target="M31")
    assert [f.instruction_id for f in
            evaluate_instructions([rule], on, {})[0]] == ["m"]
    assert evaluate_instructions([rule], other, {})[0] == []
    assert evaluate_instructions([rule], quiet, {})[0] == []


def test_a_target_completion_does_not_raise_the_mosaic_trigger():
    rule = Instruction(id="m", trigger="on_mosaic_complete", action="notify")
    ctx = TriggerContext(now_ts=0.0, target_complete=True,
                         active_target="M33")
    assert evaluate_instructions([rule], ctx, {})[0] == []


# --------------------------------------------------------------- the compile

RA = "00h 42m 44s"
DEC = "+41 16 09"


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=float(x), y=0.0, params=params)


def _e(src, sp, dst, dp):
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _target(nid, name, x, rows=1, cols=1):
    return _n(nid, "target", x, name=name, ra=RA, dec=DEC, rows=rows,
              cols=cols, overlap=25, fovX=2.0, fovY=1.33,
              angle="Rotate to PA", rotation=30, counts="Accepted subs")


def _mosaic_flow(*, rows=3, cols=2, report_after="capture") -> FlowGraph:
    """DUSK -> M31 block -> CAPTURE -> REPORT 'target done' -> NOTIFY. The
    REPORT hangs off the block's stage (``report_after="capture"``) or off
    the block itself (``"block"``)."""
    up = "c" if report_after == "capture" else "t"
    up_port = "complete" if report_after == "capture" else "target"
    nodes = [_n("d", "dusk", 0, stop="Dawn"), _target("t", "M31", 200,
                                                      rows, cols),
             _n("c", "capture", 400, filter="Ha"), _n("r", "report", 600),
             _n("nt", "notify", 800)]
    edges = [_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
             _e(up, up_port, "r", "session"), _e("r", "done", "nt", "do")]
    return FlowGraph(nodes=nodes, edges=edges)


def _beside_a_mosaic() -> FlowGraph:
    """The issue's evidence: a 3x2 M31 block (no REPORT of its own) beside a
    single M33 -> CAPTURE -> REPORT 'target done' -> NOTIFY."""
    nodes = [_n("d", "dusk", 0, stop="Dawn"), _target("t", "M31", 200, 3, 2),
             _n("c", "capture", 400, filter="Ha"),
             _target("t2", "M33", 900), _n("c2", "capture", 1100, filter="Ha"),
             _n("r2", "report", 1300), _n("nt", "notify", 1500)]
    edges = [_e("d", "window", "t", "arm"), _e("d", "window", "t2", "arm"),
             _e("t", "target", "c", "run"), _e("t2", "target", "c2", "run"),
             _e("c2", "complete", "r2", "session"),
             _e("r2", "done", "nt", "do")]
    return FlowGraph(nodes=nodes, edges=edges)


def _triggers(graph: FlowGraph) -> list[str]:
    return [r["when"] for r in compile_plan(graph, "n")["instructions"]]


@pytest.mark.parametrize("after", ["capture", "block"])
def test_a_report_ending_a_mosaic_lane_compiles_to_on_mosaic_complete(after):
    """Its single flow parent is the block, or a stage the block owns."""
    assert _triggers(_mosaic_flow(report_after=after)) == [
        "on_mosaic_complete"]


def test_a_report_after_a_single_target_stays_on_target_complete():
    """M33's REPORT, drawn in a graph that also holds a mosaic, is a rule
    about M33: it is not the mosaic's."""
    assert _triggers(_beside_a_mosaic()) == ["on_target_complete"]


def test_a_report_after_a_one_panel_block_stays_on_target_complete():
    assert _triggers(_mosaic_flow(rows=1, cols=1)) == ["on_target_complete"]


def test_both_reports_in_one_graph_each_get_their_own_trigger():
    """The mosaic's REPORT and M33's, in one graph."""
    g = _beside_a_mosaic()
    g.nodes += [_n("r1", "report", 700), _n("n1", "notify", 900)]
    g.edges += [_e("c", "complete", "r1", "session"),
                _e("r1", "done", "n1", "do")]
    assert sorted(_triggers(g)) == ["on_mosaic_complete", "on_target_complete"]


def test_the_mosaic_rule_is_a_legal_plan_rule_with_nothing_lost():
    g = _mosaic_flow()
    plan, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
    assert [i.trigger for i in plan.instructions] == ["on_mosaic_complete"]
    assert not [u for u in unmapped if "will not run" in u["detail"]], unmapped


def test_a_dark_queue_after_a_mosaic_still_funds_the_day_darks():
    """REPORT 'target done' -> CALIBRATION QUEUE 'do' is the end-of-night
    darks (the quick sheet's DARKS AFTER chip). Drawn after a mosaic it is
    now ``on_mosaic_complete``, and must fund the day darks as the
    ``on_target_complete`` wire did."""
    g = _mosaic_flow()
    g.nodes = [n for n in g.nodes if n.id != "nt"] + [
        _n("cq", "calib", 800, darks="Always", quota=10)]
    g.edges = [e for e in g.edges if e.to != "nt"] + [
        _e("r", "done", "cq", "do")]
    plan, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
    assert plan.day_darks > 0, (plan.day_darks, unmapped)


# ----------------------------------------------------------- the plan-hash pin


def _without_ids(o):
    """A plan's ids are minted fresh on every build: drop them from the hash."""
    if isinstance(o, dict):
        return {k: _without_ids(v) for k, v in o.items() if k != "id"}
    if isinstance(o, list):
        return [_without_ids(v) for v in o]
    return o


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:16]


def _pin_target(nid: str, name: str, x: float) -> FlowNode:
    """The target the hash script drew: no grid keys at all."""
    return _n(nid, "target", x, name=name, ra=RA, dec=DEC,
              counts="Accepted subs")


def _single_with_report() -> FlowGraph:
    nodes = [_n("d", "dusk", 0, stop="Dawn"), _pin_target("t", "M33", 200),
             _n("c", "capture", 400, filter="Ha"), _n("r", "report", 600),
             _n("nt", "notify", 800)]
    edges = [_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
             _e("c", "complete", "r", "session"), _e("r", "done", "nt", "do")]
    return FlowGraph(nodes=nodes, edges=edges)


def _single_without_report() -> FlowGraph:
    nodes = [_n("d", "dusk", 0, stop="Dawn"), _pin_target("t", "M33", 200),
             _n("c", "capture", 400, filter="Ha")]
    edges = [_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")]
    return FlowGraph(nodes=nodes, edges=edges)


def _two_singles_with_report() -> FlowGraph:
    nodes = [_n("d", "dusk", 0, stop="Dawn"), _pin_target("t1", "M33", 200),
             _pin_target("t2", "M81", 900), _n("c", "capture", 400, filter="Ha"),
             _n("c2", "capture", 1100, filter="Ha"), _n("r", "report", 600),
             _n("nt", "notify", 800)]
    edges = [_e("d", "window", "t1", "arm"), _e("d", "window", "t2", "arm"),
             _e("t1", "target", "c", "run"), _e("t2", "target", "c2", "run"),
             _e("c", "complete", "r", "session"), _e("r", "done", "nt", "do")]
    return FlowGraph(nodes=nodes, edges=edges)


#: Taken on the base tree (origin/main d4549f22) before this change, with the
#: scratch script program/scratch-5.2/hashpin.py: the compiled dict, the
#: SequencePlan (its ids dropped) and the unmapped list of each flow. A flow with no multi-panel
#: block must compile to the same bytes after the change.
PLAN_HASHES = {
    "single_with_report": ("955bae1993d7dd73", "811b6a2fc63171ed",
                           "bd21325fa73a7efc"),
    "single_no_report": ("69d9f11d43005949", "bd7ed4f0f628d58d",
                         "c9e6e20edd6691ab"),
    "two_singles_with_report": ("651aa3390bf734b5", "c729f16631743d80",
                                "291ffc9db2954926"),
}


@pytest.mark.parametrize("name,build", [
    ("single_with_report", _single_with_report),
    ("single_no_report", _single_without_report),
    ("two_singles_with_report", _two_singles_with_report)])
def test_a_flow_with_no_mosaic_compiles_to_the_bytes_it_always_did(name,
                                                                   build):
    g = build()
    compiled = compile_plan(g, "n")
    plan, unmapped = to_sequence_plan(compiled, g)
    assert (_sha(compiled), _sha(_without_ids(plan.model_dump())),
            _sha(unmapped)) == PLAN_HASHES[name]
