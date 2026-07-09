# Backend Drivers — Phase 1 (Drivers Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Globally-configured backend drivers (NINA / Alpaca / PHD2 + implicit sim / native-Rust / ASTAP) with a probe API reporting live availability + per-role/task offers, and a Settings → Backend Drivers panel — the foundation the Equipment tab (Phase 2) and task routing (Phase 3) build on.

**Architecture:** A new `drivers` config section (pydantic, additive) with typed `ConfigStore` CRUD; a new `server/astrodeck/drivers.py` module owning implicit-driver detection, per-type probes, a 15 s TTL cache with `invalidate()`, and `describe_all()` (never raises); five FastAPI routes following the house `require()` + `@declare()` pattern; UI types + thin API client + a `DriversPanel` settings component reusing existing house components.

**Tech Stack:** Python 3.11 / FastAPI / pydantic v2 / pytest; React 18 + TS + Tailwind tokens; UI tests via the repo's inline-assert harness run with `npx tsx` (NO vitest in this repo).

**Spec:** `docs/superpowers/specs/2026-07-08-equipment-drivers-ux-design.md` (§3.1, §3.2, §4.2, §8 — Phase 1 scope only).

## Global Constraints

- Repo root: `C:\Users\bear\astro` (Git Bash: `/c/Users/bear/astro`). Server code in `server/`, UI in `ui/`.
- Run server tests from `server/`: `.venv/Scripts/python -m pytest tests/<file> -q` (Windows venv). Full suite must stay green (803+ passed; 11 `astrodeck_native` tests may skip without the wheel — expected).
- NO new Python or npm dependencies.
- Every new API route MUST use `dependencies=[Depends(require(CAP_*))]` **and** `@declare(CAP_*)` — `tests/test_backend_roles_match_served.py` fails otherwise.
- Config is additive: old `astrodeck.json` files (no `drivers` key) MUST load unchanged (pydantic default fills it). `DriverEntry` holds NO secret (host/port only) — nothing to add to `redacted()`.
- Read caps: `CAP_VIEW_STATUS`. Write caps: `CAP_CONFIG_BACKEND` (same as all connect-surface writes).
- Match the house comment style: explanatory block comments stating WHY, mirroring the files you touch.
- Commit after each task (conventional commits). Never `--no-verify`.
- Do NOT modify `ConnectView.tsx`, `BackendPicker.tsx`, or `CapabilitiesCard.tsx` — they are retired in Phase 2/3, not now.

---

### Task 1: `DriverEntry` config model + ConfigStore CRUD

**Files:**
- Modify: `server/astrodeck/config.py` (imports ~line 20; new section after `ProvidersConfig` ~line 257; `AppConfig` ~line 276; ConfigStore methods after `set_providers` ~line 543)
- Test: `server/tests/test_drivers_config.py` (create)

**Interfaces:**
- Consumes: existing `ConfigStore` (`cfg()`, `bump_and_save()`), pydantic `BaseModel`/`Field`.
- Produces (Tasks 2–5 rely on these exact signatures):
  - `class DriverEntry(BaseModel)`: `id: str`, `type: Literal["nina","alpaca","phd2"]`, `host: str`, `port: int`, `enabled: bool = True`, `label: str = ""`, `extra: dict`
  - `DRIVER_DEFAULT_PORTS: dict[str, int]` = `{"nina": 1888, "alpaca": 11111, "phd2": 4400}`
  - `AppConfig.drivers: list[DriverEntry]` (default `[]`)
  - `ConfigStore.add_driver(driver_type: str, host: str, port: int | None = None, label: str = "", extra: dict | None = None) -> DriverEntry` (raises `ValueError`)
  - `ConfigStore.update_driver(driver_id: str, patch: dict) -> DriverEntry` (raises `KeyError` unknown id, `ValueError` bad field/value)
  - `ConfigStore.delete_driver(driver_id: str) -> None` (raises `KeyError`)

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_drivers_config.py`:

```python
"""DriverEntry config CRUD (equipment-drivers spec 2026-07-08 §3.1).

Configured backend drivers are GLOBAL config (not per-profile): declared once,
referenced by id from profiles. These tests pin the CRUD contract Tasks 2-5
build on: server-minted unique ids, per-type default ports, validation errors
as ValueError (-> 422 at the API), unknown ids as KeyError (-> 404), and
additive back-compat (old config files without a ``drivers`` key load fine).
"""
import pytest

from astrodeck.config import ConfigStore


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(tmp_path / "astrodeck.json")


def test_add_driver_mints_unique_id_and_persists(store, tmp_path):
    d = store.add_driver("nina", "astrotown.lan", 1888, label="NINA on astrotown")
    assert d.id.startswith("nina-") and len(d.id) == len("nina-") + 4
    assert d.enabled is True and d.label == "NINA on astrotown"
    # round-trip: a FRESH store on the same file reads the entry back
    again = ConfigStore(tmp_path / "astrodeck.json").cfg()
    assert [e.id for e in again.drivers] == [d.id]


def test_add_driver_defaults_port_per_type(store):
    assert store.add_driver("nina", "h1").port == 1888
    assert store.add_driver("alpaca", "h2").port == 11111
    assert store.add_driver("phd2", "h3").port == 4400


def test_add_driver_default_label_names_type_and_host(store):
    d = store.add_driver("alpaca", "192.168.1.50")
    assert d.label == "ALPACA @ 192.168.1.50"


def test_add_driver_rejects_unknown_type_and_blank_host(store):
    with pytest.raises(ValueError):
        store.add_driver("asiair", "h")
    with pytest.raises(ValueError):
        store.add_driver("nina", "   ")


def test_update_driver_patches_and_revalidates(store):
    d = store.add_driver("alpaca", "192.168.1.50")
    u = store.update_driver(d.id, {"port": 11112, "enabled": False})
    assert (u.port, u.enabled) == (11112, False)
    with pytest.raises(ValueError):
        store.update_driver(d.id, {"port": 99999})      # out of range
    with pytest.raises(ValueError):
        store.update_driver(d.id, {"id": "evil"})       # id is immutable
    with pytest.raises(ValueError):
        store.update_driver(d.id, {"host": "  "})       # blank host


def test_update_delete_unknown_id_raise_keyerror(store):
    with pytest.raises(KeyError):
        store.update_driver("nope", {"port": 1})
    with pytest.raises(KeyError):
        store.delete_driver("nope")


def test_delete_driver_removes_entry(store):
    d = store.add_driver("phd2", "127.0.0.1")
    store.delete_driver(d.id)
    assert store.cfg().drivers == []


def test_old_config_without_drivers_key_loads(tmp_path):
    p = tmp_path / "astrodeck.json"
    p.write_text('{"version": 3}', encoding="utf-8")
    cfg = ConfigStore(p).cfg()
    assert cfg.drivers == []
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `server/`): `.venv/Scripts/python -m pytest tests/test_drivers_config.py -q`
Expected: FAIL — `AttributeError: 'ConfigStore' object has no attribute 'add_driver'` (and `AppConfig` has no `drivers`).

- [ ] **Step 3: Implement the model + CRUD**

In `server/astrodeck/config.py`:

(a) Add `import secrets` to the imports block (after `import os`, ~line 20).

