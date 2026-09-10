// Pure-lib test for horizonModel.ts. Sabotage check: dropping the wrap
// extension points (first-before-last / last-after-first) turns the
// 315-degree wrap test red; loosening movePoint's neighbour clamp turns
// "movePoint cannot cross a neighbour" red; scanning rows bottom-to-top
// instead of top-to-bottom in autoTraceSkyline turns its worked example red.
import {
  horizonAltAt,
  insertPoint,
  isObstructed,
  movePoint,
  removePoint,
  summary,
  autoTraceSkyline,
  horizonVerdict,
} from "../horizonModel";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function near(a: number, b: number, eps: number, m = ""): void {
  if (Math.abs(a - b) > eps) throw new Error(`${m} expected ~${b}, got ${a}`);
}
function eqPoint(a: { az: number; alt: number }, b: { az: number; alt: number }): void {
  if (a.az !== b.az || a.alt !== b.alt) {
    throw new Error(`expected {az:${b.az},alt:${b.alt}}, got {az:${a.az},alt:${a.alt}}`);
  }
}

const PTS = [
  { az: 0, alt: 10 },
  { az: 90, alt: 20 },
  { az: 180, alt: 5 },
  { az: 270, alt: 15 },
];

test("horizonAltAt: linear interpolation between two points", () => {
  // halfway between (0,10) and (90,20) -> 15
  near(horizonAltAt(PTS, 45), 15, 1e-9);
});

test("horizonAltAt: exact hit on a stored point returns that point's altitude", () => {
  eq(horizonAltAt(PTS, 90), 20);
});

test("horizonAltAt: wraps at 360 (interpolates from the last point back to the first)", () => {
  // halfway between (270,15) and (360=0,10) -> 12.5
  near(horizonAltAt(PTS, 315), 12.5, 1e-9);
});

test("horizonAltAt: empty polyline reads as flat 0", () => {
  eq(horizonAltAt([], 45), 0);
});

test("isObstructed: at/below the horizon line is obstructed, above it is not", () => {
  eq(isObstructed(PTS, 12, 45), true, "12 <= horizon(45)=15");
  eq(isObstructed(PTS, 20, 45), false, "20 > horizon(45)=15");
});

test("insertPoint keeps the array az-sorted", () => {
  const next = insertPoint(PTS, 45.4, 12.6);
  eq(next.length, 5);
  eq(next[1].az, 45, "rounded and sorted between az 0 and az 90");
  eq(next[1].alt, 13);
});

test("movePoint clamps az strictly between its neighbours and alt to -8..88", () => {
  const pts = [{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }];
  const movedHigh = movePoint(pts, 1, 999, 999);
  eq(movedHigh[1].az, 99, "clamped below the next point (100-1)");
  eq(movedHigh[1].alt, 88, "clamped at the 88 ceiling");
  const movedLow = movePoint(pts, 1, -999, -999);
  eq(movedLow[1].az, 11, "clamped above the previous point (10+1)");
  eq(movedLow[1].alt, -8, "clamped at the -8 floor");
});

test("movePoint at the ends clamps to 0/359 (no neighbour on that side)", () => {
  const pts = [{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }];
  eq(movePoint(pts, 0, -50, 0)[0].az, 0);
  eq(movePoint(pts, 2, 999, 0)[2].az, 359);
});

test("removePoint drops exactly the given index, no confirm", () => {
  const next = removePoint(PTS, 1);
  eq(next.length, 3);
  eq(next.some((p) => p.az === 90), false);
});

test("summary: worked example \"13 points * up to 38deg\"", () => {
  const thirteen = [
    { az: 0, alt: 10 }, { az: 30, alt: 12 }, { az: 60, alt: 20 }, { az: 90, alt: 5 },
    { az: 120, alt: 8 }, { az: 150, alt: 38 }, { az: 180, alt: 22 }, { az: 210, alt: 6 },
    { az: 240, alt: 4 }, { az: 270, alt: 30 }, { az: 300, alt: 18 }, { az: 330, alt: 9 },
    { az: 350, alt: 7 },
  ];
  eq(thirteen.length, 13);
  eq(summary(thirteen), "13 points · up to 38°");
});

test("summary: empty horizon", () => {
  eq(summary([]), "0 points");
});

test("autoTraceSkyline: first row under skyLum per column, no-drop column reads to the bottom", () => {
  const skyLum = 100;
  const columns = [
    [200, 200, 50, 50], // drops at row 2 of 4
    [200, 200, 200, 200], // never drops -> bottom of frame
    [50, 50, 50, 50], // drops immediately at row 0
    [200, 50, 50, 50], // drops at row 1
  ];
  const pts = autoTraceSkyline(columns, skyLum, 90, -10);
  eq(pts.length, 4);
  eqPoint(pts[0], { az: 0, alt: 23 });
  eqPoint(pts[1], { az: 90, alt: -10 });
  eqPoint(pts[2], { az: 180, alt: 90 });
  eqPoint(pts[3], { az: 270, alt: 57 });
});

test("horizonVerdict is re-exported and callable (reuse of ui/src/lib/horizon.ts)", () => {
  eq(horizonVerdict(30, 25, false), "ok");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
