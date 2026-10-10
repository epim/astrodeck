// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Luma, the two-level Uint8 pyramid, the per-keyframe LK template and the bilinear homography warp for the horizon
// panorama scanner (SPEC-v2 4.2, 4.6 steps 1-3).
//
// Pixel convention (types.ts Intrinsics): pixel i spans [i, i + 1) and is centred at i + 0.5, and cx = w / 2. Every
// coordinate that meets a homography or an Intrinsics below is a continuous coordinate in that convention; the array
// index of a pixel is its coordinate minus 0.5.
//
// The template's rotation increments (what T21 solves for) are written once here, because the Jacobian's sign and
// frame are part of the contract:
//   * omega = (wx, wy, wz) is a rotation vector in radians in the TEMPLATE (a) camera frame of rotation.ts: x right,
//     y up, looking along -z. Under a positive increment the image content moves up (wx), left (wy) and
//     counter-clockwise (wz) on screen.
//   * jac column k at a pixel is d(intensity)/d(omega_k) for the warp W(x; omega) = rotationHomography(K, K, expSO3(omega))
//     applied to the template pixel x, i.e. gradient . dW/d(omega_k) at omega = 0, per radian.
//   * for an inverse-compositional step with error e = I(W(x; q_BA)) - T(x), solve hess . d_omega = sum jac^T e and
//     update q_BA <- qmul(q_BA, qinv(expSO3(d_omega))).
import { pixelLuminance } from '../photosphereGeometry';
import type { Intrinsics, Level, Pyramid, Template, TemplateLevel } from './types';

/** Rec. 601 luma of an RGBA frame, rounded to 0..255. It calls `pixelLuminance`, so the weights live in one place. */
export function lumaOf(rgba: Uint8ClampedArray, w: number, h: number): Level {
  const n = w * h, px = new Uint8Array(n);
  for (let i = 0, j = 0; i < n; i++, j += 4) px[i] = Math.round(pixelLuminance(rgba[j], rgba[j + 1], rgba[j + 2]));
  return { w, h, px };
}

/** The next pyramid level: 2x2 box means, rounded half up. An odd last row or column is dropped. */
function halve(src: Level): Level {
  const w = src.w >> 1, h = src.h >> 1, s = src.px, px = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const r0 = 2 * y * src.w, r1 = r0 + src.w;
    for (let x = 0; x < w; x++) {
      const i = 2 * x;
      px[y * w + x] = (s[r0 + i] + s[r0 + i + 1] + s[r1 + i] + s[r1 + i + 1] + 2) >> 2;
    }
  }
  return { w, h, px };
}

/** L0 luma (transient: the caller drops it after the first alignment) and the kept L1 and L2: 18,000 bytes at 180 x 320. */
export function buildPyramid(rgba: Uint8ClampedArray, w: number, h: number): { pyr: Pyramid; l0: Level } {
  const l0 = lumaOf(rgba, w, h), l1 = halve(l0), l2 = halve(l1);
  return { pyr: { l1, l2 }, l0 };
}

/** Gradients (central differences, one-sided on the border, intensity per pixel), the three Jacobian columns and the
 *  3x3 Hessian sum(jac jac^T) of one level. */
