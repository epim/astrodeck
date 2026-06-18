# AstroDeck — Pluggable Backends, RBAC & Remote Access — PROGRAM DESIGN SPEC

**Date:** 2026-06-16
**Role:** Tech-lead design spec for a 4-workstream program (backend harness, RBAC, remote relay, native drivers).
**Status:** Design approved. Each workstream is specced → built → verified independently, in dependency order.
**Scope:** `server/` (FastAPI + `astrodeck` package) and `ui/` (React). Vendor-neutral: NINA is a transition bridge; the
end-state is no-NINA via direct Alpaca + native drivers (see `memory/astrodeck-vendor-neutral-direction.md`).

---

## 0. OVERVIEW & DECOMPOSITION

Four workstreams, **in dependency order**. Each is a standalone spec + build + green-gate; later ones consume earlier
ones but do not block their own internal staging.

| WS | Title | Depends on | One-line goal |
|---|---|---|---|
| **W1** | Pluggable backend harness + device support + managed PHD2 | — (foundation) | The plugin seam (`devices/backend.py`, already committed) so a new backend FILLING AN EXISTING ROLE = new module + one `register()` call, zero hub edits (new roles are a known multi-file change, W1.9). Retire the `self.mode`/`sim_rig`/`nina_client` branches. Add server-side sun-exclusion safety (W1.10). AstroDeck owns PHD2 as a supervised internal service. |
| **W2** | RBAC (capability-based) | W1 (so `config.backend` is a real capability) | Capability-gated routes + WS; pluggable auth provider (`none` default, `google` OIDC). Builds on the existing `ASTRODECK_TOKEN` infra. |
| **W3** | Remote access (PLAIN HTTP/WS tunnel over ONE outbound WSS — **NO gRPC, NO Tailscale**) | W2 (home re-auths every tunnelled request) | The scope dials ONE OUTBOUND persistent **WSS** to a small PUBLIC relay; a remote browser hits the relay over HTTPS+WSS and the relay TUNNELS the WHOLE app (SPA + API + `/ws`) down the scope link as **opaque framed bytes**. The home re-authenticates + re-authorizes **EVERY** tunnelled request (`remote=True`). The relay holds NO signing secret and forwards bytes only. RBAC/safety enforce **AT THE HOME**. |
| **W4** | Native drivers / in-process guider (roadmap) | W1 (Backend protocol) | `NativeGuider` (no PHD2 binary), native/INDI/Rust drivers as additional `Backend` registrations, the eventual NINA→Rust port. Brief only. |

**Why this order:** W1 creates the abstraction every later feature builds against (W4 drivers are just more `Backend`
registrations; W2's `config.backend` capability gates the W1 connect surface). W2 must precede W3 because the relay's
whole job is to tag each tunnelled request with an **authenticated role** — there is no point shipping remote access
before there are roles to enforce. W4 is a roadmap appendix: it changes nothing about W1's seam, it only adds plugins.

**Current state these build on (verified in tree):**
- **`devices/backend.py` ALREADY EXISTS and is canonical** (the seam, `ConnSpec`/`RigSpec`/`Backend`/`BackendSession`
  + registry are committed; `register()` returns the backend; `RigSpec.resolve()`/`to_dict`/`from_dict` exist;
  `ConnSpec` optional fields default to `None`; the solver/guider are duck-typed `object | None`). W1's NEW work is the
  **orchestrator + the 4 wrapping backends + managed-PHD2**, not this module (W1.1).
- `hub.py` carries a `self.mode` string (`none|sim|alpaca|nina`, `hub.py:106`) and **more `self.mode`/`sim_rig`/
  `nina_client` reads than the four originally named** that W1 retires (full inventory in W1.5): `capture_profile`
  (`hub.py:633`), `_preview_source` (`hub.py:693`), the `_nina_heartbeat` path (`hub.py:485`), `poll_status` `nina_link`
  (`hub.py:1320`), `summary()`/`poll_status` `"mode"` (`hub.py:338`/`1216`), and `solve_and_sync`'s
  `get_solver(self.sim_rig, mode=self.mode)` (`hub.py:922`).
- `hub.ROLES` (`hub.py:52`) **duplicates** `backend.ROLES` and is **missing `guider`** — W1.9 unifies them to one source.
- `make_device` (`devices/alpaca.py:669`) constructs a **brand-new `AlpacaConnection`** per call — driving the
  `NativeSession` per-endpoint connection-pool respec (W1.2).
- `_check_horizon` (`hub.py:454`) **returns early on a default site** and only blocks `alt < 0`; `/api/mount/goto`
  `force=true` (`app.py:1113`) bypasses even that — the gap the W1.10 sun-exclusion guard closes.
- The solver ABC is `solve/base.py:PlateSolver` (there is **no** `Solver` class); precedence lives in
  `solve/__init__.py:get_solver(sim_rig, mode=...)` (`:17`) — one owner (W1.3).
- The guider is **already plugged independently** (`hub.connect_phd2` swaps `self.guider` without touching the rig) —
  W1 formalizes that reality into the per-role override model.
- The `/ws` handler (`api/app.py:1505`) is **send-only** (it never calls `receive()`; `app.py:1525-1527`); the optional
  shared-token gate is at `app.py:1513`. So W2 WS RBAC is **accept-time subscribe gating only** (W2.2). The event bus
  gives each subscriber `asyncio.Queue(maxsize=500)` and **drops the oldest on `QueueFull`** (`events.py:32`/`46`) —
  shaping W3 backpressure.
- Optional auth is a single shared token (`api/app.py` `auth_token()`/`_auth_mw` at `:351`). `POST /api/config`
  (`ConfigPatchBody`, `app.py:247`) merges `{site, safety, escalation, alerts, deadman_url}` in one route (W2.2
  multi-domain finding). W2 generalizes this into pluggable identity + capability checks without breaking the open-LAN
  default.

---

## W1 — PLUGGABLE BACKEND HARNESS (the foundation)

### W1.1 The plugin seam — `server/astrodeck/devices/backend.py` (ALREADY EXISTS — extend, don't recreate)

**`devices/backend.py` already exists and is canonical.** It already defines `ConnSpec`, `RigSpec` (with
`resolve()`/`to_dict`/`from_dict`), the `Backend`/`BackendSession` `Protocol`s, the `ROLES` literal, and the
`register`/`get_backend`/`list_backends` registry. **The NEW work in W1 is the orchestrator + the four wrapping
backends + the managed-PHD2 supervisor — NOT this module.** The block below reflects the committed source so the rest
of the spec is grounded in what is actually there; deviations from it in earlier drafts (a `Guider`/`Solver` import,
`""`/`0` defaults, a `void` `register`) are corrected here. No backend imports the hub; the hub imports only this
module + the registry. The solver/guider are referenced **only by duck type** (`object | None`) precisely to keep this
module import-cycle-free (there is no `Solver` class to import — the real one is `solve/base.py:PlateSolver`; the guider
ABC is `guide/base.py:Guider` — and importing either here would re-introduce a cycle, so it is deliberately NOT done).

```python
# devices/backend.py  (EXISTS — this is the committed contract, not new code)
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
# NOTE: NO `from .base import Device`, NO `from ..guide.base import Guider`, NO
# `from ..solve.base import Solver` — devices/base.py:Device, guide/base.py:Guider
# and solve/base.py:PlateSolver are all duck-typed below to keep this import-light.

#: the roles a backend may fill (committed: backend.py:28). "guider" is already a
#: role here (guiding is a role, not a device-dict slot in the hub today).
ROLES: tuple[str, ...] = ("camera", "telescope", "focuser", "guider",
                          "filterwheel", "switch", "safety")

@dataclass
class ConnSpec:
    """How to reach ONE role on ONE backend. The persisted, replayable unit.
    Optional fields default to None (committed backend.py:50) — NOT ""/0 — so an
    absent host/port is unambiguous and survives to_dict/from_dict round-trips."""
    backend: str                       # registry key: "sim" | "nina" | "native" | "phd2"
    host: str | None = None
    port: int | None = None
    dev_type: str | None = None        # Alpaca device type, e.g. "camera"/"telescope"
    dev_num: int | None = None
    role: str | None = None            # one of ROLES
    extra: dict = field(default_factory=dict)   # backend-specific (e.g. nina profile id, managed-PHD2)
    def to_dict(self) -> dict[str, Any]: ...     # already implemented (backend.py:58)
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConnSpec": ...   # already implemented (backend.py:70)

@dataclass
class RigSpec:
    """A full rig: a PRIMARY backend + per-role ConnSpec overrides. Roles absent
    from `roles` resolve to the primary backend via resolve() (committed)."""
    primary: str                       # registry key of the default backend
    roles: dict[str, ConnSpec] = field(default_factory=dict)   # role -> ConnSpec
    def resolve(self, role: str) -> ConnSpec: ...   # already implemented (backend.py:196)
    def to_dict(self) -> dict[str, Any]: ...         # already implemented (backend.py:204)
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RigSpec": ...    # already implemented (backend.py:211)

@runtime_checkable
class BackendSession(Protocol):
    """A LIVE, opened connection to one backend (one NINA client, one Native host
    AlpacaConnection set, one sim rig). Owns its transport; closed on disconnect."""
    name: str
    async def get_device(self, role: str, conn: ConnSpec) -> object: ...   # devices.base.Device | None, duck-typed
    def native_guider(self) -> object | None: ...        # guide.base.Guider this backend provides, if any
    def native_solver(self) -> object | None: ...        # in-process PlateSolver (native only), if any; no stage-A backend provides one
    async def health(self) -> dict | None: ...           # backend_links entry, or None
    async def close(self) -> None: ...

@runtime_checkable
class Backend(Protocol):
    """A registered backend KIND (a factory). Stateless; open() makes a session."""
    name: str                          # registry key, e.g. "native"
    label: str                         # UI label, e.g. "Native (direct)"
    roles: tuple[str, ...]             # roles this backend can fill
    discoverable: bool                 # whether discover() does anything useful
    hostless: bool = False             # endpoint-less: host/port normalize to None in the grouping key (True on SimBackend/Phd2Backend) — W1.3
    async def open(self, conn: ConnSpec) -> BackendSession: ...
    async def discover(self) -> list[dict]: ...          # [] when not discoverable

# ----- registry (the whole plugin surface) — already implemented (backend.py:147) -----
BACKENDS: dict[str, Backend] = {}
def register(b: Backend) -> Backend:   # RETURNS the backend (decorator-friendly), not None
    BACKENDS[b.name] = b
    return b
def get_backend(name: str) -> Backend: ...   # raises KeyError listing registered names
def list_backends() -> list[dict]: ...        # [{name, label, roles, discoverable}], sorted by name
```

> **The seam in one sentence:** a new backend is a new module under `devices/backends/` that defines a `Backend` + its
> `BackendSession` and calls `register(...)` at import time. The hub never names a backend; it only iterates the
> registry and the active `RigSpec`. This is what lets W4's native/INDI drivers land with **zero hub edits** (scoped in
> W1.9 to *new backends filling existing roles* — adding a brand-new role is still a multi-file change).

### W1.2 The four wrapping backends (wrap EXISTING code; no rewrites)

Each is a thin adapter over code that already works. New files under `devices/`; each ends with one `register()` call.

| File (NEW) | Class | `label` | Wraps | Roles | `discoverable` |
|---|---|---|---|---|---|
| `devices/backends/sim.py` | `SimBackend` / `SimSession` | `"Simulator"` | `devices/sim.py:build_sim_rig` | all 7 | no |
| `devices/backends/nina.py` | `NinaBackend` / `NinaSession` | `"NINA bridge"` | `devices/nina.py:build_nina_rig` | camera/telescope/focuser/filterwheel/switch/guider (add `switch` to the committed tuple; safety stays unfillable — W1.9) | yes (`discover_nina`) |
| `devices/backends/native.py` | `NativeBackend` / `NativeSession` | `"Native (direct)"` | `devices/alpaca.py:make_device` + `discover` | camera/telescope/focuser/filterwheel/switch/safety | yes (`alpaca.discover`) |
| `devices/backends/phd2.py` | `Phd2Backend` / `Phd2Session` | `"PHD2 guiding"` | `guide/phd2.py:PHD2Guider` | **guider only** | no |

Key wrapping notes:
- **`SimSession`** holds the dict from `build_sim_rig()` (already a `role -> Device` map plus `_rig`/`guide_camera`).
  `get_device(role)` returns `rig[role]`; `native_guider()` returns a `SimGuider`. **`native_solver()` returns `None`
  today** (`sim_backend.py:67`) — so the SimSolver guard has no source yet. **Respec:** `SimSession.native_solver()`
  yields the guarded `SimSolver` **bound to THIS session's camera** (it carries the session so it knows the frame's
  provenance is sim), and the orchestrator only consults it for the CAMERA role's session (W1.3 `_pick_solver`). One
  session == one `SimRig`. **The SimSolver real-rig refusal is keyed PER-ROLE on the CAMERA role's session, not a
  rig-wide `mode`** — see W1.5, because a `primary=sim` rig with a real-mount override (or a `primary=native` rig with a
  sim-camera override) makes a single rig-wide "is this a real rig?" test ambiguous; the only honest question is "is the
  camera that produced this frame a sim camera?". The seam that makes this implementable is the
  `BackendSession.is_simulated`/`camera_provenance` accessor (or `_pick_solver` reading the camera session's `.name`)
  specced in W1.3/W1.5; on a real-camera session `native_solver()` returns `None` so the hub falls back to ASTAP and the
  SimSolver never runs against a real mount.
  **Sim GUIDE-camera provenance is INDEPENDENT of sim imaging-camera provenance — the same hazard exists on the guide
  path.** `build_sim_rig()` exposes a SEPARATE `guide_camera`, and the per-role override model permits two mixed
  configurations the SimSolver-provenance reasoning above (entirely about the IMAGING camera) does not cover: (a) a SIM
  guider paired with a **REAL mount** (a bench config that exercises real `pulse_guide`), and (b) the normal native case
  where the imaging camera is real but the guider is PHD2. So state guide-frame provenance separately: **a sim guider must
  never issue real `pulse_guide`/`dither` against a real mount without the same mixed-rig first-motion ack the imaging
  path requires (W1.4).** Choose ONE: **bind a sim guider's `pulse_guide` to a sim mount only** (refuse on a real mount
  session), OR **extend the W1.4 first-motion ack to guider-initiated motion on a mixed rig** (the guider's first
  `pulse_guide`/`dither` of a session requires the same one-tap ack as a SlewPad first-motion). Either way the guide path
  inherits the mixed-rig protection, not just the goto path.
  - **The inverse bench case — a REAL guider + SIM mount — fails in PHD2 and must surface as a clear refusal, not an opaque
    alert.** A real PHD2/native guider calibrating against a **sim mount** sees the mount as a no-op (the sim mount accepts
    `pulse_guide` but does not move the star), so calibration **never converges and times out**. Surface this at
    orchestrator time as a clear "real guider cannot calibrate against a sim mount" refusal (the same "bind sim guider to
    sim mount" refusal, mirrored) rather than letting it bubble up as an opaque PHD2 calibration-timeout alert the operator
    cannot diagnose.
  - **PHD2-initiated `pulse_guide` BYPASSES AstroDeck's first-motion ack entirely — so the guide-path motion guard is the
    ORCHESTRATOR-TIME refusal, NOT the UI ack, for any PHD2-guided rig.** In managed (and unmanaged-local) mode PHD2 owns
    the mount connection and issues `pulse_guide` itself; AstroDeck never sees those pulses, so the W1.4 first-motion UI
    ack (a SlewPad/goto gesture) cannot interpose on them. For any PHD2-guided rig the mixed-rig guide-motion protection
    MUST therefore be the **"bind sim guider to sim mount" refusal enforced at orchestrator time** (reject a `phd2` guider
    bound to a sim-mount session, and a sim guider bound to a real-mount session), because the UI ack is structurally
    unreachable on the PHD2 pulse path. The UI ack extension above applies only to a SIM guider whose pulses AstroDeck
    itself issues.
- **`NinaSession`** holds the one `NinaClient` from `build_nina_rig(host, port)` (one HTTP client + the event WS).
  `get_device(role)` returns `rig["devices"][role]`; `native_guider()` returns `rig["guider"]`; `native_solver()`
  returns `None` (committed `nina_backend.py:72-75`) — NINA captures a frame and solves via the hub's local solver
  (`solve.get_solver` → ASTAP, with a refusing `SimSolver` fallback), exactly as `hub.solve_and_sync` does today
  (`hub.py:959-966`); `health()` returns the `nina_link` dict (the old `poll_status` block, now generic).
  No `NinaSolver` class exists (only `AstapSolver`/`SimSolver`), so wiring `native_solver()` to a "NINA solver adapter"
  would make `_pick_solver` (lines 424-425) prefer a nonexistent/hanging NINA solver over ASTAP, re-introducing the
  orphaned-ASTAP / live-rig-hang hazard the `solve_and_sync` fix removed. `_pick_solver`'s ASTAP-vs-guarded-`SimSolver`
  precedence stays the SINGLE owner of the NINA camera solve path.
  **One NINA client per session** — the harness guarantees roles sharing the NINA backend share one client (the
  per-wrapper test asserts object identity). **`close()` must NOT tear down NINA itself** (bridge semantics, per the
  committed `BackendSession.close` docstring) — it drops AstroDeck's client + event WS only.
  **`NinaBackend.roles` must include `switch` (one-model reconciliation):** committed
  `NinaBackend.roles = ('camera','telescope','focuser','filterwheel','guider')` (`nina_backend.py:128`) omits BOTH
  `switch` AND `safety`, but (a) the W1.2 table above lists `switch` for NINA, and (b) the committed thin builder
  `connect_nina` does `RigSpec(primary='nina', roles={r: conn for r in ROLES})` (`hub.py:215`) — requesting ALL 7 roles
  including `switch` — which the new W1.C / §T4(4) "reject any override whose role not in `backend.roles`" rule would
  REJECT. Reconcile in ONE model: **add `'switch'` to `NinaBackend.roles`** (matching the W1.2 table; `devices/nina.py`
  bridges NINA's switch hub), leaving ONLY `safety` genuinely unfillable. The reject-rule (§T4(4)) applies to **EXPLICIT
  user overrides**, NOT to primary-derived resolution — so a `nina` primary that resolves `switch` from the primary is
  fine; only an explicit `switch`→`nina` override against a `nina` that did NOT list `switch` would have been rejected,
  which is exactly why `switch` is added to the tuple.
  **Safety-role gap (call-out):** `NinaBackend.roles` still omits `safety` (correct — `devices/nina.py` exposes no
  SafetyMonitor class). That silently disables the fail-closed unattended-safety engine for NINA-primary rigs. For
  unattended/remote (W3) use, recommend a **per-role `safety` override to a native Alpaca `SafetyMonitor`** — a concrete
  use of the per-role override model that closes a real unattended-safety hole (cross-referenced in W4).
- **`NativeSession`** = direct ASCOM **Alpaca** (the no-NINA path). This is a **REWRITE of the committed
  `devices/backends/native_backend.py`, NOT a from-scratch add.** The shipped `NativeSession.get_device` calls
  `alpaca.make_device(...)` **per role** (`native_backend.py:62`) — and **`make_device` (`devices/alpaca.py:669`)
  constructs a BRAND-NEW `AlpacaConnection` (its own `httpx.AsyncClient`) on every call** — so there is **no connection
  pool today**; each role leaks one httpx client. (NOTE: per-device `make_device` does NOT break ClientID/transaction
  sequencing — `_client_id = 4242` / `_txn = 0` are MODULE-GLOBALS and `_next_txn()` mutates the global
  (`alpaca.py:38-45`), so sequencing is process-global and unaffected by a per-device `AlpacaConnection`; the earlier
  "breaks per-connection ClientID/ClientTransactionID sequencing" claim was factually wrong and is deleted.) The shipped
  `close()` (`native_backend.py:81`) **only clears a dict — it never `aclose`s anything**,
  so it cannot actually close a connection (the connection is hidden inside the device by `make_device`). **Respec
  (rewrite `native_backend.py`):** `NativeSession` owns a `dict[(host, port) -> AlpacaConnection]`. For each role, look
  up (or create) the shared connection for its `(conn.host, conn.port)`, build the device **against that existing
  connection** — add a `make_device` variant that accepts an already-constructed `AlpacaConnection` (or bypass
  `make_device` and instantiate `DEVICE_CLASSES[dev_type](conn_obj, dev_num, name)` directly), then `set dev.role`,
  `await dev.connect()`. Two roles on the same host:port reuse ONE connection; a different host opens a second.
  `native_solver()` is None (use ASTAP / guarded SimSolver). `health()` reports per-host reachability. **`close()` must
  `aclose` EVERY owned `AlpacaConnection`** (`conn.close()` aclose's each httpx client) so a profile switch leaks
  nothing — the §T1 `test_native_backend.py` asserts `close()` actually closes every owned connection (impossible to
  satisfy today since `make_device` hides the connection inside the device).
- **`Phd2Session`** wraps `PHD2Guider(host, port)`. `get_device(non-guider)` **raises `KeyError`** (PHD2 is guider-only —
  matching the committed `Phd2Session.get_device` `raise KeyError` at `phd2_backend.py:56-59`, the house convention sim/
  native also follow); `get_device("guider")` returns the connected guider and `native_guider()` returns the same guider.
  This is the formalization of today's independently-plugged guider. **A guider-only backend must NOT be represented as a
  device `RoleResult` — see W1.3** (its success is "`native_guider()` is not None", not a device in `rig`; the guider is
  resolved via the guider-role session in `connect_profile` step 5, never via a device `get_device` lookup in the
  endpoint-grouping path — so the `KeyError` for non-guider roles is never reached on the happy path).

### W1.3 `devices/orchestrator.py` (EXISTS as `assemble()`/`AssembledRig` — RENAMED + RESHAPED, not greenfield)

The single function the hub calls to bring a `RigSpec` online. Owns: role→backend resolution, **one session per
backend ENDPOINT**, graceful per-role degradation, teardown-on-partial-failure, and assembly of `hub.rig` /
`hub.sessions`.

#### W1.3.0 SUPERSEDES THE COMMITTED ORCHESTRATOR — this is a rename + reshape, NOT new code

**`devices/orchestrator.py` ALREADY SHIPS** with `async def assemble(spec: RigSpec) -> AssembledRig`
(`orchestrator.py:61`) and a `to_dict` (`orchestrator.py:124`), and **`tests/test_orchestrator.py` PINS that
contract** — `test_to_dict_summary_shape` asserts the summary keys are exactly
`{roles, sessions, has_guider, has_native_solver, failures}` (`test_orchestrator.py:69`), and the assembled-rig fields
are `rig` / `sessions` / `guider` / `solver_source` / `failures` (`orchestrator.py:45-49`). `connect_profile`/
`ConnectResult`/`RoleResult` as written below DO NOT EXIST YET — they are the **target shape** of a deliberate
rename-and-reshape of the committed `assemble`/`AssembledRig`. Stop framing W1's orchestrator as unbuilt; the work is to
EVOLVE the shipped function, and the shipped tests are the gate that proves the reshape preserves behavior.

The reshape, stated as an explicit breaking amendment landed in **stage A**:

| Committed today (`orchestrator.py`) | Reshaped target (this spec) | Why |
|---|---|---|
| `assemble(spec) -> AssembledRig` | `connect_profile(spec) -> ConnectResult` | rename; same single entry point the hub calls. |
| `AssembledRig.failures: dict[str, str]` (failed roles ONLY) | `ConnectResult.results: list[RoleResult]` where each `RoleResult` is `(role, ok, error, attempted)` covering **EVERY role the rig requested** | a dict of failures cannot represent the W1.6 tri-state LED (never-tried vs failed vs connected) — a role absent from `failures` could be connected OR never-resolved. See W1.6. |
| `sessions: dict[str, BackendSession]` keyed by the **string** `_session_key(name, conn)` = `"{name}@{host}:{port}"` (`orchestrator.py:52`) | `sessions: dict[tuple[str, str|None, int|None], BackendSession]` keyed by the `(backend, host, port)` **tuple** | the string key is a lossy join; the tuple is the grouping key W1.3 already requires for endpoint isolation, and is unambiguous for `disconnect_all` fan-out. |
| `solver_source: object | None` | `solver: object | None` (same camera-session-`native_solver()`-or-fall-back semantics) | rename only; the fallback to `solve.get_solver` is unchanged (W1.3 solver-precedence note). |
| `to_dict` over `AssembledRig` | `to_dict` over `ConnectResult`, keys preserved as a **back-compat superset** | the existing `{roles, sessions, has_guider, has_native_solver, failures}` keys are kept (with `failures` derived from the not-ok `results`) so `test_to_dict_summary_shape` still passes; `results` is added alongside. |

**The back-compat `sessions` summary value MUST be computed None-safely AND JSON-ably from the new tuple keys — do NOT reuse the committed `to_dict` line.** The committed `to_dict` emits `"sessions": sorted(result.sessions)` (`orchestrator.py:129`), which sorted over the OLD string keys. Re-keying `sessions` to the `(backend, host|None, port|None)` tuple breaks that line two ways: (a) **`sorted()` over the tuple keys raises** `TypeError: '<' not supported between instances of 'int' and 'NoneType'` the moment two same-host keys differ in port-presence — e.g. `('native','host',11111)` vs `('native','host',None)`, exactly the partial-port multi-endpoint rigs the tuple key is introduced to support — because tuple comparison falls through to comparing `11111 < None`; and (b) **raw tuples are not JSON-serializable**, so even an unsorted dump fails the API/diagnostics contract. The reshaped `to_dict` MUST therefore stringify each tuple key AND sort None-safely, e.g. `"sessions": sorted(str(k) for k in result.sessions)` (sorting the stringified tuples is total and JSON-able), or equivalently sort the keys under a None-coercing key `key=lambda k: (k[0], k[1] or "", k[2] or -1)` and emit `str(k)`/`list(k)` for each. State this derivation explicitly in this row so no implementer carries `sorted(result.sessions)` forward verbatim and ships the `TypeError`.

**Stage-A gate:** `tests/test_orchestrator.py` is **rewritten in stage A as the C1 gate** (the fault-injecting §T2 suite
replaces the happy-path `assemble` tests) — but its existing `to_dict`-shape and graceful-degrade assertions are
carried forward (back-compat keys preserved) so the rename is proven non-regressive. **The W1.5 characterization
snapshots are taken against the `assemble()` path that exists today** (the pre-refactor baseline), then re-asserted
against `connect_profile` after the reshape.

**An `open()`-failure contributes NO session — `sessions` stays empty when the only endpoint fails, and the thin
builders detect total-primary-failure from `results` to preserve the committed NINA fail-fast.** When a backend's
`open()` raises, `connect_profile` records the failure on every device role of that endpoint (the `except` around
`get_backend(...).open(...)` above) and adds NO entry to `sessions`. So a single-endpoint rig whose only `open()` fails
returns `ConnectResult` with **empty `sessions`** and not-ok `RoleResult`s carrying the recorded `error`. This is what
the committed `connect_nina` fail-fast depends on: today it does `if not result.sessions: raise DeviceError(next(iter(
result.failures.values()), ...))` (`hub.py:222-224`) to convert NINA-unreachable into a propagated `DeviceError` rather
than a silent partial-connect. After the reshape the thin builders detect total-failure-of-the-primary by checking **no
session opened for the primary backend** and **re-raising the recorded `RoleResult.error`** (read from `results`, since
`failures` is now a derived back-compat alias): e.g. `if not any(key[0] == spec.primary for key in result.sessions):
raise DeviceError(<first not-ok primary RoleResult.error>)`. State this in W1.3.0 so the reshape preserves the
DeviceError fail-fast. Add a **§T2/§T4 case:** a NINA-primary rig whose `open()` raises produces **empty `sessions`**
AND the thin `connect_nina` builder **re-raises** the recorded reason (NOT a silent partial-connect), matching the
committed `hub.py:222-224` behavior.

**Grouping key is `(backend, host, port)`, NOT bare `backend`.** A native rig can put its mount on one Alpaca
host:port and its camera on another (and a dual-NINA setup can span two NINA hosts); "one session per backend opened
from `role_conns[0][1]`" would (a) collapse two distinct endpoints into one session and (b) open every role against the
**first** role's coordinates. Group by the full endpoint so each endpoint opens exactly one session. This matches the
committed `_session_key(name, conn)` instance-dedup (`orchestrator.py:52`); the reshape only changes the key from a
string to the `(backend, host, port)` tuple (W1.3.0).

**Hostless backends NORMALIZE host/port to `None` in the grouping key, so all sim roles always coalesce into ONE
session.** `sim` (and `phd2` in unmanaged-LOCAL mode) is **endpoint-less** — there is one `SimRig` per open, and every
sim role MUST share it (the shared `SimRig` state is the whole point — two SimSessions would split the simulated
mount/camera state). But a stray `ConnSpec.host`/`port` on a sim override (left over from a copy-paste or a UI default)
would make `(backend, host, port)` produce TWO sim keys and open the `SimRig` twice, silently splitting state. So
`_group(resolved)` **normalizes `host`/`port` to `None` for any hostless backend** via the REQUIRED `Backend.hostless`
boolean flag (W1.1; `True` on `SimBackend`/`Phd2Backend`, default `False`): `_group`/`_pick_guider`/`_pick_solver`
normalize `host`/`port` → `None` via `get_backend(name).hostless`, **never a literal `{sim, phd2}` name set**. (The
discarded alternative — `RigSpec.resolve`/`connect_profile` dropping `host`/`port` for sim/phd2-local before keying — is
NOT permitted: it can only be implemented by hardcoding the `{sim, phd2}` name set inside `_group`/`connect_profile`,
re-introducing exactly the `if backend == "sim"` coupling the pluggable design exists to delete.) So all sim roles always
collapse to the single key `("sim", None, None)` regardless of stray addressing. Add a **§T2 case:** a `primary=sim` rig with a sim override carrying a **non-None `host`** still opens the
`SimRig` **exactly ONCE** (assert one session in `sessions`, and that the override role and a primary role share the
SAME `SimRig`/shared state) — proving stray addressing cannot split the shared sim state.

**`Backend.open` takes ONE `ConnSpec`, NOT a role-slice — keep the committed signature.** The committed Protocol is
`async def open(self, conn: ConnSpec) -> BackendSession` (`backend.py:135`) and all four shipped backends implement
exactly that (`sim_backend`/`nina_backend`/`native_backend`/`phd2_backend`). The orchestrator pseudocode in earlier
drafts called `get_backend(name).open_endpoint(role_conns)` — **a method that exists nowhere**; do NOT introduce it.
The chosen contract (explicit stage-A amendment) is **(b): keep `open(conn)`**, passing the endpoint's **representative
`ConnSpec`** (host/port identify the endpoint), and have the session read each role's own `host`/`port`/`dev_num` from
the per-role `ConnSpec` passed to `get_device(role, conn)` at device-build time. This is already how the committed
sessions behave (`get_device(role, conn)` receives the role's `ConnSpec`, `native_backend.py:53-66`), so the
per-endpoint connection pool (W1.2 `NativeSession`) keys off `(conn.host, conn.port)` at `get_device` time, not off
`open`. The session may still be modeled as an explicit per-endpoint connection pool with stated `health()`/`close()`
fan-out; the orchestrator must not assume one transport per backend NAME.

```python
# devices/orchestrator.py  (RESHAPE of the committed assemble()/AssembledRig — see W1.3.0)
# RoleResult is tri-state: attempted=False -> never tried (no LED red); attempted=True+ok -> connected;
# attempted=True+not ok -> failed (carries error). connect_profile emits ONE per REQUESTED role.
@dataclass
class RoleResult:
    role: str
    ok: bool = False
    error: str | None = None
    attempted: bool = False          # False => "never tried" (unfillable/not requested), NOT a red LED

def _requested_roles(spec: RigSpec) -> set[str]:
    # The set of roles this rig "asks for" = every role the PRIMARY backend can fill
    # (get_backend(spec.primary).roles) UNION every role with an explicit override
    # (set(spec.roles)). RigSpec carries only primary:str + roles:dict, so the primary's
    # fillable set MUST be read from the registry — it cannot be derived from RigSpec alone.
    # An explicit override for a role NOT in that backend's roles is STILL "requested" (so it
    # surfaces a FAILED RoleResult rather than vanishing) — the W1.C/§T4(4) reject-rule fires on
    # the override at apply-time; this function only decides what shows an LED.
    return set(spec.roles) | set(get_backend(spec.primary).roles)

async def connect_profile(spec: RigSpec) -> ConnectResult:
    # 1. resolve only the roles the RigSpec actually requests (primary + overrides) — NOT all of ROLES,
    #    so an unfillable role on a given rig never shows a permanent red LED (W1.9 rotator note, W1.6).
    requested: set[str] = _requested_roles(spec)        # = set(spec.roles) | get_backend(spec.primary).roles
    resolved: dict[str, ConnSpec] = {r: spec.resolve(r) for r in requested}
    # 2. group roles by (backend, host, port) so each ENDPOINT opens EXACTLY ONE session.
    #    'guider' is handled specially (step 5) — a guider-only backend (PHD2) returns
    #    no device and must NOT emit a device RoleResult here.
    by_endpoint: dict[tuple[str, str | None, int | None], list[tuple[str, ConnSpec]]] = _group(resolved)
    sessions: dict[tuple[str, str | None, int | None], BackendSession] = {}
    rig: dict[str, Device] = {}
    # every REQUESTED role starts attempted=False ("never tried"); flipped as we work it.
    results: dict[str, RoleResult] = {r: RoleResult(r) for r in requested}
    opened: list[BackendSession] = []                   # for teardown-on-failure
    try:
        for key, role_conns in by_endpoint.items():
            backend_name = key[0]
            rep_conn = role_conns[0][1]                 # endpoint's representative ConnSpec (host/port)
            try:
                session = await get_backend(backend_name).open(rep_conn)  # committed open(conn); NOT open_endpoint
            except Exception as e:
                for role, _ in role_conns:              # whole endpoint down → its (device) roles failed
                    if role != "guider":
                        results[role] = RoleResult(role, ok=False, error=str(e), attempted=True)
                continue
            sessions[key] = session
            opened.append(session)
            for role, conn in role_conns:
                if role == "guider":
                    continue                            # guider resolved in step 5, never as a device result
                try:
                    dev = await session.get_device(role, conn)   # session reads conn.host/port/dev_num per-role
                    if dev is not None:
                        rig[role] = dev
                    results[role] = RoleResult(role, ok=dev is not None, attempted=True,
                                               error=None if dev is not None else "role unavailable")
                except Exception as e:                  # PER-ROLE failure DEGRADES, never crashes
                    results[role] = RoleResult(role, ok=False, error=str(e), attempted=True)
    except BaseException:
        for s in opened:                                # an unexpected raise must not leak transports
            try: await s.close()
            except Exception: pass
        raise
    # 5. pick the guider from the GUIDER ROLE's session.native_guider(); its RoleResult is
    #    ok=(guider is not None) — NOT a device lookup (see W1.2 Phd2Session). _pick_guider keys
    #    'sessions' with the SAME normalized grouping key _group used (hostless host/port -> None)
    #    so a stray-addressed sim/phd2-local guider override still finds its session (defined below).
    guider = _pick_guider(resolved, sessions)
    if "guider" in requested:
        results["guider"] = RoleResult("guider", ok=guider is not None, attempted=True,
                                       error=None if guider is not None else "no guider")
    # 6. pick the solver: active camera's native_solver() ELSE delegate to solve.get_solver(...)
    #    (the SINGLE owner of ASTAP-vs-sim precedence — do NOT re-derive it here). _pick_solver
    #    receives the CAMERA SESSION (its .name maps to the literal solver mode) so the per-role
    #    SimSolver guard is implementable (W1.5).
    solver = _pick_solver(resolved.get("camera"), rig.get("camera"), sessions)
    return ConnectResult(rig=rig, sessions=sessions, guider=guider,
                         solver=solver, results=list(results.values()))
```

- **`_requested_roles(spec)` is DEFINED, not assumed.** It is `set(spec.roles) | set(get_backend(spec.primary).roles)`
  — i.e. `connect_profile` reads `get_backend(spec.primary).roles` from the registry to enumerate what the primary can
  fill, UNIONed with the explicit overrides. This matters because `RigSpec` carries only `primary: str` + `roles: dict`
  (`backend.py`), so there is **no way to enumerate the requested set from `RigSpec` alone** — the primary's fillable
  roles MUST come from the registered `Backend.roles`. A role present as an **explicit override but NOT in that backend's
  `roles`** is STILL "requested" so it surfaces a **failed `RoleResult`** (the W1.C / §T4(4) server-side reject-rule
  rejects the offending OVERRIDE at apply-time) rather than silently vanishing from the LED grid. The whole W1.6 tri-state
  LED contract ("an unfillable role never shows a red LED") hinges on this function being exactly this set, so it is
  pinned here and exercised by the §T2 grouping tests (§T2(9)).
- **`_pick_guider(resolved, sessions)` is DEFINED, not assumed — and its session lookup is PINNED the same way
  `_pick_solver`'s is.** It locates the guider's session using the **SAME normalized grouping key `_group` produces**:
  apply the hostless `host`/`port` → `None` normalization (W1.3 §T2(10)) to `resolved["guider"]` BEFORE keying into
  `sessions`, then return that session's `native_guider()`. Keying off the **raw** `(conn.backend, conn.host, conn.port)`
  is a BUG: a stray non-None host on a `sim` or `phd2`-local guider override (a copy-paste / UI default) would key under
  `(backend, host, port)` while `_group` opened the session under `(backend, None, None)` — the lookup MISSES, returns
  `None`, and `connect_profile` emits a spurious `"no guider"` `RoleResult` (line 393) for a guider that is in fact up.
  Equivalently, restore the explicit `role → session-key` map the committed `assemble()` built (`orchestrator.py:76`,
  `:92-93`, `:105-108`) and have BOTH `_pick_guider` and `_pick_solver` read it — either way both seams resolve their
  session through the SAME normalized key, never raw addressing.
- **Solver precedence has ONE owner.** Do NOT re-derive "ASTAP else sim" in the orchestrator. `_pick_solver` returns
  the active camera session's `native_solver()` if non-None, otherwise calls the existing
  `solve.get_solver(sim_rig, mode=...)` (`solve/__init__.py:17`) so ASTAP-vs-guarded-sim precedence stays in one place
  (today the hub and `solve` both reason about it — W1.5 collapses that). `mode` passed to `get_solver` is derived from
  the **camera role's** session name, not a rig-wide mode (ties to the SimSolver per-role guard, W1.5).
- **The per-role SimSolver guard needs an explicit seam — it has none today.** Committed `SimSession.native_solver()`
  returns `None` (`sim_backend.py:67`), `AssembledRig.solver_source` carries no signal about camera provenance, and
  `SimSolver._REAL_MODES = ('nina','alpaca')` keys on literal mode strings — so as written the per-role guard has no
  implementable path. Add the seam: **`_pick_solver(camera_conn, camera_dev, sessions)` receives the camera role's
  session**, and the literal solver mode is derived from **that session's `.name`** mapped to `'sim'`/`'nina'`/`'alpaca'`
  (NOT a hub-wide `self.mode`). When the camera session is sim, `SimSession.native_solver()` yields a `SimSolver` bound
  to that camera session (so it knows the frame's provenance); a real-camera session returns `None` here and the hub
  falls back to ASTAP/`get_solver` with `mode` ∈ `_REAL_MODES`, which forces the `SimSolver` refusal on a real rig.
  Equivalently, expose `BackendSession.is_simulated` (or `camera_provenance`) so `_pick_solver` can branch without
  string-matching `.name`. Either way the guard keys off the **camera-producing session**, which is the only honest
  signal of whether the frame is fake (W1.5).
- **The `guide_camera` pseudo-role needs a Protocol-level seam — `connect_profile` cannot reach it today.** `build_sim_rig`
  exposes a NON-`ROLES` `guide_camera` (`sim.py:493`) that the committed `connect_sim` stores at `hub.devices['guide_camera']`
  (`hub.py:187`) and `summary()` reads — but `connect_profile` iterates only `_requested_roles ⊆ ROLES` (`guide_camera` is
  not in `ROLES`), so the reshape would **never populate `guide_camera`**, silently breaking the guide-cam readout and the
  W4 `NativeGuider` centroiding fixture (which needs the guide frame source). It cannot be reached via `get_device(role)`
  because it is not a role. **Decision: add ONE Protocol-level accessor `BackendSession.guide_camera() -> object | None`**
  (analogous to `native_guider()`), defaulting to `None` for nina/native/phd2 and returning the sim guide camera for
  `SimSession`. `connect_profile` calls it on the **CAMERA role's session** (the session that owns the imaging+guide frame
  source) and surfaces the result into `ConnectResult` as an explicit `guide_camera` field — NOT by reading the
  `SimSession`-only `.guide_camera` property. The hub stores `ConnectResult.guide_camera` at `hub.rig['guide_camera']` /
  `hub.devices['guide_camera']` exactly as today, so `summary()['guide_camera']` is preserved. (This is the same
  Protocol-vs-non-Protocol-accessor fix W1.5 makes for `shared_state`/`client` — `guide_camera` is the third such bypass.)
  Add a **§T3 assertion** that `summary()['guide_camera']` is byte-for-byte unchanged across the `assemble→connect_profile`
  reshape, and a **§T1 assertion** that the guide-cam frame source is reachable through the contracted
  `BackendSession.guide_camera()` accessor (not the `SimSession`-only property).

Hub assembly (replaces today's `connect_sim`/`connect_nina`/`connect_alpaca_device` bodies, which become thin
`RigSpec`-builders that call `connect_profile`):
- `hub.rig: dict[str, Device]` (was `hub.devices`), `hub.sessions: dict[tuple, BackendSession]` (NEW), `hub.guider`,
  `hub.solver`. `hub.require(role)` reads `hub.rig`.
- **Per-role failure degrades to "disconnected"** in `results` (already the `apply_profile` contract at `hub.py:567`)
  rather than aborting the whole connect — a missing focuser must not stop the camera coming up.
- **Teardown contract (transport-leak safety):** `connect_profile` must close any sessions it opened if a later step
  raises (the `try/finally` over `opened` above). **`hub.disconnect_all()` is rewritten to `await s.close()` for every
  `s in hub.sessions.values()`** (it must replace today's `self.devices`/`self.nina_client`/`self.guider` teardown at
  `hub.py:218`) so every backend transport — not just NINA's — is released. **On re-apply (profile switch), close the
  OLD sessions BEFORE opening the new ones** (close-before-open ordering) so two generations of transports never
  coexist and leak across a switch.

### W1.4 Granularity — PRIMARY backend + PER-ROLE overrides (user choice)

The user picks a **primary** backend (fills every role by default) and optionally **overrides individual roles**:
- *Native mount + camera, PHD2 guiding* → `primary="native"`, `roles={"guider": ConnSpec(backend="phd2", ...)}`.
- *Real mount + sim camera* (bench testing) → `primary="native"`, `roles={"camera": ConnSpec(backend="sim")}`.
- *Pure sim* → `primary="sim"`, no overrides.

The guider being independently pluggable today is exactly this model with one override; W1 generalizes it to all roles.

**Per-role REAL/SIM identity is the operator-truth, NOT a single primary-derived mode chip.** A `primary=sim` rig with
a real-mount override would display a global "simulator" chip while a real mount physically slews — a dangerous lie on
the daytime bench. W1 surfaces a persistent **per-role badge** (`REAL` / `SIM`, and `NINA` / `native` / `PHD2`) at
**every place the operator acts**: the Mount view + SlewPad, the capture controls, and the guide controls each read
their OWN role's backend. The client's `slewController.isNina()` / hold-disable logic must read the **telescope role's**
backend, not a single global `mode` — otherwise hold-slew behaves wrong on a mixed rig (see W1.5 + the W2 client role
model in W1.C/W2.5).
- **The "any real role" header indicator is a SATURATED MOTION-CLASS warning that NAMES the real role**, not the dim
  telemetry-banner treatment. It lights whenever ANY role is non-sim and reads e.g. "REAL MOUNT (native) — motion is
  physical" so a mostly-sim bench rig with one real motion device is never mistaken for pure sim and the operator sees
  WHICH device is real, loudly.
- **Header/banner PRECEDENCE — one loud region, the rest collapse into the chip cluster.** This spec introduces four-to-
  five competing persistent loud surfaces (REAL-MOTION class, DAYTIME/SUN, PAUSED-PENDING-ACK, boot-failure,
  transport-down, telemetry-stale). Define a **single persistent alert region** with an explicit priority ordering —
  **REAL-MOTION class > DAYTIME/SUN > PAUSED-PENDING-ACK > boot-failure > transport-down > telemetry-stale** — so at most
  ONE loud banner shows and the rest collapse into the header chip cluster. This prevents a safety-critical banner
  (REAL-MOTION, SUN) being visually swamped by a routine transport/telemetry notice. **Require shape/letter
  differentiation, not just hue:** the `HealthLeds` night-mode `--good`/`--warn`/`--bad` are near-identical reds at night,
  so REAL-MOTION and SUN must remain distinguishable by icon/shape/letter (a red-on-red hue difference is invisible in
  the dark).

**Mixed real-motion + sim-imaging is a guarded case — and the guard must RECUR per action, not just at connect.** A rig
that pairs a **real motion device** (mount/focuser) with **sim imaging** is exactly the daytime-bench hazard the
SimSolver guard and the sun-exclusion check (W1.10) protect against. The connect-time confirm (W1.C) is one-time; but
the accidental-aim-at-sun-via-fake-solve action RECURS at every SlewPad / goto press long after that confirm. So:
- The picker (W1.C) requires an explicit connect confirm for the mixed rig (one-time), AND
- A **lightweight PER-ACTION guard** sits on SlewPad/goto for a mixed real-motion+sim-imaging rig: the **first motion of
  a session requires a one-tap ack (or an explicit "arm")** — distinct from the always-blocking sun cone (W1.10), which
  is a hard floor, not an ack. This catches the recurring fake-solve-then-slew hazard the connect confirm cannot.
  - **The first-motion ack is a SEPARATE "arm" affordance that gates the pad BEFORE any press — NOT a confirm dialog
    fired from inside `onPointerDown`.** Wire it as an explicit **"Arm real mount" toggle** at the **pad boundary**: until
    armed, the SlewPad renders **inert/dimmed with an overlay** ("Arm real mount to slew") and `onPointerDown` is a no-op;
    arming is a single deliberate toggle that enables the pad for the session. **It MUST NOT interpose a modal in the
    pointer-capture press path.** A confirm dialog fired from inside `onPointerDown` (`SlewPad.tsx:201-214`, where
    `onPointerDown → setPointerCapture → ctrl.beginPress → /api/mount/move`) would **steal pointer capture mid-press**,
    leaving a slew started with **no `onPointerUp` release path** (the up event lands on the modal, not the pad) — a
    runaway. It must also **not collide with the blur/visibility `panicStop`** (`SlewPad.tsx:162-177`, which fires
    `/api/mount/stop` fire-and-forget): arming is pad state, panicStop is unconditional, so a panicStop during an armed
    hold still stops the mount and (optionally) disarms. The arm gate wraps the **pad/`beginPress` boundary**, not the
    `/api/mount/move` call. The same arm gate covers the **NINA nudge path** (`SlewPad.tsx:118-144`, a relative goto) so
    a NINA-mount nudge on a mixed rig is equally gated. The implementer wires the arm gate where `padDisabled` already
    gates the pad, not inside the move call.
- The **MountView "Solve & Sync" button is visibly tied to the SimSolver guard**: on a real mount + sim camera it is
  shown disabled-with-explanation ("sim solver cannot sync a real mount") rather than a silent 409/refusal, so the
  refusal is explained at the control, not surfaced as an opaque error.
- **The disabled-with-explanation must NOT be anchored ONLY at the MountView button — solve-and-sync RECURS from
  centering paths.** Solve-and-sync also fires from a **centering SEQUENCE** (`goto_and_center` per target), the **polar**
  routine, and **meridian-flip re-slews**. The server is gated correctly (the guard is in `goto_and_center`, W1.5/W1.10),
  but an operator who sees the disabled MountView button can still start a centering sequence on the same real-mount +
  sim-camera rig and get a **silent per-target refusal mid-run**. So extend disabled-with-explanation to the **sequence
  Run / Preflight** path: when the active rig is **real-mount + sim-camera AND the sequence uses plate-solve centering**,
  the **`PreflightModal` surfaces a pinned blocker row** ("sim solver cannot center a real mount — disable centering or use
  a real camera") that **disables Run**, reusing the existing `PreflightModal` blocker mechanism (no new UI). This puts
  the explanation in front of the operator BEFORE the run instead of as a mid-sequence opaque refusal.
- The FOV/pixel-scale used for any "solve-from-hint" is re-derived from the ACTUAL connected camera, never a stale
  profile (W1.5 / W1.11), so a sim "solve" can never fake-center a real mount.
- **MountView `force=true` "slew anyway" ESCALATES to HOLD mode on a REAL mount, in a SINGLE combined confirm.** Today
  `MountView.tsx:74-90` runs a plain `mode:'confirm'` for the `'low'` altitude verdict, then posts goto with
  `force: pf?.verdict === 'low'` (`:89`). On a **real mount** that plain confirm is too weak for a force-override that
  commits real physical motion toward a low/obstructed target. Re-thread it so that **when the telescope role's backend
  is REAL**, the "slew anyway" confirm is a **`confirmDialog` hold (`mode:'hold'`, tone `danger`)**. Specify the
  ORDERING so the altitude confirm does NOT short-circuit before the hold gate: a **single combined confirm** both warns
  about the altitude verdict AND requires the hold gesture (one dialog, not "plain altitude confirm → then a separate
  hold") — so an operator cannot dismiss the altitude warning and bypass the hold. On a SIM mount the existing plain
  `mode:'confirm'` is retained (frictionless). Ground the change against the existing force-threading at
  `MountView.tsx:74-90`/`:89` so the implementer updates that path, not a new one.

### W1.5 Retire the `self.mode` / `sim_rig` / `nina_client` branches → query capabilities/sessions

`hub.mode` survives **only as a DERIVED label** = `spec.primary` (for the existing UI mode chip, itself demoted to a
per-role badge per W1.4). The four originally-named branches are **not the full inventory** — a source grep for
`self.mode` / `sim_rig` / `nina_client` / `backend ==` shows the retirement touches more. Every behavioral read becomes
a capability/session query:

| Old read (file:approx-line) | Was keyed on | Becomes |
|---|---|---|
| `capture_profile` (`hub.py:633`) | `self.mode == "nina"` | iterate `hub.sessions`/`hub.rig` and emit one `ConnSpec` per connected role from `dev.describe()` + `session.name` (no `mode` test). |
| `_preview_source` (`hub.py:693`) | `self.mode` ∈ {sim,alpaca,nina} | the camera role's `session.name` (the backend that produced the frame). |
| `summary()` (`hub.py:338`) `"mode"` | `self.mode` | derived label `spec.primary`; the UI consumes the per-role badges (W1.4), not this single string. |
| `ensure_status_poller` / heartbeat (`hub.py:1074`) | `self.mode == "nina"` heartbeat (`_nina_heartbeat`, `hub.py:485`) | iterate sessions: any session exposing `health()` gets its own generic heartbeat task (not NINA-special). |
| `poll_status` `nina_link` (`hub.py:1320`) | `self.mode == "nina"` and `self.nina_client is not None` | a generic **`backend_links` array**: `[h for s in hub.sessions.values() if (h := await s.health())]`. The `nina_link` key stays as a back-compat alias derived from the NINA entry until the UI migrates. |
| `poll_status` status dict (`hub.py:1216`) `"mode"` | `self.mode` | derived label only. |
| `solve_and_sync` → `get_solver(self.sim_rig, mode=self.mode)` (`hub.py:922`) | `self.mode` + `self.sim_rig` | `mode` derived from the **camera role's** session name; `sim_rig` from the camera session, NOT a hub-wide handle. |
| NINA WS / heartbeat / bridge lifecycle (`_nina_ws_task`/`_nina_hb_task`/`_bridge_ready`, `disconnect_all` `hub.py:229-240`) | `self.mode == "nina"` | owned by `NinaSession` (its event WS + heartbeat live in the session; `disconnect_all` just `close()`s sessions). |
| `reconnect_role` / `_last_connect` replay | per-mode connect replay | replay the persisted `ConnSpec` through `connect_profile` (no per-mode branch). |
| `safety_simulate` sim-only gate (`api/app.py:697`) | `hub.mode != "sim"` → 404 | the CAMERA/PRIMARY **session identity** is sim (re-key off the camera/primary session, NOT the retired `self.mode`), so it can never become reachable on a real rig once `self.mode` is a derived label. This route is `config.safety`-gated in W2.1; see that finding. |
| `connect_sim` reads `session.shared_state` + `session.guide_camera` (`hub.py:177-178`) | NON-Protocol `SimSession`-only attributes | `self.sim_rig` derives from the **camera session's** `native_solver()`/`is_simulated` provenance seam (W1.3/W1.5) — there is NO rig-wide `sim_rig` handle post-retirement; `guide_camera` flows through the new Protocol accessor `BackendSession.guide_camera()` (W1.3), not the `SimSession`-only property. |
| `connect_nina` reads `session.client` (`hub.py:226`) | NON-Protocol `NinaSession`-only attribute | the hub stops reading `session.client` AT ALL — `close()` (which the session owns, W1.5) replaces every use of the raw client; the heartbeat/WS lifecycle already moved into `NinaSession`, so nothing outside the session needs the client handle. |

**Three of these are NON-Protocol session accessors — the BackendSession Protocol exposes ONLY
`name`/`get_device`/`native_guider`/`native_solver`/`health`/`close`.** `session.shared_state`, `session.guide_camera`
(`hub.py:177-178`), and `session.client` (`hub.py:226`) are read by the hub today but are **not on the Protocol**, so a
characterization test that only asserts "no `self.sim_rig` reads outside `SimSession`" would PASS while these three
survive and silently re-couple the hub to a concrete session class. The fates above are mandatory: (a) `self.sim_rig`
becomes derived from the camera session's provenance seam, no rig-wide handle; (b) the hub stops reading `session.client`
entirely (`close()` replaces it); (c) `guide_camera` flows through the Protocol-defined `BackendSession.guide_camera()`
accessor (W1.3). **Extend the W1.5 guard test to assert NO hub call site references a non-Protocol session attribute** —
i.e. the only attributes the hub reads on a `BackendSession` are the six Protocol members — so these three bypasses are
caught alongside `self.mode ==`.

`poll_status` gains `out["backend_links"] = [...]`; the UI's reliability surface reads the array (each entry:
`{backend, label, active, last_ok_age_s, last_error, healthy, warming_up}`).

**NinaSession WS/heartbeat/`_bridge_ready` relocation — the warming-up/health seam, specced concretely.** Moving the
NINA event WS, heartbeat, and bridge lifecycle into `NinaSession` (the row above) requires an explicit migration the
committed code does not yet support: committed `NinaSession.close()` (`nina_backend.py:105`) only calls `client.close()`
— it must additionally **cancel its WS + heartbeat tasks**; and committed `NinaSession.health()`
(`nina_backend.py:77`, returns `{backend, ok, host, port, last_ok, last_error}`) carries **NO `warming_up`/`healthy`**,
yet the W1.5 `backend_links` entry shape above requires them and the hub's `_bridge_ready`-derived warming logic feeds
`poll_status`. So:
- **`health()` gains `warming_up` and `healthy` as an ADDITIVE field change across ALL FOUR sessions** (sim/nina/native/
  phd2) so every `backend_links` entry has the full shape — additive only, no field removed, so the §T3 characterization
  snapshot still matches on the existing keys.
- The hub's `_bridge_ready`-derived warming computation (currently in the hub) is **replaced by reading the NINA
  session's `health().warming_up`** — the warming state moves into `NinaSession` alongside its WS/heartbeat.
- `NinaSession.close()` cancels the WS + heartbeat tasks it now owns, so `disconnect_all` can remain "just `close()` every
  session" with no NINA-special teardown branch.

**Backend-specific POST-ASSEMBLY wiring inventory — "zero hub edits" requires a GENERIC replacement for every step in
the three `connect_*` methods, else a new INDI backend still needs a `connect_indi()`.** The committed `connect_sim`/
`connect_nina`/`connect_alpaca_device` each do backend-specific work AFTER `assemble()` returns; "a new backend = new
module + `register()`" is only true if each of these has a generic, backend-agnostic replacement driven by
`connect_profile`. Inventory and replacement:

| Backend-specific post-assembly step (today) | Generic replacement (connect_profile-driven) |
|---|---|
| `connect_sim`: per-role `await dev.connect()` loop + `await guide_cam.connect()` + `await self.guider.connect()` (`hub.py:183-190`) | `connect_profile` calls `await dev.connect()` UNIFORMLY for every device it places in `rig`, plus the guide-camera (via `BackendSession.guide_camera()`, W1.3) and the guider — no per-backend connect loop in the hub. |
| `connect_sim`: `self.sim_rig = session.shared_state`; `self._last_connect[role] = {"backend": "sim"}` (`hub.py:177`,`185`) | `sim_rig` derives from the camera session's provenance seam (no rig-wide handle, W1.5); `_last_connect` is replaced by the persisted `ConnSpec` per role (replay re-runs `connect_profile` against it). |
| `connect_nina`: `self.nina_client = session.client`; `self._start_nina_ws()` (`hub.py:226`,`240`) | the hub never reads `session.client` (W1.5); the WS/heartbeat are OWNED by `NinaSession` and started inside `open()`/the session, so there is no hub-side `_start_nina_ws` to call. |
| `connect_alpaca_device`: `set dev.role` + `await dev.connect()` + `_last_connect` replay record | `connect_profile`/`NativeSession.get_device` already sets `dev.role` and the uniform `dev.connect()` covers it; replay uses the persisted `ConnSpec`. |

So the generic post-assembly contract is: **`connect_profile` awaits `device.connect()` for every placed device + the
guide camera + the guider; the session owns its own WS/heartbeat lifecycle; replay re-runs the persisted `ConnSpec`** —
no per-backend hub method. **Test (the "zero hub edits" proof):** register a NEW `FakeBackend` filling `camera` +
`telescope` with ONLY a `register()` call + one import line, and assert a rig with `primary="fakebackend"` comes up
through `connect_profile` with **NO new hub method** — `hub.rig` is populated, `device.connect()` was awaited for each
role, and there is **no `KeyError`** and no `connect_fakebackend` anywhere. This is the concrete discharge of the
"adding a backend needs zero hub edits" headline (W1.9).

**SimSolver real-rig refusal is PER-ROLE, not rig-wide — and needs an explicit SEAM that does not exist today.** The
fake-solve guard must key off whether the **camera role's session is sim** (the camera that produced the frame), not a
single `self.mode`. On a `primary=sim`+real-mount rig (or `primary=native`+sim-camera rig) a rig-wide test is ambiguous
and can either (a) let a sim solver fake-center a REAL mount or (b) refuse a legitimately-sim camera. Committed
`SimSolver._REAL_MODES = ('nina','alpaca')` keys on literal mode strings, `SimSession.native_solver()` returns `None`,
and `AssembledRig.solver_source` carries no camera-provenance signal — so there is **no implementable path** as written.
The seam: `_pick_solver`/`get_solver` receives the **camera session** (W1.3); the literal solver mode is derived from
that session's `.name` mapped to `'sim'`/`'nina'`/`'alpaca'` (or a `BackendSession.is_simulated`/`camera_provenance`
accessor). `SimSession.native_solver()` returns a `SimSolver` bound to its camera session (knows the frame is sim); a
real-camera session returns `None`, so `_pick_solver` falls back to ASTAP/`get_solver` with `mode ∈ _REAL_MODES`, and
`SimSolver.solve` refuses to sync because it would write a fake center to a real mount. The guard lives at the
camera-session boundary, not on `self.mode`.

**Guard test:** a source-grep characterization test asserts **no `self.mode ==` remains** anywhere except the single
derived-label assignment (`self.mode = spec.primary`), that `self.sim_rig` / `self.nina_client` reads outside
`NinaSession`/`SimSession` are gone, AND that **no hub call site references a non-Protocol `BackendSession` attribute**
(`session.shared_state`/`session.guide_camera`/`session.client` and any other off-Protocol member) — the hub may read
ONLY `name`/`get_device`/`native_guider`/`native_solver`/`guide_camera`/`health`/`close` on a session. (The current
"no `self.sim_rig` reads outside `SimSession`" assertion passes while the three non-Protocol accessors survive; this
strengthening closes that gap.)

### W1.6 Persistence, boot auto-connect, and the connect API

- **Persistence:** a `RigSpec` is stored as the active **Profile** (extends `profiles.Profile` /
  `ProfileDevice` to carry `backend`+`role`+`ConnSpec.extra`; the per-device `backend` field already exists in the
  profile model). `config_store` already persists `active_profile_id` (`config.py:135`).
- **Boot auto-connect:** `api/app.py:_lifespan` (`:57`) gains: on startup, if `active_profile_id` is set, build its
  `RigSpec` and `await connect_profile(...)` (best-effort, logged) so the rig comes up without a manual click. **The
  auto-connect MUST swallow connect failures** — a raise inside `_lifespan` bricks the whole UI (see test C2); on a
  failure the app still starts and `/api/status` returns 200 with the rig marked disconnected.
- **Mount posture on auto-connect distinguishes "never initiate motion" from "halt unsafe motion found".** AstroDeck
  never auto-unparks, auto-slews, or enables tracking without an **authenticated human action** — that governs only
  AstroDeck's OWN motion. It does NOT cover a real mount that was **ALREADY unparked-and-tracking** when AstroDeck
  (re)starts (power blip, mid-session restart, NINA already driving) — the unattended daytime hazard, and a banner does
  NOT stop a physically-tracking mount.
  - **CONNECT-TIME SAFE-ING ACTION (not just a banner):** on ANY connect (boot or manual), read the mount's pier/park
    state and current alt-az. If a **real** mount is found **unparked+tracking with its current alt-az inside the sun
    cone** (W1.10), **STOP tracking (and optionally PARK) BEFORE surfacing any banner** — gated only by
    `config.solar_override` to RESUME. This is "AstroDeck halts unsafe motion it discovers," a deliberate exception to
    "AstroDeck never initiates motion."
  - A real mount that comes online idle is left **PARKED with tracking OFF**; AstroDeck still never auto-unparks/slews.
  - The boot-failure / DAYTIME-SUN alert is pushed to a **persistent header chip AND the existing alerts/escalation
    sink** (not an ephemeral toast) so an absent operator is actually notified.
- **In-progress-sequence resumption posture on auto-connect — `PAUSED-PENDING-ACK`, NEVER auto-resumed (unattended-restart
  safety).** The connect-time SAFE-ING halt covers a mount discovered tracking into the sun, and the W1.4 per-action
  first-motion UI ack covers manual motion — but NOTHING yet gates **RESUMPTION of a persisted in-progress sequence**
  across an AstroDeck restart (power blip mid-night). The W1.4 ack is a UI gesture an **absent** operator cannot give, so
  the two unstated outcomes are both unsafe: silently resume slewing, or block forever with no signal. **Posture: on
  auto-connect, any persisted in-progress sequence enters a `PAUSED-PENDING-ACK` state — it is NEVER auto-resumed.** The
  state is surfaced on the **persistent header chip AND the alerts/escalation sink** (same sink as boot-failure/
  daytime-sun); **resumption requires an authenticated action** (`control.mount`, the same authority that could start the
  sequence). State plainly: **unattended boot defaults to "no motion until acked"** — an operator whose overnight sequence
  did NOT resume after a power blip is seeing the INTENDED safe behavior, not a bug. (This composes with the connect-time
  SAFE-ING halt: a mount found tracking into the sun is halted regardless of any sequence state.) Tested by a §T4 case
  (a persisted in-progress sequence + auto-connect → state is `PAUSED-PENDING-ACK`, `engine.start` is NOT called, the
  alert reaches the escalation sink, and resume requires `control.mount`).
  - **Resume UX is CONCRETE — a consequence-naming hold-confirm, not a reflexive tap.** Without a resume control that
    names what resuming DOES, an operator returning to a `PAUSED-PENDING-ACK` sequence reflexively taps "resume" and
    starts physical motion. Spec it on the EXISTING `SequenceView` surface + `confirmDialog` (no new UI): **(1)** a
    DISTINCT `SequenceView` visual state — NOT the normal "paused" chip — labeled e.g. **"Held after restart — no motion
    until you resume"**, so the held-on-boot state is visually unmistakable from an operator-initiated pause; **(2)**
    resume is a **hold-confirm** (`confirmDialog` `mode:'hold'`, tone `danger`) stating **"Resuming will re-slew the mount
    and may perform a meridian flip"** so the physical consequence is named before the gesture; **(3)** resume **re-runs
    the connect-time sun-cone check on the FIRST re-slew** (the same `goto_and_center` sun guard, W1.10) so an operator
    cannot resume a sequence into a daytime sky without `config.solar_override` — resuming is not a bypass of the sun
    floor. The §T4 case asserts the held state is the distinct visual state, resume is gated by the hold-confirm, and the
    first re-slew is subject to the sun cone.
- **Boot-result surface (persistent, page-load-surviving) — driven by a per-role result for EVERY requested role.** Do
  NOT rely on an ephemeral toast for a failure that happens before the operator is looking. The LED tri-state depends on
  `connect_profile` emitting a `RoleResult(ok/error/attempted)` for **every role the RigSpec requested** (W1.3.0/W1.3) —
  the committed `failures: dict` (failed roles only) CANNOT represent tri-state (a role absent from `failures` could be
  connected OR never-resolved). Persist the **last connect result**: per-role status carrying the captured
  `RoleResult.error` shown **inline on the Rig page LEDs** (today binary on/off with no error text), tri-state per role
  — `never tried` (`attempted=False`) / `tried and failed (error)` (`attempted=True, ok=False`) / `connected`
  (`ok=True`) — plus a **header indicator** when the last boot auto-connect had ANY failed role. Because only REQUESTED
  roles get a `RoleResult`, an unfillable role on a given rig never shows a permanent red LED (W1.9 rotator note). This
  is the same `results` array `connect_profile` returns (W1.3), retained on the hub and exposed via `/api/status`. The
  per-role-result dependency is a **stage-B gate** (test C2/§T4).
  - **The boot-failure header chip is ACTIONABLE, and channel ownership is split TOAST-vs-PERSISTENT by trigger.** The
    chip is not just an indicator: **tapping it deep-links to the Rig page with the failed roles highlighted** and offers
    a **"retry failed roles" action** that reuses the per-role reconnect via `connect_profile` (the W1.5 reconnect-replay
    path) on exactly the not-ok `RoleResult` roles. Channel ownership is pinned to remove the toast-vs-persistent
    ambiguity: a **BOOT-time connect failure emits NO toast** (no operator is present to see it) and lives ONLY in the
    persistent surface (header chip + per-role LED) **and** the escalation sink; a **MANUAL connect failure keeps the
    existing toast** (`ConnectView showToast`; `SlewPad onError → showToast` — today the ONLY connect-error channel) AND
    updates the per-role LED. So the rule is: **persistent surface + escalation sink own absent-operator (boot) failures;
    the toast owns present-operator (manual) failures, additionally updating the LED.** This removes the ambiguity about
    which channel owns which failure class.
- **Mid-session "retrying" is a FOURTH state, not folded into "failed".** The tri-state above is the BOOT snapshot;
  backends drop and reconnect during a night (NINA reconnect backoff, Alpaca blips, the W3 relay redial), and
  `backend_links` already carries `warming_up`/`last_ok_age_s`/`healthy` (W1.5). Extend the per-role surface to a
  four-state model: **never tried / connected / retrying (degraded-but-reconnecting** — amber/animated with last-ok age,
  read from `backend_links`) **/ hard-failed (gave up after capped-backoff max-retry)**. The same four-state semantics
  apply to device backends and to the relay link (W3.3).
- **A mid-session HARD-FAILED transition ESCALATES, it does not just update the LED (absent-operator notification).** The
  four-state model defines the chip, but a chip only helps an operator who later opens the page — useless for an
  unattended/remote (W3) night when a role exhausts capped-backoff max-retry and goes **hard-failed** (mount drops at 1am
  and never returns). So a per-role transition to **hard-failed** for any **motion/imaging-critical role** — at minimum
  **mount, camera, guider, safety** — AND for the **relay link** (W3.3) pushes to the **SAME alerts/escalation sink** as
  boot-failure and daytime-sun (not merely the chip). **Roles that escalate vs only update the LED:** mount / camera /
  guider / safety / relay-link → **escalate**; a non-critical role going hard-failed mid-idle (e.g. a **focuser** drop
  while not sequencing, a **filterwheel** or **switch** drop mid-idle) **updates the LED + chip only**, no escalation —
  so the escalation sink is not spammed for a benign idle drop. (A focuser/filterwheel that hard-fails DURING an active
  sequence escalates via the sequence's own abort/pause path, not this connect-link path.) Tested by the §T "retrying"
  state-machine case (connected→retrying→hard-failed) asserting the escalation sink receives the hard-failed transition
  for a critical role and does NOT for an idle focuser drop.
- **"Reaches the escalation sink" is asserted via a TYPED BUS EVENT on a RECORDING FAKE BUS — not a real outbound send.**
  Four safety paths assert "the alert reaches the escalation sink" — the connect-time SAFE-ING halt (§T1.10 case e), the
  connected→retrying→hard-failed transition, `PAUSED-PENDING-ACK` (§T4), and the daytime-sun alert — but the test seam
  must be pinned or the assertion is untestable. Pin it: each of these paths **emits a TYPED bus event** (a known
  `type`/`level`/`source`, e.g. an escalation/alert event on the bus) and the tests assert that event is **published to a
  recording fake bus** (the pattern `test_engine_*.py` already uses), NOT a real outbound `httpx` send. This works
  because alerts are **bus-event-driven**: `AlertDispatcher._on_bus_event` (`alerting.py:233`) translates a bus `Event`
  into an outbound `AlertEvent`, so asserting the bus event proves the escalation will fire without coupling the safety
  test to network delivery. The **chip-AND-sink dual surface is asserted as TWO observable signals**: (1) a status/chip
  flag on `/api/status` (the persistent header chip) AND (2) the typed bus event (the escalation sink) — these are two
  independent assertions, not one. Actual `httpx` DELIVERY of the alert (URL/payload) is covered SEPARATELY by the
  existing `MockTransport` alerting tests, not re-asserted in the safety-path tests.
- **Connect API** (additive; old `/api/connect/*` kept as thin shims that build a single-backend `RigSpec`):
  - `GET  /api/backends` → `list_backends()` (drives the Settings backend picker).
  - `POST /api/connect/rig` → body `RigSpec`; returns `ConnectResult` (`{rig, results, backend_links}`).
  - `GET  /api/discover/{backend}` → `get_backend(name).discover()` (generic; replaces per-backend discover routes,
    which stay as aliases).

### W1.7 Managed PHD2 (the "graceful" ask) — part of W1

**The complaint:** NINA shells out to `phd2.exe` and you hand-maintain a **separate** PHD2 equipment profile.
**AstroDeck's answer:** own PHD2 as an internal **supervised service** behind the existing `Guider` interface
(`guide/base.py:Guider`). `Phd2Backend` (W1.2) gains a `ManagedPhd2` supervisor when `conn.extra["managed"]` is set:

1. **Auto-launch** `phd2 --headless` with **`shell=False` and a fixed argv list** (NEVER a shell string). The path is
   validated against an **allowlist of known install locations** (and/or a configured-and-verified absolute path); user
   strings from `ConnSpec.extra` are NOT passed as arbitrary extra flags — only an allowlisted flag set. Absent path →
   assume user-run. **Security:** `ConnSpec.extra` is attacker-influenceable by anyone who can write a profile (everyone
   on the LAN under the `none` default; whoever holds `config.backend` under RBAC) and the watchdog re-launches on a
   loop, so this is a command/path-injection sink — managed-PHD2 launch parameters MUST NOT be settable by a non-admin
   (this is exactly why `config.backend` is a privileged capability in W2: it controls process launch + profile sync).
2. **Sync the equipment profile by writing the PHD2 profile store DIRECTLY ON DISK** before launch — **not** via
   JSON-RPC. **PHD2's JSON-RPC has NO method to set the guide-camera/mount driver, pixel scale, or focal length:**
   `set_profile` only SELECTS an existing profile and `set_connected` only connects already-configured equipment. (The
   current `PHD2Guider` implements **none** of `set_profile`/`get_profiles`/`find_star`/`set_connected`/`loop` — only
   `_rpc`/`start_guiding`/`stop_guiding`/`flip_calibration`/`dither`/`stats`/`guide_frame` exist at `guide/phd2.py`.) So
   to achieve true zero-duplicate config AstroDeck writes the guide camera + mount + pixel scale from the active
   `RigSpec` into PHD2's **platform-specific profile store**: the **Windows registry** (`HKCU\Software\StarkLabs\PHD2`)
   or the **Linux/Mac `PHDGuidingV2` config DB** (an INI/plist under the user profile). Document the store path per
   platform; the writer is gated by the same allowlist/admin constraint as step 1. **No duplicate hand-maintained
   profile** — but via the on-disk store, not an RPC.
   - **The pixel scale written here is the GUIDE camera's, NOT the imaging train's — these are PHYSICALLY DISTINCT
     scales and conflating them makes guide RMS + dither wrong by 3-5×.** `PHD2Guider.pixel_scale` (`guide/phd2.py:64`,
     default 2.0) is the **GUIDE camera** arcsec/px that scales `RADistanceRaw`/`DECDistanceRaw` (`phd2.py:192-193`) and
     `dither(pixels)` (`phd2.py:285`) — derived from the **guide camera + guide-scope focal length**, a SEPARATE optical
     train from the imaging camera (`Camera.sensor_width`/`pixel_size_um`) that W1.11 re-derives for FOV/framing. Writing
     the IMAGING-camera scale into PHD2 would make guide RMS and dither amplitude wrong by the guidescope/mainscope focal
     ratio (commonly 3-5×). So the managed-PHD2 profile write derives pixel scale / calibration / dither from a
     **guide-optics record** (guide camera + guide-scope focal length) added to the profile/`RigSpec` — NOT from the
     imaging train. `phd2_backend.open()` pulls `conn.extra['pixel_scale_arcsec']`; that value MUST carry guide-optics
     provenance, not the imaging-camera FOV re-derivation. **Recommended:** prefer letting **PHD2's own calibrated scale**
     drive the RMS conversion rather than overwriting `pixel_scale` from a guessed value — write the guide-optics scale
     only as the seed for an uncalibrated profile, and read back PHD2's calibration once it exists.
   - **The on-disk writer is a `Phd2ProfileStore` ABSTRACTION (Protocol), with platform impls selected at construction —
     so the profile-sync test never touches the real registry/FS.** The store path is **platform-specific** —
     `HKCU\Software\StarkLabs\PHD2` (Windows registry) and the `PHDGuidingV2` INI/plist (Linux/Mac) — and **Windows is the
     dev/CI OS**, so an un-faked profile-write test would **mutate the developer's real PHD2 registry**. Spec a writer
     abstraction: `class Phd2ProfileStore(Protocol): def write(self, profile_record) -> None: ...` with `WindowsRegistry`
     / `IniFile` impls chosen at construction (the supervisor takes the store as an injected dependency, alongside the
     injected launcher and port-probe). The §T5(1) profile-sync test **injects a recording fake store** and asserts the
     **structured payload** passed to `write()` — the guide camera, the mount, and the **GUIDE-optics pixel scale** — never
     touching the real registry/FS. **Assert provenance:** the value written is the **guide-optics** pixel scale (guide
     camera + guide-scope), **NOT the imaging-camera scale** (the W1.7/W1.11 conflation guard) — so a test catches an
     accidental imaging-scale write. An **OS-gated smoke test** for the real `WindowsRegistry`/`IniFile` writer MAY run on
     demand, but the unit gate uses the fake store.
3. **Auto-connect** the PHD2 equipment — add a **net-new `set_connected(true)` RPC** to `PHD2Guider` (does not exist
   today).
4. **Auto-select-star + calibrate** on Start Guiding — `start_guiding()` (`guide/phd2.py:245`) already does the
   select/settle; managed mode additionally needs **net-new `find_star`, `loop`, and a calibration-status query RPC**
   (none exist today) so "auto-calibrate if uncalibrated" has a real signal to branch on.
   - **Managed-PHD2 auto-calibrate MUST NOT fire inside `meridian_flip`'s post-flip `start_guiding()`.** `meridian_flip`
     (`hub.py:1043-1079`) does `stop_guiding → goto_and_center → flip_calibration → start_guiding`. If managed mode's
     "auto-calibrate if uncalibrated" branch runs inside that post-flip `start_guiding`, a freshly-written managed profile
     (which has NO calibration yet) would trigger a **full re-calibration mid-sequence** — a calibration routine **slews a
     real GEM off the centered target** and **invalidates the plate-solve centering** just performed by `goto_and_center`.
     So after a flip, **prefer `flip_calibration()` (reuse the existing calibration) over a fresh calibrate**, and only
     fall back to a calibrate if **NO calibration exists AND re-center afterward** (re-run `goto_and_center` so the target
     is re-centered post-calibration). The auto-calibrate-if-uncalibrated branch is scoped to the **initial** Start
     Guiding, not the post-flip restart. Tested by **§T1.12 case**: managed PHD2 + meridian flip **with an existing
     calibration** asserts `flip_calibration` is used and **NO new calibration slew occurs** (the fake records no
     `find_star`/calibrate after the flip).
5. **Supervise + auto-restart on crash** — a watchdog task restarts the headless process (capped backoff, reusing the
   `_RECONNECT_BACKOFF` pattern at `guide/phd2.py:61`), re-writes the on-disk profile (step 2), and re-issues
   `set_connected`.

> **Net-new PHD2 RPC methods this introduces:** `set_connected`, `find_star`, `loop`, and a calibration-status query.
> These are added to `PHD2Guider` as part of C5; the test plan injects them through `FakePHD2`.

**W1-WINDOW SECURITY HARD-GATES — these sinks are LAN-ANONYMOUS for the entire W1→W2 gap (do NOT defer to W2).** W1
ships managed PHD2 (C5), `POST /api/connect/rig`, and the generic `GET /api/discover/{backend}` (C2) **before any RBAC
lands (C6)**. Under the shipped `none` default **every LAN caller is `admin`**, so the managed-PHD2 process spawn + the
on-disk profile writes + the new generic discover proxy are **anonymous-LAN-reachable for the whole window**. The
existing `/api/discover/alpaca` already calls `validate_scan_host` (`alpaca.py:126`), but the new generic route and
`/api/connect/rig` do not yet. State as **W1 hard-gates landing IN W1, not deferred to W2**:
- **(a)** the generic `GET /api/discover/{backend}` **reuses `validate_scan_host`-style allow/deny** for any host-bearing
  backend (reject loopback / private / link-local / metadata) **before any outbound probe** — same guard the alpaca
  discover route already has, applied to the generic route in C2.
- **(b)** managed-PHD2 launch (C5) lands its **argv allowlist + path allowlist + port-4400 detect-and-refuse +
  no-user-strings-as-flags** with a test **IN C5, independent of RBAC** — it does not wait for `config.backend` to exist.
- **(c)** Acknowledge in W1.8 that until C6 these sinks are **LAN-anonymous**, and **recommend shipping the C6 `none`-
  default RBAC scaffold (route table + boot assertion, behavior-preserving) BEFORE/ALONGSIDE C5** so the privileged
  sinks (`config.backend` for connect/managed-PHD2) have a capability check the moment they exist, not weeks later.

**Managed PHD2 is INCOMPATIBLE-BY-CONSTRUCTION with a NINA primary/guider.** In a NINA-primary (or NINA-guider) rig the
guider already IS PHD2: `NinaGuider` reaches PHD2 at the bridge host's `:4400`, and NINA owns that `phd2.exe` process
**and its on-disk equipment profile**. Launching a SECOND managed `phd2.exe` would fight for port `4400` and the
watchdog relaunch loop (step 5) would repeatedly **overwrite the very profile store NINA's running instance is using**
(step 2). So:
- The **W1.C picker hard-disables the managed-PHD2 toggle whenever ANY role (especially `guider`) resolves to NINA**,
  and the orchestrator **refuses a RigSpec that pairs a `nina` guider/primary with a `phd2` extra `managed=true`**
  (server-side, not just UI).
- Managed PHD2 is documented as valid **only on native/sim primaries**.
- **Before launch (or profile-write), detect a pre-existing PHD2 on the target port** (`:4400`) — if one is already
  listening, **REFUSE rather than clobber** the running instance / its profile store, surfacing the conflict instead of
  silently corrupting it.

A setting (`ConnSpec.extra["managed"] = false`) instead **connects to a user-run PHD2** (today's behavior, unchanged)
and issues NONE of the launch / on-disk-profile-write / set_connected steps.
The true "no separate binary" end-state is **W4 `NativeGuider`** — a drop-in behind the same `Guider` interface.

### W1.9 Roles, the `ROLES` tuple, and the registration import site

**"Zero hub edits" is scoped to NEW BACKENDS FILLING EXISTING ROLES.** Adding a brand-new *role* is a known multi-file
change: the `ROLES` literal in **`backend.py:28` (7 roles, with `guider`) AND `hub.py:52` (6 roles, OMITS `guider`)** —
today DUPLICATED and DRIFTED — plus every backend's `roles` tuple, the orchestrator grouping, `capture_profile`, and the
UI picker all move. **Unify the two `ROLES` tuples to ONE source** (`hub` imports `backend.ROLES`, gaining `guider`) so
they can never drift, and either keep roles as a curated literal or derive them from the registered `Device` subclasses.

- **`NativeBackend.roles` is WRONG today — fix it to the explicit set.** It ships as `roles = ROLES`
  (`native_backend.py:92`), which advertises **`guider`** and **`safety`**, but native `_ROLE_TO_DEV_TYPE`
  (`native_backend.py:25`) has **no `guider` entry**, so `get_device('guider')` would `KeyError` (Alpaca has no guider
  device; native guiding is PHD2/W4 `NativeGuider`). The W1.C picker rejects an override whose role is not in
  `backend.roles`, so the over-broad tuple would offer a native guider that can never connect. **Set
  `NativeBackend.roles` to the explicit set the W1.2 table lists:**
  `("camera", "telescope", "focuser", "filterwheel", "switch", "safety")` — exclude `guider`. Add a test asserting each
  backend's `roles` matches exactly what its `get_device`/`native_guider()` can actually serve (no advertised role that
  raises `KeyError`).
- **`NinaBackend.roles` is also WRONG today — add `switch`.** Committed
  `NinaBackend.roles = ('camera','telescope','focuser','filterwheel','guider')` (`nina_backend.py:128`) OMITS `switch`,
  contradicting both the W1.2 table and the committed thin builder `connect_nina` (`RigSpec(primary='nina',
  roles={r: conn for r in ROLES})`, `hub.py:215`) which requests ALL 7 roles. Add `'switch'` so the only genuinely
  unfillable NINA role is `safety` (recommended per the W1.2 safety-override note). **Thin builders / migration request
  `backend.roles ∪ explicit-overrides`, NOT blindly all of `ROLES`.** The committed `connect_nina` blind
  `{r: conn for r in ROLES}` must be replaced by `{r: conn for r in get_backend('nina').roles}` (plus any explicit
  override) so it never requests `safety` against a NINA that cannot fill it. The §T4(4) server-side override-rejection
  rule applies to **EXPLICIT user overrides only**, NEVER to primary-derived resolution — a `nina` primary resolving
  `switch`/`guider`/etc. from the primary is always allowed.
- **Add `rotator` to `ROLES` — but only with the device-class + not-requested-LED guards, ELSE defer to W4.** `rotator`
  is wanted for framing / auto-PA, but there is **no `Rotator` device class** (absent from `base.py` /
  `alpaca.py:DEVICE_CLASSES` / `nina.py`), `framing.py` never reads/sets a device PA, and no consumer reads a rotator —
  so naively adding it to `ROLES` makes `spec.resolve('rotator')` resolve to the primary, `get_device('rotator')` fail,
  and surface a **permanent red LED on every rig**. Choose ONE:
  - **(a) DEFER `rotator` to W4** alongside `dome`/`flat-panel` (the clean option — no half-built role), OR
  - **(b) keep it in W1.9 only if** you also (i) scope a minimal `Rotator(Device)` ABC in `base.py`, AND (ii) make
    absent/unfillable roles resolve to **"not requested"** — `connect_profile` emits a `RoleResult` ONLY for roles
    present in the RigSpec (W1.3 `requested`), so an unfillable role never shows a red LED. **Drop the "auto-PA /
    derotation" W1 benefit unless `framing.py` integration is in scope** (it is W4, so the benefit is W4's, not W1's).
  *Lean: (a) defer — there is no W1 consumer of a rotator.*
  - **Two DOMAIN consequences of deferring the rotator, flagged so they are not discovered downstream:** (1) **camera PA
    is NOT preserved across a GEM meridian flip** — the field rotates 180° relative to the sensor and, with no rotator,
    the sequence cannot restore the framing angle, so post-flip subs are rotated and the **stacker must derotate** (state
    this in W1.12 too, not silently); (2) **`framing.rotation_deg` is ADVISORY-ONLY until W4.** `compute_mosaic`/framing
    carries `rotation_deg` (`catalog/framing.py:50`, pure-math input) with **no device behind it**, so a planned mosaic
    `rotation_deg` can never be physically achieved and the framing UI must not promise a PA the rig cannot hit. **Size
    the W4 rotator work to include framing/PA integration** (drive `rotation_deg` to a real rotator + restore PA across a
    flip), so the W4 rotator is not under-scoped as a bare device add.
- **Defer `dome`/`roof` and `flat-panel`/`CoverCalibrator` to W4** — all absent from `devices/` today; note them as the
  known future role additions so the multi-file cost is anticipated, not discovered.
  - **Domain consequence (state it, don't discover it downstream): automated flats require the deferred `CoverCalibrator`/
    flat-panel role, so until W4 an unattended/remote rig's flat frames stay a MANUAL on-site step.** `engine.py` already
    supports calibration targets, but **no `CoverCalibrator` device exists anywhere** (absent from `alpaca.py`'s
    `DEVICE_CLASSES`, `base.py`, and `nina.py`), so the sequence engine cannot drive a panel's brightness or open/close a
    cover. For a W3 remote/unattended rig this means flats cannot be automated end-to-end until the W4 role lands — call
    this out so it is a known limitation, not a surprise. **Size the W4 `CoverCalibrator` work to include:** brightness
    control + cover open/close + **sequence-engine integration (an auto-flat target type)** — not merely a bare device
    class. Grounded: `engine.py` supports calibration targets but there is no CoverCalibrator device on the ABC/Alpaca/NINA.

**Registration import site (deterministic `register()`) — `backends/__init__.py` ALREADY EXISTS; the real gap is EAGER
vs LAZY registration.** `devices/backends/__init__.py` is **already committed** and already imports all four backends
(`sim`, `nina`, `native`, `phd2`) by name to trigger their module-level `register(...)`. So the file is not new work.
The remaining gap is WHEN it is imported: today the registry is populated **lazily via `_harness()` (`hub.py:61`) on the
FIRST connect**. But `GET /api/backends → list_backends()` drives the **Settings backend picker**, which the operator
opens **BEFORE any connect** — so on a fresh app `list_backends()` would return an **EMPTY `BACKENDS`** and the picker
would show no backends. Fix: a **single EAGER registration import** at **app-module import OR in `_lifespan`** (before
routes serve) — `import astrodeck.devices.backends` — so `BACKENDS` is fully populated before any request, not just
before the first `connect_profile`. Adding a genuinely new backend (INDI/Rust per W4) STILL requires editing
`__init__.py` to add one `from . import X_backend` line (the "honest cost" note below). Add a **§T4 assertion:**
`GET /api/backends` on a **fresh app with NO prior connect** returns **all four** registered backends (proving eager
registration, not lazy-on-first-connect).

> **Honest cost of "zero hub edits": adding a backend is "new module + `register()` + ONE import line in
> `backends/__init__.py`".** The headline elsewhere in this spec ("new backend = new module + one `register()` call, zero
> hub edits"; "all of W4 is additional registrations — zero hub edits") is **literally true of `hub.py`** (the hub is
> never touched) but understates the seam: `devices/backends/__init__.py` imports the four backends by **hardcoded name**
> to trigger `register()`, so a genuinely new backend (INDI / Rust per W4) requires **editing `__init__.py` to add one
> more `from . import X_backend` line**. State this plainly so "zero edits" is not read as "drop a file in a directory."
> This single deterministic import site is a **deliberate choice** over auto-discovery (explicit, ordered, debuggable
> registration; no import-side-effect surprises). If true zero-edit drop-in becomes a goal, switch to
> `pkgutil.iter_modules` over the `backends/` package or `importlib.metadata` entry-points — but that is an explicit
> trade (loss of deterministic order + harder failure diagnosis), not the current design.

### W1.10 Server-side sun-avoidance / daytime gating (W1 DELIVERABLE — not deferred)

This is a **W1 safety deliverable, not a W2/W4 item.** Today `_check_horizon` (`hub.py:454`) **returns early on a
default site** (`s.get("is_default")`) and otherwise only blocks `alt < 0` — so on the common un-configured bench rig
there is NO protection against slewing at the sun, and `/api/mount/goto`'s `force=true` (`app.py:1113`) bypasses even
the horizon check.

**The guard lives at the MOUNT-MOTION BOUNDARY, not at the `/api/mount/*` route.** A sun cone wired only into
`/api/mount/goto` is **re-entered unguarded** by internal slews: `POST /api/sequence/start` with `force=true` bypasses
`_horizon_block` (verified `app.py:1316-1339`, `if not force:`) and then drives `goto_and_center` per target;
`meridian_flip` → `goto_and_center` (`hub.py:1000`) re-slews with no horizon/sun check; `POST /api/polar/*` rotates RA.
So the sun-exclusion check is enforced **inside the hub/orchestrator device-motion methods — `goto_and_center`
(`hub.py:943`) and `move_axis`/`slew`** — so that **sequence-driven, polar-driven, and meridian-flip re-slews all
inherit it**, regardless of which route initiated them. The `/api/mount/*` routes call those same methods, so they are
covered too; the route-level check (if any) is convenience, not the floor.

**Solar-scope aware: ON by default, deliberately disableable for solar astronomy.** The cone protects normal deep-sky
gear from the Sun, but a **solar scope behind a proper full-aperture solar filter WANTS to point at the Sun** — so the
gate is not an immovable hard floor, it is a **default-on safety that an authenticated admin can turn OFF for a solar
session**. Two distinct config knobs on `SafetyConfig` (`config.py:85`):
- `solar_avoidance: bool = True` — the master enable. `True` (default) = cone armed; `False` = solar-astronomy mode,
  cone inert. **Flipping it (in either direction) requires `config.solar_override`** (route-level, like other
  privileged config writes — see the field-level RBAC below), so it is a deliberate admin toggle, never a stray
  capture-tier write.
- `solar_exclusion_deg: float = 30.0` — the cone half-angle. `30` is grounded in the W1.10 arch-page sun-cone note
  (the diagram labels a "~30° exclusion cone"); `ge=0, le=90`. Also gated by `config.solar_override`.

Add an explicit **`_check_solar`** guard (`hub.py`, signature `_check_solar(ra_hours, dec_deg, *, force=False)`) that:
- Is **INERT when `solar_avoidance is False`** (solar-session mode) and a no-op when `solar_exclusion_deg <= 0`.
- Rejects any **goto/center** AND any **manual move** (`/api/mount/move`, the SlewPad) whose **target OR current**
  pointing falls within the **sun-exclusion cone** (`solar_exclusion_deg`) of the computed Sun position.
- **Computes the separation in RA/Dec, NOT alt-az — so it is genuinely site-independent.** The Sun's apparent RA/Dec
  is date-only (observer parallax is ~8.8 arcsec, negligible against a 30° cone), so the cone works on a default site
  with no lat/lon. **Reuse the EXISTING sun helper — do NOT add a new sun-position function:** `sun_radec`
  (`catalog/coords.py:81`) returns `(ra_hours, dec_deg)` from `unix_time` alone and already backs `sun_altaz`/
  `dark_window` used by `app.py` and `sequence/schedule.py`. The guard calls `sun_radec(unix_time)` (pass `unix_time`
  so it is test-clockable, §T1 sun-guard tests) and reuses the EXISTING `_ang_sep_deg(ra1_h, dec1, ra2_h, dec2)`
  (`hub.py:1587`) for the great-circle separation `sep = _ang_sep_deg(ra_hours, dec_deg, sun_ra, sun_dec)`; reject when
  `sep < solar_exclusion_deg`. (The poller-drift case below reuses the same `sun_radec` against the mount's CURRENT
  RA/Dec — no alt-az, no lat/lon dependency anywhere in the gate.)
- Is **INDEPENDENT of `site.is_default`** — because it is RA/Dec-based it needs no site at all, and the daytime-bench
  accident is exactly the default-site case the old `_check_horizon` skips.
- The `force=True` argument **does NOT bypass the cone** — it is threaded only so callers share one signature with
  `_check_horizon`; the sun cone is bypassed ONLY by `solar_avoidance=False` (admin-set via `config.solar_override`),
  never by a per-call `force`.
- **`/api/mount/goto` `force=true` MUST NOT bypass the sun guard** (it may still bypass the visible-horizon check, but
  the sun cone stands unless `solar_avoidance` is False).
- **`POST /api/sequence/start` `force=true` IS subject to the sun cone.** Because `_check_solar` sits inside
  `goto_and_center` (the motion boundary, `hub.py:1186`), a forced sequence that bypasses the route-level
  `_horizon_block` (`app.py:1733`) STILL hits the sun cone on every per-target slew and meridian-flip re-slew. A
  sequence runs into a sun-adjacent target ONLY when `solar_avoidance` is already False (admin-set via
  `config.solar_override`) — `force` alone cannot disarm it. This closes the dawn-during-unattended-sequence path.
- **A PERIODIC sun-proximity check on the status poller catches a TRACKING mount drifting into the cone — the
  motion-boundary guard alone structurally cannot.** `_check_solar` is **slew-triggered** (it fires at goto/move/
  connect), so it cannot see a target that was CLEAR at flip/slew time **drift into the cone over the following hour via
  sidereal tracking** — the dawn-overtakes-a-tracking-mount path on an unattended run. Add a **periodic check on the
  status poller** (`_status_loop`, `hub.py:1324`) that reuses `sun_radec` against the mount's **current RA/Dec** (same
  cone half-angle, same `unix_time`-clockable helper) and, when `solar_avoidance` is True and a **tracking** mount's
  current pointing crosses INTO the cone, fires a **SAFE-ING halt** (stop tracking, optionally park, escalate to the
  persistent chip + alerts/escalation sink). Resume from that halt is gated by `config.solar_override` (re-arming
  motion in a daytime sky is a privileged action). This makes the sun floor cover BOTH the slew-initiated case (motion
  boundary) AND the drift-over-time case (poller), so a clear-at-flip target overtaken by dawn during an unattended
  sequence is halted, not cooked. The poller halt is also inert when `solar_avoidance` is False (a solar session
  legitimately tracks the Sun).

**Override semantics (decided).** The gate has exactly ONE disarm path and ONE privileged action:
- **Disarm = `solar_avoidance=False`**, written through `POST /api/config` `safety` block. The `safety` block already
  requires `config.safety`; the TWO sun fields (`solar_avoidance`, `solar_exclusion_deg`) carry an ADDITIONAL
  field-level requirement of `config.solar_override` (mirroring the existing `site.horizon_min_deg → config.safety`
  nested field-level rule in `_check_config_caps`, `app.py:806`). So toggling sun-avoidance needs BOTH `config.safety`
  (the block) AND `config.solar_override` (the field) — admin-only, deliberate.
- **No per-call `force` bypass.** Neither `goto force=true` nor `sequence/start force=true` disarms the cone — they only
  bypass the visible-horizon check. The cone is governed solely by the persisted `solar_avoidance` flag.
- **Resume-after-halt** (connect-time SAFE-ING halt and the poller-drift halt) is gated by `config.solar_override` as
  before. Disarming via `solar_avoidance=False` is the standing solar-session mode; the resume cap is the per-incident
  unblock.

**UI:** two surfaces, both pinned to EXISTING components (no new component):
- A distinct, loud, persistent **`DAYTIME / SUN-EXCLUSION`** state (its own banner/state, not a generic toast) that
  names the blocked target's separation from the Sun and the override path. Required to remain shape/letter
  distinguishable from REAL-MOTION at night (the §6 red-on-red note).
- A **"Solar astronomy mode (disable sun avoidance)" toggle in Settings → Safety**, beside the horizon/pier floor
  controls, rendered as a DESTRUCTIVE-tier toggle (`config.solar_override` is in `DESTRUCTIVE_CAPS`,
  `capabilities.py:45`) so it double-confirms and names the consequence ("the mount may now slew at the Sun — only do
  this with a solar filter installed"). Shown disabled-with-explanation when the caller lacks `config.solar_override`.

This ties directly to the W1.6 auto-connect posture (mount comes online PARKED, tracking OFF; a discovered
tracking-into-the-sun mount is HALTED when `solar_avoidance` is True) so a boot never slews toward the Sun unattended,
while a deliberately-configured solar rig (`solar_avoidance=False`) is left free to track it.

### W1.11 Per-role capability advertisement (cooling / dew / pier-side / FOV provenance)

The `Backend`/`BackendSession` protocol exposes only `get_device`/`native_guider`/`native_solver`/`health`/`close` —
**no capability advertisement** — so the W1.C picker can't tell the user *pre-connect* that a chosen mount won't report
pier side (which makes the `config.safety` pier-collision enforcement inert) or which camera supports cooling. The
device classes already carry the flags (`Camera.can_cool`/`has_dew_heater` at `base.py:113`,
`Telescope.reports_destination_pier_side` at `base.py:152`). Spec:
- `ConnectResult` exposes, per connected role, the device's `describe()` + its capability flags (keep the flags on the
  device classes) so the picker and the W2 mount-limits gate can read them.
- **Dew-heater ownership** is documented in ONE place: camera-integrated (`Camera.set_dew_heater`) vs a Switch port —
  the picker shows which owns it so it isn't double-listed.
- **Filter-offset → focuser ownership on a MIXED rig — documented like dew-heater ownership.** `FilterWheel` carries
  per-filter focuser offsets (`filter_offsets`, `base.py:242`), and W2.1 lists "filter-change focuser-offset moves" as a
  transitive mount/focuser coupling — but the per-role override model lets `filterwheel` and `focuser` resolve to
  **different backends/sessions**, and the spec must say WHO applies the offset move. Decide and document:
  - **A backend that internally couples filter+focuser (NINA applies the offset itself) advertises that** via a capability
    flag (e.g. `FilterWheel.applies_focuser_offset = True`); native Alpaca does NOT couple them.
  - **On a SAME-backend rig** the coupling backend (NINA) applies the offset move itself; AstroDeck does NOT also move the
    focuser (no double-apply).
  - **On a MIXED rig** (filterwheel and focuser on different sessions — or a natively-uncoupled backend like Alpaca), the
    **hub/orchestrator owns the offset move** (it holds both `hub.rig['filterwheel']` and `hub.rig['focuser']`), exactly
    like the cross-backend meridian-flip coordination (W1.12). It reads the target filter's offset and drives the
    `focuser` role's `move`.
  - **The flag prevents double-correction:** when the active filterwheel backend advertises `applies_focuser_offset`, the
    hub SKIPS its own offset move; otherwise the hub applies it. So a natively-applying backend is never double-corrected
    and a non-applying backend's offset is never silently skipped.
  - **The double-apply ALREADY EXISTS in committed `sequence/engine.py` — name the exact consumer.** `engine.py`'s
    `_apply_filter` (`engine.py:1358-1364`) does `await foc.move_to(pos + delta)` **UNCONDITIONALLY** whenever
    `self.plan.apply_filter_offsets` is set and a focuser is present — it does NOT consult any
    `FilterWheel.applies_focuser_offset` flag. So a **NINA-primary rig is double-correcting focus on EVERY filter change
    TODAY** (NINA moves the focuser by the offset, then AstroDeck's engine moves it AGAIN by the same delta) — this is a
    present bug, not a future risk. The fix lands in `engine.py:_apply_filter`: **gate the `foc.move_to(pos + delta)` on
    NOT `fw.applies_focuser_offset`** (skip the engine's move when the active filterwheel advertises it applies the offset
    itself; apply it otherwise). **§T test:** assert the engine **SKIPS** its offset move when the active filterwheel
    advertises `applies_focuser_offset` (the focuser `move_to` is NOT called for the offset) and **APPLIES** it (calls
    `move_to(pos + delta)`) when the flag is absent/False. Grounded: `engine.py:1358` is the unconditional move.
- Do NOT conflate the `backend_links` link-health array (W1.5) with capability/cooling readout (which flows through
  `poll_status`).
- **FOV/pixel-scale provenance on override:** on connect AND on any per-role **camera** override, the orchestrator/hub
  **re-derives** the effective pixel scale + FOV hint from the ACTUAL connected **IMAGING** camera
  (`sensor_width`/`sensor_height`/`pixel_size_um`), never a stale profile — so a mixed real-mount/sim-camera bench rig
  does not feed a sim "solve from hint" that fake-centers a real mount (the SimSolver guard hazard, W1.5/W1.10).
  **This is the IMAGING train ONLY and is DISJOINT from the GUIDE pixel scale.** The guide camera + guide-scope are a
  separate optical train; `PHD2Guider.pixel_scale` (`guide/phd2.py:64`) — which scales guide RMS and `dither` — must NOT
  be set from this imaging-camera re-derivation (it would be wrong by the guidescope/mainscope focal ratio, commonly
  3-5×). Guide pixel scale comes from a separate **guide-optics record** on the profile/`RigSpec` (W1.7 step 2),
  preferably read back from PHD2's own calibration. The two scales never cross-feed.
- **Cooling is on/off only today — a thermal-RAMP + warmup-before-disconnect contract is a KNOWN GAP (acknowledge at
  minimum).** `Camera.set_cooler(on, target_c)` (`base.py:134`) is a **single set-point call with no ramp**. The W1.3
  per-`(host,port)` teardown rewrite (`disconnect_all` / close-before-open) **abruptly cuts cooler power on `close()`**,
  which **warms a cooled sensor uncontrolled and fast** — and rapid temperature swings (more than a few °C/min) damage
  cooled CMOS/CCD sensors (thermal shock, condensation). The abstraction needs a **cooling-ramp contract**: a
  target-temperature ramp on BOTH cooldown AND warmup bounded to a safe rate (e.g. ≤ a few °C/min), and a **graceful
  warmup-before-disconnect step** in `close()`/`disconnect` (ramp the setpoint toward ambient before cutting power),
  rather than on/off. At MINIMUM acknowledge this as a known gap so the teardown rewrite (W1.3) does not ship an abrupt
  cooler-cut as final behavior. (This is independent of any RBAC thermal-shock concern — it is a device-lifecycle
  contract.) Grounded: `base.py:134` is a single set-point with no ramp; nothing in `close()`/`disconnect` warms before
  cutting power.

### W1.8 Staging (each stage ends green: `pytest` + `npm build`)

| Stage | Content | Green gate |
|---|---|---|
| **A — abstraction (additive, behavior-preserving)** | the 4 wrapping backends (exist; `native_backend.py` REWRITTEN for the connection pool, W1.2), `devices/backends/__init__.py` import site, **`orchestrator.assemble` RENAMED+RESHAPED to `connect_profile`/`ConnectResult`** (W1.3.0) + sun guard at the motion boundary (W1.10). Hub gains `rig`/`sessions`; old connect paths reimplemented over `connect_profile` but emit identical summaries. No UI change. | existing `tests/` pass unchanged; **W1.5 characterization snapshots taken against the committed `assemble()` path** BEFORE the refactor, re-asserted against `connect_profile` after; **`test_orchestrator.py` REWRITTEN as the §T2 fault-injecting C1 gate** with `to_dict`-shape back-compat carried forward; per-wrapper `test_{sim,nina,native,phd2}_backend.py` (§T1, incl. native `close()`-actually-closes + roles-match-served); **`test_sun_guard.py`** (§T1.10). |
| **B — profiles + persistence + boot auto-connect** | `RigSpec`↔Profile (+ legacy migration), `/api/backends`, `/api/connect/rig`, `_lifespan` auto-connect (failure-swallowing) + parked/tracking-off posture + connect-time SAFE-ING halt (W1.6) + per-role-`RoleResult`-for-every-requested-role surface. | `test_profiles.py` round-trips a multi-backend RigSpec + legacy-schema migration (the migration test — incl. the `alpaca`→`native` alias-not-`KeyError` assertion — is OWNED by `test_profiles.py`, cross-referenced from §T4(5)); **C2** boot-connect-failure-isolation + mixed-rig integration + connect-API route tests (§T4). **Gate: `connect_profile` emits a `RoleResult` per requested role (tri-state basis).** |
| **C — settings / Profiles UI** | Settings backend picker (primary + per-role override rows, resolved-backend display, mixed real/sim confirm, preflight reuse), per-role REAL/SIM badges, managed-PHD2 toggle, `backend_links` health readout, per-role boot-result LEDs. | `npm run build` **+ UI unit tests** (vitest or extracted pure-store fns, §T8) — `npm run build` alone is NOT the gate. |
| **D — decouple the mode-branches** | retire ALL `self.mode`/`sim_rig`/`nina_client` reads (W1.5); `backend_links` array; `hub.mode` becomes a derived label; per-role SimSolver guard. | full `pytest` green with NINA/native/sim/mixed rigs; **source-grep guard test** asserts no `self.mode ==` left except the label assignment. |

> **W1-window LAN-anonymous acknowledgement (ties to W1.7 hard-gates a/b/c).** Stages B/C ship `/api/connect/rig`, the
> generic `/api/discover/{backend}`, and managed-PHD2 spawn **before W2's RBAC (C6)**. Under the `none` default every LAN
> caller is `admin`, so these privileged sinks are **anonymous-LAN-reachable for the whole W1→W2 gap**. The mitigations
> are NOT deferred to W2: the discover-route `validate_scan_host` guard and the managed-PHD2 argv/path/port allowlist land
> WITH their stages (B and C5 respectively), and the recommendation is to ship the **C6 `none`-default RBAC scaffold
> (route table + boot assertion, behavior-preserving) before/alongside C5** so `config.backend` gates connect + managed-
> PHD2 the moment those sinks exist.

### W1.C Per-role backend picker — concrete UX (NEW; no existing pattern)

There is **no Settings/Profiles view in `ui/src` today** — `ConnectView` is a flat connect-button list. This is a
new, high-error-surface UI; spec it concretely:
- Each **role row shows its RESOLVED backend**, including inheritance — e.g. `Guider: inherits primary → sim`,
  `Mount: native (override)` — so an **unintended sim/real mix is visible at a glance**.
- A **summary line before Connect**: `Mount: REAL native · Camera: SIM · Guider: PHD2 (managed)`.
- **The danger hold-confirm fires for ANY rig containing a REAL motion device (mount OR focuser) — not only the mixed
  real-motion+sim-imaging case.** The original gate confirmed only the half-sim bench combo, leaving a **fully-real rig
  (real mount + real camera) with NO extra confirmation** even though it is MORE dangerous (everything moves physically).
  So require the **`confirmDialog` hold mode (tone `danger` + `HoldButton`)** whenever the resolved rig contains a real
  mount or real focuser, with a **one-line consequence summary** matched to the rig: e.g. "This connects REAL mount +
  REAL camera — motion will be physical" (all-real) or "REAL mount + SIM camera — fake solves cannot center this mount"
  (mixed). **Sim-only connects stay frictionless** (no hold). The mixed bench case is one instance of the broader
  "any real motion device" rule, not a special case.
- **Reuse the existing `PreflightModal`/Checklist** as the connect preflight rather than inventing a connect flow.
- **Carry per-role `RoleResult` errors back into the rows** (the `results` array from `connect_profile`) so a failed
  role shows its error inline, not a generic toast.
- **Reject an override whose role is not in the chosen backend's `roles`** (e.g. a `safety` override pointing at NINA)
  in the client AND server.
- The **managed-PHD2 toggle** produces `ConnSpec.extra["managed"] = true`. **It is hard-disabled whenever any role
  resolves to NINA** (W1.7 managed-PHD2/NINA collision); show the disabled toggle with a "NINA owns PHD2" explanation.
- **Discovery → override-row binding (the picker can't assume the user knows host/port/dev_num).** Discovery is
  per-backend, async, and can return zero devices or a device whose `DeviceType` maps to no `ROLES` entry (today's
  ConnectView "not a supported role yet" case). Spec the binding concretely:
  - Each override row offers a **"choose from discovered"** affordance backed by `GET /api/discover/{backend}` (W1.6),
    with explicit **empty / scanning / error sub-states PER ROW** — NOT a single page-level scan whose error vanishes as
    a toast.
  - **Carry the DIFFERENTIATED discovery error into the row** (unreachable host vs reached-but-not-Alpaca vs timeout) —
    not a vanishing toast — so the operator can tell a wrong IP from a wrong port from a dead device.
  - Define behavior when a **discovered device's type maps to no role** (offer it as "unsupported role" / non-selectable,
    matching ConnectView), and when **two roles want the same physical device** (warn / disallow the duplicate binding).
- **First-run / migration onboarding — DON'T drop an upgrading operator onto a blank picker.** Today an operator clicks
  "Connect Simulator Rig" / "Bridge to NINA" on the flat `ConnectView`; post-W1 they land on a substantially more complex
  primary + per-role-override picker with resolved-backend rows, badges, and a managed-PHD2 toggle. §T4(5) covers the
  DATA migration but nothing covers the **user's mental-model migration** (e.g. picking `native` primary without
  understanding it means direct-Alpaca-no-NINA). So: on **first load (or when a legacy profile is migrated)**,
  **pre-populate the picker FROM the migrated `RigSpec`** so the existing rig shows as **already-configured** (primary +
  roles filled, resolved-backend rows populated) — NOT a blank picker. Show a **one-line explainer per primary option**
  ("NINA bridge — fly your existing NINA rig"; "Native — direct Alpaca, no NINA"; "Simulator — no hardware"). Map the
  old flat-list affordances to obvious picker **presets** (the "Connect Simulator Rig" button → a `primary=sim` preset;
  "Bridge to NINA" → a `primary=nina` preset). This is cheap — it reuses the migration §T4(5) already requires — and it
  is the difference between an upgrade that "just works" and one where the operator faces an empty, unfamiliar form.

### W1.12 Meridian-flip guider-calibration across backends (W1 contract; W4 native impl)

`meridian_flip` (`hub.py:978`) calls `guider.flip_calibration()` **unconditionally** today (`hub.py:1000`, guarded only
by a `callable()`/try-except so a missing method or a sim no-op can't break the flip; each guider self-guards
internally). Cross-backend coordination is the orchestrator/hub's job, not the guider's:
- **The pier-side gate must be INVERTED — flip UNLESS pier side is KNOWN-and-unchanged (see W1.13).** The naive "flip
  only on a CONFIRMED change" gate is MORE dangerous than today's unconditional flip on the common non-reporting GEM, so
  W1.13 specifies the exact gate. Do NOT build a strict "confirmed change" gate.
- **Guider and mount can live on DIFFERENT backends** (e.g. PHD2 guider + native mount); the flip coordination crosses
  session boundaries, so the hub/orchestrator owns it (it already holds both `hub.guider` and `hub.rig["telescope"]`),
  not the guider.
- **W4 `NativeGuider.flip_calibration`** must be defined: negate the RA (and, per its calibration model, possibly Dec)
  calibration vector. (`PHD2Guider.flip_calibration` at `guide/phd2.py:257` and `NinaGuider` at `nina.py:664` already
  exist; the sim guider's is a no-op — all three satisfy the same `Guider.flip_calibration` ABC at `guide/base.py:50`.)
- **Camera PA is NOT preserved across the flip (rotator deferred to W4).** A GEM flip rotates the field 180° relative to
  the sensor; with the rotator role deferred (W1.9) the sequence cannot restore the framing angle, so **post-flip subs
  are rotated and the downstream stacker must derotate**. `framing.rotation_deg` (`catalog/framing.py:50`) is
  advisory-only until the W4 rotator role lands. Note this here so the meridian-flip path does not imply PA is held.

### W1.13 Meridian-flip pier-side gate — INVERTED (flip on UNKNOWN; the non-reporting-GEM safety case)

A strict "flip the guider calibration ONLY on a CONFIRMED pier-side change" gate is **more dangerous than the current
unconditional flip** and would strand the most common real mount. Grounding: `Telescope.pier_side` /
`destination_pier_side` default to **UNKNOWN** with `reports_destination_pier_side = False` (`base.py:152`) — the common
real GEM does not report a confirmed side. On such a mount a "flip only on a CONFIRMED change" gate would **SKIP the
flip** after a real physical pier swap, and guiding would then run with the pre-flip calibration → **BACKWARDS / runaway
RA**. So:
- **GATE THE INVERTED RULE ON A POSITIVE "THIS MOUNT PERFORMS GEM FLIPS" SIGNAL FIRST — else it back-fires on fork/
  alt-az/harmonic mounts.** The inverted "flip-on-UNKNOWN" rule is correct ONLY for a German equatorial. On a **fork /
  alt-az / harmonic** mount that never physically flips, "treat UNKNOWN as flip" would FLIP the guider calibration on a
  mount that did not move pier sides — inducing the exact backwards/runaway-RA outcome the gate exists to prevent, in the
  OPPOSITE direction. The "mount positively advertises it does NOT flip" escape clause **has no backing flag today** —
  only `reports_destination_pier_side` exists (`base.py:152`), which is about whether pier side is *reported*, not about
  whether the mount *flips*.
- **The GEM/non-GEM classifier MUST NOT use `time_to_meridian_flip() is None` as the non-GEM signal — that property is
  NINA-only and mis-classifies the W4/test-target GEMs.** `Telescope.time_to_meridian_flip()` is overridden **solely in
  `nina.py:429`**; `base.py:202` returns `None`, and **neither `SimTelescope`** (`sim.py:241`, explicitly "a German
  equatorial that reports DestinationSideOfPier") **nor Alpaca/ASCOM telescopes** (there is no standard
  `TimeToMeridianFlip` ASCOM property) override it. So a real native-Alpaca GEM and the sim GEM — exactly the rigs W4 and
  §T1.12 target — return `None` from `time_to_meridian_flip()` and would be mis-classified as non-GEM, **NEVER getting
  their guider calibration flipped: the precise backwards/runaway-RA hazard this section exists to prevent.** This also
  directly contradicts the already-committed `hub._compute_meridian`, which detects a GEM via `_is_gem(side)` =
  `pier_side ∈ {east, west}` (`hub.py:1217-1220`, `:1244-1246`) **precisely because an Alpaca/sim GEM returns `None` from
  `time_to_meridian_flip()`** and derives hours-to-flip from the hour angle instead (`hub.py:1238-1243`).
- **Gate the flip-calibration path on a positive does-GEM-flip signal that AGREES with the committed `_compute_meridian`.
  The classifier is `_is_gem(side) OR does_meridian_flip_flag` — NOT either branch standing ALONE.** `_is_gem(side)`
  alone (`hub.py:1217-1220`) returns `side ∈ {east, west}`, so `_is_gem("unknown")` is **False** — but the common real
  GEM is a **non-reporting / UNKNOWN-pier** mount (`base.py:190-191` `pier_side()` → `UNKNOWN`; `base.py:152`
  `reports_destination_pier_side = False`). Classifying such a GEM by `_is_gem(side)` alone marks it **non-GEM** and
  (under step (1) below) NEVER flips its calibration — the exact backwards/runaway-RA hazard this section exists to
  prevent. So `_is_gem(side)` is **NOT** an acceptable standalone classifier; the does-GEM-flip signal **MUST** be
  `(_is_gem(side)) OR (positive does_meridian_flip / is_german_equatorial flag)`.
- **Add the MANDATORY `does_meridian_flip` / `is_german_equatorial` capability flag to the `Telescope` ABC.** None
  exists today (grep confirms only `reports_destination_pier_side` at `base.py:152` — that flag is about
  `destination_pier_side` support, NOT meridian-flip class). The native Alpaca backend **sets it from ASCOM
  `AlignmentMode == germanPolar`**; `SimTelescope` **sets it `True`**. The flag is **NOT optional/unset** — an unset
  optional flag collapses back to the `_is_gem(side)`-only mis-classification (or the `time_to_meridian_flip() is None`
  mis-classification) above. A mount with `pier_side` **UNKNOWN AND no positive flip flag** is the **only** true non-GEM
  (a fork / alt-az). The decision tree is: **(1) if the mount does NOT do GEM flips (NOT `_is_gem(side)` AND no positive
  flip flag — and NOT keyed off `time_to_meridian_flip() is None`) → NEVER flip the calibration** (a fork/alt-az never
  needs it); **(2) for any GEM — `_is_gem(side)` true, OR the flip flag set even when pier side is UNKNOWN / non-reporting
  and `time_to_meridian_flip()` returns `None` — apply the inverted rule** — FLIP UNLESS pier side is KNOWN-and-UNCHANGED.
- **Invert the gate (GEM only): FLIP the calibration UNLESS the pier side is KNOWN-and-UNCHANGED** (both pre- and
  post-slew sides reported and equal). **Treat UNKNOWN / non-reporting as "flip"** — for a GEM. This makes the dangerous
  default (skip on a real non-reporting GEM) impossible while the step-(1) GEM gate makes the opposite failure (flip a
  fork mount) impossible too.
- **Keep each guider's internal self-guard as the SECOND line of defence** (the guider may still no-op if it knows it
  cannot be backwards) — the hub-level GEM-gated inverted gate is the FIRST line.
- Spell this out so the implementer does NOT build a "flip only on confirmed change" gate that silently strands
  non-reporting GEMs into backwards guiding, AND does not build an ungated inverted gate that flips a fork mount, AND does
  not key the GEM gate off `time_to_meridian_flip() is None` (which would strand the sim/Alpaca GEMs). Tested by §T1.12:
  the cross-backend pier-gate test PLUS a **non-flipping fork/alt-az mount** case (distinguished as **pier_side ==
  UNKNOWN AND not a flagged/`_is_gem` GEM**) asserting `flip_calibration` is **NOT called**, PLUS an **Alpaca/sim GEM that
  returns `None` from `time_to_meridian_flip()` but reports pier_side east/west** asserting the inverted FLIP-UNLESS-
  known-and-unchanged rule **STILL fires** (the mis-classification regression guard).

---

## W2 — RBAC (capability-based)

### W2.1 Capabilities & roles

Roles are **bundles of capabilities**, not hardcoded checks. A route declares the capability it needs; the role just
determines which capabilities a caller holds. **The capability set is built by enumerating EVERY mutating route in
`app.py`, not an abstract taxonomy** — the table below is reconciled against the real route surface.

**Capabilities:**

| Capability | Guards (real routes) | Notes |
|---|---|---|
| `view.status` | read-only status + WS subscribe (`GET /api/status`, `/ws` accept) — live telemetry, NO bulk media | every authenticated caller; the floor a viewer link holds |
| `view.preview` | downsized preview frames (`GET /api/preview/{id}/lossless.png`, `/thumb.jpg`, `/png`) — NOT raw FITS | in the default viewer-link set (live-watch) |
| `view.media` | **full-res / raw science frames: `GET /api/preview/{id}/fits`** (`app.py:1044`/`:1057`, raw FITS, tens of MB) and any full-res science route | **NOT in the default viewer-link set** (W3.3); a viewer link grants it only by explicit opt-in |
| `view.site_precise` | precise site lat/lon in the `hub.summary()` WS hello + `config` bus frames (`hub.py:404`) | **NOT in the default viewer-link set**; absence → coarsened/redacted coordinates (W3.3 redaction) |
| `control.capture` | capture/loop, autofocus; **`POST /api/camera/cooler`** (`app.py:1084`), **`POST /api/camera/dew-heater`** (`app.py:1093`), **`POST /api/focuser/halt`** (`app.py:1225`), **`POST /api/filterwheel/position`** (`app.py:1240`) | drive **imaging** only — NOT motion. (Consider carving `camera/cooler` into its own cap if thermal-shock by a low-trust operator is a concern; listed here so the boot assertion has a declared mapping, not an inferred one.) |
| `control.mount` | mount **MOTION** — EVERY route that ultimately calls `Telescope.slew`/`move_axis`/`set_tracking`/`park`/`unpark`/`pulse_guide`: `/api/mount/goto` (`:1104`), `/api/mount/move` (`:1137`), `/api/mount/tracking` (`:1178`), `/api/mount/slew`, park/unpark, **`POST /api/mount/stop`** (`:1159` — cancels goto/solve + commands `tel.stop`), sync, **`POST /api/mount/solve_sync`** (`:1129` — writes a sync to a REAL mount), **`POST /api/sequence/{start,pause,resume,abort}`** (`:1307`/`:1347`/`:1352`/`:1357` — the engine slews+centers+meridian-flips per target), **`POST /api/sequence/recover`** (`:1440` — calls `engine.start`, the SAME slew+center+meridian-flip entry as `sequence/start`), **`POST /api/polar/{start,stop,pause,resume}`** (`:1459`+ — rotates RA) | **split out of `control.capture`** so a capture role can't drive a real mount remotely. Sequence + polar (and `sequence/recover`, `mount/solve_sync`, `mount/stop`) are motion-capable and MUST carry `control.mount` (or a `control.sequence` sub-cap), NOT `control.capture` — see the privilege-escalation note below. |
| `control.guide` | start/stop/**dither** guiding | `dither` PULSES the mount (`pulse_guide`) — a bounded, rate-limited accepted exception (see transitive-coupling note). |
| `control.power` | `POST /api/switch/set` (`:1258`) | a switch toggle can **brown out the rig mid-slew** — its own cap |
| `config.safety` | the REAL pier-collision + safety knobs: `nogo_box`, `enforce_pier_limits`, `min_alt_deg`, horizon control points (all in `SafetyConfig` under `config.safety`), deadman; **`POST /api/safety/simulate`** (`app.py:691`) | **DESTRUCTIVE** — these are the true pier/horizon floors. `safety/simulate` calls `force_safe()`/`force_unsafe()` on the LIVE `SafetyMonitor` (`force_safe` SUPPRESSES the `on_unsafe` pause/park) — it can DISARM the fail-closed engine, so it is `config.safety`-gated AND its sim-only guard is re-keyed off the camera/primary SESSION identity (NOT the retired `hub.mode`, `app.py:697`; added to the W1.5 inventory) so it can never become reachable on a real rig. The boot assertion flags it. |
| `config.solar_override` | the W1.10 daytime/sun-exclusion override + `goto force=true` (horizon-bypass) + **forced `sequence/start`** (sun cone) | dedicated override cap; NOT implied by `control.mount`. A forced sequence cannot bypass the sun cone without it (W1.10). |
| `config.backend` | backend/profile/device connect & apply (W1), **managed-PHD2 launch** (process-spawn) | privileged precisely because it controls process launch + profile-sync (W1.7) |
| `config.site_optics` | site coords + optics; **note `PUT /api/site` also rides `horizon_min_deg`** (a safety floor) — see field-level note in W2.2 | |
| `config.alerts` | alert sinks, escalation (`POST /api/alerts` `:724`, `POST /api/alerts/{id}/test` `:756`, `DELETE /api/alerts/{id}` `:749`), **`deadman_url`** | **SSRF + credential-exfil sink (privileged).** `POST /api/alerts` stores attacker-controllable `url`/`token`; `.../test` makes a REAL outbound request to it; `deadman_url` is an outbound ping. Under W3 WAN exposure this is an SSRF primitive (outbound to internal hosts / cloud metadata) and a credential-exfil path (re-point a sink, read the delivered payload). Enforce the EXISTING `alerting._url_is_safe` (`alerting.py:69-103`) at the route/config-merge layer (reuse it, do NOT re-mirror `validate_scan_host`): reject loopback / private / link-local / metadata before any outbound test/ping for ALERT/webhook URLs (`allow_private=False`). **The `deadman_url` keeps the committed `allow_private=True` exception** (`alerting.py:459` — a private-LAN self-hosted monitor is legitimate), but **multicast / reserved / unspecified blocks STILL apply to the deadman URL** (those are never a legitimate monitor target). **`config.alerts` is excluded from every viewer-link and operator capability set.** |
| `admin.users` | user→role allowlist, auth provider config | |

> **`config.mount_limits` removed.** As originally written it guarded an **empty route set** — there is NO
> set-limits/home route in `app.py`. The real pier-collision knobs (`nogo_box`, `enforce_pier_limits`, `min_alt_deg`,
> horizon control points) live in `SafetyConfig` under **`config.safety`** (set via `POST /api/config`), so
> `config.safety` IS the destructive-pier capability. Mount *motion* is a separate runtime concern, hence the new
> **`control.mount`**.

> **PRIVILEGE-ESCALATION FIX — `control.mount` is re-derived from the MOTION boundary, not a hand-picked route list.**
> The original `control.mount` enumerated only goto/move/slew/park/unpark/stop/sync/solve_sync — but `POST
> /api/sequence/{start,pause,resume,abort}`, `POST /api/sequence/recover` (`:1440`), `POST /api/polar/{start,stop,pause,
> resume}`, and `POST /api/mount/tracking` ALL drive a real mount (the sequence engine slews+centers+meridian-flips per
> target; `sequence/recover` re-enters `engine.start`; polar rotates RA). An `operator` holding only
> `control.capture`+`control.guide` could therefore drive a real mount across the sky — breaking the exact
> invariant the `control.mount` split exists to enforce. So `control.mount` is re-derived by enumerating **EVERY route
> whose handler transitively calls `Telescope.slew`/`move_axis`/`set_tracking`/`park`/`unpark`/`pulse_guide`** (sequence
> engine + polar session included).
>
> **The boot assertion is BOTH list-based AND transitive — and the transitive layer is an EXPLICIT route→motion-sink
> DECLARATION, NOT runtime call-graph inference.** Runtime call-graph analysis at `create_app()` time is infeasible
> (FastAPI cannot introspect which methods a handler will transitively call). So pin the mechanism as a **declaration**:
> each motion-capable route is **annotated/registered as reaching a named motion sink** (a small per-route metadata tag,
> e.g. `reaches={"SequenceEngine.start"}`), and the boot check asserts **every route DECLARED as reaching a motion sink
> carries `control.mount`** (or `control.sequence`). Maintain the **explicit motion-sink allowlist**:
> `Telescope.slew`/`move_axis`/`set_tracking`/`park`/`unpark`/`pulse_guide` **AND `SequenceEngine.start`** (the
> `sequence/start` + `sequence/recover` entry) **AND the polar-session start**. This makes §T6(2) DETERMINISTIC: a route
> **DECLARED as reaching `SequenceEngine.start` but tagged `control.capture`** FAILS the boot — no inference needed.
> **The two layers COMPOSE:** a NEW motion route added **without the `reaches=` declaration** is still caught by the
> all-mutating-routes-need-a-declared-capability assertion (it would have SOME cap or fail the presence check), and a
> motion route with the WRONG cap is caught by the declaration layer — so neither an undeclared nor a mis-tagged motion
> route can slip through. Tested in §T6(2): a `control.capture`-tagged route DECLARED reaching `engine.start` (e.g. a
> mis-tagged `sequence/recover`) must FAIL the boot check.

> **Transitive mount-motion couplings (the operator boundary, decided).** `control.guide`'s `dither` pulses the mount,
> and a sequence (were it `control.capture`) commands `goto_and_center`/`meridian_flip`/`solve_sync` + filter-change
> focuser-offset moves — so an imaging-only tier can still cause real-mount motion. The decided policy: **sub-gate
> sequence + polar mount motion behind `control.mount`** (above), so the operator tier (`view`+`control.capture`+
> `control.guide`) can run imaging and guiding (bounded, rate-limited `pulse_guide` via `dither` is the ONE accepted
> motion exception) but **cannot start a slewing sequence**. A forced sequence-run can NEVER bypass the sun guard for an
> operator (W1.10; requires `config.solar_override`, which the operator lacks).

**Roles (capability bundles):**

```python
# 'view' is SPLIT (W2.1/W3.3) into view.status / view.preview / view.media + view.site_precise.
# There is no monolithic 'view' capability — no route may be tagged with it (the boot assertion rejects it).
VIEWER_LINK_CAPS = {"view.status", "view.preview"}   # the DEFAULT viewer-link set: live-watch only.
                                                     # EXCLUDES view.media (raw FITS) and view.site_precise (precise lat/lon).
ROLES_CAP = {
    "viewer":   VIEWER_LINK_CAPS,                                # = {view.status, view.preview}; NO raw-FITS, NO precise site
    "operator": {"view.status", "view.preview", "control.capture", "control.guide"},  # imaging + guiding; NOT control.mount/power/config
    "admin":    ALL_CAPS,                                        # everything (incl. view.media, view.site_precise)
}
```

`ROLES_CAP["viewer"]` EQUALS `VIEWER_LINK_CAPS` — the local `viewer` role and a relay-issued viewer link carry the SAME
default cap set (`{view.status, view.preview}`). A viewer link gains `view.media` (raw FITS) or `view.site_precise`
(precise coordinates) ONLY by an explicit per-link opt-in carrying a DISTINCT explicit caps list (W3.3 viewer-link
issuance) — neither is ever implied by the role. `operator` is reserved for a future "friend can run my imaging but not
drive the mount or reconfigure limits" tier (note it deliberately EXCLUDES `control.mount`, `control.power`, every
`config.*`, and `view.media`/`view.site_precise`); `viewer` and `admin` ship first. Because **sequence-start and polar
are now `control.mount`-gated** (the privilege-escalation fix above), an `operator` can run a single capture/loop and
guiding (with `dither`'s bounded `pulse_guide` as the one accepted motion exception) but **cannot start a
slewing/centering/meridian-flipping SEQUENCE** — that needs `control.mount`. This is the honest boundary; the earlier
"operator can image but a sequence drives the mount" leak is closed. `config.safety` and `config.solar_override` are
flagged DESTRUCTIVE so the UI double-confirms even for admins.

**The route→capability table is exhaustive and ASSERTED AT BOOT.** `auth/rbac.py` enumerates `app.routes` at startup
and **fails create_app() if ANY mutating route (POST/PUT/PATCH/DELETE + the `/ws` accept) lacks a declared
capability** — no accidentally-open control route. The assertion is **stronger than mere presence**: every
**motion-capable handler** (any route whose handler transitively reaches
`Telescope.slew`/`move_axis`/`set_tracking`/`park`/`unpark`/`pulse_guide` — sequence-start, polar, mount/tracking,
goto/move/slew, meridian flip) **MUST carry `control.mount`** (or `control.sequence`), not just *a* capability — so a
sequence route mis-tagged `control.capture` fails the boot check. A single `Depends(requires(cap))` per route is
INSUFFICIENT for multi-domain mutating routes — see W2.2 for `POST /api/config`. The `safety/simulate` route must carry
`config.safety` or the boot assertion fails (it is a live-safety-engine mutator).

**The boot assertion validates the RE-KEYED view-split table, so a data-minimization route mis-tag also FAILS boot.**
Because `view` is split (`view.status`/`view.preview`/`view.media`/`view.site_precise`, W2.1), the boot check **rejects
the retired monolithic `view` capability outright** — any route still declaring `view` fails `create_app()`. In
particular the raw-FITS / full-res science routes (`GET /api/preview/{id}/fits`, `app.py:1044`/`:1057`, and any other
full-res route) **MUST carry `view.media`, NOT `view` and NOT `view.preview`** — a `fits`/full-res route tagged
`view`/`view.preview` FAILS the boot check, so a viewer-link principal holding only `{view.status, view.preview}`
(`VIEWER_LINK_CAPS`) can never reach raw FITS through a mis-tagged route. The `/ws` accept and `GET /api/status` carry
`view.status`; the downsized preview routes carry `view.preview`. This closes the W3.3 leak at the single-source-of-truth
table rather than only in prose, and is exercised by a §T6 assertion (a `fits` route tagged broad `view`/`view.preview`
must FAIL boot).

### W2.2 Enforcement — a FastAPI dependency + WS gate

- `server/astrodeck/auth/rbac.py` (NEW): `requires(cap: str)` returns a FastAPI dependency that resolves the caller →
  role (via the auth provider, W2.3) → checks `cap in ROLES_CAP[role]`, else `403`. Declared **per route**:
  ```python
  @app.post("/api/mount/goto", dependencies=[Depends(requires("control.mount"))])   # MOTION, not capture
  @app.put("/api/site",        dependencies=[Depends(requires("config.site_optics"))])  # but see horizon_min_deg note
  @app.post("/api/connect/rig", dependencies=[Depends(requires("config.backend"))])
  @app.post("/api/switch/set",  dependencies=[Depends(requires("control.power"))])
  ```
- **Route-level `Depends()` is INSUFFICIENT for multi-domain mutating routes.** `POST /api/config` (`ConfigPatchBody`,
  `app.py:247`) partial-merges any subset of `{site, safety, escalation, alerts, deadman_url}` in ONE call — so a single
  `Depends(requires(cap))` on the route would let a caller holding **one** config capability mutate **ALL** of them
  (e.g. a `config.alerts` holder rewriting `deadman_url` + `safety.min_alt_deg` = privilege escalation). **Either split
  `POST /api/config` into domain-specific routes (one per capability) OR enforce capabilities at the FIELD level inside
  the handler after binding the body.** The spec chooses field-level enforcement for `/api/config` to keep the single
  debounced client PUT, with a per-field cap map and these REQUIRED properties:
  - **Presence is determined by `model_fields_set` / the raw JSON keys, NOT attribute-value-vs-default.** A
    value-vs-default check is unsound both ways: setting `safety.min_alt_deg` to its *default* value would EVADE the
    `config.safety` check, and a benign block left unchanged could be FALSELY rejected. The handler reads which blocks
    were actually present in the inbound body (`body.model_fields_set`, recursing into nested models for the
    `horizon_min_deg`-in-`site` case) and gates ONLY those.
  - **Explicit block → capability map:** `site` → `config.site_optics` (and `site.horizon_min_deg` ALSO → `config.safety`
    when present); `safety` → `config.safety`; **`escalation` → `config.alerts`** (see the deliberate mapping note below);
    `alerts` → `config.alerts`; `deadman_url` → `config.alerts` (it is an alert/healthcheck sink AND an SSRF target,
    W2.1 — apply the same SSRF allow/deny before accepting it).
  - **AUTH and REMOTE config are PINNED to `admin.users` and kept OUT of the general `POST /api/config` field map.**
    `AuthConfig` (the provider, `role_allowlist`, signing keys, `revoked_jti`) and `RemoteConfig` (relay enable,
    `device_token`) are **privilege-defining** — mutating them changes WHO is an admin and WHETHER the WAN is reachable,
    which is strictly more sensitive than any `config.site_optics`/`config.alerts` block. **The escalation vector, stated
    explicitly:** a mere `config.alerts` holder who could submit an `auth` block flipping `provider = "none"` would
    **disable ALL authentication — every subsequent caller resolves to `admin`** (the `none` provider's implicit admin),
    a total takeover from a notification-tier capability. So **either** (a) route auth/remote writes through **dedicated
    `admin.users`-gated routes** — `POST /api/auth/config` and `POST /api/remote/config` — and **FORBID any `auth`/`remote`
    key in `ConfigPatchBody`** (the general config route never accepts them), **or** (b) add `auth → admin.users` and
    `remote → admin.users` to the field map so those blocks require `admin.users` even on the general route. Either way
    `auth`/`remote` are NEVER mutable by a `config.*`-but-not-`admin.users` principal. (Option (a) is preferred — it keeps
    the privilege-defining surface off the debounced multi-domain config PUT entirely.)
  - **The field-map is FAIL-CLOSED for any UNMAPPED block — reject the whole body 403, never silently merge.** If the
    inbound body carries a block with **no entry in the block→capability map** (a new/unknown block, including a stray
    `auth`/`remote` key under option (a)), the handler **rejects the entire request 403** and merges NOTHING — it must
    NOT pass an unrecognized block through to the merge. This makes the §T6 "map EVERY `ConfigPatchBody` field" test
    fail-closed: a future field added without a cap-map entry is **rejected**, not silently accepted, so an attacker
    cannot smuggle a privilege-defining block through a gap in the map.
  - **`escalation` is mapped to `config.alerts`, NOT `config.safety` — a deliberate, pinned decision.** `EscalationConfig`
    (`config.py:104`) controls automation **NOTIFICATION / RECOVERY** policy (`cooling_action` warn|abort|skip
    `config.py:107`; `reconnect_resume`/`reconnect_retries` `config.py:114-115`) — an alert/automation-policy domain,
    **not** a pier/horizon floor. Bundling it under `config.safety` would let the **destructive pier role** (`config.safety`)
    silently rewrite whether the system **ABORTS vs WARNS** on a cooling failure, while the **`config.alerts`** holder
    (logically responsible for notification/recovery policy, adjacent to `deadman_url` and the alert sinks) could not touch
    it — backwards. So `escalation` rides `config.alerts`. (If a future EscalationConfig field genuinely controls a
    pier/horizon floor, split THAT field out — but `cooling_action`/`reconnect_*` are notification/recovery policy.) The
    §T6 field-level test that maps EVERY `ConfigPatchBody` field pins `escalation → config.alerts` explicitly, so this
    decision cannot silently drift.
  - **Mixed allowed/disallowed bodies are rejected ATOMICALLY.** If ANY present block's capability is not held, the whole
    request `403`s and **NOTHING is merged** — the persisted config is unchanged (no partial write of the allowed
    blocks). Tested in §T6 (assert persisted config unchanged after the 403).
  - **`PUT /api/site` is gated the same way** — it carries `horizon_min_deg` (a `config.safety` floor) alongside site
    coords, so apply `model_fields_set` gating: `horizon_min_deg` present → require `config.safety`; site coords →
    `config.site_optics`; reject atomically.
- **WS gating is ACCEPT-TIME ONLY — there is no command channel to gate.** The `/ws` handler (`api/app.py:1505`) is
  **send-only**: after `accept()` it loops `ev = await q.get(); await websocket.send_json(...)` (`app.py:1525-1527`) and
  **never calls `receive()`**. ALL control is REST POST. So RBAC on WS reduces to **"who may SUBSCRIBE"** (`view`+),
  checked at **accept time** (mirroring today's token gate at `app.py:1513`); close `1008` before accept for an
  unauthorized subscriber. **Drop the earlier "inbound command frame checked / close 1008 on a control attempt"
  language** — it targets a channel that does not exist. (A future WS *command* path would have to be designed from
  scratch and would inherit nothing from the accept-time gate; reserved, not specced here.)
- **A long-lived viewer WS must not outlive its auth — on EXPIRY *or* REVOCATION.** The `/ws` handler is send-only and
  **never re-resolves auth mid-stream** (`app.py:1525-1527`); `requires()` re-checks `jti` only on NEW requests. So a
  viewer link **revoked** (its `jti` added to `revoked_jti`) while a multi-hour `/ws` is open would keep streaming live
  status/preview until the socket happens to drop. The periodic per-WS re-check (already required for `exp`) **MUST ALSO
  consult the `jti` deny registry** and tear the socket down on a revoked `jti` — not just on expiry. Spec a **max
  re-check interval** (e.g. ≤ 30 s) that bounds the leak window for BOTH `exp` and revocation; close `1008` with a
  distinguishable reason so the client renders "expired"/"revoked", not the transport-loss banner (W2.5).
  - **The RELAY-TUNNELLED case needs its own mechanism — the home has no per-viewer socket to drop.** A relay-tunnelled
    "WS" is the **relay's per-`ws_id` projection off ONE home-side `bus.subscribe()`** (W3.3 single-subscriber model) —
    the home re-checks auth only on NEW requests and holds **no per-viewer socket** to tear down; only the **relay** knows
    which `ws_id` maps to which `jti`. So: the **relay maintains the `ws_id`→`jti` map** and EITHER **polls the home's
    `revoked_jti`** OR **receives a home-pushed revocation event** (a `REVOKE` control frame on reserved `stream_id=0`,
    W3.2 / W3.3 revocation-propagation path), then **drops the matching per-`ws_id` projection** within the **same bounded interval as the home-direct case
    (≤ 30 s)** — state this relayed-case max revocation-propagation latency explicitly, mirroring the direct ≤ 30 s bound.
  - **Renewal is contingent on a LIVE home stream + a bounded-age revocation snapshot.** Viewer-link "renew-while-
    connected" (W3.3) must NOT keep renewing access during a relay redial/backoff when the relay cannot see the home's
    current `revoked_jti`. So renewal requires (a) a live home stream AND (b) a revocation snapshot whose age is within a
    bounded window — a link revoked **during** a relay redial cannot keep renewing viewer access once the snapshot goes
    stale. Tested alongside the §T6/§T7 revocation cases.
- A **route→capability table** lives in `auth/rbac.py` as the single source of truth (mirrors §W2.1) so a new mutating
  route without a declared capability fails the **boot-time `app.routes` assertion** (no accidentally-open control
  route).

### W2.3 Identity — pluggable auth provider

Generalizes the existing single-token infra (`api/app.py` `auth_token`/`_auth_mw` `:351`, WS gate `:1513`). A provider
resolves an inbound request → `(identity, role)`.

```python
# auth/provider.py  (NEW)
class AuthProvider(Protocol):
    name: str
    async def resolve(self, request) -> Principal | None: ...   # (email|None, role)
```

| Provider | Behavior |
|---|---|
| **`none` (default)** | Open / admin. Resolves every caller to `admin`. **Today's LAN behavior is byte-for-byte unchanged** — no login, full control. The optional `ASTRODECK_TOKEN` still gates access when set; a token-bearing caller is `admin`. **HARD INTERLOCK (W3) — enforced ON THE PER-REQUEST PATH, not only at boot.** A boot-time refuse-to-start is NOT sufficient: it does not cover a *runtime* `RemoteConfig.enabled` flip, or a relay that connects before the boot check, and with `provider == "none"` the home resolves `admin` for EVERY caller — so the W3.3 home 403 re-check is INERT (admin-for-all over the WAN). Make the refusal a **property of the request path**: when a scope is flagged **REMOTE/untrusted** (W3.3 step 3) AND `provider == "none"`, the in-process replay **hard-fails CLOSED (deny, NOT admin)**, and the `relay_client` **refuses to forward ANY frame while `provider == "none"`**. The boot refuse-to-start stays as a first line, but the per-request deny is the real interlock. When the relay path is active the role comes ONLY from the verified relay token (W3.3), never from the `none` provider's implicit admin. |
| **`google`** | Google OIDC / OAuth2 **Authorization Code + PKCE**. Verify the ID token (issuer/audience/signature/exp). **Required security steps:** bind a `state` (CSRF) and `nonce` (ID-token replay) to a pre-auth cookie and check both on callback; require `email_verified == true`; pin `hd` (hosted domain) for Workspace before mapping; then map `email → role` via the config **allowlist**, **re-evaluated on EVERY request** (cheap, in-memory) so removing an email is immediate — today an allowlist consulted only at login leaves a removed admin holding a valid JWT until `exp` (alternatively keep `exp` very short with refresh). A **default role** option assigns any *authenticated* user a role (e.g. `viewer`), or **deny** unlisted emails. |

After login, issue an **AstroDeck session** = a signed JWT in a cookie carrying `{email, role, jti, exp}`. The cookie
MUST be **`Secure` + `HttpOnly` + `SameSite=Lax`** (Strict where the OAuth redirect allows) and path-scoped. The RBAC
dependency reads the role from this session (or from the `none`-provider's implicit admin). **Logout = cookie clear AND
server-side `jti` invalidation** (and, under W3, the relay honors a revocation signal). **SAML is noted as a future
provider** for Google Workspace SSO (same `AuthProvider` seam, different token validation).

**The cookie introduces a CSRF surface the current header/query-token scheme lacks — defend it explicitly.** Moving auth
to a `HttpOnly` cookie makes EVERY mutating POST (park, goto, switch/set, sequence start, config) CSRF-able: `SameSite=Lax`
STILL sends the cookie on top-level cross-site navigations and form POSTs, and there is no anti-CSRF token. The current
`x-auth-token`/`?token=` scheme is immune (an attacker's site cannot read or set those). So the cookie path MUST add one
of:
- **(a) Keep reading the principal from an `Authorization: Bearer` header the JS sets on every `/api` mutation** (the
  cookie is then only a convenience carrier for the bootstrap; a cross-site form POST omits the header → denied), OR
- **(b) `SameSite=Strict` PLUS an `Origin`/`Referer` check on every mutating route** (reject when `Origin` is not the
  home/relay origin).
A **directly-exposed (TLS-reverse-proxied) home with cookie auth is fully exposed** and MUST carry this defense (it has
no relay in front to enforce origin).

**Token-forgery blast radius — do NOT share a symmetric secret with the relay.** A single HS256 `session_secret` SHARED
with the relay means a **relay compromise can MINT home-trusted admin tokens** (park/slew/power). Use **asymmetric
signing (RS256/EdDSA)** — relay signs with a private key, home verifies with the relay's PUBLIC key — OR (preferred)
make the **HOME the JWT issuer** and treat the relay as pure transport forwarding the OIDC principal, so the relay never
holds home signing authority. Use a **separate signing key for viewer links** (W3.3) so a link-minting bug can never
forge an admin session. Maintain a server-side **`jti` allow/deny registry** so a session/link is revocable
independently of the signing key (rotating the key logs everyone out).

**`revoked_jti` is mutated ONLY through an `admin.users`-gated, APPEND-ONLY revoke/unrevoke API — NEVER via the
`POST /api/config` merge.** Although `revoked_jti` lives on `AuthConfig` (which lives on the mutable `AppConfig`), it is
the **revocation registry**, and a `POST /api/config` (or `PUT`) merge that could **shrink or clear** it would
**un-revoke every killed viewer link and admin session in a single request** — a lower-cap principal could resurrect
every link the operator just killed. So: (1) `revoked_jti` is writable **only** through a dedicated `admin.users`-gated
**revoke** (append a `jti`) / **unrevoke** (a deliberate, audited, separately-gated removal) API — the revoke path is
**append-only** for the common case; (2) a config PUT/`POST /api/config` carrying `auth.revoked_jti` (or any `auth` key,
per the W2.2 auth-pinning above) is **rejected** — the general config merge can never touch the registry. Add a **§T6
test:** a `config.*`-but-NOT-`admin.users` principal attempting to **shrink/clear** `revoked_jti` (via a config PUT
carrying `auth.revoked_jti`) is **403** and the **registry is UNCHANGED** (assert the stored `revoked_jti` still contains
the previously-revoked entries) — so a lower cap cannot un-revoke a killed viewer link or admin session.

Config (appends to `AppConfig`, `config.py:131`, additive per the master-plan union rule):
```python
class AuthConfig(BaseModel):
    provider: str = "none"             # "none" | "google"
    google_client_id: str = ""
    google_hd: str = ""                # optional Workspace hosted-domain pin (empty = any verified email)
    role_allowlist: dict[str, str] = {}   # email -> role; re-evaluated EVERY request
    default_role: str | None = None       # role for any authenticated user, or None = deny
    # Signing: prefer asymmetric. session_priv/pub for the home-issued session JWT;
    # relay_pubkey to VERIFY relay-forwarded principals; viewer_link_* a SEPARATE key.
    session_signing_alg: str = "EdDSA"    # NOT HS256-shared-with-relay
    session_private_key: str = ""         # home's signing key (home is the issuer)
    relay_pubkey: str = ""                # verify relay-forwarded principal (W3.3)
    viewer_link_pubkey: str = ""          # SEPARATE key for viewer links (W3.3)
    revoked_jti: list[str] = []           # server-side deny registry (revocation before exp)
```

> **`default_role` is CONSTRAINED so a non-null default cannot grant elevated WAN access to the entire Google
> population.** With the relay terminating OIDC over the WAN, `default_role` assigns a role to ANY *authenticated* Google
> user. If `default_role` were above `viewer` AND `google_hd` were empty (any verified Google email), the **entire Google
> user base** could authenticate and obtain WAN-reachable `control.*`/`config.*` access — a catastrophic default. So
> enforce ONE of: **(a)** `default_role` is capped at **at most `viewer`** (reject any `control.*`/`config.*`-bearing role
> as a default — only `viewer` or `None`/deny is a valid default), **OR (b)** a **non-empty `google_hd` is REQUIRED
> whenever `default_role` is non-null** (deny any default-role grant for an out-of-domain verified email — the default
> applies only within the pinned Workspace domain). Document plainly: **`default_role` above `viewer` + empty `hd` means
> the whole Google user base can authenticate with elevated access.** Add a **§T6/§T7 case:** an **out-of-allowlist
> verified email** with `default_role = "operator"` and **empty `hd`** is **DENIED** (or, under option (a), downgraded to
> `viewer`) — never granted operator.

> **The OAuth redirect is terminated in ONE of TWO topologies — they are NOT the same code, do not claim "identical".**
> Google's Authorization-Code flow needs a public HTTPS callback URL; a LAN home has none, so by default the relay
> terminates. Cookie domain (relay vs home origin), who holds the PKCE `code_verifier`, and who validates `state`/`nonce`
> all DIFFER between the two — the earlier "the provider code is identical" claim was false and is dropped (it is true
> only of the narrow **ID-token verification helper** — issuer/audience/signature/exp/`hd`/`email_verified` — which both
> topologies share). The two topologies:
> - **(1) Relay-terminated (default for a LAN home).** The **relay** owns `/auth/google/callback`: it initiates
>   `/authorize`, sets + verifies `state`/`nonce`/PKCE against its OWN pre-auth cookie (relay origin), validates the ID
>   token, and mints the **home-verifiable principal** (W2.3 asymmetric/home-issued) it forwards over the tunnel. The
>   **home does ZERO OIDC** and **never sees `state`/`nonce`/the raw code**. CRITICAL: in this topology the **home MUST
>   NOT accept a raw Google ID token from the relay** — it accepts ONLY the relay-signed / home-issued principal — so a
>   relay that skips its `state`/`nonce`/PKCE checks **cannot be papered over by home-side OIDC** (there is none); the
>   relay's correctness is load-bearing and is tested directly (§T6 callback-handler cases, §T7 relay-service).
> - **(2) Home-terminated (directly-exposed, TLS-reverse-proxied home).** The **home** owns the full flow:
>   `/authorize` + `/auth/google/callback`, its own `state`/`nonce`/PKCE pre-auth cookie (home origin), ID-token
>   validation, and session minting. No relay is involved in auth.
>
> Only the token-verification helper is shared; everything about cookie domain, PKCE custody, and `state`/`nonce`
> validation is topology-specific.

#### W2.3-bis Multi-method auth (`local` + `google` enabled together) — supersedes the single-`provider` field

The single `AuthConfig.provider: str = "none"` is **too narrow**: an offline rig (phone/tablet straight to the box, no
internet) needs a **username + bcrypt-password** login, AND that login must be able to coexist with Google OIDC on a
home that is sometimes online. So `provider` is **generalized to an enabled-METHODS list** and a `LocalAuthProvider` is
added alongside the existing `GoogleAuthProvider`. **SAML is explicitly out of scope** (dropped by the user) — do NOT
add it to the method enum or anywhere else.

**Methods model.**
```python
class AuthConfig(BaseModel):
    # NEW (multi-method). Empty list => OPEN/admin (today's byte-for-byte default).
    methods: list[str] = []          # subset of {"local", "google"}; order is irrelevant
    # DEPRECATED-but-honored for migration (see below). Never written back.
    provider: str = "none"           # legacy single-provider; "none"|"google"
    admin_token: str = ""            # break-glass bearer => admin (secret); ALWAYS active, method-independent
    # --- local auth (NEW) ---
    local_enabled_first_run: bool = True   # allow the first-admin setup route while the user store is EMPTY
    session_ttl_s: int = 8 * 3600          # session cookie lifetime for BOTH local + google logins
    # --- google (UNCHANGED) ---
    google_client_id: str = ""
    google_client_secret: str = ""   # secret
    google_redirect_uri: str = ""
    google_hd: str = ""
    role_allowlist: dict[str, str] = {}    # email -> role; re-evaluated EVERY request (google)
    default_role: str | None = None        # google default role (or None=deny); the `viewer`-ceiling rule still applies
    # --- signing / revoke (UNCHANGED) ---
    session_signing_alg: str = "EdDSA"     # W3 seam; the home session is HMAC today (auth/session.py)
    session_private_key: str = ""
    session_public_key: str = ""
    relay_pubkey: str = ""
    viewer_link_pubkey: str = ""
    revoked_jti: list[str] = []            # append-only deny registry; admin.users-gated ONLY
```
`user_store_path` is NOT an `AuthConfig` field — the user store lives at a FIXED location (`server/config/users.json`,
next to `astrodeck.json`) so a config that disables auth can never relocate or orphan the credential file, and so the
`create-admin` CLI can find it without reading `AuthConfig`.

**Migration (read-time, in `AuthConfig` model-validator — old `astrodeck.json` files load unchanged).** If `methods`
is **empty** AND the legacy `provider == "google"`, treat it as `methods == ["google"]`; `provider in {"none",""}` with
empty `methods` stays **OPEN/admin** (today's default). `provider` is **never written back** — once any `/api/auth/config`
write occurs, the persisted shape carries `methods` and the legacy field is left at its default. **`admin.users` writes
that set `methods` must also blank a stale `provider`** is NOT required (the validator's precedence is: a non-empty
`methods` wins and `provider` is ignored), but the redactor and `/api/auth/config` response report only `methods`.

**`resolve_principal` chain (pinned order; FIRST non-None wins, fail-closed otherwise).** This is the SAME single
`resolve_principal` seam (`deps.py`) — multi-method is realized as a `MultiAuthProvider` that the chain composes, NOT as
N parallel middlewares:
1. **Break-glass admin token** (`admin_token`/`ASTRODECK_TOKEN`) — if set AND the bearer matches → **admin**. Always
   active regardless of `methods`, so it can NEVER lock you out (anti-lockout #1).
2. **Session cookie** (`ad_session`, HMAC-signed) — the SAME cookie whether it was minted by the LOCAL login or the
   GOOGLE callback (both call `sign_session(role, email, jti, ttl_s)`), so `SessionCookieProvider` resolves BOTH with no
   per-method branch; its `revoked_jti` check is unchanged.
3. **None** — if `methods` is empty → **admin** (open default, today's behavior); else (a real method is enabled and no
   cookie/token matched) → **None** (fail-closed 401).

```python
# auth/providers.py (extend)
class MultiAuthProvider:
    """Composes the enabled methods behind ONE resolve(). name is the join of
    enabled method names (e.g. "local+google") so the W3 remote-interlock check
    `name == "none"` is still correct (a composed provider is never "none")."""
    name: str                                   # "local", "google", "local+google", ...
    def __init__(self, *, methods, admin_token, revoked_jti, role_allowlist,
                 default_role, google_hd): ...
    async def resolve(self, request) -> Principal | None:
        # 1) break-glass token  2) session cookie (local OR google)  3) None (fail-closed)
```
`build_provider(auth_cfg)` (in `deps.py`) selects: empty `methods` (after migration) **and** no `admin_token` →
`NoneAuthProvider`; empty `methods` **with** `admin_token` → `TokenAdminProvider` (unchanged); otherwise →
`MultiAuthProvider(methods=...)`. The W3 per-request remote interlock (`resolve_principal(remote=True)` hard-denies when
`provider.name == "none"`) is preserved verbatim — only a `NoneAuthProvider` is named `"none"`, so enabling ANY method
closes the open-default-over-WAN hole exactly as before.

> **`session_ttl_s`, `methods`, `local_enabled_first_run` are privilege-defining and ride `admin.users` ONLY** — same
> rule as the rest of `AuthConfig` (W2.2): they are NEVER accepted by the general `POST /api/config` merge, only by the
> dedicated `admin.users`-gated `POST /api/auth/config`. A `config.alerts` holder cannot flip `methods` back to empty
> (which would re-open the rig to admin-for-all) — that is the exact escalation W2.2 forbids.

### W2.5 Client role model — viewer surfaces are READ-ONLY in the UI, not 403-on-tap

The UI has **no concept of role today**. With server-only enforcement, a viewer sees live slew pads / capture / park /
stop, taps them, and gets a silent `403` (or, for the WS, a `1008` close the client renders as the generic "DISPLAY
DISCONNECTED" `ConnectionBanner`) — teaching viewers the app is broken. Spec the **client role model alongside W2**:
- The **session principal/role is readable by the UI** (e.g. a `/api/me` returning `{email, role, caps}`, or the role
  embedded in the bootstrap `hello`) so it renders viewer surfaces **read-only**. The viewer JWT carries an **explicit
  capability set** (not just a role string), so read-only is enforced structurally even if role→cap mapping drifts.
  - **`/api/me` REQUIRES `view`, returns the ACTUAL resolved principal, and is FAIL-CLOSED — never a default-admin.**
    `/api/me` discloses identity/caps, so it carries the `view` capability dependency (not open). It returns the
    **genuinely resolved** principal: `admin` only under the `none` provider, `viewer` for a viewer-link, the
    allowlist-mapped role for a Google user. On **ANY resolution failure it returns `401`** — it MUST NOT fall back to a
    default-admin principal (a fail-OPEN `/api/me` would tell a viewer's UI it is admin and unlock every control client-
    side). **Explicitly EXCLUDE `/api/me` from `_AUTH_OPEN_PREFIXES`/`_AUTH_OPEN_EXACT`** (`app.py:283-284`) so it is never
    in the unauthenticated set. **Extend the boot-time route assertion** (today MUTATING-routes-only) to ALSO cover
    **identity-disclosing GETs**: every route that returns a principal/caps (`/api/me` and any future such GET) must carry
    an auth dependency, asserted at `create_app()`. Add a **§T6/§T8 test:** an **unauthenticated `/api/me` is 401**, and a
    **viewer-link `/api/me` returns `viewer` caps, NOT admin**.
- **Rendering is FAIL-CLOSED.** Until caps are POSITIVELY resolved (the `/api/me`/`hello` round-trip has returned), all
  motion + power + config controls render **disabled/hidden by default** — an unknown role is treated as **viewer, not
  admin** (no flash of an actionable slew pad before caps load). For **viewer links specifically, `control.mount` /
  `control.power` controls are HIDDEN (not disabled-but-visible)** so a friend never sees an actionable slew pad or Park
  button at all; show the **"View-only — you're watching, not driving"** badge instead.
- **Viewer STOP / panic-stop — DECIDED: viewers get NO motion controls including STOP; the OPERATOR is the safety
  authority.** A viewer is read-only; exposing even a STOP would imply they can intervene in motion they cannot otherwise
  affect, and it is `control.mount`-gated server-side regardless. Decision **(a): viewers get no motion controls at all,
  including STOP** — and the **viewer-link landing copy states the operator (not the viewer) is the safety authority**
  ("You're watching a session someone else is driving — they control the equipment and can stop it; you cannot."). (The
  alternative (b) — a read-only-safe "request stop" behind a dedicated low-trust emergency-stop cap — is explicitly NOT
  taken for the first ship; reserved.) **Critically, the client-side `panicStop` is best-effort and a viewer 403 MUST be
  swallowed silently:** `SlewPad`'s blur/visibility `panicStop` fires `POST /api/mount/stop` **fire-and-forget**
  `.catch(() => {})` on `blur`/`visibilitychange` (`SlewPad.tsx:162-177`) — an automatic background call, NOT a user tap.
  For a viewer this POST 403s (STOP is `control.mount`-gated), and that 403 **must NOT surface as an error toast NOR
  trip the "not permitted" classifier banner** (W2.5 three-state classifier) — it is an unsolicited background call the
  viewer never initiated. So the panicStop path classifies a 403 as "expected, suppress" rather than routing it through
  the user-facing classifier.
- **Per-VIEW connect gating becomes PER-ROLE — not one sticky `equipConnected` for the whole rig.** Today every gated
  view uses `NotConnectedInterstitial` driven by a single sticky `equipConnected` derived from `status.mode !== 'none'`
  (`store.ts:881`), and `SlewPad` reads `isNina` from the single global `status.mode` (`SlewPad.tsx:91`). W1.4 fixes the
  per-role badges + per-role `isNina`, but per-VIEW GATING is still all-or-nothing: on a **partial-connect rig** (camera
  up, mount failed — the exact W1.3 degradation the LEDs now show) every view either reads "connected" (so MountView lets
  the operator tap a **dead slew pad → silent error**) or "not connected" (hiding the **working camera**). Specify
  `equipConnected` becomes **PER-ROLE** (keyed by each view's required role), reading the **same per-role `RoleResult`**
  the LEDs use (W1.6): **MountView shows `NotConnectedInterstitial` with the telescope `RoleResult.error` inline when the
  telescope is not connected even if the camera is**, while **CaptureView stays usable** on the connected camera. This is
  the natural extension of the W1.6 per-role surface down to the per-view gating layer; the §T8 client-role tests cover
  a partial-connect rig (telescope failed) asserting MountView gates while CaptureView does not.
  - **`NotConnectedInterstitial` is ROLE-AWARE — a viewer never sees the "Go to Rig"/connect CTA they cannot act on.**
    Today `NotConnectedInterstitial.tsx:39-46` hardcodes a single `setView('connect')` "Go to Rig" button — but a viewer
    has no `config.backend` and cannot connect equipment, so the button is a dead end that contradicts the View-only
    framing. Make the interstitial **take the caller's caps**: for a **viewer** show a **passive message** with **NO
    actionable button** ("The operator hasn't connected a telescope yet" — they're watching, not configuring); only
    **operators/admins holding `config.backend`** see the "Go to Rig" CTA. Update `NotConnectedInterstitial` to accept the
    caps and branch the CTA on `config.backend`.
- **Hold-confirm even for operators on destructive runtime actions.** Add the `confirmDialog` **hold** mode to
  **Park / Unpark / Stop / goto** even when the caller HAS the capability — an accidental Park mid-sequence or Unpark in
  daytime is destructive. (Distinct from the W2.4 DESTRUCTIVE double-confirm on `config.*`; this is the runtime-motion
  variant.)
- **Three look-alike dead-ends get DISTINCT copy — they must not collide.** The client classifies and renders:
  - **permission-denied (`403`)** → **"view only — not permitted"** (NOT the reconnecting banner).
  - **session-expired / revoked** → **"your view link expired — ask <sharer> for a new one"** — detected by reading the
    WS **`1008` close code/reason** (W2.2) to distinguish it from transport loss; NOT a reconnect spinner.
  - **transport-down** → **"reconnecting to <home>"**.
- **Fix the hardcoded "the rig keeps running" copy in ALL THREE surfaces, not just `ConnectionBanner`.** The LAN-only
  "rig keeps running" reassurance is hardcoded in **THREE** places, not one: `ConnectionBanner.tsx:25` (label/detail),
  `HealthLeds.tsx:74` (`linkTitle`), AND `store.ts:659`. The W2.5 3-state classifier (403 → not-permitted, 1008-expiry →
  expired, transport → reconnecting) and this "fix the copy" note must name **all three** so a remote viewer whose relay
  dropped never sees a false "rig keeps running" claim it cannot verify — fix `HealthLeds.tsx:74 linkTitle` and
  `store.ts:659` alongside `ConnectionBanner`, driven by the **same role/remote context** as the classifier. Define the
  **viewer-link landing page AND expiry copy together**: before the visitor touches anything it states read-only, who
  shared it, what they can see, and (on expiry) the ask-for-a-new-link path.

### W2.4 Staging

A) `auth/provider.py` + `AuthProvider("none")` + `auth/rbac.py` `requires()` + route table + **boot-time `app.routes`
assertion** + accept-time WS gate, all defaulting to admin (green: every existing test passes, no behavior change).
B) `AuthConfig` + asymmetric signed-session JWT issue/verify + `jti` registry + `admin.users` allowlist API + **client
role model (W2.5) + DESTRUCTIVE double-confirm** (pulled FORWARD — it must ship with the first non-admin role OR with
ANY remote exposure, NOT at the tail after viewer links could already be deployed; reuse the existing `confirmDialog`
**hold** mode with tone `danger` + `HoldButton`). C) `GoogleProvider` (OIDC verify + PKCE + state/nonce/`email_verified`
/`hd`) + login UI. **C2 (LOCAL AUTH — W2.6)** `UserStore` + `LocalAuthProvider` + `MultiAuthProvider` + `methods` model
+ `POST /auth/local` login + `admin.users` user-CRUD routes + first-run setup route + `create-admin` CLI + bcrypt
dependency + the login/Users/auth-method UI (ships with or after C; needs the W2.5 client role model already present).
D) `operator` role.

### W2.6 — User management + local auth (offline, no internet)

**Goal.** A rig with no internet (phone/tablet → box) needs a real login without Google. Add a `local` method
(username + bcrypt password) and real **user management**, both behind the existing capability/session machinery, while
keeping the **NON-BREAKING** guarantee: with `methods == []` the server is still OPEN (every caller = admin). A method
enforces ONLY once explicitly enabled. **All three anti-lockout paths ship together** (see below).

**Dependency.** Add **`bcrypt>=4.1`** to `server/pyproject.toml` `dependencies` and `pip install bcrypt` into
`server/.venv` (it is NOT installed today — verified). Use the `bcrypt` package directly (NOT `passlib`): `bcrypt.hashpw`
+ `bcrypt.checkpw`, **cost factor 12** (`bcrypt.gensalt(12)`). `checkpw` is constant-time internally. bcrypt silently
truncates input at 72 bytes, so the login/CRUD layer **rejects passwords longer than 72 bytes** (UTF-8 encoded) with a
422 rather than letting them be silently truncated. The stored `password_hash` is the full `$2b$12$...` string.

**UserStore — `server/astrodeck/auth/users.py` (NEW).** A small JSON-backed store, SAME atomicity discipline as
`ConfigStore` (reuse `persist.write_json_atomic` / `read_json_or`), at the FIXED path `server/config/users.json`
(`CONFIG_DIR / "users.json"`). It is a SEPARATE file from `astrodeck.json` so credentials never ride the config merge /
redaction / broadcast paths and the `create-admin` CLI needs no `AuthConfig`.

```python
# auth/users.py  (NEW)
class User(BaseModel):
    id: str                       # opaque uuid4 hex; stable across username changes
    username: str                 # unique, case-folded for lookup (stored lower); 1..64 chars
    email: str | None = None      # optional; used only for display + (future) google linking
    role: str                     # "viewer" | "operator" | "admin" (validated against ROLES)
    password_hash: str            # bcrypt "$2b$12$..."; SECRET — NEVER serialized to any API
    enabled: bool = True          # a disabled user cannot log in (existing sessions still need jti-revoke)
    created: float                # unix ts (time.time())

    def to_public(self) -> dict:  # the ONLY shape any API/UI ever sees
        return {"id": self.id, "username": self.username, "email": self.email,
                "role": self.role, "enabled": self.enabled, "created": self.created}
        # password_hash is STRUCTURALLY ABSENT here — there is no code path that returns it.

class UserStore:
    def __init__(self, path: Path = CONFIG_DIR / "users.json"): ...
    # -- reads --
    def is_empty(self) -> bool: ...                       # gates the first-run route
    def list(self) -> list[User]: ...                     # callers MUST .to_public() before serializing
    def get(self, user_id: str) -> User | None: ...
    def get_by_username(self, username: str) -> User | None: ...   # case-folded lookup
    # -- writes (each does an atomic full-file rewrite + in-mem refresh) --
    def create(self, *, username: str, password: str, role: str,
               email: str | None = None, enabled: bool = True) -> User: ...   # hashes; raises on dup username
    def set_password(self, user_id: str, password: str) -> User: ...          # re-hash; bumps nothing else
    def set_role(self, user_id: str, role: str) -> User: ...
    def set_enabled(self, user_id: str, enabled: bool) -> User: ...
    def rename(self, user_id: str, username: str) -> User: ...                # dup-checked
    def delete(self, user_id: str) -> None: ...
    # -- auth --
    def verify(self, username: str, password: str) -> User | None:
        # case-folded username lookup -> bcrypt.checkpw -> enabled check.
        # Returns None (not a reason) on ANY failure (unknown user, bad pw, disabled)
        # so the login route cannot leak which of the three failed (timing + message).
        ...
```
**Invariants (pinned):** (1) `password_hash` is NEVER returned by `to_public()`, `UserStore.list()` consumers, the
`/api/users` routes, or any broadcast — there is exactly ONE place it is read (`verify`) and ONE place it is written
(the hashing in `create`/`set_password`). (2) **The redactor (`api/app.py redacted()`) need not touch users** because
users.json is never part of the broadcast config; but if a user dict ever reaches a response it MUST be via `to_public`.
(3) **You cannot delete or disable the LAST enabled admin** — `delete`/`set_enabled(False)`/`set_role` away from admin
**raise `ValueError("last admin")`** when it would leave zero enabled admins (the route maps it to 409), so the UI can
never strand the rig. (4) Username uniqueness is **case-folded** (`ADMIN` == `admin`).

**LocalAuthProvider + login flow.** The provider itself adds **no new resolve path** — a local login mints the SAME
`ad_session` cookie the `SessionCookieProvider` already resolves (chain step 2 above), so `LocalAuthProvider` is realized
as a **login route** + the existing session resolution, composed under `MultiAuthProvider`. Flow:
```
POST /auth/local  {username, password}
  -> UserStore.verify(username, password)            # bcrypt, constant-time, enabled-gated
  -> None  => 401 {"detail": "invalid credentials"}  # generic, no which-field leak
  -> User  => jti = token_urlsafe(16)
              token = sign_session(user.role, email=user.email, jti=jti, ttl_s=auth.session_ttl_s)
              Set-Cookie ad_session=<token>  HttpOnly, SameSite=Strict, Secure-on-HTTPS, path=/
              200 {"role": ..., "email": ...}         # same public shape as /auth/me
```
This reuses the cookie/CSRF posture already pinned for the google session (SameSite=Strict + the SPA also sends the
session as `Authorization: Bearer` on mutations). `POST /auth/local` is **only mounted/active when `"local" in methods`**
(else 404), mirroring how `/auth/login` 404s when google is off. **Logout is the EXISTING `POST /auth/logout`** — it
clears `ad_session` and appends the `jti` to `revoked_jti`, identical for local and google sessions (no new route).

**First-run setup (anti-lockout #3).** `POST /auth/setup/local {username, password}` creates the FIRST admin:
- **Gated to `UserStore.is_empty()` AND `"local" in methods` AND `auth.local_enabled_first_run`.** Once ANY user exists
  it returns **409 `{"detail": "setup already complete"}`** — it auto-closes; there is no flag to flip.
- Reachable from **localhost/LAN without a session** (it is added to the auth-middleware open set the SAME way the login
  dance is, BUT only while the store is empty — the handler itself re-checks emptiness, so the open-path is not a
  bypass once a user exists). It always creates the user with `role="admin"`, `enabled=True`.
- After creating the first admin it returns 200 and the UI proceeds to the normal login screen.

**CLI (anti-lockout #2) — `python -m astrodeck create-admin <username>`.** An `argparse` SUBCOMMAND added to
`__main__.py` (today `main()` parses only `--host/--port`; restructure to a subparser where the **no-subcommand default
is `run`** so `python -m astrodeck` and `python -m astrodeck run --host ...` both still start the server unchanged):
```
python -m astrodeck create-admin <username> [--password PW]   # seeds OR resets a local admin; does NOT start the server
python -m astrodeck run [--host H] [--port P]                 # default subcommand == today's behavior
```
`create-admin`: if the user exists → **reset its password + force role=admin + enabled=True** (recovery); else create a
new admin. `--password` is optional; when omitted, read it interactively WITHOUT echo (`getpass.getpass`) and confirm.
It writes `users.json` directly via `UserStore` and **never imports `api.app` / starts uvicorn**, so it works on a box
that won't boot the server. **Break-glass token (anti-lockout #1)** is unchanged — `ASTRODECK_TOKEN`/`admin_token`
always resolves to admin (chain step 1), independent of `methods` or the user store.

**Route + CLI surface (pinned, all NEW user routes behind `admin.users` except login/setup).** These live in a new
`auth/users_routes.py` `APIRouter` (so `api/app.py` is untouched, mirroring `auth/routes.py`), included via the same
`try: include_router` seam:
| Method + path | Cap | Notes |
|---|---|---|
| `POST /auth/local` | (open; only when `"local" in methods`) | username+password → session cookie; 401 on failure |
| `POST /auth/setup/local` | (open; only while store EMPTY + local on) | first-admin; 409 once any user exists |
| `POST /auth/logout` | (self) | EXISTING route; clears cookie + revokes jti (no change) |
| `GET  /api/users` | `admin.users` | `[to_public(), ...]` — NEVER `password_hash` |
| `POST /api/users` | `admin.users` | create `{username,password,role,email?}` → `to_public()`; 409 on dup |
| `PATCH /api/users/{id}` | `admin.users` | role/enabled/email/username; enforces "last admin" 409 |
| `POST /api/users/{id}/password` | `admin.users` | admin reset of another user's password |
| `DELETE /api/users/{id}` | `admin.users` | enforces "last admin" 409 |
| `GET  /api/auth/methods` | (open) | `{"methods": [...], "google_configured": bool, "first_run": bool}` — the LOGIN SCREEN reads this to decide which buttons/forms to show; discloses NO secrets |

`methods` itself is edited through the EXISTING `POST /api/auth/config` (`admin.users`-gated) which already persists the
whole `AuthConfig`; no new method-config route is needed.

**UI surface.**
- **Login screen** (`ui/src/views/Login.tsx`, shown when `/auth/me` 401s under a real method): reads
  `GET /api/auth/methods`; renders (a) a **local username/password form** when `"local"` is enabled, (b) a **"Sign in
  with Google"** button (→ `/auth/login`) when `"google"` is enabled + configured, and (c) the **first-run create-admin
  form** when `first_run` is true (store empty). With `methods == []` the app never shows Login (open/admin) — unchanged.
- **Admin → Users panel** (under Settings, gated client-side on `caps` including `admin.users`): table of `to_public()`
  users with add / edit-role / enable-disable / reset-password / delete, each destructive action using the EXISTING
  `HoldButton`/`confirmDialog` danger pattern (W2.5). "Last admin" 409s surface as an inline error.
- **Auth-method config panel** (Settings, `admin.users`): toggle `local`/`google` in `methods`, set `session_ttl_s`, and
  the existing google_* / allowlist / default_role fields; saves via `POST /api/auth/config`. Shows a LOUD note that
  setting `methods == []` re-opens the rig to admin-for-all on the LAN.

**Tests (additive to §T6).** bcrypt round-trip + wrong-password reject; `verify` returns None (not a reason) for
unknown/disabled/bad-pw; `to_public()` has NO `password_hash` (assert the key is absent); the "last admin" guard 409s on
delete/disable/role-change; first-run route 409s once a user exists; `methods == []` still resolves admin (non-breaking);
a `local`-minted cookie resolves via `SessionCookieProvider` exactly like a google-minted one; `create-admin` CLI seeds
then resets without importing `api.app`; a `config.alerts`-only principal CANNOT flip `methods`/`session_ttl_s` via
`POST /api/config` (rejected, registry/auth unchanged — extends the W2.2 §T6 case).

---

## W3 — REMOTE ACCESS (PLAIN HTTP/WS tunnel over ONE outbound WSS — NO gRPC, NO Tailscale)

> **OWNER DECISION (build EXACTLY this).** **NO Tailscale. NO gRPC.** Remote access is **plain HTTP/WS**. The **SCOPE**
> dials ONE **OUTBOUND persistent WSS** to a small **PUBLIC RELAY** (so no home port-forwarding / NAT pain). A remote
> browser hits the RELAY over **HTTPS + WSS**; the relay **TUNNELS** the request down the scope connection to the home
> app, and streams the response back. **The home serves the WHOLE app — SPA + API + `/ws` — so the relay forwards
> EVERYTHING** (the previous "frontend on a CDN, relay separate" split from the 2026-06-17 ADR is SUPERSEDED for the
> shipped design: the relay forwards the static SPA bytes too, because the home already serves them at `/`). The HOME
> **re-authenticates + re-authorizes EVERY tunnelled request** (`remote=True`, so the open `none` default is DENIED
> remotely) and the sun / RBAC / safety gates still enforce **at the home**. The relay **holds NO signing secret and
> CANNOT forge a principal** — it only forwards bytes. **FULL remote control for an authenticated ADMIN; friends are
> view-only via RBAC.** The relay is **HOST-AGNOSTIC** (Docker + a Fly.io config; the owner deploys it).

This **supersedes the prior gRPC framing** (Connect / `.proto` / grpc-web are GONE). The transport is a SINGLE WebSocket
the scope opens to the relay; many concurrent browser HTTP exchanges and nested WS streams are **multiplexed** inside it
with a tiny self-described binary framing (length-prefixed header + payload). No code-gen, no protobuf toolchain, no
second port. The home FastAPI app in `api/app.py` is **unchanged in surface**; remote is purely transport + a
principal-injection seam.

### W3.1 Approach — TUNNEL the existing HTTP/WS over ONE outbound WSS (chosen over gRPC and over Tailscale)

The Web UI + REST/WS are **unchanged**; remote is purely a transport. The **scope** opens ONE **outbound WSS** to the
relay and keeps it open forever — **no home port-forwarding, no inbound at the home**. A remote browser reaches the
relay over HTTPS+WSS; the relay frames each browser request/response (and each nested browser `/ws`) and forwards it
down the scope's single WSS as **opaque bytes**. The scope replays each framed HTTP request **against its own in-process
ASGI app** (`app(scope, receive, send)` — no network hop, no second port) and streams the response back up.

> **Decision: tunnel opaque bytes, don't rewrite, don't buy a tunnel.** We frame the existing HTTP requests and WS
> frames inside one WSS and replay them against the home's own ASGI app. This **reuses the entire current UI + REST
> surface + W2 RBAC** with no parallel API. **gRPC is rejected** — browsers can't speak raw gRPC (would need
> grpc-web/Connect + a proxy), and a native-gRPC control API would duplicate ~40 routes + the safety gates into a second
> contract. **Tailscale/Cloudflare Tunnel are rejected** — the owner wants to own the relay (host-agnostic Docker +
> Fly.io) and the per-principal/viewer-link boundary, not expose the whole LAN service behind a third-party tunnel's own
> auth. The relay is a **dumb byte-forwarder**; all auth/RBAC/safety is re-decided at the home.

### W3.2 Tunnel protocol — ONE WSS, multiplexed binary frames (`server/astrodeck/remote/protocol.py` — NEW)

**This replaces `proto/relay.proto`. There is NO protobuf.** The wire format is a minimal length-prefixed binary frame
the scope and relay both speak; nothing else needs it. One scope↔relay WSS carries ALL of: many concurrent browser HTTP
request/response exchanges, many nested browser `/ws` streams, bulk media, keepalive, and home→relay control pushes
(revocation). Concurrency is by a **stream id** namespace; message **type** says what each frame is.

**Frame on the wire (both directions, inside ONE WSS binary message):**

```
  ┌────────┬──────────┬──────────────┬───────────────────────────────────────────┐
  │ type   │ stream_id│ header_len   │ header(JSON, header_len bytes) │ payload   │
  │ 1 byte │ 8 bytes  │ 4 bytes (BE) │ (small, type-specific metadata)│ (bytes)   │
  └────────┴──────────┴──────────────┴───────────────────────────────────────────┘
```

- `type` (1 byte) — one of the **frame types** below.
- `stream_id` (uint64) — the multiplex key. **The RELAY allocates `stream_id`** for every inbound browser HTTP request
  and for every browser `/ws`; it is **OPAQUE to the home and only echoed back**. The home NEVER mints a stream id
  except for the reserved control id `0` (keepalive/revoke, see below). HTTP and WS share the SAME id space but never
  collide because each id is opened exactly once with a single open-frame.
- `header` — small JSON metadata for this frame type (method/path/status/headers/ws_id/etc.). JSON, not protobuf, so it
  is debuggable and needs no codegen; it is bounded (a few KB) and never carries the body.
- `payload` — raw bytes (an HTTP body chunk, a `/ws` JSON event, etc.). **Bounded per frame** (default 64 KiB) so one
  big body never monopolizes the socket.

**Frame types (the `type` byte):**

| type | name | dir | header carries | payload |
|------|------|-----|----------------|---------|
| `0x01` | `REQ_OPEN` | relay→scope | `{method, path, query, headers[], has_body}` | — |
| `0x02` | `REQ_DATA` | relay→scope | `{eof}` | request-body chunk (uploads) |
| `0x03` | `REQ_ABORT` | relay→scope | `{reason}` | — (browser hung up before EOF) |
| `0x04` | `RESP_HEAD` | scope→relay | `{status, headers[]}` | — |
| `0x05` | `RESP_DATA` | scope→relay | `{eof}` | response-body chunk (streamed) |
| `0x06` | `WS_OPEN` | relay→scope | `{path, query, headers[], ws_id}` | — (browser opened `/ws`) |
| `0x07` | `WS_DATA` | scope→relay | `{ws_id, seq}` | one `/ws` JSON event (server→client) |
| `0x08` | `WS_CLOSE` | both | `{ws_id, code}` | — |
| `0x10` | `PING` / `0x11` `PONG` | both | `{ts}` | — keepalive (on reserved `stream_id=0`) |
| `0x12` | `HELLO` | scope→relay | `{device_token, home_id, generation, proto_version}` | — first frame |
| `0x13` | `HELLO_ACK` | relay→scope | `{ok, endpoint, reason}` | — relay confirms registration |
| `0x14` | `REVOKE` | relay→scope **and** scope→relay | `{jti[], ws_id[]}` | — revocation push (on `stream_id=0`) |
| `0x15` | `WINDOW` | both | `{stream_id, credit}` | — flow-control credit grant (see backpressure) |

- **Multiplexing.** Each browser HTTP exchange = one `stream_id`: `REQ_OPEN` → optional `REQ_DATA…(eof)` →
  `RESP_HEAD` → `RESP_DATA…(eof)`. Each browser `/ws` = one `stream_id` opened with `WS_OPEN`, carrying `WS_DATA`
  frames (one bus event each) until `WS_CLOSE`. Many of each interleave on the one WSS, distinguished by `stream_id`.
- **Ordering invariant.** WebSocket guarantees in-order delivery within the one socket, so frames for a given
  `stream_id` arrive in order; the scope reassembles a body by concatenating that id's `RESP_DATA`/`REQ_DATA` payloads
  in arrival order until `eof`. **A `stream_id`'s frames never interleave with another id's body** because each frame is
  self-delimited (length-prefixed) and tagged with its id — a slow body for id A is sliced into bounded chunks so id B's
  frames get their turn on the socket.
- **Keepalive.** `PING`/`PONG` on the reserved `stream_id=0` at a fixed interval (~10 s) with a **miss-count → tear down
  the WSS → reconnect**, so a half-open NAT'd residential socket doesn't sit dead for minutes. Liveness of the tunnel is
  kept **separate** from rig telemetry liveness (a legitimate 3-minute exposure is not a dead tunnel).
- **Flow control / backpressure (no protobuf HTTP/2 window, so we add a tiny credit scheme).** Because everything rides
  ONE WSS there is no per-stream HTTP/2 window; a 125 MB FITS body could starve the 2 s status poll. So:
  - **Per-`stream_id` credit.** The receiver grants `WINDOW{stream_id, credit}` (bytes it will accept); the sender may
    only have `credit` unacked body bytes in flight on that id. When a slow WAN browser stops draining, the relay stops
    granting credit on its bulk id; the scope's ASGI `send` shim **`await`s** the next `RESP_DATA` until credit frees,
    which **back-pressures the home's file iterator** instead of buffering the whole FITS in RAM.
  - **Class fairness without separate sockets.** Tag each stream with a **class** in `REQ_OPEN`/`WS_OPEN` header
    (`event` = `/ws`; `control` = small request/response; `bulk` = FITS/PNG/large GET). The scope's writer **round-robins
    frames across ready streams** and caps each `bulk` stream's per-turn quota, so a 125 MB FITS is interleaved with
    status/guide `event` frames and never head-of-line-blocks them. (This is the ONE-socket equivalent of the prior
    three-RPC split; the class is a scheduling hint on one WSS, not a separate connection.)
  - **Drop policy on the `/ws` path.** `event` streams are drop-tolerant: on per-viewer buffer overflow **drop oldest
    STATUS** but **keep the LATEST preview/sequence** (coalesce). `control` request/response and log bodies are
    **never dropped** (deliver-or-error). This mirrors the existing `EventBus` `maxsize=500` drop-oldest
    (`events.py:32`/`46`).
- **Large media stays chunked + pull-based.** The two heaviest responses are STREAMED off disk, NOT materialized:
  `GET /api/preview/{id}/fits` is a **`FileResponse`** (`app.py:1460`) and the SPA/static UI is **`StaticFiles` +
  `FileResponse`** (`app.py:2162`/`2168`). The scope ASGI `send` shim emits a `RESP_HEAD` on `http.response.start` and
  one bounded `RESP_DATA{eof = not more_body}` per `http.response.body` event, **awaiting credit before each chunk**, so
  the relay streams pass-through (no whole-object buffering) and bulk media never balloons scope or relay memory.
- **NO direct/signed-URL media bypass in the shipped design.** Every byte goes through the tunnel so it is
  principal-checked at the home like any other request; there is no separate signed-URL fetch path to re-validate. (A
  future optimization could add one, but it would be a NEW capability bound to `jti` + `view.media` + short TTL,
  re-validated at redemption — out of scope here.)
- **`stream_id` collision + orphan rules.** (1) A scope receiving a SECOND `REQ_OPEN`/`WS_OPEN` for a `stream_id`
  already live **rejects the new one** (does not overwrite the in-flight stream). (2) A `REQ_DATA`/`RESP_DATA`/`WS_DATA`
  for an unknown/closed `stream_id` is an **error, never silently buffered** (orphan-chunk reject), closing the
  body-corruption hazard. (3) **Control frames (`PING`/`PONG`/`REVOKE`) ride reserved `stream_id=0`** and are exempt
  from the orphan rule — they are routed by type/`jti`, not by a per-request id.
- **Header transform (BOTH directions), done at the SCOPE replay shim.**
  - **STRIP every inbound auth/identity header** from the tunnelled `REQ_OPEN` headers before building the ASGI scope:
    `authorization`, `x-auth-token`, and any session cookie. `ASTRODECK_TOKEN`/header auth is **NOT a valid carrier over
    the tunnel** (it would let a compromised relay forge admin). The principal is injected by the scope-state mechanism
    in W3.3, never by a forwardable header.
  - **Recompute `Content-Length`/`Transfer-Encoding`** for the chunked body; drop hop-by-hop headers.
  - **Rewrite `Set-Cookie` `Domain`/`Path`/`Secure`/`SameSite`** so a home-issued session cookie is valid on the
    **relay origin the browser actually talks to** (else cookie auth silently breaks remotely). `ad_session` flips
    `SameSite=Strict → Lax` for the cross-origin relay web case (W2.3).
- **Version handshake.** `HELLO.proto_version` (+ optional feature bits) on the first frame, with a reject-incompatible-
  major / degrade-missing-optional policy, so the scope and relay deploy independently.
- **The WS tunnel is INTENTIONALLY server→client-only.** The home `/ws` is **send-only today** (`app.py:2140-2142`
  loops `q.get()`→`send_json` and **never calls `receive()`**), so the tunnel models `WS_DATA` as scope→relay→browser
  only — there is **no browser→home WS frame**. A FUTURE interactive-WS feature (a client→server `WS_DATA` direction for
  subscribe filters / acks) is a **protocol change AND a relay change AND a home `/ws`-receive change** — not a drop-in.
  Reserved, not specced here. **Commands stay on the cap-gated REST path** so each is individually `require(cap)`-checked;
  never add an inbound command channel over `/ws` to "simplify the tunnel" — it would bypass RBAC (invariant §4.5).

### W3.3 Components

#### W3.3.0 The scope client (the home-side outbound dialer) — `server/astrodeck/remote/relay_client.py` (NEW)

A **lifespan background task**, **OPT-IN**: it only dials when a relay is configured AND enabled
(`RemoteConfig.enabled and RemoteConfig.relay_url`). It is launched from `_lifespan` (`app.py:79`) **alongside** the
existing `dispatcher.run()` task, and — exactly like that task — it is **ISOLATED: it can NEVER block or crash
`_lifespan` or local serving.** A relay outage degrades to **local-only autonomy**; LAN access is 100% unaffected
(invariant §4.7). On boot, if disabled/unconfigured, it is a no-op (so today's behavior is byte-for-byte).

When enabled it: opens ONE WSS to `RemoteConfig.relay_url`, sends `HELLO{device_token, home_id, generation}`, awaits
`HELLO_ACK`, then loops reading frames. For each `REQ_OPEN` it **builds an ASGI scope and replays against the
in-process app** (`app(scope, receive, send)` — no network hop, no second port); for each `WS_OPEN` it subscribes to
`bus` ONCE (see backpressure) and streams `WS_DATA` events up.

- **The scope client invokes the in-process ASGI app directly.** `create_app()` returns the FastAPI app
  (`__main__.py:138` runs it via `uvicorn.run(app, ...)`); the relay client closes over the SAME app object and calls
  `await app(scope, receive, send)` with a hand-built `scope`. This is the crux of the remote-flag mechanism (W3.3.2):
  the scope dict carries `state` the home reads, with NO network header an on-LAN attacker could forge.
- **The `send` shim chunks a STREAMING response — the two heaviest responses are NOT materialized bytes.** The two
  biggest responses are STREAMED off disk: `GET /api/preview/{id}/fits` is a **`FileResponse`** (`app.py:1460`) and the
  SPA/static UI is **`StaticFiles` + `FileResponse`** (`app.py:2162`/`2168`) — both emit their body via repeated
  - **The `send` shim chunks a STREAMING response — the two heaviest responses are NOT materialized bytes.** Do NOT frame
    chunking as "the relay slices an already-materialized `Response.body`": the two biggest responses are STREAMED off
    `send({'type':'http.response.body', ..., 'more_body':True})`. So the scope-client ASGI `send` shim is concrete: on
    `http.response.start` → emit a `RESP_HEAD` (status + headers); on each `http.response.body` → emit a
    `RESP_DATA{eof = not more_body}`. **Apply REAL backpressure: `await` the WSS send of each chunk (and await tunnel
    flow-control credit, W3.2) BEFORE returning control to the ASGI body iterator** — so a slow WAN viewer **throttles
    the home's file iterator** instead of buffering the entire FITS in RAM. `FileResponse` and `StaticFiles` are the
    streaming cases this shim must handle. Symmetrically, the **`receive` callable feeds tunnelled `REQ_DATA` frames as
    `http.request` events** (with `more_body`) for upload bodies, so request bodies stream in too.

#### W3.3.1 What the relay forwards: the WHOLE app (SPA + API + `/ws`)

Because the home already serves the SPA at `/`, all static assets under `/assets`, the REST API under `/api`, the auth
dance under `/auth`, and the `/ws` event stream (`app.py:2159-2169`, `:2107`), **the relay forwards EVERYTHING** — a
remote browser loads `index.html`, the JS bundle, makes API calls, and opens `/ws`, all down the one scope WSS. There
is no separate frontend host: the relay is a pure transport in front of the home origin. (This is the deliberate
simplification of the 2026-06-17 ADR's frontend/relay split — fewer moving parts, one origin, the same byte-identical
bundle the LAN serves.)

#### W3.3.2 The REMOTE-FLAG mechanism (CRITICAL — must NOT be LAN-spoofable)

This is the single most important security seam. A tunnelled request must reach the home marked **`remote=True`** so
`resolve_principal(request, remote=True)` (`auth/deps.py:133-143`) **HARD-DENIES the open `none` provider** — an open
LAN default can never be served remotely. The mechanism MUST NOT be forgeable by an on-LAN attacker.

**PINNED: carry `remote` in the ASGI request scope, NOT in any network header.** The scope client builds the replay
scope with `scope['state']['astrodeck_remote'] = True` (ASGI `scope["state"]`, the lifespan-state slot Starlette
exposes per-request). The home reads it through the principal seam; because it lives in the in-process scope dict the
scope client constructs, **there is NO network header an on-LAN attacker could set** to forge it. Concretely:

1. **STRIP all inbound auth/identity headers** (`authorization`, `x-auth-token`, any session cookie) from the tunnelled
   `REQ_OPEN` headers before building the scope (W3.2 header transform). `ASTRODECK_TOKEN`/header auth is **NOT a valid
   carrier over the tunnel**.
2. **Set `scope['state']['astrodeck_remote'] = True`** on EVERY replayed request and on the tunnelled `/ws` scope.
3. **Wire the flag into the auth seam (the home-side change, small + surgical):**
   - `resolve_principal(request, *, remote=False)` already exists and already hard-denies `none` when `remote=True`.
     The gap is that `require(cap)._dep`, `get_principal`, and the `/ws` handler call it WITHOUT `remote=`
     (`deps.py:152`, `:166`; `app.py:2132`), so they always pass `remote=False` today.
   - **Change those three call sites to derive `remote` from the request scope:** add a one-line helper
     `_scope_is_remote(request) -> bool` that reads `request.scope.get("state", {}).get("astrodeck_remote", False)`
     and pass its result as `remote=` into `resolve_principal`. The WS gate reads the same flag off `websocket.scope`.
     This is the WHOLE wiring — no route signatures change; the flag rides the scope the relay client built.
   - **A header fallback is AVOIDED.** If a header is ever unavoidable, it is trusted ONLY on the loopback relay-client
     injection path and **stripped from any real inbound request** by `_auth_mw` — but the scope-state path above needs
     no header, so the shipped design uses no trusted header at all (closing the LAN-spoof hole by construction).
4. **Defense in depth:** even with an injected principal, a control route 403s AT HOME (the `require(cap)` dependency
   re-derives caps) — the relay's role stamp is advisory, the home's check is authoritative. The `none` provider is
   inert remotely because of step 3, so "admin-for-all over the WAN" is structurally impossible.

**Principal injection (who the remote caller is).** The relay proves identity (Google OIDC, or a viewer link it minted)
and forwards a **home-verifiable principal token** in the `REQ_OPEN` header (`principal_token`). The home injects the
principal ONLY from that token (verified against `relay_pubkey` / `viewer_link_pubkey`, W2.3), **never** from plaintext
`role`/`email` strings, and **never** from a session cookie/header (stripped in step 1). The relay holds **only a public
key** — it can drop obviously-bad attempts but can NEVER mint or escalate; the home re-verifies every request. **The
relay holds NO signing secret and CANNOT forge a principal** (owner invariant).

- **`device_token` blast radius:** document rotation; a leaked `device_token` lets an attacker impersonate the home's
  WSS to the relay — bind the scope→relay dial with **mTLS or a rotatable per-home device cert** in addition to the
  token (the relay binds a tunnel to a KNOWN scope). mTLS authenticates the PIPE; it is NEVER an input to AstroDeck
  authorization (that is always re-decided at the home).
- **RESIDUAL TRUST: the relay is a plaintext MITM — ACCEPTED RISK (named explicitly).** TLS terminates AT the relay, so
  a compromised relay can READ all tunnelled traffic (bodies, raw FITS/PNG, the principal, cookies) and act as an
  already-authenticated VIEWER in real time — even though token MINTING is withheld from it (public-key-only). Document
  this in `SECURITY.md`. Mitigations: **(a)** mTLS scope↔relay; **(b) tunnel-block the privilege-defining writes** —
  `admin`/`config.*` routes are **unreachable over the tunnel regardless of principal** (the home refuses them on any
  REMOTE-flagged scope, BEYOND the per-request 403). **Named explicitly: `POST /api/auth/config`,
  `POST /api/remote/config`, the `auth`/`remote` blocks on `POST /api/config` (W2.2), and the `admin.users`
  revoke/unrevoke API are NEVER reachable over the tunnel** — so even a compromised relay forwarding a forged-admin
  principal cannot flip `provider=none` or re-key/disable the relay from the WAN. **(c)** the owner's decision is
  **FULL remote control for an authenticated ADMIN** (including `control.mount`/`control.power`) gated behind a
  **fresh, short-TTL step-up** for DESTRUCTIVE_CAPS (`capabilities.py:45`), so a long-idle remote session cannot slew or
  open the roof without re-auth; **friends are view-only via RBAC**.

#### W3.3.3 Backpressure + resync (scope side)

- **The scope client subscribes to the bus EXACTLY ONCE, never once-per-viewer.** `events.py` hands out one
  `asyncio.Queue(maxsize=500)` per `bus.subscribe()` with a shared **drop-oldest** policy (`events.py:32`/`46`).
  Subscribing once-per-viewer would put M `maxsize=500` queues + M JSON fan-outs in the synchronous publish loop on a
  Pi — pathological. **Pin the model:** the `relay_client` is a **SINGLE bus subscriber** (one queue), then maintains
  its OWN **per-viewer bounded buffer keyed by `ws_id`** with drop-or-disconnect THERE — never M real bus subscribers.
  A slow viewer's per-`ws_id` buffer fills and **that viewer alone is dropped/disconnected**; it NEVER blocks the shared
  WSS (one stuck consumer applying tunnel flow-control backpressure to the single socket would stall every other viewer
  AND the request path). **Per-viewer buffer bound + per-event-type drop policy:** a bounded per-`ws_id` buffer (~200
  events); on overflow **drop oldest STATUS** (tolerable) but **keep the LATEST preview/sequence** (coalesce, don't drop
  the newest). Per-viewer `WS_DATA` streams give delivery isolation, but ONE bus subscription sits behind them all.
  - **The RELAY→BROWSER leg has its OWN symmetric per-browser bounded EGRESS buffer — a second, distinct buffer from the
    scope-side one.** The scope-side per-`ws_id` buffer (above) protects the HOME's single bus subscription. But a slow
    BROWSER reading from the RELAY is a separate hazard: if the relay had no egress bound, one slow browser would
    back-pressure the relay's READ of the scope WSS (stalling every sibling viewer of that home) and balloon relay
    memory (a multi-tenant DoS — one slow browser degrades other tenants). So the **relay service maintains a per-browser
    (per-`ws_id`) bounded EGRESS buffer** with the **same drop/coalesce policy** (drop-oldest STATUS, keep-latest
    preview/sequence) and **disconnects a browser on sustained overflow**. **The two buffers are DISTINCT:** the
    scope-side per-`ws_id` buffer protects the bus subscription; the **relay-side per-`ws_id` egress buffer protects the
    relay's read of the scope WSS AND other tenants**. **The per-viewer `seq` survives BOTH buffers**, so a relay-side
    drop is a **detectable gap** (the browser sees a non-contiguous per-viewer `seq` and re-snapshots) — not silent. Add
    a **§T7 relay-service case:** a **stalled browser is dropped** while a **sibling on the SAME home keeps receiving all
    events** (the slow browser's egress buffer overflows and it is disconnected; the fast sibling gets the full stream —
    the relay's read of the scope WSS never stalls).
  - **Resync on reconnect — concrete protocol, no lost-update race.** The `/ws` handler sends the `hub.summary()` hello
    AFTER `bus.subscribe()` (`app.py:2137-2139`), so any event published between the snapshot read and the first
    `q.get()` (or, over the relay, between re-subscribe and re-hello to a reconnecting browser on a high-latency WAN) is
    silently dropped. Define the resync concretely — adopt BOTH:
    - **Per-VIEWER monotonic `seq` assigned at the per-`ws_id` FAN-OUT point** (NOT a per-Event counter stamped at the
      shared bus). A `seq` stamped once per `Event` at the shared bus is a **GLOBAL** counter, but the scope client is a
      **SINGLE bus subscriber** whose per-`ws_id` buffers join at different times and **drop/coalesce independently** — so
      every legitimate per-viewer drop would look like a gap and fire a **spurious re-snapshot**. Therefore `seq` is
      assigned **after the single `bus.subscribe()`, in the `relay_client` per-`ws_id` projection** (carried in the
      `WS_DATA` header): monotonic **per viewer**, incremented **only for frames actually delivered to that `ws_id`**.
      **Intentional coalescing advances `seq` by exactly 1** carrying only the latest frame — NOT a gap. A viewer detects
      a real gap only as a non-contiguous per-viewer `seq` and requests a re-snapshot.
    - **Buffer-before-snapshot + dedupe:** the relay buffers incoming events into the per-viewer buffer BEFORE sending
      the snapshot, and the snapshot (`hub.summary()`) is **authoritative for all at-least-latest classes**
      (status / sequence / preview-id) so a missed intermediate event is harmless.
    - **"Resync without duplicating"** = re-attach the SAME single bus subscription (never add a second), re-send
      `hub.summary()` per browser, and **RESET (not duplicate) the per-viewer buffers** for the reconnecting `ws_id`s.
  - **Isolation + reconnect.** The scope client runs as an **ISOLATED lifespan background task that can NEVER block or
    crash `_lifespan` or local serving — LAN access is 100% unaffected by a relay outage** (invariant §4.7). Reconnect
    uses capped exponential backoff + **FULL JITTER** (~15 s cap; mirror `ws.ts`) **with a max-retry/alert policy** so
    many homes redialing one relay after a deploy don't thundering-herd. On redial send a higher `generation` (the
    restart-surviving fencing token in `HELLO`) so the relay **evicts the home's stale WSS** — reconnect-safe, no
    split-brain routing to a half-dead socket. **Degrades to local-only if the relay is down.**
  - **Idempotency:** define a `stream_id` **dedup window**; mark non-idempotent control POSTs (e.g. `mount/goto`) so a
    lost-response-after-reconnect surfaces as an **error**, not a silent retry. (The mount-motion lock in §4.10 is the
    backstop that prevents a stale REMOTE slew from firing after a LOCAL abort regardless of retry behavior.)

#### W3.3.4 DEVICE REGISTRATION + AUTH (scope ↔ relay)

- **The scope authenticates to the relay with a `device_token`** (a SECRET on `RemoteConfig`, issued once, rotatable).
  On `HELLO{device_token, home_id, generation}` the relay validates the token and **maps it to a stable scope
  endpoint** — a path or subdomain (`home_id` in the URL, e.g. `https://relay.example/h/<home_id>/...` or
  `<home_id>.relay.example`) so a remote browser has a stable address for THIS scope.
- **One scope per token; reconnect-safe.** A second live WSS presenting the same `device_token` with a HIGHER
  `generation` **evicts the older socket** (the fencing token); an EQUAL-or-lower generation is rejected. A restart
  re-dials with a monotonic, restart-surviving generation (clock-derived or persisted) so a rebooted scope is never
  treated as stale.
- **`device_token` blast radius** (restated): a leaked token lets an attacker impersonate the home's WSS — bind the dial
  with **mTLS or a rotatable per-home device cert** so the relay binds a tunnel to a KNOWN scope. mTLS authenticates the
  PIPE only; never an AstroDeck-authorization input.

#### W3.3.5 The relay SERVICE (standalone, minimal deps, host-agnostic) — `relay/` (NEW, deployed separately)

A small standalone service (its own `relay/` dir; lean **Python+`websockets`/`uvicorn`** or **Go** — the owner deploys
it). **Minimal deps; HOST-AGNOSTIC: ships a Dockerfile + a Fly.io config.** Responsibilities:

  - **Terminate browser HTTPS+WSS** and the scope WSS; map a browser HTTP/WS connection to tunnel frames and back
    (`stream_id` ↔ browser request; `ws_id` ↔ browser `/ws`), fanning `WS_DATA` out to the right browser.
  - **Device registration** (W3.3.4): validate `device_token`, pin `home_id → live scope WSS`, fence by `generation`.
  - **Identity termination for remote users** via Google OIDC (the SAME provider config as W2; the relay owns the public
    `/auth/google/callback` since the LAN home has no public callback URL). It forwards a **home-verifiable principal
    token** (W2.3: home-issued, or signed with a key the home holds the PUBLIC half of). **It holds NO signing secret
    that could mint home-trusted admin** — public-key-only. The home re-derives the role and 403s independently.
  - **Viewer-link issuance** — a scoped, expiring link that mints a **viewer-only** session for a friend (no Google
    account required): `GET /share/{token}` → short-lived `viewer` JWT **signed with the SEPARATE viewer-link key**
    (W2.3) so a link-minting bug can never forge an admin/config session. The viewer JWT carries an **explicit
    capability set**, a unique **`jti`** in the server-side allow/deny registry so a leaked `/share` link is **revocable
    before `exp`** (independent of `session_secret` — rotating that key logs everyone out). Define: an explicit
    **TTL + renew-while-connected** for multi-hour imaging sessions; **max concurrent viewers per link**; **per-link
    audit logging**. `config.*`/`admin`/`control.mount`/`control.power`/`config.alerts` are never reachable through a
    viewer link, enforced again at the home.
  - **`view` is SPLIT in the W2.1 table — a viewer link carries only `VIEWER_LINK_CAPS`.** The split
    (`view.status` / `view.preview` / `view.media` + `view.site_precise`) is now canonical in the W2.1 capability table,
    NOT just this prose. A `/share` link carries the default `VIEWER_LINK_CAPS = {view.status, view.preview}` so it can
    live-watch (status + downsized preview) WITHOUT bulk science-frame download or precise coordinates. Decisions baked
    in and ENFORCED at the table + boot assertion (W2.1): **raw-FITS download (`GET /api/preview/{id}/fits`,
    `app.py:1044`/`:1057`) is gated on `view.media`, which is NOT in `VIEWER_LINK_CAPS`** (off by default — a viewer link
    grants it only by explicit per-link opt-in); and **precise site coordinates are gated on `view.site_precise`** (also
    excluded by default — absence coarsens/redacts the coordinates server-side per the redaction bullet below). The boot
    assertion rejects any preview/FITS route still tagged the retired monolithic `view`, so this is closed at the
    single-source-of-truth table, not only in the share UI.
  - **REDACTION IS A SERVER-SIDE PROPERTY OF SNAPSHOT/EVENT GENERATION — the "coarse/redacted toggle" has nowhere else to
    run.** Three lenses converge into a leak with **no implementation seam** as written: (1) `hub.summary()`
    (`hub.py:404`/`:409`, verified) **unconditionally** embeds full site `lat`/`lon` AND `redacted(config_store.cfg())`
    — and `redacted()` keeps **precise coords, alert destination hosts, AND the full safety/escalation block**; (2) that
    summary is the `/ws` **hello** (`app.py:2139`) and is **re-broadcast on every config-bus event**; (3) the tunnel
    carries `/ws` JSON as **verbatim opaque payload bytes** (the `WS_DATA` payload), so a redaction step bolted onto the
    relay has nothing to act on. So make redaction a **server-side property of snapshot/event generation**: add a
    **`principal`/`scope`/`caps` parameter to `hub.summary()`** (and to the `'config'` bus emission) so a caller **lacking
    `view.site_precise`** (coarsen coordinates to grid/hemisphere) **and lacking `config.site_optics`** (strip the
    `site`/`alert`/`safety` blocks from the embedded config) gets the coarsened/stripped frame **BEFORE it reaches the
    queue**. Because the **scope client is a SINGLE bus subscriber** fanning out per `ws_id` (W3.3.3 backpressure), it
    **cannot rely on one shared redacted snapshot** — the per-viewer coarsening happens in the **`relay_client`
    per-`ws_id` projection**, so the **`WS_DATA` payload is scope-specific, NOT globally identical** across viewers (an
    admin's `ws_id` carries precise coords, a viewer-link `ws_id` carries the coarsened frame). The two view capabilities
    — **`view.site_precise`** (precise lat/lon) and re-use of **`config.site_optics`** for the site/alert/safety config
    subset — are EXCLUDED from the default viewer-link set. **§T test:** a viewer-capability WS hello AND a `config` bus
    event contain **NO precise lat/lon and NO alert/safety config** (the coarsened/stripped shape), while an admin hello
    carries them.
  - **Browser ↔ relay** over **HTTPS + WSS** (plain — no Connect/grpc-web); the relay maps a browser HTTP/WS connection
    to tunnel frames and back (`stream_id` ↔ browser request, `ws_id` ↔ browser WS), and fans `WS_DATA` out to the right
    browser.
- **Browser client — ZERO component changes.** The UI's `api.ts`/`ws.ts` are byte-identical for the LAN case (relative
  same-origin stays default). For the remote case the browser simply loads the SPA from the relay origin and talks plain
  `fetch`/`WebSocket` to that SAME origin — the relay reverse-proxies `/`, `/assets`, `/api`, `/auth`, `/ws` down the
  tunnel, so there is no API-base indirection and no CORS. (`ad_session` flips `SameSite=Strict → Lax`, W2.3, the one
  server-side cookie change the cross-origin relay web case needs.)
- **Deployment / scalability — persistent connections ⇒ Fly.io / VPS, NOT Cloud Run.** A home's persistent WSS AND every
  remote browser for that home must reach the **SAME relay instance** (affinity), OR a **shared pub/sub (Redis/NATS)**
  lets any instance route to the one holding the socket. **Cloud Run is disqualified** — its request-scoped lifetime +
  scale-to-zero + instance-recycling fight a kept-open bidi WSS + in-memory registry. **Target Fly.io (always-on,
  Anycast, suited to long-lived bidi streams) or a VPS.** Single-instance is fine to start; the
  in-memory-registry-vs-shared-bus choice shapes the relay's internals, so resolve it in W3. The relay ships a
  **Dockerfile + a `fly.toml`**; the owner deploys it (HOST-AGNOSTIC).
- **Browser RE-ROUTING after a relay-INSTANCE outage.** When the relay instance holding a home's WSS dies, the scope
  re-dials (its capped-backoff redial) and browsers must find the home again. Pick ONE: **(a)** a **shared pub/sub
  (Redis/NATS)** so ANY instance serves a browser by `home_id → current-socket-holder` lookup, OR **(b)** a **stable
  routing key** (`home_id` in the path/subdomain) + an **LB that re-pins** to the instance now holding that home's WSS,
  PLUS a **browser reconnect-backoff that RE-RESOLVES** (re-does the `home_id` lookup / re-hits the LB) rather than
  blindly retrying the dead instance. State the **bounded window** where home-reconnected-but-browsers-not-yet is
  EXPECTED (the scope re-dials faster than browsers notice), and that **browser reconnect re-runs the resync**
  (`hub.summary()` hello + per-viewer `seq` reset) so no events are lost across the failover. Tie this to the §T7(5)
  single-instance-affinity test with an **instance-FAILOVER case:** the socket-holding instance drops, the scope
  re-dials to a new instance, and a browser **re-resolves and resyncs** (hello +
  seq reset) rather than erroring on the dead instance.

### W3.4 Build-vs-buy (the owner chose a custom WS relay — NO Tailscale)

**Tailscale / Cloudflare Tunnel are REJECTED (owner decision).** They would expose the whole LAN service behind a
third-party tunnel's own auth, with **less control over per-user role tagging and viewer-link scoping**, and bind the
deployment to that vendor. The **custom plain-WSS relay is chosen** so the owner owns the auth/role boundary end to end
(it terminates OIDC, mints scoped viewer links, forwards a home-verified principal) AND owns the host (Docker + Fly.io).
The relay is a **dumb byte-forwarder**; it holds no signing secret and re-checks nothing — the home is the sole
authorizer. There is no interim third-party tunnel: the relay is the shipped transport.

### W3.5 Staging

A) **Tunnel protocol + scope client** — `remote/protocol.py` (the binary framing of W3.2: length-prefixed frames,
`stream_id` multiplex, REQ/RESP/WS/PING/HELLO/REVOKE/WINDOW types, per-stream credit flow-control) + `remote/relay_client.py`
(the OPT-IN lifespan task: dial ONE WSS, `HELLO{device_token}`, replay **chunked** HTTP against the in-process ASGI app
with **header-stripping + `scope['state']['astrodeck_remote']=True` injection** + the auth-seam wiring of W3.3.2;
isolated background task; degrades to local-only). Tested OFF-WIRE against a fake in-memory frame channel.
B) **WS tunnelling** (`WS_OPEN` → single `bus.subscribe()` → per-`ws_id` `WS_DATA` stream) with **per-viewer
backpressure isolation** (drop-or-disconnect, never block the shared WSS) + the per-viewer `seq`/resync.
C) **Relay service** — `relay/` (browser HTTPS+WSS termination, device registration + `generation` fencing, Google-OIDC
termination, **home-verifiable** principal token, affinity-vs-shared-bus decision resolved here).
D) **Viewer-link issuance** (separate signing key + `jti` revocation + explicit caps + TTL/renew/max-viewers/audit) +
scoped `viewer` sessions; **deploy target = Fly.io/VPS (Cloud Run disqualified)**; the relay ships a Dockerfile + `fly.toml`.

> **Relay green-each-stage gate (C8-C9) — language decision + gate command + integration-test classification.** The
> relay lives in its own `relay/` dir (deployed separately). **Decide the language up front** because it determines the
> test runner: **Go** (`go test ./...`) **OR Python+`websockets`** (`pytest relay/`) — *lean: Python, to reuse the repo's
> pytest + fakes discipline and the shared OIDC helper.* The **per-stage unit gate is the OFF-WIRE `relay/test_*.py` (or
> `*_test.go`) files** run by that command (browser↔frame round-trip, header transform, OIDC termination, fan-out
> isolation, affinity/failover — all over a **fake in-memory tunnel-frame channel, no WSS on wire**). **The single
> ON-WIRE WSS integration test is a SEPARATE integration gate** — a **named fixture** (local relay + local home), run **on
> demand / a dedicated CI job**, NOT part of the per-stage unit gate. So a stage goes green on the off-wire unit files;
> the on-wire WSS test is an additional scheduled check that does not block the fast inner loop.

### W3.6 CONFIG fields — `RemoteConfig` on `AppConfig` (`config.py`)

A NEW `RemoteConfig` block, appended to `AppConfig` exactly like the `auth` block (additive — old config files without
it load fine; pydantic fills the default). With `enabled=False` (the default) the scope NEVER dials, so today's behavior
is byte-for-byte. The `device_token` is a SECRET and MUST be redacted out of every WS/REST config dump.

```python
class RemoteConfig(BaseModel):
    enabled: bool = False        # OPT-IN: the scope dials ONLY when True AND relay_url is set
    relay_url: str = ""          # wss://relay.example/...  (the public relay the scope dials)
    device_token: str = ""       # SECRET: authenticates this scope to the relay (rotatable)
    home_id: str = ""            # stable id the relay pins (path/subdomain); generated on first enable
    # (mTLS material — device cert/key — may live here later; keep it a SECRET like device_token)

class AppConfig(BaseModel):
    ...
    remote: RemoteConfig = Field(default_factory=RemoteConfig)   # appended; old configs load fine
```

- **Redaction (extend `redacted()`, `config.py:492`).** Add a `remote` branch mirroring the `auth` branch: blank
  `device_token`, surface a `remote_token_configured` / `remote_configured` boolean. The `redacted()` dump already
  scrubs `auth` secrets; `device_token` joins them. **§T:** assert a config dump never contains `device_token`.
- **Mutation is `admin.users`-gated AND tunnel-blocked.** `RemoteConfig` is written ONLY through a dedicated
  `admin.users`-gated `POST /api/remote/config` (NEVER via the general `POST /api/config` merge — `ConfigPatchBody` is
  `extra="forbid"`, `app.py:369`, so a stray `remote` block 422s at binding, exactly as `auth` does). That route is on
  the **tunnel-block list** (W3.3.2) so a compromised relay can never re-key/disable the relay from the WAN.
- **`config_store.set_remote(remote)`** is the typed setter (mirrors `set_auth`, `config.py:402`): bump version, write
  atomically. Enabling it generates a `home_id` if empty.

### W3.7 NON-NEGOTIABLE invariants the HOME enforces (hold even if the relay is fully compromised)

These restate §4 of the council ADR, pinned to this WS-tunnel design. The relay is untrusted infrastructure.

1. **Sun-avoidance is a hardware-safety gate BELOW the API** — `hub._check_solar` (`hub.py:711`) on the slew /
   `set_tracking` / manual-jog / sequence-start chokepoints (`app.py:1529`, `:1569`, inside `goto_and_center`
   `hub.py:1226`). It is NOT bypassed by `force`; disarming needs `config.solar_override` + a config write. No client
   path — remote, relay, or buggy — may point optics at the sun. (W1.10; already landed.)
2. **The open `none` provider can NEVER be served remotely.** Every tunnelled request and the tunnelled `/ws` accept set
   `scope['state']['astrodeck_remote']=True`, which `resolve_principal(..., remote=True)` reads to HARD-DENY `none`
   (`deps.py:141-143`). This is the W3.3.2 wiring — derived from the in-process scope, NOT a forgeable header. **§T:** a
   tunnelled request under `provider=none` resolves to **None (401/deny), never admin**; the scope client REFUSES to
   forward any frame while `provider=="none"`.
3. **The relay can never forge a principal or an admin command.** The home is the sole token issuer; the relay holds
   only a PUBLIC key (`relay_pubkey`/`viewer_link_pubkey`). No `X-Forwarded-User`-style trusted header.
4. **RBAC is re-checked at the home on EVERY request** (`require(cap)`, `deps.py:158`), never decided at the edge. Caps
   fail closed for unknown roles. The remote surface is a deliberate subset (tunnel-block list, W3.3.2);
   **DESTRUCTIVE_CAPS** (`capabilities.py:45`) require a fresh short-TTL step-up so a long-idle remote session cannot
   slew or open the roof.
5. **The `/ws` channel stays send-only.** Commands stay on the cap-gated REST path. Never add an inbound command channel
   over `/ws` (`app.py:2140-2142` never calls `receive()`).
6. **Mount limits / horizon / safety floors are independent of auth.** Remote bypasses neither.
7. **Loss of the relay degrades to safe local autonomy.** The scope client is an isolated lifespan task; a relay outage
   never blocks `_lifespan` or local serving. The home's `on_unsafe` abort/park/warm is the backstop.
8. **A local-only kill switch** tears down the outbound WSS instantly (set `RemoteConfig.enabled=False` locally; the
   scope client stops dialing) — master revocation needing no relay cooperation, unreachable from the remote path.
9. **Per-principal audit + rate-limit at the home tunnel-ingress.** Every relay-borne command is logged at the home with
   the VERIFIED principal (role/email/jti from the signed token), append-only.

10. **MOTION SERIALIZATION — a single mount-motion lock so a stale REMOTE slew cannot fire after a LOCAL abort (owner
    invariant). WHERE IT LIVES: in the `hub`, as a process-global `asyncio.Lock` (`hub._motion_lock`) plus a monotonic
    `hub._motion_epoch` counter — co-located with the existing single-mount state (`hub._busy`, the move-axis deadman
    `hub.last_move_ts`, `hub.py:168-171`). This is the ONE place every motion path already funnels through.**
    - **Today's de-facto serialization (verify, then HARDEN).** Mount motion already funnels through ONE named task
      lane: every slew/park/centering runs as the single `"goto"`-named task via `_spawn("goto", ...)` (`app.py:1533`,
      `:1542`, `:1631`), and `_spawn` **409s a second `"goto"`** while one is live (`app.py:130-131`). `POST /api/mount/stop`
      (`app.py:1594`) **cancels the `"goto"` task** and zeroes the axes; `move_axis` (`app.py:1555`) is a direct await
      guarded by the 250 ms deadman. So concurrent slews are ALREADY rejected — but this is a per-NAME guard, not a true
      motion lock, and it does NOT order an abort against an in-flight start, which is the exact remote-vs-local race.
    - **THE RACE to close.** A LOCAL operator hits abort/stop at the same moment a REMOTE admin's slew is being accepted.
      Cancelling the `"goto"` task does not, by itself, guarantee the device-level `slew()` call already dispatched into
      the driver is countermanded — a slew command in flight to the mount could still execute AFTER the local stop
      returns. The fix is an explicit lock + epoch fence around the device-touching critical section.
    - **THE LOCK CONTRACT (define + enforce at the hub motion boundary).**
      - Every motion-committing hub method — `goto_and_center` / plain slew, `park`/`unpark`, `set_tracking(True)`,
        `move_axis(non-zero)`, and the engine's per-target slew+center+meridian-flip — **acquires `hub._motion_lock`
        around the device call**, reads the current `_motion_epoch` BEFORE the await, and **re-checks the epoch is
        unchanged immediately before issuing the device command**; if the epoch advanced, it **abandons the motion**
        (does not dispatch) and raises a cancelled/aborted result.
      - **Any STOP/abort/park path — `POST /api/mount/stop`, `engine.abort()`, an `on_unsafe` safety abort, and the
        move-axis deadman halt — BUMPS `_motion_epoch` FIRST** (fencing every in-flight or queued slew), then cancels
        the `"goto"` task, then issues `tel.stop()`. The epoch bump is what makes a slew that was accepted-but-not-yet-
        dispatched (the remote race) **abort instead of fire** — it sees the advanced epoch at its pre-dispatch check.
      - **A STOP wins ties deterministically:** because STOP bumps the epoch and a queued slew re-reads it under the same
        lock before dispatch, a slew can NEVER win a race against a stop that has already bumped. A stale REMOTE slew
        accepted just before a LOCAL abort is fenced out by the epoch advance.
      - **The lock guards the device-touching section only** (not the whole long centering loop) so STOP can always
        acquire it promptly to bump the epoch + call `tel.stop()`; the long-running centering loop checks the epoch
        between steps and bails when it advances. The deadman (`hub.py:165-170`) remains the independent hardware
        backstop for manual jog.
    - **RBAC controls WHO; this lock controls concurrent conflicting WHATS.** They are orthogonal: RBAC may admit the
      remote admin's slew, and this lock+epoch still guarantees a subsequent (or concurrent) LOCAL abort fences it.
    - **§T motion-race test:** start a (sim) slew, fire a stop/abort that bumps the epoch mid-flight, and assert the
      device `slew()` is **abandoned at its pre-dispatch epoch check** (no post-abort device motion); assert a second
      concurrent slew is rejected; assert `move_axis` non-zero respects the same lock/epoch.

---

## W4 — NATIVE DRIVERS / IN-PROCESS GUIDER (roadmap brief)

Not built in this program; specced here so the W1 seam is sized for it. **All of W4 is additional `Backend`
registrations — zero hub edits.**

- **`NativeGuider`** — in-process **multi-star centroiding** on the guide camera (`devices/sim.py`'s guide-cam path is
  the test fixture) + mount `pulse_guide` (`devices/base.py` already has `pulse_guide` on `Telescope`). A drop-in behind
  the same `Guider` interface (`guide/base.py`) → the true "no separate binary" end-state that retires managed PHD2.
  Registered as a `Backend` with `roles=("guider",)`, exactly like `Phd2Backend`. **Must implement
  `flip_calibration`** (per W1.12/W1.13): negate the RA (and, per its calibration model, possibly Dec) calibration
  vector; the hub gates the call per the INVERTED pier-side gate (W1.13), and guider/mount may be on different backends.
  - **NativeGuider forces ADDITIVE `Telescope` ABC methods — size the seam honestly, it is more than a Backend
    registration.** Converting a centroid error (pixels → arcsec) to a `pulse_guide` duration needs the mount's
    **guide-rate (arcsec/sec PER AXIS)**, and deciding whether to pulse-guide at all needs a **pulse-guide capability
    flag** — NEITHER is on the `Telescope` ABC today (only `pulse_guide(direction, ms)` exists; **no guide-rate accessor
    exists ANYWHERE on the ABC/Alpaca/NINA/sim**). The guide-rate seam is **per-axis**, not a single scalar: ASCOM exposes
    `GuideRateRightAscension` and `GuideRateDeclination` as **separate, independently SETTABLE** properties — so the
    additive seam is a **per-axis guide-rate READ and an optional per-axis SET** (a NativeGuider may want to set the rate
    before calibrating, not just read it). `flip_calibration`'s "negate RA/Dec" also depends on a **calibration model the
    ABC does not define**, and Dec-compensation needs a **declination hook**.
  - **NativeGuider must implement its OWN calibration routine — it is NOT a thin wrapper, and THIS is the real reason it
    is a larger lift than a Backend registration.** Calibration — measuring the mount's actual response per axis (push a
    known pulse, measure the star's pixel displacement, derive the px↔arcsec↔ms transform and the axis angles) — is
    work **PHD2 provides for free** and that NativeGuider must **duplicate from scratch**. So the W4 NativeGuider cost is
    dominated by re-implementing calibration + the guiding loop, not by the `Backend`/`Guider` plumbing. **Keep OPEN
    DECISION 3** (managed-PHD2-now vs NativeGuider-soon), but note the **calibration re-implementation as the concrete
    reason** NativeGuider is a substantial lift, not a quick drop-in — so the decision is made with the real cost visible.
- **Per-role `safety` override for unattended/remote use** — `NinaBackend` omits the `safety` role (W1.2), so a
  NINA-primary rig has no fail-closed safety engine. For unattended (W3 remote) operation, override the `safety` role to
  a **native Alpaca `SafetyMonitor`** (`AlpacaSafetyMonitor`, `alpaca.py:648`) — a concrete, already-supported use of
  the per-role override model that closes a real unattended-safety hole.
- **Native / INDI / Rust device drivers** — additional `Backend` registrations (e.g. `IndiBackend`, a Rust-FFI
  `RustBackend`) filling camera/telescope/etc. roles directly, bypassing both NINA and Alpaca where a native SDK is
  faster/richer. Adding the **`dome`/`roof` and `flat-panel`/`CoverCalibrator` roles** (deferred from W1.9) lands here
  as the known multi-file role additions. **Size the `CoverCalibrator` work to include brightness control + cover
  open/close + sequence-engine integration (an auto-flat target type)** — not just a bare device class — since until it
  lands, an unattended/remote rig's flat frames remain a MANUAL on-site step (W1.9).
- **NINA → Rust port** — the eventual replacement of the NINA bridge with a Rust service exposing the same
  `BackendSession` contract; the harness means the UI/hub never know the difference.

---

## OPEN DECISIONS

1. **Relay hosting target** — Fly.io vs a small VPS. **Cloud Run is DISQUALIFIED (owner decision)** — its request-scoped
   lifetime + scale-to-zero + instance-recycling fight a kept-open bidi WSS + in-memory registry (W3.3 / W3.5). The relay
   ships HOST-AGNOSTIC: a **Dockerfile + a `fly.toml`** (the owner deploys it). The only remaining sub-decision is
   sticky-routing of `home_id`→instance vs a shared pub/sub (Redis/NATS) for multi-instance routing — resolved in W3.C
   because it shapes the in-memory-registry-vs-shared-bus architecture. *Decided: Fly.io/VPS; single-instance to start.*
2. **Interim off-the-shelf tunnel?** — **CLOSED (owner decision): NO.** No Tailscale / Cloudflare-Tunnel stopgap. The
   custom plain-WSS relay (W3) IS the shipped transport; the owner owns the per-user role boundary and the host end to
   end. *Closed.*
3. **Native-guider ambition timing** — does `NativeGuider` (W4) land soon enough to skip investing in managed-PHD2
   polish (W1.7), or is managed PHD2 the supported path for the foreseeable future? **The real cost driver is that
   NativeGuider must RE-IMPLEMENT calibration** (per-axis mount-response measurement) that PHD2 provides for free, plus
   additive per-axis guide-rate `Telescope` methods (W4 NativeGuider note) — so it is a substantial lift, not a quick
   drop-in. *Lean: managed PHD2 now; NativeGuider as a later drop-in, decided with the calibration cost visible.*
4. **SAML vs OIDC** — if Google **Workspace** SSO becomes a requirement, do we add a SAML provider or stay on OIDC? Both
   fit the `AuthProvider` seam (W2.3). *Open until a Workspace requirement is real.*

---

## SEQUENCING / STAGING — mapped to build chunks

**Program order is strictly W1 → W2 → W3, with W4 as a later append.** Within each workstream the A–D stages each end
green (`pytest` + `npm run build`).

| Chunk | Workstream.stage | Deliverable | Verify (see TEST PLAN §T#) |
|---|---|---|---|
| **C1** | W1.A | 4 wrapping backends (`native_backend.py` rewritten for the pool) + `backends/__init__` import site + **`assemble`→`connect_profile` rename/reshape** (W1.3.0) + sun guard at the motion boundary; hub `rig`/`sessions`; old connect paths reimplemented over it | **ENTRY:** W1.5 characterization snapshots vs `assemble()` (§T3). per-wrapper `test_{sim,nina,native,phd2}_backend.py` (§T1) + **rewritten** fault-injecting `test_orchestrator.py` (§T2, `to_dict`-shape back-compat carried) + `test_sun_guard.py` (§T1.10) + §T1.12 pier-gate + existing green |
| **C2** | W1.B | RigSpec↔Profile persistence, `/api/backends`, `/api/connect/rig`, boot auto-connect (parked, failure-swallowing) | multi-backend profile round-trip + reconnect; boot-connect-failure isolation + mixed-rig integration (§T4) |
| **C3** | W1.C | Settings backend picker (primary + per-role, resolved-backend display, mixed-rig confirm) + per-role badges + managed-PHD2 toggle + `backend_links`/boot-LED readout | `npm run build` **+ UI unit tests** (§T8) |
| **C4** | W1.D | retire ALL `self.mode`/`sim_rig`/`nina_client` reads; `backend_links` array; `mode` = derived label; per-role SimSolver guard + **W1.11 filter-offset decoupling** (gate `engine._apply_filter` offset move on NOT `fw.applies_focuser_offset`) | full `pytest` across sim/native/nina/mixed + **source-grep guard** (§T3) + **§T1.11 filter-offset skip/apply** |
| **C5** | W1.7 | managed PHD2 supervisor (allowlisted spawn, on-disk profile write, set_connected/find_star/loop/cal-status RPCs, watchdog) | injected-launcher supervisor tests (§T5) |
| **C6** | W2.A–B | `AuthProvider("none")` + `requires()` + route table + **boot `app.routes` assertion** + accept-time WS gate + asymmetric session JWT + `jti` + allowlist API + client role model | every existing test green (admin default) + RBAC dependency/WS/JWT tests (§T6) |
| **C7** | W2.C–D | Google OIDC provider (state/nonce/email_verified/hd) + login UI + `operator` role | OIDC verify (faked JWKS, no live Google) + 403 matrix per role (§T6) |
| **C8** | W3.A–B | `remote/protocol.py` (binary tunnel framing: `stream_id` multiplex, REQ/RESP/WS/PING/HELLO/REVOKE/WINDOW, per-stream credit) + `remote/relay_client.py` (OPT-IN lifespan dial-out over ONE WSS, chunked HTTP/WS replay against the in-process ASGI app, `scope['state']['astrodeck_remote']=True` injection + `RemoteConfig`) + the mount-motion lock/epoch (W3.7.10) | off-wire relay_client scope/framing/replay + `remote=True` deny-`none` + home-side 403 re-check + reconnect + motion-race (§T7) |
| **C9** | W3.C–D | relay service (`relay/`: browser HTTPS+WSS, device registration + generation fencing, OIDC termination, home-verifiable principal token, viewer links: separate key/jti/caps) + backpressure isolation; deploy = Fly.io/VPS (Docker + `fly.toml`) | relay JWT-minting/viewer-link unit tests (off-wire); ONE on-wire WSS transport integration (§T7) |
| **C10** | W4 | (roadmap) `NativeGuider` (+ `flip_calibration`) + native/INDI/Rust backends as new registrations | — |

**Critical path:** C1 (the seam) unblocks everything; its ENTRY gate is the §T3 characterization snapshots taken BEFORE
any refactor. C4 (decoupling) should not start until C1–C3 are green so the branch retirement is verified against a
working harness. C6 (RBAC `none`-default) is independent of W1 internals and can overlap W1.C/D. C8 must not start until
C6–C7 land (the relay tags roles W2 defines).

---

## TEST PLAN (repo convention: in-process **fakes**, NOT `unittest.mock`; UI via `npx tsx` inline-assert harness)

> Grounding: the registry test already exists as `test_backend_registry.py`; PHD2 tests reuse `FakePHD2` from
> `test_phd2.py`; NINA tests reuse the `tools/mock_nina` ASGITransport; `ui/` has **no vitest/jest** today — only
> hand-rolled `npx tsx` `*.test.ts` files over `lib`/`store`, and the only "test" script is `npm run build` (compile).

**§T1 — per-wrapper unit tests (W1.2 / C1), one file per BackendSession:**
- `test_sim_backend.py`: every role maps; `native_guider()`/`native_solver()` identity; SimSolver guard **refuses on a
  real rig** (camera-session-keyed, W1.5).
- `test_nina_backend.py` over `tools/mock_nina` ASGITransport: two NINA roles **share ONE client object** (assert object
  identity); `health()` returns the `nina_link` shape; `close()` does **NOT** tear down external NINA.
- `test_native_backend.py` with a fake `AlpacaConnection`/httpx ASGITransport: two roles on the **same host:port reuse
  one connection**, a **different host opens a second**; `dev.role` set + `connect()` awaited; **`close()` actually
  `aclose`s EVERY owned connection** (the §T1 assertion that is impossible to satisfy against the committed code, where
  `close()` only clears a dict and `make_device` hides the connection inside the device — W1.2). Also assert
  `NativeBackend.roles` does NOT advertise `guider`/an unfillable role (W1.9): `get_device('guider')` is never offered
  because the role is absent from `backend.roles`.
- `test_phd2_backend.py` reusing `FakePHD2`: `get_device(non-guider)` → **raises `KeyError`** (`pytest.raises(KeyError)`,
  matching the committed `phd2_backend.py:56-59` and committed `test_phd2_backend.py:116-119`); `native_guider()` identity.
- **`test_backend_roles_match_served.py` (W1.9):** for EVERY registered backend, assert each role in `backend.roles` is
  actually serviceable — `get_device(role)`/`native_guider()` does not `KeyError` — so an over-broad `roles` tuple (the
  shipped `NativeBackend.roles = ROLES` advertising `guider`/`safety`) fails the test.

**§T2 — orchestrator degradation/grouping fault injection (W1.3 / C1) — the C1 gate.** This **REWRITES the committed
`tests/test_orchestrator.py`** (W1.3.0): the existing happy-path `assemble` tests are replaced by a
`FakeBackend`/`FakeSession` fault-injecting suite targeting `connect_profile`, while the existing `to_dict`-shape
(`{roles, sessions, has_guider, has_native_solver, failures}`) and graceful-degrade assertions are **carried forward**
(back-compat keys preserved, plus the new `results` list) so the rename+reshape is proven non-regressive. **The
carried-forward `to_dict`-shape assertion MUST be rewritten to exercise a rig with TWO same-host endpoints of differing
port-presence** — `(backend, host, 11111)` AND `(backend, host, None)` both keyed under the same host — and assert
`to_dict(result)["sessions"]` returns a sorted list of JSON-able strings WITHOUT raising. This is the C1 gate that
catches the `TypeError: '<' not supported between instances of 'int' and 'NoneType'` an implementer would ship by
carrying `sorted(result.sessions)` (`orchestrator.py:129`) forward over the new tuple keys (W1.3.0 to_dict row), and the
non-JSON-able raw-tuple regression — neither is caught by a single-endpoint shape assertion. Enumerate:
(1) `open()` raises → every role on that endpoint `RoleResult(ok=False, attempted=True)`, OTHER backends still come up;
(2) one role's `get_device` raises → that role degraded, sibling roles on the same session still in the rig;
(3) `get_device` returns `None` → `RoleResult(ok=False, attempted=True)`, no crash; (4) two roles on one endpoint →
`open()` called **exactly once** (call-count); (5) solver precedence native → ASTAP → guarded sim; (6) guider picked
from the **guider-role's** session including the PHD2-override case (and NOT emitted as a device RoleResult); (7) two
native hosts → **two connections**, correct device per host; (8) a later step raising → already-opened sessions are
`close()`d (teardown contract); (9) **every REQUESTED role gets exactly one `RoleResult`** and a role NOT requested gets
**none** (the tri-state/“not requested” basis for the W1.6 LED, so an unfillable role shows no red LED);
(10) **hostless coalescing (W1.3):** a `primary=sim` rig with a sim override carrying a **non-None `host`** opens the
`SimRig` **exactly ONCE** (one session; the override role and a primary role share the same shared state) — stray
addressing cannot split shared sim state; (11) **"zero hub edits" proof (W1.5):** a NEW `FakeBackend` filling
`camera`+`telescope`, registered with ONLY `register()` + one import line, comes up through `connect_profile` with **NO
new hub method** — `hub.rig` populated, `device.connect()` awaited per role, no `KeyError`, no `connect_fakebackend`.
**Add a HOSTLESS variant of this same `FakeBackend`** (`hostless = True`) carrying a stray **non-None `host`** on ≥2 of
its roles: assert all those roles STILL coalesce to **ONE** session (one `open()` call), proving `_group` keyed off
`get_backend(name).hostless` rather than a literal `{sim, phd2}` name set — a name-list `_group` that only special-cases
`sim`/`phd2` would split this new hostless backend into two sessions and FAIL here;
(12) **empty-sessions on total-primary-failure (W1.3.0):** a single-endpoint rig whose only `open()` raises returns
**empty `sessions`** and the thin builder **re-raises** the recorded `RoleResult.error` (matching the committed
`hub.py:222-224` NINA fail-fast), not a silent partial-connect; (13) **stray-addressed guider still found (W1.3.0
`_pick_guider` seam):** a guider override on a HOSTLESS backend (`sim` or `phd2`-local) carrying a **stray non-None
`host`** is STILL resolved — `_pick_guider` keys `sessions` with the SAME normalized `(backend, None, None)` key `_group`
used, so `guider is not None` and its `RoleResult` is **`ok=True`** (NOT a spurious `"no guider"`). This mirrors the
§T2(10) imaging-path stray-addressing case for the guider seam and fails any implementation keying `_pick_guider` off the
raw `(conn.backend, conn.host, conn.port)`.

**§T2/§T4 — mixed-rig per-role SimSolver derivation.** A MIXED rig — `primary=native` mount + camera OVERRIDDEN to
`sim` — asserts `_pick_solver` selects the **guarded `SimSolver` via the CAMERA session** and that `get_solver` receives
the mode derived from the **camera session name** (not a hub-wide mode); then assert the `SimSolver` **REFUSES to sync**
because the mount being synced is REAL (the fake-center hazard, W1.5). Add the inverse (`primary=sim` + real-mount
override) and assert the same refusal. This nails the per-role derivation that §T2(5) only covers for same-backend
precedence.

**§T3 — characterization + guard (W1.5 / C1+C4 ENTRY/EXIT).** BEFORE the refactor, snapshot today's outputs on a NINA
rig and a sim rig: the `capture_profile` ConnSpec list, the `_preview_source` value, and the FULL `poll_status` dict
(asserting `backend_links` is present AND `nina_link` still equals the NINA entry for back-compat). Run identical
assertions AFTER. Add a **source-grep guard test**: no `self.mode ==` remains except the derived-label assignment, and
no `self.sim_rig`/`self.nina_client` reads outside `SimSession`/`NinaSession`.

**§T1.10 — server-side SUN-EXCLUSION guard (W1.10 / C1+C4 gate) — the single most safety-critical new test.** New
`test_sun_guard.py` with a **FIXED clock** (pass `unix_time` to `sun_radec`/`sun_altaz`, `catalog/coords.py:81`/`:103`)
and fixed site lat/long: (1) **target** alt-az inside the cone → rejected; (2) target clear but **CURRENT mount** alt-az
inside the cone → rejected; (3) `goto force=true` does **NOT** bypass the sun cone (still rejected) though it may bypass
the visible-horizon check; (4) with `is_default=true` the guard STILL fires (independent of site default); (5) a caller
holding `config.solar_override` is permitted, **without it → 403**; (6) `/api/mount/move` gated identically;
(7) a **forced SEQUENCE** (`/api/sequence/start force=true`) toward a sun-adjacent target is **rejected without
`config.solar_override`** (covers the dawn-during-unattended-sequence path, since the guard lives in `goto_and_center`,
not the route); (8) a night target away from the sun PASSES; (9) **periodic-drift case (W1.10):** a TRACKING mount whose
alt-az was CLEAR at slew time but **drifts INTO the cone over time** (advance the fixed clock so `sun_altaz` overtakes
the current mount alt-az) triggers the SAME SAFE-ING halt **from the status poller** (stop tracking, escalate) — not only
at goto/move/connect. This is a C1/C4 gate for W1.10.

**§T1.10 (connect-time SAFE-ING halt) — the single most NOVEL safety action, currently untested.** The W1.6 connect-time
SAFE-ING halt (a real mount found **unparked + tracking with current alt-az INSIDE the sun cone** is **STOPPED/PARKED
BEFORE any banner**, gated by `config.solar_override` to resume) is a stage-B deliverable with **no test in §T1-§T8** —
add one (a new `test_sun_guard.py` case, or `test_connect_safe_ing.py`) driving `connect_profile` with a **FAKE telescope
reporting `unparked` + `tracking=true` + alt-az INSIDE the sun cone** (fixed clock + fixed site, reusing the
`sun_altaz`/`unix_time` seam): assert **(a)** `set_tracking(false)` (and `park` if configured) is called **BEFORE any
banner/alert event** — **record the fake's method-call order** to prove halt-before-banner; **(b)** the action fires
**INDEPENDENT of `site.is_default`**; **(c)** a mount found **idle/parked is left UNTOUCHED**; **(d)** RESUME requires
`config.solar_override`; **(e)** the escalation is asserted as the **two-signal dual surface** (W1.6): a status/chip flag
on `/api/status` (persistent header chip) AND a **TYPED bus event published to a RECORDING FAKE BUS** (the
`test_engine_*.py` pattern), NOT a real outbound `httpx` send — the bus event proves the escalation will fire via
`AlertDispatcher._on_bus_event` (`alerting.py:233`) without coupling the test to network delivery (which the existing
`MockTransport` alerting tests cover separately).

**§T1.11 — filter-offset double-apply skip/apply (W1.11 / C4).** New case in the `test_sequence.py` / `test_engine_*.py`
in-process fakes suite (NOT `unittest.mock`): drive `engine._apply_filter` across a filter change with an in-process
**fake `FilterWheel`** (carrying `filter_offsets`) and **fake `Focuser`** (recording `move_to` calls), `plan.apply_filter_offsets = True`. (1) **SKIP:** when the active filterwheel advertises `applies_focuser_offset = True`, assert
`focuser.move_to` is **NOT called for the offset** (the engine must skip its own `move_to(pos + delta)` because NINA
applies it natively — the double-correction guard). (2) **APPLY:** when the flag is **absent/`False`**, assert the engine
**APPLIES** the offset — `focuser.move_to(pos + delta)` is called exactly once with the per-filter delta. Grounds the
W1.11 fix: committed `sequence/engine.py:1358-1364` does `await foc.move_to(pos + delta)` UNCONDITIONALLY (it consults no
`FilterWheel.applies_focuser_offset` flag), so a NINA-primary rig double-corrects focus on every filter change TODAY;
this test fails until the move is gated on NOT `fw.applies_focuser_offset`. (Wired into a chunk gate via C4/W1.D's
decoupling pass — see the chunk table Verify column.)

**§T1.12 — cross-backend meridian-flip pier-gate (W1.12/W1.13).** With a fake **reporting GEM** mount (reporting
pier_side east/west so `_is_gem(side)` is true) reporting an **UNCHANGED** pier side across the flip: assert
`guider.flip_calibration()` is **NOT called only when the side is KNOWN-and-unchanged**; with a **confirmed side change**
assert it **IS called exactly once**. **Add a NON-REPORTING GEM case (the runaway-RA regression guard) — a DISTINCT
mount from the fork case below:** `does_meridian_flip = True` (the mandatory W1.13 flag), `pier_side()` returns
**UNKNOWN** (non-reporting), and `time_to_meridian_flip()` returns `None` (`base.py:202`/`sim.py` return `None`;
`nina.py:429` alone overrides): assert the inverted FLIP-UNLESS-known-and-unchanged rule **STILL fires** — i.e.
`flip_calibration` **IS called** even though `_is_gem("unknown")` is **False**, proving the GEM gate keys off
`_is_gem(side) OR the mandatory flip flag`, NOT off `_is_gem(side)` alone NOR off `time_to_meridian_flip() is None`, so
the sim/native non-reporting GEMs the W4 tests target are not mis-classified into backwards guiding (W1.13). **Add a
NON-FLIPPING FORK/ALT-AZ mount case** as a **SEPARATE** mount distinguished as **`does_meridian_flip = False` AND
pier_side == UNKNOWN AND not a flagged/`_is_gem` GEM** (and `time_to_meridian_flip()` returns `None`): assert
`flip_calibration` is **NOT called** even when pier side is UNKNOWN (the GEM-gate-first rule, W1.13 step 1 — so the
inverted rule cannot back-fire on a fork/alt-az mount). The two UNKNOWN-pier mounts are deliberately split: the flag
`True` one MUST flip, the flag `False` one MUST NOT — they differ ONLY in `does_meridian_flip`, proving the flag is the
discriminator. Cover the **cross-backend** case (PHD2-guider fake + native-mount fake on different sessions) so the
**hub-level** coordination — not the guider — triggers the flip. **Add the
MANAGED-PHD2 + meridian-flip case (W1.7):** with managed PHD2 and an EXISTING calibration, the post-flip restart uses
`flip_calibration` and issues **NO new calibration slew** (the fake records no `find_star`/calibrate after the flip) —
so a freshly-written managed profile cannot trigger a full re-calibration that slews a real GEM off the centered target.

**§T4 — boot auto-connect isolation + mixed-rig integration + connect-API + migration (C2/C4).** (1) `_lifespan`
auto-connect **SWALLOWS** a connect failure (patch one backend's `open()` to raise) — app still starts, `/api/status` →
200, rig marked disconnected (a crashed `_lifespan` would brick the UI). (2) Mixed-rig integration (native mount + sim
camera + PHD2 guider via fakes): `hub.rig` has the right device per role from the right session, **exactly the expected
number of sessions** opened, guider is the PHD2 one, solver falls back correctly. (3) Persisted multi-backend Profile
reloads and reconnects to the same rig shape.
- **(4) W1.6 connect-API routes via FastAPI `TestClient`:** `GET /api/backends` returns the `list_backends()` shape;
  `POST /api/connect/rig` with a valid **sim** RigSpec returns 200 with `{rig, results, backend_links}` and the
  documented per-role `RoleResult` fields (`role`/`ok`/`error`/`attempted`); a `POST` whose **EXPLICIT override** role is
  **NOT in the target backend's `roles`** (e.g. an explicit `safety` → NINA override — `safety` is the one role NINA
  cannot fill) is **REJECTED server-side** (the W1.C server-side validation), not silently accepted; **but a `nina`
  PRIMARY that resolves `switch` from the primary is ACCEPTED** (the reject-rule is explicit-override-only, and `switch`
  is now in `NinaBackend.roles` — W1.9), proving the rule does NOT fire on primary-derived resolution. Assert
  `_requested_roles(spec)` for a `nina` primary with one `guider` override equals `get_backend('nina').roles | {'guider'}`
  (W1.3), and that a role absent from that set gets NO `RoleResult` (the §T2(9) tri-state basis).
  `GET /api/discover/{backend}` delegates to that backend's `discover()` and an **unknown backend 404s**.
- **(5) Legacy-profile migration (upgrade-day) — OWNED BY `test_profiles.py`, including the alias guard.** To remove the
  ambiguity between this case and the W1.8 stage-B gate (which says `test_profiles.py` round-trips the multi-backend
  RigSpec + legacy migration), the migration test **lives in `test_profiles.py`** and **explicitly includes the
  `alpaca`→`native` alias-not-`KeyError` assertion** (the single most upgrade-day-breaking case — a legacy profile that
  `KeyError`s `get_backend` on boot). The real `profiles.ProfileDevice` has `backend='alpaca'` +
  `host/port/dev_type/dev_num` and **no role-as-`ConnSpec`, no `extra`/`managed`**, and `Profile.mode` is derived on
  `{'alpaca'}`/nina. Construct a Profile in the OLD schema on disk (a `ProfileDevice` with `backend='alpaca'`, plus an
  `nina_host`-only profile), assert it deserializes and maps to a valid `RigSpec` (primary + roles) whose
  `connect_profile` produces the **same rig shape as before**, AND assert the legacy `backend='alpaca'` → new `'native'`
  key is an **explicit alias/migration**, NOT a silent `KeyError` in `get_backend`. This §T4(5) entry **cross-references
  `test_profiles.py`** so the case is unambiguously owned by ONE file.

**§T5 — managed PHD2 supervisor (W1.7 / C5).** Extend `FakePHD2`; **the launcher AND the port-probe AND the on-disk
profile store are ALL injected dependencies** (no real `phd2.exe`, no real socket, no real registry/FS): the supervisor
takes a `launcher`, an `is_port_open(host, port)` probe, and a `Phd2ProfileStore` (W1.7) at construction.
(1) profile-sync **writes** guide-camera + mount + the **GUIDE-optics pixel scale** from the active `RigSpec` to the
**injected recording fake store** — assert the written payload (a recording fake `Phd2ProfileStore.write(profile_record)`,
NEVER the real `HKCU\Software\StarkLabs\PHD2` registry, which on the Windows dev/CI OS an un-faked test would mutate),
and **assert provenance**: the value written is the GUIDE-optics scale, **NOT the imaging-camera scale** (W1.7/W1.11
conflation guard); (2) auto-connect issues `set_connected true`; (3) Start Guiding when uncalibrated triggers `find_star`
+ calibrate (record methods); (4) watchdog auto-restart on a simulated process exit relaunches with **capped backoff**
and re-syncs the profile; (5) `managed=false` connects to a user-run PHD2 and issues **NONE** of the
launch/profile/`set_connected` calls. (The `set_connected`/`find_star`/`loop`/cal-status RPCs are net-new — W1.7.)
**Security hard-gate cases (the most security-critical managed-PHD2 behaviors — prose-only today, ZERO coverage):**
**(6)** a **non-allowlisted `phd2` path** OR a **user-supplied extra flag** (from `ConnSpec.extra`) is **REFUSED BEFORE
any spawn** — assert the injected launcher is **never called** (the command/path-injection sink, W1.7 step 1);
**(7)** a **pre-existing listener on `:4400`** → **REFUSE-not-clobber** — drive the **injected fake `is_port_open`**
returning `True` and assert the supervisor **does NOT launch and does NOT write the profile store** (no real socket
opened; the running-PHD2 clobber sink, W1.7 step 6); **(8)** the **orchestrator REJECTS** a RigSpec pairing a `nina`
guider/primary with a `phd2` `extra managed=true` **server-side** (the NINA-owns-PHD2 collision, W1.7 — not just the UI
toggle). All three assert the security refusal fires through the injected launcher/probe/store seams, never real
processes/sockets/registry.

**§T6 — RBAC dependency + OIDC provider (W2 / C6-C7).** (1) `requires()` per-capability × per-role → 200/403 against a
`FakeAuthProvider` returning a fixed `Principal` (no live OIDC); (2) a **startup-assertion test** registering a control
route with no declared capability and asserting `create_app()` raises — **AND** a motion-capable route declared with only
`control.capture` fails the boot assertion (W2.1 motion-boundary check), specifically including a
**`sequence/recover`-style route that reaches `SequenceEngine.start` tagged `control.capture`** (the transitive-sink
allowlist check — a recover route mis-tagged capture must FAIL the boot assertion); (3) WS gate: a viewer
WS receives events; (4) `GoogleProvider` injecting a **locally-generated RSA keypair + stub JWKS** via a
`jwks_provider`/`clock` SEAM (no `respx`; network faked) — accept-valid, reject bad-issuer/bad-audience/expired/
bad-signature, **no live Google**; (5) session JWT issue/verify/exp/tamper; (6) regression: `provider="none"` yields
admin and existing `test_auth.py` passes unchanged.

**§T6 (GoogleProvider CALLBACK-HANDLER security — the actual OAuth attack surface, currently untested).** §T6(4) above
tests only ID-token validity (signature/issuer/audience/exp); the **callback-handler** security steps W2.3 requires
(`state`, `nonce`, PKCE, `email_verified`, `hd`) are the steps most likely to be mis-wired and are the real attack
surface — test them over the in-process app + a **faked `code`→token exchange and JWKS** (via the existing
`jwks_provider`/`clock` seam — no live Google): **(1)** absent/mismatched `state` vs the pre-auth cookie → **reject**
(CSRF); **(2)** `nonce` != stored nonce → **reject** (ID-token replay); **(3)** `email_verified == false` → **deny** even
with a valid signature; **(4)** `hd` != configured `google_hd` → **deny**; **(5)** PKCE `code_verifier` mismatch →
**reject**; **(6)** happy path **binds `state` + `nonce`** and **maps `email` → role** via the allowlist.
**This six-assertion callback-security suite runs against BOTH callback handlers — the HOME-terminated handler here
(§T6) and the RELAY-terminated handler (§T7 relay-service) — same six assertions, DIFFERENT fixtures.** The two are
**distinct code**: the ID-token verification helper (issuer/audience/signature/exp/`hd`/`email_verified`) is shared, but
the callback WIRING — cookie domain (home vs relay origin), PKCE `code_verifier` custody, and `state`/`nonce`
storage/validation — is topology-specific (W2.3), so each handler gets its own pass with its own cookie/PKCE fixture. It
is NOT "the assertions are the same so test once" — it is "both handlers, the same six assertions, different fixtures."

**§T6 (field-level RBAC) — the escalation W2.2 exists to stop.** Over the in-process app + `FakeAuthProvider`:
(1) a principal holding ONLY `config.alerts` PATCHing a `POST /api/config` body that ALSO carries a `safety` block (or
`deadman_url`) → **403**, while an alerts-only body → 200 — proving presence is read from `model_fields_set`, not
value-vs-default (set `safety.min_alt_deg` to its DEFAULT value and assert it STILL 403s); (2) `PUT /api/site` carrying
`horizon_min_deg` requires `config.safety` while site-coords-only requires only `config.site_optics`; (3) a body mixing
allowed + disallowed blocks is rejected **ATOMICALLY** — assert the **persisted config is UNCHANGED** after the 403 (no
partial merge). Map EVERY `ConfigPatchBody` field to its capability so a future field added without a cap-map entry
**fails the test** — and **pin `escalation` → `config.alerts` explicitly** (W2.2): assert a `config.safety`-only
principal patching an `escalation` block is **403** (the destructive pier role cannot rewrite cooling-abort/reconnect
policy) while a `config.alerts`-only principal patching `escalation` is **200**.
(4) **AUTH/REMOTE pinning (W2.2):** a `config.alerts` holder submitting an `auth` block flipping `provider="none"` (the
disable-all-auth escalation) → **403** (auth/remote are `admin.users`-gated, kept off the general config route); and an
**UNMAPPED block** in the body → the WHOLE request **403** and merges nothing (fail-closed for any unrecognized block,
never silent-merge). (5) **`revoked_jti` registry (W2.3):** a `config.*`-but-NOT-`admin.users` principal attempting to
**shrink/clear** `revoked_jti` via a config PUT carrying `auth.revoked_jti` → **403** and the stored `revoked_jti` is
**UNCHANGED** (still contains the previously-revoked entries) — so a lower cap cannot un-revoke a killed viewer link.

**§T6 (session lifecycle) — mid-stream expiry + jti revocation + allowlist re-eval (with the injected clock seam).**
(1) a connected viewer WS whose JWT `exp` passes (advance the injected clock) is **closed/re-checked on the next periodic
re-resolve**, not left streaming; (2) adding a `jti` to `revoked_jti` makes the next request bearing that token 401/403
**though signature+exp are still valid** — AND an **OPEN tunnelled WS for that jti is torn down** (the
revocation-before-exp WS case, W2.2/W3.3); (3) removing an email from `role_allowlist` denies the next request
immediately (every-request re-eval). Pure in-process tests against the dependency, no live OIDC.

**§T6 (SSRF allow/deny on alert + deadman URLs — ENFORCE the EXISTING guard at the RBAC/route layer; do NOT re-mirror
`validate_scan_host` and do NOT break the committed deadman exception).** The guard already exists as
`alerting._url_is_safe(url, allow_private=False)` (`alerting.py:69-103`) — the new work is **enforcing it at the
RBAC/route layer** (`POST /api/alerts/{id}/test` and the `POST /api/config` alerts/`deadman_url` merge path), **reusing
`_url_is_safe`** rather than re-mirroring `validate_scan_host` (which is the per-request Alpaca-scan guard, a different
seam). Over the in-process app + a `FakeAuthProvider` holding `config.alerts`, **inject/stub the outbound HTTP client**:
- **Regular ALERT/webhook URLs** (`allow_private=False`): POST a URL at **`127.0.0.1`**, **`169.254.169.254`** (cloud
  metadata), a **`10.x`/`192.168.x`** host, and a **link-local** addr → each **rejected 4xx BEFORE any outbound request**
  (assert **no `httpx` call fired** via the injected/stubbed client); a **public allowlisted URL → accepted**. Cover
  `POST /api/alerts` and `POST /api/alerts/{id}/test`.
- **The `deadman_url` PRESERVES the committed `allow_private=True` exception (`alerting.py:459`) — do NOT assert
  deadman→private is rejected.** A self-hosted Uptime-Kuma/healthchecks on `192.168.x.x` is the user's OWN monitor and is
  the common deadman setup; asserting it rejected would break the shipped `test_deadman_allows_private_lan_host`. So the
  deadman test asserts a **private/loopback/link-local deadman host is ACCEPTED** (`allow_private=True`), while a
  **multicast / reserved / unspecified** deadman host is still **rejected** (those guards apply even with
  `allow_private=True`). The RBAC/route work only enforces `_url_is_safe` with the **same `allow_private` argument the
  committed code already uses per URL kind** — `False` for alert/webhook, `True` for `deadman_url`.

**§T6 (cookie CSRF defense — mandated by W2.3, currently untested).** W2.3 mandates a CSRF defense on the new cookie auth
path (Bearer header on every `/api` mutation **OR** `SameSite=Strict` + `Origin`/`Referer` check) — but nothing verifies
a forged cross-site mutation is rejected. With cookie auth active: a mutating POST (e.g. `/api/mount/goto`) carrying the
session cookie **but NO Bearer header** (option a) **OR** a **cross-origin `Origin` header** (option b) → **403**; the
same POST with the correct Bearer/Origin → **200**. Also assert the **session cookie is emitted `Secure` + `HttpOnly` +
`SameSite`** (cookie-attribute assertion on the login response).

**§T (four-state surface state-machine transitions — W1.6/W3.3, currently untested).** §T3 only snapshots the
`backend_links` shape; nothing exercises the four-state TRANSITIONS the W1.6 amber-vs-red signal depends on. Add a test
(server-side over a fake session whose `health()` **flips** `warming_up`/`healthy`/`last_ok_age_s` across calls, or a
pure UI store-fn test in §T8) mapping a sequence of `backend_links` snapshots to the four states and asserting the
transitions: **connected → retrying** on a drop, **retrying → hard-failed** after max-retry, **retrying → connected** on
recovery, including the **relay-link** variant (W3.3). Assert a **hard-failed transition for a CRITICAL role
(mount/camera/guider/safety/relay-link)** publishes a **TYPED bus event to the RECORDING FAKE BUS** (the escalation-sink
seam, W1.6 — asserted as the bus event, NOT a real `httpx` send) while an **idle focuser hard-fail updates the LED only**
(no bus event published) — the W1.6 escalation rule, asserted via the same recording-fake-bus pattern the connect-time
SAFE-ING and PAUSED-PENDING-ACK cases use.

**§T7 — scope client / viewer-link auth, OFF-WIRE (W3 / C8-C9).** Home-side `relay_client.py` (no WSS on wire): feed a
constructed `REQ_OPEN` (+ `REQ_DATA`) through the scope-builder and assert the correct ASGI scope (method/path/query/
headers/body) **with `scope['state']['astrodeck_remote']=True`**, the framed-back response (`RESP_HEAD`/`RESP_DATA`,
status/headers/body), and that a **viewer-tagged request to a control route 403s AT HOME** (defense-in-depth re-check)
even if the relay said viewer; `WS_OPEN` tests with a fake bus assert subscribe + `WS_DATA` emission + unsubscribe on
`WS_CLOSE`; a reconnect/backoff test (failing transport, jittered cap). Add a **`provider=="none"` + REMOTE-flagged scope
→ hard-DENY** test (the per-request WAN interlock, W3.3.2 / W3.7.2) and a **`relay_client` refuses to forward any frame
while `provider=="none"`** test. Relay service: unit-test JWT minting/signing, role stamping from a validated session,
and viewer-link issuance producing a **viewer-only** JWT that cannot carry `admin`/config caps. **WSS transport stays
integration-level; framing/replay/auth logic is unit-tested off-wire.**

**§T7 (MOTION-RACE — the owner invariant, W3.7.10).** Assert the mount-motion lock/epoch fences a stale REMOTE slew
against a LOCAL abort: start a (sim) slew via `_spawn("goto", ...)`, fire a `POST /api/mount/stop` (or `engine.abort()` /
`on_unsafe`) that **bumps `hub._motion_epoch` mid-flight**, and assert the device `slew()` is **abandoned at its
pre-dispatch epoch check** — **no device motion occurs after the abort**. Also assert: a SECOND concurrent slew is
rejected (the existing `_spawn` 409); a non-zero `move_axis` respects the same lock/epoch; a STOP that bumps the epoch
**always wins the tie** against a queued slew re-reading it under the lock.

**§T7 (REMOTE-FLAG is scope-borne, NOT header-spoofable).** Build a replay scope WITHOUT setting
`scope['state']['astrodeck_remote']` and inject an `X-Forwarded`-style / `authorization` / `x-auth-token` header that an
on-LAN attacker might forge; assert it is **STRIPPED** and does **NOT** flip `remote` (the flag comes only from
`scope['state']`). Then set the state flag and assert `resolve_principal` receives `remote=True` and hard-denies `none`.

**§T7 (header TRANSFORM / Set-Cookie rewrite — pure-function).** W3.2 requires a bidirectional header transform; a broken
`Set-Cookie` rewrite is a **silent remote-only auth break LAN tests cannot catch**. Feed a `RESP_HEAD` carrying
`Set-Cookie` (`Domain=home.local`), `Content-Length`, and a hop-by-hop header (e.g. `Connection`) through the transform:
assert **`Set-Cookie` `Domain` rewritten to the relay origin** (and `Path`/`Secure`/`SameSite=Lax` applied),
**`Content-Length` recomputed/stripped for the chunked path**, **`Transfer-Encoding` handled**, **hop-by-hop dropped**,
and **inbound `authorization`/`x-auth-token`/cookie STRIPPED** before the scope is built. Pure-function, fully off-wire.

**§T7 (scope-client isolation-from-`_lifespan`).** W3.3.0 states the scope client "can NEVER block or crash `_lifespan`
or local serving — LAN access is 100% unaffected by a relay outage" (mirroring the §T4(1) backend boot-isolation case).
Start the app with `RemoteConfig.enabled=true` and a **relay dial that immediately raises/refuses** (patch the WSS dial
to raise); assert **`_lifespan` completes**, **`/api/status` returns 200**, **LAN routes work**, and the **scope task is
retrying in the background** (not crashed / un-awaited). Also assert that with `RemoteConfig.enabled=false` (default) the
scope client **never dials** (byte-for-byte today's behavior).

**§T7 (STREAMING send/receive shim + BACKPRESSURE — the most RAM-critical novel piece).** Drive the home-side ASGI
`send`/`receive` shim with a **fake ASGI app** that emits `http.response.start` then several `http.response.body` events
(`more_body=True` … then `more_body=False`):
- **Framing:** the shim emits **exactly ONE `RESP_HEAD`** then **N `RESP_DATA`s**, with **`eof=true` ONLY on the last**
  — the `FileResponse` (FITS, `app.py:1460`) / `StaticFiles` (`app.py:2162`/`2168`) streaming case.
- **Backpressure (no-RAM-blowup):** with a relay-send awaitable that BLOCKS (or zero tunnel `WINDOW` credit), assert the
  shim does **NOT request the next `http.response.body` event until the prior chunk's send completes** — a slow WAN
  viewer **throttles the home's file iterator** instead of buffering the whole FITS in RAM.
- **Receive mirror:** tunnelled `REQ_DATA` frames surface as `http.request` events with the correct `more_body` (True
  for all but the `eof` chunk, False on `eof`), so upload bodies stream IN symmetrically.

**§T7 (transport edge, OFF-WIRE).** (1) **Chunk reassembly:** feed N `RESP_DATA`s ending `eof=true` and assert the
reassembled body is **byte-for-byte** original; a **missing-eof** and an **orphan/unknown-`stream_id` chunk** case
**ERROR rather than truncate** (W3.2 orphan-reject). (2) **Per-viewer backpressure:** a fake bus + two viewer buffers
where one never drains → the **slow one is dropped/disconnected and the fast one still receives ALL events** (the "never
block the shared WSS" invariant; assert exactly ONE real bus subscription behind both — W3.3.3). (3) **Generation
fencing:** a second `HELLO` with `generation+1` **evicts the prior WSS** (reconnect-safe). (4) **Ping-miss teardown:** a
`PONG`-starved socket tears down after the miss count. (5) **`stream_id` dedup:** a duplicate live `stream_id` `REQ_OPEN`
for a `mount/goto` is **rejected/surfaced as error**. Keep WSS-on-wire at integration level.

**§T7 (RELAY SERVICE — `relay/` dir — its own harness, named files).** With a **fake scope WSS** (an in-memory tunnel-
frame channel; no WSS on wire) and named files under `relay/` (e.g. `relay/test_proxy.py`, `relay/test_headers.py`,
`relay/test_oidc.py`, `relay/test_fanout.py`, `relay/test_registration.py`): **(1) browser↔frame round-trip** — a
browser HTTP request becomes a `REQ_OPEN` and a `RESP_HEAD`/`RESP_DATA` becomes the browser response (method/path/
**repeated headers** survive; `ws_id`↔browser binding correct). **(2) bidirectional header transform** —
`Content-Length`/`Transfer-Encoding` recompute, hop-by-hop drop, and **`Set-Cookie` rewrite to the relay origin**.
**(3) OIDC termination** produces a **home-verifiable principal token** (NOT a shared HS256 secret) — and runs the §T6
callback-handler `state`/`nonce`/PKCE/`email_verified`/`hd` cases against the relay's `/auth/google/callback`.
**(4) fan-out isolation** — a `WS_DATA` for `ws_id` A goes **ONLY** to browser A. **(5) device registration + fencing**
— a `HELLO{device_token}` pins `home_id`; a duplicate token with a higher `generation` **evicts** the older socket, a
lower one is rejected; a bad token is refused. **(6) single-instance-affinity routing** — a `home_id`'s browser request
routes to the instance holding that home's WSS. **PLUS exactly ONE concrete ON-WIRE WSS integration test** (local relay +
local home, a **named fixture**, explicit pass/fail on a tunnelled request round-trip).

**§T8 — UI tests (W1.C / W2.C / C3).** `ui/` has no test runner — EITHER (a) wire **vitest + @testing-library/react +
jsdom** as an explicit prerequisite deliverable and write component tests, OR (b) **extract the pure logic** (RigSpec
construction/merge: primary + per-role overrides → `POST /api/connect/rig` body; parsing `/api/backends` +
`backend_links` into view state) into store/lib functions and unit-test them with the existing `tsx` harness (like the
shipped `slewController.test.ts`). Cases: RigSpec with no overrides (pure primary); single guider override; an
**invalid override (role not in a backend's `roles`) rejected**; map `backend_links` entries to the health readout; the
managed-PHD2 toggle producing `ConnSpec.extra.managed`. **Replace `npm run build` as the C3 gate with build + these unit
tests.**

**§T8 (client role model / viewer read-only — security-adjacent, currently no coverage).** Extract pure logic and test
with the `npx tsx` harness: (1) caps `{view}` → the control-visibility selector **hides/disables** slew/capture/park and
shows the **View-only badge**; admin caps **enable** them; **until caps resolve, controls render FAIL-CLOSED** (treated
as viewer, not admin — W2.5); for a viewer link, `control.mount`/`control.power` are **HIDDEN** (not disabled-visible).
(2) `slewController.isNina()` / hold-disable reads the **TELESCOPE role's** backend on a mixed rig (add a mixed-rig
case, W1.4). (3) a **classifier maps `403` → "not permitted"**, **session-expiry (WS `1008` reason) → "expired"**,
**transport drop → "reconnecting"** — assert the three do NOT collide (W2.5). (4) parsing `/api/me` (or the `hello`
role/caps) into view state. (5) **per-VIEW per-role gating** (W2.5): on a partial-connect rig (telescope `RoleResult` not
ok, camera ok), the MountView gating selector returns "not connected" (surfacing the telescope `RoleResult.error`) while
the CaptureView selector returns "connected" — proving `equipConnected` is per-role, not one sticky rig-wide flag.
- **The SECURITY-adjacent rendering guarantees need a REAL render runner — a pure-selector test alone does NOT discharge
  the no-flash / hidden-node guarantee.** The two guarantees that are security-load-bearing — (i) `control.mount`/
  `control.power` **HIDDEN-not-disabled** for viewer links (a disabled-but-present node still leaks the control's
  existence and can be re-enabled in devtools) and (ii) **fail-closed-until-caps-resolve with NO flash** of an actionable
  control before caps load — are about the **rendered DOM over time**, which a pure-logic selector test cannot observe.
  So for AT LEAST these cases require **option (a): `vitest` + `@testing-library/react` + `jsdom` as a prerequisite
  deliverable**, OR mandate the components be structured so the hide/disable decision is a **pure selector consumed by a
  thin asserted render** (assert the node is ABSENT, not merely `disabled`, and that the first paint before caps resolve
  shows no actionable control) — **pin which per case**. A pure-selector unit test is sufficient for the non-security
  cases (RigSpec construction, `backend_links` parsing, the classifier mapping) but NOT for the hidden-node / no-flash
  guarantees. The repo has no component-render runner today (only `tsx` `lib`/`store` inline-assert tests), so this is a
  real prerequisite, not an assumed capability.
- **The three-surface "rig keeps running" copy fix is asserted across ALL THREE call sites reading ONE remote-context
  flag.** §T8 must explicitly cover the W2.5 copy fix: assert that `ConnectionBanner.tsx:25`, `HealthLeds.tsx:74`
  (`linkTitle`), AND `store.ts:659` all read the **same remote-context flag** (the role/remote context that drives the
  3-state classifier) so a remote viewer whose relay dropped never sees a false "the rig keeps running" claim from ANY of
  the three. The test asserts all three call sites resolve their copy from the one shared flag, not three independent
  hardcoded strings.

---

## Review revision r9 — W3 re-pinned to the owner's no-gRPC WS-tunnel design (2026-06-18)

The owner ruled out **gRPC** and **Tailscale**. W3 is fully rewritten to **plain HTTP/WS tunnelled over ONE outbound WSS**
the **scope** dials to a small **public relay** the owner hosts (Docker + Fly.io). Grounded against the committed tree
(`api/app.py` ASGI app + `/ws` send-only handler `:2107` + SPA serving `:2159` + `_auth_mw` `:514`; `auth/deps.py`
`resolve_principal(remote=)` `:133`; `config.py` `AppConfig`/`redacted()`; `hub.py` motion paths + `_spawn("goto")`
lane + deadman `:168`; `events.py` single bus). What changed:

- **Transport replaced.** `proto/relay.proto` / Connect / grpc-web are GONE. W3.2 is now a **binary tunnel framing**
  (`remote/protocol.py`): length-prefixed `{type, stream_id, header(JSON), payload}` frames multiplexing many browser
  HTTP exchanges + nested `/ws` streams + bulk media + keepalive + revocation over ONE WSS. Added a per-`stream_id`
  **credit (`WINDOW`) flow-control** scheme (replaces HTTP/2 per-stream windows) and a **class round-robin** scheduler so
  a 125 MB FITS never HOL-blocks the status poll. Deleted the entire stale protobuf block + the three-RPC-per-class model.
- **Relay forwards the WHOLE app (W3.3.1).** The 2026-06-17 ADR's frontend-on-CDN/relay split is SUPERSEDED for the
  shipped design: the home already serves SPA+API+`/ws`, so the relay reverse-proxies EVERYTHING down the tunnel — one
  origin, zero UI component changes, no CORS.
- **REMOTE-FLAG mechanism PINNED (W3.3.2, the security crux).** The scope client invokes the in-process ASGI app
  directly and sets `scope['state']['astrodeck_remote']=True` — read by `resolve_principal(..., remote=True)` so the open
  `none` provider hard-denies remotely. **No network header an on-LAN attacker could forge.** Pinned the small home-side
  wiring: a `_scope_is_remote(request)` helper feeding `remote=` into the three `resolve_principal` call sites
  (`require._dep`, `get_principal`, the `/ws` gate). Inbound `authorization`/`x-auth-token`/cookie are STRIPPED.
- **Device registration + auth (W3.3.4)** and the **relay service shape (W3.3.5)** specced: `device_token` (SECRET) →
  stable `home_id` endpoint; one scope per token; `generation` fencing for reconnect-safety; relay holds only a PUBLIC
  key (can't mint/forge); OIDC termination + viewer-link issuance with a SEPARATE key + `jti` revocation.
- **CONFIG (W3.6):** added `RemoteConfig{enabled, relay_url, device_token[SECRET], home_id}` on `AppConfig` (additive;
  default `enabled=False` = byte-for-byte today), redacted like `auth`, written only via an `admin.users`-gated
  `POST /api/remote/config` that is itself tunnel-blocked.
- **MOTION SERIALIZATION (W3.7.10):** defined the single mount-motion lock — `hub._motion_lock` + a monotonic
  `hub._motion_epoch`, co-located with the existing single-mount state. Verified today's de-facto serialization (the
  `_spawn("goto")` name lane + the stop-cancels-goto path + the 250 ms deadman) and HARDENED it: every STOP/abort bumps
  the epoch FIRST, every motion-committing call re-checks the epoch under the lock immediately before dispatching the
  device command and abandons if it advanced — so **a stale REMOTE slew accepted just before a LOCAL abort is fenced
  out** (the owner invariant). Added the §T motion-race test.
- **Deploy:** Cloud Run DISQUALIFIED → **Fly.io/VPS** (Docker + `fly.toml`). OPEN DECISIONS 1-2 closed.
- **Tests + milestones re-pinned:** §T7 rewritten to WS-tunnel terminology (`REQ_OPEN`/`RESP_DATA`/`WS_DATA`/`HELLO`/
  `REVOKE`/`WINDOW`), added the remote-flag-not-header-spoofable test + the motion-race test; C8/C9 milestone rows updated;
  off-wire unit gate keeps `pytest`, the single on-wire test is now **WSS** not gRPC. Older revision notes (r1-r8) are
  left intact as history; where they describe the prior gRPC framing they are superseded by this entry and the W3 body.

## Review revision r1

Applied the agreed review findings, grounded against the committed tree (`devices/backend.py`, `devices/base.py`,
`devices/alpaca.py`, `hub.py`, `guide/phd2.py`, `solve/base.py`, `solve/__init__.py`, `api/app.py`, `events.py`,
`config.py`). What changed and why:

**W1 — backend harness:**
- **W1.1 contract drift fixed.** Stated `devices/backend.py` ALREADY EXISTS and is canonical; W1 EXTENDS it (orchestrator
  + 4 wrapping backends are the new work). Removed the bogus `Guider`/`Solver` imports (no `Solver` class exists — it is
  `solve/base.py:PlateSolver`), set the duck-typed `object | None` solver/guider, `ConnSpec` `None` defaults,
  `register()`-returns-backend, and `RigSpec.resolve/to_dict/from_dict` to match committed source.
- **W1.2 NativeSession respec.** Documented that `make_device` (`alpaca.py:669`) builds a NEW `AlpacaConnection` per
  call (so the old "reuse one connection" claim was false and leaked clients); respecced a per-`(host,port)` connection
  pool, `set dev.role`, `await connect()`, and `close()` closing ALL owned connections. Added per-wrapper `close()`
  bridge semantics and the NINA `safety`-role gap call-out.
- **W1.3 orchestrator.** Grouping key is `(backend, host, port)` not bare backend; pass the full resolved slice to
  `open`; added teardown-on-partial-failure, a rewritten `disconnect_all` fanning `close()` over sessions, and
  close-before-open on re-apply. Made `guider` special (no spurious device `RoleResult`) and routed solver precedence
  through the single owner `solve.get_solver` instead of re-deriving ASTAP-else-sim.
- **W1.4** added per-role REAL/SIM (+ NINA/native/PHD2) badges as operator-truth, per-role `isNina()`/hold logic, and a
  guarded mixed real-motion+sim-imaging case.
- **W1.5** expanded the retirement inventory to ALL `self.mode`/`sim_rig`/`nina_client` reads (summary, poll_status mode,
  solve_and_sync, NINA WS/heartbeat lifecycle, reconnect replay) and re-keyed the SimSolver refusal to the CAMERA role's
  session; added the source-grep guard test.
- **W1.6** mandated failure-swallowing boot auto-connect, a PARKED/tracking-off mount posture, and a persistent
  page-load-surviving per-role boot-result surface (tri-state LEDs + header indicator).
- **W1.7 managed PHD2** downgraded: PHD2 JSON-RPC cannot set the guide-camera/mount driver/pixel-scale, so profile sync
  writes the platform-specific on-disk profile store directly; listed net-new RPCs (`set_connected`/`find_star`/`loop`/
  cal-status); hardened launch against command/path injection (`shell=False`, fixed argv, path allowlist, admin-only).
- **NEW W1.9** (roles + the duplicated `ROLES` tuples unified to one source, `rotator` added now, dome/flat deferred to
  W4, named the `devices/backends/__init__.py` registration import site), **NEW W1.10** (server-side sun-exclusion /
  daytime gating as a W1 deliverable, independent of `is_default`, `force=true` does NOT bypass it, distinct loud UI
  state), **NEW W1.11** (per-role capability advertisement for cooling/dew/pier-side + FOV-provenance re-derivation),
  **NEW W1.C** (concrete per-role picker UX), **NEW W1.12** (meridian-flip calibration gated on confirmed pier change,
  cross-backend, NativeGuider `flip_calibration` defined).

**W2 — RBAC:**
- **W2.1** rebuilt the capability taxonomy against the REAL route surface: removed `config.mount_limits` (guarded an
  empty route set; the real pier knobs are `SafetyConfig` under `config.safety`), split out `control.mount` (motion) and
  `control.power` (`/api/switch/set`), added `config.solar_override` (gates `goto force=true`), and made the
  route→capability table assert over `app.routes` at boot.
- **W2.2** corrected the WS model to ACCEPT-TIME subscribe-gating only (the `/ws` handler is send-only, never
  `receive()`s), dropped the non-existent "command frame" gating, added mid-stream JWT-expiry teardown, and required
  field-level capability enforcement for the multi-domain `POST /api/config` / `PUT /api/site`.
- **W2.3** added OIDC state/nonce/`email_verified`/`hd`, every-request allowlist re-eval, Secure+HttpOnly+SameSite
  cookie, `jti` revocation, and replaced the shared-HS256 `session_secret` with asymmetric/home-issued signing (relay
  never holds home signing authority) plus a hard fail-open interlock (refuse remote while provider=`none`).
- **NEW W2.5** client role model (read-only viewer surfaces, 403≠disconnect, remote-aware banner); pulled the DESTRUCTIVE
  double-confirm forward into staging B reusing the existing `confirmDialog` hold mode.

**W3 — remote relay:**
- **W3.2** added chunked request/response bodies (`HttpRequestChunk`/`HttpResponseChunk`) to stop triple-buffering and
  HOL-blocking on multi-MB FITS/PNG, multiple concurrent streams, explicit max-message-size, a large-media signed-URL
  bypass, `query`, `repeated Header` (multi-value/Set-Cookie), a `stream_generation` fencing token, and keepalive.
- **W3.3** specified the principal-injection contract (strip inbound auth headers, inject only a home-verifiable signed
  principal, mark scope remote/untrusted, home-side 403 re-check, device_token rotation/mTLS), per-viewer backpressure
  isolation (drop-or-disconnect, never block the shared stream), full-jitter/capped reconnect, relay-outage isolation
  from `_lifespan`, idempotency/corr_id dedup, viewer-link scoping (separate key, jti, explicit caps, TTL/renew/
  max-viewers/audit), and the affinity-vs-shared-bus scalability invariant (Cloud Run flagged hostile).
- **W3.5 staging** and **OPEN DECISION 1** updated accordingly.

**W4** added NativeGuider `flip_calibration`, the per-role native-`SafetyMonitor` safety override for unattended use,
and the deferred dome/flat roles.

**TEST PLAN (NEW §T1–§T8)** replaced the thin staging-table test names with named, fakes-based files: per-wrapper
backend tests (identity asserts), fault-injecting orchestrator (the C1 gate), pre/post characterization + source-grep
guard, boot-isolation + mixed-rig integration, injected-launcher managed-PHD2, RBAC dependency + faked-JWKS OIDC,
off-wire relay-client/viewer-link auth, and a UI testing prerequisite (vitest or extracted pure store/lib logic via the
existing `tsx` harness) replacing `npm run build`-only gates. The chunk table's Verify column now points at these.

**OVERVIEW** refreshed: corrected line numbers, the "four branches" → full inventory framing, the
`backend.py`-already-exists note, the `make_device`/`_check_horizon`/`PlateSolver`/`/ws`-send-only/event-bus-drop
grounding facts, and scoped "zero hub edits" to new backends filling existing roles.

---

## Review revision r2

Second review pass, grounded by spot-checking the committed tree at the time of writing: `devices/orchestrator.py`
(ships `assemble()`/`AssembledRig`), `tests/test_orchestrator.py` (pins `to_dict` shape + graceful degrade),
`devices/backend.py` (`open(conn)` Protocol), `devices/backends/{sim,nina,native,phd2}_backend.py` (native
`roles = ROLES`, native `close()` clears a dict, `native_solver()` returns None, NINA roles omit safety),
`api/app.py` (sequence/polar/tracking/safety/cooler/dew/focuser/filterwheel routes; `safety_simulate` reads `hub.mode`;
`sequence/start` `if not force` bypasses `_horizon_block`; `/ws` subscribes-then-hellos, send-only),
`hub.py` (`meridian_flip` calls `flip_calibration` unconditionally; `goto_and_center`/`_check_horizon` with
`is_default` early-return), `catalog/coords.py` (`sun_radec`/`sun_altaz`/`dark_window` already exist),
`devices/alpaca.py` (`validate_scan_host`). What changed and why:

**W1 — backend harness:**
- **W1.3.0 (NEW) — "supersedes committed orchestrator."** Reframed W1.3 from greenfield to a **rename+reshape of the
  shipped `assemble()`/`AssembledRig`**: `assemble→connect_profile`, `failures: dict` → `results: list[RoleResult]`
  (`ok/error/attempted`, one per REQUESTED role), string `_session_key` → `(backend,host,port)` tuple,
  `solver_source→solver`, `to_dict` keys preserved as a back-compat superset. Stated `test_orchestrator.py` is rewritten
  in stage A as the C1 gate and the W1.5 snapshots are taken against today's `assemble()` path.
- **W1.2/W1.3 — `open_endpoint` contract break removed.** The pseudocode called a non-existent
  `open_endpoint(role_conns)`; switched to the committed `open(conn)` passing the endpoint's representative `ConnSpec`,
  with sessions reading per-role `host/port/dev_num` at `get_device` time (option b).
- **W1.2 — `native_backend.py` reframed as a REWRITE.** Documented the shipped per-role `make_device` (no pool) and the
  `close()`-only-clears-a-dict bug; required a real `(host,port)→AlpacaConnection` pool and a `close()` that `aclose`s
  every owned connection, with the §T1 test that is impossible against current code.
- **W1.2/W1.3/W1.5 — SimSolver per-role SEAM made implementable.** `SimSession.native_solver()` returns `None` today;
  specced `_pick_solver` receiving the camera SESSION (name→literal mode, or `is_simulated`/`camera_provenance`), a
  `SimSolver` bound to the sim camera session, and a real-camera session returning `None` to force ASTAP/refusal.
- **W1.4/W1.C — recurring mixed-rig friction.** Added a per-action first-motion ack on SlewPad/goto (distinct from the
  always-blocking sun cone), a SATURATED motion-class header naming the real role, and a Solve&Sync button visibly tied
  to the SimSolver guard. Added discovery→override-row binding (per-row scanning/empty/error sub-states, differentiated
  errors, no-role and duplicate-device handling) and hard-disabled the managed-PHD2 toggle on NINA rigs.
- **W1.6 — connect-time SAFE-ING action + four-state surface.** Distinguished "never initiate motion" from "halt unsafe
  motion discovered": on connect, a real mount found unparked+tracking inside the sun cone is STOPPED (optionally parked)
  BEFORE the banner, gated by `config.solar_override`; pushed boot/daytime alerts to a header chip AND the alerts sink.
  Tied the tri-state LED to a `RoleResult` for EVERY requested role and added a fourth "retrying" state from
  `backend_links`.
- **W1.7 — managed-PHD2 vs NINA collision.** Made managed PHD2 incompatible-by-construction with a NINA primary/guider
  (UI toggle hard-disabled, orchestrator refuses the RigSpec), valid only on native/sim primaries, with pre-launch
  port-`4400` detect-and-refuse rather than clobber.
- **W1.9 — roles fixed.** `NativeBackend.roles` corrected to the explicit 6-role set (no `guider`/unfillable
  `KeyError`); documented the `hub.ROLES`(6) vs `backend.ROLES`(7) drift; made `rotator` either deferred to W4 or
  gated on a real `Rotator` ABC + not-requested-LED, dropping the W1 auto-PA benefit; added a roles-match-served test.
- **W1.10 — sun guard at the MOTION BOUNDARY.** Moved the guard into `goto_and_center`/`move_axis` so sequence-force,
  polar, and meridian-flip re-slews inherit it; stated `sequence/start force=true` is subject to the sun cone (needs
  `config.solar_override`); corrected grounding to REUSE the existing `sun_altaz`/`sun_radec`/`dark_window`
  (`catalog/coords.py`) and add only the separation-cone check.
- **W1.12/W1.13 (NEW) — INVERTED pier-side gate.** Flip the guider calibration UNLESS pier side is KNOWN-and-unchanged
  (or the mount advertises no-flip); treat UNKNOWN/non-reporting as "flip," with each guider's self-guard as the second
  line — so a strict "confirmed-change" gate can't strand non-reporting GEMs into backwards guiding.

**W2 — RBAC:**
- **W2.1 — `control.mount` re-derived from the motion boundary.** Added sequence/{start,pause,resume,abort}, polar/*,
  and mount/tracking to `control.mount`; made the boot assertion validate that motion-capable handlers carry
  `control.mount` (not merely "a capability"). Enumerated cooler/dew-heater/focuser-halt/filterwheel under
  `control.capture`; added `safety/simulate` under `config.safety` (re-keyed off camera/primary session, app.py:697
  added to W1.5 inventory); flagged `config.alerts`/`deadman_url` as an SSRF + credential-exfil sink (validate_scan_host
  allow/deny) excluded from viewer/operator; documented transitive couplings and the operator boundary (sequence is now
  `control.mount`-gated).
- **W2.2 — field-level enforcement uses `model_fields_set`.** Presence is read from set fields / raw JSON keys (not
  value-vs-default); explicit block→cap map (site, `horizon_min_deg`→safety, safety, escalation, alerts, deadman_url);
  mixed bodies rejected ATOMICALLY (no partial merge); same gating on `PUT /api/site`. WS teardown now also consults the
  `jti` deny registry (revocation-before-exp), with a bounded max re-check interval.
- **W2.3 — cookie CSRF defense + per-request `none`-WAN interlock.** Added explicit CSRF defense for the cookie path
  (Bearer header on mutations OR SameSite=Strict + Origin/Referer check); made the `none`-provider WAN refusal a
  property of the request path (remote scope + `provider==none` → hard deny, relay refuses to forward), not only a boot
  check.
- **W2.5 — fail-closed client + 3-state narrative.** Controls render disabled/hidden until caps resolve (unknown→viewer);
  viewer-link motion/power HIDDEN; hold-confirm on Park/Unpark/Stop/goto even for operators; distinct copy for
  permission-denied vs session-expired (read the 1008 reason) vs transport-down; fixed the ConnectionBanner remote copy.

**W3 — remote relay:**
- **W3.2 — ordering invariant + protocol gaps.** Pinned a corr_id to ONE Tunnel stream for its lifetime (head frame
  establishes the binding; `stream_class` field added) so chunked responses reassemble; added proto_version + feature
  bits to `Hello`, per-WS `WsEvent.seq`; specced version handshake, in-flight caps/timeouts/body-size/corr_id-collision
  DoS limits, bidirectional header transform (Content-Length/Transfer-Encoding/hop-by-hop/Set-Cookie rewrite), home as
  jti source of truth, clock-skew leeway, restart-surviving generation.
- **W3.3 — ONE bus subscription + concrete resync + residual MITM + view split.** Pinned the relay to a SINGLE
  `bus.subscribe()` with its own per-`ws_id` bounded buffers (drop status / keep latest preview-sequence); defined the
  resync (per-event seq gap-detect, buffer-before-snapshot, reset-not-duplicate); named the relay-is-a-plaintext-MITM
  residual trust as an accepted risk (mTLS, admin/config unreachable over the tunnel, control.mount/power direct-only);
  split monolithic `view` into `view.status`/`view.preview`/`view.media` and kept raw-FITS + precise site coords out of
  the default viewer-link set.

**W4** — noted NativeGuider forces additive `Telescope` ABC methods (guide-rate read, pulse-guide capability flag,
declination hook) + a defined calibration model, so the seam is more than a pure Backend registration.

**TEST PLAN** — added the missing safety/security coverage: **`test_sun_guard.py`** (§T1.10, fixed clock, 8 cases incl.
force-goto and forced-sequence not bypassing the cone, solar-override gating); **§T1.12** cross-backend meridian-flip
pier-gate (inverted-gate behavior); field-level RBAC tests for `/api/config` + `PUT /api/site` (model_fields_set,
atomic-rejection-persists-nothing); session-lifecycle tests (mid-stream expiry teardown, jti revocation tears down open
WS, allowlist re-eval); client role-model / viewer read-only UI tests (fail-closed render, mixed-rig isNina, 3-state
classifier); relay transport edge tests (chunk reassembly byte-for-byte + missing-eof error, per-viewer backpressure
with one bus subscription, generation fencing, ping-miss teardown, corr_id dedup); a mixed-rig per-role SimSolver
derivation test (§T2/§T4); a legacy-profile migration test (old `ProfileDevice` schema → RigSpec, `alpaca`→`native`
alias); and W1.6 connect-API route tests via `TestClient` (`/api/backends`, `/api/connect/rig` shape + server-side
invalid-override rejection, `/api/discover/{backend}` 404). Rewrote §T2 to target `connect_profile` with `to_dict`-shape
back-compat carried forward, and updated the W1.8 staging + chunk tables to reflect the orchestrator rename and the new
gates.

---

## Review revision r3

Third review pass, grounded by spot-checking the committed tree at the time of writing: `hub.py`
(`summary()` embeds full `site` + `redacted(config_store.cfg())` at `:404`/`:409`; `ROLES` 6-tuple at `:52`),
`devices/backends/nina_backend.py` (`roles` omits `switch`+`safety` at `:128`; `close()` only `client.close()`;
`health()` lacks `warming_up`/`healthy`), `devices/backends/native_backend.py` (`close()` clears a dict),
`devices/alpaca.py` (`_client_id`/`_txn` MODULE-GLOBALS at `:38-45`; `validate_scan_host` at `:126`),
`guide/phd2.py` (`pixel_scale` default 2.0 at `:64`, scales `RADistanceRaw`/`dither`), `config.py`
(`EscalationConfig.cooling_action`/`reconnect_*` at `:104`+), `api/app.py` (`sequence/recover` `:1440`, `mount/solve_sync`
`:1129`, `mount/stop` `:1159`, `/ws` hello `:1524`, `FileResponse` FITS `:1057`, `StaticFiles` `:1535-1543`),
`devices/base.py` (`FilterWheel.filter_offsets` `:242`, `Telescope.time_to_meridian_flip` `:202`/`reports_destination_pier_side`
`:152`, `Camera.sensor_width`/`pixel_size_um`), `catalog/framing.py` (`rotation_deg` `:50`), and the UI
(`store.ts:659`/`881`, `ConnectionBanner.tsx:25`, `HealthLeds.tsx:74`, `SlewPad.tsx`). What changed and why:

**W1 — backend harness:**
- **W1.3 — `_requested_roles(spec)` DEFINED** as `set(spec.roles) | set(get_backend(spec.primary).roles)` (it was
  load-bearing for the whole tri-state LED contract but undefined and underivable from `RigSpec`); added the function to
  the orchestrator pseudocode + a grounding bullet + §T4(4)/§T2(9) coverage. Stated an explicit override for a
  backend-unfillable role is still "requested" (surfaces a failed `RoleResult`).
- **W1.2/W1.9 — `NinaBackend.roles` add `switch`.** Reconciled the contradiction between the W1.2 table (`switch`), the
  committed tuple (omits `switch`+`safety`), and the committed `connect_nina` blind `{r: conn for r in ROLES}`; added
  `switch` (leaving only `safety` unfillable), made thin builders/migration request `backend.roles ∪ overrides`, and
  scoped the override-reject rule to EXPLICIT overrides only. Updated §T4(4).
- **W1.2 — deleted the false ClientID/ClientTransactionID sequencing claim** (they are module-globals, unaffected by
  per-device `make_device`); kept the genuine leaked-httpx-client + `close()`-clears-a-dict justifications for the pool.
- **W1.5 — NinaSession WS/heartbeat/`_bridge_ready` migration made concrete.** `close()` must cancel WS+heartbeat tasks;
  `health()` gains `warming_up`/`healthy` as an ADDITIVE field across all four sessions; the hub's `_bridge_ready` warming
  computation is replaced by reading the NINA session's `health().warming_up`.
- **W1.6 — in-progress-sequence resumption posture.** Added `PAUSED-PENDING-ACK` (never auto-resumed) on auto-connect,
  surfaced on the header chip + escalation sink, resume requires `control.mount`; "unattended boot defaults to no motion
  until acked" stated as intended safe behavior + a §T4 case.
- **W1.6/W3.3 — mid-session hard-failed ESCALATES.** A hard-failed transition for mount/camera/guider/safety/relay-link
  pushes to the alerts/escalation sink (not just the LED); an idle focuser/filterwheel/switch drop updates the LED only;
  added the §T transition test.
- **W1.7/W1.11 — guide vs imaging pixel-scale conflation fixed.** PHD2 pixel scale/calibration/dither derive from the
  GUIDE camera + guide-scope (a separate guide-optics record), NOT the imaging train W1.11 re-derives (wrong by 3-5×);
  recommended driving RMS from PHD2's own calibration. Marked the W1.11 FOV re-derivation as imaging-train-only/disjoint.
- **W1.7 — W1-window LAN-anonymous hard-gates.** Managed-PHD2 spawn + generic `/api/discover/{backend}` + `/api/connect/rig`
  are anonymous-LAN-reachable for the whole W1→W2 gap; pinned (a) `validate_scan_host` on the generic discover route,
  (b) managed-PHD2 argv/path/port allowlist landing in C5 independent of RBAC, (c) the C6 `none`-default RBAC scaffold
  before/alongside C5; acknowledged in W1.8.
- **W1.9 — `backends/__init__.py` is a per-backend edit site.** Stated plainly: adding a backend = "new module +
  `register()` + one import line"; named the deterministic-registration vs auto-discovery trade.
- **W1.11/W1.2 — cross-backend filter-offset → focuser ownership** documented like dew-heater ownership, with an
  `applies_focuser_offset` flag so a natively-coupling backend (NINA) is not double-corrected and the hub owns the offset
  move on a mixed rig.
- **W1.12/W1.13 — GEM-gate the inverted pier rule FIRST.** Gate the flip-calibration path on a positive does-GEM-flip
  signal (`time_to_meridian_flip()` not None / explicit flag) so the inverted "flip-on-UNKNOWN" rule cannot back-fire on
  a fork/alt-az mount; added the non-flipping-fork §T1.12 case.
- **W1.9/W1.12 — rotator-deferral domain consequences.** Camera PA is NOT preserved across a GEM flip (stacker
  derotates) and `framing.rotation_deg` is advisory-only until the W4 rotator; sized the W4 rotator to include framing/PA.
- **W1.2 — sim guide-camera provenance** stated independent of imaging-camera provenance: a sim guider must not
  `pulse_guide`/`dither` a real mount without the W1.4 first-motion ack (bind to a sim mount, or extend the ack to guiding).
- **W1.4 — Solve&Sync disabled-with-explanation extended** from the MountView button to the sequence Run/Preflight path
  (a pinned PreflightModal blocker row disables Run on a real-mount+sim-camera centering sequence).
- **W1.C — first-run/migration onboarding** added: pre-populate the picker FROM the migrated RigSpec (not blank), one-line
  per-primary explainers, flat-list → preset mapping.

**W2 — RBAC:**
- **W2.1 — `control.mount` made exhaustive.** Added `sequence/recover` (`:1440`), `mount/solve_sync` (`:1129`),
  `mount/stop` (`:1159`) explicitly; made the boot assertion an explicit motion-sink allowlist (incl. `SequenceEngine.start`
  + polar start) AND transitive; added `sequence/recover` to the §T6(2) boot-assertion test.
- **W2.2 — `escalation` remapped to `config.alerts`** (not `config.safety`) — it is notification/recovery policy
  (`cooling_action`/`reconnect_*`), not a pier floor; pinned in the §T6 field-map test.
- **W2.3 — split the two OAuth topologies.** Relay-terminated vs home-terminated differ in cookie domain / PKCE custody /
  state-nonce validation; dropped "provider code is identical" (scoped to the token-verification helper); stated the home
  MUST NOT accept a raw Google ID token from the relay in topology (1).
- **W2.2/W2.3 — relay-tunnelled viewer-link revocation.** The relay holds the `ws_id`→`jti` map and polls/receives the
  home's `revoked_jti`, dropping the per-`ws_id` projection within ≤30s (mirroring the direct bound); renewal contingent
  on a live home stream + a bounded-age revocation snapshot.
- **W2.5 — per-VIEW gating becomes per-role** (`equipConnected` per required role, reading the same `RoleResult` the LEDs
  use): MountView gates on telescope while CaptureView stays usable on a partial-connect rig.
- **W2.5/HealthLeds — fixed the "rig keeps running" copy in ALL THREE surfaces** (`ConnectionBanner.tsx:25`,
  `HealthLeds.tsx:74`, `store.ts:659`), not just the banner.

**W3 — remote relay:**
- **W3.3/W2.2 — server-side redaction seam.** Added a `principal`/`scope`/`caps` parameter to `hub.summary()` + the
  `config` bus emission so a viewer lacking `view.site_precise`/`config.site_optics` gets coarsened coords + a
  site/alert/safety-stripped config BEFORE the frame is queued; per-`ws_id` coarsening in the relay's projection;
  `WsEvent.json` documented as scope-specific, not globally identical; added the §T viewer-hello/config-event test.
- **W3.2 — signed-URL bypass hardened** into a one-shot scoped capability (bound to jti + `view.media`, very short TTL,
  re-validated at redemption against `revoked_jti`), or dropped in favor of the chunked path.
- **W3.3 — resync seq model de-contradicted.** `seq` is assigned at the per-`ws_id` fan-out point, monotonic per viewer,
  only for delivered frames; coalescing advances seq by exactly 1; removed the "per-event seq on Event" phrasing and the
  contradictory proto comment.
- **W3.3 — ASGI replay of STREAMING responses.** Specced the `send`/`receive` shim for `FileResponse` (FITS) +
  `StaticFiles`: `http.response.body` → `HttpResponseChunk{eof = not more_body}` with real backpressure (await the relay
  send before pulling the next ASGI body event); request bodies stream via `HttpRequestChunk` → `http.request`.
- **W3.2/W3.3 — DOWN-direction stream-class/flow-control** made symmetric (inbound bulk/upload vs control on separate
  classes; in-flight corr_id cap + per-chunk idle timeout govern DOWN too; or bound upload size small if rare).

**TEST PLAN:**
- **§T1.10 — connect-time SAFE-ING halt test** added (the single most novel safety action, previously untested):
  fake unparked+tracking+in-sun-cone mount → halt-before-banner (call-order recorded), independent of `is_default`,
  idle mount untouched, resume needs `config.solar_override`, alert to chip + escalation sink.
- **§T6 — GoogleProvider callback-handler security** (state/nonce/PKCE/email_verified/hd) added over the in-process app +
  faked code→token/JWKS; §T6 SSRF allow/deny on alert+deadman URLs; §T6 cookie CSRF defense + cookie-attribute assertion;
  §T6 escalation→config.alerts field-map pin; §T four-state transition state-machine.
- **§T7 — relay SERVICE harness** (named files, fake home stream): browser↔frame round-trip, bidirectional header
  transform + Set-Cookie rewrite, OIDC-termination → home-verifiable principal, fan-out isolation, single-instance
  affinity, PLUS one on-wire gRPC integration test. Added the relay header-transform off-wire test and the
  relay_client isolation-from-`_lifespan` test.
- **§T1.12 — non-flipping fork-mount case** (assert `flip_calibration` NOT called).
- **§T4(5)/W1.8 — migration test ownership consolidated** to `test_profiles.py` (incl. the `alpaca`→`native`
  alias-not-`KeyError` assertion), cross-referenced from §T4(5).
- **§T8 — per-view per-role gating** UI case added.

---

## Review revision r4

Fourth review pass, grounded by spot-checking the committed tree at the time of writing: `hub.py`
(`connect_sim` reads `session.shared_state`/`session.guide_camera` at `:177-178`, `connect_nina` reads `session.client`
at `:226`, `meridian_flip` `stop_guiding→goto_and_center→flip_calibration→start_guiding` at `:1043-1079`),
`sequence/engine.py` (`_apply_filter` does `foc.move_to(pos+delta)` UNCONDITIONALLY at `:1358-1364`),
`alerting.py` (`AlertDispatcher._on_bus_event` at `:233`; `_url_is_safe(allow_private)` at `:69-103`; deadman
`allow_private=True` at `:459`), `devices/backends/__init__.py` (ALREADY EXISTS, imports all four),
`devices/base.py` (`Camera.set_cooler` single set-point at `:134`), `devices/sim.py` (non-`ROLES` `guide_camera` at
`:493`), `api/app.py` (`_AUTH_OPEN_PREFIXES`/`_AUTH_OPEN_EXACT` at `:283-284`), and the UI
(`SlewPad.tsx:118-144`/`:162-177`/`:201-214`, `MountView.tsx:74-90`, `NotConnectedInterstitial.tsx:39-46`). What changed
and why:

**W1 — backend harness:**
- **W1.2 — real-guider+sim-mount bench case + PHD2-bypasses-ack.** Added the inverse bench case (real guider against a
  sim mount → calibration times out, surface as a clear refusal) and stated PHD2-initiated `pulse_guide` BYPASSES the
  W1.4 UI ack entirely, so the guide-path mixed-rig guard is the orchestrator-time "bind sim guider to sim mount" refusal,
  not the UI ack, for any PHD2-guided rig.
- **W1.3 — `guide_camera` Protocol seam.** Decided the non-`ROLES` `guide_camera` (`sim.py:493`) flows through a new
  `BackendSession.guide_camera()` accessor surfaced into `ConnectResult`, not the `SimSession`-only property; added §T3
  (summary unchanged) + §T1 (reachable via the accessor) assertions.
- **W1.3 — hostless coalescing.** `_group` normalizes host/port to `None` for hostless backends (sim, phd2-local) so all
  sim roles coalesce into ONE `SimRig` regardless of stray addressing; added §T2(10).
- **W1.3.0 — `open()`-failure→empty-sessions contract.** A failed `open()` contributes NO session; the thin builders
  detect total-primary-failure from `results` and re-raise to preserve the committed `hub.py:222-224` NINA fail-fast;
  added §T2(12).
- **W1.5 — three NON-Protocol session accessors retired.** Added `session.shared_state`/`guide_camera`/`client` to the
  retirement table with their generic fates; strengthened the guard test to assert NO hub call site reads a non-Protocol
  `BackendSession` attribute. Added a backend-specific POST-assembly wiring inventory + the "zero hub edits" FakeBackend
  proof (§T2(11)).
- **W1.4 — first-motion ack re-specced as a SEPARATE "arm" affordance** at the pad boundary (not a modal from inside
  `onPointerDown`, which would steal pointer capture mid-press); covers the NINA nudge path and does not collide with the
  blur/visibility `panicStop`. Added the MountView `force=true` "slew anyway" → HOLD-mode escalation on a real mount in a
  single combined confirm (`MountView.tsx:74-90`). Added a header/banner precedence ordering (one loud region;
  shape/letter differentiation for night-mode).
- **W1.6 — concrete PAUSED-PENDING-ACK resume UX** (distinct SequenceView held state, hold-confirm naming "re-slew + may
  meridian-flip", resume re-runs the sun-cone check); actionable boot-failure header chip (deep-link + retry-failed-roles)
  with toast-vs-persistent channel ownership pinned (boot = persistent+sink, no toast; manual = toast+LED). Pinned the
  escalation-sink test seam as a TYPED bus event on a recording fake bus (the `test_engine_*.py` pattern, grounded
  `alerting.py:233`), asserted as a two-signal dual surface (chip + bus event), httpx delivery covered separately.
- **W1.7 — managed-PHD2 auto-calibrate vs meridian flip.** Auto-calibrate must NOT fire inside the post-flip
  `start_guiding`; prefer `flip_calibration` over a fresh calibrate (only fall back + re-center if no calibration);
  §T1.12 case added. Specced a `Phd2ProfileStore` writer Protocol (platform impls), injected as a recording fake in
  §T5(1) with GUIDE-optics provenance asserted (never the real `HKCU\Software\StarkLabs\PHD2` registry on the Windows
  CI OS). Added §T5(6)(7)(8) security hard-gate cases (non-allowlisted path/flag refused before spawn; pre-existing
  `:4400` listener refused-not-clobbered via an injected `is_port_open` fake; orchestrator rejects nina-guider+managed)
  with launcher/port-probe/store all injected.
- **W1.9 — `backends/__init__.py` already exists; the gap is eager vs lazy registration.** Specced a single eager
  registration import so `GET /api/backends`/`list_backends()` is never served an empty `BACKENDS` before the first
  connect (`_harness()` is lazy at `hub.py:61`); added the §T4 fresh-app assertion. Added the CoverCalibrator/flat-panel
  deferral domain consequence (automated flats are a manual step until W4) and sized the W4 CoverCalibrator
  (brightness + cover + auto-flat target type).
- **W1.10 — periodic sun-proximity check on the status poller** so a TRACKING mount drifting into the cone over time is
  halted, not only at goto/move/connect; added §T1.10(9).
- **W1.11 — filter-offset double-apply named in committed `engine.py`.** `engine.py:_apply_filter` (`:1358-1364`) does
  the offset `foc.move_to` UNCONDITIONALLY — a NINA-primary rig double-corrects focus TODAY; gate it on NOT
  `applies_focuser_offset`; the §T skip/apply test is added as a named case in r7 (§T1.11) and wired into the C4 gate.
  Added a cooling-ramp / warmup-before-disconnect contract as a
  known gap (`Camera.set_cooler` at `base.py:134` is a single set-point; the W1.3 teardown abruptly cuts cooler power).
- **W1.C — danger hold-confirm extended to ANY real motion device** (mount OR focuser), not only the mixed bench combo,
  with a per-rig consequence summary; sim-only stays frictionless.

**W2 — RBAC:**
- **W2.2 — auth/remote config pinned to `admin.users`, OUT of the general config field map**, with the disable-all-auth
  escalation vector named; the field map is FAIL-CLOSED for any unmapped block (reject whole body 403, never silent
  merge). Added §T6(4).
- **W2.3 — `revoked_jti` mutated ONLY via an `admin.users`-gated append-only revoke API**, never via the config merge; a
  config PUT carrying `auth.revoked_jti` is rejected; added §T6(5). Constrained `default_role` to ≤ `viewer` OR
  non-empty `google_hd` so a non-null default cannot grant elevated WAN access to the whole Google population; added a
  §T6/§T7 deny case.
- **W2.1/§T6 — boot-time transitive-reachability mechanism pinned** as an EXPLICIT route→motion-sink declaration (not
  runtime call-graph inference, infeasible at `create_app()`); the two layers (declaration + all-mutating-need-a-cap)
  compose. §T6 SSRF test reconciled to ENFORCE the existing `_url_is_safe` (reuse, not re-mirror `validate_scan_host`)
  and PRESERVE the committed deadman `allow_private=True` exception (do not break `test_deadman_allows_private_lan_host`);
  multicast/reserved/unspecified still apply to deadman. §T6 GoogleProvider callback suite runs against BOTH handlers
  (home + relay), same six assertions, different fixtures.
- **W2.5 — viewer STOP/panic-stop DECIDED:** viewers get no motion controls including STOP (operator is the safety
  authority, on the landing copy); the client-side `panicStop` 403 (`SlewPad.tsx:162-177`) is swallowed silently, never
  a toast/classifier banner. Made `NotConnectedInterstitial` role-aware (no "Go to Rig" CTA for viewers,
  `NotConnectedInterstitial.tsx:39-46`). Specced `/api/me` as `view`-required, returning the actual resolved principal,
  FAIL-CLOSED (401, never default-admin), excluded from `_AUTH_OPEN_*` (`app.py:283-284`), with the boot route assertion
  extended to identity-disclosing GETs. Required a real render runner (vitest+@testing-library) for the hidden-node /
  no-flash security guarantees (pure-selector alone insufficient) and §T8 coverage of the three-surface copy fix.

**W3 — remote relay:**
- **W3.2 — single Tunnel RPC vs multi-stream contradiction resolved:** one Tunnel RPC PER stream-class (event/control/
  bulk-media) for true per-class HTTP/2 flow control; `stream_class` demoted to a self-describing tag.
  `stream_generation` made a HOME-SESSION fencing token shared across all class-streams, with GROUP eviction (G+1 on any
  class-stream evicts all ≤G for that home; full set re-opens atomically) and a §T7 partial-redial case. Pinned `corr_id`
  allocation (relay-allocated, opaque to home, separate namespace from `ws_id`) and collision rules (reject duplicate
  live head; orphan-chunk→error). Added the WS-server→client-only forward-note (`app.py:1525-1527`).
- **W3.3 — relay→browser leg backpressure** added as a SECOND distinct per-browser egress buffer in the relay service
  (drop-oldest-status / keep-latest-preview, per-browser disconnect on overflow) so one slow browser cannot back-pressure
  the relay's read of the home stream nor balloon relay memory (multi-tenant DoS); per-viewer `seq` survives both buffers
  (relay drop is a detectable gap); added a §T7 relay-service stalled-browser case. Added the relay-INSTANCE failover
  re-routing story (shared pub/sub OR stable routing key + re-resolving reconnect; bounded window; resync on browser
  reconnect) tied to §T7(5). Added the independent WS/event PUSH-path revocation enforcement (relay re-checks `jti`
  against a home-pushed revoke set, tears down the open browser WS within ≤30 s) since per-request `requires()` cannot
  cover a long-lived `/ws`. Extended the tunnel-block list to name `POST /api/auth/config`/`POST /api/remote/config`/the
  `auth`/`remote` config blocks + the revoke API as never-reachable-over-tunnel.
- **W3.3/§T7 — relay_client streaming send/receive shim + backpressure test** added (the most RAM-critical novel piece):
  fake ASGI app emitting `http.response.start` + N `http.response.body` (more_body True…False) → exactly one
  `HttpResponse` head then N `HttpResponseChunk` with eof only on the last; a blocking relay-send proves the shim does
  NOT pull the next body event until the prior send completes (the FileResponse/StaticFiles no-RAM-blowup invariant);
  receive mirror for `HttpRequestChunk`→`http.request`.
- **W3.5 — relay green-each-stage gate** pinned: relay language decision (Go `go test ./...` vs Python+Connect
  `pytest relay/`, lean Python+Connect), off-wire `relay/test_*.py` as the per-stage unit gate, the single on-wire gRPC
  integration test classified as a SEPARATE on-demand integration gate (named fixture), not part of the unit gate.

**W4** — expanded the NativeGuider seam: per-axis guide-rate READ + optional per-axis SET (ASCOM
GuideRate{RA,Dec} are separate, independently settable; no guide-rate accessor exists anywhere today), and stated
NativeGuider must implement its OWN calibration routine (a real re-implementation of what PHD2 provides for free) — the
concrete reason it is a larger lift than a Backend registration; OPEN DECISION 3 updated to name the calibration cost.

---

## Review revision r5

Fifth review pass — STRICT convergence, surfacing only material must-fix defects (correctness bugs, safety holes,
spec↔committed-code drift). Grounded by spot-checking the committed tree: `devices/orchestrator.py` (`to_dict` does
`"sessions": sorted(result.sessions)` at `:129`), `devices/backends/phd2_backend.py` (`Phd2Session.get_device` `raise
KeyError` for non-guider roles at `:56-59`), `tests/test_phd2_backend.py` (`test_get_device_rejects_non_guider_role`
asserts `pytest.raises(KeyError)` at `:116-119`), `devices/nina.py` (`time_to_meridian_flip` overridden at `:429`),
`devices/base.py` (`time_to_meridian_flip` returns `None` at `:202`), `devices/sim.py` (`SimTelescope` is "a German
equatorial", does NOT override `time_to_meridian_flip`, at `:241`), and `devices/hub.py` (`_is_gem(side)` = pier_side
east/west at `:1217-1220`; `_compute_meridian` gates `flip_enabled` on `_is_gem(side)` at `:1244-1246` precisely because
an Alpaca/sim GEM returns `None` from `time_to_meridian_flip()`). What changed and why:

- **W1.3.0 — `to_dict` back-compat `sessions` value made None-safe AND JSON-able over the new tuple keys (HIGH).** The
  reshape re-keys `sessions` to `(backend, host|None, port|None)` but was silent on how the back-compat `sessions`
  summary value is derived — so an implementer would carry the committed `"sessions": sorted(result.sessions)`
  (`orchestrator.py:129`) forward. `sorted()` over the new tuple keys raises `TypeError: '<' not supported between
  instances of 'int' and 'NoneType'` the moment two same-host keys differ in port-presence (`('native','host',11111)`
  vs `('native','host',None)`) — exactly the partial-port multi-endpoint rigs the tuple key exists to support — and raw
  tuples are not JSON-able. Pinned the derivation in the W1.3.0 to_dict row (`sorted(str(k) for k in result.sessions)`,
  or a None-coercing sort key emitting stringified tuples), and rewrote the carried-forward §T2 to_dict-shape assertion
  to exercise a rig with TWO same-host endpoints of differing port-presence so the C1 gate catches the crash and the
  non-JSON-able regression.
- **W1.13/§T1.12 — does-GEM-flip classifier MUST NOT key off `time_to_meridian_flip() is None` (HIGH, safety).** That
  property is NINA-only (overridden solely at `nina.py:429`; `base.py:202` and `sim.py` return `None`), so a real
  native-Alpaca GEM and the sim GEM — the W4/§T1.12 targets — return `None` and were mis-classified as non-GEM, NEVER
  getting their guider calibration flipped: the exact backwards/runaway-RA hazard the gate exists to prevent. This also
  contradicted the committed `hub._compute_meridian`, which detects a GEM via `_is_gem(side)` = pier_side east/west
  (`hub.py:1217-1220`, `:1244-1246`) precisely because Alpaca/sim GEMs return `None`. Re-specced the classifier to gate on
  a positive does-GEM-flip signal that AGREES with `_compute_meridian` — EITHER reuse the committed `_is_gem(side)`/
  pier_side east|west signal OR a MANDATORY (not "and/or"-optional) `is_german_equatorial`/`does_meridian_flip` flag set
  by the native Alpaca backend from ASCOM `AlignmentMode == germanPolar` (and `SimTelescope` → `True`). Fixed the §T1.12
  cases: fork/alt-az distinguished as pier_side == UNKNOWN AND not-`_is_gem`/not-flagged (flip NOT called), while an
  Alpaca/sim GEM that returns `None` from `time_to_meridian_flip()` but reports pier_side east/west STILL gets the
  inverted FLIP-UNLESS-known-and-unchanged rule (mis-classification regression guard).
- **§T1 + design body — PHD2 `get_device(non-guider)` reconciled to `raises KeyError` (HIGH, spec↔code drift).** Spec
  line 235 ("returns None for non-guider roles") and §T1 line 1979 ("`get_device(non-guider)` → None") contradicted the
  committed `Phd2Session.get_device` which RAISES `KeyError` (`phd2_backend.py:56-59`), matching the house convention
  (sim/native also raise) and the committed `test_phd2_backend.py:116-119` (`pytest.raises(KeyError)`). A §T1 test written
  verbatim to the spec would fail shipped code, and this was not framed as an intentional red test. Changed both the
  design body (lines 235-238, including the dependent guider-only prose) and the §T1 line to `raises KeyError` so spec
  body, §T1, and committed code agree.

## Review revision r6

Sixth review pass — STRICT convergence, applying only material must-fix defects grounded by spot-checking the committed
tree: `devices/hub.py` (`_is_gem(side)` = pier_side east/west at `:1216-1220`; `_compute_meridian` at `:1222-1249`;
`summary()` embeds full site lat/lon + redacted config at `:404`/`:409`), `devices/base.py` (`pier_side()` → `UNKNOWN`
at `:190-191`; `reports_destination_pier_side = False` at `:152`; `time_to_meridian_flip()` → `None` at `:202`),
`devices/orchestrator.py` (`assemble()` builds an explicit `role_session` key map at `:76`/`:92-93`/`:105-108`;
`_session_key` at `:52`), and `api/app.py` (raw-FITS `GET /api/preview/{id}/fits` `FileResponse` at `:1044`/`:1057`).
What changed and why:

- **W1.13/§T1.12 — does-GEM-flip classifier MUST be `_is_gem(side) OR flip-flag`, never `_is_gem(side)` alone (HIGH,
  safety).** The r5 decision tree offered option (a) — reuse `_is_gem(side)` (pier_side east/west, `hub.py:1216-1220`) —
  as a STANDALONE classifier. But the common real GEM is a non-reporting / UNKNOWN-pier mount (`base.py:190-191`
  `pier_side()` → `UNKNOWN`; `base.py:152` `reports_destination_pier_side=False`), so `_is_gem("unknown")` is `False` and
  step (1) classifies it non-GEM and NEVER flips its calibration — the exact backwards/runaway-RA hazard the section
  exists to prevent, contradicting its own claim. Removed option (a) as a standalone choice: the classifier is now
  `_is_gem(side) OR positive does_meridian_flip/is_german_equatorial flag`, and that flag is a MANDATORY (not optional)
  addition to the `Telescope` ABC — none exists today (`reports_destination_pier_side` at `base.py:152` is about
  destination-pier-side support, NOT flip class). Native Alpaca sets it from ASCOM `AlignmentMode == germanPolar`;
  `SimTelescope` sets it `True`. A mount with pier_side UNKNOWN AND no flag is the only true non-GEM (fork/alt-az). Split
  the self-contradictory §T1.12 GEM case ("reporting pier_side east/west so `_is_gem` is true ... when the side is
  UNKNOWN") into TWO distinct mounts: (i) a non-reporting GEM (`does_meridian_flip=True`, pier UNKNOWN, `ttf=None`)
  asserting `flip_calibration` IS called, and (ii) a fork (`does_meridian_flip=False`, pier UNKNOWN) asserting it is NOT
  called — the two differ ONLY in the flag, proving the flag is the discriminator.
- **W1.3.0 — undefined `_pick_guider` session-lookup seam PINNED (HIGH, correctness).** `connect_profile` calls
  `guider = _pick_guider(resolved, sessions)` (line 390) but `_pick_guider` was DEFINED NOWHERE (single grep occurrence =
  the call site), unlike its fully-specified sibling `_pick_solver`. `sessions` is keyed by the `_group`-normalized
  `(backend, host|None, port|None)` tuple (hostless sim/phd2-local → host/port `None`). An implementer keying off raw
  `(conn.backend, conn.host, conn.port)` would MISS the session for a stray-addressed sim/PHD2-local guider override and
  emit a spurious `"no guider"` `RoleResult` (line 393). Added a defining bullet pinning `_pick_guider`'s lookup the same
  way `_pick_solver`'s is — locate the guider session via the SAME normalized grouping key `_group` produces (hostless
  host/port → None before keying) then return `native_guider()`; OR restore the explicit `role → session-key` map the
  committed `assemble()` built (`orchestrator.py:76`/`:92-93`/`:105-108`) and have both seams read it. Updated the
  pseudocode call-site comment and added §T2 case (13): a stray-non-None-host guider override on a hostless backend is
  STILL found and yields a non-None guider with an `ok=True` `RoleResult` (mirrors §T2(10) for the guider seam).

---

## Review revision r8

Strict convergence pass, grounded by spot-checking the committed tree: `sequence/engine.py` (`_apply_filter` at `:1342`,
called from `:812`, unconditional `await foc.move_to(pos + delta)` at `:1364`) and `tests/test_sequence.py`
(`engine._apply_filter(...)` already invoked at `:125`). What changed and why:

- **W3.2 `proto/relay.proto` — `HomeFrame` oneof had no revocation-signal variant; the required home→relay revocation
  PUSH was unexpressible on the wire (HIGH, correctness / security — spec lines 1626-1633).** The spec mandates a
  home-pushed revoke "over a `HomeFrame`" (lines 1373, 1744-1745) and asserts a §T6(2)/§T7 teardown test against it
  (lines 1746-1749), but the `HomeFrame` oneof carried only `{response, resp_chunk, ws_event, hello, pong}` — no variant
  could encode it and it cannot be smuggled as a `corr_id`-bearing chunk, so a revoked viewer's tunnelled `/ws` keeps
  receiving live status/preview past the ≤ 30 s bound. Added an explicit control variant `RevokeSignal revoke = 6;` to
  the `HomeFrame` oneof and defined `message RevokeSignal { repeated string jti = 1; repeated string ws_id = 2; }`, so the
  home can PUSH revocation that closes matching OPEN per-`ws_id` projections within the ≤ 30 s bound independent of any
  in-flight `corr_id`. Stated this control frame carries **no `corr_id`** and is therefore **exempt from the orphan-chunk
  reject rule** (lines 1724-1725 — it is neither a head nor a chunk; the relay routes it by `jti`/`ws_id`). Updated the
  W3.3 prose (lines 1744-1745) to name the concrete `HomeFrame.revoke` / `RevokeSignal` variant. The documented
  poll-of-`revoked_jti` fallback is kept; the required push is now expressible on the wire so the §T6(2)/§T7 assertion is
  implementable.
- **TEST PLAN §T1.11 + C4 chunk Verify column + W1.11 prose — wrong method name `_change_filter` corrected to the
  committed `_apply_filter` (HIGH, spec↔code DRIFT — spec lines 1011, 1015, 2023, 2139, 2703, 2752, 2931).** The committed
  tree has `SequenceEngine._apply_filter` (`engine.py:1342`, called from `:812`, unconditional `move_to(pos + delta)` at
  `:1364`) and **no** `_change_filter`; `tests/test_sequence.py:125` already calls `engine._apply_filter(...)`. As
  written, the named §T1.11 entry point (`drive engine._change_filter`) would `AttributeError` against committed code and
  the C4 gate instruction pointed at a nonexistent method. Replaced `_change_filter` with `_apply_filter` at all seven
  occurrences. The cited line range (`engine.py:1358-1364`) is correct and was left unchanged — only the method name was
  wrong. No new test content added.
- **W2.1 ↔ W3.3 — split view capabilities reconciled into the single-source-of-truth table (HIGH, data-minimization /
  security).** The split-view caps W3.3 relies on (`view.status`/`view.preview`/`view.media`/`view.site_precise`) lived
  ONLY in W3.2/W3.3 prose + changelog and were NEVER reconciled into the W2.1 table, which still mapped ALL of
  `/api/preview/*` (incl. raw-FITS `GET /api/preview/{id}/fits`, `app.py:1044`/`:1057`) to monolithic `view` with
  `ROLES_CAP['viewer']={'view'}`. The boot route→capability assertion therefore validated only monolithic `view`, so a
  viewer-link principal granted `{view}` passed `requires('view')` on the raw-FITS route and the `hub.summary()` WS hello
  carried precise site lat/lon (`hub.py:404`) — re-opening exactly the leak the W3.3 split exists to close. (1) Replaced
  the monolithic `view` table row with `view.status`/`view.preview`/`view.media` + `view.site_precise`, gating raw-FITS
  (and any full-res science route) on `view.media`, status/WS on `view.status`, downsized preview on `view.preview`.
  (2) Defined the explicit default `VIEWER_LINK_CAPS = {view.status, view.preview}` (EXCLUDES `view.media` and
  `view.site_precise`) and stated `ROLES_CAP['viewer']` EQUALS it, with `view.media`/`view.site_precise` reachable only
  via an explicit per-link opt-in caps list. (3) Extended the boot assertion to validate the re-keyed table — it rejects
  the retired monolithic `view` outright, so a preview/FITS route still tagged broad `view`/`view.preview` FAILS boot
  (exercised by a §T6 assertion). Updated the W3.3 "`view` is too monolithic" bullet to reference the now-canonical split
  rather than framing it as a future action.

---

## Review revision r7

Strict convergence pass: only must-fix defects (spec↔committed-code drift, a TEST-PLAN gap with a dangling false
changelog claim, and an either/or mechanism permitting hardcoded backend names). No enrichment or scope additions.

**W1 — pluggable backends / orchestrator:**
- **W1.2 — `NinaSession.native_solver()` spec contradicted committed code and resurrected a removed broken path (HIGH,
  correctness / live-rig-hang).** Spec line 198 said `native_solver()` "returns the NINA solver adapter," but committed
  `nina_backend.py:72-75` returns `None` and there is **no `NinaSolver` class** (only `AstapSolver`/`SimSolver`). Wiring
  that line literally would make `_pick_solver` (lines 424-425) prefer a nonexistent/hanging NINA solver over ASTAP,
  re-introducing the orphaned-ASTAP / live-rig-hang hazard the committed `hub.solve_and_sync` fix (`hub.py:959-966`)
  deliberately removed. Reconciled line 198 to committed reality: `native_solver()` returns `None`; NINA captures a frame
  and solves via the hub's local solver (`solve.get_solver` → ASTAP, refusing `SimSolver` fallback), exactly as
  `solve_and_sync` does today; `_pick_solver`'s ASTAP-vs-guarded-`SimSolver` precedence is the single owner of the NINA
  camera solve path. Softened the `BackendSession` contract comment (line 120) from "in-process PlateSolver (NINA/native)"
  to "(native only) … no stage-A backend provides one."
- **W1.11 — filter-offset double-apply fix had no §T case and no owning chunk (HIGH, TEST-PLAN gap + false changelog
  claim).** The committed bug is real: `sequence/engine.py:1358-1364` does `await foc.move_to(pos + delta)`
  UNCONDITIONALLY whenever `apply_filter_offsets` is set and a focuser is present — it consults no
  `FilterWheel.applies_focuser_offset` flag — so a NINA-primary rig double-corrects focus on every filter change today.
  The r4 changelog (old line 2729) claimed "added the §T skip/apply test," but no such case existed in §T1-§T8. Lifted the
  already-written §T assertions into a NAMED **§T1.11** test (in-process fake `FilterWheel`/`Focuser`): assert
  `engine._apply_filter` SKIPS `move_to(pos + delta)` when the active filterwheel advertises
  `applies_focuser_offset=True` and APPLIES it when the flag is absent/`False`. Wired the W1.11 fix + §T1.11 into the
  **C4/W1.D** chunk Verify column so a gate actually runs it, and corrected the dangling r4 claim to point at the now-real
  §T1.11.
- **W1.3 — hostless-coalescing left as an either/or that permitted hardcoded backend names (MEDIUM, architecture).**
  Lines 305-306 offered `Backend.hostless` OR "`RigSpec.resolve`/`connect_profile` drops host/port for sim/phd2-local
  before keying"; the second branch can only be built by hardcoding the `{sim, phd2}` name set inside
  `_group`/`connect_profile`, re-introducing the `if backend == "sim"` coupling the pluggable design exists to delete.
  Pinned `Backend.hostless` (default `False`, `True` on `SimBackend`/`Phd2Backend`) as the REQUIRED mechanism, added the
  flag to the W1.1 `Backend` Protocol, deleted the alternative, and stated `_group`/`_pick_guider`/`_pick_solver`
  normalize host/port→`None` via `get_backend(name).hostless`, never a literal name set. Strengthened **§T2(11)** with a
  HOSTLESS `FakeBackend` variant (stray non-None host on ≥2 roles must still coalesce to ONE session), which catches a
  name-list `_group` that only special-cases sim/phd2.
