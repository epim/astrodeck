# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every seam that sends a move asks the one gate again, and every caller of
the hub's refusal stops on it (#888, fix round 2).

The first round put the gate (``devices.base.position_known_for_motion``)
at the head of each path. A re-review found three kinds of gap, each a
latch that lands after the first ask and before the move:

- ``SunWatch._park`` asked nothing after ``tick``'s first ask, across the
  reopen, the position, tracking and parked reads (each up to 30 s) and the
  wait on the motion lock. OWNER RULING 4B (2026-10-09) forbids any park
  while latched, so it asks again before its "Parking now" line and again
  under the lock;
- ``Hub.goto_and_center`` asked only under its FIRST motion lock. The
  rotation pre-move and each centring attempt take the lock again after an
  await (the rotator read, the rotate loop, a solve and sync) and now ask
  again under it; the native TPPA asks before every leg of its arc;
- six engine callers of ``goto_and_center`` read its ``position_unknown``
  shape as a stop (`_stop_if_centring_refused_unknown`) with no test: the
  acquisition in `_setup_target`, the meridian flip, the rotator self-test,
  the re-centre after the unguided sweep, `_recentre_for_hold` (the lost
  guide star and the walking field) and the limit recovery's re-centre.

Fix round 3 added a case for each mutant a second review found surviving
(H5, H6, S2, S9, M1 to M4, M10): a latch during attempt 1's solve, a doubt
carried on the hub's record only, tracking never touched while latched, the
night hold in the safe order, and the latch state dropped by a net that
stops watching.

Each case asserts that nothing further reached the mount; each control
shows the same path moving. Every mutant named below was applied to a byte
backup of its production file, run under the suite's normal command (xdist
on), and the file restored from the backup with its sha256 checked.
Coordinates are made up; no mount position is printed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrodeck.devices.base import RIG_DOUBT_ATTR, DeviceError
from astrodeck.devices.sim import SimTelescope
from astrodeck.mount_offset import POSITION_UNKNOWN_MOTION_DETAIL
from astrodeck.polar import native as nat
from astrodeck.sequence.engine import PositionUnknownStop
from astrodeck.sun_watch import (BLIND_LOG_EVERY, POSITION_UNKNOWN_DAYLIGHT,
                                 POSITION_UNKNOWN_NIGHT_HOLD)

from test_850_engine_sync_refused import _no_hold, flip_hub  # noqa: F401
from test_888_every_move_asks_the_gate import (_REFUSED, _approach_rig,
                                               _check_words, _recorded_mount,
                                               _rig_at, _Stop, _west_rig)
from test_centring_settings_reach_goto import (_after_a_lost_star,
                                               _after_a_walking_field,
                                               _after_the_sweep, _guided,
                                               _setup, _target)
from test_recovery_centring_is_measured import (  # noqa: F401 (fixtures)
    _engine, _pinned_mount, sim_hub, temp_store)
from test_sun_watch import SUN_DEC, SUN_RA_H
from test_sun_watch import cfg, pinned_sun  # noqa: F401 (fixtures)
from test_w14_sun_watch_blind_fallback import _driven, _tick_n
from test_w15_sun_watch_follow_ups import sky  # noqa: F401 (fixture)
import test_the_flip_stops_paying_for_itself as flp

_REASON = "a fictional reason"


def _refusing(hub) -> None:
    """The recording double's ``goto_and_center`` answers the hub's
    position-unknown refusal, still recording the call."""
    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        return dict(_REFUSED)
    hub.goto_and_center = goto


def _stop_lines(bus_lines, where: str) -> list[str]:
    return [m for lvl, m, _s in bus_lines
            if lvl == "warning" and m.startswith(POSITION_UNKNOWN_MOTION_DETAIL)
            and m.endswith(f"the run stops without moving the mount ({where})")]


# =================================================== the sun watch's park

