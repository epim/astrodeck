// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T07: luma, the two-level pyramid, the LK template and the homography warp (SPEC-v2 4.2, 4.6 steps 1-3, 7.2).
//
// Mutant this file must catch (SPEC-v2 7.2): Float32 levels. `buildPyramid` keeping L1 and L2 as Float32Array makes
// `pyramid bytes` fail (72,000 bytes against 18,000 at 180 x 320, and 96,000 against 24,000 at 240 x 320).
//
// The Jacobian and the update rule are pinned against rotation.ts here, not against a copy of the formula: the numeric
// derivative uses `rotationHomography` and `expSO3`, and the convergence test runs a real inverse-compositional loop
// on two renderings of one analytic scene. A flipped sign or a swapped frame in any of the three columns diverges.
import assert from 'node:assert/strict';
import { pixelLuminance } from '../../photosphereGeometry';
import { buildPyramid, lumaOf, prepareTemplate, warpLevel } from '../pyramid';
import { angleBetweenDeg, expSO3, intrinsicsAt, priorFNorm, qinv, qmul, qnormalize, qrotate, rotationHomography, unprojectPixel } from '../rotation';
import type { Intrinsics, Level, Pyramid, Quat, TemplateLevel, V3 } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** mulberry32: a seeded generator, so every random case below is the same case every run. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function randomRgba(r: () => number, w: number, h: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < out.length; i++) out[i] = Math.floor(r() * 256);
  return out;
}
function greyRgba(w: number, h: number, fn: (x: number, y: number) => number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const v = fn(x, y), o = (y * w + x) * 4;
    out[o] = v; out[o + 1] = v; out[o + 2] = v; out[o + 3] = 255;
  }
  return out;
}

// ---- Luma -------------------------------------------------------------------------------------------------------------

test('lumaOf is Rec. 601, identical to pixelLuminance, and ignores alpha', () => {
  const r = rng(3), w = 37, h = 23, rgba = randomRgba(r, w, h), lv = lumaOf(rgba, w, h);
  assert.equal(lv.w, w); assert.equal(lv.h, h);
  assert.ok(lv.px instanceof Uint8Array); assert.equal(lv.px.length, w * h);
  for (let i = 0; i < w * h; i++) {
    const want = Math.round(pixelLuminance(rgba[4 * i], rgba[4 * i + 1], rgba[4 * i + 2]));
    assert.equal(lv.px[i], want, `pixel ${i}`);
  }
  const probe = (r: number, g: number, b: number, a = 255) => lumaOf(new Uint8ClampedArray([r, g, b, a]), 1, 1).px[0];
  assert.equal(probe(255, 0, 0), 76); assert.equal(probe(0, 255, 0), 150); assert.equal(probe(0, 0, 255), 29);
  assert.equal(probe(0, 0, 0), 0); assert.equal(probe(255, 255, 255), 255);
  assert.equal(probe(255, 0, 0, 0), probe(255, 0, 0, 255), 'alpha is ignored');
  for (let v = 0; v < 256; v++) assert.equal(probe(v, v, v), v, `grey ${v} is its own luma`);
});

// ---- Pyramid sizes and bytes ----------------------------------------------------------------------------------------

test('pyramid sizes: L1 and L2 of 180 x 320 are 90 x 160 and 45 x 80; of 240 x 320 they are 120 x 160 and 60 x 80', () => {
  for (const [w, h, w1, h1, w2, h2] of [[180, 320, 90, 160, 45, 80], [240, 320, 120, 160, 60, 80]]) {
    const { pyr, l0 } = buildPyramid(new Uint8ClampedArray(w * h * 4), w, h);
    assert.deepEqual([l0.w, l0.h, l0.px.length], [w, h, w * h]);
    assert.deepEqual([pyr.l1.w, pyr.l1.h, pyr.l1.px.length], [w1, h1, w1 * h1]);
    assert.deepEqual([pyr.l2.w, pyr.l2.h, pyr.l2.px.length], [w2, h2, w2 * h2]);
  }
  const odd = buildPyramid(new Uint8ClampedArray(181 * 321 * 4), 181, 321).pyr;
  assert.deepEqual([odd.l1.w, odd.l1.h, odd.l2.w, odd.l2.h], [90, 160, 45, 80], 'an odd last row or column is dropped');
});

