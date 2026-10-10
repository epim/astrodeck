// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Pair alignment for the horizon panorama scanner (SPEC-v2 4.6, and the closure window of 4.9): the b-from-a camera
// rotation qBA between a template keyframe a and a candidate frame b, starting from the predictor's qBA.
//
//   1. Prewarp. a's L2 is warped into b's predicted view, so what is left between the two is the predictor's error.
//      warpLevel takes the map from OUT pixels to SOURCE pixels, so the prewarp passes
//      rotationHomography(k2, k2, qinv(qPred)), and sampling b at the template's pixels passes
//      rotationHomography(k, k, qBA) (ruling S5).
//   2. Phase correlation of the prewarped a against b at L2: a 32 x 64 window centred on the predicted overlap
//      ('keyframe'), or the full L2 frames padded with NaN to 64 x 128 ('closure'; never luma 0, S5). The shift turns
//      the predicted ray at the window's centre of content into the measured one, and that rotation of b's rays seeds
//      LK. PSR grades the peak; it is not a wrong-match rejector (S4), so ZNCC and the inlier test carry the rejection
//      here and the innovation gate does in the tracker (4.7).
//   3. Inverse-compositional LK on the three rotation increments: 4 iterations at L2, then 2 at L1, each level ending
//      early when the update is under 0.002 degrees. The Jacobian frame and sign and the update rule are pyramid.ts's:
//      hess d = sum jac^T e and q <- qmul(q, qinv(expSO3(d))). The photometric model is b = gain a + bias, so the
//      template term is scaled by the gain, the Hessian by gain squared and the right-hand side by the gain. Huber
//      weights at 1.345 x 1.4826 x MAD apply from each level's second iteration (see step 3 below for why not its
//      first). The Hessian is rebuilt every iteration over the valid pixels from Template jac, with those weights (S5),
//      never taken from Template hess, which sums the whole level.
//   4. Acceptance: every test of 4.6 step 4 (or of the closure window) must pass. The reason reported is the first
//      that fails, in the order of AlignResult.reason: texture, no-peak, overlap, diverged, low-zncc, few-inliers.
//   5. Outputs: the covariance sigma_r^2 (gain^2 H)^-1 with no focal term, up to 16 correspondences, and the RGB
//      overlap statistics for the chained gains (4.10).
//
// The gain is the ratio of the luma standard deviations over the overlap and the bias matches the means, both in
// closed form each iteration (over the Huber weights when they apply). A least-squares regression of b on a would give
// ZNCC x that ratio instead: it shrinks with any misalignment, so an early step divided by it overshoots, and on a
// textured wrong match it would scale the texture test's gain^2 H toward zero and report a mismatch as no texture,
// which would keep the 'mismatch' cue (2.12, a mismatch is textured && !ok) from ever firing. The ratio of spreads is
// what the two images agree on whether or not they are aligned.
//
// Allocation: the per-size work buffers and the two phase-correlation scratches are made once and reused, so a call
// allocates only its result, the correspondences and a few small arrays.
import { pixelLuminance } from '../photosphereGeometry';
import { makePhaseScratch, phaseCorrelate } from './phaseCorrelate';
import { warpLevel } from './pyramid';
import { angleBetweenDeg, expSO3, qinv, qmul, qnormalize, rotationHomography, unprojectPixel } from './rotation';
import type {
  AlignResult, AlignWindow, Correspondence, Intrinsics, Level, PhaseScratch, Pyramid, Quat, Template, TemplateLevel, V3,
} from './types';

export const ACCEPT: Readonly<{ psrMin: 7; overlapMin: 0.5; znccMin: 0.7; inlierMin: 0.6; closureZnccMin: 0.8; closureMaxDeg: 12 }> = {
  psrMin: 7, overlapMin: 0.5, znccMin: 0.7, inlierMin: 0.6, closureZnccMin: 0.8, closureMaxDeg: 12,
};

