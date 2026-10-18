# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071: the guide loop's flight recorder.

The runaway on pier east after a meridian flip could not be told apart from a
mount that had stopped tracking, because the night log kept the guider's
sentences and none of the cycles behind them. The native guider now keeps the
last 60 cycles (and the calibration walk) in a ring and writes it to
``captures/guide/<night>/<HHMMSS>-<event>.json`` when a calibration is
discarded, the star is lost or re-acquired, five pulses in a row hit the
limit, or a guided frame carries the "pulses at the pulse limit" note, with
one night-log line naming the file.

Real code under test: ``NativeGuider._guide_loop``, ``_judge_frame``,
``_stop_for_bad_calibration``, ``_handle_lock_lost``, ``_note_lock``, the
engine's ``_reporter_record`` and ``FlightRecorder``. Only the Rust engine and
the mount are doubles (the Oct-08 harness and the #204 re-lock harness).

Each test names the mutant of production code it was shown RED under. The
mutants were applied to a byte backup of the file and the file restored from
the backup (never git checkout); the observed failures are in the report.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import astrodeck.hub as hub_module
from astrodeck.events import night_key
from astrodeck.guide import flight_recorder as fr_mod
from astrodeck.guide.flight_recorder import FlightRecorder
from astrodeck.sequence.engine import SequenceEngine

from _oct08_guider_harness import (
    CAP_MS, Mount, ScriptedEngine, arm_loop, cal_dict, make_guider,
    profile_id, pulse, stop_loop, wait_until)
from test_guider_relock_nearest_star import (
    _A, _A_BACK, _B, _IDLE, _LOST, _PULSE, _harness)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return tmp_path


def _files(tmp_path):
    folder = tmp_path / "captures" / "guide" / night_key()
    return sorted(folder.glob("*.json")) if folder.exists() else []


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _named(files, event):
    return [f for f in files if f.name.endswith(f"-{event}.json")
            or f"-{event}-" in f.name]


async def _run_to_end(g, eng, timeout=3.0):
    task = arm_loop(g, eng)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    finally:
        if not task.done():
            await stop_loop(g)
    await g._fr.flush()


# --------------------------------------------------- the runaway's two files


@pytest.mark.asyncio
async def test_a_runaway_writes_the_streak_file_then_the_discard_file(
        _isolated, bus_lines):
    """Eight east frames at the 1000 ms limit with the RA error growing 2 px a
    frame (11 arcsec at 5.5 arcsec/px). The fifth pulse of the streak writes
    ``limit-streak`` with 5 cycles; the trip on the sixth frame writes
    ``cal-discarded-runaway`` with 6, the last one with no pulse sent (the
    guider stopped itself before it). The night log names each file and says
    the cycle count, the RA trend (+11.0 arcsec/cycle), and that every pulse
    was at the limit.

    MUTANT A (the ``dump_flight_recorder("cal-discarded-...")`` call removed
    from ``_stop_for_bad_calibration``): RED, no ``cal-discarded-runaway``
    file.
    MUTANT B (``s["len"] == RUNAWAY_PULSES`` -> ``s["len"] >= RUNAWAY_PULSES``):
    RED, two ``limit-streak`` files (the sixth frame writes one too).
    """
    g = make_guider(profile=profile_id("fr1"), mount=Mount())
    eng = ScriptedEngine([(pulse("east", CAP_MS), (3.0 + 2.0 * k, 0.0))
                          for k in range(8)])
    await _run_to_end(g, eng)

    files = _files(_isolated)
    streak = _named(files, "limit-streak")
    disc = _named(files, "cal-discarded-runaway")
    assert len(streak) == 1 and len(disc) == 1, [f.name for f in files]

    s = _load(streak[0])
    assert s["event"] == "limit-streak"
    assert len(s["cycles"]) == 5
    # The file is written as the fifth frame is judged, before its pulse
    # goes out: four sent, the fifth asked for.
    assert all(len(c["pulses"]) == 1 and c["pulses"][0]["at_limit"]
               and c["pulses"][0]["dir"] == "east"
               and c["pulses"][0]["sent_ms"] == CAP_MS
               and not c["pulses"][0]["capped"] for c in s["cycles"][:4])
    assert s["cycles"][4]["pulses"] == []
    assert s["cycles"][4]["asked"] == [{"dir": "east", "ms": CAP_MS}]

    d = _load(disc[0])
    assert len(d["cycles"]) == 6
    assert d["cycles"][-1]["pulses"] == [], "the trip is before the pulse"
    assert d["cycles"][0]["dt"] is None
    assert all(c["dt"] is not None and c["dt"] >= 0 for c in d["cycles"][1:])
    first = d["cycles"][0]
    assert first["ra_err_px"] == 3.0 and first["ra_err_arcsec"] == 16.5
    assert first["pier_side"] in ("east", "unknown")
    assert d["unit"] == "arcsec" and d["image_scale_arcsec_px"] == 5.5
    assert set(d["calibration"]) <= {"x_rate", "y_rate", "x_angle", "y_angle",
                                     "y_angle_error", "is_valid"}

    night = night_key()
    lines = [m for lv, m, src in bus_lines
             if "flight recorder" in m and lv == "info"]
    assert len(lines) == 2, lines
    assert f"guide/{night}/{disc[0].name}" in lines[1]
    assert "6 cycles" in lines[1]
    assert "RA error trend +11.0 arcsec/cycle over the last 6" in lines[1]
    assert "100% of 5 pulses at the limit" in lines[1]
    assert "mean cycle" in lines[1]
    assert f"guide/{night}/{streak[0].name}" in lines[0]


@pytest.mark.asyncio
async def test_four_at_the_limit_write_nothing(_isolated):
    """A streak of four is not five: no file, no line.

    MUTANT C (``== RUNAWAY_PULSES`` -> ``== RUNAWAY_PULSES - 1``): RED, a
    ``limit-streak`` file after four frames."""
    g = make_guider(profile=profile_id("fr2"), mount=Mount())
    script = [(pulse("east", CAP_MS), (3.0 + 2.0 * k, 0.0)) for k in range(4)]
    eng = ScriptedEngine(script)
    task = arm_loop(g, eng)
    try:
        assert await wait_until(lambda: not eng.script and eng.frames > 6, 2.0)
    finally:
        await stop_loop(g)
    await g._fr.flush()
    assert _files(_isolated) == []


@pytest.mark.asyncio
async def test_a_failed_probation_writes_the_not_held_file(_isolated):
    """The other discard path: ten measured frames, the last five asking for
    the limit in alternating directions with the error flat (no streak grows),
    so the probation fails ("did not hold"), not the runaway gate. A streak
    file is a MID-RUN event and is not written during a probation.

    MUTANT D (the ``cal-discarded-not-held`` name made the runaway one): RED,
    no ``cal-discarded-not-held`` file.
    MUTANT D2 (none): the streak-during-probation case below grades E."""
    g = make_guider(profile=profile_id("fr3"), mount=Mount())
    first = (pulse("north", 200), (0.5, 0.3))
    script = [first] + [(pulse("east", 100), (2.0, 0.3))] * 5 + [
        (pulse("east" if k % 2 == 0 else "west", CAP_MS), (2.0, 0.3))
        for k in range(5)]
    eng = ScriptedEngine(script)
    task = arm_loop(g, eng, probation=True)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    finally:
        if not task.done():
            await stop_loop(g)
    await g._fr.flush()
    files = _files(_isolated)
    assert [f.name.split("-", 1)[1] for f in files] == [
        "cal-discarded-not-held.json"], [f.name for f in files]
    assert len(_load(files[0])["cycles"]) == 11


@pytest.mark.asyncio
async def test_a_streak_during_the_probation_is_not_a_mid_run_event(_isolated):
    """Five east pulses at the limit with the error flat, in the probation's
    last five frames: the probation fails ("did not hold") and writes its one
    file; the mid-run ``limit-streak`` file is not written as well.

    MUTANT E (``and self._probation is None`` removed from the streak
    test): RED, a ``limit-streak`` file beside the discard."""
    g = make_guider(profile=profile_id("fr4"), mount=Mount())
    script = ([(pulse("north", 200), (0.5, 0.3))]
              + [(pulse("east", 100), (2.0, 0.3))] * 4
              + [(pulse("east", CAP_MS), (2.0, 0.3))] * 5)
    task = arm_loop(g, ScriptedEngine(script), probation=True)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    finally:
        if not task.done():
            await stop_loop(g)
    await g._fr.flush()
    assert [f.name.split("-", 1)[1] for f in _files(_isolated)] == [
        "cal-discarded-not-held.json"]


# ----------------------------------------------- star lost and re-acquired


@pytest.mark.asyncio
async def test_a_spell_of_lost_frames_writes_one_star_lost_file_and_one_reacquire(
        _isolated, monkeypatch):
    """Two lost frames in a row, then the star back 0.5 px from the lock: ONE
    ``star-lost`` file (the first loss of the spell, not one per lost frame)
    and ONE ``reacquire`` file.

    MUTANT F (``self._reacquire == 1 or`` removed, so every loss writes):
    RED, two ``star-lost`` files.
    MUTANT G (the ``reacquire`` dump removed from ``_note_lock``): RED, no
    ``reacquire`` file."""
    g, _e = _harness(
        monkeypatch, [_IDLE, _PULSE, _LOST, _LOST, _IDLE, _PULSE],
        [[_A, _B], [_A, _B], [], [], [_B, _A_BACK], [_B, _A_BACK]])
    await g._guide_loop()
    await g._fr.flush()
    files = _files(_isolated)
    assert len(_named(files, "star-lost")) == 1, [f.name for f in files]
    assert len(_named(files, "reacquire")) == 1, [f.name for f in files]
    lost = _load(_named(files, "star-lost")[0])
    assert len(lost["cycles"]) == 3
    assert lost["cycles"][-1]["action"] == "lock_lost"
    assert len(_load(_named(files, "reacquire")[0])["cycles"]) == 5


@pytest.mark.asyncio
async def test_the_different_star_stop_writes_a_file(_isolated, monkeypatch):
    """The 2026-10-17 02:37 shape: the lock star never comes back and the
    engine keeps guiding on another one; the guider's own stop writes
    ``relock-stop`` so the cycles of the chase are kept.

    MUTANT H (the dump removed from ``_stop_for_relock``): RED, no file."""
    from astrodeck.guide.native import RELOCK_UNCONFIRMED_FRAMES as N
    g, _e = _harness(
        monkeypatch, [_IDLE, _PULSE, _LOST] + [_PULSE] * (N + 2),
        [[_A], [_A], []] + [[_B]] * (N + 2))
    await g._guide_loop()
    await g._fr.flush()
    assert len(_named(_files(_isolated), "relock-stop")) == 1


# ------------------------------------------------------- the frame note


@pytest.mark.asyncio
async def test_the_frame_note_writes_the_pulse_limit_file(_isolated):
    """The engine's "N guide pulse(s) at the pulse limit during this exposure"
    note asks the guider for its cycles, so the file holds the exposure.

    MUTANT I (the ``dump("pulse-limit-frame")`` call removed from
    ``SequenceEngine._reporter_record``): RED, no file."""
    g = make_guider(profile=profile_id("fr5"), mount=Mount())
    g._engine = ScriptedEngine([])
    for _ in range(4):
        g._record_cycle({"action": "idle"}, False)
    me = SimpleNamespace(
        _capped_during_frame=lambda: {"north": 25, "east": 25},
        hub=SimpleNamespace(guider=g), reporter=None)
    SequenceEngine._reporter_record(me, SimpleNamespace(name="T"), None, {},
                                    accepted=True)
    await g._fr.flush()
    files = _named(_files(_isolated), "pulse-limit-frame")
    assert len(files) == 1
    assert len(_load(files[0])["cycles"]) == 4


# ------------------------------------------------------ the bound, the walk


def test_files_per_night_are_capped_with_one_warning(_isolated, monkeypatch,
                                                      bus_lines):
    """The cap is 200 a night; shrunk to 3 here. Five dumps write three files
    and one warning, and the warning says when it lifts.

    MUTANT J (``>=`` -> ``>`` in the cap test): RED, four files."""
    monkeypatch.setattr(fr_mod, "MAX_FILES_PER_NIGHT", 3)
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    for k in range(5):
        r.dump(f"e{k}")
    assert len(_files(_isolated)) == 3
    warns = [m for lv, m, _s in bus_lines if lv == "warning"]
    assert len(warns) == 1 and "next night" in warns[0], warns


def test_the_cap_counts_files_already_on_disk(_isolated, monkeypatch):
    """A restart mid-night does not reset the cap: the directory is counted
    the first time a night is written to.

    MUTANT K (``_count`` returns 0 always): RED, 3 more files, 5 in all."""
    monkeypatch.setattr(fr_mod, "MAX_FILES_PER_NIGHT", 3)
    folder = _isolated / "captures" / "guide" / night_key()
    folder.mkdir(parents=True)
    for k in range(2):
        (folder / f"0000{k}0-old.json").write_text("{}", encoding="utf-8")
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    for k in range(4):
        r.dump(f"e{k}")
    assert len(_files(_isolated)) == 3


@pytest.mark.asyncio
async def test_two_events_in_one_second_do_not_share_a_file(_isolated):
    """The first write is still in the executor when the second event picks
    its name, so the name check cannot rely on the disk alone (found by the
    star-lost mutant: two files were counted and one was written).

    MUTANT P (``or path in self._taken`` removed): RED, one file."""
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    r.dump("x")
    r.dump("x")
    await r.flush()
    assert len(_files(_isolated)) == 2


async def test_a_cycles_monotonic_time_maps_to_wall_time(_isolated,
                                                          monkeypatch):
    """Each cycle's ``t`` is ``time.monotonic()`` and the file has two stamps
    taken together at the dump, ``written_at`` (wall) and ``mono_at_write``
    (monotonic): ``wall = written_at - (mono_at_write - t)``. Proven against
    a recorder whose clocks are fakes with a known offset: a cycle recorded
    at monotonic 1000 and wall W, dumped 40 s later, maps back to wall W.

    MUTANT F7-a (``mono_at_write`` taken from ``time.time()``): RED, the
    mapped time is off by the clock offset. MUTANT F7-b (the key removed):
    RED."""
    mono = {"v": 1000.0}
    wall = {"v": round(time.time(), 3)}
    w0 = wall["v"]
    real_time = fr_mod.time

    class _Clock:
        def __getattr__(self, name):
            return getattr(real_time, name)

        monotonic = staticmethod(lambda: mono["v"])
        time = staticmethod(lambda: wall["v"])

    monkeypatch.setattr(fr_mod, "time", _Clock())
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    mono["v"] += 40.0
    wall["v"] += 40.0
    r.dump("x")
    await r.flush()
    data = _load(_files(_isolated)[0])
    assert data["cycles"][0]["t"] == 1000.0
    assert data["mono_at_write"] == 1040.0
    mapped = data["written_at"] - (data["mono_at_write"] - data["cycles"][0]["t"])
    assert mapped == pytest.approx(w0, abs=1e-3)


def test_the_wall_time_formula_is_documented():
    """The module docstring carries the formula a reader needs.

    MUTANT F7-c (the docstring paragraph removed): RED."""
    assert "wall = written_at - (mono_at_write - t)" in fr_mod.__doc__


def test_the_calibration_walk_is_kept_step_by_step(_isolated):
    """Each step's leg, direction, commanded ms and the star's position before
    and after: the 'after' of one step is the 'before' of the next, and the
    last is closed by ``walk_end``.

    MUTANT L (``walk_step`` does not close the previous step): RED, null
    ``star_after_px`` on step 1."""
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    r.begin_walk()
    r.walk_step(1, "west", "west", 1000, (10.0, 20.0))
    r.walk_step(2, "west", "west", 1000, (12.5, 20.1))
    r.walk_step(3, "east", "east", 1000, (15.0, 20.2))
    r.walk_end((12.4, 20.1))
    r.dump("cal-test")
    walk = _load(_files(_isolated)[0])["calibration_walk"]
    assert [w["step"] for w in walk] == [1, 2, 3]
    assert walk[0]["star_after_px"] == walk[1]["star_before_px"] == [12.5, 20.1]
    assert walk[1]["star_after_px"] == walk[2]["star_before_px"]
    assert walk[2]["star_after_px"] == [12.4, 20.1]
    assert walk[0]["dir"] == "west" and walk[0]["ms"] == 1000


@pytest.mark.asyncio
async def test_calibrate_feeds_the_walk_to_the_recorder(_isolated,
                                                         monkeypatch):
    """The real ``_calibrate`` loop, with a scripted engine and a fake star
    find: its cal steps reach the recorder with the star position read from
    the frame, and a discard afterwards writes them out.

    MUTANT M (the ``self._fr.walk_step(...)`` call removed from
    ``_calibrate``): RED, an empty ``calibration_walk``."""
    import astrodeck.guide.native as nativemod

    class Star:
        def __init__(self):
            self.n = 0

        def guide_star_find(self, data):
            self.n += 1
            return [{"x": 100.0 + self.n, "y": 50.0, "snr": 30.0}], {}

    monkeypatch.setattr(nativemod, "_native", Star())
    monkeypatch.setattr(nativemod, "_CAL_SETTLE_S", 0)

    class CalEngine(ScriptedEngine):
        def begin_calibration(self, x, y):
            pass

        def calibration_advisories(self):
            return []

    steps = [({"action": "cal_step", "leg": "west", "dir": "west", "ms": 1000},
              None)] * 3
    g = make_guider(profile=profile_id("fr6"), mount=Mount())
    g._engine = CalEngine(steps, cal=cal_dict(), tail=(
        {"action": "idle"}, None))
    g._active = True
    await g._calibrate()
    g.dump_flight_recorder("walk-check")
    await g._fr.flush()
    walk = _load(_files(_isolated)[0])["calibration_walk"]
    assert [w["step"] for w in walk] == [1, 2, 3]
    assert all(w["dir"] == "west" and w["ms"] == 1000 for w in walk)
    assert walk[0]["star_before_px"] is not None
    assert walk[2]["star_after_px"] is not None


# ------------------------------------------------------- off the event loop


@pytest.mark.asyncio
async def test_the_write_is_off_the_event_loop(_isolated, monkeypatch):
    """With a loop running, the file is written by an executor thread, not by
    the loop's own.

    MUTANT N (``_submit`` calls ``self._write`` directly): RED, the write ran
    on the loop thread."""
    seen = []
    real = FlightRecorder._write

    def spy(path, payload):
        seen.append(threading.get_ident())
        real(path, payload)

    monkeypatch.setattr(FlightRecorder, "_write", staticmethod(spy))
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    r.dump("off-loop")
    await r.flush()
    assert seen and seen[0] != threading.get_ident()
    assert len(_files(_isolated)) == 1


def test_a_failure_to_write_never_raises(_isolated, monkeypatch, bus_lines):
    """The guide loop calls this on its hot path; a full disk is a warning.

    MUTANT O (the ``try/except`` removed from ``dump``): RED, OSError."""
    def boom(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(FlightRecorder, "_write", staticmethod(boom))
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    assert r.dump("x") is None
    assert any("could not write" in m for _lv, m, _s in bus_lines)
