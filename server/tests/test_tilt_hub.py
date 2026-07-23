"""Integration: frame_tilt over a real synthetic star field through the same
detect_stars -> star_marks pipeline the hub feeds it from (PRO-13 Task 2).

Not a hub/websocket test — proves the ``info["tilt"]`` block's *shape* is
well-formed off real detection output, mirroring how hub.py:frame_tilt(marks,
info["data_width"], info["data_height"]) is called.
"""
import numpy as np

from astrodeck.imaging import detect_stars, frame_tilt, star_marks


def test_frame_tilt_block_shape_from_real_detection():
    rng = np.random.default_rng(3)
    img = rng.normal(500, 8, (900, 900))
    ys, xs = np.mgrid[0:900, 0:900]
    for _ in range(120):
        px, py = rng.uniform(40, 860), rng.uniform(40, 860)
        img += 2.5e5 * np.exp(-((xs - px) ** 2 + (ys - py) ** 2) / (2 * 1.6 ** 2))
    marks = star_marks(detect_stars(img))
    t = frame_tilt(marks, 900, 900)
    assert t is not None
    assert t["cols"] == 3 and t["rows"] == 3 and len(t["zones"]) == 9
    assert t["pattern"] in {"uniform", "tilt", "coma", "tracking"}
    assert isinstance(t["severity"], float)
    for z in t["zones"]:
        assert set(z) == {"hfr", "ecc", "theta", "n"}


def test_frame_tilt_absent_key_when_none():
    # Mirrors the hub's guard: `if tilt is not None: info["tilt"] = tilt` — a
    # too-sparse marks list must simply omit the key, never emit a null/partial
    # block.
    info: dict = {"data_width": 900, "data_height": 900}
    tilt = frame_tilt([{"x": 10.0, "y": 10.0, "hfr": 2.0}],
                      info["data_width"], info["data_height"])
    if tilt is not None:
        info["tilt"] = tilt
    assert "tilt" not in info
