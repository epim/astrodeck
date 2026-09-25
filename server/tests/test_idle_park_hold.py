"""The idle mount is park-held on the HAZARD's clock (#165).

A mount left tracking with no frame loop watching it has nothing checking its
floor or its meridian: those checks run per frame, the wait's safety gate has
no target, and sun_watch stands down while a sequence runs. The teardown used
to hang off the length of ONE computed wait ("longer than WAIT_TEARDOWN_S"),
which two ordinary waits never are:

* an eta-0 wait - a setting target below its start gate with its window open,
  which `schedule.gating_status` answers with ``eta_s = 0`` - so the scheduler
  re-evaluated every 5 s until the window closed;
* a constraint wait, whose ``start_ts`` is nulled on purpose, so it rode the
  scheduler's 5 s else-branch, which had no teardown at all.

Both kept tracking the finished target, unwatched, for as long as they lasted.
That is the "safety rides value paths" class: the guard hung off the wait's own
interval, and the hazard does not care how the wait was computed.

Three follow-ups from the S0 review live here too: a stop is READ BACK, and one
the mount did not take is asked again (on its own clock since #189 A3, see
test_idle_stop_retry_clock.py), said once per idle spell; the predicate's
CEILING end (the zenith keep-out) is acted on like its floor; and a new run on
the same engine does not watch the last run's target, unless `start()` is told
that a caller left the mount tracking one (#202).

THE HARNESS IS A CLOCKED SIMULATOR WITH CONCURRENT SLEEPERS. `engine_mod.time`
is a fake wall clock, and engine.py's `asyncio.sleep` parks the calling task on
a timer heap when that task is the engine's run task or its idle-stop retry
task. A driver advances the clock to the EARLIEST wake, and only once every
live engine task is parked, so two tasks sleeping side by side each wake on
their own schedule: one clock that summed both tasks' sleeps would put the
retry's minute into the wait loop's tick and fake the very cadence under test.
A whole night runs through the real `_run_scheduled` and `_wait_until` against
sim_hub without a real sleep. The sim camera parks the run task for each
exposure. At a horizon the driver stops and every engine task stays parked
until the test has looked, so the end-of-run wind-down cannot be mistaken for
the park-hold under test. `set_tracking` is spied (pass-through) with the fake
time of every call. The site is a fixture, never the real one.

A MOUNT THAT WILL NOT STOP hangs, not fails fast: its ``set_tracking(False)``
(and, when unreadable, its ``get_tracking``) parks the calling task for
``MOUNT_QUERY_TIMEOUT_S`` and then raises ``asyncio.TimeoutError``, which is
what a dead serial link costs the engine (the #133 class).
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import re
import time

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.catalog import altaz
from astrodeck.config import (AppConfig, ConfigStore, EscalationConfig,
                              SafetyConfig, Site)
from astrodeck.devices.sim import SimTelescope
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence import schedule
from astrodeck.sequence.engine import SafetyAbort

#: A fixture site. Any real site would do as well; this one is a round number.
LAT, LON = 40.0, -74.0
TICK = engine_mod.SCHEDULE_WAIT_STEP_S
TEARDOWN = engine_mod.WAIT_TEARDOWN_S
RETRY = engine_mod.IDLE_STOP_RETRY_S
HANG = engine_mod.MOUNT_QUERY_TIMEOUT_S
#: Fake seconds per science exposure unless a test says otherwise.
EXPOSURE_S = 30.0


# --------------------------------------------------------------------- fixtures

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Fixture", latitude=LAT, longitude=LON,
                        is_default=False))
    store.set_safety(SafetyConfig(enabled=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    # A sim slew takes at least 0.5 s outside the fast path (inside it, #207
    # makes that zero too); nothing here is about it.
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


# ---------------------------------------------------------------------- harness

class _Clock:
    """engine.py's `time`: `time()` reads the fake clock, the rest is real."""

    def __init__(self, real, t0: float):
        self._real = real
        self.t = t0

    def time(self) -> float:
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Asyncio:
    """engine.py's `asyncio`: `sleep` goes to the harness, the rest is real."""

    def __init__(self, real, run: "_Clocked"):
        self._real = real
        self._run = run

    async def sleep(self, delay, result=None):
        return await self._run.sleep(delay, result)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Clocked:
    """One clocked-simulator night. See the module docstring.

    THE ENGINE TASKS are the run task and the idle-stop retry task, by
    identity (``engine._task``, ``engine._idle_stop_task``). Each parks on
    the timer heap when it sleeps, and `_drive` advances the clock to the
    earliest wake only when every live one of them is parked. A task that
    is running, or waiting on real I/O in the sim, holds the clock still.
    Any other task that sleeps through engine.py sleeps for real, as the
    watchdog does, and so sits out the test.

    ``t0`` pins where the fake night starts; left out, it starts now. A test
    whose timeline crosses a minute-resolution ``HH:MM`` boundary pins it
    (`_t0_at_second`), or the night's shape moves with the wall-clock second
    the test happened to start on (#223).
    """

    def __init__(self, hub, monkeypatch, *, horizon_s: float,
                 t0: float | None = None):
        self.hub = hub
        self.t0 = time.time() if t0 is None else float(t0)
        self.clock = _Clock(time, self.t0)
        self.horizon = self.t0 + horizon_s
        self.frozen = asyncio.Event()
        self.engine = SequenceEngine(hub)
        self.tracking_off: list[float] = []     # fake time of set_tracking(False)
        #: (fake time, on, which engine task asked) of every set_tracking
        self.tracking_calls: list[tuple[float, bool, str]] = []
        self.exposure_starts: list[tuple[str, float]] = []
        self.exposure_ends: list[tuple[str, float]] = []
        self.slews: list[float] = []
        self.ticks: list[float] = []            # fake time of every 5 s wait sleep
        #: (fake time, context) of every `_safety_gate` call
        self.gates: list[tuple[float, str]] = []
        #: (fake time, call, which task) of every mount hang a cancel cut short
        self.cut_hangs: list[tuple[float, str, str]] = []
        #: The mount that will not stop (`_a_mount_that_will_not_stop`).
        self.stop_hangs = False
        self.read_hangs = False
        self._timers: list[tuple[float, int, asyncio.Future]] = []
        self._parked: dict[asyncio.Task, asyncio.Future] = {}
        self._seq = itertools.count()
        self._real_sleep = asyncio.sleep
        monkeypatch.setattr(engine_mod, "time", self.clock)
        monkeypatch.setattr(engine_mod, "asyncio", _Asyncio(asyncio, self))

        tel = hub.devices["telescope"]
        real_set, real_get, real_slew = (tel.set_tracking, tel.get_tracking,
                                         tel.slew)

        async def set_tracking(on):
            if self.frozen.is_set():
                return await real_set(on)
            self.tracking_calls.append((self.clock.t, bool(on), self.who()))
            if not on:
                self.tracking_off.append(self.clock.t)
                if self.stop_hangs:
                    await self.hang("set_tracking(False)")
            await real_set(on)

        async def get_tracking():
            if self.read_hangs and not self.frozen.is_set():
                await self.hang("get_tracking")
            return await real_get()

        async def slew(ra_hours, dec_deg):
            if not self.frozen.is_set():
                self.slews.append(self.clock.t)
            await real_slew(ra_hours, dec_deg)

        monkeypatch.setattr(tel, "set_tracking", set_tracking)
        monkeypatch.setattr(tel, "get_tracking", get_tracking)
        monkeypatch.setattr(tel, "slew", slew)

        real_capture = hub.capture

        async def capture(exposure_s, *a, **kw):
            info = await real_capture(exposure_s, *a, **kw)
            name = kw.get("target", "")
            self.exposure_starts.append((name, self.clock.t))
            if self._is_engine_task(asyncio.current_task()):
                await self._park(float(exposure_s))
            else:
                self.clock.t += float(exposure_s)
            self.exposure_ends.append((name, self.clock.t))
            return info

        monkeypatch.setattr(hub, "capture", capture)

        real_gate = self.engine._safety_gate

        async def safety_gate(*a, **kw):
            if not self.frozen.is_set():
                self.gates.append((self.clock.t, kw.get("context", "")))
            return await real_gate(*a, **kw)

        monkeypatch.setattr(self.engine, "_safety_gate", safety_gate)
        self._driver = asyncio.get_running_loop().create_task(self._drive())

    # ------------------------------------------------------------ the clock

    def _engine_tasks(self) -> list[asyncio.Task]:
        return [t for t in (self.engine._task, self.engine._idle_stop_task)
                if t is not None and not t.done()]

    def _is_engine_task(self, task) -> bool:
        return task is not None and task in (self.engine._task,
                                             self.engine._idle_stop_task)

    def who(self) -> str:
        """Which engine task is asking: "run", "retry", or another name."""
        task = asyncio.current_task()
        if task is not None and task.get_name() == "idle-stop-retry":
            return "retry"
        if task is not None and task is self.engine._task:
            return "run"
        return task.get_name() if task is not None else "none"

    async def _park(self, d: float) -> None:
        """Park the calling engine task until the clock reaches now + ``d``."""
        if self.frozen.is_set():
            await self._real_sleep(0)          # the wind-down after the abort
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._timers, (self.clock.t + max(0.0, d),
                                      next(self._seq), fut))
        self._parked[asyncio.current_task()] = fut
        await fut

    async def _drive(self) -> None:
        """Advance to the earliest wake once every live engine task is parked.

        At the horizon the clock stops and nothing more is woken: every
        engine task stays parked until the test's abort cancels it, so
        nothing after the horizon (the window closing, the end-of-run park)
        may be read as the behaviour under test."""
        while not self.frozen.is_set():
            await self._real_sleep(0)
            live = self._engine_tasks()
            if not live or any(self._parked.get(t) is None
                               or self._parked[t].done() for t in live):
                continue
            while self._timers and self._timers[0][2].done():
                heapq.heappop(self._timers)
            if not self._timers:
                continue
            wake, _seq, fut = heapq.heappop(self._timers)
            self.clock.t = max(self.clock.t, wake)
            if self.clock.t >= self.horizon:
                self.frozen.set()
                return
            fut.set_result(None)

    async def sleep(self, delay, result=None):
        task = asyncio.current_task()
        if not self._is_engine_task(task):
            return await self._real_sleep(delay, result)
        d = max(0.0, float(delay))
        if d == TICK and task is self.engine._task:
            self.ticks.append(self.clock.t)
        await self._park(d)
        return result

    async def hang(self, call: str) -> None:
        """A mount call that never answers: the calling engine task waits out
        the call's bound, ``MOUNT_QUERY_TIMEOUT_S``, then times out."""
        task = asyncio.current_task()
        if self._is_engine_task(task):
            try:
                await self._park(HANG)
            except asyncio.CancelledError:
                self.cut_hangs.append((self.clock.t, call, self.who()))
                raise
        raise asyncio.TimeoutError()

    # ------------------------------------------------------------- the night

    async def night(self, plan: SequencePlan, timeout: float = 60.0,
                    **start_kw) -> None:
        self.engine.start(plan, **start_kw)
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if self.frozen.is_set() or not self.engine.running:
                break
            await self._real_sleep(0.01)
        assert self.frozen.is_set(), (
            f"premise: the run must still be going at the horizon; it ended "
            f"at fake +{self.clock.t - self.t0:.0f}s: {self.engine.state}")

    async def close(self) -> None:
        """The test's abort, then the driver."""
        try:
            await self.engine.abort()
        finally:
            self._driver.cancel()
            await asyncio.gather(self._driver, return_exceptions=True)

    def exposure_end(self, name: str) -> float:
        ends = [t for n, t in self.exposure_ends if n == name]
        assert ends, f"premise: {name} never took an exposure"
        return ends[-1]

    def tracking(self) -> bool:
        return bool(self.hub.devices["telescope"].rig.tracking)

    def rel(self, ts: list[float], origin: float) -> list[float]:
        return [round(t - origin, 1) for t in ts]


