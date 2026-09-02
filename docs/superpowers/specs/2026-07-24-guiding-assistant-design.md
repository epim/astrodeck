# Guiding Assistant wizard + adaptive Dec backlash — design

**Status:** design only (no implementation).
**Author:** design agent (spec for the parity wave, task #84).
**Date:** 2026-07-24.
**Scope:** a guided measurement session that samples the mount for ~1–2 min, measures
periodic error / drift / backlash / seeing, then **recommends** guide params (RA
aggression / hysteresis / min-move, RA & Dec algorithm, and a Dec backlash seed) and lets
the user apply them. Companion: the adaptive Dec backlash controller (dossier §10.2).

---

## 0. TL;DR / the load-bearing finding

**The whole "measure + recommend + apply" wizard ships as pure Python + UI. No Rust change,
no maturin wheel rebuild.** The apply target — every knob the wizard recommends — is
config surface that **already exists and is already forwarded end-to-end** to the engine:

| Recommended knob | Existing config field | Wired since |
| --- | --- | --- |
| RA aggression / hysteresis / min-move | `GuideConfig.ra_params.{aggression,hysteresis,min_move}` | PRO-12 Tier 2 (T6) |
| RA algorithm | `GuideConfig.ra_algorithm` | P2-T3 |
| Dec algorithm | `GuideConfig.dec_algorithm` | P2-T3 |
| Dec min-move | `GuideConfig.dec_params.min_move` | PRO-12 Tier 2 (T6) |
| **Dec backlash seed (ms)** | `GuideConfig.blc_pulse_ms` | PRO-12 Tier 1 |

`config.py::set_guide` persists all of them and `guide/native.py::guide_algo_config`
(server/astrodeck/guide/native.py:112-131) forwards them to `GuideEngine` at construct
time. The engine already **consumes** `blc_pulse_ms` as a static direction-reversal pulse
(`engine.rs:1338-1356`). So the wizard **populates a seed the engine already honors** — it
does not need a new engine capability.

**The one thing that genuinely needs Rust is the *adaptive* controller** (dossier §10.2 —
continuous in-engine re-sizing of the BLC pulse from observed over/undershoot). That is a
new in-engine state machine + `EngineConfig` fields + a wheel rebuild, exactly like PRO-12
Tier 2's T5. It is **explicitly deferred to a v2 Rust follow-up** (§7). v1 ships the static
seed measured by the wizard, which is the same thing PHD2 ships (static BLC seeded by its
Backlash tool; adaptive is a separate opt-in).

This mirrors the dossier's own recommendation (native-parity/algorithms/phd2-guiding.md:1669):
> ship the adaptive size controller (§10.2) OFF until we have a backlash measurement tool …
> The §10.3 `BacklashTool` state machine is the way to *seed* a non-zero pulse; port it as a
> guided one-shot routine … so AstroDeck can offer "measure backlash" without the user
> hand-entering a pulse.

The Guiding Assistant **is** that measurement tool.

---

## 1. What the wizard measures & recommends

Two measurement phases, run back-to-back in one ~1–2 min session, driven entirely from the
host (raw exposures + raw pulse-guides; **no** engine calibration state machine, **no**
correction algorithms):

### 1.1 Phase A — uncalibrated drift / seeing / periodic error (~60–90 s, mount idle)

Expose the guide camera at the guide cadence, run `guide_star_find` per frame (already the
native path's star finder), track the brightest star's centroid, and accumulate a
time-series of (t, x_px, y_px). With the mount **not** being corrected, this yields:

- **Total & per-axis RMS** (px, and arcsec when `image_scale_known`).
- **Dec drift rate** `drift_per_min` (px/min → arcsec/min): the linear least-squares slope
  of the Dec centroid vs time. This is the polar-alignment residual proxy and is the input
  the backlash phase needs to de-trend its N/S runs (dossier §10.3
  `drift_per_sec = drift_per_min/60`).
- **RA periodic-error amplitude / peak-to-peak**: the de-trended RA excursion, and (stretch)
  a dominant-period estimate via autocorrelation for a PPEC recommendation.
- **High-frequency star jitter** = the seeing proxy (residual after removing drift + the
  slow PE trend), which sets how tight `min_move` can safely be.

### 1.2 Phase B — Dec backlash (~20–40 s, drives raw N/S pulses)

A faithful port of the dossier §10.3 `BacklashTool` state machine as a **pure-Python
one-shot routine** (not an engine state machine): `CLEAR_NORTH → STEP_NORTH → STEP_SOUTH →
TEST_CORRECTION → RESTORE → WRAPUP`. It drives raw `tel.pulse_guide("north"/"south", ms)`
and reads back the Dec centroid each frame. `ComputeBacklashPx` (dossier §10.3, the
drift-corrected estimator) yields **`bl_px`, `bl_ms` (the seed), and `north_rate`**, plus a
`sigma_ms` confidence band and a result code (`VALID`, `TOO_FEW_NORTH` (usable, flagged),
`TOO_FEW_SOUTH`, `BL_NOT_CLEARED`, `SANITY`).

Precondition: a Dec **rate** in px/ms (`y_rate`). We do **not** require a full engine
calibration — the sim + real mounts both report `guide_rates()` and pulse exactly at that
rate, and `native.py::_build_engine_config:718-728` already derives `px_s` from the declared
rate + image scale. v1 uses `y_rate = guide_rate_deg_s * 3600 / image_scale / 1000` when no
calibration is loaded, and the real calibration `yRate` when one is (open decision D2).

Safety: the `OutOfRoom(margin)` guard (star within `search_region` px of any frame edge)
halts a run before it walks the star off the sensor — **mandatory** in v1 (§8 risks).

### 1.3 Recommendation logic (pure, tested)

A single pure function `recommend(measurements) -> Recommendations`. Rules (PHD2-GA parity,
tuned to AstroDeck's defaults table, phd2-guiding.md:1560-1600):

- **min-move** ← smart formula `max(0.1515 + 0.1548/scale, 0.15)` (dossier line 1576),
  nudged by measured seeing: looser when high-frequency jitter is large, tighter when calm.
  Falls back to 0.2 when scale unknown.
- **RA aggression / hysteresis** ← from RA RMS vs jitter: default 0.7/0.1; lower aggression
  when the residual is dominated by seeing (over-correction risk), keep default otherwise.
- **RA algorithm** ← Hysteresis by default; recommend **PPEC** only when a clear dominant
  periodic term is detected AND PPEC is available (RA-only). (Conservative: default keeps
  Hysteresis; PPEC is an advanced opt-in — see D3.)
- **Dec algorithm** ← ResistSwitch by default (robust to backlash); recommend Lowpass2 only
  for a very-low-backlash, low-drift mount (advanced).
- **Dec backlash seed (`blc_pulse_ms`)** ← `bl_ms` when result is `VALID`/`TOO_FEW_NORTH`,
  clamped to `[0, 10000]`, floored to 0 on `SANITY`/`TOO_FEW_SOUTH` with a plain-language
  "couldn't measure backlash reliably — leaving it off" note.
- **Polar-alignment verdict** ← plain-language band from `drift_per_min` (e.g. "excellent /
  good / consider re-doing polar alignment"). Advisory only; never auto-applied.

Every recommendation carries `{ current, recommended, unit, rationale, confidence }` so the
advanced view can render a before/after with a reason.

---

## 2. Architecture & the real seams (file:line)

```
        ┌─────────────────────────── UI ───────────────────────────┐
        │ GuideView.tsx  (thin shell; new panel/section)            │
        │   Novice: one "Run Guiding Assistant" button + progress   │
        │   Advanced: <details> raw curves + per-field apply        │
        │        │ POST /api/guide/assistant/start                  │
        │        │ GET  /api/guide/assistant/report                 │
        │        │ POST /api/guide/assistant/stop                   │
        │        │ (apply) PUT /api/guide/settings  ← EXISTING      │
        │        │         DELETE /api/guide/calibration ← EXISTING │
        │  lib/guideAssistant.ts  (PURE: measurement→copy,          │
        │        apply-payload builder, selective-apply merge)      │
        └──────────────────────────┬───────────────────────────────┘
                                   │
        ┌──────────────────────── server ──────────────────────────┐
        │ api/app.py  new routes (near guide block, ~app.py:3316)   │
        │ guide/assistant.py  (NEW)                                 │
        │   • recommend(measurements) -> Recommendations   [PURE]   │
        │   • BacklashRun state machine (§10.3 port)       [PURE]   │
        │   • drift/PE/seeing reducers                     [PURE]   │
        │ guide/native.py  NativeGuider.run_guiding_assistant()     │
        │   drives self._expose() + _native.guide_star_find()       │
        │   + self.tel.pulse_guide(); publishes progress on bus     │
        └──────────────────────────┬───────────────────────────────┘
                                   │  (v1 stops here — pure Python)
        ┌───────────── Rust follow-up (v2, DEFERRED) ───────────────┐
        │ astro-guide/engine.rs  adaptive BLC state machine +       │
        │ EngineConfig.blc_{adaptive,floor,ceiling} → wheel rebuild │
        └───────────────────────────────────────────────────────────┘
```

### Key seams

- **Where the measurement lives:** a new method
  `NativeGuider.run_guiding_assistant(opts, on_progress) -> AssistantReport` on
  `guide/native.py` (the class already owns `self.cam`, `self.tel`, `self._expose`
  (native.py:657), `_native.guide_star_find` (native.py:280), and reads `guide_rates`
  (native.py:684)). It **refuses when actively guiding** (`self._active`) — the caller must
  stop first — and reuses `_expose`'s retry/backoff. The pure reducers + `recommend` +
  `BacklashRun` live in `guide/assistant.py` so they are unit-testable with zero hardware.
- **Provider gating:** the assistant is a **native-guider-only** capability (it needs raw
  pulse+centroid access the PHD2/NINA bridges don't expose). The route probes
  `getattr(hub.guider, "run_guiding_assistant", None)`; a non-native guider → honest 400
  and the button is honest-disabled (§11.8) with a "native guider only" title. Mirrors the
  `clear_calibration` capability-probe idiom (app.py:3284, 3299-3302).
- **Long-running task:** the measurement is ~90 s → spawn via the existing `_spawn` helper
  (app.py:343) exactly like `/api/guide/start` (app.py:3224). Progress streams on a new bus
  channel `"guide_assistant"` (`{phase, pct, message}`); the final `AssistantReport` is
  cached on the guider (`self._last_assistant_report`) and read back via
  `GET /api/guide/assistant/report`. Cancel = `POST /api/guide/assistant/stop` sets a stop
  event the routine polls between pulses (same shape as `stop_guiding`'s `self._stop`).
- **Apply is zero new backend:** the client builds the settings body from the accepted
  recommendations and calls the **existing** `PUT /api/guide/settings` (app.py:3324) +
  optionally the existing `DELETE /api/guide/calibration` (app.py:3289, since an algorithm
  change wants a fresh calibration). `set_guide` (config.py:857) already validates + clamps.
- **Reuse of viz:** the advanced raw-curve view reuses `GuideScatter` / `GuideGraph`
  (components/graphs) fed the assistant's sample series — the same components GuideView
  already renders for live guiding (GuideView.tsx:111,134).

---

## 3. Backend plan

### 3.1 `guide/assistant.py` (NEW, all pure / testable)

- `@dataclass AssistantMeasurements`: `samples: list[(t,x,y)]`, `rms_ra/dec/total` (px),
  `drift_per_min_px`, `pe_amplitude_px`, `pe_period_s | None`, `jitter_px`,
  `image_scale_arcsec`, `image_scale_known`, plus a `backlash` sub-dataclass
  (`bl_px, bl_ms, sigma_ms, north_rate, result_code`).
- `reduce_phaseA(samples, image_scale) -> (rms…, drift, pe, jitter)` — least-squares slope
  + de-trended stats. Pure.
- `class BacklashRun` — the §10.3 state machine as a per-frame step function
  `step(dec_px_now) -> BacklashCommand(kind=pulse|done, dir?, ms?)`, with `compute()`
  implementing `ComputeBacklashPx` (drift-corrected, sanity/too-few codes). Pure; the
  orchestrator feeds it centroids and dispatches its pulse commands.
- `recommend(m: AssistantMeasurements, current: GuideConfig) -> list[Recommendation]` —
  §1.3 rules. Pure. `Recommendation = {field, current, recommended, unit, rationale,
  confidence}`.
- `report_dict(m, recommendations) -> dict` — JSON wire shape for the GET.

### 3.2 `NativeGuider.run_guiding_assistant` (guide/native.py, new method)

Orchestrates: guard (`_active` → DeviceError "stop guiding first"); Phase A sample loop
(expose → `guide_star_find` → append centroid, publish progress); Phase B (read `y_rate`;
drive `BacklashRun` commands via `tel.pulse_guide`, honoring `OutOfRoom` + the stop event);
reduce + `recommend`; cache `self._last_assistant_report`; return it. Never runs the mount
without the edge guard. Absorbs a transient exposure fault via `_expose` (already retries).

### 3.3 Routes (api/app.py, in the guide block ~3316)

- `POST /api/guide/assistant/start` (`CAP_CONTROL_GUIDE`) — 409 no guider / 400 non-native /
  409 already guiding; else `_spawn("guide_assistant", hub.guider.run_guiding_assistant(...))`.
- `GET  /api/guide/assistant/report` (`CAP_VIEW_STATUS`) — `{report: <dict>|null}`.
- `POST /api/guide/assistant/stop` (`CAP_CONTROL_GUIDE`) — cancels the run.
- **No apply route** — the client reuses `PUT /api/guide/settings` + `DELETE
  /api/guide/calibration`.

No `config.py` schema change (all target fields exist). No new Pydantic model beyond a small
`AssistantStartBody` (optional `{ include_backlash: bool = true, duration_s?: int }`).

---

## 4. UI plan & progressive disclosure

A new **`GuideAssistantPanel`** in GuideView.tsx's right column (thin render shell), backed
by pure **`ui/src/lib/guideAssistant.ts`** (all logic + copy). Mirrors the
`guideNarration.ts` + `guideSettings.ts` "pure lib, thin binding" pattern already in the
view.

### 4.1 Novice default (zero configuration)

- One primary button: **"Run Guiding Assistant"**. Subtitle: *"Watches your mount for about
  a minute and recommends the best guide settings."*
- While running: a progress bar + a single plain-language line driven by the bus
  `guide_assistant` phase (*"Watching your mount…"* → *"Measuring Dec backlash…"* →
  *"Crunching the numbers…"*). A **Stop** button.
- On completion: a plain-language **summary card** (from `guideAssistant.ts`):
  > *"Your mount has about **430 ms** of Dec backlash and drifts **1.2′/min** (polar
  > alignment: good). I've prepared recommended settings."*
  Plus **one** button: **"Apply recommended settings"** → PUTs the built body, toasts
  *"Recommended guide settings applied — they take effect on the next guiding start,"*
  optionally clears calibration. Done. The novice never sees a number they must interpret.

### 4.2 Advanced disclosure (collapsed `<details>`, zero novice clutter)

Behind an **"Advanced / show measurements"** toggle:

- **Raw curves:** the Phase-A sample scatter + time-series via the existing `GuideScatter` /
  `GuideGraph` components.
- **Numeric measurements:** RMS RA/Dec/total, drift/min, PE peak-to-peak (+ period if
  found), jitter, and backlash **`bl_ms ± sigma_ms`** with its result-code caveat.
- **Per-recommendation before→after table** with a **checkbox per field** (selective apply):
  each row = `field · current → recommended · rationale`. "Apply selected" builds the body
  from only the checked rows (`guideAssistant.ts` merge helper) and PUTs.
- **"Open in tuning editor"** hands the recommended params straight into the existing
  `GuideSettingsDrawer` / `AlgoParams` editor (GuideView.tsx:408,595) for hand-tuning — the
  expert affordance, reusing what's already built.

### 4.3 Honest-disabled (§11.8)

The Run button uses the honest-disabled idiom (dim + `aria-disabled` + `title`, never native
`disabled`) — same as `AlgoParams` (GuideView.tsx:595-606) — for: no guider connected;
guider not native ("Guiding Assistant works with the AstroDeck native guider"); currently
guiding ("stop guiding first"); viewer without `control.guide` (`accessPhrase`).

### 4.4 `ui/src/lib/guideAssistant.ts` (PURE)

- `summarize(report) -> { headline, polarVerdict, tone }` — the novice card copy.
- `formatRecommendations(report) -> Row[]` — advanced before/after rows.
- `buildApplyBody(report, selectedFields?) -> GuideSettingsPutBody` — maps recommendations
  onto the PUT shape, running through the **existing** `validateGuideSettings` + `toSnake`
  (guideSettings.ts:205,228) so clamps + camel→snake stay single-sourced. `selectedFields`
  omitted ⇒ apply all (novice one-tap).

---

## 5. LEAN test plan (estimated **net-new ≈ 8–11 tests**)

Holistic, parametrized, reusing existing sim fixtures (`test_native_guider_e2e.py`,
`test_sim_guide_camera.py`) and the `guideSettings.test.ts` style. Pure logic carries the
load; the render shell is **not** DOM-tested.

**Python (~5–6 net-new):**
1. `test_guide_assistant_recommend.py` — **ONE** parametrized test over `recommend()`:
   rows = {high backlash, ~zero backlash, high drift/poor polar, calm seeing→tighter
   min-move, clear PE→PPEC-eligible, sanity/too-few→backlash floored to 0}. (~1 fn, ~6
   cases.)
2. Same file — `reduce_phaseA` slope/RMS/jitter correctness on a synthetic series with a
   known drift+sinusoid (1 fn).
3. Same file — `BacklashRun.compute` on a synthetic N/S trace: `VALID` + one `SANITY`/
   negative-clamp edge (1 parametrized fn).
4. `test_native_guider_assistant_e2e.py` — **ONE** integration test against the sim rig
   (seed `rig.guide_drift_px_s`, `guide_pe_amplitude_px`, and a sim backlash if added):
   run the assistant, assert a plausible report + non-empty recommendations, then assert
   `buildApplyBody`→`PUT /api/guide/settings` round-trips the seed into `GuideConfig`
   (1 fn).
5. Same file — refusal paths parametrized: refuses while guiding; 400 on a non-native
   guider; edge-guard halts before running the star off-frame (1 parametrized fn).

**UI (~3–4 net-new):**
6. `guideAssistant.test.ts` — **ONE** parametrized test: `summarize` copy bands +
   `buildApplyBody` maps a report onto a valid, clamped PUT body (reusing
   `validateGuideSettings`), + selective-apply merge honors `selectedFields` (1–2 fns).
7. Same file — an out-of-range/garbage recommendation is clamped by the shared validator
   (1 fn), pinning that `buildApplyBody` doesn't bypass `guideSettings` clamps.

Everything else (the async sample loop's pulse dispatch) is exercised by test #4 rather than
unit-mocked, keeping the count down. **No** DOM/render tests are added — consistent with the
"thin shell" rule.

---

## 6. Open decisions (each with a recommendation)

- **D1 — Star centroid vs FindPeak for backlash.** PHD2's GA uses `FindPeak` mode
  (phd2-guiding.md:58) for the backlash run; our `guide_star_find` is centroid-only.
  **Recommendation:** ship v1 with the centroid finder (adequate for a seed estimate; the
  sim has round stars); note FindPeak as a small future Rust star-find flag if real-mount
  trails prove noisy. Non-blocking.
- **D2 — Backlash `y_rate` source when uncalibrated.** **Recommendation:** use the declared
  `guide_rates()` rate (× scale) when no valid calibration is loaded, and the real
  calibration `yRate` when one is. Surface which was used in the report (confidence hint). A
  wildly-wrong declared rate only mis-scales the seed, which the user reviews before apply.
- **D3 — How aggressive should PPEC/algorithm recommendations be?** **Recommendation:**
  conservative — keep Hysteresis/ResistSwitch as the recommended default and only *offer*
  PPEC (RA) / Lowpass2 (Dec) as an **advanced, opt-in** suggestion with a rationale, never
  auto-selected in the novice one-tap. Avoids surprising a novice with a niche algorithm.
- **D4 — Apply then clear calibration?** An algorithm change usually wants a fresh
  calibration. **Recommendation:** when the applied set changes an algorithm, prompt (advanced)
  / auto-clear (novice, with a toast) via the existing `DELETE /api/guide/calibration`;
  when it changes only params/backlash, leave calibration intact.
- **D5 — Progress transport.** **Recommendation:** a dedicated `"guide_assistant"` bus
  channel (not overloading `"guide"`, which the live graph consumes) carrying
  `{phase, pct, message}`; final report via GET. Keeps the live guide contract untouched.
- **D6 — Adaptive controller now or later?** **Recommendation:** **later** (Rust v2, §7).
  v1's static seed captures ~90% of the benefit and ships with zero wheel risk.

---

## 7. Rust follow-up (v2, DEFERRED — clearly marked)

The **adaptive Dec backlash controller** (dossier §10.2) is the only piece needing Rust +
maturin. It is an in-engine state machine that, during guiding, watches the realized Dec
move vs the commanded move across reversals and **re-sizes `blc_pulse_ms`** within a
floor/ceiling (PHD2 `backlash_comp.cpp` `BLC_*` states; defaults floor 20 ms, ceiling
1.5×pulse, phd2-guiding.md:1590). It requires:

- `EngineConfig` fields: `blc_adaptive: bool`, `blc_floor_ms`, `blc_ceiling_ms` (+ parse in
  `astrodeck-native/src/lib.rs` alongside the existing `blc_pulse_ms`).
- A `BacklashComp` adaptation state added to the per-frame correction path (`engine.rs`
  around the existing static block, 1338-1356).
- A **maturin wheel rebuild** (exactly the PRO-12 Tier 2 T5 pattern) + Rust golden tests
  (`tests/blc_golden.rs` already exists per engine.rs:168) + the Python allowlist gaining
  the three keys in `_build_engine_config` (native.py:706-714).

This is a clean seam **because** v1 already surfaces `blc_pulse_ms` and the wizard seeds it —
the adaptive path just makes that seed self-tuning. Shipping v1 first de-risks it (the seed
gives the adaptive controller a good starting point).

---

## 8. Risks

- **Running the star off the sensor.** The Phase-B N/S pulses walk Dec deliberately. The
  `OutOfRoom(search_region)` guard is **mandatory**; without it a large-backlash mount could
  lose the star mid-run. Covered by test #5.
- **Mount safety envelope.** Raw pulses bypass the engine's duration clamps. Cap each
  measurement pulse at a sane ceiling (≤ ~1 s, PHD2's `MAX_NORTH_PULSES`/count logic) and
  never move while a slew/park is in flight (the guider already owns the mount during a
  guide session; the assistant runs in the same exclusive window).
- **Bad declared guide rate (D2).** Only mis-scales the *seed*, which the user reviews before
  apply — not silently applied to a live loop.
- **User applies then never recalibrates.** Mitigated by D4's clear-calibration prompt.
- **Provider confusion.** A PHD2/NINA user expects "Guiding Assistant" (PHD2 has its own).
  The honest-disabled title makes it explicit this drives the *native* guider only.
- **Test-count creep.** Held down by parametrization + one sim e2e instead of per-phase
  mocks (§5).

---

## 9. Why this is implementable as specced

Every apply target already exists and is validated/persisted/forwarded (§0 table). The
measurement reuses `_expose`, `guide_star_find`, `pulse_guide`, and `guide_rates` that
`NativeGuider` already owns. The long-running-task + progress + capability-probe patterns are
all copied from existing guide routes. The UI reuses `GuideScatter`/`GuideGraph`,
`validateGuideSettings`, `toSnake`, `GuideSettingsDrawer`, the honest-disabled idiom, and the
`PUT /api/guide/settings` + `DELETE /api/guide/calibration` endpoints. The only genuinely new
code is one pure Python module, one `NativeGuider` method, three thin routes, one pure UI
lib, and one thin panel — no schema migration, no Rust, no wheel.
```
