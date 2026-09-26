"""A group's per-night pier state is kept in the session (#312, S3
orchestrator ruling 4; spec 5.7, 3.4, 5.9).

WHAT WAS WRONG. The one-pier-change rule (5.7) rests on ``GroupRun.flipped``
and the side the group's hops measured (``_group_side``), and both lived only
in the engine's memory for the run. A crash, a /recover or an auto-resume the
same night is a new run on the same session, and it started every group
unflipped with no side. Where a panel before the meridian had room again by
then (its window had opened, or it had cleared the mask, since the change),
the meridian rule let it through ahead of the panels past the meridian: its
hop took the group back across the pier, a second pier change that night, and
the next panel past the meridian made a third.

WHAT IT DOES NOW. ``Session.group_pier`` holds ``{group_id: {night, flipped,
side, verified}}``, written only through ``Session.note_group_pier`` with the
``events.night_key()`` of the moment and saved at once, the set-aside
record's pattern: at the group's first side read and at its pier change
(`_persist_group_pier`). ``start`` reads tonight's record back, and
`_start_groups` puts it into the group's run state (`_restore_group_pier`).
Another night's record is history: that night the group starts unflipped.

THE RUNS are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py) with ``coords_clock``, so the meridian countdown and
the sim mount's pier side both read the night's fake clock. A restart is a
second `Night` on the same hub, with a FRESH engine, started through
``engine.start(plan, session=...)`` on the session as the store holds it: the
first night is cut at a fake instant by its horizon and aborted, as a
restarted process finds the session on disk. The side every hop lands on is
read from the sim mount's own oracle at the moment it lands (`SideLog`, as
test_group_meridian.py reads it); the sim picks its side from the hour angle,
as the AM5 does (test_group_meridian.py asserts that premise first).

THE MOSAIC is a 1x2, 50 L frames a panel. Panel 1-2 stands 0.3 h east of the
meridian at the night's start, so it shoots on the pre-flip (west) side, runs
out of room before its flip point, and waits for its crossing, 18 min in.
Panel 1-1 is 1.2 h further east, and its window opens 30 min in, so at 1-2's
crossing nothing before the meridian can shoot: 1-2's hop is the group's one
pier change. From 30 min 1-1 is open, before the meridian, with 45 min of
room: a flipped group holds it for its crossing, an unflipped one shoots it.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim. Every mutant was applied in a private scratch copy of server/
(scratchpad s3-ec-mut-q7v2), never in the shared tree (#254):

* "not persisted": `_persist_group_pier` returns at once, so nothing is
  written.
* "not restored": `_restore_group_pier` returns at once, so the record is
  written and never read.
* "read regardless of night": `Session.group_pier_on` returns the group's
  record whatever night it names.
* "saved with the ledger only": `_persist_group_pier` without its
  ``session_store.save_run_state`` call, so the record reaches the disk
  only with the next frame's save.
* "no default": ``Session.group_pier`` declared with no default, a required
  field.
* "typed dict": ``Session.group_pier`` typed ``dict[str, dict]``, so a
  value that is not a dict fails the file's validation.
* "any record is a dict": `Session.group_pier_on` without its ``isinstance``
  check.
* "no night check": `Session.note_group_pier` without its refusal of an
  empty night.
* "any side": `Session.note_group_pier` without its refusal of a side that
  is not east, west or None.

Three more, added by the S3-EC verifier and run the same way (scratchpad
s3-ec-verify-k4m9), because each put back less than the record holds and
every case above stayed green under it:

* "restored as flipped": `_restore_group_pier` sets ``run.flipped`` True
  for any record of tonight's, even one written before the pier change.
* "side not restored": `_restore_group_pier` puts back ``flipped`` alone,
  neither the side nor whether it was verified.
* "verified not restored": `_restore_group_pier` puts back the side but not
  whether it was verified.
* "unread-then-read not persisted": `_group_pier_check` without the
  `_persist_group_pier` call after the first side read past an unreadable
  change.
"""
from __future__ import annotations

import json
import time as _time

import pytest

from _group_harness import (GROUP_ID, GROUP_NAME, LON, T0, Night, grid_plan,
                            group_hub, group_store)
from astrodeck.events import night_key
from astrodeck.sequence import schedule
from astrodeck.sequence.session import Session, session_store