async def test_a_latch_set_during_the_tick_s_reads_gets_no_park(
        cfg, pinned_sun, sky, bus_lines):
    """OWNER RULING 4B. The tube is inside the cone with the Sun up, and
    the latch lands while ``tick`` waits on the parked read, after its
    first ask: no park, and no "Parking now" line paging as a park. The
    latched tick's own alert is said instead.

    MUTANT SW7 "the park asks once before its line" (the ``if self._
    latched_at_park(tel, cfg): return None`` before the "Parking now" line
    in `SunWatch._park` removed): RED. The ask under the motion lock still
    holds the park, so the loss shows as a held park paging as a park -
        AssertionError: [('error', 'SUN WATCH: the tube is pointing where
        the Sun will be ... Parking now.', 'safety'), ...]
    """
    hub, tel = _approach_rig(cfg)
    sky["alt"] = -2.0

    async def is_parked():
        tel.position_known = False          # the latch lands mid-tick
        return False
    tel.is_parked = is_parked
    w, _now = _driven(hub)
    await w.tick()
    assert tel.park_calls == 0, f"the park ran: {tel.park_calls}"
    assert not [m for _l, m, _s in bus_lines if "Parking now" in m], bus_lines
    assert [m for lvl, m, _s in bus_lines
            if lvl == "error" and m == POSITION_UNKNOWN_DAYLIGHT], bus_lines
    assert w.state()["position_unknown"] is True


async def test_a_latch_set_while_the_park_waits_on_the_lock_gets_no_park(
        cfg, pinned_sun, sky, bus_lines):
    """The same, with the latch set while `_park` waits on the hub's motion
    lock behind another motion: asked again under the lock.

    MUTANT SW8 "nothing asks under the lock" (the ``if self._latched_at_
    park(tel, cfg): return None`` inside ``async with lock:`` in
    `SunWatch._park` removed): RED -
        AssertionError: the park ran: 1
    """
    hub, tel = _approach_rig(cfg)
    sky["alt"] = -2.0
    lock = asyncio.Lock()
    hub._motion_lock = lock
    w, _now = _driven(hub)
    async with lock:
        ticking = asyncio.ensure_future(w.tick())
        for _ in range(50):
            await asyncio.sleep(0)
        assert not ticking.done(), "premise: the park waits on the lock"
        tel.position_known = False
    await ticking
    assert tel.park_calls == 0, f"the park ran: {tel.park_calls}"


async def test_control_with_the_position_known_the_locked_park_runs(
        cfg, pinned_sun, sky):
    """CONTROL. No latch: the park that waited on the lock is sent."""
    hub, tel = _approach_rig(cfg)
    sky["alt"] = -2.0
    lock = asyncio.Lock()
    hub._motion_lock = lock
    w, _now = _driven(hub)
    async with lock:
        ticking = asyncio.ensure_future(w.tick())
        for _ in range(50):
            await asyncio.sleep(0)
    await ticking
    assert tel.park_calls == 1, tel.park_calls


async def test_a_latched_tube_clear_of_the_sun_still_raises_the_alert(
        cfg, pinned_sun, sky, bus_lines):
    """OWNER RULING 4B: the latched state judges no approach at all, so a
    tube whose UNTRUSTED position reads clear of the Sun is no comfort. With
    the Sun above the dawn-park threshold the alert is said and the state is
    published, and the position is not even read. Nothing parks.

    MUTANT SW1 "the tick asks nothing" (the ``if not position_known_for_
    motion(self.hub, tel): self._position_unknown_tick(cfg); return`` block
    at the top of `SunWatch.tick` removed). The park's own asks (SW7, SW8)
    still hold every park, which is why the first round's two SW1 cases
    now pass under it; this one does not: RED -
        AssertionError: the untrusted position was read: ['position']
    """
    hub, tel = _rig_at((SUN_RA_H + 12.0) % 24.0, -SUN_DEC, tracking=True)
    tel.position_known = False
    reads: list[str] = []
    real = tel.get_position

    async def get_position():
        reads.append("position")
        return await real()
    tel.get_position = get_position
    sky["alt"] = -2.0
    w, _now = _driven(hub)
    await w.tick()
    assert reads == [], f"the untrusted position was read: {reads}"
    said = [m for lvl, m, _s in bus_lines
            if lvl == "error" and m == POSITION_UNKNOWN_DAYLIGHT]
    assert len(said) == 1, bus_lines
    assert w.state()["position_unknown"] is True
    assert tel.park_calls == 0


