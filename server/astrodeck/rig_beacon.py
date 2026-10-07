# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The status line the dead-man ping carries off the box (#606, part A).

When the rig PC dies its alerts die with it, and its night log sits on the
disk of the machine that is down. The dead-man switch (#125, backlog ruling
D-19, owner-approved 2026-09-30) says THAT it stopped; nothing off the box said
WHEN or in what state. Monitors in the healthchecks.io family store the body of
a ping and a timestamp history, so one short line riding the ping the dead-man
already sends puts "last seen, doing X" where the owner can read it with the PC
dark, with no relay and no new service::

    v=1 state=running frames=42 level=warning up=3600 boot=normal

====== ===========================================================================
key    value
====== ===========================================================================
v      the format version, always 1
state  the engine's state word, from a fixed list, else ``unknown``
frames the engine's accepted-frame count, an integer capped at 999999
level  the highest of ``info`` / ``warning`` / ``error`` the log said in the last
       15 minutes, else ``none``
up     seconds since this process started, an integer
boot   how the PC's previous session ended: ``unexpected`` / ``normal`` /
       ``unread`` (``bootcause.BOOT_WORD``)
====== ===========================================================================

``boot`` is about the PC's last BOOT, not this process: it holds until the PC
next boots, while ``up`` restarts with the server (a deploy, a crash restart).
``boot=unexpected up=300`` after a server restart is the same reset as an hour
ago, so read it beside the monitor's own timestamps.

A CLOSED vocabulary, on purpose. The line goes to a service the owner does not
run, on every ping, for as long as the rig is up, so the question is never "does
it read well" but "can any input put a character of its own into it". The night
log is the cautionary case: its lines can carry a target name, a mount position
or site-derived text. That is why the issue's "last night-log line" is here
``level``, one of four words, and why nothing below ever touches a message, a
target, a coordinate, a mount position, an alt/az or the site label. Three
walls, each enough to stop a hostile string alone, because the #19 lesson is
that a filter cannot withhold a value its own route computes:

1. every field is read through a clamp to its vocabulary (:func:`_word`,
   :func:`_count`), so a value outside it becomes ``unknown`` / ``none`` /
   ``unread`` / 0, never itself;
2. :func:`render` clamps AGAIN and builds the text from a fixed key order, so a
   hand-built dict, or a later edit that forgot a clamp above, still renders
   closed;
3. the dispatcher refuses to send anything that is not exactly this grammar
   (:func:`is_closed_vocabulary`).

And this module cannot reach a site value to leak: it imports no config, no
locations, no hub and no engine (a test reads its import list). Its inputs are
the objects it is handed.

