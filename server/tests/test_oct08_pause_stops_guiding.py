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


# ------------------------------- merge round (P1 final re-review, finding 1)
#
# NAMED MUTANTS (sequence/engine.py byte backup, the suite's normal command,
# restore + sha256; runner p1m_mutants.py in the oct08 scratch directory):
#  * MF1 the `_repose_for_pause` call after the flip's side read removed.
#  * MF2 the `_repose_for_pause` call on the flip's park/unpark path removed.
#  * MS1 the `_repose_for_pause` call at the end of `_setup_target` removed.


@pytest.fixture
async def flip_hub(tmp_path, monkeypatch):
    """test_the_flip_stops_paying_for_itself.py's own hub (as
    test_850_engine_sync_refused.py borrows it): the config store isolated,
    a configured site so the flip gate can place the meridian."""
    from _simhub import a_real_site
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    a_real_site(monkeypatch)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@pytest.mark.asyncio
async def test_a_flip_after_a_pause_does_not_read_as_a_moved_mount(
        flip_hub, monkeypatch, bus_lines):
    """MR-1 (P1's final re-review). The run paused, the target passed its
    flip point, and on resume the frame loop's flip gate flipped it: the pier
    side changed by the engine's own re-slew. The pause had stood the guider
    down, so the hub's flip did not restart it, and the resumed recovery
    compared the pause's pose with the flipped one and skipped a target that
    does not re-centre with PAUSE_MOVED_REASON, for the night. The flip must
    refresh the pose: the recovery restarts guiding, uncharged, and raises
    nothing.

    Drives the REAL flip gate and ``hub.meridian_flip`` (only the slew is
    faked, and it swaps the pier side), then the REAL recovery. Lines are
    read through ``bus_lines``: the flip's own lines pass ``site_derived``,
    which the harness's ``Logs`` spy does not take.

    MUTANT MF1 (the refresh after the flip removed): RED -
        astrodeck.sequence.engine.StopTarget: the mount is not where it was
        when the run paused
    """
    import test_the_flip_stops_paying_for_itself as flp

    def said(needle: str) -> bool:
        return any(needle in m for _lv, m, _src in bus_lines)

    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)
    g = st["guider"]
    assert t.center is False, "premise: a target that does not re-centre"
    task = await _pause_and_park(e)
    assert g.calls == ["stop"], g.calls
    side_at_pause = st["side"]
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert e._guiding_off_for_pause is True

    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["gotos"], "premise: the flip must have re-slewed"
    assert st["side"] != side_at_pause, "premise: the pier side changed"
    assert "start" not in g.calls, (
        f"premise: the hub's flip restarted a guider the pause stood down: "
        f"{g.calls}")
    assert e._guiding_off_for_pause is True, "premise: the flag is unconsumed"

    await e._maybe_recover_guiding(t)
    assert g.calls[-1] == "start", g.calls
    assert g.calls.count("start") == 1, g.calls
    assert e._guiding_recoveries == 0, "a resume is not charged"
    assert not said(L_PAUSE_MOVED)
    assert said(L_RESUME_RESTART)