def _lst_h(t: float, lon: float = LON) -> float:
    return schedule.hour_angle_h(0.0, lon, t) % 24.0


def _ra_at(ha_h: float, t: float, lon: float = LON) -> float:
    """The RA whose hour angle is ``ha_h`` at ``t`` (HA = LST - RA)."""
    return (_lst_h(t, lon) - ha_h) % 24.0


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _t0_at_second(second: float) -> float:
    """A fake night start ``second`` seconds past the current minute.

    `_hhmm` truncates to the minute, so a boundary written as
    ``_hhmm(t0 + x)`` falls ``second`` seconds short of ``t0 + x``, and
    everything the engine does on its own grid from ``t0`` meets it at a
    phase set by ``t0``'s second. Pinning the second pins that phase. Every
    time zone's offset is a whole number of minutes, so the local second and
    the epoch second agree."""
    return (time.time() // 60.0) * 60.0 + float(second)


def _target(name: str, ra: float, dec: float, *,
            exposure_s: float = EXPOSURE_S, **sched) -> Target:
    t = Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=exposure_s, gain=100,
                                   count=1)])
    for k, v in sched.items():
        setattr(t.schedule, k, v)
    return t


def _plan(*targets: Target, flip: bool = False) -> SequencePlan:
    return SequencePlan(name="idle", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=flip,
                        safety_check=False, targets=list(targets))


def _constraint_waiter(name: str, t0: float, *, lon: float = LON,
                       ready_after_s: float | None = None) -> Target:
    """A target held by its hour-angle constraint: `constraint_gate` nulls its
    ``start_ts``, so it rides the scheduler's 5 s else-branch. Waits two hours
    unless ``ready_after_s`` says when its window opens."""
    lim = 1.0
    if ready_after_s is None:
        ra = _ra_at(-3.0, t0, lon)
    else:
        ra = (_lst_h(t0 + ready_after_s, lon) + lim) % 24.0
    return _target(name, ra, 40.0, max_hour_angle_h=lim)


def _unset_the_site(store, monkeypatch) -> None:
    """The 0,0 default with ``is_default`` set, as a fresh install has it.
    ``ConfigStore.set_site`` always clears the flag (a saved site is by
    definition not the default), so it is put back on the live object."""
    store.set_site(Site(name="Unset", latitude=0.0, longitude=0.0))
    monkeypatch.setattr(store.cfg().site, "is_default", True)


def _park_lines(lines) -> list[str]:
    return [m for _lvl, m, _src in lines if "stopping tracking" in m]


def _unconfirmed_lines(lines) -> list[str]:
    return [m for lvl, m, _src in lines
            if lvl == "warning" and "did not confirm the stop" in m]


def _a_mount_that_will_not_stop(run: _Clocked, *, readback: str) -> None:
    """A dead-ish link, the #133 class: ``set_tracking(False)`` hangs for its
    whole bound, ``MOUNT_QUERY_TIMEOUT_S`` of fake time, times out, and the
    mount goes on tracking. ``readback`` is what a read of it then says:
    "still tracking" (True, at once) or "unreadable" (the read hangs for its
    bound too and times out, so None). ``set_tracking(True)`` still works.

    Every stop attempt is still recorded in ``run.tracking_off``. Once the run
    is frozen at the horizon every call passes through, so the wind-down after
    the test's abort is not graded and cannot trip over the double."""
    run.stop_hangs = True
    run.read_hangs = readback == "unreadable"


# ------------------------------------------------ the two waits that never tore down

async def test_an_eta0_wait_park_holds_on_the_idle_clock(sim_hub, monkeypatch,
                                                         bus_lines):
    """Alpha shoots and completes. Bravo is below its 30 degree gate, setting,
    with its window open, so `gating_status` answers eta 0 and the scheduler
    re-evaluates every 5 s. The mount must stop tracking Alpha within
    WAIT_TEARDOWN_S plus one tick of Alpha's last exposure.

    Mutant "teardown keyed on wait_ts - now" (check 1 deleted from
    `_idle_hold_reason`, which leaves only the scheduler's planned-wait rule):
    RED -
        AssertionError: the mount kept tracking Alpha, unwatched, for the
        whole 870 s after its last exposure; set_tracking(False) at [] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=900.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)      # high, rising, flip hours off
    b = _target("Bravo", _ra_at(+4.0, t0), 0.0, min_altitude_deg=30.0,
                start_mode="time", start_time=_hhmm(t0 - 2 * 3600),
                stop_mode="time", stop_time=_hhmm(t0 + 3 * 3600))
    win = schedule.resolve_window(b.schedule, sim_hub.site, -12.0, t0)
    gs = schedule.gating_status(b, sim_hub.site, -12.0, t0 + 60.0, window=win)
    assert (gs["state"], gs["eta_s"]) == ("waiting", 0.0) and gs["start_ts"] < t0, (
        f"premise: Bravo must be the eta-0 shape (window open, below its gate, "
        f"no crossing ahead): {gs}")

    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, (
            f"the mount kept tracking Alpha, unwatched, for the whole "
            f"{run.horizon - idle:.0f} s after its last exposure; "
            f"set_tracking(False) at {run.rel(run.tracking_off, idle)} s")
        assert TEARDOWN <= offs[0] - idle <= TEARDOWN + TICK, (
            f"park-held {offs[0] - idle:.1f} s after the last exposure; the "
            f"idle clock is {TEARDOWN:.0f} s plus one {TICK:.0f} s tick")
        assert run.tracking() is False
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Alpha" in lines[0], lines
    finally:
        await run.close()


async def test_a_constraint_wait_park_holds_on_the_idle_clock(sim_hub, monkeypatch,
                                                              bus_lines):
    """The same, with Bravo held by an hour-angle constraint: `start_ts` is None,
    so the scheduler takes its 5 s else-branch, which had no teardown at all.

    Mutant "teardown keyed on wait_ts - now" (check 1 deleted): RED -
        AssertionError: the mount kept tracking Alpha through a constraint
        wait for the whole 870 s after its last exposure; set_tracking(False)
        at [] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=900.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    gs = schedule.gating_status(b, sim_hub.site, -12.0, t0 + 60.0,
                                window=(t0, None))
    assert gs["state"] == "waiting" and gs["start_ts"] is None, (
        f"premise: Bravo must be a constraint wait (start_ts nulled): {gs}")

    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, (
            f"the mount kept tracking Alpha through a constraint wait for the "
            f"whole {run.horizon - idle:.0f} s after its last exposure; "
            f"set_tracking(False) at {run.rel(run.tracking_off, idle)} s")
        assert TEARDOWN <= offs[0] - idle <= TEARDOWN + TICK, (
            f"park-held {offs[0] - idle:.1f} s after the last exposure")
        assert run.tracking() is False
    finally:
        await run.close()


