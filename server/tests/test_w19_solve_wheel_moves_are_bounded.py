# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The plate solve's filter borrow is bounded and survives a cancel
(WP-164, #815 and #816).

WHAT WENT WRONG. ``Hub._borrow_wheel_for_solve`` drives the wheel to a slot a
solve can see through, and ``_return_wheel_after_solve`` sends it back. Both
awaited ``fw.set_position`` with no bound, and neither could survive a cancel.

* #815. ``AlpacaFilterWheel.set_position`` polls ``while position == -1``, and
  every poll SUCCEEDS, so a wheel jammed between slots never trips a transport
  timeout. The sequence engine bounds the same call (``_apply_filter``,
  ``FILTER_MOVE_TIMEOUT_S``); the hub's borrow and return did not, so a jammed
  wheel hung a centring solve, a rotate, a rotator sync and a guide-offset
  measurement for ever.
* #816. Every caller awaits the borrow OUTSIDE the ``finally`` that returns the
  wheel (the borrow's return value is the slot to return). A cancel that landed
  while the move was on the wire (Stop, a shutdown) skipped the borrow's
  ``except Exception`` and the caller's ``finally`` both, and the wheel stayed
  on the solve filter. The next frame's ``_apply_filter`` derived its focus
  offset from a slot the focuser never travelled to.

THE FIX. Every wheel await in the borrow and the return is bounded by
``SOLVE_WHEEL_MOVE_TIMEOUT_S`` (the engine's 90 s), and a move that times out is
a move that failed half-way, so the wheel is asked where it is and the way back
is owed whenever it may have left (#723). A cancel that lands once the move
command may have been sent sends the wheel home on a shielded task of its own,
bounded by ``SOLVE_WHEEL_CANCEL_RESTORE_TIMEOUT_S``, and is then raised on. The
two callers that read the wheel or narrate between the borrow and their
``finally`` now do it inside the try.

The wheel cases run on the simulator's wheel with its Ha, OIII and SII slots
marked narrowband, scripted by ``_Wheel``. Bounds are shortened so a case of a
stall does not wait out the real ones; they are read at call time.

Named mutants, each run from a byte backup of ``hub.py`` and restored with a
byte copy (md5 compared). The first failing assertion is quoted with the case
that raised it.

* "borrow move unbounded" (``await asyncio.wait_for(fw.set_position(want),
  SOLVE_WHEEL_MOVE_TIMEOUT_S)`` made ``await fw.set_position(want)``) ->
  ``test_a_jammed_wheel_does_not_hang_the_borrow``: Failed, "the borrow waited
  on a wheel that never finished its move" (and the three callers' cases).
* "return unbounded" (the same in ``_return_wheel_after_solve``) ->
  ``test_a_jammed_wheel_does_not_hang_the_return``: Failed, "the wheel's return
  waited on a move that never finished".
* "no restore on cancel" (the ``except asyncio.CancelledError`` clause of the
  borrow made to ``raise`` at once) -> ``test_a_cancel_mid_move_sends_the_wheel_
  home``: AssertionError, "a stop during the borrow's move left the wheel on
  slot 0, the solve filter".
* "restore unbounded" (the bound passed to ``_run_to_its_bound`` in
  ``_restore_wheel_after_cancel`` made ``3600.0``) -> ``test_a_jammed_wheel_
  cannot_make_a_stop_hang``: Failed, "a stop waited on a jammed wheel".
* "restore not shielded" (``_run_to_its_bound(lambda: fw.set_position(int(
  slot)), ...)`` made ``asyncio.wait_for(fw.set_position(int(slot)), ...)``) ->
  ``test_a_second_cancel_does_not_cut_the_restore_short``: AssertionError, "a
  second stop cut the wheel's way home short".
* "restore without a move" (``sent_from`` made always set) -> ``test_a_cancel_
  before_any_move_command_owes_nothing``: AssertionError, "a stop before any
  move was sent still cost the wheel a command".
* "the filter read outside the try" (the guide offset's ``through`` read moved
  back above its ``try``) -> ``test_a_stop_on_the_guide_offsets_filter_read_
  returns_the_wheel``: AssertionError, "a stop on the guide offset's filter
  read left the wheel on slot 0".
* "the borrow outside the narration's try" (``solve_and_sync``'s borrow moved
  back above its outer ``try``) -> ``test_a_stop_in_the_solve_borrow_clears_the_
  exposing_narration``: AssertionError, "a stop during the borrow left the
  solve narrated as exposing".
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401 (fixture import)

#: The wheel the sim rig carries, with Ha, OIII and SII marked narrowband the
#: way a rig's profile marks them (the sim marks none by default).
L_SLOT, HA_SLOT = 0, 5
NARROWBAND = [False, False, False, False, True, True, True, False]


class _Wheel:
    """The sim wheel's ``set_position`` scripted. The Nth move (1-based) in
    ``hang_on`` never finishes: with ``lands`` the wheel reaches the slot first
    and then goes on polling for ever (Position -1 for ever), without it the
    wheel never leaves (a command that was never obeyed). While a move is hung
    the wheel reads as an Alpaca wheel in transit does: ``get_position``
    clamps to 0 and ``is_moving`` is True, unless ``reads_home`` says the wheel
    is standing still on the slot it started on. Every attempt is recorded."""

    def __init__(self, hub, monkeypatch, *, hang_on=(), lands=True,
                 reads_home=False, settle_s: float = 0.0):
        self.fw = hub.devices["filterwheel"]
        self.fw.filter_narrowband = list(NARROWBAND)
        self.rig = hub.sim_rig
        self.attempts: list[int] = []
        self.hang_on = set(hang_on)
        self.lands = lands
        self.reads_home = reads_home
        self.settle_s = settle_s
        self.hung = False
        #: Set when a hung move has begun, and when a later move has.
        self.started = asyncio.Event()
        self.restoring = asyncio.Event()
        #: Lets a hung move go, so a case that finds the code under test
        #: waiting on it can say so and clean up instead of waiting with it.
        self.release = asyncio.Event()
        real_get = self.fw.get_position
        real_moving = self.fw.is_moving

        async def set_position(slot):
            self.attempts.append(int(slot))
            n = len(self.attempts)
            if n in self.hang_on:
                if self.lands:
                    self.rig.filter_slot = int(slot)
                self.hung = True
                self.started.set()
                await self.release.wait()              # Position -1 for ever
            if self.hung:
                self.restoring.set()
            # The sim wheel's own move sleeps 0.4 s a slot in real time, which
            # a bound of a fifth of a second would cut off; the cases grade the
            # commands and where the wheel ends, so the move is instant here.
            await asyncio.sleep(self.settle_s)
            self.rig.filter_slot = int(slot)
            self.hung = False

        async def get_position():
            if self.hung and not self.reads_home:
                return 0
            return await real_get()

        async def is_moving():
            if self.hung:
                return not self.reads_home
            return await real_moving()

        monkeypatch.setattr(self.fw, "set_position", set_position)
        monkeypatch.setattr(self.fw, "get_position", get_position)
        monkeypatch.setattr(self.fw, "is_moving", is_moving)


@pytest.fixture
def short_bounds(monkeypatch):
    """Both wheel bounds shortened so a case of a stall does not wait out the
    real ones. ``raising=False``: on code without the constants a case fails on
    what it grades (a wait with no end), not on a missing attribute."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 0.2,
                        raising=False)
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_CANCEL_RESTORE_TIMEOUT_S", 0.2,
                        raising=False)


@pytest.fixture
def cancel_bounds(monkeypatch):
    """For the cases of a cancel: the move bound LONG, so it cannot fire before
    the cancel the case sends and turn a case of a Stop into one of a jam, and
    the restore bound short."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 30.0,
                        raising=False)
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_CANCEL_RESTORE_TIMEOUT_S", 0.2,
                        raising=False)


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    real = events_mod.bus.log

    def record(level, message, source="hub", **kw):
        lines.append((level, message, source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


# ----------------------------------------------------------------- #815


def test_the_move_bound_is_the_engines_and_the_restore_bound_is_shorter():
    """The hub cannot import the engine to borrow its bound, so a test keeps
    the two in step: a wheel move is as long for a solve as for a frame. The
    restore a Stop waits on is shorter than that, or the Stop would wait the
    longer."""
    from astrodeck.sequence import engine as engine_mod
    assert hub_module.SOLVE_WHEEL_MOVE_TIMEOUT_S == engine_mod.FILTER_MOVE_TIMEOUT_S
    assert 0 < hub_module.SOLVE_WHEEL_CANCEL_RESTORE_TIMEOUT_S < (
        hub_module.SOLVE_WHEEL_MOVE_TIMEOUT_S)


async def test_a_jammed_wheel_does_not_hang_the_borrow(sim_hub, monkeypatch,
                                                       short_bounds):
    """The wheel on Ha and jammed on its way to luminance: Position -1 for
    ever. The borrow gives up on the move, finds the wheel in transit, and
    reports the way back to Ha as owed, as it does for any move that fails
    half-way (#723)."""
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=False)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    try:
        back = await asyncio.wait_for(sim_hub._borrow_wheel_for_solve(), 3.0)
    except asyncio.TimeoutError:
        pytest.fail("the borrow waited on a wheel that never finished its move")

    assert wheel.attempts == [L_SLOT], wheel.attempts
    assert back == HA_SLOT, (
        "the move timed out with the wheel in transit and the borrow did not "
        f"owe the way back: {back!r}")


async def test_a_move_that_times_out_with_the_wheel_still_home_owes_nothing(
        sim_hub, monkeypatch, short_bounds):
    """CONTROL. A move that never answers from a wheel that is standing still
    on the slot it started on is a refused move, not a half-made one: nothing
    is owed, and no second command goes to a wheel that has just ignored one."""
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=False,
                   reads_home=True)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    try:
        back = await asyncio.wait_for(sim_hub._borrow_wheel_for_solve(), 3.0)
    except asyncio.TimeoutError:
        pytest.fail("the borrow waited on a wheel that never finished its move")

    assert back is None, f"the wheel never left slot 5 and a return was owed: {back!r}"
    await sim_hub._return_wheel_after_solve(back)
    assert wheel.attempts == [L_SLOT], wheel.attempts


async def test_a_timed_out_move_that_reached_its_slot_is_sent_back(
        sim_hub, monkeypatch, short_bounds):
    """The wheel reached luminance and went on polling for ever (the reply
    never came). It is on the solve filter, so the caller's return must put it
    back, and does."""
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=True)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    back = await asyncio.wait_for(sim_hub._borrow_wheel_for_solve(), 3.0)
    assert sim_hub.sim_rig.filter_slot == L_SLOT, "premise: the move landed"
    await asyncio.wait_for(sim_hub._return_wheel_after_solve(back), 3.0)

    assert back == HA_SLOT
    assert sim_hub.sim_rig.filter_slot == HA_SLOT
    assert wheel.attempts == [L_SLOT, HA_SLOT], wheel.attempts


async def test_a_jammed_wheel_does_not_hang_the_return(sim_hub, monkeypatch,
                                                       short_bounds):
    """The borrow's move landed and the way back jams. The return gives up,
    says so with the bound in the line (a bare timeout has no text of its own),
    and leaves the correction to the next frame's filter move."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(2,), lands=False)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    back = await sim_hub._borrow_wheel_for_solve()
    assert back == HA_SLOT and wheel.attempts == [L_SLOT], "premise"
    try:
        await asyncio.wait_for(sim_hub._return_wheel_after_solve(back), 3.0)
    except asyncio.TimeoutError:
        pytest.fail("the wheel's return waited on a move that never finished")

    said = [ln for ln in logs if "could not return the wheel" in ln[1]]
    assert len(said) == 1 and said[0][0] == "warning", logs
    assert "no answer within" in said[0][1], said[0][1]
    assert "()" not in said[0][1], said[0][1]


async def test_a_jammed_wheel_does_not_hang_a_rotate(sim_hub, monkeypatch,
                                                     short_bounds):
    """Through a real caller. ``rotate_to_pa`` solves twice, each frame between
    a borrow and a return. The first borrow's move jams; the rotate goes on,
    solves, and the wheel ends where the run left it."""
    sim_hub.sim_rig.filter_slot = HA_SLOT
    sim_hub.sim_rig.rotator_pa_offset_deg = 20.0
    sim_hub.sim_rig.rotator_mech_deg = 10.0
    _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=True)

    try:
        result = await asyncio.wait_for(sim_hub.rotate_to_pa(40.0), 20.0)
    except asyncio.TimeoutError:
        pytest.fail("the rotate waited on a wheel that never finished its move")

    assert result["rotated"] is True, result
    assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
        f"the wheel was left on slot {sim_hub.sim_rig.filter_slot} after a "
        "borrow whose move jammed")


# ----------------------------------------------------------------- #816


async def _cancelled_mid_move(hub, wheel) -> asyncio.Task:
    """Start the borrow, wait for its move to be on the wire, and cancel."""
    task = asyncio.ensure_future(hub._borrow_wheel_for_solve())
    await asyncio.wait_for(wheel.started.wait(), 5.0)
    task.cancel()
    return task


async def test_a_cancel_mid_move_sends_the_wheel_home(sim_hub, monkeypatch,
                                                      cancel_bounds):
    """Stop pressed while the borrow's move is on the wire and the wheel has
    reached luminance. The wheel must end on the slot it started on, and the
    cancel must still reach whoever pressed Stop."""
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=True)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    task = await _cancelled_mid_move(sim_hub, wheel)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5.0)

    assert task.cancelled(), "the stop was swallowed on its way out of the borrow"
    assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
        f"a stop during the borrow's move left the wheel on slot "
        f"{sim_hub.sim_rig.filter_slot}, the solve filter")
    assert wheel.attempts == [L_SLOT, HA_SLOT], wheel.attempts


async def test_a_jammed_wheel_cannot_make_a_stop_hang(sim_hub, monkeypatch,
                                                      cancel_bounds):
    """The way home jams as well. The Stop waits for the restore for its bound
    and no longer: the cancel arrives, the wheel is still jammed, and the
    task ends cancelled, with the failure said."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1, 2), lands=True)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    task = await _cancelled_mid_move(sim_hub, wheel)
    t0 = time.monotonic()
    # ``asyncio.wait``, not ``wait_for``: the restore runs behind a shield, so
    # cancelling the task on a timeout would not end it either.
    done, _pending = await asyncio.wait({task}, timeout=3.0)
    if task not in done:
        wheel.release.set()
        await asyncio.wait({task}, timeout=5.0)
        pytest.fail("a stop waited on a jammed wheel")

    assert task.cancelled()
    assert time.monotonic() - t0 < 2.0
    said = [ln for ln in logs if "could not be sent back" in ln[1]]
    assert len(said) == 1 and said[0][0] == "warning", logs


async def test_a_second_cancel_does_not_cut_the_restore_short(
        sim_hub, monkeypatch, cancel_bounds):
    """Stop pressed twice, or a shutdown on top of a Stop: the second cancel
    lands while the wheel is on its way home, and the way home is not
    abandoned halfway by it."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_CANCEL_RESTORE_TIMEOUT_S", 5.0,
                        raising=False)
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=True,
                   settle_s=0.3)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    task = await _cancelled_mid_move(sim_hub, wheel)
    await asyncio.wait_for(wheel.restoring.wait(), 5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5.0)

    assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
        "a second stop cut the wheel's way home short")


async def test_a_cancel_before_any_move_command_owes_nothing(
        sim_hub, monkeypatch, cancel_bounds):
    """CONTROL. A cancel that lands on the borrow's first position read has
    sent nothing: the wheel is where it was and no command goes to it."""
    wheel = _Wheel(sim_hub, monkeypatch)
    sim_hub.sim_rig.filter_slot = HA_SLOT
    reading = asyncio.Event()

    async def get_position():
        reading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(wheel.fw, "get_position", get_position)
    task = asyncio.ensure_future(sim_hub._borrow_wheel_for_solve())
    await asyncio.wait_for(reading.wait(), 5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5.0)

    assert wheel.attempts == [], (
        f"a stop before any move was sent still cost the wheel a command: "
        f"{wheel.attempts}")
    assert sim_hub.sim_rig.filter_slot == HA_SLOT


async def test_a_stop_in_the_rotate_borrow_leaves_the_wheel_home(
        sim_hub, monkeypatch, cancel_bounds):
    """Through a real caller: ``rotate_to_pa`` stopped while its first borrow
    turns the wheel."""
    sim_hub.sim_rig.filter_slot = HA_SLOT
    sim_hub.sim_rig.rotator_pa_offset_deg = 20.0
    sim_hub.sim_rig.rotator_mech_deg = 10.0
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=True)

    task = asyncio.ensure_future(sim_hub.rotate_to_pa(40.0))
    await asyncio.wait_for(wheel.started.wait(), 10.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10.0)

    assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
        f"a stop during the rotate's borrow left the wheel on slot "
        f"{sim_hub.sim_rig.filter_slot}")


async def test_a_stop_in_the_solve_borrow_clears_the_exposing_narration(
        sim_hub, monkeypatch, cancel_bounds):
    """``solve_and_sync`` publishes ``exposing`` before it borrows and clears
    it in a ``finally``. A cancel that landed on the borrow was outside that
    ``finally``, and an idle rig showed "exposing" for good."""
    wheel = _Wheel(sim_hub, monkeypatch, hang_on=(1,), lands=True)
    sim_hub.sim_rig.filter_slot = HA_SLOT
    activity: list[object] = []
    real_publish = events_mod.bus.publish

    def publish(type, **data):
        if data.get("action") == "solve_activity":
            activity.append(data.get("activity"))
        return real_publish(type, **data)
    monkeypatch.setattr(events_mod.bus, "publish", publish)

    task = asyncio.ensure_future(sim_hub.solve_and_sync(0.05))
    await asyncio.wait_for(wheel.started.wait(), 10.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10.0)

    assert "exposing" in activity, f"premise: the narration began: {activity}"
    assert activity[-1] is None, (
        f"a stop during the borrow left the solve narrated as exposing: "
        f"{activity}")
    assert sim_hub.sim_rig.filter_slot == HA_SLOT


async def test_a_stop_on_the_guide_offsets_filter_read_returns_the_wheel(
        sim_hub, monkeypatch, cancel_bounds):
    """The guide-scope offset reads which filter the frame goes through between
    its borrow and its ``try``. A cancel landing on that read, with the wheel
    borrowed and moved, skipped the return."""
    wheel = _Wheel(sim_hub, monkeypatch)
    sim_hub.sim_rig.filter_slot = HA_SLOT
    reading = asyncio.Event()

    async def narrowband_filter_loaded():
        reading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(sim_hub, "_narrowband_filter_loaded",
                        narrowband_filter_loaded)

    task = asyncio.ensure_future(sim_hub.measure_guide_offset(
        exposure_s=0.05, guide_exposure_s=0.05))
    await asyncio.wait_for(reading.wait(), 10.0)
    assert sim_hub.sim_rig.filter_slot == L_SLOT, "premise: the wheel is borrowed"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10.0)

    assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
        f"a stop on the guide offset's filter read left the wheel on slot "
        f"{sim_hub.sim_rig.filter_slot}")
    assert wheel.attempts == [L_SLOT, HA_SLOT], wheel.attempts
