# NOV-10 — One-tap calibration capture + "take darks now?" prompt

**Feature:** A calibration quick-action in the Capture view that sets `frame_type`
(Dark / Flat / Bias — manual capture is Light-only today), pre-fills exposure /
gain / offset / binning / temp from the last lights, coaches the cover/uncover
step, and fires an end-of-session "you shot 60 × 120s at −10°C — take matching
darks now?" prompt. Builds foundation **F-C** (frame_type in the manual-capture
path).

Date: 2026-07-23 · Branch: `feat/wave0-remainder`

---

## 1. Design

### 1.1 Goal

Give a novice a one-tap path from "I just shot my lights" to a matched set of
darks, without leaving Capture and without knowing what a dark is. Three moves:

1. **Pick a frame type** (Light / Dark / Flat / Bias) on the Capture exposure
   panel and have Single/Loop actually shoot it (the shutter follows automatically).
2. **Match last lights** — one tap copies the exposure/gain/offset/binning/temp
   the lights were shot at into the calibration capture.
3. **End-of-session nudge** — when the user stops a run of lights, a confirm
   dialog offers to switch to Dark + pre-fill and shoot now.

### 1.2 Current-state seams (all read; file:line)

**The gap is entirely in the UI — the backend already shoots calibration frames.**

- `ui/src/views/CaptureView.tsx:102-109` — the capture POST `body` is built with
  `{ exposure_s, gain, offset, binning, save, target }` and **no `frame_type`**.
  So every manual Single/Loop is implicitly a Light.
- `CaptureView.tsx:48-54` — the field state lives here as local `useState`:
  `exposure "2"`, `gain "120"`, `offset "30"`, `binning "1"`, `coolerTarget "-10"`.
  There is **no** store slice remembering "last lights" — it must be added.
- `CaptureView.tsx:207-216` — `onSingle`/`onLoop` guard then
  `api.post("/api/capture", body)` / `"/api/capture/loop", body`.
- `CaptureView.tsx:165-175` — the completion effect: a **new** `liveId` means a
  frame finished + decoded. This is the honest "a frame landed" signal — the hook
  to count banked lights.
- `CaptureView.tsx:217-223` — `onStop` fires when the user stops a loop (`looping`
  is true just before). The honest end-of-session trigger.
- `CaptureView.tsx:66,449` and `:431-453` — sensor temp is `cam?.temperature`;
  cooler target is `cooler?.target_c`. Source for the "at −10°C" clause.
- `server/astrodeck/api/app.py:434-441` — **`CaptureBody` ALREADY has
  `frame_type: str = "Light"`.** No additive backend model field is needed.
- `app.py:2357-2374` — `/api/capture` route ALREADY forwards
  `frame_type=body.frame_type` into `hub.capture(...)`.
- `app.py:2376-2391` — `/api/capture/loop` route calls
  `hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning)` — it
  **drops `body.frame_type`**. Looping calibration would open the shutter. This is
  the one genuine backend gap.
- `server/astrodeck/hub.py:1380-1393` — `capture()` accepts `frame_type="Light"`
  and sets the shutter: `light=(frame_type.upper() not in ("DARK", "BIAS"))`.
- `hub.py:1683-1707` — `start_loop(exposure_s, gain, offset, binning)` has no
  `frame_type` and calls `self.capture(exposure_s, gain, offset, binning)` at
  `:1700` with no frame_type → always Light.
- `server/tests/test_hub_capture_precession.py:238-260` —
  `test_capture_frame_type_controls_shutter` already proves Dark/Bias close the
  shutter through `hub.capture`. The loop path has no such coverage.

**Reusable primitives found (no new infra):**

- `ui/src/components/ui/SegmentedControl.tsx:23-29` — a ready
  `SegmentedControl<T>({ options, value, onChange, disabled, ariaLabel })`, a real
  radiogroup, night-safe, ≥44px targets. Use it for the frame-type picker.
