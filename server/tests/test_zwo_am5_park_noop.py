# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#342: the AM5 driver notices a silent ``:hP#`` no-op within seconds.

WHAT WAS WRONG. The AM5 takes ``:hP#`` fire-and-forget: no ack, and a park
the mount throws away looks exactly like one it took. ``_send_park_and_wait``
then polled the parked flag for the whole ``PARK_WAIT_S`` (60 s) before the
driver's own retry sent it again, so a lost first park cost a minute before
the roof closed, in the rain -- and because ``park()`` does not return until
then, the engine's "park again at once" (S3 orchestrator ruling 3,
``_park_and_read_back``) could never reach this mount.

S4 orchestrator ruling 9: after ``:hP#`` the driver watches for motion and for
the parked flag. A mount that is neither moving nor parked within
``PARK_NOOP_DETECT_S`` is sent ``:hP#`` once more, and the attempt then runs
to its end as before. The second attempt and the ``DeviceError`` after both
are unchanged.

"MOVING" IS THE POSITION CHANGING, measured from the read taken as the
command goes out, by the ``SETTLE_DEG`` criterion the settle poll already
uses. It is not ``is_slewing()``: that flag is set only by ``slew()`` around a
goto, never by ``:hP#``, so a detector reading it would call every real park a
no-op and re-send into a moving mount. Nor is it ``:GU#``, whose slewing bits
the protocol doc records as unverified against live states.

ALL ON FAKE TIME. ``am5.asyncio`` is replaced by a view of the real module
whose ``sleep`` and loop ``time`` read one fake clock, so a 60 s attempt costs
nothing and every timestamp below is exact. The mount double moves on that
clock too: with its drive off, its RA advances at the sidereal rate, which is
the drift a mount standing still really shows and the detector must not
mistake for a slew. All coordinates are fictional (site privacy).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.devices import lx200
from astrodeck.devices.base import DeviceError

from test_zwo_am5 import FakeLink, FIXED_UTC, _connect_script  # rootdir-relative


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


#: RA hours per second of time that a mount standing still with its drive off
#: sees go past: one sidereal day is 86164.09 s.
SIDEREAL_H_PER_S = 24.0 / 86164.0905

#: What one ``:hP#`` does in the double.
IGNORED = "ignored"   # the silent no-op: accepted, nothing moves
SLEWS = "slews"       # drives to the park position for ``slew_s``, then parked
PARKS = "parks"       # reports parked at once, without moving (already home)

#: How far a park that turns only the RA axis carries the RA read, in hours:
#: a mount at the pole with its RA axis 60 degrees from home, where the Dec
#: read cannot change because it already says +90.
RA_SLEW_H = -4.0


# ------------------------------------------------------------- the fake clock

class _Clock:
    """Fake time, advanced only by the driver's own sleeps."""

    def __init__(self) -> None:
        self.t = 0.0


class _LoopOnClock:
    """The running loop, except that ``time()`` reads the fake clock."""

    def __init__(self, loop, clock: _Clock) -> None:
        self._loop, self._clock = loop, clock

    def time(self) -> float:
        return self._clock.t

    def __getattr__(self, name):
        return getattr(self._loop, name)


