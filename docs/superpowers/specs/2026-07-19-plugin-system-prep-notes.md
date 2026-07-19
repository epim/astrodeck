# Plugin System — Design-Prep Notes (NOT a spec)

**Date:** 2026-07-19
**Type:** Read-only design-preparation research. This is a **PREP note to ground a brainstorming
conversation** — it is explicitly **not a spec, not a plan, and not a design decision**. It inventories
what is already pluggable, sketches candidate architectures with trade-off leans, and frames the
decisions the user must make in the brainstorm.
**Scope:** Piece 2 of the platform-expansion campaign — a first-class plugin system so third parties (and
we) can extend AstroDeck without forking: device drivers, sequence actions/triggers/conditions,
image-processing steps, providers (weather/catalog), and UI panels. Also the substrate for the NINA
Path-A / Path-B conclusions in `2026-07-19-nina-plugin-compat-spike.md`.

> **Read this first (the one load-bearing finding):** AstroDeck has a **mature, plugin-shaped registry for
> device backends** and a **proven subprocess-isolation precedent** (the comhost). But its **sequencer is a
> hardcoded monolithic pipeline with no step/trigger/condition registry at all** — which is exactly the
> surface a NINA-familiar SDK (Path B) needs. The plugin system's hardest and highest-value work is
> **refactoring the sequencer into a registry**, not inventing a packaging mechanism.

---

## (a) Extension-point inventory — what's already pluggable vs. what needs a new contract

Ranked by how close each already is to "a plugin point."

### 1. Device-backend registry — ALREADY a plugin system (strongest seam) ✅

`server/astrodeck/devices/backend.py`:
- `BACKENDS: dict[str, Backend]` registry + `register()` / `get_backend()` / `list_backends()`
  (`backend.py:173`, `:176`, `:183`, `:193`).
- `Backend` and `BackendSession` are `@runtime_checkable` **Protocols** (`backend.py:96`, `:142`) — a
  vendor adapter's whole contract (`name`, `label`, `roles`, `discoverable`, `hostless`, `open()`,
  `discover()` + the session's `get_device` / `native_guider` / `guide_camera` / `native_solver` /
  `health` / `close`). This is a clean, documented, duck-typed interface with a serializable intent object
  (`ConnSpec`, `backend.py:44`) and a whole-rig plan (`RigSpec`, `backend.py:209`).
- Concrete backends **self-register at import via side-effect** (`devices/backends/__init__.py:11-15`
  imports `sim`/`nina`/`native`/`phd2`/`ascom_local`); the registry is populated by importing the package
  (`hub.py:73`, `api/app.py:1170`).
- `list_backends()` already emits a **JSON-able capability manifest** the UI/API render from
  (`backend.py:193`).

