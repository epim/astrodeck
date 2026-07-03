"""Smoke tests for the Rust-native engine bridge (`astrodeck_native`).

These exercise the PyO3 surface described in the native-parity architecture
spec (§4.4/§5): star detection/measurement, focus-curve fitting, the autofocus
sweep state machine, and three-point polar alignment. The whole module is
skipped when the compiled wheel is not installed, so the suite stays green on
machines without the Rust toolchain (graceful-degradation requirement).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

an = pytest.importorskip(
    "astrodeck_native",
    reason="native engine wheel not installed (build with maturin develop)",
)


def _synthetic_star_frame() -> np.ndarray:
    """A 64x64 uint16 frame with one bright, round Gaussian star."""
    h, w = 64, 64
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, sigma, peak, background = 32.0, 30.0, 2.2, 18000.0, 100.0
    gauss = peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma * sigma))
    frame = np.clip(background + gauss, 0, 65535).astype(np.uint16)
    return frame


def test_version_string() -> None:
    assert isinstance(an.__version__, str)
    assert an.__version__.count(".") >= 1


def test_detect_and_measure_finds_star() -> None:
    frame = _synthetic_star_frame()
    stars, stats = an.detect_and_measure(frame, None)

    assert isinstance(stars, list)
    assert len(stars) >= 1, "expected at least one detected star"
    assert stats["star_count"] == len(stars)
    assert stats["hfr_median"] > 0.0

    star = stars[0]
    for key in ("x", "y", "hfr", "flux", "background", "eccentricity", "psf"):
        assert key in star
    assert star["hfr"] > 0.0
    assert star["flux"] > 0.0
    # Centroid should land near the injected star position.
    assert abs(star["x"] - 32.0) < 2.0
    assert abs(star["y"] - 30.0) < 2.0

    # When a PSF fit is accepted it carries the documented sub-fields.
    if star["psf"] is not None:
        psf = star["psf"]
        assert psf["model"] in ("gaussian", "moffat")
        for key in ("sigma_x", "sigma_y", "theta", "fwhm_x", "fwhm_y", "r2"):
            assert key in psf
        assert psf["fwhm_x"] > 0.0 and psf["fwhm_y"] > 0.0


def test_detect_and_measure_accepts_params_dict() -> None:
    frame = _synthetic_star_frame()
    # The autofocus profile turns PSF modelling off; stars still detected.
    stars, stats = an.detect_and_measure(frame, {"profile": "autofocus"})
    assert len(stars) >= 1
    assert stars[0]["psf"] is None


def test_fit_focus_curve_recovers_parabola_vertex() -> None:
    vertex = 5000.0
    # Clean parabola: value = a*(pos - vertex)^2 + floor.
    points = [
        (float(pos), 1.5 + 0.002 * (pos - vertex) ** 2, 0.05)
        for pos in range(4600, 5401, 100)
    ]
    out = an.fit_focus_curve(points, "parabolic")

    assert out["valid"] is True
    assert out["failure"] is None
    assert abs(out["best_position"] - vertex) < 1.0
    assert out["r2s"]["quadratic"] > 0.99
    assert len(out["curve"]) > 0


def test_fit_focus_curve_unknown_method_raises() -> None:
    with pytest.raises(ValueError):
        an.fit_focus_curve([(0.0, 1.0, 0.1)], "not_a_method")


def test_focus_sweep_drives_v_curve_to_done() -> None:
    start = 5000
    step = 100
    vertex = 5000.0
    slope = 0.01
    floor = 1.5

    def hfr_at(position: int) -> float:
        return floor + slope * abs(position - vertex)

    sweep = an.FocusSweep(
        {
            "step_size": step,
            "offset_steps": 4,
            "method": "star_hfr",
            "curve_fitting": "trendlines",
            "r_squared_threshold": 0.7,
        },
        start,
    )

    outcome = None
    for _ in range(200):
        action = sweep.next()
        if action["action"] == "move_to":
            pos = action["position"]
            sweep.add_measurement(pos, hfr_at(pos), 0.02, 40)
        elif action["action"] == "done":
            outcome = action["outcome"]
            break
        elif action["action"] == "failed":
            pytest.fail(f"sweep failed: {action['reason']}")
        else:
            pytest.fail(f"unexpected action {action['action']}")
    else:
        pytest.fail("sweep did not terminate within iteration budget")

    assert outcome is not None
    assert outcome["valid"] is True
    assert abs(outcome["best_position"] - vertex) <= step


def _jd_to_unix(jd: float) -> float:
    return (jd - 2440587.5) * 86400.0


def test_tppa_from_three_aligned_mount_zero_error() -> None:
    # A perfect small circle of constant declination about the celestial pole:
    # an ideally aligned mount -> ~zero polar error (dossier §12 test 2).
    jd_2000 = 2451544.5
    ts = _jd_to_unix(jd_2000)
    solves = [
        {"ra_hours": 20.0 / 15.0, "dec_deg": 80.0, "timestamp_unix_s": ts},
        {"ra_hours": 60.0 / 15.0, "dec_deg": 80.0, "timestamp_unix_s": ts},
        {"ra_hours": 90.0 / 15.0, "dec_deg": 80.0, "timestamp_unix_s": ts},
    ]
    site = {"latitude_deg": 49.0, "longitude_deg": 0.0}
    # Disable refraction so the true pole and refracted pole coincide.
    options = {
        "correct_for_refraction": True,
        "pressure_hpa": 0.0,
        "temperature_c": 0.0001,
        "relative_humidity": 0.0,
        "wavelength_um": 0.0,
    }
    result = an.tppa_from_three(solves, site, options)

    assert "model" in result and "error" in result
    error = result["error"]
    for key in ("az_arcmin", "alt_arcmin", "total_arcmin", "az_direction", "alt_direction", "flags"):
        assert key in error
    assert isinstance(error["flags"], list)
    assert error["total_arcmin"] < 0.1, error["total_arcmin"]

    # The frozen model round-trips through tppa_update: image geometry is
    # required for the continuous phase, so a model without it errors clearly.
    with pytest.raises(ValueError):
        an.tppa_update(result["model"], solves[2])


def test_tppa_update_tracks_reference_frame() -> None:
    # With image geometry supplied, updating with the reference solve (solve 3)
    # reproduces the initial error.
    jd_2000 = 2451544.5
    ts = _jd_to_unix(jd_2000)
    solves = [
        {"ra_hours": 20.0 / 15.0, "dec_deg": 80.0, "timestamp_unix_s": ts},
        {"ra_hours": 60.0 / 15.0, "dec_deg": 80.0, "timestamp_unix_s": ts},
        {"ra_hours": 90.0 / 15.0, "dec_deg": 80.0, "timestamp_unix_s": ts},
    ]
    site = {"latitude_deg": 49.0, "longitude_deg": 0.0}
    options = {
        "correct_for_refraction": True,
        "arcsec_per_pixel": 2.0,
        "image_width_px": 4000.0,
        "image_height_px": 3000.0,
    }
    result = an.tppa_from_three(solves, site, options)
    err0 = result["error"]
    err_same = an.tppa_update(result["model"], solves[2])
    assert abs(err_same["alt_arcmin"] - err0["alt_arcmin"]) < 0.1
    assert abs(err_same["az_arcmin"] - err0["az_arcmin"]) < 0.1
