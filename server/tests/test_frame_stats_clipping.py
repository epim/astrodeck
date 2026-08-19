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
