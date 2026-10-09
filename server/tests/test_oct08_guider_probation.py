# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#848: a calibration is not claimed, saved or shot on until it has held the
star.

2026-10-07, after the flip: six fresh walks, five of them wrong, each one
claimed "calibrated and guiding" and persisted the moment the walk ended, and
the run shot a 180 s frame on the first even though its first dither had
already failed to settle. 2026-10-08 (orchestrator ruling): a REUSED
calibration ran RA away to 100 arcsec within a minute of "reusing persisted
calibration", so a reused file goes through the same probation.

The probation (``NativeGuider._judge_frame``): at ``PROBATION_FRAMES`` (10)
measured frames it fails if (A) any of the last 5 asked for a correction at
the limit on an axis where one capped pulse moves the star at least three
noise sigmas (``_meaningful``), or (B) an open capped streak has grown by G.
It fails past ``PROBATION_WALL_S`` or ``PROBATION_MAX_LOOP_FRAMES`` loop
frames without a verdict. ``start_guiding`` waits for the verdict inside the
engine's bound for the start. A failure discards the calibration and fails
the start with ``PROBATION_FAILED_MSG``. The first dither after a pass that
does not settle discards it too (the issue's own clause).

NAMED MUTANTS (guide/native.py byte backup, the suite's normal command,
restore + sha256; the observed results are in REPORT-P1.md):
 * M12 "probation not armed" (start_guiding arms ``None``; the old claim and
   persist restored after the loop task is created).
 * M12r "a reused calibration is not on probation" (armed only when the walk
   was fresh).
 * M13 "tail ignored" (``if k:`` -> ``if False:``).
 * M14 "every capped frame counts" (``_meaningful`` returns True).
 * M15+M1 "rule B removed" together with "the gate never trips".
 * M16 "BLC not subtracted" (``_judged_ms`` returns ``ms``).
 * M17 the "calibrating" line removed from ``_current_phase``.
 * M18 ``_persist_calibration()`` removed from ``_probation_passed``.
 * M19 the wall test removed.
 * M20 the loop-frame test removed.
 * M21 "a loop that ended without a verdict counts as a pass".
 * M22 ``user_stopped = self._stop.is_set()``.
 * M23 the ``except asyncio.CancelledError`` block removed from
   ``_await_probation``.
 * M24 the epoch test in ``_probation_passed`` removed.
 * M25 the declination read removed from ``needs_calibration``.
 * M26 ``announce`` ignored in ``_cal_reusable``.
 * M27 the ``needs_calibration`` hint ignored in ``start_guiding``.
 * M28 ``_first_dither_pending = True`` removed from ``_probation_passed``.
 * M29 the flag not cleared on a settled dither.
 * M-T14 any mirrored bound changed by 1 s.
 * M-T15 the dither refusal removed.
 * Fix round 1 (review B): R1 ``_said_needs`` stores nothing; R2 the reuse
   branch's ``bound_s = _GUIDE_START_BOUND_S`` removed; R12 the budget
   ignores the time already spent in the start; R3 the probation clock not
   restarted for a fallback walk; R14 ``start_guiding`` keeps ``_streaks``;
   R7 the settle-error path skips ``_first_dither_failed``.

The first-dither clause applies after EVERY probation pass, reused
calibrations included (the 2026-10-08 ruling put reuse on probation, and
``_probation_passed`` does not know which it was). The design's T18f ("a
reused calibration does not carry the clause") was dropped for that reason;
REPORT-P1.md lists it for the owner.
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide import native
from astrodeck.guide.native import (
    DITHER_REFUSED, PROBATION_FAILED_MSG, PROBATION_FRAMES,
    PROBATION_LOOP_DIED_MSG, PROBATION_STOP_REASON, RUNAWAY_STOP_REASON,
    GuidingStopped, NativeGuider)
from astrodeck.providers import NATIVE_AVAILABLE

from _oct08_guider_harness import (
    CAP_MS, IDLE, Camera, Logs, Mount, ScriptedEngine, arm_loop, cal_dict,
    cal_path, make_guider, plant_cal, profile_id, pulse, stop_loop, wait_until)

native_only = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                 reason="native wheel absent")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _healthy(n, err=0.3):
    return [(IDLE, (err, err))] * n


#: A small correction that reaches the mount: the probation counts frames
#: only from the first one (a star that needs none exercises nothing).
FIRST = (pulse("east", 100), (0.3, 0.3))
PULSING = (pulse("east", 100), (0.3, 0.3))


def _scripted_start(g: NativeGuider, eng: ScriptedEngine, *, scope_dec_deg=0.0):
    """Make ``start_guiding``'s FRESH path guide on ``eng``: the walk is
    replaced by installing the scripted engine (the real engine it built is
    dropped), everything else in the start is the real code."""
    async def _fake_calibrate():
        g._engine = eng
        g._scope_dec_rad = math.radians(scope_dec_deg)
        g._caps = None
    g._calibrate = _fake_calibrate
    g._lock_xy = None


