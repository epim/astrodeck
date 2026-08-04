// cooling.test.ts — the Capture screen's warm-down readout (lib/cooling.ts).
//
// Why these assertions and not others: the warm ramp is a ten-minute background
// process that replaced an instantaneous (and hardware-damaging) one. The two
// ways the UI can betray that are (a) showing nothing, so the user presses Warm
// again or "fixes" it with Cool, and (b) showing a ramp that is not running —
// the server falls back to switching the cooler straight off when it cannot read
// the sensor, and a panel that renders that identically to a real ramp puts the
// product back to lying about a safe ramp.
//
// Run directly:  npx tsx src/__tests__/cooling.test.ts
import { warmReadout } from "../lib/cooling";
import type { WarmInfo } from "../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

const warm = (o: Partial<WarmInfo> = {}): WarmInfo => ({
  active: true,
  source: "user",
  ramped: true,
  delegated: false,
  start_c: -10,
  ambient_c: 20,
  ambient_from: "assumed",
  setpoint_c: -10,
  temp_c: -10,
  rate_c_per_min: 2,
  elapsed_s: 0,
  eta_s: 900,
  note: "",
  ...o,
});

// ------------------------------------------------------------------ absence
test("no warm on status renders nothing at all", () => {
  eq(warmReadout(undefined), null, "undefined");
  eq(warmReadout(null), null, "null");
});

// ----------------------------------------------------------------- progress
test("progress tracks the SETPOINT, which is the thing we control", () => {
  eq(warmReadout(warm({ setpoint_c: -10 }))!.pct, 0, "at the start");
  eq(warmReadout(warm({ setpoint_c: 5 }))!.pct, 50, "half way from -10 to 20");
  eq(warmReadout(warm({ setpoint_c: 20 }))!.pct, 100, "at ambient");
});

test("progress is clamped, never negative and never past 100", () => {
  eq(warmReadout(warm({ setpoint_c: -40 }))!.pct, 0, "below the start temp");
  eq(warmReadout(warm({ setpoint_c: 99 }))!.pct, 100, "past ambient");
});

test("a degenerate ramp (ambient == start) does not divide by zero", () => {
  const r = warmReadout(warm({ start_c: 12, ambient_c: 12, setpoint_c: 12 }))!;
  assert(Number.isFinite(r.pct), `pct was ${r.pct}`);
});

test("a backend-owned ramp measures progress on the clock instead", () => {
  // NINA runs its own ramp on a duration, so there is no setpoint of ours to
  // report. Claiming one would be inventing a number we are not the source of.
  const r = warmReadout(warm({ delegated: true, elapsed_s: 300, eta_s: 300,
                               setpoint_c: null }))!;
  eq(r.pct, 50, "half the duration elapsed");
  assert(r.detail.includes("own ramp"), `detail must name whose ramp: ${r.detail}`);
});

// ------------------------------------------------------------------ honesty
test("an assumed ambient is LABELLED assumed", () => {
  // "to 20 °C" on a 4 °C night is a guess, and the user watching the setpoint
  // climb toward it is entitled to know that before they conclude it is broken.
  const r = warmReadout(warm({ ambient_from: "assumed" }))!;
  assert(r.detail.includes("(assumed)"), r.detail);
  const m = warmReadout(warm({ ambient_c: 11, ambient_from: "measured" }))!;
  assert(m.detail.includes("(measured)"), m.detail);
});

test("a cooler switched off with NO ramp never reads like a completed ramp", () => {
  // This is the regression that matters most in the UI: the server falls back to
  // the old cut-it-dead behaviour when it cannot read the sensor, and the panel
  // must show that differently or the fallback is invisible again.
  const done = warmReadout(warm({ active: false, ramped: true, note: "warm complete" }))!;
  const cut = warmReadout(warm({
    active: false, ramped: false,
    note: "this camera cannot report its sensor temperature",
  }))!;
  eq(done.unramped, false, "a real ramp is not flagged");
  eq(cut.unramped, true, "the fallback must be flagged");
  assert(done.headline !== cut.headline,
    `both states rendered as "${done.headline}"`);
  assert(cut.headline.toLowerCase().includes("no ramp"), cut.headline);
  assert(cut.detail.includes("cannot report"), `the reason must survive: ${cut.detail}`);
});

test("a finished warm stops claiming to be in progress", () => {
  const r = warmReadout(warm({ active: false, note: "warm complete" }))!;
  eq(r.active, false, "active");
  eq(r.pct, 100, "a finished ramp is full");
  assert(!r.headline.startsWith("Warming"), r.headline);
});

test("a stopped warm does not say it completed", () => {
  // The one sentence a cancel must never produce.
  const r = warmReadout(warm({ active: false, note: "stopped: cooling was requested" }))!;
  assert(r.detail.startsWith("stopped:"), r.detail);
});

// ------------------------------------------------------------------ the ETA
test("the ETA is human, and says so when there is none left", () => {
  const r = warmReadout(warm({ eta_s: 610 }))!;
  assert(r.headline.includes("10m"), `expected minutes in "${r.headline}"`);
  const end = warmReadout(warm({ eta_s: 0 }))!;
  assert(end.headline.includes("finishing"), end.headline);
});

test("the numbers a user needs are all on the detail line", () => {
  const r = warmReadout(warm({ setpoint_c: -3.25, temp_c: -3.4 }))!;
  assert(r.detail.includes("-3.3 °C") || r.detail.includes("−3.3 °C"),
    `setpoint missing: ${r.detail}`);
  assert(r.detail.includes("sensor"), `sensor missing: ${r.detail}`);
  assert(r.detail.includes("2 °C/min"), `rate missing: ${r.detail}`);
});

// ------------------------------------------------------------------ report
console.log(`\ncooling: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) throw new Error(`${failed} test(s) failed`);
