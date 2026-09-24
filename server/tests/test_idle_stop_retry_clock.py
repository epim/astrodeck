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

Now the first attempt is still made on the tick (#216 is that one), and an
unconfirmed stop is handed to a task of its own that asks at most once per
``IDLE_STOP_RETRY_S``, with ``set_tracking(False)`` and the read-back only.
Every path that ends the spell cancels it and waits for it: the next
`_setup_target` before it restores tracking, the end of the run, `abort`, and
the next `start`.

THE HARNESS IS test_idle_park_hold's clocked simulator, whose timer heap wakes
the run task and the retry task each on its own schedule. That is the point
of it here: a clock that summed both tasks' sleeps would charge the retry's
hangs to the wait loop and could not tell a retry on its own task from one
inline in the tick. The first case proves it can.
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import ExposureStep, SequencePlan, Target

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    HANG, RETRY, TEARDOWN, TICK, _a_mount_that_will_not_stop, _asks,
    _Clocked, _constraint_waiter, _lst_h, _plan, _ra_at, _target,
    _unconfirmed_lines, sim_hub, temp_store)

#: How far past one tick a gap between two safety-gate calls may run. The
#: harness wakes the wait loop on the dot, so anything real is far above it.
EPS = 0.5


def _retry_asks(run: _Clocked) -> list[float]:
    return [t for t, who in _asks(run) if who == "retry"]


# --------------------------------------------------- the wait loop keeps its tick

