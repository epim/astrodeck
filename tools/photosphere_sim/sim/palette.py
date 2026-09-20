"""The landmark palette: 24 colours a decoder can tell apart.

Every channel is one of 0, 128 or 255, and the three greys (0, 0, 0),
(128, 128, 128) and (255, 255, 255) are left out, because the scene's own
surfaces are greyish and the background noise is grey by construction. That
leaves 3 * 3 * 3 - 3 = 24 colours, no two of which share a channel pattern.

The order is fixed and part of the contract, because scenes address colours by
index: red slowest, then green, then blue fastest, skipping the greys. So
index 0 is (0, 0, 128), index 8 is (128, 0, 0) and index 23 is (255, 255, 128).

Distance is the largest per-channel absolute difference (Chebyshev), not a
Euclidean or perceptual distance: it answers the only question asked of it,
which is whether some other colour could be confused for a palette colour on
any single channel.
"""

from __future__ import annotations

__all__ = ["LEVELS", "PALETTE", "nearest"]

LEVELS: tuple[int, int, int] = (0, 128, 255)

PALETTE: list[tuple[int, int, int]] = [
    (r, g, b) for r in LEVELS for g in LEVELS for b in LEVELS if not r == g == b
]


def nearest(rgb) -> tuple[int, float]:
    """The palette colour closest to ``rgb``, as ``(index, distance)``.

    Distance is the largest per-channel absolute difference. Ties go to the
    lowest index. ``rgb`` is any sequence of three numbers; it is not required
    to be a whole-numbered colour.
    """
    r, g, b = (float(c) for c in rgb)
    best_index = -1
    best_distance = float("inf")
    for index, (pr, pg, pb) in enumerate(PALETTE):
        distance = max(abs(r - pr), abs(g - pg), abs(b - pb))
        if distance < best_distance:
            best_index, best_distance = index, distance
    return (best_index, best_distance)
