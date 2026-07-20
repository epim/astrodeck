# Driver Framework (sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize AstroDeck's device-backend registry into a third-party-ready driver framework — manifest+versioning, entry-point discovery, first-class serial addressing, registry-derived driver types, and a device-borne hardware-safety flag — without breaking any existing backend, config, or profile.

**Architecture:** Every change is additive to the existing `devices/backend.py` registry + Protocol layer. New `Backend` manifest fields default so present backends satisfy the Protocol unchanged; new schema fields default to the network shape so legacy JSON loads identically; the real-hardware safety signal moves off a central string tuple onto the device (stamped once from the backend manifest at the orchestrator). Discovery adds `importlib.metadata` entry points alongside the existing built-in imports.

**Tech Stack:** Python 3.11+, pydantic v2, FastAPI, pytest (+pytest-asyncio, pytest-xdist), `packaging` for version compare. Rust is untouched.

## Global Constraints

Every task's requirements implicitly include these:

- **Additive & back-compatible.** Every new field defaults to the network / `False` / `""` shape. Legacy config and profile JSON with none of the new keys MUST load unchanged with identical behavior. Each schema task includes an explicit back-compat test.
- **No core edit to add a driver.** External backends load via `importlib.metadata` entry points (group `astrodeck.backends`). A plugin load failure is caught and recorded, NEVER fatal (mirror the `providers.resolve_all` never-raises discipline: catch `Exception`, not `BaseException`).
- **Built-ins are never version-gated.** `min_app_version` gating applies only to discovered external backends.
- **Collision guard.** A discovered plugin may not overwrite a built-in backend `name`; if it tries, restore the built-in and record the refusal.
- **Safety is critical.** The real-hardware signal is device-borne (`Device.hardware`), stamped once from the backend manifest at the orchestrator — for role devices AND the `guide_camera` pseudo-device — and read by `providers.py` at all three sites. `_REAL_BACKENDS` is deleted.
- **Stable-identity rule.** A backend's `name` is the identity embedded in saved profiles/config and MUST NOT change across versions.
- **Tests run from the `server/` directory.** Per-step single-file run: `python -m pytest tests/<file>::<test> -v -n0`. Full suite: `python -m pytest` (uses `addopts = -n 12`). Commit after each green step.
- **Commit trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

## File Structure

**Modified**
- `server/astrodeck/devices/backend.py` — `Backend` Protocol manifest fields; `list_backends()` emits them; `ConnSpec` serial fields (`transport`, `port_path`) + `to_dict`/`from_dict`.
- `server/astrodeck/devices/base.py` — `Device.hardware: bool = False`.
- `server/astrodeck/devices/alpaca.py`, `server/astrodeck/devices/nina.py` — device-class `hardware = True` next to `backend = "..."`.
- `server/astrodeck/devices/backends/{sim,nina,native,phd2,ascom_local}_backend.py` (+`ascom_local.py`) — manifest attributes on each `*Backend` class.
- `server/astrodeck/devices/orchestrator.py` — stamp `device.hardware` from the backend manifest (role devices + `guide_camera`).
- `server/astrodeck/providers.py` — read `getattr(dev,"hardware",False)` at three sites; delete `_REAL_BACKENDS`.
- `server/astrodeck/config.py` — `DriverType = str`; `DriverEntry` gains `transport`/`port_path` + `_check_transport` validator; relax `host`/`port`.
- `server/astrodeck/profiles.py` — `ProfileDevice` gains `transport`/`port_path`; `to_rigspec` carries them.
- `server/astrodeck/drivers.py` — registry-derived `driver_type_to_backend()` / `configurable_driver_types()`; replace `_DRIVER_TYPE_TO_BACKEND` use; probe guard in `_probe_configured`.
- `server/astrodeck/api/app.py` — `add_driver` validates `body.type` against the registry; new `/api/backends/plugins` read-only route.
- `server/pyproject.toml` — add `packaging` to `dependencies`.

**Created**
- `server/astrodeck/devices/backends/_discovery.py` — `discover_plugin_backends()`, `plugin_load_report()`.
- `server/tests/test_driver_framework.py` — all new tests.

---

## Task 1: Backend manifest fields

**Files:**
- Modify: `server/astrodeck/devices/backend.py` (Protocol ~`:143-168`; `list_backends` `:193-204`)
- Modify: `server/astrodeck/devices/backends/sim_backend.py:172-179`, `nina_backend.py:131-139`, `native_backend.py:195-209`, `phd2_backend.py:123-131`, `ascom_local.py:38-44`
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Produces: `Backend` Protocol attributes `version:str`, `author:str`, `min_app_version:str`, `transport:str`, `hardware:bool`, `driver_type:str` (all defaulted). `list_backends()` dicts gain keys `version, author, min_app_version, transport, hardware, driver_type`. Built-in `hardware`: native=True, nina=True, ascom-local=True, sim=False, phd2=False. Built-in `driver_type`: nina="nina", native="alpaca", phd2="phd2", sim="", ascom-local="".

- [ ] **Step 1: Write the failing test**

