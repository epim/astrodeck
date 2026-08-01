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
    real_detect = N._native.detect_and_measure
    real_sweep = N._native.FocusSweep
    POISON_HFR = 0.4242          # unmistakable: no real sweep point looks like this
    calls = {"n": 0}

    def detector(data, params):
        calls["n"] += 1
        if calls["n"] == 5:      # mid-sweep, well past the probe
            return [], {"star_count": 2, "hfr_median": POISON_HFR, "hfr_mad": 0.0}
        return real_detect(data, params)

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

    monkeypatch.setattr(N._native, "detect_and_measure", detector)
    monkeypatch.setattr(N._native, "FocusSweep", SpySweep)

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
    real_detect = N._native.detect_and_measure
    calls = {"n": 0}

    def detector(data, params):
        calls["n"] += 1
        if calls["n"] == 5:
            return [], {"star_count": 50, "hfr_median": None, "hfr_mad": 0.0}
        return real_detect(data, params)

    monkeypatch.setattr(N._native, "detect_and_measure", detector)
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

    def mixed(data, min_stars=3):
        pos = rig.focuser_pos
        return (9.0, 4, None) if abs(pos - 18500) < 100 else \
            (2.0 + ((pos - 19200) / 1000.0) ** 2, 500, None)

    monkeypatch.setattr(A, "sweep_metric", mixed)
    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)
    assert res.success, res.message
    assert res.advice and "1 of 9 fitted points" in res.advice, res.advice
    assert "richest of 500" in res.advice, res.advice
    assert "the fit discounts them" in res.advice, res.advice


async def test_legacy_fit_lets_the_richest_frames_decide(monkeypatch):
    """A four-star point cannot drag the vertex the way a five-hundred-star one
    can. Before weighting, every point voted alike, so a single thin sample near
    the minimum could move the answer hundreds of steps."""
    rig, cam, foc = await _connected_sim()
    true_focus = 19200          # the sim starts here; keep the vertex in the window
    outlier_at = 18500

    def fake_metric(data, min_stars=3):
        # A textbook V, except at one position where four stars report nonsense.
        pos = rig.focuser_pos
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
