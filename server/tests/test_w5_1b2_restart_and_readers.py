# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What reads a chain's phases besides the cycle driver, and a mosaic restarted
after a panel finished (WP 5.1b2, #1074; the re-homed WP 5.1a case).

* ``ResumeArm`` must agree with the engine about which targets owe frames
  tonight: a chain whose first phase is set aside is held, not owed.
* ``_remaining_hops`` prices a chained panel's visits by every phase, since
  the phases are shot one after the other.
* A member whose ledger is complete reads as complete after a same-night
  restart even when tonight's set-aside records name it (a centring set-aside
  that expired and shot the rest), so the group completes and its
  ``on_mosaic_complete`` fires, once.

Every night runs the REAL scheduler on the clocked simulator at the fixed
instant ``T0`` (tests/_group_harness.py).

MUTANT "set aside is checked first" (`_start_groups` reads a member in the
night's set-aside records as set aside before it asks the ledger), red on the
restart case:
    AssertionError: ['the mosaic is complete'] expected, got []
MUTANT "the price takes the longest phase" (`_visits_owed` as it was: the
member's rounds are the longest step's, whatever its phase), red on the hops
case:
    AssertionError: {'M31 1-1': 11, 'M31 1-2': 11, ...}  assert 11 == 23
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store, ra_at)
from astrodeck.events import night_key
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import (ExposureStep, Instruction, SequencePlan,
                                       Target)
from astrodeck.sequence.resume_arm import recentre_candidates
from astrodeck.sequence.session import Session, SessionFrame, session_store

MOSAIC_DONE = "the mosaic is complete"


def _chain_target(tid: str = "t1") -> Target:
    """One target whose lane is a cycle of L R (two frames each) and then a
    cycle of S Ha, phases 0 and 1."""
    steps = [ExposureStep(id=f"{tid}-{f}", filter=f, exposure_s=30.0, count=2,
                          phase=p)
             for f, p in (("L", 0), ("R", 0), ("S", 1), ("Ha", 1))]
    return Target(id=tid, name="Chain", ra_hours=ra_at(-2.0), dec_deg=40.0,
                  center=True, autofocus_first=False, acquisition="cycle",
                  steps=steps)


def _session(plan: SequencePlan) -> Session:
    return Session(name=plan.name, created_ts=T0 - 86400.0, status="dormant",
                   plan=plan)


# -------------------------------------------------------------- ResumeArm

def _chain_plan() -> SequencePlan:
    return SequencePlan(name="chain", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=[_chain_target()])


def test_resume_arm_holds_a_chain_whose_first_phase_is_set_aside():
    """The engine shoots nothing of a chain while its first phase's owed
    steps are all set aside, so ResumeArm re-centres on nothing for it. With
    one of them still shootable, the chain is a candidate."""
    night = night_key(T0)
    plan = _chain_plan()
    whole = _session(plan)
    for sid in ("t1-L", "t1-R"):
        whole.note_set_aside("t1", "rejects", night=night, step_id=sid)
    assert recentre_candidates(whole, night) == []
    partly = _session(plan)
    partly.note_set_aside("t1", "rejects", night=night, step_id="t1-L")
    assert [t.id for t in recentre_candidates(partly, night)] == ["t1"]


def test_resume_arm_reads_the_later_phase_once_the_first_is_banked():
    """Phase 1 banked, phase 2's steps set aside: nothing shootable. Phase 1
    banked and phase 2 shootable: a candidate."""
    night = night_key(T0)
    plan = _chain_plan()

    def banked() -> Session:
        s = _session(plan)
        for sid in ("t1-L", "t1-R"):
            for _ in range(2):
                s.frames.append(SessionFrame(
                    ts=T0 - 3600.0, night="n0", target_id="t1", step_id=sid,
                    auto_accepted=True))
        return s

    aside = banked()
    for sid in ("t1-S", "t1-Ha"):
        aside.note_set_aside("t1", "rejects", night=night, step_id=sid)
    assert recentre_candidates(aside, night) == []
    assert [t.id for t in recentre_candidates(banked(), night)] == ["t1"]


# ------------------------------------------------------------------ the hops

async def test_a_chained_panels_hops_are_priced_by_every_phase(
        group_hub, monkeypatch):
    """A 2 x 2 mosaic, each panel a cycle of L R then a cycle of S Ha, three
    cycles each, one pass a visit: a panel owes three rounds of phase 1 and
    three of phase 2, six visits, and the whole mosaic 24 hops. Priced by the
    longest step alone it was 12."""
    plan = grid_plan(panel_kw={"filters": ("L", "R", "S", "Ha")})
    for t in plan.targets:
        for s in t.steps:
            s.phase = 1 if s.filter in ("S", "Ha") else 0
    seen: dict[str, int] = {}
    box: dict = {}

    def hops(rec):
        seen.setdefault(rec["target"], box["engine"]._remaining_hops())

    night = Night(group_hub, monkeypatch)
    box["engine"] = night.engine
    night.on_capture = hops
    try:
        done = await night.run(plan)
    finally:
        await night.close()
    assert done, night.trace[-3:]
    assert seen[f"{GROUP_NAME} 1-1"] == 23, seen


def test_the_current_phase_is_the_lowest_one_that_owes_frames():
    steps = [ExposureStep(filter=f, exposure_s=1, count=1, phase=p)
             for f, p in (("L", 0), ("R", 0), ("S", 1), ("Ha", 2))]
    owed = list(enumerate(steps))
    current, later = SequenceEngine._current_phase(owed)
    assert [s.filter for _i, s in current] == ["L", "R"]
    assert [s.filter for _i, s in later] == ["S", "Ha"]
    current, later = SequenceEngine._current_phase(owed[2:])
    assert [s.filter for _i, s in current] == ["S"]
    assert [s.filter for _i, s in later] == ["Ha"]
    assert SequenceEngine._current_phase([]) == ([], [])


# ------------------------------------------- a restart after a panel finished

def _notify() -> list[Instruction]:
    return [Instruction(id="per-mosaic", trigger="on_mosaic_complete",
                        action="notify", message=MOSAIC_DONE, level="info")]


async def test_a_member_that_finished_after_being_set_aside_completes_the_group(
        group_hub, monkeypatch):
    """A same-night restart of a 1 x 2 mosaic: panel 1-1's centring set-aside
    expired and it then shot everything, so the ledger holds all of it while
    tonight's record still names it. The ledger wins: 1-1 reads complete, 1-2
    is shot, the group completes and its rule fires once.

    MUTANT "set aside is checked first": 1-1 reads set aside, the group is
    never complete tonight, and the rule never fires."""
    plan = grid_plan(rows=1, cols=2, instructions=_notify())
    session = _session(plan)
    first = plan.targets[0]
    for step in first.steps:
        for _ in range(step.count):
            session.frames.append(SessionFrame(
                ts=T0 - 3600.0, night="n0", target_id=first.id,
                step_id=step.id, auto_accepted=True))
    session.note_set_aside(first.id, "centring failed on every try",
                           night=night_key(T0), kind="centring", ts=T0 - 3000.0)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0, session=session)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    assert {t for t, _f in night.shots()} == {f"{GROUP_NAME} 1-2"}, (
        night.shots())
    fired = [m for _t, _lvl, m in night.lines if m == MOSAIC_DONE]
    assert fired == [MOSAIC_DONE], (
        f"{[MOSAIC_DONE]} expected, got {fired}")
    assert not night.said(f"{GROUP_NAME} 1-1: set aside earlier tonight")
    assert session_store.load(night.session_id).status == "complete"