# --------------------------------------------- the floor and the flip, before 120 s

async def test_a_target_sinking_through_the_floor_is_park_held_at_that_tick(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Alpha sets through the mount's altitude floor about 40 s into the wait.
    It is park-held at the first tick after it crosses, long before the idle
    clock would have done it, and the log line carries words, not an altitude.

    Mutant "no floor check" (check 2 deleted from `_idle_hold_reason`): RED -
        AssertionError: Alpha sank through the floor 40.0 s into the wait but
        was park-held at 120.0 s: the idle clock caught it, the floor did not
    Mutant "altitude in the floor sentence" (the reason returns the slew
    gate's sentence, verdict[1]): RED - the numbers move with the hour the
    suite runs -
        AssertionError: the park-hold line carries a number: 'target Alpha
        altitude 32° below safety floor 32° (az 238°) — stopping tracking
        until the next target is set up'
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    exp = 150.0
    a = _target("Alpha", _ra_at(+3.0, t0), 0.0, exposure_s=exp)   # west, setting
    t_cross = t0 + exp + 40.0
    floor = altaz(a.ra_hours, a.dec_deg, LAT, LON, t_cross)[0]
    # The slew gate projects SLEW_PROJECT_S ahead; Alpha must clear it at the
    # slew and still cross inside the idle clock, or this tests nothing.
    assert altaz(a.ra_hours, a.dec_deg, LAT, LON,
                 t0 + engine_mod.SLEW_PROJECT_S + 5.0)[0] > floor, "premise"
    temp_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=floor))
    b = _constraint_waiter("Bravo", t0)

    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        assert 20.0 < t_cross - idle < TEARDOWN - 40.0, (
            f"premise: the crossing must fall well inside the idle clock "
            f"({t_cross - idle:.1f} s after the last exposure)")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "Alpha was never park-held"
        assert t_cross <= offs[0] <= t_cross + TICK + 0.5, (
            f"Alpha sank through the floor {t_cross - idle:.1f} s into the "
            f"wait but was park-held at {offs[0] - idle:.1f} s: "
            + ("the idle clock caught it, the floor did not"
               if offs[0] - idle >= TEARDOWN else "not at the crossing tick"))
        lines = _park_lines(bus_lines)
        assert len(lines) == 1, lines
        assert not re.search(r"\d", lines[0]), (
            f"the park-hold line carries a number: {lines[0]!r}")
        assert "floor" in lines[0], lines[0]
    finally:
        await run.close()


def _ceiling_run(sim_hub, temp_store, monkeypatch, *, cross_after_idle_s):
    """Alpha rises through the zenith keep-out ``cross_after_idle_s`` after its
    150 s exposure ends, and Bravo waits two hours on a constraint. The
    ceiling is set to Alpha's own altitude at that moment, so the crossing is
    exact whatever hour the suite runs at."""
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    exp = 150.0
    a = _target("Alpha", _ra_at(-3.0, t0), 0.0, exposure_s=exp)   # east, rising
    t_cross = t0 + exp + cross_after_idle_s
    ceiling = altaz(a.ra_hours, a.dec_deg, LAT, LON, t_cross)[0]
    # The slew gate judges the HIGHEST altitude across SLEW_PROJECT_S; Alpha
    # must clear it at the slew, or the run refuses it and this tests nothing.
    assert altaz(a.ra_hours, a.dec_deg, LAT, LON,
                 t0 + engine_mod.SLEW_PROJECT_S + 5.0)[0] < ceiling, "premise"
    temp_store.set_safety(SafetyConfig(enabled=False, max_alt_deg=ceiling))
    return run, a, _constraint_waiter("Bravo", t0), t_cross


