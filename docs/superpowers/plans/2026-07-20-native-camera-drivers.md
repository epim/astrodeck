# Native Camera Drivers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A vendor-agnostic native camera framework — shared engine + narrow adapter waist + per-brand ctypes bindings — proven by ZWO ASI (guide) and Player One (imaging, LRN/HCG) adapters, so adding a new brand costs one isolated module.

**Architecture:** A `NativeCamera(Camera)` engine *composes* a `CameraAdapter` (a plain, sync, ~10-hook object) over per-brand ctypes SDK wrappers. An additive `CameraCapabilities` descriptor keeps brand features from being flattened. Per-vendor backends (`zwo-asi`, `player-one`) register via entry points; a backward-compatible orchestrator change lets the `guide_camera` role resolve from its own endpoint.

**Tech Stack:** Python 3.11+, asyncio, numpy, ctypes (CDLL), pytest (+ xdist), FastAPI (backend registry). Vendored SDKs: ZWO ASICamera2 (MIT), Player One (license-gated).

## Global Constraints

- **Cross-platform clean degradation** — native camera backends are win-x64-only for now; absent DLLs or non-Windows platforms must leave discovery, `/api/backends`, and the drivers surface working, never raising (guard SDK loads → `[]`/absent).
- **`ctypes.CDLL`, not `WinDLL`** — Linux-safe loads; declare `argtypes`/`restype` for every called export; export-verification gates DLL selection (mirror `zwo_sdk.py`).
- **No observatory site coordinates** in code/tests/docs — fictional values only; real values read at runtime from config.
- **No new runtime deps** beyond the vendored DLLs (`numpy` already a dep).
- **Framework fidelity** — every backend sets ALL manifest fields explicitly (`name`, `label`, `roles`, `version`, `author`, `min_app_version`, `transport`, `hardware`, `driver_type`, `discoverable`, `hostless`); duck-typed Protocol defaults do NOT apply. Register via `register_all()` entry points. Backend `name` is stable identity — never changes.
- **Adapters are synchronous**; the engine wraps every hook in `asyncio.to_thread` (SDKs are not documented thread-safe — one lock per device).
- **The exact Player One sampling-mode SDK symbol is pinned against the real `PlayerOneCamera.h` at build time** (Task 9); never assert a guessed symbol as fact.
- **TDD, DRY, YAGNI, frequent commits.** Run the full server suite (`cd server && python -m pytest -q`) green before each wave's final commit.

## File Structure

New package `server/astrodeck/devices/cameras/` (the reusable core):
- `cameras/__init__.py` — package marker + public exports.
- `cameras/adapter.py` — `ROI`, `CameraCapabilities`, `CameraAdapter` ABC. **One responsibility:** the waist contract + descriptor. No SDK, no asyncio.
- `cameras/engine.py` — `NativeCamera(Camera)`. **One responsibility:** vendor-blind exposure/cooling/ROI/buffer logic composing an adapter.
- `cameras/registry.py` — in-tree adapter registry (`register_adapter`/`iter_adapters`) for the parametrized contract suite + optional in-tree discovery.
- `cameras/zwo_asi_sdk.py` — ctypes bindings for ASICamera2 (mirrors `zwo_sdk.py`).
- `cameras/zwo_asi.py` — `AsiCameraAdapter`.
- `cameras/player_one_sdk.py` — ctypes bindings for PlayerOneCamera.
- `cameras/player_one.py` — `PlayerOneAdapter`.

New backends (thin, in existing `backends/` dir):
- `backends/zwo_asi.py` — `ZwoAsiBackend` + `ZwoAsiSession` + `register_all()`.
- `backends/player_one.py` — `PlayerOneBackend` + `PlayerOneSession` + `register_all()`.

Modified:
- `devices/orchestrator.py:287` — generalize `_pick_guide_camera`; fix guide-camera hardware stamping.
- `pyproject.toml` — two new `astrodeck.backends` entry points; version → 0.2.7.
- `astrodeck/__init__.py` — `__version__ = "0.2.7"`.

Vendored binaries:
- `vendor/zwo/` — add `ASICamera2.dll` (+ update README); LICENSE already present (MIT).
- `vendor/playerone/` — `PlayerOneCamera.dll` + `LICENSE` + `README` (Task 8-gated).

Tests (in `server/tests/`):
- `test_camera_adapter.py`, `test_camera_engine.py`, `test_camera_registry.py`,
  `test_camera_contract.py` (parametrized keystone), `test_zwo_asi_adapter.py`,
  `test_zwo_asi_backend.py`, `test_player_one_adapter.py`, `test_player_one_backend.py`,
  `test_camera_readnoise.py`, and additions to `test_orchestrator*.py`.

---

## WAVE 1 — Vendor-blind foundation (engine / adapter / registry / contract suite)

### Task 1: Adapter contract + capability descriptor

**Files:**
- Create: `server/astrodeck/devices/cameras/__init__.py`
- Create: `server/astrodeck/devices/cameras/adapter.py`
- Test: `server/tests/test_camera_adapter.py`

**Interfaces:**
- Consumes: `astrodeck.devices.base.DeviceError`.
- Produces: `ROI(x:int, y:int, w:int, h:int, bin:int)`; `CameraCapabilities` (frozen dataclass, fields per spec §4); `CameraAdapter` (ABC with abstract hooks `capabilities`, `open`, `close`, `start_exposure`, `image_ready`, `read_frame`, `abort`, and optional `set_read_mode`, `set_target_temp`, `set_cooler`, `get_temperature`, `get_cooler_power`, `set_dew_heater`).

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_camera_adapter.py
import numpy as np
import pytest
from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import ROI, CameraCapabilities, CameraAdapter


def _caps(**kw):
    base = dict(sensor_width=100, sensor_height=80, pixel_size_um=3.76,
                bit_depth=16, bayer_pattern=None, gain_range=(0, 600),
                offset_range=(0, 255), bin_modes=(1, 2), roi_supported=True,
                has_cooler=False, has_dew_heater=False, max_adu=65535)
    base.update(kw)
    return CameraCapabilities(**base)


class _Min(CameraAdapter):
    """Implements ONLY the required hooks; optional ones inherit defaults."""
    def capabilities(self): return _caps()
    def open(self, index): pass
    def close(self): pass
    def start_exposure(self, *, seconds, gain, offset, roi, light): pass
    def image_ready(self): return True
    def read_frame(self): return b"\x00" * (100 * 80 * 2)
    def abort(self): pass


def test_capabilities_defaults():
    c = _caps()
    assert c.read_modes == ()
    assert c.hcg_threshold_gain is None
    assert dict(c.extra) == {}


def test_capabilities_frozen():
    c = _caps()
    with pytest.raises(Exception):
        c.sensor_width = 999  # frozen


def test_roi_fields():
    r = ROI(x=1, y=2, w=3, h=4, bin=2)
    assert (r.x, r.y, r.w, r.h, r.bin) == (1, 2, 3, 4, 2)


def test_optional_hooks_default_behavior():
    a = _Min()
    assert a.get_temperature() is None
    assert a.get_cooler_power() is None
    for call in (lambda: a.set_read_mode("x"),
                 lambda: a.set_target_temp(-10.0),
                 lambda: a.set_cooler(True),
                 lambda: a.set_dew_heater(50)):
        with pytest.raises(DeviceError):
            call()