``level`` is read from the log ring WITHOUT the ``site_derived`` lines
(``bus.log_history_unflagged``): such a line is flagged because its MOMENT is
set by a site computation (a meridian wait ending, a flip at the crossing; spec
6.9, #166), and this line leaves the box with a time on it. The ring holds 200
lines, so a flood inside the window can push an older warning out before this
reads it: the word can understate a busy quarter hour, never overstate it.

Uptime Kuma's push monitor wants its text in a ``?msg=`` query rather than a
body. Not built: the plan names healthchecks.io, the dispatcher falls back to a
plain GET on a monitor that will not take a POST (see ``alerting``), and a Kuma
owner loses only this line. Add a ``?msg=`` form if the owner picks Kuma. The
relay half of #606 (the beacon on PONG, the last N per home) and showing it to
anyone are separate rulings and are not here.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from . import bootcause

__all__ = ["BEACON_VERSION", "FRAMES_MAX", "LEVEL_WINDOW_S", "PROCESS_START",
           "build_beacon", "render", "make_source", "is_closed_vocabulary"]

BEACON_VERSION = 1
#: How far back the ``level`` word looks. Long enough that a warning at the top
#: of the hour is still on the line when the owner opens the monitor, short
#: enough that a bad night does not read as bad all the next day.
LEVEL_WINDOW_S = 15 * 60.0
FRAMES_MAX = 999_999
#: Three years, in seconds: a cap so the field stays a short integer.
_UP_MAX = 99_999_999

#: Every word the engine sets for ``state["state"]`` (sequence/engine.py:
#: ``_TERMINAL_STATES`` plus the live ones). Anything else reads ``unknown``.
_STATES = frozenset({"idle", "running", "paused", "holding", "aborting",
                     "aborted", "complete", "error"})
_STATE_DEFAULT = "unknown"
#: Ascending severity; ``bus.log`` takes any string, but only these three count.
_LEVEL_RANK = {"info": 1, "warning": 2, "error": 3}
_LEVELS = ("none", "info", "warning", "error")
_BOOT_WORDS = frozenset({"unexpected", "normal", "unread"})

#: The wire order and names. ``up_s`` in the dict is ``up`` on the line.
_FIELDS = (("v", "v"), ("state", "state"), ("frames", "frames"),
           ("level", "level"), ("up_s", "up"), ("boot", "boot"))

#: The one line the dispatcher will send, built from the vocabularies above so
#: the grammar and the clamps cannot drift apart.
_GRAMMAR = re.compile(
    "v={v} state=({states}) frames=[0-9]{{1,{fd}}} level=({levels}) "
    "up=[0-9]{{1,{ud}}} boot=({boots})".format(
        v=BEACON_VERSION,
        states="|".join(sorted(_STATES | {_STATE_DEFAULT})),
        fd=len(str(FRAMES_MAX)),
        levels="|".join(_LEVELS),
        ud=len(str(_UP_MAX)),
        boots="|".join(sorted(_BOOT_WORDS))))

#: When this module was imported. The app imports it while it builds, so this
#: is within a second of the process starting; wall-clock because the beacon's
#: ``now`` is (the log ring's timestamps are).
PROCESS_START = time.time()


def _word(value: Any, allowed: frozenset[str] | tuple[str, ...], default: str) -> str:
    """``value`` if it is exactly one of ``allowed``, else ``default``. Never
    ``value`` itself unless it is a member: a non-string, a string of another
    case and an empty one all fall to the default."""
    return value if isinstance(value, str) and value in allowed else default


def _count(value: Any, cap: int) -> int:
    """``value`` as an integer in ``[0, cap]``; 0 for anything that is not a
    number (a string of words, None, NaN, infinity)."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(n, cap))


def _engine_fields(engine: Any) -> tuple[str, int]:
    """``(state word, frame count)`` from an engine-shaped object. Reads
    exactly two keys of ``engine.state`` and nothing else in it: the dict also
    holds the target, the schedule and the hold detail, all of it the site's."""
    try:
        state = engine.state
        raw_state = state.get("state")
        progress = state.get("progress")
        raw_frames = progress.get("frames_done") if isinstance(progress, dict) else None
    except Exception:                      # noqa: BLE001 - a beacon never raises
        return (_STATE_DEFAULT, 0)
    return (_word(raw_state, _STATES, _STATE_DEFAULT), _count(raw_frames, FRAMES_MAX))


def _level_word(bus: Any, now: float) -> str:
    """The highest log level in the last :data:`LEVEL_WINDOW_S`, or ``none``.
    Reads only each line's ``type``, ``ts`` and ``data.level``: never its
    message."""
    try:
        rows = list(bus.log_history_unflagged)
    except Exception:                      # noqa: BLE001 - a beacon never raises
        return "none"
    best = 0
    for row in rows:
        try:
            if row.get("type") != "log":
                continue
            if now - float(row["ts"]) >= LEVEL_WINDOW_S:
                continue
            best = max(best, _LEVEL_RANK.get(row["data"].get("level"), 0))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return _LEVELS[best]


def build_beacon(engine: Any, bus: Any, boot_word: Any, now: float, *,
                 started: float | None = None) -> dict[str, Any]:
    """The beacon as a dict of exactly the six keys, every value already inside
    its vocabulary. Pure: it reads ``engine.state`` and ``bus``'s unflagged log
    ring, and takes the clock and the boot word as arguments.

    ``started`` is the process start (default :data:`PROCESS_START`); a test
    passes its own."""
    state, frames = _engine_fields(engine)
    began = PROCESS_START if started is None else started
    return {
        "v": BEACON_VERSION,
        "state": state,
        "frames": frames,
        "level": _level_word(bus, now),
        "up_s": _count(now - began, _UP_MAX),      # a clock that stepped back reads 0
        "boot": _word(boot_word, _BOOT_WORDS, "unread"),
    }


def _coerce(key: str, value: Any) -> str:
    """One field of a beacon, as the text that goes on the line. The last wall:
    whatever ``value`` is, what returns is a member of ``key``'s vocabulary or
    a bare integer."""
    if key == "v":
        return str(BEACON_VERSION)
    if key == "state":
        return _word(value, _STATES, _STATE_DEFAULT)
    if key == "frames":
        return str(_count(value, FRAMES_MAX))
    if key == "level":
        return _word(value, _LEVELS, "none")
    if key == "up_s":
        return str(_count(value, _UP_MAX))
    return _word(value, _BOOT_WORDS, "unread")


def render(beacon: dict[str, Any]) -> str:
    """``v=1 state=running frames=42 level=warning up=3600 boot=normal``.

    One line, ``key=value`` pairs in a fixed order, lower-case letters, digits
    and ``_=. -`` only. Keys the dict carries beyond the six are not printed."""
    return " ".join(f"{wire}={_coerce(key, beacon.get(key))}" for key, wire in _FIELDS)


def is_closed_vocabulary(text: Any) -> bool:
    """True only for a line :func:`render` could have produced. The dispatcher
    asks this of whatever its ``beacon_source`` returns, because the door the
    text leaves by should not take the source's word for it. A coordinate-
    looking string of lower-case letters and digits passes a character-class
    check, so the test is the grammar, not the alphabet."""
    return isinstance(text, str) and _GRAMMAR.fullmatch(text) is not None


def make_source(engine: Any, bus: Any) -> Callable[[], str]:
    """The ``AlertDispatcher.beacon_source`` for this engine and bus.

    ``bootcause.BOOT_WORD`` is read as a module attribute at each call: the
    boot read finishes seconds after startup, so a value bound when this was
    built would be ``unread`` for the life of the process."""
    def source() -> str:
        return render(build_beacon(engine, bus, bootcause.BOOT_WORD, time.time()))
    return source
