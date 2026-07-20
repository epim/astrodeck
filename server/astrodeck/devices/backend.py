"""Pluggable-backend contract (Stage A).

The goal: choosing Simulator / NINA / Native(Alpaca) for each device role becomes
DATA, not an ``if/elif`` chain. This module defines the interface and registry;
it is purely additive and behavior-preserving — actual backends WRAP the existing
factories (``build_sim_rig``, ``build_nina_rig``, ``make_device``) without changing
what they do.

Import-light by design: this module imports NOTHING from ``hub`` (or the concrete
backends) so it can be imported anywhere without a cycle. The solver is referenced
only by duck type (``native_solver() -> object | None``) for the same reason.

Concepts:
  - ``ConnSpec``   : a serializable "where/what to connect" intent for one role.
  - ``Backend``    : a vendor adapter (sim / nina / native). Opens a ``BackendSession``.
  - ``BackendSession`` : a live connection to one backend; hands out role devices,
                    plus optional native guider/solver and a health probe.
  - registry       : ``register`` / ``get_backend`` / ``list_backends`` over ``BACKENDS``.
  - ``RigSpec``    : a whole-rig plan — a primary backend plus per-role overrides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: The device roles a rig fills. Mirrors ``hub.ROLES`` / the device ABCs in
#: ``devices/base.py`` but kept as a local literal so this module imports nothing.
#: ``guide_camera`` (P2-T3 fix round, D6): the dedicated guide camera is any
#: Camera DEVICE assigned to this role — first-class so ``drivers.describe_all``
#: offers it and the standard rig-assignment UI can assign one on real rigs.
ROLES: tuple[str, ...] = (
    "camera",
    "telescope",
    "focuser",
    "guider",
    "filterwheel",
    "switch",
    "safety",
    "rotator",
    "guide_camera",
)


@dataclass
class ConnSpec:
    """A serializable intent to connect ONE role on ONE backend.

    ``backend`` is the only required field (the registry name, e.g. ``"sim"``,
    ``"nina"``, ``"native"``). The rest are optional because different backends
    need different coordinates: Alpaca/native needs ``host``/``port``/``dev_type``/
    ``dev_num``; NINA needs ``host``/``port``; the sim needs nothing. ``extra``
    carries backend-specific options without widening this contract.
    """

    backend: str
    host: str | None = None
    port: int | None = None
    dev_type: str | None = None
    dev_num: int | None = None
    role: str | None = None
    #: Reference to a CONFIGURED driver (AppConfig.drivers[].id). When set, the
    #: hub resolves it to concrete backend/host/port/extra at connect time
    #: (drivers.resolve_driver_ids); raw addressing above stays authoritative
    #: when it is None — additive back-compat (spec §3.3).
    driver_id: str | None = None
    #: transport discriminator: "network" (host/port) | "serial" (port_path).
    #: Defaults keep every existing spec/profile a network spec (back-compat).
    transport: str = "network"
    port_path: str | None = None    # serial device path, e.g. "COM3" / "/dev/ttyACM0"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """A plain JSON-able dict (for Profiles / persistence)."""
        return {
            "backend": self.backend,
            "host": self.host,
            "port": self.port,
            "dev_type": self.dev_type,
            "dev_num": self.dev_num,
            "role": self.role,
            "driver_id": self.driver_id,
            "transport": self.transport,
            "port_path": self.port_path,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConnSpec":
        """Rebuild from ``to_dict`` output; tolerant of missing optional keys."""
        return cls(
            backend=d["backend"],
            host=d.get("host"),
            port=d.get("port"),
            dev_type=d.get("dev_type"),
            dev_num=d.get("dev_num"),
            role=d.get("role"),
            driver_id=d.get("driver_id"),
            transport=d.get("transport", "network"),
            port_path=d.get("port_path"),
            extra=dict(d.get("extra") or {}),
        )


@runtime_checkable
class BackendSession(Protocol):
    """A live connection to one backend.

    A backend's ``open(conn)`` returns one of these. It hands out concrete device
    objects per role (each a ``devices.base.Device`` subclass — duck-typed here to
    avoid importing it), plus the backend's native guider/solver where it has them,
    a health probe, and a clean ``close``.
    """

    name: str

    async def get_device(self, role: str, conn: "ConnSpec") -> object:
        """Return the connected device object filling ``role`` (per ``conn``)."""
        ...

    def native_guider(self) -> object | None:
        """The backend's own guider (e.g. ``NinaGuider``), or None to let the hub
        pick a guider (PHD2 / sim)."""
        ...

    def guide_camera(self) -> object | None:
        """The backend's dedicated guide-camera device, or None.

        A non-ROLE pseudo-device (the sim exposes one; nina/native/phd2 default to
        None). The orchestrator calls this on the CAMERA role's session and
        surfaces the result as ``ConnectResult.guide_camera`` so the hub never
        reaches the ``SimSession``-only property. SYNC by contract."""
        ...

    def native_solver(self) -> object | None:
        """The backend's own plate solver (duck-typed; avoids a solve import
        cycle), or None to let the hub choose via ``solve.get_solver``."""
        ...

    async def health(self) -> dict | None:
        """A link-health snapshot (e.g. NINA last-ok/last-error), or None when the
        backend has no out-of-band link to report on."""
        ...

    async def close(self) -> None:
        """Release the session. Bridge semantics apply: closing AstroDeck's view
        must never tear down externally-owned equipment (e.g. NINA's)."""
        ...


@runtime_checkable
class Backend(Protocol):
    """A vendor adapter: the thing the registry stores and ``open()``s.

    ``name`` is the registry key (stored in ``ConnSpec.backend``); ``label`` is the
    human name for the UI; ``roles`` is the subset of ``ROLES`` this backend can
    fill; ``discoverable`` says whether ``discover()`` does real network work.
    """

    name: str
    label: str
    roles: tuple[str, ...]
    discoverable: bool
    #: Endpoint-less backend: the orchestrator's ``_group`` normalizes host/port
    #: to None in the grouping key so all of this backend's roles coalesce into
    #: ONE session regardless of stray addressing. True on ``SimBackend`` /
    #: ``Phd2Backend`` (one shared SimRig / one local PHD2 socket); default False.
    hostless: bool = False

    #: --- driver manifest (additive; all defaulted so existing backends satisfy
    #: the Protocol unchanged). ``name`` remains the STABLE identity embedded in
    #: saved profiles/config and MUST NOT change across versions.
    version: str = "0"            # driver semver; built-ins report the app version
    author: str = ""             # "" for first-party
    min_app_version: str = "0"   # discovered externals older-than-app are skipped
    transport: str = "network"   # "network" | "serial"  ("loopback" reserved)
    hardware: bool = False       # real hardware? -> the device-borne safety flag
    driver_type: str = ""        # config-facing configurable-driver type ("" = none)

    async def open(self, conn: "ConnSpec") -> BackendSession:
        """Open a live session for this backend (wraps the existing factory)."""
        ...

    async def discover(self) -> list[dict]:
        """Find instances/devices on the network; ``[]`` when not discoverable."""
        ...


# ---------------------------------------------------------------------- registry

#: The process-wide backend registry: name -> Backend. Populated by ``register``.
BACKENDS: dict[str, Backend] = {}


def register(b: Backend) -> Backend:
    """Register a backend under ``b.name`` (last registration wins). Returns the
    backend so it can be used as a decorator or in an assignment."""
    BACKENDS[b.name] = b
    return b


def get_backend(name: str) -> Backend:
    """Return the registered backend named ``name``; raise ``KeyError`` if absent."""
    try:
        return BACKENDS[name]
    except KeyError:
        raise KeyError(
            f"unknown backend {name!r}; registered: {sorted(BACKENDS)}"
        ) from None


def list_backends() -> list[dict]:
    """A JSON-able summary of every registered backend (for the UI / API):
    ``[{name, label, roles, discoverable, version, author, min_app_version,
    transport, hardware, driver_type}, ...]``, ordered by name."""
    return [
        {
            "name": b.name,
            "label": b.label,
            "roles": tuple(b.roles),
            "discoverable": bool(b.discoverable),
            "version": str(getattr(b, "version", "0")),
            "author": str(getattr(b, "author", "")),
            "min_app_version": str(getattr(b, "min_app_version", "0")),
            "transport": str(getattr(b, "transport", "network")),
            "hardware": bool(getattr(b, "hardware", False)),
            "driver_type": str(getattr(b, "driver_type", "")),
        }
        for b in sorted(BACKENDS.values(), key=lambda b: b.name)
    ]


# ------------------------------------------------------------------------ rigspec

@dataclass
class RigSpec:
    """A whole-rig connection plan.

    ``primary`` is the default backend name; ``roles`` holds per-role ``ConnSpec``
    overrides. ``resolve(role)`` returns the explicit ``ConnSpec`` for a role, or
    synthesizes a default one on the primary backend when none is given — so a
    plain "everything on sim" rig needs only ``RigSpec("sim")``.
    """

    primary: str
    roles: dict[str, ConnSpec] = field(default_factory=dict)

    def resolve(self, role: str) -> ConnSpec:
        """Resolve ``role`` to a ``ConnSpec``. An explicit override wins; otherwise
        the default backend is ``primary`` and the spec carries the role name."""
        spec = self.roles.get(role)
        if spec is not None:
            return spec
        return ConnSpec(backend=self.primary, role=role)

    def to_dict(self) -> dict[str, Any]:
        """A plain JSON-able dict (for Profiles / persistence)."""
        return {
            "primary": self.primary,
            "roles": {r: s.to_dict() for r, s in self.roles.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RigSpec":
        """Rebuild from ``to_dict`` output."""
        return cls(
            primary=d["primary"],
            roles={r: ConnSpec.from_dict(s) for r, s in (d.get("roles") or {}).items()},
        )
