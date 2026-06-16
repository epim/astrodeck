# AstroDeck — Operational-Readiness Review

**Date:** 2026-06-16
**Reviewer:** engineering assessment (adversarial, internal — not marketing)
**Scope:** `C:\Users\bear\astro` — FastAPI `server/` + React `ui/`, device abstraction with Alpaca / NINA-bridge / simulator backends.
**Basis:** full read of the server source + `server/tests/`, the live-rig handoff at `.remember/remember.md`, and one LIVE-RIG validation session (2026-06-15 night) against a real NINA rig: Player One Poseidon-M PRO mono, ZWO AM-series harmonic mount, ZWO EAF, Wanderer 8-slot wheel, PHD2; site 37.13N/122.02W.

> **One-line verdict:** AstroDeck is an impressively complete and unusually well-disciplined young system — clean role abstraction, fail-closed safety philosophy, ~241 unit tests, and a clean adversarial-review trail — but it has had **exactly one night on real hardware**, almost all of its tests run against the simulator or mocks, the **live plate-solve path is broken**, **guiding is unproven on sky**, and the **automation/safety engine has never run an unattended multi-hour session on a real rig.** It is *Usable-with-caveats for attended sessions* and *Not-ready for trustworthy unattended overnight operation.*

---

## How to read the ratings

| Rating | Meaning |
|---|---|
| **Solid** | Well-implemented, well-tested, and (where applicable) proven on real hardware. Trust it. |
| **Usable-with-caveats** | Works, but has known gaps or is only partly proven; attend it / know the edges. |
| **Rough** | Implemented but thinly tested, fragile, or proven-broken in one or more real paths. |
| **Not-ready** | Should not be relied on for the job it implies, especially unattended. |

"Validated LIVE" = exercised on real hardware on 2026-06-15. "Sim/unit only" = never touched real hardware; correctness rests on the simulator or mocks, which in several cases encode the *same assumptions as the code under test*.

---

## Executive summary

- **The architecture is genuinely good.** A small role-based device abstraction (`devices/base.py`), an event bus that fans out to the UI, an engine that works in roles not vendors, fail-closed safety with a double staleness guard, atomic config writes, and verifiable path-traversal guards. The code is heavily commented with fix-markers (`C1-*`, `P0-*`, `F-*`) showing a real review history.
- **Live hardware proved a narrow but real slice:** capture→preview, site/optics persistence, sky math, GOTO/slew, native autofocus (HFR 1.96), and TPPA polar alignment all worked under the stars. Two real NINA-bridge bugs were found *only* because of real hardware (tracking endpoint; RA hours-vs-degrees) and are fixed + committed (`d32699f`).
- **The live plate-solve path is broken and is the #1 imaging-critical gap.** `goto_and_center` in NINA mode calls NINA's `/prepared-image/solve`, which **hangs** on the rig. It is now bounded by a timeout and degrades to raw GoTo (≈11′ error), but the working ASTAP solver in `solve/astap.py` is **completely orphaned in NINA mode** — `hub.solve_and_sync` returns before it is ever reached. Plate-solve centering is effectively non-functional on the live rig.
- **Guiding is the least-proven path.** PHD2 connects and calibrates, but calibration **failed on sky tonight** (high-dec target + ~1.8° polar error on the harmonic mount). There is **no PHD2 reconnect**, **no calibration management**, and critically **no meridian-flip calibration flip** — a real GEM hazard. The guide-cam "preview" is a 15×15 PHD2 star thumbnail, not a full frame.
- **The automation/safety backend is the biggest untested risk.** It is well-designed and unit-tested and passed a 24-finding adversarial review, but every safety test uses 0.05 s frames with directly-injected safety state. On a real rig with 300–600 s subs there is up to a full sub-exposure of blind time between safety checks, the dead-man's-switch **stops pinging during legitimate cloud-pauses** (false alarm), and **three escalation knobs (`require_cooling`/`require_guiding`/`af_failure_action`) are dead config** the engine never reads. It has never run an unattended multi-hour session on hardware.
- **There is no security model and no deployment story.** The server binds `0.0.0.0:8800` with **no authentication, no CORS policy, no TLS** — anyone on the network can slew the mount. There is **no Docker, no Raspberry Pi image** — it runs from a venv. **No live-stacking and no flats wizard** (both flagged Tier-1 UX items, never built).
- **Test coverage is broad but shallow at the hardware boundary, and the suite is not green.** Running it now: **250 passed, 1 FAILED** (`test_autofocus.py::test_autofocus_converges` — the parabola fitter landed 600 units off true focus, tolerance 400) in ~4 min. The README's "251 passing" badge is stale/wrong. The two hardest transports — the **Alpaca backend** and the **PHD2 guider** — have **essentially zero unit tests**, and `AstapSolver` and `save_fits` are untested.

