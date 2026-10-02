# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The wind-down stops the guider before it parks, reads the park back, and
parks once more at once (#311; S3 orchestrator ruling 3, spec "Still waiting
on the owner" item 24, which refines S2 orchestrator ruling 1, item 19).

Since #270 the wind-down started the guider stop on a task of its own,
yielded one loop turn and parked at once, on the premise that "the park moves
the mount whatever the guider is doing". On the AM5 that premise is false.
An east guide pulse is a tracking suspend (``:Td#`` ... ``:Te#``); a park
that begins inside one reads tracking off and sends no stop of its own, the
pulse's ``:Te#`` lands, and ``:hP#`` is then accepted and silently does
nothing (zwo_am5 ``_park_now``, verified on hardware 2026-07-30). That driver
notices only after its own ``PARK_WAIT_S`` poll: 60 s more before the roof
closes, in the rain that ended the run.

THE RULING. The wind-down asks the guider to stop and waits for the stop,
bounded by ``WIND_DOWN_GUIDER_STOP_S``: the longest pulse the native guider
can have in flight on any mount, plus a margin for the driver to end it. A
pulse in flight is over before the park begins, and a guider that has
stopped answering holds the park for that cap and no longer; the stop is
still reaped afterwards, bounded by ``GUIDE_OP_TIMEOUT_S``. Then the
wind-down parks, reads the park back, and parks once more at once if the
mount does not report parked. The roof close still waits only for the park.

THE HARNESS is test_unsafe_ending_parks_at_once's `_Ending`: a clocked
simulator night that scripted rain ends on a SafetyAbort, its wind-down on a
task of its own that the harness clocks. The idle stop is decided and made
long before the rain here, so nothing is handed to the park. THE SCRIPTED
PULSE: the wind-down's own guider stop comes back only once the pulse it cut
is over, ``PULSE_S`` of fake time later (the AM5's longest pulse), and a park
asked while that pulse is on the wire is lost, the #311 shape: park()
returns and the mount is not parked. The site is a fixture, never the real
one.

WHAT THE ENGINE CANNOT REACH. On the AM5 itself park() does not return after
a lost ``:hP#``: the driver polls for ``PARK_WAIT_S`` and retries on its own,
so a lost park there costs that minute before the engine can read anything
back. The read-back retry here is at once for a driver whose park() returns
with the mount neither parked nor slewing (this simulator's lost park); the
AM5's own minute is #342. An asynchronous Alpaca park, whose park() returns
while the mount is still slewing to park, is waited for rather than asked
again (item 9, test_park_readback_async_park.py). What closes #311 on the AM5
for the native guider is the wait, which keeps the pulse and the park apart.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import DeviceError, DomeShutterState
from astrodeck.sequence.engine import SequenceEngine

from test_idle_park_hold import sim_hub, temp_store  # noqa: F401
from test_park_readback_async_park import _clock_the_park
from test_unsafe_ending_parks_at_once import AT_ONCE, GUIDE_S, POLL, _Ending

#: The cap under test.
CAP = engine_mod.WIND_DOWN_GUIDER_STOP_S


def _am5_longest_pulse_s() -> float:
    """The AM5 driver's own ceiling on one pulse, in seconds (read from the
    driver, not copied: `ZwoAm5Telescope.max_pulse_ms`)."""
    from astrodeck.devices.backends.zwo_am5 import ZwoAm5Telescope
    return ZwoAm5Telescope.max_pulse_ms / 1000.0


#: The scripted pulse: the longest one the rig's mount will perform.
PULSE_S = _am5_longest_pulse_s()


def _script(e: _Ending, monkeypatch, *, pulse_s: float | None = None,
            hang: bool = False, lose_first_park: bool = False) -> dict:
    """Script the wind-down's own guider stop and the parks after it.

    ``pulse_s``: the stop cuts a guide pulse that is still on the wire and
    comes back once that pulse is over, ``pulse_s`` of fake time later; a park
    asked meanwhile is lost (returns, mount not parked). ``hang``: the stop
    does not answer, its caller waits out ``GUIDE_OP_TIMEOUT_S`` on the fake
    clock and it times out, as ``asyncio.wait_for`` has it; no pulse is in
    flight. ``lose_first_park``: the first park asked is lost whatever the
    guider is doing (a pulse sent through PHD2's own mount connection, which
    nothing here orders against the park, #311). Only the wind-down's guider
    stop is scripted: the idle stop's, earlier in the night, is the real one.

    Every park asked is recorded in ``e.events`` as "park asked", a lost one
    also as "park lost"; `_Ending`'s own "park" is the parks that reached the
    mount."""
    run = e.run
    guider = run.hub.guider
    assert guider is not None and guider.connected, (
        "premise: the sim rig has a connected guider")
    rec: dict = {"asked": [], "over": [], "pulse": False, "lost": 0}
    real_stop = guider.stop_guiding

    async def stop_guiding(*a, **kw):
        if run.frozen.is_set() or not e.winding or run.who() == "retry":
            return await real_stop(*a, **kw)
        rec["asked"].append(run.clock.t)
        e.events.append(("guider stop", run.clock.t))
        try:
            if hang:
                await run._park(GUIDE_S)
                raise asyncio.TimeoutError()
            if pulse_s:
                rec["pulse"] = True
                await run._park(pulse_s)
        finally:
            rec["pulse"] = False
            rec["over"].append(run.clock.t)
            e.events.append(("guider over", run.clock.t))
        return await real_stop(*a, **kw)

    monkeypatch.setattr(guider, "stop_guiding", stop_guiding)
    tel = run.hub.devices["telescope"]
    inner = tel.park                        # `_Ending`'s record, then the sim

    async def park():
        e.events.append(("park asked", run.clock.t))
        if rec["pulse"] or (lose_first_park and rec["lost"] == 0):
            rec["lost"] += 1
            e.events.append(("park lost", run.clock.t))
            return
        return await inner()

    monkeypatch.setattr(tel, "park", park)
    return rec


def _order(e: _Ending, *names: str) -> list[str]:
    return [w for w, _t in e.events if w in names]


# ------------------------------------------- the stop comes before the park

async def test_the_park_waits_for_the_pulse_the_guider_stop_ends(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The rain ends the run while a guide pulse is on the wire: the
    wind-down's guider stop cuts it and comes back when it is over, one
    AM5-longest pulse later. The park must be asked after the stop came back,
    not inside the pulse, so it is not lost: one park, and the mount parked.

    Mutant "no await" (the bounded wait before the park deleted, the #270
    code's one loop turn put back: the park asked straight after the stop):
    RED (observed) -
        AssertionError: the park was asked inside the guide pulse the
        wind-down's stop was ending: parks lost at [0.0, 0.0] s after the
        stop was asked, the pulse over at 1 s; order ['guider stop', 'park
        asked', 'park asked', 'guider over']
    (the read-back's retry is lost too: it is asked at the same fake
    instant, with the pulse still in flight.)
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    rec = _script(e, monkeypatch, pulse_s=PULSE_S)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert rec["asked"] and rec["over"], (
            f"premise: the wind-down asked the guider to stop: {e.events}")
        asked, over = rec["asked"][0], rec["over"][0]
        assert round(over - asked, 6) == PULSE_S, (
            f"premise: the pulse was on the wire for {over - asked} s")
        lost = [round(t - asked, 2) for w, t in e.events if w == "park lost"]
        order = _order(e, "guider stop", "guider over", "park asked")
        assert lost == [] and order[:3] == [
                "guider stop", "guider over", "park asked"], (
            f"the park was asked inside the guide pulse the wind-down's stop "
            f"was ending: parks lost at {lost} s after the stop was asked, "
            f"the pulse over at {over - asked:g} s; order {order}")
        asks = e.at("park asked")
        assert asks[0] - over <= POLL, (
            f"the park waited {asks[0] - over:.2f} s past the guider stop's "
            f"return")
        assert len(asks) == 1, (
            f"a park that read back parked was asked again: {asks}")
        assert e.run.hub.devices["telescope"].rig.parked
    finally:
        await e.run.close()


async def test_a_guider_that_does_not_answer_holds_the_park_for_the_cap(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The wind-down's guider stop does not answer at all. The park goes
    ahead exactly ``WIND_DOWN_GUIDER_STOP_S`` after the stop was asked, on the
    fake clock, and says once that the guider had not stopped; the stop is
    still reaped after the park, by its own ``GUIDE_OP_TIMEOUT_S``.

    Mutant "unbounded await" (the wait's deadline removed: it polls the stop
    until it is done, however long): RED (observed) -
        AssertionError: a guider stop that did not answer held the park
        120.000 s after it was asked; the cap is 4.5 s
    Its other spelling, the guider stop's task awaited outright: RED
    (observed), as a hang the harness bounds in real time -
        AssertionError: premise: the run ended within 60.0 s
    (a wind-down awaiting a task is not parked on the fake clock, so the
    clock never reaches the stop's own timeout and the night never ends.)
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    rec = _script(e, monkeypatch, hang=True)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert rec["asked"], f"premise: the guider stop was asked: {e.events}"
        asked = rec["asked"][0]
        asks = e.at("park asked")
        assert asks, f"premise: a park was asked: {e.events}"
        took = asks[0] - asked
        assert abs(took - CAP) < 1e-6, (
            f"a guider stop that did not answer held the park {took:.3f} s "
            f"after it was asked; the cap is {CAP:g} s")
        said = [m for _lvl, m, _s in bus_lines if "had not stopped" in m]
        assert len(said) == 1, said
        over, wound = rec["over"], e.at("wound")
        assert over and round(over[0] - asked, 6) == GUIDE_S, (
            f"premise: the stop timed out at its own bound: {over}")
        assert wound and over[0] <= wound[0] <= asked + GUIDE_S + 2 * POLL, (
            f"the stop was not reaped after the park, within its bound: "
            f"stop over at {over}, wind-down back at {wound}")
        assert e.run.hub.devices["telescope"].rig.parked
    finally:
        await e.run.close()


async def test_a_cap_off_the_poll_grid_is_still_kept_exactly(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The wait polls every ``IDLE_STOP_FINISH_POLL_S``, and the real cap is
    a whole number of those polls (4.5 s of 0.25 s), so the case above
    cannot tell a wait that cuts its last sleep to the bound from one that
    sleeps whole polls and overshoots it. Moved half a poll off that grid, the
    cap must still be kept exactly: a guider that does not answer holds the
    park for the cap, not until the next poll after it. (A margin or a pulse
    cap changed later moves the real cap off the grid the same way.)

    Mutant "no cut" (the wait's ``asyncio.sleep(min(step, left))`` made
    ``asyncio.sleep(step)``): RED (observed, the verifier's round) -
        AssertionError: a guider stop that did not answer held the park
        4.750 s after it was asked; the cap, off the poll grid, is 4.625 s
    """
    cap = CAP + POLL / 2
    assert (cap / POLL) % 1, f"premise: {cap:g} s is off the {POLL:g} s grid"
    monkeypatch.setattr(engine_mod, "WIND_DOWN_GUIDER_STOP_S", cap)
    e = _Ending(sim_hub, temp_store, monkeypatch)
    rec = _script(e, monkeypatch, hang=True)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert rec["asked"], f"premise: the guider stop was asked: {e.events}"
        asks = e.at("park asked")
        assert asks, f"premise: a park was asked: {e.events}"
        took = asks[0] - rec["asked"][0]
        assert abs(took - cap) < 1e-6, (
            f"a guider stop that did not answer held the park {took:.3f} s "
            f"after it was asked; the cap, off the poll grid, is {cap:g} s")
        assert e.run.hub.devices["telescope"].rig.parked
    finally:
        await e.run.close()


# ------------------------------------------------ the park is read back

async def test_a_park_that_reads_back_unparked_is_asked_again_at_once(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The first park is lost (park() returns, the mount is neither parked
    nor slewing). The read-back says so, and the park is asked once more at
    the same fake instant, not on the read-back's next poll, a tick or a
    minute later: the mount ends parked, "mount parked" is said once, and no
    fallback stop follows.

    RE-PINNED FOR ITEM 9 (the follow-up to S3 orchestrator ruling 3): the
    read-back now waits for a park that is on its way, polling every
    ``PARK_READ_BACK_POLL_S`` (test_park_readback_async_park.py), and this is
    its control: a mount that reads neither parked nor slewing is still
    parked again at once. The park runs on a task of its own (#305), which
    this case now hands to the harness's clock (`_clock_the_park`): unclocked,
    a poll there would sleep in real time with the fake clock standing still,
    and "the same fake instant" would hold of a read-back that polled first.
    So "at once" is pinned to the instant, where it was within ``AT_ONCE``
    (1 s), which a one-second poll would have met.

    Mutant "no read-back retry" (the second attempt deleted: one park, taken
    at its word): RED (observed) -
        AssertionError: the park read back unparked and was not asked again:
        parks asked at [0.0] s after the stop was asked; the mount parked:
        False
    Mutant "a poll before a lost park is judged" (item 9's read-back sleeps
    one ``PARK_READ_BACK_POLL_S`` before it reads the slewing state): RED
    (observed) -
        AssertionError: the park was asked again 1.00 s after the first, not
        at the same fake instant
    """
    e = _Ending(sim_hub, temp_store, monkeypatch)
    _clock_the_park(e, monkeypatch)
    rec = _script(e, monkeypatch, lose_first_park=True)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert rec["asked"], f"premise: the guider stop was asked: {e.events}"
        asked = rec["asked"][0]
        asks = e.at("park asked")
        rig = e.run.hub.devices["telescope"].rig
        assert rec["lost"] == 1, f"premise: the first park was lost: {e.events}"
        assert len(asks) == 2, (
            f"the park read back unparked and was not asked again: parks "
            f"asked at {[round(t - asked, 2) for t in asks]} s after the stop "
            f"was asked; the mount parked: {rig.parked}")
        gap = asks[1] - asks[0]
        assert gap == 0.0, (
            f"the park was asked again {gap:.2f} s after the first, not at "
            f"the same fake instant")
        assert rig.parked, "the second park did not park the mount"
        again = [m for _l, m, _s in bus_lines if "parking once more" in m]
        parked = [m for _l, m, _s in bus_lines if m.startswith("mount parked")]
        assert len(again) == 1 and len(parked) == 1, (again, parked)
        fallback = [m for _l, m, _s in bus_lines
                    if "the park did not complete" in m]
        assert fallback == [], fallback
    finally:
        await e.run.close()


# ---------------------------------------------- the roof after the park

async def test_the_roof_closes_after_the_park_and_waits_on_nothing_else(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The roof closes on unsafe. The guider stop does not answer, and the
    first park is lost. The close must come after the park that parked (the
    read-back's), at once after it, and before the guider stop is reaped:
    the roof close waits only for the park. The roof then closes over the
    parked mount (`close_observatory` refuses otherwise).

    Mutant "retry after the close" (the read-back and its second park moved
    after the roof close, as the failed park's stop is): RED (observed) -
        AssertionError: the roof close was asked before the park that
        parked: order ['park asked', 'close', 'park asked']
    Mutant "close waits for the reap" (the guider stop's reap moved ahead of
    the roof close): RED (observed) -
        AssertionError: the roof close waited for the guider stop's reap: it
        was asked at 120.0 s after the stop, the park at 4.5 s
    """
    dome = sim_hub.devices.get("dome")
    assert dome is not None and dome.connected, "premise: the sim has a roof"
    e = _Ending(sim_hub, temp_store, monkeypatch, roof=True)
    rec = _script(e, monkeypatch, hang=True, lose_first_park=True)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert rec["asked"] and rec["lost"] == 1, (
            f"premise: the stop was asked and the first park lost: "
            f"{e.events}")
        asked = rec["asked"][0]
        order = _order(e, "park asked", "close")
        assert order[:3] == ["park asked", "park asked", "close"], (
            f"the roof close was asked before the park that parked: order "
            f"{order}")
        park, close = e.at("park")[0], e.at("close")[0]
        assert close - park <= AT_ONCE and close - asked <= CAP + AT_ONCE, (
            f"the roof close waited for the guider stop's reap: it was asked "
            f"at {close - asked:.1f} s after the stop, the park at "
            f"{park - asked:.1f} s")
        over = rec["over"]
        assert over and close < over[0], (
            f"premise: the guider stop was still out at the close: {over}")
        assert await dome.shutter_state() is DomeShutterState.CLOSED, (
            "the roof did not close over the parked mount")
    finally:
        await e.run.close()


# ------------------------------------------------ what cannot be read

async def test_a_read_back_that_cannot_answer_does_not_repeat_the_park(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. The park returns, and the read-back cannot answer (the mount's
    park state raises). That is no evidence the park was lost, so the park is
    not asked again: one park, "mount parked" said with the read-back's
    failure named, and no fallback stop. A driver's own park is the claim, as
    before this change; the roof close still refuses over a mount it cannot
    confirm parked (`close_observatory`).

    Mutant "an unreadable read-back parks again" (the retry asked on anything
    but a confirmed park): RED (observed) -
        AssertionError: a read-back that could not answer asked the park
        again: 2 parks
    """
    tel = sim_hub.devices["telescope"]
    parks: list[int] = []
    real_park = tel.park

    async def park():
        parks.append(1)
        await real_park()

    async def is_parked():
        raise DeviceError("no reply to the park-state query")

    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "is_parked", is_parked)
    await SequenceEngine(sim_hub)._wind_down(park=True, warm=False)
    assert len(parks) == 1, (
        f"a read-back that could not answer asked the park again: "
        f"{len(parks)} parks")
    said = [m for _l, m, _s in bus_lines if m.startswith("mount parked")]
    assert len(said) == 1 and "could not be read back" in said[0], said
    assert not [m for _l, m, _s in bus_lines
                if "the park did not complete" in m]
    assert tel.rig.parked, "premise: the sim parked"


@pytest.mark.parametrize("park_fails", ["fails", "times out"])
async def test_a_park_that_raised_is_not_asked_again(
        sim_hub, temp_store, monkeypatch, bus_lines, park_fails):
    """CONTROL. The second park is for a park() that RETURNED and left the
    mount unparked. A park that raised, or timed out, has had its driver's
    own retries and its whole ``PARK_TIMEOUT_S`` (the AM5 raises only after
    two ``PARK_WAIT_S`` polls), and asking again would hold the roof close
    for another round of them in the weather that ended the run. So: one
    park, and then the failed park's stop of tracking (#270), said once.

    Mutant "a raised park is asked again" (the ``except Exception`` arm of
    `_park_and_read_back` goes on to the second attempt): RED on "fails"
    (observed, the verifier's round) -
        AssertionError: a park that raised (fails) was asked again: 2 parks
    Mutant "a timed-out park is asked again" (the same, in its ``except
    asyncio.TimeoutError`` arm): RED on "times out" (observed) -
        AssertionError: a park that raised (times out) was asked again: 2
        parks
    """
    e = _Ending(sim_hub, temp_store, monkeypatch, park_fails=park_fails)
    e.engine.start(e.n.plan)
    try:
        await e.to_the_end()
        assert e.engine.state.get("end_reason") == "unsafe", (
            f"premise: the rain ended it: {e.engine.state}")
        assert e.parks, f"premise: a park was asked: {e.events}"
        assert len(e.parks) == 1, (
            f"a park that raised ({park_fails}) was asked again: "
            f"{len(e.parks)} parks")
        again = [m for _l, m, _s in bus_lines if "parking once more" in m]
        assert again == [], again
        said = [m for _l, m, _s in bus_lines
                if "the park did not complete" in m]
        assert len(said) == 1, (
            f"premise: the failed park's stop followed: {said}")
        assert not e.run.hub.devices["telescope"].rig.parked, (
            "premise: the park never happened")
    finally:
        await e.run.close()


# ----------------------------------------------------------- the cap itself

def test_the_cap_outlasts_the_longest_pulse_and_its_end_and_stays_short():
    """``WIND_DOWN_GUIDER_STOP_S`` is derived, not guessed: it must be longer
    than the longest pulse any guider here can have in flight (the native
    guider's own ceiling on one correction, which it lowers to a mount's
    published ``max_pulse_ms`` and never raises; the AM5's ``max_pulse_ms``)
    plus what the AM5 driver waits, after a stop cuts a pulse short, for its
    pulse thread to stop the mount (``_PULSE_CANCEL_JOIN_S``). And it must
    stay short: the point of the ruling is that a guider that has stopped
    answering does not hold the park for the guider's own
    ``GUIDE_OP_TIMEOUT_S``. Every number is read from its source, so raising
    the AM5's pulse cap, the native guider's, or the AM5's join turns this
    red.

    Mutant "a guessed cap" (``WIND_DOWN_GUIDER_STOP_S = 1.0``): RED (observed) -
        AssertionError: the guider-stop cap 1 s is not longer than the longest
        pulse (2.5 s) plus the driver's end of it (2 s)
    Mutant "no margin" (``WIND_DOWN_PULSE_MARGIN_S = 0.0``): RED (observed) -
        AssertionError: the guider-stop cap 2.5 s is not longer than the
        longest pulse (2.5 s) plus the driver's end of it (2 s)
    Mutant "the AM5's cap alone" (the native cap replaced by the AM5's 1000
    ms in the derivation): RED (observed) -
        AssertionError: the guider-stop cap 3 s is not longer than the longest
        pulse (2.5 s) plus the driver's end of it (2 s)
    Mutant "the guider's whole bound" (``WIND_DOWN_GUIDER_STOP_S =
    GUIDE_OP_TIMEOUT_S``): RED (observed) -
        AssertionError: the guider-stop cap 120 s is not short: a hung guider
        would hold the park for it (the guider's own bound is 120 s)
    """
    from astrodeck.devices.backends import zwo_am5
    from astrodeck.guide import native
    longest = max(zwo_am5.ZwoAm5Telescope.max_pulse_ms,
                  native._ENGINE_MAX_DURATION_MS) / 1000.0
    join = zwo_am5._PULSE_CANCEL_JOIN_S
    assert CAP >= longest + join, (
        f"the guider-stop cap {CAP:g} s is not longer than the longest pulse "
        f"({longest:g} s) plus the driver's end of it ({join:g} s)")
    assert CAP <= GUIDE_S / 10.0, (
        f"the guider-stop cap {CAP:g} s is not short: a hung guider would "
        f"hold the park for it (the guider's own bound is {GUIDE_S:g} s)")

