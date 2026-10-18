# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071 round 2: the pulse probe measures the step each stage makes, and runs
the Dec stages.

The emulated guide rate is 1x sidereal, so a 1000 ms pulse should move the
mount's model about one ``:GR#`` count and a ten-pulse train about ten. Round 1
measured only the rate AFTER each stage, never whether the model moved by what
was commanded, which is the bench test the 2026-08-21 investigation asked for
and never ran. ``Telescope.measure_model_step`` is that measurement, and
``pulse_probe.run_protocol`` wraps every pulse stage in it. The protocol also
runs north and south pulses, because the 2026-10-16 02:34 event hit the limit
in Dec as well and the 08-21 walk was in Dec.

The mount is the real ``ZwoAm5Telescope`` over a fake link whose position is a
MODEL that the pulses move (the real pulse thread, real timing), so the step
the driver measures is graded against the displacement the pulses commanded.

Each test names the mutant of the production code it was shown RED under, from
a byte backup restored afterwards (sha256 checked).
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.hub as hub_module
import astrodeck.pulse_probe as pp
from astrodeck.devices.base import Telescope
from astrodeck.devices.sim import SimRig, SimTelescope
from astrodeck.events import night_key

from test_1071_probe_window import VClock, vclock  # noqa: F401
from test_zwo_am5 import FakeLink, _connect_script, fixed_env  # noqa: F401

#: Arcsec per second of a 1x sidereal guide rate.
RATE = 15.041


class PulseModelLink(FakeLink):
    """Position is a model. A pulse's start command begins a move at the
    guide rate and its stop command ends it, timed on the real clock (the
    driver's pulse thread is real): west and north advance RA and Dec, east
    and south go the other way. ``:GR#`` prints hundredths of a second of
    time and ``:GD#`` whole arcsec, as the real codec would."""

    START = {"Mw": "w", "Td": "e", "Mn": "n", "Ms": "s"}
    STOP = {"Qw": "w", "Te": "e", "Qn": "n", "Qs": "s"}

    def __init__(self, ra_s: float = 10 * 3600.0, dec: float = 20.0):
        super().__init__(_connect_script(GAT="1", Gm="E", Gps="0", Td="1",
                                         Te="1"))
        self.ra_s, self.dec = ra_s, dec
        self.active: tuple[str, float] | None = None
        self.moved_ms = {"w": 0.0, "e": 0.0, "n": 0.0, "s": 0.0}
        self.script["GR"] = self._gr
        self.script["GD"] = self._gd

    def arm(self) -> None:
        pass

    def _exchange(self, cmd, reply):
        out = super()._exchange(cmd, reply)
        now = time.monotonic()
        if cmd in self.START:
            self.active = (self.START[cmd], now)
        elif cmd in self.STOP and self.active is not None:
            d, t0 = self.active
            self.active = None
            dt = now - t0
            self.moved_ms[d] += dt * 1000.0
            sign = 1.0 if d in ("w", "n") else -1.0
            if d in ("w", "e"):
                self.ra_s += sign * dt * RATE / 15.0
            else:
                self.dec += sign * dt * RATE / 3600.0
        return out

    def _gr(self, _cmd: str) -> str:
        u = int(math.floor((self.ra_s % 86400.0) * 100 + 1e-6))
        h, rem = divmod(u, 360000)
        m, su = divmod(rem, 6000)
        return f"{h:02d}:{m:02d}:{su // 100:02d}.{su % 100:02d}"

    def _gd(self, _cmd: str) -> str:
        total = int(math.floor(abs(self.dec) * 3600.0 + 1e-6))
        d, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{'-' if self.dec < 0 else '+'}{d:02d}*{m:02d}:{s:02d}"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(am5, "PROBE_MIN_S", 0.1)
    return tmp_path


async def _tel(link) -> am5.ZwoAm5Telescope:
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    link.sent.clear()
    return tel


def _file(tmp):
    folder = tmp / "guide" / night_key()
    return sorted(folder.glob("*-pulse-probe*.json"))


# ------------------------------------------------- measure_model_step itself


