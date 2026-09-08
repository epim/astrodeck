"""The whole autofocus chain on the simulator, with nothing substituted.

Every other autofocus test either hands ``sweep_metric`` a canned curve (so it
exercises the advice copy, not the search) or asserts a failure path. This file
runs sweep -> measure -> fit -> move for real against ``build_sim_rig``, whose
camera renders a star field whose PSF sigma is a known function of focuser
position, so "did it find focus" has a ground-truth answer.

The reason that gap mattered: the metric under the sweep was replaced this week
(``median_hfr`` -> ``imaging.stars.focus_size``) because the old one was flat at
its 15px measurement-box ceiling and every sweep fitted noise. A green suite
said nothing about that, because nothing in the suite ever measured a real
frame. On the sky the loop had never once succeeded, and there was no test that
could have told us whether the search on top of the new metric was sound.

WHY THESE NUMBERS. ``SimCamera._render_stars`` renders each star with
``sigma = (1.8 + |pos - best_focus| / 700) / binning`` px, i.e. a true V in
|offset| with a 1.8px seeing floor at focus, and ``_render`` seeds its RNG from
the focuser position, so every frame in a sweep is reproducible but each
position carries its own independent noise draw. Measured on this rig (60
independent seeds per point, bin 2): ``focus_size`` reads 1.11px at focus,
1.46px at 350 steps out, 1.76px at 700 and 2.40px at 1400 — a clean V — with a
per-point scatter of sd ~0.073px over that span.

That scatter is NOT constant in defocus, and an earlier draft of this file said
it was. It holds between 0.066 and 0.077px out to 1400, then grows: ~0.092px at
2800 and ~0.106px at 4000, which is exactly where the wide sweep's outermost
points sit. Bigger donuts spread the same flux over more pixels, so the size
estimate gets noisier the further out you measure. Quoting the inner figure for
the whole range understates the arms of the very sweep that needs them, so the
numbers below say which range they came from.
"""
from __future__ import annotations

import re

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus import run_autofocus
from astrodeck.imaging.stars import focus_size

#: The default sweep here. A ±1400 window at 350-step spacing around a sim whose
#: V is ~1.3px deep over that span: wide enough that both arms are measurable,
#: narrow enough that the fit has to interpolate rather than read the answer off
#: a sample.
EXPOSURE_S = 0.05
GAIN = 200
STEP = 350
SIDES = 4
BINNING = 2

#: The "I am nowhere near focus" sweep: ±4000 steps at 1000-step spacing, the
#: shape a run takes after a filter change or a fresh cold start. See
#: ``test_a_wide_sweep_from_thousands_of_steps_out_also_converges`` for why it
#: is the case worth spending the extra seconds on.
WIDE_STEP = 1000
WIDE_SIDES = 4

#: How close ``best_position`` has to land, in focuser steps.
#:
#: Chosen from what the sim justifies, not from what passes. Two independent
#: readings agree on ~150:
#:
#: * Measurement. One sweep point's ``focus_size`` scatter is sd ~0.073px over
#:   the inner ±1400 (see the module docstring) and the curve's arms rise at
#:   (2.40-1.11)/1400 = 0.00093 px per step, so a single point is worth ~79
#:   steps of positional uncertainty on its own; a 9-point weighted parabola
#:   pulls that down but not to nothing. A survey over four sweep geometries
#:   (step 250/±5, 350/±4, 500/±4, 1000/±4) and starting offsets across the
#:   whole window measured the landing error as mean ~0 (no bias), sd ~43 steps.
#:   The WORST single landing depends on which offsets you happen to sample —
#:   108 on the grid these tests use, 116 on a denser re-run — so the tolerance
#:   is set from the sd, which is stable, rather than from a worst case that
#:   moves with the sampling. 150 is ~3.5 sd: a real regression fails it, a
#:   benign change in the detector's thresholds does not.
#: * Optics. 150 steps grows the sim's PSF from 1.80px to 2.01px unbinned, 12%
#:   on the seeing floor. That is the scale at which a star starts to look
#:   different, so a tolerance any looser would be calling out-of-focus frames
#:   focused.
#:
#: For contrast, the fit's historical miss was "hundreds of units", which the
#: ±400 in ``test_autofocus.py::test_autofocus_converges`` cannot tell from
#: success.
FOCUS_TOLERANCE_STEPS = 150

