# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A declined re-sweep probe pays for nothing but the probe (WP-143, #722).

WHAT IT COST. ``run_native_autofocus`` asks the engine for its first move and
starts the first point's move and exposure BEFORE the probe frame is measured,
so the probe's full-frame star count (27 to 67 s on the rig) overlaps them. When
the probe was then declined (#558's gate, or the hopeless-field refusal) the
speculation was already in flight: ``_settle()`` waited for that exposure to
finish and the focuser was driven back, so every declined probe cost one more
exposure and two more focuser moves than the probe itself. The owed re-sweep
asks again every ``SPARSE_RESWEEP_EVERY_S`` for as long as the sky stays thin,
so the cost is paid on every one of those asks, and only the last of them ever
sweeps.

THE FIX. A sweep that was handed a gate (``min_probe_stars`` not None) does not
speculate until the probe has passed it. The overlap is given up for the gated
sweep that DOES go on, once, and kept for every sweep that has no gate (the
initial autofocus and every plan refocus), where a decline cannot happen. A
declined probe then made no move, so there is nothing to put back and nothing
to wait for. The alternative, cancelling the speculation on a decline, was
rejected: ``_settle(cancel=True)`` interrupts an exposure in flight and the
move may already have happened, so it pays less but never nothing, and it
cannot be tested as "no move".

Named mutants, each run from a byte backup of ``focus/native.py`` inside this
worktree and restored byte-identically (sha256 compared, the mutant text grepped
absent). The first failing assertion is quoted verbatim, with the case that
raised it.

* "gated sweeps speculate too" (``gated = min_probe_stars is not None`` made
  ``gated = False``, so every sweep starts its first point under the probe as
  before, and the gate is still applied) ->
  ``test_a_probe_under_the_gate_costs_one_exposure_and_no_moves[14]`` (and [8],
  [2], and the clipped-frame case), ``AssertionError: a probe of 14 stars under
  the gate cost 2 exposures and 3 focuser move(s); it should cost one exposure
  and no moves``.
* "the restore is unconditional" (the gate branch's ``if speculated:`` made
  ``if True:``) ->
  ``test_a_probe_under_the_gate_costs_one_exposure_and_no_moves[14]`` (and [8],
  [2]), ``AssertionError: a probe of 14 stars under the gate cost 1 exposures
  and 1 focuser move(s); it should cost one exposure and no moves``.
* "the speculation starts before the refusal" (the gated sweep's first-point
  start moved from after the hopeless-field refusal to before it) ->
  ``test_a_refused_gated_probe_on_a_clipped_frame_pays_nothing``,
  ``AssertionError: a clipped probe of 3 stars refused under a gate cost 2
  exposures and 3 focuser move(s); it should cost one exposure and no moves``.
* "an ungated sweep waits for its probe" (``gated = min_probe_stars is not
  None`` made ``gated = True``, so every sweep starts its first point only
  after the probe has passed the gate) ->
  ``test_a_sweep_with_no_gate_still_overlaps_the_probe_measurement``,
  ``AssertionError: the first point's exposure had not begun while the probe
  was being measured: an ungated sweep lost its overlap``.

A first version of the first mutant disabled only the pre-probe test and left
the post-gate start in place, so the first point was started TWICE (two tasks
driving one focuser) and the suite hung. ``gated = False`` is the mutant that
does not.
"""
from __future__ import annotations

import threading

import pytest

import astrodeck.focus.native as N
from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus.native import SPARSE_FIELD_WARN

native_only = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


async def _counted_sim():
    """The sim camera and focuser, each counting what it is asked to do: every
    ``move_to`` (target recorded, in order) and every ``expose``."""
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    moves: list[int] = []
    exposures: list[float] = []
    real_move, real_expose = foc.move_to, cam.expose

    async def move_to(position):
        moves.append(int(position))
        return await real_move(position)

    async def expose(*a, **kw):
        exposures.append(float(a[0]))
        return await real_expose(*a, **kw)

    foc.move_to = move_to
    cam.expose = expose
    return cam, foc, moves, exposures


def _probe_then_points(probe_stars: int, point_stars: int = 1, *,
                       on_probe=None):
    """The Rust detector, scripted: ``probe_stars`` on the first call (the probe
    at the start position), ``point_stars`` after it. ``on_probe`` runs inside
    the probe's measurement, on the worker thread, before it answers."""
    seen = {"calls": 0}

    def detector(data, params):
        seen["calls"] += 1
        if seen["calls"] == 1:
            if on_probe is not None:
                on_probe()
            return [], {"star_count": probe_stars, "hfr_median": 3.4,
                        "hfr_mad": 0.30}
        return [], {"star_count": point_stars, "hfr_median": 3.4,
                    "hfr_mad": 0.30}
    return detector


@native_only
@pytest.mark.parametrize("probe", [SPARSE_FIELD_WARN - 1, 8, 2])
async def test_a_probe_under_the_gate_costs_one_exposure_and_no_moves(
        monkeypatch, probe):
    """The owed re-sweep's probe counts fewer stars than the line: declined. The
    probe is the ONLY exposure the sweep made and the focuser was never moved,
    not to the first point and not back."""
    cam, foc, moves, exposures = await _counted_sim()
    start = await foc.get_position()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(probe))
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 1))

    res = await N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
        binning=2, min_probe_stars=SPARSE_FIELD_WARN)

    assert res.gated is True and res.success is False, (
        f"premise: the probe of {probe} stars was declined: {res.message!r}")
    assert len(exposures) == 1 and not moves, (
        f"a probe of {probe} stars under the gate cost {len(exposures)} "
        f"exposures and {len(moves)} focuser move(s); it should cost one "
        f"exposure and no moves")
    assert await foc.get_position() == start


