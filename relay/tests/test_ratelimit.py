"""Per-IP token-bucket rate limiting (clock-injected, no sleeps)."""
from __future__ import annotations

from relay.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_burst_then_throttle():
    clk = Clock()
    rl = RateLimiter(rate=1.0, capacity=3.0, clock=clk)
    assert rl.allow("1.2.3.4") is True
    assert rl.allow("1.2.3.4") is True
    assert rl.allow("1.2.3.4") is True
    assert rl.allow("1.2.3.4") is False     # bucket drained


def test_refill_over_time():
    clk = Clock()
    rl = RateLimiter(rate=2.0, capacity=2.0, clock=clk)
    assert rl.allow("ip") and rl.allow("ip")
    assert rl.allow("ip") is False
    clk.t = 1.0                              # +1s -> +2 tokens (capped at 2)
    assert rl.allow("ip") is True
    assert rl.allow("ip") is True


def test_per_key_isolation():
    clk = Clock()
    rl = RateLimiter(rate=1.0, capacity=1.0, clock=clk)
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    assert rl.allow("b") is True             # a different IP has its own bucket


def test_prune_drops_idle_buckets():
    clk = Clock()
    rl = RateLimiter(rate=1.0, capacity=1.0, clock=clk, max_idle_s=10.0)
    rl.allow("a")
    clk.t = 100.0
    assert rl.prune() == 1
