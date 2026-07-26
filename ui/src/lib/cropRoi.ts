// cropRoi.ts — pure ROI math for the pixel-peep zoom (GET /api/preview/{id}/crop).
// (crop+render UI design §2.1, §4.1)
//
// The display base the stage paints is DOWNSCALED (server caps encode width at
// 1400). Past the point where one sensor pixel would cover more than one CSS
// pixel, the CSS upscale is a lie about focus/noise/stars — so we fetch a
// sensor-1:1 crop of exactly the visible box and paint it on top.
//
// Everything here is pure integer geometry so it can be tested without a DOM:
//  - visibleSensorRoi: inverse-transform the stage rect through the viewport,
//    convert display px -> sensor px, clamp inside the sensor, cap the area to
//    (about) what's actually on screen so a 60MP sensor never ships a full strip.
//  - quantizeRoi: snap to a lattice so small pans reuse the SAME request (this is
//    what makes the LRU cache hit and what stops a pinch from storming the server).
//  - cropCacheKey / cropQuery: the stable identity + query string for that ROI.

export interface CropRoi {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Everything the ROI math needs. All CSS px except data* (sensor px). */
export interface RoiGeom {
  /** viewport transform: translate(x,y) scale(scale), origin 0 0 */
  scale: number;
  x: number;
  y: number;
  /** stage (container) size */
  stageW: number;
  stageH: number;
  /** display image size (the ≤1400px base the stage paints) */
  dispW: number;
  dispH: number;
  /** native sensor dims */
  dataW: number;
  dataH: number;
}

/** Lattice the ROI snaps to. Bigger = fewer requests, more overdraw. */
export const CROP_GRID = 128;
/** Hard ceiling on either crop dimension (memory + PNG encode guard, R4). */
export const CROP_MAX_DIM = 2048;

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * The viewport scale at which one sensor pixel == one CSS pixel.
 *
 * The base is downscaled by `displayScale = dispW / dataW`, so a sensor pixel
 * covers `displayScale * viewport.scale` CSS px; that hits 1 at
 * `scale = dataW / dispW`.
 */
export function displayNativeScale(dispW: number, dataW: number): number {
  if (!dispW || !dataW) return 1;
  return dataW / dispW;
}

/**
 * Is a crop worth fetching? Only once the user has zoomed PAST display-native —
 * below that the base already has more real pixels than the screen shows, so we
 * issue zero requests (novice zoom "just works" with no traffic at all).
 */
export function shouldCrop(g: RoiGeom): boolean {
  if (!g.dispW || !g.dataW || !g.stageW || !g.stageH) return false;
  if (!(g.scale > 0)) return false;
  return g.scale > displayNativeScale(g.dispW, g.dataW) * (1 + 1e-3);
}

/**
 * The visible rectangle expressed in SENSOR pixels, clamped inside the sensor and
 * capped to ~the viewport's worth of pixels.
 *
 * Returns null when the geometry is degenerate or nothing is visible. The server
 * clamps too (app.py) but clamping here keeps the cache key stable and stops us
 * from asking for a 5000px strip when 400px is on screen.
 */
export function visibleSensorRoi(g: RoiGeom, maxDim: number = CROP_MAX_DIM): CropRoi | null {
  const { scale, stageW, stageH, dispW, dispH, dataW, dataH } = g;
  if (!(scale > 0) || dispW <= 0 || dispH <= 0 || dataW <= 0 || dataH <= 0) return null;
  if (stageW <= 0 || stageH <= 0) return null;

  const sx = dispW / dataW; // display px per sensor px (x)
  const sy = dispH / dataH;
  if (!(sx > 0) || !(sy > 0)) return null;

  // inverse-transform the stage rect into display space, clamped to the image
  const d0x = clamp((0 - g.x) / scale, 0, dispW);
  const d1x = clamp((stageW - g.x) / scale, 0, dispW);
  const d0y = clamp((0 - g.y) / scale, 0, dispH);
  const d1y = clamp((stageH - g.y) / scale, 0, dispH);
  if (d1x <= d0x || d1y <= d0y) return null;

  // display px -> sensor px, outward-rounded so we never under-cover the view
  let x = clamp(Math.floor(d0x / sx), 0, dataW - 1);
  let y = clamp(Math.floor(d0y / sy), 0, dataH - 1);
  let w = clamp(Math.ceil(d1x / sx) - x, 1, dataW - x);
  let h = clamp(Math.ceil(d1y / sy) - y, 1, dataH - y);

  // Cap the request to about what's on screen (+2px slop), then to maxDim. Keep
  // the cap centered on the visible box so the middle of the view stays sharp.
  const capW = Math.min(maxDim, Math.max(1, Math.ceil(stageW / (scale * sx)) + 2));
  const capH = Math.min(maxDim, Math.max(1, Math.ceil(stageH / (scale * sy)) + 2));
  if (w > capW) {
    x = clamp(x + Math.floor((w - capW) / 2), 0, Math.max(0, dataW - capW));
    w = Math.min(capW, dataW - x);
  }
  if (h > capH) {
    y = clamp(y + Math.floor((h - capH) / 2), 0, Math.max(0, dataH - capH));
    h = Math.min(capH, dataH - y);
  }

  if (w <= 0 || h <= 0) return null;
  return { x, y, w, h };
}

/**
 * A small, fixed-size ROI centred on the VIEWPORT CENTRE (Decision D).
 *
 * This is what the opt-in 1:1 loupe uses when the user has NOT zoomed past
 * display-native — the loupe's whole point is inspecting real sensor pixels
 * without having to zoom, and there is no visible-box crop to reuse yet. It is
 * deliberately tiny (a couple of hundred sensor px), so the extra traffic is a
 * single small PNG per pan-settle, and only while an expert has opted in.
 */
export function centerSensorRoi(g: RoiGeom, size: number): CropRoi | null {
  const { scale, stageW, stageH, dispW, dispH, dataW, dataH } = g;
  if (!(scale > 0) || dispW <= 0 || dispH <= 0 || dataW <= 0 || dataH <= 0) return null;
  if (stageW <= 0 || stageH <= 0) return null;
  const w = Math.max(1, Math.min(Math.round(size), dataW));
  const h = Math.max(1, Math.min(Math.round(size), dataH));
  // container centre -> display space -> sensor space
  const cx = Math.round(((stageW / 2 - g.x) / scale) * (dataW / dispW));
  const cy = Math.round(((stageH / 2 - g.y) / scale) * (dataH / dispH));
  return {
    x: clamp(cx - Math.floor(w / 2), 0, dataW - w),
    y: clamp(cy - Math.floor(h / 2), 0, dataH - h),
    w,
    h,
  };
}

/* ------------------------------------------------------------------ loupe fit
 * How big the 1:1 loupe box may be on a stage this wide.
 *
 * The loupe is a fixed-size panel pinned to the stage's bottom-right. At its
 * original 160px box (172px with chrome) it ate ~48% of a 360px phone stage and
 * ran straight into the bottom-left chip stack. The stage is NOT the viewport —
 * it is a panel inside a scrolling column, and `compact` is smaller again — so a
 * CSS media query keyed to viewport width is the wrong instrument. This is a
 * pure function of the MEASURED stage width (PreviewStage already runs a
 * ResizeObserver for `fitScale`; we reuse that measurement).
 *
 * SCALE FIRST, SUPPRESS ONLY AT THE FLOOR. "1:1" is a pixel RATIO, not a size:
 * a 108px window still shows 108 real sensor pixels, ~20x a typical star's FWHM
 * and plenty for the focus/noise check the loupe exists for. Silently rendering
 * nothing would leave the user staring at a Magnifier toggle they just pressed
 * that does nothing (house rule §11.8). Below `LOUPE_BOX_MIN` the crosshair, the
 * 1px border and the ROI caption stop framing anything useful, so we return 0
 * and the stage says WHY out loud instead of shrinking to a peephole.
 */
/** Full-size loupe box, CSS px (== sensor px at 1:1). */
export const LOUPE_BOX_MAX = 160;
/** Below this a 1:1 window is a peephole, not an inspection tool. */
export const LOUPE_BOX_MIN = 96;
/** Panel padding + border around the box. */
export const LOUPE_CHROME_PX = 12;
/** Widest share of the stage the whole loupe panel may occupy. */
export const LOUPE_STAGE_FRACTION = 0.34;

export function loupeBoxSize(stageW: number): number {
  // Unmeasured (ResizeObserver has not fired yet): assume it fits. Returning 0
  // here would flash the "too narrow" note for one commit on every mount.
  if (!(stageW > 0)) return LOUPE_BOX_MAX;
  const budget = Math.floor(stageW * LOUPE_STAGE_FRACTION) - LOUPE_CHROME_PX;
  if (budget >= LOUPE_BOX_MAX) return LOUPE_BOX_MAX;
  if (budget < LOUPE_BOX_MIN) return 0;
  // 4px lattice so the centre crosshair always lands on a whole pixel.
  return Math.floor(budget / 4) * 4;
}

/**
 * Snap an ROI outward onto a `grid` lattice and re-clamp inside the sensor.
 *
 * This is the anti-storm mechanism: a few-pixel pan produces the IDENTICAL ROI
 * (and therefore the identical cache key + URL), so it costs zero requests.
 */
export function quantizeRoi(roi: CropRoi, dataW: number, dataH: number, grid: number = CROP_GRID): CropRoi {
  const g = Math.max(1, Math.floor(grid));
  const x = clamp(Math.floor(roi.x / g) * g, 0, Math.max(0, dataW - 1));
  const y = clamp(Math.floor(roi.y / g) * g, 0, Math.max(0, dataH - 1));
  const x1 = clamp(Math.ceil((roi.x + roi.w) / g) * g, x + 1, dataW);
  const y1 = clamp(Math.ceil((roi.y + roi.h) / g) * g, y + 1, dataH);
  return { x, y, w: x1 - x, h: y1 - y };
}

/** Stable identity for the LRU cache. Frame id is part of it because the server
 *  keeps the linear array for only the latest 1–2 frames — a crop from an older
 *  id is not reusable, ever. */
export function cropCacheKey(previewId: number, roi: CropRoi): string {
  return `${previewId}:${roi.x}:${roi.y}:${roi.w}:${roi.h}`;
}

/** Query string for GET /api/preview/{id}/crop. */
export function cropQuery(roi: CropRoi): string {
  const p = new URLSearchParams();
  p.set("x", String(roi.x));
  p.set("y", String(roi.y));
  p.set("w", String(roi.w));
  p.set("h", String(roi.h));
  return `?${p.toString()}`;
}
