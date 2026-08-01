"""What detect_stars' fixed cutout can and cannot measure, pinned.

Not a bug report — a boundary. ``detect_stars`` feeds the overlay, the star
count, the cloud detector and the preview HFR readout, all of which live near
focus where a 15 px box is right and cheap. What it must never be mistaken for
is a focus metric across a sweep, because its number stops growing long before
the star does, and on 2026-07-31 that is exactly the mistake that was made: an
880 px donut field reported "median HFR 4.5px" and the Focus panel called it
FAIR.

These tests exist so that ceiling is a documented property with a number
attached, and so the claim "star_size does not share it" is checked rather than
asserted in a comment.
"""
import math

import numpy as np

from astrodeck.imaging.stars import (
    HFR_BOX_CEILING_FRACTION, detect_stars, star_size,
)


def _gaussians(sigma: float, flux: float = 300_000.0,
               shape: tuple[int, int] = (400, 400), seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = rng.normal(500, 8, shape)
    for py, px in ((100, 100), (100, 300), (300, 100), (300, 300), (200, 200)):
        ys = (np.arange(shape[0]) - py)[:, None]
        xs = np.arange(shape[1]) - px
        img += flux * np.exp(-(xs ** 2 + ys ** 2) / (2 * sigma ** 2)) \
            / (2 * math.pi * sigma ** 2)
    return img


def _median_hfr(img: np.ndarray) -> float:
    stars = detect_stars(img)
    assert stars, "no stars detected in a frame built entirely of stars"
    return float(np.median([s.hfr for s in stars]))


def test_the_box_measures_a_sharp_star_correctly():
    """The regime it is for: flux-weighted mean radius of a Gaussian is
    1.253*sigma, and inside the box that is what comes back."""
    assert abs(_median_hfr(_gaussians(1.6)) - 1.253 * 1.6) < 0.15


def test_the_box_SATURATES_and_a_defocused_star_reads_the_box_not_the_star():
    """A sigma=12 star has a true HFR of 15px. The box reports ~4.2 — the same
    number a sigma=5 star gets, and the same number the sky gave for a donut
    hundreds of pixels across. Two frames a factor of two apart in focus are
    indistinguishable through this measurement, which is why a sweep built on
    it has nothing to fit."""
    soft = _median_hfr(_gaussians(5.0))
    softer = _median_hfr(_gaussians(12.0))
    assert softer < 1.25 * soft, (
        f"sigma 5 -> {soft:.2f}, sigma 12 -> {softer:.2f}: if these have "
        "genuinely separated, the ceiling is gone and this file should say so")
    assert softer < HFR_BOX_CEILING_FRACTION * (15 // 2), (
        f"{softer:.2f} exceeds the box's arithmetic ceiling — the measurement "
        "changed and HFR_BOX_CEILING_FRACTION is now wrong")


def test_star_size_does_NOT_share_the_ceiling():
    """The same two frames, through the metric an autofocus sweep uses. These
    have to separate by roughly the factor the stars did (2.4x)."""
    small = star_size(_gaussians(5.0))
    big = star_size(_gaussians(12.0))
    assert small is not None and big is not None
    assert big.radius > 2.0 * small.radius, \
        f"sigma 5 -> {small.radius:.2f}, sigma 12 -> {big.radius:.2f}"
    assert abs(big.radius - 1.253 * 12.0) < 3.0, \
        f"{big.radius:.2f} is not a sigma=12 star (expect {1.253 * 12:.2f})"


def test_star_size_agrees_with_the_box_where_the_box_still_works():
    """Continuity matters: every threshold in the UI is calibrated in HFR px, so
    the two measurements must not disagree on a sharp star."""
    img = _gaussians(1.6)
    size = star_size(img)
    assert size is not None
    assert abs(size.radius - _median_hfr(img)) < 0.5
