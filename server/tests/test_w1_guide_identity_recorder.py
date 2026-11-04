# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1085, #1089: the guide flight recorder carries the guide star's HFR and
flux, and the mount model's rate, on every cycle.

Before, each cycle's ``hfr`` was always null (the native engine's ``stats()``
exported only SNR) and ``model_ra_rate_arcsec_s`` / ``model_dec_rate_arcsec_s``
were always null (nothing kept a timestamped mount reading to difference), so
a mount fault could not be told from a sky fault from the recorder alone: a
star that fades and swells while the error grows is cloud or dew, one that
keeps its flux and HFR while it walks away is the mount, and the model rate
beside the camera error shows whether the mount integrates the pulses it is
sent.

The star's shape comes from the engine (``star_flux``, ``star_hfd`` in
``stats()``), forwarded on a measured cycle. The model rate comes from the
driver's mark API (``model_mark`` / ``model_step_since``), read once per cycle
by a task the loop does not wait for, started after the cycle's pulse: the
readings stay in the driver and only differences leave it (#140).

The scripted tests replace only the Rust engine and the mount's model; the
host code under test (``_record_cycle``, ``_schedule_model_read``,
``_read_model_step``, ``FlightRecorder``) is real. The last test runs the real
engine on the sim rig through the privately built wheel.

Each test names the mutant it was shown RED under, from a byte backup of the
production file restored afterwards (sha256 checked).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

import astrodeck.guide.native as nativemod
import astrodeck.hub as hub_module
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.flight_recorder import FlightRecorder
from astrodeck.guide.native import NativeGuider

from _oct08_guider_harness import (
    IDLE, Mount, ScriptedEngine, arm_loop, cal_dict, make_guider, profile_id,
    pulse, stop_loop, wait_until)
from test_1071_privacy import DEC_A, DEC_B, RA_A, RA_B, _dec_forms, _forms
from test_1071_walk_model import ModelMount
from test_w1_guide_identity import LOCK_XY, NEEDS_STAR_STATS, ShapeEngine


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return tmp_path


def _step(ra=7.5, dec=-1.5, seconds=2.5, **kw) -> dict:
    return {"ra_step_arcsec": ra, "dec_step_arcsec": dec, "seconds": seconds,
            "disturbed": False, "disturbed_by": [], **kw}


# ------------------------------------------------------------- the recorder


def test_a_cycle_carries_the_stars_radius_and_flux():
    """``hfd`` 6.0 px is an HFR of 3.0 px; the flux is kept as it is. A cycle
    with no star shape (an unmeasured frame) has both null.

    MUTANT F1 (``hfr`` recorded as the diameter): RED, 6.0 == 3.0. MUTANT F2
    (``flux`` always None): RED, None == 1234.5."""
    fr = FlightRecorder()
    rec = fr.begin_cycle(hfd=6.0, flux=1234.5, measured=True)
    assert rec["hfr"] == 3.0
    assert rec["flux"] == 1234.5
    bare = fr.begin_cycle()
    assert bare["hfr"] is None and bare["flux"] is None


def test_the_model_rate_is_the_step_over_the_seconds_it_took():
    """7.5 arcsec of RA and -1.5 of Dec over 2.5 s is 3.0 and -0.6 arcsec/s.

    MUTANT M1 (the division by ``seconds`` dropped): RED, 7.5 == 3.0."""
    fr = FlightRecorder()
    rec = fr.begin_cycle()
    fr.note_model_step(rec, _step())
    assert rec["model_ra_rate_arcsec_s"] == 3.0
    assert rec["model_dec_rate_arcsec_s"] == -0.6


@pytest.mark.parametrize("step", [
    None,
    "a string",
    _step(disturbed=True, disturbed_by=["goto"]),
    _step(seconds=0.0),
    _step(seconds=-1.0),
    _step(seconds=3600.0),
    {"ra_step_arcsec": 1.0, "dec_step_arcsec": 1.0},
    _step(ra=float("nan")),
], ids=["no-step", "not-a-dict", "disturbed", "zero-seconds",
        "negative-seconds", "longer-than-a-cycle-gap", "no-seconds",
        "nan-step"])
def test_no_rate_is_recorded_when_the_window_cannot_be_trusted(step):
    """No step, a window a sync or slew disturbed, a zero, negative or
    cycle-gap-long duration, a missing field and a non-finite step all leave
    both rates null rather than record a number that means nothing.

    MUTANT M2 (the ``disturbed`` test dropped): RED on the disturbed case.
    MUTANT M3 (the ``seconds`` range test dropped): RED on the negative and
    long cases (a zero duration still ends in the division error the
    recorder swallows)."""
    fr = FlightRecorder()
    rec = fr.begin_cycle()
    fr.note_model_step(rec, step)
    assert rec["model_ra_rate_arcsec_s"] is None
    assert rec["model_dec_rate_arcsec_s"] is None


# ----------------------------------------------------- through the guide loop


class WatchedMount(ModelMount):
    """``ModelMount`` that counts position reads (a trap: the model task is
    total and would swallow an exception raised here) and logs the order of
    its calls."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.events: list[str] = []
        self.position_reads = 0

    async def get_position(self):
        self.position_reads += 1
        return await super().get_position()

    async def pulse_guide(self, direction, ms):
        self.events.append("pulse-start")
        await asyncio.sleep(0.005)
        await super().pulse_guide(direction, ms)
        self.events.append("pulse-end")

    async def model_mark(self):
        self.events.append("mark")
        return await super().model_mark()

    async def model_step_since(self, mark):
        self.events.append("step")
        return await super().model_step_since(mark)


def _shaped_script(n):
    """``n`` measured cycles, each with a pulse and its own flux and HFD."""
    script = [(pulse("east", 300), (1.0 + 0.1 * k, 0.0)) for k in range(n)]
    stars = [(LOCK_XY[0], LOCK_XY[1], 1000.0 + k, 3.0 + 0.1 * k)
             for k in range(n)]
    return script, stars


async def _record(mount, n=8, *, engine_cls=ShapeEngine):
    g = make_guider(profile=profile_id("w1rec"), mount=mount)
    script, stars = _shaped_script(n)
    eng = engine_cls(script, stars, cal=cal_dict())
    arm_loop(g, eng)
    try:
        assert await wait_until(lambda: len(g._fr.ring) >= n
                                and len(mount.pulses) >= n, 5.0)
        # the last cycle's read is a task of its own: let it land
        await wait_until(lambda: g._model_task is None
                         or g._model_task.done(), 2.0)
    finally:
        await stop_loop(g)
    return g, list(g._fr.ring)[:n]


async def test_rows_carry_radius_flux_and_model_rates():
    """Eight measured cycles on a mount whose model follows the pulses: every
    row has the engine's flux (1000 + k) and an HFR of half its HFD, and from
    the second cycle on every row has the model's rates, the east pulses
    showing as RA motion and none as Dec.

    MUTANT G1 (``_record_cycle`` passes no shape): RED, ``hfr`` None.
    MUTANT G2 (``_schedule_model_read`` never starts the task): RED, no row
    has a rate."""
    mount = WatchedMount()
    g, cycles = await _record(mount)
    assert len(cycles) == 8
    assert mount.position_reads == 0, "the guide loop read the position"
    for k, c in enumerate(cycles):
        assert c["flux"] == 1000.0 + k, c
        assert c["hfr"] == round((3.0 + 0.1 * k) / 2.0, 3), c
    rated = [c for c in cycles if c["model_ra_rate_arcsec_s"] is not None]
    assert len(rated) >= 6, [c["model_ra_rate_arcsec_s"] for c in cycles]
    assert cycles[0]["model_ra_rate_arcsec_s"] is None, (
        "the first cycle has no mark to difference against")
    for c in rated:
        # 300 ms east at the model's guide rate, over the double's 1.0 s window
        assert c["model_ra_rate_arcsec_s"] < 0.0, c
        assert c["model_dec_rate_arcsec_s"] == 0.0


async def test_an_unmeasured_cycle_records_no_star_shape():
    """The engine keeps the last accepted star's shape across a frame it
    rejected, so a cycle that measured nothing must not carry it as its own.

    MUTANT G3 (the ``measured`` gate dropped in ``_record_cycle``): RED, the
    rejected frame carries a flux."""
    g = make_guider(profile=profile_id("w1unm"), mount=WatchedMount())
    script = [(pulse("east", 300), (1.0, 0.0)), (IDLE, None),
              (pulse("east", 300), (1.0, 0.0))]
    stars = [(LOCK_XY[0], LOCK_XY[1], 1000.0, 3.0)] * 3
    eng = ShapeEngine(script, stars, cal=cal_dict())
    arm_loop(g, eng)
    try:
        assert await wait_until(lambda: len(g._fr.ring) >= 3, 5.0)
    finally:
        await stop_loop(g)
    first, middle, last = list(g._fr.ring)[:3]
    assert first["flux"] == 1000.0 and last["flux"] == 1000.0
    assert middle["measured"] is False
    assert middle["flux"] is None and middle["hfr"] is None


async def test_the_loop_never_waits_for_the_mount_model():
    """A mount whose model read hangs costs the guide loop nothing: the
    pulses keep their cadence while the read sits in its own task, which is
    why the read starts after the pulse and is never awaited.

    MUTANT L1 (the loop awaits ``_read_model_step``): RED, fewer than six
    pulses in two seconds."""
    mount = WatchedMount()
    mount.break_marks = "hang"
    g = make_guider(profile=profile_id("w1hang"), mount=mount)
    script, stars = _shaped_script(8)
    arm_loop(g, ShapeEngine(script, stars, cal=cal_dict()))
    try:
        assert await wait_until(lambda: len(mount.pulses) >= 6, 2.0), (
            f"{len(mount.pulses)} pulses in 2 s with the model read hung")
        assert mount.events.count("mark") == 1, "reads queued behind a hang"
    finally:
        await stop_loop(g)


async def test_the_read_is_scheduled_after_the_cycles_pulse_has_gone():
    """The model read must not sit between a frame and its correction, so it
    is scheduled once the pulse has completed: for every cycle the order is
    pulse starts, pulse ends, read scheduled. (Each pulse here takes a few
    milliseconds, so a read scheduled before the dispatch would be seen
    ahead of its pulse.)

    MUTANT L2 (``_schedule_model_read`` moved before the dispatch): RED,
    ``schedule`` ahead of the first ``pulse-start``."""
    mount = WatchedMount()
    order: list[str] = []
    g = make_guider(profile=profile_id("w1ord"), mount=mount)
    real = g._schedule_model_read

    def spy(rec):
        order.append("schedule")
        mount.events.append("schedule")
        return real(rec)

    g._schedule_model_read = spy
    script, stars = _shaped_script(5)
    arm_loop(g, ShapeEngine(script, stars, cal=cal_dict()))
    try:
        assert await wait_until(lambda: len(order) >= 5, 5.0)
    finally:
        await stop_loop(g)
    cycle = [e for e in mount.events
             if e in ("pulse-start", "pulse-end", "schedule")]
    assert cycle[:15] == ["pulse-start", "pulse-end", "schedule"] * 5, cycle


async def test_a_mount_that_cannot_measure_its_model_is_never_asked():
    """The plain mount double has no model API (``can_measure_model_step``
    false, like every driver but the AM5's): nothing is read, and the rates
    stay null.

    MUTANT C1 (the capability test dropped from ``_schedule_model_read``):
    RED, the model task started."""
    mount = Mount()
    assert not getattr(mount, "can_measure_model_step", False)
    g, cycles = await _record(mount, n=5)
    assert g._model_task is None
    assert all(c["model_ra_rate_arcsec_s"] is None for c in cycles)
    assert all(c["flux"] is not None for c in cycles)


async def test_a_failed_model_read_leaves_the_rate_null_and_the_loop_running():
    """A mount whose model read raises costs its cycles their rates and
    nothing else: the loop pulses on, no cycle has a rate, and the next cycle
    asks the mount again (a failure does not leave the one-at-a-time slot
    taken).

    MUTANT E1 (``_schedule_model_read`` skipping whenever a task has ever
    been started, not only while one is running): RED, one read attempt
    instead of one per cycle."""
    mount = WatchedMount()
    mount.break_marks = "raise"
    g, cycles = await _record(mount, n=6)
    assert len(mount.pulses) >= 6
    assert all(c["model_ra_rate_arcsec_s"] is None for c in cycles)
    assert mount.events.count("mark") >= 3, mount.events


# --------------------------------------------------------------------- privacy


def _rows(cycles):
    return [{k: v for k, v in c.items() if k not in ("t", "dt")}
            for c in cycles]


async def test_no_position_reaches_the_rows_or_the_dump(tmp_path):
    """The same run on two mounts that differ only in where the model stands
    (two synthetic RA and Dec readings) records the same rates, and neither
    the ring nor the file the recorder writes carries either reading in any
    form. The mount's ``get_position`` counts reads, and the loop must make
    none. The readings stay in the driver; only differences leave.

    MUTANT P1 (``_read_model_step`` also reading ``tel.get_position()`` and
    storing its RA as the record's rate): RED, a position read and the two
    runs' rates differ."""
    runs = []
    for ra, dec in ((RA_A, DEC_A), (RA_B, DEC_B)):
        mount = WatchedMount(ra0=float(ra), dec0=dec * 3600.0)
        g, cycles = await _record(mount, n=8)
        assert mount.position_reads == 0, "the guide loop read the position"
        g.dump_flight_recorder("privacy")
        await g._fr.flush()
        dumped = "".join(p.read_text(encoding="utf-8") for p in
                         (tmp_path / "captures" / "guide").rglob("*.json"))
        runs.append((ra, dec, _rows(cycles), dumped))
        for p in (tmp_path / "captures" / "guide").rglob("*.json"):
            p.unlink()
    (ra_a, dec_a, rows_a, dump_a), (ra_b, dec_b, rows_b, dump_b) = runs
    for ra, dec, _rows_, dumped in runs:
        for form in _forms(ra) + _dec_forms(dec) + [f"{ra:.1f}",
                                                    f"{dec * 3600.0:.1f}"]:
            assert form not in dumped, f"{form!r} is in the dump"
    paired = 0
    for a, b in zip(rows_a, rows_b):
        if (a["model_ra_rate_arcsec_s"] is not None
                and b["model_ra_rate_arcsec_s"] is not None):
            paired += 1
            assert a["model_ra_rate_arcsec_s"] == b["model_ra_rate_arcsec_s"]
            assert a["model_dec_rate_arcsec_s"] == b["model_dec_rate_arcsec_s"]
    assert paired >= 4, "too few rated cycles to compare the two runs"


# ------------------------------------------------------ the sim mount's model


async def test_the_sim_mount_runs_the_action_and_measures_its_step():
    """The stock sim mount says it can measure its model, so the pulse probe
    hands it each stage's pulses through ``measure_model_step`` and relies on
    it to RUN them. A north pulse moves Dec only, and the mark it took is not
    kept afterwards.

    MUTANT S1 (``SimTelescope.measure_model_step`` absent, so the base
    method answers None without running the action): RED, the action never
    ran."""
    tel = build_sim_rig()["telescope"]
    await tel.connect()
    ran = []

    async def action():
        ran.append(1)
        await tel.pulse_guide("north", 1000)

    step = await tel.measure_model_step(action)
    assert ran == [1], "the action was not run"
    assert step["dec_step_arcsec"] > 0
    assert step["ra_step_arcsec"] == 0.0
    assert step["seconds"] > 0.0
    assert step["disturbed"] is False
    assert tel._model_marks == {}


async def test_the_sim_mounts_marks_are_bounded_and_an_unknown_mark_gives_none():
    """Sixteen marks are kept, the oldest dropped, as on the AM5 driver; a
    mark never given or already dropped answers None.

    MUTANT S2 (the bound loop removed from ``model_mark``): RED, 20 marks
    held."""
    tel = build_sim_rig()["telescope"]
    ids = [await tel.model_mark() for _ in range(20)]
    assert len(tel._model_marks) == 16
    assert await tel.model_step_since(ids[0]) is None
    assert await tel.model_step_since(ids[-1]) is not None
    assert await tel.model_step_since(10 ** 6) is None


# ------------------------------------------------------ the real engine, sim


@NEEDS_STAR_STATS
async def test_sim_rows_carry_non_null_hfr_flux_and_model_rates():
    """The done-when of #1107 item 1: on the sim, with the real engine,
    recorder rows carry a non-null HFR and flux on every measured cycle and a
    model rate on the cycles after the first, and the rates move (the guide
    pulses move the model).

    MUTANT G4 (the PyO3 layer publishing None for ``star_flux``, wheel
    rebuilt): RED, ``a measured cycle has no hfr``."""
    rig = build_sim_rig()
    cam, tel, srig = rig["guide_camera"], rig["telescope"], rig["_rig"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=f"test-w1rec-{uuid.uuid4().hex[:8]}")
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    # The ring holds 60 cycles and a fast sim fills it in milliseconds, so the
    # rows are collected as they are opened.
    seen: list[dict] = []
    real_begin = g._fr.begin_cycle

    def collect(**kw):
        rec = real_begin(**kw)
        seen.append(rec)
        return rec

    g._fr.begin_cycle = collect
    # Knock the star off the lock so the loop has corrections to make, whose
    # pulses move the mount's model.
    off_x, off_y = srig._guide_offset_px
    srig._guide_offset_px = (off_x + 6.0, off_y + 4.0)
    try:
        assert await wait_until(
            lambda: sum(1 for c in seen if c["measured"]) >= 30, 30.0, 0.02)
        await wait_until(lambda: g._model_task is None
                         or g._model_task.done(), 2.0)
        cycles = [c for c in seen if c["measured"]]
    finally:
        await g.stop_guiding()
        await g.disconnect()
    assert all(c["hfr"] is not None and c["hfr"] > 0 for c in cycles), (
        "a measured cycle has no hfr")
    assert all(c["flux"] is not None and c["flux"] > 0 for c in cycles), (
        "no measured cycle carries a flux")
    rated = [c for c in cycles if c["model_ra_rate_arcsec_s"] is not None]
    assert len(rated) >= 0.8 * len(cycles), (
        f"only {len(rated)} of {len(cycles)} rows rated")
    assert any(c["model_ra_rate_arcsec_s"] != 0.0
               or c["model_dec_rate_arcsec_s"] != 0.0 for c in rated)
