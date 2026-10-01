"""An event the tunnel cannot send owes the viewer a gap marker (#485).

After S7-BUS (#444) every home-side drop but one was announced. The one left
was ``RelayClient._send_ws_event``: an event whose JSON is over
``DEFAULT_MAX_PAYLOAD`` (64 KiB), or that cannot be serialised at all, makes
``encode_frame`` raise, and the event was dropped for that remote viewer with
a throttled log line and nothing sent to the browser. The LAN ``/ws`` has no
such ceiling, so a LAN viewer and a remote viewer of the same rig could
disagree with no signal on the remote side: a large mosaic's ``sequence``
state, or the ``hello`` summary itself, lost until the next event of its
type.

Now the drop owes the viewer the ``{"type":"relay_gap"}`` marker, the bytes
the bus and the relay already send after their own drops, and it goes ahead
of the next frame that does encode. The browser answers it by re-reading the
monitor snapshot over the HTTP tunnel, which chunks a large body, so the
state the frame could not carry arrives that way. The marker takes the
dropped event's ``seq``, so the numbers the relay records stay increasing.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of server/
(scratchpad/H4-H4-RELAY-mut in the session scratchpad), from a byte backup
restored with its sha256 checked. The observed failure is recorded in the
case it turned red.

  M1 "marker removed"            the owed marker is cleared and never sent.
  M2 "marker after the event"    the marker is sent behind the frame that
                                 encoded instead of ahead of it.
  M3 "owed forever"              the debt is never cleared, so every later
                                 frame carries a marker.
  M4 "every frame owes"          a marker ahead of every frame, dropped or
                                 not.
  M5 "a bus marker doubled"      the owed marker is sent even when the next
                                 frame is itself a relay_gap marker.
  M6 "marker takes the event's number"
                                 the marker is sent with the encoding
                                 frame's ``seq`` rather than the dropped
                                 one's.
"""
from __future__ import annotations

import asyncio
import json

from astrodeck.auth import principal_for_role, set_active_provider
from astrodeck.remote.protocol import DEFAULT_MAX_PAYLOAD, FrameType, decode_frame
from astrodeck.remote.relay_client import _WsSendGuard

from _deadline import wait_until
from test_remote_relay import (FakeChannel, _FixedPrincipalProvider,
                               _make_client, _make_relay_client,
                               _wait_for_frame)

#: The marker as the relay sends it after its own drops (relay/relay/proxy.py
#: ``RELAY_GAP_FRAME``) and the bus serves it after a subscriber's
#: (events.GapMarker). Pinned as a literal, as test_s7_bus_drop_gap pins it:
#: it is the wire contract with ui/src/ws.ts.
GAP_BYTES = b'{"type":"relay_gap"}'

#: An event body well past the 64 KiB frame ceiling.
_BLOB = ["x" * 100] * 1000
assert len(json.dumps(_BLOB)) > DEFAULT_MAX_PAYLOAD


def _kind(payload: bytes) -> str:
    """One WS_DATA payload as these cases compare it: ``"gap"`` for the
    marker byte for byte, ``"hello"``, or the event's ``data.marker``."""
    if payload == GAP_BYTES:
        return "gap"
    obj = json.loads(payload)
    if obj.get("type") == "relay_gap":
        return f"gap in another shape: {payload!r}"
    if obj.get("type") == "hello":
        return "hello"
    return str(obj.get("data", {}).get("marker"))


def _ws_data(channel):
    return [f for f in channel.sent_frames() if f.type == FrameType.WS_DATA]


async def _viewer_lane(app, publish, last: str) -> list:
    """Open one tunnelled ``/ws``, wait for its hello, run ``publish(bus)``,
    wait for the event marked ``last`` to go out, and return the WS_DATA
    frames the lane sent, in order."""
    from astrodeck.events import bus

    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    channel.push_frame(FrameType.WS_OPEN, 4,
                       {"path": "/ws", "query": "", "ws_id": "ws4"})
    task = asyncio.create_task(client._serve_once(client._config()))
    try:
        await _wait_for_frame(channel, lambda f: f.type == FrameType.WS_DATA)
        publish(bus)
        await _wait_for_frame(
            channel, lambda f: f.type == FrameType.WS_DATA
            and f.payload != GAP_BYTES
            and json.loads(f.payload).get("data", {}).get("marker") == last)
    finally:
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)
    assert not any(f.type == FrameType.WS_CLOSE for f in channel.sent_frames()), \
        "a dropped event must not close the viewer's stream"
    return _ws_data(channel)