def test_read_modes_declared_survives():
    c = _caps(read_modes=("Normal", "LowNoise"), hcg_threshold_gain=125)
    assert c.read_modes == ("Normal", "LowNoise")
    assert c.hcg_threshold_gain == 125
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_camera_adapter.py -q`
Expected: FAIL with `ModuleNotFoundError: astrodeck.devices.cameras.adapter`

- [ ] **Step 3: Write the implementation**

```python
# server/astrodeck/devices/cameras/__init__.py
"""Vendor-agnostic native camera framework (engine + adapter waist + bindings)."""
```

```python
# server/astrodeck/devices/cameras/adapter.py
"""The camera adapter waist: the narrow, stable interface a brand implements,
plus the additive capability descriptor that keeps brand features from being
flattened to a lowest-common-denominator. No SDK, no asyncio, no CameraFrame —
the engine (engine.py) owns all of that and merely drives these hooks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping

from ..base import DeviceError


@dataclass(frozen=True)
class ROI:
    """A subframe + binning request in unbinned sensor pixels."""
    x: int
    y: int
    w: int
    h: int
    bin: int


@dataclass(frozen=True)
class CameraCapabilities:
    """What a camera can do, DECLARED (never assumed). Consumers render what is
    present; unsupported features are simply absent, not clamped away.

    ``read_modes`` generalizes discrete sampling modes (Player One LRN/Normal,
    QHY ReadMode, ...); () means none. ``hcg_threshold_gain`` is informational
    (the gain at/above which conversion gain lowers read noise); None when N/A.
    ``extra`` carries typed brand-unique data without polluting the core."""
    sensor_width: int
    sensor_height: int
    pixel_size_um: float
    bit_depth: int
    bayer_pattern: str | None
    gain_range: tuple[int, int]
    offset_range: tuple[int, int]
    bin_modes: tuple[int, ...]
    roi_supported: bool
    has_cooler: bool
    has_dew_heater: bool
    max_adu: int
    read_modes: tuple[str, ...] = ()
    hcg_threshold_gain: int | None = None
    extra: Mapping[str, object] = field(default_factory=dict)


class CameraAdapter(ABC):
    """One brand's camera, reduced to primitive synchronous hooks. Required
    hooks are the irreducible minimum; optional hooks default to raising
    DeviceError (or returning None) so a brand implements ONLY what it has —
    exactly as Camera.set_cooler raises today."""

    # --- required ---------------------------------------------------------
    @abstractmethod
    def capabilities(self) -> CameraCapabilities: ...
    @abstractmethod
    def open(self, index: int) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def start_exposure(self, *, seconds: float, gain: int, offset: int,
                       roi: ROI, light: bool) -> None: ...
    @abstractmethod
    def image_ready(self) -> bool: ...
    @abstractmethod
    def read_frame(self) -> bytes: ...
    @abstractmethod
    def abort(self) -> None: ...

    # --- optional (implement only if the hardware has it) -----------------
    def set_read_mode(self, mode: str) -> None:
        raise DeviceError("camera has no selectable read modes")

    def set_target_temp(self, celsius: float) -> None:
        raise DeviceError("camera has no cooler")

    def set_cooler(self, on: bool) -> None:
        raise DeviceError("camera has no cooler")

    def get_temperature(self) -> float | None:
        return None

    def get_cooler_power(self) -> int | None:
        return None

    def set_dew_heater(self, power: int) -> None:
        raise DeviceError("camera has no dew heater")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_camera_adapter.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/cameras/__init__.py server/astrodeck/devices/cameras/adapter.py server/tests/test_camera_adapter.py
git commit -m "feat(cameras): adapter waist + additive capability descriptor"
```

---

### Task 2: `NativeCamera` engine — exposure lifecycle + buffer assembly

**Files:**
- Create: `server/astrodeck/devices/cameras/engine.py`
- Test: `server/tests/test_camera_engine.py`

**Interfaces:**
- Consumes: `CameraAdapter`, `CameraCapabilities`, `ROI` (Task 1); `astrodeck.devices.base.Camera`, `CameraFrame`, `DeviceError`.
- Produces: `NativeCamera(Camera)` with `__init__(self, adapter: CameraAdapter, index: int = 0, name: str = "")`; `async connect()`; `async disconnect()`; `async expose(seconds, gain, offset, binning=1, light=True, save=False, target="") -> CameraFrame`; `async abort_exposure()`. Class attr `EXPOSURE_POLL_MARGIN_S = 15.0`. Reads capabilities into the `Camera` fields `sensor_width/sensor_height/pixel_size_um/max_gain/can_cool/has_dew_heater/bayer_pattern` on connect.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_camera_engine.py
import asyncio
import numpy as np
import pytest
from astrodeck.devices.base import CameraFrame
from astrodeck.devices.cameras.adapter import ROI, CameraCapabilities, CameraAdapter
from astrodeck.devices.cameras.engine import NativeCamera


class FakeAdapter(CameraAdapter):
    def __init__(self, w=6, h=4, ready_after=1):
        self.calls: list[str] = []
        self._w, self._h = w, h
        self._ready_after = ready_after
        self._polls = 0
        self.opened = False
        self.last_start = None

    def capabilities(self):
        return CameraCapabilities(
            sensor_width=self._w, sensor_height=self._h, pixel_size_um=3.76,
            bit_depth=16, bayer_pattern=None, gain_range=(0, 600),
            offset_range=(0, 255), bin_modes=(1, 2), roi_supported=True,
            has_cooler=False, has_dew_heater=False, max_adu=65535)

    def open(self, index): self.calls.append(f"open{index}"); self.opened = True
    def close(self): self.calls.append("close"); self.opened = False

    def start_exposure(self, *, seconds, gain, offset, roi, light):
        self.calls.append("start")
        self.last_start = dict(seconds=seconds, gain=gain, offset=offset,
                               roi=roi, light=light)
        self._polls = 0

    def image_ready(self):
        self._polls += 1
        return self._polls >= self._ready_after

    def read_frame(self):
        self.calls.append("read")
        # little-endian uint16 ramp, row-major h*w
        arr = np.arange(self._w * self._h, dtype="<u2")
        return arr.tobytes()

    def abort(self): self.calls.append("abort")


@pytest.mark.asyncio
async def test_connect_maps_capabilities_to_camera_fields():
    cam = NativeCamera(FakeAdapter(w=6, h=4))
    await cam.connect()
    assert cam.sensor_width == 6 and cam.sensor_height == 4
    assert cam.pixel_size_um == 3.76
    assert cam.can_cool is False
    assert cam.max_gain == 600


@pytest.mark.asyncio
async def test_expose_returns_shaped_linear_frame():
    fa = FakeAdapter(w=6, h=4)
    cam = NativeCamera(fa)
    await cam.connect()
    frame = await cam.expose(0.01, gain=100, offset=10, binning=1)
    assert isinstance(frame, CameraFrame)
    assert frame.data.dtype == np.uint16
    assert frame.data.shape == (4, 6)          # (height, width) row-major
    assert frame.data_is_linear is True
    assert frame.full_well == 65535
    assert frame.gain == 100 and frame.offset == 10
    assert fa.calls == ["open0", "start", "read"]
    assert fa.last_start["light"] is True


@pytest.mark.asyncio
async def test_expose_cancel_aborts_in_camera():
    fa = FakeAdapter(ready_after=10_000)     # never ready
    cam = NativeCamera(fa)
    await cam.connect()
    task = asyncio.create_task(cam.expose(5.0, gain=0, offset=0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "abort" in fa.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_camera_engine.py -q`
Expected: FAIL with `ModuleNotFoundError: ...cameras.engine`

- [ ] **Step 3: Write the implementation**

```python
# server/astrodeck/devices/cameras/engine.py
"""NativeCamera: the vendor-blind engine that turns a CameraAdapter into a
Camera. Owns the exposure lifecycle (generalized from alpaca.py:expose), buffer
assembly, cooling, ROI/binning, and cancellation — written once for every
brand. Every adapter hook runs under asyncio.to_thread + a per-device lock."""
from __future__ import annotations

import asyncio
import time

import numpy as np

from ..base import Camera, CameraFrame, DeviceError
from .adapter import ROI, CameraAdapter


class NativeCamera(Camera):
    #: extra wall-clock beyond the requested exposure before imageready is a
    #: timeout (download + USB latency). Matches the Alpaca margin family.
    EXPOSURE_POLL_MARGIN_S = 15.0

    def __init__(self, adapter: CameraAdapter, index: int = 0, name: str = ""):
        super().__init__(name or "Native Camera")
        self._a = adapter
        self._index = index
        self._lock = asyncio.Lock()
        self._caps = None
        self._exposing = False

    async def _run(self, fn, *a):
        """Run a sync adapter hook off the event loop, serialized per device."""
        async with self._lock:
            return await asyncio.to_thread(fn, *a)

    async def connect(self) -> None:
        await asyncio.to_thread(self._a.open, self._index)
        caps = self._a.capabilities()
        self._caps = caps
        self.sensor_width = caps.sensor_width
        self.sensor_height = caps.sensor_height
        self.pixel_size_um = caps.pixel_size_um
        self.max_gain = caps.gain_range[1]
        self.can_cool = caps.has_cooler
        self.has_dew_heater = caps.has_dew_heater
        self.bayer_pattern = caps.bayer_pattern

    async def disconnect(self) -> None:
        try:
            await asyncio.to_thread(self._a.close)
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        caps = self._caps
        if caps is None:
            raise DeviceError("camera not connected")
        roi = ROI(x=0, y=0, w=caps.sensor_width, h=caps.sensor_height, bin=binning)
        await self._run(lambda: self._a.start_exposure(
            seconds=seconds, gain=gain, offset=offset, roi=roi, light=light))
        self._exposing = True
        deadline = time.monotonic() + seconds + self.EXPOSURE_POLL_MARGIN_S
        try:
            while not await asyncio.to_thread(self._a.image_ready):
                if time.monotonic() > deadline:
                    raise DeviceError("exposure imageready timeout")
                await asyncio.sleep(min(0.5, max(0.05, seconds / 20)))
        except asyncio.CancelledError:
            await asyncio.to_thread(self._a.abort)
            raise
        finally:
            self._exposing = False
        raw = await asyncio.to_thread(self._a.read_frame)
        data = self._shape(raw, roi, caps)
        temp = await self.get_temperature()
        return CameraFrame(
            data=data, exposure_s=seconds, gain=gain, offset=offset,
            binning=binning, bayer_pattern=caps.bayer_pattern,
            temperature_c=temp, timestamp=time.time(),
            full_well=caps.max_adu, data_is_linear=True)

    @staticmethod
    def _shape(raw: bytes, roi: ROI, caps) -> np.ndarray:
        """Raw little-endian sensor bytes -> 2-D uint16 [height, width]. 8-bit
        readout is promoted to uint16 so the frame dtype is uniform."""
        w, h = roi.w // roi.bin, roi.h // roi.bin
        dt = np.uint8 if caps.bit_depth <= 8 else "<u2"
        arr = np.frombuffer(raw, dtype=dt, count=w * h)
        return arr.reshape((h, w)).astype(np.uint16)

    async def abort_exposure(self) -> None:
        await asyncio.to_thread(self._a.abort)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_camera_engine.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/cameras/engine.py server/tests/test_camera_engine.py
git commit -m "feat(cameras): NativeCamera engine — expose lifecycle + buffer assembly"
```

---

### Task 3: Engine — cooling, dew heater, read-mode, ROI subframe

**Files:**
- Modify: `server/astrodeck/devices/cameras/engine.py`
- Test: `server/tests/test_camera_engine.py` (extend)

**Interfaces:**
- Consumes: Task 2 `NativeCamera`; adapter optional hooks.
- Produces: on `NativeCamera` — `async set_cooler(on, target_c=None)`, `async get_temperature()`, `async set_dew_heater(power)`, `async cooler_power()`; `expose(...)` now honors a `read_mode: str | None = None` kwarg and a `roi: ROI | None = None` kwarg (default full-frame). `expose` applies `set_read_mode` before `start_exposure` when `read_mode` is given.

- [ ] **Step 1: Write the failing test**

```python
# append to server/tests/test_camera_engine.py
class CoolAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self._cooler = False
        self._target = None
        self._temp = 20.0
        self._read_mode = None

    def capabilities(self):
        c = super().capabilities()
        return CameraCapabilities(**{**c.__dict__,
                                     "has_cooler": True, "has_dew_heater": True,
                                     "read_modes": ("Normal", "LowNoise")})

    def set_read_mode(self, mode): self.calls.append(f"mode:{mode}"); self._read_mode = mode
    def set_target_temp(self, c): self._target = c
    def set_cooler(self, on): self._cooler = on
    def get_temperature(self): return -10.0 if self._cooler else self._temp
    def get_cooler_power(self): return 42 if self._cooler else 0
    def set_dew_heater(self, power): self.calls.append(f"dew:{power}")


@pytest.mark.asyncio
async def test_cooling_delegates_to_adapter():
    ca = CoolAdapter()
    cam = NativeCamera(ca)
    await cam.connect()
    assert cam.can_cool is True
    await cam.set_cooler(True, target_c=-10.0)
    assert ca._cooler is True and ca._target == -10.0
    assert await cam.get_temperature() == -10.0
    assert await cam.cooler_power() == 42


@pytest.mark.asyncio
async def test_expose_applies_read_mode_before_start():
    ca = CoolAdapter()
    cam = NativeCamera(ca)
    await cam.connect()
    await cam.expose(0.01, gain=0, offset=0, read_mode="LowNoise")
    assert ca.calls.index("mode:LowNoise") < ca.calls.index("start")
    assert ca._read_mode == "LowNoise"


@pytest.mark.asyncio
async def test_dew_heater_delegates():
    ca = CoolAdapter()
    cam = NativeCamera(ca)
    await cam.connect()
    await cam.set_dew_heater(75)
    assert "dew:75" in ca.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_camera_engine.py -k "cooling or read_mode or dew" -q`
Expected: FAIL (`set_cooler`/`cooler_power`/`read_mode` kwarg not present)

- [ ] **Step 3: Write the implementation**

Modify `expose` signature to `async def expose(self, seconds, gain, offset, binning=1, light=True, save=False, target="", *, read_mode=None, roi=None)`. Immediately after computing/adopting `roi` (use the passed `roi` when given, else full-frame) and BEFORE `start_exposure`, insert:

```python
        if read_mode is not None:
            await self._run(lambda: self._a.set_read_mode(read_mode))
```

Add these methods to `NativeCamera`:

```python
    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        if not self._caps or not self._caps.has_cooler:
            raise DeviceError(f"{self.name} has no cooler")
        if target_c is not None:
            await self._run(lambda: self._a.set_target_temp(target_c))
        await self._run(lambda: self._a.set_cooler(on))

    async def get_temperature(self) -> float | None:
        return await asyncio.to_thread(self._a.get_temperature)

    async def cooler_power(self) -> int | None:
        return await asyncio.to_thread(self._a.get_cooler_power)

    async def set_dew_heater(self, power: int) -> None:
        if not self._caps or not self._caps.has_dew_heater:
            raise DeviceError(f"{self.name} has no dew heater")
        await self._run(lambda: self._a.set_dew_heater(int(power)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_camera_engine.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/cameras/engine.py server/tests/test_camera_engine.py
git commit -m "feat(cameras): engine cooling/dew/read-mode delegation"
```

---

### Task 4: Adapter registry + parametrized camera-contract suite (keystone)

**Files:**
- Create: `server/astrodeck/devices/cameras/registry.py`
- Test: `server/tests/test_camera_registry.py`
- Test: `server/tests/test_camera_contract.py`

**Interfaces:**
- Consumes: `CameraAdapter` (Task 1), `NativeCamera` (Task 2/3).
- Produces: `register_adapter(vendor: str, factory: Callable[[], CameraAdapter])`; `iter_adapters() -> list[tuple[str, Callable[[], CameraAdapter]]]`; `clear_registry()` (test hygiene). A module-level `_CONTRACT_ADAPTERS` fixture list the contract suite parametrizes over — every adapter (real + a canonical fake) must pass.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_camera_registry.py
import pytest
from astrodeck.devices.cameras.adapter import CameraAdapter, CameraCapabilities
from astrodeck.devices.cameras import registry


class _A(CameraAdapter):
    def capabilities(self): raise NotImplementedError
    def open(self, i): pass
    def close(self): pass
    def start_exposure(self, **k): pass
    def image_ready(self): return True
    def read_frame(self): return b""
    def abort(self): pass


def test_register_and_iter():
    registry.clear_registry()
    registry.register_adapter("brandx", _A)
    names = [n for n, _ in registry.iter_adapters()]
    assert "brandx" in names


def test_duplicate_vendor_rejected():
    registry.clear_registry()
    registry.register_adapter("brandx", _A)
    with pytest.raises(ValueError):
        registry.register_adapter("brandx", _A)
```

```python
# server/tests/test_camera_contract.py
"""The keystone: every camera adapter is subjected to the SAME behavioral
contract, driven by a fake SDK. A new brand can't silently violate it."""
import asyncio
import numpy as np
import pytest
from astrodeck.devices.base import CameraFrame, DeviceError
from astrodeck.devices.cameras.engine import NativeCamera