---

## Subsystem-by-subsystem assessment

### 1. Equipment connect & discovery (Alpaca / NINA / PHD2 / sim)

**(a) Implemented.** `hub.connect_*` paths for sim, NINA bridge, per-role Alpaca, and PHD2 (`hub.py:153-217`). Alpaca UDP discovery + an SSRF-guarded manual scanner (`devices/alpaca.py`, `validate_scan_host` resolves the host and rejects loopback/private/link-local to defeat DNS rebinding). NINA discovery sweeps the local /24 (`devices/nina.py`, `discover_nina`). A `_last_connect` replay map enables `reconnect_role()` (`hub.py:277`) — **Alpaca only**. Connect/disconnect lifecycle is careful: `disconnect_all` cancels every task and clears state (`hub.py:218-264`).

**(b) Tests / live.** Sim devices well-covered (`test_sim_devices.py`). NINA bridge covered behaviorally by `test_nina.py` **against a mock that encodes the same assumptions as the code** (e.g. `mock_nina.py` registers *both* `/tracking` and `/set-tracking`, so the exact endpoint bug fixed tonight is **not** guarded by a failing test). **Alpaca backend has no `test_alpaca.py` at all** — discovery, ImageBytes parsing, SSRF validation, and the emergency `stop()` are untested. **LIVE:** NINA connect/discovery validated on the rig; two real bugs surfaced and fixed (`d32699f`). Alpaca connect path is **sim/unit only** — never exercised against a real Alpaca server.

**(c) Rating: Usable-with-caveats** (NINA bridge); **Rough** (Alpaca, untested on real gear).

**(d) Known issues.**
- Alpaca / NINA never flip `connected = False` on a transport drop; nothing auto-detects a dropped link. `reconnect_role` covers only Alpaca; **PHD2 and NINA have no reconnect** (`hub.py:283`).
- `NinaGuider.guide_frame` hard-codes PHD2 at `<bridge-host>:4400` (`devices/nina.py` ~`:714`) — a remote or non-default-port PHD2 silently yields no guide thumbnail, no log.
- The NINA bridge is a **transition dependency** (roadmap: drop NINA, port to Rust, drive via Alpaca/native). Today the most-proven backend is the one slated for removal.

---

### 2. Capture & live preview

**(a) Implemented.** `hub.capture` → `_publish_preview` (`hub.py:643-807`) with two honest paths: NINA's pre-stretched JPEG used verbatim (display-domain histogram, no false linear claims), and a raw/linear path (sim/Alpaca) that runs one star-detection pass feeding HFR + count + overlay, builds a display JPEG + a lossless PNG + true linear histogram. A 3-tier memory cap rings frames (8 display / 50 thumb / 1-2 lossless — `_trim_previews`). FITS is written *before* the first preview event so `saved_path` is correct from the start. Preview routes serve display/lossless/thumb/fits/png with a `_is_local_save` guard that resolves + `is_relative_to(CAPTURE_DIR)` (`app.py:939-952`).

**(b) Tests / live.** Imaging pipeline is the best-tested numeric subsystem (`test_imaging.py`: HFR-tracks-defocus, detection, stretch monotonicity, histogram travel). **`save_fits` itself has zero tests** — BITPIX/BZERO/BSCALE for uint16 are unverified. **LIVE:** capture→preview validated on real stars tonight (real M42/M81 FITS in `captures/`).

**(c) Rating: Solid** (for single-frame capture + preview, which is genuinely proven on sky).

**(d) Known issues.**
- `save_fits` writes RA in **degrees under a header key literally named `RA`** (`imaging/fitsio.py:34`) — a convention foot-gun; and there is no dtype assertion, so a float frame would silently produce `BITPIX=-64` 4×-size FITS that ASTAP/NINA may reject.
- `/crop` and `/render.png` are **501 stubs** (`app.py:967-977`) — "Pass 2" never landed.
- `detect_stars` drops stars with `hfr > 7.0` and caps measurable HFR at ~7 (15px box, `imaging/stars.py:83`) — exactly the defocused/saturated stars an autofocus sweep needs.
- **No live-stacking** — a Tier-1 UX item, never built.

---

### 3. Cooling

