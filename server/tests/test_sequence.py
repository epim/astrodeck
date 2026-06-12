"""Sequence engine: ordering, file output, pause/abort safety."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def small_plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="test",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[
                ExposureStep(filter="Ha", exposure_s=0.05, gain=100, count=2),
                ExposureStep(filter="OIII", exposure_s=0.05, gain=100, count=2),
            ],
        )],
        guide=False, dither_every=0, autofocus_every=0,
    )
    return SequencePlan(**(defaults | overrides))


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


async def test_sequence_completes_and_saves(sim_hub, tmp_path):
    engine = SequenceEngine(sim_hub)
    engine.start(small_plan())
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    fits_files = list(tmp_path.rglob("*.fits"))
    assert len(fits_files) == 4
    assert engine.state["progress"]["frames_done"] == 4
    # filter wheel ends on the last step's filter
    assert sim_hub.sim_rig.filter_slot == 5  # OIII


async def test_sequence_pause_resume(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(small_plan())
    await asyncio.sleep(0.3)
    engine.pause()
    await asyncio.sleep(1.0)
    frames_at_pause = engine.state.get("progress", {}).get("frames_done", 0)
    await asyncio.sleep(1.0)
    # paused: at most one in-flight frame finishes, then no more
    assert engine.state["progress"]["frames_done"] <= frames_at_pause + 1
    engine.resume()
    assert await wait_for(lambda: engine.state.get("state") == "complete")


async def test_sequence_abort_is_safe(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(small_plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        center=False, autofocus_first=False,
        steps=[ExposureStep(filter="L", exposure_s=5.0, gain=100, count=50)],
    )]))
    await asyncio.sleep(0.5)
    await engine.abort()
    assert not engine.running
    assert engine.state["state"] == "aborted"


async def test_dither_fires(sim_hub):
    engine = SequenceEngine(sim_hub)
    await sim_hub.guider.start_guiding()
    engine.start(small_plan(guide=True, dither_every=2))
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=60)
    assert sim_hub.guider.dither_count >= 1


async def test_goto_and_center_converges(sim_hub):
    result = await sim_hub.goto_and_center(10.0, 30.0, solve_exposure_s=0.05)
    assert result["centered"], result
    ra, dec = await sim_hub.require("telescope").get_position()
    assert abs(dec - 30.0) < 0.05
