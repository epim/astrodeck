import numpy as np
import pytest
from astrodeck.imaging.readnoise import read_noise_e, compare_modes


def _bias(sigma_adu, seed):
    rng = np.random.default_rng(seed)
    return (1000 + rng.normal(0, sigma_adu, size=(64, 64))).astype(np.float64)


def test_read_noise_electrons_recovered():
    egain = 0.25
    frames = [_bias(8.0, s) for s in range(4)]
    rn = read_noise_e(frames, egain)
    assert abs(rn - 8.0 * egain) < 0.15   # ~2.0 e-


def test_lrn_lower_than_normal():
    egain = 0.25
    normal = {"frames": [_bias(16.0, s) for s in range(3)], "egain": egain}
    low = {"frames": [_bias(6.0, s + 100) for s in range(3)], "egain": egain}
    out = compare_modes(normal, low)
    assert out["improved"] is True
    assert out["low_e"] < out["normal_e"]


def test_single_frame_falls_back_to_spatial():
    rn = read_noise_e([_bias(10.0, 7)], 0.25)
    assert abs(rn - 10.0 * 0.25) < 0.2


def test_empty_raises():
    with pytest.raises(ValueError):
        read_noise_e([], 0.25)
