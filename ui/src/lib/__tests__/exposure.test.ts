// Unit tests for the Capture view's manual exposure bounds check (R3-CAP-02).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so this uses the same tiny inline-assert harness as eta.test.ts / weather.test.ts.
// Run directly with a TS-aware runner: npx tsx src/lib/__tests__/exposure.test.ts

import { EXPOSURE_MAX_S, isExposureInvalid } from "../exposure";

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

// ---------------------------------------------------------------- constants
test("EXPOSURE_MAX_S is the documented 1h ceiling", () => {
  assert(EXPOSURE_MAX_S === 3600, `expected 3600, got ${EXPOSURE_MAX_S}`);
});

// ---------------------------------------------------------------- lower bound
test("zero is invalid", () => {
  assert(isExposureInvalid("0") === true, "0s must block");
});
test("negative is invalid", () => {
  assert(isExposureInvalid("-1") === true, "-1s must block");
});
test("empty string is invalid", () => {
  assert(isExposureInvalid("") === true, "empty must block");
});
test("whitespace-only string is invalid", () => {
  assert(isExposureInvalid("   ") === true, "whitespace must block");
});
test("non-numeric text is invalid (NaN)", () => {
  assert(isExposureInvalid("abc") === true, "NaN must block");
});
test("a tiny positive value is valid", () => {
  assert(isExposureInvalid("0.001") === false, "0.001s must pass");
});

// ---------------------------------------------------------------- upper bound
test("exactly at the 3600s ceiling is valid", () => {
  assert(isExposureInvalid("3600") === false, "3600s must pass (inclusive ceiling)");
});
test("just over the ceiling is invalid", () => {
  assert(isExposureInvalid("3601") === true, "3601s must block");
});
test("a wildly absurd value is invalid", () => {
  assert(isExposureInvalid("999999") === true, "999999s must block");
});
test("scientific notation that exceeds the ceiling is invalid", () => {
  // "1e10" parses to a finite, positive number — Number.isFinite alone
  // would let it through; the explicit upper-bound check is what catches it.
  assert(isExposureInvalid("1e10") === true, "1e10 must block despite being finite");
});
test("scientific notation within bounds is valid", () => {
  assert(isExposureInvalid("1e2") === false, "1e2 (=100) must pass");
});
test("Infinity is invalid", () => {
  assert(isExposureInvalid("Infinity") === true, "Infinity must block");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nexposure.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export const result = { passed, failed, total };
