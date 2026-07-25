// Unit tests for the conditional-sequencer pure helpers (PRO-3). Same tiny
// inline-assert harness as eta.test.ts — no jsdom, no runner: compiles under
// `tsc -b` and runs directly with:  npx tsx src/lib/__tests__/instructions.test.ts
// Each `test(...)` maps 1:1 to an `it(...)` if a real runner lands later.

import type { Instruction } from "../../types";
import {
  actionChangePatch, defaultInstruction, describeInstruction, toCompound, toFlat,
  validateInstruction, triggerNeedsThreshold, triggerNeedsTime,
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

// ------------------------------------------- control-flow expansion helpers
const P = (kind: "hfr_above" | "guide_rms_above" | "at_time", threshold = 0,
           at_time: string | null = null) => ({ kind, threshold, at_time });

const VALIDATION_CASES: Array<[string, Partial<Instruction>, boolean]> = [
  ["jump without a destination is invalid",
   { action: "run_target", target_arg: null }, false],
  ["jump with a destination is valid",
   { action: "skip_target", target_arg: "M42" }, true],
  ["compound with 2 valid terms is valid",
   { when: { op: "all", terms: [P("hfr_above", 3), P("guide_rms_above", 1)] } }, true],
  ["compound with 1 term is invalid",
   { when: { op: "any", terms: [P("hfr_above", 3)] } }, false],
  ["compound with 9 terms is invalid",
   { when: { op: "any", terms: Array.from({ length: 9 }, () => P("hfr_above", 3)) } }, false],
  ["compound term needs a positive threshold",
   { when: { op: "all", terms: [P("hfr_above", 0), P("guide_rms_above", 1)] } }, false],
  ["compound at_time term needs HH:MM",
   { when: { op: "all", terms: [P("at_time", 0, "9pm"), P("hfr_above", 3)] } }, false],
  ["compound overrides an unfilled flat trigger",
   { trigger: "at_time", at_time: null,
     when: { op: "all", terms: [P("hfr_above", 3), P("guide_rms_above", 1)] } }, true],
];
for (const [name, patch, valid] of VALIDATION_CASES) {
  test(name, () => {
    const problems = validateInstruction({ ...defaultInstruction(), ...patch });
    eq(problems.length === 0, valid, `${name}: ${problems.join(" | ")}`);
  });
}

test("describe renders a compound with AND / OR and the jump destination", () => {
  const and = describeInstruction({
    ...defaultInstruction(), action: "run_target", target_arg: "M42",
    when: { op: "all", terms: [P("hfr_above", 3.5), P("guide_rms_above", 1.2)] },
  });
  assert(and.includes("AND") && and.includes("3.5") && and.includes("M42"), `got: ${and}`);
  const or = describeInstruction({
    ...defaultInstruction(),
    when: { op: "any", terms: [P("hfr_above", 3.5), P("guide_rms_above", 1.2)] },
  });
  assert(or.includes("OR"), `got: ${or}`);
});

test("compound upgrade is lossless and reversible", () => {
  const flat = { ...defaultInstruction(), trigger: "on_hfr_above" as const, threshold: 3.5 };
  const when = toCompound(flat);
  eq(when.terms.length, 2, "seeds two terms");
  eq(when.terms[0].kind, "hfr_above", "keeps the authored condition");
  eq(when.terms[0].threshold, 3.5, "keeps the authored threshold");
  eq(toFlat(when).trigger, "on_hfr_above", "round-trips back");
  eq(toFlat(when).threshold, 3.5, "round-trips the threshold");
});

test("switching to a jump action seeds a target and turns once on", () => {
  const patch = actionChangePatch(defaultInstruction(), "run_target", ["M31", "M42"]);
  eq(patch.once, true, "once defaults on for jumps");
  eq(patch.target_arg, "M31", "seeds a destination");
  const back = actionChangePatch(
    { ...defaultInstruction(), action: "run_target", target_arg: "M31" }, "notify", ["M31"]);
  eq(back.target_arg, null, "clears the destination when leaving a jump");
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
