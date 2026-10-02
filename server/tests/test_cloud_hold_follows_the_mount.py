# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A cloud hold follows the mount it has (#224, #221, #228).

The hold of #203 and #205 watches the target it holds: its floor, its keep-out
and its flip point. That is right only while the mount is on that target, and a
hold does not always have that.

* #224. A hold opened by the next target's pre-slew safety gate holds Bravo
  while the mount is still tracking Alpha, the target the run has finished
  with. Every look watched Bravo, so nothing watched Alpha, which sank through
  the mount's floor 200 s into the hold with the mount tracking it.
* #225 (fixed in H1) was the same shape on a stopped mount, and its fix only
  covered a mount the hold found stopped.
* #221. A hold opened from a scheduler wait had no target at all. It watched
  nothing, and on a stopped mount every check was a streak read as cloud, so
  it could only end at its 45 minute abort, whatever the sky did.
* #228. When the re-point was refused or failed, the hold published its
  opening detail ("Checking at the science exposure ...") over a stopped
  mount that judged no sky.

THE RULINGS: H2 orchestrator rulings 2 and 1 (spec, Still waiting on the
owner, items 5 and 6; spec 5.8). The orchestrator made them so that the
second hardening round could be built, and each is binding until the owner
overturns it; they are not the owner's rulings 1 and 2 of 2026-09-24, which
the spec's Revision 2 records on other subjects. A hold whose mount is not on
its target points the mount there
before it judges the sky, behind the slew gate, the Sun check and the
projection the slew gate raises on; when it may not, it stops the mount once,
says why, and points it on a later look once it may. It never reads the last
target's flip latch. A scheduler wait under a cloudy sky opens no hold: it says
so once and publishes it, and the next target's setup opens a hold with a
target. Flips switched off, the hold takes no action at the flip point.

THE HARNESS is test_cloud_hold_watch's `_Watched` (test_idle_park_hold's clocked
simulator underneath): the real scheduler, frame loop and hold on the fake
clock, the sky a script. The site is a fixture, and nothing here prints a
mount's altitude or azimuth: every geometric premise is asked as a yes or no.
"""
from __future__ import annotations

import re
import time

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.catalog import altaz
from astrodeck.config import AppConfig, SafetyConfig, Site
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target

from test_cloud_hold_watch import (
    EXP, LEAD_S, MAX_HOLD_S, WATCH, _Watched, _crosses, _crossing_ra,
    _flip_ra, _nearer, _plan, _target)
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    LAT, LON, _constraint_waiter, _hhmm, _ra_at, sim_hub, temp_store)

CHECKING = "Checking at the science exposure"


# ---------------------------------------------------------------- the spies

def _spy_looks(w: _Watched, monkeypatch, a: Target, b: Target) -> list:
    """(fake time, tracking, the mount nearer ``a`` than ``b``, the flip
    latch) at every look a hold takes, read before the look acts."""
    looks: list[tuple[float, bool, bool, bool]] = []
    real = w.engine._hold_watch

    async def look(target, *, ahead_s):
        if not w.run.frozen.is_set():
            rig = w.tel.rig
            looks.append((w.run.clock.t, bool(rig.tracking),
                          _nearer(rig.ra_hours, rig.dec_deg, a, b),
                          bool(w.engine._flip_armed)))
        return await real(target, ahead_s=ahead_s)

    monkeypatch.setattr(w.engine, "_hold_watch", look)
    return looks


def _spy_checks(w: _Watched, monkeypatch, a: Target, b: Target) -> list:
    """(fake time, tracking, nearer ``a`` than ``b``) of every unsaved check
    frame, read as the shutter opens."""
    checks: list[tuple[float, bool, bool]] = []
    inner = w.hub.capture

    async def capture(exposure_s, *args, **kw):
        if kw.get("save") is False and not w.run.frozen.is_set():
            rig = w.tel.rig
            checks.append((w.run.clock.t, bool(rig.tracking),
                           _nearer(rig.ra_hours, rig.dec_deg, a, b)))
        return await inner(exposure_s, *args, **kw)

    monkeypatch.setattr(w.hub, "capture", capture)
    return checks


def _set_aside_at_the_floor(w: _Watched, t_h0: float) -> tuple[Target, float]:
    """Alpha, held from ``t_h0``, sinks through the mount's 30 degree floor
    250 s into its hold, so the look at +240 s sets it aside with tracking
    stopped (test_cloud_hold_watch (c)). Returns Alpha and the crossing."""
    t_cross = w.t0 + t_h0 + 250.0
    ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(ra, 20.0, 30.0, t_cross, rising=False), "premise"
    return _target("Alpha", ra, 20.0), t_cross


def _aside(w: _Watched) -> float:
    aside = [t for t, _st, _h, d in w.states
             if d == "cloud hold ended: Alpha was set aside"]
    assert aside, "premise: the first hold ended with Alpha set aside"
    return aside[0]


# ------------------------------------------------ 1. the re-point at the open

async def test_a_hold_opened_for_the_next_target_points_the_mount_there_first(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#224's reproduction. Alpha takes its one frame and completes; Bravo is
    ready at once, so its setup's pre-slew gate opens a hold for Bravo with
    the mount still tracking Alpha. Alpha then sinks through the mount's 30
    degree floor 200 s into that hold. The hold points the mount at Bravo
    before its first check, through the slew gate, so by then the mount is
    Bravo's and tracking, and nothing tracks Alpha through its floor.

    Mutant "watch B while tracking A" (the re-point block at the hold's
    open, in `_hold_for_clear`, deleted): RED (observed) -
        AssertionError: the hold watched Bravo while the mount tracked Alpha
        through its floor: tracking Alpha at the looks at [210.0, 240.0,
        270.0, 300.0] s, and Alpha crossed the floor at 200.0 s
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 810.0,
                 safety={"min_alt_deg": 30.0})
    t_cross = w.t0 + t_h0 + 200.0
    ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(ra, 20.0, 30.0, t_cross, rising=False), "premise"
    a = _target("Alpha", ra, 20.0, count=1)
    b = _target("Bravo", _ra_at(-3.0, w.t0), 40.0)
    clear = all(altaz(b.ra_hours, b.dec_deg, LAT, LON, t)[0] > 35.0
                for t in (w.t0, w.t0 + t_h0 + 810.0))
    assert clear, "premise: Bravo is well clear of the floor all along"
    looks = _spy_looks(w, monkeypatch, a, b)
    checks = _spy_checks(w, monkeypatch, a, b)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + t_h0)) < 1.0, (
            "premise: Bravo's setup opened the hold as Alpha's one frame ended")
        assert w.engine.state.get("target") == "Bravo", "premise: Bravo's hold"
        on_alpha = [t for t, tracking, near_a, _f in looks
                    if tracking and near_a and t >= t_cross]
        assert not on_alpha, (
            f"the hold watched Bravo while the mount tracked Alpha through "
            f"its floor: tracking Alpha at the looks at "
            f"{w.rel(on_alpha[:4], t_h)} s, and Alpha crossed the floor at "
            f"{t_cross - t_h:.1f} s")
        assert checks, "premise: the hold took a check"
        t1, tracking, near_a = checks[0]
        assert tracking and not near_a, (
            f"the first check, at {t1 - t_h:.1f} s, was taken with the mount "
            f"{'tracking' if tracking else 'stopped'} and nearer "
            f"{'Alpha' if near_a else 'Bravo'}")
        slews = [t for t in w.run.slews if t >= t_h]
        assert slews and slews[0] < t1, (
            f"the mount was not pointed at Bravo before the first check: "
            f"slews at {w.rel(slews, t_h)} s")
        assert w.engine._tracked_target is b
    finally:
        await w.close()


async def test_control_a_hold_whose_mount_is_on_its_target_does_not_slew(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. A hold opened by the frame loop holds the target the mount
    is already tracking (its own setup pointed it there), so there is nothing
    to point: no slew, and the checks come at the old cadence.

    Mutant "re-point whatever the mount is on" (the open's
    ``self._tracked_target is not target`` made True): RED (observed) -
        AssertionError: a hold on the target the mount was tracking slewed
        it: slews at [0.0] s
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 400.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        assert w.engine._tracked_target is a, "premise: the mount is Alpha's"
        slews = [t for t in w.run.slews if t >= t_h]
        assert not slews, (
            f"a hold on the target the mount was tracking slewed it: slews at "
            f"{w.rel(slews, t_h)} s")
        msgs = [m for _l, m, _s in bus_lines]
        assert not [m for m in msgs if "now pointed at this one" in m], msgs
        assert w.rel([t for t, _tr in w.probes if t >= t_h][:2], t_h) == [
            120.0, 270.0], "the checks no longer come at the old cadence"
    finally:
        await w.close()


# ------------------------------------------- 2. a refused or failed re-point

async def test_a_refused_repoint_stops_the_last_target_once_and_says_why(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Alpha takes its one frame, with its flip latch armed (it is east of
    the meridian), and completes. Bravo is ready, but rising into the 70
    degree keep-out 100 s after its hold opens: the slew gate, projecting a
    slew three minutes ahead, would raise the SafetyAbort that ends the run.
    So the hold does not slew. It stops the mount, which was still tracking
    Alpha, once and at once, through `_hold_park`, publishes that the mount
    is stopped and why, and says so once, though every look after is refused
    too. While the mount is not on Bravo, Alpha's flip latch is not Bravo's,
    and it is disarmed.

    Mutant "keep tracking A when B is refused" (the `_hold_park` after a
    refused re-point at the open deleted): RED, the mount tracks Alpha,
    unwatched, until a look finds Bravo's own keep-out inside its interval
    and stops it for that (observed) -
        AssertionError: the mount was left tracking Alpha after Bravo's
        re-point was refused: stops at [90.0] s, expected one at 0.0 s
    Mutant "no disarm at the open" (the ``self._flip_armed = False`` at the
    open deleted; asked of the latch itself, because while the mount is
    elsewhere no look reads it, and the re-point re-arms it): RED
    (observed) -
        AssertionError: Alpha's flip latch was still armed at the looks at
        [0.0, 30.0, 60.0] s, with the mount not on Bravo
    Mutant "remove the once-only guard" (in `_hold_park`) is RED here too,
    every refused look stopping the mount again (observed) -
        AssertionError: stopped more than once: [0.0, 90.0, 120.0, 120.0,
        150.0, 180.0, 210.0, 240.0, 240.0, 270.0, 300.0, 330.0, 360.0,
        360.0, 390.0]
    """
    t_h0 = EXP
    t_in_s = t_h0 + 100.0
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_in_s + 300.0,
                 safety={"max_alt_deg": 70.0})
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    t_in = w.t0 + t_in_s
    b_ra = _crossing_ra(70.0, 45.0, t_in, rising=True)
    assert _crosses(b_ra, 45.0, 70.0, t_in, rising=True), "premise"
    b = _target("Bravo", b_ra, 45.0)
    looks = _spy_looks(w, monkeypatch, a, b)
    armed_at_open: list[bool] = []
    real_hold = w.engine._hold_for_clear

    async def hold(reason, target):
        armed_at_open.append(bool(w.engine._flip_armed))
        return await real_hold(reason, target)

    monkeypatch.setattr(w.engine, "_hold_for_clear", hold)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        assert armed_at_open[:1] == [True], (
            "premise: Alpha's flip latch was armed when Bravo's hold opened")
        stops = w.stops(t_h)
        assert stops[:1] and abs(stops[0] - t_h) < 0.5, (
            f"the mount was left tracking Alpha after Bravo's re-point was "
            f"refused: stops at {w.rel(stops, t_h)} s, expected one at 0.0 s")
        assert len(stops) == 1, f"stopped more than once: {w.rel(stops, t_h)}"
        assert not [t for t, on, _w in w.run.tracking_calls
                    if on and t >= t_h], "tracking was turned back on"
        assert not [t for t in w.run.slews if t >= t_h], "the hold slewed"
        assert not [t for t, _tr in w.probes if t >= t_h], (
            f"a check was taken on the stopped mount: {w.probes}")
        armed = [t for t, _tr, _a, latch in looks if latch]
        assert len(looks) >= 3 and not armed, (
            f"Alpha's flip latch was still armed at the looks at "
            f"{w.rel(armed[:3], t_h)} s, with the mount not on Bravo")
        said = [d for t, _s, _h, d in w.states if t >= t_h]
        stopped = [d for d in said if "the mount is stopped" in d]
        assert stopped and "not on Bravo" in stopped[0] and \
            "zenith keep-out" in stopped[0], said[:3]
        assert not [d for d in said if CHECKING in d], said[:3]
        msgs = [m for _l, m, _s in bus_lines]
        refused = [m for m in msgs if "not on Bravo" in m]
        assert len(refused) == 1, (
            f"the refusal was said {len(refused)} times over "
            f"{len(looks)} looks: {refused[:3]}")
        # WORDS ONLY, where the refusal is published: the detail is
        # view.status and the line reaches the /api/logs ring. The slew gate's
        # own sentence says "zenith keep-out" too, and so passes every check
        # above, but it carries the target's altitude and azimuth, which with
        # a time is the site (#19, #140). Shown only with its digits masked.
        # See test_a_refused_repoint_says_which_limit_in_words_and_no_numbers
        # for the mutant this was added against.
        numbered = [re.sub(r"\d", "#", s) for s in (stopped[0], refused[0])
                    if re.search(r"[\d°]", s)]
        assert not numbered, (
            f"the published refusal carries numbers (masked): {numbered}")
    finally:
        await w.close()


