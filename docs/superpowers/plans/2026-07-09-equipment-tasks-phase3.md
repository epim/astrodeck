# Phase 3 Plan: Equipment Tasks Section + Registry-Driven Providers

**Spec reference:** `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` §3.4 (resolver extensions) + §4.1 (Tasks rows) + spec review findings 4, 6.

**Base commit:** `6b9c5a3` (Phase 2 ship)

**Outcome:** Equipment tab Tasks section (autofocus/polar/solve rows with concrete vocabulary + reason lines), registry-driven override validation, JNOW per-device fix, CapabilitiesCard retirement, full suite green, CI green.

---

## Task 1: `solve` capability + registry-driven override validation

**Implementer:** sonnet  
**Reviewer:** opus  
**Brief:**

Config evolution:

```python
# server/astrodeck/config.py ~line 220
class ProvidersConfig(BaseModel):
    autofocus: str = "auto"  # existing
    polar_align: str = "auto"  # existing
    solve: str = "auto"  # NEW: override for plate solve, same model
```

Provider vocabulary stays hardcoded-but-registry-driven. In `server/astrodeck/providers.py` near line 45:

```python
# OLD (hardcoded whitelists):
# @dataclass(frozen=True)
# class ProviderChoice:
#   kind: Literal["backend", "astrodeck", "unavailable"]
#   ...

# NEW: registry-driven, no hardcoded Literal
_OVERRIDE_VOCABULARY = set()  # populated at runtime

def _register_override_vocabulary(*vocab: str):
    """Register valid override terms for dynamic validation."""
    _OVERRIDE_VOCABULARY.update(vocab)

# At module load, register the base vocabulary:
_register_override_vocabulary("auto")
_OVERRIDE_VOCABULARY.add("astrodeck")  # always valid (native/sim/fallback)
_OVERRIDE_VOCABULARY.add("astap")      # always valid (solve)
_OVERRIDE_VOCABULARY.add("sim")        # always valid (sim-only)

# For each backend driver, add its id as a valid override term:
# (done lazily at resolve time via drivers.describe_all())
```

**Validation (write-time, in `config.py:set_providers`)**:

```python
def set_providers(self, providers: dict) -> None:
    """Store providers config with registry-driven vocabulary validation."""
    cfg = ProvidersConfig(**providers)  # pydantic schema (str fields only)
    
    # Dynamic vocabulary check: each override must be in the registry
    from .drivers import describe_all as drivers_describe
    from .providers import _OVERRIDE_VOCABULARY
    
    described = drivers_describe()  # {roles, drivers}
    active_ids = {d["id"] for d in described["drivers"]}
    vocab = _OVERRIDE_VOCABULARY | active_ids
    
    for cap in ("autofocus", "polar_align", "solve"):
        override = getattr(cfg, cap)
        if override != "auto" and override not in vocab:
            raise ValueError(f"unknown override '{override}' for {cap}; known: {sorted(vocab)}")
    
    self._config["providers"] = cfg.model_dump()
```

**Validation (resolve-time, in `providers.py:resolve`)**:

```python
def resolve(cap: Capability, hub: Hub) -> ProviderChoice:
    """Resolve a capability to a concrete provider via drivers registry."""
    override = config_store.cfg().providers.get(cap, "auto")
    
    # If override is not valid, degrade to auto (never raise resolve)
    vocab = _OVERRIDE_VOCABULARY | {d["id"] for d in drivers.describe_all()["drivers"]}
    if override not in vocab:
        log.warning(f"Provider override '{override}' for {cap} no longer available; falling back to auto")
        override = "auto"
    
    # ... existing auto-resolution logic, with override checked
```

**Tests:** `server/tests/test_providers.py`
- `test_set_providers_validates_against_registry` — unknown override → ValueError
- `test_solve_capability_exists_and_defaults_auto`
- `test_registry_driven_override_vocabulary_includes_active_drivers`
- `test_resolve_solve_astap_when_available` / `test_resolve_solve_sim_fallback`
- `test_resolve_degrade_stale_override_to_auto_and_warn`

**Code locations:**
- `server/astrodeck/config.py:220` (ProvidersConfig + solve field)
- `server/astrodeck/config.py:220-270` (set_providers validation)
- `server/astrodeck/providers.py:40-80` (registry, resolve-time degradation)
- `server/astrodeck/providers.py:135-180` (resolve adds solve)

---

## Task 2: `_has_real_solver` per-device (JNOW carry-in fix) 

**Implementer:** opus  
**Reviewer:** sonnet  
**Brief — Critical priority:**

Current state (bug): `hub.py:1204` `_mount_expects_jnow = (mode in _REAL_MODES)`, where `_REAL_MODES = ("nina", "alpaca")`. So a mixed rig (mode="nina" because it exists somewhere) with a native Alpaca mount skips J2000↔JNOW precession — arcminute-class pointing error. The fix is per-device, not per-mode.