- `ui/src/components/ConfirmDialog.tsx:42-52` — `confirmDialog(opts): Promise<boolean>`
  imperative wrapper over the store's `pushConfirm`; `body` is `ReactNode`.
- `ui/src/store.ts:563,597,1067` — `enqueueToast` / `showToast` for the follow-up toast.
- Honest-disabled idiom `ui/src/components/preview/PreviewToolbar.tsx:32-45`:
  `aria-disabled={disabled}` + `title={reason}` + `<Icon name="lock" size=… />`,
  **never** native `disabled`. `icons.tsx:56` has `lock`; `:63` has `info`.
- Pure-logic idioms: `ui/src/lib/exposure.ts` (a `.ts` with no React/DOM);
  `ui/src/lib/__tests__/eta.test.ts` and `ui/src/components/__tests__/healthStrip.test.ts`
  (inline-assert, run under `npx tsx`, no jsdom).

### 1.3 Approach

**Data shapes** (new `ui/src/lib/calibration.ts`, pure, no React):

```ts
export type FrameType = "Light" | "Dark" | "Flat" | "Bias";
export const FRAME_TYPES: readonly FrameType[] = ["Light", "Dark", "Flat", "Bias"];

// Snapshot of what a light frame was shot at (recorded when a light frame LANDS).
export interface LightSnapshot {
  exposureS: number; gain: number; offset: number; binning: number;
  tempC: number | null;  // sensor temp at capture, if any cooler reports one
  count: number;         // frames banked at THESE settings this session
}

// Prefill copied into the capture form when adopting a calibration batch.
export interface CalibrationPrefill {
  frameType: FrameType;
  exposure: string; gain: string; offset: string; binning: string;
  coolerTarget: string | null;  // null => leave the cooler field untouched
}
```

**Copy table** (coaching text under the selector; only shown for non-Light):

| frameType | coach text |
|-----------|-----------|
| `Light`   | (none — normal imaging) |
| `Dark`    | "Cap the scope so no light reaches the sensor. A dark matches your light's exposure, gain and temperature with the shutter closed." |
| `Bias`    | "Cap the scope. A bias is the shortest possible dark — it maps read-noise and offset only." |
| `Flat`    | "Uncover the scope and point it at an even light source (flat panel or dawn sky). Flats keep the shutter open." |

**Behavior**

- `accumulateLight(prev, next)` — reducer for the completion signal: if `next`'s
  exposure/gain/offset/binning **match** `prev`, return `prev` with `count+1` and
  refreshed `tempC`; otherwise start a fresh batch at `count: 1`. This is the one
  bit worth a focused test (batch continuity vs. reset).
- `shouldOfferDarks(light): light is LightSnapshot` — true only when
  `light != null && light.count >= 1` and settings are finite/positive.
- `darkPrefillFrom(light): CalibrationPrefill` — same exposure/gain/offset/binning
  as the lights, `frameType: "Dark"`, `coolerTarget` = the light's `tempC` as a
  string (or null when unknown).
- `formatLightSummary(light): string` → `"60 × 120s · gain 120 · −10 °C"`. Count
  clause dropped when `count < 1`; temp clause dropped when `tempC == null`.

**Store slice** (`ui/src/store.ts`):

```ts
lastLight: LightSnapshot | null;                       // state, init null
noteLightFrame: (snap: Omit<LightSnapshot, "count">) => void;  // = accumulateLight
clearLastLight: () => void;
```
plus `export const useLastLight = () => useStore((s) => s.lastLight)`.
In-memory only — the nudge fires within the same session; no persistence needed.

**CaptureView wiring**

- New `const [frameType, setFrameType] = useState<FrameType>("Light")`; mirror it
  into a `frameTypeRef` each render (same pattern as `expLenRef`) so the
  liveId-advance effect reads it without a stale closure.
- `body` gains `frame_type: frameType` (`CaptureView.tsx:102-109`).
- A `SegmentedControl` of `FRAME_TYPES` above the capture buttons; `disabled` when
  `!canCapture` (matches the surrounding viewer read-only inputs).
