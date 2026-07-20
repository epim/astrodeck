"""Driver registry surface — configured + implicit drivers, availability
probes, and the offers mapping the Equipment/Settings UI renders from
(equipment-drivers spec 2026-07-08 §3.2).

Configured drivers (nina/alpaca/phd2) live in config (``AppConfig.drivers``);
implicit drivers (sim / astrodeck native engine / ASTAP) are DETECTED, not
stored. ``describe_all()`` merges both, probing configured drivers
concurrently, and NEVER raises — a probe failure (or bug) becomes a
``status.error`` row, so the API surface can't 500.

Probe results are cached ``PROBE_TTL_S`` per driver id. ``invalidate()`` drops
the cache; Phase 2 wires it to rig-connect failures (the spec's cache-honesty
rule: a failed connect must not leave a stale "reachable" row for up to 15 s).

Offers contract (spec review finding 1): every ``offers.devices`` entry
carries EVERYTHING the client needs to build that driver's ConnSpec/device
selector — Alpaca entries MUST include ``dev_type`` + ``dev_num``; nina/phd2/
sim entries need only ``role`` + ``name``. The UI never synthesizes addressing.
"""
from __future__ import annotations

import asyncio
import time

from .config import DriverEntry, config_store
from .devices.backend import ROLES

PROBE_TTL_S = 15.0

#: driver_id -> (monotonic stamp, probe result). Module-level cache, cleared by
#: ``invalidate()``. Monotonic for the TTL math; wall-clock ``probed_at`` rides
#: inside the result for the UI.
_CACHE: dict[str, tuple[float, dict]] = {}


def invalidate(driver_id: str | None = None) -> None:
    """Drop cached probe results (one driver, or ALL when None)."""
    if driver_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(driver_id, None)


# ------------------------------------------------------------------- helpers

def _ok(devices: list[dict], tasks: list[str], detail: str | None = None) -> dict:
    return {"reachable": True, "error": None, "detail": detail,
            "offers": {"devices": devices, "tasks": tasks}}


def _down(error: str) -> dict:
    return {"reachable": False, "error": error, "detail": None,
            "offers": {"devices": [], "tasks": []}}


# -------------------------------------------------------------------- probes

async def _probe_nina(host: str, port: int) -> dict:
    """NINA Advanced API probe: version check, then per-role equipment info.
    Reuses devices.nina's probe helpers so the enumeration logic lives once."""
    import httpx

    from .devices.nina import _detail, _probe_version
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            ver = await _probe_version(c, host, port)
            if ver is None:
                return _down(f"no NINA Advanced API at {host}:{port}")
            d = await _detail(c, host, port)
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return _down(str(e)[:200])
    devices = [{"role": role, "name": name}
               for role, name in sorted(d["devices"].items())]
    # NINA offers its own autofocus + TPPA plugin as task providers (spec §3.2).
    return _ok(devices, ["autofocus", "polar_align"], detail=f"API {ver}")


#: Alpaca DeviceType (lowercased) -> AstroDeck role. Inverse of the native
#: backend's ``_ROLE_TO_DEV_TYPE``. ``rotator`` is now mapped (the CAA spec
#: added the role); genuinely unknown types (e.g. ``dome``) are still SKIPPED,
#: not an error here. A ``camera`` device is additionally offered under the
#: ``guide_camera`` role (P2-T3 fix round, D6: the guide camera is any Camera
#: device assigned to that role) — see ``_probe_alpaca``, since this one-to-one
#: map cannot express a dev type serving two roles.
_DEV_TYPE_TO_ROLE: dict[str, str] = {
    "camera": "camera", "telescope": "telescope", "focuser": "focuser",
    "filterwheel": "filterwheel", "switch": "switch",
    "safetymonitor": "safety", "rotator": "rotator",
}


async def _probe_alpaca(host: str, port: int) -> dict:
    """Alpaca management-API probe: enumerate configured devices. Every entry
    carries dev_type + dev_num (review finding 1 — the ConnSpec addressing)."""
    import httpx
    url = f"http://{host}:{port}/management/v1/configureddevices"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            value = r.json().get("Value", [])
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return _down(str(e)[:200])
    devices: list[dict] = []
    for d in value if isinstance(value, list) else []:
        dev_type = str(d.get("DeviceType", "")).lower()
        role = _DEV_TYPE_TO_ROLE.get(dev_type)
        if role is None:
            continue
        entry = {"role": role,
                 "name": d.get("DeviceName") or dev_type,
                 "dev_type": dev_type,
                 "dev_num": int(d.get("DeviceNumber", 0))}
        devices.append(entry)
        # D6 (P2-T3 fix round): every Alpaca camera is ALSO offerable as the
        # dedicated guide camera — same device, same addressing, second role —
        # so the assignment UI's one-rule (offers-carry-the-role) lights the
        # guide_camera row for Alpaca drivers.
        if role == "camera":
            devices.append(dict(entry) | {"role": "guide_camera"})
    return _ok(devices, [])