async def test_a_target_rising_into_the_zenith_keep_out_is_park_held_at_that_tick(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The predicate the idle check asks has two ends. Alpha rises into the
    mount's zenith keep-out (``safety.max_alt_deg``) 40 s into the wait, and is
    park-held at the first tick after it crosses, as a target sinking through
    the floor is. A mount tracking on up there reaches its own tripod (#101).
    Words in the line, no altitude.

    Mutant "floor only" (the idle check acts on a "floor" verdict and ignores
    "ceiling", as S0 shipped): RED -
        AssertionError: Alpha rose into the zenith keep-out 40.0 s into the
        wait but was park-held at 120.0 s: the idle clock caught it, the
        ceiling did not
    Mutant "the idle check projects" (see the control below): RED -
        AssertionError: Alpha rose into the zenith keep-out 40.0 s into the
        wait but was park-held at 0.0 s: not at the crossing tick
    """
    run, a, b, t_cross = _ceiling_run(sim_hub, temp_store, monkeypatch,
                                      cross_after_idle_s=40.0)
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        assert 20.0 < t_cross - idle < TEARDOWN - 40.0, (
            f"premise: the crossing must fall well inside the idle clock "
            f"({t_cross - idle:.1f} s after the last exposure)")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "Alpha was never park-held"
        assert t_cross <= offs[0] <= t_cross + TICK + 0.5, (
            f"Alpha rose into the zenith keep-out {t_cross - idle:.1f} s into "
            f"the wait but was park-held at {offs[0] - idle:.1f} s: "
            + ("the idle clock caught it, the ceiling did not"
               if offs[0] - idle >= TEARDOWN else "not at the crossing tick"))
        lines = _park_lines(bus_lines)
        assert len(lines) == 1, lines
        assert not re.search(r"\d", lines[0]), (
            f"the park-hold line carries a number: {lines[0]!r}")
        assert "zenith keep-out" in lines[0] and "Alpha" in lines[0], lines[0]
    finally:
        await run.close()


async def test_control_a_target_below_its_ceiling_is_not_held_early(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same rising Alpha, with the keep-out set where Alpha will
    only reach it 30 s AFTER the idle clock runs out. Until then it is below
    the ceiling, and only the idle clock stops it, at WAIT_TEARDOWN_S.

    Mutant "the idle check projects" (``projected=True``, the slew gate's
    look SLEW_PROJECT_S ahead, in `_idle_hold_reason`): RED -
        AssertionError: park-held 0.0 s into the wait, before the idle clock,
        with Alpha still below its ceiling: ["Alpha has risen into the
        mount's zenith keep-out — stopping tracking until the next target is
        set up"]
    """
    run, a, b, t_cross = _ceiling_run(sim_hub, temp_store, monkeypatch,
                                      cross_after_idle_s=TEARDOWN + 30.0)
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        assert t_cross - idle > TEARDOWN + TICK, "premise: crosses late"
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "premise: the idle clock still has to stop it"
        assert offs[0] - idle >= TEARDOWN, (
            f"park-held {offs[0] - idle:.1f} s into the wait, before the idle "
            f"clock, with Alpha still below its ceiling: "
            f"{_park_lines(bus_lines)}")
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "nothing has been shot" in lines[0], lines
    finally:
        await run.close()


def _flip_run(sim_hub, monkeypatch, *, lon: float = LON):
    """Alpha reaches its flip point 75 s after its exposure ends (the frame
    loop's own flip window is exposure + 30 s + 12 s, so it leaves the flip
    owed), and Bravo waits two hours on a constraint."""
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    lead_h = schedule.MERIDIAN_FLIP_LEAD_MIN / 60.0
    t_flip = t0 + EXPOSURE_S + 75.0
    a = _target("Alpha", (_lst_h(t_flip, lon) + lead_h) % 24.0, 20.0)
    b = _constraint_waiter("Bravo", t0, lon=lon)
    return run, a, b, t_flip


async def test_a_target_reaching_its_flip_point_is_park_held_at_that_tick(
        sim_hub, monkeypatch, bus_lines):
    """Alpha was left owing a meridian flip. It is park-held by the tick at
    which it reaches the plan's flip point (the check looks one tick ahead),
    not two minutes later on the idle clock.

    Mutant "no flip check" (check 3 deleted from `_idle_hold_reason`): RED -
        AssertionError: Alpha reached its flip point 75.0 s into the wait but
        was park-held at 120.0 s
    """
    run, a, b, t_flip = _flip_run(sim_hub, monkeypatch)
    try:
        await run.night(_plan(a, b, flip=True))
        idle = run.exposure_end("Alpha")
        assert abs((t_flip - idle) - 75.0) < 1.0, (
            f"premise: the flip point is 75 s after the exposure "
            f"({t_flip - idle:.1f})")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "Alpha was never park-held"
        assert t_flip - TICK - 0.5 <= offs[0] <= t_flip + 0.5, (
            f"Alpha reached its flip point {t_flip - idle:.1f} s into the wait "
            f"but was park-held at {offs[0] - idle:.1f} s")
        assert len([s for s in run.slews if s < offs[0]]) == 1, (
            f"premise: the frame loop must have left the flip owed, not taken "
            f"it: slews at {run.rel(run.slews, run.t0)}")
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "flip point" in lines[0], lines
        assert not re.search(r"\d", lines[0]), lines[0]
    finally:
        await run.close()


@pytest.mark.parametrize("taught", ["mount cannot flip early", "early flip was a no-op"])
async def test_the_flip_point_is_the_plans_lead_not_the_learned_one(
        sim_hub, monkeypatch, bus_lines, taught):
    """Spec 5.1 check 3 is "the plan's flip point (`plan.meridian_flip_lead_min`
    before transit)", and 5.7 says why never the learned lead: `_flip_lead_s`
    drops to 0 once the mount has shown it cannot flip early (#127), or once
    this target's early flip changed nothing - right for when to ATTEMPT a
    flip, wrong as the edge of safe tracking, because the AM5 that teaches it
    stops tracking 4.7 to 7.6 min BEFORE transit. With either lesson learned,
    Alpha must still be park-held at the plan's flip point, not left tracking
    towards transit until the idle clock runs out.

    Mutant "use `_flip_lead_s`" (``_idle_flip_due`` reads
    ``self._flip_lead_s(target)``, as it first shipped): RED, both cases -
        AssertionError: with the learned lead at zero (mount cannot flip
        early) Alpha reached the plan's flip point 75.0 s into the wait but
        was park-held at 120.0 s
        AssertionError: with the learned lead at zero (early flip was a
        no-op) Alpha reached the plan's flip point 75.0 s into the wait but
        was park-held at 120.0 s
    """
    run, a, b, t_flip = _flip_run(sim_hub, monkeypatch)
    if taught == "mount cannot flip early":
        name = run.engine._mount_name()
        assert name, "premise: the learned trait is keyed on the mount's name"
        run.engine._flips_early_by_mount[name] = False
    else:
        # `start()` clears `_flip_no_op`, so the lesson is re-taught the moment
        # the run first asks for the learned lead - before Alpha is idle.
        real_lead = run.engine._flip_lead_s

        def lead_after_a_no_op(target=None):
            run.engine._flip_no_op.add(a.id)
            return real_lead(target)

        monkeypatch.setattr(run.engine, "_flip_lead_s", lead_after_a_no_op)
    try:
        await run.night(_plan(a, b, flip=True))
        assert run.engine._flip_lead_s(a) == 0.0, (
            f"premise: the learned lead must be zero ({taught})")
        assert run.engine._plan_flip_lead_s() == \
            schedule.MERIDIAN_FLIP_LEAD_MIN * 60.0, "premise: the plan's lead"
        idle = run.exposure_end("Alpha")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "Alpha was never park-held"
        assert t_flip - TICK - 0.5 <= offs[0] <= t_flip + 0.5, (
            f"with the learned lead at zero ({taught}) Alpha reached the "
            f"plan's flip point {t_flip - idle:.1f} s into the wait but was "
            f"park-held at {offs[0] - idle:.1f} s")
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "flip point" in lines[0], lines
    finally:
        await run.close()


async def test_control_a_flip_the_mount_can_track_through_is_not_park_held(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. `schedule.flip_can_be_skipped` is the one exemption, and it is
    False for every mount today (schedule.py says why), so the control supplies
    the answer. Alpha is then left alone at its flip point; only the idle clock
    stops it, at WAIT_TEARDOWN_S.

    Mutant "exemption ignored" (`_idle_flip_due` returns True without asking
    `flip_can_be_skipped`): RED -
        AssertionError: park-held at 75.0 s, before the idle clock, although
        the mount can track Alpha through the meridian
    """
    asked: list[tuple[float, float, float, str]] = []

    def can_skip(dec_deg, lat_deg, pier_side, *a, **kw):
        asked.append((run.clock.t, dec_deg, lat_deg, pier_side))
        return True

    run, a, b, t_flip = _flip_run(sim_hub, monkeypatch)
    monkeypatch.setattr(engine_mod.schedule, "flip_can_be_skipped", can_skip)
    try:
        await run.night(_plan(a, b, flip=True))
        idle = run.exposure_end("Alpha")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "premise: the idle clock still has to stop it"
        assert offs[0] - idle >= TEARDOWN, (
            f"park-held at {offs[0] - idle:.1f} s, before the idle clock, "
            f"although the mount can track Alpha through the meridian")
        idle_asks = [q for q in asked if q[0] >= idle]
        assert idle_asks, (
            "premise: the idle check never asked the exemption, so this "
            "control proves nothing")
        _t, dec, lat, _side = idle_asks[0]
        assert (dec, lat) == (a.dec_deg, LAT), idle_asks[0]
    finally:
        await run.close()


# ------------------------------------------------------------------ the latch

async def test_the_latch_park_holds_once_per_idle_spell(sim_hub, monkeypatch,
                                                        bus_lines):
    """Alpha shoots; Bravo's window opens ten minutes later; Charlie waits two
    hours. Two idle spells, a `_setup_target` between them: exactly one
    set_tracking(False) in each, however many 5 s ticks each spell lasts.

    Mutant "no latch" (`_idle_park_hold` never closes it): RED -
        AssertionError: 240 set_tracking(False) calls for 2 idle spells, at
        [120.0, 125.0, 130.0] ... s
    Mutant "latch never reset" (`_setup_target` does not re-open it): RED -
        AssertionError: 1 set_tracking(False) calls for 2 idle spells, at
        [120.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=1500.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0, ready_after_s=600.0)
    c = _constraint_waiter("Charlie", t0)

    try:
        await run.night(_plan(a, b, c))
        idle_a = run.exposure_end("Alpha")
        idle_b = run.exposure_end("Bravo")
        assert idle_b - idle_a > 2 * TEARDOWN, "premise: two separate spells"
        # In fake seconds, not ticks: a stop the read-back confirms spends its
        # confirm probes, so under the defect the tick count shrinks with it.
        assert run.horizon - idle_a > 100 * TICK, "premise: long idle spells"
        offs = run.tracking_off
        assert len(offs) == 2, (
            f"{len(offs)} set_tracking(False) calls for 2 idle spells, at "
            f"{run.rel(offs, idle_a)[:3]}{' ...' if len(offs) > 3 else ''} s")
        assert TEARDOWN <= offs[0] - idle_a <= TEARDOWN + TICK
        assert TEARDOWN <= offs[1] - idle_b <= TEARDOWN + TICK
        assert len(_park_lines(bus_lines)) == 2
    finally:
        await run.close()


# ------------------------------------------------- a stop the mount did not take

def _asks(run: _Clocked) -> list[tuple[float, str]]:
    """(fake time, which engine task) of every set_tracking(False)."""
    return [(t, who) for t, on, who in run.tracking_calls if not on]


@pytest.mark.parametrize("readback", ["still tracking", "unreadable"])
async def test_a_stop_the_mount_did_not_take_is_asked_again_on_its_own_clock(
        sim_hub, monkeypatch, bus_lines, readback):
    """`_park_hold` swallows every failure - it has to, it also serves the
    safety pause - so the idle park-hold used to close its latch on a stop
    that never happened and never look again: the mount tracked on,
    unwatched, for the rest of the wait. Here ``set_tracking(False)`` hangs
    for its bound and times out, and the mount goes on tracking. The
    idle-stop task must read tracking back, find it not False, and ask
    again IDLE_STOP_RETRY_S after each attempt ends, for the rest of the
    spell. An unreadable read-back counts the same way: a dead serial link
    (the #133 class) cannot confirm a stop.

    ON ITS OWN CLOCK (#189 A3, #216): every ask comes from the idle-stop
    task, the first one included, and two asks are exactly the retry
    interval plus the last attempt's own hang apart (one mount timeout for
    the stop, one more when the read-back hangs too), which is what the
    engine promises: never less than the interval, and nothing added to it.
    This case used to pin the first ask on the run task, ``["run"] +
    ["retry"] * n``: that was the first attempt made inline, the #216
    defect, and test_idle_stop_retry_clock's case (a) now grades what it
    cost.

    SAID ONCE. One warning per idle spell, and the "stopping tracking" line
    once too, however many times it retries.

    Mutant "no readback" (the `_tracking_now` read-back after `_park_hold`
    in `_idle_stop_retry` deleted, so the first attempt is taken on trust):
    RED, both cases (observed; this and the next two re-run after #216 with
    the texts unchanged) -
        AssertionError: a stop the mount did not take (still tracking) was
        asked 1 time(s) in a 450 s spell: set_tracking(False) at [120.0] s
        AssertionError: a stop the mount did not take (unreadable) was
        asked 1 time(s) in a 420 s spell: set_tracking(False) at [120.0] s
    Mutant "unreadable counts as stopped" (``if not tracking: return`` in
    place of ``if tracking is False: return``): RED, the unreadable case
    only (observed) -
        AssertionError: a stop the mount did not take (unreadable) was
        asked 1 time(s) in a 420 s spell: set_tracking(False) at [120.0] s
    Mutant "the retry says it again" (`_idle_stop_retry` logs the
    unconfirmed-stop warning after each failed read-back): RED, both cases
    (observed) -
        AssertionError: 5 unconfirmed-stop warnings across 5 attempts
        AssertionError: 3 unconfirmed-stop warnings across 4 attempts
    (the unreadable case's last retry is still hanging on its read-back
    when the horizon stops the clock, so it has not said it yet).
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    _a_mount_that_will_not_stop(run, readback=readback)
    hang = HANG if readback == "still tracking" else 2 * HANG
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        asks = [(t, who) for t, who in _asks(run) if t >= idle]
        offs = [t for t, _who in asks]
        assert offs and TEARDOWN <= offs[0] - idle <= TEARDOWN + TICK, (
            f"premise: the first stop comes on the idle clock: "
            f"{run.rel(offs, idle)[:3]} s")
        spell = run.horizon - offs[0]
        assert spell > 3 * (RETRY + hang), f"premise: a long spell ({spell})"
        assert len(offs) > 1, (
            f"a stop the mount did not take ({readback}) was asked "
            f"{len(offs)} time(s) in a {spell:.0f} s spell: "
            f"set_tracking(False) at {run.rel(offs, idle)[:5]} s")
        assert [who for _t, who in asks] == ["retry"] * len(asks), (
            f"every ask is the idle-stop task's, the first one included "
            f"(#216): {asks}")
        gaps = sorted({round(y - x, 1) for x, y in zip(offs, offs[1:])})
        assert gaps == [RETRY + hang], (
            f"not the retry interval plus the last attempt's hang "
            f"({RETRY + hang:.0f} s): gaps {gaps}")
        assert run.horizon - offs[-1] <= RETRY + hang, (
            f"the retry gave up: last ask {run.horizon - offs[-1]:.0f} s "
            f"before the horizon")
        assert run.tracking() is True, "premise: the double kept it tracking"
        warned = _unconfirmed_lines(bus_lines)
        assert len(warned) == 1, (
            f"{len(warned)} unconfirmed-stop warnings across {len(offs)} "
            f"attempts")
        assert not re.search(r"\d", warned[0]), warned[0]
        assert len(_park_lines(bus_lines)) == 1, _park_lines(bus_lines)[:3]
    finally:
        await run.close()


async def test_a_planned_wait_stop_that_did_not_take_is_asked_again_a_minute_later(
        sim_hub, monkeypatch, bus_lines):
    """The scheduler's planned-wait rule decides the stop a moment after the
    last exposure: Charlie is three hours away. The mount does not take it.
    The retry must ask again one retry interval after that attempt ends,
    although the idle clock has not run out and would have no reason of its
    own until WAIT_TEARDOWN_S: the retry asks again for a stop already
    decided, it does not decide it a second time.

    Mutant "retry only when a reason holds" (`_idle_stop_retry` skips an ask
    while `_idle_hold_reason` has no reason for the tracked target): RED
    (observed) -
        AssertionError: the planned-wait stop was not taken and the next
        ask came at 150.0 s, not one interval after the first attempt ended
        (90.0 s): set_tracking(False) at [0.0, 150.0, 240.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    c = _target("Charlie", _ra_at(-3.0, t0), 40.0,
                start_mode="time", start_time=_hhmm(t0 + 3 * 3600))
    _a_mount_that_will_not_stop(run, readback="still tracking")
    try:
        await run.night(_plan(a, c))
        idle = run.exposure_end("Alpha")
        assert "waiting for Charlie" in (run.engine.state.get("detail") or ""), (
            f"premise: the scheduler reached Charlie's long planned wait: "
            f"{run.engine.state.get('detail')!r}")
        offs = [t for t, _who in _asks(run) if t >= idle]
        assert offs and offs[0] - idle < TICK, (
            f"premise: the planned-wait rule stopped it at once: "
            f"{run.rel(offs, idle)[:3]} s")
        want = offs[0] + HANG + RETRY
        assert len(offs) > 1 and abs(offs[1] - want) < 0.5, (
            f"the planned-wait stop was not taken and the next ask came at "
            f"{offs[1] - idle if len(offs) > 1 else None} s, not one interval "
            f"after the first attempt ended ({want - idle:.1f} s): "
            f"set_tracking(False) at {run.rel(offs, idle)[:3]} s")
        assert len(_unconfirmed_lines(bus_lines)) == 1
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "long wait" in lines[0], lines[:3]
    finally:
        await run.close()


async def test_a_first_wait_stop_that_did_not_take_is_asked_again_a_minute_later(
        sim_hub, monkeypatch, bus_lines):
    """The run OPENS on a long planned wait: Charlie, its only target, is three
    hours away, so the scheduler's planned-wait rule stops the mount before
    anything was set up. The mount does not take it. The warning promises to
    ask again about once a minute, and nothing is tracked, so the retry must
    not hang off the tracked-target guard: that guard decides whether a new
    stop is decided, not whether one already in flight is asked again.

    Mutant "retry only with a tracked target" (`_idle_stop_retry`'s retry
    loop returns while ``_tracked_target`` is None): RED - one ask across
    the whole wait, under a warning promising more (observed, and again
    after #216 moved the first attempt onto the task, same text) -
        AssertionError: the first wait's unconfirmed stop was asked 1
        time(s) in a 600 s wait, under a warning promising about once a
        minute: set_tracking(False) at [0.0] s
    The CONTROL is `test_no_tracked_target_no_idle_park_hold`: with nothing
    tracked, a first wait still decides no stop of its own.
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    c = _target("Charlie", _ra_at(-3.0, t0), 40.0,
                start_mode="time", start_time=_hhmm(t0 + 3 * 3600))
    _a_mount_that_will_not_stop(run, readback="still tracking")
    try:
        await run.night(_plan(c))
        assert "waiting for Charlie" in (run.engine.state.get("detail") or ""), (
            f"premise: the run opened on Charlie's long planned wait: "
            f"{run.engine.state.get('detail')!r}")
        assert run.slews == [] and run.engine._tracked_target is None, (
            "premise: nothing was acquired, so nothing is tracked")
        offs = [t for t, _who in _asks(run)]
        assert offs and offs[0] - t0 < TICK, (
            f"premise: the planned-wait rule stopped it at once: "
            f"{run.rel(offs, t0)[:3]} s")
        warned = _unconfirmed_lines(bus_lines)
        assert warned and "about once a minute" in warned[0], (
            f"premise: the warning makes the promise: {warned}")
        assert len(offs) > 3, (
            f"the first wait's unconfirmed stop was asked {len(offs)} time(s) "
            f"in a {run.horizon - t0:.0f} s wait, under a warning promising "
            f"about once a minute: set_tracking(False) at "
            f"{run.rel(offs, t0)[:5]} s")
        gaps = sorted({round(y - x, 1) for x, y in zip(offs, offs[1:])})
        assert gaps == [RETRY + HANG], f"not on the retry clock: gaps {gaps}"
        assert len(warned) == 1
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "long wait" in lines[0], lines[:3]
    finally:
        await run.close()


async def test_the_unconfirmed_stop_is_said_once_per_idle_spell(
        sim_hub, monkeypatch, bus_lines):
    """Two idle spells with a `_setup_target` between them, and a mount that
    never takes the stop: one warning in EACH spell. The flag that limits it
    is re-armed with the latch, so the second spell's operator is told too.

    Mutant "the warning flag is never re-armed" (`_setup_target` re-opens
    the latch but leaves ``_idle_hold_retrying`` set): RED (observed) -
        AssertionError: 1 unconfirmed-stop warning(s) for 2 idle spells in
        which the mount never stopped
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=1500.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0, ready_after_s=600.0)
    c = _constraint_waiter("Charlie", t0)
    _a_mount_that_will_not_stop(run, readback="still tracking")
    try:
        await run.night(_plan(a, b, c))
        idle_a, idle_b = run.exposure_end("Alpha"), run.exposure_end("Bravo")
        assert idle_b - idle_a > 2 * TEARDOWN, "premise: two separate spells"
        in_a = [t for t in run.tracking_off if idle_a <= t < idle_b]
        in_b = [t for t in run.tracking_off if t >= idle_b]
        assert len(in_a) > 3 and len(in_b) > 3, (
            f"premise: retried in both spells ({len(in_a)}, {len(in_b)})")
        warned = _unconfirmed_lines(bus_lines)
        assert len(warned) == 2, (
            f"{len(warned)} unconfirmed-stop warning(s) for 2 idle spells in "
            f"which the mount never stopped")
    finally:
        await run.close()


async def test_control_a_mount_that_stops_is_asked_once_and_not_warned_about(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. The simulator's mount takes the stop, and the read-back
    confirms it: one set_tracking(False) across the whole idle spell, no
    unconfirmed-stop warning, and the latch stays closed.

    Mutant "re-open the latch whatever the read-back says" (the latch
    re-opened before the read-back is looked at): RED -
        AssertionError: a mount that stopped was asked 90 time(s) in one
        idle spell, at [120.0, 125.0, 130.0, 135.0] s
    Mutant "start the retry whatever the read-back says" (the retry task
    created before the read-back is looked at, #189 A3): RED (observed) -
        AssertionError: a mount that stopped was asked 2 time(s) in one idle
        spell, at [120.0, 180.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        # In fake seconds, not ticks: every confirmed stop spends the
        # read-back's confirm probes, so a tick count moves with the defect.
        assert run.horizon - idle > TEARDOWN + 50 * TICK, (
            "premise: a long idle spell")
        offs = [t for t in run.tracking_off if t >= idle]
        assert len(offs) == 1, (
            f"a mount that stopped was asked {len(offs)} time(s) in one idle "
            f"spell, at {run.rel(offs, idle)[:4]} s")
        assert _unconfirmed_lines(bus_lines) == []
        assert run.engine._idle_hold_open is False
        assert run.tracking() is False
    finally:
        await run.close()


async def test_with_no_telescope_nothing_is_read_back(sim_hub, bus_lines):
    """A camera-only rig still passes through the scheduler's planned-wait
    rule. There is no mount to stop and none to read back, so the latch closes
    and nothing warns about a stop nobody could have confirmed.

    Mutant "read back without a mount" (the no-telescope return deleted, so
    `_tracking_now` answers None for the missing mount): RED -
        AssertionError: a rig with no mount was warned about a stop it could
        not confirm: ['the mount did not confirm the stop (its tracking state
        cannot be read) — asking again about once a minute until it does']
    """
    e = SequenceEngine(sim_hub)
    e.plan = _plan()
    tel = sim_hub.devices.pop("telescope")
    try:
        await e._idle_park_hold("the next target is a long wait away")
        assert _unconfirmed_lines(bus_lines) == [], (
            f"a rig with no mount was warned about a stop it could not "
            f"confirm: {_unconfirmed_lines(bus_lines)}")
        assert e._idle_hold_open is False
        assert len(_park_lines(bus_lines)) == 1, bus_lines
    finally:
        sim_hub.devices["telescope"] = tel


async def test_the_planned_wait_rule_spends_the_same_latch(sim_hub, monkeypatch,
                                                          bus_lines):
    """The scheduler's own rule - a planned wait longer than WAIT_TEARDOWN_S
    park-holds at once - stays, and goes through the same latch. Alpha shoots;
    Bravo's eta-0 wait lets the idle clock park-hold; Bravo's window then
    closes and the scheduler turns to Charlie, three hours away, which is the
    planned-wait rule's case. Same idle spell, so no second park-hold.

    Mutant "the planned-wait rule bypasses the latch" (the scheduler calls
    `_park_hold` directly, as it used to): RED - the second time moves with
    the minute Bravo's HH:MM stop truncates to -
        AssertionError: 2 park-holds in one idle spell, at [120.0, 520.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=900.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _target("Bravo", _ra_at(+4.0, t0), 0.0, min_altitude_deg=30.0,
                start_mode="time", start_time=_hhmm(t0 - 2 * 3600),
                stop_mode="time", stop_time=_hhmm(t0 + 600))
    c = _target("Charlie", _ra_at(-3.0, t0), 40.0,
                start_mode="time", start_time=_hhmm(t0 + 3 * 3600))
    try:
        await run.night(_plan(a, b, c))
        idle = run.exposure_end("Alpha")
        assert "waiting for Charlie" in (run.engine.state.get("detail") or ""), (
            f"premise: the scheduler must reach Charlie's long planned wait: "
            f"{run.engine.state.get('detail')!r}")
        assert len(run.tracking_off) == 1, (
            f"{len(run.tracking_off)} park-holds in one idle spell, at "
            f"{run.rel(run.tracking_off, idle)} s")
        assert TEARDOWN <= run.tracking_off[0] - idle <= TEARDOWN + TICK
    finally:
        await run.close()


async def test_setup_time_is_not_idle_time(sim_hub, monkeypatch, bus_lines):
    """The idle clock starts again when `_setup_target` COMPLETES, not only at
    the slew: an initial focus sweep and a guider start are the engine at work
    on the target, not the mount left alone. Alpha's one-minute window closes
    during a 90 s sweep, so it shoots nothing and the per-exposure anchor never
    moves; the park-hold must then come WAIT_TEARDOWN_S after the sweep, which
    only the end-of-setup anchor can give.

    Mutant "no end-of-setup anchor" (the ``self._idle_since = time.time()``
    beside ``_last_frame_at`` at the end of `_setup_target` deleted, so the
    clock runs from the slew): RED -
        AssertionError: park-held 30.0 s after setup completed (120.0 s after
        the slew): the focus sweep was charged to the idle clock
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    sweep_s = 90.0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0, max_run_min=1)
    a.autofocus_first = True
    b = _constraint_waiter("Bravo", t0)
    swept: list[float] = []

    async def sweep(*_a, **_kw):
        # A focus sweep's worth of fake time, and nothing else.
        run.clock.t += sweep_s
        swept.append(run.clock.t)

    monkeypatch.setattr(run.engine, "_autofocus", sweep)
    assert "focuser" in sim_hub.devices, "premise: the sweep needs a focuser"
    try:
        await run.night(_plan(a, b))
        assert len(run.slews) == 1 and swept, (
            f"premise: Alpha was slewed to and swept: slews "
            f"{run.rel(run.slews, t0)}, sweep ended {run.rel(swept, t0)}")
        assert run.exposure_ends == [], (
            f"premise: Alpha's window must close during the sweep, so no "
            f"exposure moves the idle clock: {run.exposure_ends}")
        setup_end, slewed = swept[0], run.slews[0]
        offs = [t for t in run.tracking_off if t >= slewed]
        assert offs, "Alpha was never park-held"
        assert TEARDOWN <= offs[0] - setup_end <= TEARDOWN + TICK, (
            f"park-held {offs[0] - setup_end:.1f} s after setup completed "
            f"({offs[0] - slewed:.1f} s after the slew): the focus sweep was "
            f"charged to the idle clock")
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Alpha" in lines[0], lines
    finally:
        await run.close()


async def test_a_setup_that_gives_up_after_the_slew_leaves_the_mount_watched(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A setup can move the mount and then give up on the target. Here guiding
    is required with action "skip" and the guider will not start, so
    `_setup_target` raises StopTarget AFTER its slew left the mount tracking
    Alpha. The idle clock has to be watching Alpha from that slew, because
    nothing else is: the next target waits on a constraint, and that wait has
    no teardown of its own.

    Mutant "tracked at the end of setup" (the tracked target and the latch set
    beside the end-of-setup anchor instead of right after the slew, so a setup
    that raises never reaches them): RED -
        AssertionError: a setup that slewed to Alpha and gave up left the
        mount tracking it, unwatched, for the whole 600 s; set_tracking(False)
        at [] s
    """
    temp_store.set_escalation(EscalationConfig(require_guiding=True,
                                               guiding_action="skip"))
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)

    async def will_not_start(*_a, **_kw):
        raise RuntimeError("the guider would not start")

    monkeypatch.setattr(sim_hub.guider, "start_guiding", will_not_start)
    plan = SequencePlan(name="idle", guide=True, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        safety_check=False, targets=[a, b])
    try:
        await run.night(plan)
        assert len(run.slews) == 1 and run.exposure_ends == [], (
            f"premise: Alpha was slewed to and never shot: slews "
            f"{run.rel(run.slews, t0)}, exposures {run.exposure_ends}")
        assert any("skipping Alpha" in m for _lvl, m, _src in bus_lines), (
            "premise: setup must have given up on Alpha after the slew")
        slewed = run.slews[0]
        offs = [t for t in run.tracking_off if t >= slewed]
        assert offs, (
            f"a setup that slewed to Alpha and gave up left the mount tracking "
            f"it, unwatched, for the whole {run.horizon - slewed:.0f} s; "
            f"set_tracking(False) at {run.rel(run.tracking_off, slewed)} s")
        assert TEARDOWN <= offs[0] - slewed <= TEARDOWN + TICK, (
            f"park-held {offs[0] - slewed:.1f} s after the slew")
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Alpha" in lines[0], lines
    finally:
        await run.close()


# ------------------------------------------------------------------- controls

async def test_control_a_short_idle_does_not_park_hold(sim_hub, monkeypatch,
                                                       bus_lines):
    """CONTROL. Idle for less than WAIT_TEARDOWN_S with the tracked target high
    and hours from its flip point: twenty-odd ticks, and tracking stays on.

    Mutant "park-hold on every tick" (`_idle_hold_tick` park-holds without
    asking `_idle_hold_reason`): RED -
        AssertionError: park-held 0.0 s into a 110 s idle with nothing wrong:
        [0.0]
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=EXPOSURE_S + 110.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        assert len([t for t in run.ticks if t >= idle]) >= 20, "premise"
        assert run.tracking_off == [], (
            f"park-held {run.tracking_off[0] - idle:.1f} s into a "
            f"{run.horizon - idle:.0f} s idle with nothing wrong: "
            f"{run.rel(run.tracking_off, idle)}")
        assert run.tracking() is True
        assert _park_lines(bus_lines) == []
    finally:
        await run.close()


async def test_no_tracked_target_no_idle_park_hold(sim_hub, monkeypatch,
                                                   bus_lines):
    """The first target waits (eta 0) before anything was acquired. The mount
    is wherever the operator left it; ten minutes of ticks, no park-hold.

    Mutant "the waiting target counted as tracked" (the scheduler's
    earliest-waiter branch sets ``self._tracked_target = wtarget``): RED -
        AssertionError: park-held a mount nothing had pointed, at [0.0] s
    Mutant "tracked-target guard removed" (`_idle_hold_tick` checks only the
    latch; ``_idle_since`` starts at 0, so the idle clock reads decades):
    RED, and louder - the run dies on the first tick -
        AssertionError: premise: the run must still be going at the horizon;
        it ended at fake +0s: {'state': 'error', ... 'detail': "'NoneType'
        object has no attribute 'name'", ...}
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    b = _target("Bravo", _ra_at(+4.0, t0), 0.0, min_altitude_deg=30.0,
                start_mode="time", start_time=_hhmm(t0 - 2 * 3600),
                stop_mode="time", stop_time=_hhmm(t0 + 3 * 3600))
    try:
        await run.night(_plan(b))
        assert len(run.ticks) > 100, "premise: a long wait"
        assert run.slews == [], "premise: nothing was acquired"
        assert run.tracking_off == [], (
            f"park-held a mount nothing had pointed, at "
            f"{run.rel(run.tracking_off, run.t0)} s")
    finally:
        await run.close()


async def test_a_new_run_does_not_watch_the_last_runs_target(
        sim_hub, monkeypatch, bus_lines):
    """`start()` resets the tracked target, and nothing pinned it: every other
    case here runs one run per engine (S0 review, test gap 5b). Two runs on
    ONE engine. Run 1 acquires Alpha, shoots it and ends. Run 2's only target
    waits on a constraint, so run 2 sits in scheduler waits before any
    `_setup_target`. Through that wait nothing is tracked, the latch is open,
    and no idle set_tracking(False) is issued: the idle watch belongs to what
    THIS run pointed, and before its first setup the mount is wherever it was
    left.

    Mutant "start keeps _tracked_target" (the ``self._tracked_target = None``
    reset in `start()` deleted): RED - run 2 park-holds on run 1's stale
    Alpha at its very first tick, because `start()` still zeroes
    ``_idle_since`` and the idle clock then reads decades -
        AssertionError: run 2 park-held run 1's Alpha before acquiring
        anything: set_tracking(False) at [0.0] s; ['Alpha: nothing has been
        shot for a while and the mount is still tracking it — stopping
        tracking until the next target is set up']
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=4 * 3600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    run.engine.start(_plan(a))
    loop = asyncio.get_running_loop()
    end = loop.time() + 60.0
    while run.engine.running and loop.time() < end:
        await run._real_sleep(0.01)
    assert not run.engine.running and not run.frozen.is_set(), (
        f"premise: run 1 must end before the horizon: {run.engine.state}")
    assert len(run.slews) == 1 and run.exposure_end("Alpha"), (
        "premise: run 1 acquired and shot Alpha")
    assert run.engine._tracked_target is a, (
        "premise: run 1 leaves Alpha as the tracked target, which is what "
        "start() has to clear")

    t1 = run.clock.t
    run.horizon = t1 + 600.0
    run.tracking_off.clear()
    run.slews.clear()
    run.ticks.clear()
    mark = len(bus_lines)
    b = _constraint_waiter("Bravo", t1)
    try:
        await run.night(_plan(b))
        assert len(run.ticks) > 100 and run.slews == [], (
            f"premise: run 2 waited ({len(run.ticks)} ticks) and acquired "
            f"nothing (slews {run.rel(run.slews, t1)})")
        stale = run.engine._tracked_target
        assert run.tracking_off == [], (
            f"run 2 park-held run 1's {getattr(stale, 'name', stale)} before "
            f"acquiring anything: set_tracking(False) at "
            f"{run.rel(run.tracking_off, t1)} s; "
            f"{_park_lines(bus_lines[mark:])}")
        assert stale is None, f"run 2 is watching {stale.name}"
        assert run.engine._idle_hold_open is True
    finally:
        await run.close()


#: `t0`'s second at which run 1's last tick and a retry ask share one fake
#: instant (#223). The first stop comes at +150 s (Alpha's 30 s exposure, then
#: WAIT_TEARDOWN_S), and each retry one IDLE_STOP_RETRY_S plus one hung
#: ``set_tracking(False)`` after the last: +240, +330, ... +600. Bravo's stop,
#: ``_hhmm(t0 + 600)``, falls ``second`` seconds before +600, and the run ends
#: on the first 5 s tick at or after it, which is +600 for every second in
#: [0, 5) and nothing else. 2.5 sits mid-band.
_TIE_SECOND = 2.5
#: One second from the middle of each 5 s band of the minute: every timeline
#: the run can have, since the run's end moves in 5 s steps.
_BAND_SECONDS = [2.5 + 5.0 * k for k in range(12)]


@pytest.mark.parametrize("second", _BAND_SECONDS)
async def test_a_run_that_ends_mid_retry_leaves_no_retry_behind(
        sim_hub, monkeypatch, bus_lines, second):
    """The retry task ends with the run that started it (#189 A3). Two runs on
    ONE engine and a mount that never takes a stop. Run 1 shoots Alpha and
    then waits on Bravo, a setting target below its gate, until Bravo's window
    closes and the run ends by itself; its idle stop went unconfirmed, so a
    retry was asking when it ended. Between the runs, with the engine idle,
    nothing may go on asking: the rig is the operator's again, and a stray
    ``set_tracking(False)`` is somebody else's night. Run 2 then opens on a
    long planned wait, so its stop is decided before any `_setup_target`
    could re-arm the once-per-spell flag: when the mount does not take it,
    it is said, once, because `start()` re-arms the flag too.

    AFTER THE END IS AN ORDER, NOT A TIME (#223). This used to count every
    retry ask with fake time ``>= ended`` as after the end, and failed about
    one run in twelve. Bravo's stop is minute-resolution, so the run's last
    tick moved with the wall-clock second the test started on, while the
    retry's asks ran on their own grid from ``t0``. With ``t0``'s second in
    [0, 5), the retry's fifth ask and the run's last tick fall due at the
    same fake instant, +600 s. The retry set its timer first, so the harness
    wakes it first: it asks and hangs, and only then does the run's tick
    find Bravo's window shut, end the run and cancel the retry mid-ask. That
    ask was made while run 1 was still running, and ``t >= ended`` counted it
    as after. The engine did nothing wrong. The end is now an event: the
    index into ``tracking_calls`` when `_run_scheduled` returns, which is
    where the engine owes the cancel. The night starts at a pinned second
    (`_t0_at_second`), one per 5 s band of the minute (`_BAND_SECONDS`).
    Swept on the old oracle at every half second of the minute, the run
    had twelve timelines, one per band, and failed in exactly the ten
    half-seconds of [0, 5), each time with the text below. `_TIE_SECOND` is
    the band where the tie happens, and a premise pins that it still does.

    Mutant "the old t >= ended oracle" (the event-order filter replaced by
    ``t >= ended`` over `_asks`): RED at the tie second only, with the #223
    failure itself; the other eleven seconds pass, the two oracles agreeing
    wherever nothing ties (observed) -
        [2.5] AssertionError: the retry went on asking after run 1 ended,
        with the engine idle: set_tracking(False) at [(0.0, 'retry')] s
        after the end
    Mutant "run end keeps the retry" (the ``finally`` that cancels the retry
    around `_run_scheduled` in `_run` deleted): RED at every second. At the
    tie second the ask made before the end is no longer among them
    (observed) -
        [2.5] AssertionError: the retry went on asking after run 1 ended,
        with the engine idle: set_tracking(False) at [(90.0, 'retry'),
        (180.0, 'retry'), (270.0, 'retry'), (360.0, 'retry')] s after the end
        [7.5] AssertionError: the retry went on asking after run 1 ended,
        with the engine idle: set_tracking(False) at [(5.0, 'retry'), (95.0,
        'retry'), (185.0, 'retry'), (275.0, 'retry')] s after the end
    Mutant "start keeps _idle_hold_retrying" (the ``self._idle_hold_retrying
    = False`` reset in `start()` deleted): RED at every second - run 2's
    unconfirmed stop is never said (observed) -
        AssertionError: 0 unconfirmed-stop warning(s) in run 2, whose own
        stop the mount never took
    Mutant "the tie second off its band" (``_TIE_SECOND = 7.5``): RED at 7.5
    only, the premise (observed) -
        AssertionError: premise: at second 7.5 a retry ask shares the run's
        last fake instant (+595.0 s) and comes before the end; pick
        `_TIE_SECOND` again from these asks: [(150.0, 'retry'), (240.0,
        'retry'), (330.0, 'retry'), (420.0, 'retry'), (510.0, 'retry')]
    (re-run after #216 moved the first attempt onto the task: the first
    ask reads 'retry' where it read 'run', and nothing else moved. The
    other three mutants above were re-run then too, with the texts above
    unchanged.)
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=4 * 3600.0,
                   t0=_t0_at_second(second))
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _target("Bravo", _ra_at(+4.0, t0), 0.0, min_altitude_deg=30.0,
                start_mode="time", start_time=_hhmm(t0 - 2 * 3600),
                stop_mode="time", stop_time=_hhmm(t0 + 600))
    _a_mount_that_will_not_stop(run, readback="still tracking")
    # THE RUN'S END, IN EVENT ORDER: how many set_tracking calls had been
    # made, and the fake time, when the scheduler returned. `_run` cancels
    # the retry right after it, so anything the retry asks past this index
    # was asked after the run was over.
    ends: list[tuple[int, float]] = []
    real_scheduled = run.engine._run_scheduled

    async def run_scheduled(plan):
        try:
            return await real_scheduled(plan)
        finally:
            ends.append((len(run.tracking_calls), run.clock.t))

    monkeypatch.setattr(run.engine, "_run_scheduled", run_scheduled)
    run.engine.start(_plan(a, b))
    loop = asyncio.get_running_loop()
    end = loop.time() + 60.0
    while run.engine.running and loop.time() < end:
        await run._real_sleep(0.01)
    try:
        assert not run.engine.running and not run.frozen.is_set(), (
            f"premise: run 1 must end before the horizon: {run.engine.state}")
        assert run.engine.state.get("end_reason") == "dawn_cutoff", (
            f"premise: run 1 ended on Bravo's closed window: "
            f"{run.engine.state}")
        assert len(ends) == 1, f"premise: one scheduler, one end: {ends}"
        ended_at, ended = ends[0]
        retried = [t for t, who in _asks(run) if who == "retry"]
        assert retried, (
            f"premise: run 1 was retrying its stop when it ended: asks "
            f"{run.rel([t for t, _w in _asks(run)], t0)} s")
        if second == _TIE_SECOND:
            # ``==`` is exact: every fake instant here is t0 plus whole
            # seconds, which a double holds exactly at this magnitude.
            tie = [t for t, on, who in run.tracking_calls[:ended_at]
                   if not on and who == "retry" and t == ended]
            assert tie, (
                f"premise: at second {second} a retry ask shares the run's "
                f"last fake instant (+{ended - t0:.1f} s) and comes before "
                f"the end; pick `_TIE_SECOND` again from these asks: "
                f"{[(round(t - t0, 1), w) for t, w in _asks(run)]}")
        # With the engine idle the driver would still advance a live retry,
        # the only engine task left: give it the real time to show itself.
        await run._real_sleep(0.3)
        after = [(round(t - ended, 1), who)
                 for t, on, who in run.tracking_calls[ended_at:]
                 if not on and who == "retry"]
        assert after == [], (
            f"the retry went on asking after run 1 ended, with the engine "
            f"idle: set_tracking(False) at {after[:4]} s after the end")

        t1 = run.clock.t
        run.horizon = t1 + 400.0
        run.tracking_calls.clear()
        mark = len(bus_lines)
        c = _target("Charlie", _ra_at(-3.0, t1), 40.0,
                    start_mode="time", start_time=_hhmm(t1 + 3 * 3600))
        await run.night(_plan(c))
        asks = _asks(run)
        # Every ask on the idle-stop task, the first one included: #216 moved
        # the first attempt off the run task, where this premise used to
        # find it.
        assert asks and asks[0][0] - t1 < TICK, (
            f"premise: run 2 opens on Charlie's long planned wait, whose rule "
            f"stops the mount at once: "
            f"{[(round(t - t1, 1), w) for t, w in asks[:3]]}")
        assert [w for _t, w in asks] == ["retry"] * len(asks), asks
        warned = _unconfirmed_lines(bus_lines[mark:])
        assert len(warned) == 1, (
            f"{len(warned)} unconfirmed-stop warning(s) in run 2, whose own "
            f"stop the mount never took")
    finally:
        await run.close()


async def test_a_start_told_what_the_mount_is_tracking_watches_it(
        sim_hub, monkeypatch, bus_lines):
    """`start(tracking=...)` (#202). ResumeArm re-centres a target seconds
    before it starts the resumed run, and leaves the mount tracking it. The
    run's only target, Bravo, then waits on a constraint, which rides the
    scheduler's 5 s else-branch and never reaches `_setup_target`. Told what
    the mount is tracking, the run watches it from the start: park-held on the
    idle clock, WAIT_TEARDOWN_S plus at most one tick after `start()`.

    Mutant "start ignores tracking" (the ``if tracking is not None:`` block in
    `start()` deleted): RED - the re-centred target tracked, unwatched,
    through the whole wait, while the control below stays green (observed) -
        AssertionError: the target the mount was left tracking was tracked,
        unwatched, through the whole 600 s wait; set_tracking(False) at [] s
    Mutant "tracking without the idle anchor" (``self._idle_since =
    time.time()`` deleted from that block, so the idle clock reads from the
    reset's 0, decades ago): RED (observed) -
        AssertionError: park-held 0.0 s after start; the idle clock is 120 s
        plus one 5 s tick
    The CONTROL is `test_no_tracked_target_no_idle_park_hold`: a start that
    is not told stops nothing.
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    b = _constraint_waiter("Bravo", t0)
    tel = sim_hub.devices["telescope"]
    await tel.slew(b.ra_hours, b.dec_deg)       # what ResumeArm's re-centre did
    await tel.set_tracking(True)
    run.slews.clear()
    run.tracking_calls.clear()
    try:
        await run.night(_plan(b), tracking=b)
        assert run.slews == [], (
            f"premise: the run never set anything up: slews "
            f"{run.rel(run.slews, t0)}")
        offs = run.tracking_off
        assert offs, (
            f"the target the mount was left tracking was tracked, unwatched, "
            f"through the whole {run.horizon - t0:.0f} s wait; "
            f"set_tracking(False) at {run.rel(offs, t0)} s")
        assert TEARDOWN <= offs[0] - t0 <= TEARDOWN + TICK, (
            f"park-held {offs[0] - t0:.1f} s after start; the idle clock is "
            f"{TEARDOWN:.0f} s plus one {TICK:.0f} s tick")
        assert run.tracking() is False
        lines = _park_lines(bus_lines)
        assert len(lines) == 1 and "Bravo" in lines[0], lines
    finally:
        await run.close()


# ------------------------------------------------------------- no saved site

async def test_with_no_site_the_flip_check_does_nothing_and_the_clock_still_runs(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """No saved site: the flip point is hour angle, and hour angle at the 0,0
    default is the Gulf of Guinea's. Alpha is placed to reach ITS 0,0 flip
    point 75 s into the wait; the check must not act on that, and the idle
    clock must still stop tracking at WAIT_TEARDOWN_S.

    Mutant "flip check computes the 0,0 default" (`_idle_flip_due` reads
    ``hub.site`` latitude/longitude instead of `site_lat_lon`): RED -
        AssertionError: with no site, park-held at 75.0 s on a flip point
        computed for longitude 0
    """
    _unset_the_site(temp_store, monkeypatch)
    run, a, b, _t_flip = _flip_run(sim_hub, monkeypatch, lon=0.0)
    try:
        await run.night(_plan(a, b, flip=True))
        assert sim_hub.site["is_default"] is True, "premise: no saved site"
        idle = run.exposure_end("Alpha")
        offs = [t for t in run.tracking_off if t >= idle]
        assert offs, "with no site the idle clock must still stop tracking"
        assert TEARDOWN <= offs[0] - idle <= TEARDOWN + TICK, (
            f"with no site, park-held at {offs[0] - idle:.1f} s on a flip "
            f"point computed for longitude 0")
    finally:
        await run.close()


@pytest.mark.parametrize("limit", [{"min_alt_deg": 20.0}, {"max_alt_deg": 60.0}],
                         ids=["floor", "ceiling"])
async def test_with_no_site_a_configured_floor_does_not_park_hold(
        sim_hub, temp_store, monkeypatch, limit):
    """No saved site, a floor or a ceiling configured: the verdict is
    "no_site", which is neither a floor nor a ceiling verdict and must not be
    acted on as one.

    Unreachable in a whole run on purpose - the slew gate refuses to acquire
    anything under a limit it cannot evaluate - so asked of the check itself.

    Mutant "any verdict counts" (``verdict is not None`` without the kind):
    RED, both cases -
        AssertionError: 'Alpha has sunk below the mount's altitude floor'
    """
    _unset_the_site(temp_store, monkeypatch)
    e = SequenceEngine(sim_hub)
    e.plan = _plan()
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False, **limit))
    a = _target("Alpha", 1.0, 20.0)
    e._idle_since = time.time()
    assert e._altitude_limit_verdict(a, projected=False, cfg=e._cfg)[0] \
        == "no_site", "premise"
    assert await e._idle_hold_reason(a) is None, await e._idle_hold_reason(a)


# ---------------------------------------------------------- one floor predicate

async def test_the_slew_gate_and_the_idle_check_ask_one_floor_predicate(
        sim_hub, monkeypatch):
    """The floor is decided ONCE. Swap the predicate's answer and both askers
    follow it: the slew gate raises its sentence, the idle check park-holds.

    Mutant "a second copy of the floor formula in the idle check" (check 2
    computes altaz + `schedule.effective_floor` itself): RED -
        AssertionError: the idle check did not ask the slew gate's
        predicate: None
    """
    e = SequenceEngine(sim_hub)
    e.plan = _plan()
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    a = _target("Alpha", _ra_at(-3.0, time.time()), 20.0)    # high: truly clear
    e._idle_since = time.time()
    monkeypatch.setattr(e, "_altitude_limit_verdict",
                        lambda target, *, projected, cfg:
                        ("floor", "the predicate's own sentence"))
    with pytest.raises(SafetyAbort, match="the predicate's own sentence"):
        await e._enforce_mount_floor(projected=True, target=a)
    why = await e._idle_hold_reason(a)
    assert why is not None and "floor" in why, (
        f"the idle check did not ask the slew gate's predicate: {why}")
