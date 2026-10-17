# Wall-clock replay (`shift_clock.py`)

A test that reads the sky (a sun altitude, a dark window, a horizon check)
can pass or fail by the hour CI happens to run it (#682). "It passed earlier
today" proves nothing: a rerun may only have moved to a passing hour. This
pytest plugin runs the same test as if it were a different hour, so the claim
is settled in minutes.

Set `ASTRODECK_SHIFT_CLOCK_S` to a number of seconds and every wall-clock
read in the pytest process comes out that many seconds ahead of the real
clock (negative is behind). The clock still advances in real time. With the
variable unset or blank the plugin does nothing, so it can stay in a command
line.

## Run it

From `server/`, on Windows PowerShell, sweeping the day in 3 h steps:

```powershell
$env:PYTHONPATH = "$PWD\tests\tools"
$test = "tests/test_w4_target_window_and_light_hold.py::test_setup_target_holds_for_light_before_autofocus_runs"
foreach ($h in 0, 3, 6, 9, 12, 15, 18, 21) {
    $env:ASTRODECK_SHIFT_CLOCK_S = [string]($h * 3600)
    "== +$h h"
    & .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -n0 -p shift_clock $test
}
Remove-Item Env:ASTRODECK_SHIFT_CLOCK_S, Env:PYTHONPATH
```

- `PYTHONPATH` must be a Windows-style path. A `/c/...` path is converted by
  some shells and not by others; where it is not, pytest stops with
  `Error importing plugin "shift_clock"`.
- `-p shift_clock` is what loads it (`PYTEST_PLUGINS=shift_clock` does the
  same). Setting the variable without it changes nothing and the run is on
  the real clock.
- A replay that really ran prints one line just above pytest's counts line,
  under `-q` too: `shift_clock: wall clock +10800 s from real time, reading
  ... UTC`. No line, no replay. The reading is the shifted time at the end of
  the run, so it names the hour the test last saw.
- `-n0` keeps one process and clean output. The default `-n 12` works too:
  the variable and `-p` reach every worker.
- A value that is not a finite number (`3h`, `nan`) is a usage error, not a
  silent real-clock run.

## Reading the result

- Red at some offsets and green at others: the test depends on the hour. The
  offsets give the hours (`+3 h` from a run at 08:30 UTC reads 11:30 UTC).
  Fix it by pinning every clock its path reads, not just one (#682 pinned
  `engine.time` and left `coords.time` on the real clock).
- Green everywhere: the hour is not the cause. Do not call the earlier failure
  a flake on this evidence alone; it may be load, ordering or a timing margin.
- Red only in a band of dates, green at every hour of a date outside it, with
  the run ending `unsafe: slew blocked by sun-exclusion cone`: the target is
  inside the Sun's 30 degree cone on those dates (`Hub._check_solar` reads the
  Sun by the calendar and no site). That is a fixture, not an hour: use a
  target clear of the Sun all year (the north ecliptic pole, RA 18 h Dec
  +66.56, is 90 degrees from it every day) or pin `coords.sun_radec`, as
  test_w4_target_window_and_light_hold.py does (#1026). Sweep dates in 15 day
  steps, not hours.
- Red at EVERY offset, offset 0 included: suspect the replay before the test.
  Run it once on the real clock without the plugin. If that is green, look
  for a clock the plugin does not move (below), and do not read it as a
  clock dependence.

## Why it installs so early

`shift_clock` swaps `time.time` in a `tryfirst` `pytest_load_initial_conftests`
hook, before `conftest.py` imports astrodeck. That order is the point. A
`field(default_factory=time.time)` binds the function object when its class
body runs. `SafetyReading.ts` (`devices/base.py`) was one until #946 made it
look the clock up per call, and any module that binds the real function at
import still is. A plugin that swaps the clock in `pytest_configure` runs
after conftest has imported astrodeck, so such a default still reads the real
clock while `Hub.safety_reading()` ages the stamp against the shifted one.
Every reading then looks stale, the run pauses
`UNSAFE: safety read stale/unavailable` and never takes a frame, and the
replay fails at all eight offsets for a reason that has nothing to do with the
hour (#917). The first scratchpad version of this tool did exactly that.

## What moves and what does not

Moved: `time.time`, `time.time_ns`, `time.localtime`, `time.gmtime` and
`time.strftime` called without a time argument (C code that reads the clock
itself), `datetime.datetime.now` and `utcnow` (the plugin puts a `datetime`
subclass in the `datetime` module; `date.today` already asks `time.time`).

Not moved:

- `time.monotonic`, `perf_counter` and `asyncio`'s clock: they measure elapsed
  time, not the hour.
- File times the OS stamps (`st_mtime`). A cache or freshness check that
  compares a file's mtime with `time.time()` sees the offset as age.
- `time.ctime` and `time.asctime` with no argument.
- A child process. It has the variable in its environment but not the plugin.
- A module that bound the real function before the hook ran (in practice only
  pytest's own, so its durations stay real), and a `datetime` object made by C
  code outside the `datetime` module (it is not an instance of the subclass).

## A shift of months: astropy's IERS table

astropy asks its own `Time.now()`, which is the shifted clock, how old its IERS
table's predictive values are. Past about 30 days every UT1 conversion (an
`AltAz` transform makes one) raised `ValueError: interpolating from IERS_Auto
using predictive values that are more than 30.0 days old`, after ten seconds
spent trying to download a newer table. A replay of a season-dependent sky test
(#925 needs a year) then read as a failure that had nothing to do with the test
(#974).

While a shift is in force, and only then, the plugin sets
`astropy.utils.iers.conf`:

- `auto_download = False`: no network, so a replay never waits on a download
  or depends on one, and astropy uses the table it ships.
- `auto_max_age = None`: that table is never "too old" for the shifted clock.
- `iers_degraded_accuracy = "warn"`: a time past the end of the table
  degrades with an `IERSDegradedAccuracyWarning` instead of raising, where
  astropy falls back to a table without predictions.

The cost is accuracy. Past the table, UT1-UTC (under a second) and polar
motion are approximated, which moves a computed altitude by well under an
arcminute. That costs nothing when the question is whether a test depends on
the date (the hour, the season), and it is wrong for a test that asserts an
altitude to the arcsecond at a shifted date. An unshifted run, with the
variable unset or blank, leaves astropy's settings alone.

If a replay still fails with `Input values for datetime class must be datetime
objects`, something imported astropy before this plugin swapped `datetime`:
a helper plugin that imports it at module top, or in a hook that runs before
this one. astropy keeps the real `datetime` class from then on and rejects the
shifted one. Import astropy later (inside a function or a fixture).

The plugin imports only pytest and the standard library at import time; it
must never import astrodeck, which is the thing it has to get in front of.
astropy is imported inside `relax_iers`, once a shift is in force, after the
clock is swapped.

Tests of the plugin itself: `tests/test_shift_clock_plugin.py`.

## Replaying the whole engine subset at several days (`date_replay.py`)

One test at one date is the case above. The suite as a whole needs the same
question asked of every file that starts an engine, at a few days of the year,
because a seasonal reader (the Sun-exclusion cone, the Moon, a dark window, a
freshness age) is green on most days (#1046, #1045, #1026, #682).

From `server/`:

```powershell
& .\.venv\Scripts\python.exe tests/tools/date_replay.py                 # 4 seasons
& .\.venv\Scripts\python.exe tests/tools/date_replay.py --dates 2027-06-13,2026-12-21
& .\.venv\Scripts\python.exe tests/tools/date_replay.py --every-days 60 --count 6
& .\.venv\Scripts\python.exe tests/tools/date_replay.py --list           # the files
```

It runs the files that build a simulator hub and name a `SequenceEngine`
under `-p shift_clock` at each day (12:00 UTC), re-runs the failures on the
real clock, and prints the ones that failed only shifted as `DATE-DEPENDENT`.
Exit 1 if there are any. Do it before a release and after adding an engine
test; about 7 minutes per date at `--workers 4`. A band of days narrower than
the spacing of the dates can fall between them: add a date when you suspect
one (M42 is in the Sun's cone from 4 to 22 June, so 13 June is a default).

Run it with nothing else using the box: a slow machine turns timing margins
into reds on the real clock too, and those are subtracted, not blamed on the
date.

### What the plugin does not shift (expected artifacts)

- **File times the OS stamps** (`st_mtime`). The calibration library ages a
  frame by its mtime against `time.time()` (`calibration/library.py`,
  `iter_cal_headers`), so under a large shift every file written in the run
  reads months old. `test_w17_dusk_flats_panel.py::test_a_restart_finds_fresh_
  flats_and_shoots_none` was red under any shift of more than the freshness
  window for that reason alone. The cure is in the test: stamp the files with
  `os.utime(path, (time.time(), time.time()))` after the code under test wrote
  them. `KNOWN_ARTIFACTS` in `date_replay.py` is for a case that cannot be
  fixed that way, and is empty.
- `time.monotonic`, `time.perf_counter` and the event loop's clock: elapsed
  time is real, which is right.
- Child processes that do not load the plugin, and any module that bound the
  real `time.time` before the hook ran.

### The guard that keeps the next one out (`_SunConeGuard`)

`conftest.py` fails, at its own teardown, a test that slews by the REAL Sun
to a target the exclusion cone can refuse on some day of the year (the target's
ecliptic latitude is smaller than the cone: `_sun_cone.py`). It reads geometry,
not the date, so it is the same on every day CI runs. The fixes are the ones in
`_simhub.py`: `SUN_PROOF_TARGET` (the north ecliptic pole), a pinned
`coords.sun_radec`, or `@pytest.mark.seasonal_sun_target` when the test means
it. Files that already slewed to such a target when the guard landed are in
`sun_cone_baseline.txt`: a ratchet, to be emptied, never added to. To list the
tests in it afresh run the suite with `ASTRODECK_SUN_CONE_REPORT=<file>` set:
it appends one line per test instead of failing.

Tests: `tests/test_date_replay_and_sun_cone_guard.py`.

