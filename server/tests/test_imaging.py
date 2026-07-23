"""Imaging math: stretch, histogram, star detection, HFR."""
import math

import numpy as np

from astrodeck.imaging import (
    auto_levels,
    auto_stretch,
    compute_histogram,
    detect_stars,
    display_histogram,
    levels_to_mtf,
    measure_frame,
    median_hfr,
    star_marks,
    stretch_with,
    to_jpeg,
    to_thumb,
)
from astrodeck.imaging.stars import (
    DEFAULT_MAX_MARKS,
    DEFAULT_MAX_STARS,
    Star,
    _ecc_theta,
    frame_eccentricity,
)


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


# ----------------------------------------------------------- stretch params

def test_auto_levels_in_range_and_ordered():
    img = synthetic_field()
    black, mid, white = auto_levels(img)
    assert 0.0 <= black < white <= 1.0
    assert 0.0 < mid < 1.0


def test_stretch_with_explicit_levels_brightens_like_auto():
    img = synthetic_field()
    black, mid, white = auto_levels(img)
    # replaying the auto levels reproduces the auto-stretch result closely
    out = stretch_with(img, black, mid, white)
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert abs(np.median(out) - np.median(auto_stretch(img))) < 0.06


def test_stretch_with_black_white_clip():
    img = synthetic_field()
    # raising black darkens the background; lowering white brightens highlights
    dark = stretch_with(img, 0.5, 0.5, 1.0)
    bright = stretch_with(img, 0.0, 0.5, 0.3)
    assert np.median(dark) < np.median(bright)


def test_levels_to_mtf_clamps_inverted_order():
    black, mid, white = levels_to_mtf(0.8, 2.0, 0.2)  # white < black, mid > 1
    assert black < white
    assert 0.0 < mid < 1.0


def test_display_histogram_has_travel():
    img = synthetic_field()
    black, mid, white = auto_levels(img)
    stretched = stretch_with(img, black, mid, white)
    disp = display_histogram(stretched, bins=128)
    lin = compute_histogram(img, bins=128)
    assert len(disp) == 128
    # a linear light-frame histogram piles into the first few bins; the
    # display-domain one spreads, so its mass is not all on the left edge.
    assert sum(disp[10:]) > sum(lin[10:])


def test_to_jpeg_returns_bytes_and_dims():
    img = synthetic_field(shape=(900, 1600))
    data, w, h = to_jpeg(img, max_width=1400)
    assert isinstance(data, (bytes, bytearray)) and len(data) > 0
    assert w == 1400 and 0 < h < 1400


def test_to_thumb_from_data_and_from_bytes():
    img = synthetic_field()
    t1 = to_thumb(img, max_width=160)
    jpeg, _, _ = to_jpeg(img)
    t2 = to_thumb(jpeg, max_width=160)
    assert len(t1) > 0 and len(t2) > 0


# ----------------------------------------------------------- star-list shape

def test_star_marks_shape_and_coords():
    img = synthetic_field(n_stars=20)
    stars = detect_stars(img)
    marks = star_marks(stars)
    assert isinstance(marks, list) and len(marks) >= 12
    assert any("ecc" in m for m in marks)        # Pass 2 is now live
    for m in marks:
        assert set(m) >= {"x", "y", "hfr"}
        assert 0 <= m["x"] <= img.shape[1]
        assert 0 <= m["y"] <= img.shape[0]
        assert m["hfr"] > 0
        if "ecc" in m:
            assert 0.0 <= m["ecc"] <= 1.0 and "theta" in m
    # round synthetic stars -> low typical elongation
    eccs = [m["ecc"] for m in marks if "ecc" in m]
    assert eccs and float(np.median(eccs)) < 0.5


def test_measure_frame_single_pass_matches_median_hfr():
    img = synthetic_field(n_stars=20)
    hfr, count, marks = measure_frame(img)
    ref_hfr, ref_count = median_hfr(img)
    assert count == ref_count
    assert hfr is not None and abs(hfr - ref_hfr) < 1e-9
    # the overlay carries every detected star up to the marks cap — the explicit
    # coupling (P3-5): len(marks) == min(count, DEFAULT_MAX_MARKS), which holds
    # only because DEFAULT_MAX_MARKS >= DEFAULT_MAX_STARS so marks never drops a
    # detected star. A bump of max_stars above the marks cap would break this.
    assert len(marks) == min(count, DEFAULT_MAX_MARKS)