test('pyramid bytes: 18,000 at 180 x 320 and 24,000 at 240 x 320, as Uint8', () => {
  for (const [w, h, bytes] of [[180, 320, 18000], [240, 320, 24000]]) {
    const { pyr } = buildPyramid(new Uint8ClampedArray(w * h * 4), w, h);
    assert.equal(pyr.l1.px.byteLength + pyr.l2.px.byteLength, bytes, `${w} x ${h}: bytes of L1 and L2`);
    assert.equal(pyr.l1.px.buffer.byteLength + pyr.l2.px.buffer.byteLength, bytes, `${w} x ${h}: and nothing larger sits behind the views`);
    for (const lv of [pyr.l1, pyr.l2]) assert.ok(lv.px instanceof Uint8Array, 'levels are Uint8Array');
  }
});

test('pyramid levels are 2x2 box means rounded half up, each from the one above', () => {
  const r = rng(8), w = 40, h = 24, { pyr, l0 } = buildPyramid(randomRgba(r, w, h), w, h);
  const halve = (src: Level, dst: Level) => {
    for (let y = 0; y < dst.h; y++) for (let x = 0; x < dst.w; x++) {
      const s = src.px[2 * y * src.w + 2 * x] + src.px[2 * y * src.w + 2 * x + 1] + src.px[(2 * y + 1) * src.w + 2 * x] + src.px[(2 * y + 1) * src.w + 2 * x + 1];
      assert.equal(dst.px[y * dst.w + x], Math.round(s / 4), `(${x}, ${y}) of ${dst.w} x ${dst.h}`);
    }
  };
  halve(l0, pyr.l1); halve(pyr.l1, pyr.l2);
  const flat = buildPyramid(greyRgba(16, 16, () => 77), 16, 16).pyr;
  assert.ok(flat.l1.px.every(v => v === 77) && flat.l2.px.every(v => v === 77), 'a flat image stays flat');
  const checker = buildPyramid(greyRgba(8, 8, (x, y) => ((x + y) & 1 ? 255 : 0)), 8, 8).pyr;
  assert.ok(checker.l1.px.every(v => v === 128), '(0 + 255 + 255 + 0) / 4 = 127.5 rounds up');
});

// ---- warpLevel -------------------------------------------------------------------------------------------------------

const level = (w: number, h: number, fn: (x: number, y: number) => number): Level =>
  ({ w, h, px: Uint8Array.from({ length: w * h }, (_, i) => fn(i % w, Math.floor(i / w))) });
const mat = (...a: number[]) => Float64Array.from(a);

test('warpLevel: identity and integer translation copy pixels, NaN and the returned fraction mark the rest', () => {
  const src = level(20, 12, (x, y) => (x * 7 + y * 13) % 251), out = new Float32Array(20 * 12);
  assert.equal(warpLevel(src, mat(1, 0, 0, 0, 1, 0, 0, 0, 1), out, 20, 12), 1);
  for (let i = 0; i < out.length; i++) assert.equal(out[i], src.px[i]);
  // out(x, y) = src(x + 3, y - 2)
  const frac = warpLevel(src, mat(1, 0, 3, 0, 1, -2, 0, 0, 1), out, 20, 12);
  let inside = 0;
  for (let y = 0; y < 12; y++) for (let x = 0; x < 20; x++) {
    const sx = x + 3, sy = y - 2, ok = sx < 20 && sy >= 0;
    if (ok) { inside++; assert.equal(out[y * 20 + x], src.px[sy * 20 + sx], `(${x}, ${y})`); } else assert.ok(Number.isNaN(out[y * 20 + x]), `(${x}, ${y}) is outside`);
  }
  assert.equal(frac, inside / 240);
  // a smaller out frame samples the top-left of the source
  const small = new Float32Array(5 * 4);
  assert.equal(warpLevel(src, mat(1, 0, 0, 0, 1, 0, 0, 0, 1), small, 5, 4), 1);
  assert.equal(small[3 * 5 + 4], src.px[3 * 20 + 4]);
});

