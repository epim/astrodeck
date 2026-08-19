"""The relay HTTP/WS server shell (Starlette + websockets) -- production wiring.

This is the ONLY module that touches real sockets; everything it depends on
(``protocol``/``registry``/``proxy``/``connection``/``oidc``/``viewer_links``/
``ratelimit``) is transport-free and unit-tested off-wire. The single on-wire
WSS integration test exercises this shell end to end.

Endpoints:
  * ``GET  /healthz``                  liveness (no auth).
  * ``WS   /scope``                    the HOME dials here (device-token auth via
                                       the HELLO frame). One per home; generation
                                       fencing evicts a stale socket.
  * ``WS   /h/{home_id}/ws``           a browser opens the tunnelled ``/ws``.
  * ``ANY  /h/{home_id}/{path:path}``  every other browser request -> tunnelled
                                       HTTP (SPA, /assets, /api, /auth).
  * ``GET  /auth/google/callback``     relay-terminated Google OIDC (W3.3.5).
  * ``GET  /share/{token}``            redeem a viewer link.

The browser-facing routes forward the WHOLE app (SPA + API + ``/ws``) so the UI
needs ZERO component changes. ``home_id`` in the path is the stable routing key
(the single-instance affinity model; swap for a shared bus to scale out).

The principal token the relay attaches to ``REQ_OPEN`` (the ``principal_token``
header) is the ONLY identity carrier the home trusts; inbound browser auth
headers/cookies are forwarded UNTOUCHED for the home to validate, EXCEPT that the
relay never *forges* one. (Per W3.3.2 the HOME-side scope client strips inbound
auth headers and injects the principal from ``principal_token``; the relay does
not need to strip them, but it MUST NOT synthesize a privileged one.)
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Optional

from .config import RelayConfig, load_device_tokens, load_seed
from .connection import ScopeConnection
from .headers import transform_response_headers
from .principal import PrincipalSigner
from .protocol import (
    MAX_PAYLOAD,
    Frame,
    ProtocolError,
    decode,
)
from .proxy import BrowserWS
from .ratelimit import RateLimiter
from .registry import HomeRegistry, ScopeTunnel

try:  # Starlette/uvicorn/websockets are optional at import time so the pure
    # core + tests run in a bare venv; the server shell needs them.
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
    from starlette.routing import Route, WebSocketRoute
    from starlette.websockets import WebSocket, WebSocketDisconnect

    _HAVE_STARLETTE = True
except Exception:  # noqa: BLE001 - server deps absent (pure-core/test env)
    _HAVE_STARLETTE = False


# --------------------------------------------------------------- shared state

class RelayState:
    """Process-wide relay state: the home registry, signers, and rate limiters.
    One per relay process (single-instance affinity)."""

    def __init__(self, cfg: RelayConfig):
        self.cfg = cfg
        self.registry = HomeRegistry(
            token_to_home=load_device_tokens(),
            ping_interval_s=cfg.ping_interval_s,
            max_ping_misses=cfg.ping_max_misses,
        )
        self.oidc_signer = _make_signer(load_seed("RELAY_OIDC_SEED_FILE"),
                                        kid="oidc")
        self.viewer_signer = _make_signer(load_seed("RELAY_VIEWER_SEED_FILE"),
                                          kid="viewer")
        self.http_limiter = RateLimiter(cfg.http_rate, cfg.http_burst)
        self.ws_limiter = RateLimiter(cfg.ws_rate, cfg.ws_burst)
        # home_id -> live ScopeConnection (the affinity table).
        self.connections: dict[str, ScopeConnection] = {}
        # Strong ref to the housekeeping loop; a bare create_task() result can be
        # garbage-collected mid-flight.
        self.housekeeping_task: "asyncio.Task | None" = None


def _make_signer(seed: bytes, *, kid: str) -> PrincipalSigner:
    """An Ed25519 signer if a real seed is mounted, else the LOUD dev-HMAC
    fallback (local dev only)."""
    if seed:
        return PrincipalSigner.from_ed25519_seed(seed, kid=kid)
    return PrincipalSigner.dev_hmac(kid=kid)


# ------------------------------------------------------- websockets glue (real)

class _StarletteScopeTunnel(ScopeTunnel):
    """Wraps a real Starlette ``WebSocket`` (the home's ``/scope`` socket) as a
    ``ScopeTunnel`` so the transport-free core sends frames over the wire."""

    def __init__(self, ws: "WebSocket"):
        super().__init__(self._send, conn_id=f"scope-{id(ws):x}")
        self._ws = ws

    async def _send(self, frame: Frame) -> None:
        await self._ws.send_bytes(frame.encode())


class _StarletteBrowserWS(BrowserWS):
    """Wraps a real browser ``/ws`` Starlette ``WebSocket`` as a ``BrowserWS``."""

    def __init__(self, ws: "WebSocket"):
        self._ws = ws

    async def send_text(self, text: str) -> None:
        await self._ws.send_text(text)

    async def close(self, code: int = 1000) -> None:
        try:
            await self._ws.close(code)
        except Exception:  # noqa: BLE001 - already closed
            pass


# ----------------------------------------------------------------- handlers

def _client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "?"


async def _scope_endpoint(state: RelayState, ws: "WebSocket") -> None:
    """Handle ONE home ``/scope`` WSS: HELLO -> register -> read loop + ping."""
    await ws.accept()
    tunnel = _StarletteScopeTunnel(ws)
    conn = ScopeConnection(state.registry, tunnel)
    # First frame MUST be HELLO.
    try:
        first = decode(await ws.receive_bytes())
    except (ProtocolError, WebSocketDisconnect, Exception):  # noqa: BLE001
        await ws.close(1002)
        return
    ack = conn.handle_hello(first)
    await ws.send_bytes(ack.encode())
    if not ack.header.get("ok"):
        await ws.close(1008)
        return
    home_id = conn.reg.home_id
    state.connections[home_id] = conn
    ping_task = asyncio.ensure_future(_ping_loop(state, conn, ws))
    try:
        while True:
            raw = await ws.receive_bytes()
            frame = decode(raw)
            reply = await conn.on_frame(frame)
            if reply is not None:
                await ws.send_bytes(reply.encode())
    except (WebSocketDisconnect, ProtocolError):
        pass
    except Exception:  # noqa: BLE001 - any read error tears the tunnel down
        pass
    finally:
        ping_task.cancel()
        # Only clear the affinity entry if we are still the live connection
        # (a higher-generation redial may have replaced us).
        if state.connections.get(home_id) is conn:
            state.connections.pop(home_id, None)
        await conn.close()


async def _ping_loop(state: RelayState, conn: ScopeConnection,
                     ws: "WebSocket") -> None:
    """Send keepalive PINGs and tear the tunnel down on PONG starvation."""
    try:
        while True:
            await asyncio.sleep(state.cfg.ping_interval_s)
            if conn.is_stale():
                await ws.close(1001)
                return
            await ws.send_bytes(conn.make_ping().encode())
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - socket gone
        return


async def _browser_http(state: RelayState, request) -> "StreamingResponse":
    """Tunnel ONE browser HTTP request to the home and stream the response."""
    home_id = request.path_params["home_id"]
    path = "/" + request.path_params.get("path", "")
    ip = _client_ip(request.scope)
    if not state.http_limiter.allow(ip):
        return PlainTextResponse("rate limited", status_code=429)
    conn = state.connections.get(home_id)
    if conn is None or conn.mux is None:
        return PlainTextResponse("home not connected", status_code=502)

    query = request.url.query or ""
    headers = [[k, v] for k, v in request.headers.items()]
    body = await request.body()
    if len(body) > state.cfg.max_body:
        return PlainTextResponse("payload too large", status_code=413)

    head_holder: dict = {}
    head_ready = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue()

    async def on_head(status: int, hdrs: list) -> None:
        head_holder["status"] = status
        head_holder["headers"] = hdrs
        head_ready.set()

    async def on_data(chunk: bytes, eof: bool) -> None:
        await chunks.put((chunk, eof))

    stream_id = await conn.mux.open_request(
        request.method, path, query, headers,
        has_body=bool(body), on_head=on_head, on_data=on_data,
    )
    # Stream the request body up (chunked) then EOF.
    if body:
        for i in range(0, len(body), MAX_PAYLOAD):
            piece = body[i:i + MAX_PAYLOAD]
            is_last = (i + MAX_PAYLOAD) >= len(body)
            await conn.mux.send_request_body(stream_id, piece, eof=is_last)
    else:
        await conn.mux.send_request_body(stream_id, b"", eof=True)

    # A DEAD TUNNEL MUST NOT PARK THIS HANDLER FOREVER. When a tunnel drops,
    # shutdown() clears `_exchanges`/`req_routes`, so nothing can ever call
    # on_head for this stream again -- an unbounded wait here leaked the task,
    # its buffered body and its queue for the life of the process, and left the
    # browser spinning with no error to show.
    try:
        await asyncio.wait_for(head_ready.wait(), timeout=state.cfg.upstream_timeout_s)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            await conn.mux.abort_request(stream_id, "relay upstream timeout")
        return PlainTextResponse("home did not respond", status_code=504)
    status = head_holder.get("status", 502)
    raw_headers = head_holder.get("headers", [])
    out_headers = transform_response_headers(
        raw_headers, relay_origin=state.cfg.origin, relay_https=state.cfg.https,
    )

    async def body_iter():
        # Same bound on the body: a tunnel that dies after the head but before
        # EOF would otherwise hang this response open forever.
        while True:
            try:
                chunk, eof = await asyncio.wait_for(
                    chunks.get(), timeout=state.cfg.upstream_timeout_s)
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    await conn.mux.abort_request(stream_id, "relay upstream timeout")
                return
            if chunk:
                yield chunk
            if eof:
                break

    resp = StreamingResponse(body_iter(), status_code=status)
    # Replace the default content-type/headers with the transformed home headers.
    for name, value in out_headers:
        resp.raw_headers.append((name.encode("latin-1"), value.encode("latin-1")))
    return resp


async def _browser_ws(state: RelayState, ws: "WebSocket") -> None:
    """Tunnel ONE browser ``/ws`` to the home and fan ``WS_DATA`` back."""
    home_id = ws.path_params["home_id"]
    ip = _client_ip(ws.scope)
    conn = state.connections.get(home_id)
    if conn is None or conn.mux is None or not state.ws_limiter.allow(ip):
        await ws.close(1013)
        return
    await ws.accept()
    browser = _StarletteBrowserWS(ws)
    headers = [[k, v] for k, v in ws.headers.items()]
    ws_id = await conn.mux.open_ws(browser, "/ws", ws.url.query or "", headers)
    try:
        # The home /ws is send-only, so we never forward browser->home frames;
        # we just hold the socket open until the browser disconnects.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await conn.mux.close_ws(ws_id)


async def _healthz(request) -> "JSONResponse":
    state: RelayState = request.app.state.relay
    return JSONResponse({
        "ok": True,
        "homes": state.registry.homes(),
        "ts": time.time(),
    })


# ----------------------------------------------------------------- app factory

def create_app(cfg: Optional[RelayConfig] = None) -> "Starlette":
    """Build the Starlette relay app. Requires Starlette/uvicorn/websockets."""
    if not _HAVE_STARLETTE:
        raise RuntimeError(
            "relay server deps (starlette/uvicorn/websockets) not installed; "
            "install relay requirements.txt"
        )
    cfg = cfg or RelayConfig.from_env()
    state = RelayState(cfg)

    async def scope_ep(ws):  # noqa: ANN001
        await _scope_endpoint(state, ws)

    async def browser_ws_ep(ws):  # noqa: ANN001
        await _browser_ws(state, ws)

    async def browser_http_ep(request):  # noqa: ANN001
        return await _browser_http(state, request)

    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        WebSocketRoute("/scope", scope_ep),
        WebSocketRoute("/h/{home_id}/ws", browser_ws_ep),
        Route("/h/{home_id}/{path:path}", browser_http_ep,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
    ]
    async def _housekeeping() -> None:
        """Expire idle rate-limiter buckets. RateLimiter.prune() documents itself
        as "call periodically from the server's housekeeping loop" and had NO
        production caller -- both limiters are keyed by client IP and grew for
        the life of the process."""
        while True:
            await asyncio.sleep(60.0)
            for limiter in (state.http_limiter, state.ws_limiter):
                with contextlib.suppress(Exception):
                    limiter.prune()

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        # `on_startup=` was removed in Starlette 0.5x; lifespan is the one
        # surviving hook. Keep the task on `state` so it is not collected.
        state.housekeeping_task = asyncio.create_task(_housekeeping())
        try:
            yield
        finally:
            state.housekeeping_task.cancel()
            with contextlib.suppress(BaseException):
                await state.housekeeping_task

    app = Starlette(routes=routes, lifespan=_lifespan)
    app.state.relay = state
    if state.oidc_signer.is_dev() or state.viewer_signer.is_dev():
        import logging
        logging.getLogger("relay").warning(
            "RELAY RUNNING WITH DEV-HMAC SIGNING KEYS -- NOT PRODUCTION SAFE. "
            "Mount RELAY_OIDC_SEED_FILE / RELAY_VIEWER_SEED_FILE."
        )
    return app


def main() -> None:  # pragma: no cover - process entrypoint
    """``python -m relay`` entrypoint: run the relay under uvicorn."""
    import uvicorn

    cfg = RelayConfig.from_env()
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.bind_host, port=cfg.bind_port,
                ws_max_size=MAX_PAYLOAD + 256 * 1024)
