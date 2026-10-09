# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#860: an AM5 goto returns only when the mount has ARRIVED.

``ZwoAm5Telescope.slew`` used to return once two consecutive position reads
were still. It never compared the settled report with the target, so a mount
that never started read as settled in about 1.5 s, and a mount halted part
way (a Stop, or the hub's manual-move deadman) read as arrived. These cases
drive the REAL ``slew`` over the ``FakeLink`` double from ``test_zwo_am5``:

* still AND within the arrival tolerance (angular, plus the drift allowance)
  returns; still but short keeps polling;
* stuck (small steps AND no progress across the stall window) raises
  ``GotoNotArrived`` stalled; a slow approach is progress, a drifting stuck
  mount is not, and a pier-flip excursion is not still;
* a whole-mount halt during the goto raises ``GotoNotArrived`` stopped,
  unless the mount is already at the target;
* ``e14`` is ``GotoRefused`` with fixed words; an unknown code stays out of
  the reason; ``:MS#`` is never re-sent after a reopen and its link failure
  halts;
* the settle step and the halt-window step are wrap-safe in RA.

Every coordinate is fictional and non-round. No test prints a coordinate.
Each test names the mutant of ``zwo_am5.py`` it was shown red under; each
mutant was applied to a byte copy and the file restored from that copy (sha256
checked).
"""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_850_hub_sync_refused import _coordinate_spellings
from test_zwo_am5 import (FakeLink, _connect_script, _humanizer_rewrites,  # noqa: F401
                          fixed_env)

import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.catalog import coords
from astrodeck.devices import lx200
from astrodeck.devices.base import DeviceError, GotoNotArrived, GotoRefused
from astrodeck.devices.serial_link import LinkError

#: A made-up target, non-round, away from the pole.
T_RA, T_DEC = 11.2345, 45.6789
#: A made-up start 15 degrees and more from it.
S_RA, S_DEC = 10.4321, 30.8765


def _target_cmds(ra: float, dec: float) -> dict:
    return {f"Sr{lx200.format_ra(ra)}": "1", f"Sd{lx200.format_dec(dec)}": "1"}


class _Path:
    """GR/GD script entries for a mount that is at ``fn(n)`` on its n-th
    position read (0-based). ``:GR#`` starts a read and ``:GD#`` finishes it,
    which is the order ``get_position`` sends them. ``on_gd(n)`` runs inside
    the n-th ``:GD#``, before it answers."""

    def __init__(self, fn, on_gd=None):
        self.fn = fn
        self.on_gd = on_gd
        self.n = -1

    def gr(self, cmd):
        self.n += 1
        return lx200.format_ra(self.fn(self.n)[0] % 24.0)

    def gd(self, cmd):
        if self.on_gd is not None:
            self.on_gd(self.n)
        return lx200.format_dec(self.fn(self.n)[1])


async def _tel(path: _Path | None, *, target=(T_RA, T_DEC), ms="0",
               name: str = "Mount"):
    """A connected AM5 telescope whose goto replies ``ms`` and whose
    position follows ``path``. The slew-time GR/GD are set AFTER connect, so
    the handshake's own reads do not consume the path."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl, name=name)
    await tel.connect()
    fl.script.update(_target_cmds(*target))
    fl.script["MS"] = ms
    if path is not None:
        fl.script["GR"] = path.gr
        fl.script["GD"] = path.gd
    fl.sent.clear()
    return fl, tel


def _q_after_ms(fl: FakeLink) -> bool:
    return "MS" in fl.sent and "Q" in fl.sent[fl.sent.index("MS") + 1:]


def _read_back(ra: float, dec: float) -> tuple[float, float]:
    """A position as the driver reads it: formatted and parsed by the real
    codec (1 s of RA, 1 arcsec of Dec)."""
    return (lx200.parse_ra(lx200.format_ra(ra)),
            lx200.parse_dec(lx200.format_dec(dec)))


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)


# ------------------------------------------------------------------ the stall


async def test_a_goto_that_never_starts_raises_stalled_not_arrived(
        fixed_env, fast, monkeypatch):
    """The mount took ``:MS#`` and never moved. The old loop called that
    settled at the third read; now it is ``GotoNotArrived`` with the fixed
    stalled words, the separation it was left at, and a ``:Q#``.

    NAMED MUTANT M1 "no arrival test" (``if stable >= 2 and arrived:`` ->
    ``if stable >= 2:``): RED, ``Failed: DID NOT RAISE``.

    NAMED MUTANT M5 "per-axis residual" (below) also turns it red: the
    residual is 14.80 per axis, not the 17.52 deg of sky.

    NAMED MUTANT M2 "no stall" (the ``stable >= stall_polls`` raise deleted):
    RED, ``TimeoutError`` from the 5 s ``wait_for``.

    NAMED MUTANT M8 "figure in the reason" (the stall raise passes
    ``f"{GOTO_STALLED_REASON} by {off:.2f} deg"``): RED, ``assert 'the mount
    st... by 17.52 deg' == 'the mount st...of the target'`` (the exact-words
    assertion goes before the no-digit one).
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.05)
    fl, tel = await _tel(_Path(lambda n: (S_RA, S_DEC)))

    with pytest.raises(GotoNotArrived) as info:
        await asyncio.wait_for(tel.slew(T_RA, T_DEC), 5.0)

    e = info.value
    assert e.reason == am5.GOTO_STALLED_REASON
    assert not re.search(r"\d", e.reason), e.reason
    assert e.residual_deg == pytest.approx(
        coords.angular_sep_deg(T_RA, T_DEC, *_read_back(S_RA, S_DEC)))
    assert e.residual_deg > 15.0
    assert _q_after_ms(fl), fl.sent