test('warpLevel: bilinear weights, exact on a plane through any homography', () => {
  const src = level(30, 20, (x, y) => x + 2 * y), out = new Float32Array(30 * 20);
  warpLevel(src, mat(1, 0, 0.5, 0, 1, 0, 0, 0, 1), out, 30, 20);
  for (let x = 0; x < 29; x++) assert.ok(Math.abs(out[4 * 30 + x] - (src.px[4 * 30 + x] + src.px[4 * 30 + x + 1]) / 2) < 1e-5, 'half a pixel is the mean of two');
  assert.ok(Number.isNaN(out[4 * 30 + 29]), 'half a pixel past the last centre is outside');
  const H = mat(0.97, 0.12, 2.3, -0.1, 1.02, 1.7, 0.0004, -0.0003, 1);
  let inside = 0;
  const frac = warpLevel(src, H, out, 30, 20);
  for (let y = 0; y < 20; y++) for (let x = 0; x < 30; x++) {
    const px = x + 0.5, py = y + 0.5, d = H[6] * px + H[7] * py + H[8];
    const sx = (H[0] * px + H[1] * py + H[2]) / d - 0.5, sy = (H[3] * px + H[4] * py + H[5]) / d - 0.5;
    if (sx >= 0 && sx <= 29 && sy >= 0 && sy <= 19) { inside++; assert.ok(Math.abs(out[y * 30 + x] - (sx + 2 * sy)) < 1e-3, `(${x}, ${y}): ${out[y * 30 + x]} vs ${sx + 2 * sy}`); } else assert.ok(Number.isNaN(out[y * 30 + x]));
  }
  assert.equal(frac, inside / 600);
});

test('warpLevel: the span of the source pixel centres is inclusive, one step beyond is NaN, behind the plane is NaN', () => {
  const src = level(10, 6, (x, y) => 10 * y + x), out = new Float32Array(10 * 6);
  warpLevel(src, mat(1, 0, 0, 0, 1, 0, 0, 0, 1), out, 10, 6);
  assert.equal(out[0], 0); assert.equal(out[59], 59);
  warpLevel(src, mat(1, 0, -1e-7, 0, 1, 0, 0, 0, 1), out, 10, 6);
  assert.ok(Number.isNaN(out[0]) && Number.isNaN(out[3 * 10]), 'a hair left of the first centre is outside');
  warpLevel(src, mat(1, 0, 1e-7, 0, 1, 0, 0, 0, 1), out, 10, 6);
  assert.ok(Number.isNaN(out[9]) && Number.isNaN(out[59]), 'a hair right of the last centre is outside');
  assert.equal(warpLevel(src, mat(1, 0, 0, 0, 1, 0, 0, 0, -1), out, 10, 6), 0, 'a negative homogeneous scale is behind the plane');
  assert.ok(out.every(Number.isNaN));
  assert.equal(warpLevel(src, mat(1, 0, 0, 0, 1, 0, 0, 0, 0), out, 10, 6), 0);
});

// ---- A scene seen through two rotated cameras ----------------------------------------------------------------------

const SHORT = 180, LONG = 320, FNORM = priorFNorm(SHORT, LONG);
const K0 = intrinsicsAt(0, SHORT, LONG, FNORM), K1 = intrinsicsAt(1, SHORT, LONG, FNORM), K2 = intrinsicsAt(2, SHORT, LONG, FNORM);
/** An analytic scene: a luma for every direction in the frame of camera a. */
function scene(ray: V3): number {
  const az = Math.atan2(ray[0], -ray[2]), el = Math.asin(ray[1]);
  return 128 + 38 * Math.sin(31 * az + 7 * el) + 33 * Math.cos(23 * el - 17 * az) + 28 * Math.sin(41 * az) * Math.sin(37 * el);
}
/** Camera b's L0 frame. qBA takes camera-a vectors to camera-b vectors, so a b-pixel's ray is taken back to camera a to read the scene. */
function render(qBA: Quat): Uint8ClampedArray {
  const inv = qinv(qBA);
  return greyRgba(SHORT, LONG, (x, y) => scene(qrotate(inv, unprojectPixel(K0, x + 0.5, y + 0.5))));
}
const Q_IDENTITY: Quat = [1, 0, 0, 0];
const Q_TRUE = qnormalize(expSO3([-1.1 * Math.PI / 180, 1.6 * Math.PI / 180, 0.9 * Math.PI / 180]));   // pitch, yaw, roll
const PYR_A = buildPyramid(render(Q_IDENTITY), SHORT, LONG).pyr, PYR_B = buildPyramid(render(Q_TRUE), SHORT, LONG).pyr;

