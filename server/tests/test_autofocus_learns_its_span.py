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

    Steepened to 250 rather than to the rig's own rate: at the real thing's
    bloat the ±1400 sweep does not survive its own wings on a sim frame — the
    outer stars run off the sensor and the run dies `not_enough_spread`, which
    is precisely the failure this change exists to stop but is no use as a
    fixture for measuring the narrowing.

    250 rather than the 200 this used until 2026-09-08, one notch further in
    for a related reason. At 200 steps/px the NARROWED ±700 sweep bloats its own
    outer point by 3.5 px, where the sim's 1216x912 field resolves two sources —
    so that point was dropped, the run aborted, and the whole test rested on
    `curve_verdict` salvaging a tip the size metric measures NINE sources at. It
    did, because the per-point Rust pass counted fifteen detections on that same
    frame and `counts` took the larger. That pass is gone (27 s a point on the
    rig's 26 MP frames) and the count now comes from the metric that measures
    the size, so nine is under the tip gate. At 250 the narrow sweep measures
    every point it asks for, which is what "the narrowed sweep still works" was
    always meant to mean.
    """
    rig, cam, foc = await _connected_sim()
    rig.defocus_steps_per_px = 250.0
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
    await record_measured_span(
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
    await record_measured_span(foc, [("not", "numbers")], 19200, 1)  # no raise
    await record_measured_span(foc, [], 19200, 1)
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


# ------------------------------------------- it has to survive a restart (2026-09-07)
#
# THE SYMPTOM. On the night of 2026-09-06/07 a sweep logged "this focuser
# defocuses at 0.0749 px/step". After a server restart the next sweep logged
# "no completed sweep has measured this focuser's defocus slope yet". Only one
# of those can be true, and nothing in either line said which.
#
# The record IS file-backed -- config.focus_calibration_path(), keyed by the
# focuser's driver id -- so these grade the property end to end: what one
# process writes, a process that shares nothing but the filesystem must read.


def _sweep_points(best=19200):
    return [(best + d, 3.0 + abs(d) * 0.08)
            for d in (-1400, -700, -350, 0, 350, 700, 1400)]


async def test_the_measured_slope_survives_a_restart():
    """Written by one call, read back from the FILE with no shared state — the
    only thing a restart preserves. Reconstructed through
    ``FocusCalibration.from_json`` rather than through the loader, so this
    cannot pass on a value the loader was holding in memory."""
    import json

    import astrodeck.config as config_mod
    _rig, _cam, foc = await _connected_sim()
    assert load_focus_calibration(None) is None, "started with a stale file"

    await record_measured_span(foc, _sweep_points(), 19200, 1)

    path = config_mod.focus_calibration_path()
    assert path.exists(), f"nothing was written to {path}"
    raw = json.loads(path.read_text(encoding="utf-8"))
    key = config_mod._FOCUSER_DEFAULT_KEY
    assert key in raw, f"the record is filed under {list(raw)}, not {key}"
    stored = FocusCalibration.from_json(raw[key])
    assert stored is not None, f"the stored record does not parse back: {raw}"
    assert stored.slope_px_per_step > 0
    assert stored.n_points == 7
    assert stored.swept_half_span == 1400

    # And a restart's first sweep is sized from it, which is the whole point.
    g = resolve_sweep(foc, None, 4)
    assert g.measured, g.basis
    assert f"{stored.slope_px_per_step:.4f} px/step" in g.basis, g.basis


async def test_the_record_says_when_and_at_what_temperature():
    """A slope with no date and no temperature cannot answer "was this measured
    tonight, on this train". The sim focuser reports 4.2 C; a focuser that
    reports none stores None rather than a zero that reads as freezing."""
    import datetime as dt

    _rig, _cam, foc = await _connected_sim()
    await record_measured_span(foc, _sweep_points(), 19200, 1)
    cal = load_focus_calibration(None)
    assert cal is not None
    assert cal.measured_on == dt.date.today().isoformat()
    assert cal.temperature_c == pytest.approx(4.2), (
        "the focuser's temperature was not recorded with the slope")
    assert "4.2 C" in resolve_sweep(foc, None, 4).basis

    class _NoThermometer:
        _state_key = "chilly"

        async def get_temperature(self):
            return None

    await record_measured_span(_NoThermometer(), _sweep_points(), 19200, 1)
    silent = load_focus_calibration("chilly")
    assert silent is not None and silent.temperature_c is None, (
        "a focuser with no thermometer invented a temperature")


async def test_a_slope_that_could_not_be_stored_says_so():
    """``save_focus_calibration`` swallows every exception on purpose —
    bookkeeping must not fail a focus run — so a store it cannot write to used
    to be indistinguishable from one it wrote to, and the only evidence was a
    contradiction between two log lines hours apart. The write is verified
    where it happens now."""
    import astrodeck.config as config_mod
    _rig, _cam, foc = await _connected_sim()

    def _refuse(_path, _data):
        raise OSError("read-only file system")

    q = bus.subscribe()
    try:
        original = config_mod.write_json_atomic
        config_mod.write_json_atomic = _refuse
        try:
            await record_measured_span(foc, _sweep_points(), 19200, 1)
        finally:
            config_mod.write_json_atomic = original
        said = _logs(q)
    finally:
        bus.unsubscribe(q)

    assert load_focus_calibration(None) is None, "precondition: nothing stored"
    assert any("could not store it" in m for m in said), (
        f"a lost calibration was silent; the log said: {said}")
    assert any(str(config_mod.focus_calibration_path()) in m for m in said), (
        "the warning does not say WHERE it tried to write")


async def test_the_never_measured_line_says_where_it_looked():
    """The line the rig printed after the restart. It has to name the store and
    the key, or "it did not persist" is not a checkable claim."""
    import astrodeck.config as config_mod
    _rig, _cam, foc = await _connected_sim()
    g = resolve_sweep(foc, None, 4)
    assert not g.measured
    assert "no completed sweep" in g.basis
    assert str(config_mod.focus_calibration_path()) in g.basis, g.basis
    assert "key 'default'" in g.basis, g.basis
