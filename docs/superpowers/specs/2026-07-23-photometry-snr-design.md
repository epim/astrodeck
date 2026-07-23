# Photometry / SNR core + integration estimator + beginner suggest-settings

**Features:** F-A (foundation, pure core) · PRO-6 (pro: integration/SNR estimator) · NOV-4 (novice: capture presets + Suggest settings)
**Date:** 2026-07-23
**Branch:** feat/wave0-remainder

---

## 1. Design

### 1.1 Goal

Three coupled features over one shared pure core:

- **F-A** — a photometry/SNR core that turns a measured sky-background (ADU) + camera read noise (e-) + gain (e-/ADU) into per-sub noise, a **sky-limited sub length**, a read-noise-dominance fraction, per-sub SNR, and stacking arithmetic. This is the tested heart; PRO-6 and NOV-4 are thin consumers.
- **PRO-6** — a forward estimator surfaced in Sequence (projected total integration, per-filter breakdown, per-step sky-limited advisory) and Monitor (a live "sub quality" tile: per-sub noise, read-noise fraction, sky-limited vs actual, and "N more subs to ×2 stack SNR").
- **NOV-4** — one-tap capture presets (Nebula-broadband / Galaxy / Cluster / First-light) that set exposure/gain/offset/binning in the Capture view, plus a **Suggest settings** action that computes a sensible sub length from the camera read noise + the live sky background.

### 1.2 Current-state seams (every one read at file:line)

**Read noise — how it's obtained, and (crucially) that it is NOT stored.**
- `server/astrodeck/imaging/readnoise.py:14` `read_noise_e(frames, egain_e_per_adu) -> float` — pure: `sigma(ADU) * egain`, difference-method for ≥2 bias frames (`readnoise.py:19-24`). `compare_modes` at `:27`.
- `server/tests/test_camera_readnoise.py:11` — the only caller; feeds synthetic bias frames. **There is no persistence**: no config field, no store, no status payload holds a measured read-noise value. Read noise is a compute-on-demand from bias frames, never saved. (Grep for `read_noise` across the repo returns only `readnoise.py`, its test, `sim.py`, and docs.)

