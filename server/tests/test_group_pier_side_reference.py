# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The group's pier-side REFERENCE after its pier change (#189 S2, spec 5.6
step 5, 5.7, D10): the side a hop past the meridian is checked against, and
what lets that hop disarm the flip latch.

`SequenceEngine._group_pier_check` checks every hop's side against the side
the group is on tonight. Before the meridian that is the side its first hop
read. After the group's one pier change it must be the side the change READ,
because the pre-flip side is, by definition, the wrong side past the
meridian. Two holes, one class (a reference nobody verified, used as if it
were one):

* A PIER CHANGE READ AS UNREADABLE kept the PRE-FLIP side as the reference.
  `_note_group_flipped` writes the side only when it is readable, and nothing
  cleared the old one, so every later hop past the meridian was compared with
  the side the group had LEFT: a hop on the right side was deferred ("the
  mount chose the other pier side") until the panel was set aside, and a hop
  that had stayed on the pre-flip side, counterweight up, matched the stale
  reference, was accepted, and disarmed the flip latch.
* A REFERENCE NOTHING VERIFIED disarmed the latch. The first hop of a group
  that is already past the meridian, or the first readable hop after an
  unreadable change, has nothing to compare its side with, yet
  `_group_pier_check` returned True for it and `_setup_target` cleared
  ``_flip_armed``: the backstop for a mount that kept its side was spent on a
  reading that proved nothing. With no pre-flip sighting of the panel,
  `_enforce_flip_owed` has no record to refuse on either. The hop may still
  mark the group flipped (the direction that keeps the one-change rule), but
  only a side compared with a measured pre-flip side, or a flip the latch
  measured, verifies the change and may disarm the latch.

Every case runs the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py) with ``coords_clock``. Each names the mutant it
was shown RED under, with the failure observed, verbatim. Every mutant was
applied in a private scratch copy of server/, never in the shared tree
(#254).
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, LON, T0, Night, grid_plan, group_hub,
                            group_store, ra_at)
from astrodeck.devices.base import DeviceError
from astrodeck.sequence import schedule
from astrodeck.sequence.session import session_store

SIDEREAL = 1.0027379093


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def crossing(ra: float) -> float:
    return T0 + schedule.hours_to_meridian_flip(ra, LON, T0) * 3600.0 / SIDEREAL


def meridian_plan(*, cols: int, ha_h: float, ha_step_h: float, count: int):
    return grid_plan(rows=1, cols=cols,
                     panel_kw={"filters": ("L",), "count": count,
                               "ha_h": ha_h, "ha_step_h": ha_step_h},
                     meridian_flip=True)


async def _night(hub, monkeypatch, plan, *, before_run=None,
                 wall_s: float = 120.0) -> Night:
    night = Night(hub, monkeypatch, coords_clock=True)
    if before_run is not None:
        before_run(night)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


async def test_an_unreadable_pier_change_leaves_no_stale_reference(
        group_hub, monkeypatch):
    """The straddle of test_group_meridian (a 1x2 of L, count 100 a panel;
    1-2 crosses 30 min in, 1-1 54 min in, the group's pier change is 1-2's
    hop 40 min in), with ONE unreadable read: the side check on the hop that
    makes the group's pier change. The group is marked flipped, as the
    unreadable branch says, and the pre-flip side it held (west) must not
    stand as the side past the meridian. Every later hop lands east, the
    right side past the meridian (the simulator picks its side from the hour
    angle, test_group_meridian asserts it first), and none is deferred: both
    panels finish and the session completes. The control has no unreadable
    read and is the straddle test's night.

    MUTANT "the pre-flip side survives an unreadable change"
    (`_note_group_flipped` without the ``pop`` of the group's side when the
    change was unreadable, which is the code as S2 built it): RED (observed):
        AssertionError: after an unreadable pier change, hops on the right side
        were deferred: ['M31: the mount chose the other pier side on 1-2: it
        reads east after the goto, and the M31 mosaic changed to the west
        side; retried on the next pass (1 of 3 consecutive)', 'M31: the mount
        chose the other pier side on 1-2: it reads east after the goto, and
        the M31 mosaic changed to the west side; retried on the next pass (2
        of 3 consecutive)']
    """
    tel = group_hub.devices["telescope"]
    real = tel.pier_side
    ra_12 = ra_at(-0.9 + 0.4)
    for unreadable in (True, False):
        box: dict = {"fail": False, "failed": 0}

        async def pier_side():
            if box["fail"]:
                box["fail"] = False
                box["failed"] += 1
                raise DeviceError("pier side: no answer")
            return await real()

        def setup(night, unreadable=unreadable):
            def goto(who, n, result):
                past = night.clock.t > crossing(ra_12) + 15.0
                if (unreadable and who == _name("1-2") and past
                        and not box.get("armed")):
                    # The next pier-side read is this hop's side check.
                    box["armed"] = True
                    box["fail"] = True
                return result
            night.goto_script = goto
            night.mp.setattr(tel, "pier_side", pier_side)

        plan = meridian_plan(cols=2, ha_h=-0.9, ha_step_h=0.4, count=100)
        night = await _night(group_hub, monkeypatch, plan, before_run=setup)
        assert night.done, night.trace[-3:]
        run = night.engine._group_runs["m31-mosaic"]
        assert run.flipped is True
        if unreadable:
            assert box["failed"] == 1, "premise: one read failed"
        deferred = night.said("the mount chose the other pier side")
        assert not deferred, (
            "after an unreadable pier change, hops on the right side were "
            f"deferred: {deferred[:2]}")
        assert night.stored.status == "complete", night.stored.status
        assert night.engine._group_side["m31-mosaic"] == "east"


