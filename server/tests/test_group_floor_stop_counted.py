# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A visit that ends below the panel's own floor still counts toward its pass
(#288 part 1; spec 5.1 outcome table and pass boundary, 5.6 step 7).

THE DEFECT. `_visit_panel`'s ``FloorStop`` arm set the panel aside and
returned without telling `GroupRun`, so the exposures that visit took before
the floor stopped it never reached ``exposures_this_pass``, and a guider
start that worked on it never reached the guide-start pass rule. A pass
whose only exposures came from such a visit read as a pass of none: a
``DEFER_WAIT_S`` wait when the other visits deferred, the group anti-spin
(every live member set aside tonight) when they took nothing; and a pass
where the other panels' guiders failed could read as the guider's fault
with the one start that worked left out.

THE FIX. The arm hands the visit to `GroupRun.note_visit` (its exposures,
its accepted frames and its guider start, counted as any visit's are, by
the one bookkeeping `GroupRun.visit_outcome` uses) before it sets the panel
aside.

The engine cases run the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py); the floor is the frame loop's own
`_enforce_altitude_floor`, fed the altitude the case names through
``_frame_altitude``, as test_group_rotation's floor case feeds it. The
anti-spin arm is pinned on `GroupRun` alone: in the engine a visit that
shoots nothing always either defers or sets its panel aside, so a pass of
no exposures and no deferrals with a floor-stopped visit in it cannot be
staged on the simulator.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/ (scratchpad s3ea-mut), never in
the shared tree (#254).
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from _group_harness import GROUP_NAME, Night, grid_plan, group_hub, group_store
from astrodeck.sequence.group_rules import (CENTRING_HOLD_RETRY_S, DEFER_WAIT_S,
                                            GUIDE_START, GroupRun, PanelDeferred)
from astrodeck.sequence.models import Schedule
from astrodeck.sequence.session import session_store


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _gotos(night, label: str) -> list[float]:
    return [night.rel(t) for t, who in night.gotos if who == _name(label)]


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


def _floor_after(night, monkeypatch, label: str, frames: int) -> None:
    """``label`` carries ``on_floor = advance`` (set by the caller) and reads
    10 degrees to the frame loop's floor check once it has shot ``frames``
    frames tonight, and its true altitude before that."""
    real = engine_mod._frame_altitude

    def altitude(target, site, when):
        if (target.name == _name(label)
                and len(_shot(night, label)) >= frames):
            return 10.0
        return real(target, site, when)

    monkeypatch.setattr(engine_mod, "_frame_altitude", altitude)


def _own_floor(plan, label: str) -> None:
    t = next(t for t in plan.targets if t.name == _name(label))
    t.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")


# ------------------------------------------------------------ the engine

async def test_a_pass_whose_only_exposure_was_floor_stopped_does_not_wait(
        group_hub, monkeypatch):
    """A 1x2. 1-1 never centres, so every visit to it defers (3 in a row
    sets it aside). 1-2 shoots its L, and at the frame boundary before its
    R it is below its own floor: set aside tonight, one exposure into the
    visit. That exposure is pass 1's only one, and it is an exposure all
    the same: pass 1 ends as a pass that shot, and pass 2 begins at once,
    with no ``DEFER_WAIT_S`` and no anti-spin set-aside. Pass 2 is 1-1's
    deferral alone and does wait; pass 3 sets 1-1 aside.

    RE-PINNED FOR WP-33 under backlog ruling D-03 (owner-approved
    2026-09-30, #591). 1-1's pass 1 miss is still its own (1-2 centred
    beside it that pass, so pass 1's floor is the ordinary two-tried
    floor), and still counts at the pass boundary, unchanged. But from
    pass 2 on 1-2 is already set aside (its own floor), so 1-1 is the
    GROUP'S ONLY LIVE PANEL, and D-03 widened ``centring_pass_verdict``
    to call that case the sky's too: every live member tried and missed,
    down to the last one. So 1-1's solo misses are no longer counted
    against ITS OWN three-strike floor at all; they HOLD the group
    (``CENTRING_HOLD_RETRY_S`` = 600 s, not ``DEFER_WAIT_S`` = 300 s)
    pass after pass, and D-03's own escalation -- the one this case now
    exists to show reaches a single-panel mosaic -- takes over: an alert
    at ``HELD_PASS_ALERT_AT`` (3) consecutive held passes and the group
    set aside at ``HELD_PASS_SET_ASIDE_AT`` (6), with ONE group-kind
    record, never a per-panel ``centring`` one (which only the old,
    bypassed, three-strike path ever wrote), so there is no expiry either.
    1-1 is hopped seven times: the one tried pass and the six held passes
    that follow it. What this case grades, that pass 1 shot and pass 2
    did not wait ``DEFER_WAIT_S`` for nothing, is unchanged; it waits
    ``CENTRING_HOLD_RETRY_S`` instead, for a different, D-03, reason. RED
    under the same mutant, re-run by the WP-33 integration in a private
    copy of server/ (scratchpad H4-INTEG-mut), pass 1 again waiting for
    nothing when it should have shot (observed):
        AssertionError: [('p01', 'floor', False)]
        assert [('p01', 'floor', False)] == [('p01', 'floor', False),
        ('p00', 'group', False)]

    MUTANT "no note_visit" (the ``run.note_visit(...)`` call deleted from
    `_visit_panel`'s FloorStop arm): RED, pass 1 reads as a pass of no
    exposures and 1-2's floor stop is dropped from the ledger, so the
    group's anti-spin set-aside fires on pass 1 instead of 1-2 ever
    shooting (observed):
        AssertionError: ['p01'] != ['p01', 'floor', False), ('p00',
        'group', False)]
    The same mutant turns the guider case below RED as well, with the
    failure recorded there, and "note_visit counts nothing" in `GroupRun`
    turns both RED with the same lines (observed).
    """
    plan = grid_plan(rows=1, cols=2)
    _own_floor(plan, "1-2")

    def goto(who, n, result):
        if who == _name("1-1"):
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = Night(group_hub, monkeypatch, goto=goto)
    _floor_after(night, monkeypatch, "1-2", frames=1)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    assert _shot(night, "1-2") == ["L"], night.shots()
    assert _shot(night, "1-1") == [], night.shots()
    # 1-2's floor, then 1-1 alone holds the group under D-03 until the
    # escalation sets the whole (one-panel) group aside; never a per-panel
    # "centring" record, since the three-strike path is never reached once
    # 1-1 is the group's last live panel.
    assert [(r["target_id"], r["kind"], bool(r.get("expired")))
            for r in night.stored.set_aside] == [
        ("p01", "floor", False), ("p00", "deferred", False)], night.stored.set_aside
    # DELIBERATE PIN CHANGE (backlog WP-131, #180 part A, wave 17): this
    # record was kind "group". The held-pass rule's set-aside of the mosaic's
    # LAST LIVE PANEL is the panel's own, kind "deferred" (it does not
    # expire, so nothing else moved): "group" is the word for the whole
    # mosaic going quiet, and the Campaign names a panel set aside night
    # after night by its kind.

    holds = night.said("holding the mosaic 10 minutes before the next pass")
    assert holds == [
        f"M31: centring failed on every one of the 1 panels tried in pass "
        f"{n}: the sky or the geometry is to blame, not a panel, so no "
        f"panel's failure count moved; holding the mosaic 10 minutes "
        f"before the next pass"
        for n in (2, 3, 4, 5, 6)], holds
    assert night.said("held for 3 passes in a row"), night.lines
    hops = _gotos(night, "1-1")
    assert len(hops) == 7, hops
    assert hops[1] - hops[0] < DEFER_WAIT_S, (
        f"1-1's hops at {hops}: pass 2 began at once")
    assert all(b - a == CENTRING_HOLD_RETRY_S for a, b in zip(hops[1:], hops[2:])), (
        f"1-1's hops at {hops}: every held pass after pass 2 waits "
        f"CENTRING_HOLD_RETRY_S, D-03's hold, not DEFER_WAIT_S")


async def test_a_floor_stopped_panels_guider_start_counts_for_its_pass(
        group_hub, monkeypatch):
    """A 1x3 whose plan guides. The guider will not start on 1-1 or 1-2 and
    starts on 1-3, which is then below its own floor at its first frame
    boundary and set aside. Three panels were tried in pass 1 and one start
    worked, so the failures are the panels', not the guider's: each is
    counted, and pass 1 ends as a deferral wait. The guide-start pass rule
    (the guider's fault, and the plan's ``guiding_action``) only comes on
    pass 2, when 1-3 is gone and the two left both fail.

    MUTANT "the floor stop's guider start dropped" (`_visit_panel`'s
    ``note_visit`` given no ``guide_started``): RED, pass 1 reads as two
    attempts and two failures, the guider's fault (observed):
        AssertionError: pass 1 was judged the guider's fault: ["M31: guiding
        did not start on any of the 2 panels tried this pass: the guider's
        fault, not a panel's; no panel's centring miss was counted in the
        same pass, and the plan's guiding_action decides"]
        assert ([])
    and the same under "note_visit drops the guide start" in `GroupRun`
    (observed).
    """
    plan = grid_plan(rows=1, cols=3, guide=True)
    _own_floor(plan, "1-3")
    starts = {_name("1-1"): False, _name("1-2"): False, _name("1-3"): True}
    night = Night(group_hub, monkeypatch,
                  guide=lambda who, n: starts.get(who, True))
    _floor_after(night, monkeypatch, "1-3", frames=0)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.lines[-4:]
    assert _shot(night, "1-3") == [], night.shots()
    first_pass = [(t, m) for t, _lvl, m in night.lines
                  if "pass 1 took no exposures and deferred 2 visits" in m]
    fault = [(t, m) for t, _lvl, m in night.lines if "the guider's fault" in m]
    assert first_pass and all(t > first_pass[0][0] for t, _m in fault), (
        f"pass 1 was judged the guider's fault: {[m for _t, m in fault]}")
    assert night.said("guiding did not start on 1-1: no guide star found "
                      "(attempt 1); retried on the next pass"), night.lines[:12]


# ------------------------------------------------------------ GroupRun alone

def _defer(kind: str) -> PanelDeferred:
    return PanelDeferred("guiding did not start", kind=kind,
                         last_error="no guide star found")


def test_note_visit_keeps_a_pass_with_exposures_from_the_anti_spin():
    """`GroupRun` alone: in one pass 1-1 is visited and takes nothing, with
    no deferral, and 1-2 shoots two frames before the engine stops it at
    its floor and sets it aside. The pass shot, so its boundary is the next
    pass and 1-1 stays live; without the noted exposures it is the group
    anti-spin, which sets 1-1 aside for the night. The control: the same
    pass with the floor-stopped visit shooting nothing IS a pass of none.

    MUTANT "note_visit counts nothing" (`GroupRun.note_visit` a no-op):
    RED (observed):
        AssertionError: assert ('set_aside_all', ['a']) == ('next_pass', [])
          At index 0 diff: 'set_aside_all' != 'next_pass'
    """
    def one_pass(floor_exposures: int):
        run = GroupRun({"a": "1-1", "b": "1-2"}, max_failed_visits=3)
        run.visit_outcome("a", complete=False, exposures=0, accepted=0)
        run.note_visit("b", exposures=floor_exposures,
                       accepted=min(1, floor_exposures))
        run.set_aside_panel("b", "1-2 sank below its own altitude floor")
        end = run.close_pass()
        return end.boundary, [p for p, _r in end.set_aside]

    assert one_pass(2) == ("next_pass", [])
    assert one_pass(0) == ("set_aside_all", ["a"])


def test_note_visit_counts_the_guider_start_that_worked():
    """`GroupRun` alone: 1-1 and 1-2 fail to start guiding and 1-3's start
    worked before its floor stopped it. Three attempts, two failures: the
    panels' fault, each counted once. The control: with no start noted it
    is two of two, the guider's fault, and no panel's count moves.

    MUTANT "note_visit drops the guide start" (``guide_started`` not passed
    on to the bookkeeping): RED (observed):
        AssertionError: assert ('guiding_act...': 0, 'c': 0}) ==
        ('defer_wait'...': 1, 'c': 0})
          At index 0 diff: 'guiding_action' != 'defer_wait'
    """
    def one_pass(started: bool):
        run = GroupRun({"a": "1-1", "b": "1-2", "c": "1-3"},
                       max_failed_visits=3)
        for p in ("a", "b"):
            run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                              deferred=_defer(GUIDE_START))
        run.note_visit("c", exposures=0, accepted=0, guide_started=started)
        run.set_aside_panel("c", "1-3 sank below its own altitude floor")
        return run.close_pass().boundary, dict(run.failed)

    assert one_pass(True) == ("defer_wait", {"a": 1, "b": 1, "c": 0})
    assert one_pass(False) == ("guiding_action", {"a": 0, "b": 0, "c": 0})


def test_note_visit_refuses_what_visit_outcome_refuses():
    """One bookkeeping, one set of refusals: a panel that is not live, more
    accepted frames than exposures, a negative count.

    MUTANT "note_visit counts nothing": RED (observed):
        AssertionError: note_visit accepted {'panel': 'b', 'exposures': 1,
        'accepted': 0}
    """
    run = GroupRun({"a": "1-1", "b": "1-2"}, max_failed_visits=3)
    run.set_aside_panel("b", "gone")
    for kw in ({"panel": "b", "exposures": 1, "accepted": 0},
               {"panel": "a", "exposures": 1, "accepted": 2},
               {"panel": "a", "exposures": -1, "accepted": 0}):
        try:
            run.note_visit(**kw)
        except ValueError:
            continue
        raise AssertionError(f"note_visit accepted {kw}")
    assert run.exposures_this_pass == 0
