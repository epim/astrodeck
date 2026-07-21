# Native-hardware config on-ramp — Plan

> Fixes the UX-review headline: native USB/serial backends (zwo-am5, player-one,
> zwo-asi, zwo-usb, wanderer-snowflake) are unreachable in the device-config UI.
> Plus two secondary UX fixes (guider label, add-driver copy).

**Goal:** Let a naive user discover and configure their real native hardware from
the UI, so it becomes assignable in the Equipment dropdowns and round-trips
through profiles — with no hand-crafted profile.

**Design decision (route):** Use the **configured-driver (`driver_id`) route**, NOT
implicit rows. Rationale (from the seam map): the config/API/connect/profile
machinery already fully supports a native serial/local driver
(`DriverEntry.transport/port_path`, `add_driver`, `DriverCreateBody`,
`resolve_driver_ids` carrying transport+port_path, the `driver_id` path in
`buildRigSpec`, and `configurable_driver_types()` already includes all five native
`driver_type`s). The ONLY blockers are: (a) no probe for serial/local driver types
→ they sit `reachable:False` → `eligibleDrivers()` hides them; (b) the Add-driver UI
hardcodes nina/alpaca/phd2. The implicit-row route would need new client
raw-addressing plumbing AND breaks profile-save (ProfileDevice has no port_path).

**Architecture:** (1) a native-hardware probe in `drivers.py` that runs the
backend's own `discover()` to confirm presence + offer its roles; (2) a "Scan for
USB/serial hardware" flow in DriversPanel that discovers across the hardware
backends and one-tap-adds each as a configured driver; (3) copy + label fixes.

## Global Constraints
- NEVER commit real observatory coordinates. Tests use fictional values only.
- `discover()` is non-invasive on all five backends (serial `list_ports` / SDK
  `count()`, never `open()`) — safe to call while a device is connected. Confirmed
  in the seam map.
- Reuse the existing driver_id machinery — do NOT add a raw-addressing client path.
- The five native driver_types + their transports: zwo-am5=serial, wanderer-
  snowflake=serial, zwo-usb=local, zwo-asi=local, player-one=local. Roles per
  backend: zwo-am5→telescope; zwo-usb→rotator,focuser; wanderer-snowflake→
  filterwheel; zwo-asi→camera,guide_camera; player-one→camera,guide_camera.
- Native-probe results are cached exactly like the network probes (`_CACHE`,
  `PROBE_TTL_S=15`), and the probe NEVER raises (degrades to reachable:False).

---

### Task 1 — Server: native-hardware probe (makes configured native drivers reachable + eligible)

**Files:** Modify `server/astrodeck/drivers.py`. Test `server/tests/test_drivers_native_probe.py` (new) + extend `server/tests/test_drivers_api.py` if present.

**Interfaces — Produces:** a configured driver whose `type` is a native hardware
`driver_type` probes via that backend's `discover()`:
- serial (zwo-am5, wanderer-snowflake): `reachable=True` iff a discovered entry's
  `port_path == entry.port_path` (else reachable False, error `"no device on <port>"`).
- local (zwo-usb, zwo-asi, player-one): `reachable=True` iff `discover()` returned
  ≥1 entry (else error `"no <label> detected"`).
- `offers.devices` = `[{"role": r, "name": entry.label or <discovered name>} for r
  in backend.roles]`; `offers.tasks = []`. `detail` = discovered device name/port.

**Steps (TDD):**
- [ ] Test: a fake backend registered with `hardware=True`, `driver_type="fake-hw"`,
  `roles=("telescope",)`, `transport="serial"`, and a `discover()` returning
  `[{"role":"telescope","name":"Fake","port_path":"COM9"}]`. A configured
  `DriverEntry(type="fake-hw", transport="serial", port_path="COM9")` →
  `_probe_configured` returns `reachable=True`, `offers.devices==[{"role":"telescope","name":...}]`.
  A mismatched `port_path="COM7"` → reachable False. A local-transport fake with
  `discover()` returning one entry (no port) + entry.transport="local" → reachable True.
  `discover()` raising → reachable False, no exception escapes.
