// Pure-lib test for fov.ts. Sabotage check: swapping the >2 / <0.7
// thresholds turns the sampling-verdict boundaries red; not rotating the
// base order turns "panelOrder: 2x1 mosaic, pass 0 is row-major; pass 1
// rotates the start" red.
//
// (No atan()-vs-linear claim here: `fovDeg` is a thin wrapper over
// `lib/framing.ts`'s `fovDegFromSensorMm`, which has always been the linear
// small-angle formula (`sensor/fl * (180/pi)`), not `2*atan(sensor/2/fl)` --
// there is no atan() call in this path to swap out. Measured, the two
// formulas agree to about 0.0005 deg at 23.5mm@530mm, well under this file's
// 0.01 tolerance, so a claim that swapping them would turn that worked
// example red was false.)
//
// That last case is this function's ONLY caller. It pins what `panelOrder`
// computes, not what a night does: the engine shoots a Plan mosaic's panels
// panel-first (#154), and the Sky copy that used to cite this order as the
// night's is pinned to panel-first in `hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts`.
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

// Re-pinned for WP-29 (#168): this used to assert the pre-#168 bug, that a
// 0.5x reducer multiplied into the effective focal length here and roughly
// doubled the field. `fovDeg` is now a thin wrapper over `lib/framing.ts`'s
// `fovDegFromSensorMm`, which never saw a reducer, and `config.py`'s
// `Optics.reducer` is documented as recorded-but-never-applied -- so a
// recorded reducer must leave `fovDeg`'s answer unchanged. See also
// `w3FovOneFormula.test.ts`, which pins the same thing against `fovFromOptics`
// directly.
test("fovDeg: a recorded reducer is ignored, matching config.py's documented semantics", () => {
  const base = fovDeg({ sensorWmm: 23.5, sensorHmm: 15.7, flMm: 530, reducer: 1 });
  const reduced = fovDeg({ sensorWmm: 23.5, sensorHmm: 15.7, flMm: 530, reducer: 0.5 });
  near(reduced.wDeg, base.wDeg, 1e-9, "a recorded reducer must not change the computed field");
  near(reduced.hDeg, base.hDeg, 1e-9, "a recorded reducer must not change the computed field");
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
