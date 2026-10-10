# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A calibration frame the plan does not hold is not the plan's progress,
its retake budget or its active target (#939, #940, #941).

THE CLASS (the same as #842, which stopped such a frame publishing a plan
STEP): a placeholder that is also a real value. The targets the engine builds
for itself (the day darks, the DUSK FLATS sets, the cloud-hold darks) are not in
``plan.targets``, so ``_index_of_target`` answers 0 for them, and 0 is an index
the plan DOES hold. Three more readers of that 0:

* ``_record_frame`` counted every frame in ``_frames_done`` while
  ``plan.total_frames()`` counts only the plan's own, so the progress bar read
  75 percent through the flats of a night that had not shot a light, the ETA's
  ``frames_remaining`` was short by the number of throwaway frames shot, and
  after the night ``frames_done`` was past ``frames_total`` (#939);
* ``_handle_reject`` kept the retake budget under ``_index_of_target``, so a
  retaken cloud-hold, day or dusk calibration frame spent the FIRST LIGHT
  target's retakes for the night (#940);
* ``_run_calibration`` published ``target_index=0`` (or the held target's
  index) beside the calibration target's own name, so the Plan editor lit
  plan target 0 and the run header read "1 of N" while the flats were being
  shot (#941).

What is driven, through a REAL ``engine.start`` on the simulator, and graded
from what the engine PUBLISHES (``engine.state``) and at the camera
(``hub.capture``), read as each exposure starts:

* the real DUSK FLATS stage, then the lights, on one plan: the flats leave
  ``progress`` at 0 frames and 0 percent, the lights count from 0 to the plan's
  total, and ``frames_done == frames_total`` at the end;
* the ETA's frame-driven cost (the refocus cadence) is the same through the
  flats as before them;
* a calibration target the PLAN holds still counts (the fix is not "calibration
  frames never count") and still spends its own retake budget;
* a retaken throwaway frame leaves the light target's retake budget whole, and
  the light target's own cap still holds;
* the throwaway frame publishes ``target_index`` as an explicit null, a plan's
  own calibration target publishes its index, and the target held through a
  cloud-hold dark is published again once the dark is done.

NAMED MUTANTS (2026-10-10), each run from a byte backup of engine.py under the
scratchpad, restored with a byte copy and compared by md5sum. See the report
for the failing line of each.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.engine import AF_COST_S
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
    """Four light frames: L x2 then R x2."""
    return Target(name=name, ra_hours=ra, dec_deg=41.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100,
                                      offset=30, binning=1, count=2)
                         for f in ("L", "R")])


def _plan(*targets: Target, **kw) -> SequencePlan:
    return SequencePlan(name="Throwaway night", guide=False, dither_every=0,
                        targets=list(targets), **kw)


def _dark_target(name: str, count: int = 2) -> Target:
    """A calibration target as the engine builds its own (no filter, so the
    wheel goes to the blackout slot): in the plan or not is the caller's."""
    return Target(name=name, ra_hours=0.0, dec_deg=0.0, calibration=True,
                  center=False, autofocus_first=False,
                  steps=[ExposureStep(exposure_s=0.05, gain=100, offset=30,
                                      binning=1, count=count,
                                      frame_type="Dark")])


class Spy:
    """What the engine PUBLISHES as each saved exposure starts."""

    def __init__(self, hub, eng, monkeypatch):
        self.shots: list[dict] = []
        real = hub.capture

        async def capture(exposure_s, gain, offset, binning, **kw):
            if kw.get("save", True):
                st = eng.state
                progress = st.get("progress") or {}
                self.shots.append({
                    "frame_type": kw.get("frame_type", "Light"),
                    "target": st.get("target"),
                    "has_index": "target_index" in st,
                    "index": st.get("target_index"),
                    "frames_done": progress.get("frames_done"),
                    "frames_total": progress.get("frames_total"),
                    "percent": progress.get("percent"),
                    "calibration_done": progress.get("calibration_frames_done"),
                    "events_cost_s": progress.get("events_cost_s"),
                })
            return await real(exposure_s, gain, offset, binning, **kw)

        monkeypatch.setattr(hub, "capture", capture)


async def _run(hub, plan, monkeypatch, body=None, *, before_lights=None):
    """A real run on the simulator.

    ``body(eng)`` REPLACES the scheduler, so the run is only the stages ahead
    of the first light and whatever ``body`` drives. ``before_lights(eng)``
    runs ahead of the real scheduler instead, so the lights are shot too. The
    plans here have no schedule, so the scheduler is not a time-of-day lottery
    (#682)."""
    eng = SequenceEngine(hub)
    spy = Spy(hub, eng, monkeypatch)
    real = eng._run_scheduled

    async def scheduled(p):
        if before_lights is not None:
            await before_lights(eng)
        if body is not None:
            await body(eng)
        else:
            await real(p)

    if body is not None or before_lights is not None:
        monkeypatch.setattr(eng, "_run_scheduled", scheduled)
    eng.start(plan)
    await eng._task
    return eng, spy


def _of(spy: Spy, frame_type: str) -> list[dict]:
    return [s for s in spy.shots if s["frame_type"] == frame_type]


# ------------------------------------------------------------------ #939

async def test_dusk_flats_leave_the_plans_progress_alone(
        sim_hub, monkeypatch):
    """4 light frames, DUSK FLATS of 2 filters x 2 frames. The flats are
    exposed with the plan at 0 of 4 frames and 0 percent (unfixed: 0, 1, 2, 3
    of 4, so 75 percent before a light was shot); the lights then count 0, 1,
    2, 3 and the night ends at exactly the plan's total, not past it."""
    plan = _plan(_lights("Alpha", 0.7),
                 dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                          adu_target=8000, count=2))
    eng, spy = await _run(sim_hub, plan, monkeypatch)

    flats, lights = _of(spy, "Flat"), _of(spy, "Light")
    assert len(flats) == 4 and len(lights) == 4, (
        f"premise: four flats then four lights were exposed: {spy.shots}")
    assert spy.shots[:4] == flats, "premise: the flats come first"
    assert plan.total_frames() == 4, "premise: only the lights are the plan's"
    for shot in flats:
        assert shot["frames_done"] == 0 and shot["percent"] == 0, (
            f"a dusk flat moved the plan's progress: {shot}")
        assert shot["frames_total"] == 4, shot
    assert [s["frames_done"] for s in lights] == [0, 1, 2, 3], (
        f"the lights did not count from zero: {lights}")
    progress = eng.state["progress"]
    assert (progress["frames_done"], progress["frames_total"],
            progress["percent"]) == (4, 4, 100.0), (
        f"the night did not end at the plan's total: {progress}")
    assert progress["calibration_frames_done"] == 4, (
        "the flats are counted apart, so a client can see them land")


async def test_throwaway_frames_leave_the_etas_frame_count_alone(
        sim_hub, monkeypatch):
    """``compute_eta`` prices the refocus cadence off ``frames_remaining``. The
    plan owes 4 frames and refocuses every 2, so 2 refocuses are owed and stay
    owed for as long as the flats are shot. Unfixed, the 4th flat was exposed
    with 3 'frames done', 1 remaining and 0 refocuses owed."""
    plan = _plan(_lights("Alpha", 0.7), autofocus_every=2,
                 dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                          adu_target=8000, count=2))

    async def nothing(eng):
        return None

    eng, spy = await _run(sim_hub, plan, monkeypatch, body=nothing)
    flats = _of(spy, "Flat")
    assert len(flats) == 4, f"premise: the stage shot its flats: {spy.shots}"
    first = flats[0]["events_cost_s"]
    assert first >= 2 * AF_COST_S, (
        f"premise: two refocuses are owed before any flat: {first}")
    assert [s["events_cost_s"] for s in flats] == [first] * 4, (
        f"the flats ate into the plan's owed frames: {flats}")
    assert eng.compute_eta()["events_cost_s"] == first, (
        "after the stage the frames are still all owed")