#: Hour angle runs this much faster than the clock.
SIDEREAL = 1.0027379093
#: One sidereal day in clock seconds: the sky stands where it stood.
SIDEREAL_DAY_S = 86164.0905
ONE_ONE, ONE_TWO = f"{GROUP_NAME} 1-1", f"{GROUP_NAME} 1-2"
#: The fake minute 1-1's window opens (a whole local minute, 29.85 min in).
OPENS = T0 + 1791.0
#: Where the first night is cut: after 1-2's pier change and after 1-1 opens.
RESTART_S = 35 * 60.0
#: Where a night is cut BEFORE the pier change: 1-2 is still shooting on the
#: pre-flip side, with room left before its flip point.
EARLY_S = 100.0


def crossing(ra: float) -> float:
    return T0 + schedule.hours_to_meridian_flip(ra, LON, T0) * 3600.0 / SIDEREAL


def _plan():
    """The 1x2 of the module docstring: 1-1 at HA -1.5 h, 1-2 at HA -0.3 h,
    50 L frames each, flips on, 1-1's window opening at ``OPENS``."""
    plan = grid_plan(rows=1, cols=2, meridian_flip=True,
                     panel_kw={"filters": ("L",), "count": 50,
                               "ha_h": -1.5, "ha_step_h": 1.2})
    hhmm = _time.strftime("%H:%M", _time.localtime(OPENS))
    one_one = plan.targets[0]
    one_one.schedule = one_one.schedule.model_copy(
        update={"start_mode": "time", "start_time": hhmm})
    return plan


class SideLog:
    """The side the sim mount lands on at every hop, across nights, as
    ``(t, target, side)``, read from the device's own oracle inside the
    harness's goto, on whichever night is running."""

    def __init__(self, hub):
        self.tel = hub.devices["telescope"]
        self.sides: list[tuple[float, str, str]] = []
        self.night: Night | None = None

    def goto(self, who, n, result):
        side = self.tel._side_for_ra(self.tel.rig.ra_hours).value
        self.sides.append((self.night.clock.t, who, side))
        return result

    def changes(self) -> list[tuple[float, str, str]]:
        return [(round((t - T0) / 60.0, 2), w, s)
                for (_t0, _w0, s0), (t, w, s) in zip(self.sides,
                                                     self.sides[1:])
                if s != s0]


