// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The live panorama raster of the horizon scanner (SPEC-v2 3.4 types:raster, 4.11, D11, D12).
//
// 1080 x 300 equirectangular: column x is azimuth (x + 0.5) / 1080 * 360, clockwise from north in the render
// frame, and row y is altitude 90 - y / 299 * 100, so row 0 is the zenith and row 299 is -10 degrees. A slice is
// painted by the inverse mapping (each raster pixel looks back into the keyframe's strip), never forward, so no
// pixel is skipped or doubled whatever the pose. Weight is feather times class weight, and the class is only ever a
// weight (D11): an aligned slice over a sensor slice takes 1 / 1.01 of the pixel and a higher class never resets
// what a lower one painted, which is what keeps a seam between classes from becoming a hard edge.
import { DEG } from '../photosphereGeometry';
import { basisFromQuat, qnormalize } from './rotation';
import { PANO_H, PANO_W, PROFILE_BINS, PixClass } from './types';
import type { BandPanoramaLike, CameraBasis, DirtyRect, KeyClass, SliceSource } from './types';

const N = PANO_W * PANO_H;
const PLATEAU_DEG = 1;       // feather: weight 1 within +-1 degree of the slice centre (horizontal tangent angle)
const EDGE_DEG = 3;          // ... falling linearly to 0 at +-3
const TRAILING_MAX_DEG = 10; // SliceSource.trailingDeg is 0..10 (4.3 rule 6)
const CLS_FEATHER = 0.5;     // cls, sigma and coverage only count a pixel the slice holds with at least this feather
const SIGMA_UNIT_DEG = 0.02; // the sigma channel is a byte in these units, 255 = none or 5.1 degrees and above
const COVER_ALT_LO = -2;     // coverage looks at the altitude band the horizon lives in: -2 to +10
const COVER_ALT_HI = 10;
const EDGE_STEPS = 48;       // samples per edge of the tangent rectangle when bounding a slice
const MAX_DIRTY = 512;       // past this many pending rectangles the list collapses to the whole raster

/** Class weight multiplies the feather (D11). A sensor-placed slice still paints, it just loses every contest. */
export const CLASS_WEIGHT: Readonly<Record<KeyClass, number>> = { aligned: 1, blurred: 0.1, sensor: 0.01 };
const PIX_CLASS: Readonly<Record<KeyClass, PixClass>> = {
  aligned: PixClass.Aligned, blurred: PixClass.Blurred, sensor: PixClass.Sensor,
};

/** Weight at distance `d` degrees from the centre when the side extends by `trail`: 1 up to 1 + trail, 0 from 3 + trail. */
function featherAt(d: number, trail: number): number {
  const e = d - trail;
  if (!(e < EDGE_DEG)) return 0;
  return e <= PLATEAU_DEG ? 1 : (EDGE_DEG - e) / (EDGE_DEG - PLATEAU_DEG);
}

/** 1 within +-1 degree of the centre, linear to 0 at +-3 (4.11). Slices 4 degrees apart sum to exactly 1. */
export function featherWeight(tanAngleDeg: number): number {
  return featherAt(Math.abs(tanAngleDeg), 0);
}

// ---- Raster geometry, tabulated once ----------------------------------------

const rowAlt = (y: number) => 90 - y / (PANO_H - 1) * 100;
const rowOfAlt = (alt: number) => (90 - alt) * (PANO_H - 1) / 100;

const SIN_AZ = new Float64Array(PANO_W), COS_AZ = new Float64Array(PANO_W);
for (let x = 0; x < PANO_W; x++) {
  const a = (x + 0.5) / PANO_W * 360 * DEG;
  SIN_AZ[x] = Math.sin(a); COS_AZ[x] = Math.cos(a);
}
const SIN_ALT = new Float64Array(PANO_H), COS_ALT = new Float64Array(PANO_H);
for (let y = 0; y < PANO_H; y++) {
  const a = rowAlt(y) * DEG;
  SIN_ALT[y] = Math.sin(a); COS_ALT[y] = Math.cos(a);
}
/** A coverage cell is 0.5 degree, 1.5 columns, so each column belongs to the cell its centre falls in (cells of 1 and 2 columns alternate). */
const COL_CELL = new Uint16Array(PANO_W);
for (let x = 0; x < PANO_W; x++) COL_CELL[x] = Math.floor((x + 0.5) * PROFILE_BINS / PANO_W);
let coverY0 = 0, coverY1 = PANO_H - 1;
while (rowAlt(coverY0) > COVER_ALT_HI) coverY0++;
while (rowAlt(coverY1) < COVER_ALT_LO) coverY1--;
const ALT0_ROW = Math.round(rowOfAlt(0));

