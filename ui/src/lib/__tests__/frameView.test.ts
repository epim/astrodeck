// Run with:  npx tsx src/lib/__tests__/frameView.test.ts
import { neededDeviceWidth, viewWidthFor, shouldRefetch, VIEW_WIDTH_STEPS } from "../frameView";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void): void {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

const ASPECT = 6252 / 4176;          // the rig's sensor, 1.497
const DPR = 2.6;                     // the operator's phone

test("portrait phone is limited by the box WIDTH", () => {
  const need = neededDeviceWidth(412, 780, ASPECT, DPR);
  assert(need === Math.ceil(412 * DPR), `got ${need}`);
  assert(viewWidthFor(need) === 1280, `rung ${viewWidthFor(need)}`);
});

test("rotate to landscape and it is limited by the box HEIGHT", () => {
  // same phone, rotated: the 3:2 frame now fits by height, and wants MORE pixels
  const need = neededDeviceWidth(915, 380, ASPECT, DPR);
  assert(need === Math.ceil(380 * ASPECT * DPR), `got ${need}`);
  assert(need > Math.ceil(412 * DPR),
    "landscape must ask for more than portrait, or rotation handling is pointless");
});

test("the two orientations land on DIFFERENT rungs", () => {
  const p = viewWidthFor(neededDeviceWidth(412, 780, ASPECT, DPR));
  const l = viewWidthFor(neededDeviceWidth(915, 380, ASPECT, DPR));
  assert(p !== l, `both rounded to ${p} — the re-fetch on rotation would be a no-op`);
});

test("fitting by the wrong axis would under-ask badly", () => {
  // the bug this function exists to avoid: using box width in landscape
  const naive = viewWidthFor(Math.ceil(915 * DPR));       // 2379 -> 2560
  const right = viewWidthFor(neededDeviceWidth(915, 380, ASPECT, DPR));
  assert(right < naive, "sanity: contain-fitting asks for less than the raw box width");
});

test("rotation upgrades but never downgrades", () => {
  assert(shouldRefetch(1280, 1600), "should fetch a sharper render");
  assert(!shouldRefetch(1600, 1280), "must not spend a render making it worse");
  assert(shouldRefetch(null, 640), "first open always fetches");
});

test("a huge desktop is capped at the last rung", () => {
  assert(viewWidthFor(99999) === VIEW_WIDTH_STEPS[VIEW_WIDTH_STEPS.length - 1], "uncapped");
});

test("a degenerate box cannot produce a zero or NaN width", () => {
  const n = neededDeviceWidth(0, 0, 0, 0);
  assert(Number.isFinite(n) && n > 0, `got ${n}`);
});

console.log(`frameView.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
