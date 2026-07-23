# PRO-5 — Automated flat acquisition (ADU-target auto-exposure) + CoverCalibrator device (F-F)

Combined design spec + TDD implementation plan. One shippable, tested v1.

---

## 1. Design

### 1.1 Goal

Give AstroDeck first-class flat-frame acquisition without a vendor app:

1. **ADU-target auto-exposure solver** — a pure, bounded, iterative solver that,
   given a measured mean/median ADU at a trial exposure, proposes the next
   exposure that lands the frame on a chosen target ADU (e.g. 25 000). This is
   the tested core.
2. **CoverCalibrator device role (F-F)** — a new vendor-neutral device ABC for a
   flat panel (brightness on/off) + optional motorized cover, plus a
   `SimCoverCalibrator` so the whole path is exercisable with zero hardware. The
   ABC + sim ship now; a real Alpaca/ASCOM backend is a noted follow-up (the
   host-side Alpaca surface already exists — see §1.2).
3. **A flat sequence step** — a calibration Flat step that, when an `adu_target`
   is set, turns the panel on, runs the solver loop to find the per-filter
   exposure, then captures the requested count of flats at the solved exposure,
   and turns the panel off at the end.

**Deferred to a noted follow-up (§4):** dusk/dawn *sky*-flat twilight re-solve
(the sky changes brightness continuously, so exposure must be re-solved every
frame). v1 targets a *panel* (constant illumination), where a single solve holds
for the whole step. The solver core is written to be reused by the twilight
follow-up unchanged.

### 1.2 Current-state seams (real file:line, all read)

**Device ABCs + how a role is declared**
- `server/astrodeck/devices/base.py:67-109` — `Device` ABC: `kind`, `connect`/
  `disconnect`, `describe()`. New device classes subclass this.
- `server/astrodeck/devices/base.py:269-290` (`Focuser`) and `:293-345`
  (`Rotator`) — the idiom for a role: `kind = "..."`, class-level capability
  flags (e.g. `Focuser.supports_native_autofocus:277`), `@abstractmethod` core
  ops, and optional methods that `raise DeviceError` by default
  (`Focuser.get_temperature:289`, `Rotator.set_reverse:325`). CoverCalibrator
  follows this shape exactly.
- `server/astrodeck/devices/base.py:374-383` (`Switch`) and `:402-421`
  (`SafetyMonitor`) — the two most recent role additions; `SafetyMonitor` shows a
  minimal ABC with one `@abstractmethod` + a default wrapper. CoverCalibrator ABC
  is inserted after `SafetyMonitor` (after line 421).

**Role registry (the role table wired through the framework)**
- `server/astrodeck/devices/backend.py:31-41` — `ROLES` tuple: the single source
  of truth for the roles a rig fills (`camera … rotator, guide_camera`). Adding
  `"covercalibrator"` here is the F-F "new role wired through the framework".
- `server/astrodeck/devices/backends/sim_backend.py:178` — `SimBackend.roles =
  ROLES` (the same object), so the sim backend automatically offers/fills every
  role in `ROLES`. `test_sim_backend_self_registered` (`tests/test_sim_backend.py:34`)
  asserts `b.roles == ROLES` — stays green because it's the same object.
- `server/astrodeck/devices/backends/sim_backend.py:73-86` — `SimSession.get_device`
  returns `self._rig[role]`, i.e. whatever `build_sim_rig()` keyed under that role.
- `server/astrodeck/devices/orchestrator.py:185-218` +
  `_requested_roles():100-114` — `connect_profile` requests `get_backend(primary).roles`
  (∪ overrides), groups by endpoint, opens one session, hands out each role's
  device. A new role in `ROLES` is requested for the sim primary with no other change.
- `server/astrodeck/hub.py:292-301` — `connect_sim` iterates `for role in ROLES`,
  `dev = result.rig.get(role)`, `await dev.connect()`, `self.devices[role] = dev`.
  A `covercalibrator` device in the sim rig is connected here automatically.
- `server/astrodeck/hub.py:359-362` (`connect_nina`) and the Alpaca path request
  only their backend's `roles`; NINA does not list covercalibrator and the Alpaca
  probe map (below) omits it, so real backends never try to fill it in v1.

**Existing CoverCalibrator scaffolding (confirms the brief's note — surprise #1)**
- `server/astrodeck/comhost/handlers_aux.py:1-64` — host-side Alpaca handlers for
  `covercalibrator` (`coverstate`/`calibratorstate`/`brightness`/`maxbrightness`,
  `opencover`/`closecover`/`calibratoron`/`calibratoroff`) ALREADY exist. Module
  docstring (`:1-6`): "AstroDeck has no ABC/client for these yet (they belong to
  the later flats/dome/weather waves)." So the comhost can already *serve* a
  CoverCalibrator; we are building the *consumer* (ABC + sim + role).