const fullRect = (): DirtyRect => ({ x: 0, y: 0, w: PANO_W, h: PANO_H });

// ---- Bounding a slice ---------------------------------------------------------

/** The columns (from `xStart`, `n` of them, wrapping) and rows (`y0`..`y1`) a slice can touch, or null when none are in the raster.
 *  The slice is the cone |x / depth| inside the horizontal limits and |y / depth| <= tanV, a convex region bounded by great
 *  circles. Its extremes lie on that boundary, which is sampled 48 times per edge and padded by a row and two columns for the
 *  sag between samples. A region holding the zenith spans every azimuth, and an azimuth range is measured from the axis heading
 *  so that it never straddles the 0/360 seam. */
function sliceBounds(b: CameraBasis, tanL: number, tanR: number, tanV: number):
    { xStart: number; n: number; y0: number; y1: number } | null {
  const { right, up, forward } = b;
  let pole = false;
  if (forward[2] > 1e-9) {
    const zx = right[2] / forward[2], zy = up[2] / forward[2];
    pole = zx >= -tanL && zx <= tanR && Math.abs(zy) <= tanV;
  }
  const heading = Math.atan2(forward[0], forward[1]);
  let altMax = -Math.PI, altMin = Math.PI, dMin = Infinity, dMax = -Infinity;
  const visit = (xt: number, yt: number) => {
    const wx = right[0] * xt + up[0] * yt + forward[0];
    const wy = right[1] * xt + up[1] * yt + forward[1];
    const wz = right[2] * xt + up[2] * yt + forward[2];
    const alt = Math.atan2(wz, Math.hypot(wx, wy));
    if (alt > altMax) altMax = alt;
    if (alt < altMin) altMin = alt;
    let d = Math.atan2(wx, wy) - heading;
    d -= 2 * Math.PI * Math.round(d / (2 * Math.PI));
    if (d < dMin) dMin = d;
    if (d > dMax) dMax = d;
  };
  for (let i = 0; i <= EDGE_STEPS; i++) {
    const u = i / EDGE_STEPS, xt = -tanL + (tanL + tanR) * u, yt = -tanV + 2 * tanV * u;
    visit(xt, -tanV); visit(xt, tanV); visit(-tanL, yt); visit(tanR, yt);
  }
  const top = pole ? 0 : Math.floor(rowOfAlt(altMax / DEG)) - 1;
  const bottom = Math.ceil(rowOfAlt(altMin / DEG)) + 1;
  if (top > PANO_H - 1 || bottom < 0) return null;
  const y0 = Math.max(0, top), y1 = Math.min(PANO_H - 1, bottom);
  if (pole) return { xStart: 0, n: PANO_W, y0, y1 };
  const c0 = Math.floor((heading + dMin) / (2 * Math.PI) * PANO_W - 0.5) - 2;
  const c1 = Math.ceil((heading + dMax) / (2 * Math.PI) * PANO_W - 0.5) + 2;
  const n = c1 - c0 + 1;
  if (n >= PANO_W) return { xStart: 0, n: PANO_W, y0, y1 };
  return { xStart: ((c0 % PANO_W) + PANO_W) % PANO_W, n, y0, y1 };
}

// ---- Azimuth shifts -----------------------------------------------------------

/** out[x] = in[x - n] * (1 - t) + in[x - n - 1] * t: a shift of n + t columns toward larger azimuth, which ADDS deg to every azimuth. */
interface ShiftPlan { colA: Int32Array; colB: Int32Array; t: number }

/** Null when the shift is a whole number of turns. A shift within 1e-9 of a whole column is snapped, so 1 degree is exactly 3 columns. */
function planShift(deg: number): ShiftPlan | null {
  if (!Number.isFinite(deg)) throw new RangeError('shift: deg must be finite');
  let cols = deg * (PANO_W / 360);
  const whole = Math.round(cols);
  if (Math.abs(cols - whole) < 1e-9) cols = whole;
  cols = ((cols % PANO_W) + PANO_W) % PANO_W;
  if (cols === 0) return null;
  const n = Math.floor(cols), t = cols - n;
  const colA = new Int32Array(PANO_W), colB = new Int32Array(PANO_W);
  for (let x = 0; x < PANO_W; x++) {
    colA[x] = (x - n + PANO_W) % PANO_W;
    colB[x] = (x - n - 1 + 2 * PANO_W) % PANO_W;
  }
  return { colA, colB, t };
}

