# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A stalled filter wheel costs a plate solve ONE wait, not two (WP-196, #963).

Every centring solve, rotate, rotator sync, guide offset and sky precheck reads
the filter wheel twice before it opens the shutter: ``Hub._borrow_wheel_for_
solve`` reads the position to choose a slot a solve can see through, and
``Hub._narrowband_filter_loaded`` reads it again to name the filter the frame
goes through. Each is bounded by ``SOLVE_WHEEL_MOVE_TIMEOUT_S`` (90 s, #815 and
#935). A native wheel whose SDK read stalls in USB never trips a transport
timeout, so a solve waited the 90 s on the borrow's read, found nothing, and
waited another 90 s for the label read of the same dead wheel before it
exposed: three minutes, for a read that only feeds a diagnostic name.

THE FIX. The borrow records that the wheel did not answer a position read, and
the label read that follows it for the same wheel is not made: the frame goes
ahead unnamed, as it does when a label read times out. The borrow says the wheel
is stalled, once. The record belongs to the latest borrow, is consumed by the
label read, and is never kept for another wheel or another solve.

The cases run on the simulator's hub with its wheel on a narrowband slot
(OIII), scripted by ``_Wheel``, through real callers. The bound is shortened so a stall
does not wait out the real one; it is read at call time. They grade the reads
of the wheel made before the shutter opens, which is the wait, and the count
of full bounds that ran out before it as a second view of the same (never the
seconds: #1047).

THE HUB'S OWN POLLER IS STOPPED (#1011). ``connect_sim`` starts the two-second
status poller, which reads this same wheel through the same ``get_position``.
A case that counts reads counted its reads, and every poll that landed inside
it: a case whose solves together outlast the poll period (a loaded machine)
read one more than the solves made, and failed on the clock. ``quiet_status_poller`` stops it for every case, and ``_Wheel``
refuses to be built beside a live one. Named mutant: the stop's ``if task is
not None:`` made ``if False:`` -> all ten cases, AssertionError "the hub's
status poller is running, and its reads of the wheel would be counted as the
solve's". With no stop and no guard at all (the file as it was), the poll
landing is forced by starting each solve 1.2 s late, so the two outlast the
real period: ``test_a_stall_is_not_carried_to_the_next_solve`` fails with "5
reads before the second solve's shutter" for the 3 it graded.

Named mutants, each run from a byte backup of ``hub.py`` and restored with a
byte copy (md5 compared). The first failing assertion is quoted.

* "label always reads" (the ``if stalled is fw:`` early return in
  ``_narrowband_filter_loaded`` made ``if False:``, which is the unfixed
  read) -> ``test_a_stalled_wheel_costs_a_solve_one_wait_before_the_exposure``:
  AssertionError, "the wheel was read 2 times before the shutter opened, and
  every read of a stalled wheel is a full bound" (and the rotate case, "the
  first frame was exposed after 2 reads of a stalled wheel", the failed-move
  case, and the two cases of the record being spent).
* "first stall not recorded" (the borrow's ``self._wheel_read_stalled = fw``
  after its timed-out first read removed) -> the same solve and rotate cases,
  the same way.
* "second stall not recorded" (the same assignment after the position read
  that follows a failed move made ``pass``) -> ``test_a_wheel_that_stalls_
  after_a_failed_move_is_not_read_again_for_its_label``: AssertionError, "the
  wheel was read 3 times before the shutter opened; it had already not
  answered once".
* "any failed read counts as a stall" (the ``where is None and`` guard in front
  of the second record, in the failed-move branch, removed) -> ``test_a_wheel_
  that_only_loses_its_moving_read_is_still_read_for_its_label``:
  AssertionError, "the wheel was read 2 times before the shutter opened; its
  position read had answered" (and the log says the position read did not
  answer).
* "no reset at the top of the borrow" (``self._wheel_read_stalled = None``
  removed from the top of ``_borrow_wheel_for_solve``) -> ``test_a_solve_that_
  never_reached_its_label_leaves_no_record_behind``: AssertionError, "the
  second solve's label read was skipped for the first solve's stall: [1, 2]".
* "record not spent" (``self._wheel_read_stalled = None`` removed from
  ``_narrowband_filter_loaded``) -> ``test_the_record_is_spent_by_the_label_
  read_it_excused``: AssertionError, "the record outlived the read it
  excused".

The controls pass on the unfixed tree as well, which is what makes them
controls: a healthy wheel is still read twice, and the label read alone, with
no borrow before it, still reads.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401 (fixture import)

NB_SLOT = 5
NARROWBAND = [False, False, False, False, True, True, True, False]
BOUND_S = 0.5


