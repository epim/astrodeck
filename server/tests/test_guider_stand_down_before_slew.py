# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The guider is stood down before a hop's slew (#148, spec 5.6 step 2).

`_setup_target` slewed to the next target with the previous target's guide
loop still running, and nothing between the two targets stopped it. The
native guider's `start_guiding` returns at once while its loop is alive (the
"already active" guard, `guide/native.py` start_guiding), so the next
target's guide start did nothing at all: no star selection, no calibration
reuse check, and no `_maybe_flip_for_pier`. On the far pier side that leaves
the old side's calibration driving the new side's corrections, which is the
runaway GN-01 exists to prevent.

The defect was PLAUSIBLE and unverified when it was filed, so the first test
here demonstrates it on the simulator with the REAL native guider (the sim
rig's own, fast-calibration config) and the real engine: guide on A, then set
up B with the mount reporting the other pier side. It was run on the
unmodified engine before any fix and went red on all three assertions (the
failure is recorded verbatim in its docstring).

The sim guide camera renders its star wherever the guide offset puts it,
whatever the mount points at, so the loop keeps its lock through the slew.
That is the case the fix has to cover. A real slew would lose the star for a
while, and a loop that is re-acquiring still counts as alive to the guard.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import PierSide
from astrodeck.devices.sim import SimTelescope
from astrodeck.guide.base import Guider, GuideStats
from astrodeck.hub import Hub
from astrodeck.providers import NATIVE_AVAILABLE
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target

native_only = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                 reason="native wheel absent")


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    import astrodeck.config as config_mod
    # The native guider persists its calibration under CONFIG_DIR at CALL
    # time; a tmp dir keeps this test's calibration out of everyone else's.
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    # The native guider, not the legacy SimGuider escape hatch.
    monkeypatch.delenv("ASTRODECK_SIM_LEGACY_GUIDER", raising=False)
    # A hop at this rate costs the slew's 0.5 s floor. That floor now goes
    # through _sim_delay too (#207), so under the fast path this line only
    # matters to a run that opts out of it.
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    g = h.guider
    yield h
    await h.disconnect_all()
    # The Rust GuideEngine is unsendable: dropped by a garbage collection
    # that happens to run on a worker thread, it raises "is being dropped on
    # another thread" into some later test. The wrappers `_instrument`
    # installs make the guider part of a reference cycle, so drop the engine
    # here, on the loop's own thread, before the cycle is left to the GC.
    if hasattr(g, "_engine"):
        g._engine = None


class _Pier:
    """The mount's pier-side report, driven by the test. The sim derives its
    side from where it points, and these two targets are chosen close
    together so the slew is short; the report is what the guider and the
    engine read, so the report is what the test sets."""

    def __init__(self, side: str) -> None:
        self.side = side

    async def __call__(self) -> PierSide:
        return PierSide(self.side)


def _loop_alive(g) -> bool:
    task = getattr(g, "_loop_task", None)
    return task is not None and not task.done()


def _target(name: str, ra: float, *, center: bool) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=20.0, center=center,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


def _engine(hub, targets, *, guide: bool = True) -> SequenceEngine:
    e = SequenceEngine(hub)
    e._cfg = None                  # warn-and-continue; no monitor gate
    e.plan = SequencePlan(name="hop", guide=guide, meridian_flip=False,
                          safety_check=False, autofocus_every=0,
                          dither_every=0, targets=targets)
    return e


def _instrument(hub, monkeypatch, pier: _Pier) -> list[tuple]:
    """Record, in order, every stop, slew, goto, start and pier check, each
    with whether the guide loop was alive at that moment."""
    events: list[tuple] = []
    tel = hub.devices["telescope"]
    g = hub.guider
    real_slew, real_stop = tel.slew, g.stop_guiding
    real_start, real_flip = g.start_guiding, g._maybe_flip_for_pier

    async def slew(ra, dec):
        events.append(("slew", _loop_alive(g)))
        await real_slew(ra, dec)

    async def goto_and_center(ra, dec, **kw):
        # The motion is the sim's real slew; only the plate-solve loop is
        # left out, because the loop state at the moment of MOTION is what
        # this measures.
        events.append(("goto", _loop_alive(g)))
        await real_slew(ra, dec)
        return {"centered": True, "error_arcmin": 0.2}

    async def stop_guiding():
        events.append(("stop", _loop_alive(g)))
        await real_stop()

    async def start_guiding():
        events.append(("start", _loop_alive(g)))
        await real_start()

    async def maybe_flip_for_pier():
        events.append(("pier check", pier.side))
        await real_flip()

    monkeypatch.setattr(tel, "slew", slew)
    monkeypatch.setattr(hub, "goto_and_center", goto_and_center)
    monkeypatch.setattr(g, "stop_guiding", stop_guiding)
    monkeypatch.setattr(g, "start_guiding", start_guiding)
    monkeypatch.setattr(g, "_maybe_flip_for_pier", maybe_flip_for_pier)
    return events


def _cal_pier(g) -> str | None:
    cal = g._engine.dump_calibration() if g._engine is not None else None
    return (cal or {}).get("pier_side")


@native_only
@pytest.mark.parametrize("center", [False, True],
                         ids=["plain slew", "goto and centre"])
