"""A run's end completes the idle stop it decided (#247; H3 orchestrator
ruling 5, spec "Still waiting on the owner" item 14).

The idle watch decides to stop tracking a mount nobody is watching (#165),
and the stop is made by a task of its own (`_idle_stop_retry`, #216): the
first attempt stops the guider and then the mount, reads the stop back, and
asks again about once a minute while the mount does not confirm it. The end
of a run used to CANCEL that task, as `_setup_target` does. A setup cancels
because it is about to turn tracking on itself; nothing does after a run. So
a run that ended while the first attempt was still stopping the guider never
sent ``set_tracking(False)``, and with ``park_when_done`` off, the default,
the mount tracked on, unwatched, after the run: the hazard the stop had been
decided for. The same for an operator's Abort, whose cancel reaches the run's
end, and for `abort`'s own call.

Now the end COMPLETES it (`_finish_idle_stop`): a first attempt in flight
is let finish, up to ``IDLE_STOP_FINISH_S`` (120 s + 2 x 30 s, the bounds it
awaits), shielded from a cancel landing meanwhile; a stop still unconfirmed,
or a first attempt wedged past that bound, is asked once more, bounded, and
read back; and a stop still unconfirmed then, or a wedged first attempt, is
said once. Nothing asks after the run.

ONLY FOR AN ENDING THAT DOES NOT PARK, since #270 (S2 orchestrator ruling 1,
which refines ruling 5's "whether or not the wind-down parks"). Every case
here is one: a natural end with ``park_when_done`` off, an operator's Abort,
a failure. An ending that parks hands the stop to its park and waits for
none of this; that is test_unsafe_ending_parks_at_once.py. Until the ruling
this file also held a natural end that parks, and an unsafe ending, to the
completion; both now hold the opposite there.

THE HARNESS is test_idle_park_hold's clocked simulator. The first attempt's
guider stop is parked on an event the test sets (`park_until`), counted as
parked so the clock goes on for the run. The run ends by itself: Alpha
shoots, Bravo waits below its altitude gate until its stop time closes its
window, a dawn cutoff, and the idle clock decides the stop two minutes after
Alpha's last exposure, well before that. Where a test must act at an exact
fake instant (release the guider, abort) it stops the clock first
(`hold_clock`), from a spy on the run task: the end of `_run_scheduled`, or
the guider stop being asked. The site is a fixture, never the real one.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    HANG, TEARDOWN, _a_mount_that_will_not_stop, _asks, _Clocked, _hhmm,
    _park_lines, _plan, _ra_at, _t0_at_second, _target, _unconfirmed_lines,
    sim_hub, temp_store)

pytestmark = pytest.mark.asyncio

#: The second of the minute the fake night starts on (`_t0_at_second`), so
#: Bravo's minute-resolution stop time closes its window at one fixed fake
#: instant: +587.5 s. Clear of every tie band `test_idle_park_hold` found.
SECOND = 12.5
FINISH = engine_mod.IDLE_STOP_FINISH_S
POLL = engine_mod.IDLE_STOP_FINISH_POLL_S
#: The unconfirmed-at-the-end warning, in the words `_complete_idle_stop`
#: uses for each half.
UNCONFIRMED = "has still not confirmed it"
WEDGED = "had not finished its first attempt"


class _Night:
    """One run on the clocked simulator that ends at Bravo's stop time, with
    the records these cases read: every ``set_tracking`` (the harness's),
    every ``get_tracking`` as (fake time, which task, index into
    ``tracking_calls``), the park, the end of `_run_scheduled` and the start
    of the wind-down, each as (index into ``tracking_calls``, fake time)."""

    def __init__(self, hub, monkeypatch, *, park: bool = False,
                 hold_at_end: bool = False):
        self.run = _Clocked(hub, monkeypatch, horizon_s=4 * 3600.0,
                            t0=_t0_at_second(SECOND))
        run = self.run
        t0 = run.t0
        self.alpha = _target("Alpha", _ra_at(-3.0, t0), 20.0)
        self.bravo = _target("Bravo", _ra_at(+4.0, t0), 0.0,
                             min_altitude_deg=30.0, start_mode="time",
                             start_time=_hhmm(t0 - 2 * 3600),
                             stop_mode="time", stop_time=_hhmm(t0 + 600))
        self.plan = _plan(self.alpha, self.bravo)
        self.plan.park_when_done = park
        self.ends: list[tuple[int, float]] = []
        self.wound: list[tuple[int, float]] = []
        self.parks: list[int] = []
        self.reads: list[tuple[float, str, int]] = []
        #: set by the end of `_run_scheduled` when ``hold_at_end``: the
        #: clock is stopped there until the test sets ``self.resume``
        self.resume: asyncio.Event | None = None
        self.at_end = asyncio.Event()
        engine = run.engine
        tel = hub.devices["telescope"]

        real_scheduled = engine._run_scheduled

        async def run_scheduled(plan):
            try:
                return await real_scheduled(plan)
            finally:
                self.ends.append((len(run.tracking_calls), run.clock.t))
                if hold_at_end:
                    self.resume = run.hold_clock()
                self.at_end.set()

        real_wind = engine._wind_down

        async def wind_down(*a, **kw):
            self.wound.append((len(run.tracking_calls), run.clock.t))
            return await real_wind(*a, **kw)

        inner_get = tel.get_tracking

        async def get_tracking():
            if not run.frozen.is_set():
                self.reads.append((run.clock.t, run.who(),
                                   len(run.tracking_calls)))
            return await inner_get()

        inner_park = tel.park

        async def park_():
            self.parks.append(len(run.tracking_calls))
            return await inner_park()

        monkeypatch.setattr(engine, "_run_scheduled", run_scheduled)
        monkeypatch.setattr(engine, "_wind_down", wind_down)
        monkeypatch.setattr(tel, "get_tracking", get_tracking)
        monkeypatch.setattr(tel, "park", park_)

    def guider_parked_on(self, gate: asyncio.Event, monkeypatch, *,
                         hold_on_entry: bool = False) -> list[float]:
        """The idle-stop task's guider stop parks on ``gate`` (the first
        attempt, the only guider stop that task makes) and then stops the
        guider for real. Every other caller, and anything after the
        horizon, gets the real stop at once. ``hold_on_entry`` stops the
        clock the moment the stop is asked, until ``self.resume`` is set.
        Returns the fake time of every parked stop."""
        run = self.run
        guider = run.hub.guider
        assert guider is not None and guider.connected, (
            "premise: the sim rig has a connected guider for `_park_hold` to "
            "stop")
        asked: list[float] = []
        real_stop = guider.stop_guiding

        async def stop_guiding(*a, **kw):
            if run.frozen.is_set() or run.who() != "retry":
                return await real_stop(*a, **kw)
            asked.append(run.clock.t)
            if hold_on_entry:
                self.resume = run.hold_clock()
            await run.park_until(gate)
            return await real_stop(*a, **kw)

        monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
        return asked

    async def until(self, cond, what: str, timeout: float = 60.0) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not cond():
            assert loop.time() < end, f"premise: {what} within {timeout} s"
            await self.run._real_sleep(0.005)

    def offs_after(self, i: int) -> list[tuple[float, str]]:
        """(fake seconds after the run's end, which task) of every
        ``set_tracking(False)`` from index ``i`` on."""
        _i, ended = self.ends[0]
        return [(round(t - ended, 2), who)
                for t, on, who in self.run.tracking_calls[i:] if not on]


# ------------------------------------------------ the first attempt in flight

async def test_a_run_that_ends_mid_first_attempt_lets_the_stop_land(
        sim_hub, monkeypatch, bus_lines):
    """The idle clock decides the stop two minutes after Alpha's last
    exposure, and the first attempt's guider stop does not come back. The
    run then ends by itself at Bravo's stop time, the guider stop still out,
    with ``park_when_done`` off. At that instant the clock stops, the guider
    stop comes back, and the clock goes on: the run's end must let the first
    attempt finish, so ``set_tracking(False)`` is sent by the idle-stop task
    and read back, and the mount is not tracking when the run is over.

    AMENDED TO S2 ORCHESTRATOR RULING 1 (#270). This test had a second case,
    the same night with ``park_when_done`` on, which held that the stop
    landed before the park. Under the ruling that ending parks at once and
    does not wait for the first attempt:
    test_unsafe_ending_parks_at_once's
    `test_a_natural_end_that_parks_parks_at_once`, which holds the opposite.

    Mutant "cancel instead of complete" (`_finish_idle_stop`'s body replaced
    by ``await self._cancel_idle_stop_retry()``, H2's end of run): RED
    (observed) -
        AssertionError: the run's end abandoned the stop its idle watch
        decided: no set_tracking(False) after the run ended (at [] s), and
        the mount is still tracking
    """
    park = False
    n = _Night(sim_hub, monkeypatch, park=park, hold_at_end=True)
    run = n.run
    gate = asyncio.Event()
    asked = n.guider_parked_on(gate, monkeypatch)
    run.engine.start(n.plan)
    try:
        await n.until(n.at_end.is_set, "the run reached its end")
        i_end, ended = n.ends[0]
        idle = run.exposure_end("Alpha")
        assert asked and TEARDOWN <= asked[0] - idle < ended - idle, (
            f"premise: the idle clock decided the stop, and its guider stop "
            f"was asked before the run ended: asked at "
            f"{run.rel(asked, idle)} s after Alpha, the run ended at "
            f"{ended - idle:.1f} s")
        assert not gate.is_set() and n.offs_after(0) == [], (
            f"premise: the first attempt was still stopping the guider when "
            f"the run ended; set_tracking(False) at {n.offs_after(0)} s")
        gate.set()                         # the guider stop comes back
        n.resume.set()                     # and the clock goes on
        await n.until(lambda: not run.engine.running, "the run ended")
        assert run.engine.state.get("end_reason") == "dawn_cutoff", (
            f"premise: Bravo's closed window ended it: {run.engine.state}")
        offs = n.offs_after(i_end)
        assert offs and offs[0][1] == "retry", (
            f"the run's end abandoned the stop its idle watch decided: no "
            f"set_tracking(False) after the run ended (at {offs} s), and the "
            f"mount is {'still' if run.tracking() else 'not'} tracking")
        assert len(offs) == 1, (
            f"the first attempt confirmed its stop, and something asked "
            f"again: {offs}")
        i_off = next(i for i, (_t, on, who) in enumerate(run.tracking_calls)
                     if i >= i_end and not on)
        read_back = [who for _t, who, i in n.reads if i > i_off]
        assert read_back and read_back[0] == "retry", (
            f"the stop was not read back after it was sent: reads "
            f"{[(round(t - ended, 2), w, i) for t, w, i in n.reads[-3:]]}")
        assert n.wound and n.wound[0][0] > i_off, (
            f"the wind-down began before the stop was sent: {n.wound}")
        assert n.parks == [], f"premise: no park: {n.parks}"
        assert run.tracking() is False
        assert run.engine._idle_stop_task is None
        said = [m for lvl, m, _s in bus_lines if lvl == "warning"
                and (UNCONFIRMED in m or WEDGED in m)]
        assert said == [], said
    finally:
        gate.set()
        if n.resume is not None:
            n.resume.set()
        await run.close()


@pytest.mark.parametrize("when", ["during the wait", "during the run's end"])
async def test_an_abort_still_sends_the_stop(sim_hub, monkeypatch, bus_lines,
                                             when):
    """The same stop in flight, under an operator's Abort, which cancels the
    run task. Two instants. DURING THE WAIT: the abort lands while the run
    waits on Bravo, the moment the guider stop is asked, and it is what ends
    the run. DURING THE RUN'S END: the run has ended by itself and is waiting
    for the first attempt when the abort's cancel lands in that wait. Either
    way the guider stop then comes back and the stop must still be sent, by
    the idle-stop task, before the abort returns.

    Mutant "cancel instead of complete" (as above): RED, both (observed) -
        [during the wait] AssertionError: the abort (during the wait)
        abandoned the stop the idle watch decided: set_tracking(False) after
        the run's end at [] s, and the mount is still tracking
        [during the run's end] AssertionError: the abort (during the run's
        end) abandoned the stop the idle watch decided: set_tracking(False)
        after the run's end at [] s, and the mount is still tracking
    Mutant "no shield" (`_poll_until` in `_complete_idle_stop` lets a
    CancelledError out of its sleep instead of noting it): RED during the
    run's end only, where the cancel lands in that sleep (observed) -
        AssertionError: the abort (during the run's end) abandoned the stop
        the idle watch decided: set_tracking(False) after the run's end at []
        s, and the mount is still tracking
    and green during the wait, where the cancel has already been spent on
    the wait before the run's end begins.
    """
    n = _Night(sim_hub, monkeypatch, hold_at_end=(when != "during the wait"))
    run = n.run
    gate = asyncio.Event()
    asked = n.guider_parked_on(gate, monkeypatch,
                               hold_on_entry=(when == "during the wait"))
    run.engine.start(n.plan)
    aborting = None
    try:
        if when == "during the wait":
            await n.until(lambda: bool(asked), "the guider stop was asked")
            assert not n.ends, "premise: the run is still waiting on Bravo"
            aborting = asyncio.ensure_future(run.engine.abort())
            await n.until(n.at_end.is_set, "the abort ended the scheduler")
        else:
            await n.until(n.at_end.is_set, "the run reached its end")
            assert asked and not gate.is_set(), (
                "premise: the first attempt was still out when the run ended")
            aborting = asyncio.ensure_future(run.engine.abort())
            for _ in range(20):            # let the cancel land in the wait
                await run._real_sleep(0)
        i_end, ended = n.ends[0]
        gate.set()
        n.resume.set()
        await asyncio.wait_for(aborting, 60.0)
        offs = n.offs_after(i_end)
        assert [w for _t, w in offs] == ["retry"], (
            f"the abort ({when}) abandoned the stop the idle watch decided: "
            f"set_tracking(False) after the run's end at {offs} s, and the "
            f"mount is {'still' if run.tracking() else 'not'} tracking")
        assert run.engine.state.get("end_reason") == "aborted", (
            f"premise: the operator's abort ended it: {run.engine.state}")
        assert run.tracking() is False
        assert run.engine._idle_stop_task is None
    finally:
        gate.set()
        if n.resume is not None:
            n.resume.set()
        if aborting is not None and not aborting.done():
            await asyncio.gather(aborting, return_exceptions=True)
        await run.close()


async def test_an_abort_in_the_run_s_end_does_not_replace_the_ending(
        sim_hub, monkeypatch, bus_lines):
    """A quality stop ends the run, with ``park_when_done`` off, while the
    first attempt's guider stop is still out: the gate in Bravo's wait raises
    NightQualityStop (the per-night reject guard's exception, raised here by
    a double of the gate). So the run's end is completing the stop with that
    exception already under way, and the operator's Abort lands in that
    wait. The shield notes the cancel and the stop is still sent; but the
    cancel must not then be raised OVER the exception already ending the
    run, whose own handler ends it: the quality stop's wind-down, and the
    report saying "quality". Raised over it, the run takes the Abort's
    teardown instead, `_safe_stop`, and the report says "aborted" about a
    night the reject guard ended.

    AMENDED TO S2 ORCHESTRATOR RULING 1 (#270). The exception here was a
    SafetyAbort until the ruling, whose handler parks and closes the roof
    under a shield (§1.9-G). An unsafe ending no longer waits for the idle
    stop at all, so no Abort can land in this wait with a SafetyAbort under
    way (test_unsafe_ending_parks_at_once.py); the ending that does not park
    carries the same rule, and this holds it there.

    Mutant "the noted cancel is raised over the exception already ending the
    run" (`_finish_idle_stop` raises CancelledError whenever a cancel landed,
    ``already_ending`` ignored; the code as it first stood): RED (observed) -
        AssertionError: an Abort landing in the run's end replaced the quality
        stop already ending it: end_reason 'aborted', so its own wind-down
        never ran
    THE CONTROL is `test_an_abort_still_sends_the_stop` [during the run's
    end]: with nothing else ending the run, the noted cancel IS raised, and
    the run ends aborted. Mutant "never raised" (the cancel always dropped),
    and mutant "always already ending" (`_run` passes
    ``already_ending=True`` whatever ended the spell): RED there, both
    (observed) -
        AssertionError: premise: the operator's abort ended it: {'state':
        'complete', ... 'detail': 'stopped at dawn (windows closed)', ...
        'end_reason': 'dawn_cutoff'}
    """
    n = _Night(sim_hub, monkeypatch, hold_at_end=True)
    run = n.run
    engine = run.engine
    gate = asyncio.Event()
    asked = n.guider_parked_on(gate, monkeypatch)
    real_safety_gate = engine._safety_gate

    async def safety_gate(*a, **kw):
        if asked and not run.frozen.is_set():
            raise engine_mod.NightQualityStop("consecutive rejects")
        return await real_safety_gate(*a, **kw)

    monkeypatch.setattr(engine, "_safety_gate", safety_gate)
    engine.start(n.plan)
    aborting = None
    try:
        await n.until(n.at_end.is_set, "the quality stop ended the scheduler")
        i_end, _ended = n.ends[0]
        assert asked and not gate.is_set() and n.wound == [], (
            "premise: the first attempt was still out, and nothing had wound "
            "down, when the quality stop reached the run's end")
        aborting = asyncio.ensure_future(engine.abort())
        for _ in range(20):                # let the cancel land in the wait
            await run._real_sleep(0)
        gate.set()
        n.resume.set()
        await asyncio.wait_for(aborting, 60.0)
        offs = n.offs_after(i_end)
        assert offs and offs[0][1] == "retry", (
            f"premise: the stop was still sent by the idle-stop task: {offs}")
        reason = engine.state.get("end_reason")
        assert reason == "quality" and n.wound, (
            f"an Abort landing in the run's end replaced the quality stop "
            f"already ending it: end_reason {reason!r}, so its own wind-down "
            f"never ran")
        i_off = next(i for i, (_t, on, who) in enumerate(run.tracking_calls)
                     if i >= i_end and not on)
        assert n.wound[0][0] > i_off, (
            f"the wind-down began before the idle stop was sent: "
            f"{n.wound}, the stop at {i_off}")
        assert n.parks == [], f"premise: this plan does not park: {n.parks}"
        assert engine._idle_stop_task is None
    finally:
        gate.set()
        if n.resume is not None:
            n.resume.set()
        if aborting is not None and not aborting.done():
            await asyncio.gather(aborting, return_exceptions=True)
        await run.close()


async def test_control_a_failure_still_completes_the_stop(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL for #305 and #311 (S3), which changed only the wind-down's
    park: a failure still completes the stop (H3 orchestrator ruling 5). The
    scheduler raises while the first attempt's guider stop is out, on a plan
    that DOES park when done. A failure does not park (`_safe_stop` touches no
    mount), so it hands nothing to a park: the run's end lets the first
    attempt finish, the idle-stop task sends the stop, and no park is asked.
    Its sibling for an operator's Abort mid-run is
    `test_an_abort_still_sends_the_stop` [during the wait].

    Mutant "every ending parks" (`_ending_parks` answers True for any
    exception): RED (observed) -
        AssertionError: the failure handed the idle stop to a park that never
        came: set_tracking(False) after the run's end at [] s, parks [], and
        the mount is still tracking
    """
    n = _Night(sim_hub, monkeypatch, park=True, hold_at_end=True)
    run = n.run
    engine = run.engine
    gate = asyncio.Event()
    asked = n.guider_parked_on(gate, monkeypatch)
    real_safety_gate = engine._safety_gate

    async def safety_gate(*a, **kw):
        if asked and not run.frozen.is_set():
            raise RuntimeError("the scheduler fell over")
        return await real_safety_gate(*a, **kw)

    monkeypatch.setattr(engine, "_safety_gate", safety_gate)
    engine.start(n.plan)
    try:
        await n.until(n.at_end.is_set, "the failure ended the scheduler")
        i_end, _ended = n.ends[0]
        assert asked and not gate.is_set() and n.wound == [], (
            "premise: the first attempt was still out when the failure "
            "reached the run's end")
        gate.set()
        n.resume.set()
        await n.until(lambda: not engine.running, "the run ended")
        assert engine.state.get("state") == "error", (
            f"premise: the failure ended it: {engine.state}")
        offs = n.offs_after(i_end)
        assert [w for _t, w in offs] == ["retry"] and n.parks == [], (
            f"the failure handed the idle stop to a park that never came: "
            f"set_tracking(False) after the run's end at {offs} s, parks "
            f"{n.parks}, and the mount is "
            f"{'still' if run.tracking() else 'not'} tracking")
        assert run.tracking() is False
        assert engine._idle_stop_task is None
    finally:
        gate.set()
        if n.resume is not None:
            n.resume.set()
        await run.close()


# ------------------------------------------------ what the end asks and says

async def test_an_unconfirmed_stop_is_asked_once_more_and_said_once(
        sim_hub, monkeypatch, bus_lines):
    """A dead link, the #133 class: ``set_tracking(False)`` hangs for its
    whole bound and times out, and every read of the mount hangs and answers
    nothing. The first attempt is over long before the run ends, unconfirmed,
    and the task is retrying. At the run's end: exactly one more stop, made
    by the run itself, bounded (one ``MOUNT_QUERY_TIMEOUT_S`` for the ask and
    one for the read-back, not a retry interval), and one warning that says
    the stop is still unconfirmed and why; the task's own unconfirmed-stop
    warning is still said once for the spell. Nothing asks after it.

    Mutant "no ask at the end" (the one more `_stop_tracking_quietly` in
    `_complete_idle_stop` deleted): RED (observed) -
        AssertionError: the run's end did not ask the unconfirmed stop exactly
        once more: set_tracking(False) after the end at [] s
    Mutant "cancel instead of complete" (as above): RED (observed) -
        AssertionError: the run's end did not ask the unconfirmed stop exactly
        once more: set_tracking(False) after the end at [] s
    """
    n = _Night(sim_hub, monkeypatch)
    run = n.run
    _a_mount_that_will_not_stop(run, readback="unreadable")
    run.engine.start(n.plan)
    try:
        await n.until(lambda: not run.engine.running, "the run ended")
        assert run.engine.state.get("end_reason") == "dawn_cutoff", (
            f"premise: Bravo's closed window ended it: {run.engine.state}")
        i_end, ended = n.ends[0]
        retried = [t for t, who in _asks(run) if who == "retry" and t < ended]
        assert len(retried) >= 2, (
            f"premise: the first attempt and at least one retry before the "
            f"end: {run.rel(retried, run.t0)} s")
        offs = n.offs_after(i_end)
        assert [w for _t, w in offs] == ["run"], (
            f"the run's end did not ask the unconfirmed stop exactly once "
            f"more: set_tracking(False) after the end at {offs} s")
        took = n.wound[0][1] - ended
        assert took <= 2 * HANG + 2 * POLL, (
            f"the run's end took {took:.1f} s over one more ask and its "
            f"read-back, each bounded at {HANG:.0f} s")
        said = [m for lvl, m, _s in bus_lines
                if lvl == "warning" and UNCONFIRMED in m]
        assert len(said) == 1 and "cannot be read" in said[0], said
        assert len(_unconfirmed_lines(bus_lines)) == 1, (
            _unconfirmed_lines(bus_lines))
        mark = len(run.tracking_calls)
        await run._real_sleep(0.3)
        after = [(t, who) for t, on, who in run.tracking_calls[mark:]
                 if not on]
        assert after == [] and run.engine._idle_stop_task is None, after
    finally:
        await run.close()


async def test_a_guider_wedged_past_the_bound_still_ends_the_run(
        sim_hub, monkeypatch, bus_lines):
    """The first attempt's guider stop never comes back, past
    ``IDLE_STOP_FINISH_S`` and past `_park_hold`'s own bound, as a stop that
    eats its cancels can. The run still ends: its end gives the first attempt
    the whole bound and no more, asks the mount once itself (it takes the
    stop), and says the one line.

    Mutant "no bound" (`_complete_idle_stop`'s first wait given an infinite
    bound): RED (observed) -
        AssertionError: a wedged guider held the run's end: the run is still
        going at fake +14400 s, 'running': 'waiting for Bravo'
        (the night's horizon; the state is the last one the scheduler set.)
        Mutant "no ask at the end" (as above): RED (observed) -
            AssertionError: the run's end cut the wedged first attempt at its
            bound and did not ask the mount itself: set_tracking(False) after
            the end at [] s, and the mount is still tracking
        Mutant "cancel instead of complete" (as above): RED (observed) -
            AssertionError: the run's end waited 0.0 s for the first attempt;
            its bound is 180 s
    """
    n = _Night(sim_hub, monkeypatch)
    run = n.run
    gate = asyncio.Event()                 # never set during the run
    asked = n.guider_parked_on(gate, monkeypatch)
    run.engine.start(n.plan)
    try:
        await n.until(lambda: not run.engine.running or run.frozen.is_set(),
                      "the run ended or the night ran out")
        assert not run.engine.running, (
            f"a wedged guider held the run's end: the run is still going at "
            f"fake +{run.clock.t - run.t0:.0f} s, "
            f"{run.engine.state.get('state')!r}: "
            f"{run.engine.state.get('detail')!r}")
        assert asked, "premise: the guider stop was asked"
        i_end, ended = n.ends[0]
        took = n.wound[0][1] - ended
        assert FINISH <= took <= FINISH + 10.0, (
            f"the run's end waited {took:.1f} s for the first attempt; its "
            f"bound is {FINISH:.0f} s")
        offs = n.offs_after(i_end)
        assert [w for _t, w in offs] == ["run"], (
            f"the run's end cut the wedged first attempt at its bound and did "
            f"not ask the mount itself: set_tracking(False) after the end at "
            f"{offs} s, and the mount is "
            f"{'still' if run.tracking() else 'not'} tracking")
        assert run.tracking() is False
        said = [m for lvl, m, _s in bus_lines
                if lvl == "warning" and WEDGED in m]
        assert len(said) == 1 and "stopped tracking" in said[0], said
    finally:
        gate.set()
        await run.close()


# -------------------------------------------------------------------- control

async def test_control_with_no_stop_decided_the_end_asks_nothing(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. Bravo's window closes a minute in, before the idle clock can
    run out, so no stop was ever decided. With ``park_when_done`` off the
    run's end sends no ``set_tracking`` at all, and the mount the operator
    left tracking Alpha is still tracking: completing a stop is not making
    one up.

    Mutant "every run end stops the mount" (`_finish_idle_stop` asks
    `_stop_tracking_quietly` when it finds no task): RED (observed) -
        AssertionError: the run's end stopped a mount no idle watch had
        decided to stop: set_tracking(False) at [(20.0, 'run')] s after Alpha
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=4 * 3600.0,
                   t0=_t0_at_second(SECOND))
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _target("Bravo", _ra_at(+4.0, t0), 0.0, min_altitude_deg=30.0,
                start_mode="time", start_time=_hhmm(t0 - 2 * 3600),
                stop_mode="time", stop_time=_hhmm(t0 + 90))
    run.engine.start(_plan(a, b))
    loop = asyncio.get_running_loop()
    end = loop.time() + 60.0
    while run.engine.running and loop.time() < end:
        await run._real_sleep(0.01)
    try:
        assert not run.engine.running and not run.frozen.is_set(), (
            f"premise: the run ended by itself: {run.engine.state}")
        idle = run.exposure_end("Alpha")
        assert run.clock.t - idle < TEARDOWN, (
            f"premise: it ended before the idle clock ran out "
            f"({run.clock.t - idle:.1f} s after Alpha)")
        assert _park_lines(bus_lines) == [], "premise: no stop was decided"
        offs = [(round(t - idle, 1), who) for t, on, who in run.tracking_calls
                if not on]
        assert offs == [], (
            f"the run's end stopped a mount no idle watch had decided to "
            f"stop: set_tracking(False) at {offs} s after Alpha")
        assert run.tracking() is True
    finally:
        await run.close()
