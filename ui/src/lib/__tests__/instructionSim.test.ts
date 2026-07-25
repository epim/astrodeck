// Unit tests for the client-side dry-run simulator (control-flow expansion).
// Same tiny inline-assert harness as instructions.test.ts — no jsdom, no runner:
// compiles under `tsc -b` and runs directly with:
//   npx tsx src/lib/__tests__/instructionSim.test.ts
import type { Condition, Instruction } from "../../types";
import { defaultInstruction } from "../instructions";
import {
  defaultSnapshot, simulateInstructions, type SimGate, type SimSnapshot,
} from "../instructionSim";

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

// ---------------------------------------------------------------- fixtures
const SNAP: SimSnapshot = {
  hfr: 4, guideRms: 0.5, frameRejected: false, targetComplete: false,
  now: "23:00", activeTarget: "M31",
};
const hfrAbove = (n: number): Condition["terms"][number] =>
  ({ kind: "hfr_above", threshold: n, at_time: null });
const rmsAbove = (n: number): Condition["terms"][number] =>
  ({ kind: "guide_rms_above", threshold: n, at_time: null });

function rule(patch: Partial<Instruction>): Instruction {
  return { ...defaultInstruction(), id: "r1", ...patch };
}

// ---------------------------------------------------------------- tests
// ONE parametrized table over the whole gate order + compound logic.
const CASES: Array<[string, Instruction, SimSnapshot, boolean, SimGate | undefined]> = [
  ["flat threshold met fires",
   rule({ trigger: "on_hfr_above", threshold: 3, action: "refocus" }), SNAP, true, undefined],
  ["flat threshold not met",
   rule({ trigger: "on_hfr_above", threshold: 9, action: "refocus" }), SNAP, false, "not_met"],
  ["disabled is gated first",
   rule({ enabled: false, trigger: "on_frame_rejected" }), { ...SNAP, frameRejected: true },
   false, "disabled"],
  ["only_target gates a different target",
   rule({ trigger: "on_frame_rejected", only_target: "M42" }),
   { ...SNAP, frameRejected: true }, false, "only_target"],
  ["unreadable metric is needs_input, never a false negative",
   rule({ trigger: "on_guide_rms_above", threshold: 1, action: "pause" }),
   { ...SNAP, guideRms: null }, false, "needs_input"],
  ["compound AND needs both terms",
   rule({ action: "pause", when: { op: "all", terms: [hfrAbove(3), rmsAbove(1)] } }),
   SNAP, false, "not_met"],
  ["compound OR needs only one term",
   rule({ action: "pause", when: { op: "any", terms: [hfrAbove(3), rmsAbove(1)] } }),
   SNAP, true, undefined],
  ["compound overrides the flat trigger",
   // flat trigger would NOT fire (no rejected frame); the compound does.
   rule({ trigger: "on_frame_rejected", action: "pause",
          when: { op: "all", terms: [hfrAbove(3), hfrAbove(1)] } }),
   SNAP, true, undefined],
  ["a jump with no destination is reported invalid",
   rule({ trigger: "on_hfr_above", threshold: 3, action: "run_target", target_arg: null }),
   SNAP, false, "invalid"],
  ["a jump with a destination fires",
   rule({ trigger: "on_hfr_above", threshold: 3, action: "run_target", target_arg: "M42" }),
   SNAP, true, undefined],
];

for (const [name, ins, snap, wouldFire, gate] of CASES) {
  test(name, () => {
    const [out] = simulateInstructions([ins], snap);
    eq(out.wouldFire, wouldFire, "wouldFire");
    eq(out.gate, gate, "gate");
    assert(out.reason.length > 0, "reason is non-empty");
    assert(out.summary.length > 0, "summary is non-empty");
  });
}

test("once/cooldown are noted honestly, never silently simulated", () => {
  const [a] = simulateInstructions(
    [rule({ trigger: "on_hfr_above", threshold: 3, action: "refocus", once: true })], SNAP);
  assert(a.wouldFire && !!a.note, "once carries a note");
  const [b] = simulateInstructions(
    [rule({ trigger: "on_hfr_above", threshold: 3, action: "refocus", cooldown_s: 60 })], SNAP);
  assert(b.wouldFire && !!b.note, "cooldown carries a note");
});

test("default snapshot renders every rule with zero configuration", () => {
  const out = simulateInstructions(
    [rule({ trigger: "on_frame_rejected" }),
     rule({ id: "r2", trigger: "on_hfr_above", threshold: 2, action: "refocus" })],
    defaultSnapshot(["M31", "M42"]));
  eq(out.length, 2, "one outcome per rule");
  eq(out[0].wouldFire, false, "no rejected frame in the default snapshot");
  eq(out[1].wouldFire, true, "default HFR 3 > 2");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ninstructionSim.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
