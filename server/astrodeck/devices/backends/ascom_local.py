"""The `ascom-local` backend (COM-T6): a managed local COM host + the EXISTING
Alpaca client pointed at it (spec §3.2). Windows-only self-registration; off
Windows this module registers nothing so ascom-local is simply absent.

AscomLocalSession REUSES NativeSession (devices/backends/native_backend.py)
unchanged — it only forces every role's ConnSpec onto the comhost loopback
endpoint before delegating, so the real Alpaca device classes drive the host.
"""
from __future__ import annotations

import sys

from astrodeck import __version__ as _app_version

from ..backend import ConnSpec, register
from .native_backend import NativeSession


class AscomLocalSession(NativeSession):
    """A NativeSession whose devices all live on the local comhost port."""

    name = "ascom-local"

    def __init__(self, port: int):
        super().__init__(host="127.0.0.1")
        self._port = port

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
        port = await get_manager().ensure()
        return AscomLocalSession(port)

    async def discover(self) -> list[dict]:
        """The native scan: registry-enumerated COM drivers (role-tagged)."""
        from .. import ascom_registry
        return ascom_registry.enumerate_offers()


# Windows-only registration (Global Constraint: clean cross-platform
# degradation). Off Windows COM is unavailable, so ascom-local must be absent
# from the registry, /api/backends, and the drivers surface.
if sys.platform == "win32":
    register(AscomLocalBackend())