(b) After the `ProvidersConfig` class (~line 257), add:

```python
# ------------------------------------------------------- backend drivers (2026-07-08)
#
# GLOBAL configured drivers (equipment-drivers spec §3.1): a driver is "how to
# reach a backend" (NINA instance, Alpaca server, PHD2), declared ONCE here and
# referenced by id from profiles/assignments. APPENDED to AppConfig (additive —
# old config files without a ``drivers`` block load fine; pydantic fills []).
# Implicit drivers (sim / astrodeck native / astap) are DETECTED, never stored.
# A DriverEntry holds NO secret (host/port/label only), so ``redacted()`` needs
# no change for it.

DriverType = Literal["nina", "alpaca", "phd2"]

#: Default port per configurable driver type (NINA Advanced API / Alpaca / PHD2).
DRIVER_DEFAULT_PORTS: dict[str, int] = {"nina": 1888, "alpaca": 11111, "phd2": 4400}


class DriverEntry(BaseModel):
    id: str                                  # server-minted "<type>-<4hex>", immutable
    type: DriverType
    host: str
    port: int = Field(ge=1, le=65535)
    enabled: bool = True
    label: str = ""
    extra: dict = Field(default_factory=dict)  # driver-typed options (e.g. phd2 managed)
```

(c) In `AppConfig` (~line 276), after the `providers` field, add:

```python
    # --- backend drivers (equipment-drivers spec; appended — old configs load fine) ---
    drivers: list[DriverEntry] = Field(default_factory=list)
```

(d) In `ConfigStore`, after `set_providers` (~line 543), add:

```python
    # -- backend drivers mutation (equipment-drivers spec §3.1) -----------------

    def add_driver(self, driver_type: str, host: str, port: int | None = None,
                   label: str = "", extra: dict | None = None) -> DriverEntry:
        """Create a configured driver with a server-minted, never-reused id.

        The id is "<type>-<4 hex>" (collision-checked against existing entries)
        so profiles can reference drivers stably. Port defaults per type; a
        blank label defaults to "<TYPE> @ <host>". Raises ``ValueError`` (→ 422
        at the API) on an unknown type or blank host."""
        if driver_type not in DRIVER_DEFAULT_PORTS:
            raise ValueError(f"unknown driver type: {driver_type!r}")
        host = (host or "").strip()
        if not host:
            raise ValueError("driver host must not be empty")
        cfg = self.cfg()
        existing = {d.id for d in cfg.drivers}
        while True:
            new_id = f"{driver_type}-{secrets.token_hex(2)}"
            if new_id not in existing:
                break
        entry = DriverEntry(
            id=new_id, type=driver_type, host=host,
            port=port if port is not None else DRIVER_DEFAULT_PORTS[driver_type],
            label=(label or "").strip() or f"{driver_type.upper()} @ {host}",
            extra=dict(extra or {}))
        cfg.drivers.append(entry)
        self.bump_and_save()
        return entry

    def update_driver(self, driver_id: str, patch: dict) -> DriverEntry:
        """Patch host/port/enabled/label/extra on one driver (id/type immutable).

        ``model_copy(update=...)`` does NOT re-validate in pydantic v2, so the
        patched entry is re-constructed through ``DriverEntry(**...)`` to run
        the field validators (port range etc). Raises ``KeyError`` for an
        unknown id (→ 404) and ``ValueError`` for a bad field/value (→ 422)."""
        allowed = {"host", "port", "enabled", "label", "extra"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"unknown driver fields: {sorted(unknown)}")
        cfg = self.cfg()
        for i, d in enumerate(cfg.drivers):
            if d.id != driver_id:
                continue
            updated = DriverEntry(**d.model_copy(update=patch).model_dump())
            if not updated.host.strip():
                raise ValueError("driver host must not be empty")
            cfg.drivers[i] = updated
            self.bump_and_save()
            return updated
        raise KeyError(driver_id)

    def delete_driver(self, driver_id: str) -> None:
        """Remove one driver. Raises ``KeyError`` for an unknown id (→ 404).
        The id is never reused (add_driver mints fresh hex); a profile still
        referencing it degrades per the spec's failure-honesty rules."""
        cfg = self.cfg()
        keep = [d for d in cfg.drivers if d.id != driver_id]
        if len(keep) == len(cfg.drivers):
            raise KeyError(driver_id)
        cfg.drivers = keep
        self.bump_and_save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_config.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Run the config-adjacent suites (regression)**

Run: `.venv/Scripts/python -m pytest tests/test_config_api.py tests/test_profiles.py -q` (if `test_config_api.py` doesn't exist, run `tests/ -k config -q`).
Expected: all pass — the added field is additive.

- [ ] **Step 6: Commit**

```bash
git -C /c/Users/bear/astro add server/astrodeck/config.py server/tests/test_drivers_config.py
git -C /c/Users/bear/astro commit -m "feat(drivers): DriverEntry config model + ConfigStore CRUD (spec §3.1)"
```

---

### Task 2: `drivers.py` probe engine (implicit detection, per-type probes, TTL cache, `describe_all`)

**Files:**
- Create: `server/astrodeck/drivers.py`
- Test: `server/tests/test_drivers_probe.py` (create)

**Interfaces:**
- Consumes: `config.DriverEntry`, `config.config_store`, `devices.backend.ROLES`, `devices.nina._detail` / `_probe_version`, `providers.NATIVE_AVAILABLE`, `solve.find_astap`.
- Produces (Task 3 relies on):
  - `async describe_all(force: bool = False) -> dict` — `{"roles": [...], "drivers": [row, ...]}`; NEVER raises.
  - `invalidate(driver_id: str | None = None) -> None`
  - `PROBE_TTL_S: float = 15.0`
  - Row shape (configured): `{id, type, label, enabled, implicit: False, host, port, status: {reachable, error, detail, probed_at}, offers: {devices: [...], tasks: [...]}}`. Implicit rows (`sim`, `astrodeck`, `astap`) same minus host/port, `implicit: True`.
  - Alpaca device offers MUST carry `dev_type` + `dev_num` (spec review finding 1).

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_drivers_probe.py`:

