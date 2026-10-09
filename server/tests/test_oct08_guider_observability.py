# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#848 / #856: what the night log says about the guider.

2026-10-07: reading the night back took frame renders and a trail-measuring
script, because the log did not say what the guider did. A runaway's pulses
were exactly at the 1000 ms cap, never over it, so the only "capped" warning
in the log (over-cap, rate-limited to one a minute) never fired for them.
Five bad walks left an advisory sentence each and no geometry. A status read
and a guide trace a moment apart disagreed (32.28 against 3.22) and neither
said when it was taken. And the walk began with no settle after the goto.

Covered here: the settle before the walk (T18, T19), the geometry line for
every completed walk and the legs of a failed one (T20, T21g), the
saturated-correction counters and the frame record's count (T21-T24), and
the stamps on the stats and the guide trace (T25, T26).

NAMED MUTANTS (byte backup, the suite's normal command, restore + sha256;
observed results in REPORT-P1.md):
 * M33 the settle wait removed from ``_calibrate``.
 * M34 ``await asyncio.sleep(_CAL_SETTLE_S)`` in place of the stop-aware wait.
 * M35 the ``_log_cal_geometry`` call removed.
 * M36 the leg's ``steps += 1`` removed.
 * M-T21g the failed-walk legs warning removed.
 * M37 ``>=`` -> ``>`` in the at-the-limit test.
 * M38 ``_saturated`` reset in ``start_guiding``.
 * M39 absolute counts instead of the diff in ``_capped_during_frame``.
 * M40 the L_CAPPED log removed; M40b the log placed after the reporter
   return.
 * M-T24 ``guide_capped`` made required.
 * M-T25 ``as_of`` not passed in ``stats()``.
 * M-T26 ``guide_as_of`` dropped from ``monitor_snapshot``.
 * Fix round 1: R6 the BLC add-on taken out in every Dec mode; R11 the
   guider-id test removed from ``_capped_during_frame``.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
import uuid

import numpy as np
import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide import native
from astrodeck.guide.base import GuideStats
from astrodeck.guide.native import GuidingStopped, NativeGuider
from astrodeck.hub import Hub
from astrodeck.providers import NATIVE_AVAILABLE
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.report import FrameRecord

from _oct08_guider_harness import (
    CAP_MS, Camera, Logs, Mount, ScriptedEngine, make_guider, pair, profile_id,
    pulse)

native_only = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                 reason="native wheel absent")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------------- settle before walk


@native_only
@pytest.mark.asyncio
async def test_the_walk_waits_for_the_mount_to_settle(monkeypatch):
    """T18. The walk's first exposure comes at least ``_CAL_SETTLE_S`` after
    the walk began (0.2 s here; 0.18 s asserted, above the 15.6 ms clock
    rule). The frame is blank, so the walk then refuses for want of a star,
    which does not matter: the first exposure's time is the measurement."""
    monkeypatch.setattr(native, "_CAL_SETTLE_S", 0.2)
    cam = Camera()
    g = make_guider(profile=None, camera=cam)
    g._engine = ScriptedEngine([])
    t0 = time.monotonic()
    with pytest.raises(DeviceError):
        await g._calibrate()
    assert cam.exposures, "premise: the walk exposed"
    assert cam.exposures[0] - t0 >= 0.18


@pytest.mark.asyncio
async def test_stop_during_the_settle_is_prompt(monkeypatch):
    """T19. A Stop 0.1 s into a 5 s settle ends the walk within 1 s with
    GuidingStopped, before any exposure."""
    monkeypatch.setattr(native, "_CAL_SETTLE_S", 5.0)
    cam = Camera()
    g = make_guider(profile=None, camera=cam)
    g._engine = ScriptedEngine([])

    async def _stop_soon():
        await asyncio.sleep(0.1)
        g._stop.set()

    stopper = asyncio.ensure_future(_stop_soon())
    with pytest.raises(GuidingStopped):
        await asyncio.wait_for(g._calibrate(), timeout=1.0)
    await stopper
    assert cam.exposures == []


# ------------------------------------------------------------ the geometry


@native_only
@pytest.mark.asyncio
async def test_every_completed_walk_logs_its_geometry(monkeypatch):
    """T20 (the real engine on the sim). One geometry line per completed walk,
    naming both axis angles, the orthogonality error and the legs; the legs'
    step counts add up to the calibration pulses the mount received."""
    logs = Logs(monkeypatch)
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=profile_id("t20"))
    await g.connect()
    walk_pulses = []
    real_pulse = tel.pulse_guide

    async def _count(direction, ms):
        if g._loop_task is None:
            walk_pulses.append((direction, ms))
        return await real_pulse(direction, ms)

    tel.pulse_guide = _count
    try:
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        lines = logs.at("info", native.L_GEOMETRY_HEAD)
        assert len(lines) == 1, lines
        line = lines[0]
        assert "RA axis" in line and "Dec axis" in line
        assert "orthogonality error" in line and "legs:" in line
        steps = [int(n) for n in re.findall(r"(\d+) steps", line)]
        assert walk_pulses, "premise: the walk pulsed"
        assert sum(steps) == len(walk_pulses)
    finally:
        tel.pulse_guide = real_pulse
        await g.stop_guiding()
        await g.disconnect()


