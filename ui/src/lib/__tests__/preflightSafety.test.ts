// preflightSafety.test.ts — UX review #2 regression.
//
// Preflight reported a green READY while /api/safety/state returned
// `{is_safe:false, reason:"rain detected"}`: the checklist had ten rows
// (Camera/Mount/Guiding/Cooling/Horizon/Focuser/Filters/Calibration/Exposure/
// Disk) and NO Safety row, so the one input that decides go/no-go was the one
// input the go/no-go screen omitted. The run it green-lit put two frames on disk
// under rain before the engine's 3-poll debounce paused it.
//
// buildPreflight() now derives a Safety row from `status.safety` — the same flat
// SafetyReading the engine's own gate reads, forwarded on every 2s poll — and it
// FAILS CLOSED: a stale/non-reporting monitor is UNSAFE, not "probably fine".
// PreflightModal turns any blocked row into a hard stop on Run Sequence, so these
// tests pin the row's status AND the resulting verdict.
//
//     npx tsx src/lib/__tests__/preflightSafety.test.ts

import { buildPreflight, preflightVerdict } from "../preflight";
import type { CheckItem, RigStatus, SafetyReading, SequencePlan, SiteInfo } from "../../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------------------- fixtures
const SITE_REAL: SiteInfo = { latitude: 51.5, longitude: -0.13, is_default: false, horizon_min_deg: 15 };

/** A rig that is otherwise perfectly ready, so the ONLY thing that can move the
 *  verdict in these tests is the safety row. */
function st(safety: SafetyReading | null | undefined): RigStatus {
  return {
    connected: { camera: { name: "c", kind: "camera", connected: true } },
    looping: false,
    ...(safety !== undefined ? { safety } : {}),
  } as RigStatus;
}
function plan(overrides: Partial<SequencePlan> = {}): SequencePlan {
  return {
    name: "P", targets: [], guide: false, dither_every: 0, dither_pixels: 0,
    autofocus_every: 0, cool_to: null, cool_timeout_s: 600, apply_filter_offsets: false,
    refocus_on_temp_delta_c: 0, meridian_flip: false, recover_guiding: false,
    hfr_reject_factor: 0, park_when_done: false, warm_cooler_when_done: false,
    safety_check: true, ...overrides,
  };
}
function reading(over: Partial<SafetyReading> = {}): SafetyReading {
  return { is_safe: true, reason: "", source: "sim safety", stale: false, ts: 0, ...over };
}
function row(items: CheckItem[]): CheckItem {
  const r = items.find((i) => i.id === "safety");
  if (!r) throw new Error("no safety row in the checklist");
  return r;
}
const build = (safety: SafetyReading | null | undefined, over: Partial<SequencePlan> = {}) =>
  buildPreflight(st(safety), plan(over), SITE_REAL, {});

// ====================================================================
// THE FINDING: unsafe monitor ⇒ blocked row ⇒ NOT READY.
// ====================================================================

test("#2: the checklist HAS a safety row at all", () => {
  const items = build(reading());
  assert(items.some((i) => i.id === "safety"), "safety row present");
  eq(items[0].id, "safety", "and it is the first row (the go/no-go input leads)");
});

test("#2: is_safe:false ⇒ row blocked, verdict blocked, reason stated verbatim", () => {
  const items = build(reading({ is_safe: false, reason: "rain detected" }));
  eq(row(items).status, "blocked", "row");
  assert((row(items).detail?.value ?? "").includes("rain detected"), "reason is shown");
  eq(preflightVerdict(items), "blocked", "verdict — this is what forces NOT READY");
});

test("#2: fail-closed — a stale reading is UNSAFE, not 'probably fine'", () => {
  // `stale` means the read timed out or the device dropped. The engine's gate
  // treats that as unsafe; a green READY here would be the same bug in a new hat.
  const items = build(reading({ is_safe: true, stale: true }));
  eq(row(items).status, "blocked", "stale ⇒ blocked even with is_safe true");
  eq(preflightVerdict(items), "blocked", "verdict");
});

test("#2: a safe monitor reads ok and does not block", () => {
  const items = build(reading({ is_safe: true, source: "sim safety" }));
  eq(row(items).status, "ok", "row");
  eq(row(items).word, "READY", "word carries the state, not colour alone");
  assert(preflightVerdict(items) !== "blocked", "does not block a good night");
});

// ====================================================================
// The two ways the row must NOT nag, and the escape hatch.
// ====================================================================

test("#2: no monitor connected ⇒ skipped (most rigs have none — never nag)", () => {
  eq(row(build(null)).status, "skipped", "null reading");
  eq(row(build(undefined)).status, "skipped", "field absent entirely");
  assert(preflightVerdict(build(null)) !== "blocked", "no monitor never blocks");
});

test("#2: escape hatch — turning the plan's safety gate off unblocks the run", () => {
  // A block must always be defeatable by an explicit, honest action. The engine
  // skips the gate when plan.safety_check is false, so the row must agree —
  // otherwise the UI blocks a run the backend would happily take.
  const items = build(reading({ is_safe: false, reason: "rain detected" }),
                      { safety_check: false });
  eq(row(items).status, "skipped", "row stands down");
  assert(preflightVerdict(items) !== "blocked", "run is no longer blocked");
});

test("#2: status not loaded yet ⇒ checking, never a premature green", () => {
  const items = buildPreflight(null, plan(), SITE_REAL, {});
  eq(row(items).status, "checking", "row");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\npreflightSafety.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
