# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Alerts survive a sink that never answers (#538), and the dispatcher acts on
the #444 ``relay_gap`` marker.

THE DEFECT. ``AlertDispatcher.run`` read the bus through an ordinary
subscription, bounded at ``SUBSCRIBER_MAX`` (500) and dropping its oldest
event when full, and it handled each event inline: ``_on_bus_event`` awaited
``_dispatch``, which awaited ``_send`` to each sink in turn, each up to
``_HTTP_TIMEOUT_S``. A hung webhook host and a warning more often than once
per timeout backed the subscription up past 500, and the bus dropped the
oldest events, an UNSAFE edge or a run_end among them, in silence. Since
#444 the subscription serves a ``relay_gap`` marker after a drop; the
dispatcher had no branch for it, so even that notice went nowhere.

THE FIX. The reader maps each event and puts its alert on an outbox bounded
apart (``_OUTBOX_MAX``), drained by a sender task, so the subscription is
read at bus speed whatever the sinks do. A full outbox gives up a warning
before a state change (UNSAFE, run_end), never the reverse. A retry pass
over what failed before gives way to a fresh alert. The marker is logged
once, at warning, naming ``Subscription.dropped``, when the subscription has
room for the line and for what one ``bus.log`` can bring with it (a storm
summary, a night-log notice: ``_GAP_ROOM``, #548). The browser answers the
same marker by re-reading the monitor snapshot
(#476, S7 orchestrator ruling 3); the dispatcher has no snapshot of the
events it missed, so it says that it missed them.

It is intermittent by nature (it needs a slow or hung sink and a warning
rate above one per timeout), so it is shown here with a sink that never
answers, as #538 asks, not with a normal night.

Each case names the mutants it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants were
applied in a private scratch copy of server/ (scratchpad/H4-H4-BUS-mut,
under the session's scratch root), never in the shared tree (#254):

* "sends inline on the reading loop": ``run`` awaits
  ``self._on_bus_event(ev)`` in place of ``self._take(ev)``, the loop as it
  was before the fix.
* "marker ignored": ``run``'s ``relay_gap`` branch removed, so the marker
  goes to ``_take`` and maps to no alert, as before the fix.
* "gap said at once": ``run`` says the gap the moment it reads the marker,
  into the full subscription the marker came from.
* "evict the oldest whatever it is": a full ``_enqueue`` gives up the head
  of the outbox, state change or not.
* "a warning evicts a state change": an outbox full of state changes gives
  up its oldest for an incoming warning.
* "outbox sends newest first": ``_send_loop`` takes ``pop()``, not
  ``popleft()``.
* "retry pass runs to the end": ``_retry_undelivered`` no longer stops for
  a fresh alert on the outbox.
* "gap said into one free slot": ``run`` says the owed gap once
  ``not q.full()``, as first written, not once ``_GAP_ROOM`` slots are free.
* "enqueue does not wake the sender": ``_enqueue`` appends without setting
  ``_outbox_ready``.
* "gap count is the running total": ``_say_gap`` names
  ``Subscription.dropped`` as the count missed.

The last three were found and shown RED by the task's verifier, in a
scratch copy of its own (scratchpad/H4-H4-BUS-verify-mut).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest

from astrodeck import alerting
from astrodeck.alerting import AlertDispatcher, AlertEvent
from astrodeck.config import AlertSink, AppConfig
from astrodeck.events import SUBSCRIBER_MAX, EventBus

#: The sink's timeout, patched down from 10 s: each send to the silent sink
#: takes this long to fail, as a real hung host takes ``_HTTP_TIMEOUT_S``.
TIMEOUT_S = 0.05
#: How long a case waits for the UNSAFE edge at the sink. The fixed code gets
#: it there within ``_OUTBOX_MAX`` sends (32 x 0.05 s = 1.6 s); the inline
#: reader either never does or needs ~500 of them.
DEADLINE_S = 10.0
#: How long a case's loop may run in all before the case calls it hung.
HANG_S = 60.0
#: How soon an idle sender must send an alert to a sink that answers. The
#: enqueue wakes it at once; a sender left to notice on its own wakes on its
#: one-second idle tick, so this sits between the two.
PROMPT_S = 0.5
UNSAFE = "UNSAFE: rain"


class _SilentSink(httpx.AsyncBaseTransport):
    """A webhook host that takes every request and never answers. Each
    request is recorded as it arrives (that is what reaching the sink
    means), then held for the client's read timeout and failed with
    ``ReadTimeout``, which is how httpcore fails a real host that has gone
    quiet: the timeout is the one the dispatcher's client puts on the
    request, so patching ``_HTTP_TIMEOUT_S`` is what makes it short."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.timeouts: set[float | None] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(json.loads(request.content))
        timeout = (request.extensions.get("timeout") or {}).get("read")
        self.timeouts.add(timeout)
        await asyncio.sleep(timeout if timeout is not None else HANG_S)
        raise httpx.ReadTimeout("timed out", request=request)

    def reached(self, message: str) -> bool:
        return any(body.get("message") == message for body in self.seen)


def _dispatcher(bus: EventBus, transport: httpx.AsyncBaseTransport,
                events: list[str]) -> tuple[AlertDispatcher, AlertSink]:
    """A dispatcher with one webhook sink wanting ``events``, whose client is
    built as ``_ensure_client`` builds it (the module's timeout) on
    ``transport``."""
    sink = AlertSink(id="hook", kind="webhook",
                     url="https://hook.example.invalid/alerts",
                     events=events, min_level="warning")
    cfg = AppConfig(alerts=[sink])
    disp = AlertDispatcher(bus, lambda: cfg)
    disp._client = httpx.AsyncClient(transport=transport,
                                     timeout=alerting._HTTP_TIMEOUT_S)
    return disp, sink


def _bounded(scenario):
    """Run ``scenario()`` on a loop of its own thread, and fail the case if
    it has not come back in HANG_S. A reader that never yields spins its
    loop forever, and a case awaiting on that loop would hang the run
    instead of failing (the "gap said at once" mutant does exactly that)."""
    out: dict[str, object] = {}

    def body() -> None:
        try:
            out["value"] = asyncio.run(scenario())
        except BaseException as exc:              # noqa: BLE001 - re-raised
            out["error"] = exc

    thread = threading.Thread(target=body, name="h4-alerts", daemon=True)
    thread.start()
    thread.join(HANG_S)
    if thread.is_alive():
        pytest.fail(f"the dispatcher's loop did not come back in {HANG_S:.0f} s: "
                    f"something on it spins without yielding")
    if "error" in out:
        raise out["error"]                        # type: ignore[misc]
    return out["value"]


async def _eventually(pred, within: float) -> bool:
    """Whether ``pred()`` came true within ``within`` seconds."""
    end = time.monotonic() + within
    while not pred():
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(0.005)
    return True


async def _start(disp: AlertDispatcher, bus: EventBus):
    """``disp.run()`` as a task, and the subscription it reads."""
    before = set(bus._subscribers)
    task = asyncio.create_task(disp.run())
    assert await _eventually(lambda: set(bus._subscribers) - before, 5.0)
    (sub,) = set(bus._subscribers) - before
    return task, sub


async def _stop(disp: AlertDispatcher, task: asyncio.Task) -> None:
    await disp.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_an_unsafe_edge_after_a_warning_burst_reaches_a_sink_that_never_answers(
        monkeypatch):
    """#538's case. The sink never answers; 600 distinct warnings arrive, each
    after a loop turn (a busy night, not a starved loop); then an UNSAFE edge;
    then the night goes on, 520 status events. The UNSAFE edge reaches the
    sink, and the reader's subscription dropped nothing: it was read at bus
    speed while the sender sat on the silent sink.

    RED under "sends inline on the reading loop". Observed:

        E   AssertionError: the UNSAFE edge never reached the sink in 10 s;
            it saw 10 requests, and the reader's subscription dropped 622
            events
        E   assert False
    """
    monkeypatch.setattr(alerting, "_HTTP_TIMEOUT_S", TIMEOUT_S)
    bus = EventBus(persist=False)
    sink = _SilentSink()
    disp, _cfg = _dispatcher(bus, sink, ["warning", "safety"])

    async def scenario():
        task, sub = await _start(disp, bus)
        try:
            for i in range(SUBSCRIBER_MAX + 100):
                bus.log("warning", f"guide star lost in frame {i}", "guide")
                await asyncio.sleep(0)
            bus.publish("safety", is_safe=False, reason="rain", action="park")
            for i in range(SUBSCRIBER_MAX + 20):
                bus.publish("status", i=i)
                await asyncio.sleep(0)
            reached = await _eventually(lambda: sink.reached(UNSAFE), DEADLINE_S)
        finally:
            await _stop(disp, task)
        return reached, sub.dropped

    reached, dropped = _bounded(scenario)
    assert reached, (
        f"the UNSAFE edge never reached the sink in {DEADLINE_S:.0f} s; it saw "
        f"{len(sink.seen)} requests, and the reader's subscription dropped "
        f"{dropped} events")
    assert dropped == 0
    assert sink.timeouts == {TIMEOUT_S}, "precondition: the patched timeout held"


def test_a_gap_in_the_readers_subscription_is_said_once_with_its_count(
        monkeypatch):
    """The loop starved: 536 distinct warnings and then an UNSAFE edge are
    published with no turn in between, so the reader cannot run and its
    subscription drops the oldest 37 whatever the reader does. Its next read
    is the #444 marker, and the dispatcher says so ONCE, at warning, with
    the count, and without that line dropping anything more; the UNSAFE
    edge, which survived the drop, still reaches the silent sink.

    RED under "marker ignored". Observed:

        E   AssertionError: assert [] == [{'level': 'w...ce': 'alert'}]
        E     Right contains one more item: {'level': 'warning', 'message':
              'alert dispatcher missed 37 bus event(s), 37 since it
              started; any alert among them was not sent', 'source':
              'alert'}

    RED under "sends inline on the reading loop" (499 warnings to send to
    the silent sink ahead of the edge, each failure publishing two more
    events into the full subscription). Observed:

        E   AssertionError: the UNSAFE edge did not reach the sink in 10 s;
            it saw 362 requests
        E   assert False

    RED under "gap said at once": the line drops the oldest event of the
    full queue, whose hole serves another marker, whose line drops the
    next, and the reader never yields again. Observed:

        E   Failed: the dispatcher's loop did not come back in 60 s:
            something on it spins without yielding
    """
    extra = 37
    monkeypatch.setattr(alerting, "_HTTP_TIMEOUT_S", TIMEOUT_S)
    bus = EventBus(history=5000, persist=False)
    sink = _SilentSink()
    disp, _cfg = _dispatcher(bus, sink, ["warning", "safety"])

    async def scenario():
        task, sub = await _start(disp, bus)
        try:
            for i in range(SUBSCRIBER_MAX + extra - 1):
                bus.log("warning", f"guide star lost in frame {i}", "guide")
            bus.publish("safety", is_safe=False, reason="rain", action="park")
            dropped_by_burst = sub.dropped
            reached = await _eventually(lambda: sink.reached(UNSAFE), DEADLINE_S)
        finally:
            await _stop(disp, task)
        return dropped_by_burst, reached, sub.dropped

    dropped_by_burst, reached, dropped_in_all = _bounded(scenario)
    assert dropped_by_burst == extra, "precondition: the burst overflowed"
    assert reached, (f"the UNSAFE edge did not reach the sink in "
                     f"{DEADLINE_S:.0f} s; it saw {len(sink.seen)} requests")
    gaps = [row["data"] for row in bus.log_history
            if row["data"].get("source") == "alert"
            and "missed" in row["data"].get("message", "")]
    assert gaps == [{
        "level": "warning",
        "message": f"alert dispatcher missed {extra} bus event(s), {extra} "
                   f"since it started; any alert among them was not sent",
        "source": "alert"}]
    assert dropped_in_all == extra, "saying the gap dropped more events"


def _gap_lines(bus: EventBus) -> list[str]:
    return [row["data"]["message"] for row in bus.log_history
            if row["data"].get("source") == "alert"
            and "missed" in row["data"].get("message", "")]


def test_the_gap_line_waits_for_room_for_all_one_log_can_put(monkeypatch):
    """The gap line is one ``bus.log``, and one ``bus.log`` can put up to
    three events on the subscription: the night-log writer's owed notice,
    the summary of the storm window the line closes, and the line. Said
    into the ONE slot that the first read after the marker frees, while a
    storm window held suppressed repeats, the summary took the slot and the
    line dropped the oldest event left, and that was the UNSAFE edge: it
    survived the bus's drop and was lost to the dispatcher saying so (#548).

    Three warnings, the UNSAFE edge, 495 more, then one line eight times
    (three pass the storm limiter, five wait for its summary), with no turn
    in between: 502 events for a queue of 500. The bus drops the first two,
    and after the reader's first read the edge is next in line.

    RED under "gap said into one free slot" (``run`` says the owed gap once
    ``not q.full()``, as first written). Observed:

        E   AssertionError: the UNSAFE edge did not reach the sink in 10 s;
            the subscription dropped 3 events, 2 of them before the reader
            ran
        E   assert False
    """
    monkeypatch.setattr(alerting, "_HTTP_TIMEOUT_S", TIMEOUT_S)
    bus = EventBus(history=5000, persist=False)
    sink = _SilentSink()
    disp, _cfg = _dispatcher(bus, sink, ["warning", "safety"])

    async def scenario():
        task, sub = await _start(disp, bus)
        try:
            for i in range(3):
                bus.log("warning", f"dew heater at {i} %", "power")
            bus.publish("safety", is_safe=False, reason="rain", action="park")
            for i in range(SUBSCRIBER_MAX - 5):
                bus.log("warning", f"guide star lost in frame {i}", "guide")
            for _ in range(8):
                bus.log("warning", "focuser not responding", "focus")
            by_burst = sub.dropped
            reached = await _eventually(lambda: sink.reached(UNSAFE), DEADLINE_S)
        finally:
            await _stop(disp, task)
        return by_burst, reached, sub.dropped

    by_burst, reached, in_all = _bounded(scenario)
    assert by_burst == 2, "precondition: the burst overflowed by two"
    assert reached, (
        f"the UNSAFE edge did not reach the sink in {DEADLINE_S:.0f} s; the "
        f"subscription dropped {in_all} events, {by_burst} of them before "
        f"the reader ran")
    assert in_all == by_burst, "saying the gap dropped more events"
    assert _gap_lines(bus) == [
        "alert dispatcher missed 2 bus event(s), 2 since it started; any "
        "alert among them was not sent"]


def test_a_second_gap_names_only_what_it_missed(monkeypatch):
    """Two starved bursts, the reader catching up in between: the second
    line names the second hole's count and the running total beside it.

    RED under "gap count is the running total" (``_say_gap`` names
    ``Subscription.dropped`` as the missed count). Observed:

        E   AssertionError: assert ['alert dispa...was not sent'] ==
            ['alert dispa...was not sent']
        E     At index 1 diff: 'alert dispatcher missed 42 bus event(s),
              42 since it started; any alert among them was not sent' !=
              'alert dispatcher missed 5 bus event(s), 42 since it started;
              any alert among them was not sent'
    """
    monkeypatch.setattr(alerting, "_HTTP_TIMEOUT_S", TIMEOUT_S)
    bus = EventBus(history=5000, persist=False)
    sink = _SilentSink()
    disp, _cfg = _dispatcher(bus, sink, ["warning", "safety"])

    async def scenario():
        task, sub = await _start(disp, bus)
        try:
            for i in range(SUBSCRIBER_MAX + 37):
                bus.log("warning", f"guide star lost in frame {i}", "guide")
            first = sub.dropped
            said = await _eventually(lambda: len(_gap_lines(bus)) == 1,
                                     DEADLINE_S)
            for i in range(SUBSCRIBER_MAX + 5):
                bus.log("warning", f"focuser slipped at step {i}", "focus")
            second = sub.dropped - first
            said_again = await _eventually(lambda: len(_gap_lines(bus)) == 2,
                                           DEADLINE_S)
        finally:
            await _stop(disp, task)
        return first, second, said, said_again

    first, second, said, said_again = _bounded(scenario)
    assert first == 37 and second > 0, "precondition: both bursts overflowed"
    assert said and said_again, f"gap lines: {_gap_lines(bus)}"
    tail = "any alert among them was not sent"
    assert _gap_lines(bus) == [
        f"alert dispatcher missed {first} bus event(s), {first} since it "
        f"started; {tail}",
        f"alert dispatcher missed {second} bus event(s), {first + second} "
        f"since it started; {tail}"]


def test_control_a_healthy_sink_gets_every_alert_once_in_order():
    """CONTROL: with a sink that answers, every alert goes out once, and a
    repeated warning inside the dedupe window is still sent once. Published
    with no turn in between, so the reader has queued all six before the
    sender takes the first, and (WP-04 follow-up, backlog W1) the sender
    itself drains the whole outbox into the one lane before that lane's own
    task gets a turn — a real backlog at the lane, not the "nothing queued"
    case. So this is no longer publish order: the lane drains by severity
    once it has a backlog (state changes, safety's UNSAFE included, ahead of
    plain warning/error alerts), FIFO within each tier. Re-pinned for that;
    it was publish order before the lane gained severity draining, verified
    by reproducing the six-alert scenario against the old plain-FIFO
    :meth:`_SinkLane.pop` (a bare ``popleft``), which gives back the order
    still named in the RED case below.

    RED under "outbox sends newest first". Observed:

        E   AssertionError: assert [('run_end', ...tarted: M31')] ==
            [('run_start'...ended: dawn')]
        E     At index 0 diff: ('run_end', 'Run ended: dawn') !=
              ('run_start', 'Run started: M31')

    RED under "enqueue does not wake the sender" (``_enqueue`` appends and
    leaves ``_outbox_ready`` unset, so the sender finds the alert only when
    its one-second idle wait runs out). Observed:

        E   AssertionError: the first alert took 1.00 s to leave an idle
            sender
        E   assert 1.0 < 0.5
    """
    seen: list[dict] = []
    first_at: list[float] = []

    def answers(request: httpx.Request) -> httpx.Response:
        if not first_at:
            first_at.append(time.monotonic())
        seen.append(json.loads(request.content))
        return httpx.Response(200)

    bus = EventBus(persist=False)
    disp, _cfg = _dispatcher(bus, httpx.MockTransport(answers),
                             ["run_start", "run_end", "safety", "warning",
                              "error"])

    async def scenario():
        task, _sub = await _start(disp, bus)
        try:
            published = time.monotonic()
            bus.publish("sequence", state="running", _first_running=True,
                        plan_name="M31")
            bus.log("warning", "focuser slow", "focus")
            bus.publish("safety", is_safe=False, reason="cloud", action="pause")
            bus.log("error", "guider lost the star", "guide")
            bus.publish("status", i=1)                   # maps to no alert
            bus.publish("safety", is_safe=True)
            bus.log("warning", "focuser slow", "focus")  # a repeat: deduped
            bus.publish("sequence", state="complete", end_reason="dawn",
                        plan_name="M31")
            await _eventually(lambda: len(seen) >= 6, DEADLINE_S)
            await asyncio.sleep(0.2)                     # and nothing after
        finally:
            await _stop(disp, task)
        return disp.undelivered_count, published

    undelivered, published = _bounded(scenario)
    assert first_at, "nothing reached the sink"
    took = first_at[0] - published
    assert took < PROMPT_S, (
        f"the first alert took {took:.2f} s to leave an idle sender")
    assert [(b["type"], b["message"]) for b in seen] == [
        ("run_start", "Run started: M31"),
        ("safety", "UNSAFE: cloud"),
        ("safety", "Conditions safe again"),
        ("run_end", "Run ended: dawn"),
        ("warning", "focuser slow"),
        ("error", "guider lost the star"),
    ]
    assert undelivered == 0


def test_a_full_outbox_gives_up_a_warning_before_a_state_change_never_the_reverse(
        monkeypatch):
    """The outbox's rule, at a bound of 4. Full, it gives up its oldest
    WARNING, though an UNSAFE edge is older. Full of state changes, it turns
    a warning away, and gives up its oldest state change for a newer one.

    RED under "evict the oldest whatever it is". Observed:

        E   AssertionError: assert ['w0', 'w1', 'w2', 'w3'] ==
            ['UNSAFE: rai...', 'w2', 'w3']
        E     At index 0 diff: 'w0' != 'UNSAFE: rain'

    RED under "a warning evicts a state change". Observed:

        E   AssertionError: assert ['UNSAFE: 1',...AFE: 3', 'w4'] ==
            ['UNSAFE: 0',..., 'UNSAFE: 3']
        E     At index 0 diff: 'UNSAFE: 1' != 'UNSAFE: 0'
    """
    monkeypatch.setattr(alerting, "_OUTBOX_MAX", 4)
    disp = AlertDispatcher(EventBus(persist=False), lambda: AppConfig())

    def box() -> list[str]:
        return [a.message for a in disp._outbox]

    warn = [AlertEvent("warning", "warning", f"w{i}") for i in range(5)]
    for alert in (AlertEvent("safety", "error", UNSAFE), *warn[:3]):
        disp._enqueue(alert)
    disp._enqueue(warn[3])
    assert box() == [UNSAFE, "w1", "w2", "w3"]
    disp._enqueue(AlertEvent("run_end", "error", "Run ended: rain"))
    assert box() == [UNSAFE, "w2", "w3", "Run ended: rain"]

    disp._outbox.clear()
    for i in range(4):
        disp._enqueue(AlertEvent("safety", "error", f"UNSAFE: {i}"))
    disp._enqueue(warn[4])
    assert box() == ["UNSAFE: 0", "UNSAFE: 1", "UNSAFE: 2", "UNSAFE: 3"]
    disp._enqueue(AlertEvent("run_end", "error", "Run ended: dawn"))
    assert box() == ["UNSAFE: 1", "UNSAFE: 2", "UNSAFE: 3", "Run ended: dawn"]


def test_a_retry_pass_gives_way_to_a_fresh_unsafe_edge(monkeypatch):
    """A long outage left the retry queue full (200 alerts for the silent
    sink). The sender, idle, starts a retry pass, 200 sends of 0.05 s; an
    UNSAFE edge published once it has begun waits for the one attempt in
    flight, not for the pass. At the real timeout the pass is over half an
    hour.

    RED under "retry pass runs to the end". Observed:

        E   AssertionError: the UNSAFE edge waited 9.44 s behind the retry
            pass
        E   assert 9.438000000081956 < 1.0
    """
    monkeypatch.setattr(alerting, "_HTTP_TIMEOUT_S", TIMEOUT_S)
    bus = EventBus(persist=False)
    sink = _SilentSink()
    disp, cfg_sink = _dispatcher(bus, sink, ["warning", "safety"])
    for i in range(alerting._UNDELIVERED_MAX):
        disp._undelivered.append(
            (cfg_sink, AlertEvent("warning", "warning", f"old warning {i}")))

    async def scenario():
        task, _sub = await _start(disp, bus)
        try:
            began = await _eventually(lambda: sink.seen, 5.0)
            published = time.monotonic()
            bus.publish("safety", is_safe=False, reason="rain", action="park")
            reached = await _eventually(lambda: sink.reached(UNSAFE), 15.0)
            waited = time.monotonic() - published
        finally:
            await _stop(disp, task)
        return began, reached, waited

    began, reached, waited = _bounded(scenario)
    assert began, "precondition: the idle sender began a retry pass"
    assert reached, "the UNSAFE edge never reached the sink"
    assert waited < 1.0, (
        f"the UNSAFE edge waited {waited:.2f} s behind the retry pass")
