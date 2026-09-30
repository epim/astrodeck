"""WP-04(a) (#549): a sink that never answers must not delay a healthy
sink's delivery of the same alert, and evicting a queued alert to make room
for a newer one must be counted and said, not silent.

Before the fix, `_send_loop` drained one shared outbox with a single sender
task: `_send_all` sent an alert to each of its sinks IN TURN, so a hung
webhook held a healthy ntfy sink's copy of the SAME alert behind it, and the
same serial loop meant a hung sink's own backlog of warnings held up every
LATER alert too -- an UNSAFE edge published after a burst of warnings still
waited behind them, minutes late on the real 10 s timeout. A full outbox's
eviction (which already favoured keeping a state change) was never counted
or reported.

THE FIX. `_fan_out` hands each alert to every targeted sink's own bounded
lane at once (`_SinkLane`, a deque plus an `asyncio.Event` -- synchronous to
append to); each lane is drained by its own task, one send at a time, so
only that sink's own backlog can ever delay it. A lane full of warnings
still gives way to a state change (never the reverse, unchanged from the
shared outbox's rule in `_enqueue`, which this file does not touch), and an
eviction is now counted and said once the lane has room again -- the
`_say_gap` pattern (#444) applied to a sink instead of the bus subscription.

This file only exercises the NEW per-sink fan-out path
(`AlertDispatcher.run` -> `_take` -> `_enqueue` -> `_send_loop` ->
`_fan_out`). The shared outbox's own eviction rule, and a single healthy
sink's in-order delivery, are already pinned by
test_h4_alerts_survive_a_hung_sink.py and are unaffected by this change (see
that file's `test_a_full_outbox_gives_up_a_warning_before_a_state_change...`
and `test_control_a_healthy_sink_gets_every_alert_once_in_order`, both still
green)."""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from astrodeck import alerting
from astrodeck.alerting import AlertDispatcher, AlertEvent
from astrodeck.config import AlertSink, AppConfig
from astrodeck.events import EventBus

#: The hung sink's timeout, patched down from 10 s: each send to it takes
#: this long to fail, as a real stalled host takes `_HTTP_TIMEOUT_S`.
TIMEOUT_S = 0.05
#: How long the healthy sink may take to see the UNSAFE edge. #549's own
#: text: "the healthy one must get the UNSAFE edge within one send of it
#: being published" -- generous next to one 0.05 s hung send, tight next to
#: the old FIFO-behind-everything-else behaviour.
DEADLINE_S = 1.0
UNSAFE = "UNSAFE: rain"


class _MixedTransport(httpx.AsyncBaseTransport):
    """Routes by host on ONE shared client, as both sinks really share the
    dispatcher's one `httpx.AsyncClient`: the "hung" host sleeps past its
    timeout and then fails, exactly how httpcore fails a real host gone
    quiet; the "ok" host answers at once."""

    def __init__(self) -> None:
        self.healthy_hits: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "hung.example.invalid":
            await asyncio.sleep(alerting._HTTP_TIMEOUT_S)
            raise httpx.ReadTimeout("never answers", request=request)
        self.healthy_hits.append(json.loads(request.content))
        return httpx.Response(200)


def _dispatcher(transport: _MixedTransport):
    hung = AlertSink(id="hung", kind="webhook",
                     url="https://hung.example.invalid/hook",
                     events=["warning", "safety"], min_level="warning")
    healthy = AlertSink(id="ok", kind="webhook",
                        url="https://ok.example.invalid/hook",
                        events=["warning", "safety"], min_level="warning")
    cfg = AppConfig(alerts=[hung, healthy])
    bus = EventBus(persist=False)
    disp = AlertDispatcher(bus, lambda: cfg)
    disp._client = httpx.AsyncClient(transport=transport,
                                     timeout=alerting._HTTP_TIMEOUT_S)
    return disp, bus


async def _eventually(pred, within: float) -> bool:
    end = time.monotonic() + within
    while not pred():
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(0.005)
    return True


async def _start(disp: AlertDispatcher, bus: EventBus):
    before = set(bus._subscribers)
    task = asyncio.create_task(disp.run())
    assert await _eventually(lambda: set(bus._subscribers) - before, 5.0)
    return task


