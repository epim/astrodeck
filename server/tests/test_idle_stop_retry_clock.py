"""An idle stop the mount did not confirm is asked again on its OWN clock
(#189 A3, #210 engine half).

The idle park-hold (#165) reads its stop back, and a stop the mount did not
confirm used to re-open the latch so that the wait loop's very next tick
asked again, through `_park_hold`. On a dead serial link (the #133 class)
one ask is a ``set_tracking(False)`` and a read-back that each hang for
``MOUNT_QUERY_TIMEOUT_S``, so every tick of the wait loop took a minute, and
the loop's other job, the safety gate every ``SCHEDULE_WAIT_STEP_S``, ran once
a minute for as long as the link stayed dead: weather unwatched on the one
night something is already wrong. And every retry stopped the guider again,
which re-stamps an idle native guider's saved PPEC window (#210).

Now the stop is made by a task of its own (`_idle_stop_retry`), and nothing
of it by the wait loop. Its first act is the first attempt, `_park_hold`,
guider and mount, made the moment the stop is decided (#216: this used to be
made inline, by the tick or the scheduler, and on a dead link it cost the
safety gate three minutes). An unconfirmed stop is then asked again at most
once per ``IDLE_STOP_RETRY_S``, with ``set_tracking(False)`` and the read-back
only. Every path that ends the spell cancels the task and waits for it: the
next `_setup_target` before it restores tracking, the end of the run, `abort`,
and the next `start`.

THE HARNESS IS test_idle_park_hold's clocked simulator, whose timer heap wakes
the run task and the retry task each on its own schedule. That is the point
of it here: a clock that summed both tasks' sleeps would charge the retry's
hangs to the wait loop and could not tell a retry on its own task from one
inline in the tick. The first case proves it can.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import ExposureStep, SequencePlan, Target

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    HANG, RETRY, TEARDOWN, TICK, _a_mount_that_will_not_stop, _asks,
    _Clocked, _constraint_waiter, _hhmm, _lst_h, _park_lines, _plan, _ra_at,
    _target, _unconfirmed_lines, sim_hub, temp_store)

#: How far past one tick a gap between two safety-gate calls may run. The
#: harness wakes the wait loop on the dot, so anything real is far above it.
EPS = 0.5


def _retry_asks(run: _Clocked) -> list[float]:
    return [t for t, who in _asks(run) if who == "retry"]


# --------------------------------------------------- the wait loop keeps its tick

#: How long a wedged guider's stop holds whoever asked: `_park_hold`'s bound.
GUIDE_HANG = engine_mod.GUIDE_OP_TIMEOUT_S


def _a_guider_that_will_not_stop(run: _Clocked, monkeypatch
                                 ) -> list[tuple[float, str]]:
    """A guider wedged in its stop: ``stop_guiding`` holds the asking engine
    task for its whole bound, ``GUIDE_OP_TIMEOUT_S`` of fake time, and then
    times out, which `_park_hold` swallows. Returns the (fake time, which
    task) of every call. Once the run is frozen at the horizon the real stop
    runs, so the wind-down after the test's abort is not graded."""
    guider = run.hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider for `_park_hold` to stop")
    calls: list[tuple[float, str]] = []
    real_stop = guider.stop_guiding

    async def stop_guiding(*a, **kw):
        if run.frozen.is_set():
            return await real_stop(*a, **kw)
        calls.append((run.clock.t, run.who()))
        if run._is_engine_task(asyncio.current_task()):
            await run._park(GUIDE_HANG)
        raise asyncio.TimeoutError()

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    return calls


def _charlie_three_hours_away(t0: float) -> Target:
    """The scheduler's planned-wait rule: a target whose window opens three
    hours out, so the stop is decided the moment the last exposure ends."""
    return _target("Charlie", _ra_at(-3.0, t0), 40.0, start_mode="time",
                   start_time=_hhmm(t0 + 3 * 3600))


