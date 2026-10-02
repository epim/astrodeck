# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A complete target does not keep a run alive, idle, until its gate opens
(#537; spec 5.1 selection and 5.9 resume; the #374 class: a run kept alive
by a target that cannot contribute).

THE DEFECT. `_schedule_loop` dropped a complete target only once its gating
called it ready ("already complete — skipping"). A resumed session carries
its complete targets in ``remaining``, so one before its window, or below
its start gate, was a waiter like any other: the run stayed alive after
everything the session owed had been shot, only to skip the target as
complete when its gate opened. #537's probe on the clocked simulator: night
two resumed with A complete and 20 minutes before its hour-angle window, B
shot at once, and the log then read "1200 A: already complete — skipping",
19.5 idle minutes with the cooler running, the mount stopped by the idle
clock but not parked, and the wind-down held back.

AS BUILT. `_drop_complete` takes every complete target out of ``remaining``
at the run's start (after `_start_groups`) and at every selection, before
any gating is asked, in the words the selection always used. A group member
is covered too. The exception is a target owed its ``on_target_complete``
rules (``_completion_owed``, #373, #481), which the selection takes up
whatever its gating says.

THE NIGHTS run the real `start`, `_run_scheduled` and `_schedule_loop` on
the clocked simulator (tests/_group_harness.py). The second night is a
resume: `engine.start` handed a dormant session whose ledger already holds
the complete target's frames, as CONTINUE and auto-resume hand it.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-B-mut``), from a byte backup restored and sha256-checked after each,
never in the shared tree (#254). The observed failure is quoted verbatim
(the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_ID, GROUP_NAME, Night, T0,  # noqa: F401
                            grid_plan, group_hub, group_store, single)
from astrodeck.sequence.models import Schedule, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame
from test_s7_consumed_jump_last_frame import CASES, _night_plan
from test_trigger_frame_banked import _rule, _run

#: #537's probe: an hour-angle window of +/-2 h, and the complete target 20
#: minutes of hour angle east of its opening at the night's start.
MAX_HA_H = 2.0
BEFORE_S = 20 * 60.0
#: How long the probe idled for its complete target, less the half-minute
#: B's frame took: the run must end well inside it.
IDLE_S = 19.5 * 60.0


def _complete_before_its_window(name: str, *, tid: str | None = None
                                ) -> Target:
    """A one-frame target 20 minutes before its hour-angle window."""
    t = single(name, count=1, ha_h=-(MAX_HA_H + BEFORE_S / 3600.0))
    if tid is not None:
        t.id = tid
    t.schedule = Schedule(max_hour_angle_h=MAX_HA_H)
    return t


def _plain_plan(*targets: Target) -> SequencePlan:
    return SequencePlan(name="resume", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=list(targets))


def _dormant(plan: SequencePlan, *complete: Target) -> Session:
    """The session a resume is handed: dormant, the frames of ``complete``
    banked on an earlier night, everything else owed."""
    frames = [SessionFrame(ts=T0 - 3600.0, night="earlier", target_id=t.id,
                           step_id=s.id)
              for t in complete for s in t.steps for _k in range(s.count)]
    return Session(name=plan.name, created_ts=T0 - 7200.0, updated_ts=T0 - 3600.0,
                   status="dormant", plan=plan, frames=frames)


async def _resume(hub, monkeypatch, plan: SequencePlan, session: Session):
    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, session=session)
    finally:
        await night.close()
    return night


def _ended_at(night: Night) -> float:
    """The night's clock at the run's terminal publish."""
    terminal = [e["ts"] for e in night.events if e["type"] == "sequence"
                and e["data"].get("state") in ("complete", "aborted", "error")]
    assert terminal, "premise: the run published a terminal state"
    return night.rel(terminal[0])


@pytest.mark.parametrize("count_mode", ["attempts", "accepted"])
async def test_a_complete_target_before_its_window_does_not_hold_the_run(
        group_hub, monkeypatch, count_mode):
    """#537's probe. Night two resumes with A complete and 20 minutes before
    its hour-angle window, B owed and in its window. B is shot at once and
    the run ends there, A dropped as complete at the start and never waited
    for; the session is complete. In both count modes: accepted mode reads
    the ledger's accepted map, taken once per selection.

    RED under mutant "complete targets left to the gating" (both
    `_drop_complete` calls taken out, `_run_scheduled`'s and the
    selection's), both cases, observed:

        AssertionError: the run ended 1200.0 s in, having shot ['B'] by 30.0
        s; a complete target kept it alive, idle: A said at [1200.0]

    RED under mutant "an empty accepted map" (`_drop_complete` handing
    ``accepted={}``), case accepted, observed:

        AssertionError: the run ended 1200.0 s in, having shot ['B'] by 30.0
        s; a complete target kept it alive, idle: A said at [1200.0]
    """
    a = _complete_before_its_window("A")
    b = single("B", count=1, ha_h=-1.0)
    plan = _plain_plan(a, b)
    plan.count_mode = count_mode
    night = await _resume(group_hub, monkeypatch, plan, _dormant(plan, a))
    assert night.done, night.lines[-4:]
    assert [c["target"] for c in night.captures] == ["B"], night.shots()
    said = [night.rel(t) for t, _l, m in night.lines
            if m == "A: already complete — skipping"]
    shot_by = night.rel(night.captures[-1]["t"]) + 30.0
    ended = _ended_at(night)
    assert ended < IDLE_S and said and said[0] <= shot_by, (
        f"the run ended {ended} s in, having shot ['B'] by {shot_by} s; a "
        f"complete target kept it alive, idle: A said at {said}")
    assert night.engine.state.get("end_reason") == "complete", (
        night.engine.state.get("end_reason"))


async def test_a_complete_member_before_its_window_does_not_hold_the_run(
        group_hub, monkeypatch):
    """The same for a group member. A 1x2 mosaic resumes with 1-1 complete
    and 20 minutes before its hour-angle window, 1-2 owed and in its window:
    1-2 is shot and the run ends, 1-1 never waited for.

    RED under mutant "complete targets left to the gating", observed:

        AssertionError: the mosaic's run ended 1200.0 s in, having shot its
        owed panel by 30.0 s; its complete panel kept it alive, idle: said
        at [1200.0]
    """
    plan = grid_plan(rows=1, cols=2, panel_kw={
        "filters": ("L",), "count": 1, "ha_h": -(MAX_HA_H + BEFORE_S / 3600.0),
        "ha_step_h": 1.0 + BEFORE_S / 3600.0,
        "schedule_kw": {"max_hour_angle_h": MAX_HA_H}})
    p11, p12 = plan.targets
    night = await _resume(group_hub, monkeypatch, plan, _dormant(plan, p11))
    assert night.done, night.lines[-4:]
    assert [c["target"] for c in night.captures] == [p12.name], night.shots()
    said = [night.rel(t) for t, _l, m in night.lines
            if m == f"{p11.name}: already complete — skipping"]
    shot_by = night.rel(night.captures[-1]["t"]) + 30.0
    ended = _ended_at(night)
    assert ended < IDLE_S and said, (
        f"the mosaic's run ended {ended} s in, having shot its owed panel by "
        f"{shot_by} s; its complete panel kept it alive, idle: said at "
        f"{said}")
    run = night.engine._group_runs[GROUP_ID]
    assert p11.id in run.completed and p11.id not in run.set_aside, (
        f"premise: the complete panel is complete in its group, never set "
        f"aside: {run.completed}, {run.set_aside}")


@pytest.mark.parametrize("count_mode", ["attempts", "accepted"])
async def test_a_target_complete_mid_run_is_dropped_at_the_next_selection(
        group_hub, monkeypatch, count_mode):
    """At every selection, not only at the start. A's one 600 s frame
    completes it and its ``on_target_complete`` rule fires a jump to A
    itself: a no-op jump out of the completion rules, so nothing is owed
    (``_completion_fired``) and A stays in ``remaining``, complete. Its
    window (``max_run_min`` 5) closed while that frame was open. The next
    selection drops it as complete; B is shot and the run completes. Left to
    the gating, A was a closed window: reported skipped, a complete target,
    and the night ended as a dawn cutoff.

    In both count modes. In accepted mode the selection reads the ledger's
    accepted map as `_accepted_now` keeps it, from the frames banked since
    it was last asked, and A's frame was banked after the run's start.

    RED under mutant "only at the start" (the selection's `_drop_complete`
    call taken out, `_run_scheduled`'s kept), both cases, observed (and the
    same under "complete targets left to the gating", case attempts):

        AssertionError: A, complete, was skipped as a closed window: ['A:
        window closed — skipping'], end_reason 'dawn_cutoff'

    RED under mutant "the tail not counted" (`_accepted_now` handing back
    the map it took first, without the frames banked since), case accepted
    only, observed:

        AssertionError: A, complete, was skipped as a closed window: ['A:
        window closed — skipping'], end_reason 'dawn_cutoff'
    """
    a = single("A", count=1)
    a.steps[0].exposure_s = 600.0
    a.schedule = Schedule(max_run_min=5)
    b = single("B", count=1, ha_h=-1.0)
    plan = _plain_plan(a, b)
    plan.count_mode = count_mode
    plan.instructions = [_rule("run_target", "A", only="A",
                               trigger="on_target_complete")]
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert [c["target"] for c in night.captures] == ["A", "B"], night.shots()
    a_end = night.rel(night.captures[0]["t"]) + 600.0
    assert a_end > 300.0, f"premise: A's window closed under its frame: {a_end}"
    closed = night.said("A: window closed")
    assert not closed and night.engine.state.get("end_reason") == "complete", (
        f"A, complete, was skipped as a closed window: {closed}, end_reason "
        f"{night.engine.state.get('end_reason')!r}")
    assert night.said("A: already complete — skipping"), night.lines[-6:]


@pytest.mark.parametrize("case", ["a single target, run to another",
                                  "a member, run to another"])
async def test_control_a_complete_target_owed_its_rules_is_taken_up(
        group_hub, monkeypatch, case):
    """CONTROL. A consumed jump fires on the target's last owed frame, so
    the target is complete and owed its ``on_target_complete`` rules, kept
    at the front of ``remaining`` (#481). The next selection drops complete
    targets, and not this one: it is taken up, its rule runs once, before
    the jump's destination is shot.

    RED under mutant "the owed exception dropped" (`_drop_complete`
    without its ``if t.id in self._completion_owed: continue``), observed:

        AssertionError: A's on_target_complete rule ran 0 times; the
        complete target owed it was dropped before it ran
    """
    who, action, arg, dest = CASES[case]
    night, _ = await _run(group_hub, monkeypatch,
                          _night_plan(who, action, arg))
    assert night.done, night.lines[-4:]
    ran = night.said(f"{who} is done")
    assert len(ran) == 1, (
        f"{who}'s on_target_complete rule ran {len(ran)} times; the complete "
        f"target owed it was dropped before it ran")
    done_at = next(t for t, _l, m in night.lines if m == f"{who} is done")
    first_dest = next(c["t"] for c in night.captures if c["target"] == dest)
    assert done_at <= first_dest, (
        f"{who}'s rule ran after {dest}, the jump's destination, was shot")
