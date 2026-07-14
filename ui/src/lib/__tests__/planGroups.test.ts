// planGroups.test.ts — pure tests for lib/planGroups.ts (mosaic "apply to all
// panels" helper). Inline-assert harness (no vitest); runs via `npx tsx`.
import { applyStepsToGroup } from "../planGroups";
import type { ExposureStep, Target } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const step = (over: Partial<ExposureStep> = {}): ExposureStep => ({
  filter: "Ha", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 20,
  frame_type: "Light", ...over,
});

const target = (over: Partial<Target> = {}): Target => ({
  name: "M31 1-1", ra_hours: 0, dec_deg: 0, center: true, autofocus_first: true,
  calibration: false, steps: [step()], ...over,
});

// --- 1. members get the steps; a non-member and a mosaic_group:undefined
//     target are untouched AND reference-equal.
test("applies to group members; leaves non-members reference-equal", () => {
  const source = [step({ filter: "Ha" }), step({ filter: "OIII" })];
  const p1 = target({ name: "M31 1-1", mosaic_group: "M31", steps: [step({ filter: "old" })] });
  const p2 = target({ name: "M31 1-2", mosaic_group: "M31", steps: [] });
  const other = target({ name: "M13", mosaic_group: "M13", steps: [step({ filter: "R" })] });
  const ungrouped = target({ name: "Solo", mosaic_group: undefined, steps: [step({ filter: "L" })] });
  const targets = [p1, p2, other, ungrouped];

  const out = applyStepsToGroup(targets, "M31", source);

  assert(out.length === 4, "same length");
  assert(out[0].steps.length === 2, "p1 got the 2 source steps");
  assert(out[0].steps[0].filter === "Ha" && out[0].steps[1].filter === "OIII", "p1 step contents match source");
  assert(out[1].steps.length === 2, "p2 got the 2 source steps");
  assert(out[1].steps[0].filter === "Ha" && out[1].steps[1].filter === "OIII", "p2 step contents match source");
  // non-members untouched AND reference-equal (React re-render scoping)
  assert(out[2] === other, "M13 target is reference-equal (untouched)");
  assert(out[3] === ungrouped, "ungrouped (mosaic_group undefined) target is reference-equal (untouched)");
});

// --- 2. deep clone: mutating one member's step after apply does not affect
//     another member or the source array.
test("deep clone: no shared step objects between members or with source", () => {
  const source = [step({ filter: "Ha" })];
  const p1 = target({ name: "A", mosaic_group: "G", steps: [] });
  const p2 = target({ name: "B", mosaic_group: "G", steps: [] });
  const out = applyStepsToGroup([p1, p2], "G", source);

  assert(out[0].steps[0] !== out[1].steps[0], "p1 and p2 step objects are distinct");
  assert(out[0].steps[0] !== source[0], "p1 step object is distinct from source");
  assert(out[0].steps !== out[1].steps, "p1 and p2 steps arrays are distinct");

  out[0].steps[0].filter = "MUTATED";
  out[0].steps[0].count = 999;
  assert(out[1].steps[0].filter === "Ha", "mutating p1's step did not affect p2's step");
  assert(out[1].steps[0].count === 20, "mutating p1's step count did not affect p2's step count");
  assert(source[0].filter === "Ha", "mutating p1's step did not affect the source array");
  assert(source[0].count === 20, "mutating p1's step did not affect the source array's count");
});

// --- 3. member count 1 (only the source): still returns a valid array (the
//     helper does not care about group size — the UI hides the button).
test("group of 1 (only the source target): still returns a valid array", () => {
  const source = [step({ filter: "Ha" }), step({ filter: "OIII" })];
  const p1 = target({ name: "Solo group member", mosaic_group: "G", steps: source });
  const out = applyStepsToGroup([p1], "G", source);

  assert(out.length === 1, "one target back");
  assert(out[0].steps.length === 2, "member got the source steps (cloned)");
  assert(out[0].steps[0] !== source[0], "cloned, not the same reference, even for the source's own target");
});

// --- 4. empty sourceSteps: members end up with [] (helper is total; UI may
//     still allow it — an empty list is a legitimate "clear").
test("empty sourceSteps clears members to []", () => {
  const p1 = target({ name: "A", mosaic_group: "G", steps: [step(), step()] });
  const p2 = target({ name: "B", mosaic_group: "G", steps: [step()] });
  const out = applyStepsToGroup([p1, p2], "G", []);

  assert(Array.isArray(out[0].steps) && out[0].steps.length === 0, "p1 cleared to []");
  assert(Array.isArray(out[1].steps) && out[1].steps.length === 0, "p2 cleared to []");
});

console.log(`planGroups.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
