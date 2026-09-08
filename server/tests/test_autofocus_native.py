"""Native (Rust engine) autofocus, end-to-end against the simulator.

The sim camera renders focus-dependent star sharpness (``sim.py`` ``_render_stars``:
star σ grows with ``|focuser_pos - best_focus|``), so the native sweep — move
focuser → expose → ``astrodeck_native.detect_and_measure`` → ``FocusSweep`` fit —
is genuinely exercisable end-to-end with no hardware and no sim changes.

The whole module is skipped when the Rust wheel is absent, so the suite stays
green without ``maturin develop``.
"""
import pytest

from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus import run_autofocus
from astrodeck.focus.autofocus import MAX_DROPS_PER_POSITION
from astrodeck.focus.native import run_native_autofocus

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


class _StubHub:
    """Minimal hub for provider resolution: just a role→device registry."""
    def __init__(self, **devices):
        self.devices = devices


async def _connected_sim():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return parts["_rig"], cam, foc


def _drain_focus(q):
    """All ``focus`` events currently queued, oldest first."""
    events = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "focus":
            events.append(ev.data)
    return events


async def test_native_autofocus_converges():
    """Native AF finds the sim's true focus and parks the focuser there,
    publishing running→done with an additive fit carrying a non-empty curve."""
    rig, cam, foc = await _connected_sim()
    q = bus.subscribe()
    try:
        result = await run_native_autofocus(
            cam, foc, exposure_s=0.05, gain=200, step=350,
            steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)

    assert result.success, result.message
    # The sweep brackets best_focus (20000) within start±4·350; the fit recovers
    # it to well within one step of the true optimum.
    assert abs(result.best_position - rig.best_focus) <= 400, result.best_position
    assert await foc.get_position() == result.best_position

    events = _drain_focus(q)
    states = [e.get("state") for e in events]
    assert states[0] == "running"
    assert states[-1] == "done"
    done = events[-1]
    assert done["best"]["position"] == result.best_position
    # Additive fit object: method + non-empty rendered curve for the UI.
    fit = done.get("fit")
    assert fit is not None, "done event must carry the additive fit object"
    assert fit.get("method")
    assert isinstance(fit.get("curve"), list) and len(fit["curve"]) > 0
    # At least one running measurement was published before done.
    assert any(s == "running" and e.get("points") for s, e in zip(states, events))


async def test_native_autofocus_restores_start_on_failure():
    """A starless field (parked mount → no stars rendered) yields no measurable
    points, so the sweep fails; the focuser must return to where it started and
    the UI must see a failed event."""
    rig, cam, foc = await _connected_sim()
    rig.parked = True  # _render_stars only fires when the mount is unparked
    start = await foc.get_position()

    q = bus.subscribe()
    try:
        result = await run_native_autofocus(
            cam, foc, exposure_s=0.05, gain=200, step=350,
            steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)

    assert not result.success
    assert await foc.get_position() == start, "focuser not restored to start"
    states = [e.get("state") for e in _drain_focus(q)]
    assert states[-1] == "failed"


async def test_provider_routes_astrodeck_for_sim_rig():
    """A native (sim) rig has no backend autofocus, so the provider layer resolves
    autofocus to the AstroDeck native engine."""
    _rig, cam, foc = await _connected_sim()
    hub = _StubHub(camera=cam, focuser=foc)
    choice = providers.resolve("autofocus", hub)
    assert choice.kind == "astrodeck", choice
    assert "native" in choice.label.lower()


async def test_run_autofocus_routes_through_provider():
    """``run_autofocus`` given a hub routes to the native engine for the sim rig
    (same convergence as calling it directly), proving the call-site wiring."""
    rig, cam, foc = await _connected_sim()
    hub = _StubHub(camera=cam, focuser=foc)
    result = await run_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350,
        steps_each_side=4, binning=1, hub=hub)
    assert result.success, result.message
    assert abs(result.best_position - rig.best_focus) <= 400, result.best_position


# ---------------------------------------------------------------- sparse fields