```python
"""Driver probe engine (equipment-drivers spec §3.2): offers mapping, the 15s
TTL cache + invalidate(), disabled short-circuit, and the never-raise contract
(a probe bug becomes a status.error row, not a 500)."""
import asyncio

import pytest

from astrodeck import drivers as drv
from astrodeck.config import ConfigStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(drv, "config_store", s)
    drv.invalidate()
    return s


def _run(coro):
    return asyncio.run(coro)


def test_describe_all_merges_configured_and_implicit(store, monkeypatch):
    store.add_driver("nina", "h1")

    async def fake_nina(host, port):
        return drv._ok([{"role": "camera", "name": "cam"}],
                       ["autofocus", "polar_align"], detail="API 2.2.2.0")

    monkeypatch.setitem(drv._PROBES, "nina", fake_nina)
    out = _run(drv.describe_all())
    assert out["roles"] == list(drv.ROLES)
    ids = [d["id"] for d in out["drivers"]]
    assert ids[-3:] == ["sim", "astrodeck", "astap"]        # implicit rows appended
    nina = out["drivers"][0]
    assert nina["implicit"] is False and nina["host"] == "h1"
    assert nina["status"]["reachable"] is True
    assert nina["status"]["detail"] == "API 2.2.2.0"
    assert nina["offers"]["devices"] == [{"role": "camera", "name": "cam"}]
    sim = out["drivers"][-3]
    assert sim["status"]["reachable"] is True               # sim is always on
    assert {"role": "camera", "name": "Simulated camera"} in sim["offers"]["devices"]


def test_probe_cached_within_ttl_forced_and_invalidated(store, monkeypatch):
    d = store.add_driver("phd2", "127.0.0.1")
    calls = {"n": 0}

    async def fake(host, port):
        calls["n"] += 1
        return drv._ok([{"role": "guider", "name": "PHD2"}], [])

    monkeypatch.setitem(drv._PROBES, "phd2", fake)
    _run(drv.describe_all())
    _run(drv.describe_all())
    assert calls["n"] == 1                  # second read served from cache
    _run(drv.describe_all(force=True))
    assert calls["n"] == 2                  # force bypasses the TTL
    drv.invalidate(d.id)
    _run(drv.describe_all())
    assert calls["n"] == 3                  # invalidate drops the entry


def test_disabled_driver_reports_disabled_without_probing(store, monkeypatch):
    d = store.add_driver("nina", "h")
    store.update_driver(d.id, {"enabled": False})

    async def boom(host, port):
        raise AssertionError("must not probe a disabled driver")

    monkeypatch.setitem(drv._PROBES, "nina", boom)
    out = _run(drv.describe_all())
    row = out["drivers"][0]
    assert row["status"]["reachable"] is False
    assert row["status"]["error"] == "disabled"
    assert row["offers"] == {"devices": [], "tasks": []}


def test_probe_exception_becomes_error_row(store, monkeypatch):
    store.add_driver("alpaca", "h")

    async def boom(host, port):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(drv._PROBES, "alpaca", boom)
    out = _run(drv.describe_all())
    row = out["drivers"][0]
    assert row["status"]["reachable"] is False
    assert "kaboom" in row["status"]["error"]


def test_alpaca_offer_carries_dev_type_and_dev_num(store, monkeypatch):
    """Review finding 1: Alpaca offers MUST carry the ConnSpec addressing
    (dev_type + dev_num); unmapped DeviceTypes (rotator, until the role exists)
    are skipped, not errors."""
    store.add_driver("alpaca", "h")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"Value": [
                {"DeviceType": "Camera", "DeviceNumber": 0, "DeviceName": "ASI2600MM"},
                {"DeviceType": "Rotator", "DeviceNumber": 0, "DeviceName": "ZWO CAA"},
            ]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    out = _run(drv.describe_all())
    devs = out["drivers"][0]["offers"]["devices"]
    assert devs == [{"role": "camera", "name": "ASI2600MM",
                     "dev_type": "camera", "dev_num": 0}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_probe.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'astrodeck.drivers'` (import error at collection).

- [ ] **Step 3: Implement `server/astrodeck/drivers.py`**

