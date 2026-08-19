"""`stats.max` cannot tell one railed pixel from a blown field.

The preview's "Stars saturated - shorten exposure or lower gain" banner fired on
`stats.max >= full_well`, which on any deep-sky sub is permanently true: some
bright star always rails. Measured on the rig 2026-08-18, a 60s B sub of NGC
7129 carried 169 saturated pixels out of 26,108,352 - 0.0006% - and the banner
was lit. A warning that is always on carries no information and trains the
operator to ignore the one time it matters.
"""
from __future__ import annotations

import numpy as np

from astrodeck.imaging.processing import frame_stats


def _frame(n_clipped: int, full_well: int = 65535) -> np.ndarray:
    d = np.full((400, 400), 500, dtype=np.uint16)
    flat = d.ravel()
    flat[:n_clipped] = full_well
    return d


def test_stats_carry_a_clipped_COUNT_not_just_the_max():
    s = frame_stats(_frame(169), full_well=65535)
    assert s["clipped"] == 169, s
    assert s["max"] == 65535


def test_the_count_is_absent_when_the_well_depth_is_unknown():
    """On a camera that does not report full_well there is nothing to count
    against, and inventing a threshold would be worse than saying nothing."""
    s = frame_stats(_frame(169))
    assert "clipped" not in s, s


def test_a_frame_with_no_clipping_reports_zero_not_missing():
    """Zero is a measurement; absent means 'could not measure'. The UI branches
    on the difference."""
    s = frame_stats(_frame(0), full_well=65535)
    assert s["clipped"] == 0


def test_the_legacy_fields_are_untouched():
    """Every existing caller passes no full_well and must get exactly what it
    got before - this dict is persisted into frame records."""
    d = _frame(5)
    assert set(frame_stats(d)) == {"min", "max", "mean", "median", "std"}


def test_live_view_keeps_the_clipped_count():
    """hub._publish_preview recomputes stats from the live STACK mean, and did so
    with no well depth — dropping `clipped` for every frame of the session.
    FocusVerdict requires `clipped != null`, so the saturation advice silently
    vanished the moment Live View was armed, while `stats.max` was still
    recomputed and still >= full_well, so the CLIP chip and the histogram's
    CLIPPED tag stayed lit. The alarm without the advice, and only in Live View.

    Two earlier attempts at this test both passed against the broken code:
    grepping for "frame_stats(" missed `asyncio.to_thread(frame_stats, data)`,
    and a starless frame never reaches the branch at all because the stacker
    produces no mean without stars to align on. It needs a real star field.
    """
    import asyncio
    import math
    import numpy as np
    from astrodeck.hub import Hub

    class _Frame:
        rendered_bytes = None
        def __init__(self, data):
            self.data = data
            self.exposure_s = 1.0
            self.gain = 100
            self.binning = 1
            self.full_well = 65535
            self.bayer_pattern = None
            self.saved_path = None
            self.data_is_linear = True

    def _stars_and_clipping(cx, cy):
        rng = np.random.default_rng(1)
        img = rng.normal(400, 5.0, (200, 240))
        for dx, dy in ((0, 0), (30, 20), (-40, 35), (55, -25), (-20, -45)):
            xs = np.arange(240) - (cx + dx)
            ys = (np.arange(200) - (cy + dy))[:, None]
            img += 300_000.0 * np.exp(-(xs**2 + ys**2) / (2 * 1.6**2)) / (2 * math.pi * 1.6**2)
        img = np.clip(img, 0, 65535).astype(np.uint16)
        img[:20, :] = 65535                       # a blown region, 10% of the frame
        return img

    hub = Hub()
    unarmed = asyncio.run(hub._publish_preview(_Frame(_stars_and_clipping(120, 100))))
    assert unarmed["stats"].get("clipped"), "premise: unarmed reports the count"

    assert hub.start_live_stack()["active"] is True
    for cx, cy in ((120, 100), (122, 99), (119, 101)):
        info = asyncio.run(hub._publish_preview(_Frame(_stars_and_clipping(cx, cy))))
    assert (info.get("livestack") or {}).get("frames"), "premise: the stack accumulated"
    assert info["stats"].get("clipped") is not None, (
        "Live View dropped stats.clipped, so the saturation advice cannot render "
        f"for the whole session — while stats.max ({info['stats'].get('max')}) "
        "still trips the CLIP chip")
