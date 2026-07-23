# PRO-8 — End-of-night report viewer + live trend dashboard

Combined design spec + TDD implementation plan. One file.

Status: **ready** — cleanly scoped, no product blockers. Backend + types + store
event + all three report routes already exist; this is a UI-only build.

---

## 1. Design

### 1.1 Goal

The end-of-night report **backend is fully built and thrown away with no viewer**:

* `server/astrodeck/sequence/report.py` — `SessionReporter`, `FrameRecord`,
  `FilterBreakdown`, `TargetBreakdown`, `SessionReport`
  (`by_filter`/`targets`/`safety_events`/`frames`), and read-time
  `SessionReporter.trends()` deriving `{hfr, temp, rms}` from the frame records
  (report.py:60-101, 431-454).
* Three routes already serve it (app.py:1969-2007):
  * `GET /api/reports` → newest-first summaries (report.py:374-394).
  * `GET /api/reports/{id}` → the full report **plus** read-time trends
    (`report.model_dump() | {"trends": trends}`, app.py:1981-1985).
  * `GET /api/reports/{id}/frames.csv` → the append-only frame list as CSV
    (app.py:1987-2007).
* The engine already publishes `bus.publish("report", id=rid)` on **every**
  terminal path (engine.py:713; reasons: `complete`, `dawn_cutoff`, `unsafe`,
  `aborted`, `error`, `quality`, `cooling_skip` — engine.py:621-690), and the
  store already stashes it as `lastReportId` (store.ts:1154-1160, 1412).

Build the **ReportView** that renders this: the per-filter/target integration
breakdown, the safety-event timeline, the HFR/RMS/temp trend lines with the
`frames.csv` download link — **plus** a live in-acquisition strip charting
HFR / star-count / RMS / temp over the last N subs in the Monitor.

### 1.2 Current state — what already exists (do NOT rebuild)

Grounded in files read for this design:

