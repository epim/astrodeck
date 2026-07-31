"""A star spreads; a hot pixel does not.

`detect_stars` fed the preview HFR readout, the Bahtinov aid and the CLOUD
DETECTOR, and it counted single hot pixels as stars. On a real uncooled frame
(Poseidon-M Pro, CCD-TEMP +15.7 C, 8 s, gain 300, no dark) 158 of the 200
"stars" it returned were single pixels whose neighbours held less than half the
centre's flux.

That is not a cosmetic miscount. It is what made "the native detector finds 2
stars where Python finds 200" look like a native-detector defect for a day —
the 200 was mostly sensor noise, and the comparison sent the investigation at
the wrong component entirely.
"""
import numpy as np

from astrodeck.imaging.stars import MIN_NEIGHBOUR_FLUX_RATIO, detect_stars


def _bg(h=192, w=192, level=700.0, noise=8.0, seed=3):
    rng = np.random.default_rng(seed)
    return rng.normal(level, noise, (h, w))


def _gaussian(img, cy, cx, peak, sigma):
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    img += peak * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sigma ** 2)))
    return img


def test_a_single_hot_pixel_is_not_a_star():
    img = _bg()
    img[96, 96] = 60000.0            # one blazing cell, neighbours untouched
    stars = detect_stars(img)
    assert stars == [], f"a lone hot pixel was reported as {len(stars)} star(s)"


def test_many_isolated_hot_pixels_are_all_rejected():
    """The real frame had hundreds. One slipping through is a wrong HFR; all of
    them slipping through is a wrong diagnosis."""
    img = _bg()
    # Spaced on a grid: this gate is about ISOLATED spikes, and randomly-placed
    # points collide often enough that the test would be measuring the adjacent-
    # pair case below instead of the one it names.
    for y in range(24, 170, 18):
        for x in range(24, 170, 18):
            img[y, x] = 50000.0
    assert detect_stars(img) == []


def test_a_pair_of_ADJACENT_hot_pixels_still_survives():
    """A known limitation, pinned so it is a decision rather than a surprise.

    Two hot cells side by side DO share flux, so the neighbour test cannot tell
    them from a very tight star. What gives them away is shape — the pair comes
    out clearly elongated (ecc ~0.79 here), where a star is round.

    Not gated here because an eccentricity cut is a real risk to genuine stars
    on a rig with tilt or trailing, and single hot pixels are the overwhelming
    majority (338 isolated spikes on the measured frame). If adjacent pairs
    start mattering, this is the test that should change."""
    img = _bg()
    img[96, 96] = 50000.0
    img[96, 97] = 50000.0
    stars = detect_stars(img)
    assert len(stars) == 1, "documenting today's behaviour, not endorsing it"
    assert stars[0].ecc > 0.6, (
        f"if this pair ever stops reading as elongated (ecc={stars[0].ecc:.2f}), "
        "the shape-based fix suggested above is no longer available")


def test_a_real_star_still_detected():
    img = _gaussian(_bg(), 96.0, 96.0, 4000.0, 2.0)
    stars = detect_stars(img)
    assert len(stars) == 1, f"expected the star, got {len(stars)}"
    assert abs(stars[0].x - 96.0) < 1.5 and abs(stars[0].y - 96.0) < 1.5


def test_an_undersampled_star_survives():
    """The gate must not cost real stars on a short-focal-length rig. A
    sigma=0.7px Gaussian still puts ~2x the peak's flux into its neighbours,
    comfortably above the 0.75 bar."""
    img = _gaussian(_bg(), 96.0, 96.0, 6000.0, 0.7)
    assert len(detect_stars(img)) == 1


def test_a_star_beside_a_hot_pixel_keeps_the_star():
    img = _gaussian(_bg(), 60.0, 60.0, 4000.0, 2.0)
    img[130, 130] = 55000.0
    stars = detect_stars(img)
    assert len(stars) == 1
    assert abs(stars[0].x - 60.0) < 2.0 and abs(stars[0].y - 60.0) < 2.0


def test_the_threshold_is_a_named_constant_not_a_magic_number():
    """It is calibrated against PSF geometry (see the module docstring), so it
    has to stay legible and adjustable."""
    assert 0.0 < MIN_NEIGHBOUR_FLUX_RATIO < 2.0