@pytest.mark.parametrize("caller", ["the wait tick", "the planned-wait rule"])
async def test_the_safety_gate_keeps_its_tick_through_the_first_attempt(
        sim_hub, monkeypatch, bus_lines, caller):
    """(a) #216. Alpha shoots, and the mount's link is dead:
    ``set_tracking(False)`` and ``get_tracking`` each hang for their whole
    bound, and the guider's stop is wedged for its own, GUIDE_OP_TIMEOUT_S.
    The stop is decided by each of its two callers in turn: the wait tick's
    idle clock (Bravo waits two hours on a constraint), and the scheduler's
    planned-wait rule (Charlie opens three hours out). From the instant the
    stop is decided to the horizon, the safety gate never waits more than
    one tick between two looks: not through the first attempt, which costs
    the guider's hang, the stop's and the read-back's, and not through the
    retries beside it.

    ON THE RETRY'S TASK, AT ONCE. The first attempt is the task's own first
    act: the guider is stopped at the instant the stop is decided, on that
    task, once, and the mount is asked as soon as the guider's stop gives up.
    The retries follow IDLE_STOP_RETRY_S after each ask ends
    (test_a_stop_the_mount_did_not_take_is_asked_again_on_its_own_clock).

    THE WINDOW STARTS AT THE DECISION, not at the first gate inside
    `_wait_until`. The planned-wait rule decides in the scheduler, before
    the wait begins, so a first attempt made inline there leaves every gap
    inside the wait at one tick and delays the first one instead.

    Mutant "first attempt inline" (`_idle_park_hold` awaits `_park_hold`
    and the read-back itself, and hands the task only the retries, as
    before #216): RED in both cases, with the gap the three hangs cost the
    loop (observed) -
        [the wait tick] AssertionError: the safety gate waited 185.0 s
        between two looks once the stop was decided (one tick is 5 s);
        gaps over a tick at [0.0] s after the decision, the guider stopped
        at [(0.0, 'run')] s
        [the planned-wait rule] AssertionError: the safety gate waited
        180.0 s between two looks once the stop was decided (one tick is
        5 s); gaps over a tick at [0.0] s after the decision, the guider
        stopped at [(0.0, 'run')] s
    Mutant "retry inline in the tick" (the task makes the first attempt and
    then hands the retries to `_idle_hold_tick`, which asks at most once per
    IDLE_STOP_RETRY_S): RED in both cases, a 65 s gap at every retry, the
    stop's hang, the read-back's and the tick (observed, the same text in
    both) -
        AssertionError: the safety gate waited 65.0 s between two looks once
        the stop was decided (one tick is 5 s); gaps over a tick at [240.0,
        360.0, 480.0, 600.0] s after the decision, the guider stopped at
        [(0.0, 'retry')] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=900.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    waits = (_constraint_waiter("Bravo", t0) if caller == "the wait tick"
             else _charlie_three_hours_away(t0))
    guider_stops = _a_guider_that_will_not_stop(run, monkeypatch)
    _a_mount_that_will_not_stop(run, readback="unreadable")
    try:
        await run.night(_plan(a, waits))
        idle = run.exposure_end("Alpha")
        assert guider_stops, "premise: the stop was decided and made"
        decided = guider_stops[0][0]
        lo, hi = ((TEARDOWN, TEARDOWN + TICK) if caller == "the wait tick"
                  else (0.0, TICK))
        assert lo <= decided - idle <= hi, (
            f"premise: {caller} decided the stop "
            f"{decided - idle:.1f} s after Alpha's last exposure")
        asks = [(t, who) for t, who in _asks(run) if t >= decided]
        assert len(asks) >= 3, (
            f"premise: the first ask and at least two retries, each hanging: "
            f"{[(round(t - decided, 1), w) for t, w in asks]}")
        # In fake seconds, not calls: the defect under test changes the count.
        window = [decided] + [t for t, _ctx in run.gates if t > decided]
        assert window[-1] - decided >= GUIDE_HANG + 2 * HANG + 2 * RETRY, (
            f"premise: the graded window spans the first attempt and the "
            f"retries after it ({window[-1] - decided:.0f} s)")
        gaps = [round(y - x, 1) for x, y in zip(window, window[1:])]
        assert max(gaps) <= TICK + EPS, (
            f"the safety gate waited {max(gaps):.1f} s between two looks once "
            f"the stop was decided (one tick is {TICK:.0f} s); gaps over a "
            f"tick at "
            f"{[round(window[i] - decided, 1) for i, g in enumerate(gaps) if g > TICK + EPS][:4]}"
            f" s after the decision, the guider stopped at "
            f"{[(round(t - decided, 1), w) for t, w in guider_stops]} s")
        assert guider_stops == [(decided, "retry")], (
            f"the guider is stopped once per spell, by the task that makes "
            f"the first attempt: {guider_stops}")
        assert asks[0] == (decided + GUIDE_HANG, "retry"), (
            f"the mount's first ask comes on the same task, as soon as the "
            f"guider's stop gives up: {asks[:2]}")
        assert len(_unconfirmed_lines(bus_lines)) == 1
        assert len(_park_lines(bus_lines)) == 1, _park_lines(bus_lines)[:3]
    finally:
        await run.close()


# ------------------------------------------------------ at most once per interval

async def test_the_retries_are_at_least_the_retry_interval_apart(
        sim_hub, monkeypatch, bus_lines):
    """(b) A mount whose ``set_tracking(False)`` hangs for its bound and whose
    read-back answers at once that it is still tracking. Its retries are at
    least IDLE_STOP_RETRY_S apart, however quickly each one comes back.

    Mutant "retry every tick" (`_idle_stop_retry` sleeps
    SCHEDULE_WAIT_STEP_S in place of IDLE_STOP_RETRY_S): RED (observed) -
        AssertionError: the retry asked again 35.0 s after the last ask;
        the interval is 60 s: gaps [35.0, 35.0, 35.0, 35.0, 35.0, 35.0]
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=900.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    _a_mount_that_will_not_stop(run, readback="still tracking")
    try:
        await run.night(_plan(a, b))
        offs = [t for t, _who in _asks(run)]
        assert len(offs) >= 4, (
            f"premise: asked again several times: {run.rel(offs, t0)} s")
        gaps = [round(y - x, 1) for x, y in zip(offs, offs[1:])]
        assert min(gaps) >= RETRY, (
            f"the retry asked again {min(gaps):.1f} s after the last ask; "
            f"the interval is {RETRY:.0f} s: gaps {gaps[:6]}")
        assert len(_unconfirmed_lines(bus_lines)) == 1
    finally:
        await run.close()


