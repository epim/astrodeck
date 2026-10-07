# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``_hold_step`` is SEEDED from the plan's first light step at a run's start,
and never carried from a previous run (#711; orchestrator ruling at the wave 15
integration of WP-107).

``_hold_step`` is the step a cloud probe borrows its filter, gain and binning
from. #711: it was set per frame and never cleared, so a run on an engine that
had run before probed through the PREVIOUS run's exposure. WP-107 cleared it at
the run's start, and that left a cloud hold entered BEFORE the first frame (the
sky is cloudy at dusk and the first target is still being set up) with nothing to
probe with: ``_cloud_probe`` returned None, and a hold that cannot see the sky
clear runs blind to its bound. The ruling: seed it at the start from the plan's
first light exposure step, the step the first frame is about to shoot; None only
when the plan has no light step at all.

Both halves, and the shape of the seed:

* NO CARRY-OVER: a second run on the same engine starts with ITS plan's step.
* A HOLD BEFORE THE FIRST FRAME PROBES WITH THE SEEDED STEP: the read taken
  right after ``start`` copies the plan's first light step's gain, offset and
  binning, and its exposure up to ``SKY_PRECHECK_MAX_S``.
* THE SEED IS A LIGHT STEP: a calibration target ahead of the science target,
  or a dark among a target's steps, is skipped (a probe through one would score
  a blackout as cloud), and a plan with no light step seeds None.

The engine is real, on a simulator hub whose ``mode`` is set to "native" (a
simulated frame carries no stars and always reads cloudy, which is why the read
refuses a simulator), through test_w14_sky_before_recovery.py's rig: the
camera's capture is scripted. The read is asked right after ``start``, before
the run's task has had a turn.

Named mutants, each run from a byte backup of ``sequence/engine.py`` and
restored byte-identically (sha256 compared, the mutant text grepped absent);
the case that failed and its first assertion, verbatim:

* "carry the old step" (``self._hold_step = self._first_light_step(plan)``
  removed from the run's start) ->
  ``test_a_second_run_starts_with_its_own_first_light_step``,
  ``AssertionError: the second run started with the first run's step (gain
  77), not its own``.
* "seed None" (the line made ``self._hold_step = None``, WP-107's reset) ->
  ``test_a_hold_before_the_first_frame_probes_with_the_seeded_step``,
  ``AssertionError: the run started with no step, so a hold before its first
  frame would probe blind: None`` (and the second-run case, ``the second run
  started with no step at all``).
* "seed the first step of any target" (``_first_light_step`` skipping neither
  calibration targets nor non-light steps) ->
  ``test_the_seed_is_a_light_step_on_a_science_target``,
  ``AssertionError: a probe through a dark would score a blackout as cloud:
  Dark``.
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target

from test_w14_sky_before_recovery import (  # noqa: F401  (fixtures and rig)
    CLEAR, _rig, _target, sim_hub)


def _plan(name: str, *targets: Target, **kw) -> SequencePlan:
    return SequencePlan(name=name, guide=kw.pop("guide", True),
                        recover_guiding=True, dither_every=0, autofocus_every=0,
                        meridian_flip=False, targets=list(targets), **kw)


def _science(name="NGC 7331", **step) -> Target:
    base = dict(filter="Ha", exposure_s=45.0, count=3, gain=91, offset=17,
                binning=3)
    base.update(step)
    return Target(name=name, ra_hours=22.6182, dec_deg=34.4098, center=True,
                  autofocus_first=False, steps=[ExposureStep(**base)])


async def _a_finished_run(eng, hub) -> None:
    """One frame on the sim, so the engine ends a run with a step of its own
    left behind in ``_hold_step`` (gain 77)."""
    eng.start(SequencePlan(name="first", guide=False, dither_every=0,
                           autofocus_every=0, meridian_flip=False,
                           targets=[Target(
                               name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                               center=False, autofocus_first=False,
                               steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                   count=1, gain=77)])]))
    deadline = asyncio.get_event_loop().time() + 30.0
    while eng.running and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert eng.state.get("state") == "complete", (
        f"premise: the first run ended complete: {eng.state}")
    assert eng._hold_step is not None and eng._hold_step.gain == 77, (
        "premise: the first run left its step behind")


