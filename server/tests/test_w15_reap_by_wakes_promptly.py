# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A wind-down's reap looks every ``REAP_LOOK_S``, not every
``IDLE_STOP_FINISH_POLL_S`` (#289, job 2).

S2 (#270) moved the wind-down's guider stop onto a task of its own and reaped
it by polling (`SequenceEngine._reap_by`). The look was the whole poll: one
quarter second, the cadence the idle-stop wait uses against a 180 s bound. A
stop that finished a moment after the reap began therefore held the run's
end, and everything behind it (the flush, "running" going False), for up to a
quarter second after the guider had already stopped. #289 showed it as a
flaky abort count; the test was fixed, the latency was left. The look is now
``REAP_LOOK_S`` (20 ms).

NOT AN EVENT WAKE. The reap still sleeps, it does not await the task: the
clocked night (`_group_harness._Asyncio.sleep` -> `Night.sleep`) parks only
engine tasks and advances the fake clock only when every one is parked, so a
wake-on-done wait would be an un-parked engine task and a hung stop would
never reach its deadline in a harness night. The cases here therefore grade
the cadence of the looks, which is the contract the harness relies on.

DETERMINISTIC, NOT WALL-CLOCK. `engine_mod.asyncio` is replaced by a wrapper
of the harness's shape that records each sleep's delay and runs a hook on it;
`engine_mod.time` is a fake clock the same sleeps advance. Nothing here
waits on the real clock, so a loaded machine cannot move a verdict.

RED under mutant "the look is the whole poll again" (``REAP_LOOK_S`` replaced
by ``IDLE_STOP_FINISH_POLL_S`` in `_reap_by`'s sleep), observed, the first
case's and the hung stop's:

    E       assert 0.25 <= 0.05
    E       assert ([0.25, 0.25, 0.25, 0.25] and 0.25 <= 0.05)

RED under mutant "the reap does not cancel" (the ``finally`` that cancels a
task still pending removed from `_reap_by`), observed, the hung stop's and the
caller's cancel:

    E       AssertionError: a stop past its deadline was left running
    E       assert False
    E        +  where False = <built-in method cancelled of _asyncio.Future
                object at 0x...>()
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence.engine import SequenceEngine

#: The longest look the reap may take. Generous against REAP_LOOK_S (20 ms)
#: and an order of magnitude under the poll it replaced (250 ms).
LOOK_CEILING_S = 0.05


class _FakeClock:
    """engine.py's ``time``: ``time()`` is a number the sleeps advance."""

    def __init__(self, real, t0: float = 1_000_000.0):
        self._real = real
        self.t = t0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


class _RecordingAsyncio:
    """engine.py's ``asyncio``: ``sleep`` records its delay, runs ``hook``
    with the 1-based count of sleeps so far, advances the fake clock by the
    delay and yields one loop turn; everything else is the real module (the
    shape of `_group_harness._Asyncio`)."""

    def __init__(self, real, clock: _FakeClock, hook=None):
        self._real = real
        self._clock = clock
        self._hook = hook
        self.delays: list[float] = []

    async def sleep(self, delay, result=None):
        self.delays.append(delay)
        if self._hook is not None:
            self._hook(len(self.delays))
        self._clock.t += delay
        await self._real.sleep(0)
        return result

    def __getattr__(self, name):
        return getattr(self._real, name)


def _engine() -> SequenceEngine:
    """`_reap_by` reads nothing off the engine, so none of a hub is built."""
    return SequenceEngine.__new__(SequenceEngine)


def _install(monkeypatch, hook=None) -> tuple[_RecordingAsyncio, _FakeClock]:
    clock = _FakeClock(engine_mod.time)
    wrapper = _RecordingAsyncio(engine_mod.asyncio, clock, hook)
    monkeypatch.setattr(engine_mod, "time", clock)
    monkeypatch.setattr(engine_mod, "asyncio", wrapper)
    return wrapper, clock


async def test_a_stop_that_finishes_during_the_park_ends_the_reap_in_one_look(
        monkeypatch):
    """The stop completes while the reap is asleep on its first look: the
    reap returns after exactly that one look, and the look is short. The
    look's length IS the latency the wind-down adds, so it is what is
    bounded (the mutant leaves it at 0.25 s)."""
    stop = asyncio.get_running_loop().create_future()

    def finish_on_first_look(n: int) -> None:
        if n == 1:
            stop.set_result(None)

    wrapper, clock = _install(monkeypatch, finish_on_first_look)
    t0 = clock.t
    await _engine()._reap_by(stop, t0 + engine_mod.GUIDE_OP_TIMEOUT_S)

    assert len(wrapper.delays) == 1, "one look, then the stop was seen done"
    assert wrapper.delays[0] <= LOOK_CEILING_S
    assert wrapper.delays[0] > 0, "a look that does not sleep spins the loop"
    assert clock.t - t0 <= LOOK_CEILING_S
    assert stop.done() and not stop.cancelled()


async def test_a_stop_already_done_is_never_looked_at(monkeypatch):
    """The normal case: the stop finished during the park, so the reap has
    nothing to wait for and takes no sleep at all. The group harness's golden
    traces depend on it: only a stop that outlives the park can move one."""
    stop = asyncio.get_running_loop().create_future()
    stop.set_result(None)
    wrapper, clock = _install(monkeypatch)
    await _engine()._reap_by(stop, clock.t + engine_mod.GUIDE_OP_TIMEOUT_S)
    assert wrapper.delays == []


async def test_a_hung_stop_is_cancelled_at_the_deadline(monkeypatch):
    """A stop that never finishes is left cancelled once the wall time passes
    its deadline, and the reap ends within one look of it. The cadence is
    every sleep, not the first: the deadline is the bound, so the overshoot is
    at most one look."""
    stop = asyncio.get_running_loop().create_future()
    wrapper, clock = _install(monkeypatch)
    t0 = clock.t
    deadline = t0 + 1.0
    await _engine()._reap_by(stop, deadline)

    assert stop.cancelled(), "a stop past its deadline was left running"
    assert clock.t >= deadline
    assert clock.t - deadline <= LOOK_CEILING_S, "the reap overshot its deadline"
    assert wrapper.delays and max(wrapper.delays) <= LOOK_CEILING_S


async def test_a_cancel_of_the_caller_cancels_the_stop_too(monkeypatch):
    """`_reap_by` never raises but for a cancel of its caller, and that
    cancel takes the stop with it (nothing may outlive the run's teardown
    asking the guider)."""
    stop = asyncio.get_running_loop().create_future()
    reaping = None

    def cancel_the_reap(n: int) -> None:
        if n == 2:
            reaping.cancel()

    _install(monkeypatch, cancel_the_reap)
    reaping = asyncio.ensure_future(
        _engine()._reap_by(stop, engine_mod.time.time() + 60.0))
    with pytest.raises(asyncio.CancelledError):
        await reaping
    assert stop.cancelled()


def test_the_look_is_a_named_constant_shorter_than_the_idle_stop_poll():
    """The look is `REAP_LOOK_S`, and it is the shorter of the two: the cooling
    waits' own polls (`IDLE_STOP_FINISH_POLL_S`) are different waits and stay
    where they were."""
    assert engine_mod.REAP_LOOK_S == pytest.approx(0.02)
    assert engine_mod.REAP_LOOK_S < engine_mod.IDLE_STOP_FINISH_POLL_S
    assert engine_mod.IDLE_STOP_FINISH_POLL_S == pytest.approx(0.25)