async def _sim_guider(tag, *, cap=None, profile=None):
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    if cap is not None:
        tel.max_pulse_ms = cap
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=profile or profile_id(tag))
    await g.connect()
    return g, rig, cam, tel


def _lock_steady(g):
    """The scripted loop has no frames worth a star-find: hold the steady lock
    path from the loop's first frame."""
    real = g._guide_loop

    async def _loop():
        g._lock_xy = (0.0, 0.0)
        return await real()
    g._guide_loop = _loop


# ------------------------------------------------------------ claim and fail


@native_only
@pytest.mark.asyncio
async def test_a_fresh_calibration_is_claimed_only_after_it_holds(
        _isolated_config_dir, monkeypatch):
    """T8 (the real engine on the sim). When "native guider calibrated and
    guiding" is logged, the engine's ``recent`` already holds at least
    PROBATION_FRAMES measured frames, the pass line came first, and the file
    is on disk."""
    g, _rig, _cam, _tel = await _sim_guider("t8")
    seen: list[tuple[str, int]] = []
    from astrodeck.events import bus
    real_log = bus.log

    def _spy(level, message, source="hub"):
        n = -1
        if g._engine is not None:
            try:
                n = len(g._engine.stats()["recent"])
            except Exception:
                n = -1
        seen.append((str(message), n))
        return real_log(level, message, source)

    monkeypatch.setattr(bus, "log", _spy)
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    try:
        claims = [n for m, n in seen if m == "native guider calibrated and guiding"]
        assert claims, f"no claim: {[m for m, _ in seen]}"
        assert claims[0] >= PROBATION_FRAMES
        msgs = [m for m, _ in seen]
        i_pass = next(i for i, m in enumerate(msgs)
                      if "the calibration held the star" in m)
        assert i_pass < msgs.index("native guider calibrated and guiding")
        assert cal_path(_isolated_config_dir, g.profile_id).exists()
    finally:
        await g.stop_guiding()
        await g.disconnect()


@native_only
@pytest.mark.asyncio
async def test_a_calibration_that_does_not_hold_fails_the_start_and_is_discarded(
        _isolated_config_dir, monkeypatch):
    """T9 (the real engine on the sim). A walk whose RA axis comes out
    reversed (the 2026-09-06 class) never holds the star: the start fails
    with the fixed-words message, nothing is saved, and the guider reads
    "stopped" with one of its two fixed reasons."""
    g, rig, _cam, _tel = await _sim_guider("t9", cap=500)
    srig = rig["_rig"]
    real_calibrate = g._calibrate

    async def _bad_walk():
        await real_calibrate()
        # A seeing-level error from the first frame, so the reversed axis
        # has something to amplify (the sim's default star is near still).
        srig.guide_seeing_px = 1.0
        cal = g._engine.dump_calibration()
        cal["x_angle"] = float(cal["x_angle"]) + math.pi
        g._engine.load_calibration(cal)
    g._calibrate = _bad_walk
    try:
        with pytest.raises(DeviceError) as ei:
            await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        assert str(ei.value) == PROBATION_FAILED_MSG
        assert not cal_path(_isolated_config_dir, g.profile_id).exists()
        assert await g.needs_calibration() is True
        st = g.stats()
        assert st.phase == "stopped"
        assert st.stop_reason in (PROBATION_STOP_REASON, RUNAWAY_STOP_REASON)
    finally:
        await g.stop_guiding()
        await g.disconnect()


@native_only
@pytest.mark.asyncio
async def test_a_reused_calibration_that_does_not_hold_is_discarded(
        _isolated_config_dir, monkeypatch):
    """T17c (2026-10-08 ruling). A calibration persisted by one session is
    reused by the next with its RA axis reversed on disk (what a calibration
    flipped for a pier change the mount did not make looks like). The reuse
    is on probation like a fresh walk: the start fails with the fixed-words
    message and the file is deleted, so the next start walks."""
    pid = profile_id("t17c")
    g1, _rig, cam, tel = await _sim_guider("t17c", cap=500, profile=pid)
    await asyncio.wait_for(g1.start_guiding(), timeout=120.0)
    await g1.stop_guiding()
    await g1.disconnect()
    path = cal_path(_isolated_config_dir, pid)
    cal = json.loads(path.read_text(encoding="utf-8"))
    cal["x_angle"] = float(cal["x_angle"]) + math.pi
    path.write_text(json.dumps(cal), encoding="utf-8")
    _rig["_rig"].guide_seeing_px = 1.0

    logs = Logs(monkeypatch)
    g2 = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                        "exposure_s": 0.2}, profile_id=pid)
    await g2.connect()
    try:
        with pytest.raises(DeviceError) as ei:
            await asyncio.wait_for(g2.start_guiding(), timeout=120.0)
        assert logs.has("reusing persisted calibration"), "premise: a reuse"
        assert str(ei.value) == PROBATION_FAILED_MSG
        assert not path.exists()
        assert not logs.has("native guider calibrated and guiding")
    finally:
        await g2.stop_guiding()
        await g2.disconnect()


