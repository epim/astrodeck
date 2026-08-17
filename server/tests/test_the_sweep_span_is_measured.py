"""How wide a sweep should be is a measurement, and here is the arithmetic.

THE CONSTANT THIS REPLACES. `run_autofocus` swept `steps_each_side=4` points of
`step=350` — ±1400 focuser steps — from the day it was written. Nothing ever
checked that against a rig. #219 closed on 2026-08-17 with the item left open in
so many words:

    the sweep still spans +/-1400 when +/-350 measured beautifully. Narrowing it
    wants the focuser's critical focus zone, which has never been measured.

It cannot be COMPUTED: `Optics` carries a focal length and no aperture, so there
is no f-ratio and so no CFZ formula, which is exactly why the 2026-07-23 one-tap
spec declined to make a CFZ claim ("we have no step-per-micron"). It can be
MEASURED, and a successful sweep measures it for free — see `focus/span.py`.

The numbers below are the rig's own, from the 2026-08-17 NGC 7129 sweep, and
they are what makes this checkable rather than plausible.
"""
import math

import pytest

from astrodeck.focus.autofocus import WING_RISE_FRAC
from astrodeck.focus.native import FLAT_TIP_BAND
from astrodeck.focus.span import (DEFAULT_STEP, MIN_SLOPE_POINTS,
                                  OUTER_SIZE_FACTOR, STEP_MAX, STEP_MIN,
                                  FocusCalibration, defocus_slope,
                                  half_span_steps, sweep_geometry)

#: The rig, 2026-08-17: 2.96 px at focus (from 1172 stars) and a slope of about
#: 0.078 px/step, which three independent points of that sweep agree on to 5%
#: once they are inverted through the hyperbola.
RIG_FOCUS_PX = 2.96
RIG_SLOPE = 0.078
RIG_BEST = 11135


def _hyperbola(best, y0, slope, offsets):
    """A V-curve as `size(d) = sqrt(y0^2 + (m*d)^2)` — the model `defocus_slope`
    inverts, so a curve built from it is the fixture that proves the inversion."""
    return [(best + d, math.sqrt(y0 ** 2 + (slope * d) ** 2)) for d in offsets]


SYMMETRIC = _hyperbola(RIG_BEST, RIG_FOCUS_PX, RIG_SLOPE,
                       (-1400, -1050, -700, -350, 0, 350, 700, 1050, 1400))


# --------------------------------------------------------------- the slope

def test_the_slope_comes_back_off_a_curve_built_from_it():
    """The round trip. Nothing else here means anything if this does not hold."""
    m = defocus_slope(SYMMETRIC, RIG_BEST, RIG_FOCUS_PX)
    assert m is not None
    assert abs(m - RIG_SLOPE) < 1e-6, m


def test_the_rigs_own_wings_agree_with_each_other():
    """Not synthetic: the three points of the 2026-08-17 sweep that the fixed
    detector measured honestly, inverted through the hyperbola. They agree to
    5%, which is the evidence that the model describes THIS rig and not just
    the textbook."""
    for offset, size in ((350, 27.38), (-350, 27.06), (1050, 84.89)):
        m = math.sqrt(size ** 2 - RIG_FOCUS_PX ** 2) / abs(offset)
        assert 0.074 < m < 0.082, (offset, size, m)


def test_a_sweep_that_never_bracketed_teaches_nothing():
    """One arm only. This is what a sweep whose focus lay outside the window
    produces, and a slope measured off it would then be used to make the NEXT
    sweep narrower — chasing focus further away each run."""
    one_armed = [(RIG_BEST + d, math.sqrt(RIG_FOCUS_PX ** 2 + (RIG_SLOPE * d) ** 2))
                 for d in (350, 700, 1050, 1400)]
    assert defocus_slope(one_armed, RIG_BEST, RIG_FOCUS_PX) is None


def test_a_couple_of_salvaged_points_are_not_a_calibration():
    """`curve_verdict` can rescue a run from five points, and it should. That
    does not make those five points a measurement of the optics."""
    thin = _hyperbola(RIG_BEST, RIG_FOCUS_PX, RIG_SLOPE, (-350, 0, 350))
    assert defocus_slope(thin, RIG_BEST, RIG_FOCUS_PX) is None
    assert MIN_SLOPE_POINTS >= 4


