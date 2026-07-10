# Rotator / CAA Support — Design

**Date:** 2026-07-10
**Rides on:** `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` (the
Equipment surface; that spec's §"Followed by" names this one)
**Algorithm source:** `docs/native-parity/algorithms/nina-platesolving.md` §11
(rotator sync, mechanical-range target mapping, rotate loop, mod-180 angle equality)
**Hardware:** ZWO CAA (camera angle adjuster), served both through the NINA bridge
and as an Alpaca `rotator` device (ASCOM Remote on the obs machine).

## 1. Goal and scope

Add a first-class `rotator` device role and take it all the way to **full framing
automation**: a target's position angle (PA) is physically enforced by a
solve→sync→rotate loop that runs before centering, in slews, sequences, and
meridian flips. Approved scope decisions:

- **Full automation** (not just a manual card): the rotate loop participates in
  `goto_and_center`, per-target sequence slews, and Atlas "slew & center".
- **Both device paths**: NINA bridge (`NinaRotator`) AND native/Alpaca
  (`AlpacaRotator`), plus `SimRotator` for testing.
- **Range limiting is first-class** (cable-wrap constraint on this rig): NINA-parity
  mechanical range (FULL/HALF/QUARTER + range start), configurable from the UI,
  with honest "this PA will image rotated" warnings whenever the range mapping
  adjusts a target.
- **ROM UI = arc dial + controls**: an SVG arc visualizing the allowed sweep,
  current position and range start, over a segmented FULL/HALF/QUARTER control,
  a range-start field, and a **"Set to current position"** button (range start is
  in mechanical degrees — a number nobody can read off the scope; capturing the
  current physical position is the usable way to set it).

Out of scope (deferred): field de-rotation for alt-az tracking; automatic PHD2
re-calibration after rotation (we only publish a staleness warning, §5.6);
dome/flat-panel roles; per-driver rotator settings (config is global, like
providers).

## 2. Device layer

### 2.1 `Rotator` ABC — `server/astrodeck/devices/base.py`

Mirrors `Focuser` (`base.py:213`). Driver I/O is **mechanical-space**; the
sky↔mechanical sync offset lives **client-side in the base class** (NINA keeps
this in its VM layer too — parity §11.2's `offset = mechanical − sky`). This is a
deliberate deviation from ASCOM driver-side `Sync`: it behaves identically across
IRotatorV2/V3 drivers, the NINA bridge (which exposes no sync), and sim.

```python
class Rotator(Device):
    kind = "rotator"
    can_reverse: bool = False
    _sync_offset_deg: float = 0.0     # mechanical − sky; set by sync()
    _synced: bool = False

    @abstractmethod
    async def get_mechanical_position(self) -> float: ...   # deg [0, 360)
    @abstractmethod
    async def move_mechanical(self, mech_deg: float) -> None: ...
        # absolute mechanical move; WAITS for completion; halts on CancelledError;
        # raises DeviceError if still moving after MOVE_TIMEOUT_S (180 s)
    @abstractmethod
    async def halt(self) -> None: ...
    async def is_moving(self) -> bool: return False

    # sky-space layer (concrete, shared by every backend):
    async def get_position(self) -> float:                   # sky PA
        return _mod360(await self.get_mechanical_position() - self._sync_offset_deg)
    async def sync(self, sky_deg: float) -> None:
        self._sync_offset_deg = _mod360(
            await self.get_mechanical_position() - sky_deg)
        self._synced = True
    async def move_to(self, sky_deg: float) -> None:         # absolute sky move
        await self.move_mechanical(_mod360(sky_deg + self._sync_offset_deg))
```

An **unsynced rotator is never an error**: offset 0 means mechanical = sky, and
both numbers are shown honestly in the UI. The rotate loop syncs after every
solve, so automation never depends on prior sync state.

### 2.2 `AlpacaRotator` — `server/astrodeck/devices/alpaca.py`

`class AlpacaRotator(_AlpacaDevice, Rotator)`, `dev_type = "rotator"`, added to
`DEVICE_CLASSES` (`alpaca.py:662`). Follows `AlpacaFocuser` (`alpaca.py:579`)
exactly: `connect()` probes `canreverse` (probe-once pattern);
`get_mechanical_position` → GET `mechanicalposition`; `move_mechanical` → PUT
`movemechanical` then poll GET `ismoving` every 0.25 s (halt + re-raise on
`CancelledError`, `DeviceError` after 180 s); `halt` → PUT `halt`;
reverse getter/setter → GET/PUT `reverse` (only when `can_reverse`).

### 2.3 `NinaRotator` — `server/astrodeck/devices/nina.py`

`class NinaRotator(_NinaDevice, Rotator)` following the existing `_NinaDevice`
conventions. New `_ROLE_CLASSES["rotator"] = (NinaRotator, "/equipment/rotator/info")`
(`nina.py:810`), rotator added to `_detail()`'s `role_paths` (`nina.py:896`) and to
`NinaBackend.roles` (`nina_backend.py:135`). Endpoints follow the bridge's
`/equipment/rotator/…` surface (info / move); the plan grounds exact paths and
parameter names from how `NinaFocuser` calls its move endpoint in the same file —
the bridge convention is uniform per device. Mechanical position comes from the
info payload; there is no bridge sync (client-side offset covers it, §2.1).

### 2.4 `SimRotator` — `server/astrodeck/devices/sim.py`

- `SimRig` gains `rotator_mech_deg: float = 0.0` and a hidden ground truth
  `rotator_pa_offset_deg: float = 0.0` (how the camera is "clocked" relative to
  mechanical zero). **Default 0.0 so existing solve/TPPA sim tests are
  byte-identical** (the `polar_misalignment` opt-in precedent); rotate-loop
  tests set it non-zero (e.g. 30.0) so they must genuinely discover it via sync.
- `SimRotator(Rotator)` mirrors `SimFocuser` (`sim.py:554`): `MOVE_RATE = 5.0`
  deg/s, animated `move_mechanical` with an `asyncio.Event` halt.
- `build_sim_rig()` (`sim.py:690`) gains `"rotator": SimRotator(rig)`.
- **`SimSolver` reports the physical truth**: its `SolveResult.rotation_deg`
  becomes `_mod360(rig.rotator_mech_deg + rig.rotator_pa_offset_deg)` (plus its
  existing behavior when no rig is attached). This closes the rotate loop
  end-to-end in sim: the loop must solve, sync (discovering the 30° offset), and
  converge on a commanded PA — a real convergence test with no hardware.

### 2.5 Role threading

- `ROLES` (`devices/backend.py:28`) += `"rotator"` (8th role).
- `NativeBackend.roles` (`native_backend.py:151`) += `"rotator"`;
  `_ROLE_TO_DEV_TYPE["rotator"] = "rotator"` (`native_backend.py:25`).
- `SimBackend.roles = ROLES` — automatic.
- `Phd2Backend` unchanged (`("guider",)`).
- `drivers.py:81` `_DEV_TYPE_TO_ROLE["rotator"] = "rotator"` — probing the obs
  machine's Alpaca endpoint now surfaces the CAA as an offer row (this is the
  gap the in-code comment reserves for "the CAA spec").
- `ProfileDevice.role` is an unconstrained `str` — profiles round-trip a rotator
  row with no schema change; update the stale role-list docstring comment
  (`profiles.py:53`).
- `test_backend_roles_match_served.py` invariants extended: native advertises
  only serviceable roles (rotator now maps), sim serves all of `ROLES` (needs the
  `build_sim_rig` key), NINA advertises rotator only with `NinaRotator` present.
- Optional-role guard: **no changes needed** — `orchestrator.py:44` `RoleResult`
  `attempted=False` already renders rotator-less rigs as a grey "skipped" cell,
  never red. An explicitly-assigned rotator that fails to open is genuinely red,
  by design.

## 3. Server: rotation math, config, the rotate loop

### 3.1 Pure math — new `server/astrodeck/rotation.py`

Direct transcription of parity §11.2/§11.4, no I/O, table-testable:

- `mod360(a)`, `angle_equals(a, b, tol)`, `angle_equals_mod180(a, b, tol)`.
- `target_mechanical_position(p, range_type, range_start_deg)` — the
  FULL/HALF/QUARTER mapping (`HALF: p or p+180; QUARTER: p / p+270 / p+180 / p+90`
  by distance-past-range-start bands), verbatim from the parity doc.
- `map_sky_target(sky_target, mech_pos, sync_offset_deg, range_type,
  range_start_deg) -> float` — parity's `get_target_position` composition:
  sky→mechanical via offset, range-map, back to sky.
- `shortest_rotation(distance_deg, range_type) -> (distance, flip180)` — the
  FULL-range "consider the 180°-rotated frame if closer" branch (§11.3).

`range_type` values are the plain strings `"full" | "half" | "quarter"`
everywhere (validated at config write, §3.2 — the `ProviderKind = str` style).

### 3.2 Config — `server/astrodeck/config.py`

`RotatorConfig` model (sibling of `ProvidersConfig`):

```python
class RotatorConfig(BaseModel):
    range_type: str = "full"        # full | half | quarter
    range_start_deg: float = 0.0    # mechanical degrees, [0, 360)
    tolerance_deg: float = 1.0      # rotate-loop tolerance, mod-180, (0, 45]
```

Write-time validation in `ConfigStore.set_rotator(...)` (mirrors
`set_providers`): unknown `range_type`, start outside [0, 360), or tolerance
outside (0, 45] → `ValueError` → HTTP 422. Persisted with the rest of the
config; changes broadcast the same way providers changes do.

### 3.3 The rotate loop — `hub.rotate_to_pa`

```python
async def rotate_to_pa(self, target_pa_deg: float,
                       exposure_s: float = 3.0,
                       max_attempts: int = 5) -> dict
```

NINA §11.3 parity, bounded (NINA loops unbounded; we cap like
`goto_and_center`). Per attempt:

1. Capture + solve exactly as `solve_and_sync` does (solver acquired **up
   front** via `providers.pick_solver` — fails before wasting an exposure;
   frame under `exposure_guard`; preview published). No mount sync — this loop
   only syncs the **rotator**: `await rot.sync(result.rotation_deg)`.
2. `target = map_sky_target(...)` from config; if the range mapping moved the
   target by > 0.1°, `bus.log("warning", ...)` and record `adjusted_to` in the
   result — a +90°/+270° adjustment genuinely changes framing (only +180° is
   equivalent), so it must be surfaced, never silent.
3. `distance, flip = shortest_rotation(target - orientation, range_type)`;
   done when `angle_equals_mod180(distance, 0, tolerance_deg)`.
4. Otherwise `await rot.move_to(orientation + distance)` and loop.

Returns `{rotated, pa_deg, adjusted_to, attempts, error_deg}`. Publishes
`bus.publish("rotator", action="rotating"/"rotated", ...)` per attempt (the
centering-events pattern). Respects the motion-epoch fence between attempts the
same way `goto_and_center` polls it; `CancelledError` halts the rotator.

### 3.4 Integration

- **`goto_and_center(..., rotation_deg: float | None = None)`** (`hub.py:1600`):
  when `rotation_deg is not None` **and** a connected rotator exists, run
  `rotate_to_pa` **before** the centering attempts (NINA CenterAndRotate order).
  A rotate-loop failure (solve fail, `DeviceError`) **degrades**: log a warning,
  set `"rotation_skipped": True` in the result, continue centering — never abort
  a slew that already happened (same philosophy as the existing
  degrade-to-raw-GoTo). No rotator connected → `rotation_deg` is advisory,
  exactly today's behavior.
- **Sequence engine**: the per-target slew passes `Target.rotation_deg`
  (`sequence/models.py:45` — the field already exists as "guidance only";
  its comment updates to reflect enforcement when a rotator is connected).
- **`GotoBody`** (`api/app.py:1791` goto route) gains optional
  `rotation_deg: float | None`; the center path threads it through. Atlas
  "slew & center" sends the framing PA.
- **Meridian flip**: no special handling. `meridian_flip` already funnels
  through `goto_and_center` (passes rotation through); mod-180 equality means
  the post-flip frame (rotated 180°) already satisfies the tolerance check, so
  the loop no-ops. State this in a test.

### 3.5 Safety

1. **Sim-solver motion guard extends to the rotator**
   (`providers._rig_has_real_motion`): a connected rotator with backend in
   `_REAL_BACKENDS` counts as real motion — the simulator solver can never feed
   invented angles to real CAA hardware, through any path (auto, override,
   profile). Same invariant class as Phase 3's guard; adversarially reviewed
   the same way.
2. **Manual moves are refused while an exposure is in flight**: the
   `/api/rotator/move` route checks the hub's active-exposure state and returns
   a clear 409-style error ("exposure in progress"). The rotate loop is
   unaffected (it owns its exposures via `exposure_guard`).
3. **Cancellation halts hardware**: `move_mechanical` halts on
   `CancelledError` (focuser pattern); `rotate_to_pa` respects the motion-epoch
   fence so STOP aborts between attempts and fences a move already in flight.

### 3.6 Guiding staleness note

After a successful `rotate_to_pa` that moved the rotator, if a guider is
connected, publish one `bus.log("warning", "camera rotated — guide calibration
may be stale; PHD2 will re-calibrate or flip as needed", "guide")`. No
automation beyond the warning (deferred).

## 4. API surface

| Route | Body | Behavior |
|---|---|---|
| `POST /api/rotator/move` | `{position_deg}` | Absolute **sky-PA** move; server range-maps first and returns `{target_deg, adjusted}`; refused while exposing (§3.5.2) |
| `POST /api/rotator/halt` | — | Immediate halt |
| `POST /api/rotator/reverse` | `{reverse: bool}` | Only when `can_reverse`; 400 otherwise |
| `POST /api/rotator/rotate-to-pa` | `{target_pa_deg, exposure_s?}` | Spawns `hub.rotate_to_pa` via the `_spawn` pattern (like solve/goto); progress over the bus |
| `POST /api/config/rotator` | `RotatorConfig` fields | 422 on invalid (§3.2) |
| `POST /api/mount/goto` | + optional `rotation_deg` | Threads into `goto_and_center` |

Status (`hub.poll_status`) gains a hand-written block following the focuser
pattern (`hub.py:1977`):

```python
out["rotator"] = {"name", "sky_deg", "mech_deg", "moving",
                  "synced", "can_reverse", "reverse"}
```

RBAC: motion routes require the same operator capability as focuser motion
(`CAP_CONTROL_CAPTURE`); `config/rotator` gated like `config/providers`
(`CAP_CONFIG_BACKEND`). When the role is absent, rotator routes fail cleanly
with **409** via the existing `hub.require` → `DeviceError` → `_err` mapping —
the repo convention for missing devices (amended from 404 during planning).

## 5. UI

### 5.1 `RotatorCard` — new `ui/src/components/equipment/RotatorCard.tsx`

Rendered on the Equipment view (sibling of `TasksPanel`), only when the status
carries a rotator. Contents:

- **Readouts**: sky PA and mechanical position (both — honesty when unsynced),
  moving indicator.
- **Manual control**: move-to-angle input + Go, nudge ±1°/±10°, Halt, reverse
  toggle (rendered only when `can_reverse`).
- **"Rotate to PA (plate solve)"** action → `/api/rotator/rotate-to-pa`.
- **ROM block (approved arc-dial treatment)**: an SVG arc dial showing the
  allowed sweep (shaded per range type + start), the current mechanical
  position marker, and the range-start marker; below it a segmented
  FULL / HALF / QUARTER control, the range-start field with **"Set to current
  position"** (reads `mech_deg` from status, POSTs config), and the tolerance
  field. The dial is display-only (drag interactions rejected — gloved hands at
  the scope); all input goes through the controls.
- **Adjusted-PA warning**: when the entered/target PA range-maps to a different
  framing (≠ +180°), show "⚠ PA N° will image as M°" inline and highlight the
  mapped position on the arc.

### 5.2 Pure helpers — new `ui/src/lib/rotation.ts`

Client mirror of `rotation.py` (`mod360`, `angleEqualsMod180`,
`targetMechanicalPosition`, `mapSkyTarget`, `shortestRotation`) so
adjusted-PA warnings render instantly with no round-trip, in the card, Atlas,
and Sequence. **Tested against the same vectors as the server module** (shared
test table, both HALF and QUARTER branches) via the repo's self-executing
`npx tsx` assert-file convention (no vitest).

### 5.3 Reality-aware banners

- `AtlasView.tsx:580`: the "camera angle is manual" note now branches on
  rotator presence (from store status). Rotator connected → "Camera will rotate
  to PA N° automatically on slew" + adjusted-PA warning when range-mapped;
  absent → today's manual note verbatim.
- `SequenceView.tsx:379`: chip becomes "PA N° · auto" with matching tooltip
  when a rotator is connected; unchanged otherwise.

### 5.4 Role labels & types

- `backendMeta.ts` `ROLE_LABEL["rotator"] = "Rotator"`.
- `BackendLinkGrid.tsx:20` currently holds its **own** hardcoded
  `ROLE_ORDER`/`ROLE_LABEL`; unify it to import from `backendMeta.ts` (targeted
  drift fix — we're touching both anyway).
- `types.ts`/`store.ts`: `status.rotator` block typing, `RotatorConfig` type,
  `GotoBody.rotation_deg`.

## 6. Testing

**Server** (pytest, existing conventions):

- `rotation.py`: table-driven vectors straight from parity §11.2 — FULL
  identity, HALF both branches, QUARTER all four bands, mod-180 equality edge
  cases (0/180/360 wraps, tolerance boundaries).
- `SimRotator`: move/halt/clamp/animation; sync offset math on the ABC.
- `AlpacaRotator`: fake-connection pattern (existing alpaca tests) — verb
  mapping, ismoving poll, halt-on-cancel, canreverse probe, move timeout.
- Rotate loop end-to-end on the sim rig: converges on a commanded PA
  (discovering the hidden 30° clock offset via sync); converges under
  HALF/QUARTER with `adjusted_to` reported; solve-failure degrade; abort fence;
  max-attempts cap.
- `goto_and_center`: rotate-before-center order; `rotation_skipped` on rotate
  failure; rotation ignored with no rotator; meridian-flip mod-180 no-op.
- Motion guard: real rotator connected → sim solver unreachable through auto,
  explicit override, and profile-load paths (Phase 3 test pattern).
- Config: 422s for junk range_type/start/tolerance; round-trip.
- API: move-during-exposure refusal; reverse without can_reverse → 400; RBAC;
  404 with no rotator; rotate-to-pa spawn shape.
- Role invariants: `test_backend_registry.py` ROLES tuple (now 8), roles-match-
  served extensions (§2.5).

**UI**: `rotation.ts` assert file (shared vector table); card/banners logic kept
in pure helpers where testable; `npm run build` clean; no vitest.

**Live validation**: smoke on :8801 (sim rig — card renders, ROM set-to-current,
rotate-to-pa converges, banners flip). Then the at-the-scope checklist with the
real CAA: probe shows the rotator offer → assign → connect → manual move → set
ROM to current → solve-rotate to a target PA.

## 7. Execution

One implementation plan (user's structural choice), executed via
subagent-driven development with per-task review gates and a whole-branch final
review, same machinery as the equipment-drivers phases. The plan grounds every
task in the file:line anchors above.
