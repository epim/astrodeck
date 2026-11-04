# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The group harness widens its real-time budgets by a MEASURED contention
factor (#1119; #755, #756, #1099).

About twenty tests failed only when the suite ran beside other work on the
same box. Each of them bounded something in REAL seconds, and the bound was
sized on an idle machine: the spin case's timeline slack (#755), the default
spin bound a cold first night can pass (#756), the 60 s a night is given
(#1099's test_group_meridian), the half second the hub's meridian block is
waited for. A starved thread is not a spin and a slow night is not a hang, so
the cure is the one #620 and #683 named: the bound moves with the load, and
what it bounds does not.

`_group_harness.contention_factor` is that one number. It is measured, on the
thread that will use it, by spinning and counting the stretches the thread was
not scheduled, and it is 1.0 on an idle box. The cases here pin the pieces:
how the number is read off a clock (against a clock the test controls, so the
answer does not depend on this box's load), how long it is trusted, and that
each budget the harness hands out is multiplied by it.

MUTANT "the bound is not scaled" (``contention_factor`` returns 1.0): every
case of the second half is RED, and the spin case in
test_group_harness_watchdog.py fails under 40 CPU burners (see that file).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import _group_harness as gh
from _group_harness import (SPIN_BOUND_S, Night, _SpinWatchdog, contention_factor,
                            default_spin_bound_s, group_hub, group_store,
                            measure_contention, wall_budget_s, watchdog_slack_s)
from astrodeck.sequence import SequenceEngine

from test_group_harness_watchdog import _plan, run_the_spin_case, spin_bound_s


class _Clock:
    """A clock the test drives: each read advances it by ``step_s``, and every
    ``every``-th read first jumps ``gap_s`` ahead, which is what a thread that
    was not scheduled for ``gap_s`` sees on its next read."""

    def __init__(self, step_s: float = 1e-6, every: int = 0, gap_s: float = 0.0):
        self.now = 0.0
        self.reads = 0
        self.step_s, self.every, self.gap_s = step_s, every, gap_s

    def __call__(self) -> float:
        self.reads += 1
        self.now += self.step_s
        if self.every and self.reads % self.every == 0:
            self.now += self.gap_s
        return self.now


def test_the_session_has_taken_its_first_reading_before_any_test(request):
    """conftest.py's ``contention`` fixture reads the factor once at session
    start, so the 0.3 s a reading blocks is not spent inside a test that times
    something. This module imports the harness, so the fixture reads it here.
    (Alone-run proof: with the fixture gone nothing has read it yet.)"""
    assert "contention" in request.fixturenames
    assert gh._contention_cache is not None, "no reading was taken up front"


def test_an_unloaded_thread_reads_as_1():
    assert measure_contention(_Clock(), window_s=0.05) == 1.0


def test_a_thread_that_runs_a_fraction_of_the_time_reads_as_the_inverse():
    """Every 1000th read is 10 ms late (under an idle tick, so the longest
    stretch says nothing): 1 ms of running, 10 ms away, so the thread ran a
    eleventh of the time and a CPU-bound step costs eleven times as long."""
    clock = _Clock(every=1000, gap_s=0.010)
    factor = measure_contention(clock, window_s=0.3)
    assert gh.IDLE_GAP_S > 0.010
    assert factor == pytest.approx(11.0, rel=0.1), factor


def test_one_long_stretch_away_reads_by_its_length():
    """One 100 ms stretch in a mostly running thread (a quarter second of
    running first) costs the share little, 1.4 times, and costs a thread that
    wakes to a loop or a lock a hundred milliseconds: the factor is the
    stretch over an idle tick, 6.25."""
    clock = _Clock(every=250_000, gap_s=0.1)
    factor = measure_contention(clock, window_s=0.3)
    assert factor == pytest.approx(0.1 / gh.IDLE_GAP_S, rel=0.05), factor


def test_the_factor_is_capped():
    """A box so loaded that a thread is away a second at a time still gets a
    finite budget: the cap, not a bound a hung test would sit out."""
    clock = _Clock(every=100, gap_s=1.0)
    assert measure_contention(clock, window_s=0.3) == gh.CONTENTION_CAP


def test_the_factor_is_trusted_for_a_while_and_then_measured_again(monkeypatch):
    answers = iter([2.0, 5.0, 7.0])
    monkeypatch.setattr(gh, "measure_contention", lambda: next(answers))
    monkeypatch.setattr(gh, "_contention_cache", None)
    assert contention_factor() == 2.0
    assert contention_factor() == 2.0, "measured again inside its own lifetime"
    taken_at = gh._contention_cache[1]
    monkeypatch.setattr(gh, "_contention_cache",
                        (2.0, taken_at - gh.CONTENTION_MAX_AGE_S - 1.0))
    assert contention_factor() == 5.0, "an old reading was trusted"


def test_a_measured_factor_below_1_is_not_used(monkeypatch):
    monkeypatch.setattr(gh, "measure_contention", lambda: 0.2)
    monkeypatch.setattr(gh, "_contention_cache", None)
    assert contention_factor() == 1.0


@pytest.fixture
def loaded(monkeypatch):
    """The harness believes the box is three times slower than idle."""
    monkeypatch.setattr(gh, "_contention_cache", (3.0, time.perf_counter()))
    return 3.0


def test_the_default_spin_bound_scales(loaded):
    assert default_spin_bound_s() == SPIN_BOUND_S * loaded


def test_a_wall_budget_scales(loaded):
    assert wall_budget_s(60.0) == 60.0 * loaded


def test_the_watchdog_slack_scales(loaded):
    """The slack is the watcher's own tick (``min(0.25, bound / 8)``) plus a
    second, and the whole of it moves with the load (#755: 2.10 s measured
    for a 1.0 s bound, 40 burners)."""
    assert watchdog_slack_s(1.0) == (0.125 + 1.0) * loaded
    assert watchdog_slack_s(8.0) == (0.25 + 1.0) * loaded


async def test_a_night_built_without_a_bound_gets_the_measured_one(
        loaded, group_hub, group_store, monkeypatch):
    night = Night(group_hub, monkeypatch)
    assert night.spin_bound_s == SPIN_BOUND_S * loaded
    await night.close()
    own = Night(group_hub, monkeypatch, spin_bound_s=1.0)
    assert own.spin_bound_s == 1.0, "a test's own bound must stand"
    await own.close()


async def test_a_night_is_given_the_scaled_wall_budget(loaded, group_hub,
                                                       group_store, monkeypatch):
    """``run(wall_s=0.3)`` on a night that never ends: with the box read as
    three times slower, the run is given 0.9 s before it returns False. A
    budget that was not scaled ends at 0.3 s."""
    async def never_ends(self, plan):
        while True:
            await asyncio.sleep(0.005)

    monkeypatch.setattr(SequenceEngine, "_run_scheduled", never_ends)
    night = Night(group_hub, monkeypatch, spin_bound_s=60.0)
    began = time.monotonic()
    try:
        done = await night.run(_plan(), wall_s=0.3)
    finally:
        await night.close()
    took = time.monotonic() - began
    assert done is False
    assert took >= 0.3 * loaded - 0.1, (
        f"the run was given {took:.2f} s, not the 0.3 s budget times the "
        f"contention factor ({loaded:g})")


async def test_settle_is_given_the_scaled_wall_budget(loaded, group_hub,
                                                      group_store, monkeypatch):
    """Same for `settle`, which `until` and `finish` call: it fails the night
    after the scaled budget, and its message names the budget the caller
    asked for, so the failure still reads against the call site."""
    monkeypatch.setattr(SequenceEngine, "running", property(lambda self: True))
    night = Night(group_hub, monkeypatch, spin_bound_s=60.0)
    began = time.monotonic()
    with pytest.raises(pytest.fail.Exception) as raised:
        try:
            await night.settle(wall_s=0.3)
        finally:
            await night.close()
    took = time.monotonic() - began
    assert took >= 0.3 * loaded - 0.1, took
    assert "in 0.3 s of real time" in str(raised.value), str(raised.value)


async def test_the_meridian_block_is_waited_for_by_the_scaled_bound(
        loaded, group_hub, group_store, monkeypatch):
    """`_hold_the_meridian` drops the hub's meridian block when it does not
    come in ``MERIDIAN_HOLD_BOUND_S``, and a night without the block publishes
    a different trace. A loaded box is slow to compute it, not unable, so the
    wait is scaled: here a block that takes a second to come must be waited
    for under a factor of three (a 0.5 s bound would have dropped it)."""
    async def slow_block(tel, ra, dec):
        await asyncio.sleep(1.0)
        return {"waited": True}

    monkeypatch.setattr(group_hub, "_compute_meridian", slow_block)
    night = Night(group_hub, monkeypatch, spin_bound_s=60.0)
    await night._hold_the_meridian()
    assert group_hub.last_meridian == {"waited": True}, (
        "the meridian block was dropped before the scaled bound ran out")


async def test_the_last_resort_waits_the_scaled_grace(loaded, monkeypatch):
    """The watchdog ends the process only when its raise did not break the
    spin a whole bound later. A loop thread starved by load takes longer than
    a bound to run the raise, so under a factor of three the grace is three
    bounds: the process is not exited for a thread that was only slow to be
    scheduled. Nothing here spins: the raise is a no-op and the last resort a
    recorder."""
    fired: list[float] = []
    monkeypatch.setattr(gh, "_raise_in_thread", lambda thread_id, exc_type: 0)
    monkeypatch.setattr(_SpinWatchdog, "_last_resort",
                        lambda self, away: fired.append(time.monotonic()))
    bound = 0.2
    dog = _SpinWatchdog(bound, "grace case")
    started = time.monotonic()
    dog.start()
    try:
        deadline = started + bound * (1 + loaded) + 1.0
        while not fired and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    finally:
        dog.stop()
    assert fired, "the last resort never ran"
    away = fired[0] - started
    assert away >= bound * (1 + loaded) - 0.1, (
        f"the last resort ran {away:.2f} s in, one bound after the raise and "
        f"not {loaded:g} bounds after it")


async def test_a_watcher_the_load_starved_still_reads_inside_the_slack(
        loaded, group_hub, group_store, monkeypatch):
    """#755 with the load made deterministic. Under 40 burners the spin case
    failed 2 of ~18 runs with "2.10 s away, for a bound of 1.0 s": the watcher
    thread was not scheduled for a second past its bound. Here the watcher is
    held off for two seconds past the bound on purpose, on a box the harness
    reads as three times slower than idle: its own timeline reads two seconds
    over the bound, and the spin case's slack at this contention (3.75 s)
    takes it, where the flat second it had (and ``watchdog_slack_s`` on an
    idle box, 1.125 s) does not.

    MUTANT "the bound is not scaled" (``contention_factor`` returns 1.0):
    RED (observed), ``AssertionError: the watchdog's OWN timeline measured
    3.10 s away, for a bound of 1 s (slack 1.12 s at this box's contention)``,
    on a box with no load at all."""
    await run_the_spin_case(group_hub, monkeypatch,
                            watcher_starved_s=spin_bound_s() + 2.0)
