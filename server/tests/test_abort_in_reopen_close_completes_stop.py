# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""An Abort that lands in the auto-reopen roof close completes the idle stop
the close ended (#393, the #345 follow-up; spec 6.17 and owner list items 14
and 19).

The auto-reopen roof close ends the idle stop's task before it parks (#306,
`_close_for_reopen`, `_cancel_idle_stop_retry`), so no retry of the stop can
write into its park. S4 re-arms the retries when that close is then refused
or fails (#345, `_rearm_idle_stop`). It re-armed nothing when the close was
CANCELLED: an operator's Abort that landed while the close was in flight
(its guider wait, its park, the park's read-back, the roof close) went
straight out of `_close_for_reopen`, and the Abort's `_finish_idle_stop`,
from `_run`'s wind-down or from `abort` itself, found ``_idle_stop_task``
None, so it asked the mount nothing and read nothing back. Owner list item 14
(H3 orchestrator ruling 5) says a run's end never abandons a stop the idle
watch decided, and an Abort does not park, so item 19 hands it to no park.
This one was abandoned: after the run the mount could still be tracking with
nothing watching it, the #345 hazard by the other road. The "safety rides
value paths" class: the stop's retry rode the lifetime of a task the close
had ended, while the hazard it asked about went on.

NOW the close's exception path re-arms the retries it ended before the
exception goes on (`_rearm_idle_stop`), and the Abort's `_finish_idle_stop`
finds the task and completes the stop: it ends the retries, asks the mount
once more, reads tracking back and says what came of it. The re-armed task
sets ``_idle_stop_first_made``, so the Abort does not wait for a first
attempt nobody will make.

THE HARNESS is test_refused_close_keeps_idle_stop's `_Pause`:
test_idle_park_hold's clocked simulator with no run, the roof on, and its
flaky link, which drops every ``set_tracking(False)`` until ``ACCEPT_AT``
(the mount tracks on and reads tracking). This is #393's probe made a case.
The idle stop is decided at 0 s and asks at 0, 60 and 120 s, all
unconfirmed. Rain at ``RAIN_AT`` (130 s), handed to `_on_unsafe` from a task
the driver clocks, opens the auto-reopen roof close. The close's park never
returns: it parks the task on an event nobody sets (`park_until`). At
``ABORT_AT`` (140 s) a second clocked task cancels the task running the
close, waits for it to end, and runs `engine.abort()`. That is an operator's
Abort: `abort` cancels the run task that runs the close, awaits it, and then
completes the stop. With no run, `abort`'s own `_finish_idle_stop` is the one
that finds the task; in a run it is `_run`'s, which is the same code. The
clock is then run on to ``UNTIL`` (440 s), so a retry left alive would ask
four more times. Every ``set_tracking(False)`` and every tracking read is
recorded with its fake time and the task that made it. The site is a
fixture, never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S5-ENG-SAFE-mut, in the session scratchpad), never in the shared
tree (#254).
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import DomeShutterState

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_refused_close_keeps_idle_stop import RAIN, RAIN_AT, _Pause

#: When the link starts taking the stop, as #393's probe has it: after the
#: Abort, so the Abort's own ask goes out and is not confirmed.
ACCEPT_AT = 160.0
#: When the Abort lands, in fake seconds after the idle stop was decided:
#: 10 s into the close's park, which never returns.
ABORT_AT = 140.0
#: How far the clock is run on, as the probe ran it.
UNTIL = 440.0
POLL = engine_mod.IDLE_STOP_FINISH_POLL_S
#: The Abort's completion line (`_complete_idle_stop`) when the mount has not
#: confirmed the stop.
LINE = ("the run is ending and the mount had not confirmed the idle stop, "
        "so it was asked once more, and it has still not confirmed it (it "
        "still reports tracking); nothing will ask it again, so it may "
        "still be tracking")


async def _abort_in_the_close(p: _Pause, monkeypatch) -> dict:
    """Rain at ``RAIN_AT``, a close whose park never returns, and at
    ``ABORT_AT`` the Abort: the close's task cancelled and waited for, then
    `engine.abort()`. The clock runs on to ``UNTIL``. Returns the record:
    the fake time of every park asked, the close's task, and when the Abort
    began and ended."""
    run, engine = p.run, p.engine
    loop = asyncio.get_running_loop()
    rec: dict = {"parks": [], "close": None, "abort": None, "aborted": None}
    never = asyncio.Event()

    async def park():
        rec["parks"].append(run.clock.t)
        await run.park_until(never)

    monkeypatch.setattr(p.tel, "park", park)

    async def the_rain():
        await engine_mod.asyncio.sleep(RAIN_AT)
        await engine._on_unsafe("rain sensor", target=None)

    rain = loop.create_task(the_rain(), name=RAIN)
    rec["close"] = rain
    run.also.add(rain)

    async def the_abort():
        await engine_mod.asyncio.sleep(ABORT_AT)
        rain.cancel()
        await asyncio.gather(rain, return_exceptions=True)
        rec["abort"] = run.clock.t
        await engine.abort()
        rec["aborted"] = run.clock.t

    async def the_night_goes_on():
        await engine_mod.asyncio.sleep(UNTIL)

    tasks = [loop.create_task(the_abort(), name="abort"),
             loop.create_task(the_night_goes_on(), name="until")]
    run.also.update(tasks)
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), 30.0)
    finally:
        run.also.difference_update(tasks)
        run.also.discard(rain)
    return rec


