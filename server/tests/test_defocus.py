"""Measuring defocus, including on the real donut frame that broke everything.

The property that matters: ONE number that changes monotonically with distance
from focus, from an 880px donut down to a star. Star COUNT does not have that
property — both detectors proved it tonight, failing in opposite directions on
the same frames.
"""
import numpy as np
import pytest

from astrodeck.imaging.defocus import (
    HANDOVER_R80_PX, BlobSize, focus_from_two, measure_blob, shrinking,
)


def _bg(h=600, w=800, level=600.0, noise=20.0, seed=5):
    rng = np.random.default_rng(seed)
    return rng.normal(level, noise, (h, w)).astype(np.float32)


def _star(img, cy, cx, peak, sigma):
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    return img + peak * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sigma ** 2)))


def _donut(img, cy, cx, radius, width, peak, obstruction=0.35):
    """A defocused star: bright annulus, dark centre. What the rig actually
    produced — including the central obstruction that makes it a ring."""
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    r = np.hypot(yy - cy, xx - cx)
    ring = (r < radius) & (r > radius * obstruction)
    out = img.copy()
    out[ring] += peak
    # soften the edges so it is not a perfect binary disc
    return out


# ------------------------------------------------------------ the metric

def test_a_focused_star_measures_small():
    m = measure_blob(_star(_bg(), 300.0, 400.0, 4000.0, 2.0))
    assert m is not None
    assert m.r80 < 30, f"a sigma=2px star should be small, got r80={m.r80}"
    assert m.ready_for_autofocus or m.r80 < 30


def test_a_donut_measures_LARGE():
    """The case that matters. A 200px-radius annulus must not come back looking
    like a star — that is what the 15px-box HFR did on every frame all night."""
    m = measure_blob(_donut(_bg(900, 1200), 450.0, 600.0, 200.0, 40.0, 3000.0))
    assert m is not None
    assert m.r80 > 100, f"a 200px-radius donut must measure large, got {m.r80}"
    assert not m.ready_for_autofocus


def test_the_metric_grows_with_defocus():
    """THE property. Star count does not have it; this must."""
    sizes = []
    for radius in (20.0, 60.0, 120.0, 200.0):
        m = measure_blob(_donut(_bg(900, 1200), 450.0, 600.0, radius, 20.0, 3000.0))
        assert m is not None
        sizes.append(m.r80)
    assert sizes == sorted(sizes), f"not monotonic with defocus: {sizes}"
    assert sizes[-1] > 3 * sizes[0], f"must span a real range: {sizes}"


def test_a_hot_pixel_does_not_capture_the_measurement():
    """A single blazing cell is brighter than any star; centring on it would
    measure noise instead of the blob."""
    img = _donut(_bg(900, 1200), 450.0, 600.0, 150.0, 30.0, 2000.0)
    img[100, 100] = 60000.0
    m = measure_blob(img)
    assert m is not None
    assert abs(m.x - 600) < 120 and abs(m.y - 450) < 120, \
        f"measured at ({m.x},{m.y}), expected near the donut at (600,450)"


def test_an_empty_frame_returns_None_rather_than_a_number():
    """A fabricated size from pure noise would send the search the wrong way."""
    assert measure_blob(_bg()) is None


def test_a_tiny_or_malformed_frame_is_refused():
    assert measure_blob(np.zeros((4, 4), dtype=np.float32)) is None
    assert measure_blob(np.zeros((10, 10, 3), dtype=np.float32)) is None


# ------------------------------------------------- extrapolating to focus

def test_two_measurements_locate_focus():
    """D = k*|x - x_focus|: with focus at 12000 and a slope of 0.02 px/step,
    positions 2000 and 7000 give blobs of 200 and 100."""
    got = focus_from_two(2000, 200.0, 7000, 100.0)
    assert got is not None and abs(got - 12000) < 1.0, got


def test_extrapolation_works_from_the_other_side():
    got = focus_from_two(20000, 160.0, 15000, 60.0)
    assert got is not None and abs(got - 12000) < 1.0, got


def test_an_unchanged_blob_gives_no_answer():
    """Rather than an infinite or wild extrapolation from measurement noise."""
    assert focus_from_two(1000, 150.0, 2000, 150.0) is None
    assert focus_from_two(1000, 150.0, 1000, 90.0) is None


def test_shrinking_reports_unknown_inside_the_noise():
    """"Move further before deciding" is the honest answer to a 1px change, and
    it is different from "no, it grew"."""
    assert shrinking(200.0, 150.0) is True
    assert shrinking(150.0, 200.0) is False
    assert shrinking(150.0, 151.0) is None


# ------------------------------------------------------- the handover bar

def test_the_handover_bar_separates_a_star_from_a_donut():
    star = measure_blob(_star(_bg(), 300.0, 400.0, 4000.0, 2.0))
    donut = measure_blob(_donut(_bg(900, 1200), 450.0, 600.0, 150.0, 30.0, 3000.0))
    assert star.r80 < HANDOVER_R80_PX < donut.r80, (
        f"star {star.r80}, bar {HANDOVER_R80_PX}, donut {donut.r80}")
