"""A clocked-simulator night for the mosaic group driver (#189 S2, spec 5.1
to 5.10, the S2 tests list).

WHAT RUNS FOR REAL. `SequenceEngine.start`, `_run`, `_run_scheduled`,
`_setup_target`, `_run_steps`, `_run_step` with its whole gate stack,
`_wait_until`, the ledger and its saves, the report, `_set_state`, against
the simulator hub's mount, camera, rotator and cooler. Nothing in the engine
is replaced by the harness: `_safety_gate` is wrapped in a pass-through spy
that records the fake time and context of every call, and a test that
replaces an engine method says so in its own docstring.

WHAT THE HARNESS OWNS, and why each is a script rather than the device:

* ``hub.capture``: parks the calling engine task for the exposure on the fake
  clock and returns a fixed info dict whose ``stars`` the test chooses per
  panel and filter. A plan with ``min_stars`` then REJECTS through the real
  `_check_quality`; the grader is the engine's own, only the sky is scripted.
  The call's keywords are recorded, so a test sees whether ``mosaic`` and
  ``panel`` rode along.
* ``hub.goto_and_center``: points the sim mount (unpark, track, slew, all real
  sim calls) and answers with a scripted result, so a test can say "panel 3
  does not centre" or "no rotator answered" without a solver.
* ``hub.last_sky_angle``: the record ``sky_angle.note_solved_rotation`` would
  write, by script. ``sky(target, attempt)`` stands for the centring solve a
  goto ends with, and ``frame_sky(target, filter, n)`` for the saved-frame WCS
  solve that lands after a light frame. Stamped on the fake clock, which is
  the clock the engine judges freshness on (the real module stamps
  ``time.time()``, the engine's clock outside a test). With no script the
  attribute is never touched, so a night that does not ask for a sky angle
  has none, as before.
* ``hub.guider``: a guider whose start succeeds or fails by script, per panel
  and attempt. It answers what the engine asks and never reads the mount.
* the focuser and the filter wheel are taken off the rig: an autofocus sweep
  and a wheel move are not what the group driver decides, and each would put
  real device timing and star-field noise into a trace that must be exact.
* ``capture_geometry.inventory``: answers "nothing known" at once, instead of
  a library scan whose note depends on how fast the disk answered.
* the config store: the test's own, at the fixture site, through
  `config_store_as`, which hands every module still holding it back the
  store that was there before when the test ends (#296).

``Night.events`` keeps every ``sequence`` and ``log`` event in the shape the
WS lanes and the log ring serve, the ``site_derived`` flag included, so a
test can put a night through `api.redact`'s filters (T18).

THE CLOCK. engine.py's, schedule.py's and catalog.coords' ``time`` are one
fake clock (``time()`` and ``monotonic()`` both read it), and engine.py's
``asyncio.sleep`` parks the engine's run task and its idle-stop task on a
timer heap. A driver advances the clock to the earliest wake once every live
engine task is parked, so a night of hours runs in a second or two of wall
time and every number that comes out of it is exact. The idea, and most of
the machinery, is test_idle_park_hold.py's `_Clocked`; this copy adds the
scripts above and a TRACE: every mount, camera and guider call the engine
makes, every published ``sequence`` state and every ``sequence`` log line,
each stamped with its fake time from the night's start.

catalog.coords is on it for every night (#320) because the hour angle is
read there with no clock of its own, by the engine's meridian countdown and
by the simulator mount, which latches its pier side from it at each slew
(#298). The golden trace is then the same at every hour of the day:
test_group_golden_wall_clock.py shifts every ``time.time`` in the process
to four of them and compares it byte for byte. One input a night reads is
on neither clock (#368): the hub's 2 s status poll runs in real time, and its
meridian cache (``hub.last_meridian``) feeds the engine's ``live`` chip and
the ETA's flip cost. A night inside either window publishes a trace that
depends on where in real time the poll lands; the golden night never
enters one, and that file's ``stop_the_status_poll`` is for a night that
does.

The trace stops at the first terminal publish. What comes after it is the
wind-down, which the group driver does not touch, and which reads things
(an armed resume's window, the cooler) that have no place in a comparison
that must be byte for byte.

THE SPIN WATCHDOG (#319). `Night.run` bounds a night in real time by polling
between ``await asyncio.sleep(0.01)`` calls, and that bound can only run while
the event loop gets control back. A scheduler loop that never reaches an await
that suspends never hands it back: a mutant of that shape held two xdist
workers at 100% of a core until an outer timeout killed the run, and pytest
printed nothing for either. So `Night.run` also arms a thread that watches the
loop from outside it. When the loop has stayed away ``SPIN_BOUND_S`` real
seconds, the thread dumps every thread's stack with faulthandler and raises
`SpinNeverYielded` into the thread running the loop, which breaks the spin;
the next time the loop comes back, `Night.run` fails the test with a message
naming the frame that was spinning and carrying the dump. Its cases are in
test_group_harness_watchdog.py.

THE SITE IS A FIXTURE, 40 N 74 W, and not anybody's rig. ``T0`` is a fixed
instant, 2026-09-02 01:48:09 UTC, at which the fixture site is dark for five
hours and NGC 7331 stands 3 h east of its meridian at 54 degrees. Found by
scanning the calendar with schedule.hour_angle_h and schedule.sun_altitude.

THE GOLDEN NIGHT STARTS AN HOUR EARLIER, at ``GOLDEN_T0``. It used to start
at ``T0`` on the premise that the golden flow plan was "far from any flip"
there, which held only while the countdown read the wall clock: the plan
runs 11700 s, and on the night's clock NGC 7331 crosses 180 min into a
195 min night, so the engine holds for the flip point at 9960 s and flips.
From ``GOLDEN_T0`` it starts at hour angle -4.0 h, 42.8 degrees up with the
sun at -15.5 (shot at once: a scan found the same trace from 30 to 90 min
before ``T0``, and a wait at 120, the sun at -4.7), and it ends 34.6 min
before its flip point, so the premise is true on the clock the night runs
on, and the trace recorded before the S2 group driver comes out byte for
byte.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import faulthandler
import heapq
import itertools
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck import capture_geometry, events
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import SimTelescope
from astrodeck.guide.base import GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, schedule
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

LAT, LON = 40.0, -74.0
#: 2026-09-02 01:48:09 UTC: dark at the fixture site, NGC 7331 at HA -3 h.
T0 = 1788313689.0
#: The golden flow plan's night, an hour before ``T0``: NGC 7331 at HA -4.0 h,
#: and 34.6 min short of its flip point when the plan's 11700 s are shot (see
#: the module docstring; #320).
GOLDEN_T0 = T0 - 3600.0
TERMINAL = ("complete", "aborted", "error")
#: A night never runs past this much fake time; reaching it fails the test.
HORIZON_S = 16 * 3600.0
#: How long, in REAL seconds, the event loop may stay away while `Night.run`
#: waits on a night before the watchdog calls it a spin that never yields
#: (#319). A working night hands the loop back every few milliseconds: the
#: longest stretch measured across every test that runs a Night (19 files,
#: 185 tests, under -n 12 on a 24-thread box with other suites running) was
#: 0.20 s. Fifty times that is still far from anything a working night does,
#: and short enough that a spin fails in bounded time instead of holding a
#: core until somebody notices.
SPIN_BOUND_S = 10.0


def ra_at(ha_h: float, t: float = T0, lon: float = LON) -> float:
    """The RA whose hour angle at ``t`` is ``ha_h`` (HA = LST - RA)."""
    lst = schedule.hour_angle_h(0.0, lon, t) % 24.0
    return (lst - ha_h) % 24.0


# ------------------------------------------------- the two golden-trace plans

GOLDEN_PLAN_PATH = (Path(__file__).parent / "fixtures" / "flow_plan_golden"
                    / "ngc7331_quick.json")
GOLDEN_TRACE_PATH = Path(__file__).parent / "fixtures" / "group_golden_trace.json"


def golden_flow_plan() -> SequencePlan:
    """The golden flow plan, as the compile pins it (``groups == []``), with
    its ids put back. The fixture blanks every id (they are minted fresh on
    each compile), and a plan whose seven steps all have the id "" is one
    step to the ledger: it reads complete after 15 of its 105 frames. So
    each gets a fixed id of its own, as a compile would give it one."""
    raw = json.loads(GOLDEN_PLAN_PATH.read_text(encoding="utf-8"))
    for i, t in enumerate(raw["targets"]):
        t["id"] = f"golden-t{i}"
        for j, s in enumerate(t["steps"]):
            s["id"] = f"golden-t{i}-s{j}"
    for k, ins in enumerate(raw.get("instructions", [])):
        ins["id"] = f"golden-i{k}"
    return SequencePlan.model_validate(raw)


def plain_mosaic_plan() -> SequencePlan:
    """Three targets that share a ``mosaic_group`` with no ``groups`` entry:
    a mosaic sent from the classic Plan, which keeps today's panel-first
    order (spec 3.4). Two frames each, so panel-first reads 1,1,2,2,3,3 and a
    rotation would read 1,2,3,1,2,3."""
    targets = [Target(name=f"Plain {i}", ra_hours=ra_at(-2.0 + 0.05 * i),
                      dec_deg=40.0 + 0.5 * i, center=True,
                      autofocus_first=False, mosaic_group="plain-mosaic",
                      steps=[ExposureStep(filter="L", exposure_s=30.0,
                                          count=2)])
               for i in (1, 2, 3)]
    return SequencePlan(name="plain mosaic", guide=True, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        targets=targets)


# ------------------------------------------------------------ a mosaic group

GROUP_ID = "m31-mosaic"
GROUP_NAME = "M31"


def panel(row: int, col: int, *, filters=("L", "R"), count: int = 3,
          per_visit: int = 1, exposure_s: float = 30.0,
          rotation_deg: float | None = None, schedule_kw: dict | None = None,
          group_id: str = GROUP_ID, ha_h: float = -2.0,
          ha_step_h: float = 0.03) -> Target:
    """One member of the fixture mosaic: panel "r-c" (1-based), named
    "M31 r-c", a cycle of ``filters``. At ``T0`` column 0 stands at hour
    angle ``ha_h`` and each column ``ha_step_h`` hours of hour angle west of
    the last, so the highest column crosses the meridian first (the
    defaults put the mosaic 2 h east of the meridian and high). A meridian
    test moves the mosaic to the meridian with ``ha_h`` and spreads it in
    RA with ``ha_step_h``."""
    from astrodeck.sequence.models import Schedule
    t = Target(id=f"p{row}{col}", name=f"{GROUP_NAME} {row + 1}-{col + 1}",
               ra_hours=ra_at(ha_h + ha_step_h * col), dec_deg=40.0 + 0.4 * row,
               center=True, autofocus_first=False, acquisition="cycle",
               mosaic_group=group_id, panel_row=row, panel_col=col,
               rotation_deg=rotation_deg,
               steps=[ExposureStep(id=f"p{row}{col}-{f}", filter=f,
                                   exposure_s=exposure_s, count=count,
                                   per_visit=per_visit) for f in filters])
    if schedule_kw:
        t.schedule = Schedule(**schedule_kw)
    return t


def single(name: str, *, filters=("L",), count: int = 1, ha_h: float = -2.5,
           **kw) -> Target:
    """A target that is no group's member."""
    tid = name.lower().replace(" ", "-")
    return Target(id=tid, name=name, ra_hours=ra_at(ha_h), dec_deg=35.0,
                  center=True, autofocus_first=False, acquisition="cycle",
                  steps=[ExposureStep(id=f"{tid}-{f}", filter=f,
                                      exposure_s=30.0, count=count)
                         for f in filters], **kw)


