# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Exactly one setup per acquisition (#241, H3 orchestrator ruling 4).

`_setup_target` opens with its pre-slew safety gate, and that gate can open
three holds: the frames' cloud hold (`_hold_for_clear`, safety armed with no
monitor), the open-sky safety pause (`_park_hold_pause`, ``on_unsafe =
pause``) and the roof reopen (`_await_safe_and_reopen`, with the roof closing
on unsafe and reopening when safe). Each one ran `_setup_target` for the
target itself when it released, and then handed back to the setup it had
interrupted, which ran its own slew, centring, focus sweep and guider start as
well. Two setups for one acquisition: on the rig a second focus sweep is 7 to
9 minutes of sky (measured 2026-09-08), on a night that has just lost time to
the weather, and the session log read as if the target was started twice.

THE RULING. A hold opened by `_setup_target`'s pre-slew gate returns to the
interrupted setup on release, which acquires the target once. A flag set and
cleared around that gate call, in a finally, marks it
(``_acquisition_behind_gate``). The release keeps the cooler gate and the
filter restore. The same rule holds for the safety pause and the roof reopen
when that gate opens them, and for a pause opened by the gate a cloud hold's
re-point asks (the re-point asks the whole slew gate, monitor half included,
so a pause can open there too). Holds opened from the frame loop, where no
setup is waiting, keep re-acquiring on release.

THE HARNESSES. The cloud hold runs on test_cloud_hold_watch's `_Watched`; the
pause and the roof on test_idle_park_hold's `_Clocked` with the simulator's
safety monitor and dome, the monitor's reading a script on the fake clock.
Both are the real scheduler, setup and frame loop. The hub's centring
(`goto_and_center`) and the engine's focus sweep (`_autofocus`) are replaced
by recording doubles that do what the real ones do to the mount (unpark,
track, slew) and nothing else, so each is counted, and nothing about a plate
solve is on the clock. The site is a fixture.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.devices.base import SafetyReading
from astrodeck.sequence import (ExposureStep, SequenceEngine, SequencePlan,
                                Target)

from test_cloud_hold_watch import EXP, _Watched, _plan
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _Clocked, _ra_at, sim_hub, temp_store)


def _target(name: str, ra: float, dec: float, *, count: int = 40,
            acquire: bool = True) -> Target:
    """A light target; ``acquire`` gives it what a Plan-built target has, a
    centred slew and a focus sweep at every setup."""
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=acquire,
                  autofocus_first=acquire,
                  steps=[ExposureStep(filter="L", exposure_s=EXP, gain=100,
                                      count=count)])


class _Acquisitions:
    """Every setup, centring, focus sweep and slew, by target, on the fake
    clock. Recording stops at the horizon, so the wind-down after the test's
    abort is not counted."""

    def __init__(self, run: _Clocked, monkeypatch):
        self.run = run
        self.setups: list[tuple[float, str]] = []
        self.centrings: list[tuple[float, float]] = []
        self.sweeps: list[tuple[float, str]] = []
        self.slews: list[tuple[float, float]] = []
        engine, hub = run.engine, run.hub
        tel = hub.devices["telescope"]
        real_setup = engine._setup_target

        async def setup(ti, target):
            if not run.frozen.is_set():
                self.setups.append((run.clock.t, target.name))
            return await real_setup(ti, target)

        monkeypatch.setattr(engine, "_setup_target", setup)
        inner_slew = tel.slew

        async def slew(ra_hours, dec_deg):
            if not run.frozen.is_set():
                self.slews.append((run.clock.t, ra_hours))
            return await inner_slew(ra_hours, dec_deg)

        monkeypatch.setattr(tel, "slew", slew)

        async def goto_and_center(ra_hours, dec_deg, **_kw):
            # What the hub's own does to the mount before it solves: unpark,
            # track, slew. The solve itself is not this test's.
            if not run.frozen.is_set():
                self.centrings.append((run.clock.t, ra_hours))
            if await tel.is_parked():
                await tel.unpark()
            await tel.set_tracking(True)
            await tel.slew(ra_hours, dec_deg)
            return {"centered": True, "error_arcmin": 0.1}

        monkeypatch.setattr(hub, "goto_and_center", goto_and_center)

        async def autofocus(label, *, step=None, target=None):
            if not run.frozen.is_set():
                self.sweeps.append((run.clock.t, getattr(target, "name", "")))
            return True

        monkeypatch.setattr(engine, "_autofocus", autofocus)

    def of(self, name: str) -> list[float]:
        return [t for t, n in self.setups if n == name]

    def at(self, what: list, ra: float) -> list[float]:
        return [t for t, r in what if abs(r - ra) < 1e-9]

    def swept(self, name: str) -> list[float]:
        return [t for t, n in self.sweeps if n == name]


