# AstroDeck Native Parity — Architecture Design

**Date:** 2026-07-03 · **Status:** approved-direction (user directive: design → spec → subagent implementation → review)
**Goal:** NINA-feature parity **without requiring NINA**, implemented in Rust as the seed of the eventual Rust port, routed per-rig so every backend uses its own best tooling.

---

## 1. The routing principle (why "providers")

AstroDeck drives three classes of rig, and each should use its native strength:

| Rig | Autofocus | Polar align | Guiding | Plate solve |
|---|---|---|---|---|
| **NINA bridge** (transition) | NINA's own AF (delegated) | NINA TPPA plugin (delegated) | PHD2 | ASTAP (ours) |
| **AstroDeck native** (Alpaca/sim) | **Rust engine (this project)** | **Rust TPPA (this project)** | PHD2 now, native later (W4) | ASTAP (ours) |
| **ASIAIR** (future backend) | ASIAIR native tooling | ASIAIR native tooling | ASIAIR | ASIAIR |

A *capability provider* is the answer to "who performs capability X for this rig?" — resolved per capability, not per rig, so a NINA-bridged mount can still use our native autofocus if the user prefers it.

## 2. Existing seams (ground truth, from code)

These are the exact attachment points; the design keeps all three and formalizes the resolution above them:

1. **Autofocus** — `focus/autofocus.py:32` checks `focuser.supports_native_autofocus` → delegates to the backend (`NinaFocuser.native_autofocus`, `devices/nina.py:476`); otherwise runs the local numpy V-curve (`autofocus.py:35–127`) with `imaging/stars.py` HFR (flux-weighted-radius proxy, not true HFR).
2. **Polar** — `polar/session.py:53`: `if hub.nina_client → _run_nina()` (drives NINA's TPPA over `ws://…/v2/tppa`) `else _run_sim()` (fake). **No native math exists.**
3. **Guiding** — `hub.guider: Guider` ABC slot (`guide/base.py:17`); PHD2Guider and SimGuider today; a native guider is a drop-in later (out of scope here, W4).
4. **Solve** — `solve/get_solver()`: ASTAP CLI else guarded SimSolver. The orchestrator's `native_solver()` seam exists but is unwired (all backends return None) — this design leaves ASTAP as the solver and treats a native solver as future work.

## 3. Provider resolution layer (small, Python)

New module `server/astrodeck/providers.py`:

```python
Capability = Literal["autofocus", "polar_align", "guide", "solve"]
ProviderKind = Literal["auto", "backend", "astrodeck"]   # per-capability setting

@dataclass
class ProviderChoice:
    kind: Literal["backend", "astrodeck"]   # resolved
    label: str          # "NINA", "AstroDeck native", "ASIAIR" — for UI badges
    reason: str         # why resolution picked it (surfaced in Settings)

def resolve(cap: Capability, hub) -> ProviderChoice
```

Resolution policy (per capability):
1. Explicit profile/config override (`providers.autofocus = "backend" | "astrodeck"`), default `"auto"`.
2. `"auto"`: use the **backend's** implementation when the connected role advertises one (`supports_native_autofocus`, NINA TPPA availability, future `supports_native_polar` on an ASIAIR backend); otherwise **astrodeck** (Rust) when its prerequisites are connected (camera+focuser for AF; camera+mount+solver for TPPA); otherwise a clear `DeviceError` naming the missing piece.
3. Resolution result is surfaced in `poll_status()` as `providers: {autofocus: {label, kind}, polar_align: {...}}` so the UI can badge every panel ("AF · NINA", "TPPA · native").

Call-site changes are minimal: `run_autofocus` and `PolarAlignSession.start` consult `resolve()` instead of their current hard-coded checks. `ProviderKind` config lives in `UpdateConfig`-style pydantic config (`config.py`) + per-profile override in `profiles.py`.

## 4. The Rust workspace (`native/` at repo root)

The algorithm core in pure Rust — no I/O, no async, deterministic, exhaustively unit-tested — bridged to today's Python server via PyO3, reusable verbatim by the future Rust server.

```
native/
  Cargo.toml                 # workspace
  crates/
    astro-star/              # star detection + HFR + PSF (NINA + Hocus Focus parity)
    astro-focus/             # AF sweep state machine + curve fitting (NINA parity)
    astro-tppa/              # three-point polar alignment math (TPPA parity)
    astrodeck-native/        # PyO3 cdylib bundling the three (maturin)
```

### 4.1 `astro-star`
- Background: median + MAD sigma; optional iterative kappa-sigma clipping (Hocus Focus mode).
- Detection: NINA-style candidate pipeline (per dossier `nina-star-detection-hfr.md`) with HF's noise model as the default profile; sensitivity presets mapped from NINA's Normal/High/Highest.
- Measurement: flux-weighted centroid; **true cumulative half-flux radius** (replaces the Python proxy); FWHM via Gaussian **and** Moffat PSF fit (Levenberg-Marquardt on `nalgebra`, exact objective per dossier `hocusfocus-star-detection-psf.md`); eccentricity.
- Output: `Vec<Star>` + `FrameStats { hfr_median, hfr_mad, star_count, fwhm_mean, ecc_mean }`.
- `rayon` for per-star parallel fitting.

### 4.2 `astro-focus`
- **Sweep as a state machine** (policy in Rust, device I/O stays in the host):
  `FocusSweep::new(cfg, start_pos) → propose() -> Step { MoveTo(pos) | Done(FitOutcome) | Failed(reason) }`, `add_measurement(pos, hfr, weight, star_count)`. Encodes NINA's sweep policy: initial offset, step size, backlash strategy (Absolute IN/OUT vs Overshoot), extend-sweep-when-minimum-not-bracketed, outlier re-measure, max points.
- Fits (per dossier `nina-autofocus.md`): trendlines (left/right robust linear + intersection), parabola, **hyperbola** (NINA default), Gaussian; combined TREND+HYPERBOLIC / TREND+PARABOLIC selection; weighted by per-point HFR σ; R² per fit; final-point selection rules identical to NINA.
- Output `FitOutcome { method, best_position, best_hfr, r2, curve: Vec<(f64,f64)> per fit for UI rendering, trendlines }`.

### 4.3 `astro-tppa`
- Input: 3 plate solves `(ra, dec, t)` + site `(lat, lon)` (+ pressure/temp for optional refraction) per dossier `tppa-polar-alignment.md`.
- Axis determination: the three solved points lie on a circle about the mount's RA axis on the celestial sphere — plane-fit → axis unit vector → convert to (alt, az); polar error = axis vs refracted pole; decompose into **altitude** and **azimuth** knob corrections with TPPA's sign conventions (E/W, up/down), southern-hemisphere handling, near-pole guards.
- **Continuous phase:** given the locked axis model + each subsequent solve, recompute the live error as the user turns knobs (the exact TPPA update math).
- Output `PolarError { az_arcmin, alt_arcmin, total_arcmin, directions }` + `TppaModel` (serializable).

### 4.4 `astrodeck-native` (PyO3)
Exposes: `detect_and_measure(buf: &[u16], w, h, params) -> (stars, stats)`, `FocusSweep` class, `fit_focus_curve(points, method)`, `tppa_from_three(...)`, `tppa_update(model, solve)`. Built with maturin; abi3 wheels (cp311+) for win-x64 + linux-x64.

## 5. Python integration

- `focus/native.py`: `run_native_autofocus(camera, focuser, cfg)` — drives the `FocusSweep` loop (move → expose → Rust detect/measure → add), publishes the same `focus` bus events (now with `fit: {method, r2, curve, trendlines}` extras). `run_autofocus` routes: provider=backend → existing delegation; provider=astrodeck → this; module missing → legacy numpy fallback + one-time warning.
- `polar/native.py`: `_run_native()` third branch in `PolarAlignSession`: capture → ASTAP solve → mount RA rotate (configurable ~10–15°, safety-gated: no slew through sun cone, respects pause/stop) ×3 → `tppa_from_three` → then continuous loop (capture/solve/`tppa_update`) publishing the same `polar` payload (already arcmin) with `source:"native"` + extra `phase: measuring|adjusting`, `point_index`.
- Sequencer parity add-on (Python, small): HFR-increase refocus trigger (`autofocus_on_hfr_increase_pct`, NINA's AutofocusAfterHFRIncrease) alongside the existing every-N-frames / temp-delta triggers, using per-frame HFR already measured.
- **Graceful degradation:** `astrodeck_native` import guarded; without the wheel, AF falls back to legacy numpy and native TPPA reports a clear "native engine not installed" error. All existing tests stay green without Rust.

## 6. Build/CI/release

- `native/` builds in CI (cargo test + clippy) on win+linux; maturin builds the wheel; release artifacts bundle the platform wheel so the self-update pipeline ships it (install step: wheel unpacked into the release venv/site dir — same signed-artifact flow as today).
- Dev: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml` into `server/.venv`.

## 7. UI (built against docs/ui-rebuild/*)

- **Focus view**: render the real fit — measured points with error bars (per-point σ/star count), fitted hyperbola curve + trendline cross, chosen minimum marker, R² + method chip, provider badge (AF · native/NINA), verdict-first copy ("Focus: excellent — HFR 1.82″, R² 0.997"). History strip of past AF runs (position vs temperature trend).
- **Align view**: native TPPA wizard — Phase 1 "Measure" (3 capture/solve tiles with progress + failure retry), Phase 2 "Adjust" (hero total-error arcmin, az/alt rows with knob-direction arrows exactly as today, live convergence sparkline, tiered verdicts: >10′ "keep going" / 2–10′ "good" / <2′ "excellent — stop here"). Pause/resume/stop preserved; Tier-2 sticky on error per doc 04.
- **Provider surfacing**: Settings → Connect gains a "Capabilities" card showing resolve() results + override dropdowns (auto/backend/astrodeck), gated `config.backend`.
- Everything respects: night mode, attention tiers (doc 03 §5), failure-routing rules (doc 04 §1), verdict+raw-number pattern (doc 04 §6).

## 8. Testing

- **Rust:** synthetic star fields (Gaussian/Moffat stars at known positions/HFR + Poisson noise) → detection recall/precision + HFR accuracy bounds; curve fits recover known minima under noise (property tests); TPPA: forward-model a mount with known (az,alt) misalignment → generate 3 solves → assert recovered error < 0.1′; sign-convention tests for all 4 hemisphere/pier combos.
- **Python:** provider resolution matrix tests; native AF loop against `SimCamera` (extend sim to render focus-dependent star HFR — enables true end-to-end AF test); native TPPA session against a misaligned sim mount model; fallback-without-wheel tests.
- **CI:** cargo test + pytest on win/linux; wheel smoke-import.

## 9. Licensing & provenance

NINA, Hocus Focus, TPPA are MPL-2.0; PHD2 is BSD-3. Algorithms are reimplemented from extracted dossiers (`docs/native-parity/algorithms/`) with per-algorithm source maps. Rust crates carry `MPL-2.0` license headers where derived, with attribution in `native/README.md` and each dossier's source-map section.

## 10. Out of scope (explicitly)

Native in-process guider (W4 — PHD2 remains), native blind plate solver (ASTAP remains), ASIAIR backend itself (the provider enum reserves its slot), full Rust server port (these crates are its seed).