# ========================================= hub.goto_and_center's later locks

class _Rotator:
    connected = True


async def test_the_rotation_pre_move_asks_again(sim_hub, monkeypatch,
                                                bus_lines):
    """The latch lands while the hub reads the rotator, after the first
    lock's gate: the rotation pre-move sends no slew and returns the
    refused shape, with the safe order in its warning.

    MUTANT HR1 "the pre-move asks nothing" (the ``refused = self._centring_
    refused_unknown(tel, "the rotation pre-move", 0)`` check and its return
    removed from the rotation pre-move in `Hub.goto_and_center`): RED -
        test_888_every_move_asks_the_gate._Stop (the slew was sent)
    """
    tel, moves = _recorded_mount(sim_hub, monkeypatch, stop_at_slew=True)
    monkeypatch.setitem(sim_hub.devices, "rotator", _Rotator())

    async def already_set(rot, angle):
        tel.mark_position_unknown(_REASON)
        return None
    monkeypatch.setattr(sim_hub, "_rotation_already_set", already_set)
    res = await sim_hub.goto_and_center(7.25, 30.0, rotation_deg=40.0,
                                        max_attempts=1)
    assert "slew" not in moves, moves
    assert res["position_unknown"] is True and res["aborted"] is True
    assert res["reason"] == POSITION_UNKNOWN_MOTION_DETAIL
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"
            and m.endswith("Not moved further (the rotation pre-move)")]
    assert len(said) == 1, bus_lines
    _check_words(said[0])


async def test_each_centring_attempt_asks_again(sim_hub, monkeypatch):
    """The latch lands during the rotator read of a rotation that needs no
    turn (the pre-move is skipped): attempt 1's lock asks again and sends
    no slew. The rotation keys ride with the refused shape.

    MUTANT HR2 "the attempts ask nothing" (the ``refused = self._centring_
    refused_unknown(tel, f"centring attempt {attempt}", ...)`` check and its
    return removed from the attempt loop in `Hub.goto_and_center`): RED -
        test_888_every_move_asks_the_gate._Stop (the slew was sent)
    """
    tel, moves = _recorded_mount(sim_hub, monkeypatch, stop_at_slew=True)
    monkeypatch.setitem(sim_hub.devices, "rotator", _Rotator())
    already = {"rotated": False, "already_set": True}

    async def already_set(rot, angle):
        tel.mark_position_unknown(_REASON)
        return dict(already)
    monkeypatch.setattr(sim_hub, "_rotation_already_set", already_set)
    res = await sim_hub.goto_and_center(7.25, 30.0, rotation_deg=40.0,
                                        max_attempts=2)
    assert "slew" not in moves, moves
    assert res["position_unknown"] is True and res["attempts"] == 0
    assert res["rotation"] == already


@pytest.mark.parametrize("rotation", [None, {"already_set": True}],
                         ids=["pre-move", "attempt"])
async def test_control_with_the_position_known_the_later_locks_slew(
        sim_hub, monkeypatch, rotation):
    """CONTROL. No latch: the pre-move (rotator not yet at the angle) and
    attempt 1 (rotator already there) each send their slew."""
    tel, moves = _recorded_mount(sim_hub, monkeypatch, stop_at_slew=True)
    monkeypatch.setitem(sim_hub.devices, "rotator", _Rotator())

    async def already_set(rot, angle):
        return None if rotation is None else dict(rotation)
    monkeypatch.setattr(sim_hub, "_rotation_already_set", already_set)
    with pytest.raises(_Stop):
        await sim_hub.goto_and_center(7.25, 30.0, rotation_deg=40.0,
                                      max_attempts=1)
    assert "slew" in moves, moves


