"""A preview event must describe the bytes it was published with.

The Capture stage sizes itself from ``display_width``/``display_height`` and
places the star marks, the reticle and the scale bar from ``data_width``/
``data_height``. If either pair survives from an earlier frame — the shape
changing mid-session is the ordinary way that happens, a bin-2 loop started
while the UI still held bin 1 — the browser stretches real bytes into a box that
belongs to a different exposure and nothing downstream can tell.

So: publish two frames of different shapes back to back and hold the whole
contract to the second one's OWN pixels.

These pass on the hub as it stood before #110 was investigated, and that is the
result, not a formality: "a cached width/height in the publish path" was one of
the two named suspects for the sheared 2026-07-31 preview and this rules it out.
The surviving suspect is upstream of the hub — see the hand-off at the top of
``test_processing_stride.py``.
"""
from __future__ import annotations

import asyncio
import io

import numpy as np
from PIL import Image

from astrodeck.hub import Hub


class _Frame:
    """The minimum a linear (sim/Alpaca/native) frame needs to be published."""
    rendered_bytes = None

    def __init__(self, data: np.ndarray):
        self.data = data
        self.exposure_s = 5.0
        self.gain = 100
        self.binning = 1
        self.full_well = 65535
        self.bayer_pattern = None
        self.saved_path = None
        self.data_is_linear = True


def _sky(h: int, w: int, seed: int = 3) -> np.ndarray:
    """Noise plus a handful of stars — enough that the detection/cloud/blob
    passes in the publish path run on something realistic."""
    rng = np.random.default_rng(seed)
    img = rng.normal(400, 6.0, (h, w))
    ys, xs = np.mgrid[0:h, 0:w]
    for fx, fy in ((0.25, 0.3), (0.6, 0.55), (0.8, 0.2), (0.4, 0.75)):
        cx, cy = fx * w, fy * h
        img += 9000.0 * np.exp(-(((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * 2.0 ** 2)))
    return np.clip(img, 0, 65535).astype(np.uint16)


def _bytes_dims(buf: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(buf)) as im:
        return im.width, im.height


def test_a_smaller_second_frame_is_published_at_its_own_dimensions():
    hub = Hub()
    big = _sky(1000, 1600)
    small = _sky(500, 800)                       # what a bin-2 loop would deliver

    a = asyncio.run(hub._publish_preview(_Frame(big)))
    b = asyncio.run(hub._publish_preview(_Frame(small)))

    assert (a["data_width"], a["data_height"]) == (1600, 1000)
    assert (b["data_width"], b["data_height"]) == (800, 500)
    # the 1400px display cap applies to the first frame and not the second, so
    # the two events must not share a display size either
    assert (a["display_width"], a["display_height"]) == (1400, 875)
    assert (b["display_width"], b["display_height"]) == (800, 500)


def test_the_published_display_size_is_the_size_of_the_bytes_actually_served():
    # This is the number the stage draws into. A frame served at one size while
    # the event advertises another is the sheared-preview bug by another route.
    hub = Hub()
    for frame in (_sky(1000, 1600), _sky(500, 800), _sky(300, 300)):
        info = asyncio.run(hub._publish_preview(_Frame(frame)))
        entry = hub.previews[info["id"]]
        assert _bytes_dims(entry.display) == (info["display_width"], info["display_height"])
        # the lossless base is offered for the same frame and must not disagree
        assert entry.lossless is not None
        assert _bytes_dims(entry.lossless) == (info["display_width"], info["display_height"])


def test_the_retained_linear_array_is_the_frame_it_was_published_with():
    # /crop and /render.png cut from entry.linear and the client maps its ROI
    # through data_width/data_height. A retained array from the previous frame
    # would make every crop land somewhere else on the sensor.
    hub = Hub()
    asyncio.run(hub._publish_preview(_Frame(_sky(1000, 1600))))
    small = _sky(500, 800)
    info = asyncio.run(hub._publish_preview(_Frame(small)))
    entry = hub.previews[info["id"]]
    assert entry.linear is not None
    assert entry.linear.shape == (info["data_height"], info["data_width"]) == small.shape
