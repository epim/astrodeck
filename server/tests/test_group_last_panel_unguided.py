# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A group's last live panel, with guiding optional and no guider connected,
is a plain target at its hop (#315, S3 orchestrator ruling 6; spec 5.6 step
7, Revision 2 ruling 5).

The guide-start pass rule blames the rig only when at least two panels were
attempted in a pass and every one failed (`group_rules.guide_start_pass_
verdict`). A group with one live panel can never make two attempts, so with
no guider connected and ``require_guiding`` off its panel deferred on every
hop, waited ``DEFER_WAIT_S`` after each pass of nothing, and was set aside
after ``max_failed_visits`` passes: a quarter of an hour, then the night,
where the same target outside a group warns and shoots unguided. S3 compiles
a TARGET block with no grid to a one-panel group, which makes that the
common case.

Every case runs the real scheduler, hop and frame loop on the clocked
simulator (tests/_group_harness.py). What is scripted is the guider's
presence (``connected = False``, a guider that is there and not up) and the
rig's escalation settings.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim (long lines wrapped). Every mutant was applied in a private scratch
copy of server/ (scratchpad/s3-eb-mut-p8w3), never in the shared tree
(#254).
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, T0, Night, grid_plan, group_hub,
                            group_store)
from astrodeck.config import EscalationConfig
from astrodeck.sequence.group_rules import DEFER_WAIT_S
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import Session, SessionFrame, session_store

#: The plain target's warning, `_setup_target`'s fall-through: what the same
#: target outside a group says before it shoots unguided.
UNGUIDED = "continuing UNGUIDED"
#: The line a deferral wait starts with (`GroupRun.close_pass`).
WAITING = f"waiting {DEFER_WAIT_S:.0f} s before the next pass"


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