async def test_a_pause_short_of_the_target_is_waited_through(
        fixed_env, fast, monkeypatch):
    """Still but short is not an arrival: a mount that pauses for a few
    polls short of the target and then goes on is waited for, not halted.

    NAMED MUTANT M1 "no arrival test": RED, the slew returns at the pause
    after 3 position reads (``assert 3 >= 7``).
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.1)
    steps = [(S_RA, S_DEC)] * 4 + [(T_RA, T_DEC - 2.0)] + [(T_RA, T_DEC)] * 3

    fl, tel = await _tel(_Path(lambda n: steps[min(n, len(steps) - 1)]))
    await asyncio.wait_for(tel.slew(T_RA, T_DEC), 5.0)

    assert fl.sent.count("GR") >= 7, fl.sent
    assert not _q_after_ms(fl), fl.sent


async def test_a_slow_final_approach_is_progress_not_a_stall(
        fixed_env, fast, monkeypatch):
    """The AM5's slow rate steps under ``SETTLE_DEG`` per poll, which is
    "still" for arrival but not "stuck": 0.01 deg per poll from 1 deg out is
    0.2 deg per window, twice ``GOTO_PROGRESS_DEG``, so it arrives.

    NAMED MUTANT M10 "stall on step only" (``if stable >= stall_polls and
    not _made_progress(window):`` -> ``if stable >= stall_polls:``): RED,
    ``GotoNotArrived`` stalled about 0.8 deg out.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.2)

    def at(n):
        return (T_RA, T_DEC + max(0.0, 1.0 - 0.01 * n))

    fl, tel = await _tel(_Path(at))
    await asyncio.wait_for(tel.slew(T_RA, T_DEC), 10.0)

    assert not _q_after_ms(fl), fl.sent


