# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What the PC's previous session ended as, written to the night log at startup.

Issue #125. On 2026-09-08 the rig PC reset itself twice on a hot afternoon, and
saying so took a dig through the Windows event log after the fact. On
2026-09-21 it went offline and stayed off, and the evidence that could say why
was on the machine that was down. Either way the question - did the last
session end cleanly, by a planned restart, or by power loss / a crash / a
firmware reset - has an answer in the System log the moment the PC is back,
and nobody reads it until something else has already gone wrong.

So the server reads it once, at startup, and writes ONE line. It is the
cheapest of #125's three asks and the only one that needs no hardware and no
outside service.

The events, all from the System log:

    EventLog 6005   the event log service started: this boot
    EventLog 6006   the event log service stopped: a clean shutdown
    User32   1074   a process asked for a restart or power-off: planned
    EventLog 6008   "the previous system shutdown was unexpected", logged AT boot
    Kernel-Power 41 "rebooted without cleanly shutting down first", logged AT boot

The two unexpected-shutdown events are written by the NEW boot about the old
one, so they sit AT the boot marker - but not after it. Measured on a real
System log: Kernel-Power 41 was stamped 16.5 s BEFORE its boot's 6005 (the
kernel writes it before the event log service reports itself started), and
6008 at the same instant as 6005. So the window around the marker reaches back
as well as forward; a window that only looked forward saw the 6008 and would
have missed a boot that logged 41 alone.

Nothing identifying leaves: the line carries event ids, a boot time and at most
the base name of the process that asked for a restart. Not the computer name,
not a user, not a path.

#606 part A: the same read also leaves ONE WORD behind, :data:`BOOT_WORD`, for
the dead-man beacon (``rig_beacon``) to carry off the box. The night log lives
on the machine that is down, so the word is the only part of this evidence an
owner can see from outside. It is a closed three-word vocabulary and is NOT the
line: the line names a process and a time, the word names neither.