test('warpLevel through rotationHomography reproduces the other camera\'s rendering (H maps out pixels to source pixels)', () => {
  for (const [k, a, b] of [[K1, PYR_A.l1, PYR_B.l1], [K2, PYR_A.l2, PYR_B.l2]] as [Intrinsics, Level, Level][]) {
    const out = new Float32Array(k.w * k.h), frac = warpLevel(b, rotationHomography(k, k, Q_TRUE), out, k.w, k.h);
    // Sampling b at the pixel each a-pixel is carried to gives a's own image, up to the bilinear error of a busy scene.
    let sum = 0, n = 0;
    for (let i = 0; i < out.length; i++) if (!Number.isNaN(out[i])) { sum += Math.abs(out[i] - a.px[i]); n++; }
    assert.equal(n / out.length, frac);
    assert.ok(frac > 0.92 && frac < 1, `fraction inside ${frac}`);
    assert.ok(sum / n < 1.5, `${k.w} x ${k.h}: mean difference ${sum / n}`);
    // The inverse homography is a different image: if H were read the other way the match would not hold.
    const wrong = new Float32Array(k.w * k.h);
    warpLevel(b, rotationHomography(k, k, qinv(Q_TRUE)), wrong, k.w, k.h);
    let off = 0;
    for (let i = 0; i < wrong.length; i++) if (!Number.isNaN(wrong[i])) off += Math.abs(wrong[i] - a.px[i]);
    assert.ok(off / n > 3 * (sum / n), `${k.w} x ${k.h}: the inverse homography should be clearly worse (${off / n} vs ${sum / n})`);
  }
});

// ---- The template ----------------------------------------------------------------------------------------------------

const TEMPLATE_A = prepareTemplate(PYR_A, K1, K2);

test('prepareTemplate: shapes, types and the pyramid it was given', () => {
  assert.equal(TEMPLATE_A.pyr, PYR_A);
  for (const [t, k] of [[TEMPLATE_A.l1, K1], [TEMPLATE_A.l2, K2]] as [TemplateLevel, Intrinsics][]) {
    const n = k.w * k.h;
    assert.ok(t.gx instanceof Float32Array && t.gy instanceof Float32Array && t.jac instanceof Float32Array && t.hess instanceof Float64Array);
    assert.equal(t.gx.length, n); assert.equal(t.gy.length, n); assert.equal(t.jac.length, 3 * n); assert.equal(t.hess.length, 9);
  }
  assert.throws(() => prepareTemplate(PYR_A, K2, K1), RangeError, 'intrinsics that do not match the levels are refused');
  // A 3:4 frame gives 120 x 160 and 60 x 80 levels.
  const f34 = priorFNorm(240, 320), p34 = buildPyramid(greyRgba(240, 320, (x, y) => (x * 3 + y * 5) % 200), 240, 320).pyr;
  const t34 = prepareTemplate(p34, intrinsicsAt(1, 240, 320, f34), intrinsicsAt(2, 240, 320, f34));
  assert.equal(t34.l1.gx.length, 120 * 160); assert.equal(t34.l1.jac.length, 3 * 120 * 160);
  assert.equal(t34.l2.gx.length, 60 * 80); assert.equal(t34.l2.jac.length, 3 * 60 * 80);
});

