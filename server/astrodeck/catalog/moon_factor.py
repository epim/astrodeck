# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Moon's penalty on a night score: one rule for the Atlas's best window
(``catalog.visibility``) and a target pool's "best available" strategy
(``sequence.group_rules``). A leaf module: it imports nothing, so the engine
can use it without the web layer the Atlas pulls in."""
from __future__ import annotations


def moon_factor(sep_deg: float, illumination: float, moon_up: bool) -> float:
    """Moon penalty multiplier for a candidate window.

    ``moon_up=False`` gives 1.0: a 95%-illuminated moon that has set is
    irrelevant, because moon *altitude* gates whether separation matters at
    all (design spec section 9, critique C2-#5). When the moon is up, small
    separations from a bright moon are penalised: full strength at the moon,
    easing to about 1.0 far away, scaled by illumination. Clamped to
    [0.3, 1.0] so a window is never zeroed out.
    """
    if not moon_up:
        return 1.0
    # 0 at the moon, 1 at about 120 degrees away; weighted by how bright the
    # moon is.
    proximity = max(0.0, 1.0 - sep_deg / 120.0)
    penalty = proximity * max(0.0, illumination)
    return max(0.3, min(1.0, 1.0 - 0.7 * penalty))
