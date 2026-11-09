# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The engine reads only a `kind: "mosaic"` group as a mosaic (WP 5.1c, #184,
#183).

THE DEFECT. Until a POOL became a group, every `plan.groups` entry was a
mosaic, and three engine readers still assumed so. A pool's candidate is a
target the operator drew, not a panel of a grid:

* its completion runs its own ``on_target_complete`` rules (a REPORT 'target
  done' after each candidate), and never ``on_mosaic_complete``, which a
  pool has no such event for (`_fire_target_complete`);
* its light frames carry no ``MOSAIC`` / ``PANEL`` FITS cards, and the report
  rows no mosaic or panel id (`_panel_labels`, `_panel_identity`).

NAMED MUTANT: every group read as a mosaic in `_fire_target_complete` (the
kind check dropped): `test_each_pool_candidate_fires_its_own_target_done`
turns red, no ``on_target_complete`` rule having run for any candidate.

The runs are the real scheduler on the clocked simulator, at the fixed
instant the harness gives every night, so no hour or time zone changes them.
"""
from __future__ import annotations

from types import SimpleNamespace

from _group_harness import Night, group_hub, group_store  # noqa: F401
from test_w5_pool_group import CANDIDATES, GID, pool_plan
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import Instruction
from astrodeck.sequence.session import session_store

TARGET_DONE = "a target is complete"
MOSAIC_DONE = "the mosaic is complete"


def _rules() -> list[Instruction]:
    return [
        Instruction(id="per-target", trigger="on_target_complete",
                    action="notify", message=TARGET_DONE, level="info"),
        Instruction(id="per-mosaic", trigger="on_mosaic_complete",
                    action="notify", message=MOSAIC_DONE, level="info"),
    ]


async def _night(hub, monkeypatch, plan) -> Night:
    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    return night


async def test_each_pool_candidate_fires_its_own_target_done(
        group_hub, monkeypatch):
    """Three candidates, each shot to completion: the rule drawn after the
    pool fires once per candidate, and the mosaic rule never.

    MUTANT "every group is a mosaic" (the kind check dropped from
    `_fire_target_complete`): RED, the candidates' completions all reach
    `_fire_mosaic_complete`:
        assert 0 == 3   (on_target_complete notifications)
    """
    plan = pool_plan("best").model_copy(update={"instructions": _rules()})
    night = await _night(group_hub, monkeypatch, plan)
    assert len(night.said(TARGET_DONE)) == len(CANDIDATES), (
        night.said(TARGET_DONE), night.said(MOSAIC_DONE))
    assert night.said(MOSAIC_DONE) == []


async def test_a_pool_candidates_frames_carry_no_mosaic_or_panel_cards(
        group_hub, monkeypatch):
    plan = pool_plan("best")
    night = await _night(group_hub, monkeypatch, plan)
    assert night.captures, "premise: the night shot frames"
    for rec in night.captures:
        extra = rec["extra"]
        assert "mosaic" not in extra and "panel" not in extra, (
            rec["target"], extra)


async def test_a_pool_candidates_report_rows_carry_no_mosaic_identity(
        group_hub, monkeypatch):
    plan = pool_plan("best")
    await _night(group_hub, monkeypatch, plan)
    reader = SequenceEngine.__new__(SequenceEngine)
    reader._groups = {g.id: g for g in plan.groups}
    for target in plan.targets:
        assert reader._panel_identity(target) == {}, target.name


def test_a_mosaic_member_is_still_labelled_a_panel():
    """The other side of the kind check: a `kind: "mosaic"` group's member
    keeps its labels and its ids (the existing mosaic tests hold the rest)."""
    plan = pool_plan("best")
    group = plan.groups[0].model_copy(update={"kind": "mosaic",
                                              "pool_strategy": None})
    target = plan.targets[0].model_copy(update={"panel_row": 0,
                                                "panel_col": 1})
    reader = SequenceEngine.__new__(SequenceEngine)
    reader._groups = {GID: group}
    step = SimpleNamespace(frame_type="light")
    assert reader._panel_labels(target, step)["panel"] == "1-2"
    assert reader._panel_identity(target) == {"mosaic_id": GID,
                                              "panel_id": target.id}
