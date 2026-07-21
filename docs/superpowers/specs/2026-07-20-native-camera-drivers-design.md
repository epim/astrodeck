# Native Camera Drivers — vendor-agnostic camera framework + ZWO ASI & Player One adapters — Design

**Date:** 2026-07-20
**Type:** Spec (brainstorm approved: layered adapter architecture, both cameras in one
sub-project, full imaging train, Player One SDK bundling gated on license verification,
Poseidon LRN/HCG as priority acceptance criteria).
**Campaign:** Platform expansion — the last device class still requiring vendor software.
Builds on the driver framework (A, merged 4f932d7), the AM5N mount driver (B, merged
bc9cd14), and the accessory drivers (C, merged 73cb1da). Closes the "zero vendor
software" gap for the imaging train.
**Rig under test:** Player One Poseidon-M Pro (IMX571 APS-C mono, cooled) as the imaging
camera; ZWO ASI220MM as the guide camera.

## Goal

A **vendor-agnostic native camera framework** in which adding a brand-new camera brand
costs exactly one self-contained module — never a change to shared, orchestrator, UI, or
imaging-train code — and no brand's real capabilities are flattened to a
lowest-common-denominator interface. Prove the framework with two deliberately different
adapters: ZWO ASI (guide) and Player One (imaging, including its Low-Read-Noise mode).

## Non-goals (YAGNI)

- **No new abstraction for cameras themselves.** `Camera.expose() → CameraFrame` already
  exists and already carries sim/Alpaca/NINA. Native cameras satisfy the *existing*
  contract; this campaign adds the layer *below* it (engine/adapter/bindings), not above.
- **No "support every brand" claim.** A brand with no driver written is supported by
  nobody. The guarantee is the *cost and isolation* of adding one (§7), verified by making
  the two first adapters vendor-dissimilar so the waist isn't secretly ZWO-shaped.
- **No Linux `.so` bundling yet** (joins when Linux is a target; loaders are written
  Linux-safe via `ctypes.CDLL` regardless — the C-sub-project precedent).
- **No OSC/debayer pipeline changes** — `CameraFrame.bayer_pattern` already threads to the
  hub's existing render path; the Poseidon-M and ASI220MM are both mono, so debayer stays
  untouched and merely *declared* by the descriptor for a future color adapter.
- **No plate-solve/sequencer changes** — cameras produce frames; downstream consumers are
  unchanged.

## Scope

**In:** a shared `NativeCamera` exposure/cooling engine; the `CameraAdapter` interface +
`CameraCapabilities` descriptor (additive, no LCD); an in-tree adapter registry; ctypes
bindings + vendored SDKs for ZWO ASICamera2 (MIT, bundled) and Player One (bundled *iff*
license verification passes, else escalate); the `zwo-asi` and `player-one` backends; a
small backward-compatible orchestrator change so the `guide_camera` role resolves from its
own endpoint; a parametrized camera-contract test suite every adapter must pass; the
LRN/HCG bias-frame acceptance harness; at-scope validation.

**Out (deferred):** color/OSC debayer work; any third brand (the framework makes it a
drop-in, but none is on the rig); Linux SDK bundling; hardware-trigger / high-speed video
streaming modes (the descriptor reserves room; no consumer needs them yet); ASI220MM
on-sky guiding tuning (that is the native-guider first-light thread, gated on a clear
night and tracked separately).

## Design

### §1 — The thin waist: engine / adapter / bindings

Three layers. A new brand author writes only the bottom two, both small.

```
  Camera (base.py, EXISTING)            ← the contract the rest of AstroDeck consumes
      ▲ implements
  NativeCamera  (cameras/engine.py)     ← SHARED, vendor-blind, written once
      │ composes (has-a, not is-a)
  CameraAdapter (cameras/adapter.py)    ← the narrow, stable waist — ~10 hooks per brand
      │ calls
  <brand>_sdk.py  (ctypes signatures)   ← mechanical DLL binding, per brand
      │ loads
  vendor/<brand>/*.dll                   ← vendored binary + LICENSE + provenance
```