# ------------------------------------------------------------ never the guider

async def test_a_retry_never_stops_the_guider_again(sim_hub, monkeypatch,
                                                    bus_lines):
    """(c) #210, the engine half. `_park_hold` stops the guider and then the
    mount. The first attempt of a spell goes through it, so the guider is
    stopped once per spell; the retries ask the mount only, because a second
    stop on an idle native guider re-stamps its saved PPEC window as freshly
    fed and the next target restores the model out of phase. Two spells with a
    `_setup_target` between them and a mount that never takes a stop: one
    ``stop_guiding`` in each, however many retries each spell makes.

    Mutant "retry through _park_hold" (`_idle_stop_retry`'s retry loop
    awaits `_park_hold` in place of `_stop_tracking_quietly`): RED (observed;
    the ask counts take in the first attempt, made on the same task since
    #216) -
        AssertionError: the guider was stopped (6, 5) times in two idle
        spells whose retries asked the mount 6 and 5 times: stop_guiding at
        [120.0, 210.0, 300.0, 390.0, 480.0, 570.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=1200.0)
    t0 = run.t0
    guider = sim_hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider for `_park_hold` to stop")
    stops: list[float] = []
    real_stop = guider.stop_guiding

    async def stop_guiding(*a, **kw):
        if not run.frozen.is_set():
            stops.append(run.clock.t)
        return await real_stop(*a, **kw)

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0, ready_after_s=600.0)
    c = _constraint_waiter("Charlie", t0)
    _a_mount_that_will_not_stop(run, readback="still tracking")
    try:
        await run.night(_plan(a, b, c))
        idle_a, idle_b = run.exposure_end("Alpha"), run.exposure_end("Bravo")
        retried = _retry_asks(run)
        in_a = [t for t in retried if idle_a <= t < idle_b]
        in_b = [t for t in retried if t >= idle_b]
        assert len(in_a) >= 3 and len(in_b) >= 3, (
            f"premise: several retries in each spell ({len(in_a)}, "
            f"{len(in_b)})")
        per_spell = (len([t for t in stops if idle_a <= t < idle_b]),
                     len([t for t in stops if t >= idle_b]))
        assert per_spell == (1, 1), (
            f"the guider was stopped {per_spell} times in two idle spells "
            f"whose retries asked the mount {len(in_a)} and {len(in_b)} "
            f"times: stop_guiding at {run.rel(stops, idle_a)[:6]} s")
    finally:
        await run.close()


# --------------------------------------------- the next target's tracking stays on

async def test_setup_cancels_a_retry_in_flight_before_it_restores_tracking(
        sim_hub, monkeypatch, bus_lines):
    """(d) THE NEW HAZARD the retry task brings. It runs beside the wait loop,
    so it can be half way through a ``set_tracking(False)`` when the next
    target becomes ready. Here Bravo's constraint lifts while the retry's
    third ask is hanging; Bravo's setup must cancel that ask, and wait for
    it, before it turns tracking on, so that the last ``set_tracking`` before
    Bravo's first exposure ends is True. Bravo exposes for five minutes, three
    retry intervals, so a retry left running cannot miss it.

    Mutant "no cancel on setup" (the `_cancel_idle_stop_retry` call at the
    top of `_setup_target`'s telescope block deleted): RED (observed) -
        AssertionError: the last set_tracking before Bravo's first exposure
        ended was (690.0, False, 'retry'): a retry of the last spell's stop
        landed after setup turned tracking on; calls [(435.0, True, 'run'),
        (435.0, True, 'run'), (510.0, False, 'retry'), (600.0, False,
        'retry'), (690.0, False, 'retry')]
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=800.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    lim = 1.0
    b = Target(name="Bravo", ra_hours=(_lst_h(t0 + 432.0) + lim) % 24.0,
               dec_deg=40.0, center=False, autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=300.0, gain=100,
                                   count=1)])
    b.schedule.max_hour_angle_h = lim
    c = _constraint_waiter("Charlie", t0)
    _a_mount_that_will_not_stop(run, readback="still tracking")
    try:
        await run.night(_plan(a, b, c))
        starts = [t for n, t in run.exposure_starts if n == "Bravo"]
        ends = [t for n, t in run.exposure_ends if n == "Bravo"]
        assert starts and ends, (
            f"premise: Bravo was set up and shot: {run.exposure_starts}")
        setup_on = [t for t, on, who in run.tracking_calls
                    if on and t <= starts[0]]
        assert setup_on, "premise: Bravo's setup turned tracking on"
        before = [(round(t - t0, 1), on, who) for t, on, who in run.tracking_calls
                  if t <= ends[0]]
        assert before[-1][1] is True, (
            f"the last set_tracking before Bravo's first exposure ended was "
            f"{before[-1]}: a retry of the last spell's stop landed after "
            f"setup turned tracking on; calls {before[-5:]}")
        cut = [(round(t - t0, 1), call, who) for t, call, who in run.cut_hangs]
        assert cut and cut[0][1:] == ("set_tracking(False)", "retry"), (
            f"premise: the retry was half way through an ask when Bravo's "
            f"setup began, and was cut short: {cut}; retry asks at "
            f"{run.rel(_retry_asks(run), t0)} s, setup at "
            f"{run.rel(setup_on, t0)} s")
    finally:
        await run.close()


