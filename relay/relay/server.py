"""The relay HTTP/WS server shell (Starlette + websockets) -- production wiring.

This is the ONLY module that touches real sockets; everything it depends on
(``protocol``/``registry``/``proxy``/``connection``/``oidc``/``viewer_links``/
``ratelimit``) is transport-free and unit-tested off-wire. The single on-wire
WSS integration test exercises this shell end to end.

Endpoints:
  * ``GET  /healthz``                  liveness (no auth), with the build
                                       identity baked into the image.
  * ``WS   /scope``                    the HOME dials here (device-token auth via
                                       the HELLO frame). One per home; generation
                                       fencing evicts a stale socket.
  * ``WS   /h/{home_id}/ws``           a browser opens the tunnelled ``/ws``.
  * ``ANY  /h/{home_id}/{path:path}``  every other browser request -> tunnelled
                                       HTTP (SPA, /assets, /api, /auth).

The browser-facing routes forward the WHOLE app (SPA + API + ``/ws``) so the UI
needs ZERO component changes. ``home_id`` in the path is the stable routing key
(the single-instance affinity model; swap for a shared bus to scale out).

The current deployment forwards the home's signed session cookie so the home can
terminate authentication. Raw ``Authorization`` / ``X-Auth-Token`` carriers are
always stripped. Consequently the TLS-terminating relay is a trusted bearer-token
intermediary: it can observe and replay a cookie, but cannot mint one. Privilege-
defining and host-lifecycle routes are denied on every tunneled request at home.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hmac
import logging
import os
import signal
import time
from typing import Optional
from urllib.parse import urlsplit

from .config import (
    RelayConfig,
    load_device_tokens,
    load_seed,
    reload_device_tokens,
)
from .connection import ScopeConnection
from .headers import transform_request_headers, transform_response_headers
from .principal import PrincipalSigner
from .protocol import (
    MAX_PAYLOAD,
    MAX_WIRE_SIZE,
    Frame,
    ProtocolError,
    decode,
)
from .proxy import BrowserWS
from .ratelimit import RateLimiter
from .registry import HomeRegistry, ScopeTunnel

log = logging.getLogger("relay.server")

try:  # Starlette/uvicorn/websockets are optional at import time so the pure
    # core + tests run in a bare venv; the server shell needs them.
    from starlette.applications import Starlette
    from starlette.requests import ClientDisconnect
    from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
    from starlette.routing import Route, WebSocketRoute
    from starlette.websockets import WebSocket, WebSocketDisconnect

    _HAVE_STARLETTE = True
except Exception:  # noqa: BLE001 - server deps absent (pure-core/test env)
    _HAVE_STARLETTE = False

try:  # uvicorn's own formatters, for the path-only access log below (#520).
    from uvicorn.logging import AccessFormatter as _AccessFormatter
    from uvicorn.logging import DefaultFormatter as _DefaultFormatter
except Exception:  # noqa: BLE001 - uvicorn absent: nothing will run them
    _AccessFormatter = _DefaultFormatter = logging.Formatter


# --------------------------------------------------------------- shared state


class UpstreamTimeout(Exception):
    """The home went quiet mid-response. Raised out of the body generator so the
    transfer FAILS rather than completing short — see `_proxy_http`."""


class DownstreamBackpressure(Exception):
    """The browser stopped consuming a response and filled its bounded queue."""


class RelayState:
    """Process-wide relay state: the home registry, signers, and rate limiters.
    One per relay process (single-instance affinity)."""

    def __init__(self, cfg: RelayConfig):
        self.cfg = cfg
        token_to_home = load_device_tokens()
        self.registry = HomeRegistry(
            token_to_home=token_to_home,
            ping_interval_s=cfg.ping_interval_s,
            max_ping_misses=cfg.ping_max_misses,
        )
        self.oidc_signer = _make_signer(load_seed("RELAY_OIDC_SEED_FILE"),
                                        kid="oidc")
        self.viewer_signer = _make_signer(load_seed("RELAY_VIEWER_SEED_FILE"),
                                          kid="viewer")
        self.http_limiter = RateLimiter(cfg.http_rate, cfg.http_burst)
        self.ws_limiter = RateLimiter(cfg.ws_rate, cfg.ws_burst)
        self.scope_limiter = RateLimiter(cfg.scope_rate, cfg.scope_burst)
        self._pending_scope_handshakes = 0
        # This deployment deliberately serves one home per process/origin.
        # Serialize the register -> ACK -> commit window so a failed ACK can
        # transactionally restore the still-live previous generation.
        self.scope_registration_lock = asyncio.Lock()
        self._active_http_total = 0
        self._active_http_by_home: dict[str, int] = {}
        self._active_ws_total = 0
        self._active_ws_by_home: dict[str, int] = {}
        # home_id -> live ScopeConnection (the affinity table).
        self.connections: dict[str, ScopeConnection] = {}
        # Strong ref to the housekeeping loop; a bare create_task() result can be
        # garbage-collected mid-flight.
        self.housekeeping_task: "asyncio.Task | None" = None
        # Strong refs to in-flight evictions scheduled from the SIGHUP reload.
        self._evict_tasks: set = set()

    async def evict_home(self, home_id: str) -> bool:
        """FINISH revoking a home: drop its routing affinity and physically
        close its tunnel socket.

        The registry's own eviction only fences the registry layer (a re-HELLO
        with the revoked token is refused, and the home leaves ``_homes``). But
        browser traffic resolves the home through ``self.connections`` and the
        WSS stays open with its read/ping tasks -- so without this step a
        revoked home keeps serving the session an attacker already holds. That
        gap shipped and was caught in re-review (2026-09-05). Returns True if a
        live connection was torn down."""
        conn = self.connections.pop(home_id, None)
        if conn is None:
            return False
        close_socket = getattr(conn.tunnel, "close_socket", None)
        with contextlib.suppress(Exception):
            if close_socket is not None:
                await close_socket(1008)   # policy violation: token revoked
            await conn.close()
        return True

    def reload_tokens(self) -> dict:
        """Re-read the device-token file and apply it to the live registry
        (OPEN-002 durable rotation/revocation, no restart). Homes whose token
        vanished are evicted at BOTH layers: the registry refuses their
        re-HELLO and ``evict_home`` closes their live socket. Returns a
        counts-only summary; never returns or logs token material."""
        summary = reload_device_tokens(self.registry)
        for home_id in summary["evicted"]:
            self._schedule(self.evict_home(home_id))
        return summary

    def _schedule(self, coro) -> None:
        """Run ``coro`` on the event loop from a sync context (a signal
        callback), holding a strong reference so the task is not collected
        mid-flight. Off-loop (unit tests) the caller awaits evict_home itself."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        self._evict_tasks.add(task)
        task.add_done_callback(self._evict_tasks.discard)

    def try_begin_scope_handshake(self) -> bool:
        if self._pending_scope_handshakes >= self.cfg.scope_pending_max:
            return False
        self._pending_scope_handshakes += 1
        return True

    def end_scope_handshake(self) -> None:
        self._pending_scope_handshakes = max(
            0, self._pending_scope_handshakes - 1)

    def try_acquire_http(self, home_id: str) -> bool:
        per_home = self._active_http_by_home.get(home_id, 0)
        if (self._active_http_total >= self.cfg.http_max_total
                or per_home >= self.cfg.http_max_per_home):
            return False
        self._active_http_total += 1
        self._active_http_by_home[home_id] = per_home + 1
        return True

    def release_http(self, home_id: str) -> None:
        count = self._active_http_by_home.get(home_id, 0)
        if count <= 1:
            self._active_http_by_home.pop(home_id, None)
        else:
            self._active_http_by_home[home_id] = count - 1
        self._active_http_total = max(0, self._active_http_total - 1)

    def try_acquire_ws(self, home_id: str) -> bool:
        per_home = self._active_ws_by_home.get(home_id, 0)
        if (self._active_ws_total >= self.cfg.ws_max_total
                or per_home >= self.cfg.ws_max_per_home):
            return False
        self._active_ws_total += 1
        self._active_ws_by_home[home_id] = per_home + 1
        return True

    def release_ws(self, home_id: str) -> None:
        count = self._active_ws_by_home.get(home_id, 0)
        if count <= 1:
            self._active_ws_by_home.pop(home_id, None)
        else:
            self._active_ws_by_home[home_id] = count - 1
        self._active_ws_total = max(0, self._active_ws_total - 1)


