"""There is no upper HFR gate in ``detect_stars``, and this says why (#192).

The removed line was ``if hfr <= 0.05 or hfr > half: continue`` — reject any
star measured larger than the cutout's half-width. It had never rejected
anything, and its own comment conceded as much.

Its twin in the guide crate (131d446) was closed the other way, by DERIVING a
default the gate could actually reach. That was the right call there because a
real, physically-plausible oversized star existed just past the sensible
ceiling. It is the wrong call here, and the difference is measurable rather
than stylistic: this measurement SATURATES, so the sharp population and the
badly-defocused population overlap, and no threshold exists that rejects one
without rejecting the other. A dial with no setting that does the job is not a
dial that needs a better default.

Everything below is measured on the real sky fixtures — the same focus sweep
the autofocus metric was built against — because the claim being made is about
what this code produces on data, not about what it could produce in principle.
"""
from __future__ import annotations

import inspect
import math

import numpy as np
import pytest

from astrodeck.imaging import stars as stars_mod
from astrodeck.imaging.stars import (HFR_BOX_CEILING_FRACTION, HFR_BOX_PX,
                                     HFR_BOX_PX_CEILING, detect_stars)

_FIXTURES = ["focus_sweep/focus_4900", "focus_sweep/focus_7900",
             "focus_sweep/focus_8900", "focus_sweep/focus_9300",
             "focus_sweep/focus_9600", "focus_sweep/focus_9900",
             "focus_sweep/focus_10200", "focus_sweep/focus_10500",
             "focus_sweep/focus_10900", "focus_sweep/focus_11900",
             "star_noise/star_field"]

#: Near focus (62 detections) versus 1000 focuser steps off it (22).
_SHARP = "star_noise/star_field"
_BLOATED = "focus_sweep/focus_8900"


def _frame(name: str) -> np.ndarray:
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / f"{name}.npz"
    with np.load(path) as z:
        return z[list(z.keys())[0]].astype(np.float64)


def _hfrs(name: str) -> list[float]:
    return [s.hfr for s in detect_stars(_frame(name))]


def _every_hfr() -> list[float]:
    out: list[float] = []
    for name in _FIXTURES:
        out += _hfrs(name)
    return out


# ============================================ the bound, derived not borrowed

def test_the_arithmetic_ceiling_is_half_the_cutouts_diagonal():
    """``hfr`` is the flux-weighted mean distance from the flux-weighted
    centroid, over a square cutout of side ``HFR_BOX_PX - 1``. The centroid is
    mass-weighted, so it is inside that square; the mean distance is maximised
    by two equal point masses at opposite corners, which puts the centroid at
    the centre and every unit of mass half a diagonal away.

    Checked here against the arithmetic itself rather than against a number
    typed into the source, so the constant cannot drift away from the geometry
    it claims to come from — this is the guide crate's CENTROID_DISK_RADIUS_PX
    idea applied to a square aperture instead of a disc.
    """
    assert HFR_BOX_PX_CEILING == pytest.approx((HFR_BOX_PX - 1) / math.sqrt(2))
    assert HFR_BOX_PX_CEILING == pytest.approx(9.899, abs=0.01)
    # And the deleted gate sat BELOW it, which is why "the gate was above its
    # own supremum" is NOT the argument here — the measured one below is.
    assert HFR_BOX_PX // 2 < HFR_BOX_PX_CEILING


def test_the_saturation_value_is_where_a_flat_cutout_lands():
    """``HFR_BOX_CEILING_FRACTION`` is not the arithmetic bound, it is where the
    measurement piles up: the mean distance from the centre of a uniformly
    filled square of half-width h is (sqrt2 + ln(1+sqrt2))/3 * h = 0.765h."""
    analytic = (math.sqrt(2) + math.log(1.0 + math.sqrt(2))) / 3.0
    assert HFR_BOX_CEILING_FRACTION == pytest.approx(analytic, abs=0.01)


# ========================================= what the code actually produces

