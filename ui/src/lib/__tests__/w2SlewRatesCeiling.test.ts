// WP-20 / #144: the default slew-rate ladder gains a rung at the driver's
// own ceiling (`Telescope.max_rate_deg_s`), instead of topping out forever at
// the 0.5 deg/s stop that shipped before any mount could say how fast it
// actually slews.
//
// Same inline-assert harness as slewController.test.ts (no vitest wired into
// this UI). Run directly with e.g.
//     npx tsx src/lib/__tests__/w2SlewRatesCeiling.test.ts

import {
  SLEW_RATES,
  CEILING_DISTINCT_RATIO,
  slewRatesWithCeiling,
} from "../slewController";

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
    failures.push(`FAIL ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const SHIPPED_TOP = SLEW_RATES[SLEW_RATES.length - 1].rateDegS; // 0.5

// --------------------------------------------------------------- the tests

test("a driver that did not say returns SLEW_RATES unchanged, by identity", () => {
  assert(slewRatesWithCeiling(null) === SLEW_RATES,
    "null must be the identical array, not an equal-looking copy");
  assert(slewRatesWithCeiling(undefined) === SLEW_RATES, "undefined too");
});

test("a non-finite or non-positive ceiling is treated as 'did not say'", () => {
  assert(slewRatesWithCeiling(NaN) === SLEW_RATES, "NaN");
  assert(slewRatesWithCeiling(Infinity) === SLEW_RATES, "Infinity");
  assert(slewRatesWithCeiling(0) === SLEW_RATES, "0.0");
  assert(slewRatesWithCeiling(-1.44) === SLEW_RATES, "negative");
});

test("a ceiling too close to the shipped top earns no extra rung", () => {
  // Below CEILING_DISTINCT_RATIO * 0.5 -- indistinguishable from "0.5 deg/s"
  // on a thumb-sized control.
  const justAbove = SHIPPED_TOP * (CEILING_DISTINCT_RATIO - 0.01);
  const out = slewRatesWithCeiling(justAbove);
  eq(out.length, SLEW_RATES.length, "no rung should have been added");
  assert(out === SLEW_RATES, "and it is the SAME array, not a copy of it");
});

// NAMED MUTANT (WP20-M2): replacing the final `return [...SLEW_RATES, {...}]`
// with a bare `return SLEW_RATES;` (the rung is computed and then dropped on
// the floor) turns this test red:
//     FAIL the AM5N's measured ceiling (1.44 deg/s) gains exactly one rung:
//     one rung, not zero and not two expected 4, got 3
//     FAIL the label never rounds UP: ... expected 1.44 deg/s, got 0.5 deg/s
// Run from a byte backup inside the WP-20 worktree, restored byte-identical
// (sha256 compared) afterwards.
test("the AM5N's measured ceiling (1.44 deg/s) gains exactly one rung", () => {
  const out = slewRatesWithCeiling(1.44);
  eq(out.length, SLEW_RATES.length + 1, "one rung, not zero and not two");
  const top = out[out.length - 1];
  eq(top.id, "ceiling");
  eq(top.rateDegS, 1.44);
  eq(top.label, "1.44 deg/s");
  // and the three shipped stops are still there, untouched, ahead of it.
  for (let i = 0; i < SLEW_RATES.length; i++) {
    eq(out[i], SLEW_RATES[i], `stop ${i} must be exactly the shipped one`);
  }
});

test("the label never rounds UP: a believed ceiling must not overstate it", () => {
  // 1.449999 deg/s must read "1.44 deg/s", never "1.45" -- a pad that says a
  // mount is faster than it is will be believed at the worst moment (driving
  // by eye right after a reset).
  const out = slewRatesWithCeiling(1.449999);
  eq(out[out.length - 1].label, "1.44 deg/s");
});

test("a mount reporting exactly the shipped top adds no rung", () => {
  const out = slewRatesWithCeiling(SHIPPED_TOP);
  eq(out.length, SLEW_RATES.length);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw2SlewRatesCeiling.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
