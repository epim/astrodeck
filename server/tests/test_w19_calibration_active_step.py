# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A calibration frame the plan does not hold publishes no plan step (#842).

THE CLASS: a placeholder that is also a real value. ``_run_calibration(ti,
target)`` is handed the plan index of ``target``, and for the targets the
engine builds for itself (the day darks, the DUSK FLATS sets, the cloud-hold
darks) there is no such index, so its callers pass 0, or the held target's
index. ``_begin_frame`` then published ``_active_step = (ti, si)``, which
indexes ``plan.targets[ti].steps[si]``: the plan's first light step (or the
held target's first step), while the frame exposing was a flat or a dark.

The one consumer of ``_active_step`` is the ETA's off-by-one guard
(`_remaining_capture_s`): "for the step in flight, only the frames AFTER the
current one are owed", so it took a frame off a light step that had not been
shot, and the finish clock the status publishes under ``progress`` was one
exposure short for as long as the calibration ran.

What is driven, through a REAL ``engine.start`` on the simulator (the scheduler
is stubbed to a body that runs the calibration, so the frames are the engine's
own and the moment is not a time-of-day lottery, #682), and graded at the
camera, with the engine's published state read as the exposure starts:

* a flat and a dark set built the way the engine builds them, called with the
  placeholder index each caller passes (0; the held target's 1): no active
  step, the plan's owed seconds undiminished, the calibration target's own name
  published;
* a plan's OWN calibration target keeps its place (1, 0): the fix is not "never
  publish a step";
* a quality-rejected calibration frame that is RETAKEN keeps the same answer
  across the retake, whose re-begin used to fall back to ``(ti, 0)``;
* a rejected LIGHT frame whose retake comes after a hold dark: the engine runs
  the plan's instructions BEFORE a retake, and ``on_frame_rejected ->
  hold_for_clear`` shoots a hold dark through ``_begin_frame`` in between, so
  the retake must take its place from its own arguments and not from
  ``_active_step``, which by then names the dark (or nothing);
* the real DUSK FLATS stage and the real day-darks phase, end to end.

NAMED MUTANTS (2026-10-10), each run from a byte backup of engine.py under the
scratchpad, restored with a byte copy and compared by md5sum. The failing
assertion of each, verbatim (first failure of the case):

* "calibration publishes the placeholder" (``_run_calibration``'s
  ``self._begin_frame(plan_ti, si, exp)`` made ``self._begin_frame(ti, si,
  exp)``): 8 failed, 2 passed (the two plan-holds-it controls). The throwaway
  cases:

      AssertionError: a Flat frame was attributed to plan step (0, 0):
      {'frame_type': 'Flat', 'active': (0, 0), 'owed_s': 7, ...}

  (the held target's index, 1, gives ``(1, 0)`` the same way), the DUSK FLATS
  and day-darks cases on ``assert (0, 0) is None``, the retake case on
  ``[(0, 0), None, (0, 0)] == [None, None, None]``, and the two light
  retakes on the hold dark, which was published as ``(0, 0)``.

* "retake uses the target's index" (``_handle_reject``'s
  ``self._begin_frame(self._plan_index(ti, target), si, ...)`` made
  ``self._begin_frame(ti, si, ...)``): 1 failed, 9 passed, the throwaway
  retake case, on the retake's own exposure:

      assert [None, (0, 0), None] == [None, None, None]

* "retake reads the shared active step" (the same line made
  ``self._begin_frame(*(self._active_step or (None, 0)), ...)``, the first
  version of this fix): 2 failed, 8 passed, both light retakes after a hold
  dark, which the throwaway-retake cases cannot see:

      AssertionError: the retake of Alpha step 0 was published as None
      AssertionError: the retake of Alpha step 1 was published as None

* "retake always step 0" (``si`` made ``0``): 1 failed, 9 passed, the step 1
  light retake, ``assert (0, 0) == (0, 1)``.

* "``_plan_index`` without the identity test" (``and targets[ti] is target``
  dropped): 8 failed, 2 passed, every one on a behaviour assertion
  (``assert (0, 0) is None``), none on a premise.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       Instruction, SequencePlan, Target)

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


def _lights(name: str, ra: float) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=41.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100,
                                      offset=30, binning=1, count=2)
                         for f in ("L", "R")])


def _plan(*targets: Target, **kw) -> SequencePlan:
    return SequencePlan(name="Calibration night", guide=False, dither_every=0,
                        targets=list(targets), **kw)


def _owed_s(plan: SequencePlan) -> int:
    """Every exposure second the plan's own steps still owe, nothing shot."""
    return round(sum(s.count * s.exposure_s
                     for t in plan.targets for s in t.steps))


def _throwaway(frame_type: str) -> Target:
    """A calibration target as ``_day_darks``, ``_dusk_flats`` and the cloud
    hold build theirs: not in the plan, a step of its own."""
    filt = "L" if frame_type == "Flat" else None
    return Target(name=f"throwaway {frame_type.lower()}s", ra_hours=0.0,
                  dec_deg=0.0, calibration=True, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=filt, exposure_s=0.05, gain=100,
                                      offset=30, binning=1, count=2,
                                      frame_type=frame_type)])