Add to `server/tests/test_driver_framework.py`:
```python
"""Driver framework: manifest, safety flag, serial addressing, registry-derived
driver types, and entry-point discovery."""
from astrodeck.devices import backends as _backends  # noqa: F401  (registration)
from astrodeck.devices.backend import BACKENDS, list_backends


def _by_name():
    return {b["name"]: b for b in list_backends()}


def test_list_backends_emits_manifest_fields():
    rows = list_backends()
    assert rows, "built-in backends must be registered"
    keys = {"name", "label", "roles", "discoverable", "version", "author",
            "min_app_version", "transport", "hardware", "driver_type"}
    for row in rows:
        assert keys <= set(row), f"missing manifest keys in {row['name']}"


def test_builtin_hardware_flags():
    m = _by_name()
    assert m["native"]["hardware"] is True
    assert m["nina"]["hardware"] is True
    assert m["sim"]["hardware"] is False
    assert m["phd2"]["hardware"] is False


def test_builtin_driver_types_reproduce_todays_map():
    m = _by_name()
    assert m["nina"]["driver_type"] == "nina"
    assert m["native"]["driver_type"] == "alpaca"
    assert m["phd2"]["driver_type"] == "phd2"
    assert m["sim"]["driver_type"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `server/`): `python -m pytest tests/test_driver_framework.py::test_list_backends_emits_manifest_fields -v -n0`
Expected: FAIL — `KeyError`/`assert` on missing manifest keys.

- [ ] **Step 3: Add manifest fields to the Protocol and `list_backends`**

In `backend.py`, extend the `Backend` Protocol (after `hostless: bool = False`, ~`:159`):
```python
    #: --- driver manifest (additive; all defaulted so existing backends satisfy
    #: the Protocol unchanged). ``name`` remains the STABLE identity embedded in
    #: saved profiles/config and MUST NOT change across versions.
    version: str = "0"            # driver semver; built-ins report the app version
    author: str = ""             # "" for first-party
    min_app_version: str = "0"   # discovered externals older-than-app are skipped
    transport: str = "network"   # "network" | "serial"  ("loopback" reserved)
    hardware: bool = False       # real hardware? -> the device-borne safety flag
    driver_type: str = ""        # config-facing configurable-driver type ("" = none)
```

Rewrite `list_backends()` to emit them with `getattr` defaults (so a sparse third-party backend still lists):
```python
def list_backends() -> list[dict]:
    """A JSON-able summary of every registered backend (for the UI / API):
    ``[{name, label, roles, discoverable, version, author, min_app_version,
    transport, hardware, driver_type}, ...]``, ordered by name."""
    return [
        {
            "name": b.name,
            "label": b.label,
            "roles": tuple(b.roles),
            "discoverable": bool(b.discoverable),
            "version": str(getattr(b, "version", "0")),
            "author": str(getattr(b, "author", "")),
            "min_app_version": str(getattr(b, "min_app_version", "0")),
            "transport": str(getattr(b, "transport", "network")),
            "hardware": bool(getattr(b, "hardware", False)),
            "driver_type": str(getattr(b, "driver_type", "")),
        }
        for b in sorted(BACKENDS.values(), key=lambda b: b.name)
    ]
```

- [ ] **Step 4: Set manifest attrs on each built-in backend class**

`sim_backend.py` (in `class SimBackend`, after `hostless = True`):
```python
    from astrodeck import __version__ as _app_version
    version = _app_version
    hardware = False
    driver_type = ""
```
> Note: put `from astrodeck import __version__ as _app_version` at module top instead of the class body if the linter objects; astrodeck's `__init__` is trivial (no cycle). Apply the same `version = _app_version` to each backend below.

`nina_backend.py` (`class NinaBackend`, after `hostless = False`):
```python
    version = _app_version   # from astrodeck import __version__ as _app_version (module top)
    hardware = True
    driver_type = "nina"
```
`native_backend.py` (`class NativeBackend`, after `hostless = False`):
```python
    version = _app_version
    hardware = True
    driver_type = "alpaca"     # preserves _DRIVER_TYPE_TO_BACKEND["alpaca"] = "native"
```
`phd2_backend.py` (`class Phd2Backend`, after `hostless = True`):
```python
    version = _app_version
    hardware = False
    driver_type = "phd2"
