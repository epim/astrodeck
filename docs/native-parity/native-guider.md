# Native Guider — Architecture & Parity Notes

AstroDeck's native autoguider (`astrodeck` provider) is a from-scratch Rust
engine that ports PHD2's guiding stack — star detection, calibration, the
per-axis correction algorithms, static backlash compensation, multi-star
tracking, and a predictive (Gaussian-process) periodic-error corrector — so an
Alpaca/native/sim rig gets NINA/PHD2-parity guiding with **no NINA and no
PHD2 process required**. It is the **default** guide provider on the
simulator rig and on any real rig where AstroDeck owns the guide camera +
mount (D6); the PHD2 bridge stays selectable per-profile as a fallback, and
NINA rigs keep guiding through NINA/PHD2 unchanged (D5). For the user-facing
how-to (calibration, algorithm picker, provider switch, RMS comparison), see
[docs/guide/guiding.md](../guide/guiding.md).

This doc is the developer-facing companion to the algorithm dossier
[`docs/native-parity/algorithms/phd2-guiding.md`](algorithms/phd2-guiding.md)
(the source-mapped, section-numbered spec every Rust module cites in its file
header per [rust-header-policy.md](rust-header-policy.md)) — it covers the
crate/module map, the Python integration, the phase-by-phase parity coverage,
the CI gates, and — the honesty layer this doc exists for — every item that
shipped v1 without full coverage.

---

## Architecture

```
                          ┌─────────────────────────────┐
                          │   native/crates/astro-guide  │   pure Rust, sync,
                          │   (Apache-2.0)                │   no I/O, no async,
                          │                               │   no globals
                          │  starfind · select · track    │
                          │  transforms · calibration      │
                          │  settle · refine               │
                          │  algorithms/                    │
                          │    hysteresis · lowpass         │
                          │    lowpass2 · resist_switch     │
                          │    z_filter · gaussian_process  │
                          │    (+ gp_math: kernels, FFT,     │
                          │     Cholesky GP inference)        │
                          │                                    │
                          │  GuideEngine::ingest(&FrameMeta,     │
                          │    &[MeasuredStar]) -> Action         │
                          └───────────────┬────────────────────┘
                                          │ PyO3 (astrodeck-native, MPL-2.0
                                          │ wheel crate; astro-guide itself
                                          │ stays Apache-2.0 — see Licensing)
                                          │ GuideEngine.process(frame, ts, exp)
                                          │   -> dict; guide_star_find(...)
                          ┌───────────────▼────────────────────┐
                          │ server/astrodeck/guide/native.py     │  Python owns
                          │ NativeGuider(Guider)                  │  ALL I/O (D1):
                          │                                        │  camera expose,
                          │  expose -> engine.process(...) -> Action │ mount pulse-
                          │  dispatch: Idle/Pulse/PulsePair/CalStep/  │guide, calib
                          │  Settle/LockLost -> mount.pulse_guide()    │persistence
                          │  publishes GuideStats on bus channel "guide"│
                          └───────────────┬───────────────────────────┘
                                          │ same GuideStats shape, same
                                          │ "guide" bus channel PHD2/NINA
                                          │ guiders publish (spec §3.2/§3.5)
                          ┌───────────────▼───────────────────────────┐
                          │ MonitorView / GuideView / SessionsPanel /   │
                          │ Sparkline — work UNCHANGED regardless of     │
                          │ which provider is guiding                    │
                          └───────────────────────────────────────────┘
```