# Adapters register their FAKE-SDK-backed factory here (real adapters add theirs
# in their own test modules via contract_adapters()). Start with the reference
# fake so the suite is never empty.
from tests.test_camera_engine import FakeAdapter

CONTRACT_ADAPTERS = [("reference-fake", lambda: FakeAdapter(w=8, h=6))]


@pytest.fixture(params=CONTRACT_ADAPTERS, ids=lambda p: p[0])
def cam(request):
    _name, factory = request.param
    return NativeCamera(factory())


@pytest.mark.asyncio
async def test_contract_expose_wellformed(cam):
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0)
    assert isinstance(f, CameraFrame)
    assert f.data.dtype == np.uint16 and f.data.ndim == 2
    assert f.data.shape == (cam.sensor_height, cam.sensor_width)
    assert f.data_is_linear is True


@pytest.mark.asyncio
async def test_contract_capabilities_self_consistent(cam):
    await cam.connect()
    caps = cam._caps
    assert caps.gain_range[0] <= caps.gain_range[1]
    assert 1 in caps.bin_modes
    assert caps.max_adu > 0
    assert caps.bit_depth in (8, 12, 14, 16)
    # additive: unsupported optional features must be internally consistent
    if not caps.has_cooler:
        with pytest.raises(DeviceError):
            await cam.set_cooler(True)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python -m pytest tests/test_camera_registry.py tests/test_camera_contract.py -q`
Expected: FAIL (`registry` missing; contract import ok once registry exists)

- [ ] **Step 3: Write the implementation**

```python
# server/astrodeck/devices/cameras/registry.py
"""In-tree registry of camera adapters. Real backends register their FACTORY so
the parametrized contract suite (test_camera_contract.py) and optional in-tree
discovery can enumerate every brand. Out-of-tree brands use the framework's
entry-point discovery instead — this registry is for the bundled ones."""
from __future__ import annotations

