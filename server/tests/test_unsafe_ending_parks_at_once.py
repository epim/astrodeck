"""An unsafe or parking ending never waits for the idle stop (#270, S2
orchestrator ruling 1, which refines #247, H3 orchestrator ruling 5).

H3 made the end of a run COMPLETE a stop the idle watch had decided
(`_finish_idle_stop`), "whether or not the wind-down parks": a first attempt
still stopping the guider was let finish for up to ``IDLE_STOP_FINISH_S``
(120 s + 2 x 30 s), then the mount was asked once more and read back. That
ran in the spell's ``finally``, ahead of the SafetyAbort handler that
publishes ``aborted / unsafe`` and parks the mount and closes the roof under
a shield (§1.9-G). So with a guider that had stopped answering, the unsafe
wind-down began up to about 270 s late, in the rain, with the published state
still "running"; and the wind-down then stopped the guider again, under its
own ``GUIDE_OP_TIMEOUT_S``, before it parked. A safety completion placed on
the critical path of a more urgent safety action.

THE RULING. A park fulfils the stop: a parked mount does not track. An ending
that parks (every unsafe one, and any other whose plan parks when done)
cancels the idle-stop task without awaiting it, fenced, and asks the park at
once (`_hand_idle_stop_to_the_park`). The wind-down starts its own guider stop
first and reaps it, bounded, after the park and the roof close, so nothing
but the park stands between the ending and the roof. A guider stop that eats
the cancel (the #235 shape) sends no ``set_tracking(False)`` once the park is
asked. A park that fails or times out is a wind-down that did not park: after
the roof-close attempt the mount is asked, once and bounded, to stop tracking,
read back, and the outcome said in words. Every ending that does not park
keeps H3's completion; those cases, the controls, are
test_run_end_completes_the_idle_stop.py's.

THE HARNESS is that file's `_Night` on the clocked simulator: Alpha shoots,
the idle clock decides the stop two minutes after Alpha's last exposure, and
the first attempt's guider stop is parked on an event the test does not set
while the run lasts (`park_until`). Here the rig has a safety monitor whose
reading is a script on the fake clock: safe, then rain from ``RAIN_AT``, in
Bravo's wait and long after the stop was decided. ``on_unsafe`` is ``park``,
the abort that does not warm, so no cooler ramp is on the clock. The unsafe
wind-down runs on a task of its own, which the harness is taught to clock
(`_clock_the_wind_down`). The site is a fixture, never the real one.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.roof as roof_mod
from astrodeck.config import SafetyConfig
from astrodeck.devices.base import DeviceError, DomeShutterState, SafetyReading
from astrodeck.sequence.engine import (NightQualityStop, SafetyAbort,
                                       SequenceEngine, SlewRefused)

from test_idle_park_hold import TEARDOWN, sim_hub, temp_store  # noqa: F401
from test_run_end_completes_the_idle_stop import _Night

#: Fake seconds from the start of the night when the scripted monitor starts
#: reading rain: inside Bravo's wait, which runs to +587.5 s, and well after
#: the idle clock decided the stop (Alpha's 30 s frame, then ``TEARDOWN``).
RAIN_AT = 300.0
GUIDE_S = engine_mod.GUIDE_OP_TIMEOUT_S
FINISH = engine_mod.IDLE_STOP_FINISH_S
POLL = engine_mod.IDLE_STOP_FINISH_POLL_S
#: "At once", in fake seconds: nothing on the way to the park sleeps.
AT_ONCE = 1.0


def _rain(run, monkeypatch, *, from_s: float = RAIN_AT) -> None:
    """The simulator's safety monitor, its reading a script on the fake
    clock: safe until ``from_s``, then rain for good."""
    at = run.t0 + from_s

    async def safety_reading():
        t = run.clock.t
        if t >= at:
            return SafetyReading(is_safe=False, reason="rain sensor",
                                 source="script", ts=t)
        return SafetyReading(is_safe=True, source="script", ts=t)

    monkeypatch.setattr(run.hub, "safety_reading", safety_reading)


def _clock_the_wind_down(n: _Night, monkeypatch) -> None:
    """Clock the unsafe wind-down's own task.

    `_run` runs that wind-down as a task of its own and awaits it through
    ``asyncio.shield`` (§1.9-G). The harness clocks only the run task and the
    idle-stop task, so a sleep of the wind-down's (its reap's polls, the
    read-back's confirm probes) would be a real one while the fake clock stood
    still, and nothing bounded on the fake clock could reach its bound. So
    while the wind-down runs on its own task, that task is clocked
    (``run.also``) and the run task, which only awaits it, counts as parked.
    A wind-down the run task awaits itself (a natural end) is clocked
    already."""
    run = n.run
    engine = run.engine
    inner = engine._wind_down

    async def wind_down(*a, **kw):
        me = asyncio.current_task()
        waiting = None
        if me is not engine._task:
            waiting = asyncio.get_running_loop().create_future()
            run.also.add(me)
            run._parked[engine._task] = waiting
        try:
            return await inner(*a, **kw)
        finally:
            if waiting is not None:
                run.also.discard(me)
                waiting.set_result(None)

    monkeypatch.setattr(engine, "_wind_down", wind_down)


class _Ending:
    """One `_Night` that ends on the scripted rain (``unsafe``), or by itself
    at Bravo's stop time, with the records these cases read, each in fake
    time and in order in ``events``: the wind-down's guider stop asked
    ("guider stop") and over ("guider over"), each park ("park"), each
    roof-close attempt ("close"), and the wind-down's return ("wound").
    ``parks`` has, per park, the state and end reason published when it was
    asked, whether an idle-stop task was still the engine's, and the index
    into ``tracking_calls``. ``park_fails`` makes every park raise: "fails"
    or "times out"."""

    def __init__(self, hub, store, monkeypatch, *, unsafe: bool = True,
                 roof: bool = False, park_when_done: bool = False,
                 park_fails: str | None = None):
        if unsafe:
            store.set_safety(SafetyConfig(
                enabled=True, on_unsafe="park", unsafe_consecutive=1,
                resume_safe_consecutive=1, max_pause_min=0,
                sky_fallback_hold=False, close_dome_on_unsafe=roof))
        self.n = _Night(hub, monkeypatch, park=park_when_done)
        n = self.n
        run = self.run = n.run
        engine = self.engine = run.engine
        if unsafe:
            n.plan.safety_check = True
            _rain(run, monkeypatch)
        self.events: list[tuple[str, float]] = []
        #: True while the wind-down runs: only its guider stop is hung
        #: (`_retry_parked_on`), never the fixture's teardown after the test.
        self.winding = False
        self.parks: list[tuple[float, str | None, str | None, bool, int]] = []
        self.closes: list[int] = []
        tel = hub.devices["telescope"]
        inner_park = tel.park

        async def park():
            self.events.append(("park", run.clock.t))
            self.parks.append((run.clock.t, engine.state.get("state"),
                               engine.state.get("end_reason"),
                               engine._idle_stop_task is not None,
                               len(run.tracking_calls)))
            if park_fails == "fails":
                raise DeviceError("the mount refused the park")
            if park_fails == "times out":
                raise asyncio.TimeoutError()
            return await inner_park()

        monkeypatch.setattr(tel, "park", park)
        inner_close = roof_mod.close_observatory

        async def close_observatory(dome, telescope, **kw):
            self.events.append(("close", run.clock.t))
            self.closes.append(len(run.tracking_calls))
            return await inner_close(dome, telescope, **kw)

        # `_wind_down` imports it from the module at the moment it closes.
        monkeypatch.setattr(roof_mod, "close_observatory", close_observatory)
        _clock_the_wind_down(n, monkeypatch)
        inner_wind = engine._wind_down

        async def wind_down(*a, **kw):
            self.winding = True
            try:
                return await inner_wind(*a, **kw)
            finally:
                self.winding = False
                self.events.append(("wound", run.clock.t))

        monkeypatch.setattr(engine, "_wind_down", wind_down)
        #: (fake time, state, end reason) of every terminal state published
        self.terminal: list[tuple[float, str | None, str | None]] = []
        real_set_state = engine._set_state

        def set_state(**kw):
            real_set_state(**kw)
            st = engine.state.get("state")
            if st in ("aborted", "complete", "error"):
                self.terminal.append((run.clock.t, st,
                                      engine.state.get("end_reason")))

        monkeypatch.setattr(engine, "_set_state", set_state)

    def at(self, what: str) -> list[float]:
        return [t for w, t in self.events if w == what]

    def ended(self) -> float:
        """When the spell ended: `_run_scheduled` returned or raised."""
        assert self.n.ends, "premise: the scheduler ended"
        return self.n.ends[0][1]

    def retry_offs_from(self, i: int) -> list[tuple[float, int]]:
        """(fake seconds after the spell ended, index) of every
        ``set_tracking(False)`` the idle-stop task sent from index ``i``."""
        ended = self.ended()
        return [(round(t - ended, 2), k)
                for k, (t, on, who) in enumerate(self.run.tracking_calls)
                if k >= i and not on and who == "retry"]

    async def to_the_end(self) -> None:
        await self.n.until(lambda: not self.engine.running, "the run ended")


def _retry_parked_on(e: _Ending, gate: asyncio.Event, monkeypatch, *,
                     swallow_until: asyncio.Event | None = None,
                     eat_then_hang: bool = False,
                     hang_the_wind_down: bool = False) -> dict:
    """The idle-stop task's guider stop (its first attempt) parks on
    ``gate``. ``swallow_until``: the #235 shape, a cancel landing in that
    stop is eaten, and the stop returns normally once ``swallow_until`` is
    set. ``eat_then_hang``: the first cancel is eaten the same way, and the
    stop then goes on not answering, on ``gate`` again, until a second cancel
    lands there, which it does not eat. ``hang_the_wind_down``: a guider stop
    asked while the wind-down runs by anyone but the idle-stop task (the
    wind-down's own) does not answer: its caller waits out
    ``GUIDE_OP_TIMEOUT_S`` on the fake clock and it times out, as
    ``asyncio.wait_for`` has it. Everything else gets the real stop.
    Returns the record: the fake time the first attempt was asked, the task
    that asked it, the fake time of every cancel that landed in its stop, and
    the fake time its stop came back."""
    run = e.run
    guider = run.hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider")
    got: dict = {"asked": [], "task": None, "cancelled": [], "returned": []}
    real_stop = guider.stop_guiding

    async def stop_guiding(*a, **kw):
        if run.frozen.is_set():
            return await real_stop(*a, **kw)
        if run.who() == "retry":
            got["asked"].append(run.clock.t)
            got["task"] = asyncio.current_task()
            try:
                await run.park_until(gate)
            except asyncio.CancelledError:
                got["cancelled"].append(run.clock.t)
                if eat_then_hang:
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        got["cancelled"].append(run.clock.t)
                        raise
                elif swallow_until is None:
                    raise
                else:
                    await asyncio.wait_for(swallow_until.wait(), 10.0)
            got["returned"].append(run.clock.t)
            return await real_stop(*a, **kw)
        if hang_the_wind_down and e.winding:
            e.events.append(("guider stop", run.clock.t))
            try:
                await run._park(GUIDE_S)
                raise asyncio.TimeoutError()
            finally:
                e.events.append(("guider over", run.clock.t))
        return await real_stop(*a, **kw)

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    return got


def _premise_the_first_attempt_was_out(e: _Ending, got: dict) -> None:
    idle = e.run.exposure_end("Alpha")
    assert got["asked"] and got["asked"][0] - idle >= TEARDOWN, (
        f"premise: the idle clock decided the stop and its guider stop was "
        f"asked: at {e.run.rel(got['asked'], idle)} s after Alpha")
    assert got["asked"][0] < e.ended(), (
        "premise: the first attempt was asked before the spell ended")


# --------------------------------------------------- (a) the unsafe ending

async def test_an_unsafe_ending_parks_at_once_with_the_idle_stop_in_flight(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#270's reproduction. The idle clock decides the stop and its first
    attempt's guider stop does not come back; then the rain ends the spell
    on a SafetyAbort. The run must publish ``aborted / unsafe``, cancel the
    idle-stop task without awaiting its guider stop, and ask ``park()`` at
    once: the same fake instant, since nothing on the way to it sleeps.

    Mutant "complete the idle stop on every ending" (H3's unconditional
    ``await self._finish_idle_stop(already_ending=not spell_ran_out)`` in
    `_run`'s spell ``finally``): RED (observed) -
        AssertionError: the unsafe ending asked park() 184.2 s after the
        SafetyAbort ended the spell, not at once: it waited for the idle
        stop's first attempt (its bound is 180 s); the state published then
        was ('aborted', 'unsafe')
    (the first attempt's 180 s, then the read-back's four 1 s confirm
    probes and a poll.) With that assertion disabled in the scratch copy,
    the same mutant fails the next, the state published meanwhile
    (observed) -
        AssertionError: 'aborted / unsafe' was published [184.2] s after the
        spell ended; until then the run read 'running' in the rain

    Mutant "no cancel at the hand-over" (``task.cancel()`` deleted from
    `_hand_idle_stop_to_the_park`, the fence still moved): RED (observed,
    the verifier's round) -
        AssertionError: the idle-stop task was not cancelled at the
        hand-over: the cancel reached its guider stop at [120.0] s after the
        spell ended
    Before that assertion this case PASSED that mutant: the wind-down's reap
    cancels the task at its ``GUIDE_OP_TIMEOUT_S`` bound, so "the task ended
    cancelled" held without the hand-over cancelling anything.
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    gate = asyncio.Event()                  # never set while the run lasts
    got = _retry_parked_on(e, gate, monkeypatch)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        ended = e.ended()
        assert e.parks, "the unsafe ending never parked"
        t_park, state, reason, task_alive, _i = e.parks[0]
        took = t_park - ended
        assert took <= AT_ONCE, (
            f"the unsafe ending asked park() {took:.1f} s after the "
            f"SafetyAbort ended the spell, not at once: it waited for the "
            f"idle stop's first attempt (its bound is {FINISH:.0f} s); the "
            f"state published then was {(state, reason)}")
        assert (state, reason) == ("aborted", "unsafe"), (
            f"park() was asked before the unsafe ending was published: "
            f"{(state, reason)}")
        said = e.terminal[:1]
        assert said and said[0][0] - ended <= AT_ONCE, (
            f"'aborted / unsafe' was published "
            f"{[round(t - ended, 1) for t, _s, _r in said]} s after the "
            f"spell ended; until then the run read 'running' in the rain")
        assert not task_alive, (
            "the idle-stop task was still the engine's when the park was "
            "asked")
        assert got["returned"] == [], (
            f"premise: its guider stop never came back: {got['returned']}")
        task = got["task"]
        assert task is not None and task.cancelled(), (
            f"the idle-stop task was not cancelled: {task}")
        # WHEN, not only whether: the reap cancels a task still out at its
        # bound, so "ended cancelled" alone holds of a hand-over that never
        # cancelled and left the stop running two minutes into the rain.
        cut = [round(t - ended, 1) for t in got["cancelled"]]
        assert cut and cut[0] <= AT_ONCE, (
            f"the idle-stop task was not cancelled at the hand-over: the "
            f"cancel reached its guider stop at {cut} s after the spell "
            f"ended")
        assert e.retry_offs_from(0) == [], (
            f"the idle-stop task sent set_tracking(False): "
            f"{e.retry_offs_from(0)}")
        assert e.run.tracking() is False
        assert e.engine._idle_stop_task is None
        assert e.engine._idle_stop_handed is None, (
            "the wind-down did not take the handed task")
    finally:
        gate.set()
        await e.run.close()


# ------------------------------------------- (b) nothing but the park first

async def test_the_roof_close_waits_on_nothing_but_the_park(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The roof closes on unsafe (``close_dome_on_unsafe``), so the rain
    ends the run through the park-and-close wind-down. The idle stop's first
    attempt is still out, as in (a), and the wind-down's own guider stop does
    not answer either. The park and the roof close are both asked at once,
    before ``GUIDE_OP_TIMEOUT_S``: the guider stop is asked first and not
    awaited ahead of them, and the wind-down reaps it after the close,
    bounded, returning no later than its bound. The roof closes over the
    parked mount (`close_observatory` refuses otherwise).

    Mutant "guider stop awaited before the park" (`_wind_down` awaits
    ``self._stop_guiding_quietly()`` before `_wind_down_park_and_close`, as
    it did before the ruling): RED (observed) -
        AssertionError: park() at 120.0 s and the roof close at 120.0 s
        after the spell ended; both must be asked before the 120 s guider
        bound: the wind-down's guider stop held them
    Mutant "no reap" (the two `_reap_by` calls in `_wind_down` deleted):
    RED (observed) -
        AssertionError: the wind-down returned at 0.0 s with its guider stop
        still out (over at [] s): it was not reaped
    """
    dome = sim_hub.devices.get("dome")
    assert dome is not None and dome.connected, "premise: the sim has a roof"
    e = _Ending(sim_hub, temp_store, monkeypatch, roof=True)
    gate = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch, hang_the_wind_down=True)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert "closing roof" in (e.engine.state.get("detail") or ""), (
            f"premise: the roof closes on unsafe: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        ended = e.ended()
        asked, parks, closes = e.at("guider stop"), e.at("park"), e.at("close")
        assert asked, "premise: the wind-down asked its guider stop"
        assert parks and closes, (
            f"premise: a park and a roof close: {e.events}")
        rel = [round(t - ended, 1) for t in (parks[0], closes[0])]
        assert max(rel) <= AT_ONCE, (
            f"park() at {rel[0]} s and the roof close at {rel[1]} s after "
            f"the spell ended; both must be asked before the {GUIDE_S:.0f} s "
            f"guider bound: the wind-down's guider stop held them")
        order = [w for w, _t in e.events
                 if w in ("guider stop", "park", "close")]
        assert order[:3] == ["guider stop", "park", "close"], (
            f"the guider stop was not asked first: {order}")
        over, wound = e.at("guider over"), e.at("wound")
        assert wound, "premise: the wind-down returned"
        assert over and wound[0] >= over[0], (
            f"the wind-down returned at {wound[0] - ended:.1f} s with its "
            f"guider stop still out (over at "
            f"{[round(t - ended, 1) for t in over]} s): it was not reaped")
        assert wound[0] - ended <= GUIDE_S + 2 * POLL, (
            f"the reap was not bounded: the wind-down returned "
            f"{wound[0] - ended:.1f} s after the spell ended")
        assert await dome.shutter_state() is DomeShutterState.CLOSED, (
            "the roof did not close over the parked mount")
        assert e.retry_offs_from(0) == []
    finally:
        gate.set()
        await e.run.close()


# ----------------------------------------------- (c) a natural parking end

async def test_a_natural_end_that_parks_parks_at_once(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """No rain: the run ends by itself at Bravo's stop time, a dawn cutoff,
    with ``park_when_done`` on and the first attempt still out. The park
    fulfils the stop, so it is asked at once, and the idle-stop task sends
    nothing: the mount is stopped by the park, not by the stop.

    Mutant "complete the idle stop on every ending" (as in (a)): RED
    (observed) -
        AssertionError: the run's end asked park() 184.2 s after it ended,
        not at once: it waited for the idle stop's first attempt (its bound
        is 180 s)
    """
    e = _Ending(sim_hub, temp_store, monkeypatch, unsafe=False,
                park_when_done=True)
    gate = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "dawn_cutoff", (
            f"premise: Bravo's closed window ended it: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        ended = e.ended()
        assert e.parks, "the run's end never parked"
        took = e.parks[0][0] - ended
        assert took <= AT_ONCE, (
            f"the run's end asked park() {took:.1f} s after it ended, not at "
            f"once: it waited for the idle stop's first attempt (its bound "
            f"is {FINISH:.0f} s)")
        assert got["returned"] == [] and got["task"].cancelled(), (
            "premise: the first attempt never came back, and was cancelled")
        assert e.retry_offs_from(0) == [], e.retry_offs_from(0)
        assert e.run.tracking() is False
        assert e.run.hub.devices["telescope"].rig.parked
        assert e.engine._idle_stop_task is None
    finally:
        gate.set()
        await e.run.close()


# --------------------------------- an Abort that lands in a natural park

async def test_an_abort_in_a_natural_park_leaves_no_task_behind(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A natural end that parks awaits its wind-down directly, unshielded,
    so an operator's Abort can land in it. Here it lands while the park is
    in flight, with the wind-down's own guider stop still out (it does not
    answer) and the idle stop's task handed to the park. The two tasks the
    wind-down started or took must not outlive the run: nothing waits for
    them once the cancel has unwound the wind-down, and a guider stop left
    parked there would ask the guider something after the run's teardown.

    WHAT THIS DOES NOT HOLD: the mount. The park was cancelled before it
    reached the mount and the idle stop had been handed to it, so the mount
    is left tracking. Under H3 the stop was completed before the park; the
    window is #270's own, the implementer's residual (1), and it is the
    orchestrator's to rule on and file, not this test's to enshrine.

    Mutant "no cleanup on a cancelled wind-down" (the ``except
    BaseException:`` clause in `_wind_down` that cancels the guider stop and
    the handed task deleted): RED (observed, the verifier's round) -
        AssertionError: the wind-down's guider stop outlived the Abort that
        unwound the wind-down: asked at [0.0] s after the run ended, over at
        [] s
    """
    e = _Ending(sim_hub, temp_store, monkeypatch, unsafe=False,
                park_when_done=True)
    gate = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch, hang_the_wind_down=True)
    tel = sim_hub.devices["telescope"]
    in_park, never = asyncio.Event(), asyncio.Event()
    real_park = tel.park

    async def park():
        in_park.set()
        await never.wait()          # in flight until the Abort cuts it
        return await real_park()

    monkeypatch.setattr(tel, "park", park)
    e.engine.start(e.n.plan)
    try:
        await e.n.until(in_park.is_set, "the natural end's park was asked")
        assert [(s, r) for _t, s, r in e.terminal] == [
            ("complete", "dawn_cutoff")], (
            f"premise: a natural end, published: {e.terminal}")
        _premise_the_first_attempt_was_out(e, got)
        ended = e.ended()
        asked = e.at("guider stop")
        assert asked and not e.at("guider over"), (
            f"premise: the wind-down's guider stop is out: {e.events}")
        await e.engine.abort()
        over = e.at("guider over")
        assert over, (
            f"the wind-down's guider stop outlived the Abort that unwound the "
            f"wind-down: asked at {[round(t - ended, 1) for t in asked]} s "
            f"after the run ended, over at "
            f"{[round(t - ended, 1) for t in over]} s")
        task = got["task"]
        assert task is not None and task.done(), (
            f"the handed idle-stop task outlived the Abort: {task}")
        assert e.retry_offs_from(0) == [], e.retry_offs_from(0)
    finally:
        never.set()
        gate.set()
        await e.run.close()


# ------------------------------------------ (e) a cancel the guider eats

async def test_a_cancel_the_guider_eats_sends_no_stop_after_the_park(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The #235 shape. The rain ends the spell with the first attempt out,
    and the cancel the parking ending sends lands in that attempt's guider
    stop, which EATS it and comes back only once the park has begun (the park
    here waits, for real, until the idle-stop task has finished). The
    `_park_hold` it returns into would send ``set_tracking(False)`` to a
    mount that is parking: a second writer to a mount with one owner. The
    fence holds it off, and the task then ends on the cancel
    (`_reraise_swallowed_cancel`).

    Mutant "no fence" (the ``fence is not None and fence !=
    self._idle_stop_epoch`` return in `_stop_tracking_quietly` deleted):
    RED (observed) -
        AssertionError: the idle-stop task sent set_tracking(False) after
        park() was asked (park at index 2): [(0.0, 2)]
    The first draft passed that mutant: it read the park's index from
    `_Ending`'s record, which the double below reaches only after its wait,
    so the late stop came before it. The index is taken as park() is asked.
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    gate = asyncio.Event()
    park_began = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch, swallow_until=park_began)
    tel = sim_hub.devices["telescope"]
    inner = tel.park
    #: the index into ``tracking_calls`` when park() was asked, taken here,
    #: before this double waits: `_Ending`'s own record is made after it
    asked: list[int] = []

    async def park():
        asked.append(len(e.run.tracking_calls))
        park_began.set()
        task = got["task"]
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=10.0)
        return await inner()

    monkeypatch.setattr(tel, "park", park)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        assert got["returned"], (
            "premise: the guider stop ate the cancel and came back")
        task = got["task"]
        assert task.done() and task.cancelled(), (
            f"premise: the task still ended on the cancel: {task}")
        assert asked, "premise: the park was asked"
        i_park = asked[0]
        late = e.retry_offs_from(i_park)
        assert late == [], (
            f"the idle-stop task sent set_tracking(False) after park() was "
            f"asked (park at index {i_park}): {late}")
        assert e.run.hub.devices["telescope"].rig.parked
    finally:
        gate.set()
        park_began.set()
        await e.run.close()


async def test_a_handed_stop_that_eats_the_cancel_is_reaped_bounded(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The #235 shape again, with a guider stop that then goes on not
    answering: it eats the hand-over's cancel and stays out. The park is
    still asked at once, and the task is fenced, so it cannot reach the
    mount; but a task the run handed off must not outlive the run's
    teardown with a device call in flight. The wind-down reaps it after the
    park and the roof close, cancelling it again by its ``GUIDE_OP_TIMEOUT_S``
    bound, and returns only then.

    Mutant "the handed task is not reaped" (``await self._reap_by(handed,
    reap_by)`` deleted from `_wind_down`, the guider stop's reap kept): RED
    (observed, the verifier's round) -
        AssertionError: the wind-down returned at 0.0 s and left the handed
        idle-stop task running: cancels reached its guider stop at [0.0] s
        after the spell ended
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    gate = asyncio.Event()                  # never set while the run lasts
    got = _retry_parked_on(e, gate, monkeypatch, eat_then_hang=True)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        ended = e.ended()
        assert e.parks and e.parks[0][0] - ended <= AT_ONCE, (
            f"premise: the park was still asked at once: {e.parks}")
        cut = [round(t - ended, 1) for t in got["cancelled"]]
        wound = e.at("wound")
        assert wound, "premise: the wind-down returned"
        back = round(wound[0] - ended, 1)
        assert len(cut) == 2, (
            f"the wind-down returned at {back} s and left the handed "
            f"idle-stop task running: cancels reached its guider stop at "
            f"{cut} s after the spell ended")
        assert cut[0] <= AT_ONCE, f"premise: the hand-over cancelled: {cut}"
        assert cut[1] <= GUIDE_S + 2 * POLL and back <= GUIDE_S + 2 * POLL, (
            f"the reap was not bounded: the second cancel at {cut[1]} s, the "
            f"wind-down back at {back} s")
        assert got["returned"] == [], (
            f"premise: the stop never came back: {got['returned']}")
        assert e.retry_offs_from(0) == [], e.retry_offs_from(0)
        assert got["task"].cancelled()
    finally:
        gate.set()
        await e.run.close()


# ------------------------------------------------ (f) a park that did not

@pytest.mark.parametrize("park_fails", ["fails", "times out"])
async def test_a_park_that_did_not_park_stops_the_mount_and_says_so(
        sim_hub, temp_store, monkeypatch, bus_lines, park_fails):
    """The rain ends the spell with the first attempt out, as in (a), and the
    roof closes on unsafe; but the park fails, or times out. That is a
    wind-down that did not park, and the idle stop handed to it was never
    made. So after the roof-close attempt (which `close_observatory`
    refuses over an unparked mount) the mount is asked once, bounded, to
    stop tracking, read back, and what came of it is said in words.

    Mutant "no fallback" (the ``if parked is False: await
    self._stop_after_a_failed_park()`` in `_wind_down_park_and_close`
    deleted): RED, both (observed) -
        AssertionError: the park failed (fails) and the mount was left
        tracking, with nothing said: set_tracking(False) after the roof
        close at [], lines []
        AssertionError: the park failed (times out) and the mount was left
        tracking, with nothing said: set_tracking(False) after the roof
        close at [], lines []
    """
    e = _Ending(sim_hub, temp_store, monkeypatch, roof=True,
                park_fails=park_fails)
    gate = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        assert e.parks and e.closes, (
            f"premise: a park and a roof-close attempt: {e.events}")
        i_close = e.closes[0]
        offs = [who for t, on, who in e.run.tracking_calls[i_close:]
                if not on]
        said = [m for lvl, m, _s in bus_lines
                if "the park did not complete" in m]
        assert offs and said, (
            f"the park failed ({park_fails}) and the mount was left "
            f"tracking, with nothing said: set_tracking(False) after the "
            f"roof close at {offs}, lines {said}")
        assert len(offs) == 1 and "retry" not in offs, (
            f"the wind-down asked the mount once itself: {offs}")
        assert e.run.tracking() is False
        assert len(said) == 1 and "it has stopped tracking" in said[0], said
        assert not e.run.hub.devices["telescope"].rig.parked, (
            "premise: the park never happened")
    finally:
        gate.set()
        await e.run.close()


@pytest.mark.parametrize("readback", ["still tracking", "unreadable"])
async def test_a_failed_park_says_when_the_mount_did_not_stop(
        sim_hub, temp_store, monkeypatch, bus_lines, readback):
    """(f)'s other outcome. The park fails, and the mount will not stop
    either: the fallback's ``set_tracking(False)`` waits out its bound and
    times out, and the read-back says the mount is still tracking, or cannot
    be read at all. The one line must say so, as an error, and say that
    nothing will ask again: a warning that the mount "has stopped" over a
    mount still tracking is the lie the fallback exists to prevent.

    Mutant "the fallback always says it stopped" (``if tracking is False:``
    in `_stop_after_a_failed_park` made ``if True:``): RED, both (observed,
    the verifier's round) -
        AssertionError: the failed park's fallback said the wrong thing
        about a mount that did not stop (still tracking): [('warning',
        'the park did not complete, so the mount was asked to stop tracking
        instead, and it has stopped tracking')]
        AssertionError: the failed park's fallback said the wrong thing
        about a mount that did not stop (unreadable): [('warning', 'the park
        did not complete, so the mount was asked to stop tracking instead,
        and it has stopped tracking')]
    Before this case that mutant survived every test in this file.
    """
    e = _Ending(sim_hub, temp_store, monkeypatch, roof=True,
                park_fails="fails")
    gate = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch)
    run = e.run
    inner = e.engine._wind_down

    async def wind_down(*a, **kw):
        # Only from the wind-down on: the night before it is (a)'s.
        run.stop_hangs = True
        run.read_hangs = readback == "unreadable"
        return await inner(*a, **kw)

    monkeypatch.setattr(e.engine, "_wind_down", wind_down)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        _premise_the_first_attempt_was_out(e, got)
        assert e.parks and e.closes, (
            f"premise: a park and a roof-close attempt: {e.events}")
        offs = [who for t, on, who in run.tracking_calls[e.closes[0]:]
                if not on]
        assert len(offs) == 1 and "retry" not in offs, (
            f"premise: the wind-down asked the mount once itself: {offs}")
        assert run.tracking() is True, (
            "premise: the mount would not stop")
        said = [(lvl, m) for lvl, m, _s in bus_lines
                if "the park did not complete" in m]
        detail = ("it still reports tracking" if readback == "still tracking"
                  else "its tracking state cannot be read")
        assert (len(said) == 1 and said[0][0] == "error"
                and detail in said[0][1]
                and "it may still be tracking" in said[0][1]), (
            f"the failed park's fallback said the wrong thing about a mount "
            f"that did not stop ({readback}): {said}")
        ended = e.ended()
        back = e.at("wound")[0] - ended
        assert back <= 2 * engine_mod.MOUNT_QUERY_TIMEOUT_S + 2 * POLL, (
            f"the fallback was not bounded: the wind-down returned "
            f"{back:.1f} s after the spell ended")
    finally:
        run.stop_hangs = run.read_hangs = False
        gate.set()
        await run.close()


# ------------------------------------------------- which endings park

class _Plan:
    def __init__(self, park_when_done: bool):
        self.park_when_done = park_when_done


def _label(ending, park_when_done) -> str:
    kind = ("a natural end" if ending is None else
            "a quality stop" if isinstance(ending, NightQualityStop) else
            "an abort" if isinstance(ending, asyncio.CancelledError) else
            "a failure" if not isinstance(ending, SafetyAbort) else
            "a SafetyAbort")
    return f"{kind} (park_when_done {park_when_done})"


@pytest.mark.parametrize("ending,park_when_done,parks", [
    (None, False, False),
    (None, True, True),
    (NightQualityStop("rejects"), False, False),
    (NightQualityStop("rejects"), True, True),
    (SafetyAbort("rain"), False, True),
    (SlewRefused("the floor", words="it is below the floor"), False, True),
    (asyncio.CancelledError(), True, False),
    (RuntimeError("boom"), True, False),
], ids=["natural end, no park", "natural end, parks", "quality stop, no park",
        "quality stop, parks", "unsafe", "a slew refused", "an abort",
        "a failure"])
def test_which_endings_hand_the_stop_to_the_park(ending, park_when_done,
                                                 parks):
    """`_ending_parks` answers for `_run`'s handlers before they run, and
    must agree with what each does: every SafetyAbort parks (§1.9-G); a
    natural end, a cooling skip and a quality stop park when the plan says;
    an Abort's cancel and a failure take `_safe_stop`, which touches no
    mount. An ending taken as parking that does not park drops the idle stop
    with nothing to make it; one taken as not parking that does waits for it
    in the rain.

    Mutant "a quality stop is a failure" (the ``isinstance(ending,
    NightQualityStop)`` arm deleted): RED, [quality stop, parks] (observed) -
        AssertionError: _ending_parks said False for a quality stop
        (park_when_done True); its handler parks: True
    Mutant "every exception parks" (``return isinstance(ending, SafetyAbort)``
    made ``return True``): RED, [an abort] and [a failure] (observed) -
        AssertionError: _ending_parks said True for an abort (park_when_done
        True); its handler parks: False
        AssertionError: _ending_parks said True for a failure (park_when_done
        True); its handler parks: False
    """
    got = SequenceEngine._ending_parks(_Plan(park_when_done), ending)
    assert got is parks, (
        f"_ending_parks said {got} for {_label(ending, park_when_done)}; "
        f"its handler parks: {parks}")
