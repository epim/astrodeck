# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The cloud hold watches the mount on its own clock (#203, #205).

A cloud hold keeps the mount tracking by design, and the checks that watch a
tracking mount all hang off the frame loop, which the hold stops for up to
``CLOUD_MAX_HOLD_MIN``:

* #203. The floor and the flip point were never looked at during a hold. A
  setting target was tracked on below the floor, and a GEM was tracked on past
  its flip point, for as long as the hold lasted. The "safety rides value
  paths" class: the guard rode the frames, the hold stopped the frames, the
  hazard went on.
* #205, observed on the night of 2026-09-23/24 (M45 mosaic baseline, session
  8447192d). The AM5 stopped at its own meridian limit during a hold, silently.
  Every check exposure after that was taken on a stationary mount, so its stars
  were streaks, the clear-sky test read each one as cloud, and the hold went on
  publishing "held for cloud" for at least forty minutes over a mount that had
  stopped with a flip still owed. The same class: the tracking check rode the
  light frames.

Now the wait between probes is ``HOLD_WATCH_S`` ticks, each a look at the mount
(`_hold_watch`), and the look before a dark or a probe sees that whole
exposure ahead. The mount is asked whether it is tracking before every probe,
and a mount that is not gives no verdict.

THE HARNESS is test_idle_park_hold's clocked simulator: engine.py's clock is
fake and its sleeps park the run task on a timer heap, so a whole night runs
through the real `_run_scheduled`, the real frame loop and the real
`_hold_for_clear` without a real sleep. The hold stays ONE task (see the hold's
docstring for why), so the heap has no new sleeper to model. The run enters the
hold the way the rig did on 2026-09-23: safety armed with no monitor assigned,
and the frames' own sky verdict standing in for one (``hub.mode`` is not "sim",
because a simulated frame is not a sky).

THE HOUR ANGLE IS READ ON THE FAKE CLOCK TOO. The harness moves engine.py's
clock only, and `_maybe_meridian_flip`, `_wait_for_flip_point` and the
simulator's own pier side all ask `catalog.coords` (through `schedule`) for
"now" without passing one, so they read the wall clock: a flip gate graded
there never reaches a flip point that the fake night reaches in seconds, and
the simulated mount never crosses its meridian. The first draft of (a) passed
that way, for the wrong reason: the hold stopped the mount on the idle
predicate, which is given the fake time, while the flip gate it was supposed
to have tried first saw the flip point still minutes away. So both modules'
``time`` are the harness's clock here.

THE SKY IS A SCRIPT, and a check frame is judged the way the real detector
judges it: taken on a mount that was not tracking when the shutter opened, it
is a streak and reads as cloud, whatever the sky is doing (#205). Otherwise it
reads whatever the script says the sky is at that moment. The site is a
fixture, never the real one, and nothing here prints a mount's altitude or
azimuth.
"""
from __future__ import annotations

import math
import re
import time

import astrodeck.sequence.engine as engine_mod
import astrodeck.catalog.coords as coords_mod
from astrodeck.catalog import altaz
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence import schedule
from astrodeck.sequence.cloudstate import CloudState

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    LAT, LON, _Clocked, _constraint_waiter, _lst_h, _ra_at, sim_hub,
    temp_store)

WATCH = engine_mod.HOLD_WATCH_S
PROBE_EVERY = engine_mod.CLOUD_PROBE_EVERY_S
MAX_HOLD_S = 60.0 * engine_mod.CLOUD_MAX_HOLD_MIN
LEAD_S = 60.0 * schedule.MERIDIAN_FLIP_LEAD_MIN
#: Fake seconds per science exposure (and so per probe) unless a test says
#: otherwise.
EXP = 30.0
#: The run's first exposure opens at the start of the fake clock, and the sky
#: closes one second in, so the hold begins at the second frame boundary.
CLOSES_AT_S = 1.0


# ---------------------------------------------------------------------- harness

class _Watched:
    """One clocked-simulator night with a cloud hold in it.

    ``clears_at_s`` is when the scripted sky opens (None: never), and
    ``stops_tracking_at_s`` is when the mount stops tracking on its own, as
    the AM5 does at its limit (None: never). Both are fake seconds from the
    start of the clock. ``safety`` is extra `SafetyConfig` fields: the mount
    floor and the zenith keep-out. ``t0`` pins the start of the fake clock
    (`_Clocked`), for a test that runs the same night twice and compares
    the two (test_engine_logs_carry_no_site_numbers.py).
    """

    def __init__(self, hub, store, monkeypatch, *, horizon_s: float,
                 clears_at_s: float | None = None,
                 stops_tracking_at_s: float | None = None,
                 safety: dict | None = None,
                 closes_at_s: float = CLOSES_AT_S,
                 t0: float | None = None):
        store.set_safety(SafetyConfig(enabled=True, sky_fallback_hold=True,
                                      **(safety or {})))
        hub.mode = "native"                 # a real rig: its frames are a sky
        hub.devices.pop("safety", None)     # ...with no monitor assigned
        hub.devices.pop("focuser", None)    # no post-flip sweep to clock
        self.hub = hub
        self.run = _Clocked(hub, monkeypatch, horizon_s=horizon_s, t0=t0)
        self.engine = self.run.engine
        self.t0 = self.run.t0
        monkeypatch.setattr(coords_mod, "time", self.run.clock)
        monkeypatch.setattr(schedule, "time", self.run.clock)
        #: When the scripted sky closes. A negative value has it shut before
        #: the run's first target is set up.
        self.closes_at = self.t0 + closes_at_s
        self.clears_at = (None if clears_at_s is None
                          else self.t0 + clears_at_s)
        self.stops_at = (None if stops_tracking_at_s is None
                         else self.t0 + stops_tracking_at_s)
        self.stopped_at: float | None = None
        #: (fake time the shutter opened, was the mount tracking then) of
        #: every check frame taken for the held target
        self.probes: list[tuple[float, bool]] = []
        #: the same, for a check frame taken with no target, which a hold
        #: entered from a scheduler wait takes
        self.untargeted: list[tuple[float, bool]] = []
        #: (fake time, state, hold, detail) after every `_set_state`
        self.states: list[tuple[float, str, str | None, str]] = []
        #: (fake time, the published ``sky.hold_deferred``) after the same
        self.deferred: list[tuple[float, str | None]] = []
        self.tel = hub.devices["telescope"]

        # The debounced verdict the safety gate's fallback reads, and its
        # sentence, which assumes a reading exists once the verdict is not None.
        monkeypatch.setattr(CloudState, "cloudy",
                            lambda _cs, now: self.sky_cloudy(now))
        monkeypatch.setattr(CloudState, "describe",
                            lambda _cs, now: ("cloudy" if self.sky_cloudy(now)
                                              else "clear"))
        monkeypatch.setattr(engine_mod, "verdict_from_info", self._verdict)

        inner_capture = hub.capture

        async def capture(exposure_s, *a, **kw):
            self._mount_events()
            tracking = bool(self.tel.rig.tracking)
            opened = self.run.clock.t
            info = dict(await inner_capture(exposure_s, *a, **kw) or {})
            info["_streaked"] = not tracking
            if kw.get("save") is False and not self.run.frozen.is_set():
                (self.probes if kw.get("target") else
                 self.untargeted).append((opened, tracking))
            return info

        monkeypatch.setattr(hub, "capture", capture)

        inner_get = self.tel.get_tracking

        async def get_tracking():
            self._mount_events()
            return await inner_get()

        monkeypatch.setattr(self.tel, "get_tracking", get_tracking)

        real_set_state = self.engine._set_state

        def set_state(**kw):
            real_set_state(**kw)
            if not self.run.frozen.is_set():
                s = self.engine.state
                self.states.append((self.run.clock.t, s.get("state"),
                                    s.get("hold"), s.get("detail") or ""))
                self.deferred.append((self.run.clock.t, (s.get("sky") or {})
                                      .get("hold_deferred")))

        monkeypatch.setattr(self.engine, "_set_state", set_state)

    # -------------------------------------------------------------- the world

    def sky_cloudy(self, now: float) -> bool:
        if now < self.closes_at:
            return False
        return self.clears_at is None or now < self.clears_at

    def _verdict(self, info):
        if (info or {}).get("_streaked"):
            return (True, 0.5, "cloudy: streaked stars on a stopped mount")
        cloudy = self.sky_cloudy(self.run.clock.t)
        return (cloudy, 0.5 if cloudy else 9.0,
                "cloudy: low contrast" if cloudy else "clear")

    def _mount_events(self) -> None:
        """Apply the mount's own stop once its time has come. Lazily, on every
        read of the mount's tracking and every exposure, which is everything
        that could observe it."""
        if (self.stops_at is not None and self.stopped_at is None
                and self.run.clock.t >= self.stops_at):
            self.tel.rig.tracking = False
            self.stopped_at = self.stops_at

    # -------------------------------------------------------------- the night

    async def night(self, plan: SequencePlan, *, timeout: float = 120.0,
                    to_the_horizon: bool = True, **start_kw) -> None:
        """Run ``plan`` to the horizon. ``start_kw`` goes to `start`, as
        ``tracking=`` for a run handed a target auto-resume re-centred."""
        import asyncio
        self.engine.start(plan, **start_kw)
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if self.run.frozen.is_set() or not self.engine.running:
                break
            await self.run._real_sleep(0.01)
        if to_the_horizon:
            assert self.run.frozen.is_set(), (
                f"premise: the run must still be going at the horizon; it "
                f"ended at fake +{self.run.clock.t - self.t0:.0f}s: "
                f"{self.engine.state.get('end_reason')!r}, "
                f"{self.engine.state.get('detail')!r}")

    async def close(self) -> None:
        await self.run.close()

    # ------------------------------------------------------------- the record

    def hold_started(self) -> float:
        held = [t for t, st, _h, _d in self.states if st == "holding"]
        assert held, "premise: the run never entered a cloud hold"
        return held[0]

    def released_at(self) -> float | None:
        done = [t for t, st, hold, d in self.states
                if st == "running" and hold is None
                and d.startswith("resumed after")]
        return done[0] if done else None

    def stops(self, since: float) -> list[float]:
        return [t for t, on, _who in self.run.tracking_calls
                if not on and t >= since]

    def rel(self, ts, origin: float) -> list[float]:
        return [round(t - origin, 1) for t in ts]


def _target(name: str, ra: float, dec: float, *, exposure_s: float = EXP,
            count: int = 40, **sched) -> Target:
    t = Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=exposure_s, gain=100,
                                   count=count)])
    for k, v in sched.items():
        setattr(t.schedule, k, v)
    return t


def _plan(*targets: Target, darks: int = 0, flip: bool = True) -> SequencePlan:
    return SequencePlan(name="hold", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=flip,
                        safety_check=True, cloud_hold_darks=darks,
                        targets=list(targets))


#: Sidereal seconds per solar second. The flip point is where the hour angle
#: comes within the lead, and an hour angle runs on sidereal time: a target
#: placed by solar seconds reaches the engine's flip point 1.6 s late in ten
#: minutes, which is how the first draft of (a) measured a "late" stop.
SIDEREAL = 1.00273790935


def _flip_ra(t_flip: float) -> float:
    """The RA whose hour angle reaches the plan's flip point (the default
    lead before transit) at ``t_flip``, by the engine's own arithmetic."""
    return (_lst_h(t_flip) + LEAD_S / 3600.0) % 24.0


