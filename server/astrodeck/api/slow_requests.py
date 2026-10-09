# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Slow-request log (#858).

On 2026-10-07 the UI said "server not responding" (one request ran past its
15 s budget, ``ui/src/api.ts``) while the server logged nothing but two slow
device-fingerprint writes. Without a record of request duration the next
occurrence could not name its route. This middleware writes one log line for
every HTTP request whose FIRST BYTE takes ``SLOW_REQUEST_S`` or longer.

Why each design point:

* Pure ASGI, not ``@app.middleware("http")``. A ``BaseHTTPMiddleware`` wraps
  the response in its own stream, which changes when the first byte leaves and
  how cancellation travels. A plain ASGI callable sees the exact
  ``http.response.start`` message and lets a ``CancelledError`` through
  untouched. It is registered last in ``create_app`` so it is the OUTERMOST
  user middleware: it times the auth and header layers too, and a
  relay-tunnelled request (the relay client replays into this same app) is
  timed exactly as a LAN one.
* Time to the first byte, not to the end of the body. The UI's timeout and the
  relay's ``upstream_timeout_s`` both wait for the head. A long stream (MJPEG,
  an export) is not slow just for being long. The first-byte time is taken
  before ``send`` is awaited, so a slow client socket is not counted either.
  The request is SETTLED at its first byte: it leaves the in-flight count, its
  stall figure stops there, and its line is written then. An open stream is
  therefore not counted as in flight for its whole life and does not keep the
  probe ticking.
* A request still waiting at the UI's budget is named THEN, by the probe
  (``still waiting after N s``), not only when it ends. On the LAN nothing
  cancels an abandoned handler, so one that never answers (a deadlock, a
  worker thread stuck in I/O) would otherwise write no line at all. Such a
  request always gets its closing line too, past the cooldown its own first
  line started.
* The route TEMPLATE (``/api/flows/{flow_id}``), never the raw path and never
  the query string. ``?token=`` carries ``ASTRODECK_TOKEN`` (#550), and a raw
  path is whatever a client typed. An unrouted request gets a fixed label.
* A cooldown per (method, route, level). A screen polling one route through a
  stall would otherwise write a line every poll; each line is itself a disk
  append on the event loop (#858 N2), and the bus's alert dedupe never merges
  two of these lines because each carries its own duration. The next line for
  a key carries the count of the lines it stood for (``+N earlier``).
* A loop-lateness probe that runs only while at least one HTTP request is
  waiting for its first byte (a ``call_later`` chain every ``LOOP_TICK_S``).
  An idle server pays nothing. A stall figure counts only lateness after the
  request entered: lateness from a stall that began earlier is left out. A ``TimerHandle`` left pending on a loop that has closed is dropped
  silently, where a pending Task would warn, which is why it is not a Task.
* The bookkeeping is guarded. Telemetry must never replace a handler's answer
  or its exception, so a failure in ``_enter``, ``_leave`` or the report is
  swallowed, and ``_leave`` releases the in-flight count and the probe in its
  own ``finally``.

How to read a line. ``loop stall N s`` present: something blocked the event
loop itself (sync disk I/O on the loop, for example), and every request in
flight waited for it. ``loop stall`` absent: the loop ran. The request waited
on its own work, OR on a worker thread queued behind a lock or a busy default
executor (#858 N6: ``/api/status`` waits for the fingerprint write). "No loop
stall" does not mean the route's own code is slow.

The line is a log line only (drawer and night file). It carries no figures in
any fixed-words field, names no operator action and no movement, and never
contains a raw path, a query string, a client address or any coordinate.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

from .. import events
from ..remote.relay_client import scope_is_remote

#: A request whose first byte takes this long is logged. Measured right after
#: the #858 incident: /api/status, /api/flows and /api/sessions all answered in
#: under 0.25 s (relay included); 3.0 s = 12 x 0.25 s, and a fifth of the UI's
#: 15 s budget, so a stall building toward the timeout is logged long before
#: the UI gives up.
SLOW_REQUEST_S = 3.0
#: One line per (method, route, level) per this many seconds; repeats in
#: between are counted. A 2 s poller through a stall: 30 lines a minute -> 1.
SLOW_REQUEST_REPEAT_S = 60.0
#: The UI's own budget. Keep in step with ``timeoutFor`` in ui/src/api.ts. At
#: or past it the UI has already shown "server not responding", so the line is
#: a warning; below it, info.
UI_REQUEST_BUDGET_S = 15.0
#: The longer UI budgets, copied from ui/src/api.ts ``timeoutFor``.
_UI_LONG_BUDGETS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"/api/connect/(nina|alpaca|phd2)"), 130.0),
    (re.compile(r"/api/discover"), 30.0),
)
#: Event-loop lateness probe period, run only while a request is in flight.
LOOP_TICK_S = 0.25
#: Loop lateness at or above this is named in the line: 4 tick periods, 64 x
#: the 15.6 ms Windows timer resolution, a third of SLOW_REQUEST_S.
LOOP_STALL_REPORT_S = 1.0
SLOW_REQUEST_SOURCE = "http"
UNROUTED_LABEL = "(unrouted)"
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def ui_budget_s(path: str) -> float:
    """The UI's timeout for a request to ``path``, in seconds."""
    for pattern, budget in _UI_LONG_BUDGETS:
        if pattern.search(path):
            return budget
    return UI_REQUEST_BUDGET_S


