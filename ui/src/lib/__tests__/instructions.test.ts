// Unit tests for the conditional-sequencer pure helpers (PRO-3). Same tiny
// inline-assert harness as eta.test.ts — no jsdom, no runner: compiles under
// `tsc -b` and runs directly with:  npx tsx src/lib/__tests__/instructions.test.ts
// Each `test(...)` maps 1:1 to an `it(...)` if a real runner lands later.

import type { Instruction, PredicateKind, TriggerKind } from "../../types";
import {
  ACTION_GROUPS, THRESHOLD_SEED, actionChangePatch, defaultInstruction,
  describeInstruction, isDestructiveAction, predicateChangePatch, toCompound,
  toFlat, triggerChangePatch, validateInstruction, triggerNeedsThreshold,
  triggerNeedsTime,
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

// GN-08: a relative hfr rule reads as a multiple of the post-focus baseline,
// never as a bare number that could be misread for a pixel value.
test("an absolute hfr rule describes itself in px", () => {
  const i = { ...defaultInstruction(), trigger: "on_hfr_above" as const, threshold: 4 };
  const s = describeInstruction(i);
  assert(s.includes("4 px"), `absolute HFR must carry its px unit, got: ${s}`);
});

test("a relative hfr rule describes itself as a multiple of focus", () => {
  const i = { ...defaultInstruction(), trigger: "on_hfr_above" as const,
              threshold: 1.3, relative: true };
  const s = describeInstruction(i);
  assert(s.includes("1.3x focus"), `relative HFR must read as 1.3x focus, got: ${s}`);
  assert(!s.includes(" px"), `relative HFR must not also claim px, got: ${s}`);
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

// ------------------------------------------- trigger/predicate seeding (UX)
// Every trigger change must leave the rule VALID: the novice path used to patch
// only `trigger`, landing straight on "Threshold must be greater than 0."
const SEED_CASES: Array<[TriggerKind, Partial<Instruction>]> = [
  ["on_hfr_above", { trigger: "on_frame_rejected", threshold: 0 }],
  ["on_guide_rms_above", { trigger: "on_frame_rejected", threshold: 0 }],
  ["at_time", { trigger: "on_frame_rejected", at_time: null }],
  ["on_hfr_above", { trigger: "at_time", at_time: "23:00", threshold: 0 }],
  ["on_frame_rejected", { trigger: "on_hfr_above", threshold: 3 }],
  ["on_target_complete", { trigger: "at_time", at_time: null }],
];
for (const [next, from] of SEED_CASES) {
  test(`changing the trigger to ${next} leaves the rule valid`, () => {
    const before = { ...defaultInstruction(), ...from } as Instruction;
    const after = { ...before, ...triggerChangePatch(before, next) };
    const problems = validateInstruction(after);
    eq(problems.length, 0, `${next}: ${problems.join(" | ")}`);
  });
}

test("switching metric re-seeds the threshold; same metric keeps the typed one", () => {
  const hfr = { ...defaultInstruction(), trigger: "on_hfr_above" as const, threshold: 3.2 };
  // HFR 3.2 would be a hopeless guide-error limit — never carry it across units
  eq(triggerChangePatch(hfr, "on_guide_rms_above").threshold,
     THRESHOLD_SEED.guide_rms_above, "re-seeds across units");
  // ... but leaving to a threshold-free trigger must not clobber the value
  eq(triggerChangePatch(hfr, "on_frame_rejected").threshold, undefined,
     "no threshold patch when the trigger doesn't use one");
});

const PRED_CASES: PredicateKind[] = [
  "hfr_above", "guide_rms_above", "frame_rejected", "target_complete", "at_time",
];
for (const kind of PRED_CASES) {
  test(`compound term seeded for ${kind} is valid`, () => {
    const term = predicateChangePatch(
      { kind: "frame_rejected", threshold: 0, at_time: null }, kind);
    const problems = validateInstruction({
      ...defaultInstruction(),
      when: { op: "all", terms: [term, term] },
    });
    eq(problems.length, 0, `${kind}: ${problems.join(" | ")}`);
  });
}

// ------------------------------------------------- destructive-action model
test("destructive actions are grouped last and flagged", () => {
  for (const a of ["abort", "run_target", "skip_target"] as const) {
    assert(isDestructiveAction(a), `${a} must be destructive`);
  }
  for (const a of ["notify", "pause", "refocus", "dither"] as const) {
    assert(!isDestructiveAction(a), `${a} must not be destructive`);
  }
  const flat = ACTION_GROUPS.flatMap((g) => g.actions);
  eq(flat[flat.length - 1], "abort", "abort is the last option offered");
  eq(flat.length, 7, "every action is offered exactly once");
  assert(ACTION_GROUPS[0].actions.every((a) => !isDestructiveAction(a)),
    "the first group holds nothing destructive");
});

// ------------------------------------------------------- the RMS unit default
//
// GuideStats.is_arcsec defaults FALSE on the server: the guider reports true
// arcsec only when a guide-scope focal length is configured, and pixels
// otherwise. The client defaulted the other way (`!== false`), so a payload
// missing the flag printed pixels with a ″ after them. On a 240 mm guide scope
// that turns 0.90 px into "0.90″" — excellent-looking guiding, ~3.2″ in truth.
// Neither direction was pinned by a test, which is how the two ends drifted.

test("an ABSENT is_arcsec prints px, never arcsec", () => {
  const inst = { ...defaultInstruction(), trigger: "on_guide_rms_above" as const,
                 threshold: 0.9 };
  const withNoFlag = describeInstruction(inst, [], undefined);
  assert(withNoFlag.includes("px"),
    `unknown units must read as px, got: ${withNoFlag}`);
  assert(!withNoFlag.includes('"'),
    `unknown units must not be printed as arcsec, got: ${withNoFlag}`);
});

test("an EXPLICIT is_arcsec:true prints arcsec", () => {
  const inst = { ...defaultInstruction(), trigger: "on_guide_rms_above" as const,
                 threshold: 0.9 };
  const arc = describeInstruction(inst, [], { rmsArcsec: true });
  assert(arc.includes('"'), `arcsec must be printed when earned, got: ${arc}`);
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