@native_only
async def test_a_refused_gated_probe_on_a_clipped_frame_pays_nothing(
        monkeypatch):
    """A clipped probe is never GATED (its few stars are merged, not missing),
    so a gated sweep reaches the hopeless-field refusal instead. That refusal is
    the other place the speculation used to be thrown away. It must be free too."""
    cam, foc, moves, exposures = await _counted_sim()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(3))
    monkeypatch.setattr(N, "saturation_fraction", lambda data: 0.5)

    res = await N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
        binning=2, min_probe_stars=SPARSE_FIELD_WARN)

    assert res.success is False and res.gated is False and (
        "overexposed" in res.message), (
        f"premise: the clipped probe was refused, not gated: {res.message!r}")
    assert len(exposures) == 1 and not moves, (
        f"a clipped probe of 3 stars refused under a gate cost "
        f"{len(exposures)} exposures and {len(moves)} focuser move(s); it "
        f"should cost one exposure and no moves")


@native_only
async def test_a_sweep_with_no_gate_still_overlaps_the_probe_measurement(
        monkeypatch):
    """CONTROL. The initial autofocus and every plan refocus pass no gate: a
    decline cannot happen to them, and the overlap is worth about ten seconds on
    a rich field. The first point's exposure has begun while the probe is still
    being measured."""
    cam, foc, moves, exposures = await _counted_sim()
    second_exposure = threading.Event()
    counted_expose = cam.expose

    async def expose(*a, **kw):
        # ``exposures`` is appended by the counting wrapper underneath, so at
        # the probe's own exposure it is still empty and the event stays down.
        if exposures:
            second_exposure.set()
        return await counted_expose(*a, **kw)
    cam.expose = expose
    overlapped = {}

    def on_probe():
        overlapped["began"] = second_exposure.wait(10.0)

    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(20, on_probe=on_probe))
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 1))

    await N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
        binning=2)

    assert overlapped.get("began") is True, (
        "the first point's exposure had not begun while the probe was being "
        "measured: an ungated sweep lost its overlap")


async def _whole_sweep(monkeypatch, min_probe_stars):
    """One complete sweep over the same scripted field; what it did to the
    focuser and how many frames it took."""
    cam, foc, moves, exposures = await _counted_sim()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(SPARSE_FIELD_WARN + 5))
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 1))
    res = await N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
        binning=2, min_probe_stars=min_probe_stars)
    return res, moves, exposures


@native_only
async def test_a_probe_that_clears_the_gate_sweeps_exactly_as_an_ungated_one(
        monkeypatch):
    """CONTROL. A gated probe that clears the line goes on to sweep, and the
    sweep is the one an ungated run makes: the same focuser moves in the same
    order and the same number of frames. Starting the first point after the
    probe instead of under it gives up the overlap and nothing else (the loop
    still claims the speculative exposure for the position the engine asks for,
    so no frame is lost or shot twice)."""
    gated, gated_moves, gated_exposures = await _whole_sweep(
        monkeypatch, SPARSE_FIELD_WARN)
    ungated, ungated_moves, ungated_exposures = await _whole_sweep(
        monkeypatch, None)

    assert gated.gated is False and len(gated_exposures) > 1, (
        f"a probe over the line did not sweep: gated={gated.gated}, "
        f"{len(gated_exposures)} exposure(s)")
    assert gated_moves == ungated_moves, (gated_moves, ungated_moves)
    assert len(gated_exposures) == len(ungated_exposures), (
        len(gated_exposures), len(ungated_exposures))
