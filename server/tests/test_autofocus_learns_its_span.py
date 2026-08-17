"""A sweep measures how wide the next one needs to be.

#219 closed leaving the span open: "narrowing it wants the focuser's critical
focus zone, which has never been measured". This is the loop that measures it —
every successful sweep records the defocus slope it just demonstrated, and the
next sweep is sized from it — and the guard rails that stop a bad measurement
wedging focus for a whole night.

The pure arithmetic is in `test_the_sweep_span_is_measured.py`. This file is
about the LIFECYCLE: what gets written, what gets read, and what gets thrown
away.
"""
import pytest

from astrodeck import providers
from astrodeck.config import load_focus_calibration
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus.autofocus import (record_measured_span, resolve_sweep)
from astrodeck.focus.span import DEFAULT_STEP, FocusCalibration

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


async def _connected_sim():
    parts = build_sim_rig()
    await parts["camera"].connect()
    await parts["focuser"].connect()
    return parts["_rig"], parts["camera"], parts["focuser"]


def _logs(q):
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append(str(ev.data.get("message", "")))
    return out


# ------------------------------------------------------------ what it learns

async def test_a_successful_sweep_measures_the_span_for_the_next_one():
    """END TO END, and the point of the whole change: run a sweep at the shipped
    ±1400, and the rig comes out of it knowing how wide it actually needs to
    be."""
    _rig, cam, foc = await _connected_sim()
    assert load_focus_calibration(None) is None, "started with a stale file"

    import astrodeck.focus.native as N
    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=1)
        said = _logs(q)
    finally:
        bus.unsubscribe(q)
    assert res.success, res.message

    cal = load_focus_calibration(None)
    assert cal is not None, "a successful sweep taught the rig nothing"
    assert cal.slope_px_per_step > 0
    assert cal.binning == 1
    assert cal.swept_half_span > 0, "no record of what range it covered"
    assert any("defocuses at" in m for m in said), (
        "the measurement has to reach the log — it is the number this project "
        "has never had")

    # And the NEXT sweep is sized from it rather than from the constant.
    g = resolve_sweep(foc, None, 4)
    assert g.measured
    assert "px/step" in g.basis
    assert g.step <= DEFAULT_STEP, "a measurement made the sweep WIDER"


async def test_a_fast_scope_gets_a_narrower_second_sweep():
    """THE PAYOFF, on a rig steep enough to need one.

    The sim's default optics are gentle — ±1400 steps only doubles the star, so
    the derivation asks for MORE and is held to what worked. A real f/5 train is
    nothing like that: 2.96 px at focus and 27 px at ±350 on 2026-08-17. Set the
    sim to that steepness and the loop does what it was built for: the first
    sweep measures the slope, the second is a fraction of the width, and it
    still finds focus.

    Steepened to 200 rather than to the rig's own rate: at the real thing's
    bloat the ±1400 sweep does not survive its own wings on a sim frame — the
    outer stars run off the sensor and the run dies `not_enough_spread`, which
    is precisely the failure this change exists to stop but is no use as a
    fixture for measuring the narrowing.
    """
    rig, cam, foc = await _connected_sim()
    rig.defocus_steps_per_px = 200.0
    rig.best_focus = await foc.get_position()

    import astrodeck.focus.native as N
    first = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                         step=350, steps_each_side=4, binning=1)
    assert first.success, first.message

    g = resolve_sweep(foc, None, 4)
    assert g.step * 4 <= 0.6 * 1400, f"no narrowing on a fast scope: {g.basis}"

    # And the narrow sweep still works — a span that fails is not a saving.
    second = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                          steps_each_side=4, binning=1)
    assert second.success, f"the narrowed sweep failed: {second.message}"
    assert abs(second.best_position - rig.best_focus) <= 200, second.best_position


async def test_a_failed_sweep_teaches_nothing():
    """A rejected curve describes something that is not a V. Learning a slope
    from one would make every future sweep the wrong width, from a run that
    already told us it could not see focus."""
    _rig, cam, foc = await _connected_sim()
    _rig.parked = True                    # no stars rendered → nothing measurable

    import astrodeck.focus.native as N
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1)
    assert not res.success
    assert load_focus_calibration(None) is None