# --------------------------------------------------------- the two checks


@pytest.mark.asyncio
async def test_capped_corrections_on_a_strong_axis_fail_the_check(
        _isolated_config_dir, monkeypatch):
    """T10. Dec 0: one capped RA pulse moves the star 2.735 px, over three
    noise sigmas (1.64 px), so a capped RA request is evidence. Ten measured
    frames, the RA error flat at 2 px, the last five asking for the limit in
    alternating directions (no streak grows): (A) fails it."""
    logs = Logs(monkeypatch)
    pid = profile_id("t10")
    g = make_guider(profile=pid)
    script = [FIRST] + _healthy(5, 2.0) + [
        (pulse("east" if k % 2 == 0 else "west", CAP_MS), (2.0, 0.3))
        for k in range(5)]
    eng = ScriptedEngine(script)
    task = arm_loop(g, eng, probation=True)
    p = g._probation
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        if not task.done():
            await stop_loop(g)
    assert p.verdict not in (None, "pass")
    assert "5 of the last 5 corrections at the 1000 ms limit" in p.verdict
    assert g.stats().stop_reason == PROBATION_STOP_REASON
    assert not cal_path(_isolated_config_dir, pid).exists()


@pytest.mark.parametrize("scope_dec", [80.0, 85.0])
@pytest.mark.asyncio
async def test_a_healthy_walk_near_the_pole_passes(
        _isolated_config_dir, monkeypatch, scope_dec):
    """T10a (the review's case). Near the pole one capped RA pulse moves the
    star 0.475 px (+80) or 0.238 px (+85), below the seeing, so seeing alone
    asks for the RA limit: frames 5-10 do, the direction following the noise,
    at a 0.6 px error. That is not evidence on so weak an axis, and no streak
    grows, so the probation passes and the file is written."""
    pid = profile_id(f"t10a{int(scope_dec)}")
    g = make_guider(profile=pid)
    dirs = ["east", "east", "west", "west", "west", "east"]
    script = [FIRST] + _healthy(4, 0.6) + [(pulse(d, CAP_MS), (0.6, 0.3))
                                           for d in dirs]
    eng = ScriptedEngine(script)
    task = arm_loop(g, eng, scope_dec_deg=scope_dec, probation=True)
    p = g._probation
    try:
        assert await wait_until(lambda: p.verdict is not None, 2.0)
        assert p.verdict == "pass"
        assert cal_path(_isolated_config_dir, pid).exists()
        assert g._active
    finally:
        await stop_loop(g)


@native_only
@pytest.mark.asyncio
async def test_a_bad_dec_calibration_fails_by_growth(
        _isolated_config_dir, monkeypatch):
    """T10b. Dec is NOT a strong axis at 5.5 arcsec/px (1.37 px per capped
    pulse, under 1.64 px), so (A) cannot see a reversed Dec axis. It shows as
    growth: Dec north at the limit from frame 3, the Dec error growing 1.37
    px a frame. The start fails, by the runaway gate or by (B), before any
    claim."""
    logs = Logs(monkeypatch)
    pid = profile_id("t10b")
    g = make_guider(profile=pid)
    script = _healthy(2) + [(pulse("north", CAP_MS), (0.3, 0.5 + 1.37 * k))
                            for k in range(30)]
    eng = ScriptedEngine(script)
    _scripted_start(g, eng)
    _lock_steady(g)
    with pytest.raises(DeviceError) as ei:
        await asyncio.wait_for(g.start_guiding(), timeout=5.0)
    assert str(ei.value) == PROBATION_FAILED_MSG
    stop_lines = (logs.at("error", "grew")
                  + logs.at("error", "native guider stopped, calibration "
                            "discarded: its corrections drove the star away"))
    assert stop_lines
    assert not logs.has("native guider calibrated and guiding")
    await g.stop_guiding()