from typing import Callable

from .adapter import CameraAdapter

_REGISTRY: dict[str, Callable[[], CameraAdapter]] = {}


def register_adapter(vendor: str, factory: Callable[[], CameraAdapter]) -> None:
    if vendor in _REGISTRY:
        raise ValueError(f"camera adapter {vendor!r} already registered")
    _REGISTRY[vendor] = factory


def iter_adapters() -> list[tuple[str, Callable[[], CameraAdapter]]]:
    return sorted(_REGISTRY.items())


def clear_registry() -> None:
    _REGISTRY.clear()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server && python -m pytest tests/test_camera_registry.py tests/test_camera_contract.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `cd server && python -m pytest -q` (expect green; Wave 1 is vendor-blind).

```bash
git add server/astrodeck/devices/cameras/registry.py server/tests/test_camera_registry.py server/tests/test_camera_contract.py
git commit -m "feat(cameras): adapter registry + parametrized contract suite"
```

---

## WAVE 2 — ZWO ASI adapter (guide camera)

### Task 5: ASICamera2 ctypes bindings + vendored DLL

**Files:**
- Create: `server/astrodeck/devices/cameras/zwo_asi_sdk.py`
- Add binary: `server/astrodeck/vendor/zwo/ASICamera2.dll` (win-x64, from the MIT ASICamera2 SDK — indi-3rdparty libasi); update `server/astrodeck/vendor/zwo/README.md` with the added binary + SDK version.
- Test: `server/tests/test_zwo_asi_sdk.py`

**Interfaces:**
- Consumes: nothing (mirrors `zwo_sdk.py` structure exactly — reuse `_loads_with_exports`/`_find_dll` idioms; do NOT import from `zwo_sdk.py`, copy the small loader so cameras and accessories stay decoupled, or refactor the loader into a shared `zwo_loader.py` if the reviewer prefers — default: copy).
- Produces: `AsiSdk` wrapper class exposing (all synchronous): `count()->int`, `open(cam_id)`, `close(cam_id)`, `get_property(cam_id)->AsiProperty` (sensor w/h, pixel size, is_color, bayer, bit_depth, name, supported bins, max gain/offset), `set_control(cam_id, ctrl, value, auto=False)`, `get_control(cam_id, ctrl)->int`, `set_roi(cam_id, w, h, bin, img_type)`, `start_exposure(cam_id, dark)`, `exp_status(cam_id)->int` (0=idle,1=working,2=success,3=failed), `get_data(cam_id, nbytes)->bytes`, `stop_exposure(cam_id)`, `get_control_caps(cam_id)->list` (for gain/offset ranges + whether HighSpeed etc.). `make_asi = AsiSdk` factory seam.

**ASICamera2 API surface (from the public headers — verify each export against `ASICamera2.h` in the SDK you vendor; these are the canonical names):**
`ASIGetNumOfConnectedCameras`, `ASIGetCameraProperty` (→ `ASI_CAMERA_INFO`), `ASIOpenCamera`, `ASIInitCamera`, `ASICloseCamera`, `ASIGetNumOfControls`, `ASIGetControlCaps` (→ `ASI_CONTROL_CAPS`), `ASISetControlValue`, `ASIGetControlValue`, `ASISetROIFormat`, `ASIGetROIFormat`, `ASIStartExposure`, `ASIStopExposure`, `ASIGetExpStatus`, `ASIGetDataAfterExp`. Control ids used: `ASI_GAIN=0`, `ASI_EXPOSURE=1` (µs), `ASI_OFFSET=5` (a.k.a. brightness), `ASI_TEMPERATURE=8` (0.1°C units). Image type `ASI_IMG_RAW16=2`.

- [ ] **Step 1: Write the failing test** (structure + a guarded real-load probe)

```python
# server/tests/test_zwo_asi_sdk.py
import sys
from pathlib import Path
import pytest
from astrodeck.devices.cameras import zwo_asi_sdk as z


def test_signatures_declared():
    # every bound export appears in the signature table with an argtype list
    assert "ASIGetCameraProperty" in z._SIGNATURES
    assert "ASIStartExposure" in z._SIGNATURES


def test_make_asi_seam_exists():
    assert z.make_asi is z.AsiSdk


@pytest.mark.skipif(
    sys.platform != "win32"
    or not (Path(z.__file__).resolve().parent.parent / "vendor" / "zwo"
            / "ASICamera2.dll").is_file(),
    reason="requires win32 + vendored ASICamera2.dll")
def test_real_dll_loads_and_exports():
    sdk = z.AsiSdk()          # raises if DLL absent/incomplete
    assert sdk.count() >= 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python -m pytest tests/test_zwo_asi_sdk.py -q`
Expected: FAIL (`ModuleNotFoundError: ...cameras.zwo_asi_sdk`)

- [ ] **Step 3: Write the implementation**

Mirror `zwo_sdk.py` exactly: `_VENDOR_DIR = .../vendor/zwo`; `_DLL_SPECS = {"ASICamera2.dll": ([known ASIStudio/indi install paths], [the export names above])}`; copy `_loads_with_exports` (CDLL) + `_find_dll`; define `ASI_CAMERA_INFO`, `ASI_CONTROL_CAPS` ctypes Structures per the header; `_SIGNATURES` with `argtypes` for each export; `_declare`; `AsiSdk` wrapper with the methods in the Interfaces block, each `_check`-ing the returned `ASI_ERROR_CODE`. Add an `ERROR_NAMES` map from `ASI_ERROR_CODE`. Keep it logic-free (no device semantics). **Verify every export name + struct layout against the `ASICamera2.h` you vendor before finalizing** — the names above are canonical but confirm against the shipped header.

Because the DLL is not present in CI, the module must import and declare signatures WITHOUT loading a DLL (loading happens in `AsiSdk.__init__`), so the two non-skipped tests pass on Linux.

- [ ] **Step 4: Run to verify it passes**

