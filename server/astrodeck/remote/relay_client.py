"""The SCOPE-SIDE dial-out client (W3.3.0).

ONE opt-in lifespan background task. When ``RemoteConfig.enabled`` and a
``relay_url`` are set, the home dials ONE outbound WSS to the public relay,
registers with its ``device_token`` (HELLO), then services tunneled traffic:

  * REQ_OPEN/REQ_DATA  -> replay an HTTP request against the IN-PROCESS ASGI app
    (``await app(scope, receive, send)`` -- NO network hop) and stream the
    response back as RESP_HEAD/RESP_DATA.
  * WS_OPEN            -> subscribe to the event bus and stream each /ws event
    down as WS_DATA (server->client ONLY; the tunneled /ws is send-only, exactly
    like the on-LAN /ws).

THE REMOTE-FLAG MECHANISM (critical, not LAN-spoofable): every replayed request
scope (and the tunneled /ws scope) gets ``scope['state']['astrodeck_remote'] =
True``. The home reads that via ``scope_is_remote`` and passes ``remote=True`` into
``resolve_principal``, which hard-denies the open ``none`` provider remotely. This
is ASGI scope STATE, not a header, so an on-LAN attacker cannot forge it (a real
uvicorn-borne request has no such key). Inbound ``authorization`` / ``x-auth-token``
/ session-cookie headers are STRIPPED at the replay shim -- they are not valid
tunnel carriers; a remote principal is injected ONLY from a home-verifiable
``principal_token`` (verification owned by the auth/relay lane; this client carries
the token through but does not mint it).

ISOLATION: this never blocks or crashes the app lifespan. A relay outage -> the
client retries with capped backoff + full jitter and the home runs local-only.
Each redial bumps ``generation`` (fencing). The whole module lazy-imports
``websockets`` so a LAN-only install (the default, ``enabled=False``) never needs
the dependency.
"""
from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from ..config import RemoteConfig
from ..events import Event, bus
from .protocol import (DEFAULT_MAX_PAYLOAD, PROTO_VERSION, Frame, FrameType,
                       decode_frame, encode_frame)

# The ASGI scope state key that marks a request/ws as relay-tunneled. Read by the
# home via ``scope_is_remote`` -> passed as ``remote=`` to ``resolve_principal``.
# A real uvicorn request never sets this, so it is NOT LAN-spoofable.
REMOTE_SCOPE_KEY = "astrodeck_remote"

# Request/response headers an on-LAN attacker (or the relay) must NOT be able to
# use as an auth carrier over the tunnel. Stripped from every replayed request.
_STRIPPED_INBOUND_HEADERS = frozenset({
    b"authorization", b"x-auth-token", b"cookie",
})

# Reconnect backoff: capped exponential with FULL jitter. Starts ~0.5s, caps at
# ~15s (the pinned ceiling). Full jitter (random in [0, computed]) avoids a
# thundering-herd reconnect if many homes share a relay restart.
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 15.0

# Per-viewer /ws fanout buffer (drop-oldest). A slow remote viewer must never
# stall the single shared bus subscription that feeds every viewer.
_WS_BUFFER_MAX = 200


def scope_is_remote(scope: dict) -> bool:
    """True iff this ASGI scope was relay-tunneled (the W3 remote flag).

    Reads ``scope['state']['astrodeck_remote']``. Safe on any scope shape (a
    missing ``state`` or key returns False), so on-LAN requests -- which never
    carry the key -- are always non-remote."""
    state = scope.get("state")
    if not isinstance(state, dict):
        return False
    return bool(state.get(REMOTE_SCOPE_KEY, False))


def _backoff_delay(attempt: int) -> float:
    """Capped-exponential FULL-jitter backoff for redial ``attempt`` (0-based)."""
    ceiling = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** attempt))
    return random.uniform(0.0, ceiling)