@pytest.mark.asyncio
async def test_blc_reversal_frames_are_not_at_the_limit(
        _isolated_config_dir, monkeypatch):
    """T10c. With a 300 ms static backlash pulse configured, the engine adds
    it on every Dec reversal and clamps to the limit, so a reversing Dec
    correction reaches the host as 1000 ms while the algorithm asked for 700.
    Those frames are not at the limit: the probation passes on a strong Dec
    axis (scale 1.0 known, y_rate 0.01 px/ms, 10 px a pulse against 9 px)
    and nothing is counted as saturated."""
    pid = profile_id("t10c")
    g = make_guider(profile=pid, scale=1.0, config={"blc_pulse_ms": 300})
    assert g._blc_ms == 300
    script = [(pulse("north", 500), (0.3, 1.0))]
    script += [(pulse("south" if k % 2 == 0 else "north", CAP_MS), (0.3, 1.0))
               for k in range(10)]
    eng = ScriptedEngine(script, cal=cal_dict(y_rate=0.01, scale=1.0))
    task = arm_loop(g, eng, probation=True)
    p = g._probation
    try:
        assert await wait_until(lambda: p.verdict is not None, 2.0)
        assert g._meaningful("dec"), "premise: Dec is a strong axis here"
        assert p.verdict == "pass"
        sat = g.saturated_pulses()
        assert sat["north"] == 0 and sat["south"] == 0
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_frames_before_the_first_correction_prove_nothing(
        _isolated_config_dir, monkeypatch):
    """T10d. A quiet star the loop never has to correct holds for twelve
    measured frames: that exercises nothing the calibration claims (on the
    sim a walk with a reversed RA axis "held" ten frames before its first
    pulse), so the probation has not passed. Once corrections reach the
    mount it counts ten frames from there and passes."""
    pid = profile_id("t10d")
    g = make_guider(profile=pid)
    eng = ScriptedEngine(_healthy(12), tail=(IDLE, None))
    arm_loop(g, eng, probation=True)
    p = g._probation
    try:
        assert await wait_until(lambda: eng.frames >= 14, 2.0)
        assert p.verdict is None
        assert not cal_path(_isolated_config_dir, pid).exists()
        eng.tail = PULSING
        assert await wait_until(lambda: p.verdict is not None, 2.0)
        assert p.verdict == "pass"
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_the_check_reads_calibrating(_isolated_config_dir, monkeypatch):
    """T11. While the probation runs the phase is "calibrating" (one of the
    engine's GUIDE_MOUNT_BUSY_PHASES, so a run's shutter waits); after the
    pass it is "guiding"."""
    g = make_guider(profile=profile_id("t11"))
    eng = ScriptedEngine([], tail=(IDLE, None))
    task = arm_loop(g, eng, probation=True)
    try:
        assert g.stats().phase == "calibrating"
        assert "calibrating" in engine_mod.GUIDE_MOUNT_BUSY_PHASES
        eng.tail = PULSING
        assert await wait_until(lambda: g._probation is None, 2.0)
        assert g.stats().phase == "guiding"
    finally:
        await stop_loop(g)
        assert task.done()


@native_only
@pytest.mark.asyncio
async def test_a_spent_start_budget_hands_the_check_to_the_loop(
        _isolated_config_dir, monkeypatch):
    """T12 (the real engine on the sim). When the engine's bound has no room
    left for the wait, the start returns at once saying the check goes on,
    with nothing saved; the loop then passes it, saves and claims."""
    monkeypatch.setattr(native, "_START_MARGIN_S", 10_000.0)
    logs = Logs(monkeypatch)
    g, _rig, _cam, _tel = await _sim_guider("t12")
    try:
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        assert logs.has(native.L_CONTINUES)
        path = cal_path(_isolated_config_dir, g.profile_id)
        assert await wait_until(path.exists, timeout=30.0, interval=0.02)
        assert await wait_until(
            lambda: logs.has("native guider calibrated and guiding"), 5.0)
    finally:
        await g.stop_guiding()
        await g.disconnect()


@pytest.mark.asyncio
async def test_a_check_that_runs_out_of_time_fails(
        _isolated_config_dir, monkeypatch):
    """T12a. Frames of 0.2 s and a 0.5 s wall: the probation fails as "not
    proven in time" before it reaches ten frames (0.5 s against 0.2 s frames
    is far above the 15.6 ms clock resolution)."""
    monkeypatch.setattr(native, "PROBATION_WALL_S", 0.5)
    logs = Logs(monkeypatch)
    g = make_guider(profile=profile_id("t12a"), camera=Camera(dwell_s=0.2))
    eng = ScriptedEngine([PULSING] * 20)
    task = arm_loop(g, eng, probation=True)
    p = g._probation
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    finally:
        if not task.done():
            await stop_loop(g)
    assert p.verdict not in (None, "pass")
    assert "not proven within" in p.verdict
    assert p.frames < PROBATION_FRAMES
    assert logs.at("error", "not proven within")


@pytest.mark.asyncio
async def test_a_check_that_cannot_measure_fails(
        _isolated_config_dir, monkeypatch):
    """T13. Thirty loop frames, only four of them measured: the star is too
    faint to prove anything, and the probation fails rather than wait out the
    wall."""
    g = make_guider(profile=profile_id("t13"))
    script = [FIRST]
    for k in range(40):
        script.append((IDLE, (0.3, 0.3)) if k % 8 == 0 else (IDLE, None))
    eng = ScriptedEngine(script, tail=(IDLE, None))
    task = arm_loop(g, eng, probation=True)
    p = g._probation
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        if not task.done():
            await stop_loop(g)
    assert p.verdict is not None and "only 4 of 30 frames measured" in p.verdict