# ============================================ the native TPPA, every leg

class _LegTel(SimTelescope):
    def __init__(self, *, latch_on_read: bool):
        super().__init__("plain")
        self.connected = True
        self.latch_on_read = latch_on_read
        self.moves: list[str] = []

    async def get_position(self):
        if self.latch_on_read:
            self.mark_position_unknown(_REASON)
        return 7.25, 30.0

    async def slew(self, ra_hours, dec_deg):
        self.moves.append("slew")

    async def rotate_axis(self, axis, degrees):
        self.moves.append("rotate_axis")
        return degrees


class _LegHub:
    _motion_epoch = 3

    def __init__(self, tel):
        self.devices = {"telescope": tel}

    def _check_solar(self, ra, dec, *, force=False):
        return None


@pytest.mark.parametrize("axis", [False, True], ids=["goto", "axis turn"])
async def test_a_tppa_leg_asks_again_before_it_moves(monkeypatch, axis):
    """The latch lands during the leg's own position read (an AM5 reopen
    inside it): neither the goto nor the axis turn is sent, and the error
    says the run stopped, in the safe order.

    MUTANT TL1 "the legs ask nothing" (the ``if not position_known_for_
    motion(hub, tel): raise DeviceError(TPPA_POSITION_UNKNOWN_MID_RUN)`` in
    `_rotate_in_ra` removed): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.devices.base.DeviceError'>
    """
    monkeypatch.setattr(nat, "_SETTLE_AFTER_SLEW_S", 0.0)
    tel = _LegTel(latch_on_read=True)
    tel.can_rotate_axis = axis
    hub = _LegHub(tel)
    with pytest.raises(DeviceError) as caught:
        await nat._rotate_in_ra(hub, tel, 3, nat._RA_STEP_HOURS, 30.0)
    assert str(caught.value) == nat.TPPA_POSITION_UNKNOWN_MID_RUN
    assert tel.moves == [], tel.moves


@pytest.mark.parametrize("axis", [False, True], ids=["goto", "axis turn"])
async def test_control_a_known_position_tppa_leg_moves(monkeypatch, axis):
    """CONTROL. No latch: the leg is sent."""
    monkeypatch.setattr(nat, "_SETTLE_AFTER_SLEW_S", 0.0)
    tel = _LegTel(latch_on_read=False)
    tel.can_rotate_axis = axis
    await nat._rotate_in_ra(_LegHub(tel), tel, 3, nat._RA_STEP_HOURS, 30.0)
    assert tel.moves == (["rotate_axis"] if axis else ["slew"]), tel.moves


def test_the_mid_run_tppa_line_keeps_the_wording_rules():
    _check_words(nat.TPPA_POSITION_UNKNOWN_MID_RUN)
    assert len("native TPPA: " + nat.TPPA_POSITION_UNKNOWN_MID_RUN) <= 137
    assert not any(ch.isdigit() for ch in nat.TPPA_POSITION_UNKNOWN_MID_RUN)


# ====================================== the engine reads the hub's refusal