def route_label(scope: dict) -> str:
    """The route TEMPLATE the request matched, or a fixed label.

    FastAPI's ``APIRoute.matches`` writes ``scope["route"]``; the StaticFiles
    mount writes none, so its requests get ``/assets/*``. Never the raw path
    and never the query string."""
    route = scope.get("route")
    p = getattr(route, "path", None)
    if isinstance(p, str) and p:
        return p
    if str(scope.get("path", "")).startswith("/assets/"):
        return "/assets/*"
    return UNROUTED_LABEL


def format_slow_line(*, method: str, label: str, outcome: str, elapsed_s: float,
                     stall_s: float, remote: bool, others: int,
                     earlier: int) -> str:
    """One slow-request log line."""
    head = f"slow request {method} {label}: {outcome} after {elapsed_s:.1f} s"
    tail: list[str] = []
    if stall_s >= LOOP_STALL_REPORT_S:
        tail.append(f"loop stall {stall_s:.1f} s")
    if remote:
        tail.append("relay")
    if others > 0:
        tail.append(f"{others} in flight")
    if earlier > 0:
        tail.append(f"+{earlier} earlier")
    return head + (f" ({', '.join(tail)})" if tail else "")


class _Waiting:
    """A request still waiting for its first byte, as the probe sees it."""

    __slots__ = ("scope", "started", "checked", "warned")

    def __init__(self, scope: dict, started: float) -> None:
        self.scope = scope
        self.started = started
        # True once the probe has dealt with this request's budget crossing.
        self.checked = False
        # True once this request's "still waiting" line was written.
        self.warned = False


