"""Which V-curves locate focus, judged on shape rather than on R² (#143).

The rig, 2026-08-08, Oiii slot of a per-filter offset run: HFR 1.67 px at
position 11173 from 321 stars, rising to 44.70 px at BOTH ends of the swept
range — and REFUSED on R² 0.743 against a 0.70 gate. Autofocus succeeds about
one run in thirteen on this rig, and this is the shape of most of the losses.

``curve_verdict`` is a pure function for the reason ``is_flat_sweep`` is one:
the decision lives inside a Rust state machine driven by a camera and a
focuser, so the end-to-end path can only be exercised where a rig or a wheel
exists — and that gap is exactly how a guard that read the sign of a
numerically-zero coefficient passed on Linux and failed on Windows. These run
anywhere.

PROVENANCE OF THE FIXTURE, because it matters that this is not a transcript.
Four numbers about that run were recorded: the minimum (1.67 px), its position
(11173), what both ends read (44.70 px), and the stars behind the best point
(321). The intermediate points were not. ``OIII_2026_08_08`` reconstructs them
as the shape that makes both ends read the SAME number — a V whose wings
saturate once the defocused blobs outgrow what the size metric can measure —
and its steepness is pinned by a stronger requirement than matching a quote:
driven through the shipped engine's own state machine it comes back ``failed``,
``r_squared_below_threshold`` (test below). So the fixture carries all three
published values AND reproduces the rejection, which is the most a
reconstruction can honestly claim.
"""
import numpy as np
import pytest

from astrodeck.focus.autofocus import (DEPTH_OVER_ROUGHNESS, MIN_ACCEPT_POINTS,
                                       THIN_POINT_STARS, curve_verdict)

#: The swept positions of that run: 9 points of 350 steps centred on 11173.
POSITIONS = [11173 + 350 * i for i in range(-4, 5)]

#: The reconstructed Oiii curve (see the module docstring) and the stars behind
#: each point — 321 at the tip is the measured one; the wings taper as a real
#: field does when it defocuses.
OIII_2026_08_08 = list(zip(
    POSITIONS,
    [44.70, 44.70, 40.40, 21.03, 1.67, 21.03, 40.40, 44.70, 44.70]))
OIII_STARS = [40, 55, 90, 210, 321, 190, 88, 51, 44]


def test_the_oiii_curve_the_r_squared_gate_threw_away_is_accepted():
    """The whole finding, as one assertion.

    1.67 px at 11173 from 321 stars, rising to 44.70 at both ends: a V that any
    person reading the chart would act on, refused on R² 0.743."""
    v = curve_verdict(OIII_2026_08_08, OIII_STARS)
    assert v.accepted, v.reason
    # Within a fifth of one sweep step of the measured minimum. The vertex is
    # fitted, so it is not required to land exactly on a sampled position — it
    # is required not to wander off the tip.
    assert abs(v.best_position - 11173) <= 70, v.best_position
    # The reason has to carry the evidence, not the verdict: a one-word
    # "accepted" is the same failure as a one-word "not_enough_spread".
    assert "43.03" in v.reason and "321 stars" in v.reason, v.reason


def test_the_engine_really_does_reject_the_curve_this_accepts():
    """The fixture is only worth anything if the shipped engine refuses it.

    Driven through the engine's own state machine — the exact object
    ``run_native_autofocus`` alternates ``next()`` / ``add_measurement`` on —
    this curve comes back ``failed`` / ``r_squared_below_threshold`` while its
    own hyperbolic fit places the minimum on the measured one. That is the
    whole finding, reproduced with no telescope: the fit knows the answer and
    the gate throws it away.

    Skipped without the Rust wheel; the criterion itself never needs it."""
    nat = pytest.importorskip("astrodeck_native")
    by_pos = dict(OIII_2026_08_08)

    # The fit, first: right answer, unacceptable score.
    fit = nat.fit_focus_curve(
        [(float(p), float(h), max(0.05 * float(h), 0.02))
         for p, h in OIII_2026_08_08], "hyperbolic")
    assert abs(fit["best_position"] - 11173) <= 70, fit["best_position"]
    assert (fit.get("r2s") or {})["hyperbolic"] < 0.70, fit["r2s"]

    # Then the state machine, with the same config run_native_autofocus builds.
    sweep = nat.FocusSweep(
        {"step_size": 350, "offset_steps": 4, "max_position": 40000,
         "curve_fitting": "hyperbolic", "r_squared_threshold": 0.7}, 11173)
    for _ in range(60):
        step = sweep.next()
        if step.get("action") != "move_to":
            break
        pos = int(step["position"])
        hfr = by_pos[pos]        # KeyError if it asks outside the fixture
        sweep.add_measurement(pos, hfr, max(0.05 * hfr, 0.02), 200)
    assert step.get("action") == "failed", step
    assert step.get("reason") == "r_squared_below_threshold", step

    # …and the criterion accepts what the gate refused.
    assert curve_verdict(OIII_2026_08_08, OIII_STARS).accepted