/** The texture test: gain^2 trace(H) / N over the valid template pixels at L1, in (luma per radian)^2 per pixel, must
 *  be above this. Calibrated per 4.6 step 4 on the two synth.ts fixtures of align.test.ts: the 180 x 320 analysis frame
 *  at the 70-degree prior (fNorm 1.2694, f = 114.2 px at L1), the template at the ring pitch 22.98, the candidate a
 *  4-degree ring step on, both under the simulator's frame noise (sd 2 per channel, 13.5):
 *    - a blank overcast frame (a smooth grey sky darkening 25 % toward the zenith, no skyline in view): 10,900. An
 *      exactly flat frame reads 0, and 4,700 under the same noise;
 *    - a thin treeline under smooth sky (synth.ts's seed-3 skyline squeezed into 1 to 6 degrees, flat and featureless
 *      below its jagged top, sky above): 400,000 at gain 1, and 196,000 when the candidate is at gain 0.7.
 *  The floor is the geometric mean of the two, 66,000: 6.0 times the overcast frame and 6.1 times below the treeline,
 *  so a floor raised 10x (660,000) fails the treeline, and the treeline at gain 0.7 still passes by 3.0 times. Noise
 *  alone grows as its variance (4,700 at sd 2), so it would take sd 7.5 per channel to pass on its own. The test
 *  scales with f^2 (it is per radian), so another lens moves every number here together. GRADIENT_FLOOR is not reused
 *  (4.6): it rejected that treeline. */
export const TEXTURE_FLOOR = 66000;

export interface RgbStrip { strip: Uint8Array; x0: number; w: number; h: number }   // L0 RGB strip of a keyframe

// ---- Constants ---------------------------------------------------------------------------------------------------------

const DEG = Math.PI / 180;
/** Phase-correlation windows at L2 (4.6 step 2, 4.9). */
const KEY_W = 32, KEY_H = 64, CLOSURE_W = 64, CLOSURE_H = 128;
/** LK iterations per level and the early exit (4.6 step 3). */
const ITERS_L2 = 4, ITERS_L1 = 2, EARLY_EXIT_DEG = 0.002;
/** Huber's 95 % efficiency constant, and a normal distribution's sd over its MAD. */
const HUBER_K = 1.345, MAD_TO_SD = 1.4826;
/** Inliers lie within 2.5 sigma (4.6 step 4); a correspondence within 1.5 sigma (step 5). */
const INLIER_SIGMAS = 2.5, CORR_SIGMAS = 1.5;
/** The residual sigma is never taken below half a luma level: two Uint8 images, one of them resampled, disagree by
 *  about 0.4 levels where they show the same smooth sky, and a MAD of a few exact zeros would otherwise make every
 *  rounding error an outlier and the covariance zero. */
const SIGMA_FLOOR = 0.5;
/** A level with fewer valid template pixels than this cannot carry three rotations, a gain and a bias. */
const MIN_VALID = 64;
/** A Cholesky pivot this small next to the trace means the Hessian has a direction with no information. */
const PIVOT_REL = 1e-9;
/** An L1 step still this large when the iterations run out has not reached the 0.05 degrees per pair that 4.6 step 3
 *  expects (0.1 px at L1), so the estimate is not trusted. Converged pairs end at most 0.023 degrees (88 synthetic
 *  pairs: the injected grid, 21 thin treelines, textured scenes and closures, all under the simulator's noise). */
const DIVERGE_STEP_DEG = 0.05;
/** Overlap means use only pixels with luma 16..240 in both images (4.6 step 5), clear of the crushed and clipped ends. */
const LUMA_LO = 16, LUMA_HI = 240;
/** The 4 x 4 grids of the overlap behind gainRGB and the correspondences, and the strip pixels a grid cell needs before
 *  its ratio counts toward the median. */
const GRID = 4, MIN_CELL_PIXELS = 16;
const IDENTITY: Quat = [1, 0, 0, 0];

// ---- Work buffers ------------------------------------------------------------------------------------------------------

interface LevelScratch { warped: Float32Array; dev: Float32Array; weight: Float32Array; sel: Float64Array }
const LEVEL_SCRATCH = new Map<number, LevelScratch>();
function levelScratch(n: number): LevelScratch {
  let s = LEVEL_SCRATCH.get(n);
  if (!s) {
    s = { warped: new Float32Array(n), dev: new Float32Array(n), weight: new Float32Array(n), sel: new Float64Array(n) };
    LEVEL_SCRATCH.set(n, s);
  }
  return s;
}
const PREWARP = new Map<number, Float32Array>();
function prewarpBuffer(n: number): Float32Array {
  let b = PREWARP.get(n);
  if (!b) { b = new Float32Array(n); PREWARP.set(n, b); }
  return b;
}
interface PhaseWindow { scratch: PhaseScratch; a: Float32Array; b: Float32Array }
const PHASE = new Map<number, PhaseWindow>();
function phaseWindow(w: 32 | 64, h: 64 | 128): PhaseWindow {
  let p = PHASE.get(w);
  if (!p) { p = { scratch: makePhaseScratch(w, h), a: new Float32Array(w * h), b: new Float32Array(w * h) }; PHASE.set(w, p); }
  return p;
}
const PAIRS = new Map<number, Int32Array>();
function pairBuffer(n: number): Int32Array {
  let b = PAIRS.get(n);
  if (!b) { b = new Int32Array(2 * n); PAIRS.set(n, b); }
  return b;
}

