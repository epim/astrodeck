# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A wheel move that fails half-way is still owed its way back (WP-143, #723).

WHAT WENT WRONG. ``Hub._borrow_wheel_for_solve`` returns the slot to restore
only once ``fw.set_position(want)`` has RETURNED. A wheel move that raises after
the command reached the device (an Alpaca poll that times out while the wheel is
in transit, a transport error on the reply to a move that was accepted) went to
the broad ``except`` and came back as ``None``: "nothing moved". The wheel had
moved, or was moving. Every caller treats ``None`` as nothing to put back, so
the solve ran through luminance and the wheel stayed there.

That is not cosmetic. ``SequenceEngine._apply_filter`` derives the focuser's
per-filter offset delta from the wheel's REAL slot at the start of each frame,
and the focuser never travelled the offset between the run's filter and the
luminance slot the borrow left it on. The next frame's delta crosses a slot
boundary the drawtube never did, and the frame is shot one filter's worth of
steps out of focus (the borrow's own docstring names this as the reason it is
symmetric at all).

THE FIX. Once the move command has been sent, a failure no longer returns
"nothing moved". The borrow asks the wheel where it is: still on the slot it
started on means nothing is owed and the solve goes on as it did; anywhere else,
or no answer, means the way back is owed and the caller's ``finally`` sends it.

THE ENGINE HALF IS NOT HERE. Issue #723 as filed is the same defect one layer
up, in ``SequenceEngine._sweep_through_luminance`` (``sequence/engine.py``,
which this work package does not own): a failure after ``_apply_filter`` moved
the wheel and the focuser returns ``None``, so ``_autofocus`` never puts the
filter back. It is reported, not built; see the work package's return.

Named mutants, each run from a byte backup of ``hub.py`` inside this worktree and
restored byte-identically (sha256 compared, the mutant text grepped absent). The
first failing assertion is quoted verbatim, with the case that raised it.

* "failed move owes nothing" (the ``if where == int(current):`` that follows the
  failed move made ``if True:``, so every failed move reports nothing to put
  back, which is what the code did before) ->
  ``test_a_move_that_lands_and_then_raises_is_still_owed_a_return``,
  ``AssertionError: the borrow's move landed and then raised, and the borrow
  reported nothing to put back: None``; and
  ``test_the_rotate_puts_the_wheel_back_after_a_borrow_that_failed_half_way``,
  ``AssertionError: the wheel was left on slot 0 after a borrow whose move
  failed half-way``; and the unreadable-wheel case with the same text as the
  first.
* "always owed" (the same condition made ``if False:``, so every failed move
  owes a return without asking where the wheel is) ->
  ``test_a_move_the_wheel_refused_owes_nothing``,
  ``AssertionError: the wheel refused the move and never left slot 5, and the
  borrow still owed a return: 5``.
* "unreadable wheel owes nothing" (the reads after the failed move answer
  ``where, moving = int(current), False`` when they raise, instead of
  ``None, True``) ->
  ``test_a_wheel_that_cannot_say_where_it_is_is_assumed_to_have_left``,
  ``AssertionError: the wheel could not be read after a failed move and the
  borrow reported nothing to put back: None``.
* "a turning wheel counts as home" (``if where == int(current) and not
  moving:`` made ``if where == int(current):``, which an Alpaca wheel in
  transit off slot 0 satisfies because ``get_position`` clamps its -1 to 0) ->
  ``test_a_wheel_in_transit_off_slot_zero_is_not_taken_for_one_still_on_it``,
  ``AssertionError: a wheel in transit off slot 0 read as still being on slot
  0, and the borrow reported nothing to put back: None``.