def grid_plan(rows: int = 2, cols: int = 2, *, group_kw: dict | None = None,
              panel_kw: dict | None = None, before=(), after=(),
              **plan_kw) -> SequencePlan:
    """A plan holding one ``rows`` x ``cols`` mosaic group, members in
    row-major plan order (so any other order the run shows is the group's),
    with optional plain targets before and after it."""
    from astrodeck.sequence.models import TargetGroup
    gkw = {"id": GROUP_ID, "name": GROUP_NAME,
           "geometry": {"rows": rows, "cols": cols}, **(group_kw or {})}
    group = TargetGroup(**gkw)
    pkw = dict(panel_kw or {})
    if group.rotate and "rotation_deg" not in pkw:
        pkw["rotation_deg"] = group.pa_deg
    members = [panel(r, c, **pkw) for r in range(rows) for c in range(cols)]
    base = {"name": "M31 mosaic", "guide": False, "dither_every": 0,
            "autofocus_every": 0, "meridian_flip": False,
            "park_when_done": False, "warm_cooler_when_done": False,
            "recover_guiding": False}
    base.update(plan_kw)
    return SequencePlan(targets=[*before, *members, *after], groups=[group],
                        **base)


# --------------------------------------------------------------------- fixtures

@contextlib.contextmanager
def config_store_as(store: ConfigStore, monkeypatch):
    """``store`` stands in for the app's config store for the length of the
    block, and NO MODULE KEEPS IT AFTERWARDS.

    `config`, `hub` and the engine are patched and put back by
    ``monkeypatch``. A module imported for the first time inside the block
    that binds ``from ..config import config_store`` at import binds THIS
    store, and nothing would ever put it back: the AM5 driver is imported by
    the simulator's connect, and every test after the first group test in a
    process then read the fixture site from it
    (test_pier_side_is_published's AM5 predictions failed three ways after
    any group test, before this). So on the way out, every module still
    holding ``store`` gets the store that was there before (the class of
    test_isolation_of_module_singletons)."""
    real = config_mod.config_store
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    try:
        yield store
    finally:
        for mod in list(sys.modules.values()):
            if getattr(mod, "config_store", None) is store:
                try:
                    setattr(mod, "config_store", real)
                except (AttributeError, TypeError):  # a module that refuses
                    pass