async def test_a_field_too_sparse_to_fit_is_refused_before_the_sweep(monkeypatch):
    """Below four stars a curve cannot be fitted, and defocusing only ever finds
    FEWER — so sweeping is five minutes of moving the focuser to reach a failure
    that was knowable from the very first frame. Refuse, name what to change,
    and leave the focuser where it started.

    Reproduces the 2026-07-30 session: a rig whose detector found 8 stars at
    best focus and 0 a few thousand steps out, which spent forty minutes
    reporting "only 0 stars" at every position."""
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    # The engine's detector, stubbed to report a field too sparse to fit.
    monkeypatch.setattr(N._native, "detect_and_measure",
                        lambda data, params: ([], {"star_count": 2,
                                                   "hfr_median": 3.0,
                                                   "hfr_mad": 0.1}))
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       binning=2)
    assert res.success is False
    # message = the diagnosis, advice = the fix. Both reach the panel — the
    # message as the verdict chip, the advice as the detail line — so between
    # them they must carry two facts and not one fact twice. They used to open
    # with the same star count and the same "defocusing finds fewer", spending
    # both lines saying it.
    assert "2 stars" in res.message
    assert res.advice and "2 stars" not in res.advice, res.advice
    assert "defocus" not in res.advice, res.advice
    # the levers, in the order that helps: exposure is free, binning costs
    # resolution the sweep does not need
    assert "longer exposure" in res.advice and "bin 1" in res.advice
    # and it never moved
    assert await foc.get_position() == start


async def test_a_merely_sparse_field_still_sweeps(monkeypatch):
    """Doubtful is not hopeless. A synthetic field of 11 stars converges
    perfectly well, so warning is right and refusing would be worse than the
    failure it prevents — the user can always halt."""
    rig, cam, foc = await _connected_sim()
    q = bus.subscribe()
    try:
        res = await run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                         step=350, steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)
    assert res.success, res.message


# ------------------------------------------------- the rejected fit's own data

def _drain_logs(q):
    """All ``log`` events currently queued, oldest first."""
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append(ev.data)
    return out


def test_vcurve_report_carries_every_step_and_the_flat_tip():
    """``not_enough_spread`` is a one-word verdict on a curve nobody can see.
    The report has to carry the series it was measured from AND the statistic
    that rejected it — here the engine's 0.1 flat-tip band, which is what
    "no spread" actually means (trendline.rs ``fit_star_hfr``)."""
    from astrodeck.focus.native import vcurve_report
    points = [(9585, 3.55, 0.05), (9760, 3.57, 0.05), (9935, 3.56, 0.05),
              (10110, 3.58, 0.05), (10285, 3.60, 0.05)]
    counts = [42, 51, 60, 55, 47]
    assert len(points) == len(counts)          # precondition: parallel series

    r = vcurve_report(points, counts)

    for (p, h, _s), n in zip(points, counts):
        assert f"{p}:{h:.2f}/{n}" in r, r      # every step, positions included
    assert "span 0.05" in r, r                 # the whole curve is 0.05 tall
    assert "min at 9585" in r, r
    # ... which is inside the 0.1 band, so BOTH trendlines are starved. That is
    # the failure, stated in the engine's own terms.
    assert "flat tip" in r and "5 of 5" in r, r
    assert "0 left / 0 right" in r, r


def test_vcurve_report_quotes_the_r_squared_gate_it_failed():
    """``r_squared_below_threshold`` without the R² is unfalsifiable. Quote
    every fit's number and the gate they were measured against."""
    from astrodeck.focus.native import (R_SQUARED_THRESHOLD, CURVE_FITTING,
                                        vcurve_report)
    # Deliberately not a V: a rising line with a kink, which fits nothing well.
    points = [(9000, 4.0, 0.1), (9350, 4.9, 0.1), (9700, 4.2, 0.1),
              (10050, 5.6, 0.1), (10400, 4.4, 0.1), (10750, 6.1, 0.1)]
    r = vcurve_report(points, [30] * len(points))

    assert "R²" in r, r
    assert CURVE_FITTING in r, r
    assert f"{R_SQUARED_THRESHOLD:.2f}" in r, r
    # a real number for the gated fit, not a placeholder
    import re
    m = re.search(rf"{CURVE_FITTING} (\d\.\d+)", r)
    assert m, r
    assert 0.0 <= float(m.group(1)) <= 1.0, r


def test_vcurve_report_never_replaces_the_failure_it_describes(monkeypatch):
    """This only ever runs while recording a failure that already happened, so
    a fit that will not compute must degrade to a note, never to an exception."""
    import astrodeck.focus.native as N

    def _boom(*a, **kw):
        raise ValueError("not enough points")
    monkeypatch.setattr(N._native, "fit_focus_curve", _boom)

    r = N.vcurve_report([(9000, 4.0, 0.1)], [12])
    assert "9000:4.00/12" in r, r
    assert "R² unavailable" in r and "not enough points" in r, r
    # and the empty case is a sentence, not an IndexError
    assert N.vcurve_report([], []) == "V-curve: no measurable points"