class Spy:
    """What the engine PUBLISHES at the moment each saved calibration exposure
    starts: the active step, the owed seconds in the status ``progress``, and
    the target it names."""

    def __init__(self, hub, eng, monkeypatch):
        self.shots: list[dict] = []
        real = hub.capture

        async def capture(exposure_s, gain, offset, binning, **kw):
            if kw.get("save", True) and kw.get("frame_type", "Light") != "Light":
                progress = eng.state.get("progress") or {}
                self.shots.append({
                    "frame_type": kw.get("frame_type"),
                    "active": eng._active_step,
                    "owed_s": progress.get("remaining_capture_s"),
                    "target": eng.state.get("target"),
                })
            return await real(exposure_s, gain, offset, binning, **kw)

        monkeypatch.setattr(hub, "capture", capture)


async def _run(hub, plan, monkeypatch, body=None):
    """A real run on the simulator. ``body(eng)`` replaces the scheduler, so it
    runs after the stages that precede the first light; without one the
    scheduler is a no-op marker."""
    eng = SequenceEngine(hub)
    spy = Spy(hub, eng, monkeypatch)

    async def scheduled(_plan):
        if body is not None:
            await body(eng)

    monkeypatch.setattr(eng, "_run_scheduled", scheduled)
    eng.start(plan)
    await eng._task
    return eng, spy


# --------------------------------------- the placeholder callers pass in

@pytest.mark.parametrize("frame_type, ti", [
    ("Flat", 0),    # DUSK FLATS: `_run_calibration(0, flat_target)`
    ("Dark", 0),    # day darks: `_run_calibration(0, dark)`
    ("Dark", 1),    # cloud-hold darks: the HELD target's index, here Bravo's
])
async def test_a_throwaway_calibration_target_publishes_no_plan_step(
        sim_hub, monkeypatch, frame_type, ti):
    """No active step, the plan's seconds still all owed, and the calibration
    target's own name. Unfixed, the active step was ``(ti, 0)`` and the owed
    seconds were one exposure short (a light step is credited a frame it never
    got)."""
    plan = _plan(_lights("Alpha", 0.7), _lights("Bravo", 5.5))
    throwaway = _throwaway(frame_type)

    async def body(eng):
        await eng._run_calibration(ti, throwaway)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body)
    assert all(t is not throwaway for t in plan.targets), (
        "premise: the throwaway target is not in the plan")
    assert len(spy.shots) == 2, ("premise: both calibration frames were "
                                 f"shot: {spy.shots}")
    for shot in spy.shots:
        assert shot["active"] is None, (
            f"a {frame_type} frame was attributed to plan step "
            f"{shot['active']}: {shot}")
        assert shot["owed_s"] == _owed_s(plan), (
            f"the status took a frame off a light step that was not shot: "
            f"{shot}")
        assert shot["target"] == throwaway.name, shot


async def test_a_plans_own_calibration_target_keeps_its_place(
        sim_hub, monkeypatch):
    """The control: a calibration target the PLAN holds is a real step of the
    plan and is published as one, at its own index, not target 0."""
    cal = _throwaway("Dark")
    cal.name = "plan darks"
    plan = _plan(_lights("Alpha", 0.7), cal)

    async def body(eng):
        await eng._run_calibration(1, cal)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body)
    assert plan.targets[1] is cal, "premise: the plan holds it"
    assert [s["active"] for s in spy.shots] == [(1, 0), (1, 0)], spy.shots


# ------------------------------------------------------------- the retake

@pytest.mark.parametrize("plan_holds_it, expected", [
    (False, None),      # the engine's throwaway dark
    (True, (1, 0)),     # a calibration target of the plan
])
async def test_a_retaken_calibration_frame_keeps_its_answer(
        sim_hub, monkeypatch, plan_holds_it, expected):
    """A calibration frame the quality gate rejects and the escalation retakes
    is re-begun, and the re-begin used to fall back to ``(ti, 0)`` for
    ``_active_step``'s absence, which for a throwaway target is the plan's first
    target again. Two captures of the first frame, then the second frame's."""
    cal = _throwaway("Dark")
    plan = _plan(_lights("Alpha", 0.7), *([cal] if plan_holds_it else []))
    ti = 1 if plan_holds_it else 0

    async def body(eng):
        graded = {"n": 0}

        def check(info, *, record=True, calibration=False):
            graded["n"] += 1
            return graded["n"] != 1          # the first frame is rejected

        monkeypatch.setattr(eng, "_check_quality", check)
        monkeypatch.setattr(eng, "_reject_action", lambda: "retake")
        await eng._run_calibration(ti, cal)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body)
    assert len(spy.shots) == 3, (
        f"premise: the first frame was retaken (3 exposures for 2 frames): "
        f"{spy.shots}")
    assert [s["active"] for s in spy.shots] == [expected] * 3, spy.shots


