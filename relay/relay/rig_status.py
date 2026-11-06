# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The rig's last pings, kept off the rig (#606): pure parsing, ring and page.

When the rig PC dies its night log sits on the disk of the machine that is down.
The home answers each relay PING with a PONG, and the PONG carries the status
beacon the rig builds (``astrodeck.rig_beacon``)::

    v=1 state=running frames=42 level=none up=3600 log=1799999940 pending=no boot=normal

The relay keeps the last ten per home, in memory beside the tunnel registry (so
the record outlives the tunnel), and ``GET /rig/{home_id}`` shows them. That is
the evidence an owner can read with the PC dark: when it was last heard, what it
was doing, whether Windows held a restart, and how its previous session ended.

The relay does not run the home, so the text a PONG carries is data from a
device: :func:`parse_beacon` accepts only the beacon grammar (a copy of
``rig_beacon``'s, kept in step by ``server/tests/test_w3_7_beacon_fields.py``)
and everything else is dropped. Nothing outside the vocabulary can reach the
stored record or the page, and the page escapes what it prints regardless.

No label comes from the home: the heading is the owner-set label or the id
(``landing`` explains why).

THE FOUR WORDS A PING IS KEPT FOR. The home answers every ten seconds, so ten
raw pings would be a hundred seconds of history, which says nothing about an
outage. A ping within a minute of the newest slot, with the same state, level,
boot and pending words, refreshes that slot in place; a changed word, or a
minute, opens a new one. Ten slots then reach back at least ten minutes while
staying exact about the moment the rig was last heard (``at`` of the newest).
"""
from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from html import escape
from typing import Any

#: Pings kept per home.
RECENT_PINGS = 10
#: A repeat inside this many seconds of the newest slot's opening shares it.
SLOT_S = 60.0

_STATES = ("aborted", "aborting", "complete", "error", "holding", "idle",
           "paused", "running", "unknown")
_LEVELS = ("none", "info", "warning", "error")
_BOOT_WORDS = ("normal", "unexpected", "unread")

_GRAMMAR = re.compile(
    r"v=1 state=(?P<state>{states}) frames=(?P<frames>[0-9]{{1,6}}) "
    r"level=(?P<level>{levels}) up=(?P<up>[0-9]{{1,8}})"
    r"(?: log=(?P<log>[0-9]{{1,12}}))?(?: pending=(?P<pending>yes|no))? "
    r"boot=(?P<boot>{boots})".format(
        states="|".join(_STATES), levels="|".join(_LEVELS),
        boots="|".join(_BOOT_WORDS)))

#: The words a change of which opens a new slot at once.
_SLOT_KEYS = ("state", "level", "boot", "pending")


def parse_beacon(text: Any) -> dict[str, Any] | None:
    """The beacon as typed fields, or None when ``text`` is not exactly the
    grammar. ``log`` and ``pending`` are None for a rig that does not send them
    yet."""
    if not isinstance(text, str):
        return None
    match = _GRAMMAR.fullmatch(text)
    if match is None:
        return None
    raw = match.groupdict()
    pending = raw["pending"]
    return {"state": raw["state"], "frames": int(raw["frames"]),
            "level": raw["level"], "up": int(raw["up"]), "boot": raw["boot"],
            "log": None if raw["log"] is None else int(raw["log"]),
            "pending": None if pending is None else pending == "yes"}


class PingLog:
    """The last :data:`RECENT_PINGS` slots of one home's pings, oldest first."""

    def __init__(self) -> None:
        self._slots: deque[dict[str, Any]] = deque(maxlen=RECENT_PINGS)

    def record(self, beacon: dict[str, Any], now: float) -> None:
        """Note a ping heard at ``now`` (epoch seconds)."""
        newest = self._slots[-1] if self._slots else None
        if (newest is not None and now - newest["since"] < SLOT_S
                and all(newest[k] == beacon[k] for k in _SLOT_KEYS)):
            newest.update(beacon, at=now)
            return
        self._slots.append({**beacon, "at": now, "since": now})

    def entries(self) -> list[dict[str, Any]]:
        """Copies of the slots, oldest first."""
        return [dict(slot) for slot in self._slots]


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _row(entry: dict[str, Any]) -> str:
    pending = entry["pending"]
    restart = ("not reported" if pending is None
               else "restart pending" if pending else "none")
    last_log = ("none" if entry["log"] in (None, 0)
                else _utc(entry["log"]))
    cells = (_utc(entry["at"]), entry["state"], entry["frames"], entry["level"],
             entry["boot"], restart, last_log)
    return ('<tr class="ping">'
            + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells)
            + "</tr>")


def render_rig_page(home_id: str, label: str, entries: list[dict[str, Any]],
                    *, connected: bool, now: float) -> str:
    """The ``GET /rig/{home_id}`` HTML body: the connection state, then the
    kept pings newest first. ``entries`` is :meth:`PingLog.entries`, oldest
    first, as given."""
    heading = escape(label or home_id)
    if connected:
        link = "Connected"
    elif entries:
        link = f"Not connected. Last heard {_utc(entries[-1]['at'])}"
    else:
        link = "Not connected"
    if entries:
        body = ('<table><thead><tr><th>Heard</th><th>State</th><th>Frames</th>'
                '<th>Log level</th><th>Boot cause</th><th>Windows</th>'
                '<th>Last log line</th></tr></thead><tbody>'
                + "".join(_row(e) for e in reversed(entries))
                + "</tbody></table>")
    else:
        body = "<p>No ping has been recorded since this relay started.</p>"
    return _PAGE_TEMPLATE.format(heading=heading, link=escape(link),
                                 checked=escape(_utc(now)), body=body)


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AstroDeck rig</title>
<style>
  body {{
    margin: 0; padding: 1.5rem 1rem;
    font-family: system-ui, -apple-system, sans-serif;
    background: #0b0e14; color: #e8eaed;
  }}
  h1 {{ font-size: 1.1rem; font-weight: 600; margin: 0 0 0.5rem; }}
  p {{ font-size: 1rem; margin: 0 0 1rem; }}
  table {{ border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ padding: 0.35rem 0.75rem; text-align: left; white-space: nowrap; }}
  th {{ color: #9aa0a6; font-weight: 500; }}
  tr.ping td {{ border-top: 1px solid #1b2130; }}
  div.scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
<h1>{heading}</h1>
<p>{link}. Page read {checked}.</p>
<div class="scroll">{body}</div>
</body>
</html>
"""
