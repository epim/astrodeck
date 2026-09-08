"""#114 — the run's OWN account of a failure has to reach the user.

2026-07-31 is why this file exists. Autofocus failed and the panel said "Not
enough stars to lock onto — check the sky is clear and roughly focused, then try
again." The sky was 2% cloud and the rig was ~10 steps off the true minimum, so
both instructions were wrong and both were acted on. The same run had already
worked out the real answer and written it to the log, where nobody was looking:
that many stars is a sparse field FOR THIS EXPOSURE AND BINNING, and the sweep
would run out of measurable points as it defocused.

Two claims are pinned here:

* the advice a run earns rides out on the RESULT and on the ``focus`` event the
  UI actually reads — not only into the log;
* a point measured from one or two detections is refused, not fitted as though
  it were a 900-star sample (and the refusal is visible, not silent).
"""
import re

import numpy as np
import pytest

from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus import run_autofocus
from astrodeck.focus.autofocus import MIN_STARS_PER_POINT
import astrodeck.focus.autofocus as A
import astrodeck.focus.native as N

native_only = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


async def _connected_sim():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return parts["_rig"], cam, foc


def _focus_events(q):
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "focus":
            out.append(ev.data)
    return out


# --------------------------------------------------------------- native engine

@native_only
async def test_a_sparse_field_run_carries_its_own_advice_out_on_the_result(monkeypatch):
    """A field rich enough to start but not to sweep: the failure must name the
    exposure and binning that would fix it, on the result and on the event.

    This is the 2026-07-31 shape exactly — 8 stars at the start position, nothing
    measurable once defocused. The server worked that out at the first frame; the
    user was told to check the sky."""
    _rig, cam, foc = await _connected_sim()
    seen = {"calls": 0}

    def detector(data, params):
        seen["calls"] += 1
        if seen["calls"] == 1:      # the pre-sweep probe, at the start position
            return [], {"star_count": 8, "hfr_median": 3.4, "hfr_mad": 0.30}
        return [], {"star_count": 1, "hfr_median": 5.0, "hfr_mad": 0.0}

    monkeypatch.setattr(N._native, "detect_and_measure", detector)
    # The SIZE feeding the fit comes from the metric seam, not the detector.
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 1))

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4, binning=2)
    finally:
        bus.unsubscribe(q)

    assert res.success is False
    assert res.advice, "the run's own advice never left the log"
    # the measurement that decided it, and the two knobs that change it
    assert "8 stars" in res.advice, res.advice
    assert "longer exposure" in res.advice and "bin 1" in res.advice, res.advice
    # it does NOT send the user outside: the sky was never the finding
    assert "sky" not in res.advice.lower(), res.advice
    # and the panel gets the same words the result carries
    events = _focus_events(q)
    assert events[-1]["state"] == "failed"
    assert events[-1].get("advice") == res.advice