// ---- Small maths -------------------------------------------------------------------------------------------------------

/** The k-th smallest of the first n entries of a, which it reorders (quickselect, median-of-three pivot). */
function select(a: Float64Array, n: number, k: number): number {
  let lo = 0, hi = n - 1;
  while (hi > lo) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < a[lo]) { const t = a[mid]; a[mid] = a[lo]; a[lo] = t; }
    if (a[hi] < a[lo]) { const t = a[hi]; a[hi] = a[lo]; a[lo] = t; }
    if (a[hi] < a[mid]) { const t = a[hi]; a[hi] = a[mid]; a[mid] = t; }
    const pivot = a[mid];
    let i = lo, j = hi;
    while (i <= j) {
      while (a[i] < pivot) i++;
      while (a[j] > pivot) j--;
      if (i <= j) { const t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
    }
    if (k <= j) hi = j; else if (k >= i) lo = i; else return a[k];
  }
  return a[k];
}

/** Median of the first n entries of a (n > 0), which it reorders; the mean of the two middle values when n is even. */
function median(a: Float64Array, n: number): number {
  const m = n >> 1, upper = select(a, n, m);
  if (n & 1) return upper;
  let lower = -Infinity;
  for (let i = 0; i < m; i++) if (a[i] > lower) lower = a[i];
  return (lower + upper) / 2;
}

/** Cholesky factor of a symmetric 3x3 (row-major), lower triangle as [l00, l10, l11, l20, l21, l22]; null when a pivot
 *  is not clearly positive, which is a direction the data does not constrain. */
function cholesky3(m: Float64Array): number[] | null {
  const tr = m[0] + m[4] + m[8];
  if (!(tr > 0)) return null;
  const eps = PIVOT_REL * tr;
  const d0 = m[0];
  if (!(d0 > eps)) return null;
  const l00 = Math.sqrt(d0), l10 = m[3] / l00, l20 = m[6] / l00;
  const d1 = m[4] - l10 * l10;
  if (!(d1 > eps)) return null;
  const l11 = Math.sqrt(d1), l21 = (m[7] - l20 * l10) / l11;
  const d2 = m[8] - l20 * l20 - l21 * l21;
  if (!(d2 > eps)) return null;
  return [l00, l10, l11, l20, l21, Math.sqrt(d2)];
}

function cholSolve(l: number[], b0: number, b1: number, b2: number): V3 {
  const [l00, l10, l11, l20, l21, l22] = l;
  const y0 = b0 / l00, y1 = (b1 - l10 * y0) / l11, y2 = (b2 - l20 * y0 - l21 * y1) / l22;
  const x2 = y2 / l22, x1 = (y1 - l21 * x2) / l11, x0 = (y0 - l10 * x1 - l20 * x2) / l00;
  return [x0, x1, x2];
}

const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** The rotation of b's camera frame that carries the ray through pixel (x, y) to the ray through (x + dx, y + dy): the
 *  shortest arc between the two, so it has no component about the ray itself (phase correlation measures no roll). */
function shiftRotation(k: Intrinsics, x: number, y: number, dx: number, dy: number): Quat {
  const from = unprojectPixel(k, x, y), to = unprojectPixel(k, x + dx, y + dy), axis = cross(from, to);
  const s = Math.hypot(axis[0], axis[1], axis[2]);
  if (!(s > 1e-15)) return IDENTITY;
  const angle = Math.atan2(s, from[0] * to[0] + from[1] * to[1] + from[2] * to[2]);
  return expSO3([axis[0] / s * angle, axis[1] / s * angle, axis[2] / s * angle]);
}

const finiteQuat = (q: Quat) => Number.isFinite(q[0]) && Number.isFinite(q[1]) && Number.isFinite(q[2]) && Number.isFinite(q[3]);

// ---- One level: the residual image and its statistics ------------------------------------------------------------------

interface LevelEval {
  frac: number;           // fraction of the template's pixels that land inside b (warpLevel's return)
  n: number;              // valid template pixels
  alpha: number; beta: number;
  g: V3;                  // sum w jac e
  hw: Float64Array;       // sum w jac jac^T, the Huber-weighted Hessian over the valid pixels
}
interface FinalEval extends LevelEval {
  sigma: number;          // 1.4826 MAD of the residuals, floored at SIGMA_FLOOR
  zncc: number; inlierFrac: number;
  hv: Float64Array;       // sum jac jac^T over the valid pixels, unweighted: the texture test and the covariance
  cells: Int32Array;      // per cell of a 4 x 4 grid of the overlap, the pixel chosen for a correspondence, or -1
}