async def test_a_flat_sweep_logs_the_series_that_failed(monkeypatch):
    """END TO END, the thing the rig could not do: a sweep that fails the fit
    must leave its own per-step data in the log.

    Twelve of thirteen autofocus attempts on the real rig failed with a
    one-word reason and "V-curve" appeared ZERO times in the entire log
    history, so every diagnosis needed a repeat run on the sky to observe."""
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()
    # A metric that reads the same at every position: no spread, by
    # construction, which is the failure family being instrumented.
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 50))

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=1)
        logs = _drain_logs(q)
    finally:
        bus.unsubscribe(q)

    assert res.success is False
    assert res.message == "not_enough_spread", res.message
    vlines = [l for l in logs if l["message"].startswith("V-curve")]
    assert len(vlines) == 1, [l["message"] for l in logs]
    line = vlines[0]
    assert line["level"] == "warning" and line["source"] == "focus"
    # every position it measured, with its size and star count
    assert res.points, "precondition: the sweep measured nothing"
    for pos, _hfr in res.points:
        assert f"{pos}:5.00/50" in line["message"], line["message"]
    assert "flat tip" in line["message"], line["message"]


async def test_a_runaway_sweep_publishes_points_the_chart_can_read(monkeypatch):
    """The leash abort published raw ``(position, hfr, sigma)`` TUPLES while
    every other exit publishes ``{position, hfr, sigma}`` dicts, and the UI's
    VCurve reads ``p.position``/``p.hfr`` off them
    (ui/src/components/graphs.tsx) — so the one failure whose entire story is
    the shape of the curve was the one that drew an empty chart."""
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()

    class _RunawaySweep:
        """One honest point, then a step far outside the requested window."""
        def __init__(self, config, start_position):
            self.start = int(start_position)
            self.n = 0

        def next(self):
            self.n += 1
            if self.n == 1:
                return {"action": "move_to", "position": self.start}
            return {"action": "move_to", "position": self.start - 1_000_000}

        def add_measurement(self, *a, **kw):
            pass

    monkeypatch.setattr(N._native, "FocusSweep", _RunawaySweep)

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=1)
        events = _drain_focus(q)
    finally:
        bus.unsubscribe(q)

    assert res.success is False
    assert "outside the window" in res.message, res.message
    assert res.points, "precondition: nothing measured, so nothing to publish"
    # the result's contract: (position, hfr), same as every other exit
    assert all(len(p) == 2 for p in res.points), res.points
    failed = [e for e in events if e.get("state") == "failed"]
    assert failed, events
    pts = failed[-1]["points"]
    assert pts, "precondition: the failed event published no points"
    assert all(isinstance(p, dict) and "position" in p and "hfr" in p
               for p in pts), pts


# ----------------------------------------------- the unmeasurable-point stall
# Measured on the rig 2026-08-08 during a per-filter offset run: the SII slot
# reached position 9077, could not measure it (frame median 237, max ~310 — a
# narrowband frame with almost no signal), and re-exposed THAT ONE POSITION
# indefinitely. Fourteen consecutive "dropping 9077" lines, the focuser
# stationary, the run unable to finish or fail, and no route that could cancel
# it.
#
# The sweep engine advances only when a measurement is ADDED, and the driver
# loop is a bare `while True`, so a point that cannot be measured is asked for
# again forever. The engine reaches that state after some points HAVE been
# measured (bracketing a minimum demands a specific position); a sweep whose
# frames are all bad from the start instead fails cleanly on spread — which is
# why a "make every frame starless" test passes for the WRONG REASON and proves
# nothing about this guard. These pin the engine to one position on purpose.


class _StuckSweep:
    """A sweep engine that keeps asking for the SAME position, like the rig's.

    Wraps the real engine so everything else in the driver loop is genuine, and
    simply never lets it move on past `stick_at`.
    """

    def __init__(self, inner, stick_at: int):
        self._inner = inner
        self._stick_at = stick_at
        self.asks = 0

    def next(self):
        self.asks += 1
        return {"action": "move_to", "position": self._stick_at}

    def add_measurement(self, *a, **kw):
        return self._inner.add_measurement(*a, **kw)


