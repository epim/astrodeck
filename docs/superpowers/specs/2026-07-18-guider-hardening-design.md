# Guider Hardening (Parity Wave, Sub-Project A) — Design

Status: ratified by user 2026-07-18 ("hit it skippy") after design presentation.
Campaign context: first sub-project of the close-all-gaps parity wave (A guider
hardening → B flats → C dome → D docs audit → E advanced sequencer). Builds
directly on the completed native-guider program (all 21 tasks,
`docs/superpowers/specs/2026-07-17-native-guider-design.md`, final review
`.superpowers/sdd/guider/final-branch-review.md`).

## §0 Purpose

Close the five ledgered guider follow-ups so the native guider can be trusted
UNATTENDED on real hardware. Every item here originates from a named review
finding; three arrive with reviewer-specified designs that are adopted verbatim.

Origin map (binding — cite in implementations):
- A1 ← final-branch-review I2 (spec §4 under-implemented: first camera hiccup
  fatal; ranked tonight-risk #1).
- A2 ← P4-T1 review concern rulings A + C + finding M6 (bundle mandated by the
  reviewer; `dither_notify` hook design is the reviewer's own minimal wiring).
- A3 ← final-branch-review tonight-risk #5 (pre-existing `AlpacaCamera.expose`
  imageready poll has no overall timeout).
- A4 ← P2-T3 review F2 (no principled `image_scale_arcsec` source on real rigs;
  fix-round implementer's proposed ledger item adopted).
- A5 ← P4-T1 review concern ruling B (§6.8.6 GuidingStarted retention, deferred
  as out-of-brief then; in-scope now).

## §1 Goals / Non-Goals

Goals:
1. A transient guide-camera exposure fault mid-guiding or mid-calibration is
   absorbed without human intervention (bounded retry), and a persistent fault
   dies loudly through the existing honest-death path (bus error, guiding=False).
2. PPEC survives dithers (gear-time compensation, not model reset), uses
   wall-clock gear time, and retains its trained window across sessions.
3. A responsive-but-stuck Alpaca camera cannot hang the guide loop.
4. Real rigs get true arcsec guide statistics when the user supplies the guide
   optics focal length.

Non-goals (explicit):
- PPEC does NOT become a default algorithm in this sub-project. Default-on is a
  separate user decision requiring on-sky evidence, after A2+A5 land.
- No adaptive BLC (D4 stands), no ST-4 (D2 stands), no NINA-rig native guiding
  (D5 stands).
- No sim pier-flip modeling / meridian-flip e2e (separate ledgered item; not in
  this wave's A scope — candidate for a later directive).

## §2 Existing seams (all landed, verified this session)

- `server/astrodeck/guide/native.py` — NativeGuider asyncio loop; owns expose
  calls in calibration (`_CAL_TIMEOUT_S` deadline region, ~:329) and guiding
  (~:414); `_build_engine_config` allowlist; honest-death = bus error +
  `guiding=False` + `is_active` False (final review verified NOT silent).
- `native/crates/astro-guide/src/algorithms/mod.rs` — `GuideAlgorithm` trait
  with defaulted `result_with(input, snr, dt)` (:47); the hook pattern A2 copies.
- `native/crates/astro-guide/src/algorithms/gaussian_process.rs` — GP guider;
  synthesized clock `set_timestamp` (:282-292); blend gate (:462-466); FFT gate
  (:362-364); `guiding_dithered`/`guiding_dither_settle_done` methods EXIST and
  are unit-covered but engine-unwired (P4-T1 concern A).
- `native/crates/astro-guide/src/engine.rs` — dither currently calls
  `ra_algo.reset()`; calibration owns the RA rate A2's hook must pass.
- `server/astrodeck/devices/alpaca.py` — `AlpacaCamera.expose()` imageready
  poll (A3 target).
- `server/astrodeck/config.py` — `Optics` model (main imaging train; A4 adds
  the guide-scope field). Guide camera `pixel_size_um` exists on camera config.
- Calibration persistence — `CONFIG_DIR/guider/<profile-id>.json` + image-scale
  sidecar (P2-T2); A5 mirrors this keying for the GP window.
- Sim: `SimGuideCamera` (`devices/sim.py`) — A1 adds the fault-injection knob.
- Upstream authority: `references/phd2/contributions/MPI_IS_gaussian_process/`
  (`gaussian_process_guider.cpp` GuidingDithered/GuidingStarted paths) +
  dossier `docs/native-parity/algorithms/phd2-guiding.md` §6.8.3/§6.8.6.

## §3 Design

### A1 — Exposure retry/backoff (Python only)
- New retry envelope in NativeGuider around every guide/cal exposure: on
  `DeviceError`/timeout, retry up to 3 times with 0.5 s / 1 s / 2 s backoff.
  A frame that exhausts retries counts as ONE lost frame (feeds the existing
  star-lost machinery, 8-frame budget unchanged).
- Consecutive-exhausted-frame budget: 5. Exceeding it stops the loop via the
  existing honest-death path (bus error names the device fault, guiding=False).
- Calibration path: same envelope; retries exhausted mid-leg → calibration
  aborts with the existing `calibration_failed` reason (never an unhandled
  exception; the session survives).
- Sim: `SimGuideCamera.guide_expose_fail_next_n: int = 0` knob — next N
  exposures raise `DeviceError` — drives all tests.
- Constants module-level, named (`_EXPOSE_RETRIES = 3`, `_EXPOSE_BACKOFF_S`,
  `_FAULT_FRAME_BUDGET = 5`).

### A2 — PPEC dither compensation + settle dead-reckoning + wall-clock (Rust + minimal PyO3)
- Trait: `fn dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool {
  let _ = (ra_amt_px, ra_rate); false }` — defaulted, same pattern as
  `result_with`; only PPEC overrides (returns true, applies upstream
  `GuidingDithered` gear-time compensation via the existing unit-covered
  `guiding_dithered` method).
- Engine dither path: call `ra_algo.dither_notify(ra_px, cal_ra_rate)`;
  true → SKIP the RA reset (Dec algo reset behavior unchanged). RA rate is the
  calibration-derived rate the engine already holds.
- Settle: lost-star frames during an open settle window use `deduce_result`
  (PPEC dead reckoning) exactly like ordinary lost frames — aligning with
  upstream guides-through-settle semantics (P4-T1 ruling C becomes observable
  once dither no longer resets; this closes it).
- Wall-clock (M6): the GP gear clock derives from frame TIMESTAMP deltas
  (`GuideEngine.process(frame, timestamp_s, exposure_s)` already receives it)
  instead of accumulated `exposure_s`. Guard: non-monotone or absurd deltas
  (>10× exposure) fall back to `exposure_s` for that frame (protects against
  host clock jumps; adjudication comment required). The P4 gate drives frames
  manually — its virtual clock must feed consistent `timestamp_s` increments;
  verify the gate still passes and remains deterministic.
- Parity sources: `gaussian_process_guider.cpp` GuidingDithered /
  DirMoveApplied paths + dossier §6.8.3. Upstream-literal rule + dual citations
  as in the whole program. New goldens: compensation shifts gear time by
  amt/rate; model prediction survives a dither (assert non-reset).
- Gate extension: `test_native_guider_ppec.py` gains a third arm — PPEC with a
  mid-run dither: post-dither RMS must recover to beat hysteresis (proves the
  model survived). Stays deterministic and ≤ ~40 s additional.

### A3 — Alpaca imageready timeout (Python only)
- `AlpacaCamera.expose()` gets an overall deadline: `exposure_s + 30 s` (named
  constant). Expiry raises `DeviceError("imageready timeout")` — which A1's
  envelope then handles in guiding contexts; imaging contexts surface the
  existing DeviceError path. No behavior change on the happy path.

### A4 — Guide optics scale (Python + UI)
- `Optics.guide_focal_length_mm: float | None = None` (config field, validated
  > 0 when present).
- `native_backend.native_guider()` computes
  `image_scale_arcsec = 206.265 * pixel_size_um / guide_focal_length_mm ×
  binning` when both inputs exist; else current default (1.0) with the existing
  documented caveat comment updated.
- Settings UI: one numeric field in the optics section ("Guide scope focal
  length (mm)"), optional, with the existing optics-form validation pattern.
- The P2-T3 F2 gap comment at the construction site is replaced by the real
  computation.

### A5 — GP cross-session retention (Rust + Python)
- Rust: `GuideEngine`/GP expose `dump_gp_window() -> Vec<(t, y, w)>`-style
  serialization and a `restore_gp_window(...)` applying upstream
  `GuidingStarted` retention (keep newest `retain_max_pct_period` = 40% of one
  period worth; source: `gaussian_process_guider.cpp` GuidingStarted + §6.8.6).
  PyO3 surface mirrors `dump_calibration` conventions.
- Python: persist beside calibration — `CONFIG_DIR/guider/<profile-id>-gp.json`
  — written on guiding stop, restored on start IF the same profile + same
  calibration (reuse-gate result) applies; cleared by the existing
  calibration DELETE endpoint (one clear = both files).
- Non-goal: no cross-PROFILE sharing; no retention when calibration was
  rejected by the reuse gates.

## §4 Error handling

- Every new failure path terminates in an existing named channel: retry →
  lost-frame → star-lost recovery; budget exceeded → bus error honest death;
  cal fault → `calibration_failed`; timeout → `DeviceError`. No new silent
  states. Corrupt `-gp.json` → ignored with a log line + fresh model (mirrors
  P2-T2 corrupt-persistence hardening).

## §5 Testing / acceptance gate

- TDD per item; sim fault knob drives A1/A3-adjacent tests; Rust goldens for
  A2/A5 (fmt/clippy -D warnings/test/build gates as always; wheel rebuild).
- **Sub-project acceptance gate (CI-wired): the "unattended night" scenario** —
  on the closed-loop sim: guiding runs, absorbs an injected 2-exposure camera
  fault, dithers with RA=PPEC WITHOUT model reset, settles, and a stop/start
  cycle restores the GP window (retention proven by immediate post-restart
  prediction quality). One deterministic e2e test file added to the CI native
  job list.
- Full server suite green (baseline 1184/0 + additions); CI native job green.

## §6 Phasing (single plan, ordered tasks)

T1 A3 timeout → T2 A1 retry envelope (uses A3's DeviceError) → T3 A4 optics
scale → T4 A2 dither/settle/clock bundle (opus, parity-critical) → T5 A5
retention → T6 acceptance gate + CI wiring + docs touch
(`docs/guide/guiding.md` + `docs/native-parity/native-guider.md` deferred-item
lines updated to "closed" as each lands).