Run: `cd server && python -m pytest tests/test_zwo_asi_sdk.py -q`
Expected: PASS (2 passed, 1 skipped off-Windows / no DLL)

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/cameras/zwo_asi_sdk.py server/tests/test_zwo_asi_sdk.py server/astrodeck/vendor/zwo/README.md
# add ASICamera2.dll on the box where it is vendored:
# git add server/astrodeck/vendor/zwo/ASICamera2.dll
git commit -m "feat(cameras): ASICamera2 ctypes bindings + vendored DLL"
```

---

### Task 6: `AsiCameraAdapter`

**Files:**
- Create: `server/astrodeck/devices/cameras/zwo_asi.py`
- Test: `server/tests/test_zwo_asi_adapter.py`

**Interfaces:**
- Consumes: `CameraAdapter`, `CameraCapabilities`, `ROI` (Task 1); `AsiSdk` + `make_asi` (Task 5).
- Produces: `AsiCameraAdapter(CameraAdapter)` with `__init__(self, sdk=None, index=0)` (sdk defaults to `make_asi()`; injectable for tests); implements all required hooks; `capabilities()` derives from `sdk.get_property` + `get_control_caps` (mono ASI220MM → `bayer_pattern=None`, `has_cooler=False`, `read_modes=()`). `start_exposure` maps seconds→µs via `set_control(ASI_EXPOSURE)`, sets gain/offset, `set_roi`, then `start_exposure(dark=not light)`. `image_ready` reads `exp_status`==2 (raises `DeviceError` on ==3 failed). `read_frame` calls `get_data(w*h*2)`. `abort` → `stop_exposure`. Registers itself: `registry.register_adapter("zwo-asi", AsiCameraAdapter)` at import.

- [ ] **Step 1: Write the failing test** (fake SDK, per FakeCaaSdk pattern)

```python
# server/tests/test_zwo_asi_adapter.py
import asyncio
import numpy as np
import pytest
from astrodeck.devices.base import CameraFrame
from astrodeck.devices.cameras.engine import NativeCamera


class FakeAsiSdk:
    def __init__(self, w=8, h=6):
        self.calls = []
        self._w, self._h = w, h
        self._status = 0

    def count(self): return 1

    def get_property(self, cam_id):
        from astrodeck.devices.cameras.zwo_asi import AsiProperty
        return AsiProperty(name="ZWO ASI220MM", width=self._w, height=self._h,
                           pixel_size_um=4.0, is_color=False, bayer=None,
                           bit_depth=16, bin_modes=(1, 2), max_gain=300,
                           max_offset=255, has_cooler=False)

    def open(self, cam_id): self.calls.append("open")
    def close(self, cam_id): self.calls.append("close")
    def set_control(self, cam_id, ctrl, value, auto=False):
        self.calls.append(f"ctrl:{ctrl}={value}")
    def get_control(self, cam_id, ctrl): return 200  # temp 0.1C etc.
    def set_roi(self, cam_id, w, h, bin, img_type):
        self.calls.append(f"roi:{w}x{h}/{bin}")
    def start_exposure(self, cam_id, dark):
        self.calls.append(f"start:dark={dark}"); self._status = 2
    def exp_status(self, cam_id): return self._status
    def get_data(self, cam_id, nbytes):
        return np.arange(self._w * self._h, dtype="<u2").tobytes()
    def stop_exposure(self, cam_id): self.calls.append("stop")


@pytest.mark.asyncio
async def test_asi_adapter_exposes_via_engine():
    from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter
    a = AsiCameraAdapter(sdk=FakeAsiSdk(8, 6), index=0)
    cam = NativeCamera(a)
    await cam.connect()
    assert cam.sensor_width == 8 and cam.bayer_pattern is None
    assert cam.can_cool is False
    f = await cam.expose(0.5, gain=120, offset=10)
    assert isinstance(f, CameraFrame) and f.data.shape == (6, 8)
    # exposure seconds -> microseconds control
    assert any("ctrl:1=500000" in c for c in a._sdk.calls)