**(a) Implemented.** `Camera.set_cooler/get_cooler` capability methods (`devices/base.py:134`), per-backend (sim power model, Alpaca `coolerpower` probe, NINA optional). The Monitor readout computes `at_target` against a single shared `COOLER_AT_TARGET_C` (1.0 °C) imported from the engine so the Monitor band and the engine's cooling-wait gate never drift (`hub.py:1125-1134`, `:1290-1302`). The engine has a `_cool_and_wait` gate.

**(b) Tests / live.** Sim cooler covered; Monitor cooler telemetry in `test_monitor_telemetry.py`. **LIVE:** the live camera (Poseidon-M PRO) was run but cooling was not called out as a validated path in the handoff — treat as **sim/unit only on real hardware.**

**(c) Rating: Usable-with-caveats.**

**(d) Known issues.**
- **`_cool_and_wait` is fail-open**: on cool-timeout it logs a warning and **shoots warm lights anyway** (`sequence/engine.py` ~`:1204`), and it **never reads `escalation.require_cooling`/`cooling_action`** — those knobs are dead config. An operator who requires cooling gets warm, uncalibrated subs all night with only a log line.
- NINA `get_cooler` infers `can_report_power` from whether `CoolerPower` was present, so the UI cooler-power bar can flap across polls.

---

### 4. Focus / autofocus

**(a) Implemented.** `focus/autofocus.py`: a 9-point V-curve sweep (`steps_each_side=4`, `step=350`), backlash takeout (approach from below), **parabola fit** via `np.polyfit(...,2)` with a 5-point local refit near the vertex when the global parabola opens upward. NINA native-autofocus delegation path normalized into the same `focus` events.

**(b) Tests / live.** **One** sim-only test (`test_autofocus.py`) on a clean low-noise curve — and **it is currently FAILING** (the fit landed at 20600 vs true 20000, 600 > 400 tolerance), so even the single happy-path sim test of the local fitter does not pass today. None of the documented failure branches (`<4` points, flat/inverted `a<=0`, mid-sweep star loss, refit) are tested. **LIVE:** HFR 1.96 validated tonight — **but almost certainly via NINA's native autofocus, not this parabola code**, since the EAF is bridged through NINA. The local `run_autofocus` V-curve fitter is **proven in sim only — and presently fails its own sim test.**

**(c) Rating: Usable-with-caveats** (native path proven; local fitter unproven).

**(d) Known issues.**
- No retry / star-count floor: lose points to clouds and it aborts to the start position with no re-attempt.
- Vertex is silently `np.clip`-ed to the sweep edge when the true minimum is out of range — reported as success at the boundary with no "minimum not bracketed" warning (`autofocus.py:80`).
- The real-curve shape is hyperbolic; the parabola + local refit is an acknowledged approximation that biases the vertex on asymmetric sweeps.

---

### 5. Mount GOTO / slew + plate-solve centering

**(a) Implemented.** `goto_and_center` (`hub.py:952`): slew → solve → sync → re-slew loop with tolerance and a graceful degrade to raw GoTo on solve failure. Server-side below-horizon guard on user GOTO (`_check_horizon`, `hub.py:454`). `AstapSolver` (`solve/astap.py`) is a complete subprocess wrapper (60 s timeout, `.ini` parse, blind/hint search). A `SimSolver` returns true sim pointing.

**(b) Tests / live.** `test_hub_solve.py` verifies FOV-hint plumbing + profile cache, but **`AstapSolver` has zero tests** (no `.ini` parse, no timeout, no discovery). **LIVE:** GOTO/slew validated on sky (re-slew to NGC 5907 confirmed the RA-degrees fix). **Plate-solve centering FAILED live** — see below.

**(c) Rating: GOTO/slew = Solid; plate-solve centering = Rough / proven-broken on the live rig.**

