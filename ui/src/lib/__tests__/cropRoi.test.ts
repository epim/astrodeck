// cropRoi.test.ts — the load-bearing geometry behind the pixel-peep zoom.
// Pure lib only; the render shells (CropOverlay/LoupePanel/useCropZoom) are
// deliberately DOM-untested because all their logic lives here.
import {
  centerSensorRoi,
  cropCacheKey,
  cropQuery,
  displayNativeScale,
  quantizeRoi,
  shouldCrop,
  visibleSensorRoi,
  type CropRoi,
  type RoiGeom,
} from "../cropRoi";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void){try{fn();passed++;}catch(e){failed++;failures.push(`✗ ${n}: ${(e as Error).message}`);}}
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function roiEq(a: CropRoi | null, b: CropRoi | null, m=""){
  const s = (r: CropRoi | null) => (r ? `${r.x},${r.y} ${r.w}x${r.h}` : "null");
  if (s(a) !== s(b)) throw new Error(`${m} expected ${s(b)}, got ${s(a)}`);
}

// A 5600x4000 sensor encoded down to a 1400x1000 display base => sensor 1:1 lands
// at viewport.scale 4. Stage is 800x600 CSS px.
const base = { stageW: 800, stageH: 600, dispW: 1400, dispH: 1000, dataW: 5600, dataH: 4000 };
const g = (scale: number, x: number, y: number): RoiGeom => ({ ...base, scale, x, y });

test("displayNativeScale + shouldCrop gate: no request until past display-native", () => {
  eq(displayNativeScale(1400, 5600), 4);
  // fit / 100% / exactly-native => the base already has the pixels: ZERO traffic
  eq(shouldCrop(g(0.5, 0, 0)), false, "fit");
  eq(shouldCrop(g(1, 0, 0)), false, "100% of display base");
  eq(shouldCrop(g(4, 0, 0)), false, "exactly sensor-native");
  eq(shouldCrop(g(6, -2000, -1500)), true, "past native");
  // degenerate geometry never asks for a crop
  eq(shouldCrop({ ...g(6, 0, 0), stageW: 0 }), false, "unmeasured stage");
  eq(shouldCrop({ ...g(6, 0, 0), dataW: 0 }), false, "no sensor dims");
});

test("visibleSensorRoi: centred, corner-clamped, edge-clamped, area-capped", () => {
  // (a) zoomed + panned into the middle -> the visible box in sensor px
  roiEq(visibleSensorRoi(g(6, -2000, -1500)), { x: 1333, y: 1000, w: 534, h: 400 }, "centred");

  // (b) top-left corner: never negative, never before pixel 0
  roiEq(visibleSensorRoi(g(6, 0, 0)), { x: 0, y: 0, w: 534, h: 400 }, "top-left corner");

  // (c) bottom-right edge: clamped INSIDE the sensor (no over-read past 5600x4000)
  const far = visibleSensorRoi(g(6, -8000, -5400));
  roiEq(far, { x: 5333, y: 3600, w: 267, h: 400 }, "bottom-right edge");
  eq(far!.x + far!.w, 5600, "right edge");
  eq(far!.y + far!.h, 4000, "bottom edge");

  // (d) the area cap bounds the request to what's on screen, centred on the view.
  //     Visible box here is 0..791; a 256px cap must stay centred on ~395.
  const big: RoiGeom = { ...base, dataW: 11200, dataH: 8000, scale: 8.1, x: 0, y: 0 };
  const capped = visibleSensorRoi(big, 256)!;
  eq(capped.w, 256, "capped width");
  eq(capped.x, 267, "cap centred on the visible box");

  // (e) nothing visible / degenerate => null, so we never fetch
  eq(visibleSensorRoi({ ...g(6, 0, 0), dispW: 0 }), null, "no display dims");

  // (f) the loupe's bounded centre ROI (used when NOT zoomed past native):
  //     centred on the viewport centre, clamped, and never bigger than asked.
  roiEq(centerSensorRoi(g(0.5, 0, 0), 256), { x: 3072, y: 2272, w: 256, h: 256 }, "centre roi");
  roiEq(centerSensorRoi(g(6, 0, 0), 256), { x: 139, y: 72, w: 256, h: 256 }, "centre roi, zoomed");
  // pushed hard into a corner it clamps rather than over-reading
  roiEq(centerSensorRoi(g(6, 999999, 999999), 256), { x: 0, y: 0, w: 256, h: 256 }, "corner clamp");
  eq(centerSensorRoi({ ...g(6, 0, 0), dataW: 0 }, 256), null, "degenerate");
});

test("quantizeRoi: lattice snap survives a small pan (this is the anti-storm)", () => {
  const roi = { x: 1333, y: 1000, w: 534, h: 400 };
  const q = quantizeRoi(roi, base.dataW, base.dataH);
  roiEq(q, { x: 1280, y: 896, w: 640, h: 512 }, "snapped");

  // a few-pixel pan MUST land on the identical ROI => identical URL + cache key
  const q2 = quantizeRoi({ x: 1340, y: 1005, w: 534, h: 400 }, base.dataW, base.dataH);
  eq(cropCacheKey(7, q2), cropCacheKey(7, q), "stable key across a small pan");
  eq(cropCacheKey(7, q), "7:1280:896:640:512");
  // a DIFFERENT frame id is never a cache hit (linear is retained 1-2 frames)
  eq(cropCacheKey(8, q) === cropCacheKey(7, q), false, "id is part of identity");

  // snapping outward must still be clamped inside the sensor
  const edge = quantizeRoi({ x: 5333, y: 3600, w: 267, h: 400 }, base.dataW, base.dataH);
  roiEq(edge, { x: 5248, y: 3584, w: 352, h: 416 }, "edge snap");
  eq(edge.x + edge.w, 5600, "no over-read x");
  eq(edge.y + edge.h, 4000, "no over-read y");

  eq(cropQuery(q), "?x=1280&y=896&w=640&h=512");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
