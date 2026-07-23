// Unit tests for the PRO-13 tilt/aberration inspector's pure client helpers
// (ui/src/lib/tilt.ts). Same tiny inline-assert harness as eta.test.ts — no
// jsdom, no vitest/jest. Run directly with a TS-aware runner:
//   npx tsx src/lib/__tests__/tilt.test.ts

import { tiltSummary, zoneHfrRange, heatFrac } from "../tilt";
import type { TiltInfo } from "../../types";

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
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

// ---------------------------------------------------------------- fixtures
const mk = (over: Partial<TiltInfo>): TiltInfo => ({
  cols: 3,
  rows: 3,
  zones: [],
  pattern: "uniform",
  severity: 0.1,
  worst_zone: null,
  ...over,
});

// ---------------------------------------------------------------- heatFrac
test("heatFrac clamps and handles degenerate range", () => {
  near(heatFrac(3, 2, 4), 0.5, 1e-9, "mid");
  eq(heatFrac(1, 2, 4), 0, "below min clamps");
  eq(heatFrac(9, 2, 4), 1, "above max clamps");
  eq(heatFrac(3, 4, 4), 0, "min==max -> 0"); // no divide-by-zero
});

// ---------------------------------------------------------------- zoneHfrRange
test("zoneHfrRange ignores null zones, needs >=2 populated", () => {
  const z = (hfr: number | null) => ({ hfr, ecc: null, theta: null, n: hfr ? 4 : 0 });
  const r = zoneHfrRange(mk({ zones: [z(2), z(null), z(4), z(3)] }))!;
  eq(r.min, 2, "min");
  eq(r.max, 4, "max");
  assert(zoneHfrRange(mk({ zones: [z(2), z(null)] })) === null, "one populated -> null");
});

// ---------------------------------------------------------------- tiltSummary
test("tiltSummary maps each pattern to label + tone", () => {
  eq(tiltSummary(mk({ pattern: "uniform" })).tone, "good", "uniform good");
  eq(tiltSummary(mk({ pattern: "tilt" })).tone, "bad", "tilt bad");
  eq(tiltSummary(mk({ pattern: "coma" })).tone, "warn", "coma warn");
  eq(tiltSummary(mk({ pattern: "tracking" })).tone, "warn", "tracking warn");
  assert(tiltSummary(mk({ pattern: "tilt" })).label.length > 0, "has label");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntilt.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
