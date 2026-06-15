# AstroDeck — Unified Monitor / Session Dashboard — Build-Ready Spec

**Date:** 2026-06-15
**Status:** Final (revised after 3 adversarial UX critiques)
**Surface owner:** Monitor / Session Dashboard
**Depends on:** reliability/toast-queue work (B7 — `runBanner` field), server-side
site config (already SF-hardcoded; uses `hub.site` as-is here).

---

## 0. Overview

A single glanceable **Monitor** view that aggregates, with zero tab-switching during
a run: last-frame thumbnail, guide sparkline + RMS, sequence progress
(bar + % + current step + honest ETA/finish + frames done/total + flagged),
camera temp + cooler, a meridian-flip countdown, and **liveness/stall detection**.

It is a **run monitor**, not a permanent home (resolves critique F2): it is *not*
placed first in the nav and is *not* the cold-start landing view. It is reached
via a persistent, non-modal "Sequence running — open Live" banner when a run
starts, and via a normal nav entry. When opened with no run active it still shows
live guide/thermal/preview if devices are connected, but its primary job is the run.

The view is **read-only for device controls**, but it **does** expose the in-run
sequence controls a user actually reaches for: **Pause / Resume** and **Abort**
(resolves A1/B5). Abort and Pause/Resume are the only writes; both are safe to
reach on a propped phone in the dark (Pause is non-destructive; Abort is
hold-to-confirm). Abort works over plain HTTP even when the telemetry WebSocket is
down (resolves A2).

### What changed from the draft (critique resolutions)

| # | Critique | Resolution |
|---|---|---|
| A1/B5 | Read-only drops Pause/Resume | **Pause/Resume added** to header (Pause = plain button; both reachable in-run) |
| A2 | Abort disabled on WS-down | **Abort always enabled**, POSTs over HTTP; "link down — sending anyway" state |
| A3/B6 | Forced auto-redirect hijacks user | **No forced redirect ever.** Persistent `runBanner` toast only; auto-select only from `connect`/idle after N s idle |
| A4/B1/B2/B3/B4/A4 | False-confidence + sawtooth + unstable ETA | ETA: low-confidence `~` until ≥3 frames; **deterministic** dither/AF/flip event costs (no smear); dual relative+absolute from **one client clock**; cooling shows state not a number |
| A5/B9 | Cosmetic motion survives stalls | **"last frame N ago"** from real event ts; sub-frame bar **freezes/greys** on stall; numeric liveness text (no motion-only signal) |
| C1/B8 | guideTrail self-contradictory | **Reuse `status.guider.recent` / `guide.recent` directly**; delete the separate `guideTrail` slice |
| C2 | Auto-y-scale flattens spikes | **Fixed ±4″ scale** with clip indicators; no per-tick rescale |
| C3/D16 | RMS color-only, no novice context | RMS gets **glyph + qualitative word** ("RMS 1.3″ ✓ good") |
| D1/E.11 | Bright thumbnail wrecks dark adaptation | **Per-tile night brightness slider** (persisted), ships day-one; LIVE border is a dim outline, not a glow |
| D2/E.12 | blink-as-urgency, no reduced-motion | Urgency = glyph+text+ring-fill; **global `prefers-reduced-motion` block** kills all decorative motion |
| D3/E.1/E.2 | glyph-only states, reuse SequenceView color | New `stateMeta()` map (glyph **+ word always**); **do not** reuse `SequenceView.tsx:112` color-only logic |
| E1/D17 | Power evicted from nav | **Do not touch nav order or Power.** Additive route only |
| E2 | Bundles rail reorder | **Decoupled.** No rail reorder in this PR |
| E3 | Error CTA → desktop-only drawer | Error renders `detail` + last error log lines **inline** (no drawer punt) |
| F1 | "flagged" tooltip (no touch hover) | Inline non-alarming micro-label, no tooltip |
| F3 | Stale frame stranded on load error | `onError` handler + LIVE/STALE tied to **real frame age** |
| F4 | LIVE means 3 things | LIVE defined precisely: new frame within `LIVE_WINDOW_S` |
| F5 | "flip off" ambiguous vs pier risk | Distinguish "N/A fork mount" from "flip DISABLED — risk near meridian" (warning) |
| G1 | One MonitorView ticker re-renders all | 1s ticker pushed into **leaf time components** only |
| G2/C13 | 24-id filmstrip fights 8-frame cache | **Filmstrip cut.** Keep only last frame + one stall timestamp |
| A1(crit2)/A2(crit2) | meridian/cooler dead on sim+Alpaca | **Compute `hours_to_flip` server-side from HA** in hub; sim cooler-power model; `can_report_cooler_power` capability |
| C14 | NINA-native run = blank Monitor | Explicit "NINA is driving — open NINA" state |
| C10/C11 | client clock vs server epoch | Server sends `eta_s` + `server_now_ms`; client derives finish from its own clock |
| D20 | granular selectors defeated by wholesale `status` replace | `useStatusSlice` uses `useStore` with **`shallow`** equality |
| Accessibility 6/7/8 | tap targets, focus rings, hold semantics | ≥44px everywhere; `:focus-visible` rings; HoldButton keyboard path + `aria-live` |

### Rejected / deferred critique items (with reason)

- **Live stacking, disk-space, battery telemetry, HFR-trend sparkline (B9 partial,
  H):** Disk-space and HFR-trend are **accepted as v1.1**, not v1 — they need new
  backend signals (`shutil.disk_usage`, a per-frame HFR ring) that belong to the
  reliability/safety batch, not this view. The **stall alarm** (last-frame-age) and
  a single-line **HFR-trend mini-sparkline** *are* included (cheap, data already
  present). Battery/power telemetry lives on the Power view (we explicitly do **not**
  evict Power), so the Monitor links to it rather than duplicating.
- **Audible/haptic alerts (H):** Local `navigator.vibrate` on `error`/`flip-due`/
  `complete` is **included** (cheap, no backend). Push/ntfy is the safety batch's
  job (review P0), out of scope here; the Monitor is wired to consume it later.
- **Mount past-meridian / below-horizon *limit alarm* (H):** This is the safety
  batch's horizon/pier-limit enforcement (review P0), not the Monitor's. The
  Monitor **surfaces** the meridian countdown and a "BELOW HORIZON" chip if
  `mount.alt < 0`, but does not own limit enforcement.
- **Exponential cooling-ETA model (B2):** Rejected in favor of dropping the numeric
  cooling ETA entirely (show temp→target + "AT TARGET ✓"), per A3. Simpler and
  honest; a number we can't honor is worse than none.