**(d) Known issues — this is the most important imaging gap.**
- **ASTAP is orphaned in NINA mode.** `solve_and_sync` branches to `_nina_solve_and_sync` and returns *before* `get_solver()`/ASTAP is ever reached (`hub.py:893` vs `:902`). So the only solver the live rig can reach is the broken NINA call.
- **NINA `/prepared-image/solve` is the wrong endpoint and HANGS** (author's own TODO at `hub.py:935-938`). It is now bounded by a 90 s timeout and `goto_and_center` degrades to `centered:false` raw GoTo (≈11′), but **plate-solve centering does not work on the live rig.** TPPA proved NINA's own solver works, so the fix is to capture-and-solve through a real NINA endpoint (or wire ASTAP into NINA mode).
- `find_astap` discovery is brittle (no `PATH`/`which` lookup, 5 hardcoded paths) and on a miss falls back to `SimSolver`, which with a hint **echoes the hint as a "successful solve"** — a dangerous false-solve on a live rig (`solve/simsolver.py:30-33`). Wiring ASTAP in should also make `SimSolver` refuse to run when `mode == "nina"`.

---

### 6. Polar alignment (TPPA)

**(a) Implemented.** `polar/session.py` is a **thin WebSocket proxy to NINA's TPPA** (`/v2/tppa`). AstroDeck sends `start-alignment` and reads back NINA-computed `AzimuthError`/`AltitudeError`/`TotalError`, converting deg→arcmin. There is **no 3-point geometry, no plate-solve, no mount-model math in this codebase** — NINA does all of it. The sim driver fakes convergence with decaying random errors. The UI bullseye reticle renders the result.

**(b) Tests / live.** `test_polar.py` tests the sim driver and the NINA message parser (deg→arcmin, progress hold/clamp) — good parser tests. The actual alignment geometry can't be tested here because it lives in NINA. **LIVE:** TPPA ran on sky tonight (az +83′ / alt −72′ / total 110′; `hypot(83,72)≈110` checks out) — **the one subsystem genuinely proven on real sky, but the proven component is NINA's; AstroDeck's contribution is the plumbing + reticle.**

**(c) Rating: Usable-with-caveats** (works via NINA; AstroDeck owns none of the math).

**(d) Known issues.**
- Hardcoded `_DEG_TO_MIN = 60.0` assumption (`session.py:21`) — if a NINA build reports arcminutes or renames a field, every displayed error is 60× off or silently 0.
- **Silent false-positive:** if NINA's JSON keys change, every `pick` returns None → `float(az or 0)` → 0.0 → the reticle reports "perfectly aligned." The worst possible failure mode for polar.
- No WebSocket-drop recovery mid-alignment.

---

### 7. Guiding (PHD2)

**(a) Implemented.** `guide/phd2.py`: persistent socket, background `_listen` task demuxing JSON-RPC responses from async events, `GuideStep`/`StarLost`/`SettleDone` republished to the bus. `start_guiding` uses the `guide` RPC + settle handling; `guide_frame` via `get_star_image` (15×15 thumbnail). RMS over the last ~100 samples. Guide-error surfacing was fixed tonight (the empty-error swallow is now surfaced via `_settle_error` / `_fail_reason`).

**(b) Tests / live.** `test_guide_frame.py` tests `star_image_to_png` and `SimGuider` — **the entire `PHD2Guider` class is untested** (connect, listen, RPC dispatch, settle, StarLost). **LIVE:** PHD2 connected and calibrated, but **calibration FAILED on sky tonight** (high-dec target + ~1.8° polar error). This is the **least-proven path on real sky.**

**(c) Rating: Rough.**

**(d) Known issues.**
- **No reconnect / backoff.** On socket EOF the `_listen` task dies and is never restarted (`phd2.py:93-96`); `_pending` futures leak (hang until their own timeout); `stats().guiding` stays stale "Guiding" forever after a drop.
- **No calibration management and — critically — no meridian-flip calibration flip.** On a real GEM, guiding runs **backwards (runaway)** on the far side of the pier. `hub.meridian_flip` restarts guiding but never flips PHD2's calibration. This is a real operational hazard not handled anywhere.
- Calibration-failure surfacing depends on PHD2 emitting `SettleDone.Error`; a `StartCalibration`→`Alert` failure is **not handled** (`Alert`/`CalibrationFailed` events are ignored) → a ~90 s `TimeoutError` instead of a friendly message.
- The guide-cam "is the view obstructed?" question is only partly answered — it's a 15×15 star thumbnail, not a full frame. A future native/Alpaca guide camera would fix this.

---

### 8. Sequence engine

**(a) Implemented.** `sequence/engine.py` (1378 lines) is intricate and careful: paused-elapsed-aware ETA, per-frame `_persist` to `.sequence_resume.json` for crash-resume (re-attaches the same report id), a window-sorted **skip-ahead scheduler** (`_run_scheduled`) that skips closed/never-rises targets, meridian-flip handling, an overhead EMA + analytic event-cost bookkeeping for honest ETAs, an HFR running-median **quality gate** before recording a frame, and a **shielded wind-down** that re-awaits the park future across cancellation so a concurrent UI abort can't orphan a half-completed park.

**(b) Tests / live.** Control flow is well-tested (`test_sequence.py`, `test_engine_safety.py`, `test_schedule.py`, `test_report.py`). **But every engine/safety test uses 0.05 s frames with directly-injected safety state** — ~6000× shorter than a real sub. The retake path has no dedicated test; the discard test stubs `_check_quality` entirely. **LIVE:** the engine has **never run on real hardware** in any session, attended or not.

**(c) Rating: Usable-with-caveats in sim; Not-ready on real hardware (unproven).**

**(d) Known issues.**
- **No timeout around `hub.capture` / `slew` / `park` / `goto_and_center`** in the engine (`engine.py:746` etc.). A hung Alpaca/NINA call is an unbounded `await` — the single most likely overnight-hang source, and nothing in the engine bounds it.
- Safety is checked **at frame boundaries only** — up to a full sub-exposure (5–10 min) of blind time if clouds arrive mid-exposure. No mid-exposure abort. Sim tests structurally cannot surface this.
- Meridian flip fires only at a frame boundary on `ttf <= 0`; with long subs the mount can track minutes past the meridian — many GEMs hit a pier limit first.
- `discard` quality action quietly reduces the frame count rather than retaking (documented but surprising); only `retake` re-exposes, capped at `hfr_retake_limit_per_target`.
- `_persist` uses a plain `write_text` (not atomic) — a power cut mid-write corrupts `.sequence_resume.json` and a crash-resume **silently forks a new report**, losing continuity (`engine.py:410`).

---

### 9. Automation / safety (SafetyMonitor gating, presets, scheduling, alerts, reports, dead-man's-switch)

**(a) Implemented.** This is the most defensively-coded part of the system. The SafetyMonitor poller runs on its **own cadence** (5 s) caching a `SafetyReading`; reads that time out/raise are cached **fail-closed** (`is_safe=False, stale=True`), and `hub.safety_reading()` independently re-stamps any cached reading older than ~18 s as stale — closing the "dead poller keeps returning SAFE" seam (`hub.py:295-312`, `:1090-1121`). The engine gate increments an `_unsafe_streak` and acts only at `unsafe_consecutive`, with named presets (`backyard` = pause, `remote` = abort/park/warm). The reporter writes atomically and derives trends at read-time. Alerting supports ntfy / webhook / Telegram with SSRF guards, dedupe, redaction, and a bounded retry queue, launched from the app lifespan so a flaky endpoint can never take the server down.

**(b) Tests / live.** Unit-tested + passed a **24-finding adversarial review (all fixed)**. `test_move_watchdog.py` is genuinely adversarial (deadman retries a *failed* stop). **But:** the safety reading is injected directly in tests, the 18 s age-out never actually fires in a test, and no test drives an UNSAFE verdict mid-exposure from the real poller. **LIVE:** **has NEVER run an unattended multi-hour session on real hardware. This is the single biggest untested risk in the project.**

**(c) Rating: Not-ready** for trustworthy unattended operation (logic strong, real-world behavior unproven; several real gaps below).

**(d) Known issues.**
- **Dead-man's-switch stops pinging during a legitimate safety pause and during scheduler waits** (`engine.py` `_on_unsafe` loop / `_wait_until`). A correct multi-hour cloud-pause (backyard default `max_pause_min=120`) → no deadman ping for up to 2 h → the external service falsely pages "rig dead." Worse, a user who learns to ignore that false page will ignore a real one. **Fix:** drive the deadman + heartbeat from a wall-clock task, not the frame loop.
- **Deadman silently skips private/LAN URLs** (the SSRF guard) — a user pointing it at a self-hosted Uptime-Kuma / healthchecks box on `192.168.x.x` (extremely common) gets a deadman that is **never pinged and never warns**. They believe they have a deadman; they have nothing.
- **Three escalation knobs are dead config:** `require_cooling`/`cooling_action`, `require_guiding`/`guiding_action`, `af_failure_action` are defined in config and surfaced in the API but the engine **never reads them**. An operator who sets `require_guiding=abort` gets an all-night unguided (trailed) run instead. Wire them in or remove them from the UI.
- **No engine-level timeout on device I/O** (see §8) — the no-progress watchdog is *warn-only* and *off by default* (`no_progress_watchdog_s=0`), so a wedged capture hangs the night with at most one warning.
- The safety poller is `create_task`'d with no supervisor; if it raises unexpectedly, nothing restarts it. The 18 s age-out then fail-closes to a *permanent spurious UNSAFE* with no self-heal until reconnect.
- Alert redaction is **fail-open and incomplete**: only `token` is blanked; `chat_id`, secrets embedded in webhook/ntfy `url` (e.g. `https://user:pass@host`), and `deadman_url` are **not** redacted and are broadcast verbatim over WS/REST. Use `pydantic.SecretStr` to make leakage fail-safe.

---

### 10. Sky atlas (framing / mosaic / visibility)

**(a) Implemented.** Two deliberate math regimes: **hand-rolled** offline math in `catalog/coords.py` (LST/altaz/sun/dark-window) and **astropy** in `catalog/visibility.py` (real `EarthLocation`/`AltAz`/`get_body`, twilight ladder, moon phase/illumination/separation, transit). `catalog/framing.py` is pure tangent-plane trig mirroring `ui/src/lib/framing.ts` for live mosaic preview. `catalog/survey.py` proxies CDS hips2fits cutouts with a SHA-1 disk cache. 64 hand-curated catalog objects.

**(b) Tests / live.** Framing is the best-tested (`test_framing.py` checks RA wrap, rho→0, snake order, rotation, validator round-trip). Visibility verified with physical checks (`test_visibility.py`). **`coords.py` is barely tested** — a single 2°-tolerance Polaris smoke test; `lst_hours`/`sun`/`dark_window` have no accuracy test and are never cross-checked against the astropy code next door. **LIVE:** sky math validated tonight via the GOTO/meridian readout — which exercises exactly the *hand-rolled* path with the least test coverage.

**(c) Rating: Solid** (framing + astropy visibility); **Usable-with-caveats** (hand-rolled coords).

**(d) Known issues.**
- **`coords.altaz` has no precession** (J2000 catalog coords used as epoch-of-date) → ~0.3–0.4° error by 2026, and it is **live in the meridian/horizon mount path** (`hub.py:1169`, `:1236`). `lst_hours` is actually GMST (no equation of equinoxes). No refraction anywhere.
- Two divergent twilight implementations (`coords.dark_window` vs `visibility._find_dark_window`) can disagree; only the astropy one has the high-latitude fallback.
- Survey worst-case latency ~30 s (15 s × 2 + backoff) and an **unbounded disk cache on the capture volume** (no TTL/LRU) — competes with FITS storage on a small Pi SD card.
- No test enforces the `framing.py` ↔ `framing.ts` "byte-identical" invariant the design relies on for "Send to Plan."

---

### 11. Monitor

**(a) Implemented.** `hub.monitor_snapshot` one-shot hydration + WS catch-up within ~2 s (`hub.py:1187-1205`); server-computed meridian block (`_compute_meridian`), cooler at-target band, disk free/low/critical, guide-recent ring, NINA link health. `MonitorView.tsx` consumes it.

**(b) Tests / live.** `test_monitor_telemetry.py` covers the telemetry shapes. **LIVE:** exercised indirectly during the live session (status was visible); not specifically validated end-to-end overnight.

**(c) Rating: Usable-with-caveats.**

**(d) Known issues.** Depends on the 2 s status poll for `last_meridian`; if the status loop is starved the ETA/flip estimate silently goes stale. Meridian status is "unknown" for fork mounts (pier side "unknown") — correct but means the flip countdown is blank there.

---

### 12. Settings / site / optics

**(a) Implemented.** Pydantic config with field validation, atomic writes via `write_json_atomic` (fsync + `os.replace` with Windows AV-lock retry), `.bak` recovery ladder, optimistic-concurrency `version` token, profiles & plans as uuid-keyed files. The San-Francisco-hardcoded-site P0 is fixed (`config.py` docstring); the site is now a config-backed property.

**(b) Tests / live.** Persistence is **genuinely well-tested** (`test_persist.py`: backup-is-a-copy, replace-retry exactness, 4-quadrant recovery ladder). Path-traversal guards verified on Windows. **LIVE:** site/optics persistence validated on the rig tonight.

**(c) Rating: Solid** (for single-user crash recovery).

**(d) Known issues.**
- **`version` conflates schema-version and concurrency-token** — there is **no real migration story**. Additive fields work (pydantic defaults), but a field rename or a non-default derivation = silent data loss; and the corrupt-reset path rewinds `version` to 1.
- **No concurrency control** despite an explicit multi-client "remote" use case — two async tasks calling `bump_and_save` can race; no file lock.
- Secret redaction gaps (see §9d).
- Windows reserved-name ids (`con`/`nul`/trailing-dot) pass the parent-dir traversal check (low severity; uuids dominate; plan imports are re-keyed).

---

### 13. Power

**(a) Implemented.** Power is the `Switch` role (`devices/base.py:265` — "Power boxes (Pegasus UPB, …), dew heaters, anything switchable") plus camera dew-heater (`set_dew_heater`). `PowerView.tsx` + `/api/switch/ports` + `/api/switch/set` + `/api/camera/dew-heater`.

**(b) Tests / live.** Sim switch covered (`test_sim_devices.py`, incl. read-only port raise). **LIVE:** the live rig's power was not called out as validated; treat as **sim/unit only on real hardware** (no Pegasus UPB in tonight's rig).

