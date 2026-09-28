"""The wind-down's park read-back waits for an asynchronous park (item 9, the
follow-up to S3 orchestrator ruling 3, spec 6.17 and "Still waiting on the
owner" item 24).

S3 made the wind-down read its park back and park once more, at once, when
the mount said it was not parked (`_park_and_read_back`, #311). It read
``AtPark`` ONCE. The Alpaca ``park()`` is a bare PUT: the driver accepts the
park, starts the slew and answers, and ``AtPark`` goes true only when the
mount arrives, tens of seconds later. So against an Alpaca mount the one read
said "not parked" about every park there is: the wind-down parked a second
time into the slew, read that back unparked as well, gave up, and asked the
mount to stop tracking in the middle of its park slew
(`_stop_after_a_failed_park`), with the roof close refusing behind it.

NOW the read-back polls ``AtPark``, and ``Slewing`` where the driver reports
it, every ``PARK_READ_BACK_POLL_S``, bounded by what is left of the park's
own ``PARK_TIMEOUT_S``, before it calls the park lost. A mount that reads
neither parked nor slewing is parked again at once, as ruling 3 requires
(the control, test_wind_down_guider_stop_before_park.py's silent no-op
double); a driver with no slewing state is polled on ``AtPark`` alone.

THE HARNESS is test_unsafe_ending_parks_at_once's `_Ending`: a clocked
simulator night that scripted rain ends on a SafetyAbort, its wind-down on a
task of its own that the harness clocks. The wind-down's park runs on a task
of its own too (`_wind_down_park`, #305), which `_clock_the_park` hands to
the driver, so the read-back's polls are sleeps on the fake clock. THE
DOUBLE is an asynchronous park: ``park()`` returns at once, and the mount
then reads slewing, not parked, for ``SLEW_S`` fake seconds, and parked from
then on. The idle stop is decided and confirmed long before the rain, so
nothing is handed to the park. The site is a fixture, never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (scratchpad s4-enga-mut), never in the shared tree (#254).
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_unsafe_ending_parks_at_once import _Ending

#: How long the double's park slew takes, in fake seconds.
SLEW_S = 20.0
#: The read-back's poll, the tolerance on "read back as it arrived".
POLL = engine_mod.PARK_READ_BACK_POLL_S


def _clock_the_park(e: _Ending, monkeypatch) -> None:
    """Hand the wind-down's park task to the harness's driver.

    `_wind_down_park_and_close` runs the park on a task of its own and waits
    for it through ``asyncio.wait`` (#305). The harness clocks the run task,
    the idle-stop task and the wind-down's task (``run.also``), so a sleep
    of the park task's, the read-back's poll, would be a real one while the
    fake clock stood still, and a park that arrives on the fake clock would
    never be seen to arrive. So while the park runs, its task is clocked and
    the task waiting for it (the wind-down's) counts as parked, as
    test_unsafe_ending_parks_at_once's `_clock_the_wind_down` does for the
    run task that awaits the wind-down."""
    run = e.run
    inner = e.engine._wind_down_park

    def wind_down_park(tel):
        caller = asyncio.current_task()

        async def park_on_the_clock():
            me = asyncio.current_task()
            waiting = asyncio.get_running_loop().create_future()
            run.also.add(me)
            run._parked[caller] = waiting
            try:
                return await inner(tel)
            finally:
                run.also.discard(me)
                if not waiting.done():
                    waiting.set_result(None)

        return park_on_the_clock()

    monkeypatch.setattr(e.engine, "_wind_down_park", wind_down_park)


def _async_park(e: _Ending, monkeypatch, *, slew_s: float = SLEW_S) -> dict:
    """The simulator's mount with an asynchronous park, as an Alpaca driver
    has it: ``park()`` returns at once; from then on the mount reads
    slewing and not parked until ``slew_s`` fake seconds after the first
    park was asked, and parked, not slewing and not tracking afterwards (as
    the simulator's own park leaves it). A second park asked during the
    slew changes nothing, as a Park PUT to a mount already parking does not.
    Before the first park the reads are the simulator's own.

    Records: the fake time of every park asked ("asked"), when the mount
    arrived ("arrived"), and every park-state and slewing read after the
    first park ("reads")."""
    run = e.run
    tel = run.hub.devices["telescope"]
    rec: dict = {"asked": [], "arrived": None, "reads": []}
    real_parked, real_slewing = tel.is_parked, tel.is_slewing

    def arrive() -> None:
        if (rec["arrived"] is None
                and run.clock.t >= rec["asked"][0] + slew_s):
            rec["arrived"] = run.clock.t
            tel.rig.parked = True
            tel.rig.tracking = False

    async def park():
        rec["asked"].append(run.clock.t)
        e.events.append(("park asked", run.clock.t))

    async def is_parked():
        if not rec["asked"]:
            return await real_parked()
        arrive()
        rec["reads"].append(("parked", run.clock.t))
        return bool(tel.rig.parked)

    async def is_slewing():
        if not rec["asked"]:
            return await real_slewing()
        arrive()
        rec["reads"].append(("slewing", run.clock.t))
        return rec["arrived"] is None

    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "is_parked", is_parked)
    monkeypatch.setattr(tel, "is_slewing", is_slewing)
    return rec


async def test_an_asynchronous_park_is_waited_for_not_repeated_or_stopped(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The rain ends the run and the wind-down parks a mount whose park is
    asynchronous: ``park()`` returns at once and the slew takes ``SLEW_S``.
    The read-back must wait for it: exactly one park, no
    ``set_tracking(False)`` from anyone while the mount slews to park, and
    "mount parked" said once, as the mount arrives (within one poll of it),
    with no "parking once more" and no failed park's stop.

    Mutant "read AtPark once" (the read-back as S3 built it: one
    `_parked_state` read after each park, no poll, no slewing read): RED
    (observed) -
        AssertionError: the asynchronous park was asked 2 times, at [0.0,
        0.0] s after the first: the read-back did not wait for the slew; the
        mount arrived at None s
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _async_park(e, monkeypatch)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        arrived = rec["arrived"]
        assert len(asked) == 1, (
            f"the asynchronous park was asked {len(asked)} times, at "
            f"{[round(t - first, 2) for t in asked]} s after the first: the "
            f"read-back did not wait for the slew; the mount arrived at "
            f"{None if arrived is None else round(arrived - first, 2)} s")
        assert arrived is not None and SLEW_S <= arrived - first <= SLEW_S \
            + POLL, (f"the read-back saw the mount arrive at "
                     f"{None if arrived is None else arrived - first} s; the "
                     f"slew took {SLEW_S:g} s and the poll is {POLL:g} s")
        stops = [round(t - first, 2) for t, on, _who in e.run.tracking_calls
                 if not on and first <= t <= arrived]
        assert stops == [], (
            f"set_tracking(False) was sent during the park slew, at {stops} "
            f"s after the park was asked")
        said = [m for _l, m, _s in bus_lines]
        parked = [m for m in said if m.startswith("mount parked")]
        assert parked == ["mount parked"], parked
        again = [m for m in said if "parking once more" in m
                 or "the park did not complete" in m]
        assert again == [], again
        slewing = [t for w, t in rec["reads"] if w == "slewing"]
        assert slewing and slewing[0] == first, (
            f"premise: the read-back asked the slewing state at once: "
            f"{rec['reads'][:4]}")
        assert e.run.hub.devices["telescope"].rig.parked
    finally:
        await e.run.close()


async def test_a_driver_with_no_slewing_state_is_polled_on_atpark_alone(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The same asynchronous park, on a driver whose slewing query cannot
    answer (it raises, as a driver without the property does). The read-back
    cannot tell a slew from a lost park, so it polls ``AtPark`` alone until
    the mount arrives, within the park's own ``PARK_TIMEOUT_S``: one park,
    "mount parked" as it arrives, and the slewing query asked once and then
    not again.

    Mutant "no slewing state reads as not slewing" (a slewing read that
    cannot answer taken for False, so the park is called lost): RED
    (observed) -
        AssertionError: a driver with no slewing state had its park asked 2
        times, at [0.0, 0.0] s: the read-back took an unanswerable slewing
        query for a lost park
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _async_park(e, monkeypatch)
    tel = e.run.hub.devices["telescope"]
    asks: list[float] = []

    async def is_slewing():
        if rec["asked"]:
            asks.append(e.run.clock.t)
        raise NotImplementedError("this driver has no Slewing property")

    monkeypatch.setattr(tel, "is_slewing", is_slewing)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        assert len(asked) == 1, (
            f"a driver with no slewing state had its park asked "
            f"{len(asked)} times, at {[round(t - first, 2) for t in asked]} "
            f"s: the read-back took an unanswerable slewing query for a lost "
            f"park")
        arrived = rec["arrived"]
        assert arrived is not None and arrived - first <= SLEW_S + POLL, (
            f"the read-back saw the mount arrive at {arrived} s")
        assert len(asks) == 1, (
            f"the slewing query that cannot answer was asked {len(asks)} "
            f"times; after the first the read-back polls AtPark alone")
        parked = [m for _l, m, _s in bus_lines if m.startswith("mount parked")]
        assert parked == ["mount parked"], parked
        assert tel.rig.parked
    finally:
        await e.run.close()


async def test_a_park_still_slewing_at_its_bound_is_not_asked_again(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The asynchronous park does not arrive within its bound: the mount
    reads slewing for three times the park's whole ``PARK_TIMEOUT_S``. At
    that bound the park has had all the time a park that times out has, and
    it is treated as one (6.17: "a park that raised or timed out is not
    asked again"): one park, a line that says it did not arrive within its
    bound, and the failed park's stop of tracking after it, at the bound and
    never before. (A slew that ends, far past the bound, rather than one that
    never does: a read-back that polled past its bound would otherwise hang
    the run's teardown, which waits for the park, and never report.)

    Mutant "the poll unbounded" (the read-back's deadline check deleted, so
    it polls a slewing mount for as long as it slews): RED (observed) -
        AssertionError: the read-back waited for the park past its 240 s
        bound: the mount was seen to arrive at 720.0 s, with 1 park(s)
    Mutant "a late park is lost" (the bound's outcome taken for a lost park,
    so the park is asked a second time at the bound): RED (observed) -
        AssertionError: a park still slewing at its 240 s bound was asked
        again: parks at [0.0, 240.0] s
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    bound = engine_mod.PARK_TIMEOUT_S
    rec = _async_park(e, monkeypatch, slew_s=3 * bound)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        arrived = rec["arrived"]
        assert arrived is None, (
            f"the read-back waited for the park past its {bound:g} s bound: "
            f"the mount was seen to arrive at {round(arrived - first, 2)} s, "
            f"with {len(asked)} park(s)")
        assert len(asked) == 1, (
            f"a park still slewing at its {bound:g} s bound was asked again: "
            f"parks at {[round(t - first, 2) for t in asked]} s")
        said = [m for _l, m, _s in bus_lines]
        late = [m for m in said if "had not arrived" in m]
        assert len(late) == 1, late
        stops = [round(t - first, 2) for t, on, _who in e.run.tracking_calls
                 if not on and t >= first]
        assert stops and stops[0] == bound, (
            f"the failed park's stop of tracking came at {stops} s after the "
            f"park; it belongs at the park's {bound:g} s bound and not "
            f"before")
        fallback = [m for m in said if "the park did not complete" in m]
        assert len(fallback) == 1, fallback
    finally:
        await e.run.close()
