# NOV-8 — Plain-language "why nothing is happening" status on the Monitor screen

**Design spec + TDD implementation plan.** Branch `feat/monitor-waiting-status`.
Design-only document; no implementation files are modified by this doc.

> **Program-brief note.** The task cites `docs/superpowers/specs/2026-07-22-pro-novice-feature-program.md` (NOV-8, §6). That file is **not present in the repo** (Glob over `docs/superpowers/specs/` returns no novice/pro brief; no `NOV-8` string exists under `docs/`). This spec therefore grounds itself in the *verified code* (cited file:line throughout) plus the feature description handed down in the task. If the brief lands later, reconcile the copy table against its §6 wording — the formatter is the single seam to touch.

---

## 1. Design

### 1.1 Goal

During a normal night-time wait the engine is `running` but *deliberately idle* — the target is below its start gate, the observing window has not opened, or a meridian flip is imminent. On the Monitor screen today the state badge reads **RUNNING**, the progress bar sits still, and a novice reads "frozen / broken". Fix: one **calm, prominent, plain-language sentence** on `MonitorView` that says *what* the app is waiting for and *how long*, e.g.

- `Waiting for your target to rise above 30° at 23:14, about 47 min.`
- `Waiting for the observing window to open at 23:14, about 47 min.`
- `Meridian flip in 18 min — imaging will pause briefly.`

The data already reaches the client on the `sequence` event. The only missing piece is a **shared pure formatter** that turns `{schedule, live}` into `{tone, text}`, consumed by **both** `MonitorView` (new prominent banner) and `SequenceView` (existing `ScheduleChip`, refactored onto it for DRY).

### 1.2 Current-state seams (verified, file:line)

**The data is already on the wire and in the store.**

- Backend `server/astrodeck/sequence/schedule.py::gating_status()` returns `{state, reason, eta_s, start_ts, stop_ts}` (schedule.py:326-389). The `out()` helper (schedule.py:362-364) shows `eta_s` is clamped `>= 0`. The five states and their **actual reason strings** are:
  - `window_closed` → `"observing window has closed"` (schedule.py:367-368)
  - `never_rises` → `f"never rises above {gate:g} deg tonight"` (schedule.py:374-376)
  - `waiting` (clock) → `"waiting for start time"`, `eta_s = start_ts - now` (schedule.py:379-380)
  - `waiting` (altitude) → `f"below start altitude ({gate:g} deg)"`, `eta_s = time_to_gate` (schedule.py:382-386)
  - `ready` → `""`, `eta_s = 0` (schedule.py:388-389)
  These reasons are **terse/machine-ish** (`"below start altitude (30 deg)"`), not novice copy — so NOV-8 phrasing is composed **client-side**, keyed off `state` + the numeric fields, extracting the gate degrees from `reason` opportunistically.
- Engine `server/astrodeck/sequence/engine.py::_live_block()` attaches `live.meridian_eta_s = round(ttf_h*3600)` **only when the flip is already within `plan.meridian_flip_warn_min`** (engine.py:550-556) and **always `> 0`** (guarded `ttf_h > 0` at :552). ⇒ The engine owns the "how soon do we mention it" gate; the client needs **no** additional lead threshold.

