# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#849: the native guide loop stops itself when its own corrections drive the
star away, on the loop's own cadence, before the next pulse goes out.

2026-10-07, NGC 7331, after the flip: five bad calibration walks in a row,
each followed by a loop pushing the mount EAST at the 1000 ms cap with the
error at hundreds of arcsec and re-locks at 0. Nothing in the loop related
the error trend to the corrections, and the only detectors rode the
sequence engine's frame loop, one frame late each time, and not at all while
the run was paused.

The gate (``NativeGuider._judge_frame``): per axis, a STREAK is the run of
consecutive measured frames asking for a correction at the axis limit in one
direction. It trips once the streak has sent ``RUNAWAY_PULSES`` (5) pulses
AND the error on that axis has grown by G = max(2 x capability, 3 x sqrt(2)
x noise) since the streak began. Capability is the engine's own model (limit
x rate, RA compensated for declination as engine.rs ``effective_x_rate``
does), so the gate behaves the same at dec +34 and at +85. A trip discards
the calibration (RULING R2) and reports phase "stopped" with a fixed-words
``stop_reason``; it does not latch ``_lost`` (not a lost star).

NAMED MUTANTS (each applied to a byte backup of guide/native.py, run under the
suite's normal command, restored and sha256-checked; observed results are in
REPORT-P1.md):
 * M1 "gate never trips": the trip test in ``_judge_frame`` made ``False``.
 * M2 "trip after dispatch": the judge call moved below ``_dispatch``.
 * M3 "no discard": ``self.clear_calibration()`` removed from
   ``_stop_for_bad_calibration``.
 * M3b "_lost restored": ``self._lost = True`` added to
   ``_stop_for_bad_calibration``.
 * M3c ``RUNAWAY_PULSES + 1`` -> ``RUNAWAY_PULSES``.
 * M4 "growth test dropped": the trip on the streak length alone.
 * M5 "direction test dropped": a streak continues whatever the direction.
 * M6 "growth threshold zero": ``_growth_px`` returns 0.0.
 * M7 "an unmeasured frame resets the streak".
 * M8 "no settle wake": the ``_settle_done`` block removed from
   ``_stop_for_bad_calibration``.
 * M9 "the first revision's per-frame rule": count a frame when the error did
   not shrink, trip at 5 counted.
 * M10 "G = 2 x cap only": the noise floor removed from ``_growth_px``.
 * M11 "no cos(dec) in the capability": ``x_eff = x_rate`` always.
 * M11b "no 60-degree arm" and M11c "no 89-degree clamp" in
   ``_axis_caps_px``.
 * Fix round 1: F5 the streak reset removed from ``dither``; R4
   ``self._stop_reason = ""`` removed from ``stop_guiding``; R13
   ``stop_reason`` dropped from the no-engine branch of ``stats()``.
