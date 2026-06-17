"""Rig assembly from a ``RigSpec`` (Stage A).

Turns a declarative ``RigSpec`` (primary backend + per-role overrides) into a
live rig: a ``role -> device`` dict, the backend sessions that produced it, the
chosen guider, an optional native solver source, and a ``role -> error`` map of
roles that failed to come up.

Design points:
  - Each DISTINCT backend instance (keyed by backend name + host + port) is
    opened exactly ONCE, so roles sharing a backend share one session (and thus
    one underlying connection / shared state -- e.g. the whole sim rig).
  - Assembly is graceful: a role that fails to open or hand out a device is
    recorded in ``failures`` and SKIPPED, never raised. The rest of the rig
    still comes up.
  - Import-light: this module imports only ``backend`` (registry + specs). It
    does NOT import ``hub`` or ``solve`` (no cycle). The native solver is
    returned as a duck-typed object for the caller to use or ignore.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .backend import (
    ROLES,
    BackendSession,
    ConnSpec,
    RigSpec,
    get_backend,
)


@dataclass
class AssembledRig:
    """The result of :func:`assemble`.

    ``rig``           : role -> connected device object (only roles that came up).
    ``sessions``      : session-key -> the open ``BackendSession`` behind it.
    ``guider``        : the guider-role session's ``native_guider()`` (or None).
    ``solver_source`` : the camera-role session's ``native_solver()`` (or None ->
                        caller falls back to ``solve.get_solver``).
    ``failures``      : role -> error string for roles that failed (graceful).
    """

    rig: dict[str, object] = field(default_factory=dict)
    sessions: dict[str, BackendSession] = field(default_factory=dict)
    guider: object | None = None
    solver_source: object | None = None
    failures: dict[str, str] = field(default_factory=dict)


def _session_key(name: str, conn: ConnSpec) -> str:
    """A stable key identifying one distinct backend INSTANCE.

    Roles that resolve to the same backend name at the same host:port share one
    session (and thus one live connection). Different addresses on the same
    backend get separate sessions."""
    return f"{name}@{conn.host or ''}:{conn.port if conn.port is not None else ''}"


async def assemble(spec: RigSpec) -> AssembledRig:
    """Assemble a live rig from ``spec``.

    For every canonical role, resolve its ``ConnSpec`` (an explicit override in
    ``spec.roles`` wins; otherwise it defaults to ``spec.primary``), open the
    owning backend session ONCE per distinct instance, and hand out the device.
    Per-role failures are recorded in ``result.failures`` and skipped.

    After devices are placed, the guider is taken from the guider-role session's
    ``native_guider()`` and the solver source from the camera-role session's
    ``native_solver()`` (each may be absent/None)."""
    result = AssembledRig()
    # session-key -> open BackendSession (deduped so each instance opens once).
    sessions: dict[str, BackendSession] = {}
    # role -> (session-key, ConnSpec) for the post-assembly guider/solver lookup.
    role_session: dict[str, str] = {}

    for role in ROLES:
        conn = spec.resolve(role)
        name = conn.backend
        key = _session_key(name, conn)
        try:
            session = sessions.get(key)
            if session is None:
                backend = get_backend(name)
                session = await backend.open(conn)
                sessions[key] = session
            # The guider is NOT a get_device() role: it is sourced from the
            # session's native_guider() after assembly (the sim, NINA, etc. own
            # their guider object directly). Opening the session above is enough
            # to make native_guider() reachable; we just record the binding.
            if role == "guider":
                role_session[role] = key
                continue
            device = await session.get_device(role, conn)
        except Exception as exc:  # noqa: BLE001 - graceful degrade per role
            result.failures[role] = str(exc)
            continue
        result.rig[role] = device
        role_session[role] = key

    result.sessions = sessions

    # guider: the guider-role session's native guider, if that role came up.
    guider_key = role_session.get("guider")
    if guider_key is not None:
        try:
            result.guider = sessions[guider_key].native_guider()
        except Exception as exc:  # noqa: BLE001
            result.failures.setdefault("guider", str(exc))

    # solver source: the camera-role session's native solver (may be None ->
    # the caller falls back to solve.get_solver).
    camera_key = role_session.get("camera")
    if camera_key is not None:
        try:
            result.solver_source = sessions[camera_key].native_solver()
        except Exception:  # noqa: BLE001 - solver is optional; never fatal
            result.solver_source = None

    return result


def to_dict(result: AssembledRig) -> dict[str, Any]:
    """A small JSON-able summary (for diagnostics / API), without the live
    device objects themselves."""
    return {
        "roles": sorted(result.rig),
        "sessions": sorted(result.sessions),
        "has_guider": result.guider is not None,
        "has_native_solver": result.solver_source is not None,
        "failures": dict(result.failures),
    }