def test_an_oversize_event_is_followed_by_the_marker_then_the_next_event(
        tmp_path, monkeypatch, bus_lines):
    """A >64 KiB event, then two small ones, on a tunnelled viewer. The
    viewer gets hello, the marker, then both small events: the marker
    arrives before the first frame that encodes, once.

    RED under M1 "marker removed". Observed:
        AssertionError: assert ['hello', 'AFTER1', 'AFTER2'] == ['hello',
        'gap', 'AFTER1', 'AFTER2']
    RED under M2 "marker after the event". Observed:
        AssertionError: assert ['hello', 'AFTER1', 'gap', 'AFTER2'] ==
        ['hello', 'gap', 'AFTER1', 'AFTER2']
    RED under M3 "owed forever". Observed:
        AssertionError: assert ['hello', 'gap', 'AFTER1', 'gap', 'AFTER2']
        == ['hello', 'gap', 'AFTER1', 'AFTER2']
    RED under M4 "every frame owes". Observed:
        AssertionError: assert ['gap', 'hello', 'gap', 'AFTER1', 'gap',
        'AFTER2'] == ['hello', 'gap', 'AFTER1', 'AFTER2']
    RED under M6 "marker takes the event's number". Observed:
        AssertionError: the marker must take the dropped event's number, so
        the relay's seq stays increasing: [1, 3, 3, 4]
        assert [1, 3, 3, 4] == [1, 2, 3, 4]
    """
    _store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("admin")))

    def publish(bus):
        bus.publish("status", marker="OVERSIZE", blob=_BLOB)
        bus.publish("status", marker="AFTER1")
        bus.publish("status", marker="AFTER2")

    frames = asyncio.run(_viewer_lane(app, publish, "AFTER2"))
    kinds = [_kind(f.payload) for f in frames]
    assert kinds == ["hello", "gap", "AFTER1", "AFTER2"]
    seqs = [f.header["seq"] for f in frames]
    # hello 1, the dropped event 2 (the marker's), AFTER1 3, AFTER2 4.
    assert seqs == [1, 2, 3, 4], (
        "the marker must take the dropped event's number, so the relay's "
        f"seq stays increasing: {seqs}")
    # The rig's own log still says it, once (the throttled line). Captured
    # through bus_lines, which also keeps that line off the lane: on the bus
    # it would be one more event for this very viewer.
    drops = [m for lvl, m, src in bus_lines
             if src == "remote" and "dropped" in m]
    assert len(drops) == 1 and "dropped 1 " in drops[0], bus_lines


def test_control_small_events_carry_no_marker(tmp_path, monkeypatch, bus_lines):
    """CONTROL: nothing dropped, nothing owed.

    RED under M4 "every frame owes". Observed:
        AssertionError: assert ['gap', 'hello', 'gap', 'A', 'gap', 'B'] ==
        ['hello', 'A', 'B']
    Green under M1, M2, M3, M5 and M6, as a control should be: with nothing
    dropped there is no debt for them to mishandle.
    """
    _store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("admin")))

    def publish(bus):
        bus.publish("status", marker="A")
        bus.publish("status", marker="B")

    frames = asyncio.run(_viewer_lane(app, publish, "B"))
    assert [_kind(f.payload) for f in frames] == ["hello", "A", "B"]
    assert [f.header["seq"] for f in frames] == [1, 2, 3]