@pytest.fixture
def group_store(tmp_path, monkeypatch, request):
    """A config store of the test's own, at the fixture site, with the
    safety monitor off (the mount limits still gate every slew). Also hands
    the spin watchdog the pytest config (`_PYTEST_CONFIG`)."""
    global _PYTEST_CONFIG
    _PYTEST_CONFIG = request.config
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Fixture", latitude=LAT, longitude=LON,
                        is_default=False))
    store.set_safety(SafetyConfig(enabled=False))
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    with config_store_as(store, monkeypatch):
        yield store


@pytest.fixture
async def group_hub(group_store, monkeypatch):
    h, popped = await night_hub(monkeypatch)
    yield h
    await close_night_hub(h, popped)


async def night_hub(monkeypatch) -> tuple[Hub, list]:
    """A simulator hub as a night uses it, for ``group_hub`` and for a test
    that needs a second, fresh one: a second night on one hub starts with
    its mount where the first left it, on the side the first latched
    (#298). Returns the hub and the devices taken off it, for
    `close_night_hub`. Needs ``group_store``'s config store in place."""
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    popped = [d for d in (h.devices.pop("focuser", None),
                          h.devices.pop("filterwheel", None)) if d is not None]
    if h.guider is not None:
        await h.guider.disconnect()
    h.guider = ScriptedGuider()
    return h, popped


async def close_night_hub(h: Hub, popped: list) -> None:
    await h.disconnect_all()
    for dev in popped:
        await dev.disconnect()


# ----------------------------------------------------------------- the guider

