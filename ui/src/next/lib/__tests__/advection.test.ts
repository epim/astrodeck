// Pure-lib test for advection.ts. Sabotage check: removing the `Math.max(2, altDeg)`
// floor clamp or the `Math.max(0.05, r2)` divide-by-zero guard turns "clamps a
// low starting altitude" / "extreme wind still returns a finite positive alt"
// red (NaN or a runaway alt); mis-ordering `atan2(gx, gy)` -> `atan2(gy, gx)`
// turns the NE-drift case red.
import { advect } from "../advection";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function near(a: number, b: number, eps = 1e-6, m = ""): void {
  if (Math.abs(a - b) > eps) throw new Error(`${m} expected ~${b}, got ${a}`);
}
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

// alt 45, az 90 (due east), 30 minutes, 12 km/h toward NE (45 deg), base 2.2 km
// -- README worked example: "advection of a point at alt 45 az 90 for 30 min
// at 12 km/h toward NE with 2.2 km base moves toward NE and never below 0 alt".
test("advects toward the wind's bearing and stays above the horizon", () => {
  const r = advect(45, 90, 30, { baseKm: 2.2, windKmh: 12, windTowardDeg: 45 });
  near(r.alt, 15.917692911116806, 1e-6, "alt");
  near(r.az, 56.63409248376183, 1e-6, "az");
  ok(r.alt > 0, "alt must stay above the horizon");
  // az moved AWAY from due-east (90) toward the wind's NE bearing (45) — i.e. decreased.
  ok(r.az < 90, "az should drift toward the wind's bearing (NE, 45 deg), not away from it");
});

test("zero minutes is a no-op advection (same alt/az)", () => {
  const r = advect(45, 200, 0, { baseKm: 2.2, windKmh: 12, windTowardDeg: 45 });
  near(r.alt, 45, 1e-6, "alt");
  near(r.az, 200, 1e-6, "az");
});

test("a low/negative starting altitude is floored at 2 deg, never crashes, never goes negative", () => {
  const r = advect(-5, 0, 60, { baseKm: 2.2, windKmh: 12, windTowardDeg: 90 });
  near(r.alt, 1.9647047994414002, 1e-6, "alt");
  near(r.az, 10.784338451611461, 1e-6, "az");
  ok(r.alt > 0, "alt must stay above the horizon even from a below-horizon start");
});

test("an extreme wind/duration still returns a finite, strictly positive altitude", () => {
  const r = advect(1, 0, 100000, { baseKm: 2.2, windKmh: 500, windTowardDeg: 0 });
  ok(Number.isFinite(r.alt), "alt must be finite");
  ok(r.alt > 0, "alt must stay strictly above 0");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
