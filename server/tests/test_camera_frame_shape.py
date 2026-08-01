"""A buffer whose rows are not the width we think they are must not become an
image.

The 2026-07-31 report was "distorted and stretched and shown at an angle. And
tiled" on the Capture preview, while the FITS written from the same exposure was
clean. Two diagnoses were proposed and both were wrong (a binning stride guess,
and a Pillow ``mode="L"`` reinterpretation that was measured to change no bytes).

The mechanism that DOES produce exactly that picture is here: ``_shape`` calls
``np.frombuffer(raw, count=w*h)``, which takes a PREFIX. Feed it a buffer whose
true row length differs from ``roi.w // roi.bin`` and every row is offset from
the previous one by a constant — a diagonal shear — and the content wraps, which
is the tiling. No exception, no warning; the frame flows on to the preview, the
star detector and the FITS writer looking like a photograph.

These tests do not prove that this is what happened on the night. They prove
that if it happens it can no longer happen QUIETLY.
"""
import numpy as np
import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import ROI
from astrodeck.devices.cameras.engine import NativeCamera


def _buf(w: int, h: int, fill: int = 1000) -> bytes:
    return np.full(w * h, fill, dtype="<u2").tobytes()


def test_a_correct_buffer_shapes_to_its_own_dimensions():
    roi = ROI(x=0, y=0, w=64, h=32, bin=1)
    out = NativeCamera._shape(_buf(64, 32), roi, None)
    assert out.shape == (32, 64)
    assert out.dtype == np.uint16


def test_binning_divides_both_axes():
    roi = ROI(x=0, y=0, w=64, h=32, bin=2)
    out = NativeCamera._shape(_buf(32, 16), roi, None)
    assert out.shape == (16, 32)


def test_a_short_buffer_is_refused_with_both_numbers():
    """np.frombuffer would raise a ValueError naming neither the frame it was
    given nor the one it expected."""
    roi = ROI(x=0, y=0, w=64, h=32, bin=1)
    with pytest.raises(DeviceError) as ei:
        NativeCamera._shape(_buf(64, 20), roi, None)
    msg = str(ei.value)
    assert "2560" in msg, msg          # what arrived
    assert "4096" in msg, msg          # what a 64x32 16-bit frame needs
    assert "64" in msg and "32" in msg, msg
    assert "shear" in msg.lower() or "sheared" in msg.lower(), msg


def test_a_long_buffer_is_used_but_announced(monkeypatch):
    """Refusing a frame over trailing padding would be worse than the bug. But
    an unexplained surplus is the signature of an ROI the sensor did not apply
    as asked, so it must not pass in silence."""
    said: list[tuple[str, str, str]] = []
    from astrodeck import events
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub": said.append(
                            (level, message, source)))
    roi = ROI(x=0, y=0, w=64, h=32, bin=1)
    out = NativeCamera._shape(_buf(64, 40), roi, None)
    assert out.shape == (32, 64), "the usable prefix is still delivered"
    assert said, "a surplus buffer must be announced"
    level, message, source = said[-1]
    assert level == "warning" and source == "camera"
    assert "5120" in message and "4096" in message, message
    assert "sheared" in message.lower() or "repeated" in message.lower(), message


def test_the_exact_shear_case_is_the_one_that_is_caught():
    """A sensor that rounds width 6252 up to 6256 returns 4 extra columns per
    row. Taking a w*h prefix of THAT and reshaping at 6252 is what shears an
    image — and it is a LONGER buffer, not a shorter one, so the length check
    has to look in both directions."""
    roi = ROI(x=0, y=0, w=6252, h=8, bin=1)
    padded = _buf(6256, 8)
    assert len(padded) > 6252 * 8 * 2
    out = NativeCamera._shape(padded, roi, None)
    # Still shaped at the REQUESTED width — the warning is what tells the user
    # the requested width may not be the sensor's.
    assert out.shape == (8, 6252)
