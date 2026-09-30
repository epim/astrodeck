"""WP-04(b) (#542): `SequenceEngine._frame_alerts_tick`'s two calls must
never block the frame loop, even when the configured monitor or a sink
never answers.

Before the fix, `deadman_ping` awaited `client.get(url)` directly and
`emit_heartbeat` awaited `_dispatch` (which awaits every sink), each up to
`_HTTP_TIMEOUT_S` (10 s). A stalled monitor -- a stalled Uptime-Kuma, a DNS
resolver that hangs -- therefore held every frame of a run for up to 10 s,
twice over when a heartbeat was also due (#538 fixed the same shape in the
dispatcher's bus reader; this path is the engine's own, so that fix does not
reach it).

THE FIX. Both calls now check whether the outbox pipeline is live
(`_outbox_ready is not None`, set by `run()`): live, `deadman_ping` fires
the real GET as its own background task (a single-flight guard means a
still-running ping just absorbs the next tick) and `emit_heartbeat` only
dedupes and enqueues -- neither one awaits a sink. `engine.py` is not
edited: `_frame_alerts_tick` still calls the very same two dispatcher
methods it always has.

A bare dispatcher (no `run()`, the shape every test in test_alerting.py and
test_unattended_night_guards_audit.py builds) keeps the exact old inline
behaviour, proved by the control test below, so this fix does not touch
those files.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from astrodeck.alerting import AlertDispatcher
from astrodeck.config import AppConfig
from astrodeck.events import EventBus
from astrodeck.sequence.engine import SequenceEngine

#: `_frame_alerts_tick` must return comfortably inside one frame's budget,
#: not merely inside the old 10 s timeout.
DEADLINE_S = 0.1
#: Safety net so a regression that restores the old blocking behaviour fails
#: fast with a clear message instead of hanging the whole test run.
HANG_GUARD_S = 5.0


class _HungTransport(httpx.AsyncBaseTransport):
    """A monitor host that takes every request and never answers -- the
    #542 shape (a stalled watchdog, a resolver that hangs), not a refused
    connection."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(3600)
        raise httpx.ReadTimeout("never answers", request=request)  # pragma: no cover


def _live_dispatcher() -> AlertDispatcher:
    """A dispatcher marked live (as `run()` leaves it) without starting
    `run()`'s own loops: `_frame_alerts_tick` only needs `_outbox_ready` set
    to take the enqueue-and-return path, and this test never waits for
    actual delivery."""
    disp = AlertDispatcher(EventBus(persist=False), lambda: AppConfig(
        deadman_url="http://monitor.example.invalid/ping"))
    disp._client = httpx.AsyncClient(transport=_HungTransport())
    disp._outbox_ready = asyncio.Event()
    return disp


def _bare_engine(disp: AlertDispatcher) -> SequenceEngine:
    """A `SequenceEngine` carrying only what `_frame_alerts_tick` and
    `_get_dispatcher` read -- the same `__new__` + hand-set-attrs pattern
    test_alerts_have_a_destination.py uses for the same class."""
    eng = SequenceEngine.__new__(SequenceEngine)
    eng.dispatcher = disp
    eng.hub = None
    eng._plan = None            # bypass the plan.setter: it re-derives a
                                 # policy from self._cfg, which this bare
                                 # engine never gets (_frame_alerts_tick only
                                 # reads the plan getter)
    eng._frames_done = 3
    return eng


async def test_frame_alerts_tick_returns_within_100ms_against_a_hung_sink():
    """RED under the mutant that reverts `deadman_ping`/`emit_heartbeat` to
    always run inline (drop the ``self._outbox_ready is None`` branch, so
    `deadman_ping` always does ``await self._deadman_ping_now()`` and
    `emit_heartbeat` always does ``await self._dispatch(alert)``). Under the
    mutant, `_frame_alerts_tick` hangs on the never-answering transport past
    this test's own ``HANG_GUARD_S`` outer bound, so the observed failure is
    that bound firing rather than the inner 0.1 s assertion (the mutant
    never reaches it). Observed, verbatim (pytest's own lines, long ones
    wrapped):

        >               raise TimeoutError from exc_val
        E               TimeoutError

        ..\\..\\..\\..\\..\\..\\..\\..\\Programs\\Python\\Python312\\Lib\\asyncio\\timeouts.py:115: TimeoutError
    """
    disp = _live_dispatcher()
    eng = _bare_engine(disp)
    try:
        started = time.monotonic()
        await asyncio.wait_for(eng._frame_alerts_tick(), timeout=HANG_GUARD_S)
        took = time.monotonic() - started
    finally:
        if disp._deadman_task is not None:
            disp._deadman_task.cancel()
        await disp._client.aclose()
    assert took < DEADLINE_S, (
        f"_frame_alerts_tick took {took:.2f} s (limit {DEADLINE_S} s): a "
        f"hung monitor is holding the frame loop again (#542)")


async def test_a_bare_dispatcher_still_pings_the_deadman_inline():
    """CONTROL: with no `run()` pipeline (`_outbox_ready is None`, the shape
    every other alerting test builds), `deadman_ping` must still perform the
    real GET inline and return its actual result -- #542's fix must not
    silently stop the dead-man's-switch from working outside a live
    dispatcher."""
    hits = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(str(req.url))
        return httpx.Response(200)

    disp = AlertDispatcher(EventBus(persist=False), lambda: AppConfig(
        deadman_url="https://hc-ping.example.invalid/abc"))
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert disp._outbox_ready is None, "precondition: no run() pipeline"
    await disp.deadman_ping()
    assert hits == ["https://hc-ping.example.invalid/abc"]
    assert disp._deadman_task is None, "the bare path must not spawn a task"
    await disp._client.aclose()
