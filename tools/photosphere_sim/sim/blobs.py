"""Connected runs of one colour in an RGBA raster.

The scorer decodes the finished panorama by looking for each palette colour in
turn, so the only question this module answers is: where are the connected
patches of pixels that could be this colour, and how big is each one. It knows
nothing about landmarks, azimuth or altitude; :mod:`sim.score` turns a
centroid into a direction.

Two decisions are worth stating because they are easy to get silently wrong.

- Distance is the largest per-channel absolute difference, the same
  (Chebyshev) distance :mod:`sim.palette` uses. A tolerance of 40 therefore
  admits a pixel that is 40 off on all three channels and rejects one that is
  41 off on a single channel.
- The panorama is cyclic in azimuth, so column ``width - 1`` touches column
  ``0``. Without that wrap, the chart yard's two landmarks that straddle north
  would each decode as two blobs and be reported as duplicates. ``wrap_x``
  turns the wrap off for a raster that is not a panorama.

Components are found with an explicit stack rather than recursion (a blob can
be hundreds of pixels long and Python's recursion limit is not a property of
the picture) and without ``scipy``, which the simulator does not depend on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["Blob", "find"]


@dataclass(frozen=True)
class Blob:
    """One connected patch: its centroid in pixel coordinates and its size.

    ``cx`` and ``cy`` are means over the patch's pixel indices, so a patch of
    one pixel at column 7 has ``cx == 7.0``, not 7.5. A patch that crosses the
    seam is averaged in unwrapped coordinates and the result is brought back
    into ``[0, width)``, so a nine-pixel disc centred on column 0 reports
    ``cx == 0.0`` rather than the meaningless mean of 0, 1 and ``width - 1``.
    """

    cx: float
    cy: float
    pixels: int


def find(rgb, alpha, colour, tolerance=40, min_pixels=6, *, wrap_x=True):
    """Every connected patch of ``colour`` in an opaque part of the raster.

    ``rgb`` is ``(H, W, 3)`` and ``alpha`` is ``(H, W)``; a pixel belongs to
    the mask when its largest per-channel difference from ``colour`` is at most
    ``tolerance`` and its alpha is exactly 255. Patches are 4-connected, and
    the columns wrap unless ``wrap_x`` is false. Patches of fewer than
    ``min_pixels`` pixels are dropped.

    The returned list is in raster-scan order of each patch's first pixel, so
    two runs over the same image give the same order.
    """
    rgb = np.asarray(rgb)
    alpha = np.asarray(alpha)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected rgb of shape (H, W, 3), got {rgb.shape}")
    if alpha.shape != rgb.shape[:2]:
        raise ValueError(f"alpha {alpha.shape} does not match rgb {rgb.shape[:2]}")

    height, width = alpha.shape
    if height == 0 or width == 0:
        return []

    target = np.asarray(colour, dtype=np.int16).reshape(1, 1, 3)
    distance = np.abs(rgb.astype(np.int16) - target).max(axis=2)
    mask = (distance <= tolerance) & (alpha == 255)

    # ``visited`` is the mask itself, consumed as the fill proceeds: a pixel
    # leaves the mask the moment it is pushed, which is what keeps a pixel off
    # the stack twice.
    remaining = mask.copy()
    found: list[Blob] = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if not remaining[y0, x0]:
            continue
        remaining[y0, x0] = False
        stack = [(int(y0), int(x0), int(x0))]
        sum_x = 0
        sum_y = 0
        count = 0
        while stack:
            y, x, unwrapped = stack.pop()
            sum_x += unwrapped
            sum_y += y
            count += 1
            if y > 0 and remaining[y - 1, x]:
                remaining[y - 1, x] = False
                stack.append((y - 1, x, unwrapped))
            if y + 1 < height and remaining[y + 1, x]:
                remaining[y + 1, x] = False
                stack.append((y + 1, x, unwrapped))
            for step in (-1, 1):
                nx = x + step
                if nx < 0 or nx >= width:
                    if not wrap_x:
                        continue
                    nx %= width
                if remaining[y, nx]:
                    remaining[y, nx] = False
                    stack.append((y, nx, unwrapped + step))
        if count < min_pixels:
            continue
        found.append(Blob(cx=(sum_x / count) % width, cy=sum_y / count, pixels=count))
    return found