@native_only
@pytest.mark.asyncio
async def test_a_failed_walk_logs_its_legs(monkeypatch):
    """T21g. A walk whose star goes for good fails as before, and first says
    which legs it walked and how far each moved the star."""
    monkeypatch.setattr(native, "_CAL_STARLESS_S", 1.0)
    logs = Logs(monkeypatch)
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=f"test-oct08-t21g-{uuid.uuid4().hex[:8]}")
    await g.connect()
    real_expose, real_pulse = cam.expose, tel.pulse_guide
    state = {"pulses": 0}
    rng = np.random.default_rng(7)

    def calibrating() -> bool:
        return getattr(g, "_phase_hint", None) == "calibrating"

    async def _pulse(direction, ms):
        if calibrating():
            state["pulses"] += 1
        return await real_pulse(direction, ms)

    async def _expose(*a, **kw):
        frame = await real_expose(*a, **kw)
        if calibrating() and state["pulses"] >= 3:
            data = np.asarray(frame.data)
            frame.data = rng.integers(480, 520,
                                      size=data.shape).astype(data.dtype)
        return frame

    tel.pulse_guide = _pulse
    cam.expose = _expose
    with pytest.raises(DeviceError):
        await g.start_guiding()
    warned = logs.at("warning", native.L_WALK_LEGS_HEAD)
    assert len(warned) == 1, warned
    assert "steps" in warned[0]
    await g.stop_guiding()


# ------------------------------------------------- saturated-pulse counters


@pytest.mark.asyncio
async def test_corrections_at_the_limit_are_counted_by_direction():
    """T21. A correction AT the limit counts (a runaway's pulses are exactly
    at the cap and the old over-cap warning never saw them); one under does
    not; an unclamped recenter step over it counts and still reaches the
    mount capped."""
    mount = Mount()
    g = make_guider(profile=None, mount=mount)
    assert g._axis_limit_ms == {"ra": CAP_MS, "dec": CAP_MS}, (
        "premise: the real _build_engine_config set the limits to the cap")
    await g._pulse(pulse("east", 1000))
    assert g.saturated_pulses()["east"] == 1
    await g._pulse(pulse("east", 999))
    assert g.saturated_pulses()["east"] == 1
    await g._pulse(pair(ra=("east", 2658)))
    assert g.saturated_pulses()["east"] == 2
    assert mount.pulses[-1] == ("east", CAP_MS)
    await g._pulse(pair(dec=("north", 1000)))
    assert g.saturated_pulses()["north"] == 1
    assert g.saturated_pulses()["south"] == 0
    assert g.saturated_pulses()["west"] == 0


@native_only
@pytest.mark.asyncio
async def test_counts_survive_a_restart(monkeypatch):
    """T22. The counters live for the guider's lifetime: the engine diffs two
    reads across an exposure, and a restart between them must not take
    counts away."""
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    tel.max_pulse_ms = 500
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=profile_id("t22"))
    await g.connect()
    try:
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        assert g._axis_limit_ms == {"ra": 500, "dec": 500}
        g._note_saturated("east", 500)
        g._note_saturated("east", 500)
        g._note_saturated("north", 600)
        before = g.saturated_pulses()
        assert before["east"] >= 2 and before["north"] >= 1
        await g.stop_guiding()
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        after = g.saturated_pulses()
        assert after["east"] >= before["east"]
        assert after["north"] >= before["north"]
    finally:
        await g.stop_guiding()
        await g.disconnect()


# ------------------------------------------------------- the frame record


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


class _Reporter:
    def __init__(self):
        self.records: list[FrameRecord] = []

    def record_frame(self, rec):
        self.records.append(rec)


def _target():
    return Target(name="NGC 7331", ra_hours=22.6, dec_deg=34.4, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])


@pytest.mark.asyncio
async def test_the_frame_record_carries_corrections_at_the_limit(
        sim_hub, monkeypatch):
    """T23. The guider's lifetime counts read 3 east when the shutter opens
    and 60 when the frame is recorded: the frame record says 57, and the night
    log says so in one warning line."""
    logs = Logs(monkeypatch)
    counts = iter([{"east": 3, "north": 0}, {"east": 60, "north": 0}])
    monkeypatch.setattr(sim_hub.guider, "saturated_pulses",
                        lambda: next(counts), raising=False)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True)
    rep = e.reporter = _Reporter()
    t = _target()
    e._begin_frame(0, 0, 0.01)
    e._reporter_record(t, t.steps[0], {}, accepted=True)
    assert rep.records and rep.records[-1].guide_capped == {"east": 57}
    lines = logs.at("warning", "guide pulse(s) at the pulse limit")
    assert lines == ["57 guide pulse(s) at the pulse limit during this "
                     "exposure of NGC 7331 (east 57)"]


