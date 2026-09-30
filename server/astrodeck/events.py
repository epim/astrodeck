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

A log line can be flagged ``site_derived`` (spec 6.9, #166): its MOMENT was set
by a site computation, such as a meridian wait ending at a computed crossing.
The words of such a line can be clean and its ``ts`` still carries the site,
because the transit of a known RA at a known instant is the longitude. The
flag rides ``data.site_derived``; the serving seams (``api.redact``) drop the
line for a principal without ``view.site_derived``, and the night file keeps
it. An unflagged line has no such key, byte for byte as before the flag.

A subscriber that falls :data:`SUBSCRIBER_MAX` events behind loses the oldest,
and is told so (#444): its :class:`Subscription` serves one
``{"type": "relay_gap"}`` marker ahead of the next event it reads. That is
the frame the relay sends after its own drops, and the browser answers it by
re-reading the monitor snapshot. The drop used to be silent, so a phone that
lost a ``preview`` sat on an older frame until the next exposure, the failure
#399 fixed at the relay hop.

The bus is thread-aware (#480). A subscription is an ``asyncio.Queue`` of the
loop it subscribed from, and code handed to ``asyncio.to_thread`` logs too
(``SessionReporter._persist`` says a failed snapshot write from its worker).
``put_nowait`` from a foreign thread wakes a parked ``get()`` through
``loop.call_soon``, which is not thread-safe: the loop is not woken, so the
line waits for something else to wake it, and asyncio's debug mode raises
out of ``bus.log`` instead. So each subscription records its loop at
``subscribe``, and a publish made off that loop's thread hands the delivery
to it with ``call_soon_threadsafe``. The ring and the night log are still
appended at once, on the caller's thread, so the record never waits on a
loop. With no loop running a subscriber is served inline, as before, and one
whose loop has closed is skipped: its reader is gone.
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
#: events one subscriber may have queued before the bus drops its oldest. A
#: consumer this far behind is minutes behind (status alone is every 2 s), so
#: it is a socket whose sends are stuck, not a slow one.
SUBSCRIBER_MAX = 500
#: the ``type`` of the marker a subscription serves after a drop (#444). The
#: relay sends the same frame after its own drops (relay/relay/proxy.py
#: ``RELAY_GAP_FRAME``), so ui/src/ws.ts answers one notice whichever hop
#: lost the event.
RELAY_GAP = "relay_gap"


def _now() -> float:
    """Monotonic seconds — in one place so tests can drive both cooldowns."""
    return time.monotonic()


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The loop running on the calling thread, or None: a worker thread, or
    synchronous code with no loop running (#480)."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _wall() -> float:
    """Wall-clock seconds for an event's ``ts``, in one place for the same
    reason as ``_now``: a test has to be able to pin the moment a line was
    published, because for a ``site_derived`` line that moment IS the thing
    being withheld, and a scan that compares two sites needs every other
    moment held still."""
    return time.time()


#: The ``data`` key a bus event carries when the MOMENT it was published was
#: set by a site computation (spec 6.9, #166). Absent, never False, on every
#: other event, so existing payloads stay byte-identical.
SITE_DERIVED_KEY = "site_derived"


def is_site_derived(row: Any) -> bool:
    """Does this event carry the ``site_derived`` flag?

    ``row`` is anything shaped like ``Event.to_json()``: a ring row, a night
    file row, a WS frame. The ONE predicate every reader asks, so the ring,
    the night file, the exports and both WS lanes cannot disagree about which
    lines are timed by the site. A truthy value counts, not only ``True``: a
    flag some future writer spells ``1`` must fail closed, not open."""
    if not isinstance(row, dict):
        return False
    data = row.get("data")
    return isinstance(data, dict) and bool(data.get(SITE_DERIVED_KEY))


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

    def nights(self, *, unflagged_bytes_for: str | None = None
               ) -> list[dict[str, Any]]:
        """``[{night, bytes}]`` for every persisted night, newest first.

        ``unflagged_bytes_for`` names a night whose ``bytes`` counts only its
        lines NOT flagged ``site_derived`` (spec 6.9, #166): what a route
        passes, with the current night, for a principal without
        ``view.site_derived``. The file keeps flagged lines, so its size
        grows by one at the moment one is written, and a reader who polls the
        size has read that moment even with every row withheld. The count of
        the unflagged lines grows only when a line the reader may see lands.
        A past night's size is left as the file's: it no longer moves, so it
        dates nothing."""
        out: list[dict[str, Any]] = []
        try:
            for p in self.dir().glob("*.jsonl"):
                try:
                    size = (self._unflagged_bytes(p)
                            if p.stem == unflagged_bytes_for
                            else p.stat().st_size)
                    out.append({"night": p.stem, "bytes": size})
                except OSError:
                    continue
        except OSError:
            return []
        out.sort(key=lambda r: r["night"], reverse=True)
        return out

    @staticmethod
    def _unflagged_bytes(path: Path) -> int:
        """The bytes of ``path``'s lines that are not flagged ``site_derived``,
        newlines included. Only a line that mentions the key is parsed; one
        that does not parse is counted, because no reader is served it as a
        flagged row either."""
        n = 0
        with open(path, "rb") as fh:
            for raw in fh:
                if f'"{SITE_DERIVED_KEY}"'.encode() in raw:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        row = None
                    if is_site_derived(row):
                        continue
                n += len(raw)
        return n

    def read(self, night: str, *, level: str | None = None,
             limit: int = LOG_READ_MAX,
             include_site_derived: bool = True) -> list[dict[str, Any]]:
        """Rows for one night, oldest first, optionally filtered by ``level``.

        ``limit`` keeps the NEWEST ``limit`` rows (the tail is what a reader
        wants). A malformed line is skipped, never fatal.

        ``include_site_derived=False`` leaves out every row flagged
        ``site_derived`` (spec 6.9): what a route passes for a principal
        without ``view.site_derived``. The default reads what the file holds,
        which is everything. Filtered here, BEFORE ``limit``, so the newest N
        rows are the newest N the reader may see: sliced first, a viewer
        polling ``limit=1`` would see the answer go empty at the moment a
        flagged line landed, which is the moment the flag withholds."""
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
                    if not include_site_derived and is_site_derived(row):
                        continue
                    rows.append(row)
        except (OSError, ValueError):
            return []
        if limit and limit > 0 and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def export_text(self, night: str, *,
                    include_site_derived: bool = True) -> str:
        """The night as plain text, one ``HH:MM:SS [level · source] message`` per
        line — what a user actually wants to read/mail, not raw JSON.

        ``include_site_derived`` as for :meth:`read`: a transcript is the same
        rows with a timestamp printed on each, so a flagged row left in it is
        the same disclosure as in the JSON."""
        lines = []
        for row in self.read(night, include_site_derived=include_site_derived):
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
    # Through ``_wall`` and looked up at each call, rather than
    # ``default_factory=time.time``, which binds the function once at class
    # creation and so cannot be pinned by a test.
    ts: float = field(default_factory=lambda: _wall())

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "ts": self.ts}


class GapMarker(Event):
    """What a :class:`Subscription` serves in place of the events the bus
    dropped from it: one marker ahead of the first event after the hole.

    It serializes as ``{"type": "relay_gap"}`` and nothing more, the relay's
    own frame byte for byte. So both consumers forward it untouched (it has
    no ``data`` for ``_redact_ws_event`` to look into, and no ``ts``: a
    transport notice has no moment of its own to report), and the browser
    sees one shape from either hop. ``data`` is still an empty dict, for an
    in-process subscriber that reads ``ev.data`` without checking ``type``."""

    def __init__(self) -> None:
        super().__init__(type=RELAY_GAP, data={})

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type}


class Subscription(asyncio.Queue):
    """One subscriber's queue: bounded, drop-oldest, and it says when it
    dropped (#444).

    Each event is numbered as it ENTERS the queue (``_put``), and the reading
    side keeps the number it expects next (``_get``). When the queue is full,
    ``EventBus._deliver`` discards the oldest entry (:meth:`drop_oldest`),
    which leaves a hole in the numbers at the head, since the oldest is what
    goes. The next ``get()`` finds the hole and returns one
    :class:`GapMarker`, and the event behind the hole on the read after. One
    marker however many were dropped since the consumer last read; a later
    fall behind makes a new hole and earns another.

    Numbered on the way in because a number handed out on the way OUT goes
    only to what survived the queue, so it cannot show what did not. That is
    what the relay lane's ``seq`` is (``_run_ws``), and its docstring used to
    say it showed a drop.

    Built on the stdlib's extension points (``_put``/``_get``, as
    ``LifoQueue`` and ``PriorityQueue`` are), so ``get()``,
    ``wait_for(q.get(), ...)`` and every consumer's loop work as before, the
    LAN ``/ws`` and ``_run_ws`` among them, and each forwards the marker like
    any event. ``qsize()`` counts events; a marker owed takes no slot.

    ``home_loop`` is the loop the subscriber subscribed from, the one that
    will await ``get()``, or None when nothing was running (#480). It is kept
    per subscription, not once for the bus, because one process can run two
    live loops that both subscribe: the test client's portal thread and the
    test's own, and a subscriber belongs to the loop that reads it."""

    def __init__(self, maxsize: int = SUBSCRIBER_MAX,
                 loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(maxsize)
        #: the loop that reads this queue, or None (see the class docstring).
        #: Not ``_loop``: ``asyncio.Queue`` binds that one itself.
        self.home_loop = loop
        #: events ``EventBus._deliver`` has dropped from this queue, ever.
        self.dropped = 0
        self._seq = 0               # the number given to the last event queued
        self._next_seq = 1          # the number the reader expects next

    def _put(self, ev: Event) -> None:
        self._seq += 1
        self._queue.append((self._seq, ev))

    def _get(self) -> Event:
        seq, ev = self._queue[0]
        if seq != self._next_seq:
            # Everything numbered in between was dropped. The marker goes
            # first; the entry stays at the head for the next read, which now
            # expects it.
            self._next_seq = seq
            return GapMarker()
        self._queue.popleft()
        self._next_seq = seq + 1
        return ev

    def drop_oldest(self) -> None:
        """Discard the head entry without serving it. Not ``get_nowait()``:
        that is a read, so it would move the expected number past the entry
        and leave no hole to announce (and, with a hole already at the head,
        return the marker instead of dropping anything)."""
        self._queue.popleft()


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
        self._subscribers: set[Subscription] = set()
        self._history: deque[Event] = deque(maxlen=history)
        #: The same ring with no ``site_derived`` line in it, for a reader
        #: without ``view.site_derived`` (spec 6.9, #166). Filtering the ring
        #: above is not enough: a flagged line still takes a slot there and
        #: pushes the oldest row out, so once the ring is full a reader polling
        #: it sees its first row vanish with no new last row, at the flagged
        #: line's moment. Here a flagged line takes no slot, so nothing moves.
        self._history_unflagged: deque[Event] = deque(maxlen=history)
        #: rolling per-night disk log (UX #9). ``None`` disables persistence.
        self.night_log: NightLogWriter | None = NightLogWriter() if persist else None
        #: storm limiter: the open run of identical lines, and its counts.
        #: (level, message, source, site_derived) - see ``_storm_suppresses``.
        self._storm_key: tuple[Any, Any, Any, bool] | None = None
        self._storm_start = 0.0
        self._storm_seen = 0
        self._storm_dropped = 0
        #: guards the night-log notice against re-entering its own publish.
        self._in_notice = False

    def subscribe(self) -> Subscription:
        # The loop running here is the one that will read the queue: every
        # consumer subscribes from the coroutine that then awaits it (#480).
        q = Subscription(SUBSCRIBER_MAX, loop=_running_loop())
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, type: str, **data: Any) -> None:
        if type in ("focus", "filter_offsets"):
            from copy import deepcopy
            self.operation_snapshots[type] = {**deepcopy(data), "_observed_at": time.time()}
        # Any publish, not just a log one: if the failure happened on the last
        # log line of the night, the status events still flowing are what carry
        # the notice out. Any UNFLAGGED one (spec 6.9, #166): the notice is
        # delivered just before the event that carries it out, at that event's
        # moment, so carried out by a ``site_derived`` event it would put an
        # unflagged line in front of a viewer at exactly the moment the flag
        # withholds. It waits for the next event instead.
        if not data.get(SITE_DERIVED_KEY):
            self._drain_night_log_notice()
        if type == "log":
            if self._storm_suppresses(data):
                return
        self._deliver(Event(type=type, data=data))

    def _deliver(self, ev: Event) -> None:
        """Ring, disk, subscribers — the fan-out, past the storm limiter.

        The ring and the night file are written here, on the caller's
        thread, whichever it is: the record of a line never waits on a loop
        (#480). Only the subscribers' queues belong to a loop."""
        if ev.type == "log":
            self._history.append(ev)
            if not is_site_derived(ev.to_json()):
                self._history_unflagged.append(ev)
            if self.night_log is not None:
                self.night_log.append(ev)
        self._fan_out(ev)

    def _fan_out(self, ev: Event) -> None:
        """Put ``ev`` in every subscriber's queue, on that queue's loop.

        A subscriber whose loop is the one running here, or that has no loop,
        is served now, as always: that is every publish made on the loop, in
        order and synchronously. So is one whose loop is stopped, since no
        thread is running it to race. One whose loop is running on another
        thread is handed to that loop with ``call_soon_threadsafe``, which
        wakes it; a put from here would wake a parked ``get()`` through the
        non-thread-safe ``call_soon`` (#480). One handoff per loop, not per
        subscriber, so the loop is woken once and serves its subscribers in
        the order this publish reached them. One whose loop has closed is
        skipped: nothing can read it any more, and a put that woke a reader
        parked on it would raise ``Event loop is closed`` out of the
        publish."""
        here = _running_loop()
        handoff: dict[asyncio.AbstractEventLoop, list[Subscription]] = {}
        for q in list(self._subscribers):
            home = q.home_loop
            if home is None or home is here:
                self._offer(q, ev)
            elif home.is_closed():
                continue
            elif not home.is_running():
                self._offer(q, ev)
            else:
                handoff.setdefault(home, []).append(q)
        for home, subs in handoff.items():
            try:
                home.call_soon_threadsafe(self._offer_all, subs, ev)
            except RuntimeError:
                pass                    # closed since the check: skipped too

    def _offer_all(self, subs: list[Subscription], ev: Event) -> None:
        """A handed-off delivery, run on the subscribers' own loop. One that
        unsubscribed while the handoff waited is left alone."""
        for q in subs:
            if q in self._subscribers:
                self._offer(q, ev)

    @staticmethod
    def _offer(q: Subscription, ev: Event) -> None:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            # Slow consumer: drop the oldest to keep the stream live, and
            # count it on this subscriber (#444). The hole the drop leaves
            # in the subscription's numbers is what makes its next read a
            # GapMarker; the count is the running total, for anyone asking
            # how far behind this consumer has fallen.
            q.drop_oldest()
            q.dropped += 1
            q.put_nowait(ev)

    # -- storm limiter ---------------------------------------------------------

    def _storm_suppresses(self, data: dict[str, Any]) -> bool:
        """True when this line is a repeat that should be dropped.

        The first ``STORM_PASS`` identical lines (same level, message and
        source) inside a ``STORM_WINDOW_S`` window go through untouched — the
        first one is never delayed, which is the whole point of a log — and the
        rest are counted. The count is published as one summary line when the
        window closes: the next publish that is either a different line or the
        same line after the window, or an explicit :meth:`flush`. No timer, so
        nothing needs a running event loop.

        The ``site_derived`` flag is part of the key. The same words flagged
        and unflagged are two lines: counted as one, a flagged line arriving
        inside an unflagged run would be dropped into that run's summary, and
        the summary, unflagged and published after the flagged line came in,
        would tell a viewer that something was said at that moment."""
        key = (data.get("level"), data.get("message"), data.get("source"),
               bool(data.get(SITE_DERIVED_KEY)))
        now = _now()
        if key == self._storm_key and now - self._storm_start < STORM_WINDOW_S:
            self._storm_seen += 1
            if self._storm_seen <= STORM_PASS:
                return False
            self._storm_dropped += 1
            return True
        # This line closes the open window, so the summary is published now,
        # at this line's moment: a flagged line closing it flags the summary.
        self.flush(closed_by_site_derived=key[3])
        self._storm_key = key
        self._storm_start = now
        self._storm_seen = 1
        self._storm_dropped = 0
        return False

    def flush(self, *, closed_by_site_derived: bool = False) -> None:
        """Close the open storm window, publishing its summary if it has one.

        ``closed_by_site_derived``: the line that closes the window is flagged
        ``site_derived`` (spec 6.9, #166). The window has no timer, so its
        summary is published at the moment of whatever line closes it, and
        that line's moment is the site; an unflagged summary would tell a
        viewer that something was said then, whatever the storm's own words.
        So the summary carries the flag, and a viewer loses the summary along
        with the line; the night file keeps both."""
        key, dropped = self._storm_key, self._storm_dropped
        self._storm_key = None
        self._storm_dropped = 0
        self._storm_seen = 0
        if key is None or dropped <= 0:
            return
        level, message, source, flagged = key
        data = {
            "level": level,
            "message": f"{message} (repeated {dropped} more times in "
                       f"{STORM_WINDOW_S:.1f} s)",
            "source": source,
        }
        # The summary is published when the window closes, so its moment is
        # the flagged line's moment; it carries the flag or it discloses it.
        # The same when the storm is unflagged and a flagged line closes it.
        if flagged or closed_by_site_derived:
            data[SITE_DERIVED_KEY] = True
        self._deliver(Event(type="log", data=data))

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

    def log(self, level: str, message: str, source: str = "hub", *,
            site_derived: bool = False) -> None:
        """Publish one log line.

        ``site_derived=True`` says the line's MOMENT was set by a site
        computation (a meridian wait ending, a group flip, a flip at the
        crossing; spec 6.9) and puts ``data.site_derived = True`` on it. The
        key is written only then: an unflagged line's payload is exactly the
        three keys it always was, because every reader of a log payload is a
        reader of that shape."""
        if site_derived:
            self.publish("log", level=level, message=message, source=source,
                         **{SITE_DERIVED_KEY: True})
        else:
            self.publish("log", level=level, message=message, source=source)

    @property
    def log_history(self) -> list[dict[str, Any]]:
        return [e.to_json() for e in self._history]

    @property
    def log_history_unflagged(self) -> list[dict[str, Any]]:
        """The ring as a reader without ``view.site_derived`` may see it: the
        last lines that are not flagged ``site_derived``, from a ring of their
        own, so a flagged line neither shows nor evicts (see
        ``_history_unflagged``)."""
        return [e.to_json() for e in self._history_unflagged]


bus = EventBus(persist=_persist_default())
