"""The star-size metric, judged on a real focuser sweep and nothing else.

tests/fixtures/focus_sweep is a monotonic sweep over one rich star field
(Caph), 4 s at gain 220 bin 1, captured 2026-07-31 for exactly this purpose.
True focus is 9900, established independently by measuring FULL frames
(9400 -> HFR 4.81, 9900 -> HFR 3.37, 10400 -> HFR 4.92; parabola vertex 9891).

Why the fixture and not a synthetic Gaussian: the metric that failed on the sky
passes every synthetic test you can write. A Gaussian never turns into an
annulus with a central obstruction and four spider vanes, never drops below the
detection threshold as it spreads, and never runs out of photons. All three of
those are what actually happened, and only the real frames contain them.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging.stars import (
    SIZE_CONFIDENT_SNR, focus_size, size_advice, star_size,
)

FIXTURES = Path(__file__).parent / "fixtures" / "focus_sweep"
TRUE_FOCUS = 9900

#: The band the manifest calls usable at bin 1 — and the band an autofocus
#: sweep actually samples (+/-1000 steps of a 350-step sweep).
BAND = (8900, 9300, 9600, 9900, 10200, 10500, 10900)

#: Noise tolerance on "increases away from the minimum". The measured rise is
#: ~0.078 px per focuser step, so between two adjacent band points (300-400
#: steps) the metric gains 23-31 px; a 2 px allowance is under a tenth of the
#: smallest real step and still far above the scatter seen re-running the same
#: frame (which is zero — the measurement is deterministic). It exists so a
#: future refactor that costs a pixel of accuracy fails on ACCURACY rather than
#: on an exact-value comparison nobody can maintain.
RISE_TOLERANCE_PX = 2.0


def _manifest() -> dict:
    return json.loads((FIXTURES / "manifest.json").read_text())


def _entry(position: int) -> dict:
    for e in _manifest()["entries"]:
        if e["focuser_position"] == position:
            return e
    raise AssertionError(f"no fixture at {position}")


def _frame(position: int) -> np.ndarray:
    return np.load(FIXTURES / _entry(position)["file"])["data"]


def _curve() -> dict[int, float]:
    out = {}
    for pos in BAND:
        size = star_size(_frame(pos))
        assert size is not None, f"no measurable source at {pos}"
        out[pos] = size.radius
    return out


# --------------------------------------------------------- the acceptance

def test_the_curve_has_one_minimum_and_it_is_true_focus():
    curve = _curve()
    lowest = min(curve, key=curve.get)
    assert lowest == TRUE_FOCUS, f"minimum at {lowest}, not {TRUE_FOCUS}: {curve}"
    assert sum(1 for p in BAND
               if all(curve[p] <= curve[q] for q in _neighbours(p))) == 1, \
        f"more than one local minimum, so a fitter can land on the wrong one: {curve}"


def test_the_v_vertex_lands_within_300_steps_of_true_focus():
    """What autofocus actually does with the curve. Two straight lines rather
    than a parabola because a defocused star's diameter grows LINEARLY with
    distance from focus — the fixture's own extents say so — and a parabola
    through a V puts the vertex right but its VALUE nowhere near."""
    curve = _curve()
    left = [(p, curve[p]) for p in BAND if p < TRUE_FOCUS]
    right = [(p, curve[p]) for p in BAND if p > TRUE_FOCUS]
    ml, bl = np.polyfit([p for p, _ in left], [v for _, v in left], 1)
    mr, br = np.polyfit([p for p, _ in right], [v for _, v in right], 1)
    assert ml < 0 < mr, f"the two arms do not point at each other: {ml}, {mr}"
    vertex = (br - bl) / (ml - mr)
    assert abs(vertex - TRUE_FOCUS) <= 300, \
        f"V vertex {vertex:.0f}, true focus {TRUE_FOCUS}: {curve}"


def test_the_metric_rises_on_both_sides_of_focus():
    """The property the 15px box did not have. It ran BACKWARDS off-focus —
    9900: HFR 4.44 over 911 stars, 10400: HFR 2.13 over 3 — which does not just
    fail to find focus, it aims the search away from it."""
    curve = _curve()
    below = [p for p in BAND if p <= TRUE_FOCUS]
    above = [p for p in BAND if p >= TRUE_FOCUS]
    for seq in (list(reversed(below)), above):
        for a, b in zip(seq, seq[1:]):
            assert curve[b] > curve[a] - RISE_TOLERANCE_PX, (
                f"{a}->{b} did not grow away from focus: "
                f"{curve[a]:.2f} -> {curve[b]:.2f}")


def test_at_focus_it_agrees_with_the_hfr_the_full_frames_measured():
    """Same estimator, same units, so every threshold hung off HFR keeps its
    meaning. The full frames put focus at HFR 3.37-4.81 either side of 9900."""
    size = star_size(_frame(TRUE_FOCUS))
    assert size is not None
    assert 2.0 < size.radius < 7.0, f"{size.radius} is not a focused star"


def test_a_thousand_steps_out_it_reports_tens_of_pixels_not_four():
    """The number the old metric could not produce at all: its box put a hard
    ceiling near 5 px, so 'miles out' and 'in focus' printed the same value."""
    for pos in (8900, 10900):
        size = star_size(_frame(pos))
        assert size is not None and size.radius > 40.0, \
            f"{pos} measured {size.radius if size else None}"


# ----------------------------------------------- where the signal runs out

@pytest.mark.parametrize("position", [5900, 6900, 12900, 13900, 14900])
def test_frames_with_no_recorded_source_measure_nothing_and_say_so(position):
    """Beyond ~1500 steps a 4 s exposure did not record the star AT ALL: Caph's
    ~1.8M ADU spread over a 300px-radius annulus is ~6 ADU per pixel above
    background, which loses to a hot pixel. The manifest flags these frames
    usable_for_size_metric=false for that reason.

    The honest answer is None. A number here would be a hot pixel's opinion
    entering the fit with the same weight as a field full of stars, which is
    precisely the failure the whole night turned on."""
    assert _entry(position)["usable_for_size_metric"] is False
    assert star_size(_frame(position)) is None
    assert focus_size(_frame(position)) == (None, 0)


def test_the_advice_names_the_exposure_rather_than_blaming_the_sky():
    """The caller cannot work out on its own that a wide sweep needs a longer
    exposure than a near-focus one; this module can, because it is the thing
    that saw the empty frame."""
    advice = size_advice(None, exposure_s=4.0)
    assert advice and "16s" in advice and "4s" in advice
    assert "exposure" in advice
    assert size_advice(star_size(_frame(TRUE_FOCUS))) is None


def test_a_source_running_off_the_frame_is_flagged_as_a_floor():
    """4900 is 5000 steps out and its blob does not fit in the crop. 'At least
    this big' is still the right answer to 'which way is focus'; presenting it
    as a measurement is what would let a caller extrapolate a distance from it."""
    size = star_size(_frame(4900))
    assert size is not None and size.lower_bound
    advice = size_advice(size)
    assert advice and "floor" in advice


# ------------------------------------------------------ the sweep contract

def test_focus_size_is_a_drop_in_for_median_hfr_and_answers_at_every_point():
    """Every band point must come back with a number. Under the old detection
    these are the points that were DROPPED for having too few stars, and a
    sweep that drops its wings has no spread left to fit."""
    for pos in BAND:
        value, n = focus_size(_frame(pos), 3)
        assert value is not None, f"{pos} dropped: only {n} sources"
        assert n >= 1


def test_one_bright_donut_is_admitted_where_three_faint_stars_would_be():
    """A 1000-step-out crop holds ONE measurable donut, and it is a better focus
    point than three threshold-grazing detections. The min_stars gate exists to
    keep a hot pixel out of the fit, and the resolved-source test now does that
    job directly — so a single source of this significance stands on its own."""
    size = star_size(_frame(10900))
    assert size is not None
    assert size.n_sources < 3, "fixture changed; this test no longer tests it"
    assert size.snr > SIZE_CONFIDENT_SNR
    assert focus_size(_frame(10900), 3)[0] is not None


def test_pure_noise_invents_no_source():
    """A phantom source is worse than none: it puts a fabricated size on the
    curve at a position where the truth was 'nothing was recorded here'."""
    rng = np.random.default_rng(19)
    for _ in range(4):
        assert star_size(rng.normal(500, 20, (1200, 1600))) is None


def test_a_field_of_stars_measures_the_stars_not_the_field():
    """measure_blob's old failure, checked on the star metric too: with 200
    sources in frame the answer is one star's size, not the spread between
    them."""
    rng = np.random.default_rng(23)
    img = rng.normal(500, 20, (1200, 1600)).astype(np.float32)
    yy, xx = np.mgrid[-8:9, -8:9]
    psf = np.exp(-(yy ** 2 + xx ** 2) / (2 * 2.0 ** 2))
    for _ in range(200):
        y, x = int(rng.integers(20, 1180)), int(rng.integers(20, 1580))
        img[y - 8:y + 9, x - 8:x + 9] += (rng.uniform(2000, 40000) * psf).astype(np.float32)
    size = star_size(img)
    assert size is not None
    assert abs(size.radius - 1.253 * 2.0) < 1.0, (
        f"{size.radius} is not a sigma=2 star (expect {1.253 * 2.0:.2f})")


def test_the_measurement_scales_with_the_star_over_a_decade():
    """Flux-weighted mean radius of a Gaussian is 1.253*sigma, and it has to
    stay that from a sharp star to a badly soft one — a metric that is only
    linear near focus gives the fitter a curve with the wrong shape."""
    rng = np.random.default_rng(31)
    for sigma in (1.5, 4.0, 12.0):
        img = rng.normal(500, 20, (1200, 1600)).astype(np.float32)
        s = int(max(8, sigma * 5))
        yy, xx = np.mgrid[-s:s + 1, -s:s + 1]
        psf = np.exp(-(yy ** 2 + xx ** 2) / (2 * sigma ** 2))
        for _ in range(30):
            y = int(rng.integers(s + 2, 1198 - s))
            x = int(rng.integers(s + 2, 1598 - s))
            img[y - s:y + s + 1, x - s:x + s + 1] += \
                (rng.uniform(20000, 200000) * psf / (2 * math.pi * sigma ** 2)).astype(np.float32)
        size = star_size(img)
        assert size is not None
        assert abs(size.radius - 1.253 * sigma) < 0.25 * sigma, \
            f"sigma={sigma}: measured {size.radius:.2f}, expect {1.253 * sigma:.2f}"


def _neighbours(pos: int) -> list[int]:
    i = BAND.index(pos)
    return [BAND[j] for j in (i - 1, i + 1) if 0 <= j < len(BAND)]
