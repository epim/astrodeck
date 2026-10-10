# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``truth/visibility.json``: which horizon bins a scanner could see at all.

A scanner is asked to measure the boundary only where the frames it was given
show one. This module says where that is, from the frames as delivered (blur,
gain, noise and all), the truth poses, the claimed footprint and the truth
horizon (the panorama scanner's spec 13.8). For each of the 720 half-degree
bins, with ``A`` the truth boundary there (the maximum of the 0.1-degree truth
over the bin):

- ``contrast_sigma``: the maximum, over frames whose claimed footprint contains
  both ``(bin centre, A)`` and ``(bin centre, A + 2)``, of the absolute
  difference between the mean luma on the arc from ``A + 0.5`` to ``A + 1.5``
  and on the arc from ``A - 1.5`` to ``A - 0.5``, both along the bin-centre
  azimuth, over ``noise_sigma``. ``None`` where no frame qualifies;
- ``visible``: ``contrast_sigma >= 3``;
- ``footprint_top_deg``: the highest altitude of the footprint region, the
  union of the frames' footprints, along the bin-centre azimuth. ``None`` where
  the region does not reach the bin.

The footprint is the scorer's (:data:`sim.score.FOOTPRINT_HALF_WIDTH_DEG`
across the short axis, :data:`sim.score.FOOTPRINT_LONG_FRACTION` of the long
half-axis along it), read from the same constants so the two cannot drift. It
is evaluated under the TRUTH pose, as the spec's "claimed footprint" says,
while the luma comes from the delivered pixels: a frame whose stabiliser has
moved the view by 1.5 degrees shows the boundary 1.5 degrees away from where
the truth pose puts it, and that frame's contrast suffers for it. That is the
point of grading from the post-processed frame.