**Verdict:** this is effectively a v0 plugin system for one category. A device-driver plugin is "a module
that calls `register(MyBackend())`." The only thing separating it from a real plugin system is
**discovery** (today it's a hardcoded import list, not entry-point/manifest discovery) and **trust
boundary** (today every backend is in-process trusted).

### 2. The comhost sidecar — ALREADY the isolation substrate ✅

`server/astrodeck/comhost/manager.py` + `devices/backends/ascom_local.py`:
- `ComHostManager` (`comhost/manager.py:117`) is a **server-owned, supervised subprocess**: spawns
  `python -m astrodeck.comhost` on an **ephemeral loopback port** (`:111`), learns the port from a
  portfile, health-checks the management API (`:195`), tears down on shutdown, **lazy-restarts a crashed
  child with spawn-backoff** (`:126-147`), and enforces **no-orphan PID discipline with a PID-reuse
  identity guard** (`_reap_stale`, `:149`; `_pid_is_comhost`, `:74`).
- `AscomLocalBackend` (`ascom_local.py:38`) is the payoff: a **registered backend whose session runs
  against a supervised sidecar** — it reuses `NativeSession` unchanged and just forces the loopback
  endpoint (`ascom_local.py:26-35`). The spike (§2.1) already noted this is the template for a plugin
  sidecar RPC.

**Verdict:** "plugins as supervised loopback sidecars" is not hypothetical here — it is **shipped and
battle-tested**. Any untrusted-plugin trust model can reuse this exact pattern.

### 3. Provider / capability resolution — a fixed POLICY, not a registry ⚠️

`server/astrodeck/providers.py`:
- `_RESOLVERS = {autofocus, polar_align, solve, guide}` (`providers.py:415`) with `resolve()` /
  `resolve_all()` (`:423`, `:435`). But each resolver is a **hand-written if/elif over a fixed family
  vocabulary** (`backend`/`astrodeck`/`astap`/`sim`), and the capability set is a closed `Literal`
  (`Capability`, `:59`). `resolve_all` never raises (`:435`) — good "a bug can't 500" discipline to keep.

**Verdict:** this answers "who runs capability X" for a **hardcoded set of four capabilities**. It is a
resolution *policy*, not a plug point. A plugin that adds a new capability (e.g. a new task type) would
require generalizing this from a closed Literal + if/elif into a **capability-provider registry**. Medium
effort; clean model to extend.

### 4. Driver registry — config-data-driven instances, hardcoded TYPES ⚠️

`server/astrodeck/drivers.py`:
- `_PROBES = {"nina", "alpaca", "phd2"}` (`drivers.py:141`), `_DRIVER_TYPE_TO_BACKEND` (`:286`),
  `_implicit_rows()` for detected built-ins (sim/astrodeck/astap/ascom-local, `:146`). `describe_all()`
  (`:254`) merges configured + implicit into the `{roles, drivers}` option space the Equipment UI renders.
- Driver **instances** are data (`AppConfig.drivers`), but driver **types** are a hardcoded dict. Adding a
  driver type = editing this file + the backend map.

**Verdict:** parallels the backend registry (they're two halves of the same thing — a "driver type" maps to
a backend + a probe). A plugin adding a device-driver category would want to register a probe here too, or
this collapses into the backend-plugin contract.

### 5. Sequencer — HARDCODED MONOLITHIC PIPELINE, no registry ❌ (the big gap)

`server/astrodeck/sequence/engine.py` + `sequence/models.py`:
- The plan is a **fixed pydantic schema**: `SequencePlan` / `Target` / `ExposureStep` / `Schedule`
  (`models.py:59`, `:42`, `:10`, `:23`). There is no notion of an arbitrary "sequence item" — a plan is
  a list of targets, each a list of exposure steps, with a fixed field set.
- The engine is a **hardwired pipeline**: `_setup_target` does slew → center → autofocus → start-guiding
  in fixed order (`engine.py:892`); `_run_step` inlines dither / refocus / meridian-flip / guiding-recovery
  as `_maybe_*` method calls (`engine.py:1043`, e.g. `_maybe_meridian_flip` at `:1075`,
  `_refocus_due` at `:1093`). "Triggers" and "conditions" exist only as **hardcoded engine behavior**, not
  as registrable objects. The schedule/window logic (`sequence/schedule.py`) is likewise fixed.

**Verdict:** **This is the single most important finding.** NINA's entire plugin model is
`ISequenceItem` / `ISequenceTrigger` / `ISequenceCondition` (spike §1.2). AstroDeck implements the
*semantics* of those (the dossier `nina-sequencer-triggers.md` is real) but as **baked-in control flow,
not an extensible registry**. There is **no seam a third party could add a sequence step/trigger/condition
to today.** Path B (NINA-familiar SDK) is blocked on refactoring this monolith into a
step/trigger/condition registry with an `execute(context, token)`-style contract. Large, high-value,
and the true center of gravity of a plugin system.

### 6. Image-processing pipeline — hardcoded on both sides, no hooks ❌

- **Server:** `server/astrodeck/imaging/processing.py` (`auto_stretch`/MTF at `:33`, `to_jpeg`,
  `display_histogram`) and `imaging/stars.py` — plain functions, no pipeline registry.
- **Client:** `ui/src/components/preview/` — `useImageRemap.ts` applies a fixed 256-entry MTF LUT via
  `buildLut` (`lut.ts`); stretch is a fixed transfer function. `StarOverlay`, `StretchHistogram`, etc. are
  fixed components.

**Verdict:** an "image-processing step" plugin category has **no contract on either side** today. Would
need a new pipeline-stage interface (server ndarray→ndarray, and/or a client canvas/LUT stage). Net-new.

### 7. UI views / panels — build-time static registry, closed union ❌ (hardest layer)

`ui/src/App.tsx` + `ui/src/types.ts`:
- Views register via **two hardcoded const arrays**: `NAV` (nav entries, `App.tsx:40`) and
  `VIEWS: Record<ViewName, () => JSX.Element>` (`App.tsx:68`), gated by `GATED` (`App.tsx:94`).
- `ViewName` is a **closed TypeScript union** (`types.ts:11`). Adding a view = edit `types.ts` +
  `store.ts` + `App.tsx`, all **statically imported at build time**. There is **no runtime/dynamic view
  loading, no module federation, no iframe host.**

**Verdict:** the UI is the hardest layer for third-party extension. Every other seam is server-side Python
where dynamic import is natural; the UI is a statically-compiled Vite bundle. A UI contribution model is a
genuine new capability (see architecture options below) with real CSP/security implications.

### 8. Smaller fixed seams (context, not primary)

- **Solver:** `solve/__init__.py:17` `get_solver()` — trivial if/elif (ASTAP else Sim), `PlateSolver` ABC
  (`solve/base.py:18`). Small closed set.
- **Weather:** `weather.py` — two hardcoded fetchers `_fetch_open_meteo` (`:58`) / `_fetch_astrospheric`
  (`:108`), no registry. A "weather provider" plugin category would generalize this.
- **Catalog:** `catalog/` — object/survey/tile modules, no provider registry.
- **Auth providers:** `auth/providers.py` — Protocol-based `AuthProvider` (`:27`) with several impls +
  `MultiAuthProvider` (`:185`), built via `build_provider()`. A second Protocol-based seam, but
  config-selected, not plugin-registered. Useful as **prior art** for "Protocol + factory" shape.

### Packaging reality (per layer)

- **Server (Python):** setuptools, `server/pyproject.toml`. **No `[project.entry-points]` defined yet.**
  Registration today is **manual side-effect import** (`devices/backends/__init__.py`). The natural
  generalization is **`importlib.metadata` entry points** — a plugin package declares
  `[project.entry-points."astrodeck.backends"]` (and `.sequence_items`, `.providers`, …) and AstroDeck
  discovers them at startup. This is a small, idiomatic step from the existing self-registration pattern.
- **Rust native engine (`astrodeck_native`):** optional wheel, **guarded import** (`NATIVE_AVAILABLE`,
  `providers.py:52`). Plugins **cannot extend Rust directly** without rebuilding the wheel; they reach it
  only *through* the Python provider/guider layer. So "a plugin that uses native compute" = a Python plugin
  calling existing native-backed capabilities, not a plugin loaded into Rust.
- **UI (React/TS/Vite):** build-time static bundle. No runtime plugin loading exists. This is the layer
  that forces an explicit architecture decision (below).

---

## (b) Candidate architectures — 2–3 approaches with trade-offs

Each is a coherent whole-system stance. These are **leans to react to in the brainstorm, not a chosen
design.**

### Approach A — "Trusted in-process, thin API" (minimum viable)

**Shape:** Plugins are Python packages discovered via `importlib.metadata` entry points, loaded
**in-process, fully trusted**. Categories map onto existing/lightly-generalized registries: device
backends (already there), a new sequence step/trigger/condition registry, provider registries
(weather/solve/catalog). **No UI-plugin mechanism in v1** — plugin config surfaces render from a
**declarative schema** (JSON-schema-ish field list the plugin returns), drawn by a generic AstroDeck form
renderer (the Equipment/Settings UI already renders from server-provided `offers`/manifests, so this fits).

- **Pros:** Smallest lift; leans entirely on proven patterns (backend registry, Protocol contracts,
  server-driven forms). Zero new isolation/CSP surface. Fast path to "we and trusted partners ship
  plugins." Matches Path A/B (our own reimplementations are trusted).
- **Cons:** No third-party sandbox — a bad plugin can crash/hang/exfiltrate (the `resolve_all`
  never-raises discipline mitigates crashes, not malice). No custom UI panels, only declarative forms —
  a plugin that needs a bespoke visualization (e.g. Hocus-Focus tilt map) can't render it.
- **Recommendation lean:** **This is the right v1 core.** It captures the device-driver, sequence, and
  provider categories — which is most of the plugin *value* — with the least risk. UI-panel and
  untrusted-sandbox are deferred as explicit later pieces.

### Approach B — "Capability-scoped + sidecar isolation for untrusted" (the ambitious full stack)

**Shape:** Approach A's registries **plus** a trust tier: untrusted/third-party plugins run as
**supervised loopback sidecars** using the **comhost pattern verbatim** (`ComHostManager` → generalized
`PluginHostManager`), talking a small JSON-RPC/gRPC contract. Plugins declare **capabilities** (which
devices/services/filesystem they may touch); the host injects only those (a NINA-mediator analogue).
UI contributions via a **declarative panel manifest** (widgets from an AstroDeck-provided component
vocabulary) and/or **server-driven UI**, never arbitrary third-party JS in the main bundle.

- **Pros:** Real trust boundary (crash isolation + capability scoping + signing). Reuses a **shipped,
  hardened** isolation mechanism — the risky part is already done. Capability injection maps cleanly to
  NINA's mediator model, strengthening Path B. Cross-language: a sidecar can be Python, .NET, anything.
- **Cons:** Much larger surface — IPC contract, lifecycle, marshaling of images/ndarrays across the
  boundary (the spike flagged image marshaling as the expensive part), per-capability permission model,
  signing/distribution. UI-panel vocabulary is a design project of its own. Over-built if the near-term
  goal is just "ship our own Path-A reimplementations."
- **Recommendation lean:** **The right *destination*, wrong *v1*.** Adopt the trust *tiering* concept and
  the sidecar mechanism as the untrusted path, but sequence it after A proves the registries. Don't pay
  the IPC/marshaling cost until a genuine untrusted third-party category exists.

### Approach C — Hybrid: trusted in-process core now, declared seams for the sidecar + UI later (RECOMMENDED LEAN)

**Shape:** Ship **Approach A's in-process trusted registries** as v1, but **design every contract so a
sidecar transport can slot behind it later** — i.e. the plugin-facing API is the same whether the
implementation is in-process or over loopback (the backend Protocols already have this property; the
comhost proves the transport swap). Concretely:
1. **Generalize discovery** to `importlib.metadata` entry points (supersedes the manual import list).
2. **Refactor the sequencer** into a step/trigger/condition registry with an
   `execute(context, token)` contract + a `SequenceContext` that injects device/service handles (the
   mediator analogue) — this is the load-bearing work and unblocks Path B.
3. **Providers** (weather/solve/catalog) get thin registries mirroring the backend one.
4. **UI:** v1 = declarative config/panel manifest rendered by AstroDeck (no third-party JS); leave a
   named seam for a future runtime-loaded/iframe panel host.
5. **Trust:** v1 in-process trusted; **the sidecar (comhost pattern) is the pre-designed upgrade path** for
   when untrusted plugins arrive.

- **Pros:** Delivers value fast (our Path-A plugins + a real SDK shape) while avoiding a v2 rewrite —
  the expensive isolation/UI pieces are *planned seams*, not retrofits. Every step is grounded in an
  existing pattern.
- **Cons:** Requires discipline to keep contracts transport-agnostic (don't leak in-process object refs
  into the plugin API). The sequencer refactor is real work regardless.
- **Recommendation lean:** **This is the recommended overall stance** — in-process-trusted core with
  entry-point discovery and a sequencer registry now; comhost-style sidecar + UI-panel host as
  pre-designed later pieces.

---

## (c) Scoping questions the brainstorm must resolve (framed as decisions)

1. **v1 plugin categories — which ship first?**
   Options: {device backend/driver} (already 90% there) · {sequence item/trigger/condition} (needs the
   sequencer refactor — highest value for NINA-parity) · {provider: weather/solve/catalog} (thin
   registries) · {image-processing step} (net-new both sides) · {UI panel} (hardest).
   **Decision:** pick the v1 set. *Prep lean: device-backend (formalize) + sequence item/trigger/condition
   (the marquee) + providers (cheap). Defer image-processing and UI panels.*

2. **Trust model for v1 — trusted-only, or tiered from day one?**
   Options: (a) in-process trusted only; (b) in-process trusted + comhost-style sidecar for untrusted;
   (c) capability-scoped injection + signing.
   **Decision:** how much isolation in v1. *Prep lean: in-process trusted for v1 (our own Path-A plugins
   are trusted); pre-design the sidecar seam; add signing/capabilities when a third-party tier is real.*

3. **UI extension mechanism — and how far in v1?**
   Options: (a) none — declarative config/panel manifest rendered by AstroDeck (no third-party JS);
   (b) runtime-loaded JS modules / module federation; (c) iframe-sandboxed panels; (d) server-driven UI.
   Each has distinct **CSP/security** implications (arbitrary third-party JS in the main bundle is the
   thing to avoid; the app already ships a strict-CSP posture).
   **Decision:** declarative-only for v1, or commit to a JS/iframe host now. *Prep lean: declarative
   manifest v1; iframe or federated panels as a later, security-reviewed piece.*

4. **Packaging & distribution — how are plugins discovered, installed, versioned?**
   Options: `importlib.metadata` entry points (idiomatic Python; generalizes today's self-registration) ·
   a drop-in plugins directory (NINA-style `%LOCALAPPDATA%\...\Plugins`) · a manifest + in-app plugin
   manager (NINA-style central registry). Plus: **version/compat contract** (NINA's hard rule: published
   type names must not change because saved sequences embed them — do we adopt the same stability
   contract for our sequence items so saved plans don't break?).
   **Decision:** discovery mechanism + install story + stability contract. *Prep lean: entry points +
   a stable-identity rule for sequence items mirroring NINA's.*

5. **NINA-SDK-familiarity depth (Path B) — how literally do we mirror NINA?**
   Options: (a) concept-parity — `SequenceItem`/`Trigger`/`Condition` + `execute(context, token)` +
   mediator-shaped injected services, so a NINA author *ports mental models*; (b) light inspiration;
   (c) a documented "NINA→AstroDeck" mapping table. The spike (§4 Path B) says concept-compatible, not
   source-compatible.
   **Decision:** how close the item/trigger/condition contracts and the injected-service surface sit to
   NINA's, and whether we publish a porting guide. *Prep lean: concept-parity on the sequencer contracts
   + a mediator-shaped `SequenceContext`; that is what makes both our Path-A reimplementations and
   third-party ports natural.*

6. **Sequencer refactor scope — how deep, and staged how?**
   The monolithic engine (`engine.py`) must become a step/trigger/condition registry to support categories
   (1)'s sequence tier and Path B. This is the biggest single work item.
   **Decision:** is v1 a *full* pluggable sequencer, or a *narrow* trigger/condition hook set bolted onto
   the existing pipeline first (e.g. let plugins add triggers/conditions before full custom items)?
   *Prep lean: stage it — expose trigger/condition hooks + a custom-item interface incrementally so the
   existing autonomous pipeline keeps working while the registry grows underneath it.*

---

## (d) How the two anchor conclusions fit

### Device-backend-as-first-plugin

The COM-backend spec's aside ("the comhost can later be a built-in plugin — device-backend plugin
category") is **well-founded and low-risk.** The backend registry (`backend.py`) is *already* the right
shape: a registry, Protocol contracts, a serializable intent object, a JSON manifest via `list_backends()`,
and self-registration. Making "device backend" the **first plugin category** requires mainly:
1. **Discovery:** replace the hardcoded `devices/backends/__init__.py` import list with entry-point
   discovery (`astrodeck.backends`) — a plugin package that calls `register()` from its entry point drops
   in with no core edit.
2. **Manifest/metadata:** add plugin identity (id, version, author, min-app-version) alongside the existing
   `name`/`label`/`roles` — trivial extension of `list_backends()`.
3. **Nothing else structurally** — the contract, the Equipment UI's manifest-driven rendering, and the
   sidecar-isolation path (via `ascom_local` → comhost) are all already present. `ascom-local` is a live
   proof that "a backend can be a supervised sidecar," which is exactly what an *untrusted* device-driver
   plugin would want. **This is the cheapest, safest first category and a natural pilot for the whole
   system.**

### NINA Path-A (native reimplementation) and Path-B (NINA-familiar SDK)

- **Path A (reimplement top plugins natively — GO/primary):** These land as **first-party AstroDeck
  plugins in our own categories.** The shape of those categories must therefore fit what the marquee
  plugins need: **sequence items/triggers/conditions** (Target Scheduler, Ground Station, Moon-angle
  guards, TPPA), **provider/task registries** (dynamic exposure, solve), **`IPluggableBehavior`-tier
  detection/AF hooks** (Hocus Focus → an image/star-detection step category). So the plugin API should be
  designed **against the spike's top-8 table** as its concrete requirement set — if the sequence registry
  and a detection/provider hook can express those eight, the API is right.
- **Path B (NINA-familiar SDK — GO/secondary, after A):** This is **gated on the sequencer refactor
  (finding 5 / question 6).** NINA's authoring model is item/trigger/condition + mediator injection; our
  equivalents don't exist as *contracts* yet (only as baked-in behavior). The mapping the SDK should
  target:
  | NINA concept | AstroDeck equivalent to build |
  |---|---|
  | `ISequenceItem.Execute(progress, token)` | a registrable sequence step with `execute(context, token)` over the `sequence/engine` loop |
  | `ISequenceTrigger` (before/after hooks) | generalize the engine's hardcoded `_maybe_meridian_flip`/`_refocus_due`/dither into a **trigger registry** |
  | `ISequenceCondition` (loop predicate) | generalize the `Schedule`/window gates + quota guards into a **condition registry** |
  | mediator injection (`ICameraMediator`, …) | a **`SequenceContext`** injecting `devices/base.py` role handles + hub capabilities (the mediator analogue) |
  | `IEquipmentProvider<T>` | **the existing backend Protocol** (already done) |
  | `IPluggableBehavior` (star detect/AF) | a new image/detection-step category (`imaging/stars.py` + `focus`) |
  | `IDockableVM` (WPF panel) | the **deferred** UI-panel category (declarative first) |
  Adopt NINA's **stable-type-identity rule** (question 4) so saved AstroDeck plans survive plugin updates —
  the same constraint NINA lives under.
- **Path C (binary DLL hosting): NO-GO**, unchanged. Nothing in the plugin system should try to host NINA
  DLLs; the *sidecar mechanism* (comhost) is for **our** plugins, not for loading NINA's binaries.

**Net:** the device-backend category is a near-free first win; the **sequencer-registry refactor is the
critical path** for both the highest-value native reimplementations (Path A) and the NINA-familiar SDK
(Path B). The isolation and cross-language stories are already de-risked by the comhost. The two open
frontiers are the **sequencer refactor** and the **UI-panel contribution model**.