def _make_signer(seed: bytes, *, kid: str) -> "PrincipalSigner | None":
    """Construct an Ed25519 signer only when a real seed was provisioned.

    The current server exposes neither relay-terminated OIDC nor viewer-link
    routes.  Keeping a public, fixed dev-HMAC signer alive in a production
    process anyway was an unnecessary latent token-forgery trap: a future route
    could accidentally use it.  Unit tests may still construct ``dev_hmac``
    explicitly, but the network server fails closed with no signer.
    """
    if seed:
        return PrincipalSigner.from_ed25519_seed(seed, kid=kid)
    return None


# ------------------------------------------------------- websockets glue (real)

class _StarletteScopeTunnel(ScopeTunnel):
    """Wraps a real Starlette ``WebSocket`` (the home's ``/scope`` socket) as a
    ``ScopeTunnel`` so the transport-free core sends frames over the wire."""

    def __init__(self, ws: "WebSocket", *, send_timeout_s: float):
        super().__init__(self._send, conn_id=f"scope-{id(ws):x}")
        self._ws = ws
        self._send_timeout_s = send_timeout_s
        self._send_lock = asyncio.Lock()
        #: The code of the FIRST close the relay itself sent on this socket,
        #: or None while only the home (or the network) has ended it. Read by
        #: the end line (#521): once the relay has hung up, the disconnect the
        #: read loop sees next is the echo of that close, and reporting its
        #: code alone would make the relay's decision read as the home's.
        self.relay_close_code: Optional[int] = None

    async def _send(self, frame: Frame) -> None:
        async with self._send_lock:
            await asyncio.wait_for(
                self._ws.send_bytes(frame.encode()),
                timeout=self._send_timeout_s,
            )

    async def close_socket(self, code: int = 1012) -> None:
        """Physically close this generation's WSS, serialised with writes."""
        self.closed = True
        if self.relay_close_code is None:
            self.relay_close_code = code
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self._ws.close(code)


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


