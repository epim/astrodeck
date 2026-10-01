"""The safety pause does not ask the mount to stop once more after an idle
stop has confirmed it (#471, #394's second half; spec 6.17 and 5.8).

The open-sky pause (`_park_hold_pause`) reads its own stop of tracking back,
and while the mount has not confirmed it, asks again at most once per
``IDLE_STOP_RETRY_S`` (#345). While an idle stop is still asking on its own
task (a plain pause does not end it), the pause leaves the asking to it:
one ask a minute, not two. But the pause's ``unconfirmed`` was the answer
it read when the pause began, and nothing refreshed it when that task
confirmed the stop and ended. At the pause's next due time it asked once
more, of a mount already stopped, before it read the stop back and learned
it was confirmed: a redundant ``set_tracking(False)``, on the link that may
be why the stop was slow to confirm, made on a stale answer.

NOW the pause remembers leaning on an idle stop (it was alive when the stop
was read back, or at a due time), and at the first due time after that task
has ended it reads the stop back first, and asks only if it is still not
confirmed. A pause that never leaned on one asks as before, with no read in
front.

THE HARNESS is test_refused_close_keeps_idle_stop's `_Pause`: the clocked
simulator with no run, a plain pause (no roof) opened by rain at ``RAIN_AT``
on a task of the test's that the driver clocks (``RAIN``), safe again at
``SAFE_AT``, on a flaky link that drops every stop until ``accept_at``.
Every ``set_tracking(False)`` and every tracking read is recorded with its
fake time and the task that made it. The site is a fixture, never the real
one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S7-ENG-SAFE-r2-mut, in the session scratchpad), never in the
shared tree (#254), and every quote is from that copy's run.
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_refused_close_keeps_idle_stop import (RAIN, RAIN_AT, RETRY, SAFE_AT,
                                                _Pause)

#: When the flaky link starts taking the stop, in fake seconds after the
#: idle stop was decided: its ask at 240 s takes, between the pause's due
#: times at 190 s and 250 s (the pause opens at ``RAIN_AT``, 130 s).
ACCEPT_AT = 200.0
#: When the control's idle stop is ended unconfirmed, between the same two
#: due times.
ENDED_AT = 220.0
#: When the link starts taking the stop in the case where the idle stop
#: confirms before the pause's FIRST due time: its ask at 180 s takes, after
#: the pause opened at 130 s and before its due time at 190 s.
EARLY_ACCEPT_AT = 150.0


def _cancel_the_idle_stop_at(p: _Pause, at: float) -> asyncio.Task:
    """At fake ``at`` seconds, end the idle stop's task with the stop still
    unconfirmed (`_cancel_idle_stop_retry`, awaited), from a task the driver
    clocks. Returns that task."""
    run, engine = p.run, p.engine

    async def end_it():
        await engine_mod.asyncio.sleep(at)
        await engine._cancel_idle_stop_retry()

    task = asyncio.get_running_loop().create_task(end_it(), name="ender")
    run.also.add(task)
    task.add_done_callback(run.also.discard)
    return task


async def test_an_idle_stop_that_confirms_between_due_times_gets_no_second_ask(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The idle stop was decided and is unconfirmed on the flaky link,
    asking once a minute on its own task. The rain opens a plain pause at
    130 s, whose own stop does not take either, and which leaves the
    asking to that task at its due time, 190 s. The link starts taking the
    stop at 200 s; the idle stop's ask at 240 s takes, is read back, and
    the task ends, confirmed. At the pause's next due time, 250 s, the
    pause reads the stop back and finds it confirmed, and sends no second
    ``set_tracking(False)``, then or for the rest of the pause.

    Mutant "ask on the stale answer" (the re-read before the ask, ``if
    leaned: ... unconfirmed = await self._pause_stop_unconfirmed(...)``,
    taken out of the pause's ``ask_if_due``, as before #471): RED
    (observed) -
        AssertionError: the pause asked the mount to stop again after the
        idle stop had confirmed it: its asks at [130.0, 250.0] s, the idle
        stop's at [0.0, 60.0, 120.0, 180.0, 240.0] s, its task done at
        [245.0] s
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=ACCEPT_AT)
    ended: list[float] = []
    try:
        first = await p.decide_the_idle_stop()
        first.add_done_callback(lambda _t: ended.append(p.run.clock.t))
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        task, _epoch = p.at_pause[0]
        retries = p.rel(p.asks_by("retry"))
        assert task is first and first.done() and not first.cancelled() \
            and retries == [0.0, 60.0, 120.0, 180.0, 240.0] and ended \
            and start + RETRY < ended[0] < start + 2 * RETRY, (
                f"premise: the pause began with the idle stop asking, which "
                f"confirmed the stop between the pause's due times "
                f"({round(start - p.t0 + RETRY, 2)} s and "
                f"{round(start - p.t0 + 2 * RETRY, 2)} s): its asks at "
                f"{retries} s, its task done at {p.rel(ended)} s")
        own = p.asks_by(RAIN, lo=start, hi=end)
        assert own == [start], (
            f"the pause asked the mount to stop again after the idle stop "
            f"had confirmed it: its asks at {p.rel(own)} s, the idle stop's "
            f"at {retries} s, its task done at {p.rel(ended)} s")
        due = start + 2 * RETRY
        looked = [r for r in p.reads_by(RAIN) if due <= r < due + RETRY]
        assert looked, (
            f"the pause did not read its stop back at its due time after "
            f"the idle stop ended, {round(due - p.t0, 2)} s: its reads at "
            f"{p.rel(p.reads_by(RAIN))} s")
        assert end - p.t0 >= SAFE_AT and p.tel.rig.tracking is False
    finally:
        await p.run.close()


async def test_an_idle_stop_that_confirms_before_the_first_due_time_gets_no_ask(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The same lean, one due time earlier. The rain opens the pause at
    130 s with the idle stop alive and asking; the link takes the stop
    from ``EARLY_ACCEPT_AT``, the idle stop's ask at 180 s takes, and its
    task ends, confirmed, before the pause's FIRST due time, 190 s. The
    pause never saw the task alive at a due time, only when it read its
    own stop back as it opened, and that is a lean too: at 190 s it reads
    the stop back first, finds it confirmed, and sends no second
    ``set_tracking(False)``. (Added by the task's verifier, its mutant run
    in S7-ENG-SAFE-verify-mut, the verifier's private copy.)

    Mutant "no lean at the pause's start" (``leaned = idle is not None and
    not idle.done()`` before the pause loop made ``leaned = False``, so only
    a task alive at a due time counts): RED (observed) -
        AssertionError: the pause asked the mount to stop again after the
        idle stop had confirmed it: its asks at [130.0, 190.0] s, the idle
        stop's at [0.0, 60.0, 120.0, 180.0] s, its task done at [185.0] s
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=EARLY_ACCEPT_AT)
    ended: list[float] = []
    try:
        first = await p.decide_the_idle_stop()
        first.add_done_callback(lambda _t: ended.append(p.run.clock.t))
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        task, _epoch = p.at_pause[0]
        retries = p.rel(p.asks_by("retry"))
        assert task is first and first.done() and not first.cancelled() \
            and retries == [0.0, 60.0, 120.0, 180.0] and ended \
            and start < ended[0] < start + RETRY, (
                f"premise: the pause began with the idle stop asking, which "
                f"confirmed the stop before the pause's first due time "
                f"({round(start - p.t0 + RETRY, 2)} s): its asks at "
                f"{retries} s, its task done at {p.rel(ended)} s")
        own = p.asks_by(RAIN, lo=start, hi=end)
        assert own == [start], (
            f"the pause asked the mount to stop again after the idle stop "
            f"had confirmed it: its asks at {p.rel(own)} s, the idle stop's "
            f"at {retries} s, its task done at {p.rel(ended)} s")
        due = start + RETRY
        looked = [r for r in p.reads_by(RAIN) if due <= r < due + RETRY]
        assert looked, (
            f"the pause did not read its stop back at its first due time, "
            f"{round(due - p.t0, 2)} s: its reads at "
            f"{p.rel(p.reads_by(RAIN))} s")
        assert p.tel.rig.tracking is False
    finally:
        await p.run.close()


async def test_control_a_re_read_that_is_still_unconfirmed_asks(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same pause leaning on the same idle stop, on a link
    that never takes the stop, and the idle stop's task is ended at 220 s,
    between the pause's due times, with the stop unconfirmed. At 250 s the
    pause reads the stop back, finds it still unconfirmed, and asks, and
    goes on asking once a minute while the pause lasts: the re-read that
    #471 put in front of the ask does not take the ask's place.

    Mutant "the re-read replaces the ask" (the ask after the re-read made
    ``elif unconfirmed:``, so the due time that re-reads asks nothing):
    RED (observed) -
        AssertionError: the pause did not ask its unconfirmed stop at its
        first due time after the idle stop ended, and once a minute after
        it: its asks at [130.0, 310.0, 370.0] s
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=10 * SAFE_AT)
    try:
        first = await p.decide_the_idle_stop()
        ender = _cancel_the_idle_stop_at(p, ENDED_AT)
        await p.rain()
        await ender
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        task, _epoch = p.at_pause[0]
        retries = p.rel(p.asks_by("retry"))
        assert task is first and first.cancelled() \
            and retries == [0.0, 60.0, 120.0, 180.0], (
                f"premise: the pause began with the idle stop asking, and its "
                f"task was ended unconfirmed at {ENDED_AT:g} s: its asks at "
                f"{retries} s")
        own = p.rel(p.asks_by(RAIN, lo=start, hi=end))
        want = [RAIN_AT] + [RAIN_AT + k * RETRY for k in (2, 3, 4)]
        assert own == want, (
            f"the pause did not ask its unconfirmed stop at its first due "
            f"time after the idle stop ended, and once a minute after it: "
            f"its asks at {own} s")
        assert p.tel.rig.tracking is True, (
            "premise: the link never took the stop")
    finally:
        await p.run.close()


async def test_control_a_pause_that_never_leaned_reads_nothing_before_it_asks(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. A plain pause with no idle stop decided: nothing else asks,
    so the pause never leans, and each of its asks is made with no read in
    front of it, as before #471: one tracking read per ask, its read-back,
    at the ask's own instant. The link takes the stop at 220 s, so the ask
    at 250 s is the last.

    Mutant "always re-read first" (the re-read's ``if leaned:`` made ``if
    True:``, so every due time reads the stop before it asks): RED
    (observed) -
        AssertionError: the pause read the stop more than once at an ask:
        its asks at [190.0, 250.0] s, its tracking reads at [130.0, 190.0,
        190.0, 250.0, 250.0, 251.0, 252.0, 253.0, 254.0] s
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=RAIN_AT + 90.0)
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        assert p.asks_by("retry") == [], "premise: no idle stop was decided"
        later = [t for t in p.asks_by(RAIN, lo=start, hi=end) if t > start]
        assert p.rel(later) == [RAIN_AT + RETRY, RAIN_AT + 2 * RETRY], (
            f"premise: the pause asked its stop again twice, a minute "
            f"apart: {p.rel(later)}")
        reads = p.reads_by(RAIN, lo=start, hi=end)
        at_asks = [len([r for r in reads if r == t]) for t in later]
        assert at_asks == [1, 1], (
            f"the pause read the stop more than once at an ask: its asks at "
            f"{p.rel(later)} s, its tracking reads at {p.rel(reads)} s")
    finally:
        await p.run.close()
