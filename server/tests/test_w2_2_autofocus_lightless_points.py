# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.2 (#1108: #1077): the autofocus sweep judge stops blaming the focus for
what the sky took, and reports a minimum the sweep found as found.

THE NIGHTS IT COMES FROM. 2026-10-17: a cloud at 03:00 starved a sweep that was
sized from a measured defocus slope, the sweep came back ``not_enough_spread``,
and the judge discarded the slope: three wrong outcomes from one weather
event. 2026-10-17 21:17 and 2026-10-19: a default-span sweep measured a clean V
(10484:55.16/6 10834:26.91/8 11184:2.88/200 11534:32.34/5 11884:109.04/6) and
was reported as a failure because the outermost point could not be measured.

WHAT IT DOES NOW. Each starved point's frame is read through
``imaging.frame_class.classify``. A flat refusal (``not_enough_spread``,
``fit_unavailable``) with a point that read ``no_light`` or ``obstructed`` on
record keeps the measured slope, because the frame says the sky starved the
sweep, not the slope. And a sweep that cannot measure its next point but holds
a minimum its measured points bracket (two points on each side, each wing
rising away from the tip, the tip from a rich frame) reports that minimum as
found and records the slope the good points showed.

The sweep engine is scripted (``_ScriptedSweep``) so each case is the exact
sequence of points; the frames are the seeded synthetic ones of
``test_w2_frame_class.py``, handed back by the camera where the script says
the sky was dark.

MUTANT RUNS, 2026-11-04, from a byte backup of ``astrodeck/focus/native.py``,
restored and sha256-verified each time:

  * the flat-refusal gate ignoring the classes (``and sky:`` changed to
    ``and False:`` in the discard branch): the slope is discarded again and
    ``test_a_flat_refusal_with_a_no_light_point_keeps_the_slope`` fails with

        AssertionError: the slope was discarded although the sky starved the sweep

  * ``_accept_bracketed`` never finding a minimum (``found = None``):
    ``test_a_bracketed_minimum_is_found_when_the_next_point_cannot_be_measured``
    and ``test_two_no_light_points_among_good_ones_report_the_minimum_found``
    fail with

        AssertionError: the sweep could not measure 18150 on 3 tries in a row
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

import astrodeck.focus.native as N
from astrodeck import providers
from astrodeck.config import load_focus_calibration, save_focus_calibration
from astrodeck.devices.base import CameraFrame
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus.autofocus import resolve_sweep
from astrodeck.focus.span import FocusCalibration
from astrodeck.imaging.frame_class import FrameContext
from astrodeck.solve import light
from astrodeck.solve.light import Reference

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "frame_class"
if str(_FIXTURES) not in sys.path:
    sys.path.append(str(_FIXTURES))
import synth_frames as synth  # noqa: E402

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")

#: The no-light reference the synthetic black level stands for.
CONTEXT = FrameContext(reference=Reference(level=synth.BLACK_LEVEL,
                                           kind=light.DARK_MASTER))

START = 19200
STEP = 350


def _frame(data) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=4.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=-5.0,
                       timestamp=time.time())


class _ScriptedSweep:
    """The sweep engine as a fixed list of steps: each ``next()`` answers the
    next one, and the last answer repeats, as a real engine re-asks for a
    position it could not measure."""

    steps: list[dict] = []

    def __init__(self, config, start):
        self._steps = list(type(self).steps)

    def next(self):
        return self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]

    def add_measurement(self, *args, **kw):
        pass


def _script(monkeypatch, *positions: int, then: dict | None = None) -> None:
    steps = [{"action": "move_to", "position": p} for p in positions]
    if then is not None:
        steps.append(then)
    _ScriptedSweep.steps = steps
    monkeypatch.setattr(N._native, "FocusSweep", _ScriptedSweep)
    monkeypatch.setattr(
        N._native, "detect_and_measure",
        lambda data, params: ([], {"star_count": 100, "hfr_median": 3.0,
                                   "hfr_mad": 0.2}))


async def _rig():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return cam, foc


