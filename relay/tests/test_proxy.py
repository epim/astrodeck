"""§T7(1) browser<->frame round-trip + orphan/dedup rules (W3.3.5).

A browser HTTP request becomes a ``REQ_OPEN`` (+ ``REQ_DATA``); a
``RESP_HEAD``/``RESP_DATA`` becomes the browser response. Method/path/repeated
headers survive; the ``ws_id``<->browser binding is correct. Orphan and
duplicate-stream frames ERROR rather than truncate."""
from __future__ import annotations

import pytest

from relay import protocol
from relay.protocol import FrameType
from relay.proxy import ProxyError, TunnelMultiplexer
from relay.registry import HomeRegistration

from conftest import FakeScopeTunnel


def _mux(tunnel: FakeScopeTunnel) -> TunnelMultiplexer:
    reg = HomeRegistration(home_id="h1", generation=1, tunnel=tunnel)
    return TunnelMultiplexer(reg)


async def _drive_request(mux, *, method="GET", path="/api/status",
                         query="x=1", headers=None, body=b""):
    """Open a request, collect the response via the callbacks."""
    got = {"status": None, "headers": None, "body": b"", "eofs": 0}

    async def on_head(status, hdrs):
        got["status"] = status
        got["headers"] = hdrs

    async def on_data(chunk, eof):
        got["body"] += chunk
        if eof:
            got["eofs"] += 1

    sid = await mux.open_request(method, path, query, headers or [],
                                 has_body=bool(body), on_head=on_head,
                                 on_data=on_data)
    if body:
        await mux.send_request_body(sid, body, eof=True)
    return sid, got


async def test_request_becomes_req_open(fake_tunnel):
    mux = _mux(fake_tunnel)
    sid, _ = await _drive_request(
        mux, method="POST", path="/api/mount/goto", query="ra=1",
        headers=[["x-thing", "a"], ["x-thing", "b"]], body=b"payload",
    )
    opens = fake_tunnel.of_type(FrameType.REQ_OPEN)
    assert len(opens) == 1
    h = opens[0].header
    assert h["method"] == "POST"
    assert h["path"] == "/api/mount/goto"
    assert h["query"] == "ra=1"
    # Repeated headers survive in order.
    assert h["headers"] == [["x-thing", "a"], ["x-thing", "b"]]
    assert opens[0].stream_id == sid
    # Body framed as a REQ_DATA with eof.
    datas = fake_tunnel.of_type(FrameType.REQ_DATA)
    assert datas[-1].payload == b"payload"
    assert datas[-1].eof() is True


async def test_response_reassembles_byte_for_byte(fake_tunnel):
    mux = _mux(fake_tunnel)
    sid, got = await _drive_request(mux)
    await mux.on_tunnel_frame(
        protocol.resp_head(sid, 201, [["content-type", "text/plain"]])
    )
    await mux.on_tunnel_frame(protocol.resp_data(sid, b"abc", eof=False))
    await mux.on_tunnel_frame(protocol.resp_data(sid, b"def", eof=False))
    await mux.on_tunnel_frame(protocol.resp_data(sid, b"ghi", eof=True))
    assert got["status"] == 201
    assert got["headers"] == [["content-type", "text/plain"]]
    assert got["body"] == b"abcdefghi"
    assert got["eofs"] == 1


async def test_orphan_resp_data_errors(fake_tunnel):
    """A RESP_DATA for an unknown stream_id is an ERROR, never silently
    buffered (W3.2 orphan reject)."""
    mux = _mux(fake_tunnel)
    with pytest.raises(ProxyError):
        await mux.on_tunnel_frame(protocol.resp_data(999, b"x", eof=True))


async def test_resp_data_before_head_errors(fake_tunnel):
    mux = _mux(fake_tunnel)
    sid, _ = await _drive_request(mux)
    with pytest.raises(ProxyError):
        await mux.on_tunnel_frame(protocol.resp_data(sid, b"x", eof=True))


async def test_duplicate_resp_head_errors(fake_tunnel):
    mux = _mux(fake_tunnel)
    sid, _ = await _drive_request(mux)
    await mux.on_tunnel_frame(protocol.resp_head(sid, 200, []))
    with pytest.raises(ProxyError):
        await mux.on_tunnel_frame(protocol.resp_head(sid, 200, []))


