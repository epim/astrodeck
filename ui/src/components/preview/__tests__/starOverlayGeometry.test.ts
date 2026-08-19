// The marker must not sit ON the thing it annotates — checked at the numbers
// THIS RIG ACTUALLY PRODUCES.
//
// The first attempt at this fix raised the star-size factor from 1.6 to 3.0 and
// tested it at displayScale = 1. That scale is unreachable: PreviewStage always
// passes dispW/data_width, and dispW is capped at 1400 by the preview encoder
// against a 6252px sensor, so displayScale is 0.224. At that scale a 4-unit tap
// floor beats both factors for every in-focus star and the change was
// bit-identical. A test that passes against a value the system cannot produce
// is worse than no test.
//
// Run with:  npx tsx src/components/preview/__tests__/starOverlayGeometry.test.ts

import { markerRadius, VISIBLE_EDGE_HFR } from "../starOverlayGeometry";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

/** The rig: 6252px sensor, preview encoder caps display width at 1400. */
const DS = 1400 / 6252;
const HFR = 3.44;                       // measured on the rig that night

// A LITERAL, not the constant under test. Deriving the expectation from
// VISIBLE_EDGE_HFR made the assertion self-referential: shrinking the constant
// moved the ring AND the bar it had to clear, so the test could not fail.
// 2.6 x HFR is where a stretched star's disc actually ends.
const EDGE_FACTOR = 2.6;
const visibleEdge = HFR * DS * EDGE_FACTOR;

test("zoomed in to inspect a star, the ring clears its visible disc", () => {
  const r = markerRadius(HFR, DS, 2.82);     // 282%, the reported zoom
  assert(r > visibleEdge,
    `ring ${r.toFixed(2)} vs visible edge ${visibleEdge.toFixed(2)} display units `
    + "— the annotation is still drawn on the star");
});

test("the ORIGINAL geometry fails this, at the real scale", () => {
  const old = Math.max(HFR * DS * 1.6, 4);
  assert(old > visibleEdge === false || old === 4,
    "sanity: the old radius was the 4-unit floor");
  // and the floor, scaled with the image, is what lands on the star at zoom
  assert(4 > visibleEdge, "premise: 4 display units exceeds the star's edge at fit");
});

test("the FIRST fix was bit-identical at this scale — that is why it did nothing", () => {
  const old = Math.max(HFR * DS * 1.6, 4);
  const firstFix = Math.max(HFR * DS * 3.0, 4);
  assert(old === firstFix,
    `old ${old} vs first fix ${firstFix} — if these ever differ, the floor stopped `
    + "binding and this story needs rewriting");
});

test("at fit zoom the tap target is unchanged", () => {
  assert(markerRadius(HFR, DS, 1) === 4,
    "zoomed out, a tiny star must keep a thumb-sized ring");
});

test("the floor is constant on SCREEN, so it shrinks as you zoom in", () => {
  const a = markerRadius(0.5, DS, 1);
  const b = markerRadius(0.5, DS, 4);
  assert(b < a, `floor did not shrink with zoom: ${a} -> ${b}`);
  assert(Math.abs(b * 4 - a * 1) < 1e-9, "the floor should be exactly 1/zoom");
});

test("a big defocused star still drives the ring itself", () => {
  const r = markerRadius(20, DS, 2.82);
  assert(r > 20 * DS * EDGE_FACTOR, "an autofocus-sweep star lost its clearance");
});

test("the shipped edge factor is not quietly loosened below the real one", () => {
  assert(VISIBLE_EDGE_HFR >= EDGE_FACTOR,
    `VISIBLE_EDGE_HFR is ${VISIBLE_EDGE_HFR}, below the ${EDGE_FACTOR}x where a `
    + "stretched star's disc ends — the ring would sit on the star again");
});

test("absurd zoom cannot divide by zero", () => {
  assert(Number.isFinite(markerRadius(HFR, DS, 0)), "non-finite radius at zoom 0");
});

console.log(`starOverlayGeometry.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
