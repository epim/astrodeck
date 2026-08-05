# PRO-4 — Auto roof/dome close on unsafe + end-of-night

Combined design spec + TDD implementation plan. One file.

Feature: a **Dome / roll-off-roof device role** (open / close / slave /
park-and-close) wired into the **safety-abort path before the pause-hold** and
into the **end-of-night finalize**, so a rain/cloud event *closes the roof over
the parked gear* rather than parking the mount under open sky. The close
ordering is correctness-critical: the roof must never travel through the mount.

v1 scope: the Dome role (device ABC + `SimDome` + role registration) + the
correctness-critical **park-and-close ordering state machine** + the safety-abort
close wiring + the end-of-night close + a thin SafetyPanel roof-status/close
affordance. Real Alpaca/COM `Dome` *client* and auto-**reopen**-on-safe-again are
noted follow-ups.

> Shares the new-device-role pattern with **PRO-5 (F-F flat/cover role)**.
> Designed against the CURRENT tree; implementer sequences **after PRO-5** and
> reconciles the two `ROLES` / `_DEV_TYPE_TO_ROLE` additions (identical edit
> sites — see §1.3 and Open Decision D1).

---

## 1. Design

### 1.1 Goal

When a `SafetyMonitor` (rain/cloud) trips, or the night finishes, an
observatory with a motorized roll-off roof should **close the roof** — not merely
park the mount under an open sky (today's behavior) and not merely pause-hold
(today's `on_unsafe="pause"` behavior). Closing a roll-off roof over an
**unparked** mount would crush the OTA, so the reaction is a *park-and-close*
whose ordering is invariant: **park the mount clear → confirm parked → close the
roof → confirm CLOSED**, and if the mount can't be confirmed parked, **refuse to
close** (a wet scope beats a crushed one) and alert loudly.

### 1.2 Current-state seams (real file:line, all read)

**Device layer**
- `server/astrodeck/devices/base.py:67` `Device(ABC)` — common lifecycle;
  `describe()` at `:98`. Role classes follow (`Camera:112`, `Telescope:167`,
  `SafetyMonitor:402`, `Rotator:293`). `SafetyReading` dataclass at `:386`. New
  `Dome` role slots in beside these.
- `server/astrodeck/devices/base.py:263` `Telescope.stop()` /
  `Telescope.park():208` / `is_parked():214` — the mount side of the ordering
  guard.

**Role registration / offers (three parallel maps — the "device-role pattern")**
- `server/astrodeck/devices/backend.py:31` `ROLES` tuple (the canonical role
  vocabulary; `SimBackend.roles = ROLES`).
- `server/astrodeck/drivers.py:85` `_DEV_TYPE_TO_ROLE` (Alpaca probe → role);
  note the comment at `:83` "genuinely unknown types (e.g. `dome`) are still
  SKIPPED".
- `server/astrodeck/devices/ascom_registry.py:35` `_DEV_TYPE_TO_ROLE` (COM host);
  comment at `:34` "dome/covercalibrator/observingconditions … host-side only".
  `ASCOM_TYPES` at `:27` already lists `"Dome"`.
- `server/astrodeck/devices/backends/native_backend.py:30` `_ROLE_TO_DEV_TYPE`
  and `get_device():77` — `alpaca.DEVICE_CLASSES.get(dev_type)` at `:102`; there
  is **no** `DEVICE_CLASSES["dome"]` yet (real Alpaca dome client = follow-up).
- `server/astrodeck/drivers.py:159` sim `_implicit_rows` offers
  `[{"role": r, …} for r in ROLES]` — a new role auto-appears in the Equipment
  offers with zero UI change.

**Connect orchestration**
- `server/astrodeck/devices/orchestrator.py:99` `_requested_roles` =
  `set(spec.roles) | set(get_backend(primary).roles)`; `:206` opens each
  endpoint once and `:227` `session.get_device(role, conn)` — graceful per-role
  degrade (a `KeyError` → failed `RoleResult`, never a crash).
- `server/astrodeck/hub.py:609` `for role in ROLES: … self.devices[role] = dev`
  — the hub connects every role the result rig carries. `require():781`,
  `safety` property `:787`.

**Simulator**
- `server/astrodeck/devices/sim.py:1004` `build_sim_rig()` returns the coherent
  `role -> device` dict (adds `safety`, `rotator`, etc.). `SimRig:171` shares
  `parked` (`:178`) across devices. `SimTelescope.park():743` sets
  `rig.parked = True`; `is_parked():751`. `SimSafetyMonitor:963` +
  `force_unsafe()/force_safe()` (`:983/:988`) — the enforcement pattern to
  mirror. `SimBackend` adapter: `server/astrodeck/devices/backends/sim_backend.py:169`
  (`roles = ROLES`; `SimSession.get_device` indexes `self._rig[role]` at `:80`).

**Safety-abort path (the wiring targets)**
- `server/astrodeck/sequence/engine.py:1367` `_on_unsafe()` — the branch point:
  `warn`→return (`:1390`), `abort_park_warm`/`park`→`raise SafetyAbort` (`:1392`),
  `pause`→`_park_hold()` (`:1396`, the **pause-hold**) then a resume loop. **The
  roof-close escalation goes at the top of this method, BEFORE the pause-hold.**
- `server/astrodeck/sequence/engine.py:643` `_run`'s `except SafetyAbort` →
  `_finalize_report("unsafe")` then a **shielded** `_wind_down(...)` at `:655`
  that completes before cancellation.
- `server/astrodeck/sequence/engine.py:2054` `_wind_down(park, warm)` — stop
  guiding → (fenced+locked) `tel.park()` at `:2086` → warm cooler. **The roof
  close is added here, after the park.** Motion fence: `bump_motion_epoch()`
  (`hub.py:1106`) + `hub._motion_lock` (`hub.py:258`).
- Callers of `_wind_down`: `engine.py:622` (cooling_skip), `:642`
  (complete/dawn), `:655` (unsafe, shielded), `:680` (quality).
- `_park_hold():1444` (tracking-off only — the pause hold), `_safe_stop():2038`.

**Config / plan**
- `server/astrodeck/config.py:101` `SafetyConfig` (`on_unsafe:120`,
  `enforce_pier_limits:110`, presets `SAFETY_PRESETS:92` `backyard`/`remote`).
  Set wholesale via `set_safety` (`api/app.py:1583`). **New flags land here.**
- `server/astrodeck/sequence/models.py:95` `park_when_done` /
  `warm_cooler_when_done` (plan-level wind-down toggles).

**Comhost scaffolding (already present — confirmed)**
- `server/astrodeck/comhost/handlers_aux.py:39-51` — a full **Dome COM↔Alpaca
  handler** (`shutterstatus`, `openshutter`, `closeshutter`, `park`, `slaved`,
  `abortslew`, `slewtoazimuth`) already exists, "host-side only … excluded from
  ascom_registry offers (no assignable role)". So a bundled COM Dome is already
  serveable over Alpaca; only the AstroDeck-side role + client are missing.

**UI**
- `ui/src/components/settings/SafetyPanel.tsx` — the Settings→Safety surface
  (today: sun-avoidance). Echoes the **full** `SafetyConfig` block on save
  (`:83`), re-hydrates via `useStore.getState().loadConfig()` (`:90`). Uses
  `Panel/Field/Toggle` (`:29`), `confirmDialog` (`:28`), honest-disabled note
  pattern (`:258`). **Roof status + close-on-unsafe toggles + manual close land
  here.**
- `ui/src/types.ts:840` `SafetyConfig` interface. `ui/src/lib/__tests__/eta.test.ts`
  — the tsx inline-assert idiom.
- `server/astrodeck/api/app.py:1885` `/api/safety/state`, `:2778` `/api/mount/park`
  (the fenced-park route to mirror for `/api/dome/close`).

### 1.3 Approach

**Data / device protocol.** Add a `Dome` role to `devices/base.py` mirroring
`SafetyMonitor`:

```python
class DomeShutterState(enum.Enum):
    OPEN = "open"; CLOSED = "closed"; OPENING = "opening"
    CLOSING = "closing"; UNKNOWN = "unknown"; ERROR = "error"

class Dome(Device):
    kind = "dome"
    #: A roll-off roof whose travel passes THROUGH the mount's volume: the mount
    #: MUST be parked clear before the roof may close. True is the FAIL-SAFE
    #: default (assume a collision is possible). A classic rotating dome whose
    #: shutter clears the OTA at any orientation may set this False.
    requires_park_before_close: bool = True
    can_slave: bool = False
    # abstract: shutter_state / open_shutter / close_shutter
    # default DeviceError: set_slaved; default False: get_slaved; abort() no-op
    # derived: is_open() / is_closed() off shutter_state()
```

Add `"dome"` to `backend.ROLES`, `drivers._DEV_TYPE_TO_ROLE`,
`ascom_registry._DEV_TYPE_TO_ROLE` (all three parallel maps). Add a `SimDome`
to `sim.py` and to `build_sim_rig()`.

**Behavior — the correctness-critical close-ordering state machine.** A pure,
engine-free async function `close_observatory(dome, telescope, …) -> bool` in a
new `sequence/roof.py` (import-light: no `engine`/`hub` import, so it unit-tests
against sim doubles directly). Contract:

1. If already `CLOSED` → return `True` (idempotent).
2. If `dome.requires_park_before_close` **and** a connected telescope exists:
   confirm `telescope.is_parked()` is `True`. **If not confirmed parked → REFUSE:
   log an error, return `False`, never call `close_shutter`.** (Never crush the
   mount. `close_observatory` does **not** itself issue the park — mount motion
   stays owned by `_wind_down`'s fenced+locked park, which runs first.)
3. Close: `dome.close_shutter()`, then confirm `shutter_state() is CLOSED`. Any
   failure/timeout → log + return `False`.

All device calls wrapped in local `asyncio.wait_for` (log + return `False` on
timeout — we are tearing down; never re-raise, matching `_wind_down`'s
"log + continue" philosophy at `engine.py:2054`).

The `SimDome` makes the ordering **self-checking**: `close_shutter()` reads
`rig.parked`; closing while unparked sets `ERROR` and raises
`DeviceError("collision")`. So a test that bypassed the guard would trip the sim.
The guard test asserts the sim was *never* touched (state stayed `OPEN`).

**Wiring point A — end-of-night + unsafe teardown (`_wind_down`).** New signature
`_wind_down(park, warm, close_dome=False)`. After the existing fenced park block,
if `close_dome and a connected dome is present`, call `close_observatory` and, on
`False`, page loudly (best-effort dispatcher + error log). Because
`close_observatory` re-confirms `is_parked` and the park ran first, a
failed/timed-out park → `is_parked False` → **refuse** — the ordering invariant
holds by construction. Callers:
- `engine.py:622/642/680` (cooling_skip / complete / dawn / quality) →
  `close_dome=bool(self._cfg and self._cfg.safety.close_dome_when_done)`.
- `engine.py:655` (unsafe, shielded) →
  `close_dome=bool(self._cfg and self._cfg.safety.close_dome_on_unsafe)`, and
  keep the existing `park=True`.

**Wiring point B — `_on_unsafe`, BEFORE the pause-hold.** At the top of
`_on_unsafe` (after the `record_safety` + `bus.publish("safety", …)` at
`:1382-1388`, before the `warn`/`pause`/`abort` branches): if a connected dome
is present and `cfg.safety.close_dome_on_unsafe`, `raise SafetyAbort(reason +
" — closing roof")`. This *escalates every* `on_unsafe` action (including
`pause`) to the shielded park-and-close teardown, so a closeable roof is never
left open under rain while the run pause-holds. We do **not** auto-reopen on
safe-again (Open Decision D3 — a follow-up).

**Config.** Two additive `SafetyConfig` flags (default `False` → every existing
rig/test byte-identical; opt-in like the `rotator_pa_offset`/`polar_misalignment`
precedent):
- `close_dome_on_unsafe: bool = False`
- `close_dome_when_done: bool = False`

Both live in `SafetyConfig` (not the plan) so the SafetyPanel — which already
echoes the whole safety block — is the single edit surface and **no
`SequencePlan`/TS-fixture churn** is required. The `remote` preset MAY set
`close_dome_on_unsafe=True` (Open Decision D2).

**UI.** SafetyPanel gains a small "Observatory roof" section: a read-only shutter
status line (from a new `GET /api/dome/state`), two toggles (`close_dome_on_unsafe`,
`close_dome_when_done`), and a manual **Close roof now** button
(`POST /api/dome/close` → same `close_observatory`). Honest-disabled (§11.8) when
no dome is connected. A pure `domeStatusLabel(state)` formatter is tsx-tested.

### 1.4 Placement summary

| Concern | File |
|---|---|
| `Dome` ABC + `DomeShutterState` | `server/astrodeck/devices/base.py` |
| Role registration (3 maps) | `backend.py`, `drivers.py`, `ascom_registry.py` |
| `SimDome` + `build_sim_rig` | `server/astrodeck/devices/sim.py` |
| Close-ordering state machine | `server/astrodeck/sequence/roof.py` (new) |
| Engine wiring (`_wind_down`, `_on_unsafe`, `_run`) | `server/astrodeck/sequence/engine.py` |
| Config flags | `server/astrodeck/config.py` |
| Routes `/api/dome/{state,close}` | `server/astrodeck/api/app.py` |
| SafetyPanel roof section | `ui/src/components/settings/SafetyPanel.tsx` |
| TS `SafetyConfig` + `domeStatusLabel` | `ui/src/types.ts`, `ui/src/lib/dome.ts` (new) |

---

## 2. Global Constraints (verbatim)

- **Privacy.** The real site coordinates `<REDACTED-LAT>` / `<REDACTED-LON>` and the
  label "<REDACTED-SITE-LABEL>" NEVER appear in code, tests, or docs. Site default is
  "My Observatory" / `0.0`.
- **Never `git add -A`.** Stage explicit paths only.
- **UI gate:** `cd ui && npx tsc -b` must pass.
- **NO jsdom.** Pure-logic UI tests run via `npx tsx` inline-assert, idiom
  `ui/src/lib/__tests__/eta.test.ts`.
- **Backend tests:** `server/.venv/Scripts/pytest.exe`, run from repo root,
  `-n0` (single worker).
- **Device drivers** follow the existing `base.py` `Device`/ABC + the
  `_DEV_TYPE_TO_ROLE` + hub-singleton pattern.
- **Client toasts** via `useStore.getState().enqueueToast`.
- **Honest-disabled (§11.8):** dim + lock + `aria-disabled` + `title`; never the
  native `disabled` attribute.
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Interfaces are exact. Every task: write the failing test first, then the minimum
code, then run the named command and confirm the expected output.

### Interfaces (exact signatures)

```python
# server/astrodeck/devices/base.py
import enum
class DomeShutterState(enum.Enum):
    OPEN = "open"; CLOSED = "closed"; OPENING = "opening"
    CLOSING = "closing"; UNKNOWN = "unknown"; ERROR = "error"

class Dome(Device):
    kind: str = "dome"
    requires_park_before_close: bool = True
    can_slave: bool = False
    async def shutter_state(self) -> DomeShutterState: ...        # abstract
    async def open_shutter(self) -> None: ...                     # abstract
    async def close_shutter(self) -> None: ...                    # abstract
    async def abort(self) -> None: ...                            # default no-op
    async def set_slaved(self, on: bool) -> None: ...             # default DeviceError
    async def get_slaved(self) -> bool: ...                       # default False
    async def is_closed(self) -> bool: ...                        # derived
    async def is_open(self) -> bool: ...                          # derived
    def describe(self) -> dict[str, Any]: ...                     # + can_slave, requires_park_before_close

# server/astrodeck/devices/sim.py
class SimDome(Dome):
    def __init__(self, rig: SimRig, name: str = "Sim Roll-Off Roof") -> None: ...
    # close_shutter() raises DeviceError("collision") when not rig.parked (self-checking)

# server/astrodeck/sequence/roof.py  (import-light: NO engine/hub import)
DOME_CLOSE_TIMEOUT_S: float = 180.0
DOME_QUERY_TIMEOUT_S: float = 30.0
async def close_observatory(
    dome, telescope, *,
    log,                       # callable(level: str, msg: str, source: str) -> None  (bus.log)
    close_timeout_s: float = DOME_CLOSE_TIMEOUT_S,
    query_timeout_s: float = DOME_QUERY_TIMEOUT_S,
) -> bool: ...                 # True iff shutter confirmed CLOSED; False = refused/failed (caller alerts)

# server/astrodeck/config.py  (SafetyConfig, additive)
close_dome_on_unsafe: bool = False
close_dome_when_done: bool = False

# server/astrodeck/sequence/engine.py
async def _wind_down(self, park: bool, warm: bool, close_dome: bool = False) -> None: ...
```

```ts
// ui/src/lib/dome.ts
export type DomeShutter = "open" | "closed" | "opening" | "closing" | "unknown" | "error";
export function domeStatusLabel(s: DomeShutter): string;   // e.g. "closed" -> "Roof closed"
export function domeIsClosed(s: DomeShutter): boolean;

// ui/src/types.ts SafetyConfig  (additive)
close_dome_on_unsafe: boolean;
close_dome_when_done: boolean;
```

---

### Task 1 — `Dome` role ABC + `DomeShutterState` (base.py)

**Impl tier: Sonnet** (mechanical; mirrors `SafetyMonitor`).

Files: `server/astrodeck/devices/base.py`, `server/tests/test_dome_role.py` (new).

Steps:
1. Test first — a minimal in-file `FakeDome(Dome)` implementing the three
   abstracts; assert `kind == "dome"`, `requires_park_before_close is True`,
   `is_closed()` derives from `shutter_state()`, `set_slaved` raises
   `DeviceError`, `describe()["kind"] == "dome"` and carries `can_slave`.
2. Add `DomeShutterState` enum + `Dome(Device)` per the Interfaces block.
   `is_closed`/`is_open` call `shutter_state()`; `describe()` = `super().describe()`
   plus `can_slave` and `requires_park_before_close`.

Command: `server/.venv/Scripts/pytest.exe server/tests/test_dome_role.py -n0 -q`
Expected: `4 passed` (or the exact count you write), no errors.

---

### Task 2 — `SimDome` + `build_sim_rig` wiring (sim.py)

**Impl tier: Opus** (the self-checking collision model is the sim half of the
correctness contract — subtle: the sim must raise on unparked-close so the
ordering guard is provable, and stay byte-identical for every existing sim test
that doesn't touch the dome).

Files: `server/astrodeck/devices/sim.py`, `server/tests/test_sim_dome.py` (new).

Interface:
```python
class SimDome(Dome):
    def __init__(self, rig: SimRig, name: str = "Sim Roll-Off Roof") -> None:
        super().__init__(name)
        self.rig = rig
        self._state = DomeShutterState.OPEN     # a roll-off roof starts OPEN (imaging)
        self.requires_park_before_close = True
        self.can_slave = False
        self._halt = asyncio.Event()

    async def connect(self) -> None:
        await asyncio.sleep(0.02); self.connected = True
    async def disconnect(self) -> None:
        self.connected = False
    async def shutter_state(self) -> DomeShutterState:
        return self._state
    async def open_shutter(self) -> None:
        self._state = DomeShutterState.OPENING
        await asyncio.sleep(0.02)
        self._state = DomeShutterState.OPEN
    async def close_shutter(self) -> None:
        # SELF-CHECKING collision model: a roll-off roof closing over an unparked
        # mount crushes the OTA. The sim REFUSES with an error so the close-
        # ordering guard (roof.close_observatory) is provable end to end.
        if not self.rig.parked:
            self._state = DomeShutterState.ERROR
            raise DeviceError("roof closed onto an unparked mount (collision)")
        self._state = DomeShutterState.CLOSING
        await asyncio.sleep(0.02)
        self._state = DomeShutterState.CLOSED
    async def abort(self) -> None:
        self._halt.set()
```
Add `SimDome(rig)` under key `"dome"` in `build_sim_rig()`'s returned dict
(`sim.py:1006`), import it from `.base`.

Tests:
- open→close after `rig.parked = True` → `CLOSED`; ordering byte-check.
- `close_shutter()` while `rig.parked is False` → raises `DeviceError`, state
  `ERROR`.
- `is_closed()` / `is_open()` derive correctly; `set_slaved(True)` raises.

Command: `server/.venv/Scripts/pytest.exe server/tests/test_sim_dome.py -n0 -q`
Expected: all pass.

---

### Task 3 — role registration in the three parallel maps

**Impl tier: Sonnet** (three one-line dict additions + `ROLES` tuple entry).

Files: `server/astrodeck/devices/backend.py` (`ROLES` + `"dome"`),
`server/astrodeck/drivers.py:85`, `server/astrodeck/devices/ascom_registry.py:35`
(add `"dome": "dome"`); `server/tests/test_dome_registration.py` (new).

> Coordinate with PRO-5: both features add to `ROLES` and the two
> `_DEV_TYPE_TO_ROLE` maps. Land the union; do not clobber PRO-5's `"cover"`
> entries (Open Decision D1).

Tests:
- `"dome" in backend.ROLES`.
- `drivers._DEV_TYPE_TO_ROLE["dome"] == "dome"` and
  `ascom_registry._DEV_TYPE_TO_ROLE["dome"] == "dome"`.
- `ascom_registry.enumerate_offers_with(fake_reader_with_a_dome)` now yields a
  `role == "dome"` entry (was previously skipped).

Command: `server/.venv/Scripts/pytest.exe server/tests/test_dome_registration.py -n0 -q`
Expected: all pass.

> Note the connect blast-radius (see Surprises): with `"dome"` in `ROLES` and a
> `SimDome` in `build_sim_rig`, every sim rig now connects `hub.devices["dome"]`.
> Any existing test asserting an exact device-key set must add `"dome"`.

---

### Task 4 — `close_observatory` ordering state machine (roof.py, NEW)

**Impl tier: Opus** (the one genuinely correctness-critical unit — the
never-crush-the-mount invariant. Justified: an ordering bug here physically
destroys hardware.)

Files: `server/astrodeck/sequence/roof.py` (new), `server/tests/test_roof_close.py`
(new). `roof.py` imports only `asyncio` + `DomeShutterState` from
`..devices.base` (no engine/hub — keeps it unit-testable and cycle-free).

```python
async def close_observatory(dome, telescope, *, log,
                            close_timeout_s=DOME_CLOSE_TIMEOUT_S,
                            query_timeout_s=DOME_QUERY_TIMEOUT_S) -> bool:
    """Close a roll-off roof/dome WITHOUT stranding the mount in its path.

    Returns True iff the shutter is confirmed CLOSED. Returns False (never
    raises) when it REFUSES to close — the mount could not be confirmed parked —
    or the close failed/timed out, so the caller can page. Mount MOTION is NOT
    performed here: the caller (_wind_down) parks the mount, fenced+locked,
    FIRST; this only VERIFIES parked before moving the roof."""
    try:
        if await asyncio.wait_for(dome.is_closed(), query_timeout_s):
            return True
    except Exception:
        pass  # fall through and try to close
    if getattr(dome, "requires_park_before_close", True) and telescope is not None \
            and getattr(telescope, "connected", False):
        try:
            parked = await asyncio.wait_for(telescope.is_parked(), query_timeout_s)
        except Exception:
            parked = False
        if not parked:
            log("error", "roof close REFUSED: mount not parked (would collide) "
                         "— roof left OPEN", "safety")
            return False
    try:
        await asyncio.wait_for(dome.close_shutter(), close_timeout_s)
    except Exception as e:
        log("error", f"roof close failed: {e}", "safety")
        return False
    try:
        st = await asyncio.wait_for(dome.shutter_state(), query_timeout_s)
    except Exception:
        st = None
    if st is not DomeShutterState.CLOSED:
        log("error", "roof close did not confirm CLOSED — gear may be exposed", "safety")
        return False
    log("info", "roof closed", "safety")
    return True
```

Tests (drive with `SimDome` + `SimTelescope` on a shared `SimRig`; a tiny
`log=lambda *a: logs.append(a)` spy):
1. **Refuse-when-unparked:** `rig.parked = False` → returns `False`,
   `dome._state` stayed `OPEN` (close_shutter NEVER called), an `error` log
   recorded. *(Proves the invariant.)*
2. **Park-then-close ordering:** `await tel.park()` (→ `rig.parked True`), then
   `close_observatory` → returns `True`, `dome._state == CLOSED`.
3. **Idempotent:** dome already `CLOSED` → returns `True` immediately (no park
   query needed).
4. **`requires_park_before_close = False`:** unparked mount, dome closes anyway
   → `True` (a rotating dome whose shutter clears the OTA).
5. **No telescope** (`telescope=None`): closes → `True`.
6. **Close failure:** monkeypatch `dome.close_shutter` to raise → returns
   `False`, error logged.

Command: `server/.venv/Scripts/pytest.exe server/tests/test_roof_close.py -n0 -q`
Expected: all pass.

---

### Task 5 — config flags (config.py + TS)

**Impl tier: Sonnet** (additive fields).

Files: `server/astrodeck/config.py` (`SafetyConfig` + two `bool = False`
fields, after `max_pause_min:124`), `ui/src/types.ts:840` (`SafetyConfig` +
two `boolean`), `server/tests/test_config_dome_flags.py` (new).

Tests:
- default `SafetyConfig()` → both flags `False`.
- round-trip: `set_safety(SafetyConfig(close_dome_on_unsafe=True))` then read →
  `True` (mirrors an existing `set_safety` test).
- an old config JSON without the keys deserializes (both default `False`).

Command: `server/.venv/Scripts/pytest.exe server/tests/test_config_dome_flags.py -n0 -q`
Expected: all pass. (TS gate deferred to Task 8.)

---

### Task 6 — engine wiring: `_wind_down` + `_on_unsafe` + `_run`

**Impl tier: Opus** (touches the shielded teardown ordering and the safety
branch point — subtle control flow; must preserve the existing fenced-park,
shielded-wind-down, and pause/abort semantics exactly while inserting the close).

Files: `server/astrodeck/sequence/engine.py`,
`server/tests/test_engine_dome_close.py` (new).

Edits:
1. `_wind_down(self, park, warm, close_dome=False)` (`:2054`). After the park
   block (`:2094`, before `if warm`):
```python
if close_dome:
    dome = self.hub.devices.get("dome")
    if dome is not None and getattr(dome, "connected", False):
        from .roof import close_observatory
        tel = self.hub.devices.get("telescope")
        ok = await close_observatory(dome, tel, log=bus.log)
        if not ok:
            bus.log("error", "AUTOMATED ROOF CLOSE FAILED — gear may be exposed",
                    "safety")
            disp = self._get_dispatcher()
            if disp is not None:
                try:
                    await disp.emit_alert("roof close failed", level="error")
                except Exception:
                    pass
```
   *(Use whatever the dispatcher's alert method is named at the wiring site;
   guard it — best-effort, never raises.)*
2. Thread `close_dome` through the four callers:
   - `:622`, `:642`, `:680` →
     `close_dome=bool(self._cfg and self._cfg.safety.close_dome_when_done)`.
   - `:655` (shielded unsafe) → add
     `close_dome=bool(self._cfg and self._cfg.safety.close_dome_on_unsafe)`.
3. `_on_unsafe` (`:1367`), immediately after `bus.log("error", …)` at `:1388`,
   before `if act == "warn"`:
```python
dome = self.hub.devices.get("dome")
if (cfg and cfg.safety.close_dome_on_unsafe
        and dome is not None and getattr(dome, "connected", False)):
    # A closeable roof must CLOSE over the gear, not pause-hold under open sky.
    # Escalate to the shielded park-and-close teardown (roof close rides
    # _wind_down). No auto-reopen on safe-again (follow-up).
    raise SafetyAbort(f"{reason} — closing roof")
```

Tests (build a sim hub with a `SimDome`; use `SimSafetyMonitor.force_unsafe`;
drive a 1-frame plan or call `_wind_down` directly):
- **end-of-night close:** `cfg.safety.close_dome_when_done=True`, plan runs to
  complete → after wind-down `rig.parked is True` and dome `CLOSED`.
- **flag off:** `close_dome_when_done=False` → dome stays `OPEN`.
- **unsafe close (pause preset):** `on_unsafe="pause"`,
  `close_dome_on_unsafe=True`, monitor forced unsafe → run ends `aborted`
  `end_reason="unsafe"`, dome `CLOSED`, `rig.parked True`. *(Proves "BEFORE the
  pause-hold": the run did NOT pause-hold with the roof open.)*
- **ordering under abort:** assert the mount was parked before the dome reached
  `CLOSED` (the `SimDome` collision model guarantees this — a wrong order would
  raise inside `close_shutter` and the run would surface the collision error).
- **no dome present:** `close_dome_on_unsafe=True` but no dome connected → today's
  pause/abort behavior unchanged (regression guard).
- **park fails → refuse:** monkeypatch `SimTelescope.park` to leave
  `rig.parked False` → dome stays `OPEN`, "ROOF CLOSE FAILED" error logged, no
  crash.

Command:
`server/.venv/Scripts/pytest.exe server/tests/test_engine_dome_close.py -n0 -q`
Expected: all pass. Also run the existing safety/wind-down suites to confirm no
regression:
`server/.venv/Scripts/pytest.exe server/tests/test_sequence_safety.py server/tests/test_sequence_engine.py -n0 -q`
(names per the repo; expected: still green after adding `"dome"` to any exact
device-set assertions).

---

### Task 7 — routes `/api/dome/state` + `/api/dome/close`

**Impl tier: Sonnet** (mirrors `/api/safety/state` and the fenced `/api/mount/park`).

Files: `server/astrodeck/api/app.py`, `server/tests/test_dome_routes.py` (new).

```python
@app.get("/api/dome/state", dependencies=[Depends(require(CAP_VIEW_STATUS))])
async def dome_state():
    dome = hub.devices.get("dome")
    if dome is None:
        return {"connected": False, "shutter": "unknown",
                "requires_park_before_close": True, "can_slave": False}
    return {"connected": bool(getattr(dome, "connected", False)),
            "shutter": (await dome.shutter_state()).value,
            "requires_park_before_close": bool(dome.requires_park_before_close),
            "can_slave": bool(dome.can_slave)}

@app.post("/api/dome/close", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
@declare(CAP_CONTROL_MOUNT, reaches={"Dome.close_shutter", "Telescope.park"})
async def dome_close():
    dome = hub.devices.get("dome")
    if dome is None or not getattr(dome, "connected", False):
        raise _err(DeviceError("no dome connected"))
    # Manual close is a motion-committing park-and-close: fence in-flight gotos,
    # park under the motion lock, THEN close (reusing the tested ordering guard).
    hub.bump_motion_epoch()
    async def _run():
        tel = hub.devices.get("telescope")
        async with hub._motion_lock:
            if tel is not None and getattr(tel, "connected", False) \
                    and getattr(dome, "requires_park_before_close", True):
                await tel.park()
            from ..sequence.roof import close_observatory
            return await close_observatory(dome, tel, log=bus.log)
    return _spawn("goto", _run(), replace=True)
```
Reuse `CAP_CONTROL_MOUNT` (roof close moves the mount) — see Open Decision D4.

Tests (FastAPI `TestClient`, sim rig): `GET /api/dome/state` → `shutter == "open"`;
`POST /api/dome/close` on a sim rig with the mount parkable → eventual
`shutter == "closed"`; `POST` with no dome → 4xx `DeviceError`.

Command: `server/.venv/Scripts/pytest.exe server/tests/test_dome_routes.py -n0 -q`
Expected: all pass.

---

### Task 8 — UI: SafetyPanel roof section + `dome.ts` + types

**Impl tier: Sonnet** (thin render + one pure formatter; correctness lives in
the typechecker + the tsx logic test).

Files: `ui/src/lib/dome.ts` (new), `ui/src/lib/__tests__/dome.test.ts` (new),
`ui/src/types.ts` (`SafetyConfig` fields — Task 5 already), a `getDomeState()` +
`closeDome()` in `ui/src/api/backends.ts` (mirror existing calls), and a new
"Observatory roof" section in `SafetyPanel.tsx`.

`dome.ts`:
```ts
export type DomeShutter = "open" | "closed" | "opening" | "closing" | "unknown" | "error";
export function domeStatusLabel(s: DomeShutter): string {
  switch (s) {
    case "open": return "Roof open";
    case "closed": return "Roof closed";
    case "opening": return "Roof opening…";
    case "closing": return "Roof closing…";
    case "error": return "Roof error";
    default: return "Roof status unknown";
  }
}
export function domeIsClosed(s: DomeShutter): boolean { return s === "closed"; }
```

SafetyPanel section: a status line `domeStatusLabel(shutter)`; two `Toggle`s
bound to `close_dome_on_unsafe` / `close_dome_when_done` (persist via the same
full-block echo at `SafetyPanel.tsx:83`); a **Close roof now** button. When no
dome is connected, the button and toggles are honest-disabled (§11.8: dim +
`aria-disabled` + `title="Connect a dome/roof device first"`, NOT native
`disabled`). Manual-close click → `closeDome()` then
`useStore.getState().enqueueToast(...)` on success/failure.

Tests:
- `dome.test.ts` (tsx inline-assert, eta.test.ts idiom): `domeStatusLabel` for
  each state; `domeIsClosed`.
  Command: `cd ui && npx tsx src/lib/__tests__/dome.test.ts`
  Expected: prints the pass tally, exit 0, `0 failed`.
- Typecheck gate: `cd ui && npx tsc -b`
  Expected: exits 0, no errors.

---

### Suggested task order

1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. (4 depends on 2 for its sim double; 6 depends on
4+5; 7 depends on 4; 8 depends on 5+7.) Tasks 1 and 5 are independent and may
run in parallel with early work.

---

## 4. Open decisions (with recommendations)

**D1 — `ROLES` / `_DEV_TYPE_TO_ROLE` merge with PRO-5.** Both PRO-4 (`dome`) and
PRO-5 (`cover`/flat) add to the same three maps and to `ROLES`.
*Recommendation:* since the implementer sequences PRO-4 **after** PRO-5, land
PRO-4's entries as additive edits on top of PRO-5's (union, never replace); the
Task-3 tests assert `"dome"` specifically so a lost PRO-5 entry is caught by
PRO-5's own tests. No shared helper needed — three literal dicts.

**D2 — should the `remote` preset enable `close_dome_on_unsafe`?** A remote
observatory with a roof is exactly the case that wants auto-close.
*Recommendation:* **Yes** — add `close_dome_on_unsafe=True` to
`SAFETY_PRESETS["remote"]` (`config.py:96`). Keep `backyard` and the base
default `False` (a supervised backyard operator may not want a surprise roof
move). This is opt-in-by-preset, still `False` for every existing config.

**D3 — auto-reopen on safe-again?** The `pause` path today re-acquires the target
when conditions clear; a closed roof would need reopening first.
*Recommendation:* **Defer** (follow-up). Auto-reopening a roof on a brief clear
is risky (a passing cloud gap → roof cycles). v1 closes and ends the night
(dormant/resumable); the user reopens manually. Note this in the release notes.

**D4 — capability for the manual close route.** Closing the roof parks the mount.
*Recommendation:* reuse **`CAP_CONTROL_MOUNT`** (the close *is* a mount-motion
action) rather than minting a `CAP_CONTROL_DOME` for one route in v1. Revisit if
PRO-5 or a future dome-slew feature adds more dome control surface.

**D5 — real Alpaca/COM `Dome` client.** The COM host already serves a Dome
(`handlers_aux.py`); AstroDeck lacks `alpaca.DEVICE_CLASSES["dome"]` and a
`native_backend` mapping, so only the `SimDome` connects in v1.
*Recommendation:* **Defer** the real client to a follow-up (PRO-4b). v1 ships the
role + ordering + wiring, fully exercised on the sim; wiring a real Alpaca Dome
client is then a drop-in (the `_DEV_TYPE_TO_ROLE` maps already route `dome` →
role, so only the client class + `DEVICE_CLASSES` entry are missing).

**D6 — one shared config flag vs two.** Could collapse to a single
`auto_close_dome` covering both unsafe and end-of-night.
*Recommendation:* **Keep two** (`close_dome_on_unsafe`, `close_dome_when_done`).
A user may want protective close-on-rain but prefer to leave the roof open at a
normal end-of-night (to keep imaging on a resume, or dew management). Two flags,
both `False`, cost nothing and read honestly in the panel.
