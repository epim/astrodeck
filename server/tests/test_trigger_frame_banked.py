"""The frame that fires a ``run_target`` or ``skip_target`` is banked before
the instruction acts (#373, S5 orchestrator ruling 3; spec 5.1's
``JumpTarget`` row, 1.2 instructions).

THE DEFECT. `SequenceEngine._run_step` shot a frame, counted it, graded it,
put it in the report and ran the plan's instructions, and only then banked it
(`_record_frame`, or `_record_session_frame` for an accepted-mode reject). A
jump fired by a per-frame rule raises `JumpTarget` inside the instructions and
unwinds past the banking, so the frame was on disk and in the report while
``Session.frames`` and ``_done`` never counted it: the target shot it again, a
no-op jump reshot it at once, and a target a jump abandoned lost it to the
ledger for good.

THE FIX (S5 orchestrator ruling 3). The frame is banked before any
instruction acts on it: an accepted frame through `_record_frame`, an
accepted-mode reject through `_record_session_frame` with its reject verdict,
an attempts-mode reject by its escalation, except a retake, which is a fresh
exposure and so still waits for the instructions (an ``abort`` a rule fires
must not wait a whole exposure). The rule context is still read first, from
the frame as the camera and the grader left it.

TWO THINGS THE FIX MAKES REACHABLE, each held here too:

* A jump fired by the frame that COMPLETES its target. A member's group is
  told (`_visit_panel`'s jump arm, `GroupRun.note_complete`): left live, a
  complete panel the scheduler drops as "already complete" kept its group
  from ever being complete, and a follower waiting for the mosaic was then
  skipped as though the mosaic were set aside. A no-op jump leaves the
  target to be taken up again, and it is complete, so its
  ``on_target_complete`` rules are owed and run then (``_completion_owed``);
  before the fix the frame was reshot and they ran after it.
* The relative focus watchdog's baseline is seeded by a frame's banking, so
  a triggered refocus now clears the baseline AFTER the frame that fired it
  seeded it, and the first frame after the sweep seeds it again, as GN-08
  says. Banked after the instructions, the frame that fired the refocus
  re-seeded the baseline the sweep had just cleared, so every relative rule
  after a triggered refocus measured against the soft frame that fired it.

The nights run the real `_run_scheduled`, `_run_step`, instruction evaluator
and dispatcher on the clocked simulator (tests/_group_harness.py); every
harness frame reports HFR 2.0, so ``on_hfr_above`` 1.0 fires on the first
frame of the target its ``only_target`` names. A pass-through spy on
`_dispatch_actions` reads the ledger and ``_done`` at the moment a fired rule
is dispatched. The baseline case runs on the plain simulator hub
(test_instructions_engine.py's fixture), because the harness takes the
focuser off the rig and a refocus rule needs one; its autofocus is a double,
which says why.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants were
applied in a private scratch copy of server/ (scratchpad/S5-ENG-SCHED-mut),
never in the shared tree (#254):

* "record after the instructions": `_run_step`'s ``_run_instructions`` call
  moved back above the banking, as the code was before the fix.
* "the trigger banked as accepted": the accepted-mode reject's
  `_record_session_frame` passing ``auto_accepted=True``.
* "the jumped visit's completion not told to its group": the
  ``run.note_complete`` block in `_visit_panel`'s ``JumpTarget`` arm
  deleted.
* "the owed completion dropped": the scheduler's JumpTarget arm no longer
  adding a no-op-jumped, complete target to ``_completion_owed``.

Two more, added by S5-ENG-SCHED's verifier and applied in its own private
copy (scratchpad/S5-ENG-SCHED-verify-mut):

* "a completion rule's own jump owed again": the JumpTarget arm owing a
  target its ``on_target_complete`` rules whatever fired the jump, as it
  was first built, so a no-op jump out of those rules re-ran them until
  ``MAX_JUMPS`` ran out.
* "attempts-mode reject handled after the instructions": the attempts-mode
  ``warn``/``discard`` escalation moved back below the instructions.
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_ID, GROUP_NAME, T0, Night, grid_plan,
                            group_hub, group_store, single)  # noqa: F401
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import (ExposureStep, Instruction,
                                       SequencePlan, Target)
from astrodeck.sequence.session import session_store
from test_instructions_engine import (_wait_done, sim_hub,  # noqa: F401
                                      temp_store)

A_KEY = "a:a-L"


def _rule(action: str, arg: str, *, only: str = "A",
          trigger: str = "on_hfr_above") -> Instruction:
    return Instruction(id=f"jump-{action}-{arg}", trigger=trigger,
                       threshold=1.0, once=True, only_target=only,
                       action=action, target_arg=arg)


def _plan(*targets: Target, rules=(), **kw) -> SequencePlan:
    base = {"name": "trigger frame", "guide": False, "dither_every": 0,
            "autofocus_every": 0, "meridian_flip": False,
            "park_when_done": False, "warm_cooler_when_done": False,
            "recover_guiding": False}
    base.update(kw)
    return SequencePlan(targets=list(targets), instructions=list(rules),
                        **base)


def _a_frames(session) -> list:
    return [f for f in session.frames if f.target_id == "a"]


def _spy_dispatch(night: Night) -> list[dict]:
    """What the ledger and ``_done`` hold for A each time a fired rule is
    dispatched: the moment the instruction acts."""
    seen: list[dict] = []
    eng = night.engine
    real = eng._dispatch_actions

    async def dispatch(fired, target, step):
        frames = _a_frames(eng._session)
        seen.append({"actions": [f.action for f in fired],
                     "ledger": len(frames),
                     "accepted": sum(1 for f in frames if f.effective()),
                     "done": eng._done.get(A_KEY, 0)})
        return await real(fired, target, step)

    night.mp.setattr(eng, "_dispatch_actions", dispatch)
    return seen


async def _run(hub, monkeypatch, plan, *, spy: bool = False, **kw):
    night = Night(hub, monkeypatch, **{k: v for k, v in kw.items()
                                       if k in ("stars", "goto", "t0")})
    seen = _spy_dispatch(night) if spy else None
    start = {"session": kw["session"]} if "session" in kw else {}
    try:
        night.done = await night.run(plan, wall_s=60.0, **start)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night, seen


def _shot(night: Night, name: str) -> int:
    return sum(1 for t, _f in night.shots() if t == name)


NO_OP = {"run to itself": ("run_target", "A"),
         "run to an unknown name": ("run_target", "Nobody")}
CONSUMED = {"skip itself": ("skip_target", "A"),
            "run to another": ("run_target", "B")}


@pytest.mark.parametrize("kind", list(NO_OP))
async def test_a_no_op_jump_on_the_first_frame_leaves_two_exposures(
        group_hub, monkeypatch, kind):
    """A two-frame target "A" whose rule fires a no-op jump on its first
    frame. When the rule is dispatched, that frame is already in
    ``Session.frames`` and in ``_done``; A is taken up again and completes
    after EXACTLY TWO exposures, where it used to shoot three.

    MUTANT "record after the instructions": RED on both kinds (observed, "run
    to itself"; "run to an unknown name" word for word the same):
        AssertionError: at dispatch the ledger held [{'actions':
        ['run_target'], 'ledger': 0, 'accepted': 0, 'done': 0}]
        assert [{'accepted':... 'ledger': 0}] == [{'accepted':... 'ledger':
        1}]
          At index 0 diff: {'actions': ['run_target'], 'ledger': 0,
        'accepted': 0, 'done': 0} != {'actions': ['run_target'], 'ledger': 1,
        'accepted': 1, 'done': 1}
    """
    action, arg = NO_OP[kind]
    plan = _plan(single("A", count=2), single("B"), rules=[_rule(action, arg)])
    night, seen = await _run(group_hub, monkeypatch, plan, spy=True)
    assert night.done, night.lines[-4:]
    assert seen == [{"actions": [action], "ledger": 1, "accepted": 1,
                     "done": 1}], f"at dispatch the ledger held {seen}"
    assert _shot(night, "A") == 2, night.shots()
    assert len(_a_frames(night.stored)) == 2, night.stored.frames
    assert night.stored.status == "complete", night.stored.status


@pytest.mark.parametrize("kind", list(CONSUMED))
async def test_a_consumed_jump_banks_its_trigger_frame_for_the_next_night(
        group_hub, monkeypatch, kind):
    """The rule on A's first frame consumes A (``skip_target`` A, or
    ``run_target`` B, which abandons it). The frame is banked before the
    jump acts, so the ledger holds A's one frame and owes one; the next run
    of the session (``start(session=)``, as CONTINUE starts it) shoots
    exactly that one, and A is complete after TWO exposures across the two.
    Unbanked, the frame was on disk and in the report, and the next run shot
    both again.

    MUTANT "record after the instructions": RED on both kinds (observed,
    "skip itself"; "run to another" the same with ``run_target`` for
    ``skip_target``):
        AssertionError: at dispatch the ledger held [{'actions':
        ['skip_target'], 'ledger': 0, 'accepted': 0, 'done': 0}]
        assert [{'accepted':... 'ledger': 0}] == [{'accepted':... 'ledger':
        1}]
          At index 0 diff: {'actions': ['skip_target'], 'ledger': 0,
        'accepted': 0, 'done': 0} != {'actions': ['skip_target'], 'ledger':
        1, 'accepted': 1, 'done': 1}
    """
    action, arg = CONSUMED[kind]
    first, seen = await _run(group_hub, monkeypatch,
                             _plan(single("A", count=2), single("B"),
                                   rules=[_rule(action, arg)]), spy=True)
    assert first.done, first.lines[-4:]
    assert seen == [{"actions": [action], "ledger": 1, "accepted": 1,
                     "done": 1}], f"at dispatch the ledger held {seen}"
    assert _shot(first, "A") == 1, first.shots()
    assert first.stored.remaining()["a-L"] == 1, first.stored.remaining()
    again, _ = await _run(group_hub, monkeypatch,
                          _plan(single("A", count=2), single("B")),
                          session=first.stored, t0=T0 + 1800.0)
    assert again.done, again.lines[-4:]
    assert _shot(again, "A") == 1, again.shots()
    assert len(_a_frames(again.stored)) == 2, again.stored.frames
    assert again.stored.remaining()["a-L"] == 0, again.stored.remaining()


async def test_a_rule_that_does_not_fire_changes_nothing(group_hub,
                                                         monkeypatch):
    """CONTROL. The same plan with a rule whose threshold no frame reaches
    publishes the same night, byte for byte, as the plan with no rule at
    all: moving the banking ahead of the instructions changes nothing when
    nothing fires. GREEN under "record after the instructions" (observed)."""
    quiet = Instruction(id="never", trigger="on_hfr_above", threshold=99.0,
                        action="run_target", target_arg="B")
    ruled, seen = await _run(group_hub, monkeypatch,
                             _plan(single("A", count=2), single("B"),
                                   rules=[quiet]), spy=True)
    bare, _ = await _run(group_hub, monkeypatch,
                         _plan(single("A", count=2), single("B")))
    assert ruled.done and bare.done
    assert seen == [], f"premise: the rule never fired: {seen}"
    assert ruled.shots() == bare.shots() == [("A", "L"), ("A", "L"),
                                             ("B", "L")], ruled.shots()
    assert ruled.trace_text() == bare.trace_text()


async def test_in_accepted_mode_a_rejected_trigger_frame_stays_a_reject(
        group_hub, monkeypatch):
    """CONTROL. In accepted mode A's first frame is rejected (too few stars
    for ``min_stars``) and fires an ``on_frame_rejected`` rule's no-op jump.
    It is banked before the rule acts, AS A REJECT: in the ledger, not
    accepted, and ``_done`` does not count it. A then needs two accepted
    frames, so it shoots three in all, and the trigger frame is the one
    reject among them.

    MUTANT "the trigger banked as accepted": RED (observed):
        AssertionError: at dispatch the ledger held [{'actions':
        ['run_target'], 'ledger': 1, 'accepted': 1, 'done': 0}]
        assert [{'accepted':... 'ledger': 1}] == [{'accepted':... 'ledger':
        1}]
          At index 0 diff: {'actions': ['run_target'], 'ledger': 1,
        'accepted': 1, 'done': 0} != {'actions': ['run_target'], 'ledger': 1,
        'accepted': 0, 'done': 0}
    MUTANT "record after the instructions": RED here too, the reject not
    yet in the ledger when the rule acts (observed):
        AssertionError: at dispatch the ledger held [{'actions':
        ['run_target'], 'ledger': 0, 'accepted': 0, 'done': 0}]
    """
    frames: list[str] = []

    def stars(who: str, _filt: str) -> int:
        frames.append(who)
        return 0 if frames.count("A") == 1 and who == "A" else 50

    plan = _plan(single("A", count=2),
                 rules=[_rule("run_target", "A", trigger="on_frame_rejected")],
                 count_mode="accepted", min_stars=10)
    night, seen = await _run(group_hub, monkeypatch, plan, spy=True,
                             stars=stars)
    assert night.done, night.lines[-4:]
    assert seen == [{"actions": ["run_target"], "ledger": 1, "accepted": 0,
                     "done": 0}], f"at dispatch the ledger held {seen}"
    banked = _a_frames(night.stored)
    assert [f.effective() for f in banked] == [False, True, True], banked
    assert _shot(night, "A") == 3, night.shots()
    assert night.stored.status == "complete", night.stored.status


async def test_a_jump_on_a_members_last_frame_completes_it_in_its_group(
        group_hub, monkeypatch):
    """A 1x2 of one frame a panel, and "Later" after it under "Wait for the
    mosaic". A no-op jump fires on 1-2's one frame, which is banked and so
    completes the panel. Its group is told: "panel 1-2 complete" is said,
    the mosaic is complete, and Later is let go and shot. 1-2's
    ``on_target_complete`` rule runs once, when the scheduler takes the
    complete panel up again. 1-2 is shot once.

    MUTANT "the jumped visit's completion not told to its group": RED
    (observed; the group still counted 1-2 live, so Later read the mosaic
    as set aside):
        AssertionError: ['Later: skipped for tonight: the M31 mosaic it waits
        for is set aside tonight; not done, so the next night takes it up']
        assert not ['Later: skipped for tonight: the M31 mosaic it waits for
        is set aside tonight; not done, so the next night takes it up']
    MUTANT "the owed completion dropped": RED (observed; 1-2's rule never
    ran):
        AssertionError: []
        assert [] == ['1-2 is done']
          Right contains one more item: '1-2 is done'
    MUTANT "record after the instructions": RED (observed; 1-2's frame was
    reshot):
        AssertionError: [('M31 1-1', 'L'), ('M31 1-2', 'L'), ('M31 1-2',
        'L'), ('Later', 'L')]
        assert 2 == 1
    """
    p12 = f"{GROUP_NAME} 1-2"
    done_rule = Instruction(id="done", trigger="on_target_complete",
                            action="notify", level="info", only_target=p12,
                            message="1-2 is done")
    plan = grid_plan(rows=1, cols=2, panel_kw={"filters": ("L",), "count": 1},
                     after=[single("Later", after_group=GROUP_ID)],
                     instructions=[_rule("run_target", "Nobody", only=p12),
                                   done_rule])
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.said("no target named 'Nobody'"), night.lines[-6:]
    skipped = night.said("skipped for tonight")
    assert not skipped, skipped
    assert night.said("M31: panel 1-2 complete: 1 of 1 filters"), (
        night.said("complete"))
    assert _shot(night, p12) == 1, night.shots()
    assert _shot(night, "Later") == 1, night.shots()
    assert night.said("1-2 is done") == ["1-2 is done"], night.said("done")
    assert night.stored.status == "complete", night.stored.status


async def test_a_no_op_jump_on_the_last_frame_still_runs_on_target_complete(
        group_hub, monkeypatch):
    """A one-frame target "A" whose one frame fires a no-op jump: banked, A
    is complete, and the scheduler takes it up again only to find it so.
    Its ``on_target_complete`` rule runs once all the same, as it did when
    the frame was reshot; A is shot once, not twice.

    MUTANT "the owed completion dropped": RED (observed):
        AssertionError: []
        assert [] == ['A is done']
          Right contains one more item: 'A is done'
    GREEN under "the jumped visit's completion not told to its group"
    (observed), which is a member's half and A is in no group.
    MUTANT "record after the instructions": RED (observed; the frame was
    reshot, and the rule ran after the reshoot):
        AssertionError: [('A', 'L'), ('A', 'L'), ('B', 'L')]
        assert 2 == 1
    """
    done_rule = Instruction(id="done", trigger="on_target_complete",
                            action="notify", level="info", only_target="A",
                            message="A is done")
    plan = _plan(single("A", count=1), single("B"),
                 rules=[_rule("run_target", "A"), done_rule])
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.said("is already running"), night.lines[-6:]
    assert _shot(night, "A") == 1, night.shots()
    assert night.said("A is done") == ["A is done"], night.said("done")
    assert night.stored.status == "complete", night.stored.status


async def test_a_triggered_refocus_leaves_the_baseline_to_the_frame_after_it(
        sim_hub, temp_store, monkeypatch):
    """A relative refocus rule (HFR over 1.3 x the baseline). Frame 1 (HFR
    2.0) seeds the baseline; frame 2 (2.8, still accepted: the HFR gate
    needs four frames of history) fires the refocus, whose success clears
    the baseline; frame 3 (2.1) seeds it again. So the baseline the night
    ends on is frame 3's, what THIS focus achieved (GN-08).

    ``_autofocus`` is a double: a sweep is not what this decides, and the
    simulator's would put real star-field noise into it. It does to the
    baseline exactly what the real one's success path does
    (``self._focus_baseline_hfr = None`` in `_autofocus`, "the NEXT accepted
    frame re-seeds it"), and reports success. ``_capture`` answers the three
    HFRs, as test_instructions_engine.py's cases answer theirs.

    MUTANT "record after the instructions": RED (observed; the frame that
    fired the refocus re-seeded the baseline the sweep had just cleared):
        AssertionError: the baseline after a triggered refocus is 2.8, the
        frame that fired it, not 2.1, the first after it
        assert 2.8 == 2.1
    """
    eng = SequenceEngine(sim_hub)
    sweeps: list[str] = []

    async def autofocus(label, **_ctx):
        sweeps.append(label)
        eng._focus_baseline_hfr = None
        return True

    hfrs = iter([2.0, 2.8, 2.1])

    async def capture(*_a, **_k):
        return {"hfr": next(hfrs), "stats": {"median": 100},
                "saved_path": None}

    monkeypatch.setattr(eng, "_autofocus", autofocus)
    monkeypatch.setattr(eng, "_capture", capture)
    plan = SequencePlan(
        name="baseline", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            gain=100, count=3)])],
        instructions=[Instruction(trigger="on_hfr_above", threshold=1.3,
                                  relative=True, action="refocus")])
    eng.start(plan)
    await _wait_done(eng)
    assert sweeps == ["triggered refocus"], sweeps
    assert eng._focus_baseline_hfr == 2.1, (
        f"the baseline after a triggered refocus is "
        f"{eng._focus_baseline_hfr}, the frame that fired it, not 2.1, the "
        f"first after it")


COMPLETION_JUMP = {"a single target": "A", "a member": f"{GROUP_NAME} 1-2"}


@pytest.mark.parametrize("who", list(COMPLETION_JUMP))
async def test_a_completion_rules_own_no_op_jump_is_not_owed_again(
        group_hub, monkeypatch, who):
    """An ``on_target_complete`` rule that fires a no-op jump (a typo'd
    name) on a target's completion, on a single target and on a member. The
    completion rules run ONCE and the jump spends ONE of ``MAX_JUMPS``, as
    before the fix; the complete target is then dropped as complete.

    The owed completion (``_completion_owed``) is for a completion the jump
    PRE-EMPTED, fired by the frame loop. A jump out of the completion rules
    themselves also leaves a complete target in ``remaining`` after a
    no-op, and read as owed, the next selection ran the rules again, each
    run fired the same jump, and so on until the budget ran out: 65 runs of
    the rules and all 64 jumps spent, so every jump for the rest of the
    night was ignored. HEAD (the frame never banked) ran them once. Found
    by S5-ENG-SCHED's verifier; ``_completion_fired`` is the fix.

    MUTANT "a completion rule's own jump owed again": the JumpTarget arm's
    ``and ready.id not in self._completion_fired`` removed, which is the
    code as S5-ENG-SCHED first built it. RED on both (observed, in the
    private copy scratchpad/S5-ENG-SCHED-verify-mut):
        AssertionError: A's on_target_complete rules ran 65 times
        assert ['A is done',...is done', ...] == ['A is done']
          Left contains 64 more items, first extra item: 'A is done'
        AssertionError: M31 1-2's on_target_complete rules ran 65 times
        assert ['M31 1-2 is ...is done', ...] == ['M31 1-2 is done']
          Left contains 64 more items, first extra item: 'M31 1-2 is done'
    """
    name = COMPLETION_JUMP[who]
    rules = [Instruction(id="done", trigger="on_target_complete",
                         action="notify", level="info", only_target=name,
                         message=f"{name} is done"),
             Instruction(id="typo", trigger="on_target_complete",
                         action="run_target", target_arg="Nobody",
                         only_target=name)]
    if who == "a single target":
        plan = _plan(single("A", count=1), single("B"), rules=rules)
    else:
        plan = grid_plan(rows=1, cols=2,
                         panel_kw={"filters": ("L",), "count": 1},
                         after=[single("Later")], instructions=rules)
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.said(f"{name} is done") == [f"{name} is done"], (
        f"{name}'s on_target_complete rules ran "
        f"{len(night.said(f'{name} is done'))} times")
    assert len(night.said("no target named 'Nobody'")) == 1
    assert night.engine._jumps_spent == 1, night.engine._jumps_spent
    assert not night.said("budget exhausted"), night.said("budget")
    assert _shot(night, name) == 1, night.shots()
    assert night.stored.status == "complete", night.stored.status


async def test_in_attempts_mode_a_rejected_trigger_frame_is_kept_first(
        group_hub, monkeypatch):
    """In ATTEMPTS mode (the default) under the default ``warn``
    escalation, A's first frame is rejected (too few stars for
    ``min_stars``) and fires an ``on_frame_rejected`` rule's no-op jump.
    The reject is kept by its escalation BEFORE the rule acts: in the
    ledger, not accepted, and counted in ``_done`` as an attempt, so A
    takes exactly TWO exposures for its count of two, where it used to
    shoot three. The accepted-mode case above holds the other reject path;
    nothing held this one (S5-ENG-SCHED's verifier moved the attempts-mode
    escalation back below the instructions and every other case here, and
    test_instructions_engine.py, test_engine_safety.py,
    test_accepted_visit_bound.py and test_session_stack_backfill.py,
    stayed as they were).

    MUTANT "attempts-mode reject handled after the instructions": the
    attempts-mode ``warn``/``discard`` branch taken out of the banking and
    handled below the instructions, as a retake is. RED (observed, in the
    private copy scratchpad/S5-ENG-SCHED-verify-mut):
        AssertionError: at dispatch the ledger held [{'actions':
        ['run_target'], 'ledger': 0, 'accepted': 0, 'done': 0}]
        assert [{'accepted':... 'ledger': 0}] == [{'accepted':... 'ledger':
        1}]
          At index 0 diff: {'actions': ['run_target'], 'ledger': 0,
        'accepted': 0, 'done': 0} != {'actions': ['run_target'], 'ledger': 1,
        'accepted': 0, 'done': 1}
    """
    frames: list[str] = []

    def stars(who: str, _filt: str) -> int:
        frames.append(who)
        return 0 if frames.count("A") == 1 and who == "A" else 50

    plan = _plan(single("A", count=2),
                 rules=[_rule("run_target", "A", trigger="on_frame_rejected")],
                 min_stars=10)
    assert plan.count_mode == "attempts", plan.count_mode
    night, seen = await _run(group_hub, monkeypatch, plan, spy=True,
                             stars=stars)
    assert night.done, night.lines[-4:]
    assert seen == [{"actions": ["run_target"], "ledger": 1, "accepted": 0,
                     "done": 1}], f"at dispatch the ledger held {seen}"
    banked = _a_frames(night.stored)
    assert [f.effective() for f in banked] == [False, True], banked
    assert _shot(night, "A") == 2, night.shots()
    assert night.stored.status == "complete", night.stored.status
