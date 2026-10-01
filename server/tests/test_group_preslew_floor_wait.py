"""A floor or keep-out refusal at a panel's own hop is a WAIT, as the same
verdict at selection is (#313, S3 orchestrator ruling 1; spec 5.1 selection
item 1, 6.2).

THE DEFECT. A mosaic panel's limits are asked twice. The scheduler asks the
slew gate's predicate at selection (`_eligibility_now`) and treats a floor or
keep-out verdict as a wait. The hop asks the gate again, in `_setup_target`'s
pre-slew `_safety_gate(context="slew")`, and there a refusal was a
`SlewRefused`, a SafetyAbort: the run ended unsafe, the rig parked and the
roof closed. Time passes between the two questions: the gate's monitor half
runs first, and a cloud hold it opens can last up to its bound. A panel that
sank into the horizon mask, a wedge or below the floor during that hold
ended the whole night, while the mosaic's other panels could still be shot.

THE FIX. `SlewRefused` carries its verdict's kind, and `_visit_panel` catches
a floor or ceiling refusal out of the member's OWN hop: the panel is requeued
as a reach wait, counted neither as a visit nor as a failure, and the run
goes on with the other panels; the next selection holds it as a waiter until
the limit clears. It is set aside, in words, only when its window has closed.
No site and a flips-off pier change still end the run, as does any refusal
raised for a single target.

THE HOLD IS STOOD IN FOR. Each case holds the panel's first pre-slew gate on
the fake clock until ``T_GATE``, 30 minutes into the night, before the real
gate runs: the time the gate's monitor half spent holding. A real cloud hold
needs a safety monitor, a cloud verdict and a sky that clears, none of which
this defect is about; the gate that refuses after it is the engine's own.
The limit is a no-go wedge (the ``floor`` kind: `schedule.effective_floor`
folds the floor, the mask and the wedges into one number), placed on the
azimuths panel 2-2 crosses between ``T_GATE`` and ``T_CLEAR`` and computed
from its own path, so the panel is clear at selection, refused at the gate,
and clear again ten minutes later.

The runs are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py). The fixture site is 40 N 74 W, not anybody's
rig, and no altitude or azimuth is printed.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/ (scratchpad s3ea-mut, or
s3ea-verify-mut where a case says so), never in the shared tree (#254).
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, LAT, LON, T0, Night, grid_plan,
                            group_hub, group_store, ra_at, single)
from astrodeck.catalog.coords import altaz
from astrodeck.config import SafetyConfig, Site
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.session import session_store

#: When the stood-in hold ends and the real gate runs.
T_GATE = T0 + 1800.0
#: When the refused panel has crossed the wedge.
T_CLEAR = T0 + 2400.0
FAILING = "2-2"
OTHERS = ("1-1", "1-2", "2-1")


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _gotos(night, label: str) -> list[float]:
    return [night.rel(t) for t, who in night.gotos if who == _name(label)]


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


def _far(target) -> None:
    """Move a target low in the east, clear of the fixture mosaic's other
    panels, so that only it crosses the wedge (2 h of hour angle east of
    them, 30 degrees further south)."""
    target.ra_hours, target.dec_deg = ra_at(-3.0), 10.0


def _wedge_for(target) -> dict:
    """A no-go wedge on the azimuths ``target`` crosses between ``T_GATE``
    and ``T_CLEAR``, reaching above anything it could be at: the slew gate
    refuses it there, as a floor, and lets it through before and after.
    Rising in the east, its azimuth grows through the night, which the
    premises below check rather than assume."""
    az = {t: altaz(target.ra_hours, target.dec_deg, LAT, LON, t)[1]
          for t in (T0 + 600.0, T_GATE, T_CLEAR)}
    assert az[T0 + 600.0] < az[T_GATE] - 0.5 < az[T_GATE] < az[T_CLEAR], (
        "premise: the target's azimuth grows through the wedge")
    return {"az_min": az[T_GATE] - 0.5, "az_max": az[T_CLEAR],
            "alt_max": 89.0}


def _hold_the_first_slew_gate(night, monkeypatch, who: str, *,
                              before=None) -> list[float]:
    """Hold ``who``'s first pre-slew gate until ``T_GATE`` on the fake clock,
    then run the real gate (the harness's spy around it). ``before`` runs
    just before the real gate, for a case that changes the rig while the
    gate held. Returns when each held gate began."""
    spy = night.engine._safety_gate
    held: list[float] = []

    async def gate(*a, **kw):
        target = kw.get("target")
        if (kw.get("context") == "slew" and target is not None
                and target.name == who and not held):
            held.append(night.rel(engine_mod.time.time()))
            await engine_mod.asyncio.sleep(T_GATE - engine_mod.time.time())
            if before is not None:
                before()
        return await spy(*a, **kw)

    monkeypatch.setattr(night.engine, "_safety_gate", gate)
    return held


def _hold_a_gate(night, monkeypatch, who: str, *, context: str, nth: int,
                 release=None) -> list[float]:
    """Hold ``who``'s ``nth`` gate of ``context`` ("slew" or "frame") until
    ``T_GATE`` on the fake clock, then await ``release(target)`` when one is
    given, then run the real gate (the harness's spy around it). Returns
    when the held gate began."""
    spy = night.engine._safety_gate
    held: list[float] = []
    seen = [0]

    async def gate(*a, **kw):
        target = kw.get("target")
        if (kw.get("context") == context and target is not None
                and target.name == who):
            seen[0] += 1
            if seen[0] == nth:
                held.append(night.rel(engine_mod.time.time()))
                await engine_mod.asyncio.sleep(T_GATE - engine_mod.time.time())
                if release is not None:
                    await release(target)
        return await spy(*a, **kw)

    monkeypatch.setattr(night.engine, "_safety_gate", gate)
    return held


async def _run(night, plan) -> Night:
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _floor_plan(group_store, **plan_kw):
    plan = grid_plan(**plan_kw)
    p22 = next(t for t in plan.targets if t.name == _name(FAILING))
    _far(p22)
    group_store.set_safety(SafetyConfig(enabled=False,
                                        nogo_box=[_wedge_for(p22)]))
    return plan, p22


# ------------------------------------------------------------- the wait

async def test_a_floor_refusal_at_a_panels_hop_waits_and_the_run_goes_on(
        group_hub, group_store, monkeypatch):
    """A 2x2 of L and R, count 3. Panel 2-2 is clear of the wedge when the
    scheduler picks it, third in the snake, and its pre-slew gate holds
    until ``T_GATE``, by when 2-2 has risen into the wedge: the gate refuses
    it, as a floor. The panel WAITS: it is requeued, not counted as a visit
    or a failure (pinned by the case after the window case, where the
    refused hop is its pass's only visit; here 2-2 completes, and its
    accepted frames would reset any count), and said in words. The run goes on: 2-1 is shot at once, then the passes of the
    other three while 2-2 waits, and 2-2 is first slewed to once it is past
    the wedge, then shot to completion. Nothing is set aside and the run
    ends complete.

    MUTANT "not caught in _visit_panel" (the ``except SlewRefused`` arm
    re-raising every refusal): RED, the night ends unsafe at the refusal
    (observed):
        AssertionError: ('aborted', 'unsafe', "slew refused: M31 2-2 would
        be below the mount's altitude floor by the end of a slew there")
        assert ('aborted', 'unsafe') == ('complete', 'complete')
          At index 0 diff: 'aborted' != 'complete'
    and the window case below with it (observed): ``AssertionError:
    ('aborted', 'unsafe')``, ``assert ('aborted', 'unsafe') == ('complete',
    'incomplete')``.
    """
    plan, _p22 = _floor_plan(group_store)
    night = Night(group_hub, monkeypatch)
    held = _hold_the_first_slew_gate(night, monkeypatch, _name(FAILING))
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "complete"), (last["state"], last.get("end_reason"),
                                  last.get("detail"))
    assert len(held) == 1 and held[0] < 600.0, (
        f"premise: 2-2 was picked early and its gate held: {held}")
    assert (T_GATE - T0, "slew") in [(night.rel(t), c) for t, c in night.gates]

    words = (f"M31: {FAILING} cannot be slewed to now: {_name(FAILING)} would "
             f"be below the mount's altitude floor by the end of a slew "
             f"there; it waits for the limit to clear, and the refused hop "
             f"is not counted as a visit")
    assert night.said(words) == [words], night.lines[-6:]
    hops = _gotos(night, FAILING)
    assert len(hops) == 3 and hops[0] >= T_CLEAR - T0, (
        f"2-2's hops at {hops}: none before it cleared the wedge at "
        f"{T_CLEAR - T0}")
    after = [who for t, who in night.gotos if T_GATE <= t < T0 + hops[0]]
    assert after[:1] == [_name("2-1")] and set(after) == {
        _name(label) for label in OTHERS}, (
        f"the run went on with the other panels while 2-2 waited: {after}")
    assert _shot(night, FAILING) == ["L", "R"] * 3, night.shots()
    for label in OTHERS:
        assert _shot(night, label) == ["L", "R"] * 3, label
    run = night.engine._group_runs["m31-mosaic"]
    assert run.failed["p11"] == 0 and not night.said("deferred"), (
        run.failed, night.said("deferred"))
    assert night.stored.set_aside == [], night.stored.set_aside


async def test_a_floor_refusal_after_its_window_closed_sets_the_panel_aside(
        group_hub, group_store, monkeypatch):
    """The same refusal, but 2-2's own window (``max_run_min`` 20) closed
    while its gate held. Nothing it waits for can come tonight, so it is
    set aside tonight, recorded, with a WARNING whose reason is words: the
    refusal's own words, no altitude, no azimuth, no time. It is never
    slewed to; the other three complete, and the run ends owing 2-2.

    MUTANT "never set aside: always requeued" (the window test in
    `_hop_refused_by_a_limit` made ``if False``): RED, the panel sits in the
    queue with its window closed until the all-closed path ends the night
    as a dawn cutoff, with nothing recorded (observed):
        AssertionError: ('complete', 'dawn_cutoff')
        assert ('complete', 'dawn_cutoff') == ('complete', 'incomplete')
          At index 1 diff: 'dawn_cutoff' != 'incomplete'
    """
    plan, p22 = _floor_plan(group_store)
    p22.schedule = Schedule(max_run_min=20)
    night = Night(group_hub, monkeypatch)
    held = _hold_the_first_slew_gate(night, monkeypatch, _name(FAILING))
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert len(held) == 1 and held[0] < 20 * 60.0, (
        f"premise: 2-2 was picked while its window was open: {held}")
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "incomplete"), (last["state"], last.get("end_reason"))
    reason = (f"{FAILING} could not be reached before its window closed: "
              f"{_name(FAILING)} would be below the mount's altitude floor "
              f"by the end of a slew there")
    assert [(r["target_id"], r["reason"]) for r in night.stored.set_aside] == [
        ("p11", reason)], night.stored.set_aside
    assert "°" not in reason
    alerts = [(lvl, m) for _t, lvl, m in night.lines
              if "set aside for tonight" in m]
    assert alerts == [("warning", f"M31: {reason}; set aside for tonight: a "
                                  f"restart tonight does not retry it, the "
                                  f"next night does")], alerts
    assert _gotos(night, FAILING) == [] and _shot(night, FAILING) == []
    for label in OTHERS:
        assert _shot(night, label) == ["L", "R"] * 3, label


async def test_a_keep_out_refusal_at_a_panels_hop_waits_too(
        group_hub, group_store, monkeypatch):
    """The ceiling kind, the zenith keep-out, waits as the floor kind does:
    time carries a panel out of it. 2-2 is moved onto the fixture site's
    zenith (its declination), transiting at ``T_GATE``, under a keep-out
    that starts 2.5 degrees below the zenith. It is clear of it when it is
    picked, third in the snake, even judged the gate's ``SLEW_PROJECT_S``
    ahead; its gate holds until ``T_GATE``, and there it is at the top of
    its arc: refused, as a ceiling, in words. It waits,
    the others go on, and it is shot once it has sunk out of the keep-out;
    nothing is set aside and the run ends complete.

    MUTANT "only the floor waits" (``REACH_TAGS.get(e.kind) != "wait"`` made
    ``e.kind != "floor"`` in `_visit_panel`): RED, the keep-out refusal ends
    the night (observed):
        AssertionError: ('aborted', 'unsafe', "slew refused: M31 2-2 would
        be in the mount's zenith keep-out by the end of a slew there, where
        the mount can reach its own tripod")
        assert ('aborted', 'unsafe') == ('complete', 'complete')
          At index 0 diff: 'aborted' != 'complete'
    It survives every floor case in this file. Applied in a private scratch copy of
    server/ (scratchpad s3ea-verify-mut), never in the shared tree (#254).
    """
    max_alt = 87.5
    plan = grid_plan()
    p22 = next(t for t in plan.targets if t.name == _name(FAILING))
    p22.ra_hours, p22.dec_deg = ra_at(-0.5), LAT
    ahead = engine_mod.SLEW_PROJECT_S
    picked, gated = T0 + 240.0 + ahead, T_GATE + ahead
    alt = {t: altaz(p22.ra_hours, p22.dec_deg, LAT, LON, t)[0]
           for t in (picked, gated)}
    assert alt[picked] < max_alt - 1.0 < max_alt + 1.0 < alt[gated], (
        "premise: 2-2 is clear of the keep-out when picked and deep in it "
        "at the held gate")
    group_store.set_safety(SafetyConfig(enabled=False, max_alt_deg=max_alt))
    night = Night(group_hub, monkeypatch)
    held = _hold_the_first_slew_gate(night, monkeypatch, _name(FAILING))
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "complete"), (last["state"], last.get("end_reason"),
                                  last.get("detail"))
    assert len(held) == 1 and held[0] <= picked - ahead - T0, (
        f"premise: 2-2 was picked by {picked - ahead - T0}: {held}")
    words = (f"M31: {FAILING} cannot be slewed to now: {_name(FAILING)} would "
             f"be in the mount's zenith keep-out by the end of a slew there; "
             f"it waits for the limit to clear, and the refused hop is not "
             f"counted as a visit")
    assert night.said(words) == [words], night.lines[-6:]
    hops = _gotos(night, FAILING)
    assert len(hops) == 3 and hops[0] > T_GATE - T0, hops
    assert _shot(night, FAILING) == ["L", "R"] * 3, night.shots()
    for label in OTHERS:
        assert _shot(night, label) == ["L", "R"] * 3, label
    assert night.stored.set_aside == [], night.stored.set_aside


async def test_a_refused_hop_alone_in_its_pass_is_neither_a_visit_nor_a_failure(
        group_hub, group_store, monkeypatch):
    """A 1x2 of L and R: 1-1 owes one frame of each and completes on its
    first visit, 1-2 owes three and is moved onto the wedge's path. Pass 2
    is 1-2 alone, and its hop's gate holds until ``T_GATE`` and refuses, as
    a floor. That refused hop is the only thing pass 2 does, which is where
    counting it would show: as a VISIT of no exposures, the boundary at its
    next selection reads a full pass that took none and sets the mosaic
    aside for the night (the group anti-spin); as a FAILURE, a pass of
    deferrals and no exposures, which waits ``DEFER_WAIT_S`` and moves 1-2's
    count. It is neither: 1-2 waits out the wedge unvisited, is hopped to
    again once it has cleared, and completes, with no pass wait, no retry
    and nothing set aside.

    (The first case's ``failed == 0`` cannot show this: 2-2 completes there,
    and an accepted frame resets the count whatever came before.)

    MUTANT "refused hop counted as a visit" (`_hop_refused_by_a_limit`
    calling ``run.visit_outcome(target.id, complete=False, exposures=0,
    accepted=0)`` before its requeue): RED, the anti-spin set the mosaic
    aside and 1-2 was never hopped to again (observed):
        AssertionError: 1-2's hops at [60.0]: one before the held gate, the
        next only once past the wedge at 2400.0
        assert (1 == 3)
         +  where 1 = len([60.0])
    MUTANT "refused hop counted as a failure, line kept" (the same call
    with a ``PanelDeferred`` of kind ``centring``): RED (observed):
        AssertionError: (['M31: pass 2 took no exposures and deferred 1
        visits; waiting 300 s before the next pass'], [], [])
        assert (['M31: pass ...ass'], [], []) == ([], [], [])
          At index 0 diff: ['M31: pass 2 took no exposures and deferred 1
        visits; waiting 300 s before the next pass'] != []
    Both survive the first case above. Applied in a private scratch copy of
    server/ (scratchpad s3ea-verify-mut), never in the shared tree (#254).
    """
    plan = grid_plan(rows=1, cols=2)
    p11 = next(t for t in plan.targets if t.name == _name("1-1"))
    for step in p11.steps:
        step.count = 1
    p12 = next(t for t in plan.targets if t.name == _name("1-2"))
    _far(p12)
    group_store.set_safety(SafetyConfig(enabled=False,
                                        nogo_box=[_wedge_for(p12)]))
    night = Night(group_hub, monkeypatch)
    held = _hold_a_gate(night, monkeypatch, _name("1-2"), context="slew",
                        nth=2)
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    run = night.engine._group_runs["m31-mosaic"]
    assert len(held) == 1 and held[0] < T_GATE - T0, (
        f"premise: 1-2's second hop began before the wedge: {held}")
    assert _shot(night, "1-1") == ["L", "R"], night.shots()
    words = (f"M31: 1-2 cannot be slewed to now: {_name('1-2')} would be "
             f"below the mount's altitude floor by the end of a slew there; "
             f"it waits for the limit to clear, and the refused hop is not "
             f"counted as a visit")
    assert night.said(words) == [words], night.lines[-6:]
    hops = _gotos(night, "1-2")
    assert len(hops) == 3 and hops[0] < held[0] and hops[1] >= T_CLEAR - T0, (
        f"1-2's hops at {hops}: one before the held gate, the next only "
        f"once past the wedge at {T_CLEAR - T0}")
    waits = night.said("before the next pass")
    retries = night.said("retried on the next pass")
    aside = night.said("set aside")
    assert (waits, retries, aside) == ([], [], []), (waits, retries, aside)
    assert _shot(night, "1-2") == ["L", "R"] * 3, night.shots()
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "complete"), (last["state"], last.get("end_reason"),
                                  last.get("detail"))
    assert run.set_aside == {} and night.stored.set_aside == [], (
        run.set_aside, night.stored.set_aside)


# ---------------------------------------------------------------- controls

async def test_control_a_pier_refusal_at_the_hop_still_ends_the_run(
        group_hub, group_store, monkeypatch):
    """Flips off and the pier guard armed. 2-2 stands 12 minutes of hour
    angle east of the meridian when the scheduler picks it, on the side the
    mount is on for the other panels; its gate holds until ``T_GATE``, by
    when 2-2 has crossed the meridian, and the gate's pier guard refuses a
    slew that would need a pier flip the plan does not allow. Waiting cannot
    fix that, so the refusal still ends the run unsafe, from the hop, as it
    always did.

    MUTANT "catch every SlewRefused kind" (the kind test dropped from
    `_visit_panel`'s ``except SlewRefused``): RED, the pier refusal waits,
    the next selection sets 2-2 aside, and the night goes on (observed):
        AssertionError: ('complete', 'incomplete', 'targets set aside —
        frames still owed')
        assert ('complete', 'incomplete') == ('aborted', 'unsafe')
          At index 0 diff: 'complete' != 'aborted'
    """
    group_store.set_safety(SafetyConfig(enabled=False,
                                        enforce_pier_limits=True))
    plan = grid_plan(meridian_flip=False)
    p22 = next(t for t in plan.targets if t.name == _name(FAILING))
    p22.ra_hours = ra_at(-0.2)
    night = Night(group_hub, monkeypatch, coords_clock=True)
    held = _hold_the_first_slew_gate(night, monkeypatch, _name(FAILING))
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert len(held) == 1 and held[0] < 600.0, held
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == ("aborted", "unsafe"), (
        last["state"], last.get("end_reason"), last.get("detail"))
    assert last["detail"].endswith(
        f"slew to {_name(FAILING)} would require a pier flip but meridian "
        f"flip is disabled"), last["detail"]
    assert _gotos(night, FAILING) == []


async def test_control_a_no_site_refusal_at_the_hop_still_ends_the_run(
        group_hub, group_store, monkeypatch):
    """A floor is configured. While 2-2's gate held, the saved site went
    away, so at the gate no altitude can be checked at all: a ``no_site``
    refusal, which waiting cannot fix. It ends the run unsafe from the hop,
    and nothing says 2-2 waits.

    MUTANT "catch every SlewRefused kind": RED, the refusal is first taken
    for a wait (the next selection then ends the run on the same verdict)
    (observed):
        AssertionError: ["M31: 2-2 cannot be slewed to now: no observing
        site is saved, so no slew can be checked against the mount's limits;
        it waits for the limit to clear, and the refused hop is not counted
        as a visit"]
        assert not ["M31: 2-2 cannot be slewed to now: no observing site is
        saved, so no slew can be checked against the mount's limits; it
        waits for the limit to clear, and the refused hop is not counted as
        a visit"]
    """
    group_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=10.0))
    plan = grid_plan()

    def unsave():
        group_store.cfg().site = Site()

    night = Night(group_hub, monkeypatch)
    held = _hold_the_first_slew_gate(night, monkeypatch, _name(FAILING),
                                     before=unsave)
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert len(held) == 1, held
    waits = night.said(f"M31: {FAILING} cannot be slewed to now")
    assert not waits, waits
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == ("aborted", "unsafe"), (
        last["state"], last.get("end_reason"), last.get("detail"))
    assert "no observing site is saved" in last["detail"], last["detail"]
    assert _gotos(night, FAILING) == []


async def test_control_a_refusal_after_the_hop_still_ends_the_run(
        group_hub, group_store, monkeypatch):
    """The wait belongs to the member's OWN hop, and only to it
    (`_visit_panel`'s ``hopped``). Here 2-2's hop goes through, early and
    clear of the wedge, and its first FRAME gate holds until ``T_GATE``;
    the hold then re-acquires the panel the way a cloud hold's release does
    (`_hold_for_clear` calling `_setup_target` for the target it held), and
    that setup's pre-slew gate refuses 2-2, now in the wedge, as a floor.
    That refusal came out of the frame loop, not the hop, and it still ends
    the run unsafe, as the docstrings of `_visit_panel` and
    `_hop_refused_by_a_limit` say: S3 orchestrator ruling 1 covers the
    panel's hop and a hold its gate opened, and nothing later.

    MUTANT "frame-loop refusal waits too" (the ``hopped`` test dropped from
    `_visit_panel`'s ``except SlewRefused``): RED, the refusal is taken for
    a reach wait and the night goes on (observed):
        AssertionError: ('complete', 'complete', 'all targets complete')
        assert ('complete', 'complete') == ('aborted', 'unsafe')
          At index 0 diff: 'complete' != 'aborted'
    It survives every other case in this file. Applied in a private scratch
    copy of server/ (scratchpad s3ea-verify-mut), never in the shared tree
    (#254).
    """
    plan, _p22 = _floor_plan(group_store)
    night = Night(group_hub, monkeypatch)

    async def release(target):
        eng = night.engine
        await eng._setup_target(eng._index_of_target(target), target)

    held = _hold_a_gate(night, monkeypatch, _name(FAILING), context="frame",
                        nth=1, release=release)
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    hops = _gotos(night, FAILING)
    assert len(held) == 1 and hops and hops[0] <= held[0] < 600.0, (
        f"premise: 2-2 was hopped to at {hops} and its first frame gate "
        f"held from {held}")
    assert (T_GATE - T0, "slew") in [(night.rel(t), c) for t, c in night.gates]
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == ("aborted", "unsafe"), (
        last["state"], last.get("end_reason"), last.get("detail"))
    assert last["detail"].endswith(
        f"slew refused: {_name(FAILING)} would be below the mount's altitude "
        f"floor by the end of a slew there"), last["detail"]
    waits = night.said(f"M31: {FAILING} cannot be slewed to now")
    assert not waits, waits
    assert len(hops) == 1 and _shot(night, FAILING) == [], (hops,
                                                            night.shots())


async def test_control_a_single_targets_floor_refusal_at_its_hop_ends_the_run(
        group_hub, group_store, monkeypatch):
    """No group: "Solo" alone, on the same path and behind the same wedge,
    its first gate held until ``T_GATE``. A single target's refusal is not
    a panel's reach wait; it ends the run unsafe from the hop, as it always
    has. The wait belongs to a panel's visit (`_visit_panel`) and nowhere
    else.

    MUTANT "the reach wait taken for every target" (the scheduler's
    single-target branch catching a floor refusal from `_setup_target`,
    waiting ``REACH_RECHECK_S`` and selecting again): RED, Solo waits out
    the wedge and is shot (observed):
        AssertionError: ('complete', 'complete', 'all targets complete')
        assert ('complete', 'complete') == ('aborted', 'unsafe')
          At index 0 diff: 'complete' != 'aborted'
    The same mutant with no wait before it selects again SPINS: nothing asks
    a single target's limits at selection, so it is picked and refused at
    the same instant for ever (the harness's spin watchdog, #319, ended it:
    "the event loop did not come back for 20.0 s of real time"). A panel's
    reach wait does not, because the next selection asks the verdict.
    """
    solo = single("Solo")
    _far(solo)
    group_store.set_safety(SafetyConfig(enabled=False,
                                        nogo_box=[_wedge_for(solo)]))
    plan = SequencePlan(name="solo", targets=[solo], guide=False,
                        dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False, recover_guiding=False)
    night = Night(group_hub, monkeypatch)
    held = _hold_the_first_slew_gate(night, monkeypatch, "Solo")
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert held == [0.0], held
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == ("aborted", "unsafe"), (
        last["state"], last.get("end_reason"), last.get("detail"))
    assert last["detail"].endswith(
        "slew refused: Solo would be below the mount's altitude floor by the "
        "end of a slew there"), last["detail"]
    assert night.gotos == [] and night.captures == []