@pytest.mark.asyncio
async def test_a_flip_recovered_by_a_park_after_a_pause_does_not_read_as_moved(
        flip_hub, monkeypatch, bus_lines):
    """MR-1b. The same, through the flip's other way of placing the mount:
    the flip's goto is refused by a mount at its limit that is not tracking,
    and the park/unpark recovery re-slews onto the far side. The flip gate
    returns early on that path (the latch left armed), so it needs its own
    refresh. The flip gate and the recovery after it are the REAL code; only
    the hub's flip (refused) and the park/unpark recovery (which re-slews
    the mount onto the other pier side) are stood in for.

    MUTANT MF2 (the refresh on the recovery path removed): RED -
        astrodeck.sequence.engine.StopTarget: the mount is not where it was
        when the run paused
    """
    import test_the_flip_stops_paying_for_itself as flp

    def said(needle: str) -> bool:
        return any(needle in m for _lv, m, _src in bus_lines)

    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)
    g = st["guider"]
    task = await _pause_and_park(e)
    side_at_pause = st["side"]
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert e._guiding_off_for_pause is True

    async def refused(*a, **kw):
        raise RuntimeError("tracking on rejected (reply '0')")

    async def not_tracking():
        return False

    recovered: list[str] = []

    async def recover(target, **kw):
        recovered.append(target.name)
        st["side"] = "east" if st["side"] == "west" else "west"
        return True

    monkeypatch.setattr(flip_hub, "meridian_flip", refused)
    monkeypatch.setattr(e, "_tracking_now", not_tracking)
    monkeypatch.setattr(e, "_recover_from_tracking_refusal", recover)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert recovered == [t.name], "premise: the flip took the recovery path"
    assert e._flip_armed is True, "premise: that path leaves the latch armed"
    assert st["side"] != side_at_pause, "premise: the pier side changed"

    await e._maybe_recover_guiding(t)
    assert g.calls[-1] == "start", g.calls
    assert e._guiding_recoveries == 0, "a resume is not charged"
    assert not said(L_PAUSE_MOVED)


@pytest.mark.asyncio
async def test_a_re_setup_after_a_pause_does_not_read_as_a_moved_mount(
        sim_hub, monkeypatch):
    """MR-2 (P1's final re-review). A safety pause's resume, a roof reopen
    or a cloud hold's release re-runs ``_setup_target`` inside the visit, not
    ``_hop``, so the pause flag is still set. The setup's slew places the
    mount on the target (here on the other pier side), and its guide start
    fails. The recovery that follows must restart guiding on a target that
    does not re-centre: the setup's slew is the engine's own move, not an
    operator's.

    Drives the REAL ``_setup_target`` (only the mount's slew is scripted, so
    the reported position follows it) and the REAL recovery.

    MUTANT MS1 (the refresh at the end of ``_setup_target`` removed): RED -
        astrodeck.sequence.engine.StopTarget: the mount is not where it was
        when the run paused
    """
    import astrodeck.flows.tonight as tonight_mod
    # The target's own window is "unknown", so the setup never waits on the
    # hour the suite runs (the #682 shape).
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)
    logs = Logs(monkeypatch)
    g = _Guider(sim_hub, monkeypatch, active=True)
    pose = _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    t = Target(name="NGC 7331", ra_hours=5.0, dec_deg=10.0, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])
    e = SequenceEngine(sim_hub)
    e._cfg = None                       # warn and continue on a failed start
    e.plan = SequencePlan(name="mr-2", guide=True, recover_guiding=True,
                          meridian_flip=False, safety_check=False,
                          autofocus_every=0, dither_every=0, targets=[t])
    task = await _pause_and_park(e)
    assert g.calls == ["stop"], g.calls
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    assert e._guiding_off_for_pause is True

    slews: list[tuple] = []

    async def slew(ra_hours, dec_deg, *a, **kw):
        slews.append((ra_hours, dec_deg))
        pose.ra, pose.dec, pose.pier = 5.0, 10.0, "west"
    monkeypatch.setattr(sim_hub.devices["telescope"], "slew", slew)

    starts = [0]

    async def start_guiding():
        g.calls.append("start")
        starts[0] += 1
        if starts[0] == 1:
            raise DeviceError("no star in the guide frame")
        g.active = True
    monkeypatch.setattr(sim_hub.guider, "start_guiding", start_guiding)

    await e._setup_target(0, t)
    assert slews, "premise: the setup slewed the mount"
    assert starts[0] == 1 and g.active is False, (
        f"premise: the setup's guide start failed: {g.calls}")
    assert e._guiding_off_for_pause is True, "premise: the flag is unconsumed"

    await e._maybe_recover_guiding(t)
    assert starts[0] == 2 and g.active is True, g.calls
    assert e._guiding_recoveries == 0, "a resume is not charged"
    assert not logs.has(L_PAUSE_MOVED)
