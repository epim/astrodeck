# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A refused re-point keeps holding; a dead mount link still ends the run
(#240, H3 orchestrator ruling 3).

H2 orchestrator ruling 2 (spec 5.8, owner list item 5) says a cloud hold
opened for target B while the mount is on another target slews to B through
the slew gate and the Sun check, and that when the slew gate would refuse it
stops tracking and says why. H2 pre-asked only the gate's altitude half
(`_hold_repoint_refusal`). The gate's pier guard (a destination pier side
different from the current one, with flips off) and the Sun cone refused the
slew with the SafetyAbort every slew raises, and that ended the run at the
hold's open.

THE RULING. Inside a cloud hold, any slew-gate refusal of a re-point stops
tracking once (`_hold_park`), says why in words only, and keeps holding; every
later look asks again, and it never ends the run. A mount-link timeout (a
`_bounded` expiry on is_parked, unpark, slew or set_tracking) still ends the
run under P0-2. The two are told apart by exception type (`SlewRefused`, a
SafetyAbort subclass raised by `_enforce_mount_floor` and by the re-point's
Sun check), never by message text.

THE HARNESS is test_cloud_hold_watch's `_Watched` (test_idle_park_hold's
clocked simulator underneath): the real scheduler, frame loop and hold on the
fake clock, the sky a script, no safety monitor, so the frames' own verdict
stands in for one. The site is a fixture, and nothing here prints a mount's
altitude or azimuth, or the Sun's.