async def test_a_plans_own_calibration_target_still_counts(
        sim_hub, monkeypatch):
    """The control: a calibration target the PLAN holds is in
    ``total_frames()``, so its frames are the plan's progress."""
    cal = _dark_target("plan darks")
    plan = _plan(_lights("Alpha", 0.7), cal)

    async def body(eng):
        await eng._run_calibration(1, cal)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body=body)
    assert plan.targets[1] is cal and plan.total_frames() == 6, (
        "premise: the plan holds it, and owes it")
    assert [s["frames_done"] for s in _of(spy, "Dark")] == [0, 1], spy.shots
    progress = eng.state["progress"]
    assert (progress["frames_done"], progress["frames_total"],
            progress["percent"]) == (2, 6, 33.3), progress
    assert progress["calibration_frames_done"] == 0, progress


@pytest.mark.parametrize("plan_frames, throwaway_frames, said", [
    (0, 3, 1),      # a night of cloud-hold darks alone
    (3, 0, 1),      # the usual night
    (0, 0, 0),      # a run that shot nothing says nothing
])
async def test_the_mount_still_tracking_line_counts_throwaway_frames_too(
        sim_hub, monkeypatch, bus_lines, plan_frames, throwaway_frames, said):
    """A run set not to park says so when it ended having shot something: the
    mount is still tracking. That line read ``_frames_done`` alone, which the
    cloud-hold darks of a night that never cleared used to fill, so taking the
    throwaway frames out of that counter must not silence it."""
    monkeypatch.delenv("ASTRODECK_NO_DAWN_PARK", raising=False)
    eng = SequenceEngine(sim_hub)
    eng._frames_done = plan_frames
    eng._calibration_frames_done = throwaway_frames
    await eng._wind_down_park_and_close(False, False)
    lines = [m for _l, m, _s in bus_lines
             if m.startswith("this run was set not to park")]
    assert len(lines) == said, lines


