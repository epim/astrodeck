# NINA Plugin Compatibility — Feasibility Spike

**Date:** 2026-07-19
**Type:** Time-boxed research spike (evidence + go/no-go). NOT a shipped feature; no repo code was modified.
**Goal under evaluation:** let AstroDeck users benefit from NINA's plugin ecosystem **without installing NINA**.
**Author:** feasibility-spike agent

> **Verified vs inferred.** Statements tagged **[V]** are verified from a cited source or from repo files read
> this session. **[I]** are informed inferences from domain knowledge / architecture reasoning and should be
> treated as estimates, not facts. Exact plugin-popularity *rankings* are **[I]** — NINA publishes no download
> counts.

---

## 0. TL;DR

- **Path A — reimplement top plugins natively: GO.** Already the project's proven model (AstroDeck reimplements
  NINA's AF, star detection, triggers from clean-room dossiers). Highest value-per-effort. Effort **L, incremental**.
- **Path B — NINA-familiar AstroDeck plugin SDK: GO (slow burn), sequence AFTER A.** Cheap to model the API on
  NINA's concepts; expensive to grow an ecosystem. Effort **M** for the SDK, ongoing for adoption.
- **Path C — host real NINA plugin DLLs (binary hosting): NO-GO.** Technically the CLR can be hosted, but a
  headless NINA plugin is welded to NINA's concrete runtime object graph (mediators, `IProfileService`,
  `IImageData`, WPF `NINA.WPF.Base`). The "fake host" you'd have to build **is essentially a headless
  reimplementation of NINA itself** — Windows-only, .NET-8-Desktop-only, and chained to NINA's SDK churn every
  release. Effort **XL**, fragility high, and it directly contradicts the "without NINA" goal.