class SlowRequestLog:
    """Pure-ASGI middleware: log every HTTP request slower than SLOW_REQUEST_S
    to its first byte (#858). See the module docstring."""

    def __init__(self, app: Any, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self._clock = clock
        self._in_flight = 0
        # Key: (method, label, level). Bounded by the code: 8 method values x
        # the route templates plus 2 fixed labels, x 2 levels.
        self._last: dict[tuple[str, str, str], float] = {}
        self._held: dict[tuple[str, str, str], int] = {}
        # token -> [loop time at entry, worst lateness seen]
        self._watch: dict[int, list[float]] = {}
        # token -> the request, until its first byte (or its end)
        self._waiting: dict[int, _Waiting] = {}
        self._next_token = 0
        self._tick_handle: asyncio.TimerHandle | None = None
        self._tick_due = 0.0
        self._tick_loop: asyncio.AbstractEventLoop | None = None

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = self._clock()
        first_byte: float | None = None
        waiting: _Waiting | None = None
        # [stall, others] once the request has left the probe, else None.
        figures: list[Any] | None = None
        reported = False

        def leave() -> None:
            # Runs ONCE per request: at the first byte, or at the end when no
            # byte was ever sent. The request leaves the in-flight count here,
            # so an open stream (MJPEG, an export) is not counted as in flight
            # for its whole life and does not keep the probe ticking.
            nonlocal figures
            if figures is not None:
                return
            figures = [0.0, 0]
            if token is not None:
                try:
                    figures[:] = self._leave(token)
                except Exception:
                    pass

        def report(outcome: str) -> None:
            nonlocal reported
            if reported:
                return
            reported = True
            leave()
            stall, others = figures if figures is not None else (0.0, 0)
            end = first_byte if first_byte is not None else self._clock()
            try:
                # A request whose "still waiting" line was written always gets
                # its closing line, so the route's outcome is never held
                # behind the cooldown its own first line started.
                self._maybe_report(scope, outcome, end - started, stall, others,
                                   force=waiting is not None and waiting.warned)
            except Exception:
                pass

        async def timed_send(message):
            nonlocal first_byte
            if first_byte is None and message.get("type") == "http.response.start":
                # Stamped BEFORE the send is awaited: a slow client socket is
                # not the server being slow. The line is written AFTER the
                # head has gone, so its own disk append (#858 N2) never delays
                # the answer it describes.
                first_byte = self._clock()
                leave()
                try:
                    await send(message)
                finally:
                    report(str(message.get("status")))
                return
            await send(message)

        try:
            token: int | None = self._enter()
        except Exception:
            token = None
        if token is not None:
            waiting = _Waiting(scope, started)
            self._waiting[token] = waiting
        outcome = "failed"
        try:
            await self.app(scope, receive, timed_send)
            outcome = "no answer"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            report(outcome)

    # ------------------------------------------------------------ loop probe

    def _enter(self) -> int:
        loop = asyncio.get_running_loop()
        token = self._next_token
        self._next_token += 1
        self._watch[token] = [loop.time(), 0.0]
        try:
            if self._tick_handle is None or self._tick_loop is not loop:
                # A handle on another loop is dropped without cancelling: that
                # loop has already stopped (a finished TestClient portal).
                self._tick_loop = loop
                self._tick_due = loop.time() + LOOP_TICK_S
                self._tick_handle = loop.call_later(LOOP_TICK_S, self._tick)
            self._in_flight += 1
        except BaseException:
            self._watch.pop(token, None)
            raise
        return token

    def _tick(self) -> None:
        loop = self._tick_loop
        if loop is None:
            return
        try:
            late = loop.time() - self._tick_due
            for entry in self._watch.values():
                entry[1] = max(entry[1], late)
        except Exception:
            pass
        try:
            self._report_waiting()
        except Exception:
            pass
        finally:
            self._tick_due = loop.time() + LOOP_TICK_S
            self._tick_handle = loop.call_later(LOOP_TICK_S, self._tick)

    def _report_waiting(self) -> None:
        """A request that has waited past the UI's budget is named NOW, from
        the probe's own clock, not when it ends: on the LAN nothing cancels
        an abandoned handler, so one that never answers (a deadlock, a worker
        thread stuck in I/O) would otherwise never write a line at all."""
        now = self._clock()
        for token, w in list(self._waiting.items()):
            if w.checked:
                continue
            elapsed = now - w.started
            if elapsed < ui_budget_s(str(w.scope.get("path", ""))):
                continue
            entry = self._watch.get(token)
            stall = entry[1] if isinstance(entry, list) else 0.0
            # Checked first: a line the cooldown holds (or that cannot be
            # written) is not tried again at 4 Hz for the rest of the request,
            # which would also inflate the next line's "+N earlier".
            w.checked = True
            w.warned = self._maybe_report(
                w.scope, "still waiting", elapsed, stall,
                max(0, self._in_flight - 1))

    def _leave(self, token: int) -> tuple[float, int]:
        try:
            self._waiting.pop(token, None)
            entry = self._watch.pop(token, None)
            if entry is None:
                return 0.0, max(0, self._in_flight - 1)
            entry_t, worst = entry
            loop = asyncio.get_running_loop()
            if loop is self._tick_loop and self._tick_handle is not None:
                # A stall that ended just before this request finished, before
                # the overdue tick had a turn to run. The min keeps lateness
                # from before this request entered out of the figure.
                now = loop.time()
                pending = min(now - self._tick_due, now - entry_t)
                worst = max(worst, pending, 0.0)
            return worst, self._in_flight - 1
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            if self._in_flight == 0 and self._tick_handle is not None:
                self._tick_handle.cancel()
                self._tick_handle = None

    # ---------------------------------------------------------------- report

    def _maybe_report(self, scope, outcome: str, elapsed: float, stall: float,
                      others: int, *, force: bool = False) -> bool:
        """Write the line unless it is fast or the cooldown holds it; True
        when a line was written. `force` skips the cooldown (and leaves its
        bookkeeping alone): the closing line of a request whose "still
        waiting" line was written."""
        if elapsed < SLOW_REQUEST_S:
            return False
        method = str(scope.get("method", "")).upper()
        if method not in _METHODS:
            method = "OTHER"
        label = route_label(scope)
        # The budget match reads the raw path but never logs it.
        level = ("warning" if elapsed >= ui_budget_s(str(scope.get("path", "")))
                 else "info")
        key = (method, label, level)
        earlier = 0
        if not force:
            now = self._clock()
            if key in self._last and now - self._last[key] < SLOW_REQUEST_REPEAT_S:
                self._held[key] = self._held.get(key, 0) + 1
                return False
            earlier = self._held.pop(key, 0)
            self._last[key] = now
        events.bus.log(level, format_slow_line(
            method=method, label=label, outcome=outcome, elapsed_s=elapsed,
            stall_s=stall, remote=scope_is_remote(scope), others=others,
            earlier=earlier), SLOW_REQUEST_SOURCE)
        return True
