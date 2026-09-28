"""The auto-reopen roof close waits for the guider only briefly, and reads
its park back (#343, the #270 class; spec 6.17).

Two paths close the roof over a parked mount, and since #270 and #311 they
treated a guider that has stopped answering differently. The wind-down asks
the guider to stop on a task of its own, waits for that stop only up to
``WIND_DOWN_GUIDER_STOP_S``, parks, reads the park back, closes the roof and
reaps the stop afterwards. The auto-reopen roof close (`_close_for_reopen`,
PRO-4 D3: close over the parked gear during a safety pause, wait for safe,
reopen, resume) called `_park_hold`, which awaits the guider stop under the
guider's own ``GUIDE_OP_TIMEOUT_S`` (120 s) before it stops tracking, and then
parked once, taking the park at its word. So a hung guider held the roof open
for two minutes in the weather that closed it, and a lost park was caught
only by `close_observatory`'s refusal, with the night falling back to the
open-sky pause.

NOW the close has the wind-down's shape: the guider stop on its own task,
`_wait_for_the_guider_before_the_park` bounded by ``WIND_DOWN_GUIDER_STOP_S``,
the stop of tracking, the park through `_park_and_read_back`, the roof close,
and the guider stop reaped afterwards, bounded by ``GUIDE_OP_TIMEOUT_S``.

THE HARNESS is test_idle_park_hold's clocked simulator with no run: the
close runs on a task of the test's that the driver clocks (``run.also``),
so every wait in it is on the fake clock. A HUNG GUIDER's stop parks its
caller for ``GUIDE_OP_TIMEOUT_S`` of fake time and then times out, as
``asyncio.wait_for`` has it. A LOST PARK returns with the mount neither
parked nor slewing, the AM5's ``:hP#`` inside a guide pulse (#311). The site
is a fixture, never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (scratchpad s4-enga-mut), never in the shared tree (#254).
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.roof as roof_mod
from astrodeck.devices.base import DomeShutterState

from test_idle_park_hold import _Clocked, sim_hub, temp_store  # noqa: F401

CAP = engine_mod.WIND_DOWN_GUIDER_STOP_S
GUIDE_S = engine_mod.GUIDE_OP_TIMEOUT_S
POLL = engine_mod.IDLE_STOP_FINISH_POLL_S
TASK = "reopen-close"


class _Close:
    """One auto-reopen roof close on the clocked simulator, with no run.

    Records in ``events``, each with its fake time and in order: the guider
    stop asked ("guider stop") and over ("guider over"), every park asked
    ("park asked") and every one that reached the mount ("park"), every roof
    close attempt ("close"), and the close's return ("returned"). ``hang``:
    the guider stop does not answer. ``lose_first_park``: the first park
    returns with the mount neither parked nor slewing."""

    def __init__(self, hub, monkeypatch, *, hang: bool = False,
                 lose_first_park: bool = False):
        self.run = _Clocked(hub, monkeypatch, horizon_s=3600.0)
        run = self.run
        self.engine = run.engine
        self.dome = hub.devices.get("dome")
        assert self.dome is not None and self.dome.connected, (
            "premise: the sim has a roof")
        self.tel = hub.devices["telescope"]
        guider = hub.guider
        assert guider is not None and guider.connected, (
            "premise: the sim rig has a connected guider")
        self.events: list[tuple[str, float]] = []
        real_stop = guider.stop_guiding
        first = [True]

        async def stop_guiding(*a, **kw):
            # Only the close's own stop is recorded and hung, never the
            # fixture's teardown after the test.
            if not first[0]:
                return await real_stop(*a, **kw)
            first[0] = False
            self.events.append(("guider stop", run.clock.t))
            try:
                if hang:
                    await run._park(GUIDE_S)
                    raise asyncio.TimeoutError()
            finally:
                self.events.append(("guider over", run.clock.t))
            return await real_stop(*a, **kw)

        monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
        real_park = self.tel.park
        lost = [0]

        async def park():
            self.events.append(("park asked", run.clock.t))
            if lose_first_park and lost[0] == 0:
                lost[0] += 1
                return
            self.events.append(("park", run.clock.t))
            return await real_park()

        monkeypatch.setattr(self.tel, "park", park)
        inner_close = roof_mod.close_observatory

        async def close_observatory(dome, telescope, **kw):
            self.events.append(("close", run.clock.t))
            return await inner_close(dome, telescope, **kw)

        # `_close_for_reopen` imports it from the module when it closes.
        monkeypatch.setattr(roof_mod, "close_observatory", close_observatory)

    async def close(self) -> bool:
        """`_close_for_reopen` on a task the driver clocks, bounded in real
        time."""
        run = self.run
        task = asyncio.get_running_loop().create_task(
            self.engine._close_for_reopen(self.dome, "rain sensor"), name=TASK)
        run.also.add(task)
        try:
            closed = await asyncio.wait_for(task, 30.0)
        finally:
            run.also.discard(task)
        self.events.append(("returned", run.clock.t))
        return closed

    def at(self, what: str) -> list[float]:
        return [t for w, t in self.events if w == what]

    def order(self, *names: str) -> list[str]:
        return [w for w, _t in self.events if w in names]


async def test_a_hung_guider_holds_the_reopen_close_s_park_for_the_cap(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The guider stop does not answer. The close's park goes ahead exactly
    ``WIND_DOWN_GUIDER_STOP_S`` after the stop was asked, on the fake clock,
    with the stop of tracking at the same instant ahead of it, and says once
    that the guider had not stopped; the roof closes over the parked mount at
    once after the park; and the guider stop is reaped after the close, by
    its own ``GUIDE_OP_TIMEOUT_S``, before the close returns.

    Mutant "guider stop awaited in _park_hold" (the #306 body put back:
    ``await self._park_hold()`` then ``await self._fenced_park()`` in place of
    the guider task, the wait and the stop of tracking): RED (observed) -
        AssertionError: a guider stop that did not answer held the reopen
        close's park 120.000 s after it was asked; the cap is 4.5 s
    """
    c = _Close(sim_hub, monkeypatch, hang=True)
    try:
        closed = await c.close()
        asked = c.at("guider stop")
        parks = c.at("park")
        assert asked and parks, f"premise: a guider stop and a park: {c.events}"
        took = parks[0] - asked[0]
        assert abs(took - CAP) < 1e-6, (
            f"a guider stop that did not answer held the reopen close's park "
            f"{took:.3f} s after it was asked; the cap is {CAP:g} s")
        stops = [(t, who) for t, on, who in c.run.tracking_calls if not on]
        assert stops[:1] == [(parks[0], TASK)], (
            f"the close did not stop tracking itself, at the cap, before its "
            f"park: {stops}")
        said = [m for _l, m, _s in bus_lines if "had not stopped" in m]
        assert len(said) == 1, said
        assert c.order("park", "close")[:2] == ["park", "close"], c.events
        assert c.at("close")[0] == parks[0], (
            f"the roof close waited on something after the park: "
            f"{c.events}")
        over, back = c.at("guider over"), c.at("returned")
        assert over and round(over[0] - asked[0], 6) == GUIDE_S, (
            f"premise: the stop timed out at its own bound: {over}")
        assert back and over[0] <= back[0] <= asked[0] + GUIDE_S + 2 * POLL, (
            f"the guider stop was not reaped after the close, within its "
            f"bound: over at {over}, the close back at {back}")
        assert closed and c.tel.rig.parked
        assert await c.dome.shutter_state() is DomeShutterState.CLOSED
    finally:
        await c.run.close()