function templateLevel(lv: Level, k: Intrinsics): TemplateLevel {
  const { w, h, px } = lv;
  if (k.w !== w || k.h !== h) throw new RangeError(`prepareTemplate: intrinsics ${k.w} x ${k.h} do not match the ${w} x ${h} level`);
  const n = w * h, gx = new Float32Array(n), gy = new Float32Array(n), jac = new Float32Array(3 * n), hess = new Float64Array(9);
  const inv = 1 / k.f;
  let h00 = 0, h01 = 0, h02 = 0, h11 = 0, h12 = 0, h22 = 0;
  for (let y = 0; y < h; y++) {
    const Y = (y + 0.5 - k.cy) * inv, ya = y > 0 ? -w : 0, yb = y < h - 1 ? w : 0, ys = ya === 0 || yb === 0 ? 1 : 2;
    for (let x = 0; x < w; x++) {
      const i = y * w + x, X = (x + 0.5 - k.cx) * inv;
      const xa = x > 0 ? -1 : 0, xb = x < w - 1 ? 1 : 0, xs = xa === 0 || xb === 0 ? 1 : 2;
      const dx = (px[i + xb] - px[i + xa]) / xs, dy = (px[i + yb] - px[i + ya]) / ys;
      gx[i] = dx; gy[i] = dy;
      // dW/d(omega) in pixels per radian: x' = f (X + wy' (1 + X^2) - wz' Y - wx' X Y), y' likewise, with the image-frame
      // components wy' = -wy and wz' = -wz of the camera-frame rotation vector (the image frame is y down, z forward).
      const XY = X * Y, j0 = k.f * (dx * -XY + dy * -(1 + Y * Y)), j1 = k.f * (dx * -(1 + X * X) + dy * -XY), j2 = k.f * (dx * Y + dy * -X);
      jac[3 * i] = j0; jac[3 * i + 1] = j1; jac[3 * i + 2] = j2;
      h00 += j0 * j0; h01 += j0 * j1; h02 += j0 * j2; h11 += j1 * j1; h12 += j1 * j2; h22 += j2 * j2;
    }
  }
  hess[0] = h00; hess[1] = h01; hess[2] = h02;
  hess[3] = h01; hess[4] = h11; hess[5] = h12;
  hess[6] = h02; hess[7] = h12; hess[8] = h22;
  return { gx, gy, jac, hess };
}

/** Per-keyframe LK data, computed once when the keyframe becomes a template. `k1` and `k2` are the L1 and L2 intrinsics. */
export function prepareTemplate(p: Pyramid, k1: Intrinsics, k2: Intrinsics): Template {
  return { pyr: p, l1: templateLevel(p.l1, k1), l2: templateLevel(p.l2, k2) };
}

/** Bilinear sample of `src` through the homography H, 3x3 row-major, which maps an OUT pixel to a SRC pixel (both as
 *  continuous coordinates, out pixel (x, y) at its centre (x + 0.5, y + 0.5)). `out` is outW x outH. A sample is NaN
 *  when the source point falls outside the span of the source pixel centres, or lies at or behind the plane (the
 *  homogeneous scale must be positive, as `rotationHomography` gives). Returns the fraction of out pixels that are not NaN.
 *  Warping a's level into b's view therefore takes the b-to-a homography, `rotationHomography(kb, ka, qinv(qBA))`;
 *  sampling b at the template's pixels takes `rotationHomography(ka, kb, qBA)`. */
export function warpLevel(src: Level, H: Float64Array, out: Float32Array, outW: number, outH: number): number {
  const { w, h, px } = src, xMax = w - 1, yMax = h - 1;
  const h0 = H[0], h1 = H[1], h2 = H[2], h3 = H[3], h4 = H[4], h5 = H[5], h6 = H[6], h7 = H[7], h8 = H[8];
  let inside = 0;
  for (let y = 0; y < outH; y++) {
    const py = y + 0.5;
    for (let x = 0; x < outW; x++) {
      const pxc = x + 0.5, d = h6 * pxc + h7 * py + h8, o = y * outW + x;
      if (!(d > 1e-12)) { out[o] = NaN; continue; }
      const sx = (h0 * pxc + h1 * py + h2) / d - 0.5, sy = (h3 * pxc + h4 * py + h5) / d - 0.5;
      if (!(sx >= 0 && sx <= xMax && sy >= 0 && sy <= yMax)) { out[o] = NaN; continue; }
      // At the last column or row the cell to the left or above is used with a weight of 1, so the edge sample is exact.
      const x0 = Math.min(Math.floor(sx), Math.max(xMax - 1, 0)), y0 = Math.min(Math.floor(sy), Math.max(yMax - 1, 0));
      const fx = sx - x0, fy = sy - y0, i = y0 * w + x0;
      // A one-pixel-wide source has no neighbour to blend with; its single column or row is the sample.
      const x1 = w > 1 ? 1 : 0, y1 = h > 1 ? w : 0;
      const top = px[i] + (px[i + x1] - px[i]) * fx, bot = px[i + y1] + (px[i + y1 + x1] - px[i + y1]) * fx;
      out[o] = top + (bot - top) * fy;
      inside++;
    }
  }
  return outW * outH > 0 ? inside / (outW * outH) : 0;
}
