// The marker must not sit ON the thing it annotates.
//
// `r = max(hfr * displayScale * 1.6, 4)` put the ring at 1.6x HFR. HFR is the
// HALF-FLUX radius: on a stretched frame a star's visible disc runs to roughly
// 2.5-3x HFR, so the ring landed inside the star, and its dark `--halo`
// under-stroke cut a dark band through the middle of every one.
//
// Reported from the rig 2026-08-18 at 282% zoom on a phone: "the stars are
// pretty doughnut-y". The pixels were fine -- FWHM 3.34px, no central dip in any
// of 12 unsaturated profiles. It was this ring.
//
// Run with:  npx tsx src/components/preview/__tests__/starOverlayGeometry.test.ts

import { markerRadius, VISIBLE_EDGE_HFR } from "../starOverlayGeometry";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

test("at pixel-peep the ring clears the star's visible disc", () => {
  // displayScale 1 = the sensor-1:1 crop layer, which is where the operator
  // zooms to inspect a star and where the old factor put the ring inside it.
  const hfr = 3.44;                       // measured on the rig that night
  const r = markerRadius(hfr, 1);
  assert(r > hfr * VISIBLE_EDGE_HFR,
    `ring at ${r.toFixed(2)}px vs a visible disc reaching ${(hfr * VISIBLE_EDGE_HFR).toFixed(2)}px `
    + "— the annotation is drawn on top of the star it annotates");
});

test("the old geometry is what this test would have caught", () => {
  const hfr = 3.44;
  const old = Math.max(hfr * 1 * 1.6, 4);
  assert(old < hfr * VISIBLE_EDGE_HFR,
    "the 1.6 factor should sit inside the visible disc — if this stops being "
    + "true the constant changed and the story above needs rewriting");
});

test("a tiny star still gets a tappable ring", () => {
  // The floor is a cold-thumb affordance, not a measurement.
  assert(markerRadius(0.1, 0.05) >= 4, "sub-pixel star lost its marker");
});

test("the ring scales with the star, so tilt still reads across the field", () => {
  const small = markerRadius(2.0, 1);
  const big = markerRadius(6.0, 1);
  assert(big > small * 2.5,
    `a 3x HFR difference must stay visible as a size difference: ${small} vs ${big}`);
});

test("at fit-zoom the floor dominates and nothing changes", () => {
  // display 1400 / data 6252
  const ds = 1400 / 6252;
  assert(markerRadius(3.44, ds) === 4,
    "at fit the marker was already floored at 4 — this change must not alter "
    + "the zoomed-out view at all");
});

console.log(`starOverlayGeometry.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