# ------------------------------------------------------------------ #940

def _rejecting(eng, monkeypatch, bad: tuple[int, ...]):
    """Reject the Nth quality check of the run (1-based), light, calibration
    and retake alike, and retake what is rejected. ONE counter, not one per
    kind: the stub is asked in the order the frames are graded, whatever
    they are, so the Nth check is the Nth frame (a retake is graded with
    its target's own ``calibration`` flag since #968, a light's with False)."""
    seen = {"n": 0}

    def check(info, *, record=True, calibration=False):
        seen["n"] += 1
        return seen["n"] not in bad

    monkeypatch.setattr(eng, "_check_quality", check)
    monkeypatch.setattr(eng, "_reject_action", lambda: "retake")


def _cap(monkeypatch, n: int) -> None:
    monkeypatch.setattr(hub_module.config_store.cfg().escalation,
                        "hfr_retake_limit_per_target", n)


async def test_a_retaken_throwaway_frame_leaves_the_light_retake_budget(
        sim_hub, monkeypatch):
    """Cap of 1 retake per target. A throwaway dark is rejected and retaken
    (checks 1 and 2), its second frame passes (3), and then the first light
    frame is rejected (4): it must still get its retake. Unfixed the dark's
    retake was kept under target 0, Alpha's index, so the light was DISCARDED
    for the cap and 4 lights were exposed, not 5."""
    _cap(monkeypatch, 1)
    throwaway = _dark_target("throwaway darks")
    plan = _plan(_lights("Alpha", 0.7))

    async def before(eng):
        _rejecting(eng, monkeypatch, bad=(1, 4))
        await eng._run_calibration(0, throwaway)

    eng, spy = await _run(sim_hub, plan, monkeypatch, before_lights=before)
    assert all(t is not throwaway for t in plan.targets), (
        "premise: the throwaway target is not in the plan")
    assert len(_of(spy, "Dark")) == 3, (
        f"premise: the dark was retaken, 3 exposures for 2 frames: "
        f"{spy.shots}")
    assert len(_of(spy, "Light")) == 5, (
        f"the first light frame was not retaken: its retake budget went on "
        f"the throwaway frame: {_of(spy, 'Light')}")
    assert eng._frames_done == 4, "premise: the plan's four lights were banked"


