# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""ZWO AM5N native serial driver: link double, telescope behavior, backend +
framework integration. All coordinates fictional (site privacy)."""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from _deadline import wait_until
from astrodeck.devices.serial_link import LinkError, SerialLink


class FakeLink:
    """Test double for SerialLink: same ``request``/``request_sync``/``close``
    surface.

    ``script`` maps an unframed command string to either a reply string, a list
    of replies (consumed in order; last repeats), or a callable(cmd)->reply.
    Unscripted "hash"/"ack" requests raise LinkError (like a silent mount);
    unscripted "none" requests are simply logged.

    ``sent_at`` timestamps EVERY command (from either entry point) with
    ``time.monotonic``, which is what lets a test prove a stop command left the
    driver while the event loop was blocked (GN-02).
    """

    def __init__(self, script: dict | None = None):
        self.script = dict(script or {})
        self.sent: list[str] = []
        #: (cmd, time.monotonic()) for every command, async or sync.
        self.sent_at: list[tuple[str, float]] = []
        #: the subset that went out through ``request_sync`` (thread side).
        self.sync_sent: list[str] = []
        self.closed = False
        # Mirrors SerialLink's abandoned/needs_reopen surface. Not optional:
        # the driver consults it on every LinkError, and a double missing it
        # would turn an honest refusal into an AttributeError.
        self._abandoned = False
        # Starts open so a bare FakeLink (no connect) still answers, which is
        # what the older tests in this file assume.
        self._open = True
        self.opens = 0

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def needs_reopen(self) -> bool:
        return self._abandoned and not self._open

    def drop(self) -> None:
        """Simulate ``SerialLink._abandon``: the port died under us."""
        self._abandoned = True
        self._open = False

    async def open(self) -> None:  # parity with SerialLink
        self.opens += 1
        self._open = True
        self._abandoned = False

    def _exchange(self, cmd: str, reply: str):
        """Shared body of ``request``/``request_sync`` (recording included)."""
        if self._abandoned:
            # A dropped port answers NOTHING, including fire-and-forget writes.
            # A double that kept replying would let the reopen tests pass
            # without a reopen ever happening.
            raise LinkError("the link was dropped after a stalled exchange and "
                            "has not been reopened")
        self.sent.append(cmd)
        self.sent_at.append((cmd, time.monotonic()))
        entry = self.script.get(cmd)
        if callable(entry):
            entry = entry(cmd)
        elif isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if reply == "none":
            return None
        if entry is None:
            raise LinkError(f"unscripted command {cmd!r}")
        return entry

    async def request(self, cmd: str, *, reply: str = "hash",
                      timeout: float = 1.5):
        return self._exchange(cmd, reply)

    def request_sync(self, cmd: str, *, reply: str = "hash",
                     timeout: float = 1.5):
        """Thread-side entry point (SerialLink parity). Called from the pulse
        watchdog thread, so it must never touch the event loop."""
        out = self._exchange(cmd, reply)
        self.sync_sent.append(cmd)
        return out

    async def close(self) -> None:
        self.closed = True
        self._open = False
        self._abandoned = False


from astrodeck.devices import lx200 as _lx200  # noqa: E402


class _Am5Model(FakeLink):
    """A ``FakeLink`` that keeps a POSITION, for the sync read-back (#850).

    ``:Sr#``/``:Sd#`` set a pending target (parsed with the real codec) and
    ack ``1``. ``:CM#`` replies ``cm_reply`` and, when ``moves`` is true,
    moves the reported position to the pending target (or to ``move_to`` when
    given, to model a mount that lands a rounding step away). With
    ``late_reads`` > 0 the move shows only after that many position reads
    (``:GR#`` counts one read), which is the unmeasured update latency.
    ``:GR#``/``:GD#`` report the current position formatted by the real codec.

    Everything else is the plain script, so ``_connect_script()`` still drives
    the handshake, whose ``:GD#`` reads this model's starting position.
    Coordinates are fictional."""

    def __init__(self, script: dict | None = None, *,
                 pos: tuple[float, float] = (9.5, 37.0),
                 cm_reply: str = "N/A", moves: bool = True,
                 move_to: tuple[float, float] | None = None,
                 late_reads: int = 0):
        super().__init__(script)
        self.pos = pos
        self.cm_reply = cm_reply
        self.moves = moves
        self.move_to = move_to
        self.late_reads = late_reads
        self.pending: list[float | None] = [None, None]
        self._staged: tuple[float, float] | None = None
        self._staged_left = 0
        self.script["CM"] = self._cm
        self.script["GR"] = self._gr
        self.script["GD"] = self._gd

    def _exchange(self, cmd: str, reply: str):
        # The set-target commands carry the value in the verb, so they cannot
        # be scripted by key ahead of time; route every one to the model.
        if cmd[:2] in ("Sr", "Sd") and cmd not in self.script:
            self.script[cmd] = self._set_pending
        return super()._exchange(cmd, reply)

    def _set_pending(self, cmd: str) -> str:
        if cmd.startswith("Sr"):
            self.pending[0] = _lx200.parse_ra(cmd[2:])
        else:
            self.pending[1] = _lx200.parse_dec(cmd[2:])
        return "1"

    def _cm(self, cmd: str) -> str:
        if self.moves:
            dest = self.move_to or (self.pending[0], self.pending[1])
            if self.late_reads:
                self._staged, self._staged_left = dest, self.late_reads
            else:
                self.pos = dest
        return self.cm_reply

    def _gr(self, cmd: str) -> str:
        if self._staged is not None:
            if self._staged_left == 0:
                self.pos, self._staged = self._staged, None
            else:
                self._staged_left -= 1
        return _lx200.format_ra(self.pos[0])

    def _gd(self, cmd: str) -> str:
        return _lx200.format_dec(self.pos[1])


# ------------------------------------------------------------- link double

async def test_fakelink_sequences_and_logging():
    fl = FakeLink({"GR": ["10:00:00", "11:00:00"], "Spu": "1"})
    assert await fl.request("GR") == "10:00:00"
    assert await fl.request("GR") == "11:00:00"
    assert await fl.request("GR") == "11:00:00"          # last repeats
    assert await fl.request("Spu", reply="ack") == "1"
    assert await fl.request("Me", reply="none") is None  # unscripted fire-forget ok
    assert fl.sent == ["GR", "GR", "GR", "Spu", "Me"]
    with pytest.raises(LinkError):
        await fl.request("GD")                            # unscripted read raises


async def test_seriallink_requires_open():
    link = SerialLink("COM99")
    with pytest.raises(LinkError):
        await link.request("GR")


# ------------------------------------------------------- telescope: connect/state

from datetime import datetime, timezone  # noqa: E402

from astrodeck.devices.base import DeviceError, PierSide  # noqa: E402
import astrodeck.devices.backends.zwo_am5 as am5  # noqa: E402

FIXED_UTC = datetime(2026, 7, 19, 22, 14, 58, tzinfo=timezone.utc)


def _connect_script(**over):
    """A FakeLink script for a successful connect (parked mount)."""
    s = {
        "GVP": "AM5N", "GV": "1.8.8",
        "SG+00:00": "1", "SH0": "1", "SC07/19/26": "1", "SL22:14:58": "1",
        "SMGE+40*00:00&+100*30:30": "1",
        "Gps": "2", "GU": "nGM000000005",
        "GR": "10:13:56", "GD": "+90*00:00", "GAT": "0",
    }
    s.update(over)
    return s


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


async def test_connect_flow_sequence_no_autounpark(fixed_env):
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert tel.connected is True
    assert tel.hardware is True and tel.backend == "zwo-am5"
    # exact init order, and NO :Spu# (connect must not auto-unpark)
    assert fl.sent[:7] == ["GVP", "GV", "SG+00:00", "SH0", "SC07/19/26",
                           "SL22:14:58", "SMGE+40*00:00&+100*30:30"]
    assert "Spu" not in fl.sent


async def test_connect_identity_mismatch_closes(fixed_env):
    fl = FakeLink(_connect_script(GVP="Prototype"))
    tel = am5.ZwoAm5Telescope(fl)
    with pytest.raises(DeviceError):
        await tel.connect()
    assert fl.closed is True and tel.connected is False


async def test_parked_state_and_unpark(fixed_env):
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert await tel.is_parked() is True          # Gps -> "2"
    fl.script["Spu"] = "1"
    await tel.unpark()                             # parked -> sends :Spu#
    assert "Spu" in fl.sent
    fl.script["Gps"] = "0"
    assert await tel.is_parked() is False


async def test_unpark_is_idempotent_when_already_unparked(fixed_env):
    """At-scope finding: :Spu# replies '0' when there is no park to cancel —
    unpark() must check state first and no-op, not raise."""
    fl = FakeLink(_connect_script(Gps="0"))       # already unparked
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.unpark()                             # must not raise
    assert "Spu" not in fl.sent


async def test_park_is_idempotent_when_already_parked(fixed_env):
    fl = FakeLink(_connect_script())               # Gps -> "2" (parked)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.park()                               # must not raise / not send
    assert "hP" not in fl.sent


async def test_guide_rates_report_emulated_pulse_rate(fixed_env):
    """guide_rates reports what pulses ACTUALLY deliver — the R1 preset — not
    the mount's :GdG# setting (which governs only the inert :Mg*# path)."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    v = await tel.guide_rates()
    assert v == (am5._PULSE_RA_RATE_DEG_S, am5._PULSE_DEC_RATE_DEG_S)
    assert "GdG" not in fl.sent


async def test_position_and_tracking_reads(fixed_env):
    fl = FakeLink(_connect_script(GAT="1"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    ra, dec = await tel.get_position()
    assert abs(ra - (10 + 13 / 60 + 56 / 3600)) < 1e-9 and dec == 90.0
    assert await tel.get_tracking() is True
    fl.script["Gm"] = "E"
    assert await tel.pier_side() == PierSide.EAST


@pytest.mark.parametrize("gps, expect", [
    ("2", "parked"),                    # really parked -> say so
    ("0", "refused in current state"),  # e14 for some OTHER reason (limits, ...)
    (None, "refused in current state"), # park probe itself fails -> stay generic
])
async def test_e14_refusal_is_labeled_honestly(fixed_env, gps, expect):
    """``e14`` is "refused in current state" — parked is only ONE cause. The
    driver probes :Gps# on the error path and must never blame park unless the
    mount actually reports parked (and a failed probe must not mask the error)."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.script["Te"] = "e14"
    if gps is None:
        fl.script.pop("Gps")            # probe raises LinkError -> best-effort
    else:
        fl.script["Gps"] = gps
    with pytest.raises(DeviceError, match=expect) as exc:
        await tel.set_tracking(True)
    if gps != "2":
        assert "parked" not in str(exc.value)   # never send the user to unpark


# ------------------------------------------- telescope: silence during a slew
#
# The AM5's OTHER way of saying no. ``e14#`` (above) is an explicit refusal and
# already gets a plain-English sentence. A mount that is busy driving the axes
# simply does not answer inside the 1.5 s exchange window, and that path used to
# hand the user the transport text straight through. From the rig:
#
#     409 - ZWO AM5 (native serial): tracking on failed: timeout waiting for
#           ack on COM3
#
# Every word true, none of it actionable: it names a COM port and an ack byte,
# so it reads as "your mount is broken" when it means "the mount is busy
# slewing". ``/api/mount/tracking`` does not take hub._motion_lock, so landing
# mid-slew is an ordinary thing for a user to do.

def _silent_ack(port: str = "COM3"):
    """Script entry: mount never answers an ack-class command (rig's wording)."""
    def _raise(cmd):
        raise LinkError(f"timeout waiting for ack on {port}")
    return _raise


def _silent_read(port: str = "COM3"):
    """Script entry: mount never answers a hash-class read."""
    def _raise(cmd):
        raise LinkError(f"timeout waiting for '#' on {port} (got b'')")
    return _raise


async def test_slewing_flag_starts_false(fixed_env):
    """``_slewing`` is now read on an ERROR path, so it must exist from
    construction — a getattr default is not enough once a real attribute lookup
    can turn an honest refusal into an AttributeError. ``_halting`` is read on
    the same path and gets the same guarantee."""
    tel = am5.ZwoAm5Telescope(FakeLink(_connect_script()))
    assert tel._slewing is False
    assert tel._halting is False and tel._halt_ref is None
    assert await tel.is_slewing() is False


async def test_ack_timeout_mid_slew_blames_the_slew_not_the_cable(fixed_env):
    s = _connect_script()
    s["Te"] = _silent_ack()
    fl, tel = await _connected_tel(s)
    tel._slewing = True                      # a goto is still settling
    with pytest.raises(DeviceError) as exc:
        await tel.set_tracking(True)
    msg = str(exc.value)
    assert "slewing" in msg and "Stop" in msg
    # The serial layer must not be in the sentence the OWNER reads. (Matching on
    # the phrase, not the bare word "ack" — "tracking" contains it.)
    assert "COM3" not in msg
    assert "waiting for ack" not in msg
    # ...but it is still chained, so the log/traceback keeps the wire detail.
    assert isinstance(exc.value.__cause__, LinkError)
    assert "COM3" in str(exc.value.__cause__)


async def test_ack_timeout_with_the_mount_idle_still_surfaces_the_serial_fault(fixed_env):
    """The rewrite is gated on ``_slewing`` precisely so this case is untouched:
    an unplugged cable / dead port with NO slew running is a genuine transport
    fault, and the transport text IS the diagnosis."""
    s = _connect_script()
    s["Te"] = _silent_ack()
    fl, tel = await _connected_tel(s)
    assert tel._slewing is False
    with pytest.raises(DeviceError) as exc:
        await tel.set_tracking(True)
    msg = str(exc.value)
    assert "timeout waiting for ack on COM3" in msg
    assert "slewing" not in msg


async def test_goto_silence_is_a_device_error_not_a_raw_link_error(fixed_env):
    """``slew``'s ``:MS#`` was the one ack-class send that bypassed ``_cmd_ack``,
    so a silent mount escaped as a ``LinkError`` — which the API's
    ``except DeviceError`` handlers do not catch, turning a busy mount into a
    500 instead of a 409."""
    s = _connect_script()
    s["Sr11:00:00"] = "1"
    s["Sd+45*00:00"] = "1"
    s["MS"] = _silent_ack()
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError) as exc:
        await tel.slew(11.0, 45.0)
    assert not isinstance(exc.value, LinkError)
    assert "timeout waiting for ack on COM3" in str(exc.value)   # idle: wire truth


async def test_a_second_goto_while_the_first_is_settling_is_honest(fixed_env):
    """Target-set is ack-class too, so a re-issued goto hits ``_cmd_ack`` before
    it ever reaches ``:MS#``."""
    s = _connect_script()
    s["Sr11:00:00"] = _silent_ack()
    fl, tel = await _connected_tel(s)
    tel._slewing = True
    with pytest.raises(DeviceError, match="slewing"):
        await tel.slew(11.0, 45.0)


async def test_reads_still_report_the_wire_even_mid_slew(fixed_env):
    """Reads are deliberately NOT rewritten: the busiest caller of ``_get`` is
    the settle poll INSIDE ``slew`` itself, and it needs the transport truth to
    decide whether to halt the mount. Telling that loop "the mount is slewing"
    would be circular and would hide a link that died mid-goto."""
    s = _connect_script()
    fl, tel = await _connected_tel(s)
    tel._slewing = True
    fl.script["GAT"] = _silent_read()
    with pytest.raises(DeviceError) as exc:
        await tel.get_tracking()
    assert "read failed" in str(exc.value)
    assert "COM3" in str(exc.value)


# ------------------------------------------------- telescope: the cancel window
#
# THE CARRY-IN from the review of 797588b. The rewrite above is gated on
# ``_slewing``, and ``slew()``'s ``finally`` clears that flag the instant the
# settle poll exits -- which on a CANCEL is while the mount is still physically
# decelerating, because :Q# is fire-and-forget and the axes have mass. The rig
# log has the ordering: "goto cancelled" at 00:50:45, then the 409 after it. So
# the moment the mount was LEAST able to answer was the one moment with no
# explanation attached.
#
# The fix invents no grace period (nobody has measured how long an AM5 takes to
# stop, and a made-up number would either expire early or swallow a real COM
# timeout for that long). It ends the window on evidence instead: the mount's
# own reported position going still, or any successful ack.


def _never_settles(s: dict) -> dict:
    """Script GR/GD to alternate FOREVER, so a slew never settles on its own.
    (Callables, not lists: FakeLink repeats a list's last element, which would
    read as settled.)"""
    n = {"ra": 0, "dec": 0}
    s["GR"] = lambda cmd: ["10:00:00", "10:30:00"][
        n.__setitem__("ra", n["ra"] + 1) or n["ra"] % 2]
    s["GD"] = lambda cmd: ["+10*00:00", "+20*00:00"][
        n.__setitem__("dec", n["dec"] + 1) or n["dec"] % 2]
    return s


async def _cancelled_mid_slew(fixed_env, monkeypatch):
    """Drive the REAL cancel path and return (link, telescope) in the window."""
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    s = _never_settles(_connect_script())
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"; s["MS"] = "0"
    fl, tel = await _connected_tel(s)
    task = asyncio.create_task(tel.slew(11.0, 45.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Q" in fl.sent                    # the halt went out
    # THE PRECONDITION FOR THE WHOLE BUG: the flag the mid-slew rewrite keys on
    # is already gone, one line after the mount was told to stop.
    assert tel._slewing is False
    return fl, tel


async def test_a_cancelled_goto_explains_the_409_it_causes(fixed_env, monkeypatch):
    """The reported ordering, end to end: cancel a goto, then hit tracking-on
    while the mount is still coming to a halt."""
    fl, tel = await _cancelled_mid_slew(fixed_env, monkeypatch)
    fl.script["Te"] = _silent_ack()

    with pytest.raises(DeviceError) as exc:
        await tel.set_tracking(True)

    msg = str(exc.value)
    assert "told to stop" in msg and "slowing down" in msg
    # THE WIRE TEXT STAYS, unlike the mid-slew case. While _slewing is true the
    # settle poll is reading GR/GD every 500 ms and succeeding, so the link is
    # provably healthy and "busy" is the only explanation left. After a halt
    # there is no such proof -- the mount may equally have died at that moment
    # -- so the honest form is the context PLUS what the wire said.
    assert "timeout waiting for ack on COM3" in msg
    assert isinstance(exc.value.__cause__, LinkError)


async def test_the_window_closes_when_the_mount_reports_it_has_stopped(
        fixed_env, monkeypatch):
    """The window is bounded by a MEASUREMENT, not a clock: two position reads
    a settle-threshold apart or less are the same evidence ``slew`` accepts as
    "the goto finished". The hub's 2 s status loop supplies them for free."""
    fl, tel = await _cancelled_mid_slew(fixed_env, monkeypatch)
    fl.script["GR"] = "11:00:00"          # the mount has come to rest
    fl.script["GD"] = "+45*00:00"

    await tel.get_position()              # first post-halt read: reference only
    assert tel._halting is True, "one read cannot prove anything stopped"
    await tel.get_position()              # second: unchanged -> at rest

    assert tel._halting is False
    fl.script["Te"] = _silent_ack()
    with pytest.raises(DeviceError) as exc:
        await tel.set_tracking(True)
    # A mount that is standing still and still will not answer is a genuine
    # transport fault again, and the transport text IS the diagnosis.
    msg = str(exc.value)
    assert "told to stop" not in msg
    assert "timeout waiting for ack on COM3" in msg


async def test_a_mount_still_moving_keeps_the_window_open(fixed_env, monkeypatch):
    """The complement of the test above, and the reason the reference position
    is a dedicated field: reads that keep CHANGING must not be mistaken for
    evidence of standstill just because they succeeded."""
    fl, tel = await _cancelled_mid_slew(fixed_env, monkeypatch)
    for _ in range(4):                    # GR/GD still alternate: still moving
        await tel.get_position()
        assert tel._halting is True


async def test_an_answer_from_the_mount_closes_the_window(fixed_env, monkeypatch):
    """The cheaper evidence: the flag claims the mount may be too busy to
    answer, and a mount that just answered has disproven it."""
    fl, tel = await _connected_tel(_connect_script())
    await tel.stop()                      # the Stop button's own :Q#
    assert tel._halting is True
    fl.script["Te"] = "1"

    await tel.set_tracking(True)          # it answered

    assert tel._halting is False
    fl.script["Td"] = _silent_ack()
    with pytest.raises(DeviceError) as exc:
        await tel.set_tracking(False)
    assert "timeout waiting for ack on COM3" in str(exc.value)
    assert "told to stop" not in str(exc.value)


async def test_guide_pulses_do_not_open_a_halt_window(fixed_env):
    """Load-bearing exclusion. pulse_guide ends every pulse with a per-direction
    :Qn#/:Qw#/... — several times a minute, all night. If those counted as
    halts the flag would never be down during guiding, and every unrelated error
    for the rest of the session would carry a stop that had nothing to do with
    it. Only the whole-mount :Q# counts."""
    fl, tel = await _connected_tel(_connect_script())
    await tel.pulse_guide("n", 1)
    assert "Qn" in fl.sent and tel._halting is False
    await tel.move_axis("ra", 0.0)        # jog release: per-axis stops, same rule
    assert "Qe" in fl.sent and tel._halting is False


# --------------------------------------------------------- telescope: tracking rate

async def test_tracking_rate_sends_lx200_command_per_rate(fixed_env):
    """Each rate name maps to its classic LX200 select command, sent
    fire-and-forget (reply="none" -- see set_tracking_rate's docstring for why
    an ack is NOT assumed for :TQ#/:TL#/:TS#)."""
    fl, tel = await _connected_tel(_connect_script())
    assert tel.can_set_tracking_rate is True
    await tel.set_tracking_rate("sidereal")
    assert fl.sent == ["TQ"]
    fl.sent.clear()
    await tel.set_tracking_rate("lunar")
    assert fl.sent == ["TL"]
    fl.sent.clear()
    await tel.set_tracking_rate("solar")
    assert fl.sent == ["TS"]


async def test_get_tracking_rate_returns_cached_last_set(fixed_env):
    fl, tel = await _connected_tel(_connect_script())
    assert await tel.get_tracking_rate() == "sidereal"       # init default
    await tel.set_tracking_rate("lunar")
    assert await tel.get_tracking_rate() == "lunar"           # cache, not a re-read
    assert fl.sent == ["TL"]                                  # no read-back command sent


async def test_tracking_rate_rejects_unknown_and_sends_nothing(fixed_env):
    fl, tel = await _connected_tel(_connect_script())
    with pytest.raises(DeviceError):
        await tel.set_tracking_rate("king")
    assert fl.sent == []                                       # nothing sent
    assert await tel.get_tracking_rate() == "sidereal"          # cache untouched


# ---------------------------------------------------------- telescope: motion

async def _connected_tel(script):
    fl = FakeLink(script)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()                    # motion assertions start clean
    return fl, tel


async def test_slew_happy_path_settles(fixed_env, monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    target_ra, target_dec = 11.0, 45.0
    s = _connect_script()
    s["Sr11:00:00"] = "1"
    s["Sd+45*00:00"] = "1"
    s["MS"] = "0"
    # approach then converge: two consecutive stable polls end the settle
    s["GR"] = ["10:30:00", "10:59:00", "11:00:00", "11:00:00", "11:00:00"]
    s["GD"] = ["+60*00:00", "+46*00:00", "+45*00:00", "+45*00:00", "+45*00:00"]
    fl, tel = await _connected_tel(s)
    await tel.slew(target_ra, target_dec)
    assert fl.sent[:3] == ["Sr11:00:00", "Sd+45*00:00", "MS"]


async def test_slew_cancel_sends_stop(fixed_env, monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"; s["MS"] = "0"
    # never converges: alternate FOREVER via callables (a list would repeat its
    # last element and read as settled — the FakeLink semantics)
    n = {"ra": 0, "dec": 0}
    s["GR"] = lambda cmd: ["10:00:00", "10:30:00"][n.__setitem__("ra", n["ra"] + 1) or n["ra"] % 2]
    s["GD"] = lambda cmd: ["+10*00:00", "+20*00:00"][n.__setitem__("dec", n["dec"] + 1) or n["dec"] % 2]
    fl, tel = await _connected_tel(s)
    task = asyncio.create_task(tel.slew(11.0, 45.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Q" in fl.sent                    # halt on cancel


async def test_slew_timeout_halts_and_raises(fixed_env, monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    monkeypatch.setattr(am5, "SLEW_TIMEOUT_S", 0.05)
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"; s["MS"] = "0"
    m = {"ra": 0, "dec": 0}
    s["GR"] = lambda cmd: ["10:00:00", "10:30:00"][m.__setitem__("ra", m["ra"] + 1) or m["ra"] % 2]
    s["GD"] = lambda cmd: ["+10*00:00", "+20*00:00"][m.__setitem__("dec", m["dec"] + 1) or m["dec"] % 2]
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError, match="timeout|settle"):
        await tel.slew(11.0, 45.0)
    assert "Q" in fl.sent


async def test_slew_while_parked_is_honest(fixed_env):
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"
    s["MS"] = "e14"
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError, match="parked"):
        await tel.slew(11.0, 45.0)


async def test_sync_sets_target_then_cm(fixed_env):
    """The order on the wire: target RA, target Dec, ``:CM#``, then the
    read-back that proves it (#850). The double accepts AND moves, as the AM5
    did for every sync away from the pole on the 2026-10-08 bench."""
    fl = _Am5Model(_connect_script(), pos=(9.5, 37.0))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    await tel.sync(10.0, 40.0)
    assert fl.sent == ["Sr10:00:00", "Sd+40*00:00", "CM", "GR", "GD"]


async def test_move_axis_rate_map_and_stop(fixed_env):
    # Calibrated table (hardware 2026-07-20): R-indices are sidereal-multiple
    # presets; 0.5 deg/s lands on R7 (~60x sid = 0.25 deg/s nearest preset
    # class), fast slews on R8.
    fl, tel = await _connected_tel(_connect_script())
    await tel.move_axis("ra", 0.5)           # -> R7 band, positive ra -> Me
    assert fl.sent == ["R7", "Me"]
    fl.sent.clear()
    await tel.move_axis("ra", 0.0)           # stop both directions of the axis
    assert fl.sent == ["Qe", "Qw"]
    fl.sent.clear()
    await tel.move_axis("dec", -20.0)        # fast negative dec -> R8 + Ms
    assert fl.sent == ["R8", "Ms"]
    fl.sent.clear()
    await tel.move_axis("dec", 0.008)        # ~2x sidereal -> R3
    assert fl.sent == ["R3", "Mn"]


async def test_park_fire_and_forget_polls_gps(fixed_env, monkeypatch):
    """VERIFIED ON HARDWARE: :hP# gives NO ack; the mount reports parked via
    :Gps# ~1s later. park() sends fire-and-forget and polls."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.01)
    fl = FakeLink(_connect_script(Gps=["0", "0", "2"]))  # unparked -> parked
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.park()
    assert "hP" in fl.sent
    assert (await tel.is_parked()) is True


async def test_stop_sends_halt_first_and_only(fixed_env):
    # Emergency-stop semantics: :Q# goes out FIRST, nothing before it. Whether
    # :Q# disturbs tracking on this firmware is an at-scope runbook item; until
    # verified, stop() does not send follow-up commands.
    fl, tel = await _connected_tel(_connect_script())
    await tel.stop()
    assert fl.sent == ["Q"]


async def test_pulse_guide_direction_strategies(fixed_env):
    """Native :Mg*# is inert; :M<dir># REPLACES tracking (at-scope 2026-07-20)
    — so east suspends tracking, west drives R2+Mw, n/s use R1 moves."""
    fl, tel = await _connected_tel(_connect_script())
    await tel.pulse_guide("north", 50)
    assert fl.sent == ["R1", "Mn", "Qn"]
    fl.sent.clear()
    await tel.pulse_guide("west", 50)
    assert fl.sent == ["R2", "Mw", "Qw"]
    fl.sent.clear()
    fl.script["GAT"] = "1"                        # tracking on
    fl.script["Td"] = "1"
    fl.script["Te"] = "1"
    await tel.pulse_guide("east", 50)
    assert fl.sent == ["GAT", "Td", "Te"]         # exact-1x-sidereal drift
    fl.sent.clear()
    fl.script["GAT"] = "0"                        # tracking off -> move fallback
    await tel.pulse_guide("east", 50)
    assert fl.sent == ["GAT", "R1", "Me", "Qe"]
    assert type(tel).can_pulse_guide is True


async def test_pulse_guide_cancel_restores_state(fixed_env):
    """A cancelled pulse stops the mount AT ONCE, not when the duration it was
    asked for would have run out. The pulse thread waits on an Event precisely
    so a cancel can cut the wait short: waiting it out would leave the mount
    driving for the rest of a pulse nobody wants any more."""
    fl, tel = await _connected_tel(_connect_script())
    fl.script["GAT"] = "1"
    fl.script["Td"] = "1"
    fl.script["Te"] = "1"
    task = asyncio.create_task(tel.pulse_guide("east", 5000))
    # The suspend is on the wire, on a wall-clock deadline (#610): 400 x
    # sleep(0.005) is 2 s on Linux but 6.2 s on Windows (sleep rounds up to
    # the 15.6 ms timer there), so a round count gives the two platforms
    # different real patience.
    await wait_until(lambda: "Td" in fl.sent, timeout_s=8.0, interval_s=0.005)
    cancelled_at = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fl.sent == ["GAT", "Td", "Te"]         # finally resumed tracking
    resumed_at = next(ts for c, ts in fl.sent_at if c == "Te")
    assert resumed_at - cancelled_at < 0.3, (
        f"the mount kept moving for {resumed_at - cancelled_at:.2f}s "
        "after the cancel")


# ------------------------------------------------- backend + framework integration

from astrodeck.devices import backends as _backends  # noqa: E402,F401  (registration)
from astrodeck.devices.backend import BACKENDS  # noqa: E402
import astrodeck.devices.backends._discovery as disc  # noqa: E402


@pytest.fixture
def registered(fixed_env):
    prior = BACKENDS.get("zwo-am5")         # entry-point discovery may have
    am5.register_all()                      # already registered it — restore,
    try:                                    # never leave the worker without it
        yield BACKENDS["zwo-am5"]
    finally:
        if prior is not None:
            BACKENDS["zwo-am5"] = prior
        else:
            BACKENDS.pop("zwo-am5", None)


def test_register_all_manifest(registered):
    from astrodeck.devices.backend import list_backends
    row = next(r for r in list_backends() if r["name"] == "zwo-am5")
    assert row["transport"] == "serial"
    assert row["hardware"] is True
    assert row["driver_type"] == "zwo-am5"
    assert row["roles"] == ("telescope",)
    from astrodeck import __version__
    assert row["version"] == __version__


def test_entry_point_discovery_loads_zwo_am5(monkeypatch, fixed_env):
    class _EP:
        name = "zwo_am5"
        dist = type("D", (), {"name": "astrodeck"})()
        def load(self):
            return am5.register_all
    monkeypatch.setattr(disc.md, "entry_points", lambda group=None: [_EP()])
    prior = BACKENDS.pop("zwo-am5", None)   # force a clean discovery run
    try:
        disc.discover_plugin_backends(app_version="99.0")
        assert "zwo-am5" in BACKENDS
        assert any(r["name"] == "zwo-am5" and r["status"] == "loaded"
                   for r in disc.plugin_load_report())
    finally:
        if prior is not None:
            BACKENDS["zwo-am5"] = prior
        else:
            BACKENDS.pop("zwo-am5", None)


async def test_connect_profile_end_to_end_serial_rig(registered, monkeypatch):
    """A profile serial row connects through the orchestrator against a FakeLink,
    and the A-framework safety stamp holds (device.hardware is True)."""
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile, ProfileDevice

    monkeypatch.setattr(am5, "_make_link", lambda port: FakeLink(_connect_script()))
    p = Profile(name="serial rig", primary_backend="none", devices=[
        ProfileDevice(role="telescope", backend="zwo-am5",
                      transport="serial", port_path="COM9")])
    res = await connect_profile(p.to_rigspec())
    tel = res.rig.get("telescope")
    assert tel is not None and tel.connected
    assert tel.hardware is True
    assert tel.firmware == "1.8.8"
    for s in res.sessions.values():
        await s.close()


# ------------------------------------------------- serial driver creation (A-minor 1)

def test_add_driver_serial(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    e = config_store.add_driver("zwo-am5", transport="serial", port_path="COM9")
    assert e.id.startswith("zwo-am5-")
    assert e.transport == "serial" and e.port_path == "COM9"
    assert e.label == "ZWO-AM5 @ COM9"
    with pytest.raises(ValueError, match="port_path"):
        config_store.add_driver("zwo-am5", transport="serial")
    # unknown network type without explicit port is rejected; with one, allowed
    with pytest.raises(ValueError, match="default port"):
        config_store.add_driver("demo-net", host="h")
    ok = config_store.add_driver("demo-net", host="h", port=4321)
    assert ok.port == 4321


def test_driver_id_resolution_carries_serial_addressing(tmp_path, monkeypatch):
    """B review I1: a serial driver REFERENCED BY ID must resolve with its
    transport/port_path intact — resolve_driver_ids used to drop them."""
    from astrodeck import drivers as drv
    from astrodeck.config import config_store
    from astrodeck.devices.backend import ConnSpec, RigSpec
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    d = config_store.add_driver("zwo-am5", transport="serial", port_path="COM9")
    spec = RigSpec(primary="none", roles={
        "telescope": ConnSpec(backend="", role="telescope", driver_id=d.id)})
    resolved, role_map, prefailed = drv.resolve_driver_ids(spec)
    assert prefailed == []
    cs = resolved.roles["telescope"]
    assert cs.transport == "serial" and cs.port_path == "COM9"
    assert cs.backend == "zwo-am5" or cs.backend == d.type   # registry-mapped


def test_api_creates_serial_driver(registered, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/config/drivers",
                   json={"type": "zwo-am5", "transport": "serial",
                         "port_path": "COM9"})
        assert r.status_code == 200
        assert r.json()["driver"]["port_path"] == "COM9"
        assert c.post("/api/config/drivers",
                      json={"type": "asiair", "host": "h"}).status_code == 422
        # legacy network create unchanged
        r2 = c.post("/api/config/drivers", json={"type": "nina", "host": "h"})
        assert r2.status_code == 200 and r2.json()["driver"]["port"] == 1888


# --------------------------------------------- solver defense (A-minor 2)

async def test_simsolver_refuses_serial_mount_rig(registered, monkeypatch, tmp_path):
    """zwo-am5 mount + sim camera, no ASTAP: the SimSolver fallback must REFUSE
    to fake a plate solve (the mode denylist never knew this session name — the
    device hardware flag is the truth that gates it now)."""
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile, ProfileDevice
    import astrodeck.solve as solve_mod

    monkeypatch.setattr(am5, "_make_link", lambda port: FakeLink(_connect_script()))
    monkeypatch.setattr(solve_mod, "find_astap", lambda: None)
    p = Profile(name="mixed", primary_backend="none", devices=[
        ProfileDevice(role="telescope", backend="zwo-am5",
                      transport="serial", port_path="COM9"),
        ProfileDevice(role="camera", backend="sim")])
    res = await connect_profile(p.to_rigspec())
    assert res.rig["telescope"].hardware is True
    assert res.solver is not None
    out = await res.solver.solve(tmp_path / "frame.fits", ra_hint=10.0, dec_hint=40.0)
    assert out.success is False and "refusing" in out.message
    for s in res.sessions.values():
        await s.close()


async def test_simsolver_pure_sim_rig_still_solves(monkeypatch, tmp_path):
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile
    import astrodeck.solve as solve_mod

    monkeypatch.setattr(solve_mod, "find_astap", lambda: None)
    res = await connect_profile(Profile(name="sim").to_rigspec())
    out = await res.solver.solve(tmp_path / "frame.fits", ra_hint=10.0, dec_hint=40.0)
    assert out.success is True
    for s in res.sessions.values():
        await s.close()


async def test_discover_filters_vid_pid(registered, monkeypatch):
    class _Port:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid = device, vid, pid
    fake_ports = [_Port("COM3", 0x03C3, 0x4001), _Port("COM8", 0x1A86, 0x7523)]
    from serial.tools import list_ports
    monkeypatch.setattr(list_ports, "comports", lambda: fake_ports)
    found = await registered.discover()
    assert found == [{"role": "telescope", "name": "ZWO AM5 (USB)",
                      "port_path": "COM3", "verified": True}]


async def test_park_stops_tracking_before_commanding_it(fixed_env):
    """VERIFIED ON HARDWARE 2026-07-30: with tracking ON the AM5 accepts :hP#
    and silently does nothing — the mount never moves, never reports parked, and
    every park times out at 60s. Tracking off first and the identical park lands
    in ~15s.

    This is the ordering that stands between the sun and the optics. A night
    ALWAYS ends with the mount tracking, so "park at dawn" hit exactly this case
    and failed. The command ORDER is asserted, not just that a park happened.
    """
    # unparked, tracking on; Gps flips to parked once :hP# has been sent
    script = _connect_script(Gps="0", GAT="1")
    fl = FakeLink(script)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    # after connect, report parked as soon as the park command has gone out
    fl.script["Gps"] = lambda cmd: "2" if "hP" in fl.sent else "0"
    fl.script["GAT"] = lambda cmd: "0" if "Td" in fl.sent else "1"

    await tel.park()

    assert "hP" in fl.sent, "the park command was never sent"
    # :Td# is tracking OFF on this mount (:Te# is on) — see set_tracking.
    stop = fl.sent.index("Td") if "Td" in fl.sent else None
    assert stop is not None, (
        f"tracking was never stopped before parking (sent: {fl.sent})")
    assert stop < fl.sent.index("hP"), (
        f"tracking must stop BEFORE :hP#, got {fl.sent}")


# --------------------------------------------------------------- Home vs park
#
# ``find_home`` is park-then-unpark, because :hP# is the mount's ONE home
# command. But ``park`` is idempotent: an already-parked mount short-circuits
# and never sends :hP#. So pressing Home on a parked mount UNPARKED it and
# nothing else — no motion, no error, and the docstring above the method saying
# "go to the home position and come back usable".
#
# That is not a theoretical state on this rig. The AM5 is a harmonic drive with
# NO BRAKE: after a power cut the tube was found 50 degrees out while the mount
# still reported parked. Home is exactly the button you press then, and it was
# the one call that would do nothing.

async def test_home_unparks_BEFORE_commanding_home(fixed_env):
    """A PARKED AM5 REFUSES :hP#.

    Captured wire truth, not inference — docs/hardware/zwo-am5-lx200-protocol.md
    records the whole motion class (``:hP#`` included) answering ``e14#`` while
    parked, and ``:Spu#`` clearing all of it. Because ``:hP#`` is
    fire-and-forget there is no ack to notice the refusal by, so commanding home
    on a parked mount looks exactly like success. The first fix for the
    already-parked case did precisely that."""
    fl = FakeLink(_connect_script())                # Gps -> "2" (parked)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    fl.script["Spu"] = "1"
    fl.script["GAT"] = "0"
    # Parked until :Spu#; unparked after it; parked again once :hP# lands —
    # which is the mount's own completion signal. :hP# would be REFUSED before
    # the unpark, which is the whole reason for the ordering.
    fl.script["Gps"] = lambda cmd: (
        "2" if ("hP" in fl.sent or "Spu" not in fl.sent) else "0")

    await tel.find_home()

    assert "hP" in fl.sent, (
        "Home must command the mount even when it already reports parked — "
        f"a no-brake mount can be parked and 50 degrees from home. Sent: {fl.sent}")
    assert fl.sent.index("Spu") < fl.sent.index("hP"), (
        f"the unpark must clear the e14 refusal BEFORE :hP#, got {fl.sent}")
    assert fl.sent.count("Spu") >= 2, (
        f"and Home must still leave the mount usable afterwards, got {fl.sent}")


async def test_park_stays_idempotent(fixed_env):
    """The counterpart: park() must keep short-circuiting. Re-sending :hP# to a
    parked mount is a wasted 60 s poll on the dawn path."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    await tel.park()
    assert "hP" not in fl.sent


# ------------------------------------------- park after a stop (rig 2026-08-06)
#
# REPRODUCED ON HARDWARE. goto -> stop -> park logged "park did not complete
# within 60s (mount still reports unparked)" and left the mount UNPARKED and
# TRACKING in daylight. The identical park from an idle mount succeeded in about
# 12 s, so the defect is specifically park-issued-into-a-halt-window: :Q# is
# fire-and-forget, the axes have mass, and the ack-class :Td# that park sends
# first lands where the mount is least able to answer. That timeout was
# swallowed by a bare ``except Exception: pass``, leaving tracking ON for the
# :hP# that followed — the one state this driver already knows makes park a
# silent no-op.
#
# stop-then-park is the EMERGENCY shape: safety abort, dawn park, the sun
# watchdog, any aborted session.


def _park_script(**over):
    """Connect script for an UNPARKED, TRACKING mount that parks when asked."""
    s = _connect_script(Gps="0", GAT="1")
    s.update(over)
    return s


async def test_park_waits_out_a_halt_before_commanding_anything(fixed_env, monkeypatch):
    """The regression proper. After :Q#, park must not talk to the mount until
    the halt window has closed on EVIDENCE — two settled position reads."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.0)
    # Position moves for two reads, then settles: the mount coasting to a stop.
    positions = ["10:00:00", "10:00:30", "11:00:00", "11:00:00", "11:00:00"]
    decs = ["+40*00:00", "+41*00:00", "+45*00:00", "+45*00:00", "+45*00:00"]
    fl = FakeLink(_park_script(GR=list(positions), GD=list(decs),
                               Gps=["0", "0", "2"], Td="1", GAT=["1", "0"]))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()

    await tel.stop()                       # :Q# — opens the halt window
    assert tel._halting is True, "precondition: the halt window must be open"
    fl.sent.clear()

    await tel.park()

    # The park must come AFTER the reads that proved the mount stopped moving,
    # and after the drive was stopped. Order is the whole subject here.
    assert "hP" in fl.sent, f"the park was never sent: {fl.sent}"
    park_at = fl.sent.index("hP")
    assert fl.sent.index("Td") < park_at, \
        f"tracking must be stopped BEFORE :hP#, got {fl.sent}"
    assert fl.sent[:park_at].count("GR") >= 2, (
        "park sent :hP# without waiting for two settled position reads — this "
        f"is the 2026-08-06 failure: {fl.sent}")
    assert tel._halting is False


async def test_a_park_that_is_ignored_with_tracking_on_is_retried(fixed_env, monkeypatch):
    """The documented silent no-op: :hP# with tracking on is accepted and does
    nothing. One retry, after re-asserting tracking-off, must recover it."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.0)
    monkeypatch.setattr(am5, "PARK_WAIT_S", 0.05)
    # The mount reports parked ONLY after a second :hP#, which is precisely
    # "the first park was accepted and did nothing". Driven off the sent log
    # rather than a fixed list so the test states the behaviour it means and
    # cannot be broken by an extra poll.
    fl: FakeLink
    fl = FakeLink(_park_script(
        GAT="1",                                    # tracking never reads off
        Gps=lambda _cmd: "2" if fl.sent.count("hP") >= 2 else "0",
        Td="1"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()

    await tel.park()

    assert fl.sent.count("hP") == 2, \
        f"a park ignored with tracking on must be retried once: {fl.sent}"
    assert fl.sent.count("Td") >= 2, \
        f"the retry must re-assert tracking-off first: {fl.sent}"


async def test_a_park_that_never_lands_says_tracking_is_why(fixed_env, monkeypatch):
    """When both attempts fail AND tracking is still on, the error must name it
    — that is the difference between a diagnosable failure and a mystery."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.0)
    monkeypatch.setattr(am5, "PARK_WAIT_S", 0.05)
    fl = FakeLink(_park_script(GAT="1", Gps="0", Td="1"))   # never stops, never parks
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()

    with pytest.raises(DeviceError) as e:
        await tel.park()
    msg = str(e.value)
    assert "two attempts" in msg, msg
    assert "tracking is still on" in msg, \
        f"the error must name the cause it knows about: {msg}"


async def test_a_mount_that_will_not_talk_still_gets_its_park_attempt(fixed_env, monkeypatch):
    """The instinct in the old bare ``except`` was right and is preserved: this
    path is the last thing between the sun and the optics, so a mount that
    cannot report or stop tracking must still be SENT the park."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.0)
    monkeypatch.setattr(am5, "PARK_WAIT_S", 0.05)
    # GAT unscripted -> LinkError on every read, exactly like a silent mount.
    s = _park_script(Gps=["0", "0", "2"])
    s.pop("GAT")
    fl = FakeLink(s)
    tel = am5.ZwoAm5Telescope(fl)
    tel._connected = True                  # skip connect: it reads GAT
    await tel.park()
    assert "hP" in fl.sent, \
        f"a mount that will not answer must still be sent the park: {fl.sent}"


async def test_the_halt_drain_is_bounded_not_indefinite(fixed_env, monkeypatch):
    """A halt window that never closes must not hang the park forever — an
    unparked mount is worse than a slow one."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.0)
    monkeypatch.setattr(am5, "PARK_WAIT_S", 0.05)
    monkeypatch.setattr(am5, "HALT_DRAIN_TIMEOUT_S", 0.05)
    # Position never settles: every read is different, so _halting never clears.
    ras = [f"{h:02d}:00:00" for h in range(24)] * 20
    fl = FakeLink(_park_script(GR=ras, Gps=["0", "0", "2"], Td="1", GAT=["1", "0"]))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.stop()
    assert tel._halting is True

    await asyncio.wait_for(tel.park(), 10.0)     # must not hang
    assert "hP" in fl.sent


# ------------------------------------------------- dropped link recovery (#207/#208)
#
# THE OUTAGE THESE PIN. 2026-08-09, from captures/logs/2026-08-08.jsonl:
#
#   02:38:26 [error/mount]  serial COM3: exchange did not return — marking the
#                           link unusable (it will be reopened)
#   02:38:26 [info/safety]  sun watch held off: the mount will not report its
#                           position, so this net cannot tell whether the Sun is
#                           closing on it
#   05:50:48 [error/safety] DAWN PARK FAILED: Gps read failed: link not open
#                           ...and 138 more, once a minute, through sunrise
#
# It was never reopened. COM3 stayed enumerated and healthy the whole time —
# only our handle was gone — and a single reconnect at 08:08 fixed it instantly.

def _dropped(tel, link) -> None:
    """Put the driver in the 02:38:26 state: handshake done, port dead."""
    assert tel.connected, "precondition: the mount was connected and working"
    link.drop()


async def test_a_dropped_link_makes_the_mount_report_itself_disconnected(fixed_env):
    """#208. ``connected`` must track the TRANSPORT, not a past success.

    For five and a half hours ``/api/status`` and ``backend_links`` both said
    this mount was connected while every command failed. Worse, ``connect()``
    reads this same flag to decide whether to re-handshake — so the stale True
    was not merely a cosmetic lie, it was the thing suppressing recovery."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    _dropped(tel, fl)

    assert tel.connected is False, (
        "a mount whose port is gone is not connected, whatever happened an "
        "hour ago")
    assert tel.describe()["connected"] is False, (
        "and the status surface must say so — this is the field the UI and "
        "backend_links render")


async def test_a_command_reopens_a_dropped_link_and_succeeds(fixed_env):
    """#207. The abandon path's promise, kept.

    ``SerialLink._abandon``'s docstring said "the driver reopens rather than
    the whole mount subsystem wedging until process restart". Nothing did."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    opens_before = fl.opens
    _dropped(tel, fl)

    ra, dec = await tel.get_position()

    assert fl.opens == opens_before + 1, "the port was actually reopened"
    assert not fl.needs_reopen and tel.connected, "and the driver is live again"
    assert (round(ra, 4), round(dec, 4)) == (10.2322, 90.0), (
        "the command that triggered the reopen must still return its answer, "
        "not merely stop raising")


async def test_park_recovers_a_dropped_link(fixed_env, monkeypatch):
    """The command that actually mattered at dawn.

    A read-only recovery would leave exactly this case broken, which is the
    case that was broken: ``park`` failed 139 times in a row against a link one
    reopen would have fixed, while the Sun climbed to +20°."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.0)
    # Gps: "0" for the handshake and the pre-park check (unparked, so the park
    # is really attempted), then "2" once :hP# has landed.
    fl = FakeLink(_park_script(Gps=["0", "0", "0", "2"], Td="1", GAT=["1", "0"]))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    _dropped(tel, fl)

    await asyncio.wait_for(tel.park(), 10.0)

    assert "hP" in fl.sent, f"the park command reached the mount: {fl.sent}"


async def test_the_reopen_reasserts_the_clock_and_site(fixed_env):
    """A link drops for two indistinguishable reasons: our side stalled, or the
    MOUNT restarted. If it restarted it is back at power-up defaults, and a bare
    port reopen would leave us issuing coordinates against the wrong clock and
    site — pointing errors with no error message anywhere."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    _dropped(tel, fl)
    fl.sent.clear()

    await tel.get_position()

    assert "SC07/19/26" in fl.sent and "SL22:14:58" in fl.sent, (
        f"the reopen must re-assert the clock: {fl.sent}")
    assert "SMGE+40*00:00&+100*30:30" in fl.sent, (
        f"...and the site: {fl.sent}")


async def test_a_reopen_that_fails_is_not_retried_on_every_command(fixed_env,
                                                                   monkeypatch):
    """Rate-limited on purpose. An abandoned exchange can leave a worker thread
    holding the OS handle, and Windows refuses a second open until it lets go —
    so early attempts are EXPECTED to fail. Without a floor, every status poll
    becomes an open() on a port somebody else still owns."""
    monkeypatch.setattr(am5, "RELINK_MIN_INTERVAL_S", 3600.0)
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()

    async def _refuse():
        raise LinkError("cannot open COM3: port busy")
    fl.open = _refuse
    _dropped(tel, fl)

    for _ in range(5):
        with pytest.raises(DeviceError):
            await tel.get_position()

    assert fl.opens == 1, (
        f"one reopen attempt, then the floor holds it off: {fl.opens} attempts")
    assert tel.connected is False, "and the mount keeps saying it is down"


async def test_disconnect_does_not_reopen_a_dropped_link(fixed_env):
    """Reopening a link in order to close it is not a recovery, it is a loop."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    opens_before = fl.opens
    _dropped(tel, fl)

    await tel.disconnect()

    assert fl.opens == opens_before, "teardown must not resurrect the port"
    assert fl.closed and tel.connected is False


async def test_connect_rehandshakes_a_dropped_link_instead_of_short_circuiting(
        fixed_env):
    """The seam between #207 and #209.

    ``connect()`` returns early on ``self.connected``, and before the health
    flag was derived that early return was the bug: a dropped link kept the
    flag True, so every reconnect path in the system — the hub's, the API's,
    and the dawn-park net's — quietly did nothing and reported success. This is
    the exact call dawn park now makes at sunrise, so it has to actually
    reconnect."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    opens_before = fl.opens
    fl.drop()
    fl.sent.clear()

    await tel.connect()

    assert fl.opens == opens_before + 1, "the port was reopened, not skipped"
    assert fl.sent[:2] == ["GVP", "GV"], (
        f"and the mount was re-identified rather than assumed: {fl.sent}")
    assert tel.connected is True


async def test_goto_refused_for_slew_limits_is_said_in_words(fixed_env):
    """2026-09-06, 20:47 PDT on the rig. Auto-resume re-centred on a target at
    nine degrees altitude and the mount answered ``:MS#`` with ``e6``. All the
    driver could say was "goto rejected (reply 'e6')", and ``e6`` appears
    nowhere in this repo - the only documented code was ``e14``. The same goto
    was accepted once the target had risen, which is the evidence for calling
    it a limit."""
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"
    s["MS"] = "e6"
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError) as exc:
        await tel.slew(11.0, 45.0)
    msg = str(exc.value)
    assert "slew limits" in msg, msg
    assert "e6" in msg, f"the raw code must stay visible: {msg}"
    assert getattr(exc.value, "code", None) == "e6", (
        "a caller must be able to tell a LIMIT refusal from a link failure "
        "without matching on message text")


async def test_an_unevidenced_goto_code_still_gets_a_sentence(fixed_env):
    """Only ``e14`` and ``e6`` have been seen on the wire. Everything else gets
    the honest generic reading rather than an invented meaning."""
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"
    s["MS"] = "e3"
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError) as exc:
        await tel.slew(11.0, 45.0)
    msg = str(exc.value)
    assert "code e3" in msg, msg
    assert "limits" in msg, msg


async def test_goto_accepted_reply_is_unchanged(fixed_env, monkeypatch):
    """``0`` is still "accepted" and nothing about the settle poll moved."""
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"; s["MS"] = "0"
    s["GR"] = "11:00:00"; s["GD"] = "+45*00:00"
    fl, tel = await _connected_tel(s)
    await tel.slew(11.0, 45.0)
    assert fl.sent[:3] == ["Sr11:00:00", "Sd+45*00:00", "MS"]


# ------------------------------------------- the sync is verified, not trusted
#
# #850. On 2026-10-07 three centring syncs of 2.2 to 2.8 deg "changed nothing":
# the driver treated any ``:CM#`` reply but ``e14`` as success and never read
# the position back, and the run imaged the wrong field for hours (#852). On
# the bench on 2026-10-08 the AM5 answered ``N/A`` and moved to the synced
# coordinates away from the pole; at the home position every sync was refused:
# all but one answered ``e11`` (sync-to-self included), and one answered
# ``N/A`` and did not move. Every coordinate below is made up.

from astrodeck.devices.base import SyncRefused, SyncUnverified  # noqa: E402
import astrodeck.events as _events_mod  # noqa: E402

#: The made-up sync target every case below asks for, and its wire spelling.
_T_RA, _T_DEC = 10.0, 40.0
_T_CMDS = ["Sr10:00:00", "Sd+40*00:00", "CM"]


async def _model_tel(**kw) -> tuple[_Am5Model, am5.ZwoAm5Telescope]:
    script = kw.pop("script", None) or _connect_script()
    fl = _Am5Model(script, **kw)
    tel = am5.ZwoAm5Telescope(fl, name="Mount")
    await tel.connect()
    fl.sent.clear()
    return fl, tel


def _bus_lines(monkeypatch) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    monkeypatch.setattr(_events_mod.bus, "log",
                        lambda level, message, source="hub", **_kw:
                        lines.append((level, message)))
    return lines


@pytest.fixture
def fast_readback(monkeypatch):
    monkeypatch.setattr(am5, "SYNC_READBACK_RETRY_S", 0.0)


@pytest.mark.parametrize("reply, expect", [
    ("e11", True), ("E14", True), ("e6#", True), (" e11# ", True),
    ("e123", True),
    ("N/A", False), ("N/A#", False), ("", False), (None, False),
    ("e", False), ("e11x", False), ("1e1", False), ("1", False),
    ("Coordinates matched", False),
])
def test_is_error_reply_matches_only_the_enn_family(reply, expect):
    """The shape the driver calls a refusal. Anything else is judged by the
    read-back, never by the word (ruling 2: no "accept only N/A")."""
    assert _lx200.is_error_reply(reply) is expect, reply


async def test_an_accepted_sync_that_moves_clears_the_latch(fixed_env):
    """``N/A`` and the report moves to the target: the sync is taken, and only
    then is the reset latch cleared. The handshake read the pole (``pos`` dec
    +90), so the mount started out not knowing where it pointed."""
    fl, tel = await _model_tel(pos=(7.0, 90.0))
    assert tel.position_known is False, "precondition: the pole read latched"

    await tel.sync(_T_RA, _T_DEC)

    assert fl.sent == _T_CMDS + ["GR", "GD"], fl.sent
    assert tel.position_known is True, (
        "a sync the read-back proved must clear the latch")


async def test_e11_at_home_is_a_sync_refused_with_its_residual(fixed_env):
    """The bench: at the home position every sync was refused, all but one
    with ``e11``, and nothing moved. A refusal, with the separation between
    what was asked and
    what the mount still reports (the pole: 50 deg from Dec +40), the latch
    left alone, and the sync not re-sent."""
    fl, tel = await _model_tel(pos=(7.0, 90.0), cm_reply="e11", moves=False)

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "e11", e.code
    assert e.residual_deg is not None and abs(e.residual_deg - 50.0) < 0.01, (
        f"the residual must be the 50 deg from the pole, got {e.residual_deg}")
    assert e.reason == am5.SYNC_E11_ELSEWHERE_REASON, (
        f"50 deg off the sky must get the home-by-eye words: {e.reason!r}")
    assert tel.position_known is False, "a refused sync established nothing"
    assert fl.sent.count("CM") == 1, f"the sync was re-sent: {fl.sent}"
    assert fl.sent.count("GR") == 1, (
        f"a refusal reads back once, best effort, not with retries: {fl.sent}")


async def test_e11_refuses_even_when_the_read_back_agrees(fixed_env):
    """Sync-to-self at home answered ``e11`` on the bench. The position then
    matches the request (nothing had to move), and it is still a refusal: an
    ``eNN`` reply is never overruled by a read-back."""
    fl, tel = await _model_tel(pos=(_T_RA, _T_DEC), cm_reply="e11",
                               moves=False)

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "e11"
    assert exc.value.residual_deg is not None and exc.value.residual_deg < 0.01
    assert exc.value.reason == am5.SYNC_E11_AT_HOME_REASON, exc.value.reason


async def test_an_e11_whose_read_back_fails_is_still_a_sync_refused(fixed_env):
    """Best effort means exactly that: the refusal is what we came to report,
    and a dead read after it leaves the residual unknown, nothing more."""
    fl, tel = await _model_tel(pos=(7.0, 90.0), cm_reply="e11", moves=False)
    fl.script["GR"] = _silent_read()

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "e11" and exc.value.residual_deg is None
    assert exc.value.reason == am5.SYNC_E11_AT_HOME_REASON, (
        f"no read-back must take the Trust-first words: {exc.value.reason!r}")


async def test_an_accept_that_does_not_move_is_a_sync_refused(
        fixed_env, fast_readback):
    """The bench's one ``N/A`` at home that moved nothing, and the shape of
    2026-10-07: the reply says yes, the read-back says no. Re-read twice more
    in case the report is merely late, then refuse with the reply verbatim and
    the measured offset."""
    start = (9.5, 37.0)
    fl, tel = await _model_tel(pos=start, moves=False)
    offset = am5.coords.angular_sep_deg(_T_RA, _T_DEC, *start)

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "N/A", e.code
    assert abs(e.residual_deg - offset) < 0.01, (e.residual_deg, offset)
    assert fl.sent.count("GR") == 1 + am5.SYNC_READBACK_RETRIES, fl.sent
    assert fl.sent.count("CM") == 1


async def test_a_small_unmoved_sync_is_still_caught(fixed_env, fast_readback):
    """The 2026-10-07 syncs were 2.2 to 2.8 deg. Half a degree, the smallest
    bench offset, is ten times ``SYNC_VERIFY_DEG`` and must not pass."""
    fl, tel = await _model_tel(pos=(_T_RA, _T_DEC - 0.5), moves=False)

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert abs(exc.value.residual_deg - 0.5) < 0.01, exc.value.residual_deg


async def test_a_late_update_on_the_second_read_is_accepted(
        fixed_env, fast_readback):
    """How soon the report moves is unmeasured (the bench read at 1 s). A
    mount whose first read is stale and whose second is right took the sync."""
    fl, tel = await _model_tel(pos=(7.0, 90.0), late_reads=1)

    await tel.sync(_T_RA, _T_DEC)

    assert fl.sent.count("GR") == 2, fl.sent
    assert tel.position_known is True


async def test_the_read_back_waits_between_tries(fixed_env, monkeypatch):
    """The retries are spaced, not back to back: three reads in the same
    millisecond would not give a late report any time to arrive."""
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(am5.asyncio, "sleep", fake_sleep)
    fl, tel = await _model_tel(pos=(9.5, 37.0), moves=False)

    with pytest.raises(SyncRefused):
        await tel.sync(_T_RA, _T_DEC)

    assert slept == [am5.SYNC_READBACK_RETRY_S] * am5.SYNC_READBACK_RETRIES


@pytest.mark.parametrize("gps, words", [
    ("2", "parked"), ("0", "refused in current state"),
])
async def test_e14_is_a_sync_refused_with_the_parked_words(fixed_env, gps,
                                                           words):
    """``e14`` keeps the honest parked probe, and is now a ``SyncRefused``,
    never a bare ``DeviceError`` (ruling 3)."""
    fl, tel = await _model_tel(pos=(7.0, 90.0), cm_reply="e14", moves=False)
    fl.script["Gps"] = gps

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "e14"
    assert words in str(exc.value) and words in exc.value.reason, exc.value
    if gps != "2":
        assert "parked" not in str(exc.value)
    assert tel.position_known is False


async def test_an_unknown_error_code_is_quoted_without_a_meaning(fixed_env):
    fl, tel = await _model_tel(pos=(9.5, 37.0), cm_reply="e3", moves=False)

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "e3"
    assert "no meaning is known" in exc.value.reason, exc.value.reason


async def test_every_read_back_failing_is_unverified_not_refused(
        fixed_env, fast_readback):
    """F1.1. The sync went out and the mount said ``N/A``; every read-back
    failed, the retries included. That is ``SyncUnverified``, carrying the
    reply and fixed words: calling it a refusal would stop a target the mount
    may well have synced, and a plain ``DeviceError`` sends the hub down its
    "plate solve failed" arm. The latch stays where it was."""
    fl, tel = await _model_tel(pos=(7.0, 90.0))
    fl.script["GR"] = _silent_read()

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert not isinstance(e, SyncRefused), e
    assert e.code == "N/A" and e.residual_deg is None, (e.code, e.residual_deg)
    assert e.reason == am5.SYNC_UNVERIFIED_READBACK, e.reason
    assert fl.sent.count("GR") == 1 + am5.SYNC_READBACK_RETRIES, (
        f"a failed read is retried like a mismatch: {fl.sent}")
    assert tel.position_known is False, (
        "an unverified sync must not clear the latch")


async def test_cm_failing_twice_on_a_live_link_is_unverified(fixed_env):
    """F1.3. Nothing is known about a sync whose ``:CM#`` got no answer, so
    it is not a refusal. The target is set again and the sync sent once more;
    when that fails too it is ``SyncUnverified`` with no reply. The port stays
    open here, so ``_request`` never reopens and this case cannot tell
    ``retry=False`` from ``retry=True`` on the second ``:CM#``: the case that
    can is ``test_cm_dropping_the_link_twice_never_sends_a_third_cm``."""
    fl, tel = await _model_tel(pos=(7.0, 90.0))
    fl.script["CM"] = _silent_read()

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "" and e.reason == am5.SYNC_UNVERIFIED_LINK_DURING, e
    assert fl.sent == _T_CMDS + _T_CMDS, fl.sent
    assert tel.position_known is False


async def test_an_unparseable_read_back_is_not_a_refusal_and_quotes_nothing(
        fixed_env):
    fl, tel = await _model_tel(pos=(7.0, 90.0))
    fl.script["GD"] = "+40*0X:00"

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert "unreadable answer" in str(exc.value), exc.value
    assert "0X" not in str(exc.value) and exc.value.__cause__ is None
    assert exc.value.__context__ is None


async def test_a_non_finite_read_back_is_not_a_refusal(fixed_env, monkeypatch):
    """``angular_sep_deg`` refuses a NaN (#324) and quotes all four
    coordinates in its text; that text must not ride out on this error."""
    fl, tel = await _model_tel(pos=(9.5, 37.0))

    async def nan_position():
        return (float("nan"), 37.0)
    monkeypatch.setattr(tel, "get_position", nan_position)

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert "not finite" in str(exc.value), exc.value
    assert exc.value.__cause__ is None and "37" not in str(exc.value)
    assert exc.value.__context__ is None


async def test_an_unexpected_reply_that_moves_is_accepted_and_recorded(
        fixed_env, monkeypatch):
    """A firmware that accepts with some other word: the read-back says it
    worked, so it is taken, and the word goes in the log once so the next
    firmware's acceptance is known, when it has the safe short shape (F1.5).
    A long or punctuated reply is taken all the same, and recorded only as
    "an unrecognised reply"."""
    lines = _bus_lines(monkeypatch)
    fl, tel = await _model_tel(pos=(7.0, 90.0), cm_reply="Matched")

    await tel.sync(_T_RA, _T_DEC)

    said = [(lvl, m) for (lvl, m) in lines if "'Matched'" in m]
    assert len(said) == 1 and said[0][0] == "info", lines
    assert tel.position_known is True

    lines.clear()
    fl, tel = await _model_tel(pos=(7.0, 90.0),
                               cm_reply="Coordinates     matched.")
    await tel.sync(_T_RA, _T_DEC)
    said = [(lvl, m) for (lvl, m) in lines if "took a sync" in m]
    assert len(said) == 1 and "an unrecognised reply" in said[0][1], lines
    assert not any("matched" in m for (_l, m) in lines), lines
    assert tel.position_known is True


async def test_the_usual_n_a_writes_no_reply_line(fixed_env, monkeypatch):
    """The control: the known acceptance is not worth a line per sync."""
    lines = _bus_lines(monkeypatch)
    fl, tel = await _model_tel(pos=(9.5, 37.0))

    await tel.sync(_T_RA, _T_DEC)

    assert [m for (_l, m) in lines if "reply" in m] == [], lines


async def test_ra_wrap_is_measured_as_an_angle(fixed_env):
    """Asked for 23:59:59, the report lands at 00:00:01: two seconds of time,
    not 24 hours. A per-axis difference would call this a 360 deg refusal."""
    t_ra = 23 + 59 / 60 + 59 / 3600
    fl, tel = await _model_tel(pos=(9.5, 20.0),
                               move_to=(1 / 3600, 20.0))

    await tel.sync(t_ra, 20.0)

    assert fl.sent[:2] == ["Sr23:59:59", "Sd+20*00:00"]
    assert tel.position_known is True


async def test_near_the_pole_twelve_hours_of_ra_can_be_within_tolerance(
        fixed_env):
    """At Dec +89.99 a report 12 h away in RA is 0.015 deg away on the sky
    (across the pole). The test is the separation, never the raw RA."""
    fl, tel = await _model_tel(pos=(9.5, 37.0),
                               move_to=(15.0, 89.995))

    await tel.sync(3.0, 89.99)

    assert tel.position_known is True


async def test_no_refusal_text_carries_the_read_back_coordinates(
        fixed_env, fast_readback):
    """Ruling 8. At home the read-back is the pole and its RA follows the local
    sidereal time: both are site oracles (#140, #166). Neither the message nor
    the reason may carry them, in any spelling the codec produces; a
    separation in degrees is allowed."""
    home = (7 + 23 / 60 + 41 / 3600, 90.0)
    away = (8 + 47 / 60 + 13 / 3600, 33 + 17 / 60 + 29 / 3600)
    for pos, reply in ((home, "e11"), (home, "N/A"), (away, "N/A"),
                       (away, "e14")):
        fl, tel = await _model_tel(pos=pos, cm_reply=reply, moves=False)
        fl.script["Gps"] = "0"
        with pytest.raises(SyncRefused) as exc:
            await tel.sync(_T_RA, _T_DEC)
        ra_s, dec_s = _lx200.format_ra(pos[0]), _lx200.format_dec(pos[1])
        for text in (str(exc.value), exc.value.reason):
            for needle in (ra_s, ra_s[:5], dec_s, dec_s[:6], dec_s[1:6],
                           f"{pos[0]:.2f}", f"{pos[1]:.2f}"):
                assert needle not in text, (
                    f"{reply} at a made-up position put {needle!r} in {text!r}")


# ------------------------------------------- #850 fix round 2: the driver half
#
# Every case below was shown RED under a named mutant of the driver, run from
# a byte backup and restored by sha256 (the report lists each pairing).


def _humanizer_rewrites(text: str) -> bool:
    """True when the UI's ``humanizeLog`` (ui/src/lib/humanize.ts) would
    replace this line with its own sentence, so the operator never reads ours.
    The four rules, in its order, as lower-cased substring tests."""
    m = text.lower()
    return (("camera" in m and any(k in m for k in
                                   ("not responding", "timeout", "disconnect")))
            or ("nina" in m and any(k in m for k in ("5", "http", "error")))
            or ("plate" in m and "solve" in m)
            or ("guid" in m and "lost" in m))


def test_the_humanizer_mirror_fires_on_each_rule():
    """The helper is only worth its assertions if it can say True."""
    for line in ("camera timeout", "NINA http 500", "plate solve failed",
                 "guiding lost"):
        assert _humanizer_rewrites(line), line
    assert not _humanizer_rewrites("the mount did not confirm the sync")


#: A half-received ``:GR#`` and ``:GD#``, in the wording serial_link used
#: before this round. The driver must not repeat transport words, whatever
#: they hold. Made-up values.
_PARTIAL = {"GR": "07:23:4", "GD": "+89*59:"}


def _chain_texts(e: BaseException) -> list[str]:
    out = [str(e), repr(e), str(getattr(e, "reason", ""))]
    for link in (e.__cause__, e.__context__):
        if link is not None:
            out += [str(link), repr(link)]
    return out


@pytest.mark.parametrize("cmd", ["GR", "GD"])
async def test_a_partial_coordinate_timeout_is_unverified_and_quotes_nothing(
        fixed_env, fast_readback, monkeypatch, cmd):
    """F1.1 + F1.2. Every read-back times out part-way through a coordinate.
    The sync is ``SyncUnverified``, and the half coordinate is in none of its
    texts, its cause or its context, nor in any log line. Mutants:
    ``m_readback_interpolates_exc`` (the transport words appended to the
    message) and ``m_raise_inside_handler`` (the raise moved into the
    ``except``, which chains the transport error as ``__context__``)."""
    lines = _bus_lines(monkeypatch)
    fl, tel = await _model_tel(pos=(7.0, 90.0))
    partial = _PARTIAL[cmd]

    def _half(_cmd):
        raise LinkError(f"timeout waiting for '#' on COM3 (got "
                        f"{partial.encode()!r})")
    fl.script[cmd] = _half

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "N/A" and e.residual_deg is None
    assert e.__suppress_context__ is True, "raised without 'from None'"
    for text in _chain_texts(e) + [m for (_l, m) in lines]:
        for needle in (partial, partial[:5], "got b"):
            assert needle not in text, f"{needle!r} leaked into {text!r}"
    assert tel.position_known is False


async def test_a_transport_failure_then_a_good_read_is_taken(
        fixed_env, fast_readback):
    """F1.1. The first read goes out milliseconds after the reply, which the
    bench never tried. One timeout there followed by a read that agrees is a
    sync the mount took. Mutant ``m_readback_fail_raises_at_once`` (a failed
    read raises instead of retrying)."""
    fl, tel = await _model_tel(pos=(7.0, 90.0))
    good = fl.script["GR"]
    calls = {"n": 0}

    def _once_silent(c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LinkError("timeout waiting for '#' on COM3 (got 0 bytes)")
        return good(c)
    fl.script["GR"] = _once_silent

    await tel.sync(_T_RA, _T_DEC)

    assert fl.sent.count("GR") == 2, fl.sent
    assert tel.position_known is True


async def test_a_far_read_then_a_failed_last_read_is_unverified(
        fixed_env, fast_readback):
    """F1.1: only the LAST read decides. Two reads that disagree, then a read
    that fails: nobody knows what the mount did after that, so it is
    unverified, not refused. Mutant ``m_any_far_read_refuses`` (the verdict
    keyed on "some read disagreed" instead of the last read)."""
    fl, tel = await _model_tel(pos=(7.0, 90.0), moves=False)
    good = fl.script["GR"]
    calls = {"n": 0}

    def _third_silent(c):
        calls["n"] += 1
        if calls["n"] == 1 + am5.SYNC_READBACK_RETRIES:
            raise LinkError("timeout waiting for '#' on COM3 (got 0 bytes)")
        return good(c)
    fl.script["GR"] = _third_silent

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert not isinstance(exc.value, SyncRefused)
    assert exc.value.residual_deg is None
    assert tel.position_known is False


async def test_a_failed_read_then_a_far_last_read_is_refused(
        fixed_env, fast_readback):
    """The mirror: a failed read early, and the last read succeeds and still
    disagrees. The mount answered, and its answer says it did not move."""
    fl, tel = await _model_tel(pos=(9.5, 37.0), moves=False)
    good = fl.script["GR"]
    calls = {"n": 0}

    def _first_silent(c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LinkError("timeout waiting for '#' on COM3 (got 0 bytes)")
        return good(c)
    fl.script["GR"] = _first_silent

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "N/A" and exc.value.residual_deg > 1.0


class _LosesTargetOnDrop(_Am5Model):
    """The review's probe: ``:CM#`` drops the link, and the mount restarts and
    loses its pending target. A ``:CM#`` with no target set does not move the
    report (the mount syncs to whatever it holds, here nothing useful)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cm_calls = 0

    def _cm(self, cmd: str) -> str:
        self.cm_calls += 1
        if self.cm_calls == 1:
            self.pending = [None, None]
            self.drop()
            raise LinkError("the link was dropped after a stalled exchange")
        if None in self.pending:
            return self.cm_reply
        return super()._cm(cmd)


async def test_cm_dropping_the_link_resends_the_target_before_the_sync(
        fixed_env, fast_readback):
    """F1.3. ``:CM#`` drops the link and the mount loses its target. The
    driver must not reopen and resend a bare ``:CM#``: it sets the target
    again (the reopen happens there) and only then syncs. Mutant
    ``m_cm_auto_retry`` (``:CM#`` sent with the default ``retry=True``)."""
    fl = _LosesTargetOnDrop(_connect_script(), pos=(7.0, 90.0))
    tel = am5.ZwoAm5Telescope(fl, name="Mount")
    await tel.connect()
    handshake = list(fl.sent)
    fl.sent.clear()
    assert tel.position_known is False, "precondition: the pole read latched"

    await tel.sync(_T_RA, _T_DEC)

    assert fl.sent == _T_CMDS + handshake + _T_CMDS + ["GR", "GD"], fl.sent
    assert tel.position_known is True, "the re-sent sync was taken and proved"


async def test_cm_failing_once_on_a_live_link_is_retried_with_the_target(
        fixed_env, fast_readback):
    """The reply of the first ``:CM#`` is lost but the port is fine: target
    and sync again, then the normal verdict."""
    fl, tel = await _model_tel(pos=(9.5, 37.0))
    model_cm = fl.script["CM"]
    calls = {"n": 0}

    def _first_lost(c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LinkError("timeout waiting for '#' on COM3 (got 0 bytes)")
        return model_cm(c)
    fl.script["CM"] = _first_lost

    await tel.sync(_T_RA, _T_DEC)

    assert fl.sent == _T_CMDS + _T_CMDS + ["GR", "GD"], fl.sent
    assert tel.position_known is True


# ------------------------------------------- #850 fix round 3: the driver half
#
# Each case below was shown RED under the named mutant of the driver, run from
# a byte backup and restored by sha256.

class _DropsOnEveryCm(_Am5Model):
    """Every ``:CM#`` drops the port (``is_open`` False afterwards) and the
    mount loses its pending target, so a reopen that resent ``:CM#`` bare
    would sync a restarted mount to whatever it holds."""

    def _cm(self, cmd: str) -> str:
        self.pending = [None, None]
        self.drop()
        raise LinkError("the link was dropped after a stalled exchange")


async def test_cm_dropping_the_link_twice_never_sends_a_third_cm(
        fixed_env, fast_readback):
    """G1.1 (review D4c). The first ``:CM#`` drops the link: the target is set
    again (the reopen happens there) and ``:CM#`` goes once more. That one
    drops the link too, and the driver stops: ``SyncUnverified`` with no
    reply, and no reopen followed by a third, bare ``:CM#``. Mutant
    ``second_cm_retry_true`` (the second ``:CM#`` sent with the default
    ``retry=True``, which reopens and resends it with no target)."""
    fl = _DropsOnEveryCm(_connect_script(), pos=(7.0, 90.0))
    tel = am5.ZwoAm5Telescope(fl, name="Mount")
    await tel.connect()
    handshake = list(fl.sent)
    fl.sent.clear()

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "" and e.reason == am5.SYNC_UNVERIFIED_LINK_DURING, e
    assert fl.sent.count("CM") == 2, fl.sent
    assert fl.sent == _T_CMDS + handshake + _T_CMDS, (
        f"the wire must end on the second :CM#, with no reopen and third "
        f":CM# after it: {fl.sent}")
    assert tel.position_known is False


#: Words of the transport or of ``_cmd_ack``'s errors, none of which may ride
#: out on a sync's ``SyncUnverified``.
_TRANSPORT_WORDS = ("COM3", "timeout", "waiting", "cannot open", "port busy",
                    "dropped", "set target", "parked", "e14", "AM5")


def _resend_reopen_fails(fl: _Am5Model) -> None:
    """``:CM#`` drops the port, and the port then will not reopen."""
    def _cm(_cmd):
        fl.drop()
        raise LinkError("the link was dropped after a stalled exchange")

    async def _refuse():
        raise LinkError("cannot open COM3: port busy")
    fl.script["CM"] = _cm
    fl.open = _refuse


def _resend_target(second):
    """``:CM#`` gets no answer on a live port, and the RE-SENT ``:Sr#`` then
    gets ``second`` (a reply, or a callable that raises)."""
    def _arrange(fl: _Am5Model) -> None:
        calls = {"n": 0}

        def _sr(cmd):
            calls["n"] += 1
            if calls["n"] == 1:
                return "1"
            return second(cmd) if callable(second) else second
        fl.script["Sr10:00:00"] = _sr
        fl.script["CM"] = _silent_read()
    return _arrange


@pytest.mark.parametrize("arrange", [
    _resend_reopen_fails,
    _resend_target(_silent_ack()),
    _resend_target("e14"),
], ids=["reopen_fails", "resent_sr_times_out", "resent_sr_answers_e14"])
async def test_the_resent_target_failing_is_unverified_in_fixed_words(
        fixed_env, arrange):
    """G1.2 (review D18). ``:CM#`` fails on the link, and then setting the
    target again fails: the port will not reopen, the re-sent ``:Sr#`` times
    out, or it answers ``e14``. ``_set_target`` reports every one of those as
    a ``DeviceError`` (through ``_cmd_ack``), carrying the port name or the
    parked words. ``sync`` must still raise ``SyncUnverified`` in fixed words,
    with nothing chained, and send no second ``:CM#``. Mutant
    ``resend_except_linkerror_only`` (``except (LinkError, DeviceError)``
    narrowed to ``except LinkError``, so a plain ``DeviceError`` escapes)."""
    fl, tel = await _model_tel(pos=(9.5, 37.0))
    arrange(fl)

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert type(e) is SyncUnverified, type(e)
    assert e.code == "" and e.reason == am5.SYNC_UNVERIFIED_LINK_DURING, e
    assert fl.sent.count("CM") == 1, fl.sent
    assert e.__context__ is None and e.__cause__ is None, (
        e.__context__, e.__cause__)
    for text in _chain_texts(e):
        for word in _TRANSPORT_WORDS:
            assert word not in text, f"{word!r} leaked into {text!r}"


@pytest.mark.parametrize("gr_answers", [True, False])
async def test_an_enn_on_the_target_is_read_back_once_for_its_residual(
        fixed_env, gr_answers):
    """G1.3. An ``eNN`` answer to the first ``:Sr#`` takes the same one
    best-effort read-back as an ``eNN`` answer to ``:CM#``, so the refusal
    says how far off the mount is, and the resume ladder is not told the
    position "could not be read back" when nobody tried. A read that fails
    leaves the residual unknown and the refusal intact. Mutant
    ``target_enn_residual_none`` (``_sync_refused(ack, None)`` again)."""
    start = (9.5, 37.0)
    fl, tel = await _model_tel(pos=start)
    fl.script["Sr10:00:00"] = "e14"
    fl.script["Gps"] = "0"
    if not gr_answers:
        fl.script["GR"] = _silent_read()

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "e14", e.code
    assert "CM" not in fl.sent, fl.sent
    assert fl.sent.count("GR") == 1, (
        f"one read, best effort, no retries: {fl.sent}")
    if gr_answers:
        offset = am5.coords.angular_sep_deg(_T_RA, _T_DEC, *start)
        assert e.residual_deg is not None, "the read-back was not taken"
        assert abs(e.residual_deg - offset) < 0.01, (e.residual_deg, offset)
        assert "deg from the synced coordinates" in str(e), e
    else:
        assert e.residual_deg is None, e.residual_deg
        assert e.__context__ is None, e.__context__


@pytest.mark.parametrize("moves", [True, False])
async def test_a_digit_run_cm_reply_is_never_quoted(
        fixed_env, fast_readback, monkeypatch, moves):
    """G1.5. ``072341`` fits the old ``[A-Za-z0-9/]{1,8}`` shape, but it is a
    ``:GR#`` answer with its separators lost, which at home is the local
    sidereal time. ``base.quotable_sync_reply`` refuses any run of three or
    more digits, so it is never quoted: not in the code, the message, the
    reason or the info line. Mutant ``reply_without_digit_run_rule``
    (``_sync_reply`` back on the bare shape test)."""
    lines = _bus_lines(monkeypatch)
    fl, tel = await _model_tel(pos=(9.5, 37.0), cm_reply="072341",
                               moves=moves)
    if moves:
        await tel.sync(_T_RA, _T_DEC)
        texts = [m for (_l, m) in lines]
        assert any("an unrecognised reply" in m for m in texts), texts
    else:
        with pytest.raises(SyncRefused) as exc:
            await tel.sync(_T_RA, _T_DEC)
        assert exc.value.code == "unrecognised", exc.value.code
        assert "an unrecognised reply" in str(exc.value), exc.value
        texts = [m for (_l, m) in lines] + _chain_texts(exc.value)
    for text in texts:
        for needle in ("072341", "0723", "2341"):
            assert needle not in text, f"{needle!r} in {text!r}"


@pytest.mark.parametrize("gps, words", [
    ("2", "mount is parked; unpark first"),
    ("0", "refused in current state"),
])
async def test_e14_on_the_target_is_a_sync_refused(fixed_env, gps, words):
    """F1.3. The first ``:Sr#`` answers ``e14``: a refusal, code ``e14``,
    with the same parked probe as the sync's own e14, and no ``:CM#`` sent.
    Mutant ``m_target_errors_plain`` (``sync`` back on ``_set_target``,
    whose e14 is a plain ``DeviceError``)."""
    fl, tel = await _model_tel(pos=(9.5, 37.0))
    fl.script["Sr10:00:00"] = "e14"
    fl.script["Gps"] = gps

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "e14"
    assert exc.value.reason.startswith(words.split(";")[0]), exc.value.reason
    assert "CM" not in fl.sent, fl.sent


async def test_a_link_failure_on_the_target_is_unverified(fixed_env):
    """F1.3. The link fails while the target is set: the sync was never sent,
    and nothing is known. Same mutant as above."""
    fl, tel = await _model_tel(pos=(9.5, 37.0))
    fl.script["Sr10:00:00"] = _silent_ack()

    with pytest.raises(SyncUnverified) as exc:
        await tel.sync(_T_RA, _T_DEC)

    e = exc.value
    assert e.code == "" and e.reason == am5.SYNC_UNVERIFIED_LINK_BEFORE, e
    assert e.__suppress_context__ is True and e.__context__ is None
    assert "COM3" not in str(e), str(e)
    assert "CM" not in fl.sent, fl.sent


async def test_a_rejected_target_is_a_sync_refused(fixed_env):
    """Neither ``1`` nor ``eNN``: the mount would not take the target, and
    ``sync`` still raises one of its two types, never a plain error."""
    fl, tel = await _model_tel(pos=(9.5, 37.0))
    fl.script["Sd+40*00:00"] = "0"

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "0" and "CM" not in fl.sent


@pytest.mark.parametrize("moves", [True, False])
async def test_a_gr_shaped_cm_reply_is_never_quoted(
        fixed_env, fast_readback, monkeypatch, moves):
    """F1.5. A desynchronised link hands ``:CM#`` a ``:GR#``-shaped answer,
    which at home is the local sidereal time. It is never quoted: not in the
    code, the message, the reason or the info line. Mutant
    ``m_reply_unsanitised`` (``_sync_reply`` returns the reply verbatim)."""
    lines = _bus_lines(monkeypatch)
    fl, tel = await _model_tel(pos=(9.5, 37.0), cm_reply="07:23:41",
                               moves=moves)
    texts = [m for (_l, m) in lines]
    if moves:
        await tel.sync(_T_RA, _T_DEC)
        texts = [m for (_l, m) in lines]
        assert any("an unrecognised reply" in m for m in texts), texts
    else:
        with pytest.raises(SyncRefused) as exc:
            await tel.sync(_T_RA, _T_DEC)
        assert exc.value.code == "unrecognised", exc.value.code
        assert "an unrecognised reply" in str(exc.value), exc.value
        texts = [m for (_l, m) in lines] + _chain_texts(exc.value)
    for text in texts:
        for needle in ("07:23:41", "07:23", "23:41"):
            assert needle not in text, f"{needle!r} in {text!r}"


@pytest.mark.parametrize("raw, code, words", [
    ("N/A", "N/A", "reply 'N/A'"),
    ("e11", "e11", "reply 'e11'"),
    ("", "", "an empty reply"),
    ("07:23:41", "unrecognised", "an unrecognised reply"),
    ("072341", "unrecognised", "an unrecognised reply"),
    ("e123", "unrecognised", "an unrecognised reply"),
    ("abcdefghi", "unrecognised", "an unrecognised reply"),
    ("N/A x", "unrecognised", "an unrecognised reply"),
])
def test_the_reply_is_quoted_only_in_its_safe_shape(raw, code, words):
    assert am5._sync_reply(raw) == (code, words)


async def test_an_empty_reply_that_does_not_move_is_refused_with_no_code(
        fixed_env, fast_readback):
    fl, tel = await _model_tel(pos=(9.5, 37.0), cm_reply="", moves=False)

    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)

    assert exc.value.code == "" and "an empty reply" in str(exc.value)


#: The e11 texts must never tell the operator to slew or go anywhere (#850
#: round 5): a goto is aimed from the position the mount believes, the very
#: thing an e11 puts in doubt. The UI's GOTO_ADVICE list
#: (w16MountPositionUnknown.test.tsx) plus "head to".
_E11_GOTO_WORDS = ("slew", "go to", "go-to", "goto",
                   "target away from the pole", "head to")
_E11_TEXTS = (am5.SYNC_E11_AT_HOME_REASON, am5.SYNC_E11_ELSEWHERE_REASON)


@pytest.mark.parametrize("residual, expected", [
    (None, "at_home"),
    (0.0, "at_home"),
    (4.99, "at_home"),
    (5.0, "at_home"),          # the threshold itself is still "near"
    (5.01, "elsewhere"),
    (10.0, "elsewhere"),
    (50.0, "elsewhere"),
])
async def test_the_e11_words_are_chosen_from_the_read_back(
        fixed_env, residual, expected):
    """J1.1. With the mount's opinion within ``SYNC_E11_ELSEWHERE_DEG`` (5.0)
    of the sky, or unread, the tube may be at home, so Trust position comes
    first behind its condition. Further out the tube is not where the mount
    thinks, so it comes home by eye first. Mutants ``mj1_threshold_ge`` (``>``
    made ``>=``, 5.0 takes the far words), ``mj2_never_elsewhere`` (always the
    near words), ``mj3_none_is_elsewhere`` (an unread residual takes the far
    words) and ``mj6_threshold_fifty`` (the constant 5.0 made 50.0)."""
    assert am5.SYNC_E11_ELSEWHERE_DEG == 5.0
    tel = am5.ZwoAm5Telescope(FakeLink(_connect_script()))
    e = await tel._sync_refused("e11", residual)
    want = (am5.SYNC_E11_AT_HOME_REASON if expected == "at_home"
            else am5.SYNC_E11_ELSEWHERE_REASON)
    assert e.reason == want, (residual, e.reason)
    assert e.code == "e11" and e.residual_deg == residual


def test_each_e11_text_fits_and_carries_no_digit():
    """J1.1. At most 90 characters each, so the hub's refused line still fits
    in 140, and no digit (the reply is quoted beside them, and a figure in a
    hold reason breaks the #618 contract). Mutant ``mj5_figure_in_words``
    (the far words say "more than 5 deg")."""
    for text in _E11_TEXTS:
        assert len(text) <= 90, (len(text), text)
        assert not any(ch.isdigit() for ch in text), text
        assert not _humanizer_rewrites(text), text


def test_no_e11_text_advises_a_goto():
    """J1.1. Both texts give the safe order: Trust position, and home by eye
    with a pad key when the tube is elsewhere. Neither says the tube IS at
    home. Mutant ``mj4_round4_words`` (the round-4 "slew away from the pole
    or use Trust position" reason restored as the near words)."""
    for text in _E11_TEXTS:
        low = text.lower()
        for banned in _E11_GOTO_WORDS:
            assert banned not in low, (
                f"the e11 words advise a goto ({banned!r}): {text!r}")
        assert "trust position" in low, text
        assert "e11 was seen there" not in low, (
            f"the words invite reading e11 as proof of home: {text!r}")
    near, far = (t.lower() for t in _E11_TEXTS)
    assert near.startswith("if the tube really is at home"), near
    assert near.index("trust position") < near.index("away from the pole"), (
        f"Trust position must come before the sync away from the pole: "
        f"{near!r}")
    assert "pad key" in far and "home by eye" in far, far
    assert far.index("home by eye") < far.index("trust position"), (
        f"home by eye must come before Trust position: {far!r}")


def test_both_e11_texts_are_pinned_exactly():
    """The two e11 texts reach the operator whenever the mount refuses a sync
    at home, so any rewording must come through this test, the way w15 pins
    the two position-unknown warnings. A ban list alone let a goto paraphrase
    through (round-5 verifier mutants v8: "if not, aim away from the pole";
    v8b: "aim at a star, or home by eye ..."), so the words are pinned, and
    "aim" and "star" are banned as whole words besides."""
    assert am5.SYNC_E11_AT_HOME_REASON == (
        "if the tube really is at home, use Trust position; "
        "a sync away from the pole then works")
    assert am5.SYNC_E11_ELSEWHERE_REASON == (
        "tube not where the mount thinks: bring it home "
        "by eye with a pad key, then Trust position")
    for text in _E11_TEXTS:
        assert not re.search(r"\b(aim|aimed|aiming|star|stars)\b",
                             text.lower()), (
            f"the e11 words point the tube at something: {text!r}")


@pytest.mark.parametrize("pos, reason", [
    ((_T_RA, _T_DEC + 1.0), "SYNC_E11_AT_HOME_REASON"),
    ((7.0, 90.0), "SYNC_E11_ELSEWHERE_REASON"),
])
async def test_the_e11_words_lead_the_route_refusal_inside_the_cut(
        fixed_env, pos, reason):
    """J1.1. The Solve & Sync route toasts ``"sync not taken: " + str(e)``,
    and the UI cuts a long line to 137 characters. With the backend's default
    name both e11 texts end inside it. Mutant ``mj7_reason_after_where``
    (the reason moved behind the reply and the residual in the message)."""
    want = getattr(am5, reason)
    fl = _Am5Model(_connect_script(), pos=pos, cm_reply="e11", moves=False)
    tel = am5.ZwoAm5Telescope(fl)            # the backend's default name
    assert tel.name == "ZWO AM5"
    await tel.connect()
    with pytest.raises(SyncRefused) as exc:
        await tel.sync(_T_RA, _T_DEC)
    assert exc.value.reason == want, exc.value.reason
    toast = "sync not taken: " + str(exc.value)
    at = toast.find(want)
    assert at >= 0, toast
    assert at + len(want) <= 137, (
        f"the e11 words end at char {at + len(want)}, past the cut: "
        f"{toast[:137]!r}")
    assert "reply 'e11'" in toast, toast     # G1.4, review D5d


async def test_no_surfaced_driver_text_trips_the_humanizer(
        fixed_env, fast_readback, monkeypatch):
    """Rule 2: every line and error text the sync path can produce, read by
    the four humanizer rules, plus the two reset-latch warnings (F1.6).
    Mutant ``m_plate_solve_words`` (the latch warning back to "a plate-solve
    sync")."""
    lines = _bus_lines(monkeypatch)
    texts: list[str] = [am5.SYNC_E11_AT_HOME_REASON,
                        am5.SYNC_E11_ELSEWHERE_REASON,
                        am5.SYNC_UNVERIFIED_READBACK,
                        am5.SYNC_UNVERIFIED_LINK_DURING,
                        am5.SYNC_UNVERIFIED_LINK_BEFORE]

    async def _raised(**kw) -> None:
        script = kw.pop("script_over", {})
        fl, tel = await _model_tel(**kw)
        fl.script.update(script)
        try:
            await tel.sync(_T_RA, _T_DEC)
        except (SyncRefused, SyncUnverified) as e:
            texts.extend([str(e), e.reason])

    home = (7.0, 90.0)
    await _raised(pos=home, cm_reply="e11", moves=False)
    await _raised(pos=home, cm_reply="e14", moves=False,
                  script_over={"Gps": "2"})
    await _raised(pos=home, cm_reply="e14", moves=False,
                  script_over={"Gps": "0"})
    await _raised(pos=home, cm_reply="e3", moves=False)
    await _raised(pos=home, cm_reply="N/A", moves=False)
    await _raised(pos=home, cm_reply="07:23:41", moves=False)
    await _raised(pos=home, cm_reply="Matched")
    await _raised(pos=home, script_over={"GR": _silent_read()})
    await _raised(pos=home, script_over={"GD": "+40*0X:00"})
    await _raised(pos=home, script_over={"CM": _silent_read()})
    await _raised(pos=home, script_over={"Sr10:00:00": _silent_ack()})
    await _raised(pos=home, script_over={"Sr10:00:00": "e14", "Gps": "2"})
    await _raised(pos=home, script_over={"Sd+40*00:00": "0"})
    # the two reset-latch warnings: a reopen at the pole, and Home on it
    fl, tel = await _model_tel(pos=home, script=_connect_script(Gps="0"))
    fl.script["hP"] = ""

    async def _no_park() -> None:
        return None
    monkeypatch.setattr(tel, "_park_now", _no_park)
    await tel.find_home()

    texts += [m for (_l, m) in lines]
    assert any("solved frame" in t for t in texts), (
        "the latch warnings were not produced, so they were not checked")
    assert len(texts) > 25, len(texts)
    for t in texts:
        assert not _humanizer_rewrites(t), f"the UI would replace: {t!r}"
