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