async def test_a_span_the_operator_typed_is_used_verbatim():
    """The Focus screen's Advanced panel exists so someone can overrule us. A
    number they typed must never be quietly replaced by ours."""
    _rig, _cam, foc = await _connected_sim()
    record_measured_span(
        foc, [(19200 + d, 3.0 + abs(d) * 0.08) for d in
              (-1400, -700, -350, 0, 350, 700, 1400)], 19200, 1)
    assert load_focus_calibration(None) is not None

    g = resolve_sweep(foc, 999, 4)
    assert g.step == 999
    assert not g.measured, "an operator's number must not be labelled 'measured'"


# --------------------------------------------------------- what it throws away

class _FailingSweep:
    """An engine that refuses immediately with a given reason."""
    def __init__(self, reason):
        self._reason = reason
        self._asked = 0

    def next(self):
        self._asked += 1
        return {"action": "failed", "reason": self._reason}

    def add_measurement(self, *a, **kw):
        pass


def _refuse_with(monkeypatch, reason):
    import astrodeck.focus.native as native_mod
    monkeypatch.setattr(native_mod._native, "FocusSweep",
                        lambda config, start: _FailingSweep(reason))


def _plant_a_calibration(foc):
    """A slope so steep the derived sweep is at its floor — i.e. exactly the
    too-narrow calibration the discard rule exists for."""
    from astrodeck.config import save_focus_calibration
    save_focus_calibration(
        None, FocusCalibration(5.0, 3.0, 1, 9, 19200, 1400, "2026-08-17"))
    assert resolve_sweep(foc, None, 4).measured


@pytest.mark.parametrize("reason", ["not_enough_spread", "fit_unavailable"])
async def test_a_flat_refusal_discards_the_span_that_produced_it(monkeypatch,
                                                                 reason):
    """THE GUARD THAT MATTERS. A too-narrow sweep fails flat, and a flat failure
    on a span we chose would otherwise narrow every sweep of the night into the
    same failure — with the rig further from focus each time and nothing saying
    why. Forgetting sends the next attempt back to the shipped default, which is
    the geometry every successful focus run in this project's history used."""
    _rig, cam, foc = await _connected_sim()
    _plant_a_calibration(foc)
    _refuse_with(monkeypatch, reason)

    import astrodeck.focus.native as N
    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           steps_each_side=4, binning=1)
        said = _logs(q)
    finally:
        bus.unsubscribe(q)

    assert not res.success
    assert load_focus_calibration(None) is None, "the bad span survived"
    assert resolve_sweep(foc, None, 4).step == DEFAULT_STEP
    assert any("discarding that calibration" in m for m in said), said


async def test_a_fit_quality_refusal_keeps_it(monkeypatch):
    """`r_squared_below_threshold` is what a sweep that is too WIDE or
    mis-centred produces — this project has a whole `curve_verdict` written
    about how little it says. Throwing away a good measurement over it would
    trade one bug for another."""
    _rig, cam, foc = await _connected_sim()
    _plant_a_calibration(foc)
    _refuse_with(monkeypatch, "r_squared_below_threshold")

    import astrodeck.focus.native as N
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       steps_each_side=4, binning=1)
    assert not res.success
    assert load_focus_calibration(None) is not None


async def test_a_flat_refusal_on_a_typed_span_is_not_ours_to_forget(monkeypatch):
    """The calibration did not choose this sweep, so it is not what failed."""
    _rig, cam, foc = await _connected_sim()
    _plant_a_calibration(foc)
    _refuse_with(monkeypatch, "not_enough_spread")

    import astrodeck.focus.native as N
    await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=1)
    assert load_focus_calibration(None) is not None


# --------------------------------------------------------------- the plumbing

async def test_recording_never_costs_a_run_that_already_succeeded():
    """This runs AFTER the focus has been found. An unwritable config dir, a
    disk full, a record it cannot parse — none of that may turn a successful
    autofocus into a failed one."""
    _rig, _cam, foc = await _connected_sim()
    record_measured_span(foc, [("not", "numbers")], 19200, 1)   # must not raise
    record_measured_span(foc, [], 19200, 1)
    assert load_focus_calibration(None) is None


async def test_an_unreadable_calibration_reads_as_never_measured(monkeypatch):
    """A file a user can edit is a file that can be nonsense. The fallback is
    the shipped default, not a crash on the way to focusing."""
    import astrodeck.config as config_mod
    config_mod.focus_calibration_path().write_text("{ not json",
                                                   encoding="utf-8")
    _rig, _cam, foc = await _connected_sim()
    g = resolve_sweep(foc, None, 4)
    assert g.step == DEFAULT_STEP
    assert not g.measured