def _stick_the_sweep(monkeypatch, stick_at: int):
    """Point the driver at a sweep that will not advance off `stick_at`."""
    import astrodeck.focus.native as native_mod
    real_cls = native_mod._native.FocusSweep
    made = {}

    def factory(config, start_pos):
        made["sweep"] = _StuckSweep(real_cls(config, start_pos), stick_at)
        return made["sweep"]

    monkeypatch.setattr(native_mod._native, "FocusSweep", factory)
    return made


def _starless_once_sweeping(cam, made):
    """Flat noise, but ONLY after the probe frame.

    Gated rather than applied to every frame, and that gate is the point: a
    pre-flight probe runs first and refuses the whole run when the START
    position has no stars ("only 0 stars at the current focus"). Blanking that
    frame too makes every test here pass on the probe's refusal instead of the
    stall guard — which is what the first draft did, green, proving nothing.

    THE GATE IS "the first exposure of the run", not "the sweep has been asked
    for a position". Since 2026-09-08 the loop asks the engine for its first
    move BEFORE the probe, so the probe's measurement can overlap that move and
    exposure — which means ``sweep.asks`` is already 1 while the probe is being
    taken, and the old gate blanked the probe itself.

    Flat noise rather than zeros: a live sensor on a field with nothing bright
    enough to size, which is what the rig had, not a dead camera.
    """
    import numpy as np
    real = cam.expose
    state = {"n": 0, "exposures": 0}
    rng = np.random.default_rng(3)

    async def fake(*a, **kw):
        frame = await real(*a, **kw)
        state["exposures"] += 1
        sweep = made.get("sweep")
        if sweep is None or state["exposures"] == 1:
            return frame
        state["n"] += 1
        data = np.asarray(frame.data)
        frame.data = rng.integers(230, 250, size=data.shape).astype(data.dtype)
        return frame

    cam.expose = fake
    return state


async def test_a_position_the_sweep_will_not_leave_fails_instead_of_spinning(
        monkeypatch):
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    made = _stick_the_sweep(monkeypatch, start + 700)
    seen = _starless_once_sweeping(cam, made)

    result = await run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350,
        steps_each_side=4, binning=1)

    assert not result.success
    # The bound IS the behaviour: without it this call never returns.
    #
    # MAX_DROPS_PER_POSITION frames at the stuck position, plus AT MOST ONE
    # more: the sweep exposes the next point speculatively while measuring this
    # one, and a sweep that will not advance is exactly the case where that
    # guess is wrong. `focus.pipeline` stops speculating after the first miss —
    # permanently, for the rest of the run — so the overshoot is one frame and
    # cannot grow with the number of retries. If this ever needs raising again,
    # the one-strike rule has stopped working.
    assert seen["n"] <= MAX_DROPS_PER_POSITION + 1, (
        f"the sweep exposed {seen['n']} times at one position — it is still "
        f"retrying a point it cannot measure")


async def test_the_stall_refusal_names_the_position_and_a_lever(monkeypatch):
    """A refusal at 2am has to say WHERE it stuck and WHAT to change."""
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    made = _stick_the_sweep(monkeypatch, start + 700)
    _starless_once_sweeping(cam, made)

    result = await run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350,
        steps_each_side=4, binning=1)

    assert not result.success
    blob = f"{result.message} {result.advice or ''}"
    assert "could not measure" in blob, blob
    assert str(start + 700) in blob, blob
    assert "exposure" in blob or "gain" in blob, blob


async def test_a_stalled_sweep_puts_the_focuser_back(monkeypatch):
    """It must not leave the drawtube parked mid-sweep — the next run and any
    manual recovery both start from wherever this left it."""
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    made = _stick_the_sweep(monkeypatch, start + 700)
    _starless_once_sweeping(cam, made)

    result = await run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350,
        steps_each_side=4, binning=1)

    assert not result.success
    assert abs(await foc.get_position() - start) <= 1, (
        "a stalled sweep left the focuser away from where it started")