def test_a_textbook_v_is_accepted():
    ys = [44.70, 33.94, 23.18, 12.43, 1.67, 12.43, 23.18, 33.94, 44.70]
    v = curve_verdict(list(zip(POSITIONS, ys)), OIII_STARS)
    assert v.accepted, v.reason
    assert abs(v.best_position - 11173) <= 70, v.best_position


def test_a_real_v_with_scatter_on_it_is_still_accepted():
    """Points that wobble are the normal case, not the exception: a defocused
    star's size is measured off a blob spread over a hundred times the pixels a
    focused one covers. A criterion that only likes clean curves buys nothing
    over the gate it replaces."""
    ys = [41.2, 30.1, 25.4, 11.0, 2.1, 13.9, 20.8, 35.5, 46.0]
    v = curve_verdict(list(zip(POSITIONS, ys)),
                      [40, 55, 90, 210, 300, 190, 88, 51, 44])
    assert v.accepted, v.reason
    assert abs(v.best_position - 11173) <= 350, v.best_position


def test_an_asymmetric_v_is_accepted_and_the_vertex_follows_the_tip():
    """The sweep is centred on wherever the focuser started, so the tip is
    rarely in the middle. An off-centre minimum is a normal curve."""
    ys = [52.0, 38.0, 24.0, 9.5, 2.4, 16.0, 31.0, 44.0, 55.0]
    v = curve_verdict(list(zip(POSITIONS, ys)),
                      [30, 50, 90, 180, 260, 150, 80, 50, 40])
    assert v.accepted, v.reason
    # The tip sample is at 11173 and its left neighbour is much lower than its
    # right one, so the true minimum is left of the sample. The vertex must
    # move that way rather than snap to the sample.
    assert v.best_position < 11173, v.best_position


# ---------------------------------------------------------------- refusals
# Each of these is a sweep this project actually recorded, or the shape of one.

def test_the_flat_sweep_that_split_two_platforms_is_refused():
    """Nine identical 3.40 px points: the 2026-08-08 curve that produced a
    confident minimum on CI's Linux/BLAS and failed on Windows, because the
    guard read the sign of a numerically-zero coefficient. Its minimum is at an
    end (every point ties, so the first wins), and it has no wings."""
    v = curve_verdict(list(zip(POSITIONS, [3.40] * 9)), [50] * 9)
    assert not v.accepted
    assert "end of the swept range" in v.reason, v.reason


def test_a_monotone_ramp_is_refused_because_focus_was_never_bracketed():
    """2026-08-01, on the sky: the size metric read smaller the further out the
    search went, so every wrong step looked like an improvement and the sweep
    walked the drawtube 3600 steps. A curve with no interior minimum has not
    found focus, however smooth it is."""
    ys = [3.4, 5.1, 7.8, 11.0, 15.2, 19.9, 25.0, 31.2, 38.0]
    v = curve_verdict(list(zip(POSITIONS, ys)), [100] * 9)
    assert not v.accepted
    assert "never bracketed" in v.reason, v.reason


def test_pure_scatter_is_refused():
    """Nine measurements of nothing in particular. The wings are the test that
    catches it: noise does not rise to both ends."""
    ys = [12.1, 9.4, 14.8, 8.2, 6.9, 13.3, 7.5, 15.1, 10.2]
    v = curve_verdict(list(zip(POSITIONS, ys)), [30] * 9)
    assert not v.accepted
    assert "too shallow" in v.reason, v.reason


def test_a_wing_that_turns_round_is_refused():
    """Both ends high and a deep interior minimum, but the left arm dives on
    its way out. One arm of a V does not do that, and a criterion that accepted
    it would accept a two-minimum curve — which is a focuser slipping, not a
    focus position."""
    ys = [20.0, 44.70, 36.5, 19.1, 1.67, 19.1, 36.5, 44.70, 20.0]
    v = curve_verdict(list(zip(POSITIONS, ys)), OIII_STARS)
    assert not v.accepted
    assert "falls back" in v.reason, v.reason


def test_a_dip_no_deeper_than_the_scatter_is_refused():
    """The case R² would also refuse, and rightly. Depth is judged against the
    curve's OWN roughness — how far each point sits from the midpoint of its
    neighbours — so a straight steep arm costs nothing and only scatter counts."""
    ys = [11.0, 6.4, 10.2, 6.0, 4.6, 7.2, 5.4, 10.8, 11.4]
    v = curve_verdict(list(zip(POSITIONS, ys)), [30] * 9)
    assert not v.accepted
    assert "falls back" in v.reason or "scatter" in v.reason, v.reason