```
`ascom_local.py` (`class AscomLocalBackend`, after `hostless = False`):
```python
    version = _app_version
    hardware = True            # reuses NativeSession -> real Alpaca devices
    driver_type = ""           # implicit built-in, not a user-configured type
    transport = "network"      # loopback HTTP, network-addressed
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_driver_framework.py -v -n0`
Expected: PASS (all three tests).

- [ ] **Step 6: Commit**

```bash
git add server/astrodeck/devices/backend.py server/astrodeck/devices/backends/ server/tests/test_driver_framework.py
git commit -m "feat(devices): backend driver manifest (version/author/min_app_version/transport/hardware/driver_type)"
```

---

## Task 2: Device-borne hardware safety flag

**Files:**
- Modify: `server/astrodeck/devices/base.py` (`Device` class ~`:82`)
- Modify: `server/astrodeck/devices/alpaca.py:266`, `server/astrodeck/devices/nina.py:183`
- Modify: `server/astrodeck/devices/orchestrator.py` (`connect_profile` `:199-258`)
- Modify: `server/astrodeck/providers.py` (`:63`, `:157`, `:356`, `:375`)
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Consumes: `Backend.hardware` (Task 1).
- Produces: `Device.hardware: bool = False` (class attr, always present). Orchestrator sets `device.hardware = get_backend(name).hardware` for each connected role device and the `guide_camera` pseudo-device. `providers.py` reads `getattr(dev, "hardware", False)`. `_REAL_BACKENDS` no longer exists.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_driver_framework.py`:
```python
import astrodeck.providers as providers
from astrodeck.devices.backend import ConnSpec, RigSpec, register
from astrodeck.devices.base import Telescope
from astrodeck.devices.orchestrator import connect_profile


def test_device_hardware_defaults_false():
    class _T(Telescope):
        async def connect(self): ...
        async def disconnect(self): ...
        async def get_position(self): return (0.0, 0.0)
        async def slew(self, ra, dec): ...
        async def sync(self, ra, dec): ...
        async def set_tracking(self, on): ...
        async def get_tracking(self): return False
        async def park(self): ...
        async def unpark(self): ...
        async def is_parked(self): return True
        async def move_axis(self, axis, rate): ...
        async def is_slewing(self): return False
    assert _T("t").hardware is False


def test_providers_has_no_real_backends_tuple():
    assert not hasattr(providers, "_REAL_BACKENDS")


async def test_orchestrator_stamps_hardware_from_backend():
    # sim rig: camera device is stamped False
    res = await connect_profile(RigSpec("sim"))
    assert res.rig["camera"].hardware is False

    # a fake hardware=True telescope backend: its device is stamped True
    class _Dev(Telescope):
        async def connect(self): ...
        async def disconnect(self): ...
        async def get_position(self): return (0.0, 0.0)
        async def slew(self, ra, dec): ...
        async def sync(self, ra, dec): ...
        async def set_tracking(self, on): ...
        async def get_tracking(self): return False
        async def park(self): ...
        async def unpark(self): ...
        async def is_parked(self): return True
        async def move_axis(self, axis, rate): ...
        async def is_slewing(self): return False

    class _Session:
        name = "fakehw"
        async def get_device(self, role, conn): return _Dev("scope")
        def native_guider(self): return None
        def guide_camera(self): return None
        def native_solver(self): return None
        async def health(self): return None
        async def close(self): ...

    class _Backend:
        name = "fakehw"; label = "Fake HW"; roles = ("telescope",)
        discoverable = False; hostless = True; hardware = True
        async def open(self, conn): return _Session()
        async def discover(self): return []

    register(_Backend())
    try:
        res2 = await connect_profile(RigSpec("fakehw"))
        assert res2.rig["telescope"].hardware is True
    finally:
        BACKENDS.pop("fakehw", None)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_driver_framework.py::test_providers_has_no_real_backends_tuple tests/test_driver_framework.py::test_orchestrator_stamps_hardware_from_backend -v -n0`
Expected: FAIL — `_REAL_BACKENDS` still exists; sim device has no `hardware` attr default won't be stamped (AttributeError-free but assertion on the fakehw path fails because stamping code absent).

- [ ] **Step 3: Add `Device.hardware` default**

In `base.py`, in `class Device`, add after `backend: str = ""` (`:82`):
```python
    #: real hardware? Read by providers to gate fake/sim plate-solves. Default
    #: False (safe); real device classes set True and the orchestrator stamps the
    #: authoritative value from the backend manifest at connect time.
    hardware: bool = False
```

- [ ] **Step 4: Set `hardware = True` on the real device base classes**

In `alpaca.py`, next to `backend = "alpaca"` (`:266`), add:
```python
    hardware = True
```
In `nina.py`, next to `backend = "nina"` (`:183`), add:
```python
    hardware = True
```
> Rationale: keeps directly-constructed AlpacaTelescope/NinaTelescope instances (used across existing provider tests) reading as real hardware without relying on the orchestrator stamp.

- [ ] **Step 5: Stamp `hardware` in the orchestrator**

In `orchestrator.py` `connect_profile`, after `opened.append(session)` (`:212`), compute the backend flag once per endpoint:
```python
            sessions[key] = session
            opened.append(session)
            backend_hw = bool(getattr(get_backend(backend_name), "hardware", False))
```
Then in the device branch (`:225-226`), change:
```python
                if device is not None:
                    rig[role] = device
```
to:
```python
                if device is not None:
                    try:
                        device.hardware = backend_hw
                    except Exception:  # noqa: BLE001 - stamping must never break connect
                        pass
                    rig[role] = device
```
After `guide_camera = _pick_guide_camera(resolved.get("camera"), sessions)` (`:250`), stamp the pseudo-device:
```python
    if guide_camera is not None:
        cam_conn = resolved.get("camera")
        if cam_conn is not None:
            try:
                guide_camera.hardware = bool(
                    getattr(get_backend(cam_conn.backend), "hardware", False))
            except Exception:  # noqa: BLE001
                pass
```

- [ ] **Step 6: Switch `providers.py` to the device flag**

Delete `_REAL_BACKENDS = ("nina", "alpaca")` (`:63`, and its comment `:61-62`).
Change the three checks:
- `:157` `if dev is not None and getattr(dev, "backend", "") in _REAL_BACKENDS:` → `if dev is not None and getattr(dev, "hardware", False):`
- `:356` `real_rig = gcam is not None and getattr(gcam, "backend", "") in _REAL_BACKENDS` → `real_rig = gcam is not None and getattr(gcam, "hardware", False)`
- `:375` `is_real = cam2 is not None and getattr(cam2, "backend", "") in _REAL_BACKENDS` → `is_real = cam2 is not None and getattr(cam2, "hardware", False)`