---

## 1. Files — exact create / modify plan (disjoint ownership)

Ownership is **disjoint by file** so parallel implementers do not collide. The
shared contracts (§3) are landed **first, by one owner (Owner-X)**, then the rest
proceed in parallel against the frozen contract.

### Owner-X — Shared contracts (land first, blocks others)
- **MODIFY** `ui/src/types.ts` — new/extended interfaces (§3.1).
- **MODIFY** `ui/src/store.ts` — `ViewName += "monitor"`, new slices, selectors, `runBanner`, autoMonitor edge detect, `handleEvent` extension (§3.2). **Owns the whole file.**
- **MODIFY** `server/astrodeck/devices/base.py` — `Camera.get_cooler()` default, `can_report_cooler_power` flag, `Telescope.flip_info()` helper stub (§3.4).

### Owner-A — Backend telemetry (Python, disjoint from Owner-X's base.py edits by being additive methods; coordinate the single base.py touch)
- **MODIFY** `server/astrodeck/hub.py` — `poll_status()` emits `camera.cooler` + top-level `meridian` (server-computed HA) (§6.1, §6.5).
- **MODIFY** `server/astrodeck/sequence/engine.py` — paused-aware `elapsed_s`, deterministic ETA fields + `server_now_ms`, `current_exposure_s`, frame-completion timestamps (§5).
- **MODIFY** `server/astrodeck/devices/sim.py` — `SimCamera.get_cooler()` with a power model; `SimTelescope` keeps `pier_side`=WEST (flip computed in hub).
- **MODIFY** `server/astrodeck/devices/alpaca.py` — `AlpacaCamera.get_cooler()` reading optional `coolerpower` with capability detection.
- **MODIFY** `server/astrodeck/devices/nina.py` — `NinaCamera.get_cooler()` (optional `CoolerPower`/`CoolerOn`); keep native `time_to_meridian_flip`.
- **MODIFY** `server/astrodeck/api.py` (or wherever routes live) — add `GET /api/monitor/snapshot` aggregator (§8).

### Owner-B — Monitor presentational cells (new files, fully disjoint)
- **CREATE** `ui/src/components/monitor.tsx` — `Sparkline`, `CountdownTile`, `ThermometerBar`, `HoldButton`, `PauseButton`, `PreviewTile`, `MetricStrip`, `StateBadge`, `LiveTimer` (the leaf 1s ticker). (`Donut` from the draft is removed — `CountdownTile` is the single countdown primitive.)
- **CREATE** `ui/src/lib/eta.ts` — pure formatting/countdown math (no React).
- **CREATE** `ui/src/lib/eta.test.ts` — vitest unit tests for the ETA/format math.
- **CREATE** `ui/src/lib/stateMeta.ts` — `stateMeta(state) → {Icon, label, tone, blinkable}` map (resolves D3/E.1).

### Owner-C — Monitor view + app wiring
- **CREATE** `ui/src/views/MonitorView.tsx` — the grid, all cells, all UI states.
- **MODIFY** `ui/src/App.tsx` — add `monitor` to `VIEWS` and a single additive `NAV` entry (appended, **no reorder, no Power eviction**); split the broad `useStore()` into granular hooks; render the `runBanner`; rail badge on the existing `sequence` nav item only.
- **MODIFY** `ui/src/index.css` — add the global `@media (prefers-reduced-motion: reduce)` block, `:focus-visible` ring utility, the LIVE dim-outline keyframe, and a night thumbnail-dimmer variable hook (§9). Single small append; coordinate with no other CSS work in-flight.

**Collision notes:** `store.ts`, `types.ts`, `base.py` are single-owner (Owner-X).
`App.tsx`, `index.css` are single-owner (Owner-C). Every Python device file is a
separate owner-A task touching one file each. Lucide icons (`lucide-react`) are a
new dependency — Owner-X adds it to `package.json`.

---

## 2. Data flow

All event types already arrive on the single `/ws` socket and are dispatched in
`store.handleEvent`. The Monitor reads **only store slices** and issues no polling
of its own (one cold-load snapshot on mount, §8).

```
/ws ──▶ store.handleEvent ──▶ zustand slices ──▶ MonitorView selectors ──▶ cells
        (status/preview/guide/sequence)
```

Cadence: `status` every 2 s (`hub._status_loop`), `sequence` on every frame/state
change (`engine._set_state`), `guide` per guider tick, `preview` per saved/looped
frame.

**Derived/accumulated client state — minimal (resolves C1/B8/G2/C13):**

- **Guide trail:** **no new slice.** The sparkline reads `guide.recent` (or
  `status.guider.recent`) directly — the existing `GuideStats.recent` window the
  backend already maintains and that `GuideGraph`/`GuideScatter` already consume.
  The draft's separate `guideTrail` ring is **deleted** (it was self-contradictory
  and reset on reconnect).
- **Liveness timestamps (the only new derived state):** `lastFrameAtMs` and
  `lastGuideAtMs` — set in `handleEvent` on each `preview` and `guide` event from
  `Date.now()`. Used for stall detection ("last frame N ago", STALE chips). One
  number each, not a ring.
- **`autoMonitor`** boolean pref (localStorage, like `night`), default **false**
  for the auto-*select* behavior (banner is always shown regardless).
- **`runBanner`** persistent store field (NOT the single overwriting `toast`) —
  resolves B7. Holds `{ active: boolean; plan_name?: string; percent?: number }`.

### Selector discipline (perf — review P1, resolves D20)

Granular hooks in `store.ts`. Because `handleEvent` replaces the whole `status`
object by reference each 2 s poll, any selector extracting a `status` sub-object
**must** use shallow equality or it re-renders every tick:

```ts
import { useStore } from "./store";
import { useShallow } from "zustand/react/shallow";

export const useSeq        = () => useStore(s => s.sequence);
export const useGuideRecent = () => useStore(s => s.guide?.recent ?? s.status?.guider?.recent ?? []);
export const useGuideRms   = () => useStore(useShallow(s => s.guide ?? s.status?.guider ?? null));
export const usePreview    = () => useStore(s => s.preview);
export const useCamera     = () => useStore(useShallow(s => s.status?.camera ?? null));
export const useMeridian   = () => useStore(useShallow(s => s.status?.meridian ?? null));
export const useMount      = () => useStore(useShallow(s => s.status?.mount ?? null));
export const useLiveness   = () => useStore(useShallow(s => ({ frame: s.lastFrameAtMs, guide: s.lastGuideAtMs })));
export const useRunBanner  = () => useStore(useShallow(s => s.runBanner));
```

