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

THE HARNESS IS A CLOCKED SIMULATOR. `engine_mod.time` is a fake wall clock and
engine.py's `asyncio.sleep`, in the run task only, advances it by what it asked
for and yields once, so a whole night runs through the real `_run_scheduled`
and `_wait_until` against sim_hub without a real sleep. The sim camera advances
the clock by each exposure. At a horizon the run task parks on a future until
the test has looked, so the end-of-run wind-down cannot be mistaken for the
park-hold under test. `set_tracking` is spied (pass-through) with the fake time
of every call. The site is a fixture, never the real one.
"""
from __future__ import annotations

import asyncio
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
    # A slew is real time in the sim (0.5 s floor); nothing here is about it.
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
    """engine.py's `asyncio`: `sleep` goes to the run, the rest is real."""

    def __init__(self, real, run: "_Clocked"):
        self._real = real
        self._run = run

    async def sleep(self, delay, result=None):
        return await self._run.sleep(delay, result)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Clocked:
    """One clocked-simulator night. See the module docstring."""

    def __init__(self, hub, monkeypatch, *, horizon_s: float):
        self.hub = hub
        self.t0 = time.time()
        self.clock = _Clock(time, self.t0)
        self.horizon = self.t0 + horizon_s
        self.frozen = asyncio.Event()
        self.engine = SequenceEngine(hub)
        self.tracking_off: list[float] = []     # fake time of set_tracking(False)
        self.exposure_ends: list[tuple[str, float]] = []
        self.slews: list[float] = []
        self.ticks: list[float] = []            # fake time of every 5 s sleep
        self._real_sleep = asyncio.sleep
        monkeypatch.setattr(engine_mod, "time", self.clock)
        monkeypatch.setattr(engine_mod, "asyncio", _Asyncio(asyncio, self))

        tel = hub.devices["telescope"]
        real_set, real_slew = tel.set_tracking, tel.slew

        async def set_tracking(on):
            if not on and not self.frozen.is_set():
                self.tracking_off.append(self.clock.t)
            await real_set(on)

        async def slew(ra_hours, dec_deg):
            if not self.frozen.is_set():
                self.slews.append(self.clock.t)
            await real_slew(ra_hours, dec_deg)

        monkeypatch.setattr(tel, "set_tracking", set_tracking)
        monkeypatch.setattr(tel, "slew", slew)

        real_capture = hub.capture

        async def capture(exposure_s, *a, **kw):
            info = await real_capture(exposure_s, *a, **kw)
            self.clock.t += float(exposure_s)
            self.exposure_ends.append((kw.get("target", ""), self.clock.t))
            return info

        monkeypatch.setattr(hub, "capture", capture)

    async def sleep(self, delay, result=None):
        if asyncio.current_task() is not self.engine._task:
            return await self._real_sleep(delay, result)
        if self.frozen.is_set():
            await self._real_sleep(0)          # the wind-down after the abort
            return result
        d = max(0.0, float(delay))
        if d == TICK:
            self.ticks.append(self.clock.t)
        self.clock.t += d
        if self.clock.t >= self.horizon:
            self.frozen.set()
            # Parked until the test's abort cancels it: nothing after the
            # horizon - the window closing, the end-of-run park - may be read
            # as the behaviour under test.
            await asyncio.get_running_loop().create_future()
        await self._real_sleep(0)
        return result

    async def night(self, plan: SequencePlan, timeout: float = 60.0) -> None:
        self.engine.start(plan)
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if self.frozen.is_set() or not self.engine.running:
                break
            await self._real_sleep(0.01)
        assert self.frozen.is_set(), (
            f"premise: the run must still be going at the horizon; it ended "
            f"at fake +{self.clock.t - self.t0:.0f}s: {self.engine.state}")

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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        spell_ticks = len([t for t in run.ticks if t >= idle_a])
        assert spell_ticks > 100, f"premise: many ticks ({spell_ticks})"
        offs = run.tracking_off
        assert len(offs) == 2, (
            f"{len(offs)} set_tracking(False) calls for 2 idle spells, at "
            f"{run.rel(offs, idle_a)[:3]}{' ...' if len(offs) > 3 else ''} s")
        assert TEARDOWN <= offs[0] - idle_a <= TEARDOWN + TICK
        assert TEARDOWN <= offs[1] - idle_b <= TEARDOWN + TICK
        assert len(_park_lines(bus_lines)) == 2
    finally:
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


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
        await run.engine.abort()


async def test_with_no_site_a_configured_floor_does_not_park_hold(
        sim_hub, temp_store, monkeypatch):
    """No saved site, a floor configured: the verdict is "no_site", which is not
    a floor verdict and must not be acted on as one.

    Unreachable in a whole run on purpose - the slew gate refuses to acquire
    anything under a floor it cannot evaluate - so asked of the check itself.

    Mutant "any verdict counts" (``verdict is not None`` without the kind):
    RED -
        AssertionError: 'Alpha has sunk below the mount's altitude floor'
    """
    _unset_the_site(temp_store, monkeypatch)
    e = SequenceEngine(sim_hub)
    e.plan = _plan()
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False, min_alt_deg=20.0))
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