- [ ] **Step 7: Run the new tests, then the provider/orchestrator suites**

Run: `python -m pytest tests/test_driver_framework.py -v -n0`
Expected: PASS.
Run the safety-adjacent existing suites to catch any test that faked "real" via `.backend`:
`python -m pytest tests/test_providers.py tests/test_orchestrator.py -v -n0`
Expected: PASS. If a test constructed a bare mock with `.backend="alpaca"` and expected real behavior, update it to set `.hardware = True` (real device classes already carry it, so only ad-hoc mocks need this).

- [ ] **Step 8: Commit**

```bash
git add server/astrodeck/devices/base.py server/astrodeck/devices/alpaca.py server/astrodeck/devices/nina.py server/astrodeck/devices/orchestrator.py server/astrodeck/providers.py server/tests/test_driver_framework.py
git commit -m "feat(safety): device-borne hardware flag stamped from backend manifest; drop _REAL_BACKENDS string denylist"
```

---

## Task 3: Serial addressing on ConnSpec

**Files:**
- Modify: `server/astrodeck/devices/backend.py` (`ConnSpec` `:44-93`)
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Produces: `ConnSpec.transport: str = "network"`, `ConnSpec.port_path: str | None = None`, both carried through `to_dict`/`from_dict` (tolerant of legacy dicts without the keys).

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_connspec_serial_roundtrip_and_legacy():
    from astrodeck.devices.backend import ConnSpec
    s = ConnSpec(backend="zwo-am5", transport="serial", port_path="COM3", role="telescope")
    d = s.to_dict()
    assert d["transport"] == "serial" and d["port_path"] == "COM3"
    assert ConnSpec.from_dict(d).port_path == "COM3"
    # legacy dict (no transport/port_path) -> network defaults
    legacy = ConnSpec.from_dict({"backend": "native", "host": "h", "port": 11111})
    assert legacy.transport == "network" and legacy.port_path is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_driver_framework.py::test_connspec_serial_roundtrip_and_legacy -v -n0`
Expected: FAIL — `TypeError`/`KeyError` on `transport`/`port_path`.

- [ ] **Step 3: Add the fields**

In `ConnSpec` (after `driver_id` `:65`):
```python
    #: transport discriminator: "network" (host/port), "serial" (port_path).
    #: Defaults keep every existing spec/profile a network spec.
    transport: str = "network"
    port_path: str | None = None    # serial device path, e.g. "COM3" / "/dev/ttyACM0"
```
In `to_dict`, add `"transport": self.transport, "port_path": self.port_path,`.
In `from_dict`, add `transport=d.get("transport", "network"), port_path=d.get("port_path"),`.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_driver_framework.py::test_connspec_serial_roundtrip_and_legacy -v -n0`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/devices/backend.py server/tests/test_driver_framework.py
git commit -m "feat(devices): serial addressing (transport/port_path) on ConnSpec"
```

---

## Task 4: Registry-derived driver types

**Files:**
- Modify: `server/astrodeck/config.py` (`DriverType` `:366`)
- Modify: `server/astrodeck/drivers.py` (`_DRIVER_TYPE_TO_BACKEND` `:286`, `resolve_driver_ids` `:325`, `_probe_configured` `:236`)
- Modify: `server/astrodeck/api/app.py` (`add_driver` handler `:1205-1215`)
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Consumes: `Backend.driver_type` (Task 1).
- Produces: `config.DriverType = str`. `drivers.driver_type_to_backend() -> dict[str,str]` and `drivers.configurable_driver_types() -> set[str]`, both derived from `BACKENDS` (importing the backends package first for registration). `resolve_driver_ids` uses `driver_type_to_backend()`. `_probe_configured` no longer `KeyError`s on a type without a network probe. The `add_driver` API rejects a `type` not in `configurable_driver_types()` with HTTP 422.

- [ ] **Step 1: Write the failing tests**

Append:
```python
def test_registry_derived_driver_type_map_matches_today():
    from astrodeck import drivers
    m = drivers.driver_type_to_backend()
    assert m == {"nina": "nina", "alpaca": "native", "phd2": "phd2"}
    assert drivers.configurable_driver_types() == {"nina", "alpaca", "phd2"}


def test_driver_type_map_extends_with_plugin():
    from astrodeck import drivers
    from astrodeck.devices.backend import register, BACKENDS

    class _B:
        name = "demo-be"; label = "Demo"; roles = ("telescope",)
        discoverable = False; hostless = True; driver_type = "demo"; hardware = True
        async def open(self, conn): ...
        async def discover(self): return []
    register(_B())
    try:
        assert drivers.driver_type_to_backend()["demo"] == "demo-be"
    finally:
        BACKENDS.pop("demo-be", None)