/** Sample b at the template's pixels through qBA, fit gain and bias, weight the residuals by Huber when `robust`
 *  (otherwise every valid pixel weighs 1), and sum the LK normal equations. With `final`, also the statistics the
 *  acceptance tests and the outputs read. Null when fewer than MIN_VALID template pixels land inside b. */
function evaluate(tl: TemplateLevel, tpl: Level, img: Level, k: Intrinsics, q: Quat, robust: boolean, final: false): LevelEval | null;
function evaluate(tl: TemplateLevel, tpl: Level, img: Level, k: Intrinsics, q: Quat, robust: boolean, final: true): FinalEval | null;
function evaluate(tl: TemplateLevel, tpl: Level, img: Level, k: Intrinsics, q: Quat, robust: boolean, final: boolean): LevelEval | FinalEval | null {
  const w = tpl.w, h = tpl.h, n = w * h, t = tpl.px, jac = tl.jac;
  const { warped, dev, weight, sel } = levelScratch(n);
  const frac = warpLevel(img, rotationHomography(k, k, q), warped, w, h);

  // Unweighted moments over the valid pixels: the first gain and bias, and ZNCC.
  let cnt = 0, sT = 0, sI = 0, sTT = 0, sII = 0, sTI = 0;
  let bx0 = w, bx1 = -1, by0 = h, by1 = -1;
  for (let y = 0, i = 0; y < h; y++) {
    for (let x = 0; x < w; x++, i++) {
      const v = warped[i];
      if (v !== v) continue;
      const tv = t[i];
      cnt++; sT += tv; sI += v; sTT += tv * tv; sII += v * v; sTI += tv * v;
      if (x < bx0) bx0 = x;
      if (x > bx1) bx1 = x;
      if (y < by0) by0 = y;
      by1 = y;
    }
  }
  if (cnt < MIN_VALID) return null;
  const mT = sT / cnt, mI = sI / cnt;
  const vT = Math.max(0, sTT / cnt - mT * mT), vI = Math.max(0, sII / cnt - mI * mI);
  let alpha = vT > 0 ? Math.sqrt(vI / vT) : 0, beta = mI - alpha * mT;

  if (robust) {
    // Residuals of that fit, their median and MAD, the Huber weights from them, and the weighted gain and bias.
    let m = 0;
    for (let i = 0; i < n; i++) {
      const v = warped[i];
      if (v !== v) continue;
      const e = v - alpha * t[i] - beta;
      dev[i] = e; sel[m++] = e;
    }
    const med0 = median(sel, m);
    m = 0;
    for (let i = 0; i < n; i++) if (warped[i] === warped[i]) sel[m++] = Math.abs(dev[i] - med0);
    const c = HUBER_K * Math.max(MAD_TO_SD * median(sel, m), SIGMA_FLOOR);
    let sw = 0, swT = 0, swI = 0, swTT = 0, swII = 0;
    for (let i = 0; i < n; i++) {
      const v = warped[i];
      if (v !== v) continue;
      const r = Math.abs(dev[i] - med0), wi = r <= c ? 1 : c / r, tv = t[i];
      weight[i] = wi;
      sw += wi; swT += wi * tv; swI += wi * v; swTT += wi * tv * tv; swII += wi * v * v;
    }
    const mwT = swT / sw, mwI = swI / sw;
    const vwT = Math.max(0, swTT / sw - mwT * mwT), vwI = Math.max(0, swII / sw - mwI * mwI);
    alpha = vwT > 0 ? Math.sqrt(vwI / vwT) : 0;
    beta = mwI - alpha * mwT;
  }

  // The normal equations with the final gain: residuals e = b - gain a - bias, weighted.
  let g0 = 0, g1 = 0, g2 = 0, h00 = 0, h01 = 0, h02 = 0, h11 = 0, h12 = 0, h22 = 0;
  let v00 = 0, v01 = 0, v02 = 0, v11 = 0, v12 = 0, v22 = 0;
  let m = 0;
  for (let i = 0; i < n; i++) {
    const v = warped[i];
    if (v !== v) continue;
    const e = v - alpha * t[i] - beta, wi = robust ? weight[i] : 1;
    const j0 = jac[3 * i], j1 = jac[3 * i + 1], j2 = jac[3 * i + 2];
    dev[i] = e;
    const we = wi * e;
    g0 += we * j0; g1 += we * j1; g2 += we * j2;
    h00 += wi * j0 * j0; h01 += wi * j0 * j1; h02 += wi * j0 * j2; h11 += wi * j1 * j1; h12 += wi * j1 * j2; h22 += wi * j2 * j2;
    if (final) {
      v00 += j0 * j0; v01 += j0 * j1; v02 += j0 * j2; v11 += j1 * j1; v12 += j1 * j2; v22 += j2 * j2;
      sel[m++] = e;
    }
  }
  const hw = Float64Array.of(h00, h01, h02, h01, h11, h12, h02, h12, h22);
  const base: LevelEval = { frac, n: cnt, alpha, beta, g: [g0, g1, g2], hw };
  if (!final) return base;

  // The final residuals' spread, the inliers, ZNCC, and the correspondence candidates: in each cell of a 4 x 4 grid
  // over the overlap's bounding box, the pixel within 1.5 sigma with the steepest template gradient.
  const med = median(sel, m);
  m = 0;
  for (let i = 0; i < n; i++) if (warped[i] === warped[i]) sel[m++] = Math.abs(dev[i] - med);
  const sigma = Math.max(MAD_TO_SD * median(sel, m), SIGMA_FLOOR);
  const cells = new Int32Array(GRID * GRID).fill(-1), score = new Float64Array(GRID * GRID);
  const cw = (bx1 - bx0 + 1) / GRID, ch = (by1 - by0 + 1) / GRID;
  let inliers = 0;
  for (let y = by0; y <= by1; y++) {
    const cy = Math.min(GRID - 1, Math.floor((y - by0) / ch));
    for (let x = bx0; x <= bx1; x++) {
      const i = y * w + x;
      if (warped[i] !== warped[i]) continue;
      const r = Math.abs(dev[i] - med);
      if (r <= INLIER_SIGMAS * sigma) inliers++;
      if (r >= CORR_SIGMAS * sigma) continue;
      const s = tl.gx[i] * tl.gx[i] + tl.gy[i] * tl.gy[i], cell = cy * GRID + Math.min(GRID - 1, Math.floor((x - bx0) / cw));
      if (s > score[cell]) { score[cell] = s; cells[cell] = i; }
    }
  }
  const zncc = vT > 0 && vI > 0 ? (sTI / cnt - mT * mI) / Math.sqrt(vT * vI) : 0;
  const hv = Float64Array.of(v00, v01, v02, v01, v11, v12, v02, v12, v22);
  return { ...base, sigma, zncc, inlierFrac: inliers / cnt, hv, cells };
}

