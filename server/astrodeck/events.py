"""Async event bus.

Everything that happens in AstroDeck (device status, preview frames, sequence
progress, guide pulses, log lines) flows through one bus and fans out to all
connected WebSocket clients and any internal subscribers.

``log`` events are ALSO persisted (UX #9). The in-memory ring is a live tail for
the drawer — on a ten-hour run it holds roughly the last forty minutes, so the
01:15 cloud pause is gone by 07:00 and a restart/auto-update wipes what is left.
:class:`NightLogWriter` appends every log line to a per-night JSONL file under
``captures/logs/`` (noon-to-noon night key, same rollover as the ``$$NIGHT$$``
filename token) with a rolling retention, so the morning-after narrative
survives the process. Writing is open-append-close and fully guarded: a disk
problem disables persistence for the process and never breaks a publish.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: nights of log files kept on disk; older ones are pruned on rollover.
LOG_KEEP_NIGHTS = 14
#: hard cap on rows returned by one persisted-log read (keeps the route bounded).
LOG_READ_MAX = 20000


def night_key(ts: float | None = None) -> str:
    """Local noon-to-noon night date for ``ts`` (``YYYY-MM-DD``).

    Identical rollover to the ``$$NIGHT$$`` filename token, so a night's log file
    and that night's frames carry the same date after midnight."""
    return time.strftime("%Y-%m-%d", time.localtime((ts if ts is not None
                                                     else time.time()) - 12 * 3600))


class NightLogWriter:
    """Append-only per-night JSONL log store under ``captures/logs``.

    The directory is resolved LIVE off ``hub.CAPTURE_DIR`` (like the report
    store) so a test that monkeypatches the capture root redirects the logs too.
    Every method is total: any OSError disables persistence for the process
    (``self.failed``) rather than propagating into a ``bus.publish`` on the
    capture path."""

    def __init__(self, keep_nights: int = LOG_KEEP_NIGHTS):
        self.keep_nights = keep_nights
        self.failed = False
        self._last_night: str | None = None
        self._pruned_for: str | None = None

    # -- paths ---------------------------------------------------------------

    @staticmethod
    def dir() -> Path:
        """``captures/logs`` — resolved live so a CAPTURE_DIR monkeypatch wins."""
        from . import hub as _hubmod
        return _hubmod.CAPTURE_DIR / "logs"

    @classmethod
    def path_for(cls, night: str) -> Path:
        return cls.dir() / f"{night}.jsonl"

    # -- write ---------------------------------------------------------------

    def append(self, ev: "Event") -> None:
        """Persist one log event. Never raises."""
        if self.failed:
            return
        try:
            night = night_key(ev.ts)
            rolled = night != self._last_night
            if rolled:
                self.dir().mkdir(parents=True, exist_ok=True)
                self._last_night = night
            with open(self.path_for(night), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev.to_json(), separators=(",", ":")) + "\n")
            if rolled:
                # after the write, so tonight's file counts toward the retention
                self._prune(night)
        except OSError:
            # Disk full / read-only / permission: stop trying. The in-memory ring
            # still serves the drawer, and capture must never fail over a log.
            self.failed = True
        except Exception:                       # noqa: BLE001 - defensive only
            self.failed = True

    def _prune(self, current: str) -> None:
        """Keep the newest ``keep_nights`` files (current one included)."""
        if self._pruned_for == current:
            return
        self._pruned_for = current
        try:
            files = sorted(self.dir().glob("*.jsonl"))
            for old in files[:max(0, len(files) - self.keep_nights)]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    # -- read ----------------------------------------------------------------

    def nights(self) -> list[dict[str, Any]]:
        """``[{night, bytes}]`` for every persisted night, newest first."""
        out: list[dict[str, Any]] = []
        try:
            for p in self.dir().glob("*.jsonl"):
                try:
                    out.append({"night": p.stem, "bytes": p.stat().st_size})
                except OSError:
                    continue
        except OSError:
            return []
        out.sort(key=lambda r: r["night"], reverse=True)
        return out

    def read(self, night: str, *, level: str | None = None,
             limit: int = LOG_READ_MAX) -> list[dict[str, Any]]:
        """Rows for one night, oldest first, optionally filtered by ``level``.

        ``limit`` keeps the NEWEST ``limit`` rows (the tail is what a reader
        wants). A malformed line is skipped, never fatal."""
        rows: list[dict[str, Any]] = []
        try:
            with open(self.path_for(night), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if level and (row.get("data") or {}).get("level") != level:
                        continue
                    rows.append(row)
        except (OSError, ValueError):
            return []
        if limit and limit > 0 and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def export_text(self, night: str) -> str:
        """The night as plain text, one ``HH:MM:SS [level · source] message`` per
        line — what a user actually wants to read/mail, not raw JSON."""
        lines = []
        for row in self.read(night):
            d = row.get("data") or {}
            stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(row.get("ts") or 0))
            lines.append(f"{stamp} [{d.get('level', 'info')} · "
                         f"{d.get('source', 'hub')}] {d.get('message', '')}")
        return "\n".join(lines) + ("\n" if lines else "")


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "ts": self.ts}


def _persist_default() -> bool:
    """``ASTRODECK_LOG_PERSIST=0`` turns the disk log off (read-only appliances,
    a RAM-disk capture root, or a CI run that doesn't want the file)."""
    return (os.environ.get("ASTRODECK_LOG_PERSIST") or "").strip().lower() \
        not in ("0", "false", "no", "off")


class EventBus:
    def __init__(self, history: int = 200, persist: bool = True):
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._history: deque[Event] = deque(maxlen=history)
        #: rolling per-night disk log (UX #9). ``None`` disables persistence.
        self.night_log: NightLogWriter | None = NightLogWriter() if persist else None

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)

    def publish(self, type: str, **data: Any) -> None:
        ev = Event(type=type, data=data)
        if type == "log":
            self._history.append(ev)
            if self.night_log is not None:
                self.night_log.append(ev)
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest to keep the stream live.
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def log(self, level: str, message: str, source: str = "hub") -> None:
        self.publish("log", level=level, message=message, source=source)

    @property
    def log_history(self) -> list[dict[str, Any]]:
        return [e.to_json() for e in self._history]


bus = EventBus(persist=_persist_default())
