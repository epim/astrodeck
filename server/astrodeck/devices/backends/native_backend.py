"""Native backend: direct ASCOM Alpaca (the no-NINA path).

WRAPS ``devices.alpaca`` (``make_device`` + ``discover``) behind the
``Backend`` / ``BackendSession`` Protocols from ``devices.backend``. This is the
vendor-neutral target: talk Alpaca straight to ZWO / Pegasus / QHY / ASCOM
Remote without a NINA bridge in the middle.

Stage A is purely additive: this module does not change what ``alpaca`` does, it
only re-exposes its factory through the pluggable contract and self-registers in
the registry so choosing "native" becomes DATA, not an ``if/elif``.

The ``alpaca`` module is imported as a module (not its names) so that
``alpaca.make_device`` / ``alpaca.discover`` resolve at call time -- which keeps
the device factory a single, monkeypatchable seam for tests.
"""
from __future__ import annotations

from .. import alpaca
from ..backend import ROLES, Backend, BackendSession, ConnSpec, register

#: Map a pluggable role to the ASCOM Alpaca device-type string ``make_device``
#: expects. Note ``safety`` -> ``"safetymonitor"`` (the Alpaca name) and that
#: ``guider`` is intentionally absent: Alpaca has no guider device; native
#: guiding is done via PHD2 (``native_guider()`` returns None).
_ROLE_TO_DEV_TYPE: dict[str, str] = {
    "camera": "camera",
    "telescope": "telescope",
    "focuser": "focuser",
    "filterwheel": "filterwheel",
    "switch": "switch",
    "safety": "safetymonitor",
}


class NativeSession:
    """A live native (Alpaca) session bound to one host.

    Devices are created lazily on first ``get_device`` for a role and cached per
    role, so repeated calls reuse the same device object (and its underlying
    ``AlpacaConnection``). ``conn`` for each role carries the port / dev_num.
    """

    name = "native"

    def __init__(self, host: str | None):
        self.host = host
        self._devices: dict[str, object] = {}

    async def get_device(self, role: str, conn: ConnSpec) -> object:
        """Return (creating + caching on first use) the device for ``role``.

        ``conn.port`` / ``conn.dev_num`` locate the device on the Alpaca server.
        The Alpaca device-type comes from an EXPLICIT ``conn.dev_type`` when the
        caller supplied one (so the hub's single-role connect passes its exact
        type through), else it is mapped from the role via ``_ROLE_TO_DEV_TYPE``.
        The device name is taken from ``conn.extra['name']`` when provided (the
        hub's display name), else falls back to ``conn.role``/``role``.
        """
        cached = self._devices.get(role)
        if cached is not None:
            return cached
        dev_type = conn.dev_type or _ROLE_TO_DEV_TYPE.get(role)
        if dev_type is None:
            raise KeyError(f"native backend has no Alpaca device for role {role!r}")
        host = conn.host if conn.host is not None else self.host
        name = (conn.extra or {}).get("name") or conn.role or role
        dev = alpaca.make_device(
            host, conn.port, dev_type, conn.dev_num, name
        )
        self._devices[role] = dev
        return dev

    def native_guider(self) -> object | None:
        """None: native guiding is done via PHD2, not an Alpaca device."""
        return None

    def native_solver(self) -> object | None:
        """None: the hub picks the solver via ``solve.get_solver``."""
        return None

    async def health(self) -> dict | None:
        """A trivial always-ok snapshot; the native path has no out-of-band link
        (each Alpaca call carries its own error)."""
        return {"backend": "native", "ok": True}

    async def close(self) -> None:
        """No-op: Alpaca devices are stateless HTTP clients owned per-device; the
        hub closes the devices it holds. Closing the session drops our cache."""
        self._devices.clear()


class NativeBackend:
    """The ``native`` adapter the registry stores and ``open()``s."""

    name = "native"
    label = "Native (direct)"
    roles = ROLES
    discoverable = True

    async def open(self, conn: ConnSpec) -> BackendSession:
        """Open a session bound to ``conn.host`` (per-role port/dev_num come from
        each role's own ConnSpec at ``get_device`` time)."""
        return NativeSession(conn.host)

    async def discover(self) -> list[dict]:
        """Alpaca UDP discovery: ``[{address, port, devices:[...]}, ...]``."""
        return await alpaca.discover()


# Self-register at import (last-registration-wins).
register(NativeBackend())