async def test_a_stuck_mount_drifting_toward_the_target_still_stalls(
        fixed_env, fast, monkeypatch):
    """A stuck mount whose report drifts toward the target (0.004 deg per
    poll, 0.08 deg per window, under ``GOTO_PROGRESS_DEG``) is still stuck,
    and stalls long before the deadline.

    NAMED MUTANT M11 "any improvement is progress" (in ``_made_progress``,
    ``first - last >= GOTO_PROGRESS_DEG`` -> ``first - last > 0``): RED, the
    deadline's plain ``DeviceError`` ("slew failed to settle within 2s")
    instead of ``GotoNotArrived``.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.2)
    monkeypatch.setattr(am5, "SLEW_TIMEOUT_S", 2.0)

    def at(n):
        return (T_RA, T_DEC - 5.0 + 0.004 * n)

    fl, tel = await _tel(_Path(at))
    with pytest.raises(GotoNotArrived) as info:
        await asyncio.wait_for(tel.slew(T_RA, T_DEC), 5.0)

    assert info.value.reason == am5.GOTO_STALLED_REASON
    assert _q_after_ms(fl), fl.sent


async def test_a_flip_excursion_away_from_the_target_is_not_a_stall(
        fixed_env, fast, monkeypatch):
    """A pier-flip goto swings AWAY from the target for a while: no progress,
    but fast steps, so the window is never all still and no stall is called.

    NAMED MUTANT M12 "stall on no progress only" (``if stable >= stall_polls
    and not _made_progress(window):`` -> ``if len(window) == window.maxlen
    and not _made_progress(window):``): RED, ``GotoNotArrived`` stalled
    during the excursion.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.2)

    def at(n):
        if n <= 30:
            return (T_RA, T_DEC - 2.0 - 0.5 * n)
        return (T_RA, min(T_DEC, T_DEC - 17.0 + 0.5 * (n - 30)))

    fl, tel = await _tel(_Path(at))
    await asyncio.wait_for(tel.slew(T_RA, T_DEC), 10.0)

    assert not _q_after_ms(fl), fl.sent


# ---------------------------------------------------------------- the arrival


async def test_arrival_allows_drift_since_the_goto_began(
        fixed_env, fast, monkeypatch):
    """The tolerance grows with the time since ``:MS#`` (a fixed-hour-angle
    firmware with tracking off walks in RA). With the rate patched large, a
    report held 0.5 deg off is inside the allowance within a few polls.

    NAMED MUTANT M4 "no drift allowance" (``allow = GOTO_ARRIVE_DEG``): RED,
    ``GotoNotArrived`` stalled.
    """
    monkeypatch.setattr(am5, "GOTO_DRIFT_DEG_S", 20.0)
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.5)
    fl, tel = await _tel(_Path(lambda n: (T_RA, T_DEC + 0.5)))

    await asyncio.wait_for(tel.slew(T_RA, T_DEC), 5.0)
    assert not _q_after_ms(fl), fl.sent


async def test_arrival_is_measured_by_angular_separation_near_the_pole(
        fixed_env, fast, monkeypatch):
    """Near the pole an hour of RA is a few hundredths of a degree of sky:
    target (3.0 h, +89.9), report (4.0 h, +89.9) is 0.026 deg apart. Measured
    per axis it would be 15 deg and the goto would never arrive.

    NAMED MUTANT M5 "per-axis residual" (``off = _moved_deg((ra_hours,
    dec_deg), (ra, dec))``): RED, ``GotoNotArrived`` stalled.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.05)
    fl, tel = await _tel(_Path(lambda n: (4.0, 89.9)), target=(3.0, 89.9))

    await asyncio.wait_for(tel.slew(3.0, 89.9), 5.0)
    assert not _q_after_ms(fl), fl.sent


async def test_a_still_mount_across_ra_midnight_settles(
        fixed_env, fast, monkeypatch):
    """A still mount reading 23:59:59 then 00:00:00 has moved one second of
    RA, not 360 degrees.

    NAMED MUTANT M6 "old settle step" (``_moved_deg(prev, (ra, dec))`` in the
    stillness test -> ``max(abs(ra - prev[0]) * 15.0, abs(dec - prev[1]))``):
    RED, the 0.3 s deadline's ``DeviceError`` ("slew failed to settle").
    """
    monkeypatch.setattr(am5, "SLEW_TIMEOUT_S", 0.3)
    fl, tel = await _tel(None, target=(0.0, 45.0))
    n = {"i": 0}

    def gr(cmd):
        n["i"] += 1
        return "23:59:59" if n["i"] % 2 else "00:00:00"

    fl.script["GR"] = gr
    fl.script["GD"] = "+45*00:00"

    await asyncio.wait_for(tel.slew(0.0, 45.0), 5.0)
    assert not _q_after_ms(fl), fl.sent


# ------------------------------------------------------------------- the halt


def _moving_until_q(fl: FakeLink):
    """GR/GD that alternate between two points far from the target while no
    ``:Q#`` has gone out, and hold one short-of-target point after."""
    n = {"i": 0}

    def at():
        if "Q" in fl.sent:
            return (S_RA, S_DEC)
        return (S_RA, S_DEC) if n["i"] % 2 else (S_RA + 0.5, S_DEC + 5.0)

    def gr(cmd):
        n["i"] += 1
        return lx200.format_ra(at()[0])

    def gd(cmd):
        return lx200.format_dec(at()[1])

    return gr, gd


