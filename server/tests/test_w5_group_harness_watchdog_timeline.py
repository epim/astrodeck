"""#620's proof: the spin test's bound reads the WATCHDOG's own timeline,
not this test's wall clock, so a slow TEARDOWN (which a loaded box can
cause, for reasons that have nothing to do with the watchdog) does not
flake it.

test_group_harness_watchdog.py::test_a_spin_that_never_yields_fails_the_
night_and_names_the_spin failed twice, seen by the backlog wave 3 verifier
running beside twelve other worktrees' suites (2026-10-01, -n 0, one run
against an unmodified engine.py): "the night failed 4.x s after it
started, for a bound of 1.0 s" -- a red full suite that was not a defect.
#610 and #615 are the same family: a wait bounded by a clock sensitive to
something OTHER than the thing it is meant to measure (there, a round
count instead of a wall-clock deadline; here, this test's own wall clock
instead of the watchdog's).

A genuine CPU-burner reproduction of the exact incident was run by hand
for WP-68 and is recorded in its return (not committed here, since nothing
short of actually saturating every core proves anything, and a suite file
cannot do that to itself on every run without becoming the next #319-shaped
hang). This file instead forces the SAME shape deterministically: it makes
`Night.close`'s teardown take several real seconds -- standing in for
whatever a loaded box adds between the watchdog firing and the test seeing
the result (raise delivery, OS scheduling, the harness's own cleanup) --
while the watchdog itself still fires and writes its report right on its
bound, before `close` ever runs. The watchdog's own timeline stays tight;
this test's wall clock does not: proving the two are genuinely different
numbers, not just two ways of writing the same one."""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from _group_harness import Night
from astrodeck.sequence import SequenceEngine

from test_group_harness_watchdog import BOUND_S, SPIN_LIMIT_S, _plan, _run  # noqa: F401 (shared harness + fixtures)
from test_group_harness_watchdog import group_hub, group_store  # noqa: F401 (fixtures)

#: Added to `Night.close`'s real teardown: enough to push this test's own
#: wall clock (`took`, as the OLD assertion read it) past the OLD bound of
#: BOUND_S + 3.0 = 4.0 s, while the watchdog's own measurement (`away_s`,
#: read from its report) is untouched, since the watchdog has already
#: fired and written that report before `close` is ever called.
SLOW_TEARDOWN_S = 3.5


async def test_a_slow_teardown_does_not_move_the_watchdogs_own_timeline(
        group_hub, group_store, monkeypatch):
    """A spin the watchdog correctly breaks, with its teardown made
    artificially slow (standing in for a loaded box): the watchdog's OWN
    timeline stays near its bound, even though this test's overall wall
    clock does not.

    MUTANT "the bound reads the test's wall clock again" (this test's own
    `away_s` assertion below swapped back for the pre-WP-68 shape, `assert
    took < BOUND_S + 3.0`): RED, observed verbatim:
        AssertionError: the night failed 5.9 s after it started, for a
        bound of 1.0 s
        assert 5.906999999890104 < (1.0 + 3.0)
    -- the exact false failure #620 reported from a genuinely loaded box,
    reproduced here from the slow teardown alone, with no load at all.
    """
    async def nothing() -> None:
        return None

    async def spin_without_yielding(self, plan):
        started = time.monotonic()
        while time.monotonic() - started < SPIN_LIMIT_S:
            await nothing()            # an await that never suspends

    real_close = Night.close

    async def slow_close(self) -> None:
        await asyncio.sleep(SLOW_TEARDOWN_S)
        await real_close(self)

    monkeypatch.setattr(SequenceEngine, "_run_scheduled",
                        spin_without_yielding)
    monkeypatch.setattr(Night, "close", slow_close)

    night = Night(group_hub, monkeypatch, spin_bound_s=BOUND_S)
    done, failed, took = await _run(night)

    assert failed is not None, f"the night did not fail; run returned {done!r}"
    timeline = re.search(
        r"did not come back for ([0-9.]+) s of real time, against a bound "
        r"of ([0-9.]+) s", failed)
    assert timeline, f"the failure text carries no watchdog timeline:\n{failed}"
    away_s = float(timeline.group(1))

    # THE FIX (#620): the watchdog's own measurement must not be moved by
    # a slow teardown, because it fires and writes its report BEFORE
    # `close` (and the slow sleep inside this test's double of it) ever
    # runs.
    assert away_s < BOUND_S + 1.0, (
        f"the watchdog's own timeline measured {away_s:.2f} s away for a "
        f"bound of {BOUND_S} s -- the slow teardown leaked into the "
        f"watchdog's OWN measurement, which should be impossible: {failed}")
    # Premise: this scenario actually exercises something. If `took` had
    # stayed small, a slow teardown would not be slowing anything and this
    # test would prove nothing about #620.
    assert took >= BOUND_S + SLOW_TEARDOWN_S - 0.5, (
        f"the slow teardown did not actually slow the wall clock ({took:.2f}"
        f" s, expected at least {BOUND_S + SLOW_TEARDOWN_S - 0.5:.2f} s) -- "
        f"this scenario proves nothing about #620 without it")