def test_depth_is_measured_against_roughness_not_against_steepness():
    """A steep clean V and a shallow noisy one must be told apart by the same
    number. Build a V of the SAME depth twice, once smooth and once with the
    points shuffled up and down around the arms."""
    smooth = [20.0, 15.0, 10.0, 5.0, 1.0, 5.0, 10.0, 15.0, 20.0]
    rough = [20.0, 11.0, 14.0, 3.0, 1.0, 8.0, 6.0, 18.0, 20.0]
    ok = curve_verdict(list(zip(POSITIONS, smooth)), [100] * 9)
    bad = curve_verdict(list(zip(POSITIONS, rough)), [100] * 9)
    assert ok.accepted, ok.reason
    assert not bad.accepted, bad.reason
    assert bad.roughness > ok.roughness


def test_a_clean_curve_on_a_four_star_tip_is_refused():
    """The shape is perfect and the tip is one detection's opinion. A median
    over four stars carries a hot pixel into the answer with the authority of
    the 321-star frame beside it."""
    ys = [44.70, 33.94, 23.18, 12.43, 1.67, 12.43, 23.18, 33.94, 44.70]
    v = curve_verdict(list(zip(POSITIONS, ys)),
                      [40, 55, 90, 210, 4, 190, 88, 51, 44])
    assert not v.accepted
    assert f"under {THIN_POINT_STARS}" in v.reason, v.reason


def test_missing_star_counts_refuse_rather_than_pass_silently():
    """A caller that hands over no counts gets a refusal, not a free pass. The
    star test is the one that cannot be inferred from the curve."""
    ys = [44.70, 33.94, 23.18, 12.43, 1.67, 12.43, 23.18, 33.94, 44.70]
    assert not curve_verdict(list(zip(POSITIONS, ys))).accepted
    assert not curve_verdict(list(zip(POSITIONS, ys)), [50, 50]).accepted


def test_too_few_points_to_judge_a_shape_is_refused():
    """Four points can be FITTED — the engine needs four — but they leave at
    most one sample on a wing, and one sample cannot be said to rise."""
    few = [(11173 - 350, 20.0), (11173, 2.0), (11173 + 350, 19.0),
           (11173 + 700, 33.0)]
    v = curve_verdict(few, [50, 300, 60, 40])
    assert not v.accepted
    assert str(MIN_ACCEPT_POINTS) in v.reason, v.reason


def test_the_order_points_arrive_in_does_not_change_the_verdict():
    """The engine may extend a sweep past its requested points to bracket a
    minimum, so ``points`` is not guaranteed sorted. A criterion that read
    "wings" off arrival order would accept and refuse the same curve depending
    on which way the focuser happened to travel."""
    shuffled = list(reversed(OIII_2026_08_08))
    a = curve_verdict(OIII_2026_08_08, OIII_STARS)
    b = curve_verdict(shuffled, list(reversed(OIII_STARS)))
    assert a.accepted and b.accepted
    assert a.best_position == pytest.approx(b.best_position)


def test_an_empty_sweep_refuses_instead_of_raising():
    """This runs while a failure is already being reported. It must not be able
    to replace that failure with one of its own."""
    v = curve_verdict([], [])
    assert not v.accepted and v.best_position is None


def test_the_roughness_floor_cannot_divide_by_zero():
    """A synthetic curve with perfectly straight arms has zero roughness. The
    depth test has nothing to compare against, and the answer is to accept on
    the rest of the evidence rather than to raise."""
    ys = [20.0, 15.0, 10.0, 5.0, 1.0, 5.0, 10.0, 15.0, 20.0]
    v = curve_verdict(list(zip(POSITIONS, ys)), [100] * 9)
    assert v.accepted, v.reason
    assert v.roughness == 0.0
    assert "no scatter" in v.reason, v.reason


def test_the_depth_threshold_is_the_one_that_decides_a_borderline_curve():
    """Sabotage-proofing the constant itself: a curve tuned to sit just under
    DEPTH_OVER_ROUGHNESS must fail, and the same curve with its scatter halved
    must pass. If someone widens the constant, one of these two goes red."""
    # A five-point V, depth 10, with one rough point on each wing: roughness
    # 2.5 px against 10 px of depth is x4.0, under the x5 the criterion wants.
    ys = np.array([10.0, 5.0 + 2.5, 0.0, 5.0 - 2.5, 10.0])
    xs = [11173 + 350 * i for i in range(-2, 3)]
    v = curve_verdict(list(zip(xs, ys)), [100] * 5)
    assert not v.accepted, v.reason
    assert "scatter" in v.reason, v.reason
    ys2 = np.array([10.0, 5.0 + 0.3, 0.0, 5.0 - 0.3, 10.0])
    v2 = curve_verdict(list(zip(xs, ys2)), [100] * 5)
    assert v2.accepted, v2.reason
    assert v2.depth / max(v2.roughness, 1e-9) > DEPTH_OVER_ROUGHNESS
