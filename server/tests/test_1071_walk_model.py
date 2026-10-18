# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071 round 3: every calibration walk records how far the mount's own model
moved across each leg, beside how far the camera saw the star move.

A walk is already a pulse experiment with a camera: N pulses one way and the
star's displacement in guide-camera px. The third number is the mount's
reported RA and Dec across the same leg. With all three, a night on either pier
side tells a mount that works (camera and model both moved) from one that
believes it moved and did not (model only) and from a model that ignores the
pulse strategy (camera only, the #851 east case).

Two kinds of mount stand in these tests. ``ModelMount`` is the harness mount
with a model of its own that each pulse moves by a known amount per direction,
so the walk's bookkeeping is graded against numbers the test chose. The real
``ZwoAm5Telescope`` over the round-2 ``PulseModelLink`` grades the driver's
``model_mark`` / ``model_step_since`` and the walk end to end. The Rust engine
and the star find are the doubles of the flight-recorder tests; the host code
under test (``NativeGuider._calibrate``, ``_legs_summary``, the recorder) is
real.

Each test names the mutant of production code it was shown RED under, applied
to a byte backup that was restored afterwards (sha256 checked), never with
git checkout.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.guide.native as nativemod
import astrodeck.hub as hub_module
from astrodeck.devices.sim import SimRig, SimTelescope
from astrodeck.events import bus, night_key

from _oct08_guider_harness import (
    Logs, Mount, ScriptedEngine, arm_loop, cal_dict,
    make_guider, profile_id, pulse, stop_loop, wait_until)
from test_1071_model_step import PulseModelLink, RATE
from test_1071_privacy import (
    DEC_A, DEC_B, RA_A, RA_B, _dec_forms, _find, _forms)
from test_zwo_am5 import fixed_env  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(nativemod, "_CAL_SETTLE_S", 0)
    return tmp_path


class Star:
    """The star find: it walks ``dx`` px in x per call, so a leg's camera
    displacement is known."""

    def __init__(self, dx: float = 2.0):
        self.n = 0
        self.dx = dx

    def guide_star_find(self, data):
        self.n += 1
        return [{"x": 100.0 + self.dx * self.n, "y": 50.0, "snr": 30.0}], {}


class CalEngine(ScriptedEngine):
    def begin_calibration(self, x, y):
        pass


def _step(leg: str, ms: int = 500) -> tuple:
    return ({"action": "cal_step", "leg": leg, "dir": leg, "ms": ms}, None)


def _engine(*legs: tuple[str, int], fail: bool = False) -> CalEngine:
    """A scripted walk: ``legs`` is (direction, steps) pairs. ``fail`` ends it
    with a lock_lost so the walk raises after the last leg."""
    script = [_step(d) for d, n in legs for _ in range(n)]
    tail = (({"action": "lock_lost", "reason": "scripted"}, None) if fail
            else ({"action": "idle"}, None))
    return CalEngine(script, cal=cal_dict(), tail=tail)


class ModelMount(Mount):
    """The harness mount plus a model of its own, in the shape of the AM5
    driver's mark API: the readings (``_ra``, ``_dec``, in arcsec, synthetic)
    stay inside; marks are ids; a step is a difference.

    ``gain`` is the share of the commanded motion the model follows, per
    direction: 1.0 for a mount that works, 0.0 for one whose model ignores
    that pulse strategy (#851's east)."""

    can_measure_model_step = True
    RATE = RATE

    def __init__(self, *, ra0: float = 40000.0, dec0: float = 150000.0,
                 gain=None, **kw):
        super().__init__(**kw)
        self._ra, self._dec = ra0, dec0
        self.gain = {"west": 1.0, "east": 1.0, "north": 1.0, "south": 1.0}
        self.gain.update(gain or {})
        self._changes = 0
        self._marks: dict[int, tuple] = {}
        self._seq = 0
        self.on_pulse = None
        self.mark_reads = 0
        self.break_marks = False

    async def pulse_guide(self, direction, ms):
        await super().pulse_guide(direction, ms)
        d = self.gain[direction] * ms / 1000.0 * self.RATE
        if direction in ("west", "east"):
            self._ra += d if direction == "west" else -d
        else:
            self._dec += d if direction == "north" else -d
        if self.on_pulse is not None:
            await self.on_pulse(self, len(self.pulses))

    async def sync(self, ra_hours, dec_deg):
        self._changes += 1

    async def model_mark(self):
        self.mark_reads += 1
        if self.break_marks == "raise":
            raise RuntimeError("serial link dead")
        if self.break_marks == "hang":
            await asyncio.sleep(3600)
        self._seq += 1
        self._marks[self._seq] = (self._ra, self._dec, self._changes)
        return self._seq

    async def model_step_since(self, mark):
        held = self._marks.get(mark)
        if held is None:
            return None
        ra0, dec0, ch0 = held
        return {"ra_step_arcsec": round(self._ra - ra0, 2),
                "dec_step_arcsec": round(self._dec - dec0, 2),
                "seconds": 1.0, "ra_quantum_arcsec": 0.0,
                "dec_quantum_arcsec": 0.0,
                "disturbed": self._changes != ch0,
                "disturbed_by": ["sync"] if self._changes != ch0 else []}


def _geometry(logs: Logs) -> str:
    lines = logs.at("info", nativemod.L_GEOMETRY_HEAD)
    assert len(lines) == 1, lines
    return lines[0]


# ------------------------------------------------ the walk, the line, the file


async def test_each_leg_carries_the_models_step_and_the_line_says_it(
        monkeypatch):
    """West 3 x 500 ms and north 2 x 500 ms on a mount whose model follows
    the pulses at the guide rate: the west leg moves RA by 22.56 arcsec and
    Dec by 0, the north leg Dec by 15.04, and the success line carries the
    star in px and arcsec (5.5 arcsec/px) beside the model.

    MUTANT W1 (``_calibrate`` never sets ``legs[-1]["model"]`` on a leg
    change): RED, no "model" in the line. MUTANT W2 (``_legs_summary`` drops
    the model clause): RED."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    logs = Logs(monkeypatch)
    mount = ModelMount()
    g = make_guider(profile=profile_id("wm1"), mount=mount)
    g._engine = _engine(("west", 3), ("north", 2))
    g._active = True
    await g._calibrate()
    line = _geometry(logs)
    assert 'west 3 steps 1.5 s: star 6.0 px (33"), model RA +23" Dec +0"' \
        in line, line
    assert 'north 2 steps 1.0 s: star 4.0 px (22"), model RA +0" Dec +15"' \
        in line, line
    assert "disturbed" not in line


async def test_a_failed_walk_carries_the_same_three_numbers(monkeypatch):
    """The failure line is the same summary: the leg the walk died in is
    closed with its model step.

    MUTANT W3 (the ``except DeviceError`` branch no longer closes the last
    leg's model): RED, no model on the last leg."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    logs = Logs(monkeypatch)
    mount = ModelMount()
    g = make_guider(profile=profile_id("wm2"), mount=mount)
    g._engine = _engine(("west", 2), ("south", 2), fail=True)
    g._active = True
    with pytest.raises(nativemod.DeviceError):
        await g._calibrate()
    warned = logs.at("warning", nativemod.L_WALK_LEGS_HEAD)
    assert len(warned) == 1, warned
    assert 'west 2 steps 1.0 s: star 4.0 px (22"), model RA +15" Dec +0"' \
        in warned[0], warned[0]
    assert 'south 2 steps 1.0 s:' in warned[0]
    assert 'model RA +0" Dec -15"' in warned[0], warned[0]


async def test_a_model_that_ignores_east_pulses_shows_in_the_east_leg(
        monkeypatch):
    """The #851 case. East gain 0: the star moved 6 px over the east leg and
    the model moved 0, and the line shows both; the west leg, which the model
    follows, shows both moving.

    MUTANT W4 (the model step of a leg taken from the PREVIOUS leg's mark,
    ``leg_mark`` not refreshed on a leg change): RED, the east leg reports
    the west leg's motion on top of its own."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    logs = Logs(monkeypatch)
    mount = ModelMount(gain={"east": 0.0})
    g = make_guider(profile=profile_id("wm3"), mount=mount)
    g._engine = _engine(("west", 2), ("east", 3))
    g._active = True
    await g._calibrate()
    line = _geometry(logs)
    assert 'east 3 steps 1.5 s: star 6.0 px (33"), model RA +0" Dec +0"' \
        in line, line
    assert 'west 2 steps 1.0 s: star 4.0 px (22"), model RA +15" Dec +0"' \
        in line, line


async def test_a_sync_inside_a_leg_marks_that_legs_step_disturbed(monkeypatch):
    """A sync lands between pulses 3 and 4: the leg it fell in (the second,
    pulses 3 to 4) is marked disturbed and names it, the first is not.

    MUTANT W5 (``_leg_step`` drops ``disturbed``/``disturbed_by``): RED."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    logs = Logs(monkeypatch)
    mount = ModelMount()

    async def hook(m, n):
        if n == 3:
            await m.sync(1.0, 2.0)

    mount.on_pulse = hook
    g = make_guider(profile=profile_id("wm4"), mount=mount)
    g._engine = _engine(("west", 2), ("north", 2))
    g._active = True
    await g._calibrate()
    line = _geometry(logs)
    first, second = line.split("legs: ")[1].split("north")
    assert "disturbed" not in first
    assert "(disturbed: sync)" in second, line


async def test_the_flight_recorder_dump_gains_the_legs(monkeypatch, _isolated):
    """The dump has a ``calibration_legs`` list: name, steps, ms, the star's
    travel in px and the model step (differences only).

    MUTANT W6 (``walk_legs`` never called from ``_calibrate``): RED, an empty
    list. MUTANT W7 (``walk_legs`` drops ``model``): RED."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    mount = ModelMount(gain={"east": 0.0})
    g = make_guider(profile=profile_id("wm5"), mount=mount)
    g._engine = _engine(("west", 2), ("east", 2))
    g._active = True
    await g._calibrate()
    g.dump_flight_recorder("legs-check")
    await g._fr.flush()
    folder = _isolated / "captures" / "guide" / night_key()
    data = json.loads(sorted(folder.glob("*.json"))[0].read_text("utf-8"))
    legs = data["calibration_legs"]
    assert [(e["leg"], e["steps"], e["ms"]) for e in legs] == [
        ("west", 2, 1000), ("east", 2, 1000)]
    assert legs[0]["star_travel_px"] == 4.0
    assert legs[0]["model"]["ra_step_arcsec"] == pytest.approx(15.04, abs=0.01)
    assert legs[1]["model"]["ra_step_arcsec"] == 0.0
    assert legs[1]["model"]["disturbed"] is False


# ------------------------------------------------- a read that fails or hangs


async def test_a_mount_that_cannot_be_read_costs_the_walk_nothing(monkeypatch):
    """The mark raises on every leg: the walk completes, every leg has no
    model, and the line says nothing about one.

    MUTANT W8 (``_leg_mark`` without its ``except``): RED, the walk raises."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    logs = Logs(monkeypatch)
    mount = ModelMount()
    mount.break_marks = "raise"
    g = make_guider(profile=profile_id("wm6"), mount=mount)
    g._engine = _engine(("west", 2), ("north", 1))
    g._active = True
    await g._calibrate()
    line = _geometry(logs)
    assert "west 2 steps" in line and "model" not in line, line
    assert logs.has("native guider calibration complete")


async def test_a_mount_that_never_answers_is_bounded(monkeypatch):
    """A mark that hangs is abandoned after ``_MODEL_READ_TIMEOUT_S`` per
    read and the walk finishes.

    MUTANT W9 (``_leg_mark`` awaits ``model_mark()`` without ``wait_for``):
    RED, the test times out."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    monkeypatch.setattr(nativemod, "_MODEL_READ_TIMEOUT_S", 0.05)
    logs = Logs(monkeypatch)
    mount = ModelMount()
    mount.break_marks = "hang"
    g = make_guider(profile=profile_id("wm7"), mount=mount)
    g._engine = _engine(("west", 2), ("north", 1))
    g._active = True
    await asyncio.wait_for(g._calibrate(), timeout=5.0)
    assert "model" not in _geometry(logs)


async def test_a_mount_without_the_api_is_walked_as_before(monkeypatch):
    """The plain harness mount (no ``can_measure_model_step``): no mark is
    attempted and the line is the star's alone.

    MUTANT W10 (``_leg_mark`` without the ``can_measure_model_step``
    check): RED, the mount here has the method but the flag off, and the
    mutant reads it anyway (``mark_reads`` above 0)."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    logs = Logs(monkeypatch)
    mount = ModelMount()
    mount.can_measure_model_step = False
    g = make_guider(profile=profile_id("wm8"), mount=mount)
    g._engine = _engine(("west", 2))
    g._active = True
    await g._calibrate()
    assert mount.mark_reads == 0
    assert 'west 2 steps 1.0 s: star 4.0 px (22")' in _geometry(logs)
    assert "model" not in _geometry(logs)


# ------------------------------------------------------- the AM5 driver itself


async def _tel(link) -> am5.ZwoAm5Telescope:
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    link.sent.clear()
    return tel


async def test_marks_are_bounded_at_sixteen_and_an_unknown_mark_gives_none(
        fixed_env):
    """Twenty marks leave sixteen: the oldest four answer None, the newest
    sixteen answer a step. A never-issued id answers None. The base
    telescope answers None to both calls.

    MUTANT D1 (``_MODEL_MARKS_MAX`` raised to 64): RED. MUTANT D2
    (``popitem(last=True)``, the newest dropped): RED, the first mark
    survives and the last is gone."""
    tel = await _tel(PulseModelLink())
    ids = [await tel.model_mark() for _ in range(20)]
    assert ids == list(range(ids[0], ids[0] + 20))
    assert len(tel._model_marks) == 16
    for old in ids[:4]:
        assert await tel.model_step_since(old) is None
    for new in ids[4:]:
        step = await tel.model_step_since(new)
        assert step is not None and set(step) >= {
            "ra_step_arcsec", "dec_step_arcsec", "seconds", "disturbed",
            "disturbed_by", "ra_quantum_arcsec", "dec_quantum_arcsec"}
    assert await tel.model_step_since(10 ** 6) is None
    assert await tel.model_step_since(0) is None
    sim = SimTelescope(SimRig())
    assert sim.can_measure_model_step is False
    assert await sim.model_mark() is None
    assert await sim.model_step_since(1) is None


async def test_the_driver_step_since_a_mark_is_what_was_commanded(fixed_env):
    """Five 200 ms west pulses after a mark: the step is that many arcsec
    of RA, Dec holds. A sync after the mark marks it disturbed, a mark
    taken after the sync is clean, and a mark can be asked twice.

    MUTANT D3 (``model_step_since`` takes ``changes`` from the current
    counter instead of the mark's): RED, the sync goes unseen. MUTANT D4
    (``model_step_since`` reads a fresh "before" instead of using the
    stored one): RED, a step of about 0."""
    link = PulseModelLink()
    tel = await _tel(link)
    mark = await tel.model_mark()
    for _ in range(5):
        await tel.pulse_guide("west", 200)
    step = await tel.model_step_since(mark)
    commanded = link.moved_ms["w"] / 1000.0 * RATE
    assert commanded == pytest.approx(5 * 0.2 * RATE, rel=0.3)
    assert step["ra_step_arcsec"] == pytest.approx(commanded, abs=0.5), step
    assert step["dec_step_arcsec"] == 0.0
    assert step["disturbed"] is False and step["disturbed_by"] == []
    again = await tel.model_step_since(mark)
    assert again["ra_step_arcsec"] == pytest.approx(commanded, abs=0.5)
    tel._note_model_change("sync")      # what sync() does on entry
    assert (await tel.model_step_since(mark))["disturbed_by"] == ["sync"]
    fresh = await tel.model_mark()
    assert (await tel.model_step_since(fresh))["disturbed"] is False


async def test_the_stored_readings_never_leave_the_driver(fixed_env):
    """The mark is an int and nothing returned, raised or repr'd carries the
    reading. (The synthetic start is 10 h 20 min 30 s.)

    MUTANT D5 (``model_mark`` returns the stored tuple): RED, and the end
    to end tests with it. MUTANT P1 (``model_step_since`` adds
    ``"ra_hours": ra1``): RED. MUTANT P2 (adds ``"dec_deg": dec1``): RED."""
    link = PulseModelLink(ra_s=10 * 3600.0 + 20 * 60 + 30, dec=33.3333)
    tel = await _tel(link)
    mark = await tel.model_mark()
    assert type(mark) is int
    step = await tel.model_step_since(mark)
    blob = json.dumps(step) + repr(mark) + repr(tel)
    assert not _find(_ra_forms(10 * 3600.0 + 20 * 60 + 30), blob)
    assert not _find(_dec_forms(33.3333), blob)


# ------------------------------------------------- the real driver, end to end


def _ra_forms(ra_s: float) -> list[str]:
    """``_forms`` plus the hours TRUNCATED at 3 and 4 decimals: the mount
    prints RA in hundredths of a second, so a leaked reading in hours is
    truncated, where ``_forms`` only rounds."""
    h = ra_s / 3600.0
    return _forms(ra_s) + [str(int(h * 1000) / 1000), str(int(h * 10000) / 10000)]


async def _am5_walk(monkeypatch, ra_s: float, dec: float, tmp):
    """The walk on the real AM5 driver over the pulse-model link, from the
    given synthetic absolute position. Returns (lines, file text, legs)."""
    monkeypatch.setattr(nativemod, "_native", Star(2.0))
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp)
    lines: list[str] = []
    monkeypatch.setattr(
        bus, "log", lambda level, message, source="hub", **kw: lines.append(
            message))
    link = PulseModelLink(ra_s=float(ra_s), dec=dec)
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    g = make_guider(profile=profile_id("wm9"), mount=tel)
    g._engine = _engine(("west", 3), ("north", 2))
    g._engine_cal = None
    g._active = True
    await g._calibrate()
    g.dump_flight_recorder("walk-privacy")
    await g._fr.flush()
    files = sorted((tmp / "guide" / night_key()).glob("*.json"))
    assert files
    return lines, files[0].read_text(encoding="utf-8"), link


async def test_the_walk_on_the_real_driver_matches_what_the_link_moved(
        monkeypatch, fixed_env, tmp_path):
    """End to end: the AM5 driver's own pulses move the link's model, and the
    legs line and the recorder file carry steps matching what moved, to the
    pulse thread's timing.

    MUTANT E1 (``model_step_since`` returning the step in hours of RA, the
    ``_RA_ARCSEC_PER_HOUR`` factor dropped): RED."""
    lines, file_text, link = await _am5_walk(monkeypatch, RA_A, DEC_A,
                                             tmp_path)
    legs = json.loads(file_text)["calibration_legs"]
    commanded_w = 3 * 0.5 * RATE
    commanded_n = 2 * 0.5 * RATE
    assert legs[0]["model"]["ra_step_arcsec"] == pytest.approx(
        commanded_w, rel=0.3)
    assert legs[1]["model"]["dec_step_arcsec"] == pytest.approx(
        commanded_n, rel=0.3)
    assert abs(legs[1]["model"]["ra_step_arcsec"]) < 1.0
    geometry = [m for m in lines if nativemod.L_GEOMETRY_HEAD in m]
    assert len(geometry) == 1 and ", model RA +" in geometry[0], geometry


async def test_a_walk_leaks_neither_coordinate_under_two_synthetic_sites(
        monkeypatch, fixed_env, tmp_path):
    """The walk on the real driver from two synthetic absolute RAs and two
    declinations: no rendering of either appears in any log line, the
    recorder file or the legs, and the legs' numbers are the same to within
    the pulse timing (a number that tracks the site would differ by hours).

    The walk has two whitelists past the driver (``_leg_step`` and
    ``walk_legs``), so a driver leak alone does not reach an output: MUTANT
    P1 alone, and with ``_leg_step`` returning ``dict(m)``, stay green HERE
    (red in the driver test above), and MUTANT P4 (P1 plus ``_leg_step``
    returning ``dict(m)`` plus ``walk_legs`` copying ``dict(m)``): RED."""
    seen = {}
    # The mount prints Dec in whole arcsec, so a leaked reading is the value
    # rounded to the arcsec; the forms searched for must be of THAT value.
    for tag, ra, dec in (("a", RA_A, round(DEC_A * 3600) / 3600.0),
                         ("b", RA_B, round(DEC_B * 3600) / 3600.0)):
        sub = tmp_path / tag
        sub.mkdir()
        with monkeypatch.context() as mp:
            lines, file_text, _link = await _am5_walk(mp, ra, dec, sub)
        text = "\n".join(lines) + file_text
        assert not _find(_ra_forms(ra), text), (tag, _find(_ra_forms(ra), text))
        assert not _find(_dec_forms(dec), text), (tag,)
        legs = json.loads(file_text)["calibration_legs"]
        seen[tag] = [(e["model"]["ra_step_arcsec"],
                      e["model"]["dec_step_arcsec"]) for e in legs]
    for (ra_a, dec_a), (ra_b, dec_b) in zip(seen["a"], seen["b"]):
        assert ra_a == pytest.approx(ra_b, abs=3.0)
        assert dec_a == pytest.approx(dec_b, abs=3.0)


# ------------------------------------------------------------ the guide loop


async def test_the_guide_loop_records_a_null_model_rate_and_reads_nothing_more(
        monkeypatch):
    """No free cache exists: the hub's status poller reads the mount every
    2 s and keeps neither the reading nor its time, and the AM5 driver's
    ``_last_pos`` has no stamp. So every cycle records the model rates as
    null, and the guide loop makes NO read of the mount's position, marks or
    steps.

    MUTANT L1 (``begin_cycle`` recording ``0.0`` for the RA rate): RED, a
    non-null rate."""
    mount = ModelMount()
    reads = {"n": 0}
    real_pos, real_mark = mount.get_position, mount.model_mark

    async def pos():
        reads["n"] += 1
        return await real_pos()

    async def mark():
        reads["n"] += 1
        return await real_mark()

    mount.get_position, mount.model_mark = pos, mark
    g = make_guider(profile=profile_id("wm10"), mount=mount)
    eng = ScriptedEngine(
        [(pulse("east", 300), (1.0 + k, 0.0)) for k in range(6)],
        cal=cal_dict())
    task = arm_loop(g, eng)
    try:
        assert await wait_until(lambda: len(g._fr.ring) >= 6, 5.0)
    finally:
        await stop_loop(g)
    cycles = list(g._fr.ring)
    assert len(cycles) >= 4
    assert all(c["model_ra_rate_arcsec_s"] is None
               and c["model_dec_rate_arcsec_s"] is None for c in cycles)
    assert reads["n"] == 0, "the guide loop read the mount for its record"
