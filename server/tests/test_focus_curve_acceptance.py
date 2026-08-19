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


# ------------------------------------------------- the NGC 5907 run, 2026-08-08

#: The ten points the rig actually measured on NGC 5907 at 22:04 local, 5 s at
#: gain 120, bin 2, L. The ELEVENTH — 9423, five half-steps out — showed 2
#: detectable stars where a fit point needs 3, three times in a row, and the
#: whole run was discarded for it.
NGC5907_POSITIONS = [9773, 10123, 10473, 10823, 11173,
                     11523, 11873, 12223, 12573, 12923]
NGC5907 = list(zip(NGC5907_POSITIONS,
                   [55.91, 42.71, 28.64, 14.20, 1.90,
                    13.83, 27.68, 41.07, 54.68, 65.97]))
NGC5907_STARS = [3, 3, 7, 12, 460, 18, 6, 4, 3, 4]


def test_the_5907_curve_survives_losing_the_point_that_killed_the_run():
    """A sweep that runs out of measurable range still measured a V.

    The engine never rejected this fit — hyperbolic R² was 0.996 against a 0.70
    gate. The run died because ONE planned position could not be measured, and
    the drop-abort returned before anything asked the curve. Ten points, a
    minimum 64 px deep sitting on 460 stars, both wings rising monotonically:
    the failure was at the end of the sweep, not at the focus.
    """
    v = curve_verdict(NGC5907, NGC5907_STARS)
    assert v.accepted, v.reason
    assert abs(v.best_position - 11173) <= 70, v.best_position


def test_the_5907_tip_is_where_the_stars_are():
    """Guard the specific inversion that would make this dangerous.

    Accepting on shape must not accept a tip nobody could measure. 5907's tip
    carries 460 stars and its wings carry 3 or 4 — if the criterion ever keyed
    on the wings it would still 'pass' this curve while locating focus in the
    wrong place, so pin the vertex to the rich end.
    """
    tip = max(range(len(NGC5907_STARS)), key=lambda i: NGC5907_STARS[i])
    assert NGC5907_POSITIONS[tip] == 11173
    v = curve_verdict(NGC5907, NGC5907_STARS)
    assert v.accepted and abs(v.best_position - NGC5907_POSITIONS[tip]) <= 70


def test_a_thin_field_that_really_cannot_focus_is_still_refused():
    """The counterpart, so the salvage cannot swallow the SII case it was
    bounded for. Same shape, but nothing anywhere had stars to fit."""
    thin = list(zip(NGC5907_POSITIONS,
                    [55.91, 42.71, 28.64, 14.20, 1.90,
                     13.83, 27.68, 41.07, 54.68, 65.97]))
    v = curve_verdict(thin, [3, 3, 3, 3, 4, 3, 3, 3, 3, 3])
    assert not v.accepted, v.reason


# --------------------------------------- what a sweep that over-reached is told

def test_a_rich_field_is_not_told_to_expose_longer_for_narrowband():
    """The #114 wrong turn, in the copy this exact run produced.

    NGC 5907 held 460 stars at its best point and the refusal blamed the filter
    and recommended a narrowband exposure. The field was never the problem.
    """
    from astrodeck.focus.autofocus import over_swept_advice
    a = over_swept_advice(460, NGC5907_POSITIONS, 9423, "a longer exposure")
    assert "narrowband" not in a.lower(), a
    assert "460 stars" in a, a
    # It has to name the range that DID work and the setting that gets there.
    assert "9773..12923" in a, a
    assert "steps_each_side 4" in a, a


def test_a_genuinely_thin_field_still_gets_the_exposure_advice():
    """The counterpart. The SII slot this bound was built for really cannot be
    focused at those settings, and telling someone to expose longer is right."""
    from astrodeck.focus.autofocus import over_swept_advice
    a = over_swept_advice(4, NGC5907_POSITIONS, 9423, "a longer exposure")
    assert "narrowband" in a.lower(), a
    assert "too thin" in a, a


def test_the_threshold_is_what_separates_them():
    """Pin the constant itself: it is the whole decision, and an end-to-end test
    cannot reach both sides of it on the sim."""
    from astrodeck.focus.autofocus import RICH_FIELD_STARS, over_swept_advice
    just_under = over_swept_advice(RICH_FIELD_STARS - 1, NGC5907_POSITIONS,
                                   9423, "a longer exposure")
    just_over = over_swept_advice(RICH_FIELD_STARS, NGC5907_POSITIONS,
                                  9423, "a longer exposure")
    assert "narrowband" in just_under.lower(), just_under
    assert "narrowband" not in just_over.lower(), just_over


# ---------------------------------------------------------------- the donut end