- Coaching `<p>` (FRAME_COACH text) rendered only when `frameType !== "Light"`.
- In the completion effect (`:165-175`), when `frameTypeRef.current === "Light"`,
  call `noteLightFrame({ exposureS, gain: gainNum, offset, binning, tempC })`.
- A **"Match last lights"** button shown when `frameType` is Dark/Bias: applies
  `darkPrefillFrom(lastLight)` via the existing `setExposure`/`setGain`/… setters.
  When `lastLight == null` it uses the **honest-disabled idiom** (dim + `lock`
  glyph + `aria-disabled` + `title="Shoot some lights first"`), never native
  `disabled`.
- In `onStop` (`:217-223`), if `looping && frameTypeRef.current === "Light" &&
  shouldOfferDarks(lastLight)` and we have not already offered this batch
  (`offeredRef`), `await confirmDialog({ title: "Take matching darks?", body:
  formatLightSummary(lastLight) + " — shoot matching darks now?", confirmLabel:
  "Set up darks", confirmPrimary: true })`. On `true`: `setFrameType("Dark")`,
  apply `darkPrefillFrom`, and `showToast("info", …)`. Purely sets up the form —
  it does **not** auto-fire an exposure.

**Backend (one real gap):** thread `frame_type` through the loop path so a batch
of darks/flats can be shot with `Loop`. `start_loop(…, frame_type="Light")` →
`self.capture(…, frame_type=frame_type)`; the `/api/capture/loop` route passes
`body.frame_type`. Fully additive; single-capture path is already done.

### 1.4 Placement

All UI additions live inside the existing **Exposure** `Panel`
(`CaptureView.tsx:238-372`): frame-type SegmentedControl + coach text directly
above the Single/Loop/Stop button grid (`:290-316`); the "Match last lights"
button sits with the coach text when a calibration type is selected. No new view,
route, or nav entry.

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real coordinates `37.348110` / `121.801704` and the label
  "My Backyard" must **NEVER** appear in code, tests, or docs. Site default is
  "My Observatory" / `0.0`. (This feature touches neither site nor coordinates.)
- **Never `git add -A`.** Stage named paths only.
- **UI gate:** `cd ui && npx tsc -b`.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert (idiom:
  `ui/src/lib/__tests__/eta.test.ts`, `ui/src/components/__tests__/healthStrip.test.ts`).
- **Backend tests:** `server/.venv/Scripts/pytest.exe` from repo root, `-n0`
  (single worker).
- **Rust:** N/A for this feature (`native/` untouched).
- **Client toasts** via `useStore.getState().enqueueToast` (or the `showToast`
  shim).
- **Honest-disabled idiom (§11.8):** dim token + `lock` glyph + `aria-disabled` +
  `title`, never native `disabled`.
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Order: pure logic → store → UI wiring (all typecheck-verified) and the backend
loop gap. UI tasks depend on Task 1's module; the backend task is independent.

---

### Task 1 — `lib/calibration.ts` pure logic + tsx test

**Impl tier: Sonnet.** Mechanical value-mapping and one equality-vs-reset
reducer; no numeric subtlety.

**Files**
- create `ui/src/lib/calibration.ts`
- create `ui/src/lib/__tests__/calibration.test.ts`

**Interfaces**
```ts
export type FrameType = "Light" | "Dark" | "Flat" | "Bias";
export const FRAME_TYPES: readonly FrameType[];
export const FRAME_COACH: Record<FrameType, string>;   // "" for Light
export interface LightSnapshot {
  exposureS: number; gain: number; offset: number; binning: number;
  tempC: number | null; count: number;
}
export interface CalibrationPrefill {
  frameType: FrameType;
  exposure: string; gain: string; offset: string; binning: string;
  coolerTarget: string | null;
}
export function accumulateLight(
  prev: LightSnapshot | null,
  next: Omit<LightSnapshot, "count">,
): LightSnapshot;
export function shouldOfferDarks(l: LightSnapshot | null): l is LightSnapshot;
export function darkPrefillFrom(l: LightSnapshot): CalibrationPrefill;
export function formatLightSummary(l: LightSnapshot): string;
```