/** Pure azimuth shift of a PANO_W x PANO_H raster. A whole-column shift is an exact copy. A fractional one is a linear
 *  resample of RGB between the two source columns, taken only where BOTH are observed: a half-seen pixel stays unseen
 *  rather than being blended with black, so alpha stays 0 or 255. */
export function shiftRaster(rgba: Uint8ClampedArray, deg: number): Uint8ClampedArray {
  if (rgba.length !== N * 4) throw new RangeError('shiftRaster: expected a PANO_W x PANO_H RGBA raster');
  const out = new Uint8ClampedArray(rgba.length);
  const plan = planShift(deg);
  if (plan === null) { out.set(rgba); return out; }
  const { colA, colB, t } = plan, s = 1 - t;
  for (let y = 0; y < PANO_H; y++) {
    const row = y * PANO_W;
    for (let x = 0; x < PANO_W; x++) {
      const o = (row + x) * 4, a = (row + colA[x]) * 4;
      if (t === 0) {
        out[o] = rgba[a]; out[o + 1] = rgba[a + 1]; out[o + 2] = rgba[a + 2]; out[o + 3] = rgba[a + 3];
        continue;
      }
      const b = (row + colB[x]) * 4;
      if (rgba[a + 3] === 0 || rgba[b + 3] === 0) continue;
      out[o] = rgba[a] * s + rgba[b] * t;
      out[o + 1] = rgba[a + 1] * s + rgba[b + 1] * t;
      out[o + 2] = rgba[a + 2] * s + rgba[b + 2] * t;
      out[o + 3] = 255;
    }
  }
  return out;
}

/** One channel of the same resample: `mix` joins the two source values of a fractional position, and a whole-column shift copies. */
function shiftChannel<T extends Uint8Array | Float32Array>(arr: T, plan: ShiftPlan, mix: (a: number, b: number, t: number) => number): void {
  const src = arr.slice() as T;
  const { colA, colB, t } = plan;
  for (let y = 0; y < PANO_H; y++) {
    const row = y * PANO_W;
    for (let x = 0; x < PANO_W; x++) {
      const a = src[row + colA[x]];
      arr[row + x] = t === 0 ? a : mix(a, src[row + colB[x]], t);
    }
  }
}

// ---- The raster ---------------------------------------------------------------

export class BandPanorama implements BandPanoramaLike {
  readonly rgba = new Uint8ClampedArray(N * 4);
  readonly cls = new Uint8Array(N);
  readonly sigma = new Uint8Array(N).fill(255);
  readonly firstSeen = new Uint16Array(N);
  readonly coverage = new Uint8Array(PROFILE_BINS);
  // Float32 sums of w g c per channel and of w: rgba is their quotient, so a pixel is never more than the sum of what was painted.
  private readonly sumR = new Float32Array(N);
  private readonly sumG = new Float32Array(N);
  private readonly sumB = new Float32Array(N);
  private readonly sumW = new Float32Array(N);
  private originMs: number | null = null;
  private dirty: DirtyRect[] = [];
  private allDirty = false;

  constructor() {}

  /** A new scan: everything is wiped, firstSeen included, and the clock for firstSeen starts at `nowMs`. */
  begin(nowMs: number): void {
    this.originMs = nowMs;
    this.firstSeen.fill(0);
    this.wipe();
  }

  /** Before a full re-render. The pixels go, `firstSeen` stays: it records when the user's sweep first reached a cell, not when the render did. */
  clear(): void {
    this.wipe();
  }

