"""Per-IP token-bucket rate limiting (basic DoS dampening at the relay edge).

The relay is the public front door, so it applies a coarse per-client-IP token
bucket BEFORE a request is tunnelled. This is edge hygiene, NOT authorization --
the home re-decides every request regardless. Two separate limiters in practice:
a generous one for HTTP requests and a tighter one for new ``/ws`` opens
(a WS open is cheap for the browser but pins a tunnel stream).

Pure + clock-injectable so it unit-tests with a fake clock (no sleeps).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """A per-key token bucket: ``rate`` tokens/sec, burst capacity ``capacity``.

    ``allow(key)`` returns True and consumes a token if one is available, else
    False (the caller returns HTTP 429). Stale buckets are pruned lazily so the
    map does not grow without bound under churning client IPs."""

    def __init__(self, rate: float = 10.0, capacity: float = 20.0, *,
                 clock: Callable[[], float] = time.monotonic,
                 max_idle_s: float = 300.0):
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._max_idle_s = max_idle_s
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        """Consume ``cost`` tokens for ``key``; True iff they were available."""
        now = self._clock()
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(tokens=self.capacity, updated=now)
            self._buckets[key] = b
        else:
            # Refill since last seen, capped at capacity.
            elapsed = now - b.updated
            b.tokens = min(self.capacity, b.tokens + elapsed * self.rate)
            b.updated = now
        if b.tokens >= cost:
            b.tokens -= cost
            return True
        return False

    def prune(self) -> int:
        """Drop buckets idle longer than ``max_idle_s``. Returns the count
        removed. Call periodically from the server's housekeeping loop."""
        now = self._clock()
        stale = [k for k, b in self._buckets.items()
                 if (now - b.updated) > self._max_idle_s]
        for k in stale:
            del self._buckets[k]
        return len(stale)
