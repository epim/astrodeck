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

THE CLOCK. engine.py's ``time`` and schedule.py's ``time`` are one fake clock
(``time()`` and ``monotonic()`` both read it), and engine.py's
``asyncio.sleep`` parks the engine's run task and its idle-stop task on a
timer heap. A driver advances the clock to the earliest wake once every live
engine task is parked, so a night of hours runs in a second or two of wall
time and every number that comes out of it is exact. The idea, and most of
the machinery, is test_idle_park_hold.py's `_Clocked`; this copy adds the
scripts above and a TRACE: every mount, camera and guider call the engine
makes, every published ``sequence`` state and every ``sequence`` log line,
each stamped with its fake time from the night's start.

The trace stops at the first terminal publish. What comes after it is the
wind-down, which the group driver does not touch, and which reads things
(an armed resume's window, the cooler) that have no place in a comparison
that must be byte for byte.

THE SITE IS A FIXTURE, 40 N 74 W, and not anybody's rig. ``T0`` is a fixed
instant, 2026-09-02 01:48:09 UTC, at which the fixture site is dark for five
hours and NGC 7331 stands 3 h east of its meridian at 54 degrees, so the
golden flow plan (dusk start, 30 degree gate, dawn stop) is shootable at
once and far from any flip. Found by scanning the calendar with
schedule.hour_angle_h and schedule.sun_altitude.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import json
import sys
import time
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
TERMINAL = ("complete", "aborted", "error")
#: A night never runs past this much fake time; reaching it fails the test.
HORIZON_S = 16 * 3600.0


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
def group_store(tmp_path, monkeypatch):
    """A config store of the test's own, at the fixture site, with the
    safety monitor off (the mount limits still gate every slew)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Fixture", latitude=LAT, longitude=LON,
                        is_default=False))
    store.set_safety(SafetyConfig(enabled=False))
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    with config_store_as(store, monkeypatch):
        yield store


@pytest.fixture
async def group_hub(group_store, monkeypatch):
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
    yield h
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

    ``coords_clock`` puts ``catalog.coords`` on the fake clock as well. The
    meridian countdown and the sim mount's pier side read the hour angle
    there, with no clock of their own, so without it a flip is timed by the
    wall clock whatever the night's fake time says. Off by default: the
    golden trace was recorded with it off, and a night far from any meridian
    reads the same either way.
    """

    def __init__(self, hub, monkeypatch, *, t0: float = T0,
                 horizon_s: float = HORIZON_S,
                 stars: Callable[[str, str], int] | None = None,
                 goto: Callable[[str, int, dict], dict] | None = None,
                 guide: Callable[[str, int], bool] | None = None,
                 sky: Callable[[str, int], Any] | None = None,
                 frame_sky: Callable[[str, str, int], Any] | None = None,
                 coords_clock: bool = False,
                 engine: SequenceEngine | None = None):
        self.hub = hub
        self.mp = monkeypatch
        self.t0 = float(t0)
        self.clock = _Clock(time, self.t0)
        self.horizon = self.t0 + horizon_s
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
        if coords_clock:
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
        the failure a spin produces."""
        self.engine.start(plan, **start_kw)
        self.session_id = self.engine._session.id
        loop = asyncio.get_running_loop()
        end = loop.time() + wall_s
        while loop.time() < end and not self.frozen.is_set():
            if not self.engine.running:
                return True
            await self._real_sleep(0.01)
        return False

    async def close(self) -> None:
        try:
            if self.engine.running:
                self.frozen.set()
                await self.engine.abort()
        finally:
            self._driver.cancel()
            await asyncio.gather(self._driver, return_exceptions=True)

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