"""
from __future__ import annotations

import asyncio
import contextlib
import math

import pytest

from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide import native
from astrodeck.guide.native import RUNAWAY_STOP_REASON, NativeGuider
from astrodeck.providers import NATIVE_AVAILABLE

from _oct08_guider_harness import (
    CAP_MS, X_RATE, Y_RATE, Logs, Mount, ScriptedEngine, arm_loop, cal_dict,
    cal_path, make_guider, plant_cal, profile_id, pulse, stop_loop, wait_until)


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _ra_frames(direction, ms, errs):
    return [(pulse(direction, ms), (e, 0.0)) for e in errs]


# ------------------------------------------------------------ the trip itself


@pytest.mark.asyncio
async def test_five_capped_pulses_that_grow_the_error_stop_guiding(
        _isolated_config_dir, monkeypatch):
    """T1. Dec 0, so one capped RA pulse moves the star 2.735 px and G is
    5.47 px. RA east at the 1000 ms limit on every frame, the RA error 3, 5,
    7, ... px: the streak reaches length 6 on the sixth frame with 10 px of
    growth, so the gate trips BEFORE that frame's pulse. Five pulses reach the
    mount, not six."""
    logs = Logs(monkeypatch)
    pid = profile_id("t1")
    path = plant_cal(_isolated_config_dir, pid)
    mount = Mount()
    g = make_guider(profile=pid, mount=mount)
    eng = ScriptedEngine(_ra_frames("east", CAP_MS, [3, 5, 7, 9, 11, 13, 15, 17]))
    task = arm_loop(g, eng, scope_dec_deg=0.0)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        if not task.done():
            await stop_loop(g)
    assert mount.pulses == [("east", CAP_MS)] * 5
    st = g.stats()
    assert st.stop_reason == RUNAWAY_STOP_REASON
    assert st.phase == "stopped"
    assert g._lost is False
    assert not await g.is_active()
    assert not path.exists()
    assert await g.needs_calibration() is True
    errs = logs.at("error", "native guider stopped, calibration discarded")
    assert len(errs) == 1
    assert "5 in a row at the 1000 ms limit, east" in errs[0]


@pytest.mark.asyncio
async def test_a_capped_recenter_that_shrinks_the_error_does_not_trip(
        _isolated_config_dir, monkeypatch):
    """T3. A dither's fast recenter: unclamped 2658 ms east steps (over the
    limit, so they count as capped), the error shrinking 2.2 px per frame.
    A long capped streak, no growth: no trip."""
    mount = Mount()
    g = make_guider(profile=profile_id("t3"), mount=mount)
    errs = [20.0 - 2.2 * k for k in range(10)]
    eng = ScriptedEngine(_ra_frames("east", 2658, errs))
    task = arm_loop(g, eng)
    try:
        assert await wait_until(lambda: not eng.script and eng.frames > 12, 2.0)
        assert not task.done()
        assert g._active and g.stats().stop_reason == ""
        assert len(mount.pulses) == 10
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_alternating_capped_corrections_do_not_count(
        _isolated_config_dir, monkeypatch):
    """T4. East, west, east ... at the limit with the RA error growing 1.5 px
    per frame: each direction change starts a new streak, so no streak ever
    reaches six frames."""
    mount = Mount()
    g = make_guider(profile=profile_id("t4"), mount=mount)
    script = [(pulse("east" if k % 2 == 0 else "west", CAP_MS),
               (2.0 + 1.5 * k, 0.0)) for k in range(10)]
    eng = ScriptedEngine(script)
    task = arm_loop(g, eng)
    try:
        assert await wait_until(lambda: not eng.script and eng.frames > 12, 2.0)
        assert not task.done() and g._active
        assert len(mount.pulses) == 10
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_a_dec_backlash_stall_rides_through(
        _isolated_config_dir, monkeypatch):
    """T5. Dec north at the limit for 12 frames while the pulses take up
    slack and the Dec error stays flat at 4.0 px: a long streak, no growth."""
    mount = Mount()
    g = make_guider(profile=profile_id("t5"), mount=mount)
    eng = ScriptedEngine([(pulse("north", CAP_MS), (0.2, 4.0))] * 12)
    task = arm_loop(g, eng)
    try:
        assert await wait_until(lambda: not eng.script and eng.frames > 14, 2.0)
        assert not task.done() and g._active
        assert len(mount.pulses) == 12
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_frames_without_a_measurement_neither_count_nor_reset(
        _isolated_config_dir, monkeypatch):
    """T6. Three capped frames growing 2 px each, two dead-reckoned frames the
    engine did not measure (a pulse east at the limit, ``recent`` unmoved),
    then three more capped growing frames. The streak counts only measured
    frames and is not reset by the unmeasured ones, so it reaches six on the
    last frame (growth 10 px) and that frame's pulse is withheld: seven
    pulses."""
    mount = Mount()
    g = make_guider(profile=profile_id("t6"), mount=mount)
    script = (_ra_frames("east", CAP_MS, [3, 5, 7])
              + [(pulse("east", CAP_MS), None)] * 2
              + _ra_frames("east", CAP_MS, [9, 11, 13]))
    eng = ScriptedEngine(script)
    task = arm_loop(g, eng)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        if not task.done():
            await stop_loop(g)
    assert len(mount.pulses) == 7
    assert g.stats().stop_reason == RUNAWAY_STOP_REASON


@pytest.mark.asyncio
async def test_a_runaway_stop_fails_a_dither_in_flight_at_once(
        _isolated_config_dir, monkeypatch):
    """T7. A dither is waiting for its settle window when the gate trips: it
    fails at once with a "settle" error (which the engine counts as a settle
    failure), not after its 90 s timeout."""
    mount = Mount()
    g = make_guider(profile=profile_id("t7"), mount=mount)
    eng = ScriptedEngine([], tail=({"action": "idle"}, (0.1, 0.1)))
    task = arm_loop(g, eng)
    try:
        assert await wait_until(lambda: eng.frames > 3, 2.0)
        dither = asyncio.ensure_future(g.dither(3.0))
        await asyncio.sleep(0.01)
        assert eng.settling, "premise: the dither opened the settle window"
        eng.script.extend(_ra_frames("east", CAP_MS, [3, 5, 7, 9, 11, 13, 15]))
        with pytest.raises(native.DeviceError) as ei:
            await asyncio.wait_for(dither, timeout=1.0)
        assert "settle" in str(ei.value)
        assert g.stats().stop_reason == RUNAWAY_STOP_REASON
    finally:
        await stop_loop(g)


# ----------------------------------------------------- near the pole (review)


@pytest.mark.asyncio
async def test_a_stalled_recenter_near_the_pole_does_not_trip(
        _isolated_config_dir, monkeypatch):
    """T7a. Scope dec +85 on a calibration walked at dec 0: one capped RA
    pulse moves the star 0.238 px, below the seeing, so G is the noise floor
    2.31 px. A recenter that stalls (3.00, 3.00, 3.05, 3.10, 3.10, 3.15 px)
    and then falls 0.24 px per frame is a capped streak of 12 that never
    grows by G: no trip. The first revision's per-frame rule ("the error did
    not shrink on 5 frames") tripped here by construction."""
    mount = Mount()
    g = make_guider(profile=profile_id("t7a"), mount=mount)
    errs = [3.00, 3.00, 3.05, 3.10, 3.10, 3.15]
    errs += [3.15 - 0.24 * k for k in range(1, 7)]
    eng = ScriptedEngine(_ra_frames("east", CAP_MS, errs))
    task = arm_loop(g, eng, scope_dec_deg=85.0)
    try:
        assert await wait_until(lambda: not eng.script and eng.frames > 14, 2.0)
        assert not task.done() and g._active
        assert len(mount.pulses) == 12
    finally:
        await stop_loop(g)


