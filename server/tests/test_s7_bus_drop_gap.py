# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Home-side fan-out drops are announced (S7-BUS, #444).

Every bus subscriber has its own bounded queue (``SUBSCRIBER_MAX``, 500), and
a consumer that falls that far behind loses the oldest event. Before #444 the
loss was silent: ``EventBus._deliver`` dropped with no count and no log line,
and the relay lane's ``seq``, which ``_run_ws``'s docstring said let the relay
or the browser detect a drop, was numbered AFTER the queue, so it stayed
contiguous across the very hole it was meant to show. A phone could lose a
``preview`` or a terminal ``sequence`` transition and nothing told it to
re-read the snapshot: the failure #399 fixed at the relay hop, one hop
earlier.

Now ``_deliver`` counts each drop on the subscriber it happened to, the
subscription numbers each event as it ENTERS the queue, and a hole in those
numbers makes the next ``get()`` serve one ``{"type": "relay_gap"}`` marker
ahead of the event behind it. That is the relay's own frame after its drops
(relay/relay/proxy.py ``RELAY_GAP_FRAME``), which ui/src/ws.ts answers with a
throttled snapshot read, and both consumers forward it through
``_redact_ws_event`` unchanged: the LAN ``/ws`` (api/app.py) and the tunnel's
``_run_ws``.