async def _probe_phd2(host: str, port: int) -> dict:
    """PHD2 probe: a plain TCP connect to its event socket (default 4400)."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0)
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return _down(str(e)[:200] or "connection failed")
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 — best-effort close
        pass
    return _ok([{"role": "guider", "name": f"PHD2 @ {host}:{port}"}], [])


#: type -> probe coroutine. A dict (not if/elif) so tests can monkeypatch one
#: probe without touching the others.
_PROBES = {"nina": _probe_nina, "alpaca": _probe_alpaca, "phd2": _probe_phd2}


# ------------------------------------------------------------------ implicit

def _implicit_rows() -> list[dict]:
    """The detected (non-configured) drivers, fixed ids ``sim`` / ``astrodeck``
    / ``astap`` (spec §3.1): always appended after the configured rows so the
    UI can render "built-ins" under the user's own drivers."""
    from .providers import NATIVE_AVAILABLE
    from .solve import find_astap

    now = time.time()
    rows: list[dict] = [{
        "id": "sim", "type": "sim", "label": "Simulator", "enabled": True,
        "implicit": True,
        "status": {"reachable": True, "error": None, "detail": None,
                   "probed_at": now},
        "offers": {"devices": [{"role": r, "name": f"Simulated {r}"}
                                for r in ROLES],
                   # Only tasks sim actually implements (spec §5 honesty): the
                   # built-in polar simulator + the SimSolver. Autofocus on a
                   # sim rig is the NATIVE engine's V-curve — the astrodeck
                   # row's offer, not this one's.
                   "tasks": ["polar_align", "solve"]},
    }]

    if NATIVE_AVAILABLE:
        import astrodeck_native
        detail = str(getattr(astrodeck_native, "__version__", "installed"))
        status = {"reachable": True, "error": None, "detail": detail,
                  "probed_at": now}
        tasks = ["autofocus", "polar_align"]
    else:
        status = {"reachable": False, "detail": None, "probed_at": now,
                  "error": "astrodeck_native wheel not installed"}
        tasks = []
    rows.append({"id": "astrodeck", "type": "astrodeck",
                 "label": "AstroDeck native", "enabled": True, "implicit": True,
                 "status": status, "offers": {"devices": [], "tasks": tasks}})

    astap = find_astap()
    if astap:
        status = {"reachable": True, "error": None, "detail": str(astap),
                  "probed_at": now}
        tasks = ["solve"]
    else:
        status = {"reachable": False, "detail": None, "probed_at": now,
                  "error": "ASTAP binary not found"}
        tasks = []
    rows.append({"id": "astap", "type": "astap", "label": "ASTAP",
                 "enabled": True, "implicit": True,
                 "status": status, "offers": {"devices": [], "tasks": tasks}})

    # ascom-local (COM-T6): the bundled COM host is a built-in on Windows, like
    # the Simulator/native/ASTAP rows above — always available, no add step. Its
    # offers are the registry-enumerated COM drivers (each carrying dev_type +
    # dev_num so the Equipment dropdowns can assign one with no host/port/ProgID).
    # Windows-only: off Windows COM is unavailable, so the row is absent and the
    # UI degrades to native/Alpaca/NINA (spec §3.3).
    import sys
    if sys.platform == "win32":
        from .devices import ascom_registry
        try:
            devices = ascom_registry.enumerate_offers()
            status = {"reachable": True, "error": None,
                      "detail": f"{len({d['dev_type'] for d in devices})} type(s)",
                      "probed_at": now}
        except Exception:  # never 500 describe_all
            devices, status = [], {"reachable": False, "detail": None,
                                   "probed_at": now,
                                   "error": "ASCOM registry read failed"}
        rows.append({"id": "ascom-local", "type": "ascom-local",
                     "label": "ASCOM (local)", "enabled": True, "implicit": True,
                     "status": status, "offers": {"devices": devices, "tasks": []}})
    return rows


# --------------------------------------------------------------- describe_all

async def _probe_configured(entry: DriverEntry, force: bool) -> dict:
    row: dict = {"id": entry.id, "type": entry.type, "label": entry.label,
                 "enabled": entry.enabled, "implicit": False,
                 "host": entry.host, "port": entry.port}
    if not entry.enabled:
        # Disabled is a USER state, not a network state — short-circuit, never
        # probe, and report it verbatim so the UI can badge "disabled".
        row["status"] = {"reachable": False, "error": "disabled",
                         "detail": None, "probed_at": time.time()}
        row["offers"] = {"devices": [], "tasks": []}
        return row
    hit = _CACHE.get(entry.id)
    if not force and hit is not None and (time.monotonic() - hit[0]) < PROBE_TTL_S:
        res = hit[1]
    else:
        probe = _PROBES.get(entry.type)
        if probe is None:
            # A driver type with no network probe (e.g. a serial driver) is not
            # unreachable -- it simply isn't network-probed here. Report neutrally
            # rather than KeyError into describe_all's error row.
            res = {"reachable": False, "error": None,
                   "detail": f"no network probe for driver type {entry.type!r}",
                   "offers": {"devices": [], "tasks": []},
                   "probed_at": time.time()}
        else:
            res = await probe(entry.host, entry.port)
            res["probed_at"] = time.time()
            _CACHE[entry.id] = (time.monotonic(), res)
    row["status"] = {"reachable": res["reachable"], "error": res["error"],
                     "detail": res.get("detail"),
                     "probed_at": res["probed_at"]}
    # Copy, never alias: ``res`` (cache hit OR the just-stored fresh probe) is
    # the SAME dict object retained in ``_CACHE``, so handing back its
    # ``offers`` by reference would let a consumer mutation (e.g. a route that
    # appends/edits a returned device row) poison every future cache-hit read
    # (P1 deferred finding). One level deep is enough: ``offers`` nests only a
    # flat ``devices`` list of flat dicts and a flat ``tasks`` list of strings.
    offers = res["offers"]
    row["offers"] = {"devices": [dict(d) for d in offers["devices"]],
                     "tasks": list(offers["tasks"])}
    return row


