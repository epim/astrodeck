// Unit tests for the conditional-sequencer pure helpers (PRO-3). Same tiny
// inline-assert harness as eta.test.ts — no jsdom, no runner: compiles under
// `tsc -b` and runs directly with:  npx tsx src/lib/__tests__/instructions.test.ts
// Each `test(...)` maps 1:1 to an `it(...)` if a real runner lands later.

import {
  defaultInstruction, describeInstruction, validateInstruction,
  triggerNeedsThreshold, triggerNeedsTime,
} from "../instructions";

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
test("default is a valid enabled notify rule", () => {
  const i = defaultInstruction();
  eq(i.enabled, true, "enabled");
  eq(validateInstruction(i).length, 0, "valid");
});
test("hfr rule needs a positive threshold", () => {
  const i = { ...defaultInstruction(), trigger: "on_hfr_above" as const, threshold: 0 };
  assert(validateInstruction(i).length > 0, "threshold required");
  assert(triggerNeedsThreshold("on_hfr_above"), "needs threshold");
});
test("at_time rule needs HH:MM", () => {
  const bad = { ...defaultInstruction(), trigger: "at_time" as const, at_time: "9pm" };
  assert(validateInstruction(bad).length > 0, "bad time");
  const ok = { ...defaultInstruction(), trigger: "at_time" as const, at_time: "23:30" };
  eq(validateInstruction(ok).length, 0, "good time");
  assert(triggerNeedsTime("at_time"), "needs time");
});
test("describe is human + mentions trigger and action", () => {
  const i = { ...defaultInstruction(), trigger: "on_hfr_above" as const,
              threshold: 3.5, action: "refocus" as const };
  const s = describeInstruction(i);
  assert(s.includes("3.5") && /refocus/i.test(s), `got: ${s}`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ninstructions.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