Named mutants, each run in a private scratch copy of server/ (the observed
failure is recorded in the test it turned red):

  M1  "no gap marker"            Subscription._get never serves the marker at
                                 a hole; it takes the entry as usual.
  M2  "seq assigned after the queue"
                                 Subscription._get numbers the entry as it
                                 LEAVES (seq = the number expected next)
                                 instead of reading the number _put gave it
                                 on the way in: the docstring's old claim,
                                 which cannot see a drop.
  M3  "drop through get_nowait"  _deliver drops the oldest with q.get_nowait()
                                 (the pre-#444 line), which counts as the
                                 consumer reading it and leaves no hole.
  M4  "marker without resync"    the marker branch does not move the expected
                                 number up to the entry behind the hole.
  M5  "one counter for every subscription"
                                 the numbering lives on the class, shared by
                                 every subscriber.
  M6  "numbered from zero"       a new subscription expects 0 first, so its
                                 first read finds a hole that is not there.
  M7  "one marker per subscription"
                                 a latch: after the first marker, later holes
                                 are passed over.
  M8  "drop not counted"         _deliver no longer counts the drop.
  M9  "redaction drops a frame with no data"
                                 api/redact.py _redact_ws_event returns None
                                 for an event without a ``data`` dict.
  M10 "relay lane rebuilds the frame"
                                 _run_ws sends {"type", "data", "ts"} built
                                 from the event's fields instead of
                                 ev.to_json().
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from astrodeck import events
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.events import SUBSCRIBER_MAX, EventBus
from astrodeck.remote.protocol import FrameType, decode_frame
from test_remote_relay import (FakeChannel, _FixedPrincipalProvider,
                               _make_client, _make_relay_client,
                               _wait_for_frame)

#: The marker as a consumer serializes it, and as the relay sends its own
#: (relay/relay/proxy.py ``RELAY_GAP_FRAME``). ui/src/ws.ts reads only
#: ``type``; the literal is pinned here, not imported, because it is the wire
#: contract with the browser and a renamed constant would break the browser
#: without breaking a test that imported it.
GAP = {"type": "relay_gap"}
GAP_BYTES = b'{"type":"relay_gap"}'


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


def _kind(ev) -> object:
    """One bus event as the lists below compare it: its ``i``, or ``"gap"``
    for a marker in the wire shape (a marker in any other shape says so)."""
    if ev.type == "relay_gap":
        body = ev.to_json()
        return "gap" if body == GAP else f"gap in another shape: {body!r}"
    return ev.data["i"]


def _drain(q, cap: int = SUBSCRIBER_MAX + 10) -> list:
    """Everything ``q`` holds, marker included, read with ``get_nowait``.
    Capped, so a queue that serves markers forever (M4) fails the test
    instead of hanging it."""
    out = []
    while not q.empty() and len(out) < cap:
        out.append(_kind(q.get_nowait()))
    return out


async def _until(pred, timeout: float = 5.0) -> None:
    async def _poll():
        while not pred():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(_poll(), timeout=timeout)


def _parked_run(extra: int) -> tuple[int, list]:
    """A consumer takes event 0 and parks (a socket whose send is stuck)
    while ``SUBSCRIBER_MAX + extra`` more are published, then catches up.
    Returns the subscription's drop count at release and what the consumer
    read, in order."""
    last = SUBSCRIBER_MAX + extra
    cap = last + 10                     # M4 serves markers forever

    async def scenario():
        bus = EventBus(persist=False)
        q = bus.subscribe()
        received: list = []
        took_first, gate = asyncio.Event(), asyncio.Event()

        async def consumer():
            while len(received) < cap:
                received.append(await q.get())
                if len(received) == 1:
                    took_first.set()
                    await gate.wait()
                await asyncio.sleep(0)

        task = asyncio.create_task(consumer())
        try:
            await asyncio.sleep(0)          # the consumer is waiting in get()
            bus.publish("status", i=0)
            await asyncio.wait_for(took_first.wait(), timeout=5.0)
            for i in range(1, last + 1):
                bus.publish("status", i=i)
            dropped = q.dropped
            gate.set()
            await _until(lambda: len(received) >= cap or (
                received[-1].type != "relay_gap"
                and received[-1].data.get("i") == last))
        finally:
            task.cancel()
        return dropped, [_kind(e) for e in received]

    return asyncio.run(scenario())


def test_a_parked_subscriber_sees_one_marker_before_its_next_event():
    """37 events past the bound while the consumer is parked: 37 drops
    counted, and the consumer reads event 0, ONE marker, then event 38, the
    oldest survivor, through to the last.

    RED under M1 "no gap marker", M2 "seq assigned after the queue" and M3
    "drop through get_nowait", each the same. Observed:

        E   AssertionError: assert [0, 38, 39, 40, 41, 42, ...] == [0, 'gap',
            38..., 40, 41, ...]
        E     At index 1 diff: 38 != 'gap'
        E     Right contains one more item: 537

    RED under M4 "marker without resync". Observed:

        E   AssertionError: assert [0, 'gap', 'g...', 'gap', ...] == [0,
            'gap', 38..., 40, 41, ...]
        E     At index 2 diff: 'gap' != 38
        E     Left contains 45 more items, first extra item: 'gap'

    RED under M6 "numbered from zero". Observed:

        E   AssertionError: assert ['gap', 'gap'..., 40, 41, ...] == [0,
            'gap', 38..., 40, 41, ...]
        E     At index 0 diff: 'gap' != 0

    RED under M8 "drop not counted". Observed:

        E   assert 0 == 37
    """
    extra = 37
    dropped, kinds = _parked_run(extra)
    assert kinds == [0, "gap"] + list(range(extra + 1, SUBSCRIBER_MAX + extra + 1))
    assert dropped == extra


def test_control_a_subscriber_exactly_full_behind_gets_no_marker():
    """CONTROL: the same parked consumer, with exactly ``SUBSCRIBER_MAX``
    events published while it is parked. The queue is full and nothing is
    dropped, so there is no count and no marker: event 0, then 1 to 500.

    RED under M5 "one counter for every subscription" (one subscriber here,
    but the class-level counter has moved on in the tests before it, so the
    first read finds a hole) and under M6 "numbered from zero", each the
    same. Observed:

        E   AssertionError: assert ['gap', 'gap'... 2, 3, 4, ...] == [0, 1,
            2, 3, 4, 5, ...]
        E     At index 0 diff: 'gap' != 0
        E     Left contains one more item: 500
    """
    dropped, kinds = _parked_run(0)
    assert kinds == list(range(0, SUBSCRIBER_MAX + 1))
    assert dropped == 0


def test_a_second_fall_behind_is_announced_again():
    """A subscription is not done with markers after its first. It falls 2
    behind before it ever reads (marker, then 2..501), catches up, then falls
    5 behind again: a second marker ahead of the second run's oldest
    survivor. Seven drops in all.

    RED under M7 "one marker per subscription". Observed:

        E   AssertionError: assert [507, 508, 50...511, 512, ...] == ['gap',
            507, ...510, 511, ...]
        E     At index 0 diff: 507 != 'gap'
        E     Right contains one more item: 1006

    RED under M1, M2 and M3, each the same (the first fall behind is already
    unannounced). Observed:

        E   AssertionError: assert [2, 3, 4, 5, 6, 7, ...] == ['gap', 2, 3,
            4, 5, 6, ...]
        E     At index 0 diff: 2 != 'gap'
        E     Right contains one more item: 501

    RED under M4 "marker without resync". Observed:

        E   AssertionError: assert ['gap', 'gap'...', 'gap', ...] == ['gap',
            2, 3, 4, 5, 6, ...]
        E     At index 1 diff: 'gap' != 2
        E     Left contains 9 more items, first extra item: 'gap'

    RED under M8 "drop not counted". Observed:

        E   assert 0 == 7
        E    +  where 0 = <Subscription at 0x1daaeff0530 maxsize=500
            tasks=1007>.dropped
    """
    bus = EventBus(persist=False)
    q = bus.subscribe()
    n = SUBSCRIBER_MAX + 2
    for i in range(n):
        bus.publish("status", i=i)
    first = _drain(q)
    for i in range(n, n + SUBSCRIBER_MAX + 5):
        bus.publish("status", i=i)
    second = _drain(q)
    assert first == ["gap"] + list(range(2, n))
    assert second == ["gap"] + list(range(n + 5, n + SUBSCRIBER_MAX + 5))
    assert q.dropped == 7


def test_a_drop_is_charged_to_the_subscriber_that_fell_behind():
    """CONTROL for the sibling: two subscribers on one bus, one reading after
    every publish and one never reading. The one that never read has 37
    drops and a marker first; the one that kept up has none and reads every
    event, in order, with no marker among them.

    RED under M5 "one counter for every subscription". Observed:

        E   AssertionError: assert ['gap', 0, 'g...'gap', 2, ...] == [0, 1,
            2, 3, 4, 5, ...]
        E     At index 0 diff: 'gap' != 0
        E     Left contains 537 more items, first extra item: 268

    RED under M6 "numbered from zero". Observed:

        E   AssertionError: assert ['gap', 0, 1, 2, 3, 4, ...] == [0, 1, 2,
            3, 4, 5, ...]
        E     At index 0 diff: 'gap' != 0
        E     Left contains one more item: 536

    RED on the slow side under M1, M2 and M3, each the same. Observed:

        E   AssertionError: assert [37, 38] == ['gap', 37]
        E     At index 0 diff: 37 != 'gap'

    RED under M4 "marker without resync". Observed:

        E   AssertionError: assert ['gap', 'gap'] == ['gap', 37]
        E     At index 1 diff: 'gap' != 37

    RED under M8 "drop not counted". Observed:

        E   assert (0, 0) == (37, 0)
        E     At index 0 diff: 0 != 37
    """
    bus = EventBus(persist=False)
    slow = bus.subscribe()
    fast = bus.subscribe()
    total = SUBSCRIBER_MAX + 37
    kept_up = []
    for i in range(total):
        bus.publish("status", i=i)
        kept_up.extend(_drain(fast))
    assert (slow.dropped, fast.dropped) == (37, 0)
    assert kept_up == list(range(total))
    assert _drain(slow)[:2] == ["gap", 37]


# ------------------------------------------------------------ the two lanes

class _ParkedChannel(FakeChannel):
    """The relay WSS with an uplink that can stall. While ``held``, a WS_DATA
    send waits for ``release``; ``parked`` is set once one is waiting, so the
    test knows ``_run_ws`` is stuck mid-send with an event in hand and every
    further publish lands in its bus subscription.

    ``done`` is set once event ``last`` is sent, or once the lane has sent
    more WS_DATA frames than were ever published, when the send fails as a
    dead uplink would. That bound is what lets a lane that never stops
    sending (M4: markers forever, none of them yielding) fail in seconds with
    the list it sent: unbounded, M4 took this test six minutes to time out."""

    def __init__(self, last: int) -> None:
        super().__init__()
        self.held = False
        self.parked = asyncio.Event()
        self.release = asyncio.Event()
        self.done = asyncio.Event()
        self.last = last
        self._ws_data = 0

    async def send(self, data: bytes) -> None:
        frame = decode_frame(data)
        if frame.type == FrameType.WS_DATA:
            self._ws_data += 1
            if self._ws_data > self.last + 50:
                self.done.set()
                raise ConnectionError("the lane sent more than was published")
            if self.held:
                self.parked.set()
                await self.release.wait()
        await super().send(data)
        if (frame.type == FrameType.WS_DATA
                and _payload_kind(frame.payload) == self.last):
            self.done.set()


def _payload_kind(payload: bytes) -> object:
    """One WS_DATA payload as the relay test compares it: ``"hello"``, an
    event's ``i``, or ``"gap"`` for the marker byte for byte."""
    if payload == GAP_BYTES:
        return "gap"
    obj = json.loads(payload)
    if obj.get("type") == "relay_gap":
        return f"gap in another shape: {payload!r}"
    if obj.get("type") == "hello":
        return "hello"
    return obj["data"]["i"]


async def _relay_lane(app, extra: int) -> tuple[int, list]:
    """One tunneled ``/ws`` opened as a viewer (so every frame goes through
    the redaction branches, not the all-caps shortcut). The uplink stalls
    with event 0 in hand while ``SUBSCRIBER_MAX + extra`` more are
    published, then clears. Returns the lane's subscription's drop count and
    the WS_DATA payloads it sent, in order."""
    bus = events.bus
    last = SUBSCRIBER_MAX + extra
    channel = _ParkedChannel(last)
    client = _make_relay_client(app, channel)
    before = set(bus._subscribers)
    channel.push_frame(FrameType.WS_OPEN, 7,
                       {"path": "/ws", "query": "", "ws_id": "wsGap"})
    task = asyncio.create_task(client._serve_once(client._config()))
    try:
        await _wait_for_frame(channel, lambda f: f.type == FrameType.WS_DATA)
        (sub,) = set(bus._subscribers) - before
        channel.held = True
        bus.publish("status", i=0)
        await asyncio.wait_for(channel.parked.wait(), timeout=5.0)
        for i in range(1, last + 1):
            bus.publish("status", i=i)
        dropped = sub.dropped
        channel.release.set()
        await asyncio.wait_for(channel.done.wait(), timeout=5.0)
    finally:
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)
    return dropped, [_payload_kind(f.payload) for f in channel.sent_frames()
                     if f.type == FrameType.WS_DATA]


