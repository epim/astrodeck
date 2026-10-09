# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#849 (RULING R1, OWNER-VETO): a paused run stops guiding.

2026-10-07: the run was paused and the guider kept pulsing the AM5 for about
an hour, until it was stopped by hand before a re-home; on that mount every
east pulse desynchronises the pointing model. ``pause()`` never touched the
guider, and the run parked in ``_checkpoint`` with nothing looking.

Now: at the paused frame boundary ``_checkpoint`` stands the guider down
(the in-flight exposure finished guided) and notes where the mount was. It
does NOT restart guiding. The frame loop does, through
``_maybe_recover_guiding``, which runs after the stop boundary, the altitude
floor, the safety, reconnect and flip gates: re-centring first if the target
centres, uncharged as a recovery attempt, and also for a plan with
``recover_guiding`` off. A target that does not re-centre is skipped if the
mount moved (or cannot be read) across the pause. No coordinates are ever
logged (the pose values below are test fixtures, not a site).

NAMED MUTANTS (sequence/engine.py byte backup, the suite's normal command,
restore + sha256; observed results in REPORT-P1.md):
 * M41 the stand-down removed from ``_checkpoint``.
 * M42 a restart added back into ``_checkpoint`` after the wait.
 * M43 the recovery increment not guarded by ``resumed``.
 * M44 ``or resumed`` dropped from the plan test.
 * M45 the moved-mount test removed.
 * M-T28 the guider-active test removed from ``_checkpoint``.
 * M-T29a the ``except Exception`` around the restart removed; M-T29b the
   decrement not guarded by ``resumed``.
 * M-T30 the plan test removed from ``_checkpoint``.
 * M-T31 ``_stand_down_guider`` always returns True.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import DeviceError, PierSide
from astrodeck.guide.base import GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence.engine import (
    L_PAUSE_MOVED, L_PAUSE_NOSTOP, L_PAUSE_STOP, L_RESUME_FAIL,
    L_RESUME_RESTART, PAUSE_MOVED_REASON, SequenceEngine, StopTarget)
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

from _oct08_guider_harness import Logs


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


class _Guider:
    """The guider calls the engine makes, recorded; ``active`` scriptable."""

    def __init__(self, hub, monkeypatch, *, active=True, stop_raises=False,
                 start_raises=None):
        self.calls: list[str] = []
        self.active = active
        g = hub.guider

        async def stop_guiding():
            self.calls.append("stop")
            if stop_raises:
                raise DeviceError("the guider would not stop")
            self.active = False

        async def start_guiding():
            self.calls.append("start")
            if start_raises is not None:
                raise start_raises
            self.active = True

        async def is_active():
            return self.active

        monkeypatch.setattr(g, "stop_guiding", stop_guiding)
        monkeypatch.setattr(g, "start_guiding", start_guiding)
        monkeypatch.setattr(g, "is_active", is_active)
        monkeypatch.setattr(g, "stats", lambda: GuideStats(
            guiding=self.active, phase="guiding" if self.active else "idle"))


class _Pose:
    """Scriptable mount position and pier side for ``_read_pose``."""

    def __init__(self, hub, monkeypatch, ra=1.0, dec=40.0, pier="east"):
        self.ra, self.dec, self.pier = ra, dec, pier
        tel = hub.devices["telescope"]

        async def get_position():
            return self.ra, self.dec

        async def pier_side():
            return {"east": PierSide.EAST, "west": PierSide.WEST}[self.pier]

        monkeypatch.setattr(tel, "get_position", get_position)
        monkeypatch.setattr(tel, "pier_side", pier_side)


def _target(center=True):
    return Target(name="NGC 7331", ra_hours=1.0, dec_deg=40.0, center=center,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])


def _engine(hub, *, recover=True, guide=True):
    e = SequenceEngine(hub)
    e.plan = SequencePlan(guide=guide, recover_guiding=recover)
    return e


async def _pause_and_park(e) -> asyncio.Future:
    e.pause()
    task = asyncio.ensure_future(e._checkpoint())
    for _ in range(20):
        await asyncio.sleep(0)
    return task


@pytest.mark.asyncio
async def test_a_pause_stops_guiding_and_checkpoint_never_restarts_it(
        sim_hub, monkeypatch):
    """T27. Pause: one stop, the checkpoint waits, the log says guiding
    stopped. Resume: the checkpoint returns and starts NOTHING; the restart
    is left to the frame loop's recovery, after its gates."""
    logs = Logs(monkeypatch)
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    assert g.calls == ["stop"]
    assert not task.done()
    assert logs.has(L_PAUSE_STOP)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert g.calls == ["stop"], "the checkpoint restarted guiding itself"
    assert e._guiding_off_for_pause is True


