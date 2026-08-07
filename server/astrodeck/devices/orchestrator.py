"""Connect a live rig from a ``RigSpec`` (Stage A).

Turns a declarative ``RigSpec`` (primary backend + per-role overrides) into a
live rig: a ``role -> device`` dict, the backend sessions that produced it, the
chosen guider, the dedicated guide camera, an optional solver source, and a
per-role ``RoleResult`` covering EVERY role the rig requested.

Design points:
  - Only the roles the ``RigSpec`` actually REQUESTS are worked (the primary
    backend's fillable roles UNION the explicit overrides) -- NOT all of
    ``ROLES``. So an unfillable role on a given rig never shows a red LED (W1.6);
    a role that was never requested gets no ``RoleResult`` at all.
  - Each DISTINCT backend ENDPOINT (keyed by the ``(backend, host, port)`` tuple)
    is opened exactly ONCE, so roles sharing an endpoint share one session (and
    thus one underlying connection / shared state -- e.g. the whole sim rig).
    Hostless backends (sim, phd2-local) normalize host/port -> None so all their
    roles coalesce into one session regardless of stray addressing.
  - Connection is graceful: a role that fails to open or hand out a device is
    recorded as a not-ok ``RoleResult`` and SKIPPED, never raised. The rest of
    the rig still comes up. An unexpected raise tears down any sessions already
    opened (no transport leak) before propagating.
  - Import-light: this module imports only ``backend`` (registry + specs). It
    does NOT import ``hub`` or ``solve`` at module scope (no cycle). The native
    solver fallback is reached via a deferred ``solve.get_solver`` import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .backend import (
    BackendSession,
    ConnSpec,
    RigSpec,
    get_backend,
)

#: An endpoint grouping key: (backend name, host|None, port|None). Hostless
#: backends normalize host/port to None so all their roles share one key.
EndpointKey = tuple[str, "str | None", "int | None"]


@dataclass
class RoleResult:
    """The tri-state outcome for ONE requested role.

    ``attempted=False`` => never tried (unfillable / not requested) -- NOT a red
    LED. ``attempted=True, ok=True`` => connected. ``attempted=True, ok=False``
    => failed (carries ``error``). ``connect_profile`` emits exactly ONE per
    REQUESTED role (W1.3/W1.6)."""

    role: str
    ok: bool = False
    error: str | None = None
    attempted: bool = False


@dataclass
class ConnectResult:
    """The result of :func:`connect_profile`.

    ``rig``          : role -> connected device object (only roles that came up).
    ``sessions``     : ``(backend, host, port)`` tuple -> the open session behind it.
    ``guider``       : the guider-role session's ``native_guider()`` (or None).
    ``guide_camera`` : the camera-role session's ``guide_camera()`` (or None).
    ``solver``       : the camera-role session's ``native_solver()`` (or None ->
                       caller falls back to ``solve.get_solver``).
    ``results``      : one ``RoleResult`` per REQUESTED role (tri-state for the
                       W1.6 LED grid). The old ``failures`` dict is a DERIVED
                       back-compat alias in :func:`to_dict`.
    """

    rig: dict[str, object] = field(default_factory=dict)
    sessions: dict[EndpointKey, BackendSession] = field(default_factory=dict)
    guider: object | None = None
    guide_camera: object | None = None
    solver: object | None = None
    results: list[RoleResult] = field(default_factory=list)


def _normalize(conn: ConnSpec) -> EndpointKey:
    """The endpoint grouping key for ``conn``: ``(backend, host, port)``, with
    host/port normalized to None for a hostless backend.

    Normalizing off ``get_backend(name).hostless`` (NOT a literal ``{sim, phd2}``
    name set) means all sim roles always collapse to ``("sim", None, None)`` and
    a stray host on a sim/phd2-local override can never split the shared state.

    SERIAL transport carries its address in ``port_path`` (COM3 / /dev/ttyACM0)
    and leaves host/port at None, so keying on host/port alone would coalesce two
    DIFFERENT serial units of the same backend into ONE session — the second
    role would silently be served from the first unit's port. The serial address
    therefore takes the host slot (the key is opaque; shape is preserved)."""
    name = conn.backend
    try:
        hostless = bool(getattr(get_backend(name), "hostless", False))
    except KeyError:
        # Unknown backend: keep its raw addressing; open() will surface the error.
        hostless = False
    if hostless:
        return (name, None, None)
    if getattr(conn, "transport", "network") == "serial":
        return (name, conn.port_path, None)
    return (name, conn.host, conn.port)


def _requested_roles(spec: RigSpec) -> set[str]:
    """The roles this rig "asks for": every role the PRIMARY backend can fill
    (``get_backend(spec.primary).roles``) UNION every role with an explicit
    override (``set(spec.roles)``).

    ``RigSpec`` carries only ``primary: str`` + ``roles: dict``, so the primary's
    fillable set MUST be read from the registry -- it cannot be derived from
    ``RigSpec`` alone. An explicit override for a role NOT in that backend's
    ``roles`` is STILL requested (so it surfaces a FAILED ``RoleResult`` rather
    than vanishing); the apply-time reject-rule (W1.C) handles the override."""
    # primary "none": the Equipment surface's explicit-only rig (spec §4.1) —
    # ONLY assigned roles are requested; no primary-derived fill. A real
    # primary keeps the W1.6 union semantics below unchanged.
    if spec.primary in ("", "none"):
        return set(spec.roles)
    return set(spec.roles) | set(get_backend(spec.primary).roles)


def _group(resolved: dict[str, ConnSpec]) -> dict[EndpointKey, list[tuple[str, ConnSpec]]]:
    """Group resolved roles by normalized endpoint key so each ENDPOINT opens
    EXACTLY ONE session. Hostless backends coalesce to ``(name, None, None)``."""
    by_endpoint: dict[EndpointKey, list[tuple[str, ConnSpec]]] = {}
    for role, conn in resolved.items():
        by_endpoint.setdefault(_normalize(conn), []).append((role, conn))
    return by_endpoint


def _pick_guider(resolved: dict[str, ConnSpec],
                 sessions: dict[EndpointKey, BackendSession],
                 rig: dict[str, object] | None = None) -> object | None:
    """Return the guider from the GUIDER ROLE's session ``native_guider()``.

    The guider session is located via the SAME normalized grouping key ``_group``
    produces (hostless host/port -> None applied to ``resolved['guider']`` BEFORE
    keying into ``sessions``), so a stray-addressed sim/phd2-local guider override
    still finds its session instead of emitting a spurious "no guider".

    FALLBACK when no guider role is assigned: the GUIDE CAMERA's session. A rig
    that has a guide camera and a mount already has everything the native guider
    needs, and requiring a separate ``guider`` row to say so is a trap you can
    only escape if you know the row exists — the observed cost was a rig with a
    guide camera bolted on, an unused guide FL setting, and ``hub.guider is
    None`` forever, so guiding could not be started, calibrated, or even
    refused with a useful message.

    Deliberately keyed on ``guide_camera`` and not on the imaging camera, even
    though ``native_guider()`` will fall back to the imaging camera on its own.
    An explicitly-assigned guide camera is the operator SAYING they intend to
    guide. Inferring a guider for every rig would hand the sequencer a guider
    built on the imaging camera — and ``plan.guide`` defaults to True, so the
    first target would try to guide with the camera it is imaging through."""
    guider_conn = resolved.get("guider") or resolved.get("guide_camera")
    if guider_conn is not None:
        session = sessions.get(_normalize(guider_conn))
        if session is not None:
            guider = session.native_guider()
            if guider is not None:
                return guider

    # LAST RESORT: build one from the assembled rig. Every session accessor
    # looks only at ITS OWN devices, and on a multi-vendor rig the guide camera
    # and the mount are not in the same session — a ZWO ASI guide camera opens
    # a "zwo-asi" session while the mount opens "zwo-am5", so the camera's
    # session finds no telescope and gives up. That is the whole reason a rig
    # with a guide camera bolted on had no guider at all, and no amount of
    # assigning a `guider` row would have fixed it.
    #
    # Uses rig["guide_camera"] only, never the imaging camera: an explicitly
    # assigned guide camera is the operator saying they intend to guide, while
    # plan.guide defaults True, so inferring one for every rig would have the
    # first target try to guide through the sensor it is exposing with.
    if not rig:
        return None
    gcam, tel = rig.get("guide_camera"), rig.get("telescope")
    if gcam is None or tel is None:
        return None
    from ..guide.native import build_native_guider
    return build_native_guider(gcam, tel, shares_the_imaging_sensor=False)


def _pick_solver(camera_conn: ConnSpec | None, camera_dev: object | None,
                 sessions: dict[EndpointKey, BackendSession],
                 real_motion: bool = False) -> object | None:
    """Return the active solver for the camera that produced the frame.

    Precedence has ONE owner: return the CAMERA role's session
    ``native_solver()`` if non-None (the sim session yields a guarded
    ``SimSolver`` carrying its provenance), else delegate to ``solve.get_solver``
    -- the single owner of ASTAP-vs-guarded-sim precedence (do NOT re-derive
    "ASTAP else sim" here). The literal ``mode`` passed to ``get_solver`` is
    derived from the CAMERA SESSION's ``name`` (NOT a hub-wide mode), so the
    per-role SimSolver guard refuses to sync a real mount (W1.5)."""
    if camera_conn is None:
        return None
    session = sessions.get(_normalize(camera_conn))
    if session is None:
        return None
    native = session.native_solver()
    if native is not None:
        # A session-provided solver (the sim session's guarded SimSolver) must
        # ALSO see the rig's real-motion truth, or a mixed rig (real serial
        # mount + sim camera) would fake-solve through this path (A-minor 2).
        if real_motion and hasattr(native, "real_motion"):
            native.real_motion = True
        return native
    # Fallback: the single owner of ASTAP-vs-guarded-sim precedence. Deferred
    # import keeps this module cycle-free. ``mode`` from the camera session name.
    from ..solve import get_solver

    mode = _solver_mode(session.name)
    return get_solver(None, mode=mode, real_motion=real_motion)


def _solver_mode(session_name: str | None) -> str | None:
    """Map a camera session name to the literal solver mode ``get_solver``/
    ``SimSolver`` keys on ('sim' / 'nina' / 'alpaca'). The native backend solves
    as the real 'alpaca' rig; anything else passes through unchanged."""
    if session_name == "native":
        return "alpaca"
    return session_name


async def connect_profile(spec: RigSpec) -> ConnectResult:
    """Bring a ``RigSpec`` online.

    Resolve ONLY the roles the spec requests (primary's fillable roles + explicit
    overrides), group them by endpoint, open each endpoint's session ONCE, and
    hand out each role's device (graceful per-role degrade). Every requested role
    yields exactly one ``RoleResult``. The guider is taken from the guider-role
    session's ``native_guider()`` and the guide camera + solver from the camera
    role's session. Any session opened before an unexpected raise is closed so no
    transport leaks."""
    requested: set[str] = _requested_roles(spec)
    resolved: dict[str, ConnSpec] = {r: spec.resolve(r) for r in requested}
    by_endpoint = _group(resolved)

    sessions: dict[EndpointKey, BackendSession] = {}
    rig: dict[str, object] = {}
    # every REQUESTED role starts attempted=False ("never tried"); flipped below.
    results: dict[str, RoleResult] = {r: RoleResult(r) for r in requested}
    opened: list[BackendSession] = []           # for teardown-on-unexpected-raise

    try:
        for key, role_conns in by_endpoint.items():
            backend_name = key[0]
            rep_conn = role_conns[0][1]         # endpoint's representative ConnSpec
            try:
                session = await get_backend(backend_name).open(rep_conn)
            except Exception as exc:  # noqa: BLE001 - whole endpoint down -> roles fail
                for role, _ in role_conns:
                    if role != "guider":
                        results[role] = RoleResult(
                            role, ok=False, error=str(exc), attempted=True)
                continue
            sessions[key] = session
            opened.append(session)
            backend_hw = bool(getattr(get_backend(backend_name), "hardware", False))
            for role, conn in role_conns:
                # The guider is NOT a get_device() role: it is sourced from the
                # session's native_guider() after assembly (step below). A
                # guider-only backend (PHD2) must NOT emit a device RoleResult.
                if role == "guider":
                    continue
                try:
                    device = await session.get_device(role, conn)
                except Exception as exc:  # noqa: BLE001 - per-role degrade, never crash
                    results[role] = RoleResult(
                        role, ok=False, error=str(exc), attempted=True)
                    continue
                if device is not None:
                    try:
                        device.hardware = backend_hw
                    except Exception:  # noqa: BLE001 - stamping must never break connect
                        pass
                    rig[role] = device
                results[role] = RoleResult(
                    role, ok=device is not None, attempted=True,
                    error=None if device is not None else "role unavailable")
    except BaseException:
        # An unexpected raise must not leak transports.
        for s in opened:
            try:
                await s.close()
            except Exception:  # noqa: BLE001
                pass
        raise

    # guider: the guider-role session's native guider (resolved via the SAME
    # normalized key, never raw addressing). Only requested roles get a result.
    guider = _pick_guider(resolved, sessions, rig)
    if "guider" in requested:
        results["guider"] = RoleResult(
            "guider", ok=guider is not None, attempted=True,
            error=None if guider is not None else "no guider")

    # guide camera: EITHER its own guide_camera-role device (filled by the main
    # loop from a SEPARATE backend/endpoint -- native two-vendor rigs: e.g. a ZWO
    # ASI guide cam alongside a Player One imaging cam) OR, as a fallback, the
    # camera-role session's guide_camera() accessor (sim/NINA, one session owns
    # both cameras). None for nina/native/phd2 without a guide camera.
    guide_camera = _pick_guide_camera(resolved.get("camera"), sessions, rig)
    # A rig-sourced guide camera is already hardware-stamped by the main loop with
    # its OWN backend's flag; only the accessor-sourced one (sim/NINA) needs
    # stamping from the camera backend.
    if guide_camera is not None and rig.get("guide_camera") is None:
        cam_conn = resolved.get("camera")
        if cam_conn is not None:
            try:
                guide_camera.hardware = bool(
                    getattr(get_backend(cam_conn.backend), "hardware", False))
            except Exception:  # noqa: BLE001
                pass

    # solver source: the camera-role session's native solver (or the get_solver
    # fallback -- the single owner of ASTAP-vs-guarded-sim precedence). The
    # device-flag real-motion truth rides along so a SimSolver fallback can
    # refuse to fake-solve ANY rig with real motion hardware (A-minor 2) --
    # including native serial mounts the session-name mode never knew.
    real_motion = any(
        bool(getattr(rig.get(r), "hardware", False))
        for r in ("telescope", "focuser", "rotator"))
    solver = _pick_solver(resolved.get("camera"), rig.get("camera"), sessions,
                          real_motion=real_motion)

    return ConnectResult(rig=rig, sessions=sessions, guider=guider,
                         guide_camera=guide_camera, solver=solver,
                         results=list(results.values()))


def _pick_guide_camera(camera_conn: ConnSpec | None,
                       sessions: dict[EndpointKey, BackendSession],
                       rig: dict[str, object]) -> object | None:
    """The guide camera, from EITHER source:

    1. its own ``guide_camera`` role, filled by the main ``get_device`` loop from
       a SEPARATE backend/endpoint (native two-vendor rigs: e.g. a ZWO ASI guide
       camera alongside a Player One imaging camera) -- already hardware-stamped
       by the loop; OR
    2. the camera-role session's ``guide_camera()`` accessor (sim/NINA, where one
       session owns both cameras and ``guide_camera`` is a non-ROLES extra).

    Prefer (1); fall back to (2). For native single-endpoint rigs (camera +
    guide_camera on one session) the two sources are the SAME object, so the
    preference is a no-op there."""
    dev = rig.get("guide_camera")
    if dev is not None:
        return dev
    if camera_conn is None:
        return None
    session = sessions.get(_normalize(camera_conn))
    if session is None:
        return None
    return session.guide_camera()


def to_dict(result: ConnectResult) -> dict[str, Any]:
    """A small JSON-able summary (for diagnostics / API), without the live device
    objects themselves.

    The legacy keys ``{roles, sessions, has_guider, has_native_solver, failures}``
    are kept as a back-compat superset (``failures`` DERIVED from the not-ok
    ``results``, ``has_native_solver`` reading the renamed ``solver``), and
    ``results`` is added alongside. The ``sessions`` summary stringifies the
    tuple keys and sorts None-safely so partial-port multi-endpoint rigs neither
    raise a ``TypeError`` (``int`` vs ``None``) nor emit non-JSON-able tuples."""
    failures = {
        rr.role: rr.error or "role unavailable"
        for rr in result.results if not rr.ok and rr.attempted
    }
    return {
        "roles": sorted(result.rig),
        # stringify each tuple key (JSON-able) and sort the strings (total order,
        # None-safe) -- do NOT sort the raw tuples (TypeError on int vs None).
        "sessions": sorted(str(k) for k in result.sessions),
        "has_guider": result.guider is not None,
        "has_native_solver": result.solver is not None,
        "failures": failures,
        "results": [
            {"role": rr.role, "ok": rr.ok, "error": rr.error,
             "attempted": rr.attempted}
            for rr in result.results
        ],
    }
