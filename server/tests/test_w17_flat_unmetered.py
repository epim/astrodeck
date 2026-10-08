# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A flat whose exposure would not meter is not shot (#841; wave 17
integration).

THE FINDING (WP-134's). ``_run_calibration`` solved a Flat step's exposure with
``_solve_flat_exposure`` and threw the answer's second half away:
``solved_exp, _ = await ...``. The solver gives up at a rail (the source is too
dim at the longest exposure, too bright at the shortest) or after its iteration
cap, and the exposure it hands back is then only the last number it tried. The
whole set was shot at it anyway, so a panel that did not light, a cover left
open or a sky past its window banked ``count`` frames that no ADU target
describes, each of which looks like a flat to the matcher.

THE RULE, as this file holds it. For a Flat step with ``adu_target > 0``:

* the OPENING solve did not converge: NONE of that step's frames are shot, the
  solver's reason is said once (a warning naming the filter), the lamp is off
  and the cover shut as after any other way out of the step, and the NEXT step
  (the next filter) goes on;
* it converged: nothing changes (the whole set is shot at the solved exposure);
* a mid-set RE-solve that does not converge changes nothing either: the set is
  already under way and goes on at the best estimate, with the line the solver
  used to print, said by the caller now.

The DUSK FLATS stage counts a set the exposure would not meter apart from a set
shot, so its closing line does not claim flats that do not exist.

HARNESS: a real engine over the simulator (``ASTRODECK_FAST_TEST`` collapses the
exposure dwell). ``flat_illumination`` is 0 until the sim panel is lit, so a
step that never lights it meters a source that does not brighten with exposure:
"too_dim_at_max". A lit panel converges, which is the control.

NAMED MUTANTS (each run from a byte backup inside this worktree, restored
byte-identically with sha256 compared); the observed failure is quoted in the
test that catches it.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import FLAT_RESOLVE_EVERY, SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(filters=("L", "R"), *, adu=8000, count=3, brightness=None):
    return SequencePlan(
        name="Flats", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(
            name="flats", ra_hours=0.0, dec_deg=0.0, calibration=True,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100, offset=30,
                                count=count, frame_type="Flat", adu_target=adu,
                                panel_brightness=brightness)
                   for f in filters])])


class Tap:
    """Every camera call the engine makes: the frame type, whether it is
    SAVED (a trial metering capture is not), and the filter in the beam."""

    def __init__(self, hub, monkeypatch):
        self.calls: list[tuple[str, bool, str]] = []
        real = hub.capture

        async def capture(exposure_s, gain, offset, binning, **kw):
            fw = hub.devices["filterwheel"]
            name = fw.filter_names[await fw.get_position()]
            self.calls.append((kw.get("frame_type", "Light"),
                               bool(kw.get("save", True)), name))
            return await real(exposure_s, gain, offset, binning, **kw)
        monkeypatch.setattr(hub, "capture", capture)

    def saved_flats(self) -> list[str]:
        return [f for t, saved, f in self.calls if t == "Flat" and saved]

    def trials(self) -> list[str]:
        return [f for t, saved, f in self.calls if t == "Flat" and not saved]


async def _run(hub, plan) -> SequenceEngine:
    eng = SequenceEngine(hub)
    eng.start(plan)
    await eng._task
    assert eng._task.exception() is None, eng._task.exception()
    return eng


def _said(bus_lines, fragment):
    return [m for lvl, m, _src in bus_lines
            if lvl == "warning" and fragment in m]


