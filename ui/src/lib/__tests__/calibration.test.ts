// Unit tests for the one-tap calibration capture pure logic
// (calibration-capture spec §3 Task 1).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so these use the same tiny inline-assert harness as lib/__tests__/eta.test.ts /
// components/__tests__/healthStrip.test.ts. They compile under `tsc -b` and run
// directly with a TS-aware runner:  npx tsx src/lib/__tests__/calibration.test.ts

import {
  accumulateLight,
  shouldOfferDarks,
  darkPrefillFrom,
  formatLightSummary,
  type LightSnapshot,
} from "../calibration";

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

// ---------------------------------------------------------------- accumulateLight
test("accumulateLight: first frame (prev null) starts a batch at count 1", () => {
  const s = accumulateLight(null, { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10 });
  eq(s.count, 1, "count");
  eq(s.exposureS, 120, "exposureS");
  eq(s.tempC, -10, "tempC");
});

test("accumulateLight: two identical frames accumulate to count 2", () => {
  const first = accumulateLight(null, { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10 });
  const second = accumulateLight(first, { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10 });
  eq(second.count, 2, "count after second identical frame");
});

test("accumulateLight: a changed setting (gain) resets the batch to count 1", () => {
  const first = accumulateLight(null, { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10 });
  const second = accumulateLight(first, { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10 });
  const third = accumulateLight(second, { exposureS: 120, gain: 200, offset: 30, binning: 1, tempC: -10 });
  eq(third.count, 1, "changed gain resets batch");
  eq(third.gain, 200, "new gain carried");
});

test("accumulateLight: identical settings but new tempC increments count AND refreshes tempC", () => {
  const first = accumulateLight(null, { exposureS: 60, gain: 100, offset: 20, binning: 2, tempC: -5 });
  const second = accumulateLight(first, { exposureS: 60, gain: 100, offset: 20, binning: 2, tempC: -6 });
  eq(second.count, 2, "count still accumulates");
  eq(second.tempC, -6, "tempC refreshes to the latest reading");
});

// ---------------------------------------------------------------- shouldOfferDarks
test("shouldOfferDarks: null snapshot -> false", () => {
  eq(shouldOfferDarks(null), false, "null");
});
test("shouldOfferDarks: count 0 -> false", () => {
  const l: LightSnapshot = { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10, count: 0 };
  eq(shouldOfferDarks(l), false, "count 0");
});
test("shouldOfferDarks: a valid snapshot -> true", () => {
  const l: LightSnapshot = { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10, count: 1 };
  eq(shouldOfferDarks(l), true, "valid");
});
test("shouldOfferDarks: exposureS 0 -> false", () => {
  const l: LightSnapshot = { exposureS: 0, gain: 120, offset: 30, binning: 1, tempC: -10, count: 3 };
  eq(shouldOfferDarks(l), false, "zero exposure");
});

// ---------------------------------------------------------------- darkPrefillFrom
test("darkPrefillFrom: builds a Dark prefill with string fields + coolerTarget from tempC", () => {
  const l: LightSnapshot = { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10, count: 60 };
  const p = darkPrefillFrom(l);
  eq(p.frameType, "Dark", "frameType");
  eq(p.exposure, "120", "exposure string");
  eq(p.gain, "120", "gain string");
  eq(p.offset, "30", "offset string");
  eq(p.binning, "1", "binning string");
  eq(p.coolerTarget, "-10", "coolerTarget string");
});

test("darkPrefillFrom: tempC null -> coolerTarget null (leave cooler field untouched)", () => {
  const l: LightSnapshot = { exposureS: 60, gain: 100, offset: 10, binning: 1, tempC: null, count: 5 };
  const p = darkPrefillFrom(l);
  eq(p.coolerTarget, null, "coolerTarget null");
});

// ---------------------------------------------------------------- formatLightSummary
test('formatLightSummary: full snapshot -> "60 × 120s · gain 120 · -10 °C"', () => {
  const l: LightSnapshot = { exposureS: 120, gain: 120, offset: 30, binning: 1, tempC: -10, count: 60 };
  eq(formatLightSummary(l), "60 × 120s · gain 120 · -10 °C", "formatted summary");
});

test("formatLightSummary: count 0 drops the count clause", () => {
  const l: LightSnapshot = { exposureS: 30, gain: 50, offset: 5, binning: 1, tempC: -5, count: 0 };
  eq(formatLightSummary(l), "30s · gain 50 · -5 °C", "no count clause");
});

test("formatLightSummary: tempC null drops the temp clause", () => {
  const l: LightSnapshot = { exposureS: 90, gain: 80, offset: 15, binning: 2, tempC: null, count: 12 };
  eq(formatLightSummary(l), "12 × 90s · gain 80", "no temp clause");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncalibration.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
