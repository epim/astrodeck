"""The cloud hold called itself through the safety gate until Python gave up.

MEASURED, astrotown 2026-09-06 22:13:46, v0.3.25. A low run produced one frame
the cloud detector called cloudy. No safety monitor was assigned and
``safety.sky_fallback_hold`` was on, so the frame-boundary gate went

    _safety_gate(context="frame")  ->  _no_safety_source  ->  _hold_for_clear

and the FIRST thing the hold's own loop does is take that same gate again -
deliberately, so dawn and a real monitor keep their say while the run sits. But
the verdict that started the hold is still cloudy (that is what a hold IS), so
the gate answered it with a SECOND hold, which took the gate, which held again.

    323 x "holding for clear sky"      in one second
    325 x "native guider stopped"      in the same second

until ``RecursionError``. The run died, and it took the night-log file writer
down with it - the ring buffer kept working, so the failure was invisible in the
UI and only the missing file said anything.

TWO GUARDS, because the two callers are different mistakes. ``_no_safety_source``
stands aside while a hold is running (the hold's probe loop owns the sky verdict
and this fallback has nothing to add), and ``_hold_for_clear`` refuses to nest at
all - the ``hold_for_clear`` INSTRUCTION reaches it by another route, and a
second hold would reset the 45-minute bound the first one is counting.

WHAT THIS FILE DRIVES. The REAL ``_safety_gate`` from inside the REAL
``_hold_for_clear``, on a real ``SequenceEngine``, with the sky reading cloudy -
the exact loop of the incident. Stubbing either one would test the stub.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _engine(hub, monkeypatch):
    """The rig of the incident: safety armed, NO monitor, fallback hold on.

    ``mode`` is not "sim" on purpose - a simulated frame is not a sky, and the
    fallback refuses to read one (see
    test_sky_stands_in_for_a_missing_monitor.py). Modelling a real rig is the
    only way to reach the branch that recursed.
    """
    hub.mode = "native"
    hub.devices.pop("safety", None)
    eng = SequenceEngine(hub)
    cfg = hub_module.config_store.cfg()
    monkeypatch.setattr(cfg.safety, "enabled", True)
    monkeypatch.setattr(cfg.safety, "sky_fallback_hold", True)
    monkeypatch.setattr(cfg.escalation, "require_safety_monitor", False)
    eng._cfg = cfg
    eng.plan = SequencePlan(
        name="p", safety_check=True, guide=False, cloud_hold_darks=0,
        targets=[Target(name="A", ra_hours=5.5, dec_deg=-5.0, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])
    return eng


class _Sky:
    """The frame-derived verdict, switchable, without faking the detector maths.

    ``clear_after`` probes: the hold's probe loop is what discovers the change,
    exactly as on the rig.
    """

    def __init__(self, eng, *, clear_after: int | None):
        self.eng = eng
        self.clear_after = clear_after
        self.probes = 0
        eng._clouds.cloudy = self._verdict            # type: ignore[assignment]
        eng._clouds.describe = lambda _now: (         # type: ignore[assignment]
            "cloudy (0 bright stars, low contrast)" if self.cloudy else "clear")

    @property
    def cloudy(self) -> bool:
        return (self.clear_after is None
                or self.probes < self.clear_after)

    def _verdict(self, _now):
        return self.cloudy


def _stub_the_rig(eng, monkeypatch, sky: _Sky, *, probe_every=0.001):
    """Everything the hold loop touches that is NOT the recursion.

    ``_safety_gate`` and ``_no_safety_source`` are deliberately REAL - they are
    the two halves of the loop under test. So is ``_hold_for_clear``.
    """
    async def _none(*a, **k):
        return None

    async def _no_dark(*a, **k):
        return False

    async def _probe(*a, **k):
        sky.probes += 1
        return sky.cloudy

    for name in ("_checkpoint", "_frame_alerts_tick", "_stand_down_guider",
                 "_cooler_gate", "_setup_target", "_restore_beam"):
        monkeypatch.setattr(eng, name, _none)
    monkeypatch.setattr(eng, "_enforce_stop_boundary", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_hold_darks", _no_dark)
    monkeypatch.setattr(eng, "_cloud_probe", _probe)
    monkeypatch.setattr(eng, "_index_of_target", lambda t: 0)
    monkeypatch.setattr(eng, "_set_state", lambda **kw: None)
    monkeypatch.setattr(engine_mod, "CLOUD_PROBE_EVERY_S", probe_every)


def _count_entries(eng, monkeypatch) -> dict:
    """Wrap the REAL ``_hold_for_clear`` and count how often it is ENTERED."""
    seen = {"n": 0, "depth": 0, "max_depth": 0}
    real = eng._hold_for_clear

    async def counted(reason, target):
        seen["n"] += 1
        seen["depth"] += 1
        seen["max_depth"] = max(seen["max_depth"], seen["depth"])
        try:
            return await real(reason, target)
        finally:
            seen["depth"] -= 1

    monkeypatch.setattr(eng, "_hold_for_clear", counted)
    return seen


async def test_the_hold_is_entered_once_and_releases_when_the_sky_clears(
        sim_hub, monkeypatch):
    """The incident, end to end, with a sky that eventually clears.

    Before the guard this did not fail an assertion - it raised
    ``RecursionError`` out of the hold, which is precisely what happened to the
    run at 22:13:46.
    """
    eng = _engine(sim_hub, monkeypatch)
    sky = _Sky(eng, clear_after=1)      # cloudy, then clear from probe 2 on
    _stub_the_rig(eng, monkeypatch, sky)
    seen = _count_entries(eng, monkeypatch)

    await eng._hold_for_clear("the frames say the sky has closed in", None)

    assert seen["n"] == 1, (
        f"the hold was entered {seen['n']} times for one cloudy sky; the "
        f"deepest nesting was {seen['max_depth']}")
    assert seen["max_depth"] == 1, "the hold nested inside itself"
    assert sky.probes >= engine_mod.CLOUD_RESUME_CLEAR_PROBES, (
        "the hold released without the clear streak it requires")
    assert eng._holding_for_clear is False, "the hold flag outlived the hold"


async def test_the_gate_inside_the_hold_does_not_start_another_hold(
        sim_hub, monkeypatch):
    """The narrow claim, isolated: the REAL gate, called while holding, on a
    sky that is still shut, must not hold again.

    This is the single call that recursed. The gate itself must still RUN -
    dawn and a real monitor are why the hold takes it at all - so the
    assertion is about the hold, not about the gate being skipped.
    """
    eng = _engine(sim_hub, monkeypatch)
    sky = _Sky(eng, clear_after=None)   # never clears
    _stub_the_rig(eng, monkeypatch, sky)
    seen = _count_entries(eng, monkeypatch)

    eng._holding_for_clear = True       # as the running hold would have left it
    try:
        await eng._safety_gate(context="frame", target=None)
    finally:
        eng._holding_for_clear = False
    assert seen["n"] == 0, (
        "the gate answered the hold's own cloudy verdict with another hold")


async def test_a_second_hold_request_is_refused_and_says_so(
        sim_hub, monkeypatch, bus_lines):
    """The ``hold_for_clear`` INSTRUCTION reaches the hold by another route, so
    the hold carries its own guard. A refusal is a warning, not a silence: a
    second request that vanished would look exactly like a hold that worked."""
    eng = _engine(sim_hub, monkeypatch)
    sky = _Sky(eng, clear_after=None)
    _stub_the_rig(eng, monkeypatch, sky)

    eng._holding_for_clear = True
    try:
        # BOUNDED, because the failure this guards against is a hold that RUNS.
        # Under a sky that never clears that is a 45-minute loop, and a test
        # that hangs for 45 minutes reports the defect as a dead worker rather
        # than as a red assertion. Sabotage found this the hard way.
        await asyncio.wait_for(
            eng._hold_for_clear("an instruction asked for a hold", None), 5.0)
    except asyncio.TimeoutError:
        pytest.fail("the second request started a real hold instead of "
                    "standing aside")
    finally:
        eng._holding_for_clear = False
    msgs = [m for (_lvl, m, _src) in bus_lines]
    assert any("already holding for clear sky" in m for m in msgs), msgs
    # ...and it must NOT have stood the guider down a second time, which is the
    # other half of the 325 lines in that one second.
    assert not any("holding for clear sky:" in m for m in msgs), msgs


async def test_the_running_hold_keeps_its_own_timeout(sim_hub, monkeypatch):
    """A nested hold would have restarted ``started``, so the 45-minute
    abort-and-park bound could never be reached under a sky that stayed shut -
    the run would sit until dawn instead. Drive the real loop with a sky that
    never clears and a bound of nothing, and it must abort.
    """
    from astrodeck.sequence.engine import SafetyAbort

    eng = _engine(sim_hub, monkeypatch)
    sky = _Sky(eng, clear_after=None)
    _stub_the_rig(eng, monkeypatch, sky)
    seen = _count_entries(eng, monkeypatch)
    monkeypatch.setattr(engine_mod, "CLOUD_MAX_HOLD_MIN", 0.001)

    with pytest.raises(SafetyAbort, match="cloud hold exceeded"):
        await eng._hold_for_clear("the sky never opened", None)
    assert seen["n"] == 1
    assert eng._holding_for_clear is False
