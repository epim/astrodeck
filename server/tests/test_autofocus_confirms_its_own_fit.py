"""A sweep must not move to a position its own confirming frame calls worse.

The engine measures ONE frame at the fitted vertex before reporting done. On the
rig 2026-08-19 that frame came back worse than a sample already in hand in all
three sweeps of the night, and the sweep moved there anyway:

    22:58  best sample 11177 -> 3.05   fit 11167, confirm 3.78  (+24%)
    23:18  best sample 11169 -> 3.24   fit 11188, confirm 3.43  (+6%)
    23:39  best sample 11188 -> 3.01   fit 11179, confirm 3.90  (+30%)

It reached the subs: the same six filters measured 10-20% better at base 11186
than at base 11167, and ~15 minutes of the run was spent at the worse one.

The margin matters, and the night decides it. The 23:18 sweep's +6% was NOISE --
its move to 11188 produced the BEST frames of the night, so a rule that
overrode on any regression would have made that sweep worse. The two that
mattered were +24% and +30%.
"""
from __future__ import annotations

import pytest

from astrodeck.focus.autofocus import CONFIRM_REGRESSION_FRAC, confirmed_best

A = [(11457, 19.67), (11387, 17.35), (11317, 12.26), (11247, 6.75), (11177, 3.05),
     (11107, 5.53), (11037, 11.04), (10967, 15.75), (10897, 21.79), (11167, 3.78)]
B = [(11449, 18.47), (11379, 15.66), (11309, 10.71), (11239, 4.69), (11169, 3.24),
     (11099, 6.81), (11029, 12.57), (10959, 17.48), (10889, 23.81), (11188, 3.43)]
C = [(11468, 19.77), (11398, 17.02), (11328, 12.10), (11258, 7.12), (11188, 3.01),
     (11118, 5.50), (11048, 10.90), (10978, 16.32), (10908, 15.59), (11179, 3.90)]


def test_the_2258_sweep_keeps_the_position_it_measured_best():
    got = confirmed_best(A, 11167)
    assert got.position == 11177, (
        f"settled on {got.position}; 11167 measured 3.78 against 3.05 already "
        "in hand at 11177")
    assert got.overridden
    assert got.measured_hfr == 3.05


def test_the_2339_sweep_keeps_the_position_it_measured_best():
    got = confirmed_best(C, 11179)
    assert got.position == 11188 and got.overridden, got


def test_the_2318_sweep_is_LEFT_ALONE_because_6_percent_is_noise():
    """The one that must not be overridden. Its fitted move was right — the
    frames that followed were the best of the night — and its confirming frame
    was only 6% off the best sample. A rule with no margin would have undone the
    one sweep that worked."""
    got = confirmed_best(B, 11188)
    assert got.position == 11188, got
    assert not got.overridden
    assert got.measured_hfr == 3.43, "the honest number is what was MEASURED there"


def test_a_fit_with_no_confirming_measurement_is_trusted():
    """No frame at the fitted position means nothing contradicts it. Falling
    back to the best sample there would throw away the interpolation the fit
    exists to provide."""
    no_confirm = [p for p in A if p[0] != 11167]
    got = confirmed_best(no_confirm, 11167)
    assert got.position == 11167 and not got.overridden
    assert got.measured_hfr is None


def test_the_margin_is_the_documented_one():
    assert CONFIRM_REGRESSION_FRAC == pytest.approx(0.15), (
        "tonight's three sweeps are the calibration: +6% must pass, +24% and "
        "+30% must not")


def test_a_confirm_better_than_every_sample_is_kept():
    """The normal, healthy case: the fit interpolated between samples and landed
    somewhere better than any of them. That is the whole point of fitting."""
    pts = [(1000, 8.0), (1100, 4.0), (1200, 4.1), (1300, 9.0), (1150, 3.2)]
    got = confirmed_best(pts, 1150)
    assert got.position == 1150 and not got.overridden
    assert got.measured_hfr == 3.2
