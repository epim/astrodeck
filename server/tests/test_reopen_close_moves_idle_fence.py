"""The auto-reopen roof close ends the idle stop's task before it parks
(#306, the #270 class).

#270 fenced the idle stop's task against a park: `_hand_idle_stop_to_the_park`
moves ``_idle_stop_epoch`` before the wind-down's park, so the task sends no
``set_tracking(False)`` once that park has begun. The auto-reopen roof close
(`_close_for_reopen`, PRO-4 D3: close the roof over the parked mount during a
safety pause, wait for safe, reopen, resume) took no part in it: it called
`_park_hold` and then `_fenced_park`, and neither moved the fence nor ended the
task. An idle-stop retry alive at that moment (a stop the mount had not
confirmed, asked again every ``IDLE_STOP_RETRY_S``) could send
``set_tracking(False)`` while the park ran: two writers to a mount with one
owner at a time. What an AM5 does with a stop mid-park has not been measured;
at worst a park it abandons, and a roof `close_observatory` then refuses to
close, left open in the weather that closed it.

NOW `_close_for_reopen` ends the task first, cancelled and awaited
(`_cancel_idle_stop_retry`), as every other path that moves the mount does,
so nothing of the task's is in flight when the close talks to the mount.

RE-PINNED FOR #343 AND #345. The close no longer stops through `_park_hold`:
it has the wind-down's shape (the guider stop on a task of its own, waited
for only up to ``WIND_DOWN_GUIDER_STOP_S``, then its own stop of tracking,
the park read back, the roof close, and the guider stop reaped;
test_reopen_close_guider_bound.py), and a close that is refused re-arms the
retries it ended (test_refused_close_keeps_idle_stop.py). Both cases here
still hold as written; their mutants were re-run against the new shape.

THE HARNESS is the simulator on the real clock, the engine with no run: the
idle stop is decided (`_idle_park_hold`) on a mount whose
``set_tracking(False)`` does not take, so its task is retrying, every
``IDLE_STOP_RETRY_S`` shortened to 10 ms. The park then waits until the task
has asked again or is over, bounded, so a live retry is given every chance
to write into it. Every ``set_tracking`` is recorded with the name of the
task that sent it. The site is a fixture, never the real one.
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import DomeShutterState
from astrodeck.sequence.engine import SequenceEngine

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401

RETRY = "idle-stop-retry"


class _Rig:
    """The sim mount and roof with a stop that does not take: every
    ``set_tracking`` is recorded as (task name, on) and ``set_tracking(False)``
    leaves the mount tracking, which reads back as tracking until it is
    parked. The park records the index into ``calls`` when it is asked, then
    waits (bounded, real time) until the idle-stop task has asked again or is
    over, then parks."""

    def __init__(self, hub, monkeypatch):
        self.hub = hub
        self.tel = hub.devices["telescope"]
        self.dome = hub.devices.get("dome")
        assert self.dome is not None and self.dome.connected, (
            "premise: the sim has a roof")
        self.calls: list[tuple[str, bool]] = []
        self.park_at: int | None = None
        self.task: asyncio.Task | None = None
        tel = self.tel
        real_set, real_park = tel.set_tracking, tel.park

        async def set_tracking(on):
            me = asyncio.current_task()
            self.calls.append((me.get_name() if me else "none", bool(on)))
            if on:
                await real_set(on)

        async def get_tracking():
            return not tel.rig.parked

        async def park():
            self.park_at = len(self.calls)
            loop = asyncio.get_running_loop()
            end = loop.time() + 2.0
            while loop.time() < end:
                if self.task is None or self.task.done():
                    break
                if self.retry_offs_from(self.park_at):
                    break
                await asyncio.sleep(0.005)
            await real_park()

        monkeypatch.setattr(tel, "set_tracking", set_tracking)
        monkeypatch.setattr(tel, "get_tracking", get_tracking)
        monkeypatch.setattr(tel, "park", park)

    def retry_offs_from(self, i: int) -> list[int]:
        return [k for k, (who, on) in enumerate(self.calls)
                if k >= i and who == RETRY and not on]


async def _a_retrying_idle_stop(engine: SequenceEngine, rig: _Rig) -> None:
    """Decide the idle stop and wait until its task is retrying: the first
    attempt and at least one retry made, the mount still tracking."""
    await engine._idle_park_hold("test: the idle clock ran out")
    rig.task = engine._idle_stop_task
    assert rig.task is not None, "premise: the idle stop has its task"
    loop = asyncio.get_running_loop()
    end = loop.time() + 5.0
    while len(rig.retry_offs_from(0)) < 2:
        assert loop.time() < end and not rig.task.done(), (
            f"premise: the idle stop is retrying: {rig.calls}")
        await asyncio.sleep(0.005)


async def test_a_retry_alive_at_the_reopen_close_sends_nothing_into_its_park(
        sim_hub, monkeypatch, bus_lines):
    """#306's reproduction. The idle stop's task is retrying a stop the mount
    will not take when a safety pause closes the roof for a later reopen. No
    ``set_tracking(False)`` from that task may reach the mount after the park
    is asked; the task is over and cleared when the close returns; and the
    close parks the mount and closes the roof over it.

    Mutant "no fence move" (the ``await self._cancel_idle_stop_retry()`` that
    ends the task at the top of `_close_for_reopen` deleted: neither the task
    ended nor its fence moved, the code as #306 found it): RED (observed) -
        AssertionError: the idle-stop retry sent set_tracking(False) into the
        reopen close's park (asked at index 3): at [3, 4, 5]
    (how many land depends on the real clock; the park waits for the first,
    bounded, so at least one does whenever the task is alive.) Re-run against
    #343's shape, the call made ``ended = False`` (S4-ENGA): RED, the same
    failure, verbatim.
    """
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    rig = _Rig(sim_hub, monkeypatch)
    try:
        await _a_retrying_idle_stop(engine, rig)
        closed = await engine._close_for_reopen(rig.dome, "rain sensor")
        assert rig.park_at is not None, "premise: the close parked"
        late = rig.retry_offs_from(rig.park_at)
        assert late == [], (
            f"the idle-stop retry sent set_tracking(False) into the reopen "
            f"close's park (asked at index {rig.park_at}): at {late}")
        assert rig.task.done() and engine._idle_stop_task is None, (
            f"the idle-stop task outlived the close: {rig.task}")
        assert closed and rig.tel.rig.parked, (
            f"the close did not park and close: {closed}")
        assert await rig.dome.shutter_state() is DomeShutterState.CLOSED
    finally:
        task = engine._idle_stop_task or rig.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_control_with_no_idle_stop_the_reopen_close_is_unchanged(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. No idle stop was decided, so there is no task to end: the
    close stops tracking itself (`_stop_tracking_quietly`, after its brief
    wait for the guider stop since #343: the one ``set_tracking(False)``,
    sent by the caller), parks, closes the roof over the parked mount and
    says it confirmed the close.

    Mutant "the task's end replaces the close's own stop" (the close's own
    stop of tracking deleted, the ``_cancel_idle_stop_retry()`` kept, as if
    ending the idle stop's task were the stop; until #343 the stop was the
    ``await self._park_hold()``, and since it the
    ``await self._stop_tracking_quietly()`` in `_close_for_reopen`): RED
    (observed, both times) -
        AssertionError: the close's own stop was not the one set_tracking(False)
        before its park: []
    """
    engine = SequenceEngine(sim_hub)
    rig = _Rig(sim_hub, monkeypatch)
    me = asyncio.current_task().get_name()
    closed = await engine._close_for_reopen(rig.dome, "rain sensor")
    offs = [who for who, on in rig.calls[:rig.park_at] if not on]
    assert offs == [me], (
        f"the close's own stop was not the one set_tracking(False) before its "
        f"park: {offs}")
    assert closed and rig.tel.rig.parked
    assert await rig.dome.shutter_state() is DomeShutterState.CLOSED
    assert engine._idle_stop_task is None
