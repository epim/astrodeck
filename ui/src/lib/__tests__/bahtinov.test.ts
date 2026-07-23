// Unit tests for the NOV-12 Bahtinov verdict mapping (lib/bahtinov.ts).
//
// No vitest/jest is wired into this UI, so these use the same tiny inline-assert
// harness as eta.test.ts / store.test.ts. They compile under `tsc -b` and run
// directly with a TS-aware runner:  npx tsx src/lib/__tests__/bahtinov.test.ts
import { bahtinovAid } from "../bahtinov";
import type { BahtinovInfo } from "../../types";

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

// ---------------------------------------------------------------- tests
test("locked → good, PERFECT headline", () => {
  const info: BahtinovInfo = {
    valid: true, offset_px: 0.3, in_focus: true, side: null,
    direction: null, angles_deg: [115, 135, 155], tol_px: 1.5, reason: "locked",
  };
  const v = bahtinovAid(info);
  eq(v.tone, "good");
  assert(/PERFECT|locked/i.test(v.headline), "headline");
});

test("off + direction in → warn, mentions turn IN", () => {
  const info: BahtinovInfo = {
    valid: true, offset_px: 3.2, in_focus: false, side: "right",
    direction: "in", angles_deg: [], tol_px: 1.5, reason: "middle spike 3.2 px right",
  };
  const v = bahtinovAid(info);
  eq(v.tone, "warn");
  assert(/in\b/i.test(v.detail) || /IN\b/.test(v.headline), "detail/headline says IN");
});

test("far off (> 4× tol) → bad tone", () => {
  const info: BahtinovInfo = {
    valid: true, offset_px: 9.0, in_focus: false, side: "left",
    direction: "out", angles_deg: [], tol_px: 1.5, reason: "middle spike 9 px left",
  };
  eq(bahtinovAid(info).tone, "bad");
});

test("invalid → neutral guidance, no number", () => {
  const info: BahtinovInfo = {
    valid: false, offset_px: null, in_focus: false, side: null,
    direction: null, angles_deg: [], tol_px: 1.5, reason: "no bright star found",
  };
  const v = bahtinovAid(info);
  eq(v.tone, "neutral");
  assert(v.detail.length > 0, "has guidance");
});

test("null info → neutral awaiting", () => {
  eq(bahtinovAid(null).tone, "neutral");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nbahtinov.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