**Composition, not inheritance.** `NativeCamera(Camera)` *holds* a `CameraAdapter`; the
adapter is a plain object with no asyncio, no `CameraFrame`, no lifecycle knowledge — so it
is trivially unit-testable against a fake SDK, and the engine's substantial shared logic
is written exactly once. (Deliberate, small departure from the accessory pattern where
`EafFocuser`/`CaaRotator` subclass the device and hold an SDK directly; justified because
the shared camera surface — expose loop, cooling loop, ROI, buffer assembly — is large.)

### §2 — `NativeCamera` engine (shared, vendor-blind)

`cameras/engine.py`. Owns everything identical across all cameras:

- **Exposure lifecycle** — generalizes `alpaca.py:expose()`: apply gain/offset/bin/ROI/read
  mode via adapter hooks → `adapter.start_exposure(...)` → poll `adapter.image_ready()`
  with a deadline of `seconds + margin` → `adapter.read_frame()` → shape raw bytes into a
  2-D `uint16` ndarray → build `CameraFrame(data_is_linear=True, full_well=..., ...)`.
- **Cancellation** — on `asyncio.CancelledError`, call `adapter.abort()` then re-raise
  (matches `Camera.expose` docstring: aborts in-camera). `abort_exposure()` delegates to
  `adapter.abort()`.
- **Retry envelope + timeouts** — reuse the exposure-retry / imageready-timeout logic
  shipped in the guider-hardening wave rather than re-inventing it.
- **Cooling regulation** — a shared setpoint/poll loop driving `adapter.set_target_temp`,
  `adapter.get_temperature`, `adapter.set_cooler`; surfaces temp + cooler power to the hub.
- **ROI / binning math** — compute frame geometry from sensor size + bin + subframe once;
  the adapter merely applies the resulting numbers.
- **Buffer assembly** — raw bytes → ndarray using the adapter's declared `bit_depth` and
  byte order; populate `full_well` from the descriptor's `max_adu`.

The engine reads *only* the `CameraCapabilities` descriptor and the adapter hooks — it
never names a vendor.

### §3 — `CameraAdapter` — the stable waist

`cameras/adapter.py`. An ABC (not a bare Protocol — we want `NotImplementedError` defaults
for the optional hooks). Required hooks are the irreducible minimum:

```python
class CameraAdapter(ABC):
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
    def read_frame(self) -> bytes: ...       # raw sensor buffer; engine shapes it
    @abstractmethod
    def abort(self) -> None: ...

    # Optional — implement ONLY if the hardware has the feature. Base raises
    # DeviceError (never silently no-ops), exactly as Camera.set_cooler does today.
    def set_read_mode(self, mode: str) -> None: raise DeviceError(...)
    def set_target_temp(self, c: float) -> None: raise DeviceError(...)
    def set_cooler(self, on: bool) -> None: raise DeviceError(...)
    def get_temperature(self) -> float | None: return None
    def get_cooler_power(self) -> int | None: return None
    def set_dew_heater(self, power: int) -> None: raise DeviceError(...)
```

All hooks are **synchronous** (direct SDK calls); the engine runs them via
`asyncio.to_thread` so a slow USB read never blocks the loop (the accessory-driver
precedent). `ROI` is a small dataclass `(x, y, w, h, bin)`.

### §4 — `CameraCapabilities` — additive descriptor, no lowest-common-denominator

`cameras/adapter.py`. The mechanism that keeps brand superpowers from being flattened:
the base contract *requires* almost nothing, and every capability is **declared** and
**probed**, never assumed.

```python
@dataclass(frozen=True)
class CameraCapabilities:
    sensor_width: int; sensor_height: int; pixel_size_um: float
    bit_depth: int                      # 16 for IMX571 on-chip ADC
    bayer_pattern: str | None           # None for mono (both rig cams)
    gain_range: tuple[int, int]; offset_range: tuple[int, int]
    bin_modes: tuple[int, ...]          # e.g. (1, 2, 3, 4)
    roi_supported: bool
    has_cooler: bool; has_dew_heater: bool
    max_adu: int                        # → CameraFrame.full_well
    read_modes: tuple[str, ...] = ()    # discrete sampling modes; () when none
    hcg_threshold_gain: int | None = None   # informational; None when N/A
    extra: Mapping[str, object] = field(default_factory=dict)  # typed brand extras
```

- Consumers (UI, imaging train) render **what is present**: cooling controls only when
  `has_cooler`; a read-mode picker only when `read_modes` is non-empty; a "read noise drops
  here" hint at `hcg_threshold_gain`. Nothing is clamped to the intersection of all cameras.
