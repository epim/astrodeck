"""The preview encoder must lay every frame out at the frame's OWN row length.

On 2026-07-31 the Capture preview drew a frame squeezed, sheared diagonally and
repeated down the canvas while the FITS written from the same exposure was
clean. That is exactly what a wrong row stride looks like: rows read back at a
width other than the one the pixels were written with, so every row starts part
of the way into the one before it.

The encode step is the only place in the preview path where raw bytes meet a
separately-supplied row length, and PIL does not police it — ``Image.fromarray``
with an explicit ``mode="L"`` lays the image over the buffer at stride == width
and never checks that the buffer is one byte per pixel. These tests pin the
three properties that keep that impossible: the encoder carries nothing between
frames, it round-trips a frame byte-exactly whatever the array's memory order,
and it refuses a buffer it cannot lay out one byte per pixel instead of encoding
something sheared and returning HTTP 200.
"""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from astrodeck.imaging.processing import to_jpeg, to_png


def _row_ramp(h: int, w: int) -> np.ndarray:
    """A frame in which every row is one constant value, rows increasing.

    Chosen because it is the sharpest possible shear detector: read at the right
    width every decoded row is flat, and read at any other width the row values
    walk across each row instead.
    """
    vals = np.round(np.linspace(0, 65535, h)).astype(np.uint16)
    return np.repeat(vals[:, None], w, axis=1)


def _decode(buf: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(buf)))


def test_a_frame_encoded_after_a_differently_shaped_one_comes_back_at_its_own_size():
    # The field trigger: a bin-2 loop started while the UI still held bin-1
    # dimensions. Whatever the previous frame was, this frame's bytes must be
    # this frame's shape.
    wide = _row_ramp(300, 900)
    narrow = _row_ramp(150, 450)

    _a, aw, ah = to_jpeg(wide, max_width=2000)
    _b, bw, bh = to_jpeg(narrow, max_width=2000)
    assert (aw, ah) == (900, 300)
    assert (bw, bh) == (450, 150)

    # ...and byte-for-byte identical to encoding it with no history at all, so
    # no cache, no reused buffer and no remembered dimension can exist.
    alone, cw, ch = to_jpeg(narrow, max_width=2000)
    assert (cw, ch) == (bw, bh)
    assert alone == _b


def test_every_decoded_row_is_flat_so_no_row_started_inside_the_previous_one():
    # Encode the second of two different-width frames losslessly at 1:1 and
    # inspect the rows. A stride mismatch cannot survive this: it makes the row
    # values walk horizontally instead of staying constant.
    to_png(_row_ramp(64, 208), stretch=False, max_width=4000)   # a wider frame first
    frame = _row_ramp(64, 96)
    img = _decode(to_png(frame, stretch=False, max_width=4000))

    assert img.shape == frame.shape
    for y, row in enumerate(img):
        assert row.min() == row.max(), f"row {y} is not flat — the row length is wrong"
    assert np.all(np.diff(img[:, 0].astype(int)) > 0)


def test_a_fortran_ordered_frame_encodes_identically_to_a_c_ordered_one():
    # The Alpaca download transposes ([x][y] -> [y][x]), so frame.data really can
    # reach the encoder Fortran-contiguous. Its shape is right and its strides are
    # not; an encoder that handed the raw buffer to PIL would transpose-and-shear
    # it. Same pixels in, same PNG out.
    frame = _row_ramp(48, 112)
    fortran = np.asfortranarray(frame)
    assert not fortran.flags.c_contiguous
    assert to_png(fortran, stretch=False, max_width=4000) == \
        to_png(frame, stretch=False, max_width=4000)


def test_the_size_the_hub_publishes_is_measured_off_the_encoded_bytes():
    # display_width/display_height go straight into the preview event and the
    # client sizes its stage from them. If they were predicted rather than
    # measured, the browser would stretch the real bytes into the wrong box.
    for h, w in ((900, 1600), (300, 900)):
        buf, rw, rh = to_jpeg(_row_ramp(h, w))       # default 1400px cap
        with Image.open(io.BytesIO(buf)) as im:
            assert (im.width, im.height) == (rw, rh)
        assert rw == min(w, 1400)


def test_a_frame_that_is_not_two_dimensional_is_refused_rather_than_sheared():
    # A colour/3-plane array under a one-byte-per-pixel layout is the textbook
    # way to produce the tiled-and-angled artefact. Refusing names the shape;
    # encoding it would have produced a plausible-looking, wrong picture.
    rgb = np.zeros((40, 60, 3), dtype=np.uint16)
    with pytest.raises(ValueError) as err:
        to_png(rgb)
    assert "(40, 60, 3)" in str(err.value)
