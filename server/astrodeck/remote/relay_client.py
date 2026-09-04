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
uvicorn-borne request has no such key). Inbound ``authorization`` and
``x-auth-token`` headers are stripped at the replay shim. The current
home-terminated-auth deployment deliberately forwards the home's signed session
cookie; therefore the relay is a trusted bearer-token intermediary. A future
blind-relay design should replace this with end-to-end protected credentials.

ISOLATION: this never blocks or crashes the app lifespan. A relay outage -> the
client retries with capped backoff + full jitter and the home runs local-only.
Each redial bumps ``generation`` (fencing). The whole module lazy-imports
``websockets`` so a LAN-only install (the default, ``enabled=False``) never needs
the dependency.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import random
import re
import time
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..config import RemoteConfig
from ..events import Event, bus
from .protocol import (DEFAULT_MAX_PAYLOAD, DEFAULT_MAX_WIRE_SIZE, PROTO_VERSION,
                       Frame, FrameType, ProtocolError, decode_frame,
                       encode_frame)

# The ASGI scope state key that marks a request/ws as relay-tunneled. Read by the
# home via ``scope_is_remote`` -> passed as ``remote=`` to ``resolve_principal``.
# A real uvicorn request never sets this, so it is NOT LAN-spoofable.
REMOTE_SCOPE_KEY = "astrodeck_remote"

# Forgeable BEARER carriers stripped from every replayed request: ``authorization``
# / ``x-auth-token`` are the shared ASTRODECK_TOKEN, which the relay or an on-LAN
# attacker could inject as a guessed admin credential -- never a valid tunnel
# carrier. The ``cookie`` is DELIBERATELY NOT stripped: the session/login cookie is
# HMAC-SIGNED by the home (the relay holds no signing secret, so it cannot forge
# one), and home-terminated auth -- e.g. Google OIDC over the relay -- carries
# identity in that cookie. (A compromised TLS-terminating relay could REPLAY a
# captured cookie: the accepted trusted-transport interim risk; the blind-relay
# E2E future removes plaintext exposure entirely.)
_STRIPPED_INBOUND_HEADERS = frozenset({
    b"authorization", b"x-auth-token",
})

# The shared ASTRODECK_TOKEN also rides as a ``?token=`` QUERY param: the transport
# middleware (api/app.py) and TokenAdminProvider both accept it from the query, and
# because those providers are not named ``none`` the W3 remote hard-deny interlock
# does NOT fire on them. So the header strip alone is not enough -- a tunneled
# ``?token=<ASTRODECK_TOKEN>`` would escalate a captured/guessed shared token to
# full admin, exactly the carrier the header strip is meant to close. Strip it from
# the tunneled query too, so the shared token is never a valid tunnel carrier in
# ANY form (only the home-signed cookie / signed principal_token remain).
_STRIPPED_QUERY_PARAMS = frozenset({"token"})

# Reconnect backoff: capped exponential with FULL jitter. Starts ~0.5s, caps at
# ~15s (the pinned ceiling). Full jitter (random in [0, computed]) avoids a
# thundering-herd reconnect if many homes share a relay restart.
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 15.0
# A session that stayed up this long is evidence the relay is HEALTHY, so the
# drop that ends it must not inherit the outage backoff. Deliberately above the
# ~50s a keepalive-timeout session costs (20s ping_interval + 20s ping_timeout +
# 10s close_timeout), so a relay that wedges just after accepting still earns the
# ceiling instead of being redialled at full speed forever.
_HEALTHY_SESSION_S = 120.0
# Clamp the exponent so ``2 ** attempt`` can never overflow float on a long
# outage (attempt keeps climbing until a clean session). 40 already puts the
# raw term at ~5e11 s, so the cap fully dominates; the clamp is purely an
# overflow guard, not a behavior change below the cap.
_BACKOFF_MAX_EXP = 40

# Per-viewer /ws fanout buffer (drop-oldest). A slow remote viewer must never
# stall the single shared bus subscription that feeds every viewer.
_WS_BUFFER_MAX = 200