@native_only
async def test_near_empty_points_never_reach_the_fit(monkeypatch):
    """One frame in the sweep yields two detections. A "median" over two
    detections is a hot pixel's opinion; before this it entered the fit weighted
    exactly like a frame with 900 stars. It must be refused — and the refusal
    must show up in the advice rather than being swallowed as a skip."""
    _rig, cam, foc = await _connected_sim()
    real_sweep = N._native.FocusSweep
    POISON_HFR = 0.4242          # unmistakable: no real sweep point looks like this
    #: DRIVEN FROM THE METRIC SEAM, once. It used to be driven from a counter on
    #: ``_native.detect_and_measure``, which now runs ONCE per run (the probe
    #: keeps it; the loop dropped it) — so that counter would sit at 1 forever
    #: and this test would report success while intercepting nothing the fit
    #: reads, which is the exact failure ``native_sweep_metric``'s docstring
    #: warns about. The fourth measurement is mid-sweep, well past the probe.
    fired = {"n": 0}

    fitted: list[tuple[int, float, float, int]] = []

    class SpySweep:
        """Records exactly what the host hands the fitter."""
        def __init__(self, cfg, start):
            self._s = real_sweep(cfg, start)

        def next(self):
            return self._s.next()

        def add_measurement(self, position, hfr, sigma, star_count):
            fitted.append((position, hfr, sigma, star_count))
            return self._s.add_measurement(position, hfr, sigma, star_count)

    # The probe's count sizes the measurement window. 20 stars is under the
    # crop threshold, so every point is measured on the whole frame and the
    # single poisoned point cannot be re-measured by the window fallback —
    # which is a different mechanism, tested in
    # test_autofocus_measures_the_centre.py.
    monkeypatch.setattr(N._native, "detect_and_measure",
                        lambda data, params: ([], {"star_count": 20,
                                                   "hfr_median": 3.0,
                                                   "hfr_mad": 0.2}))
    monkeypatch.setattr(N._native, "FocusSweep", SpySweep)

    def metric(frame):
        fired["n"] += 1
        return (POISON_HFR, 2) if fired["n"] == 4 else (3.0, 500)
    monkeypatch.setattr(N, "native_sweep_metric", metric)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=2)

    assert fitted, "the sweep measured nothing at all"
    assert all(n >= MIN_STARS_PER_POINT for *_rest, n in fitted), \
        f"a sub-{MIN_STARS_PER_POINT}-star sample was fed to the fitter: {fitted}"
    assert all(hfr != POISON_HFR for _p, hfr, _s, _n in fitted)
    assert all(h != POISON_HFR for _p, h in res.points)
    # visible, not silent: the run says which points it threw away and why
    assert res.advice and "dropped rather than fitted" in res.advice, res.advice
    assert f"fewer than {MIN_STARS_PER_POINT}" in res.advice, res.advice


@native_only
async def test_a_measured_failure_is_not_blamed_on_the_star_count(monkeypatch):
    """Twelve stars at every point, every point measured, nothing dropped, and a
    curve too flat to fit. That is a step-size or focuser fault and the engine
    says so (``not_enough_spread``, which the panel renders as "HFR barely
    changed from one end of the sweep to the other… increase the step size").
    The advice OUTRANKS that line in the panel, so anything invented here does
    not sit beside the real explanation — it replaces it.

    The regression: advice was keyed off the start-position star count alone, so
    any native failure under 15 stars was captioned "Only 12 stars in the field
    — too few to keep measuring as the sweep defocuses", contradicted by the
    run's own record and sending the user after exposure and sky for a focuser
    that would not move. The same fault with 400 stars said nothing and got the
    right explanation, so the sparse-field user was told something strictly
    worse for an identical fault — on the very rigs that filed the bug."""
    _rig, cam, foc = await _connected_sim()

    def flat(n):
        return lambda data, params: (
            [], {"star_count": n, "hfr_median": 3.4, "hfr_mad": 0.30})

    # A curve too flat to fit, expressed through the seam the fit reads.
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (3.4, 12))

    monkeypatch.setattr(N._native, "detect_and_measure", flat(12))
    sparse = await N.run_native_autofocus(cam, foc, exposure_s=2.0, gain=200,
                                          step=350, steps_each_side=4, binning=2)
    monkeypatch.setattr(N._native, "detect_and_measure", flat(400))
    rich = await N.run_native_autofocus(cam, foc, exposure_s=2.0, gain=200,
                                        step=350, steps_each_side=4, binning=2)

    assert sparse.success is False and rich.success is False
    # Nothing was dropped and nothing was unmeasurable, so the run learned
    # nothing the engine had not already said: it keeps quiet and lets the
    # engine's reason through.
    assert sparse.advice is None, sparse.advice
    # And the answer does not depend on how rich the field happened to be.
    assert sparse.advice == rich.advice
    assert sparse.message == rich.message