async def test_a_reopen_close_park_that_reads_back_unparked_is_asked_again(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The close's first park is lost: park() returns and the mount reads
    neither parked nor slewing. The read-back says so and the park is asked
    once more, at the same fake instant, BEFORE `close_observatory`: the
    second park parks, and the roof closes over the parked mount, where the
    close used to be refused and the night to fall back to the open-sky
    pause with the roof open.

    Mutant "fenced park with no read-back" (`_fenced_park` parking with a
    bare ``asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)``, as before #343):
    RED (observed) -
        AssertionError: the reopen close's park read back unparked and was
        not asked again before the roof close: order ['park asked', 'close'];
        the close returned False
    """
    c = _Close(sim_hub, monkeypatch, lose_first_park=True)
    try:
        closed = await c.close()
        order = c.order("park asked", "close")
        assert order[:3] == ["park asked", "park asked", "close"], (
            f"the reopen close's park read back unparked and was not asked "
            f"again before the roof close: order {order}; the close returned "
            f"{closed}")
        asks = c.at("park asked")
        assert asks[1] == asks[0], (
            f"the park was asked again {asks[1] - asks[0]:.2f} s after the "
            f"first, not at once")
        again = [m for _l, m, _s in bus_lines if "parking once more" in m]
        assert len(again) == 1, again
        assert closed and c.tel.rig.parked
        assert await c.dome.shutter_state() is DomeShutterState.CLOSED
    finally:
        await c.run.close()


#: When the Abort lands, in fake seconds after the close began: inside the
#: wait for a guider that does not answer, well before its cap.
ABORT_AT = 1.0


async def test_an_abort_in_the_reopen_close_s_guider_wait_starts_no_motion(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """An Abort (a cancel of the task running the close) lands ``ABORT_AT``
    into the close's wait for a guider stop that does not answer. The wait
    notes it and goes on to its cap, as the wind-down's does (#305); then
    the close stops tracking itself, at the cap, and raises the cancel
    before its park. No park is asked and the roof is not moved: an Abort
    starts no motion, and the stop of tracking is still made because the
    close has already ended the idle stop's task, which would have made it.
    The guider stop is cancelled with the close rather than left running,
    and the close ends cancelled. (Verifier's case: the ``if cancelled:``
    branch of `_close_for_reopen` had no test, so the first mutant below
    survived the rest of the suite for #343. Its mutants were run in the
    private scratch copy s4-enga-verify-mut.)

    Mutant "cancel in the reopen guider wait swallowed" (the ``if cancelled:
    raise asyncio.CancelledError()`` in `_close_for_reopen` deleted): RED
    (observed) -
        AssertionError: an Abort that landed 1 s into the reopen close's
        guider wait was swallowed: the close parked at [4.5] s and tried the
        roof at [4.5] s after the guider stop was asked; the close was
        cancelled: False
    Mutant "abort raised before the close's stop of tracking" (the
    ``if cancelled:`` raise moved ahead of ``_stop_tracking_quietly``): RED
    (observed) -
        AssertionError: the close did not stop tracking itself at the cap
        before it raised the Abort: []
    Mutant "guider stop left running on an abort" (the ``guider_stop.cancel()``
    in `_close_for_reopen`'s ``except BaseException`` deleted): RED
    (observed) -
        AssertionError: the guider stop was not cancelled with the close, at
        the cap: it was over at [] s (its own bound is 120 s)
    """
    c = _Close(sim_hub, monkeypatch, hang=True)
    run = c.run
    loop = asyncio.get_running_loop()
    try:
        task = loop.create_task(
            c.engine._close_for_reopen(c.dome, "rain sensor"), name=TASK)
        run.also.add(task)

        async def abort_on_the_clock():
            await run._park(ABORT_AT)
            task.cancel()

        abort = loop.create_task(abort_on_the_clock(), name="abort")
        run.also.add(abort)
        try:
            done, _ = await asyncio.wait({task}, timeout=30.0)
        finally:
            run.also.discard(task)
            run.also.discard(abort)
        assert done, f"premise: the close ended: {c.events}"
        # The guider stop's own cancel lands on its next turn.
        for _ in range(10):
            await asyncio.sleep(0)
        asked = c.at("guider stop")
        assert asked and abort.done(), (
            f"premise: the guider stop was asked and the Abort sent: "
            f"{c.events}")
        rel = [round(t - asked[0], 3) for t in c.at("park asked")]
        tried = [round(t - asked[0], 3) for t in c.at("close")]
        assert rel == [] and tried == [] and task.cancelled(), (
            f"an Abort that landed {ABORT_AT:g} s into the reopen close's "
            f"guider wait was swallowed: the close parked at {rel} s and "
            f"tried the roof at {tried} s after the guider stop was asked; "
            f"the close was cancelled: {task.cancelled()}")
        stops = [(round(t - asked[0], 6), who)
                 for t, on, who in run.tracking_calls if not on]
        assert stops == [(CAP, TASK)], (
            f"the close did not stop tracking itself at the cap before it "
            f"raised the Abort: {stops}")
        said = [m for _l, m, _s in bus_lines if "had not stopped" in m]
        assert len(said) == 1, said
        over = [round(t - asked[0], 6) for t in c.at("guider over")]
        assert over == [CAP], (
            f"the guider stop was not cancelled with the close, at the cap: "
            f"it was over at {over} s (its own bound is {GUIDE_S:g} s)")
        assert not c.tel.rig.parked
        assert await c.dome.shutter_state() is DomeShutterState.OPEN
    finally:
        await c.run.close()


async def test_control_a_guider_that_answers_costs_the_close_nothing(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL. The guider stop answers at once and the park takes. The
    close stops tracking, parks, closes the roof and returns, all at the
    instant the guider stop was asked: the wait for the guider costs a
    guider that answers nothing, one park, nothing said about the guider,
    and the roof closed over the parked mount."""
    c = _Close(sim_hub, monkeypatch)
    try:
        closed = await c.close()
        asked = c.at("guider stop")
        assert asked, f"premise: the guider stop was asked: {c.events}"
        when = {w: c.at(w) for w in ("guider over", "park", "close",
                                     "returned")}
        assert all(ts == [asked[0]] for ts in when.values()), (
            f"a guider that answered held the close: {c.events}")
        assert not [m for _l, m, _s in bus_lines if "had not stopped" in m]
        assert closed and c.tel.rig.parked
        assert await c.dome.shutter_state() is DomeShutterState.CLOSED
    finally:
        await c.run.close()