class ScriptedGuider:
    """A guider whose start succeeds or fails by ``script(target, attempt)``.

    ``attempt`` counts that target's starts from 1. The target is the one the
    engine published as being set up, which `_setup_target` does before its
    slew. A failed start raises the way a real guider's does and leaves the
    guider inactive; a good one leaves it guiding, so no recovery runs."""

    name = "scripted guider"

    def __init__(self) -> None:
        self.connected = True
        self.active = False
        self.script: Callable[[str, int], bool] | None = None
        self.attempts: dict[str, int] = {}
        self.note: Callable[[str, Any], None] = lambda kind, what: None
        self.current: Callable[[], str] = lambda: ""

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        self.active = False

    async def start_guiding(self) -> None:
        who = self.current()
        n = self.attempts.get(who, 0) + 1
        self.attempts[who] = n
        ok = True if self.script is None else bool(self.script(who, n))
        self.note("start", [who, n, ok])
        if not ok:
            self.active = False
            raise DeviceError(f"no guide star found (attempt {n})")
        self.active = True

    async def stop_guiding(self) -> None:
        self.note("stop", None)
        self.active = False

    async def is_active(self) -> bool:
        return self.active

    async def dither(self, pixels: float = 3.0, settle=None) -> None:
        self.note("dither", None)

    def stats(self) -> GuideStats:
        return GuideStats()


# ------------------------------------------------------------------- the clock

class _Clock:
    """engine.py's and schedule.py's ``time``: ``time()`` and ``monotonic()``
    read the fake clock, everything else is the real module."""

    def __init__(self, real, t0: float):
        self._real = real
        self.t = t0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Asyncio:
    """engine.py's ``asyncio``: ``sleep`` goes to the night, the rest is real."""

    def __init__(self, real, night: "Night"):
        self._real = real
        self._night = night

    async def sleep(self, delay, result=None):
        return await self._night.sleep(delay, result)

    def __getattr__(self, name):
        return getattr(self._real, name)


def sky_record(pa_deg: float, t: float, *, source: str = "plate solve + sync",
               **extra) -> dict:
    """A ``hub.last_sky_angle`` record in the shape
    ``sky_angle.note_solved_rotation`` writes, exposed and solved at ``t``."""
    rec = {"pa_deg": float(pa_deg), "exposed_at": float(t),
           "solved_at": float(t), "source": source, "pier_side": None,
           "camera": "Sim Camera", "calibrated": False, "reason": None,
           "mechanical_deg": None, "rotator_before_deg": None,
           "offset_deg": None}
    rec.update(extra)
    return rec


def _state_view(payload: dict, t0: float) -> dict:
    """The published state, as the trace keeps it: every top-level key's
    name (so a key that appears is seen), and the values that say what the
    run is doing. The session id is random and two clock stamps are
    absolute, so those three are dropped or made relative."""
    view: dict[str, Any] = {"keys": sorted(k for k in payload
                                           if not k.startswith("_"))}
    for k in ("state", "detail", "target", "target_index", "schedule", "hold",
              "end_reason", "plan_name", "group"):
        if k in payload:
            view[k] = payload[k]
    prog = dict(payload.get("progress") or {})
    for k in ("server_now_ms", "frame_started_at_ms"):
        if prog.get(k) is not None:
            prog[k] = round(prog[k] - t0 * 1000.0)
    if prog:
        view["progress"] = prog
    sess = payload.get("session")
    if isinstance(sess, dict):
        view["session"] = {k: v for k, v in sess.items() if k != "id"}
    return view


# --------------------------------------------------------- the spin watchdog

class SpinNeverYielded(BaseException):
    """Raised INTO the thread running the event loop when the loop has not
    come back for the watchdog's bound (#319).

    A BaseException, not an Exception. The engine answers an Exception in a
    step or a gate with an "error" end, or logs it and carries on, and either
    would hide the spin behind a verdict about something else; its
    ``except BaseException`` blocks re-raise. The engine's task then ends
    with this as its exception, and `Night.run`, back in control, fails the
    test with ``report``, which `_SpinWatchdog` fills in on the class it
    raises."""

    report = "the event loop did not come back (#319)"

    def __str__(self) -> str:
        return self.report


#: The pytest config, for the watchdog's last resort to get its report out
#: past the capture (`_SpinWatchdog._last_resort`). Set by ``group_store``;
#: one object for the whole session, so a module global is enough.
_PYTEST_CONFIG: Any = None


def _raise_in_thread(thread_id: int, exc_type: type[BaseException]) -> int:
    """Have ``thread_id`` raise ``exc_type`` at its next bytecode.

    `PyThreadState_SetAsyncExc` rather than `_thread.interrupt_main`: the
    latter raises KeyboardInterrupt, and pytest answers that by ending the
    whole run, which is the one thing this must not do. The call returns the
    number of threads it reached, so 0 says the thread had already gone.
    The thread takes the raise at its next check of the eval breaker: at
    once when it is spinning, and on return when it is inside a C call
    (the event loop's poll, a ``time.sleep``)."""
    return ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread_id), ctypes.py_object(exc_type))


