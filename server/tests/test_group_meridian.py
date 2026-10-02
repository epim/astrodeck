# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic group's meridian rule on the clocked simulator (#189 S2, task
T18; spec 5.7, 5.3, 5.6 step 5, 5.10, 6.9, 6.11, Appendix A.4, D10, I-38;
#136, #166).

AT MOST ONE PIER CHANGE PER GROUP PER NIGHT. A pre-flip panel is eligible
while it has room for a hop and one frame before its flip point, the PLAN's
lead before transit, and its visit ends at the frame boundary before that
point. A panel past the meridian is eligible only while no pre-flip panel
is; the hop that takes the first one is the group's pier change, read back
from the mount, never assumed. After it only panels past the meridian are
shot, and the rest wait for their crossings. Every case here is one run; a
restart the same night keeps the one change through ``Session.group_pier``
(#312), and those cases are in test_group_pier_state_persisted.py.

Every case runs the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py), whose clock ``catalog.coords`` is on for every
night (#320; the ``coords_clock`` these cases pass is that default): the
meridian countdown and the sim mount's pier side read the hour angle on the
fake clock. The pier side after each hop is read from the simulator mount's
own oracle, inside the harness's goto, the moment the slew lands.

THE PREMISE (I-38). The straddle test proves something only if the
simulator mount picks its pier side from the hour angle, as the AM5 does
(`hub.meridian_flip`'s docstring); `test_the_sim_mount_picks_its_pier_side
_from_the_hour_angle` asserts it first. It held. Until S4-SIM the sim also
read its side from where it pointed NOW, so it reported the far side of a
target it merely tracked across the meridian, which a real GEM does not
(#298); it now latches the side at each slew, sync, park and unpark
(test_sim_pier_side_latched.py). Every count here reads the side at the
moment a hop lands, where the two behave alike, and every case here passed
unchanged on the latching mount.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/, never in the shared tree (#254).
"""
from __future__ import annotations

import math

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, LON, T0, Night, grid_plan, group_hub,
                            group_store, ra_at)
from astrodeck.sequence import schedule
from astrodeck.sequence.group_rules import GroupRun
from astrodeck.sequence.session import session_store

#: Hour angle runs this much faster than the clock.
SIDEREAL = 1.0027379093
LEAD_S = schedule.MERIDIAN_FLIP_LEAD_MIN * 60.0


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _label(target: str) -> str:
    prefix = GROUP_NAME + " "
    return target[len(prefix):] if target.startswith(prefix) else target


def crossing(ra: float) -> float:
    """The fake time at which ``ra`` crosses the fixture site's meridian."""
    return T0 + schedule.hours_to_meridian_flip(ra, LON, T0) * 3600.0 / SIDEREAL


def meridian_plan(*, cols: int = 2, ha_h: float, ha_step_h: float,
                  count: int, filters=("L",), group_kw: dict | None = None,
                  **plan_kw):
    """A 1 x ``cols`` mosaic near the meridian with flips on: column 0 at
    hour angle ``ha_h`` at T0, each further column ``ha_step_h`` further
    west, one pass a visit, ``count`` frames of each filter a panel."""
    return grid_plan(rows=1, cols=cols, group_kw=group_kw,
                     panel_kw={"filters": filters, "count": count,
                               "ha_h": ha_h, "ha_step_h": ha_step_h},
                     meridian_flip=True, **plan_kw)


class SideLog:
    """Reads the simulator mount's pier side the moment each hop lands, from
    the device's own oracle, into ``self.sides`` as ``(t, target, side)``."""

    def __init__(self, hub):
        self.tel = hub.devices["telescope"]
        self.sides: list[tuple[float, str, str]] = []
        self.night: Night | None = None

    def goto(self, who, n, result):
        side = self.tel._side_for_ra(self.tel.rig.ra_hours).value
        self.sides.append((self.night.clock.t, who, side))
        return result

    def changes(self) -> int:
        seq = [s for _t, _w, s in self.sides]
        return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0,
                 on_capture=None, before_run=None, **kw) -> Night:
    night = Night(hub, monkeypatch, coords_clock=True, **kw)
    night.on_capture = on_capture
    if before_run is not None:
        before_run(night)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


# ------------------------------------------------------------ the premise

async def test_the_sim_mount_picks_its_pier_side_from_the_hour_angle(
        group_hub, monkeypatch):
    """I-38, FIRST: on the clocked clock, a goto east of the meridian lands
    the simulator mount on the west side of the pier and a goto past it on
    the east (ASCOM convention, as the AM5N was measured), the destination
    oracle agrees with where the goto lands, and the SAME RA an hour later,
    now past the meridian, lands on the east side: the side is a function
    of the hour angle at the goto, read on the fake clock. Without this the
    straddle test below would count pier changes the simulator invented.

    MUTANT "a constant side" (`SimTelescope._side_for_ra` returning WEST
    whatever the hour angle, the pre-2026-08-06 shape): RED (observed):
        AssertionError: assert 'west' == 'east'
          - east
          + west
    """
    night = Night(group_hub, monkeypatch, coords_clock=True)
    try:
        tel = group_hub.devices["telescope"]
        east_ra, west_ra = ra_at(-0.5), ra_at(0.5)
        assert (await tel.destination_pier_side(east_ra, 40.0)).value == "west"
        await tel.slew(east_ra, 40.0)
        assert (await tel.pier_side()).value == "west", (
            "a goto east of the meridian landed on the east side")
        assert (await tel.destination_pier_side(west_ra, 40.0)).value == "east"
        await tel.slew(west_ra, 40.0)
        assert (await tel.pier_side()).value == "east", (
            "a goto past the meridian landed on the west side")
        # The clock, not the RA: an hour on, the first RA is past the
        # meridian, and a goto to it lands on the east side.
        night.clock.t = T0 + 3600.0
        assert schedule.hours_to_meridian_flip(east_ra, LON, night.clock.t) < 0
        await tel.slew(east_ra, 40.0)
        assert (await tel.pier_side()).value == "east", (
            "the side did not follow the fake clock's hour angle")
    finally:
        await night.close()



# ------------------------------------------------------- one pier change

def _straddle_setup(log):
    def setup(night):
        log.night = night
        night.goto_script = log.goto
    return setup


async def test_a_meridian_straddle_changes_pier_side_once(group_hub,
                                                          monkeypatch):
    """A 1x2 of L, count 100 a panel, straddling the meridian: 1-2 crosses 30
    min into the night and 1-1, 0.4 h of RA east of it, 54 min in. With the
    plan's 10 min lead, a 150 s hop estimate and a 72 s frame, 1-2 runs out
    of room before its flip point at 16.3 min and 1-1 at 40.3 min. From 30
    min 1-2 is past the meridian, and HELD, because 1-1 can still shoot
    before its flip point; at 40.3 min it is the only panel eligible, and
    its hop is the group's one pier change. After it only panels past the
    meridian are shot: 1-2 alone until 1-1 crosses, then both. The side
    every hop lands on, read from the mount at the moment it lands: west
    and then east, exactly one change, at 1-2's hop (spec 5.7, 6.11, D10).

    MUTANT "no hysteresis" (`_meridian_now` calling every candidate
    eligible, the deadlines kept): RED (observed):
        AssertionError: 47 pier changes: [(30.42, '1-2', 'east'), (30.92, '1-1',
        'west'), (31.42, '1-2', 'east'), (31.92, '1-1', 'west'), (32.42, '1-2',
        'east'), (32.92, '1-1', 'west')]
        assert 47 == 1
    """
    log = SideLog(group_hub)
    plan = meridian_plan(ha_h=-0.9, ha_step_h=0.4, count=100)
    night = await _night(group_hub, monkeypatch, plan,
                         before_run=_straddle_setup(log), wall_s=120.0)
    assert night.done, night.trace[-3:]
    assert night.stored.status == "complete", night.stored.status
    flips = [(round((t - T0) / 60.0, 2), _label(w), s)
             for (t0, w0, s0), (t, w, s) in zip(log.sides, log.sides[1:])
             if s != s0]
    assert log.changes() == 1, f"{log.changes()} pier changes: {flips[:6]}"
    (when, who, side), = flips
    assert who == "1-2" and side == "east", flips
    # The change comes when 1-1 runs out of room before its flip point, and
    # not at 1-2's own crossing, 30 min in.
    one_one = ra_at(-0.9)
    out_of_room = (crossing(one_one) - LEAD_S - 150.0 - 72.0 - T0) / 60.0
    assert out_of_room - 1.0 <= when <= out_of_room + 1.0, (when, out_of_room)
    # Before the change every hop landed west, after it every hop east.
    assert {s for t, _w, s in log.sides if (t - T0) / 60.0 < when} == {"west"}
    assert {s for t, _w, s in log.sides if (t - T0) / 60.0 >= when} == {"east"}
    assert night.said("the mosaic changed pier side at 1-2"), night.lines[-5:]


async def test_a_hop_that_lands_on_the_other_side_is_deferred(group_hub,
                                                              monkeypatch):
    """The side a hop lands on is READ, never assumed (spec 5.6 step 5).
    Both panels of a 1x2 stand well east of the meridian, and the group is
    on the side its first hop read, west. The mount is scripted to report
    the east side whenever it is on 1-2: every hop there contradicts the
    group's side, so 1-2 is deferred ("the mount chose the other pier
    side") on three consecutive passes, shoots nothing, and is set aside
    with the alert naming it and the reading; 1-1 shoots all its frames.
    The control reads the truth and defers nothing.

    MUTANT "no side check" (`_group_pier_check` returning before it reads
    the side): RED (observed):
        AssertionError: 1-2 shot on the side the group is not on: ['L', 'R',
        'L', 'R', 'L', 'R']
        assert ['L', 'R', 'L', 'R', 'L', 'R'] == []
    """
    from astrodeck.devices.base import PierSide
    tel = group_hub.devices["telescope"]
    real = tel.pier_side
    for liar in (True, False):
        box: dict = {}

        async def pier_side(liar=liar):
            side = await real()
            night = box.get("night")
            if (liar and night is not None
                    and night.engine.state.get("target") == _name("1-2")):
                return PierSide.EAST if side == PierSide.WEST else PierSide.WEST
            return side

        def setup(night):
            box["night"] = night

        monkeypatch.setattr(tel, "pier_side", pier_side)
        plan = grid_plan(rows=1, cols=2, meridian_flip=True)
        night = await _night(group_hub, monkeypatch, plan, before_run=setup)
        assert night.done, night.trace[-3:]
        shot = {lb: [f for t, f in night.shots() if t == _name(lb)]
                for lb in ("1-1", "1-2")}
        assert shot["1-1"] == ["L", "R"] * 3, shot
        if not liar:
            assert shot["1-2"] == ["L", "R"] * 3, shot
            assert not night.said("pier side"), night.said("pier side")
            continue
        assert shot["1-2"] == [], (
            f"1-2 shot on the side the group is not on: {shot['1-2']}")
        deferred = night.said("the mount chose the other pier side on 1-2")
        assert len(deferred) == 3, deferred
        aside = [m for _t, lvl, m in night.lines if lvl == "warning"
                 and "set aside for tonight" in m and "1-2" in m]
        assert aside and "it reads east after the goto" in aside[0], aside
        assert "on the west side" in aside[0], aside
        assert [r["target_id"] for r in night.stored.set_aside] == ["p01"]


async def test_a_mount_that_keeps_its_side_past_the_meridian_is_deferred(
        group_hub, monkeypatch):
    """The other half of the side check (spec 5.6 step 5, 5.7, D10): the hop
    that takes the first panel past the meridian IS the group's pier change,
    and it is read back, never assumed. A one-panel mosaic 20 min before
    transit shoots up to its flip point on the side its first hop read,
    west. The mount is scripted as one that does not change sides: it reads
    west whatever it points at. The hop past the meridian then reads the
    side the group is already on, so the mount did NOT flip, and the panel
    is deferred ("the mount stayed on its side past the meridian") on three
    consecutive passes, shoots nothing past the crossing, and is set aside
    tonight with the reading, so the next night takes it up. The group is
    not marked flipped. The control reads the truth: the same hop lands
    east, which is the group's one pier change, and the panel finishes past
    the meridian.

    Without the deferral the group believes it changed sides on a hop that
    read the side it was already on, and disarms the flip latch. The frame
    loop's flip-owed invariant (`_enforce_flip_owed`) is then the only thing
    between the panel and a counterweight-up exposure: it refuses to expose,
    re-slews every half minute for 20 min, and skips the panel for the night
    (seen under the mutant below). The tube is still safe; the group rule is
    what makes it a deferral instead of 20 min of re-slews and a skip.

    MUTANT "stayed side not deferred" (`_group_pier_check`'s past-meridian
    same-side deferral removed, so the hop falls through to marking the
    group flipped): RED (observed, the T18 verifier's run):
        AssertionError: deferred 0 times; instead: ['M31: the mosaic changed
        pier side at 1-1; from now on tonight only panels past the meridian
        are shot, so it changes side once', 'M31 1-1: a meridian flip is owed
        and the mount is still on the west side -- refusing to expose.
        Holding up to 20 min for the flip.']
        assert 0 == 3
    """
    from astrodeck.devices.base import PierSide
    tel = group_hub.devices["telescope"]
    real = tel.pier_side
    ra = ra_at(-20.0 / 60.0)
    for keeps in (True, False):
        async def pier_side(keeps=keeps):
            side = await real()
            return PierSide.WEST if keeps else side

        monkeypatch.setattr(tel, "pier_side", pier_side)
        plan = meridian_plan(cols=1, ha_h=-20.0 / 60.0, ha_step_h=0.0,
                             count=40)
        night = await _night(group_hub, monkeypatch, plan)
        assert night.done, night.trace[-3:]
        cross = crossing(ra)
        before = [c for c in night.captures if c["t"] < cross]
        past = [c for c in night.captures if c["t"] >= cross]
        run = night.engine._group_runs["m31-mosaic"]
        assert before, "premise: the panel shot before its flip point"
        if not keeps:
            assert past and len(before) + len(past) == 40, (len(before),
                                                            len(past))
            assert run.flipped is True
            assert night.said("the mosaic changed pier side at 1-1")
            assert not night.said("stayed on its side")
            assert night.stored.status == "complete", night.stored.status
            continue
        assert len(past) == 0, (
            f"the panel shot {len(past)} frames past the meridian on the side "
            f"it was already on")
        stayed = night.said("the mount stayed on its side past the meridian "
                            "on 1-1")
        instead = [m for _t, _l, m in night.lines
                   if "changed pier side" in m or "flip is owed" in m]
        assert len(stayed) == 3, (
            f"deferred {len(stayed)} times; instead: {instead[:2]}")
        assert not night.said("a meridian flip is owed"), (
            "the frame loop's flip-owed hold engaged: the hop was not deferred")
        assert not night.said("changed pier side")
        assert run.flipped is False
        assert night.engine._group_side["m31-mosaic"] == "west"
        assert [(r["target_id"], r["reason"])
                for r in night.stored.set_aside] == [
            ("p00", "the mount stayed on its side past the meridian on 1-1 "
                    "on 3 consecutive visits: it still reads west after the "
                    "goto past the meridian")], night.stored.set_aside
        assert night.stored.status == "dormant", night.stored.status


# -------------------------------------------------------------- the margin

@pytest.mark.parametrize("ha_min, eligible", [(-8.0, False), (-20.0, True)],
                         ids=["8-min-before-transit", "20-min-control"])
async def test_the_margin_is_the_plans_lead_not_the_learned_one(
        group_hub, monkeypatch, ha_min, eligible):
    """The mount has taught this process that it cannot flip early (#127:
    `_flips_early_by_mount` False), so `_flip_lead_s` is 0. A one-panel
    mosaic 8 min before transit is still NOT eligible: the group's margin
    is the plan's 10 min lead (`_group_flip_margin_s`), and a panel inside
    it has no room before its flip point, so it waits for its crossing and
    is first acquired past the meridian (spec 5.7: the AM5 stops tracking
    4.7 to 7.6 min before transit, so a zero margin runs a visit into the
    mount's own limit). The control, 20 min before transit, has 10 min of
    room and is acquired at once.

    MUTANT "use _flip_lead_s" (`_group_flip_margin_s` returning
    `self._flip_lead_s()`): RED on 8-min-before-transit (observed):
        AssertionError: the panel was acquired 0.0 min in, before its crossing
        at 7.98 min
        assert 0.0 >= 7.9781565308570865
    """
    ra = ra_at(ha_min / 60.0)

    def setup(night):
        tel = group_hub.devices["telescope"]
        night.engine._flips_early_by_mount[tel.name] = False

    plan = meridian_plan(cols=1, ha_h=ha_min / 60.0, ha_step_h=0.0, count=3)
    night = await _night(group_hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    assert night.engine._flip_lead_s() == 0.0, "premise: the trait is learned"
    first = (night.gotos[0][0] - T0) / 60.0
    cross = (crossing(ra) - T0) / 60.0
    if eligible:
        assert first == 0.0, first
        assert not night.said("waits for the meridian"), night.lines[:5]
    else:
        assert first >= cross, (
            f"the panel was acquired {first:.1f} min in, before its crossing "
            f"at {cross:.2f} min")
        # ...a quarter of a minute past it, where the mount's side is not in
        # doubt (`MERIDIAN_SIDE_MARGIN_S`), and no later.
        margin = engine_mod.MERIDIAN_SIDE_MARGIN_S / 60.0
        assert cross + margin <= first <= cross + margin + 0.1, (first, cross)
        assert night.said("1-1 waits for the meridian"), night.lines[:5]


# ------------------------------------------- the visit deadline, the idle

def _record_bounds(night, into: list):
    """Wrap the engine's `_run_visit` to record every bound it is given, as
    ``(target name, VisitBound)``. A pass-through: the visit runs as it
    would."""
    real = night.engine._run_visit

    async def run_visit(ti, target, visit):
        into.append((target.name, visit))
        return await real(ti, target, visit)

    night.mp.setattr(night.engine, "_run_visit", run_visit)


async def test_a_visit_ends_at_the_frame_boundary_before_its_flip_point(
        group_hub, monkeypatch):
    """A sequential one-panel mosaic of 60 L frames, 30 min before transit:
    the panel runs to completion or to its flip point, the plan's lead
    before transit, whichever comes first (spec 5.1: sequential keeps the
    meridian rule; 5.3, 5.7). Its first visit carries the flip point as its
    deadline and ENDS AT THE FRAME BOUNDARY BEFORE IT: every frame of the
    visit is over by the deadline, the next one (30 s, the 12 s overhead
    seed, the 30 s margin) would not have been, and no frame straddles the
    flip point. The panel then waits for its crossing and finishes past the
    meridian on the far side.

    MUTANT "deadline dropped" (`_visit_panel` passing no ``deadline_ts`` to
    the sequential bound, which then runs the panel to completion): RED
    (observed):
        AssertionError: no visit carried a deadline: []
        assert None is not None
    MUTANT "deadline in the countdown's hours" (`_meridian_now` taking
    ``deadline_h`` as clock hours): RED (observed, the T18 verifier's run):
        AssertionError: 4.914780616760254
        assert 4.914780616760254 < 3.0
    """
    bounds: list = []
    ra = ra_at(-0.5)
    plan = meridian_plan(cols=1, ha_h=-0.5, ha_step_h=0.0, count=60,
                         group_kw={"mode": "sequential"})
    night = await _night(group_hub, monkeypatch, plan,
                         before_run=lambda n: _record_bounds(n, bounds))
    assert night.done, night.trace[-3:]
    assert night.stored.status == "complete", night.stored.status
    deadline = next((vb.deadline_ts for _n, vb in bounds
                     if vb.deadline_ts is not None), None)
    assert deadline is not None, f"no visit carried a deadline: {bounds}"
    flip_point = crossing(ra) - LEAD_S
    # The deadline is the flip point in clock seconds: `_meridian_now`
    # converts the countdown's hours, and what is left is the lead's own
    # 0.27% (1.6 s of 600). Taken as clock hours it lands 4.9 s late here.
    assert abs(deadline - flip_point) < 3.0, (deadline - flip_point)
    first_visit = [c for c in night.captures if c["visit"] == 1]
    assert first_visit, night.captures[:2]
    last_end = max(c["t"] + c["exposure_s"] for c in first_visit)
    assert last_end <= deadline, (last_end - deadline)
    assert last_end + 30.0 + 12.0 + 30.0 > deadline, (
        f"the visit ended {deadline - last_end:.0f} s before its deadline, "
        f"with room for another frame")
    straddled = [c for c in night.captures
                 if c["t"] < flip_point < c["t"] + c["exposure_s"]]
    assert straddled == [], straddled
    assert len(first_visit) < 60, len(first_visit)
    assert night.said("the visit ends here"), night.lines[-5:]
    later = [c for c in night.captures if c["visit"] > 1]
    assert later and min(c["t"] for c in later) >= crossing(ra), (
        "the panel came back before its crossing")


@pytest.mark.parametrize("phase_s", [0.0, 12.0, 18.0],
                         ids=["control", "phase-12s", "phase-18s"])
async def test_a_long_visit_ends_before_the_flip_gate_acts(
        group_hub, monkeypatch, phase_s):
    """A sequential one-panel mosaic 70 min before transit, 200 frames of L:
    an hour of room before its flip point, so one long visit. The visit's
    deadline and the frame loop's flip gate must draw the same line. The
    gate re-reads the countdown at every frame, and the countdown counts
    hour angle, 0.27% faster than the clock, so a deadline taken as clock
    hours lands up to 10 s past the gate's line by the end of an hour. At
    some phases of the frame cadence the last frame the visit let through
    then met the gate: it held for the flip point, re-slewed (a flip
    attempt 10 min before transit, on a mount that cannot flip that early),
    and the frame opened past the flip point. Converted to clock seconds
    (`_meridian_now`), the visit ends first at every phase: no flip is
    attempted before the crossing, and every frame of the first visit is
    over by the flip point. The phases are offsets of the panel's hour
    angle; "control" is one where the two lines agreed even before.

    MUTANT "deadline in the countdown's hours" (`_meridian_now` taking
    ``deadline_h`` as clock hours, as it did): RED on phase-12s and
    phase-18s, the control green (observed, the T18 verifier's run):
        AssertionError: a flip was attempted mid-visit before the crossing:
        ['meridian flip: stopping guiding and re-slewing']
        assert ['meridian fl...lip complete'] == []
    """
    ha = -(70.0 * 60.0 + phase_s) / 3600.0
    ra = ra_at(ha)
    plan = meridian_plan(cols=1, ha_h=ha, ha_step_h=0.0, count=200,
                         group_kw={"mode": "sequential"})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    cross = crossing(ra)
    early = [m for t, _l, m in night.lines
             if t < cross and m.startswith("meridian flip")]
    assert early == [], (
        f"a flip was attempted mid-visit before the crossing: {early[:1]}")
    flip_point = cross - LEAD_S
    first_visit = [c for c in night.captures if c["visit"] == 1]
    assert len(first_visit) > 60, len(first_visit)
    late = [round(c["t"] + c["exposure_s"] - flip_point, 1)
            for c in first_visit if c["t"] + c["exposure_s"] > flip_point]
    assert late == [], (
        f"frames of the first visit end past the flip point: {late}")
    assert night.said("the visit ends here"), night.lines[-5:]
    assert night.stored.status == "complete", night.stored.status


async def test_the_measured_preflip_idle_matches_the_a4_formula(
        group_hub, monkeypatch):
    """The pre-flip idle, measured on the clocked simulator, against
    Appendix A.4 (spec 5.7 cost 1, `group_rules.preflip_idle_cut_h`):
    ``max(0, lead + hop + f - span)``, where nothing in the group is
    eligible from the moment the last pre-flip panel runs out of room until
    the first panel crosses.

    A 1x2 of L, count 30 a panel, one frame a visit: 1-2 crosses 20 min in
    and 1-1, 0.1 h of RA east, 26 min in. The formula, with the plan's 10
    min lead, the 150 s hop estimate (the fake clock's hops take no time, so
    the seed stands), the engine's own overhead EMA and span = 0.1 h x
    0.9972696: 7.7 min. MEASURED: from the end of the last frame before the
    first crossing to the hop after it. The two agree within one frame,
    which is the formula's own grain: a visit is chosen while the room lasts
    and its frame then runs up to one frame past that moment.

    The hop after the crossing is the group's pier change at once, with no
    deferral: the rule counts a panel past the meridian only a quarter of a
    minute after its crossing (`MERIDIAN_SIDE_MARGIN_S`), where the side a
    goto lands on is not in doubt. That quarter of a minute is inside the
    one-frame agreement: measured 463.8 s, formula 463.0 s.

    MUTANT "no room reserved" (`_meridian_now` passing ``hop_h=0``, a
    pre-flip panel chosen with room for its frame only): RED (observed):
        AssertionError: measured 313 s, formula 463 s, one frame 42 s
        assert 149.5670490148213 <= 42.0
    MUTANT "no side margin" (`_meridian_now` passing ``crossed_h=0``, so a
    panel counts as past the meridian at the crossing itself, where the
    simulator's unsynced goto lands 7 s of RA east of it): RED (observed):
        AssertionError: ['M31: the mount stayed on its side past the meridian on
        1-2: it still reads west after the goto past the meridian; re...
        meridian on 1-2: it still reads west after the goto past the meridian;
        retried on the next pass (2 of 3 consecutive)']
    """
    from astrodeck.sequence.group_rules import (preflip_idle_cut_h,
                                                ra_span_solar_h)
    ras = [ra_at(-0.4333), ra_at(-0.3333)]
    ema: list[float] = []
    box: dict = {}

    def setup(night):
        box["night"] = night

    def on_capture(rec):
        ema.append(box["night"].engine._overhead_ema)

    plan = meridian_plan(ha_h=-0.4333, ha_step_h=0.1, count=30)
    night = await _night(group_hub, monkeypatch, plan, before_run=setup,
                         on_capture=on_capture)
    assert night.done, night.trace[-3:]
    first_cross = min(crossing(r) for r in ras)
    post_hops = [t for t, _who in night.gotos if t >= first_cross]
    assert post_hops, night.gotos[-3:]
    hop_after = min(post_hops)
    before = [c for c in night.captures if c["t"] < first_cross]
    last_end = max(c["t"] + c["exposure_s"] for c in before)
    measured = hop_after - last_end
    k = len(before) - 1
    f_s = 30.0 + ema[k] + 30.0
    formula = preflip_idle_cut_h(lead_h=LEAD_S / 3600.0,
                                 hop_h=150.0 / 3600.0, f_h=f_s / 3600.0,
                                 span_h=ra_span_solar_h(ras)) * 3600.0
    one_frame = 30.0 + ema[k]
    assert formula > 6 * 60.0, f"premise: an idle to measure ({formula:.0f} s)"
    assert abs(measured - formula) <= one_frame, (
        f"measured {measured:.0f} s, formula {formula:.0f} s, one frame "
        f"{one_frame:.0f} s")
    assert night.said("wait for the meridian"), night.lines[-5:]
    # The hop after the crossing lands past the meridian at once: it is the
    # group's pier change, not a deferral for a side still in doubt.
    assert not night.said("stayed on its side"), night.said("stayed")
    assert [w for t, w in night.gotos if t >= first_cross][:1] == [
        _name("1-2")], night.gotos[-3:]
    assert night.said("the mosaic changed pier side at 1-2")


# ----------------------------------------------- the moment is the site

async def test_a_meridian_wait_reaches_a_viewer_as_nothing_at_all(
        group_hub, monkeypatch):
    """The meridian wait of the idle case above, put through T8's filters as
    a VIEWER and as an OPERATOR would read it (spec 6.9, 5.10; #166, #19).

    A line or a publish whose MOMENT a site computation set carries
    ``site_derived``: the wait's start (a panel ran out of room before its
    flip point), its end (a crossing), the hop that ends it and the group's
    pier change. ``state.group.meridian_wait`` is true from the wait's
    start through that hop until its first exposure, and every sequence
    publish in between is flagged too. For a viewer, the log ring
    (``_redact_log_rows_for``, what ``/api/logs`` serves), the WS lane
    (``_redact_ws_event``) and the state route (``_redact_sequence_for``)
    then carry none of it: no flagged line, no publish from inside the
    wait, and no ``panel`` or ``pass`` while ``meridian_wait`` is true. The
    known positive is in the same case: an operator reads every one of
    those lines.

    MUTANT "no flag" (`_note_meridian_waits` logging both edges of the wait
    without ``site_derived``): RED (observed):
        AssertionError: a viewer's /api/logs carries ['M31: 1-2, 1-1 wait for
        the meridian, so the mosaic changes pier side once', 'M31: the meridian
        wait is over; 1-2 is next']
    MUTANT "meridian_wait never set" (`_group_state` publishing
    ``meridian_wait: False`` always): RED (observed):
        AssertionError: state.group.meridian_wait was never true
        assert []
    """
    from astrodeck.api.redact import (_redact_log_rows_for,
                                      _redact_sequence_for, _redact_ws_event)
    from astrodeck.auth import principal_for_role
    viewer = principal_for_role("viewer")
    operator = principal_for_role("operator")

    plan = meridian_plan(ha_h=-0.4333, ha_step_h=0.1, count=30)
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]

    logs = [e for e in night.events if e["type"] == "log"
            and e["data"].get("source") == "sequence"]
    seq = [e for e in night.events if e["type"] == "sequence"]
    timed = ("wait for the meridian", "the meridian wait is over",
             "changed pier side")
    seen = _redact_log_rows_for(logs, viewer)
    leaked = [r["data"]["message"] for r in seen
              if any(w in r["data"]["message"] for w in timed)]
    assert leaked == [], f"a viewer's /api/logs carries {leaked}"
    hop = [e for e in logs if e["data"]["message"] == "target 2/2: M31 1-2"
           and e["data"].get("site_derived")]
    assert len(hop) == 1, "the hop that ends the wait was not flagged once"
    assert not any(r is hop[0] for r in seen), "the viewer read the hop"
    flagged = [m for _t, _lvl, m in night.flagged]
    for needle in ("1-2, 1-1 wait for the meridian",
                   "the meridian wait is over; 1-2 is next",
                   "the mosaic changed pier side at 1-2"):
        assert any(needle in m for m in flagged), (needle, flagged)
    held = [r["data"]["message"]
            for r in _redact_log_rows_for(logs, operator)]
    assert all(m in held for m in flagged), "the operator lost a line"

    # The wait, as the run published it: from its first state to the first
    # exposure after it, every state says so, and every one is flagged.
    waiting = [i for i, e in enumerate(seq)
               if (e["data"].get("group") or {}).get("meridian_wait")]
    assert waiting, "state.group.meridian_wait was never true"
    lo, hi = waiting[0], waiting[-1]
    window = seq[lo:hi + 1]
    assert all((e["data"].get("group") or {}).get("meridian_wait")
               for e in window if e["data"].get("group") is not None), [
        e["data"].get("detail") for e in window]
    assert all(e["data"].get("site_derived") for e in window), [
        (e["data"].get("detail"), e["data"].get("site_derived"))
        for e in window if not e["data"].get("site_derived")][:3]
    assert window[0]["ts"] < min(crossing(r) for r in
                                 (ra_at(-0.4333), ra_at(-0.3333))), (
        "premise: the wait began before the crossing")
    shots_in = [c for c in night.captures
                if window[0]["ts"] < c["t"] < window[-1]["ts"]]
    assert shots_in == [], shots_in
    # A viewer: nothing from inside the wait on the WS lane, and never the
    # panel or the pass while the wait is on, on any seam.
    ws = [_redact_ws_event(e, viewer) for e in window]
    assert ws == [None] * len(window), [
        w["data"].get("detail") for w in ws if w is not None][:3]
    for e in seq:
        for served in (_redact_ws_event(e, viewer),
                       {"data": _redact_sequence_for(e["data"], viewer)}):
            g = (served or {}).get("data", {}).get("group") or {}
            if g.get("meridian_wait"):
                assert "panel" not in g and "pass" not in g, g
    # ...and the operator does get the panel through the wait.
    assert any((_redact_sequence_for(e["data"], operator).get("group") or {})
               .get("panel") for e in window), "premise: a panel to withhold"


# ------------------------------------------- when the prediction is wrong

async def test_a_hop_that_outruns_its_estimate_is_not_a_visit(group_hub,
                                                             monkeypatch):
    """A one-panel mosaic 15 min before transit: 5 min of room before its
    flip point, enough for the 150 s hop estimate and a 72 s frame, so it
    is chosen. The hop then takes 400 s (a slow centring, a guider that
    walked a calibration), the flip point comes first, and the visit ends
    at its deadline with NOTHING shot. That is not a visit to the pass
    rules (`_visit_panel`): counted as one, it is a pass of no exposures and
    no deferrals, and the next pass boundary would set the whole mosaic
    aside for the night (spec 5.1 item 1). Instead the panel waits for its
    crossing and is shot past the meridian.

    MUTANT "a deadline miss counts as a visit" (`_visit_panel` without its
    not-a-visit branch): RED (observed):
        AssertionError: [{'night': '2026-09-01', 'reason': 'a full pass over 1
        panels took no exposures; setting the mosaic aside for tonight',
        'step_id': None, 'target_id': 'p00'}]
    """
    box: dict = {}

    def setup(night):
        box["night"] = night
        real = group_hub.goto_and_center

        async def slow_goto(*a, **kw):
            result = await real(*a, **kw)
            await night._park(400.0)
            return result

        night.mp.setattr(group_hub, "goto_and_center", slow_goto)

    ra = ra_at(-0.25)
    plan = meridian_plan(cols=1, ha_h=-0.25, ha_step_h=0.0, count=2,
                         filters=("L", "R"))
    night = await _night(group_hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    assert night.stored.set_aside == [], night.stored.set_aside
    assert night.stored.status == "complete", night.stored.status
    assert night.captures and min(c["t"] for c in night.captures) >= \
        crossing(ra), "a frame was shot before the crossing"
    assert night.said("reached its flip point before a frame could fit"), (
        night.lines[:6])


async def test_a_flip_the_latch_measured_mid_visit_flips_the_group(
        group_hub, monkeypatch):
    """The backstop (spec 5.7): when a prediction is wrong and the flip latch
    flips a member mid-visit, that MEASURED flip is the group's one pier
    change. Made wrong here on purpose: the mount is scripted as one that
    CAN flip early (a goto within the plan's 10 min lead of transit lands on
    the far side, and the side stays where a goto left it, as on a real
    GEM), and the group's margin is forced to zero, so a panel 15 min before
    transit is visited up to its crossing. The latch's lead-time attempt,
    10 min before transit, lands on the far side: the group is flipped,
    said once and flagged ``site_derived``, and the panel is next acquired
    past the meridian on that side.

    MUTANT "the latch's flip goes unnoticed" (the group hook in
    `_maybe_meridian_flip` removed): RED (observed):
        AssertionError: ['M31: the mount chose the other pier side on 1-1: it
        reads east after the goto, and the M31 mosaic is on the west sid...: it
        reads east after the goto, and the M31 mosaic is on the west side;
        retried on the next pass (2 of 3 consecutive)']
    """
    from astrodeck.catalog.coords import hour_angle_h
    from astrodeck.devices.base import PierSide
    tel = group_hub.devices["telescope"]

    def early(ra):
        # West of the pier until 10.5 min before transit, then east.
        ha = hour_angle_h(ra, LON, box["night"].clock.t)
        return PierSide.EAST if ha > -10.5 / 60.0 else PierSide.WEST

    box: dict = {"landed": PierSide.WEST}

    def setup(night):
        box["night"] = night
        real_slew = tel.slew

        async def slew(ra_hours, dec_deg):
            await real_slew(ra_hours, dec_deg)
            box["landed"] = early(ra_hours)

        async def pier_side():
            return box["landed"]

        async def destination_pier_side(ra_hours, dec_deg):
            return early(ra_hours)

        night.mp.setattr(tel, "slew", slew)
        night.mp.setattr(tel, "pier_side", pier_side)
        night.mp.setattr(tel, "destination_pier_side", destination_pier_side)
        night.mp.setattr(night.engine, "_group_flip_margin_s", lambda: 0.0)

    ra = ra_at(-0.25)
    plan = meridian_plan(cols=1, ha_h=-0.25, ha_step_h=0.0, count=40)
    night = await _night(group_hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    flipped = [t for t, _l, m in night.lines
               if "meridian flip complete (pier side west -> east)" in m]
    assert flipped and flipped[0] < crossing(ra), (
        "premise: the latch flipped the panel before its crossing")
    # A group that did not learn of the flip judges the next hop against
    # the side it no longer is on, and defers a panel that is where it
    # should be.
    assert not night.said("the mount chose the other pier side"), (
        night.said("the mount chose the other pier side"))
    said = night.said("the flip of 1-1 changed the mosaic's pier side")
    assert len(said) == 1, [m for _t, _l, m in night.lines if "pier" in m]
    assert any("the flip of 1-1" in m for _t, _l, m in night.flagged)
    assert not night.said("the mosaic changed pier side at 1-1")
    assert night.engine._group_runs["m31-mosaic"].flipped is True
    assert night.engine._group_side["m31-mosaic"] == "east"


# -------------------------------------- the band just past the crossing

@pytest.mark.parametrize("flipped", [False, True], ids=["before", "flipped"])
def test_a_panel_counts_as_past_the_meridian_only_past_the_band(flipped):
    """`group_rules.meridian_eligibility`'s ``crossed_h`` (T18): a panel
    counts as past the meridian only ``crossed_h`` after its crossing. In
    the band it waits, waking where the band ends; beyond it, it is
    eligible, flipped or not. A panel with no room before its flip point
    wakes at the band's end too. With the default of 0 there is no band,
    and the rule is the spec's as written (the control).

    MUTANT "the band only before the flip" (the flipped branch testing
    ``h_p <= 0.0``): RED on flipped (observed):
        AssertionError: MeridianVerdict(eligible=True, deadline_h=None,
        wake_h=None, reason='past the meridian, on the side the group flipped
        to')
        assert True is False
    MUTANT "the band only after the flip" (the not-flipped branch testing
    ``h_p <= 0.0``): RED on before (observed):
        AssertionError: MeridianVerdict(eligible=True, deadline_h=None,
        wake_h=None, reason='past the meridian, and no panel before the
        meridian can shoot')
        assert True is False
    """
    from astrodeck.sequence.group_rules import (PanelMeridian,
                                                meridian_eligibility)
    s = 1.0 / 3600.0
    panels = {"in_band": PanelMeridian(h_p=-10 * s, f_h=72 * s),
              "past": PanelMeridian(h_p=-20 * s, f_h=72 * s)}
    if not flipped:
        panels["no_room"] = PanelMeridian(h_p=300 * s, f_h=72 * s)
    got = meridian_eligibility(panels, lead_h=600 * s, hop_h=150 * s,
                               flipped=flipped, meridian_flip=True,
                               crossed_h=15 * s)
    assert got["in_band"].eligible is False, got["in_band"]
    assert got["in_band"].wake_h == pytest.approx(5 * s), got["in_band"]
    assert got["past"].eligible is True, got["past"]
    if not flipped:
        assert got["no_room"].eligible is False
        assert got["no_room"].wake_h == pytest.approx(315 * s)
    plain = meridian_eligibility(panels, lead_h=600 * s, hop_h=150 * s,
                                 flipped=flipped, meridian_flip=True)
    assert plain["in_band"].eligible is True, plain["in_band"]


# ------------------------------------------------------------- the wake pad

async def test_a_wake_computed_to_the_edge_is_padded_past_it(group_hub,
                                                              monkeypatch):
    """``MERIDIAN_WAKE_PAD_S`` (T18 item 4, #301): a wake the rule computes
    right at the edge of ``MERIDIAN_SIDE_MARGIN_S`` must still land strictly
    after it, never at or before it. Without the pad, a countdown read as
    the smallest float above the edge converts to a wake indistinguishable
    from ``now`` (the increment is far below a Unix timestamp's own
    precision, so the addition rounds away to nothing), and the panel is
    held again for a sliver: the do-while wait (`_wait_until`) would then
    take a run of near-zero sleeps instead of the one real wait the rule
    meant.

    No real RA lands `_meridian_now`'s hour-angle math on that exact edge on
    demand -- it is the reason #301 stayed open as long as it did -- so this
    calls `_meridian_now` directly with `schedule.hours_to_meridian_flip`
    stubbed to the smallest representable float above
    ``-MERIDIAN_SIDE_MARGIN_S`` hours: "a wake computed to the edge itself",
    in the constant's own comment.

    MUTANT "no pad" (``MERIDIAN_WAKE_PAD_S`` set to ``0.0``): RED (observed):
        AssertionError: the wake landed AT the edge, not after it: 0.0
        assert 0.0 >= 0.5
    """
    plan = meridian_plan(cols=1, ha_h=-20.0 / 60.0, ha_step_h=0.0, count=2)
    night = Night(group_hub, monkeypatch, coords_clock=True)
    try:
        night.engine.plan = plan
        group = plan.groups[0]
        target = plan.targets[0]
        run = GroupRun(members={target.id: "1-1"}, max_failed_visits=3)
        # The smallest double greater than the band's own edge: the
        # countdown reading the edge "a float's width before it" the
        # constant's comment describes, constructed exactly rather than
        # chased through the real trig pipeline.
        crossed_h = engine_mod.MERIDIAN_SIDE_MARGIN_S / 3600.0
        edge = math.nextafter(-crossed_h, 0.0)
        monkeypatch.setattr(schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: edge)
        elig = await night.engine._meridian_now(group, run, [target], T0)
    finally:
        await night.close()
    e = elig[target.id]
    assert e.eligible is False, e
    assert e.held == "meridian", e
    assert e.wake_ts - T0 >= 0.5, (
        f"the wake landed AT the edge, not after it: {e.wake_ts - T0}")
