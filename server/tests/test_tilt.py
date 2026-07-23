"""Sensor-tilt / corner-vs-center optical-aberration inspector (PRO-13).

Pure aggregation over the SAME ``marks`` ``frame_eccentricity`` reads — no new
detection pass. Covers: doubled-angle axis math at the ±π/2 seam, zone
binning, and the four classification patterns (uniform/tilt/coma/tracking).
"""
import math

from astrodeck.imaging.stars import (
    _axis_diff,
    _axis_mean,
    _axis_spread,
    _bin_zones,
    frame_tilt,
)

W = H = 900  # 3x3 grid -> each zone 300px


def test_axis_mean_and_spread_handle_the_pi_seam():
    # aligned horizontal axes -> mean ~0, spread ~0
    assert abs(_axis_mean([0.0, 0.05, -0.05])) < 1e-6
    assert _axis_spread([0.0, 0.0, 0.0]) < 1e-9
    # +80deg and -80deg are the SAME axis (near vertical): tight, not scattered
    a = math.radians(80)
    b = math.radians(-80)
    assert _axis_spread([a, b]) < 0.15
    # genuinely perpendicular axes -> maximally scattered
    assert _axis_spread([0.0, math.pi / 2]) > 0.9


def test_axis_diff_is_acute_and_wraps():
    assert abs(_axis_diff(0.0, 0.0)) < 1e-9
    assert abs(_axis_diff(0.0, math.pi / 2) - math.pi / 2) < 1e-9
    # 10deg vs 170deg are 20deg apart as axes (not 160)
    assert abs(_axis_diff(math.radians(10), math.radians(170)) - math.radians(20)) < 1e-6


def _mark(x, y, hfr, ecc=None, theta=None):
    m = {"x": float(x), "y": float(y), "hfr": float(hfr)}
    if ecc is not None:
        m["ecc"] = float(ecc)
        m["theta"] = float(theta or 0.0)
    return m


def test_bin_zones_medians_and_null_thin_zones():
    marks = []
    # zone 0 (top-left): 4 stars hfr 2.0, ecc 0.1
    marks += [_mark(30 + i, 30 + i, 2.0, 0.1, 0.0) for i in range(4)]
    # zone 8 (bottom-right): 4 stars hfr 4.0, ecc 0.5
    marks += [_mark(630 + i, 630 + i, 4.0, 0.5, 0.0) for i in range(4)]
    # zone 4 (center): only 2 stars -> too thin -> None hfr
    marks += [_mark(450, 450, 3.0, 0.2, 0.0), _mark(451, 451, 3.0, 0.2, 0.0)]
    zones = _bin_zones(marks, W, H, 3, 3)
    assert len(zones) == 9
    assert abs(zones[0]["hfr"] - 2.0) < 1e-9 and zones[0]["n"] == 4
    assert abs(zones[8]["hfr"] - 4.0) < 1e-9
    assert zones[4]["hfr"] is None and zones[4]["n"] == 2  # honest: too few


def _zone_field(specs):
    """specs: {zone_index: (hfr, ecc, theta_or_None)} -> marks (4 per named zone)."""
    out = []
    for idx, (hfr, ecc, theta) in specs.items():
        r, c = divmod(idx, 3)
        cx, cy = (c + 0.5) * 300, (r + 0.5) * 300
        for i in range(4):
            out.append(_mark(cx + i, cy + i, hfr, ecc, theta))
    return out


def _all(hfr, ecc, theta):
    return {i: (hfr, ecc, theta) for i in range(9)}


def test_uniform_round_and_flat():
    t = frame_tilt(_zone_field(_all(2.0, 0.10, 0.0)), W, H)
    assert t is not None and t["pattern"] == "uniform"
    assert len(t["zones"]) == 9 and t["cols"] == 3 and t["rows"] == 3


def test_tilt_is_asymmetric_hfr_gradient():
    # HFR ramps left(1.8) -> right(3.8) across columns; stars stay round
    specs = {}
    for i in range(9):
        col = i % 3
        specs[i] = (1.8 + col * 1.0, 0.12, 0.0)
    t = frame_tilt(_zone_field(specs), W, H)
    assert t["pattern"] == "tilt"
    assert t["worst_zone"] in (2, 5, 8)  # rightmost column is worst


def test_coma_is_radial_corner_degrade():
    # center sharp+round, corners bloated with radial elongation
    specs = {4: (1.8, 0.05, 0.0)}
    for i in (0, 1, 2, 3, 5, 6, 7, 8):
        r, c = divmod(i, 3)
        cx, cy = (c + 0.5) * 300, (r + 0.5) * 300
        radial = math.atan2(cy - H / 2, cx - W / 2)
        specs[i] = (3.4, 0.55, radial)
    t = frame_tilt(_zone_field(specs), W, H)
    assert t["pattern"] == "coma"


def test_tracking_is_uniform_directional_elongation():
    # every zone elongated the SAME direction (RA drift), HFR flat
    t = frame_tilt(_zone_field(_all(2.4, 0.55, 0.0)), W, H)
    assert t["pattern"] == "tracking"


def test_frame_tilt_abstains_when_too_sparse():
    assert frame_tilt([_mark(10, 10, 2.0)], W, H) is None
