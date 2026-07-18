# Native Guider (in-house PHD2 replacement) — Design

Status: DRAFT pending user ratification (user away; design decisions recorded in §0).
Algorithm authority: `docs/native-parity/algorithms/phd2-guiding.md` (the 1687-line
source-mapped PHD2 dossier, "the dossier" below). This spec is the SYSTEM spec — it
cites dossier sections instead of restating algorithm math.
Licensing evidence: `.superpowers/sdd/guider-licensing-report.md` (verified upstream).

## 0. Decisions

USER-DECIDED (2026-07-17, verbatim intent):
- Native-first, PHD2 fallback: the native guider becomes the default provider; the
  existing `guide/phd2.py` bridge stays selectable per-profile during maturing, then
  retires (the NINA pattern).
- v1 scope: FULL SUITE — hysteresis + lowpass (RA), resist-switch (Dec), Z-filter,
  calibration, dither+settle, star-lost recovery, MULTI-STAR tracking, and the
  predictive PEC (Gaussian-process) algorithm.
- Mission frame: one open-source package (NINA+PHD2+everything), Windows/Linux/Mac,
  one install for a newcomer and for an advanced observatory alike. No external
  processes; no GUI-toolkit dependencies in engine code.

CONTROLLER-DECIDED (recorded for ratification; overridable without rework debt):
- D1. Engine architecture = Approach A below (pure-Rust engine, Python I/O loop).
- D2. Correction output v1 = mount pulse-guide only (Alpaca `PulseGuide`, sim nudge).
  ST-4-through-guide-camera output is deferred (needs per-camera SDK work that
  contradicts nothing here; the engine emits abstract corrections either way).
- D3. The dossier §17 recommended deferring PPEC/Z-filter; the user's full-suite
  ruling overrides it. The PLAN still sequences core-first (P1-P3 guide real skies
  before P4 lands PPEC) so there is always a shippable milestone.
- D4. Backlash compensation: static BLC ships in v1 (dossier §7); the ADAPTIVE BLC
  controller stays off/deferred until a backlash-measurement tool exists (dossier
  §17 advisory stands — measuring tools are a v2 project).
- D5. NINA-mode rigs keep guiding through `NinaGuider`/PHD2 unchanged — the native
  guider targets Alpaca/native/sim rigs where AstroDeck owns the devices. (A NINA
  rig lets NINA own its guider; mixing AstroDeck-native guiding with NINA-owned
  imaging is out of scope.)
- D6. Guide camera = any `Camera` device assigned to a new `guide_camera` RigSpec
  role (Alpaca camera N, sim guide cam). No new device class; the existing `Camera`
  ABC contract (expose → CameraFrame) is sufficient at guide cadences (0.5-5s).
RESOLVED USER DECISIONS:
- AstroDeck project license = **Apache-2.0** (user-decided 2026-07-17; root LICENSE
  committed). Consequence for this project: the `astro-guide` crate carries
  **Apache-2.0** file headers (with BSD-3 derivation notes naming the PHD2 source),
  NOT MPL-2.0 — BSD-3 permits Apache sublicensing. MPL-2.0 remains ONLY on the
  files genuinely derived from MPL sources (NINA/Hocus-Focus ports in the existing
  crates); the workspace license field becomes per-crate. THIRD-PARTY-NOTICES
  obligations unchanged.

## 1. Goals / non-goals

GOALS: guide an Alpaca/native/sim rig unattended all night with RMS comparable to
PHD2 defaults on the same rig; calibrate, dither+settle under the sequence engine's
existing contract, survive star-lost, meridian-flip via calibration flip, persist
calibration across restarts; full algorithm suite per §0; zero new processes; all
platforms; PHD2 selectable fallback untouched.

NON-GOALS (v1): ST-4 output (D2); adaptive BLC (D4); NINA-rig native guiding (D5);
guiding assistant/backlash-measurement tooling; AO (adaptive optics) devices;
comet/planetary tracking; PHD2's server API emulation (nothing external speaks to
our guider).

## 2. Approaches considered

- A (CHOSEN): new pure `astro-guide` Rust crate + Python `guide/native.py` loop.
  Exactly the proven `astro-focus`/`focus/native.py` shape: the crate is a
  synchronous, I/O-free state machine ingesting frames' measured offsets and
  emitting corrections; Python owns camera exposure, mount pulses, asyncio timing,
  bus events, cancellation. Pros: testable pure (golden vectors from the dossier),
  cross-platform via existing maturin wheel, degrades cleanly when the wheel is
  absent (NATIVE_AVAILABLE idiom), zero new I/O machinery. Cons: Python loop adds
  ~ms-scale jitter — irrelevant at 1-5s guide cadence.