**Steps**

1. Write the module. Core reducer + summary:
```ts
const sameSettings = (a: Omit<LightSnapshot,"count">, b: LightSnapshot) =>
  a.exposureS === b.exposureS && a.gain === b.gain &&
  a.offset === b.offset && a.binning === b.binning;

export function accumulateLight(prev: LightSnapshot | null,
                                next: Omit<LightSnapshot, "count">): LightSnapshot {
  if (prev && sameSettings(next, prev)) return { ...prev, tempC: next.tempC, count: prev.count + 1 };
  return { ...next, count: 1 };
}

export function shouldOfferDarks(l: LightSnapshot | null): l is LightSnapshot {
  return !!l && l.count >= 1 && Number.isFinite(l.exposureS) && l.exposureS > 0;
}

const fmtExp = (s: number) => (s >= 1 ? `${Math.round(s)}s` : `${s}s`);

export function formatLightSummary(l: LightSnapshot): string {
  const parts = [
    l.count >= 1 ? `${l.count} × ${fmtExp(l.exposureS)}` : `${fmtExp(l.exposureS)}`,
    `gain ${l.gain}`,
  ];
  if (l.tempC != null) parts.push(`${l.tempC} °C`);
  return parts.join(" · ");
}

export function darkPrefillFrom(l: LightSnapshot): CalibrationPrefill {
  return {
    frameType: "Dark",
    exposure: String(l.exposureS), gain: String(l.gain),
    offset: String(l.offset), binning: String(l.binning),
    coolerTarget: l.tempC != null ? String(l.tempC) : null,
  };
}
```
2. Write `calibration.test.ts` copying the harness header from
   `eta.test.ts:21-43` (`test`, `eq`, `assert`). Cover:
   - `accumulateLight(null, s)` → `count 1`.
   - two identical `s` → `count 2`; a third with a changed `gain` → `count 1`
     (batch reset).
   - identical settings but new `tempC` → count increments **and** `tempC`
     refreshes.
   - `shouldOfferDarks(null)` false; `count 0` false; a valid snapshot true;
     `exposureS 0` false.
   - `darkPrefillFrom` → `frameType "Dark"`, string fields, `coolerTarget "-10"`;
     `tempC: null` → `coolerTarget: null`.
   - `formatLightSummary({count:60,exposureS:120,gain:120,tempC:-10,…})` ===
     `"60 × 120s · gain 120 · -10 °C"`; and with `count 0`/`tempC null` the
     dropped clauses.

**Commands**
```
cd ui && npx tsx src/lib/__tests__/calibration.test.ts
cd ui && npx tsc -b
```
**Expected:** `calibration.test: N/N passed`; `tsc -b` clean (no output).

---

### Task 2 — `lastLight` store slice

**Impl tier: Sonnet.** A state field + two setters delegating to Task 1.

**Files**
- modify `ui/src/store.ts` (type decl near `:559-598`; init near `:660`; impl
  near `:948-959`; selector with the other `use*` selectors)
- modify `ui/src/__tests__/store.test.ts` (append 2 assertions)

**Interfaces** (added to the store type + implementation)
```ts
// state
lastLight: LightSnapshot | null;
// actions
noteLightFrame: (snap: Omit<LightSnapshot, "count">) => void;
clearLastLight: () => void;
// selector (module scope, with the other exported hooks)
export const useLastLight = () => useStore((s) => s.lastLight);
```

**Steps**
1. `import { accumulateLight, type LightSnapshot } from "./lib/calibration";`
2. Add `lastLight: null` to the initial state; implement:
```ts
noteLightFrame: (snap) => set((s) => ({ lastLight: accumulateLight(s.lastLight, snap) })),
clearLastLight: () => set({ lastLight: null }),
```
3. In `store.test.ts`, drive the store directly:
```ts
useStore.getState().noteLightFrame({ exposureS:120, gain:120, offset:30, binning:1, tempC:-10 });
useStore.getState().noteLightFrame({ exposureS:120, gain:120, offset:30, binning:1, tempC:-9 });
eq(useStore.getState().lastLight?.count, 2, "second identical frame accumulates");
eq(useStore.getState().lastLight?.tempC, -9, "temp refreshes to latest");
```