def test_an_oversize_hello_is_owed_to_the_first_event(tmp_path, monkeypatch,
                                                     bus_lines):
    """The issue's second shape: the ``hello`` rides the same send, so a
    ``hub.summary()`` past 64 KiB was dropped and a remote viewer rendered
    nothing until the next ``status``. Now the first event is preceded by
    the marker, and the browser's snapshot read fills the view.

    RED under M1 "marker removed". Observed:
        AssertionError: assert ['FIRST'] == ['gap', 'FIRST']
    RED under M2 "marker after the event". Observed:
        AssertionError: assert ['FIRST', 'gap'] == ['gap', 'FIRST']
    """
    _store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("admin")))
    from astrodeck.hub import hub
    monkeypatch.setattr(hub, "summary", lambda: {"blob": _BLOB})

    async def scenario():
        from astrodeck.events import bus

        channel = FakeChannel()
        client = _make_relay_client(app, channel)
        channel.push_frame(FrameType.WS_OPEN, 4,
                           {"path": "/ws", "query": "", "ws_id": "ws4"})
        before = set(bus._subscribers)
        task = asyncio.create_task(client._serve_once(client._config()))
        try:
            # No hello goes out to wait for, so wait for the subscription,
            # on a wall-clock deadline (#610): 500 x sleep(0.01) is 5 s on
            # Linux but 7.8 s on Windows (sleep rounds up to the 15.6 ms
            # timer there), so a round count gives the two platforms
            # different real patience.
            await wait_until(lambda: set(bus._subscribers) - before,
                             timeout_s=10.0, interval_s=0.01)
            assert set(bus._subscribers) - before, "the viewer never subscribed"
            await asyncio.sleep(0.05)   # let the hello's send run and drop
            bus.publish("status", marker="FIRST")
            await _wait_for_frame(
                channel, lambda f: f.type == FrameType.WS_DATA
                and f.payload != GAP_BYTES)
        finally:
            channel.finish()
            await asyncio.wait_for(task, timeout=5.0)
        return _ws_data(channel)

    frames = asyncio.run(scenario())
    assert [_kind(f.payload) for f in frames] == ["gap", "FIRST"]


class _Wire:
    """The minimum ``_raw_send_on`` needs: a socket that records frames."""

    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _direct_client():
    wire = _Wire()
    client = _make_relay_client(None, FakeChannel())
    client._ws = wire
    return client, wire


def test_an_unserialisable_event_owes_the_marker_too(bus_lines):
    """The other way ``encode_frame`` fails: JSON cannot serialise the event
    (a set here). Driven straight through ``_send_ws_event``, as the lane
    would call it.

    RED under M1 "marker removed". Observed:
        AssertionError: assert ['OK'] == ['gap', 'OK']
    RED under M2 "marker after the event". Observed:
        AssertionError: assert ['OK', 'gap'] == ['gap', 'OK']
    RED under M6 "marker takes the event's number". Observed:
        assert [2, 2] == [1, 2]
    """
    client, wire = _direct_client()
    guard = _WsSendGuard()

    async def scenario():
        await client._send_ws_event(4, "ws4", 1, {"type": "status",
                                                  "data": {"s": {1, 2}}},
                                    guard, wire)
        assert wire.sent == [], "the unserialisable event was not dropped"
        await client._send_ws_event(4, "ws4", 2, {"type": "status",
                                                  "data": {"marker": "OK"}},
                                    guard, wire)

    asyncio.run(scenario())
    frames = [decode_frame(raw) for raw in wire.sent]
    assert [_kind(f.payload) for f in frames] == ["gap", "OK"]
    assert [f.header["seq"] for f in frames] == [1, 2]
    assert all(f.header["ws_id"] == "ws4" and f.stream_id == 4 for f in frames)


def test_a_marker_from_the_bus_pays_the_debt(bus_lines):
    """When the next frame that encodes is itself a relay_gap (the bus
    dropped from this subscriber too), it is the notice: the viewer gets one
    marker, not two in a row.

    RED under M5 "a bus marker doubled". Observed:
        AssertionError: assert ['gap', 'gap', 'LATER'] == ['gap', 'LATER']
    RED under M3 "owed forever" and M4 "every frame owes", each with the
    same line: the marker in front of the bus's, or in front of LATER.
    """
    client, wire = _direct_client()
    guard = _WsSendGuard()

    async def scenario():
        await client._send_ws_event(4, "ws4", 1, {"type": "status",
                                                  "data": {"blob": _BLOB}},
                                    guard, wire)
        await client._send_ws_event(4, "ws4", 2, {"type": "relay_gap"},
                                    guard, wire)
        await client._send_ws_event(4, "ws4", 3, {"type": "status",
                                                  "data": {"marker": "LATER"}},
                                    guard, wire)

    asyncio.run(scenario())
    frames = [decode_frame(raw) for raw in wire.sent]
    assert [_kind(f.payload) for f in frames] == ["gap", "LATER"]
