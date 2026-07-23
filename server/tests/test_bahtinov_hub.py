"""NOV-12 through the hub: arm/disarm toggles state, an armed hub attaches an
additive `bahtinov` field to the preview event, and an UNARMED hub publishes NO
bahtinov field (byte-for-byte-unchanged preview path, mirroring live-stacking)."""
import asyncio

import numpy as np

from astrodeck.hub import Hub
from _bahtinov_synth import make_bahtinov


class _Frame:
    rendered_bytes = None

    def __init__(self, data):
        self.data = data
        self.exposure_s = 2.0
        self.gain = 100
        self.binning = 1
        self.full_well = 65535
        self.bayer_pattern = None
        self.saved_path = None
        self.data_is_linear = True


def _sub(central_shift=0.0):
    return np.clip(make_bahtinov(central_shift=central_shift), 0, 65535).astype(np.uint16)


def test_arm_disarm_toggles_state():
    hub = Hub()
    assert hub.bahtinov is None
    assert hub.arm_bahtinov(tol_px=2.0)["active"] is True
    assert hub.bahtinov == {"tol_px": 2.0, "invert": False}
    assert hub.disarm_bahtinov()["active"] is False
    assert hub.bahtinov is None


def test_armed_attaches_bahtinov_field():
    hub = Hub()
    hub.arm_bahtinov(tol_px=1.5)
    info = asyncio.run(hub._publish_preview(_Frame(_sub(central_shift=0.0))))
    assert "bahtinov" in info
    b = info["bahtinov"]
    assert b["valid"] is True and b["in_focus"] is True
    assert b["offset_px"] is not None and b["tol_px"] == 1.5
    assert len(b["angles_deg"]) == 3


def test_unarmed_publish_has_no_bahtinov():
    hub = Hub()
    info = asyncio.run(hub._publish_preview(_Frame(_sub(central_shift=0.0))))
    assert "bahtinov" not in info


def test_disarm_stops_attaching():
    hub = Hub()
    hub.arm_bahtinov()
    a = asyncio.run(hub._publish_preview(_Frame(_sub())))
    assert "bahtinov" in a
    hub.disarm_bahtinov()
    b = asyncio.run(hub._publish_preview(_Frame(_sub())))
    assert "bahtinov" not in b