**Commands**
```
cd ui && npx tsx src/__tests__/store.test.ts
cd ui && npx tsc -b
```
**Expected:** store test passes with the two new assertions; `tsc -b` clean.

---

### Task 3 — CaptureView: frame-type selector, coaching, prefill, prompt

**Impl tier: Sonnet.** Thin render + wiring over tested primitives; correctness
is enforced by `tsc -b` and the Task 1/2 tests behind the logic it calls.

**Files**
- modify `ui/src/views/CaptureView.tsx`

**Interfaces used** (all existing/tested)
```ts
import { SegmentedControl } from "../components/ui";
import { confirmDialog } from "../components/ConfirmDialog";
import { FRAME_TYPES, FRAME_COACH, darkPrefillFrom, shouldOfferDarks,
         formatLightSummary, type FrameType } from "../lib/calibration";
import { useLastLight } from "../store";
```

**Steps**
1. State + refs: `const [frameType, setFrameType] = useState<FrameType>("Light");`
   `const lastLight = useLastLight();` `const frameTypeRef = useRef<FrameType>("Light");`
   then `frameTypeRef.current = frameType;` on each render (mirrors `expLenRef`).
2. `body` (`:102-109`) gains `frame_type: frameType`.
3. Completion effect (`:165-175`): after the loop/idle branch, add
   `if (frameTypeRef.current === "Light") noteLightFrame({ exposureS, gain: gainInvalid ? 0 : gainNum, offset: Number(offset)||0, binning: Number(binning)||1, tempC: cam?.temperature ?? null });`
   (grab `noteLightFrame` via `const noteLightFrame = useStore((s) => s.noteLightFrame);`).
4. Render the `SegmentedControl` + coach text above the button grid (`:290`):
```tsx
<div className="mt-3">
  <SegmentedControl
    ariaLabel="frame type"
    options={FRAME_TYPES.map((f) => ({ value: f, label: f }))}
    value={frameType}
    onChange={setFrameType}
    disabled={!canCapture}
  />
  {frameType !== "Light" && (
    <p className="text-[11px] text-dim mt-2 leading-snug">{FRAME_COACH[frameType]}</p>
  )}
  {(frameType === "Dark" || frameType === "Bias") && (
    lastLight
      ? <button className="btn tap min-h-[44px] mt-2" onClick={() => {
          const p = darkPrefillFrom(lastLight);
          setFrameType(p.frameType); setExposure(p.exposure); setGain(p.gain);
          setOffset(p.offset); setBinning(p.binning);
          if (p.coolerTarget) setCoolerTarget(p.coolerTarget);
        }}>Match last lights</button>
      : <span className="btn tap min-h-[44px] mt-2 opacity-40 inline-flex items-center gap-1.5"
              aria-disabled title="Shoot some lights first">
          <Icon name="lock" size={12} /> Match last lights
        </span>
  )}
</div>
```
5. End-of-session prompt in `onStop` (`:217-223`), after `api.post("/api/capture/stop")`:
```ts
const wasLightLoop = looping && frameTypeRef.current === "Light";
if (wasLightLoop && shouldOfferDarks(lastLight) && !offeredRef.current) {
  offeredRef.current = true;
  void confirmDialog({
    title: "Take matching darks?",
    body: `You shot ${formatLightSummary(lastLight!)} — shoot matching darks now?`,
    confirmLabel: "Set up darks", cancelLabel: "Not now", confirmPrimary: true,
  }).then((ok) => {
    if (!ok) return;
    const p = darkPrefillFrom(lastLight!);
    setFrameType("Dark"); setExposure(p.exposure); setGain(p.gain);
    setOffset(p.offset); setBinning(p.binning);
    if (p.coolerTarget) setCoolerTarget(p.coolerTarget);
    showToast("info", "Darks set up — cap the scope, then press Single or Loop.");
  });
}
```
`const offeredRef = useRef(false);` reset to `false` whenever a fresh Light loop
begins (in `onLoop`, when `frameType === "Light"`). Ensure `Icon` is imported
(it already is, `:10`).