**(c) Rating: Usable-with-caveats** (sim-proven; no real power box exercised).

**(d) Known issues.** Power management is generic switch toggling — no UPB-specific telemetry (voltage/current/auto-dew) modeled. No automated power-on/off sequencing tied to the sequence engine.

---

## Cross-cutting concerns

### Error handling / reconnect
- **Strong** at the task level: `_spawn` wraps every long op so a failure logs rather than crashes; `disconnect_all` cancels everything; the move-axis deadman retries a failed stop.
- **Weak** at the device level: no transport-drop detection; reconnect covers **Alpaca only** (not NINA, not PHD2); no engine-level timeout on device I/O; several **unbounded polling loops that return success on timeout** (NINA slew/focuser, Alpaca exposure/move). A hung device is the most likely overnight failure and the system is not hardened against it.

### WebSocket event model
- Clean single-bus fan-out (`events.py`): each client gets an `asyncio.Queue(maxsize=500)` with **drop-oldest backpressure**, so a slow consumer can't stall the server. **But** the history ring keeps **only `type=="log"` events** (`events.py:41`) — a client connecting mid-sequence gets a `hello` summary + log history but **no replay of device/progress/safety state** until the next event (≤2 s for status, but safety/preview are event-driven). No per-event sequence numbers, so a client can't detect a dropped event. WS has **no auth** (see security).

