# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#863: a mount's reply bytes never reach an error text.

At the home position the AM5 points at the pole, so a ``:GR#`` reply follows
local sidereal time and a ``:GD#`` or ``:GA#`` reply is the site latitude. A
half-received or garbled reply is therefore a site oracle (#140, #166) of the
kind a key-name filter cannot withhold: the driver computes and prints it
itself, in an exception text that reaches log lines, hold reasons and the
operator's screen.

``SerialLink``'s timeout text was the first case (#850 made it a count). These
cases grade the rest of the class end to end, on what a person reading the
logs would see: the ``DeviceError`` text AND every exception chained behind it,
the way a traceback prints them. The tell is a made-up reply that looks like a
coordinate; the driver may say how many bytes arrived and which command, never
the bytes. A short reply code (``0``, ``e6``) is still quoted, because that
code is the only thing a firmware can be searched for (see
``base.quotable_sync_reply``). Every value below is made up.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.devices.base import DeviceError, GotoRefused
from astrodeck.devices.serial_link import SerialLink

from test_serial_link_sync import FakeSerial
from test_zwo_am5 import FIXED_UTC, FakeLink, _connect_script  # rootdir-relative


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


def _everything_said(exc: BaseException) -> str:
    """The exception's text plus every exception a traceback would print
    behind it (``__cause__``, else ``__context__`` unless suppressed)."""
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        if cur.__cause__ is not None:
            cur = cur.__cause__
        elif cur.__suppress_context__:
            cur = None
        else:
            cur = cur.__context__
    return "\n".join(parts)


async def _connected_tel(script, cls=FakeLink):
    fl = cls(script)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()
    return fl, tel


# ------------------------------------------- the transport, through the driver

class _QuickLink(SerialLink):
    """A real ``SerialLink`` with a short read deadline, so a timed-out read
    costs a third of a second rather than the driver's 1.5."""

    async def request(self, cmd, *, reply="hash", timeout=1.5):
        return await super().request(cmd, reply=reply, timeout=0.3)


async def test_a_timed_out_position_read_reports_a_count_through_the_driver():
    """The issue's own case, end to end: the mount sends half of a ``:GR#``
    reply and goes quiet. ``ZwoAm5Telescope._get`` wraps the transport's
    timeout text unchanged, so whatever ``SerialLink`` puts in it is what the
    settle poll, ``get_position`` and every caller then log."""
    ser = FakeSerial()
    ser.arm(b"07:23:4")                 # no '#': the reply never finished
    link = _QuickLink("COM-TEST")
    link._ser = ser
    tel = am5.ZwoAm5Telescope(link)

    with pytest.raises(DeviceError) as exc:
        await tel.get_position()

    said = _everything_said(exc.value)
    assert "GR read failed" in said, said
    assert "7 bytes" in said, said
    for needle in ("07:23:4", "07:23", "7:23", "23:4", "b'0"):
        assert needle not in said, f"{needle!r} in {said!r}"


# --------------------------------------------------- an unparseable position

@pytest.mark.parametrize("raw_ra, raw_dec, needles", [
    # RA garbled: the Dec read beside it was fine and is not echoed either.
    ("07:2x:41", "+90*00:00", ("07:2", "2x", "x:41", "+90", "90*00", "0:00")),
    ("07:23:41", "+9x*00:00", ("07:23", "23:41", "+9x", "9x*", "0:00")),
    ("", "+90*00:00", ("+90", "90*00", "0:00")),
])
async def test_an_unparseable_position_names_sizes_not_the_replies(
        fixed_env, raw_ra, raw_dec, needles):
    """``get_position`` used to raise ``unparseable position reply
    (RA='07:2x:41' Dec='+90*00:00')``: both raw replies, one of them a perfectly
    good read of the pole. It says which commands and how long their replies
    were, and keeps the parser's own words (which quote the offending field)
    off the exception chain too."""
    fl, tel = await _connected_tel(_connect_script(GR=raw_ra, GD=raw_dec))

    with pytest.raises(DeviceError) as exc:
        await tel.get_position()

    said = _everything_said(exc.value)
    assert "unparseable position reply" in said, said
    assert f":GR# gave {len(raw_ra)} bytes" in said, said
    assert f":GD# gave {len(raw_dec)} bytes" in said, said
    for needle in needles:
        assert needle not in said, f"{needle!r} in {said!r}"


# ---------------------------------------------------- an ack-class rejection

@pytest.mark.parametrize("reply, quoted, words", [
    ("0", "reply '0'", None),            # the 2026-09-06 incident's own code
    ("e6", "reply 'e6'", None),
    ("07:23:41", None, "an unrecognised reply of 8 bytes"),
    ("+12*34:56", None, "an unrecognised reply of 9 bytes"),
    ("", None, "an empty reply"),
])
async def test_an_ack_rejection_quotes_a_code_and_nothing_else(
        fixed_env, reply, quoted, words):
    """``_cmd_ack`` raised ``tracking on rejected (reply {reply!r})`` for ANY
    reply. A code is quoted; anything with the shape of a coordinate is
    reported by size."""
    fl, tel = await _connected_tel(_connect_script(Te=reply))

    with pytest.raises(DeviceError) as exc:
        await tel.set_tracking(True)

    said = _everything_said(exc.value)
    assert "tracking on rejected" in said, said
    if quoted is not None:
        assert quoted in said, said
    else:
        assert words in said, said
        for needle in (reply, reply[:5], "07:23", "+12*"):
            if needle:
                assert needle not in said, f"{needle!r} in {said!r}"


# --------------------------------------------------------- a refused goto

async def test_a_goto_refused_with_an_odd_reply_names_a_size_not_the_reply(
        fixed_env):
    """The message, the ``code`` a caller can read and the ``reason`` a hold
    reason is built from all stay clear of a coordinate-shaped reply."""
    s = _connect_script()
    s["Sr11:00:00"] = "1"
    s["Sd+45*00:00"] = "1"
    s["MS"] = "07:23:41"
    fl, tel = await _connected_tel(s)

    with pytest.raises(GotoRefused) as exc:
        await tel.slew(11.0, 45.0)

    said = _everything_said(exc.value)
    assert "goto rejected" in said, said
    assert "an unrecognised reply of 8 bytes" in said, said
    for text in (said, exc.value.code, exc.value.reason):
        for needle in ("07:23", "23:41", "7:23:41"):
            assert needle not in text, f"{needle!r} in {text!r}"


async def test_a_goto_refused_with_a_known_code_still_names_the_code(fixed_env):
    """The other side of the same rule (the 2026-09-06 ``e6``)."""
    s = _connect_script()
    s["Sr11:00:00"] = "1"
    s["Sd+45*00:00"] = "1"
    s["MS"] = "e6"
    fl, tel = await _connected_tel(s)

    with pytest.raises(GotoRefused) as exc:
        await tel.slew(11.0, 45.0)

    assert "reply 'e6'" in str(exc.value)
    assert exc.value.code == "e6"


# ------------------------------------------------ a device that is not an AM5

@pytest.mark.parametrize("ident, quoted, words", [
    ("Proto", "reply 'Proto'", None),
    ("NotAMount 07:23:41", None, "an unrecognised reply of 18 bytes"),
])
async def test_the_identity_check_does_not_echo_what_the_port_said(
        fixed_env, ident, quoted, words):
    """``GVP=<whatever answered>`` was in the connect error, and the connect
    error is what the reopen path logs. A short token is quoted so the
    operator can tell what is on the port; anything longer or coordinate-shaped
    is reported by size."""
    fl = FakeLink(_connect_script(GVP=ident))
    tel = am5.ZwoAm5Telescope(fl)

    with pytest.raises(DeviceError) as exc:
        await tel.connect()

    said = _everything_said(exc.value)
    assert "not an AM5" in said and ":GVP#" in said, said
    if quoted is not None:
        assert quoted in said, said
    else:
        assert words in said, said
        for needle in ("NotAMount", "07:23", "23:41"):
            assert needle not in said, f"{needle!r} in {said!r}"


# ------------------------------------------------------- the pulse thread

class _PulseAnswers(FakeLink):
    """The pulse thread's door (``request_sync``) answers the named ack-class
    commands with a chosen reply and everything else with ``1``."""

    def __init__(self, script, answers):
        super().__init__(script)
        self.answers = answers

    def request_sync(self, cmd, *, reply="hash", timeout=1.5):
        self.sent.append(cmd)
        self.sync_sent.append(cmd)
        if reply != "ack":
            return None
        return self.answers.get(cmd, "1")


@pytest.mark.parametrize("answers, command", [
    ({"Td": "07:23:41"}, "pulse east (suspend tracking)"),     # start refused
    ({"Te": "07:23:41"}, "pulse east (resume tracking)"),      # stop refused
])
async def test_a_refused_pulse_command_names_a_size_not_the_reply(
        fixed_env, bus_lines, answers, command):
    """Both the exception and the warning the coroutine logs for a stop the
    mount answered with a refusal."""
    fl, tel = await _connected_tel(_connect_script(GAT="1"),
                                   cls=lambda script: _PulseAnswers(script,
                                                                    answers))

    with pytest.raises(DeviceError) as exc:
        await asyncio.wait_for(tel.pulse_guide("east", 50), timeout=10)

    said = _everything_said(exc.value)
    assert f"{command} rejected" in said, said
    assert "an unrecognised reply of 8 bytes" in said, said
    logged = "\n".join(m for _lvl, m, _src in bus_lines)
    for text in (said, logged):
        for needle in ("07:23", "23:41"):
            assert needle not in text, f"{needle!r} in {text!r}"