@pytest.mark.asyncio
async def test_a_guider_that_cannot_count_records_none(sim_hub, monkeypatch):
    """T23b. A guider with no counters (PHD2, the legacy sim guider) records
    None and logs nothing."""
    logs = Logs(monkeypatch)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True)
    rep = e.reporter = _Reporter()
    t = _target()
    e._begin_frame(0, 0, 0.01)
    e._reporter_record(t, t.steps[0], {}, accepted=True)
    assert rep.records[-1].guide_capped is None
    assert logs.at("warning", "guide pulse(s) at the pulse limit") == []


@pytest.mark.asyncio
async def test_the_count_is_logged_without_a_reporter(sim_hub, monkeypatch):
    """T23c. The night-log line does not depend on a session report."""
    logs = Logs(monkeypatch)
    counts = iter([{"east": 0}, {"east": 4}])
    monkeypatch.setattr(sim_hub.guider, "saturated_pulses",
                        lambda: next(counts), raising=False)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True)
    e.reporter = None
    t = _target()
    e._begin_frame(0, 0, 0.01)
    e._reporter_record(t, t.steps[0], {}, accepted=True)
    assert len(logs.at("warning", "4 guide pulse(s) at the pulse limit")) == 1


def test_an_old_report_loads_without_the_field():
    """T24. Every report written before the field loads unchanged."""
    assert FrameRecord.model_validate({"ts": 1.0, "target": "x"}).guide_capped is None


# ------------------------------------------------------------------- stamps


def test_stats_say_when_and_which_window():
    """T25. ``stats()`` carries when it was composed and the first and last
    sample time of the window its RMS covers."""
    g = make_guider(profile=None)
    eng = ScriptedEngine([])
    eng.recent = [[100.0, 0.1, 0.1], [101.0, 0.2, 0.1], [103.0, 0.1, 0.2]]
    g._engine = eng
    before = time.time()
    st = g.stats()
    after = time.time()
    assert before <= st.as_of <= after
    assert st.rms_from == 100.0
    assert st.rms_to == 103.0


@pytest.mark.asyncio
async def test_the_trace_carries_its_stamp(sim_hub, monkeypatch):
    """T26. The monitor snapshot's guide trace says when it was read."""
    monkeypatch.setattr(sim_hub.guider, "stats",
                        lambda: GuideStats(guiding=True, as_of=1234.5,
                                           recent=[{"t": 1.0, "ra": 0.1,
                                                    "dec": 0.2}]))
    snap = await sim_hub.monitor_snapshot()
    assert snap["guide_as_of"] == 1234.5
    assert snap["guide_recent"] == [{"t": 1.0, "ra": 0.1, "dec": 0.2}]


# ------------------------------------------------------- fix round 1 (review)


@pytest.mark.asyncio
async def test_a_one_way_dec_mode_takes_no_blc_out():
    """FR1-8 (review B, mutant R6: the BLC add-on taken out in every Dec
    mode). engine.rs adds the static backlash pulse only in the auto Dec
    mode, and a recenter step fires whatever the mode. So in the north-only
    mode a south recenter step of 1100 ms after a north pulse carries no BLC,
    and it is over the 1000 ms limit: counted. The auto-mode control, the
    same two pulses with 200 ms of BLC, judges the request at 900 ms: not
    counted."""
    g = make_guider(profile=None, config={"dec_guide_mode": "north",
                                          "blc_pulse_ms": 200})
    await g._pulse(pair(dec=("north", 300)))
    await g._pulse(pair(dec=("south", 1100)))
    assert g.saturated_pulses()["south"] == 1
    auto = make_guider(profile=None, config={"dec_guide_mode": "auto",
                                             "blc_pulse_ms": 200})
    await auto._pulse(pair(dec=("north", 300)))
    await auto._pulse(pair(dec=("south", 1100)))
    assert auto.saturated_pulses()["south"] == 0, "premise: auto takes BLC out"


@pytest.mark.asyncio
async def test_a_new_guider_object_is_counted_from_zero(sim_hub, monkeypatch):
    """FR1-9 (review B, mutant R11: the guider-id test removed from
    ``_capped_during_frame``). The hub swapped its guider (a reconnect, a
    profile activation) while the shutter was open, and the new guider's
    lifetime count already reads 10. Nothing ties its 10 to the old guider's
    3, so the frame's count is the new guider's whole 10, not 7."""
    class _CountingGuider:
        connected = True

        def __init__(self, east):
            self.east = east

        def saturated_pulses(self):
            return {"east": self.east}

    monkeypatch.setattr(sim_hub, "guider", _CountingGuider(3))
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True)
    e._begin_frame(0, 0, 0.01)
    monkeypatch.setattr(sim_hub, "guider", _CountingGuider(10))
    assert e._capped_during_frame() == {"east": 10}