async def test_the_cancel_waits_for_a_stop_already_on_the_wire_to_land(
        sim_hub, monkeypatch, bus_lines):
    """(d), the AWAIT half. A cancel does not unsend a command: the serial link
    joins an exchange already on the wire before it lets a cancel through
    (``serial_link.request``'s CANCEL SAFETY), so a retry cancelled half way
    through its ``set_tracking(False)`` can still land that stop on its way
    out. `_cancel_idle_stop_retry` must therefore return only once the retry
    has finished, landing included, so the ``set_tracking(True)`` that
    `_setup_target` issues next is the last word. Case (d) cannot see this:
    its double's cancelled stop never lands, so "cancelled" and "cancelled and
    awaited" look the same there.

    The real `_idle_stop_retry` task, with the interval set to 0 so it asks
    again at once, and a mount double that refuses the spell's first stop
    (the task's first attempt) and whose retry ask then hangs until
    cancelled and lands the stop a few loop turns later. Then exactly what
    setup does: cancel the retry, and turn tracking on.

    Mutant "cancel without await" (`_cancel_idle_stop_retry` cancels the task
    and returns without the ``gather``): RED (observed) -
        AssertionError: the retry's stop landed after the set_tracking(True)
        that followed its cancel, so the next target is shot on a mount that
        is not tracking: effects [('on', 'test'), ('off landed', 'retry')];
        the retry was still running when the cancel returned
    """
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.0)
    engine = engine_mod.SequenceEngine(sim_hub)
    tel = sim_hub.devices["telescope"]
    real_set = tel.set_tracking
    await real_set(True)
    #: (what took effect on the mount, which task) in the order it did
    effects: list[tuple[str, str]] = []
    on_the_wire = asyncio.Event()
    stops: list[str] = []

    async def set_tracking(on):
        task = asyncio.current_task()
        who = "retry" if task.get_name() == "idle-stop-retry" else "test"
        if on:
            await real_set(True)
            effects.append(("on", who))
            return
        stops.append(who)
        if len(stops) == 1:
            # The task's first attempt (#216): the mount does not take it.
            raise asyncio.TimeoutError()
        on_the_wire.set()
        try:
            await asyncio.Event().wait()         # a link that never answers
        except asyncio.CancelledError:
            # The exchange already sent completes before the cancel is let
            # through, as the serial link's join does.
            for _ in range(5):
                await asyncio.sleep(0)
            await real_set(False)
            effects.append(("off landed", "retry"))
            raise

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    try:
        await engine._idle_park_hold("the next target is a long wait away")
        task = engine._idle_stop_task
        assert task is not None and not task.done(), "premise: a retry is alive"
        await asyncio.wait_for(on_the_wire.wait(), 5.0)
        assert stops == ["retry", "retry"], (
            f"premise: the first attempt and the retry now on the wire are "
            f"both the task's: {stops}")
        assert effects == [], f"premise: nothing landed while it hung: {effects}"

        await engine._cancel_idle_stop_retry()      # what setup does first
        running_at_return = not task.done()
        await tel.set_tracking(True)                 # and what it does next
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5.0)
        assert tel.rig.tracking is True, (
            f"the retry's stop landed after the set_tracking(True) that "
            f"followed its cancel, so the next target is shot on a mount that "
            f"is not tracking: effects {effects}; the retry was "
            f"{'still running' if running_at_return else 'finished'} when the "
            f"cancel returned")
        assert effects == [("off landed", "retry"), ("on", "test")], effects
        assert not running_at_return
        assert engine._idle_stop_task is None
    finally:
        await engine._cancel_idle_stop_retry()