async def test_a_stop_during_the_goto_is_named_stopped(
        fixed_env, fast, monkeypatch):
    """``stop()`` during a goto: the halted mount is not "arrived" and not
    "stalled"; it is a goto a stop ended.

    NAMED MUTANT M3 "halts not counted" (``self._halt_gen += 1`` deleted from
    ``_note_halt``): RED, the reason is the stalled words.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 1.0)
    fl, tel = await _tel(None)
    fl.script["GR"], fl.script["GD"] = _moving_until_q(fl)

    task = asyncio.create_task(tel.slew(T_RA, T_DEC))
    await asyncio.sleep(0.05)
    await tel.stop()

    with pytest.raises(GotoNotArrived) as info:
        await asyncio.wait_for(task, 3.0)
    assert info.value.reason == am5.GOTO_STOPPED_REASON


async def test_the_manual_move_deadman_ends_a_goto_as_stopped(
        fixed_env, fast, monkeypatch, sim_hub):
    """The REAL deadman: a stale jog keepalive makes ``Hub._move_watchdog``
    bump the motion epoch and call ``tel.stop()``, which notes the halt. The
    goto it lands in ends as stopped, never as arrived.

    NAMED MUTANT M3 "halts not counted": RED, the reason is the stalled
    words.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 1.0)
    fl, tel = await _tel(None)
    fl.script["GR"], fl.script["GD"] = _moving_until_q(fl)
    hub = sim_hub
    monkeypatch.setitem(hub.devices, "telescope", tel)
    epoch = hub._motion_epoch
    hub._move_rates_seen = {"ra": 0.5, "dec": 0.0}
    hub.last_move_ts = time.monotonic() - 10

    task = asyncio.create_task(tel.slew(T_RA, T_DEC))
    try:
        hub.ensure_move_watchdog()
        with pytest.raises(GotoNotArrived) as info:
            await asyncio.wait_for(task, 5.0)
    finally:
        wd = hub._move_watchdog_task
        if wd is not None:
            wd.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wd
    assert info.value.reason == am5.GOTO_STOPPED_REASON
    assert hub._motion_epoch > epoch


async def test_a_halt_landing_on_an_arrived_mount_returns(
        fixed_env, fast, monkeypatch):
    """A halt that lands on the very poll the mount proves it arrived does
    not undo the arrival: the report is at the target.

    NAMED MUTANT M13 "halt before arrival" (the ``_halt_gen`` raise moved
    above the ``if prev is not None:`` arrival block): RED,
    ``GotoNotArrived`` stopped.
    """
    steps = [(S_RA, S_DEC), (S_RA + 0.3, S_DEC + 3.0)] + [(T_RA, T_DEC)] * 3
    tel_box: dict = {}

    def on_gd(n):
        if n == 4:      # the read that brings ``stable`` to 2
            tel_box["tel"]._note_halt()

    path = _Path(lambda n: steps[min(n, len(steps) - 1)], on_gd=on_gd)
    fl, tel = await _tel(path)
    tel_box["tel"] = tel

    await asyncio.wait_for(tel.slew(T_RA, T_DEC), 5.0)
    assert tel._halt_gen == 1, "the halt did land during the goto"