async def test_a_step_that_will_not_meter_shoots_none_of_its_frames(
        sim_hub, monkeypatch, bus_lines):
    """Panel never lit, so the source does not brighten with exposure: the
    solver ends at the rail ("too_dim_at_max"). Nothing is saved for EITHER
    filter, each filter says why once, and the second filter was still
    attempted (its trial captures are there).

    MUTANT "an unconverged step still shoots" (``engine._run_calibration``:
    the ``continue`` after the not-converged warning made ``pass``) turned
    this red (4 failed, 2 passed), observed:

        AssertionError: a flat that no ADU target describes was banked:
        ['L', 'L', 'L', 'R', 'R', 'R']
        assert ['L', 'L', 'L', 'R', 'R', 'R'] == []

    MUTANT "no reason in the line" (the ``({self._flat_solve_reason}) - none
    of its`` fragment made ``- none of its``) turned this and the next case
    red (2 failed, 4 passed), observed:

        AssertionError: flats: flat exposure did not converge on L - none of
        its 3 flats are shot, and the next step goes on
        assert '(too_dim_at_max)' in 'flats: flat exposure did not converge
        on L - none of its 3 flats are shot, and the next step goes on'
    """
    tap = Tap(sim_hub, monkeypatch)
    await _run(sim_hub, _plan())
    assert tap.saved_flats() == [], (
        "a flat that no ADU target describes was banked: "
        f"{tap.saved_flats()}")
    assert "L" in tap.trials() and "R" in tap.trials(), (
        f"the next filter was not attempted: {tap.trials()}")
    for f in ("L", "R"):
        lines = _said(bus_lines, f"did not converge on {f} ")
        assert len(lines) == 1, (f, lines, "said once, naming the filter")
        assert "(too_dim_at_max)" in lines[0], lines[0]
        assert "none of its 3 flats are shot" in lines[0], lines[0]
    assert not _said(bus_lines, "using "), (
        "the line that promised the unconverged exposure is back")


async def test_the_reason_is_the_solvers_own_word(sim_hub, monkeypatch,
                                                  bus_lines):
    """The line carries the solver's reason, not a fixed one: a source that is
    too BRIGHT at the shortest exposure says so.

    MUTANT "no reason in the line" (above) turned this red as well, observed:

        AssertionError: flats: flat exposure did not converge on L - none of
        its 3 flats are shot, and the next step goes on
        assert '(too_bright_at_min)' in 'flats: flat exposure did not converge
        on L - none of its 3 flats are shot, and the next step goes on'
    """
    # A saturating source at any exposure: the sim reads back full scale.
    real = sim_hub.devices["camera"].expose

    async def bright(seconds, gain, offset, binning=1, light=True, **kw):
        frame = await real(seconds, gain, offset, binning, light, **kw)
        frame.data[:] = 65535
        return frame
    monkeypatch.setattr(sim_hub.devices["camera"], "expose", bright)
    tap = Tap(sim_hub, monkeypatch)
    await _run(sim_hub, _plan(filters=("L",)))
    assert tap.saved_flats() == []
    (line,) = _said(bus_lines, "did not converge on L ")
    assert "(too_bright_at_min)" in line, line


async def test_a_step_that_meters_is_shot_in_full(sim_hub, monkeypatch,
                                                  bus_lines):
    """THE CONTROL: with the sim panel lit the source brightens with
    exposure, the solver converges, and every frame of every step is shot, as
    before. If the harness could not reach a converged solve, the red cases
    above would pass for nothing.

    MUTANT "a converged step is skipped too" (the ``if not converged:`` guard
    made ``if True:``) turned this and the re-solve case red (2 failed, 4
    passed), observed:

        AssertionError: []
        assert [] == ['L', 'L', 'L', 'R', 'R', 'R']
    """
    tap = Tap(sim_hub, monkeypatch)
    await _run(sim_hub, _plan(brightness=100))
    assert tap.saved_flats() == ["L", "L", "L", "R", "R", "R"], tap.saved_flats()
    assert not _said(bus_lines, "did not converge"), bus_lines


async def test_the_lamp_is_off_after_a_step_that_would_not_meter(
        sim_hub, monkeypatch):
    """The panel is lit for the metering and the step is left by ``continue``:
    the ``finally`` still turns it off, so a flat set that would not meter does
    not leave the lamp burning for the lights that follow. The lamp here is
    lit but so weak that the solver still ends at the rail."""
    from astrodeck.devices.sim import SimCoverCalibrator
    monkeypatch.setattr(SimCoverCalibrator, "ADU_PER_BRIGHTNESS", 1e-6)
    tap = Tap(sim_hub, monkeypatch)
    await _run(sim_hub, _plan(filters=("L",), brightness=100))
    assert tap.saved_flats() == []
    assert (await sim_hub.calibrator_status())["state"] == "off", (
        "the lamp was left lit after a step that would not meter")