- B (rejected): whole loop in Rust (Rust talks Alpaca HTTP + owns scheduling).
  Duplicates the Python device layer, breaks the established ownership pattern,
  complicates cancellation/teardown that the sequence engine already exercises
  through Python guiders.
- C (rejected by user posture decision): embed/bundle the PHD2 process.

## 3. Components

### 3.1 `native/crates/astro-guide` (new, pure; Apache-2.0 headers + BSD-3 derivation notes — see §0 license resolution)
Modules mirror the dossier's sections; each cites its dossier § + PHD2 source map:
- `starfind.rs` — PHD2 `Star::Find` parity (annulus background, Simonetti SNR, HFD;
  dossier §1). Parallel to, not shared with, astro-star's NINA-parity detector;
  reuses `astro-star` low-level primitives (GrayFrame/Rect/stats) via dependency.
- `select.rs` — auto-select + multi-star secondary set management (dossier §2).
- `track.rs` — per-frame tracking, jump rejection, multi-star median offset,
  RefineOffset (dossier §3-4).
- `transforms.rs` — camera↔mount axes transforms (dossier §5).
- `algorithms/` — `hysteresis.rs`, `lowpass.rs`, `resist_switch.rs`, `zfilter.rs`,
  `gaussian_process.rs` (PPEC port from `contributions/MPI_IS_gaussian_process`,
  BSD-3 Max Planck Society, verified; linear algebra via `nalgebra` — Eigen does
  not travel; dossier §6). One trait `GuideAlgorithm { step(offset)->correction,
  reset(), params }`, per-axis instances.
- `calibration.rs` — calibration state machine + quality advisories, pier-side
  flip math (dossier §8-10).
- `engine.rs` — `GuideEngine`: composes the above; API shape follows `FocusSweep`:
  constructed with config; `begin_calibration()/begin_guiding()`; per frame
  `ingest(frame_meta, measured_stars) -> Action` where Action ∈ {Pulse{axis,dir,ms},
  PulsePair, Settle, LockLost, CalStep{...}, Idle}; `dither(dx,dy)`; `stats()`
  (RMS windows matching `GuideStats`); `flip_calibration()`; serializable
  calibration state (for persistence).
- `settle.rs` — settle detector (dossier §12) evaluated inside the engine.
- PyO3 surface (in `astrodeck-native`): `GuideEngine` class + `guide_star_find`
  (frame → star candidates) mirroring `detect_and_measure`'s calling convention.

### 3.2 `server/astrodeck/guide/native.py` — `NativeGuider(Guider)`
Owns the loop (the `focus/native.py` pattern): exposure loop on the guide camera →
`guide_star_find` → `engine.ingest` → `telescope.pulse_guide(...)` per Action →
`bus.publish("guide", ...)` with the SAME GuideStats shape (UI works unchanged).
Implements the full `Guider` ABC incl. `dither`, `stats`, `flip_calibration`
(`can_flip_calibration = True`), `guide_frame` (last guide frame → PNG thumbnail,
existing endpoint). Cancellation/teardown honors the sequence engine's
`GUIDE_OP_TIMEOUT_S` contract. Star-lost → bounded reacquire (engine LockLost →
auto-reselect attempt) before reporting inactive, so `_maybe_recover_guiding`
keeps its existing semantics. Calibration persistence: engine state serialized to
`CONFIG_DIR/guider/<profile-id>.json` (pier side, declination, rates, angles);
on start, reuse if compatible per dossier §8 rules else recalibrate. Crash
recovery = same path (resume-arm needs no changes; guiding restarts via
`plan.guide` exactly as today).

### 3.3 Provider + backend wiring
- `providers.py`: add `"guide"` to `Capability` + `ProvidersConfig.guide:
  str = "auto"` + `_resolve_guide` (auto → `astrodeck` when guide_camera+mount
  connected and wheel present; `backend` → PHD2/NINA guider; `sim` on sim rigs).
  Per-profile override via existing `Profile.providers`.
- `devices/backends/native_backend.py`: add `"guider"` and `"guide_camera"` roles
  (guider fulfilled by `NativeGuider` bound to the assigned guide camera + mount).
  `backend.py` ROLES gains `"guide_camera"`. phd2_backend/sim_backend unchanged
  (sim gains the closed-loop camera below).