### Config persistence
- Solid single-user crash recovery; **no migration framework and no concurrency control** (see §12). The engine's resume file is **not** written atomically (unlike config) — a real corruption risk on power loss.

### Security
- **This is the most serious cross-cutting gap.** `__main__.py:15` binds **`0.0.0.0:8800` by default with no authentication, no CORS policy, and no TLS.** Anyone who can reach the host on the LAN can slew the mount, command power switches, start/abort sequences, and read the config. The SSRF guards (`validate_scan_host`, alert/deadman URL checks) are well done and protect against the server being used as a *scanning/exfil proxy*, and path-traversal guards are verified — **but the control surface itself is wide open.** For a backyard rig on a trusted home network this is tolerable; for the "remote observatory" use case the product explicitly targets it is unacceptable. There is no rate-limiting and no audit log.
- Token redaction is fail-open and incomplete (§9d).

### Single-server-process model
- Everything (REST, WS, the engine, all device I/O, the safety/status/heartbeat pollers) runs in **one uvicorn process on one asyncio event loop.** A CPU-bound stretch/detect is offloaded to `asyncio.to_thread`, which is correct, but **any blocking call that isn't offloaded stalls the whole rig** (status poll, safety poll, WS fan-out, the engine). There is no process supervision, no auto-restart, no watchdog *outside* the process — if uvicorn dies mid-session, the rig is uncommanded until a human notices (the tonight workaround was an OS-level scheduled task to force-park at dawn, which tellingly lives *outside* AstroDeck). The engine's resume file is the only crash-recovery mechanism, and it isn't atomic.