async def _wire(monkeypatch, cam, foc, frames: dict, hfr: dict) -> None:
    """At a position in ``frames`` the camera hands back that synthetic frame;
    at one in ``hfr`` the size metric measures ``hfr[position]`` from 100
    stars; at every other position it measures nothing (a starved point)."""
    seen = {"pos": None}
    real_expose = cam.expose

    async def expose(*args, **kw):
        seen["pos"] = await foc.get_position()
        if seen["pos"] in frames:
            return _frame(frames[seen["pos"]])
        return await real_expose(*args, **kw)

    cam.expose = expose

    def metric(data):
        h = hfr.get(seen["pos"])
        return (h, 100) if h is not None else (None, 0)

    monkeypatch.setattr(N, "native_sweep_metric", metric)


async def _rig_where(monkeypatch, frames: dict, hfr: dict):
    cam, foc = await _rig()
    await _wire(monkeypatch, cam, foc, frames, hfr)
    return cam, foc


def _plant_a_calibration() -> None:
    save_focus_calibration(
        None, FocusCalibration(5.0, 3.0, 1, 9, START, 1400, "2026-08-17"))


def _logs(q) -> list[str]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append(str(ev.data.get("message", "")))
    return out


async def _sweep(cam, foc, **kw):
    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(
            cam, foc, exposure_s=0.05, gain=200, steps_each_side=4,
            binning=1, **kw)
        return res, _logs(q)
    finally:
        bus.unsubscribe(q)


# ----------------------------------------------- the slope outlives a dark sky

@pytest.mark.parametrize("dark", [synth.no_light_frame, synth.obstructed_frame],
                         ids=["no_light", "obstructed"])
async def test_a_flat_refusal_with_a_no_light_point_keeps_the_slope(
        monkeypatch, dark):
    """A sweep sized from a measured slope starves on two points whose frames
    read ``no_light`` (or ``obstructed``), then the engine refuses it flat.
    The slope was not wrong, so it stays, and the log says why."""
    _plant_a_calibration()
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    p1, p2 = START + 2 * st, START + st
    await _wire(monkeypatch, cam, foc, {p1: dark(), p2: dark()}, {})
    _script(monkeypatch, p1, p2, then={"action": "failed",
                                       "reason": "not_enough_spread"})
    assert resolve_sweep(foc, None, 4).measured

    res, said = await _sweep(cam, foc, frame_context=CONTEXT)

    assert not res.success
    assert load_focus_calibration(None) is not None, (
        "the slope was discarded although the sky starved the sweep")
    assert any("keeping that calibration" in m for m in said), said
    assert not any("discarding that calibration" in m for m in said), said


async def test_a_flat_refusal_with_ordinary_starved_points_still_discards(
        monkeypatch):
    """The discard keeps its purpose: starved points whose frames are a clear
    star field (a span too wide for the field, not a dark sky) say nothing in
    the slope's favour, so a flat refusal still forgets it."""
    _plant_a_calibration()
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    p1, p2 = START + 2 * st, START + st
    field = synth.stars_frame()
    await _wire(monkeypatch, cam, foc, {p1: field, p2: field}, {})
    _script(monkeypatch, p1, p2, then={"action": "failed",
                                       "reason": "not_enough_spread"})

    res, said = await _sweep(cam, foc, frame_context=CONTEXT)

    assert not res.success
    assert load_focus_calibration(None) is None, "the bad span survived"
    assert any("discarding that calibration" in m for m in said), said


# ---------------------------------------------- a minimum found is reported found

#: The #1077 curve, at this rig's start position: five measured points around
#: a clean V, 350 steps apart, the tip from 200 stars.
V_1077 = {START - 2 * STEP: 55.16, START - STEP: 26.91, START: 2.88,
          START + STEP: 32.34, START + 2 * STEP: 109.04}


