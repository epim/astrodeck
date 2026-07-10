# Equipment Tab — Phase 2 (Devices Surface) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The unified per-device Equipment surface: every device role is assigned to a configured driver (from Phase 1's `/api/drivers`), assignments compile to a `RigSpec` with `driver_id` ConnSpecs, the old mode-centric ConnectView and the Settings BackendPicker are retired, and removed/unreachable drivers degrade honestly.

**Architecture:** `driver_id` threads through the connect spine (`ConnSpec` → API body → profile row → `hub.connect_rigspec`, the single funnel). A new `drivers.resolve_driver_ids()` maps driver references to concrete backend/host/port at connect time; missing/disabled drivers become pre-failed `RoleResult`s that surface in `backend_links`. A new `primary: "none"` RigSpec mode requests ONLY explicitly-assigned roles. The UI gets a pure assignment lib (`lib/equipment.ts`) + an `EquipmentView` that replaces ConnectView under the existing `"connect"` view id.

**Tech Stack:** Python 3.11 / FastAPI / pydantic v2 / pytest; React 18 + TS + Tailwind; UI tests = inline-assert harness via `npx tsx` (NO vitest).

**Spec:** `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` §3.3, §4.1, §4.3, §4.4, §5 + the §3.2 cache-honesty rule. Phase 3 (task rows, resolver vocab) is OUT of scope.

## Global Constraints

- Repo root `C:\Users\bear\astro` (Git Bash `/c/Users/bear/astro`). Server tests from `server/`: `.venv/Scripts/python -m pytest <target> -q`. Full suite must stay green (821+ passed; native wheel installed in this venv, so no skips expected).
- NO new Python or npm dependencies.
- New/changed API surface keeps the house RBAC idiom (`dependencies=[Depends(require(CAP))]` + `@declare(CAP)`); `tests/test_backend_roles_match_served.py` enforces.
- Back-compat is a hard rule: raw host/port ConnSpecs and old profiles (no `driver_id`, no `primary_backend`) must connect exactly as before. `driver_id` and `primary:"none"` are additive.
- The UI view id stays `"connect"` (it is wired into store defaults, ConnectionBanner, preflight, NotConnectedInterstitial). Only its label ("Rig" → "Equipment"), component, and file change.
- KEEP: `DriversPanel.tsx` (Settings), `BackendLinkGrid.tsx`, `backendMeta.ts` (its `buildConnSpec` + tests stay valid). RETIRE (delete): `views/ConnectView.tsx`, `components/settings/BackendPicker.tsx`. Do NOT touch `CapabilitiesCard.tsx` (Phase 3).
- Driver-type → backend registry name mapping is EXACTLY: `{"nina": "nina", "alpaca": "native", "phd2": "phd2"}`.
- Match house comment style. Commit per task (`git -C /c/Users/bear/astro`). Never `--no-verify`.

---

### Task 1: `driver_id` + `primary:"none"` plumbing (spec layer)

