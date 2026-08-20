"""A RUN MUST ASSERT ITS OWN SENSOR TEMPERATURE, EVERY FRAME.

Two nights running, frames were shot at ambient while the operator believed the
camera was at -10:

  2026-08-18  no standing setpoint at all -> 35 frames at ~20C
  2026-08-19  setpoint set and confirmed at 23:27, gone by the next night ->
              63 frames at ~17C before anyone looked

The second one is the worse defect, because the operator DID set it. The warm
ramp cleared `cooling.setpoint_c` on the way down at dawn — deliberately, to
stop a stale order re-cooling the camera at 08:00 — and in doing so threw away
the answer to a different question: what temperature does this rig image at?

Those are two different facts. Stopping the cooler is a live command; the
setpoint is a standing preference, and only the operator changes it.

And even a preference that survives is not enough on its own: a TEC that trips,
a camera that reconnects, or anything else that drops cooling mid-run must not
go unnoticed for three hours. The run holds the intent, so the run checks it.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(cool_to=-10.0, count=6) -> SequencePlan:
    return SequencePlan(
        name="cool", guide=False, dither_every=0, autofocus_every=0,
        cool_to=cool_to,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                            count=count)])])


async def _wait(eng, timeout=40.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if not eng.running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not finish: {eng.state}")


# ---------------------------------------------- the preference must survive

async def test_warming_does_not_forget_what_temperature_the_rig_images_at(sim_hub):
    """The dawn wind-down turns the cooler OFF. It must not also delete the
    operator's setpoint — that is the bug that cost two nights of warm frames."""
    from astrodeck.config import config_store
    await sim_hub.cool_camera(-10.0)
    assert config_store.cfg().cooling.setpoint_c == -10.0, "premise"
    await sim_hub.warm_camera(source="dawn", ramp=False)
    assert config_store.cfg().cooling.setpoint_c == -10.0, (
        "the warm ramp erased the standing setpoint, so the NEXT night opens "
        "with no cooling intent and shoots at ambient")


async def test_the_operator_can_still_clear_it_deliberately(sim_hub):
    from astrodeck.config import config_store
    await sim_hub.cool_camera(-10.0)
    sim_hub._remember_cooling(None)
    assert config_store.cfg().cooling.setpoint_c is None


# ------------------------------------------------- the run must assert it

async def test_a_cooler_that_drops_mid_run_is_re_asserted(sim_hub, monkeypatch):
    """A TEC that trips, or anything else that switches the cooler off, must be
    caught within a frame — not three hours later by a person."""
    cam = sim_hub.devices["camera"]
    calls = []
    real = cam.set_cooler

    async def spy(on, target=None):
        calls.append((on, target))
        return await real(on, target)

    monkeypatch.setattr(cam, "set_cooler", spy)
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(cool_to=-10.0, count=4))
    await asyncio.sleep(0.5)
    await cam.set_cooler(False)          # the TEC drops mid-run
    calls.clear()
    await _wait(eng)
    assert any(on for on, _t in calls), (
        "the cooler went off mid-run and the sequence never turned it back on")


async def test_a_run_with_no_cooling_intent_is_left_alone(sim_hub, monkeypatch):
    """A plan that asks for no cooling must not have cooling imposed on it —
    that would command a TEC nobody asked for, on every night."""
    cam = sim_hub.devices["camera"]
    calls = []

    async def spy(on, target=None):
        calls.append((on, target))

    monkeypatch.setattr(cam, "set_cooler", spy)
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(cool_to=None, count=3))
    await _wait(eng)
    assert not any(on for on, _t in calls), f"cooled an uncooled plan: {calls}"


async def test_a_camera_that_cannot_cool_does_not_stop_the_night(sim_hub, monkeypatch):
    """Not every camera has a TEC. The assertion must degrade, not abort."""
    cam = sim_hub.devices["camera"]
    monkeypatch.setattr(cam, "can_cool", False, raising=False)
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(cool_to=-10.0, count=3))
    await _wait(eng)
    assert (eng.state.get("progress") or {}).get("frames_done", 0) == 3