#: Two ``focus_size`` readings of the same physical defocus differ by sd ~0.10px
#: (0.073 each, added in quadrature), which makes 0.25 about 2.4 sd of pure
#: noise — not the "just under 3 sd" an earlier draft of this comment asserted.
#: 3 sd would have been 0.31px, so that draft was quoting a tolerance 25% tighter
#: than the one it had actually written down.
#:
#: 2.4 sd is the right budget regardless, because the noise is only the floor and
#: this number is bounded from both ends. The ceiling is what it must CATCH: one
#: sweep step of mis-parking (350) moves the metric by 1.46-1.11 = 0.35px, so a
#: budget at or above that would let a verify frame taken a whole step off the
#: vertex pass as if it were the vertex. 0.25 sits between the two — clear of the
#: noise, and under the smallest positional error worth failing a run over.
METRIC_AGREEMENT_PX = 0.25


async def _rig_at(start_position: int, best_focus: int):
    """A connected sim camera+focuser with a known true focus, parked at a known
    starting offset. Returns ``(rig, camera, focuser)``."""
    parts = build_sim_rig()
    rig = parts["_rig"]
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    rig.best_focus = best_focus
    rig.focuser_pos = start_position
    return rig, cam, foc


async def _sweep(cam, foc, step: int = STEP, sides: int = SIDES):
    return await run_autofocus(cam, foc, exposure_s=EXPOSURE_S, gain=GAIN,
                               step=step, steps_each_side=sides, binning=BINNING)


@pytest.mark.parametrize("best_focus, start", [
    # Both arms of the V, near and far, on each side. The 225-below and
    # 750-above cases were the two worst landings in the 45-run narrow survey
    # (-93 and +89 steps): they are here on purpose, so the tolerance is asked
    # to hold at the measured worst case rather than at a comfortable one.
    pytest.param(20_000, 18_950, id="1050-below-focus"),
    pytest.param(20_000, 19_775, id="225-below-focus"),
    pytest.param(20_000, 20_750, id="750-above-focus"),
    pytest.param(20_000, 21_050, id="1050-above-focus"),
    # A true focus the sim does NOT default to (20000), at the other end of the
    # travel: proves the loop is finding the minimum rather than agreeing with a
    # number that happens to be baked into the fixture.
    pytest.param(30_000, 29_450, id="550-below-focus-at-30000"),
])
async def test_autofocus_finds_the_sims_true_focus_from_either_side(
        best_focus, start):
    rig, cam, foc = await _rig_at(start, best_focus)

    result = await _sweep(cam, foc)

    assert result.success, f"{result.message} / {result.advice}"
    miss = result.best_position - rig.best_focus
    assert abs(miss) <= FOCUS_TOLERANCE_STEPS, (
        f"landed {miss:+d} steps from the sim's true focus {rig.best_focus} "
        f"(tolerance ±{FOCUS_TOLERANCE_STEPS}); curve was {result.points}")
    # A run that reports success without a verify measurement has not verified
    # anything.
    assert result.best_hfr is not None


@pytest.mark.parametrize("offset", [
    # -2500 was the worst landing (-108 steps) in the wide-sweep survey and
    # +3000 is the farthest start the ±4000 window still brackets; both are here
    # because they are the extremes, not because they are comfortable.
    pytest.param(-2_500, id="2500-below-focus"),
    pytest.param(3_000, id="3000-above-focus"),
])
async def test_a_wide_sweep_from_thousands_of_steps_out_also_converges(offset):
    """A ±4000-step sweep started thousands of steps out still lands inside the
    same tolerance as a ±1400 one.

    This is the case that pays for itself. At the narrow window the sim's PSF
    never grows past ~1.9px at bin 2, which is comfortably inside the old
    ``median_hfr``'s 15px measurement box — so the narrow tests above pass
    identically whichever metric the sweep uses, and they cannot tell you the
    metric swap was necessary. Out here the difference shows: re-running this
    same survey with ``median_hfr`` substituted for ``focus_size`` puts 6 of 13
    starting offsets outside ±150, worst 207 steps, while ``focus_size`` keeps
    every one of them inside 108.

    It is also the run the user actually needs. A narrow sweep is a nudge after
    a filter change; this is the one you start after a cold boot, and it is the
    one that on the real sky has never yet succeeded.
    """
    rig, cam, foc = await _rig_at(20_000 + offset, 20_000)

    result = await _sweep(cam, foc, step=WIDE_STEP, sides=WIDE_SIDES)

    assert result.success, f"{result.message} / {result.advice}"
    miss = result.best_position - rig.best_focus
    assert abs(miss) <= FOCUS_TOLERANCE_STEPS, (
        f"landed {miss:+d} steps out from a {offset:+d}-step start "
        f"(tolerance ±{FOCUS_TOLERANCE_STEPS}); curve was {result.points}")
    assert await foc.get_position() == result.best_position