@native_only
async def test_a_frame_full_of_stars_never_earns_expose_longer(monkeypatch):
    """A point can also vanish because the detector found stars and could not
    size them — our fault, not the sky's. That must not be filed as "too few
    stars", because the remedy attached to that finding is a longer exposure,
    and telling someone with 50 stars in the frame to expose longer is the same
    wrong turn as telling them to check a clear sky."""
    _rig, cam, foc = await _connected_sim()
    calls = {"n": 0}

    # A probe rich enough to sweep and too thin to crop, so every point is
    # measured whole and the one unsizeable frame is not re-measured by the
    # window fallback (a different mechanism — see
    # test_autofocus_measures_the_centre.py).
    monkeypatch.setattr(N._native, "detect_and_measure",
                        lambda data, params: ([], {"star_count": 30,
                                                   "hfr_median": 3.0,
                                                   "hfr_mad": 0.2}))

    # "Found them, could not size them" is a property of the SIZE seam: a rich
    # frame whose sources the metric could not measure. COUNTED ON THAT SEAM —
    # the Rust detector now runs once per run, so a counter on it would sit at
    # 1 and poison nothing.
    def metric(frame):
        calls["n"] += 1
        return (None, 50) if calls["n"] == 4 else (3.0, 200)

    monkeypatch.setattr(N, "native_sweep_metric", metric)
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=2)

    assert res.advice, "the dropped frame was never mentioned"
    assert "could not size" in res.advice, res.advice
    assert "longer exposure" not in res.advice, res.advice
    assert "fewer than" not in res.advice, res.advice


@native_only
def test_a_thin_median_is_weighted_like_the_guess_it_is():
    """σ is the standard error of the point's median, so star count — not just
    scatter — decides how hard a point pulls on the curve.

    The failure this pins: a 3-star frame whose three stars happened to agree
    (MAD 0.02) used to be handed to the fitter as the most certain point in the
    sweep, outvoting a 900-star frame with honest scatter. Engine weight is
    1/σ², so the ratio below is the whole fix."""
    thin = N.point_sigma(hfr=3.0, mad=0.02, n_stars=3)
    rich = N.point_sigma(hfr=3.0, mad=0.30, n_stars=900)
    assert thin > rich, (thin, rich)
    # By a margin that matters. Passing raw MAD through, these same two frames
    # gave the 3-star point 1/0.02² = 2500 against the 900-star point's 1/0.30²
    # = 11 — 225x MORE pull for the guess. Now it has under a tenth as much.
    assert (rich / thin) ** 2 < 0.1, f"thin point still carries {(rich/thin)**2:.2f} weight"
    # the 2% floor: a freak zero-MAD sample is not infinitely precise (it used to
    # become a 1/0.001² = 1e6-weight anchor).
    assert N.point_sigma(hfr=3.0, mad=0.0, n_stars=9) > 0.02


# --------------------------------------------------------------- legacy numpy path

async def test_legacy_sweep_refuses_thin_points_and_says_why(monkeypatch):
    """The no-provider numpy path has the same duty: refuse near-empty frames,
    and let the failure name the sweep's own exposure/binning instead of the
    stock "need stars in frame"."""
    _rig, cam, foc = await _connected_sim()
    asked: list[int] = []

    def fake_metric(data, min_stars=3):
        # Two detections is what the frame has; the sweep's own floor decides
        # whether that is a measurement.
        asked.append(min_stars)
        return (None, 2, None) if 2 < min_stars else (3.0, 2, None)

    monkeypatch.setattr(A, "sweep_metric", fake_metric)

    q = bus.subscribe()
    try:
        res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                                  steps_each_side=4, binning=2)
    finally:
        bus.unsubscribe(q)

    assert res.success is False
    # the sweep sets the floor itself rather than inheriting the detector default
    assert set(asked) == {MIN_STARS_PER_POINT}, asked
    assert res.points == [], "a 2-star sample was recorded as a curve point"
    assert res.advice, "the failure carried no advice"
    assert "9 sweep points" in res.advice, res.advice
    assert "longer exposure than 2s" in res.advice, res.advice
    assert "bin 1 instead of 2" in res.advice, res.advice
    assert _focus_events(q)[-1].get("advice") == res.advice
    # the message says what was measured against what was needed — no guessing
    assert "0 of 9" in res.message, res.message