async def test_the_acquisition_stops_on_the_hub_s_refusal(bus_lines):
    """`_setup_target`'s inline centring, the path every centred
    acquisition takes: the refusal ends the run in its own words, never the
    no-light hold, and nothing further is sent.

    MUTANT RR1 "the acquisition ignores the shape" (the ``await self._stop_
    if_centring_refused_unknown(result, target, "acquiring the target")``
    in `_setup_target` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _refusing(hub)
    holds = _no_hold(e)
    with pytest.raises(PositionUnknownStop):
        await e._setup_target(0, t)
    assert holds == [], f"the refusal reached the no-light hold: {holds}"
    assert len(hub.gotos) == 1, hub.gotos
    assert hub.tel.calls == [], hub.tel.calls
    assert len(_stop_lines(bus_lines, "acquiring the target")) == 1, bus_lines


async def test_control_a_centred_acquisition_goes_on(bus_lines):
    """CONTROL. The same acquisition centred: no stop."""
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    await e._setup_target(0, t)
    assert len(hub.gotos) == 1
    assert _stop_lines(bus_lines, "acquiring the target") == []


async def test_the_meridian_flip_stops_on_the_hub_s_refusal(
        flip_hub, monkeypatch, bus_lines):
    """The REAL ``hub.meridian_flip``, its re-centre refused for an
    unknown position: nothing flipped, the run ends, and guiding is not
    restarted nor the post-flip sweep run on a tube nobody knows.

    MUTANT RR2 "the flip ignores the shape" (the ``await self._stop_if_
    centring_refused_unknown(flip_result, target, "the meridian flip")`` in
    `_maybe_meridian_flip` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=False)

    async def goto(ra_h, dec_d, **kw):
        st["gotos"].append((ra_h, dec_d))
        return dict(_REFUSED)
    monkeypatch.setattr(flip_hub, "goto_and_center", goto)
    with pytest.raises(PositionUnknownStop):
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert len(st["gotos"]) == 1, st["gotos"]
    assert "start" not in st["guider"].calls, st["guider"].calls
    assert st["af"] == [], st["af"]
    assert len(_stop_lines(bus_lines, "the meridian flip")) == 1, bus_lines


async def test_control_a_flip_that_moved_goes_on(flip_hub, monkeypatch,
                                                 bus_lines):
    """CONTROL. A flip whose re-centre centred: no stop."""
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["gotos"]
    assert _stop_lines(bus_lines, "the meridian flip") == []


def _self_test_rig(t):
    e, hub = _setup(t)
    hub.devices["rotator"] = _Rotator()
    hub._rotation_trusted = None
    ran: list[str] = []

    async def rotator_self_test(*a, **kw):
        ran.append("self-test")
        return {"followed": True}
    hub.rotator_self_test = rotator_self_test
    e._group_of = lambda target: SimpleNamespace(rotate=True,
                                                 name="Fictional M", id="m1")
    return e, hub, ran


async def test_the_rotator_self_test_stops_on_the_hub_s_refusal(bus_lines):
    """The self-test's own centring refused: the run ends before the
    self-test turns the rotator and solves.

    MUTANT RR3 "the self-test ignores the shape" (the ``await self._stop_
    if_centring_refused_unknown(placed, target, "the rotator self-test")``
    in `_ensure_rotator_self_test` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    t = _target(name="Fictional M 1-1", rotation_deg=30.0)
    e, hub, ran = _self_test_rig(t)
    _refusing(hub)
    with pytest.raises(PositionUnknownStop):
        await e._ensure_rotator_self_test(t)
    assert ran == [], f"the self-test ran: {ran}"
    assert len(hub.gotos) == 1, hub.gotos
    assert len(_stop_lines(bus_lines, "the rotator self-test")) == 1


async def test_control_a_centred_self_test_runs():
    """CONTROL. The self-test's centring centred: the self-test runs."""
    t = _target(name="Fictional M 1-1", rotation_deg=30.0)
    e, hub, ran = _self_test_rig(t)
    await e._ensure_rotator_self_test(t)
    assert ran == ["self-test"], ran


_RECENTRES = pytest.mark.parametrize(
    "recentre, where", [
        (_after_the_sweep, "re-centring after the unguided sweep"),
        (_after_a_lost_star, "re-centring after the guide star went missing"),
        (_after_a_walking_field, "re-centring after the guided field walked"),
    ], ids=["after the unguided sweep", "after a lost star",
            "after a walking field"])