def _headers_to_scope(header_list: Iterable[Any]) -> list[tuple[bytes, bytes]]:
    """Turn a JSON ``[[name, value], ...]`` header list into ASGI raw headers
    (lowercased bytes), DROPPING any inbound auth carrier (auth headers are not
    valid tunnel carriers; the principal is injected separately)."""
    raw: list[tuple[bytes, bytes]] = []
    for item in header_list or []:
        try:
            name, value = item
        except (TypeError, ValueError):
            continue
        nb = str(name).lower().encode("latin-1", "replace")
        if nb in _STRIPPED_INBOUND_HEADERS:
            continue
        raw.append((nb, str(value).encode("latin-1", "replace")))
    return raw


def _scope_headers_to_list(raw_headers: Iterable[tuple[bytes, bytes]]) -> list[list[str]]:
    """ASGI raw response headers -> JSON-safe ``[[name, value], ...]``."""
    out: list[list[str]] = []
    for name, value in raw_headers:
        out.append([name.decode("latin-1", "replace"),
                    value.decode("latin-1", "replace")])
    return out


class _RequestStream:
    """One in-flight tunneled HTTP request replayed against the in-process app.

    Owns the ASGI ``receive``/``send`` shims for a single ``stream_id``: REQ_DATA
    frames feed the request-body ``receive`` queue; the app's ``send`` events are
    translated to RESP_HEAD / RESP_DATA frames and AWAIT the WSS send (natural
    backpressure -- the streaming-response shim won't pull the next body chunk
    until the prior frame is on the wire)."""

    def __init__(self, stream_id: int, send_frame: Callable[[Frame], Awaitable[None]]):
        self.stream_id = stream_id
        self._send_frame = send_frame
        self._body: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue()
        self._aborted = False

    def feed_body(self, chunk: bytes, eof: bool) -> None:
        self._body.put_nowait((chunk, eof))

    def abort(self) -> None:
        self._aborted = True
        # Unblock a receive() awaiting more body so the app task can unwind.
        self._body.put_nowait((b"", True))

    async def receive(self) -> dict:
        if self._aborted:
            return {"type": "http.disconnect"}
        chunk, eof = await self._body.get()
        if self._aborted:
            return {"type": "http.disconnect"}
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": not eof,
        }

    async def send(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == "http.response.start":
            await self._send_frame(Frame(
                type=FrameType.RESP_HEAD,
                stream_id=self.stream_id,
                header={
                    "status": int(message.get("status", 200)),
                    "headers": _scope_headers_to_list(message.get("headers", [])),
                },
            ))
        elif mtype == "http.response.body":
            body = message.get("body", b"") or b""
            more = bool(message.get("more_body", False))
            # Chunk to the payload ceiling so a large FileResponse/FITS streams in
            # bounded frames (and never lands in one oversize frame). AWAIT each
            # send -> the app is naturally backpressured by the WSS write.
            mv = memoryview(body)
            if not mv:
                await self._send_frame(Frame(
                    type=FrameType.RESP_DATA, stream_id=self.stream_id,
                    header={"eof": not more}, payload=b""))
                return
            total = len(mv)
            off = 0
            while off < total:
                end = min(off + DEFAULT_MAX_PAYLOAD, total)
                last = end >= total
                await self._send_frame(Frame(
                    type=FrameType.RESP_DATA, stream_id=self.stream_id,
                    header={"eof": last and not more},
                    payload=bytes(mv[off:end])))
                off = end


class RelayClient:
    """The scope-side dial-out client. Construct with the in-process ASGI app and
    a ``RemoteConfig`` provider, then ``await run()`` (the lifespan launches this
    as an isolated background task).

    Reconnect/backoff and per-frame dispatch live here; the per-request and
    per-ws state lives in ``_RequestStream`` and the ws fanout. The client NEVER
    raises out of ``run()`` -- every connection error degrades to a backoff +
    redial, so the home stays local-only while the relay is unreachable."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        config_provider: Callable[[], RemoteConfig],
        *,
        connect: Callable[[str], Awaitable[Any]] | None = None,
    ):
        self._app = app
        self._config = config_provider
        # Injectable connect (tests pass a fake bidi channel); default lazy-imports
        # websockets so a LAN-only install never needs the dependency.
        self._connect = connect or self._default_connect
        self._generation = 0
        self._stop = asyncio.Event()
        # Live per-connection state (reset on each (re)connect).
        self._ws: Any = None
        self._send_lock = asyncio.Lock()
        self._reqs: dict[int, _RequestStream] = {}
        self._req_tasks: dict[int, asyncio.Task] = {}
        self._ws_streams: dict[int, asyncio.Task] = {}

    # -- public lifecycle ------------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    def stop(self) -> None:
        """Signal the run loop to stop (called on app shutdown)."""
        self._stop.set()

    async def run(self) -> None:
        """The reconnect supervisor: dial, serve, redial with capped backoff.

        Returns when ``stop()`` is signaled. NEVER raises -- a connection failure
        is logged once and retried; the home runs local-only meanwhile."""
        attempt = 0
        while not self._stop.is_set():
            cfg = self._config()
            if not (cfg.enabled and cfg.relay_url):
                # Disabled / unconfigured: idle until stop (the lifespan only
                # launches us when enabled, but re-check defensively for a live
                # config edit toggling us off).
                return
            try:
                self._generation += 1
                await self._serve_once(cfg)
                attempt = 0  # a clean session resets the backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - degrade to local-only, never crash
                bus.log("warning",
                        f"relay connection lost ({type(exc).__name__}: {exc}); "
                        f"retrying local-only", "remote")
            if self._stop.is_set():
                break
            delay = _backoff_delay(attempt)
            attempt += 1
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    # -- connection ------------------------------------------------------------

    async def _default_connect(self, url: str) -> Any:
        """Open the outbound WSS. Lazy-imports ``websockets`` so the dependency is
        only required when the relay is actually used."""
        import websockets  # lazy: optional dependency, only on the remote path
        return await websockets.connect(url, max_size=None)

    async def _serve_once(self, cfg: RemoteConfig) -> None:
        """One full connection lifetime: connect, HELLO, dispatch frames until the
        socket closes. Cleans up all per-connection state on exit."""
        ws = await self._connect(cfg.relay_url)
        self._ws = ws
        self._reqs = {}
        self._req_tasks = {}
        self._ws_streams = {}
        try:
            await self._raw_send(encode_frame(
                FrameType.HELLO, 0, {
                    "device_token": cfg.device_token,
                    "home_id": cfg.home_id,
                    "generation": self._generation,
                    "proto_version": PROTO_VERSION,
                }))
            bus.log("info", f"relay dialed (gen={self._generation})", "remote")
            async for raw in self._iter_messages(ws):
                try:
                    frame = decode_frame(raw)
                except Exception as exc:  # noqa: BLE001 - a bad frame closes the conn
                    bus.log("warning", f"relay protocol error: {exc}", "remote")
                    raise
                await self._dispatch(frame)
        finally:
            await self._teardown_connection(ws)

    async def _iter_messages(self, ws: Any):
        """Yield each inbound message. ``websockets`` connections are themselves
        async-iterable; a fake channel may expose ``__aiter__`` or ``recv``."""
        if hasattr(ws, "__aiter__"):
            async for raw in ws:
                yield raw
            return
        while True:
            try:
                yield await ws.recv()
            except Exception:  # noqa: BLE001 - closed -> stop iterating
                return

    async def _teardown_connection(self, ws: Any) -> None:
        for task in list(self._req_tasks.values()):
            task.cancel()
        for task in list(self._ws_streams.values()):
            task.cancel()
        for stream in list(self._reqs.values()):
            stream.abort()
        self._reqs.clear()
        self._req_tasks.clear()
        self._ws_streams.clear()
        with contextlib.suppress(Exception):
            await ws.close()
        self._ws = None

    # -- sending ---------------------------------------------------------------

    async def _raw_send(self, data: bytes) -> None:
        """Serialize all WSS sends through one lock (one writer per connection)."""
        ws = self._ws
        if ws is None:
            return
        async with self._send_lock:
            await ws.send(data)

    async def _send_frame(self, frame: Frame) -> None:
        await self._raw_send(encode_frame(
            frame.type, frame.stream_id, frame.header, frame.payload))

    # -- dispatch --------------------------------------------------------------

    async def _dispatch(self, frame: Frame) -> None:
        t = frame.type
        if t == FrameType.REQ_OPEN:
            self._open_request(frame)
        elif t == FrameType.REQ_DATA:
            stream = self._reqs.get(frame.stream_id)
            if stream is not None:
                stream.feed_body(frame.payload, frame.eof)
        elif t == FrameType.REQ_ABORT:
            stream = self._reqs.get(frame.stream_id)
            if stream is not None:
                stream.abort()
        elif t == FrameType.WS_OPEN:
            self._open_ws(frame)
        elif t == FrameType.WS_CLOSE:
            self._close_ws(frame)
        elif t == FrameType.PING:
            await self._raw_send(encode_frame(FrameType.PONG, 0, frame.header))
        elif t in (FrameType.PONG, FrameType.HELLO_ACK, FrameType.WINDOW,
                   FrameType.REVOKE):
            # Acked / flow-control / revoke handling is owned by the relay-pairing
            # lane; the dial-out client tolerates them as no-ops today.
            pass
        # Unknown-but-decodable types are ignored (forward-compatible).

    # -- HTTP request replay ---------------------------------------------------

    def _build_http_scope(self, frame: Frame) -> dict:
        """Construct the ASGI HTTP scope for a tunneled request, marked REMOTE.

        The ``state`` carries the W3 remote flag (NOT LAN-spoofable). A relay-
        forwarded, home-verifiable ``principal_token`` (if present) is passed
        through in ``state`` for the auth lane to verify against ``relay_pubkey`` /
        ``viewer_link_pubkey`` -- this client never mints or trusts it directly."""
        hdr = frame.header
        state: dict[str, Any] = {REMOTE_SCOPE_KEY: True}
        ptoken = hdr.get("principal_token")
        if ptoken:
            state["astrodeck_principal_token"] = ptoken
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": str(hdr.get("method", "GET")).upper(),
            "scheme": "https",
            "path": str(hdr.get("path", "/")),
            "raw_path": str(hdr.get("path", "/")).encode("latin-1", "replace"),
            "query_string": str(hdr.get("query", "")).encode("latin-1", "replace"),
            "root_path": "",
            "headers": _headers_to_scope(hdr.get("headers", [])),
            "client": ("relay", 0),
            "server": ("astrodeck", 0),
            "state": state,
        }

    def _open_request(self, frame: Frame) -> None:
        sid = frame.stream_id
        if sid in self._reqs:
            # Duplicate live stream_id: protocol violation. Abort the prior; the
            # relay must not reuse a live id.
            self._reqs[sid].abort()
        stream = _RequestStream(sid, self._send_frame)
        self._reqs[sid] = stream
        scope = self._build_http_scope(frame)
        if not frame.header.get("has_body", False):
            stream.feed_body(b"", True)  # bodyless request: immediate EOF
        task = asyncio.create_task(self._run_request(sid, scope, stream))
        self._req_tasks[sid] = task

    async def _run_request(self, sid: int, scope: dict, stream: _RequestStream) -> None:
        """Replay one request against the in-process app, isolated so a handler
        error becomes a 500 on the tunnel, never a crash of the run loop."""
        try:
            await self._app(scope, stream.receive, stream.send)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface a 500, keep the conn alive
            bus.log("warning", f"tunneled request failed: {exc}", "remote")
            with contextlib.suppress(Exception):
                await self._send_frame(Frame(
                    type=FrameType.RESP_HEAD, stream_id=sid,
                    header={"status": 500, "headers": []}))
                await self._send_frame(Frame(
                    type=FrameType.RESP_DATA, stream_id=sid,
                    header={"eof": True}, payload=b""))
        finally:
            self._reqs.pop(sid, None)
            self._req_tasks.pop(sid, None)

    # -- tunneled /ws fanout (server->client ONLY) -----------------------------

    def _open_ws(self, frame: Frame) -> None:
        # ``ws_id`` is OPAQUE to the home: the relay allocates it (a STRING like
        # "ws1" so it never collides with the integer stream_id namespace -- see
        # relay/registry.py). We must NOT coerce it to int. The frame's wire
        # ``stream_id`` (relay-allocated, integer) is what we send WS_DATA back on;
        # the opaque ``ws_id`` is echoed in the header so the relay routes the
        # fan-out to exactly this browser.
        ws_id = frame.header.get("ws_id")
        if ws_id is None or ws_id in self._ws_streams:
            return
        wire_stream_id = frame.stream_id
        task = asyncio.create_task(self._run_ws(ws_id, wire_stream_id))
        self._ws_streams[ws_id] = task

    def _close_ws(self, frame: Frame) -> None:
        ws_id = frame.header.get("ws_id")
        if ws_id is None:
            return
        task = self._ws_streams.pop(ws_id, None)
        if task is not None:
            task.cancel()

    async def _run_ws(self, ws_id, wire_stream_id: int) -> None:
        """Mirror the on-LAN /ws: subscribe to the bus and stream each event down
        as WS_DATA (server->client ONLY -- there is NO upstream control channel,
        exactly like the send-only /ws). Per-ws monotonic ``seq`` lets the relay /
        browser detect a drop; the local buffer drops oldest under backpressure.

        ``ws_id`` is the relay's opaque (string) browser id, carried in the WS_DATA
        header; ``wire_stream_id`` is the integer stream the frame actually rides
        (frames are keyed on the wire by stream_id, which MUST be a valid uint64)."""
        q = bus.subscribe()
        seq = 0
        try:
            # Mirror the on-LAN hello frame so a remote viewer renders immediately.
            from ..hub import hub
            seq += 1
            await self._send_frame(Frame(
                type=FrameType.WS_DATA, stream_id=wire_stream_id,
                header={"ws_id": ws_id, "seq": seq},
                payload=_event_payload({"type": "hello", "data": hub.summary(), "ts": 0})))
            while True:
                ev: Event = await q.get()
                seq += 1
                await self._send_frame(Frame(
                    type=FrameType.WS_DATA, stream_id=wire_stream_id,
                    header={"ws_id": ws_id, "seq": seq},
                    payload=_event_payload(ev.to_json())))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - close just this ws stream
            bus.log("warning", f"tunneled ws failed: {exc}", "remote")
            with contextlib.suppress(Exception):
                await self._send_frame(Frame(
                    type=FrameType.WS_CLOSE, stream_id=wire_stream_id,
                    header={"ws_id": ws_id, "code": 1011}))
        finally:
            bus.unsubscribe(q)
            self._ws_streams.pop(ws_id, None)


def _event_payload(obj: dict) -> bytes:
    import json
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


async def run_relay_client(
    app: Callable[..., Awaitable[None]],
    config_provider: Callable[[], RemoteConfig],
) -> RelayClient | None:
    """Lifespan entry point. Returns the live ``RelayClient`` (so the caller can
    ``stop()`` it on shutdown), or ``None`` if remote is disabled/unconfigured.

    OPT-IN: dials ONLY when ``enabled`` and a ``relay_url`` are set, so the default
    config does nothing (LAN-only is byte-for-byte today). ISOLATED: the run loop
    never raises, so a relay outage can never take the app down."""
    cfg = config_provider()
    if not (cfg.enabled and cfg.relay_url):
        return None
    client = RelayClient(app, config_provider)
    asyncio.create_task(client.run())
    return client


__all__ = [
    "RelayClient", "run_relay_client", "scope_is_remote",
    "REMOTE_SCOPE_KEY",
]
