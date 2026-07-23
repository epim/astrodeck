// Pure tests for the NOV-5 starter template catalog + mapping. No jsdom — runs
// under `npx tsx`, compiles under `tsc -b` (idiom: lib/__tests__/eta.test.ts).
//   npx tsx src/lib/__tests__/sequenceTemplates.test.ts
import {
  SEQUENCE_TEMPLATES,
  resolveFilter,
  templateSteps,
} from "../sequenceTemplates";
import type { ExposureStep } from "../../types";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// -------- catalog integrity
test("catalog: non-empty, unique ids, ≥1 positive-valued step each", () => {
  assert(SEQUENCE_TEMPLATES.length >= 3, "expected the 3 briefed starters");
  eq(new Set(SEQUENCE_TEMPLATES.map((t) => t.id)).size, SEQUENCE_TEMPLATES.length, "ids unique");
  for (const t of SEQUENCE_TEMPLATES) {
    assert(t.label.length > 0 && t.blurb.length > 0, `${t.id} needs label+blurb`);
    assert(t.steps.length >= 1, `${t.id} needs steps`);
    for (const s of t.steps) {
      assert(s.exposure_s > 0, `${t.id} exposure_s > 0`);
      assert(Number.isInteger(s.count) && s.count > 0, `${t.id} count int > 0`);
    }
  }
});

// -------- resolveFilter
test("resolveFilter: Luminance intent lands on a mono wheel's 'L'", () => {
  eq(resolveFilter("Luminance", ["L", "R", "G", "B"]), "L", "L");
});
test("resolveFilter: exact name preserved (case-insensitive alias)", () => {
  eq(resolveFilter("Luminance", ["Luminance"]), "Luminance", "exact");
  eq(resolveFilter("luminance", ["Lum"]), "Lum", "alias+case");
});
test("resolveFilter: no wheel / no match / null intent → null", () => {
  eq(resolveFilter("Luminance", []), null, "no wheel");
  eq(resolveFilter("Luminance", ["Ha", "OIII"]), null, "no match");
  eq(resolveFilter(null, ["L"]), null, "null intent");
});

// -------- templateSteps
test("templateSteps: emits full ExposureStep on STARTER_BASE, no id", () => {
  const lum = SEQUENCE_TEMPLATES.find((t) => t.id === "lum-60x120")!;
  const steps: ExposureStep[] = templateSteps(lum, ["L", "R", "G", "B"]);
  eq(steps.length, 1, "one step");
  const s = steps[0];
  eq(s.exposure_s, 120, "exp"); eq(s.count, 60, "count"); eq(s.filter, "L", "resolved filter");
  eq(s.gain, 100, "gain"); eq(s.offset, 30, "offset"); eq(s.binning, 1, "bin");
  eq(s.frame_type, "Light", "frame_type"); eq(s.id, undefined, "no id (ensurePlanIds mints)");
});
test("templateSteps: OSC rig (no wheel) → filter null for every step", () => {
  for (const t of SEQUENCE_TEMPLATES) {
    for (const s of templateSteps(t, [])) eq(s.filter, null, `${t.id} filter null on OSC`);
  }
});
test("templateSteps: frame totals match labels (60/30/20)", () => {
  const total = (id: string) =>
    templateSteps(SEQUENCE_TEMPLATES.find((t) => t.id === id)!).reduce((a, s) => a + s.count, 0);
  eq(total("lum-60x120"), 60, "lum frames");
  eq(total("osc-30x180"), 30, "osc frames");
  eq(total("quick-20x60"), 20, "quick frames");
});

const total = passed + failed;
console.log(`\nsequenceTemplates.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
export const result = { passed, failed, total };
