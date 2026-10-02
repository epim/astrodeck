# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A member's ``after_group`` holds its whole group (#330; spec 1.6 "Wait for
the mosaic", 3.5).

THE DEFECT. Under the flow setting "Wait for the mosaic" the compile writes
``after_group`` on every panel of a mosaic that follows another
(`to_plan`, the "FOLLOWERS WAIT ONLY WHEN THE FLOW SAYS SO" loop), and
`plan_identity_errors` grades the plan as written: its cycle refusal counts a
member's gate as holding the member's whole group. The engine read
``after_group`` for targets in no group only: its one call site was guarded by
``self._group_of(target) is None``. So a downstream mosaic shot straight
through the upstream mosaic's waits, the opposite of what the setting says.

THE READING NOW (spec 1.6, as `plan_identity_errors` gives it). While a group
any member of the downstream group waits for can still shoot tonight, the
downstream group is not eligible: its members are waiters with no wake of
their own, so the scheduler sleeps on the upstream group's wake, never a busy
loop. Once that group is set aside tonight the downstream group is skipped for
the night, not done. Once it is complete the downstream group runs.

THE PLANS. An upstream 2x2 "M31" (the harness's panels) and a downstream 1x2
"M33" after it in plan order, whose panels carry ``after_group`` naming M31,
as the compile writes them. M31's window opens ``OPENS`` into the night, so at
the start every M31 panel waits and only M33 is ready: exactly the moment a
downstream mosaic that ignores its gate is shot. The control case gives M33 no
gate and shows it IS shot then, so the held cases grade the gate and not the
window.

The runs are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py). Each case names the mutant it was shown RED under,
with the failure observed, verbatim (pytest's own lines, long ones wrapped).
Every mutant was applied in a private scratch copy of server/ (scratchpad
s4-engb-mut), never in the shared tree (#254):

* "restore the _group_of(target) is None guard": the selection's gate call
  guarded again by ``self._group_of(target) is None``, as it was.
* "only the gated member waits": `_follower_gate` asks a member's own
  ``after_group`` (as a follower's) instead of its group's gate.
* "a held group's reach is still read": `_eligibility_now` without the early
  ``continue`` for a group its gate holds.
* "a held group's ready time is now": `_group_ready_ts` without its early
  ``return None`` for a group its gate holds.
* "a skipped group leaves member by member": the scheduler's ``leave_tonight``
  loop without the branch that skips a member's whole group, so each skipped
  member is dropped alone with a follower's line.
* "a held member wakes at once": `_follower_gate` returning a held member's
  wait with ``wake_ts`` set to now.
* "the selection goes on after a group skip": the scheduler's ``continue``
  after a group skip deleted, so the waiter and ``all_closed`` it worked out
  before the skip are acted on.
* "a live upstream outranks a set-aside one": `_group_gate` returning the
  first "wait" it meets, and a "skip" only when no upstream waits.
* "the group's gates read from its first member only": `_start_groups`
  gathering a group's ``after_group`` gates from ``mem[:1]`` instead of
  every member.

"The selection goes on after a group skip" and "a live upstream outranks a
set-aside one" were applied by the S4-ENGB verifier in its own scratch copy
(scratchpad s4-engb-verify-mut). The first eight were run again on the
finished S4 code in scratchpad s4-engb-resume-mut, and each failed as
recorded below. The last, which every case but
`test_a_gate_on_the_last_member_holds_its_whole_group` passed, was applied
by a second verifier in scratchpad s4-engb-verify-r2-mut, where that case
was added for it.
"""
from __future__ import annotations

import time as _time

from _group_harness import (GROUP_ID, GROUP_NAME, T0, Night, group_hub,
                            group_store, panel, ra_at, single)
from astrodeck.config import SafetyConfig
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup,
                                       plan_identity_errors)
from astrodeck.sequence.session import session_store

UP_ID, UP_NAME = GROUP_ID, GROUP_NAME
DOWN_ID, DOWN_NAME = "m33-mosaic", "M33"
#: The fake instant M31's window opens: a whole local minute, 9.85 min in, so
#: an "HH:MM" start time names it exactly.
OPENS = T0 + 591.0
LATER = "Later"


#: M31's layout angle when the set-aside cases rotate it (below).
UP_PA = 30.0


def _up_panels(*, opens: bool = True, rotating: bool = False) -> list[Target]:
    """The upstream 2x2, two L frames a panel, one a visit, its window opening
    at ``OPENS`` unless ``opens`` is False; at ``UP_PA`` when ``rotating``."""
    kw = {}
    if opens:
        hhmm = _time.strftime("%H:%M", _time.localtime(OPENS))
        kw["schedule_kw"] = {"start_mode": "time", "start_time": hhmm}
    if rotating:
        kw["rotation_deg"] = UP_PA
    return [panel(r, c, filters=("L",), count=2, **kw)
            for r in range(2) for c in range(2)]


def _up_group(*, rotating: bool = False) -> TargetGroup:
    """M31's group, rotating at ``UP_PA`` when ``rotating``."""
    kw = {"rotate": True, "pa_deg": UP_PA} if rotating else {}
    return TargetGroup(id=UP_ID, name=UP_NAME,
                       geometry={"rows": 2, "cols": 2}, **kw)


def _up_never_turns(who, n, result):
    """How the set-aside cases set M31 aside tonight: every M31 hop centres
    and its rotator does not turn the camera to the mosaic's angle
    (``rotation_skipped``), a ``rotation`` deferral counted against the panel
    at once (5.6 step 4), so each panel is set aside at its third pass, at
    600 s, for the night. Every other hop centres.

    RE-PINNED FOR H4 (#534, H4 orchestrator ruling 2). These cases used to
    have no M31 panel ever CENTRE. Since H4 a pass in which every panel tried
    missed its centring holds the group with no strikes, for as long as the
    window lasts, so M31 was never set aside, M33 waited for it all night
    (#563) and each case failed at the 16 h fake horizon ("centring failed
    on every one of the 4 panels tried in pass 96"). A rotator that did not
    turn sets M31 aside exactly as the misses did before H4, pass for pass
    and second for second, which is the event these cases are about."""
    if who.startswith(UP_NAME):
        return dict(result, rotation_skipped=True)
    return result


def _down_panel(col: int, *, after: str | None) -> Target:
    """Downstream panel 1-(col+1): west of M31 and never behind a limit, two
    L frames, one a visit, waiting for ``after`` (None: no gate)."""
    return Target(id=f"q0{col}", name=f"{DOWN_NAME} 1-{col + 1}",
                  ra_hours=ra_at(-2.6 + 0.03 * col), dec_deg=33.0,
                  center=True, autofocus_first=False, acquisition="cycle",
                  mosaic_group=DOWN_ID, panel_row=0, panel_col=col,
                  after_group=after,
                  steps=[ExposureStep(id=f"q0{col}-L", filter="L",
                                      exposure_s=30.0, count=2, per_visit=1)])


def _plan(*, gates=(UP_ID, UP_ID), opens: bool = True, after=(),
          rotating: bool = False) -> SequencePlan:
    """M31 then M33, M33's two panels waiting for the groups in ``gates``
    (None: that panel has no gate), then ``after``'s plain targets. M31
    rotates at ``UP_PA`` when ``rotating``."""
    groups = [_up_group(rotating=rotating),
              TargetGroup(id=DOWN_ID, name=DOWN_NAME,
                          geometry={"rows": 1, "cols": 2})]
    targets = [*_up_panels(opens=opens, rotating=rotating),
               *[_down_panel(c, after=g) for c, g in enumerate(gates)],
               *after]
    plan = SequencePlan(name="two mosaics", targets=targets, groups=groups,
                        guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False, recover_guiding=False)
    assert plan_identity_errors(plan) == [], plan_identity_errors(plan)
    return plan


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0,
                 **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _ends(night: Night, prefix: str) -> list[float]:
    return [c["t"] + c["exposure_s"] for c in night.captures
            if c["target"].startswith(prefix)]


def _starts(night: Night, prefix: str) -> list[float]:
    return [c["t"] for c in night.captures if c["target"].startswith(prefix)]


def _last_up(night: Night) -> int:
    """The index of M31's last frame among every frame of the night. On the
    fake clock a hop takes no time, so the next frame can start at the very
    instant M31's last one ends: what came first is read from the ORDER."""
    return max(i for i, c in enumerate(night.captures)
               if c["target"].startswith(UP_NAME))


def _down_before_up_done(night: Night) -> list[str]:
    """Every M33 frame shot before M31's last, in order."""
    last = _last_up(night)
    return [c["target"] for c in night.captures[:last]
            if c["target"].startswith(DOWN_NAME)]


# ------------------------------------------------------------ the control

async def test_without_the_gate_the_downstream_mosaic_fills_the_wait(
        group_hub, monkeypatch):
    """CONTROL, the premise of every held case: M33 with NO gate is shot at
    the start, while every M31 panel waits for its window, because it is the
    first ready member. So a held case where M33 waits is graded on the gate,
    not on the window or the order."""
    night = await _night(group_hub, monkeypatch, _plan(gates=(None, None)))
    assert night.done, night.lines[-4:]
    down = _starts(night, DOWN_NAME)
    assert down and min(down) < OPENS, (
        f"premise: an ungated M33 is shot while M31 waits: "
        f"{[(round(t - T0), w) for t, w in night.gotos]}")
    assert night.stored.status == "complete", night.stored.status


# --------------------------------------------------------- held, then run

async def test_a_downstream_mosaic_waits_for_the_upstream_one_then_runs(
        group_hub, monkeypatch):
    """"Wait for the mosaic", both M33 panels gated: no M33 panel is slewed
    to or shot while M31 has a live panel, through the ten minutes every M31
    panel waits for its window, when M33 is the only ready member. The wait
    is said once, for the mosaic. Once M31 is complete M33 runs its course,
    and the night completes.

    NEVER A BUSY LOOP. A held member is a waiter with no wake of its own, so
    the scheduler's wake is M31's opening: ONE selection before it, then one
    sleep, where a member that woke "now" would be asked again on every
    ``SCHEDULE_WAIT_STEP_S`` tick of the ten minutes. Selections are counted
    by wrapping `_eligibility_now` (a pass-through spy).

    MUTANT "restore the _group_of(target) is None guard": RED, the selections
    of the M33 visits it lets into the wait (observed):
        AssertionError: 6 selections while M33 was held and M31 waited for its
        window
        assert 6 == 1
         +  where 6 = len([1788313689.0, 1788313719.0, 1788313749.0,
        1788313749.0, 1788313779.0, 1788313809.0])
    and, before the selection count was added, at the next assertion
    (observed):
        AssertionError: M33 was shot 4 times before M31's last frame: ['M33
        1-1', 'M33 1-2', 'M33 1-1', 'M33 1-2']
        assert not ['M33 1-1', 'M33 1-2', 'M33 1-1', 'M33 1-2']
    MUTANT "a held member wakes at once" (`_follower_gate` handing a held
    member's wait a wake time of now): RED (observed):
        AssertionError: 119 selections while M33 was held and M31 waited for
        its window
        assert 119 == 1
         +  where 119 = len([1788313689.0, 1788313694.0, 1788313699.0,
        1788313704.0, 1788313709.0, 1788313714.0, ...])
    """
    night = Night(group_hub, monkeypatch)
    selections: list[float] = []
    real = night.engine._eligibility_now

    async def eligibility_now(*a, **kw):
        selections.append(night.clock.t)
        return await real(*a, **kw)

    monkeypatch.setattr(night.engine, "_eligibility_now", eligibility_now)
    try:
        night.done = await night.run(_plan(), wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    waiting = [t for t in selections if t < OPENS]
    assert len(waiting) == 1, (
        f"{len(waiting)} selections while M33 was held and M31 waited for "
        f"its window")
    assert len(_ends(night, UP_NAME)) == 8, night.shots()
    assert len(_starts(night, DOWN_NAME)) == 4, night.shots()
    early = _down_before_up_done(night)
    assert not early, (
        f"M33 was shot {len(early)} times before M31's last frame: {early}")
    first_down_hop = min(k for k, e in enumerate(night.trace)
                         if e[1] == "goto" and e[2].startswith(DOWN_NAME))
    last_up_frame = max(k for k, e in enumerate(night.trace)
                        if e[1] == "capture" and e[2].startswith(UP_NAME))
    assert first_down_hop > last_up_frame, "M33 was slewed to before M31 was done"
    assert night.said("M33: waits: after the M31 mosaic") == [
        "M33: waits: after the M31 mosaic"], night.said("waits:")
    assert night.stored.status == "complete", night.stored.status


async def test_one_gated_member_holds_its_whole_group(group_hub, monkeypatch):
    """Only M33 1-1 carries the gate; 1-2 carries none. A member's gate holds
    its WHOLE group (spec 1.6; `plan_identity_errors` counts it so), so 1-2
    is not shot before M31 is done either: the mosaic is done only when every
    panel is.

    MUTANT "only the gated member waits": RED (observed):
        AssertionError: ['M33 1-2', 'M33 1-2']
        assert not ['M33 1-2', 'M33 1-2']
    MUTANT "restore the _group_of(target) is None guard": RED (observed):
        AssertionError: ['M33 1-1', 'M33 1-2', 'M33 1-1', 'M33 1-2']
        assert not ['M33 1-1', 'M33 1-2', 'M33 1-1', 'M33 1-2']
    """
    night = await _night(group_hub, monkeypatch, _plan(gates=(UP_ID, None)))
    assert night.done, night.lines[-4:]
    early = _down_before_up_done(night)
    assert not early, early
    assert len(_starts(night, DOWN_NAME)) == 4, night.shots()
    assert night.stored.status == "complete", night.stored.status


async def test_a_gate_on_the_last_member_holds_its_whole_group(group_hub,
                                                               monkeypatch):
    """The case above with the gate moved to M33 1-2, the LAST member in
    plan order; 1-1, the first, carries none. `_start_groups` gathers what
    a group waits for from EVERY member, so the group is held all the same,
    and the wait is said once, for the mosaic. The case above alone cannot
    tell every member from the first one (#396).

    MUTANT "the group's gates read from its first member only" (the
    ``waits`` list in `_start_groups` built from ``mem[:1]``): RED
    (observed):
        AssertionError: ['M33 1-1', 'M33 1-2', 'M33 1-1', 'M33 1-2']
        assert not ['M33 1-1', 'M33 1-2', 'M33 1-1', 'M33 1-2']
    MUTANT "only the gated member waits": RED here too, on the ungated
    first member (observed):
        AssertionError: ['M33 1-1', 'M33 1-1']
        assert not ['M33 1-1', 'M33 1-1']
    """
    night = await _night(group_hub, monkeypatch, _plan(gates=(None, UP_ID)))
    assert night.done, night.lines[-4:]
    early = _down_before_up_done(night)
    assert not early, early
    assert len(_starts(night, DOWN_NAME)) == 4, night.shots()
    assert night.said("M33: waits: after the M31 mosaic") == [
        "M33: waits: after the M31 mosaic"], night.said("waits:")
    assert night.stored.status == "complete", night.stored.status


# -------------------------------------------------- skipped, not done

async def test_a_downstream_mosaic_is_skipped_once_the_upstream_is_set_aside(
        group_hub, monkeypatch):
    """No M31 panel ever centres: each is deferred on three consecutive
    passes and set aside tonight, so M31 cannot be done tonight. M33 is then
    SKIPPED FOR THE NIGHT, NOT DONE: never slewed to, said once for the
    mosaic, marked gone from its group with the reason, not recorded for the
    night (M31's records already say it), still owed, and the session stays
    dormant for the next night. Nothing closed a window on it.

    M31's window is open from the start here, so M33 is held through M31's
    two deferral waits, when every M31 panel waits and M33 alone is ready.

    MUTANT "restore the _group_of(target) is None guard": RED (observed):
        AssertionError: [(1788313689.0, 'M31 1-1'), (1788313689.0, 'M31 1-2'),
        (1788313689.0, 'M31 2-2'), (1788313689.0, 'M31 2-1'), (1788313689.0,
        'M33 1-1'), (1788313719.0, 'M33 1-2'), ...]
        assert False
         +  where False = all(<generator object test_a_downstream_mosaic_is_
        skipped_once_the_upstream_is_set_aside.<locals>.<genexpr> at
        0x000001ED7F268AC0>)
    MUTANT "a skipped group leaves member by member": RED (observed; each
    member leaves alone, on a follower's line):
        AssertionError: ['M33 1-1: skipped for tonight: the M31 mosaic it
        waits for is set aside tonight; not done, so the next night takes
        it...3 1-2: skipped for tonight: the M31 mosaic it waits for is set
        aside tonight; not done, so the next night takes it up']
        assert ['M33 1-1: sk... takes it up'] == ['M33: skippe... takes it up']
          At index 0 diff: 'M33 1-1: skipped for tonight: the M31 mosaic it
        waits for is set aside tonight; not done, so the next night takes it
        up' != 'M33: skipped for tonight: the M31 mosaic it waits for is set
        aside tonight; not done, so the next night takes it up'
          Left contains one more item: 'M33 1-2: skipped for tonight: the M31
        mosaic it waits for is set aside tonight; not done, so the next night
        takes it up'
    MUTANT "a held member wakes at once": RED, the held member became the
    wait the run published, so M33 read as the active group (observed):
        AssertionError: M33 was never the active group
        assert [{'id': 'm33-...3', ...}, ...] == []
          Left contains 120 more items, first extra item: {'id':
        'm33-mosaic', 'meridian_wait': False, 'mode': 'rotate', 'name':
        'M33', ...}

    RE-PINNED FOR H4: M31 is set aside by a rotator that never turns, no
    longer by centring misses (`_up_never_turns` says why). The three
    mutants above were run again by the H4 integration against it, each RED
    with the words recorded above.
    """
    night = await _night(group_hub, monkeypatch,
                         _plan(opens=False, rotating=True),
                         goto=_up_never_turns)
    assert night.done, night.lines[-4:]
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p00", "p01", "p10", "p11"], night.stored.set_aside
    assert all(not who.startswith(DOWN_NAME) for _t, who in night.gotos), (
        night.gotos)
    assert night.captures == [], night.captures[:2]
    skipped = night.said("skipped for tonight")
    assert skipped == [
        "M33: skipped for tonight: the M31 mosaic it waits for is set aside "
        "tonight; not done, so the next night takes it up"], skipped
    published = [s["group"] for s in night.states
                 if isinstance(s.get("group"), dict)
                 and s["group"].get("id") == DOWN_ID]
    assert published == [], "M33 was never the active group"
    run = night.engine._group_runs[DOWN_ID]
    assert run.set_aside == {
        pid: "the M31 mosaic it waits for is set aside tonight"
        for pid in ("q00", "q01")}, run.set_aside
    owed = night.stored.remaining()
    assert owed["q00-L"] == 2 and owed["q01-L"] == 2, owed
    assert night.stored.status == "dormant", night.stored.status
    assert night.engine._dawn_cutoff is False


def _hhmm(t: float) -> str:
    return _time.strftime("%H:%M", _time.localtime(t))


async def _skip_night(hub, monkeypatch, *, late_window: bool
                      ) -> tuple[Night, float, list[tuple]]:
    """M31, whose rotator never turns (`_up_never_turns`, re-pinned for H4
    from "never centring"), is set aside at 600 s; M33 waits for it; "Later"
    waits for it too, and its window closes at 471 s (``stop_time``), so once
    M33 leaves nothing is left that can be shot tonight. With
    ``late_window``, M33 1-1's own window opens about two hours in (1-2's is
    open from the start). Returns the night, the fake time of the one M33
    skip line, and every published state as ``(time from the start, state,
    detail, active group's name)``."""
    later = Target(id="later", name=LATER, ra_hours=ra_at(-2.5),
                   dec_deg=35.0, center=True, autofocus_first=False,
                   after_group=UP_ID,
                   schedule=Schedule(stop_mode="time",
                                     stop_time=_hhmm(OPENS - 120.0)),
                   steps=[ExposureStep(id="later-L", filter="L",
                                       exposure_s=30.0, count=1)])
    plan = _plan(opens=False, after=[later], rotating=True)
    if late_window:
        q00 = next(t for t in plan.targets if t.id == "q00")
        q00.schedule = Schedule(start_mode="time",
                                start_time=_hhmm(OPENS + 6600.0))
        assert plan_identity_errors(plan) == [], plan_identity_errors(plan)

    night = await _night(hub, monkeypatch, plan, goto=_up_never_turns)
    assert night.done, night.lines[-4:]
    skip = [t for t, _lv, m in night.lines
            if m.startswith("M33: skipped for tonight")]
    assert len(skip) == 1, night.said("skipped")
    published = []
    for e in night.trace:
        if e[1] != "state":
            continue
        g = e[2].get("group")
        published.append((e[0], e[2].get("state"), e[2].get("detail"),
                          g.get("name") if isinstance(g, dict) else None))
    assert night.captures == [], night.captures[:2]
    return night, skip[0], published


async def test_a_skipped_group_is_not_then_waited_for(group_hub, monkeypatch):
    """A group skip ends the wait for its panels at once. M33 1-2 is ready
    from the start, so the skip is decided at 600 s, when M31 is set aside;
    M33 1-1's window opens about two hours in, so at that selection 1-1 was
    still a WAITER, the soonest one. Once M33 leaves, the selection is asked
    again over what is left, and the night ends at the skip.

    Without that, the scheduler acted on the waiter it had worked out before
    the skip dropped it: it published "waiting for M33 1-1" with M33, just
    skipped for the night, as the active group, stopped tracking for a long
    wait, and slept until 1-1's window opened, only to end the night there
    as a dawn cutoff.

    MUTANT "the selection goes on after a group skip": RED (observed):
        AssertionError: the night ended 6591 s after M33 was skipped:
        [(600.0, 'running', 'waiting for M33 1-1', 'M33'), (7191.0,
        'complete', 'stopped at dawn (windows closed)', None)]
        assert 6591.0 == 0.0

    NO LONGER RED, found by the H4 integration: re-run against the rotation
    deferral (`_up_never_turns`) in a private copy of server/ (scratchpad
    H4-INTEG-mut), the mutant leaves this case and its control green (2
    passed). The engine's own comment on that ``continue`` says why: since
    #374 a WAITING member meets the gate as well and leaves with its group,
    so no panel of the skipped group can be the stale waiter any more, and
    the re-ask is belt and braces. The change is #374's (S5), not H4's:
    the same mutant on HEAD's tree before H4 (``git archive HEAD``, scratchpad
    H4-INTEG-head), with this case as it read then, also gave 2 passed.
    Filed as #572. The case still grades that the night ends at the skip.
    """
    night, skip_t, published = await _skip_night(group_hub, monkeypatch,
                                                 late_window=True)
    gap = published[-1][0] - night.rel(skip_t)
    assert gap == 0.0, (
        f"the night ended {gap:.0f} s after M33 was skipped: {published[-2:]}")
    shown = [p for p in published if p[3] == DOWN_NAME]
    assert shown == [], f"M33 was published as the active group: {shown[:2]}"


async def test_a_skip_with_no_waiting_panel_ends_the_night_either_way(
        group_hub, monkeypatch):
    """CONTROL for the case above: the same night with M33 1-1's window open
    from the start, so no M33 panel is a waiter when the skip comes. The
    night ends at the skip with the mutant "the selection goes on after a
    group skip" applied as without it (GREEN under the mutant, observed), so
    the case above grades the waiter a skip leaves behind, not the skip."""
    night, skip_t, published = await _skip_night(group_hub, monkeypatch,
                                                 late_window=False)
    assert published[-1][0] == night.rel(skip_t), published[-2:]
    assert not [p for p in published if p[3] == DOWN_NAME], published


async def test_one_upstream_set_aside_skips_a_group_another_still_holds(
        group_hub, monkeypatch):
    """M33's two panels wait for different mosaics: 1-1 for M31, 1-2 for
    M51, whose window opens about twenty minutes after M31's set-aside. When
    M31 is set aside at 600 s, M33 can no longer be let go tonight, whatever
    M51 does, so it is skipped then: a set-aside upstream outranks a live one
    (`_group_gate`). M51 then shoots its panels when its window opens.

    MUTANT "a live upstream outranks a set-aside one" (`_group_gate` returning
    the first "wait" it meets, and a "skip" only when no upstream waits): RED,
    M33 held until M51 was done and only then skipped (observed):
        AssertionError: M33 was skipped at 1851 s, after M51's frames
        assert 1851.0 == 600.0

    RE-PINNED FOR H4: M31 is set aside by a rotator that never turns
    (`_up_never_turns`), and its set-aside time is read off its own
    deferral lines. The mutant, re-run by the H4 integration against it, is
    RED with the words recorded above.
    """
    m51_opens = OPENS + 1200.0
    m51 = [Target(id=f"r0{c}", name=f"M51 1-{c + 1}",
                  ra_hours=ra_at(-1.8 + 0.03 * c), dec_deg=47.0, center=True,
                  autofocus_first=False, acquisition="cycle",
                  mosaic_group="m51-mosaic", panel_row=0, panel_col=c,
                  schedule=Schedule(start_mode="time",
                                    start_time=_hhmm(m51_opens)),
                  steps=[ExposureStep(id=f"r0{c}-L", filter="L",
                                      exposure_s=30.0, count=1,
                                      per_visit=1)])
           for c in range(2)]
    groups = [_up_group(rotating=True),
              TargetGroup(id="m51-mosaic", name="M51",
                          geometry={"rows": 1, "cols": 2}),
              TargetGroup(id=DOWN_ID, name=DOWN_NAME,
                          geometry={"rows": 1, "cols": 2})]
    plan = SequencePlan(name="three mosaics", groups=groups,
                        targets=[*_up_panels(opens=False, rotating=True),
                                 *m51,
                                 _down_panel(0, after=UP_ID),
                                 _down_panel(1, after="m51-mosaic")],
                        guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False, recover_guiding=False)
    assert plan_identity_errors(plan) == [], plan_identity_errors(plan)

    night = await _night(group_hub, monkeypatch, plan, goto=_up_never_turns)
    assert night.done, night.lines[-4:]
    skip = [(night.rel(t), m) for t, _lv, m in night.lines
            if m.startswith("M33: skipped for tonight")]
    assert [m for _t, m in skip] == [
        "M33: skipped for tonight: the M31 mosaic it waits for is set aside "
        "tonight; not done, so the next night takes it up"], skip
    # M31's own deferral lines (`_up_never_turns`, re-pinned for H4 from
    # "M31: centring failed"); the last is the set-aside.
    set_aside = max(night.rel(t) for t, _lv, m in night.lines
                    if m.startswith("M31: the rotator did not turn"))
    m51_frames = _starts(night, "M51")
    assert len(m51_frames) == 2 and min(m51_frames) >= m51_opens - 1.0, (
        f"premise: M51 shot both panels once its window opened: "
        f"{night.shots()}")
    assert skip[0][0] == set_aside, (
        f"M33 was skipped at {skip[0][0]:.0f} s, after M51's frames"
        if skip[0][0] > night.rel(min(m51_frames)) else skip)
    assert not _starts(night, DOWN_NAME), night.shots()


# ------------------------------------------- what a held group costs nothing

async def test_a_held_group_makes_no_pier_reads(group_hub, group_store,
                                                monkeypatch):
    """While M33 is held, `_eligibility_now` asks it nothing: with the pier
    guard armed, the mount's destination side is read for M31's panels at
    every selection and never for M33's until M31 is done. Each such read is
    a bounded mount call a wedged link would stall (#314), and its verdict
    would be about a group that cannot be shot yet.

    The mount's destination-side read is wrapped to record which RA it was
    asked about and then answers as the simulator does.

    MUTANT "a held group's reach is still read": RED (observed):
        AssertionError: 20 destination reads for M33 before M31 was done
        assert 20 == 0
         +  where 20 = len([0, 0, 0, 0, 1, 1, ...])
    MUTANT "restore the _group_of(target) is None guard" is RED here too,
    by the reads of the M33 visits it lets through (observed):
        AssertionError: 4 destination reads for M33 before M31 was done
        assert 4 == 0
         +  where 4 = len([0, 1, 2, 3])
    """
    group_store.set_safety(SafetyConfig(enabled=False,
                                        enforce_pier_limits=True))
    tel = group_hub.devices["telescope"]
    assert tel.reports_destination_pier_side
    real = tel.destination_pier_side
    #: (frames shot so far, RA asked about) for every destination read.
    asked: list[tuple[int, float]] = []
    night_ref: list[Night] = []

    async def destination_pier_side(ra_hours, dec_deg):
        asked.append((len(night_ref[0].captures), round(float(ra_hours), 6)))
        return await real(ra_hours, dec_deg)

    monkeypatch.setattr(tel, "destination_pier_side", destination_pier_side)
    plan = _plan()
    down_ra = {round(t.ra_hours, 6) for t in plan.targets
               if t.mosaic_group == DOWN_ID}
    night = Night(group_hub, monkeypatch)
    night_ref.append(night)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.lines[-4:]
    # A read made before M31's last frame was taken is a read while M31 had
    # a live panel.
    last = _last_up(night)
    up_reads = [n for n, ra in asked if ra not in down_ra and n <= last]
    down_early = [n for n, ra in asked if ra in down_ra and n <= last]
    assert up_reads, "premise: the pier guard read M31's destinations"
    assert len(down_early) == 0, (
        f"{len(down_early)} destination reads for M33 before M31 was done")
    assert any(ra in down_ra for _t, ra in asked), (
        "premise: M33's destinations are read once it is let go")


async def test_a_follower_after_a_held_mosaic_fills_the_upstream_wait(
        group_hub, monkeypatch):
    """A plain target after both mosaics in plan order and carrying no gate
    (a branched lane, or a hand-made plan) is a FOLLOWER under the default
    rule: while every live panel waits it runs a visit bounded by the next
    panel due (spec 1.6). M31 waits ten minutes for its window and M33 is
    held, so the follower fills those ten minutes, bounded by M31's opening.
    A held group gives `_group_ready_ts` no time. Read as "now" (its members
    carry no reachability verdict while held, so each reads as eligible at
    once), it left the follower no room for a visit before "the next panel",
    and the follower waited through the whole wait instead of filling it.

    MUTANT "a held group's ready time is now": RED (observed):
        AssertionError: the follower shot nothing while M31 waited: [(0, 'M31
        1-1'), (30, 'M31 1-2'), (60, 'M31 2-2'), (90, 'M31 2-1')]
        assert []
    """
    plan = _plan(after=[single(LATER, count=30, ha_h=-2.0)])
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    first_up = min(_starts(night, UP_NAME))
    before = [e for e in _ends(night, LATER) if e <= first_up]
    assert before, (
        f"the follower shot nothing while M31 waited: "
        f"{[(round(t - first_up), w) for t, w in night.gotos][:4]}")
    assert first_up >= OPENS - 1.0, (first_up - T0)
    assert max(before) <= OPENS + 1.0, (max(before) - OPENS)
    assert not _down_before_up_done(night), night.shots()
    assert night.said("M33: waits: after the M31 mosaic")
    assert night.stored.status == "complete", night.stored.status
