"""Single-use, short-TTL tickets for authenticating a WebSocket (OPEN-011).

A browser cannot set an Authorization header on a WebSocket, so a shared token
had to ride the ``?token=`` query string -- and query strings leak into browser
history, telemetry, and proxy/access logs. This module lets an already
authenticated caller exchange that standing credential (over an authenticated
POST, never a query string) for a one-time ticket. The ticket satisfies the WS
shared-token gate exactly once and expires in seconds, so even if it lands in a
log it is inert.

The ticket is NOT a principal grant: the WS still resolves and re-checks the RBAC
principal from the session/provider. It only replaces the long-lived token in the
query carrier.
"""
from __future__ import annotations

import secrets
import threading
import time

#: Long enough for a client to mint-then-connect, short enough that a leaked
#: ticket in a log is useless.
DEFAULT_TTL_S = 30.0
#: Bound outstanding tickets so a mint flood cannot grow memory without limit.
_MAX_OUTSTANDING = 1024


class WsTicketStore:
    def __init__(self, *, ttl_s: float = DEFAULT_TTL_S,
                 max_outstanding: int = _MAX_OUTSTANDING):
        self._ttl = float(ttl_s)
        self._max = int(max_outstanding)
        self._lock = threading.Lock()
        self._tickets: dict[str, float] = {}  # ticket -> expiry (monotonic)

    def _prune(self, now: float) -> None:
        for t in [t for t, exp in self._tickets.items() if exp <= now]:
            del self._tickets[t]

    def issue(self, *, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            if len(self._tickets) >= self._max:
                # evict the soonest-to-expire to stay bounded under a flood.
                del self._tickets[min(self._tickets, key=self._tickets.get)]
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = now + self._ttl
            return ticket

    def consume(self, ticket: str, *, now: float | None = None) -> bool:
        """Redeem a ticket exactly once. False if unknown, already used, or
        expired."""
        if not ticket:
            return False
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            exp = self._tickets.pop(ticket, None)  # single-use: remove on read
            return exp is not None and exp > now

    def ttl_s(self) -> float:
        return self._ttl


#: Process-wide store (one WS gate per process).
_store = WsTicketStore()


def issue() -> str:
    return _store.issue()


def consume(ticket: str) -> bool:
    return _store.consume(ticket)


def ttl_s() -> float:
    return _store.ttl_s()
