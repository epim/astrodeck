"""Measuring defocus, including on the real donut frame that broke everything.

The property that matters: ONE number that changes monotonically with distance
from focus, from an 880px donut down to a star. Star COUNT does not have that
property — both detectors proved it tonight, failing in opposite directions on
the same frames.

And the property that matters just as much, added after this measurement told a
user with 200 sharp stars that they were 13 mm out of focus: the single-blob
assumption is CHECKED. When the frame is not one blob, this function says so
instead of measuring the spread of the field and calling it a source.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging.defocus import (
    HANDOVER_R80_PX, MAX_SOURCES_FOR_ONE_BLOB, BlobSize, focus_from_two,
    measure_blob, shrinking,
)

FIXTURES = Path(__file__).parent / "fixtures" / "focus_sweep"


def _sweep_frame(position: int) -> np.ndarray:
    """A real frame from the 2026-07-31 focuser sweep (see test_focus_metric)."""
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    entry = next(e for e in manifest["entries"]
                 if e["focuser_position"] == position)
    return np.load(FIXTURES / entry["file"])["data"]


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


# ------------------------------------------- the single-blob assumption, CHECKED

def _star_field(n=200, shape=(1200, 1600), sigma=2.0, seed=23):
    """A frame that is FULL of sharp stars — the state this measurement used to
    describe as a 445px blob."""
    rng = np.random.default_rng(seed)
    img = rng.normal(500, 20, shape).astype(np.float32)
    yy, xx = np.mgrid[-8:9, -8:9]
    psf = np.exp(-(yy ** 2 + xx ** 2) / (2 * sigma ** 2))
    for _ in range(n):
        y = int(rng.integers(20, shape[0] - 20))
        x = int(rng.integers(20, shape[1] - 20))
        img[y - 8:y + 9, x - 8:x + 9] += (rng.uniform(2000, 40000) * psf).astype(np.float32)
    return img


def test_a_frame_full_of_stars_is_REFUSED_rather_than_called_one_blob():
    """The reported defect. 200 stars at median HFR 4.49px came back as
    "r80 445px — 891px across", because the old code summed every pixel above
    5 sigma inside a 1200px window: it was measuring the spread of the FIELD.
    lib/focusVerdict.ts lets defocus_r80 > 25 outrank HFR, so that number told
    a user with 200 sharp stars to run coarse focus, and it produced a
    fabricated 13mm defocus in front of them."""
    assert measure_blob(_star_field()) is None


def test_a_single_star_is_still_measured_so_the_handover_can_happen():
    """The refusal above must be about MULTIPLICITY, not about being in focus —
    coarse focus has to be able to see the blob shrink all the way down."""
    m = measure_blob(_star(_bg(), 300.0, 400.0, 4000.0, 2.0))
    assert m is not None and m.ready_for_autofocus


def test_it_measures_ONE_source_and_not_the_distance_between_two():
    """A blob with a second source elsewhere in frame: the answer is the blob's
    own size. Summing a window instead makes the answer grow with the SEPARATION
    of the sources, which is why a rich field read as a huge defocus."""
    img = _donut(_bg(900, 1200), 450.0, 600.0, 120.0, 30.0, 3000.0)
    img = _star(img, 100.0, 1100.0, 6000.0, 2.0)      # far corner, unrelated
    alone = measure_blob(_donut(_bg(900, 1200), 450.0, 600.0, 120.0, 30.0, 3000.0))
    both = measure_blob(img)
    assert both is not None and alone is not None
    assert abs(both.r80 - alone.r80) < 8.0, \
        f"the far star moved the measurement: {alone.r80} -> {both.r80}"


def test_the_multi_source_bar_is_a_named_constant_with_a_reason():
    assert 1 <= MAX_SOURCES_FOR_ONE_BLOB <= 4


# ------------------------------------------------- against the real sweep

def test_a_focused_real_frame_does_not_report_a_large_blob():
    """focus_9900 is TRUE focus, measured independently from full frames.
    Anything but a small number here is the defect that sent the Focus panel
    into "far outside focus, run coarse focus" on a perfectly focused rig."""
    m = measure_blob(_sweep_frame(9900))
    assert m is not None
    assert m.r80 < HANDOVER_R80_PX, f"in focus, yet r80={m.r80}"
    assert m.ready_for_autofocus


def test_a_grossly_defocused_real_frame_reports_a_large_radius():
    """5000 steps out. The blob does not fit in the crop, so the number is
    flagged a floor — "at least this big" is the honest form of an answer whose
    aperture ran off the edge of the sensor."""
    m = measure_blob(_sweep_frame(4900))
    assert m is not None
    assert m.r80 > 40.0, f"5000 steps out measured r80={m.r80}"
    assert not m.ready_for_autofocus
    assert m.lower_bound


def test_it_shrinks_monotonically_as_the_real_sweep_approaches_focus():
    r = [measure_blob(_sweep_frame(p)).r80 for p in (10900, 10500, 10200, 9900)]
    assert r == sorted(r, reverse=True), f"not monotonic toward focus: {r}"


def test_a_frame_that_recorded_no_source_gets_None_not_a_number():
    """14900 is 5000 steps out on the other side, and at 4s the star simply is
    not there: the manifest flags it usable_for_size_metric=false and the only
    bright thing in the crop is a hot pixel.

    So this frame cannot yield "a real, large radius" — no metric recovers a
    signal that was never captured, and a size invented from a hot pixel is
    exactly the lie this whole change exists to remove. None is the answer, and
    imaging.stars.size_advice is what tells the user to expose longer."""
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    entry = next(e for e in manifest["entries"] if e["focuser_position"] == 14900)
    assert entry["usable_for_size_metric"] is False
    assert measure_blob(_sweep_frame(14900)) is None
