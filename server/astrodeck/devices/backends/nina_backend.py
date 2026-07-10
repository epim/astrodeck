"""NINA backend adapter (Stage A) -- the transition bridge.

WRAPS ``devices.nina.build_nina_rig`` behind the ``Backend`` / ``BackendSession``
Protocols. Purely additive and behavior-preserving: it does NOT change what the
NINA devices do -- it just hands out the very same device objects ``build_nina_rig``
returns, keyed by role, plus NINA's own ``NinaGuider`` as the native guider.

NINA is a network bridge to a running NINA instance (``discoverable = True``;
``discover()`` delegates to ``discover_nina``). It has no native plate solver of
its own here -- NINA mode routes solving through the local solver in the hub, so
``native_solver()`` returns None. ``close()`` releases AstroDeck's HTTP client
only; bridge semantics mean it never tears down NINA's externally-owned equipment.

Import-shape note: ``build_nina_rig`` returns
``{"client": NinaClient, "devices": {role: dev}, "guider": NinaGuider | None}``
-- the per-role devices are NESTED under ``"devices"`` (not flat on the rig dict),
and ``guider`` is a sibling key, so ``get_device``/``native_guider`` read from the
right places. The factory import is deferred into ``open()`` so merely registering
this backend at import time does not drag in httpx / numpy / PIL.
"""
from __future__ import annotations

from ..backend import BackendSession, ConnSpec, register


class NinaSession:
    """A live connection to one NINA instance.

    Built ONCE per ``open()``: ``build_nina_rig(host, port)`` probes NINA and
    returns one coherent set of device objects sharing a single ``NinaClient``.
    We keep that exact rig dict so every ``get_device`` call hands back the same
    instance, and reach the shared client for the health probe.
    """

    name = "nina"

    def __init__(self, rig: dict[str, object]) -> None:
        # ``rig`` is exactly what build_nina_rig() returns:
        #   {"client": NinaClient, "devices": {role: dev}, "guider": ... | None}
        self._rig = rig
        self._client = rig.get("client")
        self._devices: dict = rig.get("devices") or {}

    @property
    def client(self) -> object | None:
        """The shared ``NinaClient`` behind this session (or None).

        Public, read-only accessor so the hub can keep wiring ``self.nina_client``
        (heartbeat / event-stream / teardown) without reaching into a private
        attribute. Additive: it does not change what the session does."""
        return self._client

    async def get_device(self, role: str, conn: ConnSpec) -> object:
        """Return the NINA device filling ``role``.

        ``conn`` is accepted for interface parity but unused -- the session is
        already bound to a host/port from ``open()``. Raises ``KeyError`` for a
        role NINA did not report as connected (only connected roles are present)."""
        try:
            return self._devices[role]
        except KeyError:
            raise KeyError(
                f"nina backend has no device for role {role!r}; "
                f"NINA reported connected: {sorted(self._devices)}"
            ) from None

    def native_guider(self) -> object | None:
        """NINA's own guider (``NinaGuider``), or None when NINA reports no
        connected guider. SYNC by contract."""
        return self._rig.get("guider")

    def guide_camera(self) -> object | None:
        """NINA exposes no dedicated guide-camera device to AstroDeck; None per
        the Protocol default. SYNC by contract."""
        return None

    def native_solver(self) -> object | None:
        """NINA has no native plate solver exposed here -- NINA mode solves via
        the local solver in the hub. SYNC by contract."""
        return None

    async def health(self) -> dict | None:
        """A small link-health snapshot for the NINA bridge.

        Light probe: ping ``/version`` so the readout stays honest even when a
        long capture means no incidental NINA traffic. Reports ``ok`` plus the
        client's last-ok / last-error stamps. Never raises -- a failed ping is a
        reported ``ok: False``, not an exception."""
        client = self._client
        if client is None:
            return {"backend": "nina", "ok": False, "error": "no client"}
        ok = True
        try:
            await client.get("/version")
        except Exception as e:  # noqa: BLE001 -- health must never raise
            ok = False
            # client.get already stamps last_error; keep a local copy for the dict
            err = str(e)[:200]
        else:
            err = None
        return {
            "backend": "nina",
            "ok": ok,
            "host": getattr(client, "host", None),
            "port": getattr(client, "port", None),
            "last_ok": getattr(client, "last_ok", None),
            "last_error": err if err is not None else getattr(client, "last_error", None),
        }

    async def close(self) -> None:
        """Release AstroDeck's HTTP client only.

        Bridge semantics: this closes our view of NINA, never NINA's own
        equipment connections. No-op if the rig carried no client."""
        client = self._client
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 -- close must be best-effort
                pass


class NinaBackend:
    """The NINA vendor adapter (transition bridge).

    ``open(conn)`` awaits ``build_nina_rig(conn.host, conn.port)`` once and wraps
    the result in a ``NinaSession``. Discoverable on the LAN: ``discover()``
    delegates to ``discover_nina``.
    """

    name = "nina"
    label = "NINA"
    # ``switch`` is fillable (devices/nina.py bridges NINA's switch hub); only
    # ``safety`` stays unfillable (NINA exposes no SafetyMonitor class). W1.2/W1.9.
    roles = ("camera", "telescope", "focuser", "filterwheel", "switch", "guider", "rotator")
    discoverable = True
    hostless = False                # NINA is a network endpoint (host:port)

    async def open(self, conn: ConnSpec) -> BackendSession:
        """Probe NINA at ``conn.host``/``conn.port`` and return a session.

        Deferred import of ``build_nina_rig`` (it pulls in httpx / numpy / PIL)
        so merely registering this backend at import time stays cheap. ``port``
        falls back to NINA's default when the ConnSpec leaves it unset.

        Builder seam: ``conn.extra["build_rig"]`` may carry an explicit rig
        builder ``async (host, port) -> rig``. The hub passes its OWN module-level
        ``build_nina_rig`` reference there so the existing monkeypatch seam
        (``monkeypatch.setattr(hub, "build_nina_rig", ...)`` in the tests) keeps
        working through the harness. Absent it, the module factory is used."""
        from ..nina import DEFAULT_PORT
        from ..nina import build_nina_rig as default_build

        build = (conn.extra or {}).get("build_rig") or default_build
        port = conn.port if conn.port is not None else DEFAULT_PORT
        rig = await build(conn.host, port)
        return NinaSession(rig)

    async def discover(self) -> list[dict]:
        """Find NINA Advanced API instances on the local network."""
        from ..nina import discover_nina

        return await discover_nina()


# Self-register at import (last-registration-wins). ``register`` returns the
# instance; we keep a module-level handle for convenience/introspection.
NINA_BACKEND = register(NinaBackend())
