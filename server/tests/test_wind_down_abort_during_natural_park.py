"""An Abort that lands in a natural parking end's park cancels neither the
park nor the idle stop handed to it (#305, a #270 follow-up).

Since #270 (S2 orchestrator ruling 1) an ending that parks hands the idle
stop's task to its park: `_hand_idle_stop_to_the_park` cancels the task,
fenced, and nothing else will make that stop, because the park is the stop.
An unsafe ending's wind-down is shielded whole (`_run` re-awaits it across a
cancel, §1.9-G). A natural end, a cooling skip or a quality stop whose plan
parks when done awaits its wind-down directly, so an operator's Abort could
land in its park: the park was cancelled, the wind-down cancelled the handed
task with it, `abort`'s `_finish_idle_stop` found no task, and `_safe_stop`
touches no mount. If the park had not reached the wire, the mount tracked on,
unwatched: the hazard the idle stop was decided for (the #247 class, and the
"safety rides value paths" class, since the stop now rode the park's path).

NOW the wind-down's park runs on a task of its own that the wind-down waits
for however many cancels land (`_wind_down_park_and_close`), and the short
wait for the guider before it (#311) notes a cancel and goes on. A park that
then did not park is followed at once by the failed park's stop of tracking,
read back. Only then is the cancel raised: the Abort still ends the run, and
nothing after the park (the roof close, the day darks, the warm ramp) runs.

THE HARNESS is test_unsafe_ending_parks_at_once's `_Ending` with no rain: the
run ends by itself at Bravo's stop time, a dawn cutoff, with
``park_when_done`` on, and the idle stop's first attempt is still stopping
the guider when it ends (`_retry_parked_on`), so the stop is handed to the
park unmade and the mount is still tracking. The park double holds the park
in flight on a real event until the test has let the Abort's cancel land,
then lets it finish, or fail. The site is a fixture, never the real one.

THE CONTROLS, an operator's Abort mid-run and a failure, each of which
completes the idle stop and parks nothing (H3 orchestrator ruling 5), are
test_run_end_completes_the_idle_stop.py's `test_an_abort_still_sends_the_stop`
and `test_control_a_failure_still_completes_the_stop`.
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import SafetyConfig
from astrodeck.devices.base import DeviceError, DomeShutterState

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_unsafe_ending_parks_at_once import (
    GUIDE_S, _Ending, _premise_the_first_attempt_was_out, _retry_parked_on)

CAP = engine_mod.WIND_DOWN_GUIDER_STOP_S


def _natural_parking_end(hub, store, monkeypatch, *,
                         hang_the_wind_down: bool = False,
                         roof: bool = False):
    """A natural end that parks, with the idle stop's first attempt still
    out when it ends; ``roof``: and closes the roof when done. Returns the
    ending, the idle stop's record and the gate that would release its
    guider stop (never set while the run lasts)."""
    if roof:
        store.set_safety(SafetyConfig(enabled=False,
                                      close_dome_when_done=True))
    e = _Ending(hub, store, monkeypatch, unsafe=False, park_when_done=True)
    gate = asyncio.Event()
    got = _retry_parked_on(e, gate, monkeypatch,
                           hang_the_wind_down=hang_the_wind_down)
    return e, got, gate


def _held_park(e: _Ending, monkeypatch, *, fails: bool = False) -> dict:
    """The park, held in flight on ``release`` (a real event) once it is
    asked. Released, it parks (through `_Ending`'s record and the sim) or,
    with ``fails``, raises as a refused park does. Records whether a cancel
    reached it and the index into ``tracking_calls`` when it was asked."""
    tel = e.run.hub.devices["telescope"]
    inner = tel.park
    rec: dict = {"asked": asyncio.Event(), "release": asyncio.Event(),
                 "cut": [], "at": None}

    async def park():
        rec["at"] = len(e.run.tracking_calls)
        rec["asked"].set()
        try:
            await rec["release"].wait()
        except asyncio.CancelledError:
            rec["cut"].append(e.run.clock.t)
            raise
        if fails:
            raise DeviceError("the mount refused the park")
        return await inner()

    monkeypatch.setattr(tel, "park", park)
    return rec


async def _abort_and_let_it_land(e: _Ending) -> asyncio.Future:
    """Start the operator's Abort and give its cancel a few loop turns to
    land in whatever the run task is waiting on."""
    aborting = asyncio.ensure_future(e.engine.abort())
    for _ in range(20):
        await e.run._real_sleep(0)
    task = e.engine._task
    assert task is not None and task.cancelling() >= 1, (
        f"premise: the Abort cancelled the run task: {task}")
    return aborting


async def _close(e: _Ending, gate: asyncio.Event, rec: dict | None,
                 aborting) -> None:
    gate.set()
    if rec is not None:
        rec["release"].set()
    if aborting is not None and not aborting.done():
        await asyncio.gather(aborting, return_exceptions=True)
    await e.run.close()


# ------------------------------------------------ an Abort in the park

async def test_an_abort_in_the_natural_park_lets_the_park_finish(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#305's reproduction. The run ends by itself and parks; the idle stop
    was handed to that park unmade, so the mount is still tracking; and the
    operator's Abort lands while the park is in flight. The park must not be
    cut: it finishes once released, the mount ends parked and not tracking,
    "mount parked" is said, and the Abort then ends the run. The handed task
    sends nothing (its fence moved at the hand-over). This plan closes the
    roof when done, and after the Abort it does not: the roof close would be
    a new motion started after the operator asked the rig to stop, and the
    park is let finish only because the idle stop depends on it.

    Mutant "unshielded natural park" (`_wind_down_park_and_close` awaits
    ``self._wind_down_park(tel)`` directly instead of waiting for its task
    across cancels): RED (observed) -
        AssertionError: the Abort cut the natural end's park (a cancel reached
        it at [0.0] s after the run ended): parked False, tracking True; the
        idle stop handed to that park was never made
    Mutant "the Abort swallowed after the park" (the cancel arm's ``raise
    asyncio.CancelledError()`` deleted, so the wind-down runs on past the
    park): RED (observed) -
        AssertionError: the roof was closed after the Abort: ['park',
        'close', 'wound']
    """
    dome = sim_hub.devices.get("dome")
    assert dome is not None and dome.connected, "premise: the sim has a roof"
    e, got, gate = _natural_parking_end(sim_hub, temp_store, monkeypatch,
                                        roof=True)
    rec = _held_park(e, monkeypatch)
    e.engine.start(e.n.plan)
    aborting = None
    try:
        await e.n.until(rec["asked"].is_set, "the natural end's park was asked")
        assert [(s, r) for _t, s, r in e.terminal] == [
            ("complete", "dawn_cutoff")], (
            f"premise: a natural end, published: {e.terminal}")
        _premise_the_first_attempt_was_out(e, got)
        assert e.run.tracking() is True, (
            "premise: the idle stop was never made, so the mount tracks")
        ended = e.ended()
        aborting = await _abort_and_let_it_land(e)
        # Whether the Abort was still waiting when the park was let go: it
        # must be, since the run waits for the park; read here, asserted
        # below with the park, so a cut park is named as the failure.
        waited = not aborting.done()
        rec["release"].set()                   # the park comes back
        await asyncio.wait_for(aborting, 60.0)
        rig = e.run.hub.devices["telescope"].rig
        cut = [round(t - ended, 1) for t in rec["cut"]]
        assert cut == [] and rig.parked and not rig.tracking, (
            f"the Abort cut the natural end's park (a cancel reached it at "
            f"{cut} s after the run ended): parked {rig.parked}, tracking "
            f"{rig.tracking}; the idle stop handed to that park was never made")
        assert waited, "the Abort returned before the park it landed in"
        said = [m for _l, m, _s in bus_lines if m.startswith("mount parked")]
        assert len(said) == 1, said
        assert e.retry_offs_from(0) == [], (
            f"the handed idle-stop task sent set_tracking(False): "
            f"{e.retry_offs_from(0)}")
        assert e.closes == [] and (await dome.shutter_state()
                                   is DomeShutterState.OPEN), (
            f"the roof was closed after the Abort: "
            f"{[w for w, _t in e.events]}")
        assert not e.engine.running
        assert got["task"] is not None and got["task"].done()
    finally:
        await _close(e, gate, rec, aborting)


async def test_an_abort_in_a_park_that_fails_still_stops_the_mount(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The same Abort in the same park, and then the park fails. That is a
    wind-down that did not park, with the idle stop handed to it unmade, so
    before the cancel goes on the mount is asked, once and bounded, to stop
    tracking, it is read back, and what came of it is said: the failed
    park's stop (`_stop_after_a_failed_park`), made by the run itself. There
    is no roof-close attempt after an Abort.

    Mutant "no fallback on a cancelled park" (the ``if not parked: await
    self._stop_after_a_failed_park()`` in the cancel arm of
    `_wind_down_park_and_close` deleted): RED (observed) -
        AssertionError: the park the Abort landed in failed and nothing
        stopped the mount: set_tracking(False) after the park at [], read
        back by [], lines []; tracking True
    """
    e, got, gate = _natural_parking_end(sim_hub, temp_store, monkeypatch,
                                        roof=True)
    rec = _held_park(e, monkeypatch, fails=True)
    e.engine.start(e.n.plan)
    aborting = None
    try:
        await e.n.until(rec["asked"].is_set, "the natural end's park was asked")
        _premise_the_first_attempt_was_out(e, got)
        assert e.run.tracking() is True, (
            "premise: the idle stop was never made, so the mount tracks")
        aborting = await _abort_and_let_it_land(e)
        rec["release"].set()                   # the park comes back refused
        await asyncio.wait_for(aborting, 60.0)
        run = e.run
        i_park = rec["at"]
        offs = [(i, who) for i, (_t, on, who)
                in enumerate(run.tracking_calls) if i >= i_park and not on]
        reads = [who for _t, who, i in e.n.reads
                 if offs and i > offs[0][0]]
        said = [m for _l, m, _s in bus_lines
                if "the park did not complete" in m]
        assert offs and reads and said, (
            f"the park the Abort landed in failed and nothing stopped the "
            f"mount: set_tracking(False) after the park at "
            f"{[w for _i, w in offs]}, read back by {reads}, lines {said}; "
            f"tracking {run.tracking()}")
        assert [w for _i, w in offs] == ["run"] and reads[0] == "run", (
            f"the run did not make the stop itself: {offs}, {reads}")
        assert len(said) == 1 and "it has stopped tracking" in said[0], said
        assert run.tracking() is False
        assert rec["cut"] == [], "premise: the park was not cut"
        assert e.closes == [], f"a roof close ran after the Abort: {e.events}"
        assert not e.engine.running
    finally:
        await _close(e, gate, rec, aborting)


# ------------------------------------ an Abort in the guider wait before it

async def test_an_abort_in_the_guider_wait_still_parks(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#311 put a short wait for the guider in front of the park, and with
    it a new place for the Abort to land. The wind-down's guider stop does
    not answer, the clock is held the moment it is asked, and the Abort's
    cancel lands in the wait's poll. The wait notes it and goes on: the park
    is asked at the cap, as with no Abort, and parks the mount; then the
    Abort ends the run.

    Mutant "the guider wait lets the cancel through" (the wait's ``except
    asyncio.CancelledError: cancelled = True`` made a re-raise): RED
    (observed) -
        AssertionError: the Abort in the guider wait stopped the park from
        being asked: parks at [] s after the guider stop was asked; parked
        False, tracking True
    """
    e, got, gate = _natural_parking_end(sim_hub, temp_store, monkeypatch,
                                        hang_the_wind_down=True)
    run = e.run
    guider = run.hub.guider
    inner = guider.stop_guiding
    hold: dict = {"release": None}

    async def stop_guiding(*a, **kw):
        if e.winding and run.who() != "retry" and hold["release"] is None:
            hold["release"] = run.hold_clock()
        return await inner(*a, **kw)

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    e.engine.start(e.n.plan)
    aborting = None
    try:
        await e.n.until(lambda: hold["release"] is not None,
                        "the wind-down asked the guider to stop")
        _premise_the_first_attempt_was_out(e, got)
        asked = e.at("guider stop")
        assert asked and e.at("park") == [], (
            f"premise: the guider stop is out and no park asked: {e.events}")
        aborting = await _abort_and_let_it_land(e)
        hold["release"].set()                  # the clock goes on
        await asyncio.wait_for(aborting, 60.0)
        rig = run.hub.devices["telescope"].rig
        parks = [round(t - asked[0], 3) for t in e.at("park")]
        assert parks and rig.parked and not rig.tracking, (
            f"the Abort in the guider wait stopped the park from being "
            f"asked: parks at {parks} s after the guider stop was asked; "
            f"parked {rig.parked}, tracking {rig.tracking}")
        assert abs(parks[0] - CAP) < 1e-6, (
            f"the park was asked {parks[0]} s after the guider stop; the cap "
            f"is {CAP:g} s")
        over = e.at("guider over")
        assert over and over[0] - asked[0] < GUIDE_S, (
            f"premise: the Abort ended the guider stop with the run: {over}")
        assert not e.engine.running
    finally:
        if hold["release"] is not None:
            hold["release"].set()
        await _close(e, gate, None, aborting)