def test_the_bounds_mirror_the_engine():
    """T14. The guider cannot import the engine, so its copies are pinned
    here, and the verdict lands inside the engine's quiet gate: the wall plus
    the longest loop frame (15 s exposure + 2.5 s pulse + 1 s) is under it."""
    assert native._GUIDE_START_BOUND_S == engine_mod.GUIDE_START_TIMEOUT_S
    assert native._GUIDE_CALIBRATE_BOUND_S == engine_mod.GUIDE_CALIBRATE_TIMEOUT_S
    assert native._GUIDE_QUIET_BOUND_S == engine_mod.GUIDE_QUIET_TIMEOUT_S
    assert native.PROBATION_WALL_S + 18.5 < native._GUIDE_QUIET_BOUND_S


@pytest.mark.asyncio
async def test_dither_is_refused_while_the_calibration_is_checked(
        _isolated_config_dir, monkeypatch):
    """T15. A dither during the probation is refused, with no "settle" in the
    text (the engine counts only settle failures toward its walking-field
    hold), and the engine is never asked to move the lock."""
    g = make_guider(profile=profile_id("t15"))
    eng = ScriptedEngine([], tail=(IDLE, None))
    arm_loop(g, eng, probation=True)
    try:
        with pytest.raises(DeviceError) as ei:
            await g.dither(3.0)
        assert str(ei.value) == DITHER_REFUSED
        assert "settle" not in str(ei.value)
        assert eng.dithers == []
    finally:
        await stop_loop(g)


# ------------------------------------------------------- stop, crash, cancel


@native_only
@pytest.mark.asyncio
async def test_stop_during_the_check_raises_guiding_stopped(
        _isolated_config_dir, monkeypatch):
    """T16. A Stop while the start waits for the verdict: the start raises
    GuidingStopped (so a plan that requires guiding escalates), nothing is
    saved."""
    pid = profile_id("t16")
    g = make_guider(profile=pid, camera=Camera(dwell_s=0.02))
    eng = ScriptedEngine([], tail=PULSING)
    _scripted_start(g, eng)
    _lock_steady(g)

    async def _stopper():
        await wait_until(lambda: g._probation is not None
                         and g._loop_task is not None and eng.frames >= 1, 5.0,
                         interval=0.001)
        await g.stop_guiding()

    stopper = asyncio.ensure_future(_stopper())
    with pytest.raises(GuidingStopped):
        await asyncio.wait_for(g.start_guiding(), timeout=5.0)
    await stopper
    assert eng.frames < PROBATION_FRAMES, "premise: stopped mid-check"
    assert not cal_path(_isolated_config_dir, pid).exists()


@native_only
@pytest.mark.asyncio
async def test_a_loop_crash_during_the_check_is_not_a_stop(
        _isolated_config_dir, monkeypatch):
    """T16a. The loop dies of an error on its 4th frame: the start fails with
    the fixed-words "guiding stopped while the calibration was being checked"
    and NOT as a user Stop. ``_stop`` cannot tell the two apart, because the
    loop's own generic handler sets it too."""
    g = make_guider(profile=profile_id("t16a"))
    eng = ScriptedEngine([], tail=PULSING)
    eng.raise_on_frame = 4
    _scripted_start(g, eng)
    _lock_steady(g)
    with pytest.raises(DeviceError) as ei:
        await asyncio.wait_for(g.start_guiding(), timeout=5.0)
    assert str(ei.value) == PROBATION_LOOP_DIED_MSG
    assert not isinstance(ei.value, GuidingStopped)


@native_only
@pytest.mark.asyncio
async def test_a_cancelled_start_does_not_leave_the_loop_running(
        _isolated_config_dir, monkeypatch):
    """T16b. The engine's bound cancels the start mid-check: the loop must
    not outlive it. The loop task is done, nothing is guiding, nothing is
    saved, and the log says so."""
    logs = Logs(monkeypatch)
    pid = profile_id("t16b")
    g = make_guider(profile=pid, camera=Camera(dwell_s=0.02))
    eng = ScriptedEngine([], tail=PULSING)
    _scripted_start(g, eng)
    _lock_steady(g)
    fut = asyncio.ensure_future(g.start_guiding())
    assert await wait_until(lambda: g._probation is not None
                            and g._loop_task is not None, 5.0, interval=0.001)
    loop_task = g._loop_task
    fut.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fut
    try:
        assert loop_task.done()
        assert g._loop_task is None
        assert not await g.is_active()
        assert not cal_path(_isolated_config_dir, pid).exists()
        assert logs.has(native.L_CANCEL)
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_a_clear_during_the_check_is_not_undone(
        _isolated_config_dir, monkeypatch):
    """T17. The operator clears the calibration while it is on probation: the
    pass that follows must not write it back (GN-01's latch, by epoch)."""
    pid = profile_id("t17")
    g = make_guider(profile=pid)
    eng = ScriptedEngine([], tail=(IDLE, None))
    arm_loop(g, eng, probation=True)
    p = g._probation
    try:
        g.clear_calibration()
        eng.tail = PULSING
        assert await wait_until(lambda: p.verdict is not None, 2.0)
        assert p.verdict == "pass"
        assert not cal_path(_isolated_config_dir, pid).exists()
    finally:
        await stop_loop(g)