# Hard per-tunnel state bounds. The public relay is an authenticated peer, not a
# memory-allocation authority: a stolen device token or compromised relay must
# not create unbounded ASGI tasks/queues on the home controller.
_REQUEST_BODY_CHUNKS_MAX = 8
_REQUEST_BODY_BYTES_MAX = 8 * 1024 * 1024
_OPEN_REQUESTS_MAX = 32
_OPEN_WS_STREAMS_MAX = 16
_HELLO_ACK_TIMEOUT_S = 10.0
_TASK_TEARDOWN_TIMEOUT_S = 2.0


def _validate_relay_config(cfg: RemoteConfig) -> None:
    """Fail closed before disclosing the device token or accepting work.

    Public relays must use TLS. Plain ``ws://`` is permitted only for an actual
    loopback host so the on-wire integration test and local development remain
    possible without teaching operators to expose credentials in plaintext.
    """
    try:
        parsed = urlsplit(cfg.relay_url)
        host = parsed.hostname or ""
        loopback = host.lower() == "localhost"
        if host and not loopback:
            with contextlib.suppress(ValueError):
                loopback = ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid remote relay URL") from exc
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("invalid remote relay URL")
    if parsed.scheme != "wss" and not (parsed.scheme == "ws" and loopback):
        raise ValueError("remote relay URL must use wss:// (ws:// is loopback-only)")
    token = (cfg.device_token or "").strip()
    if (not 32 <= len(token) <= 256
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
        raise ValueError(
            "remote relay device_token must be 32-256 printable ASCII characters")
    if not isinstance(cfg.home_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", cfg.home_id):
        raise ValueError("remote relay home_id must be 1-64 URL-safe characters")


class _WsSendGuard:
    """Per-ws throttle + counter for 'dropped an unsendable event' warnings.

    A single /ws event whose JSON exceeds the per-frame ceiling
    (``DEFAULT_MAX_PAYLOAD``) -- a status/preview/config payload that grew past
    64 KiB -- makes ``encode_frame`` raise ``ProtocolError``. That must DROP just
    that event and keep the shared telemetry stream alive, but a repeating
    oversize event (e.g. a large preview frame every exposure) would spam the bus
    log. Warn at most once per ``_WARN_INTERVAL_S`` and fold the running count
    into that one line."""

    _WARN_INTERVAL_S = 30.0

    def __init__(self) -> None:
        self.dropped = 0
        self._last_warn = float("-inf")

    def note_drop(self, exc: Exception) -> None:
        import time as _t
        self.dropped += 1
        now = _t.monotonic()
        if now - self._last_warn >= self._WARN_INTERVAL_S:
            self._last_warn = now
            bus.log("warning",
                    f"tunneled ws: dropped {self.dropped} oversize/unsendable "
                    f"event(s); telemetry stream kept alive "
                    f"(last: {type(exc).__name__}: {exc})", "remote")


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
    """Capped-exponential FULL-jitter backoff for redial ``attempt`` (0-based).

    The exponent is clamped BEFORE the shift: ``attempt`` grows by one per failed
    dial and only resets on a clean session, so during a long relay outage (~2h
    at the 15s cap) it reaches four digits. ``2 ** attempt`` then materializes a
    huge int that ``min`` converts to float -- at attempt>=1024 that conversion
    raises OverflowError, which would escape the (documented never-raising) run()
    supervisor and permanently kill reconnection. Clamping to ``_BACKOFF_MAX_EXP``
    (well past the point the cap dominates) keeps the shift bounded and cheap."""
    exp = min(attempt, _BACKOFF_MAX_EXP)
    ceiling = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** exp))
    return random.uniform(0.0, ceiling)


def _strip_query_token(query: str) -> str:
    """Drop shared-token carriers (``?token=``) from a tunneled query string,
    preserving every other param (and their order/repetition). This closes the
    query form of the shared ASTRODECK_TOKEN over the tunnel just like the header
    strip closes the header forms -- remote requests must never carry local-trust
    credentials. A blank/malformed query yields a blank query (never raises)."""
    if not query:
        return ""
    # keep_blank_values so value-less params (``?foo``) survive; strict_parsing off
    # so a malformed query degrades to best-effort rather than raising.
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if k not in _STRIPPED_QUERY_PARAMS]
    return urlencode(kept)


