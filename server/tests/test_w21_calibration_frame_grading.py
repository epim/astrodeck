# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A throwaway calibration frame is graded as a calibration frame, and is not
a sample of the lights' cadence (#968, #969).

THE CLASS (the same as #939/#940/#941, which stopped such a frame counting as
the plan's progress, spending its retake budget and publishing its place): the
engine's own darks and flats ride the lights' machinery, and each reader of a
shared path has to be told what kind of frame it is holding.

* ``_run_calibration`` grades a frame with ``_check_quality(info,
  calibration=True)``, which leaves out the star floor, the guide-RMS ceiling
  and the eccentricity ceiling: a dark has no stars and a flat is not guided.
  ``_handle_reject`` re-graded the RETAKE of that same frame with
  ``_check_quality(new_info)``, which applies all three, so a retaken dark was
  thrown away as "shot with the guider stopped" (or under the star floor, or as
  eccentric) and the retake was spent for nothing (#968).
* ``_record_frame`` folded every frame's cadence into ``_overhead_ema`` and
  ``_overhead_samples``, the engine's own DUSK FLATS included, so the lights'
  per-frame overhead was moved by the flats and ``eta_confident`` could turn
  true before a light was shot (#969).

What is driven, through a REAL ``engine.start`` on the simulator with the
engine's own ``_handle_reject`` and ``_record_frame`` and, but for one scripted
verdict (below), its own ``_check_quality``, and graded from what the engine
PUBLISHES (``engine.state``), from the files on disk and from ``compute_eta``:

* a dark whose first grading is refused and that is retaken is kept when the
  retake would fail the star floor, the guide-RMS ceiling or the eccentricity
  ceiling, one case each. The HFR median gate was the one gate a calibration
  frame was held to and so what refused that first grading; it is not any
  more (#995, test_w22_calibration_hfr_median.py), so nothing real refuses a
  calibration frame and the first verdict is scripted, for that one call;
* the control: a retaken LIGHT frame is still graded by those gates;
* a DUSK FLATS stage leaves the overhead EMA, its sample count and the ETA's
  confidence where they started;
* the controls: a calibration target the PLAN holds, and the lights, still
  feed the overhead EMA.

NAMED MUTANTS (2026-10-10), each run from a byte backup of engine.py under the
scratchpad, restored with a byte copy and compared by md5sum. See the report
for the failing line of each:

* the retake graded without the flag again (``_check_quality(new_info)``):
  the three ``test_a_retaken_dark_is_not_graded_by_the_light_gates`` cases;
* the overhead EMA folding every frame again (``plan_frame and`` removed):
  ``test_dusk_flats_do_not_feed_the_overhead_ema`` and
  ``test_the_lights_after_the_flats_earn_the_etas_confidence``;
* over-correction, the retake graded as a calibration frame whatever it is:
  ``test_a_retaken_light_frame_is_still_graded_by_the_light_gates``;
* over-correction, the EMA skipping every calibration target:
  ``test_a_plans_own_calibration_target_still_feeds_the_overhead_ema``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.engine import DEFAULT_OVERHEAD_S, ETA_MIN_FRAMES
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       SequencePlan, Target)

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
                  steps=[ExposureStep(filter=f, exposure_s=0.05, gain=100,
                                      offset=30, binning=1, count=2)
                         for f in ("L", "R")])


def _dark_target(name: str, count: int = 2) -> Target:
    """A calibration target as the engine builds its own (no filter, so the
    wheel goes to the blackout slot): in the plan or not is the caller's."""
    return Target(name=name, ra_hours=0.0, dec_deg=0.0, calibration=True,
                  center=False, autofocus_first=False,
                  steps=[ExposureStep(exposure_s=0.05, gain=100, offset=30,
                                      binning=1, count=count,
                                      frame_type="Dark")])


def _plan(*targets: Target, **kw) -> SequencePlan:
    return SequencePlan(name="Graded night", guide=False, dither_every=0,
                        targets=list(targets), **kw)


class Camera:
    """The camera, as the engine sees it: ``hub.capture`` is real, and what it
    REPORTS about each saved exposure may be rewritten per frame type and
    ordinal, because a simulated dark has no stars to fail a gate with and a
    run's own cadence is whatever this machine's scheduler makes it.

    ``report(frame_type, n, info)`` edits the n-th (1-based) saved exposure of
    that frame type. ``lag_s`` is slept after every saved exposure, so each
    frame's cadence exceeds its exposure by a margin the clock cannot eat: the
    overhead sample is positive on every frame, which is the case where a
    frame is folded into the EMA at all."""

    def __init__(self, hub, monkeypatch, *, report=None, lag_s: float = 0.0):
        self.shots: list[dict] = []
        self._n: dict[str, int] = {}
        real = hub.capture

        async def capture(exposure_s, gain, offset, binning, **kw):
            info = await real(exposure_s, gain, offset, binning, **kw)
            if kw.get("save", True):
                ft = kw.get("frame_type", "Light")
                n = self._n[ft] = self._n.get(ft, 0) + 1
                if report is not None:
                    report(ft, n, info)
                self.shots.append({"frame_type": ft, "n": n,
                                   "exposure_s": exposure_s,
                                   "path": info.get("saved_path")})
                if lag_s:
                    await asyncio.sleep(lag_s)
            return info

        monkeypatch.setattr(hub, "capture", capture)

    def of(self, frame_type: str) -> list[dict]:
        return [s for s in self.shots if s["frame_type"] == frame_type]


async def _run(hub, plan, monkeypatch, *, report=None, lag_s=0.0, body=None,
               before_lights=None):
    """A real run on the simulator. ``body(eng)`` REPLACES the scheduler (the
    run is the stages ahead of the first light and whatever ``body`` drives);
    ``before_lights(eng)`` runs ahead of the real scheduler instead. The plans
    here have no schedule, so the scheduler is not a time-of-day lottery
    (#682)."""
    eng = SequenceEngine(hub)
    cam = Camera(hub, monkeypatch, report=report, lag_s=lag_s)
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
    return eng, cam


# ------------------------------------------------------------------ #968

#: Four accepted lights at 3.0 px, so that a light at 9.0 px is refused by the
#: HFR median gate at a factor of 1.5.
_HFR_WINDOW = [3.0, 3.0, 3.0, 3.0]


async def _stopped() -> bool:
    return False


def _only_gate(**gate) -> dict:
    """Every light-frame gate off but the one named, so that a retake that is
    thrown away was thrown away by THAT gate."""
    off = {"min_stars": 0, "max_guide_rms": 0, "max_eccentricity": 0}
    off.update(gate)
    return off


def _refuse_the_first_calibration_frame(eng, monkeypatch) -> None:
    """Script ONE verdict: the first calibration frame the engine grades is
    refused, which is what the HFR median gate did until #995 (a calibration
    frame is held to no gate now, so nothing real can). Every other grading,
    the retake's included, is the engine's own ``_check_quality``."""
    real = eng._check_quality
    refused: list[dict] = []

    def check(info, **kw):
        if kw.get("calibration") and not refused:
            refused.append(info)
            return False
        return real(info, **kw)

    monkeypatch.setattr(eng, "_check_quality", check)


@pytest.mark.parametrize("gate, plan_kw, reported, guided", [
    # The guider has stopped: the issue's probe. A dark is never guided.
    ("guide RMS", _only_gate(max_guide_rms=2.0), {}, False),
    ("star floor", _only_gate(min_stars=50), {"stars": 3}, True),
    ("eccentricity", _only_gate(max_eccentricity=0.6), {"ecc": 0.9}, True),
])
async def test_a_retaken_dark_is_not_graded_by_the_light_gates(
        sim_hub, monkeypatch, bus_lines, gate, plan_kw, reported, guided):
    """Two throwaway darks. The first is refused and is retaken; the retake
    carries a reading that the named light gate would refuse (the second
    dark carries it too, and passes, which is the same gate graded as
    calibration). The retake must be KEPT: banked, on disk, and counted.
    Unfixed it was unlinked and the frame was lost, with the gate's own
    sentence in the log."""
    plan = SequencePlan(name="Graded night", guide=False, dither_every=0,
                        targets=[_lights("Alpha", 0.7)], **plan_kw)
    throwaway = _dark_target("throwaway darks")

    def report(ft, n, info):
        if ft != "Dark":
            return
        info.pop("hfr", None)
        info.update(reported)

    async def body(eng):
        monkeypatch.setattr(eng, "_reject_action", lambda: "retake")
        if not guided:
            monkeypatch.setattr(eng, "_frame_was_guided", _stopped)
        _refuse_the_first_calibration_frame(eng, monkeypatch)
        await eng._run_calibration(0, throwaway)

    eng, cam = await _run(sim_hub, plan, monkeypatch, report=report,
                          body=body)
    darks = cam.of("Dark")
    assert all(t is not throwaway for t in plan.targets), (
        "premise: the throwaway target is not in the plan")
    assert len(darks) == 3, (
        f"premise: the first dark was refused and retaken, "
        f"3 exposures for 2 frames: {cam.shots}")
    assert not Path(darks[0]["path"]).exists(), (
        "premise: the refused original was unlinked, so a file that exists "
        "below is a file somebody kept")
    assert Path(darks[1]["path"]).exists() and Path(darks[2]["path"]).exists(), (
        f"the retaken dark was thrown away by the {gate} gate, which does not "
        f"apply to a calibration frame: {darks}")
    assert eng._calibration_frames_done == 2, (
        f"two darks were owed and one was lost to the {gate} gate")
    assert eng.state["progress"]["calibration_frames_done"] == 2
    assert not any(msg.startswith(("Rejected: shot with the guider stopped",
                                   "frame stars", "frame eccentricity"))
                   for _lvl, msg, _src in bus_lines), (
        f"a light gate spoke about a dark: {bus_lines}")


async def test_a_retaken_light_frame_is_still_graded_by_the_light_gates(
        sim_hub, monkeypatch):
    """The control for the above: the fix tells the retake what kind of frame
    it is holding, it does not stop a retake being graded. The first light is
    refused by the HFR gate and retaken; the retake is under the star floor,
    so it is discarded, and the other three lights are banked."""
    plan = _plan(_lights("Alpha", 0.7), hfr_reject_factor=1.5,
                 **_only_gate(min_stars=50))

    def report(ft, n, info):
        if ft != "Light":
            return
        info.pop("hfr", None)
        info["stars"] = 3 if n == 2 else 100       # the first retake is #2
        if n == 1:
            info["hfr"] = 9.0

    async def before(eng):
        eng._recent_hfr = list(_HFR_WINDOW)
        monkeypatch.setattr(eng, "_reject_action", lambda: "retake")

    eng, cam = await _run(sim_hub, plan, monkeypatch, report=report,
                          before_lights=before)
    assert len(cam.of("Light")) == 5, (
        f"premise: 4 lights and the retake of the first: {cam.shots}")
    assert eng._frames_done == 3, (
        "the retake of a light frame under the star floor was banked; the "
        "light gates no longer grade a retake")


# ------------------------------------------------------------------ #969

def _dusk_plan(**kw) -> SequencePlan:
    """The real DUSK FLATS stage: 3 flats on each of 2 filters, then 4 lights.

    The simulator does not wait out an exposure in the suite (the conftest
    compresses its pacing), so a frame's cadence is the few milliseconds the
    engine itself takes, and the one second a flat is metered to at 8000 ADU
    would make every overhead negative, which the EMA rightly ignores. The
    sim's flat median is linear in the exposure, so the ADU target is cut to
    400 to meter a flat at about 50 ms, and the premise below asserts it: the
    ``lag_s`` the camera sleeps is then what a frame's cadence is made of."""
    return _plan(_lights("Alpha", 0.7),
                 dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                          adu_target=400, count=3), **kw)


async def _nothing(eng):
    return None


async def test_dusk_flats_do_not_feed_the_overhead_ema(sim_hub, monkeypatch):
    """6 flats (two filters of three), no light frame. The stage is more than
    ETA_MIN_FRAMES, so unfixed it earned the ETA its confidence on a cadence
    measured from flats, and moved the overhead off its seed."""
    assert 6 >= ETA_MIN_FRAMES, "premise: the stage alone would be enough"
    eng, cam = await _run(sim_hub, _dusk_plan(), monkeypatch,
                          lag_s=0.2, body=_nothing)
    assert len(cam.of("Flat")) == 6, f"premise: the flats were shot: {cam.shots}"
    assert all(s["exposure_s"] < 0.1 for s in cam.of("Flat")), (
        f"premise: a flat's cadence beats its exposure: {cam.shots}")
    assert cam.of("Light") == [], "premise: no light was shot"
    assert eng._calibration_frames_done == 6, (
        "premise: they were recorded as throwaway frames")
    assert eng._overhead_samples == 0, (
        f"{eng._overhead_samples} flats were counted as samples of the "
        f"lights' per-frame overhead")
    assert eng._overhead_ema == DEFAULT_OVERHEAD_S, (
        f"the overhead moved off its seed through the flats: "
        f"{eng._overhead_ema}")
    assert eng.compute_eta()["eta_confident"] is False, (
        "the ETA is confident on a measurement taken from flats")


async def test_a_plans_own_calibration_target_still_feeds_the_overhead_ema(
        sim_hub, monkeypatch):
    """The control: a calibration target the plan HOLDS is in the plan's
    total, so its frames are priced by the EMA and are samples of it."""
    cal = _dark_target("plan darks", count=4)
    plan = _plan(_lights("Alpha", 0.7), cal)

    async def body(eng):
        await eng._run_calibration(1, cal)

    eng, cam = await _run(sim_hub, plan, monkeypatch, lag_s=0.2, body=body)
    assert plan.targets[1] is cal and len(cam.of("Dark")) == 4, cam.shots
    assert eng._overhead_samples >= 1, (
        "the plan's own darks were left out of the overhead EMA")
    assert eng._overhead_ema != DEFAULT_OVERHEAD_S


async def test_the_lights_after_the_flats_earn_the_etas_confidence(
        sim_hub, monkeypatch):
    """The control that the flats are left out and the lights are not: the
    real stage and then four lights. The first light follows the setup, which
    flags its gap as an event, so three lights are samples, which is exactly
    ETA_MIN_FRAMES."""
    eng, cam = await _run(sim_hub, _dusk_plan(), monkeypatch,
                          lag_s=0.2)
    assert len(cam.of("Flat")) == 6 and len(cam.of("Light")) == 4, cam.shots
    assert eng._frames_done == 4, "premise: the plan's four lights were banked"
    assert eng._overhead_samples == ETA_MIN_FRAMES, (
        f"{eng._overhead_samples} samples: the lights alone are the evidence")
    assert eng._overhead_ema != DEFAULT_OVERHEAD_S
