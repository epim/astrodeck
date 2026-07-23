"""NOV-12 analyzer wrapper: crop → bahtinov_offset → BahtinovResult + verdict.

Locks the verdict copy/threshold table (locked/off), the geometric side vs the
rig-calibrated IN/OUT direction (invert flips direction, never side), and the
never-raises-on-a-blank-frame contract. Uses the shared synthetic generator."""
import numpy as np

from astrodeck.imaging.bahtinov import analyze_bahtinov
from _bahtinov_synth import make_bahtinov


def test_locked_when_in_focus():
    img = make_bahtinov(central_shift=0.0)
    r = analyze_bahtinov(img, center=((img.shape[0] - 1) / 2,) * 2, tol_px=1.5)
    assert r.valid and r.in_focus and r.offset_px is not None
    d = r.to_dict()
    assert d["valid"] is True and d["in_focus"] is True and "tol_px" in d


def test_off_when_defocused_has_side():
    c = ((257 - 1) / 2,) * 2
    r = analyze_bahtinov(make_bahtinov(central_shift=5.0), center=c, tol_px=1.5)
    assert r.valid and not r.in_focus
    assert r.side in ("left", "right") and abs(r.offset_px) > 1.5


def test_invert_flips_direction_not_side():
    c = ((257 - 1) / 2,) * 2
    base = analyze_bahtinov(make_bahtinov(central_shift=5.0), center=c, invert=False)
    inv = analyze_bahtinov(make_bahtinov(central_shift=5.0), center=c, invert=True)
    assert base.side == inv.side              # geometric side is invariant
    assert base.direction != inv.direction    # IN/OUT label flips


def test_blank_frame_invalid_never_raises():
    r = analyze_bahtinov(np.full((257, 257), 100.0, np.float64))
    assert not r.valid and r.offset_px is None and r.reason
    assert r.to_dict()["valid"] is False