**Files:**
- Modify: `server/astrodeck/devices/backend.py` (ConnSpec dataclass ~line 40, `to_dict` ~58, `from_dict` ~70)
- Modify: `server/astrodeck/devices/orchestrator.py` (`_requested_roles` ~line 99)
- Modify: `server/astrodeck/api/app.py` (`ConnSpecBody` ~line 369; the `connect_rig` route's body→ConnSpec mapping and its primary validation)
- Modify: `server/astrodeck/profiles.py` (`ProfileDevice` ~line 52, `to_rigspec` ~line 132)
- Test: `server/tests/test_driver_id_plumbing.py` (create)

**Interfaces:**
- Consumes: existing `ConnSpec`/`RigSpec` dataclasses, `ConnSpecBody`/`RigSpecBody` pydantic models, `ProfileDevice`.
- Produces (Tasks 2–3 rely on):
  - `ConnSpec.driver_id: str | None = None` — serialized by `to_dict`, parsed tolerantly by `from_dict` (`d.get("driver_id")`).
  - `RigSpec(primary="none", roles={...})` → orchestrator requests ONLY `set(spec.roles)` (no primary-derived fill).
  - `ConnSpecBody.driver_id: str | None = None` passed through the route's ConnSpec construction.
  - `ProfileDevice.driver_id: str = ""`; `to_rigspec` sets `ConnSpec.driver_id = d.driver_id or None`.
  - `/api/connect/rig` accepts `primary: "none"` (no 422).

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_driver_id_plumbing.py`:

```python
"""driver_id + primary:"none" plumbing (equipment-drivers spec §3.3 / Phase 2).

driver_id is an ADDITIVE reference field on ConnSpec: old serialized specs
(no key) parse unchanged, and a spec that carries one round-trips it.
primary:"none" is the explicit-only rig mode: the orchestrator requests ONLY
the roles with explicit overrides — no primary-derived fill (so an Equipment
assignment surface can connect exactly what the user assigned, nothing more).
"""
import pytest

from astrodeck.devices.backend import ConnSpec, RigSpec
from astrodeck.devices.orchestrator import _requested_roles
from astrodeck.profiles import Profile, ProfileDevice


def test_connspec_driver_id_roundtrip_and_tolerant_parse():
    spec = ConnSpec(backend="native", driver_id="alpaca-ab12", role="camera")
    d = spec.to_dict()
    assert d["driver_id"] == "alpaca-ab12"
    again = ConnSpec.from_dict(d)
    assert again.driver_id == "alpaca-ab12"
    # old dict without the key parses with driver_id=None (additive back-compat)
    legacy = ConnSpec.from_dict({"backend": "sim"})
    assert legacy.driver_id is None


def test_primary_none_requests_only_explicit_roles():
    spec = RigSpec(primary="none", roles={
        "camera": ConnSpec(backend="native", role="camera"),
        "guider": ConnSpec(backend="phd2", role="guider"),
    })
    assert _requested_roles(spec) == {"camera", "guider"}


def test_primary_sim_still_requests_sim_roles():
    # regression: the old primary-derived fill is untouched for real primaries
    spec = RigSpec(primary="sim")
    assert "camera" in _requested_roles(spec)
    assert "safety" in _requested_roles(spec)


def test_profile_device_driver_id_flows_to_rigspec():
    p = Profile(name="t", primary_backend="none", devices=[
        ProfileDevice(role="camera", backend="native", driver_id="alpaca-ab12",
                      dev_type="camera", dev_num=0, name="ASI2600MM"),
    ])
    rs = p.to_rigspec()
    assert rs.roles["camera"].driver_id == "alpaca-ab12"
    # old profile rows (no driver_id key) still map with driver_id=None
    p2 = Profile(name="t2", devices=[
        ProfileDevice(role="camera", backend="alpaca", host="h", port=11111),
    ])
    assert p2.to_rigspec().roles["camera"].driver_id is None


def test_connect_rig_route_accepts_primary_none_and_driver_id(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        # primary "none" + one driver_id override must NOT 422 at the body/
        # validation layer. The referenced driver doesn't exist, so the role
        # comes back as a failed RoleResult ("driver removed") — Task 2 pins
        # that message; here we only pin "not a 4xx".
        r = c.post("/api/connect/rig", json={
            "primary": "none",
            "roles": {"camera": {"backend": "native", "role": "camera",
                                  "driver_id": "alpaca-gone"}},
        })
        assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `server/`): `.venv/Scripts/python -m pytest tests/test_driver_id_plumbing.py -q`
Expected: FAIL — `TypeError: ConnSpec.__init__() got an unexpected keyword argument 'driver_id'`.

- [ ] **Step 3: Implement**

(a) `server/astrodeck/devices/backend.py` — in the `ConnSpec` dataclass add the field after `role`:

```python
    role: str | None = None
    #: Reference to a CONFIGURED driver (AppConfig.drivers[].id). When set, the
    #: hub resolves it to concrete backend/host/port/extra at connect time
    #: (drivers.resolve_driver_ids); raw addressing above stays authoritative
    #: when it is None — additive back-compat (spec §3.3).
    driver_id: str | None = None
    extra: dict = field(default_factory=dict)
```

In `to_dict`, add `"driver_id": self.driver_id,` alongside the other fields. In `from_dict`, add `driver_id=d.get("driver_id"),` (tolerant — old dicts lack the key).

(b) `server/astrodeck/devices/orchestrator.py` — `_requested_roles` grows the explicit-only mode; put this at the top of the function with the WHY comment:

```python
    # primary "none": the Equipment surface's explicit-only rig (spec §4.1) —
    # ONLY assigned roles are requested; no primary-derived fill. A real
    # primary keeps the W1.6 union semantics below unchanged.
    if spec.primary in ("", "none"):
        return set(spec.roles)
```

(c) `server/astrodeck/api/app.py` — `ConnSpecBody` gains `driver_id: str | None = None` (after `role`). Then find the `connect_rig` route: wherever it builds `ConnSpec(...)` from each `ConnSpecBody`, pass `driver_id=body_spec.driver_id` through; wherever it validates `primary` (the W1.C reject rule — read the route body; it may call `get_backend(primary)` or check the registry), allow the literal `"none"` before that check. Preserve the existing override-validation behavior for everything else.

(d) `server/astrodeck/profiles.py` — `ProfileDevice` gains (after `name`):

```python
    # Phase 2: reference to a configured driver (AppConfig.drivers[].id). ""
    # (old rows) → the raw host/port above stays authoritative. Saved by the
    # Equipment surface; resolved by drivers.resolve_driver_ids at connect.
    driver_id: str = ""
```

In `to_rigspec`, add `driver_id=d.driver_id or None,` to the `ConnSpec(...)` construction. Also: `Profile.primary_backend` may now legitimately be `"none"` (saved by the Equipment surface) — check `_heal_sim_primary` and `_derived_primary` do not fight it (they don't touch a `"none"` value; verify and leave them).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_driver_id_plumbing.py -q`
Expected: `5 passed`.
NOTE: the route test can only pass once the route accepts `"none"`; if the route hard-rejects unknown primaries via `get_backend`, the orchestrator raising `KeyError("none")` must not happen either — `_requested_roles` short-circuits before `get_backend(spec.primary)` is reached for the requested-set, but `hub._apply_connect_result` receives `primary="none"` and derives `self.mode = "none"` today (harmless string). Task 2 replaces that derivation; for THIS task it only needs to not raise.

- [ ] **Step 5: Regression + commit**

Run: `.venv/Scripts/python -m pytest tests/test_connect_api.py tests/test_backend_registry.py tests/test_profiles.py tests/test_hub_connect_profile.py -q`
Expected: all pass.

```bash
git -C /c/Users/bear/astro add server/astrodeck/devices/backend.py server/astrodeck/devices/orchestrator.py server/astrodeck/api/app.py server/astrodeck/profiles.py server/tests/test_driver_id_plumbing.py
git -C /c/Users/bear/astro commit -m "feat(equipment): driver_id on ConnSpec/profile + primary-none explicit-only rigs (spec §3.3)"
```

---

### Task 2: driver-id resolution in the connect funnel (+ cache honesty)

**Files:**
- Modify: `server/astrodeck/drivers.py` (append after `describe_all`)
- Modify: `server/astrodeck/hub.py` (`_connect_rigspec_unlocked` ~line 445, `_apply_connect_result` ~line 483)
- Test: `server/tests/test_driver_id_resolution.py` (create)

**Interfaces:**
- Consumes: Task 1's `ConnSpec.driver_id`; Phase 1's `config_store.cfg().drivers`, `drivers.invalidate`.
- Produces (Task 3+ rely on):
  - `drivers.resolve_driver_ids(spec: RigSpec) -> tuple[RigSpec, dict[str, str], list[tuple[str, str]]]` — (resolved spec, role→driver_id, prefailed `[(role, error)]`). Missing driver → `"driver removed: <id>"`; disabled → `"driver disabled: <label>"`. Resolved ConnSpec: backend from the driver type map, host/port from the driver, `extra = {**driver.extra, **spec.extra}`, dev_type/dev_num/role preserved.
  - Hub behavior: prefailed roles appear in `results`/`backend_links` as `attempted=True, ok=False` with the error; any FAILED driver-backed role invalidates that driver's probe cache; `primary="none"` derives the legacy `mode` scalar via `_effective_primary`.
  - `Hub._effective_primary(result) -> str` — registry-name preference `nina > native > sim > (first session) > "sim"`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_driver_id_resolution.py`:

```python
"""drivers.resolve_driver_ids + hub wiring (spec §3.3, §5, cache-honesty §3.2).

The resolver is PURE over config: driver_id → concrete backend/host/port at
connect time. Missing/disabled drivers pre-fail their role (honest RoleResult,
never a 500/silent skip). A failed driver-backed connect drops that driver's
probe-cache row so /api/drivers can't report stale 'reachable'."""
import asyncio

import pytest

from astrodeck import drivers as drv
from astrodeck.config import ConfigStore
from astrodeck.devices.backend import ConnSpec, RigSpec


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(drv, "config_store", s)
    drv.invalidate()
    return s


def _spec(role, **kw):
    return RigSpec(primary="none", roles={role: ConnSpec(role=role, **kw)})


def test_resolves_backend_host_port_and_merges_extra(store):
    d = store.add_driver("alpaca", "192.168.1.50", 11111)
    store.update_driver(d.id, {"extra": {"from_driver": 1, "shared": "driver"}})
    spec = _spec("camera", backend="native", driver_id=d.id,
                 dev_type="camera", dev_num=2,
                 extra={"shared": "spec", "name": "ASI2600MM"})
    resolved, role_map, prefailed = drv.resolve_driver_ids(spec)
    assert prefailed == []
    assert role_map == {"camera": d.id}
    c = resolved.roles["camera"]
    assert (c.backend, c.host, c.port) == ("native", "192.168.1.50", 11111)
    assert (c.dev_type, c.dev_num) == ("camera", 2)
    # spec extra wins on collision; driver extra fills the rest
    assert c.extra == {"from_driver": 1, "shared": "spec", "name": "ASI2600MM"}


def test_driver_type_backend_mapping(store):
    for dtype, backend in (("nina", "nina"), ("alpaca", "native"), ("phd2", "phd2")):
        d = store.add_driver(dtype, "h")
        resolved, _, pre = drv.resolve_driver_ids(
            _spec("guider" if dtype == "phd2" else "camera",
                  backend="x", driver_id=d.id))
        assert pre == []
        role = "guider" if dtype == "phd2" else "camera"
        assert resolved.roles[role].backend == backend


def test_missing_and_disabled_drivers_prefail(store):
    resolved, role_map, pre = drv.resolve_driver_ids(
        _spec("camera", backend="native", driver_id="alpaca-gone"))
    assert resolved.roles == {} and role_map == {}
    assert pre == [("camera", "driver removed: alpaca-gone")]

    d = store.add_driver("nina", "h", label="My NINA")
    store.update_driver(d.id, {"enabled": False})
    _, _, pre = drv.resolve_driver_ids(_spec("camera", backend="nina", driver_id=d.id))
    assert pre == [("camera", "driver disabled: My NINA")]


def test_raw_specs_pass_through_untouched(store):
    spec = _spec("camera", backend="native", host="h", port=1, dev_type="camera")
    resolved, role_map, pre = drv.resolve_driver_ids(spec)
    assert pre == [] and role_map == {}
    assert resolved.roles["camera"] is spec.roles["camera"]


# ---------------------------------------------------------------- hub wiring

def test_hub_prefail_lands_in_results_and_cache_invalidated(store, monkeypatch):
    """End-to-end through hub.connect_rigspec: a sim camera + a removed-driver
    guider → camera connects, guider is a failed RoleResult with the honest
    error, and it appears in backend_links. A driver-backed role that FAILS
    invalidates that driver's probe cache row."""
    import astrodeck.hub as hub_mod
    h = hub_mod.hub

    async def run():
        spec = RigSpec(primary="none", roles={
            "camera": ConnSpec(backend="sim", role="camera"),
            "guider": ConnSpec(backend="phd2", role="guider",
                                driver_id="phd2-gone"),
        })
        return await h.connect_rigspec(spec)

    # hub reads the drivers module at call time — point ITS view of config at
    # the temp store too (hub calls drivers_mod.resolve_driver_ids which reads
    # drv.config_store, already monkeypatched by the fixture).
    out = asyncio.run(run())
    by_role = {r["role"]: r for r in out["results"]}
    assert by_role["camera"]["ok"] is True
    g = by_role["guider"]
    assert g["ok"] is False and g["attempted"] is True
    assert g["error"] == "driver removed: phd2-gone"
    link_roles = {l["role"] for l in out["backend_links"]}
    assert "guider" in link_roles
    # cleanup so later tests see no live rig
    asyncio.run(h.disconnect_all())


def test_failed_driver_backed_role_invalidates_cache(store):
    d = store.add_driver("phd2", "127.0.0.1", 4400)
    # seed a fake 'reachable' cache row, then fail a connect through that driver
    drv._CACHE[d.id] = (999999999.0, {"reachable": True, "error": None,
                                       "detail": None, "probed_at": 0.0,
                                       "offers": {"devices": [], "tasks": []}})
    import astrodeck.hub as hub_mod
    h = hub_mod.hub

    async def run():
        spec = RigSpec(primary="none", roles={
            "guider": ConnSpec(backend="phd2", role="guider", driver_id=d.id),
        })
        return await h.connect_rigspec(spec)

    out = asyncio.run(run())
    by_role = {r["role"]: r for r in out["results"]}
    assert by_role["guider"]["ok"] is False      # nothing listens on 4400 here
    assert d.id not in drv._CACHE                # cache-honesty: row dropped
    asyncio.run(h.disconnect_all())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_driver_id_resolution.py -q`
Expected: FAIL — `AttributeError: module 'astrodeck.drivers' has no attribute 'resolve_driver_ids'`.

- [ ] **Step 3: Implement the resolver** — append to `server/astrodeck/drivers.py`:

```python
# ------------------------------------------------------- driver_id resolution

#: Driver type -> backend registry name. The Alpaca lane's registry name is
#: "native" (devices/backends/native_backend.py); nina/phd2 match their type.
_DRIVER_TYPE_TO_BACKEND: dict[str, str] = {
    "nina": "nina", "alpaca": "native", "phd2": "phd2",
}


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
            backend=_DRIVER_TYPE_TO_BACKEND[d.type],
            host=d.host,
            port=d.port,
            dev_type=conn.dev_type,
            dev_num=conn.dev_num,
            role=conn.role or role,
            driver_id=did,
            extra={**(d.extra or {}), **(conn.extra or {})},
        )
    return RigSpec(primary=spec.primary, roles=roles), role_to_driver, prefailed
```

- [ ] **Step 4: Wire the hub** — in `server/astrodeck/hub.py`:

(a) `_connect_rigspec_unlocked` (~line 445): after `_harness()` and before `connect_profile(spec)`:

```python
        # Phase 2 (spec §3.3): resolve driver_id references to concrete
        # addressing. Missing/disabled drivers pre-fail their role HONESTLY —
        # they surface as attempted+failed RoleResults below, never vanish.
        from . import drivers as drivers_mod
        spec, role_to_driver, prefailed = drivers_mod.resolve_driver_ids(spec)
        result = await connect_profile(spec)
        if prefailed:
            from .devices.orchestrator import RoleResult
            keep = [rr for rr in result.results
                    if rr.role not in {r for r, _ in prefailed}]
            keep.extend(RoleResult(role, ok=False, error=err, attempted=True)
                        for role, err in prefailed)
            result.results = keep
        # Cache honesty (spec §3.2): a driver-backed role that FAILED to
        # connect drops that driver's probe-cache row immediately, so the
        # Equipment surface's next /api/drivers read reflects reality instead
        # of a up-to-15s-stale 'reachable'.
        failed = {rr.role for rr in result.results if rr.attempted and not rr.ok}
        for role, did in role_to_driver.items():
            if role in failed:
                drivers_mod.invalidate(did)
```

(replacing the existing bare `result = await connect_profile(spec)` line).

(b) `_apply_connect_result` (~line 483): `primary` may now be `"none"`. At the TOP of the method add:

```python
        # primary "none" (explicit-only rig, spec §4.1): there is no declared
        # primary to derive mode/handles from — pick the strongest connected
        # backend. Preference nina > native > sim: the NINA handle powers the
        # heartbeat/event-stream wiring below, a native session means a real
        # (alpaca-mode) rig, and sim is the weakest signal. `mode` stays the
        # legacy scalar; per-device truth lives in backend_links.
        if primary in ("", "none"):
            primary = self._effective_primary(result)
```

and add the helper next to `_primary_session`:

```python
    @staticmethod
    def _effective_primary(result: "ConnectResult") -> str:
        """Derive a primary label for a primary-less rig from its OPEN
        sessions, preference nina > native > sim, else the first session's
        backend, else "sim" (an empty rig behaves like the old default)."""
        names = [k[0] for k in result.sessions]
        for pref in ("nina", "native", "sim"):
            if pref in names:
                return pref
        return names[0] if names else "sim"
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_driver_id_resolution.py tests/test_driver_id_plumbing.py -q`
Expected: all pass (`test_hub_prefail...` exercises the full funnel; if the hub test flakes on the singleton's event-loop locks, the repo's autouse `_reset_hub_singleton_locks` fixture already resets them — do not add sleeps).

- [ ] **Step 6: Full suite + commit**

Run: `.venv/Scripts/python -m pytest -q` → green.

```bash
git -C /c/Users/bear/astro add server/astrodeck/drivers.py server/astrodeck/hub.py server/tests/test_driver_id_resolution.py
git -C /c/Users/bear/astro commit -m "feat(equipment): driver_id resolution in the connect funnel + probe-cache honesty (spec §3.3/§5)"
```

---

### Task 3: UI pure lane — assignment model + RigSpec compiler

**Files:**
- Modify: `ui/src/types.ts` (`ConnSpec` interface ~line 865: add `driver_id?: string | null;` after `role`)
- Create: `ui/src/lib/equipment.ts`
- Test: `ui/src/lib/__tests__/equipment.test.ts` (create; inline-assert harness via `npx tsx`, same shape as `drivers.test.ts` including the globalThis process-exit cast)

**Interfaces:**
- Consumes: `DriverInfo`, `DriverDeviceOffer`, `DriversResponse`, `RigSpec`, `ConnSpec` from `../types`.
- Produces (Task 4 relies on — exact exports):

```ts
export interface Assignment {
  driverId: string;
  devType?: string;   // alpaca device selector (from the offer)
  devNum?: number;
  name?: string;      // display name (from the offer)
}
export type AssignmentMap = Record<string, Assignment | null>;
export type SlotState = "unassigned" | "ok" | "driver-removed" | "driver-unreachable" | "driver-disabled";
export const DRIVER_TYPE_TO_BACKEND: Record<string, string>;
export function eligibleDrivers(role: string, drivers: DriverInfo[]): DriverInfo[];
export function deviceChoices(role: string, driver: DriverInfo): DriverDeviceOffer[];
export function slotState(a: Assignment | null, drivers: DriverInfo[]): SlotState;
export function buildRigSpec(assignments: AssignmentMap): RigSpec;
export function hasRealMotion(assignments: AssignmentMap, drivers: DriverInfo[]): boolean;
export function loadAssignments(): AssignmentMap;      // localStorage, {} on any error
export function saveAssignments(a: AssignmentMap): void;
```

- [ ] **Step 1: Create `ui/src/lib/equipment.ts`**

```ts
// equipment.ts — the pure assignment model behind the Equipment tab (spec
// §4.1). No React, no fetch: the ONE RULE (a driver is offered on a row only
// if enabled + reachable + actually offering that role), sticky-assignment
// state, the AssignmentMap → RigSpec compiler (primary "none": explicit-only
// rigs), and localStorage persistence. Kept pure so the tsx harness tests it
// without a DOM.
import type {
  ConnSpec,
  DriverDeviceOffer,
  DriverInfo,
  RigSpec,
} from "../types";

export interface Assignment {
  driverId: string;
  devType?: string; // alpaca device selector (from the picked offer)
  devNum?: number;
  name?: string; // display name from the offer (folded into ConnSpec.extra)
}

// role -> assignment (null/absent = unassigned). Keyed by the server-fed role
// list, so a future role (rotator) needs zero changes here.
export type AssignmentMap = Record<string, Assignment | null>;

export type SlotState =
  | "unassigned"
  | "ok"
  | "driver-removed"
  | "driver-unreachable"
  | "driver-disabled";

// Mirrors server drivers._DRIVER_TYPE_TO_BACKEND — the Alpaca lane's registry
// name is "native"; sim is its own backend for the one-tap sim rig.
export const DRIVER_TYPE_TO_BACKEND: Record<string, string> = {
  nina: "nina",
  alpaca: "native",
  phd2: "phd2",
  sim: "sim",
};

/** THE ONE RULE (spec §2): a driver appears as an option on a role's row only
 *  when it is enabled, reachable, and its probe actually offers that role. */
export function eligibleDrivers(role: string, drivers: DriverInfo[]): DriverInfo[] {
  return drivers.filter(
    (d) =>
      d.enabled &&
      d.status.reachable &&
      d.offers.devices.some((o) => o.role === role),
  );
}

/** The concrete enumerated devices a driver offers for a role (alpaca may
 *  have several; nina/phd2/sim at most one). */
export function deviceChoices(role: string, driver: DriverInfo): DriverDeviceOffer[] {
  return driver.offers.devices.filter((o) => o.role === role);
}

/** Sticky-assignment state (spec §5): the user's choice persists through a
 *  driver outage and re-lights when it returns; a deleted driver reads
 *  "driver-removed" and never silently reconnects elsewhere. */
export function slotState(a: Assignment | null, drivers: DriverInfo[]): SlotState {
  if (!a) return "unassigned";
  const d = drivers.find((x) => x.id === a.driverId);
  if (!d) return "driver-removed";
  if (!d.enabled) return "driver-disabled";
  if (!d.status.reachable) return "driver-unreachable";
  return "ok";
}

/** Compile assignments → RigSpec. primary "none" = explicit-only: the server
 *  requests exactly the assigned roles, nothing primary-derived. The sim
 *  built-in compiles to backend "sim" directly (no driver_id — sim is
 *  implicit); configured drivers ride their driver_id and let the SERVER
 *  resolve host/port at connect time (never client-synthesized addressing). */
export function buildRigSpec(assignments: AssignmentMap): RigSpec {
  const roles: Record<string, ConnSpec> = {};
  for (const [role, a] of Object.entries(assignments)) {
    if (!a) continue;
    const extra: Record<string, unknown> = {};
    if (a.name) extra.name = a.name;
    if (a.driverId === "sim") {
      roles[role] = { backend: "sim", role, ...(a.name ? { extra } : {}) };
      continue;
    }
    const spec: ConnSpec = {
      backend: DRIVER_TYPE_TO_BACKEND[a.driverId.split("-")[0]] ?? "native",
      role,
      driver_id: a.driverId,
    };
    if (a.devType) spec.dev_type = a.devType;
    if (a.devNum !== undefined) spec.dev_num = a.devNum;
    if (a.name) spec.extra = extra;
    roles[role] = spec;
  }
  return { primary: "none", roles };
}

/** Hold-to-confirm gate: true when a real (non-sim) driver fills a motion
 *  role — mirrors BackendPicker's MOTION_ROLES rule. */
export function hasRealMotion(assignments: AssignmentMap, drivers: DriverInfo[]): boolean {
  return ["telescope", "focuser"].some((role) => {
    const a = assignments[role];
    if (!a || a.driverId === "sim") return false;
    return slotState(a, drivers) !== "driver-removed";
  });
}

// ------------------------------------------------------------- persistence
// Pre-profile stickiness across reloads. Profiles are the durable store; this
// is just "don't lose my dropdowns on F5". Any parse error degrades to {}.
const LS_KEY = "astrodeck.equipment.assignments.v1";

export function loadAssignments(): AssignmentMap {
  try {
    const raw = globalThis.localStorage?.getItem(LS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as AssignmentMap) : {};
  } catch {
    return {};
  }
}

export function saveAssignments(a: AssignmentMap): void {
  try {
    globalThis.localStorage?.setItem(LS_KEY, JSON.stringify(a));
  } catch {
    // storage full / privacy mode — stickiness is best-effort, never fatal
  }
}
```

NOTE on `buildRigSpec`'s backend derivation: the driver id prefix (`nina-`/`alpaca-`/`phd2-`) IS the driver type by construction (`add_driver` mints `<type>-<4hex>`), and the server re-derives the backend authoritatively in `resolve_driver_ids` anyway — the client value is a placeholder that keeps `ConnSpec.backend` non-empty. Keep the comment in the code saying exactly that.

- [ ] **Step 2: Add `driver_id` to the ConnSpec interface** — `ui/src/types.ts` ~line 871, after `role?: string | null;`:

```ts
  driver_id?: string | null; // reference to AppConfig.drivers[].id (spec §3.3)
```

- [ ] **Step 3: Write the tsx test** — create `ui/src/lib/__tests__/equipment.test.ts` (harness identical in shape to `drivers.test.ts`: local `test`/`eq` helpers, counters, summary line, globalThis process-exit cast):

```ts
// equipment.test.ts — pure-logic tests for the Equipment assignment lane
// (spec §4.1). Inline-assert harness (no vitest); runs via `npx tsx`.
import {
  buildRigSpec,
  deviceChoices,
  eligibleDrivers,
  hasRealMotion,
  slotState,
  type AssignmentMap,
} from "../equipment";
import type { DriverInfo } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const drv = (over: Partial<DriverInfo>): DriverInfo => ({
  id: "alpaca-ab12", type: "alpaca", label: "Alpaca", enabled: true,
  implicit: false,
  status: { reachable: true, error: null, detail: null, probed_at: 0 },
  offers: { devices: [], tasks: [] },
  ...over,
});

const alpaca = drv({
  offers: {
    devices: [
      { role: "camera", name: "ASI2600MM", dev_type: "camera", dev_num: 0 },
      { role: "camera", name: "ASI220MM", dev_type: "camera", dev_num: 1 },
      { role: "focuser", name: "EAF", dev_type: "focuser", dev_num: 0 },
    ],
    tasks: [],
  },
});
const sim = drv({
  id: "sim", type: "sim", label: "Simulator", implicit: true,
  offers: {
    devices: [
      { role: "camera", name: "Simulated camera" },
      { role: "telescope", name: "Simulated telescope" },
    ],
    tasks: ["autofocus", "polar_align"],
  },
});

test("eligibleDrivers applies the one rule", () => {
  eq(eligibleDrivers("camera", [alpaca, sim]).length, 2);
  eq(eligibleDrivers("telescope", [alpaca, sim]).map((d) => d.id).join(","), "sim");
  const down = { ...alpaca, status: { ...alpaca.status, reachable: false } };
  eq(eligibleDrivers("camera", [down, sim]).map((d) => d.id).join(","), "sim");
  const off = { ...alpaca, enabled: false };
  eq(eligibleDrivers("camera", [off, sim]).map((d) => d.id).join(","), "sim");
});

test("deviceChoices filters the driver's offers by role", () => {
  eq(deviceChoices("camera", alpaca).length, 2);
  eq(deviceChoices("focuser", alpaca)[0].name, "EAF");
  eq(deviceChoices("guider", alpaca).length, 0);
});

test("slotState is sticky and honest", () => {
  const a = { driverId: "alpaca-ab12" };
  eq(slotState(null, [alpaca]), "unassigned");
  eq(slotState(a, [alpaca]), "ok");
  eq(slotState(a, []), "driver-removed");
  eq(slotState(a, [{ ...alpaca, enabled: false }]), "driver-disabled");
  eq(slotState(a, [{ ...alpaca, status: { ...alpaca.status, reachable: false } }]),
     "driver-unreachable");
});

test("buildRigSpec compiles explicit-only rigs with driver_id", () => {
  const m: AssignmentMap = {
    camera: { driverId: "alpaca-ab12", devType: "camera", devNum: 1, name: "ASI220MM" },
    telescope: { driverId: "sim", name: "Simulated telescope" },
    guider: null,
  };
  const rs = buildRigSpec(m);
  eq(rs.primary, "none");
  eq(Object.keys(rs.roles).sort().join(","), "camera,telescope");
  eq(rs.roles.camera.driver_id, "alpaca-ab12");
  eq(rs.roles.camera.backend, "native");
  eq(rs.roles.camera.dev_num, 1);
  eq((rs.roles.camera.extra as { name?: string }).name, "ASI220MM");
  eq(rs.roles.telescope.backend, "sim");
  eq(rs.roles.telescope.driver_id, undefined);
});

test("hasRealMotion gates on non-sim motion roles", () => {
  eq(hasRealMotion({ telescope: { driverId: "sim" } }, [sim]), false);
  eq(hasRealMotion({ focuser: { driverId: "alpaca-ab12" } }, [alpaca]), true);
  eq(hasRealMotion({ camera: { driverId: "alpaca-ab12" } }, [alpaca]), false);
  // a removed driver can't drive motion — no hold-confirm needed
  eq(hasRealMotion({ telescope: { driverId: "alpaca-gone" } }, [alpaca]), false);
});

console.log(`equipment.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 4: Run test + typecheck**

Run: `cd /c/Users/bear/astro/ui && npx tsx src/lib/__tests__/equipment.test.ts`
Expected: `equipment.test.ts: 5 passed, 0 failed`
Run: `npm --prefix /c/Users/bear/astro/ui run build` → clean.

- [ ] **Step 5: Commit**

```bash
git -C /c/Users/bear/astro add ui/src/types.ts ui/src/lib/equipment.ts ui/src/lib/__tests__/equipment.test.ts
git -C /c/Users/bear/astro commit -m "feat(ui/equipment): pure assignment model + RigSpec compiler (spec §4.1)"
```

---

### Task 4: `EquipmentView` — the devices surface

**Files:**
- Create: `ui/src/views/EquipmentView.tsx`
- Test: build/typecheck (no DOM harness in this repo); behavior lives in Task 3's pure lib

**Interfaces:**
- Consumes: Task 3's `equipment.ts` exports; `listDrivers`, `probeDriver`, `connectRig`, `listProfiles`, `getProfile`, `saveProfile`, `activateProfile` from `../api/backends`; `api` from `../api`; store (`useStore` → `status`, `showToast`); `useCanConfigBackend`; `BackendLinkGrid` (dense); `confirmDialog`; house `Panel/Field/Led/EmptyState/InfoDot` + `Icon`; `ROLE_LABEL` from `../components/settings/backendMeta`.
- Produces: `export default function EquipmentView(): JSX.Element` (mounted by Task 5 under view id `"connect"`).

**Before coding:** read `BackendPicker.tsx` (idioms: hold-confirm call shape, toast handling, class names) and `DriversPanel.tsx` (reload pattern) — this view must read as their sibling. Verify `Profile`/`ProfileRow` field names in `ui/src/types.ts` before writing the profile bar (the plan's code below uses `p.id`, `p.name`, `p.active`, and full-Profile `devices` rows `{role, backend, driver_id, dev_type, dev_num, name}` + `primary_backend` — adjust to the real interface if it differs and note it).

- [ ] **Step 1: Create `ui/src/views/EquipmentView.tsx`**

```tsx
// EquipmentView.tsx — the unified per-device Equipment surface (spec §4.1),
// replacing the mode-centric ConnectView under the same "connect" view id.
// One uniform row grammar: for each server-fed role, pick WHO drives it from
// the drivers that actually offer it (the one rule), then Connect Rig compiles
// assignments → RigSpec (primary "none", driver_id ConnSpecs) → the existing
// /api/connect/rig. Live truth (LEDs + names) rides backend_links + status;
// failure honesty: sticky assignments, per-row RoleResult errors, an
// unreachable-driver banner, and a /api/drivers refetch after a failed
// connect (probe-cache honesty, spec §3.2/§5).
import { useEffect, useMemo, useState, type JSX } from "react";
import type {
  ConnectRigResult,
  DriverInfo,
  DriversResponse,
  Profile,
  ProfileRow,
  RoleResult,
} from "../types";
import { api, ApiError } from "../api";
import {
  activateProfile,
  connectRig,
  getProfile,
  listDrivers,
  listProfiles,
  saveProfile,
} from "../api/backends";
import { useStore } from "../store";
import { useCanConfigBackend } from "../lib/caps";
import {
  buildRigSpec,
  deviceChoices,
  eligibleDrivers,
  hasRealMotion,
  loadAssignments,
  saveAssignments,
  slotState,
  type Assignment,
  type AssignmentMap,
} from "../lib/equipment";
import { confirmDialog } from "../components/ConfirmDialog";
import BackendLinkGrid from "../components/settings/BackendLinkGrid";
import { ROLE_LABEL } from "../components/settings/backendMeta";
import { EmptyState, Field, InfoDot, Led, Panel } from "../components/ui";
import { Icon } from "../components/icons";

const SLOT_WORD: Record<string, { word: string; tone: string }> = {
  unassigned: { word: "UNASSIGNED", tone: "text-faint" },
  ok: { word: "ASSIGNED", tone: "text-accent" },
  "driver-removed": { word: "DRIVER REMOVED", tone: "text-bad" },
  "driver-unreachable": { word: "DRIVER UNREACHABLE", tone: "text-warn" },
  "driver-disabled": { word: "DRIVER DISABLED", tone: "text-warn" },
};

export default function EquipmentView(): JSX.Element {
  const status = useStore((s) => s.status);
  const showToast = useStore((s) => s.showToast);
  const canConfig = useCanConfigBackend();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<AssignmentMap>(() => loadAssignments());
  const [results, setResults] = useState<Record<string, RoleResult>>({});
  const [busy, setBusy] = useState(false);
  const [profiles, setProfiles] = useState<ProfileRow[] | null>(null);
  const [profileName, setProfileName] = useState("");

  const reloadDrivers = async () => {
    try {
      setData(await listDrivers());
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
    }
  };
  const reloadProfiles = () =>
    listProfiles().then(setProfiles).catch(() => setProfiles(null));
  useEffect(() => {
    void reloadDrivers();
    void reloadProfiles();
  }, []);

  const drivers = useMemo(() => data?.drivers ?? [], [data]);
  const roles = data?.roles ?? [];
  const links = status?.backend_links ?? [];
  const linkByRole = useMemo(
    () => Object.fromEntries(links.map((l) => [l.role, l])),
    [links],
  );

  const setAssignment = (role: string, a: Assignment | null) => {
    setAssignments((m) => {
      const next = { ...m, [role]: a };
      saveAssignments(next);
      return next;
    });
  };

  // Any configured driver that is enabled but unreachable → one visible
  // banner (spec §5) instead of dead dropdown entries.
  const downDrivers = drivers.filter(
    (d) => !d.implicit && d.enabled && !d.status.reachable,
  );

  const assignedCount = roles.filter((r) => assignments[r]).length;

  const doConnect = async () => {
    if (hasRealMotion(assignments, drivers)) {
      const ok = await confirmDialog({
        title: "Connect this rig?",
        body: "This rig includes a real mount or focuser. Connecting will command the hardware to attach and may move it. Hold to confirm.",
        mode: "hold",
        tone: "danger",
        confirmLabel: "Connect rig",
      });
      if (!ok) return;
    }
    setBusy(true);
    setResults({});
    try {
      const res: ConnectRigResult = await connectRig(buildRigSpec(assignments));
      const map: Record<string, RoleResult> = {};
      for (const r of res.results) map[r.role] = r;
      setResults(map);
      const okCount = res.results.filter((r) => r.ok).length;
      const attempted = res.results.filter((r) => r.attempted).length;
      if (okCount === 0 && attempted > 0) {
        showToast("error", "Rig connect attempted but no roles came up — see per-row errors");
        void reloadDrivers(); // cache honesty: reflect reality immediately
      } else {
        showToast("success", `Rig connected — ${okCount}/${attempted} roles up`);
        if (okCount < attempted) void reloadDrivers();
      }
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 422
            ? `Invalid rig: ${e.message}`
            : e.message
          : e instanceof Error
            ? e.message
            : "connect failed";
      showToast("error", msg);
      void reloadDrivers();
    } finally {
      setBusy(false);
    }
  };

  const doDisconnect = () =>
    void (async () => {
      setBusy(true);
      try {
        await api.post("/api/disconnect");
        setResults({});
        showToast("success", "Disconnected");
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "disconnect failed");
      } finally {
        setBusy(false);
      }
    })();

  const doSimRig = () =>
    void (async () => {
      setBusy(true);
      try {
        await api.post("/api/connect/sim");
        showToast("success", "Simulator rig connected");
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "sim connect failed");
      } finally {
        setBusy(false);
      }
    })();

  // ------------------------------------------------------------- profiles
  const doSaveProfile = () =>
    void (async () => {
      const name = profileName.trim();
      if (!name) return;
      const devices = Object.entries(assignments)
        .filter(([, a]) => a && a.driverId !== "sim")
        .map(([role, a]) => ({
          role,
          backend: "native", // placeholder; server resolves via driver_id
          driver_id: (a as Assignment).driverId,
          dev_type: (a as Assignment).devType ?? "",
          dev_num: (a as Assignment).devNum ?? 0,
          name: (a as Assignment).name ?? "",
          host: "",
          port: 0,
          extra: {},
        }));
      try {
        await saveProfile({ name, primary_backend: "none", devices } as unknown as Profile);
        setProfileName("");
        void reloadProfiles();
        showToast("success", `Profile "${name}" saved`);
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "profile save failed");
      }
    })();

  const doLoadProfile = (id: string) =>
    void (async () => {
      try {
        const p = await getProfile(id);
        const next: AssignmentMap = {};
        for (const d of p.devices ?? []) {
          if (!d.driver_id) continue; // raw-addressing rows: connect via Activate
          next[d.role] = {
            driverId: d.driver_id,
            devType: d.dev_type || undefined,
            devNum: d.dev_num,
            name: d.name || undefined,
          };
        }
        setAssignments(next);
        saveAssignments(next);
        showToast("success", `Loaded assignments from "${p.name}" — review, then Connect`);
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "profile load failed");
      }
    })();

  // ---------------------------------------------------------------- render
  if (loadErr) {
    return (
      <Panel title="Equipment">
        <EmptyState icon="alert" title="Couldn't load drivers" hint={loadErr} />
      </Panel>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_minmax(260px,340px)]">
      <div className="flex flex-col gap-4 min-w-0">
        <Panel
          title="Devices"
          right={
            <InfoDot
              label="About device assignment"
              content="For each device slot, pick which configured driver runs it — only drivers that actually offer that device are listed. Manage drivers under Settings → Backend Drivers."
            />
          }
        >
          {downDrivers.length > 0 && (
            <p className="text-[11px] text-warn mb-3 inline-flex items-center gap-1.5">
              <Icon name="alert" size={12} />
              {downDrivers.map((d) => d.label).join(", ")}{" "}
              {downDrivers.length === 1 ? "is" : "are"} configured but unreachable —
              assignments stay put and re-light when it returns.
            </p>
          )}
          <div className="flex flex-col gap-2.5">
            {roles.map((role) => (
              <RoleSlot
                key={role}
                role={role}
                drivers={drivers}
                assignment={assignments[role] ?? null}
                link={linkByRole[role]}
                result={results[role]}
                status={status}
                disabled={!canConfig || busy}
                onAssign={(a) => setAssignment(role, a)}
              />
            ))}
            {roles.length === 0 && (
              <p className="text-dim text-xs">Loading device slots…</p>
            )}
          </div>
        </Panel>

        <Panel title="Rig Actions">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn btn-accent min-h-11"
              disabled={busy || !canConfig || assignedCount === 0}
              onClick={() => void doConnect()}
            >
              <Icon name="link" size={14} className="inline -mt-0.5 mr-1.5" />
              {busy ? "Working…" : `Connect Rig (${assignedCount})`}
            </button>
            <button type="button" className="btn" disabled={busy || !canConfig} onClick={doSimRig}>
              ▶ Simulator rig
            </button>
            <button type="button" className="btn btn-danger" disabled={busy || !canConfig} onClick={doDisconnect}>
              Disconnect
            </button>
            {hasRealMotion(assignments, drivers) && (
              <span className="text-[11px] text-warn inline-flex items-center gap-1">
                <Icon name="alert" size={12} /> Drives a real mount/focuser — hold to confirm.
              </span>
            )}
          </div>
          {!canConfig && (
            <p className="text-[11px] text-dim mt-2 inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — connecting equipment needs operator or admin access.
            </p>
          )}
        </Panel>

        <Panel title="Profiles" right={<span className="label text-dim">assignments, saved</span>}>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Save current assignments as">
              <input
                className="field !py-1 w-[180px]"
                placeholder="Backyard rig"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
              />
            </Field>
            <button
              type="button"
              className="btn !py-1.5"
              disabled={!canConfig || !profileName.trim() || assignedCount === 0}
              onClick={doSaveProfile}
            >
              Save
            </button>
          </div>
          {profiles && profiles.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              {profiles.map((p) => (
                <div key={p.id} className="flex items-center gap-2 border border-line bg-bg/60 px-3 py-2">
                  <span className="text-xs text-ink truncate flex-1">
                    {p.name}
                    {p.active && <span className="label text-accent ml-2">ACTIVE</span>}
                  </span>
                  <button type="button" className="btn !py-1 !px-2 text-[10px]" disabled={busy} onClick={() => doLoadProfile(p.id)}>
                    Load
                  </button>
                  <button
                    type="button"
                    className="btn !py-1 !px-2 text-[10px]"
                    disabled={busy || !canConfig}
                    onClick={() =>
                      void activateProfile(p.id).then(
                        () => showToast("success", "Profile activating — watch the link grid"),
                        (e: unknown) => showToast("error", e instanceof Error ? e.message : "activate failed"),
                      )
                    }
                  >
                    Activate
                  </button>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {/* live per-role truth: the boot-LED tri-state grid (kept per spec §4.3) */}
      <Panel title="Link Status">
        <BackendLinkGrid links={links} dense />
      </Panel>
    </div>
  );
}

// One device slot row: assignment select → (optional) device select → slot
// state word → live LED + connected name → inline RoleResult error. The
// guider row nests the read-only guide-camera line (spec review finding 3).
function RoleSlot({
  role,
  drivers,
  assignment,
  link,
  result,
  status,
  disabled,
  onAssign,
}: {
  role: string;
  drivers: DriverInfo[];
  assignment: Assignment | null;
  link?: { connected?: boolean; error?: string | null };
  result?: RoleResult;
  status: ReturnType<typeof useStore.getState>["status"];
  disabled: boolean;
  onAssign: (a: Assignment | null) => void;
}): JSX.Element {
  const eligible = eligibleDrivers(role, drivers);
  const state = slotState(assignment, drivers);
  const meta = SLOT_WORD[state];
  const chosen = assignment ? drivers.find((d) => d.id === assignment.driverId) : undefined;
  const choices = chosen ? deviceChoices(role, chosen) : [];
  const connectedName = status?.connected?.[role]?.connected
    ? status.connected[role].name
    : null;
  const led = connectedName ? "on" : result && result.attempted && !result.ok ? "bad" : "off";

  const pickDriver = (driverId: string) => {
    if (!driverId) return onAssign(null);
    const d = drivers.find((x) => x.id === driverId);
    const offers = d ? deviceChoices(role, d) : [];
    const first = offers[0];
    onAssign({
      driverId,
      devType: first?.dev_type,
      devNum: first?.dev_num,
      name: first?.name,
    });
  };

  return (
    <div className="border border-line bg-bg/60 px-3 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <Led state={led} label={`${ROLE_LABEL[role] ?? role} link`} />
        <span className="label w-28 shrink-0">{ROLE_LABEL[role] ?? role}</span>
        <select
          className="field !py-1 max-w-[200px]"
          value={assignment?.driverId ?? ""}
          disabled={disabled}
          onChange={(e) => pickDriver(e.target.value)}
          aria-label={`${ROLE_LABEL[role] ?? role} driver`}
        >
          <option value="">— unassigned —</option>
          {eligible.map((d) => (
            <option key={d.id} value={d.id}>
              {d.label}
            </option>
          ))}
          {/* sticky: a chosen driver that is no longer eligible stays listed
              (greyed by the state word) instead of silently vanishing */}
          {assignment && !eligible.some((d) => d.id === assignment.driverId) && (
            <option value={assignment.driverId}>
              {chosen?.label ?? assignment.driverId}
            </option>
          )}
        </select>
        {choices.length > 1 && (
          <select
            className="field !py-1 max-w-[180px]"
            value={`${assignment?.devType ?? ""}#${assignment?.devNum ?? ""}`}
            disabled={disabled}
            onChange={(e) => {
              const [dt, dn] = e.target.value.split("#");
              const offer = choices.find(
                (o) => o.dev_type === dt && String(o.dev_num) === dn,
              );
              if (offer && assignment)
                onAssign({
                  ...assignment,
                  devType: offer.dev_type,
                  devNum: offer.dev_num,
                  name: offer.name,
                });
            }}
            aria-label={`${ROLE_LABEL[role] ?? role} device`}
          >
            {choices.map((o) => (
              <option key={`${o.dev_type}#${o.dev_num}`} value={`${o.dev_type}#${o.dev_num}`}>
                {o.name} #{o.dev_num}
              </option>
            ))}
          </select>
        )}
        <span className={`mono text-[10px] tracking-wider ${meta.tone}`}>{meta.word}</span>
        <div className="flex-1" />
        <span className="mono text-xs truncate max-w-[220px] text-ink/90">
          {connectedName ?? <span className="text-dim">—</span>}
        </span>
      </div>
      {/* honest per-row failure: connect result error, else live link error */}
      {result && result.attempted && !result.ok && result.error && (
        <p className="mt-1.5 pl-[2.6rem] text-[10px] text-bad">{result.error}</p>
      )}
      {role === "guider" && (
        <div className="mt-2 pl-[2.6rem] flex items-center gap-2 text-[11px]">
          <span className="label">guide cam</span>
          <span className="mono text-dim truncate">
            {(() => {
              const gc = status?.guide_camera;
              const guider = status?.guider;
              const on = !!gc?.connected || !!guider;
              return on ? (gc?.name ?? guider?.name ?? "guide camera") : "— not connected —";
            })()}
          </span>
        </div>
      )}
    </div>
  );
}
```

Adapt only where verified reality differs (e.g. `status.connected[role]` shape, `ProfileRow.active`, `confirmDialog` options — read the real types/components first and note every adaptation in the report).

- [ ] **Step 2: Typecheck + build**

Run: `npm --prefix /c/Users/bear/astro/ui run build`
Expected: clean (`EquipmentView` is not yet mounted — that's Task 5 — but it must compile).

- [ ] **Step 3: Commit**

```bash
git -C /c/Users/bear/astro add ui/src/views/EquipmentView.tsx
git -C /c/Users/bear/astro commit -m "feat(ui/equipment): EquipmentView — per-device assignment surface (spec §4.1)"
```

---

### Task 5: Swap the tab, retire the old surfaces, verify live

**Files:**
- Modify: `ui/src/App.tsx` (NAV entry ~line 40: label "Rig"→"Equipment"; import + `VIEWS.connect` → `EquipmentView`)
- Modify: `ui/src/components/BottomNav.tsx` (~line 24: label "Rig"→"Equipment")
- Modify: `ui/src/components/settings/SettingsView.tsx` (remove the `BackendPicker` import + render; `DriversPanel` and `BackendLinkGrid` stay)
- Delete: `ui/src/views/ConnectView.tsx`, `ui/src/components/settings/BackendPicker.tsx`
- Test: build + grep sweep + live smoke

- [ ] **Step 1: Swap and retire**

In `App.tsx`: replace `import ConnectView from "./views/ConnectView";` with `import EquipmentView from "./views/EquipmentView";`; NAV entry becomes `{ id: "connect", label: "Equipment", icon: "rig" }`; `VIEWS` entry becomes `connect: EquipmentView,`. In `BottomNav.tsx` change the matching label. In `SettingsView.tsx` remove the BackendPicker import and its render (keep the layout around DriversPanel intact). Then:

```bash
git -C /c/Users/bear/astro rm ui/src/views/ConnectView.tsx ui/src/components/settings/BackendPicker.tsx
```

- [ ] **Step 2: Dangling-reference sweep**

Run: `cd /c/Users/bear/astro/ui && grep -rn "ConnectView\|BackendPicker" src/ --include="*.ts*"`
Expected: NO matches (the `backends.test.ts` inline tests import from `backendMeta`/`BackendLinkGrid` only — they survive). If `SettingsView`'s viewer fallback branch referenced BackendPicker, replace that branch's content with the read-only `BackendLinkGrid` it already renders.

- [ ] **Step 3: Build + both tsx test files + full server suite**

Run: `npm --prefix /c/Users/bear/astro/ui run build` → clean.
Run: `cd /c/Users/bear/astro/ui && npx tsx src/lib/__tests__/equipment.test.ts && npx tsx src/lib/__tests__/backends.test.ts && npx tsx src/lib/__tests__/drivers.test.ts` → all pass.
Run (from `server/`): `.venv/Scripts/python -m pytest -q` → green.

- [ ] **Step 4: Live smoke (grounded)**

Start the server on a fresh port: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m astrodeck --port 8802` (background). Then:
- `POST /api/config/drivers {"type":"phd2","host":"127.0.0.1"}` → note the id.
- `POST /api/connect/rig` with `{"primary":"none","roles":{"camera":{"backend":"sim","role":"camera"},"guider":{"backend":"phd2","role":"guider","driver_id":"<the id>"}}}` → expect camera ok, guider failed ("connection failed"-class error), 200.
- `GET /api/drivers` → the phd2 row must show `reachable: false` (cache was invalidated by the failed connect — the §3.2 rule, observable end-to-end).
- `POST /api/connect/rig` with `{"primary":"none","roles":{"camera":{"backend":"sim","role":"camera"},"guider":{"backend":"phd2","role":"guider","driver_id":"phd2-gone"}}}` → guider error `"driver removed: phd2-gone"`.
- Served `index.html` bundle contains `"Equipment"` and does NOT contain `"NINA Bridge — Transition Mode"` (ConnectView's banner copy — proves the old view is gone from the bundle).
- Kill the 8802 server. Record every command + output in the report.

- [ ] **Step 5: Commit**

```bash
git -C /c/Users/bear/astro add -A ui/src
git -C /c/Users/bear/astro commit -m "feat(ui/equipment): Equipment tab replaces ConnectView; BackendPicker retired (spec §4.3)"
```

---

## Phase-2 exit criteria (grounded, not assumed)

- Full server suite green locally AND CI green on push (`gh run watch --exit-status` — verify).
- `primary:"none"` + `driver_id` rig connects end-to-end; removed driver → honest per-role error in `results` AND `backend_links`; failed driver-backed connect drops the probe-cache row (observable via `GET /api/drivers`).
- Old profiles (raw host/port) still activate unchanged (`test_hub_connect_profile.py` + regression suites green).
- Equipment tab live: assignment dropdowns obey the one rule; sticky assignments survive reload; ConnectView/BackendPicker gone from the bundle.

## Deferred-in from Phase 1 (fix only if touched; else carry to Phase 3)

T1 update_driver error precedence docs; T2 cache-by-ref offers + real nina/phd2 probe-path tests; T3 direct envelope/403/422 asserts; T4 client `config`-echo types + offersSummary branches; T5 cosmetics (error colors, scan glyph); `validate_scan_host` on driver writes; document `extra` stays non-secret.
