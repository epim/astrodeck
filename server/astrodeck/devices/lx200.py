# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""LX200 ASCII codec for the ZWO AM5 family (pure, no I/O).

Wire truth from the captured session (docs/hardware/zwo-am5-lx200-protocol.md):
commands are ``:CMD#``; replies are ``#``-terminated strings EXCEPT the bare
one-byte ack ``1`` (set/unpark commands) and the fire-and-forget motion class
(no reply at all). ``*`` (0x2A) is the degree separator. Init commands take NO
space (``:SC07/19/26#``). ``:SMGE`` longitude is W-positive — AstroDeck stores
East-positive, so the sign is negated here. ``e14#`` is the mount's
"refused in current state" reply (parked).

``:CM#`` (sync) answers ``N/A#`` when the AM5 takes it (bench, 2026-10-08,
fw 1.8.8: every accepted sync away from the pole, 0.5 to 5 deg offsets). At
the HOME position (tube at the pole) it answered ``e11#`` and took nothing,
sync-to-self included. NEITHER REPLY IS PROOF: once, at home, a 5 deg sync
answered ``N/A`` and the reported position did not move, so the driver reads
the position back after every sync and that read-back is the test (#850).
``is_error_reply`` below only recognises the ``eNN`` refusal family; it
assigns no meaning to any code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

#: The mount's success ack for set/unpark commands — a bare byte, no '#'.
ACK_OK = "1"
#: The refusal reply (stripped of '#'): motion/config refused in current state.
REFUSED = "e14"
#: The AM5's ``:CM#`` acceptance (stripped of '#'), seen on every accepted sync
#: on the bench 2026-10-08. A HINT, not a proof: see the module docstring.
SYNC_ACCEPTED = "N/A"

_ERROR_REPLY = re.compile(r"[eE]\d+")


def is_error_reply(reply: str | None) -> bool:
    """True for the mount's ``eNN`` refusal family (``e11``, ``E14#``, ...).

    Pure shape match on the reply stripped of ``#`` and whitespace. It says
    "the mount said no", nothing about why: ZWO publishes no e-code table, and
    the meanings this project has are the few seen on the wire (see
    docs/hardware/zwo-am5-lx200-protocol.md). Everything that is NOT this shape
    (``N/A``, an empty reply, some other firmware's words) is NOT a refusal by
    this test, and a caller that needs to know whether the command worked must
    measure it (``ZwoAm5Telescope.sync`` reads the position back, #850)."""
    return _ERROR_REPLY.fullmatch(
        (reply or "").strip().rstrip("#").strip()) is not None


def build(cmd: str) -> bytes:
    """Frame ``cmd`` for the wire: ``"GR"`` -> ``b":GR#"``."""
    return f":{cmd}#".encode("ascii")


def _strip(s: str) -> str:
    return s.rstrip("#").strip()


def parse_ra(s: str) -> float:
    """``"HH:MM:SS#"`` -> hours (float)."""
    h, m, sec = _strip(s).split(":")
    return int(h) + int(m) / 60.0 + float(sec) / 3600.0


def parse_dec(s: str) -> float:
    """``"sDD*MM:SS#"`` -> signed degrees. Also parses latitude / guide-rate
    strings (same shape)."""
    t = _strip(s)
    sign = -1.0 if t.startswith("-") else 1.0
    t = t.lstrip("+-")
    d, rest = t.split("*")
    m, sec = rest.split(":")
    return sign * (int(d) + int(m) / 60.0 + float(sec) / 3600.0)


def _sex(value: float) -> tuple[int, int, int]:
    """|value| -> (D, M, S) with carry so 59.9999s rounds to the next minute."""
    total = round(abs(value) * 3600)
    d, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return int(d), int(m), int(s)


def format_ra(hours: float) -> str:
    """Hours -> ``HH:MM:SS`` (no frame), normalized to the cyclic range [0,24).

    RA is cyclic, so ``25.5`` and ``-1.0`` are legal inputs the mount's ``:Sr#``
    parser is NOT willing to see spelled ``25:30:00`` / a dropped sign. Wrapping
    happens on the INTEGER SECONDS count so a 59.9999s carry (``23.999999`` ->
    86400s) collapses back to ``00:00:00`` instead of emitting ``24:00:00``.
    (``_sex`` stays sign-magnitude for ``format_dec`` — declination is NOT
    cyclic and must keep its sign, never wrap.)"""
    total = round((hours % 24.0) * 3600) % 86400
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_dec(deg: float) -> str:
    """Signed degrees -> ``sDD*MM:SS``."""
    sign = "-" if deg < 0 else "+"
    d, m, s = _sex(deg)
    return f"{sign}{d:02d}*{m:02d}:{s:02d}"


def format_lon_wpos(lon_east_deg: float) -> str:
    """East-positive longitude -> the mount's W-positive ``sDDD*MM:SS``.

    Degrees are 3-digit (``DDD``) per the LX200 longitude field and the captured
    example (``+100*…``) — a 2-digit form for |lon|<100° is unverified against
    the AM5 parser and might fail site-init (B review I2)."""
    w = -lon_east_deg
    sign = "-" if w < 0 else "+"
    d, m, s = _sex(w)
    return f"{sign}{d:03d}*{m:02d}:{s:02d}"


def smge(lat_deg: float, lon_east_deg: float) -> str:
    """The combined geo-set command body (captured form):
    ``SMGE{sDD*MM:SS}&{sDDD*MM:SS}`` with W-positive longitude."""
    return f"SMGE{format_dec(lat_deg)}&{format_lon_wpos(lon_east_deg)}"


def utc_init_cmds(now_utc: datetime) -> list[str]:
    """The clock-init sequence, UTC scheme: zero offset + no DST + UTC date/time
    (mount 'local' == UTC => correct sidereal). Each expects ack ``1``.
    NOTE: no space after SC/SL — matches the captured ASIMount session."""
    return [
        "SG+00:00",
        "SH0",
        f"SC{now_utc.strftime('%m/%d/%y')}",
        f"SL{now_utc.strftime('%H:%M:%S')}",
    ]


@dataclass
class Lx200Status:
    """Conservative decode of the ``:GU#`` OnStep-style status word. Only the
    leading tracking flag is trusted ('n' = not tracking); everything else rides
    in ``raw`` until validated against live states. Parked state should key on
    ``:Gps#`` (2 = parked), not this word."""

    raw: str
    tracking: bool


def parse_status(word: str) -> Lx200Status:
    t = _strip(word)
    return Lx200Status(raw=t, tracking=not t.startswith("n"))