  /** Paints one slice by the inverse mapping and returns the rectangle it touched (x may run past 1080 and wrap; w and h are 0 when nothing was painted). */
  paint(s: SliceSource, nowMs: number): DirtyRect {
    const k = s.k0;
    if (s.strip.length < s.stripW * s.stripH * 3) throw new RangeError('paint: the strip is shorter than stripW x stripH x 3');
    if (!(k.f > 0) || !(k.h > 0)) throw new RangeError('paint: k0 needs a positive focal length and height');
    const [gR, gG, gB] = s.gainRGB;
    if (!(Number.isFinite(gR) && Number.isFinite(gG) && Number.isFinite(gB))) throw new RangeError('paint: gainRGB must be finite');
    const basis = basisFromQuat(qnormalize(s.pose));
    if (!Number.isFinite(basis.forward[0] + basis.forward[1] + basis.forward[2])) throw new RangeError('paint: pose must be finite');
    const { right, up, forward } = basis;

    // The vertical limit is 0.9 tan(long / 2), and tan(long / 2) = (h / 2) / f.
    const tanV = s.topFrac * (k.h / 2) / k.f;
    // Each side's limit is 3 degrees, plus the trailing fill on the one side it applies to.
    const trail = s.trailingSide === 0 ? 0 : Math.min(TRAILING_MAX_DEG, Math.max(0, s.trailingDeg || 0));
    const trailL = s.trailingSide === -1 ? trail : 0, trailR = s.trailingSide === 1 ? trail : 0;
    const tanL = Math.tan((EDGE_DEG + trailL) * DEG), tanR = Math.tan((EDGE_DEG + trailR) * DEG);

    const bounds = sliceBounds(basis, tanL, tanR, tanV);
    if (bounds === null) return { x: 0, y: 0, w: 0, h: 0 };

    const cl = PIX_CLASS[s.cls], cw = CLASS_WEIGHT[s.cls];
    const sigmaUnits = s.sigmaDeg >= 0 ? Math.min(255, Math.round(s.sigmaDeg / SIGMA_UNIT_DEG)) : 255;
    const stamp = this.stamp(nowMs);
    const { strip, stripW, stripH, stripX0 } = s;
    const { rgba, cls, sigma, firstSeen, coverage, sumR, sumG, sumB, sumW } = this;

    let jMin = bounds.n, jMax = -1, yMin = PANO_H, yMax = -1;
    for (let y = bounds.y0; y <= bounds.y1; y++) {
      const ca = COS_ALT[y], sa = SIN_ALT[y], row = y * PANO_W;
      const inCover = y >= coverY0 && y <= coverY1;
      for (let j = 0, x = bounds.xStart; j < bounds.n; j++, x = x + 1 === PANO_W ? 0 : x + 1) {
        // 1. the ray, from the column and row tables; 2. one rotation into the camera (the transpose of the basis).
        const wx = SIN_AZ[x] * ca, wy = COS_AZ[x] * ca, wz = sa;
        const depth = wx * forward[0] + wy * forward[1] + wz * forward[2];
        if (!(depth > 1e-9)) continue;
        // 3-4. the tangent coordinates, and the horizontal test: left of the axis is the image's left side.
        const xt = (wx * right[0] + wy * right[1] + wz * right[2]) / depth;
        if (xt <= -tanL || xt >= tanR) continue;
        // 5. the vertical test.
        const yt = (wx * up[0] + wy * up[1] + wz * up[2]) / depth;
        if (yt < -tanV || yt > tanV) continue;
        const feather = featherAt(Math.abs(Math.atan(xt)) / DEG, xt < 0 ? trailL : trailR);
        if (!(feather > 0)) continue;
        // 6. bilinear sample of the strip. Pixel i spans [i, i+1), so its centre is at i + 0.5; a point inside the strip's
        //    own extent samples with the edge pixel repeated, and a point outside it is skipped.
        const sx = k.cx + k.f * xt - stripX0, sy = k.cy - k.f * yt;
        if (!(sx >= 0 && sx < stripW && sy >= 0 && sy < stripH)) continue;
        const fx = sx - 0.5, fy = sy - 0.5;
        const ix = Math.floor(fx), iy = Math.floor(fy), ux = fx - ix, uy = fy - iy;
        const x0 = ix < 0 ? 0 : ix, x1 = ix + 1 > stripW - 1 ? stripW - 1 : ix + 1;
        const y0 = iy < 0 ? 0 : iy, y1 = iy + 1 > stripH - 1 ? stripH - 1 : iy + 1;
        const p00 = (y0 * stripW + x0) * 3, p10 = (y0 * stripW + x1) * 3, p01 = (y1 * stripW + x0) * 3, p11 = (y1 * stripW + x1) * 3;
        const w00 = (1 - ux) * (1 - uy), w10 = ux * (1 - uy), w01 = (1 - ux) * uy, w11 = ux * uy;
        const r = strip[p00] * w00 + strip[p10] * w10 + strip[p01] * w01 + strip[p11] * w11;
        const g = strip[p00 + 1] * w00 + strip[p10 + 1] * w10 + strip[p01 + 1] * w01 + strip[p11 + 1] * w11;
        const b = strip[p00 + 2] * w00 + strip[p10 + 2] * w10 + strip[p01 + 2] * w01 + strip[p11 + 2] * w11;
        // 7. accumulate: weight is feather times class weight.
        const i = row + x, w = feather * cw;
        sumR[i] += w * gR * r; sumG[i] += w * gG * g; sumB[i] += w * gB * b; sumW[i] += w;
        const total = sumW[i], o = i * 4;
        rgba[o] = sumR[i] / total; rgba[o + 1] = sumG[i] / total; rgba[o + 2] = sumB[i] / total; rgba[o + 3] = 255;
        if (firstSeen[i] === 0) firstSeen[i] = stamp;
        if (feather >= CLS_FEATHER) {
          if (cls[i] < cl) cls[i] = cl;
          if (sigma[i] > sigmaUnits) sigma[i] = sigmaUnits;
          if (inCover && coverage[COL_CELL[x]] < cl) coverage[COL_CELL[x]] = cl;
        }
        if (j < jMin) jMin = j;
        if (j > jMax) jMax = j;
        if (y < yMin) yMin = y;
        if (y > yMax) yMax = y;
      }
    }
    if (jMax < 0) return { x: 0, y: 0, w: 0, h: 0 };
    const rect = { x: (bounds.xStart + jMin) % PANO_W, y: yMin, w: jMax - jMin + 1, h: yMax - yMin + 1 };
    this.markDirty(rect);
    return rect;
  }

