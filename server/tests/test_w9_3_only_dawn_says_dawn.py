# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1239: only a dawn ending says dawn.

A run whose last window closed said "stopped at dawn (windows closed)" whatever
closed it: a clock stop time, a target's ``max_run_min`` cap, or targets that
set or never rose. The detail and the night-log line now name the boundary that
closed the LAST window. ``end_reason`` stays ``dawn_cutoff`` for every one of
them (stored reports, the session machine and auto-resume read it), and the
tests below pin that too.

Every end-to-end case drives the REAL engine, as `test_a_short_night_stays_
resumable` does.

MUTANT "every window ending reads dawn" (`_window_end_phrase` returning
``_WINDOW_END_WORDS["dawn"]`` whatever was recorded): RED, the run ended by
``max_run_min`` with frames owed -
    AssertionError: a run ended by its run-length cap said:
    'stopped at dawn (windows closed)'
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Schedule, Target

#: A made-up site, not the observatory's. A dawn stop needs one saved.
LAT, LON = 40.0, -74.0


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    cfg = hub_module.config_store.cfg()
    monkeypatch.setattr(cfg.safety, "solar_avoidance", False)
    monkeypatch.setattr(cfg.site, "latitude", LAT)
    monkeypatch.setattr(cfg.site, "longitude", LON)
    monkeypatch.setattr(cfg.site, "is_default", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _target(name, schedule, count=8) -> Target:
    return Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
                  autofocus_first=False, schedule=schedule,
                  steps=[ExposureStep(filter="L", exposure_s=0.05,
                                      count=count)])


def _plan(name, targets) -> SequencePlan:
    return SequencePlan(name=name, guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=targets)


def _clock(offset_s: float) -> str:
    """Local HH:MM, ``offset_s`` from now (within the +-12 h the scheduler
    resolves a clock time to, so it holds at any hour and in any zone)."""
    return time.strftime("%H:%M", time.localtime(time.time() + offset_s))


def _close_the_window(eng, target) -> None:
    """The window closes UNDER a running frame loop: the per-frame boundary
    check is what sees it."""
    start, _stop = eng._frozen.get(id(target)) or (time.time() - 100, None)
    eng._frozen[id(target)] = (start, time.time() - 1)


async def _shoot_then_close(sim_hub, name, schedule):
    plan = _plan(name, [_target("A", schedule)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert eng.state.get("end_reason") == "dawn_cutoff", (
        "the stored reason is read by reports, sessions and auto-resume")
    return eng


def _end_lines(bus_lines, name):
    return [m for (_lvl, m, _src) in bus_lines
            if m.startswith(f"sequence '{name}'")]


async def test_a_dawn_stop_says_dawn(sim_hub, bus_lines):
    eng = await _shoot_then_close(
        sim_hub, "w93dawn", Schedule(stop_mode="dawn"))
    assert eng.state["detail"] == "stopped at dawn (windows closed)"
    assert "stopped at dawn:" in _end_lines(bus_lines, "w93dawn")[-1]


async def test_a_clock_stop_time_says_its_stop_time(sim_hub, bus_lines):
    eng = await _shoot_then_close(
        sim_hub, "w93time",
        Schedule(stop_mode="time", stop_time=_clock(6 * 3600)))
    assert eng.state["detail"] == "stopped at its stop time (windows closed)", (
        f"a run ended by its stop time said: {eng.state['detail']!r}")
    line = _end_lines(bus_lines, "w93time")[-1]
    assert "stopped at its stop time:" in line and "dawn" not in line, line


async def test_a_run_length_cap_says_its_cap_not_dawn(sim_hub, bus_lines):
    eng = await _shoot_then_close(
        sim_hub, "w93cap", Schedule(max_run_min=120))
    assert eng.state["detail"] == "stopped at its run-length cap (windows closed)", (
        f"a run ended by its run-length cap said: {eng.state['detail']!r}")
    line = _end_lines(bus_lines, "w93cap")[-1]
    assert "stopped at its run-length cap:" in line and "dawn" not in line, line


async def test_a_cap_later_than_dawn_is_not_the_cause(sim_hub):
    """The cap only counts when it came sooner than the stop it was folded
    into: a 10 hour cap on a dawn stop leaves dawn the boundary."""
    eng = await _shoot_then_close(
        sim_hub, "w93later", Schedule(stop_mode="dawn", max_run_min=60 * 40))
    assert eng.state["detail"] == "stopped at dawn (windows closed)"


async def test_a_stop_time_already_past_at_selection_says_its_stop_time(
        sim_hub, bus_lines):
    """The all-closed path at selection, not the frame loop's: the window was
    shut before the first frame."""
    plan = _plan("w93past", [_target(
        "A", Schedule(stop_mode="time", stop_time=_clock(-120)))])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert eng.state["end_reason"] == "dawn_cutoff"
    assert eng.state["detail"] == "stopped at its stop time (windows closed)"
    assert "stopped at its stop time:" in _end_lines(bus_lines, "w93past")[-1]


def _engine_with_windows(sim_hub, windows):
    """An engine holding frozen windows of ``(name, stop_ts, schedule)``."""
    eng = SequenceEngine(sim_hub)
    targets = [_target(n, s) for n, _ts, s in windows]
    eng._frozen = {id(t): (0.0, ts) for t, (_n, ts, _s) in zip(targets, windows)}
    return eng, targets


def test_the_last_window_to_close_names_the_ending(sim_hub):
    """Several windows closed for different reasons: the run says the reason
    of the LAST to close, whatever order they are noted in."""
    eng, (early, late) = _engine_with_windows(sim_hub, [
        ("early", 1000.0, Schedule(stop_mode="time", stop_time="01:00")),
        ("late", 2000.0, Schedule(stop_mode="dawn"))])
    eng._note_window_end(late)
    eng._note_window_end(early)
    assert eng._window_end_phrase() == "stopped at dawn"
    eng._window_end = None
    eng._note_window_end(early)
    eng._note_window_end(late)
    assert eng._window_end_phrase() == "stopped at dawn"
    eng._window_end = None
    eng._note_window_end(late)
    eng._frozen[id(early)] = (0.0, 3000.0)
    eng._note_window_end(early)
    assert eng._window_end_phrase() == "stopped at its stop time"


def test_no_window_recorded_means_targets_set_or_never_rose(sim_hub):
    eng = SequenceEngine(sim_hub)
    assert eng._window_end_phrase() == (
        "stopped with every target set or not yet risen")
