"""The hop between targets is measured and priced into the ETA (#189 U-07,
spec 5.6 step 10 and 5.10).

`compute_eta` priced exposures, per-frame overhead, dithers, autofocus and
the flip, and nothing for moving between targets: every slew, centring and
guide start was missing from the finish clock of a multi-target night. Now:

* `_setup_target` records its wall time as event cost ``"hop"``, MINUS the
  initial autofocus sweep's own time, because the sweep already records
  itself as ``"autofocus"`` and must not be counted twice. The clock starts
  after the safety gate, so a weather hold at the gate is not charged to
  the hop either.
* `compute_eta` adds ``remaining_hops x _event_cost("hop", HOP_COST_S)`` to
  ``events_cost_s``, where the hops are the non-calibration targets other
  than the one being acquired that still owe frames. Before the first
  acquisition the run's opening slew is not a hop between targets (no ETA
  has ever priced it, single target or not), so the count is one less than
  the targets owing frames.
* ``eta_confident`` is False while hops remain and none has been measured,
  and the new additive key ``hops_costed`` says whether the hop term is
  measured (True when no hop remains, since there is then nothing to cost).

The clock is virtual: `engine.time` is replaced by a proxy whose
``monotonic`` only moves when a stub says so, so each recorded hop is an
exact number.
"""
from __future__ import annotations

import asyncio
import time as _realtime

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.sim import SimTelescope
from astrodeck.guide.base import Guider, GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import ETA_MIN_FRAMES, HOP_COST_S


class _Clock:
    """Stand-in for the ``time`` module inside the engine: ``monotonic`` is
    virtual, everything else is the real module."""

    def __init__(self) -> None:
        self.mono = 1000.0

    def monotonic(self) -> float:
        return self.mono

    def advance(self, s: float) -> None:
        self.mono += s

    def __getattr__(self, name):
        return getattr(_realtime, name)


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(engine_mod, "time", c)
    return c


SLEW_S = 60.0      # the slew, virtual
GUIDE_S = 30.0     # the guide start, virtual
SWEEP_S = 400.0    # the initial autofocus sweep, virtual
GATE_S = 1000.0    # a weather hold at the safety gate, virtual


class _Tel:
    connected = True

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock

    async def is_parked(self) -> bool:
        return False

    async def unpark(self) -> None: ...

    async def set_tracking(self, on: bool) -> None: ...

    async def get_tracking(self) -> bool:
        return True

    async def slew(self, ra: float, dec: float) -> None:
        self.clock.advance(SLEW_S)


class _Guider(Guider):
    name = "clocked guider"

    def __init__(self, clock: _Clock) -> None:
        self.connected = True
        self.clock = clock

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None:
        self.clock.advance(GUIDE_S)

    async def stop_guiding(self) -> None: ...

    async def is_active(self) -> bool:
        return False

    async def dither(self, pixels: float = 3.0, settle=None) -> None: ...

    def stats(self) -> GuideStats:
        return GuideStats()

    async def needs_calibration(self) -> bool | None:
        return False


class _Hub:
    def __init__(self, clock: _Clock) -> None:
        self.devices = {"telescope": _Tel(clock), "focuser": object()}
        self.guider = _Guider(clock)
        self.site: dict = {}

    def _check_solar(self, ra, dec, *, force=False) -> None:
        return None

    def require(self, role: str):
        return self.devices[role]


def _target(name: str, ra: float = 6.0, *, count: int = 2,
            calibration: bool = False, af: bool = False) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=20.0, center=False,
                  autofocus_first=af, calibration=calibration,
                  steps=[ExposureStep(filter="L", exposure_s=10.0,
                                      count=count)])


def _engine(clock: _Clock, targets, *, guide: bool = True) -> SequenceEngine:
    e = SequenceEngine(_Hub(clock))
    e._cfg = None
    e.plan = SequencePlan(name="hops", guide=guide, meridian_flip=False,
                          safety_check=False, autofocus_every=0,
                          dither_every=0, targets=targets)
    # Enough overhead samples that only the hop can hold confidence back.
    e._overhead_samples = ETA_MIN_FRAMES
    return e


def _base_eta_s(e: SequenceEngine) -> float:
    """What the ETA is without any hop: capture plus per-frame overhead
    (no dither, no autofocus cadence and no flip in these plans)."""
    frames = e.plan.total_frames() - e._frames_done
    return e._remaining_capture_s() + frames * e._overhead_ema


# ------------------------------------------------------------- the ETA term


