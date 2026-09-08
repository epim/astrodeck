"""A half-pixel star is a pixel (#219).

THE MEASUREMENT, 2026-08-17 00:23-00:36, NGC 7129, Ha, 24 s at gain 200, bin 1 -
the re-measurement the backlog asked for before any work on #219:

    autofocus: 1108 stars at the starting position
    autofocus: 12185 -> HFR 84.89 (6 stars)
    autofocus: 11835 -> HFR 14.12 (8 stars)
    autofocus: 11485 -> HFR 27.38 (32 stars)
    autofocus: 11135 -> HFR  2.96 (1172 stars)     <- the real focus
    autofocus: 10785 -> HFR 27.06 (49 stars)
    autofocus: 10435 -> HFR 10.40 (13 stars)
    autofocus: 10085 -> HFR  5.52 (10 stars)
    autofocus:  9735 -> HFR  0.50 (5 stars)        <- impossible
    autofocus:  9385 -> HFR  0.51 (4 stars)        <- impossible
    autofocus:  9035 -> HFR  0.52 (12 stars)       <- impossible
    dropping 8685 x3 -> "the search cannot move on"

The planned sweep was "9 points of 350 steps around 11135", i.e. 9735..12535.
It ran PAST the bottom of that range to 9385, 9035, 8685 - because 9735 came
back at 0.50 and looked like a better minimum than the 2.96 it had already
measured. It was chasing a fake minimum out of the range, and it burned
thirteen minutes and the night's initial focus doing it.

This kills the original diagnosis. #219 was filed as "only works on a bright
field"; the field here had ELEVEN HUNDRED stars at focus. What fails is the
opposite end of the sweep, where the star images are so bloated that the
detector finds only a few noise peaks - and reports their size as half a pixel.

A half-flux RADIUS of 0.5 px means the flux is inside a single pixel. That is
not a resolved star image at any binning; it is a measurement of a pixel. It
must never be admitted as a focus measurement, and admitting it is worse than
dropping the point, because a too-small number does not just add noise - it
outranks the true minimum and steers the search away from it.
"""
import pytest

from astrodeck.focus.autofocus import MIN_STARS_PER_POINT, _size_point
from astrodeck.imaging.stars import SourceSize


def _size(radius, n=8, snr=40.0):
    return SourceSize(radius=radius, n_sources=n, n_found=n, snr=snr,
                      scale=1, lower_bound=False)


def test_the_real_focus_point_is_accepted():
    """11135 -> 2.96 px from 1172 stars. Whatever the floor is, it must not
    touch this: it is the answer the sweep threw away."""
    value, n = _size_point(_size(2.96, n=1172), MIN_STARS_PER_POINT)
    assert value == 2.96
    assert n == 1172


@pytest.mark.parametrize("radius", [0.50, 0.51, 0.52])
def test_a_sub_pixel_radius_is_refused(radius):
    """The three points that steered the search off the end of its range. Each
    had MORE than the minimum star count, so the existing star gate could not
    catch them - 5, 4 and 12 stars against a minimum of 3."""
    value, n = _size_point(_size(radius, n=5), MIN_STARS_PER_POINT)
    assert value is None, (
        f"a half-flux radius of {radius} px means the flux fits in ONE pixel; "
        "admitting it lets a noise peak outrank a 1172-star focus")


def test_it_is_refused_even_with_a_confident_snr():
    """`_size_point` admits a thin point when the SNR is high, which exists so a
    genuine donut at the end of a sweep is not dropped. A sub-pixel radius must
    not ride in through that door: high SNR on a single hot pixel is exactly
    what a cosmic ray looks like."""
    value, _ = _size_point(_size(0.5, n=1, snr=1000.0), MIN_STARS_PER_POINT)
    assert value is None


def test_one_pixel_and_above_still_measures():
    """The floor is the sampling limit, not a quality bar. A tight, real,
    well-sampled star at 1.2 px is a legitimate focus point and some rigs live
    there."""
    value, _ = _size_point(_size(1.2, n=40), MIN_STARS_PER_POINT)
    assert value == 1.2


def test_the_defocused_wings_still_measure():
    """The other end must keep working: 84.89 px from 6 stars is a real, useful
    point - it is what brackets the minimum - and #219's earlier fix exists to
    stop those being dropped."""
    value, n = _size_point(_size(84.89, n=6), MIN_STARS_PER_POINT)
    assert value == 84.89
    assert n == 6


