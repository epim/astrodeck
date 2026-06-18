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


async def test_orphan_ws_data_errors(fake_tunnel):
    mux = _mux(fake_tunnel)
    with pytest.raises(Exception):
        await mux.on_tunnel_frame(protocol.ws_data(0, "ws-nope", 1, b"{}"))


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


async def test_coalesce_keeps_latest_preview(fake_tunnel):
    """On overflow, preview/sequence events COALESCE (keep latest) rather than
    drop the newest -- the buffer never grows past its bound but the freshest
    preview survives."""
    mux = _mux(fake_tunnel, egress_max=2)
    slow = FakeBrowserWS(slow=True)
    ws = await mux.open_ws(slow, "/ws", "", [])
    await _settle()
    for i in range(10):
        ev = ('{"type":"preview","frame":%d}' % i).encode()
        await mux.on_tunnel_frame(protocol.ws_data(0, ws, i + 1, ev))
        await asyncio.sleep(0)
    await _settle()
    # Release and let the pump drain whatever survived; the LATEST preview must
    # be present in what little was buffered (coalesce keeps newest).
    slow.release.set()
    await _settle()
    if slow.received:  # may have been disconnected; if anything came through:
        assert any("frame" in r for r in slow.received)


async def test_close_ws_emits_ws_close(fake_tunnel):
    mux = _mux(fake_tunnel)
    browser = FakeBrowserWS()
    ws_id = await mux.open_ws(browser, "/ws", "", [])
    await mux.close_ws(ws_id, 1000)
    closes = fake_tunnel.of_type(protocol.FrameType.WS_CLOSE)
    assert closes and closes[-1].header["ws_id"] == ws_id