async def describe_all(force: bool = False) -> dict:
    """The full option space the UI renders from: ``{roles, drivers}``
    (spec §3.2). ``roles`` comes from ``devices.backend.ROLES`` so a future
    role (rotator) appears with zero UI changes. NEVER raises — a probe bug
    degrades to a status.error row (defensive gather)."""
    entries = list(config_store.cfg().drivers)
    probed = await asyncio.gather(
        *(_probe_configured(e, force) for e in entries),
        return_exceptions=True)
    rows: list[dict] = []
    for e, r in zip(entries, probed):
        if isinstance(r, BaseException):
            rows.append({"id": e.id, "type": e.type, "label": e.label,
                         "enabled": e.enabled, "implicit": False,
                         "host": e.host, "port": e.port,
                         "status": {"reachable": False,
                                    "error": f"probe error: {r}",
                                    "detail": None, "probed_at": time.time()},
                         "offers": {"devices": [], "tasks": []}})
        else:
            rows.append(r)
    try:
        implicit = _implicit_rows()
    except Exception:  # noqa: BLE001 — implicit detection must never 500 describe_all
        implicit = []
    return {"roles": list(ROLES), "drivers": rows + implicit}


# ------------------------------------------------------- driver_id resolution

def driver_type_to_backend() -> dict[str, str]:
    """Config-facing driver ``type`` -> backend registry ``name``, derived from
    the registry (each backend that provides a configurable driver type declares
    it via ``Backend.driver_type``). Built-ins reproduce the historical map
    ``{"nina":"nina","alpaca":"native","phd2":"phd2"}``; a plugin backend that
    sets ``driver_type`` extends it with no core edit."""
    from .devices import backends as _b  # noqa: F401 - ensure registration
    from .devices.backend import BACKENDS
    return {getattr(b, "driver_type", ""): b.name
            for b in BACKENDS.values() if getattr(b, "driver_type", "")}


def configurable_driver_types() -> set[str]:
    """The set of user-configurable driver ``type`` values (registry-derived)."""
    return set(driver_type_to_backend())


def resolve_driver_ids(spec):
    """Resolve every ``ConnSpec.driver_id`` in ``spec`` to concrete addressing
    (spec §3.3). Returns ``(resolved_spec, role_to_driver_id, prefailed)``.

    A ConnSpec WITHOUT a driver_id passes through untouched (raw-addressing
    back-compat). One WITH a driver_id takes backend/host/port from the
    configured driver; its own dev_type/dev_num/role survive, and ``extra``
    merges driver-then-spec (spec wins) so a driver-level option (e.g. phd2
    ``managed``) flows in without the client re-sending it.

    A missing driver pre-fails its role with "driver removed: <id>"; a
    disabled one with "driver disabled: <label>" — per the spec's failure-
    honesty rules these become attempted+failed RoleResults at the hub, never
    a silent skip and never a whole-rig 500."""
    from .devices.backend import ConnSpec, RigSpec

    by_id = {d.id: d for d in config_store.cfg().drivers}
    roles: dict[str, ConnSpec] = {}
    role_to_driver: dict[str, str] = {}
    prefailed: list[tuple[str, str]] = []
    for role, conn in spec.roles.items():
        did = getattr(conn, "driver_id", None)
        if not did:
            roles[role] = conn
            continue
        d = by_id.get(did)
        if d is None:
            prefailed.append((role, f"driver removed: {did}"))
            continue
        if not d.enabled:
            prefailed.append((role, f"driver disabled: {d.label}"))
            continue
        role_to_driver[role] = did
        roles[role] = ConnSpec(
            backend=driver_type_to_backend().get(d.type, d.type),
            host=d.host,
            port=d.port,
            dev_type=conn.dev_type,
            dev_num=conn.dev_num,
            role=conn.role or role,
            driver_id=did,
            extra={**(d.extra or {}), **(conn.extra or {})},
        )
    return RigSpec(primary=spec.primary, roles=roles), role_to_driver, prefailed
