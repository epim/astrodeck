"""The multiplexer: browser HTTP/WS <-> tunnel frames (W3.3.5).

``TunnelMultiplexer`` owns ONE ``HomeRegistration`` and turns:

  * a browser HTTP request -> ``REQ_OPEN`` (+ ``REQ_DATA`` chunks) down the
    tunnel, then reassembles the ``RESP_HEAD``/``RESP_DATA`` frames back into a
    streamed browser response;
  * a browser ``/ws`` -> ``WS_OPEN`` down the tunnel, then fans the home's
    ``WS_DATA`` frames out to EXACTLY that browser (fan-out isolation, §T7(4)),
    through a per-browser bounded EGRESS buffer.

The per-browser egress buffer is the RELAY-side buffer (distinct from the
home-side per-``ws_id`` buffer, W3.3.3): if it had none, ONE slow browser would
back-pressure the relay's READ of the scope WSS and stall every sibling viewer of
that home (a multi-tenant DoS). So a slow browser's buffer fills, applies the
drop/coalesce policy (drop-oldest STATUS, keep-latest preview/sequence), and the
browser is DISCONNECTED on sustained overflow -- while siblings keep receiving
the full stream (the §T7 fan-out-isolation case). The per-viewer ``seq`` survives
the drop so the browser sees a gap and re-snapshots (detectable, not silent).

This layer is transport-free: a "browser response" is delivered to a callback,
and a "browser WS" is an object with ``async send(text)`` + ``async close()``.
The Starlette ASGI shell in ``server.py`` adapts real browser sockets to these;
the unit tests drive them with in-memory fakes (NO WSS on the wire).
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from . import protocol
from .protocol import Frame, FrameType
from .registry import HomeRegistration

# Per-browser egress buffer bound (events queued toward one browser /ws).
DEFAULT_WS_EGRESS_MAX = 200

# Event ``type`` values we COALESCE on overflow (keep the latest, don't drop the
# newest) vs DROP-OLDEST. Mirrors the home-side per-ws_id policy + the EventBus
# drop-oldest. Status is the high-rate drop-tolerant class.
_COALESCE_TYPES = frozenset({"preview", "sequence"})


class ProxyError(Exception):
    """A tunnelled exchange failed (orphan chunk, missing eof, home gone)."""


# --------------------------------------------------------------- HTTP exchange

@dataclass
class _HttpExchange:
    """In-flight browser HTTP request awaiting its tunnelled response.

    The response is delivered incrementally: ``on_head`` once, then ``on_data``
    per chunk with the final ``eof=True``. ``head_seen`` guards against a
    RESP_DATA arriving before its RESP_HEAD (a protocol error)."""

    stream_id: int
    on_head: Callable[[int, list], Awaitable[None]]
    on_data: Callable[[bytes, bool], Awaitable[None]]
    head_seen: bool = False
    done: "asyncio.Event" = field(default_factory=asyncio.Event)


# ----------------------------------------------------------------- WS browser

class BrowserWS:
    """Adapter contract for one browser ``/ws`` socket. Subclassed by the
    Starlette shell; faked in tests. ``send_text`` pushes one ``/ws`` JSON event
    string to the browser; ``close`` tears the browser socket down."""

    async def send_text(self, text: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def close(self, code: int = 1000) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass
class _WsViewer:
    """Relay-side state for one tunnelled browser ``/ws``: the browser socket,
    its bounded egress buffer, and the per-viewer ``seq`` continuity tracker."""

    ws_id: str
    stream_id: int
    browser: BrowserWS
    egress_max: int = DEFAULT_WS_EGRESS_MAX
    buffer: "asyncio.Queue" = field(default_factory=asyncio.Queue)
    last_seq: int = -1
    overflowed: bool = False
    pump_task: Optional["asyncio.Task"] = None


class TunnelMultiplexer:
    """Maps browser HTTP/WS connections onto ONE home tunnel and back.

    Owns the registration's routing maps. One multiplexer per connected home;
    the relay server holds one per ``home_id`` (the affinity model)."""

    def __init__(self, reg: HomeRegistration, *,
                 ws_egress_max: int = DEFAULT_WS_EGRESS_MAX):
        self.reg = reg
        self.ws_egress_max = ws_egress_max
        self._exchanges: dict[int, _HttpExchange] = {}
        self._viewers: dict[str, _WsViewer] = {}
        #: Stream ids we told the home to abandon. A home that was ALREADY
        #: answering will land a RESP_HEAD/RESP_DATA here a moment later, and
        #: that is expected traffic, not a protocol violation — see
        #: `abort_request`. Bounded: one entry per aborted request on a tunnel
        #: that stays up all night.
        self._aborted: "OrderedDict[int, None]" = OrderedDict()

    @property
    def tunnel(self):
        return self.reg.tunnel

    # ------------------------------------------------- browser HTTP -> tunnel

    async def open_request(self, method: str, path: str, query: str,
                           headers: list, *, has_body: bool,
                           on_head: Callable[[int, list], Awaitable[None]],
                           on_data: Callable[[bytes, bool], Awaitable[None]],
                           cls: str = protocol.CLASS_CONTROL) -> int:
        """Open a browser HTTP exchange: allocate a ``stream_id``, register the
        response callbacks, and send ``REQ_OPEN`` down the tunnel. Returns the
        ``stream_id`` so the caller can stream request-body chunks + close it."""
        stream_id = self.reg.next_stream_id()
        ex = _HttpExchange(stream_id=stream_id, on_head=on_head, on_data=on_data)
        self._exchanges[stream_id] = ex
        self.reg.req_routes[stream_id] = ex
        await self.tunnel.send_frame(
            protocol.req_open(stream_id, method, path, query, headers,
                              has_body=has_body, cls=cls)
        )
        return stream_id

    async def send_request_body(self, stream_id: int, chunk: bytes, *,
                                eof: bool) -> None:
        """Stream a request-body chunk up the tunnel (uploads). The chunk is
        sliced to ``MAX_PAYLOAD`` by the caller; here we forward as-is."""
        await self.tunnel.send_frame(protocol.req_data(stream_id, chunk, eof=eof))

    #: How many aborted stream ids to remember. Late replies arrive within one
    #: round trip; this is orders of magnitude more slack than that.
    ABORTED_MEMORY = 1024

    async def abort_request(self, stream_id: int, reason: str = "") -> None:
        """The browser hung up before EOF -> tell the home to abandon it.

        REMEMBER THE ID. Dropping the exchange and forgetting it made the home's
        in-flight reply an ORPHAN, and `_on_resp_head` raises ProxyError on an
        orphan — which `_scope_endpoint` catches with a bare `except Exception`
        and turns into `shutdown(1012)`, tearing down the whole home tunnel and
        every browser on it.

        That was unreachable until the upstream timeout shipped. Now any request
        slower than `upstream_timeout_s` aborts, and a home that answers a beat
        later kills remote access for everyone. `POST /api/connect/rig` awaits a
        full rig bring-up inside its handler and routinely exceeds 30s.
        """
        self._drop_exchange(stream_id)
        self._aborted[stream_id] = None
        while len(self._aborted) > self.ABORTED_MEMORY:
            self._aborted.popitem(last=False)
        await self.tunnel.send_frame(protocol.req_abort(stream_id, reason))

    def _is_late_reply(self, stream_id: int) -> bool:
        """True when this stream was aborted, so a reply for it is expected
        late traffic to be discarded rather than a protocol error."""
        return stream_id in self._aborted

    # -------------------------------------------------- browser WS -> tunnel

    async def open_ws(self, browser: BrowserWS, path: str, query: str,
                      headers: list) -> str:
        """Open a tunnelled browser ``/ws``: allocate a ``ws_id`` + ``stream_id``,
        start the per-browser egress pump, and send ``WS_OPEN`` down the tunnel.
        Returns the ``ws_id``."""
        ws_id = self.reg.next_ws_id()
        stream_id = self.reg.next_stream_id()
        viewer = _WsViewer(ws_id=ws_id, stream_id=stream_id, browser=browser,
                           egress_max=self.ws_egress_max,
                           buffer=asyncio.Queue(maxsize=self.ws_egress_max))
        self._viewers[ws_id] = viewer
        self.reg.ws_routes[ws_id] = viewer
        viewer.pump_task = asyncio.ensure_future(self._pump_viewer(viewer))
        await self.tunnel.send_frame(
            protocol.ws_open(stream_id, ws_id, path, query, headers,
                             cls=protocol.CLASS_EVENT)
        )
        return ws_id

    async def close_ws(self, ws_id: str, code: int = 1000) -> None:
        """The browser closed its ``/ws`` -> tear down the viewer + tell home."""
        viewer = self._viewers.pop(ws_id, None)
        self.reg.ws_routes.pop(ws_id, None)
        if viewer is None:
            return
        if viewer.pump_task is not None:
            viewer.pump_task.cancel()
        # tunnel may already be gone on a full teardown; ignore send errors.
        try:
            await self.tunnel.send_frame(
                protocol.ws_close(viewer.stream_id, ws_id, code)
            )
        except Exception:  # noqa: BLE001 - tunnel closing, best-effort
            pass

    # ------------------------------------------------- tunnel -> browser fan

    async def on_tunnel_frame(self, frame: Frame) -> None:
        """Route ONE frame the home sent up the tunnel to the right browser.

        Enforces the orphan-stream rule: a RESP/WS frame for an unknown stream
        is an ERROR (raises ``ProxyError``), never silently buffered (W3.2)."""
        t = frame.type
        if t == FrameType.RESP_HEAD:
            await self._on_resp_head(frame)
        elif t == FrameType.RESP_DATA:
            await self._on_resp_data(frame)
        elif t == FrameType.WS_DATA:
            await self._on_ws_data(frame)
        elif t == FrameType.WS_CLOSE:
            await self._on_ws_close(frame)
        elif protocol.is_control_type(t):
            # PING/PONG/REVOKE handled by the connection loop, not here.
            return
        else:
            raise ProxyError(f"unexpected tunnel frame {frame.name} from home")

    async def _on_resp_head(self, frame: Frame) -> None:
        ex = self._exchanges.get(frame.stream_id)
        if ex is None:
            if self._is_late_reply(frame.stream_id):
                return          # we aborted it; the home had already answered
            raise ProxyError(
                f"RESP_HEAD for unknown stream_id {frame.stream_id} (orphan)"
            )
        if ex.head_seen:
            raise ProxyError(
                f"duplicate RESP_HEAD for stream_id {frame.stream_id}"
            )
        ex.head_seen = True
        status = int(frame.header.get("status", 502))
        headers = frame.header.get("headers", [])
        await ex.on_head(status, headers)

    async def _on_resp_data(self, frame: Frame) -> None:
        ex = self._exchanges.get(frame.stream_id)
        if ex is None:
            if self._is_late_reply(frame.stream_id):
                return          # tail of a reply we already abandoned
            raise ProxyError(
                f"RESP_DATA for unknown stream_id {frame.stream_id} (orphan)"
            )
        if not ex.head_seen:
            raise ProxyError(
                f"RESP_DATA before RESP_HEAD on stream_id {frame.stream_id}"
            )
        eof = frame.eof()
        await ex.on_data(frame.payload, eof)
        if eof:
            ex.done.set()
            self._drop_exchange(frame.stream_id)

    async def _on_ws_data(self, frame: Frame) -> None:
        ws_id = frame.ws_id()
        viewer = self._viewers.get(ws_id)
        if viewer is None:
            # A WS_DATA for a ws_id no longer in self._viewers is the EXPECTED
            # close race, NOT a protocol violation: when a browser /ws drops we
            # pop the viewer + send WS_CLOSE down (close_ws), but the home's
            # _run_ws keeps emitting queued/in-flight WS_DATA until the WS_CLOSE
            # reaches it and cancels the task. Those trailing frames must be a
            # benign DROP -- raising here would propagate ProxyError out through
            # the scope read loop and tear down the WHOLE multiplexed home tunnel
            # (every sibling viewer 502s, every in-flight tunneled HTTP aborts,
            # the home redials). The orphan-stream FATAL rule is for RESP_*
            # streams; a WS_DATA racing a WS_CLOSE is normal, so drop + move on.
            return
        seq = int(frame.header.get("seq", 0))
        self._enqueue_viewer(viewer, seq, frame.payload)

    async def _on_ws_close(self, frame: Frame) -> None:
        ws_id = frame.ws_id()
        viewer = self._viewers.pop(ws_id, None)
        self.reg.ws_routes.pop(ws_id, None)
        if viewer is None:
            return
        code = int(frame.header.get("code", 1000))
        if viewer.pump_task is not None:
            viewer.pump_task.cancel()
        await viewer.browser.close(code)

    # ---------------------------------------------- per-browser egress buffer

    def _enqueue_viewer(self, viewer: _WsViewer, seq: int, payload: bytes) -> None:
        """Enqueue one ``/ws`` event toward a browser, applying the bounded-
        buffer drop/coalesce policy. The per-viewer ``seq`` is carried so a drop
        is a DETECTABLE gap the browser re-snapshots on (not silent)."""
        item = (seq, payload)
        try:
            viewer.buffer.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass
        # Overflow: a slow browser. Apply the policy.
        etype = _event_type(payload)
        if etype in _COALESCE_TYPES:
            # Keep the LATEST preview/sequence: drop one old item, push newest.
            try:
                viewer.buffer.get_nowait()
                viewer.buffer.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
        else:
            # Drop-oldest STATUS (tolerable): drop oldest, push newest.
            try:
                viewer.buffer.get_nowait()
                viewer.buffer.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
        # Sustained overflow flag: the pump disconnects the browser if the
        # buffer stays saturated (see _pump_viewer). We mark it on every overflow
        # so a persistently-full buffer trips the disconnect.
        viewer.overflowed = True

    async def _pump_viewer(self, viewer: _WsViewer) -> None:
        """Drain one viewer's egress buffer to its browser socket. Runs as an
        isolated task per viewer so a SLOW browser's send only blocks ITS OWN
        pump -- never the relay's read of the scope WSS, never a sibling viewer.

        On sustained overflow (the buffer was full AND we just delivered a
        coalesced/dropped frame leaving a gap) the browser is DISCONNECTED so a
        stuck browser cannot pin relay memory; the sibling keeps its full
        stream."""
        try:
            while True:
                seq, payload = await viewer.buffer.get()
                # Detectable-gap accounting: a non-contiguous seq means we
                # dropped/coalesced -- the browser will re-snapshot. We still
                # deliver the newest frame.
                viewer.last_seq = seq
                try:
                    await viewer.browser.send_text(payload.decode("utf-8"))
                except Exception:  # noqa: BLE001 - browser socket gone
                    await self.close_ws(viewer.ws_id, 1011)
                    return
                # Disconnect policy: if the buffer overflowed AND remains
                # saturated after we drained one, the browser is not keeping up.
                if viewer.overflowed and viewer.buffer.full():
                    await viewer.browser.close(1013)  # try-again-later
                    await self.close_ws(viewer.ws_id, 1013)
                    return
                if viewer.buffer.empty():
                    viewer.overflowed = False
        except asyncio.CancelledError:  # pragma: no cover - normal teardown
            raise

    # ------------------------------------------------------------- teardown

    def _drop_exchange(self, stream_id: int) -> None:
        self._exchanges.pop(stream_id, None)
        self.reg.req_routes.pop(stream_id, None)

    async def shutdown(self, code: int = 1012) -> None:
        """The home tunnel dropped: fail every in-flight exchange and disconnect
        every browser ``/ws`` so they re-resolve + resync against the new tunnel
        (the failover path, §T7(6) instance-failover)."""
        for ex in list(self._exchanges.values()):
            ex.done.set()
        self._exchanges.clear()
        self.reg.req_routes.clear()
        for viewer in list(self._viewers.values()):
            if viewer.pump_task is not None:
                viewer.pump_task.cancel()
            try:
                await viewer.browser.close(code)
            except Exception:  # noqa: BLE001
                pass
        self._viewers.clear()
        self.reg.ws_routes.clear()


def _event_type(payload: bytes) -> str:
    """Best-effort extraction of a ``/ws`` event ``type`` for the drop policy.
    A non-JSON / typeless payload is treated as drop-oldest (the conservative,
    status-like default)."""
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if isinstance(obj, dict):
        return str(obj.get("type", ""))
    return ""