def test_config_driver_type_is_open_str():
    from astrodeck.config import DriverType
    assert DriverType is str
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_driver_framework.py::test_registry_derived_driver_type_map_matches_today tests/test_driver_framework.py::test_config_driver_type_is_open_str -v -n0`
Expected: FAIL — `AttributeError` (`driver_type_to_backend` absent); `DriverType` is a `Literal`, not `str`.

- [ ] **Step 3: Open the `DriverType` alias**

In `config.py:366` change:
```python
DriverType = Literal["nina", "alpaca", "phd2"]
```
to:
```python
# Open string: the configurable-driver vocabulary is now registry-derived
# (drivers.configurable_driver_types()), validated at the API layer. Kept as a
# named alias so existing imports/annotations continue to work.
DriverType = str
```

- [ ] **Step 4: Add registry-derived maps in `drivers.py`**

Replace the `_DRIVER_TYPE_TO_BACKEND` dict (`:284-288`) with functions:
```python
def driver_type_to_backend() -> dict[str, str]:
    """Config-facing driver ``type`` -> backend registry ``name``, derived from
    the registry (each backend that provides a configurable driver type declares
    it via ``Backend.driver_type``). Built-ins reproduce the historical map
    ``{"nina":"nina","alpaca":"native","phd2":"phd2"}``; a plugin backend that
    sets ``driver_type`` extends it with no core edit."""
    from .devices import backends as _b  # noqa: F401 - ensure registration
    from .devices.backend import BACKENDS
    return {getattr(b, "driver_type", ""): b.name
            for b in BACKENDS.values() if getattr(b, "driver_type", "")}


def configurable_driver_types() -> set[str]:
    """The set of user-configurable driver ``type`` values (registry-derived)."""
    return set(driver_type_to_backend())
```
In `resolve_driver_ids`, change `:325` `backend=_DRIVER_TYPE_TO_BACKEND[d.type],` to:
```python
            backend=driver_type_to_backend().get(d.type, d.type),
```
> `.get(..., d.type)` degrades safely: an unknown type falls through to a same-named backend rather than `KeyError`-ing inside connect resolution.

- [ ] **Step 5: Guard `_probe_configured` against types without a network probe**

In `_probe_configured` (`:232-238`), replace the cache-miss probe call:
```python
    hit = _CACHE.get(entry.id)
    if not force and hit is not None and (time.monotonic() - hit[0]) < PROBE_TTL_S:
        res = hit[1]
    else:
        probe = _PROBES.get(entry.type)
        if probe is None:
            # A driver type with no network probe (e.g. a serial driver) is not
            # unreachable — it simply isn't network-probed here. Report neutrally
            # instead of KeyError-ing into describe_all's error row.
            res = {"reachable": False, "error": None,
                   "detail": f"no network probe for driver type {entry.type!r}",
                   "offers": {"devices": [], "tasks": []}}
            res["probed_at"] = time.time()
        else:
            res = await probe(entry.host, entry.port)
            res["probed_at"] = time.time()
            _CACHE[entry.id] = (time.monotonic(), res)
```

- [ ] **Step 6: Validate the driver type at the `add_driver` API**

In `app.py` `add_driver` (`:1208`), before the `try:`:
```python
        from .. import drivers as drivers_mod
        if body.type not in drivers_mod.configurable_driver_types():
            raise HTTPException(
                422, f"unknown driver type: {body.type!r}")
```
> `config_store.add_driver` keeps its own `DRIVER_DEFAULT_PORTS` check (default-port lookup for network types); the registry check makes the vocabulary extensible and is the spec's "unknown type rejected" surface now that `DriverType` no longer enforces it.

- [ ] **Step 7: Run the new tests + drivers/API suites**

Run: `python -m pytest tests/test_driver_framework.py -v -n0`
Then: `python -m pytest tests/test_drivers.py -v -n0` (and any `test_api*drivers*` file — discover with `python -m pytest tests -k driver -v -n0`).
Expected: PASS. Existing `add_driver` tests that post `type in {nina,alpaca,phd2}` still pass; an unknown type still 422s.

- [ ] **Step 8: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/drivers.py server/astrodeck/api/app.py server/tests/test_driver_framework.py
git commit -m "feat(drivers): registry-derived driver types; open DriverType; probe guard for non-network types"
```

---

## Task 5: Serial addressing on DriverEntry + ProfileDevice

**Files:**
- Modify: `server/astrodeck/config.py` (`DriverEntry` `:372-379`)
- Modify: `server/astrodeck/profiles.py` (`ProfileDevice` `:52-68`, `to_rigspec` `:136+`)
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Consumes: `ConnSpec.transport`/`port_path` (Task 3).
- Produces: `DriverEntry` gains `transport: str = "network"`, `port_path: str = ""`, relaxed `host`/`port`, and a `_check_transport` validator (serial requires `port_path`; network requires `host` + port 1..65535). `ProfileDevice` gains `transport: str = "network"`, `port_path: str = ""`; `to_rigspec` copies them into each `ConnSpec`.

> Serial-driver *creation* via `config_store.add_driver` / the API body is deferred to sub-project B (where a real serial `driver_type` exists); this task lands the schema + validators + profile carry, tested by direct construction and round-trip.

- [ ] **Step 1: Write the failing tests**