def _crossing_ra(alt_deg: float, dec: float, t_cross: float, *,
                 rising: bool) -> float:
    """The RA whose altitude at the fixture site passes ``alt_deg`` at
    ``t_cross``, rising (east) or setting (west)."""
    lat = math.radians(LAT)
    d = math.radians(dec)
    cos_h = ((math.sin(math.radians(alt_deg)) - math.sin(lat) * math.sin(d))
             / (math.cos(lat) * math.cos(d)))
    ha_h = math.degrees(math.acos(cos_h)) / 15.0
    return _ra_at(-ha_h if rising else ha_h, t_cross)


def _crosses(ra: float, dec: float, alt_deg: float, t: float, *,
             rising: bool) -> bool:
    """Premise check, answered as a yes or no so no altitude is printed: the
    target is on the near side of ``alt_deg`` a minute before ``t`` and on the
    far side a minute after."""
    before = altaz(ra, dec, LAT, LON, t - 60.0)[0]
    after = altaz(ra, dec, LAT, LON, t + 60.0)[0]
    return (before < alt_deg < after) if rising else (before > alt_deg > after)


# ------------------------------------------------------------- (a) the flip point

async def test_a_hold_that_starts_a_minute_before_the_flip_point_stops_there(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(a) Alpha is 60 s from the plan's flip point when the hold begins, and
    the sky never opens. The hold takes the owed flip the frame loop's way at
    the flip point, and the simulated AM5, like the real one, cannot flip
    before the meridian: its goto lands on the side it was on. A flip that
    cannot be taken stops tracking, so the mount stops at the flip point, and
    nothing turns tracking on again until the flip gate's retry at the
    meridian itself. The night runs to 30 s short of the hold's 45 min bound.

    Mutant "hold loop without the check" (`_hold_watch` returns at once):
    RED, the mount tracks through the flip point and the meridian, with no
    flip tried, for the whole hold (observed) -
        AssertionError: the hold kept tracking Alpha past its flip point: no
        stop in the 2670 s from the hold's start to the horizon, 30 s short
        of the 45 min bound; tracking calls after the hold began: []
    Mutant "a parked hold probes anyway" (the ``_hold_parked`` check before
    the tracking probe made False): RED, the next probe finds the mount
    stopped and resumes it on the pre-flip side (observed) -
        AssertionError: tracking came back on at [128.0, 658.4, 658.4,
        688.4, 698.0] s, before the meridian at 658.4 s
    """
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=EXP + MAX_HOLD_S - 30.0)
    t_flip = w.t0 + EXP + 60.0
    t_transit = t_flip + LEAD_S / SIDEREAL
    a = _target("Alpha", _flip_ra(t_flip), 20.0)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        assert abs((t_flip - t_h) - 60.0) < 1.0, (
            f"premise: the hold begins 60 s before the flip point "
            f"({t_flip - t_h:.1f})")
        stops = w.stops(t_h)
        calls = [(round(t - t_h, 1), on) for t, on, _w in w.run.tracking_calls
                 if t >= t_h]
        assert stops, (
            f"the hold kept tracking Alpha past its flip point: no stop in "
            f"the {w.run.horizon - t_h:.0f} s from the hold's start to the "
            f"horizon, {t_h + MAX_HOLD_S - w.run.horizon:.0f} s short of the "
            f"45 min bound; tracking calls after the hold began: {calls}")
        # The flip gate waits for the flip point and then tries the flip: a
        # goto, which turns tracking on first, and a plate solve whose
        # exposures take fake time. The stop follows that attempt, not the
        # flip point itself, and comes before anything else.
        tried = [t for t, on, _w in w.run.tracking_calls
                 if on and t_flip - 0.5 <= t <= t_flip + 0.5]
        assert tried, (
            f"premise: the flip was tried at the flip point, "
            f"{t_flip - t_h:.1f} s into the hold: {calls}")
        after = [(t, on) for t, on, _w in w.run.tracking_calls
                 if t > tried[-1] or (t == tried[-1] and not on)]
        assert after and after[0][1] is False, (
            f"the flip attempt at {tried[-1] - t_h:.1f} s was followed by "
            f"{[(round(t - t_h, 1), on) for t, on in after[:2]]}, not a stop")
        assert stops[0] - tried[-1] < 10.0, (
            f"the mount stopped {stops[0] - tried[-1]:.1f} s after the flip "
            f"attempt, which took no exposure that long")
        back_on = [t for t, on in after if on]
        assert back_on and min(back_on) >= t_transit - 0.5, (
            f"tracking came back on at {w.rel(back_on, t_h)} s, before the "
            f"meridian at {t_transit - t_h:.1f} s")
        assert not [t for t, _tr in w.probes if stops[0] <= t < min(back_on)], (
            f"a check was taken on the stopped mount: {w.probes}")
        assert any("flip point and the flip cannot be taken" in m
                   for _l, m, _s in bus_lines)
        assert w.released_at() is None, "premise: the sky stayed shut"
        said = [d for t, _s, _h, d in w.states
                if stops[0] <= t < min(back_on)]
        assert any("the mount is stopped" in d for d in said), said[:3]
    finally:
        await w.close()


async def test_the_parked_hold_flips_at_the_meridian_and_judges_the_sky_again(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(a), the rest of #205's night. The hold stopped the AM5 at the plan's
    flip point because the flip could not be taken there. The retry the flip
    gate keeps armed waits for the meridian itself, and the hold's looks keep
    giving it the chance: at the meridian the goto lands on the far side, the
    latch is spent, the flip's own goto has tracking back on, and the hold
    goes back to judging the sky. It clears two minutes after the meridian and
    the hold releases on two clear checks taken on the new side, with no check
    ever judged on the stopped mount.

    Mutant "a parked hold never goes back to judging" (the un-park block at
    the end of `_hold_flip_watch` deleted): RED (observed) -
        AssertionError: the flip at the meridian never un-parked the hold
    Mutant "hold loop without the check" is RED here too, on the premise
    (observed) -
        AssertionError: premise: stopped at the flip point: []
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_h0 + 1500.0,
                 clears_at_s=t_h0 + 60.0 + LEAD_S + 120.0)
    t_flip = w.t0 + t_h0 + 60.0
    t_transit = t_flip + LEAD_S / SIDEREAL
    a = _target("Alpha", _flip_ra(t_flip), 20.0)
    b = _constraint_waiter("Bravo", w.t0)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        stops = w.stops(t_h)
        assert stops and abs(stops[0] - t_flip) <= WATCH + 0.5, (
            f"premise: stopped at the flip point: {w.rel(stops, t_h)}")
        flips = [m for _l, m, _s in bus_lines
                 if "the meridian flip has been taken" in m]
        assert flips, "the flip at the meridian never un-parked the hold"
        released = w.released_at()
        judged = [t for t, tracking in w.probes if t >= t_h]
        assert released is not None, (
            f"the hold never released after the flip was taken at "
            f"{t_transit - t_h:.1f} s: it sat stopped under a sky that "
            f"cleared at {w.clears_at - t_h:.1f} s; checks judged at "
            f"{w.rel(judged, t_h)} s")
        assert released > t_transit
        assert all(tracking for t, tracking in w.probes if t >= t_h), (
            f"a check was judged on a stopped mount: {w.probes}")
        after_flip = [t for t in judged if t >= t_transit]
        assert len(after_flip) >= engine_mod.CLOUD_RESUME_CLEAR_PROBES
    finally:
        await w.close()


async def test_a_flip_the_mount_never_takes_is_not_tracked_past_in_the_hold(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(a) with a mount that never changes pier side, the 2026-09-10/11 shape
    `_enforce_flip_owed` exists for. The hold stops at the flip point as in
    (a). At the meridian the flip gate's retry re-slews, the mount still
    reports the side it started on, and the gate spends its latch anyway (a
    second no-op is not retried), which is also what lifts the hold's stop.
    The owed-flip invariant is what is left: past the meridian on the
    pre-flip side it refuses, holds for ``flip_owed_hold_min`` (two minutes
    here) and sets Alpha aside with tracking stopped. No check frame is
    taken on the pre-flip side past the meridian.

    Mutant "no owed flip in the hold" (the ``if due: _enforce_flip_owed``
    in `_hold_flip_watch` deleted), which every other test here survives:
    RED (observed) -
        AssertionError: the hold judged the sky past the meridian with the
        mount still on its pre-flip side: checks at [658.4, 808.4, 958.4,
        1108.4] s, the meridian at 658.4 s
    Mutant "no stop on set-aside" (see the next-target test) was RED here
    too until #221, because Bravo's wait then opened a target-less hold and
    nothing looked at Alpha. It is GREEN here now (observed): the wait opens
    no hold, and its idle park-hold stops Alpha on the idle clock. The
    set-aside's own stop is held by the next-target test, where no wait
    follows.
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_h0 + 60.0 + LEAD_S + 480.0,
                 safety={"flip_owed_hold_min": 2.0})
    t_flip = w.t0 + t_h0 + 60.0
    t_transit = t_flip + LEAD_S / SIDEREAL
    a = _target("Alpha", _flip_ra(t_flip), 20.0)
    b = _constraint_waiter("Bravo", w.t0)
    real_side = w.tel.pier_side
    first: list = []

    async def stuck_side():
        # The first answer, forever: a goto never changes this mount's side.
        if not first:
            first.append(await real_side())
        return first[0]

    monkeypatch.setattr(w.tel, "pier_side", stuck_side)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        stops = w.stops(t_h)
        assert stops and abs(stops[0] - t_flip) <= WATCH + 0.5, (
            f"premise: stopped at the flip point: {w.rel(stops, t_h)}")
        msgs = [m for _l, m, _s in bus_lines]
        past = [t for t, _tr in w.probes if t >= t_transit]
        assert not past, (
            f"the hold judged the sky past the meridian with the mount still "
            f"on its pre-flip side: checks at {w.rel(past, t_h)} s, the "
            f"meridian at {t_transit - t_h:.1f} s")
        assert any("a meridian flip is required and the mount is still on the" in m
                   and "Alpha" in m for m in msgs), msgs[-8:]
        assert any("skip" in m.lower() and "Alpha" in m for m in msgs), (
            f"Alpha was never set aside: {msgs[-6:]}")
        last = [(t, on) for t, on, _w in w.run.tracking_calls
                if t >= t_transit]
        assert last and last[-1][1] is False, (
            f"the mount was left tracking after the set-aside: "
            f"{[(round(t - t_h, 1), on) for t, on in last[-3:]]}")
        # Any state, not the one at the horizon: the scheduler then waits on
        # Bravo, whose "waiting" publish replaces the detail. (That wait,
        # under the same cloud, used to open a target-less hold of its own;
        # since #221 it opens none, and says so.)
        assert any(d == "cloud hold ended: Alpha was set aside"
                   for _t, _s, _h, d in w.states)
    finally:
        await w.close()


# ------------------------------------------------------ (b) tracking lost mid-hold

async def test_a_mount_that_stops_tracking_mid_hold_is_restored_not_judged(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(b) #205. Alpha is far from its floor and its flip point. Five minutes
    into the hold the mount stops tracking on its own. The next check is not
    judged: the hold says the mount stopped, publishes that rather than "held
    for cloud" alone, and runs the frame loop's resume-then-recover, which
    resumes it. The sky clears just after the stop, and the hold releases on
    two clear checks taken on the tracking mount, well inside its bound.

    Mutant "the hold skips the tracking probe" (the `_tracking_now` branch
    before the probe never taken): RED, every check after the stop is a streak
    read as cloud, and the hold runs to its limit and aborts (observed) -
        AssertionError: the hold never released: it read cloudy to its limit
        and the run ended 'unsafe' ('cloud hold exceeded 45 min - parking');
        the mount stopped at 310.0 s and the sky cleared at 320.0 s; checks
        at [120.0, 270.0, 420.0, 570.0] s, tracking [True, True, False,
        False]

    IN PLACE, BECAUSE THE MOUNT IS ON ALPHA: Alpha's own setup pointed it
    there, so the hold had nothing to point at its open, and by the time
    its tracking read runs, any hold's mount is on its target (#224; a hold
    whose mount was elsewhere is stopped until a look points it). The open's
    choice is held by test_cloud_hold_follows_the_mount.py: its control shows
    a hold on the mount's own target does not slew, and the next-target test
    below shows one opened elsewhere points the mount before anything else.

    THE CONTROL FOR #248 (H3 orchestrator ruling 6). That branch now has a
    second arm: a mount the ENGINE stopped since it was last pointed is
    re-pointed (test_hold_repoints_after_any_stop.py). This mount stopped on
    its own, so it takes the resume in place, as before, and no slew.
    Mutant "the stopped branch re-points whatever stopped the mount" (its
    ``self._mount_stopped_since is not None`` test made True): RED
    (observed) -
        AssertionError: a mount that stopped on its own was re-slewed
        instead of resumed in place: slews after the stop at [424.0] s
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_h0 + MAX_HOLD_S + 60.0,
                 clears_at_s=t_h0 + 320.0,
                 stops_tracking_at_s=t_h0 + 310.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    b = _constraint_waiter("Bravo", w.t0)
    try:
        await w.night(_plan(a, b), to_the_horizon=False)
        t_h = w.hold_started()
        assert abs(t_h - (w.t0 + t_h0)) < 1.0, "premise: the hold begins"
        released = w.released_at()
        checks = [t for t, _tr in w.probes if t >= t_h]
        assert released is not None, (
            f"the hold never released: it read cloudy to its limit and the "
            f"run ended {w.engine.state.get('end_reason')!r} "
            f"({w.engine.state.get('detail')!r}); "
            f"the mount stopped at {w.stopped_at - t_h:.1f} s and the sky "
            f"cleared at {w.clears_at - t_h:.1f} s; checks at "
            f"{w.rel(checks[:4], t_h)} s, tracking "
            f"{[tr for t, tr in w.probes if t >= t_h][:4]}")
        assert w.stopped_at is not None, "premise: the mount stopped"
        assert released - t_h < MAX_HOLD_S
        # Before the release, whose own setup re-acquires Alpha by a slew.
        moved = [t for t in w.run.slews if w.stopped_at <= t < released]
        assert not moved, (
            f"a mount that stopped on its own was re-slewed instead of "
            f"resumed in place: slews after the stop at "
            f"{w.rel(moved, t_h)} s")
        assert all(tr for t, tr in w.probes if t >= t_h), (
            f"a check was judged on a stopped mount: "
            f"{[(round(t - t_h, 1), tr) for t, tr in w.probes]}")
        msgs = [m for _l, m, _s in bus_lines]
        assert sum("has stopped tracking during the cloud hold" in m
                   for m in msgs) == 1, msgs
        assert any("trying to resume" in m for m in msgs), (
            f"the hold never ran the in-place resume on the mount that "
            f"stopped on Alpha: "
            f"{[m for m in msgs if 'Alpha' in m and 'tracking' in m][-2:]}")
        assert any(m == "tracking resumed" for m in msgs), msgs
        said = [d for t, st, _h, d in w.states
                if t >= w.stopped_at and st == "holding"]
        assert said and "stopped tracking" in said[0], said[:3]
        clear_after = [t for t in checks if w.stopped_at <= t < released]
        assert len(clear_after) >= engine_mod.CLOUD_RESUME_CLEAR_PROBES
    finally:
        await w.close()


# ------------------------------------------------------------------ (c) the floor

async def test_a_target_sinking_through_the_mount_floor_mid_hold_is_set_aside(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(c) Alpha sinks through the mount's 30 degree floor 250 s into the
    hold. The look that first sees the crossing inside its one interval stops
    tracking and sets Alpha aside (StopTarget, as the frame loop does), and
    the published state no longer says the run is held for cloud. The night
    goes on to Bravo's wait.

    Mutant "floor unchecked in the hold" (the mount-floor branch of
    `_hold_watch` deleted): RED (observed) -
        AssertionError: Alpha sank through the floor 250.0 s into the hold
        and the hold went on tracking it: stops at [] s, still holding at
        the horizon
    Mutant "the hold's end is not published" (the `except StopTarget` in
    `_hold_for_clear` deleted): RED (observed) -
        AssertionError: the target was set aside but the published state
        still says hold='clouds'

    WORDS ONLY in the reason, as the idle park-hold's reasons are: it reaches
    the log with the skip and the published detail, which viewers read, and
    a pointing plus a time is the site (#19, #140). Mutant "the slew gate's
    sentence as the reason" (``raise StopTarget(limit[1])``): RED
    (observed; degree signs spelled out, the fixture target's azimuth
    elided) -
        AssertionError: a line about the set-aside carries a number:
        'Alpha: skipped — target Alpha altitude 30 deg below safety floor
        30 deg (az ...)'
    Since #233 (H3 T11) ``limit[1]`` is the gate's words, and that mutant
    passes (observed: 1 passed); the numbers are the verdict's
    ``site_detail``. Mutant "the slew gate's numbers as the reason"
    (``raise StopTarget(limit.site_detail)``): RED (observed, spelled and
    elided as above) -
        AssertionError: a line about the set-aside carries a number:
        'Alpha: skipped — target Alpha altitude 30 deg below safety floor
        30 deg (az ...)'
    """
    t_h0 = EXP
    t_cross_s = t_h0 + 250.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_cross_s + 150.0,
                 clears_at_s=t_cross_s - 15.0,
                 safety={"min_alt_deg": 30.0})
    t_cross = w.t0 + t_cross_s
    ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(ra, 20.0, 30.0, t_cross, rising=False), "premise"
    a = _target("Alpha", ra, 20.0)
    b = _constraint_waiter("Bravo", w.t0)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        stops = w.stops(t_h)
        assert stops and stops[0] <= t_cross + 0.5, (
            f"Alpha sank through the floor {t_cross - t_h:.1f} s into the "
            f"hold and the hold went on tracking it: stops at "
            f"{w.rel(stops, t_h)} s, "
            f"{'still holding' if w.engine.state.get('hold') else 'not holding'}"
            f" at the horizon")
        assert stops[0] >= t_cross - WATCH - 0.5, (
            f"stopped {t_cross - stops[0]:.1f} s before the crossing: more "
            f"than one interval early")
        msgs = [m for _l, m, _s in bus_lines]
        assert any("reaches the mount's altitude floor during the cloud hold"
                   in m for m in msgs), msgs
        assert any("skip" in m.lower() and "Alpha" in m for m in msgs), (
            f"premise: the scheduler set Alpha aside: {msgs[-8:]}")
        assert not [t for t, _tr in w.probes if t > stops[0]], (
            f"a check was taken after Alpha was set aside: {w.probes}")
        assert w.engine.state.get("hold") is None, (
            f"the target was set aside but the published state still says "
            f"hold={w.engine.state.get('hold')!r}")
        ended = [d for _t, _s, _h, d in w.states
                 if d.startswith("cloud hold ended")]
        assert ended == ["cloud hold ended: Alpha was set aside"], ended
        about = [m for m in msgs if "Alpha" in m and (
            "floor" in m or "skipped" in m)]
        assert about, "premise: the set-aside was logged"
        numbered = [m for m in about if re.search(r"\d", m)]
        assert not numbered, (
            f"a line about the set-aside carries a number: {numbered[0]!r}")
    finally:
        await w.close()


