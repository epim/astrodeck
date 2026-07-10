# Equipment Tab — Tasks (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Supersession note:** this file replaces an earlier draft (committed as `d6744d2`) that was not grounded against the shipped Phase 1/2 interfaces (it referenced nonexistent APIs: a `_OVERRIDE_VOCABULARY` registration hook, `self._config` on ConfigStore, `hub._last_connect_result.role_results`, a `test_hub_integration.py`, and React render tests in a repo with no vitest). Every file/line reference below was verified against `HEAD` at plan time.

**Goal:** Finish the equipment-drivers unification (spec `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` §3.4 + §4.1 phase 3): a Tasks section on the Equipment tab (autofocus / polar align / plate solve) driven by a new `solve` capability and a registry-driven override vocabulary, CapabilitiesCard retired — plus the per-device JNOW precession fix (mixed-rig arcminute pointing error).

**Architecture:** The server's `providers.py` resolver grows a third capability (`solve`) and learns the concrete override vocabulary (`auto | <driver-id> | astrodeck | astap | sim`, legacy `backend` kept as an alias). Validation is registry-driven at write time (`config.set_providers` → 422) and degrade-to-auto at resolve time. The two `get_solver()` call sites (hub `solve_and_sync`, native TPPA) route through the resolver via a new `providers.pick_solver(hub)`, making the "SimSolver never fakes a solve near real hardware" guard motion-device-keyed instead of mode-keyed (spec review finding 6). The UI adds a TasksPanel to EquipmentView using the same one-rule eligibility as device rows, and deletes CapabilitiesCard.

**Tech Stack:** FastAPI + pydantic (server), pytest (server tests), React + TS + Tailwind (UI), self-executing `npx tsx` assert files (UI tests — this repo has NO vitest).

## Deferral triage (from `.superpowers/sdd/progress.md`)

Folded into this phase: `ProfileDevice.driver_id` proper typing in `ui/src/types.ts` (Task 5, retiring the `EquipProfileDevice` stopgap), `doSaveProfile` busy-gate (Task 6). All other P1/P2 minors stay deferred; the list remains in the ledger.

## Global Constraints