# ------------------------------------------------ a tunnel's end, said (#521)
#
# The rig's tunnel dropped in clusters with "no close frame" on the rig's side,
# and this log held the new generation's handshake and nothing about the old
# one ending. A close the relay made, a close the home sent and a cut in the
# network between them were indistinguishable, because none of them was
# written down. So the end of every registered tunnel is one line, and the
# next cluster classifies itself.
#
# What the line carries is chosen as the access log's query is withheld
# (#520): the home id, which is the routing key already in every tunnelled
# path, the generation, the lifetime and how it ended. Never the device token,
# a cookie or the home's IP.

#: The code a WebSocket stack reports for a connection that ended with no
#: close frame at all (RFC 6455 7.1.5: reserved, never sent on the wire).
#: uvicorn's websockets and wsproto protocols both report it for a dropped
#: TCP connection.
NO_CLOSE_FRAME_CODE = 1006

#: The reserved codes that carry no status from the peer, so neither names a
#: close the home chose. 1006 is the dropped connection above. 1005 ("no status
#: received", RFC 6455 7.4.1, also never sent on the wire) is what the newest
#: uvicorn/websockets stack reports for the SAME abrupt drop: the CI runner,
#: installing the relay's unpinned requirements on 2026-09-30, logged
#: "close=1005" where the Windows dev box logged "no close frame" (#597's
#: library difference). Both read as "no close frame", the rig's own words.
NO_STATUS_CODES = (1005, NO_CLOSE_FRAME_CODE)

#: Why the relay sends each code it closes a LIVE tunnel with. One call site
#: per code: the keepalive (``_ping_loop``), revocation (``evict_home``) and
#: generation fencing, where a newer HELLO replaces this socket. The 1012 the
#: handler's own ``finally`` sends comes after the line is written, so it
#: never reaches here.
_RELAY_CLOSE_WHY = {
    1001: "no PONG within the keepalive deadline",
    1008: "its device token was revoked",
    1012: "a newer generation replaced it",
}


def tunnel_end_line(home_id: str, generation: int, lived_s: float, *,
                    received_code: Optional[int] = None,
                    relay_code: Optional[int] = None,
                    error: str = "") -> str:
    """The one line the relay writes when a home's registered tunnel ends.

    ``relay_code`` is the relay's own first close on the socket, and wins:
    the disconnect that follows it is only the echo. Otherwise
    ``received_code`` is what the read loop's disconnect carried, which is
    the home's close code, or the reserved 1006 for none at all, printed as
    the words the rig's side of the same drop uses. ``error`` names a read
    that ended some other way (a frame the relay could not decode)."""
    head = (f"home tunnel ended: home={home_id} gen={int(generation)} "
            f"lived={max(0.0, lived_s):.1f}s")
    if relay_code is not None:
        why = _RELAY_CLOSE_WHY.get(relay_code, "")
        return (f"{head} close={relay_code} sent by the relay"
                + (f": {why}" if why else ""))
    if error:
        return f"{head} close=none: {error}"
    if received_code is None or received_code in NO_STATUS_CODES:
        return f"{head} close=no close frame"
    return f"{head} close={received_code}"


# ----------------------------------------------------------------- handlers

def _client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "?"


def _canonical_origin(raw: str) -> str:
    """Return a normalized HTTP(S) origin, or ``""`` for an invalid value."""
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}):
            return ""
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        if port is not None and not (
                (scheme == "https" and port == 443)
                or (scheme == "http" and port == 80)):
            host = f"{host}:{port}"
        return f"{scheme}://{host}"
    except (TypeError, ValueError):
        return ""


