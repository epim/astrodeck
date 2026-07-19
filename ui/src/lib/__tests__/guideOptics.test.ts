// guideOptics.test.ts — the guide-scope plate scale hint reuses lib/optics.ts
// scale(fl, px, bin) = 206.265 * px * bin / fl (A4; the SAME arcsec-per-px
// formula the native backend uses). Run with:
//   npx tsx src/lib/__tests__/guideOptics.test.ts   (from ui/)
import { scale } from "../optics";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("4um / 200mm guide scope -> 4.1253 arcsec/px", () => {
  assert(Math.abs(scale(200, 4.0) - 4.1253) < 1e-3, `got ${scale(200, 4.0)}`);
});
test("binning 2 doubles the guide scale", () => {
  assert(Math.abs(scale(200, 4.0, 2) - 8.2506) < 1e-3, `got ${scale(200, 4.0, 2)}`);
});
test("no focal length -> 0 (no hint)", () => {
  assert(scale(0, 4.0) === 0, "fl<=0 guards to 0");
});

console.log(`guideOptics.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
