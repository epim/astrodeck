// A SATURATED FRAME MUST NOT BE TOLD TO LENGTHEN ITS EXPOSURE.
//
// FocusVerdict computes `clipped` and then returns from the `fewStars` branch
// BEFORE it is ever read. On a frame where every pixel is railed the star
// detector finds nothing, so the screen shows
//
//     Few stars — check focus/clouds, or lengthen the exposure
//
// which is the exact inverse of the repair. Seen on the rig 2026-08-19 with the
// cap on in daylight: MIN = MEDIAN = MEAN = MAX = 65535, 0 stars, and that
// advice on screen. Zero stars is the CONSEQUENCE of the saturation, not an
// exposure shortfall.
//
// autofocus.py already carries a comment about this exact wrong turn ("it sends
// someone to lengthen an exposure that was never the problem"); the server
// learned it and this component did not.
//
// Run with:  npx tsx src/components/preview/__tests__/focusVerdictOrder.test.ts

import { adviceFor } from "../focusAdvice";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void): void {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

const RAILED = { fewStars: true, clipped: true };
const DARK = { fewStars: true, clipped: false };

test("a railed frame is told to SHORTEN, never to lengthen", () => {
  const a = adviceFor(RAILED);
  assert(a !== null && a.kind === "clipped", `got ${a?.kind}`);
  assert(/shorten|lower/i.test(a!.text), a!.text);
  assert(!/lengthen/i.test(a!.text),
    `advises lengthening a frame whose every pixel is at full scale: ${a!.text}`);
});

test("a genuinely dark frame still gets the lengthen advice", () => {
  const a = adviceFor(DARK);
  assert(a !== null && a.kind === "few-stars", `got ${a?.kind}`);
  assert(/lengthen/i.test(a!.text), a!.text);
});

test("saturation outranks few-stars, because it CAUSES it", () => {
  assert(adviceFor(RAILED)!.kind !== adviceFor(DARK)!.kind,
    "the two frames need different repairs and must not share a message");
});

test("a clean frame with stars gets neither", () => {
  assert(adviceFor({ fewStars: false, clipped: false }) === null, "spurious advice");
});

test("clipping is reported even when the stars are fine", () => {
  const a = adviceFor({ fewStars: false, clipped: true });
  assert(a !== null && a.kind === "clipped", "a clipped frame with stars said nothing");
});

console.log(`focusVerdictOrder.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