@pytest.mark.parametrize("extra", [37, 0], ids=["dropped", "control-full"])
def test_the_relay_lane_forwards_the_marker(tmp_path, monkeypatch, extra):
    """The tunnel's ``_run_ws``: a viewer's uplink stalls with event 0 in
    hand. With 37 past the bound, 37 drops are counted on the lane's own
    subscription and the relay is sent hello, event 0, the marker as the
    relay's own frame byte for byte, then 38 onward. CONTROL (``extra=0``):
    exactly full, nothing dropped, no marker.

    RED (dropped) under M1, M2 and M3, each the same, and under M9
    "redaction drops a frame with no data", the same again. Observed:

        E   AssertionError: assert ['hello', 0, ..., 40, 41, ...] ==
            ['hello', 0, ..., 39, 40, ...]
        E     At index 2 diff: 38 != 'gap'
        E     Right contains one more item: 537

    RED (dropped) under M10 "relay lane rebuilds the frame". Observed:

        E   assert ['hello', 0, ..., 39, 40, ...] == ['hello', 0, ..., 39,
            40, ...]
        E     At index 2 diff: 'gap in another shape:
            b\\'{"type":"relay_gap","data":{},"ts":1790645264.1513531}\\''
            != 'gap'

    RED (dropped) under M4 "marker without resync". Observed:

        E   AssertionError: assert ['hello', 0, ...', 'gap', ...] ==
            ['hello', 0, ..., 39, 40, ...]
        E     At index 3 diff: 'gap' != 38
        E     Left contains 84 more items, first extra item: 'gap'

    RED (dropped) under M8 "drop not counted". Observed:

        E   assert 0 == 37

    RED under M5 "one counter for every subscription" and M6 "numbered from
    zero", both cases, each the same. Observed (control-full):

        E   AssertionError: assert ['hello', 'ga... 1, 2, 3, ...] ==
            ['hello', 0, 1, 2, 3, 4, ...]
        E     At index 1 diff: 'gap' != 0
        E     Left contains one more item: 500
    """
    _store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("viewer")))
    dropped, kinds = asyncio.run(_relay_lane(app, extra))
    last = SUBSCRIBER_MAX + extra
    if extra:
        assert kinds == ["hello", 0, "gap"] + list(range(extra + 1, last + 1))
    else:
        assert kinds == ["hello"] + list(range(0, last + 1))
    assert dropped == extra