test('prepareTemplate gradients: central differences inside, one-sided on the border, intensity per pixel', () => {
  const ramp: Pyramid = { l1: level(12, 10, (x, y) => x + y), l2: level(6, 5, (x) => 2 * x) };
  const t = prepareTemplate(ramp, { w: 12, h: 10, f: 14, cx: 6, cy: 5 }, { w: 6, h: 5, f: 7, cx: 3, cy: 2.5 });
  assert.ok(t.l1.gx.every(v => v === 1) && t.l1.gy.every(v => v === 1), 'a ramp of slope 1 has gradient 1 everywhere, border included');
  assert.ok(t.l2.gx.every(v => v === 2) && t.l2.gy.every(v => v === 0));
  const r = rng(6), w = 90, h = 160, rnd = level(w, h, () => Math.floor(r() * 256));
  const tt = prepareTemplate({ l1: rnd, l2: PYR_A.l2 }, K1, K2).l1, p = rnd.px;
  for (const [x, y] of [[40, 70], [1, 1], [88, 158], [0, 80], [89, 80], [45, 0], [45, 159], [0, 0], [89, 159]]) {
    const i = y * w + x;
    const gx = x === 0 ? p[i + 1] - p[i] : x === w - 1 ? p[i] - p[i - 1] : (p[i + 1] - p[i - 1]) / 2;
    const gy = y === 0 ? p[i + w] - p[i] : y === h - 1 ? p[i] - p[i - w] : (p[i + w] - p[i - w]) / 2;
    assert.equal(tt.gx[i], gx, `gx (${x}, ${y})`); assert.equal(tt.gy[i], gy, `gy (${x}, ${y})`);
  }
});

/** Where the template pixel (x + 0.5, y + 0.5) goes under a small rotation, by rotationHomography. */
function carried(k: Intrinsics, q: Quat, px: number, py: number): [number, number] {
  const H = rotationHomography(k, k, q), d = H[6] * px + H[7] * py + H[8];
  return [(H[0] * px + H[1] * py + H[2]) / d, (H[3] * px + H[4] * py + H[5]) / d];
}

test('prepareTemplate Jacobian: each column is the gradient dotted with the pixel motion under expSO3 about that camera axis', () => {
  const eps = 1e-6;
  for (const [t, k] of [[TEMPLATE_A.l1, K1], [TEMPLATE_A.l2, K2]] as [TemplateLevel, Intrinsics][]) {
    const pts: [number, number][] = [[k.w >> 1, k.h >> 1], [0, 0], [k.w - 1, 0], [0, k.h - 1], [k.w - 1, k.h - 1], [7, 3], [k.w - 9, k.h - 5], [k.w >> 2, 3 * (k.h >> 2)]];
    let strong = 0;
    for (const [x, y] of pts) {
      const i = y * k.w + x;
      for (let axis = 0; axis < 3; axis++) {
        const e: V3 = [0, 0, 0]; e[axis] = eps;
        const plus = carried(k, expSO3(e), x + 0.5, y + 0.5), e2: V3 = [0, 0, 0]; e2[axis] = -eps;
        const minus = carried(k, expSO3(e2), x + 0.5, y + 0.5);
        const want = t.gx[i] * (plus[0] - minus[0]) / (2 * eps) + t.gy[i] * (plus[1] - minus[1]) / (2 * eps);
        const got = t.jac[3 * i + axis];
        assert.ok(Math.abs(got - want) <= 1e-4 * Math.max(1, Math.abs(want)), `${k.w} x ${k.h} (${x}, ${y}) axis ${axis}: ${got} vs ${want}`);
        if (Math.abs(want) > 100) strong++;
      }
    }
    assert.ok(strong >= 6, 'the sample points are on texture, so the comparison is not of zeros');
  }
});

/** Cholesky pivots of a symmetric 3x3; all positive means positive definite. */
function pivots(m: Float64Array): number[] {
  const l = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], out: number[] = [];
  for (let i = 0; i < 3; i++) for (let j = 0; j <= i; j++) {
    let s = m[3 * i + j];
    for (let k = 0; k < j; k++) s -= l[i][k] * l[j][k];
    if (i === j) { out.push(s); l[i][i] = Math.sqrt(Math.max(s, 0)); } else l[i][j] = s / l[j][j];
  }
  return out;
}

