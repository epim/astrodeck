"""Abort publishes an intermediate state BEFORE the teardown it has to wait for.

UX review finding 9. ``POST /api/sequence/abort`` awaits the WHOLE wind-down:
cancel the run task, then ``_safe_stop`` (bounded, but generously -- 30 s to
abort the exposure, 120 s to stop the guider, 2x30 s for the flat panel/cover),
finalize the report, then drain every in-flight thumbnail render. Roughly 210 s
worst case, against the client's 15 s request cap. The terminal ``aborted`` was
published only after all of that, so the only thing the caller ever saw was its
own timeout: an abort that WORKED reported "server not responding", in a red
toast, on the control an operator reaches for when something is already wrong.

The engine knows the difference between "I have started tearing this down" and
"the rig has stopped". It has to say both -- the same two-step the polar
session's ``pausing``/``paused`` uses (polar/session.py).
"""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # Fixed sky target + the W1.10 sun cone would make this date-dependent; the
    # cone has its own suite (test_sun_guard.py). Same disarm as test_sequence.py.
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def long_plan() -> SequencePlan:
    """Long enough that the abort lands mid-run, not on a finished one."""
    return SequencePlan(
        name="abort-state",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=5.0, gain=100, count=50)],
        )],
        guide=False, dither_every=0, autofocus_every=0)


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def drain_sequence(q: asyncio.Queue) -> list[dict]:
    """Every ``sequence`` payload queued so far, oldest first."""
    out = []
    while True:
        try:
            ev = q.get_nowait()
        except asyncio.QueueEmpty:
            return out
        if ev.type == "sequence":
            out.append(ev.data)


