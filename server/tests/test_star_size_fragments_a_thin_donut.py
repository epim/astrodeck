"""A defocused star is a ring, and the detector must measure the ring (#219).

THE BUG THIS FILE WAS WRITTEN FOR, and now guards against coming back.

#219 was filed as "autofocus only works on a bright field", diagnosed through a
hardcoded 2 s bin-2 frame (#233), and re-measured on 2026-08-17 at bin 1 on a
field with ELEVEN HUNDRED stars at focus - where it failed anyway. Outward from
focus at 11135 the rig measured:

    +/-350 steps   27.06 and 27.38 px   from 49 and 32 stars    good, symmetric
    +/-700 steps   10.40 and 14.12 px   from 13 and  8 stars    backwards
    -1050 steps     5.52 px             from 10 stars           backwards

Sizes that FALL as defocus grows are impossible, and they are why `curve_verdict`
refused the curve: "the left wing falls back 16.66px ... an arm that turns round
is not one arm of a V".

ROOT CAUSE. `star_size` walks pyramid levels coarse->fine, letting a coarse
detection claim a circle so finer levels cannot re-detect its parts. A coarse
seed was then MEASURED at full resolution with an aperture of 8*k px - and that
measurement, on a thin ring, locks onto an arc rather than the ring. The rim
comes back as several small sources and the arcs become the answer.

THE FIX measures each seed at the resolution it was FOUND at, on the binned copy
that seeding already built and cached. `_measure_at_bin` existed already for the
escalation path. It is both more correct AND cheaper: the full-resolution
version of the same correctness cost 23x on a real 3126x2088 frame (235 -> 5362
ms, eleven times a sweep, on a Pi), while measuring at bin k is ~k^2 less and
lands at 239 ms - inside the noise of the 235 ms baseline.

`SIZE_BIN_TRUST` is the other half. At bin k a radius cannot come back smaller
than about one binned pixel, so a 6 px star found at k=64 reported 32 px - the
size of the BIN. Such a seed is deferred to a finer level instead of claimed.

A THIRD change was built and then removed: screening the seed on the binned copy
too, on the theory that a 3 px hot-pixel probe lands in the ring's dark hole
(it does - five k=16 seeds test False at R=3 and True at R=8). Sabotaging it
back to full resolution left every test here green, so it was decoration and
went. Measuring at the right level is what fixes this.

Hocus Focus (George Hilios) found the same class independently - its 4.0.0.5
note restores tighter Brightness Sensitivity because looser values "admitted
noise/donut fragments that could skew autofocus on wide/defocused sweeps".
"""
import numpy as np
import pytest

from astrodeck.imaging.stars import SIZE_BIN_TRUST, star_size


def _bg(h, w, level=700.0, noise=8.0, seed=5):
    return np.random.default_rng(seed).normal(level, noise, (h, w))


def _donut(img, cy, cx, r, peak, thickness):
    """An annulus - what a defocused star is, as opposed to a blob."""
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    img += peak * np.exp(-((d - r) ** 2) / (2 * thickness ** 2))
    return img


def _field(r, rim_fraction, size=None, peak=9000.0):
    """Nine donuts of radius ``r``, on a frame that scales with them.

    Frame size is not incidental: `_size_levels` derives the pyramid from the
    shape, so a fixed small frame both removes the coarse levels a big ring
    needs AND crowds the rings together. On the rig a 48 px donut sits in a
    3126 px frame - a 65:1 ratio. Nine of them in a 768 px frame is 16:1, which
    fails for reasons that have nothing to do with the detector's logic.
    """
    size = size or max(768, 32 * r)
    img = _bg(size, size)
    rng = np.random.default_rng(6)
    step = size // 4
    for iy in range(1, 4):
        for ix in range(1, 4):
            _donut(img, iy * step + rng.integers(-6, 6),
                   ix * step + rng.integers(-6, 6), r, peak,
                   max(0.8, r * rim_fraction))
    return img


@pytest.mark.parametrize("r", [6, 10, 16, 24, 34, 48])
@pytest.mark.parametrize("rim", [0.35, 0.12])
def test_a_donut_is_measured_at_its_true_size(r, rim):
    """THE REGRESSION GUARD. A thin rim is what broke it: the ring fragments and
    each arc gets measured instead. Thick rims never failed and are here so a
    future fix cannot trade one for the other."""
    size = star_size(_field(r, rim))
    assert size is not None, f"nine {r}px donuts and nothing detected"
    assert 0.55 * r < size.radius < 1.9 * r, (
        f"a {r}px donut with a {rim} rim measured {size.radius:.2f}px")


@pytest.mark.parametrize("r", [16, 24, 34])
def test_one_ring_is_one_source(r):
    """The fingerprint of the bug was the COUNT going up: nine donuts arriving
    as 19 then 24 "sources" as the rings broke into arcs. Nine in, nine out."""
    size = star_size(_field(r, 0.12))
    assert size is not None
    assert size.n_sources <= 12, (
        f"{size.n_sources} sources from nine donuts - the rings are fragmenting "
        "again")


def test_the_answer_tracks_the_true_radius():
    """The tell that it is measuring rings rather than fragments: the answer
    depends on the thing being measured. Before the fix a 24px and a 34px ring
    both came back at ~7.7px."""
    a = star_size(_field(24, 0.12))
    b = star_size(_field(34, 0.12))
    assert a is not None and b is not None
    assert b.radius - a.radius > 6.0, (
        f"24px -> {a.radius:.2f}px, 34px -> {b.radius:.2f}px; a measurement that "
        "barely moves when the object grows 40% is not measuring the object")


def test_a_tight_star_is_not_reported_at_the_bin_size():
    """The trap the fix had to avoid on the way. Measuring a seed at bin k puts
    a floor of about one binned pixel under the answer, so a 6 px star found at
    k=64 came back as 32 px - the size of the BIN. `SIZE_BIN_TRUST` defers such
    a seed to a finer level instead of claiming it."""
    size = star_size(_field(6, 0.35, size=1536))
    assert size is not None
    assert size.radius < 12, (
        f"a 6px source measured {size.radius:.2f}px - that is a bin, not a star")
    assert SIZE_BIN_TRUST >= 1.0


def test_saturation_is_not_involved():
    """Last night's dropped frames read 'max 44476' of 65535, close enough to
    full scale to suspect clipping. It was never the cause."""
    clean = star_size(_field(24, 0.12))
    clipped = star_size(np.clip(_field(24, 0.12), 0, 44000))
    assert clean is not None and clipped is not None
    assert abs(clean.radius - clipped.radius) < 0.01


def test_the_wing_shape_is_now_physical():
    """End to end, in the terms the sweep cares about: as defocus grows the
    measured size must GROW. This ordering is exactly what the rig violated
    (27.06 then 10.40 then 5.52 going outward) and what made the fitter refuse
    'an arm that turns round'."""
    sizes = []
    for r in (6, 12, 20, 30, 42):
        s = star_size(_field(r, 0.12, size=1536))
        assert s is not None, f"lost the ring at r={r}"
        sizes.append(s.radius)
    assert sizes == sorted(sizes), f"sizes did not grow with defocus: {sizes}"