def test_every_real_detection_lands_far_below_the_deleted_gate():
    """204 detections across a 10,000-step focus sweep. If the old gate had
    ever fired, this is where it would show."""
    hfrs = _every_hfr()
    assert len(hfrs) > 150, (
        f"only {len(hfrs)} detections across the whole sweep, against 204 when "
        f"this was written — either the fixtures moved or something is now "
        f"rejecting detections that used to survive, which is what a new upper "
        f"HFR gate does")
    assert max(hfrs) < HFR_BOX_PX // 2, (
        f"the largest HFR on real sky is {max(hfrs):.3f} px against a deleted "
        f"gate at {HFR_BOX_PX // 2}; if this ever reverses, the gate was doing "
        f"something after all and this file is wrong")
    # It creeps PAST the saturation value, which is the correction this task
    # also made: 0.77h is where the measurement piles up, not a hard ceiling.
    # This half is also what notices a new upper gate: any threshold low enough
    # to fire truncates the top of this distribution, and a truncated top lands
    # under 0.77h.
    saturation = HFR_BOX_CEILING_FRACTION * (HFR_BOX_PX // 2)
    assert max(hfrs) > saturation, (
        f"the largest HFR on real sky is {max(hfrs):.3f}, under the {saturation:.3f} "
        f"a saturating cutout reaches — the top of the distribution is being "
        f"cut off, which is what an upper HFR gate does when it fires")


def test_no_threshold_separates_a_sharp_frame_from_a_bloated_one():
    """THE REASON THE GATE WAS REMOVED RATHER THAN RE-DERIVED.

    A gate is worth having when some value of it rejects what you want gone and
    keeps what you want. Here the two populations overlap, so no such value
    exists: every cut that rejects a defocused star also rejects sharp stars
    from a frame at focus.
    """
    sharp = _hfrs(_SHARP)
    bloated = _hfrs(_BLOATED)
    assert sharp and bloated
    overlap_lo, overlap_hi = max(min(sharp), min(bloated)), min(max(sharp),
                                                               max(bloated))
    assert overlap_lo < overlap_hi, (
        f"sharp {min(sharp):.2f}..{max(sharp):.2f} and bloated "
        f"{min(bloated):.2f}..{max(bloated):.2f} no longer overlap — a "
        f"separating HFR threshold now EXISTS, and #192's reasoning for "
        f"deleting the gate instead of deriving one has stopped holding")


def test_the_floor_is_a_division_guard_and_is_labelled_as_one():
    """The lower half of the old condition survives. It is not a size policy —
    it stops a cutout whose flux collapsed onto one pixel from reporting an
    infinitely sharp star — and nothing on real sky comes near it."""
    assert min(_every_hfr()) > 0.05 * 10


# ================================== the knob that pretended to tune the gate

def test_detect_stars_takes_no_box_argument():
    """``box=`` was the only thing that could move the deleted threshold, and no
    caller in the tree ever passed it. Leaving it would leave the appearance of
    a tuning dial over a decision this module has made and documented (growing
    the cutout was tried: a wider box on a dense field measures the CLUSTER)."""
    params = inspect.signature(detect_stars).parameters
    assert "box" not in params, (
        "detect_stars grew a box= argument back; the module header records why "
        "changing it is wrong, so it must not be offered as if it were tunable")
    assert stars_mod.HFR_BOX_PX == 15


def test_the_cutout_size_still_reaches_the_measurement():
    """HFR_BOX_PX is not decoration: it sets the aperture, so moving it moves
    the numbers. Without this the constant could be ignored by the code and
    every assertion above would be about nothing."""
    sharp = _hfrs(_SHARP)
    from unittest.mock import patch

    with patch.object(stars_mod, "HFR_BOX_PX", 31):
        wider = _hfrs(_SHARP)
    assert wider, "no detections at all with a wider cutout"
    assert max(wider) > max(sharp) * 1.2, (
        f"doubling the cutout ({max(sharp):.2f} -> {max(wider):.2f}) did not "
        f"move the measurement — HFR_BOX_PX is not reaching detect_stars")
