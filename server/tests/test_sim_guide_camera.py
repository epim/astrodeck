import numpy as np
import pytest

from astrodeck.devices.sim import build_sim_rig


@pytest.mark.asyncio
async def test_guide_camera_star_moves_with_pulse():
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    frame0 = await cam.expose(1.0, 100, 30, binning=1)
    # locate brightest pixel
    a0 = np.asarray(frame0.data)
    y0, x0 = np.unravel_index(int(np.argmax(a0)), a0.shape)
    # a WEST pulse must shift the rendered star (closed loop)
    await tel.pulse_guide("west", 800)
    frame1 = await cam.expose(1.0, 100, 30, binning=1)
    a1 = np.asarray(frame1.data)
    y1, x1 = np.unravel_index(int(np.argmax(a1)), a1.shape)
    assert (abs(int(x1)-int(x0)) + abs(int(y1)-int(y0))) >= 2, "star did not respond to pulse"

@pytest.mark.asyncio
async def test_guide_camera_has_single_bright_star():
    rig = build_sim_rig()
    cam = rig["guide_camera"]
    await cam.connect()
    frame = await cam.expose(1.0, 100, 30, binning=1)
    a = np.asarray(frame.data)
    assert a.dtype == np.uint16
    assert a.max() > a.mean() + 500  # a clear star above background
