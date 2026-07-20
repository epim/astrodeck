# Driver Framework — Design (sub-project A)

**Date:** 2026-07-20
**Type:** Spec (approved in brainstorming; drives an implementation plan next).
**Campaign:** Platform expansion (task #80), Piece 2. This is the **device-driver plugin
category** — named in `2026-07-19-plugin-system-prep-notes.md` as the platform's cheapest,
safest first pillar. It is the framework half of a two-part arc:

- **Sub-project A (this spec):** formalize the existing backend registry into a real,
  third-party-ready **driver framework** — manifest/versioning, entry-point discovery, a
  transport/serial-addressing model, registry-derived driver types, and a hardened safety
  signal. Validated against the backends we already ship plus an in-test example plugin.
- **Sub-project B (separate spec):** the **ZWO AM5N native serial driver** — the first real
  citizen built on A (LX200 codec + pyserial transport + `Telescope` impl + `:Spu#` unpark +
  settle-by-polling). Out of scope here except as the "first citizen" the design must fit.

## Background — what already exists

AstroDeck already has a plugin-shaped device layer. The framework work is hardening it, not
inventing it.

- **Registry + Protocol contract** — `server/astrodeck/devices/backend.py`:
  `BACKENDS: dict[str, Backend]` with `register()` (`:176`), `get_backend()` (`:183`),
  `list_backends()` (`:193`). `Backend` and `BackendSession` are `@runtime_checkable`
  Protocols (`:143`, `:97`). Serializable intents: `ConnSpec` (one role, `:44`) and `RigSpec`
  (whole rig, `:209`).
- **Self-registration by import side-effect** — `devices/backends/__init__.py:11-15` imports
  `sim_backend`, `nina_backend`, `native_backend`, `phd2_backend`, `ascom_local`; each calls
  `register(...)` at import. This hardcoded list is the only discovery mechanism.
- **Config/persistence** — `AppConfig.drivers: list[DriverEntry]` (`config.py:403`,
  `DriverEntry` at `:372`); per-rig assignments in `Profile.devices: list[ProfileDevice]`
  (`profiles.py:52`). Both are **network-only** addressing (`host`/`port`/`dev_type`/`dev_num`).
- **Driver-type coupling** — `DriverType = Literal["nina","alpaca","phd2"]` (`config.py:366`),
  `_DRIVER_TYPE_TO_BACKEND` (`drivers.py:286`), `_PROBES` (`drivers.py:141`). Adding a driver
  type edits three hardcoded places.
- **Safety signal** — `providers.py:63` `_REAL_BACKENDS = ("nina","alpaca")`, checked at
  `:157`, `:356`, `:375` via `getattr(dev, "backend", "") in _REAL_BACKENDS`. It reads the
  **device** `.backend` attribute (`alpaca.py:266` → `"alpaca"`, `nina.py:183` → `"nina"`,
  `base.py:82` default `""`). This decides "is this real hardware?" — which in turn decides
  whether a fake/sim plate-solve is permitted.

## Goals

1. A driver can declare **identity + version + compatibility** in a manifest, surfaced to the UI.
2. **Third-party driver packages** load via `importlib.metadata` entry points with **no core
   edit** — one bad plugin can never break startup, and version-incompatible plugins are
   skipped, not crashed.
3. **Serial devices have a first-class home** in the schema (a transport discriminator +
   serial addressing), so a mount on `COM3` / `/dev/ttyACM0` persists and resolves natively.
4. **Driver types are registry-derived**, removing the three-touch-point `Literal` coupling.
5. The **real-hardware safety signal moves onto the device and cannot be forgotten** — it is
   stamped from the backend manifest, closing the fail-open trap where a new real driver is
   silently treated as a simulator.

## Non-goals (explicitly deferred — other specs / Piece 2)

- The pluggable **sequencer** (step/trigger/condition registry) — the big Piece 2 item.
- **Provider registries** (weather/solve/catalog), **image-processing-step** plugins, and
  **UI-panel** contributions.
- **Untrusted-plugin isolation.** v1 is in-process trusted. The comhost sidecar
  (`comhost/manager.py` + `ascom_local.py`) remains the *pre-designed* upgrade path; we do not
  build a trust boundary here.
- The **ZWO AM5N driver itself** (sub-project B). This spec ships an in-test example backend to
  prove the framework, not a real driver.

## Design

Five pieces, each pinned to a named seam.

### §1 — Driver manifest: identity, version, compatibility

Extend the `Backend` Protocol (`backend.py:143`) with manifest fields. All default so existing
backends satisfy the Protocol unchanged until we set them:

```python
class Backend(Protocol):
    name: str                    # STABLE identity — the registry key, embedded in saved
                                 # profiles/config. MUST NOT change across versions.
    label: str
    roles: tuple[str, ...]
    discoverable: bool
    hostless: bool = False       # (existing)
    # --- manifest (new; all defaulted, additive) ---
    version: str = "0"           # driver semver; built-ins report the app version
    author: str = ""             # "" for first-party
    min_app_version: str = "0"   # skip-load if the app is older than this
    transport: str = "network"   # "network" | "serial" | "loopback"  (see §3)
    hardware: bool = False       # real hardware? drives the safety signal (see §5)
    driver_type: str = ""        # config-facing configurable-driver type (see §4); "" = not
                                 # a user-configurable type (sim / native / ascom-local)
```

**Stable-identity rule (NINA's hard constraint, adopted):** `name` is the identity embedded in
saved profiles (`ProfileDevice.backend`) and config (`ConnSpec.backend`). A driver update MUST
NOT change its `name`, or saved rigs break. Documented in the Protocol docstring; a plugin-author
note goes in the framework doc. (No runtime enforcement is possible for external packages; the
rule is contractual, like NINA's.)

`list_backends()` (`:193`) grows to emit the manifest so the Equipment UI can show version/author
and flag load problems:

```python
{"name","label","roles","discoverable","version","author","min_app_version",
 "transport","hardware","driver_type"}
```

Built-in backends set `version = astrodeck.__version__` (import lazily inside each backend module
to avoid a cycle) and `author = ""`.

### §2 — Entry-point discovery

`devices/backends/__init__.py` keeps importing the five built-ins (they are always present),
then discovers external backends via `importlib.metadata`:

```python
# after the built-in imports
from ._discovery import discover_plugin_backends
discover_plugin_backends()      # scans group "astrodeck.backends"; guarded per entry point
```

A new module `devices/backends/_discovery.py`:

- Reads `importlib.metadata.entry_points(group="astrodeck.backends")`. Each entry point resolves
  to a **zero-argument callable** that registers one or more backends (mirrors today's
  side-effect idiom: the callable calls `register(MyBackend())`).
- Wraps **every** entry-point load+call in `try/except Exception` (never `BaseException`): a
  failing plugin is recorded, not raised — the same "a bug can't break the system" discipline as
  `providers.resolve_all`.
- **Version gating:** after a plugin registers, for each backend it added, compare
  `min_app_version` against `astrodeck.__version__` using `packaging.version.Version`. If the app
  is older, **unregister** that backend and record a "needs newer app" diagnostic. (`packaging`
  is already an install-time dependency via pip/setuptools; the plan confirms it in
  `server/pyproject.toml` and adds it explicitly if missing.)
- Records outcomes in a module-level list readable via `plugin_load_report() -> list[dict]`
  (`{name, dist, version, status: "loaded"|"failed"|"incompatible", detail}`), surfaced through a
  read-only API endpoint so the UI can show "plugin X failed to load: <reason>."

We publish the group in our own `server/pyproject.toml` so **our** first driver (sub-project B)
loads through the exact path an external plugin uses — dogfooding discovery (the one open call from
brainstorming, resolved: AM5N registers via entry point, not the built-in import list):

```toml
[project.entry-points."astrodeck.backends"]
# populated by sub-project B, e.g.  zwo_am5 = "astrodeck.devices.backends.zwo_am5:register_all"
```

### §3 — Transport model + serial addressing (the "format" + the "storage medium")

The framework owns the **addressing model and the transport discriminator**; the actual serial
I/O helper (open port, read-until-`#`, write) ships with the driver in sub-project B. This keeps
the framework from growing a runtime transport-layer hierarchy it doesn't yet need.

Add a `transport` discriminator plus serial addressing to the three addressing carriers.
`transport="network"` is the default everywhere, so every existing record loads unchanged.

**`ConnSpec`** (`backend.py:44`) — add fields + carry them through `to_dict`/`from_dict`:

```python
transport: str = "network"      # "network" | "serial" | "loopback"
port_path: str | None = None    # serial device: "COM3" / "/dev/ttyACM0"
# serial line params that differ from 8N1 ride in `extra` (baud is a no-op on the
# AM5N's USB-CDC link); we do not widen the core contract for them.
```

**`DriverEntry`** (`config.py:372`) — make network addressing optional and add serial fields, with
a validator enforcing per-transport requirements:

```python
class DriverEntry(BaseModel):
    id: str
    type: str                              # was DriverType Literal; now registry-validated (§4)
    transport: str = "network"
    host: str = ""                         # was required; now defaulted
    port: int = Field(default=0, ge=0, le=65535)   # was ge=1; 0 allowed for serial
    port_path: str = ""                    # serial device path
    enabled: bool = True
    label: str = ""
    extra: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_transport(self) -> "DriverEntry":
        if self.transport == "serial":
            if not self.port_path:
                raise ValueError("serial driver requires port_path")
        else:  # network / loopback
            if not self.host:
                raise ValueError(f"{self.transport} driver requires host")
            if not (1 <= self.port <= 65535):
                raise ValueError(f"{self.transport} driver requires port 1..65535")
        return self
```

**`ProfileDevice`** (`profiles.py:52`) — add the same two fields (`transport: str = "network"`,
`port_path: str = ""`); `to_rigspec` copies them into the `ConnSpec`.

**Migration is non-destructive:** all new fields default to the network shape; old JSON on disk
(no `transport`/`port_path` keys) loads as `transport="network"` with today's behavior. Atomic
write + `.bak` recovery already exists (`persist.py`). No data rewrite, no version bump required on
the config schema beyond the additive fields.

### §4 — Registry-derived driver types

Remove the closed `Literal` and the two hardcoded maps; derive them from the registry so a plugin
that declares `driver_type` becomes a first-class configurable driver with no core edit.

- `config.py:366`: `DriverType = Literal[...]` → `DriverType = str`. `DriverEntry.type` becomes a
  plain `str`; membership is validated at the `drivers.py` layer (which may import the registry —
  `config.py` must stay import-light and does not).
- `drivers.py`: replace the literal `_DRIVER_TYPE_TO_BACKEND` (`:286`) and `_PROBES` (`:141`) with
  functions derived from `BACKENDS`:

  ```python
  def driver_type_to_backend() -> dict[str, str]:
      # {b.driver_type: b.name for b in BACKENDS.values() if b.driver_type}
  def configurable_driver_types() -> set[str]:
      # {b.driver_type for b in BACKENDS.values() if b.driver_type}
  def probe_types() -> set[str]:
      # == configurable_driver_types(); a configurable driver type is one we probe
      # for reachability. Built-ins reproduce today's _PROBES = {"nina","alpaca","phd2"}.
  ```

  The built-in manifests set `driver_type`: `nina_backend` → `"nina"`, `native_backend` →
  `"alpaca"` (preserving today's `alpaca`→`native` mapping), `phd2_backend` → `"phd2"`; sim /
  native-implicit / ascom-local leave it `""`. Because those three are exactly the built-ins with a
  non-empty `driver_type`, `configurable_driver_types()`/`probe_types()` reproduce the current
  `_PROBES` and `_DRIVER_TYPE_TO_BACKEND` byte-for-byte, then let plugins extend them.
- `describe_all()` (`:254`) and `resolve_driver_ids()` (`:291`) read the derived maps; behavior for
  the existing three types is byte-identical. A driver `type` not present in
  `configurable_driver_types()` is rejected with a clear error (same failure surface as an unknown
  Literal today).

### §5 — Safety signal on the device (close the fail-open trap)

Today safety depends on a device's `.backend` string being a member of a central tuple
(`_REAL_BACKENDS`). A new real driver that forgets to add its string is silently treated as a
simulator — and the dangerous direction (real hardware → sim) is the one that lets AstroDeck fake a
plate-solve on live gear. Fix: make "real hardware" a positive, single-source declaration that is
**stamped onto the device from the backend manifest**, so a driver author declares it once and it
cannot be forgotten at the check site.

- `Backend.hardware: bool` — manifest field (§1). Built-ins: `native_backend` (Alpaca/direct) and
  `ascom_local` → `True`; `nina_backend` → `True`; `sim_backend` → `False`; `phd2_backend` is a
  guider backend (no telescope safety role) → `False`.
- `Device` base (`base.py`) gains `hardware: bool = False` (attribute always present; safe default).
- **Single wiring point — the orchestrator.** `devices/orchestrator.py` already knows the backend
  name for every device it hands out (it calls `get_backend(name).open()` then
  `session.get_device(role, conn)`). After obtaining each device it stamps
  `device.hardware = get_backend(name).hardware`. This covers **role devices and the
  `guide_camera` pseudo-device** (surfaced via `session.guide_camera()` from the camera endpoint's
  session), so the `gcam` real-rig check at `providers.py:356` reads the flag correctly. One place;
  automatic for every present and future backend. Devices created outside the orchestrator (unit
  tests) keep the safe `False` default.
- `providers.py`: the three checks `getattr(dev, "backend", "") in _REAL_BACKENDS` (`:157`, `:356`,
  `:375`) become `getattr(dev, "hardware", False)`. `_REAL_BACKENDS` (`:63`) is deleted. The device
  `.backend` string stays (it still drives `_BACKEND_LABELS` UI badges) — only the *safety* decision
  is decoupled from it.

**Why this is safer, concretely:** a plugin author sets `hardware=True` once in their manifest;
the orchestrator propagates it; no edit to a far-away tuple is required, so the fail-open path
(forget the tuple → real gear treated as sim) is structurally removed. A test asserts a device
connected through a `hardware=True` backend reports `hardware=True` and that `providers` treats the
rig as real.

## End-to-end: how a driver plugs in

1. External package declares `[project.entry-points."astrodeck.backends"]`; its callable calls
   `register(MyBackend())` with a manifest (`name`, `version`, `min_app_version`, `transport`,
   `hardware`, `driver_type`).
2. At startup, `discover_plugin_backends()` loads it (guarded), version-gates it, records the
   outcome.
3. It appears in `list_backends()` (with manifest) and, if it set `driver_type`, in
   `configurable_driver_types()` → the Equipment UI offers it.
4. The user configures a `DriverEntry` / assigns a `ProfileDevice` with `transport="serial"` +
   `port_path="COM3"`; it persists through the additive schema.
5. `connect_profile` → `resolve_driver_ids` (registry-derived map) → `get_backend(name).open(conn)`
   → the session opens the serial transport and returns the `Telescope`.
6. The orchestrator stamps `device.hardware = backend.hardware`; `providers` sees real hardware and
   refuses to fake a solve.

**No core file is edited to add the driver** — the framework's whole point.

## Testing strategy

New `server/tests/test_driver_framework.py` (plus targeted additions to existing device tests):

- **Manifest round-trip:** `list_backends()` emits the new keys; built-ins report sane values
  (`native`/`nina` → `hardware=True`, `sim` → `False`).
- **Entry-point discovery:** monkeypatch `importlib.metadata.entry_points` to return a fake
  in-test backend; assert it registers and appears in `list_backends()`.
- **Guarded load:** a fake entry point whose callable raises → startup completes, the failure is in
  `plugin_load_report()`, and the built-ins are still registered.
- **Version gating:** a fake backend with `min_app_version` above `astrodeck.__version__` is
  unregistered and reported `"incompatible"`.
- **Serial addressing:** `DriverEntry(transport="serial", port_path="COM3")` validates; a serial
  entry with no `port_path` raises; a network entry with no `host` raises; round-trips through
  config persistence. `ProfileDevice` serial fields survive `to_rigspec` into `ConnSpec`.
- **Back-compat:** legacy config/profile JSON (no `transport`/`port_path`) loads as network with
  unchanged behavior; an existing all-network profile connects exactly as before.
- **Driver-type generalization:** `driver_type_to_backend()` reproduces
  `{"nina":"nina","alpaca":"native","phd2":"phd2"}` from built-ins; a fake plugin declaring
  `driver_type="demo"` extends it; an unknown type is rejected.
- **Safety (the important one):** a device connected via a `hardware=True` backend reports
  `hardware=True` and `providers` treats the rig as real; a sim device reports `False`; a
  regression test asserts none of the three `providers.py` sites reference `_REAL_BACKENDS` and all
  read the device flag.
- **Existing suite stays green:** every current backend still registers and every device/provider
  test passes unchanged.

## First citizen (sub-project B, preview only)

The AM5N validates the framework: `ZwoAm5Backend` (`name="zwo-am5"`, `driver_type="zwo-am5"`,
`roles=("telescope",)`, `transport="serial"`, `hardware=True`, `discover()` enumerates serial
ports / matches VID:PID `03C3:4001`), a `ZwoAm5Session` owning one pyserial handle + an LX200 codec,
and `ZwoAm5Telescope(Telescope)` implementing every ABC method over LX200 with `:Spu#` unpark baked
into connect and `slew()` settling by polling `:GU#`/coordinates (AM5N moves are fire-and-forget,
no ack). It registers through the §2 entry point. Full design in the sub-project B spec.

## Risks & open items

- **`packaging` availability:** the version-gate needs `packaging.version`. Near-universal, but the
  plan verifies it in `server/pyproject.toml` and pins it if absent.
- **Entry-point scan cost / duplicate names:** discovery runs once at import; a plugin whose `name`
  collides with a built-in would overwrite it (`register` is last-wins). The plan adds a
  collision guard: a plugin may not overwrite a built-in `name` (reject + report), protecting the
  first-party backends.
- **`min_app_version` for built-ins:** built-ins are never version-gated (they ship with the app);
  gating applies only to discovered external backends.
- **UI surfacing** of `plugin_load_report()` and manifest fields is a thin read-only add; the full
  Equipment-UI treatment (badges, per-plugin detail) can follow — the API contract is defined here
  so the UI is unblocked.

## Success criteria

1. An external package can add a working device backend with **zero edits** to AstroDeck core, and
   a broken or incompatible one is isolated and reported, not fatal.
2. A serial mount persists and resolves through the schema with first-class serial addressing.
3. Driver types come from the registry; the three existing types behave identically.
4. The real-hardware safety signal is device-borne and manifest-stamped; a new real driver cannot
   be silently demoted to a simulator.
5. The full existing test suite stays green; new tests cover discovery, versioning, serial
   addressing, back-compat, and the safety decoupling.
