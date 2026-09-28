"""The simulator mount keeps the pier side a goto chose (#298; spec 5.7, I-38,
Revision 1's last row).

A German equatorial changes side only through a slew, which is the whole
point of a meridian flip. The simulator mount used to answer `pier_side`
from the hour angle of wherever it pointed NOW, so a target it was merely
tracking crossed the meridian and the mount reported the far side with no
slew: every meridian test graded a mount that flips itself, and the
flip-owed invariant (`_enforce_flip_owed`), a missed flip and a mount
tracking past the meridian on the pre-flip side could not be staged on it.

Now `SimTelescope` latches its side at each slew, sync, park and unpark,
from the hour angle at that moment (the RULE stays
`coords.pier_side_for_hour_angle`, the one copy), and `pier_side` reports
the latched side. `destination_pier_side` keeps the hour-angle rule: it is
the side a goto there WOULD pick, which is what the pre-slew guard asks.

Each case runs the simulator mount on its own, at the harness's fixture
site (40 N 74 W, not anybody's rig), with ``catalog.coords`` on a clock the
case moves by hand, the clock the mount's hour angle reads. Each names the
mutant it was shown RED under, with the failure observed, verbatim. Every
mutant was applied in a private scratch copy of server/, never in the
shared tree (#254).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.catalog.coords as coords_mod
from _group_harness import LON, T0, _Clock, group_store, ra_at  # noqa: F401
from astrodeck.devices.base import PierSide
from astrodeck.devices.sim import SimRig, SimTelescope
from astrodeck.sequence import schedule

#: Hour angle runs this much faster than the clock.
SIDEREAL = 1.0027379093
DEC = 40.0


def crossing(ra: float) -> float:
    """The clock time at which ``ra`` crosses the fixture site's meridian."""
    return T0 + schedule.hours_to_meridian_flip(ra, LON, T0) * 3600.0 / SIDEREAL


@pytest.fixture
def clock(group_store, monkeypatch):
    """The clock ``catalog.coords`` reads, and so the simulator mount's hour
    angle: set by hand (``clock.t``), starting at ``T0``."""
    fake = _Clock(time, T0)
    monkeypatch.setattr(coords_mod, "time", fake)
    return fake


@pytest.fixture
def mount(clock):
    """A simulator mount on a rig of its own, at the fixture site."""
    return SimTelescope(SimRig())


async def test_a_tracked_target_keeps_its_side_across_the_meridian_until_a_slew(
        mount, clock):
    """A goto 15 min before transit lands west of the pier (ASCOM convention,
    as the AM5N was measured). The mount then only tracks while the target
    crosses: half an hour on, 15 min past the meridian, it still reports
    west, because nothing slewed it. A goto to the same target now, past
    the meridian, takes the new side, east. Through the crossing the
    destination oracle said east while the mount said west: the two answer
    different questions, and only a slew brings them back together.

    MUTANT "pier_side from the hour angle now" (`SimTelescope.pier_side`
    returning ``self._side_for_ra(self.rig.ra_hours)``, as it did before
    #298): RED (observed):
        AssertionError: the side changed with no slew: the mount reports
        east 15 min past the meridian, tracking the target it was slewed to
        west of the pier
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
    MUTANT "the slew does not latch" (the latch in `slew`'s ``finally``
    removed, so the mount answers from the hour angle as it did before any
    move): RED, the same failure, verbatim.
    """
    ra = ra_at(-0.25)
    await mount.slew(ra, DEC)
    assert (await mount.pier_side()).value == "west", (
        "premise: a goto east of the meridian lands west of the pier")
    clock.t = crossing(ra) + 15 * 60.0
    assert schedule.hours_to_meridian_flip(ra, LON, clock.t) < 0, (
        "premise: the target is past the meridian")
    side = (await mount.pier_side()).value
    assert side == "west", (
        f"the side changed with no slew: the mount reports {side} 15 min "
        f"past the meridian, tracking the target it was slewed to west of "
        f"the pier")
    # The destination oracle is the hour-angle rule, latch or no latch.
    assert (await mount.destination_pier_side(ra, DEC)).value == "east"
    await mount.slew(ra, DEC)
    assert (await mount.pier_side()).value == "east", (
        "a slew past the meridian did not take the new side")


