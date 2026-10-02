# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""§T7(4) fan-out isolation + per-browser bounded egress buffer (W3.3.3/W3.3.5).

A ``WS_DATA`` for ws_id A goes ONLY to browser A. A SLOW browser's egress buffer
overflows and it is DISCONNECTED while a sibling on the SAME home keeps receiving
ALL events (the relay's read of the scope WSS never stalls)."""
from __future__ import annotations

import asyncio

import pytest

from relay import protocol
from relay.proxy import TunnelMultiplexer
from relay.registry import HomeRegistration

from conftest import FakeBrowserWS, FakeScopeTunnel


def _mux(tunnel, *, egress_max=200):
    reg = HomeRegistration(home_id="h1", generation=1, tunnel=tunnel)
    return TunnelMultiplexer(reg, ws_egress_max=egress_max)


async def _settle():
    # Let the per-viewer pump tasks run.
    for _ in range(5):
        await asyncio.sleep(0)


async def test_ws_open_emits_ws_open_frame(fake_tunnel):
    mux = _mux(fake_tunnel)
    browser = FakeBrowserWS()
    ws_id = await mux.open_ws(browser, "/ws", "", [["origin", "x"]])
    opens = fake_tunnel.of_type(protocol.FrameType.WS_OPEN)
    assert len(opens) == 1
    assert opens[0].header["ws_id"] == ws_id


async def test_ws_data_fans_only_to_its_browser(fake_tunnel):
    mux = _mux(fake_tunnel)
    a = FakeBrowserWS()
    b = FakeBrowserWS()
    ws_a = await mux.open_ws(a, "/ws", "", [])
    ws_b = await mux.open_ws(b, "/ws", "", [])

    await mux.on_tunnel_frame(protocol.ws_data(0, ws_a, 1, b'{"type":"status","v":1}'))
    await mux.on_tunnel_frame(protocol.ws_data(0, ws_b, 1, b'{"type":"status","v":2}'))
    await _settle()

    assert a.received == ['{"type":"status","v":1}']
    assert b.received == ['{"type":"status","v":2}']


async def test_orphan_ws_data_is_benign_noop(fake_tunnel):
    """A WS_DATA for an unknown/already-closed ws_id is the EXPECTED close race
    (the home streams trailing frames until WS_CLOSE reaches it), NOT a protocol
    violation. It must be DROPPED silently -- raising would propagate out of the
    scope read loop and tear down the whole shared home tunnel."""
    mux = _mux(fake_tunnel)
    # Must not raise (previously raised ProxyError, killing the tunnel).
    await mux.on_tunnel_frame(protocol.ws_data(0, "ws-nope", 1, b"{}"))


async def test_ws_data_after_close_does_not_tear_down_tunnel(fake_tunnel):
    """The end-to-end race: a browser /ws drops (close_ws pops the viewer + sends
    WS_CLOSE), then trailing in-flight WS_DATA for that ws_id arrives. Those frames
    must be dropped without raising, so a sibling viewer's stream is unaffected."""
    mux = _mux(fake_tunnel)
    gone = FakeBrowserWS()
    sibling = FakeBrowserWS()
    ws_gone = await mux.open_ws(gone, "/ws", "", [])
    ws_sib = await mux.open_ws(sibling, "/ws", "", [])
    await _settle()

    # Browser drops: the relay pops the viewer and sends WS_CLOSE down.
    await mux.close_ws(ws_gone, 1006)

    # Home's _run_ws had already queued frames for ws_gone -> they arrive orphaned.
    for i in range(5):
        await mux.on_tunnel_frame(
            protocol.ws_data(0, ws_gone, i + 1, b'{"type":"status"}'))
    # A WS_CLOSE for the same (already-gone) ws_id is likewise a benign no-op.
    await mux.on_tunnel_frame(protocol.ws_close(0, ws_gone, 1000))

    # The sibling's tunnel is intact: a normal WS_DATA still fans through.
    await mux.on_tunnel_frame(
        protocol.ws_data(0, ws_sib, 1, b'{"type":"status","ok":1}'))
    await _settle()
    assert sibling.received == ['{"type":"status","ok":1}']


async def test_slow_browser_dropped_sibling_keeps_full_stream(fake_tunnel):
    """The headline isolation case: a stalled browser is disconnected; the fast
    sibling receives every event. Exactly ONE bus path feeds both (the home's
    single subscription); the relay's read never stalls on the slow one."""
    mux = _mux(fake_tunnel, egress_max=4)
    slow = FakeBrowserWS(slow=True)      # never drains until released
    fast = FakeBrowserWS()
    ws_slow = await mux.open_ws(slow, "/ws", "", [])
    ws_fast = await mux.open_ws(fast, "/ws", "", [])
    await _settle()

    # Push many STATUS events to BOTH. The slow one's buffer (cap 4) overflows
    # and it gets disconnected; the fast one gets them all.
    for i in range(40):
        ev = ('{"type":"status","i":%d}' % i).encode()
        await mux.on_tunnel_frame(protocol.ws_data(0, ws_slow, i + 1, ev))
        await mux.on_tunnel_frame(protocol.ws_data(0, ws_fast, i + 1, ev))
        await asyncio.sleep(0)
    await _settle()

    # The fast sibling received the full stream (status is drop-tolerant only
    # when ITS buffer overflows; here it drains promptly so it gets everything).
    assert len(fast.received) == 40
    assert fast.received[-1] == '{"type":"status","i":39}'

    # The slow browser was disconnected (egress overflow) and pinned no more
    # than its bounded buffer. Release it to prove it never blocked the others.
    slow.release.set()
    await _settle()
    assert slow.closed_code in (1013, 1011)
    assert len(slow.received) <= 5  # bounded buffer, never the full 40


# ---------------------------------------------------- overflow policy (#399)
#
# 2026-09-27, NGC 7331 over the relay: the phone's LAST FRAME tile read NO
# FRAME YET at 50/105 frames while the rig served frame 535. The overflow
# branch dropped the OLDEST queued event whatever its type (both arms of the
# old if/else did the same thing), so a queued `preview` behind a run of 2 s
# `status` frames was the first thing to go. The test that stood here could
# not fail: it asserted only "if anything came through, something mentions a
# frame".
#
# Each case fills a slow browser's buffer WITHOUT yielding, so the pump has
# taken nothing yet and what the browser finally receives is exactly what the
# policy left in the buffer.
#
# NAMED MUTANTS, each run in a private scratch copy of relay/ (never the shared
# tree); the observed failure is quoted at the test it turns red.
#   R1 "coalesce == drop-oldest"   _enqueue_viewer: a coalesce event takes the
#                                  drop-oldest path a status takes, instead of
#                                  replacing the queued event of its own type.
#   R2 "drop an arbitrary oldest"  _enqueue_viewer: the drop path drops the
#                                  oldest queued event whatever its type (the
#                                  shape of the old code).
#   R3 "no gap frame"              _pump_viewer: never sends relay_gap.
#   R4 "all-coalesce drops newest" _enqueue_viewer: with no non-coalesce event
#                                  queued, the fallback drops the NEWEST queued
#                                  event instead of the oldest.

GAP = '{"type":"relay_gap"}'


def _status(i: int) -> bytes:
    return ('{"type":"status","i":%d}' % i).encode()


def _preview(pid: int) -> bytes:
    return ('{"type":"preview","data":{"id":%d}}' % pid).encode()


async def _slow_viewer(mux):
    """A browser whose sends block until ``release`` is set, with its pump
    parked on an empty buffer."""
    slow = FakeBrowserWS(slow=True)
    ws = await mux.open_ws(slow, "/ws", "", [])
    await _settle()
    return slow, ws


async def _burst(mux, ws, events: list) -> None:
    """Hand the relay a burst of events for one viewer. A WS_DATA never
    suspends inside ``on_tunnel_frame``, so the viewer's pump does not run
    between them."""
    for i, ev in enumerate(events):
        await mux.on_tunnel_frame(protocol.ws_data(0, ws, i + 1, ev))


async def test_overflow_keeps_the_queued_preview_behind_a_status_flood(fake_tunnel):
    """A queued preview followed by 200 status events on a cap-4 buffer: the
    oldest STATUS events go, the preview survives, and the browser is told,
    once, that it missed something.

    R2 "drop an arbitrary oldest", observed (the preview was the first to go):
      At index 1 diff: '{"type":"status","i":196}' != '{"type":"preview","data":{"id":535}}'
    R3 "no gap frame", observed:
      At index 0 diff: '{"type":"preview","data":{"id":535}}' != '{"type":"relay_gap"}'
      Right contains one more item: '{"type":"status","i":199}'
    """
    mux = _mux(fake_tunnel, egress_max=4)
    slow, ws = await _slow_viewer(mux)
    await _burst(mux, ws, [_preview(535)] + [_status(i) for i in range(200)])
    slow.release.set()
    await _settle()
    assert slow.received == [
        GAP,
        _preview(535).decode(),
        _status(197).decode(), _status(198).decode(), _status(199).decode(),
    ]
    assert slow.closed_code is None, "a backlog that drained is not a stuck browser"


async def test_a_second_preview_replaces_the_first_in_place(fake_tunnel):
    """The coalesce half: a newer preview arriving at a full buffer takes the
    queued preview's own slot. No status is dropped for it, and the stale frame
    is never delivered.

    R1 "coalesce == drop-oldest", observed (the stale frame is delivered, and
    status 197 was dropped to make room for the new one):
      assert ['{"type":"relay_gap"}', '{"type":"preview","data":{"id":535}}',
              '{"type":"status","i":198}', '{"type":"status","i":199}',
              '{"type":"preview","data":{"id":536}}'] == [...]
      At index 1 diff: '{"type":"preview","data":{"id":535}}' != '{"type":"preview","data":{"id":536}}'
    R2 "drop an arbitrary oldest", observed:
      At index 1 diff: '{"type":"status","i":197}' != '{"type":"preview","data":{"id":536}}'
    """
    mux = _mux(fake_tunnel, egress_max=4)
    slow, ws = await _slow_viewer(mux)
    await _burst(mux, ws, [_preview(535)] + [_status(i) for i in range(200)]
                 + [_preview(536)])
    slow.release.set()
    await _settle()
    assert slow.received == [
        GAP,
        _preview(536).decode(),
        _status(197).decode(), _status(198).decode(), _status(199).decode(),
    ]


async def test_preview_only_overflow_coalesces_without_a_gap(fake_tunnel):
    """A burst of previews on a cap-2 buffer keeps its first and its LATEST
    frame. Superseding a preview loses nothing the browser needs, so no gap
    frame is sent for it: a gap costs the browser a snapshot read.

    R1 "coalesce == drop-oldest", observed:
      At index 0 diff: '{"type":"relay_gap"}' != '{"type":"preview","data":{"id":0}}'
      Left contains one more item: '{"type":"preview","data":{"id":9}}'
    """
    mux = _mux(fake_tunnel, egress_max=2)
    slow, ws = await _slow_viewer(mux)
    await _burst(mux, ws, [_preview(i) for i in range(10)])
    slow.release.set()
    await _settle()
    assert slow.received == [_preview(0).decode(), _preview(9).decode()]


async def test_all_coalesce_buffer_drops_its_oldest_for_a_status(fake_tunnel):
    """With only previews queued there is no status to sacrifice. The oldest
    queued event goes (here a preview that three newer ones supersede), the
    status is delivered, and the latest preview survives.

    R4 "all-coalesce drops newest", observed:
      At index 1 diff: '{"type":"preview","data":{"id":0}}' != '{"type":"preview","data":{"id":1}}'
    """
    mux = _mux(fake_tunnel, egress_max=4)
    slow, ws = await _slow_viewer(mux)
    await _burst(mux, ws, [_preview(i) for i in range(4)] + [_status(0)])
    slow.release.set()
    await _settle()
    assert slow.received == [
        GAP,
        _preview(1).decode(), _preview(2).decode(), _preview(3).decode(),
        _status(0).decode(),
    ]


async def test_gap_frame_rearms_after_it_is_delivered(fake_tunnel):
    """One gap frame per run of drops, not one per dropped event, and a LATER
    run gets its own. A latch that never re-armed would make the first gap of
    the night the only one the browser ever heard about.

    R3 "no gap frame", observed:
      assert 0 == 2
    """
    mux = _mux(fake_tunnel, egress_max=4)
    slow, ws = await _slow_viewer(mux)
    await _burst(mux, ws, [_status(i) for i in range(50)])
    slow.release.set()
    await _settle()
    slow.release.clear()
    await _burst(mux, ws, [_status(100 + i) for i in range(50)])
    slow.release.set()
    await _settle()
    assert slow.received.count(GAP) == 2
    # Each gap frame comes immediately before the first event after its drops.
    assert slow.received[0] == GAP and slow.received[1] == _status(46).decode()
    assert slow.received[5] == GAP and slow.received[6] == _status(146).decode()


async def test_no_gap_frame_without_a_drop(fake_tunnel):
    """Control: a browser that keeps up is never sent a gap frame, and sees
    exactly the stream the home sent."""
    mux = _mux(fake_tunnel, egress_max=4)
    fast = FakeBrowserWS()
    ws = await mux.open_ws(fast, "/ws", "", [])
    await _settle()
    for i in range(20):
        await mux.on_tunnel_frame(protocol.ws_data(0, ws, i + 1, _status(i)))
        await asyncio.sleep(0)
    await _settle()
    assert fast.received == [_status(i).decode() for i in range(20)]


async def test_close_ws_emits_ws_close(fake_tunnel):
    mux = _mux(fake_tunnel)
    browser = FakeBrowserWS()
    ws_id = await mux.open_ws(browser, "/ws", "", [])
    await mux.close_ws(ws_id, 1000)
    closes = fake_tunnel.of_type(protocol.FrameType.WS_CLOSE)
    assert closes and closes[-1].header["ws_id"] == ws_id
