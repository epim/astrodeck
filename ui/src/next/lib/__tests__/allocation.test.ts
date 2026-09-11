// Pure-lib test for allocation.ts. Sabotage check: dividing by
// `filters.length` instead of the CHECKED count turns "6h over 4 filters"
// red; dropping the OSC branch's own hours*3600 term turns "OSC single
// exposure" red; removing the unchecked-filter zeroing turns "unchecked
// filters get 0" red.
import { allocate, allocateOsc, finishesAt } from "../allocation";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

// README worked example: "allocation 6 h over 4 filters at 60 s = 90 subs each".
test("6h over 4 filters at 60s exposure -> 90 subs each, 5400s total each", () => {
  const filters = ["L", "R", "G", "B"].map((name) => ({ name, exposureS: 60, checked: true }));
  const result = allocate(6, filters);
  eq(result.length, 4);
  for (const f of result) {
    eq(f.count, 90, `${f.name} count`);
    eq(f.totalS, 5400, `${f.name} totalS`);
  }
});

test("unchecked filters split the window among the checked ones only, and score 0", () => {
  const filters = [
    { name: "L", exposureS: 60, checked: true },
    { name: "R", exposureS: 60, checked: true },
    { name: "Ha", exposureS: 180, checked: false },
  ];
  const result = allocate(2, filters); // per = 2*3600/2 = 3600
  const byName = Object.fromEntries(result.map((f) => [f.name, f]));
  eq(byName.L.count, 60, "L count (3600/60)");
  eq(byName.R.count, 60, "R count");
  eq(byName.Ha.count, 0, "unchecked filter gets 0");
  eq(byName.Ha.totalS, 0, "unchecked filter totalS 0");
});

test("no filters checked -> every count 0, never divides by zero", () => {
  const filters = [{ name: "L", exposureS: 60, checked: false }];
  const result = allocate(3, filters);
  eq(result[0].count, 0);
  eq(result[0].totalS, 0);
});

// OSC branch: README "OSC rigs get a single EXPOSURE row instead".
test("allocateOsc: 2h at 120s exposure -> 60 subs, 7200s total", () => {
  const r = allocateOsc(2, 120);
  eq(r.count, 60);
  eq(r.totalS, 7200);
});

test("finishesAt adds hours*3600*1000 ms to the start", () => {
  const start = 1_700_000_000_000;
  eq(finishesAt(start, 2), start + 2 * 3600 * 1000);
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