def _expected_browser_origin(state: RelayState, headers) -> str:
    scheme = "https" if state.cfg.https else "http"
    public = (state.cfg.origin or "").strip()
    if not public:
        public = (headers.get("host") or "").strip()
    if "://" not in public:
        public = f"{scheme}://{public}"
    return _canonical_origin(public)


def _browser_origin_allowed(state: RelayState, headers) -> bool:
    supplied = _canonical_origin((headers.get("origin") or "").strip())
    expected = _expected_browser_origin(state, headers)
    if not supplied or not expected:
        return False
    return hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8"))


async def _scope_endpoint(state: RelayState, ws: "WebSocket") -> None:
    """Handle ONE home ``/scope`` WSS: HELLO -> register -> read loop + ping."""
    ip = _client_ip(ws.scope)
    if (not state.scope_limiter.allow(ip)
            or not state.try_begin_scope_handshake()):
        with contextlib.suppress(Exception):
            await ws.close(1013)
        return

    # An unauthenticated peer gets only a small, finite window in which to send
    # the mandatory HELLO. Release the pending-handshake slot immediately after
    # that first frame, not after the long-lived home connection closes.
    try:
        await ws.accept()
        try:
            raw = await asyncio.wait_for(
                ws.receive_bytes(), timeout=state.cfg.scope_hello_timeout_s)
            first = decode(raw)
        except asyncio.TimeoutError:
            await ws.close(1008)
            return
        except (ProtocolError, WebSocketDisconnect, RuntimeError):
            await ws.close(1002)
            return
        except Exception:  # noqa: BLE001 - malformed transport; fail closed
            await ws.close(1002)
            return
    finally:
        state.end_scope_handshake()

    tunnel = _StarletteScopeTunnel(
        ws, send_timeout_s=state.cfg.upstream_timeout_s)
    conn = ScopeConnection(
        state.registry, tunnel, ws_egress_max=state.cfg.ws_egress_max)
    async with state.scope_registration_lock:
        try:
            ack = conn.handle_hello(first, defer_commit=True)
            await tunnel.send_frame(ack)
        except Exception:  # noqa: BLE001 - invalid HELLO/auth state
            # Registration is provisional until the positive ACK is known to
            # be on the wire. Roll it back so an ACK write failure cannot leave
            # a ghost route or evict a healthy previous generation.
            with contextlib.suppress(Exception):
                await conn.close()
            await tunnel.close_socket(1008)
            return
        if ack.header.get("ok") is not True:
            await tunnel.close_socket(1008)
            return
        home_id = conn.reg.home_id
        if (state.registry.get(home_id) is not conn.reg
                or not conn.commit_registration()):
            await tunnel.close_socket(1012)
            await conn.close()
            return

        prior = state.connections.get(home_id)
        state.connections[home_id] = conn
        if prior is not None and prior is not conn:
            # Generation fencing must evict the physical socket, not merely set
            # the transport-free flag. Otherwise its read/ping tasks linger.
            close_socket = getattr(prior.tunnel, "close_socket", None)
            if close_socket is not None:
                with contextlib.suppress(Exception):
                    await close_socket(1012)
            with contextlib.suppress(Exception):
                await prior.close()
    # The tunnel is live from here: this is the moment its lifetime counts
    # from, and every way out of the loop below reaches the end line.
    live_since = time.monotonic()
    received_code: Optional[int] = None
    read_error = ""
    ping_task = asyncio.ensure_future(_ping_loop(state, conn, ws))
    try:
        while True:
            raw = await ws.receive_bytes()
            frame = decode(raw)
            reply = await conn.on_frame(frame)
            if reply is not None:
                await tunnel.send_frame(reply)
    except WebSocketDisconnect as exc:
        received_code = exc.code
    except ProtocolError:
        read_error = "the home sent a frame the relay could not decode or route"
    except Exception as exc:  # noqa: BLE001 - any read error tears the tunnel down
        # The class only: an exception's text from a socket layer can carry
        # a peer address, and the line must not.
        read_error = f"the read failed ({type(exc).__name__})"
    finally:
        ping_task.cancel()
        with contextlib.suppress(BaseException):
            await ping_task
        # Written before this handler's own 1012 below, so ``relay_code`` is
        # only ever a close the relay decided on while the tunnel was live.
        with contextlib.suppress(Exception):
            log.info("%s", tunnel_end_line(
                home_id, conn.reg.generation, time.monotonic() - live_since,
                received_code=received_code,
                relay_code=tunnel.relay_close_code, error=read_error))
        # Only clear the affinity entry if we are still the live connection
        # (a higher-generation redial may have replaced us).
        if state.connections.get(home_id) is conn:
            state.connections.pop(home_id, None)
        await tunnel.close_socket(1012)
        await conn.close()


