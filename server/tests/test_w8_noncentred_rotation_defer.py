# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""(a) #160: a non-centred slew that was asked to rotate used to say so in
the log and nothing else. `_setup_target`'s non-centred branch
(server/astrodeck/sequence/engine.py) never calls `goto_and_center` -- the
one path that turns the rotator -- so `hop_centring` stayed `None` for a
panel shot this way, and `_group_hop_checks` only runs when
`hop_centring is not None`. A rotating mosaic panel set `center=False`
could therefore never be deferred for the angle it never reached: it shot
blind, every visit, at whatever angle the camera happened to be at.

Fixed by setting `hop_centring = {"rotation_skipped": True}` on that branch
(exactly the flag a connected rotator that tried and failed through
`goto_and_center` would carry), so the existing group-angle machinery
(`_group_hop_checks`, `_group_angle_check`, `_rotator_evidence`) reads it
and defers the panel the same way.

Runs the REAL `_setup_target`/`_run_steps` on the clocked simulator hub
through `_group_harness.Night`, exactly as test_group_rotation.py does.
"""
from __future__ import annotations

import pytest

from _group_harness import GROUP_NAME, grid_plan, group_hub, group_store  # noqa: F401


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _noncentred(plan, label: str):
    """``plan`` with the member named ``label`` forced to ``center=False``;
    every other target untouched."""
    name = _name(label)
    return plan.model_copy(update={"targets": [
        t.model_copy(update={"center": False}) if t.name == name else t
        for t in plan.targets]})


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0):
    from _group_harness import Night
    from astrodeck.sequence.session import session_store

    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


async def test_a_noncentred_panel_in_a_rotating_mosaic_is_deferred_not_shot_blind(
        group_hub, monkeypatch):
    """Panel 1-2 of a rotating 2x2 mosaic is permanently non-centred (never
    plate-solved, never rotated), while its three siblings centre and rotate
    normally. 1-2 must never bank a frame: every one of its hops carries the
    rotation-skipped flag and no fresh angle evidence, so `_group_hop_checks`
    defers it every time, it is set aside after three consecutive visits
    (`group_rules.GroupRun`, the same ``max_failed_visits`` rule every other
    counted deferral uses), and the other three panels complete on schedule.

    MUTANT "flag not set" (the ``hop_centring = {"rotation_skipped": True}``
    line in `_setup_target`'s non-centred branch replaced with ``pass``, run
    from a byte-for-byte backup of engine.py, restored and compared
    byte-identical afterwards): RED here, 1-2 shoots exactly like its
    siblings and is never deferred (observed):

        >       assert _shot(night, "1-2") == [], night.shots()
        E       AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31
        1-2', 'L'), ('M31 1-2', 'R'), ('M31 2-2', 'L'), ('M31 2-2', 'R'), ...]
        E       assert ['L', 'R', 'L', 'R', 'L', 'R'] == []
    """
    plan = grid_plan(group_kw={"rotate": True, "pa_deg": 30.0})
    plan = _noncentred(plan, "1-2")

    night = await _night(group_hub, monkeypatch, plan)

    assert night.done, night.trace[-3:]
    assert _shot(night, "1-2") == [], night.shots()
    for label in ("1-1", "2-2", "2-1"):
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.shots())
    said = night.said("the rotator did not turn the camera to the mosaic's "
                      "angle")
    assert said, night.lines[-10:]
    assert any(r["target_id"] == "p01" for r in night.stored.set_aside), (
        night.stored.set_aside)


async def test_control_a_noncentred_panel_in_a_non_rotating_mosaic_shoots_as_before(
        group_hub, monkeypatch):
    """Control: the SAME permanently non-centred panel, but the group does
    not rotate (``group.rotate`` defaults to False). `_group_hop_checks`'s
    rotation checks are gated on ``group.rotate and target.rotation_deg is
    not None``, so setting ``hop_centring`` here must change nothing: the
    panel still shoots at whatever angle the camera sits, exactly as every
    non-centred target has always worked. Without this control the fix
    could over-defer a plain non-centred panel that was never asking for a
    rotation in the first place."""
    plan = grid_plan()
    plan = _noncentred(plan, "1-2")

    night = await _night(group_hub, monkeypatch, plan)

    assert night.done, night.trace[-3:]
    for label in ("1-1", "1-2", "2-2", "2-1"):
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.shots())
    assert night.stored.set_aside == [], night.stored.set_aside