**Provider routing** (`server/astrodeck/providers.py::_resolve_guide`,
registered in `_RESOLVERS["guide"]`): NINA owns guiding on a NINA rig (D5,
never raises); otherwise the native engine runs if a `guide_camera` + a
`telescope` are both connected and the `astrodeck_native` wheel is installed
(badged `astrodeck` on real Alpaca/native hardware, `sim` on the simulator rig
— **the same `NativeGuider` engine either way**, only the UI badge differs);
otherwise it falls back to the PHD2 bridge (`backend`, "PHD2 fallback" — never
raises, matching spec §3.3/§4's "guiding never crashes" contract). An explicit
per-capability override (`ProvidersConfig.guide`: `auto` / `backend` /
`astrodeck` / a driver id) is a real SELECTION input applied at guiding start
(`hub.select_guide_provider`, called from `guide_start`/`guide_calibrate`): it
is honored when a guider of the requested family is actually constructible on
the connected rig, and DEGRADES gracefully to whatever is wired otherwise
(e.g. `backend` on a bridge-less sim rig stays native — pinned by
`test_guide_provider_selection.py`; never a crash). A running guider is never
hot-swapped — a switch takes effect at the NEXT guiding start. The honesty
rule (`_resolve_guide` branch (0)): once a guider is wired, the status badge —
and everything downstream of it, including the UI's per-provider RMS tagging —
reports the ACTUAL serving guider (`Guider.provider_family`), never the
requested override. `providers.guide_eligible_providers` (surfaced as
`status.providers.guide.eligible`) tells the UI which override values apply to
the connected rig, so the dropdown never offers a no-op. The override is
validated at write time (`ConfigStore.set_providers` — unknown values 422) and
rides the same per-profile snapshot mechanism the other three task-provider
overrides do (`Profile.providers`, restored on profile activate/load).

**`guide_camera` is a first-class device role** (D6, `devices/backend.py`
`ROLES`): the dedicated guide camera is any `Camera` device assigned to this
role — no new device class, the existing `Camera` ABC (`expose ->
CameraFrame`) is sufficient at guide cadences (0.5–5 s). It is distinct from
the legacy `guider` role (the PHD2/NINA guiding *connection*, not a camera).

**API surface** (`server/astrodeck/api/app.py`, all `control.guide`-gated
except the read-only preview): `POST /api/guide/{start,stop,dither}`, `POST
/api/guide/calibrate` (force a fresh calibration), `DELETE
/api/guide/calibration` (clear the persisted one), `GET`/`PUT
/api/guide/settings` (per-axis algorithm `GuideConfig`), `GET
/api/guide/frame.png` (`view.preview`-gated guide-star thumbnail). The
provider override rides the pre-existing `POST /api/config/providers`
(`ProvidersConfig`, `config.backend`-gated) — the same route the Equipment
tab's Tasks panel writes.

---

## Full-suite v1 scope (spec §0, USER-DECIDED)

Hysteresis + Lowpass (RA), Resist-Switch (Dec), Z-Filter, calibration,
dither+settle, star-lost recovery, **multi-star tracking**, and the
predictive PEC (Gaussian-process, **PPEC**) algorithm — all shipped and
parity-reviewed (phase table below). Pulse-guide-only correction output (D2)
and static-only backlash compensation (D4) are the two deliberately narrowed
scopes; see [Known deferred items](#known-deferred-items).

## Phase-by-phase parity coverage

| Phase | Scope | Acceptance gate | CI wiring |
|-------|-------|------------------|-----------|
| **P0** | Licensing infra (THIRD-PARTY-NOTICES, header policy), crate skeleton + naming, `NinaGuider`/backend probing groundwork | crate builds + smoke test | `cargo fmt`/`clippy`/`test` in the `native` CI job |
| **P1** | Star find/select/track/transforms, guide algorithms (Hysteresis, ResistSwitch), calibration state machine, `GuideEngine` + move/pulse limiting, PyO3 surface, closed-loop sim coupling, `NativeGuider` | **P1 gate**: closed-loop sim converges — RMS settles to ~0.31 px steady state (peak 0.44 px early) against a 2.0 px threshold; calibration 24.2 s, valid + orthogonal | `test_native_guider_e2e.py` in the `native` job |
| **P2** | Dither + settle (guides *during* settle, not a blind pause), star-lost recovery + fast reuse-based restart (<15 s), calibration persistence (read + write, pier/scale/binning-compatibility gated), provider resolution + DEFAULT FLIP (native engine default on sim rigs), `guide_camera` role, GuideView calibration/settings/reticle/provider badge | **P2 gate** (P2-T2, 3/3): dither+settle, star-lost recovery, meridian-flip calibration-flip contract, calibration persistence round-trip — all on the sim | `test_native_guide_surface.py`, `test_resolve_guide.py`, recovery/regression suites in `server/tests` |
| **P3** | Multi-star tracking (auto-transition fixed so it's alive in the default calibrate→guide flow, not just when manually invoked), Lowpass2 + Z-Filter (mkfilter-derived IIR coefficients, numerically verified to 1e-15 against an independent reimplementation) | **P3 gate**: sim converges with multi-star tracking active and Lowpass2 (Dec)/Z-Filter (RA) as the axis algorithms — 6.71 s | targeted Rust + Python suites in the `native` job |
| **P4** | Gaussian-Process **PPEC** (predictive periodic-error correction: FFT period identification, GP regression with a Cholesky-factored regularized kernel, warm-up-gated blend with the reactive Hysteresis fallback), static backlash compensation (BLC) | **P4 gate**: PPEC beats Hysteresis on strong injected periodic error — `rms_ra(ppec) = 0.310 px` vs `rms_ra(hysteresis) = 0.678 px` (ratio 0.457, well under an 0.8 acceptance bar), 4.0 px injected PE amplitude, dither-free, ~45 s wall clock, deterministic (synthesized engine clock, not wall-clock — see [Known deferred items](#known-deferred-items)) | `test_native_guider_ppec.py` in the `native` job |
| **P5** (this doc + GuideView provider switch + same-night RMS compare) | User + developer docs, PHD2-fallback provider switch without leaving the Guide view, same-night head-to-head RMS comparison (`ui/src/lib/rmsCompare.ts`) | — (docs/UX, no new gate) | — |

Every phase was implementer-then-independent-reviewer parity-checked against
the dossier (opus/sonnet review passes citing exact upstream `phd2/src/*.cpp`
line ranges per finding); several review rounds caught and fixed genuine
parity bugs before merge (documented in each phase's commit history) rather
than shipping first-draft ports. At P4 completion: **164 Rust tests**
(`cargo test -p astro-guide`), the server suite (`server/tests`) exercising
the Python integration, provider resolution, and both CI-gated e2e scenarios.

**CI wiring**: the `native` job in `.github/workflows/ci.yml` builds the
`astrodeck_native` wheel (`maturin build --release`) and runs it against the
real compiled engine — critically, this means a Rust regression that breaks
convergence or the PPEC advantage fails CI, not just a local smoke test. Both
phase gates run as real pytest files in that job (`test_native_guider_e2e.py`,
`test_native_guider_ppec.py`), alongside `test_native_module.py`,
`test_autofocus_native.py`, `test_polar_native.py`, and
`test_native_guide_surface.py`.

---

## Known deferred items

These are the program's honest gaps — read before trusting native guiding in
a scenario near one of these edges:

- **PPEC dither-compensation wiring.** The engine currently **resets** the
  learned GP model on every dither instead of compensating for the gear-time
  gap the way upstream PHD2 does (`GuidingDithered`, dossier §6.8.6) — at real
  guiding cadence with dithering enabled, this can hold PPEC in its reactive
  Hysteresis-blend fallback indefinitely instead of ever reaching the trained
  prediction. This is a **named prerequisite before PPEC becomes a sim/real
  default** (it currently requires an explicit RA-algorithm pick). Bundled
  with the same follow-up: **settle-window dead-reckoning ownership** (which
  layer advances the smoothed-distance estimate during a settle dwell isn't
  fully settled) and the **wall-clock vs. exposure-synthesized engine clock**
  question (the P4 gate deliberately drives the GP on a synthesized
  per-frame clock, not `timestamp_s` wall-clock time, to keep the gate
  deterministic — a real-cadence period-compression subtlety needs resolving
  before that assumption generalizes to on-sky use).
- **Cross-session GP model retention (dossier §6.8.6) is not wired.** Upstream
  PHD2 retains a trained PPEC model across a guiding stop/start (subject to
  pier-side/worm-offset compatibility checks) instead of retraining from
  scratch; AstroDeck's engine does not persist the GP model today, so every
  guiding session (re)learns periodic error from zero.
- **Real-rig `image_scale_arcsec` needs an `Optics.guide_focal_length_mm`
  config field.** This is an **arcsec-badge/reporting units-only** gap —
  guiding itself corrects in pixels and is unaffected — but the RMS numbers
  shown in arcsec on a real (non-sim) rig are mis-scaled until this field
  exists; documented at the construction site
  (`devices/backends/native_backend.py::native_guider()`) rather than silently
  hardcoded.
- **Sim pier-flip modeling + a hub-driven meridian-flip e2e test are required
  before trusting an on-sky meridian flip.** The P2 calibration-flip gate
  proves the *contract* (the guider's `flip_calibration` method is called
  correctly and swaps axis signs as expected) but not *reconvergence through a
  physically modeled pier flip* — the sim's pier side is currently hardcoded,
  so there is no closed-loop test of "flip happens on-sky, guiding
  reconverges." Do not assume flip behavior is proven beyond the contract
  level.
- **D2 — ST-4 (guide-camera-relay) output is deferred.** v1 correction output
  is mount pulse-guide only (Alpaca `PulseGuide`, sim nudge); the engine emits
  an abstract `Action` either way, so an ST-4 backend is an additive future
  dispatch target, not a rework.
- **D4 — adaptive backlash compensation is off.** Only **static** BLC ships
  (a fixed seed pulse on a Dec direction reversal, `blc_pulse_ms`); the
  adaptive controller that measures and auto-tunes the pulse (dossier §10.2)
  stays deferred until a backlash-measurement tool exists.
- **D5 — NINA rigs keep guiding through NINA/PHD2, unchanged.** Mixing
  AstroDeck-native guiding with a NINA-owned imaging rig is explicitly out of
  scope; a NINA rig's `guider` role is always the NINA/PHD2 bridge regardless
  of the `ProvidersConfig.guide` override (see `_resolve_guide`'s `if nina:`
  branch, which wins before any override is consulted).

---

## Licensing posture

AstroDeck's project license is **Apache-2.0** (root `LICENSE`, ratified
2026-07-17 — see [rust-header-policy.md](rust-header-policy.md)'s amendment
note). `native/crates/astro-guide` is licensed Apache-2.0 as a **per-crate
override** of the `native/` workspace's default (the pre-existing
`astro-star`/`astro-focus`/`astro-tppa`/`astrodeck-native` crates stay
MPL-2.0, matching their NINA/Hocus-Focus/TPPA provenance — untouched by this
program). Because `astro-guide` is a clean-room Rust port of PHD2's expressed
guiding logic, every file that derives from PHD2 additionally carries a BSD-3
derivation note pointing at the specific upstream `phd2/src/*.cpp` file/lines
and the dossier `§` it was ported from (BSD-3-Clause permits sublicensing
under Apache-2.0, so this is compatible). The Gaussian-Process/PPEC module is
itself a derivative of PHD2's GP guider, which upstream attributes to the
**Max Planck Society** (BSD-3-Clause) — see the citation and copyright holder
table in **`THIRD-PARTY-NOTICES.md`** (root), which carries the full
BSD-3-Clause text, the PHD2 contributor copyright table (per-algorithm
attribution — hysteresis/resist-switch/multi-star to Bret McKee, Z-filter to
Ken Self, multi-star extensions to Bruce Waddington, GP/PPEC to the Max Planck
Society with the Klenske et al. paper citation), and the MPL-2.0 statement for
the rest of `native/`.

---

## Related

- [docs/guide/guiding.md](../guide/guiding.md) — user-facing how-to (Control,
  Guide Algorithm, Provider switch, same-night RMS comparison).
- [docs/native-parity/algorithms/phd2-guiding.md](algorithms/phd2-guiding.md)
  — the source-mapped algorithm dossier every module header cites.
- [docs/native-parity/rust-header-policy.md](rust-header-policy.md) — the
  exact license header text per crate.
- [docs/infrastructure/native-backend.md](../infrastructure/native-backend.md)
  — the native Alpaca backend (camera/mount/focuser/etc.), the sibling
  no-NINA path this guider rides on top of.
