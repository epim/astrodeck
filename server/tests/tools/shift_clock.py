# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Wall-clock replay: run a test as if it were a different hour (#917, #682).

A test that reads the sky (a sun altitude, a dark window, a horizon check)
passes or fails by the hour CI happens to run it (#682). "It passed this
morning" is exactly what such a test does, and a rerun may only have moved to
a passing hour. This plugin proves it in minutes: with
``ASTRODECK_SHIFT_CLOCK_S`` set to a number of seconds, every wall-clock read
in the process comes out that many seconds ahead of the real clock, and the
same test can be run at 0, 3, ... 21 h and the results compared. The clock
still advances in real time; only its offset is fixed. Unset (or blank), the
plugin does nothing at all.

IT MUST INSTALL BEFORE conftest.py IMPORTS astrodeck, which is why this is a
``tryfirst`` ``pytest_load_initial_conftests`` hook and not ``pytest_configure``.
A ``field(default_factory=time.time)`` binds the function object once, when its
class body runs. ``SafetyReading.ts`` is one (devices/base.py). Swap
``time.time`` after conftest has imported astrodeck and that default still
reads the REAL clock while ``Hub.safety_reading()`` ages it against the
shifted one: every reading looks stale, the run pauses UNSAFE and never takes
a frame, and the replay reports a failure at every offset that looks exactly
like a clock dependence and is not (#917). The first scratchpad version of
this tool did that.

What is shifted: ``time.time``, ``time.time_ns``, ``time.localtime`` /
``gmtime`` / ``strftime`` called without a time argument (C code that reads
the clock itself and never asks ``time.time``), and ``datetime.datetime.now``
/ ``utcnow``, reached through a subclass swapped into the ``datetime`` module
(a C type takes no attribute assignment). ``date.today`` already asks
``time.time``. What is not: ``time.monotonic`` and the other elapsed-time
clocks, file times the OS stamps (``st_mtime``), ``ctime`` / ``asctime``,
child processes, and any module that bound the real function before this
hook ran. See README.md beside this file for how to run it.

The module imports only pytest and the standard library, never astrodeck: it
runs before the code under test is importable at all."""
from __future__ import annotations

import datetime
import math
import os
import time

import pytest

#: The one switch. Seconds to add to the real clock; unset or blank is inert.
ENV = "ASTRODECK_SHIFT_CLOCK_S"

#: The shift in force in this process, or None while the plugin is inert.
_shift_s: float | None = None


def parse_shift(raw: str | None) -> float | None:
    """The seconds to shift by, or None (inert) for an unset or blank ``raw``.

    Anything else that is not a finite number is a usage error, not an inert
    plugin: a replay that quietly ran on the real clock would report a pass
    for an hour it never tested."""
    if raw is None or not raw.strip():
        return None
    try:
        shift = float(raw)
    except ValueError:
        shift = math.nan
    if not math.isfinite(shift):
        raise pytest.UsageError(
            f"{ENV}={raw!r} is not a finite number of seconds")
    return shift


def install(shift_s: float) -> None:
    """Make this process's wall clock read ``shift_s`` seconds ahead of real
    time. Once per process: a second call would shift the shifted clock."""
    global _shift_s
    if _shift_s is not None:
        return
    real_time, real_time_ns = time.time, time.time_ns
    real_localtime, real_gmtime = time.localtime, time.gmtime
    real_strftime = time.strftime
    real_datetime = datetime.datetime
    shift_ns = round(shift_s * 1e9)

    def shifted_time() -> float:
        return real_time() + shift_s

    def shifted_time_ns() -> int:
        return real_time_ns() + shift_ns

    def localtime(secs=None):
        return real_localtime(shifted_time() if secs is None else secs)

    def gmtime(secs=None):
        return real_gmtime(shifted_time() if secs is None else secs)

    def strftime(fmt, t=None):
        return real_strftime(fmt, localtime() if t is None else t)

    class Datetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(shifted_time(), tz)

        @classmethod
        def utcnow(cls):
            return cls.fromtimestamp(
                shifted_time(), datetime.timezone.utc).replace(tzinfo=None)

    # Reprs and pickles name the class; keep them the stdlib's.
    Datetime.__name__ = Datetime.__qualname__ = "datetime"
    Datetime.__module__ = "datetime"

    time.time, time.time_ns = shifted_time, shifted_time_ns
    time.localtime, time.gmtime, time.strftime = localtime, gmtime, strftime
    datetime.datetime = Datetime
    _shift_s = shift_s


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests():
    shift = parse_shift(os.environ.get(ENV))
    if shift is not None:
        install(shift)


def pytest_terminal_summary(terminalreporter):
    """One line that says the replay really ran. It prints under ``-q`` too:
    the opposite of the #917 false red is a replay whose plugin never loaded,
    which reports a clean pass for an hour it never tested."""
    if _shift_s is not None:
        terminalreporter.write_line(
            f"shift_clock: wall clock {_shift_s:+g} s from real time, reading "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC at the "
            f"end of the run")
