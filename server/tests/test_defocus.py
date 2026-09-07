"""Measuring defocus, including on the real donut frame that broke everything.

The property that matters: ONE number that changes monotonically with distance
from focus, from an 880px donut down to a star. Star COUNT does not have that
property — both detectors proved it tonight, failing in opposite directions on
the same frames.

And the property that matters just as much, added after this measurement told a
user with 200 sharp stars that they were 13 mm out of focus: it measures ONE
SOURCE, on that source's own aperture. Never a window's total flux, which is
what made the answer grow with the SEPARATION of the stars in frame.

It measures one source; it does not REFUSE crowded frames. A first attempt at
this fix did refuse them, and that bricked coarse focus, which has no second
instrument and reads no-answer as a reason to tell the user to go outside and
check the sky. So the tests below pin both halves: an honest small number on a
field of sharp stars, and an answer at every defocus radius on a field of many.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging.defocus import (
    HANDOVER_R80_PX, BlobSize, focus_from_two, measure_blob, measure_defocus,
    shrinking,
)

FIXTURES = Path(__file__).parent / "fixtures" / "focus_sweep"
NGC604 = Path(__file__).parent / "fixtures" / "ngc604_20260906"


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


# --------------------------------- ONE source, and never a refusal to answer

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


def _donut_field(n, radius, shape=(1200, 1600), peak=500.0, seed=7):
    """Every star in the frame defocused by the same amount — what a rig
    actually shows part-way out, and what coarse focus has to measure."""
    rng = np.random.default_rng(seed)
    img = rng.normal(600, 20, shape).astype(np.float32)
    m = int(radius) + 8
    yy, xx = np.mgrid[-m:m + 1, -m:m + 1]
    r = np.hypot(yy, xx)
    blob = np.where((r <= radius) & (r >= 0.35 * radius), peak, 0.0) \
        if radius >= 3 else peak * 20 * np.exp(-(r ** 2) / (2 * 2.0 ** 2))
    for _ in range(n):
        y = int(rng.integers(m + 2, shape[0] - m - 2))
        x = int(rng.integers(m + 2, shape[1] - m - 2))
        img[y - m:y + m + 1, x - m:x + m + 1] += blob.astype(np.float32)
    return img


def test_a_frame_full_of_stars_measures_ONE_STAR_not_the_spread_of_the_field():
    """The reported defect. 200 stars at median HFR 4.49px came back as
    "r80 445px — 891px across", because the old code summed every pixel above
    5 sigma inside a 1200px window: it was measuring the spread of the FIELD.
    lib/focusVerdict.ts lets defocus_r80 > 25 outrank HFR, so that number told
    a user with 200 sharp stars to run coarse focus, and it produced a
    fabricated 13mm defocus in front of them.

    The honest answer on that frame is a few pixels, because one sharp star IS
    a few pixels — not a refusal, which would leave coarse focus with nothing."""
    m = measure_blob(_star_field())
    assert m is not None, "refusing here is what bricked coarse focus"
    assert m.r80 < HANDOVER_R80_PX, f"200 sharp stars measured r80={m.r80}"
    assert m.ready_for_autofocus
    assert m.n_sources > 2, "the frame's multiplicity is reported, not hidden"


def test_coarse_focus_gets_an_answer_at_every_defocus_on_a_crowded_field():
    """The regression a review caught. With a multi-source refusal in place this
    returned None for EVERY radius here, and focus.coarse — which has no second
    instrument — aborted on the first probe with "nothing bright enough to
    measure anywhere in the frame — check the sky, the cover, and that the
    camera is exposing", on a frame holding sixty sources.

    Defocus is a property of the optical train, so every source in the frame
    carries the same blur and the dominant one is a good answer whether it has
    company or not."""
    sizes = []
    for radius in (0, 8, 20, 45, 90):
        m = measure_blob(_donut_field(60, radius))
        assert m is not None, f"no answer at radius {radius}: coarse focus dies here"
        sizes.append(m.r80)
    assert sizes == sorted(sizes), f"not monotonic with defocus: {sizes}"
    assert sizes[0] < HANDOVER_R80_PX < sizes[-1], \
        f"a crowded field must still cross the handover bar: {sizes}"


def test_a_single_star_is_still_measured_so_the_handover_can_happen():
    """Coarse focus has to be able to see the blob shrink all the way down."""
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


# ------------------------------- a star field is not a defocus blob (2026-09-07)
#
# THE DEFECT, from the rig at 01:17. A 60 s L sub of NGC 604 that the run's own
# grader read at HFR 3.32 with 1294-1416 stars was shown as
#
#     Far out of focus - Blob is 2268 px across - further out than an autofocus
#     sweep can bracket. Run coarse focus first
#
# because hub.py published `measure_blob`'s r80 on every linear preview and the
# blob it found was M33. `measure_blob` is not wrong -- the dominant source in
# that frame really is that big -- it is being asked the wrong question, and
# `measure_defocus` is the one that asks the right one.


def _ngc604(name: str) -> np.ndarray:
    from astropy.io import fits
    return fits.getdata(NGC604 / f"{name}.fits.gz").astype(np.float64)


def test_the_blob_on_an_in_focus_galaxy_frame_is_not_a_defocus_reading():
    """m33field_G60: 2048x1536 of last night's G sub, M33's core and its star
    field together, graded HFR 3.43 over 200 detections. In focus.

    `measure_blob` reads r80 466 px here -- 932 px across -- and every clean
    WHOLE frame from the same night reads worse: R_0007 1154, R_0030 1266, on
    fields whose stars measure 2.73 and 2.94 px. That number is what reached the
    Focus panel."""
    from astrodeck.imaging.stars import median_hfr
    data = _ngc604("m33field_G60")
    grader, n = median_hfr(data)
    assert grader is not None and grader < 3.8 and n >= 100, (
        f"fixture drifted: {n} stars at HFR {grader}")

    raw = measure_blob(data)
    assert raw is not None and raw.r80 > 100.0, (
        f"fixture drifted: measure_blob reads {raw.r80 if raw else None} here, "
        "so this frame no longer reproduces the defect")

    assert measure_defocus(data) is None, (
        f"a frame with {n} stars at HFR {grader:.2f} px was reported "
        f"{2 * raw.r80:.0f} px across")


def test_a_real_donut_field_still_reports_its_blob_at_every_size():
    """The other half, and the half a looser rule would break. A donut field
    ALSO carries hundreds of "stars" -- rim fragments -- at a box HFR of 3.9-5.0,
    so no star COUNT and no HFR bar near 6 px separates the two. What separates
    them is that the box cannot describe a rim fragment: see
    `stars.compact_star_population`."""
    for name in ("donut_L60", "donutfield_L60"):
        data = _ngc604(name)
        raw = measure_blob(data)
        got = measure_defocus(data)
        assert raw is not None
        assert got is not None, (
            f"{name}: a defocused frame was mistaken for a star field, so "
            "coarse focus and the Focus panel both lose their only signal")
        assert got.r80 == raw.r80


def test_the_real_sweep_keeps_every_point_of_its_defocus_curve():
    """The wrapper must not eat the sweep. Each of these is a real frame from
    the 2026-07-31 ground-truth sweep and each has to keep the blob it had."""
    for pos in (4900, 7900, 8900, 9300, 9600, 10200, 10500, 10900, 11900):
        raw = measure_blob(_sweep_frame(pos))
        got = measure_defocus(_sweep_frame(pos))
        assert raw is not None and got is not None, f"{pos} lost its blob"
        assert got.r80 == raw.r80


def test_a_synthetic_field_of_sharp_stars_reports_no_defocus():
    """And the same field defocused reports one, at every radius. The synthetic
    control for the two real cases above, with everything else held equal."""
    assert measure_defocus(_star_field()) is None
    assert measure_defocus(_donut_field(60, 0)) is None, (
        "radius 0 IS a field of sharp stars")
    for radius in (8, 20, 45, 90):
        assert measure_defocus(_donut_field(60, radius)) is not None, (
            f"radius {radius}: coarse focus dies here")


def test_the_wrapper_reuses_the_callers_detection_pass():
    """The hub grades every sub before it asks this, and hands the star list
    over. Passing it must not change the answer -- if it could, the preview and
    a bare call would disagree about the same frame."""
    from astrodeck.imaging.stars import detect_stars
    for name in ("m33field_G60", "donut_L60"):
        data = _ngc604(name)
        stars = detect_stars(data)
        a, b = measure_defocus(data), measure_defocus(data, stars=stars)
        assert (a is None) == (b is None), name
        if a is not None:
            assert a.r80 == b.r80


def test_coarse_focus_still_calls_the_unconditional_measurement():
    """`focus.coarse` must NOT go through the wrapper: on a frame it cannot
    measure it has to say "nothing bright enough to measure" and stop, not
    "your stars look fine". Read from the source so a future edit that points
    it at `measure_defocus` fails here rather than on the rig at 2am."""
    import inspect

    from astrodeck.focus import coarse
    src = inspect.getsource(coarse)
    assert "measure_defocus" not in src, (
        "coarse focus now screens its own frames for a star population, and a "
        "coarse-focus frame by definition has none")
    assert "measure_blob" in src
