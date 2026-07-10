# Rotator / CAA Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A first-class `rotator` device role (ZWO CAA) with full framing automation — a solve→sync→rotate loop that enforces a target's position angle in slews, sequences, and Atlas, with cable-wrap range limiting and an arc-dial Equipment card.

**Architecture:** Driver I/O is mechanical-space; the sky↔mechanical sync offset lives client-side in the `Rotator` ABC (NINA VM-layer parity). Pure range/angle math in new `rotation.py` / `rotation.ts` (shared test vectors). The rotate loop (`hub.rotate_to_pa`) runs BEFORE centering inside `goto_and_center` and degrades on failure, never aborting a slew that already happened.

**Tech Stack:** FastAPI + pydantic (server), React/TS/Tailwind v4 (UI), pytest, self-executing `npx tsx` assert files (NO vitest).

**Spec:** `docs/superpowers/specs/2026-07-10-rotator-caa-design.md`
**Algorithm source:** `docs/native-parity/algorithms/nina-platesolving.md` §11.2–11.4

## Global Constraints

- Server tests run FROM `server/`: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q <file>` — run the named files per task (full suite ~8 min, reserved for the final review). Run pytest in the FOREGROUND and wait; never background it.
- UI tests are self-executing assert files run via `npx tsx <file>` from `ui/`; there is NO vitest and NO `test` npm script. Never reference bare `process` — use the `(globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1)` cast.
- UI build check: from `ui/`: `npm run build` (must be clean).
- `range_type` values are exactly the strings `"full" | "half" | "quarter"`; defaults: `range_type="full"`, `range_start_deg=0.0`, `tolerance_deg=1.0`. Validation bounds: `range_start_deg` in `[0, 360)`, `tolerance_deg` in `(0, 45]`; violations raise `ValueError` → HTTP 422.
- Rotator driver I/O is MECHANICAL-space only (`get_mechanical_position` / `move_mechanical`); never call a driver's own Sync. The sky mapping lives in the `Rotator` ABC (`sync_offset_deg = mechanical − sky`).
- Missing/disconnected device → `hub.require(role)` raises `DeviceError` → route maps via `_err(e)` → HTTP **409** (repo convention; the spec was amended from 404).
- RBAC: rotator motion routes use `CAP_CONTROL_CAPTURE` (the focuser pattern); `POST /api/config/rotator` uses `CAP_CONFIG_BACKEND` (the providers pattern).
- All rotate-loop tolerance comparisons are mod-180 (`angle_equals_mod180`) — a frame rotated 180° is the same framing.
- SVG colors ONLY via CSS vars / theme tokens (`var(--accent)`, `text-dim`, `border-line` …) — never hardcoded hex (night mode redshifts through the vars).
- `SimRig.rotator_pa_offset_deg` defaults to **0.0** so every existing sim solve/TPPA test is byte-identical; tests that exercise sync-discovery set it explicitly.
- No new dependencies (server or UI).
- Python float mod: `a % 360.0` is already euclidean (non-negative) for the ranges used — it IS the parity doc's `euclidian_mod`.

---

## File map

| File | Change |
|---|---|
| `server/astrodeck/rotation.py` | NEW — pure angle/range math |
| `server/astrodeck/devices/base.py` | `Rotator` ABC |
| `server/astrodeck/devices/sim.py` | `SimRig` fields, `SimRotator`, `build_sim_rig` |
| `server/astrodeck/solve/simsolver.py` | rotation truth from sim rig |
| `server/astrodeck/devices/alpaca.py` | `AlpacaRotator`, `DEVICE_CLASSES` |
| `server/astrodeck/devices/nina.py` | `NinaRotator`, `_ROLE_CLASSES`, `_detail` |
| `server/astrodeck/devices/backend.py` | `ROLES` += rotator |
| `server/astrodeck/devices/backends/native_backend.py` | roles + `_ROLE_TO_DEV_TYPE` |
| `server/astrodeck/devices/backends/nina_backend.py` | roles += rotator |
| `server/astrodeck/drivers.py` | `_DEV_TYPE_TO_ROLE` += rotator |
| `server/astrodeck/profiles.py` | role docstring comment |
| `server/astrodeck/config.py` | `RotatorConfig`, `AppConfig.rotator`, `set_rotator` |
| `server/astrodeck/providers.py` | motion guard += rotator |
| `server/astrodeck/hub.py` | `rotate_to_pa`, `goto_and_center(rotation_deg=)`, status block |
| `server/astrodeck/sequence/engine.py` + `models.py` | pass/annotate `rotation_deg` |
| `server/astrodeck/api/app.py` | rotator routes, config route, `GotoBody.rotation_deg` |
| `ui/src/types.ts`, `ui/src/api/backends.ts`, `ui/src/lib/rotation.ts`, `ui/src/lib/equipment.ts` | types, wrapper, math mirror, motion mirror |
| `ui/src/components/equipment/RotatorCard.tsx` | NEW — card + arc dial |
| `ui/src/views/EquipmentView.tsx`, `AtlasView.tsx`, `SequenceView.tsx` | integration + banners |
| `ui/src/components/settings/backendMeta.ts`, `BackendLinkGrid.tsx` | labels, de-dupe |

---

### Task 1: `rotation.py` — pure angle & range math

**Files:**
- Create: `server/astrodeck/rotation.py`
- Test: `server/tests/test_rotation.py`

**Interfaces:**
- Produces: `mod360(a: float) -> float`; `angle_equals(a, b, tol) -> bool`; `angle_equals_mod180(a, b, tol) -> bool`; `target_mechanical_position(p, range_type, range_start_deg) -> float`; `map_sky_target(sky_target, mech_pos, sync_offset_deg, range_type, range_start_deg) -> float`; `shortest_rotation(target_deg, orientation_deg, range_type) -> float` (signed distance; commanded absolute = `mod360(orientation + distance)`).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_rotation.py
"""Table-driven tests for astrodeck.rotation — direct transcription checks of
docs/native-parity/algorithms/nina-platesolving.md §11.2/§11.4. The UI mirror
(ui/src/lib/rotation.ts) asserts the SAME vectors — keep the tables in sync."""
import pytest

from astrodeck.rotation import (
    angle_equals,
    angle_equals_mod180,
    map_sky_target,
    mod360,
    shortest_rotation,
    target_mechanical_position,
)


def test_mod360_is_euclidean():
    assert mod360(0.0) == 0.0
    assert mod360(360.0) == 0.0
    assert mod360(-10.0) == 350.0
    assert mod360(725.0) == 5.0


# (a, b, tol, equal) — §11.4: d = |mod(a,360)-mod(b,360)|; equal when
# d <= tol or (360-d) <= tol (with the 1e-13 slack).
ANGLE_EQ = [
    (0.0, 0.0, 1.0, True),
    (0.5, 0.0, 1.0, True),
    (1.5, 0.0, 1.0, False),
    (359.5, 0.0, 1.0, True),      # wrap
    (180.0, 0.0, 1.0, False),
    (365.0, 5.0, 0.5, True),      # mod-360 collapse
]


@pytest.mark.parametrize("a,b,tol,eq", ANGLE_EQ)
def test_angle_equals(a, b, tol, eq):
    assert angle_equals(a, b, tol) is eq


MOD180_EQ = [
    (180.0, 0.0, 1.0, True),      # the whole point: 180° apart IS equal
    (179.5, 0.0, 1.0, True),
    (90.0, 0.0, 1.0, False),
    (359.5, 180.0, 1.0, True),
]


@pytest.mark.parametrize("a,b,tol,eq", MOD180_EQ)
def test_angle_equals_mod180(a, b, tol, eq):
    assert angle_equals_mod180(a, b, tol) is eq


# (p, range_type, range_start, expected) — §11.2 get_target_mechanical_position.
# d = mod360(p - range_start); HALF: p if d<180 else p+180;
# QUARTER: p / p+270 / p+180 / p+90 for d<90 / d<180 / d<270 / else.
RANGE_MAP = [
    (10.0, "full", 0.0, 10.0),
    (350.0, "full", 245.0, 350.0),
    # HALF, start 0: allowed [0,180)
    (10.0, "half", 0.0, 10.0),        # d=10  < 180 → p
    (190.0, "half", 0.0, 10.0),       # d=190 ≥ 180 → p+180 = 370 → 10
    # HALF, start 245: allowed [245, 65)
    (30.0, "half", 245.0, 30.0),      # d=mod360(30-245)=145 < 180 → p
    (100.0, "half", 245.0, 280.0),    # d=215 ≥ 180 → p+180 = 280
    # QUARTER, start 0: allowed [0,90)
    (10.0, "quarter", 0.0, 10.0),     # d=10  < 90  → p
    (100.0, "quarter", 0.0, 10.0),    # d=100 < 180 → p+270 = 370 → 10
    (200.0, "quarter", 0.0, 20.0),    # d=200 < 270 → p+180 = 380 → 20
    (300.0, "quarter", 0.0, 30.0),    # d=300 ≥ 270 → p+90  = 390 → 30
    # QUARTER, start 245
    (250.0, "quarter", 245.0, 250.0),  # d=5   < 90  → p
    (340.0, "quarter", 245.0, 250.0),  # d=95  < 180 → p+270 = 610 → 250
    (100.0, "quarter", 245.0, 280.0),  # d=215 < 270 → p+180 = 280
    (160.0, "quarter", 245.0, 250.0),  # d=275 ≥ 270 → p+90  = 250
]


@pytest.mark.parametrize("p,rt,start,expected", RANGE_MAP)
def test_target_mechanical_position(p, rt, start, expected):
    assert target_mechanical_position(p, rt, start) == pytest.approx(expected)


def test_map_sky_target_full_range_is_identity():
    # FULL: mech target == mech, so the sky target comes back unchanged.
    assert map_sky_target(120.0, 78.5, 30.0, "full", 0.0) == pytest.approx(120.0)


def test_map_sky_target_composes_offset_and_range():
    # §11.2: mech = mod360(sky + offset); mech_tgt = range_map(mech);
    # back = mod360(mech_tgt - offset). sky=100, offset=40 → mech=140;
    # HALF start 245 → d=mod360(140-245)=255 ≥ 180 → mech_tgt=320;
    # back = 320-40 = 280.
    assert map_sky_target(100.0, 0.0, 40.0, "half", 245.0) == pytest.approx(280.0)


def test_shortest_rotation_full_prefers_180_flip():
    # §11.3 FULL branch: distance 170° → the 180°-flipped frame is only −10 away.
    d = shortest_rotation(170.0, 0.0, "full")
    assert d == pytest.approx(-10.0)
    # distance 10 stays 10
    assert shortest_rotation(10.0, 0.0, "full") == pytest.approx(10.0)
    # distance 100: mod180=100, m2=-80 → -80 is shorter
    assert shortest_rotation(100.0, 0.0, "full") == pytest.approx(-80.0)


def test_shortest_rotation_limited_range_keeps_full_signed():
    # HALF/QUARTER: the target was already range-mapped — do NOT collapse
    # mod-180 (that could command the out-of-range twin). Just normalize
    # to (-180, 180].
    assert shortest_rotation(350.0, 0.0, "half") == pytest.approx(-10.0)
    assert shortest_rotation(190.0, 0.0, "quarter") == pytest.approx(-170.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `server/`): `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotation.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'astrodeck.rotation'`

- [ ] **Step 3: Write the implementation**

```python
# server/astrodeck/rotation.py
"""Pure rotator angle & mechanical-range math.

Direct transcription of docs/native-parity/algorithms/nina-platesolving.md
§11.2 (get_target_mechanical_position / get_target_position) and §11.4
(angle equality). No I/O, no device knowledge — table-tested, and mirrored
in ui/src/lib/rotation.ts with the SAME test vectors.
"""
from __future__ import annotations

#: slack from the reference implementation (ANGLE_EQUALS_EPSILON)
_EPS = 1e-13