```python
"""Driver registry surface — configured + implicit drivers, availability
probes, and the offers mapping the Equipment/Settings UI renders from
(equipment-drivers spec 2026-07-08 §3.2).

Configured drivers (nina/alpaca/phd2) live in config (``AppConfig.drivers``);
implicit drivers (sim / astrodeck native engine / ASTAP) are DETECTED, not
stored. ``describe_all()`` merges both, probing configured drivers
concurrently, and NEVER raises — a probe failure (or bug) becomes a
``status.error`` row, so the API surface can't 500.

Probe results are cached ``PROBE_TTL_S`` per driver id. ``invalidate()`` drops
the cache; Phase 2 wires it to rig-connect failures (the spec's cache-honesty
rule: a failed connect must not leave a stale "reachable" row for up to 15 s).

Offers contract (spec review finding 1): every ``offers.devices`` entry
carries EVERYTHING the client needs to build that driver's ConnSpec/device
selector — Alpaca entries MUST include ``dev_type`` + ``dev_num``; nina/phd2/
sim entries need only ``role`` + ``name``. The UI never synthesizes addressing.
"""
from __future__ import annotations

import asyncio
import time

from .config import DriverEntry, config_store
from .devices.backend import ROLES

PROBE_TTL_S = 15.0

#: driver_id -> (monotonic stamp, probe result). Module-level cache, cleared by
#: ``invalidate()``. Monotonic for the TTL math; wall-clock ``probed_at`` rides
#: inside the result for the UI.
_CACHE: dict[str, tuple[float, dict]] = {}


def invalidate(driver_id: str | None = None) -> None:
    """Drop cached probe results (one driver, or ALL when None)."""
    if driver_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(driver_id, None)


# ------------------------------------------------------------------- helpers

def _ok(devices: list[dict], tasks: list[str], detail: str | None = None) -> dict:
    return {"reachable": True, "error": None, "detail": detail,
            "offers": {"devices": devices, "tasks": tasks}}


def _down(error: str) -> dict:
    return {"reachable": False, "error": error, "detail": None,
            "offers": {"devices": [], "tasks": []}}


# -------------------------------------------------------------------- probes

async def _probe_nina(host: str, port: int) -> dict:
    """NINA Advanced API probe: version check, then per-role equipment info.
    Reuses devices.nina's probe helpers so the enumeration logic lives once."""
    import httpx

    from .devices.nina import _detail, _probe_version
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            ver = await _probe_version(c, host, port)
            if ver is None:
                return _down(f"no NINA Advanced API at {host}:{port}")
            d = await _detail(c, host, port)
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return _down(str(e)[:200])
    devices = [{"role": role, "name": name}
               for role, name in sorted(d["devices"].items())]
    # NINA offers its own autofocus + TPPA plugin as task providers (spec §3.2).
    return _ok(devices, ["autofocus", "polar_align"], detail=f"API {ver}")


#: Alpaca DeviceType (lowercased) -> AstroDeck role. Inverse of the native
#: backend's ``_ROLE_TO_DEV_TYPE``. Unmapped types (e.g. ``rotator`` until that
#: role exists) are SKIPPED — the CAA spec adds the mapping, not an error here.
_DEV_TYPE_TO_ROLE: dict[str, str] = {
    "camera": "camera", "telescope": "telescope", "focuser": "focuser",
    "filterwheel": "filterwheel", "switch": "switch",
    "safetymonitor": "safety",
}


async def _probe_alpaca(host: str, port: int) -> dict:
    """Alpaca management-API probe: enumerate configured devices. Every entry
    carries dev_type + dev_num (review finding 1 — the ConnSpec addressing)."""
    import httpx
    url = f"http://{host}:{port}/management/v1/configureddevices"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            value = r.json().get("Value", [])
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return _down(str(e)[:200])
    devices: list[dict] = []
    for d in value if isinstance(value, list) else []:
        dev_type = str(d.get("DeviceType", "")).lower()
        role = _DEV_TYPE_TO_ROLE.get(dev_type)
        if role is None:
            continue
        devices.append({"role": role,
                        "name": d.get("DeviceName") or dev_type,
                        "dev_type": dev_type,
                        "dev_num": int(d.get("DeviceNumber", 0))})
    return _ok(devices, [])


async def _probe_phd2(host: str, port: int) -> dict:
    """PHD2 probe: a plain TCP connect to its event socket (default 4400)."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0)
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return _down(str(e)[:200] or "connection failed")
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 — best-effort close
        pass
    return _ok([{"role": "guider", "name": f"PHD2 @ {host}:{port}"}], [])


#: type -> probe coroutine. A dict (not if/elif) so tests can monkeypatch one
#: probe without touching the others.
_PROBES = {"nina": _probe_nina, "alpaca": _probe_alpaca, "phd2": _probe_phd2}


# ------------------------------------------------------------------ implicit

def _implicit_rows() -> list[dict]:
    """The detected (non-configured) drivers, fixed ids ``sim`` / ``astrodeck``
    / ``astap`` (spec §3.1): always appended after the configured rows so the
    UI can render "built-ins" under the user's own drivers."""
    from .providers import NATIVE_AVAILABLE
    from .solve import find_astap

    now = time.time()
    rows: list[dict] = [{
        "id": "sim", "type": "sim", "label": "Simulator", "enabled": True,
        "implicit": True,
        "status": {"reachable": True, "error": None, "detail": None,
                   "probed_at": now},
        "offers": {"devices": [{"role": r, "name": f"Simulated {r}"}
                                for r in ROLES],
                   "tasks": ["autofocus", "polar_align"]},
    }]

    if NATIVE_AVAILABLE:
        import astrodeck_native
        detail = str(getattr(astrodeck_native, "__version__", "installed"))
        status = {"reachable": True, "error": None, "detail": detail,
                  "probed_at": now}
        tasks = ["autofocus", "polar_align"]
    else:
        status = {"reachable": False, "detail": None, "probed_at": now,
                  "error": "astrodeck_native wheel not installed"}
        tasks = []
    rows.append({"id": "astrodeck", "type": "astrodeck",
                 "label": "AstroDeck native", "enabled": True, "implicit": True,
                 "status": status, "offers": {"devices": [], "tasks": tasks}})

    astap = find_astap()
    if astap:
        status = {"reachable": True, "error": None, "detail": str(astap),
                  "probed_at": now}
        tasks = ["solve"]
    else:
        status = {"reachable": False, "detail": None, "probed_at": now,
                  "error": "ASTAP binary not found"}
        tasks = []
    rows.append({"id": "astap", "type": "astap", "label": "ASTAP",
                 "enabled": True, "implicit": True,
                 "status": status, "offers": {"devices": [], "tasks": tasks}})
    return rows


# --------------------------------------------------------------- describe_all

async def _probe_configured(entry: DriverEntry, force: bool) -> dict:
    row: dict = {"id": entry.id, "type": entry.type, "label": entry.label,
                 "enabled": entry.enabled, "implicit": False,
                 "host": entry.host, "port": entry.port}
    if not entry.enabled:
        # Disabled is a USER state, not a network state — short-circuit, never
        # probe, and report it verbatim so the UI can badge "disabled".
        row["status"] = {"reachable": False, "error": "disabled",
                         "detail": None, "probed_at": time.time()}
        row["offers"] = {"devices": [], "tasks": []}
        return row
    hit = _CACHE.get(entry.id)
    if not force and hit is not None and (time.monotonic() - hit[0]) < PROBE_TTL_S:
        res = hit[1]
    else:
        res = await _PROBES[entry.type](entry.host, entry.port)
        res["probed_at"] = time.time()
        _CACHE[entry.id] = (time.monotonic(), res)
    row["status"] = {"reachable": res["reachable"], "error": res["error"],
                     "detail": res.get("detail"),
                     "probed_at": res["probed_at"]}
    row["offers"] = res["offers"]
    return row


async def describe_all(force: bool = False) -> dict:
    """The full option space the UI renders from: ``{roles, drivers}``
    (spec §3.2). ``roles`` comes from ``devices.backend.ROLES`` so a future
    role (rotator) appears with zero UI changes. NEVER raises — a probe bug
    degrades to a status.error row (defensive gather)."""
    entries = list(config_store.cfg().drivers)
    probed = await asyncio.gather(
        *(_probe_configured(e, force) for e in entries),
        return_exceptions=True)
    rows: list[dict] = []
    for e, r in zip(entries, probed):
        if isinstance(r, BaseException):
            rows.append({"id": e.id, "type": e.type, "label": e.label,
                         "enabled": e.enabled, "implicit": False,
                         "host": e.host, "port": e.port,
                         "status": {"reachable": False,
                                    "error": f"probe error: {r}",
                                    "detail": None, "probed_at": time.time()},
                         "offers": {"devices": [], "tasks": []}})
        else:
            rows.append(r)
    return {"roles": list(ROLES), "drivers": rows + _implicit_rows()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_probe.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /c/Users/bear/astro add server/astrodeck/drivers.py server/tests/test_drivers_probe.py
git -C /c/Users/bear/astro commit -m "feat(drivers): probe engine — implicit detection, offers mapping, TTL cache (spec §3.2)"
```

---

### Task 3: API routes — `GET /api/drivers`, probe, CRUD

**Files:**
- Modify: `server/astrodeck/api/app.py` (body models near the other `*Body` classes; routes in the "equipment" section directly after `GET /api/backends`, ~line 733)
- Test: `server/tests/test_drivers_api.py` (create)

**Interfaces:**
- Consumes: Task 1 CRUD, Task 2 `describe_all`/`invalidate`, existing `require`/`declare`/`config_store`/`HTTPException`/`_config_payload()`.
- Produces (Task 4/5 client relies on):
  - `GET /api/drivers` (view.status) → `describe_all()` shape
  - `POST /api/drivers/{driver_id}/probe` (view.status) → 404 unknown id; else `invalidate(id)` + `describe_all()`
  - `POST /api/config/drivers` (config.backend) body `{type, host, port?, label?, extra?}` → `{"driver": {...}, "config": <payload>}`; 422 on ValueError
  - `PATCH /api/config/drivers/{driver_id}` (config.backend) body `{host?, port?, enabled?, label?, extra?}` → same shape; 404/422
  - `DELETE /api/config/drivers/{driver_id}` (config.backend) → `{"deleted": id, "config": <payload>}`; 404

**Before coding:** read the existing `set_providers_config` route (~line 679) and `GET /api/backends` (~line 723) — the new routes copy their exact idioms (`asyncio.to_thread` for store writes, `ValueError → HTTPException(422)`, deferred imports, `_config_payload()` echo).

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_drivers_api.py`. First read `server/tests/test_connect_api.py` lines 1–60 and reuse its client fixture idiom (`app_module.create_app()` + `TestClient`); the drivers version additionally points the config singleton at a temp file and clears the probe cache:

```python
"""Driver API routes (equipment-drivers spec §3.1/§3.2): CRUD + probe + the
merged describe surface. In-process via TestClient against a temp ConfigStore
(repo convention — see test_connect_api.py)."""
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