# --------------------------------------- the start bound and the reuse answer


@pytest.mark.asyncio
async def test_needs_calibration_reads_the_live_declination(
        _isolated_config_dir, monkeypatch):
    """T17a (#18 divergence). A file walked at +34.4, reusable in every other
    way; the mount at +66.1 (49 percent of the RA rate). ``needs_calibration``
    now asks the cos(dec) question ``start_guiding`` asks, and answers True
    without logging the refusal (the start logs it, once). The control, the
    mount at +34.4, answers False."""
    pid = profile_id("t17a")
    plant_cal(_isolated_config_dir, pid,
              cal_dict(dec_rad=math.radians(34.4), pier="east"))
    logs = Logs(monkeypatch)
    mount = Mount(dec_deg=66.1, pier="east")
    g = make_guider(profile=pid, mount=mount)
    n0 = len(logs.lines)
    assert await g.needs_calibration() is True
    assert logs.at("warning")[0:] == [m for lv, m in logs.lines[n0:]
                                      if lv == "warning"]
    assert [m for lv, m in logs.lines[n0:] if lv == "warning"] == []
    mount.dec_deg = 34.4
    assert await g.needs_calibration() is False


@native_only
@pytest.mark.asyncio
async def test_the_probation_budget_follows_the_engines_bound(
        _isolated_config_dir, monkeypatch):
    """T17b. ``start_guiding`` waits for the verdict only inside the bound
    the ENGINE wrapped it in, and the engine picks 180 s exactly when
    ``needs_calibration`` just answered False. So: a fresh False answer gives
    180; no answer, or one older than ``_BOUND_HINT_S``, gives 660."""
    seen: list[float] = []

    async def run(said):
        g = make_guider(profile=profile_id("t17b"))
        eng = ScriptedEngine([], tail=PULSING)
        _scripted_start(g, eng)
        _lock_steady(g)
        real = g._await_probation

        async def spy(t0, bound_s):
            seen.append(bound_s)
            return await real(t0, bound_s)
        g._await_probation = spy
        g._needs_cal_said = said
        await asyncio.wait_for(g.start_guiding(), timeout=5.0)
        await g.stop_guiding()

    await run((False, time.monotonic()))
    await run(None)
    await run((False, time.monotonic() - native._BOUND_HINT_S - 5.0))
    assert seen == [180.0, 660.0, 660.0]


# ------------------------------------------------- the first dither's clause


async def _passed(g, eng_tail=PULSING):
    eng = ScriptedEngine([], tail=eng_tail)
    arm_loop(g, eng, probation=True)
    p = g._probation
    assert await wait_until(lambda: p.verdict is not None, 2.0)
    assert p.verdict == "pass"
    return eng


@pytest.mark.asyncio
async def test_a_failed_first_dither_after_a_pass_discards_and_stops(
        _isolated_config_dir, monkeypatch):
    """T18d. The issue's own criterion: the first dither after the calibration
    passed does not settle. The calibration is discarded and the guider stops
    itself; the dither still raises its "settle" error for the engine."""
    logs = Logs(monkeypatch)
    pid = profile_id("t18d")
    g = make_guider(profile=pid)
    try:
        await _passed(g)
        assert cal_path(_isolated_config_dir, pid).exists()
        with pytest.raises(DeviceError) as ei:
            await g.dither(3.0, {"timeout": 1.0})
        assert "settle" in str(ei.value)
        st = g.stats()
        assert st.phase == "stopped"
        assert st.stop_reason == PROBATION_STOP_REASON
        assert not cal_path(_isolated_config_dir, pid).exists()
        assert logs.has(native.L_FIRST_DITHER)
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_only_the_first_dither_carries_the_clause(
        _isolated_config_dir, monkeypatch):
    """T18e. The first dither settles; the second times out. A later dither's
    failure is just that: the guider stays up and the file stays."""
    pid = profile_id("t18e")
    g = make_guider(profile=pid)
    try:
        eng = await _passed(g)
        first = asyncio.ensure_future(g.dither(3.0, {"timeout": 2.0}))
        assert await wait_until(lambda: g._settle_open, 1.0)
        eng.settling = False                       # the window closes
        await asyncio.wait_for(first, timeout=2.0)
        with pytest.raises(DeviceError):
            await g.dither(3.0, {"timeout": 1.0})
        assert g._active
        assert g.stats().stop_reason == ""
        assert cal_path(_isolated_config_dir, pid).exists()
    finally:
        await stop_loop(g)