"""
from __future__ import annotations

from _simhub import sim_hub  # noqa: F401 (fixture import)

#: The wheel the sim rig carries, with its Ha, OIII and SII marked narrowband
#: the way a rig's profile marks them (the sim marks none by default).
L_SLOT, HA_SLOT = 0, 5
NARROWBAND = [False, False, False, False, True, True, True, False]


class _Wheel:
    """The sim wheel's ``set_position`` scripted: the Nth move (1-based) raises,
    either AFTER the wheel has gone to the slot (the command landed and the
    reply did not come) or BEFORE it (the command was refused). Every attempt is
    recorded, so a test can see what the wheel was asked to do."""

    def __init__(self, hub, monkeypatch, *, fail_on=(1,), lands=True,
                 unreadable_after_failure=False):
        self.fw = hub.devices["filterwheel"]
        self.fw.filter_narrowband = list(NARROWBAND)
        self.rig = hub.sim_rig
        self.attempts: list[int] = []
        self.fail_on = set(fail_on)
        self.lands = lands
        self.failed = False
        real_set = self.fw.set_position
        real_get = self.fw.get_position

        async def set_position(slot):
            self.attempts.append(int(slot))
            if len(self.attempts) in self.fail_on:
                if self.lands:
                    await real_set(slot)
                self.failed = True
                raise RuntimeError("wheel timed out answering the move")
            return await real_set(slot)

        async def get_position():
            if unreadable_after_failure and self.failed:
                raise RuntimeError("wheel not answering")
            return await real_get()

        monkeypatch.setattr(self.fw, "set_position", set_position)
        monkeypatch.setattr(self.fw, "get_position", get_position)


async def test_a_move_that_lands_and_then_raises_is_still_owed_a_return(
        sim_hub, monkeypatch):
    """The wheel on Ha. The borrow's move to luminance reaches the wheel and
    the wheel goes, and then the call raises. The wheel is on luminance, so the
    borrow must say the way back to Ha is owed, and sending it must put the
    wheel there."""
    wheel = _Wheel(sim_hub, monkeypatch)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    back = await sim_hub._borrow_wheel_for_solve()

    assert sim_hub.sim_rig.filter_slot == L_SLOT, "premise: the move landed"
    assert back == HA_SLOT, (
        "the borrow's move landed and then raised, and the borrow reported "
        f"nothing to put back: {back!r}")
    await sim_hub._return_wheel_after_solve(back)
    assert sim_hub.sim_rig.filter_slot == HA_SLOT
    assert wheel.attempts == [L_SLOT, HA_SLOT], wheel.attempts


async def test_a_move_the_wheel_refused_owes_nothing(sim_hub, monkeypatch):
    """CONTROL. The command is refused and the wheel never leaves Ha. Nothing
    moved, so nothing is owed: no second command to a wheel that has just
    refused one, and the solve goes on as it did before this existed."""
    wheel = _Wheel(sim_hub, monkeypatch, lands=False)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    back = await sim_hub._borrow_wheel_for_solve()

    assert sim_hub.sim_rig.filter_slot == HA_SLOT, "premise: the wheel never left"
    assert back is None, (
        "the wheel refused the move and never left slot 5, and the borrow "
        f"still owed a return: {back!r}")
    await sim_hub._return_wheel_after_solve(back)
    assert wheel.attempts == [L_SLOT], (
        f"a refused borrow cost a second wheel command: {wheel.attempts}")


async def test_a_wheel_in_transit_off_slot_zero_is_not_taken_for_one_still_on_it(
        sim_hub, monkeypatch):
    """The Alpaca wheel reports Position -1 while it turns and ``get_position``
    clamps that to 0, so a wheel that has just left slot 0 READS as slot 0. A
    wheel that started on slot 0 (Ha, here) and is now in transit therefore
    answers "I am where I started" and must still be sent home: the position
    alone cannot say, so the borrow asks whether it is standing still too."""
    fw = sim_hub.devices["filterwheel"]
    fw.filter_names = ["Ha", "L", "R", "G", "B", "S", "Oiii", "Dark"]
    fw.filter_narrowband = [True, False, False, False, False, True, True, False]
    sim_hub.sim_rig.filter_slot = 0
    turning = {"now": False}

    async def set_position(slot):
        turning["now"] = True          # the wheel left slot 0 ...
        raise RuntimeError("wheel timed out answering the move")  # ... unheard

    async def get_position():
        return 0                       # the clamp: Position -1 reads as 0

    async def is_moving():
        return turning["now"]

    monkeypatch.setattr(fw, "set_position", set_position)
    monkeypatch.setattr(fw, "get_position", get_position)
    monkeypatch.setattr(fw, "is_moving", is_moving)

    back = await sim_hub._borrow_wheel_for_solve()

    assert back == 0, (
        "a wheel in transit off slot 0 read as still being on slot 0, and the "
        f"borrow reported nothing to put back: {back!r}")


async def test_a_wheel_that_cannot_say_where_it_is_is_assumed_to_have_left(
        sim_hub, monkeypatch):
    """The move raised and now the wheel will not answer a position read. Not
    knowing is not the same as not having moved: the command was sent, so the
    way back is owed (a restore to the slot it is already on costs one command;
    leaving it on luminance costs the next frame its focus)."""
    _Wheel(sim_hub, monkeypatch, unreadable_after_failure=True)
    sim_hub.sim_rig.filter_slot = HA_SLOT

    back = await sim_hub._borrow_wheel_for_solve()

    assert back == HA_SLOT, (
        "the wheel could not be read after a failed move and the borrow "
        f"reported nothing to put back: {back!r}")


async def test_the_rotate_puts_the_wheel_back_after_a_borrow_that_failed_half_way(
        sim_hub, monkeypatch):
    """Through a real caller. ``rotate_to_pa`` solves twice, each frame between
    a borrow and a return. The FIRST borrow's move lands and raises; the second
    borrow is clean. The wheel started on Ha and must end on Ha, not on the
    luminance slot the failed borrow left it on."""
    sim_hub.sim_rig.filter_slot = HA_SLOT
    sim_hub.sim_rig.rotator_pa_offset_deg = 20.0
    sim_hub.sim_rig.rotator_mech_deg = 10.0
    _Wheel(sim_hub, monkeypatch)

    result = await sim_hub.rotate_to_pa(40.0)

    assert result["rotated"] is True, result
    assert sim_hub.sim_rig.filter_slot == HA_SLOT, (
        f"the wheel was left on slot {sim_hub.sim_rig.filter_slot} after a "
        "borrow whose move failed half-way")


async def test_a_clean_borrow_is_unchanged(sim_hub, monkeypatch):
    """CONTROL. A borrow whose move succeeds reports the way back and the
    return sends it, exactly as before: two commands, the wheel home."""
    wheel = _Wheel(sim_hub, monkeypatch, fail_on=())
    sim_hub.sim_rig.filter_slot = HA_SLOT

    back = await sim_hub._borrow_wheel_for_solve()
    await sim_hub._return_wheel_after_solve(back)

    assert back == HA_SLOT
    assert wheel.attempts == [L_SLOT, HA_SLOT], wheel.attempts
    assert sim_hub.sim_rig.filter_slot == HA_SLOT
