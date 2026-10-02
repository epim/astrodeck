# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A refused auto-reopen roof close keeps the idle stop's retries, and the
open-sky pause reads its own stop back (#345, the #306 follow-up; spec 6.17
and 5.8).

The auto-reopen roof close ends the idle stop's task before it parks (#306,
`_close_for_reopen`), so no retry writes into its park. When that close is
then refused (its park fails, so `close_observatory` will not move the
roof), the run falls back to the open-sky pause (`_park_hold_pause`,
INVARIANT 2), and the pause stopped tracking once, through `_park_hold`, and
read nothing back. So on the flaky link that had left the idle stop
unconfirmed, the likely link for a refused close too, nothing asked for the
stop again for the whole pause: a link that recovered mid-pause left the
mount tracking, unwatched, until the weather cleared. The plain pause path
never ended the task, so its retries went on: two roads into one pause that
differed. The "safety rides value paths" class: the retry rode the task's
lifetime, which the close ended, while the hazard went on.

NOW a refused or failed close re-arms the idle stop's retries on a fresh
epoch before the pause (`_rearm_idle_stop`), and the pause reads its own
stop back and, while the mount has not confirmed it, asks again at most once
per ``IDLE_STOP_RETRY_S``, leaving the asking to an idle stop's task while
one is alive. Since #393 a close that an Abort cuts short re-arms them as
well, for the Abort to complete (test_abort_in_reopen_close_completes_stop.py,
which drives this file's `_Pause`).

THE HARNESS is test_idle_park_hold's clocked simulator with no run. The
idle stop is decided on the engine (`_idle_park_hold`); the unsafe verdict
is handed to `_on_unsafe` from a task of the test's that the driver clocks,
named ``RAIN``, at ``RAIN_AT`` fake seconds; the monitor's reading is a
script, rain until ``SAFE_AT``. THE FLAKY LINK (`_flaky_link`): until
``accept_at`` a ``set_tracking(False)`` goes out and does not take (the
mount tracks on and reads tracking) and a park fails; from then on both
work. It is intermittent by construction: a dead link would not show
whether anything asks again once the link is back. Every mount call is
recorded with the fake time and the task that made it. The site is a
fixture, never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (scratchpad s4-enga-mut), never in the shared tree (#254).
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.devices.base import DeviceError, DomeShutterState, SafetyReading

from test_idle_park_hold import _Clocked, sim_hub, temp_store  # noqa: F401

RETRY = engine_mod.IDLE_STOP_RETRY_S
POLL_S = engine_mod.SAFETY_PAUSE_POLL_S
#: The task that hands the engine its unsafe verdict, and so runs the close
#: and the pause.
RAIN = "rain"
#: Fake seconds after the idle stop is decided: when it rains (two retries
#: of the unconfirmed stop in, at 60 s and 120 s), and when it is safe again.
RAIN_AT = 130.0
SAFE_AT = 400.0


class _Pause:
    """The engine, with no run, on the clocked simulator, its safety config
    ``cfg``, and the records these cases read: ``asks`` (fake time, task) of
    every ``set_tracking(False)``, ``reads`` (fake time, task) of every
    tracking read, the pause's own spell (``paused``, ``released``) and, at
    the moment the pause began, the idle-stop task and epoch (``at_pause``).
    """

    def __init__(self, hub, monkeypatch, *, roof: bool, accept_at: float):
        self.run = _Clocked(hub, monkeypatch, horizon_s=3600.0)
        run = self.run
        engine = self.engine = run.engine
        engine._cfg = AppConfig(safety=SafetyConfig(
            enabled=True, on_unsafe="pause", unsafe_consecutive=1,
            resume_safe_consecutive=1, max_pause_min=0,
            sky_fallback_hold=False, close_dome_on_unsafe=roof,
            reopen_dome_when_safe=roof))
        self.t0 = run.t0
        self.dome = hub.devices.get("dome")
        assert self.dome is not None and self.dome.connected, (
            "premise: the sim has a roof")
        self.tel = hub.devices["telescope"]
        self.asks: list[tuple[float, str]] = []
        self.reads: list[tuple[float, str]] = []
        self.paused: list[float] = []
        self.released: list[float] = []
        self.at_pause: list[tuple[asyncio.Task | None, int]] = []
        _flaky_link(self, monkeypatch, accept_at=self.t0 + accept_at)

        async def safety_reading():
            t = run.clock.t
            if t < self.t0 + SAFE_AT:
                return SafetyReading(is_safe=False, reason="rain sensor",
                                     source="script", ts=t)
            return SafetyReading(is_safe=True, source="script", ts=t)

        monkeypatch.setattr(hub, "safety_reading", safety_reading)
        inner = engine._park_hold_pause

        async def park_hold_pause(reason, target):
            task = engine._idle_stop_task
            self.at_pause.append((task if task is not None
                                  and not task.done() else None,
                                  engine._idle_stop_epoch))
            self.paused.append(run.clock.t)
            try:
                return await inner(reason, target)
            finally:
                self.released.append(run.clock.t)

        monkeypatch.setattr(engine, "_park_hold_pause", park_hold_pause)

    async def decide_the_idle_stop(self) -> asyncio.Task:
        """The idle watch decides the stop at ``t0``; its task is the
        engine's, which the driver clocks."""
        await self.engine._idle_park_hold("test: the idle clock ran out")
        task = self.engine._idle_stop_task
        assert task is not None, "premise: the idle stop has its task"
        return task

    async def rain(self) -> None:
        """At ``RAIN_AT``, the unsafe verdict, from a task the driver clocks;
        returns when the engine's answer to it (the close, the pause) has."""
        run, engine = self.run, self.engine

        async def the_rain():
            await engine_mod.asyncio.sleep(RAIN_AT)
            await engine._on_unsafe("rain sensor", target=None)

        task = asyncio.get_running_loop().create_task(the_rain(), name=RAIN)
        run.also.add(task)
        try:
            await asyncio.wait_for(task, 30.0)
        finally:
            run.also.discard(task)

    def rel(self, ts) -> list[float]:
        return [round(t - self.t0, 2) for t in ts]

    def asks_by(self, who: str, lo: float = float("-inf"),
                hi: float = float("inf")) -> list[float]:
        return [t for t, w in self.asks if w == who and lo <= t <= hi]

    def reads_by(self, who: str, lo: float = float("-inf"),
                 hi: float = float("inf")) -> list[float]:
        return [t for t, w in self.reads if w == who and lo <= t <= hi]


def _flaky_link(p: _Pause, monkeypatch, *, accept_at: float) -> None:
    """The sim mount on a link that drops the stop and the park until
    ``accept_at`` (fake wall time): a ``set_tracking(False)`` goes out (the
    harness's spy records it) and does not take, so the mount tracks on and
    reads tracking; a park raises. From ``accept_at`` both work."""
    run, tel = p.run, p.tel
    spy_set, spy_get, real_park = tel.set_tracking, tel.get_tracking, tel.park

    async def set_tracking(on):
        if not on:
            p.asks.append((run.clock.t, run.who()))
        await spy_set(on)
        if not on and run.clock.t < accept_at:
            tel.rig.tracking = True

    async def get_tracking():
        p.reads.append((run.clock.t, run.who()))
        return await spy_get()

    async def park():
        if run.clock.t < accept_at:
            raise DeviceError("the mount's link dropped the park")
        return await real_park()

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "park", park)


def _premise_retrying(p: _Pause) -> None:
    before = p.asks_by("retry", hi=p.t0 + RAIN_AT)
    assert len(before) >= 2, (
        f"premise: the idle stop was unconfirmed and retrying before the "
        f"rain: its asks at {p.rel(before)}")


# ------------------------------------------------- a refused roof close

async def test_a_refused_close_re_arms_the_idle_stop_and_the_pause_reads_back(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#345's reproduction. The idle stop is unconfirmed and retrying on the
    flaky link; the rain opens an auto-reopen roof close, which ends that
    task, and the close's park fails, so the roof close is refused and the
    run falls back to the open-sky pause, whose own stop does not take
    either. Mid-pause the link recovers (``accept_at`` 150 s). Then:

    * the idle stop's retries were re-armed before the pause, on a fresh
      epoch, and its task asks the mount again during the pause, once the
      link is back, and reads the stop back: the mount stops tracking long
      before the sky clears;
    * the pause read its own stop back straight after making it.

    Mutant "no re-arm after a refused close" (the ``_rearm_idle_stop()``
    call in `_close_for_reopen` deleted): RED (observed) -
        AssertionError: nothing on the idle stop's own task asked the mount
        to stop again during the pause (130.0 s to 404.0 s): its asks after
        the link came back at []; the pause began with the idle-stop task
        None, epoch 0 (before the rain: 0)
    Mutant "pause stops once with no read-back" (the pause's read-back and
    its asks again deleted from `_park_hold_pause`: its first read-back
    replaced by ``unconfirmed = False``): RED (observed) -
        AssertionError: the pause did not read its own stop back: its stop
        at [130.0, 130.0] s, its tracking reads at []
    (the two asks at 130 s are the close's own stop and the pause's, both
    on the ``rain`` task at the same fake instant.) The control below passes
    under both mutants.
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=150.0)
    try:
        first = await p.decide_the_idle_stop()
        epoch = p.engine._idle_stop_epoch
        await p.rain()
        _premise_retrying(p)
        said = [m for _l, m, _s in bus_lines]
        assert any("roof close refused/failed" in m for m in said), (
            "premise: the roof close was refused")
        assert await p.dome.shutter_state() is not DomeShutterState.CLOSED
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        assert round(start - p.t0, 6) >= RAIN_AT and end - p.t0 >= SAFE_AT
        task, pause_epoch = p.at_pause[0]
        back = p.t0 + 150.0
        asked = p.asks_by("retry", lo=back, hi=end)
        read = [t for t in p.reads_by("retry", lo=back, hi=end)
                if asked and t >= asked[0]]
        assert asked and read, (
            f"nothing on the idle stop's own task asked the mount to stop "
            f"again during the pause ({round(start - p.t0, 2)} s to "
            f"{round(end - p.t0, 2)} s): its asks after the link came back "
            f"at {p.rel(asked)}; the pause began with the idle-stop task "
            f"{None if task is None else task.get_name()}, epoch "
            f"{pause_epoch} (before the rain: {epoch})")
        assert task is not None and task is not first, (
            "the retries were not re-armed on a task of their own")
        assert pause_epoch == epoch + 1, (
            f"the re-armed retries are not on a fresh epoch: {epoch} before, "
            f"{pause_epoch} at the pause")
        stopped = [t for t, w in p.asks if t >= back]
        assert not p.tel.rig.tracking and stopped and stopped[0] < end, (
            f"the mount was still tracking when the pause ended: asks after "
            f"the link came back at {p.rel(stopped)}")
        own = p.asks_by(RAIN, lo=start, hi=start)
        own_reads = [t for t in p.reads_by(RAIN, lo=start)
                     if own and t >= own[0]]
        assert own and own_reads and own_reads[0] < start + POLL_S, (
            f"the pause did not read its own stop back: its stop at "
            f"{p.rel(own)} s, its tracking reads at {p.rel(own_reads)}")
    finally:
        await p.run.close()


# ------------------------------------------------ the pause's own asks

async def test_the_pause_asks_its_unconfirmed_stop_again_once_a_minute(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """A plain pause (no roof), with no idle stop decided: nothing but the
    pause is there to ask. Its stop does not take, nor does its first ask
    again; the link recovers at 90 s into the pause. The pause reads its
    stop back, says once that the mount has not confirmed it, and asks again
    at most once per ``IDLE_STOP_RETRY_S``, each ask read back, until one
    takes: three asks in all, a minute or more apart, and none after.

    Mutant "pause stops once with no read-back" (as above): RED (observed) -
        AssertionError: the pause's stop was asked at [130.0] s and the
        mount is still tracking at the end of the pause: nothing asked
        again once the link came back at 220.0 s
    Mutant "the pause asks on every poll" (the ``time.time() >= next_ask``
    test dropped from `_park_hold_pause`, so every ``SAFETY_PAUSE_POLL_S``
    poll asks): RED (observed) -
        AssertionError: the pause asked its stop at [130.0, 135.0, 140.0,
        145.0, 150.0, 155.0, 160.0, 165.0, 170.0, 175.0, 180.0, 185.0,
        190.0, 195.0, 200.0, 205.0, 210.0, 215.0, 220.0] s: not three
        asks at least a minute apart
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=RAIN_AT + 90.0)
    try:
        await p.rain()
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        asked = p.asks_by(RAIN, lo=start, hi=end)
        assert asked and (p.tel.rig.tracking is False
                          or len(asked) > 1), (
            f"the pause's stop was asked at {p.rel(asked)} s and the mount "
            f"is still tracking at the end of the pause: nothing asked again "
            f"once the link came back at {round(RAIN_AT + 90.0, 2)} s")
        gaps = [b - a for a, b in zip(asked, asked[1:])]
        assert len(asked) == 3 and all(g >= RETRY for g in gaps), (
            f"the pause asked its stop at {p.rel(asked)} s: not three asks "
            f"at least a minute apart")
        for t in asked:
            assert [r for r in p.reads_by(RAIN) if t <= r < t + POLL_S], (
                f"the ask at {round(t - p.t0, 2)} s was not read back")
        assert p.tel.rig.tracking is False and asked[-1] < end
        said = [m for _l, m, _s in bus_lines
                if "did not confirm the safety pause's stop" in m]
        assert len(said) == 1, said
        assert p.asks_by("retry") == [], "premise: no idle stop was decided"
    finally:
        await p.run.close()


# ------------------------------------------------------------- control

async def test_control_a_plain_pause_keeps_the_idle_stop_s_retries(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The same unconfirmed idle stop, and a plain pause (no roof),
    as before #345: the pause does not end the idle stop's task, which goes
    on asking on its own clock, once a minute from when it was decided,
    through the pause, and reads back the ask that takes once the link
    recovers (200 s). While that task is alive the pause asks nothing more
    itself: one ask a minute, not two. Nothing is re-armed and no epoch
    moves. It passes on the code before #345 as well.
    """
    p = _Pause(sim_hub, monkeypatch, roof=False, accept_at=200.0)
    try:
        first = await p.decide_the_idle_stop()
        epoch = p.engine._idle_stop_epoch
        await p.rain()
        _premise_retrying(p)
        start, end = p.paused[0], p.released[0]
        task, pause_epoch = p.at_pause[0]
        assert task is first and pause_epoch == epoch, (
            f"the plain pause did not find the idle stop's own task asking: "
            f"{task} at epoch {pause_epoch} (the first {first}, epoch "
            f"{epoch})")
        retries = p.rel(p.asks_by("retry"))
        assert retries == [0.0, 60.0, 120.0, 180.0, 240.0], (
            f"the idle stop's retries did not go on at their own cadence "
            f"through the pause: {retries}")
        assert first.done() and not first.cancelled(), (
            "the idle stop's task did not end by confirming the stop")
        confirm = [r for r in p.reads_by("retry") if r >= p.t0 + 240.0]
        assert confirm, "the ask that took was not read back"
        own = p.asks_by(RAIN, lo=start, hi=p.t0 + 240.0)
        assert own == [start], (
            f"the pause asked again itself while the idle stop's task was "
            f"asking: its asks at {p.rel(own)} s, its own stop at "
            f"{round(start - p.t0, 2)} s")
        assert p.tel.rig.tracking is False and end - p.t0 >= SAFE_AT
    finally:
        await p.run.close()


async def test_a_refused_close_with_no_idle_stop_re_arms_nothing(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The same refused roof close on the flaky link, with no idle stop
    decided: the rain comes mid-run, so the close ends no task, and there is
    nothing of the idle stop's to start again. Nothing is re-armed and no
    epoch moves; the pause alone asks for its unconfirmed stop, a minute
    after its own and a minute after that, when the link is back (220 s),
    and reads the one that takes back. (Verifier's case: the ``ended``
    condition in `_close_for_reopen` had no test, so the mutant below
    survived the rest of the suite. Run in the private scratch copy
    s4-enga-verify-mut.)

    Mutant "re-arm whether or not an idle stop was ended" (``and ended``
    dropped from the re-arm's condition in `_close_for_reopen`): RED
    (observed) -
        AssertionError: a refused close re-armed an idle stop that was
        never decided: the pause began with the task idle-stop-retry, epoch
        1; the pause's own asks after its first at [310.0]
    (the pause left the asking to the task it found alive, and asked once
    more itself a minute after that task had confirmed the stop.) Mutant
    "pause stops once with no read-back" (as above): RED here too.
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=RAIN_AT + 90.0)
    try:
        await p.rain()
        said = [m for _l, m, _s in bus_lines]
        assert any("roof close refused/failed" in m for m in said), (
            "premise: the roof close was refused")
        assert p.paused and p.released, "premise: the pause ran"
        start, end = p.paused[0], p.released[0]
        task, pause_epoch = p.at_pause[0]
        later = [t for t in p.asks_by(RAIN, lo=start, hi=end) if t > start]
        assert task is None and pause_epoch == 0, (
            f"a refused close re-armed an idle stop that was never decided: "
            f"the pause began with the task "
            f"{None if task is None else task.get_name()}, epoch "
            f"{pause_epoch}; the pause's own asks after its first at "
            f"{p.rel(later)}")
        assert p.asks_by("retry") == [], p.rel(p.asks_by("retry"))
        assert p.rel(later) == [RAIN_AT + RETRY, RAIN_AT + 2 * RETRY], (
            f"the pause did not ask for its stop itself, once a minute: "
            f"{p.rel(later)}")
        assert [r for r in p.reads_by(RAIN) if later[-1] <= r < later[-1]
                + POLL_S], "the ask that took was not read back"
        assert p.tel.rig.tracking is False
    finally:
        await p.run.close()


async def test_the_re_armed_retries_do_not_hold_an_abort_for_a_first_attempt(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The idle stop's FIRST attempt is still in flight when the rain opens
    the roof close: its guider stop has not come back, so it has not asked
    the mount anything yet. The close ends that task, its park fails, the
    close is refused, and the retries are re-armed. The re-armed task makes
    no first attempt of its own, so a run's end must not wait for one
    (``_idle_stop_first_made``, `_finish_idle_stop`): an Abort after the
    pause ends the re-armed retries at once, asks the mount once more, reads
    it back and says so. Waiting for a first attempt that nothing will ever
    make held the Abort for ``IDLE_STOP_FINISH_S`` (180 s) and then said, of
    a stop asked for all through the pause, that its first attempt had not
    finished. The link never takes the stop in this case, so the retries
    never end on their own. (Verifier's case: the ``_idle_stop_first_made``
    line in `_rearm_idle_stop` had no test, and its mutant survived every
    idle-stop and roof-close suite. Run in the private scratch copy
    s4-enga-verify2-mut.)

    Mutant "re-arm without first_made" (``self._idle_stop_first_made =
    True`` deleted from `_rearm_idle_stop`): RED (observed) -
        AssertionError: an Abort after a refused close waited 180.25 s for
        a first attempt the re-armed retries never make: ['the idle stop had
        not finished its first attempt when the run ended, so the mount was
        asked to stop once more, and it has still not confirmed it (it still
        reports tracking); nothing will ask it again, so it may still be
        tracking']
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=10 * SAFE_AT)
    run = p.run
    guider = sim_hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider")
    real_stop = guider.stop_guiding
    never = asyncio.Event()
    hung: list[float] = []

    async def stop_guiding(*a, **kw):
        # The first guider stop is the idle stop's first attempt, and it
        # does not come back; every later one (the close's, the pause's)
        # answers.
        if not hung:
            hung.append(run.clock.t)
            await run.park_until(never)
        return await real_stop(*a, **kw)

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    try:
        first = await p.decide_the_idle_stop()
        await p.rain()
        assert hung and first.cancelled(), (
            f"premise: the close ended the idle stop inside its first "
            f"attempt: its guider stop asked at {p.rel(hung)}, the task "
            f"cancelled: {first.cancelled()}")
        assert p.asks_by("retry", hi=p.t0 + RAIN_AT) == [], (
            "premise: the first attempt never reached the mount")
        said = [m for _l, m, _s in bus_lines]
        assert any("roof close refused/failed" in m for m in said), (
            "premise: the roof close was refused")
        task, _epoch = p.at_pause[0]
        assert task is not None and task is not first and not task.done(), (
            "premise: the retries were re-armed and are still asking")
        assert p.tel.rig.tracking, "premise: the link never took the stop"
        begun = run.clock.t
        ending = asyncio.get_running_loop().create_task(p.engine.abort(),
                                                        name="abort")
        run.also.add(ending)
        try:
            await asyncio.wait_for(ending, 30.0)
        finally:
            run.also.discard(ending)
        took = run.clock.t - begun
        said = [m for _l, m, _s in bus_lines]
        cut = [m for m in said if "had not finished its first attempt" in m]
        assert took <= engine_mod.IDLE_STOP_FINISH_POLL_S and not cut, (
            f"an Abort after a refused close waited {took:g} s for a first "
            f"attempt the re-armed retries never make: {cut}")
        once = [m for m in said if m.startswith(
            "the run is ending and the mount had not confirmed the idle stop")]
        assert len(once) == 1, once
        assert task.done(), "the re-armed retries outlived the Abort"
        asked = p.asks_by("abort", lo=begun)
        read = [r for r in p.reads_by("abort", lo=begun)
                if asked and r >= asked[0]]
        assert len(asked) == 1 and read, (
            f"the Abort did not ask the mount once more and read it back: "
            f"asks at {p.rel(asked)}, reads at {p.rel(read)}")
    finally:
        await p.run.close()


async def test_a_refused_close_after_a_confirmed_idle_stop_re_arms_nothing(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL for the re-arm. The link takes the stop, so the idle stop's
    task confirms it and ends long before the rain; only the park fails, so
    the roof close is still refused. The close found no idle stop still
    asking (`_cancel_idle_stop_retry` answers False for a task that has
    ended), so nothing is re-armed and no epoch moves; the pause's own stop
    takes and is read back, and nothing asks again. (Verifier's case: the
    answer for a task that had ended had no test, and its mutant survived
    every idle-stop and roof-close suite. Run in the private scratch copy
    s4-enga-verify2-mut.)

    Mutant "a done idle task counts as alive" (``alive = True`` in
    `_cancel_idle_stop_retry`, so it answers True for any task it found):
    RED (observed) -
        AssertionError: a refused close re-armed an idle stop that had
        already confirmed its stop: the pause began with the task
        idle-stop-retry, epoch 1 (before the rain: 0); retry asks after the
        rain at [190.0]
    """
    p = _Pause(sim_hub, monkeypatch, roof=True, accept_at=0.0)

    async def park():
        raise DeviceError("the mount refused the park")

    monkeypatch.setattr(p.tel, "park", park)
    try:
        first = await p.decide_the_idle_stop()
        epoch = p.engine._idle_stop_epoch
        await p.rain()
        assert first.done() and not first.cancelled(), (
            "premise: the idle stop's task ended by confirming the stop")
        before = p.asks_by("retry", hi=p.t0 + RAIN_AT)
        assert before == [p.t0], (
            f"premise: the idle stop asked once and took: {p.rel(before)}")
        said = [m for _l, m, _s in bus_lines]
        assert any("roof close refused/failed" in m for m in said), (
            "premise: the roof close was refused")
        task, pause_epoch = p.at_pause[0]
        later = p.asks_by("retry", lo=p.t0 + RAIN_AT)
        assert task is None and pause_epoch == epoch and later == [], (
            f"a refused close re-armed an idle stop that had already "
            f"confirmed its stop: the pause began with the task "
            f"{None if task is None else task.get_name()}, epoch "
            f"{pause_epoch} (before the rain: {epoch}); retry asks after the "
            f"rain at {p.rel(later)}")
        # The close's own stop and the pause's are both at the pause's first
        # instant, on the rain task; nothing after them.
        start, end = p.paused[0], p.released[0]
        again = [t for t in p.asks_by(RAIN, lo=start, hi=end) if t > start]
        assert again == [], (
            f"the pause asked again for a stop it had read back: at "
            f"{p.rel(again)}")
        assert not [m for m in said
                    if "did not confirm the safety pause's stop" in m]
        assert p.tel.rig.tracking is False
    finally:
        await p.run.close()


async def test_a_pause_with_no_mount_says_nothing_about_a_stop(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL for the pause's read-back. A rig with no telescope (a
    camera-only run) pauses and the weather clears at the first look: there
    is no mount to read back, as the idle stop finds (`_idle_stop_retry`), so
    the pause says nothing about a stop it could not confirm, where a read
    of a mount that is not there ("its tracking state cannot be read") would
    have promised a minute-by-minute ask of nothing for the whole pause.
    (Verifier's case: the telescope guard in `_pause_stop_unconfirmed` had
    no test, and its mutant survived every pause suite. Run in the private
    scratch copy s4-enga-verify2-mut.)

    Mutant "pause read-back with no mount guard" (the telescope guard at the
    top of `_pause_stop_unconfirmed` deleted): RED (observed) -
        AssertionError: a pause with no mount said it could not confirm a
        stop: ["the mount did not confirm the safety pause's stop of
        tracking (its tracking state cannot be read) — asking again about
        once a minute while the pause lasts"]
    """
    import time

    from astrodeck.sequence.engine import SequenceEngine
    tel = sim_hub.devices.pop("telescope")
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(safety=SafetyConfig(
        enabled=True, on_unsafe="pause", unsafe_consecutive=1,
        resume_safe_consecutive=1, max_pause_min=0, sky_fallback_hold=False))

    async def safety_reading():
        return SafetyReading(is_safe=True, source="script", ts=time.time())

    monkeypatch.setattr(sim_hub, "safety_reading", safety_reading)
    try:
        await asyncio.wait_for(engine._park_hold_pause("rain sensor", None),
                               30.0)
        said = [m for _l, m, _s in bus_lines]
        assert any(m == "conditions safe again — resuming" for m in said), (
            "premise: the pause ran and released")
        stop = [m for m in said if "safety pause's stop" in m]
        assert stop == [], (
            f"a pause with no mount said it could not confirm a stop: {stop}")
    finally:
        await tel.disconnect()


async def test_a_refused_close_with_no_mount_re_arms_nothing(
        sim_hub, monkeypatch, bus_lines):
    """A rig with a roof and no telescope (a camera-only run under a dome).
    A task is alive in the idle stop's place when the close ends it, and the
    roof close then fails (the shutter refuses). There is no mount to ask or
    read back, as `_idle_stop_retry` finds, so nothing is re-armed: the
    retry loop would otherwise ask a missing mount once a minute for the
    rest of the pause, and read back "unknown" every time.

    Mutant "re-arm with no mount" (the telescope guard at the top of
    `_rearm_idle_stop` deleted): RED (observed) -
        AssertionError: a refused close re-armed the idle stop on a rig with
        no telescope: idle-stop-retry
    """
    from astrodeck.sequence.engine import SequenceEngine
    tel = sim_hub.devices.pop("telescope")
    dome = sim_hub.devices["dome"]

    async def close_shutter():
        raise DeviceError("the shutter motor refused")

    monkeypatch.setattr(dome, "close_shutter", close_shutter)
    engine = SequenceEngine(sim_hub)
    engine._idle_stop_task = asyncio.get_running_loop().create_task(
        asyncio.sleep(3600.0), name="idle-stop-retry")
    try:
        closed = await engine._close_for_reopen(dome, "rain sensor")
        assert closed is False, "premise: the roof close failed"
        task = engine._idle_stop_task
        assert task is None, (
            f"a refused close re-armed the idle stop on a rig with no "
            f"telescope: {task.get_name()}")
    finally:
        task = engine._idle_stop_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await tel.disconnect()
