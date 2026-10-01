"""WP-18 (#342): park cannot lose its command or interleave with a pulse.

Fix shape (plan 2026-09-30, backlog ruling: "Send `:Td#` every time before
`:hP#`, and make park and pulse share one lock"). S4 orchestrator ruling 9
(quick no-op detection, `test_zwo_am5_park_noop.py`) already shipped the
"re-send a lost `:hP#` within seconds" half. Its own docstring named the two
items still open, and this file covers both:

  1. `_send_park_and_wait`'s re-send used to send only the bare `:hP#` again,
     not `:Td#` first -- so a guide pulse's own `:Te#` (the east strategy's
     tracking resume, see `pulse_guide`) landing in that window could put
     tracking back on right before the one command `:hP#` is a documented
     silent no-op against (verified on hardware 2026-07-30).
  2. Nothing stopped a park and a pulse from running at the same time on the
     one serial link -- the SAME tracking race, reachable by a different
     timing, plus the chance of the two interleaving their commands outright.

All coordinates below are fictional (site privacy).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.devices.backends.zwo_am5 as am5

from test_zwo_am5 import FakeLink, FIXED_UTC, _connect_script  # rootdir-relative


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


async def _connected_tel(script):
    fl = FakeLink(script)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    return fl, tel


# ------------------------- item 1: :Td# before every :hP#, the re-send too

async def test_the_resent_hP_also_gets_its_own_Td_first(fixed_env, monkeypatch):
    """Tracking already reads off (GAT stays "0"), so `_tracking_off_verified`
    sends no `:Td#` of its own at all -- the only `:Td#` traffic in this test
    comes from the new unconditional send. The mount's position never
    changes, so ruling 9's motion check can never clear `watching`, and
    `PARK_NOOP_DETECT_S` is set to 0.0 so the re-send condition is true on the
    very first poll -- no reliance on a real-time margin (Windows' timer
    quantizes `asyncio.sleep`; a test that depended on a wall-clock window
    would be a coin toss, see the project's own clock-resolution note).

    EVERY `:hP#` on the wire -- the first send and the re-send alike -- must
    be the command directly after a `:Td#`, with nothing between them.

    RED under mutant "the re-send sends only :hP#" (the ``await
    self._stop_tracking_for_park()`` call removed from the loop's re-send
    branch in `_send_park_and_wait`, i.e. S4 ruling 9 exactly as it first
    shipped), observed:
        AssertionError: :hP# at index 9 is not directly preceded by :Td#, got
        ['Gps', 'GAT', 'Td', 'hP', 'GR', 'GD', 'Gps', 'GR', 'GD', 'hP', 'Gps']
        assert 'GD' == 'Td'
    """
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.05)
    monkeypatch.setattr(am5, "PARK_NOOP_DETECT_S", 0.0)
    monkeypatch.setattr(am5, "PARK_WAIT_S", 2.0)
    fl = FakeLink(_connect_script(
        Gps=lambda _c: "2" if fl.sent.count("hP") >= 2 else "0",
        GAT="0", Td="1", GR="10:00:00", GD="+40*00:00"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()

    await asyncio.wait_for(tel.park(), 5.0)

    assert fl.sent.count("hP") == 2, f"expected exactly one re-send: {fl.sent}"
    for i, cmd in enumerate(fl.sent):
        if cmd == "hP":
            assert fl.sent[i - 1] == "Td", (
                f":hP# at index {i} is not directly preceded by :Td#, got "
                f"{fl.sent}")


async def test_the_ordinary_path_still_gets_its_Td_before_its_one_hP(
        fixed_env):
    """Control: the ordinary path (tracking already off, so ruling 9's
    re-send never fires -- one `:hP#` and done) must still get a `:Td#`
    immediately before that one `:hP#`: the unconditional send is not limited
    to the retry branch.

    Gps is a CALLABLE, not a list: a plain list is consumed once by
    `connect()`'s own priming read of `:Gps#`, which would silently eat the
    "still unparked" entry a test meant for `park()` itself (the trap this
    test's first draft fell into)."""
    fl = FakeLink(_connect_script(
        Gps=lambda _c: "2" if "hP" in fl.sent else "0", GAT="0", Td="1"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()

    await tel.park()

    assert fl.sent.count("hP") == 1, fl.sent
    hp_i = fl.sent.index("hP")
    assert fl.sent[hp_i - 1] == "Td", f":hP# not preceded by :Td#: {fl.sent}"


# --------------------------- item 2: park and pulse share one lock

async def test_park_and_pulse_never_run_at_the_same_time(fixed_env,
                                                          monkeypatch):
    """A park and a pulse are both motion commands on the ONE serial link.
    Without a shared lock, a park requested while a pulse is mid-flight could
    land its own `:Td#`/`:hP#` between the pulse's start and stop -- the
    pulse's own tracking resume racing a concurrent park's tracking-off, or
    the two simply interleaving their commands. One lock makes them mutually
    exclusive: whichever got there first runs to completion before the
    other's first command goes out.

    The pulse is given 300 ms (`_PULSE_MAX_MS` is 1000, so it is not capped)
    -- comfortably longer than Windows' ~15.6 ms timer quantization, so the
    margin here is about the lock's OWN semantics (strict FIFO mutual
    exclusion, not a wall-clock race): the test only starts `park()` once the
    pulse's move command is confirmed on the wire, i.e. once `pulse_guide`
    already holds the lock, so there is no timing window for the two to
    interleave either way.

    RED under mutant "pulse takes its own lock" (`pulse_guide`'s ``async with
    self._pulse_park_lock:`` replaced by ``async with asyncio.Lock():`` -- a
    private, never-contended lock, i.e. no sharing with park at all),
    observed:
        AssertionError: park's own :Td# landed before the in-flight pulse's
        stop :Qn#, so the two ran concurrently on the one link: ['R1', 'Mn',
        'Gps', 'GAT', 'Td', 'hP', 'GR', 'GD', 'Gps', 'Qn']
        assert 9 < 4
    """
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.05)
    # Built inline, not through `_connected_tel`: the script's Gps callable
    # closes over `fl`, which must already be bound before `connect()`'s own
    # priming read of `:Gps#` can fire it.
    fl = FakeLink(_connect_script(Gps=lambda _c: "2" if "hP" in fl.sent else "0"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()

    pulse_task = asyncio.create_task(tel.pulse_guide("north", 300))
    for _ in range(400):                   # the move is genuinely on the wire
        if "Mn" in fl.sent:
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError(f"Mn never went out: {fl.sent}")

    park_task = asyncio.create_task(tel.park())
    await asyncio.gather(pulse_task, park_task)

    assert {"Mn", "Qn", "Td", "hP"} <= set(fl.sent), fl.sent
    assert fl.sent.index("Qn") < fl.sent.index("Td"), (
        f"park's own :Td# landed before the in-flight pulse's stop :Qn#, so "
        f"the two ran concurrently on the one link: {fl.sent}")
    assert fl.sent.index("Qn") < fl.sent.index("hP"), (
        f"park's own :hP# landed before the in-flight pulse's stop :Qn#, so "
        f"the two ran concurrently on the one link: {fl.sent}")


async def test_pulse_waits_for_an_in_flight_park(fixed_env, monkeypatch):
    """The mirror image: a pulse requested while a park is already running
    must wait for the park to finish, not land its commands inside it.

    Checking that the pulse's `:R1#` comes after `:hP#` would not discriminate
    (the test only fires the pulse after seeing `:hP#`, so that much is true
    by construction either way). What a MISSING lock actually lets through is
    the pulse's `:R1#` beating park's OWN NEXT poll: once `:hP#` is out, park
    still has one full ``PARK_POLL_S`` of waiting ahead of it (Gps reads "not
    parked" once more before "parked"), and a pulse started right then has no
    reason to wait for that sleep to end -- unless the lock makes it. 200 ms
    is comfortably above Windows' ~15.6 ms timer quantization, so this is a
    margin, not a coin toss.

    RED under mutant "park takes its own lock" (`_park_now`'s ``async with
    self._pulse_park_lock:`` replaced by ``async with asyncio.Lock():`` -- a
    private, never-contended lock, i.e. no sharing with pulse_guide at all),
    observed:
        AssertionError: the pulse's own :R1#/:Mn# landed before the in-flight
        park finished polling :Gps#, so the two ran concurrently on the one
        link: ['Gps', 'GAT', 'Td', 'hP', 'GR', 'GD', 'R1', 'Mn', 'Qn', 'Gps',
        'GR', 'GD', 'Gps']
        assert 6 > 12
    """
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.2)
    # One "not parked yet" poll after :hP# (Gps: connect's own prime read and
    # park()'s pre-check each consume one entry first), then parked -- so
    # there is exactly one PARK_POLL_S-long window, after :hP#, in which an
    # unlocked pulse has nothing stopping it from running right away.
    fl, tel = await _connected_tel(
        _connect_script(Gps=["0", "0", "0", "2"], GAT="0", Td="1"))

    park_task = asyncio.create_task(tel.park())
    for _ in range(400):
        if "hP" in fl.sent:
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError(f"hP never went out: {fl.sent}")

    pulse_task = asyncio.create_task(tel.pulse_guide("north", 50))
    await asyncio.gather(park_task, pulse_task)

    assert {"hP", "R1", "Mn", "Qn"} <= set(fl.sent), fl.sent
    last_gps = max(i for i, c in enumerate(fl.sent) if c == "Gps")
    assert fl.sent.index("R1") > last_gps, (
        f"the pulse's own :R1#/:Mn# landed before the in-flight park finished "
        f"polling :Gps#, so the two ran concurrently on the one link: "
        f"{fl.sent}")