async def test_an_all_thin_sweep_is_not_told_the_fit_discounted_it(monkeypatch):
    """Both fitters weight RELATIVELY — √n into ``polyfit(w=…)``, 1/σ² in the
    engine — and both are invariant to scaling every weight alike. So when all
    nine points carry the same star count the weighting cancels exactly and the
    fit leans on them completely.

    The copy said "so the fit barely leans on them", which is a claim about our
    own arithmetic that our own arithmetic contradicts, and it said it in the
    one run where it mattered. It then appended "Try a longer exposure…" to a
    sentence that had just diagnosed a stuck focuser or too-narrow sweep,
    leaving the user to pick which half of their own advice to believe."""
    _rig, cam, foc = await _connected_sim()
    # Measurable, thin, and identical everywhere: a flat curve read off nine
    # equally weak samples.
    monkeypatch.setattr(A, "sweep_metric",
                        lambda data, min_stars=3: (3.4, 8, None))

    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)
    assert res.success is False
    assert res.advice
    assert "barely leans" not in res.advice, res.advice
    assert "nothing richer to weigh them against" in res.advice, res.advice
    # the flat-curve diagnosis keeps its own remedy and is not undercut by a
    # rival instruction: the levers are offered as evidence quality, not as a fix
    assert "raise the step size" in res.advice, res.advice
    assert "Try a longer exposure" not in res.advice, res.advice
    assert "A firmer result would need a longer exposure" in res.advice, res.advice


async def test_a_thin_point_beside_rich_ones_is_reported_as_discounted(monkeypatch):
    """The other half of the same claim: one 4-star point among 500-star ones
    genuinely IS down-weighted, and saying so is worth a line — with the richest
    count in it, so the user can check the claim instead of taking it."""
    rig, cam, foc = await _connected_sim()

    def mixed(frame, min_stars=3):
        pos = frame.focuser_position
        return (9.0, 4, None) if abs(pos - 18500) < 100 else \
            (2.0 + ((pos - 19200) / 1000.0) ** 2, 500, None)

    monkeypatch.setattr(A, "sweep_metric", mixed)
    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)
    assert res.success, res.message
    assert res.advice and "1 of 9 fitted points" in res.advice, res.advice
    assert "richest of 500" in res.advice, res.advice
    assert "the fit discounts them" in res.advice, res.advice


def _v_curve_at(rig, minimum: int):
    """A textbook V with its vertex at ``minimum``, 500 stars everywhere — so
    the fit is exact and the only thing under test is what the run SAYS about a
    vertex it cannot see.

    Read off the FRAME, not off ``rig.focuser_pos``. The sweep exposes the next
    point while this one is being measured, so the live focuser position is the
    NEXT point's and a curve built from it comes out shifted one step — which is
    how this substitute failed the day pipelining landed. The sim stamps every
    frame with the position it was rendered at (``CameraFrame.focuser_position``)
    precisely so a double can say which point it is looking at.
    """
    def metric(frame, min_stars=3):
        return (2.0 + ((frame.focuser_position - minimum) / 1000.0) ** 2,
                500, None)
    return metric


async def test_a_vertex_just_past_the_edge_still_earns_its_number(monkeypatch):
    """A minimum 800 steps outside a 2800-step window is a near miss: the arm
    the sweep DID measure still constrains where the turn is, so the run has
    earned the right to name the position and the user has earned a second run
    that lands. This is the half of the not-bracketed advice that was always
    right, pinned so the fix below cannot swallow it."""
    rig, cam, foc = await _connected_sim()
    # sim starts at 19200; ±4·350 sweeps 17800..20600.
    monkeypatch.setattr(A, "sweep_metric", _v_curve_at(rig, 21_400))

    res = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                              steps_each_side=4, binning=2)

    assert res.success is False
    assert "bracket" in res.message
    assert res.advice
    # Parsed, not string-matched: the vertex is a float landing on 21399.99…,
    # so pinning the exact digits would break on a numpy rounding change without
    # anything being wrong with the advice.
    said = re.search(r"about (\d+) steps above the swept range", res.advice)
    where = re.search(r"Re-run centred near (\d+)", res.advice)
    assert said and where, res.advice
    assert abs(int(said.group(1)) - 800) <= 2, res.advice
    assert abs(int(where.group(1)) - 21_400) <= 2, res.advice


