"""Simulator backend behaves like coherent hardware."""
import asyncio

import pytest

from astrodeck.devices.sim import build_sim_rig


@pytest.fixture
async def rig():
    parts = build_sim_rig()
    state = parts.pop("_rig")
    for dev in parts.values():
        await dev.connect()
    parts["_rig"] = state
    return parts


async def test_camera_exposure_returns_frame(rig):
    cam = rig["camera"]
    frame = await cam.expose(0.05, 100, 30, binning=2)
    assert frame.data.shape == (cam.sensor_height // 2, cam.sensor_width // 2)
    assert frame.data.dtype.name == "uint16"
    assert frame.data.max() > frame.data.min()


async def test_camera_abort(rig):
    cam = rig["camera"]
    task = asyncio.create_task(cam.expose(5.0, 100, 30))
    await asyncio.sleep(0.1)
    await cam.abort_exposure()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_slew_then_sync_tightens_pointing(rig):
    tel, state = rig["telescope"], rig["_rig"]
    await tel.slew(10.0, 45.0)
    ra, dec = await tel.get_position()
    # lands close but off by the deliberate pointing error
    assert abs(dec - 45.0) < 0.1
    assert abs(dec - 45.0) > 0.001
    await tel.sync(10.0, 45.0)
    assert state.pointing_error_deg < 0.01


async def test_park_blocks_slew(rig):
    tel = rig["telescope"]
    await tel.park()
    assert await tel.is_parked()
    with pytest.raises(RuntimeError):
        await tel.slew(5.0, 0.0)
    await tel.unpark()
    await tel.slew(5.0, 0.0)


async def test_focuser_moves_and_halts(rig):
    foc = rig["focuser"]
    start = await foc.get_position()
    await foc.move_to(start + 600)
    assert await foc.get_position() == start + 600
    task = asyncio.create_task(foc.move_to(start + 5000))
    await asyncio.sleep(0.05)
    await foc.halt()
    await task
    assert await foc.get_position() < start + 5000


async def test_filterwheel(rig):
    fw = rig["filterwheel"]
    await fw.set_position(4)
    assert await fw.get_position() == 4
    assert fw.filter_names[4] == "Ha"


async def test_switch_ports(rig):
    sw = rig["switch"]
    ports = await sw.get_ports()
    assert len(ports) == 8
    await sw.set_port(4, 75)
    ports = await sw.get_ports()
    assert ports[4].value == 75
    with pytest.raises(RuntimeError):
        await sw.set_port(6, 5)  # read-only sensor


async def test_focus_affects_star_sharpness(rig):
    cam, foc, state = rig["camera"], rig["focuser"], rig["_rig"]
    from astrodeck.imaging import median_hfr

    await foc.move_to(state.best_focus)
    sharp_frame = await cam.expose(0.05, 200, 30, binning=2)
    hfr_sharp, _ = median_hfr(sharp_frame.data)

    await foc.move_to(state.best_focus + 2500)
    soft_frame = await cam.expose(0.05, 200, 30, binning=2)
    hfr_soft, _ = median_hfr(soft_frame.data)

    assert hfr_sharp is not None and hfr_soft is not None
    assert hfr_soft > hfr_sharp