async def test_the_focuser_is_left_at_the_position_the_run_reports():
    """A sweep that reports a position and leaves the focuser somewhere else
    shoots the rest of the night out of focus while the UI says it succeeded.
    The final approach moves twice (down by one step for backlash, then up), so
    "reported == where it stopped" is a claim about the LAST move landing, not a
    tautology."""
    _, cam, foc = await _rig_at(19_200, 20_000)

    result = await _sweep(cam, foc)

    assert result.success, result.message
    assert await foc.get_position() == result.best_position


async def test_the_reported_hfr_is_the_size_measured_at_the_reported_position():
    """``best_hfr`` has to be the frame at ``best_position``, not the best point
    the sweep happened to sample. The sim renders deterministically from the
    focuser position, so re-exposing where the run left the focuser must
    reproduce the number it published exactly — if it does not, the reported
    figure describes some other position."""
    _, cam, foc = await _rig_at(19_200, 20_000)

    result = await _sweep(cam, foc)
    assert result.success, result.message

    frame = await cam.expose(EXPOSURE_S, GAIN, 30, binning=BINNING)
    again, _n, _size = focus_size(frame.data)
    assert again == pytest.approx(result.best_hfr, abs=1e-9)


async def test_the_reported_hfr_sits_at_the_bottom_of_the_measured_curve():
    """The published HFR must be consistent with the V the run just measured:
    at or below the lowest sampled point (the fitted vertex lies between
    samples, so it can only be better, up to the metric's own scatter), and far
    below the arms. A number that came back level with the arms would mean the
    focuser did not go where the fit said."""
    _, cam, foc = await _rig_at(19_200, 20_000)

    result = await _sweep(cam, foc)
    assert result.success, result.message

    sizes = [h for _, h in result.points]
    assert result.best_hfr <= min(sizes) + METRIC_AGREEMENT_PX, (
        f"verify frame read {result.best_hfr:.3f}px but the sweep's best "
        f"sample was {min(sizes):.3f}px")
    # The arms of this sweep sit around 2.4px against ~1.1px at the vertex, so
    # anything above half the worst sample is not the bottom of a V.
    assert result.best_hfr < 0.6 * max(sizes), (
        f"verify frame read {result.best_hfr:.3f}px against a sweep maximum of "
        f"{max(sizes):.3f}px — that is an arm, not a minimum")


@pytest.mark.parametrize("offset, direction", [
    # +2000 puts true focus 600 steps above the top of the ±1400 window, -1800
    # puts it 400 below the bottom. Both are close enough OUTSIDE that the fit
    # still opens upward (a > 0) and reaches the bracket check — which is the
    # guard under test. Further out (say ±3500) the swept segment is nearly a
    # straight line and the flat-fit guard catches it first; that is an honest
    # failure too, but it would not prove this one survived.
    pytest.param(2_000, "above", id="focus-above-the-window"),
    pytest.param(-1_800, "below", id="focus-below-the-window"),
])
async def test_focus_outside_the_swept_window_fails_instead_of_clipping(
        offset, direction):
    """The fitted vertex falling outside the measured range means the sweep
    never saw the minimum. Clipping it to whichever edge is nearer and reporting
    success parks the focuser at a position that is definitively NOT in focus
    and tells the user it is — the exact shape of lie this codebase exists to
    stop. Fail, say why, and put the focuser back."""
    start = 19_200
    _, cam, foc = await _rig_at(start, start + offset)
    lo_edge, hi_edge = start - STEP * SIDES, start + STEP * SIDES
    span = hi_edge - lo_edge

    result = await _sweep(cam, foc)

    assert not result.success
    assert "bracket" in result.message.lower(), result.message
    assert result.best_position not in (lo_edge, hi_edge)
    assert result.best_position == start
    # No verify frame was taken, so there is no HFR to report and none is
    # invented.
    assert result.best_hfr is None
    # The run knows WHICH WAY to look — the stars were still shrinking toward
    # one end — and it must say so rather than leaving a 2am user to guess.
    assert result.advice
    assert f"{direction} the swept range" in result.advice, result.advice
    # How FAR it does not know, and it used to answer anyway: a vertex
    # extrapolated off one arm put "re-run centred near -921283" in front of a
    # user whose focus was 600 steps past the edge. It may still name a target —
    # a near-miss vertex is real information — but only one the focuser can
    # reach and near enough to the data that the measured arm constrains it.
    quoted = re.search(r"centred near (-?\d+)", result.advice)
    if quoted:
        target = int(quoted.group(1))
        assert 0 <= target <= foc.max_position, result.advice
        assert lo_edge - span <= target <= hi_edge + span, result.advice
    # Left where the user had it, not stranded up to a full window out of focus.
    assert await foc.get_position() == start