@pytest.mark.asyncio
async def test_asi_registered_in_registry():
    import astrodeck.devices.cameras.zwo_asi  # noqa: F401 (import registers)
    from astrodeck.devices.cameras import registry
    assert "zwo-asi" in [n for n, _ in registry.iter_adapters()]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python -m pytest tests/test_zwo_asi_adapter.py -q`
Expected: FAIL (`...cameras.zwo_asi` missing)

- [ ] **Step 3: Write the implementation**

Create `zwo_asi.py`: an `AsiProperty` dataclass (fields per the fake); `AsiCameraAdapter(CameraAdapter)` per the Interfaces block. `capabilities()` builds `CameraCapabilities(read_modes=(), hcg_threshold_gain=None, has_cooler=prop.has_cooler, ...)`. Map exposure seconds→µs (`int(round(seconds*1e6))`) into `set_control(cam_id, ASI_EXPOSURE=1, µs)`; gain→`set_control(0)`; offset→`set_control(5)`; `set_roi(w//bin, h//bin, bin, ASI_IMG_RAW16=2)`; `start_exposure(cam_id, dark=not light)`. `image_ready`: status 2→True, 3→raise `DeviceError`, else False. `get_temperature`: `get_control(ASI_TEMPERATURE=8)/10.0`. Guard the module-level `registry.register_adapter("zwo-asi", AsiCameraAdapter)` in a try/except ValueError so repeated imports don't raise.

- [ ] **Step 4: Run to verify it passes**

Run: `cd server && python -m pytest tests/test_zwo_asi_adapter.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Wire into the contract suite + commit**

Append `("zwo-asi-fake", lambda: AsiCameraAdapter(sdk=FakeAsiSdk(8, 6)))` to `CONTRACT_ADAPTERS` in `test_camera_contract.py` (import the fake). Run `cd server && python -m pytest tests/test_camera_contract.py -q` → PASS across both params.

```bash
git add server/astrodeck/devices/cameras/zwo_asi.py server/tests/test_zwo_asi_adapter.py server/tests/test_camera_contract.py
git commit -m "feat(cameras): ZWO ASI adapter (guide camera) + contract coverage"
```

---

### Task 7: `zwo-asi` backend + entry point

**Files:**
- Create: `server/astrodeck/devices/backends/zwo_asi.py`
- Modify: `server/pyproject.toml` (add entry point)
- Test: `server/tests/test_zwo_asi_backend.py`

**Interfaces:**
- Consumes: `AsiCameraAdapter`, `make_asi` (Task 5/6); `NativeCamera`; the backend registration idiom from `backends/zwo_usb.py`.
- Produces: `ZwoAsiSession` (`get_device(role, conn)` builds a `NativeCamera(AsiCameraAdapter(...))` for role `"camera"` OR `"guide_camera"`; `guide_camera()` returns the device when it holds the guide role, else None; `native_guider()`/`native_solver()`→None; `health()`; `close()`). `ZwoAsiBackend` manifest: `name="zwo-asi"`, `label="ZWO ASI camera"`, `roles=("camera", "guide_camera")`, `discoverable=True`, `hostless=True`, `transport="local"`, `hardware=True`, `driver_type="zwo-asi"`, all other manifest fields explicit. `discover()` SDK-enumerated, guarded → `[]`. `register_all()`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_zwo_asi_backend.py
import pytest
from astrodeck.devices.backend import get_backend
import astrodeck.devices.backends.zwo_asi as zab


def test_backend_manifest_complete():
    b = zab.ZwoAsiBackend()
    for f in ("name", "label", "roles", "version", "author", "min_app_version",
              "transport", "hardware", "driver_type", "discoverable", "hostless"):
        assert hasattr(b, f)
    assert b.name == "zwo-asi"
    assert set(b.roles) == {"camera", "guide_camera"}
    assert b.transport == "local" and b.hardware is True


@pytest.mark.asyncio
async def test_session_builds_guide_camera(monkeypatch):
    from tests.test_zwo_asi_adapter import FakeAsiSdk
    from astrodeck.devices.cameras import zwo_asi
    monkeypatch.setattr(zwo_asi, "make_asi", lambda: FakeAsiSdk(8, 6))
    sess = zab.ZwoAsiSession()

    class Conn:  # minimal ConnSpec stand-in
        extra = {"name": "ZWO ASI220MM"}
    dev = await sess.get_device("guide_camera", Conn())
    assert dev is not None
    assert sess.guide_camera() is dev
    await sess.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python -m pytest tests/test_zwo_asi_backend.py -q`
Expected: FAIL (module missing)

- [ ] **Step 3: Write the implementation**

Mirror `backends/zwo_usb.py`. `ZwoAsiSession.get_device(role, conn)`: accept role in `("camera", "guide_camera")`; build `AsiCameraAdapter(sdk=make_asi(), index=…)` wrapped in `NativeCamera`, `await dev.connect()`, cache by role, set `dev.role = role`, return. `guide_camera()` returns `self._devices.get("guide_camera")`. `discover()`: guarded `make_asi().count()` → one `{"role": "guide_camera", "name": "ZWO ASI (USB)", "verified": True}` per unit (role hint "guide_camera" since the ASI220MM is the guide cam on this rig; the profile can still assign it to "camera"). `register_all()` sets `version=__version__` and `register(ZwoAsiBackend())`.

Add to `server/pyproject.toml` under `[project.entry-points."astrodeck.backends"]`:
```toml
zwo_asi = "astrodeck.devices.backends.zwo_asi:register_all"
```
Then refresh metadata: `cd server && pip install -e . -q`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd server && pip install -e . -q && python -m pytest tests/test_zwo_asi_backend.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Full suite + commit**

Run: `cd server && python -m pytest -q` (green).

```bash
git add server/astrodeck/devices/backends/zwo_asi.py server/tests/test_zwo_asi_backend.py server/pyproject.toml
git commit -m "feat(cameras): zwo-asi backend + entry point"
```

---

## WAVE 3 — Player One adapter (imaging, LRN/HCG)

### Task 8: Player One SDK license verification (GATING)

**Files:**
- Create: `docs/hardware/player-one-sdk-licensing.md`
- (Conditional) Add: `server/astrodeck/vendor/playerone/{PlayerOneCamera.dll,LICENSE,README.md}`

**This is a decision gate, not code.** Deliverable: a findings doc that states, with evidence, whether the Player One SDK's own license permits redistribution of `PlayerOneCamera.dll`.

- [ ] **Step 1:** Download the official Player One SDK from player-one-astronomy.com; locate the license/readme inside the package (not the website).
- [ ] **Step 2:** Record in `docs/hardware/player-one-sdk-licensing.md`: the exact license text/filename, SDK version, and a clear verdict — **PERMITS** or **FORBIDS** redistribution.
- [ ] **Step 3 (branch):**
  - **PERMITS** → vendor `PlayerOneCamera.dll` into `server/astrodeck/vendor/playerone/` with `LICENSE` (verbatim) + `README.md` (version/origin/audit trail, mirroring `vendor/zwo/README.md`). Proceed to Task 9.
  - **FORBIDS / UNCLEAR** → **STOP. Escalate to the user** with the two pre-identified fallbacks (first-run checksum-pinned download; require-install via known-path loader). Do NOT bundle. Tasks 9–12 proceed either way (the adapter loads from vendor dir OR the chosen fallback path — the loader's `_find_dll` search order already supports env override + known installs).
- [ ] **Step 4: Commit** the findings doc (and the vendored DLL iff permitted).

```bash
git add docs/hardware/player-one-sdk-licensing.md
git commit -m "docs(cameras): Player One SDK redistribution verdict"
```

---

### Task 9: PlayerOneCamera ctypes bindings + LRN symbol pinning

**Files:**
- Create: `server/astrodeck/devices/cameras/player_one_sdk.py`
- Test: `server/tests/test_player_one_sdk.py`

**Interfaces:**
- Produces: `PlayerOneSdk` (synchronous) exposing: `count()`, `get_properties(idx)->PoaProperty` (camera_id, name, sensor w/h, pixel size, is_color, bayer, bit_depth, is_cooled, max bin), `open(cam_id)`, `close(cam_id)`, `get_config_attributes(cam_id)` (enumerate configs incl. gain/offset ranges, whether HCG/read-mode config exists), `set_config(cam_id, config_id, value, is_auto=False)`, `get_config(cam_id, config_id)`, `set_image_format(cam_id, w, h, bin, img_format)`, `start_exposure(cam_id, is_single=True)`, `image_ready(cam_id)->bool`, `get_image_data(cam_id, nbytes, timeout_ms)->bytes`, `stop_exposure(cam_id)`, `get_egain(cam_id)->float`. `make_player_one = PlayerOneSdk` seam.

**Player One API surface (verify against the vendored `PlayerOneCamera.h`):** `POAGetCameraCount`, `POAGetCameraProperties` (→ `POACameraProperties`), `POAOpenCamera`, `POAInitCamera`, `POACloseCamera`, `POAGetConfigAttributes`/`POAGetConfigsCount`, `POAGetConfigValueRange`, `POASetConfig`, `POAGetConfig`, `POASetImageFormat`/`POASetImageSize`/`POASetImageBin`, `POAStartExposure`, `POAStopExposure`, `POAImageReady`, `POAGetImageData`. Config ids in `POAConfig`: `POA_EXPOSURE`, `POA_GAIN`, `POA_OFFSET`, `POA_TEMPERATURE`, `POA_TARGET_TEMP`, `POA_COOLER`, `POA_COOLER_POWER`, `POA_HEATER_POWER` (dew), `POA_EGAIN`. Image format: `POA_RAW16`.

**LRN pinning (priority):**
- [ ] **Step A:** In the vendored `PlayerOneCamera.h`, find the config controlling the **sampling / read mode** (Normal vs Low Noise). Candidates to check: a `POAConfig` member for HCG/gain-mode, or a dedicated read-mode/`POAGetSensorModeCount`/`POAGetSensorMode`/`POASetSensorMode` API (newer SDKs expose sensor modes explicitly). Record the exact symbol(s) in a module docstring comment `# LRN via <symbol> (verified against PlayerOneCamera.h vX.Y.Z)`.
- [ ] **Step B:** Expose it on `PlayerOneSdk` as `sensor_modes(cam_id)->list[str]` + `set_sensor_mode(cam_id, name_or_index)` (if the mode API exists) OR `set_config(POA_<mode>, ...)` (if it is a config). Whichever the header dictates.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_player_one_sdk.py
import sys
from pathlib import Path
import pytest
from astrodeck.devices.cameras import player_one_sdk as p


def test_signatures_declared():
    assert "POAGetCameraProperties" in p._SIGNATURES
    assert "POAStartExposure" in p._SIGNATURES


def test_make_seam():
    assert p.make_player_one is p.PlayerOneSdk


@pytest.mark.skipif(
    sys.platform != "win32"
    or not (Path(p.__file__).resolve().parent.parent / "vendor" / "playerone"
            / "PlayerOneCamera.dll").is_file(),
    reason="requires win32 + vendored PlayerOneCamera.dll")
def test_real_dll_loads():
    sdk = p.PlayerOneSdk()
    assert sdk.count() >= 0
```

- [ ] **Step 2: Run → fails** (`...cameras.player_one_sdk` missing).
- [ ] **Step 3: Implement** mirroring `zwo_sdk.py`: `_VENDOR_DIR=.../vendor/playerone`, `_DLL_SPECS={"PlayerOneCamera.dll": ([known installs], [exports above + the pinned LRN symbol])}`, copied CDLL loader, ctypes Structures for `POACameraProperties` per header, `_SIGNATURES`, `_declare`, `PlayerOneSdk` wrapper (`_check` on `POAErrors`). Module imports + declares WITHOUT loading (load in `__init__`) so CI passes DLL-less.
- [ ] **Step 4: Run → passes** (2 passed, 1 skipped).
- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/cameras/player_one_sdk.py server/tests/test_player_one_sdk.py
git commit -m "feat(cameras): PlayerOneCamera bindings + pinned LRN/sensor-mode symbol"
```

---

### Task 10: `PlayerOneAdapter` — LRN, HCG, cooling, dew, ROI

**Files:**
- Create: `server/astrodeck/devices/cameras/player_one.py`
- Test: `server/tests/test_player_one_adapter.py`

**Interfaces:**
- Consumes: `CameraAdapter/CameraCapabilities/ROI`; `PlayerOneSdk` + `make_player_one`.
- Produces: `PlayerOneAdapter(CameraAdapter)` with `__init__(self, sdk=None, index=0)`. `capabilities()`: mono IMX571 → `bayer_pattern=None`, `bit_depth=16`, `has_cooler=True`, `has_dew_heater=True`, `read_modes=("Normal", "LowNoise")` (from `sdk.sensor_modes`), `hcg_threshold_gain=125`, `extra={"egain": sdk.get_egain(...)}`, `max_adu=65535`. Optional hooks implemented: `set_read_mode` → `sdk.set_sensor_mode`; `set_target_temp`→`POA_TARGET_TEMP`; `set_cooler`→`POA_COOLER`; `get_temperature`→`POA_TEMPERATURE`; `get_cooler_power`→`POA_COOLER_POWER`; `set_dew_heater`→`POA_HEATER_POWER`. Registers `("player-one", PlayerOneAdapter)`.

- [ ] **Step 1: Write the failing test** (FakePlayerOneSdk with call log)

```python
# server/tests/test_player_one_adapter.py
import numpy as np
import pytest
from astrodeck.devices.cameras.engine import NativeCamera


class FakePoaSdk:
    def __init__(self, w=6252, h=4176):
        self.calls = []; self._w, self._h = w, h; self._mode = "Normal"
        self._ready = True
    def count(self): return 1
    def get_properties(self, i):
        from astrodeck.devices.cameras.player_one import PoaProperty
        return PoaProperty(camera_id=0, name="Poseidon-M Pro", width=self._w,
                           height=self._h, pixel_size_um=3.76, is_color=False,
                           bayer=None, bit_depth=16, is_cooled=True,
                           max_bin=4, max_gain=600, max_offset=1000)
    def open(self, cid): self.calls.append("open")
    def close(self, cid): self.calls.append("close")
    def sensor_modes(self, cid): return ["Normal", "LowNoise"]
    def set_sensor_mode(self, cid, m): self.calls.append(f"mode:{m}"); self._mode = m
    def get_egain(self, cid): return 0.25
    def set_config(self, cid, k, v, is_auto=False): self.calls.append(f"cfg:{k}={v}")
    def get_config(self, cid, k): return 2000  # temp*10 etc
    def set_image_format(self, cid, w, h, b, fmt): self.calls.append(f"fmt:{w}x{h}/{b}")
    def start_exposure(self, cid, is_single=True): self.calls.append("start")
    def image_ready(self, cid): return self._ready
    def get_image_data(self, cid, nbytes, timeout_ms):
        return np.zeros(self._w * self._h, dtype="<u2").tobytes()
    def stop_exposure(self, cid): self.calls.append("stop")


@pytest.mark.asyncio
async def test_poa_capabilities_expose_lrn_and_hcg():
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter
    a = PlayerOneAdapter(sdk=FakePoaSdk(8, 6))
    caps = a.capabilities()
    assert caps.read_modes == ("Normal", "LowNoise")
    assert caps.hcg_threshold_gain == 125
    assert caps.has_cooler and caps.has_dew_heater
    assert abs(caps.extra["egain"] - 0.25) < 1e-9


@pytest.mark.asyncio
async def test_poa_lrn_applied_via_engine():
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter
    a = PlayerOneAdapter(sdk=FakePoaSdk(8, 6))
    cam = NativeCamera(a)
    await cam.connect()
    await cam.expose(0.01, gain=125, offset=10, read_mode="LowNoise")
    assert "mode:LowNoise" in a._sdk.calls
    assert a._sdk.calls.index("mode:LowNoise") < a._sdk.calls.index("start")


@pytest.mark.asyncio
async def test_poa_cooling_via_engine():
    from astrodeck.devices.cameras.player_one import PlayerOneAdapter
    a = PlayerOneAdapter(sdk=FakePoaSdk(8, 6))
    cam = NativeCamera(a)
    await cam.connect()
    await cam.set_cooler(True, target_c=-10.0)
    assert any("cfg:" in c for c in a._sdk.calls)
```

- [ ] **Step 2: Run → fails.**
- [ ] **Step 3: Implement** `PoaProperty` dataclass + `PlayerOneAdapter` per Interfaces. Exposure seconds→µs into `POA_EXPOSURE`. `image_ready` polls `sdk.image_ready`. `read_frame` → `sdk.get_image_data(w*h*2, timeout_ms)`. Guard the module-level `registry.register_adapter("player-one", PlayerOneAdapter)`.
- [ ] **Step 4: Run → passes.**
- [ ] **Step 5:** Add `("player-one-fake", lambda: PlayerOneAdapter(sdk=FakePoaSdk(8, 6)))` to `CONTRACT_ADAPTERS`; run contract suite green; commit.

```bash
git add server/astrodeck/devices/cameras/player_one.py server/tests/test_player_one_adapter.py server/tests/test_camera_contract.py
git commit -m "feat(cameras): Player One adapter — LRN mode, HCG, cooling, dew, ROI"
```

---

### Task 11: `player-one` backend + entry point

**Files:**
- Create: `server/astrodeck/devices/backends/player_one.py`
- Modify: `server/pyproject.toml`
- Test: `server/tests/test_player_one_backend.py`

**Interfaces:** Mirror Task 7. `PlayerOneBackend`: `name="player-one"`, `label="Player One camera"`, `roles=("camera", "guide_camera")`, `transport="local"`, `hostless=True`, `hardware=True`, `driver_type="player-one"`, all manifest fields explicit. Session builds `NativeCamera(PlayerOneAdapter(...))`; `discover()` guarded; `register_all()`.

- [ ] **Step 1–4:** Test mirrors `test_zwo_asi_backend.py` (manifest completeness; session builds a `camera`-role device). Implement mirroring `backends/zwo_asi.py`. Add entry point `player_one = "astrodeck.devices.backends.player_one:register_all"`; `pip install -e . -q`.
- [ ] **Step 5:** Full suite green; commit.

```bash
git add server/astrodeck/devices/backends/player_one.py server/tests/test_player_one_backend.py server/pyproject.toml
git commit -m "feat(cameras): player-one backend + entry point"
```

---

### Task 12: LRN/HCG read-noise acceptance harness

**Files:**
- Create: `server/astrodeck/imaging/readnoise.py`
- Test: `server/tests/test_camera_readnoise.py`

**Interfaces:**
- Produces: `read_noise_e(frames: list[np.ndarray], egain_e_per_adu: float) -> float` — read noise in electrons from bias frames: `std(ADU) * egain` averaged over frames (use the robust per-pixel temporal std when ≥2 frames, else spatial std). `compare_modes(normal: dict, low: dict) -> dict` returning `{"normal_e":…, "low_e":…, "improved": low_e < normal_e}`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_camera_readnoise.py
import numpy as np
import pytest
from astrodeck.imaging.readnoise import read_noise_e, compare_modes


def _bias(sigma_adu, seed):
    rng = np.random.default_rng(seed)
    return (1000 + rng.normal(0, sigma_adu, size=(64, 64))).astype(np.float64)


def test_read_noise_electrons_recovered():
    egain = 0.25
    frames = [_bias(8.0, s) for s in range(4)]
    rn = read_noise_e(frames, egain)
    assert abs(rn - 8.0 * egain) < 0.15   # ~2.0 e-


def test_lrn_lower_than_normal():
    egain = 0.25
    normal = {"frames": [_bias(16.0, s) for s in range(3)], "egain": egain}
    low = {"frames": [_bias(6.0, s + 100) for s in range(3)], "egain": egain}
    out = compare_modes(normal, low)
    assert out["improved"] is True
    assert out["low_e"] < out["normal_e"]
```

- [ ] **Step 2: Run → fails.**
- [ ] **Step 3: Implement** `readnoise.py` (numpy temporal/spatial std → electrons; `compare_modes`).
- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit.**

```bash
git add server/astrodeck/imaging/readnoise.py server/tests/test_camera_readnoise.py
git commit -m "feat(cameras): read-noise acceptance harness (LRN/HCG verification)"
```

---

## WAVE 4 — Orchestrator guide_camera generalization

### Task 13: Resolve `guide_camera` from its own endpoint (backward-compatible)

**Files:**
- Modify: `server/astrodeck/devices/orchestrator.py` (`connect_profile` guide-camera block ~258-269, `_pick_guide_camera` ~287-297)
- Test: `server/tests/test_orchestrator_guide_camera.py`

**Interfaces:**
- Consumes: existing `connect_profile`, `_pick_guide_camera`, `rig` dict, `ConnectResult`.
- Produces: `_pick_guide_camera(camera_conn, sessions, rig)` — **prefers `rig.get("guide_camera")`** (a device the main loop filled from a separate `guide_camera`-role endpoint), **falls back** to the camera-role session's `guide_camera()` accessor (sim/NINA one-session case). Hardware stamping fixed: a rig-sourced guide camera is already stamped by the main loop (its OWN backend's hw flag) and must NOT be re-stamped from the camera backend; only the accessor-sourced one gets stamped from the camera backend.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_orchestrator_guide_camera.py
import pytest
from astrodeck.devices import orchestrator as orch


class _Dev:
    def __init__(self, tag): self.tag = tag; self.hardware = False; self.role = ""


class _SessAccessor:
    """A sim/NINA-style session that owns the guide cam via the accessor."""
    def __init__(self, gc): self._gc = gc
    def guide_camera(self): return self._gc


def test_prefers_rig_guide_camera_over_accessor():
    # rig already has a guide_camera filled by its OWN endpoint (separate backend)
    rig_gc = _Dev("asi")
    rig = {"camera": _Dev("poseidon"), "guide_camera": rig_gc}
    picked = orch._pick_guide_camera(None, {}, rig)
    assert picked is rig_gc


def test_falls_back_to_camera_session_accessor():
    sim_gc = _Dev("sim-gc")

    class Conn:  # normalizes to a key present in sessions
        backend = "sim"; host = ""; port = 0; dev_type = ""; dev_num = 0
        role = "camera"; port_path = None; transport = "local"
        extra = {}
    conn = Conn()
    sessions = {orch._normalize(conn): _SessAccessor(sim_gc)}
    picked = orch._pick_guide_camera(conn, sessions, {})   # no rig guide_camera
    assert picked is sim_gc
```

- [ ] **Step 2: Run → fails** (`_pick_guide_camera` takes 2 args).

Run: `cd server && python -m pytest tests/test_orchestrator_guide_camera.py -q`
Expected: FAIL (TypeError: too many positional args)

- [ ] **Step 3: Implement**

Replace `_pick_guide_camera` (orchestrator.py ~287):

```python
def _pick_guide_camera(camera_conn: ConnSpec | None,
                       sessions: dict[EndpointKey, BackendSession],
                       rig: dict[str, object]) -> object | None:
    """The guide camera, from EITHER source:
    1. its own ``guide_camera`` role, filled by the main get_device loop from a
       SEPARATE backend/endpoint (native two-vendor rigs: ASI guide + Player One
       imaging) — already hardware-stamped by the loop; OR
    2. the camera-role session's ``guide_camera()`` accessor (sim/NINA, where one
       session owns both cameras).
    Prefer (1); fall back to (2)."""
    dev = rig.get("guide_camera")
    if dev is not None:
        return dev
    if camera_conn is None:
        return None
    session = sessions.get(_normalize(camera_conn))
    if session is None:
        return None
    return session.guide_camera()
```

Update the call site in `connect_profile` (~261) to pass `rig`, and fix stamping so only the accessor-sourced device is re-stamped from the camera backend:

```python
    guide_camera = _pick_guide_camera(resolved.get("camera"), sessions, rig)
    # A rig-sourced guide camera (its own role/endpoint) is already stamped by the
    # main loop with its OWN backend's hardware flag; only stamp the accessor-
    # sourced one (sim/NINA) from the camera backend.
    if guide_camera is not None and rig.get("guide_camera") is None:
        cam_conn = resolved.get("camera")
        if cam_conn is not None:
            try:
                guide_camera.hardware = bool(
                    getattr(get_backend(cam_conn.backend), "hardware", False))
            except Exception:  # noqa: BLE001
                pass
```

- [ ] **Step 4: Run → passes.** Then the FULL orchestrator/provider suite to prove no regression to the sim/NINA path:

Run: `cd server && python -m pytest tests/test_orchestrator_guide_camera.py tests/ -k "orchestrat or guide or provider or connect" -q`
Expected: PASS (existing guide-camera tests still green).

- [ ] **Step 5: Full suite + commit**

Run: `cd server && python -m pytest -q` (green).

```bash
git add server/astrodeck/devices/orchestrator.py server/tests/test_orchestrator_guide_camera.py
git commit -m "feat(orchestrator): resolve guide_camera role from its own endpoint (bw-compat)"
```

---

## WAVE 5 — Version bump, deploy, at-scope validation

### Task 14: 0.2.7 bump + deploy runbook + at-scope checklist

**Files:**
- Modify: `server/astrodeck/__init__.py` (`__version__="0.2.7"`), `server/pyproject.toml` (version)
- Create: `docs/hardware/native-cameras-validation.md` (runbook + at-scope checklist)

- [ ] **Step 1:** Bump version in both files. Confirm `plugin_load_report()` shows `zwo-asi` and `player-one` loaded (`cd server && python -c "from astrodeck.devices.backends._discovery import plugin_load_report; print(plugin_load_report())"`).
- [ ] **Step 2:** Full suite green: `cd server && python -m pytest -q`.
- [ ] **Step 3:** Write `docs/hardware/native-cameras-validation.md` with the at-scope runbook:
  - **Enumerate/connect** both cameras with no vendor software running.
  - **Bias-frame read-noise (photon-free):** capture N bias frames at Normal vs LowNoise (gain 0), and gain-124 vs gain-125; run `read_noise_e`; expect LRN lower and gain-125 approaching ~1.36 e⁻. Record numbers.
  - **Cooling:** set −10 °C, confirm regulation + `cooler_power()` reporting.
  - **Light frames** through the Poseidon (imaging train end-to-end preview/FITS).
  - **Native-guider first light** through the ASI220MM + native AM5N mount (clear night; cross-ref native-guider-first-light-readiness memory).
  - **Rollback** intact (releases\ layout, prior 0.2.6 current symlink).
- [ ] **Step 4: Commit** and (on user go) deploy via the accessory campaign's `build_release.py` → scp → `install-0.2.7.ps1` pattern.

```bash
git add server/astrodeck/__init__.py server/pyproject.toml docs/hardware/native-cameras-validation.md
git commit -m "chore(cameras): 0.2.7 bump + native-camera validation runbook"
```

---

## Self-Review

**Spec coverage:** §1 thin-waist → Tasks 1–3; §2 engine → 2–3; §3 adapter → 1; §4 capabilities/no-LCD → 1 (+ read_modes/hcg in 10); §5 Player One LRN/HCG → 9–10; §6 ZWO ASI → 5–6; §7 add-a-brand contract → 4 (registry+contract suite); §8 backends + orchestrator → 7, 11, 13; §9 testing (contract suite, fakes, read-noise, degradation) → 4, 6, 10, 12; §10 SDK licensing → 5 (ZWO), 8 (Player One gate). Build order §ordered → Waves 1–5. **No gaps.**

**Placeholder scan:** SDK binding tasks (5, 9) intentionally carry "verify export names/struct layout against the vendored header" steps — this is not a placeholder but the honest, required shape (the DLLs/headers are not in this environment; the exact symbols, esp. the Player One LRN symbol per spec §5, are pinned at build time). Every vendor-blind task (1–4, 13) and every fake-driven adapter test carries complete, runnable code.

**Type consistency:** `NativeCamera(adapter, index, name)`, `CameraAdapter` hook names, `CameraCapabilities` fields, `ROI(x,y,w,h,bin)`, `_pick_guide_camera(camera_conn, sessions, rig)`, `read_noise_e(frames, egain)` are used identically across all tasks that reference them. Adapter registration name strings (`"zwo-asi"`, `"player-one"`) match their backend `driver_type`/`name`.

## Execution Handoff

Recommended: **subagent-driven-development**, but the campaign's proven mode (opus plan-writers + one sonnet implementer derailed last session) is **inline execution** — Waves 1 and 4 are fully implementable + testable in-session now (vendor-blind); Waves 2, 3, 5 are code-complete against fakes but their vendored DLLs + at-scope steps land on astrotown when the SDKs/hardware are in hand (the accessory-campaign deploy-then-validate pattern).