def mod360(a: float) -> float:
    """Euclidean mod 360 — result always in [0, 360)."""
    return a % 360.0


def angle_equals(a: float, b: float, tol: float) -> bool:
    """§11.4: equal within tol degrees, wrap-aware (mod 360)."""
    d = abs(mod360(a) - mod360(b))
    t = tol % 360.0
    return (d - t) <= _EPS or ((360.0 - d) - t) <= _EPS


def angle_equals_mod180(a: float, b: float, tol: float) -> bool:
    """§11.4 'oneEightyIsEqual': a frame rotated 180° is the same framing."""
    d = abs(a % 180.0 - b % 180.0)
    t = tol % 180.0
    return (d - t) <= _EPS or ((180.0 - d) - t) <= _EPS


def target_mechanical_position(p: float, range_type: str,
                               range_start_deg: float) -> float:
    """§11.2: map a mechanical angle into the allowed sweep. ``p`` in [0,360)."""
    d = mod360(p - range_start_deg)
    if range_type == "half":
        t = p if d < 180.0 else p + 180.0
    elif range_type == "quarter":
        if d < 90.0:
            t = p
        elif d < 180.0:
            t = p + 270.0
        elif d < 270.0:
            t = p + 180.0
        else:
            t = p + 90.0
    else:  # "full"
        t = p
    return mod360(t)


def map_sky_target(sky_target: float, mech_pos: float, sync_offset_deg: float,
                   range_type: str, range_start_deg: float) -> float:
    """§11.2 get_target_position: desired sky PA -> reachable sky PA given the
    mechanical range. ``sync_offset_deg`` is mechanical − sky (Rotator ABC).
    ``mech_pos`` is accepted for signature clarity/parity but the mapping only
    needs the offset."""
    del mech_pos  # parity signature; offset alone determines the mapping
    position = mod360(sky_target)
    mech = mod360(position + sync_offset_deg)
    mech_tgt = target_mechanical_position(mech, range_type, range_start_deg)
    return mod360(mech_tgt - sync_offset_deg + 360.0)


def shortest_rotation(target_deg: float, orientation_deg: float,
                      range_type: str) -> float:
    """§11.3: signed distance to move. Commanded absolute sky angle is
    ``mod360(orientation + distance)``.

    FULL range considers the 180°-rotated frame when it is closer (camera
    frames are PA-ambiguous mod 180). Limited ranges must NOT collapse mod-180
    — the target was already range-mapped and the twin may be out of range —
    so they get plain shortest-signed normalization into (-180, 180].
    """
    distance = target_deg - orientation_deg
    if range_type == "full":
        m = mod360(distance) % 180.0
        m2 = m - 180.0
        return m if m < abs(m2) else m2
    d = mod360(distance)
    return d - 360.0 if d > 180.0 else d
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotation.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/rotation.py server/tests/test_rotation.py
git commit -m "feat(rotator): rotation.py — parity range mapping + mod-180 angle math (spec §3.1)"
```

---

### Task 2: `Rotator` ABC + `SimRotator` + sim-solver truth hook

**Files:**
- Modify: `server/astrodeck/devices/base.py` (add `Rotator` after `Focuser`, ~line 235)
- Modify: `server/astrodeck/devices/sim.py` (SimRig fields ~line 190; new `SimRotator`; `build_sim_rig` ~line 690)
- Modify: `server/astrodeck/solve/simsolver.py` (sim-rig success branch)
- Test: `server/tests/test_rotator_devices.py` (new)

**Interfaces:**
- Consumes: `astrodeck.rotation.mod360` (Task 1).
- Produces: `Rotator` ABC — class attrs `kind="rotator"`, `can_reverse: bool = False`, `sync_offset_deg: float = 0.0`, `synced: bool = False`, `MOVE_TIMEOUT_S: float = 180.0`; abstract `async get_mechanical_position() -> float`, `async move_mechanical(mech_deg: float) -> None`, `async halt() -> None`; concrete `async get_position() -> float`, `async sync(sky_deg: float) -> None`, `async move_to(sky_deg: float) -> None`, `async is_moving() -> bool` (default False), `async get_reverse() -> bool` (default False), `async set_reverse(value: bool) -> None` (default raises `DeviceError`). `SimRig.rotator_mech_deg`, `SimRig.rotator_pa_offset_deg`. `build_sim_rig()` gains `"rotator"` key.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_rotator_devices.py
"""Rotator ABC sync math + SimRotator behavior + SimSolver rotation truth."""
import asyncio

import pytest

from astrodeck.devices.sim import SimRig, SimRotator, build_sim_rig
from astrodeck.solve.simsolver import SimSolver


@pytest.fixture()
def rig():
    return SimRig()


def test_sim_rig_has_rotator_fields_defaulting_inert(rig):
    assert rig.rotator_mech_deg == 0.0
    assert rig.rotator_pa_offset_deg == 0.0   # 0.0 keeps existing sim tests identical


def test_build_sim_rig_includes_rotator():
    devices = build_sim_rig()
    assert isinstance(devices["rotator"], SimRotator)


@pytest.mark.asyncio
async def test_unsynced_rotator_sky_equals_mechanical(rig):
    rot = SimRotator(rig)
    await rot.connect()
    rig.rotator_mech_deg = 78.5
    assert rot.synced is False
    assert await rot.get_position() == pytest.approx(78.5)


@pytest.mark.asyncio
async def test_sync_sets_offset_and_sky_moves_land(rig):
    rot = SimRotator(rig)
    await rot.connect()
    rig.rotator_mech_deg = 100.0
    await rot.sync(70.0)                       # offset = 100 - 70 = 30
    assert rot.synced is True
    assert rot.sync_offset_deg == pytest.approx(30.0)
    assert await rot.get_position() == pytest.approx(70.0)
    await rot.move_to(120.0)                   # mech target = 120 + 30 = 150
    assert rig.rotator_mech_deg == pytest.approx(150.0)
    assert await rot.get_position() == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_halt_stops_a_move_short(rig):
    rot = SimRotator(rig)
    await rot.connect()
    task = asyncio.create_task(rot.move_mechanical(180.0))
    await asyncio.sleep(0.05)                  # let it start
    await rot.halt()
    await task
    assert rig.rotator_mech_deg < 180.0        # stopped short of the target


@pytest.mark.asyncio
async def test_sim_solver_reports_rotator_truth(rig):
    rig.rotator_mech_deg = 50.0
    rig.rotator_pa_offset_deg = 30.0           # tests OPT IN to the clock offset
    solver = SimSolver(rig, mode=None)
    result = await solver.solve(None)
    assert result.success
    assert result.rotation_deg == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_sim_solver_rotation_zero_when_defaults(rig):
    # default rig (mech 0, offset 0) → rotation 0.0, byte-identical to before
    solver = SimSolver(rig, mode=None)
    result = await solver.solve(None)
    assert result.success
    assert result.rotation_deg == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotator_devices.py`
Expected: FAIL — `ImportError: cannot import name 'SimRotator'`

- [ ] **Step 3: Implement the `Rotator` ABC** (in `devices/base.py`, directly after `Focuser`; use the same `DeviceError` the module already defines/imports — check the top of the file)

```python
class Rotator(Device):
    """Camera rotator / angle adjuster (e.g. ZWO CAA).

    Driver I/O is MECHANICAL-space only; the sky↔mechanical sync offset lives
    HERE, client-side (offset = mechanical − sky), exactly like NINA's VM
    layer (parity §11.2). Never call a driver's own Sync — this behaves
    identically across Alpaca IRotatorV2/V3, the NINA bridge, and sim.
    An unsynced rotator is not an error: offset 0 means sky == mechanical.
    """

    kind = "rotator"
    can_reverse: bool = False
    sync_offset_deg: float = 0.0     # mechanical − sky; set by sync()
    synced: bool = False
    MOVE_TIMEOUT_S: float = 180.0    # a full CAA revolution is minutes-slow

    @abstractmethod
    async def get_mechanical_position(self) -> float: ...  # deg [0, 360)

    @abstractmethod
    async def move_mechanical(self, mech_deg: float) -> None: ...
    # absolute mechanical move; waits for completion; halts on CancelledError

    @abstractmethod
    async def halt(self) -> None: ...

    async def is_moving(self) -> bool:
        return False

    async def get_reverse(self) -> bool:
        return False

    async def set_reverse(self, value: bool) -> None:
        raise DeviceError("this rotator does not support reverse")

    # ---- sky-space layer (shared by every backend) ----
    async def get_position(self) -> float:
        """Sync-adjusted sky position angle, deg [0, 360)."""
        from ..rotation import mod360
        return mod360(await self.get_mechanical_position() - self.sync_offset_deg)

    async def sync(self, sky_deg: float) -> None:
        """Declare that the CURRENT mechanical position is this sky PA."""
        from ..rotation import mod360
        self.sync_offset_deg = mod360(
            await self.get_mechanical_position() - sky_deg)
        self.synced = True

    async def move_to(self, sky_deg: float) -> None:
        """Absolute sky-PA move through the current offset."""
        from ..rotation import mod360
        await self.move_mechanical(mod360(sky_deg + self.sync_offset_deg))
```

(If `DeviceError` is not defined in `base.py`, import it exactly the way `alpaca.py` does.)

- [ ] **Step 4: Implement `SimRotator` + rig fields + `build_sim_rig` entry** (in `devices/sim.py`; add `Rotator` to the existing `from .base import (...)` block)

In `SimRig.__init__` (after `self.filter_slot = 0`):

```python
        self.rotator_mech_deg = 0.0
        # hidden ground truth: how the camera is "clocked" vs mechanical zero.
        # 0.0 by default so every existing sim solve/TPPA test is byte-identical
        # (the polar_misalignment opt-in precedent); rotate-loop tests set it.
        self.rotator_pa_offset_deg = 0.0
```

New class (place near `SimFocuser`):

```python
class SimRotator(Rotator):
    MOVE_RATE = 5.0  # deg/s

    def __init__(self, rig: SimRig, name: str = "Sim Rotator CAA"):
        super().__init__(name)
        self.rig = rig
        self._halt = asyncio.Event()
        self._moving = False

    async def connect(self) -> None:
        await asyncio.sleep(0.05)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_mechanical_position(self) -> float:
        return self.rig.rotator_mech_deg % 360.0

    async def is_moving(self) -> bool:
        return self._moving

    async def halt(self) -> None:
        self._halt.set()

    async def move_mechanical(self, mech_deg: float) -> None:
        target = mech_deg % 360.0
        self._halt.clear()
        self._moving = True
        try:
            # shortest signed travel, animated in ~2° steps like SimFocuser
            delta = ((target - self.rig.rotator_mech_deg + 180.0) % 360.0) - 180.0
            steps = max(1, int(abs(delta) / 2.0))
            step = delta / steps
            for _ in range(steps):
                if self._halt.is_set():
                    return
                self.rig.rotator_mech_deg = (self.rig.rotator_mech_deg + step) % 360.0
                await asyncio.sleep(abs(step) / self.MOVE_RATE)
            self.rig.rotator_mech_deg = target
        finally:
            self._moving = False
```

In `build_sim_rig()` add `"rotator": SimRotator(rig),` after `"switch": SimSwitch(),`. (Hub connect loops iterate `ROLES`, so the extra key is inert until Task 5 adds the role — same as the `"guide_camera"` key today.)

- [ ] **Step 5: Sim-solver truth hook** (in `solve/simsolver.py`, replace ONLY the `if self.sim_rig is not None:` success branch)