In `server/astrodeck/hub.py`:

```python
# Lines ~1200-1210, currently:
# _mount_expects_jnow = mode in _REAL_MODES  # WRONG on mixed rigs
# Now:

def _mount_expects_jnow(self) -> bool:
    """Check if the mounted telescope expects J2000→JNOW precession.
    
    Returns True iff the mount device is NOT a simulator. Mixed rigs
    can have one simulator mount + real Alpaca/NINA devices elsewhere,
    so we check the actual mount backend, not the global mode.
    """
    result = self._last_connect_result
    if not result:
        return False
    mount_role_result = result.role_results.get("telescope")
    if not mount_role_result:
        return False
    # The backend for the mount device (not the global mode)
    mount_backend = mount_role_result.backend
    return mount_backend != "sim"
```

Wire it at the call site (`hub.py ~1650`, in `preview_source` or wherever `_mount_expects_jnow` is currently referenced):

```python
# OLD: if hub.mode in ("nina", "alpaca"):  # or hub._mount_expects_jnow with the old logic
# NEW:
if hub._mount_expects_jnow():  # now a method; handles mixed rigs
    frame.dec_j2000 = ...  # apply precession
```

Also update `_preview_source` / any equatorial-math consumers that reference the old `_REAL_MODES` constant.

**Tests:** `server/tests/test_hub_integration.py` (existing, may need adaptation)
- `test_mount_expects_jnow_uses_mount_backend_not_global_mode` — rig with mode="nina" + mount="native" → True; rig with mount="sim" → False
- `test_jnow_precession_applied_correctly_with_native_mount` (E2E: native mount, real target, verify precession magnitude)

**Code locations:**
- `server/astrodeck/hub.py:1204` (change from bool property to method)
- `server/astrodeck/hub.py:~1650` (call site, update to method syntax)
- Search for `_REAL_MODES` and `_mount_expects_jnow` to find all consumers

---

## Task 3: Equipment Tasks UI + resolve contract

