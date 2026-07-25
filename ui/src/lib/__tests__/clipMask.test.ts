// clipMask.test.ts — the per-pixel saturation test (Tier 2 of the clip mask).
// The threshold decision (== 255 on the auto-stretched 8-bit crop) is the whole
// honesty claim, so it gets pinned here.
import { CLIP_RGB, maskToRgba, saturationMask } from "../clipMask";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void){try{fn();passed++;}catch(e){failed++;failures.push(`✗ ${n}: ${(e as Error).message}`);}}
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

/** RGBA (L-mode: R==G==B) for a list of grey values. */
function rgba(greys: number[]): Uint8ClampedArray {
  const out = new Uint8ClampedArray(greys.length * 4);
  greys.forEach((v, i) => { out[i*4] = v; out[i*4+1] = v; out[i*4+2] = v; out[i*4+3] = 255; });
  return out;
}

test("saturationMask: only a clipped (255) pixel counts; 254 does not", () => {
  //           saturated  near-clip  midtone  saturated
  const buf = rgba([255, 254, 128, 255]);
  const r = saturationMask(buf, 2, 2);
  eq(r.mask.join(""), "1001", "mask");
  eq(r.count, 2, "count");
  eq(r.fraction, 0.5, "fraction");

  // the boundary is inclusive at the threshold, exclusive below it
  eq(saturationMask(buf, 2, 2, 254).mask.join(""), "1101", "threshold 254");
  eq(saturationMask(buf, 2, 2, 129).count, 3, "threshold 129");

  // a clean crop paints nothing at all (the caller skips the whole raster)
  eq(saturationMask(rgba([0, 10, 200, 254]), 2, 2).count, 0, "no saturation");
  eq(saturationMask(rgba([]), 0, 0).fraction, 0, "empty crop is not a divide-by-zero");
});

test("maskToRgba: amber where saturated, FULLY TRANSPARENT elsewhere", () => {
  const out = maskToRgba(Uint8Array.from([1, 0]));
  eq(out.length, 8, "rgba length");
  eq([out[0], out[1], out[2], out[3]].join(","), `${CLIP_RGB.join(",")},255`, "saturated px");
  // untouched pixels must stay clear so the real image shows through — we tint,
  // we never repaint the frame.
  eq([out[4], out[5], out[6], out[7]].join(","), "0,0,0,0", "clean px");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