@pytest.mark.asyncio
async def test_a_slow_runaway_near_the_pole_trips_once_it_has_grown(
        _isolated_config_dir, monkeypatch):
    """T7b. Scope dec +85: the RA error grows 0.24 px per capped pulse
    (1.00 + 0.24 k). Growth first reaches G = 2.31 px at k = 10, so exactly
    ten pulses reach the mount: about 13 arcsec of drift, however small each
    pulse is."""
    mount = Mount()
    g = make_guider(profile=profile_id("t7b"), mount=mount)
    eng = ScriptedEngine(_ra_frames("east", CAP_MS,
                                    [1.00 + 0.24 * k for k in range(30)]))
    task = arm_loop(g, eng, scope_dec_deg=85.0)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    finally:
        if not task.done():
            await stop_loop(g)
    assert len(mount.pulses) == 10
    assert g.stats().stop_reason == RUNAWAY_STOP_REASON


def test_the_capability_mirrors_the_engine():
    """T7c. ``_axis_caps_px`` is engine.rs ``effective_x_rate`` times the
    limit: compensated by cos(cur)/cos(cal); not compensated for a
    calibration past 60 degrees; the current declination clamped at 89."""
    g = make_guider(profile=None)

    def caps(cal_dec, scope_dec):
        g._engine = ScriptedEngine([], cal=cal_dict(dec_rad=math.radians(cal_dec)))
        g._scope_dec_rad = math.radians(scope_dec)
        g._caps = None
        return g._axis_caps_px()

    c = caps(30.0, 80.0)
    want = CAP_MS * X_RATE / math.cos(math.radians(30)) * math.cos(math.radians(80))
    assert c["ra"] == pytest.approx(want, rel=1e-9)
    assert c["dec"] == pytest.approx(CAP_MS * Y_RATE, rel=1e-9)
    assert caps(70.0, 80.0)["ra"] == pytest.approx(CAP_MS * X_RATE, rel=1e-9)
    assert caps(0.0, 89.5)["ra"] == pytest.approx(
        CAP_MS * X_RATE * math.cos(math.radians(89.0)), rel=1e-9)


# ------------------------------------------------------------- the real engine


pytestmark_native = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                       reason="native wheel absent")