// ---- Outputs -----------------------------------------------------------------------------------------------------------

/** sigma^2 (gain^2 H)^-1 in rad^2; a matrix with an unconstrained direction (no texture, or no gain) is infinite on the
 *  diagonal: the gyro places that frame (section 6, featureless sky). */
function covariance(hv: Float64Array, alpha: number, sigma: number): Float64Array {
  const out = new Float64Array(9), l = alpha > 0 ? cholesky3(hv) : null;
  if (!l) { out[0] = out[4] = out[8] = Infinity; return out; }
  const s = sigma * sigma / (alpha * alpha);
  for (let col = 0; col < 3; col++) {
    const x = cholSolve(l, col === 0 ? 1 : 0, col === 1 ? 1 : 0, col === 2 ? 1 : 0);
    for (let row = 0; row < 3; row++) out[row * 3 + col] = s * x[row];
  }
  // The inverse of a symmetric matrix is symmetric; average away the rounding so consumers can rely on it.
  for (let r = 0; r < 3; r++) for (let c = r + 1; c < 3; c++) { const v = (out[r * 3 + c] + out[c * 3 + r]) / 2; out[r * 3 + c] = v; out[c * 3 + r] = v; }
  return out;
}

/** The chosen L1 template pixels as L0 correspondences: a's pixel centre carried along its ray to L0, and its image in b
 *  through the final rotation (H = K0 R_BA K0^-1). */
function correspondences(cells: Int32Array, w1: number, k1: Intrinsics, k0: Intrinsics, q: Quat): Correspondence[] {
  const H = rotationHomography(k0, k0, q), s = k0.f / k1.f, out: Correspondence[] = [];
  for (const i of cells) {
    if (i < 0) continue;
    const x = i % w1, y = (i - x) / w1;
    const ua = k0.cx + (x + 0.5 - k1.cx) * s, va = k0.cy + (y + 0.5 - k1.cy) * s;
    const d = H[6] * ua + H[7] * va + H[8];
    out.push({ ua, va, ub: (H[0] * ua + H[1] * va + H[2]) / d, vb: (H[3] * ua + H[4] * va + H[5]) / d });
  }
  return out;
}

