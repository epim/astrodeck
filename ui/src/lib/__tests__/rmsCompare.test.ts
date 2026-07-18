// rmsCompare.test.ts — pure tests for lib/rmsCompare.ts's compareRmsWindows,
// the same-night head-to-head RMS comparison GuideView's provider-switch
// panel renders (P5-T1, spec §6 P5). Copies the apiError.test.ts idiom
// exactly (see lib/__tests__/apiError.test.ts): a tiny in-file test()/assert()
// harness, no test runner dependency, run directly under tsx.
//
// Imports directly from lib/rmsCompare.ts — the module has no React import and
// no lib/base.ts (window.location) dependency, so it runs under plain Node/tsx
// (see repo verify command: `npx tsx src/lib/__tests__/*.test.ts`).
import {
  compareRmsWindows,
  MIN_WINDOW_SAMPLES,
  COMPARABLE_THRESHOLD_PCT,
  type RmsWindow,
} from "../rmsCompare";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const native = (rmsTotal: number, samples = 40): RmsWindow =>
  ({ label: "AstroDeck native", rmsTotal, samples });
const phd2 = (rmsTotal: number, samples = 40): RmsWindow =>
  ({ label: "PHD2", rmsTotal, samples });

// (a) a clear winner each direction.
test("native tighter RMS reads native better", () => {
  const r = compareRmsWindows(native(0.4), phd2(0.8));
  assert(r.verdict === "a-better", `verdict: ${r.verdict}`);
  assert(r.message.includes("AstroDeck native") && r.message.includes("better"),
    `message: ${r.message}`);
  assert(r.deltaPct !== null && r.deltaPct > 90 && r.deltaPct < 110, `deltaPct: ${r.deltaPct}`);
});

test("PHD2 tighter RMS reads PHD2 better", () => {
  const r = compareRmsWindows(native(0.9), phd2(0.5));
  assert(r.verdict === "b-better", `verdict: ${r.verdict}`);
  assert(r.message.includes("PHD2") && r.message.includes("better"), `message: ${r.message}`);
  assert(r.deltaPct !== null && r.deltaPct > 70 && r.deltaPct < 90, `deltaPct: ${r.deltaPct}`);
});

// (b) within the comparable threshold either direction.
test("windows within the comparable threshold read comparable", () => {
  const r = compareRmsWindows(native(0.50), phd2(0.53));
  assert(r.verdict === "comparable", `verdict: ${r.verdict}`);
  assert(r.message.includes("comparable"), `message: ${r.message}`);
});

test("comparable is symmetric regardless of argument order", () => {
  const r1 = compareRmsWindows(native(0.50), phd2(0.53));
  const r2 = compareRmsWindows(phd2(0.53), native(0.50));
  assert(r1.verdict === "comparable" && r2.verdict === "comparable",
    `verdicts: ${r1.verdict}, ${r2.verdict}`);
});

// (c) exact threshold boundary — inclusive (<=), per the documented rule.
// Integer rmsTotal values (100 vs 100+COMPARABLE_THRESHOLD_PCT) keep the
// percentage computation exact in floating point, so the boundary lands
// precisely on COMPARABLE_THRESHOLD_PCT with no float-drift flakiness.
test("exactly COMPARABLE_THRESHOLD_PCT apart is still comparable (inclusive boundary)", () => {
  const lo = 100;
  const hi = lo + COMPARABLE_THRESHOLD_PCT; // exactly +COMPARABLE_THRESHOLD_PCT%
  const r = compareRmsWindows(native(lo), phd2(hi));
  assert(r.verdict === "comparable", `verdict: ${r.verdict} (lo=${lo}, hi=${hi})`);
  assert(r.deltaPct === COMPARABLE_THRESHOLD_PCT, `deltaPct: ${r.deltaPct}`);
});

test("just past COMPARABLE_THRESHOLD_PCT crowns a winner", () => {
  const lo = 100;
  const hi = lo + COMPARABLE_THRESHOLD_PCT + 1; // one point past the threshold
  const r = compareRmsWindows(native(lo), phd2(hi));
  assert(r.verdict === "a-better", `verdict: ${r.verdict} (lo=${lo}, hi=${hi})`);
});

// (d) both windows converged to ~0 RMS — no divide-by-zero winner-crowning.
test("both windows at zero RMS read comparable, not a crash", () => {
  const r = compareRmsWindows(native(0), phd2(0));
  assert(r.verdict === "comparable", `verdict: ${r.verdict}`);
  assert(r.deltaPct === 0, `deltaPct: ${r.deltaPct}`);
});

// (e) insufficient / empty windows.
test("a missing window (undefined) is insufficient data", () => {
  const r = compareRmsWindows(undefined, phd2(0.5));
  assert(r.verdict === "insufficient-data", `verdict: ${r.verdict}`);
  assert(r.message.includes("insufficient data"), `message: ${r.message}`);
  assert(r.deltaPct === null, `deltaPct: ${r.deltaPct}`);
});

test("a null window is insufficient data", () => {
  const r = compareRmsWindows(native(0.5), null);
  assert(r.verdict === "insufficient-data", `verdict: ${r.verdict}`);
});

test("both windows missing is insufficient data", () => {
  const r = compareRmsWindows(undefined, undefined);
  assert(r.verdict === "insufficient-data", `verdict: ${r.verdict}`);
});

test("an empty window (zero samples) is insufficient data", () => {
  const r = compareRmsWindows(native(0.4, 0), phd2(0.5, 40));
  assert(r.verdict === "insufficient-data", `verdict: ${r.verdict}`);
});

test("a window just under MIN_WINDOW_SAMPLES is insufficient data", () => {
  const r = compareRmsWindows(native(0.4, MIN_WINDOW_SAMPLES - 1), phd2(0.5, 40));
  assert(r.verdict === "insufficient-data", `verdict: ${r.verdict}`);
});

test("a window exactly at MIN_WINDOW_SAMPLES is usable (inclusive boundary)", () => {
  const r = compareRmsWindows(native(0.4, MIN_WINDOW_SAMPLES), phd2(0.8, MIN_WINDOW_SAMPLES));
  assert(r.verdict !== "insufficient-data", `verdict: ${r.verdict}`);
});

test("a negative or NaN rmsTotal is insufficient data (defensive, never crash)", () => {
  const r1 = compareRmsWindows(native(-1), phd2(0.5));
  assert(r1.verdict === "insufficient-data", `verdict: ${r1.verdict}`);
  const r2 = compareRmsWindows(native(NaN), phd2(0.5));
  assert(r2.verdict === "insufficient-data", `verdict: ${r2.verdict}`);
});

console.log(`rmsCompare.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
