"""The golden group trace at every hour of the wall clock (#320, #298).

#320: `test_group_rotation.py`'s golden trace failed at some times of day
and passed at others, on a pristine tree. The night runs on the harness's
fake clock, but ``catalog.coords``, where the engine's meridian countdown
and the simulator mount's pier side read the hour angle, was left on the
WALL clock. Whenever the hour of day put NGC 7331 inside its flip lead at
the fixture longitude, the countdown (frozen, since the wall clock barely
moves while a night of hours runs) said the flip point was a frame away,
and the engine held for it until the harness's 16 h fake horizon. The
window moves about 4 min earlier each day, so a run that passed proved
nothing about the other half of the day.

Every case here shifts EVERY ``time.time`` in the process by an offset,
computed from the real clock when the case runs, so it always lands where
its name says: the event bus, the session store, the hub and anything else
that reads the wall clock read another hour of the day. The night itself
runs on the harness's clock (tests/_group_harness.py), which now owns
``catalog.coords`` for every night. "flip-hold" is #320's window, NGC 7331
12 min before its transit at the fixture longitude by the wall clock:
inside the plan's 10 min lead plus one 180 s frame.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/, never in the shared tree (#254).
"""
from __future__ import annotations

import time

import pytest

from _group_harness import (GOLDEN_T0, LON, T0, Night, close_night_hub,
                            golden_flow_plan, group_hub, group_store,
                            night_hub, ra_at)  # noqa: F401
from astrodeck.sequence import schedule
from test_group_rotation import (_expected_trace, _first_difference,
                                 _golden_as_recorded)

#: The process's own wall clock, taken before any case shifts it.
_REAL_TIME = time.time
#: Hour angle runs this much faster than the clock.
SIDEREAL = 1.0027379093
SIDEREAL_DAY_S = 86400.0 / SIDEREAL

#: Where each shift of the wall clock puts NGC 7331: hours of hour angle
#: before its transit at the fixture longitude (negative: past it).
WALL = {
    "flip-hold": 12.0 / 60.0,
    "just-past-transit": -5.0 / 60.0,
    "six-hours-east": 6.0,
    "six-hours-west": -6.0,
}


def _golden_ra() -> float:
    (target,) = golden_flow_plan().targets
    return target.ra_hours


def wall_offset_s(to_transit_h: float, ra: float | None = None) -> float:
    """The shift of the wall clock, within half a sidereal day of the real
    one, that puts ``ra`` (NGC 7331 by default) ``to_transit_h`` hours of
    hour angle before its transit at the fixture longitude."""
    ra = _golden_ra() if ra is None else ra
    d = (schedule.hours_to_meridian_flip(ra, LON, _REAL_TIME())
         - to_transit_h) * 3600.0 / SIDEREAL
    return ((d + SIDEREAL_DAY_S / 2.0) % SIDEREAL_DAY_S) - SIDEREAL_DAY_S / 2.0


def shift_the_wall_clock(monkeypatch, offset_s: float) -> None:
    """Every ``time.time()`` in the process reads ``offset_s`` after the
    real clock (a later call replaces an earlier one; they do not add)."""
    monkeypatch.setattr(time, "time", lambda: _REAL_TIME() + offset_s)


def _where(ra: float) -> float:
    """Where the (shifted) wall clock puts ``ra``: hours before transit."""
    return schedule.hours_to_meridian_flip(ra, LON, time.time())


def stop_the_status_poll(hub) -> None:
    """Stop the hub's 2 s status poll and drop its meridian cache (#368).

    The poll runs on REAL time, and its cache, ``hub.last_meridian``, feeds
    the engine's ``live`` chip (inside ``meridian_flip_warn_min``, 15 min)
    and the ETA's flip cost (a flip due before the run ends). A night
    inside either window then publishes a trace that depends on where, in
    real time, the poll happened to land: the night crossing the meridian
    below, run twice on fresh hubs at one hour of the wall clock, differed
    in two tries of three, by a ``live`` key one run published at 10602 s
    and the other did not. The golden night never enters either window (it
    ends 34.6 min before its flip point), so its trace does not depend on
    the poll."""
    task = hub._status_task
    if task is not None and not task.done():
        task.cancel()
    hub.last_meridian = None


