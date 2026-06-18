"""AstroDeck remote-access RELAY service (standalone, host-agnostic).

A small public service the home "scope" dials OUTBOUND over ONE persistent WSS.
A remote browser hits the relay over HTTPS+WSS; the relay TUNNELS each request
(and each ``/ws``) down the scope connection to the home app and streams the
response back. The home re-authenticates + re-authorizes EVERY tunnelled request
(remote=True) and the sun/RBAC/safety gates still enforce at the home. The relay
holds NO signing secret and CANNOT forge a principal -- it only forwards bytes
plus a home-verifiable principal token.

See ``relay/README.md`` for the protocol surface, how to run it locally, and how
to deploy it (Docker + Fly.io).
"""
from __future__ import annotations

__version__ = "0.1.0"

from . import protocol  # re-export the wire codec for convenience

__all__ = ["protocol", "__version__"]
