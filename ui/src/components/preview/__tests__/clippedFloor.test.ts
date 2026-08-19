// "Stars saturated - shorten exposure or lower gain" fired on `stats.max >= fw`
// — ONE railed pixel. Every deep-sky sub rails a bright star core, so the
// warning was permanently on and meant nothing. Measured on the rig 2026-08-18:
// a 60s B sub of NGC 7129, 169 clipped px of 26,108,352, frames good, banner lit.
//
// Run with:  npx tsx src/components/preview/__tests__/clippedFloor.test.ts

import { clippedFloor, CLIPPED_WARN_FRAC } from "../clippedFloor";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void): void {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

const W = 6252, H = 4176;                 // the rig's sensor

test("the rig's own good frame does not warn", () => {
  assert(169 < clippedFloor(W, H),
    `169 clipped px tripped a floor of ${clippedFloor(W, H)} — that frame was fine`);
});

test("a genuinely blown field does warn", () => {
  // ~1% of the frame railed: the exposure really is too long.
  assert(Math.round(W * H * 0.01) >= clippedFloor(W, H), "a blown frame stayed silent");
});

test("a hundred blown star cores is the rough line", () => {
  // Order-of-magnitude sanity on where the threshold sits, so a future tweak
  // has to argue with a number rather than a vibe.
  const floor = clippedFloor(W, H);
  assert(floor > 1000 && floor < 20000, `floor is ${floor}px, outside the intended band`);
});

test("a small frame cannot trip on a handful of pixels", () => {
  // A binned preview or a tiny ROI: the fraction alone would make 3px a warning.
  assert(clippedFloor(200, 200) >= 500,
    "the absolute floor must protect small frames from a proportional threshold");
});

test("the fraction is the documented one", () => {
  assert(CLIPPED_WARN_FRAC === 0.0002, "threshold changed — update the calibration note");
});

console.log(`clippedFloor.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
