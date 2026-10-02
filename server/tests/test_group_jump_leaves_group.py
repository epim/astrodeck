# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A member an instruction abandons leaves its group (#288 part 2; spec 5.1
pass boundary, 5.10, 1.2 instructions).

THE DEFECT. A ``run_target`` to another target, or a ``skip_target`` aimed
at the member being shot, raises `JumpTarget` at a frame boundary, and the
scheduler's arm removes the member from ``remaining`` through `_apply_jump`.
Nothing told its `GroupRun`, unlike a skip-drain, a missed start or (before
#316) a plain StopTarget, which all call `_group_member_gone`. So the
published ``state.group.set_aside`` never named it, and the pass rules still
counted it live: `GroupRun.close_pass` could not reach ``none_live``, and
when the last live panel was set aside at a boundary the group waited
``DEFER_WAIT_S`` for nothing before it ended.

THE FIX. When `_apply_jump` answers that it consumed the active target, the
arm calls `_group_member_gone` for it: "skipped by instruction", the
skip-drain's words, or "abandoned by an instruction".

The runs are the real `_run_scheduled`, the real instruction evaluator and
dispatcher, on the clocked simulator (tests/_group_harness.py). The rule
fires on the first frame of 1-2 (``only_target``), after an HFR above 1 px,
which every harness frame reports (2.0).

A JUMPED VISIT IS COUNTED (#322). `_visit_panel` hands the visit a jump ends
to its group before the jump goes on up (`GroupRun.note_visit`): its
exposures toward the pass boundary, its accepted frames, and a guider start
that worked toward the guide-start pass rule. Before it, a pass whose only
frame came from a jumped visit read as a pass of no exposures and waited
``DEFER_WAIT_S`` for nothing. A NO-OP JUMP (an unknown name, a run to the
member itself) leaves the member live and UNVISITED, so it is taken up again
at once, in the same pass: the decision `_visit_panel` documents, and the
last case here holds.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The #288 mutants
were applied in a private scratch copy of server/ (scratchpad s3ea-mut), and
the #322 ones in scratchpad s4-engb-mut, never in the shared tree (#254).
Every one of them was run again on the finished S4 code in scratchpad
s4-engb-resume-mut and failed as recorded:

* "jumped visit not noted": `_visit_panel`'s JumpTarget arm re-raising
  without its `GroupRun.note_visit` call.
* "the jumped visit marks the panel visited": that arm counting the visit
  through `GroupRun.visit_outcome` (``complete=False``), which also marks the
  panel visited, instead of `note_visit`.
* "the jumped visit's guide start not counted": that arm's `note_visit`
  passing ``guide_started=False``. Applied by the S4-ENGB verifier in its
  own scratch copy (scratchpad s4-engb-verify-mut).

The jumped visit's accepted frames are noted too, and no case here grades
them (#396): a consumed panel is not live, and the reject rule reads live
panels only, so they can move a verdict only when a no-op jump ends a visit
that banked a frame and the visit that takes the panel up again banks none.
Since S5 the frame that fires a jump is banked before the jump acts (#373,
S5 orchestrator ruling 3), so a jumped visit's accepted frames include it;
test_trigger_frame_banked.py holds that.
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.sequence.models import Instruction
from astrodeck.sequence.session import session_store

JUMPS = {
    "skip": ("skip_target", f"{GROUP_NAME} 1-2", "skipped by instruction"),
    "run": ("run_target", "Lead", "abandoned by an instruction"),
}


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


@pytest.mark.parametrize("kind", list(JUMPS))
async def test_a_member_a_jump_consumes_leaves_its_group(
        group_hub, monkeypatch, kind):
    """A plan of "Lead" (one frame, shot first) then a 1x2 that guides. The
    guider never starts on 1-1, so 1-1 is deferred every pass, each failure
    counted at the pass boundary, and set aside at its third. On 1-2's first
    frame a rule fires a jump that consumes 1-2 (``skip_target`` 1-2, or
    ``run_target`` Lead, which abandons it). 1-2 leaves the group: from then
    on ``state.group.set_aside`` names it, with the instruction's words.
    And the group ends the moment its last live panel is set aside: the
    boundary that sets 1-1 aside reaches ``none_live``, so passes 1 and 2
    wait ``DEFER_WAIT_S`` and nothing waits after pass 3.

    PASS 1 DOES NOT WAIT (#322). The frame 1-2 shot before the jump, and its
    guider start, count for the pass, so pass 1 took an exposure and goes
    straight on to pass 2; until #322 it read as a pass of no exposures and
    waited ``DEFER_WAIT_S`` too. Only pass 2, 1-1's deferral alone, waits.

    MUTANT "jumped visit not noted": RED on both kinds (observed, "skip";
    "run" word for word the same):
        AssertionError: ['M31: pass 1 took no exposures and deferred 1
        visits; waiting 300 s before the next pass', 'M31: pass 2 took no
        exposures and deferred 1 visits; waiting 300 s before the next pass']
        assert ['M31: pass 1...he next pass'] == ['M31: pass 2...he next pass']
          At index 0 diff: 'M31: pass 1 took no exposures and deferred 1
        visits; waiting 300 s before the next pass' != 'M31: pass 2 took no
        exposures and deferred 1 visits; waiting 300 s before the next pass'
          Left contains one more item: 'M31: pass 2 took no exposures and
        deferred 1 visits; waiting 300 s before the next pass'

    MUTANT "no _group_member_gone" (the call in the scheduler's JumpTarget
    arm deleted): RED on both kinds, the group never hears of it (observed
    again with #322 built, scratchpad s4-engb-resume-mut; "skip" and "run"
    word for word the same):
        AssertionError: no published group named 1-2 set aside after the
        jump: [[], [], [], [], [], [], [], [], [{'panel': '1-1', 'reason':
        'guiding did not start on 1-1 on 3 consecutive visits: no guide star
        found (attempt 3)'}]]
        assert False
    and, run again with that check taken out (a scratch copy of this test),
    the wait for nothing after 1-1's set-aside (observed, both kinds; pass
    1 no longer waits, #322):
        AssertionError: ['M31: pass 2 took no exposures and deferred 1
        visits; waiting 300 s before the next pass', 'M31: pass 3 took no
        exposures and deferred 1 visits; waiting 300 s before the next pass']
        assert ['M31: pass 2...he next pass'] == ['M31: pass 2...he next
        pass']
          Left contains one more item: 'M31: pass 3 took no exposures and
        deferred 1 visits; waiting 300 s before the next pass'
    """
    action, arg, words = JUMPS[kind]
    rule = Instruction(id="jump", trigger="on_hfr_above", threshold=1.0,
                       once=True, only_target=_name("1-2"), action=action,
                       target_arg=arg)
    plan = grid_plan(rows=1, cols=2, guide=True, before=[single("Lead")],
                     instructions=[rule])
    night = Night(group_hub, monkeypatch,
                  guide=lambda who, n: who != _name("1-1"))
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    assert [t for t, _f in night.shots()] == ["Lead", _name("1-2")], (
        f"premise: the rule consumed 1-2 at its first frame: {night.shots()}")
    jumped = next(t for t, _lvl, m in night.lines
                  if m.startswith("instruction: ") and ("skipping target" in m
                                                        or "jumping to" in m))

    # Every published state with its fake time, from the trace.
    published = [(night.t0 + e[0], e[2]) for e in night.trace
                 if e[1] == "state"]
    after = [v["group"]["set_aside"] for t, v in published
             if t >= jumped and v.get("group")]
    assert after, "premise: the group was published after the jump"
    named = {"panel": "1-2", "reason": words}
    assert any(named in sa for sa in after), (
        f"no published group named 1-2 set aside after the jump: {after}")

    waits = night.said("waiting 300 s before the next pass")
    assert waits == [
        "M31: pass 2 took no exposures and deferred 1 visits; waiting "
        "300 s before the next pass"], waits
    alerts = night.said("set aside for tonight")
    assert alerts == ["M31: guiding did not start on 1-1 on 3 consecutive "
                      "visits: no guide star found (attempt 3); set aside for "
                      "tonight: a restart tonight does not retry it, the next "
                      "night does"], alerts
    # The group ended at that boundary, and the run with it: the terminal
    # publish comes at the same fake instant as the set-aside.
    t_last = next(t for t, _lvl, m in night.lines if m == alerts[0])
    assert published[-1][1]["state"] == "complete", published[-1]
    assert published[-1][0] == t_last, (night.rel(t_last),
                                        night.rel(published[-1][0]))
    assert [r["target_id"] for r in night.stored.set_aside] == ["p00"], (
        "an instruction's drop lasts this run and is not recorded for the "
        f"night: {night.stored.set_aside}")


async def test_a_no_op_jump_leaves_the_panel_unvisited_for_the_same_pass(
        group_hub, monkeypatch):
    """THE NO-OP DECISION (#322). A 1x2, two L frames a panel, one a visit.
    On 1-2's first frame a rule fires ``run_target`` to a name no target
    has: `_apply_jump` says so and ignores it, and 1-2 stays in the night.
    Its jumped visit is counted, and it is NOT marked visited, so the
    scheduler takes it up again AT ONCE, IN THE SAME PASS: the next hop is to
    1-2 again, its frame is shot under pass 1, and only then does the pass
    close. Marked visited, it would sit ahead of the unvisited members
    without a visit's requeue, so the next selection closed the pass on it.

    RE-PINNED IN S5, DELIBERATELY: 1-2 SHOOTS TWO FRAMES FOR ITS TWO. This
    case pinned THREE ("The frame that fired the jump is not banked (the
    jump is raised before the ledger records it, #373), so 1-2 shoots three
    frames for its two"), a count that recorded #373 rather than wanted it.
    The frame that fires the jump is now banked before the jump acts (#373,
    S5 orchestrator ruling 3), so the visit that takes 1-2 up again shoots
    its second frame and completes it. Under the mutant "record after the
    instructions" (`_run_step` running the instructions above the banking
    again, applied in scratchpad/S5-ENG-SCHED-mut) this case is RED at the
    new pin, with the old count (observed):
        AssertionError: [('M31 1-1', 1), ('M31 1-2', 1), ('M31 1-2', 1),
        ('M31 1-1', 2), ('M31 1-2', 2)]
        assert 3 == 2

    MUTANT "the jumped visit marks the panel visited": RED (observed; the
    pass closed on 1-2 with no requeue, so the visit that took it up again
    was pass 2's, and a third pass followed):
        AssertionError: [('M31 1-1', 1), ('M31 1-2', 1), ('M31 1-2', 2),
        ('M31 1-1', 2), ('M31 1-2', 3)]
        assert [1, 2] == [1, 1]
          At index 1 diff: 2 != 1
    """
    rule = Instruction(id="noop", trigger="on_hfr_above", threshold=1.0,
                       once=True, only_target=_name("1-2"),
                       action="run_target", target_arg="Nobody")
    plan = grid_plan(rows=1, cols=2, panel_kw={"filters": ("L",), "count": 2},
                     instructions=[rule])
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    assert night.said("no target named 'Nobody'"), night.lines[-6:]
    shots = [(c["target"], (c["group"] or {}).get("pass"))
             for c in night.captures]
    jumped = next(k for k, (who, _p) in enumerate(shots)
                  if who == _name("1-2"))
    # The two 1-2 frames either side of the jump: the one that fired it and
    # the first of the visit that took the panel up again.
    pair = shots[jumped:jumped + 2]
    assert [who for who, _p in pair] == [_name("1-2")] * 2, shots
    assert [p for _who, p in pair] == [1, 1], shots
    assert sum(1 for who, _p in shots if who == _name("1-2")) == 2, shots
    assert night.stored.status == "complete", night.stored.status


async def test_a_jumped_visits_guider_start_counts_for_its_pass(
        group_hub, monkeypatch):
    """A guided 1x3 whose guider starts on 1-2 alone; a rule consumes 1-2
    (``skip_target``) at its first frame. Pass 1 then tried the guider on
    three panels and it worked on one, so the failures on 1-1 and 1-3 are
    theirs and each is retried. Only pass 2, where it fails on both panels
    tried, is the guider's fault, and the rig's guiding action (go on
    unguided) comes after each failing panel's SECOND attempt.

    Without the jumped visit's guider start, pass 1 read as two failures out
    of two and was blamed on the rig one pass early, after one attempt each.

    MUTANT "the jumped visit's guide start not counted" (the JumpTarget arm's
    `GroupRun.note_visit` passing ``guide_started=False``): RED (observed):
        AssertionError: ["M31: guiding did not start on 1-1: no guide star
        found (attempt 1); retried on the next pass (counted when the pass
        ends: a guider that fails on every panel is the rig's fault)"]
        assert 1 == 2
    """
    rule = Instruction(id="jump", trigger="on_hfr_above", threshold=1.0,
                       once=True, only_target=_name("1-2"),
                       action="skip_target", target_arg=_name("1-2"))
    plan = grid_plan(rows=1, cols=3, guide=True, instructions=[rule])
    night = Night(group_hub, monkeypatch,
                  guide=lambda who, n: who == _name("1-2"))
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.lines[-4:]
    assert night.shots()[0] == (_name("1-2"), "L"), (
        f"premise: 1-2 shot one frame and was consumed: {night.shots()[:3]}")
    rig = [k for k, (_t, _lv, m) in enumerate(night.lines)
           if "guiding did not start on any of the" in m]
    assert len(rig) == 1, night.said("guiding did not start on any")
    tries = [m for _t, _lv, m in night.lines[:rig[0]]
             if m.startswith("M31: guiding did not start on 1-1")]
    assert len(tries) == 2, tries