**Gain (e-/ADU) — present only inside native adapters, NOT on the wire (brief's "PRO-2 added `CameraFrame.egain_e_per_adu`" is not in the code — see Surprises).**
- `server/astrodeck/devices/cameras/player_one.py:42` `self._egain = 0.0`, populated at `:75` `self._egain = float(self._sdk.get_egain(...))`, exposed only via `capabilities().extra["egain"]` at `player_one.py:63`.
- `server/astrodeck/devices/cameras/zwo_asi.py:51` — same `extra={"egain": p.egain}` pattern.
- `server/astrodeck/devices/cameras/engine.py:48-59` — the engine copies `sensor_width/pixel_size_um/max_gain/...` off `caps` on connect but **does not** copy `caps.extra["egain"]`. So egain never reaches `Camera`, the hub status payload, or the UI.
- `server/astrodeck/devices/base.py:44-64` `CameraFrame` fields: `data, exposure_s, gain, offset, binning, bayer_pattern, temperature_c, timestamp, ... full_well, data_is_linear` — **no egain field**.
- `server/astrodeck/hub.py:2354-2361` — the `status.camera` payload = `temperature, can_cool, has_dew_heater, width, height, max_gain, max_bin, cooler`. **No egain.**

**Sky-background ADU — IS on the wire, via the preview stats (linear frames only).**
- `server/astrodeck/imaging/processing.py:174-181` `frame_stats(data)` = `{min,max,mean,median,std}`. `median` = the robust sky-background ADU.
- `server/astrodeck/hub.py:1480` `"stats": frame_stats(data)` inside `_publish_preview` (`hub.py:1457`). For raw/linear backends (sim/Alpaca) this median is a true linear background ADU; for NINA (`hub.py:1497`, `data_is_linear=False`) it is a decoded-8-bit value and must not be used for photometry.
- `ui/src/types.ts:190` `PreviewInfo.stats: {min,max,mean,median,std}`; `:194-206` also carries `exposure_s, gain, binning, data_is_linear, full_well`. So the client already has `median, exposure_s, gain, binning, data_is_linear` per frame.
- Background estimators elsewhere (not the wire, but confirm the median-as-sky convention): `imaging/stars.py:57-59` `bg=median(img)`, `imaging/clouds.py:65-68` `_background`.
- `ui/src/types.ts:180-186` `StarMark {x,y,hfr,ecc?,theta?}` — **no flux/peak** → a per-star signal is NOT available client-side (bears on PRO-6 per-sub SNR; see §4 Open decisions).

**Capture fields to preset + a home for Suggest.**
- `ui/src/views/CaptureView.tsx:59-62` local state `exposure/gain/offset/binning` with setters; `:242-254` `onSingle/onLoop` build `body` from them. The existing **"Match last lights"** button (`CaptureView.tsx:368-379`) is the exact precedent for a one-tap prefill that calls `setExposure/setGain/setOffset/setBinning`.
- `ui/src/views/CaptureView.tsx:300-337` the Exposure `Panel` — where the preset chips + Suggest button + a compact photometry-profile input go.
- Honest-disabled idiom already used in this file: `CaptureView.tsx:383-388` (dim + `aria-disabled` + `title` + lock glyph, never native `disabled`).

**Where the integration/SNR readouts live.**
- Sequence: `ui/src/views/SequenceView.tsx:248-250` per-target `targetFrames/targetSeconds` + plan `totalFrames`; `:742` per-step `((count*exposure)/60)` minutes; the NOV-5 starter-template row at `:690-708` is the layout precedent for a per-target advisory strip. Plan-wide integration already surfaces in `PlanLibraryPanel` (SequenceView.tsx:842).
- Monitor: `ui/src/views/MonitorView.tsx:110` `preview = usePreview()`; `:148-152` `previewMeta`; `:457-516` the Progress panel; `:538-552` the "Last frame" tile. `seq.progress.frames_done/frames_total/rejected` at `MonitorView.tsx:477`. A new "Sub quality" `Panel` slots into the 12-col grid alongside these.
- Post-run per-filter integration already exists (report, past-tense): `server/astrodeck/sequence/report.py:73` `FilterBreakdown`, `:96-99` `SessionReport.by_filter`. PRO-6 is the **forward** analogue (planning + live), so it is a new client-side rollup, not a report change.

**Store + persistence idioms.**
- `ui/src/store.ts:1358` `useStatus`, `:1369` `usePreview`, `:1470` `useCamera` (`s.status?.camera`), `:1466` `useSeq`. `site` slice at `:469/661/792`. localStorage-persisted UI slices: `store.ts:135` `PLAN_KEY`, `:236` `PREVIEW_KEY`, `:323-327` haptics/touch keys — the pattern a `photometry` slice follows.
- `ui/src/lib/__tests__/eta.test.ts:9-43` — the inline-assert harness idiom (no jsdom) run via `npx tsx`. `ui/src/lib/exposure.ts:18` `isExposureValueInvalid(n)` — reused to bound preset/suggestion values.

### 1.3 Approach

**Placement of the core: client-side pure TS.** All essential inputs for the always-on outputs (sky-limited sub length, read-noise fraction, sub-length verdict, integration projection) are either already on the wire (`preview.stats.median`, `exposure_s`, `binning`, `data_is_linear`) or are small per-camera constants (egain, read noise, bias). No server round-trip, no numpy — the math is a handful of closed-form scalars. So F-A ships as `ui/src/lib/photometry.ts`, tsx-tested, matching the `eta.ts`/`optics.ts` precedent. **No server endpoint is required for the core.**

**Sourcing egain + read noise: a small persisted client "photometry profile."** Because neither egain nor read noise is on the wire or in config today, the consumers read a `photometry` store slice `{egain, readNoiseE, biasAdu}` persisted to `localStorage` (key `astrodeck-photometry`), entered once by the user (the read-noise harness or the camera datasheet supplies the numbers). When any of egain/readNoiseE is 0/unknown, the SNR/suggest surfaces show an **honest prompt** ("add read noise + gain to enable SNR") — never a wrong number and never a native-disabled control. Integration projection needs none of these and always works. A small **optional** server task (Task 7) auto-prefills `egain` from the value native adapters already know (`caps.extra["egain"]`); the client prefers it when present and falls back to the manual field otherwise. This keeps the whole feature shippable with zero hard server dependency.

**F-A core outputs (all pure, all tested):**
- `subNoise(skyE, readNoiseE, darkE)` → `{skyE, darkE, readE, totalNoiseE, readFraction}` — total sub noise `sqrt(sky+dark+read²)` and read-noise dominance.
- `skyElectronsPerSub(medianAdu, biasAdu, egain)`, `skyRateEPerSec(skyE, exposureS)`.
- `skyLimitedSubSeconds(readNoiseE, skyRate, factor=10)` → sky-limited sub length (Robin Glover / SharpCap criterion: sky-noise variance ≥ `factor`× read-noise variance).
- `subLengthVerdict(actualS, skyLimitedS)` → `too_short | good | long | unknown`.
- `subSnr(signalE, noise)`, `stackedSnr(perSubSnr, n)`, `subsForStackedSnr(perSubSnr, targetSnr)`, `moreSubsForSnrMultiple(have, multiple)` (the exact √N stacking law — needs only frame counts).
- `projectedIntegrationSeconds(steps)`, `integrationByFilter(steps)`.
- `suggestSubLength(input)` → the NOV-4 assembler (sky-limited, clamped to `[min,max]`, with a reason string).

**NOV-4:** `CAPTURE_PRESETS` (pure data) + a preset-chip row and a "Suggest settings" button in the Capture Exposure panel, plus a compact photometry-profile input. Presets reuse the exact `setExposure/setGain/setOffset/setBinning` pattern of "Match last lights". Suggest reads `usePreview().stats.median` (guarded on `data_is_linear`) + the profile → `suggestSubLength` → sets exposure + toast.

**PRO-6:** Sequence gets a per-target/plan per-filter integration line (`integrationByFilter`) and a per-step sky-limited advisory (when a recent linear preview + profile exist). Monitor gets a "Sub quality" panel reading `usePreview` + the profile: total noise, read-noise fraction, sky-limited vs actual, and "N more subs to ×2 stack SNR" from `progress.frames_done`.

---

## 2. Global Constraints (verbatim)

- **Privacy.** The operator's real backyard latitude/longitude and its private label must **NEVER** appear in code, tests, or docs (this file deliberately does not reproduce those secret values — stating the rule must not leak them). The site default in code is name **"My Observatory"** with latitude/longitude **0.0 / 0.0** (`config.py:56-59`), `is_default=True`. This feature does not touch site coordinates.
- **Never `git add -A`.** Stage only the specific files each task names.
- **UI gate:** `cd ui && npx tsc -b` must pass with zero errors before any UI task is considered done.
- **No jsdom.** Pure logic is tested via `npx tsx` inline-assert files under `ui/src/lib/__tests__/` (idiom: `ui/src/lib/__tests__/eta.test.ts`). Thin render is verified by the typechecker only.
- **Backend tests:** run from the repo root with `server/.venv/Scripts/pytest.exe`, single-process `-n0`.
- **Client toasts:** via `useStore.getState().enqueueToast` (the store's `showToast` wrapper used in CaptureView is equivalent — either is acceptable; prefer the existing `showToast` already imported in a view).
- **Honest-disabled (§11.8):** a control that cannot act is dimmed + locked + `aria-disabled` + `title`, **never** the native `disabled` attribute. Precedent: `CaptureView.tsx:383-388`.
- **Do not disrupt astrotown** (the deployed box): no config-schema breakage, no removal of existing status fields; all additions are additive and default-inert.

---

## 3. TDD Implementation Plan

### Interfaces (exact signatures)

```ts
// ui/src/lib/photometry.ts  (F-A — the tested core)
export const SKY_LIMIT_FACTOR = 10;
export const SUB_LONG_MULT = 4;

export interface SubNoise {
  skyE: number; darkE: number; readE: number;
  totalNoiseE: number; readFraction: number;
}
export function subNoise(skyE: number, readNoiseE: number, darkE?: number): SubNoise;
export function skyElectronsPerSub(medianAdu: number, biasAdu: number, egain: number): number;
export function skyRateEPerSec(skyE: number, exposureS: number): number;
export function skyLimitedSubSeconds(readNoiseE: number, skyRateEPerSec: number, factor?: number): number | null;

export type SubVerdict = "too_short" | "good" | "long" | "unknown";
export function subLengthVerdict(actualS: number, skyLimitedS: number | null): SubVerdict;

export function subSnr(signalE: number, noise: SubNoise): number;
export function stackedSnr(perSubSnr: number, n: number): number;
export function subsForStackedSnr(perSubSnr: number, targetSnr: number): number | null;
export function moreSubsForSnrMultiple(have: number, multiple: number): number;

export function projectedIntegrationSeconds(steps: { exposure_s: number; count: number }[]): number;
export function integrationByFilter(
  steps: { filter: string | null; exposure_s: number; count: number }[],
): { filter: string; seconds: number }[];

export interface SuggestInput {
  medianAdu: number; biasAdu: number; egain: number; readNoiseE: number;
  exposureS: number; minSubS?: number; maxSubS?: number; factor?: number;
}
export interface Suggestion {
  ok: boolean; skyLimitedS: number | null; suggestedS: number | null;
  readFraction: number | null; reason: string;
}
export function suggestSubLength(inp: SuggestInput): Suggestion;

// ui/src/lib/capturePresets.ts  (NOV-4 — pure data)
export interface CapturePreset {
  id: string; label: string; blurb: string;
  exposure_s: number; gain: number; offset: number; binning: number;
}
export const CAPTURE_PRESETS: CapturePreset[];

// ui/src/store.ts  (photometry profile slice)
export interface PhotometryProfile { egain: number; readNoiseE: number; biasAdu: number; }
// state: photometry: PhotometryProfile
// action: setPhotometry: (p: Partial<PhotometryProfile>) => void
export const usePhotometry: () => PhotometryProfile;
```

---

### Task 1 — F-A pure core `photometry.ts` + tests  ·  **Opus**

**Impl tier: Opus.** Justification: the numeric correctness of the noise model (shot vs read in quadrature), the sky-limited criterion, and the √N stacking arithmetic are the load-bearing, subtle heart of all three features — a wrong variance term or a swapped ratio silently mis-advises every sub. Everything downstream is mechanical.

**Files:** `ui/src/lib/photometry.ts` (new), `ui/src/lib/__tests__/photometry.test.ts` (new).

**Step 1a — write `photometry.ts`** exactly:

```ts
// photometry.ts — F-A: the pure photometry/SNR core (no React, no DOM; tsx-testable).
// The tested heart shared by NOV-4 (Suggest) and PRO-6 (integration/SNR estimator).
// Model follows the standard CMOS sub-exposure treatment (Robin Glover / SharpCap
// Smart Histogram): per-sub variance = sky + dark shot noise + read noise²; a sub is
// "sky-limited" once sky-noise variance dominates read-noise variance; stacked SNR ∝ √N.

// Sky-limited factor: require sky-noise variance >= FACTOR * read-noise variance. At
// 10, total sub noise sits within ~sqrt(1+1/10)-1 ≈ 4.9% of the read-noise-free ideal
// (the usual "good enough" target). Exposed so callers can tighten/loosen it.
export const SKY_LIMIT_FACTOR = 10;
// Verdict bands (multiples of the sky-limited length): < 1x => read-noise-limited
// (too short); [1x, SUB_LONG_MULT] => good; > SUB_LONG_MULT => longer than needed.
export const SUB_LONG_MULT = 4;

export interface SubNoise {
  skyE: number;         // sky signal electrons in the sub
  darkE: number;        // dark-current electrons in the sub
  readE: number;        // read-noise electrons (constant per sub)
  totalNoiseE: number;  // sqrt(skyE + darkE + readE^2)
  readFraction: number; // readE^2 / totalNoiseE^2  (0..1) — read-noise dominance
}

/** Total per-sub noise (e-) from shot (sky+dark) and read terms in quadrature. */
export function subNoise(skyE: number, readNoiseE: number, darkE = 0): SubNoise {
  const sky = Math.max(0, skyE);
  const dark = Math.max(0, darkE);
  const read = Math.max(0, readNoiseE);
  const varTotal = sky + dark + read * read;
  return {
    skyE: sky, darkE: dark, readE: read,
    totalNoiseE: Math.sqrt(varTotal),
    readFraction: varTotal > 0 ? (read * read) / varTotal : 1,
  };
}

/** Sky signal electrons in one sub from a light-frame background median (ADU),
 *  the bias pedestal (ADU) and the sensor gain (e-/ADU). Clamped >= 0. */
export function skyElectronsPerSub(medianAdu: number, biasAdu: number, egain: number): number {
  return Math.max(0, (medianAdu - biasAdu) * egain);
}

/** Sky rate (e-/s) from the sub's sky electrons and its exposure. 0 when the
 *  exposure is non-positive (guards a downstream divide-by-zero). */
export function skyRateEPerSec(skyE: number, exposureS: number): number {
  return exposureS > 0 ? Math.max(0, skyE) / exposureS : 0;
}

/** Sky-limited sub length (s): t = FACTOR * readNoise^2 / skyRate. null when the
 *  sky rate is non-positive (unmeasurable — e.g. no linear light frame yet). */
export function skyLimitedSubSeconds(
  readNoiseE: number, skyRateEPerSec: number, factor = SKY_LIMIT_FACTOR,
): number | null {
  if (!(skyRateEPerSec > 0)) return null;
  const read = Math.max(0, readNoiseE);
  return (factor * read * read) / skyRateEPerSec;
}

export type SubVerdict = "too_short" | "good" | "long" | "unknown";

/** Compare an actual sub length to the sky-limited length. */
export function subLengthVerdict(actualS: number, skyLimitedS: number | null): SubVerdict {
  if (skyLimitedS == null || !(actualS > 0)) return "unknown";
  if (actualS < skyLimitedS) return "too_short";
  if (actualS > skyLimitedS * SUB_LONG_MULT) return "long";
  return "good";
}

/** Per-sub SNR of a signal (electrons in the sub) against the total sub noise. */
export function subSnr(signalE: number, noise: SubNoise): number {
  return noise.totalNoiseE > 0 ? Math.max(0, signalE) / noise.totalNoiseE : 0;
}

/** Stacked SNR after n subs (shot/read-noise limited): perSubSnr * sqrt(n). */
export function stackedSnr(perSubSnr: number, n: number): number {
  return n > 0 ? perSubSnr * Math.sqrt(n) : 0;
}

/** Subs needed for a target STACKED snr given the per-sub snr. Ceil; null when the
 *  per-sub snr is non-positive (no signal / unreachable). */
export function subsForStackedSnr(perSubSnr: number, targetSnr: number): number | null {
  if (!(perSubSnr > 0) || !(targetSnr > 0)) return null;
  return Math.ceil((targetSnr / perSubSnr) ** 2);
}

/** Extra subs to raise a stack of `have` subs to `multiple`x its current SNR.
 *  SNR ∝ sqrt(N) => N_target = have * multiple^2. Exact; needs only counts. */
export function moreSubsForSnrMultiple(have: number, multiple: number): number {
  if (have <= 0 || multiple <= 1) return 0;
  return Math.ceil(have * multiple * multiple) - have;
}

/** Projected total integration (s) for a set of steps (count x exposure). */
export function projectedIntegrationSeconds(steps: { exposure_s: number; count: number }[]): number {
  return steps.reduce((a, s) => a + Math.max(0, s.exposure_s) * Math.max(0, s.count), 0);
}

/** Per-filter integration rollup (s), grouping by filter label (null => "—").
 *  Insertion order preserved so the UI list is deterministic. */
export function integrationByFilter(
  steps: { filter: string | null; exposure_s: number; count: number }[],
): { filter: string; seconds: number }[] {
  const order: string[] = [];
  const acc = new Map<string, number>();
  for (const s of steps) {
    const key = s.filter ?? "—";
    if (!acc.has(key)) order.push(key);
    acc.set(key, (acc.get(key) ?? 0) + Math.max(0, s.exposure_s) * Math.max(0, s.count));
  }
  return order.map((f) => ({ filter: f, seconds: acc.get(f) ?? 0 }));
}

export interface SuggestInput {
  medianAdu: number; biasAdu: number; egain: number; readNoiseE: number;
  exposureS: number; minSubS?: number; maxSubS?: number; factor?: number;
}
export interface Suggestion {
  ok: boolean; skyLimitedS: number | null; suggestedS: number | null;
  readFraction: number | null; reason: string;
}

/** NOV-4 assembler: sky-limited sub length from a measured background, clamped to
 *  [minSubS, maxSubS] and rounded to a whole second. `ok:false` (with a reason)
 *  when egain/read-noise/exposure are unusable — the caller shows the honest prompt. */
export function suggestSubLength(inp: SuggestInput): Suggestion {
  const { medianAdu, biasAdu, egain, readNoiseE, exposureS } = inp;
  const minSubS = inp.minSubS ?? 5;
  const maxSubS = inp.maxSubS ?? 600;
  if (!(egain > 0) || !(readNoiseE > 0)) {
    return { ok: false, skyLimitedS: null, suggestedS: null, readFraction: null,
      reason: "Add your camera's gain (e-/ADU) and read noise (e-) to enable Suggest." };
  }
  if (!(exposureS > 0)) {
    return { ok: false, skyLimitedS: null, suggestedS: null, readFraction: null,
      reason: "No linear preview frame yet — take one light frame first." };
  }
  const skyE = skyElectronsPerSub(medianAdu, biasAdu, egain);
  const rate = skyRateEPerSec(skyE, exposureS);
  const skyLimitedS = skyLimitedSubSeconds(readNoiseE, rate, inp.factor);
  if (skyLimitedS == null) {
    return { ok: false, skyLimitedS: null, suggestedS: null, readFraction: null,
      reason: "Sky background reads at the bias floor — can't estimate a sky-limited sub." };
  }
  const suggestedS = Math.round(Math.min(maxSubS, Math.max(minSubS, skyLimitedS)));
  const readFraction = subNoise(skyE * (suggestedS / exposureS), readNoiseE).readFraction;
  return { ok: true, skyLimitedS, suggestedS, readFraction,
    reason: `Sky-limited near ${Math.round(skyLimitedS)}s at this sky brightness.` };
}
```

**Step 1b — write `photometry.test.ts`** (mirror `eta.test.ts` harness: local `test/eq/near/assert`, a `console.log` tally, `export const result`). Cover at minimum:

```ts
import {
  subNoise, skyElectronsPerSub, skyRateEPerSec, skyLimitedSubSeconds,
  subLengthVerdict, subSnr, stackedSnr, subsForStackedSnr, moreSubsForSnrMultiple,
  projectedIntegrationSeconds, integrationByFilter, suggestSubLength,
  SKY_LIMIT_FACTOR, SUB_LONG_MULT,
} from "../photometry";
// (harness copied from eta.test.ts)

// subNoise: read-dominated when sky is tiny
test("subNoise: read dominates a short sub", () => {
  const n = subNoise(4, 2);            // sky=4 e-, read=2 e- => var=8, read²=4
  near(n.totalNoiseE, Math.sqrt(8), 1e-9, "total");
  near(n.readFraction, 0.5, 1e-9, "read fraction");
});
test("subNoise: sky-limited sub => read fraction small", () => {
  const n = subNoise(400, 2);         // var=404, read²=4
  assert(n.readFraction < 0.02, "read fraction tiny when sky-limited");
});
test("subNoise: clamps negatives", () => {
  const n = subNoise(-5, -3);
  eq(n.skyE, 0, "sky clamp"); eq(n.readE, 0, "read clamp"); eq(n.readFraction, 1, "0/0 => 1");
});
// skyElectronsPerSub / rate
test("skyElectronsPerSub: (median-bias)*egain, clamped", () => {
  near(skyElectronsPerSub(1200, 1000, 0.25), 50, 1e-9, "50 e-");
  eq(skyElectronsPerSub(900, 1000, 0.25), 0, "below bias => 0");
});
test("skyRateEPerSec: divide guard", () => {
  near(skyRateEPerSec(50, 10), 5, 1e-9, "5 e-/s");
  eq(skyRateEPerSec(50, 0), 0, "zero exposure => 0");
});
// sky-limited length: t = factor*RN²/rate
test("skyLimitedSubSeconds: factor*RN^2/rate", () => {
  near(skyLimitedSubSeconds(2, 5)!, (10 * 4) / 5, 1e-9, "8s");   // default factor 10
  near(skyLimitedSubSeconds(2, 5, 3)!, (3 * 4) / 5, 1e-9, "custom factor");
  eq(skyLimitedSubSeconds(2, 0), null, "no sky => null");
});
// verdict bands
test("subLengthVerdict: bands around sky-limited", () => {
  eq(subLengthVerdict(30, 60), "too_short", "under");
  eq(subLengthVerdict(90, 60), "good", "within long-mult");
  eq(subLengthVerdict(60 * SUB_LONG_MULT + 1, 60), "long", "beyond");
  eq(subLengthVerdict(120, null), "unknown", "no estimate");
});
// SNR + stacking
test("subSnr + stackedSnr: √N growth", () => {
  const n = subNoise(96, 2);          // total = sqrt(100) = 10
  near(subSnr(50, n), 5, 1e-9, "per-sub 5");
  near(stackedSnr(5, 4), 10, 1e-9, "×2 after 4 subs");
});
test("subsForStackedSnr: (target/perSub)^2 ceil", () => {
  eq(subsForStackedSnr(5, 20), 16, "16 subs for 20 from 5");
  eq(subsForStackedSnr(0, 20), null, "no signal => null");
});
test("moreSubsForSnrMultiple: exact √N law", () => {
  eq(moreSubsForSnrMultiple(30, 2), 90, "×2 => 4× total => +90");
  eq(moreSubsForSnrMultiple(30, 1), 0, "×1 => none");
});
// integration
test("projectedIntegrationSeconds + integrationByFilter", () => {
  const steps = [
    { filter: "L", exposure_s: 120, count: 60 },
    { filter: "R", exposure_s: 180, count: 10 },
    { filter: "L", exposure_s: 60, count: 5 },
    { filter: null, exposure_s: 30, count: 2 },
  ];
  eq(projectedIntegrationSeconds(steps), 120*60 + 180*10 + 60*5 + 30*2, "sum");
  const by = integrationByFilter(steps);
  eq(by[0].filter, "L", "L first (insertion order)");
  eq(by[0].seconds, 120*60 + 60*5, "L merged");
  eq(by[3].filter, "—", "null bucket");
});
// suggest assembler
test("suggestSubLength: happy path clamps + rounds", () => {
  const s = suggestSubLength({ medianAdu: 1200, biasAdu: 1000, egain: 0.25,
    readNoiseE: 2, exposureS: 10 });
  // skyE=50 => rate=5 e-/s => sky-limited=8s => clamped to min 5..600 => 8
  eq(s.ok, true, "ok"); eq(s.suggestedS, 8, "8s");
});
test("suggestSubLength: missing profile => honest not-ok", () => {
  const s = suggestSubLength({ medianAdu: 1200, biasAdu: 1000, egain: 0,
    readNoiseE: 2, exposureS: 10 });
  eq(s.ok, false, "not ok"); assert(s.reason.includes("gain"), "reason mentions gain");
});
```

**Verify:**
- `cd ui && npx tsx src/lib/__tests__/photometry.test.ts` → expected last line `photometry.test: <N>/<N> passed` with `0` failures (N = number of `test(...)` above).
- `cd ui && npx tsc -b` → no output, exit 0.

---

### Task 2 — NOV-4 preset data `capturePresets.ts` + tiny test  ·  **Sonnet**

**Impl tier: Sonnet (mechanical).** Pure data table + a guard test; no subtlety.

**Files:** `ui/src/lib/capturePresets.ts` (new), `ui/src/lib/__tests__/capturePresets.test.ts` (new).

**Step 2a — `capturePresets.ts`:**

```ts
// capturePresets.ts — NOV-4: one-tap beginner capture presets for the Capture view.
// Pure data; CaptureView maps a chip row over CAPTURE_PRESETS and applies a preset
// via the existing setExposure/setGain/setOffset/setBinning setters (the same path
// as "Match last lights", CaptureView.tsx:368). No new state shape.
export interface CapturePreset {
  id: string;
  label: string;
  blurb: string;
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
}

// Gain 100 / offset 30 mirror SequenceView.DEFAULT_STEP (SequenceView.tsx:28) so a
// preset-applied capture matches a hand-built plan step except where the intent
// differs. First-light uses a short, higher-gain frame for fast framing/focus.
export const CAPTURE_PRESETS: CapturePreset[] = [
  { id: "nebula-broadband", label: "Nebula (broadband)",
    blurb: "Faint broadband nebulosity — long subs, moderate gain.",
    exposure_s: 180, gain: 100, offset: 30, binning: 1 },
  { id: "galaxy", label: "Galaxy",
    blurb: "Small bright cores with faint arms — medium subs.",
    exposure_s: 120, gain: 100, offset: 30, binning: 1 },
  { id: "cluster", label: "Cluster",
    blurb: "Bright stars — short subs keep cores from clipping.",
    exposure_s: 60, gain: 100, offset: 30, binning: 1 },
  { id: "first-light", label: "First light",
    blurb: "Quick, high-gain frames to check framing and focus.",
    exposure_s: 5, gain: 200, offset: 30, binning: 1 },
];
```

**Step 2b — `capturePresets.test.ts`** (same harness): assert ids are unique, every `exposure_s` passes `!isExposureValueInvalid` (import from `../exposure`), gain/offset/binning are finite ≥ 0, binning ≥ 1.

**Verify:** `cd ui && npx tsx src/lib/__tests__/capturePresets.test.ts` → `capturePresets.test: N/N passed`; `cd ui && npx tsc -b` → exit 0.

---

### Task 3 — photometry profile store slice  ·  **Sonnet**

**Impl tier: Sonnet (mechanical).** Follows the existing localStorage-slice pattern (`store.ts:236` PREVIEW_KEY, `:323` HAPTICS_KEY) exactly.

**Files:** `ui/src/store.ts` (edit).

**Steps:**
1. Add `const PHOTOMETRY_KEY = "astrodeck-photometry";` near the other keys (`store.ts:135`/`:236`).
2. Add a `loadPhotometry()` helper (try/catch localStorage → `{egain, readNoiseE, biasAdu}`; default `{egain:0, readNoiseE:0, biasAdu:0}` — all inert). Mirror `loadThumbBrightness`'s shape.
3. Add `PhotometryProfile` to the store type + initial state `photometry: loadPhotometry()`.
4. Add `setPhotometry: (p) => set((s) => { const next = { ...s.photometry, ...p }; try { localStorage.setItem(PHOTOMETRY_KEY, JSON.stringify(next)); } catch {} return { photometry: next }; })`.
5. Export `export const usePhotometry = () => useStore((s) => s.photometry);` near `usePreview` (`store.ts:1369`).

**Step 3b — round-trip test** append cases to the existing `ui/src/lib/__tests__/` store-adjacent test only if one imports the store cleanly; otherwise assert the pure `loadPhotometry`/merge logic by extracting it is unnecessary — a `tsc -b` pass plus a 3-line `npx tsx` inline check of the default shape is sufficient (store construction pulls React; keep the tsx check to the exported default object, not a full store mount).

**Verify:** `cd ui && npx tsc -b` → exit 0. (No jsdom; the slice is exercised for real by Tasks 4–6.)

---

### Task 4 — NOV-4 Capture UI: presets + Suggest + profile inputs  ·  **Sonnet**

**Impl tier: Sonnet (mechanical).** Thin render over Task 1/2/3; the only logic (`suggestSubLength`) is already tested. Verified by the typechecker.

**Files:** `ui/src/views/CaptureView.tsx` (edit).

**Steps:**
1. Imports: `CAPTURE_PRESETS` from `../lib/capturePresets`; `suggestSubLength` from `../lib/photometry`; `usePhotometry, usePreview` (add `usePreview` — CaptureView uses `useLivePreviewId` today but needs the full `PreviewInfo` for `stats.median`; `usePreview` at `store.ts:1369`).
2. Below the Binning field (`CaptureView.tsx:337`), add a **preset chip row** — one button per `CAPTURE_PRESETS`, `min-h-[44px]`, `disabled={!canCapture}` semantics via the honest-disabled idiom when read-only, each `onClick` calling `setExposure(String(p.exposure_s)); setGain(String(p.gain)); setOffset(String(p.offset)); setBinning(String(p.binning));` then `showToast("info", \`Preset: ${p.label}\`)`. Mirror the NOV-5 starter row markup (`SequenceView.tsx:692-708`).
3. Add a compact **"Camera photometry"** sub-section (3 small numeric `Field`s bound to `usePhotometry()` + `setPhotometry`): gain e-/ADU, read noise e-, bias ADU. Prefill gain from `status.camera?.egain` (Task 7) when the profile's egain is 0 and the status value is present — read-only hint "from camera" in that case.
4. Add a **"Suggest settings"** button. Compute:
   ```ts
   const prof = usePhotometry();
   const preview = usePreview();
   const linearMedian = preview && preview.data_is_linear ? preview.stats.median : null;
   const canSuggest = prof.egain > 0 && prof.readNoiseE > 0 && linearMedian != null;
   const onSuggest = () => {
     const s = suggestSubLength({
       medianAdu: linearMedian!, biasAdu: prof.biasAdu, egain: prof.egain,
       readNoiseE: prof.readNoiseE, exposureS: preview!.exposure_s,
     });
     if (!s.ok || s.suggestedS == null) { showToast("warn", s.reason); return; }
     setExposure(String(s.suggestedS));
     showToast("success", `Suggested ${s.suggestedS}s — ${s.reason}`);
   };
   ```
   When `!canSuggest`, render the button via the **honest-disabled** idiom (`CaptureView.tsx:383-388`: dim + `aria-disabled` + `title` explaining what's missing — "needs a linear preview + camera gain/read noise"), never native `disabled`.

**Verify:** `cd ui && npx tsc -b` → exit 0. Manual: presets set the four fields; Suggest sets exposure after a sim light frame; Suggest is honest-locked before a frame or before the profile is filled.

---

### Task 5 — PRO-6 Sequence: per-filter integration + sky-limited advisory  ·  **Sonnet**

**Impl tier: Sonnet (mechanical).** Thin render over `integrationByFilter`/`subLengthVerdict` (tested). No new math in the view.

**Files:** `ui/src/views/SequenceView.tsx` (edit).

**Steps:**
1. Import `integrationByFilter, projectedIntegrationSeconds, skyElectronsPerSub, skyRateEPerSec, skyLimitedSubSeconds, subLengthVerdict` from `../lib/photometry`; `usePhotometry, usePreview`.
2. In each target card header area (near the per-target `targetFrames/targetSeconds`, `SequenceView.tsx:248-249`), render a per-filter integration line from `integrationByFilter(t.steps)` → e.g. `L 2.0h · R 0.5h` using the existing `Math.floor(min/60)h Math.round(min%60)m` format (SequenceView.tsx:801).
3. Per **step** row (`SequenceView.tsx:709-761`), add a sky-limited advisory chip when a recent **linear** preview + a filled profile exist:
   ```ts
   const prof = usePhotometry(); const preview = usePreview();
   const skyLimitedS = (preview && preview.data_is_linear && prof.egain > 0 && prof.readNoiseE > 0)
     ? skyLimitedSubSeconds(prof.readNoiseE,
         skyRateEPerSec(skyElectronsPerSub(preview.stats.median, prof.biasAdu, prof.egain),
           preview.exposure_s))
     : null;
   const verdict = subLengthVerdict(s.exposure_s, skyLimitedS);
   ```
   Render a tiny tone-coded chip: `too_short` → warn "read-noise limited · sky-limited ≈ Ns", `good` → dim check, `long` → dim "longer than needed", `unknown` → render nothing (no preview/profile — never a misleading chip).
4. Do NOT change the run/plan totals or the report — this is additive display only.

**Verify:** `cd ui && npx tsc -b` → exit 0. Manual: with sim connected + a light frame + a filled profile, short steps show the read-noise-limited advisory; with no profile, no chip appears.

---

### Task 6 — PRO-6 Monitor: "Sub quality" tile  ·  **Sonnet**

**Impl tier: Sonnet (mechanical).** Thin render over the tested core + live store slices.

**Files:** `ui/src/views/MonitorView.tsx` (edit).

**Steps:**
1. Import `subNoise, skyElectronsPerSub, skyRateEPerSec, skyLimitedSubSeconds, subLengthVerdict, moreSubsForSnrMultiple` from `../lib/eta`? No — from `../lib/photometry`; add `usePhotometry`.
2. Add a `<Panel className="col-span-full sm:col-span-1 lg:col-span-3" title="Sub quality">` near the Thermal/Guiding panels (`MonitorView.tsx:554-646`). Gate its body:
   - If `preview == null || !preview.data_is_linear` → dim note "no linear frame".
   - Else if `prof.egain <= 0 || prof.readNoiseE <= 0` → honest prompt "Add camera gain + read noise (Capture → Camera photometry) to see sub SNR" (a link/button to `setView("capture")`, not native-disabled).
   - Else compute `skyE = skyElectronsPerSub(preview.stats.median, prof.biasAdu, prof.egain)`, `noise = subNoise(skyE, prof.readNoiseE)`, `skyLimited = skyLimitedSubSeconds(prof.readNoiseE, skyRateEPerSec(skyE, preview.exposure_s))`, `verdict = subLengthVerdict(preview.exposure_s, skyLimited)`.
   - Render `Stat`s: total noise (e-), read-noise fraction (%), sky-limited (s) vs actual with the verdict tone. When a run is active, add "to double stack SNR: +`moreSubsForSnrMultiple(progress.frames_done, 2)` subs" (needs only `seq.progress.frames_done`, `MonitorView.tsx:477`).
3. Absolute per-object SNR is intentionally omitted here (no signal on the wire — see Open decisions); the tile shows quality (noise/read-fraction/sky-limited) + the exact stacking arithmetic, which are fully honest without a signal.

**Verify:** `cd ui && npx tsc -b` → exit 0. Manual: with sim running + profile filled, the tile shows noise/read-fraction/sky-limited + "to double stack SNR" count.

---

### Task 7 — (Optional, additive) surface camera egain on status  ·  **Sonnet**

**Impl tier: Sonnet (mechanical).** Small, additive, default-inert; lets native cameras auto-prefill the profile. Ship last; the feature works fully without it.

**Files:** `server/astrodeck/devices/base.py`, `server/astrodeck/devices/cameras/engine.py`, `server/astrodeck/hub.py`, `ui/src/types.ts`, `server/tests/test_camera_engine*.py` (or a new focused test).

**Steps:**
1. `base.py:112` `Camera`: add class attr `egain: float = 0.0` (0 = unknown; keeps sim/Alpaca/NINA unchanged).
2. `engine.py:48-59` after copying caps: `self.egain = float(caps.extra.get("egain", 0.0) or 0.0)`.
3. `hub.py:2358-2360` status.camera payload: add `"egain": getattr(cam, "egain", 0.0)`.
4. `ui/src/types.ts:49-58` `RigStatus.camera`: add `egain?: number;` (optional — old servers omit it).
5. Client (Task 4/6): prefer `status.camera?.egain` when the profile egain is 0.
6. **pytest:** assert a Player One engine `connect()` sets `cam.egain` from `caps.extra["egain"]`, and a camera with no `egain` in extra leaves it `0.0`.

**Verify:** `server/.venv/Scripts/pytest.exe server/tests/test_camera_engine*.py -n0` (or the new test) → all pass; `cd ui && npx tsc -b` → exit 0.

---

## 4. Open decisions

Each with a recommendation.

1. **Per-object / per-star SNR needs a signal that is not on the wire.** `StarMark` (`types.ts:180-186`) carries no flux/peak, and `measure_stars` computes flux server-side but doesn't emit it. So an absolute "per-sub SNR of the target" cannot be computed client-side today.
   **Rec:** ship PRO-6 with the fully-computable outputs — sky-limited length, read-noise fraction, sub-length verdict, and the exact √N stacking arithmetic ("N more subs to ×2 stack SNR", which needs only frame counts). Keep the core's `subSnr(signalE, noise)` in place. If an absolute SNR is later wanted, add one additive field to the preview payload (`star_flux_median` ADU, already computed inside `measure_stars`) and multiply by egain — a small follow-up, not a blocker.

2. **egain + read noise vary with the gain setting; the profile stores a single value.** A profile measured at gain 100 is only strictly valid at gain 100.
   **Rec:** store one working value + document the assumption in the profile input's `title`. This matches how every consumer tool (SharpCap/PixInsight) treats a session. A per-gain table is out of scope (we have no generic gain-vs-egain curve for arbitrary cameras).

3. **Sky-limited factor default (3 vs 5 vs 10).** Lower = shorter subs (more read-noise penalty), higher = longer subs.
   **Rec:** default `SKY_LIMIT_FACTOR = 10` (total noise within ~5% of the read-noise-free ideal — the widely-used "good" target), exposed as an override. Do not surface a slider in v1 (novice-hostile).

4. **Bias pedestal source.** `skyElectronsPerSub` needs a bias ADU; we have the camera `offset` setting but not a measured bias.
   **Rec:** a user-entered `biasAdu` in the profile (default 0), with a `title` hint "median of a bias frame". Defaulting to 0 slightly overestimates sky (conservative — suggests marginally longer subs), which is safe. A future enhancement: auto-fill from a captured Bias frame's `stats.median`.

5. **Where the photometry-profile editor lives.** Options: Capture view (near Suggest), or a Settings/Equipment surface.
   **Rec:** Capture view (Task 4) — it is where Suggest is used and where a novice already sets exposure/gain, so the inputs are in context. Monitor's "Sub quality" tile links to Capture when the profile is empty (Task 6).

6. **Should the core live server-side instead?**
   **Rec:** no. Every essential input is already client-side (`preview.stats.median`, `exposure_s`, `data_is_linear`) or a tiny local constant; the math is closed-form scalars. Client-side `photometry.ts` avoids a round-trip, matches the `eta.ts`/`optics.ts` precedent, and is tsx-testable without jsdom. Task 7 (server egain) is a convenience prefill, not a dependency.
