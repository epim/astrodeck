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

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/ (scratchpad s3ea-mut), never in
the shared tree (#254).
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

    PASS 1'S WAIT IS ITSELF A DEFECT (#322), pinned here as it stands: the
    frame 1-2 shot before the jump is not counted for its pass, because a
    jumped visit never reaches `GroupRun`, so pass 1 reads as a pass of no
    exposures. The fix for #322 changes the first expected line below.

    MUTANT "no _group_member_gone" (the call in the scheduler's JumpTarget
    arm deleted): RED on both kinds, the group never hears of it (observed,
    "skip"; "run" the same with one publish fewer):
        AssertionError: no published group named 1-2 set aside after the
        jump: [[], [], [], [], [], [], [], [], [{'panel': '1-1', 'reason':
        'guiding did not start on 1-1 on 3 consecutive visits: no guide star
        found (attempt 3)'}]]
        assert False
    and, run again with that check taken out (a scratch copy of this test),
    the wait for nothing after 1-1's set-aside (observed, both kinds):
        AssertionError: ['M31: pass 1 took no exposures and deferred 1
        visits; waiting 300 s before the next pass', 'M31: pass 2 took no
        expos...300 s before the next pass', 'M31: pass 3 took no exposures
        and deferred 1 visits; waiting 300 s before the next pass']
        assert ['M31: pass 1...he next pass'] == ['M31: pass 1...he next
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
        f"M31: pass {n} took no exposures and deferred 1 visits; waiting "
        f"300 s before the next pass" for n in (1, 2)], waits
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

