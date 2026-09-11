// Pure-lib test for reach.ts. Sabotage check: dropping the `hidden` short-
// circuit turns "hidden scores -1 and rankTargets drops it" red; swapping the
// sort direction (asc instead of desc) turns "ranks descending" red; changing
// the 240-minute cap turns "time-above-floor caps around 4h" red.
import { rankTargets, reachScore, windowLabel } from "../reach";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function near(a: number, b: number, eps = 1e-9, m = ""): void {
  if (Math.abs(a - b) > eps) throw new Error(`${m} expected ~${b}, got ${a}`);
}
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

test("best-possible target scores 1.0 (alt 90, clear, 4h+ window, moon far)", () => {
  const s = reachScore({ altNow: 90, cloudPct: 0, minutesAboveFloorToDawn: 240, moonSepDeg: 90 });
  near(s, 1, 1e-9);
});

test("worst-possible target (below horizon, total overcast, no window, moon on top) scores 0", () => {
  const s = reachScore({ altNow: 0, cloudPct: 100, minutesAboveFloorToDawn: 0, moonSepDeg: 0 });
  near(s, 0, 1e-9);
});

test("hidden always scores -1, regardless of the other factors", () => {
  const s = reachScore({ altNow: 90, cloudPct: 0, minutesAboveFloorToDawn: 240, moonSepDeg: 90, hidden: true });
  eq(s, -1);
});

test("time-above-floor caps at 240 minutes (matches the design's min(4h) bonus)", () => {
  const at4h = reachScore({ altNow: 45, cloudPct: 50, minutesAboveFloorToDawn: 240, moonSepDeg: 45 });
  const at8h = reachScore({ altNow: 45, cloudPct: 50, minutesAboveFloorToDawn: 480, moonSepDeg: 45 });
  near(at4h, at8h, 1e-9, "8h window should score the same as 4h (capped)");
});

test("rankTargets sorts descending by score and drops hidden kinds", () => {
  const list = [
    { id: "low", altNow: 20, cloudPct: 60, minutesAboveFloorToDawn: 30, moonSepDeg: 10 },
    { id: "high", altNow: 70, cloudPct: 5, minutesAboveFloorToDawn: 200, moonSepDeg: 80 },
    { id: "gone", altNow: 90, cloudPct: 0, minutesAboveFloorToDawn: 240, moonSepDeg: 90, hidden: true },
  ];
  const ranked = rankTargets(list);
  eq(ranked.length, 2, "hidden target must be dropped");
  eq(ranked[0].id, "high", "highest score first");
  eq(ranked[1].id, "low", "lowest score last");
  ok(ranked[0].score >= ranked[1].score, "descending order");
});

test("windowLabel(160) formats as \"2h 40m\" (README reach-strip example)", () => {
  eq(windowLabel(160), "2h 40m");
});

test("windowLabel drops the hour part under 60 minutes", () => {
  eq(windowLabel(45), "45m");
});

test("windowLabel(0) is \"0m\"", () => {
  eq(windowLabel(0), "0m");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