async def test_a_ten_pulse_west_train_moves_the_model_by_what_was_commanded(
        fixed_env, storage):
    """Ten 150 ms west pulses at 1x sidereal: 22.6 arcsec of RA commanded,
    and the measured step is that, to within the pulse thread's timing. Dec
    does not move. The result is differences and flags only.

    MUTANT S-a (``d_ra`` without the ``_RA_ARCSEC_PER_HOUR`` scale, hours
    reported as arcsec): RED, a step of 0.006.
    MUTANT S-b (the after-read taken before ``await action()``): RED, a step
    of 0."""
    link = PulseModelLink()
    tel = await _tel(link)
    assert tel.can_measure_model_step is True

    async def train():
        for _ in range(10):
            await tel.pulse_guide("west", 150)

    step = await tel.measure_model_step(train)
    commanded = link.moved_ms["w"] / 1000.0 * RATE
    assert commanded == pytest.approx(10 * 0.150 * RATE, rel=0.25)
    assert step["ra_step_arcsec"] == pytest.approx(commanded, abs=0.5), step
    assert step["dec_step_arcsec"] == 0.0
    assert step["ra_quantum_arcsec"] == pytest.approx(0.15)
    assert step["dec_quantum_arcsec"] == 1.0
    assert step["disturbed"] is False and step["disturbed_by"] == []
    assert set(step) == {"ra_step_arcsec", "dec_step_arcsec", "seconds",
                         "ra_quantum_arcsec", "dec_quantum_arcsec",
                         "disturbed", "disturbed_by"}


async def test_east_and_south_steps_are_signed_as_the_mount_reports(
        fixed_env, storage):
    """East and south go the other way in the model; the steps are negative.

    MUTANT S-c (``abs()`` on the steps): RED."""
    link = PulseModelLink()
    tel = await _tel(link)
    east = await tel.measure_model_step(lambda: tel.pulse_guide("east", 400))
    south = await tel.measure_model_step(lambda: tel.pulse_guide("south", 400))
    assert east["ra_step_arcsec"] < -4.0, east
    assert south["dec_step_arcsec"] <= -3.0, south


async def test_the_ra_step_is_the_short_way_round_24_hours(fixed_env, storage):
    """A west pulse across 00:00:00 is a small positive step, not 24 hours
    back.

    MUTANT S-d (the ``(... + 12.0) % 24.0 - 12.0`` removed from the step):
    RED, about -1.3 million arcsec."""
    link = PulseModelLink(ra_s=86400.0 - 1.0)
    tel = await _tel(link)
    step = await tel.measure_model_step(lambda: tel.pulse_guide("west", 1000))
    assert 10.0 < step["ra_step_arcsec"] < 20.0, step


async def test_an_exception_from_the_action_propagates_and_skips_the_read(
        fixed_env, storage):
    """The action fails: the error reaches the caller, and the after-read was
    not taken (one RA and one Dec read on the wire, no more).

    MUTANT S-e (``await action()`` inside a ``try/except Exception: pass``):
    RED, nothing raised. MUTANT S-f (the after-read in a ``finally``): RED,
    two reads."""
    link = PulseModelLink()
    tel = await _tel(link)

    async def boom():
        raise RuntimeError("the action failed")

    with pytest.raises(RuntimeError):
        await tel.measure_model_step(boom)
    assert link.sent.count("GR") == 1 and link.sent.count("GD") == 1


async def test_it_does_not_hold_the_pulse_park_lock(fixed_env, storage):
    """Every ``pulse_guide`` takes the lock; a measurement that held it would
    deadlock its own pulses. Proven by the pulse completing, and by the lock
    being free while the action runs.

    MUTANT S-g (``async with self._pulse_park_lock`` around the body): RED,
    the action times out."""
    link = PulseModelLink()
    tel = await _tel(link)
    seen = {}

    async def action():
        seen["locked"] = tel._pulse_park_lock.locked()
        await tel.pulse_guide("west", 50)

    step = await asyncio.wait_for(tel.measure_model_step(action), 5.0)
    assert seen["locked"] is False
    assert step is not None


async def test_a_sync_inside_the_action_marks_the_step(fixed_env, storage):
    """A sync in the middle of a stage moves the model by the correction, not
    by the pulses: the step says so.

    MUTANT S-h (``disturbed`` always False in ``measure_model_step``): RED."""
    link = PulseModelLink()
    tel = await _tel(link)

    async def action():
        tel._note_model_change("sync")      # what sync() does on entry

    step = await tel.measure_model_step(action)
    assert step["disturbed"] is True and step["disturbed_by"] == ["sync"]


