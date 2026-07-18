import math
import numpy as np
import pytest

native = pytest.importorskip("astrodeck_native")  # skip cleanly when wheel absent

def _gaussian_frame(w, h, cx, cy, amp=4000.0, sg=1.6, bg=100):
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx-cx)**2 + (yy-cy)**2) / (2*sg*sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)

def test_guide_star_find_returns_candidates():
    frame = _gaussian_frame(64, 64, 32.4, 30.6)
    stars, meta = native.guide_star_find(frame)
    assert len(stars) >= 1
    s = stars[0]
    assert abs(s["x"] - 32.4) < 0.6 and abs(s["y"] - 30.6) < 0.6
    assert s["snr"] > 5.0
    assert "sat_thresh" in meta

def test_engine_process_pulse_pair_shape():
    cfg = {"image_scale_arcsec": 2.0, "ra_algorithm": "hysteresis",
           "dec_algorithm": "resist_switch", "dec_guide_mode": "auto"}
    e = native.GuideEngine(cfg)
    # inject a calibration so the offset->pulse path is deterministic
    e.load_calibration({"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
                        "y_angle": math.pi/2, "y_angle_error": 0.0,
                        "declination": 0.0, "pier_side": "west",
                        "ra_parity": "even", "dec_parity": "even",
                        "rotator_angle": 0.0, "binning": 1, "is_valid": True})
    e.begin_guiding()
    f0 = _gaussian_frame(64, 64, 32.0, 32.0)
    a0 = e.process(f0, 0.0, 2.0)  # establishes lock
    assert a0["action"] in ("idle", "pulse_pair", "settle")
    f1 = _gaussian_frame(64, 64, 37.0, 32.0)  # +5px in x
    a1 = e.process(f1, 2.0, 2.0)
    assert a1["action"] == "pulse_pair"
    assert a1["ra"] is not None and a1["ra"]["dir"] == "west"
    assert a1["ra"]["ms"] > 0

def test_calibration_roundtrips():
    e = native.GuideEngine({"image_scale_arcsec": 2.0})
    cal = {"x_rate": 0.02, "y_rate": 0.018, "x_angle": 0.1, "y_angle": 1.6,
           "y_angle_error": 0.0, "declination": 0.2, "pier_side": "east",
           "ra_parity": "odd", "dec_parity": "even", "rotator_angle": 0.0,
           "binning": 1, "is_valid": True}
    e.load_calibration(cal)
    out = e.dump_calibration()
    assert out is not None
    assert abs(out["x_rate"] - 0.02) < 1e-9 and out["pier_side"] == "east"