async def test_a_cancel_the_guiders_stop_swallows_still_ends_the_task(
        sim_hub, monkeypatch, bus_lines):
    """(d), the cancel's other trap, which #216 made reachable. The first
    attempt is the task's own now, so a cancel from any of the cancel points
    can land while its `_park_hold` is stopping the guider. The native
    guider's ``stop_guiding`` cancels its guide loop and awaits it under
    ``suppress(CancelledError)``; a cancel of whoever is awaiting that stop is
    passed on to the loop task, comes back as the loop's CancelledError, and
    is suppressed with it. The task would run on into its retry loop, and
    `_cancel_idle_stop_retry`, which awaits it, would wait for as long as the
    mount refused the stop: `_setup_target` stuck before its slew.

    The real `_idle_stop_retry` task, on the sim mount behind a double that
    never takes a stop, and the sim guider's stop replaced by the native
    guider's shape: a guide loop that takes a moment to die, cancelled and
    awaited under ``suppress``. The setup's cancel lands while that await is
    in flight. It must return, the task must end cancelled, and the mount
    must be asked nothing past the first attempt's own stop. The guider's
    stop itself, and the run task's other calls to it, are #235.

    Mutant "no re-raise after the first attempt" (the
    `_reraise_swallowed_cancel()` after `_park_hold()` in `_idle_stop_retry`
    deleted): RED (observed) -
        AssertionError: the cancel landed in the guider's stop, which ate it,
        and the task ran on into its retry loop: the cancel had not returned
        after 2 s, and the task asked the mount 65 time(s) before the bound
        cancelled it again
    (the count is however many 0.02 s retries fit in the 2 s bound: 64 on
    one run, 65 on the next).
    """
    import contextlib
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.02)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    engine = engine_mod.SequenceEngine(sim_hub)
    tel = sim_hub.devices["telescope"]
    guider = sim_hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider for `_park_hold` to stop")
    real_set = tel.set_tracking
    await real_set(True)
    stops: list[str] = []

    async def set_tracking(on):
        if on:
            await real_set(True)
            return
        stops.append(asyncio.current_task().get_name())
        raise asyncio.TimeoutError()             # never taken

    in_the_stop = asyncio.Event()

    async def guide_loop():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)             # the guide exposure in flight
            raise

    async def stop_guiding():
        # native.py's shape: cancel the loop, then await it under suppress.
        loop_task = asyncio.get_running_loop().create_task(guide_loop())
        await asyncio.sleep(0)
        loop_task.cancel()
        in_the_stop.set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await loop_task

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    task = None
    try:
        await engine._idle_park_hold("the next target is a long wait away")
        task = engine._idle_stop_task
        assert task is not None, "premise: the stop was handed to its task"
        await asyncio.wait_for(in_the_stop.wait(), 5.0)
        assert stops == [], f"premise: the guider is stopped first: {stops}"

        returned = True
        try:
            await asyncio.wait_for(engine._cancel_idle_stop_retry(), 2.0)
        except asyncio.TimeoutError:
            # `wait_for` then cancels the gather, which cancels the task a
            # second time; that one lands in the retry loop's sleep.
            returned = False
        asked = len(stops)
        assert returned and task.cancelled(), (
            f"the cancel landed in the guider's stop, which ate it, and the "
            f"task ran on into its retry loop: the cancel "
            f"{'returned' if returned else 'had not returned after 2 s'}, and "
            f"the task asked the mount {asked} time(s) before "
            f"{'it ended' if returned else 'the bound cancelled it again'}")
        assert stops == ["idle-stop-retry"], (
            f"the mount was asked past the first attempt's own stop: {stops}")
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_the_retry_ends_once_the_mount_confirms_the_stop(
        sim_hub, monkeypatch, bus_lines):
    """(d), the other end of the retry's life: it asks "until a read-back
    confirms it or the spell ends", so a mount that takes the stop on a
    later ask is asked no more. Every case above keeps the mount refusing to
    the horizon, where only a cancel can end the retry, so none of them sees
    whether a confirmed stop ends it.

    The real `_idle_stop_retry` task, with the interval and the read-back's
    confirm spacing set to 0 so it asks at once, on the sim mount behind a
    double whose first two stops do not take (the task's first attempt and
    its first retry, both on the task since #216) and whose third does. The
    read-back is the sim's own. Then the loop is given many turns: a retry
    that ended stays ended.

    Mutant "the retry never stops" (the ``return`` after a confirmed False
    in `_idle_stop_retry`'s loop made ``pass``): RED (observed) -
        AssertionError: the mount confirmed the stop on the task's 3rd ask
        and the task went on asking: 122 asks, still running
    """
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.0)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    engine = engine_mod.SequenceEngine(sim_hub)
    tel = sim_hub.devices["telescope"]
    real_set = tel.set_tracking
    await real_set(True)
    #: which task asked for each stop, in order
    stops: list[str] = []

    async def set_tracking(on):
        if on:
            await real_set(True)
            return
        task = asyncio.current_task()
        stops.append("task" if task.get_name() == "idle-stop-retry"
                     else task.get_name())
        if len(stops) < 3:
            raise asyncio.TimeoutError()        # not taken
        await real_set(False)                   # taken

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    try:
        await engine._idle_park_hold("the next target is a long wait away")
        task = engine._idle_stop_task
        assert task is not None and not task.done(), "premise: a retry is alive"
        for _ in range(400):
            if task.done():
                break
            await asyncio.sleep(0)
        for _ in range(200):                    # and a while longer
            await asyncio.sleep(0)
        asked = len(stops)
        assert task.done() and asked == 3, (
            f"the mount confirmed the stop on the task's 3rd ask and the "
            f"task went on asking: {asked} asks, "
            f"{'ended' if task.done() else 'still running'}")
        assert stops == ["task"] * 3, (
            f"premise: every stop, the first attempt included, was the "
            f"task's: {stops}")
        assert tel.rig.tracking is False
        assert task.exception() is None
    finally:
        await engine._cancel_idle_stop_retry()