async def test_a_bracketed_minimum_is_found_when_the_next_point_cannot_be_measured(
        monkeypatch):
    """#1077, the 21:17 sweep: a clear minimum bracketed on both sides, and a
    sixth point the sky or the bloat made unmeasurable. The curve judge refuses
    that V (it is convex, so its scatter between neighbours is large against
    its depth); the sweep reports the minimum as found, and the slope the good
    points showed is recorded for the next sweep."""
    cam, foc = await _rig_where(monkeypatch, {}, V_1077)
    far = START - 3 * STEP
    _script(monkeypatch, *V_1077, far, far, far)
    assert load_focus_calibration(None) is None, "started with a stale file"

    res, said = await _sweep(cam, foc, step=STEP)

    assert res.success, res.message
    assert res.best_position is not None
    assert abs(res.best_position - START) <= STEP // 2, res.best_position
    assert res.best_hfr == pytest.approx(2.88)
    assert "bracket" in res.message, res.message
    assert load_focus_calibration(None) is not None, (
        "the slope the good points showed was not kept")
    assert await foc.get_position() == res.best_position


async def test_two_no_light_points_among_good_ones_report_the_minimum_found(
        monkeypatch):
    """The #1108 scenario: a good V, then two outer points whose frames read
    no light (a cloud over the last of the sweep). The sweep reports the
    minimum the good points found instead of failing the whole sweep."""
    p1, p2 = START - 3 * STEP, START + 3 * STEP
    dark = synth.no_light_frame()
    cam, foc = await _rig_where(monkeypatch, {p1: dark, p2: dark}, V_1077)
    _script(monkeypatch, *V_1077, p1, p2, p2, p2)

    res, _said = await _sweep(cam, foc, step=STEP, frame_context=CONTEXT)

    assert res.success, res.message
    assert abs(res.best_position - START) <= STEP // 2, res.best_position
    assert res.best_hfr == pytest.approx(2.88)


async def test_a_sweep_with_no_minimum_among_its_good_points_still_fails(
        monkeypatch):
    """Good points that never turn upward on one side are not a bracket: the
    run keeps failing as it did, and the focuser goes back to the start."""
    ramp = {START - 2 * STEP: 9.0, START - STEP: 8.0, START: 7.0,
            START + STEP: 6.0, START + 2 * STEP: 5.0}
    cam, foc = await _rig_where(monkeypatch, {}, ramp)
    far = START - 3 * STEP
    _script(monkeypatch, *ramp, far, far, far)

    res, _said = await _sweep(cam, foc, step=STEP)

    assert not res.success
    assert "could not measure" in res.message, res.message
    assert await foc.get_position() == START


# ------------------------------------------------------- the bracket rule itself

def _pts(heights: list[float], counts: list[int] | None = None):
    pos = [START + STEP * (i - len(heights) // 2) for i in range(len(heights))]
    pts = [(p, h, 0.1) for p, h in zip(pos, heights)]
    return pts, counts if counts is not None else [100] * len(heights)


def test_a_clean_v_is_bracketed_and_the_vertex_follows_the_tip():
    pts, counts = _pts([55.16, 26.91, 2.88, 32.34, 109.04], [6, 8, 200, 5, 6])
    best, hfr = N.bracketed_minimum(pts, counts)
    assert hfr == 2.88
    assert START - STEP < best < START + STEP


@pytest.mark.parametrize("heights, why", [
    ([26.91, 2.88, 32.34, 109.04], "one point on a side"),
    ([2.88, 10.0, 20.0, 40.0, 80.0], "the minimum is the end of the range"),
    ([30.0, 60.0, 2.88, 40.0, 70.0], "a wing turns back"),
    ([3.2, 3.1, 2.88, 3.0, 3.3], "wings too shallow to be a V"),
], ids=["short", "end", "turned", "shallow"])
def test_a_curve_that_does_not_bracket_a_minimum_is_not_accepted(heights, why):
    pts, counts = _pts(heights)
    assert N.bracketed_minimum(pts, counts) is None, why


def test_a_minimum_measured_from_a_thin_tip_is_not_accepted():
    """Under ``THIN_POINT_STARS`` the tip is one detection's opinion, as
    ``curve_verdict`` holds."""
    pts, counts = _pts([55.16, 26.91, 2.88, 32.34, 109.04],
                       [60, 60, 4, 60, 60])
    assert N.bracketed_minimum(pts, counts) is None