def test_the_lan_ws_forwards_the_marker_unchanged(tmp_path, monkeypatch):
    """The LAN ``/ws`` (api/app.py, unedited): its subscription is already 3
    past the bound when it connects, the state a parked consumer is in when
    its send clears. A viewer reads hello, then the marker exactly as the
    relay sends it, then event 3, the oldest survivor.

    The subscription is filled on a private bus and handed to the handler in
    place of ``bus.subscribe()``'s, because the handler runs on the test
    client's own loop in another thread: publishing 500 events at it from
    here would race its reads and could leave nothing dropped.

    RED under M1, M2 and M3, each the same, and under M9 "redaction drops a
    frame with no data", the same again. Observed (M1):

        E   AssertionError: assert {'data': {'i'...pe': 'status'} ==
            {'type': 'relay_gap'}
        E     Differing items:
        E     {'type': 'status'} != {'type': 'relay_gap'}
        E     Left contains 2 more items:
        E     {'data': {'i': 3}, 'ts': 1790645220.796708}

    RED under M4 "marker without resync" (the marker, then more markers).
    Observed:

        E   assert [None, None] == [3, 4]
        E     At index 0 diff: None != 3

    RED under M8 "drop not counted", at the precondition. Observed:

        E   AssertionError: assert 0 == 3
        E    +  where 0 = <Subscription at 0x1daaf7ad040 maxsize=500
            _queue=[(4, Event(type='status', data={'i': 3}, ...
    """
    _store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("viewer")))
    behind = EventBus(persist=False)
    q = behind.subscribe()
    for i in range(SUBSCRIBER_MAX + 3):
        behind.publish("status", i=i)
    assert q.dropped == 3               # precondition: the hole is there
    monkeypatch.setattr(events.bus, "subscribe", lambda: q)
    with TestClient(app).websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        got = [ws.receive_json() for _ in range(3)]
    assert got[0] == GAP
    assert [g.get("data", {}).get("i") for g in got[1:]] == [3, 4]
