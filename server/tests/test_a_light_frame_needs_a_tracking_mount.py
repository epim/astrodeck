"""A LIGHT FRAME ON A MOUNT THAT IS NOT TRACKING IS A STREAK.

2026-08-19, 00:54: the AM5 hit its own meridian limit five minutes after the
engine declined a flip, and stopped tracking. The run kept going until 01:56 —
21 frames, three of every filter, 60s exposures on a stationary mount, all
accepted, rejected=0. It would have run to dawn.

Every layer that could have caught it was off or looking elsewhere:
`max_guide_rms` is 0/off so RMS 4085 rejected nothing, `require_guiding` is
false, and fifteen consecutive dither-settle failures were each treated as an
isolated hiccup. All of those are thresholds someone has to have configured.

This is not a threshold. A light frame needs a tracking mount, always, and the
mount already knows the answer. Calibration frames do not care, so they are not
gated.
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


def _plan(count=6, frame_type="Light") -> SequencePlan:
    return SequencePlan(
        name="tracking", guide=False, dither_every=0, autofocus_every=0,
        # M42's hour angle depends on when the suite runs, and since 2026-08-22
        # the flip fires on a 10-minute lead and stays armed until
        # lead + FLIP_ARM_MARGIN_MIN past transit — so for a slice of the day
        # these runs would flip mid-test. This file is about the tracking gate,
        # not the meridian.
        meridian_flip=False,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                            count=count, frame_type=frame_type)])])


async def _wait(eng, timeout=40.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if not eng.running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not finish: {eng.state}")


async def test_a_mount_that_stops_tracking_MID_RUN_stops_the_run(sim_hub, monkeypatch):
    """The real shape: tracking is fine at start and dies at 00:54.

    A first version of this test simply reported tracking=False from the
    beginning, and passed with 0 frames captured — because the engine enables
    tracking BEFORE it slews, so the run died at startup instead. That proved
    nothing about a mid-run gate. Tracking must be healthy first, then stop.
    """
    tel = sim_hub.devices["telescope"]
    state = {"on": True, "reads": 0}

    async def get_tracking():
        state["reads"] += 1
        if state["reads"] > 3:                  # a few frames in, the limit bites
            state["on"] = False
        return state["on"]

    async def refuse(on):                       # the AM5 at its meridian limit
        if not state["on"] and on:
            raise RuntimeError("tracking on rejected (reply '0')")
        state["on"] = bool(on)

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "set_tracking", refuse)

    eng = SequenceEngine(sim_hub)
    eng.start(_plan(count=12))
    await _wait(eng)
    done = (eng.state.get("progress") or {}).get("frames_done", 0)
    assert 0 < done < 12, (
        f"captured {done}/12: the run must start normally and then STOP when the "
        "mount stops tracking — every frame after that point is a streak")


async def test_it_tries_to_switch_tracking_back_on_first(sim_hub, monkeypatch):
    """A mount that merely got switched off should be recovered, not abandoned."""
    tel = sim_hub.devices["telescope"]
    state = {"on": False, "attempts": 0}

    async def get_tracking():
        return state["on"]

    async def set_tracking(on):
        state["attempts"] += 1
        state["on"] = bool(on)

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)

    eng = SequenceEngine(sim_hub)
    eng.start(_plan(count=3))
    await _wait(eng)
    assert state["attempts"] >= 1, "never even tried to resume tracking"
    assert (eng.state.get("progress") or {}).get("frames_done", 0) == 3, (
        "recovered the mount but abandoned the target anyway")


async def test_calibration_frames_do_not_need_tracking(sim_hub, monkeypatch):
    """Darks and bias are shot on a parked mount on purpose."""
    tel = sim_hub.devices["telescope"]

    async def not_tracking():
        return False

    monkeypatch.setattr(tel, "get_tracking", not_tracking)
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(count=3, frame_type="Dark"))
    await _wait(eng)
    assert (eng.state.get("progress") or {}).get("frames_done", 0) == 3, (
        "gated a dark on tracking — calibration frames are shot parked")


async def test_an_unreadable_tracking_state_is_not_a_verdict(sim_hub, monkeypatch):
    """A driver that cannot answer must not end the night. Unknown is not False."""
    tel = sim_hub.devices["telescope"]

    async def boom():
        raise RuntimeError("link down")

    monkeypatch.setattr(tel, "get_tracking", boom)
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(count=3))
    await _wait(eng)
    assert (eng.state.get("progress") or {}).get("frames_done", 0) == 3, (
        "an unreadable mount stopped a run that was probably fine")


async def test_a_run_can_start_against_a_mount_pinned_at_its_limit(sim_hub, monkeypatch):
    """THE ACTION THAT CLEARS THE LIMIT SAT BEHIND THE ONE THAT COULD NOT SUCCEED.

    `_run` enabled tracking BEFORE it slewed. A ZWO AM5 parked against its own
    meridian limit answers `tracking on rejected (reply '0')`, so the run died
    at startup — and the slew, which is the one thing that would have moved the
    mount off the limit, never happened. Measured on the rig 2026-08-19 02:00:
    two attempts to restart the night both failed instantly, and the only
    recovery left was a manual park.

    A refusal before the slew is now survivable: slew first, ask again after.
    """
    tel = sim_hub.devices["telescope"]
    state = {"slewed": False, "on": False, "refusals": 0}
    real_slew = tel.slew

    async def slew(ra, dec):
        state["slewed"] = True                  # moving off the limit
        return await real_slew(ra, dec)

    async def set_tracking(on):
        if on and not state["slewed"]:
            state["refusals"] += 1
            raise RuntimeError("tracking on rejected (reply '0')")
        state["on"] = bool(on)

    async def get_tracking():
        return state["on"]

    monkeypatch.setattr(tel, "slew", slew)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "get_tracking", get_tracking)

    eng = SequenceEngine(sim_hub)
    eng.start(_plan(count=3))
    await _wait(eng)
    assert state["refusals"] >= 1, "premise: the mount refused before the slew"
    assert state["slewed"], (
        "the run never slewed — it died on the tracking refusal, so the mount "
        "stayed pinned at its limit and no restart could ever recover it")
    assert (eng.state.get("progress") or {}).get("frames_done", 0) == 3, (
        f"state={eng.state.get('state')} detail={eng.state.get('detail')}")
