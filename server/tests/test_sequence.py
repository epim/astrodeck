"""Sequence engine: ordering, file output, pause/abort safety."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # These tests slew to FIXED sky targets (M42, etc.). The W1.10 sun-exclusion
    # cone is ON by default and (correctly) blocks a slew whenever the real Sun is
    # within 30 deg of the target -- which depends on today's date and would make
    # these sequence-MECHANICS tests flaky a few weeks a year. The cone itself is
    # covered by test_sun_guard.py; here we disarm it so the mechanics are tested
    # date-independently. monkeypatch restores the field after the test.
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    # Force the legacy fast deterministic SimGuider (P2-T3 flip escape hatch):
    # these sequence-MECHANICS tests spy on the sim guider (``dither_count``,
    # ``_guiding``) and must not pay the native guider's real calibration walk —
    # the native guiding path itself is covered by test_native_guider_*.py.
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
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


# --------------------------------------------------- new autonomous-night features

async def test_cooling_before_lights(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(small_plan(cool_to=-10.0))
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert sim_hub.devices["camera"]._cooler_on


async def test_calibration_target_darks(sim_hub, tmp_path):
    engine = SequenceEngine(sim_hub)
    plan = SequencePlan(name="cal", guide=False, targets=[Target(
        name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
        steps=[ExposureStep(filter=None, exposure_s=0.05, count=3, frame_type="Dark")])])
    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert len(list(tmp_path.rglob("Dark_*.fits"))) == 3
    # calibration must not move the mount off its parked pole
    assert not await sim_hub.require("telescope").is_slewing()


async def test_filter_offsets_move_focuser(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(apply_filter_offsets=True)
    fw, foc = sim_hub.devices["filterwheel"], sim_hub.devices["focuser"]
    start_pos = await foc.get_position()
    start_slot = await fw.get_position()
    await engine._apply_filter(ExposureStep(filter="Ha", exposure_s=0.05, count=1))
    expected = fw.filter_offsets[fw.filter_names.index("Ha")] - fw.filter_offsets[start_slot]
    assert (await foc.get_position()) - start_pos == expected


async def test_refocus_on_temp_delta(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(refocus_on_temp_delta_c=1.0)
    engine._last_focus_temp = await sim_hub.devices["focuser"].get_temperature()
    assert not await engine._refocus_due()       # no drift
    engine._last_focus_temp += 5.0               # pretend last focus was 5°C warmer
    assert await engine._refocus_due()


async def test_guiding_recovery(sim_hub):
    g = sim_hub.guider
    await g.start_guiding()
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    g._guiding = False                            # simulate lost star
    await engine._maybe_recover_guiding()
    assert g.stats().guiding                      # recovered


async def test_resume_after_abort(sim_hub, tmp_path):
    plan = SequencePlan(name="r", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
                            autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05, count=6)])])
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: engine._frames_done >= 2)
    await engine.abort()
    from astrodeck.sequence.session import session_store
    s = session_store.recoverable()
    assert s is not None and s.status == "dormant"
    done_before = sum(s.done_map().values())
    assert 1 <= done_before < 6

    engine2 = SequenceEngine(sim_hub)
    engine2.start(s.plan, session=s)
    assert await wait_for(lambda: engine2.state.get("state") == "complete")
    assert engine2._frames_done == 6              # resumed, did not redo all 6
    assert session_store.recoverable() is None    # nothing dormant remains
    assert session_store.load(s.id).status == "complete"
    assert len(session_store.load(s.id).nights) == 2   # one report per night
