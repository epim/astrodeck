# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Synthetic frames for ``astrodeck.imaging.frame_class`` (WP 2.1, #1108).

Every frame is drawn from a fixed seed, so the fixtures are the same bytes on
every machine and nothing here comes from a real rig frame. Each is a
1024 x 1024 uint16 array (the size of ``tests/fixtures/star_noise``), because
``cloud_score``'s star-density thresholds are per megapixel and a thumbnail
would read every star field as dense.

The levels are the ones the combined issue's frames are made of, scaled to be
unmistakable: a black level near 240 ADU with 9 ADU of per-pixel noise (the
#251 capped frame), a lit sky a few hundred ADU above it, and a bright cloud
deck far above that. The site, the sky position and the rig's real numbers
appear nowhere.

    stars_frame()           clear sky, 120 stars
    no_light_frame()        a capped optic: the black level and a few hot pixels
    obstructed_frame()      a treeline over the lower part of the view, the sky
                            above it with a handful of stars (``edge_row``
                            130: the tree covers 87 % and the median reads
                            at the black level)
    cloud_frame()           a bright, starless deck with a smooth gradient
    cloud_bank_frame()      a sharp-edged bright cloud bank over most of the
                            view, a clear window with stars in the rest
    ramp_frame()            starless, a smooth sky-glow gradient and nothing else

``BLACK_LEVEL`` is what ``no_light_reference()`` stands for.
"""
from __future__ import annotations

import numpy as np

SIZE = 1024
BLACK_LEVEL = 240.0
BLACK_NOISE = 9.0

#: Star PSF width in pixels (sigma), and the half-width of the stamp it is
#: drawn into. 1.8 px is a well-focused star at bin 1.
STAR_SIGMA = 1.8
_STAMP = 8

#: The scale of an out-of-focus treeline's edge, pixels (the tanh width).
EDGE_SOFTNESS_PX = 12.0


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _noise(rng: np.random.Generator, level: float, sigma: float) -> np.ndarray:
    return rng.normal(level, sigma, (SIZE, SIZE))


def _add_star(img: np.ndarray, x: int, y: int, amplitude: float) -> None:
    ys, xs = np.mgrid[-_STAMP:_STAMP + 1, -_STAMP:_STAMP + 1]
    img[y - _STAMP:y + _STAMP + 1, x - _STAMP:x + _STAMP + 1] += (
        amplitude * np.exp(-(xs ** 2 + ys ** 2) / (2 * STAR_SIGMA ** 2)))


def _scatter_stars(img: np.ndarray, rng: np.random.Generator, count: int,
                   rows: tuple[int, int] = (0, SIZE)) -> None:
    """``count`` stars at random positions with rows in ``rows``, amplitudes
    from 1500 to 12000 ADU above the local level."""
    margin = _STAMP + 2
    lo = max(rows[0], margin)
    hi = min(rows[1], SIZE - margin)
    for _ in range(count):
        _add_star(img, int(rng.integers(margin, SIZE - margin)),
                  int(rng.integers(lo, hi)), float(rng.uniform(1500, 12000)))


def _to_adu(img: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(img), 0, 65535).astype(np.uint16)


def stars_frame(seed: int = 101) -> np.ndarray:
    """A clear sky: a 400 ADU background and 120 stars."""
    rng = _rng(seed)
    img = _noise(rng, 400.0, 12.0)
    _scatter_stars(img, rng, 120)
    return _to_adu(img)


def no_light_frame(seed: int = 102) -> np.ndarray:
    """A capped optic: the black level, 9 ADU of noise, and 40 isolated hot
    pixels (single bright cells, which the star detector rejects)."""
    rng = _rng(seed)
    img = _noise(rng, BLACK_LEVEL, BLACK_NOISE)
    ys = rng.integers(0, SIZE, 40)
    xs = rng.integers(0, SIZE, 40)
    img[ys, xs] += rng.uniform(400, 3000, 40)
    return _to_adu(img)


def obstructed_frame(seed: int = 103, dark_level: float = BLACK_LEVEL,
                     sky_level: float = 600.0, stars: int = 8,
                     edge_row: int = 360, wobble: int = 40) -> np.ndarray:
    """A treeline over the lower view: a wavy edge ``wobble`` rows either side
    of ``edge_row``. Above it a sky at ``sky_level`` with ``stars`` stars;
    below it a surface at ``dark_level`` (the black level by default: the tree
    blocks everything, as a cap does).

    A frame's median reads at the black level only when the tree covers most
    of the view: the dark side's own noise puts the median at the dark
    pixels' upper quantile by the lit share, so ``edge_row`` 130 (87 % dark)
    is within ``light``'s 3 ADU band of the black level and the default 360
    (65 % dark) is not."""
    rng = _rng(seed)
    x = np.arange(SIZE)
    edge = edge_row + wobble * np.sin(x / 150.0)
    # A tree is out of focus: the edge is a smooth step a dozen pixels wide,
    # which the star detector does not mistake for sources, and which is
    # still one step at the scale of the 128-pixel blocks the test reads.
    below = 0.5 * (1.0 + np.tanh((np.arange(SIZE)[:, None] - edge[None, :])
                                 / EDGE_SOFTNESS_PX))
    img = ((1.0 - below) * _noise(rng, sky_level, 14.0)
           + below * _noise(rng, dark_level, BLACK_NOISE))
    _scatter_stars(img, rng, stars, rows=(20, edge_row - wobble - 12))
    return _to_adu(img)


def cloud_frame(seed: int = 104) -> np.ndarray:
    """A bright deck: 1500 ADU, a smooth gradient of 300 ADU across the
    frame, 20 ADU of noise, no stars."""
    rng = _rng(seed)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    ramp = 300.0 * (xx / SIZE) + 150.0 * (yy / SIZE)
    return _to_adu(_noise(rng, 1500.0, 20.0) + ramp)


def cloud_bank_frame(seed: int = 105) -> np.ndarray:
    """A cloud bank with a SHARP edge: a bright deck over the right 70 % of
    the view with no stars in it, a clear window on the left with 3 stars.
    The edge is as sharp as a treeline; what differs is which side is bright
    and where the stars are."""
    rng = _rng(seed)
    img = _noise(rng, 400.0, 12.0)
    img[:, 300:] = _noise(rng, 1500.0, 20.0)[:, 300:]
    window = np.zeros((SIZE, SIZE))
    for _ in range(3):
        _add_star(window, int(rng.integers(12, 288)),
                  int(rng.integers(12, SIZE - 12)),
                  float(rng.uniform(1500, 12000)))
    return _to_adu(img + window)


def ramp_frame(seed: int = 106) -> np.ndarray:
    """A smooth sky-glow gradient from 300 to 700 ADU and no stars: vignetting
    or a light dome, with no edge anywhere."""
    rng = _rng(seed)
    yy = np.arange(SIZE)[:, None] * np.ones((1, SIZE))
    return _to_adu(_noise(rng, 300.0, 12.0) + 400.0 * (yy / SIZE))


def noisy_cloud_frame(seed: int = 107) -> np.ndarray:
    """The #1093-class trap, a bright and very noisy deck whose k-sigma peaks
    look like stars (``tests/test_clouds.py`` builds the same one)."""
    rng = _rng(seed)
    return _to_adu(_noise(rng, 36000.0, 3300.0))


def constant_frame(level: int = 240) -> np.ndarray:
    """Every pixel the same: a buffer, not an exposure."""
    return np.full((SIZE, SIZE), level, dtype=np.uint16)


def uniform_dim_frame(seed: int = 108, level: float = 700.0) -> np.ndarray:
    """A starless frame above the black level with no structure at all: a
    clear sky behind a dew-covered optic, say. Nothing here can be named."""
    rng = _rng(seed)
    return _to_adu(_noise(rng, level, 12.0))


def gradual_dark_frame(seed: int = 109) -> np.ndarray:
    """A dark lower view reached by a GRADUAL ramp: a linear fall from a
    1000 ADU sky at the top to the black level at the bottom, with 10 stars in
    the lit upper third. Smooth and dark below, stars above, but no edge: each
    block row steps about an eighth of the whole drop."""
    rng = _rng(seed)
    rows = np.arange(SIZE)[:, None] / (SIZE - 1)
    img = _noise(rng, 0.0, BLACK_NOISE) + (1000.0 - (1000.0 - BLACK_LEVEL) * rows)
    _scatter_stars(img, rng, 10, rows=(20, 330))
    return _to_adu(img)


def rough_dark_frame(seed: int = 110) -> np.ndarray:
    """A SHARP edge and 8 stars in the lit sky above it, but the region below
    is not one surface: 128-pixel patches alternating between the black level
    and 600 ADU, as cloud of many levels or a cluttered foreground would be.
    Sharp and starry, but not smooth."""
    rng = _rng(seed)
    img = _noise(rng, 1000.0, BLACK_NOISE + 1.0)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    patch = ((yy // 128 + xx // 128) % 2).astype(bool)
    low = np.where(patch, 600.0, BLACK_LEVEL)
    img[256:] = (_noise(rng, 0.0, BLACK_NOISE) + low)[256:]
    _scatter_stars(img, rng, 8, rows=(20, 230))
    return _to_adu(img)
