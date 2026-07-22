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

from astrodeck import __version__ as _app_version

from .. import alpaca
from ..backend import Backend, BackendSession, ConnSpec, register

#: Map a pluggable role to the ASCOM Alpaca device-type string ``make_device``
#: expects. Note ``safety`` -> ``"safetymonitor"`` (the Alpaca name). ``guider``
#: is intentionally absent: Alpaca has no guider device — native guiding runs the
#: Rust engine over a guide CAMERA (``native_guider()``), it is never a
#: ``get_device`` role. ``guide_camera`` DOES map to a real Alpaca ``camera`` (a
#: second camera addressed by its own ``dev_num``), so a rig that explicitly
#: assigns a native guide camera can hand it to the native guider (P2-T3, D6).
_ROLE_TO_DEV_TYPE: dict[str, str] = {
    "camera": "camera",
    "telescope": "telescope",
    "focuser": "focuser",
    "filterwheel": "filterwheel",
    "switch": "switch",
    "safety": "safetymonitor",
    "rotator": "rotator",
    "guide_camera": "camera",
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
        self._guider: object | None = None       # lazily-built NativeGuider (P2-T3)

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
        """The native Rust-engine autoguider over this session's guide camera +
        mount, or None to let the hub fall back to the PHD2 bridge (P2-T3, spec
        §3.3/§5).

        Built lazily from the roles already connected on THIS session: an
        explicitly-assigned ``guide_camera`` (else the imaging ``camera`` as an
        OAG-style fallback) plus the ``telescope``. Returns None when the wheel
        is absent or either device is missing — so a native rig with no guide
        camera cleanly degrades to PHD2 instead of a guider that can't run.
        SYNC by contract."""
        if self._guider is not None:
            return self._guider
        from ...providers import NATIVE_AVAILABLE
        if not NATIVE_AVAILABLE:
            return None
        gcam = self._devices.get("guide_camera") or self._devices.get("camera")
        tel = self._devices.get("telescope")
        if gcam is None or tel is None:
            return None
        # Deferred import: keep module load light (the native guider pulls the
        # guide stack / numpy) and avoid a cycle just to register the backend.
        from ...guide.native import NativeGuider, guide_algo_config
        # A4 (P2-T3 review F2, CLOSED): compute a real image_scale_arcsec from
        # the configured guide-scope focal length + the guide camera's pixel
        # size, so on-sky RMS is reported in true arcsec. Falls back to 1.0
        # (the documented default, guiding correctness unaffected — calibration
        # measures px/ms empirically) when either input is missing. The guide
        # loop runs at bin 1, so binning = 1 here.
        image_scale = 1.0
        image_scale_known = False
        try:
            from ...config import config_store
            guide_fl = config_store.cfg().optics.guide_focal_length_mm
            px = getattr(gcam, "pixel_size_um", None)
            binning = 1
            if guide_fl and guide_fl > 0 and px and px > 0:
                image_scale = 206.265 * float(px) / float(guide_fl) * binning
                image_scale_known = True  # real arcsec/px → RMS reported in arcsec
        except Exception:  # pragma: no cover - defensive; scale stays 1.0
            image_scale = 1.0
            image_scale_known = False
        self._guider = NativeGuider(
            gcam, tel,
            config={"exposure_s": 2.0, "image_scale_arcsec": image_scale,
                    "image_scale_known": image_scale_known,
                    **guide_algo_config()},
            profile_id=None)
        return self._guider

    def guide_camera(self) -> object | None:
        """The native rig's dedicated guide camera (the ``guide_camera`` role's
        Alpaca device), or None when the rig assigns no separate guide camera.
        SYNC by contract."""
        return self._devices.get("guide_camera")

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
    # guider *device* (no ``guider`` key in ``_ROLE_TO_DEV_TYPE``), so the
    # W1.9 drift guard keeps it off ``roles``. The native autoguider is served
    # via ``native_guider()`` (the Rust engine over an assigned guide camera +
    # mount, P2-T3) when a profile explicitly overrides the ``guider`` role onto
    # this endpoint — never auto-advertised. ``guide_camera`` IS advertised
    # (P2-T3 fix round, D6): it is a real Alpaca camera device addressed by its
    # own dev_num, so the assignment UI can put a dedicated guide camera on a
    # real rig. ``safety`` stays (``safetymonitor`` is served).
    roles = ("camera", "telescope", "focuser", "filterwheel", "switch", "safety",
             "rotator", "guide_camera")
    discoverable = True
    hostless = False                # Alpaca is a network endpoint (host:port)
    version = _app_version
    # These three equal the ``Backend`` Protocol's own declared defaults, but
    # are set explicitly (not left implicit) so this backend stays a real
    # ``isinstance(..., Backend)`` per the runtime_checkable Protocol: default
    # VALUES declared in a Protocol class body only apply to classes that
    # *inherit* the Protocol; these backends duck-type it structurally instead
    # (same reason ``hostless`` above must be assigned explicitly, not relied
    # on as a Protocol default).
    author = ""
    min_app_version = "0"
    transport = "network"
    hardware = True
    driver_type = "alpaca"     # preserves _DRIVER_TYPE_TO_BACKEND["alpaca"] = "native"

    async def open(self, conn: ConnSpec) -> BackendSession:
        """Open a session bound to ``conn.host`` (per-role port/dev_num come from
        each role's own ConnSpec at ``get_device`` time)."""
        return NativeSession(conn.host)

    async def discover(self) -> list[dict]:
        """Alpaca UDP discovery: ``[{address, port, devices:[...]}, ...]``."""
        return await alpaca.discover()


# Self-register at import (last-registration-wins).
register(NativeBackend())