async def test_a_halt_window_across_ra_midnight_closes(fixed_env):
    """``get_position``'s halt window ends on two still reads, and a still
    mount at RA midnight is still.

    NAMED MUTANT M14 "old halt-window step" (the check back to
    ``max(abs(ra - prev[0]) * 15.0, abs(dec - prev[1])) < SETTLE_DEG``): RED,
    ``assert True is False``.
    """
    fl, tel = await _tel(None, target=(0.0, 45.0))
    n = {"i": 0}

    def gr(cmd):
        n["i"] += 1
        return "23:59:59" if n["i"] % 2 else "00:00:00"

    fl.script["GR"] = gr
    fl.script["GD"] = "+45*00:00"
    tel._note_halt()
    await tel.get_position()
    await tel.get_position()
    assert tel._halting is False


# --------------------------------------------------------------- the refusals


@pytest.mark.parametrize("gps,words", [
    ("2", am5.GOTO_E14_PARKED_REASON),
    ("0", am5.GOTO_E14_STATE_REASON),
])
async def test_e14_to_a_goto_is_a_goto_refused(fixed_env, gps, words):
    """``e14`` to ``:MS#`` is ``GotoRefused`` like ``e6``, so the resume
    ladder's ``GotoRefused`` arm reads its fixed words, never the code.

    NAMED MUTANT M7 "old e14 path" (``raise await self._goto_refused_e14()``
    -> ``raise await self._refused_error("goto")``): RED in both cases,
    ``DeviceError`` is not ``GotoRefused``.
    """
    fl, tel = await _tel(None, ms="e14")
    fl.script["Gps"] = gps

    with pytest.raises(GotoRefused) as info:
        await tel.slew(T_RA, T_DEC)

    e = info.value
    assert e.code == "e14"
    assert e.reason == words
    assert not re.search(r"\d", e.reason), e.reason
    if gps == "2":
        assert "parked" in str(e)
    else:
        assert "parked" not in str(e), "never send the user to unpark"


async def test_an_unknown_goto_code_stays_out_of_the_reason(fixed_env):
    """An ``eN`` nobody has captured keeps its code in the message, which is
    searchable, and out of the reason, which becomes a hold reason (#618).

    NAMED MUTANT M15 "code in the reason" (``reason=_goto_refusal_reason(
    reply)`` -> ``reason=words``): RED, the no-digit assertion (the reason
    holds "(code e3)").
    """
    fl, tel = await _tel(None, ms="e3")

    with pytest.raises(GotoRefused) as info:
        await tel.slew(T_RA, T_DEC)

    e = info.value
    assert e.reason == am5.GOTO_UNKNOWN_REFUSAL_REASON
    assert not re.search(r"\d", e.reason), e.reason
    assert "code e3" in str(e)


