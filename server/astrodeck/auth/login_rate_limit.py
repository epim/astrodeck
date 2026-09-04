"""Bounded, process-local throttling for local password authentication.

The limiter intentionally stores only keyed digests of normalized account
names.  It is suitable for AstroDeck's supported single-process deployment;
multi-worker deployments need an external atomic limiter instead.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import threading
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    attempts: deque[float] = field(default_factory=deque)
    last_seen: float = 0.0


class LoginAttemptLimiter:
    """Sliding-window account limiter with bounded attacker-controlled state."""

    def __init__(
        self,
        key_secret: bytes,
        *,
        max_failures: int = 10,
        window_s: float = 600,
        max_keys: int = 10_000,
        idle_ttl_s: float = 3600,
    ) -> None:
        if not isinstance(key_secret, bytes) or len(key_secret) < 16:
            raise ValueError("key_secret must contain at least 128 bits")
        if max_failures <= 0 or window_s <= 0 or max_keys <= 0 or idle_ttl_s <= 0:
            raise ValueError("limiter bounds must be positive")
        self._key_secret = key_secret
        self._max_failures = int(max_failures)
        self._window_s = float(window_s)
        self._max_keys = int(max_keys)
        self._idle_ttl_s = float(idle_ttl_s)
        self._buckets: dict[bytes, _Bucket] = {}
        self._overflow = _Bucket()
        self._lock = threading.Lock()

    def _digest(self, normalized_username: str) -> bytes:
        value = (normalized_username or "").strip().casefold().encode("utf-8")
        return hmac.new(self._key_secret, value, hashlib.sha256).digest()

    def _trim(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self._window_s
        while bucket.attempts and bucket.attempts[0] <= cutoff:
            bucket.attempts.popleft()

    def _prune_idle(self, now: float) -> None:
        cutoff = now - self._idle_ttl_s
        stale = []
        for digest, bucket in self._buckets.items():
            self._trim(bucket, now)
            if not bucket.attempts and bucket.last_seen <= cutoff:
                stale.append(digest)
        for digest in stale:
            self._buckets.pop(digest, None)
        self._trim(self._overflow, now)
        if not self._overflow.attempts and self._overflow.last_seen <= cutoff:
            self._overflow.last_seen = 0.0

    def begin_attempt(self, normalized_username: str, *, now: float) -> int | None:
        """Atomically reserve one verifier attempt or return ``Retry-After``.

        New names share one overflow bucket once the per-account table is full.
        No active account bucket is evicted to make space for attacker churn.
        """
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        digest = self._digest(normalized_username)
        with self._lock:
            self._prune_idle(now)
            bucket = self._buckets.get(digest)
            if bucket is None:
                if len(self._buckets) < self._max_keys:
                    bucket = _Bucket(last_seen=now)
                    self._buckets[digest] = bucket
                else:
                    bucket = self._overflow
            self._trim(bucket, now)
            bucket.last_seen = now
            if len(bucket.attempts) >= self._max_failures:
                remaining = bucket.attempts[0] + self._window_s - now
                return max(1, min(60, int(math.ceil(remaining))))
            bucket.attempts.append(now)
            return None

    def record_success(self, normalized_username: str) -> None:
        """Clear the successful account's dedicated history.

        A name routed through the shared overflow bucket cannot safely clear
        other names' failures, so overflow remains fail-closed until it ages.
        """
        digest = self._digest(normalized_username)
        with self._lock:
            self._buckets.pop(digest, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)
