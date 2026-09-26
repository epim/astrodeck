"""A guiding loss the #72 recovery gives up on, mid-visit, defers a mosaic
panel as ``guide_lost`` (#303, S3 orchestrator ruling 6; spec 5.6 step 7).

Before, `_maybe_recover_guiding` took the rig's ``guiding_action`` at its
bound for a panel exactly as for a single target: abort ended the night over
one panel's guide star, skip dropped the panel, and warn stood recovery down
and shot the panel on unguided, where #142 lets those frames through the RMS
gate. The owner's ruling 5 says not to leave that hole ("try again after the
next go around through the other panels"), and the next hop restarts guiding
anyway. So the panel now defers, whatever the action says, and is set aside
after ``max_failed_visits`` consecutive passes. A single target keeps the
escalation.

THE SKY, AS SCRIPTED. Panel 1-2's hop starts its guider, and the star is
lost during every exposure of 1-2 (the harness's ``on_capture`` hook marks
the guider inactive before the shutter closes). A frame shot while the star
is lost is trailed, and the real grader rejects it: each frame reports the
plan's ``min_stars`` worth of stars only while the guider is guiding. Each
recovery "succeeds" (the guider starts again) and the star is lost again in
the next exposure, the shape recovery took on 2026-09-20 (#72).

WHY ACCEPTED MODE. The #72 bound counts recovery attempts since the last
BANKED frame, and `_record_frame` clears it: an accepted frame always, and a
rejected one too in attempts mode, where a reject is recorded unless it is
retaken. So the bound is reached only when frames are not recorded between
the attempts, which is accepted mode rejecting trailed frames, as here, or a
retake. In attempts mode every frame clears the count and the bound is never
reached however long the star stays lost (probed on this harness: twelve
trailed frames, eleven recoveries each at "(1/2)", no deferral and no
stand-down). That is #134, open, and this deferral inherits it: until #134 is
fixed, a panel reaches ``guide_lost`` only in the shape scripted here.

Every case runs the real scheduler, hop, frame loop, recovery and grader on
the clocked simulator (tests/_group_harness.py). Each names the mutant it was
shown RED under, with the failure observed, verbatim (long lines wrapped).
Every mutant was applied in a private scratch copy of server/
(scratchpad/s3-eb-mut-p8w3), never in the shared tree (#254).
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.config import EscalationConfig
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import session_store

LOST = f"{GROUP_NAME} 1-2"
#: What the recovery bound says when it gives up (`_maybe_recover_guiding`).
GAVE_UP = ("guiding could not be kept after 2 recovery attempts without a "
           "frame")
#: The set-aside sentence after three consecutive guide_lost deferrals.
SET_ASIDE = (f"guiding was lost and did not recover on 1-2 on 3 consecutive "
             f"visits: {GAVE_UP}")
#: The warn escalation's stand-down, which shoots on unguided.
STOOD_DOWN = "standing down from recovery and continuing unguided"
#: The rig's escalation per ``guiding_action``: abort and skip only act when
#: guiding is required; warn is what the rig does when it is optional.
ESCALATION = {"abort": EscalationConfig(require_guiding=True,
                                        guiding_action="abort"),
              "skip": EscalationConfig(require_guiding=True,
                                       guiding_action="skip"),
              "warn": EscalationConfig(require_guiding=False,
                                       guiding_action="warn")}


def _plan_kw() -> dict:
    """Guiding with recovery on, accepted mode with a star floor the real
    grader enforces, and a step reject guard wide enough never to decide
    these cases (the night guard off)."""
    return dict(guide=True, recover_guiding=True, count_mode="accepted",
                min_stars=5, max_consecutive_rejects=20,
                max_consecutive_rejects_night=0)


def _lost_plan() -> SequencePlan:
    """A 2x2 of one filter, four frames a panel, all four in one visit: a
    panel that keeps its star completes on its first visit."""
    return grid_plan(panel_kw={"filters": ("L",), "count": 4,
                               "per_visit": 4}, **_plan_kw())


async def _lost_night(hub, monkeypatch, plan, *, lose: set[str]) -> Night:
    """Run ``plan`` with the star lost during every exposure of each target
    named in ``lose``, and a frame graded trailed (no stars) while the star
    is lost."""
    guider = hub.guider

    def on_capture(rec):
        if rec["target"] in lose:
            guider.active = False

    night = Night(hub, monkeypatch,
                  stars=lambda who, f: 50 if guider.active else 0)
    night.on_capture = on_capture
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _starts_and_frames(night: Night, who: str) -> str:
    """``who``'s guider starts ("S") and exposures ("C"), in order: the
    hop's start, then each recovery's, each followed by the frame it was
    made for."""
    out = []
    for entry in night.trace:
        kind = entry[1]
        if kind == "guider" and entry[2] == "start" and entry[3][0] == who:
            out.append("S")
        elif kind == "capture" and entry[2] == who:
            out.append("C")
    return "".join(out)


@pytest.mark.parametrize("action", ["abort", "skip", "warn"])
async def test_a_guiding_loss_defers_the_panel_under_every_guiding_action(
        group_hub, group_store, monkeypatch, action):
    """Panel 1-2 loses its star in every exposure. On each visit the #72
    recovery makes its two attempts, each followed by one frame, and then
    gives up; the panel is deferred as ``guide_lost`` and the next pass
    comes back to it. After three consecutive passes it is set aside for
    tonight with the loss in words. The other three panels complete, and the
    run goes on to its natural end, whatever ``guiding_action`` says: abort
    does not end the night, skip does not drop the panel, and warn does not
    shoot it unguided. No frame of 1-2 is taken without a guider start made
    for it: the hop's start, then each recovery's.

    MUTANT "abort ends the run" (the abort escalation asked before the
    member's deferral in `_maybe_recover_guiding`). RED on abort alone
    (observed):
        AssertionError: ('unsafe', 'guiding could not be kept after 2
        recovery attempts without a frame')
        assert 'unsafe' == 'incomplete'
    MUTANT "no deferral under warn" (the member's deferral only when
    ``require_guiding`` and the action is abort or skip). RED on warn alone
    (observed):
        AssertionError: 1-2 was shot with no guider start of its own after
        recovery gave up: SCSCSCCSCSCSCCSCCCCSCCCCSCCCC
        assert (True and 'CC' not in 'SCSCSCCSCSC...CCSCCCCSCCCC'
    MUTANT "no guide_lost deferral" (the member's deferral deleted: #303's
    "plain StopTarget", which S3's #316 arm now turns into a ``target_stop``
    deferral under skip). RED on each (observed):
        abort:
            AssertionError: ('unsafe', 'guiding could not be kept after 2
            recovery attempts without a frame')
            assert 'unsafe' == 'incomplete'
        skip:
            AssertionError: [{'night': '2026-09-01', 'reason': 'the visit
            stopped on 1-2 on 3 consecutive visits: guiding could not be
            kept after 2 recovery attempts without a frame', 'step_id':
            None, 'target_id': 'p01'}]
        warn:
            AssertionError: 1-2 was shot with no guider start of its own
            after recovery gave up: SCSCSCCSCSCSCCSCCCCSCCCCSCCCC
    MUTANT "the loss is never counted" (`GroupRun.visit_outcome` requeues a
    ``guide_lost`` deferral without counting it): 1-2 is never set aside by
    the rule and hops until its step's reject guard gives up. RED on each
    (observed; the guard's sentence cut where it quotes the engine's dash):
        AssertionError: [{'night': '2026-09-01', 'reason': 'M31 1-2: L set
        aside for tonight after 20 consecutive rejects ...'}, {'night':
        '2026-09-01', 'reason': 'every filter 1-2 still owes is set aside',
        'step_id': None, 'target_id': 'p01'}]
    MUTANT "the budget spans visits" (the reset of ``_guiding_recoveries``
    at the deferral deleted): 1-2's third visit makes no recovery attempt,
    and is deferred on its first loss under a sentence claiming two. RED on
    each (observed):
        AssertionError: 1-2's visits did not each make the hop's start and
        two recovery attempts: SCSCSCSCSCSCSC
        assert 'SCSCSCSCSCSCSC' == 'SCSCSCSCSCSCSCSCSC'
    """
    group_store.set_escalation(ESCALATION[action])
    night = await _lost_night(group_hub, monkeypatch, _lost_plan(),
                              lose={LOST})
    assert night.done, night.trace[-3:]
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    assert ended[0] == "incomplete", ended
    for label in ("1-1", "2-2", "2-1"):
        shot = [f for t, f in night.shots() if t == f"{GROUP_NAME} {label}"]
        assert shot == ["L"] * 4, (label, shot)
    seq = _starts_and_frames(night, LOST)
    assert seq.startswith("S") and "CC" not in seq, (
        f"1-2 was shot with no guider start of its own after recovery gave "
        f"up: {seq}")
    assert not night.said(STOOD_DOWN), night.said(STOOD_DOWN)
    assert [(r["target_id"], r["step_id"], r["reason"])
            for r in night.stored.set_aside] == [("p01", None, SET_ASIDE)], (
        night.stored.set_aside)
    deferred = night.said("guiding was lost and did not recover on 1-2: "
                          f"{GAVE_UP}; retried on the next pass")
    assert len(deferred) == 2, night.said("1-2")
    # Three visits, each the hop's start and two recoveries, each with its
    # frame: the recovery's budget is the visit's own.
    assert seq == "SC" * 9, (
        f"1-2's visits did not each make the hop's start and two recovery "
        f"attempts: {seq}")


@pytest.mark.parametrize("action", ["abort", "skip", "warn"])
async def test_control_a_single_targets_guiding_loss_takes_guiding_action(
        group_hub, group_store, monkeypatch, action):
    """Control: a target in no group keeps today's escalation at the bound.
    Abort ends the run, skip drops the target for the run, and warn stands
    recovery down and shoots on unguided (here until the step's reject guard
    sets the step aside, since every frame of it is trailed).

    MUTANT "every target defers" (the ``member is not None`` test dropped,
    so a target in no group raises the deferral too): nothing catches it
    outside a panel visit, and the run ends in an error. RED on each
    (observed):
        abort:
            AssertionError: ('error', 'guiding was lost and did not recover:
            guiding could not be kept after 2 recovery attempts without a
            frame')
            assert ('error', 'gu...hout a frame') == ('unsafe',
            'g...hout a frame')
        skip, warn:
            AssertionError: ('error', 'guiding was lost and did not recover:
            guiding could not be kept after 2 recovery attempts without a
            frame')
            assert 'error' == 'incomplete'
    """
    group_store.set_escalation(ESCALATION[action])
    kw = _plan_kw()
    kw["max_consecutive_rejects"] = 6
    plan = SequencePlan(name="solo", dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False,
                        targets=[single("Solo", count=4)], **kw)
    night = await _lost_night(group_hub, monkeypatch, plan, lose={"Solo"})
    assert night.done, night.trace[-3:]
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    seq = _starts_and_frames(night, "Solo")
    if action == "abort":
        assert ended == ("unsafe", GAVE_UP), ended
        assert seq == "SCSCSC", seq
    elif action == "skip":
        assert ended[0] == "incomplete", ended
        assert night.said(f"Solo: skipped \u2014 {GAVE_UP}"), night.lines[-6:]
        assert seq == "SCSCSC", seq
    else:
        assert ended[0] == "incomplete", ended
        assert night.said(STOOD_DOWN), night.lines[-6:]
        # Shot on unguided: frames with no start of their own, until the
        # step's reject guard (6) sets the step aside.
        assert seq == "SCSCSCCCC", seq
    assert not night.said("did not recover"), night.said("did not recover")


async def test_a_group_the_pass_rule_sent_on_unguided_keeps_the_escalation(
        group_hub, group_store, monkeypatch):
    """A group the pass rule has already sent on unguided (every start of a
    pass failed, and ``warn`` said carry on, `_close_group_pass`) is not
    deferred when the #72 bound is reached: its failed starts already take
    the rig's escalation, and so does its guiding loss (spec 5.6 step 7). A
    deferral there would hold every panel of a mosaic the rig has already
    judged guider-less to a guide star it cannot get.

    THE NIGHT: the guider never starts, and a frame shot unguided is
    trailed (no stars) and rejected in accepted mode, so the bound is
    reached on each visit. Pass 1's four failed starts send the group on
    unguided (asserted as the premise); every later visit's recovery gives
    up and stands down, until each step's reject guard (6) sets it aside.

    Before this case the arm was held only by test_mosaic_spec_claims's AST
    check that the deferral's ``if`` mentions ``self._group_unguided``.
    Each mutant ran in a private copy of ``server/`` (scratchpad
    s3-tcf-review-mut3), restored and SHA-256 compared.

    MUTANT "the text kept, the arm gone" (the deferral's test made
    ``member is not None and (member.id not in self._group_unguided or
    True)``, which that AST check passes): RED (observed; every other case
    in this file stayed green):
        AssertionError: a group already sent on unguided was deferred for
        its guiding loss: ['M31: guiding was lost and did not recover on
        1-1: guiding could not be kept after 2 recovery attempts without a
        frame; retried on the next pass (1 of 3 consecutive)', 'M31:
        guiding was lost and did not recover on 1-2: guiding could not be
        kept after 2 recovery attempts without a frame; retried on the next
        pass (1 of 3 consecutive)']
    MUTANT "the unguided test dropped" (``if member is not None:``): RED
    (observed), the same failure word for word.
    """
    group_store.set_escalation(ESCALATION["warn"])
    guider = group_hub.guider
    kw = _plan_kw()
    kw["max_consecutive_rejects"] = 6
    night = Night(group_hub, monkeypatch, guide=lambda who, n: False,
                  stars=lambda who, f: 50 if guider.active else 0)
    try:
        night.done = await night.run(_lost_plan().model_copy(update=kw),
                                     wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    msgs = [m for _t, _lvl, m in night.lines]
    unguided = next((i for i, m in enumerate(msgs)
                     if "continuing the panels unguided" in m), None)
    assert unguided is not None, (
        f"premise: pass 1 sent the group on unguided: {msgs[:8]}")
    after = msgs[unguided + 1:]
    assert not [m for m in after if "did not recover" in m], (
        f"a group already sent on unguided was deferred for its guiding "
        f"loss: {[m for m in after if 'did not recover' in m][:2]}")
    assert [m for m in after if STOOD_DOWN in m], (
        f"premise: the bound was reached after the group went unguided: "
        f"{after[:8]}")
