// exposureBias.test.ts — the frame-type-aware exposure rule.
// Inline assert harness like catalogHint.test.ts.
// Run: npx tsx src/lib/__tests__/exposureBias.test.ts
//
// Round-4 review deferral #7. A BIAS frame is the shortest read the sensor can
// do, so 0 is its CORRECT exposure, not a mistake — calibrationLibrary already
// drops the exposure from a bias master's summary for the same reason. The
// plain bounds check (`n <= 0` invalid) rejected it, which blocked the whole
// RUN on a legitimate calibration plan and surfaced only as a 422 at the end.
// Every other frame type still needs a real exposure: a 0s dark or flat IS a
// mistake, and quietly allowing one would swap a false block for a false pass.
import { isStepExposureInvalid, isExposureValueInvalid, EXPOSURE_MAX_S } from "../exposure";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void) {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string) { if (!c) throw new Error(m); }

test("a 0s Bias step is valid", () => {
  assert(!isStepExposureInvalid(0, "Bias"), "0s Bias must be allowed");
});

test("0s stays invalid for every other frame type, including an unset one", () => {
  for (const ft of ["Light", "Dark", "Flat", undefined]) {
    assert(isStepExposureInvalid(0, ft as string | undefined),
      `0s must stay invalid for ${ft ?? "(unset, defaults to Light)"}`);
  }
});

test("the shared bounds still apply to Bias", () => {
  assert(isStepExposureInvalid(-1, "Bias"), "negative is invalid even for Bias");
  assert(isStepExposureInvalid(EXPOSURE_MAX_S + 1, "Bias"), "over max is invalid for Bias");
  assert(isStepExposureInvalid(Number.NaN, "Bias"), "NaN is invalid for Bias");
  assert(!isStepExposureInvalid(3, "Bias"), "a real short Bias exposure is fine");
});

test("normal exposures are unaffected", () => {
  assert(!isStepExposureInvalid(300, "Light"), "a 300s light is fine");
  assert(!isStepExposureInvalid(120, "Flat"), "a 120s flat is fine");
  assert(!isStepExposureInvalid(EXPOSURE_MAX_S, "Dark"), "a max-length dark is fine");
});

test("the bare numeric check is unchanged, so other callers keep their old rule", () => {
  assert(isExposureValueInvalid(0), "the frame-type-agnostic check still rejects 0");
  assert(!isExposureValueInvalid(1), "and still accepts 1");
});

const total = passed + failed;
console.log(`\nexposureBias.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