async def test_a_resolve_that_does_not_converge_goes_on_at_the_best_estimate(
        sim_hub, monkeypatch, bus_lines):
    """A set already under way is not abandoned: the OPENING solve converged,
    so these are flats, drifting; a mid-set re-solve that cannot converge
    keeps the best estimate, says so, and the set is shot in full.

    MUTANT "the re-solve's line is dropped" (the ``if not resolved:`` block in
    ``_run_calibration`` made ``if False:``) turned this red (1 failed, 5
    passed), observed:

        AssertionError: []
        assert [] == ['flats: flat exposure did not converge
        (max_iterations); using 0.5s']
    """
    eng = SequenceEngine(sim_hub)
    calls: list[float | None] = []

    async def scripted(step, target, start_exposure_s=None):
        calls.append(start_exposure_s)
        if start_exposure_s is None:               # the opening solve
            eng._flat_solve_reason = "converged"
            return 0.4, True
        eng._flat_solve_reason = "max_iterations"  # a re-solve, past the cap
        return 0.5, False

    monkeypatch.setattr(eng, "_solve_flat_exposure", scripted)
    tap = Tap(sim_hub, monkeypatch)
    n = FLAT_RESOLVE_EVERY + 2
    eng.start(_plan(filters=("L",), count=n))
    await eng._task
    assert len(calls) >= 2 and calls[0] is None and calls[1] is not None, calls
    assert tap.saved_flats() == ["L"] * n, (
        f"a set under way was abandoned: {tap.saved_flats()}")
    assert _said(bus_lines, "did not converge (max_iterations); using 0.5s") == [
        "flats: flat exposure did not converge (max_iterations); using 0.5s"], (
        [m for lvl, m, _s in bus_lines if "converge" in m])


async def test_the_dusk_flats_stage_does_not_claim_a_set_it_did_not_shoot(
        sim_hub, tmp_path, monkeypatch, bus_lines):
    """The DUSK FLATS stage runs each filter's set through ``_run_calibration``;
    a set the exposure would not meter is counted apart, so the closing line is
    true. A weak panel here (lit, the source too dim at the longest exposure).

    MUTANT "every set is counted shot" (the stage's ``if self._done.get(...)
    > 0:`` made ``if True:``) turned this red (1 failed, 5 passed), observed:

        AssertionError: ['DUSK FLATS: 2 set(s) shot, 0 skipped (the library
        had them)']
        assert ['DUSK FLATS:...ry had them)'] == ['DUSK FLATS:...d not meter)']

    and "an unconverged step still shoots" (above) turns it red too.
    """
    from astrodeck.devices.sim import SimCoverCalibrator
    from astrodeck.sequence.models import DuskFlatsPlan
    monkeypatch.setattr(SimCoverCalibrator, "ADU_PER_BRIGHTNESS", 1e-6)
    plan = SequencePlan(
        name="Flats night", guide=False, dither_every=0,
        targets=[Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100,
                                            offset=30, count=1)
                               for f in ("L", "R")])],
        dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                 adu_target=8000, count=3))
    eng = SequenceEngine(sim_hub)
    monkeypatch.setattr(eng, "_run_scheduled", _no_lights)
    tap = Tap(sim_hub, monkeypatch)
    eng.start(plan)
    await eng._task
    assert tap.saved_flats() == [], tap.saved_flats()
    closing = [m for lvl, m, _s in bus_lines if m.startswith("DUSK FLATS:")
               and "set(s) shot" in m]
    assert closing == ["DUSK FLATS: 0 set(s) shot, 0 skipped (the library had "
                       "them), 2 not shot (the exposure would not meter)"], closing


async def _no_lights(plan):
    return None