interface StripStats { gainRGB: [number, number, number]; meanA: [number, number, number]; meanB: [number, number, number]; n: number }
/** Without both strips: zeros, and n = 0 (the align block). */
const noStrips = (): StripStats => ({ gainRGB: [0, 0, 0], meanA: [0, 0, 0], meanB: [0, 0, 0], n: 0 });

/** Overlap channel means and per-channel gains from the two L0 RGB strips (4.6 step 5, RI M6). Every pixel of a's strip
 *  is carried through the rotation to b; it counts when it lands on a pixel of b's strip and both have luma 16..240.
 *  meanA and meanB are the channel means over those pixels and n their count. gainRGB is b over a per channel, like
 *  `gain`: the median over the cells of a 4 x 4 grid of the overlap of each cell's ratio of channel sums, so a person
 *  or a moving branch in one cell does not move it. Zeros when nothing overlaps. */
function stripStats(a: RgbStrip, b: RgbStrip, k0: Intrinsics, q: Quat): StripStats {
  const H = rotationHomography(k0, k0, q), pairs = pairBuffer(a.w * a.h), pa = a.strip, pb = b.strip;
  let n = 0, sx0 = a.w, sx1 = -1, sy0 = a.h, sy1 = -1;
  for (let sy = 0; sy < a.h; sy++) {
    // Along a row the homogeneous image of the pixel centre moves by the first column of H per pixel.
    const v = sy + 0.5, u0 = a.x0 + 0.5;
    let nx = H[0] * u0 + H[1] * v + H[2], ny = H[3] * u0 + H[4] * v + H[5], d = H[6] * u0 + H[7] * v + H[8];
    for (let sx = 0; sx < a.w; sx++, nx += H[0], ny += H[3], d += H[6]) {
      if (!(d > 1e-12)) continue;
      const bx = Math.floor(nx / d) - b.x0, by = Math.floor(ny / d);
      if (!(bx >= 0 && bx < b.w && by >= 0 && by < b.h)) continue;
      const ia = (sy * a.w + sx) * 3, ib = (by * b.w + bx) * 3;
      const la = Math.round(pixelLuminance(pa[ia], pa[ia + 1], pa[ia + 2])), lb = Math.round(pixelLuminance(pb[ib], pb[ib + 1], pb[ib + 2]));
      if (la < LUMA_LO || la > LUMA_HI || lb < LUMA_LO || lb > LUMA_HI) continue;
      pairs[2 * n] = ia; pairs[2 * n + 1] = ib; n++;
      if (sx < sx0) sx0 = sx;
      if (sx > sx1) sx1 = sx;
      if (sy < sy0) sy0 = sy;
      sy1 = sy;
    }
  }
  if (n === 0) return noStrips();
  const cellA = new Float64Array(3 * GRID * GRID), cellB = new Float64Array(3 * GRID * GRID), cellN = new Int32Array(GRID * GRID);
  const kx = GRID / (sx1 - sx0 + 1), ky = GRID / (sy1 - sy0 + 1);
  for (let p = 0; p < n; p++) {
    const ia = pairs[2 * p], ib = pairs[2 * p + 1], pix = (ia / 3) | 0, sy = (pix / a.w) | 0, sx = pix - sy * a.w;
    const cell = Math.min(GRID - 1, ((sy - sy0) * ky) | 0) * GRID + Math.min(GRID - 1, ((sx - sx0) * kx) | 0), c3 = 3 * cell;
    cellN[cell]++;
    cellA[c3] += pa[ia]; cellA[c3 + 1] += pa[ia + 1]; cellA[c3 + 2] += pa[ia + 2];
    cellB[c3] += pb[ib]; cellB[c3 + 1] += pb[ib + 1]; cellB[c3 + 2] += pb[ib + 2];
  }
  // The overlap sums are the sums of the cells.
  const sumA = [0, 0, 0], sumB = [0, 0, 0];
  for (let cell = 0; cell < GRID * GRID; cell++) for (let ch3 = 0; ch3 < 3; ch3++) { sumA[ch3] += cellA[3 * cell + ch3]; sumB[ch3] += cellB[3 * cell + ch3]; }
  const gainRGB: [number, number, number] = [0, 0, 0], ratios = new Float64Array(GRID * GRID);
  for (let ch3 = 0; ch3 < 3; ch3++) {
    let r = 0;
    for (let cell = 0; cell < GRID * GRID; cell++) {
      if (cellN[cell] >= MIN_CELL_PIXELS && cellA[3 * cell + ch3] > 0) ratios[r++] = cellB[3 * cell + ch3] / cellA[3 * cell + ch3];
    }
    gainRGB[ch3] = r > 0 ? median(ratios, r) : 0;
  }
  return {
    gainRGB, n,
    meanA: [sumA[0] / n, sumA[1] / n, sumA[2] / n],
    meanB: [sumB[0] / n, sumB[1] / n, sumB[2] / n],
  };
}