  takeDirty(): DirtyRect[] {
    const out = this.dirty;
    this.dirty = [];
    this.allDirty = false;
    return out;
  }

  /** The painted run of column `x` that contains altitude 0 (the row nearest it), or null when that pixel was never seen. */
  observedRun(x: number): { topAlt: number; bottomAlt: number } | null {
    const col = ((Math.floor(x) % PANO_W) + PANO_W) % PANO_W;
    const seen = (y: number) => this.rgba[(y * PANO_W + col) * 4 + 3] !== 0;
    if (!seen(ALT0_ROW)) return null;
    let top = ALT0_ROW, bottom = ALT0_ROW;
    while (top > 0 && seen(top - 1)) top--;
    while (bottom < PANO_H - 1 && seen(bottom + 1)) bottom++;
    return { topAlt: rowAlt(top), bottomAlt: rowAlt(bottom) };
  }

  /** Adds `deg` to every azimuth by resampling columns of rgba, cls and sigma (see `shiftRaster`; a fractional position takes the
   *  lower class and the larger sigma of its two source columns). The accumulators move with them so a later paint stays coherent,
   *  coverage is recomputed from cls, and `firstSeen` stays put: it is the scan-frame record of when a cell was first reached. */
  shiftAzimuth(deg: number): void {
    const plan = planShift(deg);
    if (plan === null) return;
    this.rgba.set(shiftRaster(this.rgba, deg));
    shiftChannel(this.cls, plan, (a, b) => (a < b ? a : b));
    shiftChannel(this.sigma, plan, (a, b) => (a > b ? a : b));
    const lerp = (a: number, b: number, t: number) => a * (1 - t) + b * t;
    shiftChannel(this.sumR, plan, lerp); shiftChannel(this.sumG, plan, lerp);
    shiftChannel(this.sumB, plan, lerp); shiftChannel(this.sumW, plan, lerp);
    this.recomputeCoverage();
    this.markAllDirty();
  }

  /** The raster as a PNG data URL on `canvas`, which is resized to PANO_W x PANO_H. Unseen pixels stay transparent. */
  toPNG(canvas: HTMLCanvasElement): string {
    canvas.width = PANO_W; canvas.height = PANO_H;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Could not prepare the panorama.');
    const image = ctx.createImageData(PANO_W, PANO_H);
    image.data.set(this.rgba);
    ctx.putImageData(image, 0, 0);
    return canvas.toDataURL('image/png');
  }

  private wipe(): void {
    this.rgba.fill(0); this.cls.fill(0); this.sigma.fill(255); this.coverage.fill(0);
    this.sumR.fill(0); this.sumG.fill(0); this.sumB.fill(0); this.sumW.fill(0);
    this.markAllDirty();
  }

  /** Deciseconds since begin(), at least 1 because 0 means never seen. The first paint of a raster that was never begun sets the origin. */
  private stamp(nowMs: number): number {
    if (this.originMs === null) this.originMs = nowMs;
    const ds = Math.round((nowMs - this.originMs) / 100);
    return ds >= 1 ? Math.min(65535, ds) : 1;
  }

  private recomputeCoverage(): void {
    this.coverage.fill(0);
    for (let y = coverY0; y <= coverY1; y++) {
      for (let x = 0; x < PANO_W; x++) {
        const c = this.cls[y * PANO_W + x], cell = COL_CELL[x];
        if (this.coverage[cell] < c) this.coverage[cell] = c;
      }
    }
  }

  private markDirty(r: DirtyRect): void {
    if (this.allDirty) return;
    this.dirty.push(r);
    if (this.dirty.length > MAX_DIRTY) this.markAllDirty();
  }

  private markAllDirty(): void {
    this.dirty = [fullRect()];
    this.allDirty = true;
  }
}
