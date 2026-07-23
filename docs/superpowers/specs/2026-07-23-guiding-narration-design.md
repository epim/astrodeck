# NOV-7 — Plain-language guiding narration + words verdict (novice)

Combined design spec + TDD implementation plan. One file.

Feature: narrate the guide engine's phases in plain words ("Finding a guide
star… Calibrating… Settling… Guiding well — you can relax") and add a words
verdict for RMS ("0.7 px ≈ 1.1″ — good for 3-minute subs"), doing the
pixels↔arcsec honesty for the user.

---

## 1. Design

### 1.1 Goal

A novice opens the Guide view during a session and sees, in plain English:

1. **What the guider is doing right now** — one sentence that tracks the
   engine's lifecycle: finding a star → calibrating → settling → guiding well.
2. **Whether the guiding is good enough** — the RMS number translated into a
   verdict with honest units: `0.7 px ≈ 1.1″ — good for 3-minute subs`.

Both come from a **single pure function** `guideNarration(input) →
{ phaseText, tone, verdict }` (matches the brief's `phase/state/rms/is_arcsec →
{phase_text, verdict}`), unit-tested via `npx tsx`. The `GuideView` render is a
thin binding: compute once, drop two lines into the existing panel.

### 1.2 Current-state seams (every claim below was read in this repo)

**What the "guide" event / `status.guider` actually carry to the client — the
whole payload, no phase:**

- `server/astrodeck/guide/base.py:18-34` — the `GuideStats` dataclass, the ONE
  shape on both the `"guide"` bus channel and `status.guider`. Fields:
  `guiding, rms_ra, rms_dec, rms_total, snr, recent, is_arcsec (default False),
  image_scale (default 0.0)`. **No phase. No settling.**
- `ui/src/types.ts:138-151` — the TS mirror `GuideStats`: same fields,
  `is_arcsec?`/`image_scale?` optional. **No phase.**
- `ui/src/store.ts:1210-1231` — the `"guide"` event ingest: `const stats =
  ev.data as GuideStats; set({ guide: stats, ... })`. Passes the payload
  through verbatim; there is no phase to carry.
- `server/astrodeck/hub.py:2372` — `out["guider"] =
  self.guider.stats().__dict__ | {"name": self.guider.name}`. The status-poll
  guider is literally `stats().__dict__`, so **any field added to the
  `GuideStats` dataclass flows to BOTH the WS event and the poll for free.**

**Where RMS is shown today (a bare number) + the is_arcsec honesty already
present:**

- `ui/src/views/GuideView.tsx:43` — `const stats = guide ?? status?.guider ??
  null;` (both sources are `GuideStats`).
- `GuideView.tsx:50` — `const isArcsec = stats?.is_arcsec !== false;` (absent ⇒
  arcsec; only explicit `false` ⇒ px). Mirror this in the caller.
- `GuideView.tsx:53-54` — `unit = isArcsec ? "″" : "px"` (true prime glyph,
  UX-35).
- `GuideView.tsx:92-98` — the RMS stat grid: `rms_ra/rms_dec/rms_total/snr` each
  `toFixed(2)`; `rms_total` gets a tone at `:96` (`<1` good, `<2` warn, else
  bad). This is the only "verdict" today and it is a color, not words.
- `GuideView.tsx:99-105` — the existing UX-15 note shown when `stats &&
  !isArcsec`: "RMS is in guide-camera pixels. Set the guide scope's focal
  length in Optics to report arcsec." **We keep this note untouched** and the
  words-verdict defers to it in the px case (see §1.4).
- `GuideView.tsx:86-88` — the only phase-ish UI today: a static `"● guiding"`
  badge when `stats?.guiding`.

**The engine DOES track phases — but they never reach the wire, and worse, the
finding/calibrating steps emit no events at all:**

- `native/crates/astro-guide/src/engine.rs:250-260` — `enum Phase { Idle,
  Calibrating, Guiding }`. **Private** (no `pub`), held in
  `GuideEngine.phase` (`engine.rs:267`).
- `engine.rs:219-233` — `GuideStatsSnapshot { guiding, settling, rms_ra,
  rms_dec, rms_total, snr, recent, secondaries }`. **The private `Phase` is NOT
  in the snapshot;** the only lifecycle bit exposed is `settling: bool`.
- `native/crates/astrodeck-native/src/lib.rs:1253-1258` — the PyO3
  `GuideEngine` additionally keeps a `calibrating`/`settling` shadow, but
  `lib.rs:1389-1417` `stats()` emits only `{guiding, settling, rms_ra, rms_dec,
  rms_total, snr, recent, secondaries}` — **no `phase`, no `calibrating`.** The
  `calibrating` shadow is used solely by `classify_lock_lost`
  (`lib.rs:1499-1520`), never surfaced.
- `server/astrodeck/guide/native.py:729-767` — `NativeGuider.stats()` maps the
  engine dict → `GuideStats`. It reads `guiding, rms_*, snr, recent` and
  computes `is_arcsec`/`image_scale` (`:753-766`) — but **drops `settling`
  entirely** and stamps no phase.
- `native.py:338-381` — `_calibrate()` exposes + `process()`es frames in a loop
  but **never `bus.publish("guide", …)`**; it only `bus.log(...)`s. The first
  guide-stats publish is at `native.py:317`, AFTER calibration completes.
  ⇒ **During finding/calibrating the client receives zero guide ticks** — it is
  blind to those phases regardless of any field we add.
- `native.py:520-531` — `_engine_settling()` already reads the engine's
  `settling` bool host-side; `native.py:160-164` holds the host lifecycle state
  `_active` / `_lost` / `_reacquire`. **The phase can be composed entirely in
  Python from state that already exists — no Rust change.**

**Adjacent RMS helpers (read to avoid a wrong reuse):**

- `ui/src/lib/rmsCompare.ts:16-58` + `ui/src/lib/guideRms.ts` — the same-night
  **native-vs-PHD2 head-to-head** comparison (two windows → which guided
  tighter). That is a DIFFERENT feature; our words-verdict is a single-window
  quality read. No overlap, no reuse — a new file.

### 1.3 Approach — the phase (what must be surfaced, minimally)

Faithful narration of "Finding… Calibrating… Settling…" needs two genuinely
missing things: (a) a `phase` string on the wire, and (b) a guide tick emitted
during the finding/calibrating steps (which are silent today). Both land in
**Python only** — the engine's `settling` and the host's `_active`/`_lost` are
already available to `native.py`, so **no cargo/maturin wheel rebuild.**

Wire vocabulary (a plain string on `GuideStats`, optional/best-effort):

```
GuidePhase = "idle" | "finding" | "calibrating" | "settling" | "guiding" | "lost"
```

`GuideStats.phase` defaults to `""` (unknown). The PHD2/NINA bridge guider
leaves it `""`; the narration falls back to the `guiding` bool for it (honest —
the bridge doesn't expose these steps). Only `NativeGuider` fills it richly.

`NativeGuider` composes the phase from state it already holds:

| host/engine state (native.py)                                   | phase          |
|-----------------------------------------------------------------|----------------|
| `self._lost`                                                    | `"lost"`       |
| `self._engine_settling()` (settle window open)                  | `"settling"`   |
| `self._active` and engine dict `guiding` is true               | `"guiding"`    |
| `self._phase_hint` set to `"finding"` / `"calibrating"`        | that hint      |
| `self._active` (loop up, lock not yet established)              | `"finding"`    |
| otherwise                                                       | `"idle"`       |

`self._phase_hint` is a small host var set during `start_guiding`/`_calibrate`
and cleared once the guide loop owns the phase (see Task 2). Two extra
`bus.publish("guide", …)` calls (one at the star-find, one at calibration
begin) give the client at least one `"finding"` tick and one `"calibrating"`
tick before guiding ticks start — enough to narrate the transition.

### 1.4 Approach — the RMS words-verdict (zero backend)

100% available client-side today: `rms_total`, `is_arcsec`, `image_scale` are
all on the wire (`native.py:753-766`), and when `is_arcsec` is true
`image_scale` is guaranteed `> 0` (`native.py:753,766`), so the pixel value is
always recoverable as `px = rms_total / image_scale`.

- **`is_arcsec` true** (rms_total is arcsec): `arc = rms_total`,
  `px = image_scale > 0 ? rms_total / image_scale : null`. Verdict:
  `"<px> px ≈ <arc>″ — <quality>"` (or `"<arc>″ — <quality>"` if px unknown).
- **`is_arcsec` false** (rms_total is px, no scale): verdict is `null` — there
  is nothing honest to say in arcsec, and the existing UX-15 note
  (`GuideView.tsx:99-105`) already explains how to fix it. No duplication.

Quality band on the arcsec value (display heuristic; tuned so the brief's
example 1.1″ lands in the "3-minute subs" band — see Open decision D1):

| arcsec RMS   | quality clause                       | tone |
|--------------|--------------------------------------|------|
| ≤ 0.6″       | `excellent — long subs are fine`     | good |
| ≤ 1.2″       | `good for 3-minute subs`             | good |
| ≤ 2.0″       | `OK for short subs`                  | warn |
| > 2.0″       | `high — expect some star trailing`   | bad  |

### 1.5 The pure function (data shapes)

`ui/src/lib/guideNarration.ts`:

```ts
export type GuidePhase =
  | "idle" | "finding" | "calibrating" | "settling" | "guiding" | "lost";

export interface GuideNarrationInput {
  connected: boolean;    // !!status?.guider || !!guide  (GuideView.tsx:44)
  phase?: string;        // GuideStats.phase; "" / absent ⇒ unknown (fallback)
  guiding: boolean;      // GuideStats.guiding
  rmsTotal: number;      // GuideStats.rms_total
  isArcsec: boolean;     // caller passes stats?.is_arcsec !== false (GuideView.tsx:50)
  imageScale: number;    // GuideStats.image_scale ?? 0  (arcsec/px, 0 = unknown)
  hasSamples: boolean;   // (recent?.length ?? 0) > 0
}

export type NarrationTone = "good" | "warn" | "bad" | "neutral";

export interface GuideNarration {
  phaseText: string;         // "Guiding well — you can relax"
  tone: NarrationTone;       // colors both the phase line and the verdict
  verdict: string | null;    // "0.7 px ≈ 1.1″ — good for 3-minute subs" | null
}
```

Phase-text copy table (evaluated top-down; first match wins):

| condition                                              | phaseText                                    | tone    |
|--------------------------------------------------------|----------------------------------------------|---------|
| `!connected`                                           | `No guider connected`                        | neutral |
| `phase === "lost"`                                     | `Lost the guide star — trying to recover`    | bad     |
| `phase === "finding"`                                  | `Finding a guide star…`                      | neutral |
| `phase === "calibrating"`                              | `Calibrating the guider…`                    | neutral |
| `phase === "settling"`                                 | `Settling after the move…`                   | warn    |
| guiding* and `!hasSamples`                             | `Guiding — measuring…`                       | neutral |
| guiding* and verdict-quality good                      | `Guiding well — you can relax`               | good    |
| guiding* and verdict-quality warn                      | `Guiding — still settling down`              | warn    |
| guiding* and verdict-quality bad                       | `Guiding, but the error is high`             | bad     |
| connected, not guiding (idle/unknown)                  | `Ready to guide`                             | neutral |

\* "guiding" = `phase === "guiding" || (phase falsy && guiding === true)`.

Verdict is non-null only when guiding\* and `rmsTotal > 0` and `isArcsec`
(§1.4). Numbers use `toFixed(1)` (friendly approximation with `≈`; the precise
`toFixed(2)` values stay in the RMS grid above).

### 1.6 Placement (thin render in GuideView)

- `phaseText`: a prominent sentence at the top of the "Guide Error" panel — as
  the panel's `right=` content replacing/next to the `"● guiding"` badge
  (`GuideView.tsx:83-90`), or a `<p>` directly under the graph. Tone-colored via
  a small class map (`good→text-good`, `warn→text-warn`, `bad→text-bad`,
  `neutral→text-dim`).
- `verdict`: a `<p className="text-[11px] …">` under the RMS grid
  (`GuideView.tsx:98`), rendered only when non-null. Sits right beside the
  existing UX-15 note (`:99-105`), which stays.

No new logic in the view — it computes `guideNarration({...})` once and renders
the two strings. The typechecker (`npx tsc -b`) verifies the binding.

---

## 2. Global Constraints (verbatim, binding)

- **Privacy** — the real coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  `"[SITE-LABEL]"` must NEVER appear in code, tests, or docs. Site default is
  `"My Observatory"` / `0.0`. (This feature touches none of these; keep it that
  way — no coordinates, no site strings in any narration copy or test.)
- **Never `git add -A`.** Stage named paths only.
- **UI gate:** `cd ui && npx tsc -b` must pass.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert, idiom
  `ui/src/lib/__tests__/eta.test.ts` and
  `ui/src/components/__tests__/healthStrip.test.ts`.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` from the repo root,
  `-n0` (single worker).
- **Rust:** `native/` has its own cargo tests — `cargo test` in the crate.
  (This plan deliberately touches **no Rust**, so no cargo run is required.)
- **Client toasts** via `useStore.getState().enqueueToast` (not used here — the
  narration is read-only display text).
- **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`, never native `disabled`. (No new controls in this feature.)
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Three right-sized tasks. Order: T1 (pure fn, ships the RMS verdict + basic phase
text with `phase` absent) → T2 (wire the real `phase`) → T3 (thin render).

### Task 1 — pure narration function + tsx test  ·  [Sonnet]

*Mechanical: an if-ladder over the copy table in §1.5 + one division for px↔arcsec.
No subtle algorithm/numerics. Sonnet.*

**Files**
- create `ui/src/lib/guideNarration.ts`
- create (test) `ui/src/lib/__tests__/guideNarration.test.ts`

**Interfaces** — exactly the block in §1.5 (`GuidePhase`, `GuideNarrationInput`,
`NarrationTone`, `GuideNarration`, `guideNarration(input): GuideNarration`).

**Steps**

1. Write `guideNarration.ts`. Illustrative core (implementer follows §1.5
   tables exactly):

   ```ts
   const PRIME = "″"; // ″  (matches GuideView.tsx:51 UX-35)

   function quality(arc: number): { clause: string; tone: NarrationTone } {
     if (arc <= 0.6) return { clause: "excellent — long subs are fine", tone: "good" };
     if (arc <= 1.2) return { clause: "good for 3-minute subs", tone: "good" };
     if (arc <= 2.0) return { clause: "OK for short subs", tone: "warn" };
     return { clause: "high — expect some star trailing", tone: "bad" };
   }

   export function guideNarration(i: GuideNarrationInput): GuideNarration {
     if (!i.connected) return { phaseText: "No guider connected", tone: "neutral", verdict: null };

     const p = i.phase;
     if (p === "lost")        return { phaseText: "Lost the guide star — trying to recover", tone: "bad", verdict: null };
     if (p === "finding")     return { phaseText: "Finding a guide star…", tone: "neutral", verdict: null };
     if (p === "calibrating") return { phaseText: "Calibrating the guider…", tone: "neutral", verdict: null };
     if (p === "settling")    return { phaseText: "Settling after the move…", tone: "warn", verdict: null };

     const guiding = p === "guiding" || (!p && i.guiding);
     if (!guiding) return { phaseText: "Ready to guide", tone: "neutral", verdict: null };

     if (!i.hasSamples || i.rmsTotal <= 0)
       return { phaseText: "Guiding — measuring…", tone: "neutral", verdict: null };

     // verdict only when we can speak in arcsec honestly (§1.4)
     let verdict: string | null = null;
     let tone: NarrationTone = "neutral";
     let text = "Guiding well — you can relax";
     if (i.isArcsec) {
       const arc = i.rmsTotal;
       const q = quality(arc);
       tone = q.tone;
       const px = i.imageScale > 0 ? arc / i.imageScale : null;
       verdict =
         (px != null ? `${px.toFixed(1)} px ≈ ${arc.toFixed(1)}${PRIME}` : `${arc.toFixed(1)}${PRIME}`) +
         ` — ${q.clause}`;
       text = tone === "good" ? "Guiding well — you can relax"
            : tone === "warn" ? "Guiding — still settling down"
            : "Guiding, but the error is high";
     }
     return { phaseText: text, tone, verdict };
   }
   ```

2. Write `guideNarration.test.ts` using the inline-assert harness from
   `eta.test.ts` (copy the `test/eq/assert` helpers; end with the
   `passed/total` console line + `export const result`). Cover:
   - not connected → `"No guider connected"`, verdict null.
   - `phase:"finding"` → `"Finding a guide star…"`; `"calibrating"` →
     `"Calibrating the guider…"`; `"settling"` → tone warn; `"lost"` → tone bad,
     verdict null.
   - **the headline case**: `{connected:true, phase:"guiding", guiding:true,
     rmsTotal:1.1, isArcsec:true, imageScale:1.5714, hasSamples:true}` →
     `verdict === "0.7 px ≈ 1.1″ — good for 3-minute subs"`, `tone === "good"`,
     `phaseText === "Guiding well — you can relax"`.
   - arcsec-only (imageScale 0) → verdict `"1.1″ — good for 3-minute subs"`.
   - `isArcsec:false` while guiding → `verdict === null` (UX-15 note owns it),
     phaseText still a guiding sentence.
   - `rmsTotal:0` / `hasSamples:false` while guiding → `"Guiding — measuring…"`,
     verdict null.
   - phase-absent fallback: `{phase:undefined, guiding:true, …good rms}` →
     guiding verdict (proves T1 ships value before T2).
   - band boundaries: `arc = 0.6` good, `1.2` good, `2.01` bad.

**Commands & expected output**
- `cd ui && npx tsx src/lib/__tests__/guideNarration.test.ts`
  → `guideNarration.test: N/N passed` (no failure lines).
- `cd ui && npx tsc -b` → exits 0, no diagnostics.

### Task 2 — surface `phase` on the wire (Python + TS type)  ·  [Sonnet]

*Mechanical: add a dataclass field, a small if-ladder `_current_phase()`, two
`bus.publish` calls, and one optional TS field. Sonnet.*

**Files**
- modify `server/astrodeck/guide/base.py` (add `phase` to `GuideStats`)
- modify `server/astrodeck/guide/native.py` (`_phase_hint`, `_current_phase()`,
  stamp phase in `stats()`, publish finding/calibrating ticks)
- modify `ui/src/types.ts` (add optional `phase` to `GuideStats`)
- create (test) `server/tests/test_native_guide_phase.py`

**Interfaces**
- `base.py`: `GuideStats.phase: str = ""` (append after `image_scale`; keep the
  dataclass field order additive so `__dict__` spread at `hub.py:2372` and the
  `**stats().__dict__` publish both pick it up automatically).
- `native.py`: `def _current_phase(self) -> str:` per the §1.3 table; a
  `self._phase_hint: str | None = None` initialized in `__init__` near
  `native.py:160`.
- `types.ts`: `phase?: GuidePhase | string;` on `GuideStats` (import/duplicate
  the `GuidePhase` union or use a bare string with a doc comment — keep it
  optional so bridge/legacy payloads type-check).

**Steps**

1. `base.py` — add the field with a docstring noting `""` = unknown and that the
   native guider fills it; the bridge leaves it `""`.
2. `native.py::stats()` (`:758-767`) — set `phase=self._current_phase()` on the
   returned `GuideStats(...)`. Add `_current_phase()` implementing the §1.3
   table using `self._lost`, `self._engine_settling()` (`:520`), the engine
   dict `s.get("guiding")`, `self._active`, and `self._phase_hint`.
3. `native.py::start_guiding()` / `_calibrate()` — set `self._phase_hint =
   "finding"` before the star-find (`:260` reuse path and `:343` cal path) and
   `bus.publish("guide", **self.stats().__dict__)` once; set `self._phase_hint =
   "calibrating"` right after `begin_calibration` (`:349`) and publish once;
   clear `self._phase_hint = None` just before the guide loop starts (`:313`)
   and on stop.
4. `test_native_guide_phase.py` — mirror the style of
   `test_native_guide_surface.py` (`pytest.importorskip("astrodeck_native")`,
   Gaussian frames), but drive the **host** `NativeGuider` against the sim
   guide camera + a pulse-guide sim mount (reuse the fixtures already used by
   `test_native_guider_e2e.py` — grep it for the rig/camera setup). Assert:
   - a fresh idle guider → `stats().phase == "idle"`.
   - after a full `start_guiding()` on a sim rig → `stats().phase ==
     "guiding"`.
   - mid-`dither()` the published/`stats()` phase is `"settling"` (assert
     `_engine_settling()` path — reuse the dither harness from
     `test_native_guider_dither.py`).
   - after a latched loss (`_lost = True`) → `stats().phase == "lost"`.
   - `stats().__dict__` contains the `"phase"` key (guards the `hub.py:2372`
     poll path).

**Commands & expected output**
- `server/.venv/Scripts/pytest.exe server/tests/test_native_guide_phase.py -n0 -q`
  → all pass (or `skipped` if the wheel is absent — the `importorskip` guard).
- `server/.venv/Scripts/pytest.exe server/tests/test_native_guide_surface.py -n0 -q`
  → still green (regression guard on the stats shape).
- `cd ui && npx tsc -b` → exits 0 (the new optional field type-checks).

### Task 3 — thin GuideView render wiring  ·  [Sonnet]

*Mechanical: import, compute once, render two strings. No logic. Verified by the
typechecker. Sonnet.*

**Files**
- modify `ui/src/views/GuideView.tsx`

**Interfaces** — none new; consumes `guideNarration` + the existing `stats`.

**Steps**

1. Import: `import { guideNarration } from "../lib/guideNarration";`.
2. After the `isArcsec` line (`GuideView.tsx:50`), compute:

   ```tsx
   const narration = guideNarration({
     connected,
     phase: stats?.phase,
     guiding: !!stats?.guiding,
     rmsTotal: stats?.rms_total ?? 0,
     isArcsec,
     imageScale: stats?.image_scale ?? 0,
     hasSamples: (stats?.recent?.length ?? 0) > 0,
   });
   const toneClass = { good: "text-good", warn: "text-warn", bad: "text-bad", neutral: "text-dim" }[narration.tone];
   ```

3. Render `narration.phaseText` prominently in the panel header — e.g. replace
   the static `"● guiding"` badge span (`:86-88`) with the tone-colored
   `narration.phaseText`, or add a `<p className={`text-sm ${toneClass} …`}>`
   under the graph (`:91`). Keep it one line.
4. Render the verdict under the RMS grid (after `:98`, before/next to the UX-15
   note at `:99`):

   ```tsx
   {narration.verdict && (
     <p className={`text-[11px] mt-2 leading-snug ${toneClass}`}>{narration.verdict}</p>
   )}
   ```

5. Leave the UX-15 note (`:99-105`) exactly as-is (owns the `!isArcsec` case).

**Commands & expected output**
- `cd ui && npx tsc -b` → exits 0, no diagnostics. (Thin render; the
  typechecker is the gate. No runtime test — no new logic to exercise.)

---

## 4. Open decisions

- **D1 — Sub-length quality bands (§1.4 table).** The `≤0.6 / ≤1.2 / ≤2.0` cuts
  and the "3-minute subs" copy are a display heuristic, not physics (RMS→sub
  length depends on scale, seeing, tolerance). *Recommendation:* ship the table
  as-is (tuned so the brief's 1.1″ reads "good for 3-minute subs"), keep the
  hedging `≈` / "OK"/"expect some", and expose the thresholds as named consts in
  `guideNarration.ts` so a later product tweak is a one-line edit. No config
  surface for now.
- **D2 — Publish cadence during calibration.** *Recommendation:* one tick per
  phase transition (`finding`, `calibrating`) — cheap and enough to narrate the
  step; the guide loop already publishes per frame once guiding. Do not add a
  per-cal-leg publish (noisy, no extra narration value).
- **D3 — Is `"finding"` worth a distinct phase from `"calibrating"`?** The
  native flow does a brief star-find then calibrates. *Recommendation:* keep
  both (the brief's copy names them separately); it is fine for `"finding"` to
  be short-lived. Zero extra cost given the §1.3 composition.
- **D4 — Bridge/PHD2 phase.** *Recommendation:* leave `phase = ""` for the
  bridge guider now; the narration falls back to the `guiding` bool (honest).
  Enriching it from PHD2's `AppState` is a separate, later task — out of scope.
- **D5 — Replace vs. augment the `"● guiding"` badge.** *Recommendation:*
  replace it with `narration.phaseText` (the badge is a strict subset — "guiding"
  is one of the phrases) to avoid two competing status affordances.
```
