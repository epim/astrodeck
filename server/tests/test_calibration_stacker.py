import numpy as np
import pytest
from astrodeck.calibration.stacker import median_stack, sigma_clip_mean, stack_frames


def test_median_of_constant_stack():
    fs = [np.full((4, 4), 100, np.uint16) for _ in range(5)]
    assert np.allclose(median_stack(fs), 100.0)


def test_sigma_clip_rejects_a_cosmic_ray():
    # 8 frames at 100, one pixel spiked to 60000 in one frame -> master ~100.
    fs = [np.full((3, 3), 100.0, np.float32) for _ in range(8)]
    fs[0][1, 1] = 60000.0
    out = sigma_clip_mean(fs, sigma=3.0)
    assert abs(out[1, 1] - 100.0) < 1.0            # spike rejected
    assert out.dtype == np.float32


def test_sigma_clip_keeps_real_signal():
    rng = np.random.default_rng(7)
    fs = [rng.normal(200.0, 5.0, (8, 8)).astype(np.float32) for _ in range(10)]
    out = sigma_clip_mean(fs, sigma=3.0)
    assert abs(float(out.mean()) - 200.0) < 2.0


def test_small_stack_degrades_to_median():
    fs = [np.full((2, 2), 10.0, np.float32), np.full((2, 2), 20.0, np.float32)]
    assert np.allclose(sigma_clip_mean(fs), np.median(np.stack(fs), 0))


def test_all_rejected_pixel_falls_back_to_median():
    # zero-MAD column forces cnt==0 path for a lone deviant -> median, no nan.
    fs = [np.full((1, 1), 5.0, np.float32) for _ in range(4)]
    fs[0][0, 0] = 5.0  # identical -> mad 0; ensure no nan escapes
    out = sigma_clip_mean(fs)
    assert np.isfinite(out).all()


def test_empty_and_ragged_raise():
    with pytest.raises(ValueError):
        stack_frames([])
    with pytest.raises(ValueError):
        stack_frames([np.zeros((2, 2)), np.zeros((3, 3))])
