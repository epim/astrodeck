# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""An unframed target locks its angle on its first shot (#189, spec Revision
2 ruling 9, 5.6 step 4, 5.7; task T15; advances #160).

The owner's ruling: "If the target block has no framing, then whatever the
first angle of the first shot is, is locked as the angle. Otherwise any framed
shot intrinsically has an angle." So a target with ``rotation_deg`` None takes
the position angle of the first fresh sky-angle record of its acquisition,
normally the centring solve before its first frame, stores it on the session
with ``Session.lock_angle`` (the solve time included) and saves it. From then
on the lock behaves like a planned angle: every later acquisition, a hop
back, a same-night restart, a later night and the flip re-centre, commands a
connected rotator to it. With no rotator, the angle each later acquisition
measures is checked against it and warns beyond ``RotatorConfig.
tolerance_deg``; a single target is never deferred or set aside for it.

Only re-framing clears a lock, and re-framing gives the target a new id, so
the lock is keyed by the target's id and never by its name.

Every case runs the REAL engine on the clocked simulator
(tests/_group_harness.py). The harness scripts the sky angle each centring
solve measured (``sky``) and each saved frame's WCS solve (``frame_sky``);
the goto records the angle it was commanded, and ``hub.meridian_flip`` is
the hub's own, wrapped only to record its call.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/, never in the shared tree
(#254).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (T0, Night, grid_plan, group_hub, group_store,
                            ra_at, sky_record)
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.engine import SafetyAbort
from astrodeck.sequence.models import (ExposureStep, Instruction,
                                       SequencePlan, Target)
from astrodeck.sequence.session import session_store

DAY_S = 86400.0
SWEEP = {"guide": False, "dither_every": 0, "autofocus_every": 0,
         "meridian_flip": False, "park_when_done": False,
         "warm_cooler_when_done": False, "recover_guiding": False}


def m33(count: int = 2, *, tid: str = "m33", center: bool = True,
        **kw) -> Target:
    """The unframed target: no ``rotation_deg``, so it locks."""
    return Target(id=tid, name="M33", ra_hours=ra_at(-2.5), dec_deg=30.0,
                  center=center, autofocus_first=False,
                  steps=[ExposureStep(id=f"{tid}-L", filter="L",
                                      exposure_s=30.0, count=count)], **kw)


def ngc891() -> Target:
    """A framed target: PA 45 set, so it never locks."""
    return Target(id="ngc891", name="NGC 891", ra_hours=ra_at(-2.0),
                  dec_deg=42.0, center=True, autofocus_first=False,
                  rotation_deg=45.0,
                  steps=[ExposureStep(id="ngc891-L", filter="L",
                                      exposure_s=30.0, count=1)])


def plan_of(*targets, **kw) -> SequencePlan:
    return SequencePlan(name="lock", targets=list(targets), **{**SWEEP, **kw})