- [ ] Implement in `_probe_configured` (`drivers.py:221-261`): when
  `entry.type not in _PROBES` AND `entry.type in driver_type_to_backend()` (a
  registry hardware type), branch to a new `await _probe_native(entry)` instead of
  the neutral "no network probe" row. `_probe_native(entry)`: get the backend
  (`from .devices.backend import get_backend`), `await backend.discover()` guarded;
  compute reachable per transport; build offers from `backend.roles`. Cache the
  result in `_CACHE` like the network path.
- [ ] Run the new tests; commit.

---

### Task 2 — Client types: expose hardware backends + widen the driver form

**Files:** Modify `ui/src/types.ts` (`BackendInfo`, `AddDriverBody`/addDriver args),
`ui/src/api/backends.ts` (`addDriver`, add `discoverBackend` already exists).
Test: extend `ui/src/lib/__tests__/backends.test.ts` if it asserts the shape.

**Interfaces — Produces:**
- `BackendInfo` gains `hardware?: boolean`, `driver_type?: string`,
  `transport?: string` (mirrors `list_backends()` which already returns them).
- `addDriver` body widened: `type: string` (not the nina|alpaca|phd2 union),
  optional `transport?: "network"|"serial"|"local"`, `port_path?: string`.

**Steps:**
- [ ] Widen `BackendInfo` + the `addDriver` signature/body type. `listBackends()`
  already exists (`GET /api/backends`); confirm it returns hardware/driver_type.
- [ ] `tsc -b` clean; commit.

---

### Task 3 — Client: "Scan for USB/serial hardware" in DriversPanel

**Files:** Modify `ui/src/components/settings/DriversPanel.tsx`,
`ui/src/components/settings/driversMeta.ts` (labels/ports for native types).

**Interfaces — Consumes:** Task 2 types; `discoverBackend(name)` (existing,
`GET /api/discover/{name}` — works for any backend); `listBackends()`.

**Steps:**
- [ ] Add a "Scan for USB / serial hardware" button (alongside/near the existing
  network Add form). On click: `listBackends()` → filter `b.hardware &&
  b.discoverable` → `Promise.all` `discoverBackend(b.name)` for each → flatten to
  `{backend, driver_type, transport, role, name, port_path?}` rows.
- [ ] Render found devices as a list; each row shows name + port (if serial) +
  "Add" button; plus an "Add all" button. "Add" → `addDriver({type: driver_type,
  transport, port_path, label})`. Skip devices whose (type+port_path) is already
  configured. After add, `reload()` (existing pattern).
- [ ] If nothing found: "No USB/serial hardware detected — check the cable/power."
- [ ] Update the empty-state copy (`DriversPanel.tsx:181-186`) + the Add-driver
  intro to mention USB/serial hardware, not just NINA/Alpaca/PHD2.
- [ ] `tsc -b` clean; run touched tests; commit.

---

### Task 4 — Guider label clarity

**Files:** Modify `ui/src/components/settings/backendMeta.ts` (`ROLE_LABEL`).

**Steps:**
- [ ] Relabel `guider: "Guider"` → `guider: "Guiding"` (or "Guide method") to
  reduce the visual near-collision with "Guide camera" in the Devices list. The
  roles are genuinely distinct (guiding *algorithm* vs physical *camera*) — this is
  a label-only change, no structural merge (per the seam-map recommendation).
- [ ] `tsc -b` clean; run `backends.test.ts`; commit.

---

### Task 5 — Suite, deploy, at-scope validation

- [ ] `cd server && pytest -q -n auto` green; `cd ui && npx tsc -b && npm run build` green.
- [ ] Bump 0.2.14, build release, deploy to astrotown.
- [ ] AT-SCOPE payoff test (the whole point): reset astrotown to naive again
  (backup), log in via token (Playwright), open Settings → Connect → "Scan for
  USB/serial hardware", confirm the REAL AM5N/Poseidon/ASI220/EAF/CAA/Snowflake are
  detected + addable, add them, confirm they now appear + are assignable in the
  Equipment dropdowns, then RESTORE the box (rig 6/6).

## Out of scope (note as follow-ups)
- One-tap "Detect hardware rig" on Equipment (scan+auto-add+auto-assign in one tap).
- Per-unit `index` addressing for multiple identical USB cameras (discover() emits
  no index today).
- Editing a serial driver's `port_path` in place (DriverPatchBody lacks the field).