def _after_the_rain(p: _Pause, what: list[tuple[float, str]]) -> list:
    """(fake seconds, task) of every record strictly after ``RAIN_AT``: the
    close's own stop is at ``RAIN_AT`` exactly, on the rain task."""
    return [(round(t - p.t0, 2), who) for t, who in what
            if t - p.t0 > RAIN_AT + 1e-9]


async def test_an_abort_in_the_reopen_close_completes_the_idle_stop(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#393's probe. The idle stop is unconfirmed and asking once a minute
    on the flaky link; the rain opens the auto-reopen close, which ends that
    task and stops tracking itself (130 s); its park never returns; the
    Abort lands at 140 s. The Abort completes the stop the close ended: after
    130 s there is exactly one ask, the Abort's, and one tracking read after
    it, the Abort's, and the "had not confirmed the idle stop" line is said
    once, since the link still drops the stop. Nothing else asks for the
    rest of the probe, and nothing is left asking: the Abort ends the
    re-armed retries. The Abort does not wait for a first attempt, since
    the re-armed task makes none.

    Mutant "the cancel path re-arms nothing" (the ``if ended:
    self._rearm_idle_stop()`` in `_close_for_reopen`'s ``except
    BaseException`` deleted, the code #393 was filed against): RED
    (observed) -
        AssertionError: an Abort in the reopen close did not complete the
        idle stop the close had ended: after 130 s the asks were [] and the
        tracking reads [], the Abort from 140.0 s to 140.0 s; the idle-stop
        task at the Abort's end: None; the line said 0 time(s)
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=ACCEPT_AT)
    try:
        first = await p.decide_the_idle_stop()
        rec = await _abort_in_the_close(p, monkeypatch)
        retries = p.rel(p.asks_by("retry"))
        assert retries == [0.0, 60.0, 120.0], (
            f"premise: the idle stop asked at 0, 60 and 120 s, unconfirmed, "
            f"and nothing of its own after the rain: {retries}")
        assert first.cancelled(), "premise: the close ended the idle stop"
        close = rec["close"]
        assert close.cancelled() and p.rel(rec["parks"]) == [RAIN_AT], (
            f"premise: the close parked at {RAIN_AT:g} s, the park never "
            f"returned, and the Abort cancelled it: parks at "
            f"{p.rel(rec['parks'])}, the close cancelled: "
            f"{close.cancelled()}")
        own = p.rel(p.asks_by(RAIN))
        assert own == [RAIN_AT], (
            f"premise: the close stopped tracking itself, once, at the "
            f"rain: {own}")
        asks = _after_the_rain(p, p.asks)
        reads = _after_the_rain(p, p.reads)
        said = [m for _l, m, _s in bus_lines]
        lines = [m for m in said if m == LINE]
        began, ended = (round(rec[k] - p.t0, 2) for k in ("abort",
                                                         "aborted"))
        left = p.engine._idle_stop_task
        assert len(asks) == 1 and len(reads) == 1 and len(lines) == 1, (
            f"an Abort in the reopen close did not complete the idle stop "
            f"the close had ended: after {RAIN_AT:g} s the asks were {asks} "
            f"and the tracking reads {reads}, the Abort from {began} s to "
            f"{ended} s; the idle-stop task at the Abort's end: "
            f"{None if left is None else left.get_name()}; the line said "
            f"{len(lines)} time(s)")
        (asked, by), (read, read_by) = asks[0], reads[0]
        assert by == "abort" and read_by == "abort" and asked <= read, (
            f"the ask and the read-back after the rain were not the "
            f"Abort's, in that order: ask {asks}, read {reads}")
        assert ABORT_AT <= asked <= ABORT_AT + 2 * POLL, (
            f"the Abort asked at {asked} s; it landed at {ABORT_AT:g} s")
        assert ended - began <= 2 * POLL, (
            f"the Abort took {ended - began:g} s: it waited for a first "
            f"attempt the re-armed retries never make")
        assert left is None, (
            f"the Abort left the idle-stop task {left.get_name()} asking")
        assert not [m for m in said if "had not finished its first attempt"
                    in m]
        assert await p.dome.shutter_state() is not DomeShutterState.CLOSED
    finally:
        await p.run.close()


async def test_an_abort_while_the_close_ends_the_idle_stop_completes_it(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The Abort lands earlier in the close: while it is still ending the
    idle stop's task, before it has asked the mount anything. The idle
    stop's first attempt is in its guider stop, which does not answer and
    eats the cancel (the #235 shape, which `_reraise_swallowed_cancel`
    still guards against, #252), so the close's `_cancel_idle_stop_retry`
    waits on it, and the Abort cancels the close there. That cancel cannot
    go through until the task it waits on ends (``gather`` waits for its
    child), so it lands when the guider stop answers: the first attempt
    then sends the stop it was making, which the link drops, and ends at
    `_reraise_swallowed_cancel`, before any read-back, and the close takes
    the cancel inside its ``try``. The stop was ended there as surely as
    later, and nothing else will complete it, so it is re-armed and the
    Abort completes it: after the first attempt's own ask, one more ask and
    one tracking read, both the Abort's, and the completion line once. The
    clock stands still while the close waits on a task that is no longer
    the engine's, so all of it happens at the rain's own instant.

    Mutant "the idle stop's own cancel outside the try" (``ended = await
    self._cancel_idle_stop_retry()`` put back ahead of the ``try``, with no
    answer taken before the await, as S4 had it): RED (observed) -
        AssertionError: an Abort that landed while the close was ending the
        idle stop did not complete it: asks after 130 s [(130.0, 'retry')],
        tracking reads [], the line said 0 time(s); the idle-stop task at
        the Abort's end: None
    Mutant "the cancel path re-arms nothing" (as above): RED here too, with
    the same failure (observed).
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=10 * UNTIL)
    run, engine = p.run, p.engine
    loop = asyncio.get_running_loop()
    guider = sim_hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider")
    real_stop = guider.stop_guiding
    eaten = asyncio.Event()
    answer = asyncio.Event()
    first = [True]

    async def stop_guiding(*a, **kw):
        # The first guider stop is the idle stop's first attempt: it does
        # not answer until the test says, and every cancel that lands in it
        # is eaten. Every later one (the fixture's teardown) answers.
        if first[0]:
            first[0] = False
            while not answer.is_set():
                try:
                    await run.park_until(answer)
                except asyncio.CancelledError:
                    eaten.set()
        return await real_stop(*a, **kw)

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    old = None
    try:
        old = await p.decide_the_idle_stop()

        async def the_rain():
            await engine_mod.asyncio.sleep(RAIN_AT)
            await engine._on_unsafe("rain sensor", target=None)

        rain = loop.create_task(the_rain(), name=RAIN)
        run.also.add(rain)
        rec: dict = {}

        async def the_abort():
            # On a task the driver clocks, and never parked until
            # `engine.abort()` sleeps, so the clock cannot run on between
            # the close's end and the Abort's completion, as it cannot
            # between a run task's end and `_run`'s own `_finish_idle_stop`.
            rain.cancel()
            await asyncio.sleep(0)
            answer.set()
            await asyncio.gather(rain, old, return_exceptions=True)
            rec["begun"] = run.clock.t
            await engine.abort()

        try:
            await asyncio.wait_for(eaten.wait(), 30.0)
            assert not rain.done() and engine._idle_stop_task is None, (
                "premise: the close is waiting for the idle stop's task, "
                "which it has taken off the engine")
            abort = loop.create_task(the_abort(), name="abort")
            run.also.add(abort)
            try:
                await asyncio.wait_for(abort, 30.0)
            finally:
                run.also.discard(abort)
        finally:
            run.also.discard(rain)
        assert rain.cancelled() and old.cancelled(), (
            f"premise: the Abort cancelled the close, and the first attempt "
            f"ended on the cancel it had eaten: {rain}, {old}")
        begun = rec["begun"]
        left = engine._idle_stop_task
        assert round(begun - p.t0, 6) == RAIN_AT, (
            f"premise: the clock stood at the rain while the close waited: "
            f"{begun - p.t0}")
        asks = [(round(t - p.t0, 2), who) for t, who in p.asks
                if t >= p.t0 + RAIN_AT]
        reads = [(round(t - p.t0, 2), who) for t, who in p.reads
                 if t >= p.t0 + RAIN_AT]
        lines = [m for _l, m, _s in bus_lines if m == LINE]
        assert [w for _t, w in asks] == ["retry", "abort"] and [
            w for _t, w in reads] == ["abort"] and len(lines) == 1, (
                f"an Abort that landed while the close was ending the idle "
                f"stop did not complete it: asks after {RAIN_AT:g} s "
                f"{asks}, tracking reads {reads}, the line said "
                f"{len(lines)} time(s); the idle-stop task at the Abort's "
                f"end: {None if left is None else left.get_name()}")
        assert asks[1][0] <= reads[0][0] <= RAIN_AT + 2 * POLL, (asks, reads)
        assert left is None, f"the Abort left {left.get_name()} asking"
        assert p.asks_by(RAIN) == [], (
            f"premise: the close was cut before its own stop of tracking: "
            f"{p.rel(p.asks_by(RAIN))}")
    finally:
        answer.set()
        if old is not None and not old.done():
            old.cancel()
        await p.run.close()


async def test_control_an_abort_in_the_close_with_no_idle_stop_adds_no_ask(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same rain, close, hung park and Abort, with no idle stop
    decided: the close ends no task, so there is nothing of the idle stop's
    to re-arm or complete. After the close's own stop at 130 s nothing asks
    the mount to stop and nothing reads tracking back, through to 440 s, and
    no completion line is said. The mount's stop here is the close's own,
    as it was before #393: an Abort starts no motion and decides no stop.

    Mutant "the cancel path re-arms whatever the close ended" (the handler's
    re-arm made unconditional, ``if ended:`` dropped): RED (observed) -
        AssertionError: an Abort in a close that ended no idle stop asked the
        mount again: asks after 130 s [(140.25, 'abort')], tracking reads
        [(140.25, 'abort')], the line said 1 time(s)
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=ACCEPT_AT)
    try:
        rec = await _abort_in_the_close(p, monkeypatch)
        close = rec["close"]
        assert close.cancelled() and p.rel(rec["parks"]) == [RAIN_AT], (
            f"premise: the close parked at the rain and was cancelled: "
            f"parks at {p.rel(rec['parks'])}")
        assert p.rel(p.asks_by(RAIN)) == [RAIN_AT], (
            "premise: the close stopped tracking itself at the rain")
        asks = _after_the_rain(p, p.asks)
        reads = _after_the_rain(p, p.reads)
        lines = [m for _l, m, _s in bus_lines
                 if "had not confirmed the idle stop" in m]
        assert asks == [] and reads == [] and lines == [], (
            f"an Abort in a close that ended no idle stop asked the mount "
            f"again: asks after {RAIN_AT:g} s {asks}, tracking reads "
            f"{reads}, the line said {len(lines)} time(s)")
        assert p.asks_by("retry") == [] and p.engine._idle_stop_task is None
    finally:
        await p.run.close()