async def _stop(disp: AlertDispatcher, task: asyncio.Task) -> None:
    await disp.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_a_hung_sink_does_not_delay_a_healthy_sinks_copy_of_the_same_alert(
        monkeypatch):
    """RED under the mutant that inlines the old serial send back into the
    sender loop (`_send_loop` doing ``await self._send_all(alert)`` again
    instead of ``self._fan_out(alert)``). Observed:

        E   AssertionError: the healthy sink did not see the UNSAFE edge
            within 1.0 s -- it waited behind the hung sink's own backlog
        E   assert False
    """
    monkeypatch.setattr(alerting, "_HTTP_TIMEOUT_S", TIMEOUT_S)
    transport = _MixedTransport()
    disp, bus = _dispatcher(transport)

    task = await _start(disp, bus)
    try:
        # A backlog for BOTH sinks well past the outbox's own cap
        # (_OUTBOX_MAX = 32, unchanged by this fix): the old serial
        # `_send_all` would still be working through roughly 31 of them
        # (each held ~TIMEOUT_S by the hung sink) when the edge is
        # published, ~1.5 s of unavoidable serial delay even though the
        # healthy sink itself never hangs.
        for i in range(50):
            bus.log("warning", f"guide star lost in frame {i}", "guide")
        published = time.monotonic()
        bus.publish("safety", is_safe=False, reason="rain", action="park")
        got = await _eventually(
            lambda: any(h.get("message") == UNSAFE for h in transport.healthy_hits),
            DEADLINE_S)
        took = time.monotonic() - published
    finally:
        await _stop(disp, task)
    assert got, (
        f"the healthy sink did not see the UNSAFE edge within {DEADLINE_S} s "
        f"-- it waited behind the hung sink's own backlog")
    assert took < DEADLINE_S


def test_a_congested_lane_counts_its_evictions_and_says_so_once(monkeypatch):
    """A lane bounded at 4 evicts warnings to keep a state change, and once
    it has drained back under that bound the dispatcher says, once, how many
    it gave up -- the `_say_gap` pattern (#444) applied to a sink's own
    lane instead of the bus subscription.

    RED under the mutant that drops the count (`_SinkLane.push`'s eviction
    branch ``return``s without touching ``self.evicted`` /
    ``self.eviction_owed``). Observed, verbatim:

        E   AssertionError: precondition: two evictions happened
        E   assert 0 == 2
        E    +  where 0 = <astrodeck.alerting._SinkLane object at ...>.evicted
    """
    monkeypatch.setattr(alerting, "_OUTBOX_MAX", 4)
    lane = alerting._SinkLane("s1")
    for i in range(4):
        lane.push(AlertEvent("warning", "warning", f"w{i}"))
    lane.push(AlertEvent("warning", "warning", "w4"))   # evicts w0
    lane.push(AlertEvent("warning", "warning", "w5"))   # evicts w1
    assert [a.message for a in lane.queue] == ["w2", "w3", "w4", "w5"]
    assert lane.evicted == 2, "precondition: two evictions happened"

    bus = EventBus(persist=False)
    disp = AlertDispatcher(bus, lambda: AppConfig())
    q = bus.subscribe()
    lane.queue.popleft()            # room now (3 of 4) -- as a lane task
                                     # sees once it pops one send
    disp._say_lane_eviction(lane)
    disp._say_lane_eviction(lane)   # a second call with nothing new: silent
    msgs = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log" and ev.data.get("level") == "warning":
            msgs.append(ev.data.get("message", ""))
    assert msgs == [
        "alert dispatcher dropped 2 queued alert(s) for sink s1: it could "
        "not keep up, 2 since it started"]


async def test_control_a_single_healthy_sink_still_delivers_in_order():
    """CONTROL: with only one sink (its own single lane), fan-out changes
    nothing observable -- alerts still arrive once, in the order published.
    Guards against a fan-out bug that reorders or drops within one lane."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content)["message"])
        return httpx.Response(200)

    sink = AlertSink(id="ok", kind="webhook", url="https://ok.example.invalid/hook",
                     events=["run_start", "run_end", "warning"],
                     min_level="warning")
    cfg = AppConfig(alerts=[sink])
    bus = EventBus(persist=False)
    disp = AlertDispatcher(bus, lambda: cfg)
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    task = await _start(disp, bus)
    try:
        bus.publish("sequence", state="running", _first_running=True,
                    plan_name="M31")
        bus.log("warning", "focuser slow", "focus")
        bus.publish("sequence", state="complete", end_reason="dawn",
                    plan_name="M31")
        await _eventually(lambda: len(seen) >= 3, 5.0)
    finally:
        await _stop(disp, task)
    assert seen == ["Run started: M31", "focuser slow", "Run ended: dawn"]