- **CLR-hosting verdict:** Mechanically feasible via an **out-of-process .NET 8 sidecar** (preferred) or
  `pythonnet` (in-process, but has a live .NET-8/CoreCLR type-exposure bug — **[V]** pythonnet #2595). Neither
  clears the real blocker, which is **binding-surface coupling**, not runtime hosting.
- **Dev-box .NET check: absent.** `dotnet` is not on PATH, no `HKLM\SOFTWARE\dotnet\Setup\InstalledVersions`
  registry key, and no `dotnet.exe` in the standard install roots. **No pythonnet smoke test was run** (rule:
  don't install runtimes). **[V]**

---

## 1. NINA plugin architecture (precise)

### 1.1 Build & load model **[V]**

- **Language/runtime:** plugins are **C# class libraries** targeting **.NET 8.0** (NINA 3.0+ requires .NET 8;
  older 2.x plugins targeted .NET Framework 4.8 and had to be migrated). Because NINA is a **WPF desktop** app,
  the effective TFM is `net8.0-windows` and the app/plugins need the **Windows Desktop runtime** — Windows-only.
- **Discovery/composition:** **MEF** (Managed Extensibility Framework, `System.ComponentModel.Composition`).
  A plugin exports classes with `[Export(typeof(IX))]`; NINA composes them into its container at startup.
  Dependencies are pulled in via `[ImportingConstructor]` constructor injection.
- **Manifest:** exactly one `[Export(typeof(IPluginManifest))]` per plugin. The base class
  `NINA.Plugin.PluginManifest` auto-populates metadata (Title, Guid, Version, Description, Author, License,
  Repository, MinimumApplicationVersion) from `AssemblyInfo.cs` assembly attributes.
- **Install:** the user copies the built DLL(s) into `%LOCALAPPDATA%\NINA\Plugins\...`; NINA also ships an
  in-app Plugin Manager driven by the central `nina.plugin.manifests` repo.
- **SDK NuGet packages referenced:** `NINA.Plugin` (primary), and transitively/practically `NINA.Core`,
  `NINA.Sequencer`, `NINA.WPF.Base`, `NINA.Astrometry`, `NINA.Image`, `NINA.Equipment`, `NINA.Profile`,
  `NINA.CustomControlLibrary`, plus `OxyPlot.Contrib.Wpf` for charts.
- **Stability contract:** once published, a plugin's exported **namespaces + type names must not change** —
  saved sequences (JSON) embed fully-qualified type names, and NINA deserializes an unknown/uninstalled type as
  a placeholder (`UnknownSequenceTrigger` and equivalents — corroborated by the repo trigger dossier §6b).

Sources: [nina.plugin.template](https://github.com/isbeorn/nina.plugin.template),
[NINA.Plugin on NuGet](https://www.nuget.org/packages/NINA.Plugin/),
[nina.plugin.manifests](https://github.com/isbeorn/nina.plugin.manifests).

### 1.2 Extension points, classified HEADLESS vs WPF-UI-bound

| Extension point | Base class | Purpose | UI coupling |
|---|---|---|---|
| `ISequenceItem` | `NINA.Sequencer.SequenceItem` | a sequence instruction | **Logic headless**; needs DataTemplates to render in-app *(see nuance)* **[V]** |
| `ISequenceContainer` | `SequenceContainer` | instruction set / strategy | Logic headless; DataTemplate for UI **[V]** |
| `ISequenceTrigger` | `SequenceTrigger` | before/after-item hook | **Logic headless**; DataTemplate for UI **[V]** |
| `ISequenceCondition` | `SequenceCondition` | loop predicate | **Logic headless**; DataTemplate for UI **[V]** |
| `IDockableVM` | `NINA.WPF.Base.DockableVM` | dockable imaging-tab panel | **WPF-bound** (it *is* a view-model with a View) **[V]** |
| `IPluggableBehavior` | — | pluggable star detection / annotation / autofocus factories | **Logic mostly headless** but returns NINA image/AF types **[V/I]** |
| `IEquipmentProvider<T>` | — | custom device drivers | Logic headless at the interface; concrete types are NINA equipment models **[V/I]** |
| Image processing (`IImagePatternFactory`, filters) | — | pipeline hooks | Operates on NINA `IImageData`/`IRenderedImage` **[V/I]** |
| `[Export(typeof(ResourceDictionary))]` DataTemplates | — | options / mini / dockable views | **WPF-bound** (XAML) **[V]** |

**The critical nuance on "headless" sequence items [V/I]:** the `Execute(progress, token)` *method body* of a
sequence item or trigger is pure headless logic. But:
1. The **class is instantiated by MEF via `[ImportingConstructor]`** taking NINA **mediator** interfaces (below).
   To create the object at all you must supply those.
2. NINA **requires a matching `DataTemplate`** (`<Type>_Mini`, `<Name>_Options`, `<Type>_Dockable`) exported as
   a `ResourceDictionary` for the item to appear/configure in the app. That XAML is WPF. A *headless host* can
   skip the DataTemplate (it never renders), but a *plugin author* still ships it, and plugins routinely bundle
   view-models, converters, and OxyPlot views in the same assembly — so the DLL references WPF even when the
   instruction logic doesn't.

### 1.3 Injectable services (the runtime host surface a headless plugin actually calls) **[V]**

Constructor-injectable via `[ImportingConstructor]` (confirmed list):

`IProfileService`, `ICameraMediator`, `ITelescopeMediator`, `IFocuserMediator`, `IFilterWheelMediator`,
`IGuiderMediator`, `IRotatorMediator`, `IFlatDeviceMediator`, `IWeatherDataMediator`, `IDomeMediator`,
`ISwitchMediator`, `ISafetyMonitorMediator`, `IImagingMediator`, `ISequenceMediator`,
`IApplicationStatusMediator`, `IImageSaveMediator`, `IImageHistoryVM`, `IPlateSolverFactory`,
`IPlanetariumFactory`, `INighttimeCalculator`, `IDeepSkyObjectSearchVM`, `IWindowServiceFactory`,
`IExposureDataFactory`, `IImageControlVM`, `IFramingAssistantVM` (25+ services).

Data types these hand back are concrete NINA image/exposure types: `IExposureData` → `IImageData`
(raw pixel array + `IImageStatistics`) → `IRenderedImage` (stretched `BitmapSource` for display + star-detection
result). These are exactly the types the AstroDeck native-parity dossiers reverse-engineered.

Sources: [nina.plugin.template](https://github.com/isbeorn/nina.plugin.template) and the NINA plugin-development
docs referenced from it.

---

## 2. Binary-hosting feasibility (the hard path)

### 2.1 Can a Python/Rust app host the CLR and load a NINA plugin DLL?

**Runtime hosting: yes, mechanically. [V]**
- **`pythonnet` (in-process):** can load CoreCLR (`PYTHONNET_RUNTIME=coreclr`, `clr_loader.get_coreclr()`,
  `pythonnet.set_runtime()` before `import clr`) and `AddReference` a .NET 8 DLL. **Caveat [V]:** pythonnet
  3.0.x has an open bug ([#2595](https://github.com/pythonnet/pythonnet/issues/2595)) where **.NET 8 assemblies
  under CoreCLR load but their types are not exposed** (`AttributeError` on access) while **.NET 9 works** — a
  real, unresolved footgun for exactly the .NET 8 target NINA uses.
- **.NET sidecar process (preferred):** a small `net8.0-windows` host EXE that loads the plugin and exposes a
  local RPC (gRPC/JSON-RPC over loopback) to AstroDeck. This is the clean CLR-hosting mechanism — no in-process
  marshaling, no pythonnet, isolates crashes, and matches how AstroDeck already talks to external processes
  (the existing NINA bridge is HTTP; PHD2 is a loopback JSON-RPC socket — see `devices/nina.py`).
- **Raw CLR hosting APIs (`hostfxr`/`nethost` from Rust):** possible but strictly worse ergonomics than a
  managed sidecar; no reason to pick it.

**Verdict on the mechanism: an out-of-process .NET 8 sidecar is the right hosting vehicle. [I]** But hosting the
CLR is the *easy 10%*. The blocker is what the plugin expects once loaded.

### 2.2 Minimal host surface — and why a fake host is impractical

Take the simplest useful headless plugin: a **sequence-item** or **trigger**. At runtime it does roughly:

```
ctor(IProfileService, ICameraMediator, ITelescopeMediator, IImagingMediator, IApplicationStatusMediator, ...)
Execute(progress, token):
    profile = profileService.ActiveProfile          // hundreds of concrete settings
    var exposureData = await imagingMediator.CaptureAndPrepareImage(captureSeq, ...)  // -> IExposureData
    var img = await exposureData.ToImageData()       // -> IImageData (+ IImageStatistics, star detection)
    telescopeMediator.SlewToCoordinatesAsync(coords) // concrete NINA Coordinates/epoch types
    applicationStatusMediator.StatusUpdate(...)      // status bar
```

To satisfy that **you must provide working implementations of every injected mediator the plugin touches**, and
each returns **concrete NINA runtime types**:

- `IProfileService` → a full `IProfile`/`ActiveProfile` object graph (camera, focuser, plate-solve, filter,
  astrometry settings — the same surface the AF and star-detection dossiers enumerate).
- `IImagingMediator`/`IExposureDataFactory` → `IExposureData`/`IImageData`/`IRenderedImage`, i.e. NINA's imaging
  pipeline including MTF stretch + star detection (the *exact* subsystems documented in
  `nina-star-detection-hfr.md`).
- `ITelescopeMediator` etc. → NINA `TelescopeInfo`, `Coordinates` (with JNOW/J2000 transforms), `PierSide`.
- `IPlateSolverFactory`, `INighttimeCalculator`, `IPlanetariumFactory` → more concrete graphs.

These interfaces live in NINA's own assemblies (`NINA.Equipment`, `NINA.Core`, `NINA.Astrometry`, `NINA.Image`,
`NINA.Profile`, `NINA.WPF.Base`). **You cannot reference the interface without referencing the assembly that
defines it**, and many of those assemblies (esp. `NINA.WPF.Base`, and image/plot types) transitively pull in
**WPF (`PresentationFramework`, `System.Windows`, `BitmapSource`)** and **OxyPlot**. So:

- **Option (i): fake the mediators, reference the real NINA interface assemblies.** You still ship NINA's DLLs
  (to get the interfaces + concrete return types like `Coordinates`, `ImageData`), and you must hand-build
  faithful fakes of `IImageData`/`IProfileService`/imaging pipeline. Faking `IImageData` *correctly* means
  reproducing NINA's star detection/stretch — **which is the native-parity work anyway**. The shim is **not
  small; it approaches "reimplement NINA's runtime."** **[I]**
- **Option (ii): load the *real* NINA assemblies to back the mediators.** Then you are effectively **running
  NINA headlessly minus the shell** — enormous surface, Windows + WPF runtime required, and you have shipped
  ~all of NINA, which **defeats the "without NINA" goal** and re-introduces the maintenance/licensing footprint
  the goal wanted to eliminate. **[I]**

**What breaks concretely [V/I]:**
- **WPF / `System.Windows`:** `IDockableVM` panels and any DataTemplate/OxyPlot view are non-starters headless.
  Loading `NINA.WPF.Base` needs the Windows Desktop runtime; no Linux/macOS server host.
- **NINA's DI/MEF container:** plugins assume the full NINA MEF catalog is present; a partial catalog fails
  composition for any plugin whose imports you didn't stub.
- **Concrete `IImageData`/`IProfileService`:** these are not thin DTOs — they carry NINA's imaging pipeline and
  the entire profile model. A "reasonable fake" is a large, bug-prone reimplementation.

**Coupling conclusion:** headless *logic* is separable in principle, but the *binding surface* is welded to
NINA's concrete runtime. The shim to make a real plugin run is **XL and tracks NINA's SDK churn each release**.

---

## 3. Environment check **[V]**

- `dotnet --info` (Git Bash): `command not found`.
- PowerShell probe: **NOT_ON_PATH**; `HKLM:\SOFTWARE\dotnet\Setup\InstalledVersions` **REG_ABSENT**; no
  `dotnet.exe` under `%ProgramFiles%\dotnet`, `%ProgramFiles(x86)%\dotnet`, or `%LOCALAPPDATA%\Microsoft\dotnet`.
- **Conclusion:** no .NET SDK/runtime on this box. Per spike rules, **nothing was installed** and **no pythonnet
  CLR smoke test was run**. This does not change the assessment: the CLR-hosting *mechanism* is well-documented
  (§2.1) and is not the bottleneck; the coupling surface (§2.2) is. The pythonnet #2595 .NET-8 caveat is an
  additional independent reason the in-process route is risky even once a runtime exists.

---

## 4. The three candidate paths, scored

Scoring keys: Effort S/M/L/XL. "% value captured" = share of real-world plugin *value* users actually get.

### Path A — Reimplement the most-impactful NINA plugins natively as AstroDeck plugins

This is the model AstroDeck already uses (the three dossiers in `docs/native-parity/algorithms/` are clean-room
reimplementations of NINA's AF, star-detection/HFR, and sequencer/trigger semantics — MPL-2.0 provenance,
behavior-not-code). Extends naturally to the plugin layer.

**Top ~8 NINA plugins by impact, with reimplementation effort [popularity = I; effort = I]:**

| # | Plugin (author) | What it does | Extension pts | Reimpl. effort | Notes |
|---|---|---|---|---|---|
| 1 | **Target Scheduler** (tcpalmer) **[V nature]** | DB-driven multi-target/multi-night automated scheduling; one `Target Scheduler Container` instruction + large project/target/exposure-plan DB UI | ISequenceContainer + big dockable UI + SQLite | **XL** | Highest-value automation plugin. AstroDeck already has a multi-night program (memory) — strong overlap; reimplement the *planning engine*, own UI. |
| 2 | **Hocus Focus** (ghilios) **[V nature]** | Better star detection, star annotation, autofocus, tilt/aberration inspection | `IPluggableBehavior` (star detect/AF) + dockable | **L** | AstroDeck already has NINA-parity star detection + AF dossiers; this is the "better than core" tier. Reimplement detector + tilt model. |
| 3 | **Ground Station** (daleghent) **[V nature]** | Notifications (Pushover/Telegram/email/MQTT/IFTTT/Discord) + failure/event triggers | ISequenceTrigger/ISequenceItem + options UI | **M** | Mostly I/O + templating; no astro math. Fast win, high daily value. |
| 4 | **Smart Histogram / dynamic exposure calc** **[I]** | Computes optimal sub-exposure length from sensor read-noise + sky background | dockable + sequence item | **M–L** | Well-bounded math (sensor model). Good native fit. |
| 5 | **Three Point Polar Alignment (TPPA)** **[I nature]** | Guided polar-alignment routine via 3 plate-solved points | dockable + plate-solve | **L** | AstroDeck already owns plate-solving; add the alignment solver + UI. |
| 6 | **Astro-Physics Tools / PlaneWave Tools** (daleghent) **[V nature]** | Vendor mount control (park positions, APCC/PWI integration) | IEquipmentProvider / sequence items | **L each, narrow** | Vendor-specific; do on demand. Belongs behind AstroDeck's `devices/base.py` abstraction. |
| 7 | **Session Metadata / Web Session History Viewer** (daleghent) **[V nature]** | Per-image metadata sidecars + session reporting/history | ISequenceItem + web/report UI | **M** | Overlaps AstroDeck's existing sessions program (memory). |
| 8 | **Moon Angle / Sky-condition guards** **[I]** | Skip/guard targets by moon separation, altitude, cloud/safety | ISequenceCondition/ISequenceTrigger | **S–M** | Pure astrometry AstroDeck already has; small conditions. |

- **Effort (overall program):** **L, incremental** — each plugin is an independent, testable unit; the hardest
  (Target Scheduler) is XL alone but overlaps existing AstroDeck programs.
- **User-install burden:** **none** — ships as AstroDeck features/plugins; no NINA, no .NET.
- **UI story:** **native React** (AstroDeck's stack) — consistent, cross-platform, mobile-friendly.
- **Maintenance risk vs NINA SDK churn:** **low** — decoupled from NINA releases; you track *behavior*, not
  binaries. (Clean-room dossier discipline already established.)
- **% of real plugin value captured:** **~70–85%** of what *most users actually use*, because plugin value is
  heavily concentrated in a handful of plugins (scheduler, focus, notifications, polar align). The long tail of
  niche plugins is not captured. **[I]**

### Path B — A NINA-familiar AstroDeck plugin SDK (authors port easily)

Model AstroDeck's plugin API on NINA's concepts: `SequenceItem`/`SequenceTrigger`/`SequenceCondition` analogues,
a mediator-style device/service injection surface mapped onto `devices/base.py` ABCs, an `Execute(progress,
token)` contract, and a manifest. Authors keep the mental model; they rewrite against AstroDeck's API (Python or
a WASM/TS plugin surface — TBD) instead of C#/WPF.

**What authors must change [I]:**
- **Language/runtime:** C#/.NET/WPF → AstroDeck's plugin runtime (not source-compatible; concept-compatible).
- **UI:** WPF DataTemplates/OxyPlot → AstroDeck's declarative UI (React components or a schema-driven form).
- **Services:** NINA mediators → AstroDeck's equivalent injected services (same *shape*, different types).
- **Sequence model:** maps almost 1:1 — AstroDeck already implements NINA's container/trigger execution
  semantics (see `nina-sequencer-triggers.md`), so the hardest conceptual part is done.

- **Effort:** **M** to design/ship the SDK (the sequencer engine parity is the expensive prerequisite, and it's
  already built). Ongoing effort to document, stabilize, and grow adoption is the real cost.
- **User-install burden:** low (AstroDeck-native plugins; whatever packaging AstroDeck chooses).
- **UI story:** native/consistent.
- **Maintenance risk vs NINA churn:** low — you own the API; NINA changes don't break you.
- **% value captured:** **0% on day 1** (empty ecosystem), growing only with third-party adoption — which is a
  chicken-and-egg community problem, not a technical one. Realistically a **long-tail multiplier on Path A**,
  not a standalone value source near-term. **[I]**

### Path C — Host real NINA plugin DLLs (headless subset)

Load unmodified NINA plugin binaries via a .NET 8 sidecar and back their injected mediators with AstroDeck.

- **Realistic coverage:** only the narrow subset of plugins that are (a) headless sequence-items/triggers,
  (b) touch few mediators, and (c) don't reference WPF/image/plot types in their assembly. In practice **most
  high-value plugins fail one of these** — Target Scheduler (huge DB UI + own SQLite + dockable), Hocus Focus
  (image pipeline + dockable + OxyPlot), TPPA (dockable), Ground Station (options UI, though its trigger logic
  is closer to headless). **Estimated realistic coverage: a small single-digit fraction of plugin value. [I]**
- **Effort:** **XL** — build the sidecar *and* a faithful fake of `IProfileService` + `IImagingMediator` +
  `IImageData`/star-detection + `ITelescopeMediator` concrete types (§2.2). That fake is most of a headless NINA.
- **User-install burden:** **high** — Windows-only, requires **.NET 8 Windows Desktop runtime**, and the user
  must obtain the plugin DLLs (and, under Option (ii), NINA's own assemblies).
- **UI story:** **broken** — plugin UIs are WPF DataTemplates; none render in AstroDeck's React app. Users get
  logic with no configuration surface unless AstroDeck also reimplements each plugin's options UI.
- **Maintenance risk vs NINA SDK churn:** **very high** — the fake host binds to NINA's concrete interfaces and
  types; every NINA `NINA.*` assembly bump can break composition or type contracts. You are chained to NINA's
  release cadence, which is the opposite of the vendor-neutral direction (memory).
- **% value captured:** **low (~5–15%)** and skewed to the least differentiated plugins. **[I]**
- **Extra risk [V]:** even the *mechanism* is shaky in-process (pythonnet #2595 on .NET 8); the out-of-process
  sidecar avoids that but adds IPC + lifecycle + crash-recovery surface for little payoff.

---

## 5. Recommendation

**Per-path go/no-go**
- **Path A (native reimplementation): GO — primary.** Best value-per-effort, zero user-install burden,
  cross-platform, low maintenance risk, and it *is* the project's already-proven clean-room model. Captures the
  ~70–85% of plugin value concentrated in a few marquee plugins.
- **Path B (NINA-familiar SDK): GO — secondary, sequence after A.** Cheap to build on top of the sequencer
  engine you already have; it's the mechanism that lets the community cover the long tail Path A won't. Treat as
  a force-multiplier, not a near-term value source.
- **Path C (binary DLL hosting): NO-GO.** The CLR can be hosted, but the plugin binding surface is welded to
  NINA's concrete runtime + WPF; the shim to run real plugins is essentially a headless reimplementation of
  NINA, Windows-only, and permanently chained to NINA's SDK churn — directly contradicting the "without NINA"
  goal. Do not pursue beyond this spike.

**Recommended sequence**
1. Keep executing **Path A**, prioritized by concentrated value: **Ground Station-class notifications (M, fast
   win) → dynamic-exposure/Smart-Histogram (M–L) → Hocus-Focus-tier detection/AF polish (L) → TPPA polar align
   (L) → Target Scheduler-class scheduling (XL, leverage the existing multi-night program)**.
2. In parallel, harden the sequencer/device abstractions into a **documented Path B plugin SDK** modeled on
   NINA's `SequenceItem`/`Trigger`/mediator concepts, so third-party authors can port mental models (not code).
3. **Retire the binary-hosting idea.** Keep the *existing* running-NINA Advanced-API **bridge**
   (`devices/nina.py`) as the on-ramp for users who still run NINA — that already delivers "benefit from a
   configured NINA rig" without the hosting nightmare, and it's the honest interop story.

**Single most important risk**
The one thing that can sink this: **Path C's coupling illusion.** "It's just a headless C# method, we can host
it" is true at the CLR level and false at the binding level — a real plugin drags in `IProfileService`,
`IImageData`/star-detection, and WPF assemblies, so the host you must build to run *one* real plugin is most of a
headless NINA, Windows-only, and re-broken by every NINA release. Anchoring compatibility to native
reimplementation (A) with a NINA-shaped SDK (B) is the only path that stays vendor-neutral and cross-platform.

---

## Appendix — evidence log

**Repo files read this session:**
`docs/native-parity/algorithms/nina-sequencer-triggers.md`, `nina-autofocus.md`, `nina-star-detection-hfr.md`
(clean-room dossiers, MPL-2.0 provenance — confirm AstroDeck already reimplements NINA AF/star-detection/trigger
semantics natively); `server/astrodeck/devices/nina.py` and `server/astrodeck/devices/backends/nina_backend.py`
(the existing Advanced-API HTTP:1888 bridge to a *running* NINA — interop, not hosting; also reaches PHD2 over a
loopback JSON-RPC socket at :4400, a template for a sidecar RPC).

**Environment:** `dotnet` not on PATH (Git Bash + PowerShell); `HKLM\SOFTWARE\dotnet\Setup\InstalledVersions`
absent; no `dotnet.exe` in standard roots → no .NET installed; no pythonnet smoke test run (per rules).

**Web sources (accessed 2026-07-19):**
- [isbeorn/nina.plugin.template](https://github.com/isbeorn/nina.plugin.template) — .NET 8, MEF `[Export]`,
  `IPluginManifest`/`PluginManifest`, base classes, DataTemplate `_Options`/`_Mini`/`_Dockable`, install path,
  type-name stability contract. **[V]**
- [NINA.Plugin on NuGet](https://www.nuget.org/packages/NINA.Plugin/) — SDK package. **[V]**
- [isbeorn/nina.plugin.manifests](https://github.com/isbeorn/nina.plugin.manifests) — central plugin registry. **[V]**
- Injectable mediator list — NINA plugin-dev docs via the template search result (IProfileService + 13 device
  mediators + IImagingMediator + status/save/history/plate-solver/planetarium factories). **[V]**
- [tcpalmer Target Scheduler](https://tcpalmer.github.io/nina-scheduler/) and
  [nina.plugin.assistant](https://github.com/tcpalmer/nina.plugin.assistant) — DB-driven scheduler; one
  `Target Scheduler Container` instruction + project/target/exposure-plan DB UI. **[V]**
- [ghilios/hocus-focus](https://github.com/ghilios/joko.nina.plugins) — star detection/annotation/AF/tilt as an
  `IPluggableBehavior`-tier plugin. **[V]**
- [daleghent/nina-plugins](https://github.com/daleghent/nina-plugins) — archived 2025-04, split into Ground
  Station (notifications), Astro-Physics Tools + PlaneWave Tools (vendor device control). **[V]**
- [pythonnet embedding docs](https://pythonnet.github.io/pythonnet/python.html) and
  [pythonnet #2595](https://github.com/pythonnet/pythonnet/issues/2595) — CoreCLR load path; **.NET 8 type
  exposure bug (works on .NET 9)**. **[V]**
</content>
</invoke>