async def test_a_side_nothing_verified_leaves_the_flip_latch_armed(
        group_hub, monkeypatch):
    """A one-panel mosaic whose first hop tonight is 5 min past the meridian,
    inside the window the flip latch arms in (the plan's 10 min lead plus
    the 5 min margin past transit). There is no pre-flip side to compare the
    hop's side with, so the hop proves nothing about a pier change: a mount
    that keeps its side past transit reads exactly as one that changed it.
    The group is marked flipped, and the latch STAYS ARMED, the backstop
    `_arm_meridian_flip` sets for any target acquired this close past
    transit, which the frame loop then spends on its own measured attempt.
    Read the moment the hop's `_setup_target` returns, through a pass-through
    spy (the frame loop's first flip gate spends the latch before any
    exposure, so a read at the shutter sees it spent either way). The
    control is the same panel 40 min past the meridian, outside the window:
    nothing arms the latch there, and the group rule changes nothing about
    that.

    MUTANT "an unverified side disarms" (`_group_pier_check` returning
    ``readable`` for a first hop past the meridian, the code as S2 built
    it): RED (observed):
        AssertionError: the first hop 5 min past transit read a side nothing
        verified, and the flip latch was disarmed on it
        assert False is True
    """
    for ha_min, armed in ((5.0, True), (40.0, False)):
        seen: dict = {}

        def setup(night):
            real_setup = night.engine._setup_target

            async def spy(ti, target):
                await real_setup(ti, target)
                seen.setdefault("armed", night.engine._flip_armed)
            night.mp.setattr(night.engine, "_setup_target", spy)

        plan = meridian_plan(cols=1, ha_h=ha_min / 60.0, ha_step_h=0.0,
                             count=2)
        night = await _night(group_hub, monkeypatch, plan, before_run=setup)
        assert night.done, night.trace[-3:]
        run = night.engine._group_runs["m31-mosaic"]
        assert run.flipped is True
        assert night.said("is past the meridian, so the mosaic starts on the "
                          "side it takes after the meridian")
        assert seen["armed"] is armed, (
            f"the first hop {ha_min:g} min past transit read a side nothing "
            f"verified, and the flip latch was "
            f"{'disarmed' if armed else 'armed'} on it")


async def test_a_verified_pier_change_still_disarms_the_flip_latch(
        group_hub, monkeypatch):
    """The CONTROL for the rule above, on the straddle night: the hop that
    makes the group's pier change (1-2, 40 min in, 10 min past its crossing
    and so inside the window the latch arms in) reads east, which differs
    from the west every hop before the meridian read. That side is VERIFIED,
    and the hop disarms the latch: a flip the hop has measured is not owed
    again, and an armed latch would re-slew at the first frame, find nothing
    flipped, and teach the engine that this mount cannot flip early (#127)
    from an attempt made past the meridian. Read the moment each hop's
    `_setup_target` returns, through a pass-through spy. No "nothing
    flipped" line is said all night.

    MUTANT "nothing ever disarms" (`_group_pier_check`'s two returns past
    the meridian made ``False``): RED (observed):
        AssertionError: the verified pier-change hop left the flip latch armed
        assert True is False
    """
    ra_12 = ra_at(-0.9 + 0.4)
    after: list[tuple[float, str, bool]] = []

    def setup(night):
        real_setup = night.engine._setup_target

        async def spy(ti, target):
            await real_setup(ti, target)
            after.append((night.clock.t, target.name, night.engine._flip_armed))
        night.mp.setattr(night.engine, "_setup_target", spy)

    plan = meridian_plan(cols=2, ha_h=-0.9, ha_step_h=0.4, count=100)
    night = await _night(group_hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    assert night.said("the mosaic changed pier side at 1-2")
    change = [(t, armed) for t, who, armed in after
              if who == _name("1-2") and t > crossing(ra_12) + 15.0]
    assert change, "premise: 1-2 was hopped to past its crossing"
    t, armed = change[0]
    assert (t - crossing(ra_12)) < 15 * 60.0, (
        "premise: the change is inside the window the latch arms in")
    assert armed is False, (
        "the verified pier-change hop left the flip latch armed")
    assert not night.said("nothing flipped"), night.said("nothing flipped")
    assert night.stored.status == "complete", night.stored.status