The 1s wall-clock ticker is **never** a store write and **never** a MonitorView-
level interval (resolves G1). It lives inside each `<LiveTimer>` / `<CountdownTile>`
leaf, so a per-second repaint touches only those digits, not the grid.

---

## 3. Shared contracts

### 3.1 TypeScript types (`ui/src/types.ts`)

```ts
// REPLACE the inline progress type on SequenceState with this named interface:
export interface SequenceProgress {
  frames_done: number;
  frames_total: number;
  percent: number;            // 0..100
  elapsed_s: number;          // server-computed, EXCLUDES paused time (§5)
  rejected: number;           // engine already tracks _rejected

  // ETA support — all optional, all server-computed (§5). Absent => client
  // renders "—"/low-confidence and falls back to elapsed only.
  eta_s?: number;             // total predicted seconds to finish (authoritative magnitude)
  eta_confident?: boolean;    // false until >= ETA_MIN_FRAMES real frames measured
  server_now_ms?: number;     // server epoch at emit; client uses to offset-correct
  current_exposure_s?: number;// exposure of the step in flight (for sub-frame bar)
  frame_started_at_ms?: number;// server epoch when the in-flight exposure began
  // event-cost breakdown (for transparency / debugging; not required by UI):
  remaining_capture_s?: number;
  events_cost_s?: number;     // sum of remaining dither/AF/flip costs
}

export interface CoolerInfo {     // under RigStatus.camera.cooler
  on: boolean;
  power: number | null;           // 0..100 %, null if camera can't report power
  target_c: number | null;
  at_target: boolean;             // |temp - target| <= COOLER_AT_TARGET_C (shared 1.0)
  can_report_power: boolean;      // false => ThermometerBar degrades to on/off + target
}

export type MeridianStatus =
  | "n_a_fork"        // mount reports no flip needed (fork/non-GEM): informational
  | "flip_disabled"   // plan.meridian_flip === false on a GEM: WARNING (pier risk)
  | "counting"        // hours_to_flip is a real positive number
  | "due"             // hours_to_flip <= 0
  | "unknown";        // can't determine (no mount / no data)

export interface MeridianInfo {     // top-level on RigStatus
  status: MeridianStatus;
  hours_to_flip: number | null;     // null unless status === "counting" | "due"
  flip_enabled: boolean;            // plan.meridian_flip AND mount is GEM
  pier_side: "east" | "west" | "unknown";
}

// SequenceState.state gains "nina_native" so the Monitor can show the honest
// "NINA is driving" state (resolves C14).
export interface SequenceState {
  state: "idle" | "running" | "paused" | "complete" | "aborted" | "error" | "nina_native";
  detail?: string;
  target?: string;
  target_index?: number;
  plan_name?: string;
  progress?: SequenceProgress;
}

// RigStatus extensions:
export interface RigStatus {
  // ...existing...
  camera?: {
    temperature: number | null;
    can_cool: boolean;
    has_dew_heater?: boolean;
    width: number; height: number; max_gain: number;
    cooler?: CoolerInfo;            // NEW
  };
  meridian?: MeridianInfo;          // NEW
}

// Snapshot aggregator response (§8):
export interface MonitorSnapshot {
  sequence: SequenceState;
  status: RigStatus;
  preview_id: number | null;
  guide_recent: { t: number; ra: number; dec: number }[];
}
```

### 3.2 Store slices (`ui/src/store.ts`)

```ts
export type ViewName =
  | "connect" | "capture" | "focus" | "mount" | "polar"
  | "guide" | "sequence" | "power" | "monitor";   // + monitor

interface AppState {
  // ...existing...
  lastFrameAtMs: number | null;        // NEW — set on each preview event
  lastGuideAtMs: number | null;        // NEW — set on each guide event
  autoMonitor: boolean;                // NEW — localStorage pref, default false
  runBanner: { active: boolean; plan_name?: string; percent?: number } | null; // NEW
  // actions:
  setAutoMonitor: (v: boolean) => void;
  dismissRunBanner: () => void;
}
```

`handleEvent` additions:
- `case "preview"`: also `set({ lastFrameAtMs: Date.now() })`.
- `case "guide"`: also `set({ lastGuideAtMs: Date.now() })`.
- `case "sequence"`: detect the rising edge `idle/complete/aborted/nina_native → running`.
  On rising edge set `runBanner = { active: true, plan_name, percent }`; update
  `percent` on subsequent progress; clear on `complete/aborted/error/idle` (after a
  short linger for complete). If `autoMonitor && view in {"connect"} ` and no user
  nav in the last `AUTOSELECT_IDLE_MS`, `setView("monitor")` — **never** otherwise
  (resolves A3/B6).

### 3.3 REST endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/monitor/snapshot` | One-shot cold-load hydration (§8). Optional/non-fatal. |
| `POST` | `/api/sequence/pause` | Existing. Used by Pause button. |
| `POST` | `/api/sequence/resume` | Existing. Used by Resume button. |
| `POST` | `/api/sequence/abort` | Existing. Used by Abort hold-button, with `AbortSignal.timeout(4000)` and a plain-`fetch` path that works on WS-down (A2). |

No other new endpoints; live data flows over the existing `/ws`.

### 3.4 Backend fields / methods

`Camera` (base.py):
```python
can_report_cooler_power: bool = False   # capability flag (default off)

async def get_cooler(self) -> dict | None:
    """Return {on, power, target_c, at_target} or None if no cooler.
    `power` is None when can_report_cooler_power is False."""
    return None
```
`COOLER_AT_TARGET_C = 1.0` — define **once** in `engine.py` (it already hardcodes
`<= 1.0` at line 279) and import it where `at_target` is computed, so the two
1.0s never drift (resolves crit2-A2).

`Telescope` (base.py) — `time_to_meridian_flip()` stays as-is (NINA overrides it).
The hub computes flip from HA for sim/Alpaca (§6.1), so no per-backend override is
required there.

`poll_status()` (hub.py) adds `camera.cooler` and a top-level `meridian` block (§6).

`engine._set_state` progress dict gains the §5 ETA fields and paused-aware
`elapsed_s`.

---

## 4. The view — responsive grid & cells

`MonitorView` is a CSS-grid of named cells. Reuse `Panel`, `Stat`, `Led`, `Toggle`
from `ui.tsx`, and `.progress-track`/`.progress-fill`, `.mono`, `.label`,
`img.astro`. Panels stretch (`h-full` + internal `flex flex-col justify-between`)
to kill the dead vertical space the review flagged.

