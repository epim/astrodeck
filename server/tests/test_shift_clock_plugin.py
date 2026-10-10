# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#917: the committed wall-clock replay plugin (tests/tools/shift_clock.py).

The first replay tool lived in a session scratchpad and swapped ``time.time``
in ``pytest_configure``, after conftest.py had imported astrodeck. Any
``field(default_factory=time.time)`` had by then bound the REAL clock;
``SafetyReading.ts`` is one. ``Hub.safety_reading()`` ages that stamp against
``time.time()``, so under the shifted clock every reading looked stale, the
bus said "UNSAFE: safety read stale/unavailable -> pause", and
``test_setup_target_holds_for_light_before_autofocus_runs`` failed at all
eight offsets (0 to 21 h) while passing on the real clock. A replay that
reports a constant failure reads as a clock dependence and sends the next
person after a bug that is not there.

These tests run the plugin in a throwaway pytest session, never in this one:
installing it here would shift the clock under every other test. The throwaway
suite's conftest imports a class with a ``default_factory=time.time`` AND the
real ``SafetyReading`` before any test runs, exactly as the repo's conftest
imports astrodeck, and its one test records every clock the plugin claims to
move. The REAL time comes from the mtime of a file the test writes - the OS
stamps files, and no plugin reaches it.

MUTATIONS RUN on tests/tools/shift_clock.py, each from a byte-for-byte backup,
the file restored with a copy and compared by md5sum afterwards:

  M1, THE NAMED MUTANT, the scratchpad plugin's defect: the install moved from
  the ``tryfirst`` ``pytest_load_initial_conftests`` hook to
  ``pytest_configure``. test_the_clock_reads_the_shift_everywhere, both cases.

  M2, no datetime subclass swapped in (``datetime.datetime = Datetime``
  removed). Same test, both cases.

  M3, ``time.time_ns`` left real. Same test, both cases.

  M4, ``time.strftime`` / ``time.localtime`` left real. Same test, both cases.

  M5, the plugin installs whether or not the variable is set (the ``if shift
  is not None`` guard removed, a 0 shift installed). test_an_unset_or_blank_
  variable_is_inert.

  M6, a non-numeric value ignored instead of refused. test_a_value_that_is_
  not_a_number_is_refused_not_ignored.