async def _run(hub, monkeypatch, plan, t0: float) -> Night:
    night = Night(hub, monkeypatch, t0=t0)
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    return night


@pytest.fixture
def wall(request, monkeypatch):
    """Shift the wall clock to ``WALL[request.param]``. A case asks for this
    ahead of the hub, so the hub is connected under the shifted clock."""
    shift_the_wall_clock(monkeypatch, wall_offset_s(WALL[request.param]))
    return request.param


@pytest.mark.parametrize("wall", list(WALL), indirect=True)
async def test_the_golden_night_is_the_same_at_every_hour_of_the_wall_clock(
        wall, group_hub, monkeypatch):
    """The golden flow plan's night, from ``GOLDEN_T0``, is the recorded
    golden trace byte for byte (fixtures/group_golden_trace.json, the one
    `test_group_rotation.py` pins) at four hours of the wall clock: inside
    #320's window, just past NGC 7331's transit, and six hours either side.

    MUTANT "sim reads the wall clock" (the harness no longer putting
    ``catalog.coords``, the clock the simulator mount's hour angle and the
    engine's countdown read, on the night's clock: the state before #320's
    fix): RED on flip-hold, with #320's own failure at #320's own instant,
    and on just-past-transit, where the frozen countdown says the flip is
    due at once (observed):
        AssertionError: premise: the run ended: [[57585.0, 'state', {'keys':
        ['detail', 'plan_name', 'progress', 'session', 'sky', 'state',
        'target', 'target_index'], 'state': 'running', 'detail': 'holding for
        the meridian flip point', 'target': 'NGC 7331', ...
        AssertionError: line 12:
            got  [0.0,"state",{"detail":"meridian flip","keys":["detail",
        "plan_name","progress","session","sky","state","target",...
            want [0.0,"state",{"detail":"NGC 7331: L 60s  [1/15]","keys":[
        "detail","plan_name","progress","session","sky","state","target",...
    MUTANT "sim reads the wall clock, at the device" (below): this case
    stays green on all four. The golden night never nears its flip point,
    so nothing in it acts on the mount's side; the two cases below are the
    ones that see it.
    """
    assert abs(group_hub.sim_rig._guide_epoch_s - time.time()) < 60.0, (
        "premise: the hub was connected under the shifted wall clock")
    assert abs(_where(_golden_ra()) - WALL[wall]) < 1.0 / 60.0, (
        "premise: the wall clock puts NGC 7331 where the case says")
    night = await _run(group_hub, monkeypatch, _golden_as_recorded(),
                       GOLDEN_T0)
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    got, want = night.trace_text(), _expected_trace("golden")
    assert got == want, _first_difference(got, want)


@pytest.mark.parametrize("where", ["flip-hold", "just-past-transit"])
async def test_a_night_across_the_meridian_is_the_same_at_every_hour(
        group_store, monkeypatch, where):
    """The golden flow plan from ``T0`` crosses NGC 7331's meridian 180 min
    into its 195. The engine holds for the flip point and makes the
    lead-time attempt, which the simulator answers on the same side (it
    takes its side from the hour angle at the goto, as the AM5 does); the
    retry at the crossing lands inside the 7 s of RA an unsynced goto falls
    east, and stays west too (#366); the flip-owed hold's re-slew 30 s later
    takes the east side. The mount's side is read at every step of that, so this
    night shows whether the side follows the night's clock or the wall's.
    Run on a fresh hub at six hours east by the wall clock, and again on
    another at ``where``, the two traces are the same, flip and all. The
    hub's real-time status poll is stopped for both (`stop_the_status_poll`).

    MUTANT "sim reads the wall clock, at the device"
    (`SimTelescope._side_for_ra` reading ``hour_angle_h(ra_hours, lon,
    time.time())``, the process's own clock, which the harness does not
    own): RED on both, at the premise: at six hours east by the wall clock
    the mount takes the west side at every goto, so the reference night
    never flips (observed):
        AssertionError: premise: the night flips
        assert 'meridian flip complete (pier side west -> east)' in '[0.0,
        "state",{"detail":"starting plan ...
    """
    traces = []
    for name in ("six-hours-east", where):
        shift_the_wall_clock(monkeypatch, wall_offset_s(WALL[name]))
        assert abs(_where(_golden_ra()) - WALL[name]) < 1.0 / 60.0, (
            f"premise: the wall clock puts NGC 7331 at {name}")
        hub, popped = await night_hub(monkeypatch)
        stop_the_status_poll(hub)
        try:
            night = await _run(hub, monkeypatch, _golden_as_recorded(), T0)
        finally:
            await close_night_hub(hub, popped)
        assert night.done, f"premise: the run ended: {night.trace[-3:]}"
        traces.append(night.trace_text())
    assert "meridian flip complete (pier side west -> east)" in traces[0], (
        "premise: the night flips")
    assert traces[1] == traces[0], _first_difference(traces[1], traces[0])


