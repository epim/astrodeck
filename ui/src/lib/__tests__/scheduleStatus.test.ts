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