def _started(lines, label: str) -> int:
    return sum(m == label for _l, m, _s in lines)


# ------------------------------------------------------------ the cloud hold

async def test_a_hold_opened_by_a_setup_returns_to_that_setup(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#241's reproduction. Alpha has one 30 s frame, Bravo is ready at once,
    the sky is cloudy from 1 s and clears at 200 s, and no safety monitor is
    assigned, so the frames' verdict stands in for one. Bravo's setup's
    pre-slew gate opens a hold for Bravo; the hold points the mount at Bravo
    and, once the sky has cleared, releases. The setup it interrupted then
    acquires Bravo, once: `_setup_target` entered once for Bravo, two slews to
    Bravo (the hold's re-point and the setup's own), one centring and one
    focus sweep after the release, and "target 2/2: Bravo" logged once.

    RED before the ruling (observed) -
        AssertionError: Bravo was set up 2 times, at [30.0, 480.0] s; slews
        to Bravo at [30.0, 480.0, 480.0] s, centrings at [480.0, 480.0] s,
        sweeps at [480.0, 480.0] s, 'target 2/2: Bravo' logged 2 times
    Mutant "the release re-enters `_setup_target`" (the
    ``_acquisition_behind_gate is target`` test in `_hold_for_clear`'s
    release made False): RED, identically (observed).
    """
    focuser = sim_hub.devices.get("focuser")
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=EXP + 600.0,
                 clears_at_s=200.0)
    sim_hub.devices["focuser"] = focuser    # Bravo's setup sweeps
    got = _Acquisitions(w.run, monkeypatch)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1, acquire=False)
    b = _target("Bravo", _ra_at(-2.0, w.t0), 60.0)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + EXP)) < 1.0, (
            "premise: Bravo's setup opened the hold as Alpha's frame ended")
        released = w.released_at()
        assert released is not None, "premise: the sky cleared and it released"
        setups = got.of("Bravo")
        slews = got.at(got.slews, b.ra_hours)
        centrings = got.at(got.centrings, b.ra_hours)
        sweeps = got.swept("Bravo")
        started = _started(bus_lines, "target 2/2: Bravo")
        assert (len(setups), len(slews), len(centrings), len(sweeps),
                started) == (1, 2, 1, 1, 1), (
            f"Bravo was set up {len(setups)} times, at "
            f"{w.rel(setups, w.t0)} s; slews to Bravo at "
            f"{w.rel(slews, w.t0)} s, centrings at "
            f"{w.rel(centrings, w.t0)} s, sweeps at {w.rel(sweeps, w.t0)} s, "
            f"'target 2/2: Bravo' logged {started} times")
        assert abs(slews[0] - t_h) < 0.5, "premise: the first is the re-point"
        assert centrings[0] >= released - 0.5 and sweeps[0] >= released - 0.5, (
            f"the centring and the sweep came before the release at "
            f"{released - w.t0:.1f} s")
        assert abs(setups[0] - t_h) < 0.5, (
            "the one setup is not the one the hold interrupted")
    finally:
        await w.close()


async def test_control_a_hold_opened_by_the_frame_loop_reacquires_once(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. A hold opened by the frame loop's gate, on Alpha, which its
    own setup pointed the mount at: no setup is waiting behind that gate, so
    the release runs Alpha's setup itself, once, with its centring and sweep.

    Mutant "the release never re-enters `_setup_target`" (the release's
    ``await self._setup_target(...)`` deleted, so every release returns
    without an acquisition): RED (observed) -
        AssertionError: the release of a frame-loop hold re-acquired Alpha 0
        times: setups after the hold opened at [] s, centrings [] s
    """
    focuser = sim_hub.devices.get("focuser")
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=EXP + 600.0,
                 clears_at_s=200.0)
    sim_hub.devices["focuser"] = focuser
    got = _Acquisitions(w.run, monkeypatch)
    a = _target("Alpha", _ra_at(-2.0, w.t0), 60.0)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        assert got.of("Alpha")[:1] == [w.t0], "premise: Alpha's own setup"
        assert w.released_at() is not None, "premise: it released"
        after = [t for t in got.of("Alpha") if t > t_h]
        centred = [t for t in got.at(got.centrings, a.ra_hours) if t > t_h]
        assert len(after) == 1 and len(centred) == 1 and \
            len([t for t in got.swept("Alpha") if t > t_h]) == 1, (
                f"the release of a frame-loop hold re-acquired Alpha "
                f"{len(after)} times: setups after the hold opened at "
                f"{w.rel(after, t_h)} s, centrings {w.rel(centred, t_h)} s")
    finally:
        await w.close()