async def test_a_link_failure_on_the_goto_is_not_resent_and_halts(
        fixed_env, fast, monkeypatch):
    """The link drops on ``:MS#``. A re-send after the reopen could reach a
    mount already slewing to the first one; instead the goto is sent once,
    and the driver halts (``:Q#``, which reopens the port, counted in
    ``_halt_gen``) and raises ``GotoNotArrived`` with the fixed link words
    and today's message, so the centring loop can solve where the mount is
    (fix round 1). Here the position is held AT the target, so a re-sent goto
    would settle and return.

    NAMED MUTANT M16 "re-sent" (``retry=False`` dropped from the ``:MS#``
    request): RED, ``Failed: DID NOT RAISE``.

    NAMED MUTANT M17 "no halt" (the link arm's ``await self._request("Q",
    reply="none")`` replaced with ``pass``): RED, no ``"Q"`` after ``"MS"``.

    NAMED MUTANT X1 "halt not noted" (``self._note_halt()`` deleted from the
    link arm): RED, ``_halt_gen`` did not grow.

    NAMED MUTANT F1-L "untyped link raise" (the link arm raises
    ``err from exc``, the plain ``DeviceError``, again): RED, not a
    ``GotoNotArrived``.
    """
    monkeypatch.setattr(am5, "RELINK_MIN_INTERVAL_S", 0.0)
    fl, tel = await _tel(_Path(lambda n: (T_RA, T_DEC)))
    state = {"dropped": False}

    def ms(cmd):
        if not state["dropped"]:
            state["dropped"] = True
            fl.drop()
            raise LinkError("timeout waiting for ack on COM3")
        return "0"

    fl.script["MS"] = ms
    opens = fl.opens
    halts = tel._halt_gen

    with pytest.raises(DeviceError) as info:
        await tel.slew(T_RA, T_DEC)

    e = info.value
    assert not isinstance(e, LinkError)
    assert isinstance(e, GotoNotArrived), type(e)
    assert e.reason == am5.GOTO_LINK_REASON
    assert not re.search(r"\d", e.reason), e.reason
    assert e.residual_deg is None
    # Today's words: built before the halt was noted, so the idle wire text.
    assert "timeout waiting for ack on COM3" in str(e)
    assert tel._halt_gen == halts + 1, "the halt was noted"
    assert fl.sent.count("MS") == 1, fl.sent
    assert _q_after_ms(fl), fl.sent
    assert fl.opens > opens, "the halt reopened the port"


def test_an_unmeasurable_end_of_the_window_is_no_progress():
    """``_made_progress`` with a ``None`` at either end (a residual nobody
    could compute) is no progress, even when the other end would make the
    fall look huge: a stuck mount is never excused by an unmeasured read.

    NAMED MUTANT X2 "None is progress" (the None branch's ``return False``
    made ``return True``): RED on the first assertion.
    """
    import collections
    assert am5._made_progress(collections.deque([None, 0.0])) is False
    assert am5._made_progress(collections.deque([5.0, None])) is False
    # Control: the same window with both ends measured is progress.
    assert am5._made_progress(collections.deque([5.0, 0.0])) is True


# ---------------------------------------------------------------- the words


async def test_the_not_arrived_line_has_no_coordinate_and_fits_the_cut(
        fixed_env, fast, monkeypatch):
    """The message names the reason and the separation, never the target
    or the position read back (#140, #166), and fits the UI's 137-character
    cut behind the ``goto failed: `` prefix ``/api/mount/goto`` adds, with
    the longest driver name.

    NAMED MUTANT M9 "coordinates in the message" (``_not_arrived``'s
    ``where`` f-string becomes ``f"; it reports {residual:.2f} deg from the
    target at {self._last_pos[0]:.4f}h {self._last_pos[1]:+.4f}"``): RED, the
    length assertion first (156 characters), ahead of the spelling ones.
    """
    monkeypatch.setattr(am5, "GOTO_STALL_S", 0.05)
    fl, tel = await _tel(_Path(lambda n: (S_RA, S_DEC)),
                         name="ZWO AM5 (native serial)")

    with pytest.raises(GotoNotArrived) as info:
        await asyncio.wait_for(tel.slew(T_RA, T_DEC), 5.0)

    msg = str(info.value)
    surfaced = "goto failed: " + msg
    assert len(surfaced) <= 137, (len(surfaced), surfaced)
    assert not _humanizer_rewrites(surfaced), surfaced
    fig = f"{info.value.residual_deg:.2f}"
    assert fig in msg
    read_ra, read_dec = tel._last_pos
    spellings = (_coordinate_spellings(T_RA, T_DEC)
                 + _coordinate_spellings(read_ra, read_dec)
                 + [lx200.format_ra(T_RA), lx200.format_dec(T_DEC),
                    lx200.format_ra(read_ra), lx200.format_dec(read_dec)])
    # Precondition: the separation's own spelling cannot hide a coordinate.
    assert not any(s in fig for s in spellings), fig
    for s in spellings:
        assert s not in msg, (s, msg)