### 4.1 Grid (mobile-first)

```
375px phone (1 col, vertical scroll, glance order = most-actionable first):
┌─────────────────────────────┐
│ HEADER STRIP (sticky top-0) │  StateBadge • LIVE • target • FINISH hh:mm · in Xh Ym
│                             │  Pause/Resume + Abort(hold) row
├─────────────────────────────┤
│ PROGRESS  (bar+%+step+ETA)  │  + "last frame N ago" stall line
├─────────────────────────────┤
│ COUNTDOWNS (flip | cooling) │  2-up inside one cell
├─────────────────────────────┤
│ THUMBNAIL (last frame)      │  16:10, LIVE/STALE, HFR/stars chips, night dimmer
├─────────────────────────────┤
│ GUIDE (sparkline + RMS✓word)│
├─────────────────────────────┤
│ THERMAL (temp + cooler)     │
└─────────────────────────────┘

>=640px (sm): 2 cols
>=1024px (lg): 12-col mosaic
  row1: [PROGRESS x8] [COUNTDOWNS x4]
  row2: [THUMBNAIL x6] [GUIDE x3] [THERMAL x3]   (thumbnail x6 per D18: bigger on desktop)
```

```tsx
<div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 auto-rows-min">
```
Header strip: `col-span-full sticky top-0 z-10`. Cells span via `lg:col-span-*`.

### 4.2 Header strip (`MonitorHeader`)

- **`StateBadge`** (from `stateMeta.ts`, resolves D3/E.1/E.2): renders a **Lucide
  icon + the word always** (never icon-only, never color-only). Map:
  - `running` → `<Activity>` + "RUNNING", tone good, `blinkable` (blink only if
    motion allowed)
  - `paused` → `<Pause>` + "PAUSED", tone warn
  - `complete` → `<Check>` + "COMPLETE", tone accent
  - `aborted` → `<Square>` + "ABORTED", tone bad
  - `error` → `<AlertTriangle>` + "ERROR", tone bad
  - `nina_native` → `<ExternalLink>` + "NINA DRIVING", tone accent
  - `idle` → `<Circle>` + "IDLE", tone dim
  **Do not** reuse `SequenceView.tsx:112` (pure-color, collapses in night mode).
- **`LIVE` chip:** shown iff `Date.now() - lastFrameAtMs < LIVE_WINDOW_S*1000`
  (resolves F4). Border is a **dim red outline** (`var(--accent-dim)`), not a glow;
  pulse only when motion allowed (resolves D2/E.11).
- Target name + `plan_name` + active filter (`status.filterwheel`).
- **Finish display (`LiveTimer`, resolves A4/C11/B4):** renders **both**
  `FINISH hh:mm` and `· in Xh Ym`, **both derived from one clock**: the client
  anchors `finishAtMs = Date.now() + (eta_s*1000) - (Date.now() - receiptMs)` …
  i.e. on each `sequence` event it stores `etaAnchor = { eta_s, receivedAt: Date.now() }`
  and renders `remaining = eta_s - (Date.now() - receivedAt)/1000`, `finishClock =
  fmtClock(Date.now() + remaining*1000)`. Server `server_now_ms` is used only to
  detect gross skew and is never rendered directly. If crossing local midnight,
  append `(+1d)`. If `eta_confident === false`, prefix `~` and show "estimating…"
  (resolves A4). If `state === "paused"`, show "PAUSED — no ETA" (a paused run has
  no honest finish). Finish clock is the **largest text in the header** (≥20px mono,
  resolves accessibility-3).
- **Controls row (resolves A1/B5/A2):**
  - `PauseButton`: plain button (pause is non-destructive). Shows "Pause" when
    running, "Resume" when paused. ≥44px. POSTs `/api/sequence/pause|resume`.
  - `HoldButton` "Abort": 800 ms press-and-hold (raised from 600 to guard accidental
    presses, accessibility-8) with a ring fill. Keyboard path: focus + Enter arms,
    second Enter within 3 s confirms. `aria-label="Hold to abort sequence"`,
    `aria-live` announces fire. ≥48px. POSTs `/api/sequence/abort` with
    `AbortSignal.timeout(4000)`; **always enabled**, even on WS-down — then it shows
    "link down — sending anyway" and relies on the HTTP response, not the socket.
  - Both controls hidden when `state` is not run-related (idle/complete shows none;
    nina_native shows none — see 4.x states).

### 4.3 Progress cell (`SeqProgressTile`)

- `.progress-track`/`.progress-fill` width = `percent`.
- **Sub-frame overlay bar** = `(Date.now() - frame_started_at_ms) / (current_exposure_s*1000)`,
  clamped 0..1, **client-interpolated** (resolves D19 — engine only emits on frame
  boundaries, not the 2 s status tick; so this MUST be client-derived). **Freezes
  and greys** (loses its fill animation, turns `--text-dim`) when a stall is
  detected: `Date.now() - lastFrameAtMs > (current_exposure_s + STALL_MARGIN_S)*1000`
  (resolves A5). A static numeric `frame 00:42 / 120s` is shown alongside so the
  liveness signal is **not motion-only** (accessibility-4, reduced-motion).
- Big `mono` (≥18px): `{frames_done}/{frames_total}` + `{percent}%`.
- `detail` line verbatim from `sequence.detail`.
- **Stall line (resolves A5/B9):** `last frame {fmtDuration(age)} ago`. Tone warn if
  `age > current_exposure_s*2`, bad if `> current_exposure_s*3 + STALL_MARGIN_S`,
  with a "CAPTURE STALLED?" label and `navigator.vibrate` once on entering bad.
- **Flagged (resolves F1):** inline micro-label, **no tooltip**: `{rejected} flagged
  (HFR/cloud check)` in muted-but-AA text; only shown when `rejected > 0`. Never
  implies data loss (C12: frames are kept, only counted).
- **HFR-trend mini-sparkline (B9):** one-line `Sparkline` of recent `preview.hfr`
  values (kept in a tiny client ring of the last ~20 hfr readings, derived from
  `preview` events). Dew early-warning. Optional; renders only if ≥3 hfr samples.

### 4.4 Countdowns cell (`CountdownsTile` → two `CountdownTile`s)