async def test_a_target_sinking_through_its_own_floor_mid_hold_is_set_aside(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(c), the target's own floor. Alpha's schedule says "set aside" below 30
    degrees (``on_floor = advance``), and no mount floor is configured. The
    frame loop asks `_enforce_altitude_floor` of the LIVE altitude between
    frames; the hold now asks it on every look, so Alpha is set aside, with
    tracking stopped, by the first look after it sinks through, not when
    the hold ends.

    Mutant "the target's floor unchecked in the hold" (the
    `_enforce_altitude_floor` call in `_hold_watch` deleted): RED (observed) -
        AssertionError: Alpha sank through its own floor 250.0 s into the
        hold and was not set aside by the horizon, 150 s later
    Mutant "no look before the probe" (the `_hold_watch` with the probe's
    exposure ahead, just before the tracking check, deleted): RED, the
    look is the one that meets the live floor at 270 s, and without it the
    next is 30 s after the probe (observed) -
        AssertionError: crossed at 250.0 s, stopped at 300.0 s
    """
    t_h0 = EXP
    t_cross_s = t_h0 + 250.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_cross_s + 150.0,
                 clears_at_s=t_cross_s + 10.0)
    t_cross = w.t0 + t_cross_s
    ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(ra, 20.0, 30.0, t_cross, rising=False), "premise"
    a = _target("Alpha", ra, 20.0, min_altitude_deg=30.0, on_floor="advance")
    b = _constraint_waiter("Bravo", w.t0)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        stops = w.stops(t_h)
        msgs = [m for _l, m, _s in bus_lines]
        aside = [m for m in msgs if "setting it aside for the rest of this run"
                 in m and "Alpha" in m]
        assert aside and stops, (
            f"Alpha sank through its own floor {t_cross - t_h:.1f} s into "
            f"the hold and was not set aside by the horizon, "
            f"{w.run.horizon - t_cross:.0f} s later")
        assert t_cross - 0.5 <= stops[0] <= t_cross + WATCH + 0.5, (
            f"crossed at {t_cross - t_h:.1f} s, stopped at "
            f"{stops[0] - t_h:.1f} s")
        assert w.engine.state.get("hold") is None
    finally:
        await w.close()


def _nearer(ra: float, dec: float, a: Target, b: Target) -> bool:
    """Is the pointing (ra, dec) nearer ``a`` than ``b``? A yes or no, so no
    pointing is printed; coarse, because the two are hours of RA apart."""
    def far(t: Target) -> float:
        d_ra = abs(((ra - t.ra_hours) + 12.0) % 24.0 - 12.0) * 15.0
        return d_ra + abs(dec - t.dec_deg)
    return far(a) < far(b)


async def test_the_next_targets_hold_never_tracks_where_the_last_was_set_aside(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(c), and what comes after it under the same cloud. Alpha sinks through
    the mount's 30 degree floor 250 s into the hold and is set aside with
    tracking stopped. Bravo is ready at once, so its `_setup_target` runs the
    pre-slew safety gate with the sky still shut, and that gate opens a NEW
    hold, for Bravo, with the mount still stopped where Alpha was: below the
    floor. It must not resume in place: nothing watches that position (every
    look is at Bravo), and tracking from there follows Alpha on down through
    the floor it was stopped for (#225). Since #224 it does not wait for its
    tracking read to find the mount stopped either: the hold points the
    mount at Bravo when it opens, behind the slew gate, judges the sky from
    there, and releases when it clears.

    The constraint-waiting Bravo of (c) cannot tell whether the HOLD stopped
    the mount: the scheduler's wait park-holds at once on Alpha's floor and
    stops it at the same fake moment. A Bravo that is ready leaves no wait to
    do it, so only the hold's own stop is left to see.

    Mutant "watch B while tracking A" (the re-point at the hold's open
    removed: its ``elsewhere`` made False): RED, the tracking read finds the
    mount stopped where Alpha was and resumes it there (observed) -
        AssertionError: Bravo's hold turned tracking on with the mount still
        where Alpha was set aside, 128.0 s after the set-aside, with no slew
        in between: set_tracking(True) at [368.0, 668.0] s
    Mutant "no stop on set-aside" (the `_stop_tracking_quietly` in
    `_hold_watch`'s ``except StopTarget`` deleted): RED, Bravo's re-point
    now leaves Alpha at the same fake moment, but with no stop first
    (observed) -
        AssertionError: Alpha was set aside at its floor and the mount was
        left tracking it: no stop from 30 s before the crossing to Bravo's
        slew; tracking calls [(240.0, True), (690.0, True), (690.0, True),
        (690.0, True), (690.0, True)] s
    CONTROL, the other arm: in (b) the mount stopped on the very target the
    hold holds, and the resume there is still in place; the hold whose mount
    is on its target does not slew at its open
    (test_cloud_hold_follows_the_mount.py).
    """
    t_h0 = EXP
    t_cross_s = t_h0 + 250.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_cross_s + 600.0,
                 clears_at_s=t_cross_s + 150.0,
                 safety={"min_alt_deg": 30.0})
    t_cross = w.t0 + t_cross_s
    ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(ra, 20.0, 30.0, t_cross, rising=False), "premise"
    a = _target("Alpha", ra, 20.0)
    b = _target("Bravo", _ra_at(-3.0, w.t0), 40.0)
    # A yes or no, so a failure prints no altitude.
    clear = all(altaz(b.ra_hours, b.dec_deg, LAT, LON, t)[0] > 35.0
                for t in (t_cross, t_cross + 600.0))
    assert clear, "premise: Bravo is well clear of the floor all along"
    tel = w.tel
    #: (fake time, on, the mount nearer Alpha than Bravo) of every
    #: set_tracking, read from the simulated mount before it obeys
    calls: list[tuple[float, bool, bool]] = []
    inner = tel.set_tracking

    async def set_tracking(on):
        if not w.run.frozen.is_set():
            calls.append((w.run.clock.t, bool(on),
                          _nearer(tel.rig.ra_hours, tel.rig.dec_deg, a, b)))
        return await inner(on)

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        msgs = [m for _l, m, _s in bus_lines]
        assert any("reaches the mount's altitude floor during the cloud hold"
                   in m for m in msgs), "premise: Alpha was set aside"
        aside = [t for t, _st, _h, d in w.states
                 if d == "cloud hold ended: Alpha was set aside"]
        assert aside, "premise: the first hold ended with the set-aside"
        t_aside = aside[0]
        slews = [t for t in w.run.slews if t >= t_aside]
        until = slews[0] if slews else w.run.horizon
        stopped = [t for t, on, on_a in calls
                   if not on and on_a and t_cross - WATCH - 0.5 <= t <= until]
        assert stopped, (
            f"Alpha was set aside at its floor and the mount was left "
            f"tracking it: no stop from {WATCH:.0f} s before the crossing to "
            f"Bravo's slew; tracking calls "
            f"{[(round(t - t_h, 1), on) for t, on, _a in calls if t >= t_h]} s")
        second = [t for t, st, _h, _d in w.states
                  if st == "holding" and t >= t_aside]
        assert second, "premise: Bravo's setup gate opened a second hold"
        resumed_there = [t for t, on, on_a in calls
                         if on and on_a and t >= t_aside]
        assert not resumed_there, (
            f"Bravo's hold turned tracking on with the mount still where "
            f"Alpha was set aside, "
            f"{resumed_there[0] - t_aside:.1f} s after the set-aside, with "
            f"{'a slew' if slews and slews[0] < resumed_there[0] else 'no slew'}"
            f" in between: set_tracking(True) at "
            f"{w.rel(resumed_there, t_h)} s")
        assert slews, "the mount was never pointed at Bravo during its hold"
        on_bravo = [t for t, on, on_a in calls
                    if on and not on_a and t >= slews[0]]
        assert on_bravo and on_bravo[0] < (w.released_at() or w.run.horizon), (
            "Bravo's hold never tracked Bravo")
        assert any("it is now pointed at this one and tracking" in m
                   for m in msgs), msgs[-6:]
        released = w.released_at()
        assert released is not None and released > on_bravo[0], (
            f"the hold never released on Bravo after the sky cleared at "
            f"{w.clears_at - t_h:.1f} s")
        assert all(tr for t, tr in w.probes if t >= t_aside), (
            f"a check was judged on a stopped mount: {w.probes}")
    finally:
        await w.close()