- `devices/alpaca.py` mount: add `CanPulseGuide` probe + `GuideRateRightAscension/
  Declination` reads (calibration uses actual rates when available; advisories per
  dossier §17 otherwise).

### 3.4 Closed-loop simulator (REQUIRED for testing without sky)
- `SimGuideCamera`: renders a synthetic star field whose positions respond to
  `SimTelescope` pointing + injected disturbances (drift rate, periodic error
  ~worm period, seeing jitter, optional wind gusts, configurable via device
  `extra`). `SimTelescope.pulse_guide` moves the model; the camera sees it.
- `SimGuider` stays (fallback), but sim rigs default to NativeGuider-over-
  SimGuideCamera → true end-to-end: calibrate on the sim, guide, assert RMS
  converges below threshold, dither settles, star-lost on injected cloud
  recovers. This is the e2e acceptance harness for every algorithm.

### 3.5 UI (minimal delta — the guide surface already exists)
- GuideView: add calibration state/action (Calibrate button + progress states,
  clear-calibration), algorithm settings drawer (per-axis algorithm pick + the
  dossier §15 parameter set with PHD2 defaults), lock-star overlay markers on the
  existing frame preview (multi-star: primary + secondaries), provider indicator.
- Settings → providers panel: guide slot appears alongside autofocus (existing
  idiom). EquipmentView: guide_camera role row via existing generic role UI.
- No changes to MonitorView/Sparkline/SessionsPanel — same `guide` bus channel.

### 3.6 Licensing infrastructure (new, repo-root)
- `THIRD-PARTY-NOTICES.md`: full BSD-3-Clause text + per-component copyright table
  (PHD2 main-tree: Open PHD Guiding development team, Stark, McKee, Self,
  Waddington; GP/PPEC: Max Planck Society (Klenske/Wenninger/Enficiaud) + paper
  citation Klenske et al., IEEE TCST 24(1):110-121, 2016); MPL-2.0 statement for
  `native/` crates; NINA/Hocus-Focus attribution moved in from prose. Derived
  Rust files carry header comments naming their PHD2 source file + dossier §.
- Root `LICENSE`: BLOCKED on the queued user decision (§0) — the notices file is
  not.

## 4. Error handling
Engine is pure → all failures surface in Python: camera exposure failure (retry
with backoff, N consecutive → guider inactive + bus warning), pulse rejection
(CanPulseGuide false → refuse to start with actionable error), calibration failure
(bounded steps per dossier §8; report reason; PHD2-parity advisories), star lost
(reacquire → recover contract above), wheel absent (provider resolution refuses
`astrodeck`, falls to `backend`/PHD2 — never a crash), settle timeout (same
failure shape the sequence engine already handles from PHD2). All bus logs, no
new alert types.

## 5. Testing
- Rust: unit suites per module with golden vectors extracted from the dossier's
  worked examples (star-find fixtures, hysteresis/resist-switch step responses,
  calibration geometry, GP regression against the paper's synthetic PE). Bit-parity
  asserted wherever the dossier specifies exact PHD2 behavior.
- Python: provider resolution matrix; NativeGuider loop with a scripted fake
  engine (Action sequences → pulse calls asserted); persistence round-trip;
  RBAC on unchanged endpoints.
- E2E: closed-loop sim scenarios (converge, dither+settle, star-lost recovery,
  meridian flip, PPEC beats hysteresis on strong injected PE) — these are the
  acceptance gates per phase.
- UI: pure-logic tests for any extracted settings/state helpers (existing idiom).

## 6. Phasing (one spec; the plan sequences shippable milestones)
- P0: licensing infra (notices file, headers policy) + `astro-guide` skeleton +
  mount gap fixes (CanPulseGuide/guide rates).
- P1: star-find/track/transforms/calibration + hysteresis/resist-switch +
  NativeGuider loop + closed-loop sim → FIRST GUIDABLE MILESTONE (sim e2e green).
- P2: dither/settle, star-lost recovery, flip_calibration, calibration
  persistence, provider/backends/UI wiring → sequence-engine parity on sim rig.
- P3: multi-star tracking; lowpass; Z-filter.
- P4: GP/PPEC port + e2e PE scenario; static BLC.
- P5: docs (guide.md updates, provider docs), PHD2-fallback selection polish,
  head-to-head RMS comparison affordance (same-night provider switch).