- **Meridian flip** (driven by `status.meridian`, resolves F5/crit2-A1):
  - `status === "counting"`: `CountdownTile` `H:MM`/`MM:SS`. Urgency by **ring fill
    proportion + glyph + text**, NOT color ramp (night collapses colors —
    resolves D2/E.9): `<5 min` adds "FLIP SOON" text + (blink if motion allowed);
    ring goes fuller as time shrinks.
  - `status === "due"`: "FLIP DUE" + `<AlertTriangle>`, tone bad.
  - `status === "flip_disabled"`: **WARNING** tile — `<AlertTriangle>` + "FLIP
    DISABLED — risk near meridian", tone warn (NOT muted). This is the pier-safety
    distinction (resolves F5).
  - `status === "n_a_fork"`: muted "no flip needed (fork mount)" — informational.
  - `status === "unknown"`: muted "flip n/a (mount doesn't report)".
- **Cooling** (resolves A3/B2 — **no numeric ETA**): driven by `camera.cooler` +
  `detail.startsWith("cooling")`:
  - cooling in progress → `−3.2 °C → −10 °C · cooling…` (text + arrow, no number).
  - `at_target` → `<Check>` + "AT −10 °C", tone good (carried by glyph+text, since
    green is red in night).
  - hidden entirely when not cooling and not before-lights.
- One `<svg>` ring per tile (no per-tick rect rebuild).

### 4.5 Thumbnail cell (`PreviewTile`)

- `<img className="astro" src={`/api/preview/${preview.id}.png`}>` — identical URL
  to CaptureView so the 8-frame `hub.previews` cache serves both.
- **Double-buffer** (kills per-frame flash): two stacked `<img>`; swap `displayedId`
  on the new image's `onLoad`. **`onError` handler (resolves F3):** if the new
  `<img>` 404s (evicted from the 8-frame cache) or fails to decode, do NOT swap;
  keep the previous frame but **flip the chip to STALE** and stop showing LIVE.
- **LIVE/STALE chip tied to real frame age** (resolves F3/F4): LIVE iff
  `now - lastFrameAtMs < LIVE_WINDOW_S*1000` AND last load succeeded; STALE
  otherwise.
- **Night thumbnail dimmer (resolves D1/E.11):** a per-tile brightness slider
  (0.2..1.0), persisted to localStorage (`astrodeck-monitor-thumb-brightness`),
  applied as an extra `filter: brightness(x)` on top of `img.astro`. Defaults to
  0.5 in night mode, 1.0 in day. Ships **day-one** (does not wait on a global
  dimmer). LIVE border is a dim outline (no luminance pulse).
- Chips overlaid bottom-left: `HFR x.xx` (good/warn/bad per CaptureView thresholds
  <3/<5), `N★`, `exp/gain/bin`. `CLIP` chip only when `preview.stats.max>=65535`
  **and** mode !== `nina` (D15: NINA stats are from a decoded 8-bit copy, unreliable
  for clip).
- **Real `<button>` not `<img onClick>` (resolves accessibility-7):** the tile is a
  focusable `<button role>` with `aria-label="Open live preview in Capture"`,
  Enter/Space activatable, `:focus-visible` ring; tap deep-links
  `setView("capture")`. Full-image tap target.

### 4.6 Guide cell (`GuideTile`)

- **`Sparkline`** (new, `monitor.tsx`): one `<path>` for RA, one for DEC, from
  `useGuideRecent()`. `React.memo` + `useMemo`. **Fixed ±4″ scale** with clip
  indicators (resolves C2 — no per-tick rescale that flattens spikes). RA/DEC
  differentiated by **dash vs solid + stroke width + inline ≥10px text labels**,
  NOT color (night collapses accent≈warn — resolves D10/accessibility-10).
- **RMS (resolves C3/D16):** `Stat` plus a **glyph + qualitative word**:
  `RMS 1.3″ ✓ good` (<1), `⚠ soft` (1–2), `✗ poor` (>2). Plus RA/DEC/SNR. The word
  is the accessible channel; color is decorative.
- Empty: muted "not guiding". STALE chip if `now - lastGuideAtMs > 15000`, with last
  RMS frozen.

### 4.7 Thermal cell (`ThermalTile`)

- `Stat` sensor temp `°C` (good if `<0` per CaptureView convention; warn if
  `cooler.on && !cooler.at_target`).
- **`ThermometerBar` (resolves crit2-A2):** if `cooler.can_report_power`, render the
  0–100 % power bar (`--accent` fill) + target + `<Check>` at-target. If NOT, degrade
  to an **on/off `Led` + the word "ON"/"OFF" + target + "(no power readout)"** — the
  word is required because the Led is color-only and red in night
  (resolves accessibility-13). Bar fill in night must clear the §9 min-contrast rule.