```python
        if self.sim_rig is not None:
            # Physical truth: the camera's sky PA is the rotator's mechanical
            # angle plus how the camera is clocked on it. Both default 0.0, so
            # rigs/tests that never touch the rotator see rotation 0.0 exactly
            # as before.
            rot_pa = (getattr(self.sim_rig, "rotator_mech_deg", 0.0)
                      + getattr(self.sim_rig, "rotator_pa_offset_deg", 0.0)) % 360.0
            return SolveResult(True, ra_hours=self.sim_rig.ra_hours,
                               dec_deg=self.sim_rig.dec_deg,
                               rotation_deg=rot_pa, pixel_scale_arcsec=1.55,
                               message="solved (simulator)")
```

- [ ] **Step 6: Run the new tests + the sim/solve neighborhoods**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotator_devices.py tests/test_providers.py tests/test_hub_solve.py`
Expected: all PASS (defaults keep existing solve behavior identical)

- [ ] **Step 7: Commit**

```bash
git add server/astrodeck/devices/base.py server/astrodeck/devices/sim.py server/astrodeck/solve/simsolver.py server/tests/test_rotator_devices.py
git commit -m "feat(rotator): Rotator ABC (client-side sync), SimRotator, sim-solver rotation truth (spec §2.1, §2.4)"
```

---

### Task 3: `AlpacaRotator`

**Files:**
- Modify: `server/astrodeck/devices/alpaca.py` (new class after `AlpacaFocuser` ~line 609; `DEVICE_CLASSES` ~line 662)
- Test: `server/tests/test_alpaca_rotator.py` (new)

**Interfaces:**
- Consumes: `Rotator` ABC (Task 2), `_AlpacaDevice` mixin.
- Produces: `AlpacaRotator(_AlpacaDevice, Rotator)` with `dev_type = "rotator"`; `DEVICE_CLASSES["rotator"] = AlpacaRotator`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_alpaca_rotator.py
"""AlpacaRotator verb mapping against a recording fake connection."""
import asyncio

import pytest

from astrodeck.devices import alpaca
from astrodeck.devices.alpaca import AlpacaRotator, DEVICE_CLASSES
from astrodeck.devices.base import DeviceError


class RecConn:
    """Records every Alpaca verb; scripted responses per GET method name.
    ``ismoving_seq`` pops one value per ismoving poll (then False)."""

    def __init__(self):
        self.host, self.port = "10.0.0.9", 11111
        self.calls = []
        self.responses = {"canreverse": True, "mechanicalposition": 123.4,
                          "ismoving": False, "reverse": False}
        self.ismoving_seq: list[bool] = []

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(("get", method, params))
        if method == "ismoving" and self.ismoving_seq:
            return self.ismoving_seq.pop(0)
        return self.responses.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(("put", method, params))
        return None


@pytest.fixture()
def rot():
    return AlpacaRotator(RecConn(), 0, "ZWO CAA")


def test_registered_in_device_classes():
    assert DEVICE_CLASSES["rotator"] is AlpacaRotator
    assert AlpacaRotator.dev_type == "rotator"


@pytest.mark.asyncio
async def test_connect_probes_canreverse(rot):
    await rot.connect()
    assert rot.connected is True
    assert rot.can_reverse is True
    assert ("get", "canreverse", {}) in rot.conn.calls


@pytest.mark.asyncio
async def test_connect_survives_missing_canreverse(rot):
    async def boom(dev_type, dev_num, method, **params):
        if method == "canreverse":
            raise DeviceError("not implemented")
        return None
    rot.conn.get = boom
    await rot.connect()
    assert rot.can_reverse is False


@pytest.mark.asyncio
async def test_mechanical_position_reads_driver(rot):
    assert await rot.get_mechanical_position() == pytest.approx(123.4)


@pytest.mark.asyncio
async def test_move_mechanical_puts_and_polls(rot):
    rot.conn.ismoving_seq = [True, True, False]
    await rot.move_mechanical(200.0)
    puts = [c for c in rot.conn.calls if c[0] == "put"]
    assert ("put", "movemechanical", {"Position": 200.0}) in puts
    polls = [c for c in rot.conn.calls if c[1] == "ismoving"]
    assert len(polls) == 3


@pytest.mark.asyncio
async def test_move_halts_on_cancel(rot):
    rot.conn.ismoving_seq = [True] * 100
    task = asyncio.create_task(rot.move_mechanical(90.0))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("put", "halt", {}) in rot.conn.calls


@pytest.mark.asyncio
async def test_move_times_out(rot, monkeypatch):
    monkeypatch.setattr(AlpacaRotator, "MOVE_TIMEOUT_S", 0.5)
    rot.conn.ismoving_seq = [True] * 100
    with pytest.raises(DeviceError):
        await rot.move_mechanical(90.0)
    assert ("put", "halt", {}) in rot.conn.calls


@pytest.mark.asyncio
async def test_reverse_gated_on_capability(rot):
    rot.can_reverse = False
    with pytest.raises(DeviceError):
        await rot.set_reverse(True)
    rot.can_reverse = True
    await rot.set_reverse(True)
    assert ("put", "reverse", {"Reverse": True}) in rot.conn.calls
```

