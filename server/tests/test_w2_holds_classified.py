# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.2 (#1108: #1093 #721 #817): the centring hold and the no-light hold
ask the starless-frame classifier instead of guessing why a solve found
nothing.

THE NIGHTS IT COMES FROM. 2026-10-18 and 2026-10-19: a mosaic's target stood
behind a tree and every hold line said "the sky or the geometry is to blame"
(#1093); a rig with no plate solver held a single target for its whole window
under a sentence about waiting for light (#721).

WHAT IT DOES NOW. The hub judges the frame a failed solve ran on through
``imaging.frame_class.classify`` and the result carries the class
(``solve_frame_class``, #1182); the engine reads it, and the hold says what
the frame shows:

  * obstructed: an obstruction in the foreground, not the sky;
  * no_light: the hub's own light verdict (``solve_light`` "dark", judged
    against the right reference, a self-shot included);
  * cloud: starless and low contrast;
  * solver_missing: the hold ENDS as a fault on the first attempt, because a
    configuration problem waits for nobody.

The frames are the seeded synthetic ones of ``test_w2_frame_class.py``
(``fixtures/frame_class/synth_frames.py``); none is from a real rig frame. A
result that carries no ``solve_failed`` (a stub, an aborted goto) is never
classified: it carries no class, because the hub holds no frame of a solve that
never ran.

MUTANT RUNS, 2026-11-04, from a byte backup of ``astrodeck/sequence/engine.py``,
restored and sha256-verified each time:

  * the classifier skipped in the hold (``cls = await
    self._classify_failed_solve(hop_centring)`` replaced by ``cls = None`` in
    ``_hold_for_light``). The obstruction night then blames the sky, observed
    verbatim by ``test_an_obstructed_frame_is_said_to_be_an_obstruction``:

        AssertionError: no line named an obstruction

    and the missing-solver, no-light, cloud and setup cases go red with it.

  * the whole-pass site skipped (``await self._note_pass_frame_class(member,
    result)`` replaced by ``pass``): ``test_a_pass_that_failed_on_an_obstruction_
    says_so`` fails with

        AssertionError: the pass named nothing about its frames
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.devices.base import CameraFrame
from astrodeck.events import bus
from astrodeck.hub import SOLVE_REASON_SOLVER_MISSING, Hub
from astrodeck.imaging.frame_class import FrameClass
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target

_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures" / "frame_class"
if str(_FIXTURES) not in sys.path:
    sys.path.append(str(_FIXTURES))
import synth_frames as synth  # noqa: E402

#: What a failed centring looks like from the real hub: no figure, and the
#: ``solve_failed`` key that says the solve was attempted and found nothing.
FAILED = {"centered": False, "error_arcmin": None, "attempts": 1,
          "solve_failed": True}


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _frame(data: np.ndarray) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=4.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=-5.0,
                       timestamp=time.time())


def _target(**kw) -> Target:
    base = dict(name="NGC-test", ra_hours=5.0, dec_deg=10.0, center=True,
                autofocus_first=True,
                steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    base.update(kw)
    return Target(**base)


def _lines(who: str, *fragments: str, level: str | None = None) -> list[str]:
    """Every log line about the target named ``who`` carrying all of
    ``fragments``, from the whole ring. The ring is shared by the session
    (``bus.log_history`` is a copy), so each test names its own target and
    counts only that target's lines."""
    out = []
    for e in bus.log_history:
        if level is not None and e["data"].get("level") != level:
            continue
        msg = e["data"].get("message", "")
        if msg.startswith(f"{who}:") and all(f in msg for f in fragments):
            out.append(msg)
    return out


def _judged(result: dict, frame):
    """The centring result as the hub now returns it: the class of the frame
    the solve ran on rides in ``solve_frame_class`` (#1182)."""
    from astrodeck.imaging.frame_class import classify
    if frame is None or not result.get("solve_failed"):
        return dict(result)
    return dict(result) | {"solve_frame_class": classify(frame).value}


def _scripted(sim_hub, monkeypatch, result: dict, frame: np.ndarray | None):
    """Every centring attempt fails the same way, its result carrying the
    class of ``frame`` as the hub would; returns the call counter."""
    calls = {"n": 0}
    result = _judged(result, frame)

    async def goto(ra_hours, dec_deg, **kw):
        calls["n"] += 1
        return dict(result)

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    # A different frame on the hub: the verdict must come from the class
    # the result carries, not from whatever the hub last held (#1182).
    sim_hub.last_frame = _frame(synth.stars_frame())
    return calls


# ---------------------------------------------------------------- the hold

async def test_an_obstructed_frame_is_said_to_be_an_obstruction(
        sim_hub, monkeypatch):
    """The #1093 night through the real hold: a solve that found nothing on a
    frame with a treeline across it. The hold names an obstruction, not the
    sky, says it once, and does not call it cloud or a lack of light. It
    still retries (the target may rise out of it), bounded as it always was.
    """
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    calls = _scripted(sim_hub, monkeypatch, FAILED, synth.obstructed_frame())

    result = await engine._hold_for_light(_target(name="tree-night"), None,
                                          _judged(FAILED, synth.obstructed_frame()))

    assert not result["centered"]
    assert calls["n"] == engine._NO_LIGHT_MAX_RETRIES
    named = _lines("tree-night", "obstruction")
    assert named, "no line named an obstruction"
    assert len(named) == 1, named
    line = named[0]
    assert "not the sky" in line
    assert "cloud" not in line and "no light" not in line
    assert not _lines("tree-night", "holding for light before autofocus")


async def test_a_cloud_frame_is_said_to_be_cloud_and_still_held(
        sim_hub, monkeypatch):
    """Cloud keeps its path: the same retry hold, now saying what it saw."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    calls = _scripted(sim_hub, monkeypatch, FAILED, synth.cloud_frame())

    await engine._hold_for_light(_target(name="overcast-night"), None,
                                 _judged(FAILED, synth.cloud_frame()))

    assert calls["n"] == engine._NO_LIGHT_MAX_RETRIES
    named = _lines("overcast-night", "cloud", "nothing to solve")
    assert len(named) == 1, named
    assert "obstruction" not in named[0]


async def test_a_dark_verdict_from_the_hub_is_said_to_be_no_light(
        sim_hub, monkeypatch):
    """No light is the hub's own verdict (``solve_light`` "dark"), judged
    against the reference it holds, a self-shot included: the classifier alone
    reads a black frame as cloud when it is given no reference. The hold says
    no light and keeps holding."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    dark = FAILED | {"solve_light": "dark", "solve_dark_kind": "dark_master"}
    calls = _scripted(sim_hub, monkeypatch, dark, synth.no_light_frame())

    await engine._hold_for_light(_target(name="capped-night"), None,
                                 _judged(dark, synth.no_light_frame()))

    assert calls["n"] == engine._NO_LIGHT_MAX_RETRIES
    named = _lines("capped-night", "no light", "nothing to solve")
    assert len(named) == 1, named
    assert "obstruction" not in named[0]


async def test_an_obstruction_is_never_called_no_light_even_with_the_dark_verdict(
        sim_hub, monkeypatch):
    """A frame mostly behind a tree reads at the black level in its median,
    so the hub's light check can say "dark" for it (the trap that makes the
    classifier test an obstruction first). The hold still says obstruction."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    dark = FAILED | {"solve_light": "dark", "solve_dark_kind": "dark_master"}
    _scripted(sim_hub, monkeypatch, dark, synth.obstructed_frame())

    await engine._hold_for_light(_target(name="dark-tree"), None,
                                 _judged(dark, synth.obstructed_frame()))

    assert _lines("dark-tree", "obstruction", "nothing to solve")
    assert not _lines("dark-tree", "no light", "nothing to solve")


async def test_a_missing_solver_ends_the_hold_as_a_fault_on_the_first_attempt(
        sim_hub, monkeypatch):
    """#721: no plate solver on the rig is a fault no waiting fixes. The hold
    makes no retry at all (no goto, no sleep) and says so at error level, so
    the operator is told, instead of holding for the target's whole window."""
    engine = SequenceEngine(sim_hub)
    missing = FAILED | {"solve_reason": SOLVE_REASON_SOLVER_MISSING}
    calls = _scripted(sim_hub, monkeypatch, missing, None)

    result = await engine._hold_for_light(_target(name="no-solver"), None,
                                          dict(missing))

    assert calls["n"] == 0
    assert result == missing
    faults = _lines("no-solver", "no plate solver", level="error")
    assert len(faults) == 1, faults
    assert "will not clear" in faults[0]
    assert not _lines("no-solver", "holding for light before autofocus")


async def test_a_result_with_no_failed_solve_is_never_read_from_pixels(
        sim_hub, monkeypatch):
    """A goto that never solved (an aborted or refused one) has no frame of
    its own. Only a result that says its solve failed (``solve_failed``) is
    classified, so such a result keeps the generic hold line even if a class
    rides it (an obstructed one here)."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    plain = {"centered": False, "error_arcmin": None,
             "solve_frame_class": FrameClass.OBSTRUCTED.value}
    _scripted(sim_hub, monkeypatch, plain, None)

    await engine._hold_for_light(_target(name="plain"), None, dict(plain))

    assert _lines("plain", "nothing to solve", "holding for light")
    assert not _lines("plain", "obstruction")


async def test_a_frame_the_classifier_cannot_name_keeps_the_generic_hold_line(
        sim_hub, monkeypatch):
    """A clear star field or a frame with no verdict (unknown) says nothing
    new: the hold keeps the sentence it always had."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    engine = SequenceEngine(sim_hub)
    _scripted(sim_hub, monkeypatch, FAILED, synth.stars_frame())

    await engine._hold_for_light(_target(name="field"), None,
                                 _judged(FAILED, synth.stars_frame()))

    assert _lines("field", "nothing to solve", "holding for light")
    assert not _lines("field", "obstruction")


# ------------------------------------------------- through setup, end to end

async def _run_setup(sim_hub, monkeypatch, name: str, result: dict,
                     frame: np.ndarray | None) -> tuple[dict, dict, list]:
    """One single target called ``name`` through the real ``start()`` ->
    ``_setup_target``, centring failing as ``result`` says. Returns the call counter, the final
    state and the autofocus labels."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **kw: {"start_unix": time.time() - 600.0,
                          "end_unix": time.time() + 3600.0, "mean_alt": 50.0})
    engine = SequenceEngine(sim_hub)
    calls = _scripted(sim_hub, monkeypatch, result, frame)
    af_labels: list[str] = []

    async def stub_autofocus(label, **kw):
        af_labels.append(label)
        return True

    monkeypatch.setattr(engine, "_autofocus", stub_autofocus)
    from _simhub import SUN_PROOF_TARGET
    plan = SequencePlan(
        name="w2-2-setup", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name=name, ra_hours=SUN_PROOF_TARGET[0],
                        dec_deg=SUN_PROOF_TARGET[1], center=True,
                        autofocus_first=True,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])
    engine.start(plan)
    import asyncio
    deadline = asyncio.get_event_loop().time() + 10.0
    while engine.running and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)
    state = dict(engine.state)
    await engine.abort()
    return calls, state, af_labels


async def test_setup_stops_a_target_whose_solver_is_missing_without_holding(
        sim_hub, monkeypatch):
    """The first centring says the rig has no plate solver. Setup makes that
    one attempt, never holds, never spends the night's autofocus on the
    target, and stops it with the fixed sentence."""
    missing = FAILED | {"solve_reason": SOLVE_REASON_SOLVER_MISSING}
    calls, state, af_labels = await _run_setup(
        sim_hub, monkeypatch, "roofline-no-solver", missing, None)

    assert calls["n"] == 1, "a missing solver was retried"
    assert af_labels == [], "autofocus ran on a target that cannot be centred"
    stops = _lines("roofline-no-solver", "stopping this target",
                   "no plate solver")
    assert stops, _lines("roofline-no-solver")
    assert not _lines("roofline-no-solver", "still no light after holding")


async def test_setup_names_the_obstruction_on_the_whole_hold(
        sim_hub, monkeypatch):
    """The #1093 shape on a single target, through setup: every retry fails on
    a treeline frame; the night log says obstruction and never the old
    sentence about waiting for light."""
    calls, _state, _af = await _run_setup(
        sim_hub, monkeypatch, "roofline-tree", FAILED,
        synth.obstructed_frame())

    assert calls["n"] >= 1 + engine_mod.SequenceEngine._NO_LIGHT_MAX_RETRIES
    assert _lines("roofline-tree", "obstruction")


# ------------------------------------------------- the whole-pass centring hold

async def test_a_pass_that_failed_on_an_obstruction_says_so(
        group_hub, monkeypatch):
    """The #1093 night itself: a mosaic whose every panel fails to centre on
    a treeline frame. The pass closes in the centring hold and its sentence
    names the obstruction in place of "the sky or the geometry"; there is no
    second line, and the sentence says it of every panel tried."""
    group_hub.last_frame = _frame(synth.stars_frame())

    def goto(who, n, result):
        return {**result, **_judged(FAILED, synth.obstructed_frame())}

    night = Night(group_hub, monkeypatch, goto=goto)
    try:
        night.arm()
        night.engine.start(grid_plan(rows=2, cols=2))
        assert await night.until(T0 + 5.0), "the run ended early"
        await night.finish()
    finally:
        await night.close()

    holds = night.said("centring failed on every one of the 4 panels")
    assert holds, "the pass closed in no centring hold"
    first = holds[0]
    assert "an obstruction in the foreground is to blame" in first, first
    assert "the sky or the geometry" not in first
    assert f"{GROUP_NAME}:" in first
    assert night.said("solve frames") == [], "a second line was said"
