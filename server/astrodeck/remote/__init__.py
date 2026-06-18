"""AstroDeck remote-access (W3) -- the SCOPE-SIDE dial-out client + wire protocol.

This package owns the home's half of the no-gRPC / no-Tailscale WS-tunnel design
(see ``docs/superpowers/specs/2026-06-16-pluggable-backends-rbac-remote-design.md``,
W3 workstream). The home dials ONE outbound WSS to a small public relay; the relay
tunnels remote-browser HTTP + /ws traffic down that connection; the home replays
each tunneled request against its OWN in-process ASGI app (NO network hop) with the
request scope marked REMOTE so ``resolve_principal`` hard-denies the open ``none``
default for remote callers.

Public surface:
  protocol  -- the binary frame codec (``Frame``, ``FrameType``, encode/decode)
  relay_client -- the dial-out client (``RelayClient``, ``run_relay_client``,
                  ``REMOTE_SCOPE_KEY``, ``scope_is_remote``)

The whole package is OPT-IN: nothing here runs unless ``RemoteConfig.enabled`` and
a ``relay_url`` are set. A LAN-only install never imports the websockets dependency
at runtime (the client lazy-imports it only when it actually dials).
"""
from __future__ import annotations

from .protocol import (DEFAULT_MAX_PAYLOAD, Frame, FrameType, ProtocolError,
                       decode_frame, encode_frame)

__all__ = [
    "Frame", "FrameType", "ProtocolError",
    "encode_frame", "decode_frame", "DEFAULT_MAX_PAYLOAD",
]
