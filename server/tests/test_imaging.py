"""Imaging math: stretch, histogram, star detection, HFR."""
import math

import numpy as np

from astrodeck.imaging import auto_stretch, compute_histogram, detect_stars, median_hfr


def synthetic_field(n_stars: int = 20, sigma: float = 1.6,
                    shape: tuple[int, int] = (400, 400),
                    seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = rng.normal(500, 8, shape)
    h, w = shape
    for _ in range(n_stars):
        px, py = rng.uniform(30, w - 30), rng.uniform(30, h - 30)
        flux = rng.uniform(40_000, 400_000)
        xs = np.arange(w) - px
        ys = (np.arange(h) - py)[:, None]
        img += flux * np.exp(-(xs**2 + ys**2) / (2 * sigma**2)) / (2 * math.pi * sigma**2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_auto_stretch_range_and_brightening():
    img = synthetic_field()
    out = auto_stretch(img)
    assert out.min() >= 0.0 and out.max() <= 1.0
    # stretch should brighten the background toward the target
    assert np.median(out) > np.median(img / 65535.0)


def test_histogram_shape_and_total():
    img = synthetic_field()
    hist = compute_histogram(img, bins=128)
    assert len(hist) == 128
    assert sum(hist) == img.size


def test_detect_stars_finds_most():
    img = synthetic_field(n_stars=20)
    stars = detect_stars(img)
    assert 12 <= len(stars) <= 30


def test_hfr_tracks_defocus():
    sharp = synthetic_field(sigma=1.4)
    blurry = synthetic_field(sigma=4.0)
    hfr_sharp, n1 = median_hfr(sharp)
    hfr_blurry, n2 = median_hfr(blurry)
    assert hfr_sharp is not None and hfr_blurry is not None
    assert hfr_blurry > hfr_sharp * 1.5


def test_empty_frame_has_no_stars():
    rng = np.random.default_rng(3)
    img = rng.normal(500, 8, (300, 300)).clip(0, 65535).astype(np.uint16)
    hfr, n = median_hfr(img)
    assert hfr is None
    assert n < 3
