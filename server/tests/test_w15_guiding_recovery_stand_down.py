# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What the pre-recovery sky read costs after the engine has stood down
(#704), and the step it copies at the start of a run (#711).

#704. WP-91 (#621) made `_maybe_recover_guiding` read the sky, one unsaved
frame of up to ``SKY_PRECHECK_MAX_S`` through two filter-wheel moves, before
it spends a recovery on a guider that may only have lost its star to cloud.
It sits BEFORE the attempts bound on purpose: with the attempts spent, the
bound's answer is the operator's ``guiding_action``, which under abort ends
the night, and cloud must not end a night over a guide star
(test_w14_sky_before_recovery.py::
test_a_cloudy_sky_holds_even_when_the_attempts_are_spent). But under
``guiding_action = warn`` the bound's answer is to stand down and keep
shooting unguided, the guider's ``is_active()`` stays False, and every frame
boundary past the 120 s probe gap reached the read again: with 180 s subs on a
clear sky, up to a minute of exposure and two wheel moves per frame for the
rest of the night, for a recovery that was never going to run.

THE FIX. The sky is read once on arriving at the spent bound, which is what
#621 pins, and not again for the same target in the same run while the bound
stays spent: the stand-down has no recovery left to protect. Keyed on the
run and the target, and forgotten when the attempts are not spent, so the
next spell, the next target and the next run each read once.

#711. ``_hold_step``, the step a cloud probe borrows its filter, gain and
binning from, was set per frame and never cleared, so a second run on the
same engine process started with the first run's last step, and the first
pre-recovery probe of the new run (or the cloud hold's) could copy an
exposure, gain or binning the new run never chose. It is cleared where a run
starts, beside ``_holding_for_clear``.

Both cases drive the real engine on a simulator hub whose ``mode`` is set to
"native" (a simulated frame carries no stars and always reads cloudy, which
is why the read refuses a simulator), through test_w14_sky_before_recovery.py's
rig: the camera's capture is scripted and the wheel is a recording double.

Named mutants, each applied from a byte backup, run, and restored
byte-identically (sha256 compared, the mutant text grepped gone).
"""
from __future__ import annotations

import asyncio
import time

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target

from test_w14_sky_before_recovery import (  # noqa: F401
    CLEAR, CLOUDY, _rig, _target, _warnings, sim_hub)

#: How many frame boundaries a stood-down night is driven through.
FRAMES = 10


def _a_frame_later(eng) -> None:
    """The next frame boundary of 180 s subs: the probe gap has run out, the
    engine's own stamp of its last reading is older than
    ``CLOUD_PROBE_EVERY_S``. Without this the gap, which is the older
    guard, would hide the one under test."""
    eng._sky_precheck_at = (time.monotonic() - engine_mod.CLOUD_PROBE_EVERY_S
                            - 1.0)


async def test_a_stood_down_run_reads_the_sky_once_not_at_every_frame(
        sim_hub, monkeypatch, bus_lines):
    """(#704) Both recovery attempts spent under ``guiding_action = warn``, the
    guider inactive, a clear sky, ten frame boundaries each past the probe
    gap: the sky is read once, on arriving at the spent bound, and the engine
    stands down every time without recovering.

    MUTANT "the stand-down skip removed" (the guard on the stood-down read
    removed from `_maybe_recover_guiding`, so the read is made at every
    boundary): RED (observed):
        AssertionError: a stood-down run read the sky at every frame
        boundary: 10 probes in 10 frames
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    for _ in range(FRAMES):
        _a_frame_later(eng)
        await eng._maybe_recover_guiding(t)

    # THE PREMISE IS THAT THE BOUND WAS REACHED AND HELD, not how often the
    # engine says so: the stand-down line is repeated at every boundary today
    # (a defect of its own, reported with this change), and a premise that
    # counted it would go red the day that is fixed, for the wrong reason.
    assert _warnings(bus_lines, "standing down from recovery"), (
        f"premise: the engine stood down: {rig.calls}")
    assert eng._guiding_recoveries == engine_mod._MAX_GUIDING_RECOVERIES, (
        "premise: the bound stayed spent through every boundary")
    assert "start" not in rig.calls and "center" not in rig.calls, (
        f"premise: nothing was recovered: {rig.calls}")
    assert len(rig.probes) == 1, (
        f"a stood-down run read the sky at every frame boundary: "
        f"{len(rig.probes)} probes in {FRAMES} frames")


async def test_a_night_that_spends_its_attempts_then_stands_down_reads_the_sky_to_the_bound_and_once_more(
        sim_hub, monkeypatch, bus_lines):
    """(#704) The whole night, from fresh. Each of the first two boundaries
    reads the sky and spends an attempt (the guide start does not take: the
    scripted guider stays inactive), the third arrives at the spent bound,
    reads once more and stands down, and the seven after it read nothing:
    3 probes in 10 frames, where the unguarded night read 10.

    MUTANT "the stand-down skip removed": RED (observed):
        AssertionError: the night read the sky 10 times in 10 frames, 2
        attempts and the bound's own read make 3
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    for _ in range(FRAMES):
        _a_frame_later(eng)
        await eng._maybe_recover_guiding(t)

    assert rig.calls.count("start") == engine_mod._MAX_GUIDING_RECOVERIES, (
        f"premise: both attempts were spent: {rig.calls}")
    assert _warnings(bus_lines, "standing down from recovery"), (
        f"premise: the rest of the night stood down: {rig.calls}")
    assert len(rig.probes) == engine_mod._MAX_GUIDING_RECOVERIES + 1, (
        f"the night read the sky {len(rig.probes)} times in {FRAMES} frames, "
        f"2 attempts and the bound's own read make "
        f"{engine_mod._MAX_GUIDING_RECOVERIES + 1}")


async def test_the_bound_reads_the_sky_for_the_next_target_too(
        sim_hub, monkeypatch):
    """(#704) The stand-down is for one target. The attempts are still spent
    when the next target loses its star (the count carries when a hop's own
    guider start did not work), and its arrival at the bound reads the sky as
    the first target's did, once: two targets, two reads, however many
    boundaries each has.

    MUTANT "the stand-down is for the night" (the key reduced to the run, the
    target dropped from it): RED (observed):
        AssertionError: the next target's arrival at the spent bound did not
        read the sky: 1 probe(s) after two targets
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    first = eng.plan.targets[0]
    second = _target(name="NGC 6946")
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    for who in (first, second):
        for _ in range(FRAMES):
            _a_frame_later(eng)
            await eng._maybe_recover_guiding(who)

    assert len(rig.probes) == 2, (
        f"the next target's arrival at the spent bound did not read the "
        f"sky: {len(rig.probes)} probe(s) after two targets")


async def test_a_cloudy_read_at_the_bound_is_not_a_stand_down(
        sim_hub, monkeypatch):
    """(#704) The read at the spent bound that says CLOUDY holds for clear sky
    and the engine has not stood down, so the next boundary at the bound reads
    the sky again: the skip is for a stand-down, not for a read.

    MUTANT "the read marks the target before it is made" (the target noted
    as read before `_sky_closed_before_recovery` is called, not after it
    answers False): RED (observed):
        AssertionError: a cloudy read at the bound stood the engine down: the
        next boundary read the sky 0 more time(s)
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY, CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    _a_frame_later(eng)
    await eng._maybe_recover_guiding(t)
    assert rig.calls == ["probe", "hold"], (
        f"premise: the first read said cloudy and held: {rig.calls}")
    _a_frame_later(eng)
    await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 2, (
        f"a cloudy read at the bound stood the engine down: the next "
        f"boundary read the sky {len(rig.probes) - 1} more time(s)")


async def test_a_new_spell_of_attempts_reads_the_sky_again(sim_hub, monkeypatch):
    """(#704) Guiding comes back and the attempts are cleared by a banked,
    guided frame; the next loss is a new spell, and when its attempts are
    spent in turn the bound reads the sky once more. The mark of the first
    spell is forgotten when the attempts are not spent.

    MUTANT "the mark is never forgotten" (the reset on a non-spent boundary
    removed): RED (observed):
        AssertionError: the second spell's bound did not read the sky: 2
        probe(s) over two spells
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    for _ in range(3):
        _a_frame_later(eng)
        await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 1, "premise: the first spell read the sky once"

    eng._guiding_recoveries = 0               # a banked, guided frame
    _a_frame_later(eng)
    await eng._maybe_recover_guiding(t)       # a loss: attempt 1 of 2
    assert eng._guiding_recoveries == 1, "premise: the new spell recovers"
    n = len(rig.probes)
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    for _ in range(3):
        _a_frame_later(eng)
        await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == n + 1, (
        f"the second spell's bound did not read the sky: "
        f"{len(rig.probes)} probe(s) over two spells")


async def test_a_new_run_reads_the_sky_at_the_spent_bound_again(
        sim_hub, monkeypatch):
    """(#704) The mark is the run's. ``_guiding_recoveries`` is not reset when
    a run starts (a hop's own guider start and a banked guided frame clear it),
    so a run on an engine that stood down in its last can begin with the bound
    still spent, and the target's identity is no proof of a different run (an
    object's id is reused). The key carries the run's start, so the new run's
    arrival at the bound reads the sky once, as the first did.

    Added by the WP-107 verifier: the key's run half was in the code and in
    this file's prose, and no case could tell it from a key of the target
    alone.

    MUTANT "the stand-down is for every run" (``self._started_at`` dropped from
    the key, ``here = (id(target),)``): RED (observed):
        AssertionError: the new run's arrival at the spent bound did not read
        the sky: 1 probe(s) over two runs
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    for _ in range(3):
        _a_frame_later(eng)
        await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 1, "premise: the first run read the sky once"

    eng._started_at += 1000.0                 # a new run, the bound still spent
    for _ in range(3):
        _a_frame_later(eng)
        await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 2, (
        f"the new run's arrival at the spent bound did not read the sky: "
        f"{len(rig.probes)} probe(s) over two runs")


async def test_a_second_runs_first_probe_does_not_copy_the_first_runs_step(
        sim_hub, monkeypatch):
    """(#711) A run on the sim shoots one frame and ends, leaving its step as
    ``_hold_step``. A second run on the same engine starts, and before its
    first frame sets a step of its own the pre-recovery read is asked for the
    sky: with nothing to copy it takes no frame, as a fresh process would,
    instead of shooting the first run's exposure, gain and binning.

    The read is asked right after `start`, before the new run's task has had
    a turn (a read with no step to copy returns without a suspension), so only
    the reset at the run's start can answer. The state is asserted after it,
    so the mutant fails on what it does and not on what it leaves.

    MUTANT "no reset" (``self._hold_step = None`` removed from the run's
    start): RED (observed):
        AssertionError: a second run's first read copied the first run's
        step: 1 frame(s) taken: [{'exp': 0.05, 'gain': 77, 'offset': 30,
        'binning': 1, 'slot': 0, 'save': False, 'target': 'NGC 7331',
        'frame_type': 'Light'}]
    """
    plan = SequencePlan(name="first", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=1, gain=77)])])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    deadline = asyncio.get_event_loop().time() + 30.0
    while eng.running and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert eng.state.get("state") == "complete", (
        f"premise: the first run ended complete: {eng.state}")
    assert eng._hold_step is not None and eng._hold_step.gain == 77, (
        "premise: the first run left its step behind")

    rig_engine, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    # The rig's recorders are installed on the hub; the engine under test is
    # the one that ran, with its own config and plan.
    del rig_engine
    eng.start(SequencePlan(name="second", guide=True, recover_guiding=True,
                           targets=[_target()]))
    try:
        taken = await eng._sky_closed_before_recovery(
            eng.plan.targets[0], why="guiding was lost")
        assert taken is False
        assert len(rig.probes) == 0, (
            f"a second run's first read copied the first run's step: "
            f"{len(rig.probes)} frame(s) taken: {rig.probes}")
        assert eng._hold_step is None, (
            "the new run started with the old run's step")
    finally:
        await eng.abort()