@pytest.fixture(autouse=True)
async def quiet_status_poller(sim_hub):
    """Stop the hub's own two-second status poller. It reads the wheel through
    the same ``get_position`` the cases count, so a poll that lands inside a
    case adds a read the solve did not make. Cancelled and awaited before the
    case begins, so none is in flight when ``_Wheel`` installs its script."""
    task = sim_hub._status_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    yield


@pytest.fixture
def short_bound(monkeypatch):
    """The wheel bound shortened. Read at call time. ``raising=False``: on
    code without the constant a case fails on what it grades, not on a
    missing attribute."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", BOUND_S,
                        raising=False)


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    real = events_mod.bus.log

    def record(level, message, source="hub", **kw):
        lines.append((level, message, source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


class _Wheel:
    """The sim wheel on a narrowband slot (OIII) with its narrowband slots
    marked, and its ``get_position`` scripted: the Nth call (1-based) never returns while
    ``stalled(N)`` is true, until ``release`` is set. With ``jam_move`` the
    first move never finishes either. Moves are otherwise instant. Every
    ``get_position`` call is recorded in ``reads``, and the first exposure
    notes how many reads had been made and when it began."""

    def __init__(self, hub, monkeypatch, *, stalled, jam_move: bool = False):
        task = hub._status_task
        assert task is None or task.done(), (
            "the hub's status poller is running, and its reads of the "
            "wheel would be counted as the solve's")
        self.fw = hub.devices["filterwheel"]
        self.fw.filter_narrowband = list(NARROWBAND)
        self.rig = hub.sim_rig
        self.rig.filter_slot = NB_SLOT
        self.stalled = stalled
        self.release = asyncio.Event()
        self.reads: list[int] = []
        self.moves: list[int] = []
        self.shutter: dict = {}
        self.waits_spent = 0
        real_get = self.fw.get_position
        jammed = {"left": 1 if jam_move else 0}

        async def get_position():
            self.reads.append(len(self.reads) + 1)
            if self.stalled(len(self.reads)):
                await self.release.wait()          # a USB stall inside the SDK
            return await real_get()

        async def set_position(slot):
            self.moves.append(int(slot))
            if jammed["left"]:
                jammed["left"] -= 1
                await self.release.wait()
            self.rig.filter_slot = int(slot)

        async def wheel_slot():
            # The preview's read of the slot AFTER the exposure
            # (``Hub._wheel_slot``) is bounded since #999, by the status
            # bound, and a wheel that never answers would cost every case
            # that bound again once the shutter had closed. It is answered
            # from the rig here so these cases grade the wait before the
            # exposure and nothing after it.
            return self.rig.filter_slot

        real_wait_for = asyncio.wait_for

        async def wait_for(aw, timeout=None, **kw):
            # A full bound spent: a wait of the (shortened) wheel bound that
            # ran out. Counted, not timed, so a loaded machine cannot move it.
            try:
                return await real_wait_for(aw, timeout, **kw)
            except asyncio.TimeoutError:
                if timeout == BOUND_S:
                    self.waits_spent += 1
                raise

        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        monkeypatch.setattr(self.fw, "get_position", get_position)
        monkeypatch.setattr(self.fw, "set_position", set_position)
        monkeypatch.setattr(hub, "_wheel_slot", wheel_slot)

        cam = hub.devices["camera"]
        real_expose = cam.expose

        async def expose(*args, **kwargs):
            self.shutter.setdefault("reads", len(self.reads))
            self.shutter.setdefault("waits", self.waits_spent)
            return await real_expose(*args, **kwargs)

        monkeypatch.setattr(cam, "expose", expose)


async def _solve(hub) -> None:
    """One real ``solve_and_sync`` on the simulator."""
    await hub.solve_and_sync(exposure_s=0.05)


# ------------------------------------------------------------- the stall


async def test_a_stalled_wheel_costs_a_solve_one_wait_before_the_exposure(
        sim_hub, monkeypatch, short_bound):
    """The wheel's position read never returns. The solve waits the bound
    once, says the wheel is stalled, and exposes through whatever is loaded.
    Unfixed it waited the bound a second time on the label read of the same
    dead wheel."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: True)
    try:
        try:
            await asyncio.wait_for(_solve(sim_hub), 10.0)
        except asyncio.TimeoutError:
            pytest.fail("the solve waited on a wheel whose position never "
                        "returned")
        assert wheel.shutter, "premise: the solve reached its exposure"
        assert wheel.shutter["reads"] == 1, (
            f"the wheel was read {wheel.shutter['reads']} times before the "
            f"shutter opened, and every read of a stalled wheel is a full "
            f"bound")
        # The second view of the same wait is a COUNT of the bounds that ran
        # out, not the seconds they took: a wall-clock window around 0.5 s
        # failed at 1.72 s on a loaded box (#1047).
        assert wheel.shutter["waits"] == 1, (
            f"{wheel.shutter['waits']} full bounds of {BOUND_S} s ran out "
            f"before the shutter opened; a stalled wheel costs one")
        said = [ln for ln in logs if "stalled" in ln[1]]
        assert len(said) == 1 and said[0][0] == "warning", logs
        assert "position read" in said[0][1], said[0][1]
        assert f"no answer within {BOUND_S:g} s" in said[0][1], said[0][1]
        assert "()" not in said[0][1], said[0][1]
    finally:
        wheel.release.set()