async def test_a_vertex_extrapolated_off_the_end_is_not_quoted_as_a_position(
        monkeypatch):
    """The other half, which was wrong every single time.

    A parabola whose vertex falls far outside the measured points is
    extrapolating off ONE arm, where its curvature comes from the noise on the
    last few samples rather than from the V. Measured on the simulator
    2026-08-01 with true focus 200-1600 steps outside a 2800-step window, that
    extrapolation returned 8935, 12540, 16804, 22036 and once 939083 steps — so
    the run told the user to "re-run centred near -921283", a focuser position
    that does not exist, for a focus 800 steps past the edge. Direction is
    knowledge here; distance is not, and a fabricated distance costs a second
    wasted sweep.

    Here the vertex lands at -5000, below the focuser's travel entirely."""
    rig, cam, foc = await _connected_sim()
    monkeypatch.setattr(A, "sweep_metric", _v_curve_at(rig, -5_000))

    res = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                              steps_each_side=4, binning=2)

    assert res.success is False
    assert res.advice
    # No number it cannot stand behind: not the extrapolated distance (22800
    # steps below the edge), not the impossible position it points at.
    assert "centred near" not in res.advice, res.advice
    assert "22800" not in res.advice and "-5000" not in res.advice, res.advice
    # What it does know, and did not used to say: which way, and the best thing
    # it actually measured.
    assert "below the swept range" in res.advice, res.advice
    assert "was at 17800" in res.advice, res.advice
    # And a lever that is true whatever the distance turns out to be.
    assert "step at 700" in res.advice, res.advice


async def test_legacy_fit_lets_the_richest_frames_decide(monkeypatch):
    """A four-star point cannot drag the vertex the way a five-hundred-star one
    can. Before weighting, every point voted alike, so a single thin sample near
    the minimum could move the answer hundreds of steps."""
    rig, cam, foc = await _connected_sim()
    true_focus = 19200          # the sim starts here; keep the vertex in the window
    outlier_at = 18500

    def fake_metric(frame, min_stars=3):
        # A textbook V, except at one position where four stars report nonsense.
        pos = frame.focuser_position
        if abs(pos - outlier_at) < 100:
            return 9.0, 4, None
        return 2.0 + ((pos - true_focus) / 1000.0) ** 2, 500, None

    monkeypatch.setattr(A, "sweep_metric", fake_metric)
    res = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                              steps_each_side=4, binning=2)
    assert res.success, res.message

    # Same points, same fitter, weights removed — the comparison IS the claim.
    xs = np.array([p for p, _ in res.points], dtype=np.float64)
    ys = np.array([h for _, h in res.points], dtype=np.float64)
    a, b, _c = np.polyfit(xs, ys, 2)
    unweighted = -b / (2 * a)
    assert abs(res.best_position - true_focus) < abs(unweighted - true_focus), \
        f"weighted {res.best_position} vs unweighted {unweighted:.0f}"
    assert abs(res.best_position - true_focus) <= 100, res.best_position


@native_only
async def test_a_sweep_that_wanders_out_of_its_window_is_stopped(monkeypatch):
    """A BOUNDED SWEEP MUST NOT BECOME AN UNBOUNDED WALK.

    2026-08-01, on the sky: a run asked for step 200 with 5 points each side —
    a window of 9500..11500 — and marched to 6900, still descending when a human
    halted it. Its size metric was inverted on that sparse field, so every step
    away from focus read "better" and the search believed it.

    Unattended that drives the drawtube at a mechanical stop the EAF's firmware
    cannot report (3.3.8 answers NOT_SUPPORTED to EAFGetErrorCode), so nothing
    downstream would have noticed either. Extending past the requested points to
    bracket a minimum is legitimate; leaving the neighbourhood is not.
    """
    _rig, cam, foc = await _connected_sim()
    start = await foc.get_position()

    # An engine that only ever wants to go further down, exactly as the real one
    # did when its metric inverted.
    class Runaway:
        def __init__(self, cfg, start_pos):
            self._p = start_pos

        def next(self):
            self._p -= 200
            return {"action": "move_to", "position": self._p}

        def add_measurement(self, *a, **k):
            return None

    monkeypatch.setattr(N._native, "FocusSweep", Runaway)
    monkeypatch.setattr(N._native, "detect_and_measure",
                        lambda data, params: ([], {"star_count": 40,
                                                   "hfr_median": 3.0,
                                                   "hfr_mad": 0.2}))

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=200, steps_each_side=5, binning=2)

    assert res.success is False
    # The leash is 2x the requested half-span (5 * 200 = 1000), so it may reach
    # start-2000 and no further.
    assert await foc.get_position() == start, \
        "a stopped sweep must put the focuser back where it started"
    assert "outside the window" in res.message, res.message
    assert str(start - 2 * 1000) in res.message or "9" in res.message, res.message
    assert res.advice and "smaller the further out" in res.advice, res.advice