Append:
```python
def test_driverentry_serial_and_network_validation():
    import pytest
    from astrodeck.config import DriverEntry
    ser = DriverEntry(id="zwo-am5-ab12", type="zwo-am5", transport="serial", port_path="COM3")
    assert ser.transport == "serial" and ser.port_path == "COM3"
    net = DriverEntry(id="nina-ab12", type="nina", host="localhost", port=1888)
    assert net.transport == "network"
    with pytest.raises(ValueError):
        DriverEntry(id="x", type="zwo-am5", transport="serial")           # no port_path
    with pytest.raises(ValueError):
        DriverEntry(id="y", type="nina", transport="network", host="")    # no host


def test_driverentry_legacy_loads_as_network():
    from astrodeck.config import DriverEntry
    e = DriverEntry(id="nina-cd34", type="nina", host="h", port=1888)
    assert e.transport == "network" and e.port_path == ""


def test_profiledevice_serial_carries_into_rigspec():
    from astrodeck.profiles import Profile, ProfileDevice
    p = Profile(name="serial rig", primary_backend="zwo-am5", devices=[
        ProfileDevice(role="telescope", backend="zwo-am5",
                      transport="serial", port_path="COM3")])
    rig = p.to_rigspec()
    cs = rig.resolve("telescope")
    assert cs.transport == "serial" and cs.port_path == "COM3"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_driver_framework.py::test_driverentry_serial_and_network_validation -v -n0`
Expected: FAIL — `DriverEntry` has no `transport`/`port_path`; serial construction fails on required `host`.

- [ ] **Step 3: Update `DriverEntry`**

In `config.py`, replace the `DriverEntry` body (`:372-379`):
```python
class DriverEntry(BaseModel):
    id: str                                  # server-minted "<type>-<4hex>", immutable
    type: DriverType
    transport: str = "network"               # "network" | "serial"
    host: str = ""                           # network transport
    port: int = Field(default=0, ge=0, le=65535)
    port_path: str = ""                      # serial transport, e.g. "COM3"
    enabled: bool = True
    label: str = ""
    extra: dict = Field(default_factory=dict)  # driver-typed options (e.g. phd2 managed)

    @model_validator(mode="after")
    def _check_transport(self) -> "DriverEntry":
        if self.transport == "serial":
            if not self.port_path:
                raise ValueError("serial driver requires port_path")
        else:  # network
            if not self.host:
                raise ValueError(f"{self.transport} driver requires host")
            if not (1 <= self.port <= 65535):
                raise ValueError(f"{self.transport} driver requires port 1..65535")
        return self
```
> Confirm `model_validator` is imported in `config.py` (it is used by `Profile`); if not already imported at module top, add it to the pydantic import line.

- [ ] **Step 4: Update `ProfileDevice` and `to_rigspec`**

In `profiles.py` `ProfileDevice` (after `driver_id` `:63`):
```python
    transport: str = "network"   # "network" | "serial"
    port_path: str = ""          # serial device path
```
In `to_rigspec`, where each row becomes a `ConnSpec`, pass the two fields through. Locate the `ConnSpec(...)` construction inside `to_rigspec` (after `:148`) and add `transport=d.transport, port_path=(d.port_path or None),` to its kwargs (mapping `""` → `None` to match `ConnSpec.port_path`'s `str | None`).

- [ ] **Step 5: Run the new tests + config/profile suites**

Run: `python -m pytest tests/test_driver_framework.py -v -n0`
Then: `python -m pytest tests/test_config.py tests/test_profiles.py -v -n0`
Expected: PASS (legacy DriverEntry/Profile JSON still loads; new serial rows validate).

- [ ] **Step 6: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/profiles.py server/tests/test_driver_framework.py
git commit -m "feat(config): serial addressing on DriverEntry + ProfileDevice with per-transport validation"
```

---

## Task 6: Entry-point plugin discovery

**Files:**
- Create: `server/astrodeck/devices/backends/_discovery.py`
- Modify: `server/astrodeck/devices/backends/__init__.py`
- Modify: `server/pyproject.toml` (add `packaging`)
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Consumes: `Backend.version`/`min_app_version` (Task 1), `BACKENDS`/`register`.
- Produces: `discover_plugin_backends(*, group="astrodeck.backends", app_version=None) -> None` and `plugin_load_report() -> list[dict]` (rows `{name, dist, version, status, detail}`, `status ∈ {"loaded","failed","incompatible"}`). Called once at import from `backends/__init__.py`, after the built-in imports.

- [ ] **Step 1: Confirm/add the `packaging` dependency**

In `server/pyproject.toml`, add to `dependencies` (`:6-16`):
```toml
    "packaging>=23.0",
```
Verify it imports: `python -c "from packaging.version import Version; print(Version('1.2') < Version('1.10'))"` → prints `True`.

- [ ] **Step 2: Write the failing tests**

Append:
```python
import astrodeck.devices.backends._discovery as disc
from astrodeck.devices.backend import BACKENDS, register


class _FakeEP:
    def __init__(self, name, fn, dist_name="demo-dist"):
        self.name = name
        self._fn = fn
        import types
        self.dist = types.SimpleNamespace(name=dist_name)
    def load(self):
        return self._fn


def _make_backend(name, *, min_app="0", version="1.0", hardware=True):
    class _B:
        pass
    b = _B()
    b.name = name; b.label = name; b.roles = ("telescope",)
    b.discoverable = False; b.hostless = True
    b.min_app_version = min_app; b.version = version
    b.hardware = hardware; b.driver_type = ""; b.transport = "network"
    return b


def test_discovery_loads_plugin(monkeypatch):
    def reg(): register(_make_backend("demo-plugin"))
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("demo-plugin", reg)])
    try:
        disc.discover_plugin_backends(app_version="0.2.5")
        assert "demo-plugin" in BACKENDS
        assert any(r["name"] == "demo-plugin" and r["status"] == "loaded"
                   for r in disc.plugin_load_report())
    finally:
        BACKENDS.pop("demo-plugin", None)