async def test_a_driver_that_cannot_measure_does_not_run_the_action():
    """The base method answers None WITHOUT running the action, which is why
    ``can_measure_model_step`` exists and ``pulse_probe`` checks it.

    MUTANT S-i (``can_measure_model_step = True`` on the base class): RED."""
    ran = []

    async def action():
        ran.append(1)

    assert Telescope.can_measure_model_step is False
    tel = SimTelescope(SimRig())
    assert tel.can_measure_model_step is False
    assert await tel.measure_model_step(action) is None
    assert ran == []


# ------------------------------------------------------- the protocol


def _short(monkeypatch):
    monkeypatch.setattr(pp, "PULSE_MS", 60)
    monkeypatch.setattr(pp, "TRAIN_PULSES", 3)
    monkeypatch.setattr(pp, "CADENCE_S", 0.15)
    monkeypatch.setattr(pp, "PROBE_SECONDS", 0.3)


def _tokens(link) -> list[str]:
    """The wire as a story: P for a probe (its closing pier-side read), and
    W E N S for pulse starts."""
    out = []
    for c in link.sent:
        if c == "Gm":
            out.append("P")
        elif c in ("Mw", "Mn", "Ms"):
            out.append(c[1].upper())
        elif c == "Td":
            out.append("E")
    return out


async def test_the_stages_run_in_the_new_order_with_the_dec_stages(
        fixed_env, storage, monkeypatch):
    """rest, west x1, east x1, west x3, east x3, north x1, south x1, north x3,
    south x3, rest again: ten stages, RA first, then Dec, then the rest.

    MUTANT P-a (the Dec stages removed from ``stage_plan``): RED, six stages.
    MUTANT P-b (south before north): RED, the wire order."""
    _short(monkeypatch)
    link = PulseModelLink()
    tel = await _tel(link)
    result = await pp.run_protocol(tel)
    assert result["completed"] is True and result["aborted"] is None
    assert [s["name"] for s in result["steps"]] == [
        "rest", "west x1", "east x1", "west x3", "east x3", "north x1",
        "south x1", "north x3", "south x3", "rest again"]
    assert [s["pulses"] for s in result["steps"]] == [0, 1, 1, 3, 3, 1, 1, 3,
                                                     3, 0]
    assert _tokens(link) == (
        ["P", "W", "P", "E", "P", "W", "W", "W", "P", "E", "E", "E", "P",
         "N", "P", "S", "P", "N", "N", "N", "P", "S", "S", "S", "P", "P"])
    assert _file(storage), "the result was not written"


async def test_each_pulse_stage_carries_the_step_it_made(fixed_env, storage,
                                                         monkeypatch):
    """A pulse stage has ``step`` (the measured dict) and ``pulse_total_ms``;
    a rest stage has neither a step nor pulses. The three-pulse west train
    moved RA by about three times the single pulse, and a north train moved
    Dec and not RA.

    MUTANT P-c (``_run_stage`` returning ``None`` for the step): RED.
    MUTANT P-d (``pulse_total_ms`` = ``pulse_ms``): RED."""
    _short(monkeypatch)
    monkeypatch.setattr(pp, "PULSE_MS", 400)
    monkeypatch.setattr(pp, "CADENCE_S", 0.45)
    link = PulseModelLink()
    tel = await _tel(link)
    result = await pp.run_protocol(tel)
    by = {s["name"]: s for s in result["steps"]}
    assert by["rest"]["step"] is None and by["rest"]["pulse_total_ms"] == 0
    assert by["rest again"]["step"] is None
    assert by["west x3"]["pulse_total_ms"] == 1200
    one, three = by["west x1"]["step"], by["west x3"]["step"]
    assert one["ra_step_arcsec"] == pytest.approx(0.4 * RATE, abs=1.5), one
    assert three["ra_step_arcsec"] == pytest.approx(3 * one["ra_step_arcsec"],
                                                    rel=0.3), (one, three)
    north = by["north x3"]["step"]
    assert north["dec_step_arcsec"] > 10.0 and north["ra_step_arcsec"] == 0.0
    assert by["south x3"]["step"]["dec_step_arcsec"] < -10.0
    data = json.loads(_file(storage)[0].read_text(encoding="utf-8"))
    assert [s["step"] is None for s in data["steps"]] == [
        True, False, False, False, False, False, False, False, False, True]