# -------------------------------------------------------------------- controls

async def test_control_a_mount_that_confirms_is_asked_once_and_no_retry_starts(
        sim_hub, monkeypatch, bus_lines):
    """(e) CONTROL. The simulator's mount takes the stop and the read-back
    confirms it: one ``set_tracking(False)`` across the whole spell, made by
    the idle-stop task, no unconfirmed-stop warning, and the task over once
    it has read the stop back. It used to assert no task at all: the task
    existed only for a retry. Since #216 it makes the first attempt too, so
    what the control grades is that it ENDS at the confirmed read-back.

    Mutant "retry whatever the read-back says" (the ``return`` on a
    confirmed False after the first attempt in `_idle_stop_retry` deleted,
    so the task goes on to its retry loop): RED (observed) -
        AssertionError: a mount that confirmed its stop was asked 2 times in
        one idle spell: [(120.0, 'retry'), (184.0, 'retry')]
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        assert run.horizon - idle > TEARDOWN + 4 * RETRY, "premise: a long spell"
        asks = [(round(t - idle, 1), who) for t, who in _asks(run) if t >= idle]
        assert len(asks) == 1, (
            f"a mount that confirmed its stop was asked {len(asks)} times in "
            f"one idle spell: {asks[:4]}")
        assert asks[0][1] == "retry", (
            f"the stop is the idle-stop task's (#216): {asks}")
        assert _unconfirmed_lines(bus_lines) == []
        task = run.engine._idle_stop_task
        assert task is not None and task.done() and task.exception() is None, (
            f"the task that made the stop is still running, or failed: {task}")
        assert run.tracking() is False
    finally:
        await run.close()


def test_the_warning_promises_the_interval_the_engine_keeps():
    """The unconfirmed-stop warning says "about once a minute" in words (a log
    line here carries no numbers). The number lives in IDLE_STOP_RETRY_S, so
    the two are pinned together: change one, change the other.

    Mutant "IDLE_STOP_RETRY_S = 300.0": RED (observed) -
        AssertionError: the warning promises about once a minute; the retry
        asks every 300 s
    """
    assert engine_mod.IDLE_STOP_RETRY_S == 60.0, (
        f"the warning promises about once a minute; the retry asks every "
        f"{engine_mod.IDLE_STOP_RETRY_S:.0f} s")


# ------------------------------------------- cancelled by abort and by a new start

def _one_spell_plan(t0: float) -> tuple[Target, SequencePlan]:
    b = _constraint_waiter("Bravo", t0)
    return b, SequencePlan(name="idle", guide=False, dither_every=0,
                           autofocus_every=0, meridian_flip=False,
                           safety_check=False, targets=[b])


async def _a_retry_left_over(run: _Clocked) -> asyncio.Task:
    """An idle-stop task alive with no run behind it: the stop is decided
    outside a run, on the dead mount, and handed to its task. Every run
    cancels its own at its end, so this is the case `start` and `abort`
    defend. The task has not had its first turn yet (nothing here yields to
    the loop), which is the shape #216 made possible: its first act is the
    first attempt, and a task created before a run's task is scheduled
    before it."""
    _a_mount_that_will_not_stop(run, readback="still tracking")
    await run.engine._idle_park_hold("the next target is a long wait away")
    task = run.engine._idle_stop_task
    assert task is not None and not task.done(), "premise: a retry is alive"
    return task


async def test_a_new_start_cancels_a_retry_left_from_before_it(
        sim_hub, monkeypatch, bus_lines):
    """A task left from before a run must not ask during it. `start` cancels
    it (it cannot await), and the run's first act awaits it. The run then
    waits ten minutes on a constraint with nothing acquired, and nothing
    asks.

    WHY `start` CANCELS, and `_run` alone no longer does. Before #216 a task
    left over had already made its first attempt inline and was asleep, so
    `_run`'s first line always reached it in time. Now its first act is the
    first attempt, and a task created before the run's task gets its turn
    first: it asked the mount at the run's first instant, before `_run`
    could cancel it.

    Mutant "start does not cancel the stale task" (the
    ``self._idle_stop_task.cancel()`` in `start()` deleted): RED (observed)
    -
        AssertionError: a retry from before the run asked during it:
        set_tracking(False) at [0.0] s
    Mutant "the run does not cancel the stale retry" (the
    `_cancel_idle_stop_retry` call at the top of `_run` deleted): green
    here, because `start` already cancelled a task that had not started.
    That call now only awaits it, for a task `start` caught half way
    through an ask; the await itself is pinned by
    `test_the_cancel_waits_for_a_stop_already_on_the_wire_to_land`.
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=600.0)
    stale = await _a_retry_left_over(run)
    _b, plan = _one_spell_plan(run.t0)
    try:
        await run.night(plan)
        asked = [round(t - run.t0, 1) for t in _retry_asks(run)]
        assert asked == [], (
            f"a retry from before the run asked during it: "
            f"set_tracking(False) at {asked[:4]} s")
        assert stale.done(), "the retry from before the run is still alive"
    finally:
        await run.close()