def test_discovery_guarded_on_raise(monkeypatch):
    def boom(): raise RuntimeError("bad plugin")
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("boom", boom)])
    disc.discover_plugin_backends(app_version="0.2.5")   # must not raise
    assert "sim" in BACKENDS                              # built-ins intact
    assert any(r["status"] == "failed" for r in disc.plugin_load_report())


def test_discovery_version_gate(monkeypatch):
    def reg(): register(_make_backend("future-plugin", min_app="99.0"))
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("future-plugin", reg)])
    disc.discover_plugin_backends(app_version="0.2.5")
    assert "future-plugin" not in BACKENDS
    assert any(r["name"] == "future-plugin" and r["status"] == "incompatible"
               for r in disc.plugin_load_report())


def test_discovery_collision_guard(monkeypatch):
    original_sim = BACKENDS["sim"]
    def overwrite(): register(_make_backend("sim"))
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("evil", overwrite)])
    disc.discover_plugin_backends(app_version="0.2.5")
    assert BACKENDS["sim"] is original_sim                # built-in preserved
    assert any(r["status"] == "failed" and "built-in" in (r["detail"] or "")
               for r in disc.plugin_load_report())
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_driver_framework.py -k discovery -v -n0`
Expected: FAIL — module `_discovery` has no `md`/`discover_plugin_backends`/`plugin_load_report`.

- [ ] **Step 4: Create `_discovery.py`**

`server/astrodeck/devices/backends/_discovery.py`:
```python
"""Entry-point discovery for third-party device backends.

External packages declare ``[project.entry-points."astrodeck.backends"]`` whose
values resolve to a zero-argument callable that calls ``register(MyBackend())``
(mirroring the built-ins' self-registration idiom). Discovery is guarded per
entry point — a broken or incompatible plugin is recorded, never fatal — and
refuses to overwrite a built-in backend name.
"""
from __future__ import annotations

import importlib.metadata as md

from ..backend import BACKENDS

#: Load outcomes for the UI/API: {name, dist, version, status, detail}.
_REPORT: list[dict] = []


def plugin_load_report() -> list[dict]:
    """A JSON-able copy of the last discovery run's outcomes."""
    return [dict(r) for r in _REPORT]


def _too_new(min_app: str, app_version: str) -> bool:
    try:
        from packaging.version import Version
        return Version(str(min_app)) > Version(str(app_version))
    except Exception:  # noqa: BLE001 - unparseable version -> do not gate
        return False


def discover_plugin_backends(*, group: str = "astrodeck.backends",
                             app_version: str | None = None) -> None:
    """Discover and register external backends from ``group``. Idempotent enough
    to re-run in tests; each run rebuilds the report."""
    if app_version is None:
        from ... import __version__ as app_version  # astrodeck.__version__

    _REPORT.clear()
    builtin_names = set(BACKENDS)
    try:
        eps = md.entry_points(group=group)
    except Exception as exc:  # noqa: BLE001 - a bad environment must not break import
        _REPORT.append({"name": None, "dist": None, "version": None,
                        "status": "failed",
                        "detail": f"entry-point scan failed: {exc}"[:300]})
        return

    for ep in eps:
        dist = getattr(getattr(ep, "dist", None), "name", None)
        before = dict(BACKENDS)
        try:
            fn = ep.load()
            fn()
        except Exception as exc:  # noqa: BLE001 - never let one plugin break startup
            BACKENDS.clear(); BACKENDS.update(before)      # undo partial mutation
            _REPORT.append({"name": ep.name, "dist": dist, "version": None,
                            "status": "failed", "detail": str(exc)[:300]})
            continue

        overwritten = [n for n in builtin_names if BACKENDS.get(n) is not before.get(n)]
        added = [n for n in BACKENDS if n not in before]
        if overwritten:
            for n in overwritten:                          # restore protected built-ins
                BACKENDS[n] = before[n]
            for n in added:                                # drop the misbehaving plugin's adds
                BACKENDS.pop(n, None)
            _REPORT.append({"name": ep.name, "dist": dist, "version": None,
                            "status": "failed",
                            "detail": f"refused: overwrites built-in backend(s) "
                                      f"{sorted(overwritten)}"})
            continue

        if not added:
            _REPORT.append({"name": ep.name, "dist": dist, "version": None,
                            "status": "loaded", "detail": "registered no new backend"})
            continue

        for n in added:
            b = BACKENDS[n]
            ver = str(getattr(b, "version", "0"))
            minv = str(getattr(b, "min_app_version", "0"))
            if _too_new(minv, app_version):
                BACKENDS.pop(n, None)
                _REPORT.append({"name": n, "dist": dist, "version": ver,
                                "status": "incompatible",
                                "detail": f"requires app >= {minv} (have {app_version})"})
            else:
                _REPORT.append({"name": n, "dist": dist, "version": ver,
                                "status": "loaded", "detail": None})
```

- [ ] **Step 5: Wire discovery into the package import**

In `backends/__init__.py`, after the built-in imports (`:15`), add:
```python
from ._discovery import discover_plugin_backends, plugin_load_report  # noqa: F401