def test_the_flat_tip_does_not_get_a_vote():
    """Near focus the hyperbola is flat, so `sqrt(size^2 - y0^2)` there is the
    difference of two nearly equal numbers divided by a small distance — all
    noise. A curve sampled ONLY near the tip must therefore yield nothing,
    rather than a number made of rounding."""
    tip_only = [(RIG_BEST + d, RIG_FOCUS_PX * 1.001) for d in (-40, -20, 20, 40)]
    assert defocus_slope(tip_only, RIG_BEST, RIG_FOCUS_PX) is None


def test_one_wild_point_does_not_carry_the_answer():
    """A median, not a fit. The outermost point of a real sweep is the one most
    likely to be wrong (it is where the stars stop being countable) and it is
    also the one a least-squares line would hang off hardest."""
    poisoned = list(SYMMETRIC)
    poisoned[0] = (poisoned[0][0], 400.0)      # the far wing reads 5x too big
    m = defocus_slope(poisoned, RIG_BEST, RIG_FOCUS_PX)
    assert m is not None
    assert abs(m - RIG_SLOPE) < 0.01, m


# ------------------------------------------------------------- the geometry

def test_the_span_lands_where_the_rig_said_it_should():
    """±350 measured beautifully on this rig; the derivation, given only what
    that sweep measured, arrives at ±300 without being told."""
    half = half_span_steps(RIG_SLOPE, RIG_FOCUS_PX)
    assert 280 < half < 320, half
    cal = FocusCalibration(RIG_SLOPE, RIG_FOCUS_PX, 1, 9, RIG_BEST, 1400,
                           "2026-08-17")
    g = sweep_geometry(cal, steps_each_side=4)
    assert g.step == 75, g.step
    assert g.measured
    assert 4 * g.step * 2 < DEFAULT_STEP * 4 * 2 / 4, "not the promised narrowing"


def test_it_never_asks_for_more_than_a_sweep_that_worked():
    """THE RATCHET GUARD, and it is not hypothetical: measured on the simulator
    2026-08-17, whose gentle synthetic defocus reads 0.0033 px/step and asks for
    ±5712 off a ±1400 sweep.

    A shallow slope legitimately wants a wider sweep — a slow train defocuses
    gently. But an UNDER-MEASURED wing produces a shallow slope too, and then
    the next sweep is wider, its wings further out, its measurement worse, its
    slope shallower still. Capping at the range the measuring sweep covered
    stops that, and is also the honest reading of a slope measured only over
    the range it sampled."""
    gentle = FocusCalibration(0.0033, 2.35, 1, 10, RIG_BEST, 1400, "2026-08-17")
    g = sweep_geometry(gentle, steps_each_side=4, focuser_max=60000)
    assert g.step * 4 <= 1400, g.step
    assert "actually covered" in g.basis, g.basis
    # An older record with no range on it imposes no ceiling — it cannot, and
    # inventing one would silently narrow a rig that never asked for it.
    legacy = FocusCalibration(0.0033, 2.35, 1, 10, RIG_BEST, 0.0, "")
    assert sweep_geometry(legacy, steps_each_side=4).step * 4 > 1400


def test_the_innermost_point_clears_the_engines_flat_tip_band():
    """The constraint that stops OUTER_SIZE_FACTOR being lowered further. A
    point within `FLAT_TIP_BAND` of the minimum joins NEITHER trendline
    (native/crates/astro-focus/src/trendline.rs), and a sweep whose inner points
    all land in that band fails `not_enough_spread` — which is precisely the
    failure a too-narrow span produces."""
    cal = FocusCalibration(RIG_SLOPE, RIG_FOCUS_PX, 1, 9, RIG_BEST, 1400, "")
    step = sweep_geometry(cal, steps_each_side=4).step
    inner = math.sqrt(RIG_FOCUS_PX ** 2 + (RIG_SLOPE * step) ** 2)
    assert inner - RIG_FOCUS_PX > 10 * FLAT_TIP_BAND, inner


