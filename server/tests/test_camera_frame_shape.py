"""A buffer whose rows are not the width we think they are must not become an
image.

The 2026-07-31 report was "distorted and stretched and shown at an angle. And
tiled" on the Capture preview, while the FITS written from the same exposure was
clean. Two diagnoses were proposed and both were wrong (a binning stride guess,
and a Pillow ``mode="L"`` reinterpretation that was measured to change no bytes).

A third candidate is here, and it is a candidate, not a verdict: ``_shape``
calls ``np.frombuffer(raw, count=w*h)``, which takes a PREFIX. Feed it a buffer
whose true row length differs from ``roi.w // roi.bin`` and every row is offset
from the previous one by a constant — a diagonal shear — and the content wraps,
which is the tiling. No exception, no warning; the frame flows on to the
preview, the star detector and the FITS writer looking like a photograph. That
picture is rendered from a real sky frame in ``test_camera_roi_shear.py``, where
it matches the words of the report but contradicts its other half — the damage
would be in the FITS too, and the FITS was reported clean.

READ THIS BEFORE TRUSTING THE TESTS BELOW. The length checks they cover cannot
catch the #110 artefact on either shipped adapter, and the module used to imply
they could. Both adapters allocate the download buffer themselves and hand its
size to the SDK; the SDK fills the front of it and reports success for any
buffer that is big ENOUGH; ``read_frame`` returns the whole allocation. So
``len(raw)`` equals the size the adapter computed, by construction, whatever the
sensor did — the wrong row length arrives at exactly the right byte count. What
catches it is the geometry read-back added to the adapters
(``CameraAdapter.applied_roi``), covered in ``test_camera_roi_readback.py``.

What the guard here is worth: it is the net under an adapter whose buffer size
is not its own — a future brand that returns whatever the SDK hands it — and it
names both numbers instead of leaving a ValueError inside numpy.
"""
import numpy as np
import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import ROI
from astrodeck.devices.cameras.engine import NativeCamera


def _buf(w: int, h: int, fill: int = 1000) -> bytes:
    return np.full(w * h, fill, dtype="<u2").tobytes()


@pytest.fixture(autouse=True)
def said(monkeypatch) -> list[tuple[str, str, str]]:
    """Every log line this module provokes, and NONE of them on the real bus.

    Two of these tests hand ``_shape`` a surplus buffer, which it announces.
    Left on the process-global bus those entries land in ``bus._history``, a
    ``deque(maxlen=200)`` shared by the whole test process — and several suites
    read new lines as ``bus.log_history[at:]`` with ``at = len(bus.log_history)``,
    a slice that is empty forever once the ring reaches its cap. A module that
    leaks entries turns other modules' tests red without touching their code."""
    out: list[tuple[str, str, str]] = []
    from astrodeck import events
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub": out.append(
                            (level, message, source)))
    return out


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


def test_a_long_buffer_is_used_but_announced(said):
    """Refusing a frame over trailing padding would be worse than the bug. But
    an unexplained surplus is the signature of an ROI the sensor did not apply
    as asked, so it must not pass in silence."""
    roi = ROI(x=0, y=0, w=64, h=32, bin=1)
    out = NativeCamera._shape(_buf(64, 40), roi, None)
    assert out.shape == (32, 64), "the usable prefix is still delivered"
    assert said, "a surplus buffer must be announced"
    level, message, source = said[-1]
    assert level == "warning" and source == "camera"
    assert "5120" in message and "4096" in message, message
    assert "sheared" in message.lower() or "repeated" in message.lower(), message


def test_a_surplus_buffer_is_still_shaped_at_the_requested_width():
    """A buffer with 4 extra columns' worth of bytes per row is used, at the
    requested width, and only the warning says the width may not be the
    sensor's. Note what this is NOT: evidence about #110. A sensor that rounded
    a width UP would need MORE bytes than the adapter allocated, and the SDK
    fails a too-small buffer outright — so the surplus direction is the one the
    shipped adapters cannot even reach, and the deficit direction never changes
    the length at all."""
    roi = ROI(x=0, y=0, w=6252, h=8, bin=1)
    padded = _buf(6256, 8)
    assert len(padded) > 6252 * 8 * 2
    out = NativeCamera._shape(padded, roi, None)
    assert out.shape == (8, 6252)


def test_the_applied_geometry_wins_over_the_requested_one(said):
    """``_layout_roi`` is where the row length is actually decided now. When the
    adapter can say what the sensor applied, that is what the buffer is cut at —
    the request is only ever a hypothesis about the frame."""
    requested = ROI(x=0, y=0, w=6252, h=4176, bin=2)     # binned 3126x2088
    applied = ROI(x=0, y=0, w=6248, h=4176, bin=2)       # binned 3124x2088

    assert NativeCamera._layout_roi(requested, applied) is applied
    assert said and said[-1][0] == "warning"
    assert "3124" in said[-1][1] and "3126" in said[-1][1], said[-1][1]


def test_an_adapter_that_cannot_report_a_geometry_leaves_the_request_alone():
    """None means "nobody checked", not "the request was honoured" — so the
    engine keeps the request and says nothing it cannot support."""
    requested = ROI(x=0, y=0, w=6252, h=4176, bin=2)
    assert NativeCamera._layout_roi(requested, None) is requested


def test_the_same_geometry_expressed_differently_is_not_a_disagreement():
    """The read-back arrives in binned pixels and is scaled back up, so an odd
    requested width round-trips to an even one. Warning about that would be an
    instrument crying wolf about its own arithmetic."""
    requested = ROI(x=0, y=0, w=6253, h=4176, bin=2)     # binned 3126
    applied = ROI(x=0, y=0, w=6252, h=4176, bin=2)       # binned 3126 too
    assert NativeCamera._layout_roi(requested, applied) is requested


def test_a_persistent_geometry_mismatch_is_reported_once_not_per_frame(said):
    """A rounding sensor mismatches on EVERY exposure. Unthrottled, the guide
    preview's 2.5s poll writes ~24 identical lines a minute into bus._history —
    a deque(maxlen=200) — so in about eight minutes one repeated sentence has
    evicted the entire run log, the UI log drawer, and the night log on disk.
    That is exactly when an operator needs the log."""
    requested = ROI(x=0, y=0, w=6252, h=4176, bin=2)
    applied = ROI(x=0, y=0, w=6248, h=4176, bin=2)
    seen: set = set()
    for _ in range(50):
        assert NativeCamera._layout_roi(requested, applied, seen) is applied
    assert len(said) == 1, f"one mismatch, {len(said)} log lines"


def test_a_DIFFERENT_mismatch_still_speaks():
    """Deduplication must not become silence: a new geometry is new news."""
    seen: set = set()
    a = ROI(x=0, y=0, w=6252, h=4176, bin=2)
    NativeCamera._layout_roi(a, ROI(x=0, y=0, w=6248, h=4176, bin=2), seen)
    NativeCamera._layout_roi(a, ROI(x=0, y=0, w=6240, h=4176, bin=2), seen)
    assert len(seen) == 2


def test_without_a_memory_it_always_speaks():
    """A bare call has no history to consult, so a silent default would make the
    mismatch invisible to anyone calling directly."""
    requested = ROI(x=0, y=0, w=6252, h=4176, bin=2)
    applied = ROI(x=0, y=0, w=6248, h=4176, bin=2)
    assert NativeCamera._layout_roi(requested, applied) is applied