async def test_a_second_run_starts_with_its_own_first_light_step(
        sim_hub, monkeypatch):
    """NO CARRY-OVER, asserted on the state `start` leaves, before the new
    run's task has had a turn."""
    eng = SequenceEngine(sim_hub)
    await _a_finished_run(eng, sim_hub)
    _rig(sim_hub, monkeypatch, [CLEAR])
    second = _plan("second", _science())

    eng.start(second)
    try:
        own = second.targets[0].steps[0]
        step = eng._hold_step
        assert step is not None, "the second run started with no step at all"
        assert step.gain != 77, (
            "the second run started with the first run's step (gain 77), "
            "not its own")
        assert step.id == own.id and step.gain == 91, (
            f"the second run did not start with its plan's first light step: "
            f"{step}")
    finally:
        await eng.abort()


async def test_a_hold_before_the_first_frame_probes_with_the_seeded_step(
        sim_hub, monkeypatch):
    """A FRESH engine, a first run, and a read asked for the sky before any
    frame has set a step: it probes through the plan's first light step, which
    is what the first frame will use, instead of taking nothing."""
    eng = SequenceEngine(sim_hub)
    _rig_engine, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    del _rig_engine
    plan = _plan("tonight", _science())

    eng.start(plan)
    try:
        assert eng._hold_step is not None, (
            f"the run started with no step, so a hold before its first frame "
            f"would probe blind: {eng._hold_step}")
        taken = await eng._sky_closed_before_recovery(
            plan.targets[0], why="guiding was lost")
        assert taken is False, "premise: a clear reading is not a closed sky"
        assert len(rig.probes) == 1, (
            f"a read before the first frame took no frame: {rig.probes}")
        probe = rig.probes[0]
        assert (probe["gain"], probe["offset"], probe["binning"]) == (91, 17, 3), (
            f"the probe did not copy the seeded step's gain, offset and "
            f"binning: {probe}")
        assert probe["exp"] == min(45.0, engine_mod.SKY_PRECHECK_MAX_S), probe
    finally:
        await eng.abort()


async def test_the_seed_is_a_light_step_on_a_science_target(sim_hub):
    """The seed skips a calibration target ahead of the science, and a dark
    among a target's steps, and the order of the plan decides which light step
    comes first."""
    dark_first = Target(
        name="Darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
        steps=[ExposureStep(exposure_s=60.0, count=5, frame_type="Dark",
                            gain=10)])
    mixed = Target(
        name="Mixed", ra_hours=1.0, dec_deg=10.0, center=False,
        autofocus_first=False,
        steps=[ExposureStep(exposure_s=30.0, count=5, frame_type="Dark",
                            gain=20),
               ExposureStep(filter="R", exposure_s=45.0, count=5, gain=33),
               ExposureStep(filter="G", exposure_s=45.0, count=5, gain=44)])
    later = _science("Later", gain=55)
    plan = _plan("p", dark_first, mixed, later)

    seed = SequenceEngine._first_light_step(plan)

    assert seed is not None, "premise: the plan has light steps"
    assert seed.frame_type == "Light", (
        f"a probe through a dark would score a blackout as cloud: "
        f"{seed.frame_type}")
    assert seed.id == mixed.steps[1].id and seed.gain == 33, (
        f"the seed is not the first light step of the first science target: "
        f"{seed}")


async def test_a_plan_with_no_light_step_seeds_none(sim_hub):
    """None only when there is no light step at all: calibration targets only,
    darks only, and an empty plan, which is also what a plan of no targets is."""
    only_cal = _plan("c", Target(
        name="Flats", ra_hours=0.0, dec_deg=0.0, calibration=True,
        steps=[ExposureStep(exposure_s=1.0, count=5, frame_type="Flat")]))
    only_darks = _plan("d", Target(
        name="Darks", ra_hours=1.0, dec_deg=1.0, center=False,
        steps=[ExposureStep(exposure_s=60.0, count=5, frame_type="Dark")]))
    empty = _plan("e")

    for what, plan in (("calibration targets only", only_cal),
                       ("darks only", only_darks), ("no targets", empty)):
        assert SequenceEngine._first_light_step(plan) is None, (
            f"{what}: a plan with no light step was seeded")
    # And a plan object that is not shaped like one is left alone, not raised on.
    assert SequenceEngine._first_light_step(object()) is None
