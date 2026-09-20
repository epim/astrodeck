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
survives the process. Writing is open-append-close and fully guarded: a failed
write never breaks a publish, and PAUSES persistence for
:data:`NIGHTLOG_RETRY_S` rather than ending it (2026-09-06: one RecursionError
inside ``append`` turned the disk log off at 22:13 for the remaining nine hours
of the night, while ``/api/logs`` kept serving the in-memory ring and looked
perfectly healthy).

The bus also collapses identical log lines arriving faster than
:data:`STORM_PASS` per :data:`STORM_WINDOW_S` into one summary line. The same
night, a recursion in the sequence engine wrote 323 copies of "holding for clear
sky" in one second; unlimited, a storm evicts the whole 200-entry ring and
floods the night file with one sentence.
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
#: seconds the night-log writer stays paused after a failed write before it
#: tries again. It retries for as long as the process lives.
NIGHTLOG_RETRY_S = 60.0
#: identical log lines that go through untouched before a storm is collapsed.
STORM_PASS = 3
#: the window a storm is measured over; a repeat this far apart is new news.
STORM_WINDOW_S = 1.0


def _now() -> float:
    """Monotonic seconds — in one place so tests can drive both cooldowns."""
    return time.monotonic()


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
    Every method is total: a failed write PAUSES persistence for
    ``NIGHTLOG_RETRY_S`` (``self.failed`` means "paused right now") rather than
    propagating into a ``bus.publish`` on the capture path — and rather than
    giving up, which is what it used to do."""

    def __init__(self, keep_nights: int = LOG_KEEP_NIGHTS):
        self.keep_nights = keep_nights
        #: paused right now — a write failed and the cooldown has not elapsed.
        self.failed = False
        self._retry_at = 0.0
        self._paused_at = 0.0
        self._paused_for = 0.0
        self._pause_desc = ""
        #: ``"pause"`` / ``"resume"`` owed to the log; the bus formats and
        #: publishes it, off the failing write's stack. See ``_pause``.
        self._notice: str | None = None
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
        """Persist one log event. Never raises.

        A failed write pauses the writer for ``NIGHTLOG_RETRY_S`` and then tries
        again, indefinitely: a disk that is full at 22:13 usually is not at
        23:13, and the night the writer gives up is the night the operator most
        needs the file."""
        if self.failed and _now() < self._retry_at:
            return                              # paused; the ring still has it
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
        except Exception as exc:                # noqa: BLE001 - total by design
            # Disk full / read-only / permission / a RecursionError arriving from
            # the caller's stack: pause, do not stop.
            self._pause(exc)
            return
        if self.failed:
            self._resume()

    def _pause(self, exc: BaseException) -> None:
        """Record a failed write with the least possible work.

        This runs inside the failing ``append``, which sits on the publish path
        — and on 2026-09-06 22:13:46 that path was the bottom of a runaway
        recursion in the sequence engine, which is where the RecursionError came
        from. Anything that needs stack here (formatting, logging, a re-entrant
        publish) can raise RecursionError *again out of the handler* and take the
        publish down with it. So this does stores only, inside a guard, and the
        notice line is formatted and published later by the bus, on the next
        publish, at sane depth."""
        self.failed = True                      # a store: cannot itself fail
        self._notice = "pause"
        self._last_night = None                 # so a retry re-does the mkdir
        try:
            now = _now()
            self._paused_at = now
            self._retry_at = now + NIGHTLOG_RETRY_S
            self._pause_desc = f"{type(exc).__name__}: {exc}"
        except Exception:                       # noqa: BLE001 - out of stack
            self._pause_desc = ""               # retry on the next append

    def _resume(self) -> None:
        """A write worked again after a pause."""
        self.failed = False
        self._paused_for = max(0.0, _now() - self._paused_at)
        self._notice = "resume"

    def take_notice(self) -> tuple[str, str] | None:
        """The one ``(level, message)`` this writer owes the log, or ``None``.

        Formatted here rather than in ``append`` because the caller is the bus,
        at ordinary stack depth, and not inside the failing write."""
        tag, self._notice = self._notice, None
        if tag == "pause":
            return ("warning",
                    f"night log file writer paused: "
                    f"{self._pause_desc or 'unknown error'}; retrying in "
                    f"{NIGHTLOG_RETRY_S:.0f} s")
        if tag == "resume":
            return ("info", "night log file writer resumed after "
                            f"{self._paused_for:.0f} s")
        return None

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
        # Bounded operation snapshots for reconnecting controllers. Unlike log
        # history these retain terminal results even when no browser was open.
        self.operation_snapshots: dict[str, dict] = {}
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._history: deque[Event] = deque(maxlen=history)
        #: rolling per-night disk log (UX #9). ``None`` disables persistence.
        self.night_log: NightLogWriter | None = NightLogWriter() if persist else None
        #: storm limiter: the open run of identical lines, and its counts.
        self._storm_key: tuple[Any, Any, Any] | None = None
        self._storm_start = 0.0
        self._storm_seen = 0
        self._storm_dropped = 0
        #: guards the night-log notice against re-entering its own publish.
        self._in_notice = False

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)

    def publish(self, type: str, **data: Any) -> None:
        if type in ("focus", "filter_offsets"):
            from copy import deepcopy
            self.operation_snapshots[type] = {**deepcopy(data), "_observed_at": time.time()}
        # Any publish, not just a log one: if the failure happened on the last
        # log line of the night, the status events still flowing are what carry
        # the notice out.
        self._drain_night_log_notice()
        if type == "log":
            if self._storm_suppresses(data):
                return
        self._deliver(Event(type=type, data=data))

    def _deliver(self, ev: Event) -> None:
        """Ring, disk, subscribers — the fan-out, past the storm limiter."""
        if ev.type == "log":
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

    # -- storm limiter ---------------------------------------------------------

    def _storm_suppresses(self, data: dict[str, Any]) -> bool:
        """True when this line is a repeat that should be dropped.

        The first ``STORM_PASS`` identical lines (same level, message and
        source) inside a ``STORM_WINDOW_S`` window go through untouched — the
        first one is never delayed, which is the whole point of a log — and the
        rest are counted. The count is published as one summary line when the
        window closes: the next publish that is either a different line or the
        same line after the window, or an explicit :meth:`flush`. No timer, so
        nothing needs a running event loop."""
        key = (data.get("level"), data.get("message"), data.get("source"))
        now = _now()
        if key == self._storm_key and now - self._storm_start < STORM_WINDOW_S:
            self._storm_seen += 1
            if self._storm_seen <= STORM_PASS:
                return False
            self._storm_dropped += 1
            return True
        self.flush()
        self._storm_key = key
        self._storm_start = now
        self._storm_seen = 1
        self._storm_dropped = 0
        return False

    def flush(self) -> None:
        """Close the open storm window, publishing its summary if it has one."""
        key, dropped = self._storm_key, self._storm_dropped
        self._storm_key = None
        self._storm_dropped = 0
        self._storm_seen = 0
        if key is None or dropped <= 0:
            return
        level, message, source = key
        self._deliver(Event(type="log", data={
            "level": level,
            "message": f"{message} (repeated {dropped} more times in "
                       f"{STORM_WINDOW_S:.1f} s)",
            "source": source,
        }))

    # -- night-log notices -----------------------------------------------------

    def _drain_night_log_notice(self) -> None:
        """Publish the one line the night-log writer owes (paused / resumed).

        The writer records a pause inside a failing ``append``, which the bus
        calls from ``_deliver``: logging from there would re-enter the bus at
        whatever depth the failure happened on (a RecursionError, in the
        2026-09-06 case), so the line waits and is published from here on the
        next publish. ``_in_notice`` keeps that publish from re-entering this."""
        w = self.night_log
        if w is None or self._in_notice:
            return
        notice = w.take_notice()
        if notice is None:
            return
        self._in_notice = True
        try:
            self.publish("log", level=notice[0], message=notice[1], source="log")
        except Exception:               # noqa: BLE001 - a notice never breaks a publish
            pass
        finally:
            self._in_notice = False

    def log(self, level: str, message: str, source: str = "hub") -> None:
        self.publish("log", level=level, message=message, source=source)

    @property
    def log_history(self) -> list[dict[str, Any]]:
        return [e.to_json() for e in self._history]


bus = EventBus(persist=_persist_default())