async def test_a_single_bad_frame_does_not_fail_the_sweep():
    """The positive control. One satellite, gust or cloud on one point must
    still ride through, or the bound would break every honest sweep and the
    tests above would still pass."""
    import numpy as np
    rig, cam, foc = await _connected_sim()
    real = cam.expose
    state = {"n": 0, "blanked": 0}
    rng = np.random.default_rng(5)

    async def fake(*a, **kw):
        frame = await real(*a, **kw)
        state["n"] += 1
        if state["n"] == 3:            # exactly one unusable frame
            state["blanked"] += 1
            data = np.asarray(frame.data)
            frame.data = rng.integers(230, 250,
                                      size=data.shape).astype(data.dtype)
        return frame

    cam.expose = fake
    result = await run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350,
        steps_each_side=4, binning=1)

    assert state["blanked"] == 1, "the bad frame never happened"
    assert result.success, f"one bad frame failed the whole sweep: {result.message}"


# ---------------------------------------------------------------------------
# #143 — the fit gate throws away curves a human would accept.
# The pure criterion is proved in tests/test_focus_curve_acceptance.py; these
# two prove the WIRING: that a sweep the engine refuses on its fit statistic
# still ends with the focuser at the fitted optimum, and that a sweep with
# nothing in it still fails.

def _saturating_v(centre, *, tip=1.67, sat=44.70, step=350, k=1.8):
    """The 2026-08-08 Oiii shape as a function of focuser position.

    A V whose wings saturate once the defocused blobs outgrow what the size
    metric can measure — which is what makes both ends of a sweep read the same
    number, as that run's did (1.67 px at the tip, 44.70 px at both ends). The
    engine refuses this curve with ``r_squared_below_threshold`` while its own
    fit puts the minimum exactly on the measured one.
    """
    def size_at(pos):
        return min(sat, tip + abs(pos - centre) * k * (sat - tip) / (4 * step))
    return size_at


async def test_a_curve_the_fit_gate_refuses_is_accepted_on_its_shape(monkeypatch):
    """END TO END: the rig's Oiii sweep, and what should have happened to it.

    Clean V, minimum bracketed, 300 stars at the tip, wings rising to 44.70 on
    both sides — and `r_squared_below_threshold`, because R² scores how well
    the model tracks wings that are 26x taller than the tip. The run must
    finish AT the focus it found, not restored to where it started."""
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    size_at = _saturating_v(start)
    # The position THIS FRAME was exposed at, not the rig's live one: the sweep
    # exposes the next point while this one is being measured, so the focuser
    # has already moved on by the time the metric runs.
    monkeypatch.setattr(N, "native_sweep_metric",
                        lambda frame: (size_at(frame.focuser_position), 300))

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=1)
        events = _drain_focus(q)
    finally:
        bus.unsubscribe(q)

    assert res.success, res.message
    # The verdict has to name what overruled the gate, or a salvaged run is
    # indistinguishable in the record from one that fitted cleanly.
    assert "curve shape" in res.message, res.message
    assert "r_squared_below_threshold" in res.message, res.message
    assert abs(res.best_position - start) <= 350, res.best_position
    # The FOCUSER, not just the number: a run that reports success and leaves
    # the drawtube back at its starting point has shot the rest of the night
    # out of focus.
    assert abs(await foc.get_position() - res.best_position) <= 1
    assert events[-1]["state"] == "done", events[-1]
    assert events[-1]["best"]["position"] == res.best_position


async def test_a_sweep_with_no_curve_in_it_still_fails(monkeypatch):
    """The control for the test above. Accepting on shape must not become
    accepting on nothing: a flat sweep has no interior minimum and no wings,
    and it has to keep failing — with the engine's own reason, and with the
    focuser put back."""
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 300))

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=1)
        logs = _drain_logs(q)
    finally:
        bus.unsubscribe(q)

    assert res.success is False
    assert res.message == "not_enough_spread", res.message
    assert abs(await foc.get_position() - start) <= 1
    # The curve's own second opinion is on the record beside the engine's.
    agrees = [l for l in logs if l["message"].startswith("curve check agrees")]
    assert len(agrees) == 1, [l["message"] for l in logs]
    assert "end of the swept range" in agrees[0]["message"], agrees[0]


# ----------------------------------------------- running out of measurable range
#
# NGC 5907, 2026-08-08 22:04, on the rig: ten points, a textbook symmetric V,
# HFR 1.90 from 460 STARS at 11173, both wings rising to 55.91 and 65.97,
# hyperbolic R-squared 0.996 against a 0.70 gate. The whole run was discarded
# because the ELEVENTH position, five half-steps out, showed 2 detectable stars
# where a fit point needs 3. The stars there are so bloated that FINDING them is
# what fails -- a fact about the end of the sweep, not about the focus.
#
# `curve_verdict` was written for exactly this and was only reachable from the
# engine's own `failed` path, so the drop-abort returned before anything asked
# the curve. These use the REAL engine, blanking only the outermost position, so
# a genuine curve exists by the time the abort fires -- the distinction the
# `_StuckSweep` tests above cannot make, because a stuck sweep never measures
# anything to salvage.