- `ui/src/types.ts` — `SequenceState.schedule?: {state:"waiting"|"ready"|"window_closed"|"never_rises"; reason:string; eta_s:number; start_ts?:number; stop_ts?:number}` (types.ts:279-285) and `SequenceState.live?: {meridian_eta_s?; sensor_temp_c?; guide_rms?}` (types.ts:288). `reason` is a required non-nullable `string` (may be `""`).
- `ui/src/store.ts` — the `sequence` event lands the **whole** `SequenceState` object by reference: `set({ sequence: seq, runBanner })` (store.ts:1254), so `schedule` and `live` ride along untouched. `useSeq = () => useStore((s) => s.sequence)` (store.ts:1446) already exposes them.
- `ui/src/views/SequenceView.tsx` — `ScheduleChip` (SequenceView.tsx:56-72) **already** renders this: `waiting` → dim clock chip `Waiting — {reason} · starts {fmtTime(start_ts)}` (:57-63); `window_closed`/`never_rises` → warn alert chip `{reason}` (:64-70); `ready` → `null` (:71). It is rendered in the run panel when `sequence.schedule` is truthy (:426-430) and inline on the active target card (:584-586). `fmtTime` is imported from `../lib/visibility` (:18) and used **only** inside `ScheduleChip` (:60).
- `ui/src/views/MonitorView.tsx` — the gap. `useSeq()` is read (MonitorView.tsx:102); the sticky header strip (`<header … sticky top-0 z-10 …>`, :315-382) shows `StateBadge state={state}` (:318), the target line (:329), and the finish `LiveTimer` (:339-348), then a controls row (:355-381). **There is no waiting/meridian sentence.** A 1 s `useCoarseTick()` (:92-99) is already in scope as `now` (ms, :129) for live text. Meridian is surfaced only as the numeric `MeridianCountdown` ring tile fed by `status.meridian` (:513, :675-729) — *not* a plain-language sentence. `telemetryStale` (:111) is folded into the `HealthStrip` verdict via `deriveHealthIssues` (:284-301, :307), a **separate** surface from the new banner.

**Reusable formatters (pure, DOM-free, tsx-safe).** `fmtDuration` (eta.ts:27-40), `fmtCountdown` (eta.ts:70-80), timing constants (eta.ts:10-19); `fmtTime(unix)` → local `HH:MM` (visibility.ts:131-137). `humanize.ts` establishes the "substring-match a server string" idiom (humanize.ts:16-47) that the gate-degree extraction below mirrors.

### 1.3 Shared-formatter contract

New pure module `ui/src/lib/scheduleStatus.ts`:

```ts
export type ScheduleTone = "calm" | "warn";
export interface ScheduleStatus { tone: ScheduleTone; text: string; }

export function formatScheduleStatus(
  schedule: SequenceState["schedule"] | undefined | null,
  live:     SequenceState["live"]     | undefined | null,
  nowUnix:  number,                       // seconds (Date.now()/1000)
): ScheduleStatus | null;
```

- Returns `null` for every "nothing to say" case: no schedule + no meridian, `ready`, idle. Callers render **nothing** on `null` (no empty box).
- `tone: "calm"` for `waiting` and meridian (not a fault — it's a normal, explained wait). `tone: "warn"` only for `window_closed` / `never_rises` (the target genuinely won't image tonight).
- **Precedence** (a schedule state means imaging is not running at all, so it dominates the meridian heads-up, which only matters mid-imaging): `window_closed` → `never_rises` → `waiting` → *(schedule `ready`/absent)* meridian → `null`.
- Pure of clocks except `nowUnix`; reuses `fmtTime` (absolute clock) and a thin local coarse duration helper `fmtWaitApprox` (plain-language "47 min" / "1 hr 12 min" — `fmtDuration` would emit `"47m 0s"`, wrong for a calm sentence). No React, no DOM.

**`nowUnix` is seconds** to line up with `start_ts`/`eta_s` (both unix seconds server-side) and to reuse `fmtTime`. `MonitorView` passes `now / 1000` (its coarse ticker is ms); `SequenceView` passes `Date.now() / 1000`.

### 1.4 Per-state copy table