@pytest.mark.parametrize("to_transit", [-0.25, 0.75, 0.25],
                         ids=["wall-says-past-transit",
                              "wall-says-before-transit", "wall-agrees"])
async def test_under_a_night_the_sim_mount_takes_its_side_from_its_clock(
        group_hub, monkeypatch, to_transit):
    """Under a night, a goto to a target 15 min before its transit BY THE
    NIGHT'S CLOCK lands west of the pier, and the destination oracle calls
    a target 15 min past it east, whatever the wall clock says. Each half
    has a wall clock that would turn it: with the wall clock putting the
    first target 15 min PAST its transit, a mount reading the wall clock
    lands east; with it putting the second target 15 min BEFORE its transit
    (the first 45 min before), a destination oracle reading the wall clock
    calls it west. Neither shift reaches the other half, which the wall
    clock leaves on the night's side. The control puts the wall clock where
    the night does, where the two clocks agree and no mutant of the clock
    can change an answer.

    MUTANT "sim reads the wall clock, at the device": RED on
    wall-says-past-transit and on wall-says-before-transit, the control
    green (observed):
        AssertionError: a goto 15 min before transit by the night's clock
        landed east of the pier
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
        AssertionError: the destination oracle called a target 15 min past
        transit by the night's clock west
        assert 'west' == 'east'
          - east
          ?  -
          + west
          ? +
    MUTANT "the destination reads the wall clock"
    (`SimTelescope.destination_pier_side` reading ``hour_angle_h(ra_hours,
    lon, time.time())`` itself, the latch left on ``catalog.coords``): RED
    on wall-says-before-transit only, the second failure above, verbatim.
    Before wall-says-before-transit was added this mutant left every case
    in this file green: at wall-says-past-transit the wall clock puts the
    second target past its transit too, so its half could not fail.
    MUTANT "the latch reads the wall clock" (`_latch_pier_side` reading
    ``time.time()`` itself, the destination left on ``catalog.coords``): RED
    on wall-says-past-transit only, the first failure above, verbatim.
    """
    east_ra, west_ra = ra_at(-0.25), ra_at(0.25)
    shift_the_wall_clock(monkeypatch, wall_offset_s(to_transit, ra=east_ra))
    assert abs(_where(east_ra) - to_transit) < 1.0 / 60.0, "premise"
    night = Night(group_hub, monkeypatch)
    try:
        tel = group_hub.devices["telescope"]
        await tel.slew(east_ra, 40.0)
        side = (await tel.pier_side()).value
        assert side == "west", (
            f"a goto 15 min before transit by the night's clock landed "
            f"{side} of the pier")
        dest = (await tel.destination_pier_side(west_ra, 40.0)).value
        assert dest == "east", (
            f"the destination oracle called a target 15 min past transit by "
            f"the night's clock {dest}")
    finally:
        await night.close()


async def test_a_night_half_on_the_wall_clock_is_refused(group_hub,
                                                        monkeypatch):
    """`Night` refuses ``coords_clock=False``, before it patches anything:
    a night whose engine and simulator mount read the hour angle on the
    wall clock is #320 itself, and the keyword survives only because eight
    test files still pass True.

    MUTANT "the old default back" (the refusal removed, so False leaves
    ``catalog.coords`` on the wall clock): RED (observed):
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError, match="#320"):
        Night(group_hub, monkeypatch, coords_clock=False)
