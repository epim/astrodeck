// Pure-logic tests for the autofocus-result verdict mapping (implementation
// brief §3): excellent|good|soft|failed|pending from the engine's best HFR, fit
// R², and sweep state. Same dependency-free inline-assert harness as
// src/__tests__/store.test.ts (no vitest/jsdom wired in — these run under tsx and
// compile under `tsc -b`).
//
// Run directly:  npx tsx src/components/__tests__/autofocusVerdict.test.ts
import { autofocusLevel, type AfLevel } from "../preview/FocusVerdict";

// ------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`${name}: ${(e as Error).message}`); }
}
function eq(actual: AfLevel, expected: AfLevel, msg: string): void {
  if (actual !== expected) throw new Error(`${msg} — expected ${expected}, got ${actual}`);
}

const TH = { hfrGood: 2.0, hfrWarn: 3.5 };

// ------------------------------------------------------------ cases
test("failed state maps to failed regardless of numbers", () => {
  eq(autofocusLevel({ state: "failed", hfr: 1.2, r2: 0.999, ...TH }), "failed", "failed");
});

test("running/idle (no result) is pending", () => {
  eq(autofocusLevel({ state: "running", hfr: null, r2: null, ...TH }), "pending", "running");
  eq(autofocusLevel({ state: "done", hfr: null, r2: null, ...TH }), "pending", "done-but-no-hfr");
});

test("tight HFR + confident fit is excellent", () => {
  eq(autofocusLevel({ state: "done", hfr: 1.82, r2: 0.997, ...TH }), "excellent", "excellent");
});

test("tight HFR but weak R² is NOT excellent (drops to good)", () => {
  // hfr within good, but R² below 0.98 → excellence withheld; HFR still ≤ warn → good.
  eq(autofocusLevel({ state: "done", hfr: 1.9, r2: 0.90, ...TH }), "good", "tight-but-weak-r2");
});

test("missing R² judges on HFR alone (backend/NINA result)", () => {
  eq(autofocusLevel({ state: "done", hfr: 1.5, r2: null, ...TH }), "excellent", "no-r2-tight");
});

test("mid HFR is good", () => {
  eq(autofocusLevel({ state: "done", hfr: 3.0, r2: 0.99, ...TH }), "good", "good");
});

test("high HFR is soft", () => {
  eq(autofocusLevel({ state: "done", hfr: 4.5, r2: 0.99, ...TH }), "soft", "soft");
});

// ------------------------------------------------------------ report
if (failed) {
  console.error(`autofocusVerdict: ${passed} passed, ${failed} FAILED`);
  for (const f of failures) console.error("  ✗ " + f);
  // Signal failure to any runner without importing node:process types.
  const proc = (globalThis as unknown as { process?: { exitCode?: number } }).process;
  if (proc) proc.exitCode = 1;
} else {
  console.log(`autofocusVerdict: ${passed} passed`);
}
