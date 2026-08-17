"""A thin donut breaks into arcs, and the detector measures the arcs (#219).

THE MISSING REPRODUCTION. #219 was filed as "autofocus only works on a bright
field", diagnosed through a hardcoded 2 s bin-2 frame (#233), and re-measured on
2026-08-17 at bin 1 on a field with ELEVEN HUNDRED stars at focus - where it
failed anyway. What was never available was a way to reproduce the wing failure
without waiting for a clear night, which is why the wrong diagnosis survived so
long.

WHAT THE RIG MEASURED, outward from focus at 11135:

    +/-350 steps   27.06 and 27.38 px   from 49 and 32 stars    good, symmetric
    +/-700 steps   10.40 and 14.12 px   from 13 and  8 stars    backwards
    -1050 steps     5.52 px             from 10 stars           backwards

Sizes that FALL as defocus grows are physically impossible, and they are what
made `curve_verdict` refuse the curve: "the left wing falls back 16.66px ... an
arm that turns round is not one arm of a V".

WHAT THESE TESTS SHOW. Synthesise the annulus a defocused star actually is and
sweep its radius. A THICK rim tracks perfectly - 26.71 px measured for 24 px
true, nine donuts counted as nine sources. Thin the rim to something a real
optic produces at large defocus and the ring breaks into arcs: the source count
RISES (9 donuts -> 19, then 24 "sources"), and the measured size collapses and
PLATEAUS around 7.7 px whether the true radius is 24 or 34. It is measuring
fragments.

Hocus Focus (George Hilios) names this exactly - its 4.0.0.5 note restores
tighter Brightness Sensitivity because looser values "admitted noise/donut
fragments that could skew autofocus on wide/defocused sweeps". Same failure,
independently found, which is good evidence the reproduction is real and not an
artefact of this synthesis.

WHY NOTHING IS FIXED HERE. The obvious fix is in the detector, and
`detect_stars` also feeds the preview HFR readout, the Bahtinov aid and the
CLOUD DETECTOR - which as of 2026-08-17 stands in for a missing safety monitor
and can hold a night. Retuning detection to chase a focus bug could hold good
nights on a false cloudy verdict. That trade deserves its own change with its
own evidence, and this file is the evidence it will need.

These tests assert TODAY'S behaviour. When the detector is fixed they will fail,
and the failure is the signal to update them - not a regression.
"""
import numpy as np
import pytest

from astrodeck.imaging.stars import star_size


def _bg(h=384, w=384, level=700.0, noise=8.0, seed=5):
    return np.random.default_rng(seed).normal(level, noise, (h, w))


def _donut(img, cy, cx, r, peak, thickness):
    """An annulus - what a defocused star looks like, as opposed to a blob."""
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    img += peak * np.exp(-((d - r) ** 2) / (2 * thickness ** 2))
    return img


def _field(r, rim_fraction, peak=9000.0, seed=5):
    """Nine donuts of radius ``r`` on a noisy background."""
    img = _bg(seed=seed)
    rng = np.random.default_rng(6)
    step = img.shape[0] // 4
    for iy in range(1, 4):
        for ix in range(1, 4):
            _donut(img, iy * step + rng.integers(-6, 6),
                   ix * step + rng.integers(-6, 6), r, peak,
                   max(0.8, r * rim_fraction))
    return img


@pytest.mark.parametrize("r", [6, 10, 16, 24])
def test_a_thick_rimmed_donut_is_measured_correctly(r):
    """The control. Nothing about donuts defeats the detector by itself."""
    size = star_size(_field(r, 0.35))
    assert size is not None
    assert 0.8 * r < size.radius < 1.6 * r, (
        f"a {r}px donut measured {size.radius:.2f}px")


@pytest.mark.parametrize("r", [24, 34])
def test_a_thin_rimmed_donut_fragments_and_under_measures(r):
    """THE BUG, reproduced. A thin ring breaks into arcs; the arcs are counted
    as separate sources and their size is reported as the star size."""
    size = star_size(_field(r, 0.12))
    assert size is not None, "the field is not empty - it has nine donuts in it"
    assert size.radius < 0.5 * r, (
        f"a {r}px donut measured {size.radius:.2f}px - if this now tracks the "
        "true radius the detector has been fixed and this test should be "
        "retired along with the #219 note")
    assert size.n_sources > 9, (
        f"{size.n_sources} sources from nine donuts - the ring fragmenting is "
        "the mechanism, and the count going UP is its fingerprint")


def test_the_under_measurement_plateaus_at_the_fragment_size():
    """The tell that it is measuring fragments rather than mis-measuring rings:
    the answer stops depending on the true radius at all."""
    a = star_size(_field(24, 0.12))
    b = star_size(_field(34, 0.12))
    assert a is not None and b is not None
    assert abs(a.radius - b.radius) < 1.0, (
        f"24px donut -> {a.radius:.2f}px, 34px donut -> {b.radius:.2f}px; a "
        "measurement that ignores a 40% change in the thing measured is not a "
        "measurement of it")


def test_saturation_is_not_the_cause():
    """Last night's dropped frames read 'max 44476' of 65535, close enough to
    full scale to suspect clipping. Ruled out: clipping the synthetic field at
    the same level changes the answer not at all."""
    clean = star_size(_field(24, 0.12))
    clipped = star_size(np.clip(_field(24, 0.12), 0, 44000))
    assert clean is not None and clipped is not None
    assert abs(clean.radius - clipped.radius) < 0.01


def test_the_fragments_survive_the_sub_pixel_floor():
    """MIN_SIZE_PX catches the 0.50px points from the far end of the sweep. It
    does NOT catch these: ~7.7px is a perfectly plausible star size, which is
    precisely why the wing points were fitted and steered the curve into 'an arm
    that turns round'. Two halves of one defect; only one is fixed."""
    from astrodeck.focus.autofocus import MIN_STARS_PER_POINT, _size_point

    size = star_size(_field(34, 0.12))
    value, _ = _size_point(size, MIN_STARS_PER_POINT)
    assert value is not None and value > 1.0