# ------------------------------------------------ the safety pause and the roof

class _Monitor:
    """The simulator's safety monitor, its reading a script on the fake
    clock: unsafe from ``unsafe_from`` to ``safe_at`` (fake seconds from the
    start of the night)."""

    def __init__(self, run: _Clocked, monkeypatch, *, unsafe_from: float,
                 safe_at: float):
        self.run = run
        self.unsafe = (run.t0 + unsafe_from, run.t0 + safe_at)

        async def safety_reading():
            t = run.clock.t
            if self.unsafe[0] <= t < self.unsafe[1]:
                return SafetyReading(is_safe=False, reason="rain sensor",
                                     source="script", ts=t)
            return SafetyReading(is_safe=True, source="script", ts=t)

        monkeypatch.setattr(run.hub, "safety_reading", safety_reading)


def _pause_night(hub, store, monkeypatch, *, horizon_s: float, **safety):
    store.set_safety(SafetyConfig(enabled=True, on_unsafe="pause",
                                  unsafe_consecutive=1,
                                  resume_safe_consecutive=1, max_pause_min=0,
                                  sky_fallback_hold=False, **safety))
    run = _Clocked(hub, monkeypatch, horizon_s=horizon_s)
    return run, _Acquisitions(run, monkeypatch)


def _safety_plan(*targets: Target) -> SequencePlan:
    return SequencePlan(name="pause", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        safety_check=True, targets=list(targets))