@_RECENTRES
async def test_a_mid_run_recentre_stops_on_the_hub_s_refusal(
        recentre, where, bus_lines):
    """Each mid-run re-centre refused for an unknown position: the run ends
    after ONE call, before the guider is restarted.

    MUTANT RR4 "the unguided-sweep re-centre ignores the shape" (its ``await
    self._stop_if_centring_refused_unknown(res, target, "re-centring after
    the unguided sweep")`` removed): RED, "after the unguided sweep" -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    MUTANT RR5 "`_recentre_for_hold` ignores the shape" (its ``await
    self._stop_if_centring_refused_unknown(res, target, where)`` removed):
    RED, "after a lost star" and "after a walking field" -
        AssertionError: [two calls] (the second attempt was sent)
    """
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    _refusing(hub)
    with pytest.raises(PositionUnknownStop):
        await recentre(e, t)
    assert len(hub.gotos) == 1, hub.gotos
    assert "start" not in hub.guider.calls, hub.guider.calls
    assert len(_stop_lines(bus_lines, where)) == 1, bus_lines


@_RECENTRES
async def test_control_a_centred_mid_run_recentre_goes_on(recentre, where,
                                                          bus_lines):
    """CONTROL. The same re-centres centred: no stop."""
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    await recentre(e, t)
    assert len(hub.gotos) == 1
    assert _stop_lines(bus_lines, where) == []


async def test_the_limit_recovery_stops_on_the_hub_s_refusal(
        sim_hub, monkeypatch, bus_lines):
    """The frame loop's tracking recovery parked and unparked a mount at its
    limit, and its re-centre came back refused for an unknown position: the
    run ends in the recovery's own words, not "the recovery failed" and not
    "recovered".

    MUTANT RR6 "the limit recovery ignores the shape" (the ``await self._
    stop_if_centring_refused_unknown(result, target, where)`` in
    `_do_tracking_recovery` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    st = _pinned_mount(sim_hub, monkeypatch, [_REFUSED])
    e, t = _engine(sim_hub)
    with pytest.raises(PositionUnknownStop):
        await e._enforce_tracking(t.steps[0], t)
    assert st["events"] == ["park", "unpark", "goto"], st["events"]
    assert len(_stop_lines(bus_lines,
                           "recovering the mount from its limit")) == 1
    assert not [m for _l, m, _s in bus_lines if "recovered in" in m]


async def test_control_a_centred_limit_recovery_goes_on(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. The recovery's re-centre centred: no stop."""
    st = _pinned_mount(sim_hub, monkeypatch,
                       [{"centered": True, "error_arcmin": 0.2,
                         "attempts": 1}])
    e, t = _engine(sim_hub)
    await e._enforce_tracking(t.steps[0], t)
    assert st["events"] == ["park", "unpark", "goto"], st["events"]
    assert [m for _l, m, _s in bus_lines if "recovered in" in m]


# ============================== round 3: the survivors of the second review

def _carry_a_doubt(hub) -> None:
    """The doubt on the HUB's record only, taken on a telescope object a
    profile activate replaced (`rig_position_known`); the object in hand
    still reads known."""
    setattr(hub, RIG_DOUBT_ATTR, (SimTelescope("plain"), _REASON))


async def test_a_latch_set_in_attempt_one_s_solve_stops_attempt_two(
        sim_hub, monkeypatch):
    """The window the per-attempt ask names: attempt 1's solve and sync
    (an AM5 relink inside it). Attempt 1 slewed and solved off target, and
    attempt 2's correction slew is not sent.

    MUTANT H5 "only attempt 1 asks" (``refused = self._centring_refused_
    unknown(...)`` in the attempt loop of `Hub.goto_and_center` made
    ``refused = None if attempt > 1 else self._centring_refused_unknown(
    ...)``): RED -
        AssertionError: ['tracking True', 'slew', 'slew', ...]
    """
    tel, moves = _recorded_mount(sim_hub, monkeypatch)

    async def solve_and_sync(exposure_s, refusal_level="warning", **kw):
        tel.mark_position_unknown(_REASON)
        return {"ra_hours": 7.25, "dec_deg": 30.5}
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve_and_sync)
    res = await sim_hub.goto_and_center(7.25, 30.0, max_attempts=2)
    assert moves.count("slew") == 1, moves
    assert res["position_unknown"] is True and res["attempts"] == 1