| Concern | Reality | file:line |
| --- | --- | --- |
| Report data models | `FrameRecord`, `FilterBreakdown`, `TargetBreakdown`, `SessionReport` | report.py:60-101 |
| Read-time trends | `SessionReporter.trends()` → `{hfr:[[ts,v]…], temp, rms}` (accepted frames only, per-series downsample to 200) | report.py:431-472 |
| List route | `GET /api/reports` (CAP_VIEW_STATUS) → bare list of summaries | app.py:1969-1973 |
| Detail route | `GET /api/reports/{id}` → `report.model_dump() \| {"trends": trends}` | app.py:1975-1985 |
| CSV route | `GET /api/reports/{id}/frames.csv` (attachment) | app.py:1987-2007 |
| Engine emits event | `bus.publish("report", id=rid)` on finalize | engine.py:713 |
| Store consumes event | `case "report"` → `set({ lastReportId: d.id })`; `useLastReportId` hook | store.ts:1154-1160, 1412 |
| TS types (ALL present) | `FilterBreakdown` / `TargetBreakdown` / `SessionReportSummary` / `SessionReport{trends}` | types.ts:805-843 |
| ViewName union | already includes `"report"` | types.ts:11-23 |
| VIEWS registry | `report: () => <PlaceholderView label="Session Report" />` (placeholder to swap) | App.tsx:80-86 |
| Run-complete panel | SequenceView finished panel — action row, no report link yet | SequenceView.tsx:396-522 |
| Mobile overflow | `NavMoreSheet` `OVERFLOW_VIEWS` list — no Reports entry yet | NavMoreSheet.tsx:36-44 |
| Live HFR ring (partial!) | MonitorView already keeps a **single** `hfrRing` (last 20 HFR) fed from `preview.id` and draws it via `Sparkline mode="trend"` | MonitorView.tsx:167-172, 490-495 |
| Charting primitives | `graphs.tsx` (Histogram/VCurve/GuideGraph/GuideScatter) + `Sparkline` in monitor.tsx (`mode:"guide"\|"trend"`, night-safe, memo'd) | graphs.tsx:1-247, monitor.tsx:278-357 |
| Toast API | `useStore.getState().enqueueToast({level,title})` (MonitorView.tsx:255-273) | store.ts:563,867 |
| Client API base | `api.get<T>(path)` cookie-auth + `ApiError`; `BASE`/`u()` from `lib/base` | api.ts:90-96 |

**SessionReviewDrawer check (brief asked first):** `SessionReviewDrawer.tsx` is a
**frame-grid regrade tool** — thumbnails, per-frame HFR/★/RMS text, accept/reject
overrides (SessionReviewDrawer.tsx:220-247). It shows **per-frame** data for
**one multi-night session**; it does **not** render the per-filter/target
**integration** breakdown, the **safety-event timeline**, or any **trend lines**,
and it reads `/api/sessions/*`, never `/api/reports/*`. **No overlap** — the
report viewer is genuinely missing. Keep the drawer untouched.

### 1.3 Approach

Three thin layers over primitives that already exist; the only real logic is
tiny coordinate math + a bounded ring, both behind pure tested functions.

1. **`lib/reportChart.ts` (pure, DOM-free, tsx-tested)** — the report→chart
   shaping and the live ring:
   * `trendGeom(values, w, h, padY?)` → `{ d, yMin, yMax } | null` — a polyline
     path with data-driven y-scale that **handles negatives** (sensor temp) and
     tight bands (HFR), unlike `Sparkline mode="trend"` whose y is floored at the
     arcsec guide scale (monitor.tsx:296) — wrong for temp/HFR.
   * `pushLiveSample(prev, s, cap)` / `pickSeries(ring, key)` — the bounded
     per-sub ring for the live strip (generalizes MonitorView's single-HFR ring).
   * `endReasonMeta(reason)` → `{ word, tone }` — maps the engine's seven real
     end reasons (not just the four in the type comment) to a labelled tone.
2. **`components/graphs.tsx` → new `TrendLine`** — a night-safe time-series line
   consuming `trendGeom`. One primitive reused by both the report trends and the
   live strip.
3. **`views/ReportView.tsx`** — thin render: a report picker (`GET /api/reports`,
   default = `lastReportId` ?? newest), header, per-filter table, per-target
   nested breakdown, safety-event timeline, three `TrendLine`s, CSV link.
4. **`views/MonitorView.tsx` + `components/monitor.tsx` → `LiveTrendStrip`** —
   generalize the existing HFR ring into a 4-series `LiveSample[]` ring and add a
   "Live trend" panel of four `TrendLine`s (HFR / stars / RMS / temp).

**Copy tables (report header block).** Integration formatting reuses
`fmtDuration` from `lib/eta.ts` (eta.ts:27, e.g. `3h 12m`). End reason via
`endReasonMeta`. Per-filter row: `<filter> · <frames> frames · <integration> ·
HFR <median>` with `<rejected> rejected` when non-zero. This mirrors
SequenceView's finished-panel language ("N frames · M rejected · Xm elapsed",
SequenceView.tsx:441-444) and the Sessions bar rollup.

**Behavior.** ReportView is a **read** (CAP_VIEW_STATUS) — viewable by every role,
no gated controls, no writes. A failed detail/list fetch surfaces one
`enqueueToast({level:"error", …})` and an inline empty state (never a blank
screen). CSV is a plain cookie-carrying `<a download>` to
`${BASE}/api/reports/${id}/frames.csv`.

### 1.4 Placement

* **ReportView** is **not** a primary-nav entry (App.tsx:80-84 codifies this
  prior decision). Reached from:
  * the SequenceView run-complete panel — a "View session report →" link shown
    when the run `finished` and `lastReportId` is set (deep-link, desktop+mobile);
  * the `NavMoreSheet` overflow — a persistent "Reports" row so past reports are
    reachable on a phone even with the engine idle (ReportView's own picker then
    browses all reports from `GET /api/reports`).
* **LiveTrendStrip** lives in the MonitorView mosaic as a `Panel`
  (`lg:col-span-*`), only while a run is active and the ring has ≥3 samples —
  same gating idiom as the existing inline HFR sparkline (MonitorView.tsx:490).

---

## 2. Global Constraints (verbatim, binding)

* **Privacy.** The real coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  `"[SITE-LABEL]"` must **NEVER** appear in code, tests, or docs. The site default
  is `"My Observatory"` / `0.0`. (Reports carry no coordinates — frames, filters,
  targets, timestamps only — so this feature never handles site location; the
  constraint still binds any fixture/example values.)
* **Never `git add -A`.** Stage explicit paths only.
* **UI typecheck gate:** `cd ui && npx tsc -b`.
* **NO jsdom / DOM harness.** Pure logic is tested with `npx tsx` inline-assert,
  following `ui/src/components/ui/__tests__/SegmentedControl.test.tsx`,
  `ui/src/components/__tests__/healthStrip.test.ts`,
  `ui/src/lib/__tests__/eta.test.ts`. A file that imports React-component modules
  which touch `window`/`matchMedia` at load can't run under `tsx` — import pure
  logic from `lib/*` directly (healthStrip.test.ts:10-15 rationale).
* **Backend tests:** `server/.venv/Scripts/pytest.exe` from repo root, `-n0` for a
  single test. (This feature adds no backend code; the backend + `test_report.py`
  already exist — no pytest changes are planned.)
* **Client toasts** via `useStore.getState().enqueueToast` (`{level, title}`).
* **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`, never native `disabled`. (ReportView has no gated controls, so this is
  a no-op here — noted for compliance.)
* **Do not disrupt astrotown.**

---

## 3. TDD Plan

Five tasks. Pure logic sits behind tested functions; thin render/wiring is
verified by `tsc -b`. All tasks are mechanical/pattern-following → **Sonnet**
(the only math — `trendGeom` — is trivial linear scaling fully pinned by a tsx
test; no Opus-worthy subtlety anywhere).

Run all UI commands from `C:\Users\bear\astro\ui`.

---

### Task 1 — Pure shaping lib + report API client + tsx test

**Files**
* create `ui/src/lib/reportChart.ts`
* create `ui/src/api/reports.ts`
* create `ui/src/lib/__tests__/reportChart.test.ts`

**Interfaces** (`lib/reportChart.ts`)

```ts
export interface LiveSample {
  t: number;                 // ms epoch of the sub
  hfr: number | null;
  stars: number | null;
  rms: number | null;
  temp: number | null;
}

export interface TrendGeom { d: string; yMin: number; yMax: number; }

/** Polyline over a fixed w×h box; x by index, y auto-scaled to data
 *  min/max with `padY` fractional headroom. Handles negatives (temp) and
 *  flat series (yMin==yMax → padded so the line sits mid-box). null if empty. */
export function trendGeom(
  values: number[], w: number, h: number, padY?: number,
): TrendGeom | null;

/** Append one sample; keep at most `cap`, newest last (bounded ring). */
export function pushLiveSample(
  prev: LiveSample[], s: LiveSample, cap: number,
): LiveSample[];

/** One series, nulls dropped. */
export function pickSeries(
  ring: LiveSample[], key: "hfr" | "stars" | "rms" | "temp",
): number[];

export interface EndReasonMeta { word: string; tone: "good" | "warn" | "bad"; }
/** Maps the engine's real end reasons to a labelled tone. */
export function endReasonMeta(reason: string | null): EndReasonMeta;
```

**Interfaces** (`api/reports.ts`)

```ts
import { api } from "../api";
import type { SessionReport, SessionReportSummary } from "../types";

export const listReports = (): Promise<SessionReportSummary[]> =>
  api.get<SessionReportSummary[]>("/api/reports");

export const getReport = (id: string): Promise<SessionReport> =>
  api.get<SessionReport>(`/api/reports/${encodeURIComponent(id)}`);
```

**Steps**

1. Write `reportChart.ts`. Core of `trendGeom`:

```ts
export function trendGeom(values: number[], w: number, h: number, padY = 0.1): TrendGeom | null {
  if (values.length === 0) return null;
  let yMin = Math.min(...values), yMax = Math.max(...values);
  const span = Math.max(yMax - yMin, 1e-6);
  yMin -= span * padY;
  yMax += span * padY;
  const n = values.length;
  const toX = (i: number) => (n <= 1 ? w : (i / (n - 1)) * w);
  const toY = (v: number) => h - ((v - yMin) / (yMax - yMin)) * h;
  const d = values
    .map((v, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`)
    .join(" ");
  return { d, yMin, yMax };
}

export function pushLiveSample(prev: LiveSample[], s: LiveSample, cap: number): LiveSample[] {
  const next = [...prev, s];
  return next.length > cap ? next.slice(next.length - cap) : next;
}

export function pickSeries(ring: LiveSample[], key: "hfr" | "stars" | "rms" | "temp"): number[] {
  return ring.map((s) => s[key]).filter((v): v is number => v != null);
}

export function endReasonMeta(reason: string | null): EndReasonMeta {
  switch (reason) {
    case "complete":     return { word: "COMPLETE", tone: "good" };
    case "dawn_cutoff":  return { word: "DAWN CUTOFF", tone: "good" };
    case "aborted":      return { word: "ABORTED", tone: "warn" };
    case "quality":      return { word: "QUALITY STOP", tone: "warn" };
    case "cooling_skip": return { word: "COOLING SKIP", tone: "warn" };
    case "unsafe":       return { word: "UNSAFE — STOPPED", tone: "bad" };
    case "error":        return { word: "ERROR", tone: "bad" };
    default:             return { word: reason ? reason.toUpperCase() : "IN PROGRESS", tone: "warn" };
  }
}
```

2. Write `api/reports.ts` (above).

3. Write `reportChart.test.ts` using the eta.test harness (test/eq/assert/near,
   final `console.log` + `export const result`). Assertions:
   * `trendGeom([], 100, 50)` → `null`.
   * `trendGeom([5], 100, 50)` → non-null, `d` starts `"M"`, `yMin < 5 && yMax > 5`
     (flat series padded).
   * `trendGeom([1,2,3], 100, 50)`: `d` has exactly one `M` + two `L`; the `L`
     x-coords are `0`→`50`→`100`; **higher value ⇒ smaller y** (assert y at
     index 2 < y at index 0 by parsing the path, or check `yMax>yMin`).
   * `trendGeom([-10,-5,0], 100, 50)` → `yMin < -10` (negatives handled), non-null.
   * `pushLiveSample`: pushing past `cap` keeps the **last** `cap`, order
     preserved, most-recent last.
   * `pickSeries([{…hfr:1},{…hfr:null},{…hfr:3}], "hfr")` → `[1,3]`.
   * `endReasonMeta` for `complete`/`unsafe`/`aborted`/`error`/`quality`/
     `cooling_skip`/`dawn_cutoff`/`null`/`"weird"` → exact `{word,tone}`.

**Verify**

```
cd ui && npx tsx src/lib/__tests__/reportChart.test.ts
```
Expected: `reportChart.test: N/N passed`, no `✗` lines, exit 0.

```
cd ui && npx tsc -b
```
Expected: clean (no errors).

**Impl tier:** Sonnet — pure arithmetic + a mapping, fully pinned by the test.

---

### Task 2 — `TrendLine` primitive in `graphs.tsx`

**Files**
* modify `ui/src/components/graphs.tsx` (add one export; touch nothing existing)

**Interface**

```ts
export const TrendLine: React.MemoExoticComponent<(props: {
  values: number[];
  label: string;
  unit?: string;
  decimals?: number;   // default 2
  w?: number;          // default 240
  h?: number;          // default 56
}) => JSX.Element>;
```

**Steps**

1. `import { memo, useMemo } from "react"` is already at graphs.tsx:6. Add
   `import { trendGeom } from "../lib/reportChart";`.
2. Append `TrendLine`: label + last-value readout on top, the `trendGeom` polyline
   svg (`preserveAspectRatio="none"`, `stroke="var(--accent)"`,
   `vectorEffect="non-scaling-stroke"`, `role="img"`, `aria-label={\`${label} trend\`}`),
   a subtle `yMin`/`yMax` range line under it, and a `no data` fallback when
   `trendGeom` returns null. Tokens only (`text-dim`, `var(--*)`) — night-safe,
   matching the file's existing conventions (graphs.tsx:136-139).

```tsx
export const TrendLine = memo(function TrendLine({
  values, label, unit = "", decimals = 2, w = 240, h = 56,
}: { values: number[]; label: string; unit?: string; decimals?: number; w?: number; h?: number; }) {
  const geom = useMemo(() => trendGeom(values, w, h), [values, w, h]);
  const last = values.length ? values[values.length - 1] : null;
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline justify-between">
        <span className="label !text-[9px]">{label}</span>
        <span className="mono text-[10px] text-dim tabular-nums">
          {last != null ? `${last.toFixed(decimals)}${unit}` : "—"}
        </span>
      </div>
      {geom ? (
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="w-full"
          style={{ height: h }} role="img" aria-label={`${label} trend`}>
          <path d={geom.d} fill="none" stroke="var(--accent)" strokeWidth={1.4}
            vectorEffect="non-scaling-stroke" />
        </svg>
      ) : (
        <div className="text-dim text-[10px] py-3 text-center">no data</div>
      )}
      {geom && (
        <div className="flex justify-between mono text-[9px] text-dim/70 tabular-nums">
          <span>{geom.yMin.toFixed(decimals)}{unit}</span>
          <span>{geom.yMax.toFixed(decimals)}{unit}</span>
        </div>
      )}
    </div>
  );
});
```

**Verify**

```
cd ui && npx tsc -b
```
Expected: clean. (Thin render over the Task-1 tested `trendGeom`; no separate test.)

**Impl tier:** Sonnet — SVG boilerplate mirroring existing graphs.tsx primitives.

---

### Task 3 — `ReportView` + App wiring

**Files**
* create `ui/src/views/ReportView.tsx`
* modify `ui/src/App.tsx` (import + swap `VIEWS.report` placeholder for the real view)

**Interface**

```ts
export default function ReportView(): JSX.Element;
```

**Behavior / structure**

* State: `list: SessionReportSummary[]`, `sel: string | null`,
  `report: SessionReport | null`, `loading: boolean`, `err: string | null`.
* Effect A (mount): `listReports()` → `setList`; choose default
  `sel = useLastReportId() ?? list[0]?.id ?? null`. On failure:
  `enqueueToast({level:"error", title:"Couldn't load reports"})`, leave empty.
* Effect B (`sel`): `getReport(sel)` → `setReport`; on `ApiError`
  `enqueueToast` + inline `err`. Guard against out-of-order responses with a
  cancelled flag (MonitorView.tsx:175-199 pattern).
* Render, top→bottom:
  1. **Picker** — `<select>` of `list` (newest first; label
     `\`${plan_name} · ${new Date(started_at*1000).toLocaleString()}\``).
     `EmptyState` (components/ui) when `list.length === 0`.
  2. **Header block** — plan name; `endReasonMeta(report.end_reason)` word+tone
     chip; started/ended clocks; big stats: total integration
     `fmtDuration(report.integration_s)`, `frames_captured` accepted,
     `frames_rejected` rejected.
  3. **Per-filter table (headline)** — `report.by_filter.map` →
     `filter · frames · fmtDuration(integration_s) · HFR {hfr_median ?? "—"}`
     + `{rejected} rejected` when `>0`.
  4. **Per-target breakdown** — `report.targets.map` → name + totals, then nested
     `by_filter` rows (reuse the same row markup).
  5. **Safety-event timeline** — `report.safety_events.map` →
     `new Date(ts*1000)` time + `reason` + `action` chip; hidden when empty.
     (Skips/veto/watchdog events land here per report.py:304-314, engine.py:1384.)
  6. **Trends** — three `TrendLine`s fed `report.trends.hfr.map(p=>p[1])`
     (label `HFR`), `…temp` (label `Sensor °C`, `unit:"°C"`), `…rms`
     (label `Guide RMS`, `unit:"″"`).
  7. **CSV link** —
     `<a href={\`${BASE}/api/reports/${encodeURIComponent(sel)}/frames.csv\`} download className="btn …">Download frames.csv</a>`
     (`BASE` from `lib/base`; cookie auth rides the browser request).
* All tables scroll inside `overflow-x-auto`; tokens only; no gated controls.

**App wiring**

```tsx
import ReportView from "./views/ReportView";
// VIEWS registry (App.tsx:85): replace the placeholder line with:
report: ReportView,
```
Keep the App.tsx:80-84 comment (report is not a primary-nav entry) — trim it to
note ReportView has now landed. Do **not** add `report` to `NAV` or `GATED`.

**Verify**

```
cd ui && npx tsc -b
```
Expected: clean. Then confirm the placeholder is gone:
`grep -n "PlaceholderView label=\"Session Report\"" ui/src/App.tsx` → no match.

**Impl tier:** Sonnet — data-shaping render over tested helpers + existing
`api`/`TrendLine`/`ui` primitives.

---

### Task 4 — Entry points (SequenceView deep-link + NavMoreSheet row)

**Files**
* modify `ui/src/views/SequenceView.tsx`
* modify `ui/src/components/NavMoreSheet.tsx`

**Steps**

1. **SequenceView** — inside the finished branch of the action row
   (SequenceView.tsx:499-520, the `{failed && …}` block sits alongside the
   success case). Add, in the `showPanel` panel action row, guarded by
   `finished` (SequenceView.tsx:222) **and** a `lastReportId`:

```tsx
const lastReportId = useLastReportId();          // import from "../store"
// …in the action row (mt-3 flex, SequenceView.tsx:452):
{finished && lastReportId && (
  <button className="btn" onClick={() => setView("report")}>
    <Icon name="check" size={12} className="inline -mt-0.5 mr-1" />
    View session report →
  </button>
)}
```
`setView` is already available in SequenceView (it's the primary view component);
if not in scope, pull `const setView = useStore((s) => s.setView);`.

2. **NavMoreSheet** — append one entry to `OVERFLOW_VIEWS` (NavMoreSheet.tsx:36-44)
   so past reports are reachable on a phone:

```tsx
{ id: "report", label: "Reports", icon: "monitor" },  // icon: reuse an existing IconName
```
(`OverflowRow` renders a default `off` Led for unknown ids — NavMoreSheet.tsx:70-77
falls through to `"off"` — so no Led logic change is needed.)

**Verify**

```
cd ui && npx tsc -b
```
Expected: clean. Manual: on a finished run the report link appears and routes to
ReportView; the mobile More sheet lists "Reports".

**Impl tier:** Sonnet — two small additive wirings.

---

### Task 5 — Live in-acquisition trend strip (MonitorView + monitor.tsx)

**Files**
* modify `ui/src/components/monitor.tsx` (add `LiveTrendStrip`)
* modify `ui/src/views/MonitorView.tsx` (generalize the HFR ring; add the panel)

**Interface** (`components/monitor.tsx`)

```ts
export const LiveTrendStrip: React.MemoExoticComponent<(props: {
  ring: LiveSample[];   // from lib/reportChart
}) => JSX.Element>;
```

**Steps**

1. **`monitor.tsx`** — add `LiveTrendStrip`: a 2-col grid of four `TrendLine`s
   (import from `./graphs`; import `pickSeries`, `LiveSample` from
   `../lib/reportChart`):
   * `HFR` — `pickSeries(ring,"hfr")`, decimals 2
   * `stars` — `pickSeries(ring,"stars")`, decimals 0
   * `Guide RMS` — `pickSeries(ring,"rms")`, unit `"″"`
   * `Sensor °C` — `pickSeries(ring,"temp")`, unit `"°C"`, decimals 1
   Each `TrendLine` self-renders `no data` when its series is empty, so a rig
   with no guiding still shows HFR/stars.

2. **`MonitorView.tsx`** — replace the single-HFR ring (MonitorView.tsx:167-172)
   with a 4-series ring using `pushLiveSample`, snapshotting **all four** metrics
   at each new sub (`preview.id` change), sampling temp/rms from the live slices
   at that instant:

```tsx
import { pushLiveSample, pickSeries, type LiveSample } from "../lib/reportChart";
// …
const liveRing = useRef<LiveSample[]>([]);
const lastSubId = useRef<number | null>(null);
if (preview && preview.id !== lastSubId.current) {
  lastSubId.current = preview.id;
  liveRing.current = pushLiveSample(liveRing.current, {
    t: Date.now(),
    hfr: preview.hfr ?? null,
    stars: preview.stars ?? null,
    rms: guideRms?.rms_total ?? null,
    temp: camera?.temperature ?? null,
  }, 40);
}
```
   * Repoint the existing Progress inline HFR sparkline (MonitorView.tsx:490-495)
     at `pickSeries(liveRing.current, "hfr")` so there is **one** ring, not two.
   * Add a new panel in the mosaic (near Guide/Thermal), gated the same way as the
     inline sparkline (run active + ≥3 samples):

```tsx
{runActive && liveRing.current.length >= 3 && (
  <Panel className="col-span-full sm:col-span-1 lg:col-span-3" title="Live trend">
    <LiveTrendStrip ring={liveRing.current} />
  </Panel>
)}
```
   Import `LiveTrendStrip` alongside the other `../components/monitor` imports
   (MonitorView.tsx:42-56).

**Verify**

```
cd ui && npx tsc -b
```
Expected: clean. Manual smoke (sim rig running): the strip fills with HFR/stars
from previews and RMS/temp when those devices report; empties gracefully idle.

**Impl tier:** Sonnet — ring generalization + panel wiring over Task-1/2 tested
helpers; the ring math is the tested `pushLiveSample`.

---

## 4. Open decisions

1. **Trend x-axis: index vs real timestamp.** The report `trends` carry `[ts,v]`
   but `TrendLine` plots by index. **Rec:** index-x for v1 — the backend already
   downsamples each series to ≤200 time-ordered points (report.py:431-472), so
   spacing already tracks time closely; a true time axis is a later polish. (Doc
   this in `TrendLine`.)
2. **Desktop persistent entry point.** ReportView is intentionally not in the
   desktop rail (App.tsx:80-84). **Rec:** ship with the SequenceView finished-run
   link (desktop) + NavMoreSheet row (mobile); if browsing old reports on desktop
   with an idle engine proves needed, add a small "Reports →" affordance to the
   Sessions/Plan area later — do **not** reverse the no-primary-nav decision now.
3. **Live-strip sample cap N.** MonitorView's old HFR ring kept 20
   (MonitorView.tsx:171). **Rec:** 40 — one strip now spans a longer stretch of
   subs, and 40 short polylines is trivially cheap; expose as the `cap` arg so
   it's one-line tunable.
4. **Live-strip placement.** Could extend the existing Progress HFR block instead
   of a new panel. **Rec:** a dedicated "Live trend" `Panel` — the Progress inline
   HFR sparkline stays a focused dew early-warning (its documented purpose,
   MonitorView.tsx:165/489), while the strip is the full four-metric view; both
   read the single generalized ring, so there's no duplicate state.
5. **CSV auth.** The route is `CAP_VIEW_STATUS` and cookie-authed; a plain
   `<a download>` carries the session cookie. **Rec:** plain anchor (no JS blob
   fetch) — simplest, and matches how the browser already authenticates every
   `api.*` request.
