"""Engine device-I/O timeout bounds (P0-2) + escalation-knob wiring (P1-7).

These cover the operational-review fixes that the existing sim suites can't
exercise on the happy path:

* **P0-2 — bounded device I/O:** every engine await on hub.capture / slew /
  goto_and_center / guider / park / cooler is wrapped in ``asyncio.wait_for``.
  A wedged call must escalate through the abort+park wind-down (SafetyAbort),
  never an unbounded hang. We drive this by replacing a hub/guider call with one
  that sleeps far past the (monkeypatched-tiny) timeout.

* **P1-7 — escalation knobs:** ``require_guiding``/``guiding_action``,
  ``require_cooling``/``cooling_action`` and ``af_failure_action`` are now read
  by the engine. We assert ``abort`` tears the night down and the default
  ``warn`` leaves the legacy behavior intact.

Config is isolated through a temp ConfigStore and CAPTURE_DIR is redirected to
tmp (same pattern as test_engine_safety.py).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import (ConfigStore, EscalationConfig, SafetyConfig, Site)
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.report import SessionReporter


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0, is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.5)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    # Disarm the W1.10 sun-exclusion cone at the hub-method level so it survives
    # the per-test ``set_safety(SafetyConfig(...))`` resets and the FIXED M42
    # targets aren't date-dependently sun-blocked. The cone is covered by
    # test_sun_guard.py; here we isolate the timeout/escalation mechanics.
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def light_plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="t",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=3)])],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    )
    return SequencePlan(**(defaults | overrides))


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


# ----------------------------------------------------- P0-2 capture timeout

async def test_capture_timeout_aborts_and_parks(sim_hub, temp_store, monkeypatch):
    """A capture that hangs past its bound tears the night down (aborted/unsafe)
    and parks the mount, instead of an unbounded await silently wedging the run."""
    store = temp_store
    store.set_safety(SafetyConfig(enabled=False))
    # shrink the capture margin so a 5 s hung 'capture' blows the bound fast.
    monkeypatch.setattr(engine_mod, "CAPTURE_MARGIN_S", 0.2)

    async def hung_capture(*a, **k):
        await asyncio.sleep(30.0)        # never returns within the bound
    monkeypatch.setattr(sim_hub, "capture", hung_capture)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running, timeout=15), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    # shielded wind-down parked the mount on the timeout teardown.
    assert await sim_hub.require("telescope").is_parked()
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None and rep.end_reason == "unsafe"


async def test_slew_timeout_aborts(sim_hub, temp_store, monkeypatch):
    """A hung slew in _setup_target is bounded and escalates to abort+park.

    Only the FIRST (target) slew hangs — the wind-down park (which in the sim
    re-uses slew) must still complete, so subsequent slews delegate to the real
    implementation."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    monkeypatch.setattr(engine_mod, "SLEW_TIMEOUT_S", 0.2)

    tel = sim_hub.require("telescope")
    real_slew = tel.slew
    calls = {"n": 0}

    async def hang_first_slew(ra, dec):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(30.0)       # the target slew wedges
        else:
            await real_slew(ra, dec)        # park's internal slew works
    monkeypatch.setattr(tel, "slew", hang_first_slew)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running, timeout=15), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    assert await sim_hub.require("telescope").is_parked()


# ------------------------------------------------ P1-7 require_guiding=abort

async def test_require_guiding_abort_when_start_fails(sim_hub, temp_store, monkeypatch):
    """require_guiding + guiding_action='abort': if guiding can't start, the run
    aborts (no all-night unguided/trailed run) — the dead knob is now wired."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig(require_guiding=True,
                                               guiding_action="abort"))

    async def failing_start():
        raise RuntimeError("PHD2 calibration failed")
    monkeypatch.setattr(sim_hub.guider, "start_guiding", failing_start)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(guide=True))
    assert await wait_for(lambda: not engine.running, timeout=15), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"


async def test_warn_guiding_default_continues_unguided(sim_hub, temp_store, monkeypatch):
    """Default guiding_action='warn' (require_guiding off): a guiding-start failure
    is logged and the run continues unguided — legacy behavior preserved."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig())   # all defaults (warn)

    async def failing_start():
        raise RuntimeError("PHD2 calibration failed")
    monkeypatch.setattr(sim_hub.guider, "start_guiding", failing_start)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(guide=True))
    assert await wait_for(lambda: engine.state.get("state") == "complete",
                          timeout=20), engine.state
    assert engine._frames_done == 3


# ------------------------------------------------ P1-7 require_cooling=abort

async def test_require_cooling_abort_on_timeout(sim_hub, temp_store, monkeypatch):
    """require_cooling + cooling_action='abort': a cool-timeout aborts instead of
    shooting warm lights all night."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig(require_cooling=True,
                                               cooling_action="abort"))

    cam = sim_hub.require("camera")

    async def never_cold():
        return 99.0           # never within COOLER_AT_TARGET_C of the target
    monkeypatch.setattr(cam, "get_temperature", never_cold)

    engine = SequenceEngine(sim_hub)
    # tiny cool_timeout so the wait loop exits fast.
    engine.start(light_plan(cool_to=-10.0, cool_timeout_s=1))
    assert await wait_for(lambda: not engine.running, timeout=15), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"


async def test_require_cooling_skip_does_not_shoot(sim_hub, temp_store, monkeypatch):
    """require_cooling + cooling_action='skip': a cool-timeout completes WITHOUT
    capturing any light frames (end_reason cooling_skip)."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig(require_cooling=True,
                                               cooling_action="skip"))
    cam = sim_hub.require("camera")

    async def never_cold():
        return 99.0
    monkeypatch.setattr(cam, "get_temperature", never_cold)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(cool_to=-10.0, cool_timeout_s=1))
    assert await wait_for(lambda: not engine.running, timeout=15), engine.state
    assert engine.state.get("state") == "complete"
    assert engine.state.get("end_reason") == "cooling_skip"
    assert engine._frames_done == 0


# ----------------------------------------------- P1-7 af_failure_action=abort

async def test_af_failure_action_abort(sim_hub, temp_store, monkeypatch):
    """af_failure_action='abort': a failed autofocus tears the night down rather
    than silently shooting out-of-focus frames."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig(af_failure_action="abort"))

    from astrodeck.focus import autofocus as af_mod
    from astrodeck.focus.autofocus import AutofocusResult

    async def failing_af(cam, foc, **k):
        return AutofocusResult(False, 0, None, [], "no stars")
    monkeypatch.setattr(engine_mod, "run_autofocus", failing_af)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
        autofocus_first=True,        # initial AF runs in _setup_target
        steps=[ExposureStep(filter="L", exposure_s=0.05, count=2)])]))
    assert await wait_for(lambda: not engine.running, timeout=15), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