The failing line each printed is in the docstring of the test that caught it."""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent / "tools"
_SERVER = Path(__file__).resolve().parents[1]

#: How far a recorded clock may sit from the instant it should read. The
#: probe takes the real time from a file mtime a few milliseconds before it
#: reads the clocks; the smallest shift under test is an hour.
_SLACK_S = 3

_CLOCKBOUND = '''\
import time
from dataclasses import dataclass, field


@dataclass
class Reading:
    # The #917 shape: the function object is bound when this class body runs.
    ts: float = field(default_factory=time.time)
'''

_CONFTEST = '''\
import sys

sys.path.insert(0, {server!r})

# Imported at conftest time, as the repo's conftest imports astrodeck.
import clockbound  # noqa: E402, F401
import astrodeck.devices.base  # noqa: E402, F401
'''

_PROBE = '''\
import calendar
import datetime
import json
import pathlib
import time

import clockbound
from astrodeck.devices.base import SafetyReading

FMT = "%Y-%m-%dT%H:%M:%S"


def test_probe():
    here = pathlib.Path(__file__).parent
    stamp = here / "stamp.txt"
    stamp.write_text("x", encoding="utf-8")
    real = stamp.stat().st_mtime            # the OS stamps the REAL clock
    utc = datetime.timezone.utc
    seen = {
        "real": real,
        "time": time.time(),
        "time_ns": time.time_ns() / 1e9,
        "datetime_now_utc": datetime.datetime.now(utc).timestamp(),
        "datetime_utcnow": (datetime.datetime.utcnow()
                            .replace(tzinfo=utc).timestamp()),
        "gmtime": calendar.timegm(time.gmtime()),
        "default_factory": clockbound.Reading().ts,
        "safety_reading": SafetyReading(True).ts,
        "datetime_now_local": datetime.datetime.now().strftime(FMT),
        "localtime": time.strftime(FMT, time.localtime()),
        "strftime": time.strftime(FMT),
        "today": datetime.date.today().isoformat(),
        "stdlib_time": type(time.time).__name__ == "builtin_function_or_method",
        "stdlib_datetime": len(datetime.datetime.__mro__) == 3,
    }
    (here / "probe.json").write_text(json.dumps(seen), encoding="utf-8")
'''

_FLOATS = ("time", "time_ns", "datetime_now_utc", "datetime_utcnow", "gmtime",
           "default_factory", "safety_reading")
_STRINGS = ("datetime_now_local", "localtime", "strftime")


def _suite(root: Path) -> Path:
    suite = root / "suite"
    suite.mkdir()
    (suite / "clockbound.py").write_text(_CLOCKBOUND, encoding="utf-8")
    (suite / "conftest.py").write_text(
        _CONFTEST.format(server=str(_SERVER)), encoding="utf-8")
    (suite / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    return suite


def _run(suite: Path, shift: str | None, *, workers: str = "0",
         plugin: bool = True) -> subprocess.CompletedProcess[str]:
    # An inherited PYTEST_ADDOPTS or PYTEST_PLUGINS from the run that is
    # running THIS test must not decide what the inner run loads, and an
    # inherited ASTRODECK_SHIFT_CLOCK_S must not shift the "unset" cases.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_") and k != "ASTRODECK_SHIFT_CLOCK_S"}
    env["PYTHONPATH"] = str(_TOOLS)
    if shift is not None:
        env["ASTRODECK_SHIFT_CLOCK_S"] = shift
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-n", workers, *(["-p", "shift_clock"] if plugin else []),
         str(suite / "test_probe.py")],
        cwd=suite, env=env, capture_output=True, text=True, timeout=180)


def _seen(suite: Path) -> dict:
    return json.loads((suite / "probe.json").read_text(encoding="utf-8"))


def _local(fmt: str, ts: float) -> str:
    return time.strftime(fmt, time.localtime(ts))


def _assert_every_clock_reads(seen: dict, expected: float) -> None:
    """Each recorded clock sits within the slack of ``expected`` seconds."""
    for name in _FLOATS:
        assert abs(seen[name] - expected) < _SLACK_S, (
            f"{name} reads {seen[name] - expected:+.1f} s from the instant "
            f"it should read")
    window = range(-_SLACK_S, _SLACK_S + 1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    ok = {_local(fmt, expected + d) for d in window}
    for name in _STRINGS:
        assert seen[name] in ok, f"{name} reads {seen[name]!r}, not near {min(ok)}"
    days = {datetime.date.fromtimestamp(expected + d).isoformat()
            for d in window}
    assert seen["today"] in days, seen["today"]


@pytest.mark.parametrize(
    "shift, workers",
    [(18000, "0"), (-3600, "2")],
    ids=["plus-5h-in-process", "minus-1h-on-xdist-workers"])
def test_the_clock_reads_the_shift_everywhere(tmp_path, shift, workers):
    """Every wall-clock read in the session reads real time + the shift, and
    that includes the stamps that were bound BEFORE any test ran: a dataclass
    ``default_factory=time.time`` and the repo's own ``SafetyReading.ts``,
    both imported by conftest.py. The xdist case proves a worker is shifted
    too (the repo's default is ``-n 12``). The run also says so on its last
    lines, under ``-q``.

    RED under M1, the install in ``pytest_configure`` (the scratchpad
    plugin's defect), both cases (observed, verbatim, [plus-5h-in-process]):

        AssertionError: default_factory reads -18000.0 s from the instant it
        should read

    RED under M2, datetime left real, both cases:

        AssertionError: datetime_now_utc reads -18000.0 s from the instant it
        should read

    RED under M3, ``time.time_ns`` left real, both cases:

        AssertionError: time_ns reads -18000.0 s from the instant it should
        read

    RED under M4, strftime and localtime left real, both cases:

        AssertionError: localtime reads '2026-10-10T01:33:55', not near
        2026-10-10T06:33:52
    """
    suite = _suite(tmp_path)
    proc = _run(suite, str(shift), workers=workers)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = _seen(suite)
    _assert_every_clock_reads(seen, seen["real"] + shift)
    assert f"shift_clock: wall clock {shift:+g} s from real time" in proc.stdout, \
        proc.stdout


@pytest.mark.parametrize("shift", [None, "", "  "],
                         ids=["unset", "empty", "blank"])
def test_an_unset_or_blank_variable_is_inert(tmp_path, shift):
    """With the plugin loaded but no shift asked for, nothing is replaced: the
    clocks read real time, ``time.time`` is still the builtin, ``datetime`` is
    still the stdlib class, and the run says nothing about a replay. A tool
    that is always on is not a tool you can leave in the command line.

    RED under M5, the install guard removed (observed, verbatim, [unset]):

        AssertionError: the stdlib clock was replaced although no shift was asked for
    """
    suite = _suite(tmp_path)
    proc = _run(suite, shift)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = _seen(suite)
    assert seen["stdlib_time"] and seen["stdlib_datetime"], \
        "the stdlib clock was replaced although no shift was asked for"
    _assert_every_clock_reads(seen, seen["real"])
    assert "shift_clock:" not in proc.stdout, proc.stdout


@pytest.mark.parametrize("value", ["3h", "nan", "inf"])
def test_a_value_that_is_not_a_number_is_refused_not_ignored(tmp_path, value):
    """``ASTRODECK_SHIFT_CLOCK_S=3h`` is a usage error that names the
    variable, and no test runs. Quietly replaying on the real clock would
    report a pass for an hour that was never tested.

    RED under M6, a bad value ignored (observed, verbatim, ['3h']):

        AssertionError: exit 0, not the usage error (4)
    """
    suite = _suite(tmp_path)
    proc = _run(suite, value)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 4, f"exit {proc.returncode}, not the usage error (4)\n{out}"
    assert "ASTRODECK_SHIFT_CLOCK_S" in out and "finite number" in out, out
    assert not (suite / "probe.json").exists(), "a test ran under a bad shift"
