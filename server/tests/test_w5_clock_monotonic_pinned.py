"""WP-40 (#299): `_Clock` is test_idle_park_hold.py's, shared by the thirteen
`_Clocked` test files (test_waits_that_watch_the_mount.py,
test_h4_pause_closes_the_idle_latch.py, test_s7_waits_read_the_weather.py,
test_h4_monitor_to_read_needs_safety_check.py,
test_safe_again_after_cooler_gate.py, test_refused_close_keeps_idle_stop.py,
test_reopen_close_guider_bound.py, test_run_end_completes_the_idle_stop.py,
test_cooling_wait_watches_the_mount.py, test_idle_park_hold.py itself,
test_cloud_hold_watch.py, test_one_setup_per_acquisition.py and
test_idle_stop_retry_clock.py) and by test_engine_logs_carry_no_site_
numbers.py. Until this fix it faked only ``time()``; ``monotonic()`` fell
through ``__getattr__`` to the real module, so anything the engine times
with ``time.monotonic()`` (the hop-cost ETA term in `_setup_target`) was a
wall-clock racer inside a harness whose whole point is that nothing in a
simulated night depends on wall time. test_engine_logs_carry_no_site_
numbers.py's module docstring and its `_rig` carry the full diagnosis (the
issue's #299 occurrence); this is a direct, fast check on the clock itself,
no simulated night needed, so the fix is graded once at its source rather
than once per file that happens to exercise a ``monotonic()`` read.
"""
from __future__ import annotations

import time as real_time

from test_idle_park_hold import _Clock

#: Far from any real `time.monotonic()` reading on a box that has not been up
#: for over a century, so equality is a real check, not a coincidence.
FAKE_T0 = 5_000_000.0


def test_monotonic_reads_the_fake_clock_not_the_real_one():
    """``monotonic()`` must track ``self.t``, the fake clock ``time()``
    already reads, not the real wall clock (which, unlike ``self.t``, moves
    on its own while this test's own assertions run).

    Mutant "fake only time()" (the `monotonic` method deleted from `_Clock`,
    its shape before WP-40): RED, observed verbatim (the real reading is this
    box's uptime in seconds, so only the mismatch is reproducible, not the
    number) -
        AssertionError: _Clock.monotonic() read the real clock, not self.t:
        1416445.671 != 5000000.0
    """
    clock = _Clock(real_time, FAKE_T0)
    assert clock.monotonic() == FAKE_T0, (
        f"_Clock.monotonic() read the real clock, not self.t: "
        f"{clock.monotonic()} != {FAKE_T0}")
    # And it must track self.t going forward, not a one-time snapshot of it:
    # the hop-cost ETA takes two readings around a block of engine work.
    clock.t = FAKE_T0 + 50.0
    assert clock.monotonic() == FAKE_T0 + 50.0, (
        "_Clock.monotonic() did not move with the fake clock after an "
        "advance")


def test_monotonic_and_time_agree_as_the_module_docstring_promises():
    """test_idle_park_hold.py's `_Clock` docstring says ``time()`` and
    ``monotonic()`` both read the fake clock: they must never diverge, since
    engine.py mixes both calls (``time.time()`` for the wall-clock stamp
    carried in events, ``time.monotonic()`` for a duration) and code that
    compared one to the other would see two clocks if they could drift."""
    clock = _Clock(real_time, FAKE_T0)
    assert clock.time() == clock.monotonic()
    clock.t += 12.5
    assert clock.time() == clock.monotonic()
