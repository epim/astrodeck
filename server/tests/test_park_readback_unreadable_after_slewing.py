"""A park read-back that saw the park on its way, and then cannot read the
park state, does not call the mount parked: the wind-down stops its tracking
instead (#447, S4 review item 7; spec 6.17, owner list items 19 and 24).

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

NOW an unreadable poll after a poll that saw the mount slewing is
``"unconfirmed"``: said in words, not asked again (a park may still be in
flight, and a second one into it is the S4 defect), and not parked, so the
wind-down makes its final, bounded stop of tracking and reads it back. An
unreadable first poll, with no slewing seen, keeps S3's rule.

THE HARNESS is test_park_readback_async_park's: test_unsafe_ending_parks_at_
once's `_Ending`, a clocked-simulator night that scripted rain ends on a
SafetyAbort, with the wind-down's park task clocked (`_clock_the_park`), so
the read-back's polls are sleeps on the fake clock. THE DOUBLE is an
asynchronous park whose link fails mid-slew: ``park()`` returns at once and
the mount reads not parked and slewing, until ``UNREADABLE_S`` fake seconds
after the park was asked; from then on its park-state query raises. The
mount never arrives, and the park leaves it tracking, as a park cut short
may, so a stop of tracking is what ends it. The idle stop was confirmed long
before the rain, so nothing is handed to the park. The site is a fixture,
never the real one.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S5-ENG-SAFE-mut, in the session scratchpad), never in the shared
tree (#254).
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_park_readback_async_park import _clock_the_park
from test_unsafe_ending_parks_at_once import _Ending

#: When the park-state query starts failing, in fake seconds after the park
#: was asked: a few polls into the slew, far inside ``PARK_TIMEOUT_S``.
UNREADABLE_S = 5.0
POLL = engine_mod.PARK_READ_BACK_POLL_S
BOUND = engine_mod.PARK_TIMEOUT_S
#: The failed park's stop, read back as taken.
STOPPED = ("the park did not complete, so the mount was asked to stop "
           "tracking instead, and it has stopped tracking")


def _link_fails_mid_park(e: _Ending, monkeypatch, *,
                         unreadable_s: float) -> dict:
    """The simulator's mount with an asynchronous park whose link fails
    ``unreadable_s`` fake seconds after the first park was asked: before
    that it reads not parked and slewing; from then on ``is_parked`` raises.
    The park leaves the mount tracking and never arrives. Before the first
    park every read is the simulator's own.

    Records: the fake time of every park asked ("asked"), every park-state
    read after the first park as (fake time, answer or "raised")
    ("parked"), and every slewing read ("slewing")."""
    run = e.run
    tel = run.hub.devices["telescope"]
    rec: dict = {"asked": [], "parked": [], "slewing": []}
    real_parked, real_slewing = tel.is_parked, tel.is_slewing

    async def park():
        rec["asked"].append(run.clock.t)
        e.events.append(("park asked", run.clock.t))
        tel.rig.tracking = True

    async def is_parked():
        if not rec["asked"]:
            return await real_parked()
        if run.clock.t >= rec["asked"][0] + unreadable_s:
            rec["parked"].append((run.clock.t, "raised"))
            raise RuntimeError("the mount's link dropped the AtPark read")
        rec["parked"].append((run.clock.t, False))
        return False

    async def is_slewing():
        if not rec["asked"]:
            return await real_slewing()
        rec["slewing"].append(run.clock.t)
        return True

    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "is_parked", is_parked)
    monkeypatch.setattr(tel, "is_slewing", is_slewing)
    return rec


async def test_an_unreadable_poll_after_slewing_stops_tracking(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The wind-down's park is on its way (the mount reads slewing) when,
    ``UNREADABLE_S`` in, its park state stops answering. The read-back does
    not call it parked: it says the park is not confirmed and asks no second
    park, and the wind-down then sends its final stop of tracking at that
    poll, not at the park's 240 s bound, and reads it back: "the park did
    not complete, so the mount was asked to stop tracking instead, and it
    has stopped tracking". The mount ends not tracking, and no line says it
    parked.

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
        assert len(asked) == 1, (
            f"the park was asked again into a slew it could not read: "
            f"parks at {[round(t - first, 2) for t in asked]} s")
        unconfirmed = [m for m in said if "park is not confirmed" in m]
        assert len(unconfirmed) == 1, unconfirmed
        assert stops == [failed[0]] and failed[0] < UNREADABLE_S + POLL, (
            f"the stop of tracking came at {stops} s after the park; it "
            f"belongs at the poll that could not read the park state, "
            f"{failed[0]} s, once, and not at the {BOUND:g} s bound")
        reads = [round(t - first, 2) for t, _who, _i in e.n.reads
                 if t >= first]
        assert reads and reads[0] >= stops[0], (
            f"the stop was not read back: tracking reads at {reads} s")
        assert [m for m in said if m == STOPPED] == [STOPPED], (
            [m for m in said if "the park did not complete" in m])
        assert tel.rig.tracking is False
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
    (observed) -
        AssertionError: an unreadable first poll, with no slewing seen, no
        longer left the driver's park standing: lines [], stops of tracking
        at [0.0] s after the park
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
