// cropRoi.test.ts — the load-bearing geometry behind the pixel-peep zoom.
// Pure lib only; the render shells (CropOverlay/LoupePanel/useCropZoom) are
// deliberately DOM-untested because all their logic lives here.
import {
  centerSensorRoi,
  cropCacheKey,
  cropQuery,
  displayNativeScale,
  LOUPE_BOX_MAX,
  LOUPE_BOX_MIN,
  LOUPE_CHROME_PX,
  LOUPE_CHROME_V_DENSE_PX,
  LOUPE_CHROME_V_PX,
  LOUPE_INSET_PX,
  LOUPE_STAGE_FRACTION_MAX,
  loupeBoxSize,
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

// ------------------------------------------------------------------ loupe fit
// The one piece of the loupe with real behaviour: how big its 1:1 box may be on
// a MEASURED stage (not the viewport — the stage is a panel inside a scrolling
// column). Scale down to a floor, then suppress and let the stage say why.
test("loupeBoxSize: full size on a roomy stage, unmeasured assumed roomy", () => {
  eq(loupeBoxSize(1200), LOUPE_BOX_MAX, "desktop stage");
  eq(loupeBoxSize(507), LOUPE_BOX_MAX, "just wide enough for full size");
  // ResizeObserver has not fired yet — must NOT flash the "too narrow" note.
  eq(loupeBoxSize(0), LOUPE_BOX_MAX, "unmeasured");
  eq(loupeBoxSize(-5), LOUPE_BOX_MAX, "nonsense width");
  eq(loupeBoxSize(NaN), LOUPE_BOX_MAX, "NaN width");
});

test("loupeBoxSize: scales down on a phone stage instead of suppressing", () => {
  // The reported regression: a 172px panel on a 360px stage was ~48% of it.
  eq(loupeBoxSize(360), 108, "360px stage");
  eq(loupeBoxSize(390), 120, "390px stage");
  eq(loupeBoxSize(412), 128, "412px stage");
  eq(loupeBoxSize(430), 132, "430px stage");
  eq((360 - (108 + LOUPE_CHROME_PX)) > 0, true, "still leaves room for the chip stack");
});

// THE 2026-08-08 DEFECT, as arithmetic. `main` pads 16, `.panel` pads 16 and
// draws a 1px border, so a phone hands the stage its viewport width minus 66.
// The old rule was `floor(stageW * 0.34) - 12`, suppressed below a 96px floor:
//   390px phone -> 324px stage -> 98  (survived by ONE pixel)
//   384px phone -> 318px stage -> 96  (survived by ZERO)
//   360px phone -> 294px stage -> 87  -> SUPPRESSED, on the device it is for.
// A 1:1 window is a pixel RATIO, so the answer is a smaller window, not none.
test("loupeBoxSize: the phone stages a 390/384/360px device actually produces", () => {
  eq(loupeBoxSize(324), 96, "390px phone, full stage");
  eq(loupeBoxSize(318), 96, "384px phone (Galaxy S25 Ultra), full stage");
  eq(loupeBoxSize(294), 88, "360px phone — was 0 before, and must never be again");
  eq(loupeBoxSize(254), 88, "320px phone, the narrowest device we lay out for");
});

test("loupeBoxSize: suppresses (0) only when the corner cannot hold the floor", () => {
  // The hard limit is a SHARE of the stage, not a fixed width: past half the
  // stage the loupe has stopped being an inset and become a split screen.
  const cut = Math.ceil((LOUPE_BOX_MIN + LOUPE_CHROME_PX) / LOUPE_STAGE_FRACTION_MAX);
  eq(cut, 200, "the suppression width, derived rather than chosen");
  eq(loupeBoxSize(200), LOUPE_BOX_MIN, "last width that still fits the floor");
  eq(loupeBoxSize(199), 0, "one px narrower suppresses");
  eq(loupeBoxSize(1), 0, "degenerate stage");
});

test("loupeBoxSize: HEIGHT suppresses too — the panel is taller than it is wide", () => {
  // A compact stage is 3:2 and floored at 230px, so on Focus the binding
  // constraint is vertical. Full layout needs box + 62 of chrome + 2x8 inset…
  eq(loupeBoxSize(800, 166), LOUPE_BOX_MIN, "exactly enough height for the floor");
  eq(loupeBoxSize(800, 165), 0, "one px shorter suppresses");
  eq(LOUPE_BOX_MIN + LOUPE_CHROME_V_PX + 2 * LOUPE_INSET_PX, 166, "…and that 166 is derived");
  // …and the DENSE layout (compact stage: the window is its own copy target,
  // the ROI is one line) needs 30px less, which is the whole reason it exists.
  eq(loupeBoxSize(800, 136, true), LOUPE_BOX_MIN, "dense fits where full does not");
  eq(loupeBoxSize(800, 136, false), 0, "the same height cannot hold the full layout");
  eq(loupeBoxSize(800, 135, true), 0, "one px shorter suppresses the dense one too");
  eq(LOUPE_BOX_MIN + LOUPE_CHROME_V_DENSE_PX + 2 * LOUPE_INSET_PX, 136, "…derived too");
  // Unmeasured height must not suppress — ResizeObserver has not fired yet.
  eq(loupeBoxSize(800, 0), LOUPE_BOX_MAX, "unmeasured height assumed roomy");
});

// The real Focus-on-a-phone geometry, end to end: a 390px phone gives a 324px
// stage; `compact` floors it at POD_MIN_STAGE_H = 230; FocusView declares that
// the pod disc owns the bottom 94px (POD_BOTTOM_PX + POD_DISC_PX), so the loupe
// gets 136px of height at the top. This is the number the whole fix turns on.
test("loupeBoxSize: a phone Focus stage, with the pod's corner reserved", () => {
  const stageW = 324, stageH = 230, podReserve = 38 + 56;
  const box = loupeBoxSize(stageW, stageH - podReserve, true);
  eq(box, LOUPE_BOX_MIN, "88px of real sensor pixels beats no magnifier at all");
  // …and the panel it sizes ends clear of the disc rather than on top of it.
  const panelBottom = LOUPE_INSET_PX + box + LOUPE_CHROME_V_DENSE_PX;
  eq(panelBottom <= stageH - podReserve, true,
    `panel bottom ${panelBottom} must clear the pod's top edge ${stageH - podReserve}`);
});

test("loupeBoxSize: monotonic, never over the stage budget, never a peephole", () => {
  let prev = 0;
  for (let w = 1; w <= 1400; w++) {
    const s = loupeBoxSize(w);
    if (w <= 0) continue;
    // 0 (suppressed) or a genuinely usable window — never something in between.
    eq(s === 0 || s >= LOUPE_BOX_MIN, true, `w=${w} size=${s} is 0 or usable`);
    eq(s <= LOUPE_BOX_MAX, true, `w=${w} never exceeds the full size`);
    if (s > 0) {
      eq(s % 4, 0, `w=${w} sits on the 4px lattice (whole-pixel crosshair)`);
      // the whole PANEL (box + chrome) stays inside the hard share ceiling
      eq(s + LOUPE_CHROME_PX <= Math.floor(w * LOUPE_STAGE_FRACTION_MAX), true,
        `w=${w} panel ${s + LOUPE_CHROME_PX} within the share ceiling`);
    }
    eq(s >= prev, true, `w=${w} never shrinks as the stage grows`);
    prev = s;
  }
  // …and monotonic in HEIGHT as well, at a width that is never the binding one.
  prev = 0;
  for (let h = 1; h <= 600; h++) {
    const s = loupeBoxSize(1200, h);
    eq(s === 0 || s >= LOUPE_BOX_MIN, true, `h=${h} size=${s} is 0 or usable`);
    eq(s >= prev, true, `h=${h} never shrinks as the stage grows`);
    prev = s;
  }
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
