// Pure-lib test for fov.ts. Sabotage check: swapping atan() for the linear
// small-angle approximation (`sensor/fl` instead of `2*atan(sensor/2/fl)`)
// turns the 23.5mm@530mm worked example red; swapping the >2 / <0.7
// thresholds turns the sampling-verdict boundaries red; not rotating the
// base order turns "panelOrder cycles" red.
import { fovDeg, mosaicPitch, panelOrder, samplingArcsecPerPx, samplingVerdict } from "../fov";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function near(a: number, b: number, eps: number, m = ""): void {
  if (Math.abs(a - b) > eps) throw new Error(`${m} expected ~${b}, got ${a}`);
}
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

// README's own Optics-sheet worked example: "2.54deg x 1.70deg field" for a
// 23.5x15.7mm sensor (IMX571) at 530mm, no reducer.
test("fovDeg: 23.5x15.7mm sensor at 530mm -> 2.54 x 1.70 deg", () => {
  const f = fovDeg({ sensorWmm: 23.5, sensorHmm: 15.7, flMm: 530, reducer: 1 });
  near(f.wDeg, 2.54, 0.01, "wDeg");
  near(f.hDeg, 1.70, 0.01, "hDeg");
});

test("fovDeg: a 0.5x reducer roughly doubles the field", () => {
  const base = fovDeg({ sensorWmm: 23.5, sensorHmm: 15.7, flMm: 530, reducer: 1 });
  const reduced = fovDeg({ sensorWmm: 23.5, sensorHmm: 15.7, flMm: 530, reducer: 0.5 });
  near(reduced.wDeg, base.wDeg * 2, 0.05, "reducer halves effective FL, ~doubles FoV");
});

test("fovDeg: zero focal length returns zero rather than Infinity/NaN", () => {
  const f = fovDeg({ sensorWmm: 23.5, sensorHmm: 15.7, flMm: 0 });
  eq(f.wDeg, 0);
  eq(f.hDeg, 0);
});

// README worked example: "sampling 3.76 um at 530 mm = 1.46 per pixel".
test("samplingArcsecPerPx: 3.76um pixels at 530mm -> 1.46\"/px", () => {
  near(samplingArcsecPerPx(3.76, 530), 1.46, 0.01);
});

test("samplingVerdict boundaries: >2 under-sampled, <0.7 over-sampled, else well sampled", () => {
  eq(samplingVerdict(2.5), "under-sampled");
  eq(samplingVerdict(1.46), "well sampled");
  eq(samplingVerdict(0.5), "over-sampled");
  eq(samplingVerdict(2), "well sampled", "exactly 2 is not > 2");
  eq(samplingVerdict(0.7), "well sampled", "exactly 0.7 is not < 0.7");
});

test("mosaicPitch: 15% overlap shrinks the pitch by 15%", () => {
  near(mosaicPitch(2.54, 0.15), 2.54 * 0.85, 1e-9);
});

test("panelOrder: 2x1 mosaic, pass 0 is row-major; pass 1 rotates the start", () => {
  const pass0 = panelOrder(2, 1, 0);
  eq(JSON.stringify(pass0), JSON.stringify([{ row: 0, col: 0 }, { row: 0, col: 1 }]));
  const pass1 = panelOrder(2, 1, 1);
  eq(JSON.stringify(pass1), JSON.stringify([{ row: 0, col: 1 }, { row: 0, col: 0 }]));
  const pass2 = panelOrder(2, 1, 2); // wraps back to pass 0's order
  eq(JSON.stringify(pass2), JSON.stringify(pass0));
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