@pytestmark_native
@pytest.mark.asyncio
async def test_a_reversed_ra_axis_on_the_sim_is_stopped(
        _isolated_config_dir, monkeypatch):
    """T2 (the real Rust engine on the sim rig). A fresh calibration passes its
    probation; then its RA axis is reversed in the engine (the 2026-09-06
    class: every RA correction now pushes the star the wrong way) and the sim
    mount is given 2 px of periodic error. The gate stops guiding with the
    runaway reason within 20 pulses.

    ``tel.max_pulse_ms`` is 500 so one capped RA pulse moves the sim star
    3.75 px, inside the engine's 15 px search region (at the sim's default
    2500 ms limit one pulse moves it 18.75 px and the engine's distance check
    rejects the walking frames as a lost star instead)."""
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    tel.max_pulse_ms = 500
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=profile_id("t2"))
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    srig = rig["_rig"]
    srig.guide_pe_amplitude_px = 2.0
    srig.guide_pe_period_s = 60.0
    srig.guide_seeing_px = 0.3
    pulses: list[tuple[str, int]] = []
    real_pulse_guide = tel.pulse_guide

    async def _count(direction, ms):
        pulses.append((direction, ms))
        return await real_pulse_guide(direction, ms)

    tel.pulse_guide = _count
    g._engine.flip_calibration(False)
    try:
        await wait_until(lambda: g._loop_task is None or g._loop_task.done()
                         or len(pulses) >= 60, timeout=60.0, interval=0.01)
        assert g.stats().stop_reason == RUNAWAY_STOP_REASON, (
            f"no runaway stop after {len(pulses)} pulses; phase "
            f"{g.stats().phase}")
        assert len(pulses) < 20
    finally:
        tel.pulse_guide = real_pulse_guide
        await g.stop_guiding()
        await g.disconnect()


# ------------------------------------------------------- fix round 1 (review)


@pytest.mark.asyncio
async def test_a_streak_does_not_span_a_dither(
        _isolated_config_dir, monkeypatch):
    """FR1-5 (review A, mutant F5: the streak reset removed from
    ``dither``). Scope dec +85, so G is the 2.31 px noise floor. Five capped
    east frames with the RA error flat at 1.0 px leave a streak of five open.
    A dither moves the lock, and the next frame is capped east with the
    error at 4.5 px: the dither's own 3.5 px step, measured against the new
    lock. The streak must start afresh there, so no trip, and every pulse
    reaches the mount."""
    mount = Mount()
    g = make_guider(profile=profile_id("fr1-5"), mount=mount)
    eng = ScriptedEngine(_ra_frames("east", CAP_MS, [1.0] * 5))
    task = arm_loop(g, eng, scope_dec_deg=85.0)
    dither = None
    try:
        assert await wait_until(lambda: not eng.script and eng.frames > 6, 2.0)
        s = g._streaks["ra"]
        assert s is not None and s["len"] == 5, "premise: a streak of 5 open"
        dither = asyncio.ensure_future(g.dither(3.0, {"timeout": 2.0}))
        assert await wait_until(lambda: bool(eng.dithers), 1.0)
        eng.script.extend(_ra_frames("east", CAP_MS, [4.5])
                          + _ra_frames("east", 500, [4.0, 3.5]))
        assert await wait_until(
            lambda: task.done() or (not eng.script and eng.frames > 10), 2.0)
        assert g.stats().stop_reason == ""
        assert not task.done() and g._active
        assert len(mount.pulses) == 8
    finally:
        await stop_loop(g)
        if dither is not None:
            dither.cancel()
            with contextlib.suppress(BaseException):
                await dither


@pytest.mark.asyncio
async def test_a_stop_after_a_self_stop_reads_idle(
        _isolated_config_dir, monkeypatch):
    """FR1-6 (review B, mutant R4: ``self._stop_reason = ""`` removed from
    ``stop_guiding``). After a runaway the guider reads "stopped" with its
    reason; the operator's Stop then ends that, and it reads "idle" with no
    reason, not "stopped" for the rest of the session."""
    g = make_guider(profile=None)
    eng = ScriptedEngine(_ra_frames("east", CAP_MS, [3, 5, 7, 9, 11, 13, 15]))
    task = arm_loop(g, eng, scope_dec_deg=0.0)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        if not task.done():
            await stop_loop(g)
    assert g.stats().phase == "stopped", "premise: the runaway stopped it"
    await g.stop_guiding()
    st = g.stats()
    assert st.stop_reason == ""
    assert st.phase == "idle"


def test_a_guider_with_no_engine_still_says_why_it_stopped():
    """FR1-7 (review B, mutant R13: ``stop_reason`` dropped from the
    no-engine branch of ``stats()``). The reason is part of every stats read,
    the engine-less one included."""
    g = make_guider(profile=None)
    g._engine = None
    g._active = False
    g._stop_reason = RUNAWAY_STOP_REASON
    st = g.stats()
    assert st.stop_reason == RUNAWAY_STOP_REASON
    assert st.phase == "stopped"
