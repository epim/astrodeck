# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Ordered phases of a TargetGroup (WP 5.1a, #1074 engine half).

THE CLAIM. A flow that draws "cycle 1 complete, then cycle 2" on one target
reached the engine as one seven-filter round robin (#1074). A group now
carries ``phases``, an ordered list of member target ids: the group runs only
the members of its current phase and the next phase starts when every member
of the current one is complete. The current phase is derived from the ledger
every time it is asked, never stored, so a resume lands in the right one; a
member set aside tonight holds its phase for tonight.

Every night here runs the REAL scheduler on the clocked simulator
(tests/_group_harness.py) at the fixed instant ``T0``, so none depends on the
wall clock, the hour or the PC's time zone. The group is two members on one
object's coordinates: "Cycle 1" shoots L R G B, "Cycle 2" shoots S Ha O3.

MUTANT "merge phases" (``group_rules.current_phase_ids`` returns None, every
phase treated as one), red on the first case below:
    AssertionError: a phase-2 frame was shot before phase 1 was complete:
    ['L', 'R', 'G', 'B', 'S', 'Ha', 'O3', 'L', 'R', 'G', 'B', 'S', 'Ha', 'O3']
"""
from __future__ import annotations

import hashlib
import json

import pytest

from _group_harness import (T0, Night, group_hub, group_store,  # noqa: F401
                            grid_plan, golden_flow_plan, plain_mosaic_plan,
                            ra_at)
from astrodeck.events import night_key
from astrodeck.sequence.group_rules import (GroupRun, current_phase_ids,
                                            current_phase_index)
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup,
                                       plan_identity_errors)
import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence.resume_arm import recentre_candidates
from astrodeck.sequence.session import Session, SessionFrame, session_store

GID = "cycles"
PHASE_1 = ("L", "R", "G", "B")
PHASE_2 = ("S", "Ha", "O3")


def _member(tid: str, name: str, filters, count: int) -> Target:
    return Target(id=tid, name=name, ra_hours=ra_at(-2.0), dec_deg=40.0,
                  center=True, autofocus_first=False, acquisition="cycle",
                  mosaic_group=GID,
                  steps=[ExposureStep(id=f"{tid}-{f}", filter=f,
                                      exposure_s=30.0, count=count)
                         for f in filters])


def phase_plan(*, count: int = 2, phases=(("c1",), ("c2",)),
               **group_kw) -> SequencePlan:
    """Two members of one group on one object: phase 1 is "Cycle 1" (L R G B),
    phase 2 is "Cycle 2" (S Ha O3), both ``count`` frames a filter."""
    group = TargetGroup(id=GID, name="Cycles",
                        phases=[list(p) for p in phases], **group_kw)
    return SequencePlan(
        name="two cycles", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False,
        warm_cooler_when_done=False, recover_guiding=False,
        targets=[_member("c1", "Cycle 1", PHASE_1, count),
                 _member("c2", "Cycle 2", PHASE_2, count)],
        groups=[group])


async def _night(hub, monkeypatch, plan, *, session=None) -> Night:
    night = Night(hub, monkeypatch)
    start_kw = {"session": session} if session is not None else {}
    try:
        night.done = await night.run(plan, wall_s=60.0, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _filters(night) -> list[str]:
    return [f for _t, f in night.shots()]


def _seeded(plan, frames: list[tuple[str, str]]) -> Session:
    """A dormant session of ``plan`` holding ``frames`` as ``(target id, step
    id)``, every one accepted."""
    s = Session(name=plan.name, created_ts=T0 - 86400.0, status="dormant",
                plan=plan)
    for tid, sid in frames:
        s.frames.append(SessionFrame(ts=T0 - 3600.0, night="n0",
                                     target_id=tid, step_id=sid,
                                     auto_accepted=True))
    return s


# -------------------------------------------------------------- the engine

async def test_phase_two_shoots_no_frame_before_phase_one_is_complete(
        group_hub, monkeypatch):
    """Phase 1 is L R G B and phase 2 is S Ha O3, two frames a filter, on the
    same coordinates: every phase-1 frame comes before the first phase-2
    frame, the frame order is exactly that, and the night completes."""
    night = await _night(group_hub, monkeypatch, phase_plan())
    assert night.done, night.trace[-3:]
    assert _filters(night) == [*PHASE_1] * 2 + [*PHASE_2] * 2, (
        f"a phase-2 frame was shot before phase 1 was complete: "
        f"{_filters(night)}")
    assert night.stored.owed() == 0 and night.stored.status == "complete"
    assert night.said("phase 1 is complete; phase 2 of 2 starts"), night.lines


async def test_a_member_set_aside_tonight_holds_its_phase_for_tonight(
        group_hub, monkeypatch):
    """Cycle 1 sinks below its own floor (on_floor advance) at its first
    frame and is set aside for tonight. Cycle 2 does not start early: the
    phase before it cannot finish tonight, so it is set aside too, with the
    reason, kind "phase", and the night ends with no phase-2 frame instead of
    idling to dawn. Both stay owed to the next night."""
    plan = phase_plan()
    plan.targets[0].schedule = Schedule(min_altitude_deg=30.0,
                                        on_floor="advance")
    real = engine_mod._frame_altitude
    monkeypatch.setattr(
        engine_mod, "_frame_altitude",
        lambda target, site, when: (10.0 if target.id == "c1"
                                    else real(target, site, when)))
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _filters(night) == [], night.shots()
    records = {r["target_id"]: r for r in night.stored.set_aside}
    assert set(records) == {"c1", "c2"}, night.stored.set_aside
    assert records["c1"]["kind"] == "floor", records
    assert records["c2"]["kind"] == "phase", records
    assert records["c2"]["night"] == night_key(T0), records
    assert "phase 1" in records["c2"]["reason"], records
    assert night.stored.owed() > 0, "both stay owed to the next night"
    held = [(lvl, m) for _t, lvl, m in night.lines
            if "phase 1 cannot finish tonight" in m]
    assert len(held) == 1 and held[0][0] == "warning", held
    assert "Cycles:" in held[0][1] and "(Cycle 1 set aside" in held[0][1], held
    assert "(Cycle 2)" in held[0][1], held
    assert not night.said("phase 1 of Cycles cannot"), (
        "the per-member warning is gone: one line per group")


async def test_a_resume_in_phase_two_continues_phase_two(
        group_hub, monkeypatch):
    """The ledger holds all of phase 1: the run starts in phase 2 and shoots
    nothing of phase 1. The phase is the ledger's, never a stored cursor."""
    plan = phase_plan()
    done = [("c1", f"c1-{f}") for f in PHASE_1 for _ in range(2)]
    night = await _night(group_hub, monkeypatch, plan,
                         session=_seeded(plan, done))
    assert night.done, night.trace[-3:]
    assert _filters(night) == [*PHASE_2] * 2, _filters(night)
    assert not [t for t, _f in night.shots() if t == "Cycle 1"]


async def test_a_resume_with_phase_one_incomplete_does_not_start_phase_two(
        group_hub, monkeypatch):
    """One L of phase 1 is banked and nothing else: phase 1 still owes seven
    of its eight frames, and phase 2 starts only after the last of them."""
    plan = phase_plan()
    night = await _night(group_hub, monkeypatch, plan,
                         session=_seeded(plan, [("c1", "c1-L")]))
    assert night.done, night.trace[-3:]
    shots = night.shots()
    last_c1 = max(i for i, (t, _f) in enumerate(shots) if t == "Cycle 1")
    first_c2 = min(i for i, (t, _f) in enumerate(shots) if t == "Cycle 2")
    assert last_c1 < first_c2, shots
    assert len([1 for t, _f in shots if t == "Cycle 1"]) == 7, shots
    assert night.stored.owed() == 0


async def test_a_member_that_completed_after_being_set_aside_reads_as_complete(
        group_hub, monkeypatch):
    """A same-night restart after Cycle 1's centring set-aside expired and it
    then shot everything: its record still names it, and the ledger says it
    is complete. Complete wins: phase 2 starts at once, nothing is said about
    Cycle 1 being set aside, and the group's run counts it complete.

    MUTANT "set aside is checked first" (the order in `_start_groups` as it
    was): Cycle 1 is read as set aside, holds phase 1, and phase 2 never
    starts without the 45 minute expiry."""
    plan = phase_plan()
    done = [("c1", f"c1-{f}") for f in PHASE_1 for _ in range(2)]
    session = _seeded(plan, done)
    session.note_set_aside("c1", "centring failed on every try",
                           night=night_key(T0), kind="centring", ts=T0 - 3000.0)
    seen: dict = {}
    real = engine_mod.SequenceEngine._drop_complete

    def spy(self, remaining, index_of):
        seen.setdefault("completed", set(self._group_runs[GID].completed))
        return real(self, remaining, index_of)

    monkeypatch.setattr(engine_mod.SequenceEngine, "_drop_complete", spy)
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert seen["completed"] == {"c1"}, seen
    assert _filters(night) == [*PHASE_2] * 2, _filters(night)
    assert not night.said("Cycle 1: set aside earlier tonight"), night.lines
    assert night.rel(night.gotos[0][0]) < 60.0, night.gotos


async def test_no_phases_is_one_phase_of_every_member(group_hub, monkeypatch):
    """The control: the same two members with ``phases == []`` are the group
    as it always was, visited in turn, so the cycles interleave."""
    night = await _night(group_hub, monkeypatch, phase_plan(phases=()))
    assert night.done, night.trace[-3:]
    assert _filters(night) == [*PHASE_1, *PHASE_2] * 2, _filters(night)


# ---------------------------------------------------- ResumeArm agreement

def test_resume_arm_recentres_on_the_current_phase_only():
    """ResumeArm picks the member the engine would select: while phase 1
    owes frames only its member is a candidate, once it is complete only
    phase 2's, and with phase 1 set aside tonight and unfinished, none of
    the group is (the engine sets phase 2 aside as well)."""
    plan = phase_plan()
    night = night_key(T0)
    fresh = _seeded(plan, [])
    assert [t.id for t in recentre_candidates(fresh, night)] == ["c1"]
    done = [("c1", f"c1-{f}") for f in PHASE_1 for _ in range(2)]
    assert [t.id for t in recentre_candidates(_seeded(plan, done), night)] \
        == ["c2"]
    aside = _seeded(plan, [])
    aside.note_set_aside("c1", "floor", night=night, kind="floor", ts=T0)
    assert recentre_candidates(aside, night) == []


# ------------------------------------------------------------- pure rules

def test_the_current_phase_is_the_first_that_owes_a_member():
    phases = [["a", "b"], ["c"], ["d"]]
    assert current_phase_index(phases, set()) == 0
    assert current_phase_index(phases, {"a"}) == 0
    assert current_phase_index(phases, {"a", "b"}) == 1
    assert current_phase_index(phases, {"a", "b", "c"}) == 2
    assert current_phase_index(phases, {"a", "b", "c", "d"}) is None
    assert current_phase_index([], {"a"}) is None


def test_a_later_member_complete_early_does_not_advance_the_phase():
    assert current_phase_index([["a"], ["b"]], {"b"}) == 0


def test_current_phase_ids_says_none_without_phases_and_empty_when_done():
    assert current_phase_ids([], set()) is None
    assert current_phase_ids([["a"], ["b"]], set()) == frozenset({"a"})
    assert current_phase_ids([["a"], ["b"]], {"a"}) == frozenset({"b"})
    assert current_phase_ids([["a"], ["b"]], {"a", "b"}) == frozenset()


def test_a_group_run_holds_its_phase_while_a_member_is_set_aside():
    run = GroupRun({"a": "A", "b": "B", "c": "C"}, max_failed_visits=3,
                   phases=[["a", "b"], ["c"]])
    assert [run.in_phase(p) for p in "abc"] == [True, True, False]
    run.completed.add("a")
    run.set_aside_panel("b", "floor", kind="floor")
    assert [run.in_phase(p) for p in "abc"] == [True, True, False]
    assert run.phase_index() == 0
    run.completed.add("b")
    assert [run.in_phase(p) for p in "abc"] == [False, False, True]


def test_a_group_run_without_phases_lets_every_member_run():
    run = GroupRun({"a": "A", "b": "B"}, max_failed_visits=3)
    assert run.phase_index() is None
    assert run.in_phase("a") and run.in_phase("b")


# ------------------------------------------------------ identity refusals

def test_a_well_formed_phased_plan_is_accepted():
    assert plan_identity_errors(phase_plan()) == []


def test_a_phase_naming_a_non_member_is_refused():
    errors = plan_identity_errors(phase_plan(phases=(("c1",), ("c2", "ghost"))))
    assert any("'ghost'" in e and "no member" in e for e in errors), errors


def test_a_member_in_two_phases_is_refused():
    errors = plan_identity_errors(
        phase_plan(phases=(("c1", "c2"), ("c2",))))
    assert any("'c2'" in e and "2 times" in e for e in errors), errors


def test_a_member_in_no_phase_is_refused():
    errors = plan_identity_errors(phase_plan(phases=(("c1",),)))
    assert any("'c2'" in e and "no phase" in e for e in errors), errors


def test_a_pool_with_phases_is_refused():
    errors = plan_identity_errors(phase_plan(kind="pool"))
    assert any("pool" in e and "phases" in e for e in errors), errors


def test_a_pool_strategy_on_a_mosaic_is_refused():
    errors = plan_identity_errors(
        phase_plan(phases=(), pool_strategy="priority"))
    assert any("mosaic" in e and "'priority'" in e for e in errors), errors


# ------------------------------------------- no stored plan moves (rule 14)

def _digest(dump: dict) -> str:
    return hashlib.sha256(json.dumps(
        dump, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plain_plan() -> SequencePlan:
    plan = plain_mosaic_plan()
    for i, t in enumerate(plan.targets):
        t.id = f"plain-{i}"
        for j, s in enumerate(t.steps):
            s.id = f"plain-{i}-{j}"
    return plan


#: Taken on the tree before this work package, with the dumps below.
BASE_DIGESTS = {
    "mosaic plan with a group":
        "e6ed997c28bee6ee9d3f61576ffd7e7ec4be52badd7803b66f3328b47f3753bf",
    "mosaic plan, sequential":
        "a40a361660b4d3e158b0942d1c354768ef0c8ba36458c2566c15a4f4c1d9b10b",
    "golden flow plan":
        "645d93d229504e7f8381fe1e37a3760af137f398bf0d48f64a87a5ac280b6ab6",
    "plan with no groups":
        "99025cda15dae769ef68a9afc331a0b94dc4ed7e28dffa46d6edbf877372bfda",
    "a bare group":
        "bf40de0657497c3f749ca9af4d5e5938d68d13a5c338bb87527ac8692e5e6f9a",
    "a group with options set":
        "6a1d8cc7353aa7ea29ac08415e99a747d8096e2a1677cb1de7bab6127e354069",
}


def test_plans_without_the_new_fields_dump_byte_for_byte_as_before():
    dumps = {
        "mosaic plan with a group": grid_plan(),
        "mosaic plan, sequential": grid_plan(group_kw={"mode": "sequential"}),
        "golden flow plan": golden_flow_plan(),
        "plan with no groups": _plain_plan(),
    }
    got = {name: _digest(plan.model_dump(mode="json"))
           for name, plan in dumps.items()}
    got["a bare group"] = _digest(
        TargetGroup(id="g", name="G").model_dump(mode="json"))
    got["a group with options set"] = _digest(TargetGroup(
        id="g", name="G", mode="sequential", order="grid"
    ).model_dump(mode="json"))
    assert got == BASE_DIGESTS


def test_the_new_fields_appear_in_the_dump_only_when_set():
    bare = TargetGroup(id="g").model_dump(mode="json")
    assert "phases" not in bare and "pool_strategy" not in bare
    full = TargetGroup(id="g", phases=[["a"]], kind="pool",
                       pool_strategy="priority").model_dump(mode="json")
    assert full["phases"] == [["a"]] and full["pool_strategy"] == "priority"
    assert TargetGroup.model_validate(full) == TargetGroup(
        id="g", phases=[["a"]], kind="pool", pool_strategy="priority")