# ------------------------------------------------------- fix round 1 (review)


class _ShiftedClock:
    """``time`` for guide/native.py alone, with ``monotonic`` moved forward by
    ``offset``: a walk that "took" minutes, without the test waiting them or
    the event loop's own clock jumping."""

    def __init__(self):
        self._real = time
        self.offset = 0.0

    def monotonic(self):
        return self._real.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(self._real, name)


def _spy_bound(g, seen):
    real = g._await_probation

    async def spy(t0, bound_s):
        seen.append(bound_s)
        return await real(t0, bound_s)
    g._await_probation = spy


@native_only
@pytest.mark.asyncio
async def test_a_reuse_answer_bounds_a_start_that_walks_after_all(
        _isolated_config_dir, monkeypatch):
    """FR1-10 (review B, mutant R1: ``_said_needs`` no longer stores the
    answer). The REAL ``needs_calibration`` answers False (a file walked at
    +34.4, the mount at +34.4), so the engine bounds the start at 180 s.
    When the start reads the mount again it is at +66.1, the file is refused
    and the start walks after all. Its probation wait is still bounded by the
    180 s the engine picked, not the 660 s a walk would have had: waiting
    past it, the engine cancels the start and setup ends the night."""
    pid = profile_id("fr1-10")
    plant_cal(_isolated_config_dir, pid,
              cal_dict(dec_rad=math.radians(34.4), pier="east"))
    mount = Mount(dec_deg=34.4, pier="east")
    g = make_guider(profile=pid, mount=mount)
    eng = ScriptedEngine([], tail=PULSING)
    _scripted_start(g, eng)
    walked: list[int] = []
    scripted_walk = g._calibrate

    async def _walk():
        walked.append(1)
        await scripted_walk()
    g._calibrate = _walk
    _lock_steady(g)
    seen: list[float] = []
    _spy_bound(g, seen)
    assert await g.needs_calibration() is False
    mount.dec_deg = 66.1
    await asyncio.wait_for(g.start_guiding(), timeout=5.0)
    try:
        assert walked == [1], "premise: the start walked after all"
        assert seen == [180.0]
    finally:
        await g.stop_guiding()


@native_only
@pytest.mark.asyncio
async def test_a_reuse_start_is_bounded_as_one_without_a_hint(
        _isolated_config_dir, monkeypatch):
    """FR1-11 (review B, mutant R2: the reuse branch's ``bound_s =
    _GUIDE_START_BOUND_S`` removed). A start that reuses asked the engine's
    own questions, so the engine bounded it at 180 s even when nothing told
    the guider so (no ``needs_calibration`` answer): its wait gets 180."""
    pid = profile_id("fr1-11")
    g1, _rig, cam, tel = await _sim_guider("fr1-11", profile=pid)
    await asyncio.wait_for(g1.start_guiding(), timeout=120.0)
    await g1.stop_guiding()
    await g1.disconnect()
    assert cal_path(_isolated_config_dir, pid).exists(), "premise: saved"
    logs = Logs(monkeypatch)
    g2 = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                        "exposure_s": 0.2}, profile_id=pid)
    await g2.connect()
    seen: list[float] = []
    _spy_bound(g2, seen)
    try:
        assert g2._needs_cal_said is None, "premise: no hint"
        await asyncio.wait_for(g2.start_guiding(), timeout=120.0)
        assert logs.has("reusing persisted calibration"), "premise: a reuse"
        assert seen == [180.0]
    finally:
        await g2.stop_guiding()
        await g2.disconnect()


@native_only
@pytest.mark.asyncio
async def test_the_probation_budget_counts_the_time_the_walk_took(
        _isolated_config_dir, monkeypatch):
    """FR1-12 (review B, mutant R12: the budget ``bound_s - _START_MARGIN_S``,
    ignoring the time already spent in the start). No hint, so the bound is
    660 s, and the walk takes 640 s of it (a shifted clock). The budget left
    is 660 - 30 - 640 = -10 s, so the start returns at once saying the check
    goes on, with the check still running. Under the mutant it would wait up
    to 630 s more, and at a rig's frame rate that ends past the engine's
    bound: the engine cancels the start, and setup ends the night."""
    clock = _ShiftedClock()
    monkeypatch.setattr(native, "time", clock)
    logs = Logs(monkeypatch)
    g = make_guider(profile=profile_id("fr1-12"))
    eng = ScriptedEngine([], tail=PULSING)
    _scripted_start(g, eng)
    scripted_walk = g._calibrate

    async def _long_walk():
        await scripted_walk()
        clock.offset += 640.0
    g._calibrate = _long_walk
    _lock_steady(g)
    await asyncio.wait_for(g.start_guiding(), timeout=5.0)
    try:
        assert logs.has(native.L_CONTINUES)
        p = g._probation
        assert p is not None and p.verdict is None
        assert not logs.has("native guider calibrated and guiding")
    finally:
        await g.stop_guiding()