async def test_control_the_next_target_a_slew_would_find_in_the_keep_out_is_not_slewed_to(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL for the re-point above, and #228's projection half. As there,
    Alpha is set aside at its floor and Bravo's setup gate opens a second
    hold with the mount stopped where Alpha was, but Bravo is rising into a
    70 degree keep-out 100 s after that hold opens. The slew gate, projecting
    a slew 180 s ahead, would raise the SafetyAbort that ends the run. So the
    hold does not slew (`_hold_repoint_refusal`): it stops the mount (it is
    already stopped; the stop is asked once), says why, once, and judges no
    sky. The run is still going at the horizon.

    #228: THE DETAIL SAYS SO FOR THE WHOLE SPELL. Before the fix the hold
    published its opening detail again after a refused re-point, "held for
    cloud - ... Checking at the science exposure ...", over a stopped mount
    that judged no sky, until its bound. From Bravo's hold on, no detail
    claims a check; the last one says the mount is stopped and why.

    Mutant "re-point elsewhere without the refusal" (the open's ``why is
    None`` test before ``_hold_repoint(target, elsewhere=True)`` made True):
    RED (observed; degree signs spelled out, the fixture target's azimuth
    elided) -
        AssertionError: premise: the run must still be going at the horizon;
        it ended at fake +270s: 'unsafe', 'target Bravo altitude 70 deg
        above the zenith keep-out 70 deg (az ...) - the mount can reach its
        own tripod up there'
    Run again after H3 (T11, #233): GREEN (observed: 1 passed). The slew
    gate's refusal no longer ends the run: `_hold_repoint` catches its
    `SlewRefused` by type, stops tracking and holds (#240, H3 orchestrator
    ruling 3, T7), so without the pre-ask the gate refuses the same slew
    and nothing moves. The sentence the run ended with is words since
    #233. This case now grades the pre-ask's result, not its necessity.
    Mutant "restore the detail unconditionally" (the open's
    ``self._set_state(detail=detail)`` run after the park as well): RED
    (observed) -
        AssertionError: the hold claimed to check the sky while the mount
        was stopped: 'held for cloud - no safety monitor is assigned and the
        frames say the sky has closed in. Checking at the science exposure,
        with up to 2 min between checks; parks after 45 min' at 240.0 s
    """
    t_h0 = EXP
    t_cross_s = t_h0 + 250.0
    # The first hold's look at +240 s sees Alpha's crossing and sets it
    # aside, and Bravo's setup opens the second hold at that moment.
    t_in_s = t_h0 + 240.0 + 100.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_in_s + 240.0,
                 safety={"min_alt_deg": 30.0, "max_alt_deg": 70.0})
    t_cross = w.t0 + t_cross_s
    ra = _crossing_ra(30.0, 20.0, t_cross, rising=False)
    assert _crosses(ra, 20.0, 30.0, t_cross, rising=False), "premise"
    a = _target("Alpha", ra, 20.0)
    t_in = w.t0 + t_in_s
    b_ra = _crossing_ra(70.0, 45.0, t_in, rising=True)
    assert _crosses(b_ra, 45.0, 70.0, t_in, rising=True), "premise"
    b = _target("Bravo", b_ra, 45.0)
    try:
        await w.night(_plan(a, b))
        t_h = w.hold_started()
        aside = [t for t, _st, _h, d in w.states
                 if d == "cloud hold ended: Alpha was set aside"]
        assert aside, "premise: the first hold ended with the set-aside"
        second = [t for t, st, _h, _d in w.states
                  if st == "holding" and t >= aside[0]]
        assert second, "premise: Bravo's setup gate opened a second hold"
        msgs = [m for _l, m, _s in bus_lines]
        assert not [t for t in w.run.slews if t >= aside[0]], (
            f"the hold slewed toward a target the slew gate would refuse: "
            f"slews at {w.rel(w.run.slews, t_h)} s")
        assert not [t for t, on, _w in w.run.tracking_calls
                    if on and t >= aside[0]], "tracking was turned back on"
        assert not [t for t, _tr in w.probes if t >= aside[0]], (
            f"a check was taken on the stopped mount: {w.probes}")
        said = [(t, d) for t, st, _h, d in w.states
                if t >= aside[0] and st == "holding"]
        claims = [(t, d) for t, d in said if "Checking at the science" in d]
        assert not claims, (
            f"the hold claimed to check the sky while the mount was stopped: "
            f"{claims[0][1]!r} at {claims[0][0] - t_h:.1f} s")
        assert said and "the mount is stopped" in said[-1][1] and \
            "zenith keep-out" in said[-1][1], said[-2:]
        refused = [m for m in msgs if "the mount is not on Bravo" in m]
        assert len(refused) == 1, (
            f"the refusal was said {len(refused)} times: {refused[:2]}")
    finally:
        await w.close()


# ------------------------------------------------- (d) a crossing under a dark

async def test_a_flip_point_inside_a_300s_hold_dark_is_met_before_the_dark(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """(d) The plan builds darks during a hold, at the interrupted step's 300 s.
    Alpha's flip point falls 100 s into the first one. The look before the
    dark sees the whole dark ahead: the flip gate waits for the point, the
    goto moves nothing, and the mount stops tracking at the flip point, before
    the dark opens. It is never tracked past its flip point under a dark.

    Mutant "watch only between exposures, no lookahead"
    (`_hold_exposure_ahead_s` returns HOLD_WATCH_S whatever the exposure):
    RED, the dark opens on a tracking mount and the crossing is found only
    when it ends (observed) -
        AssertionError: the flip point was 100.0 s into the hold and the
        mount stopped at 300.0 s, 200.0 s late: dark exposures opened at
        [0.0, 300.0] s
    The same mutant leaves (c) green: a floor met between exposures is the
    ticks' to find, and they still do.
    Mutant "no look before the dark" (the `_hold_watch` call in
    `_hold_darks` deleted): RED, identically (observed) -
        AssertionError: the flip point was 100.0 s into the hold and the
        mount stopped at 300.0 s, 200.0 s late: dark exposures opened at
        [0.0, 300.0] s
    """
    exp = 300.0
    t_h0 = exp
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_h0 + 100.0 + exp + 60.0)
    t_flip = w.t0 + t_h0 + 100.0
    a = _target("Alpha", _flip_ra(t_flip), 20.0, exposure_s=exp)
    try:
        await w.night(_plan(a, darks=3))
        t_h = w.hold_started()
        darks = [t for n, t in w.run.exposure_starts
                 if n.startswith("cloud-hold darks") and t >= t_h]
        assert darks, "premise: the hold shot a dark"
        stops = w.stops(t_h)
        assert stops and stops[0] <= t_flip + WATCH + 0.5, (
            f"the flip point was {t_flip - t_h:.1f} s into the hold and the "
            f"mount stopped at "
            f"{(stops[0] - t_h) if stops else float('nan'):.1f} s, "
            f"{(stops[0] - t_flip) if stops else float('nan'):.1f} s late: "
            f"dark exposures opened at {w.rel(darks, t_h)} s")
        assert stops[0] >= t_flip - engine_mod.FLIP_FRAME_MARGIN_S - WATCH - 0.5
        assert stops[0] <= darks[0] + 0.5, (
            f"the dark opened at {darks[0] - t_h:.1f} s on a tracking mount "
            f"whose flip point was inside it")
    finally:
        await w.close()


# --------------------------------------------------------------- the keep-out

async def test_a_target_rising_into_the_keep_out_mid_hold_stops_tracking(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The zenith keep-out (#101): Alpha rises through a 70 degree ceiling 230
    s into the hold, far from its flip point. The look that first sees it
    inside its interval stops tracking (the hold park-holds; it does not set
    the target aside), publishes that the mount is stopped, and takes no check
    frame while stopped.

    Mutant "keep-out unchecked in the hold" (the ceiling branch of
    `_hold_watch` deleted): RED (observed) -
        AssertionError: Alpha rose into the keep-out 230.0 s into the hold
        and the mount went on tracking it: stops at [] s
    Mutant "a parked hold probes anyway" (the ``_hold_parked`` check before
    the tracking probe in `_hold_for_clear` made False): RED, the probe's
    tracking check finds the stopped mount and resumes it in place, inside
    the keep-out (observed) -
        AssertionError: the mount is tracking again with Alpha in the
        keep-out: tracking turned on at [278.0] s
    """
    t_h0 = EXP
    t_cross_s = t_h0 + 230.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_cross_s + 300.0,
                 safety={"max_alt_deg": 70.0})
    t_cross = w.t0 + t_cross_s
    ra = _crossing_ra(70.0, 45.0, t_cross, rising=True)
    assert _crosses(ra, 45.0, 70.0, t_cross, rising=True), "premise"
    a = _target("Alpha", ra, 45.0)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        stops = w.stops(t_h)
        assert stops and t_cross - WATCH - 0.5 <= stops[0] <= t_cross + 0.5, (
            f"Alpha rose into the keep-out {t_cross - t_h:.1f} s into the "
            f"hold and the mount went on tracking it: stops at "
            f"{w.rel(stops, t_h)} s")
        back_on = [t for t, on, _w in w.run.tracking_calls
                   if on and t > stops[0]]
        assert w.run.tracking() is False, (
            f"the mount is tracking again with Alpha in the keep-out: "
            f"tracking turned on at {w.rel(back_on, t_h)} s")
        assert not [t for t, _tr in w.probes if t > stops[0]], (
            f"a check was taken on the stopped mount: {w.probes}")
        assert w.engine.state.get("state") == "holding", (
            "the keep-out park-holds; it does not end the hold")
        said = [d for t, _s, _h, d in w.states if t >= stops[0]]
        assert said and "zenith keep-out" in said[0], said[:2]
    finally:
        await w.close()


def _parked_for_the_keep_out(sim_hub, *, ha_h: float) -> tuple[SequenceEngine,
                                                              Target]:
    """An engine whose hold has stopped for the keep-out, the mount stopped
    somewhere else, and Alpha at hour angle ``ha_h`` now (dec 45 culminates
    about five degrees from the zenith at the fixture site; the ceiling is
    84 degrees, so Alpha is inside it for about twenty minutes either side
    of transit)."""
    import time
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(name="hold", guide=False, meridian_flip=True,
                          safety_check=False, targets=[])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False, max_alt_deg=84.0))
    a = _target("Alpha", _ra_at(ha_h, time.time()), 45.0)
    e.plan.targets.append(a)
    e._hold_parked = "ceiling"
    e._holding_for_clear = True
    return e, a


