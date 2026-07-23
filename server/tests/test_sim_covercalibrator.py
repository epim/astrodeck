import numpy as np, pytest
from astrodeck.devices.sim import build_sim_rig, SimCoverCalibrator
from astrodeck.devices.base import CoverCalibrator, CoverState


@pytest.mark.asyncio
async def test_sim_rig_includes_covercalibrator():
    rig = build_sim_rig()
    cc = rig["covercalibrator"]
    assert isinstance(cc, SimCoverCalibrator) and isinstance(cc, CoverCalibrator)


@pytest.mark.asyncio
async def test_on_off_roundtrip_and_state():
    rig = build_sim_rig(); cc = rig["covercalibrator"]; await cc.connect()
    assert await cc.get_calibrator_state() == "off"
    await cc.calibrator_on(120)
    assert await cc.get_brightness() == 120
    assert await cc.get_calibrator_state() == "ready"
    await cc.calibrator_off()
    assert await cc.get_calibrator_state() == "off"


@pytest.mark.asyncio
async def test_cover_open_close():
    rig = build_sim_rig(); cc = rig["covercalibrator"]; await cc.connect()
    assert cc.has_cover is True
    assert await cc.get_cover_state() == CoverState.CLOSED
    await cc.open_cover()
    assert await cc.get_cover_state() == CoverState.OPEN


@pytest.mark.asyncio
async def test_panel_on_brightens_sim_frames():
    # turning the panel on raises the camera's mean ADU (linear in brightness)
    rig = build_sim_rig(); cam = rig["camera"]; cc = rig["covercalibrator"]
    await cam.connect(); await cc.connect()
    dark = (await cam.expose(1.0, 0, 30, 1, light=True)).data.mean()
    await cc.calibrator_on(200)
    lit = (await cam.expose(1.0, 0, 30, 1, light=True)).data.mean()
    assert lit > dark + 50    # the panel term is clearly visible


@pytest.mark.asyncio
async def test_default_off_render_unchanged():
    # flat_illumination defaults to 0 → a frame with the panel never touched is
    # identical to one from a rig that has no covercalibrator interaction.
    rig = build_sim_rig()
    assert rig["_rig"].flat_illumination == 0.0