async def test_a_light_targets_own_retake_cap_still_holds(
        sim_hub, monkeypatch):
    """The control for the one above: a light target is still capped. Cap 1,
    the first and the third quality checks fail (the second is the first
    one's retake, which passes): the first is retaken, the second rejected
    frame is discarded for the cap."""
    _cap(monkeypatch, 1)
    plan = _plan(_lights("Alpha", 0.7))

    async def before(eng):
        _rejecting(eng, monkeypatch, bad=(1, 3))

    eng, spy = await _run(sim_hub, plan, monkeypatch, before_lights=before)
    assert len(_of(spy, "Light")) == 5, (
        f"one retake and one discard: 4 frames + 1 retake = 5 exposures, 6 "
        f"means the cap did not hold: {_of(spy, 'Light')}")
    assert eng._frames_done == 3, "one frame was discarded, three were banked"


async def test_a_plans_own_calibration_target_spends_its_own_budget(
        sim_hub, monkeypatch):
    """The other control: a calibration target the plan DOES hold is a
    target with a budget of its own, at its own index. Cap 1, both of its
    frames rejected: the first is retaken, the second is discarded for the
    cap (3 exposures, not 4)."""
    _cap(monkeypatch, 1)
    cal = _dark_target("plan darks")
    plan = _plan(_lights("Alpha", 0.7), cal)

    async def body(eng):
        _rejecting(eng, monkeypatch, bad=(1, 3))
        await eng._run_calibration(1, cal)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body=body)
    assert plan.targets[1] is cal, "premise: the plan holds it"
    assert len(_of(spy, "Dark")) == 3, (
        f"the plan target's retake cap did not hold: {_of(spy, 'Dark')}")


# ------------------------------------------------------------------ #941

@pytest.mark.parametrize("frame_type, ti", [
    ("Flat", 0),    # DUSK FLATS: `_run_calibration(0, flat_target)`
    ("Dark", 0),    # day darks: `_run_calibration(0, dark)`
    ("Dark", 1),    # cloud-hold darks: the HELD target's index, here Bravo's
])
async def test_a_throwaway_target_publishes_a_null_target_index(
        sim_hub, monkeypatch, frame_type, ti):
    """An EXPLICIT null (the key present), not an absent key: a client reads
    absence as 'no target published yet' and a null as 'no plan target is
    exposing'. The calibration target's own name is still published."""
    plan = _plan(_lights("Alpha", 0.7), _lights("Bravo", 5.5))
    throwaway = _dark_target("throwaway frames")
    throwaway.steps[0].frame_type = frame_type
    if frame_type == "Flat":
        throwaway.steps[0].filter = "L"

    async def body(eng):
        await eng._run_calibration(ti, throwaway)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body=body)
    shots = _of(spy, frame_type)
    assert len(shots) == 2, f"premise: both frames were shot: {spy.shots}"
    for shot in shots:
        assert shot["has_index"] and shot["index"] is None, (
            f"a {frame_type} frame was published as plan target "
            f"{shot['index']}: {shot}")
        assert shot["target"] == throwaway.name, shot


