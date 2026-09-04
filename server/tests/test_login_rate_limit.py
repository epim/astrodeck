"""Regression tests for the bounded local-password account limiter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from astrodeck.auth.login_rate_limit import LoginAttemptLimiter


def _limiter(**overrides) -> LoginAttemptLimiter:
    options = {
        "max_failures": 3,
        "window_s": 10.0,
        "max_keys": 2,
        "idle_ttl_s": 20.0,
    }
    options.update(overrides)
    return LoginAttemptLimiter(b"k" * 32, **options)


def test_sliding_window_denies_then_expires_and_success_resets():
    limiter = _limiter()
    assert limiter.begin_attempt("Alice", now=0.0) is None
    assert limiter.begin_attempt(" alice ", now=1.0) is None
    assert limiter.begin_attempt("ALICE", now=2.0) is None
    assert limiter.begin_attempt("alice", now=3.0) == 7

    limiter.record_success("aLiCe")
    assert limiter.begin_attempt("alice", now=3.0) is None

    # The oldest attempt expires at the exact end of the sliding window.
    assert limiter.begin_attempt("bob", now=0.0) is None
    assert limiter.begin_attempt("bob", now=1.0) is None
    assert limiter.begin_attempt("bob", now=2.0) is None
    assert limiter.begin_attempt("bob", now=10.0) is None


def test_parallel_attempt_reservation_cannot_overshoot_the_limit():
    limiter = _limiter(max_failures=5, max_keys=10)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(
            lambda _: limiter.begin_attempt("same-account", now=5.0),
            range(20),
        ))
    assert results.count(None) == 5
    assert sum(result is not None for result in results) == 15


def test_attacker_controlled_name_churn_is_bounded_and_fail_closed():
    limiter = _limiter(max_failures=2, max_keys=2)
    assert limiter.begin_attempt("known-a", now=0.0) is None
    assert limiter.begin_attempt("known-b", now=0.0) is None
    for index in range(100):
        limiter.begin_attempt(f"attacker-{index}", now=1.0)

    assert len(limiter) == 2
    # New names share a saturated overflow bucket; changing names cannot create
    # unbounded state or recover a fresh verifier attempt.
    assert limiter.begin_attempt("never-seen-before", now=1.0) is not None
    assert "known-a" not in repr(vars(limiter))
