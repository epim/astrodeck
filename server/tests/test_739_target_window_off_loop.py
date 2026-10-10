# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#739: the engine asked ``flows.tonight.target_own_window`` for a target's
own window synchronously, on the event loop, at every target setup
(`_await_target_window` before the slew, `_hold_for_light` after a dark
centring). The first call of a night builds ``catalog.visibility``'s astropy
scaffold (twilight scan, sun and moon over the whole night): seconds on a
loaded or Pi-class box, during which the status poll, the WS relay and the
safety loop shared the stalled loop. Seen once, under CPU load, as the group
harness's spin watchdog: "the event loop did not come back for 10.2 s of real
time".

The compute now runs in a thread. These tests grade that the loop KEEPS
RUNNING while it works, and that the answer the engine acts on is still the
one the function gives.

HOW THE STALL IS GRADED WITHOUT A STOPWATCH. The stand-in for the slow compute
cannot return until the event loop has run a callback of its own
(`loop.call_later(..., event.set)`). Called on the loop it waits out its
timeout with the loop frozen, so the callback never fires and the test fails;
called in a thread the callback fires at once. No wall-clock threshold, so a
loaded box cannot make it pass or fail by luck.

The site is the synthetic one test_w4_target_window_and_light_hold.py uses.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.flows.tonight import target_own_window
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import ExposureStep, Target

_SITE = {"latitude": 35.0, "longitude": -110.0, "elevation_m": 1200.0,
         "is_default": False}

#: How long the stand-in waits for the loop before giving up. Only a stalled
#: loop ever waits this long, so it is the cost of the FAILING path, not the
#: passing one.
_STALL_TIMEOUT_S = 3.0


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # ``Hub.site`` is a property over the config store; the engine reads it
    # when it asks for the window, and the suite's store holds no real site.
    monkeypatch.setattr(Hub, "site", property(lambda self: dict(_SITE)))
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(**kw) -> Target:
    base = dict(name="NGC-test", ra_hours=5.5, dec_deg=20.0, center=True,
                autofocus_first=True,
                steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    base.update(kw)
    return Target(**base)


def _compute_that_needs_the_loop(seen: dict):
    """A ``compute_night`` that is slow in the one way that matters: it cannot
    finish until the event loop has turned. Returns a window that closed an
    hour ago, so the engine's gate has nothing to wait for and returns."""
    loop_ran = threading.Event()
    asyncio.get_running_loop().call_later(0.05, loop_ran.set)

    def compute(ra_hours, dec_deg, *, date=None, step_min=20, alt_limit=0.0,
                site=None):
        seen["thread"] = threading.get_ident()
        seen["loop_kept_running"] = loop_ran.wait(timeout=_STALL_TIMEOUT_S)
        t = time.time()
        return {"best_window": {"start_unix": t - 7200.0,
                                "end_unix": t - 3600.0, "mean_alt": 40.0}}
    return compute


async def test_await_target_window_leaves_the_loop_running(
        sim_hub, monkeypatch):
    """NAMED MUTANT (run from a byte backup, restored and md5-verified): in
    ``_await_target_window`` (engine.py) replace

        window = await asyncio.to_thread(
            target_own_window,
            target.ra_hours, target.dec_deg, site=self.hub.site, ...)

    with the direct call ``window = target_own_window(target.ra_hours, ...)``.
    This test then fails with:

        assert seen["loop_kept_running"]  (False: the compute ran ON the
        loop and waited its whole timeout with the loop frozen)."""
    seen: dict = {}
    monkeypatch.setattr("astrodeck.catalog.visibility.compute_night",
                        _compute_that_needs_the_loop(seen))
    engine = SequenceEngine(sim_hub)

    await engine._await_target_window(_target())

    assert seen, "the engine never asked for the target's window"
    assert seen["loop_kept_running"], (
        "the window compute ran on the event loop and held it for its whole "
        "wait: nothing else on the loop (status poll, relay, safety) could "
        "run meanwhile")
    assert seen["thread"] != threading.get_ident()


async def test_hold_for_light_leaves_the_loop_running(sim_hub, monkeypatch):
    """NAMED MUTANT (run from a byte backup, restored and md5-verified): in
    ``_hold_for_light`` (engine.py) replace the ``await asyncio.to_thread(
    target_own_window, ...)`` with the direct call. This test then fails with
    the same ``assert seen["loop_kept_running"]``."""
    seen: dict = {}
    monkeypatch.setattr("astrodeck.catalog.visibility.compute_night",
                        _compute_that_needs_the_loop(seen))
    engine = SequenceEngine(sim_hub)
    centred = {"centered": True, "error_arcmin": 0.1}

    result = await engine._hold_for_light(_target(), None, centred)

    assert result is centred            # already centred: nothing to retry
    assert seen, "the engine never asked for the target's window"
    assert seen["loop_kept_running"], (
        "the window compute ran on the event loop and held it for its whole "
        "wait")
    assert seen["thread"] != threading.get_ident()


async def test_the_window_the_engine_acts_on_is_the_one_the_function_gives(
        sim_hub, monkeypatch):
    """Unchanged result, unstubbed astropy. The engine's gate forwards the
    target's RA/Dec, the site and its own altitude floor, and what the thread
    computes is what a plain call with the same arguments computes."""
    calls: list[tuple] = []

    def spy(*a, **kw):
        out = target_own_window(*a, **kw)
        calls.append((a, kw, out, threading.get_ident()))
        return out

    monkeypatch.setattr("astrodeck.flows.tonight.target_own_window", spy)
    engine = SequenceEngine(sim_hub)
    # A near-pole target: at the synthetic site (lat 35) it stays between 30
    # and 40 deg altitude all night, all year, so it clears the 20 deg floor
    # for the whole of every night's astronomical dark. An equatorial target
    # (RA 5.5 h, Dec +20) has no window at all from late April to mid August,
    # and this test would then fail by the date it runs, not by the code.
    target = _target(ra_hours=5.5, dec_deg=85.0)
    target.schedule.min_altitude_deg = 20.0

    # Closed-window verdicts return at once; either way the gate asked once.
    await engine._await_target_window(target)
    result = await engine._hold_for_light(
        target, None, {"centered": True, "error_arcmin": 0.1})
    assert result == {"centered": True, "error_arcmin": 0.1}

    assert len(calls) == 2
    for args, kwargs, out, tid in calls:
        assert args == (5.5, 85.0)
        assert kwargs["site"] == _SITE
        assert kwargs["min_altitude_deg"] == 20.0
        assert tid != threading.get_ident()
        # Not vacuous, on any date and at any hour the suite runs: this
        # target has a window every night (checked every 3 days of a year at
        # 0, 6, 12 and 18 h UTC).
        assert out is not None
        assert out["start_unix"] <= out["end_unix"]
        assert out == target_own_window(*args, **kwargs)


@pytest.mark.parametrize("gate", ["await_window", "hold_for_light"])
async def test_a_compute_that_raises_is_still_best_effort(
        sim_hub, monkeypatch, gate):
    """The gates' contract is that a window this cannot resolve is nothing to
    wait for, never a new way to stop a run. An exception now arrives through
    the thread's future instead of the call; it must be swallowed the same."""
    def boom(*a, **kw):
        raise RuntimeError("astropy blew up")

    monkeypatch.setattr("astrodeck.flows.tonight.target_own_window", boom)
    engine = SequenceEngine(sim_hub)

    if gate == "await_window":
        assert await engine._await_target_window(_target()) is None
    else:
        centred = {"centered": True, "error_arcmin": 0.1}
        assert await engine._hold_for_light(_target(), None, centred) is centred