def test_star_marks_cap_covers_detect_cap():
    # the marks cap MUST be >= the detect cap so the overlay never silently drops
    # a star the detector found (P3-5 — latent coupling made explicit).
    assert DEFAULT_MAX_MARKS >= DEFAULT_MAX_STARS


def test_star_marks_ecc_only_for_unsaturated_midbright():
    # an explicit star list with ecc set on both a saturated and a faint star;
    # neither should get an ecc field attached (Pass-1 keeps ecc 0.0 anyway, and
    # the gating must never attach it to a saturated flat-top star).
    full_well = 60000
    stars = [
        Star(x=10, y=10, flux=1e5, hfr=2.0, peak=59000, ecc=0.4, theta=0.1),  # saturated
        Star(x=20, y=20, flux=5e4, hfr=2.1, peak=30000, ecc=0.3, theta=0.2),  # mid-bright
    ]
    marks = star_marks(stars, full_well=full_well)
    sat_mark = next(m for m in marks if m["x"] == 10)
    assert "ecc" not in sat_mark   # saturated star never carries ecc


# ----------------------------------------------------------- eccentricity core

def test_ecc_theta_exact_vectors():
    # round: equal moments, no cross term
    e, t = _ecc_theta(4.0, 4.0, 0.0)
    assert e == 0.0 and t == 0.0
    # horizontal elongation Ixx>Iyy: ecc=sqrt(1-1/9), PA=0
    e, t = _ecc_theta(9.0, 1.0, 0.0)
    assert abs(e - math.sqrt(8/9)) < 1e-9 and abs(t) < 1e-9
    # vertical elongation Iyy>Ixx: same ecc, PA=+pi/2
    e, t = _ecc_theta(1.0, 9.0, 0.0)
    assert abs(e - math.sqrt(8/9)) < 1e-9 and abs(t - math.pi/2) < 1e-9
    # 45 deg: equal diagonal, cross term -> PA=pi/4
    e, t = _ecc_theta(5.0, 5.0, 4.0)
    assert abs(e - math.sqrt(8/9)) < 1e-9 and abs(t - math.pi/4) < 1e-9
    # degenerate guard
    assert _ecc_theta(0.0, 0.0, 0.0) == (0.0, 0.0)


def _elongated_blob(sx: float, sy: float, angle: float = 0.0,
                    shape=(120, 120), flux=3.0e5) -> np.ndarray:
    cx, cy = shape[1] / 2, shape[0] / 2
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    xr = (xx - cx) * math.cos(angle) + (yy - cy) * math.sin(angle)
    yr = -(xx - cx) * math.sin(angle) + (yy - cy) * math.cos(angle)
    g = flux * np.exp(-(xr**2 / (2*sx**2) + yr**2 / (2*sy**2)))
    img = np.full(shape, 500.0) + g
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_detect_stars_measures_elongation():
    stars = detect_stars(_elongated_blob(sx=2.2, sy=1.1))   # analytic ecc ~0.866
    assert stars, "expected a detection"
    s = max(stars, key=lambda s: s.flux)
    assert 0.6 < s.ecc < 0.95          # box truncation lowers it below analytic
    assert abs(s.theta) < 0.2          # major axis ~ +x


def test_detect_stars_round_is_low_ecc():
    stars = detect_stars(_elongated_blob(sx=1.5, sy=1.5))
    s = max(stars, key=lambda s: s.flux)
    assert s.ecc < 0.25


def test_detect_stars_pa_tracks_rotation():
    stars = detect_stars(_elongated_blob(sx=2.2, sy=1.1, angle=math.pi/4))
    s = max(stars, key=lambda s: s.flux)
    assert abs(abs(s.theta) - math.pi/4) < 0.25


def test_frame_eccentricity_median_of_trusted():
    assert frame_eccentricity([]) is None
    assert frame_eccentricity([{"x": 1, "y": 1, "hfr": 2.0}]) is None  # no ecc key
    marks = [{"x": 1, "y": 1, "hfr": 2.0, "ecc": 0.2, "theta": 0.0},
             {"x": 2, "y": 2, "hfr": 2.0, "ecc": 0.4, "theta": 0.0},
             {"x": 3, "y": 3, "hfr": 2.0, "ecc": 0.6, "theta": 0.0}]
    assert abs(frame_eccentricity(marks) - 0.4) < 1e-9