**Commands**
```
cd ui && npx tsc -b
```
**Expected:** clean typecheck. (Thin render — no separate runtime test; logic is
covered by Tasks 1–2. A manual smoke: pick Dark → coach shows; Loop lights → Stop
→ prompt appears.)

---

### Task 4 — Backend: thread `frame_type` through the loop path

**Impl tier: Sonnet.** Two-line signature/param thread + a route arg; one pytest.

**Files**
- modify `server/astrodeck/hub.py` (`start_loop`, `:1683-1707`)
- modify `server/astrodeck/api/app.py` (`capture_loop`, `:2390`)
- modify `server/tests/test_hub_capture_precession.py` (append one test)

**Interfaces**
```python
# hub.py
async def start_loop(self, exposure_s: float, gain: int, offset: int,
                     binning: int = 1, frame_type: str = "Light") -> None: ...
#   inner _loop(): await self.capture(exposure_s, gain, offset, binning, frame_type=frame_type)

# app.py capture_loop route
await hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning,
                     frame_type=body.frame_type)
```

**Steps**
1. Add the `frame_type` param to `start_loop`; pass it into the `self.capture(...)`
   call at `hub.py:1700`.
2. Pass `frame_type=body.frame_type` in the `capture_loop` route (`app.py:2390`).
3. Append a pytest next to `test_capture_frame_type_controls_shutter`:
```python
@pytest.mark.asyncio
async def test_start_loop_threads_frame_type(monkeypatch, tmp_path):
    """A Loop of calibration frames must carry frame_type into each capture
    (regression: the loop path used to drop it and always shoot Light)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        calls: list[str | None] = []
        async def spy_capture(*a, **k):
            calls.append(k.get("frame_type"))
            await asyncio.sleep(0)
        monkeypatch.setattr(h, "capture", spy_capture)
        await h.start_loop(0.01, 100, 30, 1, frame_type="Dark")
        await asyncio.sleep(0.02)
        h.stop_loop()
        assert calls and all(ft == "Dark" for ft in calls)
    finally:
        await h.disconnect_all()
```
   (`asyncio`, `pytest`, `Hub`, `hub_module` are already imported in this test file.)

**Commands**
```
server\.venv\Scripts\pytest.exe server/tests/test_hub_capture_precession.py -n0 -q
```
**Expected:** the existing shutter test plus `test_start_loop_threads_frame_type`
pass.

---

## 4. Open decisions

1. **Loop `frame_type` — in scope or defer?** Recommendation: **in scope**
   (Task 4). Shooting a batch of N darks is naturally a Loop; the single-capture
   path already works, but Loop silently opening the shutter on a "dark" batch is
   a real correctness bug. The change is fully additive with a pytest.

2. **When to fire the nudge.** Recommendation: **only on stopping a Light *loop***
   (not after each Single, which would nag). Guarded by `offeredRef` so it offers
   once per light-loop batch.

3. **"Match last lights" for Bias.** Recommendation: for now the button always
   prefills a **Dark** (matched exposure). Bias (zero exposure) is reachable by
   selecting the Bias segment directly; auto-zeroing exposure on prefill is a
   later polish, not MVP.

4. **Persist `lastLight` across reloads?** Recommendation: **no** — in-memory
   store slice only. The nudge is a same-session convenience; a persisted
   yesterday's-lights prompt on a fresh launch would be noise.

5. **Temp source for the match.** Recommendation: record the **live sensor temp**
   (`cam.temperature`) at frame-land time, not the cooler set-point — darks should
   match the temperature the sensor actually ran at. Null when no cooler reports a
   temperature (temp clause simply dropped from the summary/prefill).
