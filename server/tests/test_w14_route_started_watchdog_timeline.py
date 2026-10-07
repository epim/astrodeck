# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#683's proof: the route-started spin test reads the WATCHDOG's own
timeline, not its own wall clock, and the sentence it reads is the one the
watchdog writes.

test_s7_sim_rotating_2x2.py::test_a_spin_in_a_route_started_run_fails_with_
the_watchdog_report bounded the night on ``time.monotonic() - spins[0] <
BOUND_S + 3.0``, the shape #620 named and WP-68 (wave 5) fixed in
test_group_harness_watchdog.py; that file's sweep never reached this one, and
it failed three runs in three (``-n 0``) on a loaded box: "the night failed 4.4
s after the spin began, for a bound of 1 s". Nothing was wrong with the
watchdog: it fires and writes its report on its bound, and what a loaded box
stretches is everything AFTER that (the raise reaching the spinning thread, the
harness's teardown, the scheduler handing the thread back).

test_w5_group_harness_watchdog_timeline.py forces that shape for the
`Night.run` watchdog by slowing ``Night.close``. This file forces it for the
ROUTE-STARTED one, which has no ``close`` between the report and the test: the
test's own ``Night.settle`` raises the report, so the delay is put there
(`_spin_scenario`'s ``after_report_delay_s``). The test's wall clock then
passes the old bound with nothing wrong, while the watchdog's own
``away`` stays at its bound, so a bound read from the first fails here and one
read from the second does not.

The regex the three tests read the timeline with is one function,
``_group_harness.watchdog_timeline``, so the second half of this file is its
drift guard: it builds a report through `_SpinWatchdog` itself and fails when
the helper stops reading it, which is what a reworded report would do to the
three tests one at a time otherwise.

Each test names the mutant it was shown RED under, run from a byte backup and
restored byte-identically (sha256 compared) afterwards, with the failure
quoted verbatim.
"""
from __future__ import annotations

import pytest

from _flow_night import flow_rig  # noqa: F401 (fixture)
from _group_harness import (_SpinWatchdog, group_store,  # noqa: F401 (fixture)
                            watchdog_timeline)
from test_s7_sim_rotating_2x2 import BOUND_S, _spin_scenario

#: Added after the watchdog has raised its report: enough to push the test's own
#: wall clock (`SpinRun.wall_s`) past the OLD bound of BOUND_S + 3.0 = 4.0 s
#: even though the watchdog fires at about BOUND_S.
SLOW_AFTER_REPORT_S = 3.5


async def test_a_slow_wait_after_the_report_does_not_move_the_watchdogs_timeline(
        flow_rig, monkeypatch):
    """A spin the route-started watchdog breaks on time, with the wait that
    reads its report made slow after the fact (standing in for a loaded box):
    the watchdog's own measurement stays near its bound while this test's wall
    clock passes the old 4.0 s bound.

    MUTANT "the bound reads the test's wall clock again" (the shared
    scenario's ``away_s`` assertion in test_s7_sim_rotating_2x2.py swapped back
    for the pre-fix ``assert took < BOUND_S + 3.0``) -- RED, observed
    verbatim:

        AssertionError: at +30.0 s of night 1: the night failed 5.6 s after
        the spin began, for a bound of 1 s
        assert 5.5939999999827705 < (1.0 + 3.0)

    -- the false failure #683 reported from a loaded box ("failed 4.4 s after
    the spin began, for a bound of 1 s"), reproduced here from the delay alone,
    with no load at all. The un-delayed case in test_s7_sim_rotating_2x2.py
    stays green under the same mutant on an idle box, which is why a proof that
    forces the delay is needed.
    """
    run = await _spin_scenario(flow_rig, monkeypatch,
                               after_report_delay_s=SLOW_AFTER_REPORT_S)

    # Premise: the delay really did stretch the test's own clock past the old
    # bound. If it had not, this case would prove nothing about #683.
    assert run.wall_s > BOUND_S + 3.0, (
        f"the delay did not push the wall clock ({run.wall_s:.2f} s) past the "
        f"old bound of {BOUND_S + 3.0:.1f} s, so this case proves nothing "
        f"about #683")
    assert run.away_s < BOUND_S + 1.0, (
        f"the watchdog's own timeline measured {run.away_s:.2f} s away for a "
        f"bound of {BOUND_S:g} s, though the wait after its report is what "
        f"was slow: {run.report}")
    assert run.bound_s == BOUND_S, run.bound_s


@pytest.mark.parametrize("what", ["Night.run", "Night (route-started run)"])
def test_the_helper_reads_the_report_the_watchdog_writes(what):
    """DRIFT GUARD. The report is built by `_SpinWatchdog._describe` itself
    (the real sentence, for each of the two names the harness gives a
    watchdog), and `watchdog_timeline` must read the two numbers out of it:
    ``away`` as `_describe` rounds it (one decimal) and the bound as it
    formats it (``:g``).

    MUTANT "helper regex changed" (``_WATCHDOG_TIMELINE``'s "did not come
    back" reworded to "did not return") -- RED for both names, observed
    verbatim (the report goes on with the loop's frames and faulthandler's
    dump):

        the failure text carries no watchdog timeline:
        Night.run: the event loop did not come back for 2.7 s of real time,
        against a bound of 1.5 s (_group_harness.SPIN_BOUND_S unless the test
        set its own). A spin that never yields (#319): ...

    MUTANT "helper reads the two numbers the wrong way round" (``return
    float(found.group(2)), float(found.group(1))``) -- RED, the same report
    after ``assert (1.5, 2.7) == (2.7, 1.5)``.
    """
    report = _SpinWatchdog(1.5, what)._describe(2.74)
    assert report.startswith(f"{what}: the event loop did not come back"), (
        f"premise: the report no longer opens with its name and the loop "
        f"sentence:\n{report}")
    assert watchdog_timeline(report) == (2.7, 1.5), report


def test_the_helper_fails_with_the_whole_report_when_it_reads_no_timeline():
    """A report with no timeline in it is a failure that shows the report, not
    a ``None`` or a ``KeyError`` that hides what the watchdog said."""
    said = "the loop was away, said nothing about for how long"
    with pytest.raises(pytest.fail.Exception) as raised:
        watchdog_timeline(said)
    assert "carries no watchdog timeline" in str(raised.value)
    assert said in str(raised.value)