### What an unattended overnight run would actually need (and doesn't have yet)
1. A timeout around every device I/O call in the engine.
2. A wall-clock deadman/heartbeat that keeps pinging through pauses and waits.
3. Automatic park-on-fatal-error from *outside* the process (a real external watchdog, not just an outbound deadman).
4. PHD2 reconnect + meridian-flip calibration handling.
5. A working plate-solve centering path.
6. The escalation knobs actually wired in.
7. Atomic resume-file writes.

---

## Deployment & packaging

- **There is no deployment story.** It runs from `server/.venv` via `python -m astrodeck`. **No Docker, no Raspberry Pi image, no systemd unit, no installer.** The UI is built separately (`npm run build` in `ui/`) and served as static files only if `ui/dist` exists. For a product positioned as an "ASIAIR replacement" (an appliance), the absence of a one-command install / image is a major adoption and reliability gap — and it means the no-supervision problem above has no packaged answer.
- **Not built (flagged Tier-1 UX items):** live-stacking, flats wizard.

---

## Test coverage reality check

- ~241 test functions, broad subsystem coverage, clean fix-marker trail. **Current run: 250 passed, 1 failed** (the autofocus convergence test — a real, reproducible flakiness in the parabola fitter, not an environment issue). Persistence, schedule math, framing, reporting, alerting, and the move-watchdog are genuinely well-tested.
- **Shallow at the hardware boundary:** the two hardest transports — **Alpaca backend** and **PHD2 guider** — have essentially **no** unit tests. `AstapSolver`, `save_fits`, and the NINA guide-socket path are untested. The NINA mock encodes the same assumptions as the code (it would not catch the `set-tracking` regression fixed tonight). Safety tests inject state directly and use 0.05 s frames, so no timing-dependent or real-latency behavior is exercised.
- **Net:** the tests prove the *control flow and pure logic* are correct. They do **not** prove the system behaves correctly against real, slow, flaky hardware over hours — which is exactly the regime that matters for the product's headline use case.