async def test_a_driver_without_the_measurement_still_runs_every_stage(
        fixed_env, storage, monkeypatch):
    """No ``can_measure_model_step``: the pulses are run by the protocol
    itself and the steps are None. (A mount that cannot measure a step can
    still be probed.)

    MUTANT P-e (``_run_stage`` always calling ``tel.measure_model_step``):
    RED, the steps are not None. (On a driver whose base method does not run
    the action the wire would also show no pulses; ``can_measure_model_step``
    exists to prevent exactly that.)"""
    _short(monkeypatch)
    link = PulseModelLink()
    tel = await _tel(link)
    monkeypatch.setattr(tel, "can_measure_model_step", False)
    result = await pp.run_protocol(tel)
    assert result["completed"] is True
    assert all(s["step"] is None for s in result["steps"])
    assert _tokens(link).count("W") == 4 and _tokens(link).count("S") == 4


async def test_a_stop_in_the_middle_of_a_stage_still_writes_the_partial_result(
        fixed_env, storage, monkeypatch):
    """The job is cancelled during the west x3 train: the file is written
    with the stages that finished, ``aborted`` says why, and ``interrupted``
    names the stage and how many pulses had gone out. The pulse in flight is
    stopped (Mw and Qw balance).

    MUTANT P-f (``interrupted`` never recorded): RED.
    MUTANT P-g (the ``finally`` that writes the result removed): RED, no
    file."""
    _short(monkeypatch)
    monkeypatch.setattr(pp, "PULSE_MS", 120)
    monkeypatch.setattr(pp, "CADENCE_S", 0.3)
    link = PulseModelLink()
    tel = await _tel(link)
    task = asyncio.create_task(pp.run_protocol(tel))
    while link.sent.count("Mw") < 3:        # the single pulse, then two of the train
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    files = _file(storage)
    assert files
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["completed"] is False and data["aborted"] == "stopped by request"
    assert [s["name"] for s in data["steps"]] == ["rest", "west x1", "east x1"]
    assert data["interrupted"]["name"] == "west x3"
    assert 1 <= data["interrupted"]["pulses"] <= 2
    assert link.sent.count("Mw") == link.sent.count("Qw")


async def test_a_completed_run_has_no_interrupted_stage(fixed_env, storage,
                                                        monkeypatch):
    """MUTANT P-h (``result["interrupted"] = None`` after a stage removed):
    RED, the last stage is reported interrupted."""
    _short(monkeypatch)
    link = PulseModelLink()
    tel = await _tel(link)
    result = await pp.run_protocol(tel)
    assert result["completed"] is True and result["interrupted"] is None


# ---------------------------------------------------- the quoted duration


def test_the_expected_time_is_computed_from_the_constants():
    """ten probes of 10 s, four single 1 s pulses, four trains of ten at a
    3.7 s cadence (nine gaps and the last pulse): 100 + 4 + 4 x 34.3 s. The
    words the route and the docs quote follow from it.

    MUTANT T-a (``expected_seconds`` counting ``n`` cadences per train):
    RED. MUTANT T-b (the Dec stages dropped from the plan): RED."""
    assert pp.expected_seconds() == pytest.approx(241.2)
    assert pp.expected_minutes_words() == "about four minutes"
    assert pp.expected_seconds(probe_s=0.0, pulse_ms=1000, train=10,
                               cadence_s=3.7) == pytest.approx(141.2)
    assert len(pp.stage_plan(10)) == 10


def test_the_route_note_and_the_docs_quote_the_computed_time():
    """The route's note and ``docs/guide/guiding.md`` say the same number the
    constants give; a change to a constant that moved the minute would make
    them disagree.

    MUTANT T-c (the note's words hard-coded to "about three minutes"): RED."""
    import pathlib
    import re
    import inspect
    import astrodeck.api.app as app_module
    src = inspect.getsource(app_module)
    assert "expected_minutes_words()" in src
    docs = (pathlib.Path(__file__).resolve().parents[2]
            / "docs" / "guide" / "guiding.md").read_text(encoding="utf-8")
    words = pp.expected_minutes_words()
    m = re.search(r"It takes (about \w+ minutes?);", docs)
    assert m and m.group(1) == words, (m and m.group(1), words)
    assert words in " ".join(pp.__doc__.split())
