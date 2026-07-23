"""EGAIN threading: e-/ADU onto CameraFrame per backend (PRO-2 F-B / ruling 5)."""
import numpy as np

from astrodeck.devices.cameras.engine import _egain_from_caps


class _Caps:
    def __init__(self, extra):
        self.extra = extra


def test_egain_from_caps_reads_extra():
    assert _egain_from_caps(_Caps({"egain": 0.8})) == 0.8


def test_egain_from_caps_none_when_absent():
    assert _egain_from_caps(_Caps({})) is None
    assert _egain_from_caps(None) is None


async def test_sim_expose_sets_egain():
    from astrodeck.devices.sim import SimCamera, SimRig
    cam = SimCamera(SimRig())
    await cam.connect()
    frame = await cam.expose(0.05, 100, 30, binning=1)
    assert frame.egain_e_per_adu == SimCamera.SIM_EGAIN
    assert frame.egain_e_per_adu is not None