- **Override vocabulary (spec §3.4):** stored values are `auto | backend | astrodeck | astap | sim | <configured driver id>`. `backend` is a LEGACY alias for "the connected backend's own implementation" and must keep working at read AND write time forever. No config rewrite, no migration step.
- **Write-time validation (spec review finding 2):** a value is valid iff it is `auto`, the legacy alias `backend`, an implicit driver id (`astrodeck` / `astap` / `sim`), or a currently-configured driver id. Unknown values → HTTP **422** on `POST /api/config/providers`. Resolve-time: a since-deleted or capability-inapplicable value degrades to `auto` (never raises).
- **SAFETY (spec review finding 6):** the simulator solver must NEVER be resolvable — not even by explicit override — when a real motion device is connected. "Real" := device `backend` attribute ∈ {`"nina"`, `"alpaca"`} on the connected `telescope` or `focuser`. (Sim devices leave the base-class default `""`; test fakes use `"sim"` — both count as not-real.)
- **JNOW (carry-in):** J2000↔JNOW precession keys on the mount DEVICE (`tel.backend == "alpaca"`), never on the global `hub.mode`. A mixed rig (primary nina + native Alpaca mount) must precess.
- `resolve_all` NEVER raises; every `ProviderChoice` carries a human `reason`; task rows keep the reason permanently visible (spec §5).
- `ProviderChoice.kind` values after this phase: `"backend" | "astrodeck" | "astap" | "sim"` (+ `"unavailable"` synthesized by `resolve_all`). The polar simulator fallback moves from kind `"astrodeck"` to kind `"sim"` (label stays `"Simulator"`) — kind now answers WHO runs it.
- Routes keep the paired RBAC pattern: `dependencies=[Depends(require(CAP))]` + `@declare(CAP)` (enforced by `server/tests/test_backend_roles_match_served.py`). No new routes in this phase.
- Server tests run from `server/`: `.venv/Scripts/python -m pytest -q` (Git Bash absolute: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q`). Full suite currently 832 passing — keep it green.
- UI tests are plain assert files run with `npx tsx <file>` from `ui/`; never reference bare `process` (use the `(globalThis as unknown as {process?:{exit(code:number):void}}).process?.exit(1)` cast — see `ui/src/__tests__/ws.test.ts`). UI build check: `npm --prefix /c/Users/bear/astro/ui run build`.
- Commit per task on `main` (user's workflow), message style `feat(providers): … (spec §3.4)` matching the repo's history.

---

### Task 1: Config — registry-driven override vocabulary + `solve` slot

**Files:**
- Modify: `server/astrodeck/config.py` (ProviderKind ~line 252, ProvidersConfig ~line 255, set_providers ~line 559; add `IMPLICIT_DRIVER_IDS` near the drivers section ~line 260)
- Modify: `server/astrodeck/api/app.py:698-707` (`set_providers_config` route — ValueError → 422)
- Test: `server/tests/test_providers_vocabulary.py` (new)

**Interfaces:**
- Consumes: existing `ConfigStore.add_driver/delete_driver` (Phase 1), `AppConfig.drivers: list[DriverEntry]`.
- Produces: `IMPLICIT_DRIVER_IDS: tuple[str, ...] = ("sim", "astrodeck", "astap")` (module constant), `ConfigStore.valid_override_values() -> set[str]`, `ProvidersConfig` with THREE `str` fields (`autofocus`, `polar_align`, `solve`, all default `"auto"`), `set_providers` validating all three against the registry. Task 2 consumes `valid_override_values()`; Task 3's drivers test consumes `IMPLICIT_DRIVER_IDS`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_providers_vocabulary.py`:

```python
"""Registry-driven provider-override vocabulary (equipment-drivers spec §3.4).

Write-time rule (review finding 2): a value is valid iff it is ``auto``, the
legacy alias ``backend``, an implicit driver id (sim/astrodeck/astap), or a
currently-configured driver id. Unknown ids are rejected at write time (422 on
the route, ValueError at the store); resolve-time degrade lives in
test_providers.py. ``solve`` is the third capability slot — old configs and
old client bodies without it must keep loading/POSTing fine.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from astrodeck.config import (
    IMPLICIT_DRIVER_IDS,
    ConfigStore,
    ProvidersConfig,
)


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(path=tmp_path / "astrodeck.json")


# ------------------------------------------------------------- store level

def test_implicit_ids_are_the_spec_triple():
    assert set(IMPLICIT_DRIVER_IDS) == {"sim", "astrodeck", "astap"}


def test_valid_values_include_legacy_implicit_and_configured(store):
    d = store.add_driver("nina", "astrotown.lan")
    v = store.valid_override_values()
    assert {"auto", "backend", "sim", "astrodeck", "astap", d.id} <= v


def test_set_providers_accepts_new_vocabulary(store):
    d = store.add_driver("nina", "astrotown.lan")
    cfg = store.set_providers(ProvidersConfig(
        autofocus=d.id, polar_align="backend", solve="astap"))
    assert cfg.providers.autofocus == d.id
    assert cfg.providers.solve == "astap"


def test_set_providers_rejects_unknown_id(store):
    with pytest.raises(ValueError, match="nina-dead"):
        store.set_providers(ProvidersConfig(autofocus="nina-dead"))


def test_deleting_a_driver_invalidates_its_id_for_new_writes(store):
    d = store.add_driver("nina", "astrotown.lan")
    store.set_providers(ProvidersConfig(autofocus=d.id))
    store.delete_driver(d.id)
    # the STORED value is untouched (resolve-time degrade handles it) …
    assert store.cfg().providers.autofocus == d.id
    # … but a NEW write of the dead id is rejected.
    with pytest.raises(ValueError):
        store.set_providers(ProvidersConfig(polar_align=d.id))


def test_solve_defaults_auto_for_old_configs():
    # An old ProvidersConfig payload without the key parses with the default.
    assert ProvidersConfig(autofocus="auto", polar_align="auto").solve == "auto"


# ------------------------------------------------------------- route level
# TestClient fixture per repo convention (see test_drivers_api.py).

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_providers_route_accepts_vocabulary_and_rejects_unknown(client):
    ok = client.post("/api/config/providers",
                     json={"autofocus": "astrodeck", "polar_align": "sim",
                           "solve": "astap"})
    assert ok.status_code == 200
    bad = client.post("/api/config/providers",
                      json={"autofocus": "nina-dead", "polar_align": "auto",
                            "solve": "auto"})
    assert bad.status_code == 422
    assert "nina-dead" in bad.text


def test_providers_route_backcompat_body_without_solve(client):
    # An old client POSTing only the two original caps must still succeed.
    r = client.post("/api/config/providers",
                    json={"autofocus": "auto", "polar_align": "backend"})
    assert r.status_code == 200
    assert r.json()["providers"]["solve"] == "auto"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `server/`): `.venv/Scripts/python -m pytest tests/test_providers_vocabulary.py -q`
Expected: FAIL — `ImportError: cannot import name 'IMPLICIT_DRIVER_IDS'`.

- [ ] **Step 3: Implement config.py**

In `server/astrodeck/config.py`:

(a) Replace the `ProviderKind` block (~lines 242-257). Keep the exported name (`providers.py` imports it) but widen it, add `solve`:

```python
# ------------------------------------------------- capability providers (native)
#
# Per-capability routing override. APPENDED to AppConfig (additive — old config
# files without a ``providers`` block load fine; pydantic fills the default).
# Vocabulary (equipment-drivers spec §3.4, registry-driven): ``auto`` (let
# ``providers.resolve()`` pick), the LEGACY alias ``backend`` (the connected
# backend's own implementation — kept working forever, no migration), an
# implicit driver id (``astrodeck`` / ``astap`` / ``sim``), or a
# currently-configured driver id (``AppConfig.drivers[].id``). Because the
# vocabulary is dynamic (driver ids), the fields are plain ``str`` validated in
# ``ConfigStore.set_providers`` — pydantic ``Literal`` can't express it.

ProviderKind = str


class ProvidersConfig(BaseModel):
    autofocus: str = "auto"
    polar_align: str = "auto"
    solve: str = "auto"
```

(b) Near the backend-drivers section (~line 260, before `DriverEntry`), add the implicit-id constant (single source for validation; `drivers._implicit_rows` row ids are pinned to it by a Task 3 test):

```python
#: The DETECTED (non-configured) driver ids drivers._implicit_rows() serves —
#: also the implicit half of the provider-override vocabulary (spec §3.4).
IMPLICIT_DRIVER_IDS: tuple[str, ...] = ("sim", "astrodeck", "astap")
```

(c) Replace `set_providers` (~line 559) and add `valid_override_values`:

```python
    # -- capability providers mutation (native parity + spec §3.4 vocabulary) --

    def valid_override_values(self) -> set[str]:
        """Every value ``set_providers`` accepts RIGHT NOW: ``auto``, the legacy
        ``backend`` alias, the implicit driver ids, and each currently-configured
        driver id. Registry-driven (spec review finding 2) — deleting a driver
        removes its id from this set for FUTURE writes; already-stored values
        degrade to auto at resolve time instead."""
        return ({"auto", "backend", *IMPLICIT_DRIVER_IDS}
                | {d.id for d in self.cfg().drivers})

    def set_providers(self, providers: "ProvidersConfig") -> AppConfig:
        """Persist a new ``ProvidersConfig`` (per-capability routing override).

        Values are validated against the CURRENT vocabulary (see
        ``valid_override_values``) so an unknown/typo'd driver id is rejected at
        write time (the route maps this ValueError to 422) rather than silently
        resolving to auto forever."""
        valid = self.valid_override_values()
        for cap in ("autofocus", "polar_align", "solve"):
            v = getattr(providers, cap, "auto")
            if v not in valid:
                raise ValueError(
                    f"unknown provider for {cap}: {v!r} — valid values are "
                    f"auto, backend (legacy), an implicit driver id "
                    f"({', '.join(IMPLICIT_DRIVER_IDS)}), or a configured "
                    f"driver id")
        cfg = self.cfg()
        cfg.providers = providers
        return self.bump_and_save()
```

(d) In `server/astrodeck/api/app.py` (~line 701-705), the vocabulary rejection becomes 422 (spec review finding 2; body-validation semantics — pydantic itself already 422s malformed bodies):

```python
    async def set_providers_config(body: ProvidersConfig):
        try:
            cfg = await asyncio.to_thread(config_store.set_providers, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload()
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_providers_vocabulary.py -q` → PASS.
Run: `.venv/Scripts/python -m pytest -q` → everything green (existing `test_providers.py` overrides use values inside the new vocabulary, so nothing should break; if a test pinned the OLD ValueError message or the 400 status, update it to the new contract).

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/api/app.py server/tests/test_providers_vocabulary.py
git commit -m "feat(providers): registry-driven override vocabulary + solve slot (spec §3.4)"
```

---

### Task 2: Resolver — `solve` capability, vocabulary mapping, motion-keyed guards

**Files:**
- Modify: `server/astrodeck/providers.py` (whole-module changes: Capability ~48, `_REAL_MODES` ~53, ProviderChoice ~65, `_override` ~84, `_has_real_solver` ~120, `_resolve_polar` ~173, `_RESOLVERS` ~209, `resolve` ~215, `resolve_all` ~227)
- Test: `server/tests/test_providers.py` (extend + update fallback-kind assertions)

**Interfaces:**
- Consumes: `config_store.valid_override_values()` and `IMPLICIT_DRIVER_IDS` (Task 1); `solve.find_astap/AstapSolver/SimSolver`; device `backend` attributes (`""` sim default, `"nina"`, `"alpaca"`).
- Produces: `Capability = Literal["autofocus", "polar_align", "solve"]`; `ProviderChoice.kind: Literal["backend","astrodeck","astap","sim"]`; `resolve("solve", hub)`; `resolve_all` with a `solve` row; `pick_solver(hub) -> PlateSolver` (raises `DeviceError` when solve is unavailable); `_rig_has_real_motion(hub)`. Task 3 consumes `pick_solver`; the UI (Task 5) mirrors the kind vocabulary.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_providers.py` (reuse its `FakeDev` / `FakeHub` / `isolated_config` fixtures; note `isolated_config` monkeypatches `providers.config_store`, so add drivers through THAT store):

```python
# ---------------------------------------------------------------- solve (§3.4)

def test_solve_auto_prefers_astap(monkeypatch):
    monkeypatch.setattr(providers, "find_astap", lambda: "C:/astap/astap.exe")
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("solve", hub)
    assert (c.kind, c.label) == ("astap", "ASTAP")
    assert "astap" in c.reason.lower()


def test_solve_falls_back_to_sim_only_without_real_motion(monkeypatch):
    monkeypatch.setattr(providers, "find_astap", lambda: None)
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("solve", hub)
    assert (c.kind, c.label) == ("sim", "Simulator")


def test_solve_refuses_sim_when_real_motion_connected(monkeypatch):
    """SAFETY: no ASTAP + a real mount => solve is UNAVAILABLE — a faked solve
    would fake-center real hardware (spec review finding 6, motion-keyed)."""
    monkeypatch.setattr(providers, "find_astap", lambda: None)
    hub = FakeHub(mode="nina", devices={"telescope": FakeDev(backend="alpaca"),
                                        "camera": FakeDev(backend="sim")})
    with pytest.raises(DeviceError) as ei:
        providers.resolve("solve", hub)
    assert "astap" in str(ei.value).lower()
    row = providers.resolve_all(hub)["solve"]
    assert row["kind"] == "unavailable"


def test_solve_sim_override_never_beats_the_motion_guard(monkeypatch, isolated_config):
    """SAFETY: an explicit ``sim`` override must NOT resolve on a rig with a
    real focuser — override-with-absent-prerequisites falls back to auto."""
    monkeypatch.setattr(providers, "find_astap", lambda: None)
    isolated_config.set_providers(ProvidersConfig(solve="sim"))
    hub = FakeHub(mode="alpaca", devices={"focuser": FakeDev(backend="alpaca")})
    with pytest.raises(DeviceError):
        providers.resolve("solve", hub)


def test_solve_astap_override_without_astap_degrades(monkeypatch, isolated_config):
    monkeypatch.setattr(providers, "find_astap", lambda: None)
    isolated_config.set_providers(ProvidersConfig(solve="astap"))
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("solve", hub)
    assert c.kind == "sim"      # auto fallback picked the sim solver


# ------------------------------------------------- vocabulary at resolve time

def test_configured_nina_driver_id_forces_backend(isolated_config):
    d = isolated_config.add_driver("nina", "astrotown.lan")
    isolated_config.set_providers(ProvidersConfig(autofocus=d.id))
    hub = FakeHub(mode="nina", nina_client=object(),
                  devices={"camera": FakeDev(backend="nina"),
                           "focuser": FakeDev(backend="nina",
                                              supports_native_autofocus=True)})
    c = providers.resolve("autofocus", hub)
    assert (c.kind, c.label) == ("backend", "NINA")
    assert "override" in c.reason


def test_alpaca_driver_id_has_no_task_impl_degrades_to_auto(isolated_config,
                                                            monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    d = isolated_config.add_driver("alpaca", "mount-pi.lan")
    isolated_config.set_providers(ProvidersConfig(autofocus=d.id))
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("autofocus", hub)
    assert c.kind == "astrodeck"        # auto resolution, not a crash


def test_deleted_driver_id_in_profile_degrades_to_auto(isolated_config,
                                                       monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    prof = Profile(name="x", providers={"autofocus": "nina-dead"})
    hub = FakeHub(mode="sim", devices=_sim_devices(), profile=prof)
    c = providers.resolve("autofocus", hub)
    assert c.kind == "astrodeck"        # malformed-value parity: silently auto


def test_polar_sim_override_pins_simulator(isolated_config):
    isolated_config.set_providers(ProvidersConfig(polar_align="sim"))
    hub = FakeHub(mode="nina", nina_client=object(), devices={})
    c = providers.resolve("polar_align", hub)
    assert (c.kind, c.label) == ("sim", "Simulator")
    assert "override" in c.reason


def test_resolve_all_covers_three_capabilities():
    hub = FakeHub(mode="sim", devices={})
    out = providers.resolve_all(hub)
    assert set(out) == {"autofocus", "polar_align", "solve"}


# ------------------------------------------------------------- pick_solver

def test_pick_solver_returns_astap_then_sim(monkeypatch):
    from astrodeck.solve import AstapSolver, SimSolver
    monkeypatch.setattr(providers, "find_astap", lambda: "C:/astap/astap.exe")
    hub = FakeHub(mode="sim", devices=_sim_devices())
    assert isinstance(providers.pick_solver(hub), AstapSolver)
    monkeypatch.setattr(providers, "find_astap", lambda: None)
    s = providers.pick_solver(hub)
    assert isinstance(s, SimSolver)
    assert s.mode is None       # resolver is the safety authority now
```

Also UPDATE the existing polar-fallback assertions in this file: everywhere the built-in simulator fallback was pinned as `kind == "astrodeck"` with label `"Simulator"`, the kind is now `"sim"` (label unchanged). Do not weaken any other assertion.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_providers.py -q`
Expected: FAIL — `unknown capability: 'solve'`, missing `pick_solver`, kind mismatches.

- [ ] **Step 3: Implement providers.py**

Apply these changes to `server/astrodeck/providers.py`:

(a) Imports + capability set. Replace the `solve` import line and `Capability`/`_REAL_MODES` block:

```python
from .config import IMPLICIT_DRIVER_IDS, ProviderKind, config_store
from .devices.base import DeviceError
from .solve import AstapSolver, PlateSolver, SimSolver, find_astap
```

```python
# The capabilities the resolver answers for (``guide`` stays hard-wired PHD2).
Capability = Literal["autofocus", "polar_align", "solve"]

# Device ``backend`` attribute values that mean REAL hardware. Sim devices leave
# the Device default ("" — devices/base.py:82); only nina.py / alpaca.py set one.
_REAL_BACKENDS = ("nina", "alpaca")
```

(Delete the old `_REAL_MODES` tuple — the mode-keyed guard is retired here; `solve/simsolver.py` keeps its own copy for direct `get_solver` callers.)

(b) Widen `ProviderChoice.kind` and its docstring:

```python
@dataclass
class ProviderChoice:
    """The resolved answer for one capability.

    ``kind`` answers WHO runs it: ``backend`` (the connected backend, e.g.
    NINA), ``astrodeck`` (the native Rust engine), ``astap`` (the local ASTAP
    binary), ``sim`` (the built-in simulator). Never ``auto`` — that is an
    input. ``label`` is the UI badge; ``reason`` explains the pick."""
    kind: Literal["backend", "astrodeck", "astap", "sim"]
    label: str
    reason: str
```

(c) Vocabulary-driven `_override` + the family mapper. Replace `_override`:

```python
def _valid_override_values() -> set[str]:
    """The write-time vocabulary, read defensively (a bare config store in a
    unit test must not break resolution)."""
    try:
        return config_store.valid_override_values()
    except Exception:
        return {"auto", "backend", *IMPLICIT_DRIVER_IDS}


def _override(cap: Capability, hub: object) -> str:
    """The effective override VALUE for ``cap``: the active PROFILE's per-rig
    override wins over the global config; both default to ``auto``. A value
    outside the current vocabulary (deleted driver id, malformed junk)
    degrades to ``auto`` rather than raising (spec §3.4 resolve-time rule)."""
    valid = _valid_override_values()
    getter = getattr(hub, "_active_profile", None)
    if callable(getter):
        try:
            prof = getter()
        except Exception:
            prof = None
        pov = getattr(prof, "providers", None) if prof is not None else None
        if isinstance(pov, dict):
            v = pov.get(cap)
            if isinstance(v, str) and v in valid:
                return v
    try:
        v = getattr(config_store.cfg().providers, cap, "auto")
        if isinstance(v, str) and v in valid:
            return v
    except Exception:
        pass
    return "auto"


def _override_family(value: str) -> str:
    """Map a concrete override value to the implementation FAMILY the resolvers
    branch on. A configured driver id maps by its driver TYPE (only NINA
    drivers implement tasks — their family is the legacy ``backend``); alpaca/
    phd2 ids and anything unknown degrade to ``auto``."""
    if value in ("auto", "backend", "astrodeck", "astap", "sim"):
        return value
    try:
        for d in config_store.cfg().drivers:
            if d.id == value:
                return "backend" if d.type == "nina" else "auto"
    except Exception:
        pass
    return "auto"
```

(d) Motion guard + `_has_real_solver` rewrite (spec review finding 6 — replaces the `hub.mode` check):

```python
def _rig_has_real_motion(hub: object) -> bool:
    """True when any CONNECTED motion device (mount/focuser) is real hardware.
    This — not the global hub mode, meaningless on a mixed rig — is what makes
    a faked plate solve dangerous (review finding 6)."""
    for role in ("telescope", "focuser"):
        dev = _connected(hub, role)
        if dev is not None and getattr(dev, "backend", "") in _REAL_BACKENDS:
            return True
    return False


def _has_real_solver(hub: object) -> bool:
    """Whether a TRUSTWORTHY plate solver resolves for this rig (native TPPA
    needs one). Delegates to the ``solve`` capability — ONE guard, motion-
    device-keyed, shared with solve_and_sync."""
    try:
        resolve("solve", hub)
        return True
    except Exception:
        return False
```

(e) `_resolve_polar`: the simulator fallback (and the new explicit pin) become kind `"sim"`. Add the pin as the FIRST override branch and change the fallback's kind:

```python
def _resolve_polar(hub: object, override: str) -> ProviderChoice:
    nina = getattr(hub, "nina_client", None) is not None
    cam = _connected(hub, "camera")
    tel = _connected(hub, "telescope")
    native_polar = bool(NATIVE_AVAILABLE and cam is not None and tel is not None
                        and _has_real_solver(hub))

    # (1) explicit override — honored only when its prerequisites are present.
    # ``sim`` has no prerequisites: the built-in simulator always runs.
    if override == "sim":
        return ProviderChoice("sim", "Simulator", "override: built-in simulator")
    if override == "backend" and nina:
        return ProviderChoice("backend", "NINA", "override: NINA TPPA plugin")
    if override == "astrodeck" and native_polar:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "override: native TPPA on camera + mount + solver")
```

…and in the trailing fallback `return`, change `ProviderChoice("astrodeck", "Simulator", reason)` to `ProviderChoice("sim", "Simulator", reason)`.

(f) New `_resolve_solve` + registration + `resolve`/`resolve_all` growth:

```python
def _resolve_solve(hub: object, override: str) -> ProviderChoice:
    """Who plate-solves. ASTAP is the only solver trusted near real hardware;
    the simulator solver is offered ONLY when no connected motion device is
    real — even an explicit ``sim`` override cannot beat that guard (review
    finding 6: a faked solve would fake-center a real mount)."""
    astap = find_astap()
    sim_ok = not _rig_has_real_motion(hub)

    # (1) explicit override — honored only when its prerequisites are present.
    if override == "astap" and astap:
        return ProviderChoice("astap", "ASTAP", f"override: ASTAP at {astap}")
    if override == "sim" and sim_ok:
        return ProviderChoice("sim", "Simulator",
                              "override: simulator solver (no real motion connected)")

    # (2) auto (or an override whose prerequisites were absent).
    if astap:
        return ProviderChoice("astap", "ASTAP", f"ASTAP found at {astap}")
    if sim_ok:
        return ProviderChoice("sim", "Simulator",
                              "no ASTAP — simulator solver (no real motion connected)")
    raise DeviceError(
        "plate solving unavailable: ASTAP not found and a real mount/focuser "
        "is connected — refusing the simulator solver (install ASTAP or set "
        "ASTAP_PATH)")


_RESOLVERS = {
    "autofocus": _resolve_autofocus,
    "polar_align": _resolve_polar,
    "solve": _resolve_solve,
}
```

In `resolve()`, pass the family: `return resolver(hub, _override_family(_override(cap, hub)))`. In `resolve_all()`, loop `for cap in ("autofocus", "polar_align", "solve"):`. Update `_resolve_autofocus`'s signature annotation to `override: str` (its two `if override == …` branches need no change — `sim`/`astap` families simply fall through to auto, which is correct: sim has no standalone autofocus implementation).

(g) `pick_solver` — the one constructor call sites use (Task 3):

```python
def pick_solver(hub: object) -> PlateSolver:
    """The actual solver instance for this rig, per ``resolve("solve")``.
    Raises ``DeviceError`` (user-presentable) when nothing trustworthy can
    solve — BEFORE any exposure is wasted. The SimSolver is built with
    ``mode=None``: the resolver's motion-device guard is the safety authority
    now (finding 6); SimSolver's own mode check remains only for legacy direct
    ``get_solver`` callers."""
    choice = resolve("solve", hub)
    if choice.kind == "astap":
        astap = find_astap()
        if astap:
            return AstapSolver(astap)
        raise DeviceError("ASTAP disappeared between resolution and use")
    return SimSolver(getattr(hub, "sim_rig", None), mode=None)
```

Also update the module docstring's resolution-policy paragraph to name the new vocabulary (`auto | backend (legacy) | astrodeck | astap | sim | <driver-id>`) and the third capability.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_providers.py tests/test_providers_vocabulary.py -q` → PASS.
Run: `.venv/Scripts/python -m pytest -q` → full suite. Tests elsewhere that pinned the polar fallback kind (`"astrodeck"` + `"Simulator"`) or two-key `resolve_all`/`status.providers` payloads must be updated to the new contract (`test_polar_native.py`, any `poll_status` providers assertions) — update the assertion, never the contract.

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/providers.py server/tests/test_providers.py
git commit -m "feat(providers): solve capability + concrete vocabulary, motion-keyed sim-solver guard (spec §3.4)"
```

---

### Task 3: Wire solve through the resolver + implicit-offers honesty

**Files:**
- Modify: `server/astrodeck/hub.py` (solve_and_sync ~line 1566 + the top-level `from .solve import get_solver` import ~line 51; poll_status fallback caps ~line 1927)
- Modify: `server/astrodeck/polar/native.py` (~line 37 import, ~line 98 `_drive`)
- Modify: `server/astrodeck/drivers.py` (`_implicit_rows` sim row ~line 150)
- Modify: `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` (~line 140 sim-offers sentence)
- Test: `server/tests/test_drivers_probe.py` (extend), existing solve/polar tests adapted

**Interfaces:**
- Consumes: `providers.pick_solver(hub)` (Task 2), `config.IMPLICIT_DRIVER_IDS` (Task 1).
- Produces: `hub.solve_and_sync` and native TPPA resolve their solver via the resolver (reason-honest, fails BEFORE exposing); sim implicit driver offers `tasks: ["polar_align", "solve"]`; `status.providers` fallback covers three caps.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_drivers_probe.py`:

```python
def test_implicit_ids_match_config_constant():
    """config.IMPLICIT_DRIVER_IDS is the vocabulary source (spec §3.4); the
    rows drivers serves must never drift from it."""
    from astrodeck.config import IMPLICIT_DRIVER_IDS
    from astrodeck.drivers import _implicit_rows
    assert [r["id"] for r in _implicit_rows()] == list(IMPLICIT_DRIVER_IDS)


def test_sim_offers_polar_and_solve_not_autofocus():
    """Failure honesty (spec §5): the sim driver offers only tasks it actually
    implements — the built-in polar simulator and the SimSolver. Autofocus on a
    sim rig is the NATIVE engine's offer (V-curve on sim devices), not sim's."""
    from astrodeck.drivers import _implicit_rows
    sim = next(r for r in _implicit_rows() if r["id"] == "sim")
    assert sim["offers"]["tasks"] == ["polar_align", "solve"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_probe.py -q`
Expected: the two new tests FAIL (sim row currently offers `["autofocus", "polar_align"]`).

- [ ] **Step 3: Implement**

(a) `server/astrodeck/drivers.py` `_implicit_rows()` sim row: change the offers line to

```python
        "offers": {"devices": [{"role": r, "name": f"Simulated {r}"}
                                for r in ROLES],
                   # Only tasks sim actually implements (spec §5 honesty): the
                   # built-in polar simulator + the SimSolver. Autofocus on a
                   # sim rig is the NATIVE engine's V-curve — the astrodeck
                   # row's offer, not this one's.
                   "tasks": ["polar_align", "solve"]},
```

(b) `server/astrodeck/hub.py` `solve_and_sync`: move solver acquisition ABOVE the capture (right after the `require("camera")`/`require("telescope")` calls ~line 1542) so an unsolvable rig fails in <1 ms instead of after a 3 s exposure, and route it through the resolver:

```python
        # Resolver-routed (spec §3.4): honors the user's solve override and the
        # motion-keyed sim-solver guard, and raises a clear DeviceError BEFORE
        # an exposure is wasted when nothing trustworthy can solve.
        from . import providers as _providers
        solver = _providers.pick_solver(self)
```

Delete the old `solver = get_solver(self.sim_rig, mode=self.mode)` line (~1566) and update the method docstring's last sentence (resolution now happens up front via the resolver). Then remove the now-unused `from .solve import get_solver` at hub.py:51 — grep the file first; if any other hub call site still uses `get_solver`, keep the import and say so in the report.

(c) `server/astrodeck/hub.py` poll_status defensive fallback (~line 1927): change the cap tuple to `("autofocus", "polar_align", "solve")`.

(d) `server/astrodeck/polar/native.py` `_drive` (~line 98): replace

```python
    solver = get_solver(getattr(hub, "sim_rig", None), getattr(hub, "mode", None))
```

with

```python
    from .. import providers as _providers
    solver = _providers.pick_solver(hub)
```

and drop the module-level `from ..solve import get_solver` import (line ~37) if now unused. A `DeviceError` here propagates to the session's existing failure handler (`bus.log("error", f"native TPPA failed: …")`) — that is the desired honesty.

(e) Spec amendment, `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` (~line 140): change the sim clause of the implicit-offers sentence from "`sim` → … role + both tasks (labelled Simulator)" to "`sim` → every role + `polar_align` + `solve` (labelled Simulator; autofocus on a sim rig is the native engine's offer) *(amended in phase 3)*".

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_probe.py -q` → PASS.
Run: `.venv/Scripts/python -m pytest -q` → full suite. Expect fallout in `test_hub_solve.py` / polar tests that relied on `get_solver` monkeypatching or mode-keyed SimSolver refusal — re-target those to `providers.pick_solver` / the resolver contract, preserving each test's original intent (e.g. "no ASTAP on a real rig must not fake-solve" now asserts `DeviceError` from `solve_and_sync` BEFORE capture). Any test asserting the sim driver offers autofocus gets the new offers list.

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/hub.py server/astrodeck/polar/native.py server/astrodeck/drivers.py server/tests/ docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md
git commit -m "feat(providers): route solve_and_sync + native TPPA through the resolver; honest sim offers (spec §3.4/§5)"
```

---

### Task 4: Per-device JNOW precession gate (mixed-rig arcminute fix)

**Files:**
- Modify: `server/astrodeck/hub.py:1192-1218` (`_mount_expects_jnow`)
- Test: `server/tests/test_hub_capture_precession.py` (rework `test_mount_frame_converts_only_for_jnow_alpaca` + fake)

**Interfaces:**
- Consumes: device `backend` attribute (`"alpaca"` set by `devices/alpaca.py:257`; sim devices leave `""`; NINA devices `"nina"`).
- Produces: `to_mount_frame`/`from_mount_frame` precess whenever the TELESCOPE DEVICE is Alpaca-backed, regardless of `hub.mode`. No API change.

- [ ] **Step 1: Rework the test to pin the per-device contract**

In `server/tests/test_hub_capture_precession.py`, give the fake its identity and replace the mode-keyed test:

```python
class _FakeAlpacaTel:
    """Fake mount exposing the Alpaca ``_get`` seam so the hub can probe
    EquatorialSystem. ``equ`` = ASCOM EquatorialCoordinateType. Carries the
    real Alpaca device marker (``backend`` — devices/alpaca.py:257) because
    the JNOW gate is DEVICE-keyed, not hub-mode-keyed."""
    backend = "alpaca"

    def __init__(self, equ: int):
        self._equ = equ

    async def _get(self, method, **params):
        if method == "equatorialsystem":
            return self._equ
        raise KeyError(method)


class _FakeSimTel:
    """A sim-backed mount: no ``backend`` marker (devices/base.py default '')."""
    backend = ""


async def test_mount_frame_converts_only_for_alpaca_devices():
    h = Hub()
    tel_jnow = _FakeAlpacaTel(equ=1)         # topocentric / JNOW

    # A sim DEVICE never converts — even when the hub is in alpaca mode.
    h.mode = "alpaca"
    assert await h.to_mount_frame(_FakeSimTel(), 16.6949, 36.4603) == (16.6949, 36.4603)

    # An Alpaca topocentric device converts (target moves ~arcmin off J2000)…
    h._mount_wants_jnow = None
    ra1, dec1 = await h.to_mount_frame(tel_jnow, 16.6949, 36.4603)
    assert _sep_arcmin(16.6949, 36.4603, ra1, dec1) > 5.0

    # …INCLUDING on a mixed rig where the hub mode is "nina" (the phase-2
    # review's arcminute regression: primary NINA + native Alpaca mount).
    h2 = Hub()
    h2.mode = "nina"
    ra2, dec2 = await h2.to_mount_frame(_FakeAlpacaTel(equ=1), 16.6949, 36.4603)
    assert _sep_arcmin(16.6949, 36.4603, ra2, dec2) > 5.0

    # An Alpaca mount that reports J2000 (==2) must NOT double-precess.
    h3 = Hub()
    h3.mode = "alpaca"
    assert await h3.to_mount_frame(_FakeAlpacaTel(equ=2), 16.6949, 36.4603) == (16.6949, 36.4603)
```

(Delete the old `test_mount_frame_converts_only_for_jnow_alpaca` — this replaces it; update the module docstring bullet accordingly.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_hub_capture_precession.py -q`
Expected: the mixed-rig (`mode="nina"`) case FAILS — coordinates come back unconverted.

- [ ] **Step 3: Implement the gate**

In `server/astrodeck/hub.py` `_mount_expects_jnow` (~line 1204), replace

```python
        if self.mode != "alpaca":
            return False
```

with

```python
        if getattr(tel, "backend", "") != "alpaca":
            return False
```

and update the docstring's first paragraph: the gate keys on the mount DEVICE's backend (`devices/alpaca.py` sets `backend = "alpaca"`), not the global `hub.mode` — so a mixed rig (e.g. primary NINA with a native Alpaca mount) precesses correctly; sim/NINA mounts (backend `""`/`"nina"`) stay unconverted as before. The `_mount_wants_jnow` cache and its reset points (`hub.py:390`, `hub.py:659`) are unchanged.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_hub_capture_precession.py -q` → PASS.
Run: `.venv/Scripts/python -m pytest -q` → full suite green (other precession consumers build real Alpaca devices or sim devices, both correctly classified; fix any fake that toggled `hub.mode` to opt into precession by giving it `backend = "alpaca"` instead).

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/hub.py server/tests/test_hub_capture_precession.py
git commit -m "fix(hub): JNOW precession keys on the mount device, not hub.mode — mixed-rig arcminute fix"
```

---

### Task 5: UI foundation — types, store, health, badge, task eligibility

**Files:**
- Modify: `ui/src/types.ts` (ProviderKind ~486, ProvidersConfig ~487, ProfileDevice ~851, Profile ~862)
- Modify: `ui/src/store.ts` (ResolvedProviderKind ~55, ProvidersStatus ~61)
- Modify: `ui/src/lib/health.ts` (~line 160 caps loop)
- Modify: `ui/src/components/ProviderBadge.tsx` (Cap ~18, CAP_META ~20, variant ~38, a11y ~41)
- Modify: `ui/src/lib/equipment.ts` (add `TaskCap`, `TASK_CAPS`, `eligibleTaskDrivers`)
- Modify: `ui/src/components/settings/CapabilitiesCard.tsx:44` (one line: add `solve: "auto"` to DEFAULT_PROVIDERS — keeps the build green until Task 6 deletes the file)
- Test: `ui/src/lib/__tests__/equipment.test.ts` (extend), `ui/src/components/__tests__/healthStrip.test.ts` (extend)

**Interfaces:**
- Consumes: server contracts from Tasks 1-2 (`ProvidersConfig{autofocus,polar_align,solve}: str`, kinds `backend|astrodeck|astap|sim|unavailable`, `DriverInfo.offers.tasks: string[]`).
- Produces: `ProviderKind = string`; `ProvidersConfig.solve`; `ProfileDevice.driver_id?: string`; `Profile.providers?: Partial<ProvidersConfig> | null`; `TaskCap`, `TASK_CAPS`, `eligibleTaskDrivers(cap, drivers)` (equipment.ts); ProviderBadge accepts `cap="solve"`. Task 6 consumes all of these.

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/lib/__tests__/equipment.test.ts` — FOLLOW THE FILE'S EXISTING assert/fixture conventions (read it first; reuse its DriverInfo factory if one exists, else minimal literals matching `types.ts DriverInfo`). The behavior to pin:

```ts
// ---------------------------------------------------------- task eligibility
// THE ONE RULE, task edition: enabled + reachable + actually offering the task.
{
  const mk = (id: string, tasks: string[], opts?: { enabled?: boolean; reachable?: boolean }) =>
    ({
      id,
      type: id.split("-")[0],
      label: id,
      enabled: opts?.enabled ?? true,
      implicit: !id.includes("-"),
      status: { reachable: opts?.reachable ?? true, error: null, detail: null, probed_at: 0 },
      offers: { devices: [], tasks },
    }) as unknown as DriverInfo;

  const drivers = [
    mk("nina-a1b2", ["autofocus", "polar_align"]),
    mk("astrodeck", ["autofocus", "polar_align"]),
    mk("astap", ["solve"]),
    mk("sim", ["polar_align", "solve"]),
    mk("nina-dead", ["autofocus"], { reachable: false }),
    mk("nina-off", ["autofocus"], { enabled: false }),
  ];

  assertEqual(eligibleTaskDrivers("autofocus", drivers).map((d) => d.id),
              ["nina-a1b2", "astrodeck"]);
  assertEqual(eligibleTaskDrivers("solve", drivers).map((d) => d.id),
              ["astap", "sim"]);
  assertEqual(eligibleTaskDrivers("polar_align", drivers).map((d) => d.id),
              ["nina-a1b2", "astrodeck", "sim"]);
}
```

Append to `ui/src/components/__tests__/healthStrip.test.ts` a solve-unavailable case, copying the shape of its existing "status.providers unavailable => Notice" case (~line 164) with `providers: { solve: { kind: "unavailable", label: "none", reason: "no ASTAP and a real mount is connected" } }` asserting an issue whose text includes `"Plate solving unavailable"`.

- [ ] **Step 2: Run to verify failure**

Run (from `ui/`): `npx tsx src/lib/__tests__/equipment.test.ts`
Expected: FAIL — `eligibleTaskDrivers` is not exported.

- [ ] **Step 3: Implement**

(a) `ui/src/types.ts` — replace the provider block (~lines 480-490):

```ts
// ------------------------------------------------------- capability providers
// Mirrors server/astrodeck/config.py ProvidersConfig — the CONFIG (write) side.
// Vocabulary (spec §3.4, registry-driven): "auto" | "backend" (LEGACY alias for
// the connected backend's own implementation — kept accepted forever) |
// "astrodeck" | "astap" | "sim" (implicit driver ids) | a configured driver id
// ("nina-a3f2"). Dynamic — a plain string; the server 422s unknown values.
export type ProviderKind = string;
export interface ProvidersConfig {
  autofocus: ProviderKind;
  polar_align: ProviderKind;
  solve: ProviderKind;
}
```

`ProfileDevice` (~line 851) gains the field the server has persisted since phase 2 (retires EquipmentView's `EquipProfileDevice` stopgap — removed in Task 6):

```ts
  extra: Record<string, unknown>;
  // Reference to AppConfig.drivers[].id (spec §3.3/§4.4); server-persisted
  // (profiles.ProfileDevice.driver_id, default ""). Optional here so
  // pre-drivers profile payloads type-check unchanged.
  driver_id?: string;
```

`Profile` (~line 862) gains the task-override snapshot (spec §4.4; mirrors `profiles.Profile.providers: dict | None`):

```ts
  optics: Optics | null;
  site_name: string | null;
  // Per-rig task overrides (spec §4.4) — wins over global config when this
  // profile is ACTIVE (server providers._override). Partial dict server-side;
  // absent on old profiles.
  providers?: Partial<ProvidersConfig> | null;
```

(b) `ui/src/store.ts` (~lines 55-64):

```ts
export type ResolvedProviderKind =
  | "astrodeck"
  | "backend"
  | "astap"
  | "sim"
  | "unavailable";
export interface ProviderChoiceView {
  kind: ResolvedProviderKind;
  label: string;
  reason: string;
}
export interface ProvidersStatus {
  autofocus?: ProviderChoiceView;
  polar_align?: ProviderChoiceView;
  solve?: ProviderChoiceView;
}
```

(Extend the comment above it: kinds now answer WHO — astap = the local ASTAP binary, sim = the built-in simulator.)

(c) `ui/src/lib/health.ts` (~line 160): add the third row to the caps loop:

```ts
  for (const [cap, label] of [
    ["autofocus", "Autofocus"],
    ["polar_align", "Polar alignment"],
    ["solve", "Plate solving"],
  ] as const) {
```

(d) `ui/src/components/ProviderBadge.tsx`: widen the cap + style maps:

```ts
type Cap = "autofocus" | "polar_align" | "solve";

const CAP_META: Record<Cap, { abbr: string; full: string }> = {
  autofocus: { abbr: "AF", full: "Autofocus" },
  polar_align: { abbr: "TPPA", full: "Polar alignment" },
  solve: { abbr: "SOLVE", full: "Plate solving" },
};
```

and the variant line (~38) — our-side kinds (astrodeck, sim) keep the accent chip, external ones (backend, astap) the muted chip:

```ts
  const variant =
    kind === "astrodeck" || kind === "sim"
      ? ""
      : kind === "backend" || kind === "astap"
        ? " prov-ext"
        : " prov-na";
```

Keep the a11y string's astrodeck-only suffix condition as-is (`kind === "astrodeck"`); update the header comment's kind table (astap → muted, sim → accent).

(e) `ui/src/lib/equipment.ts`: after `eligibleDrivers`, add:

```ts
/** The three task slots the Equipment Tasks section renders (spec §4.1). */
export type TaskCap = "autofocus" | "polar_align" | "solve";
export const TASK_CAPS: { cap: TaskCap; label: string }[] = [
  { cap: "autofocus", label: "Autofocus" },
  { cap: "polar_align", label: "Polar align" },
  { cap: "solve", label: "Plate solve" },
];

/** THE ONE RULE, task edition (spec §4.1): a driver appears in a task row's
 *  dropdown only when it is enabled, reachable, and its probe actually offers
 *  that task. */
export function eligibleTaskDrivers(cap: TaskCap, drivers: DriverInfo[]): DriverInfo[] {
  return drivers.filter(
    (d) => d.enabled && d.status.reachable && d.offers.tasks.includes(cap),
  );
}
```

(f) `ui/src/components/settings/CapabilitiesCard.tsx:44`: `const DEFAULT_PROVIDERS: ProvidersConfig = { autofocus: "auto", polar_align: "auto", solve: "auto" };` (file dies in Task 6; this keeps the interim build green).

- [ ] **Step 4: Run tests + build**

Run: `npx tsx src/lib/__tests__/equipment.test.ts` → PASS; `npx tsx src/components/__tests__/healthStrip.test.ts` → PASS.
Run: `npm --prefix /c/Users/bear/astro/ui run build` → clean (fix any other `ProvidersConfig` literal the compiler flags by adding `solve: "auto"`).

- [ ] **Step 5: Commit**

```bash
git add ui/src/types.ts ui/src/store.ts ui/src/lib/health.ts ui/src/components/ProviderBadge.tsx ui/src/lib/equipment.ts ui/src/components/settings/CapabilitiesCard.tsx ui/src/lib/__tests__/equipment.test.ts ui/src/components/__tests__/healthStrip.test.ts
git commit -m "feat(ui/providers): solve capability types, task eligibility rule, badge + health rows (spec §4.1)"
```

---

### Task 6: Equipment Tasks panel + retire CapabilitiesCard

**Files:**
- Create: `ui/src/components/equipment/TasksPanel.tsx`
- Modify: `ui/src/views/EquipmentView.tsx` (drop `EquipProfileDevice` ~52; insert TasksPanel ~307; providers snapshot in `doSaveProfile` ~185; restore in `doLoadProfile` ~234; Save busy-gate ~352)
- Modify: `ui/src/components/settings/SettingsView.tsx:27,154` (remove import + usage)
- Delete: `ui/src/components/settings/CapabilitiesCard.tsx`
- Modify: `ui/src/components/settings/DriversPanel.tsx:10` (comment referenced the dead card — reword to "the cap the panel renders read-only (same Gated pattern as the rest of Settings)")

**Interfaces:**
- Consumes: `TASK_CAPS`, `eligibleTaskDrivers` (Task 5); `useConfig`/`useProviders`/`useStore` + `setProvidersConfig` (existing — CapabilitiesCard's exact pattern); `ProviderBadge cap="solve"` (Task 5); the `DriverInfo` list EquipmentView already loads.
- Produces: the spec §4.1 Tasks section; profile save/load round-trips task overrides (spec §4.4); `DEFAULT_PROVIDERS` exported from TasksPanel. No new API surface.

- [ ] **Step 1: Create TasksPanel**

`ui/src/components/equipment/TasksPanel.tsx` — complete file:

```tsx
// TasksPanel.tsx — the Equipment tab's TASKS section (spec §4.1): who runs
// autofocus / polar align / plate solve. Same row grammar as the device slots:
// a dropdown of concrete eligible providers (THE ONE RULE, task edition —
// enabled + reachable + actually offering the task) around an "Auto" default,
// the resolved ProviderBadge, and the resolver's reason line permanently
// visible (spec §5: "why is this on NINA right now" always has an answer).
//
// Values are driver ids (implicit "astrodeck"/"astap"/"sim" or configured
// "nina-xxxx"); the server validates writes against the registry (422) and
// keeps accepting the LEGACY "backend" alias — shown here as a sticky option
// when it is the stored value, never offered fresh. Writes go to the GLOBAL
// config (POST /api/config/providers), exactly as the retired CapabilitiesCard
// did; per-profile overrides ride profile save/load in EquipmentView.
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, ProvidersConfig } from "../../types";
import { setProvidersConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useProviders, useStore } from "../../store";
import { useCanConfigBackend } from "../../lib/caps";
import { eligibleTaskDrivers, TASK_CAPS, type TaskCap } from "../../lib/equipment";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
import { ProviderBadge } from "../ProviderBadge";

export const DEFAULT_PROVIDERS: ProvidersConfig = {
  autofocus: "auto",
  polar_align: "auto",
  solve: "auto",
};

export default function TasksPanel({
  drivers,
  busy = false,
}: {
  drivers: DriverInfo[];
  busy?: boolean;
}): JSX.Element {
  const config = useConfig();
  const resolved = useProviders();
  const canConfig = useCanConfigBackend();

  const seed: ProvidersConfig = { ...DEFAULT_PROVIDERS, ...(config?.providers ?? {}) };
  const [draft, setDraft] = useState<ProvidersConfig>(seed);
  const [busyCap, setBusyCap] = useState<TaskCap | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed whenever a fresh config lands (our own save, or another client's).
  useEffect(() => {
    setDraft(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.autofocus, seed.polar_align, seed.solve]);

  const persist = async (cap: TaskCap, value: string) => {
    if (busyCap) return;
    setErr(null);
    setBusyCap(cap);
    const next: ProvidersConfig = { ...draft, [cap]: value };
    setDraft(next); // optimistic — echoed back by loadConfig() below
    try {
      await setProvidersConfig(next);
      await useStore.getState().loadConfig();
    } catch (e) {
      setDraft(seed); // revert the optimistic edit
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.backend required to change task routing"
            : e.message
          : e instanceof Error
            ? e.message
            : "couldn't save task override";
      setErr(msg);
    } finally {
      setBusyCap(null);
    }
  };

  return (
    <Panel
      title="Tasks"
      right={
        <InfoDot
          label="About task routing"
          content="Pick who runs each task. Auto chooses the best available for the connected rig; the options are the drivers that actually offer the task right now. The line under each row explains the current resolution."
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        {TASK_CAPS.map(({ cap, label }) => {
          const eligible = eligibleTaskDrivers(cap, drivers);
          const value = draft[cap];
          const choice = resolved?.[cap];
          const inList = value === "auto" || eligible.some((d) => d.id === value);
          const stale = drivers.find((d) => d.id === value);
          return (
            <div key={cap} className="border border-line bg-bg/60 px-3 py-2.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="label w-28 shrink-0">{label}</span>
                <select
                  className="field !py-1 max-w-[220px]"
                  value={value}
                  disabled={!canConfig || busy || busyCap === cap}
                  onChange={(e) => void persist(cap, e.target.value)}
                  aria-label={`${label} provider override`}
                >
                  <option value="auto">Auto (best available)</option>
                  {eligible.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.label}
                    </option>
                  ))}
                  {/* sticky (spec §5): a stored value no longer offered stays
                      listed (legacy "backend" alias, or a driver that went
                      unreachable/was deleted) instead of silently vanishing */}
                  {!inList && (
                    <option value={value}>
                      {value === "backend"
                        ? "Backend (legacy)"
                        : (stale?.label ?? value)}
                    </option>
                  )}
                </select>
                <div className="flex-1" />
                <ProviderBadge cap={cap} />
              </div>
              {choice?.reason && (
                <p className="text-[11px] text-dim mt-1.5 pl-[8rem] leading-snug">
                  {choice.reason}
                </p>
              )}
            </div>
          );
        })}
        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing task routing needs operator or admin access.
          </p>
        )}
        {err && (
          <p className="text-[11px] text-bad inline-flex items-center gap-1.5">
            <Icon name="alert" size={11} /> {err}
          </p>
        )}
      </div>
    </Panel>
  );
}
```

- [ ] **Step 2: Integrate into EquipmentView**

In `ui/src/views/EquipmentView.tsx`:

(a) Delete the `EquipProfileDevice` type (~lines 48-52) and its two uses: `doSaveProfile` builds `const devices: ProfileDevice[] = []` and pushes the same literal (now valid — `driver_id` is a typed optional field, Task 5); `doLoadProfile` reads `const driverId = d.driver_id;` directly. Update both stale comments (they explain a stopgap that no longer exists).

(b) Import + mount the panel and the store's config. Imports: `TasksPanel, { DEFAULT_PROVIDERS }` from `../components/equipment/TasksPanel`; add `useConfig` to the existing `../store` import; `setProvidersConfig` from `../api/backends`; `ProvidersConfig` type from `../types`. Add `const config = useConfig();` near the other hooks (~line 65). Insert between the Devices `</Panel>` and the "Rig Actions" panel (~line 307):

```tsx
        <TasksPanel drivers={drivers} busy={busy} />
```

(c) Profile snapshot (spec §4.4). In `doSaveProfile`, add to the `Profile` literal (after `site_name: null,`):

```tsx
        // Task-override snapshot (spec §4.4): the profile carries the CURRENT
        // global task routing so Activate restores it (server-side precedence).
        providers: config?.providers ? { ...config.providers } : null,
```

(d) Profile restore. In `doLoadProfile`, after `saveAssignments(next);` and before the success toast:

```tsx
        // Restore the profile's task overrides into global config so the Tasks
        // rows and the server's resolution match the loaded snapshot. Viewers
        // (no config.backend) skip this — assignments alone are still useful.
        if (p.providers && canConfig) {
          try {
            await setProvidersConfig({ ...DEFAULT_PROVIDERS, ...p.providers });
            await useStore.getState().loadConfig();
          } catch {
            showToast("warning", "Assignments loaded, but task overrides couldn't be restored");
          }
        }
```

(e) Busy-gate fold-in (P2 deferral): the profile Save button's `disabled` (~line 352) becomes `disabled={!canConfig || !profileName.trim() || assignedCount === 0 || busy}`.

- [ ] **Step 3: Retire CapabilitiesCard**

- `ui/src/components/settings/SettingsView.tsx`: delete line 27 (`import CapabilitiesCard …`) and line 154 (`<CapabilitiesCard />`).
- Delete `ui/src/components/settings/CapabilitiesCard.tsx` (`git rm`).
- `ui/src/components/settings/DriversPanel.tsx:10`: reword the comment so it no longer points at the dead file.
- Grep `ui/src` for any remaining `CapabilitiesCard` reference — must be zero.

- [ ] **Step 4: Build + tests**

Run: `npm --prefix /c/Users/bear/astro/ui run build` → clean.
Run: `npx tsx src/lib/__tests__/equipment.test.ts` and `npx tsx src/lib/__tests__/drivers.test.ts` → PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add -A ui/src
git commit -m "feat(ui/equipment): Tasks section (autofocus/polar/solve rows) + profile task-override round-trip; retire CapabilitiesCard (spec §4.1/§4.4)"
```

---

## Verification (controller, after all tasks)

1. Full server suite from `server/`: `.venv/Scripts/python -m pytest -q` — green.
2. UI build + every `__tests__` tsx file — green.
3. Live smoke on :8801 (`/c/Users/bear/astro/server/.venv/Scripts/python.exe -m astrodeck --port 8801`): Equipment shows the Tasks section with reasons; `GET /api/drivers` sim row offers `polar_align, solve`; `POST /api/config/providers` with a junk id → 422; Settings no longer shows Capabilities.
4. Push + `gh run watch --exit-status` — CI verified green before claiming ship.
