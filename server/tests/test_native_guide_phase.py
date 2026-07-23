"""NOV-7 (docs/superpowers/specs/2026-07-23-guiding-narration-design.md):
``GuideStats.phase`` + ``NativeGuider._current_phase()`` (design doc §1.3).
Fixture shape mirrors ``test_native_guider_e2e.py`` / ``_dither.py`` /
``_recovery.py`` — the sim rig + a pulse-guide-capable sim mount.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

native = pytest.importorskip("astrodeck_native")  # skip cleanly when wheel absent
pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Mirrors test_native_guider_recovery.py: keep every run's persisted
    calibration in its own tmp_path, never the real server/config/guider/."""
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _profile_id(tag: str) -> str:
    return f"test-phase-{tag}-{uuid.uuid4().hex[:8]}"


def test_fresh_idle_guider_reports_idle_phase():
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"],
                     config={"image_scale_arcsec": 2.0}, profile_id=None)
    st = g.stats()
    assert st.phase == "idle"
    assert st.guiding is False


def test_stats_dict_carries_phase_key():
    """Guards the hub.py:2372 poll path (`stats().__dict__`) — any field added
    to GuideStats must be visible in the raw dict spread, not just the
    dataclass attribute."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"],
                     config={"image_scale_arcsec": 2.0}, profile_id=None)
    d = g.stats().__dict__
    assert "phase" in d
    assert d["phase"] == "idle"


def test_latched_star_loss_reports_lost_phase():
    """``_lost`` wins precedence over everything else in the §1.3 table —
    checked directly (no need to drive a full recovery scenario for this
    unit-level precedence assertion; test_native_guider_recovery.py already
    covers the end-to-end star-loss timeline)."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"],
                     config={"image_scale_arcsec": 2.0}, profile_id=None)
    g._lost = True
    assert g.stats().phase == "lost"


@pytest.mark.asyncio
async def test_native_guider_start_guiding_reaches_guiding_phase():
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=_profile_id("guiding"))
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    # give the loop a moment to land a frame past lock-establishment
    await asyncio.sleep(1.0)
    st = g.stats()
    assert st.guiding
    assert st.phase == "guiding"

    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_native_guider_dither_reports_settling_phase_mid_flight():
    """Mirrors test_native_guider_dither.py's mid-flight polling: while the
    engine's settle window is open (fast recenter + dwell, dossier §11.2/§12)
    the composed phase must read "settling", not "guiding"/"finding"."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=_profile_id("settling"))
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    await asyncio.sleep(1.0)

    dither_task = asyncio.create_task(g.dither(3.0))
    saw_settling = False
    for _ in range(50):  # up to ~5s of polling
        await asyncio.sleep(0.1)
        if g.stats().phase == "settling":
            saw_settling = True
            break
        if dither_task.done():
            break
    assert saw_settling, "expected stats().phase == 'settling' while the dither settle window is open"

    await asyncio.wait_for(dither_task, timeout=90.0)
    st = g.stats()
    assert st.guiding
    assert st.phase == "guiding"

    await g.stop_guiding()
    await g.disconnect()