async def test_an_unmeasured_three_target_plan_carries_two_seeded_hops(clock):
    """Three targets and nothing measured: two hops at the 150 s seed.

    Mutant "no hop term" (`compute_eta` adds nothing for hops): RED -
        AssertionError: the hop term: 0 s, want 2 x 150 s
    Mutant "count the active target" (the count includes the target being
    acquired, and the opening slew before the first acquisition): RED -
        AssertionError: the hop term: 450 s, want 2 x 150 s
    Mutant "confident without a hop sample" (the hop clause dropped from
    ``eta_confident``): RED -
        AssertionError: a finish clock carrying 2 unmeasured hops claimed to
        be confident
    Mutant "hops_costed always True": RED -
        AssertionError: 2 hops remain and none is measured, yet hops_costed
        is True
    """
    assert HOP_COST_S == 150.0, "the seed the spec prices hops at (A.3)"
    e = _engine(clock, [_target("A"), _target("B", 6.1), _target("C", 6.2)])
    eta = e.compute_eta()
    assert eta["events_cost_s"] == 300, (
        f"the hop term: {eta['events_cost_s']} s, want 2 x 150 s")
    assert eta["eta_s"] == round(_base_eta_s(e) + 300), eta
    assert eta["eta_confident"] is False, (
        "a finish clock carrying 2 unmeasured hops claimed to be confident")
    assert eta["hops_costed"] is False, (
        "2 hops remain and none is measured, yet hops_costed is True")


async def test_after_a_measured_90_s_hop_the_two_left_cost_90_each(clock):
    """Acquire A through the real `_setup_target`: slew 60 s and guide start
    30 s on the virtual clock, so one hop of exactly 90 s is recorded. B and C
    are left, at the measured 90 s each; A is being acquired, so it is not a
    hop.

    Mutant "no hop term": RED -
        AssertionError: the hop term: 0 s, want 2 x 90 s
    Mutant "count the active target": RED -
        AssertionError: the hop term: 270 s, want 2 x 90 s
    Mutant "hops_costed ignores the measurement" (``hops_costed`` reports
    only whether hops remain): RED -
        AssertionError: a measured hop left hops_costed False
    """
    a, b, c = _target("A"), _target("B", 6.1), _target("C", 6.2)
    e = _engine(clock, [a, b, c])
    await e._setup_target(0, a)
    assert e._event_costs.get("hop") == [SLEW_S + GUIDE_S], (
        f"premise: one hop of 90 s recorded: {e._event_costs}")
    eta = e.compute_eta()
    assert eta["events_cost_s"] == 180, (
        f"the hop term: {eta['events_cost_s']} s, want 2 x 90 s")
    assert eta["eta_s"] == round(_base_eta_s(e) + 180), eta
    assert eta["hops_costed"] is True, "a measured hop left hops_costed False"
    assert eta["eta_confident"] is True, (
        f"measured hops and enough frames, yet not confident: {eta}")


async def test_only_targets_that_still_owe_frames_are_hops(clock):
    """A finished target and a calibration target are never hopped to.

    Mutant "count finished targets" (owing frames not checked): RED -
        AssertionError: B is finished and D is calibration, so one hop (to
        C) remains: 180 s
    Mutant "count calibration targets" (the calibration test dropped):
    RED -
        AssertionError: B is finished and D is calibration, so one hop (to
        C) remains: 180 s
    """
    a, b, c = _target("A"), _target("B", 6.1), _target("C", 6.2)
    d = _target("D", calibration=True)
    e = _engine(clock, [a, b, c, d])
    await e._setup_target(0, a)                      # A is being acquired
    e._done[f"{b.id}:{b.steps[0].id}"] = b.steps[0].count
    e._frames_done = b.steps[0].count
    eta = e.compute_eta()
    assert eta["events_cost_s"] == round(SLEW_S + GUIDE_S), (
        f"B is finished and D is calibration, so one hop (to C) remains: "
        f"{eta['events_cost_s']} s")


async def test_the_flip_window_includes_the_hops(clock, monkeypatch):
    """The flip is priced only when it falls inside the remaining run, and
    the hops are part of the remaining run.

    Mutant "window without hops" (the hop term left out of
    ``remaining_window_s``): RED -
        AssertionError: the flip was judged against a window without the
        hops: 132.0 s, want 432.0 s
    ("no hop term" reds it the same way, and "count the active target"
    reads 582.0 s.)
    """
    seen: list[float] = []

    def flip_pending(window_s: float) -> bool:
        seen.append(window_s)
        return False

    e = _engine(clock, [_target("A"), _target("B", 6.1), _target("C", 6.2)])
    monkeypatch.setattr(e, "_flip_pending", flip_pending)
    e.compute_eta()
    want = _base_eta_s(e) + 300
    assert seen and seen[0] == pytest.approx(want), (
        f"the flip was judged against a window without the hops: "
        f"{seen[0] if seen else None} s, want {want} s")