def test_drivers_crud_roundtrip(client):
    r = client.post("/api/config/drivers",
                    json={"type": "nina", "host": "astrotown.lan"})
    assert r.status_code == 200
    d = r.json()["driver"]
    assert d["id"].startswith("nina-") and d["port"] == 1888

    r = client.patch(f"/api/config/drivers/{d['id']}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["driver"]["enabled"] is False

    r = client.delete(f"/api/config/drivers/{d['id']}")
    assert r.status_code == 200 and r.json()["deleted"] == d["id"]


def test_drivers_validation_and_404(client):
    assert client.post("/api/config/drivers",
                       json={"type": "asiair", "host": "h"}).status_code == 422
    assert client.post("/api/config/drivers",
                       json={"type": "nina", "host": "  "}).status_code == 422
    assert client.patch("/api/config/drivers/nope",
                        json={"port": 2}).status_code == 404
    assert client.delete("/api/config/drivers/nope").status_code == 404
    assert client.post("/api/drivers/nope/probe").status_code == 404


def test_get_drivers_returns_roles_and_implicit_rows(client):
    r = client.get("/api/drivers")
    assert r.status_code == 200
    body = r.json()
    assert "camera" in body["roles"]
    ids = {d["id"] for d in body["drivers"]}
    assert {"sim", "astrodeck", "astap"} <= ids
    sim = next(d for d in body["drivers"] if d["id"] == "sim")
    assert sim["implicit"] is True and sim["status"]["reachable"] is True


def test_probe_route_accepts_implicit_ids(client):
    assert client.post("/api/drivers/sim/probe").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_api.py -q`
Expected: FAIL — `404` on every new route (routes don't exist yet).

- [ ] **Step 3: Implement the routes**

In `server/astrodeck/api/app.py`, next to the other request-body models add:

```python
class DriverCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    host: str
    port: int | None = None
    label: str = ""
    extra: dict = {}


class DriverPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = None
    port: int | None = None
    enabled: bool | None = None
    label: str | None = None
    extra: dict | None = None
```

Directly after the `GET /api/backends` route (~line 733), add:

```python
    # -------------------------------------------- backend drivers (spec 2026-07-08)
    # GLOBAL driver config + the merged availability surface. Read = view.status
    # (same as discovery); write = config.backend (same as every connect write).
    # describe_all() never raises, so GET /api/drivers can't 500.

    @app.get("/api/drivers", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_drivers():
        from .. import drivers as drivers_mod
        return await drivers_mod.describe_all()

    @app.post("/api/drivers/{driver_id}/probe",
              dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def probe_driver(driver_id: str):
        """Force ONE driver's re-probe (bypasses the 15s cache). Implicit ids
        (sim/astrodeck/astap) are accepted — they recompute on every describe."""
        from .. import drivers as drivers_mod
        known = ({d.id for d in config_store.cfg().drivers}
                 | {"sim", "astrodeck", "astap"})
        if driver_id not in known:
            raise HTTPException(404, "unknown driver")
        drivers_mod.invalidate(driver_id)
        return await drivers_mod.describe_all()

    @app.post("/api/config/drivers",
              dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def add_driver(body: DriverCreateBody):
        try:
            entry = await asyncio.to_thread(
                config_store.add_driver, body.type, body.host, body.port,
                body.label, body.extra)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"driver": entry.model_dump(), "config": _config_payload()}

    @app.patch("/api/config/drivers/{driver_id}",
               dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def patch_driver(driver_id: str, body: DriverPatchBody):
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            entry = await asyncio.to_thread(
                config_store.update_driver, driver_id, patch)
        except KeyError:
            raise HTTPException(404, "unknown driver")
        except ValueError as e:
            raise HTTPException(422, str(e))
        from .. import drivers as drivers_mod
        drivers_mod.invalidate(driver_id)      # addressing may have changed
        return {"driver": entry.model_dump(), "config": _config_payload()}

    @app.delete("/api/config/drivers/{driver_id}",
                dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def delete_driver(driver_id: str):
        try:
            await asyncio.to_thread(config_store.delete_driver, driver_id)
        except KeyError:
            raise HTTPException(404, "unknown driver")
        from .. import drivers as drivers_mod
        drivers_mod.invalidate(driver_id)
        return {"deleted": driver_id, "config": _config_payload()}
```

If `_config_payload` does not exist under that exact name, use whatever helper `set_providers_config` returns and keep the `{"driver": ..., "config": ...}` envelope.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_api.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Run the route-contract + full server suite**

Run: `.venv/Scripts/python -m pytest tests/test_backend_roles_match_served.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: all pass (11 native skips OK). The roles-match test proves the new routes declare their caps correctly.

- [ ] **Step 6: Commit**

```bash
git -C /c/Users/bear/astro add server/astrodeck/api/app.py server/tests/test_drivers_api.py
git -C /c/Users/bear/astro commit -m "feat(drivers): API — GET /api/drivers, per-driver probe, config CRUD (spec §3.2)"
```

---

### Task 4: UI types + API client + pure helpers (with tsx tests)

**Files:**
- Modify: `ui/src/types.ts` (new section after `ProvidersConfig` ~line 488; add one field to `interface AppConfig`)
- Modify: `ui/src/api/backends.ts` (new section at the end)
- Create: `ui/src/components/settings/driversMeta.ts`
- Test: `ui/src/lib/__tests__/drivers.test.ts` (create; inline-assert harness run via `npx tsx` — copy the harness shape from `ui/src/lib/__tests__/backends.test.ts`, NOT vitest)

**Interfaces:**
- Consumes: Task 3 route shapes; existing `api` wrapper (`api.get/post/patch/del`).
- Produces (Task 5 relies on):
  - Types: `DriverType`, `DriverEntry`, `DriverDeviceOffer`, `DriverStatus`, `DriverInfo`, `DriversResponse`
  - Client: `listDrivers(): Promise<DriversResponse>`, `probeDriver(id)`, `addDriver(body)`, `updateDriver(id, patch)`, `deleteDriver(id)`
  - Helpers: `DRIVER_TYPE_LABEL`, `DRIVER_DEFAULT_PORT`, `offersSummary(d: DriverInfo): string`, `validateDriverForm(type, host, port): string | null`

- [ ] **Step 1: Add the types**

In `ui/src/types.ts`, after the `ProvidersConfig` interface (~line 488):

```ts
// ------------------------------------------------------------- backend drivers
// Mirrors server/astrodeck/drivers.py describe_all() + config.py DriverEntry
// (equipment-drivers spec 2026-07-08 §3.1/§3.2). DriverEntry is the CONFIG
// (write) side; DriverInfo is the READ side (probe status + offers) the
// Equipment/Settings surfaces render from.
export type DriverType = "nina" | "alpaca" | "phd2" | "sim" | "astrodeck" | "astap";

export interface DriverEntry {
  id: string;            // server-minted "<type>-<4hex>", immutable
  type: DriverType;      // configured entries are only nina|alpaca|phd2
  host: string;
  port: number;
  enabled: boolean;
  label: string;
  extra: Record<string, unknown>;
}

export interface DriverDeviceOffer {
  role: string;
  name: string;
  dev_type?: string;     // Alpaca only — ALWAYS present there (ConnSpec addressing)
  dev_num?: number;      // Alpaca only
}

export interface DriverStatus {
  reachable: boolean;
  error: string | null;
  detail: string | null; // NINA API version / ASTAP path / native wheel version
  probed_at: number;     // unix seconds
}

export interface DriverInfo {
  id: string;
  type: DriverType;
  label: string;
  enabled: boolean;
  implicit: boolean;     // sim/astrodeck/astap = detected built-ins, not stored
  host?: string;
  port?: number;
  status: DriverStatus;
  offers: { devices: DriverDeviceOffer[]; tasks: string[] };
}

export interface DriversResponse {
  roles: string[];       // fed from devices/backend.py ROLES — a new role appears free
  drivers: DriverInfo[];
}
```

In `interface AppConfig` (find `providers?: ProvidersConfig;` ~line 475), add directly below:

```ts
  // --- backend drivers (equipment-drivers spec; global, never per-profile) ---
  drivers?: DriverEntry[];
```

- [ ] **Step 2: Add the client functions**

At the end of `ui/src/api/backends.ts` (import the new types in the existing `import type` block: `DriverEntry`, `DriversResponse`):

```ts
// ------------------------------------------------------------ backend drivers
/** GET /api/drivers → configured + implicit drivers with probe status + offers
 *  (spec 2026-07-08 §3.2). Never 500s: failures land in each row's status. */
export const listDrivers = (): Promise<DriversResponse> =>
  api.get<DriversResponse>("/api/drivers");

/** POST /api/drivers/{id}/probe → force ONE driver's re-probe (bypass the 15s
 *  TTL); returns the full refreshed DriversResponse. 404 unknown id. */
export const probeDriver = (id: string): Promise<DriversResponse> =>
  api.post<DriversResponse>(`/api/drivers/${encodeURIComponent(id)}/probe`);

/** POST /api/config/drivers → create (server mints the id; port defaults per
 *  type). 422 unknown type / blank host. config.backend-gated. */
export const addDriver = (body: {
  type: "nina" | "alpaca" | "phd2";
  host: string;
  port?: number;
  label?: string;
  extra?: Record<string, unknown>;
}): Promise<{ driver: DriverEntry }> =>
  api.post<{ driver: DriverEntry }>("/api/config/drivers", body);

/** PATCH /api/config/drivers/{id} → patch host/port/enabled/label/extra
 *  (id/type immutable). 404 unknown, 422 invalid. */
export const updateDriver = (
  id: string,
  patch: Partial<Pick<DriverEntry, "host" | "port" | "enabled" | "label" | "extra">>,
): Promise<{ driver: DriverEntry }> =>
  api.patch<{ driver: DriverEntry }>(
    `/api/config/drivers/${encodeURIComponent(id)}`, patch);

/** DELETE /api/config/drivers/{id} → {deleted:id}. 404 unknown. */
export const deleteDriver = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/config/drivers/${encodeURIComponent(id)}`);
```

- [ ] **Step 3: Write the pure helpers**

Create `ui/src/components/settings/driversMeta.ts`:

```ts
// driversMeta.ts — pure helpers for the Backend Drivers settings panel
// (equipment-drivers spec §4.2). Kept out of the component so the inline-assert
// test harness (npx tsx, no DOM) can exercise them directly.
import type { DriverInfo } from "../../types";

export const DRIVER_TYPE_LABEL: Record<string, string> = {
  nina: "NINA",
  alpaca: "Alpaca server",
  phd2: "PHD2",
  sim: "Simulator",
  astrodeck: "AstroDeck native",
  astap: "ASTAP",
};

export const DRIVER_DEFAULT_PORT: Record<string, number> = {
  nina: 1888,
  alpaca: 11111,
  phd2: 4400,
};

/** One-line human summary of what a driver offers right now — the Settings
 *  "offers" readout ("camera: ASI2600MM · telescope: EQ6-R · tasks: autofocus"). */
export function offersSummary(d: DriverInfo): string {
  const parts = d.offers.devices.map((o) => `${o.role}: ${o.name}`);
  if (d.offers.tasks.length) parts.push(`tasks: ${d.offers.tasks.join(", ")}`);
  return parts.length ? parts.join(" · ") : "nothing offered";
}

/** Validate the add/edit driver form. Returns an error string, or null when
 *  valid. Port may be blank (server defaults it per type). */
export function validateDriverForm(
  type: string,
  host: string,
  port: string,
): string | null {
  if (!(type in DRIVER_DEFAULT_PORT)) return "unknown driver type";
  if (!host.trim()) return "host is required";
  if (port.trim() !== "") {
    const p = Number(port);
    if (!Number.isInteger(p) || p < 1 || p > 65535) return "port must be 1-65535";
  }
  return null;
}
```

- [ ] **Step 4: Write the tsx test**

Create `ui/src/lib/__tests__/drivers.test.ts` (same self-contained harness as `backends.test.ts` — `test()`/`eq()` helpers, counters, summary print, `process.exit(1)` on failure):

```ts
// drivers.test.ts — pure-logic tests for the Backend Drivers lane (spec §4.2).
// Inline-assert harness (no vitest in this repo); runs via `npx tsx`.
import {
  DRIVER_DEFAULT_PORT,
  offersSummary,
  validateDriverForm,
} from "../../components/settings/driversMeta";
import type { DriverInfo } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const info = (over: Partial<DriverInfo>): DriverInfo => ({
  id: "nina-abcd", type: "nina", label: "NINA", enabled: true, implicit: false,
  status: { reachable: true, error: null, detail: null, probed_at: 0 },
  offers: { devices: [], tasks: [] },
  ...over,
});

test("offersSummary lists devices then tasks", () => {
  const d = info({
    offers: {
      devices: [
        { role: "camera", name: "ASI2600MM" },
        { role: "telescope", name: "EQ6-R" },
      ],
      tasks: ["autofocus", "polar_align"],
    },
  });
  eq(offersSummary(d),
     "camera: ASI2600MM · telescope: EQ6-R · tasks: autofocus, polar_align");
});

test("offersSummary empty => 'nothing offered'", () => {
  eq(offersSummary(info({})), "nothing offered");
});

test("validateDriverForm accepts blank port (server defaults it)", () => {
  eq(validateDriverForm("nina", "astrotown.lan", ""), null);
});

test("validateDriverForm rejects bad type / blank host / bad port", () => {
  eq(validateDriverForm("asiair", "h", "") !== null, true);
  eq(validateDriverForm("nina", "  ", "") !== null, true);
  eq(validateDriverForm("nina", "h", "0") !== null, true);
  eq(validateDriverForm("nina", "h", "99999") !== null, true);
  eq(validateDriverForm("nina", "h", "abc") !== null, true);
});

test("default ports match the server table", () => {
  eq(DRIVER_DEFAULT_PORT.nina, 1888);
  eq(DRIVER_DEFAULT_PORT.alpaca, 11111);
  eq(DRIVER_DEFAULT_PORT.phd2, 4400);
});

console.log(`drivers.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exit(1);
}
```

- [ ] **Step 5: Run the test + typecheck**

Run: `cd /c/Users/bear/astro/ui && npx tsx src/lib/__tests__/drivers.test.ts`
Expected: `drivers.test.ts: 5 passed, 0 failed`

Run: `npm --prefix /c/Users/bear/astro/ui run build`
Expected: `tsc -b` clean + `✓ built in …ms`

- [ ] **Step 6: Commit**

```bash
git -C /c/Users/bear/astro add ui/src/types.ts ui/src/api/backends.ts ui/src/components/settings/driversMeta.ts ui/src/lib/__tests__/drivers.test.ts
git -C /c/Users/bear/astro commit -m "feat(ui/drivers): driver types, API client, pure form/offers helpers (spec §4.2)"
```

---

### Task 5: Settings → Backend Drivers panel

**Files:**
- Create: `ui/src/components/settings/DriversPanel.tsx`
- Modify: `ui/src/components/settings/SettingsView.tsx` (import ~line 26; render directly ABOVE `<BackendPicker />` ~line 149)

**Interfaces:**
- Consumes: Task 4 types/client/helpers; house components `Panel, Field, Led, Toggle, EmptyState, InfoDot` from `../ui`, `Icon` from `../icons`, `confirmDialog` from `../ConfirmDialog`, `useStore` (showToast), `useCanConfigBackend` from `../../lib/caps`, `discoverBackend` from `../../api/backends`, `DiscoveredNina`/`DiscoveredAlpaca` types from `./backendMeta`.
- Produces: `export default function DriversPanel(): JSX.Element` — no props (self-fetching, like `BackendPicker`).

**Before coding:** read `BackendPicker.tsx` (same directory) for the exact house idioms: `Led state=`, `btn`/`field`/`label` classes, discovery result rendering, toast/error handling. Match them.

- [ ] **Step 1: Write the component**

Create `ui/src/components/settings/DriversPanel.tsx`:

```tsx
// DriversPanel.tsx — Settings → "Backend Drivers" (equipment-drivers spec §4.2).
// The ONE place backends are declared: add/edit/enable/probe/delete configured
// drivers (NINA / Alpaca / PHD2), see what each currently OFFERS (per-role
// devices + tasks), and see the implicit built-ins (Simulator / AstroDeck
// native / ASTAP) with their detection state. Per-device ASSIGNMENT lives on
// the Equipment tab (Phase 2) — this panel is drivers only.
//
// Data source: GET /api/drivers (probe status + offers, 15s server cache);
// probeDriver(id) forces a refresh. Writes are config.backend-gated — without
// the cap the panel renders read-only (same pattern as CapabilitiesCard).
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, DriversResponse } from "../../types";
import {
  addDriver,
  deleteDriver,
  discoverBackend,
  listDrivers,
  probeDriver,
  updateDriver,
} from "../../api/backends";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { useCanConfigBackend } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
import { EmptyState, Field, InfoDot, Led, Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import type { DiscoveredAlpaca, DiscoveredNina } from "./backendMeta";
import {
  DRIVER_DEFAULT_PORT,
  DRIVER_TYPE_LABEL,
  offersSummary,
  validateDriverForm,
} from "./driversMeta";

type AddForm = { type: "nina" | "alpaca" | "phd2"; host: string; port: string; label: string };
const emptyForm = (): AddForm => ({ type: "nina", host: "", port: "", label: "" });

export default function DriversPanel(): JSX.Element {
  const showToast = useStore((s) => s.showToast);
  const canConfig = useCanConfigBackend();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<AddForm>(emptyForm());
  const [formErr, setFormErr] = useState<string | null>(null);
  // discovery substate for the add form (nina/alpaca only)
  const [scanning, setScanning] = useState(false);
  const [found, setFound] = useState<DiscoveredNina[] | DiscoveredAlpaca[] | null>(null);

  const reload = async () => {
    try {
      setData(await listDrivers());
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
    }
  };
  useEffect(() => {
    void reload();
  }, []);

  const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      await reload();
      if (okMsg) showToast("success", okMsg);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.backend required to change drivers"
            : e.message
          : e instanceof Error
            ? e.message
            : "driver operation failed";
      showToast("error", msg);
    } finally {
      setBusy(false);
    }
  };

  const submitAdd = () => {
    const err = validateDriverForm(form.type, form.host, form.port);
    setFormErr(err);
    if (err) return;
    void run(async () => {
      await addDriver({
        type: form.type,
        host: form.host.trim(),
        port: form.port.trim() === "" ? undefined : Number(form.port),
        label: form.label.trim() || undefined,
      });
      setForm(emptyForm());
      setFound(null);
    }, "driver added");
  };

  const scan = async () => {
    // Registry names: the Alpaca lane is backend "native"; NINA is "nina".
    const backend = form.type === "alpaca" ? "native" : "nina";
    setScanning(true);
    setFound(null);
    try {
      setFound((await discoverBackend(backend)) as DiscoveredNina[] | DiscoveredAlpaca[]);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "discovery failed");
    } finally {
      setScanning(false);
    }
  };

  const pick = (host: string, port: number) => {
    setForm((f) => ({ ...f, host, port: String(port) }));
    setFound(null);
  };

  const remove = (d: DriverInfo) =>
    void (async () => {
      const ok = await confirmDialog({
        title: `Delete ${d.label}?`,
        body: "Profiles referencing this driver will show 'driver removed' until reassigned.",
        tone: "danger",
        confirmLabel: "Delete driver",
      });
      if (ok) void run(() => deleteDriver(d.id), "driver deleted");
    })();

  if (loadErr) {
    return (
      <Panel title="Backend Drivers">
        <EmptyState icon="alert" title="Couldn't load drivers" hint={loadErr} />
      </Panel>
    );
  }

  const configured = data?.drivers.filter((d) => !d.implicit) ?? [];
  const implicit = data?.drivers.filter((d) => d.implicit) ?? [];

  return (
    <Panel
      title="Backend Drivers"
      right={
        <InfoDot
          label="About backend drivers"
          content="Declare how AstroDeck reaches your equipment backends — a NINA instance, Alpaca servers, PHD2 — once, globally. Each driver is probed for what it currently offers; per-device assignment happens on the Equipment surface."
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        {/* ------------------------------------------------ configured drivers */}
        {configured.length === 0 && (
          <p className="text-xs text-dim">
            No drivers configured yet — add your NINA instance, Alpaca servers or
            PHD2 below. The built-ins (Simulator, native engine, ASTAP) are always
            available.
          </p>
        )}
        {configured.map((d) => (
          <DriverRow
            key={d.id}
            d={d}
            busy={busy}
            canConfig={canConfig}
            onToggle={(en) => void run(() => updateDriver(d.id, { enabled: en }))}
            onProbe={() => void run(() => probeDriver(d.id))}
            onDelete={() => remove(d)}
          />
        ))}

        {/* ------------------------------------------------------- add driver */}
        {canConfig && (
          <div className="border border-line bg-bg/60 px-3 py-2.5">
            <div className="label mb-2">Add driver</div>
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Type">
                <select
                  className="field !py-1"
                  value={form.type}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, type: e.target.value as AddForm["type"] }))
                  }
                >
                  <option value="nina">NINA</option>
                  <option value="alpaca">Alpaca server</option>
                  <option value="phd2">PHD2</option>
                </select>
              </Field>
              <Field label="Host">
                <input
                  className="field !py-1 w-[150px]"
                  placeholder={form.type === "nina" ? "astrotown.lan" : "192.168.1.50"}
                  value={form.host}
                  onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                />
              </Field>
              <Field label="Port">
                <input
                  className="field !py-1 w-[78px]"
                  placeholder={String(DRIVER_DEFAULT_PORT[form.type])}
                  value={form.port}
                  onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                />
              </Field>
              <Field label="Label (optional)">
                <input
                  className="field !py-1 w-[160px]"
                  value={form.label}
                  onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                />
              </Field>
              <button type="button" className="btn btn-accent !py-1.5" disabled={busy} onClick={submitAdd}>
                <Icon name="plus" size={12} className="inline -mt-0.5 mr-1" />
                Add
              </button>
              {form.type !== "phd2" && (
                <button type="button" className="btn !py-1.5" disabled={scanning} onClick={() => void scan()}>
                  {scanning ? "Scanning…" : "⟳ Scan network"}
                </button>
              )}
            </div>
            {formErr && <p className="text-[11px] text-bad mt-1.5">{formErr}</p>}
            {found !== null && (
              <div className="mt-2 flex flex-col gap-1">
                {found.length === 0 && (
                  <p className="text-[10px] text-dim">nothing found on this network</p>
                )}
                {form.type === "nina"
                  ? (found as DiscoveredNina[]).map((i) => (
                      <button
                        key={i.url}
                        type="button"
                        className="btn !normal-case !tracking-normal !font-sans !py-1.5 text-left flex justify-between items-center"
                        onClick={() => pick(i.host, i.port)}
                      >
                        <span className="mono text-[11px] truncate">
                          {i.hostname ?? i.host}:{i.port}
                        </span>
                        <span className="label">
                          {i.nina_version ? `NINA ${i.nina_version}` : "use"}
                        </span>
                      </button>
                    ))
                  : (found as DiscoveredAlpaca[]).map((s) => (
                      <button
                        key={`${s.address}:${s.port}`}
                        type="button"
                        className="btn !normal-case !tracking-normal !font-sans !py-1.5 text-left flex justify-between items-center"
                        onClick={() => pick(s.address, s.port)}
                      >
                        <span className="mono text-[11px] truncate">
                          {s.address}:{s.port}
                        </span>
                        <span className="label">{s.devices.length} device(s)</span>
                      </button>
                    ))}
              </div>
            )}
          </div>
        )}

        {/* --------------------------------------------------------- built-ins */}
        <div className="label mt-1">Built-in</div>
        {implicit.map((d) => (
          <div key={d.id} className="flex items-start gap-3 border border-line bg-bg/60 px-3 py-2.5">
            <Led state={d.status.reachable ? "on" : "off"} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-ink">{d.label}</span>
                <span className="mono text-[10px] text-dim">{DRIVER_TYPE_LABEL[d.type] ?? d.type}</span>
                {d.status.detail && (
                  <span className="mono text-[10px] text-dim truncate">{d.status.detail}</span>
                )}
              </div>
              <p className="text-[11px] text-dim mt-0.5 leading-snug truncate">
                {d.status.reachable ? offersSummary(d) : d.status.error}
              </p>
            </div>
          </div>
        ))}

        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing drivers needs operator or admin access.
          </p>
        )}
      </div>
    </Panel>
  );
}

// One configured-driver row: LED + label + addressing + offers line + controls.
function DriverRow({
  d,
  busy,
  canConfig,
  onToggle,
  onProbe,
  onDelete,
}: {
  d: DriverInfo;
  busy: boolean;
  canConfig: boolean;
  onToggle: (enabled: boolean) => void;
  onProbe: () => void;
  onDelete: () => void;
}): JSX.Element {
  const led = !d.enabled ? "off" : d.status.reachable ? "on" : "bad";
  return (
    <div className="border border-line bg-bg/60 px-3 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <Led state={led} label={`${d.label} status`} />
        <div className="min-w-0">
          <span className="text-sm text-ink">{d.label}</span>{" "}
          <span className="mono text-[10px] text-dim">
            {DRIVER_TYPE_LABEL[d.type] ?? d.type} · {d.host}:{d.port}
          </span>
        </div>
        <div className="flex-1" />
        <Toggle checked={d.enabled} disabled={!canConfig || busy} onChange={onToggle} label={`${d.label} enabled`} showState />
        <button type="button" className="btn !py-1 !px-2 text-[10px]" disabled={busy} onClick={onProbe}>
          <Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />
          Probe
        </button>
        {canConfig && (
          <button type="button" className="btn btn-danger !py-1 !px-2 text-[10px]" disabled={busy} onClick={onDelete}>
            Delete
          </button>
        )}
      </div>
      <p className="mt-1.5 pl-[1.6rem] text-[11px] leading-snug truncate">
        {!d.enabled ? (
          <span className="text-faint">disabled</span>
        ) : d.status.reachable ? (
          <span className="text-dim">{offersSummary(d)}</span>
        ) : (
          <span className="text-warn">{d.status.error}</span>
        )}
      </p>
    </div>
  );
}
```

Adjust only where the house components differ (e.g. `Led` prop name, `Icon` name set — verify `plus`/`refresh`/`lock`/`alert` exist in `components/icons`; substitute the nearest existing icon if not).

- [ ] **Step 2: Mount it in Settings**

In `ui/src/components/settings/SettingsView.tsx`: add `import DriversPanel from "./DriversPanel";` next to the other panel imports (~line 26), and render `<DriversPanel />` directly ABOVE `<BackendPicker />` (~line 149) inside the Connect tab section. BackendPicker stays until Phase 2 retires it.

- [ ] **Step 3: Typecheck + build**

Run: `npm --prefix /c/Users/bear/astro/ui run build`
Expected: `tsc -b` clean + `✓ built in …ms`.

- [ ] **Step 4: Live smoke check (sim + API)**

From `server/`: `.venv/Scripts/python -m pytest tests/test_drivers_api.py tests/test_drivers_probe.py tests/test_drivers_config.py -q` → all pass.
Then start the server (`.venv/Scripts/python -m astrodeck --port 8800`), open `http://127.0.0.1:8800` → Settings → Connect: the Backend Drivers panel renders with the three built-in rows (Simulator green; AstroDeck native red-or-green depending on wheel; ASTAP per detection). Add a `phd2` driver pointing at `127.0.0.1` → row appears, probe shows unreachable error (no PHD2 running) — that's the honest expected state. Stop the server.

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python -m pytest -q` → all pass (native skips OK).

```bash
git -C /c/Users/bear/astro add ui/src/components/settings/DriversPanel.tsx ui/src/components/settings/SettingsView.tsx
git -C /c/Users/bear/astro commit -m "feat(ui/drivers): Settings -> Backend Drivers panel (add/probe/enable/delete + built-ins) (spec §4.2)"
```

---

## Phase-1 exit criteria (grounded, not assumed)

- `pytest -q` green locally AND CI green on push (`gh run list` — verify, don't assume).
- `GET /api/drivers` returns roles + configured + 3 implicit rows; Alpaca offers carry `dev_type`/`dev_num`.
- Settings panel: add/probe/enable/disable/delete round-trips against the live server.
- No change to any existing connect path (ConnectView/BackendPicker behavior identical).

Phase 2 (Equipment tab — devices) and Phase 3 (tasks + resolver) get their own plan documents once this lands, written against the real Phase-1 interfaces.
