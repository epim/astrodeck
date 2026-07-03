"""Unit tests for the Alpaca binary ImageBytes decoder
(``AlpacaCamera._parse_imagebytes``, server/astrodeck/devices/alpaca.py).

These tests build synthetic ImageBytes buffers BY HAND, straight from the
ASCOM ImageBytes wire spec, independent of the decoder's own reshape/transpose
logic -- so a bug in the decoder cannot "average out" against a test that
reuses the same math to build its fixture.

Wire layout (little-endian):
  - a 44-byte header of 11 int32s:
      metadata_version, error_number, client_txn, server_txn, data_start,
      image_element_type, transmission_element_type, rank, dim1, dim2, dim3
  - the payload at offset ``data_start`` is the flat row-major memory dump of
    the .NET array ``int[dim1, dim2(, dim3)]``. .NET row-major storage means
    the RIGHTMOST index varies fastest:
      * rank 2: buf[k] = pixel(x = k // dim2, y = k % dim2)
      * rank 3: buf[k] = pixel(x = k // (dim2*dim3),
                               y = (k % (dim2*dim3)) // dim3,
                               plane = k % dim3)
    dim1 is width (X), dim2 is height (Y).

The decoded result must come back as ``arr[y][x]`` (shape (dim2, dim1) ==
(height, width)) -- matching the JSON ImageArray path's ``np.array(Value).T``
convention.
"""
from __future__ import annotations

import struct

import numpy as np
import pytest

from astrodeck.devices.alpaca import AlpacaCamera
from astrodeck.devices.base import DeviceError

_HEADER_FMT = "<11i"
_TX_TYPE = {"int16": 1, "int32": 2, "float64": 3, "float32": 4,
            "uint8": 6, "uint16": 8, "uint32": 9}
_STRUCT_FMT = {"int16": "<h", "int32": "<i", "uint16": "<H", "uint32": "<I"}


def _header(*, error_number: int = 0, rank: int = 2, dim1: int = 0,
            dim2: int = 0, dim3: int = 0, tx_type: int = 8,
            data_start: int = 44) -> bytes:
    """Pack the 44-byte ImageBytes header. Field order pinned by the spec."""
    return struct.pack(
        _HEADER_FMT,
        1,              # metadata_version
        error_number,   # error_number
        11,             # client_txn (arbitrary)
        22,             # server_txn (arbitrary)
        data_start,     # data_start
        0,              # image_element_type (unused by the decoder)
        tx_type,        # transmission_element_type
        rank,           # rank
        dim1, dim2, dim3,
    )


def _payload_rank2(dim1: int, dim2: int, value_fn, dtype: str) -> bytes:
    """Flat row-major dump of int[dim1, dim2]: y (dim2) varies fastest."""
    fmt = _STRUCT_FMT[dtype]
    out = bytearray()
    for x in range(dim1):
        for y in range(dim2):
            out += struct.pack(fmt, value_fn(x, y))
    return bytes(out)


def _payload_rank3(dim1: int, dim2: int, dim3: int, value_fn, dtype: str) -> bytes:
    """Flat row-major dump of int[dim1, dim2, dim3]: plane varies fastest,
    then y, then x."""
    fmt = _STRUCT_FMT[dtype]
    out = bytearray()
    for x in range(dim1):
        for y in range(dim2):
            for plane in range(dim3):
                out += struct.pack(fmt, value_fn(x, y, plane))
    return bytes(out)


def test_rank2_nonsquare_wire_layout_decodes_to_yx():
    """A non-square rank-2 uint16 image: dim1(width)=4, dim2(height)=3, with a
    distinct value per pixel (100*x + y) so any transpose/reshape mixup shows
    up as a wrong value, not just a wrong shape."""
    dim1, dim2 = 4, 3  # width, height

    def value(x, y):
        return 100 * x + y

    header = _header(rank=2, dim1=dim1, dim2=dim2, dim3=0, tx_type=8)
    payload = _payload_rank2(dim1, dim2, value, "uint16")
    buf = header + payload

    arr = AlpacaCamera._parse_imagebytes(buf)

    assert arr.shape == (dim2, dim1)  # (height, width) == (3, 4)
    assert arr.dtype == np.uint16
    for x in range(dim1):
        for y in range(dim2):
            assert arr[y, x] == value(x, y), f"mismatch at x={x}, y={y}"


def test_rank3_single_plane_decodes_to_yx():
    """Rank-3 (dim3=1) uint16 image: the rightmost index (plane) varies
    fastest, then y, then x. With a single plane the decoded [y][x] values
    must match plane 0 exactly."""
    dim1, dim2, dim3 = 4, 3, 1

    def value(x, y, plane):
        return 100 * x + y  # plane is trivial (only 0) but keep it explicit

    header = _header(rank=3, dim1=dim1, dim2=dim2, dim3=dim3, tx_type=8)
    payload = _payload_rank3(dim1, dim2, dim3, value, "uint16")
    buf = header + payload

    arr = AlpacaCamera._parse_imagebytes(buf)

    assert arr.shape == (dim2, dim1)
    for x in range(dim1):
        for y in range(dim2):
            assert arr[y, x] == value(x, y, 0), f"mismatch at x={x}, y={y}"


def test_rank3_multi_plane_takes_plane_zero():
    """With dim3 > 1 (e.g. a colour ImageBytes payload) the decoder must take
    ONLY plane 0 -- plane 1's values must never leak into the result. This
    directly exercises the ``arr[:, :, 0]`` plane selection in the fix."""
    dim1, dim2, dim3 = 3, 2, 2

    def value(x, y, plane):
        # plane 0 and plane 1 are disjoint value ranges so any cross-talk is
        # unmistakable.
        return (1000 if plane == 0 else 9000) + 100 * x + y

    header = _header(rank=3, dim1=dim1, dim2=dim2, dim3=dim3, tx_type=8)
    payload = _payload_rank3(dim1, dim2, dim3, value, "uint16")
    buf = header + payload

    arr = AlpacaCamera._parse_imagebytes(buf)

    assert arr.shape == (dim2, dim1)
    for x in range(dim1):
        for y in range(dim2):
            assert arr[y, x] == value(x, y, 0), f"mismatch at x={x}, y={y}"
            assert arr[y, x] < 9000, "plane-1 value leaked into the decode"


def test_int16_negative_clips_to_zero_uint16():
    """A signed int16 payload (transmission_element_type=1) with a negative
    value must clip to 0, and the returned dtype must be uint16."""
    dim1, dim2 = 2, 2

    values = {
        (0, 0): 10,
        (0, 1): -5,   # must clip to 0
        (1, 0): 20,
        (1, 1): 30,
    }

    def value(x, y):
        return values[(x, y)]

    header = _header(rank=2, dim1=dim1, dim2=dim2, dim3=0, tx_type=1)
    payload = _payload_rank2(dim1, dim2, value, "int16")
    buf = header + payload

    arr = AlpacaCamera._parse_imagebytes(buf)

    assert arr.dtype == np.uint16
    assert arr.shape == (dim2, dim1)
    assert arr[0, 0] == 10
    assert arr[1, 0] == 0        # clipped from -5
    assert arr[0, 1] == 20
    assert arr[1, 1] == 30


def test_error_number_raises_device_error():
    """A non-zero ``error_number`` in the header must raise DeviceError before
    any payload parsing is attempted."""
    buf = _header(error_number=1024, rank=2, dim1=1, dim2=1, dim3=0, tx_type=8)
    with pytest.raises(DeviceError):
        AlpacaCamera._parse_imagebytes(buf)
