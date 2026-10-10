# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The `ascom-local` backend (COM-T6): a managed local COM host + the EXISTING
Alpaca client pointed at it (spec §3.2). Windows-only self-registration; off
Windows this module registers nothing so ascom-local is simply absent.

AscomLocalSession REUSES NativeSession (devices/backends/native_backend.py)
unchanged — it only forces every role's ConnSpec onto the comhost loopback
endpoint before delegating, so the real Alpaca device classes drive the host.
"""
from __future__ import annotations

import sys
from typing import Awaitable, Callable

from astrodeck import __version__ as _app_version

from ..alpaca import AlpacaConnection
from ..backend import ConnSpec, register
from .native_backend import NativeSession


class ComhostConnection(AlpacaConnection):
    """The Alpaca connection to the managed comhost, which follows the comhost
    across a respawn (#992).

    ``ComHostManager`` respawns a dead comhost on a NEW ephemeral port, and a
    plain connection keeps the port it was built with: a reconnect after the
    respawn put its ``Connected=true`` to a port nothing listens on, so a dead
    comhost could never be recovered by reconnecting its devices. A
    ``Connected=true`` PUT is a (re)connect, so it first asks the manager where
    the comhost is now (``current_port`` is the manager's ``ensure``, which also
    respawns a dead one) and repoints before sending. The devices sharing this
    connection read their address from it, so they all follow.

    ``current_port=None`` is a connection that never follows (a session built
    on a fixed port)."""

    def __init__(self, host: str, port: int,
                 current_port: "Callable[[], Awaitable[int]] | None" = None):
        super().__init__(host, port)
        self._current_port = current_port

    async def put(self, dev_type: str, dev_num: int, method: str, **params):
        if (self._current_port is not None and method == "connected"
                and params.get("Connected") is True):
            port = await self._current_port()
            if port != self.port:
                self.repoint(port)
        return await super().put(dev_type, dev_num, method, **params)


class AscomLocalSession(NativeSession):
    """A NativeSession whose devices all live on the local comhost port."""

    name = "ascom-local"

    def __init__(self, port: int,
                 current_port: "Callable[[], Awaitable[int]] | None" = None):
        super().__init__(host="127.0.0.1")
        self._port = port
        self._current_port = current_port

    def _new_connection(self, host, port) -> object:
        return ComhostConnection(host, port, self._current_port)

    async def get_device(self, role: str, conn: ConnSpec) -> object:
        # Force the endpoint onto the managed comhost; keep the caller's
        # dev_type/dev_num/role/extra. The EXISTING Alpaca client (via
        # NativeSession) does the rest — no new client (spec §3, Global
        # Constraint reuse-existing-Alpaca-client).
        conn = ConnSpec(
            backend="ascom-local", host="127.0.0.1", port=self._port,
            dev_type=conn.dev_type, dev_num=conn.dev_num,
            role=conn.role or role, extra=dict(conn.extra or {}))
        return await super().get_device(role, conn)


class AscomLocalBackend:
    name = "ascom-local"
    label = "ASCOM (local)"
    roles = ("camera", "telescope", "focuser", "filterwheel", "switch",
             "safety", "rotator", "guide_camera")
    discoverable = True
    hostless = False
    version = _app_version
    #: author/min_app_version equal the Protocol defaults but are set
    #: explicitly so isinstance(Backend) still holds -- see native_backend.py's
    #: note.
    author = ""
    min_app_version = "0"
    hardware = True            # reuses NativeSession -> real Alpaca devices
    driver_type = ""           # implicit built-in, not a user-configured type
    transport = "network"      # loopback HTTP, network-addressed

    async def open(self, conn: ConnSpec) -> AscomLocalSession:
        from ...comhost.manager import get_manager
        manager = get_manager()
        port = await manager.ensure()
        return AscomLocalSession(port, current_port=manager.ensure)

    async def discover(self) -> list[dict]:
        """The native scan: registry-enumerated COM drivers (role-tagged)."""
        from .. import ascom_registry
        return ascom_registry.enumerate_offers()


# Windows-only registration (Global Constraint: clean cross-platform
# degradation). Off Windows COM is unavailable, so ascom-local must be absent
# from the registry, /api/backends, and the drivers surface.
if sys.platform == "win32":
    register(AscomLocalBackend())