class _SpinWatchdog:
    """Watch one event loop from outside it, for `Night.run` (#319).

    `pet` is called from the loop's own thread each time `Night.run` gets a
    turn; the watcher thread wakes a few times a second and compares the
    last pet with the clock. Everything here is REAL time
    (`time.monotonic`, the harness's own `time` module, never the night's
    fake clock), because a spin is a real-time event: the fake clock stops
    with it.

    When the loop has been away ``bound_s``, the watcher writes the report,
    faulthandler's dump of every thread included, and raises
    `SpinNeverYielded` in the loop's thread. If the loop is still away a
    whole bound after that, the spin swallowed the exception or sits in C
    code where a raise cannot reach it; then `_last_resort` puts the report
    where it survives and ends the process with `os._exit`. Under xdist
    that is a crashed worker, which xdist reports against the running test
    and replaces, so the run goes on; under -n0 it ends the run, with the
    report printed. A hang is the one outcome not allowed.

    `stop`, called from the loop's thread, ends the watcher, since a
    watchdog outliving its run would throw into whatever the test does
    next. It marks the watchdog stopped and then waits out a raise already
    under way (the raise happens under the lock, after a last look at the
    mark), so no raise STARTS after it returns. A raise made just before
    may still land in the loop's thread afterwards, at its next check of
    the eval breaker, which is a few bytecodes on; `Night.run` catches one
    that lands in its own frame or in `stop`, and one that lands later
    fails the test with the report, which is true: the loop did stay away
    a whole bound. Neither window is one a test can place a raise in on
    purpose; they are guarded because the cost of missing one is a
    watcher left running into the next test."""

    def __init__(self, bound_s: float, what: str) -> None:
        self.bound_s = float(bound_s)
        self.what = what
        self.loop_thread = threading.get_ident()
        self.beat = time.monotonic()
        #: The longest the loop stayed away between two pets, in real
        #: seconds: what a working night's gaps look like, for the control.
        self.max_away = 0.0
        #: The failure text, once the watchdog has fired; None until then.
        self.report: str | None = None
        self._lock = threading.Lock()
        self._stopped = False
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True,
                                        name=f"spin watchdog: {what}")

    def start(self) -> None:
        self.beat = time.monotonic()
        self._thread.start()

    def pet(self) -> None:
        now = time.monotonic()
        self.max_away = max(self.max_away, now - self.beat)
        self.beat = now

    def stop(self) -> None:
        self._stopped = True
        try:
            with self._lock:         # a raise under way finishes first
                pass
        finally:
            self._halt.set()
            if self._thread.is_alive():
                self._thread.join(timeout=5.0)

    def _watch(self) -> None:
        tick = min(0.25, self.bound_s / 8.0)
        fired_at: float | None = None
        while not self._halt.wait(tick):
            away = time.monotonic() - self.beat
            if away < self.bound_s:
                fired_at = None
                continue
            with self._lock:
                if self._stopped:
                    return
                if fired_at is None:
                    if self.report is None:
                        self.report = self._describe(away)
                    exc_type = type(SpinNeverYielded.__name__,
                                    (SpinNeverYielded,),
                                    {"report": self.report})
                    _raise_in_thread(self.loop_thread, exc_type)
                    fired_at = time.monotonic()
                elif time.monotonic() - fired_at >= self.bound_s:
                    self._last_resort(away)

    def _describe(self, away: float) -> str:
        """The failure text: what happened, the frame the loop's thread was
        in and its chain of frames in the package, and faulthandler's dump
        of every thread. Names and line numbers only: no variable's value.
        The first two are read a moment before the dump, so on a spin they
        can name a neighbouring frame of the same loop."""
        marker = f"{os.sep}astrodeck{os.sep}"

        def name(fs: traceback.FrameSummary) -> str:
            k = fs.filename.rfind(marker)
            path = (fs.filename[k + 1:] if k >= 0
                    else Path(fs.filename).name)
            return f"{fs.name} ({path}:{fs.lineno})"

        frame = sys._current_frames().get(self.loop_thread)
        stack = traceback.extract_stack(frame) if frame is not None else []
        where = name(stack[-1]) if stack else "an unknown frame"
        # The package's frames, outermost first. The innermost one alone is
        # often a helper the loop calls (a log line, a publish), where the
        # spin passes through rather than where it turns; the chain shows
        # the loop around it.
        ours = " > ".join(name(fs) for fs in stack if marker in fs.filename)
        try:
            # Through a file because faulthandler writes to a descriptor,
            # and read back so the dump rides in the failure text, which
            # reaches the report under any capture mode and under xdist.
            with tempfile.TemporaryFile("w+", encoding="utf-8",
                                        errors="replace") as f:
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.flush()
                f.seek(0)
                dump = f.read()
        except Exception as exc:  # noqa: BLE001 - the raise must still happen
            dump = f"(faulthandler could not dump: {type(exc).__name__}: {exc})"
        lines = [
            f"{self.what}: the event loop did not come back for {away:.1f} s "
            f"of real time, against a bound of {self.bound_s:g} s "
            f"(_group_harness.SPIN_BOUND_S unless the test set its own). "
            f"A spin that never yields (#319): the engine kept running "
            f"without reaching an await that suspends, so the night's "
            f"wall-clock bound (wall_s) could not run either.",
            f"The loop's thread was spinning in {where}.",
            f"Its frames in astrodeck, outermost first: {ours or 'none'}.",
            "Every thread's stack when the watchdog fired, from faulthandler:",
            dump.rstrip(),
        ]
        return "\n".join(lines)

    def _last_resort(self, away: float) -> None:
        """Put the report where it survives the exit, and exit.

        pytest's fd capture has descriptor 2 pointed at a temporary file
        while a test runs, and `os._exit` throws that file away unread: a
        scratch run of the "raise never lands" mutant printed NOTHING under
        -n0, and under xdist only "worker 'gw0' crashed while running ...".
        So, with the pytest config ``group_store`` hands over: the capture
        is suspended, which puts descriptor 2 back on the terminal for a
        -n0 run; and in an xdist worker, whose stderr does not reach the
        controller's output on this box (a probe writing to it after the
        suspend showed nothing), the report is also sent to the controller
        as a warning on the running test, so it is printed in the run's
        warnings summary beside xdist's own "crashed while running"."""
        text = (f"{self.report}\n\nThe raise did not break the spin: the loop "
                f"has now been away {away:.1f} s. Ending this process so the "
                f"run can go on (#319).\n")
        cfg = _PYTEST_CONFIG
        if cfg is not None:
            try:
                capman = cfg.pluginmanager.getplugin("capturemanager")
                if capman is not None:
                    capman.suspend_global_capture(in_=False)
            except Exception:  # noqa: BLE001 - the exit below must happen
                pass
            try:
                _tell_the_xdist_controller(cfg, text)
            except Exception:  # noqa: BLE001 - the exit below must happen
                pass
        try:
            os.write(2, text.encode("utf-8", "replace"))
        finally:
            os._exit(3)