async def test_a_refused_repoint_points_the_mount_once_a_look_allows_it(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Bravo is ready while inside the 70 degree keep-out, on its way down
    out of it 100 s after its hold opens. The re-point is refused at the
    open, so the mount (tracking Alpha) is stopped; the first look after
    Bravo leaves the keep-out finds the slew allowed and points the mount at
    Bravo, and only then does the hold judge the sky, from Bravo.

    Mutant "no re-point from a later look" (the ``elsewhere`` re-point at
    the top of `_hold_watch` deleted): RED (observed) -
        AssertionError: the hold never pointed the mount at Bravo after it
        left the keep-out at 100.0 s: slews at [] s
    """
    t_h0 = EXP
    t_out_s = t_h0 + 100.0
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_out_s + 400.0,
                 safety={"max_alt_deg": 70.0})
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    t_out = w.t0 + t_out_s
    b_ra = _crossing_ra(70.0, 45.0, t_out, rising=False)
    assert _crosses(b_ra, 45.0, 70.0, t_out, rising=False), "premise"
    b = _target("Bravo", b_ra, 45.0)
    checks = _spy_checks(w, monkeypatch, a, b)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        stops = w.stops(t_h)
        assert stops[:1] and abs(stops[0] - t_h) < 0.5, (
            f"premise: the refused re-point stopped the mount: "
            f"{w.rel(stops, t_h)}")
        slews = [t for t in w.run.slews if t >= t_h]
        assert slews and t_out <= slews[0] <= t_out + WATCH + 0.5, (
            f"the hold never pointed the mount at Bravo after it left the "
            f"keep-out at {t_out - t_h:.1f} s: slews at {w.rel(slews, t_h)} s")
        on = [t for t, on, _w in w.run.tracking_calls if on and t >= slews[0]]
        assert on, "premise: the re-point tracks"
        assert checks and all(tr and not near_a for _t, tr, near_a in checks), (
            f"a check was taken off Bravo or on a stopped mount: {checks}")
        assert checks[0][0] >= slews[0]
        assert w.engine._tracked_target is b
        msgs = [m for _l, m, _s in bus_lines]
        assert not [m for m in msgs if "could not point the mount" in m], msgs
    finally:
        await w.close()


async def test_the_refusal_counts_the_targets_own_floor(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Bravo's own schedule sets it aside below 30 degrees (``on_floor =
    advance``), and it sinks through that floor 100 s after its hold opens.
    No mount floor is configured, so the slew gate would allow the slew, but
    it would be a slew to a target about to be set aside. The hold refuses
    it on Bravo's own floor, over the slew's three minutes, and the first
    look after the crossing sets Bravo aside. The mount never moves.

    Mutant "the refusal ignores the target's own floor" (the own-floor
    check in `_hold_repoint_refusal` deleted): RED (observed) -
        AssertionError: the hold slewed to Bravo, which sank below its own
        floor 100.0 s into its hold: slews at [0.0] s
    """
    t_h0 = EXP
    t_cross_s = t_h0 + 100.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_cross_s + 200.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    t_cross = w.t0 + t_cross_s
    b_ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(b_ra, 20.0, 30.0, t_cross, rising=False), "premise"
    b = _target("Bravo", b_ra, 20.0, min_altitude_deg=30.0,
                on_floor="advance")
    c = _constraint_waiter("Charlie", w.t0)
    try:
        await w.night(_plan(a, b, c))
        t_h = w.hold_started()
        assert w.engine.state.get("hold") is None, "premise: the hold ended"
        msgs = [m for _l, m, _s in bus_lines]
        assert any("Bravo" in m and "setting it aside for the rest of this "
                   "run" in m for m in msgs), (
            f"premise: Bravo was set aside at its own floor: {msgs[-6:]}")
        slews = [t for t in w.run.slews if t >= t_h]
        assert not slews, (
            f"the hold slewed to Bravo, which sank below its own floor "
            f"{t_cross - t_h:.1f} s into its hold: slews at "
            f"{w.rel(slews, t_h)} s")
        assert any("not on Bravo" in m and "own altitude floor" in m
                   for m in msgs), msgs[-6:]
    finally:
        await w.close()


# ------------------------------------------------- 3. a parked mount at open

async def test_a_parked_mount_is_unparked_and_pointed_at_the_held_target(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """DECIDED: a hold that finds the mount parked, with nothing acquired
    (the run's first target, the sky already shut), unparks it, bounded, as
    `_setup_target`'s uncentred branch does, and points it at the target:
    the hold then judges the sky and releases when it clears. A mount that
    will not come off park leaves the hold stopped and says so once, never
    once a look.

    Mutant "no unpark in the re-point" (the ``is_parked`` / ``unpark`` lines
    in `_hold_repoint` deleted): RED, the slew is refused by the parked
    mount on every look, and the hold never judges the sky (observed) -
        AssertionError: the hold never released: the mount was never taken
        off park, checks at [] s, the sky clear from 200.0 s
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=640.0,
                 clears_at_s=200.0, closes_at_s=-1.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    w.tel.rig.parked = True
    # THE PREMISE IS SCRIPTED. On a fresh run the frames have no verdict yet,
    # so a real first gate cannot read cloud; the script puts the rig in the
    # shape H2 orchestrator ruling 2 names anyway. The check frames borrow
    # the last step the engine shot, which `start` does not reset (the engine
    # outlives its runs), so it is set here as an earlier run would have left
    # it.
    w.engine._hold_step = a.steps[0]
    unparked: list[float] = []
    real_unpark = w.tel.unpark

    async def unpark():
        unparked.append(w.run.clock.t)
        return await real_unpark()

    monkeypatch.setattr(w.tel, "unpark", unpark)
    checks = _spy_checks(w, monkeypatch, a, a)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        assert abs(t_h - w.t0) < 1.0, (
            "premise: the first target's setup opened the hold")
        released = w.released_at()
        judged = [t for t, _tr, _n in checks if t >= t_h]
        assert released is not None, (
            f"the hold never released: the mount was "
            f"{'never taken' if not unparked else 'taken'} off park, checks "
            f"at {w.rel(judged, t_h)} s, the sky clear from "
            f"{w.clears_at - t_h:.1f} s")
        assert unparked and unparked[0] <= judged[0], (
            "premise: unparked before the first check")
        assert all(tr for _t, tr, _n in checks), f"a streak was judged: {checks}"
        msgs = [m for _l, m, _s in bus_lines]
        assert not [m for m in msgs if "could not point the mount" in m], msgs
    finally:
        await w.close()


async def test_control_a_mount_that_will_not_unpark_is_said_once(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL for the unpark: a mount that refuses to come off park. The
    hold stays stopped, takes no check, publishes that the mount is stopped,
    and says the failed re-point ONCE, though every look asks again.

    Mutant "warn on every failed re-point" (the once-only latch around the
    warning in `_hold_repoint` removed): RED (observed) -
        AssertionError: the failed re-point was said 18 times: ['Alpha:
        could not point the mount at the target (unpark refused) - still
        stopped; each look asks again, and this is said once', 'Alpha: could
        not point the mount at the target (unpark refused) - still stopped;
        each look asks again, and this is said once']
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=400.0,
                 closes_at_s=-1.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    w.tel.rig.parked = True
    w.engine._hold_step = a.steps[0]        # as in the case above
    tries: list[float] = []

    async def unpark():
        tries.append(w.run.clock.t)
        raise RuntimeError("unpark refused")

    monkeypatch.setattr(w.tel, "unpark", unpark)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        assert len(tries) >= 8, f"premise: asked again on each look: {tries}"
        msgs = [m for _l, m, _s in bus_lines]
        said = [m for m in msgs if "could not point the mount" in m]
        assert len(said) == 1, (
            f"the failed re-point was said {len(said)} times: {said[:2]}")
        assert not [t for t, _tr in w.probes if t >= t_h], "a check was taken"
        details = [d for t, _s, _h, d in w.states if t >= t_h]
        assert details and "the mount is stopped" in details[-1], details[-2:]
        assert not [d for d in details if CHECKING in d], details[:3]
    finally:
        await w.close()


# ---------------------------------------------------------- 4. the flip latch

async def test_the_hold_never_flips_the_next_target_on_the_last_ones_latch(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Alpha was acquired east of the meridian, so its flip latch is armed
    when it completes. Bravo is an hour and a half west of its meridian: a
    mount pointed there owes no flip, and Bravo's own latch would not arm.
    Bravo's hold points the mount at Bravo and arms Bravo's latch through
    `_arm_meridian_flip`, so no flip is attempted for Bravo during the hold.

    Mutant "hold reads A's latch" (the disarm at the open and the
    `_arm_meridian_flip` after an elsewhere re-point both deleted): RED, the
    first look runs the flip gate on Alpha's armed latch for a target long
    past its meridian (observed) -
        AssertionError: the hold flipped Bravo on Alpha's latch: meridian
        flips at [0.0, 30.0] s
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 300.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    b = _target("Bravo", _ra_at(+1.5, w.t0), 40.0)
    flips: list[float] = []
    real_flip = w.hub.meridian_flip

    async def meridian_flip(ra, dec, *args, **kw):
        flips.append(w.run.clock.t)
        return await real_flip(ra, dec, *args, **kw)

    monkeypatch.setattr(w.hub, "meridian_flip", meridian_flip)
    armed_at_open: list[bool] = []
    real_hold = w.engine._hold_for_clear

    async def hold(reason, target):
        armed_at_open.append(bool(w.engine._flip_armed))
        return await real_hold(reason, target)

    monkeypatch.setattr(w.engine, "_hold_for_clear", hold)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        assert armed_at_open[:1] == [True], (
            "premise: Alpha's latch was armed when Bravo's hold opened")
        assert w.engine._tracked_target is b, "premise: the mount is Bravo's"
        assert not flips, (
            f"the hold flipped Bravo on Alpha's latch: meridian flips at "
            f"{w.rel(flips, t_h)} s")
        assert w.engine._flip_armed is False
    finally:
        await w.close()


async def test_the_held_targets_own_flip_latch_is_armed_by_the_repoint(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The other half. Alpha was acquired west of the meridian, so no latch
    is armed. Bravo is east, 200 s from its flip point when its hold opens.
    The re-point arms Bravo's latch, so at the flip point the hold takes the
    owed flip the frame loop's way; the simulated AM5 cannot flip before the
    meridian, so the flip that could not be taken stops tracking there.
    Never tracked past its flip point.

    Mutant "no arm after the re-point" (the `_arm_meridian_flip` after an
    elsewhere re-point deleted; the disarm at the open kept): RED (observed) -
        AssertionError: Bravo was tracked past its flip point at 200.0 s:
        stops at [] s
    Mutant "hold reads A's latch" (the disarm and the arm both deleted) is
    RED here identically (observed): Alpha, acquired west, left no latch.
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 330.0)
    a = _target("Alpha", _ra_at(+2.0, w.t0), 40.0, count=1)
    t_flip = w.t0 + t_h0 + 200.0
    b = _target("Bravo", _flip_ra(t_flip), 20.0)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        assert w.engine._tracked_target is b, "premise: the mount is Bravo's"
        slews = [t for t in w.run.slews if t >= t_h]
        assert slews and slews[0] - t_h < 0.5, "premise: pointed at Bravo"
        stops = [t for t in w.stops(t_h) if t > slews[0]]
        assert stops and stops[0] <= t_flip + 10.0, (
            f"Bravo was tracked past its flip point at {t_flip - t_h:.1f} s: "
            f"stops at {w.rel(stops, t_h)} s")
        assert any("flip point and the flip cannot be taken" in m
                   for _l, m, _s in bus_lines)
    finally:
        await w.close()


# ------------------------------------------ 5. the mount is the target's now

async def test_a_keep_out_repoint_makes_the_mount_the_targets(
        sim_hub, monkeypatch):
    """Every successful re-point makes the mount the held target's, the
    keep-out re-point as much as the elsewhere one: `_tracked_target` is
    what the next idle spell watches if the hold ends. In a reachable state
    the keep-out re-point already holds that target (a hold stopped for the
    keep-out was tracking it), so no whole night can see the assignment;
    it is pinned here, on the method, with another target tracked.

    Mutant "drop the assignment in the keep-out branch" (the assignment
    made in the ``elsewhere`` branch only, as before): RED (observed) -
        AssertionError: after pointing the mount back at Bravo the engine
        still says it is tracking Alpha
    """
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", 5.0, 20.0)
    bravo = _target("Bravo", 7.0, 40.0)
    e.plan = SequencePlan(name="r", meridian_flip=False,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha
    e._hold_parked = "ceiling"
    tel = sim_hub.devices["telescope"]
    await tel.set_tracking(False)

    await e._hold_repoint(bravo)

    assert tel.rig.tracking is True and e._hold_parked is None, "premise"
    assert e._tracked_target is bravo, (
        f"after pointing the mount back at Bravo the engine still says it "
        f"is tracking {getattr(e._tracked_target, 'name', None)}")


async def test_a_hold_that_repointed_and_then_ended_leaves_the_target_watched(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Alpha takes its one frame. Bravo waits for its hour-angle window,
    under cloud, long enough for the idle park-hold to stop Alpha, which
    closes the idle latch for that spell. Bravo's window opens, its setup's
    gate opens a hold, and the hold points the mount at Bravo. Bravo's own
    window then closes (``max_run_min``) inside the hold, which ends it with
    Bravo set aside and the mount still tracking Bravo: the stop boundary is
    not a look, and does not stop tracking. The next wait must watch Bravo,
    and so the re-point re-opens the idle latch, as a setup does; the wait
    stops tracking Bravo on the idle clock.

    Mutant "elsewhere re-point leaves the idle latch closed" (the
    ``_idle_hold_open = True`` in `_hold_repoint`'s elsewhere branch
    deleted): RED (observed) -
        AssertionError: the hold ended with the mount tracking Bravo and the
        wait that followed never stopped it: tracking calls after the hold
        ended [] s
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=900.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    b = _constraint_waiter("Bravo", w.t0, ready_after_s=300.0)
    b.schedule.max_run_min = 10
    c = _constraint_waiter("Charlie", w.t0)
    try:
        await w.night(_plan(a, b, c))
        idle_stops = [t for t in w.stops(w.t0) if t < w.t0 + 300.0]
        assert idle_stops, "premise: the idle park-hold stopped Alpha"
        t_h = w.hold_started()
        assert t_h >= w.t0 + 300.0 - 0.5, (
            f"premise: Bravo's hold, at {t_h - w.t0:.1f} s")
        slews = [t for t in w.run.slews if t >= t_h]
        assert slews and slews[0] - t_h < 0.5, "premise: pointed at Bravo"
        ended = [t for t, _st, _h, d in w.states
                 if d == "cloud hold ended: Bravo was set aside"]
        assert ended, "premise: Bravo's window closed inside its hold"
        during = [on for t, on, _w in w.run.tracking_calls
                  if slews[0] <= t < ended[0]]
        assert during and during[-1] is True, (
            "premise: the mount was tracking Bravo when the hold ended")
        after = [(round(t - ended[0], 1), on)
                 for t, on, _w in w.run.tracking_calls if t >= ended[0]]
        assert (0.0 <= after[0][0] <= engine_mod.SCHEDULE_WAIT_STEP_S + 0.5
                and after[0][1] is False) if after else False, (
            f"the hold ended with the mount tracking Bravo and the wait that "
            f"followed never stopped it: tracking calls after the hold ended "
            f"{after} s")
    finally:
        await w.close()


# ------------------------------------------------- 6. no target-less hold

async def test_a_cloudy_wait_opens_no_hold_and_the_night_goes_on(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#221's scenario. Alpha is held for cloud and sinks through the mount's
    30 degree floor 250 s into the hold, so it is set aside with tracking
    stopped. Bravo waits on its hour-angle window, which opens at 1500 s. The
    sky clears a minute after the set-aside. The wait opens no hold: a
    cloudy verdict there is said once for the spell and published in the
    state, the wait goes on, and when Bravo is ready it is set up and shot
    under the clear sky. Nothing reaches the hold's 45 minute bound.

    Mutant "target-less hold from the wait" (the ``target is None`` branch
    in `_no_safety_source` deleted, so the wait's gate opens
    ``_hold_for_clear(reason, None)`` as before): RED, the target-less hold
    judges streaks from the stopped mount and runs to its bound (observed) -
        AssertionError: premise: the run must still be going at the horizon;
        it ended at fake +2970s: 'unsafe', 'cloud hold exceeded 45 min -
        parking'
    Mutant "said on every look" (the once-per-spell latch in
    `_note_hold_deferred` removed): RED (observed) -
        AssertionError: the cloudy wait was said 12 times in one spell
    (The note's clearing when the sky no longer reads cloudy is held by the
    unit case in test_sky_stands_in_for_a_missing_monitor.py: here Bravo's
    setup clears it too, before the horizon, so this night cannot tell.)
    """
    t_h0 = EXP
    horizon_s = t_h0 + 240.0 + MAX_HOLD_S + 120.0
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=horizon_s,
                 clears_at_s=t_h0 + 240.0 + 60.0,
                 safety={"min_alt_deg": 30.0})
    a, _t_cross = _set_aside_at_the_floor(w, t_h0)
    b = _constraint_waiter("Bravo", w.t0, ready_after_s=1500.0)
    b.steps[0].count = 80
    try:
        await w.night(_plan(a, b))
        t_aside = _aside(w)
        assert not w.untargeted, (
            f"a check was taken with no target: {w.untargeted[:3]}")
        # The sky is clear again long before Bravo is ready, so no hold of any
        # kind has a reason to open after the set-aside.
        holds = [t for t, st, _h, _d in w.states
                 if st == "holding" and t > t_aside]
        assert not holds, (
            f"a hold opened with no target at {w.rel(holds[:1], t_aside)} s "
            f"after the set-aside")
        msgs = [m for _l, m, _s in bus_lines]
        said = [m for m in msgs if "no target set up to judge it from" in m]
        assert len(said) == 1, (
            f"the cloudy wait was said {len(said)} times in one spell")
        published = [t for t, d in w.deferred if d and t >= t_aside]
        assert published and published[0] - t_aside < 1.0, (
            "the cloudy wait was never published")
        assert w.deferred[-1][1] is None, (
            "the note outlived the cloud: still published at the horizon")
        shot = [t for n, t in w.run.exposure_starts
                if n == "Bravo" and t > t_aside]
        assert shot, "Bravo was never shot once its window opened"
    finally:
        await w.close()


async def test_a_new_wait_spell_says_it_again(sim_hub, monkeypatch, bus_lines):
    """Once per WAIT SPELL, not once per run: a target's setup ends the
    spell (and clears what the wait published), so the next wait under a
    closed sky says it again. Asked of the gate and the setup directly.

    Mutant "no reset at the setup" (the two lines at the top of
    `_setup_target` that end the spell deleted): RED (observed) -
        AssertionError: the setup did not end the wait's note
    Mutant "the setup keeps the spell's latch" (only its
    ``_hold_deferred_said = False`` deleted): RED (observed) -
        AssertionError: the second wait spell said nothing: ["the frames say
        the sky has closed in, with no target set up to judge it from - no
        cloud hold opens without one. The run goes on as it was (a wait
        keeps its idle park-hold on the mount), and the next target's setup
        opens the hold if the sky is still closed"]
    """
    sim_hub.mode = "native"
    sim_hub.devices.pop("safety", None)
    e = SequenceEngine(sim_hub)
    a = _target("Alpha", _ra_at(-3.0, time.time()), 40.0)
    e.plan = SequencePlan(name="w", guide=False, meridian_flip=False,
                          safety_check=True, targets=[a])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=True,
                                           sky_fallback_hold=True))
    e._clouds.cloudy = lambda _now: True           # type: ignore[assignment]
    e._clouds.describe = lambda _now: "cloudy"     # type: ignore[assignment]
    holds: list[str] = []

    async def hold(reason, target):
        holds.append(target.name if target is not None else "no target")

    monkeypatch.setattr(e, "_hold_for_clear", hold)
    await e._no_safety_source(None)
    await e._no_safety_source(None)
    assert e._hold_deferred, "premise: the first spell published the note"
    await e._setup_target(0, a)
    assert holds == ["Alpha"], f"premise: the setup's gate held Alpha: {holds}"
    assert e._hold_deferred is None, "the setup did not end the wait's note"
    await e._no_safety_source(None)
    said = [m for _l, m, _s in bus_lines
            if "no target set up to judge it from" in m]
    assert len(said) == 2, f"the second wait spell said nothing: {said}"