def test_focus_size_agrees_with_the_sweep():
    """`_size_point` documents itself as mirroring `imaging.stars.focus_size`
    "exactly ... so the accept/refuse rule cannot drift". Two copies of a rule
    is how a fix lands on one and not the other."""
    import numpy as np

    from astrodeck.imaging import stars as stars_mod

    calls = {"n": 0}

    def fake_star_size(_data):
        calls["n"] += 1
        return _size(0.5, n=5)

    orig = stars_mod.star_size
    stars_mod.star_size = fake_star_size
    try:
        value, _n, _detail = stars_mod.focus_size(
            np.zeros((4, 4), dtype=np.uint16))
    finally:
        stars_mod.star_size = orig
    assert calls["n"] == 1
    assert value is None, "focus_size admitted what the sweep refuses"


# --------------------------------------------------------------- the outcome
# The floor turns three "measurements" into three drops. It stops the walk - the
# sweep can no longer chase a fake minimum out of its own range. It does NOT
# rescue this particular run, and pretending otherwise would be the comfortable
# lie. These tests pin what actually happens, both before and after, so the
# remaining defect stays visible instead of being absorbed into a green run.


def test_the_floor_changes_WHY_the_run_fails():
    """Before: the minimum is a noise peak at 0.50 and the wings "rise" 0.02px
    above it, so the curve is refused as too shallow - after the sweep has
    already walked 2450 steps past its range to collect those points.

    After: the sub-pixel points never enter, and the curve is refused for
    something true about the optics instead. Different failure, and only one of
    them costs thirteen minutes.
    """
    from astrodeck.focus.autofocus import curve_verdict

    kept = [(12185, 84.89, 6), (11835, 14.12, 8), (11485, 27.38, 32),
            (11135, 2.96, 1172), (10785, 27.06, 49), (10435, 10.40, 13),
            (10085, 5.52, 10)]
    noisy = kept + [(9735, 0.50, 5), (9385, 0.51, 4), (9035, 0.52, 12)]

    def judge(rows):
        return curve_verdict([(p, h) for p, h, _ in rows],
                             counts=[n for _, _, n in rows])

    before, after = judge(noisy), judge(kept)
    assert not before.accepted and not after.accepted
    assert "too shallow" in before.reason, before.reason
    assert "0.50px minimum" in before.reason, (
        "the noise peak should be what the fitter calls the minimum")
    # It used to be refused for "an arm that turns round". Since 2026-08-18 the
    # judge PEELS a star-starved end that turns back (STARVED_END_FRAC) -- the
    # decision `test_the_wings_under_measure_and_that_is_the_next_defect` calls
    # for, made for END points only. Here that removes 5.52/10 stars and
    # 10.40/13 stars, and what is left is refused for the truer reason: this
    # curve's own scatter (30.43px, driven by the 14.12 sitting between 27.38
    # and 84.89) is larger than the 24.10px the minimum is deep. Still refused,
    # which is the property that matters; the sweep still over-ran its range.
    assert "scatter" in after.reason, after.reason
    assert after.depth is not None and after.roughness is not None
    assert after.depth < after.roughness, (
        f"depth {after.depth} vs roughness {after.roughness} — if this curve "
        "ever becomes clean enough to accept, the sweep span is the thing that "
        "changed and this test should be re-derived")


def test_the_wings_under_measure_and_that_is_the_next_defect():
    """WHY the surviving curve is still refused, stated as data rather than as
    a note somebody has to remember.

    At +/-350 steps the sweep measured 27.06 and 27.38 from 49 and 32 stars -
    excellent, and near-symmetric. Further out, where only 6-13 stars survive,
    it measured 10.40 and 5.52 and 14.12: SMALLER than the points closer to
    focus, which is physically backwards. The arm turns round, and that is what
    the verdict is objecting to.

    Same disease as the sub-pixel points, one notch less obvious: the detector's
    measured size degrades as the star count collapses, and MIN_STARS_PER_POINT
    of 3 is far too weak a guard when the in-focus frame yielded 1172. Fixing it
    means deciding what a thin point is WORTH, which is a trade (bloated wings
    legitimately have few stars) and not a mechanical change.
    """
    outward = [(10785, 27.06, 49), (10435, 10.40, 13), (10085, 5.52, 10)]
    sizes = [h for _, h, _ in outward]
    assert sizes == sorted(sizes, reverse=True), (
        "if this ever becomes monotonic the wing defect is fixed and this test "
        "should be retired along with the note in the backlog")
    assert all(n < 50 for _, _, n in outward[1:]), (
        "the under-measured points are the thin ones - that correlation is the "
        "whole lead")
