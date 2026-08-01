"""What the preview encoder does with a frame's row length — and what it cannot.

Read this before re-opening #110 (Capture preview drawn squeezed, sheared
diagonally and repeated down the canvas on 2026-07-31, while the FITS written
from the same exposure was clean). One pass has already been spent here. These
tests record what that pass ELIMINATED so the next one starts further along;
none of them is a reproduction of the artefact, because the artefact does not
happen at this layer.

Eliminated, with evidence:

* ``_encode`` itself. The tempting suspect is ``Image.fromarray(arr, mode="L")``,
  which lays the image over the buffer at stride == width without checking the
  buffer is one byte per pixel — hand it uint16 and it really does return the
  sheared, tiled picture (checked against Pillow 12.2). But ``_encode`` has
  always cast to ``uint8`` on the previous line, so ``mode="L"`` merely restated
  the dtype's own mode and never saw a wide buffer, and a 3-plane array raised
  rather than encoding anything. Dropping the redundant ``mode=`` and naming the
  refused shape changed the error text and nothing else; every test below except
  the last one passes unchanged on the code as it stood before that edit, which
  is the point of keeping them.
* the hub carrying a width, a height or an array from one frame to the next —
  see ``test_preview_dims.py``, which publishes two differently-shaped frames
  back to back.

Still open, and the reason #110 is: the frame is already the wrong shape when it
reaches the display path, and every layer below is faithful to it. The one place
an imaging frame gets a row length that is not derived from its own bytes is
``devices/cameras/engine.py::_shape``:

    w, h = roi.w // roi.bin, roi.h // roi.bin
    arr = np.frombuffer(raw, dtype="<u2", count=w * h).reshape((h, w))

Note which direction the failure has to run. ``np.frombuffer`` RAISES on a
buffer shorter than ``count`` and truncates silently only on a longer one, so a
short download cannot shear — it would have thrown. And it cannot even do that
here: both adapters allocate the download themselves at ``_nbytes = w*h*2`` from
the SAME requested width, so ``len(raw)`` matches ``count`` by construction and
``frombuffer`` has nothing left to notice. So the mismatch that can survive is
CONTENT, not length: whatever width the SDK actually applied, the camera fills
the buffer with rows of THAT width and ``_shape`` lays them out at the width we
asked for. Every row then starts a few pixels into the one before it — a
diagonal shear that wraps, which is exactly "at an angle and tiled".

Verified in this repo: neither adapter reads the applied size back.
``player_one.py`` and ``zwo_asi.py`` both compute ``w, h = roi.w // roi.bin,
roi.h // roi.bin``, push it at the SDK, set ``_nbytes = w*h*2`` from the same
numbers, and never ask what the camera settled on.

NOT verified, and the thing to check first: both vendors document a width
alignment (ASI ~%8, POA ~%4) that a requested width can violate. If that is what
happens, it fits the night exactly — the sensor is 6252 wide and every frame
saved that night was bin 1 and clean, while a bin-2 loop asks for 6252 // 2 ==
3126, which is not a multiple of 4 or 8. Do not write that down as the cause
until the rig says so; the last pass wrote down a cause it had not measured and
that is why this file exists. One line of instrumentation settles it: log
``roi.w``, ``roi.bin``, the size the SDK reports after it is set, and
``len(raw)``. The fix is then to shape from the applied size, not the requested
one — or to refuse the exposure and say which width the camera would not give.
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


def _as_encoded(frame: np.ndarray) -> np.ndarray:
    """The 8-bit image ``_encode`` produces from a linear frame, computed here."""
    return (np.clip(frame.astype(np.float64) / 65535.0, 0, 1) * 255).astype(np.uint8)


def test_a_shear_that_happened_upstream_is_encoded_unchanged_and_looks_legitimate():
    """The #110 artefact, built at the layer that can actually produce it.

    This is what ``_shape`` does when the camera filled the buffer with rows of
    ``true_w`` pixels and the shaping code lays them out at ``declared_w``. The
    point of the test is the second half: the encoder passes the damage through
    untouched and reports a size that agrees with the declared width, so the
    published event, the stage's measurement and the bytes all agree with each
    other while the picture is wrong. No check below this line can catch it —
    which is why the fix belongs above it, and why a guard added here would be
    theatre.
    """
    h, true_w, declared_w = 40, 62, 64
    sensor = _row_ramp(h, true_w)
    buf = np.zeros(h * declared_w, dtype=np.uint16)      # the tail is SDK padding
    buf[: sensor.size] = sensor.ravel()
    sheared = buf.reshape(h, declared_w)

    # the damage is real: rows no longer hold one value, they walk by 2 px a row
    assert any(row.min() != row.max() for row in sheared)

    img = _decode(to_png(sheared, stretch=False, max_width=4000))
    _bytes, w, hh = to_jpeg(sheared, max_width=4000)
    assert (w, hh) == (declared_w, h)                    # agrees with the event
    assert np.array_equal(img, _as_encoded(sheared))     # bit-for-bit the input


def test_a_frame_encoded_after_a_differently_shaped_one_comes_back_at_its_own_size():
    # A standing contract, not a repro: the encoder holds no state, and this is
    # what says so. The field trigger for #110 was a bin-2 loop started while the
    # UI still held bin-1 dimensions, so "the previous frame's width" has to be
    # ruled out at every layer, including the ones that turn out to be innocent.
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
    # inspect the rows. A stride mismatch introduced HERE cannot survive this: it
    # makes the row values walk horizontally instead of staying constant. (One
    # introduced upstream survives it — see the first test in this file.)
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


def test_a_frame_that_is_not_two_dimensional_is_refused_by_shape_and_not_by_luck():
    # Pillow refused this before the guard too ("Too many dimensions: 3 > 2" for
    # uint8, a TypeError about the data type for uint16) — the guard is not what
    # stops it, and claiming otherwise is how #110 got closed once already. What
    # it adds is the shape in the message: which caller sent what, from a
    # traceback that otherwise names only PIL.
    rgb = np.zeros((40, 60, 3), dtype=np.uint16)
    with pytest.raises(ValueError) as err:
        to_png(rgb)
    assert "(40, 60, 3)" in str(err.value)
