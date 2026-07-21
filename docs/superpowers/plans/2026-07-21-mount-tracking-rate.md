# Multi-rate Mount Tracking — Implementation Plan

> **For agentic workers:** implement task-by-task, TDD, commit per task.

**Goal:** Add sidereal/lunar/solar tracking-rate control to the mount, generic +
capability-gated, surfaced as a tri-state segmented control in MountView.

**Architecture:** Base `Telescope` gains a capability flag + two default methods;
AM5N/Alpaca/sim opt in; a new cap-gated API route + status fields; a `SegmentedControl`
UI component wired into MountView.

**Tech stack:** Python 3.12 / FastAPI / pytest (server), React + TS + Tailwind v4 /
vitest (ui). Serial LX200 for the AM5N.

## Global Constraints
- Rate vocabulary is exactly `("sidereal","lunar","solar")`, lowercase, one source of
  truth `TRACKING_RATES` in `devices/base.py`. Never hardcode the list elsewhere.
- NEVER commit real observatory coordinates. Tests use fictional values only.
- Follow the existing capability-flag pattern (`can_pulse_guide`) — no abstract
  method (would break every existing backend/test double).
- Backends that don't support it keep `can_set_tracking_rate=False`; the UI hides the
  control for them. NINA stays False.
- Match surrounding code style; keep the new UI component night-mode-safe (CSS vars,
  no hardcoded colors) and ≥44px tap targets.

---

### Task 1: Base contract + `TRACKING_RATES`

**Files:** Modify `server/astrodeck/devices/base.py`; Test
`server/tests/test_devices_base.py` (create if absent, else append).

**Interfaces — Produces:**
- `TRACKING_RATES: tuple[str,...] = ("sidereal","lunar","solar")` (module scope)
- `Telescope.can_set_tracking_rate: bool = False`
- `async Telescope.set_tracking_rate(rate: str) -> None` — default raises
  `DeviceError(f"{self.name} cannot set tracking rate")`
- `async Telescope.get_tracking_rate() -> str` — default returns `"sidereal"`

- [ ] Test: a bare `Telescope` subclass (minimal test double) has
  `can_set_tracking_rate is False`, `await get_tracking_rate() == "sidereal"`, and
  `set_tracking_rate("lunar")` raises `DeviceError`.
- [ ] Implement the flag + two methods near `pulse_guide`/`guide_rates` (~line 198).
  Place `TRACKING_RATES` at module scope by the other module constants.
- [ ] Run the tests; commit.

---

### Task 2: Sim mount + AM5N driver

**Files:** Modify the sim telescope (find it: `grep -rl "class .*Telescope" server/astrodeck/devices` — likely `devices/sim.py`); Modify
`server/astrodeck/devices/backends/zwo_am5.py`. Tests: the existing sim + AM5N test
modules (`grep -rl "zwo_am5\|SimTelescope" server/tests`).

**Interfaces — Consumes:** Task 1 names. **Produces:** both mounts set
`can_set_tracking_rate=True` and round-trip the rate.

- [ ] Sim test: `set_tracking_rate("solar")` then `get_tracking_rate()=="solar"`;
  invalid rate raises `DeviceError`. Implement: store `self._tracking_rate` (init
  `"sidereal"`), validate against `TRACKING_RATES`.
- [ ] AM5N test (use the existing fake serial link/`request` double): each rate sends
  the correct LX200 command — sidereal→`:TQ#`, lunar→`:TL#`, solar→`:TS#` (verify the
  exact command-token format the driver's `_link.request`/`_cmd_ack` uses, matching
  siblings like `set_tracking`); `get_tracking_rate` returns the cached last-set value
  (init `"sidereal"`); invalid rate raises `DeviceError` and sends nothing.
- [ ] Implement in `zwo_am5.py`: `can_set_tracking_rate=True`, a `_tracking_rate`
  cache attr, `set_tracking_rate` maps name→command and sends via the same mechanism
  as `set_tracking` (reply/ack style consistent with neighbors), then updates the
  cache; `get_tracking_rate` returns the cache.
- [ ] Run tests; commit.

---

### Task 3: Alpaca mount

**Files:** Modify `server/astrodeck/devices/alpaca.py`; Test `server/tests/test_alpaca.py`.

**Interfaces — Consumes:** Task 1. **Produces:** Alpaca mount maps enum↔name and
gates capability off `TrackingRates`.

