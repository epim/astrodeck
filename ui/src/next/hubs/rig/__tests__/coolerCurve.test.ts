// coolerCurve.test.ts - the camera sheet's ring arc and cooling curve, as maths.
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/coolerCurve.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The point of these assertions is the HONESTY of the drawing, not its
// prettiness. Three of them guard a specific lie the prototype tells or that an
// obvious implementation would tell:
//
//   * fewer than three readings must produce NO polyline. Two points is a
//     straight line between two numbers and reads as a trend; the prototype
//     instead fabricates 24 points from an exponential (plan E25).
//   * a camera that reported no temperature must produce NO arc. `null` is not
//     0 C - 0 C is a real reading and its arc looks exactly like a claim.
//   * the set-point line and the trace must share ONE scale, or a sensor drawn
//     against its own min/max appears to arrive at a target it is nowhere near.

import {
  BUILDING_NOTE, CURVE_H, CURVE_MAX_SAMPLES, CURVE_PAD, CURVE_W,
  RING_CIRCUMFERENCE, coolerCurve, pushTemp, ringDash, ringFraction,
} from "../lib/coolerCurve";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) throw new Error(`${msg} (expected ~${want}, got ${got})`);
}

// ================================================================== the ring

test("precondition: the ring's fraction is the prototype's own formula", () => {
  // (25 - t) / 40, so -15 fills it and +25 empties it.
  near(ringFraction(-15) ?? -1, 1, 1e-9, "-15 C does not fill the ring");
  near(ringFraction(25) ?? -1, 0, 1e-9, "+25 C does not empty the ring");
  near(ringFraction(5) ?? -1, 0.5, 1e-9, "+5 C is not half the ring");
});

test("the ring clamps rather than over-drawing", () => {
  eq(ringFraction(-40), 1, "a camera colder than the drawn range overfilled the ring");
  eq(ringFraction(40), 0, "a camera hotter than the drawn range drew a negative arc");
});

test("HONESTY: no reading is not zero degrees", () => {
  eq(ringFraction(null), null, "a null temperature produced a fraction");
  eq(ringFraction(undefined), null, "an absent temperature produced a fraction");
  eq(ringFraction(Number.NaN), null, "NaN produced a fraction");
  // And the dash for it is an EMPTY arc, not a full one.
  eq(ringDash(null), `0.0 ${RING_CIRCUMFERENCE}`, "a camera with no reading still drew an arc");
});

test("the dash is dash-then-a-full-circumference gap", () => {
  eq(ringDash(0.5), `${(RING_CIRCUMFERENCE / 2).toFixed(1)} ${RING_CIRCUMFERENCE}`,
    "half a ring is not half the circumference");
});

// =============================================================== the buffer

test("pushTemp keeps the last N and returns the SAME array when there is nothing to add", () => {
  let buf: number[] = [];
  for (let i = 0; i < CURVE_MAX_SAMPLES + 5; i++) buf = pushTemp(buf, i);
  eq(buf.length, CURVE_MAX_SAMPLES, "the ring buffer grew past its cap");
  eq(buf[buf.length - 1], CURVE_MAX_SAMPLES + 4, "the newest reading is not last");
  eq(buf[0], 5, "the buffer dropped the wrong end");
  const same = pushTemp(buf, null);
  assert(same === buf, "a null reading allocated a new array - every poll would re-render");
});

// ================================================================ the curve

test("HONESTY: below three readings there is no polyline, and the card is told why", () => {
  for (const n of [0, 1, 2]) {
    const c = coolerCurve([12, 10].slice(0, n), -10);
    eq(c.points, "", `${n} readings drew a curve`);
    eq(c.ready, false, `${n} readings claimed to be ready`);
    eq(c.note, BUILDING_NOTE, `${n} readings gave the card nothing to say`);
  }
  const three = coolerCurve([12, 4, -2], -10);
  assert(three.points.length > 0, "three readings drew nothing - the curve never appears");
  eq(three.ready, true, "three readings did not read as ready");
  eq(three.note, null, "a drawn curve still carries the building note");
});

test("the set-point line is drawn before the curve is", () => {
  const none = coolerCurve([], -10);
  eq(none.setpointY, CURVE_H / 2, "with no readings the set-point line vanished");
  const some = coolerCurve([12], -10);
  assert(some.setpointY != null, "one reading dropped the set-point line");
  eq(some.points, "", "one reading still drew a curve");
});

test("no set-point means no set-point line - never a line at zero", () => {
  eq(coolerCurve([12, 6, 0], null).setpointY, null, "a missing set-point drew a line anyway");
  eq(coolerCurve([12, 6, 0], undefined).setpointY, null, "an absent set-point drew a line anyway");
});

test("hotter is higher, and the x axis spans the box", () => {
  const c = coolerCurve([12, 0, -10], -10);
  const pts = c.points.split(" ").map((p) => p.split(",").map(Number));
  eq(pts.length, 3, "a reading was dropped from the polyline");
  eq(pts[0][0], 0, "the first reading is not at the left edge");
  eq(pts[2][0], CURVE_W, "the last reading is not at the right edge");
  assert(pts[0][1] < pts[2][1], "the warm reading is not drawn above the cold one");
  near(pts[0][1], CURVE_PAD, 0.05, "the hottest reading is not at the top of the box");
  near(pts[2][1], CURVE_H - CURVE_PAD, 0.05, "the coldest reading is not at the bottom");
});

test("the set-point shares the trace's scale", () => {
  // Sensor 12 -> 2, target -10: the target is BELOW everything measured, so its
  // line must sit at the bottom of the box and the trace above it. A curve
  // scaled to its own min/max would put the 2 at the bottom and the target off
  // the box - the camera would look like it had arrived.
  const c = coolerCurve([12, 7, 2], -10);
  const ys = c.points.split(" ").map((p) => Number(p.split(",")[1]));
  assert(c.setpointY != null, "no set-point line");
  assert(ys.every((y) => y < c.setpointY!), "a reading was drawn at or below a colder set-point");
  near(c.setpointY!, CURVE_H - CURVE_PAD, 0.05, "the set-point is not at the bottom of the shared window");
  eq(c.lo, -10, "the window does not reach the set-point");
  eq(c.hi, 12, "the window does not reach the warmest reading");
});

test("a flat reading gets a window instead of a divide by zero", () => {
  const c = coolerCurve([-10, -10, -10], null);
  const ys = c.points.split(" ").map((p) => Number(p.split(",")[1]));
  assert(ys.every((y) => Number.isFinite(y)), "a flat trace produced NaN coordinates");
  near(ys[0], CURVE_H / 2, 0.05, "a flat trace is not drawn through the middle");
  eq(c.hi - c.lo, 1, "a flat trace did not get a one-degree window");
});

test("every drawn point stays inside the box", () => {
  const c = coolerCurve([30, -40, 0, 5], -80);
  for (const p of c.points.split(" ")) {
    const y = Number(p.split(",")[1]);
    assert(y >= CURVE_PAD - 1e-9 && y <= CURVE_H - CURVE_PAD + 1e-9,
      `a point escaped the box at y=${y}`);
  }
});

const total = passed + failed;
console.log(`coolerCurve.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
