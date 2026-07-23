"""Live View through the hub: arming makes preview events carry `livestack`,
frames accumulate across the loop's subs, stop clears it, and an unarmed hub
publishes NO livestack field (byte-for-byte-unchanged preview path)."""
import asyncio
import numpy as np
import pytest
from astrodeck.hub import Hub


class _Frame:
    rendered_bytes = None
    def __init__(self, data):
        self.data = data
        self.exposure_s = 120.0
        self.gain = 100
        self.binning = 1
        self.full_well = 65535
        self.bayer_pattern = None
        self.saved_path = None
        self.data_is_linear = True


def _sub(cx, cy):
    import math
    rng = np.random.default_rng(1)
    img = rng.normal(400, 5.0, (200, 240))
    xs = np.arange(240) - cx
    ys = (np.arange(200) - cy)[:, None]
    img += 300_000.0 * np.exp(-(xs**2 + ys**2) / (2 * 1.6**2)) / (2 * math.pi * 1.6**2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_arming_accumulates_and_stop_clears():
    hub = Hub()
    assert hub.start_live_stack()["active"] is True
    a = asyncio.run(hub._publish_preview(_Frame(_sub(120, 100))))
    assert a["livestack"]["frames"] == 1
    b = asyncio.run(hub._publish_preview(_Frame(_sub(122, 99))))   # 2px drift, accepted
    assert b["livestack"]["frames"] == 2 and b["livestack"]["accepted"] is True
    assert abs(b["livestack"]["integrated_s"] - 240.0) < 1e-6
    hub.stop_live_stack()
    c = asyncio.run(hub._publish_preview(_Frame(_sub(120, 100))))
    assert "livestack" not in c

def test_unarmed_publish_has_no_livestack():
    hub = Hub()
    info = asyncio.run(hub._publish_preview(_Frame(_sub(120, 100))))
    assert "livestack" not in info
