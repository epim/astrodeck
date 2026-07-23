# NOV-6 — One-tap "Focus my scope" + plain "Sharp!" verdict (novice)

Design spec + TDD implementation plan (one file).

Slug: `one-tap-focus`
Branch base: `feat/wave0-remainder`
Date: 2026-07-23

---

## 0. TL;DR / what's actually missing

The autofocus **engine is complete** and already streams a terminal verdict that the
Focus view renders. A novice today still has to type an **Exposure** and a **Step
size** and pick **Binning/Filter** before pressing *Run Autofocus*
(`FocusView.tsx:220-252`), and the outcome is shown as a jargon chip —
`Focus — excellent · HFR 1.82 px · 2.4″ · R² 0.997 · hyperbolic`
(`FocusVerdict.tsx:43-81`).

So the genuinely-missing slice is small and purely additive:

1. A **pure param-derivation function** so the novice never types Exposure/Step —
   they're derived from the camera, the focuser travel range, and the current live
   frame (which is already showing measurable stars).
2. A **pure plain-verdict function** that maps the *same* level the jargon chip
   already computes (`autofocusLevel`) to a jargon-free sentence
   (`"Sharp!" / "Stars are tight — you're focused."`).
3. A **thin render**: one primary **"Focus my scope"** button (derives params →
   POSTs the existing endpoint unchanged) and a plain-sentence line above the
   existing jargon chip in the Result panel. Manual Exposure/Step/Filter/Binning
   drop behind a `▸ Advanced` disclosure (existing precedent
   `StretchHistogram.tsx:290-297`).

No backend change. The `/api/focuser/autofocus` endpoint and `AutofocusBody`
(`app.py:481-487`, `app.py:2726-2755`) accept exactly the fields we derive.

---

## 1. Design

### 1.1 Goal

A novice presses **one button** and gets **one plain sentence**. No numbers to
choose going in; no V-curve / R² / HFR jargon coming out. Experts keep every knob
and every number, one disclosure away.

### 1.2 Current-state seams (all read, real file:line)

**The button + params the novice must set today** — `ui/src/views/FocusView.tsx`:
- `241-243`: the manual `Exposure (s)` / `Step size` / `Filter` / `Binning` fields.
- `244-252`: the `Run Autofocus` button POSTs
  `{ exposure_s, step, binning, filter? }` to `/api/focuser/autofocus`, reading the
  four hand-typed local states `afExposure`/`afStep`/`afBin`/`afFilter`
  (`67-72`).
- `96-98`: `act(fn)` helper — try/catch → `showToast("error", …)`. Reused as-is.
- `102-106`: `focMax` = `foc.max` when `> 0`, else `null` (already computed for the
  nudge clamps). This is the focuser travel range we derive `step` from.
- `80-81`: `afMaxBin = min(8, max(1, camera.max_bin ?? 4))` — the camera bin ceiling
  is already read here.
- `45`, `93`: `shown = useLivePreview()` (the current live/pinned frame) and
  `pixelScale`. `shown` carries `exposure_s`, `gain`, `binning`, `hfr`, `stars`
  (`types.ts:182-208`) — the "current star sizes" the brief wants to auto-derive from.
- `39`, `157-190`: the **Result panel** already renders `<AutofocusVerdict/>` from
  the persisted `afResult` (`useLastAutofocusResult`). This is where the plain
  sentence lands, above the jargon chip.

**The existing verdict surface** — `ui/src/components/preview/FocusVerdict.tsx`:
- `16-33`: `autofocusLevel({state,hfr,r2,hfrGood,hfrWarn}) → AfLevel`
  (`"excellent"|"good"|"soft"|"failed"|"pending"`). **Pure, no React/DOM.** This is
  the single threshold source the plain verdict must reuse (so the sentence can
  never disagree with the chip).
- `35-81`: `AutofocusVerdict` React component (the jargon chip) — unchanged.
- Already imported by a **pure tsx test**
  (`components/__tests__/autofocusVerdict.test.ts:8`), proving `autofocusLevel` is
  tsx-runnable today.

