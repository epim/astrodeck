"""The calibration walk must not wait forever for a star that is gone.

Measured on the rig 2026-08-08 (RA 23h Dec +30, guide cam 2 s at gain 100):

    t= 6.4  go_west step 1   walk (0.21, 0.42)
    t= 8.8  go_west step 2   walk (10.58, 26.85)
    t=11.1  go_west step 3   walk (3.36, -0.60)
    t=185.3 guide failed: native guider: calibration timed out

Three pulses in eleven seconds, then 174 seconds of nothing, then a sentence
carrying none of it. The engine does not advance its state machine on a frame
where the calibration star is not found (``engine.rs::ingest_calibrating``
returns ``Action::Idle`` on ``star.filter(|s| s.found)``), and the host loop
treated Idle as "a momentary lost star mid-leg; keep exposing" with no bound at
all. Both layers were content to wait out the wall clock in silence.

So: bound the starless wait, and make BOTH failures state what was measured.
"""
from __future__ import annotations

import asyncio
import uuid

import numpy as np
import pytest
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytest.importorskip("astrodeck_native")
pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


async def _guider(tag: str, **cfg):
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    # 0.2 s, matching the other native-guider suites. At 0.05 s the sim's star
    # is too faint to find and the INITIAL star-find fails, so every test here
    # raised "no guide star found" instead of the refusal it was written for —
    # and two of them still went green in a full-file run off other tests'
    # leftovers.
    config = {"image_scale_arcsec": 2.0, "exposure_s": 0.2}
    config.update(cfg)
    g = NativeGuider(cam, tel, config=config,
                     profile_id=f"test-starless-{tag}-{uuid.uuid4().hex[:8]}")
    await g.connect()
    return g, cam, tel


def _blank_after_pulses(g, cam, tel, n_pulses: int, seed: int = 7):
    """The star vanishes once ``n_pulses`` CALIBRATION pulses have landed.

    Gated on ``_phase_hint == "calibrating"`` on BOTH the counter and the
    blanking, and that gate is the point. Two earlier drafts of this helper
    were wrong in ways that still went green:

    * keyed on an exposure count — the number of exposures before the walk
      begins is an implementation detail, so it blanked the pre-calibration
      star-find instead and proved a different refusal;
    * keyed on raw pulses — the guider pulses the mount BEFORE calibration
      (native.py:1161/1331 drive ``pulse_guide`` with no calibration state
      machine), so the counter was already past the threshold when the walk
      started, and again the initial find went blank.

    The second draft passed the whole file and failed when run alone: the first
    test was riding state the others left behind. Run new tests alone.

    Flat NOISE, not zeros: a constant array is refused further upstream as a
    dead sensor, and the case under test is a live camera on a field with no
    star bright enough to find.
    """
    real_expose, real_pulse = cam.expose, tel.pulse_guide
    state = {"pulses": 0, "blanked": 0}
    rng = np.random.default_rng(seed)

    def calibrating() -> bool:
        return getattr(g, "_phase_hint", None) == "calibrating"

    async def pulse(direction, ms):
        if calibrating():
            state["pulses"] += 1
        return await real_pulse(direction, ms)

    async def expose(*a, **kw):
        frame = await real_expose(*a, **kw)
        if calibrating() and state["pulses"] >= n_pulses:
            state["blanked"] += 1
            data = np.asarray(frame.data)
            frame.data = rng.integers(480, 520,
                                      size=data.shape).astype(data.dtype)
        return frame

    tel.pulse_guide = pulse
    cam.expose = expose
    return state


@pytest.mark.asyncio
async def test_a_star_that_never_comes_back_is_named_not_waited_out(monkeypatch):
    import astrodeck.guide.native as native_mod
    # Short bound, and a wall clock far away, so the test proves the STARLESS
    # path fired and not merely that some deadline eventually did.
    monkeypatch.setattr(native_mod, "_CAL_STARLESS_S", 1.0)
    monkeypatch.setattr(native_mod, "_CAL_TIMEOUT_S", 120.0)

    g, cam, tel = await _guider("named")
    _blank_after_pulses(g, cam, tel, 3)

    started = asyncio.get_running_loop().time()
    with pytest.raises(DeviceError) as excinfo:
        await g.start_guiding()
    elapsed = asyncio.get_running_loop().time() - started

    msg = str(excinfo.value)
    assert "lost the calibration star" in msg, msg
    assert elapsed < 60, (
        f"it waited {elapsed:.0f}s — the starless bound did not fire and the "
        f"wall-clock timeout did")
    await g.stop_guiding()