async def test_abort_says_it_is_aborting_while_the_teardown_still_runs(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(long_plan())
    assert await wait_for(lambda: engine.state.get("state") == "running"), engine.state

    # Hold the teardown open at the same place the real one is slow -- inside
    # _safe_stop, which is where the exposure-abort/guider-stop/panel-off device
    # I/O lives. Everything before it is exactly what production does.
    entered = asyncio.Event()
    release = asyncio.Event()
    real_safe_stop = engine._safe_stop

    async def slow_safe_stop():
        entered.set()
        await asyncio.wait_for(release.wait(), 30.0)
        await real_safe_stop()

    engine._safe_stop = slow_safe_stop

    q = bus.subscribe()
    try:
        drain_sequence(q)                       # only what the abort publishes
        aborting = asyncio.create_task(engine.abort())
        assert await wait_for(entered.is_set), "the teardown never started"

        # PRECONDITION. Without a teardown still in flight there is no window for
        # an intermediate state to exist in, and every assertion below is vacuous.
        assert not aborting.done(), (
            "abort() returned before the teardown finished -- the 15s-vs-210s "
            "window this state exists for never opened")
        assert engine.running, (
            "the engine reports the run as over while its teardown is running")

        assert engine.state.get("state") == "aborting", (
            "the engine went straight from running to nothing while it tears the "
            f"run down -- the caller has no state to render: {engine.state}")

        frames = [d for d in drain_sequence(q) if d.get("state") == "aborting"]
        assert frames, (
            "'aborting' never reached the bus, so no WS client can see it -- "
            "engine.state alone is only visible to a poll of /api/sequence/state")
        # It must SAY something the operator can act on, not restate the button
        # they just held.
        detail = (frames[-1].get("detail") or "").lower()
        assert detail and "abort" not in detail, (
            f"the abort state's detail says nothing the button didn't: {detail!r}")
        # ...and it must not carry a finish clock for a run that is being killed.
        assert "eta_s" not in (frames[-1].get("progress") or {}), (
            "the aborting frame still advertises an ETA -- a countdown to a "
            "completion that will never happen")

        release.set()
        await asyncio.wait_for(aborting, 60.0)
        assert engine.state.get("state") == "aborted", engine.state
        assert any(d.get("state") == "aborted" for d in drain_sequence(q)), (
            "the terminal state never landed after the teardown finished")
    finally:
        release.set()
        bus.unsubscribe(q)


async def test_a_second_abort_during_the_teardown_is_a_no_op(sim_hub):
    """The window the new state names is a window the route can be re-entered in.

    ``self._task and not self._task.done()`` is true for the WHOLE wind-down, so
    it cannot tell a first abort from a second. And a second one is routine: the
    POST's 15 s cap expires ~195 s before the teardown does, so the operator sees
    a failed abort and presses again -- and the lock screen's emergency STOP
    posts this route on every press and is deliberately never disabled.

    A second ``cancel()`` lands on a task already inside
    ``except CancelledError: await self._safe_stop()``. The re-raised
    ``BaseException`` is not caught by ``_safe_stop``'s ``except (TimeoutError,
    Exception)``, so the teardown is SEVERED partway: the guider is never
    stopped, the flat panel is never switched off, the report is never
    finalized. The guard is ``PolarAlignSession.pause``'s, for the same reason.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(long_plan())
    assert await wait_for(lambda: engine.state.get("state") == "running"), engine.state

    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    real_safe_stop = engine._safe_stop

    async def slow_safe_stop():
        entered.set()
        # The teardown's own device I/O, held open at an await -- exactly where a
        # second cancellation would land in production (30 s to abort the
        # exposure, 120 s to stop the guider).
        await asyncio.wait_for(release.wait(), 30.0)
        await real_safe_stop()
        completed.set()

    engine._safe_stop = slow_safe_stop

    q = bus.subscribe()
    try:
        first = asyncio.create_task(engine.abort())
        assert await wait_for(entered.is_set), "the teardown never started"

        # PRECONDITIONS. Without a teardown still in flight there is no second
        # caller to guard and every assertion below is vacuous.
        assert not first.done(), "the teardown finished before the second press"
        assert engine.running, "the engine already reports the run as over"
        assert engine.state.get("state") == "aborting", engine.state
        drain_sequence(q)                     # only what the SECOND press says

        # THE SECOND PRESS.
        await asyncio.wait_for(engine.abort(), 10.0)

        mid = [d.get("state") for d in drain_sequence(q)]
        assert "aborted" not in mid, (
            "the second abort published the terminal state while the rig was "
            f"still tearing down -- the client is told it stopped: {mid}")
        assert not first.done(), (
            "the second abort cut the first one's teardown short")

        release.set()
        await asyncio.wait_for(first, 60.0)
        assert completed.is_set(), (
            "_safe_stop never ran to completion -- a second cancel severed the "
            "teardown after abort_exposure, so the guider is still guiding, the "
            "flat panel is still lit and the report was never finalized")
        assert engine.state.get("state") == "aborted", engine.state
    finally:
        release.set()
        bus.unsubscribe(q)


async def test_abort_does_not_rewrite_a_terminal_frame_back_to_aborting(sim_hub):
    """The UNSAFE path publishes its terminal frame from INSIDE the run task and
    then parks the mount under ``asyncio.shield`` -- so the task is still live
    long after the run has ended (engine.py's ``except SafetyAbort``). A UI abort
    landing in that window would rewrite ``aborted / end_reason=unsafe`` back to
    "aborting" with a detail that promises the exposure and the guider are being
    stopped -- while what is actually in flight is a park.

    The terminal frame is published here the same way the unsafe path publishes
    it (from outside ``abort()``, with the task still running); the predicate
    under test is "a terminal state is already on the wire", not how it got there.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(long_plan())
    assert await wait_for(lambda: engine.state.get("state") == "running"), engine.state

    entered = asyncio.Event()
    release = asyncio.Event()
    real_safe_stop = engine._safe_stop

    async def slow_safe_stop():
        entered.set()
        await asyncio.wait_for(release.wait(), 30.0)
        await real_safe_stop()

    engine._safe_stop = slow_safe_stop
    engine._set_state(state="aborted", detail="roof closed — unsafe",
                      end_reason="unsafe")

    q = bus.subscribe()
    try:
        drain_sequence(q)
        assert engine.running, "PRECONDITION: the run task already ended"
        aborting = asyncio.create_task(engine.abort())
        assert await wait_for(entered.is_set), "the teardown never started"
        states = [d.get("state") for d in drain_sequence(q)]
        assert "aborting" not in states, (
            "abort() reopened a run that had already ended -- the client goes "
            f"back from ABORTED to a wind-down that is not what is running: {states}")
        release.set()
        await asyncio.wait_for(aborting, 60.0)
        assert engine.state.get("state") == "aborted", engine.state
        assert engine.state.get("end_reason") == "unsafe", (
            "the unsafe cause was erased by the user abort that followed it")
    finally:
        release.set()
        bus.unsubscribe(q)


async def test_pause_and_resume_cannot_re_arm_a_run_being_torn_down(sim_hub):
    """``self.running`` is True inside the teardown, so nothing else stops
    /api/sequence/resume from publishing state="running" (with a live finish
    clock) for a run whose task has already been cancelled -- which un-hides
    every control the abort just took away."""
    engine = SequenceEngine(sim_hub)
    engine.start(long_plan())
    assert await wait_for(lambda: engine.state.get("state") == "running"), engine.state

    entered = asyncio.Event()
    release = asyncio.Event()
    real_safe_stop = engine._safe_stop

    async def slow_safe_stop():
        entered.set()
        await asyncio.wait_for(release.wait(), 30.0)
        await real_safe_stop()

    engine._safe_stop = slow_safe_stop

    q = bus.subscribe()
    try:
        aborting = asyncio.create_task(engine.abort())
        assert await wait_for(entered.is_set), "the teardown never started"
        assert not aborting.done() and engine.running, "no teardown in flight"
        drain_sequence(q)

        engine.pause()
        engine.resume()

        states = [d.get("state") for d in drain_sequence(q)]
        assert "paused" not in states, (
            f"a pause landed on a run that is being torn down: {states}")
        assert "running" not in states, (
            "a resume put the run back to RUNNING while its task was already "
            f"cancelled: {states}")
        assert engine.state.get("state") == "aborting", engine.state
        assert "eta_s" not in (engine.state.get("progress") or {}), (
            "a finish clock came back for a cancelled run")

        # ...and any publish that omits `state=` merges into a state that is
        # STILL "aborting", so an ETA suppression keyed on this call's state
        # string re-attaches a full live finish clock to the teardown frame.
        engine._set_state(detail="draining thumbnails")
        assert engine.state.get("state") == "aborting", (
            "precondition: the merged state is no longer the teardown's")
        assert "eta_s" not in (engine.state.get("progress") or {}), (
            "a publish that did not name a state put the finish clock back on "
            f"an aborting frame: {engine.state.get('progress')}")

        release.set()
        await asyncio.wait_for(aborting, 60.0)
        assert engine.state.get("state") == "aborted", engine.state
    finally:
        release.set()
        bus.unsubscribe(q)


async def test_abort_with_nothing_running_does_not_claim_a_teardown(sim_hub):
    """No task, no teardown, no intermediate -- the same refusal
    ``PolarAlignSession.pause`` makes when there is no session to pause."""
    engine = SequenceEngine(sim_hub)
    assert not engine.running, "the fixture started something; nothing to prove here"
    q = bus.subscribe()
    try:
        await engine.abort()
        states = [d.get("state") for d in drain_sequence(q)]
    finally:
        bus.unsubscribe(q)
    assert states, "abort() published nothing at all"
    assert "aborting" not in states, (
        "abort announced a teardown it never ran -- a client would render a "
        "wind-down over an engine that has been idle the whole time")
    assert states[-1] == "aborted"