**Implementer:** opus (judgment call on how/where to house task rows' state)  
**Reviewer:** sonnet  
**Brief:**

Add `EquipmentTasksSection` component under `EquipmentView.tsx` (mirroring `EquipmentDevicesSection`). One row per task: autofocus, polar_align, solve (guiding stays a device row per the spec).

**Component structure:**

```tsx
interface TaskRow {
  task: Capability;  // "autofocus" | "polar_align" | "solve"
  abbr: string;      // "AF" | "TPPA" | "Plate solve"
  enabled: boolean;  // true if prerequisites met
  error?: string;    // "not installed" / "no camera" / …
  resolved: string;  // concrete provider id: "astrodeck" / "astap" / driver-id / …
}

// server/astrodeck/providers.py adds to resolve_all:
resolve_all(hub) → {
  autofocus: {kind, label, reason},
  polar_align: {kind, label, reason},
  solve: {kind, label, reason},  // NEW
}

// UI reads resolve_all and offers dropdowns of **eligible providers** per task
// (only drivers whose type can handle it: solve→astap|sim, autofocus→backend|astrodeck, etc)
```

In the Equipment tab:

```
DEVICES
  [rows as before]

TASKS
  Autofocus   [Auto ▾]  ◆ AstroDeck native   "native V-curve on camera + focuser"
  Polar Align [Auto ▾]  ◆ AstroDeck native   "three-point on native scope"
  Plate Solve [Auto ▾]  ◆ ASTAP              "ASTAP found; sim fallback available"
```

**Component:** `ui/src/components/EquipmentTasksSection.tsx` — reads `useProviders()` + `status.providers.solve` (NEW), offers override dropdowns + badges.

**Override storage:** extends `AssignmentMap` in `lib/equipment.ts`:

```typescript
export interface EquipAssignments {
  devices: {[role: string]: string};  // role → driver_id (existing)
  tasks: {[cap: string]: string};     // capability → override (NEW: autofocus, polar_align, solve)
}
```

Reads from `/api/status` `providers` field (NEW `solve` entry) and `/api/config/providers` (NEW `solve` field). UI doesn't set overrides directly; it POSTs to the existing `set_providers_config` route (no new endpoint).

**Tests:**
- UI: `ui/src/components/__tests__/EquipmentTasksSection.test.tsx` — render task rows, offer eligible providers
- E2E: connect a rig, verify task rows render with honest enable/disable + error messages, verify override dropdown works

**Code locations:**
- `ui/src/components/EquipmentTasksSection.tsx` (new)
- `ui/src/views/EquipmentView.tsx` (import + render the new section)
- `ui/src/lib/equipment.ts:EquipAssignments` (extend for tasks field)
- `server/astrodeck/api/app.py` (route already exists; no changes)
- `server/astrodeck/providers.py:resolve_all` (already returns full dict; no changes needed)

---

## Task 4: Retire `CapabilitiesCard` + complete provider UI bridge

**Implementer:** opus  
**Reviewer:** sonnet  
**Brief:**

Delete `ui/src/components/settings/CapabilitiesCard.tsx` (now merged into Equipment Tasks section). Search for all imports and usages; they are all in Settings → Connect (old ConnectView/BackendPicker area, now EquipmentView — already gone in P2).

Do a final sweep for any remaining references to:
- `CapabilitiesCard`
- `override` in Settings (old card)
- Legacy provider dropdown vocabulary (`"auto"/"backend"/"astrodeck"`)

Wire the correct provider vocabulary from the registry into the UI **types/client helpers** if any remain hardcoded:

```typescript
// OLD (if it exists in ui/src/):
// const PROVIDER_KINDS = ["auto", "backend", "astrodeck"] as const;
// NEW: derive from server at runtime via /api/drivers (the registry is there)
```

**Tests:**
- Search for dead imports (CapabilitiesCard should have zero references after deletion)
- Confirm Equipment Tasks section has replaced all provider-selection UX

**Code locations:**
- `ui/src/components/settings/CapabilitiesCard.tsx` (DELETE)
- Search repo for `CapabilitiesCard`, `override`, `Capabilities` in UI files
- Any hardcoded vocabulary lists in UI types/helpers

---

## Task 5: Whole-branch review + smoke + ship

**Implementer:** opus whole-branch reviewer  
**Reviewer:** (n/a, this is the gate)  
**Brief:**

Full diff review (6b9c5a3..HEAD): spec compliance, any new critical findings, regressions check (full server suite, UI build, live smoke on fresh server + Equipment tab Tasks section round-trip).

Deferred Minors from P1+P2 (triaged and low-priority):
- T1: `update_driver` error-precedence documentation (unknown field before id lookup)
- T2: `cache-by-ref` if driver CRUD ever mutates the returned Offer dicts (unlikely, but defensive)
- T2: real probe paths for NINA/PHD2 (not stubbed in tests)
- T2: client `config-echo` type safety (ConfigResponse typing)
- T2: `validate_scan_host` hardening on driver write paths (DNS rebind defense, low-risk)
- T2: `extra` field stays non-secret note (documentation)
- P2 T1: `prefail_ordering` doc (what makes a prefail bubble to the top of the LED grid)
- P2 T1: `mode` "phd2" honesty (if phd2 is the only device, what should `mode` report?)
- P2 T2: `all_prefailed_equipConnected` flip (when all drivers are dead, show disconnected state honestly)
- P2 T4: "mixed" profile label suggestion (UI polish)
- P2 T4: legacy-load toast copy (Settings load toast wording)
- P2 T4: `doSaveProfile` busy-gate (prevent double-save)
- P2 T4: guider error wording (parity between device error + task row error)

Gate: **CI green on push** (`gh run watch --exit-status`).

---

## Task Breakdown (TDD per task)

| Task | Role | Code | Files | RED test | Adapt expected |
|------|------|------|-------|----------|----------------|
| 1 | sonnet impl → opus review | solve cap + registry vocab | config.py, providers.py, test_providers.py | `test_set_providers_validates_against_registry` ImportError | override vocab registration path |
| 2 | opus impl → sonnet review | JNOW per-device fix | hub.py, test_hub_integration.py | `test_mount_expects_jnow_uses_mount_backend_not_global_mode` AttributeError | method vs property (async context) |
| 3 | opus impl → sonnet review | Equipment Tasks UI | EquipmentTasksSection.tsx, equipment.ts, EquipmentView.tsx, (server: resolve_all unchanged) | `test_EquipmentTasksSection` ImportError | provider resolve shape confirmation |
| 4 | opus impl → sonnet review | Retire CapabilitiesCard | (delete .tsx, sweep for imports) | cargo-cult check: 0 remaining imports | dead-code verification in test suite |
| 5 | opus whole-branch (gate) | Spec compliance review + live smoke | all | full suite + healthz 200 + Equipment connect round-trip | none; gate is the verdict |

---

## Pre-Task Checklist (for implementers)

- [ ] Spec §3.4 + §4.1 read and understood
- [ ] Base commit `6b9c5a3` checked out locally; suite passing (832)
- [ ] For each task, read the exact code locations linked in the brief
- [ ] TDD: RED test first (exact filename), confirm it fails, then implement
- [ ] Commit message follows `fix(…) | feat(…): <action>` per spec §9
- [ ] No secrets, no breaking changes, no unused imports

---

**Deferral checklist:** Triaged Minors all documented in `.superpowers/sdd/progress.md` at the bottom; Phase 4 can triage+execute if needed.

