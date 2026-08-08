"""Long operations narrate themselves (2026-08-07).

Three operations used to render as a stuck busy button for their whole
duration: a goto's centering solves (up to 3 s shutter + 15 s ASTAP, up to
three times per goto), the guide calibration walk (~30-60 s of pulses), and
each autofocus point (exposure + 2-4 s of measurement). Each now publishes
what it is doing THIS second on the bus channel its screen already reads —
the same `activity` idiom the polar driver shipped on 2026-08-07.

Plus the guide camera's frame settings: a constructor-frozen 2.0 s exposure
no UI could reach becomes a persisted, live-applied setting — the same
no-move-at-all trap the polar solve settings closed (#156's shape).
"""
import asyncio

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.providers import NATIVE_AVAILABLE

native_only = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                 reason="native wheel absent")


def _drain(q, ev_type):
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == ev_type:
            out.append(ev.data)
    return out


# ------------------------------------------------------- centering solves

@pytest.mark.asyncio
async def test_a_centering_solve_narrates_exposing_solving_then_clears(sim_hub):
    q = bus.subscribe()
    try:
        await sim_hub.solve_and_sync(0.05)
    finally:
        bus.unsubscribe(q)
    acts = [e for e in _drain(q, "mount") if e.get("action") == "solve_activity"]
    seq = [a.get("activity") for a in acts]
    assert seq == ["exposing", "solving", None], seq
    assert acts[0].get("exposure_s") == 0.05, "the ring needs the fill clock"


@pytest.mark.asyncio
async def test_a_failed_solve_still_clears_the_activity(sim_hub, monkeypatch):
    """A thrown solve must not leave 'solving' blinking over an idle rig."""
    class _NoStars:
        name = "broken"
        async def solve(self, path, **kw):
            class _R:
                success = False
                message = "no stars"
            return _R()

    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: _NoStars())
    q = bus.subscribe()
    try:
        with pytest.raises(DeviceError):
            await sim_hub.solve_and_sync(0.05)
    finally:
        bus.unsubscribe(q)
    acts = [e.get("activity") for e in _drain(q, "mount")
            if e.get("action") == "solve_activity"]
    assert acts and acts[-1] is None, acts


# --------------------------------------------------- guide calibration walk

async def _sim_guider():
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    from astrodeck.guide.native import NativeGuider
    # 0.2 s, matching the other native-guider suites. At 0.05 s the sim's star
    # is faint enough that the INITIAL star-find intermittently returns nothing
    # and start_guiding raises "no guide star found" — measured here on
    # 2026-08-08 at roughly one failure in four runs, on the code BEFORE the
    # calibration changes as well as after, so it is the fixture and not the
    # guider. A test that fails a quarter of the time teaches people to re-run
    # CI instead of reading it.
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=None)
    await g.connect()
    return g


@native_only
@pytest.mark.asyncio
async def test_the_calibration_walk_is_narrated_step_by_step():
    g = await _sim_guider()
    q = bus.subscribe()
    try:
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    finally:
        bus.unsubscribe(q)
        await g.stop_guiding()
        await g.disconnect()

    # TWO kinds of `cal` block ride this stream now (2026-08-08). A STEP is a
    # pulse the engine asked for; a STALL tick carries `starless` and reports
    # that the walk is standing still because the star cannot be found — it
    # repeats the step count it is stuck on rather than advancing it. Counting
    # both as steps made this test fail whenever the sim happened to produce an
    # unmeasurable frame, which is timing-dependent: it passed alone and failed
    # in a full parallel run.
    blocks = [e["cal"] for e in _drain(q, "guide") if e.get("cal")]
    cals = [c for c in blocks if not c.get("starless")]
    assert cals, "not one calibration step reached the bus"
    first, last = cals[0], cals[-1]
    # each step names its leg, pulse and ordinal — the chip's whole diet
    assert first["leg"] and first["dir"], first
    assert first["ms"] > 0, "the pulse ring needs a duration"
    assert first["step"] == 1 and last["step"] == len(cals), (
        "steps must count monotonically")
    # ...and the star's measured walk, from its origin, for the crosshair plot
    assert last["walk"][0] == [0.0, 0.0], last["walk"][:3]
    assert len(last["walk"]) > 3, "the walk never accumulated positions"


# --------------------------------------------------- guide camera settings

@native_only
@pytest.mark.asyncio
async def test_guide_camera_settings_apply_to_the_next_frame():
    g = await _sim_guider()
    try:
        eff = g.set_camera_settings(exposure_s=1.5, gain=250)
        assert eff["exposure_s"] == 1.5 and eff["gain"] == 250
        assert g.camera_settings()["exposure_s"] == 1.5
        # idle: binning applies freely (the NEXT calibration measures its scale)
        assert g.set_camera_settings(binning=2)["binning"] == 2
    finally:
        await g.disconnect()


@native_only
@pytest.mark.asyncio
async def test_binning_is_refused_while_the_guider_is_active():
    """The calibration measured px/ms in the CURRENT binning's pixels; changing
    it under an active session silently rescales every correction."""
    g = await _sim_guider()
    try:
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        with pytest.raises(DeviceError):
            g.set_camera_settings(binning=2)
        # exposure and gain stay live — that is the point of the dial
        assert g.set_camera_settings(exposure_s=0.1)["exposure_s"] == 0.1
    finally:
        await g.stop_guiding()
        await g.disconnect()


@native_only
@pytest.mark.asyncio
async def test_build_native_guider_reads_the_persisted_camera_settings():
    """The dial's PUT persists to config; a guider built after a reconnect must
    come up with those values, not the historical constructor constants."""
    from astrodeck.config import config_store
    from astrodeck.guide.native import build_native_guider
    gc = config_store.cfg().guide.model_copy(
        update={"exposure_s": 3.5, "gain": 222, "binning": 2})
    config_store.set_guide(gc)

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = build_native_guider(cam, tel)
    assert g is not None
    s = g.camera_settings()
    assert (s["exposure_s"], s["gain"], s["binning"]) == (3.5, 222, 2), s


# ------------------------------------------------------- autofocus points

@native_only
@pytest.mark.asyncio
async def test_the_af_sweep_narrates_each_point(monkeypatch):
    import astrodeck.focus.native as N
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    q = bus.subscribe()
    try:
        await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                     step=350, steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)
    running = [e for e in _drain(q, "focus") if e.get("state") == "running"]
    acts = [e.get("activity") for e in running if "activity" in e]
    assert "exposing" in acts and "measuring" in acts, acts[:10]
    # the narration names which point of how many
    indexed = [e for e in running if e.get("point_index") is not None]
    assert indexed, "no point ordinals published"
    assert all(e.get("points_planned") == 9 for e in indexed), (
        "the requested count is the denominator the chip prints")
    # an accepted point's publish clears the activity — no 'measuring' left
    # blinking between points
    accepted = [e for e in running if e.get("points") and "activity" in e
                and e.get("activity") is None]
    assert accepted, "accepted points never cleared the activity"