def _tell_the_xdist_controller(cfg, text: str) -> None:
    """Send ``text`` to the xdist controller as a warning recorded on the
    running test, through the worker's own channel; nothing when this
    process is not an xdist worker. The send is written to the channel
    before it returns, so the exit that follows does not lose it."""
    interactor = next((p for p in cfg.pluginmanager.get_plugins()
                       if type(p).__name__ == "WorkerInteractor"), None)
    if interactor is None:
        return
    import warnings

    from xdist.remote import serialize_warning_message
    nodeid = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]
    message = warnings.WarningMessage(UserWarning(text), UserWarning,
                                      __file__, 0)
    interactor.sendevent(
        "warning_recorded",
        warning_message_data=serialize_warning_message(message),
        when="runtest", nodeid=nodeid, location=None)


# ------------------------------------------------------------------- the night

class Night:
    """One clocked-simulator night. See the module docstring.

    ``stars(target, filter)`` is the star count each frame reports (50 unless
    a test says otherwise); with the plan's ``min_stars`` it decides the
    real grader's verdict. ``goto(target, attempt, result)`` may rewrite the
    centring result the engine is handed. ``guide(target, attempt)`` decides
    each guider start.

    ``sky(target, attempt)`` is the sky angle that goto's centring solve
    measured: a PA, a dict of record fields over :func:`sky_record`'s, or
    None for no record from this goto (the solve failed, or reported no
    rotation), which leaves the last one standing, stale. ``frame_sky(target,
    filter, n)`` does the same for the ``n``-th light frame of ``target``,
    exposed when that frame's shutter opened and landing after it, as the
    saved-frame WCS solve does.

    ``catalog.coords`` IS ON THE NIGHT'S CLOCK, for every night (#320). The
    engine's meridian countdown and the simulator mount's pier side read the
    hour angle there with no clock of their own. It used to be a choice,
    ``coords_clock``, off by default, on the premise that "a night far from
    any meridian reads the same either way"; but that premise is about the
    night's fake time, and with the choice off both read the WALL clock. So
    whenever the hour of day put NGC 7331 near the fixture site's meridian,
    the golden night held for a flip point the frozen countdown never
    reached, until the fake horizon: the golden trace failed for a stretch
    of every evening. ``coords_clock`` stays a keyword because eight test
    files still pass it (the meridian, reach, follower, pier, locked-angle
    and S3 example cases); anything but True is refused, since a night half
    on the wall clock is exactly that failure.

    ``spin_bound_s`` is the spin watchdog's bound for `run` (#319), in real
    seconds; only the watchdog's own tests shorten it.
    """

    def __init__(self, hub, monkeypatch, *, t0: float = T0,
                 horizon_s: float = HORIZON_S,
                 spin_bound_s: float = SPIN_BOUND_S,
                 stars: Callable[[str, str], int] | None = None,
                 goto: Callable[[str, int, dict], dict] | None = None,
                 guide: Callable[[str, int], bool] | None = None,
                 sky: Callable[[str, int], Any] | None = None,
                 frame_sky: Callable[[str, str, int], Any] | None = None,
                 coords_clock: bool = True,
                 engine: SequenceEngine | None = None):
        if coords_clock is not True:
            raise ValueError(
                "Night: catalog.coords runs on the night's clock (#320); a "
                "night whose hour angle reads the wall clock times its flip "
                "by the hour of day the suite runs at")
        self.hub = hub
        self.mp = monkeypatch
        self.t0 = float(t0)
        self.clock = _Clock(time, self.t0)
        self.horizon = self.t0 + horizon_s
        self.spin_bound_s = float(spin_bound_s)
        #: The last `run`'s watchdog, kept for a test to read its
        #: ``max_away``; None before the first run.
        self.watchdog: _SpinWatchdog | None = None
        self.frozen = asyncio.Event()
        self.engine = engine if engine is not None else SequenceEngine(hub)
        self.stars = stars or (lambda target, filt: 50)
        self.goto_script = goto
        self.sky_script = sky
        self.frame_sky_script = frame_sky
        #: Every goto the engine asked for, flip re-centres included, as
        #: ``{"t", "who", "rotation_deg", "kw"}``: what angle each commanded.
        self.goto_calls: list[dict] = []
        self._frame_n: dict[str, int] = {}
        #: Called with each capture's record, before its exposure, from the
        #: engine's own task: a test reads the engine's live state there.
        self.on_capture: Callable[[dict], None] | None = None
        self.trace: list[list] = []
        self.captures: list[dict] = []
        self.gotos: list[tuple[float, str]] = []
        self.gates: list[tuple[float, str]] = []
        self.states: list[dict] = []
        self.lines: list[tuple[float, str, str]] = []
        #: Every ``sequence`` and ``log`` event as the bus fanned it out, in
        #: ``Event.to_json()``'s shape (``type``, ``data``, ``ts`` on the fake
        #: clock), the ``site_derived`` flag included: what the WS lanes and
        #: the log ring serve, for a test to put through `api.redact`'s
        #: filters. Not part of the trace, so the golden trace is untouched.
        self.events: list[dict] = []
        #: The ``sequence`` lines published with ``site_derived=True``, as
        #: ``(t, level, message)``.
        self.flagged: list[tuple[float, str, str]] = []
        self._goto_n: dict[str, int] = {}
        self._recording = True
        self._timers: list[tuple[float, int, asyncio.Future]] = []
        self._parked: dict[asyncio.Task, asyncio.Future] = {}
        self._seq = itertools.count()
        self._real_sleep = asyncio.sleep
        monkeypatch.setattr(engine_mod, "time", self.clock)
        monkeypatch.setattr(schedule, "time", self.clock)
        import astrodeck.catalog.coords as coords_mod
        monkeypatch.setattr(coords_mod, "time", self.clock)
        monkeypatch.setattr(engine_mod, "asyncio", _Asyncio(asyncio, self))

        async def inventory(*a, **k):
            return [], ""

        monkeypatch.setattr(capture_geometry, "inventory", inventory)

        guider = hub.guider
        if isinstance(guider, ScriptedGuider):
            guider.script = guide
            guider.note = lambda kind, what: self._note("guider", kind, what)
            guider.current = lambda: str(self.engine.state.get("target", ""))

        tel = hub.devices["telescope"]
        real_set, real_slew = tel.set_tracking, tel.slew

        async def set_tracking(on):
            self._note("tracking", bool(on))
            await real_set(on)

        async def slew(ra_hours, dec_deg):
            self._note("slew", round(float(ra_hours), 6),
                       round(float(dec_deg), 6))
            await real_slew(ra_hours, dec_deg)

        monkeypatch.setattr(tel, "set_tracking", set_tracking)
        monkeypatch.setattr(tel, "slew", slew)

        async def goto_and_center(ra_hours, dec_deg, *args, rotation_deg=None,
                                  **kw):
            who = str(self.engine.state.get("target", ""))
            n = self._goto_n.get(who, 0) + 1
            self._goto_n[who] = n
            self.gotos.append((self.clock.t, who))
            self.goto_calls.append({"t": self.clock.t, "who": who,
                                    "rotation_deg": rotation_deg,
                                    "kw": dict(kw)})
            self._note("goto", who, rotation_deg,
                       {k: kw[k] for k in sorted(kw)})
            if await tel.is_parked():
                await tel.unpark()
            await tel.set_tracking(True)
            await tel.slew(ra_hours, dec_deg)
            result = {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                      "rotation": None}
            if self.goto_script is not None:
                result = self.goto_script(who, n, result)
            if self.sky_script is not None:
                self._write_sky(self.sky_script(who, n), self.clock.t)
            return result

        monkeypatch.setattr(hub, "goto_and_center", goto_and_center)

        async def capture(exposure_s, gain, offset, binning=1, **kw):
            eng = self.engine
            filt = ""
            if eng.plan is not None and eng._active_step is not None:
                ti, si = eng._active_step
                filt = eng.plan.targets[ti].steps[si].filter
            name = kw.get("target", "")
            extra = {k: v for k, v in sorted(kw.items())
                     if k not in ("target", "frame_type", "save")}
            group = eng.state.get("group")
            rec = {"t": self.clock.t, "target": name, "filter": filt,
                   "exposure_s": float(exposure_s), "extra": extra,
                   "visit": len(self.gotos),
                   "group": dict(group) if isinstance(group, dict) else None}
            self.captures.append(rec)
            if self.on_capture is not None:
                self.on_capture(rec)
            self._note("capture", name, filt, float(exposure_s), gain, offset,
                       binning, kw.get("frame_type"), kw.get("save"), extra)
            if self._is_engine_task(asyncio.current_task()):
                await self._park(float(exposure_s))
            else:
                self.clock.t += float(exposure_s)
            if (self.frame_sky_script is not None
                    and str(kw.get("frame_type", "Light")).lower() == "light"):
                k = self._frame_n.get(name, 0) + 1
                self._frame_n[name] = k
                self._write_sky(self.frame_sky_script(name, filt, k), rec["t"])
            return {"hfr": 2.0, "stars": int(self.stars(name, filt)),
                    "saved_path": None, "data_width": 64, "data_height": 48}

        monkeypatch.setattr(hub, "capture", capture)

        real_gate = self.engine._safety_gate

        async def safety_gate(*a, **kw):
            self.gates.append((self.clock.t, kw.get("context", "")))
            return await real_gate(*a, **kw)

        monkeypatch.setattr(self.engine, "_safety_gate", safety_gate)

        bus = events.bus
        real_publish, real_log = bus.publish, bus.log

        def publish(topic, **payload):
            if topic in ("sequence", "log") and self._recording:
                self.events.append({"type": topic, "data": dict(payload),
                                    "ts": self.clock.t})
            if topic == "sequence" and self._recording:
                view = _state_view(payload, self.t0)
                self.states.append(view)
                self._note("state", view)
                if payload.get("state") in TERMINAL:
                    self._recording = False
            return real_publish(topic, **payload)

        def log(level, message, source="hub", **kw):
            if self._recording and source == "sequence":
                self.lines.append((self.clock.t, level, message))
                if kw.get("site_derived"):
                    self.flagged.append((self.clock.t, level, message))
                self._note("log", level, message)
            return real_log(level, message, source, **kw)

        monkeypatch.setattr(bus, "publish", publish)
        monkeypatch.setattr(bus, "log", log)
        self._driver = asyncio.get_running_loop().create_task(self._drive())

    def _write_sky(self, what, exposed_at: float) -> None:
        """Put a scripted sky-angle record on the hub, exposed at
        ``exposed_at`` and solved now; ``None`` writes nothing."""
        if what is None:
            return
        fields = dict(what) if isinstance(what, dict) else {"pa_deg": what}
        rec = sky_record(fields.pop("pa_deg"), exposed_at, **fields)
        rec["solved_at"] = self.clock.t
        self.hub.last_sky_angle = rec

    # ------------------------------------------------------------ the trace

    def _note(self, kind: str, *what) -> None:
        if self._recording and not self.frozen.is_set():
            self.trace.append([round(self.clock.t - self.t0, 3), kind, *what])

    def trace_text(self) -> str:
        """The trace as it is compared and stored: one JSON array per line,
        keys sorted, so a difference is one line in a diff."""
        return "".join(json.dumps(entry, sort_keys=True,
                                  separators=(",", ":")) + "\n"
                       for entry in self.trace)

    # ------------------------------------------------------------ the clock

    def rel(self, t: float) -> float:
        return round(t - self.t0, 3)

    def _engine_tasks(self) -> list[asyncio.Task]:
        return [t for t in (self.engine._task, self.engine._idle_stop_task)
                if t is not None and not t.done()]

    def _is_engine_task(self, task) -> bool:
        return task is not None and task in (self.engine._task,
                                             self.engine._idle_stop_task)

    async def _park(self, d: float) -> None:
        if self.frozen.is_set():
            self.clock.t += max(0.0, float(d))
            await self._real_sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._timers, (self.clock.t + max(0.0, d),
                                      next(self._seq), fut))
        self._parked[asyncio.current_task()] = fut
        await fut

    async def _drive(self) -> None:
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
        await self._park(max(0.0, float(delay)))
        return result

    # ------------------------------------------------------------- the night

    async def run(self, plan, *, wall_s: float = 60.0, **start_kw) -> bool:
        """Start ``plan`` and wait until the run has ended and wound down.
        False when the harness's WALL-CLOCK bound (``wall_s`` real seconds)
        or the fake horizon comes first: the run was still going, which is
        the failure a spin that yields produces.

        A spin that never yields gives this loop no turn at all, so the
        bound above never runs. The watchdog armed here catches that one
        (#319): it breaks the spin, and the turn this loop then gets fails
        the test with the watchdog's report. Armed before ``start``, since a
        spin may begin at the first step, and stopped on the way out
        whatever the way out is."""
        dog = _SpinWatchdog(self.spin_bound_s, "Night.run")
        self.watchdog = dog
        dog.start()
        try:
            self.engine.start(plan, **start_kw)
            self.session_id = self.engine._session.id
            loop = asyncio.get_running_loop()
            end = loop.time() + wall_s
            while loop.time() < end and not self.frozen.is_set():
                dog.pet()
                if dog.report is not None:
                    break
                if not self.engine.running:
                    return True
                await self._real_sleep(0.01)
            else:
                return False
        except SpinNeverYielded:
            # The raise landed in this frame rather than the engine's: the
            # loop was back by then, so the report says all there is.
            pass
        finally:
            # Once more if the raise lands in the stop itself: only one is
            # ever made, and the watcher must not outlive this run.
            for _attempt in range(3):
                try:
                    dog.stop()
                    break
                except SpinNeverYielded:
                    continue
        pytest.fail(dog.report or SpinNeverYielded.report, pytrace=False)

    async def close(self) -> None:
        try:
            if self.engine.running:
                self.frozen.set()
                await self.engine.abort()
        finally:
            self._driver.cancel()
            await asyncio.gather(self._driver, return_exceptions=True)
            # A run the watchdog broke ends with `SpinNeverYielded` as its
            # task's exception. The failure already carries the report, so
            # take the exception here, or asyncio logs it again as "Task
            # exception was never retrieved" when the task is collected,
            # outside the test's report. Only then: any other exception a
            # run ends with is left for asyncio to report.
            task, dog = self.engine._task, self.watchdog
            if (dog is not None and dog.report is not None
                    and task is not None and task.done()
                    and not task.cancelled()):
                task.exception()

    # -------------------------------------------------------------- reading

    def shots(self) -> list[tuple[str, str]]:
        """Every science exposure as ``(target, filter)``, in order."""
        return [(c["target"], c["filter"]) for c in self.captures]

    def visits(self) -> list[tuple[str, tuple[str, ...]]]:
        """Every hop the engine made, in order, with the filters it shot
        there: ``[(target, (filters...)), ...]``. A hop is one centred goto,
        which `_setup_target` makes once per visit, so a visit that shot
        nothing (a deferral) is here with no filters, and two visits in a row
        to one panel stay two entries."""
        out: list[tuple[str, list[str]]] = [(who, []) for _t, who in self.gotos]
        for c in self.captures:
            n = c["visit"]
            if 0 < n <= len(out):
                out[n - 1][1].append(c["filter"])
        return [(t, tuple(f)) for t, f in out]

    def said(self, needle: str) -> list[str]:
        return [m for _t, _lvl, m in self.lines if needle in m]