def test_the_outer_point_clears_the_curve_verdicts_wing_bar():
    """The other constraint. `curve_verdict` refuses a curve whose wings do not
    rise WING_RISE_FRAC above the minimum, so a span sized by this rule has to
    clear that bar on its own arithmetic — not by luck on one rig."""
    cal = FocusCalibration(RIG_SLOPE, RIG_FOCUS_PX, 1, 9, RIG_BEST, 1400, "")
    step = sweep_geometry(cal, steps_each_side=4).step
    outer = math.sqrt(RIG_FOCUS_PX ** 2 + (RIG_SLOPE * step * 4) ** 2)
    assert (outer - RIG_FOCUS_PX) / RIG_FOCUS_PX > WING_RISE_FRAC * 2, outer
    assert outer / RIG_FOCUS_PX == pytest.approx(OUTER_SIZE_FACTOR, rel=0.02)


def test_binning_does_not_change_the_answer():
    """`y0` and the slope both scale as 1/bin, so their ratio — which is the
    whole of the half-span — does not. Worth pinning rather than reasoning
    about: the sweep's binning is chosen per run (bin 1 on this rig, bin 2 by
    default), and a span that silently halved with it would make every other
    night's calibration wrong."""
    at_bin2 = [(p, h / 2) for p, h in SYMMETRIC]
    m1 = defocus_slope(SYMMETRIC, RIG_BEST, RIG_FOCUS_PX)
    m2 = defocus_slope(at_bin2, RIG_BEST, RIG_FOCUS_PX / 2)
    assert half_span_steps(m1, RIG_FOCUS_PX) == pytest.approx(
        half_span_steps(m2, RIG_FOCUS_PX / 2), rel=1e-9)


def test_nothing_measured_means_the_shipped_default():
    """The only honest answer before a sweep has completed — and it is the
    geometry every successful focus run in this project's history used, so it
    is not a placeholder either."""
    g = sweep_geometry(None, steps_each_side=4)
    assert g.step == DEFAULT_STEP
    assert not g.measured
    assert "no completed sweep" in g.basis


def test_the_basis_says_what_it_was_measured_from():
    """A sweep that changes its own width between runs and logs only the number
    leaves 'why did it sweep ±300 tonight' unanswerable from the morning's log.
    The number and the reason are produced together on purpose."""
    cal = FocusCalibration(RIG_SLOPE, RIG_FOCUS_PX, 2, 11, RIG_BEST, 1400, "2026-08-17")
    basis = sweep_geometry(cal, steps_each_side=4).basis
    for fragment in ("0.0780 px/step", "2026-08-17", "11 points", "bin 2",
                     "2.96 px"):
        assert fragment in basis, (fragment, basis)


@pytest.mark.parametrize("slope,expect", [(50.0, STEP_MIN), (1e-5, STEP_MAX)])
def test_an_absurd_slope_is_clamped_rather_than_believed(slope, expect):
    """A slope 600x the rig's would ask for a 1-step sweep; one 8000x smaller
    would ask for a 2-million-step one. Neither is a sweep."""
    cal = FocusCalibration(slope, RIG_FOCUS_PX, 1, 9, RIG_BEST, 0.0, "")
    g = sweep_geometry(cal, steps_each_side=4)
    assert g.step == expect
    assert "floor" in g.basis or "ceiling" in g.basis


def test_the_sweep_cannot_be_wider_than_the_focuser():
    """A short-travel focuser is a hard wall, not a preference."""
    cal = FocusCalibration(0.001, RIG_FOCUS_PX, 1, 9, RIG_BEST, 0.0, "")
    g = sweep_geometry(cal, steps_each_side=4, focuser_max=4000)
    assert g.step * 8 <= 4000, g.step
    assert "travel" in g.basis


# ------------------------------------------------------------ the stored form

@pytest.mark.parametrize("raw", [
    None, {}, "nonsense", {"slope_px_per_step": 0.0, "in_focus_px": 3.0},
    {"slope_px_per_step": -1.0, "in_focus_px": 3.0},
    {"slope_px_per_step": 0.07, "in_focus_px": 0.0},
    {"slope_px_per_step": "wide", "in_focus_px": 3.0},
    {"in_focus_px": 3.0},
])
def test_a_broken_record_is_no_record(raw):
    """This reads a file on disk that a user can edit and an older version can
    have written. A half-parsed calibration would size a sweep off a fragment,
    so anything short of complete-and-usable reads as 'never measured'."""
    assert FocusCalibration.from_json(raw) is None


def test_a_good_record_survives_the_round_trip():
    cal = FocusCalibration(RIG_SLOPE, RIG_FOCUS_PX, 1, 9, RIG_BEST, 1400, "2026-08-17")
    assert FocusCalibration.from_json(cal.to_json()) == cal