An azimuth is a meridian, a great circle through the zenith, and the footprint
is a convex cone, so its intersection with a meridian is one arc and "contains
both ends" means "contains the whole segment". :func:`meridian_footprint`
solves that arc in closed form: each of the cone's five constraints is
``a cos(alt) + b sin(alt) >= 0``, a half circle of altitudes centred on
``atan2(b, a)``, and the arc is the intersection of the half circles.
"""

from __future__ import annotations

import math

import numpy as np

from .frames_post import luma_of
from .score import (FOOTPRINT_HALF_WIDTH_DEG, FOOTPRINT_LONG_FRACTION, PROFILE_BIN_DEG,
                    PROFILE_BINS)

__all__ = ["VISIBLE_SIGMA", "build", "meridian_footprint", "truth_boundary"]

#: A bin is visible when its boundary stands this many noise sigmas clear.
VISIBLE_SIGMA = 3.0
#: Footprint wanted above the boundary, degrees (the sky model needs room).
FOOTPRINT_ABOVE_DEG = 2.0
#: The two luma windows lie this far from the boundary, nearest edge and
#: farthest edge, degrees, and are sampled at this step.
WINDOW_NEAR_DEG = 0.5
WINDOW_FAR_DEG = 1.5
SAMPLE_STEP_DEG = 0.1
#: ``reference-horizon.json`` holds -10 where no altitude is solid.
NO_BOUNDARY_DEG = -9.99
#: An 8-bit frame cannot be quieter than its own rounding, so a noise-free case
#: is divided by this and not by zero.
QUANTISATION_SIGMA = 1.0 / math.sqrt(12.0)


def truth_boundary(reference_horizon: dict) -> np.ndarray:
    """``A`` for each of the 720 bins: the highest truth altitude within the bin."""
    alt_max = np.asarray(reference_horizon["alt_max"], dtype=np.float64)
    if alt_max.size % PROFILE_BINS:
        raise ValueError(f"the truth horizon has {alt_max.size} azimuths, which "
                         f"{PROFILE_BINS} bins do not divide")
    return alt_max.reshape(PROFILE_BINS, -1).max(axis=1)


def _footprint_slopes(camera) -> tuple:
    """``(tan of the half width, long-axis half extent)`` of the claimed footprint."""
    return (math.tan(math.radians(FOOTPRINT_HALF_WIDTH_DEG)),
            FOOTPRINT_LONG_FRACTION * camera.cy / camera.fy)


def meridian_footprint(basis, camera, az_deg) -> tuple:
    """The altitudes the claimed footprint of one frame covers along each azimuth.

    Returns ``(low, high)``, degrees, one entry per azimuth in ``az_deg``, with
    NaN in both where the footprint does not meet that meridian. The footprint
    is the directions in front of the camera with ``|x / z| <= tan 3 deg`` and
    ``|y / z| <= 0.9 cy / fy`` in the camera frame of ``basis``.
    """
    az = np.radians(np.atleast_1d(np.asarray(az_deg, dtype=np.float64)))
    horizontal = np.stack([np.sin(az), np.cos(az), np.zeros_like(az)], axis=-1)
    k_wide, k_long = _footprint_slopes(camera)
    p = {name: horizontal @ vec for name, vec in (
        ("r", basis.right), ("u", basis.up), ("f", basis.forward))}
    q = {"r": basis.right[2], "u": basis.up[2], "f": basis.forward[2]}

    # Each constraint is a cos(alt) + b sin(alt) >= 0 for the altitude on the
    # meridian; the first restricts alt to [-90, 90], the second is z > 0.
    one = np.ones_like(az)
    zero = np.zeros_like(az)
    constraints = [
        (one, zero),
        (p["f"], q["f"] * one),
        (k_wide * p["f"] - p["r"], k_wide * q["f"] - q["r"]),
        (k_wide * p["f"] + p["r"], k_wide * q["f"] + q["r"]),
        (k_long * p["f"] - p["u"], k_long * q["f"] - q["u"]),
        (k_long * p["f"] + p["u"], k_long * q["f"] + q["u"]),
    ]
    centres = [np.arctan2(b, a) for a, b in constraints]
    reference = centres[1]
    high = np.full(az.shape, np.inf)
    low = np.full(az.shape, -np.inf)
    for centre in centres:
        # The half circle, unwrapped to within 180 degrees of the reference.
        near = reference + (centre - reference + math.pi) % (2.0 * math.pi) - math.pi
        high = np.minimum(high, near + math.pi / 2.0)
        low = np.maximum(low, near - math.pi / 2.0)
    # A meridian square on to the optical axis in a level view has no point in
    # front of the camera at all: its z > 0 constraint is the zero vector.
    degenerate = np.hypot(*constraints[1]) < 1e-12
    empty = degenerate | ~(high > low)
    return (np.where(empty, np.nan, np.degrees(low)),
            np.where(empty, np.nan, np.degrees(high)))


def _bilinear(luma: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """``luma`` at index-space ``(x, y)``, where pixel ``(i, j)`` sits at ``(i, j)``."""
    height, width = luma.shape
    x0 = np.clip(np.floor(x).astype(np.intp), 0, width - 1)
    y0 = np.clip(np.floor(y).astype(np.intp), 0, height - 1)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = x - x0
    fy = y - y0
    return (luma[y0, x0] * (1 - fx) * (1 - fy) + luma[y0, x1] * fx * (1 - fy)
            + luma[y1, x0] * (1 - fx) * fy + luma[y1, x1] * fx * fy)


def _sample_directions(az_deg: np.ndarray, alt_deg: np.ndarray) -> np.ndarray:
    az = np.radians(az_deg)
    alt = np.radians(alt_deg)
    return np.stack([np.sin(az) * np.cos(alt), np.cos(az) * np.cos(alt), np.sin(alt)], axis=-1)


def build(frames, images, camera, reference_horizon: dict, noise_sigma: float) -> dict:
    """The contents of ``truth/visibility.json``.

    ``frames`` are the delivered frames' truth records (anything with a
    ``basis``), ``images`` the matching delivered ``(H, W, 3)`` uint8 frames, in
    the same order, ``camera`` the truth intrinsics, ``reference_horizon`` the
    parsed ``reference-horizon.json`` and ``noise_sigma`` the case's frame noise
    (floored at one 8-bit rounding step, which is what a noise-free frame still
    has). The returned dict is JSON-ready.
    """
    sigma = max(float(noise_sigma), QUANTISATION_SIGMA)
    boundary = truth_boundary(reference_horizon)
    centres = (np.arange(PROFILE_BINS) + 0.5) * PROFILE_BIN_DEG
    has_boundary = boundary > NO_BOUNDARY_DEG

    count = int(round((WINDOW_FAR_DEG - WINDOW_NEAR_DEG) / SAMPLE_STEP_DEG)) + 1
    along = np.arange(count) * SAMPLE_STEP_DEG
    offsets = np.concatenate([-WINDOW_FAR_DEG + along, WINDOW_NEAR_DEG + along])
    directions = _sample_directions(centres[:, None], boundary[:, None] + offsets[None, :])

    contrast = np.full(PROFILE_BINS, np.nan)
    top = np.full(PROFILE_BINS, np.nan)
    for frame, image in zip(frames, images, strict=True):
        basis = frame.basis
        low, high = meridian_footprint(basis, camera, centres)
        top = np.fmax(top, high)
        inside = (has_boundary & (low <= boundary)
                  & (boundary + FOOTPRINT_ABOVE_DEG <= high))
        if not inside.any():
            continue
        bins = np.nonzero(inside)[0]
        cam = directions[bins] @ np.column_stack([basis.right, basis.up, basis.forward])
        z = cam[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            x = camera.cx + camera.fx * cam[..., 0] / z - 0.5
            y = camera.cy - camera.fy * cam[..., 1] / z - 0.5
        seen = ((z > 0).all(axis=1) & (x >= 0).all(axis=1) & (x <= camera.width - 1).all(axis=1)
                & (y >= 0).all(axis=1) & (y <= camera.height - 1).all(axis=1))
        if not seen.any():
            continue
        bins, x, y = bins[seen], x[seen], y[seen]
        values = _bilinear(luma_of(image), x, y)
        gap = np.abs(values[:, count:].mean(axis=1) - values[:, :count].mean(axis=1)) / sigma
        contrast[bins] = np.fmax(contrast[bins], gap)

    visible = np.nan_to_num(contrast, nan=-1.0) >= VISIBLE_SIGMA
    return {
        "bins": PROFILE_BINS,
        "noise_sigma": sigma,
        "contrast_sigma": [None if math.isnan(v) else round(float(v), 6) for v in contrast],
        "visible": [bool(v) for v in visible],
        "footprint_top_deg": [None if math.isnan(v) else round(float(v), 6) for v in top],
    }