@native_only
@pytest.mark.asyncio
async def test_a_fallback_walk_restarts_the_probation_clock(
        _isolated_config_dir, monkeypatch):
    """FR1-13 (review B, mutant R3: ``p.armed_at = time.monotonic()`` removed
    from ``_prove_reuse_with_first_pulse``). A reused calibration's first
    pulse fails, so the guider walks a fresh one in place, and the walk takes
    longer than ``PROBATION_WALL_S`` (a shifted clock; a rig walk is about
    220 s against the 200 s wall). The fresh walk is judged from its own
    first guided frame, so it passes and is claimed; under the mutant it
    fails at once as "not proven within 200 s" and is discarded."""
    pid = profile_id("fr1-13")
    g1, _rig, cam, tel = await _sim_guider("fr1-13", profile=pid)
    await asyncio.wait_for(g1.start_guiding(), timeout=120.0)
    await g1.stop_guiding()
    await g1.disconnect()
    clock = _ShiftedClock()
    monkeypatch.setattr(native, "time", clock)
    logs = Logs(monkeypatch)
    g2 = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                        "exposure_s": 0.2}, profile_id=pid)
    await g2.connect()
    real_pulse_guide = tel.pulse_guide
    failed = {"v": False}

    async def _flaky(direction, ms):
        if not failed["v"]:
            failed["v"] = True
            raise OSError("the first guide pulse found a dead write side")
        return await real_pulse_guide(direction, ms)
    tel.pulse_guide = _flaky
    walks: list[int] = []
    real_walk = g2._calibrate

    async def _slow_walk():
        walks.append(1)
        await real_walk()
        clock.offset += native.PROBATION_WALL_S + 50.0
    g2._calibrate = _slow_walk
    try:
        await asyncio.wait_for(g2.start_guiding(), timeout=120.0)
        assert logs.has("reusing persisted calibration"), "premise: a reuse"
        assert walks == [1], "premise: the first pulse failed and it walked"
        assert await wait_until(
            lambda: logs.has("native guider calibrated and guiding"), 30.0)
        assert not logs.at("error", "not proven within")
    finally:
        tel.pulse_guide = real_pulse_guide
        await g2.stop_guiding()
        await g2.disconnect()


@native_only
@pytest.mark.asyncio
async def test_a_restart_does_not_inherit_a_streak(
        _isolated_config_dir, monkeypatch):
    """FR1-14 (review B, mutant R14: ``start_guiding`` no longer resets
    ``_streaks``). A capped east streak of five was open when the last session
    stopped (a self-stop leaves it). The next start's first frame is capped
    east with the RA error at 6.0 px: counted from that session it would be a
    sixth pulse with 6.0 px of growth (G is 5.47 px at dec 0), a runaway on a
    calibration that has not sent a single pulse. It is the first frame of a
    new streak, so the start passes."""
    g = make_guider(profile=profile_id("fr1-14"))
    g._streaks = {"ra": {"dir": "east", "len": native.RUNAWAY_PULSES,
                         "e0": 0.0}, "dec": None}
    eng = ScriptedEngine([(pulse("east", CAP_MS), (6.0, 0.0))], tail=PULSING)
    _scripted_start(g, eng)
    _lock_steady(g)
    await asyncio.wait_for(g.start_guiding(), timeout=5.0)
    try:
        assert g.stats().stop_reason == ""
        assert await g.is_active()
    finally:
        await g.stop_guiding()


@pytest.mark.asyncio
async def test_a_first_dither_that_fails_its_settle_discards_too(
        _isolated_config_dir, monkeypatch):
    """FR1-15 (review B, mutant R7: the ``_first_dither_failed()`` call on the
    settle-error path removed). The first dither after a pass fails not by
    the host's wait timing out but by the engine closing the window with its
    own settle timeout (``lock_lost`` / ``settle_timeout``). The clause is the
    same: the calibration is discarded and the guider stops itself."""
    logs = Logs(monkeypatch)
    pid = profile_id("fr1-15")
    g = make_guider(profile=pid)
    try:
        eng = await _passed(g)
        assert cal_path(_isolated_config_dir, pid).exists()
        dither = asyncio.ensure_future(g.dither(3.0, {"timeout": 5.0}))
        assert await wait_until(lambda: g._settle_open, 1.0)
        # One synchronous block, so the next frame the loop takes is this one.
        eng.script.append(({"action": "lock_lost", "reason": "settle_timeout"},
                           None))
        eng.settling = False
        with pytest.raises(DeviceError) as ei:
            await asyncio.wait_for(dither, timeout=2.0)
        assert "settle failed" in str(ei.value)
        assert g.stats().stop_reason == PROBATION_STOP_REASON
        assert not cal_path(_isolated_config_dir, pid).exists()
        assert logs.has(native.L_FIRST_DITHER)
    finally:
        await stop_loop(g)
