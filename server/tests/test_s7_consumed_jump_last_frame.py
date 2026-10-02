# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A jump that consumes its target on that target's LAST owed frame leaves a
complete target, not a skipped one (#481, S7 finding A2, S7 orchestrator
ruling 2; spec 5.1's ``JumpTarget`` row, 1.2 instructions; S5 orchestrator
ruling 3, #373; S7-ENG-FLIP).

THE DEFECT. Since #373 the frame that fires an instruction is banked before
the instruction acts, so a ``run_target`` to another target, or a
``skip_target`` of the target being shot, can fire on the frame that
completes it. S5 made the no-op jump right (its ``on_target_complete``
rules owed once, ``_completion_owed``), and told a member's group that the
panel was complete (`GroupRun.note_complete`). A CONSUMED jump was left as
it was: `_apply_jump` wrote "skipped <name>" into the report timeline for a
target the ledger holds complete, "abandoning" it in the log, and the
scheduler removed it with its ``on_target_complete`` rules never run.

THE RULING (S7 orchestrator ruling 2). Such a target is complete. No
"skipped <name>" is written to the report timeline. The group is told the
panel is complete (`GroupRun.note_complete`), not "abandoned by an
instruction". Its ``on_target_complete`` rules are owed once, through
``_completion_owed``, and not when they have fired already
(``_completion_fired``): the jump that came out of those rules themselves.

AS BUILT. The scheduler's JumpTarget arm reads whether the target is
complete before it applies the jump; `_apply_jump` then writes no skip for
a complete target and says the target is complete rather than abandoned;
the arm owes the completion (unless ``_completion_fired``) and keeps the
target at the FRONT of ``remaining``, so the next selection finds it
complete and runs its rules there, before the jump's destination is shot:
the target completed first, and its frame fired the jump.

The nights run the real `_run_scheduled`, `_run_step`, instruction evaluator
and dispatcher on the clocked simulator (tests/_group_harness.py); every
harness frame reports HFR 2.0, so ``on_hfr_above`` 1.0 fires on the first
frame of the target its ``only_target`` names (test_trigger_frame_banked.py's
rule).

Each case names the mutants it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants
were applied in a private scratch copy of server/ (scratchpad
S7-ENG-FLIP-mut, from a byte backup), never in the shared tree (#254):

* "consumed jump marks skipped": the JumpTarget arm and `_apply_jump` as
  they were before this change (a consumed target removed, marked skipped,
  its completion never owed).
* "completion owed twice": the arm owing a consumed, complete target its
  ``on_target_complete`` rules whatever fired the jump (the ``not in
  self._completion_fired`` guard removed from the consumed half).
* "owed completion left in place": the consumed, complete target owed but
  left where it stood in ``remaining``, behind the jump's destination.
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_ID, GROUP_NAME, Night, grid_plan,  # noqa: F401
                            group_hub, group_store, single)
from astrodeck.sequence.models import Instruction
from test_trigger_frame_banked import _plan, _rule, _run, _shot

P12 = f"{GROUP_NAME} 1-2"


def _done_rule(name: str) -> Instruction:
    return Instruction(id=f"done-{name}", trigger="on_target_complete",
                       action="notify", level="info", only_target=name,
                       message=f"{name} is done")


def _skips(night: Night) -> list[str]:
    """The report timeline's skip entries, as `SessionReporter.mark_skipped`
    writes them: "skipped <name>"."""
    return [e["reason"] for e in night.engine.reporter.build().safety_events
            if e.get("action") == "skip"]


def _first_shot_at(night: Night, name: str) -> float:
    """The night's clock when ``name``'s first exposure opened."""
    return next(c["t"] for c in night.captures if c["target"] == name)


#: (who is shot, the jump's action, its argument, who the jump runs to)
CASES = {
    "a single target, skip itself": ("A", "skip_target", "A", None),
    "a single target, run to another": ("A", "run_target", "B", "B"),
    "a member, skip itself": (P12, "skip_target", P12, None),
    "a member, run to another": (P12, "run_target", "Later", "Later"),
}


def _night_plan(who: str, action: str, arg: str, *, count: int = 1,
                extra_rules=()):
    rules = [_rule(action, arg, only=who), _done_rule(who), *extra_rules]
    if who == P12:
        return grid_plan(rows=1, cols=2,
                         panel_kw={"filters": ("L",), "count": count},
                         after=[single("Later")], instructions=rules)
    return _plan(single("A", count=count), single("B"), rules=rules)


@pytest.mark.parametrize("case", list(CASES))
async def test_a_consumed_jump_on_the_last_frame_leaves_it_complete(
        group_hub, monkeypatch, case):
    """The jump fires on the one frame the target owes. The target is shot
    once and complete in the ledger; the report timeline has no "skipped"
    entry for it; a member's group holds the panel complete and never set
    aside; and the target's ``on_target_complete`` rule runs exactly once,
    before the jump's destination is shot.

    MUTANT "consumed jump marks skipped": RED on all four (observed; each
    "skip itself" case word for word its "run to another" twin):
        a single target:
        AssertionError: the report says a complete target was skipped:
        ['skipped A']
        assert ['skipped A'] == []
        a member:
        AssertionError: the report says a complete target was skipped:
        ['skipped M31 1-2']
        assert ['skipped M31 1-2'] == []
    MUTANT "owed completion left in place": RED on both "run to another"
    cases (observed), the two "skip itself" cases green, where the target
    already stands first; the times are the night's clock:
        AssertionError: A's on_target_complete rule ran after B, the jump's
        destination, was shot
        assert 1788313749.0 <= 1788313719.0
        AssertionError: M31 1-2's on_target_complete rule ran after Later,
        the jump's destination, was shot
        assert 1788313779.0 <= 1788313749.0
    GREEN under "completion owed twice" (observed): no completion rule
    here fires a jump.
    """
    who, action, arg, dest = CASES[case]
    night, _ = await _run(group_hub, monkeypatch,
                          _night_plan(who, action, arg))
    assert night.done, night.lines[-4:]
    assert _shot(night, who) == 1, night.shots()
    assert night.stored.status == "complete", night.stored.status
    assert _skips(night) == [], (
        f"the report says a complete target was skipped: {_skips(night)}")
    assert night.said(f"{who} is done") == [f"{who} is done"], (
        f"{who}'s on_target_complete rule ran "
        f"{len(night.said(f'{who} is done'))} times")
    assert not night.said(f"abandoning {who}"), night.said("abandoning")
    if who == P12:
        run = night.engine._group_runs[GROUP_ID]
        assert "p01" in run.completed and "p01" not in run.set_aside, (
            run.completed, run.set_aside)
        assert night.said("M31: panel 1-2 complete: 1 of 1 filters")
    if dest is not None:
        done_at = next(t for t, _l, m in night.lines
                       if m == f"{who} is done")
        assert done_at <= _first_shot_at(night, dest), (
            f"{who}'s on_target_complete rule ran after {dest}, the jump's "
            f"destination, was shot")
        assert _shot(night, dest) == 1, night.shots()


@pytest.mark.parametrize("who", ["A", P12])
async def test_a_completion_rules_own_consumed_jump_is_not_owed_again(
        group_hub, monkeypatch, who):
    """The target's last frame fires ``run_target`` to one target, and its
    ``on_target_complete`` rules fire ``run_target`` to another: a consumed
    jump out of the completion rules themselves. Those rules have run
    (``_completion_fired``), so that jump owes them nothing: they run ONCE,
    two jumps are spent, and the night shoots the target, then the target
    its completion rules ran to, then the one its frame ran to.

    MUTANT "completion owed twice": RED on both (observed), the completion
    rules re-run by every jump they fired until ``MAX_JUMPS`` ran out:
        AssertionError: A's on_target_complete rules ran 64 times
        assert ['A is done',...is done', ...] == ['A is done']
        AssertionError: M31 1-2's on_target_complete rules ran 64 times
        assert ['M31 1-2 is ...is done', ...] == ['M31 1-2 is done']
    MUTANT "consumed jump marks skipped": RED on both (observed), the rules
    never run at all:
        AssertionError: A's on_target_complete rules ran 0 times
        assert [] == ['A is done']
        AssertionError: M31 1-2's on_target_complete rules ran 0 times
        assert [] == ['M31 1-2 is done']
    MUTANT "owed completion left in place": RED on both (observed), the
    rules run once but after the frame's destination was shot:
        AssertionError: [('A', 'L'), ('B', 'L'), ('C', 'L')]
        assert ['A', 'B', 'C'] == ['A', 'C', 'B']
        AssertionError: [('M31 1-1', 'L'), ('M31 1-2', 'L'), ('Later',
        'L'), ('Then', 'L')]
        assert ['M31 1-2', 'Later', 'Then'] == ['M31 1-2', 'Then', 'Later']
    """
    first, then = ("B", "C") if who == "A" else ("Later", "Then")
    onward = Instruction(id="onward", trigger="on_target_complete",
                         action="run_target", target_arg=then,
                         only_target=who)
    if who == P12:
        plan = grid_plan(rows=1, cols=2,
                         panel_kw={"filters": ("L",), "count": 1},
                         after=[single("Later"), single("Then")],
                         instructions=[_rule("run_target", first, only=who),
                                       _done_rule(who), onward])
    else:
        plan = _plan(single("A", count=1), single("B"), single("C"),
                     rules=[_rule("run_target", first, only=who),
                            _done_rule(who), onward])
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    ran = night.said(f"{who} is done")
    assert ran == [f"{who} is done"], (
        f"{who}'s on_target_complete rules ran {len(ran)} times")
    assert night.engine._jumps_spent == 2, night.engine._jumps_spent
    order = [t for t, _f in night.shots() if t in (who, first, then)]
    assert order == [who, then, first], night.shots()
    assert _skips(night) == [], _skips(night)


@pytest.mark.parametrize("who", ["A", P12])
async def test_a_consumed_jump_on_an_earlier_frame_still_skips(
        group_hub, monkeypatch, who):
    """CONTROL. The same consuming jump on the FIRST of two owed frames: the
    target is not complete, so it is abandoned as before. The report
    timeline says "skipped <name>", the log says it is abandoned, a member
    is set aside in its group "abandoned by an instruction", and its
    ``on_target_complete`` rule never runs.

    GREEN under "consumed jump marks skipped", "completion owed twice" and
    "owed completion left in place" (observed): none of them touches a
    target that is not complete.

    RE-PINNED FOR WP-44 (#524, 2026-09-30, deliberate): the report line now
    carries the reason `mark_skipped` was called with, "abandoned by an
    instruction", after a colon.
    """
    dest = "B" if who == "A" else "Later"
    night, _ = await _run(group_hub, monkeypatch,
                          _night_plan(who, "run_target", dest, count=2))
    assert night.done, night.lines[-4:]
    assert _shot(night, who) == 1, night.shots()
    assert _skips(night) == [f"skipped {who}: abandoned by an instruction"], (
        _skips(night))
    assert night.said(f"(abandoning {who})"), night.said("instruction")
    assert night.said(f"{who} is done") == []
    if who == P12:
        run = night.engine._group_runs[GROUP_ID]
        assert run.set_aside.get("p01") == "abandoned by an instruction", (
            run.set_aside)
        assert "p01" not in run.completed


async def test_a_no_op_jump_on_the_last_frame_is_unchanged(group_hub,
                                                           monkeypatch):
    """CONTROL. A no-op jump (a run to the target itself) on its last owed
    frame, the path S5 built: A is shot once, taken up again complete, its
    rule runs once, one jump is spent, and nothing is written as skipped.
    test_trigger_frame_banked.py holds the rest of that path.

    GREEN under "consumed jump marks skipped", "completion owed twice" and
    "owed completion left in place" (observed).
    """
    night, _ = await _run(group_hub, monkeypatch,
                          _night_plan("A", "run_target", "A"))
    assert night.done, night.lines[-4:]
    assert night.said("is already running"), night.lines[-6:]
    assert _shot(night, "A") == 1, night.shots()
    assert night.said("A is done") == ["A is done"], night.said("done")
    assert night.engine._jumps_spent == 1
    assert _skips(night) == [], _skips(night)


def _gated_after_its_frame(how: str):
    """Target "A", ready when the night opens and shut out by its own
    schedule before its one 30 s frame ends: its hour-angle window closes
    15 s into the frame ("its window closes"), or, setting, it sinks below
    its start gate 15 s into the frame ("it sinks below its start gate").
    Either way the next selection's gating no longer calls it ready."""
    from _group_harness import T0, LAT, LON
    from astrodeck.sequence import schedule as sch
    from astrodeck.sequence.models import Schedule
    if how == "its window closes":
        return single("A", ha_h=2.0 - 15.0 / 3600.0,
                      schedule=Schedule(max_hour_angle_h=2.0))
    a = single("A", ha_h=2.0)
    alt = [sch.target_altitude(a.ra_hours, a.dec_deg, LAT, LON, T0 + s)
           for s in (0.0, 15.0, 30.0)]
    assert alt[0] > alt[1] > alt[2], "the fixture target must be setting"
    a.schedule = Schedule(min_altitude_deg=alt[1])
    return a


@pytest.mark.parametrize("how", ["its window closes",
                                 "it sinks below its start gate"])
async def test_a_complete_target_owed_its_rules_is_not_held_by_its_gating(
        group_hub, monkeypatch, how):
    """A consumed jump on A's last owed frame, where A's own schedule shuts
    it out before that frame ends (S7 review of #481, S7 orchestrator ruling
    2). A is complete and owed its ``on_target_complete`` rules; they need
    no sky, since they shoot nothing. So the next selection takes A up
    whatever its gating says, runs its rules once, before the jump's
    destination is shot, and drops it; the night then shoots B and ends
    complete, the session complete, and nothing is written as skipped.

    THE DEFECT THIS HOLDS (found reviewing S7). The arm put A at the front
    of ``remaining`` for "the next selection finds it complete and runs
    them", but the selection only takes up a target its gating calls ready,
    and a complete target is gated like any other. With its window closed,
    A sat out the selection, B was shot, and the closed-window sweep then
    wrote "skipped A" and ended the night as a dawn cutoff, the session
    left dormant with nothing owed and A's rules never run. Below its start
    gate, A was a waiter with no rise ahead tonight: the run went on waiting
    for a target that owed nothing, the long-wait park-hold stopping the
    mount, until the harness's 16 h horizon ended it (on a rig, until A rose
    again or a window stop, the #374 shape). Before S7 the consumed jump
    removed A (with "skipped A", #481), so this was S7's to introduce.

    MUTANT "complete owed target gated": the selection's owed-completion
    pick removed (the scheduler as S7 left it). Applied in a private scratch
    copy of server/ (scratchpad s7-review-mut), never in the shared tree.
    RED on both (observed):
        its window closes:
        AssertionError: the night ended dawn_cutoff, not complete
        assert 'dawn_cutoff' == 'complete'
        it sinks below its start gate:
        AssertionError: the night did not end by itself: [(1788313719.0,
        'info', 'target 2/2: B'), (1788313749.0, 'info', 'the next target
        is a long wait away — stopping tracking until the next target is
        set up'), (1788371289.0, 'warning', 'sequence aborted'),
        (1788371289.0, 'info', "'trigger frame': stopped by hand, so
        auto-resume is disarmed for it. Arm it from the session list to
        pick it up again.")]
        assert False
    The controls are this file's other cases, whose A is ready when its
    rules come due: GREEN under the mutant (observed), since the pick only
    changes a selection that the gating would have refused.
    """
    plan = _plan(_gated_after_its_frame(how), single("B"),
                 rules=[_rule("run_target", "B", only="A"), _done_rule("A")])
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, (
        f"the night did not end by itself: {night.lines[-4:]}")
    ended = night.engine.reporter.build().end_reason
    assert ended == "complete", f"the night ended {ended}, not complete"
    assert night.said("A is done") == ["A is done"], night.said("done")
    done_at = next(t for t, _l, m in night.lines if m == "A is done")
    assert done_at <= _first_shot_at(night, "B"), (
        "A's on_target_complete rule ran after B was shot")
    assert _shot(night, "A") == 1 and _shot(night, "B") == 1, night.shots()
    assert _skips(night) == [], _skips(night)
    assert night.stored.status == "complete", night.stored.status


@pytest.mark.parametrize("dest", ["A", "B"])
async def test_every_window_closing_leaves_the_complete_target_complete(
        group_hub, monkeypatch, dest):
    """#490: A's one 150 s frame, inside ITS OWN 2-minute window
    (``max_run_min``), ends past it and fires a jump, a no-op ("run to A") or
    a consumed one ("run to B"). A's window is closed at the next selection,
    but A is complete and owed its ``on_target_complete`` rules, so that
    selection takes A up (the pick above) before any all-closed sweep could
    mark it skipped: its rule runs once, and it is never written as skipped,
    whichever way the jump went.

    MUTANT "complete owed target gated" (above), in the same private
    scratch copy: RED on both (observed), the sweep marking A skipped with
    its rule never run, as #490 recorded:
        AssertionError: []
        assert [] == ['A is done']

    RE-PINNED FOR WP-44 (#524, 2026-09-30, deliberate): the report line now
    carries the reason `mark_skipped` was called with, "window closed",
    after a colon.

    RE-PINNED AGAIN FOR #164 (orchestrator ruling, 2026-10-02, owner-approved
    plan 2026-09-30, backlog ruling D-nn): A and B used to SHARE one frozen
    window, both anchored to the run's start, so B's budget was already
    spent by the time A's 150 s frame put that shared window in the past --
    B was skipped with it ("skipped B: window closed", a dawn cutoff)
    however the jump went, despite never having been touched. B's own
    ``max_run_min`` now counts from B's own first attempt, which has not
    happened by the time A completes at 150 s: an unattempted target has no
    ``max_run_min`` limit of its own yet (only its own dawn/clock stop,
    which B has none of), so B is picked up fresh next, shoots its one frame
    well inside its own new budget, and the night completes instead of
    ending on a dawn cutoff that was really just A's own overrun bleeding
    onto a target that never got a turn -- #164's own two-target example.
    """
    from astrodeck.sequence.models import Schedule
    a = single("A", schedule=Schedule(max_run_min=2))
    a.steps[0].exposure_s = 150.0
    b = single("B", schedule=Schedule(max_run_min=2))
    plan = _plan(a, b, rules=[_rule("run_target", dest, only="A"),
                              _done_rule("A")])
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.said("A is done") == ["A is done"], night.said("done")
    jumped = ["instruction: jumping to target 'B' (A is complete)"]
    no_op = ["instruction run_target: 'A' is already running — no-op"]
    if dest == "B":
        assert night.said(jumped[0]) == jumped, night.lines
        assert night.said(no_op[0]) == [], night.lines
    else:
        assert night.said(no_op[0]) == no_op, night.lines
        assert night.said(jumped[0]) == [], night.lines
    assert _skips(night) == [], _skips(night)
    assert night.shots() == [("A", "L"), ("B", "L")], night.shots()
    assert night.stored.status == "complete", night.stored.status
    assert night.engine.reporter.build().end_reason == "complete"