# ----------------------------------------------------------- the measurement


async def test_the_hop_does_not_count_the_autofocus_sweep(clock, monkeypatch):
    """A's setup: slew 60 s, an initial sweep of 400 s, guide start 30 s. The
    sweep records itself as "autofocus", so the hop is 90 s, not 490.

    Mutant "hop includes the sweep" (the sweep's time not subtracted): RED -
        AssertionError: the hop recorded the sweep too: [490.0], want [90.0]
    """
    async def autofocus(label, *a, **kw):
        clock.advance(SWEEP_S)
        return True

    a = _target("A", af=True)
    e = _engine(clock, [a, _target("B", 6.1)])
    monkeypatch.setattr(e, "_autofocus", autofocus)
    await e._setup_target(0, a)
    assert e._event_costs.get("hop") == [SLEW_S + GUIDE_S], (
        f"the hop recorded the sweep too: {e._event_costs.get('hop')}, "
        f"want [{SLEW_S + GUIDE_S}]")


async def test_a_hold_at_the_safety_gate_is_not_hop_cost(clock, monkeypatch):
    """The safety gate can hold for weather; that is the sky, not the hop.

    Mutant "clock starts before the gate" (the hop clock read at the top of
    `_setup_target`): RED -
        AssertionError: the hop charged the gate's hold: [1090.0], want
        [90.0]
    """
    async def gate(*, context, target=None):
        clock.advance(GATE_S)

    a = _target("A")
    e = _engine(clock, [a, _target("B", 6.1)])
    monkeypatch.setattr(e, "_safety_gate", gate)
    await e._setup_target(0, a)
    assert e._event_costs.get("hop") == [SLEW_S + GUIDE_S], (
        f"the hop charged the gate's hold: {e._event_costs.get('hop')}, "
        f"want [{SLEW_S + GUIDE_S}]")


# ----------------------------------------------------------------- controls


async def test_control_a_single_target_eta_is_unchanged(clock):
    """CONTROL. One target has nothing to hop to, before its acquisition and
    after it, so its ETA is exactly what it was before hops were priced, and
    there is nothing to cost.

    Mutant "count the active target": RED -
        AssertionError: a single-target plan grew a hop term: 150 s
    """
    a = _target("A")
    e = _engine(clock, [a])
    for when in ("before", "after"):
        if when == "after":
            await e._setup_target(0, a)
        eta = e.compute_eta()
        assert eta["events_cost_s"] == 0, (
            f"a single-target plan grew a hop term: {eta['events_cost_s']} s")
        assert eta["eta_s"] == round(_base_eta_s(e)), (when, eta)
        assert eta["hops_costed"] is True, (when, eta)
        assert eta["eta_confident"] is True, (when, eta)


# --------------------------------------------------------- across two runs


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _quick_plan(*targets: Target) -> SequencePlan:
    return SequencePlan(name="runs", guide=False, meridian_flip=False,
                        safety_check=False, autofocus_every=0,
                        dither_every=0, targets=list(targets))


def _quick(name: str, ra: float) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=20.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.02, count=1)])


async def test_a_new_run_does_not_inherit_the_last_runs_target_index(sim_hub):
    """The engine is one per process (`api/app.py`) and outlives its runs, so
    `start` resets the index of the target being acquired. A two-target run
    ends acquiring its second target (index 1); the next run is a single
    target, and before its setup begins it has nothing to hop to.

    Real runs on the sim rig, no virtual clock: the second run's ETA is read
    straight after `start`, before its task has run a line, which is the
    moment the stale index would be read.

    Mutant "acquiring index not reset at start" (the ``self._acquiring_ti =
    None`` in `start` deleted): RED -
        AssertionError: the new single-target run was charged a hop to its
        own only target, left over from the last run's index 1: 150 s
    """
    e = SequenceEngine(sim_hub)
    e.start(_quick_plan(_quick("A", 6.00), _quick("B", 6.05)))
    await asyncio.wait_for(asyncio.shield(e._task), 60)
    assert e.state.get("state") == "complete", (
        f"premise: the first run finished: {e.state.get('state')} "
        f"{e.state.get('detail')}")
    assert e._acquiring_ti == 1, (
        f"premise: the first run ended acquiring index 1: {e._acquiring_ti}")

    e.start(_quick_plan(_quick("C", 6.10)))
    eta = e.compute_eta()
    await asyncio.wait_for(asyncio.shield(e._task), 60)
    assert eta["events_cost_s"] == 0, (
        f"the new single-target run was charged a hop to its own only "
        f"target, left over from the last run's index 1: "
        f"{eta['events_cost_s']} s")
    assert eta["hops_costed"] is True, eta