(If `DeviceError` lives elsewhere than `devices.base`, fix the import to match `alpaca.py`'s own.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_alpaca_rotator.py`
Expected: FAIL — `ImportError: cannot import name 'AlpacaRotator'`

- [ ] **Step 3: Implement `AlpacaRotator`** (after `AlpacaFocuser`; add `"rotator": AlpacaRotator,` to `DEVICE_CLASSES`)

```python
class AlpacaRotator(_AlpacaDevice, Rotator):
    dev_type = "rotator"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        try:
            self.can_reverse = bool(await self._get("canreverse"))
        except DeviceError:
            self.can_reverse = False

    async def get_mechanical_position(self) -> float:
        return float(await self._get("mechanicalposition"))

    async def is_moving(self) -> bool:
        return bool(await self._get("ismoving"))

    async def move_mechanical(self, mech_deg: float) -> None:
        await self._put("movemechanical", Position=float(mech_deg))
        waited = 0.0
        try:
            while await self._get("ismoving"):
                await asyncio.sleep(0.25)
                waited += 0.25
                if waited >= self.MOVE_TIMEOUT_S:
                    await self._put("halt")
                    raise DeviceError(
                        f"rotator move timed out after {self.MOVE_TIMEOUT_S:.0f}s")
        except asyncio.CancelledError:
            await self._put("halt")
            raise

    async def halt(self) -> None:
        await self._put("halt")

    async def get_reverse(self) -> bool:
        if not self.can_reverse:
            return False
        try:
            return bool(await self._get("reverse"))
        except DeviceError:
            return False

    async def set_reverse(self, value: bool) -> None:
        if not self.can_reverse:
            raise DeviceError("this rotator does not support reverse")
        await self._put("reverse", Reverse=bool(value))
```

Remember to add `Rotator` to alpaca.py's `from .base import ...` block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_alpaca_rotator.py tests/test_native_backend.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/alpaca.py server/tests/test_alpaca_rotator.py
git commit -m "feat(rotator): AlpacaRotator — mechanical-space verbs, ismoving poll, reverse gating (spec §2.2)"
```

---

### Task 4: `NinaRotator`

**Files:**
- Modify: `server/astrodeck/devices/nina.py` (new class near `NinaFocuser` ~line 507; `_ROLE_CLASSES` ~line 810; `_detail` `role_paths` ~line 904)
- Modify: `server/astrodeck/devices/backends/nina_backend.py:135` (`roles` tuple)
- Test: `server/tests/test_nina_rotator.py` (new)

**Interfaces:**
- Consumes: `Rotator` ABC; `_NinaDevice` mixin (`info()` TTL cache, `self.client.get(path, **params)`); `pick(...)` helper already in nina.py.
- Produces: `NinaRotator(_NinaDevice, Rotator)` with `info_path = "/equipment/rotator/info"`; `_ROLE_CLASSES["rotator"] = (NinaRotator, "/equipment/rotator/info")`; `NinaBackend.roles` includes `"rotator"`; `_detail` probes `("rotator", "rotator")`.

**Grounding note:** the bridge convention is uniform (`/equipment/<dev>/<action>?param=`, halt = `stop-move`, per `NinaFocuser` in the same file). NINA reports both `Position` and `MechanicalPosition` in rotator info; when AstroDeck owns the rotator nobody syncs it inside NINA, so the two are equal — the `pick` fallback is safe. If NINA's actual rotator move endpoint differs at the real scope, that surfaces in the live-CAA checklist, not in these unit tests.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_nina_rotator.py
"""NinaRotator against a scripted fake NinaClient."""
import asyncio

import pytest

from astrodeck.devices.nina import _ROLE_CLASSES, NinaRotator


class FakeClient:
    def __init__(self):
        self.host, self.port = "obs-pc", 1888
        self.calls = []
        self.info = {"Connected": True, "Name": "ZWO CAA",
                     "MechanicalPosition": 77.0, "Position": 77.0,
                     "CanReverse": False, "IsMoving": False}
        self.moving_seq: list[bool] = []

    async def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/equipment/rotator/info":
            info = dict(self.info)
            if self.moving_seq:
                info["IsMoving"] = self.moving_seq.pop(0)
            return info
        return {}


@pytest.fixture()
def rot():
    return NinaRotator(FakeClient(), "rotator")


def test_registered_in_role_classes():
    cls, path = _ROLE_CLASSES["rotator"]
    assert cls is NinaRotator
    assert path == "/equipment/rotator/info"


@pytest.mark.asyncio
async def test_connect_reads_info(rot):
    await rot.connect()
    assert rot.connected is True
    assert rot.name == "ZWO CAA"
    assert rot.can_reverse is False


@pytest.mark.asyncio
async def test_mechanical_position_prefers_mechanical_field(rot):
    rot.client.info["MechanicalPosition"] = 200.5
    rot.client.info["Position"] = 10.0
    assert await rot.get_mechanical_position() == pytest.approx(200.5)


@pytest.mark.asyncio
async def test_move_mechanical_calls_endpoint_and_polls(rot):
    rot.client.moving_seq = [True, False]
    await rot.move_mechanical(150.0)
    paths = [c[0] for c in rot.client.calls]
    assert "/equipment/rotator/move-mechanical" in paths
    call = next(c for c in rot.client.calls
                if c[0] == "/equipment/rotator/move-mechanical")
    assert call[1] == {"position": 150.0}


@pytest.mark.asyncio
async def test_nina_backend_advertises_rotator():
    from astrodeck.devices.backends.nina_backend import NinaBackend
    assert "rotator" in NinaBackend.roles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_nina_rotator.py`
Expected: FAIL — `ImportError: cannot import name 'NinaRotator'`

- [ ] **Step 3: Implement**

```python
class NinaRotator(_NinaDevice, Rotator):
    kind = "rotator"
    info_path = "/equipment/rotator/info"

    async def connect(self) -> None:
        info = await self.info(force=True)
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", "DisplayName", default=self.name)
        self.can_reverse = bool(pick(info, "CanReverse", default=False))

    async def get_mechanical_position(self) -> float:
        # NINA reports both; when AstroDeck owns the rotator nobody syncs it
        # inside NINA, so Position == MechanicalPosition and the fallback is safe.
        v = pick(await self.info(), "MechanicalPosition", "Position", default=0.0)
        return float(v or 0.0) % 360.0

    async def is_moving(self) -> bool:
        return bool(pick(await self.info(force=True), "IsMoving", "Moving",
                         default=False))

    async def move_mechanical(self, mech_deg: float) -> None:
        await self.client.get("/equipment/rotator/move-mechanical",
                              position=float(mech_deg) % 360.0)
        waited = 0.0
        try:
            while await self.is_moving():
                await asyncio.sleep(0.25)
                waited += 0.25
                if waited >= self.MOVE_TIMEOUT_S:
                    await self.halt()
                    raise DeviceError(
                        f"rotator move timed out after {self.MOVE_TIMEOUT_S:.0f}s")
        except asyncio.CancelledError:
            await self.halt()
            raise

    async def halt(self) -> None:
        # Bridge convention mirrors the focuser's stop-move; best-effort — a
        # bridge without the endpoint must not crash a cancel path.
        try:
            await self.client.get("/equipment/rotator/stop-move")
        except DeviceError:
            pass
```

Then: add `"rotator": (NinaRotator, "/equipment/rotator/info"),` to `_ROLE_CLASSES`; append `("rotator", "rotator")` to `_detail`'s `role_paths`; add `"rotator"` to `NinaBackend.roles` in `nina_backend.py` (keep tuple order: append at the end). Add `Rotator` to nina.py's base imports.

- [ ] **Step 4: Run tests**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_nina_rotator.py tests/test_nina.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/nina.py server/astrodeck/devices/backends/nina_backend.py server/tests/test_nina_rotator.py
git commit -m "feat(rotator): NinaRotator — bridge rotator via /equipment/rotator/* (spec §2.3)"
```

---

### Task 5: Role threading + status block

**Files:**
- Modify: `server/astrodeck/devices/backend.py:28-36` (`ROLES`)
- Modify: `server/astrodeck/devices/backends/native_backend.py:25-32` (`_ROLE_TO_DEV_TYPE`) and `:151` (`roles`)
- Modify: `server/astrodeck/drivers.py:81-85` (`_DEV_TYPE_TO_ROLE`) + the stale comment at `:79-80`
- Modify: `server/astrodeck/profiles.py:53` (role list docstring comment)
- Modify: `server/astrodeck/hub.py` (`poll_status` — add rotator block after the focuser block ~line 1983)
- Test: update `server/tests/test_backend_registry.py:117-119`, `server/tests/test_backend_roles_match_served.py`, `server/tests/test_drivers_probe.py:117-133`; add status-block test to `server/tests/test_rotator_devices.py`

**Interfaces:**
- Consumes: device classes from Tasks 2–4.
- Produces: `ROLES == ("camera","telescope","focuser","guider","filterwheel","switch","safety","rotator")` (append at END — UI grids order by this list); `_ROLE_TO_DEV_TYPE["rotator"]="rotator"`; `_DEV_TYPE_TO_ROLE["rotator"]="rotator"`; status `out["rotator"] = {name, sky_deg, mech_deg, moving, synced, can_reverse, reverse}`.

- [ ] **Step 1: Update the existing invariants tests to the NEW expectations (red first)**

In `tests/test_backend_registry.py:117-119`:

```python
def test_roles_constant_matches_device_roles():
    assert ROLES == ("camera", "telescope", "focuser", "guider",
                     "filterwheel", "switch", "safety", "rotator")
```

In `tests/test_drivers_probe.py` — the test at ~:117-133 asserting the ZWO CAA rotator row is SKIPPED: invert it. Keep the same fake management payload (`{"DeviceType": "Rotator", "DeviceNumber": 0, "DeviceName": "ZWO CAA"}`) and assert the probe now RETURNS a row `{"role": "rotator", "name": "ZWO CAA", "dev_type": "rotator", "dev_num": 0}`; update its docstring ("the CAA spec added the mapping").

Append to `tests/test_backend_roles_match_served.py`:

```python
def test_native_serves_rotator():
    from astrodeck.devices.backends.native_backend import _ROLE_TO_DEV_TYPE
    b = get_backend("native")
    assert "rotator" in b.roles
    assert _ROLE_TO_DEV_TYPE["rotator"] == "rotator"


def test_sim_serves_rotator():
    from astrodeck.devices.sim import build_sim_rig
    b = get_backend("sim")
    assert "rotator" in b.roles          # SimBackend.roles = ROLES → automatic
    assert "rotator" in build_sim_rig()


def test_nina_serves_rotator():
    from astrodeck.devices.nina import _ROLE_CLASSES
    b = get_backend("nina")
    assert "rotator" in b.roles
    assert "rotator" in _ROLE_CLASSES
```

Append to `tests/test_rotator_devices.py`:

```python
@pytest.mark.asyncio
async def test_poll_status_rotator_block():
    from astrodeck.hub import hub as global_hub  # match how other hub tests get it
    # If other hub tests construct a fresh Hub() instead, do the same here.
    from astrodeck.devices.sim import SimRig, SimRotator
    rig = SimRig()
    rot = SimRotator(rig)
    await rot.connect()
    rig.rotator_mech_deg = 100.0
    await rot.sync(70.0)
    global_hub.devices["rotator"] = rot
    try:
        out = await global_hub.poll_status()
        assert out["rotator"]["mech_deg"] == pytest.approx(100.0)
        assert out["rotator"]["sky_deg"] == pytest.approx(70.0)
        assert out["rotator"]["synced"] is True
        assert out["rotator"]["moving"] is False
        assert out["rotator"]["can_reverse"] is False
    finally:
        global_hub.devices.pop("rotator", None)
```

(Adapt the hub acquisition line to however `tests/test_hub_capture_precession.py` obtains a hub — construct or import — and mirror it; the assertion block stays as written.)

- [ ] **Step 2: Run to verify the updated tests fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_backend_registry.py tests/test_backend_roles_match_served.py tests/test_drivers_probe.py tests/test_rotator_devices.py`
Expected: the new/updated tests FAIL (ROLES is still 7; probe still skips; no status block)

- [ ] **Step 3: Thread the role**

- `devices/backend.py`: append `"rotator",` to `ROLES` (LAST position).
- `native_backend.py`: `_ROLE_TO_DEV_TYPE["rotator"] = "rotator"` (add line `"rotator": "rotator",`) and `roles = ("camera", "telescope", "focuser", "filterwheel", "switch", "safety", "rotator")`.
- `drivers.py`: add `"rotator": "rotator",` to `_DEV_TYPE_TO_ROLE` and rewrite the `:79-80` comment to say rotator is now mapped (still SKIP genuinely-unknown types like `dome`).
- `profiles.py:53`: role comment becomes `# camera|telescope|focuser|guider|filterwheel|switch|safety|rotator`.
- `hub.py` `poll_status`, immediately after the filterwheel block, following its exact template:

```python
        rot = self.devices.get("rotator")
        if rot and rot.connected:
            try:
                out["rotator"] = {
                    "name": rot.name,
                    "sky_deg": round(await rot.get_position(), 2),
                    "mech_deg": round(await rot.get_mechanical_position(), 2),
                    "moving": await rot.is_moving(),
                    "synced": rot.synced,
                    "can_reverse": rot.can_reverse,
                    "reverse": await rot.get_reverse(),
                }
            except Exception:
                pass
```

- [ ] **Step 4: Run the full role/probe/status neighborhoods**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_backend_registry.py tests/test_backend_roles_match_served.py tests/test_drivers_probe.py tests/test_rotator_devices.py tests/test_native_backend.py tests/test_drivers_api.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/backend.py server/astrodeck/devices/backends/native_backend.py server/astrodeck/drivers.py server/astrodeck/profiles.py server/astrodeck/hub.py server/tests/test_backend_registry.py server/tests/test_backend_roles_match_served.py server/tests/test_drivers_probe.py server/tests/test_rotator_devices.py
git commit -m "feat(rotator): thread the rotator role — ROLES, native/nina/sim/drivers maps, status block (spec §2.5)"
```

---

### Task 6: `RotatorConfig` + config route + motion-guard extension

**Files:**
- Modify: `server/astrodeck/config.py` (new `RotatorConfig` after `ProvidersConfig` ~line 260; `AppConfig` field; `ConfigStore.set_rotator` after `set_providers` ~line 593)
- Modify: `server/astrodeck/api/app.py` (new route after the providers route ~line 707)
- Modify: `server/astrodeck/providers.py:149-157` (`_rig_has_real_motion`)
- Test: `server/tests/test_rotator_config.py` (new); extend `server/tests/test_providers.py`

**Interfaces:**
- Consumes: `_config_payload`, `require`, `CAP_CONFIG_BACKEND`, `bus`, `redacted` — all already imported in app.py.
- Produces: `RotatorConfig(range_type="full", range_start_deg=0.0, tolerance_deg=1.0)`; `AppConfig.rotator: RotatorConfig`; `config_store.set_rotator(RotatorConfig) -> AppConfig` (raises `ValueError` on bad values); `POST /api/config/rotator`; motion guard checks `("telescope", "focuser", "rotator")`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_rotator_config.py
"""RotatorConfig validation + the /api/config/rotator route (422 contract)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)   # force fresh load from tmp
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_defaults():
    from astrodeck.config import RotatorConfig
    rc = RotatorConfig()
    assert (rc.range_type, rc.range_start_deg, rc.tolerance_deg) == ("full", 0.0, 1.0)


def test_set_rotator_validates(tmp_path, monkeypatch):
    from astrodeck.config import RotatorConfig, config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(range_type="diagonal"))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(range_start_deg=360.0))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(range_start_deg=-1.0))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(tolerance_deg=0.0))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(tolerance_deg=45.1))
    cfg = config_store.set_rotator(
        RotatorConfig(range_type="half", range_start_deg=245.0, tolerance_deg=2.0))
    assert cfg.rotator.range_type == "half"


def test_route_422_on_junk(client):
    r = client.post("/api/config/rotator",
                    json={"range_type": "diagonal", "range_start_deg": 0.0,
                          "tolerance_deg": 1.0})
    assert r.status_code == 422


def test_route_persists_and_returns_config(client):
    r = client.post("/api/config/rotator",
                    json={"range_type": "quarter", "range_start_deg": 245.0,
                          "tolerance_deg": 1.5})
    assert r.status_code == 200
    assert r.json()["rotator"] == {"range_type": "quarter",
                                   "range_start_deg": 245.0,
                                   "tolerance_deg": 1.5}
    r2 = client.get("/api/config")
    assert r2.json()["rotator"]["range_type"] == "quarter"
```

(If the drivers-api client fixture pattern needs auth headers for `CAP_CONFIG_BACKEND` in tests, mirror EXACTLY how `tests/test_drivers_api.py` posts to `/api/config/*` — same fixture, same auth story.)

Append to `tests/test_providers.py` (imports/FakeHub/FakeDev already exist there — reuse them):

```python
def test_solve_refuses_sim_when_real_rotator_connected(monkeypatch):
    """SAFETY: a real ROTATOR is motion hardware too — a faked solve would feed
    invented angles to a real CAA (spec §3.5.1)."""
    monkeypatch.setattr(providers, "find_astap", lambda: None)
    hub = FakeHub(mode="nina", devices={"rotator": FakeDev(backend="alpaca"),
                                        "camera": FakeDev(backend="sim")})
    with pytest.raises(DeviceError):
        providers.resolve("solve", hub)
    assert providers.resolve_all(hub)["solve"]["kind"] == "unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotator_config.py tests/test_providers.py`
Expected: FAIL — `ImportError: cannot import name 'RotatorConfig'`; the new providers test fails (rotator not in the guard)

- [ ] **Step 3: Implement**

`config.py` — after `ProvidersConfig`:

```python
class RotatorConfig(BaseModel):
    """Rotator mechanical range-of-motion + rotate-loop tolerance (spec §3.2).
    range_type: "full" | "half" | "quarter" — cable-wrap limiting (parity §11.2).
    range_start_deg is MECHANICAL degrees (set from the UI's "Set to current
    position"). tolerance_deg is the rotate loop's mod-180 convergence bound."""
    range_type: str = "full"
    range_start_deg: float = 0.0
    tolerance_deg: float = 1.0
```

`AppConfig` gains (next to `providers`): `rotator: RotatorConfig = Field(default_factory=RotatorConfig)` — match exactly how `providers` is declared on `AppConfig` (same Field style).

`ConfigStore.set_rotator` — directly after `set_providers`:

```python
    def set_rotator(self, rotator: "RotatorConfig") -> AppConfig:
        """Persist the rotator ROM/tolerance config; write-time validated so a
        junk range never reaches the rotate loop (route maps ValueError→422)."""
        if rotator.range_type not in ("full", "half", "quarter"):
            raise ValueError(
                f"unknown range_type: {rotator.range_type!r} — "
                f"valid values are full, half, quarter")
        if not (0.0 <= rotator.range_start_deg < 360.0):
            raise ValueError("range_start_deg must be in [0, 360)")
        if not (0.0 < rotator.tolerance_deg <= 45.0):
            raise ValueError("tolerance_deg must be in (0, 45]")
        cfg = self.cfg()
        cfg.rotator = rotator
        return self.bump_and_save()
```

`api/app.py` — after the providers route, same shape:

```python
    @app.post("/api/config/rotator",
              dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def set_rotator_config(body: RotatorConfig):
        try:
            cfg = await asyncio.to_thread(config_store.set_rotator, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload()
```

(add `RotatorConfig` to app.py's existing `from ..config import ...` block.)

`providers.py` `_rig_has_real_motion`: change the loop line to
`for role in ("telescope", "focuser", "rotator"):` and extend the docstring's "(mount/focuser)" to "(mount/focuser/rotator)".

- [ ] **Step 4: Run tests**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotator_config.py tests/test_providers.py tests/test_providers_vocabulary.py tests/test_drivers_api.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/api/app.py server/astrodeck/providers.py server/tests/test_rotator_config.py server/tests/test_providers.py
git commit -m "feat(rotator): RotatorConfig + /api/config/rotator (422) + motion-guard covers real rotators (spec §3.2, §3.5.1)"
```

---

### Task 7: `hub.rotate_to_pa` — the rotate loop

**Files:**
- Modify: `server/astrodeck/hub.py` (new method directly after `solve_and_sync` ~line 1598; import `rotation` at top: `from . import rotation as _rotation` next to the other intra-package imports)
- Test: `server/tests/test_rotate_to_pa.py` (new)

**Interfaces:**
- Consumes: `providers.pick_solver`, `exposure_guard`, `_publish_preview`, `save_fits`, `CAPTURE_DIR`, `effective_optics`, `_motion_epoch`/`_motion_committed_clean`, `config_store.cfg().rotator`, `_rotation.*` (Task 1), `Rotator` device (Task 2).
- Produces: `async hub.rotate_to_pa(target_pa_deg: float, exposure_s: float = 3.0, max_attempts: int = 5) -> dict` returning `{"rotated": bool, "pa_deg": float, "adjusted_to": float | None, "attempts": int, "error_deg": float}` (or `{"rotated": False, "aborted": True, ...}` on fence abort); raises `DeviceError` on solve failure / non-convergence.

- [ ] **Step 1: Write the failing tests** — end-to-end on the sim rig; connect via the hub's sim path the same way `tests/test_hub_solve.py` sets up a sim hub (mirror its fixture; the assertions below are the contract):

```python
# server/tests/test_rotate_to_pa.py
"""rotate_to_pa end-to-end on the sim rig: the loop must DISCOVER the camera's
clock offset via solve+sync and converge — no shortcuts."""
import pytest

# Mirror tests/test_hub_solve.py's sim-hub fixture exactly (fresh Hub connected
# to the sim rig, ASTAP forced absent so pick_solver returns SimSolver).


@pytest.mark.asyncio
async def test_converges_on_target_pa(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 30.0        # hidden truth the loop must discover
    rig.rotator_mech_deg = 10.0             # camera PA = 40.0
    result = await sim_hub.rotate_to_pa(120.0)
    assert result["rotated"] is True
    assert result["attempts"] >= 2          # solve → move → verify solve
    truth = (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0
    # mod-180 distance to target within default 1.0° tolerance
    d = abs((truth - 120.0 + 90.0) % 180.0 - 90.0)
    assert d <= 1.0
    assert result["adjusted_to"] is None    # FULL range: nothing adjusted


@pytest.mark.asyncio
async def test_full_range_may_take_180_twin(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 350.0
    result = await sim_hub.rotate_to_pa(175.0)   # twin at -5° is far closer
    assert result["rotated"] is True
    truth = rig.rotator_mech_deg % 360.0
    d = abs((truth - 175.0 + 90.0) % 180.0 - 90.0)
    assert d <= 1.0
    # the physical move stayed small (took the twin, not the 175° sweep)
    assert abs(((350.0 - truth + 180.0) % 360.0) - 180.0) < 30.0


@pytest.mark.asyncio
async def test_limited_range_reports_adjustment(sim_hub, monkeypatch):
    from astrodeck.config import RotatorConfig, config_store
    config_store.set_rotator(RotatorConfig(range_type="quarter",
                                           range_start_deg=0.0,
                                           tolerance_deg=1.0))
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 10.0
    result = await sim_hub.rotate_to_pa(100.0)   # QUARTER [0,90): 100 maps to 10
    assert result["rotated"] is True
    assert result["adjusted_to"] is not None     # honesty: framing changed
    assert 0.0 <= rig.rotator_mech_deg % 360.0 < 91.0   # stayed in range (+tol)


@pytest.mark.asyncio
async def test_solve_failure_raises_device_error(sim_hub, monkeypatch):
    from astrodeck.devices.base import DeviceError
    from astrodeck.solve.base import SolveResult

    async def fail_solve(self, fits_path, **kw):
        return SolveResult(False, message="clouds")
    from astrodeck.solve.simsolver import SimSolver
    monkeypatch.setattr(SimSolver, "solve", fail_solve)
    with pytest.raises(DeviceError):
        await sim_hub.rotate_to_pa(120.0)


@pytest.mark.asyncio
async def test_requires_rotator(sim_hub):
    from astrodeck.devices.base import DeviceError
    sim_hub.devices.pop("rotator", None)
    with pytest.raises(DeviceError):
        await sim_hub.rotate_to_pa(120.0)
```

(The `sim_hub` fixture: copy the sim-hub setup from `tests/test_hub_solve.py` into this file's own fixture — including whatever it does to route `pick_solver` to the SimSolver — and additionally ensure `"rotator"` is connected in `sim_hub.devices`; after Task 5 the sim connect path does that automatically. Reset the rotator config to defaults in fixture teardown: `config_store.set_rotator(RotatorConfig())`, and point `config_store._path` at tmp_path exactly like `test_rotator_config.py` so tests never touch the real config file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotate_to_pa.py`
Expected: FAIL — `AttributeError: 'Hub' object has no attribute 'rotate_to_pa'`

- [ ] **Step 3: Implement** (after `solve_and_sync`; the capture/solve block deliberately mirrors it)

```python
    async def rotate_to_pa(self, target_pa_deg: float,
                           exposure_s: float = 3.0,
                           max_attempts: int = 5) -> dict:
        """Solve→sync→rotate loop (NINA §11.3 parity, bounded): physically
        enforce a sky position angle. Syncs the ROTATOR only (never the mount).
        The solver is resolved up front (motion-guarded — a sim solver can
        never drive a real rotator) and a solve failure raises DeviceError;
        goto_and_center degrades it to rotation_skipped."""
        rot = self.require("rotator")
        cam: Camera = self.require("camera")
        from . import providers as _providers
        from . import rotation as _rotation
        solver = _providers.pick_solver(self)
        rcfg = config_store.cfg().rotator
        target = _rotation.mod360(target_pa_deg)
        adjusted_to = None
        moved = False
        error = None
        orientation = None
        epoch = self._motion_epoch
        for attempt in range(1, max_attempts + 1):
            if not self._motion_committed_clean(epoch):
                bus.log("warning", "rotate abandoned: aborted", "rotator")
                return {"rotated": False, "aborted": True,
                        "pa_deg": orientation, "adjusted_to": adjusted_to,
                        "attempts": attempt - 1, "error_deg": error}
            tel = self.devices.get("telescope")
            ra_hint = dec_hint = None
            if tel is not None and tel.connected:
                try:
                    ra_hint, dec_hint = await tel.get_position()
                    if ra_hint is not None:
                        ra_hint, dec_hint = await self.from_mount_frame(
                            tel, ra_hint, dec_hint)
                except Exception:
                    ra_hint = dec_hint = None
            async with self.exposure_guard("rotate to PA"):
                frame = await cam.expose(exposure_s, 200, 30, binning=2)
            self.last_frame = frame
            await self._publish_preview(frame)
            tmp = CAPTURE_DIR / "_solve" / "rotate.fits"
            await asyncio.to_thread(
                save_fits, frame, tmp,
                ra_hours=ra_hint, dec_deg=dec_hint, instrument=cam.name)
            opt = self.effective_optics()
            result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                        fov_deg_hint=opt["fov_h_deg"] or None)
            if not result.success:
                raise DeviceError(f"rotate: plate solve failed: {result.message}")
            orientation = _rotation.mod360(result.rotation_deg)
            await rot.sync(orientation)
            mech = await rot.get_mechanical_position()
            prev = target
            target = _rotation.map_sky_target(prev, mech, rot.sync_offset_deg,
                                              rcfg.range_type,
                                              rcfg.range_start_deg)
            if not _rotation.angle_equals(target, prev, 0.1):
                # a ±90°/±270° adjustment genuinely changes framing (only ±180
                # is equivalent) — surface it, never silently (spec §3.3).
                adjusted_to = target
                bus.log("warning",
                        f"rotator: target PA {prev:.1f}° adjusted to "
                        f"{target:.1f}° by the {rcfg.range_type} mechanical "
                        f"range", "rotator")
            distance = _rotation.shortest_rotation(target, orientation,
                                                   rcfg.range_type)
            error = abs(((distance + 90.0) % 180.0) - 90.0)  # mod-180 magnitude
            bus.publish("rotator", action="rotating", attempt=attempt,
                        orientation_deg=round(orientation, 2),
                        target_deg=round(target, 2))
            if _rotation.angle_equals_mod180(distance, 0.0, rcfg.tolerance_deg):
                bus.publish("rotator", action="rotated",
                            pa_deg=round(orientation, 2))
                if moved and self.guider and self.guider.connected:
                    bus.log("warning",
                            "camera rotated — guide calibration may be stale; "
                            "PHD2 will re-calibrate or flip as needed", "guide")
                return {"rotated": True, "pa_deg": orientation,
                        "adjusted_to": adjusted_to, "attempts": attempt,
                        "error_deg": round(error, 2)}
            await rot.move_to(_rotation.mod360(orientation + distance))
            moved = True
        raise DeviceError(
            f"rotator failed to converge after {max_attempts} attempts "
            f"(last error {error:.1f}°)")
```

(`Camera`, `CAPTURE_DIR`, `save_fits`, `config_store`, `bus`, `DeviceError` are all already imported in hub.py — reuse; do NOT re-import.)

- [ ] **Step 4: Run tests**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotate_to_pa.py tests/test_hub_solve.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/hub.py server/tests/test_rotate_to_pa.py
git commit -m "feat(rotator): hub.rotate_to_pa — bounded solve/sync/rotate loop with range honesty (spec §3.3)"
```

---

### Task 8: Integration — `goto_and_center`, sequence, goto route, meridian flip

**Files:**
- Modify: `server/astrodeck/hub.py:1600-1666` (`goto_and_center` signature + rotate-first block + result key)
- Modify: `server/astrodeck/sequence/engine.py:829-843` (`_setup_target` center branch)
- Modify: `server/astrodeck/sequence/models.py:45` (comment only)
- Modify: `server/astrodeck/api/app.py:315-319` (`GotoBody`) and the goto route's center branch
- Test: `server/tests/test_goto_rotation.py` (new)

**Interfaces:**
- Consumes: `rotate_to_pa` (Task 7).
- Produces: `goto_and_center(..., rotation_deg: float | None = None)`; result gains `"rotation": dict | None` and `"rotation_skipped": True` on rotate failure; `GotoBody.rotation_deg: float | None = None`.

- [ ] **Step 1: Write the failing tests** (reuse the Task 7 `sim_hub` fixture pattern — factor it into a tiny shared helper module `tests/_simhub.py` if that is cleaner than duplicating; both files importing one fixture helper is fine)

```python
# server/tests/test_goto_rotation.py
"""goto_and_center + rotation: rotate-before-center order, degrade-on-failure,
meridian-flip mod-180 no-op, sequence/route threading."""
import pytest


@pytest.mark.asyncio
async def test_goto_and_center_rotates_before_centering(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 20.0
    rig.rotator_mech_deg = 0.0
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True
    assert result["rotation"]["rotated"] is True
    truth = (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0
    assert abs((truth - 90.0 + 90.0) % 180.0 - 90.0) <= 1.0


@pytest.mark.asyncio
async def test_goto_without_rotation_unchanged(sim_hub):
    result = await sim_hub.goto_and_center(5.0, 10.0)
    assert result["centered"] is True
    assert result.get("rotation") is None
    assert "rotation_skipped" not in result


@pytest.mark.asyncio
async def test_rotation_failure_degrades_not_aborts(sim_hub, monkeypatch):
    async def boom(self, *a, **k):
        from astrodeck.devices.base import DeviceError
        raise DeviceError("clouds over the rotate solve")
    monkeypatch.setattr(type(sim_hub), "rotate_to_pa", boom)
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True          # centering still ran
    assert result["rotation_skipped"] is True


@pytest.mark.asyncio
async def test_no_rotator_means_advisory_only(sim_hub):
    sim_hub.devices.pop("rotator", None)
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True
    assert result.get("rotation") is None      # silently advisory, as today


@pytest.mark.asyncio
async def test_already_at_pa_mod180_noops(sim_hub):
    """Meridian-flip contract: a frame 180° from target is EQUAL — the loop
    must converge with zero physical moves."""
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 270.0               # camera PA 270 == target 90 mod 180
    before = rig.rotator_mech_deg
    result = await sim_hub.rotate_to_pa(90.0)
    assert result["rotated"] is True
    assert result["attempts"] == 1
    assert rig.rotator_mech_deg == pytest.approx(before)


def test_goto_body_carries_rotation():
    from astrodeck.api.app import GotoBody
    b = GotoBody(ra_hours=1.0, dec_deg=2.0, rotation_deg=133.0)
    assert b.rotation_deg == 133.0
    assert GotoBody(ra_hours=1.0, dec_deg=2.0).rotation_deg is None


def test_sequence_passes_rotation():
    """_setup_target's centered branch forwards target.rotation_deg."""
    import inspect
    from astrodeck.sequence import engine
    src = inspect.getsource(engine.SequenceEngine._setup_target)
    assert "rotation_deg=target.rotation_deg" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_goto_rotation.py`
Expected: FAIL — `TypeError: goto_and_center() got an unexpected keyword argument 'rotation_deg'`

- [ ] **Step 3: Implement**

`hub.py` `goto_and_center`: signature becomes

```python
    async def goto_and_center(self, ra_hours: float, dec_deg: float,
                              tolerance_deg: float = 0.02,
                              max_attempts: int = 3,
                              solve_exposure_s: float = 3.0,
                              rotation_deg: float | None = None) -> dict:
```

Immediately after the existing unpark/track motion-lock block (before `last_err = None`), insert:

```python
        # Rotate BEFORE centering (NINA CenterAndRotate order, spec §3.4): slew
        # once so the solved field is the target's, run the rotate loop, then
        # fall through to the normal centering attempts (which re-slew anyway).
        # A rotate failure DEGRADES — never abort a slew that already happened.
        rotation_result: dict | None = None
        rotation_skipped = False
        rot = self.devices.get("rotator")
        if rotation_deg is not None and rot is not None and rot.connected:
            async with self._motion_lock:
                if not self._motion_committed_clean(epoch):
                    bus.log("warning", "goto abandoned: aborted before rotation",
                            "mount")
                    return {"centered": False, "error_arcmin": None,
                            "attempts": 0, "aborted": True, "rotation": None}
                slew_ra, slew_dec = await self.to_mount_frame(tel, ra_hours, dec_deg)
                await tel.slew(slew_ra, slew_dec)
            try:
                rotation_result = await self.rotate_to_pa(
                    rotation_deg, exposure_s=solve_exposure_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                bus.log("warning",
                        f"rotation to PA {rotation_deg:.0f}° failed ({e}); "
                        f"continuing without rotation", "rotator")
                rotation_skipped = True
```

Then extend the FOUR return dicts in the attempts loop + final return: add `"rotation": rotation_result` to each, and additionally `"rotation_skipped": True` when `rotation_skipped` (cleanest: build a small helper dict `_rot_keys = {"rotation": rotation_result, **({"rotation_skipped": True} if rotation_skipped else {})}` right after the block above and `| _rot_keys` each return literal — note the early pre-motion abort return above already carries its own).

`sequence/engine.py` `_setup_target` centered branch — the `goto_and_center` call becomes:

```python
                result = await _bounded(
                    self.hub.goto_and_center(target.ra_hours, target.dec_deg,
                                             rotation_deg=target.rotation_deg),
                    GOTO_TIMEOUT_S + (300 if target.rotation_deg is not None else 0),
                    f"goto+center {target.name}")
```

(the +300 s headroom covers up to 5 extra rotate-loop solves; the constant itself is untouched.)

`sequence/models.py:45` comment becomes:
`rotation_deg: float | None = None  # target camera angle (PA) — enforced when a rotator is connected; guidance otherwise`

`api/app.py`: `GotoBody` gains `rotation_deg: float | None = None`; the center branch becomes
`return _spawn("goto", hub.goto_and_center(body.ra_hours, body.dec_deg, rotation_deg=body.rotation_deg))`.

- [ ] **Step 4: Run tests + neighbors**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_goto_rotation.py tests/test_rotate_to_pa.py tests/test_sequence_engine.py`
(If `tests/test_sequence_engine.py` doesn't exist under that name, run the sequence tests that DO cover `_setup_target` — find them with `grep -l _setup_target server/tests/`.)
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/hub.py server/astrodeck/sequence/engine.py server/astrodeck/sequence/models.py server/astrodeck/api/app.py server/tests/test_goto_rotation.py
git commit -m "feat(rotator): rotation rides goto_and_center/sequence/goto — rotate-first, degrade-on-failure (spec §3.4)"
```

---

### Task 9: Rotator action API routes

**Files:**
- Modify: `server/astrodeck/api/app.py` (new body models near `FocuserMoveBody` ~line 332; new routes near the focuser routes ~line 1975)
- Test: `server/tests/test_rotator_api.py` (new)

**Interfaces:**
- Consumes: `hub.require`, `_err`, `_spawn`, `CAP_CONTROL_CAPTURE`, `config_store`, `hub._capture_lock`/`hub._capture_busy`, `rotation` math, `rotate_to_pa`.
- Produces: `POST /api/rotator/move {position_deg}` → `{"target_deg", "adjusted", "started"}`; `POST /api/rotator/halt`; `POST /api/rotator/reverse {reverse}`; `POST /api/rotator/rotate-to-pa {target_pa_deg, exposure_s?}` (spawned).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_rotator_api.py
"""Rotator route contracts: 409 without device, exposure refusal, reverse
gating, range-mapped move response. Uses the drivers-api client fixture and
manipulates the app module's hub directly (routes read the module-global hub
at call time)."""
import pytest
from fastapi.testclient import TestClient

from astrodeck.devices.sim import SimRig, SimRotator


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


@pytest.fixture()
def hub():
    from astrodeck.api import app as app_module
    return app_module.hub   # adjust to the module's actual hub symbol if named differently


@pytest.fixture()
def rot(hub):
    rig = SimRig()
    r = SimRotator(rig)
    r.connected = True          # SimRotator.connect() only flips this flag
    hub.devices["rotator"] = r
    yield r
    hub.devices.pop("rotator", None)
    hub._busy.pop("rotator", None)
    hub._busy.pop("rotate_to_pa", None)


def test_move_409_without_rotator(client):
    r = client.post("/api/rotator/move", json={"position_deg": 90.0})
    assert r.status_code == 409
    assert "rotator" in r.json()["detail"]


def test_move_refused_while_exposing(client, hub, rot, monkeypatch):
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    monkeypatch.setattr(hub, "_capture_busy", "sequence exposure", raising=False)
    r = client.post("/api/rotator/move", json={"position_deg": 90.0})
    assert r.status_code == 409
    assert "busy" in r.json()["detail"]


def test_move_returns_range_mapped_target(client, hub, rot):
    from astrodeck.config import RotatorConfig, config_store
    config_store.set_rotator(RotatorConfig(range_type="quarter",
                                           range_start_deg=0.0,
                                           tolerance_deg=1.0))
    r = client.post("/api/rotator/move", json={"position_deg": 100.0})
    assert r.status_code == 200
    body = r.json()
    assert body["adjusted"] is True
    assert body["target_deg"] == pytest.approx(10.0)   # QUARTER map: 100→10
    assert body["started"] == "rotator"


def test_reverse_400_when_unsupported(client, hub, rot):
    r = client.post("/api/rotator/reverse", json={"reverse": True})
    assert r.status_code == 400


def test_halt_ok(client, hub, rot):
    r = client.post("/api/rotator/halt")
    assert r.status_code == 200


def test_rotate_to_pa_spawns(client, hub, rot, monkeypatch):
    async def instant(self, *a, **k):
        return {"rotated": True}
    monkeypatch.setattr(type(hub), "rotate_to_pa", instant)
    r = client.post("/api/rotator/rotate-to-pa", json={"target_pa_deg": 120.0})
    assert r.status_code == 200
    assert r.json() == {"started": "rotate_to_pa"}
```

(First discovery step: confirm how `app.py` names its hub global — `grep -n "^hub" server/astrodeck/api/app.py` / check its imports — and adjust the `hub` fixture symbol; the ROUTE code below is fixed regardless.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotator_api.py`
Expected: FAIL — 404s (routes don't exist)

- [ ] **Step 3: Implement** — body models next to `FocuserMoveBody`:

```python
class RotatorMoveBody(BaseModel):
    position_deg: float


class RotatorReverseBody(BaseModel):
    reverse: bool


class RotateToPaBody(BaseModel):
    target_pa_deg: float
    exposure_s: float = 3.0
```

Routes (near the focuser routes; `from ..rotation import angle_equals, map_sky_target, mod360` added to app.py's intra-package imports):

```python
    @app.post("/api/rotator/move",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_move(body: RotatorMoveBody):
        try:
            rot = hub.require("rotator")
        except DeviceError as e:
            raise _err(e)
        # spec §3.5.2: a manual rotation mid-exposure ruins the frame — refuse.
        if hub._capture_lock.locked():
            raise HTTPException(
                409, f"camera is busy ({hub._capture_busy or 'exposing'}); "
                     f"rotator move refused")
        rcfg = config_store.cfg().rotator
        mech = await rot.get_mechanical_position()
        target = map_sky_target(body.position_deg, mech, rot.sync_offset_deg,
                                rcfg.range_type, rcfg.range_start_deg)
        adjusted = not angle_equals(target, mod360(body.position_deg), 0.1)
        return _spawn("rotator", rot.move_to(target)) | {
            "target_deg": round(target, 2), "adjusted": adjusted}

    @app.post("/api/rotator/halt",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_halt():
        try:
            rot = hub.require("rotator")
        except DeviceError as e:
            raise _err(e)
        for name in ("rotator", "rotate_to_pa"):
            task = hub._busy.get(name)
            if task and not task.done():
                task.cancel()
        await rot.halt()
        return {"ok": True}

    @app.post("/api/rotator/reverse",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_reverse(body: RotatorReverseBody):
        try:
            rot = hub.require("rotator")
        except DeviceError as e:
            raise _err(e)
        if not rot.can_reverse:
            raise HTTPException(400, "this rotator does not support reverse")
        await rot.set_reverse(body.reverse)
        return {"reverse": body.reverse}

    @app.post("/api/rotator/rotate-to-pa",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_rotate_to_pa(body: RotateToPaBody):
        try:
            hub.require("rotator")
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("rotate_to_pa",
                      hub.rotate_to_pa(body.target_pa_deg, body.exposure_s))
```

- [ ] **Step 4: Run tests**

Run: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q tests/test_rotator_api.py tests/test_rotator_config.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/api/app.py server/tests/test_rotator_api.py
git commit -m "feat(rotator): action routes — move (range-mapped, exposure-guarded), halt, reverse, rotate-to-pa (spec §4)"
```

---

### Task 10: UI foundations — types, API wrapper, `rotation.ts`, labels, motion mirror

**Files:**
- Modify: `ui/src/types.ts` (RigStatus block ~line 42; `RotatorConfig` + `AppConfig.rotator` near ProvidersConfig ~line 480)
- Modify: `ui/src/api/backends.ts` (wrapper after `setProvidersConfig` ~line 219)
- Create: `ui/src/lib/rotation.ts`
- Modify: `ui/src/lib/equipment.ts` (`hasRealMotion` — add rotator)
- Modify: `ui/src/components/settings/backendMeta.ts` (`ALL_ROLES`, `ROLE_LABEL`)
- Modify: `ui/src/components/settings/BackendLinkGrid.tsx` (delete its local `ROLE_ORDER`/`ROLE_LABEL`, import from backendMeta)
- Test: `ui/src/lib/__tests__/rotation.test.ts` (new); extend `ui/src/lib/__tests__/equipment.test.ts`

**Interfaces:**
- Produces (consumed by Tasks 11–12): `RigStatus.rotator?: RotatorStatus` where `RotatorStatus = { name: string; sky_deg: number; mech_deg: number; moving: boolean; synced: boolean; can_reverse: boolean; reverse: boolean }`; `RotatorConfig = { range_type: "full" | "half" | "quarter"; range_start_deg: number; tolerance_deg: number }`; `AppConfig.rotator?: RotatorConfig`; `setRotatorConfig(cfg: RotatorConfig): Promise<AppConfig>`; from `lib/rotation.ts`: `mod360`, `angleEquals`, `angleEqualsMod180`, `targetMechanicalPosition`, `mapSkyTarget`, `shortestRotation`, and `adjustedPa(targetPa: number, rot: {sky_deg: number; mech_deg: number}, cfg: RotatorConfig): { target: number; adjusted: boolean }`.

- [ ] **Step 1: Write the failing test** — `ui/src/lib/__tests__/rotation.test.ts`, using the equipment.test.ts harness verbatim (same `test`/`eq` helpers, same globalThis exit cast) and the **same numeric vectors as `server/tests/test_rotation.py`** (all RANGE_MAP rows, the two map_sky_target cases, the three shortest_rotation FULL cases, the two limited-range cases, plus):

```ts
test("adjustedPa flags a framing change", () => {
  const cfg = { range_type: "quarter" as const, range_start_deg: 0, tolerance_deg: 1 };
  const rot = { sky_deg: 10, mech_deg: 10 };            // offset 0
  const r = adjustedPa(100, rot, cfg);                  // QUARTER [0,90): 100→10
  eq(Math.round(r.target), 10);
  eq(r.adjusted, true);
  const r2 = adjustedPa(50, rot, cfg);
  eq(r2.adjusted, false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `ui/`): `npx tsx src/lib/__tests__/rotation.test.ts`
Expected: FAIL — cannot find module `../rotation`

- [ ] **Step 3: Implement `ui/src/lib/rotation.ts`** — a line-for-line TS mirror of `server/astrodeck/rotation.py` (same function names camelCased, same `_EPS = 1e-13`, same comments pointing at parity §11.2/§11.4; JS `%` is signed, so implement `mod360 = (a) => ((a % 360) + 360) % 360` and use it everywhere Python's `%` appears), plus:

```ts
// offset = mechanical − sky (the server Rotator ABC's sync_offset_deg is not
// on the wire; both live values are, so derive it).
export function adjustedPa(
  targetPa: number,
  rot: { sky_deg: number; mech_deg: number },
  cfg: { range_type: "full" | "half" | "quarter"; range_start_deg: number },
): { target: number; adjusted: boolean } {
  const offset = mod360(rot.mech_deg - rot.sky_deg);
  const target = mapSkyTarget(targetPa, rot.mech_deg, offset,
    cfg.range_type, cfg.range_start_deg);
  return { target, adjusted: !angleEquals(target, mod360(targetPa), 0.1) };
}
```

- [ ] **Step 4: types + wrapper + labels + motion mirror**

`types.ts`: add to `RigStatus` (after `filterwheel?`): `rotator?: RotatorStatus;` and export the two interfaces exactly as in this task's Interfaces block; add `rotator?: RotatorConfig;` to `AppConfig` next to `providers?`.

`api/backends.ts`:

```ts
/** POST /api/config/rotator → persist the rotator range-of-motion + tolerance
 *  (CAA spec §3.2). config.backend-gated; 422 on invalid values. */
export const setRotatorConfig = (cfg: RotatorConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/rotator", cfg);
```

`backendMeta.ts`: append `"rotator"` to `ALL_ROLES`; add `rotator: "Rotator"` to `ROLE_LABEL`.

`BackendLinkGrid.tsx`: delete its local `ROLE_ORDER` and `ROLE_LABEL` consts; `import { ALL_ROLES, ROLE_LABEL } from "./backendMeta";` and use `ALL_ROLES` where `ROLE_ORDER` was (same `.indexOf` → 99 fallback behavior for unknown roles). This kills the duplicate that already drifted.

`lib/equipment.ts` `hasRealMotion`: wherever it checks the telescope/focuser roles, add `"rotator"` to the same list (read the function first; keep its existing shape). Add one test to `equipment.test.ts`:

```ts
test("hasRealMotion counts a real rotator", () => {
  // mirror the existing hasRealMotion test's fixture shape with role "rotator"
  // on a real (non-sim) backend and assert true.
});
```

(Write the real fixture by copying the adjacent `hasRealMotion` test in the same file — same data shape, role swapped.)

- [ ] **Step 5: Run UI tests + build**

Run (from `ui/`): `npx tsx src/lib/__tests__/rotation.test.ts && npx tsx src/lib/__tests__/equipment.test.ts && npm run build`
Expected: both test files all-pass; build clean

- [ ] **Step 6: Commit**

```bash
git add ui/src/types.ts ui/src/api/backends.ts ui/src/lib/rotation.ts ui/src/lib/equipment.ts ui/src/lib/__tests__/rotation.test.ts ui/src/lib/__tests__/equipment.test.ts ui/src/components/settings/backendMeta.ts ui/src/components/settings/BackendLinkGrid.tsx
git commit -m "feat(ui/rotator): types, config wrapper, rotation.ts math mirror (shared vectors), role labels, motion mirror (spec §5.2, §5.4)"
```

---

### Task 11: `RotatorCard` — arc dial + controls

**Files:**
- Create: `ui/src/components/equipment/RotatorCard.tsx`
- Modify: `ui/src/views/EquipmentView.tsx` (render after `<TasksPanel …/>`)

**Interfaces:**
- Consumes: `useStatus`/`useConfig`/`useCanConfigBackend`, `setRotatorConfig`, `api.post`, `adjustedPa` + math from `lib/rotation.ts`, `Panel`/`InfoDot`/`Icon` UI kit, theme tokens.
- Produces: `<RotatorCard />` (no props — reads the store), rendered only when `status?.rotator` exists.

- [ ] **Step 1: Implement the component** (this is UI work — the "test" is the build plus Task 12's assert file for any extracted pure helpers; keep ALL geometry helpers pure and exported for that):

```tsx
// RotatorCard.tsx — Equipment card for the rotator/CAA (spec §5.1). Readouts
// (sky PA + mechanical — both, honesty when unsynced), manual move/nudge/halt,
// reverse (only when supported), solve-driven "Rotate to PA", and the ROM
// (range-of-motion) block: a display-only SVG arc dial over segmented
// FULL/HALF/QUARTER, range-start with "Set to current position", tolerance.
// The dial is deliberately NOT drag-interactive — gloved hands at the scope;
// all input goes through the controls. Colors via theme vars only (night mode).
import { useEffect, useState, type JSX } from "react";
import type { RotatorConfig } from "../../types";
import { api, ApiError } from "../../api";
import { setRotatorConfig } from "../../api/backends";
import { useConfig, useStatus, useStore } from "../../store";
import { useCanConfigBackend } from "../../lib/caps";
import { adjustedPa, mod360 } from "../../lib/rotation";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";

export const DEFAULT_ROTATOR_CFG: RotatorConfig = {
  range_type: "full",
  range_start_deg: 0,
  tolerance_deg: 1,
};

// --- pure SVG geometry (exported for the assert-file tests) ---------------
export function polarXY(cx: number, cy: number, r: number, deg: number) {
  // 0° at 12 o'clock, clockwise (matches how a rotator angle reads on-sky)
  const rad = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

export function arcPath(cx: number, cy: number, r: number,
                        startDeg: number, sweepDeg: number): string {
  const s = polarXY(cx, cy, r, startDeg);
  const e = polarXY(cx, cy, r, startDeg + sweepDeg);
  const large = sweepDeg > 180 ? 1 : 0;
  return `M ${s.x.toFixed(2)} ${s.y.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${e.x.toFixed(2)} ${e.y.toFixed(2)}`;
}

export function allowedSweepDeg(rangeType: RotatorConfig["range_type"]): number {
  return rangeType === "half" ? 180 : rangeType === "quarter" ? 90 : 360;
}

const RANGE_OPTIONS = ["full", "half", "quarter"] as const;

export default function RotatorCard(): JSX.Element | null {
  const status = useStatus();
  const config = useConfig();
  const canConfig = useCanConfigBackend();
  const rot = status?.rotator;

  const seed: RotatorConfig = { ...DEFAULT_ROTATOR_CFG, ...(config?.rotator ?? {}) };
  const [draft, setDraft] = useState<RotatorConfig>(seed);
  const [angle, setAngle] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.range_type, seed.range_start_deg, seed.tolerance_deg]);

  if (!rot) return null;

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message
        : e instanceof Error ? e.message : "rotator action failed");
    } finally {
      setBusy(false);
    }
  };

  const persist = (patch: Partial<RotatorConfig>) =>
    run(async () => {
      const next = { ...draft, ...patch };
      setDraft(next);
      try {
        await setRotatorConfig(next);
        await useStore.getState().loadConfig();
      } catch (e) {
        setDraft(seed);
        throw e;
      }
    });

  const move = (deg: number) =>
    run(() => api.post("/api/rotator/move", { position_deg: mod360(deg) }));

  const parsedAngle = Number(angle);
  const angleOk = angle.trim() !== "" && Number.isFinite(parsedAngle);
  const hint = angleOk ? adjustedPa(parsedAngle, rot, draft) : null;

  // --- dial geometry ---
  const size = 120, cx = 60, cy = 60, R = 48;
  const sweep = allowedSweepDeg(draft.range_type);
  const mechStart = draft.range_start_deg;
  const cur = polarXY(cx, cy, R, rot.mech_deg);
  const startMark = polarXY(cx, cy, R, mechStart);

  return (
    <Panel
      title={`Rotator · ${rot.name}`}
      right={<InfoDot label="About the rotator"
        content="Angles are sky position angle (PA); the mechanical readout is the raw device angle. The shaded arc is the allowed range of motion — set its start by rotating to a cable-safe position and pressing 'Set to current position'. Moves are refused during exposures." />}
    >
      <div className="flex flex-wrap items-start gap-4">
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
             role="img" aria-label={`Rotator at ${rot.mech_deg.toFixed(1)} degrees mechanical`}
             className="shrink-0">
          <circle cx={cx} cy={cy} r={R} fill="none"
                  stroke="var(--line-bright)" strokeWidth={2} />
          {sweep < 360 && (
            <path d={arcPath(cx, cy, R, mechStart, sweep)} fill="none"
                  stroke="var(--accent)" strokeOpacity={0.55} strokeWidth={5} />
          )}
          {sweep === 360 && (
            <circle cx={cx} cy={cy} r={R} fill="none"
                    stroke="var(--accent)" strokeOpacity={0.35} strokeWidth={5} />
          )}
          {sweep < 360 && (
            <circle cx={startMark.x} cy={startMark.y} r={3.5}
                    fill="var(--warn)" />
          )}
          <circle cx={cur.x} cy={cur.y} r={4.5} fill="var(--accent)"
                  stroke="var(--bg)" strokeWidth={1.5}>
            {rot.moving && (
              <animate attributeName="opacity" values="1;0.3;1" dur="1s"
                       repeatCount="indefinite" />
            )}
          </circle>
          <text x={cx} y={cy - 4} textAnchor="middle"
                className="mono" fill="var(--text)" fontSize="13">
            {rot.sky_deg.toFixed(1)}°
          </text>
          <text x={cx} y={cy + 12} textAnchor="middle"
                fill="var(--text-dim)" fontSize="9">
            mech {rot.mech_deg.toFixed(1)}°{rot.synced ? "" : " · unsynced"}
          </text>
        </svg>

        <div className="flex flex-col gap-2.5 min-w-[240px] flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label w-20 shrink-0">Move to</span>
            <input className="field !py-1 w-20 mono" inputMode="decimal"
                   value={angle} placeholder="PA °"
                   onChange={(e) => setAngle(e.target.value)}
                   aria-label="Target position angle, degrees" />
            <button className="btn min-h-9" disabled={busy || !angleOk}
                    onClick={() => hint && void move(hint.target)}>Go</button>
            <button className="btn min-h-9" disabled={busy}
                    onClick={() => void move(rot.sky_deg - 1)}>−1°</button>
            <button className="btn min-h-9" disabled={busy}
                    onClick={() => void move(rot.sky_deg + 1)}>+1°</button>
            <button className="btn btn-danger min-h-9"
                    onClick={() => void run(() => api.post("/api/rotator/halt"))}>
              Halt
            </button>
          </div>
          {hint?.adjusted && (
            <p className="text-[11px] text-warn leading-snug">
              ⚠ PA {Math.round(parsedAngle)}° is outside the range of motion —
              it will image as {Math.round(hint.target)}°.
            </p>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <button className="btn btn-accent min-h-9" disabled={busy}
                    onClick={() => void run(() => api.post("/api/rotator/rotate-to-pa",
                      { target_pa_deg: angleOk ? mod360(parsedAngle) : rot.sky_deg }))}>
              Rotate to PA (plate solve)
            </button>
            {rot.can_reverse && (
              <label className="flex items-center gap-1.5 text-[11px] text-dim">
                <input type="checkbox" checked={rot.reverse} disabled={busy}
                       onChange={(e) => void run(() => api.post("/api/rotator/reverse",
                         { reverse: e.target.checked }))} />
                reverse
              </label>
            )}
          </div>

          <div className="border border-line bg-bg/60 px-3 py-2.5 flex flex-col gap-2">
            <span className="label">Range of motion</span>
            <div className="flex items-center gap-1.5" role="radiogroup"
                 aria-label="Mechanical range">
              {RANGE_OPTIONS.map((rt) => (
                <button key={rt}
                        className={`btn min-h-9 uppercase text-[10px] tracking-wider ${
                          draft.range_type === rt ? "btn-accent" : ""}`}
                        disabled={!canConfig || busy}
                        aria-pressed={draft.range_type === rt}
                        onClick={() => void persist({ range_type: rt })}>
                  {rt}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-dim w-20 shrink-0">Start</span>
              <input className="field !py-1 w-20 mono" inputMode="decimal"
                     value={String(draft.range_start_deg)}
                     disabled={!canConfig || busy || draft.range_type === "full"}
                     aria-label="Range start, mechanical degrees"
                     onChange={(e) => {
                       const v = Number(e.target.value);
                       if (Number.isFinite(v)) setDraft({ ...draft, range_start_deg: v });
                     }}
                     onBlur={() => void persist({ range_start_deg: mod360(draft.range_start_deg) })} />
              <button className="btn min-h-9"
                      disabled={!canConfig || busy || draft.range_type === "full"}
                      onClick={() => void persist({ range_start_deg: mod360(rot.mech_deg) })}>
                Set to current position
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-dim w-20 shrink-0">Tolerance</span>
              <input className="field !py-1 w-16 mono" inputMode="decimal"
                     value={String(draft.tolerance_deg)}
                     disabled={!canConfig || busy}
                     aria-label="Rotate tolerance, degrees"
                     onChange={(e) => {
                       const v = Number(e.target.value);
                       if (Number.isFinite(v)) setDraft({ ...draft, tolerance_deg: v });
                     }}
                     onBlur={() => void persist({ tolerance_deg: draft.tolerance_deg })} />
              <span className="text-[11px] text-faint">° (mod-180)</span>
            </div>
          </div>

          {!canConfig && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — changing the range of motion needs operator or admin access.
            </p>
          )}
          {err && (
            <p className="text-[11px] text-bad inline-flex items-center gap-1.5">
              <Icon name="alert" size={11} /> {err}
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}
```

(Adapt small kit realities on contact: if `useStatus` isn't exported from the store use `useStore((s) => s.status)`; if `Icon` names differ pick the closest existing glyph; keep the structure and copy as written.)

- [ ] **Step 2: Render it** — in `EquipmentView.tsx`, import `RotatorCard` and add `<RotatorCard />` on the line after `<TasksPanel drivers={drivers} busy={busy} />`. The component self-hides without a rotator.

- [ ] **Step 3: Build + assert-file for the pure geometry** — append to a new `ui/src/components/__tests__/rotatorCard.test.ts` (same harness as equipment.test.ts):

```ts
import { allowedSweepDeg, arcPath, polarXY } from "../equipment/RotatorCard";
// ... harness ...
test("polarXY puts 0° at 12 o'clock", () => {
  const p = polarXY(60, 60, 48, 0);
  eq(Math.round(p.x), 60);
  eq(Math.round(p.y), 12);
});
test("allowedSweepDeg maps range types", () => {
  eq(allowedSweepDeg("full"), 360);
  eq(allowedSweepDeg("half"), 180);
  eq(allowedSweepDeg("quarter"), 90);
});
test("arcPath large-arc flag flips past 180", () => {
  eq(arcPath(60, 60, 48, 0, 90).includes(" 0 0 1 "), true);
  eq(arcPath(60, 60, 48, 0, 270).includes(" 0 1 1 "), true);
});
```

Run (from `ui/`): `npx tsx src/components/__tests__/rotatorCard.test.ts && npm run build`
Expected: tests pass; build clean

- [ ] **Step 4: Commit**

```bash
git add ui/src/components/equipment/RotatorCard.tsx ui/src/components/__tests__/rotatorCard.test.ts ui/src/views/EquipmentView.tsx
git commit -m "feat(ui/rotator): RotatorCard — arc-dial ROM, manual moves, solve-rotate, set-start-to-current (spec §5.1)"
```

---

### Task 12: Reality-aware banners (Atlas + Sequence)

**Files:**
- Modify: `ui/src/views/AtlasView.tsx:580-587` (banner) — it already has `const status = useStatus()` at `:115`
- Modify: `ui/src/views/SequenceView.tsx:379-386` (PA chip) — add a status read
- Test: extend `ui/src/components/__tests__/rotatorCard.test.ts` only if logic is extracted; otherwise build check

**Interfaces:**
- Consumes: `status?.rotator` (Task 10 typing), `adjustedPa` + `useConfig` for the range warning.

- [ ] **Step 1: AtlasView** — replace the `:580-587` block with:

```tsx
              {/* rotation honesty note — reality-aware (CAA spec §5.3) */}
              {rotation_deg > 0.5 && (status?.rotator ? (
                <p className="text-[12px] text-dim leading-snug">
                  Camera will rotate to PA {Math.round(rotation_deg)}°
                  automatically on slew ({status.rotator.name}).
                  {(() => {
                    const cfg = { range_type: "full" as const, range_start_deg: 0,
                                  ...(config?.rotator ?? {}) };
                    const h = adjustedPa(rotation_deg, status.rotator, cfg);
                    return h.adjusted ? (
                      <span className="text-warn">
                        {" "}⚠ Outside the range of motion — it will image
                        as {Math.round(h.target)}°.
                      </span>
                    ) : null;
                  })()}
                </p>
              ) : (
                <p className="text-[12px] text-dim leading-snug">
                  Camera angle is manual — set your camera to PA{" "}
                  {Math.round(rotation_deg)}° before the run; there is no rotator
                  in the rig.
                </p>
              ))}
```

(`config` comes from `useConfig()` — add the hook to AtlasView if it doesn't already read config; import `adjustedPa` from `../lib/rotation`.)

Also: Atlas's "slew & center"/"Send to Plan" goto call (the `api.post("/api/mount/goto", …)` in AtlasView, if present — search the file) gains `rotation_deg: rotation_deg > 0.5 ? rotation_deg : undefined` in its body when a rotator is connected. If AtlasView has no direct goto call (only plan handoff), skip — the sequence path carries it.

- [ ] **Step 2: SequenceView** — add `const status = useStatus();` (import pattern per the file's existing store usage), then replace the chip `:379-386`:

```tsx
                  {t.rotation_deg != null && t.rotation_deg > 0.5 && (
                    <span
                      className="mono text-[10px] text-accent border border-line2 px-1.5 py-0.5"
                      title={status?.rotator
                        ? "The rotator will move to this position angle automatically at target start"
                        : "Set your camera to this position angle before the run (no rotator in rig)"}
                    >
                      PA {Math.round(t.rotation_deg)}°{status?.rotator ? " · auto" : ""}
                    </span>
                  )}
```

- [ ] **Step 3: Build + full UI test pass**

Run (from `ui/`): `npx tsx src/lib/__tests__/rotation.test.ts && npx tsx src/lib/__tests__/equipment.test.ts && npx tsx src/components/__tests__/rotatorCard.test.ts && npx tsx src/components/__tests__/healthStrip.test.ts && npm run build`
Expected: all pass; build clean

- [ ] **Step 4: Commit**

```bash
git add ui/src/views/AtlasView.tsx ui/src/views/SequenceView.tsx
git commit -m "feat(ui/rotator): Atlas/Sequence banners go reality-aware — auto-rotate vs manual, range warnings (spec §5.3)"
```

---

## After all tasks

1. Final whole-branch review (superpowers:requesting-code-review) on the most capable model; independently re-run the FULL server suite (`/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q`, ~8 min, expect 853 + new, 0 failures) and the UI build + all four assert files.
2. Live smoke on :8801 (restart the server: kill the old PID, `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m astrodeck --port 8801`): sim connect shows the Rotator card; ROM set-to-current persists; `POST /api/rotator/rotate-to-pa` converges (watch bus events); Atlas/Sequence banners flip with/without the rotator; `/api/config/rotator` 422s junk.
3. At-the-scope checklist for the real CAA (user-run): probe the obs Alpaca endpoint → rotator offer appears → assign + connect → manual move → set ROM to current → solve-rotate to a target PA. NINA-bridge path: connect the CAA in NINA, verify `/equipment/rotator/*` responds as coded in Task 4 (the one deliberately live-validated assumption).
4. Push + `gh run watch --exit-status` until CI is green; never claim green from local.