@pytest.mark.asyncio
async def test_the_frame_loop_restarts_guiding_uncharged(sim_hub, monkeypatch):
    """T27a. After the pause, the frame loop's recovery re-centres a target
    that centres, then restarts guiding, without charging an attempt."""
    logs = Logs(monkeypatch)
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    order: list[str] = []

    async def fake_center(ra, dec, **kw):
        order.append("center")
    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)
    real_start = sim_hub.guider.start_guiding

    async def start():
        order.append("start")
        await real_start()
    monkeypatch.setattr(sim_hub.guider, "start_guiding", start)
    await e._maybe_recover_guiding(_target(center=True))
    assert order == ["center", "start"]
    assert e._guiding_recoveries == 0
    assert logs.has(L_RESUME_RESTART)
    assert e._guiding_off_for_pause is False


@pytest.mark.asyncio
async def test_a_resume_restarts_guiding_even_without_recovery(
        sim_hub, monkeypatch):
    """T27b. ``recover_guiding`` off: the resume still gives back what the
    pause took away, once; a second call (flag consumed, guider still down)
    starts nothing."""
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub, recover=False)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    monkeypatch.setattr(sim_hub, "goto_and_center",
                        lambda *a, **k: asyncio.sleep(0))
    await e._maybe_recover_guiding(_target(center=False))
    assert g.calls == ["stop", "start"]
    g.active = False
    await e._maybe_recover_guiding(_target(center=False))
    assert g.calls == ["stop", "start"]


@pytest.mark.parametrize("after", [
    (1.0, 40.0, "west"),            # the pier side changed (a flip, a home)
    (1.0 + 5.0 / 15.0, 40.0, "east"),   # five degrees away (a goto)
])
@pytest.mark.asyncio
async def test_a_moved_mount_on_a_target_that_does_not_recentre_stops_the_target(
        sim_hub, monkeypatch, after):
    """T27c. A target that does not re-centre, and a mount that is not where
    it was when the run paused: guiding is not restarted on the wrong field;
    the target stops with fixed words, and no position is printed."""
    logs = Logs(monkeypatch)
    g = _Guider(sim_hub, monkeypatch, active=True)
    pose = _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    pose.ra, pose.dec, pose.pier = after
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    with pytest.raises(StopTarget) as ei:
        await e._maybe_recover_guiding(_target(center=False))
    assert str(ei.value) == PAUSE_MOVED_REASON
    assert "start" not in g.calls
    assert logs.has(L_PAUSE_MOVED)


@pytest.mark.asyncio
async def test_a_tracking_mount_across_a_pause_restarts_guiding(
        sim_hub, monkeypatch):
    """T27c control. The same pose, give or take 0.2 degrees of tracking:
    guiding restarts."""
    g = _Guider(sim_hub, monkeypatch, active=True)
    pose = _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    pose.dec = 40.2
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    await e._maybe_recover_guiding(_target(center=False))
    assert g.calls == ["stop", "start"]


@pytest.mark.asyncio
async def test_an_operator_restart_during_the_pause_is_left_alone(
        sim_hub, monkeypatch):
    """T27d. The operator restarted guiding by hand during the pause: the
    recovery finds it active, starts nothing, and the flag is consumed."""
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    g.active = True
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    await e._maybe_recover_guiding(_target())
    assert g.calls == ["stop"]
    assert e._guiding_off_for_pause is False


@pytest.mark.asyncio
async def test_a_pause_with_nothing_guiding_touches_nothing(
        sim_hub, monkeypatch):
    """T28. Nothing guiding at the pause: no stop, no flag."""
    g = _Guider(sim_hub, monkeypatch, active=False)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert g.calls == []
    assert e._guiding_off_for_pause is False


@pytest.mark.asyncio
async def test_a_failed_restart_after_resume_is_not_fatal(sim_hub, monkeypatch):
    """T29. The restart fails: the frame loop goes on (recovery takes over at
    the next frame if the plan recovers), the operator line and the evidence
    line are separate, and no attempt was charged."""
    logs = Logs(monkeypatch)
    g = _Guider(sim_hub, monkeypatch, active=True,
                start_raises=DeviceError("x"))
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    await e._maybe_recover_guiding(_target(center=False))
    assert "start" in g.calls
    assert logs.has(L_RESUME_FAIL)
    assert logs.has("resume: the restart failed with: x")
    assert e._guiding_recoveries == 0


@pytest.mark.asyncio
async def test_a_failed_restart_under_cloud_gives_back_nothing(
        sim_hub, monkeypatch):
    """T29b. The same failure with the sky reading cloudy afterwards: an
    ordinary recovery is given its attempt back, but a resume charged
    nothing, so nothing is taken off the count."""
    g = _Guider(sim_hub, monkeypatch, active=True,
                start_raises=DeviceError("x"))
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    e._guiding_recoveries = 1

    async def sky(target, *, why, after_failure=False):
        return bool(after_failure)
    monkeypatch.setattr(e, "_sky_closed_before_recovery", sky)
    await e._maybe_recover_guiding(_target(center=False))
    assert "start" in g.calls
    assert e._guiding_recoveries == 1


