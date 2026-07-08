# Equipment Surface & Backend Drivers — UX + Architecture Design

**Date:** 2026-07-08
**Status:** Approved design (brainstormed with user; supersedes the split
ConnectView / BackendPicker / CapabilitiesCard surfaces)
**Depends on:** pluggable-backend registry (`devices/backend.py`), capability
resolver (`providers.py`), RigSpec connect path (`/api/connect/rig`)
**Followed by:** rotator/CAA support (separate spec — rides on this model)

## 1. Problem

"When does AstroDeck use NINA vs native?" has no clear answer in the UI today.
Three root causes, all verified in the current code:

1. **Two device-connect surfaces with opposite mental models.** The top-level
   Connect tab (`ui/src/views/ConnectView.tsx`) is *mode-centric* ("connect the
   whole rig as Simulator / Alpaca / NINA bridge") with a hardcoded, stale role
   list. The newer Settings → `BackendPicker` is *per-role pluggable* (primary
   + overrides → one `RigSpec`) — the correct model, buried in Settings.
2. **Task routing is a third, detached surface.** `CapabilitiesCard` covers
   only autofocus + polar align, with abstract vocabulary
   (`Auto / Backend / AstroDeck native`) instead of naming the concrete thing
   (NINA, the Rust engine, PHD2, ASTAP). Guiding and plate solve are
   hard-wired with no user choice at all.
3. **Connection addressing is re-typed per role row** instead of being
   configured once, and backends are offered for roles they cannot actually
   fill right now (e.g. NINA offered for a role NINA has nothing connected to).

User requirements (from brainstorm):

- Per-device **completely pluggable**, mixed freely; some slots may be neither
  NINA nor native (PHD2, ASTAP, sim).
- All four cross-cutting tasks user-pluggable: autofocus, polar align,
  guiding, plate solve.
- NINA becomes a **backend driver configured under Settings**; once configured,
  NINA appears as a per-device option **only where NINA actually offers that
  device**.
- Global driver config; per-profile assignments.

## 2. Concept model

Two layers, one rule.

**Drivers** (global, machine-scoped — *how to reach things*): a configured way
to reach equipment or run a task. Declared once in **Settings → Backend
Drivers**:

| Driver | Kind | Config |
|---|---|---|
| NINA instance | configured | host, port, enabled, label |
| Alpaca server (0..n) | configured | host, port, enabled, label |
| PHD2 | configured | host, port, managed flag, enabled |
| Simulator | implicit | always available |
| AstroDeck native (Rust engine) | implicit, detected | wheel importable? |
| ASTAP | implicit, detected | binary found? (path override allowed) |

**Assignments** (per-profile — *who does what*): for each **device slot**
(camera, telescope, focuser, guider, filterwheel, switch, safety, and any
future role such as rotator) and each **task slot** (autofocus, polar align,
plate solve), which driver does it. Profiles reference drivers **by id** and
never store driver connection details.

**The one rule:** a driver appears as an option on a row **only if it is
configured, enabled, reachable, and actually offers that thing** — NINA shows
on the camera row only when its probe reports a connected camera; the Rust
engine shows under autofocus only when the wheel imports.

**Guiding is a device row, not a task row.** The guider role's driver choice
(PHD2 direct vs NINA's guider vs sim) *is* the guiding decision; a separate
task slot would be a second competing surface for the same choice.

## 3. Server design

### 3.1 Driver config + registry

New `drivers` section in the config store (`config.py`):

```json
{
  "drivers": [
    {"id": "nina-a1", "type": "nina",   "host": "astrotown.lan", "port": 1888,
     "enabled": true, "label": "NINA on astrotown", "extra": {}},
    {"id": "alpaca-b2", "type": "alpaca", "host": "192.168.1.50", "port": 11111,
     "enabled": true, "label": "Mount-side Alpaca", "extra": {}},
    {"id": "phd2-c3", "type": "phd2", "host": "127.0.0.1", "port": 4400,
     "enabled": true, "label": "PHD2", "extra": {"managed": false}}
  ]
}
```

- `id`: server-generated, stable, never reused (`<type>-<4 hex>`); `label`
  user-editable.
- CRUD via `POST/PUT/DELETE /api/config/drivers[/{id}]` — RBAC:
  `config.backend` (same cap as the rest of the connect surface).
- Implicit drivers (`sim`, `astrodeck`, `astap`) have **fixed ids**, are not
  stored, and cannot be deleted — only observed (and for ASTAP, given a path
  override).
- Persisted via the existing config store (survives restart; not per-profile).

### 3.2 Availability probe — `GET /api/drivers`

Returns the full option space the Equipment surface renders from:

```json
{
  "roles": ["camera", "telescope", "focuser", "guider",
             "filterwheel", "switch", "safety"],
  "drivers": [
    {"id": "nina-a1", "type": "nina", "label": "NINA on astrotown",
     "enabled": true, "implicit": false,
     "status": {"reachable": true, "error": null, "probed_at": 1751970000.0},
     "offers": {
       "devices": [{"role": "camera", "name": "ASI2600MM"},
                    {"role": "telescope", "name": "EQ6-R"}],
       "tasks": ["autofocus", "polar_align"]
     }},
    {"id": "astrodeck", "type": "astrodeck", "label": "AstroDeck native",
     "enabled": true, "implicit": true,
     "status": {"reachable": true, "error": null, "probed_at": 1751970000.0},
     "offers": {"devices": [], "tasks": ["autofocus", "polar_align"]}}
  ]
}
```

- `roles` is fed from `devices/backend.py:ROLES` so a new role (rotator)
  appears in the UI with zero UI changes.
- **Offers per type:** NINA → roles its Advanced API reports connected, plus
  `autofocus`/`polar_align` task offers; Alpaca → enumerated devices (role,
  `dev_type`, `dev_num`, name); PHD2 → `guider` when reachable; sim → every
  role + both tasks (labelled Simulator); `astrodeck` → `autofocus` +
  `polar_align` when the wheel imports (+ camera/focuser/mount prerequisites
  are reported by the resolver, not here); `astap` → `solve` when the binary
  is found.
- Probe results cached ~15 s; `POST /api/drivers/{id}/probe` forces a
  refresh. Probing never raises: failures land in `status.error`.
- RBAC: `CAP_VIEW_STATUS` to read (viewers see the same surface read-only).
  No precise-site data is involved; hostnames match what `status` already
  exposes.

### 3.3 RigSpec evolution (not replacement)

`ConnSpec` gains `driver_id: str | None`. At connect time the orchestrator
resolves `driver_id` → the configured driver's host/port/extra, then proceeds
exactly as today. Raw host/port ConnSpecs (old profiles, scripts, tests)
continue to work unchanged — `driver_id` is additive back-compat.

### 3.4 Task resolver extensions (`providers.py`)

- **New capability `solve`:** choices ASTAP | sim (| future native solver).
  Call sites that use `solve.get_solver()` route through
  `resolve("solve", hub)`; the existing "SimSolver refuses on real rigs"
  guard becomes the resolver's reason string.
- **Concrete override vocabulary:** stored override values become
  `auto | <driver-id> | astrodeck | astap | sim`. The resolver **keeps
  accepting** legacy `backend` as an alias for "the connected backend's own
  implementation" — no config rewrite, no migration step; the UI simply
  writes the new vocabulary.
- Resolution policy is unchanged: explicit override wins **only when its
  prerequisites are present**, else fall back to auto; `resolve_all` never
  raises; every choice carries a human `reason`.
- `resolve_all` grows the `solve` row so `status.providers` covers all task
  slots the UI shows.

## 4. UI design

### 4.1 New top-level **Equipment** tab (replaces the Connect tab)

One uniform row grammar across two sections:

```
DEVICES                                    (roles fed from /api/drivers)
  ● Camera      [ Mount-side Alpaca ▾ ]   ASI2600MM #0        ✓ linked
  ● Mount       [ NINA on astrotown ▾ ]   EQ6-R               ✓ linked
  ○ Safety      [ — unassigned      ▾ ]                       not filled
TASKS
  ◆ Autofocus   [ Auto ▾ ]  AstroDeck native — "native V-curve on camera + focuser"
  ◆ Polar align [ Auto ▾ ]  NINA — "NINA bridge present — using its TPPA plugin"
  ◆ Plate solve [ Auto ▾ ]  ASTAP — "ASTAP found at C:\Program Files\astap"
RIG ACTIONS
  [Connect Rig]  [Disconnect]  [Simulator rig]      (hold-confirm on real motion)
```

- **Device rows:** the dropdown lists only eligible drivers (per the one
  rule). Picking an Alpaca/NINA driver with multiple matching devices opens
  the enumerated device list (name + `dev_type` + `dev_num`) — **no host/port
  typing on this surface, ever**. Row shows live LED (from
  `status.backend_links`), connected device name, inline per-role connect
  error (`RoleResult`).
- **Task rows:** dropdown = `Auto` + concrete available providers; resolved
  badge (`ProviderBadge`) + the resolver's `reason` always visible.
- **Rig actions:** Connect Rig compiles assignments → `RigSpec` →
  existing `/api/connect/rig`; hold-to-confirm when the resolved rig includes
  a real mount/focuser (`MOTION_ROLES` rule carried over); one-tap Simulator
  rig preserved from ConnectView.
- Read-only for viewers (same `Gated`/caps pattern as today).

### 4.2 Settings → **Backend Drivers** (replaces BackendPicker's card)

- Add/edit/enable/disable/delete configured drivers; label editing.
- Discovery buttons move here: NINA network scan, Alpaca UDP discovery,
  manual host query — discovery *results create or fill driver entries*
  (reusing today's discovery components).
- Per-driver readout: probe status LED, last error, and the **offers** list
  ("camera: ASI2600MM · telescope: EQ6-R · tasks: autofocus, TPPA").
- Implicit drivers shown with detection state (native wheel version /
  ASTAP path) so "why isn't native offered" is answerable at a glance.

### 4.3 Component disposition

| Component | Fate |
|---|---|
| `views/ConnectView.tsx` | **Retired** (nice bits — sim one-tap, NINA blurb, discovery — absorbed) |
| `settings/BackendPicker.tsx` | **Retired** (its RigSpec-building logic moves behind the Equipment tab) |
| `settings/CapabilitiesCard.tsx` | **Retired** (superseded by Tasks section) |
| `settings/BackendLinkGrid.tsx` | **Kept** — embedded in Equipment as the live tri-state readout |
| `ProviderBadge`, discovery lists, `confirmDialog` hold-confirm | **Kept / reused** |

### 4.4 Profiles

A profile snapshot = device assignments (`role → {driver_id, device selector}`)
+ task overrides. The device selector is driver-typed: for Alpaca
`{dev_type, dev_num}` (which enumerated device on the server); for NINA, PHD2
and sim it is empty (the driver itself is unambiguous per role). Loading a profile re-renders Equipment with its assignments;
Connect Rig applies them. Existing profiles (raw host/port RigSpecs) keep
connecting via back-compat; on first save from the new surface they upgrade to
driver-id form.

## 5. Failure honesty

- **Driver unreachable** → its options grey out with the probe error;
  **assignments are sticky** — the user's choice persists, resolves
  "unavailable" with the reason, and re-lights when the driver returns.
- **Assignment references a deleted driver** → row shows "driver removed",
  falls back to unassigned; never silently reconnects elsewhere.
- **NINA configured but unreachable** → one visible banner on Equipment; NINA
  simply absent from dropdowns (never a dead entry).
- **Task rows** keep the resolver `reason` permanently visible — "why is this
  on NINA right now" always has an answer.
- Probe failures, enumeration failures, and connect failures are three
  distinct, per-row surfaced states (probe error on the driver, missing offer
  on the dropdown, `RoleResult.error` inline after connect).

## 6. RBAC & remote

- Driver CRUD + assignments + connect: `config.backend` (unchanged cap).
- `GET /api/drivers`: `CAP_VIEW_STATUS`; content parity with existing
  `status` exposure (no new sensitive data class).
- Remote viewers over the relay see Equipment read-only exactly as they see
  status today; the 60 s auth re-check and redaction layer apply unchanged.

## 7. Phasing (three PR-sized phases)

1. **Drivers foundation:** config schema + CRUD + probe API
   (`/api/drivers`), Settings → Backend Drivers panel (discovery moved in),
   implicit-driver detection. No behavior change to connect paths.
2. **Equipment tab — devices:** new tab with the Devices section + rig
   actions, assignments compiled to `RigSpec` (`driver_id` ConnSpecs),
   ConnectView + BackendPicker retired, `BackendLinkGrid` embedded.
3. **Equipment tab — tasks:** Tasks section, resolver `solve` capability,
   concrete override vocabulary (legacy `backend` still accepted),
   CapabilitiesCard retired.

The rotator/CAA spec follows as its own design and drops into this surface as
one more server-fed device row.

## 8. Testing

- **Server:** driver CRUD + persistence round-trip; probe → offers mapping per
  driver type (NINA enumeration, Alpaca enumeration, PHD2 reachability,
  wheel/ASTAP detection, all mocked); `driver_id` → host/port resolution incl.
  raw-spec back-compat; `solve` resolution matrix (ASTAP present/absent ×
  real/sim rig); legacy `backend` alias acceptance; RBAC denials (403 without
  `config.backend`).
- **UI (vitest):** option-population logic (the one rule) from `/api/drivers`
  fixtures; sticky-assignment behavior across driver outage; RigSpec
  compilation from assignments; task dropdown writes concrete vocabulary.
- **E2E (sim):** configure zero drivers → Simulator rig one-tap → all rows
  live; full pass through the new surface on the sim rig in CI.
- **Live validation (grounded):** on astrotown — NINA driver configured,
  camera offered via both NINA and Alpaca, mixed assignment connects, task
  rows resolve with honest reasons. Not claimed done until run.

## 9. Out of scope

- Rotator/CAA support (next spec, rides on this).
- ASIAIR backend, native plate solver, NINA-free release bundling
  (Rust-wheel-in-CI — separate pending work).
- Instant (push) remote-viewer eviction; unchanged 60 s re-check.