async def _run(hub, monkeypatch, plan, log: SideLog, *, t0: float = T0,
               horizon_s: float | None = None, session=None,
               before_run=None, wall_s: float = 120.0) -> Night:
    kw = {} if horizon_s is None else {"horizon_s": horizon_s}
    night = Night(hub, monkeypatch, t0=t0, coords_clock=True, goto=log.goto,
                  **kw)
    log.night = night
    if before_run is not None:
        before_run(night)
    start_kw = {} if session is None else {"session": session}
    try:
        night.done = await night.run(plan, wall_s=wall_s, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _spy_saved_record(night, into: list) -> None:
    """A pass-through spy on `_group_pier_check`: after each real call
    returns, the group's record AS THE SESSION FILE ON DISK HOLDS IT, with
    the fake time. It replaces nothing the engine decides."""
    real = night.engine._group_pier_check

    async def check(target, group):
        out = await real(target, group)
        on_disk = session_store.load(night.engine._session.id)
        into.append((night.clock.t, target.name,
                     dict(on_disk.group_pier.get(group.id) or {})))
        return out

    night.mp.setattr(night.engine, "_group_pier_check", check)


async def _first_night(hub, monkeypatch, log, *, saved=None):
    """The first night, cut at ``RESTART_S`` after 1-2's pier change, with
    the premises of the module docstring asserted."""
    plan = _plan()
    start, _stop = schedule.resolve_window(
        plan.targets[0].schedule, {"latitude": 40.0, "longitude": LON},
        -12.0, T0)
    assert start == OPENS, f"premise: 1-1 opens {start - T0:.0f} s in"
    before = None if saved is None else (lambda n: _spy_saved_record(n, saved))
    night = await _run(hub, monkeypatch, plan, log, horizon_s=RESTART_S,
                       before_run=before)
    assert night.done is False, "premise: the first night was cut short"
    changes = log.changes()
    assert len(changes) == 1 and changes[0][1:] == (ONE_TWO, "east"), (
        f"premise: the first night made its one pier change at 1-2: "
        f"{changes}")
    one_two = night.engine.plan.targets[1].ra_hours
    assert crossing(one_two) < log.sides[-1][0], "premise"
    assert night.said("the mosaic changed pier side at 1-2"), night.lines[-4:]
    assert [w for _t, w in night.gotos if w == ONE_ONE] == [], (
        "premise: the flipped group held 1-1 once it opened")
    return night


# ------------------------------------------------ a restart the same night

async def test_a_restart_after_the_pier_change_keeps_one_change(group_hub,
                                                                monkeypatch):
    """The first night makes its one pier change at 1-2's hop, 18 min in,
    and is cut at 35 min, after 1-1 has opened. A fresh engine resumes the
    session the same night. It reads tonight's record back, so the group is
    flipped from its start: 1-2 goes on, 1-1 waits for its crossing, and
    every hop of both runs lands west and then east, exactly one pier
    change, and the session completes.

    MUTANT "not persisted": RED (observed; unflipped, the restarted run
    shoots 1-1 on the west side, and 1-2's next hop crosses back):
        AssertionError: 3 pier changes across the restart: [(18.25, 'M31
        1-2', 'east'), (35.25, 'M31 1-1', 'west'), (60.25, 'M31 1-2',
        'east')]
        assert 3 == 1
         +  where 3 = len([(18.25, 'M31 1-2', 'east'), (35.25, 'M31 1-1',
        'west'), (60.25, 'M31 1-2', 'east')])
    MUTANT "not restored": RED (observed), the same three changes, word for
    word.
    """
    log = SideLog(group_hub)
    first = await _first_night(group_hub, monkeypatch, log)
    second = await _run(group_hub, monkeypatch, first.stored.plan, log,
                        t0=first.clock.t, session=first.stored)
    assert night_key(second.t0) == night_key(T0), "premise: the same night"
    assert second.done, second.trace[-3:]
    changes = log.changes()
    assert len(changes) == 1, (
        f"{len(changes)} pier changes across the restart: {changes}")
    # What the restart read: the first night's record, tonight's key.
    rec = first.stored.group_pier.get(GROUP_ID)
    assert rec == {"night": night_key(T0), "flipped": True, "side": "east",
                   "verified": True}, rec
    assert second.said("it changed pier side earlier tonight"), (
        second.lines[:4])
    one_one = second.engine.plan.targets[0].ra_hours
    hop = min(t for t, w in second.gotos if w == ONE_ONE)
    assert hop >= crossing(one_one), (
        f"1-1 was shot {crossing(one_one) - hop:.0f} s before its crossing")
    assert second.stored.status == "complete", second.stored.status


async def test_a_restart_before_the_pier_change_starts_unflipped(
        group_hub, monkeypatch):
    """CONTROL: a record written BEFORE the pier change says only the side
    the group measured, and a restart the same night reads it as that. The
    first night is cut ``EARLY_S`` in, while 1-2 is still shooting on the
    pre-flip (west) side; the restarted run takes 1-2 up at once, on the
    west side and before its crossing, makes the group's one pier change at
    1-2's crossing, and completes, as the uncut night does.

    MUTANT "restored as flipped": RED (observed; flipped from its start on
    the west side it had only measured, the group held 1-2 for its
    crossing, then deferred every hop past it for reading east):
        AssertionError: the restarted run's first hop was M31 1-2 on the
        east side 18.26 min in; 1-2 crosses at 17.95 min
        assert (('M31 1-2', 'east') == ('M31 1-2', 'west')
          At index 1 diff: 'east' != 'west'
    MUTANT "not persisted": RED (observed), on the record's assertion:
        AssertionError: {}
        assert None == {'flipped': False, 'night': '2026-09-01', 'side':
        'west', 'verified': False}
    """
    log = SideLog(group_hub)
    plan = _plan()
    first = await _run(group_hub, monkeypatch, plan, log, horizon_s=EARLY_S)
    assert first.done is False, "premise: the first night was cut short"
    assert log.changes() == [], "premise: no pier change before the cut"
    assert first.stored.group_pier.get(GROUP_ID) == {
        "night": night_key(T0), "flipped": False, "side": "west",
        "verified": False}, first.stored.group_pier
    n_first = len(log.sides)
    second = await _run(group_hub, monkeypatch, first.stored.plan, log,
                        t0=first.clock.t, session=first.stored)
    assert second.done, second.trace[-3:]
    one_two = crossing(plan.targets[1].ra_hours)
    t, who, side = log.sides[n_first]
    assert (who, side) == (ONE_TWO, "west") and t < one_two, (
        f"the restarted run's first hop was {who} on the {side} side "
        f"{(t - T0) / 60.0:.2f} min in; 1-2 crosses at "
        f"{(one_two - T0) / 60.0:.2f} min")
    changes = log.changes()
    assert len(changes) == 1 and changes[0][1:] == (ONE_TWO, "east"), (
        f"pier changes across the restart: {changes}")
    assert second.said("it changed pier side earlier tonight") == []
    assert second.stored.status == "complete", second.stored.status


async def test_a_restart_holds_the_group_to_the_side_it_changed_to(
        group_hub, monkeypatch):
    """THE SIDE COMES BACK WITH THE FLIP, and it is a safety check, not
    bookkeeping. The first night changes to the east side at 1-2 and is cut
    at ``RESTART_S``. In the restarted run every goto leaves the mount on
    the pre-flip WEST side (the sim's side oracle held there for this run
    only), as a mount that tracks on through the meridian may: past the
    meridian that is counterweight-up. The run knows the group changed to
    the east side tonight, so it defers every hop that reads west, shoots
    nothing, and sets both panels aside. Without the side, the first hop's
    west became the group's side and the frames were shot.

    The pre-flip sightings the frame loop's flip-owed check keeps are this
    run's only (`_pre_flip_side` is cleared at every start), so after a
    restart the restored side is the one guard on this.

    MUTANT "side not restored": RED (observed; every frame the session
    owed was shot on the west side past the meridian):
        AssertionError: the restarted run shot 58 frames with the mount on
        the west side, past the meridian: [(35.25, 'M31 1-2'), (35.75,
        'M31 1-2'), (36.25, 'M31 1-2')]
        assert [(35.25, 'M31...31 1-2'), ...] == []
          Left contains 58 more items, first extra item: (35.25, 'M31 1-2')
    It is GREEN under "verified not restored" (observed): the side alone is
    what holds the hops here.
    """
    from astrodeck.devices.base import PierSide
    log = SideLog(group_hub)
    first = await _first_night(group_hub, monkeypatch, log)
    tel = group_hub.devices["telescope"]
    n_first = len(log.sides)

    def stay_west(night):
        night.mp.setattr(tel, "_side_for_ra", lambda ra: PierSide.WEST)

    second = await _run(group_hub, monkeypatch, first.stored.plan, log,
                        t0=first.clock.t, session=first.stored,
                        before_run=stay_west)
    assert second.done, second.trace[-3:]
    assert {s for _t, _w, s in log.sides[n_first:]} == {"west"}, "premise"
    shot = [(round((c["t"] - T0) / 60.0, 2), c["target"])
            for c in second.captures]
    assert shot == [], (
        f"the restarted run shot {len(shot)} frames with the mount on the "
        f"west side, past the meridian: {shot[:3]}")
    assert second.said("it reads west after the goto, and the M31 mosaic "
                       "changed to the east side"), second.lines[:4]
    assert sorted(r["target_id"] for r in second.stored.set_aside) == [
        "p00", "p01"], second.stored.set_aside


async def test_a_restart_takes_no_flip_the_group_already_made(group_hub,
                                                              monkeypatch):
    """THE VERIFIED SIDE COMES BACK TOO. A hop past the meridian disarms
    the flip latch only on a VERIFIED side (`_group_pier_check`), and 1-1,
    held for its crossing and taken up just past it, is acquired close
    enough past transit that `_arm_meridian_flip` arms the latch for it.
    Uncut, the night verified the east side at 1-2's pier change and 1-1's
    hops disarm the latch: no meridian flip is taken, and every hop shoots
    a frame. The restarted run reads back that the east side was verified,
    so it takes none either.

    The uncut night is run first, from ``T0``, as the measured control.

    MUTANT "verified not restored": RED (observed; the latch re-slewed 1-1
    again and again for a flip the group had already made, and the mount
    stayed east each time):
        AssertionError: the restarted run took meridian flips the uncut
        night did not: ['meridian flip: stopping guiding and re-slewing',
        '...']
        assert ['meridian fl...-walked', ...] == []
          Left contains 44 more items, first extra item: 'meridian flip:
        stopping guiding and re-slewing'
    MUTANT "side not restored": RED (observed), the same words.
    """
    uncut = await _run(group_hub, monkeypatch, _plan(), SideLog(group_hub))
    assert uncut.done and uncut.stored.status == "complete", "premise"
    assert uncut.said("meridian flip") == [], uncut.said("meridian flip")
    assert len(uncut.gotos) == len(uncut.captures), "premise: a hop a frame"
    log = SideLog(group_hub)
    first = await _first_night(group_hub, monkeypatch, log)
    second = await _run(group_hub, monkeypatch, first.stored.plan, log,
                        t0=first.clock.t, session=first.stored)
    assert second.done, second.trace[-3:]
    flips = second.said("meridian flip")
    assert flips == [], (
        f"the restarted run took meridian flips the uncut night did not: "
        f"{flips[:1] + ['...'] if flips else flips}")
    assert len(second.gotos) == len(second.captures), (
        len(second.gotos), len(second.captures))
    assert second.stored.status == "complete", second.stored.status


async def test_the_record_is_on_disk_the_moment_the_side_is_measured(
        group_hub, monkeypatch):
    """SAVED AT ONCE, the set-aside record's pattern: right after the hop
    that measures the group's first side, and right after the hop that is
    its pier change, the session FILE already holds the record, before any
    frame of that visit is saved with the ledger. A crash in the next
    exposure keeps it.

    MUTANT "saved with the ledger only": RED (observed):
        AssertionError: after the first hop the file held {}
        assert {} == {'flipped': F...ified': False}
          Right contains 4 more items:
          {'flipped': False, 'night': '2026-09-01', 'side': 'west',
        'verified': False}
    MUTANT "not persisted": RED (observed), the same words.
    """
    log = SideLog(group_hub)
    saved: list = []
    await _first_night(group_hub, monkeypatch, log, saved=saved)
    tonight = night_key(T0)
    first = saved[0]
    assert first[2] == {"night": tonight, "flipped": False, "side": "west",
                        "verified": False}, (
        f"after the first hop the file held {first[2]}")
    change_t = T0 + log.changes()[0][0] * 60.0
    at_change = next(s for s in saved if s[0] >= change_t - 1.0)
    assert at_change[2] == {"night": tonight, "flipped": True,
                            "side": "east", "verified": True}, (
        f"after the pier change the file held {at_change[2]}")


async def test_the_first_side_after_an_unread_change_is_saved(group_hub,
                                                              monkeypatch):
    """A PIER CHANGE WHOSE SIDE COULD NOT BE READ is saved as flipped with no
    side, and the first hop after it that reads a side makes that side the
    one later hops are held to (`_group_pier_check`): it goes to the file at
    once as well, so a restart tonight holds them to it too. Here the
    mount's side read returns nothing for 1-2's first hop past its crossing
    only (the pier change); every other read is the sim's own.

    MUTANT "unread-then-read not persisted": RED (observed; the file kept
    the unread side for the rest of the night):
        AssertionError: after the first readable hop past the change the
        file held {'night': '2026-09-01', 'flipped': True, 'side': None,
        'verified': False}
          Differing items:
          {'side': None} != {'side': 'east'}
    """
    log = SideLog(group_hub)
    saved: list = []
    changed: list = []
    ra = _plan().targets[1].ra_hours

    def unread_change(night):
        engine = night.engine
        real_check, real_side = engine._group_pier_check, engine._pier_side_now
        unread = {"on": False}

        async def side_now():
            return None if unread["on"] else await real_side()

        async def check(target, group):
            first = (not changed and target.name == ONE_TWO
                     and night.clock.t > crossing(ra))
            if first:
                changed.append(night.clock.t)
            unread["on"] = first
            try:
                return await real_check(target, group)
            finally:
                unread["on"] = False

        night.mp.setattr(engine, "_pier_side_now", side_now)
        night.mp.setattr(engine, "_group_pier_check", check)
        _spy_saved_record(night, saved)

    await _run(group_hub, monkeypatch, _plan(), log, horizon_s=RESTART_S,
               before_run=unread_change)
    tonight = night_key(T0)
    assert len(changed) == 1, "premise: 1-2 hopped past its crossing"
    after = [rec for t, _who, rec in saved if t >= changed[0]]
    assert after[0] == {"night": tonight, "flipped": True, "side": None,
                        "verified": False}, (
        f"after the unread change the file held {after[0]}")
    assert len(after) > 1, "premise: a hop after the change"
    assert after[1] == {"night": tonight, "flipped": True, "side": "east",
                        "verified": False}, (
        f"after the first readable hop past the change the file held "
        f"{after[1]}")


# ----------------------------------------------------------- the next night

async def test_the_next_night_starts_unflipped(group_hub, monkeypatch):
    """The session the first night left, flipped, is taken up one sidereal
    day after the cut: the sky stands exactly where it stood, and the night
    key is the next night's. Last night's record is history, so the group
    starts UNFLIPPED: 1-1, open and before the meridian with room, is shot
    first, on the pre-flip side, exactly as a group that had not flipped
    would be, and the record the night writes carries its own night.

    MUTANT "read regardless of night": RED (observed):
        AssertionError: the next night began at M31 1-2 on the east side, as
        a group flipped last night
        assert 'M31 1-2' == 'M31 1-1'
          - M31 1-1
          ?       ^
          + M31 1-2
          ?       ^
    """
    log = SideLog(group_hub)
    first = await _first_night(group_hub, monkeypatch, log)
    t1 = T0 + RESTART_S + SIDEREAL_DAY_S
    assert night_key(t1) != night_key(T0), "premise: another night"
    session = first.stored
    assert session.group_pier[GROUP_ID]["flipped"] is True, "premise"
    n_first = len(log.sides)
    second = await _run(group_hub, monkeypatch, session.plan, log, t0=t1,
                        session=session, horizon_s=600.0)
    _t, who, side = log.sides[n_first]
    assert who == ONE_ONE, (
        f"the next night began at {who} on the {side} side, as a group "
        f"flipped last night")
    assert side == "west", side
    assert second.said("it changed pier side earlier tonight") == []
    rec = second.stored.group_pier[GROUP_ID]
    assert rec["night"] == night_key(t1) and rec["flipped"] is False, rec


# ------------------------------------------------------- the record itself

def test_an_older_session_file_without_the_field_loads(tmp_path):
    """A session file written before #312 has no ``group_pier``: it loads,
    reads as no record for any group or night, and so starts every group
    unflipped, today's behaviour. A record that is not a dict (the file is
    JSON anyone can edit) reads as none, and never fails the load, since a
    file that fails validation vanishes from every scan.

    MUTANT "no default": RED (observed), at the first load:
        pydantic_core._pydantic_core.ValidationError: 1 validation error for
        Session
        group_pier
          Field required [type=missing, input_value={'id': 's-old',
        'schema_v...[], 'locked_angles': {}}, input_type=dict]
        astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: s-old (fails validation)
    MUTANT "typed dict" (``dict[str, dict]``): RED (observed), at the second
    load:
        pydantic_core._pydantic_core.ValidationError: 1 validation error for
        Session
        group_pier.m31-mosaic
          Input should be a valid dictionary [type=dict_type,
        input_value='flipped', input_type=str]
        astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: s-old (fails validation)
    MUTANT "any record is a dict": RED (observed):
        AttributeError: 'str' object has no attribute 'get'
    """
    # Written out by hand, the fields a session file had before #312, so
    # the file owes nothing to this build's model.
    raw = {"id": "s-old", "schema_version": 1, "name": "old",
           "created_ts": T0, "updated_ts": T0, "status": "dormant",
           "nights": [], "frames": [], "auto_resume": True, "origin": "flow",
           "origin_id": "f1", "crash_resumes": 0, "set_aside": [],
           "locked_angles": {}}
    path = session_store._path("s-old")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert "group_pier" not in path.read_text(encoding="utf-8"), "premise"
    old = session_store.load("s-old")
    assert old.group_pier == {}
    assert old.group_pier_on(GROUP_ID, night_key(T0)) is None
    raw["group_pier"] = {GROUP_ID: "flipped"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    odd = session_store.load("s-old")
    assert odd.group_pier_on(GROUP_ID, night_key(T0)) is None


def test_the_writer_refuses_what_no_night_could_read():
    """``note_group_pier`` replaces the group's record (the state moves
    forward through a night), and refuses a record no restart could use: an
    empty night, which no ``night_key`` ever is, and a side the mount could
    not have reported. An unread side (None) is kept, and is never
    verified.

    MUTANT "no night check": RED (observed):
        Failed: DID NOT RAISE <class 'ValueError'>
    MUTANT "any side": RED (observed):
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    s = Session()
    s.note_group_pier("g", night="2026-09-01", flipped=False, side="west",
                      verified=False)
    s.note_group_pier("g", night="2026-09-01", flipped=True, side=None,
                      verified=True)
    assert s.group_pier == {"g": {"night": "2026-09-01", "flipped": True,
                                  "side": None, "verified": False}}
    with pytest.raises(ValueError):
        s.note_group_pier("g", night="", flipped=True, side="east",
                          verified=True)
    with pytest.raises(ValueError):
        s.note_group_pier("g", night="2026-09-01", flipped=True,
                          side="Pier.EAST", verified=True)
    assert s.group_pier["g"]["side"] is None, "a refusal wrote nothing"