async def test_a_hold_stopped_for_the_keep_out_points_back_at_the_target(
        sim_hub, monkeypatch, bus_lines):
    """Out of the keep-out, the hold re-slews to where Alpha is now and tracks
    it. NOT a resume in place: the mount stopped where Alpha entered the
    keep-out, and tracking from there follows that patch of sky up through
    its culmination, which is the keep-out itself.

    Mutant "resume in place" (`_hold_repoint` turns tracking on without the
    slew): RED (observed) -
        AssertionError: the hold turned tracking back on where the mount had
        stopped instead of on Alpha: the mount was left 7.5 deg of RA away
    """
    e, a = _parked_for_the_keep_out(sim_hub, ha_h=+22.0 / 60.0)
    assert e._altitude_limit_verdict(a, projected=True, cfg=e._cfg) is None, (
        "premise: Alpha is out of the keep-out, even for a slew")
    tel = sim_hub.devices["telescope"]
    await tel.slew((a.ra_hours - 0.5) % 24.0, a.dec_deg)    # where it stopped
    await tel.set_tracking(False)
    try:
        await e._hold_watch(a, ahead_s=WATCH)
        assert e._hold_parked is None, "the hold is still stopped"
        off_h = abs(((tel.rig.ra_hours - a.ra_hours) + 12.0) % 24.0 - 12.0)
        assert off_h < 0.05, (
            f"the hold turned tracking back on where the mount had stopped "
            f"instead of on Alpha: the mount was left {off_h * 15.0:.1f} deg "
            f"of RA away")
        assert tel.rig.tracking is True
        assert any("out of the zenith keep-out" in m
                   for _l, m, _s in bus_lines)
    finally:
        e._holding_for_clear = False