- `server/astrodeck/devices/ascom_registry.py:27-39` — `ASCOM_TYPES` already lists
  `"CoverCalibrator"` (enumerable), but `_DEV_TYPE_TO_ROLE:35-39` deliberately
  omits it ("host-side only … no AstroDeck role"). Same omission in
  `server/astrodeck/drivers.py:85-89` (`_DEV_TYPE_TO_ROLE` for the Alpaca probe).
  We leave both maps unchanged in v1 (see §1.5 / surprise #2).

**Capture path + how a frame's mean ADU is available (the measurement seam)**
- `server/astrodeck/hub.py:1386-1446` — `Hub.capture(exposure_s, gain, offset,
  binning, save=False, target="", frame_type="Light")`. `save=False` skips the
  FITS write; it still exposes and calls `_publish_preview`, returning `info`.
- `server/astrodeck/hub.py:1485` — `info["stats"] = frame_stats(data)` rides on
  EVERY returned capture dict.
- `server/astrodeck/imaging/processing.py:174-181` — `frame_stats(data)` →
  `{"min","max","mean","median","std"}`. `median` (int) and `mean` (float) are
  exactly the ADU measurement the solver consumes; no new plumbing needed
  (surprise #5).
- `server/astrodeck/imaging/__init__.py:1-11` — `frame_stats` is exported.

**Sim rig (add a SimCoverCalibrator)**
- `server/astrodeck/devices/sim.py:1004-1017` — `build_sim_rig()` returns the
  role→device dict (`camera`, `guide_camera`, `telescope`, …, `safety`, `_rig`).
  We add `"covercalibrator": SimCoverCalibrator(rig)`.
- `server/astrodeck/devices/sim.py:171-226` — `SimRig.__init__` opt-in-knob idiom
  (`guide_star_hidden:226`, `rotator_pa_offset_deg:186`, `polar_misalignment:197`):
  default-off state a device/test tunes, leaving existing renders byte-identical.
  We add `self.flat_illumination = 0.0` (0 = off).
- `server/astrodeck/devices/sim.py:398-423` — `SimCamera._render`; the sky term at
  `:421` (`img += (8.0 + 14.0*yy) * seconds * (...)`) is already *linear in
  exposure*, so the solver converges end-to-end even with no panel model
  (surprise #4). We add a gated uniform `flat_illumination * seconds` term so the
  sim panel is *observable* (turning it on brightens frames), default-off keeps
  existing frames byte-identical.
- `server/astrodeck/devices/sim.py:927-960` (`SimSwitch`) — a minimal non-camera
  sim device (connect/disconnect + a couple of methods); the shape SimCoverCalibrator mirrors.

**Sequence model + engine (how a flat step is requested + run)**
- `server/astrodeck/sequence/models.py:10-20` — `ExposureStep` already carries
  `frame_type: str = "Light"` (comment: `Light | Dark | Bias | Flat`). We add two
  additive optional fields: `adu_target` and `panel_brightness` (surprise #3:
  flats already run; only the auto-exposure + panel is missing).
- `server/astrodeck/sequence/engine.py:1013-1041` — `_run_calibration(ti, target)`:
  the calibration path (target.calibration=True) that runs Dark/Bias/**Flat**
  steps today with a *fixed* `step.exposure_s`, per-step count loop, no quality
  gate. This is where the ADU solve + panel control plug in.
- `server/astrodeck/sequence/engine.py:1002-1011` — `_capture(step, target)` wraps
  `hub.capture(..., save=True, frame_type=step.frame_type)` under `_bounded`.
- `server/astrodeck/sequence/engine.py:110-119` — `_bounded(awaitable, timeout_s,
  what)`; `:74-93` device-I/O timeout constants. Every engine device await is bounded.

**API route pattern**
- `server/astrodeck/api/app.py:2604-2612` — `dew_heater` route: `@app.post(...,
  dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])`, `@declare(...)`,
  `hub.require(role)` + device call, `except DeviceError: raise _err(e)`. Calibrator
  routes mirror this exactly.

**UI seams**
- `ui/src/types.ts:426-437` — `ExposureStep` TS interface (mirror of the pydantic
  model). Add `adu_target?` + `panel_brightness?`.
- `ui/src/views/SequenceView.tsx:40` — `FRAME_TYPES = ["Light","Dark","Flat","Bias"]`
  (Flat already selectable); `:778-800` the per-step editor grid; `:772` the
  `isLightStep` gate — the model for a `frame_type === "Flat"` conditional field.
- `ui/src/components/settings/backendMeta.ts:10-37` — `ALL_ROLES` (drives the
  Equipment assignment rows) + `ROLE_LABEL` (`ROLE_LABEL[role] ?? role` fallback,
  `EquipmentView.tsx:553-554`). Device roles are typed as plain `string`
  (`EquipmentView.tsx:104,516`; `types.ts:669`), so a new role never breaks tsc.

### 1.3 Approach — data

- **`ExposureStep` additive fields** (`sequence/models.py`, mirrored in `types.ts`):
  - `adu_target: int = 0` — 0 = off (behave exactly as today: fixed `exposure_s`).
    `> 0` and `frame_type == "Flat"` ⇒ run the ADU solver before the count loop.
  - `panel_brightness: int | None = None` — panel level to set while shooting this
    step (None = don't touch the panel; user runs sky/manual flats).
  - Defaults preserve every existing saved plan verbatim.

- **`SimRig.flat_illumination: float = 0.0`** — uniform ADU/sec the sim panel adds
  when on (opt-in knob, default 0 leaves renders byte-identical).

### 1.4 Approach — behavior / algorithm (the tested core)

**Pure solver** `server/astrodeck/imaging/flats.py` (no numpy, no I/O):

Flat signal above the bias pedestal is ~proportional to exposure, so the
next-exposure estimate is a single linear ratio, clamped and iterated:

```
next = last_exposure * (target_adu - pedestal) / (measured - pedestal)
```

- Converged when `measured ∈ [target*(1-tol), target*(1+tol)]` → accept
  `last_exposure`.
- **Rail detection:** if `measured` is too bright and we are already at
  `min_exposure_s` → `too_bright_at_min`; too dim at `max_exposure_s` →
  `too_dim_at_max`. No-signal (`measured - pedestal ≤ ε`) pushes toward
  `max_exposure_s`.
- **Iteration guard:** after `max_iterations` un-converged updates, stop and
  return the *closest* exposure tried so far (`max_iterations`, `converged=False`).
- Deterministic, side-effect-free ⇒ pytest with a synthetic linear panel model.

**Measurement choice:** use `info["stats"]["median"]` (robust to the stars/dust a
real flat and the sim both render into a light frame; a flat is near-uniform so
median ≈ the flat level while rejecting the few star pixels). See §4 decision 1.

**Engine loop** (in `_run_calibration`, per Flat step with `adu_target > 0`):

```
1. panel on (if covercalibrator connected AND panel_brightness set); open cover if has_cover
2. solver = FlatExposureSolver(target=adu_target, initial=step.exposure_s, min=…, max=…)
   exp = solver.first().exposure_s
   loop (bounded per capture):
       info = hub.capture(exp, gain, offset, binning, save=False, frame_type="Flat")   # trial, not saved
       st = solver.update(info["stats"]["median"])
       publish detail "metering flat: <median> ADU @ <exp>s"
       if st.done: break
       exp = st.exposure_s
3. if st.converged: capture ×count at st.exposure_s (save=True)     # the kept flats
   else: log a warning; fall back to step.exposure_s for the count (still usable frames)
4. panel off; close cover (if has_cover)   — also on abort/wind-down (best-effort)
```

Panel-off is also wired into the engine's teardown (`_safe_stop`/`_wind_down`) so
an aborted run never leaves the panel lit.

### 1.5 Approach — device protocol (CoverCalibrator ABC)

Vendor-neutral, modeled on ASCOM ICoverCalibratorV1 but with AstroDeck-style
string states (like `PierSide`):

```python
class CoverState(enum.Enum):
    NOT_PRESENT = "not_present"; CLOSED = "closed"; MOVING = "moving"
    OPEN = "open"; UNKNOWN = "unknown"; ERROR = "error"

class CoverCalibrator(Device):
    kind = "covercalibrator"
    max_brightness: int = 1        # panel reports its own; on/off-only panel = 1
    has_cover: bool = False        # not every flat panel has a motorized cover
    @abstractmethod async def get_brightness(self) -> int: ...
    @abstractmethod async def calibrator_on(self, brightness: int) -> None: ...
    @abstractmethod async def calibrator_off(self) -> None: ...
    async def get_calibrator_state(self) -> str: ...      # "off" | "ready" | …
    async def get_cover_state(self) -> CoverState: return CoverState.NOT_PRESENT
    async def open_cover(self) -> None:  raise DeviceError(f"{self.name} has no cover")
    async def close_cover(self) -> None: raise DeviceError(f"{self.name} has no cover")
    def describe(self) -> dict: {**super, "max_brightness", "has_cover"}   # static caps only (sync)
```

`SimCoverCalibrator` holds `_brightness`/`_on`, a cover state (starts CLOSED,
`has_cover=True`), and on `calibrator_on` sets `rig.flat_illumination` so the sim
camera brightens. Tested against this sim double (a fake-SDK-equivalent).

### 1.6 Placement

| Concern | File | New / edit |
|---|---|---|
| Pure ADU solver | `server/astrodeck/imaging/flats.py` | new |
| CoverCalibrator ABC + `CoverState` | `server/astrodeck/devices/base.py` | edit (append after `SafetyMonitor`) |
| SimCoverCalibrator + rig knob + render + build_sim_rig | `server/astrodeck/devices/sim.py` | edit |
| Role registration | `server/astrodeck/devices/backend.py` (`ROLES`) | edit (1 line) |
| Hub calibrator accessors | `server/astrodeck/hub.py` | edit |
| Engine flat acquisition | `server/astrodeck/sequence/engine.py` | edit |
| Sequence model fields | `server/astrodeck/sequence/models.py` | edit |
| API routes | `server/astrodeck/api/app.py` | edit |
| UI: flat-step field + panel card + role label | `ui/src/…` | edit |
| Tests | `server/tests/test_flat_solver.py`, `test_sim_covercalibrator.py`, `test_flat_sequence.py` | new |

---

## 2. Global Constraints (verbatim)

- **Privacy:** the real coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  `"[SITE-LABEL]"` must NEVER appear in code, tests, or docs. Site default is
  `"My Observatory"` / `0.0`.
- Never `git add -A`.
- **UI gate:** `cd ui && npx tsc -b`.
- **NO jsdom** — pure logic is tested via `npx tsx` inline-assert (idiom:
  `ui/src/lib/__tests__/eta.test.ts`).
- **Backend tests:** `server/.venv/Scripts/pytest.exe`, run from the repo root,
  `-n0` (single worker).
- **Device drivers** follow the existing `base.py` `Device`/ABC pattern + the
  `_DEV_TYPE_TO_ROLE` + hub-singleton pattern.
- **Client toasts** via `useStore.getState().enqueueToast`.
- **Honest-disabled (§11.8):** dim + lock + `aria-disabled` + `title`, never the
  native `disabled` attribute.
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Right-sized tasks. Each: exact Files, an Interfaces block with exact signatures,
bite-sized steps (actual code), exact test/typecheck commands + expected output,
and an impl-tier line.

All pytest commands run **from the repo root** `C:\Users\bear\astro`.

### Interfaces (exact signatures)

```python
# server/astrodeck/imaging/flats.py
from dataclasses import dataclass

@dataclass(frozen=True)
class FlatStep:
    exposure_s: float      # exposure to try next, or the accepted one when done
    done: bool             # stop the metering loop
    converged: bool        # measurement landed in the target band
    reason: str            # "" running | "converged" | "too_bright_at_min"
                           #  | "too_dim_at_max" | "max_iterations"
    iterations: int        # updates applied so far

class FlatExposureSolver:
    def __init__(self, target_adu: float, *, initial_exposure_s: float,
                 min_exposure_s: float = 0.001, max_exposure_s: float = 30.0,
                 tolerance: float = 0.10, pedestal: float = 0.0,
                 max_iterations: int = 8) -> None: ...
    def first(self) -> FlatStep: ...                 # the initial (clamped) exposure
    def update(self, measured_adu: float) -> FlatStep: ...   # feed the median of the last capture

# server/astrodeck/devices/base.py
class CoverState(enum.Enum): ...
class CoverCalibrator(Device):
    kind = "covercalibrator"; max_brightness: int = 1; has_cover: bool = False
    async def get_brightness(self) -> int: ...                # abstract
    async def calibrator_on(self, brightness: int) -> None: ...  # abstract
    async def calibrator_off(self) -> None: ...               # abstract
    async def get_calibrator_state(self) -> str: ...
    async def get_cover_state(self) -> CoverState: ...
    async def open_cover(self) -> None: ...
    async def close_cover(self) -> None: ...

# server/astrodeck/devices/sim.py
class SimCoverCalibrator(CoverCalibrator):
    def __init__(self, rig: SimRig, name: str = "Sim Flat Panel"): ...

# server/astrodeck/hub.py  (Hub methods)
async def calibrator_on(self, brightness: int) -> None: ...
async def calibrator_off(self) -> None: ...
async def open_cover(self) -> None: ...
async def close_cover(self) -> None: ...
async def calibrator_status(self) -> dict | None: ...   # None when no device

# server/astrodeck/sequence/models.py  (ExposureStep additions)
adu_target: int = Field(0, ge=0, le=65535)
panel_brightness: int | None = Field(None, ge=0)
```

```ts
// ui/src/types.ts  (ExposureStep additions)
adu_target?: number;
panel_brightness?: number;
```

---

### Task 1 — Pure ADU-target exposure solver

**Impl tier: Opus** — genuinely subtle numeric/correctness: the linear estimate,
rail detection at both bounds, no-signal / divide-by-zero guards, the
closest-so-far fallback, and the convergence band all have off-by-one and
sign traps that must be pinned by tests.

**Files:** `server/astrodeck/imaging/flats.py` (new),
`server/tests/test_flat_solver.py` (new).

**Step 1.1 — write the failing test first.** `server/tests/test_flat_solver.py`:

```python
from astrodeck.imaging.flats import FlatExposureSolver

def _linear_panel(k, bias=100.0):
    # a flat whose median ADU = bias + k * exposure (the panel model)
    return lambda exp: bias + k * exp

def _run(solver, panel, cap=20):
    st = solver.first()
    for _ in range(cap):
        st = solver.update(panel(st.exposure_s))
        if st.done:
            return st
    raise AssertionError("solver never terminated")

def test_converges_to_target_from_low_seed():
    # k=5000 ADU/s, target 25000 → true exposure ≈ (25000-100)/5000 ≈ 4.98s
    s = FlatExposureSolver(25000, initial_exposure_s=1.0, pedestal=100.0)
    st = _run(s, _linear_panel(5000))
    assert st.converged and st.reason == "converged"
    assert abs((100.0 + 5000 * st.exposure_s) - 25000) <= 0.10 * 25000

def test_converges_from_high_seed():
    s = FlatExposureSolver(20000, initial_exposure_s=30.0, pedestal=100.0)
    st = _run(s, _linear_panel(8000))
    assert st.converged

def test_too_bright_at_min_rails():
    # even the shortest exposure overshoots the target → honest rail, not a loop
    s = FlatExposureSolver(5000, initial_exposure_s=1.0, min_exposure_s=0.5,
                           pedestal=0.0)
    st = _run(s, _linear_panel(100000))     # 0.5s → 50000 ADU ≫ 5000
    assert st.done and not st.converged and st.reason == "too_bright_at_min"
    assert st.exposure_s == 0.5

def test_too_dim_at_max_rails():
    s = FlatExposureSolver(50000, initial_exposure_s=1.0, max_exposure_s=2.0,
                           pedestal=0.0)
    st = _run(s, _linear_panel(1000))       # 2s → 2000 ADU ≪ 50000
    assert st.done and not st.converged and st.reason == "too_dim_at_max"
    assert st.exposure_s == 2.0

def test_no_signal_pushes_to_max_then_rails():
    s = FlatExposureSolver(25000, initial_exposure_s=1.0, max_exposure_s=10.0,
                           pedestal=100.0)
    st = _run(s, lambda exp: 100.0)         # flat bias, zero panel signal
    assert st.done and not st.converged and st.reason == "too_dim_at_max"

def test_bounded_iterations_returns_closest():
    # a panel that never converges (measurement ignores exposure, off-band)
    s = FlatExposureSolver(25000, initial_exposure_s=1.0, max_iterations=4,
                           pedestal=0.0)
    st = _run(s, lambda exp: 24000 if exp < 3 else 26000)  # oscillates off-band? keep off-band
    assert st.done and st.iterations <= 4

def test_first_clamps_initial_into_bounds():
    s = FlatExposureSolver(1000, initial_exposure_s=99.0, max_exposure_s=30.0)
    assert s.first().exposure_s == 30.0
```

**Step 1.2 — implement `flats.py`** so the tests pass. Core logic:

```python
"""Pure ADU-target flat-exposure solver (PRO-5). No numpy, no I/O — a bounded,
iterative linear solver so it is exercisable with a synthetic panel in pytest and
reusable by the sequence engine (and, later, the twilight sky-flat re-solve)."""
from __future__ import annotations
from dataclasses import dataclass

_EPS = 1e-9

@dataclass(frozen=True)
class FlatStep:
    exposure_s: float
    done: bool
    converged: bool
    reason: str
    iterations: int

def _clamp(x, lo, hi): return max(lo, min(hi, x))

class FlatExposureSolver:
    def __init__(self, target_adu, *, initial_exposure_s,
                 min_exposure_s=0.001, max_exposure_s=30.0,
                 tolerance=0.10, pedestal=0.0, max_iterations=8):
        self.target = float(target_adu)
        self.min_s = float(min_exposure_s)
        self.max_s = float(max_exposure_s)
        self.tol = float(tolerance)
        self.pedestal = float(pedestal)
        self.max_iterations = int(max_iterations)
        self._last = _clamp(float(initial_exposure_s), self.min_s, self.max_s)
        self._iters = 0
        self._best_err = float("inf")
        self._best_exp = self._last

    def _band(self):
        return self.target * (1 - self.tol), self.target * (1 + self.tol)

    def first(self) -> FlatStep:
        return FlatStep(self._last, False, False, "", 0)

    def update(self, measured_adu: float) -> FlatStep:
        self._iters += 1
        m = float(measured_adu)
        lo, hi = self._band()
        err = abs(m - self.target)
        if err < self._best_err:
            self._best_err, self._best_exp = err, self._last
        # converged?
        if lo <= m <= hi:
            return FlatStep(self._last, True, True, "converged", self._iters)
        signal = m - self.pedestal
        # rail checks BEFORE proposing a move
        if m > hi and self._last <= self.min_s + _EPS:
            return FlatStep(self.min_s, True, False, "too_bright_at_min", self._iters)
        if (m < lo) and self._last >= self.max_s - _EPS:
            return FlatStep(self.max_s, True, False, "too_dim_at_max", self._iters)
        if self._iters >= self.max_iterations:
            return FlatStep(self._best_exp, True, False, "max_iterations", self._iters)
        # linear estimate (no-signal → drive to max)
        if signal <= _EPS:
            nxt = self.max_s
        else:
            nxt = self._last * (self.target - self.pedestal) / signal
        self._last = _clamp(nxt, self.min_s, self.max_s)
        return FlatStep(self._last, False, False, "", self._iters)
```

> Note for the implementer: `test_no_signal_pushes_to_max_then_rails` requires
> that once `_last == max_s` and `m < lo`, the NEXT `update` rails to
> `too_dim_at_max` — verify the ordering (rail check reads `self._last`, which
> was set to `max_s` on the prior update). Adjust the oscillation test's panel if
> `max_iterations` fires first; the assertion only checks `iterations <= 4`.

**Command:**
```
server/.venv/Scripts/pytest.exe server/tests/test_flat_solver.py -n0 -q
```
**Expected:** `7 passed in <1s`.

---

### Task 2 — CoverCalibrator ABC + `CoverState`

**Impl tier: Sonnet** — mechanical ABC following the `SafetyMonitor`/`Rotator`
idiom already in the file.

**Files:** `server/astrodeck/devices/base.py` (edit — append after
`SafetyMonitor`, i.e. after line 421).

**Step 2.1 — append the enum + ABC** (exact code in §1.5). Key points:
- `import enum` is already at `base.py:12`; `DeviceError` at `:21`.
- `get_brightness`/`calibrator_on`/`calibrator_off` are `@abstractmethod`.
- `get_calibrator_state` default returns `"off"`; `get_cover_state` default
  `CoverState.NOT_PRESENT`; `open_cover`/`close_cover` default `raise DeviceError`.
- Override `describe()` to add `"max_brightness"` and `"has_cover"` (sync — no await).

**Step 2.2 — no standalone test** (the ABC is abstract). It is exercised via
Task 3's `SimCoverCalibrator`. Verify import health:
```
server/.venv/Scripts/pytest.exe server/tests/test_sim_backend.py -n0 -q
```
**Expected:** existing sim-backend tests still `… passed` (no import error from
the new symbols).

---

### Task 3 — SimCoverCalibrator + rig knob + sim render + `build_sim_rig`

**Impl tier: Sonnet** — the device is a straightforward state machine; the one
mildly-subtle bit (the gated render term) is spelled out and default-off.

**Files:** `server/astrodeck/devices/sim.py` (edit),
`server/tests/test_sim_covercalibrator.py` (new).

**Step 3.1 — failing test.** `server/tests/test_sim_covercalibrator.py`:

```python
import numpy as np, pytest
from astrodeck.devices.sim import build_sim_rig, SimCoverCalibrator
from astrodeck.devices.base import CoverCalibrator, CoverState

@pytest.mark.asyncio
async def test_sim_rig_includes_covercalibrator():
    rig = build_sim_rig()
    cc = rig["covercalibrator"]
    assert isinstance(cc, SimCoverCalibrator) and isinstance(cc, CoverCalibrator)

@pytest.mark.asyncio
async def test_on_off_roundtrip_and_state():
    rig = build_sim_rig(); cc = rig["covercalibrator"]; await cc.connect()
    assert await cc.get_calibrator_state() == "off"
    await cc.calibrator_on(120)
    assert await cc.get_brightness() == 120
    assert await cc.get_calibrator_state() == "ready"
    await cc.calibrator_off()
    assert await cc.get_calibrator_state() == "off"

@pytest.mark.asyncio
async def test_cover_open_close():
    rig = build_sim_rig(); cc = rig["covercalibrator"]; await cc.connect()
    assert cc.has_cover is True
    assert await cc.get_cover_state() == CoverState.CLOSED
    await cc.open_cover()
    assert await cc.get_cover_state() == CoverState.OPEN

@pytest.mark.asyncio
async def test_panel_on_brightens_sim_frames():
    # turning the panel on raises the camera's mean ADU (linear in brightness)
    rig = build_sim_rig(); cam = rig["camera"]; cc = rig["covercalibrator"]
    await cam.connect(); await cc.connect()
    dark = (await cam.expose(1.0, 0, 30, 1, light=True)).data.mean()
    await cc.calibrator_on(200)
    lit = (await cam.expose(1.0, 0, 30, 1, light=True)).data.mean()
    assert lit > dark + 50    # the panel term is clearly visible

@pytest.mark.asyncio
async def test_default_off_render_unchanged():
    # flat_illumination defaults to 0 → a frame with the panel never touched is
    # identical to one from a rig that has no covercalibrator interaction.
    rig = build_sim_rig()
    assert rig["_rig"].flat_illumination == 0.0
```

**Step 3.2 — implement.** In `SimRig.__init__` (after `:226`):
```python
        # PRO-5 flat panel: uniform ADU/sec the sim CoverCalibrator adds when on.
        # OFF (0.0) by default so every existing SimCamera render is byte-for-byte
        # unchanged (the guide_star_hidden / rotator_pa_offset_deg opt-in idiom).
        self.flat_illumination = 0.0
```
In `SimCamera._render`, inside `if light and not self.rig.parked:` (after the sky
term at `:421`):
```python
            if self.rig.flat_illumination > 0:
                img += self.rig.flat_illumination * seconds
```
New class (near `SimSwitch`):
```python
class SimCoverCalibrator(CoverCalibrator):
    """A simulated flat panel + motorized cover. `calibrator_on` sets
    `rig.flat_illumination` so SimCamera frames actually brighten (a coherent
    rig, like the guide-loop wiring)."""
    max_brightness = 255
    has_cover = True
    ADU_PER_BRIGHTNESS = 60.0    # ADU/sec per brightness unit at gain 0

    def __init__(self, rig, name="Sim Flat Panel"):
        super().__init__(name); self.rig = rig
        self._brightness = 0; self._on = False
        self._cover = CoverState.CLOSED

    async def connect(self):  await asyncio.sleep(0.02); self.connected = True
    async def disconnect(self): self.connected = False
    async def get_brightness(self): return self._brightness
    async def get_calibrator_state(self): return "ready" if self._on else "off"

    async def calibrator_on(self, brightness):
        self._brightness = max(0, min(self.max_brightness, int(brightness)))
        self._on = True
        self.rig.flat_illumination = self.ADU_PER_BRIGHTNESS * self._brightness

    async def calibrator_off(self):
        self._on = False; self.rig.flat_illumination = 0.0

    async def get_cover_state(self): return self._cover
    async def open_cover(self):  self._cover = CoverState.OPEN
    async def close_cover(self): self._cover = CoverState.CLOSED
```
Add imports `CoverCalibrator, CoverState` to the `from .base import (...)` block
(`sim.py:17-31`). In `build_sim_rig()` return dict (`:1006-1017`) add:
```python
        "covercalibrator": SimCoverCalibrator(rig),
```

**Command:**
```
server/.venv/Scripts/pytest.exe server/tests/test_sim_covercalibrator.py -n0 -q
```
**Expected:** `5 passed`.

---

### Task 4 — Register the role (F-F wired through the framework)

**Impl tier: Sonnet** — one-line addition, but **verify the ripple** with the
full suite (this is the F-F integration checkpoint).

**Files:** `server/astrodeck/devices/backend.py` (edit `ROLES`).

**Step 4.1 —** add `"covercalibrator"` to `ROLES` (`backend.py:31-41`), after
`"rotator"` (before or after `guide_camera` — put it after `rotator`, before
`guide_camera`, to keep real roles grouped):
```python
ROLES = ("camera","telescope","focuser","guider","filterwheel","switch",
         "safety","rotator","covercalibrator","guide_camera")
```

**Step 4.2 — do NOT touch** `drivers._DEV_TYPE_TO_ROLE` (`drivers.py:85-89`) or
`ascom_registry._DEV_TYPE_TO_ROLE` (`ascom_registry.py:35-39`). Leaving them
unchanged keeps the Alpaca/ASCOM probes from offering an assignable
covercalibrator that has no client device class yet (that is the deferred vendor
backend — §4). Sim offers it via `SimBackend.roles = ROLES`.

**Step 4.3 — verify the ripple.** The connect loop (`hub.py:292-301`), implicit
offers (`drivers._implicit_rows:159`), `describe_all().roles`, and the
contract/backend tests all read `ROLES`. Run the device + sim + connect suites:
```
server/.venv/Scripts/pytest.exe server/tests/test_sim_backend.py server/tests/test_hub_connect.py server/tests/test_drivers.py -n0 -q
```
> If `test_hub_connect.py` / `test_drivers.py` names differ, discover with
> `server/.venv/Scripts/pytest.exe --collect-only -q server/tests | grep -iE "connect|driver|orchestr"`.
**Expected:** all `passed`; a green `connect_sim` now populates
`hub.devices["covercalibrator"]`. Add one assertion to the sim connect test:
`assert "covercalibrator" in hub.devices`.

---

### Task 5 — Hub calibrator accessors + status

**Impl tier: Sonnet** — thin wrappers over the connected device, mirroring
`Hub.safety`/`Hub.require` (`hub.py:781-790`).

**Files:** `server/astrodeck/hub.py` (edit),
`server/tests/test_hub_calibrator.py` (new).

**Step 5.1 — failing test:**
```python
import pytest
from astrodeck.hub import Hub

@pytest.mark.asyncio
async def test_hub_calibrator_control():
    hub = Hub(); await hub.connect_sim()
    await hub.calibrator_on(150)
    st = await hub.calibrator_status()
    assert st["state"] == "ready" and st["brightness"] == 150
    await hub.calibrator_off()
    assert (await hub.calibrator_status())["state"] == "off"

@pytest.mark.asyncio
async def test_calibrator_status_none_when_absent():
    hub = Hub()   # nothing connected
    assert await hub.calibrator_status() is None
```

**Step 5.2 — implement** (add near `Hub.safety`, `hub.py:787`):
```python
    @property
    def calibrator(self):
        return self.devices.get("covercalibrator")

    async def calibrator_on(self, brightness: int) -> None:
        await self.require("covercalibrator").calibrator_on(int(brightness))

    async def calibrator_off(self) -> None:
        await self.require("covercalibrator").calibrator_off()

    async def open_cover(self) -> None:
        await self.require("covercalibrator").open_cover()

    async def close_cover(self) -> None:
        await self.require("covercalibrator").close_cover()

    async def calibrator_status(self) -> dict | None:
        cc = self.calibrator
        if cc is None or not cc.connected:
            return None
        return {"state": await cc.get_calibrator_state(),
                "brightness": await cc.get_brightness(),
                "max_brightness": cc.max_brightness,
                "has_cover": cc.has_cover,
                "cover_state": (await cc.get_cover_state()).value}
```

**Command:**
```
server/.venv/Scripts/pytest.exe server/tests/test_hub_calibrator.py -n0 -q
```
**Expected:** `2 passed`.

---

### Task 6 — Sequence model fields

**Impl tier: Sonnet** — additive pydantic fields.

**Files:** `server/astrodeck/sequence/models.py` (edit `ExposureStep`, `:10-20`).

**Step 6.1 —** add after `frame_type` (`:20`):
```python
    # --- PRO-5 flat auto-exposure (additive; 0/None = off => back-compat) ---
    adu_target: int = Field(0, ge=0, le=65535)     # >0 + Flat ⇒ solve exposure to this ADU
    panel_brightness: int | None = Field(None, ge=0)  # flat-panel level while shooting; None = don't touch
```

**Step 6.2 — test** (append to an existing models test or new
`test_flat_model.py`):
```python
from astrodeck.sequence.models import ExposureStep
def test_flat_fields_default_off():
    s = ExposureStep(exposure_s=2, count=5)
    assert s.adu_target == 0 and s.panel_brightness is None
def test_flat_fields_roundtrip():
    s = ExposureStep(exposure_s=2, count=5, frame_type="Flat",
                     adu_target=25000, panel_brightness=120)
    assert s.model_dump()["adu_target"] == 25000
```
**Command:**
```
server/.venv/Scripts/pytest.exe server/tests/test_flat_model.py -n0 -q
```
**Expected:** `2 passed`. (If the repo is pydantic v1, use `s.dict()` instead of
`s.model_dump()`.)

---

### Task 7 — Engine flat acquisition (ADU solve + panel control)

**Impl tier: Opus** — touches the autonomous run loop: bounded trial captures,
panel on/off ordering, converged-vs-rail fallback, and best-effort panel-off on
teardown. Ordering and cancellation-safety matter.

**Files:** `server/astrodeck/sequence/engine.py` (edit `_run_calibration`, add a
helper + a timeout const + teardown hook), `server/tests/test_flat_sequence.py` (new).

**Step 7.1 — failing integration test** (drives the whole path against the sim):
```python
import pytest
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import SequencePlan, Target, ExposureStep

@pytest.mark.asyncio
async def test_flat_step_solves_and_shoots(tmp_path, monkeypatch):
    hub = Hub(); await hub.connect_sim()
    eng = SequenceEngine(hub)
    step = ExposureStep(exposure_s=0.5, count=3, frame_type="Flat",
                        gain=0, offset=30, binning=1,
                        adu_target=20000, panel_brightness=180)
    plan = SequencePlan(name="Flats", guide=False, targets=[
        Target(name="Flats", ra_hours=0, dec_deg=0, calibration=True, steps=[step])])
    eng.start(plan)
    await eng._task              # run to completion
    # 3 flats recorded, panel left OFF, exposure was re-solved (not the 0.5s seed)
    assert eng._frames_done == 3
    assert (await hub.calibrator_status())["state"] == "off"
```
> Use the same run-to-completion idiom the existing calibration tests use
> (discover with `grep -rn "_run_calibration\|calibration=True" server/tests`).

**Step 7.2 — implement.** Add a timeout const near `engine.py:74-93`:
```python
CALIBRATOR_CMD_TIMEOUT_S = 30.0   # panel on/off / cover move
FLAT_METER_MAX_S = 8              # trial metering captures cap (belt-and-braces)
```
Add a helper (near `_capture`, `:1002`):
```python
    async def _solve_flat_exposure(self, step, target) -> tuple[float, bool]:
        """Meter the panel to step.adu_target. Returns (exposure_s, converged).
        Trial captures are save=False (never written to disk)."""
        from ..imaging.flats import FlatExposureSolver
        solver = FlatExposureSolver(step.adu_target, initial_exposure_s=step.exposure_s)
        st = solver.first(); exp = st.exposure_s
        for _ in range(FLAT_METER_MAX_S):
            info = await _bounded(
                self.hub.capture(exp, step.gain, step.offset, step.binning,
                                 save=False, frame_type="Flat"),
                float(exp) + CAPTURE_MARGIN_S, "flat metering capture")
            med = float(info["stats"]["median"])
            st = solver.update(med)
            self._set_state(detail=f"{target.name}: metering flat "
                                   f"{med:.0f} ADU @ {exp:g}s")
            if st.done:
                break
            exp = st.exposure_s
        if not st.converged:
            bus.log("warning", f"{target.name}: flat exposure did not converge "
                               f"({st.reason}); using {st.exposure_s:g}s", "sequence")
        return st.exposure_s, st.converged
```
Extend `_capture` to accept an exposure override (`:1002-1011`):
```python
    async def _capture(self, step, target, *, exposure_s=None):
        exp = float(exposure_s if exposure_s is not None else step.exposure_s)
        budget = exp + CAPTURE_MARGIN_S
        return await _bounded(
            self.hub.capture(exp, step.gain, step.offset, step.binning,
                             save=True, target=target.name, frame_type=step.frame_type),
            budget, f"capture {exp:g}s")
```
In `_run_calibration` (`:1022`), wrap the per-step loop so a Flat step with
`adu_target > 0` first turns the panel on + solves, and uses the solved exposure
for the count loop; turn the panel off after each such step:
```python
        for si, step in enumerate(target.steps):
            solved_exp = None
            flat_auto = (step.frame_type.upper() == "FLAT" and step.adu_target > 0)
            if flat_auto and "covercalibrator" in self.hub.devices:
                if step.panel_brightness is not None:
                    await _bounded(self.hub.calibrator_on(step.panel_brightness),
                                   CALIBRATOR_CMD_TIMEOUT_S, "calibrator on")
                    cc = self.hub.calibrator
                    if getattr(cc, "has_cover", False):
                        await _bounded(self.hub.open_cover(),
                                       CALIBRATOR_CMD_TIMEOUT_S, "open cover")
                self._set_state(detail=f"{target.name}: solving flat exposure")
                solved_exp, _ = await self._solve_flat_exposure(step, target)
            key = f"{target.id}:{step.id}"
            for i in range(self._done.get(key, 0), step.count):
                await self._checkpoint()
                await self._frame_alerts_tick()
                self._begin_frame(ti, si, solved_exp or step.exposure_s)
                self._set_state(state="running",
                                detail=f"{target.name}: {step.frame_type} "
                                       f"{(solved_exp or step.exposure_s):g}s [{i+1}/{step.count}]")
                info = await self._capture(step, target, exposure_s=solved_exp)
                accepted = self._check_quality(info, calibration=True)
                self._reporter_record(target, step, info, accepted=accepted)
                if accepted:
                    self._record_frame(key, i, target, step, info)
                else:
                    if not await self._handle_reject(info, key, i, target, step):
                        self._record_frame(key, i, target, step, info, accepted=False)
            if flat_auto and "covercalibrator" in self.hub.devices \
                    and step.panel_brightness is not None:
                await self._panel_off_safe()
```
Add a best-effort panel-off helper + wire it into teardown. Locate `_safe_stop`
(`grep -n "_safe_stop\|_wind_down" server/astrodeck/sequence/engine.py`) and call
`await self._panel_off_safe()` inside it:
```python
    async def _panel_off_safe(self) -> None:
        """Best-effort: never let an aborted/failed run leave the panel lit."""
        if "covercalibrator" not in self.hub.devices:
            return
        try:
            await _bounded(self.hub.calibrator_off(), CALIBRATOR_CMD_TIMEOUT_S,
                           "calibrator off")
            cc = self.hub.calibrator
            if getattr(cc, "has_cover", False):
                await _bounded(self.hub.close_cover(), CALIBRATOR_CMD_TIMEOUT_S,
                               "close cover")
        except Exception as e:
            bus.log("warning", f"panel-off failed: {e}", "sequence")
```
> `_record_frame`/`_begin_frame` use the exposure passed to `_begin_frame` for
> the ETA; pass `solved_exp or step.exposure_s` (as above) so the sub-frame bar
> reflects the real exposure.

**Command:**
```
server/.venv/Scripts/pytest.exe server/tests/test_flat_sequence.py -n0 -q
```
**Expected:** `1 passed`. Then run the full calibration/engine suite to confirm
no regression:
```
server/.venv/Scripts/pytest.exe server/tests -k "calibration or sequence or engine" -n0 -q
```
**Expected:** all `passed`.

---

### Task 8 — API routes (manual panel control)

**Impl tier: Sonnet** — mirror the `dew_heater` route (`app.py:2604-2612`).

**Files:** `server/astrodeck/api/app.py` (edit — add Body models + 3 routes near
the camera routes, ~`:2612`).

**Step 8.1 —** add pydantic bodies (near the other `*Body` models) and routes:
```python
class CalibratorBody(BaseModel):
    brightness: int = Field(ge=0)
class CoverBody(BaseModel):
    open: bool

    @app.post("/api/calibrator/on", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def calibrator_on(body: CalibratorBody):
        try:
            await hub.calibrator_on(body.brightness); return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/calibrator/off", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def calibrator_off():
        try:
            await hub.calibrator_off(); return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/calibrator/cover", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def calibrator_cover(body: CoverBody):
        try:
            await (hub.open_cover() if body.open else hub.close_cover())
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)
```

**Step 8.2 — test** (mirror existing route tests; discover the test client fixture
with `grep -rln "TestClient\|app_client\|api/camera/cooler" server/tests`):
```python
def test_calibrator_routes(client):     # client = connected-sim TestClient fixture
    assert client.post("/api/calibrator/on", json={"brightness": 100}).status_code == 200
    assert client.post("/api/calibrator/off").status_code == 200
```
**Command:**
```
server/.venv/Scripts/pytest.exe server/tests/test_api_calibrator.py -n0 -q
```
**Expected:** `1 passed`.

---

### Task 9 — UI: flat-step field + panel card + role label

**Impl tier: Sonnet** — thin render, verified by the typechecker (no jsdom).

**Files:** `ui/src/types.ts` (edit), `ui/src/views/SequenceView.tsx` (edit),
`ui/src/components/settings/backendMeta.ts` (edit),
`ui/src/components/settings/FlatPanelCard.tsx` (new, small).

**Step 9.1 —** `types.ts` `ExposureStep` (`:426-437`): add
```ts
  adu_target?: number;
  panel_brightness?: number;
```

**Step 9.2 —** `backendMeta.ts` (`:10-37`): add `"covercalibrator"` to
`ALL_ROLES` and `ROLE_LABEL`:
```ts
// ALL_ROLES: add after "rotator"
  "covercalibrator",
// ROLE_LABEL:
  covercalibrator: "Flat panel",
```
(Roles are typed `string` throughout the Equipment surface, so this only adds a
row; on non-sim rigs its dropdown is empty — honest, assignable when a real
backend lands.)

**Step 9.3 —** `SequenceView.tsx` step editor (`:776-800`): when the step's
`frame_type === "Flat"`, render one extra ADU-target input (honest-disabled while
`running` per §11.8 — dim + lock + `aria-disabled` + `title`, never native
`disabled`). Minimal, additive:
```tsx
{(s.frame_type === "Flat") && (
  <input className="field !py-1" title="target ADU (0 = fixed exposure)"
    aria-disabled={running || undefined}
    value={s.adu_target ?? 0}
    onChange={(e) => !running && patchStep(ti, si, { adu_target: num(e.target.value, s.adu_target ?? 0) })} />
)}
```

**Step 9.4 —** `FlatPanelCard.tsx`: a tiny control card (on/off + brightness
slider + open/close cover) posting to `/api/calibrator/*`, rendered on the
Equipment surface only when `status.devices.covercalibrator` exists. Use
`useStore.getState().enqueueToast` for error surfacing. Keep it thin (render +
`api.post`); no pure logic needed. (Optional: if any non-trivial pure helper
appears — e.g. brightness clamp — extract it and add a `tsx` inline-assert test
per the eta.test.ts idiom.)

**Command (the UI gate):**
```
cd ui && npx tsc -b
```
**Expected:** exit 0, no output (clean typecheck). No tsx test required — this is
thin render; the pure logic (the solver) is Python and tested in Task 1.

---

### Suggested execution order

1 (solver) → 2 (ABC) → 3 (sim) → 4 (role) → 5 (hub) → 6 (model) → 7 (engine) →
8 (API) → 9 (UI). Tasks 1–3 are independent of 5–9 except through the ABC; 4 is
the F-F integration gate; 7 depends on 1,3,5,6.

---

## 4. Open decisions (each with a recommendation)

1. **Measure mean or median ADU?** A flat is a light frame; the sim (and a real
   sky) render stars/dust into it. Mean (float) gives finer ratio resolution;
   median (int) rejects stars/hot pixels. **Recommend median** — it measures the
   flat *background* the calibration frame is for, and it is already on every
   `info["stats"]`. (The solver takes `pedestal`/`tolerance` so int granularity
   is a non-issue at target ~25 000.)

2. **Where does the solver live?** `imaging/flats.py` vs `sequence/flats.py`.
   **Recommend `imaging/flats.py`** — pure, dependency-light, and reusable by both
   the engine and a future manual "shoot flats now" route + the twilight follow-up.

3. **Surface covercalibrator in the Equipment assignment grid now?** **Recommend
   yes** (add to `ALL_ROLES` + `ROLE_LABEL`): it makes the F-F role visible and
   assignable the moment a backend offers it; on non-sim rigs the dropdown is
   empty (honest), and roles are `string`-typed so nothing breaks. The sim
   auto-connects it via `connect_sim` regardless.

4. **Do NOT add covercalibrator to the Alpaca/ASCOM `_DEV_TYPE_TO_ROLE` maps in
   v1.** Adding it would advertise an assignable Alpaca/ASCOM CoverCalibrator that
   has no client device class to open — a broken offer. **Recommend deferring**
   the real Alpaca `AlpacaCoverCalibrator` client (the host-side handlers already
   exist, `handlers_aux.py`) to the vendor-backend follow-up; v1 is sim-only.

5. **New sequence step type vs additive fields?** **Recommend additive fields**
   (`adu_target`, `panel_brightness` on `ExposureStep`) — back-compat, no new
   model, and it matches how `frame_type` already discriminates Flat/Dark/Bias.

6. **Panel on/off + cover ownership.** **Recommend the engine owns it** around the
   flat step (on before solve, off after the step) with a best-effort
   `_panel_off_safe()` wired into `_safe_stop`/`_wind_down`, plus manual
   `/api/calibrator/*` routes for out-of-sequence use. This guarantees an aborted
   run never leaves the panel lit.

7. **Twilight sky-flat re-solve (the explicit deferral).** A sky flat's
   illumination changes every frame, so exposure must be re-solved continuously
   (and dawn/dusk direction flips whether exposures should grow or shrink). This
   balloons (needs a per-frame re-solve loop, a sun-altitude gate, and an
   abort-when-too-bright/too-dark policy). **Recommend deferring** to a noted
   follow-up; the `FlatExposureSolver` core is written to be reused there
   unchanged (feed it each frame's median; it already rails honestly when the sky
   leaves the achievable band).