**The pure-logic home** — `ui/src/lib/autofocus.ts`: existing AF pure helpers
(`normalizeAutofocusResult`, `filterNameFromStatus`, `afResultAgeLabel`), header
explicitly "No React, no DOM: npx-tsx testable". Its test is
`ui/src/lib/__tests__/autofocus.test.ts`. **All three new pure functions land here.**

**The server contract (unchanged)** — `ui/src/... → server/astrodeck/api/app.py`:
- `481-487`: `AutofocusBody` = `exposure_s:2.0, gain:120, step:350,
  steps_each_side:4, binning:2, filter:int|None`. Every field we derive already
  exists with these defaults.
- `2726-2755`: handler — 409s if `engine.running or hub.looping`, requires
  `camera`+`focuser`, optionally moves the filter wheel, then `run_autofocus(...)`
  and `_spawn("autofocus", …)` (fire-and-forget; result streams back over the
  `focus` WS event → store → `afResult`). We change **nothing** here.
- `focus/autofocus.py:30-68`: `run_autofocus(camera, focuser, *, exposure_s, gain,
  step, steps_each_side, binning, …)` — confirms the field names/units we send.

**Data shapes** — `ui/src/types.ts`:
- `49-58`: `RigStatus.camera` = `{width,height,max_gain,max_bin?, …}` (no
  `pixel_size` on the live camera status).
- `98-104` / `498-509`: `RigStatus.optics: OpticsComputed` =
  `{image_scale_arcsec_px, pixel_size_um, focal_length_mm, …}` — pixel scale for the
  arcsec detail, but **not** focuser step-per-micron (nothing in status carries it).
- `182-209`: `PreviewInfo` = `{exposure_s, gain, binning, hfr?, stars?,
  pixel_scale_arcsec?, …}`.

**Honest-disabled idiom** (Global Constraint §11.8) — precedent
`components/equipment/RotatorCard.tsx:199-204`: a denied control shows a dim note
with `<Icon name="lock" size={11}/>` + explanatory copy (not a bare native
`disabled`). The one-tap hero button adopts this treatment via a pure
`focusButtonState` helper (below).

### 1.3 Approach — the three pure units (all in `lib/autofocus.ts`)

Nothing physics-y is invented. We only have: camera bin ceiling + max gain, the
focuser **travel range** (`foc.max`), and the **current live frame** (which is
empirically already showing stars). We derive honestly from exactly those, and we
never claim a Critical-Focus-Zone we can't compute (there is no step-per-micron in
`status`).

#### (A) `deriveAutofocusParams(inputs) → DerivedAfParams`

| Field | Rule | Why (honest basis) |
|---|---|---|
| `exposure_s` | reuse the **live frame's** `exposure_s` when it's already showing stars (`stars ≥ AF_STAR_FLOOR` **and** `hfr != null`), else fallback `2s`; then clamp to `[1, 6]s`. | The exposure on screen is *empirically* producing measurable stars — the most honest signal available. Clamp so a 300 s light sub doesn't make AF glacial and a 0.1 s framing sub doesn't starve each sample. |
| `binning` | `min(2, camera.max_bin ?? 4)`. | 2× is the AF sweet spot (brighter stars, fast download); respects a bin-1-only sensor. |
| `gain` | reuse live `gain` when finite else default `120`; clamp to `[0, max_gain]`. | Consistent with reusing the live exposure; never exceed the sensor ceiling. |
| `steps_each_side` | fixed `4` (engine default, untouched). | The engine's proven sweep width. |
| `step` | **keep the proven default `350`**; only *rescale* when the default sweep span (`350·4·2 = 2800`) is a bad fraction of `foc.max`: `>30%` (tiny focuser → step too big, walk off travel) or `<4%` (huge focuser → step too small, never leaves focus). Rescale target = **12% of travel**: `step = clamp(round(focMax·0.12 / 8), 20, 1500)`. `foc.max` null → keep `350`. | This is **explicitly not** a CFZ claim — we have no step-per-micron. It is "spread the sweep across a modest fraction of the focuser's travel," and it leaves the field-proven 350 alone on typical rigs, intervening only at range extremes. |