async def test_a_pause_opened_by_a_setup_returns_to_that_setup(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The safety pause, the same shape. Alpha has one 30 s frame; the
    monitor reads unsafe from 1 s to 100 s, so Bravo's setup's pre-slew gate
    opens the open-sky pause, which stops tracking and waits. When the
    monitor reads safe again the pause releases, and the setup it
    interrupted acquires Bravo, once.

    RED before the ruling (observed) -
        AssertionError: Bravo was set up 2 times, at [30.0, 100.0] s;
        centrings at [100.0, 100.0] s, sweeps at [100.0, 100.0] s, 'target
        2/2: Bravo' logged 2 times
    Mutant "the pause release re-enters `_setup_target`" (the
    ``_acquisition_behind_gate is target`` test in `_park_hold_pause` made
    False): RED, identically (observed).
    """
    run, got = _pause_night(sim_hub, temp_store, monkeypatch,
                            horizon_s=EXP + 300.0)
    _Monitor(run, monkeypatch, unsafe_from=1.0, safe_at=100.0)
    a = _target("Alpha", _ra_at(-3.0, run.t0), 40.0, count=1, acquire=False)
    b = _target("Bravo", _ra_at(-2.0, run.t0), 60.0)
    try:
        await run.night(_safety_plan(a, b))
        msgs = [m for _l, m, _s in bus_lines]
        assert any(m.startswith("UNSAFE: rain sensor") for m in msgs), (
            "premise: the pause opened")
        assert [t for t in run.tracking_off if abs(t - (run.t0 + EXP)) < 0.5], (
            "premise: Bravo's gate paused and stopped tracking as Alpha's "
            "frame ended")
        setups = got.of("Bravo")
        centrings = got.at(got.centrings, b.ra_hours)
        sweeps = got.swept("Bravo")
        started = _started(bus_lines, "target 2/2: Bravo")
        assert (len(setups), len(centrings), len(sweeps), started) == (
            1, 1, 1, 1), (
            f"Bravo was set up {len(setups)} times, at "
            f"{run.rel(setups, run.t0)} s; centrings at "
            f"{run.rel(centrings, run.t0)} s, sweeps at "
            f"{run.rel(sweeps, run.t0)} s, 'target 2/2: Bravo' logged "
            f"{started} times")
        assert centrings[0] >= run.t0 + 100.0 - 0.5, (
            "premise: the centring followed the pause")
    finally:
        await run.close()


async def test_control_a_pause_opened_by_the_frame_loop_reacquires_once(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The pause opened by the frame loop's gate, on Alpha between
    two frames: nothing waits behind that gate, so the release re-acquires
    Alpha itself, once.

    Mutant "the pause release never re-enters `_setup_target`" (the
    release's ``await self._setup_target(ti, target)`` deleted): RED
    (observed) -
        AssertionError: the release of a frame-loop pause re-acquired Alpha
        0 times: setups at [0.0] s
    """
    run, got = _pause_night(sim_hub, temp_store, monkeypatch,
                            horizon_s=EXP + 300.0)
    _Monitor(run, monkeypatch, unsafe_from=1.0, safe_at=100.0)
    a = _target("Alpha", _ra_at(-2.0, run.t0), 60.0)
    try:
        await run.night(_safety_plan(a))
        setups = got.of("Alpha")
        assert len(setups) == 2 and setups[0] == run.t0, (
            f"the release of a frame-loop pause re-acquired Alpha "
            f"{len(setups) - 1} times: setups at {run.rel(setups, run.t0)} s")
        assert len(got.at(got.centrings, a.ra_hours)) == 2
    finally:
        await run.close()


async def test_a_roof_reopen_opened_by_a_setup_returns_to_that_setup(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The roof, the same shape. With the roof closing on unsafe and
    reopening when safe, Bravo's setup's pre-slew gate parks the mount and
    closes the roof over it; when the monitor reads safe again the roof
    reopens, and the setup it interrupted unparks and acquires Bravo, once.

    RED before the ruling (observed) -
        AssertionError: Bravo was set up 2 times, at [30.0, 100.0] s;
        centrings at [100.0, 100.0] s, 'target 2/2: Bravo' logged 2 times
    Mutant "the roof release re-enters `_setup_target`" (the
    ``_acquisition_behind_gate is target`` test in `_await_safe_and_reopen`
    made False): RED, identically (observed).
    """
    dome = sim_hub.devices.get("dome")
    assert dome is not None and dome.connected, "premise: the sim has a roof"
    run, got = _pause_night(sim_hub, temp_store, monkeypatch,
                            horizon_s=EXP + 300.0,
                            close_dome_on_unsafe=True,
                            reopen_dome_when_safe=True)
    _Monitor(run, monkeypatch, unsafe_from=1.0, safe_at=100.0)
    a = _target("Alpha", _ra_at(-3.0, run.t0), 40.0, count=1, acquire=False)
    b = _target("Bravo", _ra_at(-2.0, run.t0), 60.0)
    try:
        await run.night(_safety_plan(a, b))
        msgs = [m for _l, m, _s in bus_lines]
        assert any("reopening roof" in m for m in msgs), (
            "premise: the roof closed and reopened")
        setups = got.of("Bravo")
        centrings = got.at(got.centrings, b.ra_hours)
        started = _started(bus_lines, "target 2/2: Bravo")
        assert (len(setups), len(centrings), started) == (1, 1, 1), (
            f"Bravo was set up {len(setups)} times, at "
            f"{run.rel(setups, run.t0)} s; centrings at "
            f"{run.rel(centrings, run.t0)} s, 'target 2/2: Bravo' logged "
            f"{started} times")
        assert run.tracking(), "premise: the one setup left Bravo tracked"
    finally:
        await run.close()


# ------------------------------------------- a pause opened by a hold's re-point

async def test_a_pause_opened_by_a_repoints_gate_returns_to_the_repoint(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The re-point asks the whole slew gate, monitor half included (the
    decision H3 orchestrator ruling 3 left open): a slew under cloud is
    allowed, and rain and wind are the monitor's, whose verdict gates every
    slew. So a pause can open inside a hold's re-point. It returns to the
    re-point when it releases, which then slews and tracks; it runs no setup
    of its own, because whatever acquires the target after the hold (the
    hold's release, or the setup it interrupted) is still to come.

    Asked of the method on the simulator's mount, with the monitor reading
    unsafe once and safe after.

    Mutant "the re-point's gate without the flag" (the
    ``self._acquisition_behind_gate = target`` before `_hold_repoint`'s
    ``_safety_gate`` call deleted): RED (observed) -
        AssertionError: the pause inside the re-point ran a setup of its own:
        ['Bravo']
    """
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.01)
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", 5.0, 20.0, acquire=False)
    bravo = _target("Bravo", 7.0, 40.0, acquire=False)
    e.plan = SequencePlan(name="r", meridian_flip=False, safety_check=True,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(
        enabled=True, on_unsafe="pause", unsafe_consecutive=1,
        resume_safe_consecutive=1, max_pause_min=0))
    e._tracked_target = alpha
    reads: list[bool] = []

    async def safety_reading():
        reads.append(True)
        return SafetyReading(is_safe=len(reads) > 1, reason="rain sensor",
                             source="script")

    monkeypatch.setattr(sim_hub, "safety_reading", safety_reading)
    setups: list[str] = []

    async def setup(ti, target):
        setups.append(target.name)

    monkeypatch.setattr(e, "_setup_target", setup)
    why = await e._hold_repoint(bravo, elsewhere=True)
    assert any(m.startswith("UNSAFE: rain sensor") for _l, m, _s in bus_lines), (
        "premise: the re-point's gate opened a pause")
    assert setups == [], (
        f"the pause inside the re-point ran a setup of its own: {setups}")
    tel = sim_hub.devices["telescope"]
    assert why is None and e._tracked_target is bravo and tel.rig.tracking, (
        f"the re-point did not go on to point the mount at Bravo after the "
        f"pause: {why!r}")
    assert e._acquisition_behind_gate is None, (
        "the flag outlived the gate it was set around")


async def test_the_flag_is_cleared_when_the_gate_raises(sim_hub, temp_store,
                                                        monkeypatch):
    """The flag is set and cleared around the setup's gate IN A FINALLY. A
    gate that raises (a refusal that ends the run, say) must not leave it
    set, or the next release anywhere, a frame-loop hold's included, would
    believe a setup is waiting behind it and skip its acquisition.

    Mutant "clear the flag only on the gate's normal return" (the finally
    around `_setup_target`'s gate made a plain statement after it): RED
    (observed) -
        AssertionError: the flag is still set after the setup's gate raised:
        'Bravo'
    """
    e = SequenceEngine(sim_hub)
    bravo = _target("Bravo", 7.0, 40.0, acquire=False)
    e.plan = SequencePlan(name="r", meridian_flip=False, targets=[bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))

    async def refusing_gate(*, context, target=None):
        assert e._acquisition_behind_gate is target, (
            "premise: the flag is set while the gate runs")
        raise engine_mod.SafetyAbort("refused")

    monkeypatch.setattr(e, "_safety_gate", refusing_gate)
    # RAISES, AND IS SEEN TO: a plain try/except here passed as well when the
    # setup never reached the gate, which leaves the flag unset for nothing.
    with pytest.raises(engine_mod.SafetyAbort, match="^refused$"):
        await e._setup_target(0, bravo)
    left = getattr(e._acquisition_behind_gate, "name", None)
    assert left is None, (
        f"the flag is still set after the setup's gate raised: {left!r}")