async def test_guiding_and_turning_the_ra_axis_keep_the_side(mount, clock):
    """Guide pulses and a single-axis turn move the mount without a goto,
    and neither flips the tube over the pole. A goto 6 min before transit
    lands west; a turn of the RA axis 5 degrees west carries the pointing 14
    min past the meridian, a guide pulse each way nudges it, and the mount
    still reports west, the counterweight now rising, as a real German
    mount would be (TPPA's arc and the native guider read this side).

    MUTANT "pier_side from the hour angle now": RED (observed):
        AssertionError: a turn of the RA axis past the meridian changed the
        side to east
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
    """
    ra = ra_at(-0.1)
    await mount.slew(ra, DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    await mount.rotate_axis("ra", -5.0)
    await mount.pulse_guide("west", 500)
    await mount.pulse_guide("east", 200)
    assert schedule.hours_to_meridian_flip(mount.rig.ra_hours, LON,
                                           clock.t) < -0.2, (
        "premise: the turn carried the pointing past the meridian")
    side = (await mount.pier_side()).value
    assert side == "west", (
        f"a turn of the RA axis past the meridian changed the side to {side}")


async def test_sync_park_and_unpark_each_latch_the_side_at_that_moment(
        mount, clock):
    """The three other moves the issue names (#298) latch the side from the
    hour angle at that moment, as a slew does. A sync past the meridian,
    of a mount slewed to the target before it crossed, takes the new side.
    A park keeps the side of the park position at the moment it parks, and
    keeps it while the park position's hour angle crosses; the unpark that
    follows takes the side that hour angle now implies.

    The park's own side is its slew's: `park` slews to the park position,
    which latches, and moves nothing after.

    MUTANT "sync does not latch" (the latch call in `SimTelescope.sync`
    removed): RED (observed):
        AssertionError: a sync past the meridian kept the side west
        assert 'west' == 'east'
          - east
          ?  -
          + west
          ? +
    MUTANT "unpark does not latch" (the latch call in `SimTelescope.unpark`
    removed): RED (observed):
        AssertionError: the unpark kept the side west of the park
        assert 'west' == 'east'
          - east
          ?  -
          + west
          ? +
    MUTANT "pier_side from the hour angle now": RED (observed):
        AssertionError: a parked mount changed side with the clock
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
    """
    ra = ra_at(-0.25)
    await mount.slew(ra, DEC)
    clock.t = crossing(ra) + 15 * 60.0
    await mount.sync(ra, DEC)
    side = (await mount.pier_side()).value
    assert side == "east", f"a sync past the meridian kept the side {side}"

    await mount.park()
    park_ra = mount.rig.ra_hours
    parked = coords_mod.pier_side_for_hour_angle(
        coords_mod.hour_angle_h(park_ra, LON, clock.t))
    assert (await mount.pier_side()).value == parked == "west", (
        "premise: the park position stands east of the meridian now")
    clock.t = crossing(park_ra) + 60 * 60.0
    assert (await mount.pier_side()).value == "west", (
        "a parked mount changed side with the clock")
    await mount.unpark()
    side = (await mount.pier_side()).value
    assert side == "east", f"the unpark kept the side {side} of the park"


async def test_the_destination_oracle_keeps_the_hour_angle_rule(mount, clock):
    """CONTROL: `destination_pier_side` is still the hour-angle rule
    (`coords.pier_side_for_hour_angle`) for every RA, at two clocks, with
    the mount latched west, and it predicts what `pier_side` reports once a
    goto lands there. No mutant of `pier_side` reaches it.

    MUTANT "the destination reads the latch" (`destination_pier_side`
    returning the latched side when there is one): RED (observed):
        AssertionError: the destination oracle left the hour-angle rule at
        12 of 24 RA hours: [(8, 'west', 'east'), (9, 'west', 'east'), (10,
        'west', 'east')]
        assert 12 == 0
         +  where 12 = len([(8, 'west', 'east'), (9, 'west', 'east'), (10,
        'west', 'east'), (11, 'west', 'east'), (12, 'west', 'east'), (13,
        'west', 'east'), ...])
    """
    await mount.slew(ra_at(-2.0), DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    for t in (T0, T0 + 5 * 3600.0):
        clock.t = t
        wrong = []
        for ra in range(24):
            rule = coords_mod.pier_side_for_hour_angle(
                coords_mod.hour_angle_h(float(ra), LON, t))
            got = (await mount.destination_pier_side(float(ra), DEC)).value
            if got != rule:
                wrong.append((ra, got, rule))
        assert len(wrong) == 0, (
            f"the destination oracle left the hour-angle rule at "
            f"{len(wrong)} of 24 RA hours: {wrong[:3]}")
    # ...and it predicts where a goto lands, away from the 7 s band an
    # unsynced goto's pointing error puts around the crossing.
    for ha in (-3.0, -0.2, 0.2, 3.0):
        ra = ra_at(ha, clock.t)
        predicted = await mount.destination_pier_side(ra, DEC)
        await mount.slew(ra, DEC)
        assert await mount.pier_side() == predicted, (ha, predicted)


async def test_a_mount_nothing_has_moved_answers_with_the_hour_angle_rule(
        mount, clock):
    """Before the first slew, sync, park or unpark there is no goto whose
    side to keep, so `pier_side` answers with the hour-angle rule for where
    the mount points: the side a goto there would pick. A mount nothing has
    moved therefore still follows the clock, at both of these instants, and
    the first slew latches.

    MUTANT "an unlatched mount is always west" (`pier_side` returning WEST
    before the first latch): RED (observed):
        AssertionError: assert <PierSide.WEST: 'west'> == <PierSide.EAST:
        'east'>
         +  where <PierSide.EAST: 'east'> = PierSide('east')
    """
    ra = mount.rig.ra_hours
    for t in (T0, crossing(ra) + 3600.0):
        clock.t = t
        rule = coords_mod.pier_side_for_hour_angle(
            coords_mod.hour_angle_h(ra, LON, t))
        assert await mount.pier_side() == PierSide(rule)


async def test_a_slew_cancelled_before_it_moved_keeps_the_side(mount, clock):
    """A goto that is cancelled before its first step moves nothing, so the
    tube is where it was and on the side it was: west, tracking a target
    that has since crossed the meridian. Latching there anyway would flip
    the side with no motion at all, the #298 defect by another door.

    MUTANT "latch whether or not the slew moved" (the ``if moved:`` guard in
    `SimTelescope.slew`'s ``finally`` removed): RED (observed):
        AssertionError: a slew cancelled before it moved changed the side to
        east
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
    """
    ra = ra_at(-0.25)
    await mount.slew(ra, DEC)
    clock.t = crossing(ra) + 15 * 60.0
    before = (mount.rig.ra_hours, mount.rig.dec_deg)
    task = asyncio.ensure_future(mount.slew(ra_at(-2.0, clock.t), DEC))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (mount.rig.ra_hours, mount.rig.dec_deg) == before, (
        "premise: the cancelled slew never moved")
    side = (await mount.pier_side()).value
    assert side == "west", (
        f"a slew cancelled before it moved changed the side to {side}")


async def test_a_goto_under_an_injected_polar_error_latches_too(
        mount, clock, monkeypatch):
    """The native-TPPA path of `slew` (an injected polar misalignment) lands
    by its own model and returns early; it latches where it lands, like the
    plain path. Synced west of the pier on a target two hours east of the
    meridian, a goto four hours of RA west, past the meridian, takes the
    east side. The misalignment model reads the simulator module's own
    clock, so it is put on the case's clock too.

    MUTANT "the polar goto does not latch" (the latch call in `slew`'s
    polar branch removed): RED (observed):
        AssertionError: the polar goto kept the side west
        assert 'west' == 'east'
          - east
          ?  -
          + west
          ? +
    """
    import astrodeck.devices.sim as sim_mod
    monkeypatch.setattr(sim_mod, "time", clock)
    mount.rig.set_polar_misalignment(2.0, 1.0, lat_deg=40.0, lon_deg=LON)
    east = ra_at(-2.0, clock.t)
    await mount.sync(east, DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    await mount.slew(ra_at(2.0, clock.t), DEC)
    assert schedule.hours_to_meridian_flip(mount.rig.ra_hours, LON,
                                           clock.t) < -1.5, (
        "premise: the goto landed past the meridian")
    side = (await mount.pier_side()).value
    assert side == "east", f"the polar goto kept the side {side}"


def _ha(ra: float, t: float) -> float:
    """Hour angle of ``ra`` at ``t`` at the fixture site, in (-12, 12]."""
    return ((coords_mod.hour_angle_h(ra, LON, t) + 12.0) % 24.0) - 12.0


async def test_a_goto_just_past_the_crossing_lands_short_of_it_and_keeps_the_side(
        mount, clock):
    """The side is latched from where the goto LANDED, pointing error
    included, not from the target it was sent to. An unsynced goto lands
    about 7 s of RA east of its target (`SimRig.pointing_error_deg`), so a
    goto to a target 3 s past the crossing lands 3.7 s short of the
    meridian and keeps the pre-flip side, west, while the destination
    oracle calls the target itself east. That band is what the engine's
    MERIDIAN_SIDE_MARGIN_S allows for (spec 5.7), and #366's no-op retry at
    the crossing is this case on a night. The control: a goto a minute past
    the crossing, outside the landing error, lands past the meridian and
    takes the east side.

    Added by the S4-SIM verifier: every other case slews to targets far
    from the crossing, where the target's side and the landed side agree,
    so the mutant below left all of them, and 92 other meridian, flip,
    pier, group, sim, TPPA, polar and resume files, green.

    MUTANT "the slew latches its target" (the latch in `slew`'s
    ``finally`` taking ``self._side_for_ra(ra_hours)``, the commanded
    target, instead of where the mount stopped): RED (observed):
        AssertionError: a goto that landed 3.7 s of RA short of the meridian
        took the east side of its target, not of where it landed
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
    """
    await mount.slew(ra_at(-2.0), DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    just_past = ra_at(3.0 / 3600.0)
    assert (await mount.destination_pier_side(just_past, DEC)).value == "east", (
        "premise: the target itself has crossed")
    await mount.slew(just_past, DEC)
    landed = _ha(mount.rig.ra_hours, clock.t)
    assert -10.0 / 3600.0 < landed < 0.0, (
        f"premise: the unsynced goto landed short of the meridian, at HA "
        f"{landed * 3600.0:+.1f} s")
    side = (await mount.pier_side()).value
    assert side == "west", (
        f"a goto that landed {-landed * 3600.0:.1f} s of RA short of the "
        f"meridian took the {side} side of its target, not of where it landed")
    # CONTROL: a minute past the crossing is outside the landing error, and
    # the goto lands past the meridian and takes the east side.
    await mount.slew(ra_at(60.0 / 3600.0), DEC)
    assert _ha(mount.rig.ra_hours, clock.t) > 0.0, "premise"
    assert (await mount.pier_side()).value == "east"


async def _cancel_after_one_step(mount, ra: float) -> None:
    """Start a goto to ``ra`` and cancel it once it has taken one step. The
    sim's pacing waits are zero under the test fast path, so the first
    yield runs the slew to its first wait and the second takes one step;
    each caller checks where the mount stopped as a premise."""
    task = asyncio.ensure_future(mount.slew(ra, DEC))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_slew_cancelled_part_way_latches_where_it_stopped(
        mount, clock):
    """A goto cancelled after it moved latches the side of where it
    stopped, as the tube stands there. Two halves, because the stopping
    point shares its side with either the start or the target, never
    neither. Stopped one step out of 2 h east of the meridian, short of a
    target 2 h past it, the side is west: the start's, not the target's.
    Stopped one step out of 2 min east of the meridian, the step having
    carried the pointing over it, the side is east, not the start's,
    though the slew never finished.

    Added by the S4-SIM verifier: `slew`'s comment says a slew cancelled
    part way latches where it stopped, and nothing ran that path, so both
    mutants below left every other case in the 93 files green.

    MUTANT "the slew latches its target" (as above): RED (observed):
        AssertionError: a slew stopped at HA -1.900 h, short of the
        meridian, latched east, the side of the target it never reached
        assert 'east' == 'west'
          - west
          ? -
          + east
          ?  +
    MUTANT "a cancelled slew never latches" (the latch moved out of
    `slew`'s ``finally`` to after the ``try``, so only a finished slew
    latches): RED (observed):
        AssertionError: a slew stopped at HA +0.023 h, past the meridian,
        kept the side west it started on
        assert 'west' == 'east'
          - east
          ?  -
          + west
          ? +
    """
    await mount.sync(ra_at(-2.0), DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    start = mount.rig.ra_hours
    await _cancel_after_one_step(mount, ra_at(2.0))
    stopped = _ha(mount.rig.ra_hours, clock.t)
    assert mount.rig.ra_hours != start and stopped < -1.5, (
        f"premise: one step taken, still east of the meridian (HA "
        f"{stopped:+.3f} h)")
    side = (await mount.pier_side()).value
    assert side == "west", (
        f"a slew stopped at HA {stopped:+.3f} h, short of the meridian, "
        f"latched {side}, the side of the target it never reached")

    await mount.sync(ra_at(-0.03), DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    await _cancel_after_one_step(mount, ra_at(2.0))
    stopped = _ha(mount.rig.ra_hours, clock.t)
    assert 0.0 < stopped < 0.5, (
        f"premise: one step took the pointing over the meridian (HA "
        f"{stopped:+.3f} h)")
    side = (await mount.pier_side()).value
    assert side == "east", (
        f"a slew stopped at HA {stopped:+.3f} h, past the meridian, kept "
        f"the side {side} it started on")


async def test_a_sync_latches_the_side_of_where_it_says_the_mount_points(
        mount, clock):
    """A sync latches from the position it was given, not from where the
    mount pointed before it. With the clock held still, a mount slewed 15
    min east of the meridian (west of the pier) and synced to a position
    15 min past it takes the east side. The control: a sync that stays
    east of the meridian leaves the side as it was.

    Added by the S4-SIM verifier: the sync case above syncs to the target
    the mount was already slewed to, where the side before the sync and
    after it are one, so the mutant below left it, and every other case in
    the 93 files, green.

    MUTANT "sync latches before it moves" (`SimTelescope.sync` calling
    `_latch_pier_side` before it sets ``rig.ra_hours``): RED (observed):
        AssertionError: a sync to a position 15 min past the meridian
        latched west, the side of where the mount pointed before it
        assert 'west' == 'east'
          - east
          ?  -
          + west
          ? +
    """
    await mount.slew(ra_at(-0.25), DEC)
    assert (await mount.pier_side()).value == "west", "premise"
    await mount.sync(ra_at(-0.2), DEC)
    assert (await mount.pier_side()).value == "west"
    await mount.sync(ra_at(0.25), DEC)
    side = (await mount.pier_side()).value
    assert side == "east", (
        f"a sync to a position 15 min past the meridian latched {side}, the "
        f"side of where the mount pointed before it")