async def test_stream_ids_are_unique_per_request(fake_tunnel):
    """The relay allocates a fresh stream_id per browser exchange (never 0)."""
    mux = _mux(fake_tunnel)
    sid1, _ = await _drive_request(mux)
    sid2, _ = await _drive_request(mux)
    assert sid1 != sid2
    assert sid1 != 0 and sid2 != 0


async def test_failed_req_open_send_rolls_back_routing_state():
    tunnel = FakeScopeTunnel()
    tunnel.closed = True
    mux = _mux(tunnel)

    async def _head(_status, _headers):
        pass

    async def _data(_chunk, _eof):
        pass

    with pytest.raises(Exception):
        await mux.open_request(
            "GET", "/api/status", "", [], has_body=False,
            on_head=_head, on_data=_data)
    assert mux._exchanges == {}
    assert mux.reg.req_routes == {}


async def test_failed_ws_open_send_rolls_back_viewer_and_pump():
    from conftest import FakeBrowserWS

    tunnel = FakeScopeTunnel()
    tunnel.closed = True
    mux = _mux(tunnel)
    browser = FakeBrowserWS()

    with pytest.raises(Exception):
        await mux.open_ws(browser, "/ws", "", [])
    assert mux._viewers == {}
    assert mux.reg.ws_routes == {}


async def test_large_body_chunked_under_max_payload(fake_tunnel):
    """A body larger than MAX_PAYLOAD streams up in bounded chunks (the
    server shell slices; here we verify the proxy forwards a pre-sliced
    stream and that each chunk is within bound when the caller slices)."""
    mux = _mux(fake_tunnel)
    sid, _ = await _drive_request(mux)
    big = b"y" * protocol.MAX_PAYLOAD
    # one max-size chunk is accepted (boundary).
    await mux.send_request_body(sid, big, eof=False)
    last = fake_tunnel.of_type(FrameType.REQ_DATA)[-1]
    assert len(last.payload) == protocol.MAX_PAYLOAD
    assert last.eof() is False


# ------------------------------------------- a late reply must not kill the home

async def test_a_late_reply_to_an_aborted_request_does_not_orphan(monkeypatch):
    """AN ABORTED REQUEST IS NOT A PROTOCOL VIOLATION.

    `abort_request` dropped the exchange BEFORE sending REQ_ABORT, so a home that
    was already answering produced a RESP_HEAD for a stream_id the relay no
    longer knew. `_on_resp_head` raised ProxyError for the orphan, which
    `_scope_endpoint` catches with a bare `except Exception` and turns into
    `mux.shutdown(1012)` — **the whole home tunnel**, and every browser on it.

    That path became reachable the moment the upstream timeout shipped: any
    request slower than `upstream_timeout_s` aborts, and a home that answers a
    moment later kills remote access for everyone. `POST /api/connect/rig`
    awaits a full rig bring-up inside its handler and routinely exceeds 30s.

    A genuine orphan — a stream_id never opened — must still be an error.
    """
    tunnel = FakeScopeTunnel()
    mux = _mux(tunnel)
    sid, got = await _drive_request(mux)
    await mux.abort_request(sid, "relay upstream timeout")

    # the home was already answering when the abort went out
    await mux.on_tunnel_frame(protocol.resp_head(sid, 200, []))
    await mux.on_tunnel_frame(protocol.resp_data(sid, b"late", eof=True))

    assert got["status"] is None, "an aborted browser must not be written to"
    assert got["body"] == b"", got


async def test_a_reply_for_a_stream_that_never_existed_is_still_an_error():
    """The check earns its keep on frames the abort cannot explain."""
    tunnel = FakeScopeTunnel()
    mux = _mux(tunnel)
    with pytest.raises(ProxyError):
        await mux.on_tunnel_frame(protocol.resp_head(4242, 200, []))


async def test_the_tombstone_set_cannot_grow_without_bound():
    """One entry per aborted request, on a tunnel that lives for a whole night."""
    tunnel = FakeScopeTunnel()
    mux = _mux(tunnel)
    for _ in range(5000):
        sid, _ = await _drive_request(mux)
        await mux.abort_request(sid)
    assert len(mux._aborted) <= 1024, f"tombstones grew to {len(mux._aborted)}"