- `read_modes` is the generalization of Player One's LRN/Normal, QHY's ReadMode, etc.
  The ASI220MM simply advertises `read_modes=()` and is unaffected.
- `extra` carries genuinely brand-unique data in a typed map without polluting the core
  descriptor or forcing an LCD.

### §5 — Player One adapter: LRN + HCG (priority)

`cameras/player_one.py` + `cameras/player_one_sdk.py`, over `vendor/playerone/`.

- **LRN (Low Read Noise) sampling mode** is a *discrete* mode (Normal ↔ Low Noise),
  independent of gain. Modeled as `read_modes=("Normal", "LowNoise")`; applied per-exposure
  via `adapter.set_read_mode(...)` before `start_exposure`. Selectable in the UI, persisted
  per-profile and per-exposure-setting.
- **HCG** engages automatically at **gain ≥ 125**, taking read noise from ~3.96 e⁻ (gain 0)
  toward ~1.36 e⁻. Not a toggle → `hcg_threshold_gain=125` (informational only).
- Full imaging train: cooling setpoint + regulation + temp/power readout; dew heater;
  ROI; 16-bit readout; `eGain` (e⁻/ADU) surfaced via `extra` for the read-noise math.
- **The exact Player One SDK config symbol for the sampling mode is pinned against the
  actual `PlayerOneCamera.h` header at build time** — the plan's first Player One task
  verifies it; it is NOT asserted as fact in this spec.

### §6 — ZWO ASI adapter (guide)

`cameras/zwo_asi.py` + `cameras/zwo_asi_sdk.py`. The ASICamera2 SDK is the **same
MIT-licensed `libasi` family already vendored** for CAA/EAF, so the `ASICamera2` DLL joins
`vendor/zwo/` with its license already covered (README updated with the added binary +
version). Capabilities: mono, `read_modes=()`, no cooler (ASI220MM is uncooled), gain/offset
ranges + bin from the SDK. This is the "does the waist bend to a *second*, dissimilar
vendor" proof, and it unblocks native guiding with zero vendor software.

### §7 — Adding a brand-new brand (the maintainability contract)

To add "BrandX", an author touches **only**:

1. `vendor/brandx/` — DLL + `LICENSE` + provenance `README`.
2. `cameras/brandx_sdk.py` — ctypes signatures (mechanical, mirrors `zwo_sdk.py`'s
   `_SIGNATURES`/`_declare`).
3. `cameras/brandx.py` — the adapter class + its `capabilities()`.
4. Registration: one line in `cameras/registry.py` **or** an out-of-tree pip package with an
   `astrodeck.backends` entry point (discovery from sub-project A handles both).
5. Tests: a fake-SDK double (the `FakeEafSdk`/`FakeCaaSdk` pattern) — and the **parametrized
   camera-contract suite auto-runs against the new adapter** (§9), so it cannot silently
   violate the contract.

**Zero edits** to the orchestrator, imaging train, preview/render path, guider, or UI.

### §8 — Backends + the orchestrator `guide_camera` generalization

- **Per-vendor backends** `zwo-asi` and `player-one` (`backends/zwo_asi.py`,
  `backends/player_one.py`), each thin: manifest (`transport="local"`, `hostless=True`,
  `hardware=True`, `driver_type`), `discover()` (SDK-enumerated, guarded → `[]` on any
  failure so absent DLLs never break discovery), and `open()`→session. The session's
  `get_device(role)` builds a `NativeCamera` wrapping its vendor adapter — the *role*
  (`camera` vs `guide_camera`) picks the physical slot, not the vendor. `guide_camera()`
  returns the device when this session holds the guide-camera role.
- **The one shared-code change.** Today `_pick_guide_camera` (`orchestrator.py:287`) only
  asks the *camera-role* session for its `guide_camera()`, assuming one session holds both
  cameras (true for sim/NINA, false for two-vendor native rigs). Generalize it,
  backward-compatibly: **if an endpoint is assigned the `guide_camera` role, resolve the
  guide camera from that endpoint's own session; otherwise fall back to the camera-role
  session's `guide_camera()`.** The sim/NINA one-session path is preserved by the fallback;
  the native two-backend path now works. `ConnectResult.guide_camera` and the native
  guider's consumption of it are unchanged.

