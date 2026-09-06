"""GN-06: the calibration report folds the Dec-parity reversal out of the
orthogonality error.

On 2026-09-06 three fresh calibrations on the rig reported ortho_error_deg
174.9-178.2 with is_valid True and no advisory. The Dec axis on that mount
runs reversed relative to RA, so the engine's raw y_angle_error legitimately
sits near +-pi: the transforms need it unfolded, the advisory already folded
it, and only the REPORT printed the raw value. A human reading 178 deg
cannot tell a square calibration from a broken one.
"""
from __future__ import annotations

import math

import pytest

from test_native_guide_optics_scale import _guider_with_cal  # rootdir-relative


@pytest.mark.parametrize("raw_deg, folded_deg, reversed_", [
    (3.0, 3.0, False),
    (-4.0, 4.0, False),
    (177.0, 3.0, True),
    (-175.0, 5.0, True),
    (180.0, 0.0, True),
])
def test_report_folds_the_reversal_out_of_a_bare_dict(raw_deg, folded_deg,
                                                       reversed_):
    """A persisted file from before the engine exported the folded value, or
    an older wheel: the report derives the fold itself."""
    cal = {"is_valid": True, "y_angle_error": math.radians(raw_deg),
           "declination": math.radians(31.0), "pier_side": "west", "binning": 1}
    rep = _guider_with_cal(cal).calibration_report()
    assert rep["ortho_error_deg"] == pytest.approx(folded_deg, abs=0.01)
    assert rep["dec_axis_reversed"] is reversed_


def test_report_prefers_the_engines_own_folded_value():
    cal = {"is_valid": True, "y_angle_error": math.radians(177.0),
           "ortho_error": math.radians(-3.0), "dec_axis_reversed": True,
           "declination": 0.5, "pier_side": "east", "binning": 1}
    rep = _guider_with_cal(cal).calibration_report()
    assert rep["ortho_error_deg"] == pytest.approx(3.0, abs=0.01)
    assert rep["dec_axis_reversed"] is True


def test_real_engine_dumps_the_folded_error_and_loads_without_it():
    """The wheel's dump_calibration carries ortho_error (folded, radians) and
    dec_axis_reversed; load_calibration neither needs nor reads them, so a
    calibration persisted before GN-06 still loads."""
    native = pytest.importorskip("astrodeck_native")
    x_angle = 0.3
    y_angle = x_angle - math.pi / 2 + 0.05          # reversed Dec, 0.05 rad off square
    raw = math.atan2(math.sin(x_angle - y_angle + math.pi / 2),
                     math.cos(x_angle - y_angle + math.pi / 2))
    assert abs(raw) > math.pi / 2, raw
    stored = {"x_rate": 0.02, "y_rate": 0.018, "x_angle": x_angle,
              "y_angle": y_angle, "y_angle_error": raw,
              "declination": 0.5, "pier_side": "west",
              "ra_parity": "even", "dec_parity": "odd",
              "rotator_angle": 0.0, "binning": 1, "is_valid": True}
    e = native.GuideEngine({})
    e.load_calibration(stored)                      # no derived keys: pre-GN-06 file
    d = e.dump_calibration()
    assert d["y_angle_error"] == pytest.approx(raw)   # the transform's value, unfolded
    assert d["ortho_error"] == pytest.approx(-0.05, abs=1e-9)
    assert d["dec_axis_reversed"] is True

    # A dict round-tripped through the dump (derived keys present, one made
    # deliberately stale) loads and re-derives from the stored field.
    d["ortho_error"] = 1.0
    d["dec_axis_reversed"] = False
    e2 = native.GuideEngine({})
    e2.load_calibration(d)
    d2 = e2.dump_calibration()
    assert d2["ortho_error"] == pytest.approx(-0.05, abs=1e-9)
    assert d2["dec_axis_reversed"] is True

    rep = _guider_with_cal(d2).calibration_report()
    assert rep["ortho_error_deg"] == pytest.approx(math.degrees(0.05), abs=0.01)
    assert rep["dec_axis_reversed"] is True