async def test_a_plans_own_calibration_target_publishes_its_index(
        sim_hub, monkeypatch):
    """The control: a calibration target of the plan is a plan target."""
    cal = _dark_target("plan darks")
    plan = _plan(_lights("Alpha", 0.7), cal)

    async def body(eng):
        await eng._run_calibration(1, cal)

    eng, spy = await _run(sim_hub, plan, monkeypatch, body=body)
    assert [(s["index"], s["target"]) for s in _of(spy, "Dark")] == [
        (1, "plan darks")] * 2, spy.shots


async def test_the_flats_publish_no_index_and_the_lights_publish_theirs(
        sim_hub, monkeypatch):
    """The real DUSK FLATS stage and then two light targets, in one run: null
    through the flats, then each light frame at its own target's index. Unfixed
    the flats read 0 and the second target's frames read 1 all the same, so
    the lights are the control that the null does not outlive the flats."""
    plan = _plan(_lights("Alpha", 0.7), _lights("Bravo", 5.5),
                 dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                          adu_target=8000, count=2))
    eng, spy = await _run(sim_hub, plan, monkeypatch)
    flats, lights = _of(spy, "Flat"), _of(spy, "Light")
    assert len(flats) == 4 and len(lights) == 8, spy.shots
    for shot in flats:
        assert shot["has_index"] and shot["index"] is None, shot
        assert str(shot["target"]).startswith("dusk flats"), shot
    assert [(s["target"], s["index"]) for s in lights] == (
        [("Alpha", 0)] * 4 + [("Bravo", 1)] * 4), lights


async def test_the_held_target_is_published_again_after_a_hold_dark(
        sim_hub, monkeypatch):
    """A cloud-hold dark is shot while target Bravo is held. While it exposes
    the state names the dark and no plan target; once it is done the hold goes
    on for BRAVO, and the state says so again, instead of naming the dark (and,
    now that the dark has no index, no target at all) for the rest of Bravo's
    frames.

    The hold is stood in for by the one thing its dark branch does, a real
    ``_hold_darks`` call on a rejected light frame (the harness of
    ``test_w19_calibration_active_step``), and the retake that follows the
    hold is the first frame exposed after the dark."""
    plan = _plan(_lights("Alpha", 0.7), _lights("Bravo", 5.5),
                 cloud_hold_darks=1,
                 instructions=[Instruction(trigger="on_frame_rejected",
                                           action="hold_for_clear")])
    eng = SequenceEngine(sim_hub)
    spy = Spy(sim_hub, eng, monkeypatch)
    lights = {"n": 0}

    def check(info, *, record=True, calibration=False):
        if calibration:
            return True
        lights["n"] += 1
        return lights["n"] != 5             # Bravo's first light is bad

    async def hold_for_clear(reason, target):
        await eng._hold_darks(target)

    monkeypatch.setattr(eng, "_check_quality", check)
    monkeypatch.setattr(eng, "_reject_action", lambda: "retake")
    monkeypatch.setattr(eng, "_hold_for_clear", hold_for_clear)
    monkeypatch.setattr(SequenceEngine, "_hold_darks_shortfall",
                        lambda self, step, quota: (quota, 0))
    eng.start(plan)
    await eng._task

    kinds = [s["frame_type"] for s in spy.shots]
    dark = kinds.index("Dark")
    assert kinds.count("Dark") == 1 and kinds[dark - 1] == "Light" \
        and kinds[dark + 1] == "Light", (
        f"premise: one hold dark between the rejected light and its "
        f"retake: {spy.shots}")
    assert spy.shots[dark]["has_index"] and spy.shots[dark]["index"] is None, (
        f"the hold dark was published as plan target "
        f"{spy.shots[dark]['index']}")
    assert str(spy.shots[dark]["target"]).startswith("cloud-hold darks"), (
        spy.shots[dark])
    rejected, retake = spy.shots[dark - 1], spy.shots[dark + 1]
    assert (rejected["target"], rejected["index"]) == ("Bravo", 1), rejected
    assert (retake["target"], retake["index"]) == ("Bravo", 1), (
        f"the held target was not published again after the dark: {retake}")
