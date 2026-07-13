# Plan Scheduling & Target Tools (Wave 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the already-working engine scheduler its missing UI: per-target schedule editor, runtime waiting/gated chips, altitude sparklines + tonight ordering, quick-add parity, Frame-in-Atlas, safety/meridian controls — plus one engine polish (clear stale WS schedule sub-state).

**Architecture:** The server engine (Batch 4b) already resolves windows, gates starts, enforces stops, and publishes `SequenceState.schedule` — every UI piece binds to shipped contracts. Client `Schedule` == server `Schedule` field-for-field. New pure helpers (`lib/schedule.ts` summary; `lib/visibility.ts` `buildSparkGeometry`) keep the SequenceView diff mostly declarative. Backfill (`defaultSchedule()`) is centralized in the store's load/add paths.

**Tech Stack:** React 18 + TS + Tailwind; FastAPI engine (one method's semantics touched); pytest + self-executing `npx tsx` asserts (NO vitest).

**Spec:** `docs/superpowers/specs/2026-07-13-plan-schedule-design.md` (1d92644). Seams (verbatim, current disk — HAND THESE TO IMPLEMENTERS): `.superpowers/sdd/seams/wave3-plan-ui.md` (client) + `wave3-engine.md` (server).

## Global Constraints

- UI gates per task, from `ui/`: `npm run build` + regression tsx tests `npx tsx src/lib/__tests__/{surveyView,tooltipMachine,missingOptics}.test.ts` (6/6, 7/7, 4/4) + any new test files. CI never runs tsx tests — run them yourself, paste outputs. KNOWN HARNESS ISSUE: if a command is auto-backgrounded, re-run immediately, stay foreground, never park.
- Server gates (Task 5 only): from `server/`, `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest tests/<file> -q` task-scoped; the wave's final verification runs the full suite.
- ENGINE SEMANTICS ARE FROZEN except Task 5's clear-on-start/terminal: window resolution, `on_missed`, stop boundaries, safety gating are shipped behavior — do not touch.
- Client `Schedule` (types.ts:630-640) and server `Schedule` (sequence/models.py:17-33) are identical; the editor binds 1:1, no type changes.
- Plan mutation idiom is `patchTarget(ti, {...})` map-spread (SequenceView.tsx:179-180); a schedule patch is `patchTarget(ti, { schedule: { ...sched, ...patch } })`. The plan-builder stays usable for viewers; only run-controls gate on `canRun`.
- All new controls ≥44 px touch targets, night-safe (no window.confirm — use `confirmDialog`), text ≥12px real CSS px, status by glyph+text not hue alone.
- Commit per task, standard two trailers (Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> + Claude-Session line).
- SequenceView is 647 lines — new UI blocks of any size go in NEW component files under `ui/src/components/sequence/`; SequenceView gets mounts + handlers only.

---

### Task 1: Pure lib — schedule summary + spark geometry (+ tests)

**Files:**
- Create: `ui/src/lib/schedule.ts`, `ui/src/lib/__tests__/schedule.test.ts`
- Modify: `ui/src/lib/visibility.ts` (append `buildSparkGeometry`; existing exports untouched)
- Create: `ui/src/lib/__tests__/sparkGeometry.test.ts`

**Interfaces:**
- Produces: `scheduleSummary(s: Schedule | undefined): string`; `SparkGeometry { altPath: string | null; altLimitY: number; darkBand: { x: number; w: number } | null }`; `buildSparkGeometry(night: VisibilityNight, w: number, h: number): SparkGeometry`. Tasks 2-4 consume these.

- [ ] **Step 1: Create `ui/src/lib/schedule.ts`**

```ts
// schedule.ts — pure presentation helpers for the per-target autorun schedule
// (wave-3 §1). No React, no DOM: npx-tsx testable (rotatorDial.ts precedent).
// The engine's semantics live server-side (sequence/schedule.py); this module
// only summarizes the CONFIG shape for the collapsed sub-panel chip.

import type { Schedule } from "../types";

function offset(min: number): string {
  if (!min) return "";
  return ` ${min > 0 ? "+" : "−"}${Math.abs(min)}m`;
}

function startLabel(s: Schedule): string {
  switch (s.start_mode) {
    case "now": return "";
    case "dusk": return `Dusk${offset(s.start_offset_min)}`;
    case "dawn": return `Dawn${offset(s.start_offset_min)}`;
    case "time": return s.start_time ?? "??:??";
    default: return "";
  }
}

function stopLabel(s: Schedule): string {
  const parts: string[] = [];
  if (s.stop_mode === "dawn") parts.push(`dawn${offset(s.stop_offset_min)}`);
  if (s.stop_mode === "time") parts.push(s.stop_time ?? "??:??");
  if (s.max_run_min > 0) parts.push(`max ${s.max_run_min}m`);
  return parts.join(" · ");
}

/** Collapsed-chip summary, e.g. "Runs immediately", "Dusk +30m → dawn",
 *  "22:00 → max 90m · skip if missed", "Alt ≥ 35° → dawn −20m". */
export function scheduleSummary(s: Schedule | undefined): string {
  if (!s) return "Runs immediately";
  const start = startLabel(s);
  const gate = s.min_altitude_deg > 0 ? `Alt ≥ ${Math.round(s.min_altitude_deg)}°` : "";
  const begin = [start, gate].filter(Boolean).join(" & ");
  const stop = stopLabel(s);
  const missed = s.on_missed === "skip" ? "skip if missed" : "";
  if (!begin && !stop && !missed) return "Runs immediately";
  const head = begin || "Now";
  const tail = [stop, missed].filter(Boolean).join(" · ");
  return tail ? `${head} → ${tail}` : head;
}
```

- [ ] **Step 2: `ui/src/lib/__tests__/schedule.test.ts`** (standard self-executing harness — transcribe the `test`/`assert`/report helpers from `surveyView.test.ts`); cases:

```ts
test("all defaults -> Runs immediately", () => {
  assert(scheduleSummary({ start_mode: "now", start_offset_min: 0, start_time: null,
    min_altitude_deg: 0, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait" }) === "Runs immediately", "defaults");
});
test("undefined -> Runs immediately", () => {
  assert(scheduleSummary(undefined) === "Runs immediately", "undefined");
});
test("dusk +30 to dawn", () => {
  const s = scheduleSummary({ start_mode: "dusk", start_offset_min: 30, start_time: null,
    min_altitude_deg: 0, stop_mode: "dawn", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait" });
  assert(s === "Dusk +30m → dawn", `got "${s}"`);
});
test("time start, cap, skip", () => {
  const s = scheduleSummary({ start_mode: "time", start_offset_min: 0, start_time: "22:00",
    min_altitude_deg: 0, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 90, on_missed: "skip" });
  assert(s === "22:00 → max 90m · skip if missed", `got "${s}"`);
});
test("altitude gate only", () => {
  const s = scheduleSummary({ start_mode: "now", start_offset_min: 0, start_time: null,
    min_altitude_deg: 35, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait" });
  assert(s === "Alt ≥ 35°", `got "${s}"`);
});
test("negative dawn offset on stop", () => {
  const s = scheduleSummary({ start_mode: "dusk", start_offset_min: 0, start_time: null,
    min_altitude_deg: 0, stop_mode: "dawn", stop_offset_min: -20, stop_time: null,
    max_run_min: 0, on_missed: "wait" });
  assert(s === "Dusk → dawn −20m", `got "${s}"`);
});
```

Run: `npx tsx src/lib/__tests__/schedule.test.ts` — expect 6/6.

- [ ] **Step 3: Append `buildSparkGeometry` to `ui/src/lib/visibility.ts`**

READ the existing `buildGeometry` body + the `VisibilitySample` type in types.ts first (the sample field names — unix timestamp + target alt — must be taken from the real type, not guessed). Then append (do not modify any existing export):

```ts
// ---------------------------------------------------------------- sparkline
// Mini variant for the Plan tab's per-target cards (wave-3 §3). buildGeometry
// closes over the full-panel VIS_W/VIS_H/PLOT constants; this one is size-
// parameterized and emits only the three layers a 120×28 chip can carry:
// target-alt path, alt-limit line, dark band. No moon, no labels, no NOW.
export interface SparkGeometry {
  altPath: string | null;             // null when never_rises_above_limit
  altLimitY: number;
  darkBand: { x: number; w: number } | null;
}

export function buildSparkGeometry(
  night: VisibilityNight,
  w: number,
  h: number,
): SparkGeometry {
  const samples = night.samples;
  if (!samples.length || night.never_rises_above_limit) {
    return { altPath: null, altLimitY: h - (night.alt_limit_deg / ALT_MAX) * h, darkBand: null };
  }
  const t0 = samples[0].unix;
  const t1 = samples[samples.length - 1].unix;
  const span = Math.max(1, t1 - t0);
  const x = (t: number) => ((t - t0) / span) * w;
  const y = (alt: number) => h - (Math.max(0, alt) / ALT_MAX) * h;
  const altPath = samples
    .map((s, i) => `${i === 0 ? "M" : "L"}${x(s.unix).toFixed(1)} ${y(s.alt).toFixed(1)}`)
    .join(" ");
  const darkBand =
    night.dark_start_unix != null && night.dark_end_unix != null
      ? {
          x: x(Math.max(t0, night.dark_start_unix)),
          w: Math.max(0, x(Math.min(t1, night.dark_end_unix)) - x(Math.max(t0, night.dark_start_unix))),
        }
      : null;
  return { altPath, altLimitY: y(night.alt_limit_deg), darkBand };
}
```

IF `VisibilitySample`'s fields are not `unix`/`alt`, use the real names and note the substitution in your report (this is the one place the plan could not pre-verify; the type is in types.ts near VisibilityNight).

- [ ] **Step 4: `ui/src/lib/__tests__/sparkGeometry.test.ts`** (same harness): build a synthetic `VisibilityNight` with 5 samples over 4 h, alt rising 10→60°, `alt_limit_deg: 30`, dark window covering the middle half; assert: path starts with `M0.0` and has 5 segments; `altLimitY === h - 30/90*h`; dark band x/w match the middle half within 0.5 px; `never_rises_above_limit: true` → `altPath === null`; empty samples → null path, no throw. Run — expect 5/5 (or the case count you implement, ≥5).

- [ ] **Step 5: Gates + commit**

`npm run build` + all tsx tests (3 old + 2 new). Then:

```bash
git add ui/src/lib/schedule.ts ui/src/lib/visibility.ts ui/src/lib/__tests__/schedule.test.ts ui/src/lib/__tests__/sparkGeometry.test.ts
git commit -m "feat(plan): schedule summary + size-parameterized spark geometry helpers (wave-3 §1,3)"
```

---

### Task 2: Schedule editor sub-panel + backfill + Automation additions  **(judgment-bearing — opus)**

**Files:**
- Create: `ui/src/components/sequence/SchedulePanel.tsx`
- Modify: `ui/src/views/SequenceView.tsx` (mount in `renderTarget`; Automation panel additions)
- Modify: `ui/src/store.ts` (backfill in `loadPlan` + `addTargetsToPlan`; `defaultPlan` gains safety/meridian defaults)

**Interfaces:**
- Consumes: `scheduleSummary` (Task 1), `defaultSchedule()` (store, currently uncalled), `patchTarget` idiom, `Schedule` type.
- Produces: `SchedulePanel({ schedule, disabled, onChange }: { schedule: Schedule; disabled: boolean; onChange: (patch: Partial<Schedule>) => void })` — a collapsed disclosure row (chip = `scheduleSummary`) that expands to the 9-field editor.

**Requirements (seam refs in `wave3-plan-ui.md`):**
1. **Backfill (store):** `loadPlan()` maps loaded targets to `{ schedule: defaultSchedule(), ...t }`-style backfill (spread order such that a PRESENT schedule wins); `addTargetsToPlan` backfills each incoming target the same way (Atlas sends currently carry no schedule). SequenceView's `addTarget` adds `schedule: defaultSchedule()` to the new target literal. After this task `target.schedule` is always present for targets flowing through these paths, but the UI must STILL tolerate `undefined` (old persisted state elsewhere): `SchedulePanel` is only rendered with a real schedule — `renderTarget` uses `t.schedule ?? defaultSchedule()` when passing it and patches through `patchTarget(ti, { schedule: { ...(t.schedule ?? defaultSchedule()), ...patch } })`.
2. **SchedulePanel UI:** collapsed row inside each target card, below the steps grid: a ≥44px disclosure button showing `⏱ {scheduleSummary(schedule)}`; expanding reveals the editor: start-mode segmented control (`now|dusk|dawn|time` — follow the repo's segmented/radio idiom if one exists, else 4 small toggle-buttons), offset stepper shown only for dusk/dawn (`Stepper` component, ±min, range −120..+120 step 5), `HH:MM` input shown only for `time` (plain `field` input, `placeholder="HH:MM"`, commit on blur; invalid text reverts — do NOT invent client validation beyond the revert; the server 422s malformed times), `min_altitude_deg` stepper (0-80, step 5, unit °, help: per-TARGET start gate vs the global safety floor), stop-mode segmented (`none|dawn|time`) with the same conditional offset/time inputs, `max_run_min` stepper (0-600 step 15, 0 = no cap), `on_missed` Toggle row (wait ↔ skip) with an InfoDot explaining the engine behavior (waits for the window vs skips the target for the night). `disabled` (running) greys everything (same `disabled={running}` convention as the step editors).
3. **Automation panel:** add two rows following the existing label/Toggle/input idiom: "safety monitor gate" `Toggle` bound to `plan.safety_check ?? <server default>` and "meridian warn lead (min)" numeric input bound to `plan.meridian_flip_warn_min ?? <server default>`. READ `server/astrodeck/sequence/models.py` SequencePlan's defaults for these two fields and mirror them in `defaultPlan()` (store.ts:129-147 — its comment REQUIRES exact parity with the server model; report the values you found).
4. Keep SequenceView's growth minimal: the panel is one `<SchedulePanel …/>` mount + the two Automation rows; everything else lives in the new file.

- [ ] Steps: implement → `npm run build` + all tsx tests (5 files) → self-review (backfill spread order actually preserves an existing schedule; expanding one target's panel doesn't expand all — per-card open state lives in SchedulePanel itself) → commit:

```bash
git commit -m "feat(plan): per-target Schedule sub-panel + defaultSchedule backfill + safety/meridian automation controls (wave-3 §1,6)"
```

---

### Task 3: Tonight tools — sparkline on cards + order-by-tonight  **(opus)**

**Files:**
- Create: `ui/src/components/sequence/TargetSpark.tsx`
- Modify: `ui/src/views/SequenceView.tsx` (spark mount in `renderTarget`; "Order by tonight" button in the Targets panel header)

**Interfaces:**
- Consumes: `buildSparkGeometry` (Task 1), `GET /api/visibility?ra&dec&alt_limit`, `POST /api/visibility/order` (`{targets:[{name,ra_hours,dec_deg,mosaic_group?}], date?}` → `{targets:[...], recommended_order:number[]}` — indices into the REQUEST list; mosaic groups stay atomic server-side), `setPlan`, `useStore((s) => s.site)` for `horizon_min_deg`.
- Produces: `TargetSpark({ ra_hours, dec_deg, altLimit }: {...}): JSX.Element` — self-contained lazy fetch + module-level cache.

**Requirements:**
1. **TargetSpark:** ~120×28 inline SVG chip (`aria-label` describing transit/peak or "does not rise above limit"); fetches `/api/visibility` once per rounded key (`Math.round(ra*1000)/1000`, `Math.round(dec*100)/100`, altLimit) through a module-level `Map<string, Promise<VisibilityNight>>` cache so N cards with duplicate coords (mosaic panels!) fire ONE request and re-renders never refetch; alive-flag cleanup like VisibilityPanel's. Render: dark band rect (low-opacity fill), alt-limit dashed line (`var(--warn)`), alt path (`var(--accent)`, strokeWidth 1.5, svg-halo); `never_rises_above_limit` renders a `⚠ low all night` warn text chip instead of the SVG; while loading, a dim placeholder box (no skeleton churn). Mount inside `renderTarget`'s header row (after the coordinates span).
2. **Order by tonight:** button in the Targets `Panel`'s `right=` slot (next to the search input; `disabled={running || plan.targets.length < 2}`): POSTs `/api/visibility/order` with `plan.targets.map(t => ({name: t.name, ra_hours: t.ra_hours, dec_deg: t.dec_deg, mosaic_group: t.mosaic_group}))`; on response, `setPlan({ ...plan, targets: body.recommended_order.map(i => plan.targets[i]) })`; toast success ("Ordered by tonight's transits") / error via the existing `act()` wrapper or showToast. NOTE the response order indices index the REQUEST array — verify lengths match before applying; bail with an error toast on mismatch.
3. NO new store state; no changes to the blocks/grouping logic (reordering the flat array is enough — groups stay contiguous because the server keeps them atomic).

- [ ] Steps: implement → gates (build + 5 tsx files) → self-review (cache key collision across altLimit values; unmount during fetch; reorder with 0/1 targets disabled) → commit:

```bash
git commit -m "feat(plan): per-target altitude sparklines (cached) + order-by-tonight via /api/visibility/order (wave-3 §3)"
```

---

### Task 4: Runtime schedule chips + quick-add confirm + Frame-in-Atlas  **(sonnet)**

**Files:**
- Modify: `ui/src/views/SequenceView.tsx`

**Interfaces:**
- Consumes: `sequence.schedule` runtime block (`SequenceState`, types.ts:248-252: `{state: "waiting"|"ready"|"window_closed"|"never_rises", reason, eta_s, start_ts?, stop_ts?}`), `fmtTime` from `lib/visibility`, `confirmDialog` (components/ConfirmDialog — same call shape as AtlasView.tsx:1192-1200), `openFraming` + `setFraming` from the store, `CatalogEntry` type, `IconButton` with `icon="frame"` (MountView.tsx:1266-1270 precedent).

**Requirements:**
1. **Runtime chip:** where the run panel shows sequence state (the `showPanel` region), when `sequence.schedule` is present render one chip line: `waiting` → `⏱ Waiting — {reason} · starts {fmtTime(start_ts)}` (dim tone); `window_closed`/`never_rises` → `⚠ {reason}` (warn tone). Also on the ACTIVE target card (`sequence.target_index === ti && running`) show the same chip inline. Pure render — no new state.
2. **Quick-add confirm:** `addTarget(e)` becomes async: fetch `GET /api/visibility?ra=${e.ra_hours}&dec=${e.dec_deg}&alt_limit=${site?.horizon_min_deg ?? 30}`; if `never_rises_above_limit`, `await confirmDialog({ title: "Below tonight's limit", body: \`${e.name || e.id} doesn't rise above ${Math.round(night.alt_limit_deg)}° tonight (peaks ${night.transit_alt.toFixed(0)}°). Add anyway?\`, tone: "warn", mode: "confirm", confirmLabel: "Add anyway" })` — abort add on cancel (mirrors AtlasView's sendToPlan gate verbatim). Visibility-fetch FAILURE must not block adding (catch → proceed without confirm). Needs `const site = useStore((s) => s.site);`.
3. **Frame in Atlas:** in `renderTarget`'s header row (near the delete button) an `IconButton icon="frame" label={\`Frame ${t.name} in the Sky Atlas\`}` that builds `const entry: CatalogEntry = { id: t.name, name: t.name, type: "", ra_hours: t.ra_hours, dec_deg: t.dec_deg, mag: 0, size_arcmin: 0, alt: 0, az: 0 };` then `openFraming(entry); setFraming({ rotation_deg: t.rotation_deg ?? 0 });` (openFraming switches view; the follow-up setFraming lands on the fresh session synchronously). Mosaic-group HEADERS get the same button using the group's first member's coords and the group name as id. Never disabled (framing works offline — MountView precedent).

- [ ] Steps: implement → gates (build + 5 tsx files) → self-review (addTarget still clears search on both paths; confirm not shown when visibility says fine; chips don't render when `sequence.schedule` absent) → commit:

```bash
git commit -m "feat(plan): runtime schedule chips, quick-add below-horizon confirm, Frame-in-Atlas buttons (wave-3 §2,4,5)"
```

---

### Task 5: Server — clear stale WS schedule sub-state  **(sonnet)**

**Files:**
- Modify: `server/astrodeck/sequence/engine.py`
- Test: extend the engine test file that already covers scheduling waits (locate via `grep -rln "waiting" server/tests/test_sequence*.py` — the file containing `test_mount_stops_tracking_while_waiting_for_next_target`; follow its sim-rig/time idiom exactly)

**Background (seam `wave3-engine.md` §6):** `self.state` accumulates via `{**self.state, **kw}` in `_set_state` (engine.py:~1123); the `schedule={"state":"waiting",...}` block set at engine.py:502-508 is never removed, so `GET /api/sequence/state`, the monitor snapshot, and the WS payload all carry a stale "waiting" block after the wait ends.

- [ ] **Step 1: Failing test.** In the located test file, add: drive the engine exactly like the existing waiting-state test (schedule window opening in the future) far enough to observe `engine.state.get("schedule")` present with `state=="waiting"`; then bring the window open (the existing test's time mechanism) / let the target start; assert `"schedule" not in engine.state` once the target is running. Add a second assertion path for abort: abort during the wait → after the engine stops, `"schedule" not in engine.state`. Run task-scoped pytest — expect the new test FAILS (stale key present).

- [ ] **Step 2: Implement — sentinel clear.** In `_set_state`, before the merge:

```python
        # schedule=None is an explicit CLEAR (wave-3 §2): the waiting sub-state
        # must not outlive the wait it describes (GET /api/sequence/state and the
        # monitor snapshot both serve this dict verbatim).
        if "schedule" in kw and kw["schedule"] is None:
            kw = {k: v for k, v in kw.items() if k != "schedule"}
            self.state.pop("schedule", None)
```

Then pass `schedule=None` at the moments a wait genuinely ends: (a) `_setup_target`'s first `_set_state(...)` (engine.py:~558) gains `schedule=None`; (b) `_run_calibration`'s first `_set_state` likewise (locate it); (c) every TERMINAL `_set_state(state="complete"|"aborted"|"error"...)` call gains `schedule=None` (grep `_set_state(state=` and extend each terminal one — mechanical extension, report each line you touched). Do NOT auto-clear on every `_set_state` call — intermediate publishes during a wait (pause, live blocks) must keep the block.

- [ ] **Step 3:** task-scoped pytest green (whole file), then targeted `tests/test_survey.py` untouched-sanity NOT required (different module). Run the full engine test file + `tests/test_sequence_engine_fixes.py` if that's not the same file.

- [ ] **Step 4: Commit**

```bash
git commit -m "fix(sequence): clear the WS schedule waiting sub-state when a gated target starts or the run ends (wave-3 §2)"
```

---

## Final verification (before whole-branch review)

- [ ] Full server suite from `server/` (`…python.exe -m pytest -q`) — expect 940+ passing (KNOWN HARNESS ISSUE applies).
- [ ] From `ui/`: `npm run build` + ALL tsx test files (3 wave-1 + schedule + sparkGeometry).
- [ ] Manual smoke list for the user: expand a schedule panel, set dusk→dawn, run with sim rig and watch the "Waiting — …" chip; order-by-tonight with a mosaic group (stays contiguous); quick-add a never-rises target (confirm appears); Frame in Atlas round-trip.
