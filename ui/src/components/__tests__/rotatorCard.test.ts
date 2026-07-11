// rotatorCard.test.ts — pure-geometry regression for the RotatorCard arc dial
// (CAA spec §5.1). The dial is display-only, but its polar/arc math decides
// where the current-angle dot and the range-of-motion sweep land, so a
// regression here would silently mis-draw the ROM. Same inline-assert / `tsx`
// style as polar.test.ts (no jsdom, no timers).
//
// Run directly:  npx tsx src/components/__tests__/rotatorCard.test.ts
// Also type-checked by `tsc -b` in the build.
//
// Imports from lib/rotatorDial DIRECTLY (not "../equipment/RotatorCard", which
// re-exports the same helpers but eagerly touches `window` via its ../../api →
// lib/base import — a plain tsx/node run has no DOM, so importing the component
// module would throw). Same precedent as healthStrip.test.ts.

import { allowedSweepDeg, arcPath, polarXY } from "../../lib/rotatorDial";

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

test("polarXY puts 0° at 12 o'clock", () => {
  const p = polarXY(60, 60, 48, 0);
  eq(Math.round(p.x), 60);
  eq(Math.round(p.y), 12);
});

test("polarXY puts 90° at 3 o'clock (clockwise)", () => {
  const p = polarXY(60, 60, 48, 90);
  eq(Math.round(p.x), 108);
  eq(Math.round(p.y), 60);
});

test("allowedSweepDeg maps range types", () => {
  eq(allowedSweepDeg("full"), 360);
  eq(allowedSweepDeg("half"), 180);
  eq(allowedSweepDeg("quarter"), 90);
});

test("arcPath large-arc flag flips past 180", () => {
  eq(arcPath(60, 60, 48, 0, 90).includes(" 0 0 1 "), true);
  eq(arcPath(60, 60, 48, 0, 270).includes(" 0 1 1 "), true);
});

console.log(`rotatorCard.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