async def test_control_a_target_that_a_slew_would_find_in_the_keep_out_stays_stopped(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. The hold stopped for the keep-out on a look that saw Alpha
    coming (a probe's look sees a whole exposure ahead), so Alpha is still
    just short of the ceiling, rising: the next look, one interval ahead,
    finds no limit. The slew gate would: it projects a slew three minutes
    ahead and would raise a SafetyAbort that ends the run. So the hold stays
    stopped and nothing moves.

    Mutant "re-point without the slew gate's projection" (the
    ``projected=True`` check before `_hold_repoint` deleted): RED (observed,
    the fixture target's azimuth elided) -
        astrodeck.sequence.engine.SafetyAbort: target Alpha altitude 84 deg
        above the zenith keep-out 84 deg (az ...) - the mount can reach its
        own tripod up there
    Run again after H3 (T11, #233): GREEN (observed: 1 passed), for the
    reason the control above gives: `_hold_repoint` now catches the gate's
    `SlewRefused` and holds (#240, T7), so the gate refuses the slew the
    pre-ask would have, and the mount stays where it is. That SafetyAbort's
    sentence is words since #233.

    The no-move check allows the stopped simulator's sidereal RA drift
    (WP-51, D-11) and nothing more. MUTANT "the mount moved 0.01 h during the
    hold" (``tel.rig.ra_hours += 0.01`` injected after the watch, a move far
    smaller than any slew): RED (observed) -
        assert 0.009999999999999787 <= 1e-06
    """
    import time
    e, a = _parked_for_the_keep_out(sim_hub, ha_h=0.0)
    # Just short of the 84 degree ceiling, rising: a minute to go.
    lat, d = math.radians(LAT), math.radians(a.dec_deg)
    cos_h = ((math.sin(math.radians(84.0)) - math.sin(lat) * math.sin(d))
             / (math.cos(lat) * math.cos(d)))
    ha_h = math.degrees(math.acos(cos_h)) / 15.0
    a.ra_hours = _ra_at(-ha_h, time.time() + 60.0)
    assert e._altitude_limit_verdict(a, projected=False, cfg=e._cfg) is None
    assert e._hold_limit_ahead(a, WATCH) is None, "premise: clear for a look"
    assert e._altitude_limit_verdict(a, projected=True,
                                     cfg=e._cfg)[0] == "ceiling", (
        "premise: a slew would find it in the keep-out")
    tel = sim_hub.devices["telescope"]
    await tel.set_tracking(False)
    before = (tel.rig.ra_hours, tel.rig.dec_deg)
    t0 = time.time()
    try:
        await e._hold_watch(a, ahead_s=WATCH)
        assert e._hold_parked == "ceiling"
        # "Nothing moves" means no slew. Since WP-51 (#519, backlog ruling
        # D-11) a stopped simulator's RA drifts with the sidereal clock, as a
        # real GEM's does, so RA may move by exactly that drift over the time
        # this took and no more; a slew would move it by degrees, and Dec
        # does not drift at all. (It read exact equality, which Windows'
        # coarse clock happened to keep and the Linux runner did not.)
        # The simulator applies the drift when RA is READ, so read it first
        # and time the bound after the read; the 1e-6 h slack (0.0036 s of
        # RA) only absorbs clock jitter, 10,000 times under the mutant's move.
        ra_after = tel.rig.ra_hours
        drift_h = (time.time() - t0) / 3600.0 * 1.0027379 + 1e-6
        assert abs(ra_after - before[0]) <= drift_h, (
            ra_after, before[0], drift_h)
        assert tel.rig.dec_deg == before[1]
        assert tel.rig.tracking is False
    finally:
        e._holding_for_clear = False


# ------------------------------------------- the watch's own arithmetic (H2)

async def test_a_crossing_in_the_last_interval_of_a_probe_is_met_before_it(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The look before a probe sees `_hold_exposure_ahead_s` of the
    exposure ahead: the exposure, its download (``FLIP_FRAME_MARGIN_S``), and
    one ``HOLD_WATCH_S`` more. Alpha rises into the 70 degree keep-out 75 s
    after that look, inside the last of those intervals. The look acts on it
    at once, the mount is stopped before the shutter would have opened, and
    no check is taken. Every other test here meets its crossing somewhere a
    shorter span would also reach.

    Mutant "drop + HOLD_WATCH_S" (`_hold_exposure_ahead_s` returns the
    exposure plus ``FLIP_FRAME_MARGIN_S`` alone): RED, the check is taken
    and the crossing is met by a tick after it (observed) -
        AssertionError: the look before the check at 120.0 s did not act on
        a crossing 75.0 s ahead, inside its span: checks at [120.0] s, the
        mount stopped at [180.0] s
    """
    t_h0 = EXP
    look_s = t_h0 + PROBE_EVERY
    t_cross_s = look_s + EXP + engine_mod.FLIP_FRAME_MARGIN_S + WATCH / 2.0
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_cross_s + 120.0, safety={"max_alt_deg": 70.0})
    t_cross = w.t0 + t_cross_s
    ra = _crossing_ra(70.0, 45.0, t_cross, rising=True)
    assert _crosses(ra, 45.0, 70.0, t_cross, rising=True), "premise"
    a = _target("Alpha", ra, 45.0)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        t_look = w.t0 + look_s
        assert abs(t_h - (w.t0 + t_h0)) < 1.0, "premise: the hold begins"
        # The span as the docstring of `_hold_exposure_ahead_s` promises it,
        # from the constants, not from the method under test.
        ahead = EXP + engine_mod.FLIP_FRAME_MARGIN_S + WATCH
        assert ahead - WATCH < t_cross - t_look <= ahead, (
            "premise: the crossing is inside the look's last interval")
        stops = w.stops(t_h)
        checks = [t for t, _tr in w.probes if t >= t_h]
        assert stops[:1] and abs(stops[0] - t_look) < 0.5 and not checks, (
            f"the look before the check at {t_look - t_h:.1f} s did not act "
            f"on a crossing {t_cross - t_look:.1f} s ahead, inside its span: "
            f"checks at {w.rel(checks, t_h)} s, the mount stopped at "
            f"{w.rel(stops, t_h)} s")
    finally:
        await w.close()


async def test_the_flip_gate_engages_at_the_look_that_can_see_the_point(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A look hands the flip gate its span less ``FLIP_FRAME_MARGIN_S``,
    because `_maybe_meridian_flip` adds that margin (and the frame loop's
    overhead allowance) itself, for a frame. Alpha's flip point is 80 s into
    the hold. The tick at +30 s sees 30 s ahead, so the gate stays out of it;
    the tick at +60 s is the first whose span, with the overhead allowance,
    holds the point, and the gate waits the last 20 s there.

    Mutant "drop - FLIP_FRAME_MARGIN_S in the lead _hold_flip_watch hands
    _maybe_meridian_flip": RED, the gate engages one look early and waits
    past the look's horizon, the watch taking no look in the meantime
    (observed) -
        AssertionError: the flip gate began waiting at [30.0] s, 50.0 s
        before the flip point: expected the look at 60.0 s, whose span with
        the 12 s overhead allowance first holds it
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch, horizon_s=t_h0 + 300.0)
    t_flip = w.t0 + t_h0 + 80.0
    a = _target("Alpha", _flip_ra(t_flip), 20.0)
    waits: list[float] = []
    real_wait = w.engine._wait_for_flip_point

    async def wait_for_flip_point(target, lead_s=0.0):
        if not w.run.frozen.is_set():
            waits.append(w.run.clock.t)
        return await real_wait(target, lead_s)

    monkeypatch.setattr(w.engine, "_wait_for_flip_point", wait_for_flip_point)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        allowance = float(w.engine._overhead_ema)
        looks = [t_h + i * WATCH for i in range(4)]
        want = next(t for t in looks
                    if t_flip - t <= WATCH + allowance + 1e-6)
        assert waits, "premise: the flip gate waited for the flip point"
        assert abs(waits[0] - want) < 0.5, (
            f"the flip gate began waiting at {w.rel(waits[:1], t_h)} s, "
            f"{t_flip - waits[0]:.1f} s before the flip point: expected the "
            f"look at {want - t_h:.1f} s, whose span with the "
            f"{allowance:.0f} s overhead allowance first holds it")
    finally:
        await w.close()


async def test_a_second_stop_in_one_stopped_spell_does_and_says_nothing(
        sim_hub, monkeypatch, bus_lines):
    """`_hold_park` is once per stopped spell. A hold stopped because its
    mount is not on its target is asked to stop again by a look that sees
    the target rising into the keep-out: no second ``set_tracking(False)``,
    no second line, no second detail. The first reason stands.

    Mutant "remove the once-only guard" (the ``_hold_parked is not None``
    return at the top of `_hold_park` deleted): RED (observed) -
        AssertionError: a second stop in one spell: set_tracking(False) x2,
        lines ['the mount is not on Bravo - stopping tracking; the cloud
        hold goes on but judges no sky until the mount can track the target
        again', "Bravo is rising into the mount's zenith keep-out - stopping
        tracking; the cloud hold goes on but judges no sky until the mount
        can track the target again"], details 2
    """
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(name="p", meridian_flip=False, targets=[])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._holding_for_clear = True
    tel = sim_hub.devices["telescope"]
    stops: list[bool] = []
    real_set = tel.set_tracking

    async def set_tracking(on):
        if not on:
            stops.append(on)
        return await real_set(on)

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    details: list[str] = []
    real_state = e._set_state

    def set_state(**kw):
        if "detail" in kw:
            details.append(kw["detail"])
        return real_state(**kw)

    monkeypatch.setattr(e, "_set_state", set_state)
    try:
        await e._hold_park("elsewhere", "the mount is not on Bravo")
        await e._hold_park("ceiling",
                           "Bravo is rising into the mount's zenith keep-out")
        lines = [m for _l, m, _s in bus_lines if "stopping tracking" in m]
        assert (len(stops), len(lines), len(details)) == (1, 1, 1), (
            f"a second stop in one spell: set_tracking(False) x{len(stops)}, "
            f"lines {lines}, details {len(details)}")
        assert e._hold_parked == "elsewhere"
        assert "not on Bravo" in details[0]
    finally:
        e._holding_for_clear = False


# -------------------------------------------------------------------- controls

async def test_control_a_hold_far_from_every_limit_releases_as_it_always_did(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. Alpha is three hours from its flip point, with no floor or
    keep-out configured, on a mount that goes on tracking. The sky clears 200
    s into the hold. The checks come at the old cadence (a probe every
    CLOUD_PROBE_EVERY_S plus the exposure), the hold releases on the second
    clear one exactly when the old single sleep would have released it, and
    the mount is never stopped.

    Mutant "the probe waits one tick past its interval" (`_hold_watch_ticks`
    counts to ``span_s + HOLD_WATCH_S``): RED (observed) -
        AssertionError: the checks no longer come at the old cadence: [150.0,
        330.0, 510.0] s, expected [120.0, 270.0, 420.0] s
    Mutant "every look stops tracking" (a ``_hold_park("ceiling", ...)``
    made at the top of every look): RED, each look stops the mount and the
    keep-out re-point puts it back, look after look (observed, H2) -
        AssertionError: the mount was stopped during a hold with nothing to
        stop it for: [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0,
        240.0, 270.0, 300.0, 330.0, 360.0, 390.0, 420.0] s
    """
    t_h0 = EXP
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=t_h0 + 600.0, clears_at_s=t_h0 + 200.0)
    a = _target("Alpha", _ra_at(-3.0, w.t0), 40.0)
    try:
        await w.night(_plan(a))
        t_h = w.hold_started()
        released = w.released_at()
        checks = [t for t, _tr in w.probes if t >= t_h]
        stops = [t for t in w.stops(t_h) if released is None or t < released]
        assert not stops, (
            f"the mount was stopped during a hold with nothing to stop it "
            f"for: {w.rel(stops, t_h)} s")
        want = [PROBE_EVERY, 2 * PROBE_EVERY + EXP, 3 * PROBE_EVERY + 2 * EXP]
        assert w.rel(checks[:3], t_h) == want, (
            f"the checks no longer come at the old cadence: "
            f"{w.rel(checks[:3], t_h)} s, expected {want} s")
        assert released is not None and \
            abs((released - t_h) - (want[2] + EXP)) < 0.5, (
                f"released at {released and released - t_h} s, expected "
                f"{want[2] + EXP} s")
        msgs = [m for _l, m, _s in bus_lines]
        assert not [m for m in msgs if "stopping tracking" in m
                    or "stopped tracking during the cloud hold" in m], msgs
    finally:
        await w.close()


def test_the_watch_interval_leaves_the_measured_flip_margin_intact():
    """HOLD_WATCH_S is justified in its comment against the measured 2.4 min
    between the plan's flip point and the AM5's own limit, with more than four
    intervals to spare. Pinned together: change one, re-justify the other.

    Mutant "HOLD_WATCH_S = 60.0": RED (observed) -
        AssertionError: HOLD_WATCH_S is 60 s: 2 intervals fit the measured
        144 s flip margin, and its comment promises more than four
    """
    margin_s = 2.4 * 60.0
    fits = margin_s / engine_mod.HOLD_WATCH_S
    assert fits > 4.0, (
        f"HOLD_WATCH_S is {engine_mod.HOLD_WATCH_S:.0f} s: {fits:.0f} intervals "
        f"fit the measured {margin_s:.0f} s flip margin, and its comment "
        f"promises more than four")
    assert engine_mod.HOLD_WATCH_S <= engine_mod.CLOUD_PROBE_EVERY_S


def test_a_culmination_inside_a_look_is_seen_though_both_ends_are_below(
        sim_hub, monkeypatch):
    """The look before a hold exposure asks the keep-out at every
    HOLD_WATCH_S across the exposure, not only at its two ends
    (`_hold_limit_ahead`): a target is highest at its culmination, and that
    can fall inside an exposure while the exposure's start and end both sit
    under the keep-out. Here a 20-minute hold dark is about to open on a
    target that culminates 5 deg from the zenith half way through it, under
    a keep-out drawn between its culmination and the higher of the two ends,
    so only a look inside the span sees the crossing. Asked of the method
    itself, on engine.py's clock pinned to one instant: the whole-night cases
    above meet every crossing at an end of some look, so none of them can
    tell the samples from the ends.

    Mutant "the two ends alone" (`_hold_limit_ahead` loops
    ``for i in (0, steps):`` in place of ``range(steps + 1)``): RED
    (observed) -
        AssertionError: a culmination inside a 1260 s look was not seen:
        None, not the keep-out
    CONTROL: the same target culminating well after the look ends is inside
    every limit for the whole look, so the samples add no verdict of their
    own. Nothing here prints an altitude: the premises are yes-or-no.
    """
    t_now = time.time()

    class _Pinned:
        """engine.py's ``time`` with ``time()`` held at one instant."""
        def time(self) -> float:
            return t_now

        def __getattr__(self, name):
            return getattr(time, name)

    monkeypatch.setattr(engine_mod, "time", _Pinned())
    ahead = SequenceEngine._hold_exposure_ahead_s(1200.0)
    assert ahead > 20 * WATCH, "premise: a look with many samples in it"
    dec = LAT + 5.0

    def alt(t: Target, when: float) -> float:
        return altaz(t.ra_hours, t.dec_deg, LAT, LON, when)[0]

    inside = _target("Inside", _ra_at(0.0, t_now + ahead / 2.0), dec)
    top = alt(inside, t_now + ahead / 2.0)
    ends = max(alt(inside, t_now), alt(inside, t_now + ahead))
    assert top - ends > 0.3, (
        "premise: the culmination stands clear above both ends of the look")
    keep_out = (top + ends) / 2.0
    e = SequenceEngine(sim_hub)
    e._cfg = AppConfig(safety=SafetyConfig(enabled=True, max_alt_deg=keep_out))

    got = e._hold_limit_ahead(inside, ahead)
    assert (got[0] if got else None) == "ceiling", (
        f"a culmination inside a {ahead:.0f} s look was not seen: "
        f"{got[0] if got else None}, not the keep-out")

    later = _target("Later", _ra_at(0.0, t_now + 3.0 * ahead), dec)
    assert all(alt(later, t_now + f * ahead) < keep_out
               for f in (0.0, 0.25, 0.5, 0.75, 1.0)), (
        "premise: the control target stays under the keep-out all look long")
    assert e._hold_limit_ahead(later, ahead) is None


async def test_a_repoint_from_another_target_makes_the_mount_the_held_targets(
        sim_hub, monkeypatch):
    """(c)'s re-point, the bookkeeping half. A hold opened by a target
    setup's pre-slew gate finds the mount on the LAST target, and points it
    at the held one (`_hold_repoint` with ``elsewhere``). From then on the
    mount is tracking the held target, and the engine must say so:
    ``_tracked_target`` is what tells the hold's open whether the mount is
    elsewhere (#224), and what the idle park-hold watches if this hold ends
    with the target set aside or its window closed. Left on the old target,
    an idle spell after the hold would watch the floor and flip point of a
    target the mount is not on (the #202 class). The idle clock starts at
    the re-point, as it does after a setup.

    Asked of the method itself, on the sim mount: (c) above runs the whole
    night, but nothing after its one re-point reads the bookkeeping, so it
    stays green without it.

    Mutant "the re-point leaves the old target tracked" (the
    ``self._tracked_target = target`` that follows any successful re-point
    deleted): RED (observed) -
        AssertionError: after pointing the mount at Bravo the engine still
        says it is tracking Alpha
    Mutant "no idle anchor at the re-point" (``self._idle_since =
    time.time()`` deleted from the ``elsewhere`` branch): RED (observed) -
        AssertionError: the idle clock did not start at the re-point
    """
    e = SequenceEngine(sim_hub)
    alpha = _target("Alpha", 5.0, 20.0)
    bravo = _target("Bravo", 7.0, 40.0)
    e.plan = SequencePlan(name="r", meridian_flip=False, targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha
    e._idle_since = 1.0
    e._hold_parked = None
    tel = sim_hub.devices["telescope"]
    await tel.set_tracking(False)

    await e._hold_repoint(bravo, elsewhere=True)

    assert tel.rig.tracking is True, "premise: the re-point tracks again"
    assert (round(tel.rig.ra_hours, 2), round(tel.rig.dec_deg, 1)) == (
        7.0, 40.0), "premise: the mount was pointed at Bravo"
    assert e._tracked_target is bravo, (
        f"after pointing the mount at Bravo the engine still says it is "
        f"tracking {getattr(e._tracked_target, 'name', None)}")
    assert e._idle_since > 1.0, "the idle clock did not start at the re-point"


async def test_a_calibration_hold_asks_the_mount_nothing(sim_hub, monkeypatch):
    """A hold with no target, or a calibration target, has nothing tracking:
    a look is a no-op and asks the mount nothing.

    Mutant "no calibration guard" (the first line of `_hold_watch` checks
    only ``target is None``): RED (observed) -
        AssertionError: a look for a calibration target asked the mount
        ['time_to_meridian_flip']
    """
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(name="c", meridian_flip=True, targets=[])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._flip_armed = True
    tel = sim_hub.devices["telescope"]
    asked: list[str] = []
    real_ttmf = tel.time_to_meridian_flip

    async def ttmf():
        asked.append("time_to_meridian_flip")
        return await real_ttmf()

    monkeypatch.setattr(tel, "time_to_meridian_flip", ttmf)
    cal = Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                 steps=[ExposureStep(exposure_s=1.0, count=1,
                                     frame_type="Dark")])
    await e._hold_watch(None, ahead_s=WATCH)
    await e._hold_watch(cal, ahead_s=WATCH)
    assert asked == [], (
        f"a look for a calibration target asked the mount {asked}")
    assert e._hold_parked is None