async def _night(hub, monkeypatch, plan, *, session=None, on_capture=None,
                 **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    night.on_capture = on_capture
    start_kw = {"session": session} if session is not None else {}
    try:
        night.done = await night.run(plan, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _commanded(night, who: str) -> list:
    return [c["rotation_deg"] for c in night.goto_calls if c["who"] == who]


# ---------------------------------------------------------------- the lock

async def test_the_first_solve_locks_the_angle_and_a_hop_back_commands_it(
        group_hub, monkeypatch):
    """M33 has no angle; NGC 891 is framed at PA 45. M33's first centring
    measures PA 30.0, and at the end of that setup the angle is locked,
    solve time included, and SAVED at once: the session on disk holds it by
    M33's first frame. Two rules then send the run to NGC 891 and back (a
    ``run_target`` jump abandons the target it leaves, so the second rule is
    what brings the run back), and the hop back commands PA 30.0, though its
    own solve now reads 12.0: the first lock wins, and a later reading never
    moves it.

    NGC 891, framed, is commanded 45 and never locks (the control).

    MUTANT "no lock" (`_lock_angle_from` returns before it locks): the hop
    back commands nothing. RED (observed):
        AssertionError: [{'kw': {}, 'rotation_deg': None, 't': 1788313689.0,
        'who': 'M33'}, {'kw': {}, 'rotation_deg': 45.0, 't': 1788313719.0,
        'who': 'NGC 891'}, {'kw': {}, 'rotation_deg': None, 't':
        1788313749.0, 'who': 'M33'}]
        assert [None, None] == [None, 30.0]
          At index 1 diff: None != 30.0

    MUTANT "lock re-read on every acquisition" (the held lock is dropped
    before each lock, so every acquisition re-reads it): the hop back still
    commands 30.0, but the lock it leaves is 12.0. RED (observed):
        AssertionError: {'m33': {'exposed_at': 1788313749.0, 'pa_deg': 12.0,
        'solved_at': 1788313749.0, 'source': 'plate solve + sync'}}
        assert {'m33': {'exp...olve + sync'}} == {'m33': {'exp...olve +
        sync'}}

    MUTANT "not saved until the run ends" (the save after the lock
    removed): RED (observed):
        AssertionError: [None]
        assert ([None] and None is not None)

    MUTANT "framed targets lock too" (``rotation_deg is None`` dropped from
    `_may_lock`): RED (observed):
        AssertionError: {'m33': {'exposed_at': 1788313689.0, 'pa_deg': 30.0,
        'solved_at': 1788313689.0, 'source': 'plate solve + sync'},
        'ngc891': {'exposed_at': 1788313719.0, 'pa_deg': 45.3, 'solved_at':
        1788313719.0, 'source': 'plate solve + sync'}}
        assert {'m33': {'exp...olve + sync'}} == {'m33': {'exp...olve +
        sync'}}
    """
    away = Instruction(trigger="on_hfr_above", threshold=1.0,
                       action="run_target", target_arg="NGC 891", once=True,
                       only_target="M33")
    back = Instruction(trigger="on_hfr_above", threshold=1.0,
                       action="run_target", target_arg="M33", once=True,
                       only_target="NGC 891")
    plan = plan_of(m33(), ngc891(), instructions=[away, back])
    readings = {("M33", 1): 30.0, ("M33", 2): 12.0, ("NGC 891", 1): 45.3}
    on_disk: list = []

    def at_capture(rec):
        if rec["target"] == "M33" and not on_disk:
            on_disk.append(session_store.load(night_ref[0].session_id)
                           .locked_angles.get("m33"))

    night_ref: list = []
    night = Night(group_hub, monkeypatch,
                  sky=lambda who, n: readings.get((who, n)))
    night_ref.append(night)
    night.on_capture = at_capture
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    stored = session_store.load(night.session_id)
    assert night.done, night.trace[-3:]
    assert _commanded(night, "M33") == [None, 30.0], night.goto_calls
    assert _commanded(night, "NGC 891") == [45.0], night.goto_calls
    first_goto = next(c["t"] for c in night.goto_calls if c["who"] == "M33")
    assert stored.locked_angles == {"m33": {
        "pa_deg": 30.0, "solved_at": first_goto, "exposed_at": first_goto,
        "source": "plate solve + sync"}}, stored.locked_angles
    assert on_disk and on_disk[0] is not None, on_disk
    assert on_disk[0]["pa_deg"] == 30.0, on_disk
    locked = night.said("angle is locked at PA 30.0")
    assert len(locked) == 1 and locked[0].startswith("M33: "), night.lines


async def test_a_same_night_restart_commands_the_lock(group_hub, monkeypatch):
    """The run that locked M33 at PA 30.0 ends; the same night the session is
    started again (``start(session=)``, as a crash-resume or the operator
    does) with one more frame owed. Its first goto commands PA 30.0.

    MUTANT "no lock" (as above): RED (observed):
        AssertionError: [{'kw': {}, 'rotation_deg': None, 't': 1788315489.0,
        'who': 'M33'}]
        assert [None] == [30.0]
          At index 0 diff: None != 30.0
    """
    first = await _night(group_hub, monkeypatch, plan_of(m33(1)),
                         sky=lambda who, n: 30.0)
    assert first.done, first.trace[-3:]
    again = await _night(group_hub, monkeypatch, plan_of(m33(2)),
                         session=first.stored, t0=T0 + 1800.0,
                         sky=lambda who, n: 12.0)
    assert again.done, again.trace[-3:]
    assert _commanded(again, "M33") == [30.0], again.goto_calls
    assert again.stored.locked_angles["m33"]["pa_deg"] == 30.0


async def test_a_later_night_commands_the_lock_and_keeps_it(group_hub,
                                                            monkeypatch):
    """A night later the session runs again, and its solve reads PA 12.0: the
    goto commands 30.0, and the lock is still 30.0 when the night ends, so
    frames from the two nights share one angle and stack (ruling 9: "the
    angle would drift night to night" is the failure it prevents).

    MUTANT "no lock": the second night's goto has no rotation_deg. RED
    (observed):
        AssertionError: [{'kw': {}, 'rotation_deg': None, 't': 1788400089.0,
        'who': 'M33'}]
        assert [None] == [30.0]
          At index 0 diff: None != 30.0

    MUTANT "lock re-read on every acquisition": the second night moves the
    lock to 12.0. RED (observed):
        AssertionError: {'m33': {'exposed_at': 1788400089.0, 'pa_deg': 12.0,
        'solved_at': 1788400089.0, 'source': 'plate solve + sync'}}
        assert 12.0 == 30.0
    """
    first = await _night(group_hub, monkeypatch, plan_of(m33(1)),
                         sky=lambda who, n: 30.0)
    assert first.done
    later = await _night(group_hub, monkeypatch, plan_of(m33(2)),
                         session=first.stored, t0=T0 + DAY_S,
                         sky=lambda who, n: 12.0)
    assert later.done, later.trace[-3:]
    assert _commanded(later, "M33") == [30.0], later.goto_calls
    assert later.stored.locked_angles["m33"]["pa_deg"] == 30.0, (
        later.stored.locked_angles)
    # THE CONTROL OF THE FIXED-CAMERA CHECK. A rotator is connected, so the
    # goto commanded the lock and the hub says if it could not turn to it;
    # the engine's check against the lock is for a rig with no rotator, and
    # its line ("There is no rotator to turn it back") would be false here.
    # MUTANT "locked check on a connected rotator too" (the
    # `_rotator_connected` test dropped from `_settle_locked_angle`): RED
    # (observed):
    #     AssertionError: ['M33: the camera reads PA 12.0 and its angle was
    #     locked at PA 30.0 by its first plate solve: 18.0 deg apart, beyond
    #     t...ck, so these frames will not stack with the earlier ones
    #     without a rotation: turn the camera back to the locked angle']
    #     assert ['M33: the ca...locked angle'] == []
    assert later.said("There is no rotator") == [], later.said(
        "There is no rotator")


async def test_a_setup_that_solved_nothing_locks_at_a_later_frame(
        group_hub, monkeypatch):
    """M33's centring solve reports no angle, so its setup has nothing fresh
    to lock: the hub still holds a record from ten minutes before the run
    (PA 99.0), which measured some other pointing. Its first light frame's
    WCS solve measures PA 30.0 after the shutter closes, and the lock is
    taken at the next frame boundary, with that frame's exposure time and
    that solve's time.

    MUTANT "lock only at setup" (the frame-boundary look in `_capture`
    removed): no lock is ever taken. RED (observed):
        AssertionError: [(1788313689.0, 'info', "sequence 'lock' started: 3
        frames, 2 min integration"), (1788313689.0, 'warning', "sequence
        '...o', 'target 1/1: M33'), (1788313719.0, 'info', 'focus baseline
        HFR 2.00 px (relative watchdogs measure against this)')]
        assert None is not None

    MUTANT "freshness ignores the exposure" (`angle_check.fresh_sky_angle`
    drops its ``exposed < since`` test): the setup locks the stale 99.0.
    RED (observed):
        AssertionError: {'exposed_at': 1788313089.0, 'pa_deg': 99.0,
        'solved_at': 1788313089.0, 'source': 'plate solve + sync'}
        assert (99.0 == 30.0)
    """
    def frame_sky(who, filt, n):
        if (who, n) == ("M33", 1):
            return {"pa_deg": 30.0, "source": "saved-frame WCS"}
        return None

    group_hub.last_sky_angle = sky_record(99.0, T0 - 600.0)
    night = await _night(group_hub, monkeypatch, plan_of(m33(3)),
                         sky=lambda who, n: None, frame_sky=frame_sky)
    assert night.done, night.trace[-3:]
    shots = [c for c in night.captures if c["target"] == "M33"]
    assert len(shots) == 3
    lock = night.stored.locked_angles.get("m33")
    assert lock is not None, night.lines
    assert lock["pa_deg"] == 30.0 and lock["source"] == "saved-frame WCS", lock
    assert lock["exposed_at"] == shots[0]["t"], (lock, shots[0]["t"])
    assert shots[0]["t"] < lock["solved_at"] <= shots[1]["t"], (lock, shots)


async def test_a_locked_target_that_does_not_centre_says_the_angle_is_not_turned(
        group_hub, monkeypatch):
    """A target with centring off slews with no goto, and only a centred goto
    turns the rotator. Its lock (taken from its first frame's WCS solve) is
    commanded like a planned angle, so the next acquisition says, as it does
    for a planned angle (#160), that the rotation was asked for and not
    made, instead of dropping it in silence.

    MUTANT "the slew branch reads only the planned angle" (the non-centred
    branch tests ``target.rotation_deg`` again): nothing is said. RED
    (observed):
        AssertionError: [(1788315489.0, 'info', "sequence 'lock' started: 3
        frames, 2 min integration"), (1788315489.0, 'warning', "sequence
        '...o', 'target 1/1: M33'), (1788315519.0, 'info', 'focus baseline
        HFR 2.00 px (relative watchdogs measure against this)')]
        assert (0 == 1)
         +  where 0 = len([])
    """
    def frame_sky(who, filt, n):
        if n != 1:
            return None
        return {"pa_deg": 30.0, "source": "saved-frame WCS"}

    first = await _night(group_hub, monkeypatch, plan_of(m33(2, center=False)),
                         frame_sky=frame_sky)
    assert first.stored.locked_angles["m33"]["pa_deg"] == 30.0, first.lines
    assert not first.said("was asked for"), first.lines
    again = await _night(group_hub, monkeypatch,
                         plan_of(m33(3, center=False)), session=first.stored,
                         t0=T0 + 1800.0)
    assert again.done, again.trace[-3:]
    said = again.said("rotation to PA 30 was asked for")
    assert len(said) == 1 and said[0].startswith("M33: "), again.lines


async def test_a_reframed_target_does_not_inherit_the_lock(group_hub,
                                                           monkeypatch):
    """Re-framing a block re-anchors it with a new id, and only re-framing
    clears a lock. The session locked "M33" (id m33-old) at PA 30.0; the plan
    it runs next carries an unframed "M33" with the new id m33. That target
    is commanded nothing on its first goto and takes a lock of its own,
    12.0, from its own solve; the old id's lock is left as it was.

    MUTANT "lock keyed by name" (the engine keys the lock by
    ``target.name``): the new target inherits PA 30.0. RED (observed):
        AssertionError: [{'kw': {}, 'rotation_deg': 30.0, 't': 1788315489.0,
        'who': 'M33'}]
        assert [30.0] == [None]
          At index 0 diff: 30.0 != None
    """
    first = await _night(group_hub, monkeypatch,
                         plan_of(m33(1, tid="m33-old")),
                         sky=lambda who, n: 30.0)
    # Premise: the first night locked one angle, 30.0 (under which key is
    # asserted at the end, so a mutant keyed otherwise fails on what the
    # second night commands, not here).
    assert [v["pa_deg"] for v in first.stored.locked_angles.values()] == [
        30.0], first.stored.locked_angles
    again = await _night(group_hub, monkeypatch, plan_of(m33(1)),
                         session=first.stored, t0=T0 + 1800.0,
                         sky=lambda who, n: 12.0)
    assert again.done, again.trace[-3:]
    assert _commanded(again, "M33") == [None], again.goto_calls
    locks = again.stored.locked_angles
    assert locks["m33"]["pa_deg"] == 12.0, locks
    assert locks["m33-old"]["pa_deg"] == 30.0, locks


async def test_control_a_group_member_never_locks(group_hub, monkeypatch):
    """A mosaic panel's angle is its group's layout angle, so a member never
    locks one of its own, whatever its solves read (the control of ruling
    9). A fixed mosaic with no angle check, every centring reading PA 30.0:
    every goto commands nothing, and the session holds no lock.

    MUTANT "members lock too" (the ``_group_of`` exclusion dropped from
    `_may_lock`): RED (observed):
        assert {None, 30.0} == {None}
          Extra items in the left set:
          30.0
    """
    night = await _night(group_hub, monkeypatch, grid_plan(),
                         sky=lambda who, n: 30.0)
    assert night.done, night.trace[-3:]
    assert {c["rotation_deg"] for c in night.goto_calls} == {None}
    assert night.stored.locked_angles == {}, night.stored.locked_angles


# ------------------------------------------------------- a fixed camera

@pytest.mark.parametrize("reading,warns", [(37.0, True), (30.4, False)])
async def test_a_fixed_camera_checks_the_lock_and_only_warns(
        group_hub, monkeypatch, reading, warns):
    """With no rotator in the rig there is nothing to command, so the lock is
    checked instead. The first night locks M33 at PA 30.0; the same night it
    is started again and the camera now reads ``reading``. Beyond the
    rotator tolerance (1.0 deg by default) that is a warning naming both
    angles; within it, nothing is said. Either way the goto commands nothing
    and the frame is shot: a single target is never deferred or set aside
    over its angle.

    MUTANT "no check on a fixed camera" (`_settle_locked_angle` returns when
    the target is locked): RED at 37.0 (observed):
        AssertionError: [(1788315489.0, 'info', "sequence 'lock' started: 2
        frames, 1 min integration"), (1788315489.0, 'warning', "sequence
        '...o', 'target 1/1: M33'), (1788315519.0, 'info', 'focus baseline
        HFR 2.00 px (relative watchdogs measure against this)')]
        assert 0 == 1
         +  where 0 = len([])

    MUTANT "the check stops the target" (the warning raises `StopTarget`
    instead): RED at 37.0 (observed):
        AssertionError: []
        assert [] == ['M33']

    MUTANT "commanded on a fixed camera" (`_commanded_rotation` ignores the
    rotator's absence): RED on both (observed):
        [37.0-True]
        AssertionError: [{'kw': {}, 'rotation_deg': 30.0, 't': 1788315489.0,
        'who': 'M33'}]
        assert [30.0] == [None]
        [30.4-False]
        AssertionError: [{'kw': {}, 'rotation_deg': 30.0, 't': 1788315489.0,
        'who': 'M33'}]
        assert [30.0] == [None]
    """
    monkeypatch.delitem(group_hub.devices, "rotator")
    first = await _night(group_hub, monkeypatch, plan_of(m33(1)),
                         sky=lambda who, n: 30.0)
    assert first.stored.locked_angles["m33"]["pa_deg"] == 30.0
    again = await _night(group_hub, monkeypatch, plan_of(m33(2)),
                         session=first.stored, t0=T0 + 1800.0,
                         sky=lambda who, n: reading)
    assert again.done, again.trace[-3:]
    assert _commanded(again, "M33") == [None], again.goto_calls
    assert [c["target"] for c in again.captures] == ["M33"], again.captures
    said = [m for _t, lvl, m in again.lines
            if lvl == "warning" and "locked at PA 30.0" in m]
    if warns:
        assert len(said) == 1, again.lines
        assert said[0].startswith(
            f"M33: the camera reads PA {reading:.1f} and its angle was locked "
            f"at PA 30.0 by its first plate solve: 7.0 deg apart, beyond the "
            f"1.0 deg rotator tolerance"), said[0]
    else:
        assert said == [], said
    assert again.stored.set_aside == []


# ------------------------------------------- the tracking-refusal recovery

def _pin_the_mount(hub, monkeypatch) -> dict:
    """A mount at its meridian limit, as the AM5 was on 2026-08-21 and 22: it
    reads not-tracking until a park clears it, and while it is pinned every
    goto dies on the hub's ``set_tracking(True)``, which runs before the
    rotate loop, so a refused goto turns nothing. Installed over the
    harness's goto, which the recovery's own re-centre then reaches. The
    returned dict records each refused goto's ``rotation_deg`` and the
    parks."""
    tel = hub.devices["telescope"]
    st: dict = {"limit": True, "refused": [], "parks": 0}
    real_get, real_park = tel.get_tracking, tel.park
    harness_goto = hub.goto_and_center

    async def get_tracking():
        return bool(await real_get()) and not st["limit"]

    async def park():
        st["parks"] += 1
        st["limit"] = False
        await real_park()

    async def goto(ra_hours, dec_deg, *args, rotation_deg=None, **kw):
        if st["limit"]:
            st["refused"].append(rotation_deg)
            raise RuntimeError("tracking on rejected (reply '0')")
        return await harness_goto(ra_hours, dec_deg, *args,
                                  rotation_deg=rotation_deg, **kw)

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(hub, "goto_and_center", goto)
    return st


@pytest.mark.parametrize("case", ["locked", "planned", "unlocked"])
async def test_a_tracking_refusal_recovery_commands_the_angle(
        group_hub, monkeypatch, case):
    """Target setup's goto is refused by a mount pinned at its limit, and the
    park/unpark recovery re-acquires the target with a goto of its own. That
    re-centre is the only goto of the acquisition that moves anything, so it
    commands what setup commanded: the lock (M33 locked at PA 30.0 the night
    before), or the planned angle (framed at 45, as it always did). An
    unlocked target commands nothing and locks on the recovery's own solve,
    which is the acquisition's first fresh measurement.

    MUTANT "the recovery commands only the planned angle"
    (`_do_tracking_recovery` passes ``target.rotation_deg`` again, as it did
    before ruling 9): the locked M33 is re-centred with no angle, and would
    be shot wherever the rotator was left. RED on "locked" (observed):
        AssertionError: ('locked', [{'kw': {}, 'rotation_deg': None, 't':
        1788315493.0, 'who': 'M33'}])
        assert [None] == [30.0]
          At index 0 diff: None != 30.0
    """
    session = None
    target = m33(2, rotation_deg=45.0) if case == "planned" else m33(2)
    if case == "locked":
        first = await _night(group_hub, monkeypatch, plan_of(m33(1)),
                             sky=lambda who, n: 30.0)
        assert first.stored.locked_angles["m33"]["pa_deg"] == 30.0
        session = first.stored
    night = Night(group_hub, monkeypatch, t0=T0 + 1800.0,
                  sky=lambda who, n: 30.0)
    st = _pin_the_mount(group_hub, monkeypatch)
    start_kw = {"session": session} if session is not None else {}
    try:
        night.done = await night.run(plan_of(target), **start_kw)
    finally:
        await night.close()
    stored = session_store.load(night.session_id)
    assert night.done, night.trace[-3:]
    want = {"locked": 30.0, "planned": 45.0, "unlocked": None}[case]
    # Premises: setup commanded the angle and was refused, and the recovery
    # ran (one park) and ended in its own re-centre.
    assert st["refused"] == [want], (case, st["refused"])
    assert st["parks"] == 1, (case, night.lines)
    assert night.said("recovered in"), night.lines
    assert _commanded(night, "M33") == [want], (case, night.goto_calls)
    assert [c["target"] for c in night.captures] == (
        ["M33"] if case == "locked" else ["M33", "M33"]), night.captures
    if case == "unlocked":
        assert stored.locked_angles["m33"]["pa_deg"] == 30.0, (
            stored.locked_angles)
    if case == "planned":
        assert stored.locked_angles == {}, stored.locked_angles


# --------------------------------------------------- the flip re-centre

def m15(**kw) -> Target:
    """Twelve minutes east of the meridian at T0 and 60 degrees up at
    transit: the plan's 10 minute lead puts its flip point two minutes into
    the run."""
    return Target(id="m15", name="M15", ra_hours=ra_at(-0.2), dec_deg=10.0,
                  center=True, autofocus_first=False,
                  steps=[ExposureStep(id="m15-L", filter="L", exposure_s=30.0,
                                      count=10)], **kw)


@pytest.mark.parametrize("case", ["planned", "locked", "neither"])
async def test_the_flip_recentre_carries_the_angle(group_hub, monkeypatch,
                                                   case):
    """The meridian flip re-centres through ``hub.meridian_flip``, and ruling
    9 says the flip re-centre commands the angle like any acquisition. A
    target framed at PA 45 is flipped with ``rotation_deg=45.0``; an unframed
    one locked at 30.0 by its setup is flipped with ``rotation_deg=30.0``;
    with neither the call is exactly today's, ``(ra, dec)`` and no keyword at
    all. The hub's own flip runs (wrapped only to record the call), and its
    re-centre's goto commands the same angle. A flip that commands an angle
    is bounded with the rotation allowance a setup's goto gets, since its
    re-centre may run the rotate loop; one that commands none is bounded
    exactly as before.

    MUTANT "rotation dropped" (`_maybe_meridian_flip` makes today's call
    whatever the angle): RED on "planned" and "locked" (observed):
        [planned]
        AssertionError: ('planned', {})
        assert {} == {'rotation_deg': 45.0}
        [locked]
        AssertionError: ('locked', {})
        assert {} == {'rotation_deg': 30.0}

    MUTANT "no allowance for a rotating flip" (`_maybe_meridian_flip` hands
    `_flip_bounded` no ``extra_s``): RED on "planned" and "locked"
    (observed):
        [planned]
        AssertionError: ('planned', [0.0])
        assert [0.0] == [300.0]
        [locked]
        AssertionError: ('locked', [0.0])
        assert [0.0] == [300.0]
    """
    target = m15(rotation_deg=45.0) if case == "planned" else m15()
    sky = (lambda who, n: 30.0) if case == "locked" else None
    calls: list = []
    real_flip = group_hub.meridian_flip

    async def flip(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_flip(*args, **kwargs)

    monkeypatch.setattr(group_hub, "meridian_flip", flip)
    bounds: list = []
    real_bounded = SequenceEngine._flip_bounded

    async def bounded(self, coro, **kw):
        bounds.append(kw.get("extra_s", 0.0))
        return await real_bounded(self, coro, **kw)

    monkeypatch.setattr(SequenceEngine, "_flip_bounded", bounded)
    night = await _night(group_hub, monkeypatch,
                         plan_of(target, meridian_flip=True), sky=sky,
                         coords_clock=True)
    assert night.done, night.trace[-3:]
    assert calls, f"premise: the run flipped: {night.lines[-6:]}"
    want = {"planned": {"rotation_deg": 45.0},
            "locked": {"rotation_deg": 30.0}, "neither": {}}[case]
    for args, kwargs in calls:
        assert args == (target.ra_hours, target.dec_deg), args
        assert kwargs == want, (case, kwargs)
    recentres = _commanded(night, "M15")[1:]
    assert len(recentres) == len(calls), night.goto_calls
    assert set(recentres) == {want.get("rotation_deg")}, recentres
    allowance = 0.0 if case == "neither" else engine_mod.ROTATION_ALLOWANCE_S
    assert bounds == [allowance] * len(calls), (case, bounds)


async def test_the_rotation_allowance_keeps_a_rotating_flip_from_being_cut(
        group_hub, monkeypatch):
    """The flip's bounds were sized from a re-centre with no rotation in it
    (``FLIP_SLEW_TIMEOUT_S`` is ``GOTO_TIMEOUT_S`` plus a guider stop). Once
    the re-centre commands an angle it may run the rotate loop first, which
    a setup's goto is allowed ``ROTATION_ALLOWANCE_S`` for; without the same
    allowance the flip's bound would be a guillotine on its own inner step.
    The bounds are scaled down together (as
    test_the_flip_bound_covers_a_calibration.py scales them), so a flip that
    overruns ``FLIP_TIMEOUT_S`` but fits the allowance is not cut when it
    asks for the allowance, and is cut, as before, when it does not. No
    guider is connected, so the calibration stage cannot extend either.

    MUTANT "allowance ignored" (`_flip_bounded` adds ``extra_s`` to neither
    stage): RED (observed):
        astrodeck.sequence.engine.SafetyAbort: meridian flip timed out after
        0s
    """
    monkeypatch.setattr(engine_mod, "FLIP_TIMEOUT_S", 0.05)
    monkeypatch.setattr(engine_mod, "ROTATION_ALLOWANCE_S", 0.5)
    monkeypatch.setattr(group_hub, "guider", None)
    eng = SequenceEngine(group_hub)

    async def slow_flip():
        await asyncio.sleep(0.2)
        return {"flipped": True}

    got = await eng._flip_bounded(slow_flip(),
                                  extra_s=engine_mod.ROTATION_ALLOWANCE_S)
    assert got == {"flipped": True}
    with pytest.raises(SafetyAbort, match="meridian flip"):
        await eng._flip_bounded(slow_flip())
