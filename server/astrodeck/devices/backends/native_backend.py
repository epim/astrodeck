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
from ..backend import Backend, BackendSession, ConnSpec, register

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
    """A live native (Alpaca) session owning a per-endpoint connection POOL.

    The session owns a ``dict[(host, port) -> AlpacaConnection]``: two roles on
    the SAME host:port share ONE ``AlpacaConnection`` (one ``httpx.AsyncClient``);
    a different host opens a second. Devices are created lazily on first
    ``get_device`` for a role, built against the SHARED connection, have their
    ``role`` set and ``connect()`` awaited, and are cached per role so repeated
    calls reuse the same device object. ``close()`` ``aclose``'s EVERY owned
    connection so a profile switch leaks no httpx client (W1.2).
    """

    name = "native"

    def __init__(self, host: str | None):
        self.host = host
        self._devices: dict[str, object] = {}
        # (host, port) -> shared AlpacaConnection. Distinct endpoints get
        # distinct connections; same endpoint reuses one (the pool).
        self._conns: dict[tuple[str | None, int | None], object] = {}

    def _connection(self, host: str | None, port: int | None) -> object:
        """Look up (or create) the shared ``AlpacaConnection`` for ``host:port``.

        Two roles on the same endpoint reuse ONE connection; a different host or
        port opens another. The connection (and its ``httpx.AsyncClient``) is
        owned by the session and torn down in ``close()``."""
        key = (host, port)
        conn = self._conns.get(key)
        if conn is None:
            conn = alpaca.AlpacaConnection(host, port)
            self._conns[key] = conn
        return conn

    async def get_device(self, role: str, conn: ConnSpec) -> object:
        """Return (creating + caching on first use) the connected device for
        ``role``.

        ``conn.port`` / ``conn.dev_num`` locate the device on the Alpaca server.
        The Alpaca device-type comes from an EXPLICIT ``conn.dev_type`` when the
        caller supplied one (so the hub's single-role connect passes its exact
        type through), else it is mapped from the role via ``_ROLE_TO_DEV_TYPE``.
        The device name is taken from ``conn.extra['name']`` when provided (the
        hub's display name), else falls back to ``conn.role``/``role``.

        The device is built against the session's SHARED ``AlpacaConnection`` for
        its ``(host, port)`` (the pool), its ``role`` is set, and ``connect()`` is
        awaited before it is handed back, so the orchestrator's uniform connect
        path finds it already live.
        """
        cached = self._devices.get(role)
        if cached is not None:
            return cached
        dev_type = conn.dev_type or _ROLE_TO_DEV_TYPE.get(role)
        if dev_type is None:
            raise KeyError(f"native backend has no Alpaca device for role {role!r}")
        host = conn.host if conn.host is not None else self.host
        name = (conn.extra or {}).get("name") or conn.role or role
        connection = self._connection(host, conn.port)
        cls = alpaca.DEVICE_CLASSES.get(dev_type.lower())
        if cls is None:
            raise KeyError(
                f"native backend: unsupported Alpaca device type {dev_type!r} "
                f"for role {role!r}"
            )
        dev = cls(connection, conn.dev_num, name)
        dev.role = role                          # device identity for Profiles (A.6)
        await dev.connect()
        self._devices[role] = dev
        return dev

    def native_guider(self) -> object | None:
        """None: native guiding is done via PHD2, not an Alpaca device."""
        return None

    def guide_camera(self) -> object | None:
        """None: the native path has no dedicated guide-camera pseudo-device
        (the imaging camera is a real Alpaca camera). SYNC by contract."""
        return None

    def native_solver(self) -> object | None:
        """None: the hub picks the solver via ``solve.get_solver``."""
        return None

    async def health(self) -> dict | None:
        """A small per-host reachability snapshot. The native path has no
        out-of-band link (each Alpaca call carries its own error), so this
        reports the endpoints the pool holds open."""
        return {
            "backend": "native",
            "ok": True,
            "hosts": sorted(f"{h}:{p}" for (h, p) in self._conns),
        }

    async def close(self) -> None:
        """``aclose`` EVERY owned ``AlpacaConnection`` (each closes its own
        ``httpx.AsyncClient``) so a profile switch leaks nothing, then drop the
        device + connection caches. Best-effort per connection: one failing
        ``aclose`` must not strand the others."""
        for connection in self._conns.values():
            try:
                await connection.close()
            except Exception:  # noqa: BLE001 -- close must be best-effort
                pass
        self._conns.clear()
        self._devices.clear()


class NativeBackend:
    """The ``native`` adapter the registry stores and ``open()``s."""

    name = "native"
    label = "Native (direct)"
    # Explicit fillable set (W1.2 table), EXCLUDING ``guider``: Alpaca has no
    # guider device (no ``guider`` key in ``_ROLE_TO_DEV_TYPE``), so native
    # guiding is done via PHD2. ``safety`` stays -- ``safetymonitor`` is served.
    roles = ("camera", "telescope", "focuser", "filterwheel", "switch", "safety")
    discoverable = True
    hostless = False                # Alpaca is a network endpoint (host:port)

    async def open(self, conn: ConnSpec) -> BackendSession:
        """Open a session bound to ``conn.host`` (per-role port/dev_num come from
        each role's own ConnSpec at ``get_device`` time)."""
        return NativeSession(conn.host)

    async def discover(self) -> list[dict]:
        """Alpaca UDP discovery: ``[{address, port, devices:[...]}, ...]``."""
        return await alpaca.discover()


# Self-register at import (last-registration-wins).
register(NativeBackend())
