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
    assert "2 stars" in res.message
    # the levers, in the order that helps: exposure is free, binning costs
    # resolution the sweep does not need
    assert "longer exposure" in res.message and "bin 1" in res.message
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
