"""NOV-12 analyzer wrapper: crop → bahtinov_offset → BahtinovResult + verdict.

Locks the verdict copy/threshold table (locked/off), the geometric side vs the
rig-calibrated IN/OUT direction (invert flips direction, never side), and the
never-raises-on-a-blank-frame contract. Uses the shared synthetic generator."""
import math

import numpy as np
import pytest

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


@pytest.mark.parametrize("central_shift", [0.0, 6.0])
def test_to_dict_geom_is_data_space_and_matches_the_offset(central_shift):
    """The live-overlay geometry must land ON the star, in DATA pixel space.

    A dropped/negated ``+center`` term is the one bug that puts the drawn spikes
    visibly off the star while every scalar in the payload still looks right, so
    pin it here: the vertex sits at the synthetic pattern's own centre, and the
    central spike's perpendicular distance FROM that vertex equals |offset_px|
    (the number the text verdict shows) — the two representations agree."""
    size = 257
    c = (size - 1) / 2                       # 128.0, the frame/pattern centre
    r = analyze_bahtinov(make_bahtinov(size=size, central_shift=central_shift),
                         center=(c, c), tol_px=1.5)
    g = r.to_dict()["geom"]
    assert g["center"] == [c, c]
    # vertex of the two OUTER spikes: data space, at the star (NOT near 0,0 —
    # that would mean the ROI-relative coords leaked out unlifted).
    assert math.hypot(g["vertex"][0] - c, g["vertex"][1] - c) < 2.0, g["vertex"]
    # exactly one central spike out of three; every point is inside the frame.
    assert len(g["spikes"]) == 3
    assert sum(1 for s in g["spikes"] if s["central"]) == 1
    assert all(0 <= s["x"] < size and 0 <= s["y"] < size for s in g["spikes"])
    # the central spike's signed distance from the vertex IS the reported offset:
    # n = the normal of the drawn (tangent) angle; distance = |n . (vertex - p)|.
    cen = next(s for s in g["spikes"] if s["central"])
    n = np.deg2rad(cen["angle_deg"] - 90.0)
    dist = abs(math.cos(n) * (g["vertex"][0] - cen["x"])
               + math.sin(n) * (g["vertex"][1] - cen["y"]))
    # 0.15 px, not 0: the payload rounds coords/angle to 1 dp for compactness,
    # which perturbs the projection by ~0.07 px. A sign/offset bug would be off by
    # ~2x the offset (or by the whole 128 px centre term), never by 0.1.
    assert abs(dist - abs(r.offset_px)) < 0.15, (dist, r.offset_px)
    assert abs(dist - central_shift) < 0.7, (dist, central_shift)


def test_blank_frame_invalid_never_raises():
    r = analyze_bahtinov(np.full((257, 257), 100.0, np.float64))
    assert not r.valid and r.offset_px is None and r.reason
    d = r.to_dict()
    assert d["valid"] is False
    # an invalid fit carries NO geometry at all — the overlay abstains rather
    # than drawing lines from a fit that was rejected.
    assert "geom" not in d and r.geom is None