`step` worked examples (pin these in the test):

| `foc.max` | default-span frac | outcome |
|---|---|---|
| `null` | — | `350` (unchanged) |
| `30000` | 9.3% (in-band) | `350` |
| `10000` | 28% (in-band) | `350` |
| `5000` | 56% (> 30%) | `round(5000·.12/8)=75` |
| `100000`| 2.8% (< 4%) | `round(100000·.12/8)=1500` (clamped at max) |

`DerivedAfParams` also returns a `basis: {exposure, step, binning}` of short
human-readable strings — surfaced in the button's `title`/help line so the novice
(and reviewer) can see *why* each number was chosen. Pure, so it's asserted directly.

#### (B) `plainFocusVerdict({state,hfr,r2,hfrGood,hfrWarn}) → PlainVerdict`

Delegates to `autofocusLevel(...)` (one threshold source) then maps the level to
copy. Every non-good line carries an **action** (§12.6 pattern, mirrors
`FocusVerdict`'s saturation action at `FocusVerdict.tsx:153-157`).

| level | tone | headline | detail |
|---|---|---|---|
| `excellent` | good | **Sharp!** | Stars are tight — you're focused. |
| `good` | good | **Focused.** | Stars look good — you're ready to shoot. |
| `soft` | warn | **Almost there.** | Stars are still a little soft — tap Focus my scope to try again. |
| `failed` | bad | **Couldn't focus.** | Not enough stars to lock onto — check the sky is clear and roughly focused, then try again. |
| `pending` (running) | neutral | **Focusing…** | Measuring your stars… |
| `pending` (idle) | neutral | **Not focused yet.** | Tap Focus my scope to start. |

#### (C) `focusButtonState({canFocus,hasFocuser,running}) → FocusButtonState`

Pure gating copy for the hero button so the render stays dumb. Precedence puts the
capability explanation first (a viewer always sees the honest read-only reason):

| condition | `disabled` | `label` | `reason` (→ `title`) | `locked` |
|---|---|---|---|---|
| `!canFocus` | true | Focus my scope | Read-only — focusing needs operator access | **true** (lock glyph) |
| `!hasFocuser` | true | Focus my scope | Connect a focuser to enable one-tap focus | false |
| `running` | true | Focusing… | Autofocus is running | false |
| else | false | Focus my scope | `null` | false |

### 1.4 Placement / render (thin)

- **Autofocus panel** (`FocusView.tsx:220-264`): add the **"Focus my scope"** primary
  button (`btn btn-accent w-full tap-lg min-h-[56px]`, `Icon name="focus"`) as the
  **first** child. Its enabled/label/title come from `focusButtonState(...)`; when
  disabled it renders `aria-disabled` + dim class + `title=reason` + lock glyph when
  `locked` (never native `disabled` on the hero — §11.8). On tap it calls
  `deriveAutofocusParams(...)` and `act(() => api.post("/api/focuser/autofocus",
  derived))` (filter left at current). Below it, wrap the existing four fields +
  `Run Autofocus` button in a `▸ Advanced` disclosure (local `useState`, mirroring
  `StretchHistogram.tsx:290-297`).
- **Result panel** (`FocusView.tsx:160-186`): above the existing `<AutofocusVerdict/>`,
  render the plain sentence from `plainFocusVerdict(...)` (headline bold + tone color,
  detail dim). The jargon chip stays beneath it (demoted, unchanged) so experts keep
  the numbers. No new store state — reads the same `afResult`.

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real coordinates **[SITE-LAT] / [SITE-LON]** and the label
  **"[SITE-LABEL]"** must **NEVER** appear in code, tests, or docs. Site default is
  **"My Observatory"** / **0.0**. (This feature touches no site/coords data; keep it
  that way — no fixtures with real coords.)
- **Never `git add -A`.** Stage explicit paths only.
- **UI typecheck gate:** `cd ui && npx tsc -b`.
- **NO jsdom / DOM harness.** Pure logic is tested via `npx tsx` inline-assert only
  (idioms: `ui/src/components/ui/__tests__/SegmentedControl.test.tsx`,
  `ui/src/components/__tests__/healthStrip.test.ts`,
  `ui/src/lib/__tests__/eta.test.ts`, and the file we extend,
  `ui/src/lib/__tests__/autofocus.test.ts`). Thin render/wiring is verified by
  `tsc -b`, never by a DOM test.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from repo root, `-n0` for a
  single test. (This feature adds **no** backend code, so no new pytest — the
  existing suite must still pass if touched, but it is not.)
- **Client toasts** via `useStore.getState().enqueueToast` (the `act()` helper's
  `showToast` wraps it — reused unchanged for the error path).
- **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`, **never** native `disabled`, on the hero button. (Precedent
  `RotatorCard.tsx:199-204`.)
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Three tasks, in order (Task 1 is a prerequisite refactor so Task 2's plain verdict
can reuse the one threshold source).

---

### Task 1 — Relocate `autofocusLevel` + `AfLevel` into `lib/autofocus.ts` (no behavior change)

**Why:** `plainFocusVerdict` (Task 2) must reuse the *exact* level thresholds the
jargon chip uses, and a `lib/` function must not import upward from `components/`.
`autofocusLevel` is already pure — move it down; re-point its two importers.

**Files**
- Modify: `ui/src/lib/autofocus.ts` (add `AfLevel` + `autofocusLevel`).
- Modify: `ui/src/components/preview/FocusVerdict.tsx` (delete local defs; import from
  `../../lib/autofocus`; keep `AF_CHIP` + `AutofocusVerdict` here).
- Modify: `ui/src/components/__tests__/autofocusVerdict.test.ts:8` (import
  `autofocusLevel, type AfLevel` from `../../lib/autofocus`).
- Test: existing `autofocusVerdict.test.ts` (must stay green, now pointing at lib).

**Interfaces** (moved verbatim — do not change logic)
```ts
// lib/autofocus.ts
export type AfLevel = "excellent" | "good" | "soft" | "failed" | "pending";
export function autofocusLevel(args: {
  state?: string; hfr?: number | null; r2?: number | null;
  hfrGood: number; hfrWarn: number;
}): AfLevel;
```

**Steps**
1. Cut lines `16-33` of `FocusVerdict.tsx` (the `AfLevel` type + `autofocusLevel`
   fn, verbatim) and paste into `lib/autofocus.ts` (below the existing exports).
2. In `FocusVerdict.tsx`, add `import { autofocusLevel, type AfLevel } from
   "../../lib/autofocus";`. `AF_CHIP` (`35-41`) and `AutofocusVerdict` (`43-81`)
   stay — they now consume the imported symbols.
3. In `autofocusVerdict.test.ts`, change line 8 import path
   `"../preview/FocusVerdict"` → `"../../lib/autofocus"`.
4. Run the moved test and the typecheck:
   ```
   cd ui && npx tsx src/components/__tests__/autofocusVerdict.test.ts
   npx tsc -b
   ```
   Expect: `autofocusVerdict.test: … 0 failed` (its existing assertions, unchanged)
   and `tsc -b` clean.

**Impl tier: Sonnet** — mechanical move + import re-point, zero logic change, guarded
by an existing test.

---

### Task 2 — Add the three pure functions + constants to `lib/autofocus.ts`

**Files**
- Modify: `ui/src/lib/autofocus.ts` (append the functions/types/consts below).
- Test: `ui/src/lib/__tests__/autofocus.test.ts` (extend — add a `deriveAutofocusParams`,
  a `plainFocusVerdict`, and a `focusButtonState` block using the file's existing
  `test`/`assert` harness).

**Interfaces**
```ts
// ---- (A) param derivation ----
export const AF_EXPOSURE_MIN_S = 1;
export const AF_EXPOSURE_MAX_S = 6;
export const AF_EXPOSURE_FALLBACK_S = 2;
export const AF_STAR_FLOOR = 5;
export const AF_DEFAULT_STEP = 350;
export const AF_STEPS_EACH_SIDE = 4;
export const AF_STEP_MIN = 20;
export const AF_STEP_MAX = 1500;
export const AF_SPAN_TARGET_FRAC = 0.12;
export const AF_SPAN_MIN_FRAC = 0.04;
export const AF_SPAN_MAX_FRAC = 0.30;
export const AF_DEFAULT_GAIN = 120;

export interface DeriveAfInputs {
  focuserMax: number | null;   // foc.max when > 0, else null
  maxBin: number | null;       // status.camera?.max_bin
  maxGain: number | null;      // status.camera?.max_gain
  liveExposureS: number | null;// shown?.exposure_s
  liveGain: number | null;     // shown?.gain
  liveStars: number | null;    // shown?.stars
  liveHfr: number | null;      // shown?.hfr
}
export interface DerivedAfParams {
  exposure_s: number;
  gain: number;
  step: number;
  steps_each_side: number;
  binning: number;
  basis: { exposure: string; step: string; binning: string };
}
export function deriveAutofocusParams(inp: DeriveAfInputs): DerivedAfParams;

// ---- (B) plain verdict ----
export type FocusTone = "good" | "warn" | "bad" | "neutral";
export interface PlainVerdict {
  level: AfLevel; tone: FocusTone; headline: string; detail: string;
}
export function plainFocusVerdict(args: {
  state?: string; hfr?: number | null; r2?: number | null;
  hfrGood: number; hfrWarn: number;
}): PlainVerdict;

// ---- (C) hero-button gating copy ----
export interface FocusButtonState {
  disabled: boolean; label: string; reason: string | null; locked: boolean;
}
export function focusButtonState(a: {
  canFocus: boolean; hasFocuser: boolean; running: boolean;
}): FocusButtonState;
```

**Steps**

1. **(A)** Implement `deriveAutofocusParams` exactly per §1.3(A). Reference body:
   ```ts
   const clampI = (v: number, lo: number, hi: number) =>
     Math.min(hi, Math.max(lo, Math.round(v)));
   const round1 = (v: number) => Math.round(v * 10) / 10;

   export function deriveAutofocusParams(inp: DeriveAfInputs): DerivedAfParams {
     // exposure — reuse the live frame only if it is already showing stars
     const liveUsable =
       inp.liveExposureS != null && Number.isFinite(inp.liveExposureS) &&
       (inp.liveStars ?? 0) >= AF_STAR_FLOOR && inp.liveHfr != null;
     const rawExp = liveUsable ? (inp.liveExposureS as number) : AF_EXPOSURE_FALLBACK_S;
     const exposure_s = Math.min(AF_EXPOSURE_MAX_S, Math.max(AF_EXPOSURE_MIN_S, round1(rawExp)));
     const exposure = liveUsable
       ? `matched the live frame (${round1(rawExp)}s, ${inp.liveStars} stars)` +
         (exposure_s !== round1(rawExp) ? `, clamped to ${exposure_s}s` : "")
       : `default ${AF_EXPOSURE_FALLBACK_S}s (no measured stars in the live frame yet)`;

     // binning — 2× sweet spot, respect a bin-1-only sensor
     const cap = inp.maxBin != null && inp.maxBin >= 1 ? Math.floor(inp.maxBin) : 4;
     const binning = Math.min(2, cap);
     const binBasis = binning === 2
       ? "2× — brighter stars, fast download"
       : "1× (camera has no higher binning)";

     // gain — reuse live gain else default, clamp to sensor ceiling
     const rawGain = inp.liveGain != null && Number.isFinite(inp.liveGain)
       ? inp.liveGain : AF_DEFAULT_GAIN;
     const gain = inp.maxGain != null && inp.maxGain > 0
       ? clampI(rawGain, 0, inp.maxGain) : Math.max(0, Math.round(rawGain));

     // step — keep proven default; rescale only at focuser-range extremes
     let step = AF_DEFAULT_STEP;
     let stepBasis = `default ${AF_DEFAULT_STEP} steps`;
     const fm = inp.focuserMax;
     if (fm != null && fm > 0) {
       const frac = (AF_DEFAULT_STEP * AF_STEPS_EACH_SIDE * 2) / fm;
       if (frac > AF_SPAN_MAX_FRAC || frac < AF_SPAN_MIN_FRAC) {
         step = clampI((fm * AF_SPAN_TARGET_FRAC) / (AF_STEPS_EACH_SIDE * 2), AF_STEP_MIN, AF_STEP_MAX);
         stepBasis = `${step} steps — sized to sweep ~${Math.round(AF_SPAN_TARGET_FRAC * 100)}% of focuser travel`;
       } else {
         stepBasis = `default ${AF_DEFAULT_STEP} steps (~${Math.round(frac * 100)}% of travel)`;
       }
     }
     return { exposure_s, gain, step, steps_each_side: AF_STEPS_EACH_SIDE, binning,
              basis: { exposure, step: stepBasis, binning: binBasis } };
   }
   ```
2. **(B)** Implement `plainFocusVerdict` per §1.3(B) — `const level =
   autofocusLevel(args)` then a `switch(level)` returning the copy table rows;
   `pending` splits on `args.state === "running"`.
3. **(C)** Implement `focusButtonState` per §1.3(C) — `if (!a.canFocus) …; if
   (!a.hasFocuser) …; if (a.running) …; return {disabled:false,…}` in that order.
4. **Extend the test** `ui/src/lib/__tests__/autofocus.test.ts` (same `test`/`assert`
   harness already in that file), adding e.g.:
   ```ts
   import {
     deriveAutofocusParams, plainFocusVerdict, focusButtonState,
     AF_DEFAULT_STEP, AF_STEP_MAX,
   } from "../autofocus";
   const TH = { hfrGood: 2.0, hfrWarn: 3.5 };

   test("derive: reuses a star-bearing live frame's exposure, clamps to band", () => {
     const p = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 300,
       liveExposureS: 3, liveGain: 100, liveStars: 40, liveHfr: 2.2 });
     assert(p.exposure_s === 3, `exposure ${p.exposure_s}`);
     assert(p.binning === 2, "bin2");
     assert(p.gain === 100, "reuse live gain");
     assert(p.step === AF_DEFAULT_STEP, `step ${p.step}`); // 9.3% in-band
     assert(p.steps_each_side === 4, "each side");
   });
   test("derive: no usable live frame -> fallback 2s; long sub clamps to 6s", () => {
     const a = deriveAutofocusParams({ focuserMax: null, maxBin: null, maxGain: null,
       liveExposureS: 300, liveGain: null, liveStars: 2, liveHfr: null });
     assert(a.exposure_s === 2, `few stars -> fallback 2s, got ${a.exposure_s}`);
     const b = deriveAutofocusParams({ focuserMax: null, maxBin: null, maxGain: null,
       liveExposureS: 300, liveGain: null, liveStars: 40, liveHfr: 2.2 });
     assert(b.exposure_s === 6, `300s sub clamps to 6s, got ${b.exposure_s}`);
   });
   test("derive: bin-1-only sensor and gain ceiling are respected", () => {
     const p = deriveAutofocusParams({ focuserMax: null, maxBin: 1, maxGain: 100,
       liveExposureS: null, liveGain: 200, liveStars: null, liveHfr: null });
     assert(p.binning === 1, "bin clamped to 1");
     assert(p.gain === 100, `gain clamped to max, got ${p.gain}`);
   });
   test("derive: step rescales only at focuser-range extremes", () => {
     const nul = deriveAutofocusParams({ focuserMax: null, maxBin: 4, maxGain: 300,
       liveExposureS: 2, liveGain: 120, liveStars: 40, liveHfr: 2 });
     assert(nul.step === AF_DEFAULT_STEP, "null focuser -> default");
     const tiny = deriveAutofocusParams({ focuserMax: 5000, maxBin: 4, maxGain: 300,
       liveExposureS: 2, liveGain: 120, liveStars: 40, liveHfr: 2 });
     assert(tiny.step === 75, `5000 -> 75, got ${tiny.step}`);       // 56% > 30%
     const huge = deriveAutofocusParams({ focuserMax: 100000, maxBin: 4, maxGain: 300,
       liveExposureS: 2, liveGain: 120, liveStars: 40, liveHfr: 2 });
     assert(huge.step === AF_STEP_MAX, `100000 -> clamp ${AF_STEP_MAX}, got ${huge.step}`); // 2.8% < 4%
   });
   test("plainFocusVerdict: sharp / soft(action) / failed(action) / running", () => {
     assert(plainFocusVerdict({ state:"done", hfr:1.8, r2:0.997, ...TH }).headline === "Sharp!", "sharp");
     const soft = plainFocusVerdict({ state:"done", hfr:4.0, r2:0.99, ...TH });
     assert(soft.tone === "warn" && /try again/.test(soft.detail), "soft carries action");
     const fail = plainFocusVerdict({ state:"failed", hfr:null, r2:null, ...TH });
     assert(fail.tone === "bad" && /try again/.test(fail.detail), "failed carries action");
     assert(plainFocusVerdict({ state:"running", hfr:null, r2:null, ...TH }).headline === "Focusing…", "running");
   });
   test("focusButtonState: viewer locked > no-focuser > running > enabled", () => {
     assert(focusButtonState({ canFocus:false, hasFocuser:true, running:false }).locked === true, "viewer locked");
     assert(focusButtonState({ canFocus:true, hasFocuser:false, running:false }).reason != null, "no focuser reason");
     assert(focusButtonState({ canFocus:true, hasFocuser:true, running:true }).label === "Focusing…", "running label");
     const ok = focusButtonState({ canFocus:true, hasFocuser:true, running:false });
     assert(ok.disabled === false && ok.reason === null, "enabled");
   });
   ```
5. Run:
   ```
   cd ui && npx tsx src/lib/__tests__/autofocus.test.ts
   npx tsc -b
   ```
   Expect: `autofocus.test: N passed, 0 failed` (existing + new) and `tsc -b` clean.

**Impl tier: Sonnet** — the one subtle piece (the `step` rescale boundaries) is fully
specified here with worked examples and boundary tests; the rest is
clamp/map/switch. No search/optimization, so no Opus needed.

---

### Task 3 — Wire the thin render in `FocusView.tsx`

**Files**
- Modify: `ui/src/views/FocusView.tsx` (hero button + Advanced disclosure + plain
  verdict line).
- Verified by: `cd ui && npx tsc -b` (no DOM test for render — constraint).

**Interfaces** — no new types; consumes Task 2's exports:
```ts
import {
  deriveAutofocusParams, plainFocusVerdict, focusButtonState,
} from "../lib/autofocus";
```

**Steps**
1. Add a local disclosure state near the other `useState`s
   (`FocusView.tsx:66-72`): `const [afAdvanced, setAfAdvanced] = useState(false);`
2. In the **Autofocus** panel (`220`), insert the hero button as the first child:
   ```tsx
   {(() => {
     const bs = focusButtonState({ canFocus, hasFocuser: !!foc, running });
     const onTap = () => {
       const d = deriveAutofocusParams({
         focuserMax: focMax,
         maxBin: status?.camera?.max_bin ?? null,
         maxGain: status?.camera?.max_gain ?? null,
         liveExposureS: shown?.exposure_s ?? null,
         liveGain: shown?.gain ?? null,
         liveStars: shown?.stars ?? null,
         liveHfr: shown?.hfr ?? null,
       });
       return act(() => api.post("/api/focuser/autofocus", {
         exposure_s: d.exposure_s, gain: d.gain, step: d.step,
         steps_each_side: d.steps_each_side, binning: d.binning,
       }));
     };
     return (
       <button
         className={`btn btn-accent w-full tap-lg min-h-[56px] ${bs.disabled ? "opacity-40" : ""}`}
         aria-disabled={bs.disabled || undefined}
         title={bs.reason ?? undefined}
         onClick={bs.disabled ? undefined : onTap}
       >
         {bs.locked && <Icon name="lock" size={13} className="inline -mt-0.5 mr-1.5" />}
         {!bs.locked && <Icon name="focus" size={14} className="inline -mt-0.5 mr-1.5" />}
         {bs.label}
       </button>
     );
   })()}
   ```
   (`aria-disabled` + dim + `title` + lock glyph, never native `disabled` — §11.8.)
3. Wrap the existing four `Field`s + the `Run Autofocus` button (`221-252`) in a
   `▸ Advanced` disclosure toggled by `afAdvanced` (mirror
   `StretchHistogram.tsx:290-297`: a `▾/▸ Advanced` `btn`, content rendered when
   open). The advanced *Run Autofocus* button and its POST body are unchanged.
4. In the **Result** panel (`160-186`), above `<AutofocusVerdict/>`, render the plain
   line:
   ```tsx
   {(() => {
     const pv = plainFocusVerdict({
       state: afResult.state, hfr: afResult.best?.hfr ?? null,
       r2: afResult.fit?.r2 ?? null, hfrGood, hfrWarn,
     });
     const tone = pv.tone === "good" ? "text-good" : pv.tone === "warn" ? "text-warn"
       : pv.tone === "bad" ? "text-bad" : "text-dim";
     return (
       <div className="mb-2" role="status" aria-live="polite">
         <div className={`text-base font-semibold ${tone}`}>{pv.headline}</div>
         <div className="text-xs text-dim">{pv.detail}</div>
       </div>
     );
   })()}
   ```
   Keep `<AutofocusVerdict/>` beneath it unchanged (the demoted expert numbers).
5. Run the gate:
   ```
   cd ui && npx tsc -b
   ```
   Expect: clean (no errors). No behavior of the existing advanced path changed;
   only additive UI.

**Impl tier: Sonnet** — pattern-following JSX wiring against the two documented
precedents (StretchHistogram disclosure, RotatorCall honest-disabled), all logic
already unit-tested in Task 2; the gate is `tsc -b`.

---

## 4. Open decisions

1. **Completion toast?** Should tapping *Focus my scope* also fire a success toast
   with the plain sentence when the run finishes?
   **Rec: No (v1).** The run streams over WS and terminality is detected in the store,
   not in `FocusView`; firing a toast there would mean new store-handler wiring and a
   double-surface with the Result panel. The plain line in the Result panel is the
   report surface (consistent with today). Revisit only if novices miss it.

2. **Hide the jargon chip behind a `▸ Details` toggle in the Result panel?**
   **Rec: No — keep it visible but demoted** beneath the plain sentence. It's small and
   already dim; a disclosure adds state for little gain, and experts value the numbers
   at a glance. Cheap to add later if novice testing says the numbers distract.

3. **Rescale-target 12% and the 4%/30% boundary fractions** — these are judgement
   calls, not derived constants.
   **Rec: ship as specified** (they leave the field-proven 350 untouched on all
   typical rigs and only intervene at genuine extremes), and treat them as tunable
   constants (already exported) once we see real focuser ranges from astrotown /
   at-scope.

4. **Reuse live `gain` vs always default 120?** Reusing keeps the button consistent
   with "use what's already showing stars," but a very high framing gain could add
   noise to AF samples. **Rec: reuse-then-clamp as specified** — it's honest and the
   clamp bounds it; if AF star detection proves gain-sensitive, switch `gain` to a flat
   `min(120, max_gain)` (one-line change, same test file).