---

## Prioritized roadmap to production-trustworthy unattended operation

**P0 — blocks any trustworthy unattended run:**
1. **Fix live plate-solve centering.** Wire `AstapSolver` into NINA mode (capture → `save_fits` → ASTAP), or call a real NINA capture-and-solve endpoint; stop using `/prepared-image/solve`. Make `SimSolver` refuse to run when `mode == "nina"` so a discovery miss can't fake-solve. (`hub.py:893-950`, `solve/astap.py`)
2. **Bound all engine device I/O** with `asyncio.wait_for` (capture/slew/park/goto/guide) and escalate a true no-progress timeout to abort+park. (`sequence/engine.py`)
3. **Make the dead-man's-switch wall-clock-driven** so it keeps pinging through pauses/waits; warn loudly (don't silently skip) when the deadman URL is unreachable/LAN. (`alerting.py`, `engine.py`)
4. **Add authentication** (at minimum a shared token / reverse-proxy guidance) before any non-trusted-network deployment, and document binding to `127.0.0.1` for local-only use. (`__main__.py`, `api/app.py`)
5. **Add an external park-on-failure watchdog** (the dawn-park scheduled task generalized) and atomic resume-file writes. (`engine.py:410`)

**P1 — needed for confidence, especially guiding:**
6. **PHD2 reconnect + meridian-flip calibration flip** (or block flips when the guider can't flip calibration). Handle `Alert`/`CalibrationFailed` events. (`guide/phd2.py`, `hub.meridian_flip`)
7. **Wire the escalation knobs** (`require_cooling`/`require_guiding`/`af_failure_action`) into the engine, or remove them from the UI so they don't lie. (`engine.py`, `config.py`)
8. **Real-latency integration test:** one engine test with multi-second frames and a poller-driven (not injected) UNSAFE transition mid-exposure.
9. **Unit-test the untested transports:** Alpaca (ImageBytes parse, SSRF, stop), PHD2 (settle/StarLost/reconnect), `AstapSolver`, and a `save_fits` round-trip asserting `BITPIX=16`/`BZERO=32768` for uint16.

**P2 — correctness & UX completeness:**
10. Add precession (and ideally refraction) to `coords.altaz`, or route the mount path through astropy; cross-check the two regimes in a test.
11. Real schema-version + migration story separate from the concurrency token; atomic engine resume writes.
12. Redact `chat_id` / URL-embedded creds / `deadman_url`; move secrets to `SecretStr`.
13. Bound the survey cache (TTL/LRU); separate it from the capture volume.
14. Build the missing Tier-1 UX: live-stacking and a flats wizard.

**P3 — productization:**
15. Deployment story: Docker image + Raspberry Pi image + systemd unit + a supervised launcher.
16. Full-frame guide-camera preview via native/Alpaca guide cam.
17. Begin the NINA→Alpaca/native (Rust) port so the most-proven backend isn't the one slated for removal.

---

## Overall verdict

AstroDeck is an **impressively complete and unusually disciplined young system.** The architecture, the fail-closed safety philosophy, the test discipline, and the visible adversarial-review history are well above what a one-week-old hobby project usually shows, and the live session proved a real, non-trivial slice of the imaging chain under the stars.

But maturity is measured in nights on sky, and AstroDeck has had **exactly one.** The live session itself surfaced a class of bug (hours-vs-degrees, wrong solve endpoint, swallowed guide errors, calibration failure) that **only real hardware reveals** — which is precisely the evidence that the rest of the unvalidated paths likely hide similar issues. The headline use case — trustworthy unattended overnight imaging — depends on the **automation/safety engine that has never run on real hardware**, a **plate-solve path that is currently broken on the live rig**, **guiding that is the least-proven path and lacks GEM-flip safety**, and a **server with no auth, no supervision, and no deployment story.**

**Bottom line:** Trust it today for **attended** sessions on a trusted network, watching the screen, with plate-solve centering treated as best-effort and guiding watched closely. Do **not** yet trust it to run a rig alone overnight. The roadmap above is the honest distance between "works on sky with a human present" and "I can go to sleep."