async def test_a_perfectly_flat_curve_is_refused_on_the_measurement(monkeypatch):
    """The refusal must come from the SPREAD, not from a rounding sign.

    ``a <= 0`` alone rejects a flat curve only when the quadratic's leading
    coefficient happens to round negative — and on genuinely flat data that sign
    is floating-point noise. The same nine identical points failed on Windows
    and produced a confident best_hfr of 2.01, from points that were all exactly
    3.40, on CI's Linux/BLAS (2026-08-08). A curve that does not move has no
    minimum on any platform.

    Distinct from the test above, which asserts the ADVICE. This asserts the
    VERDICT, and that it is reached from evidence rather than from luck.
    """
    _rig, cam, foc = await _connected_sim()
    monkeypatch.setattr(A, "sweep_metric",
                        lambda data, min_stars=3: (3.4, 400, None))
    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)
    assert res.success is False, (
        f"a flat sweep produced a focus at {res.best_position} "
        f"(HFR {res.best_hfr}) from points that never moved")
    assert "flat" in (res.message or "").lower(), res.message


async def test_a_real_v_curve_is_not_caught_by_the_flat_gate(monkeypatch):
    """The positive control, and the reason the threshold is 5% and not 50%.

    Without it the gate could refuse everything and the test above would still
    pass.
    """
    _rig, cam, foc = await _connected_sim()
    start = await foc.get_position()

    def v(frame, min_stars=3):
        # A real V about the start position, read off the position THIS FRAME
        # was exposed at. A first draft guessed an attribute name, silently got
        # the constant `start` every time, and so fed the gate the very flat
        # curve it was meant to be the control for. Reading the live focuser
        # instead is the same failure one step subtler: the sweep exposes the
        # next point while this one is measured, so the rig's position belongs
        # to a different frame.
        return (2.0 + abs(frame.focuser_position - start) / 350.0, 400, None)

    monkeypatch.setattr(A, "sweep_metric", v)
    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)
    assert res.success, f"the flat gate refused a real V-curve: {res.message}"


# ------------------------------------------------- the flat-sweep decision itself
# Extracted and tested directly BECAUSE the end-to-end behaviour is platform
# dependent: on flat data the quadratic's leading coefficient rounds negative on
# one BLAS and positive on another, so a full-sweep test of this case passes for
# free on the machine where it already worked and can only fail on the one where
# it did not. Measured 2026-08-08: nine points all exactly 3.40 failed on Windows
# and produced a confident best_hfr of 2.01 on CI's Linux.

def test_a_curve_that_never_moves_is_flat():
    assert A.is_flat_sweep([3.4] * 9) is True


def test_a_curve_that_moves_by_noise_is_still_flat():
    assert A.is_flat_sweep([3.40, 3.41, 3.40, 3.42, 3.40]) is True


def test_a_real_v_is_not_flat():
    """The rig's own Oiii sweep, 2026-08-08."""
    assert A.is_flat_sweep(
        [19.78, 35.56, 23.96, 12.25, 1.67, 3.09, 10.93, 22.89, 34.72, 44.70]
    ) is False


def test_a_shallow_but_real_v_is_not_flat():
    """The threshold must not eat a genuine curve near good focus. 2.0 -> 2.4 is
    a 20% swing — shallow, and still twenty times the noise band above."""
    assert A.is_flat_sweep([2.4, 2.2, 2.0, 2.2, 2.4]) is False


def test_too_few_points_to_see_a_v_counts_as_flat():
    assert A.is_flat_sweep([]) is True
    assert A.is_flat_sweep([3.4]) is True


def test_a_tiny_absolute_swing_cannot_pass_on_a_tiny_minimum():
    """The absolute floor. Without it a sweep whose minimum HFR is near zero
    would make the relative threshold vanish and any wobble would read as a V."""
    assert A.is_flat_sweep([0.001, 0.002, 0.001]) is True
