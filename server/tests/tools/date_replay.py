# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Date replay of the engine-using test files (#1046, #1045, #682).

A test that reads the calendar (the Sun-exclusion cone, the Moon, a dark
window, a twilight, a freshness age) passes or fails by the day CI runs it.
``shift_clock.py`` can run a test as if it were another day; this runs the
files that start an engine at a set of days, and reports the failures that
are the DATE'S and nobody else's:

    failed shifted  -  failed again on the real clock  -  KNOWN_ARTIFACTS

A test that also fails unshifted is red for its own reasons (load, order, a
real defect) and is not blamed on the date. What is left is a test that is
green today and red on that day: fix it by pinning the clock it reads or by
choosing a fixture that no season can break (SUN_PROOF_TARGET in _simhub.py).

Run it from ``server/``, before a release and after adding an engine test::

    python tests/tools/date_replay.py                  # 4 seasons, engine files
    python tests/tools/date_replay.py --dates 2027-06-13,2026-12-21
    python tests/tools/date_replay.py --every-days 60 --count 6 --workers 4
    python tests/tools/date_replay.py --list           # only name the files
    python tests/tools/date_replay.py --files tests/test_x.py --dates 2027-06-13

The engine files cost about 7 minutes at ``--workers 4`` per date. The default
dates are the next 21 March, 13 June (inside the 4 to 22 June window in which
M42 is in the Sun's cone), 23 September and 21 December, each read at 12:00
UTC. A day replayed tells you about that day: a band narrower than the spacing
of the dates can slip between them, so add a date when you suspect one.

Exit status 0: no date-dependent failure. 1: at least one. 2: usage error.

WHAT THE REPLAY CANNOT SEE (see README.md beside this file): everything
``shift_clock`` does not shift (file times the OS stamps, ``time.monotonic``,
child processes). A test that stamps a file with the OS and reads its age
against the clock must stamp it with ``time.time()`` (``os.utime``) instead:
``KNOWN_ARTIFACTS`` is for the ones that cannot, and is empty on purpose.

Pure parts first (``dates_for``, ``offset_seconds``, ``engine_using_files``,
``parse_failures``, ``date_dependent``) so a test can grade them without a
replay. Standard library only."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
SERVER = TESTS.parent

#: Test ids that fail under ANY large shift for a reason that is not the test's
#: date reading (a blind spot of the plugin that the test cannot be fixed
#: around). Empty: the one known case, a freshness age read off a file's
#: st_mtime, is fixed in the test (it stamps its files with the shifted clock).
KNOWN_ARTIFACTS: frozenset[str] = frozenset()

#: MM-DD of the default seasons.
SEASON_DAYS = ("03-21", "06-13", "09-23", "12-21")

_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+?)(?: - .*)?$", re.MULTILINE)
_DRIVES_A_SIM_HUB = re.compile(r"\bconnect_sim\b|\bsim_hub\b")
_STARTS_AN_ENGINE = re.compile(r"\bSequenceEngine\b")


def dates_for(now: dt.datetime, *, days: tuple[str, ...] = SEASON_DAYS,
              every_days: int | None = None, count: int = 6) -> list[dt.date]:
    """The days to replay. By default the next occurrence of each of ``days``
    (MM-DD) on or after ``now``'s date, in calendar order; with ``every_days``
    instead ``count`` days that far apart, the first one ``every_days`` after
    today."""
    today = now.date()
    if every_days is not None:
        return [today + dt.timedelta(days=every_days * k)
                for k in range(1, count + 1)]
    found = []
    for mmdd in days:
        month, day = (int(p) for p in mmdd.split("-"))
        year = today.year
        while True:
            try:
                cand = dt.date(year, month, day)
            except ValueError:                # 29 February in a common year
                year += 1
                continue
            if cand >= today:
                break
            year += 1
        found.append(cand)
    return sorted(found)


def offset_seconds(day: dt.date, now_ts: float) -> int:
    """Seconds to add to the clock so that it reads ``day`` at 12:00 UTC."""
    noon = dt.datetime(day.year, day.month, day.day, 12,
                       tzinfo=dt.timezone.utc)
    return int(noon.timestamp() - now_ts)