async def test_an_attempt_reads_a_doubt_carried_from_a_replaced_object(
        sim_hub, monkeypatch):
    """The doubt lands on the hub's record during the rotator read, the
    telescope object still reading known: the attempt's ask reads the rig
    record (``position_known_for_motion``) and sends no slew.

    MUTANT H6 "the attempt reads only the object" (``if position_known_for_
    motion(self, tel):`` in `Hub._centring_refused_unknown` made ``if
    getattr(tel, "position_known", True):``): RED -
        test_888_every_move_asks_the_gate._Stop (the slew was sent)
    """
    tel, moves = _recorded_mount(sim_hub, monkeypatch, stop_at_slew=True)
    monkeypatch.setitem(sim_hub.devices, "rotator", _Rotator())

    async def already_set(rot, angle):
        _carry_a_doubt(sim_hub)
        return {"rotated": False, "already_set": True}
    monkeypatch.setattr(sim_hub, "_rotation_already_set", already_set)
    monkeypatch.setattr(sim_hub, RIG_DOUBT_ATTR, None, raising=False)
    res = await sim_hub.goto_and_center(7.25, 30.0, rotation_deg=40.0,
                                        max_attempts=1)
    assert "slew" not in moves, moves
    assert res["position_unknown"] is True


async def test_the_sun_watch_reads_a_doubt_carried_from_a_replaced_object(
        cfg, pinned_sun, sky, bus_lines):
    """The doubt on the hub's record only (a profile activate replaced the
    telescope object): the tick stands down as for a latched object, reads
    no position and publishes the state.

    MUTANT S2 "the tick reads only the object" (``if not position_known_
    for_motion(self.hub, tel):`` at the gate in `SunWatch.tick` made ``if
    not getattr(tel, "position_known", True):``): RED -
        AssertionError: the untrusted position was read: ['position']
    """
    hub, tel = _rig_at((SUN_RA_H + 12.0) % 24.0, -SUN_DEC, tracking=True)
    _carry_a_doubt(hub)
    reads: list[str] = []
    real = tel.get_position

    async def get_position():
        reads.append("position")
        return await real()
    tel.get_position = get_position
    sky["alt"] = -2.0
    w, _now = _driven(hub)
    await w.tick()
    assert reads == [], f"the untrusted position was read: {reads}"
    assert w.state()["position_unknown"] is True
    assert tel.park_calls == 0


@pytest.mark.parametrize("connected", [True, False], ids=["link up",
                                                          "link down"])
async def test_the_latched_net_never_touches_tracking(
        cfg, pinned_sun, sky, connected):
    """OWNER RULING 4B: the net "never changes tracking for that reason",
    the Sun up or down, the link up or down, across the alert's cadence.

    MUTANT S9 "the latched tick stops tracking" (``await tel.set_tracking(
    False)`` added before ``self._position_unknown_tick(cfg)`` at the gate
    in `SunWatch.tick`): RED, both -
        AssertionError: [False, False, ...]
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    tel.connected = connected
    w, now = _driven(hub)
    sky["alt"] = -30.0
    await _tick_n(w, now, 2)
    sky["alt"] = -2.0
    await _tick_n(w, now, BLIND_LOG_EVERY + 1)
    assert tel.tracking_calls == [], tel.tracking_calls
    assert tel.park_calls == 0 and "unpark" not in tel.calls


async def test_the_night_hold_is_said_once_in_the_safe_order(
        cfg, pinned_sun, sky, bus_lines):
    """Latched with the Sun below the dawn-park threshold: ONE info hold,
    exactly ``POSITION_UNKNOWN_NIGHT_HOLD`` behind its prefix, which
    test_888_every_move_asks_the_gate.py's wording case checks.

    MUTANT M10 "goto advice in the hold" (``self._hold(POSITION_UNKNOWN_
    NIGHT_HOLD, None)`` in `_position_unknown_tick` made ``self._hold(
    "slew it to a target, then Trust position", None)``): RED -
        AssertionError: [('info', 'sun watch held off: slew it to ...')]
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await _tick_n(w, now, 3)
    held = [(lvl, m) for lvl, m, _s in bus_lines
            if m.startswith("sun watch held off")]
    assert held == [("info",
                     "sun watch held off: " + POSITION_UNKNOWN_NIGHT_HOLD)], held