THE CONTROLS the ruling names are held where they already were:
test_cloud_hold_follows_the_mount.py's
`test_a_refused_repoint_stops_the_last_target_once_and_says_why` (the
altitude refusal, pre-asked, stops once and says why) and
`test_a_hold_opened_for_the_next_target_points_the_mount_there_first` (a
successful re-point). Both pass unchanged with this ruling in.
"""
from __future__ import annotations

import asyncio
import re

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.devices.base import DeviceError, PierSide, SafetyReading
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan

from test_cloud_hold_watch import EXP, WATCH, _Watched, _plan, _target
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _ra_at, sim_hub, temp_store)

CHECKING = "Checking at the science exposure"


def _numbered(s: str) -> str | None:
    """``s`` with its digits masked, if it carries any, else None. Words
    only is the rule for everything the hold publishes or logs (#19,
    #140), so a failure shows the shape, never the number."""
    return re.sub(r"\d", "#", s) if re.search(r"[\d°]", s) else None


def _two_targets(w: _Watched):
    """Alpha east of the meridian (the tube on the west side), one frame;
    Bravo an hour and a half west of it (the tube on the east side), ready
    at once. The mount starts on Alpha's side, so Alpha's own setup is no
    pier change and the only one is the move to Bravo."""
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    b = _target("Bravo", _ra_at(+1.5, w.t0), 40.0)
    w.tel.rig.ra_hours, w.tel.rig.dec_deg = a.ra_hours, a.dec_deg
    return a, b


def _still_holding(w: _Watched) -> str:
    """The premise every refusal case shares, asked as a sentence: the run
    reached the horizon, still holding, or how it ended."""
    if w.run.frozen.is_set() and w.engine.state.get("end_reason") is None:
        return ""
    return (f"the run ended at fake "
            f"+{w.run.clock.t - w.t0:.0f}s: "
            f"{w.engine.state.get('end_reason')!r}, "
            f"{w.engine.state.get('detail')!r}")


async def test_a_pier_guard_refusal_stops_tracking_and_the_hold_goes_on(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Alpha takes its one frame and completes. Bravo is ready, but a slew to
    it would change the pier side, with ``enforce_pier_limits`` on and the
    plan's flips off, so the slew gate's pier guard refuses it. Bravo's setup
    opens a hold for Bravo under the closed sky. The hold stops tracking once,
    at once, publishes that the mount is stopped and why in words, and goes
    on holding: the run is not ended, and every later look asks the pier
    guard again.

    RED before the ruling (observed) -
        AssertionError: the pier guard's refusal of the re-point ended the
        run instead of stopping tracking: the run ended at fake +30s:
        'unsafe', 'slew to Bravo would require a pier flip but meridian flip
        is disabled'
    Mutant "`_enforce_mount_floor` raises a plain SafetyAbort for the pier
    guard" (``SlewRefused`` made ``SafetyAbort`` there): RED, identically
    (observed).
    Mutant "the refusal said on every look" (``self._hold_repoint_said =
    True`` deleted from `_hold_repoint`'s refusal branch): RED (observed) -
        AssertionError: the refusal was said 18 times over 18 asks: ['the
        mount is not on Bravo, and a slew to Bravo would need a pier flip,
        and this plan has meridian flips switched off - stopping tracking;
        the cloud hold goes on but judges no sky until the mount can track
        the target again', 'Bravo: a slew to Bravo would need a pier flip,
        and this plan has meridian flips switched off - the mount stays
        stopped; each look asks again, and this is said once', 'Bravo: a
        slew to Bravo would need a pier flip, and this plan has meridian
        flips switched off - the mount stays stopped; each look asks again,
        and this is said once']
    The pier guard's own sentence carries no number, so the words check
    here cannot see the gate's sentence published in place of the words;
    the Sun case below, whose sentence does, is the one that does.
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=EXP + 400.0,
                 safety={"enforce_pier_limits": True})
    a, b = _two_targets(w)
    asks: list[tuple[float, str, str]] = []
    real_dest = w.tel.destination_pier_side

    async def destination_pier_side(ra, dec):
        side = await real_dest(ra, dec)
        if abs(ra - b.ra_hours) < 1e-9 and not w.run.frozen.is_set():
            here = await w.tel.pier_side()
            asks.append((w.run.clock.t, side.value, here.value))
        return side

    monkeypatch.setattr(w.tel, "destination_pier_side", destination_pier_side)
    try:
        await w.night(_plan(a, b, flip=False), to_the_horizon=False)
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + EXP)) < 1.0, (
            "premise: Bravo's setup opened the hold as Alpha's frame ended")
        assert asks and asks[0][1] != asks[0][2] and asks[0][1] in (
            PierSide.EAST.value, PierSide.WEST.value), (
            f"premise: a slew to Bravo changes the pier side: {asks[:1]}")
        ended = _still_holding(w)
        assert not ended, (
            f"the pier guard's refusal of the re-point ended the run instead "
            f"of stopping tracking: {ended}")
        stops = w.stops(t_h)
        assert stops[:1] and abs(stops[0] - t_h) < 0.5 and len(stops) == 1, (
            f"the mount was not stopped once, at the hold's open: stops at "
            f"{w.rel(stops, t_h)} s")
        assert not [t for t in w.run.slews if t >= t_h], "the hold slewed"
        later = [t for t, _d, _h in asks if t >= t_h + 3 * WATCH]
        assert len(asks) >= 4 and later, (
            f"the pier guard was not asked again by later looks: asked at "
            f"{w.rel([t for t, _d, _h in asks], t_h)} s")
        said = [d for t, _s, _h, d in w.states if t >= t_h]
        stopped = [d for d in said if "the mount is stopped" in d]
        assert stopped and "not on Bravo" in stopped[0] and \
            "pier flip" in stopped[0], said[:3]
        assert not [d for d in said if CHECKING in d], said[:3]
        assert not [t for t, _tr in w.probes if t >= t_h], (
            "a check was taken on the stopped mount")
        msgs = [m for _l, m, _s in bus_lines]
        refused = [m for m in msgs if "pier flip" in m]
        assert len(refused) == 1 and "not on Bravo" in refused[0], (
            f"the refusal was said {len(refused)} times over {len(asks)} "
            f"asks: {refused[:3]}")
        numbered = [n for n in map(_numbered, (stopped[0], refused[0])) if n]
        assert not numbered, (
            f"the published refusal carries numbers (masked): {numbered}")
    finally:
        await w.close()


async def test_a_sun_cone_refusal_stops_tracking_and_the_hold_goes_on(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The same night with Bravo inside the Sun's exclusion cone (the hub's
    cone check scripted to refuse Bravo, and only Bravo, with the hub's own
    sentence, which carries a separation in degrees). The hold stops
    tracking once, says why in words, goes on holding, and asks the cone
    again on later looks.

    RED before the ruling (observed) -
        AssertionError: the Sun cone's refusal of the re-point ended the run
        instead of stopping tracking: the run ended at fake +30s: 'unsafe',
        'slew blocked by sun-exclusion cone: target is within 12 deg of the
        Sun (exclusion 30 deg) - enable a solar session
        (config.solar_override) to override'
    Mutant "the re-point's Sun check raises a plain SafetyAbort"
    (``SlewRefused`` made ``SafetyAbort`` in `_hold_repoint`'s Sun check):
    RED, identically (observed).
    Mutant "the hold says the gate's sentence" (``e.words`` made ``e`` in
    `_hold_repoint`'s `_hold_park` call): RED (observed) -
        AssertionError: the published refusal carries numbers (masked):
        ['held for cloud - the mount is stopped: the mount is not on Bravo,
        and slew blocked by sun-exclusion cone: target is within ## deg of
        the Sun (exclusion ## deg) - enable a solar session
        (config.solar_override) to override. The sky is not judged until it
        tracks again', 'the mount is not on Bravo, and slew blocked by
        sun-exclusion cone: target is within ## deg of the Sun (exclusion ##
        deg) - enable a solar session (config.solar_override) to override -
        stopping tracking; the cloud hold goes on but judges no sky until
        the mount can track the target again']
    Mutant "the refusal said on every look": RED here too (observed) -
        AssertionError: the refusal was said 18 times over 18 asks: ["the
        mount is not on Bravo, and Bravo is inside the Sun's exclusion cone
        - stopping tracking; the cloud hold goes on but judges no sky until
        the mount can track the target again", "Bravo: Bravo is inside the
        Sun's exclusion cone - the mount stays stopped; each look asks
        again, and this is said once", "Bravo: Bravo is inside the Sun's
        exclusion cone - the mount stays stopped; each look asks again, and
        this is said once"]
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=EXP + 400.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    b = _target("Bravo", _ra_at(-2.0, w.t0), 60.0)
    asked: list[float] = []

    def check_solar(self, ra, dec, *, force=False):
        if abs(ra - b.ra_hours) < 1e-9 and abs(dec - b.dec_deg) < 1e-9:
            if not w.run.frozen.is_set():
                asked.append(w.run.clock.t)
            raise DeviceError(
                "target is within 12 deg of the Sun (exclusion 30 deg) - "
                "enable a solar session (config.solar_override) to override")

    monkeypatch.setattr(Hub, "_check_solar", check_solar)
    try:
        await w.night(_plan(a, b), to_the_horizon=False)
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + EXP)) < 1.0, "premise: Bravo's hold"
        ended = _still_holding(w)
        assert not ended, (
            f"the Sun cone's refusal of the re-point ended the run instead of "
            f"stopping tracking: {ended}")
        stops = w.stops(t_h)
        assert stops[:1] and abs(stops[0] - t_h) < 0.5 and len(stops) == 1, (
            f"the mount was not stopped once, at the hold's open: stops at "
            f"{w.rel(stops, t_h)} s")
        assert not [t for t in w.run.slews if t >= t_h], "the hold slewed"
        later = [t for t in asked if t >= t_h + 3 * WATCH]
        assert len(asked) >= 4 and later, (
            f"the Sun cone was not asked again by later looks: asked at "
            f"{w.rel(asked, t_h)} s")
        said = [d for t, _s, _h, d in w.states if t >= t_h]
        stopped = [d for d in said if "the mount is stopped" in d]
        assert stopped and "not on Bravo" in stopped[0] and \
            "Sun" in stopped[0], said[:3]
        assert not [d for d in said if CHECKING in d], said[:3]
        msgs = [m for _l, m, _s in bus_lines]
        refused = [m for m in msgs if "exclusion cone" in m
                   or "Sun" in m]
        assert len(refused) == 1 and "not on Bravo" in refused[0], (
            f"the refusal was said {len(refused)} times over {len(asked)} "
            f"asks: {refused[:3]}")
        numbered = [n for n in map(_numbered, (stopped[0], refused[0])) if n]
        assert not numbered, (
            f"the published refusal carries numbers (masked): {numbered}")
    finally:
        await w.close()


async def test_a_dead_link_during_the_repoint_still_ends_the_run(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """P0-2 is not a refusal. Bravo's hold points the mount at Bravo, and the
    slew never answers: it hangs past ``SLEW_TIMEOUT_S`` (cut to a third of a
    real second here, since the bound is a real ``wait_for``), and the
    timeout's SafetyAbort ends the run, as a dead link does everywhere else in
    the engine. Before the ruling this was already so; the case pins it, so a
    refusal catch widened to every SafetyAbort cannot quietly turn a dead
    mount link into a hold that holds until its 45 minute bound.

    Mutant "`_hold_repoint` catches the timeout's SafetyAbort as a failed
    slew" (its ``except SafetyAbort: raise`` deleted, so the bound's abort
    falls to ``except Exception``): RED (observed) -
        AssertionError: the dead link was caught as a failed slew and the
        hold went on: the run was still holding at the horizon, fake +450s;
        lines ['Bravo: could not point the mount at the target (slew to
        Bravo timed out after 0s) - still stopped; each look asks again, and
        this is said once']
    """
    monkeypatch.setattr(engine_mod, "SLEW_TIMEOUT_S", 0.3)
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=EXP + 400.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    b = _target("Bravo", _ra_at(-2.0, w.t0), 60.0)
    hung: list[float] = []
    inner_slew = w.tel.slew

    async def slew(ra_hours, dec_deg):
        if abs(ra_hours - b.ra_hours) < 1e-9 and not w.run.frozen.is_set():
            hung.append(w.run.clock.t)
            await asyncio.Event().wait()        # never answers
        return await inner_slew(ra_hours, dec_deg)

    monkeypatch.setattr(w.tel, "slew", slew)
    try:
        await w.night(_plan(a, b), to_the_horizon=False)
        t_h = w.hold_started()
        assert hung and abs(hung[0] - t_h) < 0.5, (
            f"premise: the hold's re-point sent the slew at its open: "
            f"{w.rel(hung, t_h)}")
        msgs = [m for _l, m, _s in bus_lines]
        assert not w.run.frozen.is_set(), (
            f"the dead link was caught as a failed slew and the hold went on: "
            f"the run was still holding at the horizon, fake "
            f"+{w.run.clock.t - w.t0:.0f}s; lines "
            f"{[m for m in msgs if 'could not point the mount' in m][:2]}")
        detail = w.engine.state.get("detail") or ""
        assert w.engine.state.get("end_reason") == "unsafe" and \
            "slew to Bravo timed out" in detail, (
                f"the run ended, but not on the slew's bound: "
                f"{w.engine.state.get('end_reason')!r}, {detail!r}")
    finally:
        await w.close()


async def test_a_monitor_abort_in_the_repoints_gate_still_ends_the_run(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Only a `SlewRefused` keeps holding. The re-point asks the whole slew
    gate, monitor half included, and the monitor half can raise a plain
    SafetyAbort of its own: rain with ``on_unsafe`` set to abort, or a pause
    past ``max_pause_min``. That still ends the run, as it does from any other
    slew, and the mount is not moved: `_hold_repoint`'s docstring claims so,
    and the dead-link case above holds only the mount-call half of it.

    Asked of the method on the simulator's mount, the monitor reading rain.

    Added by the T7 verifier: with the gate's catch widened to every
    SafetyAbort the whole owned suite stayed green (68 passed).
    Mutant "the re-point's gate catch widened to every SafetyAbort"
    (``except SlewRefused as e`` made ``except SafetyAbort as e``, its words
    taken from the message when it has none): RED (observed) -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.SafetyAbort'>
    """
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.01)
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", 5.0, 20.0)
    bravo = _target("Bravo", 7.0, 40.0)
    e.plan = SequencePlan(name="r", meridian_flip=False, safety_check=True,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(
        enabled=True, on_unsafe="abort_park_warm", unsafe_consecutive=1))
    e._tracked_target = alpha
    assert "safety" in sim_hub.devices, "premise: the sim has a monitor"

    async def safety_reading():
        return SafetyReading(is_safe=False, reason="rain sensor",
                             source="script")

    monkeypatch.setattr(sim_hub, "safety_reading", safety_reading)
    tel = sim_hub.devices["telescope"]
    slews: list[float] = []
    inner_slew = tel.slew

    async def slew(ra_hours, dec_deg):
        slews.append(ra_hours)
        return await inner_slew(ra_hours, dec_deg)

    monkeypatch.setattr(tel, "slew", slew)
    with pytest.raises(engine_mod.SafetyAbort) as ended:
        await e._hold_repoint(bravo, elsewhere=True)
    assert not isinstance(ended.value, engine_mod.SlewRefused), (
        "premise: the monitor's abort is not a limit refusal")
    assert "rain sensor" in str(ended.value), (
        f"the run ended, but not on the monitor's abort: {ended.value}")
    assert slews == [] and e._tracked_target is alpha, (
        f"the mount was pointed at Bravo through a monitor abort: {slews}")
    assert e._acquisition_behind_gate is None, (
        "the flag outlived the gate that raised")


# ------------------------------------------------ told apart by type, not text

async def test_a_limit_refusal_is_a_slew_refused_and_a_timeout_is_not(
        sim_hub, temp_store, monkeypatch):
    """The two are told apart by exception type. The slew gate's pier guard
    and its altitude half raise `SlewRefused`, a SafetyAbort (so every caller
    that does not name it still ends the run, exactly as before), carrying a
    words-only ``words`` for the hold. Since #233 (H3 T11) the message, which
    a run it ends logs and publishes, is words as well, and the gate's
    altitude, floor and azimuth ride ``site_detail`` alone (the premise
    below; test_engine_logs_carry_no_site_numbers.py grades it). A
    `_bounded` expiry raises a plain SafetyAbort, never a `SlewRefused`,
    whatever its text.

    Mutant "the altitude half raises a plain SafetyAbort" (``SlewRefused``
    made ``SafetyAbort`` at the end of `_enforce_mount_floor`): RED
    (observed) -
        AssertionError: the floor refusal is a SafetyAbort, not a
        SlewRefused
    Mutant "`_enforce_mount_floor` raises a plain SafetyAbort for the pier
    guard": RED (observed) -
        AssertionError: the pier refusal is a SafetyAbort, not a SlewRefused
    Mutant "a floor refusal reads as no site" (the ``kind == "floor"``
    branch of `_limit_words` deleted): RED (observed) -
        AssertionError: no observing site is saved, so no slew can be
        checked against the mount's limits
    Mutant "restore the gate's altitude and azimuth" (#233, T11: the floor
    and ceiling sentences of `_altitude_limit_verdict` put back to the
    numeric ones): RED (observed) -
        AssertionError: premise: the message is words (#233): 'target Bravo
        altitude -##° below safety floor ##° (az #°)'
    Mutant "site_detail dropped" (`_enforce_mount_floor` passes
    ``site_detail=None``): RED (observed) -
        AssertionError: premise: the gate's own sentence rides site_detail
    """
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(name="r", meridian_flip=False, targets=[])
    tel = sim_hub.devices["telescope"]
    now = engine_mod.time.time()
    low = _target("Bravo", _ra_at(12.0, now), 0.0)
    e._cfg = AppConfig(safety=SafetyConfig(enabled=True, min_alt_deg=30.0))
    with pytest.raises(engine_mod.SafetyAbort) as floor:
        await e._enforce_mount_floor(projected=True, target=low)
    assert isinstance(floor.value, engine_mod.SlewRefused), (
        f"the floor refusal is a {type(floor.value).__name__}, not a "
        f"SlewRefused")
    assert _numbered(str(floor.value)) is None and \
        "altitude floor" in str(floor.value), (
            f"premise: the message is words (#233): "
            f"{_numbered(str(floor.value))!r}")
    assert "safety floor" in (floor.value.site_detail or ""), (
        "premise: the gate's own sentence rides site_detail")
    assert _numbered(floor.value.words) is None and \
        "altitude floor" in floor.value.words, floor.value.words

    tel.rig.ra_hours = _ra_at(-3.0, now)
    across = _target("Charlie", _ra_at(+1.5, now), 40.0)
    e._cfg = AppConfig(safety=SafetyConfig(enabled=True,
                                           enforce_pier_limits=True))
    with pytest.raises(engine_mod.SafetyAbort) as pier:
        await e._enforce_mount_floor(projected=True, target=across)
    assert isinstance(pier.value, engine_mod.SlewRefused), (
        f"the pier refusal is a {type(pier.value).__name__}, not a "
        f"SlewRefused")
    assert "pier flip" in pier.value.words and \
        _numbered(pier.value.words) is None, pier.value.words

    async def never():
        await asyncio.Event().wait()

    with pytest.raises(engine_mod.SafetyAbort) as timeout:
        await engine_mod._bounded(never(), 0.01, "slew to Bravo")
    assert not isinstance(timeout.value, engine_mod.SlewRefused), (
        "a mount call past its bound reads as a limit refusal")
