// stepDial.test.ts — the arc's geometry and its commit rule. Inline-assert
// harness (no vitest); runs via `npx tsx`.
//
// This is the control you use in the dark with cold hands, so the tests care
// most about the ways it could change a value you did not mean to change.
import {
  DEAD_ZONE_PX,
  OPTION_PITCH_PX,
  commitValue,
  nudgeLabel,
  optionAt,
  stepByKey,
} from "../stepDial";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const STEPS = [1, 10, 100, 1000] as const;

// ------------------------------------------------------------- the dead zone

test("no travel selects nothing", () => {
  eq(optionAt(0, 4), null);
});

test("travel inside the dead zone selects nothing", () => {
  eq(optionAt(DEAD_ZONE_PX - 1, 4), null, "jitter must not select");
});

test("a tap leaves the value alone", () => {
  // The one that matters most: a tap is what you do by ACCIDENT, and a tap that
  // reset the step to 1 would be a silent trap.
  eq(commitValue(STEPS, 100, 0), 100);
  eq(commitValue(STEPS, 1000, DEAD_ZONE_PX - 1), 1000);
});

test("sliding back to the origin cancels", () => {
  // Negative dy = travelled DOWN past the press point. Self-cancel.
  eq(optionAt(-80, 4), null);
  eq(commitValue(STEPS, 100, -80), 100, "never mind must mean never mind");
});

// ------------------------------------------------------------------ the arc

test("just past the dead zone picks the first option", () => {
  eq(optionAt(DEAD_ZONE_PX, 4), 0);
  eq(commitValue(STEPS, 100, DEAD_ZONE_PX), 1);
});

test("each pitch of travel advances exactly one option", () => {
  eq(optionAt(DEAD_ZONE_PX + OPTION_PITCH_PX, 4), 1);
  eq(optionAt(DEAD_ZONE_PX + OPTION_PITCH_PX * 2, 4), 2);
  eq(optionAt(DEAD_ZONE_PX + OPTION_PITCH_PX * 3, 4), 3);
});

test("overshooting the top clamps to the last option", () => {
  // A long enthusiastic swipe must not wrap around to the smallest step.
  eq(optionAt(DEAD_ZONE_PX + OPTION_PITCH_PX * 99, 4), 3);
  eq(commitValue(STEPS, 1, 9999), 1000);
});

test("an empty option list can never select", () => {
  eq(optionAt(500, 0), null);
});

// -------------------------------------------------------------- the keyboard

test("arrow up goes to a bigger step", () => {
  eq(stepByKey(STEPS, 10, "ArrowUp"), 100);
  eq(stepByKey(STEPS, 10, "ArrowRight"), 100);
});

test("arrow down goes to a smaller step", () => {
  eq(stepByKey(STEPS, 100, "ArrowDown"), 10);
  eq(stepByKey(STEPS, 100, "ArrowLeft"), 10);
});

test("the ends clamp instead of wrapping", () => {
  // Wrapping from 1000 to 1 on one extra keypress is how you move a focuser a
  // thousand steps by mistake.
  eq(stepByKey(STEPS, 1000, "ArrowUp"), 1000);
  eq(stepByKey(STEPS, 1, "ArrowDown"), 1);
});

test("Home and End jump to the extremes", () => {
  eq(stepByKey(STEPS, 100, "Home"), 1);
  eq(stepByKey(STEPS, 10, "End"), 1000);
});

test("an unrelated key changes nothing", () => {
  eq(stepByKey(STEPS, 100, "a"), 100);
  eq(stepByKey(STEPS, 100, "Enter"), 100);
});

test("a value not in the list degrades to the first rather than throwing", () => {
  eq(stepByKey(STEPS, 7 as unknown as 1, "ArrowUp"), 1);
});

// ------------------------------------------------------------------- labels

test("nudge labels carry an explicit sign", () => {
  eq(nudgeLabel(100, 1), "+100");
  eq(nudgeLabel(100, -1), "-100");
  eq(nudgeLabel(-100, -1), "-100", "an already-negative magnitude must not double up");
});

console.log(`stepDial.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
