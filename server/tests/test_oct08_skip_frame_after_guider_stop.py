# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#848: a frame is not shot when the guider stopped itself during the
iteration that was about to shoot it.

2026-10-07, F+7: the first dither after walk 1 failed to settle, which
already proved the walk bad, and the run shot a 180 s frame on it anyway (RMS
690 arcsec). A guider that stops during a dither is not "busy", so the quiet
gate let the shutter open.

THE SKIP GATE (``SequenceEngine._run_step``): a guider that was guiding at
the top of the iteration and is not guiding at the shutter means the frame is
not taken; the next iteration's recovery re-centres and restarts guiding
first. Bounded: each skip needs a fresh active-to-inactive transition, which
needs a successful start, and recovery starts are capped by
``_MAX_GUIDING_RECOVERIES``. With ``recover_guiding`` off nothing would
restart guiding, so nothing is skipped, unless a pause stood the guider
down after the top read: the resume restarts it whatever ``recover_guiding``
says.

These drive the REAL ``_run_step`` on the sim hub; only the guider's calls
are scripted. The gates that read the sky, the clock and the meridian are
stubbed to no-ops so the cases do not depend on the hour they run at.

NAMED MUTANTS (sequence/engine.py byte backup, the suite's normal command,
restore + sha256; observed results in REPORT-P1.md):
 * M30 the skip gate removed.
 * M31 the ``recover_guiding`` term dropped from the gate.
 * M32 the ``guided_at_top`` term dropped from the gate.
 * F4 (fix round 1) the gate's ``or self._guiding_off_for_pause`` dropped,
   so a pause after the top read shoots the frame unguided under
   ``recover_guiding`` off.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import AppConfig
from astrodeck.devices.base import DeviceError, PierSide
from astrodeck.guide.base import GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence.engine import (
    _MAX_GUIDING_RECOVERIES, L_SKIP, SequenceEngine)
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

from _oct08_guider_harness import Logs

ITERATION_CAP = 8
SELF_STOP = "native guider: dither settle failed (guiding stopped itself)"


class _Capped(Exception):
    pass


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target():
    return Target(name="NGC 7331", ra_hours=1.0, dec_deg=40.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])


def _rig(sim_hub, monkeypatch, *, recover=True, stops_on_dither=(1,)):
    """An engine on the sim hub whose guider stops itself during the dithers
    numbered in ``stops_on_dither`` (1-based; "all" for every one)."""
    events: list[str] = []
    state = {"active": True, "dithers": 0, "iter": 0}
    g = sim_hub.guider

    async def is_active():
        return state["active"]

    async def start_guiding():
        events.append("start")
        state["active"] = True

    async def stop_guiding():
        state["active"] = False

    async def dither(pixels=3.0, settle=None):
        if not state["active"]:
            raise DeviceError("native guider: cannot dither when not guiding")
        state["dithers"] += 1
        events.append("dither")
        if stops_on_dither == "all" or state["dithers"] in stops_on_dither:
            state["active"] = False
            raise DeviceError(SELF_STOP)

    monkeypatch.setattr(g, "is_active", is_active)
    monkeypatch.setattr(g, "start_guiding", start_guiding)
    monkeypatch.setattr(g, "stop_guiding", stop_guiding)
    monkeypatch.setattr(g, "dither", dither)
    monkeypatch.setattr(g, "stats", lambda: GuideStats(
        guiding=state["active"],
        phase="guiding" if state["active"] else "idle"))

    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True, recover_guiding=recover, dither_every=1)
    cfg = AppConfig()
    cfg.guide.dither_settle_fail_limit = 0     # the walking-field hold off
    e._cfg = cfg
    e._done = {}
    e._frames_done = 0
    e._frames_since_dither = 1                 # a dither is due on frame 1

    async def _noop(*a, **k):
        return None

    for name in ("_safety_gate", "_enforce_altitude_floor",
                 "_maybe_meridian_flip", "_enforce_flip_owed"):
        monkeypatch.setattr(e, name, _noop)
    monkeypatch.setattr(e, "_enforce_stop_boundary", lambda target: None)

    real_checkpoint = e._checkpoint

    async def checkpoint():
        state["iter"] += 1
        events.append(f"iter{state['iter']}")
        if state["iter"] > ITERATION_CAP:
            raise _Capped("the frame loop never reached the shutter")
        await real_checkpoint()
    monkeypatch.setattr(e, "_checkpoint", checkpoint)

    real_begin = e._begin_frame

    def begin(ti, si, exposure_s):
        events.append("capture")
        return real_begin(ti, si, exposure_s)
    monkeypatch.setattr(e, "_begin_frame", begin)
    return e, events, state