def _headers_to_scope(header_list: Iterable[Any]) -> list[tuple[bytes, bytes]]:
    """Turn a JSON ``[[name, value], ...]`` header list into ASGI raw headers
    (lowercased bytes), DROPPING any inbound auth carrier (auth headers are not
    valid tunnel carriers; the principal is injected separately)."""
    if not isinstance(header_list, list):
        raise ProtocolError("request headers must be a list")
    raw: list[tuple[bytes, bytes]] = []
    separators = frozenset('()<>@,;:\\"/[]?={} \t')
    for item in header_list:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ProtocolError("request header must be a name/value pair")
        name, value = item
        if not isinstance(name, str) or not isinstance(value, str):
            raise ProtocolError("request header name/value must be text")
        if (not name or any(ord(ch) <= 32 or ord(ch) >= 127
                            or ch in separators for ch in name)):
            raise ProtocolError("invalid request header name")
        if any((ord(ch) < 32 and ch != "\t")
               or ord(ch) == 127 or ord(ch) > 255 for ch in value):
            raise ProtocolError("invalid request header value")
        nb = name.lower().encode("latin-1")
        if nb in _STRIPPED_INBOUND_HEADERS:
            continue
        raw.append((nb, value.encode("latin-1")))
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
        self._body: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue(
            maxsize=_REQUEST_BODY_CHUNKS_MAX)
        self._aborted = False
        self._received_bytes = 0
        self.response_started = False
        self.response_complete = False

    def feed_body(self, chunk: bytes, eof: bool) -> bool:
        if self._aborted:
            return False
        self._received_bytes += len(chunk)
        if self._received_bytes > _REQUEST_BODY_BYTES_MAX:
            return False
        try:
            self._body.put_nowait((chunk, eof))
            return True
        except asyncio.QueueFull:
            return False

    def abort(self) -> None:
        self._aborted = True
        # Unblock a receive() awaiting more body so the app task can unwind.
        with contextlib.suppress(asyncio.QueueEmpty):
            while True:
                self._body.get_nowait()
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

    async def send_frame(self, frame: Frame) -> None:
        """Send on the exact tunnel generation that created this stream."""
        if self._aborted:
            raise ConnectionError("request stream is aborted")
        await self._send_frame(frame)

    async def send_terminal_frame(self, frame: Frame) -> None:
        """Send relay-owned terminal metadata after fencing the ASGI task.

        Only the overflow handler uses this bypass. Application ``send`` calls
        remain blocked once ``abort()`` has fenced the stream.
        """
        await self._send_frame(frame)

    async def send(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == "http.response.start":
            await self.send_frame(Frame(
                type=FrameType.RESP_HEAD,
                stream_id=self.stream_id,
                header={
                    "status": int(message.get("status", 200)),
                    "headers": _scope_headers_to_list(message.get("headers", [])),
                },
            ))
            self.response_started = True
        elif mtype == "http.response.body":
            body = message.get("body", b"") or b""
            more = bool(message.get("more_body", False))
            # Chunk to the payload ceiling so a large FileResponse/FITS streams in
            # bounded frames (and never lands in one oversize frame). AWAIT each
            # send -> the app is naturally backpressured by the WSS write.
            mv = memoryview(body)
            if not mv:
                await self.send_frame(Frame(
                    type=FrameType.RESP_DATA, stream_id=self.stream_id,
                    header={"eof": not more}, payload=b""))
                if not more:
                    self.response_complete = True
                return
            total = len(mv)
            off = 0
            while off < total:
                end = min(off + DEFAULT_MAX_PAYLOAD, total)
                last = end >= total
                await self.send_frame(Frame(
                    type=FrameType.RESP_DATA, stream_id=self.stream_id,
                    header={"eof": last and not more},
                    payload=bytes(mv[off:end])))
                off = end
            if not more:
                self.response_complete = True


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
        self._ws_streams: dict[Any, asyncio.Task] = {}
        self._reapers: set[asyncio.Task] = set()

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
            started = time.monotonic()
            try:
                cfg = self._config()
                if not (cfg.enabled and cfg.relay_url):
                    # Disabled / unconfigured: idle until stop (the lifespan
                    # normally launches us only when enabled, but re-check
                    # defensively for a live config edit toggling us off).
                    return
                self._generation += 1
                await self._serve_once(cfg)
                attempt = 0  # a clean session resets the backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - degrade to local-only, never crash
                held = time.monotonic() - started
                # A DROP IS NOT A CLEAN RETURN, and for the whole life of this
                # loop that was the only thing that reset `attempt`. So the first
                # failure of a process poisoned every redial after it: from
                # attempt>=5 the ceiling pins at _BACKOFF_CAP_S and each dial
                # drew uniform(0, 15s). Measured on the rig 2026-08-18 -- a
                # session healthy for 3h19m dropped and the rig stayed
                # local-only for 12s before trying again.
                if held >= _HEALTHY_SESSION_S:
                    attempt = 0
                # `held` is the DIAL duration when the failure happened inside
                # _connect (the first statement of _serve_once) and the SESSION
                # lifetime otherwise -- which of the two failure classes this was
                # is readable straight off the log line.
                bus.log("warning",
                        f"relay connection lost gen={self._generation} after "
                        f"{held:.1f}s ({type(exc).__name__}: {exc}); "
                        f"retrying local-only", "remote")
            if self._stop.is_set():
                break
            # Belt-and-braces: the backoff math is now overflow-safe, but this
            # call sits OUTSIDE the _serve_once try/except, so any future slip
            # here would escape the never-raising supervisor and kill reconnect
            # forever. Fall back to the cap on any arithmetic error rather than die.
            try:
                delay = _backoff_delay(attempt)
            except Exception:  # noqa: BLE001 - never let backoff math kill the loop
                delay = _BACKOFF_CAP_S
            attempt += 1
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    # -- connection ------------------------------------------------------------

    async def _default_connect(self, url: str) -> Any:
        """Open the outbound WSS. Lazy-imports ``websockets`` so the dependency is
        only required when the relay is actually used."""
        import websockets  # lazy: optional dependency, only on the remote path
        return await websockets.connect(
            url,
            max_size=DEFAULT_MAX_WIRE_SIZE,
            max_queue=16,
            # Compression can amplify a tiny malicious frame into the message
            # limit and consumes CPU/memory before protocol validation. Tunnel
            # payloads are already JSON/images/FITS and gain little from it.
            compression=None,
        )

    async def _serve_once(self, cfg: RemoteConfig) -> None:
        """One full connection lifetime: connect, HELLO, dispatch frames until the
        socket closes. Cleans up all per-connection state on exit."""
        _validate_relay_config(cfg)
        ws = await self._connect(cfg.relay_url)
        self._ws = ws
        # A task stuck on an older transport must never hold the next
        # generation's writer lock.
        self._send_lock = asyncio.Lock()
        self._reqs = {}
        self._req_tasks = {}
        self._ws_streams = {}
        config_watch: asyncio.Task | None = None
        try:
            await self._raw_send_on(ws, encode_frame(
                FrameType.HELLO, 0, {
                    "device_token": cfg.device_token,
                    "home_id": cfg.home_id,
                    "generation": self._generation,
                    "proto_version": PROTO_VERSION,
                }))
            messages = self._iter_messages(ws).__aiter__()
            try:
                raw_ack = await asyncio.wait_for(
                    messages.__anext__(), timeout=_HELLO_ACK_TIMEOUT_S)
            except (asyncio.TimeoutError, StopAsyncIteration) as exc:
                raise ProtocolError("relay did not complete HELLO handshake") from exc
            ack = decode_frame(raw_ack)
            if ack.type != FrameType.HELLO_ACK or ack.header.get("ok") is not True:
                # Do not reflect an untrusted relay-supplied reason into the
                # local event log; it can contain control characters or secret
                # material. The operator still gets the failure class.
                raise ProtocolError("relay HELLO rejected")
            bus.log("info", f"relay authenticated (gen={self._generation})", "remote")
            config_watch = asyncio.create_task(
                self._watch_connection_config(ws, cfg))
            async for raw in messages:
                try:
                    frame = decode_frame(raw)
                except Exception as exc:  # noqa: BLE001 - a bad frame closes the conn
                    bus.log("warning", f"relay protocol error: {exc}", "remote")
                    raise
                await self._dispatch(frame)
        finally:
            if config_watch is not None:
                config_watch.cancel()
                with contextlib.suppress(BaseException):
                    await config_watch
            await self._teardown_connection(ws)

    async def _watch_connection_config(self, ws: Any, initial: RemoteConfig) -> None:
        """Close a live tunnel promptly when its local kill switch/config changes."""
        fingerprint = (
            initial.enabled, initial.relay_url, initial.device_token,
            initial.home_id,
        )
        while True:
            await asyncio.sleep(1.0)
            try:
                current = self._config()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - config failure is fail-closed
                bus.log(
                    "warning",
                    "relay configuration became unreadable "
                    f"({type(exc).__name__}); closing the remote tunnel",
                    "remote",
                )
                with contextlib.suppress(Exception):
                    await ws.close()
                return
            current_fingerprint = (
                current.enabled, current.relay_url, current.device_token,
                current.home_id,
            )
            if self._stop.is_set() or current_fingerprint != fingerprint:
                with contextlib.suppress(Exception):
                    await ws.close()
                return

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
        # Make every captured-generation send fail before cancellation starts.
        # A handler that is slow to honour cancellation can then never write a
        # late response onto a subsequent relay connection.
        if self._ws is ws:
            self._ws = None
        for stream in list(self._reqs.values()):
            stream.abort()
        tasks = set(self._req_tasks.values()) | set(self._ws_streams.values())
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(
                tasks, timeout=_TASK_TEARDOWN_TIMEOUT_S)
            for task in done:
                with contextlib.suppress(BaseException):
                    task.result()
            if pending:
                bus.log(
                    "warning",
                    f"relay teardown: {len(pending)} task(s) ignored cancellation; "
                    "their stale-socket writes remain fenced",
                    "remote",
                )
        self._reqs.clear()
        self._req_tasks.clear()
        self._ws_streams.clear()
        with contextlib.suppress(Exception):
            await ws.close()

    # -- sending ---------------------------------------------------------------

    async def _raw_send(self, data: bytes) -> None:
        """Serialize all WSS sends through one lock (one writer per connection)."""
        ws = self._ws
        if ws is None:
            raise ConnectionError("relay connection is not active")
        await self._raw_send_on(ws, data)

    async def _raw_send_on(self, ws: Any, data: bytes) -> None:
        """Write only if ``ws`` is still the current tunnel generation."""
        if ws is None or self._ws is not ws:
            raise ConnectionError("stale relay connection")
        lock = self._send_lock
        async with lock:
            if self._ws is not ws:
                raise ConnectionError("stale relay connection")
            await ws.send(data)

    async def _send_frame(self, frame: Frame) -> None:
        await self._raw_send(encode_frame(
            frame.type, frame.stream_id, frame.header, frame.payload))

    async def _send_frame_on(self, ws: Any, frame: Frame) -> None:
        await self._raw_send_on(ws, encode_frame(
            frame.type, frame.stream_id, frame.header, frame.payload))

    async def _send_ws_event(self, wire_stream_id: int, ws_id: Any, seq: int,
                             obj: dict, guard: _WsSendGuard, ws: Any = None) -> None:
        """Encode + send ONE /ws event as a WS_DATA frame, ISOLATING an encode
        failure so it can never tear down the shared telemetry stream.

        The event JSON is encoded FIRST; if that raises -- the payload grew past
        ``DEFAULT_MAX_PAYLOAD`` (``encode_frame`` -> ``ProtocolError``) or is not
        JSON-serializable -- we DROP just this one event (throttled warning) and
        return, leaving the stream live so the next (small) status frame still
        arrives. This is the fix for 'TELEMETRY CATCHING UP': previously the
        ``ProtocolError`` propagated to ``_run_ws``'s ``except`` and closed the
        WHOLE ws stream, so ONE oversize frame starved the UI of ALL subsequent
        status frames (and the relay's redial just hit the same oversize frame
        again -> a repeating exception that never delivered telemetry).

        A raw-SEND failure (a dead socket) is deliberately NOT caught here: it
        propagates so the stream closes -- a broken transport is not a per-event
        problem and must end the stream, exactly as before."""
        try:
            data = encode_frame(FrameType.WS_DATA, wire_stream_id,
                                {"ws_id": ws_id, "seq": seq}, _event_payload(obj))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - oversize/unserializable: drop 1 event
            guard.note_drop(exc)
            return
        if ws is None:
            await self._raw_send(data)
        else:
            await self._raw_send_on(ws, data)

    # -- dispatch --------------------------------------------------------------

    async def _dispatch(self, frame: Frame) -> None:
        t = frame.type
        if t == FrameType.REQ_OPEN:
            await self._open_request(frame)
        elif t == FrameType.REQ_DATA:
            stream = self._reqs.get(frame.stream_id)
            if stream is not None and not stream.feed_body(
                    frame.payload, frame.eof):
                await self._reject_request_body_overflow(frame.stream_id, stream)
        elif t == FrameType.REQ_ABORT:
            await self._abort_request(frame.stream_id)
        elif t == FrameType.WS_OPEN:
            await self._open_ws(frame)
        elif t == FrameType.WS_CLOSE:
            await self._close_ws(frame)
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
            "query_string": _strip_query_token(
                str(hdr.get("query", ""))).encode("latin-1", "replace"),
            "root_path": "",
            "headers": _headers_to_scope(hdr.get("headers", [])),
            "client": ("relay", 0),
            "server": ("astrodeck", 0),
            "state": state,
        }

    async def _open_request(self, frame: Frame) -> None:
        sid = frame.stream_id
        ws = self._ws
        if ws is None:
            raise ConnectionError("relay connection is not active")
        if sid in self._reqs:
            # Reuse would let one request overwrite another request's routing
            # state and make the older task remove the newer one on completion.
            raise ProtocolError(f"duplicate live request stream_id {sid}")
        if len(self._reqs) >= _OPEN_REQUESTS_MAX:
            await self._send_http_failure(
                sid, 503, b"too many requests",
                send_frame=lambda out: self._send_frame_on(ws, out))
            return
        stream = _RequestStream(
            sid, lambda out: self._send_frame_on(ws, out))
        self._reqs[sid] = stream
        scope = self._build_http_scope(frame)
        if not frame.header.get("has_body", False):
            stream.feed_body(b"", True)  # bodyless request: immediate EOF
        task = asyncio.create_task(self._run_request(sid, scope, stream))
        self._req_tasks[sid] = task

    async def _send_http_failure(
            self, sid: int, status: int, body: bytes = b"", *,
            send_frame: Callable[[Frame], Awaitable[None]] | None = None) -> None:
        sender = send_frame or self._send_frame
        await sender(Frame(
            type=FrameType.RESP_HEAD, stream_id=sid,
            header={"status": status,
                    "headers": [["content-type", "text/plain; charset=utf-8"]]}))
        await sender(Frame(
            type=FrameType.RESP_DATA, stream_id=sid,
            header={"eof": True}, payload=body[:DEFAULT_MAX_PAYLOAD]))

    async def _abort_request(self, sid: int) -> None:
        """Abort and fence one isolated in-process request task.

        Reaping is detached because this method runs on the single tunnel
        reader, which must not wait for a hostile handler that suppresses
        cancellation.
        """
        stream = self._reqs.get(sid)
        if stream is not None:
            stream.abort()
        task = self._req_tasks.get(sid)
        if task is not None and task is not asyncio.current_task():
            self._cancel_and_reap(task, f"request {sid}")
        if self._reqs.get(sid) is stream:
            self._reqs.pop(sid, None)
        if self._req_tasks.get(sid) is task:
            self._req_tasks.pop(sid, None)

    async def _reject_request_body_overflow(
            self, sid: int, stream: _RequestStream) -> None:
        """Cancel only the request whose bounded upload queue filled.

        Waiting on ``Queue.put`` here would block the single tunnel reader and
        let one slow endpoint starve every other stream. Cancelling the isolated
        ASGI task keeps the rest of the home connection responsive.
        """
        stream.abort()
        task = self._req_tasks.get(sid)
        if task is not None:
            self._cancel_and_reap(task, f"overflowed request {sid}")
        if not stream.response_started:
            await self._send_http_failure(
                sid, 413, b"request body not consumed",
                send_frame=stream.send_terminal_frame)
        elif not stream.response_complete:
            await stream.send_terminal_frame(Frame(
                type=FrameType.RESP_DATA, stream_id=sid,
                header={"eof": True}, payload=b""))
        if self._reqs.get(sid) is stream:
            self._reqs.pop(sid, None)
        if self._req_tasks.get(sid) is task:
            self._req_tasks.pop(sid, None)

    def _cancel_and_reap(self, task: asyncio.Task, label: str) -> None:
        """Cancel without blocking the tunnel reader; bound exception reaping."""
        task.cancel()
        reaper = asyncio.create_task(self._reap_cancelled(task, label))
        self._reapers.add(reaper)
        reaper.add_done_callback(self._reapers.discard)

    async def _reap_cancelled(self, task: asyncio.Task, label: str) -> None:
        done, pending = await asyncio.wait(
            {task}, timeout=_TASK_TEARDOWN_TIMEOUT_S)
        for finished in done:
            with contextlib.suppress(BaseException):
                finished.result()
        if pending:
            bus.log(
                "warning",
                f"tunneled {label} ignored cancellation; its writes are fenced",
                "remote",
            )

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
                await stream.send_frame(Frame(
                    type=FrameType.RESP_HEAD, stream_id=sid,
                    header={"status": 500, "headers": []}))
                await stream.send_frame(Frame(
                    type=FrameType.RESP_DATA, stream_id=sid,
                    header={"eof": True}, payload=b""))
        finally:
            if self._reqs.get(sid) is stream:
                self._reqs.pop(sid, None)
            if self._req_tasks.get(sid) is asyncio.current_task():
                self._req_tasks.pop(sid, None)

    # -- tunneled /ws fanout (server->client ONLY) -----------------------------

    async def _open_ws(self, frame: Frame) -> None:
        # ``ws_id`` is OPAQUE to the home: the relay allocates it (a STRING like
        # "ws1" so it never collides with the integer stream_id namespace -- see
        # relay/registry.py). We must NOT coerce it to int. The frame's wire
        # ``stream_id`` (relay-allocated, integer) is what we send WS_DATA back on;
        # the opaque ``ws_id`` is echoed in the header so the relay routes the
        # fan-out to exactly this browser.
        ws_id = frame.header.get("ws_id")
        ws = self._ws
        if ws is None:
            raise ConnectionError("relay connection is not active")
        if (not isinstance(ws_id, str) or not 1 <= len(ws_id) <= 64
                or any(ch not in "abcdefghijklmnopqrstuvwxyz"
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in ws_id)):
            raise ProtocolError("invalid ws_id")
        if ws_id in self._ws_streams:
            raise ProtocolError(f"duplicate live ws_id {ws_id!r}")
        if len(self._ws_streams) >= _OPEN_WS_STREAMS_MAX:
            await self._send_frame_on(ws, Frame(
                type=FrameType.WS_CLOSE, stream_id=frame.stream_id,
                header={"ws_id": ws_id, "code": 1013}))
            return
        wire_stream_id = frame.stream_id
        # Thread the WHOLE WS_OPEN frame into the ws task: its header carries the
        # browser's cookie/headers + an optional home-verifiable principal_token
        # that _run_ws needs to AUTHORIZE this viewer before it joins the bus.
        task = asyncio.create_task(
            self._run_ws(ws_id, wire_stream_id, frame, ws))
        self._ws_streams[ws_id] = task

    async def _close_ws(self, frame: Frame) -> None:
        ws_id = frame.header.get("ws_id")
        if ws_id is None:
            return
        task = self._ws_streams.pop(ws_id, None)
        if task is not None:
            self._cancel_and_reap(task, f"websocket {ws_id!r}")

    def _ws_auth_request(self, frame: Frame):
        """Build a Starlette ``Request`` for per-viewer authorization from a
        WS_OPEN frame, reusing the SAME remote-flagged scope machinery as the
        replayed HTTP requests (``_build_http_scope``): the W3 remote flag in
        ``state`` (NOT LAN-spoofable), the cookie header passed through, the
        forgeable bearer carriers stripped, the ``?token=`` query stripped, and
        the home-verifiable ``principal_token`` (if any) parked in scope state.

        A WS_OPEN header has no ``method`` (``_build_http_scope`` defaults GET) but
        carries path/query/headers/principal_token, so the http scope is well-
        formed for ``resolve_principal`` (which only reads cookies/headers/query,
        never the request body -- so no ``receive`` is needed)."""
        from starlette.requests import Request
        return Request(self._build_http_scope(frame))

    async def _run_ws(self, ws_id, wire_stream_id: int, frame: Frame,
                      ws: Any = None) -> None:
        """Mirror the on-LAN /ws: AUTHORIZE the viewer, then subscribe to the bus
        and stream each REDACTED event down as WS_DATA (server->client ONLY -- there
        is NO upstream control channel, exactly like the send-only /ws). Per-ws
        monotonic ``seq`` lets the relay / browser detect a drop; the local buffer
        drops oldest under backpressure.

        Unlike a LAN client, a remote viewer is authorized PER SOCKET here (the LAN
        handler's accept-gate is not reached over the tunnel): we resolve the
        principal from the WS_OPEN frame with ``remote=True`` (so the open ``none``
        provider hard-denies), refuse (WS_CLOSE 4401) without EVER subscribing if it
        lacks ``view.status``, redact every frame for a principal lacking
        ``view.site_precise``, and RE-resolve every ``WS_AUTH_RECHECK_S`` so a
        revoked/expired/downgraded viewer is dropped (4401) or tightened mid-stream.

        ``ws_id`` is the relay's opaque (string) browser id, carried in the WS_DATA
        header; ``wire_stream_id`` is the integer stream the frame actually rides
        (frames are keyed on the wire by stream_id, which MUST be a valid uint64)."""
        from ..api import redact
        from ..auth import resolve_principal
        from ..auth.capabilities import CAP_VIEW_STATUS

        if ws is None:
            ws = self._ws
        if ws is None:
            return
        q = None
        try:
            req = self._ws_auth_request(frame)
            # Accept-time gate: an unauthorized remote viewer must NEVER join
            # the bus. ``remote=True`` makes the open ``none`` provider deny.
            principal = await resolve_principal(req, remote=True)
            if principal is None or not principal.has(CAP_VIEW_STATUS):
                with contextlib.suppress(Exception):
                    await self._send_frame_on(ws, Frame(
                        type=FrameType.WS_CLOSE, stream_id=wire_stream_id,
                        header={"ws_id": ws_id, "code": 4401}))
                return

            q = bus.subscribe()
            seq = 0
            # Re-authenticate even on a quiet socket so revocation and session
            # expiry take effect without waiting for telemetry.
            import time as _t
            next_check = _t.monotonic() + redact.WS_AUTH_RECHECK_S
            guard = _WsSendGuard()
            # Mirror the on-LAN hello frame (REDACTED) so a remote viewer renders
            # immediately without leaking precise site coords it may not hold.
            from ..hub import hub
            seq += 1
            await self._send_ws_event(wire_stream_id, ws_id, seq, {
                "type": "hello",
                "data": redact._redact_site_for(hub.summary(), principal),
                "ts": 0}, guard, ws)
            while True:
                # Wake for either the next event or the recheck deadline, so a quiet
                # socket is still re-validated on schedule (not only on traffic).
                timeout = max(0.0, next_check - _t.monotonic())
                try:
                    ev: Event | None = await asyncio.wait_for(q.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    ev = None
                if _t.monotonic() >= next_check:
                    principal = await resolve_principal(req, remote=True)
                    if principal is None or not principal.has(CAP_VIEW_STATUS):
                        await self._send_frame_on(ws, Frame(
                            type=FrameType.WS_CLOSE, stream_id=wire_stream_id,
                            header={"ws_id": ws_id, "code": 4401}))
                        return
                    next_check = _t.monotonic() + redact.WS_AUTH_RECHECK_S
                if ev is not None:
                    out = redact._redact_ws_event(ev.to_json(), principal)
                    if out is not None:  # None = dropped event (weather spec §8)
                        seq += 1
                        await self._send_ws_event(
                            wire_stream_id, ws_id, seq, out, guard, ws)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - close just this ws stream
            bus.log("warning", f"tunneled ws failed: {exc}", "remote")
            with contextlib.suppress(Exception):
                await self._send_frame_on(ws, Frame(
                    type=FrameType.WS_CLOSE, stream_id=wire_stream_id,
                    header={"ws_id": ws_id, "code": 1011}))
        finally:
            if q is not None:
                bus.unsubscribe(q)
            if self._ws_streams.get(ws_id) is asyncio.current_task():
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