async def test_the_guide_loop_is_stopped_before_the_next_targets_slew(
        sim_hub, monkeypatch, center):
    """Guide on A (mount east), set up B with the mount reporting west. The
    loop must be dead when B's motion starts, and B's guide start must run
    for real: through `_maybe_flip_for_pier`, ending with a calibration for
    the side the mount is on.

    RED ON THE UNMODIFIED ENGINE, run before the fix (2026-09-24), both
    branches, first failing assertion:
        AssertionError: B's slew started with A's guide loop still running:
        [('slew', True), ('start', True)]
        AssertionError: B's goto started with A's guide loop still running:
        [('goto', True), ('start', True)]
    (the list is B's events: no stop at all, the motion with the loop alive,
    and a start that found the loop alive and returned at once). With the
    motion and stop assertions disabled, the next two were red too, both
    branches:
        AssertionError: B's guide start never reached _maybe_flip_for_pier:
        [('slew', True), ('start', True)]
        AssertionError: B guides on a calibration for the east side while
        the mount reports west
    Mutant "remove the stand-down" (the `_stand_down_guider()` call before
    the branch in `_setup_target` deleted): RED, both branches, the same
    first line as above.
    """
    tel = sim_hub.devices["telescope"]
    g = sim_hub.guider
    assert type(g).__name__ == "NativeGuider", (
        f"premise: the sim rig's own native guider, got {type(g).__name__}")
    assert g.tel is tel, "premise: the guider and the engine read one mount"
    pier = _Pier("east")
    monkeypatch.setattr(tel, "pier_side", pier)
    a = _target("A", 6.00, center=center)
    b = _target("B", 6.05, center=center)
    e = _engine(sim_hub, [a, b])
    events = _instrument(sim_hub, monkeypatch, pier)

    await e._setup_target(0, a)
    assert _loop_alive(g), f"premise: A must be guiding: {events}"
    assert _cal_pier(g) == "east", (
        f"premise: A's calibration is for the east side: {_cal_pier(g)}")

    pier.side = "west"
    mark = len(events)
    await e._setup_target(1, b)
    b_events = events[mark:]
    motion = "goto" if center else "slew"
    moves = [ev for ev in b_events if ev[0] == motion]
    assert len(moves) == 1, f"premise: B moved once: {b_events}"
    assert moves[0][1] is False, (
        f"B's {motion} started with A's guide loop still running: "
        f"{b_events}")
    assert ("stop", True) in b_events and \
        b_events.index(("stop", True)) < b_events.index(moves[0]), (
            f"the stop was not observed before B's {motion}: {b_events}")
    start = b_events.index(next(ev for ev in b_events if ev[0] == "start"))
    assert ("pier check", "west") in b_events[start:], (
        f"B's guide start never reached _maybe_flip_for_pier: {b_events}")
    assert _cal_pier(g) == "west", (
        f"B guides on a calibration for the {_cal_pier(g)} side while the "
        f"mount reports west")


# ------------------------------------------------------------------ controls


class _RecordingGuider(Guider):
    """A connected guider that records stops and starts."""

    name = "recording guider"

    def __init__(self) -> None:
        self.connected = True
        self.calls: list[str] = []

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None:
        self.calls.append("start")

    async def stop_guiding(self) -> None:
        self.calls.append("stop")

    async def is_active(self) -> bool:
        return False

    async def dither(self, pixels: float = 3.0, settle=None) -> None: ...

    def stats(self) -> GuideStats:
        return GuideStats()

    async def needs_calibration(self) -> bool | None:
        return False


@pytest.fixture
async def plain_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@pytest.mark.parametrize("center", [False, True],
                         ids=["plain slew", "goto and centre"])
async def test_control_a_plan_that_does_not_guide_never_stops_the_guider(
        plain_hub, monkeypatch, center):
    """CONTROL. A plan that does not guide never started the guider, so the
    guider is the operator's, and the hop leaves it alone.

    Mutant "stand down whatever the plan says" (the `self.plan.guide` test
    dropped from the stand-down): RED, both branches -
        AssertionError: a plan that does not guide stopped the guider:
        ['stop']
    """
    g = _RecordingGuider()
    plain_hub.guider = g

    async def goto_and_center(ra, dec, **kw):
        return {"centered": True, "error_arcmin": 0.2}

    monkeypatch.setattr(plain_hub, "goto_and_center", goto_and_center)
    t = _target("A", 6.0, center=center)
    e = _engine(plain_hub, [t], guide=False)
    await e._setup_target(0, t)
    assert g.calls == [], (
        f"a plan that does not guide stopped the guider: {g.calls}")


async def test_control_a_guiding_plan_stops_the_recording_guider_once(
        plain_hub, monkeypatch):
    """CONTROL for the double above: a guiding plan DOES stop it, once,
    before the start. Without this the no-guide control could pass because
    the double is never reached at all.

    Mutant "remove the stand-down": RED -
        AssertionError: a guiding plan's hop: ['start']
    """
    g = _RecordingGuider()
    plain_hub.guider = g
    t = _target("A", 6.0, center=False)
    e = _engine(plain_hub, [t], guide=True)
    await e._setup_target(0, t)
    assert g.calls == ["stop", "start"], f"a guiding plan's hop: {g.calls}"


@pytest.mark.parametrize("state", ["absent", "disconnected"])
async def test_control_no_guider_means_no_stop(plain_hub, state):
    """CONTROL. No guider, or one that is not connected: the hop has nothing
    to stand down and must not fail trying.

    Mutant "stop without asking whether a guider is there" (the guard in
    `_stand_down_guider` reduced to `if self.hub.guider:`): RED, disconnected -
        AssertionError: a disconnected guider was asked to stop: ['stop']
    (the absent case stays green under it: `None` still fails the reduced
    test, and `_stand_down_guider` swallows every error by design, so the
    absent case is a no-crash check, not a guard.)
    """
    g = _RecordingGuider()
    if state == "absent":
        plain_hub.guider = None
    else:
        g.connected = False
        plain_hub.guider = g
    t = _target("A", 6.0, center=False)
    e = _engine(plain_hub, [t], guide=True)
    await e._setup_target(0, t)
    if state == "disconnected":
        assert "stop" not in g.calls, (
            f"a disconnected guider was asked to stop: {g.calls}")