async def test_an_abort_completes_a_stop_left_over_with_no_run_behind_it(
        sim_hub, monkeypatch, bus_lines):
    """`abort` ends an idle-stop task whatever the run did, including when
    there is no run at all, and since #247 it ends it by COMPLETING the stop
    rather than dropping it (H3 orchestrator ruling 5, spec "Still waiting
    on the owner" item 14): nothing turns tracking back on after an abort.
    The task's first attempt, not begun when the abort comes, is let run;
    the mount does not take it, so abort asks once more, bounded, reads it
    back, and says once that it is still unconfirmed. Afterwards, with the
    engine idle, the harness would advance a live task (the only engine task
    left); none asks.

    This case used to start a run and abort it before its first turn, so
    that the run never reached its own end. `start` now cancels a task left
    over itself (see the case above), which would keep that version green
    with `abort`'s call deleted; with no `start`, only `abort` can.

    ON THE CLOCK (`abort_on_the_clock`). Called straight from the test,
    abort's wait for the first attempt polled with real sleeps while the
    driver ran the task through forty fake retries in one of them.

    Mutant "abort leaves the retry" (the `_finish_idle_stop` call in `abort`
    deleted): RED (observed) -
        AssertionError: the abort did not complete the stop left over: its
        first attempt and one more ask were wanted, set_tracking(False) at
        [(0.0, 'retry')] s, and after the abort at [(90.0, 'retry'), (180.0,
        'retry'), (270.0, 'retry'), (360.0, 'retry')] s
    Mutant "abort cancels the stop" (`_cancel_idle_stop_retry` in its place,
    H2's call): RED (observed) -
        AssertionError: the abort did not complete the stop left over: its
        first attempt and one more ask were wanted, set_tracking(False) at
        [(0.0, 'retry')] s, and after the abort at [] s
    and the same text under "cancel instead of complete" (the body of
    `_finish_idle_stop` replaced by ``await self._cancel_idle_stop_retry()``).
    The first attempt's ask at 0.0 is made in both: the task was created
    before the abort's, so it gets its first turn first, and the cancel then
    cuts it on the wire.
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=3600.0)
    stale = await _a_retry_left_over(run)
    try:
        await run.abort_on_the_clock()
        done_at = len(run.tracking_calls)
        await run._real_sleep(0.3)
        asks = [(round(t - run.t0, 1), who)
                for t, on, who in run.tracking_calls[:done_at] if not on]
        after = [(round(t - run.t0, 1), who)
                 for t, on, who in run.tracking_calls[done_at:] if not on]
        assert [w for _t, w in asks] == ["retry", "abort"], (
            f"the abort did not complete the stop left over: its first "
            f"attempt and one more ask were wanted, set_tracking(False) at "
            f"{asks[:4]} s, and after the abort at {after[:4]} s")
        assert stale.done() and after == [], (
            f"a retry outlived the abort and asked at {after[:4]} s")
        assert len(_unconfirmed_lines(bus_lines)) == 1, (
            _unconfirmed_lines(bus_lines))
        ended = [m for lvl, m, _s in bus_lines if lvl == "warning"
                 and "has still not confirmed it" in m]
        assert len(ended) == 1, ended
    finally:
        await run.close()


async def test_an_abort_mid_spell_leaves_no_retry(sim_hub, monkeypatch,
                                                  bus_lines):
    """The ordinary abort: the operator stops a run whose idle stop is being
    retried. After the abort nothing asks the mount again.

    Mutant "the abort path cancels nothing" (both the `finally` around the
    cooling wait and `_run_scheduled` and the call in `abort` deleted): RED
    (observed) -
        AssertionError: the retry went on asking after the abort:
        set_tracking(False) at [90.0, 180.0, 270.0, 360.0] s after it
    Either cancel alone keeps it green: this pins the behaviour, and the two
    cases above pin each call.
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=3600.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    _a_mount_that_will_not_stop(run, readback="still tracking")
    run.engine.start(_plan(a, b))
    loop = asyncio.get_running_loop()
    end = loop.time() + 60.0
    try:
        while len(_retry_asks(run)) < 2 and loop.time() < end:
            await run._real_sleep(0.01)
        assert len(_retry_asks(run)) >= 2, "premise: the retry is asking"
        await run.engine.abort()
        aborted = run.clock.t
        await run._real_sleep(0.3)
        after = [round(t - aborted, 1) for t in _retry_asks(run) if t > aborted]
        assert after == [], (
            f"the retry went on asking after the abort: set_tracking(False) "
            f"at {after[:4]} s after it")
    finally:
        await run.close()