class _AsyncioOnClock:
    """The driver module's ``asyncio``: ``sleep`` and the loop's ``time`` on
    the fake clock, everything else the real module. Patched onto
    ``am5.asyncio`` only, so pytest-asyncio's own loop never sees it."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, result=None):
        self._clock.t += max(0.0, float(delay))
        await asyncio.sleep(0)          # still a real yield to the loop
        return result

    def get_running_loop(self):
        return _LoopOnClock(asyncio.get_running_loop(), self._clock)


# ----------------------------------------------------------- the mount double

class _ParkingAm5(FakeLink):
    """An unparked AM5 with its drive off, on the fake clock.

    ``answers[i]`` is what the i-th ``:hP#`` does (IGNORED, SLEWS or PARKS);
    past the end of the list every ``:hP#`` is ignored. ``hp_at`` records the
    fake time of every ``:hP#``. With ``readable=False`` the position reads go
    unanswered, the way a silent mount's do (``FakeLink`` raises LinkError for
    an unscripted read); with ``gr_misses=n`` only the first n RA reads after
    the first ``:hP#`` do. A slew moves the Dec read toward +90 by default;
    with ``slew_axis="ra"`` it turns the RA axis alone, by ``RA_SLEW_H``."""

    def __init__(self, clock: _Clock, answers, *, ra_h: float = 10.0,
                 dec: float = 40.0, slew_s: float = 15.0,
                 slew_axis: str = "dec", readable: bool = True,
                 gr_misses: int = 0) -> None:
        self.clock = clock
        self.answers = list(answers)
        self.ra0, self.dec0, self.slew_s = ra_h, dec, slew_s
        self.slew_axis, self.gr_misses = slew_axis, gr_misses
        self.hp_at: list[float] = []
        self.slew_from: float | None = None
        self.parked_at: float | None = None
        script = _connect_script(Gps=self._gps, GAT="0", Td="1",
                                 GR=self._gr, GD=self._gd)
        if not readable:
            del script["GR"], script["GD"]
        super().__init__(script)

    def _exchange(self, cmd: str, reply: str):
        out = super()._exchange(cmd, reply)
        if cmd == "hP":
            n = len(self.hp_at)
            self.hp_at.append(self.clock.t)
            answer = self.answers[n] if n < len(self.answers) else IGNORED
            if answer == SLEWS and self.slew_from is None:
                self.slew_from = self.clock.t
                self.parked_at = self.clock.t + self.slew_s
            elif answer == PARKS and self.parked_at is None:
                self.parked_at = self.clock.t
        return out

    def _gps(self, _cmd: str) -> str:
        parked = self.parked_at is not None and self.clock.t >= self.parked_at
        return "2" if parked else "0"

    def _slew_frac(self) -> float:
        if self.slew_from is None:
            return 0.0
        return min(1.0, (self.clock.t - self.slew_from) / self.slew_s)

    def _gr(self, _cmd: str) -> str | None:
        if self.hp_at and self.gr_misses > 0:
            self.gr_misses -= 1
            return None                 # unanswered: FakeLink raises LinkError
        ra = self.ra0 + self.clock.t * SIDEREAL_H_PER_S
        if self.slew_axis == "ra":
            ra += RA_SLEW_H * self._slew_frac()
        return lx200.format_ra(ra)

    def _gd(self, _cmd: str) -> str:
        if self.slew_axis == "ra":
            return lx200.format_dec(self.dec0)
        return lx200.format_dec(
            self.dec0 + (90.0 - self.dec0) * self._slew_frac())


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(am5, "asyncio", _AsyncioOnClock(c))
    return c


async def _mount(clock: _Clock, answers, **kw):
    fl = _ParkingAm5(clock, answers, **kw)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    return fl, tel


# ------------------------------------------------------------------ the rule

def test_the_detect_window_is_seconds_and_under_the_sidereal_ceiling():
    """``PARK_NOOP_DETECT_S`` is a few seconds, far shorter than one
    attempt, and short enough that a mount standing still cannot drift past
    ``SETTLE_DEG`` inside it.

    The last is computed, not assumed: with its drive off an AM5 standing
    still sees its RA advance at the sidereal rate, 0.00418 degrees per
    second. The stillness read is taken at the first poll at or after the
    window, so up to ``PARK_NOOP_DETECT_S + PARK_POLL_S`` after the reference;
    over that span the drift must stay under ``SETTLE_DEG`` or a lost park
    reads as motion and is never re-sent (6 s is 0.025 degrees; the window
    would have to pass about 11 s to reach 0.05).

    RED under mutant "a detect window as long as the attempt"
    (``PARK_NOOP_DETECT_S = PARK_WAIT_S``), observed:
        AssertionError: PARK_NOOP_DETECT_S is 60.0 s against PARK_WAIT_S
        60.0 s; ruling 9 wants seconds, far shorter than one attempt
        assert (60.0 <= 10.0)
    The value is pinned HERE because the lost-park test reads its bound off
    the same constant: under this mutant that bound stretched to 61 s, and
    the second attempt's :hP# at 61 s fitted inside it. The lost-park test
    went red on its log line alone, because 60 s of sidereal drift read as
    motion and the re-send never went out.

    RED under mutant "a settle criterion tighter than the sidereal drift"
    (``SETTLE_DEG = 0.02``), observed:
        AssertionError: a still mount drifts 0.025 deg over the window, past
        SETTLE_DEG 0.02: a lost park would read as a slew
        assert 0.025068447742740346 < 0.02
    """
    detect, wait, poll = am5.PARK_NOOP_DETECT_S, am5.PARK_WAIT_S, am5.PARK_POLL_S
    assert 1.0 <= detect <= 10.0 and detect * 10 <= wait, (
        f"PARK_NOOP_DETECT_S is {detect} s against PARK_WAIT_S {wait} s; "
        f"ruling 9 wants seconds, far shorter than one attempt")
    drift_deg = (detect + poll) * SIDEREAL_H_PER_S * 15.0
    assert drift_deg < am5.SETTLE_DEG, (
        f"a still mount drifts {drift_deg:.3f} deg over the window, past "
        f"SETTLE_DEG {am5.SETTLE_DEG}: a lost park would read as a slew")


# ----------------------------------------------------------- the lost park

@pytest.mark.parametrize("ra_h", [10.0, 24.0 - 2 / 3600.0],
                         ids=["mid-sky", "across 0h"])
async def test_a_silently_ignored_park_is_sent_again_within_seconds(
        fixed_env, clock, bus_lines, ra_h):
    """The first ``:hP#`` is accepted and nothing moves; the second one
    drives the mount home in 15 s. The re-send goes out at
    ``PARK_NOOP_DETECT_S`` (5 s), no later than one poll past it and no
    earlier, the park completes 20 s in, and the log says why the command
    went twice.

    "across 0h" starts the still mount at 23:59:58, so its sidereal drift
    carries RA through 24h to 00:00:03 inside the window. That is 5 s of RA,
    0.02 degrees; read without the wrap it is 360 degrees of "motion".

    RED under mutant "wait out PARK_WAIT_S before re-sending" (the code
    before this change: ``_send_park_and_wait`` polls only the parked flag),
    observed in both cases:
        AssertionError: the lost park was sent again 61.0 s after the first
        :hP#; ruling 9 wants it within PARK_NOOP_DETECT_S + one poll (6 s)
        assert (61.0 - 0.0) <= 6.0

    RED under mutant "no RA wrap" (the displacement taken as
    ``abs(ra - ref_ra) * 15``), observed in "across 0h" only:
        AssertionError: the lost park was sent again 61.0 s after the first
        :hP#; ruling 9 wants it within PARK_NOOP_DETECT_S + one poll (6 s)
        assert (61.0 - 0.0) <= 6.0

    RED under mutant "re-send in silence" (the ``bus.log`` before the
    re-send removed), observed in both cases:
        AssertionError: the re-send must say why the command went twice: []
        assert 0 == 1

    RED under mutant "give up at the re-send" (``return False`` straight
    after the re-send, so the second attempt follows at once), observed in
    both cases:
        AssertionError: re-sent once, not 2: [0.0, 5.0, 5.0]

    RED under mutant "send :hP# twice up front", observed in both cases:
        AssertionError: re-sent 0.0 s in, before the mount had
        PARK_NOOP_DETECT_S to start moving
    """
    fl, tel = await _mount(clock, [IGNORED, SLEWS], ra_h=ra_h)

    await tel.park()

    assert len(fl.hp_at) >= 2, f"the lost park was never sent again: {fl.sent}"
    first, again = fl.hp_at[0], fl.hp_at[1]
    within = am5.PARK_NOOP_DETECT_S + am5.PARK_POLL_S
    assert again - first <= within, (
        f"the lost park was sent again {again - first:.1f} s after the first "
        f":hP#; ruling 9 wants it within PARK_NOOP_DETECT_S + one poll "
        f"({within:g} s)")
    assert again - first >= am5.PARK_NOOP_DETECT_S, (
        f"re-sent {again - first:.1f} s in, before the mount had "
        f"PARK_NOOP_DETECT_S to start moving")
    assert len(fl.hp_at) == 2, f"re-sent once, not {len(fl.hp_at) - 1}: {fl.hp_at}"
    assert await tel.is_parked() is True
    assert clock.t == pytest.approx(again + fl.slew_s), (
        f"the park should land when the re-sent slew does, not at {clock.t}")
    said = [m for lvl, m, src in bus_lines
            if lvl == "warning" and src == "mount" and "once more" in m]
    assert len(said) == 1, f"the re-send must say why the command went twice: {said}"


async def test_a_reference_read_that_went_unanswered_is_taken_again(
        fixed_env, clock):
    """The position read taken as the first ``:hP#`` goes out is unanswered;
    every read after it answers. That one miss must not switch the detector
    off: the first read that does answer becomes the reference, and a mount
    still standing there at ``PARK_NOOP_DETECT_S`` gets its command again.

    The harness is checked to have reached the branch: the one miss it was
    given is spent, and the first RA read after the ``:hP#`` is the
    reference read, since the poll sleeps before it reads anything else.

    RED under mutant "a first unanswered read never gets a reference" (the
    ``ref = now`` branch replaced by ``continue``, so a reference that failed
    stays missing), observed:
        AssertionError: with its reference read unanswered, the lost park was
        sent again 61.0 s in; the next read that answers must stand in for it
        assert 61.0 <= (5.0 + 1.0)
    Before this test was added that mutant passed every test in the file.
    Under "wait out PARK_WAIT_S before re-sending" it is red on the harness
    check instead ("the reference read was never taken, so never missed"),
    since the code before ruling 9 takes no reference read at all.
    """
    fl, tel = await _mount(clock, [IGNORED, SLEWS], gr_misses=1)

    await tel.park()

    assert fl.gr_misses == 0, "the reference read was never taken, so never missed"
    assert fl.hp_at[:1] == [0.0] and len(fl.hp_at) >= 2, (
        f"the lost park was never sent again: {fl.hp_at}")
    again = fl.hp_at[1]
    assert am5.PARK_NOOP_DETECT_S <= again <= (
        am5.PARK_NOOP_DETECT_S + am5.PARK_POLL_S), (
        f"with its reference read unanswered, the lost park was sent again "
        f"{again:.1f} s in; the next read that answers must stand in for it")
    assert len(fl.hp_at) == 2, f"re-sent once, not {len(fl.hp_at) - 1}: {fl.hp_at}"
    assert clock.t == pytest.approx(again + fl.slew_s)


async def test_a_mount_that_never_moves_still_fails_in_the_documented_time(
        fixed_env, clock):
    """Every ``:hP#`` is thrown away. Each of the two attempts sends its
    command, re-sends it once at ``PARK_NOOP_DETECT_S`` and then waits out
    the rest of its ``PARK_WAIT_S``; the ``DeviceError`` after both is the
    one it always was. With tracking reading off, the whole park takes
    between ``2 * PARK_WAIT_S`` and ``2 * (PARK_WAIT_S + PARK_POLL_S)``
    (122 s here): the poll may overrun each deadline by one poll, and the
    tracking-off check between the attempts costs nothing when tracking
    already reads off (up to 2 s per check when it does not).

    RED under mutant "the re-send restarts the attempt" (the deadline reset to
    ``now + timeout_s`` after the re-send), observed:
        AssertionError: the park gave up after 132.0 s; the documented bound
        is 122 s (two attempts of PARK_WAIT_S, each overrun by at most one
        poll)
        assert 132.0 <= 122.0

    RED under mutant "give up at the re-send" (``return False`` after the
    re-send), observed:
        AssertionError: the park gave up after 10.0 s; each attempt must
        still wait out its PARK_WAIT_S (120 s for both)
        assert 10.0 >= (2 * 60.0)

    RED under mutant "wait out PARK_WAIT_S before re-sending" (the code
    before this change), observed:
        AssertionError: expected 2 attempts x (send + one re-send): [0.0, 61.0]
        assert 2 == 4
    """
    fl, tel = await _mount(clock, [])

    with pytest.raises(DeviceError) as e:
        await tel.park()

    msg = str(e.value)
    assert "two attempts" in msg and f"{am5.PARK_WAIT_S * 2:.0f}s" in msg, msg
    bound = 2 * (am5.PARK_WAIT_S + am5.PARK_POLL_S)
    assert clock.t <= bound, (
        f"the park gave up after {clock.t:.1f} s; the documented bound is "
        f"{bound:g} s (two attempts of PARK_WAIT_S, each overrun by at most "
        f"one poll)")
    assert clock.t >= 2 * am5.PARK_WAIT_S, (
        f"the park gave up after {clock.t:.1f} s; each attempt must still "
        f"wait out its PARK_WAIT_S ({2 * am5.PARK_WAIT_S:g} s for both)")
    # Two attempts, each re-sent once, each re-send at the window.
    assert len(fl.hp_at) == 4, f"expected 2 attempts x (send + one re-send): {fl.hp_at}"
    starts, resends = fl.hp_at[0::2], fl.hp_at[1::2]
    for start, resend in zip(starts, resends):
        assert am5.PARK_NOOP_DETECT_S <= resend - start <= (
            am5.PARK_NOOP_DETECT_S + am5.PARK_POLL_S), fl.hp_at
    assert starts[1] - starts[0] >= am5.PARK_WAIT_S, (
        f"the second attempt began {starts[1] - starts[0]:.1f} s after the "
        f"first; the first must still run its full PARK_WAIT_S")


# ------------------------------------------------------------------ controls

@pytest.mark.parametrize("axis, dec", [("dec", 40.0), ("ra", 90.0)],
                         ids=["on Dec", "on RA alone, from the pole"])
async def test_control_a_park_that_slews_at_once_is_never_resent(
        fixed_env, clock, bus_lines, axis, dec):
    """The mount drives home for 15 s from the first ``:hP#``, three times
    ``PARK_NOOP_DETECT_S``, and does not report parked until it arrives. It
    is moving, so the command is not sent again, and the park lands at 15 s.

    "on RA alone, from the pole" is a mount already at Dec +90 with its RA
    axis turned away from home, so the park moves the RA read and nothing
    else. Motion on either axis is motion; a detector that watched only Dec
    would call this park lost and send it into a turning mount.

    RED under mutant "re-send while slewing" (the motion check dropped: every
    mount not parked at ``PARK_NOOP_DETECT_S`` is re-sent), observed in both
    cases:
        AssertionError: a park already slewing was sent again: [0.0, 5.0]
        assert [0.0, 5.0] == [0.0]

    RED under mutant "send :hP# twice up front" (a second ``:hP#`` straight
    after the first, no evidence asked), observed in both cases:
        AssertionError: a park already slewing was sent again: [0.0, 0.0]
        assert [0.0, 0.0] == [0.0]

    RED under mutant "motion ignores RA" (``_moved_deg`` returns the Dec step
    alone), observed in "on RA alone, from the pole" only:
        AssertionError: a park already slewing was sent again: [0.0, 5.0]
        assert [0.0, 5.0] == [0.0]
    Before that case was added this mutant passed every test in the file.

    RED under mutant "motion ignores Dec" (``_moved_deg`` returns the RA step
    alone), observed in "on Dec" only:
        AssertionError: a park already slewing was sent again: [0.0, 5.0]
        assert [0.0, 5.0] == [0.0]
    """
    fl, tel = await _mount(clock, [SLEWS], slew_axis=axis, dec=dec)

    await tel.park()

    assert fl.hp_at == [0.0], f"a park already slewing was sent again: {fl.hp_at}"
    assert clock.t == pytest.approx(fl.slew_s)
    assert not [m for _l, m, _s in bus_lines if "once more" in m]


async def test_control_a_park_that_parks_at_once_sends_nothing_more(
        fixed_env, clock):
    """A mount already at home parks without moving and says so at the first
    poll. ``park()`` returns there, having sent one ``:hP#`` and nothing
    after it but reads.

    RED under mutant "send :hP# twice up front", observed:
        AssertionError: after :hP# the driver sent more than reads:
        ['hP', 'GR', 'GD', 'Gps']
        assert {'GD', 'GR', 'Gps', 'hP'} <= {'GD', 'GR', 'Gps'}
    """
    fl, tel = await _mount(clock, [PARKS])

    await tel.park()

    after = fl.sent[fl.sent.index("hP") + 1:]
    assert set(after) <= {"GR", "GD", "Gps"}, (
        f"after :hP# the driver sent more than reads: {after}")
    assert fl.hp_at == [0.0]
    assert clock.t == am5.PARK_POLL_S


async def test_control_an_unreadable_position_is_never_taken_for_stillness(
        fixed_env, clock, bus_lines):
    """The mount slews home for 15 s, but its position reads go unanswered.
    Without a measurement nothing proves the park was lost, so it is not
    sent again; and the failed reads do not fail the park, which lands when
    the flag says so.

    RED under mutant "an unreadable position counts as still" (the probe
    returns ``(0.0, 0.0)`` when the read fails), observed:
        AssertionError: re-sent on no evidence: [0.0, 5.0]
        assert [0.0, 5.0] == [0.0]

    RED under mutant "a failed position read raises" (the probe lets the
    ``DeviceError`` out), observed:
        astrodeck.devices.base.DeviceError: ZWO AM5: GR read failed:
        unscripted command 'GR'
    """
    fl, tel = await _mount(clock, [SLEWS], readable=False)

    await tel.park()

    assert fl.hp_at == [0.0], f"re-sent on no evidence: {fl.hp_at}"
    assert clock.t == pytest.approx(fl.slew_s)