test('prepareTemplate Hessian: sum of jac jac^T, exactly symmetric, positive definite on texture, zero on a flat image', () => {
  for (const [t, k] of [[TEMPLATE_A.l1, K1], [TEMPLATE_A.l2, K2]] as [TemplateLevel, Intrinsics][]) {
    const want = new Float64Array(9);
    for (let i = 0; i < k.w * k.h; i++) for (let a = 0; a < 3; a++) for (let b = 0; b < 3; b++) want[3 * a + b] += t.jac[3 * i + a] * t.jac[3 * i + b];
    for (let j = 0; j < 9; j++) assert.ok(Math.abs(t.hess[j] - want[j]) <= 1e-5 * Math.abs(want[j]) + 1e-6, `${k.w} x ${k.h} hess[${j}] ${t.hess[j]} vs ${want[j]}`);
    assert.equal(t.hess[1], t.hess[3]); assert.equal(t.hess[2], t.hess[6]); assert.equal(t.hess[5], t.hess[7]);
    const p = pivots(t.hess), trace = t.hess[0] + t.hess[4] + t.hess[8];
    assert.ok(p.every(v => v > 1e-6 * trace), `${k.w} x ${k.h}: pivots ${p} against trace ${trace}`);
  }
  const flat = prepareTemplate(buildPyramid(greyRgba(SHORT, LONG, () => 120), SHORT, LONG).pyr, K1, K2);
  for (const t of [flat.l1, flat.l2]) {
    assert.ok(t.hess.every(v => v === 0), 'no texture, no information');
    assert.ok(t.gx.every(v => v === 0) && t.jac.every(v => v === 0));
    assert.ok(pivots(t.hess)[0] <= 0, 'and it is not positive definite');
  }
});

/** Solve a 3x3 system by Gaussian elimination with partial pivoting. */
function solve3(m: Float64Array, g: number[]): V3 {
  const a = [[m[0], m[1], m[2], g[0]], [m[3], m[4], m[5], g[1]], [m[6], m[7], m[8], g[2]]];
  for (let c = 0; c < 3; c++) {
    let p = c;
    for (let r = c + 1; r < 3; r++) if (Math.abs(a[r][c]) > Math.abs(a[p][c])) p = r;
    [a[c], a[p]] = [a[p], a[c]];
    for (let r = c + 1; r < 3; r++) { const f = a[r][c] / a[c][c]; for (let q = c; q < 4; q++) a[r][q] -= f * a[c][q]; }
  }
  const x: V3 = [0, 0, 0];
  for (let r = 2; r >= 0; r--) x[r] = (a[r][3] - (r < 2 ? a[r][r + 1] * x[r + 1] : 0) - (r < 1 ? a[r][r + 2] * x[r + 2] : 0)) / a[r][r];
  return x;
}

/** One inverse-compositional step as T21 will write it (see the header of pyramid.ts): the update is qmul(q, qinv(expSO3(d))). */
function lkStep(q: Quat, template: TemplateLevel, tmpl: Level, img: Level, k: Intrinsics): Quat {
  const out = new Float32Array(k.w * k.h);
  warpLevel(img, rotationHomography(k, k, q), out, k.w, k.h);
  const g = [0, 0, 0];
  for (let i = 0; i < out.length; i++) {
    if (Number.isNaN(out[i])) continue;
    const e = out[i] - tmpl.px[i];
    for (let a = 0; a < 3; a++) g[a] += template.jac[3 * i + a] * e;
  }
  return qmul(q, qinv(expSO3(solve3(template.hess, g))));
}

test('an inverse-compositional loop on the template recovers a rotation of 1.6 deg yaw, -1.1 pitch, 0.9 roll', () => {
  let q: Quat = Q_IDENTITY;
  const start = angleBetweenDeg(q, Q_TRUE);
  assert.ok(start > 2, `the start is ${start} degrees off`);
  for (let it = 0; it < 6; it++) q = lkStep(q, TEMPLATE_A.l2, PYR_A.l2, PYR_B.l2, K2);
  const afterL2 = angleBetweenDeg(q, Q_TRUE);
  assert.ok(afterL2 < 0.02, `after L2: ${afterL2} degrees`);
  for (let it = 0; it < 4; it++) q = lkStep(q, TEMPLATE_A.l1, PYR_A.l1, PYR_B.l1, K1);
  const afterL1 = angleBetweenDeg(q, Q_TRUE);
  assert.ok(afterL1 < 0.01, `after L1: ${afterL1} degrees`);
  assert.ok(afterL1 < afterL2, 'the finer level refines');
});

console.log(`pyramid.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
