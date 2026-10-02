# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The no-guider rule counts the members THIS PASS CAN VISIT, not every live
member (the #315 follow-up, S3 orchestrator ruling 6; spec 5.6 step 7).

THE DEFECT. S3 made the group's last LIVE panel a plain target when no
guider is connected and guiding is optional: the guide-start pass rule
blames the rig only after two attempts in a pass, and one live panel never
makes two, so it deferred on every hop and was set aside after three
passes. The premise holds just as well for the last REACHABLE panel. A
member held all pass, below the mount's floor, past the meridian while
another panel can still shoot before it, or before its own start gate, is
live and makes no attempt either. `_live_panels` counted `GroupRun.live()`,
so with such a member the one panel the pass could visit deferred at every
hop, each pass one attempt and one deferral, a ``DEFER_WAIT_S`` wait after
each, and it was set aside after three: #315 in another shape (found in
S3's safety review of #315, and noted on the issue).

THE FIX. Each selection records the members it lets through for the pass
(`GroupRun.note_let_through`, from `_eligibility_now`), and `_live_panels`
counts the live members the pass visited or let through, and the member
asking (`GroupRun.visitable`).

Every case runs the real scheduler, hop and frame loop on the clocked
simulator (tests/_group_harness.py). What is scripted is the guider's
presence (``connected = False``: a guider that is there and not up), the
rig's escalation, and where the held panel stands. The fixture site is
40 N 74 W, not anybody's rig, and no altitude or azimuth is printed.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad/s4-engc-mut), never in the
shared tree (#254), and run again on the finished S4 tree on 2026-09-27
(scratchpad/s4-engc-resume-mut), where each failed as recorded.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from _group_harness import (GROUP_NAME, LAT, LON, T0, Night, grid_plan,
                            group_hub, group_store, ra_at)
from astrodeck.catalog.coords import altaz
from astrodeck.config import EscalationConfig, SafetyConfig
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.group_rules import (DEFER_WAIT_S, GroupRun,
                                            no_guider_defers)
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.session import session_store

#: The plain target's warning, `_setup_target`'s fall-through: what the same
#: target outside a group says before it shoots unguided.
UNGUIDED = "continuing UNGUIDED"
#: The line a deferral wait starts with (`GroupRun.close_pass`).
WAITING = f"waiting {DEFER_WAIT_S:.0f} s before the next pass"
#: When the held panel is free: 30 minutes into the night, long after the
#: reachable panel's three visits (about four minutes when shot, and about
#: fifteen when deferred, two deferral waits included).
T_FREE = T0 + 1800.0


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _optional_guiding(store) -> None:
    """The rig's escalation as it ships: guiding optional, warn."""
    store.set_escalation(EscalationConfig(require_guiding=False,
                                          guiding_action="warn"))


def _low(target) -> None:
    """Put ``target`` low in the east and rising, 4 h of hour angle east of
    the meridian on the equator, well below the other panel."""
    target.ra_hours, target.dec_deg = ra_at(-4.0), 0.0


def _alt(target, t: float) -> float:
    return altaz(target.ra_hours, target.dec_deg, LAT, LON, t)[0]


def _held_plan(store, how: str) -> SequencePlan:
    """A 1x2 of L and R, 3 frames each, one frame a visit, guiding asked,
    whose panel 1-2 is held from the start of the night until ``T_FREE``,
    and 1-1 is free throughout:

    * ``reach``: 1-2 below the MOUNT's altitude floor (`_eligibility_now`'s
      reach verdict, ``held`` "reach");
    * ``gating``: 1-2 below its OWN start gate, ``min_altitude_deg`` (the
      gating calls it waiting, so it is never a candidate);
    * ``meridian``: flips on, 1-1 before the meridian with room to spare and
      1-2 past it, where the one-pier-change rule holds it while a panel
      before the meridian can still shoot (``held`` "meridian"). Both at Dec
      20, so neither passes near the zenith."""
    if how == "meridian":
        plan = grid_plan(rows=1, cols=2, guide=True, meridian_flip=True,
                         panel_kw={"ha_h": -1.5, "ha_step_h": 1.8})
        for t in plan.targets:
            t.dec_deg = 20.0
        return plan
    plan = grid_plan(rows=1, cols=2, guide=True)
    p11, p12 = plan.targets
    _low(p12)
    floor = _alt(p12, T_FREE)
    assert _alt(p12, T0) < floor - 3.0, "premise: 1-2 starts well below"
    assert min(_alt(p11, T0 + m * 60.0) for m in range(0, 31)) > floor + 20.0, (
        "premise: 1-1 is far above the floor all the while")
    if how == "reach":
        store.set_safety(SafetyConfig(enabled=False, min_alt_deg=floor))
    else:
        p12.schedule = Schedule(min_altitude_deg=floor)
    return plan


async def _night(hub, monkeypatch, plan) -> Night:
    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


# ------------------------------------------------------------- the rule

@pytest.mark.parametrize("how", ["reach", "gating", "meridian"])
async def test_a_panel_held_all_pass_does_not_make_the_reachable_one_defer(
        group_hub, group_store, monkeypatch, how):
    """A 1x2 with guiding optional and no guider connected, whose 1-2 is
    held all pass (see `_held_plan`: below the mount's floor, below its own
    start gate, or past the meridian). 1-1 is the one panel the pass can
    visit, so it follows the plain-target rule: each of its three visits is
    shot unguided after the plain target's warning, with no deferral, no
    ``DEFER_WAIT_S`` wait, and nothing set aside. Once 1-1 is complete and
    1-2 is free, 1-2 is shot the same way, and the run completes.

    MUTANT "_live_panels counts every live member" (`_live_panels`
    returning ``max(1, len(run.live()))``, S3's count): 1-1 defers at every
    hop, waits ``DEFER_WAIT_S`` after each pass of one attempt, and is set
    aside after three. RED on each (observed):
        reach and meridian, the same lines:
            AssertionError: [('M31 1-1', ()), ('M31 1-1', ()), ('M31 1-1',
            ()), ('M31 1-2', ('L', 'R')), ('M31 1-2', ('L', 'R')), ('M31
            1-2', ('L', 'R'))]
            assert [('M31 1-1', ...M31 1-1', ())] == [('M31 1-1', ..., ('L',
            'R'))]
              At index 0 diff: ('M31 1-1', ()) != ('M31 1-1', ('L', 'R'))
        gating (1-1 defers once, the run then sleeps through its deferral
        wait to 1-2's rise, #380, and there both defer and the pass rule
        sends them on unguided):
            AssertionError: [('M31 1-1', ()), ('M31 1-2', ()), ('M31 1-1',
            ()), ('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')), ('M31
            1-1', ('L', 'R')), ...]
            assert [('M31 1-1', ...M31 1-1', ())] == [('M31 1-1', ..., ('L',
            'R'))]
              At index 0 diff: ('M31 1-1', ()) != ('M31 1-1', ('L', 'R'))
    MUTANT "count the members, not the live ones" (`_live_panels`
    returning ``len(run.members)``): the same on each; reach and meridian
    (observed) with 1-2 deferred three times too:
        AssertionError: [('M31 1-1', ()), ('M31 1-1', ()), ('M31 1-1', ()),
        ('M31 1-2', ()), ('M31 1-2', ()), ('M31 1-2', ())]
    """
    _optional_guiding(group_store)
    group_hub.guider.connected = False
    night = await _night(group_hub, monkeypatch, _held_plan(group_store, how))
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert visits[:3] == [(_name("1-1"), ("L", "R"))] * 3, visits
    first_12 = min(t for t, who in night.gotos if who == _name("1-2"))
    last_11 = max(t for t, who in night.gotos if who == _name("1-1"))
    assert last_11 < first_12, (
        "premise: 1-2 was held until 1-1 was done", night.rel(last_11),
        night.rel(first_12))
    if how != "meridian":
        assert first_12 >= T_FREE, (
            "premise: 1-2 was held all the while 1-1 was shot",
            night.rel(first_12))
    assert visits[3:] == [(_name("1-2"), ("L", "R"))] * 3, visits
    assert night.engine.state.get("end_reason") == "complete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert len(night.said(UNGUIDED)) == 6, night.said("guid")
    assert not night.said("guiding did not start"), night.said("guiding")
    assert not night.said(WAITING), night.said("waiting")
    assert night.stored.set_aside == []


# ---------------------------------------------------------------- controls

async def test_control_both_panels_reachable_both_defer(
        group_hub, group_store, monkeypatch):
    """Control: with nothing held, the pass can visit both panels, and S2's
    rule is unchanged. Guiding optional, no guider connected: 1-1 and 1-2
    are each deferred in the first pass and shoot nothing, two attempted and
    two failed is the rig's fault, and ``guiding_action`` (warn) sends the
    panels on unguided from the second pass.

    MUTANT "only the panel asking is counted" (`_live_panels` returning 1):
    both panels are shot unguided in the first pass. RED (observed):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')),
        ('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')), ('M31 1-1', ('L',
        'R')), ('M31 1-2', ('L', 'R'))]
        assert [('M31 1-1', ..., ('L', 'R'))] == [('M31 1-1', ...M31 1-2',
        ())]
          At index 0 diff: ('M31 1-1', ('L', 'R')) != ('M31 1-1', ())
    MUTANT "nothing let through is kept" (`GroupRun.note_let_through` made
    a no-op, so only visited members and the one asking count): the first
    panel of each pass is shot unguided and only the second defers, so no
    pass ever makes the rig verdict's two attempts. RED (observed):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ()), ('M31
        1-2', ('L', 'R')), ('M31 1-1', ()), ('M31 1-1', ('L', 'R')), ('M31
        1-2', ()), ...]
        assert [('M31 1-1', ...M31 1-2', ())] == [('M31 1-1', ...M31 1-2',
        ())]
          At index 0 diff: ('M31 1-1', ('L', 'R')) != ('M31 1-1', ())
    """
    _optional_guiding(group_store)
    group_hub.guider.connected = False
    night = await _night(group_hub, monkeypatch,
                         grid_plan(rows=1, cols=2, guide=True))
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert visits[:2] == [(_name("1-1"), ()), (_name("1-2"), ())], visits
    assert night.said("guiding did not start on any of the 2 panels tried "
                      "this pass"), night.lines[-8:]
    assert visits[2:4] == [(_name("1-1"), ("L", "R")),
                           (_name("1-2"), ("L", "R"))], visits
    assert night.stored.owed() == 0 and night.stored.set_aside == []


async def test_control_with_guiding_required_the_reachable_panel_still_defers(
        group_hub, group_store, monkeypatch):
    """Control: the plain-target rule is for guiding the rig marks OPTIONAL.
    With ``require_guiding`` on (and ``guiding_action`` abort), 1-1 of the
    ``reach`` night above, the one panel the pass can visit, still defers as
    S2 built it, three times, and is set aside; 1-2 then does the same once
    it is free, and the run ends incomplete. The plain-target rule would
    have ended the night at the first hop.

    MUTANT "required guiding lets the reachable panel through too"
    (`group_rules.no_guider_defers` returning ``live >= 2``): 1-1 takes the
    plain target's escalation, abort. RED (observed):
        AssertionError: ('unsafe', 'guiding required but the guider
        (scripted guider) is not connected')
        assert 'unsafe' == 'incomplete'
          - incomplete
          + unsafe
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    group_hub.guider.connected = False
    night = await _night(group_hub, monkeypatch,
                         _held_plan(group_store, "reach"))
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "incomplete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.visits() == ([(_name("1-1"), ())] * 3
                              + [(_name("1-2"), ())] * 3), night.visits()
    assert night.shots() == []
    assert [r["target_id"] for r in night.stored.set_aside] == ["p00", "p01"]
    assert not night.said(UNGUIDED), night.said(UNGUIDED)


# ------------------------------------------------ the count's pass scope

def test_what_a_pass_let_through_is_not_carried_into_the_next():
    """`GroupRun.visitable` is THIS PASS's: what one pass's selections let
    through is cleared by `GroupRun.start_pass`, so a member visitable last
    pass and held all of this one is not counted again, and a set-aside
    member is never counted, whatever a selection let through. The clocked
    nights above never let a member through in one pass and then hold it
    all of the next, so none of them reaches the clear.

    MUTANT "let_through never cleared at a pass start" (the verifier's, run
    in scratchpad/s4-engc-verify-mut; the ``self.let_through.clear()``
    deleted from `GroupRun.start_pass`): every case above and in
    test_group_last_panel_unguided.py and test_group_guide_lost_defers.py
    stays green (observed, 17 passed); this case is RED (observed):
        AssertionError: ['p00', 'p01']
        assert ['p00', 'p01'] == []
          Left contains 2 more items, first extra item: 'p00'
    MUTANT "nothing let through is kept" (as in the controls above) turns
    it RED too, at the first let-through (observed, 2026-09-27):
        AssertionError: assert [] == ['p00', 'p01']
          Right contains 2 more items, first extra item: 'p00'
    """
    run = GroupRun({"p00": "1-1", "p01": "1-2"}, max_failed_visits=3)
    assert run.visitable() == []
    run.note_let_through(["p00", "p01", "not-a-member"])
    assert run.visitable() == ["p00", "p01"]
    assert run.let_through == {"p00", "p01"}
    run.start_pass()
    assert run.visitable() == [], run.visitable()
    # Control: the next pass's own selection counts again.
    run.note_let_through(["p01"])
    assert run.visitable() == ["p01"]
    run.set_aside_panel("p01", "sank below its own altitude floor")
    assert run.visitable() == [], run.visitable()


def test_the_count_is_what_the_pass_can_visit_and_the_member_asking():
    """`_live_panels` itself, on a stand-in engine: the members this pass
    visited or let through (`GroupRun.visitable`), the member whose hop
    asks when it is live, and never less than 1; with no run state for the
    group (a setup driven before `_start_groups`), every member the plan
    carries.

    THE MEMBER ASKING IS COUNTED BY NAME, although no clocked night can
    tell: every hop of a run is made of a member the selection that chose
    it let through (`_meridian_now` answers for every candidate, and a pass
    boundary selects again before any hop). A hop that reached the rule any
    other way would otherwise count one panel short, and take the plain
    target's rule with another panel of the pass still visitable.

    Each mutant run in scratchpad/s4-engc-resume-mut, from a byte backup.
    MUTANT "the asking member not added" (``can.add(asking.id)`` and its
    ``if`` deleted): every clocked case above and in
    test_group_last_panel_unguided.py stays green (observed, 11 passed);
    here RED (observed):
        AssertionError: 1-3 asking: 1
        assert 1 == 2
    MUTANT "a member set aside is counted when it asks" (the ``and
    run.is_live(asking.id)`` dropped). RED (observed):
        AssertionError: 1-3 set aside, asking: 2
        assert 2 == 1
    MUTANT "no floor" (``return max(1, len(can))`` made ``return
    len(can)``). RED (observed):
        AssertionError: nothing let through, nobody asking: 0
        assert 0 == 1
    MUTANT "no run state counts one" (the last ``return`` made ``return
    1``). RED (observed):
        AssertionError: no run state: 1
        assert 1 == 3
    MUTANT "_live_panels counts every live member" (S3's count, as in the
    first case above). RED (observed):
        AssertionError: nothing let through, nobody asking: 3
        assert 3 == 1
    """
    group = SimpleNamespace(id="g")
    p = {pid: SimpleNamespace(id=pid) for pid in ("p00", "p01", "p02")}
    run = GroupRun({"p00": "1-1", "p01": "1-2", "p02": "1-3"},
                   max_failed_visits=3)
    eng = SimpleNamespace(_group_runs={"g": run})

    def count(asking=None) -> int:
        return SequenceEngine._live_panels(eng, group, asking)

    got = count()
    assert got == 1, f"nothing let through, nobody asking: {got}"
    run.note_let_through(["p00"])
    got = count(p["p00"])
    assert got == 1, f"1-1 let through and asking: {got}"
    got = count(p["p02"])
    assert got == 2, f"1-3 asking: {got}"
    # What the count decides: with guiding optional, 1-3 asking with 1-1
    # still visitable defers (S2's rule), and 1-1 alone would not.
    assert no_guider_defers(live=count(p["p02"]), require_guiding=False)
    assert not no_guider_defers(live=count(p["p00"]), require_guiding=False)
    run.set_aside_panel("p02", "sank below its own altitude floor")
    got = count(p["p02"])
    assert got == 1, f"1-3 set aside, asking: {got}"

    members = [SimpleNamespace(id=pid, member=True) for pid in p]
    bare = SimpleNamespace(
        _group_runs={},
        plan=SimpleNamespace(targets=members + [
            SimpleNamespace(id="other", member=False)]),
        _group_of=lambda t: group if t.member else None)
    got = SequenceEngine._live_panels(bare, group)
    assert got == 3, f"no run state: {got}"