- If `camera.can_cool === false`, whole tile shows muted "no cooler".
- Camera dew-heater state (`has_dew_heater`) gets a one-line read with a link to the
  Power/Capture control (B9: surface it; we don't duplicate the control here).

---

## 5. Backend: paused-aware elapsed + deterministic ETA (`engine.py`)

The load-bearing math. Server computes magnitude; client derives wall-clock.

### 5.1 Paused-aware elapsed (resolves C10)

The engine currently does `elapsed = time.time() - self._started_at`, which keeps
ticking through pause. Track paused duration:
```python
# in __init__/start:
self._paused_accum_s = 0.0
self._pause_started_at: float | None = None
# in pause(): self._pause_started_at = time.time()
# in resume():
#   if self._pause_started_at: self._paused_accum_s += time.time() - self._pause_started_at
#   self._pause_started_at = None
def _elapsed_s(self) -> float:
    paused = self._paused_accum_s
    if self._pause_started_at is not None:
        paused += time.time() - self._pause_started_at
    return max(0.0, time.time() - self._started_at - paused)
```
Emit `elapsed_s = round(self._elapsed_s())`.

### 5.2 Deterministic event costs (resolves B1/A4/crit2-A4)

**Do not** smear dither/AF/flip into one EMA (causes the sawtooth). Split:

- **Per-frame download/settle overhead** — small, frequent. EMA with **α=0.1**
  (lowered from 0.3 so one cloud-slowed frame doesn't whipsaw), seeded from a sane
  constant `DEFAULT_OVERHEAD_S = 12` until `ETA_MIN_FRAMES (=3)` real frames, then
  data-driven. The EMA sample explicitly **excludes** dither/AF/flip frames (those
  are accounted separately), measured as
  `cadence - exposure - (event_cost_for_that_frame)`.
- **Event costs — counted analytically** from the plan (the engine knows
  `dither_every`, `autofocus_every`, frames remaining, and pier state):
  ```
  dithers_remaining  = floor(frames_remaining / dither_every)      if dither_every else 0
  refocus_remaining  = floor(frames_remaining / autofocus_every)   if autofocus_every else 0
  flip_pending       = 1 if (flip_enabled and 0 < hours_to_flip <= remaining_window) else 0
  events_cost_s = dithers_remaining*DITHER_COST + refocus_remaining*AF_COST + flip_pending*FLIP_COST
  ```
  `DITHER_COST`, `AF_COST`, `FLIP_COST` start as constants but are **replaced by a
  measured rolling average per event type** as the run observes them (resolves
  B1/B3 — measure the flip, don't bury a guessed 90 s). Until measured, mark ETA
  low-confidence.

### 5.3 ETA assembly

```python
remaining_capture_s = sum((count - done)*exposure for remaining steps)
in_flight = max(0, current_exposure_s - (time.time() - frame_started_at))  # not double-counted: remaining_capture excludes the in-flight frame
eta_s = remaining_capture_s + in_flight \
      + frames_remaining * overhead_ema + events_cost_s
eta_confident = (real_frames_measured >= ETA_MIN_FRAMES) and events_measured_ok
```
Off-by-one guard (resolves crit2-A4): `remaining_capture_s` counts frames with
index `> i` for the current step (the in-flight frame is `in_flight`, counted once).
Add a unit test asserting `remaining_capture_s` never includes the in-flight frame.

### 5.4 Progress dict emission

```python
kw["progress"] = {
  "frames_done": self._frames_done,
  "frames_total": total,
  "percent": round(100*self._frames_done/total, 1) if total else 0,
  "elapsed_s": round(self._elapsed_s()),
  "rejected": self._rejected,
  "eta_s": round(eta_s),
  "eta_confident": bool(eta_confident),
  "server_now_ms": round(time.time()*1000),
  "current_exposure_s": self._cur_exposure_s,        # set in _run_step before capture
  "frame_started_at_ms": round(self._frame_started_at*1000),
  "remaining_capture_s": round(remaining_capture_s),
  "events_cost_s": round(events_cost_s),
}
```
`_cur_exposure_s` and `_frame_started_at` are set in `_run_step`/`_run_calibration`
immediately before `hub.capture`. The EMA updates in `_record_frame` from
`now - self._last_frame_done` minus exposure minus that frame's event cost.

### 5.5 NINA-native sequence (resolves C14)

When `hub.mode == "nina"` and AstroDeck's own engine is **not** running but NINA's
own sequence is (detected via `_handle_nina_event` seeing `IMAGE-SAVE`/sequence
events), publish `sequence` with `state: "nina_native"` and a `detail` from the
last NINA event. The Monitor then shows the honest "NINA is driving this sequence —
open NINA for full detail" state (no fake progress bar), while the thumbnail/guide/
thermal cells still work from the `IMAGE-SAVE`-derived preview and `status`.
(If wiring NINA `IMAGE-SAVE` to a `preview` event is out of scope for v1, the cell
shows "NINA driving — no live preview here"; never silent blankness.)

---

## 6. Backend: meridian (server-side HA) + cooler (`hub.py`)

### 6.1 Server-computed meridian (resolves crit2-A1)

`time_to_meridian_flip` is **not** an ASCOM primitive; only NINA computes it. So the
hub computes it for sim/Alpaca from hour angle, and prefers the device value when
present (NINA). In `poll_status`, when a mount is connected:

```python
from .catalog.coords import lst_hours
meridian = {"status": "unknown", "hours_to_flip": None,
            "flip_enabled": _plan_flip_enabled(), "pier_side": "unknown"}
try:
    ttf = await tel.time_to_meridian_flip()      # NINA returns a number; others None
    side = (await tel.pier_side()).value          # "east"/"west"/"unknown"
    meridian["pier_side"] = side
    if ttf is None:
        # compute from HA: HA = LST - RA (hours, wrap to [-12,12])
        lst = lst_hours(self.site["longitude"])
        ha = ((lst - ra + 12) % 24) - 12
        # GEM on the east side tracking west flips when target crosses meridian
        # (HA crosses 0 going positive). hours_to_flip ~= -HA + flip_pause.
        flip_pause_h = FLIP_PAUSE_MIN / 60.0      # configurable, default 0
        ttf = -ha + flip_pause_h
    if not _is_gem(side):                          # fork/unknown => no flip
        meridian["status"] = "n_a_fork" if side != "unknown" else "unknown"
    elif not meridian["flip_enabled"]:
        meridian["status"] = "flip_disabled"       # GEM but plan disabled it: WARN
    elif ttf <= 0:
        meridian["status"] = "due";       meridian["hours_to_flip"] = ttf
    else:
        meridian["status"] = "counting";  meridian["hours_to_flip"] = ttf
except Exception:
    pass
out["meridian"] = meridian
```
`_plan_flip_enabled()` reads the active sequence plan's `meridian_flip` (via the
engine) or `False` if no plan. `_is_gem` treats `east`/`west` pier reports as GEM,
`unknown` as not-determinable. This also fixes the latent engine bug where
`_maybe_meridian_flip` polled a `None` that never fired on sim/Alpaca — once the
hub exposes a real number, the engine can flip those mounts too (separate follow-up;
note it but do not block on it).

### 6.2 Cooler (resolves crit2-A2)

`poll_status` camera block adds:
```python
cooler = await cam.get_cooler()       # None if no cooler
if cooler is not None:
    cooler["at_target"] = (cooler["target_c"] is not None
        and (await cam.get_temperature()) is not None
        and abs((await cam.get_temperature()) - cooler["target_c"]) <= COOLER_AT_TARGET_C)
    out["camera"]["cooler"] = cooler
```
- **sim** (`SimCamera.get_cooler`): trivial power model so the tile is exercised:
  `power = clamp((ambient - temp)/(ambient - target)*100, 0, 100)` with
  `ambient=12.3`; `can_report_power=True`.
- **Alpaca** (`AlpacaCamera.get_cooler`): try `coolerpower` (optional — many cameras
  throw); on `DeviceError` set `power=None, can_report_power=False`. Read
  `cooleron`. Detect `can_report_power` once at connect by probing `coolerpower`.
- **NINA** (`NinaCamera.get_cooler`): read optional `CoolerPower`/`CoolerOn` from
  camera info; treat as absent across versions → `power=None`.

---

## 7. All UI states (whole-view)

| Condition | Behavior |
|---|---|
| **WS down** (`!wsConnected`) | Header shows `NO LINK` + amber banner "reconnecting — values may be stale". **Data values** dim to 60%; **warning chrome (STALE chips, banner) stays full opacity/high contrast** (resolves P3-18). **Abort + Pause stay enabled** and POST over HTTP (resolves A2). |
| **Cold load / hydrating** | Non-animated, AA-contrast skeleton (resolves P3-17): static `--line-bright` placeholders, **no shimmer**. One-shot `/api/monitor/snapshot` (§8) fills until WS catches up. |
| **No sequence** (`idle`, never ran) | Progress + Countdowns collapse to ghost-glyph empty state + a `Plan a session →` button (≥44px, focus ring) → `setView("sequence")`. Thumbnail/Guide/Thermal still live if devices connected. **Not** the landing view (F2). |
| **Running** | Full live view; LIVE shown per real frame age. Pause + Abort visible. |
| **Paused** | `StateBadge` "PAUSED"; sub-frame bar frozen; finish shows "PAUSED — no ETA"; Resume + Abort visible. |
| **Complete** | "COMPLETE"; bar 100%; finish replaced by `done · {frames_done} frames · {rejected} flagged (kept)`; thumbnail frozen (no LIVE). `runBanner` lingers then clears. No controls. |
| **Aborted / Error** | `StateBadge` bad; **error renders inline** (resolves E3): `detail` failure text + the last ~5 `level==="error"|"warning"` lines from the store `logs` (no drawer punt — drawer is desktop-only). `navigator.vibrate` once on entering error. A `Plan →` link, not "View log". Does not auto-clear. |
| **NINA driving** (`nina_native`) | Honest "NINA is driving this sequence — open NINA for detail" panel (resolves C14); no fake progress; other live cells work where data exists. No Pause/Abort (we don't control NINA's run). |
| **Per-cell missing device** | Self-guard: no guider → "not guiding"; no cooler → "no cooler"; no preview → "no frame yet"; fork mount → "no flip needed". No cell throws. |

---

## 8. Cold-load hydration — `GET /api/monitor/snapshot`

```
GET /api/monitor/snapshot →
  { sequence: engine.state-or-{state:"idle"},
    status:   await hub.poll_status(),      # includes camera.cooler + meridian
    preview_id: hub.preview_seq or null,
    guide_recent: hub.guider.stats().recent if hub.guider else [] }
```
Called once in `MonitorView` `useEffect` with `AbortSignal.timeout(4000)`;
non-fatal on failure (WS will catch up in ≤2 s). Pre-fills `status`, `sequence`,
`preview`, and seeds `lastFrameAtMs`/`lastGuideAtMs` so the view isn't blank for the
first poll.

---

## 9. Night-mode + 375px-phone notes

- **Layout:** single column, `gap-3`, `px-3 pb-20` (clears the fixed bottom nav).
  Header `sticky top-0` so finish clock + Pause + Abort stay reachable while
  scrolling. Glance order: Progress + Countdowns above the fold.
- **Tap targets (resolves accessibility-6):** **every** interactive element ≥44px
  (≥48px for Abort): Pause/Resume, Abort hold, thumbnail button, `Plan →`/empty-
  state CTAs, the `runBanner` "open Live" action, and any rail badge. No bare ~16px
  `→` links.
- **Focus + keyboard (resolves accessibility-7):** add a `:focus-visible` ring
  utility in `index.css` (`outline: 2px solid var(--accent); outline-offset: 2px`)
  and apply to HoldButton, PauseButton, the thumbnail `<button>`, and all CTAs
  (the codebase sets `outline:none` and has no rings today). HoldButton has a
  keyboard arm/confirm path and `aria-live` announcement (accessibility-8).
- **Red mode (`:root.night`):**
  1. Thumbnail uses `img.astro` (auto red-safe) **plus** the per-tile night
     brightness dimmer (§4.5), default 0.5 — the single most night-hostile element
     gets its own dimmer day-one (resolves D1).
  2. **State is never color-only** (resolves accessibility-1/2): `StateMeta` renders
     Lucide icon + word; countdown urgency uses ring-fill + text; RMS uses
     glyph+word; cooler uses "ON/OFF" text beside the Led. Color is decorative.
  3. **Promote state-bearing dim text off `--text-dim`** (fails AA — resolves
     accessibility-5/D5): STALE, "FLIP DISABLED", "not guiding", "FLIP DUE", the ETA
     value, "last frame N ago" use `--color-ink` or a tone color; `--text-dim` is
     reserved for static field labels only.
  4. **Min-contrast progress fill** (resolves accessibility-14): the
     `.progress-fill` gets a 1px bright top edge so the % boundary is crisp against
     the low-contrast night gradient.
- **Motion (resolves D2/E.12/accessibility-3/4):** one global
  `@media (prefers-reduced-motion: reduce)` block in `index.css` disables LIVE
  pulse, `.blink`, crossfade, sub-frame animation, view cross-fade, and skeleton
  shimmer; numeric liveness text (`frame 00:42/120s`, "last frame N ago") remains as
  the non-motion liveness channel. Default (motion allowed) keeps at most: the data-
  driven progress width transition, one dim LIVE-outline pulse, and the `<5 min`
  flip blink — nothing bright pulses.
- **SVG text & rings:** ≥10px mono labels (`Sparkline`, `CountdownTile`, chips);
  `stroke="var(--accent)"`/`var(--warn)` (auto-red); `preserveAspectRatio="none"`
  width-fluid.
- **Performance on a phone:** ≤1 `<img>` swap/frame (double-buffered, `onError`-
  guarded); no MonitorView-level interval (tickers in leaves only); memoized SVGs;
  shallow-equality selectors so only the touched cell repaints; fixed grid rows (no
  layout thrash).

### Shared constants (one source of truth; export from `lib/eta.ts` / a consts file)

```
LIVE_WINDOW_S        = 8       # "new frame arriving" window for LIVE chip
STALL_MARGIN_S       = 20      # grace before declaring a capture stall
GUIDE_STALE_S        = 15
COOLER_AT_TARGET_C   = 1.0     # MUST equal engine._cool_and_wait threshold
ETA_MIN_FRAMES       = 3       # ETA stays low-confidence (~) below this
OVERHEAD_EMA_ALPHA   = 0.1
DEFAULT_OVERHEAD_S   = 12
AUTOSELECT_IDLE_MS   = 8000    # only auto-select Monitor from connect/idle after this
SPARKLINE_SCALE_ARCSEC = 4     # fixed guide y-scale (no auto-rescale)
THUMB_BRIGHTNESS_NIGHT_DEFAULT = 0.5
```

---

## 10. `lib/eta.ts` signatures (pure, unit-tested)

```ts
export function fmtDuration(s: number): string;     // 7340 -> "2h 2m"; 95 -> "1m 35s"; 9 -> "9s"
export function fmtClock(ms: number): string;       // epoch -> "03:41" (local 24h), "(+1d)" if next day
export function fmtCountdown(s: number): string;     // switches MM:SS <-> H:MM with a tested boundary (resolves P3-16)
// Client-anchored finish from one clock (resolves C11/B4):
export function deriveFinish(etaS: number, receivedAtMs: number, nowMs: number):
  { remainingS: number; finishAtMs: number };
```
`eta.test.ts` covers: `fmtDuration` ranges, `fmtClock` midnight rollover,
`fmtCountdown` MM:SS↔H:MM boundary, `deriveFinish` monotonic countdown, and (mirror
of the engine guard) that remaining-capture never double-counts the in-flight frame.

---

## 11. Component signatures (`components/monitor.tsx`)

```tsx
export const Sparkline: React.MemoExoticComponent<(p: {
  samples: { t: number; ra: number; dec: number }[] | number[];
  scaleArcsec?: number;          // default SPARKLINE_SCALE_ARCSEC, FIXED (no autoscale)
  mode?: "guide" | "trend";      // guide = RA/DEC dual path; trend = single hfr line
}) => JSX.Element>;

export function CountdownTile(p: {
  label: string; seconds: number | null;
  warnAtS: number; dueLabel?: string;
  variant: "flip" | "cooling";
  reducedMotion?: boolean;
}): JSX.Element;                  // single countdown primitive (Donut removed)

export function ThermometerBar(p: {
  power: number | null; on: boolean; target: number | null;
  atTarget: boolean; canReportPower: boolean;   // degrades to ON/OFF text when false
}): JSX.Element;

export function HoldButton(p: {
  label: string; holdMs?: number;               // default 800
  danger?: boolean; onConfirm: () => void;
  ariaLabel: string;                            // required for SR
}): JSX.Element;                  // keyboard arm/confirm + aria-live

export function PauseButton(p: {
  paused: boolean; onPause: () => void; onResume: () => void; disabled?: boolean;
}): JSX.Element;

export function PreviewTile(p: {
  previewId: number | null; live: boolean; stale: boolean;
  hfr?: number; stars?: number; meta?: string; clip?: boolean;
  brightness: number; onBrightness: (v: number) => void;   // night dimmer
  onOpen: () => void;                                       // -> Capture (real <button>)
}): JSX.Element;

export function StateBadge(p: { state: SequenceState["state"]; reducedMotion?: boolean }): JSX.Element;
export function LiveTimer(p: { etaS?: number; receivedAtMs: number; confident?: boolean; paused?: boolean }): JSX.Element;

// MonitorView.tsx
export default function MonitorView(): JSX.Element;
```

---

## 12. Implementation checklist (build order)

**Phase 0 — contracts (Owner-X, blocks all):**
- [ ] `types.ts`: add `SequenceProgress`, `CoolerInfo`, `MeridianInfo`/`MeridianStatus`, `MonitorSnapshot`; extend `SequenceState` (`nina_native`, `target_index`) + `RigStatus.camera.cooler` + `RigStatus.meridian`.
- [ ] `store.ts`: `ViewName += "monitor"`; `lastFrameAtMs`/`lastGuideAtMs`/`autoMonitor`/`runBanner` + actions; `handleEvent` timestamps + rising-edge banner + guarded auto-select; granular selectors with `useShallow`.
- [ ] `base.py`: `Camera.get_cooler` default, `can_report_cooler_power`; export `COOLER_AT_TARGET_C` from engine and import where needed.
- [ ] Add `lucide-react` to `package.json`.

**Phase 1 — backend telemetry (Owner-A, parallel after Phase 0):**
- [ ] `engine.py`: paused-aware `_elapsed_s`; deterministic ETA (split overhead EMA α=0.1 + analytic event costs + measured event costs); emit progress fields + `server_now_ms` + `current_exposure_s` + `frame_started_at_ms`; off-by-one guard + its assertion.
- [ ] `hub.poll_status`: server-side `meridian` from HA (prefer device value); `camera.cooler` with shared `at_target`.
- [ ] `sim.py`/`alpaca.py`/`nina.py`: `get_cooler()` per backend (sim power model; Alpaca optional `coolerpower` w/ capability probe; NINA optional).
- [ ] `nina_native` sequence state on NINA-driven runs.
- [ ] `GET /api/monitor/snapshot` aggregator.

**Phase 2 — cells + math (Owner-B, parallel after Phase 0):**
- [ ] `lib/eta.ts` (`fmtDuration`/`fmtClock`/`fmtCountdown`/`deriveFinish`) + `lib/eta.test.ts` (all cases incl. midnight + in-flight non-double-count).
- [ ] `lib/stateMeta.ts` (Lucide icon + word + tone map; NOT SequenceView reuse).
- [ ] `components/monitor.tsx`: `Sparkline` (fixed ±4″, dash/width-differentiated, memo), `CountdownTile` (ring-fill urgency), `ThermometerBar` (power/no-power degrade), `HoldButton` (800 ms + keyboard + aria-live), `PauseButton`, `PreviewTile` (double-buffer + onError + night dimmer + real `<button>`), `StateBadge`, `LiveTimer` (leaf 1s ticker, one-clock finish).

**Phase 3 — view + wiring (Owner-C, after Phase 2 cells exist):**
- [ ] `MonitorView.tsx`: grid + all cells + all §7 UI states + cold-load snapshot + `navigator.vibrate` hooks.
- [ ] `App.tsx`: add `monitor` to `VIEWS` + one **appended** `NAV` entry (no reorder, no Power eviction); split broad `useStore()` to granular hooks; render persistent `runBanner` (≥44px action); rail badge on the existing `sequence` item.
- [ ] `index.css`: append global `prefers-reduced-motion` block, `:focus-visible` ring utility, dim LIVE-outline keyframe, progress-fill bright top-edge, night thumb-dimmer hook.

**Phase 4 — verify:**
- [ ] `POST /api/connect/sim`, build a short 2-target plan, Run; confirm on a **375px** viewport in **night mode**: ETA shows `~`/estimating for first <3 frames then firms; finish clock = largest header text; meridian tile populates (server HA, not "flip off"); cooler power bar exercised (sim model); Pause/Resume/Abort reachable ≥44px with focus rings; Abort works with WS killed; sub-frame bar freezes when capture is artificially stalled; thumbnail dimmer persists; no decorative motion under `prefers-reduced-motion`.
- [ ] Bridge to NINA, start a NINA-native run; confirm the honest "NINA driving" state (not blank).
- [ ] vitest green for `eta.test.ts`.
