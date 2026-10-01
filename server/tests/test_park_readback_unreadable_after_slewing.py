"""A park read-back that saw the park on its way, and then cannot read the
park state, does not call the mount parked: the wind-down stops its tracking
instead (#447, S4 review item 7; spec 6.17, owner list items 19 and 24). And
it waits for the park to its deadline before it says so (#525, S7
orchestrator ruling 4).

Since S4 the park read-back waits for a park on its way (`_await_the_park`,
test_park_readback_async_park.py): it polls ``AtPark``, and ``Slewing``
where the driver has it, for up to what is left of the park's own
``PARK_TIMEOUT_S``. A poll whose park state could not be read answered
``"unreadable"`` at once, whatever the polls before it had seen, and
`_park_and_read_back` takes that as the driver's park standing: "mount
parked (its park state could not be read back)", and True. That rule is
S3's, for one read straight after the park, where an unreadable read is no
evidence the park was lost. Inside S4's wait it also covered a mount already
seen "not parked, slewing", whose park was known to be in flight and never
seen to arrive, and whose link then failed: the one park that cannot be
taken on trust. The wind-down, told the park took, sent no stop of tracking
(`_stop_after_a_failed_park` runs only for a park that did not take, #270),
so a park the dropped link had cut short left the mount tracking after the
run, and the night's record said it had parked.

S5 (#447) made an unreadable poll after a poll that saw the mount slewing
``"unconfirmed"``, at once: not parked, not asked again, so the wind-down
made its final, bounded stop of tracking a few seconds into the park slew.
That sent a stop into a park still in flight, the S4 defect the waited
read-back was built to remove, on one read a flaky link had dropped, and a
park that went on to arrive was recorded as one that did not (#525).

NOW (S7 orchestrator ruling 4) such a poll keeps waiting, to the park's
deadline. A later poll that reads parked answers parked; only the deadline
answers ``"unconfirmed"``, and then the wind-down stops the mount's tracking
and reads it back. An unreadable poll asks no slewing state (a "not
slewing" beside a park state nobody can read may be a park that arrived).
An unreadable first poll, with no slewing seen, keeps S3's rule.

THE HARNESS is test_park_readback_async_park's: test_unsafe_ending_parks_at_
once's `_Ending`, a clocked-simulator night that scripted rain ends on a
SafetyAbort, with the wind-down's park task clocked (`_clock_the_park`), so
the read-back's polls are sleeps on the fake clock. THE DOUBLE is an
asynchronous park whose link fails mid-slew: ``park()`` returns at once and
the mount reads not parked and slewing, until ``UNREADABLE_S`` fake seconds
after the park was asked; from then on its park-state query raises, until
the case says the link is back and the mount has arrived. The park leaves
the mount tracking, as a park cut short may, so a stop of tracking is what
ends it when it never arrives. The idle stop was confirmed long before the
rain, so nothing is handed to the park. The site is a fixture, never the
real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. The #447 mutants were applied in a private scratch copy
of server/ (S5-ENG-SAFE-mut); every mutant here was run again on the S7
code, with the old pin, in S7-ENG-SAFE-r2-mut, and every quote is from that
run. Both copies are in the session scratchpad, never the shared tree
(#254).
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_park_readback_async_park import _clock_the_park
from test_unsafe_ending_parks_at_once import _Ending

#: When the park-state query starts failing, in fake seconds after the park
#: was asked: a few polls into the slew, far inside ``PARK_TIMEOUT_S``.
UNREADABLE_S = 5.0
#: When the link comes back in the case where the park arrives: seven
#: unreadable polls after the first, still far inside the bound.
BACK_S = 12.0
POLL = engine_mod.PARK_READ_BACK_POLL_S
BOUND = engine_mod.PARK_TIMEOUT_S
#: The failed park's stop, read back as taken.
STOPPED = ("the park did not complete, so the mount was asked to stop "
           "tracking instead, and it has stopped tracking")


def _link_fails_mid_park(e: _Ending, monkeypatch, *,
                         unreadable_s: float, back_s: float | None = None,
                         slewing_while_unreadable: bool = True) -> dict:
    """The simulator's mount with an asynchronous park whose link fails
    ``unreadable_s`` fake seconds after the first park was asked: before
    that it reads not parked and slewing; from then on ``is_parked`` raises.
    With ``back_s`` the link comes back then, and the mount has arrived: it
    reads parked, not slewing, and not tracking. Without it the park leaves
    the mount tracking and never arrives. ``slewing_while_unreadable``:
    what a slewing read answers while the park state cannot be read (a
    mount that has arrived reads False there). Before the first park every
    read is the simulator's own.

    Records: the fake time of every park asked ("asked"), every park-state
    read after the first park as (fake time, answer or "raised")
    ("parked"), and every slewing read ("slewing")."""
    run = e.run
    tel = run.hub.devices["telescope"]
    rec: dict = {"asked": [], "parked": [], "slewing": []}
    real_parked, real_slewing = tel.is_parked, tel.is_slewing

    def arrived() -> bool:
        return (back_s is not None and bool(rec["asked"])
                and run.clock.t >= rec["asked"][0] + back_s)

    def unreadable() -> bool:
        return (bool(rec["asked"]) and not arrived()
                and run.clock.t >= rec["asked"][0] + unreadable_s)

    async def park():
        rec["asked"].append(run.clock.t)
        e.events.append(("park asked", run.clock.t))
        tel.rig.tracking = True

    async def is_parked():
        if not rec["asked"]:
            return await real_parked()
        if arrived():
            tel.rig.tracking = False
            rec["parked"].append((run.clock.t, True))
            return True
        if unreadable():
            rec["parked"].append((run.clock.t, "raised"))
            raise RuntimeError("the mount's link dropped the AtPark read")
        rec["parked"].append((run.clock.t, False))
        return False

    async def is_slewing():
        if not rec["asked"]:
            return await real_slewing()
        rec["slewing"].append(run.clock.t)
        if arrived():
            return False
        if unreadable():
            return slewing_while_unreadable
        return True

    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "is_parked", is_parked)
    monkeypatch.setattr(tel, "is_slewing", is_slewing)
    return rec


async def test_an_unreadable_poll_after_slewing_waits_to_the_deadline(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The wind-down's park is on its way (the mount reads slewing) when,
    ``UNREADABLE_S`` in, its park state stops answering, and it never
    answers again. The read-back does not call it parked, and it does not
    give up on it at the first unreadable poll either: it polls on, once a
    ``PARK_READ_BACK_POLL_S``, to the park's deadline, ``PARK_TIMEOUT_S``
    after the park was asked, and only then says the park is not confirmed.
    It asks no second park. The wind-down then sends its final stop of
    tracking, at the deadline and not before, and reads it back: "the park
    did not complete, so the mount was asked to stop tracking instead, and
    it has stopped tracking". The mount ends not tracking, and no line says
    it parked.

    DELIBERATE PIN CHANGE (S7 orchestrator ruling 4, #525). This case was
    test_an_unreadable_poll_after_slewing_stops_tracking, and pinned the
    stop at the first unreadable poll, 5 s after the park (#447's rule). The
    ruling moves it to the deadline. The old pin, HEAD's copy of this file
    run against the ruling's code in the private scratch copy (observed) -
        AssertionError: the stop of tracking came at [240.0] s after the
        park; it belongs at the poll that could not read the park state,
        5.0 s, once, and not at the 240 s bound

    Mutant "unconfirmed at once" (S5's rule back: `_await_the_park`
    returns ``"unconfirmed"`` at the first unreadable poll after a slewing
    read): RED (observed) -
        AssertionError: a park seen slewing whose park state then could not
        be read was given up at the first unreadable poll: the wind-down's
        stops of tracking at [5.0] s after the park, the park state read
        unreadable at [5.0] s; the ruling waits to the 240 s deadline
    Mutant "unreadable after slewing read as parked" (`_await_the_park`
    returns ``"unreadable"`` for every unreadable poll, the S4 code #447 was
    filed against): RED (observed) -
        AssertionError: a park seen slewing whose park state then could not
        be read was taken as parked: lines ['mount parked (its park state
        could not be read back)'], the wind-down's stops of tracking at []
        s after the park, the mount still tracking: True
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _link_fails_mid_park(e, monkeypatch, unreadable_s=UNREADABLE_S)
    tel = e.run.hub.devices["telescope"]
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        seen = [round(t - first, 2) for t in rec["slewing"]]
        failed = [round(t - first, 2) for t, got in rec["parked"]
                  if got == "raised"]
        assert seen and seen[0] == 0.0 and failed, (
            f"premise: the read-back saw the park on its way at once, and "
            f"a later poll could not read the park state: slewing reads at "
            f"{seen}, failed park-state reads at {failed}")
        said = [m for _l, m, _s in bus_lines]
        parked = [m for m in said if m.startswith("mount parked")]
        stops = [round(t - first, 2) for t, on, _who in e.run.tracking_calls
                 if not on and t >= first]
        assert parked == [] and stops, (
            f"a park seen slewing whose park state then could not be read "
            f"was taken as parked: lines {parked}, the wind-down's stops of "
            f"tracking at {stops} s after the park, the mount still "
            f"tracking: {tel.rig.tracking}")
        assert min(stops) >= BOUND, (
            f"a park seen slewing whose park state then could not be read "
            f"was given up at the first unreadable poll: the wind-down's "
            f"stops of tracking at {stops} s after the park, the park state "
            f"read unreadable at {failed[:3]} s; the ruling waits to the "
            f"{BOUND:g} s deadline")
        assert len(asked) == 1, (
            f"the park was asked again into a slew it could not read: "
            f"parks at {[round(t - first, 2) for t in asked]} s")
        assert failed[0] < UNREADABLE_S + POLL and failed[-1] == BOUND \
            and len(failed) >= BOUND - UNREADABLE_S, (
                f"the read-back did not poll on, once a "
                f"{POLL:g} s, from the first unreadable poll to the "
                f"deadline: unreadable reads at {failed[:3]} ... "
                f"{failed[-3:]} ({len(failed)} in all)")
        assert [t for t in seen if t > UNREADABLE_S] == [], (
            f"an unreadable poll asked the slewing state: slewing reads at "
            f"{seen}")
        unconfirmed = [m for m in said if "park is not confirmed" in m]
        assert len(unconfirmed) == 1, unconfirmed
        assert stops == [BOUND], (
            f"the stop of tracking came at {stops} s after the park; it "
            f"belongs at the deadline, {BOUND:g} s, once")
        reads = [round(t - first, 2) for t, _who, _i in e.n.reads
                 if t >= first]
        assert reads and reads[0] >= stops[0], (
            f"the stop was not read back: tracking reads at {reads} s")
        assert [m for m in said if m == STOPPED] == [STOPPED], (
            [m for m in said if "the park did not complete" in m])
        assert tel.rig.tracking is False
    finally:
        await e.run.close()


async def test_a_park_that_arrives_after_unreadable_polls_is_parked(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The park is on its way; ``UNREADABLE_S`` in, the link drops the park
    state for seven polls, and at ``BACK_S`` it comes back with the mount
    arrived: parked, not slewing, not tracking. The read-back, still
    waiting, reads that poll and answers parked: "mount parked", one park,
    no line that the park is not confirmed, and no stop of tracking after
    it (a stop sent into the park while it was still on its way is the S4
    defect, #525).

    Mutant "unconfirmed at once" (as above): RED (observed) -
        AssertionError: a park that arrived at 12 s, after the link dropped
        its park state at 5 s, was not read as parked: lines [], 'not
        confirmed' lines ["the mount was slewing to park and then its park
        state could not be read up to the park's deadline during wind-down:
        the park is not confirmed"], the wind-down's stops of tracking at
        [5.0] s after the park, park-state reads [(0.0, False), (1.0,
        False), (2.0, False), (3.0, False), (4.0, False), (5.0, 'raised')]
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _link_fails_mid_park(e, monkeypatch, unreadable_s=UNREADABLE_S,
                               back_s=BACK_S)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        reads = [(round(t - first, 2), got) for t, got in rec["parked"]]
        failed = [t for t, got in reads if got == "raised"]
        assert failed and failed[0] < UNREADABLE_S + POLL and rec["slewing"] \
            and rec["slewing"][0] == first, (
                f"premise: the read-back saw the park on its way at once, "
                f"and then the park state could not be read: park-state "
                f"reads {reads}")
        said = [m for _l, m, _s in bus_lines]
        parked = [m for m in said if m.startswith("mount parked")]
        unconfirmed = [m for m in said if "park is not confirmed" in m]
        stops = [round(t - first, 2) for t, on, _who in e.run.tracking_calls
                 if not on and t >= first]
        assert parked == ["mount parked"] and unconfirmed == [] \
            and stops == [], (
                f"a park that arrived at {BACK_S:g} s, after the link dropped "
                f"its park state at {UNREADABLE_S:g} s, was not read as "
                f"parked: lines {parked}, 'not confirmed' lines "
                f"{unconfirmed}, the wind-down's stops of tracking at {stops} "
                f"s after the park, park-state reads {reads[:6]}")
        arrived = [t for t, got in reads if got is True]
        assert arrived and arrived[0] < BACK_S + POLL, (
            f"the parked read came at {arrived}, not the first poll after "
            f"the link came back at {BACK_S:g} s")
        assert len(asked) == 1, [round(t - first, 2) for t in asked]
        assert not [m for m in said if "the park did not complete" in m]
    finally:
        await e.run.close()


async def test_an_unreadable_poll_asks_no_slewing_state(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The park is on its way, and ``UNREADABLE_S`` in its park state stops
    answering while its slewing state answers "not slewing": a mount that
    may well have arrived, whose AtPark read the link keeps dropping. The
    read-back asks no slewing state on an unreadable poll, so it never
    reads that as a lost park and never parks a second time into a mount
    that may be parked or still moving. It waits to the deadline, as the
    first case, and the wind-down stops tracking there, once.

    Mutant "an unreadable poll asks the slewing state" (``if state is False
    and slewing_known:`` made ``if slewing_known:`` in `_await_the_park`,
    so the "not slewing" read beside an unreadable park state answers
    "lost"): RED (observed) -
        AssertionError: a park whose state could not be read was asked
        again: parks at [0.0, 5.0] s after the first, slewing reads at [0.0,
        1.0, 2.0, 3.0, 4.0, 5.0] s
    (It turns the first case red too, at its check that no unreadable poll
    asks the slewing state: every poll to the deadline asks it.)
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _link_fails_mid_park(e, monkeypatch, unreadable_s=UNREADABLE_S,
                               slewing_while_unreadable=False)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        seen = [round(t - first, 2) for t in rec["slewing"]]
        assert len(asked) == 1, (
            f"a park whose state could not be read was asked again: parks "
            f"at {[round(t - first, 2) for t in asked]} s after the first, "
            f"slewing reads at {seen[:10]} s")
        assert seen and max(seen) < UNREADABLE_S, (
            f"an unreadable poll asked the slewing state: slewing reads at "
            f"{seen[:10]} s")
        stops = [round(t - first, 2) for t, on, _who in e.run.tracking_calls
                 if not on and t >= first]
        assert stops == [BOUND], (
            f"the stop of tracking came at {stops} s after the park; it "
            f"belongs at the deadline, {BOUND:g} s, once")
    finally:
        await e.run.close()


async def test_control_an_unreadable_first_poll_keeps_the_driver_s_park(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """CONTROL: S3's rule, unchanged. The park state cannot be read from the
    first poll on, so no poll ever saw the park on its way, and an
    unreadable read is no evidence it was lost: the driver's park stands,
    "mount parked (its park state could not be read back)", one park, and
    the wind-down sends no stop of tracking after it and says nothing of a
    park that did not complete. (test_mosaic_spec_claims's scripted
    "unreadable" mount holds the same rule on a bare engine.)

    Mutant "every unreadable poll unconfirmed" (the slewing-seen condition
    dropped, so any unreadable poll answers ``"unconfirmed"``): RED
    (observed at #447, and again on the S7 code) -
        AssertionError: an unreadable first poll, with no slewing seen, no
        longer left the driver's park standing: lines [], stops of tracking
        at [0.0] s after the park
    Mutant "an unreadable first poll waits too" (the S7 form of that
    mutant: ``if state is None and not slewing_seen:`` made ``if False:``,
    so an unreadable first poll waits to the deadline like one after a
    slew): RED (observed) -
        AssertionError: an unreadable first poll, with no slewing seen, no
        longer left the driver's park standing: lines [], stops of tracking
        at [240.0] s after the park
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _link_fails_mid_park(e, monkeypatch, unreadable_s=0.0)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        asked = rec["asked"]
        assert asked, f"premise: the wind-down parked: {e.events}"
        first = asked[0]
        assert rec["slewing"] == [] and rec["parked"][:1] == [
            (first, "raised")], (
            f"premise: the first poll could not read the park state, and no "
            f"slewing read was taken: {rec}")
        said = [m for _l, m, _s in bus_lines]
        parked = [m for m in said if m.startswith("mount parked")]
        stops = [round(t - first, 2) for t, on, _who in e.run.tracking_calls
                 if not on and t >= first]
        assert parked == ["mount parked (its park state could not be read "
                          "back)"] and stops == [], (
            f"an unreadable first poll, with no slewing seen, no longer left "
            f"the driver's park standing: lines {parked}, stops of tracking "
            f"at {stops} s after the park")
        assert len(asked) == 1, [round(t - first, 2) for t in asked]
        assert not [m for m in said if "the park did not complete" in m
                    or "park is not confirmed" in m]
    finally:
        await e.run.close()