#1076: the forward-looking half sits at the bottom of this module. It reads
whether Windows has a restart PENDING and Windows Update's ACTIVE HOURS, so the
arm gate can refuse a night that Windows may restart the PC under.
"""
from __future__ import annotations

import asyncio
import ntpath
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, tzinfo
from typing import Any, NamedTuple

__all__ = ["classify", "parse_events", "log_boot_cause", "BOOT_WORD",
           "RestartState", "restart_state", "active_hours",
           "night_outside_active_hours", "DEFAULT_ACTIVE_HOURS"]

#: How the previous session ended, as one word for the dead-man beacon:
#: "unexpected" (a crash, power loss or reset: Kernel-Power 41 / EventLog 6008),
#: "normal" (a clean shutdown or a planned restart), or "unread". "unread" is
#: both what it says until :func:`log_boot_cause` has finished (the System log
#: is read off the loop, seconds after startup) and what it stays when the read
#: finds nothing it can state: not Windows, wevtutil failing, no boot marker, or
#: a boot with no record of how the session before it ended. A "normal" there
#: would be a claim the System log never made.
#:
#: Read it as ``bootcause.BOOT_WORD`` at the moment of use, never
#: ``from .bootcause import BOOT_WORD``: that binds the value once, at import,
#: which is before the read and so would be "unread" for the life of the process.
BOOT_WORD = "unread"

_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
#: How long after the boot marker an unexpected-shutdown event may be written
#: and still describe THAT boot. They are logged in the first seconds; five
#: minutes is generous and still far short of the next boot.
_AT_BOOT = timedelta(minutes=5)
#: How far BEFORE the boot marker Kernel-Power 41 may be stamped and still be
#: that boot's (measured 16.5 s). 41 is only ever written at boot, so two
#: minutes cannot reach a previous session's.
_KERNEL_LEAD = timedelta(minutes=2)
#: How long before a clean 6006 a 1074 may be and still be the request that
#: caused it. A shutdown takes seconds to a few minutes after it is asked for.
_ASKED_BEFORE = timedelta(minutes=10)
_QUERY = ("*[System[(EventID=41 or EventID=1074 or EventID=6005 "
          "or EventID=6006 or EventID=6008)]]")


def _parse_system_time(stamp: str) -> datetime:
    """`2026-09-08T20:32:53.1234567Z` -> an aware UTC datetime.

    wevtutil writes SEVEN fractional digits and a trailing Z; the fraction is
    cut to the six a datetime holds. Raises ValueError on anything else.
    """
    stamp = stamp.strip()
    if not stamp.endswith("Z"):
        raise ValueError(stamp)
    body = stamp[:-1]
    if "." in body:
        whole, frac = body.split(".", 1)
        body = f"{whole}.{(frac + '000000')[:6]}"
    return datetime.fromisoformat(body + "+00:00")


def parse_events(xml_text: str) -> list[dict[str, Any]]:
    """`wevtutil qe ... /f:xml` output -> ``[{id, provider, at, data}]``.

    wevtutil prints one ``<Event>`` element per match with no root, so they
    are wrapped in one before parsing. Anything unparseable is skipped rather
    than raised: this runs at boot and must never be the reason boot fails.
    """
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(f"<root>{xml_text}</root>")
    except ET.ParseError:
        return out
    for ev in root.findall("e:Event", _NS):
        sysn = ev.find("e:System", _NS)
        if sysn is None:
            continue
        try:
            event_id = int((sysn.findtext("e:EventID", default="", namespaces=_NS) or "").strip())
            created = sysn.find("e:TimeCreated", _NS)
            stamp = (created.get("SystemTime") if created is not None else "") or ""
            at = _parse_system_time(stamp)
        except (ValueError, TypeError):
            continue
        provider = sysn.find("e:Provider", _NS)
        data = {d.get("Name") or f"#{i}": (d.text or "")
                for i, d in enumerate(ev.findall("e:EventData/e:Data", _NS))}
        out.append({"id": event_id,
                    "provider": provider.get("Name", "") if provider is not None else "",
                    "at": at, "data": data})
    return out


def classify(events: list[dict[str, Any]]) -> tuple[str, str]:
    """``(level, message)`` for the boot the latest 6005 marks.

    ``level`` is "warning" for an unexpected end, "info" otherwise, so a night
    log read for trouble finds the one that matters without the rest.
    """
    level, message, _word = _classify(events)
    return (level, message)


def _classify(events: list[dict[str, Any]]) -> tuple[str, str, str]:
    """``(level, message, word)``: :func:`classify`'s answer plus the
    :data:`BOOT_WORD` that goes with it.

    The word is decided HERE, beside the branch that decides the message, not
    derived from the level afterwards: the level is "info" both for a clean
    shutdown and for "the System log cannot say", and only the branch knows
    which of the two it is."""
    events = sorted(events, key=lambda e: e["at"])
    boots = [e for e in events if e["id"] == 6005]
    if not boots:
        return ("info", "boot cause: no boot marker in the System log, so what "
                        "the previous session ended as cannot be said", "unread")
    boot = boots[-1]["at"]
    when = boot.strftime("%Y-%m-%d %H:%M UTC")
    after = [e for e in events if boot - _KERNEL_LEAD <= e["at"] <= boot + _AT_BOOT]
    unexpected = sorted({e["id"] for e in after if e["id"] in (41, 6008)})
    if unexpected:
        ids = " and ".join({41: "Kernel-Power 41", 6008: "EventLog 6008"}[i] for i in unexpected)
        return ("warning",
                f"boot cause: this PC started {when}, and the session before it "
                f"ended UNEXPECTEDLY ({ids}) - power loss, a crash, or a "
                f"firmware or thermal reset. Nothing shut it down on purpose.",
                "unexpected")
    before = [e for e in events if e["at"] < boot and e["id"] in (1074, 6006)]
    if before:
        last = before[-1]
        # A planned restart logs 1074 (who asked) and THEN 6006 (the log
        # stopping). Reading only the last event called every Windows Update
        # restart a plain clean shutdown and dropped who asked for it.
        if last["id"] == 6006:
            asked = [e for e in before if e["id"] == 1074
                     and last["at"] - _ASKED_BEFORE <= e["at"] <= last["at"]]
            if asked:
                last = asked[-1]
        if last["id"] == 1074:
            process = ntpath.basename(last["data"].get("param1", "") or "").split(" ")[0]
            kind = (last["data"].get("param5", "") or "restart").strip()
            by = f" by {process}" if process else ""
            return ("info", f"boot cause: this PC started {when} after a planned "
                            f"{kind}{by} (User32 1074).", "normal")
        return ("info", f"boot cause: this PC started {when} after a clean "
                        f"shutdown (EventLog 6006).", "normal")
    return ("info", f"boot cause: this PC started {when}; the System log holds no "
                    f"record of how the session before it ended.", "unread")


def _read_system_log(timeout_s: float = 10.0) -> str:
    """The last 40 matching System events as XML, newest first. '' off Windows
    or on any failure - including wevtutil missing, denied or slow."""
    if sys.platform != "win32":
        return ""
    try:
        done = subprocess.run(
            ["wevtutil", "qe", "System", f"/q:{_QUERY}", "/c:40", "/rd:true", "/f:xml"],
            capture_output=True, text=True, timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


async def log_boot_cause(log=None) -> None:
    """Read the System log off the event loop, write one line and set
    :data:`BOOT_WORD`. Never raises."""
    global BOOT_WORD
    if log is None:
        from .events import bus
        log = bus.log
    try:
        xml_text = await asyncio.to_thread(_read_system_log)
        if not xml_text:
            return                          # not Windows, or nothing readable
        level, message, word = _classify(parse_events(xml_text))
        # The word first: it is what the next dead-man ping carries, and the
        # line below is a bus publish that can fail on its own account.
        BOOT_WORD = word
        log(level, message, "system")
    except Exception:                       # noqa: BLE001 - never the reason boot fails
        return


# ---------------------------------------------------------------------------
# The other half: can Windows restart this PC under a night? (#1076)
#
# Windows Update restarted the rig PC at 08:43 under TrustedInstaller. It does
# that when a restart is PENDING (an update is staged) and the clock is outside
# the ACTIVE HOURS (inside them it will not). Both live in the registry, and
# both are read here, read-only, through ONE function, :func:`_registry_read`,
# which tests replace with a fake. Nothing in this module writes the registry,
# changes Windows Update, or changes a power or service setting. Moving the
# active hours is the operator's act, documented in the refusal below and never
# done by the server.
# ---------------------------------------------------------------------------

#: The two markers an update leaves behind when it wants a restart, each a key
#: whose mere existence is the signal. The word is what the status, the beacon
#: and the refusal say. ``PendingFileRenameOperations`` is left out on purpose:
#: installers set it for their own files and it does not wake TrustedInstaller.
_PENDING_MARKERS = (
    ("windows_update", r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                       r"\WindowsUpdate\Auto Update\RebootRequired"),
    ("component_servicing", r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                            r"\Component Based Servicing\RebootPending"),
)
_ACTIVE_HOURS_USER = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
_ACTIVE_HOURS_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
#: Windows' own active hours when nothing has set any: 08:00 to 17:00.
DEFAULT_ACTIVE_HOURS = (8, 17)
#: How finely a night is sampled against the active hours.
_SAMPLE_STEP_S = 15 * 60


def _registry_read(key: str, value: str | None = None) -> Any:
    """One read-only look at ``HKLM\\<key>``: the named value's data, or
    ``True`` when ``value`` is None and the key exists; None when it is
    absent, unreadable, or this is not Windows.

    THE ONLY PLACE THIS MODULE TOUCHES THE REGISTRY, and it only opens a key
    for reading (``KEY_READ``). The 64-bit view is asked for explicitly: a
    32-bit Python would otherwise read ``WOW6432Node`` and find nothing."""
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            if value is None:
                return True
            return winreg.QueryValueEx(handle, value)[0]
    except OSError:
        return None


def _hour(raw: Any) -> int | None:
    """``raw`` as an hour of the day, or None. A bool is not an hour."""
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 23:
        return None
    return raw


def _hours_pair(key: str) -> tuple[int, int] | None:
    start, end = _hour(_registry_read(key, "ActiveHoursStart")), \
        _hour(_registry_read(key, "ActiveHoursEnd"))
    return (start, end) if start is not None and end is not None else None


def active_hours() -> tuple[int, int]:
    """``(start, end)`` hours of the day inside which Windows Update will not
    restart the PC. A policy that sets them beats the user's setting; a pair
    that cannot be read whole (one hour missing or out of range) is treated as
    unset, because half a window says nothing. ``end`` may be before
    ``start``: the window then wraps midnight."""
    if _registry_read(_ACTIVE_HOURS_POLICY, "SetActiveHours") == 1:
        policy = _hours_pair(_ACTIVE_HOURS_POLICY)
        if policy is not None:
            return policy
    return _hours_pair(_ACTIVE_HOURS_USER) or DEFAULT_ACTIVE_HOURS


class RestartState(NamedTuple):
    """Whether a restart is pending, which markers say so, and the active
    hours."""
    pending: bool
    sources: tuple[str, ...]
    active_start: int
    active_end: int

    def as_status(self) -> dict[str, Any]:
        """The block ``/api/status`` carries: words and hours, nothing else."""
        return {"pending": self.pending, "sources": list(self.sources),
                "active_hours": {"start": self.active_start,
                                 "end": self.active_end}}


def restart_state() -> RestartState:
    """Read the pending-restart markers and the active hours. Never raises: a
    registry that cannot be read is "nothing pending, default hours", the
    same answer a PC that is not Windows gives."""
    try:
        sources = tuple(word for word, key in _PENDING_MARKERS
                        if _registry_read(key) is True)
        start, end = active_hours()
    except Exception:                       # noqa: BLE001 - a status read never raises
        return RestartState(False, (), *DEFAULT_ACTIVE_HOURS)
    return RestartState(bool(sources), sources, start, end)


def _inside(hour: float, start: int, end: int) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def night_outside_active_hours(dusk: float, dawn: float, start: int, end: int,
                               *, tz: tzinfo | None = None) -> bool:
    """Does any part of ``[dusk, dawn]`` (epoch seconds) fall outside the
    active hours ``start``..``end``? True means Windows may restart the PC
    during it. ``tz`` is the PC's own zone by default: active hours are local
    clock hours."""
    t = dusk
    while True:
        local = datetime.fromtimestamp(t, tz)
        if not _inside(local.hour + local.minute / 60.0, start, end):
            return True
        if t >= dawn:
            return False
        t = min(t + _SAMPLE_STEP_S, dawn)