async def _ping_loop(state: RelayState, conn: ScopeConnection,
                     ws: "WebSocket") -> None:
    """Send keepalive PINGs and tear the tunnel down on PONG starvation."""
    try:
        while True:
            await asyncio.sleep(state.cfg.ping_interval_s)
            if conn.is_stale():
                close_socket = getattr(conn.tunnel, "close_socket", None)
                if close_socket is not None:
                    await close_socket(1001)
                else:
                    await ws.close(1001)
                return
            await conn.tunnel.send_frame(conn.make_ping())
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - socket gone
        return


async def _browser_http(state: RelayState, request) -> "StreamingResponse":
    """Tunnel ONE browser HTTP request to the home and stream the response."""
    home_id = request.path_params["home_id"]
    path = "/" + request.path_params.get("path", "")
    ip = _client_ip(request.scope)

    # Every browser mutation carrying a session cookie must originate from the
    # relay's own origin. This rejects cross-site form/fetch CSRF before it can
    # consume a tunnel stream. GET/HEAD/OPTIONS remain usable for navigation,
    # health checks, and ordinary non-browser clients.
    if (request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
            and not _browser_origin_allowed(state, request.headers)):
        return PlainTextResponse("invalid browser origin", status_code=403)
    if not state.http_limiter.allow(ip):
        return PlainTextResponse("rate limited", status_code=429)
    conn = state.connections.get(home_id)
    if conn is None or conn.mux is None:
        return PlainTextResponse("home not connected", status_code=502)

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            return PlainTextResponse("invalid content-length", status_code=400)
        if declared_length < 0:
            return PlainTextResponse("invalid content-length", status_code=400)
        if declared_length > state.cfg.max_body:
            return PlainTextResponse("payload too large", status_code=413)

    if not state.try_acquire_http(home_id):
        return PlainTextResponse(
            "too many concurrent requests", status_code=503,
            headers={"Retry-After": "1"})

    lease_held = True

    def release_lease() -> None:
        nonlocal lease_held
        if lease_held:
            lease_held = False
            state.release_http(home_id)

    try:
        query = request.url.query or ""
        headers = transform_request_headers(
            request.scope.get("headers", []), forward_cookie=True)
    except (TypeError, ValueError, UnicodeError):
        release_lease()
        return PlainTextResponse("invalid request headers", status_code=400)
    head_holder: dict = {}
    head_ready = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue(
        maxsize=state.cfg.http_egress_chunks)
    failure: dict[str, str] = {}
    stream_holder: dict[str, int] = {}
    aborted = False

    async def on_head(status: int, hdrs: list) -> None:
        head_holder["status"] = status
        head_holder["headers"] = hdrs
        head_ready.set()

    async def on_data(chunk: bytes, eof: bool) -> None:
        if failure:
            return
        try:
            chunks.put_nowait((chunk, eof))
        except asyncio.QueueFull:
            # Never await a slow browser here: this callback runs on the ONE
            # shared home-tunnel reader, and blocking it would stall every
            # sibling request/viewer. Drop the queued tail, wake the response
            # generator so it fails the transfer, and abort only this stream.
            failure["reason"] = "browser response buffer full"
            with contextlib.suppress(asyncio.QueueEmpty):
                while True:
                    chunks.get_nowait()
            chunks.put_nowait((b"", True))
            sid = stream_holder.get("id")
            if sid is not None:
                # Do not await a relay->home write on the one shared home read
                # loop. The isolated task is bounded by the tunnel send timeout;
                # the response generator also aborts defensively in ``finally``.
                asyncio.create_task(
                    abort_stream("browser response buffer full"))

    async def abort_stream(reason: str) -> None:
        nonlocal aborted
        sid = stream_holder.get("id")
        if aborted or sid is None:
            return
        aborted = True
        with contextlib.suppress(Exception):
            await conn.mux.abort_request(sid, reason)

    try:
        stream_id = await conn.mux.open_request(
            request.method, path, query, headers,
            # Always stream a terminal REQ_DATA frame. This avoids buffering to
            # discover whether an unusual GET/DELETE carries a body.
            has_body=True, on_head=on_head, on_data=on_data,
        )
        stream_holder["id"] = stream_id
        if failure:
            await abort_stream(failure["reason"])

        # Incrementally forward request bytes. ``request.body()`` used to
        # materialize the entire attacker-controlled body before checking the
        # limit, which made RELAY_MAX_BODY an allocation target rather than a
        # protection.
        received = 0
        upload_started = asyncio.get_running_loop().time()
        request_body_iter = request.stream().__aiter__()
        while not head_ready.is_set():
            remaining = (state.cfg.request_total_timeout_s
                         - (asyncio.get_running_loop().time() - upload_started))
            if remaining <= 0:
                await abort_stream("request upload exceeded total timeout")
                release_lease()
                return PlainTextResponse("request upload timed out", status_code=408)
            try:
                chunk = await asyncio.wait_for(
                    request_body_iter.__anext__(),
                    timeout=min(state.cfg.request_body_timeout_s, remaining),
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                await abort_stream("request upload stalled")
                release_lease()
                return PlainTextResponse("request upload timed out", status_code=408)
            if not chunk:
                continue
            received += len(chunk)
            if received > state.cfg.max_body:
                await abort_stream("request payload too large")
                release_lease()
                return PlainTextResponse("payload too large", status_code=413)
            for offset in range(0, len(chunk), MAX_PAYLOAD):
                await conn.mux.send_request_body(
                    stream_id, chunk[offset:offset + MAX_PAYLOAD], eof=False)
        await conn.mux.send_request_body(stream_id, b"", eof=True)
    except ClientDisconnect:
        await abort_stream("browser disconnected during request upload")
        release_lease()
        return PlainTextResponse("client disconnected", status_code=400)
    except Exception:  # noqa: BLE001 - tunnel/write failure
        await abort_stream("request upload failed")
        release_lease()
        return PlainTextResponse("home tunnel failed", status_code=502)

    # A DEAD TUNNEL MUST NOT PARK THIS HANDLER FOREVER. When a tunnel drops,
    # shutdown() clears `_exchanges`/`req_routes`, so nothing can ever call
    # on_head for this stream again -- an unbounded wait here leaked the task,
    # its buffered body and its queue for the life of the process, and left the
    # browser spinning with no error to show.
    try:
        await asyncio.wait_for(head_ready.wait(), timeout=state.cfg.upstream_timeout_s)
    except asyncio.TimeoutError:
        await abort_stream("relay upstream timeout")
        release_lease()
        return PlainTextResponse("home did not respond", status_code=504)
    try:
        status = int(head_holder.get("status", 502))
        if not 100 <= status <= 599:
            raise ValueError("invalid upstream status")
        raw_headers = head_holder.get("headers", [])
        if not isinstance(raw_headers, list):
            raise ValueError("invalid upstream header list")
        out_headers = transform_response_headers(
            # Host-only cookies are narrower than Domain cookies (subdomains
            # cannot receive them) and are valid on the relay host without a
            # Domain rewrite.
            raw_headers, relay_origin="", relay_https=state.cfg.https,
        )
        encoded_headers: list[tuple[bytes, bytes]] = []
        separators = frozenset('()<>@,;:\\"/[]?={} \t')
        for item in out_headers:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("invalid upstream header pair")
            name, value = item
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError("invalid upstream header value")
            if (not name or any(ord(ch) <= 32 or ord(ch) >= 127
                                or ch in separators for ch in name)
                    or any((ord(ch) < 32 and ch != "\t")
                           or ord(ch) == 127 for ch in value)):
                raise ValueError("unsafe upstream header")
            encoded_headers.append(
                (name.encode("latin-1"), value.encode("latin-1")))
    except (TypeError, ValueError, UnicodeError):
        await abort_stream("invalid response metadata")
        release_lease()
        return PlainTextResponse("invalid response from home", status_code=502)

    async def body_iter():
        # Same bound on the body: a tunnel that dies after the head but before
        # EOF would otherwise hang this response open forever.
        complete = False
        try:
            while True:
                try:
                    chunk, eof = await asyncio.wait_for(
                        chunks.get(), timeout=state.cfg.upstream_timeout_s)
                except asyncio.TimeoutError:
                    await abort_stream("relay upstream timeout")
                    # RAISE, DO NOT RETURN. Returning ends the chunked response
                    # cleanly and makes a truncated download look complete.
                    raise UpstreamTimeout(
                        f"home stopped sending after the response head "
                        f"(waited {state.cfg.upstream_timeout_s:g}s)")
                if failure:
                    await abort_stream(failure["reason"])
                    raise DownstreamBackpressure(failure["reason"])
                if chunk:
                    yield chunk
                if eof:
                    complete = True
                    break
        finally:
            if not complete:
                await abort_stream("browser response ended before EOF")
            release_lease()

    resp = StreamingResponse(body_iter(), status_code=status)
    # Replace the default content-type/headers with the transformed home headers.
    resp.raw_headers = encoded_headers
    return resp


async def _browser_ws(state: RelayState, ws: "WebSocket") -> None:
    """Tunnel ONE browser ``/ws`` to the home and fan ``WS_DATA`` back."""
    home_id = ws.path_params["home_id"]
    ip = _client_ip(ws.scope)
    if not _browser_origin_allowed(state, ws.headers):
        await ws.close(1008)
        return
    conn = state.connections.get(home_id)
    if (conn is None or conn.mux is None
            or not state.ws_limiter.allow(ip)
            or not state.try_acquire_ws(home_id)):
        await ws.close(1013)
        return

    ws_id: str | None = None
    try:
        await ws.accept()
        browser = _StarletteBrowserWS(ws)
        headers = transform_request_headers(
            ws.scope.get("headers", []), forward_cookie=True)
        ws_id = await conn.mux.open_ws(
            browser, "/ws", ws.url.query or "", headers)
        # The home /ws is send-only, so we never forward browser->home frames;
        # receiving any application frame is a policy violation. Closing on the
        # first one also prevents a client from using this socket as an upload
        # bandwidth sink.
        await ws.receive_text()
        await ws.close(1008)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await ws.close(1011)
    finally:
        if ws_id is not None:
            await conn.mux.close_ws(ws_id)
        state.release_ws(home_id)


# What /healthz says when the image carries no build identity. An explicit
# word, because the alternative is a guess: the package's own __version__ has
# read 0.1.0 since the relay was written, so it cannot tell one build from
# another.
BUILD_UNKNOWN = "unknown"


def build_identity(environ=None) -> dict:
    """The version and git commit baked into the image at build time (#462).

    The Dockerfile declares both as build args and passes them to env, and
    ``scripts/deploy_relay.ps1`` sets them and then waits for /healthz to
    report its commit. That is how a deploy is verified, and drift noticed,
    from outside with no Fly credential: the relay once ran July code for two
    months and nothing said so. An image built without the args has each
    variable set to "" (an ARG with no value expands to that), so empty and
    blank read as unset."""
    env = os.environ if environ is None else environ

    def _read(name: str) -> str:
        return (env.get(name) or "").strip() or BUILD_UNKNOWN

    return {
        "version": _read("RELAY_BUILD_VERSION"),
        "commit": _read("RELAY_BUILD_COMMIT"),
    }


async def _healthz(request, build: dict) -> "JSONResponse":
    return JSONResponse({
        "ok": True,
        "ts": time.time(),
        "version": build["version"],
        "commit": build["commit"],
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

    # Read once: the environment is fixed when the image is built, so the
    # identity cannot change for the life of the process.
    build = build_identity()

    async def healthz_ep(request):  # noqa: ANN001
        return await _healthz(request, build)

    routes = [
        Route("/healthz", healthz_ep, methods=["GET"]),
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
            for limiter in (
                    state.http_limiter, state.ws_limiter, state.scope_limiter):
                with contextlib.suppress(Exception):
                    limiter.prune()

    def _reload_tokens_signal() -> None:
        # SIGHUP: the operator edited the mounted device-token file (rotated or
        # revoked a token). Apply it without a restart; log COUNTS ONLY.
        try:
            summary = state.reload_tokens()
            log.info(
                "device tokens reloaded: homes=%d tokens=%d evicted=%d",
                summary["homes"], summary["tokens"], len(summary["evicted"]),
            )
        except Exception as exc:  # noqa: BLE001 - reload must never crash the relay
            log.warning("device-token reload failed: %s", exc)

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        # `on_startup=` was removed in Starlette 0.5x; lifespan is the one
        # surviving hook. Keep the task on `state` so it is not collected.
        state.housekeeping_task = asyncio.create_task(_housekeeping())
        loop = asyncio.get_running_loop()
        sighup = getattr(signal, "SIGHUP", None)   # absent on Windows
        if sighup is not None:
            # add_signal_handler is unavailable on some loops (Windows Proactor);
            # a missing reload signal is a soft degradation, not a startup error.
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sighup, _reload_tokens_signal)
        try:
            yield
        finally:
            if sighup is not None:
                with contextlib.suppress(
                        NotImplementedError, RuntimeError, ValueError):
                    loop.remove_signal_handler(sighup)
            state.housekeeping_task.cancel()
            with contextlib.suppress(BaseException):
                await state.housekeeping_task

    app = Starlette(routes=routes, lifespan=_lifespan)
    app.state.relay = state
    return app


# ------------------------------------------------ the logs: paths only (#520)
#
# THE RELAY LOGS WHERE A REQUEST WENT, NEVER WHAT IT ASKED. uvicorn's access
# line is `GET /h/home-1/api/cloudmap/at?alt=..&az=.. HTTP/1.1` by default, and
# on 2026-09-28 the Fly relay's log held the mount's live pointing exactly that
# way, several times a minute. A pointing at a known time is a function of the
# site, and this log belongs to a third party's pipeline.
#
# The UI no longer puts a coordinate in any URL, and the rig refuses one that
# does; this is the third wall, and the only one that covers a route written
# next year. Every query string is withheld, whatever it carries: a list of
# the sensitive names would be one more thing to keep up to date, and the
# path is what anyone reading a relay log is looking for.
#
# Two formatters because uvicorn writes a request target in two places: the
# access log for HTTP, and its error logger for a WebSocket handshake
# (`"WebSocket /h/home-1/ws?..." [accepted]`). relay/relay itself formats no
# request path or URL into any log call; the two it has log counts and an
# exception, and they are left as they are.

#: What a withheld query string reads as. It still says one was there, which
#: is the part a person diagnosing a tunnel can use. No space in it: the
#: request line is split on spaces by anything that parses an access log.
QUERY_WITHHELD = "?<withheld>"


def _path_only(value: object) -> object:
    """A request target with its query string replaced by a marker. Anything
    that is not a path carrying a query comes back untouched, so the same rule
    can be run over every argument of every uvicorn record."""
    if isinstance(value, str) and value.startswith("/") and "?" in value:
        return value.split("?", 1)[0] + QUERY_WITHHELD
    return value


def _without_queries(record: logging.LogRecord) -> logging.LogRecord:
    """A copy of ``record`` whose request targets have lost their queries.

    A COPY, because the record is shared: every handler on the logger and its
    parents is handed the same object, and rewriting it in place would change
    what they see as a side effect of which formatter happened to run first."""
    args = record.args
    if not isinstance(args, tuple) or not any(
            _path_only(a) is not a for a in args):
        return record
    clone = copy.copy(record)
    clone.args = tuple(_path_only(a) for a in args)
    return clone


class PathOnlyAccessFormatter(_AccessFormatter):
    """uvicorn's access formatter, with the query string withheld."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(_without_queries(record))


class PathOnlyDefaultFormatter(_DefaultFormatter):
    """uvicorn's default formatter (its error logger, which is where the
    WebSocket handshake lines go), with the query string withheld."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(_without_queries(record))


def uvicorn_log_config() -> dict:
    """uvicorn's own logging config with both formatters swapped for the
    path-only ones. Copied, not edited: ``LOGGING_CONFIG`` is uvicorn's module
    global, and uvicorn writes into the dict it is handed.

    It also gives the relay's own ``relay`` loggers a handler (#521, #556).
    uvicorn's config configures only uvicorn's loggers and leaves the root
    bare, so an info line from ``relay.server`` fell through to Python's
    last-resort handler, which prints warnings and above: the SIGHUP reload's
    counts line and the tunnel end line were written by the code and never
    printed. Through the default handler they share uvicorn's stream and the
    path-only formatter; ``propagate`` is off as it is for uvicorn's own, so
    a root handler added later cannot print them twice."""
    from uvicorn.config import LOGGING_CONFIG

    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["formatters"]["access"]["()"] = PathOnlyAccessFormatter
    cfg["formatters"]["default"]["()"] = PathOnlyDefaultFormatter
    cfg["loggers"]["relay"] = {
        "handlers": ["default"], "level": "INFO", "propagate": False}
    return cfg


def uvicorn_options(cfg: RelayConfig) -> dict:
    """Every keyword ``main`` hands ``uvicorn.run``, in one place so the test
    of the access log runs the server exactly as production does."""
    return dict(host=cfg.bind_host, port=cfg.bind_port,
                proxy_headers=bool(cfg.forwarded_allow_ips),
                forwarded_allow_ips=cfg.forwarded_allow_ips or "",
                access_log=cfg.uvicorn_access_log,
                log_config=uvicorn_log_config(),
                ws_max_size=MAX_WIRE_SIZE, ws_max_queue=16,
                limit_concurrency=256, backlog=128, timeout_keep_alive=5,
                h11_max_incomplete_event_size=65536)


def main() -> None:  # pragma: no cover - process entrypoint
    """``python -m relay`` entrypoint: run the relay under uvicorn."""
    from .runtime_security import require_unprivileged_runtime

    # Refuse an over-privileged service identity before reading configuration
    # or secrets and before importing the ASGI server.
    require_unprivileged_runtime("AstroDeck relay")

    import uvicorn

    cfg = RelayConfig.from_env()
    app = create_app(cfg)
    uvicorn.run(app, **uvicorn_options(cfg))
