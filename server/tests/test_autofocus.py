"""Autofocus converges on the simulator's true focus position."""
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus import run_autofocus


async def test_autofocus_converges():
    parts = build_sim_rig()
    state = parts["_rig"]
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()

    result = await run_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=2)
    assert result.success, result.message
    assert abs(result.best_position - state.best_focus) <= 400
    assert result.best_hfr is not None and result.best_hfr < 3.5
    assert await foc.get_position() == result.best_position