async def test_a_stalled_wheel_costs_a_rotate_one_wait_per_frame(
        sim_hub, monkeypatch, short_bound):
    """Through the rotate, which borrows for each of its solve frames. The
    wheel is dead throughout: each frame costs one bound, not two, and the
    rotate goes on."""
    sim_hub.sim_rig.rotator_pa_offset_deg = 20.0
    sim_hub.sim_rig.rotator_mech_deg = 10.0
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: True)
    frames: list[int] = []
    cam = sim_hub.devices["camera"]
    real_expose = cam.expose

    async def expose(*args, **kwargs):
        frames.append(len(wheel.reads))
        return await real_expose(*args, **kwargs)

    monkeypatch.setattr(cam, "expose", expose)
    try:
        try:
            await asyncio.wait_for(sim_hub.rotate_to_pa(40.0), 30.0)
        except asyncio.TimeoutError:
            pytest.fail("the rotate waited on a wheel whose position never "
                        "returned")
        assert frames, "premise: the rotate exposed"
        assert frames[0] == 1, (
            f"the first frame was exposed after {frames[0]} reads of a "
            f"stalled wheel")
        per_frame = [b - a for a, b in zip([0] + frames, frames)]
        assert all(n == 1 for n in per_frame), (
            f"reads of the stalled wheel before each frame: {per_frame}")
    finally:
        wheel.release.set()


async def test_a_wheel_that_stalls_after_a_failed_move_is_not_read_again_for_its_label(
        sim_hub, monkeypatch, short_bound):
    """The borrow reads the wheel (it answers), sends the move, and the move
    jams; the borrow then asks where the wheel is, and that read stalls too.
    The wheel has now been silent twice and the label read would wait a third
    bound for a diagnostic name: it is not made."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: n >= 2,
                   jam_move=True)
    try:
        try:
            await asyncio.wait_for(_solve(sim_hub), 10.0)
        except asyncio.TimeoutError:
            pytest.fail("the solve waited on a wheel that stopped answering")
        assert wheel.shutter, "premise: the solve reached its exposure"
        assert wheel.moves[:1] == [0], "premise: the borrow sent its move"
        assert wheel.shutter["reads"] == 2, (
            f"the wheel was read {wheel.shutter['reads']} times before the "
            f"shutter opened; it had already not answered once")
        said = [ln for ln in logs if "stalled" in ln[1]]
        assert said and said[0][0] == "warning", logs
    finally:
        wheel.release.set()


async def test_a_wheel_that_only_loses_its_moving_read_is_still_read_for_its_label(
        sim_hub, monkeypatch, short_bound):
    """The move jams, the borrow asks where the wheel is and the wheel answers,
    but its is-moving read never returns. The position read works, so it is not
    a stalled wheel for the label read, which is made; and the borrow does not
    say that the position read failed, because it did not."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: False,
                   jam_move=True)

    async def is_moving():
        await wheel.release.wait()
        return False

    monkeypatch.setattr(wheel.fw, "is_moving", is_moving, raising=False)
    try:
        try:
            await asyncio.wait_for(_solve(sim_hub), 10.0)
        except asyncio.TimeoutError:
            pytest.fail("the solve waited on a wheel that answered its "
                        "position")
        assert wheel.shutter, "premise: the solve reached its exposure"
        assert wheel.moves[:1] == [0], "premise: the borrow sent its move"
        assert wheel.shutter["reads"] == 3, (
            f"the wheel was read {wheel.shutter['reads']} times before the "
            f"shutter opened; its position read had answered, so the borrow's "
            f"read, the where-is-it read and the label read make three")
        said = [ln for ln in logs if "did not answer" in ln[1]
                or "is stalled" in ln[1]]
        assert not said, said
    finally:
        wheel.release.set()


# ------------------------------------------------------------- controls