`gate` = degrees parsed from `reason` via `/(\d+(?:\.\d+)?)\s*deg/` (→ number | null; graceful when absent). `remaining`/`clock` resolution: if `start_ts != null && start_ts > nowUnix` → clock case (`remaining = start_ts - nowUnix` — **live**, ticks down each render; `clockUnix = start_ts`); else if `eta_s > 0` → altitude case (`remaining = eta_s`; `clockUnix = nowUnix + eta_s`); else `remaining = 0`, `clockUnix = null`. (In the clock case `now + eta_s == start_ts`, so this is exact for both; the altitude case's `start_ts` is already in the past — engine.py case 4 — hence the `nowUnix + eta_s` fallback.)

| Input | tone | text |
|---|---|---|
| `waiting`, altitude (start_ts in past), gate=30 | calm | `Waiting for your target to rise above 30° at 23:14, about 47 min.` |
| `waiting`, altitude, gate=null | calm | `Waiting for your target to rise high enough at 23:14, about 47 min.` |
| `waiting`, clock (start_ts future) | calm | `Waiting for the observing window to open at 23:14, about 47 min.` |
| `waiting`, no clock resolvable (`clockUnix==null`, `remaining>0`) | calm | `Waiting for the observing window to open, about 47 min.` |
| `waiting`, no timing at all (`remaining==0`) | calm | `Waiting for the observing window to open.` |
| `window_closed` | warn | `Tonight's observing window has closed for this target.` |
| `never_rises`, gate=30 | warn | `This target never rises above 30° from your site tonight.` |
| `never_rises`, gate=null | warn | `This target never rises high enough from your site tonight.` |
| meridian `meridian_eta_s = 1080` (schedule ready/absent) | calm | `Meridian flip in 18 min — imaging will pause briefly.` |
| meridian `meridian_eta_s <= 0` (defensive; engine emits `>0`) | calm | `Meridian flip starting — imaging will pause briefly.` |
| `ready` / no schedule / no meridian / idle | — | `null` → render nothing |

`fmtWaitApprox(s)`: `<60s` → `under a minute`; `<3600s` → `${round(s/60)} min`; else `${h} hr ${m} min` (drops `min` when `0`). All plain-language; TZ-independent (the `HH:MM` clock comes from `fmtTime`, which is browser-local by design — matching every other Monitor clock).

### 1.5 Placement & prominence on Monitor

Render the banner **inside the sticky header** (`MonitorView.tsx` `<header>` :315-382), as a new full-width row **immediately after** the target/finish flex block (:316-349) and **before** the controls row (:355). Rationale:

- The header is `sticky top-0 z-10`, so the sentence stays pinned and directly contextualizes the **RUNNING** `StateBadge` two lines above it — resolving the exact "it says RUNNING but nothing's happening" confusion at the source.
- Above the fold on cold load (the reassurance moment), and it does not fight the grid layout below.

Styling (calm vs warn), `aria-live="polite"` so screen-readers announce the wait without stealing focus:

```tsx
{waitStatus && (
  <div
    className={`mt-2 flex items-start gap-2 text-sm leading-snug ${
      waitStatus.tone === "warn" ? "text-warn" : "text-ink"
    }`}
    aria-live="polite"
  >
    <Icon
      name={waitStatus.tone === "warn" ? "alert" : "clock"}
      size={16}
      className={`shrink-0 mt-0.5 ${waitStatus.tone === "warn" ? "text-warn" : "text-accent"}`}
    />
    <span>{waitStatus.text}</span>
  </div>
)}
```

`text-sm` (larger than the `text-[11px]` `ScheduleChip`) + accent clock icon reads as prominent-but-calm. `clock`/`alert`/`info` icons all exist (icons.tsx:17).

**Coexistence with the health/telemetry-stale row.** The `HealthStrip` (:307) is the *fault* verdict (safety, disk, link-down, telemetry-stale — deriveHealthIssues :284-301); it renders **above** the grid. The waiting banner is a *non-fault* narrative and lives in the header **below** it. No overlap: `window_closed`/`never_rises` are **not** in `deriveHealthIssues`, so warn-tone waits never double-signal against the strip. The pre-existing below-horizon chip (:621-625) is a mount-alt fact, orthogonal to the schedule gate.

**Coexistence with the Meridian tile.** `MeridianCountdown` (:513) shows the numeric ring whenever `status.meridian` is counting (any ttf). The banner's meridian sentence appears only within `plan.meridian_flip_warn_min` (engine-gated). Near a flip both show — the ring is the dial, the sentence is the plain-language "why it's about to pause". Complementary, kept. (See Open Decision O3.)

### 1.6 SequenceView refactor (DRY)

`ScheduleChip` (SequenceView.tsx:56-72) is rewritten to delegate to `formatScheduleStatus`, passing **`undefined` for `live`** so its behavior is byte-for-byte preserved (SequenceView has never shown a meridian note; the run panel's meridian lives elsewhere):

```tsx
function ScheduleChip({ schedule }: { schedule: NonNullable<SequenceState["schedule"]> }) {
  const status = formatScheduleStatus(schedule, undefined, Date.now() / 1000);
  if (!status) return null;                        // `ready` → nothing, as today
  const warn = status.tone === "warn";
  return (
    <span className={`text-[11px] inline-flex items-center gap-1 ${warn ? "text-warn" : "text-dim"}`}>
      <Icon name={warn ? "alert" : "clock"} size={13} /> {status.text}
    </span>
  );
}
```

- `waiting` → dim clock chip (unchanged tone/icon); `window_closed`/`never_rises` → warn alert chip (unchanged); `ready` → `null` (unchanged).
- **Intended copy change:** the chip text upgrades from `Waiting — {reason} · starts {HH:MM}` to the unified plain-language sentence (adds the "about N min" tail, drops the raw reason). This is the point of the unification.
- **Remove the now-unused `fmtTime` import** (SequenceView.tsx:18) — it was used only here (:60); leaving it trips `tsc` under `noUnusedLocals`.
- The two render sites (:426-430, :584-586) and their `{sequence.schedule && …}` / `{… && running && sequence.schedule && …}` guards are **unchanged**, so no meridian chip appears where there was none.

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real coordinates **37.348110 / 121.801704** and the label **"My Backyard"** must NEVER appear in code, tests, or docs. The site default is **"My Observatory" / 0.0**. This feature reads only `schedule`/`live` (no coordinates); tests use synthetic epoch integers and generic reason strings only.
- **Never `git add -A`.** Stage only the files this plan names.
- **CI gate:** `cd ui && npx tsc -b` must pass. This is the type-check gate for every task.
- **No jsdom / DOM harness.** Pure logic is tested via `npx tsx` inline-assert scripts (idioms: `ui/src/components/ui/__tests__/SegmentedControl.test.tsx`, `ui/src/components/__tests__/healthStrip.test.ts`, `ui/src/lib/__tests__/eta.test.ts`). The formatter is a pure function with a real `tsx` test; the `MonitorView` wiring is a thin render verified only by `tsc -b` (no DOM test).
- **Client toasts** (if ever needed) go through `useStore.getState().enqueueToast` — not applicable here (this feature renders inline text, no toast).
- **Do not disrupt astrotown.** UI-only change; no backend, no deploy.

---

## 3. TDD Plan

### Task 1 — Pure shared formatter `formatScheduleStatus` + tsx test

**Impl tier:** Sonnet (pure string/number logic, fully specified, real test).

**Files**
- **Create** `ui/src/lib/scheduleStatus.ts`
- **Create** `ui/src/lib/__tests__/scheduleStatus.test.ts`

**Interfaces**
```ts
export type ScheduleTone = "calm" | "warn";
export interface ScheduleStatus { tone: ScheduleTone; text: string; }
export function formatScheduleStatus(
  schedule: SequenceState["schedule"] | undefined | null,
  live:     SequenceState["live"]     | undefined | null,
  nowUnix:  number,
): ScheduleStatus | null;
export function fmtWaitApprox(seconds: number): string;   // exported for the test
export function parseGateDeg(reason: string): number | null; // exported for the test
```

**Steps**

1. Write `ui/src/lib/scheduleStatus.ts` (RED — the test imports it and fails to resolve until this exists):

```ts
// scheduleStatus.ts — pure plain-language copy for the Monitor/Sequence "why
// nothing is happening" line (NOV-8). Turns the engine's sequence.schedule /
// sequence.live blocks into ONE calm sentence, shared by MonitorView (prominent
// header banner) and SequenceView (ScheduleChip). No React, no DOM — unit-tested
// via `npx tsx`. The server `reason` strings are terse (schedule.py gating_status:
// "below start altitude (30 deg)"); novice copy is composed HERE off `state` +
// the numeric fields, extracting the gate degrees from `reason` when present.

import type { SequenceState } from "../types";
import { fmtTime } from "./visibility";

export type ScheduleTone = "calm" | "warn";
export interface ScheduleStatus {
  tone: ScheduleTone;
  text: string;
}

/** Plain-language coarse wait: "under a minute" / "47 min" / "1 hr 12 min".
 *  (fmtDuration would emit "47m 0s" — wrong for a calm, novice-facing sentence.) */
export function fmtWaitApprox(seconds: number): string {
  const t = Math.max(0, Math.round(seconds));
  if (t < 60) return "under a minute";
  if (t < 3600) return `${Math.round(t / 60)} min`;
  const h = Math.floor(t / 3600);
  const m = Math.round((t % 3600) / 60);
  return m > 0 ? `${h} hr ${m} min` : `${h} hr`;
}

/** Extract the start-gate degrees from a server reason string, or null.
 *  "below start altitude (30 deg)" -> 30; "never rises above 30 deg" -> 30. */
export function parseGateDeg(reason: string): number | null {
  const m = /(\d+(?:\.\d+)?)\s*deg/.exec(reason);
  return m ? Number(m[1]) : null;
}

function waitingText(
  schedule: NonNullable<SequenceState["schedule"]>,
  nowUnix: number,
): string {
  const { reason, eta_s, start_ts } = schedule;
  // Resolve the countdown + absolute clock. Clock case (window not open yet):
  // start_ts is in the future -> a LIVE remaining that ticks down. Altitude case
  // (engine.py case 4): start_ts already passed -> fall back to eta_s + now.
  let remaining: number;
  let clockUnix: number | null;
  if (start_ts != null && start_ts > nowUnix) {
    remaining = start_ts - nowUnix;
    clockUnix = start_ts;
  } else if (eta_s > 0) {
    remaining = eta_s;
    clockUnix = nowUnix + eta_s;
  } else {
    remaining = 0;
    clockUnix = null;
  }

  const gate = parseGateDeg(reason);
  const isAltitude = /altitude/i.test(reason) || (start_ts != null && start_ts <= nowUnix);
  let lead: string;
  if (isAltitude) {
    lead = gate != null
      ? `Waiting for your target to rise above ${gate}°`
      : `Waiting for your target to rise high enough`;
  } else {
    lead = `Waiting for the observing window to open`;
  }

  if (clockUnix != null) {
    return `${lead} at ${fmtTime(clockUnix)}, about ${fmtWaitApprox(remaining)}.`;
  }
  if (remaining > 0) {
    return `${lead}, about ${fmtWaitApprox(remaining)}.`;
  }
  return `${lead}.`;
}

export function formatScheduleStatus(
  schedule: SequenceState["schedule"] | undefined | null,
  live: SequenceState["live"] | undefined | null,
  nowUnix: number,
): ScheduleStatus | null {
  // Schedule states dominate — they explain why imaging isn't running at all.
  if (schedule) {
    switch (schedule.state) {
      case "window_closed":
        return { tone: "warn", text: "Tonight's observing window has closed for this target." };
      case "never_rises": {
        const gate = parseGateDeg(schedule.reason);
        return {
          tone: "warn",
          text: gate != null
            ? `This target never rises above ${gate}° from your site tonight.`
            : `This target never rises high enough from your site tonight.`,
        };
      }
      case "waiting":
        return { tone: "calm", text: waitingText(schedule, nowUnix) };
      case "ready":
        break; // fall through to the meridian heads-up
    }
  }

  // Meridian flip heads-up — only mid-imaging (schedule ready/absent). The engine
  // attaches meridian_eta_s ONLY within plan.meridian_flip_warn_min and always
  // > 0 (engine.py _live_block), so no client lead gate is needed.
  const m = live?.meridian_eta_s;
  if (m != null && Number.isFinite(m)) {
    return {
      tone: "calm",
      text: m > 0
        ? `Meridian flip in ${fmtWaitApprox(m)} — imaging will pause briefly.`
        : `Meridian flip starting — imaging will pause briefly.`,
    };
  }

  return null;
}
```

2. Write `ui/src/lib/__tests__/scheduleStatus.test.ts` (same inline-assert harness as `eta.test.ts`; TZ-independent — the `HH:MM` clock is asserted with a `\d{2}:\d{2}` regex, never a fixed string):

```ts
// Unit tests for the shared "why nothing is happening" formatter (NOV-8).
// No vitest/jsdom in this UI — run directly:
//   npx tsx src/lib/__tests__/scheduleStatus.test.ts
// Also compiled by `tsc -b`.
import {
  formatScheduleStatus,
  fmtWaitApprox,
  parseGateDeg,
} from "../scheduleStatus";
import type { SequenceState } from "../../types";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function match(s: string, re: RegExp, msg = ""): void {
  if (!re.test(s)) throw new Error(`${msg} — ${JSON.stringify(s)} did not match ${re}`);
}
const NOW = 1_000_000; // arbitrary unix-seconds anchor

// ---- fmtWaitApprox ----
test("fmtWaitApprox: sub-minute / minutes / hours", () => {
  eq(fmtWaitApprox(45), "under a minute");
  eq(fmtWaitApprox(2820), "47 min");     // 47*60
  eq(fmtWaitApprox(3600), "1 hr");
  eq(fmtWaitApprox(4320), "1 hr 12 min"); // 72 min
  eq(fmtWaitApprox(-5), "under a minute");
});

// ---- parseGateDeg ----
test("parseGateDeg: pulls degrees from terse server reasons", () => {
  eq(parseGateDeg("below start altitude (30 deg)"), 30);
  eq(parseGateDeg("never rises above 30 deg tonight"), 30);
  eq(parseGateDeg("waiting for start time"), null);
});

// ---- waiting: altitude (start_ts already past) ----
test("waiting/altitude → plain sentence with gate + live countdown", () => {
  const sched: SequenceState["schedule"] = {
    state: "waiting", reason: "below start altitude (30 deg)",
    eta_s: 2820, start_ts: NOW - 100,
  };
  const r = formatScheduleStatus(sched, undefined, NOW)!;
  eq(r.tone, "calm", "tone");
  match(r.text, /^Waiting for your target to rise above 30° at \d{2}:\d{2}, about 47 min\.$/, "altitude copy");
});

// ---- waiting: clock (window not open yet) ----
test("waiting/clock → 'observing window to open' with start_ts clock", () => {
  const sched: SequenceState["schedule"] = {
    state: "waiting", reason: "waiting for start time",
    eta_s: 2820, start_ts: NOW + 2820,
  };
  const r = formatScheduleStatus(sched, undefined, NOW)!;
  eq(r.tone, "calm");
  match(r.text, /^Waiting for the observing window to open at \d{2}:\d{2}, about 47 min\.$/, "clock copy");
});

// ---- window_closed / never_rises = warn ----
test("window_closed → warn", () => {
  const r = formatScheduleStatus(
    { state: "window_closed", reason: "observing window has closed", eta_s: 0 },
    undefined, NOW)!;
  eq(r.tone, "warn");
  assert(r.text.includes("window has closed"), "closed copy");
});
test("never_rises → warn, gate woven in", () => {
  const r = formatScheduleStatus(
    { state: "never_rises", reason: "never rises above 30 deg tonight", eta_s: 0 },
    undefined, NOW)!;
  eq(r.tone, "warn");
  eq(r.text, "This target never rises above 30° from your site tonight.");
});

// ---- ready / absent / idle → null ----
test("ready → null (nothing to say)", () => {
  eq(formatScheduleStatus({ state: "ready", reason: "", eta_s: 0 }, undefined, NOW), null);
});
test("no schedule + no live → null", () => {
  eq(formatScheduleStatus(undefined, undefined, NOW), null);
  eq(formatScheduleStatus(null, null, NOW), null);
});

// ---- meridian heads-up (schedule ready/absent) ----
test("meridian eta → calm 'imaging will pause briefly'", () => {
  const r = formatScheduleStatus(undefined, { meridian_eta_s: 1080 }, NOW)!;
  eq(r.tone, "calm");
  eq(r.text, "Meridian flip in 18 min — imaging will pause briefly.");
});
test("meridian eta <= 0 → 'starting' (defensive)", () => {
  const r = formatScheduleStatus({ state: "ready", reason: "", eta_s: 0 }, { meridian_eta_s: 0 }, NOW)!;
  eq(r.text, "Meridian flip starting — imaging will pause briefly.");
});

// ---- precedence: schedule waiting beats meridian ----
test("waiting schedule wins over a concurrent meridian eta", () => {
  const r = formatScheduleStatus(
    { state: "waiting", reason: "below start altitude (30 deg)", eta_s: 2820, start_ts: NOW - 1 },
    { meridian_eta_s: 600 }, NOW)!;
  match(r.text, /^Waiting for your target to rise above 30°/, "schedule takes precedence");
});

const total = passed + failed;
console.log(`scheduleStatus.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
export const result = { passed, failed, total };
```

3. **Run the test (GREEN):**
```
cd ui && npx tsx src/lib/__tests__/scheduleStatus.test.ts
```
Expected output (last line): `scheduleStatus.test: 11/11 passed` and exit 0.

4. **Type-check gate:**
```
cd ui && npx tsc -b
```
Expected: no output, exit 0.

---

### Task 2 — Wire the Monitor header banner + refactor SequenceView onto the shared formatter

**Impl tier:** Sonnet (thin render + a mechanical refactor; verified by `tsc -b`, no DOM test — per the no-jsdom constraint).

**Files**
- **Modify** `ui/src/views/MonitorView.tsx`
- **Modify** `ui/src/views/SequenceView.tsx`
- (No new test file — pure logic is already covered by Task 1; these are render-only edits gated by `tsc -b`.)

**Interfaces** (consumed, not defined): `formatScheduleStatus(schedule, live, nowUnix) => ScheduleStatus | null` from `../lib/scheduleStatus`.

**Steps**

1. **MonitorView** — add the import:
```tsx
import { formatScheduleStatus } from "../lib/scheduleStatus";
```
2. **MonitorView** — derive the status near the other derived-state block (after `const idle = state === "idle";`, ~:216). `now` is the ms coarse tick (:129):
```tsx
const waitStatus = formatScheduleStatus(seq.schedule, seq.live, now / 1000);
```
3. **MonitorView** — render the banner inside `<header>`, immediately after the target/finish flex `</div>` (closes at :349) and before the `{runActive && (` controls block (:355):
```tsx
{waitStatus && (
  <div
    className={`mt-2 flex items-start gap-2 text-sm leading-snug ${
      waitStatus.tone === "warn" ? "text-warn" : "text-ink"
    }`}
    aria-live="polite"
  >
    <Icon
      name={waitStatus.tone === "warn" ? "alert" : "clock"}
      size={16}
      className={`shrink-0 mt-0.5 ${waitStatus.tone === "warn" ? "text-warn" : "text-accent"}`}
    />
    <span>{waitStatus.text}</span>
  </div>
)}
```
(`Icon` is already imported at MonitorView.tsx:38; `useSeq`'s `seq` already carries `schedule`/`live`.)

4. **SequenceView** — add the import:
```tsx
import { formatScheduleStatus } from "../lib/scheduleStatus";
```
5. **SequenceView** — replace the body of `ScheduleChip` (:56-72) with the delegating version in §1.6 (passes `undefined` for `live`, `Date.now()/1000` for now).
6. **SequenceView** — remove the now-unused `fmtTime` import (:18). Confirm no other `fmtTime` use remains in the file first:
```
cd ui && npx tsc -b
```
Expected: no output, exit 0. (If `tsc` flags `fmtTime` as unused, the import removal in step 6 resolves it; if it flags `fmtTime` as *still used*, do not remove it — but the verified grep shows :60 is the only use.)

7. **Re-run Task 1's test** to confirm no regression to the shared module:
```
cd ui && npx tsx src/lib/__tests__/scheduleStatus.test.ts
```
Expected: `scheduleStatus.test: 11/11 passed`, exit 0.

**Manual smoke (optional, not a gate):** with the sim rig, a target whose start gate/altitude is unmet shows `Waiting for your target to rise above 30° at HH:MM, about N min.` pinned under the RUNNING badge; a plan with `meridian_flip` on, within the warn lead, shows `Meridian flip in N min — imaging will pause briefly.`

---

## 4. Open decisions

- **O1 — `now` unit in the formatter.** *Recommendation:* **seconds** (`nowUnix`), matching `start_ts`/`eta_s` and `fmtTime`. Callers divide their ms clock by 1000. (Chosen above.)
- **O2 — SequenceView shows meridian too?** SequenceView passes `undefined` for `live`, so its chip is behavior-preserved (no meridian note). *Recommendation:* keep it opted-out — the run panel already has its own meridian affordances; NOV-8 targets the Monitor. Flip to passing `sequence.live` only if product wants parity.
- **O3 — Banner meridian vs the `MeridianCountdown` ring tile (redundancy).** Both can show near a flip (sentence + dial). *Recommendation:* **keep both** — complementary (the sentence explains the pause in words; the tile is the numeric countdown). If judged noisy, suppress the banner's meridian branch when `status.meridian.status === "counting"` — but that couples the formatter to `status`, so prefer leaving it.
- **O4 — 24h vs 12h clock.** `fmtTime` renders `HH:MM` (24h); the task's example showed `11:14 pm`. *Recommendation:* **24h**, for DRY + consistency with every other Monitor/Sequence clock. A 12h variant would be a separate, app-wide formatting decision.
- **O5 — Gate-degree parsing from `reason`.** The degrees live only inside the terse server string; we regex them out with graceful fallback (mirrors `humanize.ts`). *Recommendation:* keep the opportunistic parse; it degrades cleanly to "high enough" copy if the server wording ever changes. If brittleness is a concern, a follow-up could add `gate_deg` as a first-class numeric field to `gating_status()` (backend change, out of NOV-8 scope).
- **O6 — Banner placement: pinned header vs a standalone band above the grid.** *Recommendation:* **pinned header** (chosen) so it stays glued to the RUNNING badge it explains. Alternative (a `col-span-full` calm band as the first grid child) scrolls away but is even more prominent on load — swap only if user testing prefers it.

---

## 5. Supervisor rulings (2026-07-23)

Design reviewed against the code — `SequenceState.schedule`/`live` shape (types.ts:279-288) and
`fmtTime(unix)→"HH:MM"` (visibility.ts:131-137) confirmed to match the formatter exactly.
**Approved.** Rulings (all match the designer's recommendations):

1. **O1** `nowUnix` in **seconds** — approved.
2. **O2** SequenceView passes `undefined` for `live` (no meridian chip there) — approved.
3. **O3** Keep **both** the Monitor banner meridian sentence and the numeric `MeridianCountdown`
   tile — they are complementary (words vs. dial), and suppressing one would couple the pure
   formatter to `status`. Approved.
4. **O4** **24h** clock via `fmtTime` — approved (DRY with every other Monitor/Sequence clock;
   the §1.1 "11:14 pm" example is illustrative only, the shipped copy is 24h "23:14").
5. **O5** Opportunistic gate-degree parse from `reason` with graceful "high enough" fallback —
   approved. Backend `gate_deg` as a first-class numeric field is a noted future follow-up, out of NOV-8 scope.
6. **O6** Pinned-header placement — approved.

**Noted (accepted):** the SequenceView chip copy changes (unified plain sentence, drops the raw
`reason`, adds the "about N min" tail) — an intended improvement from the unification, not a regression.