async def test_a_park_made_before_the_doubt_does_not_hold_one_after_trust(
        cfg, pinned_sun, sky):
    """The net parked the tube; it was then put back in the cone (a reset,
    moved by hand) and the latch set; after Trust position the net parks
    it again instead of holding on "the mount has not moved".

    MUTANT M1 "the old park remembered" (``self._acted = False`` in
    `_position_unknown_tick` removed): RED -
        AssertionError: the second park was held: 1
    """
    hub, tel = _approach_rig(cfg)
    ra0, dec0 = tel.ra_hours, tel.dec_deg
    sky["alt"] = -2.0
    w, now = _driven(hub)
    await w.tick()
    assert tel.park_calls == 1, "premise: the net parked it"
    tel.ra_hours, tel.dec_deg, tel.parked, tel.tracking = (ra0, dec0, False,
                                                          True)
    tel.position_known = False
    now["t"] += 60.0
    await w.tick()
    tel.position_known = True
    now["t"] += 60.0
    await w.tick()
    assert tel.park_calls == 2, f"the second park was held: {tel.park_calls}"


async def test_a_blind_streak_ends_when_the_latch_sets(cfg, pinned_sun, sky):
    """A blind streak (the link down) and then the latch: the state says
    position unknown, not blind, with no stale ``blind_since``.

    MUTANT M2 "the blind streak kept" (``self._drop_blind()`` in
    `_position_unknown_tick` removed): RED -
        AssertionError: {'blind': True, 'blind_since': ..., ...}
    """
    hub, tel, _age = _west_rig(cfg)
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await w.tick()
    tel.connected = False
    await _tick_n(w, now, 3)
    assert w.state()["blind"] is True, "premise: blind"
    tel.position_known = False
    await w.tick()
    st = w.state()
    assert st["blind"] is False and st["blind_since"] is None, st
    assert st["position_unknown"] is True, st


@pytest.mark.parametrize("why", ["solar avoidance off", "cone zero",
                                 "no telescope"])
async def test_a_net_that_stops_watching_drops_the_latch_state(
        cfg, pinned_sun, sky, why):
    """A net that stops watching (disarmed, a zero cone, no telescope)
    publishes no position-unknown state for the mount it no longer
    watches.

    MUTANT M3 "the disarm keeps it" (``self._drop_unknown()`` in the
    ``solar_avoidance`` arm of `SunWatch.tick` removed): RED, solar
    avoidance off. MUTANT M3B (the same in the zero-cone arm): RED, cone
    zero. MUTANT M4 (the same in the ``tel is None`` arm): RED, no
    telescope. Each -
        AssertionError: {'position_unknown': True, ...}
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await w.tick()
    assert w.state()["position_unknown"] is True, "premise: latched"
    if why == "solar avoidance off":
        cfg.safety.solar_avoidance = False
    elif why == "cone zero":
        cfg.safety.solar_exclusion_deg = 0.0
    else:
        del hub.devices["telescope"]
    now["t"] += 60.0
    await w.tick()
    st = w.state()
    assert st["position_unknown"] is False, st
    assert st["position_unknown_since"] is None, st
