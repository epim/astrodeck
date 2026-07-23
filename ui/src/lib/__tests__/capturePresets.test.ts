// Unit tests for NOV-4's beginner capture-preset data table
// (spec docs/superpowers/specs/2026-07-23-photometry-snr-design.md §3, Task 2).
//
// Same inline-assert harness as eta.test.ts / photometry.test.ts. Run with:
//   npx tsx src/lib/__tests__/capturePresets.test.ts

import { CAPTURE_PRESETS } from "../capturePresets";
import { isExposureValueInvalid } from "../exposure";

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

// ---------------------------------------------------------------- checks
test("CAPTURE_PRESETS: ids are unique", () => {
  const ids = CAPTURE_PRESETS.map((p) => p.id);
  const unique = new Set(ids);
  assert(unique.size === ids.length, `duplicate ids in ${ids.join(", ")}`);
});
test("CAPTURE_PRESETS: exposure_s is always a valid exposure value", () => {
  for (const p of CAPTURE_PRESETS) {
    assert(!isExposureValueInvalid(p.exposure_s), `${p.id} exposure_s=${p.exposure_s} invalid`);
  }
});
test("CAPTURE_PRESETS: gain/offset/binning are finite and >= 0", () => {
  for (const p of CAPTURE_PRESETS) {
    assert(Number.isFinite(p.gain) && p.gain >= 0, `${p.id} gain`);
    assert(Number.isFinite(p.offset) && p.offset >= 0, `${p.id} offset`);
    assert(Number.isFinite(p.binning) && p.binning >= 0, `${p.id} binning`);
  }
});
test("CAPTURE_PRESETS: binning is at least 1", () => {
  for (const p of CAPTURE_PRESETS) {
    assert(p.binning >= 1, `${p.id} binning=${p.binning} must be >= 1`);
  }
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncapturePresets.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