// ---- The pair --------------------------------------------------------------------------------------------------------

function checkLevel(what: string, lv: { w: number; h: number }, k: Intrinsics): void {
  if (lv.w !== k.w || lv.h !== k.h) throw new RangeError(`alignPair: ${what} is ${lv.w} x ${lv.h}, the intrinsics say ${k.w} x ${k.h}`);
}

export function alignPair(tpl: Template, img: Pyramid, qPred: Quat,
  k: { l0: Intrinsics; l1: Intrinsics; l2: Intrinsics },
  opts: { window: AlignWindow; rgbA?: RgbStrip; rgbB?: RgbStrip }): AlignResult {
  checkLevel('the template L1', tpl.pyr.l1, k.l1); checkLevel('the template L2', tpl.pyr.l2, k.l2);
  checkLevel('the image L1', img.l1, k.l1); checkLevel('the image L2', img.l2, k.l2);
  const closure = opts.window === 'closure';
  const w2 = k.l2.w, h2 = k.l2.h;

  // 1. Prewarp: a's L2 in b's predicted view, NaN where a does not reach.
  const pre = prewarpBuffer(w2 * h2);
  const preFrac = warpLevel(tpl.pyr.l2, rotationHomography(k.l2, k.l2, qinv(qPred)), pre, w2, h2);

  // 2. Phase correlation. The keyframe window is centred on the centroid of the predicted overlap and kept inside the
  //    frame; the closure window holds the whole frame, centred. Anything outside the frame is NaN in both.
  const pw = closure ? CLOSURE_W : KEY_W, ph = closure ? CLOSURE_H : KEY_H, win = phaseWindow(pw, ph);
  let mx = 0, my = 0, cnt = 0;
  for (let y = 0, i = 0; y < h2; y++) for (let x = 0; x < w2; x++, i++) if (pre[i] === pre[i]) { mx += x + 0.5; my += y + 0.5; cnt++; }
  if (cnt === 0) return refusal(qPred, opts, k, preFrac);
  const place = (centre: number, size: number, frame: number) =>
    frame <= size ? Math.floor((frame - size) / 2) : Math.min(Math.max(Math.round(centre - size / 2), 0), frame - size);
  const ox = closure ? Math.floor((w2 - pw) / 2) : place(mx / cnt, pw, w2);
  const oy = closure ? Math.floor((h2 - ph) / 2) : place(my / cnt, ph, h2);
  let cx = 0, cy = 0, inWin = 0;
  for (let wy = 0, j = 0; wy < ph; wy++) {
    const y = oy + wy;
    for (let wx = 0; wx < pw; wx++, j++) {
      const x = ox + wx;
      if (x >= 0 && x < w2 && y >= 0 && y < h2) {
        const i = y * w2 + x, va = pre[i];
        win.a[j] = va; win.b[j] = img.l2.px[i];
        if (va === va) { cx += x + 0.5; cy += y + 0.5; inWin++; }
      } else {
        win.a[j] = NaN; win.b[j] = NaN;
      }
    }
  }
  const pc = phaseCorrelate(win.a, win.b, pw, ph, win.scratch);
  const psr = pc.psr;
  const peak = psr >= ACCEPT.psrMin && Number.isFinite(pc.dx) && Number.isFinite(pc.dy) && inWin > 0;
  // The content predicted at the window's centre of content was found (dx, dy) away: rotate b's rays to match.
  const q0 = peak ? qnormalize(qmul(shiftRotation(k.l2, cx / inWin, cy / inWin, pc.dx, pc.dy), qPred)) : qPred;

  // 3. IC-LK, L2 then L1, from the phase-correlation seed. Without a peak the pair is refused anyway, so LK is skipped
  //    and the statistics are read at the prediction. The first iteration at each level weighs every pixel alike and
  //    Huber weights the rest: before that first step the residuals are dominated by what the seed left, chiefly the
  //    roll phase correlation cannot see, which displaces the frame's edges and not its centre; Huber read from those
  //    residuals discards the edge pixels that carry the roll, and the 4 + 2 iterations then leave up to 0.07 degrees
  //    of a 4-degree roll (0.008 with the first step unweighted, on align.test.ts's injected grid).
  let q = q0, iterations = 0, stuck = false, lastStepDeg = 0;
  if (peak) {
    const levels: [TemplateLevel, Level, Level, Intrinsics, number][] = [
      [tpl.l2, tpl.pyr.l2, img.l2, k.l2, ITERS_L2], [tpl.l1, tpl.pyr.l1, img.l1, k.l1, ITERS_L1]];
    descend: for (const [tl, tlv, ilv, kl, iters] of levels) {
      for (let it = 0; it < iters; it++) {
        const ev = evaluate(tl, tlv, ilv, kl, q, it > 0, false);
        if (!ev || !(ev.alpha > 0)) { stuck = true; break descend; }
        const l = cholesky3(ev.hw);
        if (!l) { stuck = true; break descend; }
        const d = cholSolve(l, ev.g[0], ev.g[1], ev.g[2]);
        const step: V3 = [d[0] / ev.alpha, d[1] / ev.alpha, d[2] / ev.alpha];
        q = qnormalize(qmul(q, qinv(expSO3(step))));
        iterations++;
        lastStepDeg = Math.hypot(step[0], step[1], step[2]) / DEG;
        if (!finiteQuat(q)) { stuck = true; break descend; }
        if (lastStepDeg < EARLY_EXIT_DEG) break;
      }
    }
  }
  let fin = finiteQuat(q) ? evaluate(tpl.l1, tpl.pyr.l1, img.l1, k.l1, q, true, true) : null;
  if (!fin) { stuck = true; q = q0; fin = evaluate(tpl.l1, tpl.pyr.l1, img.l1, k.l1, q0, true, true); }
  if (!fin) return refusal(qPred, opts, k, preFrac, psr);

  // 4. Acceptance.
  const alpha = fin.alpha, texture = alpha * alpha * (fin.hv[0] + fin.hv[4] + fin.hv[8]) / fin.n;
  const textured = texture > TEXTURE_FLOOR;
  // A keyframe result beyond the window's unambiguous half-width (16 px at L2) cannot have come from its peak; a
  // closure accepts a residual of at most 12 degrees (4.9).
  const limitDeg = closure ? ACCEPT.closureMaxDeg : (pw / 2) / k.l2.f / DEG;
  const diverged = peak && (stuck || lastStepDeg > DIVERGE_STEP_DEG || !(angleBetweenDeg(qPred, q) <= limitDeg));
  const znccMin = closure ? ACCEPT.closureZnccMin : ACCEPT.znccMin;
  const reason: AlignResult['reason'] = !textured ? 'texture'
    : !(psr >= ACCEPT.psrMin) ? 'no-peak'
    : !(fin.frac >= ACCEPT.overlapMin) ? 'overlap'
    : diverged ? 'diverged'
    : !(fin.zncc >= znccMin) ? 'low-zncc'
    : !(fin.inlierFrac >= ACCEPT.inlierMin) ? 'few-inliers'
    : undefined;
  const ok = reason === undefined;
  const qBA = ok ? q : qPred;

  // 5. Outputs.
  const strips = opts.rgbA && opts.rgbB ? stripStats(opts.rgbA, opts.rgbB, k.l0, qBA) : noStrips();
  const result: AlignResult = {
    ok, qBA, covRad2: covariance(fin.hv, alpha, fin.sigma),
    gain: alpha, bias: fin.beta, gainRGB: strips.gainRGB, meanA: strips.meanA, meanB: strips.meanB, n: strips.n,
    psr, zncc: fin.zncc, overlap: fin.frac, inlierFrac: fin.inlierFrac, iterations, textured,
    corr: ok ? correspondences(fin.cells, k.l1.w, k.l1, k.l0, q) : [],
  };
  if (reason) result.reason = reason;
  return result;
}

/** No usable overlap at all: nothing to measure, so not textured (a 'mismatch' needs texture), refused for overlap. */
function refusal(qPred: Quat, opts: { rgbA?: RgbStrip; rgbB?: RgbStrip }, k: { l0: Intrinsics }, overlap: number, psr = 0): AlignResult {
  const strips = opts.rgbA && opts.rgbB ? stripStats(opts.rgbA, opts.rgbB, k.l0, qPred) : noStrips();
  const cov = new Float64Array(9);
  cov[0] = cov[4] = cov[8] = Infinity;
  return {
    ok: false, reason: 'overlap', qBA: qPred, covRad2: cov, gain: 0, bias: 0, ...strips,
    psr, zncc: 0, overlap, inlierFrac: 0, iterations: 0, textured: false, corr: [],
  };
}