@pytest.mark.parametrize("reject_n, si", [
    (1, 0),     # Alpha's first L is rejected: step 0
    (3, 1),     # Alpha's first R is rejected: step 1
])
async def test_a_light_retake_after_a_hold_dark_keeps_its_own_step(
        sim_hub, monkeypatch, reject_n, si):
    """The engine runs the plan's instructions BEFORE a retake, and
    ``on_frame_rejected -> hold_for_clear`` shoots a hold dark in between,
    through ``_begin_frame`` and ``_run_calibration``. The retake is the LIGHT
    frame again, so it is published as the step it is: ``(0, si)``, and the
    ETA counts it once (as the in-flight frame), not twice.

    Read off ``_active_step`` after the dark it was ``None`` (the step 0 case)
    or the hold dark's leftover (the step 1 case): the ETA then owed the
    retake's second as well as the in-flight one.

    The hold is stood in for by the one thing its dark branch does, a real
    ``_hold_darks`` call, which runs the real ``_run_calibration`` on the
    engine's own dark target; nothing else in the hold begins a frame."""
    plan = _plan(_lights("Alpha", 0.7), cloud_hold_darks=1,
                 instructions=[Instruction(trigger="on_frame_rejected",
                                           action="hold_for_clear")])
    exposure_s = plan.targets[0].steps[si].exposure_s
    eng = SequenceEngine(sim_hub)
    shots: list[dict] = []
    real = sim_hub.capture

    async def capture(exposure_s_, gain, offset, binning, **kw):
        if kw.get("save", True):
            shots.append({"frame_type": kw.get("frame_type", "Light"),
                          "active": eng._active_step,
                          "owed_s": eng.compute_eta().get(
                              "remaining_capture_s")})
        return await real(exposure_s_, gain, offset, binning, **kw)

    monkeypatch.setattr(sim_hub, "capture", capture)

    lights = {"n": 0}

    def check(info, *, record=True, calibration=False):
        if calibration:
            return True
        lights["n"] += 1
        return lights["n"] != reject_n      # light number `reject_n` is bad

    async def hold_for_clear(reason, target):
        await eng._hold_darks(target)

    monkeypatch.setattr(eng, "_check_quality", check)
    monkeypatch.setattr(eng, "_reject_action", lambda: "retake")
    monkeypatch.setattr(eng, "_hold_for_clear", hold_for_clear)
    monkeypatch.setattr(SequenceEngine, "_hold_darks_shortfall",
                        lambda self, step, quota: (quota, 0))
    eng.start(plan)
    await eng._task

    kinds = [s["frame_type"] for s in shots]
    darks = [k for k, t in enumerate(kinds) if t == "Dark"]
    assert len(darks) == 1 and 0 < darks[0] < len(shots) - 1, (
        f"premise: one hold dark between the reject and its retake: {shots}")
    assert kinds.count("Light") == 5, (
        f"premise: four lights and one retake: {shots}")
    assert kinds[darks[0] - 1] == kinds[darks[0] + 1] == "Light", shots
    assert shots[darks[0]]["active"] is None, shots[darks[0]]
    retake = shots[darks[0] + 1]
    # the lights banked before the rejected one, and the retaken frame itself
    # is the one in flight
    banked_s = (reject_n - 1) * exposure_s
    assert retake["active"] == (0, si), (
        f"the retake of Alpha step {si} was published as {retake['active']}: "
        f"{shots}")
    assert retake["owed_s"] == _owed_s(plan) - banked_s - exposure_s, (
        f"the ETA did not count the retake once, as the in-flight frame: "
        f"{shots}")


# ------------------------------------------ the real stages, end to end

async def test_the_dusk_flats_stage_publishes_no_plan_step(
        sim_hub, monkeypatch):
    """The stage the issue names: every saved flat of a real DUSK FLATS set,
    L then R, is exposed with no active step and the lights' seconds all
    owed."""
    plan = _plan(_lights("Alpha", 0.7),
                 dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                          adu_target=8000, count=2))
    eng, spy = await _run(sim_hub, plan, monkeypatch)
    flats = [s for s in spy.shots if s["frame_type"] == "Flat"]
    assert len(flats) == 4, f"premise: the stage shot its flats: {spy.shots}"
    for shot in flats:
        assert shot["active"] is None, (
            f"a dusk flat was attributed to plan step {shot['active']}")
        assert shot["owed_s"] == _owed_s(plan), shot
        assert str(shot["target"]).startswith("dusk flats"), shot


async def test_the_day_darks_phase_publishes_no_plan_step(
        sim_hub, monkeypatch):
    """The other call site named: the darks taken after the night."""
    plan = _plan(_lights("Alpha", 0.7), day_darks=2)
    monkeypatch.setattr(SequenceEngine, "_hold_darks_shortfall",
                        lambda self, step, quota: (quota, 0))

    eng, spy = await _run(sim_hub, plan, monkeypatch)
    darks = [s for s in spy.shots if s["frame_type"] == "Dark"]
    assert len(darks) == 2, f"premise: the phase shot its darks: {spy.shots}"
    for shot in darks:
        assert shot["active"] is None, (
            f"a day dark was attributed to plan step {shot['active']}")
        assert shot["owed_s"] == _owed_s(plan), shot
        assert str(shot["target"]).startswith("day darks"), shot
