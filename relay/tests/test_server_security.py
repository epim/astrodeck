"""Security regression tests for the Starlette relay shell."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")

from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from relay import protocol
from relay.config import RelayConfig
from relay.server import (DownstreamBackpressure, RelayState,
                          _browser_http, _browser_origin_allowed,
                          _scope_endpoint)

_RELAY_ORIGIN = "https://" + "relay.test"
_EVIL_ORIGIN = "https://" + "evil.test"


class _Request:
    def __init__(self, chunks, *, method="POST", origin=_RELAY_ORIGIN,
                 content_length=None):
        raw = [(b"host", b"relay.test")]
        if origin is not None:
            raw.append((b"origin", origin.encode("ascii")))
        if content_length is not None:
            raw.append((b"content-length", str(content_length).encode("ascii")))
        self.path_params = {"home_id": "home-1", "path": "api/upload"}
        self.method = method
        self.scope = {"client": ("192.0.2.10", 1234), "headers": raw}
        self.headers = Headers(raw=raw)
        self.url = SimpleNamespace(query="")
        self._chunks = list(chunks)

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


class _Mux:
    def __init__(self, response_chunks=((b"ok", True),), *,
                 response_status=200, response_headers=None):
        self.response_chunks = list(response_chunks)
        self.response_status = response_status
        self.response_headers = (response_headers if response_headers is not None
                                 else [["content-type", "text/plain"]])
        self.sent = []
        self.aborted = []
        self.on_head = None
        self.on_data = None

    async def open_request(self, method, path, query, headers, *, has_body,
                           on_head, on_data):
        self.on_head = on_head
        self.on_data = on_data
        return 7

    async def send_request_body(self, stream_id, chunk, *, eof):
        self.sent.append((bytes(chunk), eof))
        if eof:
            await self.on_head(self.response_status, self.response_headers)
            for response_chunk, response_eof in self.response_chunks:
                await self.on_data(response_chunk, response_eof)

    async def abort_request(self, stream_id, reason=""):
        self.aborted.append((stream_id, reason))


class _ScopeWebSocket:
    def __init__(self, hello_frame, ip):
        self.scope = {"client": (ip, 1234)}
        self._incoming = asyncio.Queue()
        self._incoming.put_nowait(hello_frame.encode())
        self.sent = []
        self.closed = []

    async def accept(self):
        pass

    async def receive_bytes(self):
        item = await self._incoming.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed.append(code)
        self._incoming.put_nowait(WebSocketDisconnect(code))


class _FailingAckWebSocket(_ScopeWebSocket):
    async def send_bytes(self, data):
        raise ConnectionResetError("ACK write failed")


def _state(monkeypatch, **overrides):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.delenv("RELAY_DEVICE_TOKENS", raising=False)
    cfg = RelayConfig(bind_host="127.0.0.1", origin="relay.test", **overrides)
    return RelayState(cfg)


def test_network_server_has_no_implicit_dev_signers(monkeypatch):
    monkeypatch.delenv("RELAY_OIDC_SEED_FILE", raising=False)
    monkeypatch.delenv("RELAY_OIDC_SEED", raising=False)
    monkeypatch.delenv("RELAY_VIEWER_SEED_FILE", raising=False)
    monkeypatch.delenv("RELAY_VIEWER_SEED", raising=False)
    state = _state(monkeypatch)
    assert state.oidc_signer is None
    assert state.viewer_signer is None


def test_exact_origin_is_required_for_browser_mutations(monkeypatch):
    state = _state(monkeypatch)
    assert _browser_origin_allowed(
        state, Headers({"host": "relay.test", "origin": _RELAY_ORIGIN}))
    assert not _browser_origin_allowed(
        state, Headers({"host": "relay.test", "origin": _EVIL_ORIGIN}))
    assert not _browser_origin_allowed(state, Headers({"host": "relay.test"}))


@pytest.mark.asyncio
async def test_request_body_is_streamed_and_never_calls_body(monkeypatch):
    state = _state(monkeypatch, max_body=20)
    mux = _Mux()
    state.connections["home-1"] = SimpleNamespace(mux=mux)
    request = _Request([b"abc", b"def"])

    response = await _browser_http(state, request)
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert response.status_code == 200
    assert body == b"ok"
    assert mux.sent == [(b"abc", False), (b"def", False), (b"", True)]
    assert state._active_http_total == 0


@pytest.mark.asyncio
async def test_chunked_request_over_limit_aborts_without_accumulating(monkeypatch):
    state = _state(monkeypatch, max_body=5)
    mux = _Mux()
    state.connections["home-1"] = SimpleNamespace(mux=mux)

    response = await _browser_http(state, _Request([b"123", b"456"]))

    assert response.status_code == 413
    assert mux.aborted and mux.aborted[0][0] == 7
    assert state._active_http_total == 0


@pytest.mark.asyncio
async def test_stalled_request_upload_times_out_and_releases_lease(monkeypatch):
    state = _state(
        monkeypatch, request_body_timeout_s=0.01,
        request_total_timeout_s=1.0)
    mux = _Mux()
    state.connections["home-1"] = SimpleNamespace(mux=mux)
    request = _Request([])

    async def _stalled_stream():
        await asyncio.Event().wait()
        yield b"unreachable"

    request.stream = _stalled_stream
    response = await _browser_http(state, request)

    assert response.status_code == 408
    assert mux.aborted and "stalled" in mux.aborted[0][1]
    assert state._active_http_total == 0


@pytest.mark.asyncio
async def test_invalid_home_response_metadata_fails_closed(monkeypatch):
    state = _state(monkeypatch)
    mux = _Mux(response_headers=[["x-test", "ok\r\ninjected: yes"]])
    state.connections["home-1"] = SimpleNamespace(mux=mux)

    response = await _browser_http(state, _Request([]))

    assert response.status_code == 502
    assert mux.aborted and "metadata" in mux.aborted[0][1]
    assert state._active_http_total == 0


@pytest.mark.asyncio
async def test_slow_browser_response_queue_fails_only_its_stream(monkeypatch):
    state = _state(monkeypatch, http_egress_chunks=2)
    mux = _Mux(response_chunks=[
        (b"one", False), (b"two", False), (b"three", True),
    ])
    state.connections["home-1"] = SimpleNamespace(mux=mux)

    response = await _browser_http(state, _Request([]))
    with pytest.raises(DownstreamBackpressure):
        _ = b"".join([chunk async for chunk in response.body_iterator])

    assert mux.aborted and "buffer full" in mux.aborted[0][1]
    assert state._active_http_total == 0


def test_non_loopback_relay_requires_a_pinned_public_origin():
    with pytest.raises(ValueError, match="RELAY_ORIGIN"):
        RelayConfig(bind_host="0.0.0.0", origin="")


@pytest.mark.asyncio
async def test_higher_generation_physically_evicts_old_scope_socket(monkeypatch):
    state = _state(monkeypatch)
    state.registry.provision("device-token", "home-1")
    old_ws = _ScopeWebSocket(
        protocol.hello("device-token", "home-1", 1), "192.0.2.20")
    old_task = asyncio.create_task(_scope_endpoint(state, old_ws))

    for _ in range(100):
        if "home-1" in state.connections:
            break
        await asyncio.sleep(0)
    assert state.connections["home-1"].reg.generation == 1

    new_ws = _ScopeWebSocket(
        protocol.hello("device-token", "home-1", 2), "192.0.2.21")
    new_task = asyncio.create_task(_scope_endpoint(state, new_ws))
    for _ in range(100):
        conn = state.connections.get("home-1")
        if conn is not None and conn.reg.generation == 2:
            break
        await asyncio.sleep(0)

    assert state.connections["home-1"].reg.generation == 2
    assert 1012 in old_ws.closed

    await new_ws.close(1000)
    await asyncio.wait_for(asyncio.gather(old_task, new_task), timeout=1.0)


@pytest.mark.asyncio
async def test_failed_new_generation_ack_preserves_the_live_scope(monkeypatch):
    state = _state(monkeypatch)
    state.registry.provision("device-token", "home-1")
    old_ws = _ScopeWebSocket(
        protocol.hello("device-token", "home-1", 1), "192.0.2.20")
    old_task = asyncio.create_task(_scope_endpoint(state, old_ws))
    for _ in range(100):
        if "home-1" in state.connections:
            break
        await asyncio.sleep(0)
    old_conn = state.connections["home-1"]

    failed_ws = _FailingAckWebSocket(
        protocol.hello("device-token", "home-1", 2), "192.0.2.21")
    await _scope_endpoint(state, failed_ws)

    assert state.connections["home-1"] is old_conn
    assert state.registry.get("home-1") is old_conn.reg
    assert old_conn.tunnel.closed is False
    assert 1012 not in old_ws.closed

    await old_ws.close(1000)
    await asyncio.wait_for(old_task, timeout=1.0)