async def test_the_safety_gate_keeps_its_tick_while_the_retry_hangs(
        sim_hub, monkeypatch, bus_lines):
    """(a) Alpha shoots, Bravo waits two hours on a constraint, and the mount's
    link is dead: ``set_tracking(False)`` and ``get_tracking`` each hang for
    their whole bound. Through a ten-minute idle spell, once the first
    attempt is over, the gap between two calls of the wait loop's safety gate
    is one tick, while the retry task asks and hangs beside it.

    THE FIRST ATTEMPT is still inline and still costs its hangs (#216, not
    this round): measured here, the gap across it is 65.0 s, the stop's hang,
    the read-back's hang and the tick. It is left out of the grading, and
    only it: the window starts where the retry task was handed the stop.

    Mutant "retry inline in the tick" (`_idle_park_hold` hands the stop to
    `_idle_hold_tick` instead of a task, and the tick asks again itself,
    still at most once per IDLE_STOP_RETRY_S): RED, a tick of 65 s at every
    retry, while (b) below stays green under it (observed) -
        AssertionError: the safety gate waited 65.0 s between two looks
        while the retry asked the dead mount (one tick is 5 s); gaps over a
        tick at [240.0, 360.0, 480.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=720.0)
    t0 = run.t0
    a = _target("Alpha", _ra_at(-3.0, t0), 20.0)
    b = _constraint_waiter("Bravo", t0)
    _a_mount_that_will_not_stop(run, readback="unreadable")
    try:
        await run.night(_plan(a, b))
        idle = run.exposure_end("Alpha")
        asks = [(t, who) for t, who in _asks(run) if t >= idle]
        assert asks and asks[0][1] == "run" and \
            TEARDOWN <= asks[0][0] - idle <= TEARDOWN + TICK, (
                f"premise: the first attempt is the run's own, on the idle "
                f"clock: {[(round(t - idle, 1), w) for t, w in asks[:3]]}")
        # The retry sleeps IDLE_STOP_RETRY_S before its first ask, so it was
        # handed the stop exactly that long before it.
        retried = _retry_asks(run) or [t for t, _w in asks[1:]]
        assert retried, "premise: the stop was asked again"
        handed = retried[0] - RETRY
        assert len([t for t in retried if t < run.horizon - 2 * HANG]) >= 3, (
            f"premise: the retry asked, and hung, several times inside the "
            f"window: {run.rel(retried, idle)} s")
        gates = [t for t, ctx in run.gates if t >= idle]
        spell = run.horizon - idle
        assert spell >= 600.0, f"premise: a ten-minute idle spell ({spell})"
        first_gap = max(y - x for x, y in zip(gates, gates[1:]) if x < handed)
        assert first_gap >= 2 * HANG, (
            f"premise: the first attempt hung inline (#216): {first_gap:.1f} s")
        graded = [t for t in gates if t >= handed]
        # In fake seconds, not calls: the defect under test changes the count.
        assert graded and graded[-1] - graded[0] >= 6 * RETRY, (
            f"premise: a long graded window "
            f"({graded[-1] - graded[0] if graded else 0:.0f} s)")
        gaps = [round(y - x, 1) for x, y in zip(graded, graded[1:])]
        assert max(gaps) <= TICK + EPS, (
            f"the safety gate waited {max(gaps):.1f} s between two looks "
            f"while the retry asked the dead mount (one tick is {TICK:.0f} s); "
            f"gaps over a tick at "
            f"{[round(graded[i] - idle, 1) for i, g in enumerate(gaps) if g > TICK + EPS][:4]} s")
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

    Mutant "retry through _park_hold" (`_idle_stop_retry` awaits
    `_park_hold` in place of `_stop_tracking_quietly`): RED (observed) -
        AssertionError: the guider was stopped (6, 5) times in two idle
        spells whose retries asked the mount 5 and 4 times: stop_guiding at
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
    at once, and a mount double whose retry ask hangs until cancelled and
    then lands the stop a few loop turns later. Then exactly what setup does:
    cancel the retry, and turn tracking on.

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

    async def set_tracking(on):
        task = asyncio.current_task()
        who = "retry" if task.get_name() == "idle-stop-retry" else "test"
        if on:
            await real_set(True)
            effects.append(("on", who))
            return
        if who != "retry":
            # The first attempt, made inline: the mount does not take it.
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


async def test_the_retry_ends_once_the_mount_confirms_the_stop(
        sim_hub, monkeypatch, bus_lines):
    """(d), the other end of the retry's life: it asks "until a read-back
    confirms it or the spell ends", so a mount that takes the stop on a
    later ask is asked no more. Every case above keeps the mount refusing to
    the horizon, where only a cancel can end the retry, so none of them sees
    whether a confirmed stop ends it.

    The real `_idle_stop_retry` task, with the interval and the read-back's
    confirm spacing set to 0 so it asks at once, on the sim mount behind a
    double whose first two stops do not take (the inline attempt and the
    retry's first ask) and whose third does. The read-back is the sim's own.
    Then the loop is given many turns: a retry that ended stays ended.

    Mutant "the retry never stops" (the ``return`` after a confirmed False
    in `_idle_stop_retry` made ``pass``): RED (observed) -
        AssertionError: the mount confirmed the stop on the retry's 2nd ask
        and the retry went on asking: 121 asks, still running
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
        stops.append("retry" if task.get_name() == "idle-stop-retry"
                     else "inline")
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
        asked = stops.count("retry")
        assert task.done() and asked == 2, (
            f"the mount confirmed the stop on the retry's 2nd ask and the "
            f"retry went on asking: {asked} asks, "
            f"{'ended' if task.done() else 'still running'}")
        assert stops[0] == "inline", f"premise: the first stop was inline: {stops}"
        assert tel.rig.tracking is False
        assert task.exception() is None
    finally:
        await engine._cancel_idle_stop_retry()


# -------------------------------------------------------------------- controls

async def test_control_a_mount_that_confirms_is_asked_once_and_no_retry_starts(
        sim_hub, monkeypatch, bus_lines):
    """(e) CONTROL. The simulator's mount takes the stop and the read-back
    confirms it: one ``set_tracking(False)`` across the whole spell, no
    unconfirmed-stop warning, and no retry task at all.

    Mutant "start the retry whatever the read-back says" (the task created
    before the read-back is looked at): RED (observed) -
        AssertionError: a mount that confirmed its stop was asked 2 times in
        one idle spell: [(120.0, 'run'), (180.0, 'retry')]
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
        assert _unconfirmed_lines(bus_lines) == []
        assert run.engine._idle_stop_task is None
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
    """A retry alive with no run behind it: the stop is asked for outside a
    run, on the dead mount, and handed to the retry task. Every run cancels
    its own at its end, so this is the case `start` and `abort` defend."""
    _a_mount_that_will_not_stop(run, readback="still tracking")
    await run.engine._idle_park_hold("the next target is a long wait away")
    task = run.engine._idle_stop_task
    assert task is not None and not task.done(), "premise: a retry is alive"
    return task


async def test_a_new_start_cancels_a_retry_left_from_before_it(
        sim_hub, monkeypatch, bus_lines):
    """A retry left from before a run must not ask during it: `start` cannot
    await, so the run's first act is to cancel it and wait for it. The run
    then waits ten minutes on a constraint with nothing acquired, and no
    retry asks.

    Mutant "the run does not cancel the stale retry" (the
    `_cancel_idle_stop_retry` call at the top of `_run` deleted): RED
    (observed) -
        AssertionError: a retry from before the run asked during it:
        set_tracking(False) at [60.0, 150.0, 240.0, 330.0] s
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


async def test_an_abort_before_the_run_begins_cancels_a_retry_left_over(
        sim_hub, monkeypatch, bus_lines):
    """A run aborted before its first turn never reaches its own cancel, so
    `abort` cancels the retry itself. Afterwards, with the engine idle, the
    harness would advance a live retry (the only engine task left); none asks.

    Mutant "abort leaves the retry" (the `_cancel_idle_stop_retry` call in
    `abort` deleted): RED (observed) -
        AssertionError: a retry outlived the abort and asked at [60.0,
        150.0, 240.0, 330.0] s
    """
    run = _Clocked(sim_hub, monkeypatch, horizon_s=3600.0)
    stale = await _a_retry_left_over(run)
    _b, plan = _one_spell_plan(run.t0)
    try:
        run.engine.start(plan)
        await run.engine.abort()
        await run._real_sleep(0.3)
        asked = [round(t - run.t0, 1) for t in _retry_asks(run)]
        assert stale.done() and asked == [], (
            f"a retry outlived the abort and asked at {asked[:4]} s")
    finally:
        await run.close()


async def test_an_abort_mid_spell_leaves_no_retry(sim_hub, monkeypatch,
                                                  bus_lines):
    """The ordinary abort: the operator stops a run whose idle stop is being
    retried. After the abort nothing asks the mount again.

    Mutant "the abort path cancels nothing" (both the `finally` around
    `_run_scheduled` and the call in `abort` deleted): RED (observed) -
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