# Discover third-party backends AFTER the built-ins are registered, so the
# collision guard can protect built-in names. Guarded internally — never fatal.
discover_plugin_backends()
```
Add `"_discovery"`, `"discover_plugin_backends"`, `"plugin_load_report"` to `__all__`.

- [ ] **Step 6: Run the discovery tests + a broad import smoke**

Run: `python -m pytest tests/test_driver_framework.py -k discovery -v -n0`
Expected: PASS.
Smoke the real import path (no plugins installed → empty report, no crash):
`python -c "import astrodeck.devices.backends as b; print(len(b.plugin_load_report()))"` → prints `0`.

- [ ] **Step 7: Commit**

```bash
git add server/astrodeck/devices/backends/_discovery.py server/astrodeck/devices/backends/__init__.py server/pyproject.toml server/tests/test_driver_framework.py
git commit -m "feat(devices): importlib.metadata entry-point backend discovery (guarded, version-gated, collision-guarded)"
```

---

## Task 7: Read-only plugin-report API

**Files:**
- Modify: `server/astrodeck/api/app.py` (near the `/api/backends` route `:1163-1172`)
- Test: `server/tests/test_driver_framework.py`

**Interfaces:**
- Consumes: `plugin_load_report()` (Task 6).
- Produces: `GET /api/backends/plugins` (requires `CAP_VIEW_STATUS`) → `plugin_load_report()` list.

- [ ] **Step 1: Write the failing test**

Append (mirror the existing app/test-client fixture used by other API tests — discover it with `python -m pytest tests -k backends -v -n0` and reuse that pattern; the assertion is the payload shape):
```python
def test_backends_plugins_endpoint(client):
    r = client.get("/api/backends/plugins")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for row in body:
        assert {"name", "dist", "version", "status", "detail"} <= set(row)
```
> `client` is the project's authenticated FastAPI test-client fixture (same one `test_api_*`/backends tests use). If the suite's fixture requires a capability token, follow the pattern those tests already use for `CAP_VIEW_STATUS` routes.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_driver_framework.py::test_backends_plugins_endpoint -v -n0`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the route**

In `app.py`, immediately after the `backends()` handler (`:1172`), add:
```python
    @app.get("/api/backends/plugins",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def backend_plugins():
        """Discovery outcomes for third-party backend plugins: ``[{name, dist,
        version, status, detail}, ...]`` where status is loaded/failed/
        incompatible. Imports the backends package so discovery has run."""
        from ..devices import backends as _b  # noqa: F401 - discovery side-effect
        return _b.plugin_load_report()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_driver_framework.py::test_backends_plugins_endpoint -v -n0`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/api/app.py server/tests/test_driver_framework.py
git commit -m "feat(api): GET /api/backends/plugins surfacing plugin load report"
```

---

## Final: full-suite verification

- [ ] Run the entire suite from `server/`: `python -m pytest` (uses `-n 12`). Expected: all green. Investigate any failure that references `_REAL_BACKENDS`, `DriverType`, a `.backend`-based "real" assumption, or `DriverEntry(host=...)` required-field construction — those are the migration surfaces this plan touches.

---

## Self-Review

**Spec coverage** (against `2026-07-20-driver-framework-design.md`):
- §1 manifest/identity/versioning → Task 1 (fields + `list_backends`), version-gating uses `min_app_version` in Task 6. ✓
- §2 entry-point discovery → Task 6 (guarded, version-gated, collision-guarded, report). ✓
- §3 transport + serial addressing → Task 3 (ConnSpec) + Task 5 (DriverEntry/ProfileDevice). ✓
- §4 registry-derived driver types → Task 4 (`DriverType=str`, derived maps, probe guard, API vocab check). ✓
- §5 device-borne safety flag → Task 2 (Device flag, real-class defaults, orchestrator stamp incl. guide_camera, providers 3 sites, delete `_REAL_BACKENDS`). ✓
- API surface for the report → Task 7. ✓
- Back-compat tests → Tasks 3, 5 (legacy ConnSpec/DriverEntry), full-suite gate. ✓
- Deferred per spec non-goals: sequencer/provider/UI/untrusted-sidecar plugins; the AM5N driver (sub-project B). Not in this plan. ✓

**Deviation from spec, flagged:** the spec sketched a `probe_types()` helper; the plan implements probe handling as a guard in `_probe_configured` (a type with no network probe reports neutrally) instead, which is simpler and achieves the same "serial types don't error the driver list" goal. Serial-driver *creation* via `config_store.add_driver`/the API body is deferred to sub-project B (no serial `driver_type` exists in A to create); Task 5 lands the schema + validators + profile carry, tested by direct construction.

**Placeholder scan:** no TBD/TODO; every code step carries full code. The two spots that say "discover the fixture pattern" (Task 7 test client, Task 2 provider-mock updates) reference existing test conventions the implementer reads — not un-specified logic.

**Type consistency:** `hardware:bool` (Backend manifest, Device attr, list_backends key) consistent throughout. `transport:str`/`port_path` typed `str|None` on ConnSpec (runtime addressing) and `str=""` on DriverEntry/ProfileDevice (persisted schema); `to_rigspec` maps `""`→`None` at the boundary (Task 5 Step 4) so the ConnSpec type holds. `driver_type_to_backend()`/`configurable_driver_types()` names consistent across Tasks 4 and 6.
