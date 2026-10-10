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

The plugin imports only pytest and the standard library; it must never import
astrodeck, which is the thing it has to get in front of.

Tests of the plugin itself: `tests/test_shift_clock_plugin.py`.
