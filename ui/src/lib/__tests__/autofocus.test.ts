// autofocus.test.ts — normalizeAutofocusResult (terminal-only gate, defensive
// point/fit parsing) + filterNameFromStatus + afResultAgeLabel (F5: R2-FOC-01).
// Run with:  npx tsx src/lib/__tests__/autofocus.test.ts   (from ui/)

import {
  afResultAgeLabel, filterNameFromStatus, normalizeAutofocusResult,
} from "../autofocus";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const CTX = { provider: { kind: "astrodeck", label: "AstroDeck native" }, filter: "Luminance", tsMs: 1_700_000_000_000 };

test("normalizeAutofocusResult: 'running' and garbage states yield null (terminal-only gate)", () => {
  assert(normalizeAutofocusResult({ state: "running", points: [], best: null }, CTX) === null, "running -> null");
  assert(normalizeAutofocusResult({ state: "idle" }, CTX) === null, "unknown state -> null");
  assert(normalizeAutofocusResult(null, CTX) === null, "null raw -> null");
  assert(normalizeAutofocusResult("nope", CTX) === null, "non-object raw -> null");
});

test("normalizeAutofocusResult: 'done' carries points/best/fit + snapshots provider/filter/ts", () => {
  const r = normalizeAutofocusResult({
    state: "done",
    points: [{ position: 100, hfr: 3.2 }, { position: 450, hfr: 1.8, sigma: 0.1 }],
    best: { position: 450, hfr: 1.79 },
    fit: { method: "hyperbolic", r2: 0.987, curve: [[100, 3.2], [450, 1.79]] },
  }, CTX)!;
  assert(r.state === "done", "state");
  assert(r.points.length === 2, `points.length=${r.points.length}`);
  assert(r.points[1].sigma === 0.1, "sigma carried");
  assert(r.best?.position === 450 && r.best.hfr === 1.79, "best");
  assert(r.fit?.method === "hyperbolic" && r.fit.r2 === 0.987, "fit method/r2");
  assert(r.fit?.curve?.length === 2, "fit.curve");
  assert(r.message === null, "no message -> null");
  assert(r.provider?.label === "AstroDeck native", "provider snapshot");
  assert(r.filter === "Luminance", "filter snapshot");
  assert(r.ts === CTX.tsMs, "ts stamped from ctx, not Date.now()");
});

test("normalizeAutofocusResult: 'failed' carries message, tolerates absent best/fit", () => {
  const r = normalizeAutofocusResult({
    state: "failed", points: [{ position: 1, hfr: 2 }], best: null,
    message: "no V-curve minimum found (flat or inverted fit)",
  }, CTX)!;
  assert(r.state === "failed", "state");
  assert(r.best === null, "best null");
  assert(r.fit === null, "fit absent -> null");
  assert(r.message === "no V-curve minimum found (flat or inverted fit)", "message carried");
});

test("normalizeAutofocusResult: malformed points/best entries are dropped, not fabricated", () => {
  const r = normalizeAutofocusResult({
    state: "done",
    points: [{ position: 1, hfr: 2 }, { position: "bad", hfr: 2 }, { hfr: 2 }, null, "x"],
    best: { hfr: 2 }, // missing position -> best must be null, not {position:0,...}
  }, CTX)!;
  assert(r.points.length === 1, `only the one valid point kept, got ${r.points.length}`);
  assert(r.best === null, "best with no position -> null, no fabricated 0");
});

test("normalizeAutofocusResult: fit.curve drops malformed tuples but keeps valid ones", () => {
  const r = normalizeAutofocusResult({
    state: "done", points: [], best: null,
    fit: { curve: [[1, 2], [1, 2, 3], "x", [1, "y"], [3, 4]] },
  }, CTX)!;
  assert(r.fit?.curve?.length === 2, `expected 2 valid tuples, got ${r.fit?.curve?.length}`);
});

test("normalizeAutofocusResult: no provider/filter in ctx -> both null (never fabricated)", () => {
  const r = normalizeAutofocusResult(
    { state: "done", points: [], best: null },
    { provider: null, filter: null, tsMs: 5 },
  )!;
  assert(r.provider === null, "provider null");
  assert(r.filter === null, "filter null");
});

test("filterNameFromStatus: valid index, missing wheel, out-of-range position all handled", () => {
  assert(filterNameFromStatus({ position: 1, names: ["L", "R", "G", "B"] }) === "R", "valid index");
  assert(filterNameFromStatus(null) === null, "no wheel -> null");
  assert(filterNameFromStatus(undefined) === null, "undefined wheel -> null");
  assert(filterNameFromStatus({ position: 9, names: ["L"] }) === null, "out-of-range -> null (not undefined-as-string)");
});

test("afResultAgeLabel: just-now / minutes / hours / days bands", () => {
  const now = 1_700_000_000_000;
  assert(afResultAgeLabel(now - 5_000, now) === "just now", "5s -> just now");
  assert(afResultAgeLabel(now - 3 * 60_000, now) === "3m ago", "3m");
  assert(afResultAgeLabel(now - 2 * 3600_000, now) === "2.0h ago", "2h");
  assert(afResultAgeLabel(now - 3 * 86_400_000, now) === "3d ago", "3d");
});

console.log(`autofocus.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