async def test_a_healthy_wheel_is_still_read_for_its_label(
        sim_hub, monkeypatch, short_bound):
    """CONTROL. The premise the cases above depend on: a wheel that answers
    is read twice before the shutter (the borrow's choice, the label of what
    the frame goes through), so the stall record changed nothing for it and
    the label read is not simply gone."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: False)

    await asyncio.wait_for(_solve(sim_hub), 10.0)

    assert wheel.shutter["reads"] == 2, wheel.reads
    assert wheel.moves[:1] == [0], "premise: the borrow moved off OIII"
    assert not [ln for ln in logs if "stalled" in ln[1]], logs


async def test_a_stall_is_not_carried_to_the_next_solve(
        sim_hub, monkeypatch, short_bound):
    """The wheel stalls for one solve and answers for the next. The second
    solve reads it twice and names what it goes through: the first solve's
    stall was that solve's, not the wheel's for ever."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: n == 1)
    try:
        await asyncio.wait_for(_solve(sim_hub), 10.0)
        assert wheel.shutter["reads"] == 1, "premise: the first solve stalled"
        wheel.shutter.clear()
        wheel.rig.filter_slot = NB_SLOT

        await asyncio.wait_for(_solve(sim_hub), 10.0)

        assert wheel.shutter["reads"] == 2 + 1, (
            f"reads before the second solve's shutter, counting the first "
            f"solve's one: {wheel.shutter['reads']}; the recovered wheel's "
            f"label read was skipped")
    finally:
        wheel.release.set()


async def test_the_record_is_spent_by_the_label_read_it_excused(
        sim_hub, monkeypatch, short_bound):
    """The label read that follows a stalled borrow is skipped, once. A label
    read after that one (no borrow between) reads the wheel: the record
    excused the one read and is not a standing order to leave the wheel
    alone."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: n == 1)
    try:
        assert await sim_hub._borrow_wheel_for_solve() is None
        assert wheel.reads == [1], "premise: the borrow's read stalled"

        assert await sim_hub._narrowband_filter_loaded() is None
        assert wheel.reads == [1], "the excused label read read the wheel"

        got = await sim_hub._narrowband_filter_loaded()
        assert wheel.reads == [1, 2], "the record outlived the read it excused"
        assert got == sim_hub.devices["filterwheel"].filter_names[NB_SLOT]
    finally:
        wheel.release.set()


async def test_a_solve_that_never_reached_its_label_leaves_no_record_behind(
        sim_hub, monkeypatch, short_bound):
    """A stalled borrow whose solve was stopped before the label read (a Stop
    lands between the two) leaves its record unspent. The next solve's borrow
    starts clean: the wheel answers it, and its label read is made."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: n == 1)
    try:
        assert await sim_hub._borrow_wheel_for_solve() is None
        assert wheel.reads == [1], "premise: the borrow's read stalled"

        back = await sim_hub._borrow_wheel_for_solve()
        await sim_hub._narrowband_filter_loaded()

        assert back == NB_SLOT, "premise: the second borrow read the wheel"
        assert wheel.reads == [1, 2, 3], (
            f"the second solve's label read was skipped for the first "
            f"solve's stall: {wheel.reads}")
    finally:
        wheel.release.set()


async def test_a_label_read_with_no_borrow_before_it_still_reads(
        sim_hub, monkeypatch, short_bound):
    """CONTROL. The stall record is the latest borrow's and nothing else's: a
    label read that no borrow preceded reads the wheel, as it always did."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: False)

    got = await sim_hub._narrowband_filter_loaded()

    assert wheel.reads == [1]
    assert got == sim_hub.devices["filterwheel"].filter_names[NB_SLOT]


async def test_a_stalled_borrow_does_not_silence_another_wheel(
        sim_hub, monkeypatch, short_bound):
    """The record names the wheel that did not answer. A label read of a
    different wheel (a profile activated between the two) is made."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n: n == 1)
    try:
        assert await sim_hub._borrow_wheel_for_solve() is None
        old = sim_hub.devices["filterwheel"]
        assert wheel.reads == [1], "premise: the borrow's read stalled"

        class _Replacement:
            connected = True
            filter_names = list(old.filter_names)
            reads = 0

            def is_narrowband(self, slot):
                return slot == NB_SLOT

            async def get_position(self):
                type(self).reads += 1
                return NB_SLOT

        sim_hub.devices["filterwheel"] = _Replacement()
        try:
            got = await sim_hub._narrowband_filter_loaded()
        finally:
            sim_hub.devices["filterwheel"] = old

        assert _Replacement.reads == 1, "the other wheel's read was skipped"
        assert got == old.filter_names[NB_SLOT]
    finally:
        wheel.release.set()
