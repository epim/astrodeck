// rotation.test.ts — table-driven tests for lib/rotation (spec §5.2). Inline-
// assert harness (no vitest); runs via `npx tsx`. SAME vectors as
// server/tests/test_rotation.py — keep the tables in sync.
import {
  adjustedPa,
  angleEquals,
  angleEqualsMod180,
  mapSkyTarget,
  mod360,
  shortestRotation,
  targetMechanicalPosition,
} from "../rotation";

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
function approx(a: number, b: number, msg = ""): void {
  if (Math.abs(a - b) > 1e-6) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

test("mod360 is euclidean", () => {
  eq(mod360(0.0), 0.0);
  eq(mod360(360.0), 0.0);
  eq(mod360(-10.0), 350.0);
  eq(mod360(725.0), 5.0);
});

// (a, b, tol, equal) — §11.4: d = |mod(a,360)-mod(b,360)|; equal when
// d <= tol or (360-d) <= tol (with the 1e-13 slack).
const ANGLE_EQ: [number, number, number, boolean][] = [
  [0.0, 0.0, 1.0, true],
  [0.5, 0.0, 1.0, true],
  [1.5, 0.0, 1.0, false],
  [359.5, 0.0, 1.0, true], // wrap
  [180.0, 0.0, 1.0, false],
  [365.0, 5.0, 0.5, true], // mod-360 collapse
];
ANGLE_EQ.forEach(([a, b, tol, expected], i) => {
  test(`angleEquals row ${i}: (${a},${b},${tol})`, () => {
    eq(angleEquals(a, b, tol), expected);
  });
});

// (a, b, tol, equal) — §11.4 'oneEightyIsEqual'.
const MOD180_EQ: [number, number, number, boolean][] = [
  [180.0, 0.0, 1.0, true], // the whole point: 180° apart IS equal
  [179.5, 0.0, 1.0, true],
  [90.0, 0.0, 1.0, false],
  [359.5, 180.0, 1.0, true],
];
MOD180_EQ.forEach(([a, b, tol, expected], i) => {
  test(`angleEqualsMod180 row ${i}: (${a},${b},${tol})`, () => {
    eq(angleEqualsMod180(a, b, tol), expected);
  });
});

// (p, range_type, range_start, expected) — §11.2 get_target_mechanical_position.
// d = mod360(p - range_start); HALF: p if d<180 else p+180;
// QUARTER: p / p+270 / p+180 / p+90 for d<90 / d<180 / d<270 / else.
const RANGE_MAP: [number, "full" | "half" | "quarter", number, number][] = [
  [10.0, "full", 0.0, 10.0],
  [350.0, "full", 245.0, 350.0],
  // HALF, start 0: allowed [0,180)
  [10.0, "half", 0.0, 10.0], // d=10  < 180 → p
  [190.0, "half", 0.0, 10.0], // d=190 ≥ 180 → p+180 = 370 → 10
  // HALF, start 245: allowed [245, 65)
  [30.0, "half", 245.0, 30.0], // d=mod360(30-245)=145 < 180 → p
  [100.0, "half", 245.0, 280.0], // d=215 ≥ 180 → p+180 = 280
  // QUARTER, start 0: allowed [0,90)
  [10.0, "quarter", 0.0, 10.0], // d=10  < 90  → p
  [100.0, "quarter", 0.0, 10.0], // d=100 < 180 → p+270 = 370 → 10
  [200.0, "quarter", 0.0, 20.0], // d=200 < 270 → p+180 = 380 → 20
  [300.0, "quarter", 0.0, 30.0], // d=300 ≥ 270 → p+90  = 390 → 30
  // QUARTER, start 245
  [250.0, "quarter", 245.0, 250.0], // d=5   < 90  → p
  [340.0, "quarter", 245.0, 250.0], // d=95  < 180 → p+270 = 610 → 250
  [100.0, "quarter", 245.0, 280.0], // d=215 < 270 → p+180 = 280
  [160.0, "quarter", 245.0, 250.0], // d=275 ≥ 270 → p+90  = 250
];
RANGE_MAP.forEach(([p, rt, start, expected], i) => {
  test(`targetMechanicalPosition row ${i}: (${p},${rt},${start})`, () => {
    approx(targetMechanicalPosition(p, rt, start), expected);
  });
});

test("mapSkyTarget full range is identity", () => {
  // FULL: mech target == mech, so the sky target comes back unchanged.
  approx(mapSkyTarget(120.0, 78.5, 30.0, "full", 0.0), 120.0);
});

test("mapSkyTarget composes offset and range", () => {
  // §11.2: mech = mod360(sky + offset); mech_tgt = range_map(mech);
  // back = mod360(mech_tgt - offset). sky=100, offset=40 → mech=140;
  // HALF start 245 → d=mod360(140-245)=255 ≥ 180 → mech_tgt=320;
  // back = 320-40 = 280.
  approx(mapSkyTarget(100.0, 0.0, 40.0, "half", 245.0), 280.0);
});

test("shortestRotation full prefers 180 flip", () => {
  // §11.3 FULL branch: distance 170° → the 180°-flipped frame is only −10 away.
  approx(shortestRotation(170.0, 0.0, "full"), -10.0);
  // distance 10 stays 10
  approx(shortestRotation(10.0, 0.0, "full"), 10.0);
  // distance 100: mod180=100, m2=-80 → -80 is shorter
  approx(shortestRotation(100.0, 0.0, "full"), -80.0);
});

test("shortestRotation limited range keeps full signed", () => {
  // HALF/QUARTER: the target was already range-mapped — do NOT collapse
  // mod-180 (that could command the out-of-range twin). Just normalize
  // to (-180, 180].
  approx(shortestRotation(350.0, 0.0, "half"), -10.0);
  approx(shortestRotation(190.0, 0.0, "quarter"), -170.0);
});

test("adjustedPa flags a framing change", () => {
  const cfg = { range_type: "quarter" as const, range_start_deg: 0, tolerance_deg: 1 };
  const rot = { sky_deg: 10, mech_deg: 10 }; // offset 0
  const r = adjustedPa(100, rot, cfg); // QUARTER [0,90): 100→10
  eq(Math.round(r.target), 10);
  eq(r.adjusted, true);
  const r2 = adjustedPa(50, rot, cfg);
  eq(r2.adjusted, false);
});

console.log(`rotation.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
