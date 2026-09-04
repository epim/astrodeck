"""The scope-tunnel connection handler (transport-free core of the read loop).

``ScopeConnection`` owns ONE home-side WSS once it has registered: it processes
the ``HELLO`` handshake, runs keepalive (PING out / PONG-miss teardown), routes
every inbound frame to the home's ``TunnelMultiplexer``, and pushes ``REVOKE``
control frames down to the home. It is deliberately transport-free -- it is fed
decoded ``Frame``s and emits ``Frame``s through the ``ScopeTunnel.send_frame``
callback -- so the unit tests drive it over an in-memory channel (no WSS on the
wire) and the Starlette shell wraps a real ``websockets`` connection.

The single ON-WIRE WSS integration test (separate gate) exercises the real
socket; everything here is off-wire.
"""
from __future__ import annotations

import time
from typing import Optional

from . import protocol
from .protocol import Frame, FrameType, ProtocolError
from .proxy import DEFAULT_WS_EGRESS_MAX, TunnelMultiplexer
from .registry import HomeRegistration, HomeRegistry, RegistrationError, ScopeTunnel


class ScopeConnection:
    """Drives one scope tunnel: HELLO -> register -> route frames + keepalive.

    Lifecycle:
      1. ``handle_hello(frame)`` validates + registers (generation fencing),
         returns the ``HELLO_ACK`` to send back. On failure it returns a
         rejecting ACK and the caller closes the socket.
      2. ``on_frame(frame)`` routes each subsequent inbound frame: PONG updates
         liveness, RESP_*/WS_* fan out via the multiplexer, an unexpected
         control frame is handled, anything malformed raises.
      3. ``make_ping()`` / ``is_stale()`` drive keepalive.
      4. ``close()`` unregisters + tears down the multiplexer (browsers
         re-resolve + resync against the next tunnel)."""

    def __init__(self, registry: HomeRegistry, tunnel: ScopeTunnel, *,
                 ws_egress_max: int = DEFAULT_WS_EGRESS_MAX):
        self.registry = registry
        self.tunnel = tunnel
        self.ws_egress_max = ws_egress_max
        self.reg: Optional[HomeRegistration] = None
        self.mux: Optional[TunnelMultiplexer] = None
        self.registered = False
        self._registration_committed = False

    # -- handshake ------------------------------------------------------------

    def handle_hello(self, frame: Frame, *, defer_commit: bool = False) -> Frame:
        """Process the first frame. Returns the ``HELLO_ACK`` to send back.

        Rejects (ok=False) a non-HELLO first frame, an incompatible major
        proto version, a bad token, or a stale generation -- the caller sends
        the ACK then closes on ``ok=False``."""
        if frame.type != FrameType.HELLO:
            return protocol.hello_ack(False, reason="expected HELLO first frame")
        pv = int(frame.header.get("proto_version", 0))
        if pv != protocol.PROTO_VERSION:
            # Reject-incompatible-major policy (single major today).
            return protocol.hello_ack(
                False, reason=f"unsupported proto_version {pv}"
            )
        try:
            self.reg = self.registry.register(frame, self.tunnel)
        except RegistrationError as exc:
            return protocol.hello_ack(False, reason=str(exc))
        self.mux = TunnelMultiplexer(
            self.reg, ws_egress_max=self.ws_egress_max)
        self.registered = True
        if not defer_commit:
            self.commit_registration()
        endpoint = f"/h/{self.reg.home_id}/"
        return protocol.hello_ack(True, endpoint=endpoint)

    def commit_registration(self) -> bool:
        """Fence the previous generation after our ACK reached the peer."""

        if self.reg is None:
            return False
        committed = self.registry.commit(self.reg)
        self._registration_committed = committed
        return committed

    # -- frame routing --------------------------------------------------------

    async def on_frame(self, frame: Frame) -> Optional[Frame]:
        """Route one inbound frame. Returns a ``Frame`` to send back when the
        frame demands an immediate reply (a PING -> PONG), else None.

        Raises ``ProtocolError`` on a frame that cannot be routed (orphan
        stream, unexpected type) so the caller tears the tunnel down."""
        if not self.registered or self.mux is None:
            raise ProtocolError("frame before HELLO_ACK")
        t = frame.type
        if t == FrameType.PONG:
            self.tunnel.mark_pong()
            return None
        if t == FrameType.PING:
            # The home may also ping us; reply with a PONG on the control stream.
            return protocol.pong(time.time())
        if t == FrameType.REVOKE:
            # A home-pushed revocation (e.g. a local kill of a session). The
            # relay drops the matching per-ws_id projections; here we just route
            # it (the server layer maps jti->ws_id and disconnects).
            return None
        # Everything else (RESP_HEAD/RESP_DATA/WS_DATA/WS_CLOSE) fans out.
        await self.mux.on_tunnel_frame(frame)
        return None

    # -- keepalive ------------------------------------------------------------

    def make_ping(self) -> Frame:
        """Build a keepalive PING (sent on the control stream at the ping
        interval)."""
        return protocol.ping(time.time())

    def is_stale(self) -> bool:
        """True iff the tunnel has missed too many PONGs -> tear it down and let
        the scope re-dial."""
        return self.tunnel.is_stale(
            ping_interval_s=self.registry.ping_interval_s,
            max_misses=self.registry.max_ping_misses,
        )

    # -- revocation push ------------------------------------------------------

    async def push_revoke(self, jti: list[str], ws_id: list[str]) -> None:
        """Push a ``REVOKE`` control frame DOWN to the home (relay->scope), e.g.
        when an admin revokes a viewer link at the relay. The home drops the
        matching sessions; the relay separately disconnects the browsers."""
        await self.tunnel.send_frame(protocol.revoke(jti, ws_id))

    # -- teardown -------------------------------------------------------------

    async def close(self, code: int = 1012) -> None:
        """Tear the tunnel down: unregister (only if still the live tunnel) and
        disconnect every browser so they re-resolve + resync."""
        self.tunnel.closed = True
        if self.reg is not None:
            if self._registration_committed:
                self.registry.unregister(self.reg.home_id, self.tunnel)
            else:
                self.registry.rollback(self.reg)
        if self.mux is not None:
            await self.mux.shutdown(code)