async def _night(hub, monkeypatch, plan, *, session=None, wall_s=60.0):
    night = Night(hub, monkeypatch)
    start_kw = {"session": session} if session is not None else {}
    try:
        night.done = await night.run(plan, wall_s=wall_s, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _optional_guiding(store) -> None:
    """The rig's escalation as it ships: guiding optional, warn."""
    store.set_escalation(EscalationConfig(require_guiding=False,
                                          guiding_action="warn"))


def _with_skipped(plan: SequencePlan, skipped: set[str]) -> SequencePlan:
    """``plan`` as the compile makes it when the operator skipped the panels
    ``skipped``: they are not members, and the group names them in
    ``skipped_ids`` (models.TargetGroup)."""
    raw = plan.model_dump()
    raw["targets"] = [t for t in raw["targets"] if t["id"] not in skipped]
    raw["groups"][0]["skipped_ids"] = sorted(skipped)
    return SequencePlan.model_validate(raw)


async def test_a_2x1_with_one_panel_skipped_and_no_guider_shoots_unguided(
        group_hub, group_store, monkeypatch):
    """A 2x1 whose panel 1-2 the operator skipped leaves a group of one:
    1-1. The plan asks for guiding, no guider is connected, and the rig marks
    guiding optional. 1-1 is shot unguided on its first visit, after the
    plain target's warning, exactly as the same target outside a group would
    be: no deferral, no ``DEFER_WAIT_S`` wait, nothing set aside, and the run
    completes.

    MUTANT "defer the last panel too" (`group_rules.no_guider_defers`
    returning True, S2's rule): 1-1 defers on every hop and is set aside
    after 3 passes. RED (observed):
        AssertionError: [('M31 1-1', ()), ('M31 1-1', ()), ('M31 1-1', ())]
        assert [('M31 1-1', ())] == [('M31 1-1', ('L', 'R'))]
          At index 0 diff: ('M31 1-1', ()) != ('M31 1-1', ('L', 'R'))
    """
    _optional_guiding(group_store)
    group_hub.guider.connected = False
    plan = _with_skipped(grid_plan(rows=1, cols=2, guide=True), {"p01"})
    assert [t.id for t in plan.targets] == ["p00"]
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert night.visits()[:1] == [(_name("1-1"), ("L", "R"))], night.visits()
    assert night.engine.state.get("end_reason") == "complete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.shots() == [(_name("1-1"), f) for f in ("L", "R") * 3]
    assert night.said(UNGUIDED), night.lines[-8:]
    assert not night.said("guiding did not start"), night.said("guiding")
    assert not night.said(WAITING), night.said("waiting")
    assert night.stored.set_aside == []


async def test_the_last_live_panel_shoots_unguided_though_its_group_has_two(
        group_hub, group_store, monkeypatch):
    """A 2x1 resumed with 1-2 already complete in the ledger: the group has
    two members and one LIVE one, 1-1. What counts is the live panels, not
    the members, because a member that is complete makes no attempt either:
    1-1 is shot unguided on its first visit, as in the case above.

    MUTANT "count the members, not the live ones" (`_live_panels` returning
    ``len(run.members)``): 1-1 defers as though 1-2 could still make a
    second attempt, and is set aside after 3 passes. RED (observed; "defer
    the last panel too" fails it with the same lines):
        AssertionError: [('M31 1-1', ()), ('M31 1-1', ()), ('M31 1-1', ())]
        assert [('M31 1-1', ())] == [('M31 1-1', ('L', 'R'))]
          At index 0 diff: ('M31 1-1', ()) != ('M31 1-1', ('L', 'R'))
    """
    _optional_guiding(group_store)
    group_hub.guider.connected = False
    plan = grid_plan(rows=1, cols=2, guide=True)
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    for f in ("L", "R"):
        for _ in range(3):
            session.frames.append(SessionFrame(
                ts=T0 - 3600.0, night="n0", target_id="p01",
                step_id=f"p01-{f}", auto_accepted=True))
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert night.visits()[:1] == [(_name("1-1"), ("L", "R"))], night.visits()
    assert _name("1-2") not in {who for who, _f in night.visits()}
    assert night.engine.state.get("end_reason") == "complete"
    assert night.said(UNGUIDED), night.lines[-8:]
    assert not night.said("guiding did not start"), night.said("guiding")
    assert night.stored.set_aside == []


async def test_control_with_guiding_required_the_last_panel_still_defers(
        group_hub, group_store, monkeypatch):
    """Control: the ruling is for guiding the rig marks OPTIONAL. With
    ``require_guiding`` on (and ``guiding_action`` abort), the lone panel of
    the 2x1 above defers as S2 built it, is set aside after three passes,
    and the run ends incomplete: the plain-target rule would have ended the
    night at the first hop.

    MUTANT "required guiding lets the last panel through too"
    (`group_rules.no_guider_defers` returning ``live >= 2``): the lone panel
    takes the plain target's escalation, abort. RED (observed):
        AssertionError: ('unsafe', 'guiding required but the guider
        (scripted guider) is not connected')
        assert 'unsafe' == 'incomplete'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    group_hub.guider.connected = False
    plan = _with_skipped(grid_plan(rows=1, cols=2, guide=True), {"p01"})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "incomplete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.visits() == [(_name("1-1"), ())] * 3, night.visits()
    assert night.shots() == []
    assert [r["target_id"] for r in night.stored.set_aside] == ["p00"]
    assert len(night.said(WAITING)) == 2, night.said("waiting")


async def test_control_a_connected_guider_that_fails_on_the_last_panel_still_defers(
        group_hub, group_store, monkeypatch):
    """Control: the ruling is for NO GUIDER CONNECTED. A guider that is
    connected and fails to start on the lone panel of the 2x1 above, with
    guiding optional, still defers as S2 built it: that failure can be the
    panel's own guide star (`group_rules.no_guider_defers`), so the panel is
    retried, set aside after three passes, and never shot unguided. (Whether
    this half of #315 should shoot unguided too is the owner's to rule; until
    then this pins the scope ruling 6 gave.)

    MUTANT "the last-panel rule on a failed start too" (the
    `no_guider_defers` fall-through copied into `_setup_target`'s
    failed-start branch). Every other group test stays green under it; this
    one goes RED: the lone panel is shot unguided and the run completes
    (observed; recorded by the S3-EB verifier in a private scratch copy of
    server/, scratchpad/s3-eb-verify-mut):
        AssertionError: ('complete', 'all targets complete')
        assert 'complete' == 'incomplete'
    """
    _optional_guiding(group_store)
    plan = _with_skipped(grid_plan(rows=1, cols=2, guide=True), {"p01"})
    night = Night(group_hub, monkeypatch, guide=lambda who, n: False)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "incomplete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.visits() == [(_name("1-1"), ())] * 3, night.visits()
    assert night.shots() == []
    assert [r["target_id"] for r in night.stored.set_aside] == ["p00"]
    assert not night.said(UNGUIDED) and not night.said("continuing unguided")


async def test_control_a_2x2_with_no_guider_still_defers_to_the_rig_verdict(
        group_hub, group_store, monkeypatch):
    """Control: with four live panels S2's rule is unchanged. Guiding
    optional, no guider connected: each panel of the first pass is deferred
    and shoots nothing, four attempted and four failed is the rig's fault,
    and ``guiding_action`` (warn) sends the panels on unguided from the
    second pass. The last-panel rule must not turn every panel into a plain
    target.

    MUTANT "every panel is a plain target" (`group_rules.no_guider_defers`
    returning ``bool(require_guiding)``): the first pass is shot unguided,
    panel by panel, and no pass ever reaches the rig verdict. RED
    (observed):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')),
        ('M31 2-2', ('L', 'R')), ('M31 2-1', ('L', 'R')), ('M31 1-1',
        ('L', 'R')), ('M31 1-2', ('L', 'R')), ...]
        assert [('M31 1-1', ..., ('L', 'R'))] == [('M31 1-1', ...M31 2-1',
        ())]
          At index 0 diff: ('M31 1-1', ('L', 'R')) != ('M31 1-1', ())
    Since the #315 follow-up the rule counts the members this pass can
    visit (`GroupRun.visitable`). MUTANT "nothing let through is kept"
    (`GroupRun.note_let_through` made a no-op, so a pass counts only the
    panels it visited and the one asking): the pass's first panel, counting
    only itself, is shot unguided before the other three defer. RED
    (observed, S4-ENGC, scratchpad/s4-engc-mut, and again on 2026-09-27 in
    scratchpad/s4-engc-resume-mut):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ()), ('M31
        2-2', ()), ('M31 2-1', ()), ('M31 1-2', ('L', 'R')), ('M31 2-2',
        ('L', 'R')), ...]
        assert [('M31 1-1', ...M31 2-1', ())] == [('M31 1-1', ...M31 2-1',
        ())]
          At index 0 diff: ('M31 1-1', ('L', 'R')) != ('M31 1-1', ())
    """
    _optional_guiding(group_store)
    group_hub.guider.connected = False
    night = await _night(group_hub, monkeypatch, grid_plan(guide=True))
    assert night.done, night.trace[-3:]
    snake = ["1-1", "1-2", "2-2", "2-1"]
    assert night.visits()[:4] == [(_name(lb), ()) for lb in snake], (
        night.visits())
    assert night.said("guiding did not start on any of the 4 panels tried "
                      "this pass"), night.lines[-8:]
    assert night.visits()[4:8] == [(_name(lb), ("L", "R")) for lb in snake]
    assert night.stored.owed() == 0 and night.stored.set_aside == []