@pytest.mark.asyncio
async def test_a_frame_is_not_shot_when_guiding_stopped_during_the_dither(
        sim_hub, monkeypatch):
    """E1. Iteration 1: guiding at the top, the dither stops the guider, so no
    frame and the skip is said. Iteration 2: recovery restarts guiding BEFORE
    the frame is taken."""
    logs = Logs(monkeypatch)
    e, events, _state = _rig(sim_hub, monkeypatch)
    t = _target()
    await e._run_step(0, 0, t, t.steps[0])
    first = events[:events.index("iter2")]
    assert "capture" not in first, events
    assert logs.has(L_SKIP)
    second = events[events.index("iter2"):]
    assert second.index("start") < second.index("capture"), events
    assert events.count("capture") == 1


@pytest.mark.asyncio
async def test_no_skip_when_the_plan_does_not_recover(sim_hub, monkeypatch):
    """E2. ``recover_guiding`` off: nothing would restart guiding, so the
    frame is taken as before, under the operator's guiding_action."""
    logs = Logs(monkeypatch)
    e, events, _state = _rig(sim_hub, monkeypatch, recover=False)
    t = _target()
    await e._run_step(0, 0, t, t.steps[0])
    assert "iter2" not in events, events
    assert events.count("capture") == 1
    assert not logs.has(L_SKIP)


@pytest.mark.asyncio
async def test_the_skip_gate_is_bounded_when_recovery_is_spent(
        sim_hub, monkeypatch):
    """E3. Every restart succeeds and every dither stops the guider again:
    one skip per recovery attempt, then, with the attempts spent, the guider
    is down at the top of the iteration and the frame is shot (as the
    operator's guiding_action says). Exactly one capture, in
    2 + _MAX_GUIDING_RECOVERIES iterations."""
    logs = Logs(monkeypatch)
    e, events, state = _rig(sim_hub, monkeypatch, stops_on_dither="all")
    t = _target()
    await e._run_step(0, 0, t, t.steps[0])
    assert events.count("capture") == 1, events
    assert state["iter"] == 2 + _MAX_GUIDING_RECOVERIES
    assert len([m for m in logs.at("warning") if m == L_SKIP]) == \
        1 + _MAX_GUIDING_RECOVERIES


@pytest.mark.asyncio
async def test_a_pause_after_the_top_read_skips_the_frame_without_recovery(
        sim_hub, monkeypatch):
    """E4 (review A, mutant F4: the gate back to ``recover_guiding`` alone).
    A pause that lands after ``guided_at_top`` was read (here in the cooling
    gate, as ``_cool_and_wait`` does) stands the guider down. The plan does
    not recover guiding, but the resume restarts it at the next iteration
    whatever ``recover_guiding`` says, so the frame is skipped there and shot
    guided after the restart, never unguided before it."""
    logs = Logs(monkeypatch)
    e, events, _state = _rig(sim_hub, monkeypatch, recover=False,
                             stops_on_dither=())
    tel = sim_hub.devices["telescope"]

    async def get_position():
        return 1.0, 40.0

    async def pier_side():
        return PierSide.EAST
    monkeypatch.setattr(tel, "get_position", get_position)
    monkeypatch.setattr(tel, "pier_side", pier_side)
    paused = {"done": False}

    async def cooling():
        if paused["done"]:
            return
        paused["done"] = True
        e.pause()
        wait = asyncio.ensure_future(SequenceEngine._checkpoint(e))
        for _ in range(20):
            await asyncio.sleep(0)
        events.append("paused")
        e.resume()
        await asyncio.wait_for(wait, timeout=2.0)
    monkeypatch.setattr(e, "_enforce_cooling", cooling)
    t = _target()
    await e._run_step(0, 0, t, t.steps[0])
    cut = events.index("iter2") if "iter2" in events else len(events)
    first = events[:cut]
    assert "paused" in first, events
    assert "capture" not in first, events
    assert logs.has(L_SKIP)
    second = events[events.index("iter2"):]
    assert second.index("start") < second.index("capture"), events
    assert events.count("capture") == 1