### §9 — Testing

- **Parametrized camera-contract suite** (`tests/test_camera_contract.py`) — the keystone.
  A single behavioral suite (`expose` returns a well-formed `CameraFrame`; `data` is 2-D
  `uint16` of the right geometry; cancellation calls `abort`; unsupported optional hooks
  raise `DeviceError`; capability declarations are self-consistent) is parametrized over
  **every registered adapter** driven by a fake SDK double. Adding a brand automatically
  subjects it to the full suite.
- **Per-adapter unit tests** over fake SDKs (`FakeAsiSdk`, `FakePlayerOneSdk`) with call
  logs — gain/offset/bin/ROI/read-mode applied in the right order; cooling loop drives the
  right hooks; buffer assembly shapes bytes correctly per bit depth/byte order.
- **Engine tests** — retry envelope, imageready timeout, cooling regulation convergence,
  ROI math — vendor-blind, against a trivial in-memory fake adapter.
- **Cross-platform degradation** — off-Windows (no DLLs) the backends are absent from the
  registry/`/api/backends` without error (the accessory-driver precedent + CI job).
- **LRN/HCG read-noise acceptance (photon-free, at the desk):** bias frames (0 s) at
  Normal vs LowNoise → ADU σ converted to e⁻ via `eGain` must show LRN lower; a
  gain-124 vs gain-125 pair must show the HCG drop toward ~1.36 e⁻. Doubles as end-to-end
  proof of the expose→download→`CameraFrame` path.
- **At-scope (clear night):** real light frames through the Poseidon (cooling stable at
  setpoint); native-guider first light through the ASI220MM + the native AM5N mount.

### §10 — SDK bundling & licensing

- **ZWO ASICamera2** — MIT `libasi`, already the basis for `vendor/zwo/`; bundle the
  `ASICamera2` DLL, extend `vendor/zwo/README.md` with the added binary + SDK version.
- **Player One** — redistribution terms are **not publicly documented** and a public MIT
  binding (playerone-sdk-rs) deliberately does *not* redistribute the binary. The plan's
  **first Player One task is a gating license verification** against the actual SDK
  package's own license/readme: if it permits redistribution, vendor it into
  `vendor/playerone/` with `LICENSE` + provenance `README` (the ZWO pattern); **if it does
  not, STOP and escalate** — do not bundle. Fallback options (first-run checksum-pinned
  download, or require-install via a known-path loader) are pre-identified but chosen by the
  user at escalation time, not assumed here.

## Global constraints

- **Cross-platform clean degradation** — native camera backends are win-x64-only for now;
  absent DLLs or non-Windows platforms must leave discovery, `/api/backends`, and the
  drivers surface working, never raising.
- **`ctypes.CDLL`, not `WinDLL`** — Linux-safe loading; declared `argtypes`/`restype` for
  every called export; export-verification gates DLL selection (the `zwo_sdk.py` precedent).
- **No observatory site coordinates** in code/tests/docs — fictional values only; real
  values read at runtime from config. (Cameras don't touch site location, but the campaign
  rule stands.)
- **Version floors** — `numpy` already a dep; no new runtime deps beyond the vendored DLLs.
- **Framework fidelity** — backends set every manifest field explicitly (runtime_checkable
  Protocol defaults don't apply to duck-typed classes); registered via `register_all()`
  entry points; stable backend `name` (embedded in profiles, must never change).

## Build order (→ implementation plan)

1. **Engine + adapter interface + capability descriptor + registry** — with the
   parametrized contract suite run against a trivial in-memory fake adapter.
2. **ZWO ASI adapter** — proves the waist on a real vendor; bundle `ASICamera2`; unblocks
   native guiding without vendor software.
3. **Player One license verification** (gating) → **Player One adapter** (LRN/HCG/cooling/
   dew heater/ROI); LRN/HCG bias-frame acceptance harness.
4. **Orchestrator `guide_camera`-role generalization** (backward-compatible) so ASI-guide +
   Poseidon-imaging resolve as two backends.
5. **Deploy 0.2.7 to astrotown + at-scope validation** (light frames, cooling, guider first
   light on a clear night).

Each step yields working, independently testable software and its own review gate.