- [ ] Test (with the existing Alpaca httpx/mock harness): `set_tracking_rate("lunar")`
  PUTs `TrackingRate=1`; `get_tracking_rate` reads the property and maps 0/1/2→names;
  `can_set_tracking_rate` becomes True only when the probed `TrackingRates` collection
  includes lunar(1) and solar(2), else False.
- [ ] Implement: name↔enum map `{"sidereal":0,"lunar":1,"solar":2}` (+ inverse);
  read `TrackingRates` at connect/probe to set the flag; `set`/`get` via the property.
  A mount that only reports sidereal → flag False (control hidden).
- [ ] Run tests; commit.

---

### Task 4: API route + status wiring

**Files:** Modify `server/astrodeck/api/app.py` (route near line 2628) and
`server/astrodeck/hub.py` (mount status dict ~line 2214). Test `server/tests/test_api_mount.py` (or the existing mount-API test module).

**Interfaces — Consumes:** Task 1. **Produces:** `POST /api/mount/tracking_rate?rate=`;
status carries `tracking_rate` + `can_set_tracking_rate`.

- [ ] Test: POST with a valid rate calls `Telescope.set_tracking_rate` and returns
  `{"tracking_rate": rate}`; an unknown rate → 422 (validate against `TRACKING_RATES`
  before calling the device); route is `require(CAP_CONTROL_MOUNT)`-gated and
  `@declare(..., reaches={"Telescope.set_tracking_rate"})`; a viewer principal → 403.
- [ ] Test: the hub mount-status dict includes `tracking_rate` and
  `can_set_tracking_rate` when a mount is connected.
- [ ] Implement both. The route mirrors the `tracking` route's shape (try/except
  `DeviceError` → `_err`). Add the two keys inside the existing connected-mount try
  block in `hub.py`.
- [ ] Run tests; commit.

---

### Task 5: `SegmentedControl` UI component

**Files:** Create `ui/src/components/ui/SegmentedControl.tsx` (or add to the ui
barrel if one exists — check `ui/src/components/ui/index.ts`); Test
`ui/src/components/ui/__tests__/SegmentedControl.test.tsx`.

**Interfaces — Produces:**
```ts
interface SegmentedControlProps<T extends string> {
  options: { value: T; label: string }[];
  value: T | undefined;
  onChange: (v: T) => void;
  disabled?: boolean;
  ariaLabel: string;
}
```

- [ ] Test: renders one button per option; the `value` segment has
  `aria-checked`/active styling; clicking a segment calls `onChange` with its value;
  `disabled` blocks onChange; `role="radiogroup"` + `role="radio"` semantics; keyboard
  (arrow/Enter) selects.
- [ ] Implement as an accessible radiogroup styled as a modern segmented pill —
  design-system tokens only (accent for active, `--line`/`--raise` for the track),
  night-mode-safe, ≥44px targets. No external deps.
- [ ] Run vitest; commit.

---

### Task 6: Wire the rate selector into MountView

**Files:** Modify `ui/src/views/MountView.tsx` (tracking block, lines 111-121) and
`ui/src/types.ts` (`MountState`). Test: extend the MountView test if one exists, else
add a focused render test.

**Interfaces — Consumes:** Tasks 4 (status shape) + 5 (`SegmentedControl`).

- [ ] `types.ts`: add `tracking_rate?: "sidereal"|"lunar"|"solar"` and
  `can_set_tracking_rate?: boolean` to `MountState`.
- [ ] Test: when `m.can_set_tracking_rate` the control renders and reflects
  `m.tracking_rate`; a click posts `/api/mount/tracking_rate?rate=<v>`; when the flag
  is absent/false the control is not rendered; when `!canMount` it is disabled.
- [ ] Implement: render `<SegmentedControl>` under the Tracking toggle, gated on
  `m?.can_set_tracking_rate`, `disabled={!canMount || !m}`, `value={m?.tracking_rate}`,
  onChange → `act(() => api.post(\`/api/mount/tracking_rate?rate=${v}\`))`. Label it
  "Tracking rate". Keep the existing toggle labelled "Sidereal tracking" → relabel to
  just "Tracking" so it no longer implies sidereal-only.
- [ ] Run vitest; commit.

---

### Task 7: Full suite + deploy gate
- [ ] `cd server && python -m pytest -q` (xdist) green.
- [ ] `cd ui && npm test` green; `npm run build` clean.
- [ ] Bundle with the HIGH review fixes (separate batch) into one 0.2.11 deploy.
- [ ] At-scope: confirm AM5N ACKs `:TL`/`:TS`, status reflects the rate, no slew.