@pytest.mark.asyncio
async def test_the_refusal_carries_the_numbers_it_measured(monkeypatch):
    """steps / walk / starless frames — the four facts that separate the causes.

    Without them "calibration failed" cannot distinguish a mount that never
    moved from a mount that moved nothing from a star that went away.
    """
    import astrodeck.guide.native as native_mod
    monkeypatch.setattr(native_mod, "_CAL_STARLESS_S", 1.0)

    g, cam, tel = await _guider("evidence")
    blanks = _blank_after_pulses(g, cam, tel, 3)

    with pytest.raises(DeviceError) as excinfo:
        await g.start_guiding()
    msg = str(excinfo.value)

    assert "pulse(s)" in msg, msg
    assert "walked" in msg and "px" in msg, msg
    assert "found no star" in msg, msg
    assert blanks["blanked"] > 0, "the field was never actually blanked"
    # …and it must say what to DO. A refusal an operator cannot act on at 2am
    # is the same as no refusal.
    assert "exposure" in msg and "focus" in msg, msg
    await g.stop_guiding()


@pytest.mark.asyncio
async def test_a_brief_flicker_does_not_abort_the_walk(monkeypatch):
    """The positive control. A star DOES flicker; only a star that is gone for
    good may fail the run. Without this the bound could be a one-frame hair
    trigger and both tests above would still pass."""
    import astrodeck.guide.native as native_mod
    monkeypatch.setattr(native_mod, "_CAL_STARLESS_S", 5.0)

    g, cam, tel = await _guider("flicker")
    real_expose, real_pulse = cam.expose, tel.pulse_guide
    state = {"pulses": 0, "blanked": 0}
    rng = np.random.default_rng(11)

    def calibrating() -> bool:
        return getattr(g, "_phase_hint", None) == "calibrating"

    async def pulse(direction, ms):
        if calibrating():
            state["pulses"] += 1
        return await real_pulse(direction, ms)

    async def expose(*a, **kw):
        frame = await real_expose(*a, **kw)
        # Two blank frames once the walk is under way, then the star returns.
        if calibrating() and state["pulses"] >= 2 and state["blanked"] < 2:
            state["blanked"] += 1
            data = np.asarray(frame.data)
            frame.data = rng.integers(480, 520,
                                      size=data.shape).astype(data.dtype)
        return frame

    tel.pulse_guide = pulse
    cam.expose = expose
    await g.start_guiding()               # must NOT raise
    assert state["blanked"] == 2, "the flicker never actually happened"
    assert g.calibration_report is not None, (
        "the walk survived the flicker but produced no calibration")
    await g.stop_guiding()


def test_the_wall_clock_cap_fits_a_real_calibration():
    """The cap must fit HARDWARE, not the sim.

    At 180 s it did not, and it killed a healthy walk on the rig 2026-08-08 —
    76 good pulses into a ~94-step calibration, both RA legs already back at
    the origin. The sim's ``pulse_guide`` only sleeps for the pulse, so a walk
    that costs ~220 s on a real mount cost a few seconds there and the cap
    looked generous.

    The arithmetic, kept here so a future trim has to argue with it: ~94 steps
    at one exposure-plus-pulse each. A 2 s guide exposure is already ~220 s,
    and a faint field wants longer exposures than that.
    """
    from astrodeck.guide.native import _CAL_TIMEOUT_S, _CAL_STARLESS_S
    steps, per_step_s = 94, 2.36
    assert _CAL_TIMEOUT_S > steps * per_step_s, (
        f"{_CAL_TIMEOUT_S}s cannot fit the {steps * per_step_s:.0f}s walk "
        f"measured on the rig")
    # …and the starless bound must stay well under it, or the backstop starts
    # doing the progress-checking again and the specific failure goes back to
    # being reported as a generic timeout.
    assert _CAL_STARLESS_S < _CAL_TIMEOUT_S / 4


@pytest.mark.asyncio
async def test_the_wall_clock_timeout_also_explains_itself(monkeypatch):
    """The other exit from the same loop. It used to be the ONLY one, and it
    said 'calibration timed out' full stop."""
    import astrodeck.guide.native as native_mod
    monkeypatch.setattr(native_mod, "_CAL_TIMEOUT_S", 0.0)

    g, _cam, _tel = await _guider("wallclock")
    with pytest.raises(DeviceError) as excinfo:
        await g.start_guiding()
    msg = str(excinfo.value)
    assert "timed out" in msg
    assert "pulse(s)" in msg and "walked" in msg, msg
    await g.stop_guiding()
