# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A calibration frame is not graded by the HFR median gate and is not a
sample of the lights' running median (#995).

THE CLASS (the same as #939/#940/#941/#968/#969): the engine's own darks and
flats ride the lights' grader, and a judgement written for light frames has to
be told what kind of frame it is holding. ``_check_quality(info,
calibration=True)`` already left out the star floor, the guide-RMS ceiling and
the eccentricity ceiling (#968), and still did two things with the HFR:

* it compared the frame's HFR with the median of the LIGHT frames' HFR, so a
  flat or a day dark that reported one (the simulator gives a flat 3.5 px and
  15 stars; on glass, dust motes and vignetting can be detected as stars) was
  rejected when it was above median * factor, and under ``hfr_reject_action``
  discard or retake was then unlinked or shot again;
* it folded an ACCEPTED frame's HFR into ``_recent_hfr``, the window of up to
  twelve samples that gates every light after it, so a DUSK FLATS stage ahead
  of the first light could seed the window with flats, or drag it.

Both are driven through the engine's own ``_check_quality`` (nothing here
replaces the grader), the first directly and the second through a REAL
``engine.start`` on the simulator, graded from the files on disk, the
exposures the camera was asked for, and ``_recent_hfr`` and ``_rejected``.

NAMED MUTANTS (2026-10-10), each run from a byte backup of engine.py under the
scratchpad, restored with a byte copy and compared by md5sum. See the report
for the failing line of each:

* the HFR gate applying to calibration frames again (``and not calibration``
  removed from the gate's condition): every test that reports a large flat HFR
  against a seeded window;
* the fold applying to calibration frames again (``and not calibration``
  removed from the closing append): the window tests, and the DUSK FLATS test;
* over-correction, the fold skipped for every frame: the control that an
  accepted light still anchors the median.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
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


#: Four accepted light frames at 3.0 px: the median is 3.0 and, at a factor of
#: 1.5, anything over 4.5 px is refused.
_WINDOW = [3.0, 3.0, 3.0, 3.0]


def _only_hfr(**kw) -> dict:
    """Every other light-frame gate off, so that a frame that is refused was
    refused by the HFR median gate."""
    off = {"min_stars": 0, "max_guide_rms": 0, "max_eccentricity": 0}
    off.update(kw)
    return off


def _engine(sim_hub, window=()) -> SequenceEngine:
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(name="p", targets=[], hfr_reject_factor=1.5,
                            **_only_hfr())
    eng._recent_hfr = list(window)
    return eng


# ------------------------------------------------- the grader, driven directly

async def test_a_flat_with_a_large_hfr_is_not_rejected_by_the_median_gate(
        sim_hub, bus_lines):
    """The issue's probe: a flat reporting 9.0 px against four lights at 3.0.
    Unfixed it was refused ("frame HFR 9.00 >> median 3.00") and counted."""
    eng = _engine(sim_hub, _WINDOW)
    flat = {"hfr": 9.0, "stars": 15}
    assert eng._check_quality(flat, calibration=True) is True
    assert eng._rejected == 0, "a flat was counted as a flagged frame"
    assert not any("HFR" in msg for _lvl, msg, _src in bus_lines), (
        f"the HFR gate spoke about a flat: {bus_lines}")
    assert eng._recent_hfr == _WINDOW, "an accepted flat moved the median"


@pytest.mark.parametrize("window", [(), (3.0,), tuple(_WINDOW)],
                         ids=["empty", "short", "full"])
async def test_a_calibration_frame_never_moves_the_median(sim_hub, window):
    """A flat accepted into an EMPTY window (the case that seeds a median from
    flats), into a short one, and into a full one (the case that drags it)."""
    eng = _engine(sim_hub, window)
    for hfr in (3.5, 3.0, 2.2):
        assert eng._check_quality({"hfr": hfr}, calibration=True) is True
    assert eng._recent_hfr == list(window)


async def test_a_light_frame_is_still_gated_and_still_anchors_the_median(
        sim_hub):
    """The control: the fix tells the grader what kind of frame it is holding,
    it does not take the gate away from the lights. A poor light is refused
    and left out, a good one is accepted and is a sample."""
    eng = _engine(sim_hub, _WINDOW)
    assert eng._check_quality({"hfr": 9.0, "stars": 15}) is False
    assert eng._rejected == 1
    assert eng._recent_hfr == _WINDOW
    assert eng._check_quality({"hfr": 3.2}) is True
    assert eng._recent_hfr == _WINDOW + [3.2]


# ----------------------------------------------------- through a real engine run

class Camera:
    """``hub.capture`` is real; what it REPORTS about each saved exposure may
    be rewritten per frame type and ordinal, because a simulated dark has no
    stars to carry an HFR and a simulated flat's HFR is not the number a test
    needs. ``report(frame_type, n, info)`` edits the n-th (1-based) saved
    exposure of that frame type."""

    def __init__(self, hub, monkeypatch, report):
        self.shots: list[dict] = []
        self._n: dict[str, int] = {}
        real = hub.capture

        async def capture(exposure_s, gain, offset, binning, **kw):
            info = await real(exposure_s, gain, offset, binning, **kw)
            if kw.get("save", True):
                ft = kw.get("frame_type", "Light")
                n = self._n[ft] = self._n.get(ft, 0) + 1
                report(ft, n, info)
                self.shots.append({"frame_type": ft, "n": n,
                                   "path": info.get("saved_path")})
            return info

        monkeypatch.setattr(hub, "capture", capture)

    def of(self, frame_type: str) -> list[dict]:
        return [s for s in self.shots if s["frame_type"] == frame_type]


def _calibration_target(frame_type: str, count: int) -> Target:
    """A target as the engine builds its own, in the plan or not."""
    return Target(name=f"{frame_type} set", ra_hours=0.0, dec_deg=0.0,
                  calibration=True, center=False, autofocus_first=False,
                  steps=[ExposureStep(exposure_s=0.05, gain=100, offset=30,
                                      binning=1, count=count,
                                      frame_type=frame_type)])


@pytest.mark.parametrize("action", ["discard", "retake"])
@pytest.mark.parametrize("frame_type", ["Flat", "Dark"])
async def test_a_calibration_frame_with_a_large_hfr_is_kept(
        sim_hub, monkeypatch, frame_type, action):
    """Two throwaway frames, each reporting 9.0 px against a window of four
    lights at 3.0 px, with the escalation action that destroys a refused frame.
    Both are KEPT: on disk, counted, exposed once each (no retake), and the
    window is the one the lights built. Unfixed the first was unlinked
    (discard) or unlinked and shot again (retake)."""
    plan = SequencePlan(name="Graded night", guide=False, dither_every=0,
                        targets=[], hfr_reject_factor=1.5, **_only_hfr())
    throwaway = _calibration_target(frame_type, count=2)

    def report(ft, n, info):
        info["hfr"] = 9.0
        info["stars"] = 15

    cam = Camera(sim_hub, monkeypatch, report)
    eng = SequenceEngine(sim_hub)

    async def scheduled(p):
        eng._recent_hfr = list(_WINDOW)
        monkeypatch.setattr(eng, "_reject_action", lambda: action)
        await eng._run_calibration(0, throwaway)

    monkeypatch.setattr(eng, "_run_scheduled", scheduled)
    eng.start(plan)
    await eng._task

    shots = cam.of(frame_type)
    assert len(shots) == 2, (
        f"premise: one exposure per frame, no retake: {cam.shots}")
    assert all(Path(s["path"]).exists() for s in shots), (
        f"a {frame_type.lower()} with a large HFR was unlinked by the {action} "
        f"action: {shots}")
    assert eng._calibration_frames_done == 2
    assert eng._rejected == 0
    assert eng._recent_hfr == _WINDOW, (
        f"the {frame_type.lower()}s moved the lights' running median: "
        f"{eng._recent_hfr}")


async def test_the_lights_after_dusk_flats_are_judged_by_their_own_median(
        sim_hub, monkeypatch):
    """The real DUSK FLATS stage (six flats, each reporting 20.0 px) and then
    six lights: four good ones at 3.0 px, a poor one at 6.0 and a good one.

    The poor light is over 1.5 x the lights' own median of 3.0. Unfixed the six
    flats had seeded the window (nothing refuses a frame while it has fewer
    than four samples), so by the fifth light the window held the flats and the
    median was 20.0: the poor light was ACCEPTED, and the window the lights
    inherited held the flats' 20.0 px. Fixed the window is the lights' own,
    the poor light is refused and left out of it."""
    plan = SequencePlan(
        name="Graded night", guide=False, dither_every=0,
        hfr_reject_factor=1.5, **_only_hfr(),
        dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                 adu_target=400, count=3),
        targets=[Target(name="Alpha", ra_hours=0.7, dec_deg=41.0,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter=f, exposure_s=0.05,
                                            gain=100, offset=30, binning=1,
                                            count=3) for f in ("L", "R")])])
    light_hfr = {5: 6.0}

    def report(ft, n, info):
        info.pop("stars", None)
        if ft == "Flat":
            info["hfr"] = 20.0
        elif ft == "Light":
            info["hfr"] = light_hfr.get(n, 3.0)

    cam = Camera(sim_hub, monkeypatch, report)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    await eng._task

    assert len(cam.of("Flat")) == 6, f"premise: the stage ran: {cam.shots}"
    assert len(cam.of("Light")) == 6, f"premise: six lights: {cam.shots}"
    assert eng._calibration_frames_done == 6
    assert eng._rejected == 1, (
        f"{eng._rejected} frames flagged: the poor light is the one frame "
        f"above 1.5 x the lights' median, and it was judged against a window "
        f"the flats had seeded")
    assert eng._recent_hfr == [3.0] * 5, (
        f"the window the lights ended with is not the lights': "
        f"{eng._recent_hfr}")