# MEASURED ON THE RIG, NGC 7129, 2026-08-18 21:27. A sweep at the shipped 350
# step, whose outer points sit 1400 steps from focus where the model predicts a
# 105px blob. Both ends turned back -- far outside focus a star is a large faint
# donut, and the detector sizes the ring SMALLER than the solid blob further in.
# The right end's 17.27px fall-back tripped the wing test, the whole sweep was
# discarded, and the rig imaged the next hour at HFR 5.61 against an achievable
# 3.05.
RIG_7129 = [(10100, 55.85), (10450, 66.70), (10800, 32.28), (11150, 3.88),
            (11500, 24.65), (11850, 56.75), (12200, 39.48)]
RIG_7129_STARS = [14, 25, 32, 2539, 36, 20, 11]


def test_a_sweep_whose_ends_turned_back_is_still_a_v():
    """The outermost point of a wing measuring SMALLER than the point inside it
    is a fact about the end of the sweep, not about the focus. Peel it and judge
    what is left."""
    v = curve_verdict(RIG_7129, RIG_7129_STARS)
    assert v.accepted, f"rejected the rig's own V-curve: {v.reason}"
    assert v.best_position is not None
    assert 11100 < v.best_position < 11250, (
        f"vertex at {v.best_position}; the measured minimum was 11150 from 2539 "
        "stars and the sweep that followed put focus at 11193")


def test_the_trim_is_named_in_the_reason():
    """An operator reading the log must see that points were dropped, or a
    5-point verdict on a 7-point sweep is unexplainable."""
    v = curve_verdict(RIG_7129, RIG_7129_STARS)
    assert "turn" in v.reason.lower() or "peel" in v.reason.lower() \
        or "outer" in v.reason.lower() or "trim" in v.reason.lower(), v.reason


def test_a_wing_that_turns_over_in_its_MIDDLE_is_still_rejected():
    """Only the ENDS get peeled. A fall-back between two interior points is
    scatter or a double star, and it still means the curve is not one V."""
    pts = [(1000, 20.0), (1100, 5.0), (1200, 12.0), (1300, 2.0), (1400, 14.0),
           (1500, 18.0), (1600, 22.0)]
    v = curve_verdict(pts, [50] * 7)
    assert not v.accepted, f"accepted a curve with two minima: {v.reason}"


def test_peeling_never_eats_the_minimum():
    """A monotonically falling curve has its smallest value AT an end -- focus
    was never bracketed. Peeling must not manufacture a bracket by eating the
    curve down to a fake interior minimum."""
    pts = [(1000 + 100 * k, 40.0 - 4.0 * k) for k in range(8)]
    v = curve_verdict(pts, [50] * 8)
    assert not v.accepted, f"accepted an unbracketed ramp: {v.reason}"
    assert "bracket" in v.reason.lower() or "end" in v.reason.lower(), v.reason


def test_a_clean_curve_is_not_trimmed():
    """Peeling is for turned-back ends only; a well-formed V keeps every point."""
    pts = [(1000, 30.0), (1100, 18.0), (1200, 8.0), (1300, 2.0), (1400, 9.0),
           (1500, 19.0), (1600, 31.0)]
    v = curve_verdict(pts, [200] * 7)
    assert v.accepted, v.reason
    assert "31.00" in v.reason, (
        "the reason should still quote the true outer wing, so an untrimmed "
        f"curve reads as untrimmed: {v.reason}")


def test_a_turned_end_measured_from_PLENTY_of_stars_is_not_a_donut():
    """The whole peel rests on star starvation. Same geometry as the rig curve,
    but the ends carry a healthy share of the tip's stars — so the turnaround is
    a real second minimum (a slipping focuser), not the detector running out of
    range, and it must still be refused."""
    healthy = [800, 25, 32, 2539, 36, 20, 700]      # ends at ~30% of the tip
    v = curve_verdict(RIG_7129, healthy)
    assert not v.accepted, (
        "peeled an end the detector had no trouble measuring — geometry alone "
        f"cannot excuse a turnaround: {v.reason}")
    assert "falls back" in v.reason, v.reason


def test_a_starved_end_that_IS_the_minimum_is_never_peeled():
    """Every condition for a peel except the one that matters.

    The last point is star-starved AND turned back AND the smallest thing
    measured — which means focus is outside the swept range, not that the
    detector gave up. Peeling it would MANUFACTURE a bracket: the point inward
    becomes an interior low and what is really an unbracketed sweep gets
    reported as a focus position at the wrong place.

    A plain ramp cannot show this (peel a monotonic curve and the minimum is
    still at the end), so the shape here is one where the second-from-last point
    is high enough to become a fake wing.
    """
    ys = [50.0, 40.0, 32.0, 20.0, 45.0, 3.0]
    pts = [(1000 + 100 * k, y) for k, y in enumerate(ys)]
    starved_at_the_low_end = [900, 900, 900, 900, 900, 4]
    v = curve_verdict(pts, starved_at_the_low_end)
    assert not v.accepted, (
        "peeled the smallest point off the end and called what was left a V — "
        f"focus is below 1000 here, not at 1300: {v.reason}")
    assert "bracket" in v.reason.lower(), v.reason