@pytest.mark.asyncio
async def test_an_unguided_plan_leaves_the_guider_alone(sim_hub, monkeypatch):
    """T30. A plan that does not guide: the pause does not touch a guider the
    operator is running by hand."""
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub, guide=False)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert g.calls == []


@pytest.mark.asyncio
async def test_stand_down_guider_reports_success(sim_hub, monkeypatch):
    """T31. ``_stand_down_guider`` says whether the stop went through, and a
    guider that would not stop is said aloud at the pause."""
    logs = Logs(monkeypatch)
    _Guider(sim_hub, monkeypatch, active=True)
    e = _engine(sim_hub)
    assert await e._stand_down_guider() is True
    _Guider(sim_hub, monkeypatch, active=True, stop_raises=True)
    assert await e._stand_down_guider() is False
    _Pose(sim_hub, monkeypatch)
    task = await _pause_and_park(e)
    assert logs.has(L_PAUSE_NOSTOP)
    assert e._guiding_off_for_pause is False
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)


# ----------------------------------------------- fix round 1 (review A)


@pytest.mark.asyncio
async def test_a_second_pause_stops_a_guider_restarted_by_hand(
        sim_hub, monkeypatch):
    """FR1-1 (review A, mutant F1: the ``not _guiding_off_for_pause`` term
    restored in ``_checkpoint``). Pause, resume, and the target stops before
    its recovery runs (its window closed), so the stand-down flag is never
    consumed. The operator restarts guiding by hand, and the run pauses
    again: the guider is pulsing the mount through that pause, so it must be
    stood down again."""
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert e._guiding_off_for_pause is True, "premise: the flag is unconsumed"
    g.active = True                             # restarted by hand
    task = await _pause_and_park(e)
    try:
        assert g.calls == ["stop", "stop"]
        assert g.active is False
    finally:
        e.resume()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_a_hop_forgets_the_last_targets_pause(sim_hub, monkeypatch):
    """FR1-2 (review A, mutant F2: the reset removed from ``_hop``). A pause
    stood the guider down on one target, which then stopped before its
    recovery ran. The scheduler hops to the next target, somewhere else,
    whose own guide start fails. Its recovery is an ordinary one (charged),
    and it must not compare this target's pose with the old target's pause
    and skip it as "moved"."""
    logs = Logs(monkeypatch)
    g = _Guider(sim_hub, monkeypatch, active=True)
    pose = _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert e._guiding_off_for_pause is True, "premise: the flag is unconsumed"

    async def setup(ti, target):
        pose.ra, pose.dec = 5.0, 10.0           # the next target's field
        g.active = False                        # its own guide start failed
    monkeypatch.setattr(e, "_setup_target", setup)
    nxt = _target(center=False)
    await e._hop(1, nxt)
    await e._maybe_recover_guiding(nxt)
    assert not logs.has(L_PAUSE_MOVED)
    assert g.calls[-1] == "start"
    assert e._guiding_recoveries == 1, "an ordinary recovery, charged"


@pytest.mark.asyncio
async def test_a_run_start_forgets_an_earlier_runs_pause(sim_hub, monkeypatch):
    """FR1-3 (review A, mutant F3: the reset removed from ``start``). The
    engine outlives the run: a run aborted while paused must not hand its
    stand-down, or the pose it noted, to the next run."""
    e = SequenceEngine(sim_hub)
    e._guiding_off_for_pause = True
    e._pause_pose = (1.0, 40.0, "east")
    plan = SequencePlan(name="fr1-3", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[_target(center=False)])
    e.start(plan)
    try:
        assert e._guiding_off_for_pause is False
        assert e._pause_pose is None
    finally:
        await e.abort()


_PositionUnknownStop = getattr(engine_mod, "PositionUnknownStop", None)


@pytest.mark.skipif(
    _PositionUnknownStop is None,
    reason="MERGE-TREE TEST: needs P2's ruling R9 gate (PositionUnknownStop "
           "and _recentre_for_hold); it runs once P1 is merged onto P2")
@pytest.mark.asyncio
async def test_a_resume_never_recentres_a_mount_whose_position_is_unknown(
        sim_hub, monkeypatch):
    """FR1-4 (review A, the merge seam). The resume adds a re-centring goto
    right after a pause, which is when an operator may re-home or power-cycle
    the mount. With the driver saying its position is unknown, the resumed
    recovery must end the run without a goto and without restarting guiding
    (P2 ruling R9). MUTANT for the orchestrator, on the merged tree: route the
    resumed branch around ``_recentre_for_hold`` (or drop its
    ``_position_unknown`` check); this test must go red."""
    g = _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch)
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    monkeypatch.setattr(sim_hub.devices["telescope"], "position_known", False,
                        raising=False)
    gotos: list[tuple] = []

    async def fake_center(*a, **k):
        gotos.append(a)
    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)
    with pytest.raises(_PositionUnknownStop):
        await e._maybe_recover_guiding(_target(center=True))
    assert gotos == []
    assert "start" not in g.calls