async def test_a_run_that_ends_in_a_cloudy_wait_takes_the_note_with_it(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The note says the next target's setup opens a hold, so it lives only
    as long as the scheduler that could set one up. Alpha shoots its one
    frame under a clear sky. Bravo sits below its 30 degree start gate,
    setting, and the sky closes at 60 s, so the wait publishes the note.
    Bravo's HH:MM stop closes its window inside ten minutes with nothing
    set up, and the run ends as a dawn cutoff with the sky still closed.
    Two day darks follow the park. The terminal state, and every publish
    after it, carries no note, and the darks say nothing about cloud.

    Before the fix (H2 T1 verifier): the terminal "complete" state went out
    with the note still saying "the next target's setup opens one", and it
    stayed published until the next start; day darks noted it afresh.

    Mutant "the note outlives the run" (the ``self._hold_deferred = None``
    in `_run`'s scheduler ``finally`` deleted): RED (observed) -
        AssertionError: the run ended with the wait's note still published:
        of the 3 publishes from the terminal state (0) on, these carry it:
        [(0, 'stopped at dawn (windows closed)'), (1, 'day darks - 1/2 at
        30s'), (2, 'day darks - 2/2 at 30s')]
    Mutant "day darks note a deferred hold" (``if not
    self._day_darks_gate:`` in `_no_safety_source` made ``if True:``): RED,
    the terminal state goes out clean and the first dark's gate publishes
    the note again, in a publish of its own that keeps the terminal detail
    (observed) -
        AssertionError: the run ended with the wait's note still published:
        of the 4 publishes from the terminal state (0) on, these carry it:
        [(1, 'stopped at dawn (windows closed)'), (2, 'day darks - 1/2 at
        30s'), (3, 'day darks - 2/2 at 30s')]
    """
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=900.0,
                 closes_at_s=2 * EXP)
    t0 = w.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0, count=1)
    b = _target("Bravo", _ra_at(+4.0, t0), 0.0, count=1,
                min_altitude_deg=30.0,
                start_mode="time", start_time=_hhmm(t0 - 2 * 3600),
                stop_mode="time", stop_time=_hhmm(t0 + 600))
    assert all(altaz(b.ra_hours, b.dec_deg, LAT, LON, t0 + s)[0] < 30.0
               for s in (0.0, 300.0, 600.0)), (
        "premise: Bravo stays under its start gate until its window closes")
    plan = _plan(a, b)
    plan.day_darks = 2
    monkeypatch.setattr(w.engine, "_hold_darks_shortfall",
                        lambda step, quota: (quota, 0))
    darks: list[float] = []

    async def run_calibration(ti, target):
        darks.append(w.run.clock.t)

    monkeypatch.setattr(w.engine, "_run_calibration", run_calibration)
    try:
        await w.night(plan, to_the_horizon=False)
        assert w.engine.state.get("end_reason") == "dawn_cutoff", (
            f"premise: the run ends at Bravo's closed window, not "
            f"{w.engine.state.get('end_reason')!r} "
            f"({w.engine.state.get('detail')!r})")
        assert not [n for n, _t in w.run.exposure_starts if n == "Bravo"], (
            "premise: Bravo is never set up")
        published = list(zip(w.states, w.deferred))
        ends = [i for i, ((_t, st, _h, _d), _n) in enumerate(published)
                if st == "complete"]
        assert ends, "premise: the terminal state was published"
        t_end = published[ends[0]][0][0]
        assert w.sky_cloudy(t_end), "premise: the sky is closed at the end"
        assert any(note for (t, _st, _h, _d), (_t, note) in published
                   if t < t_end), "premise: the wait published the note"
        assert len(darks) == 2 and all(w.sky_cloudy(t) for t in darks), (
            f"premise: two day darks under the closed sky, got "
            f"{w.rel(darks, t0)} s")
        # 0 is the terminal state; what follows is the wind-down's.
        after = published[ends[0]:]
        carry = [(i, d) for i, ((_t, _st, _h, d), (_t2, note))
                 in enumerate(after) if note]
        assert not carry, (
            f"the run ended with the wait's note still published: of the "
            f"{len(after)} publishes from the terminal state (0) on, these "
            f"carry it: {carry}")
        said = [m for _l, m, _s in bus_lines
                if "no target set up to judge it from" in m]
        assert len(said) == 1, (
            f"the wait's spell and the day darks said it {len(said)} times")
    finally:
        await w.close()


# ------------------------------------------------ 7. the calibration hold

async def test_a_calibration_hold_asks_the_mount_nothing_and_does_not_slew(
        sim_hub, monkeypatch):
    """A hold during a calibration block holds a target with no place on
    the sky. The mount is on another target (a light the run shot earlier),
    and none of that is the hold's business: it asks the mount nothing, and
    points it nowhere, from its open to its release.

    Mutant "the calibration guard dropped from the open re-point" (the
    ``not target.calibration`` in the open's elsewhere test deleted): RED,
    the hold slews to the calibration target's placeholder position
    (observed) -
        AssertionError: the calibration hold asked the mount ['is_parked',
        'slew', 'set_tracking']
    """
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", _ra_at(-3.0, time.time()), 40.0)
    darks = Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                   center=False, autofocus_first=False,
                   steps=[ExposureStep(exposure_s=1.0, count=5,
                                       frame_type="Dark")])
    e.plan = SequencePlan(name="c", meridian_flip=True,
                          targets=[alpha, darks])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha
    e._flip_armed = True
    e._hold_step = darks.steps[0]

    async def _none(*a, **k):
        return None

    async def _clear(*a, **k):
        return False

    for name in ("_checkpoint", "_safety_gate", "_frame_alerts_tick",
                 "_stand_down_guider", "_cooler_gate", "_restore_beam"):
        monkeypatch.setattr(e, name, _none)
    monkeypatch.setattr(e, "_enforce_stop_boundary", lambda *a, **k: None)
    monkeypatch.setattr(e, "_cloud_probe", _clear)
    monkeypatch.setattr(engine_mod, "CLOUD_PROBE_EVERY_S", 0.01)
    tel = sim_hub.devices["telescope"]
    asked: list[str] = []
    for name in ("slew", "set_tracking", "get_tracking", "pier_side",
                 "is_parked", "unpark", "time_to_meridian_flip",
                 "destination_pier_side"):
        real = getattr(tel, name)

        def spy(real=real, name=name):
            async def call(*a, **k):
                asked.append(name)
                return await real(*a, **k)
            return call

        monkeypatch.setattr(tel, name, spy())
    await e._hold_for_clear("clouds", darks)
    assert e._holding_for_clear is False, "premise: the hold released"
    assert asked == [], f"the calibration hold asked the mount {asked}"


# ----------------------------------------------------- #228: honest detail

async def test_a_stopped_mount_whose_slew_fails_never_claims_to_check_the_sky(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#228, the slew half (the projection half is test_cloud_hold_watch's
    keep-out control). Alpha is set aside at its floor with tracking
    stopped; Bravo's setup gate opens a hold with the mount stopped where
    Alpha was. The slew to Bravo is allowed, and fails, on every look. The
    mount stays stopped and no sky is judged, and the published detail says
    so, with why, for the whole spell: never "Checking at the science
    exposure". The failure is said once, not once a look.

    Mutant "restore the detail unconditionally" (the open's
    ``self._set_state(detail=detail)`` run whatever the re-point did): RED
    (observed) -
        AssertionError: the hold claimed to check the sky while the mount
        was stopped: 'held for cloud - no safety monitor is assigned and the
        frames say the sky has closed in. Checking at the science exposure,
        with up to 2 min between checks; parks after 45 min' at 240.0 s
    Mutant "warn on every failed re-point": RED (observed) -
        AssertionError: the failed re-point was said 20 times
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 700.0,
                 safety={"min_alt_deg": 30.0})
    a, _t_cross = _set_aside_at_the_floor(w, t_h0)
    b = _target("Bravo", _ra_at(-3.0, w.t0), 40.0)
    inner = w.tel.slew
    tries: list[float] = []

    async def slew(ra_hours, dec_deg):
        if _nearer(ra_hours, dec_deg, b, a):
            tries.append(w.run.clock.t)
            raise RuntimeError("slew refused by the mount")
        return await inner(ra_hours, dec_deg)

    monkeypatch.setattr(w.tel, "slew", slew)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        t_aside = _aside(w)
        assert len(tries) >= 10, f"premise: asked on each look: {tries}"
        said = [(t, d) for t, st, _h, d in w.states
                if t >= t_aside and st == "holding"]
        claims = [(t, d) for t, d in said if CHECKING in d]
        assert not claims, (
            f"the hold claimed to check the sky while the mount was stopped: "
            f"{claims[0][1]!r} at {claims[0][0] - t_h:.1f} s")
        assert said and "the mount is stopped" in said[-1][1] and \
            "not on Bravo" in said[-1][1], said[-2:]
        msgs = [m for _l, m, _s in bus_lines]
        failed = [m for m in msgs if "could not point the mount" in m]
        assert len(failed) == 1, (
            f"the failed re-point was said {len(failed)} times")
        assert not [t for t, _tr in w.probes if t >= t_aside], w.probes
        assert not [t for t, on, _w in w.run.tracking_calls
                    if on and t >= t_aside], "tracking was turned back on"
    finally:
        await w.close()


# ---------------------------------- H2 orchestrator ruling 1: flips off

async def test_a_flips_off_hold_takes_no_action_at_the_flip_point(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """H2 ORCHESTRATOR RULING 1: with the plan's flips switched off, the
    hold takes no action at the flip point at all. Alpha's flip point falls
    200 s into the hold, the sky clears 60 s after it, and the hold goes on
    judging the sky through the flip point and releases on two clear checks,
    with the mount never stopped. (A mount that stops at its own limit is
    the tracking probe's to find, as in the control below.)

    Mutant "park at the flip point regardless" (``not plan.meridian_flip
    or`` restored in `_hold_flip_watch`'s park test): RED, the look before
    the first check sees the flip point inside that check's span and stops
    the mount, which flips off can never un-stop, so no sky is judged until
    the bound (observed) -
        AssertionError: the flips-off hold stopped the mount at the flip
        point and never released: stops at [120.0] s, checks at [] s, the
        flip point at 200.0 s and the sky clear from 260.0 s
    CONTROLS, green under that mutant: the flips-on flip-point tests in
    test_cloud_hold_watch.py (observed for
    test_a_hold_that_starts_a_minute_before_the_flip_point_stops_there),
    and the flips-off mount that stops at its own limit, below.
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 700.0,
                 clears_at_s=t_h0 + 260.0)
    t_flip = w.t0 + t_h0 + 200.0
    a = _target("Alpha", _flip_ra(t_flip), 20.0)
    try:
        await w.night(_plan(a, flip=False))
        t_h = w.hold_started()
        released = w.released_at()
        stops = [t for t in w.stops(t_h) if released is None or t < released]
        checks = [t for t, _tr in w.probes if t >= t_h]
        assert released is not None and not stops, (
            f"the flips-off hold stopped the mount at the flip point and "
            f"never released: stops at {w.rel(stops, t_h)} s, checks at "
            f"{w.rel(checks, t_h)} s, the flip point at {t_flip - t_h:.1f} s "
            f"and the sky clear from {w.clears_at - t_h:.1f} s")
        past = [t for t in checks if t_flip <= t < released]
        assert len(past) >= engine_mod.CLOUD_RESUME_CLEAR_PROBES, (
            f"the checks stopped at the flip point: {w.rel(checks, t_h)}")
        assert w.engine.state.get("end_reason") != "unsafe"
    finally:
        await w.close()


async def test_control_a_flips_off_mount_that_stops_at_its_limit_is_resumed(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL for H2 orchestrator ruling 1. Flips off, the mount stops
    tracking on its own (as the AM5 does at its own limit) 310 s into the
    hold, far from the flip point. The tracking read before the next check
    finds it, judges no streak, and the frame loop's resume-then-recover
    path puts tracking back; the hold releases on clear checks from the
    tracking mount. Unchanged by that ruling: green with and without the
    mutant above (observed).

    Mutant "the hold skips the tracking probe" (the ``_tracking_now`` test
    before the check made False): RED, every check after the stop is a
    streak read as cloud (observed) -
        AssertionError: the flips-off hold never released after its mount
        stopped at 310.0 s: the run ended 'unsafe'; checks at [120.0, 270.0,
        420.0, 570.0] s, tracking [True, True, False, False]
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_h0 + MAX_HOLD_S + 60.0,
                 clears_at_s=t_h0 + 320.0,
                 stops_tracking_at_s=t_h0 + 310.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    b = _constraint_waiter("Bravo", w.t0)
    try:
        await w.night(_plan(a, b, flip=False), to_the_horizon=False)
        t_h = w.hold_started()
        released = w.released_at()
        assert w.stopped_at is not None, "premise: the mount stopped"
        assert released is not None, (
            f"the flips-off hold never released after its mount stopped at "
            f"{w.stopped_at - t_h:.1f} s: the run ended "
            f"{w.engine.state.get('end_reason')!r}; checks at "
            f"{w.rel([t for t, _tr in w.probes if t >= t_h][:4], t_h)} s, "
            f"tracking {[tr for t, tr in w.probes if t >= t_h][:4]}")
        msgs = [m for _l, m, _s in bus_lines]
        assert sum("has stopped tracking during the cloud hold" in m
                   for m in msgs) == 1, msgs
        assert any("trying to resume" in m for m in msgs), msgs
        assert all(tr for t, tr in w.probes if t >= t_h), w.probes
    finally:
        await w.close()


# ------------------------------- the guards the first round left unpinned

async def test_the_flip_point_of_a_target_the_mount_is_not_on_is_left_alone(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """While the mount is stopped elsewhere, the held target's flip point is
    not the hold's to act on: the pier side it would be read against is the
    last target's (`_hold_watch` skips `_hold_flip_watch` while
    ``_hold_parked`` is "elsewhere"). The disarmed latch alone does not cover
    it, because `_enforce_flip_owed` asks no latch.

    Bravo's hold opens with the mount on Alpha, and every slew to Bravo
    fails, so the mount stays stopped where Alpha was for the whole night.
    Bravo's flip point comes 200 s into the hold and its transit about ten
    minutes after that. (Not a keep-out refusal: a look that finds the
    target in the keep-out never reaches the flip watch at all, so only a
    failed slew leaves a hold stopped elsewhere with nothing else to do at
    the flip point.) Nothing is flipped, no flip is called owed, and no
    pre-flip side is filed for Bravo from Alpha's pier side.

    Mutant "flip watch while elsewhere" (the ``elif self._hold_parked !=
    "elsewhere"`` before `_hold_flip_watch` made a plain ``else``): RED, the
    flip point files Alpha's pier side as Bravo's pre-flip side, the transit
    finds the mount still on it, and the owed-flip hold re-arms the latch
    and tries Bravo's flip from Alpha's position on every look (observed) -
        AssertionError: the hold acted on Bravo's flip point with the mount
        on Alpha: flips at [810.0, 840.0, 870.0, 900.0, 930.0] s, owed
        ['Bravo: a meridian flip is owed and the mount is still on the west
        side -- refusing to expose. Holding up to 20 min for the flip.']
    """
    t_h0 = EXP
    t_flip_s = t_h0 + 200.0
    horizon_s = t_flip_s + LEAD_S + 150.0
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=horizon_s)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0, count=1)
    t_flip = w.t0 + t_flip_s
    b = _target("Bravo", _flip_ra(t_flip), 20.0)
    inner = w.tel.slew
    tries: list[float] = []

    async def slew(ra_hours, dec_deg):
        if _nearer(ra_hours, dec_deg, b, a):
            tries.append(w.run.clock.t)
            raise RuntimeError("slew refused by the mount")
        return await inner(ra_hours, dec_deg)

    monkeypatch.setattr(w.tel, "slew", slew)
    flips: list[float] = []
    real_flip = w.hub.meridian_flip

    async def meridian_flip(ra, dec, *args, **kw):
        flips.append(w.run.clock.t)
        return await real_flip(ra, dec, *args, **kw)

    monkeypatch.setattr(w.hub, "meridian_flip", meridian_flip)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        assert w.engine.state.get("target") == "Bravo", "premise: Bravo's hold"
        assert w.engine._hold_parked == "elsewhere", (
            "premise: the mount was still stopped elsewhere at the horizon")
        assert tries and max(tries) > t_flip + LEAD_S, (
            "premise: every look asked for Bravo, past its transit")
        msgs = [m for _l, m, _s in bus_lines]
        owed = [m for m in msgs if "a meridian flip is owed" in m]
        assert not flips and not owed, (
            f"the hold acted on Bravo's flip point with the mount on Alpha: "
            f"flips at {w.rel(flips, t_h)} s, owed {owed[:1]}")
        key = getattr(b, "id", None) or b.name
        assert key not in w.engine._pre_flip_side, (
            "Alpha's pier side was filed as Bravo's pre-flip side")
    finally:
        await w.close()


async def test_a_hold_on_a_rig_with_no_mount_judges_the_sky(
        sim_hub, monkeypatch):
    """A run with no mount has nothing to point, so a hold on a light target
    there is never "elsewhere" (the open's ``"telescope" in
    self.hub.devices``): it judges the sky and releases when it clears, as
    it always did.

    Mutant "no telescope guard" (that clause deleted from the open's
    ``elsewhere``): RED, `_tracked_target` is never set on a rig with no
    mount, so every hold is elsewhere, its re-point returns with nothing to
    move, and the hold stops a mount it does not have and judges no sky
    until its bound (observed) -
        AssertionError: a hold with no mount judged no sky and ran to its
        bound: SafetyAbort('cloud hold exceeded 0 min - parking'), checks 0
    """
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", _ra_at(-3.0, time.time()), 40.0)
    e.plan = SequencePlan(name="n", meridian_flip=True, targets=[alpha])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._hold_step = alpha.steps[0]
    tel = sim_hub.devices.pop("telescope")

    async def _none(*a, **k):
        return None

    for name in ("_checkpoint", "_safety_gate", "_frame_alerts_tick",
                 "_stand_down_guider", "_cooler_gate", "_restore_beam"):
        monkeypatch.setattr(e, name, _none)
    monkeypatch.setattr(e, "_enforce_stop_boundary", lambda *a, **k: None)
    checks: list[str] = []

    async def probe(target):
        checks.append(target.name)
        return False

    monkeypatch.setattr(e, "_cloud_probe", probe)
    setups: list[str] = []

    async def setup(ti, target):
        setups.append(target.name)

    monkeypatch.setattr(e, "_setup_target", setup)
    monkeypatch.setattr(engine_mod, "CLOUD_PROBE_EVERY_S", 0.01)
    # A bound of a few real seconds: long enough for a hold that judges the
    # sky to release, short enough that one which cannot fails the test
    # instead of hanging it.
    monkeypatch.setattr(engine_mod, "CLOUD_MAX_HOLD_MIN", 0.05)
    try:
        try:
            await e._hold_for_clear("clouds", alpha)
        except engine_mod.SafetyAbort as ex:
            raise AssertionError(
                f"a hold with no mount judged no sky and ran to its bound: "
                f"{ex!r}, checks {len(checks)}") from ex
        assert checks and setups == ["Alpha"], (checks, setups)
        assert e._holding_for_clear is False
    finally:
        sim_hub.devices["telescope"] = tel


async def test_a_failed_repoint_is_said_again_in_a_later_stopped_spell(
        sim_hub, monkeypatch, bus_lines):
    """Once per STOPPED SPELL, not once per hold (`_hold_repoint`'s
    ``_hold_repoint_said``). Bravo's re-point fails twice (said once), then
    succeeds, which ends the spell; a later keep-out stop opens a new one,
    and its failed re-point is said again. Asked of the method itself.

    Mutant "the latch outlives the spell" (the ``self._hold_repoint_said =
    False`` after a successful re-point deleted): RED (observed) -
        AssertionError: the failed re-point of the second stopped spell was
        not said: 1 line(s)
    """
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", 5.0, 20.0)
    bravo = _target("Bravo", 7.0, 40.0)
    e.plan = SequencePlan(name="r", meridian_flip=False,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha
    e._hold_parked = "elsewhere"
    tel = sim_hub.devices["telescope"]
    await tel.set_tracking(False)
    inner = tel.slew
    refuse = {"on": True}

    async def slew(ra_hours, dec_deg):
        if refuse["on"]:
            raise RuntimeError("slew refused by the mount")
        return await inner(ra_hours, dec_deg)

    monkeypatch.setattr(tel, "slew", slew)

    def said() -> int:
        return sum("could not point the mount" in m
                   for _l, m, _s in bus_lines)

    await e._hold_repoint(bravo, elsewhere=True)
    await e._hold_repoint(bravo, elsewhere=True)
    assert said() == 1, "premise: said once in the first spell"
    refuse["on"] = False
    await e._hold_repoint(bravo, elsewhere=True)
    assert e._hold_parked is None and e._tracked_target is bravo, (
        "premise: the re-point worked")
    # A later stop in the same hold: the keep-out, say.
    e._hold_parked = "ceiling"
    await tel.set_tracking(False)
    refuse["on"] = True
    await e._hold_repoint(bravo)
    assert said() == 2, (
        f"the failed re-point of the second stopped spell was not said: "
        f"{said()} line(s)")


async def test_a_new_hold_says_its_failed_repoint_though_the_last_one_did(
        sim_hub, monkeypatch, bus_lines):
    """The other end of the spell: a hold's open starts one. The last hold
    ended stopped, its failed re-point said (a set-aside, a stop boundary or
    the bound ends a hold without resetting that latch). Bravo's hold opens
    with the mount on Alpha, its slew fails on every look, and it is said
    once, for this hold, until the bound ends it.

    Mutant "the latch outlives the hold" (the ``self._hold_repoint_said =
    False`` at the hold's open deleted): RED (observed) -
        AssertionError: the new hold's failed re-point was said 0 times
    """
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", 5.0, 20.0)
    bravo = _target("Bravo", 7.0, 40.0)
    e.plan = SequencePlan(name="r", meridian_flip=False,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha
    e._hold_step = bravo.steps[0]
    e._hold_repoint_said = True             # as the last hold left it

    async def _none(*a, **k):
        return None

    for name in ("_checkpoint", "_safety_gate", "_frame_alerts_tick",
                 "_stand_down_guider", "_cooler_gate", "_restore_beam"):
        monkeypatch.setattr(e, name, _none)
    monkeypatch.setattr(e, "_enforce_stop_boundary", lambda *a, **k: None)
    tel = sim_hub.devices["telescope"]
    tries: list[int] = []

    async def slew(ra_hours, dec_deg):
        tries.append(1)
        raise RuntimeError("slew refused by the mount")

    monkeypatch.setattr(tel, "slew", slew)
    monkeypatch.setattr(engine_mod, "CLOUD_PROBE_EVERY_S", 0.01)
    # About a real second: many looks at this cadence, and the bound is what
    # ends a hold whose re-point never works.
    monkeypatch.setattr(engine_mod, "CLOUD_MAX_HOLD_MIN", 0.02)
    with pytest.raises(engine_mod.SafetyAbort, match="cloud hold exceeded"):
        await e._hold_for_clear("clouds", bravo)
    assert len(tries) >= 2, f"premise: asked again on a later look: {tries}"
    said = sum("could not point the mount" in m for _l, m, _s in bus_lines)
    assert said == 1, f"the new hold's failed re-point was said {said} times"


# ------------------------------------ 8. the refusal is words, never numbers

#: What `_hold_repoint_refusal` says for each rule that refuses a re-point:
#: the three verdicts of the slew gate's altitude half, and the target's own
#: floor. Each reaches the log line and the published detail through
#: `_hold_park` (the refused-re-point case above shows the ceiling one there).
REFUSALS = {
    "floor": ("Bravo would be below the mount's altitude floor by the end of "
              "a slew there"),
    "ceiling": ("Bravo would be in the mount's zenith keep-out by the end of "
                "a slew there"),
    "no_site": ("no observing site is saved, so no slew can be checked "
                "against the mount's limits"),
    "own floor": "Bravo is sinking below its own altitude floor",
}


@pytest.mark.parametrize("rule", list(REFUSALS))
async def test_a_refused_repoint_says_which_limit_in_words_and_no_numbers(
        sim_hub, temp_store, monkeypatch, rule):
    """`_hold_repoint_refusal` answers in words, one sentence per rule, never
    the slew gate's own sentence. The gate's sentence for a floor or a
    ceiling carries the target's altitude and azimuth, and its no-site
    sentence carries a latitude and a longitude. The refusal is published
    (view.status) and logged, and a known target's altitude at a known time
    is the site (#19, #140). The method's docstring promises WORDS ONLY; no
    case asked it before this one.

    Each rule is reached through the real verdicts on the fixture site
    (never the real one), asked as a yes or no: the gate's kind for the
    three mount-side rules, and no gate verdict at all for the target's own
    floor. No altitude is printed, even on failure: a sentence that carries
    a number is shown with its digits masked.

    Mutant "the refusal is the gate's sentence" (each of the three verdict
    branches returns ``verdict[1]``; since H3, whose `_limit_words` holds
    the three for the gate's `SlewRefused` too (#240), the one
    ``return self._limit_words(target, kind)`` made ``return verdict[1]``,
    observed again with the same four failures): RED, all three mount-side
    rules, and green for the own floor, which has no gate sentence to leak
    (observed) -
        [floor] AssertionError: the floor refusal carries numbers, and it
        is published (masked): 'target Bravo altitude -##° below safety
        floor ##° (az #°)'
        [ceiling] AssertionError: the ceiling refusal carries numbers, and
        it is published (masked): 'target Bravo altitude ##° above the
        zenith keep-out ##° (az ##°) — the mount can reach its own tripod
        up there'
        [no_site] AssertionError: the no_site refusal carries numbers, and
        it is published (masked): 'cannot check Bravo against the altitude
        limits: no observing site is saved, so every altitude here would be
        computed for latitude #, longitude #. Save the site in Settings, or
        clear the floor, horizon, no-go and ceiling limits if this mount
        genuinely has none.'
    With the ceiling branch alone mutated, the end-to-end refused-re-point
    case above goes RED as well, at its published refusal (observed) -
        AssertionError: the published refusal carries numbers (masked):
        ['held for cloud - the mount is stopped: the mount is not on Bravo,
        and target Bravo altitude ##° above the zenith keep-out ##° (az ##°)
        — the mount can reach its own tripod up there. The sky is not
        judged until it tracks again', 'the mount is not on Bravo, and
        target Bravo altitude ##° above the zenith keep-out ##° (az ##°) —
        the mount can reach its own tripod up there - stopping tracking;
        the cloud hold goes on but judges no sky until the mount can track
        the target again']
    Before this case and that check, the ceiling mutant passed all 42 tests
    in this file and test_cloud_hold_watch.py.

    SINCE #233 (H3 T11) THE GATE'S SENTENCE IS WORDS, so ``verdict[1]`` no
    longer leaks a number; the numbers are ``verdict.site_detail``. The same
    mutant run again (``return verdict[1]``): still RED for the three
    mount-side rules, the floor and the ceiling now on their words and the
    no-site sentence, whose 0s are no site, on its digits; and the
    end-to-end case above is green, having no number to catch (observed,
    both files: 3 failed, 43 passed) -
        [floor] AssertionError: the floor refusal names another rule:
        "slew refused: Bravo would be below the mount's altitude floor by
        the end of a slew there"
        [ceiling] AssertionError: the ceiling refusal names another rule:
        "slew refused: Bravo would be in the mount's zenith keep-out by the
        end of a slew there, where the mount can reach its own tripod"
        [no_site] AssertionError: the no_site refusal carries numbers, and
        it is published (masked): 'cannot check Bravo against the altitude
        limits: no observing site is saved, so every altitude here would be
        computed for latitude #, longitude #. Save the site in Settings, or
        clear the floor, horizon, no-go and ceiling limits if this mount
        genuinely has none.'
    Mutant "the refusal is the gate's numbers" (``return
    verdict.site_detail``): RED, the floor and ceiling on their numbers, the
    no-site rule because it has none and so reads as no refusal, and the
    end-to-end case at its published refusal (observed) -
        [floor] AssertionError: the floor refusal carries numbers, and it
        is published (masked): 'target Bravo altitude -##° below safety
        floor ##° (az #°)'
        [ceiling] AssertionError: the ceiling refusal carries numbers, and
        it is published (masked): 'target Bravo altitude ##° above the
        zenith keep-out ##° (az ##°) — the mount can reach its own tripod
        up there'
        [no_site] AssertionError: premise: no_site refuses the re-point
        AssertionError: the published refusal carries numbers (masked):
        ['held for cloud - the mount is stopped: the mount is not on Bravo,
        and target Bravo altitude ##° above the zenith keep-out ##° (az ##°)
        — the mount can reach its own tripod up there. The sky is not
        judged until it tracks again', 'the mount is not on Bravo, and
        target Bravo altitude ##° above the zenith keep-out ##° (az ##°) —
        the mount can reach its own tripod up there - stopping tracking;
        the cloud hold goes on but judges no sky until the mount can track
        the target again']

    Mutant "a floor refusal reads as no site" (the ``kind == "floor"``
    branch deleted, so the floor falls through to the no-site sentence; in
    `_limit_words` since H3, observed again with the same failure): RED, the
    floor only (observed) -
        AssertionError: the floor refusal names another rule: "no observing
        site is saved, so no slew can be checked against the mount's limits"
    It passed every test in this file, test_cloud_hold_watch.py,
    test_sky_stands_in_for_a_missing_monitor.py,
    test_the_cloud_hold_does_not_recurse.py, test_idle_park_hold.py,
    test_hold_ends_the_idle_stop_retry.py and test_idle_stop_retry_clock.py
    before this case.
    """
    now = time.time()
    limits = {"floor": {"min_alt_deg": 30.0},
              "ceiling": {"max_alt_deg": 70.0},
              "no_site": {"min_alt_deg": 30.0},
              "own floor": {}}[rule]
    e = SequenceEngine(sim_hub)
    e._cfg = AppConfig(safety=SafetyConfig(enabled=True, **limits))
    if rule == "ceiling":
        # On the meridian at the site's own declination: the zenith.
        b = _target("Bravo", _ra_at(0.0, now), LAT)
    elif rule == "own floor":
        b = _target("Bravo", _ra_at(12.0, now), 0.0, min_altitude_deg=30.0,
                    on_floor="advance")
    else:
        # Twelve hours from the meridian on the equator: far below any floor.
        b = _target("Bravo", _ra_at(12.0, now), 0.0)
    if rule == "no_site":
        monkeypatch.setattr(temp_store.cfg(), "site",
                            Site(name="", latitude=0.0, longitude=0.0,
                                 elevation_m=0.0, is_default=True))
    verdict = e._altitude_limit_verdict(b, projected=True, cfg=e._cfg)
    kind = None if verdict is None else verdict[0]
    assert kind == (None if rule == "own floor" else rule), (
        f"premise: the slew gate's verdict is {kind!r}, not this rule's")
    said = e._hold_repoint_refusal(b)
    assert said is not None, f"premise: {rule} refuses the re-point"
    # A plain bool, so pytest's rewriting has no sentence to echo back.
    numbered = re.search(r"[\d°]", said) is not None
    assert not numbered, (
        f"the {rule} refusal carries numbers, and it is published "
        f"(masked): {re.sub(r'[0-9]', '#', said)!r}")
    assert said == REFUSALS[rule], (
        f"the {rule} refusal names another rule: {said!r}")
