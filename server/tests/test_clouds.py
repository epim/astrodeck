"""Image-based cloud detection: starless / low-contrast frames read cloudy,
rich bright-star fields read clear, the score tracks star loss, and — the case
that motivated the design — a hundred faint NOISE peaks on a bright cloudy
background must NOT read clear."""
import math

import numpy as np

from astrodeck.imaging import CloudResult, Star, cloud_score, frame_contrast


def star_field(n_stars: int = 30, sigma: float = 1.6,
               shape: tuple[int, int] = (480, 640), bg: float = 500.0,
               noise: float = 8.0, seed: int = 7) -> np.ndarray:
    """Clear sky: faint noise background + bright point sources."""
    rng = np.random.default_rng(seed)
    img = rng.normal(bg, noise, shape)
    h, w = shape
    for _ in range(n_stars):
        px, py = rng.uniform(30, w - 30), rng.uniform(30, h - 30)
        flux = rng.uniform(40_000, 400_000)
        xs = np.arange(w) - px
        ys = (np.arange(h) - py)[:, None]
        img += flux * np.exp(-(xs**2 + ys**2) / (2 * sigma**2)) / (2 * math.pi * sigma**2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def clouded_frame(shape: tuple[int, int] = (480, 640), level: float = 1500.0,
                  noise: float = 20.0, gradient: float = 300.0,
                  seed: int = 11) -> np.ndarray:
    """Cloud deck: elevated, smoothly varying background, no point sources."""
    rng = np.random.default_rng(seed)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    ramp = gradient * (xx / w) + 0.5 * gradient * (yy / h)
    img = rng.normal(level, noise, shape) + ramp
    return np.clip(img, 0, 65535).astype(np.uint16)


def bright_noisy_cloud(shape: tuple[int, int] = (720, 1280), level: float = 36000.0,
                       noise: float = 3300.0, seed: int = 5) -> np.ndarray:
    """The real-cloud failure case: a BRIGHT, very noisy sky. The k-sigma star
    detector finds dozens of ~5-6 sigma noise peaks here (none truly bright).
    Cloud detection must still call this cloudy — raw star count would not."""
    rng = np.random.default_rng(seed)
    img = rng.normal(level, noise, shape)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_clear_rich_field_reads_clear():
    r = cloud_score(star_field(n_stars=40))
    assert isinstance(r, CloudResult)
    assert not r.cloudy
    assert r.score < 0.4
    assert r.bright_stars >= 15
    assert r.contrast > 40


def test_starless_dark_frame_reads_cloudy():
    r = cloud_score(clouded_frame(level=600.0))
    assert r.cloudy
    assert r.score > 0.7
    assert r.bright_stars == 0


def test_bright_noisy_cloud_reads_cloudy():
    """A bright, very noisy sky with no real point sources → cloudy, low
    contrast, no bright stars."""
    r = cloud_score(bright_noisy_cloud())
    assert r.bright_stars <= 2
    assert r.contrast < 10
    assert r.cloudy and r.score > 0.6


def test_faint_noise_peaks_do_not_read_clear():
    """Regression for the real-data failure: on a bright cloudy frame the k-sigma
    detector returns ~150 faint (~5-6 sigma) 'stars'. None are truly bright, so
    the verdict must stay cloudy — raw star COUNT must not override it."""
    img = bright_noisy_cloud(level=36000.0, noise=3300.0)
    bg = float(np.median(img))
    noise = float(np.median(np.abs(img.astype(float) - bg))) * 1.4826
    # 150 detections sitting at ~5.5 sigma — exactly what real cloud produced.
    faint = [Star(x=float(i), y=float(i), flux=100.0, hfr=5.0,
                  peak=bg + 5.5 * noise) for i in range(150)]
    r = cloud_score(img, stars=faint)
    assert len(faint) > 100               # detector "found" many stars
    assert r.bright_stars == 0            # but none clear the bright-sigma bar
    assert r.cloudy and r.score > 0.6


def test_score_increases_as_stars_disappear():
    scores = [cloud_score(star_field(n_stars=n)).score
              for n in (60, 30, 12, 3, 0)]
    assert scores == sorted(scores)
    assert scores[0] < 0.3 < scores[-1]


def test_contrast_clear_much_higher_than_cloud():
    assert frame_contrast(star_field(n_stars=40)) > 3 * frame_contrast(clouded_frame())


def test_stars_passthrough_skips_detection():
    img = star_field(n_stars=40)
    # Passing an empty star list forces the bright-star signal to fully-cloudy
    # regardless of the (rich) pixels — proves the caller's list is used.
    forced = cloud_score(img, stars=[])
    assert forced.bright_stars == 0
    assert forced.score > cloud_score(img).score


def test_to_dict_shape_and_rounding():
    d = cloud_score(clouded_frame()).to_dict()
    assert set(d) == {"cloudy", "score", "bright_stars", "bright_density",
                      "contrast", "reason"}
    assert isinstance(d["cloudy"], bool)
    assert 0.0 <= d["score"] <= 1.0
    assert isinstance(d["reason"], str) and d["reason"]


def test_thresholds_are_tunable():
    img = star_field(n_stars=12)
    strict = cloud_score(img, clear_bright_density=50.0, clear_contrast=300.0)
    lax = cloud_score(img, clear_bright_density=0.1, clear_contrast=3.0)
    assert strict.score > lax.score