def engine_using_files(tests_dir: Path = TESTS) -> list[Path]:
    """The test files that build a simulator hub AND name a SequenceEngine:
    the files in which a run can reach the slew gates, the Moon and the dark
    window. A file that only imports the name is included; the replay costs a
    little more and misses nothing."""
    found = []
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _DRIVES_A_SIM_HUB.search(text) and _STARTS_AN_ENGINE.search(text):
            found.append(path)
    return found


def parse_failures(output: str) -> set[str]:
    """Test ids from a ``-rfE`` summary: every ``FAILED id`` and ``ERROR id``."""
    return set(_FAILED.findall(output))


def date_dependent(shifted: set[str], unshifted: set[str],
                   artifacts: frozenset[str] = KNOWN_ARTIFACTS) -> set[str]:
    """Failures that are the date's: red shifted, green on the real clock,
    and not a listed artifact of the plugin."""
    return {t for t in shifted - unshifted if t not in artifacts}


def _pytest(files: list[str], *, shift_s: int | None, workers: int,
            python: str, cwd: Path = SERVER) -> tuple[set[str], str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_") and k != "ASTRODECK_SHIFT_CLOCK_S"}
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "-n", str(workers), "-rfE", "--tb=no"]
    if shift_s is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(HERE), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        env["ASTRODECK_SHIFT_CLOCK_S"] = str(shift_s)
        cmd += ["-p", "shift_clock"]
    proc = subprocess.run(cmd + files, cwd=cwd, env=env,
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    if shift_s is not None and "shift_clock: wall clock" not in out:
        raise SystemExit("the shift_clock plugin did not report: the replay "
                         "ran on the real clock, so its verdict is void\n"
                         + out[-2000:])
    return parse_failures(out), out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dates", help="comma separated YYYY-MM-DD days")
    ap.add_argument("--every-days", type=int,
                    help="replay --count days this far apart instead")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--files", nargs="*",
                    help="test files, instead of the engine-using ones")
    ap.add_argument("--list", action="store_true",
                    help="print the files that would be replayed, and stop")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--cwd", type=Path, default=SERVER,
                    help="run pytest from here (default server/); the files "
                         "must be under it for pytest to name them")
    args = ap.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    try:
        if args.dates:
            days = [dt.date.fromisoformat(d.strip())
                    for d in args.dates.split(",") if d.strip()]
        else:
            days = dates_for(now, every_days=args.every_days,
                             count=args.count)
    except ValueError as exc:
        print(f"date_replay: {exc}", file=sys.stderr)
        return 2
    files = args.files or [p.relative_to(SERVER).as_posix()
                           for p in engine_using_files()]
    if args.list:
        print("\n".join(files))
        print(f"{len(files)} files", file=sys.stderr)
        return 0
    if not files or not days:
        print("date_replay: nothing to replay", file=sys.stderr)
        return 2

    print(f"date_replay: {len(files)} files at {len(days)} dates "
          f"({', '.join(d.isoformat() for d in days)})", flush=True)
    bad: dict[str, set[str]] = {}
    for day in days:
        shift = offset_seconds(day, time.time())
        shifted, _ = _pytest(files, shift_s=shift, workers=args.workers,
                             python=args.python, cwd=args.cwd)
        again: set[str] = set()
        if shifted:
            # Only the failed ids, on the real clock: a failure that is still
            # there is not the date's.
            again, _ = _pytest(sorted(shifted), shift_s=None,
                               workers=args.workers, python=args.python,
                               cwd=args.cwd)
        mine = date_dependent(shifted, again)
        print(f"{day.isoformat()}: {len(shifted)} red shifted, "
              f"{len(shifted & again)} red on the real clock too, "
              f"{len(mine)} date-dependent", flush=True)
        for test in sorted(mine):
            print(f"  DATE-DEPENDENT {test}")
        if mine:
            bad[day.isoformat()] = mine
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