def _blank_beyond(cam, foc, limit: int):
    """Flat noise once the sweep reaches past `limit`; everything nearer measures.

    Keyed on a THRESHOLD rather than an exact position because the engine picks
    its own positions and does not sweep the symmetric range the caller asks
    for: with step 350 and steps_each_side 4 the sim visits start-700 through
    start+2100. An exact-position fixture silently blanked nothing, and the
    `seen["n"] > 0` assertion below is what caught that rather than the test
    passing for the wrong reason.
    """
    import numpy as np
    real = cam.expose
    rng = np.random.default_rng(11)
    seen = {"n": 0}

    async def fake(*a, **kw):
        frame = await real(*a, **kw)
        if await foc.get_position() >= limit:
            seen["n"] += 1
            data = np.asarray(frame.data)
            frame.data = rng.integers(230, 250, size=data.shape).astype(data.dtype)
        return frame

    cam.expose = fake
    return seen


async def test_a_curve_that_ran_out_of_range_is_accepted_not_discarded(monkeypatch):
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    step, side = 350, 4
    # The far end of what this sweep reaches, so the curve is complete before
    # the unmeasurable point arrives.
    seen = _blank_beyond(cam, foc, start + step * 6)

    # BIN 2, because the salvage this models needs a TIP worth salvaging. The
    # NGC 5907 run it comes from measured 460 stars at its best point and 2 at
    # the eleventh, and `curve_verdict` refuses a tip under THIN_POINT_STARS
    # for exactly that reason. The sim's bin-1 frame is a 1216x912 postage
    # stamp whose best point resolves nine sources — itself a thin tip, so it
    # fails the gate on its own merits. At bin 2 the same field resolves 21-28
    # and the fixture is the shape it claims to be.
    #
    # (It passed at bin 1 until 2026-09-08 only because the per-point Rust
    # `detect_and_measure` counted 11-15 detections on that frame where
    # `star_size` resolves nine. That pass is gone — it cost 27 s a point on
    # the rig — and the count now comes from the metric that measures the size.
    # On a real 26 MP field the fine path answers with hundreds either way.)
    result = await run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=step,
        steps_each_side=side, binning=2)

    assert seen["n"] > 0, (
        "the sweep never reached the blanked position, so this test proves "
        "nothing about the drop-abort — pick an endpoint the sweep visits")
    assert result.success, (
        f"a sweep with a measured V was thrown away because one endpoint could "
        f"not be measured: {result.message}")
    assert result.best_position is not None
    # It must land on the curve's TIP, not on the position it gave up at and
    # not back at the start. Anchored to the measured minimum rather than to
    # `start`, because the sim's focus is not at the start position and an
    # assertion that assumed it was failed on a correct answer.
    tip = min(result.points, key=lambda p: p[1])[0]
    assert abs(result.best_position - tip) <= step, (
        f"salvaged to {result.best_position}, but the measured minimum is at "
        f"{tip}")
    assert result.best_position < start + step * 6, (
        "the vertex landed at the end the sweep could not measure")


async def test_the_range_refusal_does_not_blame_a_field_that_is_rich(monkeypatch):
    """When the salvage cannot save it, the advice still has to be true.

    A rich field that merely over-swept must not be told to expose longer for a
    narrowband filter — that is the #114 wrong turn, and it was the copy this
    exact run produced.
    """
    from astrodeck.focus.autofocus import RICH_FIELD_STARS
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    made = _stick_the_sweep(monkeypatch, start + 700)
    _starless_once_sweeping(cam, made)

    result = await run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350,
        steps_each_side=4, binning=1)

    assert not result.success
    rich = max((s for _p, _h, s in (result.points or [])), default=0)
    if rich >= RICH_FIELD_STARS:
        assert "narrowband" not in (result.advice or "").lower(), (
            f"the field's best point held {rich} stars and the advice still "
            f"blames the filter: {result.advice!r}")
        assert "steps_each_side" in (result.advice or ""), result.advice
