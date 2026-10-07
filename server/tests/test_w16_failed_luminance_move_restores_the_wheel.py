# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A luminance move that fails partway is put back, and put back WITHOUT an
offset the focuser never took (#723, backlog WP-143; wave 16 integration).

WHAT WENT WRONG. ``SequenceEngine._sweep_through_luminance`` moves the wheel
to luminance through ``_apply_filter``, which is two moves: the wheel, then
the focuser's per-filter offset. Any failure after the wheel had moved went to
the broad ``except`` and came back as ``None``, "nothing moved": the sweep ran
through luminance, ``_autofocus`` had nothing to restore, and the wheel stayed
on luminance. The next frame's ``_apply_filter`` then derives its offset delta
from the wheel's REAL slot, a slot boundary the focuser never travelled, and
the frame is shot one filter's worth of steps out of focus. The hub's own
borrow (``Hub._borrow_wheel_for_solve``) had the same half; WP-143 fixed it
(test_w16_failed_borrow_restores_the_wheel.py), and this is the engine half
that package could not own.

THE FIX, in three places in ``sequence/engine.py``:

* ``_apply_filter`` puts the wheel back itself (``_put_the_wheel_back``) when
  the focuser's offset step fails, so wheel and focuser agree again and the
  failure still propagates as it always did (``SafetyAbort`` is let through
  untouched: its teardown owns the devices);
* ``_sweep_through_luminance`` asks the wheel where it is (``_wheel_is_home``)
  when the move raised: home means nothing is owed and the sweep goes on
  through the filter in the beam; anywhere else, or no answer, means the way
  back is OWED, so it returns the restore name and records that the offset
  never landed (``_luminance_offset_landed``);
* ``_restore_filter_after_sweep`` moves the wheel ALONE in that case
  (``_apply_filter(..., apply_offset=False)``), never applying the inverse of
  an offset the focuser never took.

THE RIG is the simulator's: its wheel, with R made narrowband so the sweep
prefers luminance one slot away (offsets R 12, L 0), and its focuser.

NAMED MUTANTS. Each was run from a byte backup of ``sequence/engine.py``
inside the integration worktree, restored byte-identically (sha256 compared)
and the mutant text grepped out, under one worker. The failing assertion each
produced is recorded on the test that caught it.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

R_SLOT, L_SLOT = 1, 0


def _engine(sim_hub):
    """An engine on the simulator's rig, offsets ON, with the wheel on R and R
    marked narrowband, so the sweep goes to luminance (one slot away) and the
    move out applies ``offsets[L] - offsets[R]`` = -12 steps."""
    fw = sim_hub.devices["filterwheel"]
    fw.filter_narrowband = [False, True] + [False] * 6
    e = SequenceEngine(sim_hub)
    t = Target(name="T", ra_hours=1.0, dec_deg=40.0,
               steps=[ExposureStep(filter="R", exposure_s=0.05, count=1)])
    e.plan = SequencePlan(targets=[t], apply_filter_offsets=True)
    e._cfg = None
    return e, fw, sim_hub.devices["focuser"]


async def _on_r(fw) -> None:
    await fw.set_position(R_SLOT)


class _FocuserFailsOnce:
    """Make the focuser's first ``get_position`` raise, as a focuser whose
    answer is lost when the wheel has just moved: the offset step cannot
    start. Later calls answer."""

    def __init__(self, foc, monkeypatch) -> None:
        self.real = foc.get_position
        self.calls = 0

        async def get_position():
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("focuser did not answer")
            return await self.real()

        monkeypatch.setattr(foc, "get_position", get_position)


async def test_a_failed_offset_sends_the_wheel_back_and_the_sweep_goes_on(
        sim_hub, monkeypatch):
    """The wheel moved to luminance, the focuser's offset did not follow:
    ``_apply_filter`` puts the wheel back, the sweep is told nothing is owed
    (``None``) and goes on through R, and the focuser never moved.

    RED under mutant "the wheel is not sent back" (the ``await self.
    _put_the_wheel_back(fw, old_slot, label)`` of ``_apply_filter``'s handler
    made ``pass``), observed (the wheel stays on luminance, and the sweep's
    own owed-return then sends it back, so the failure is the answer, not the
    final position):

        AssertionError: nothing is owed once the wheel is back: 'R'
        assert 'R' is None
    """
    e, fw, foc = _engine(sim_hub)
    await _on_r(fw)
    before = await foc.get_position()
    _FocuserFailsOnce(foc, monkeypatch)
    back = await e._sweep_through_luminance("test")
    assert back is None, f"nothing is owed once the wheel is back: {back!r}"
    assert await fw.get_position() == R_SLOT, (
        f"the wheel was left on slot {await fw.get_position()} after a "
        f"failed offset move")
    monkeypatch.delattr(foc, "get_position")       # the focuser answers again
    assert await foc.get_position() == before, "the focuser moved"


async def test_a_wheel_that_cannot_be_sent_back_is_owed_a_return_with_no_offset(
        sim_hub, monkeypatch):
    """The offset failed AND the wheel could not be sent back: it sits on
    luminance with no offset applied. The sweep returns the name to restore,
    and the restore moves the wheel ALONE: the focuser, which never took the
    offset out, is not asked to take its inverse.

    RED under mutant "an owed return is not returned" (the ``return back`` of
    the not-home branch in ``_sweep_through_luminance`` made ``return None``),
    observed:

        AssertionError: the wheel is on slot 0 and nothing is owed: None

    RED under mutant "the restore applies the offset anyway" (``apply_offset=
    self._luminance_offset_landed`` in ``_restore_filter_after_sweep`` made
    ``apply_offset=True``), observed:

        AssertionError: the restore moved the focuser by 12 steps for an
        offset it never took
    """
    e, fw, foc = _engine(sim_hub)
    await _on_r(fw)
    before = await foc.get_position()
    _FocuserFailsOnce(foc, monkeypatch)
    real_set = fw.set_position
    sent = []

    async def set_position(slot):
        sent.append(slot)
        if len(sent) == 1:
            return await real_set(slot)             # out to luminance: lands
        raise RuntimeError("the wheel did not take the move back")

    monkeypatch.setattr(fw, "set_position", set_position)
    back = await e._sweep_through_luminance("test")
    assert await fw.get_position() == L_SLOT, "premise: the wheel is stranded"
    assert back == "R", f"the wheel is on slot {L_SLOT} and nothing is owed: {back!r}"
    assert e._luminance_offset_landed is False, (
        "the offset never landed and the engine does not know it")
    monkeypatch.delattr(foc, "get_position")       # the wheel and the focuser
    monkeypatch.delattr(fw, "set_position")        # answer again
    await e._restore_filter_after_sweep(back, "test")
    assert await fw.get_position() == R_SLOT, "the wheel was not put back"
    moved = await foc.get_position() - before
    assert moved == 0, (
        f"the restore moved the focuser by {moved} steps for an offset it "
        f"never took")
    assert e._luminance_offset_landed is True, "the flag is spent by the restore"


async def test_a_move_that_lands_then_raises_is_owed_its_way_back(
        sim_hub, monkeypatch):
    """The Alpaca shape (the hub's borrow had it too): the wheel's move lands
    and the call raises (a poll that times out while it is in transit, a
    lost reply). ``_apply_filter`` never reaches the focuser; the wheel is on
    luminance; the sweep must see that and owe the way back, with no offset.

    RED under mutant "a wheel off its slot reads as home" (``_wheel_is_home``
    made to answer True), observed:

        AssertionError: the wheel is on slot 0 and nothing is owed: None
    """
    e, fw, foc = _engine(sim_hub)
    await _on_r(fw)
    before = await foc.get_position()
    real_set = fw.set_position
    sent = []

    async def lands_then_raises(slot):
        sent.append(slot)
        await real_set(slot)
        raise RuntimeError("lost the reply to a move that landed")

    monkeypatch.setattr(fw, "set_position", lands_then_raises)
    back = await e._sweep_through_luminance("test")
    assert await fw.get_position() == L_SLOT, "premise: the move landed"
    assert back == "R", f"the wheel is on slot {L_SLOT} and nothing is owed: {back!r}"
    assert e._luminance_offset_landed is False
    monkeypatch.delattr(fw, "set_position")        # the wheel answers again
    await e._restore_filter_after_sweep(back, "test")
    assert await fw.get_position() == R_SLOT, "the wheel was not put back"
    assert await foc.get_position() == before, (
        "the restore moved the focuser for an offset it never took")


async def test_control_a_wheel_that_refuses_the_move_owes_nothing_and_is_asked_once(
        sim_hub, monkeypatch):
    """CONTROL: the move raises and the wheel never left R. Nothing is owed,
    the sweep goes on, and no second command goes to a wheel that has just
    refused one."""
    e, fw, foc = _engine(sim_hub)
    await _on_r(fw)
    sent = []

    async def refuses(slot):
        sent.append(slot)
        raise RuntimeError("the wheel refused")

    monkeypatch.setattr(fw, "set_position", refuses)
    back = await e._sweep_through_luminance("test")
    assert back is None, f"a refused move owes nothing: {back!r}"
    assert sent == [L_SLOT], f"commands sent to a wheel that refused: {sent}"
    monkeypatch.delattr(fw, "set_position")
    assert await fw.get_position() == R_SLOT


async def test_control_a_clean_move_is_undone_with_its_offset(sim_hub):
    """CONTROL: nothing fails. The move out applies the offset, the restore
    applies the inverse, and the focuser is where it started: the behaviour
    this file must not change."""
    e, fw, foc = _engine(sim_hub)
    await _on_r(fw)
    before = await foc.get_position()
    back = await e._sweep_through_luminance("test")
    assert back == "R" and await fw.get_position() == L_SLOT
    assert e._luminance_offset_landed is True
    assert await foc.get_position() == before - 12, "the offset was not applied"
    await e._restore_filter_after_sweep(back, "test")
    assert await fw.get_position() == R_SLOT
    assert await foc.get_position() == before, "the offset was not undone"
