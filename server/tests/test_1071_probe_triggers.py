# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071: where the tracking-truth probe is asked for.

Three triggers, each a ten second look at the mount's own RA rate with nothing
moving it, one log line, and a failure that is logged and ignored:

  a. the native guider discards a calibration or detects a runaway (the
     guider has just stopped, so no pulse is pending);
  b. once, about 60 s after each real meridian flip, before guiding restarts;
  c. when the frame loop's #873 guard holds for guiding: once per retry
     spell, before the spell's wait (not once per attempt).

The mount doubles here implement ``probe_ra_drift`` and record the call; the
measurement itself is graded in ``test_1071_probe_ra_drift``. The code under
test is the real ``NativeGuider._stop_for_bad_calibration``, the real
``Hub.meridian_flip`` and the real ``SequenceEngine._hold_for_guiding``.

Each test names the mutant it was shown RED under, from a byte backup of the
production file restored afterwards.
"""
from __future__ import annotations

import asyncio
import functools
import time
from types import SimpleNamespace

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from astrodeck.guide.native import PROBATION_STOP_REASON, RUNAWAY_STOP_REASON
from astrodeck.sequence.engine import (GUIDE_RETRY_GAP_S, GUIDE_RETRY_WAIT_S,
                                       SequenceEngine,
                                       _MAX_GUIDING_RECOVERIES)

from _oct08_guider_harness import (CAP_MS, Mount, ScriptedEngine, arm_loop,
                                   make_guider, profile_id, pulse, stop_loop)
from _simhub import sim_hub  # noqa: F401 (fixture import)


def _result(frac: float = 0.0) -> dict:
    return {"ra_drift_arcsec_s": round(frac * 15.041, 3),
            "sidereal_fraction": frac, "tracking_flag_before": True,
            "tracking_flag_after": True, "seconds": 10.0,
            "pier_side": "east", "resolution_arcsec_s": 1.5,
            "disturbed": False, "cut_short": False}


class ProbeMount(Mount):
    can_probe_ra_drift = True

    def __init__(self, *, fail: bool = False, **kw):
        super().__init__(**kw)
        self.probes: list[tuple[float, float]] = []
        self.fail = fail

    async def probe_ra_drift(self, seconds):
        self.probes.append((seconds, time.monotonic()))
        if self.fail:
            raise DeviceError("the mount did not answer")
        return _result(0.9)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")


def _lines(bus_lines, needle="tracking probe"):
    return [m for _lv, m, _s in bus_lines if needle in m]


# ------------------------------------------------------------ trigger a


async def _runaway(g):
    eng = ScriptedEngine([(pulse("east", CAP_MS), (3.0 + 2.0 * k, 0.0))
                          for k in range(8)])
    task = arm_loop(g, eng)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    finally:
        if not task.done():
            await stop_loop(g)
    await g._fr.flush()


async def test_a_runaway_asks_the_mount_for_a_ten_second_probe(bus_lines):
    """The guider stops itself on a runaway: one probe of 10 s goes to the
    mount, off the loop, and one line says what it read.

    MUTANT 1 (``self._schedule_probe(...)`` removed from
    ``_stop_for_bad_calibration``): RED, no probe.
    MUTANT 2 (the runaway label and the discard label swapped): RED, the line
    names the wrong trigger."""
    mount = ProbeMount()
    g = make_guider(profile=profile_id("pa1"), mount=mount)
    await _runaway(g)
    await g._probe_task
    assert [s for s, _t in mount.probes] == [10.0]
    lines = _lines(bus_lines)
    assert len(lines) == 1 and lines[0].startswith(
        "tracking probe (runaway): RA drift +13.54 arcsec/s = +0.90 sidereal")
    assert g.stats().stop_reason == RUNAWAY_STOP_REASON


async def test_a_calibration_that_did_not_hold_asks_too(bus_lines):
    """The probation path ("the calibration did not hold the star") is the
    other discard.

    MUTANT 3 (the probe scheduled only when ``runaway``): RED, no probe."""
    mount = ProbeMount()
    g = make_guider(profile=profile_id("pa2"), mount=mount)
    script = [(pulse("north", 200), (0.5, 0.3))] + \
        [(pulse("east", 100), (2.0, 0.3))] * 5 + \
        [(pulse("east" if k % 2 == 0 else "west", CAP_MS), (2.0, 0.3))
         for k in range(5)]
    task = arm_loop(g, ScriptedEngine(script), probation=True)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    finally:
        if not task.done():
            await stop_loop(g)
    await g._fr.flush()
    await g._probe_task
    assert g.stats().stop_reason == PROBATION_STOP_REASON
    assert len(mount.probes) == 1
    assert _lines(bus_lines)[0].startswith(
        "tracking probe (calibration discarded)")


async def test_a_probe_that_fails_does_not_touch_the_guider(bus_lines):
    """The mount will not answer the probe. The guider has stopped exactly as
    it would have, the probe task ends without raising, and one warning says
    the probe failed.

    MUTANT 4 (``run_probe``'s ``except Exception`` removed): RED, the
    task raises DeviceError."""
    mount = ProbeMount(fail=True)
    g = make_guider(profile=profile_id("pa3"), mount=mount)
    await _runaway(g)
    await g._probe_task              # raises if the failure escaped
    assert g.stats().stop_reason == RUNAWAY_STOP_REASON
    assert not g._active
    warns = [m for lv, m, _s in bus_lines
             if lv == "warning" and "tracking probe (runaway) failed" in m]
    assert len(warns) == 1


async def test_a_mount_that_cannot_probe_costs_the_guider_nothing(bus_lines):
    """The plain mount double has no probe: no task, no line."""
    g = make_guider(profile=profile_id("pa4"), mount=Mount())
    await _runaway(g)
    assert g._probe_task is None or g._probe_task.done()
    assert _lines(bus_lines) == []


# ------------------------------------------------------------ trigger b


class _StubGuider:
    connected = True

    def __init__(self, order):
        self.order = order

    async def is_active(self):
        return True

    async def stop_guiding(self):
        self.order.append("stop")

    async def flip_calibration(self):
        self.order.append("flip_cal")
        return True

    async def start_guiding(self):
        self.order.append("start")


def _rig_flip(sim_hub, monkeypatch, *, sides, can_probe=True, fail=False):
    order: list = []
    tel = sim_hub.devices["telescope"]
    monkeypatch.setattr(tel, "can_probe_ra_drift", can_probe, raising=False)

    async def probe(seconds):
        order.append(("probe", seconds, time.monotonic()))
        if fail:
            raise DeviceError("no answer")
        return _result(0.0)

    monkeypatch.setattr(tel, "probe_ra_drift", probe, raising=False)

    async def goto(ra, dec, **kw):
        order.append(("goto", time.monotonic()))
        return {}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    it = iter(sides)

    async def side():
        return next(it)

    monkeypatch.setattr(sim_hub, "pier_side_now", side)
    sim_hub.guider = _StubGuider(order)
    return order


async def test_a_flip_probes_after_the_delay_and_before_guiding_restarts(
        sim_hub, monkeypatch, bus_lines):
    """A real flip (the pier side changed): stop, re-centre, flip the
    calibration, WAIT the delay, probe 10 s, and only then restart guiding.
    The line carries the minutes since the flip and is flagged site-derived.

    MUTANT 5 (the probe call moved below ``start_guiding``): RED, the order.
    MUTANT 6 (``await asyncio.sleep(FLIP_PROBE_DELAY_S)`` removed): RED, the
    probe starts with no delay after the re-centre."""
    monkeypatch.setattr(hub_module, "FLIP_PROBE_DELAY_S", 0.3)
    order = _rig_flip(sim_hub, monkeypatch, sides=["east", "west"])
    # The delay is graded by what was awaited, not by a clock: Windows'
    # timer lets a sleep return up to 15 ms early.
    real_sleep = asyncio.sleep

    async def spy(delay, *a, **k):
        if delay == 0.3:
            order.append("delay")
        return await real_sleep(delay, *a, **k)

    monkeypatch.setattr(asyncio, "sleep", spy)
    result = await sim_hub.meridian_flip(1.0, 2.0)
    assert result["flipped"] is True
    kinds = [o if isinstance(o, str) else o[0] for o in order]
    assert kinds == ["stop", "goto", "flip_cal", "delay", "probe",
                     "start"], kinds
    probe = next(o for o in order if o[0] == "probe")
    assert probe[1] == 10.0
    line = _lines(bus_lines)[0]
    assert line.startswith("tracking probe (after the meridian flip)")
    assert "0 min since the last flip" in line


async def test_a_flip_that_flipped_nothing_does_not_probe(sim_hub,
                                                           monkeypatch):
    """The lead-time attempt that changes nothing is not a flip.

    MUTANT 7 (``if flipped:`` removed from the probe call): RED."""
    monkeypatch.setattr(hub_module, "FLIP_PROBE_DELAY_S", 0.0)
    order = _rig_flip(sim_hub, monkeypatch, sides=["east", "east"])
    result = await sim_hub.meridian_flip(1.0, 2.0)
    assert result["flipped"] is False
    assert not [o for o in order if isinstance(o, tuple) and o[0] == "probe"]


async def test_a_mount_that_cannot_probe_is_not_waited_for(sim_hub,
                                                            monkeypatch):
    """Every mount without a probe (the sim, Alpaca) pays no 60 s.

    MUTANT 8 (the ``can_probe_ra_drift`` test removed from
    ``_probe_tracking_after_flip``): RED, the flip takes the whole delay."""
    monkeypatch.setattr(hub_module, "FLIP_PROBE_DELAY_S", 5.0)
    order = _rig_flip(sim_hub, monkeypatch, sides=["east", "west"],
                      can_probe=False)
    t0 = time.monotonic()
    await sim_hub.meridian_flip(1.0, 2.0)
    assert time.monotonic() - t0 < 2.0
    assert "start" in order


async def test_a_failed_flip_probe_still_restarts_guiding(sim_hub,
                                                           monkeypatch):
    """MUTANT 9 (``run_probe`` re-raises): RED, guiding is not restarted."""
    monkeypatch.setattr(hub_module, "FLIP_PROBE_DELAY_S", 0.0)
    order = _rig_flip(sim_hub, monkeypatch, sides=["east", "west"], fail=True)
    result = await sim_hub.meridian_flip(1.0, 2.0)
    assert result["flipped"] is True
    assert order[-1] == "start"


# ------------------------------------------------------------ trigger c


def _hold_self(tel, order, *, recoveries):
    ns = SimpleNamespace(
        _policy=SimpleNamespace(recover_guiding=True),
        _guiding_recoveries=recoveries, _cfg=None,
        _group_of=lambda t: None, _budget_key=lambda t: "alpha",
        _guide_retry_spells={}, _set_state=lambda **k: None,
        _sky_read_at_bound=1, _stand_down_said_at=1,
        hub=SimpleNamespace(devices={"telescope": tel},
                            minutes_since_flip=lambda: 30.0))

    async def wait(target, span_s):
        order.append(("wait", span_s))

    ns._guide_retry_wait = wait
    ns._probe_tracking = functools.partial(SequenceEngine._probe_tracking, ns)
    return ns


class _HoldTel:
    can_probe_ra_drift = True

    def __init__(self, order, fail=False):
        self.order, self.fail = order, fail

    async def probe_ra_drift(self, seconds):
        self.order.append(("probe", seconds))
        if self.fail:
            raise DeviceError("no answer")
        return _result(0.0)


async def test_a_retry_spell_probes_once_before_its_wait(bus_lines):
    """The attempts are spent, so the hold starts a spell: one probe, then
    the long wait.

    MUTANT 10 (the probe call moved after ``_guide_retry_wait``): RED, the
    order is wait then probe.
    MUTANT 11 (the probe call removed): RED, no probe."""
    order: list = []
    ns = _hold_self(_HoldTel(order), order,
                    recoveries=_MAX_GUIDING_RECOVERIES)
    await SequenceEngine._hold_for_guiding(ns, SimpleNamespace(name="T"))
    assert order == [("probe", 10.0), ("wait", GUIDE_RETRY_WAIT_S)], order
    line = _lines(bus_lines)[0]
    assert line.startswith("tracking probe (guiding hold)")
    assert "30 min since the last flip" in line


async def test_the_short_retries_between_attempts_do_not_probe(bus_lines):
    """Before the attempts are spent the hold waits the short gap and the next
    pass makes the next attempt: no probe per attempt.

    MUTANT 12 (the probe also called in the short branch): RED."""
    order: list = []
    ns = _hold_self(_HoldTel(order), order, recoveries=0)
    await SequenceEngine._hold_for_guiding(ns, SimpleNamespace(name="T"))
    assert order == [("wait", GUIDE_RETRY_GAP_S)], order


async def test_a_failed_hold_probe_does_not_stop_the_wait(bus_lines):
    """MUTANT 13 (``_probe_tracking``'s guard removed and ``run_probe``
    re-raising): RED, the spell never waits."""
    order: list = []
    ns = _hold_self(_HoldTel(order, fail=True), order,
                    recoveries=_MAX_GUIDING_RECOVERIES)
    await SequenceEngine._hold_for_guiding(ns, SimpleNamespace(name="T"))
    assert order == [("probe", 10.0), ("wait", GUIDE_RETRY_WAIT_S)], order
