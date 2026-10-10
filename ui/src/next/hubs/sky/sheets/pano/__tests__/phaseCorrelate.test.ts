// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T07: phase correlation for the panorama scanner (SPEC-v2 4.6 step 2, 7.2).
//
// Mutant this file must catch (SPEC-v2 7.2, RI m7): the Hann window removed (the `win` array filled with 1 in
// `makePhaseScratch`). The integer and sub-pixel cases below shift NON-CIRCULAR crops of a larger texture, which is what
// a window is for: with no window the crop edges are a shifted-in step that the phase-only spectrum cannot ignore, and
// every estimate is pulled toward zero or lands on the wrong integer. A circular shift of one periodic image would pass
// without the window and proves nothing (RI m7).
//
// The 7.2 row asks for 0.1 px on every shift and PSR < 4 on unrelated images. The algorithm it describes cannot give
// either (measured below), so the T07 review restated the row (ruling S4, SPEC-v2 12.4) to the bounds asserted here. Each
// bound was calibrated on 300 seeded textures (or 2,000 white-noise pairs) and is asserted on a subset, so none passes
// because of a lucky seed:
//   * Integer shifts up to 7 px: every error within 0.1 px (worst of 300 textures 0.098). The error is the window pulling
//     the peak toward zero shift, so it grows with the shift: 0.18 px at the 25 percent reliable limit (8, 16).
//   * Sub-pixel shifts: every error within 0.35 px and the mean within 0.18 px (nine shifts, 300 textures: worst 0.312,
//     mean 0.145, 95th percentile 0.229). A parabola through three samples of a sinc-like peak is biased by 0.091, 0.117
//     and 0.106 px at fractions 0.2, 0.3 and 0.4 on an ideal 32-point peak with no window and no noise; the window pull and
//     the aliasing of the sampled texture come on top. At a HALF-pixel shift the parabola is unbiased, so only the window
//     pull is left (mean 0.09 to 0.11 px near zero shift), and a mis-scaled vertex shows there: the mean must stay within
//     0.14 px (vertex halved: 0.29; vertex x 1.5: 0.19). A ratio estimator for a sinc peak (delta = c1 / (c0 + c1)) gave
//     0.02 to 0.06 px on sub-pixel shifts but is worse than the parabola on large integer shifts (0.11 to 0.16 px at 8 px)
//     and is not what SPEC-v2 describes.
//   * Unrelated images do not give PSR < 4: the Hann window makes the null surface noisier near zero shift than the
//     sidelobe sd says. At 32 x 64 independent white-noise pairs have a median PSR of 4.6 (2,000 pairs; 12 percent fall
//     below 4, 0.55 percent reach 7, the maximum is 7.6) and independent value-noise pairs a median of 4.7 (600 pairs; 2.3
//     percent reach 7, the maximum is 10.2). So the file asserts a median below 5 and at most 5 percent at the acceptance
//     value 7. PSR >= 7 alone does not reject a wrong match; the ZNCC and inlier gates of 4.6 have to carry that. Crops of
//     ONE texture far apart are not unrelated for this purpose (they share a lattice orientation and repeat structure):
//     33 percent of them reach 7 at 32 x 64 and 82 percent at 64 x 128. That is measured, not asserted here.
import assert from 'node:assert/strict';
import { makePhaseScratch, phaseCorrelate } from '../phaseCorrelate';
import type { PhaseResult } from '../types';

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

// ---- A larger value-noise texture, sampled like a sensor --------------------------------------------------------------

function lattice(ix: number, iy: number, seed: number): number {
  let h = (Math.imul(ix, 0x27d4eb2d) ^ Math.imul(iy, 0x165667b1) ^ Math.imul(seed + 1, 0x9e3779b1)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b); h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35); h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
const smooth = (t: number) => t * t * (3 - 2 * t);
function valueNoise(x: number, y: number, seed: number): number {
  const x0 = Math.floor(x), y0 = Math.floor(y), fx = smooth(x - x0), fy = smooth(y - y0);
  const top = lattice(x0, y0, seed) * (1 - fx) + lattice(x0 + 1, y0, seed) * fx;
  const bot = lattice(x0, y0 + 1, seed) * (1 - fx) + lattice(x0 + 1, y0 + 1, seed) * fx;
  return top * (1 - fy) + bot * fy;
}
/** Three octaves of value noise (lattice spacing 12, 6 and 3 px), each on its own rotated and offset lattice so that two
 *  seeds never share a lattice, then averaged over each pixel's footprint (2 x 2 samples) as a sensor would. It is defined
 *  everywhere, so a crop at any offset, whole or fractional, is a true translation of the scene. */
function makeTexture(seed: number): (x: number, y: number) => number {
  const octaves = [12, 6, 3].map((spacing, i) => {
    const theta = lattice(i, 1, seed * 31) * Math.PI;
    return { spacing, c: Math.cos(theta), s: Math.sin(theta), ox: lattice(i, 2, seed * 31) * 1000, oy: lattice(i, 3, seed * 31) * 1000, amp: 0.6 ** i };
  });
  const total = octaves.reduce((s, o) => s + o.amp, 0);
  const at = (x: number, y: number) => {
    let v = 0;
    octaves.forEach((o, i) => { v += o.amp * (valueNoise((o.c * x + o.s * y) / o.spacing + o.ox, (-o.s * x + o.c * y) / o.spacing + o.oy, seed * 7 + i) - 0.5); });
    return 128 + 220 * v / total;
  };
  return (x, y) => (at(x - 0.25, y - 0.25) + at(x + 0.25, y - 0.25) + at(x - 0.25, y + 0.25) + at(x + 0.25, y + 0.25)) / 4;
}
function crop(tex: (x: number, y: number) => number, ox: number, oy: number, w: number, h: number): Float32Array {
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) out[y * w + x] = tex(x + ox, y + oy);
  return out;
}
const textures = new Map<number, (x: number, y: number) => number>();
function textureOf(seed: number): (x: number, y: number) => number {
  let t = textures.get(seed);
  if (!t) { t = makeTexture(seed); textures.set(seed, t); }
  return t;
}
/** b is a with its content moved by (dx, dy): a feature at a(x, y) is at b(x + dx, y + dy). Both are crops of one texture. */
function pair(seed: number, dx: number, dy: number, w: 16 | 32 | 64, h: 16 | 64 | 128) {
  const tex = textureOf(seed);
  return { a: crop(tex, 60, 80, w, h), b: crop(tex, 60 - dx, 80 - dy, w, h) };
}
function noiseImage(r: () => number, n: number): Float32Array {
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = r() * 255;
  return out;
}

const W = 32, H = 64, SEEDS = Array.from({ length: 24 }, (_, i) => i + 1);
const scratch = makePhaseScratch(W, H);
/** The error (the larger of the two axes) at each seed, and the smallest PSR. */
function errorsAt(dx: number, dy: number): { errs: number[]; minPsr: number } {
  const errs: number[] = [];
  let minPsr = Infinity;
  for (const seed of SEEDS) {
    const { a, b } = pair(seed, dx, dy, W, H);
    const r = phaseCorrelate(a, b, W, H, scratch);
    errs.push(Math.max(Math.abs(r.dx - dx), Math.abs(r.dy - dy)));
    minPsr = Math.min(minPsr, r.psr);
  }
  return { errs, minPsr };
}
const meanOf = (v: readonly number[]) => v.reduce((s, x) => s + x, 0) / v.length;
const medianOf = (v: readonly number[]) => [...v].sort((a, b) => a - b)[Math.floor(v.length / 2)];
const worstError = (dx: number, dy: number) => { const { errs, minPsr } = errorsAt(dx, dy); return { err: Math.max(...errs), minPsr }; };

// ---- The scratch and the window -----------------------------------------------------------------------------------------

test('makePhaseScratch sizes its arrays and builds a symmetric Hann window', () => {
  for (const [w, h] of [[16, 16], [32, 64], [64, 128]] as [16 | 32 | 64, 16 | 64 | 128][]) {
    const s = makePhaseScratch(w, h);
    assert.equal(s.w, w); assert.equal(s.h, h);
    for (const arr of [s.re, s.im, s.re2, s.im2, s.win]) { assert.ok(arr instanceof Float64Array); assert.equal(arr.length, w * h); }
    let sum = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const v = s.win[y * w + x];
      sum += v;
      assert.ok(v > 0 && v <= 1, `win[${x},${y}] = ${v}`);
      assert.ok(Math.abs(v - s.win[y * w + (w - 1 - x)]) < 1e-12, 'mirror in x');
      assert.ok(Math.abs(v - s.win[(h - 1 - y) * w + x]) < 1e-12, 'mirror in y');
      const expect = Math.sin(Math.PI * (x + 0.5) / w) ** 2 * Math.sin(Math.PI * (y + 0.5) / h) ** 2;
      assert.ok(Math.abs(v - expect) < 1e-12, `win[${x},${y}] ${v} != ${expect}`);
    }
    assert.ok(Math.abs(sum - w * h / 4) < 1e-9, `window sum ${sum} != ${w * h / 4}`);
    assert.ok(s.win[0] < 0.01 && s.win[(h >> 1) * w + (w >> 1)] > 0.97, 'tapers to the edge, near 1 at the centre');
  }
  assert.throws(() => makePhaseScratch(24 as never, 64), RangeError);
  assert.throws(() => makePhaseScratch(32, 8 as never), RangeError);
});

test('a scratch of another size is refused, and scratches of several sizes do not disturb each other', () => {
  const small = makePhaseScratch(16, 16), big = makePhaseScratch(64, 128);
  const { a, b } = pair(1, 2, -3, W, H);
  assert.throws(() => phaseCorrelate(a, b, W, H, small), RangeError);
  const first = phaseCorrelate(a, b, W, H, scratch);
  const sa = pair(2, 1, 1, 16, 16), ba = pair(3, -3, 4, 64, 128);
  phaseCorrelate(sa.a, sa.b, 16, 16, small);
  phaseCorrelate(ba.a, ba.b, 64, 128, big);
  const again = phaseCorrelate(a, b, W, H, scratch);
  assert.deepEqual(again, first, 'the same call gives the same result after other sizes ran');
  assert.deepEqual(phaseCorrelate(a, b, W, H, scratch), first, 'and the scratch carries nothing from call to call');
});

// ---- Agreement with a plain DFT -------------------------------------------------------------------------------------------

/** The same pipeline written the slow way: direct O(n^2) transforms, no tables, no in-place permutation. */
function referenceCorrelate(a: Float32Array, b: Float32Array, w: number, h: number): PhaseResult {
  const n = w * h;
  const prepare = (src: Float32Array) => {
    const fin = [...src].filter(Number.isFinite);
    const mean = fin.length ? fin.reduce((s, v) => s + v, 0) / fin.length : 0;
    const re = new Float64Array(n), im = new Float64Array(n);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const v = src[y * w + x];
      re[y * w + x] = (Number.isFinite(v) ? v - mean : 0) * Math.sin(Math.PI * (x + 0.5) / w) ** 2 * Math.sin(Math.PI * (y + 0.5) / h) ** 2;
    }
    return { re, im };
  };
  const dft2 = (re: Float64Array, im: Float64Array, sign: number) => {
    const tr = new Float64Array(n), ti = new Float64Array(n);
    for (let y = 0; y < h; y++) for (let k = 0; k < w; k++) {
      let sr = 0, si = 0;
      for (let x = 0; x < w; x++) {
        const ang = sign * 2 * Math.PI * ((k * x) % w) / w, c = Math.cos(ang), s = Math.sin(ang);
        sr += re[y * w + x] * c - im[y * w + x] * s; si += re[y * w + x] * s + im[y * w + x] * c;
      }
      tr[y * w + k] = sr; ti[y * w + k] = si;
    }
    const or = new Float64Array(n), oi = new Float64Array(n);
    for (let x = 0; x < w; x++) for (let l = 0; l < h; l++) {
      let sr = 0, si = 0;
      for (let y = 0; y < h; y++) {
        const ang = sign * 2 * Math.PI * ((l * y) % h) / h, c = Math.cos(ang), s = Math.sin(ang);
        sr += tr[y * w + x] * c - ti[y * w + x] * s; si += tr[y * w + x] * s + ti[y * w + x] * c;
      }
      or[l * w + x] = sr; oi[l * w + x] = si;
    }
    return { re: or, im: oi };
  };
  const A = prepare(a), B = prepare(b);
  const fa = dft2(A.re, A.im, -1), fb = dft2(B.re, B.im, -1);
  const cre = new Float64Array(n), cim = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const cr = fb.re[i] * fa.re[i] + fb.im[i] * fa.im[i], ci = fb.im[i] * fa.re[i] - fb.re[i] * fa.im[i], m = Math.hypot(cr, ci);
    if (m > 1e-6) { cre[i] = cr / m; cim[i] = ci / m; }
  }
  const s = dft2(cre, cim, 1).re;
  let at = 0;
  for (let i = 1; i < n; i++) if (s[i] > s[at]) at = i;
  const px = at % w, py = (at - px) / w;
  const side: number[] = [];
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const ex = Math.min(Math.abs(x - px), w - Math.abs(x - px)), ey = Math.min(Math.abs(y - py), h - Math.abs(y - py));
    if (ex > 5 || ey > 5) side.push(s[y * w + x]);
  }
  const mean = side.reduce((t, v) => t + v, 0) / side.length;
  const sd = Math.sqrt(side.reduce((t, v) => t + (v - mean) ** 2, 0) / side.length);
  const at2 = (x: number, y: number) => s[((y + h) % h) * w + ((x + w) % w)];
  const vertex = (m: number, c: number, p: number) => (m - 2 * c + p < 0 ? 0.5 * (m - p) / (m - 2 * c + p) : 0);
  const c0 = at2(px, py);
  return {
    dx: (px < w / 2 ? px : px - w) + vertex(at2(px - 1, py), c0, at2(px + 1, py)),
    dy: (py < h / 2 ? py : py - h) + vertex(at2(px, py - 1), c0, at2(px, py + 1)),
    psr: s[at] > 1e-9 ? (s[at] - mean) / Math.max(sd, 1e-9 * s[at]) : 0,
  };
}

test('phaseCorrelate agrees with a direct DFT on 16 x 16 and 32 x 64, NaN samples included', () => {
  const r = rng(11);
  for (const [w, h] of [[16, 16], [32, 64]] as [16 | 32, 16 | 64][]) {
    const sc = makePhaseScratch(w, h);
    for (let k = 0; k < 3; k++) {
      const { a, b } = pair(20 + k, k - 1.4, 2 - k, w, h);
      if (k === 2) { for (let i = 0; i < 12; i++) b[Math.floor(r() * w * h)] = NaN; for (let i = 0; i < 12; i++) a[Math.floor(r() * w * h)] = NaN; }
      const got = phaseCorrelate(a, b, w, h, sc), want = referenceCorrelate(a, b, w, h);
      assert.ok(Math.abs(got.dx - want.dx) < 1e-6 && Math.abs(got.dy - want.dy) < 1e-6, `${w}x${h} #${k}: (${got.dx}, ${got.dy}) != (${want.dx}, ${want.dy})`);
      assert.ok(Math.abs(got.psr - want.psr) < 1e-6 * Math.max(1, want.psr), `${w}x${h} #${k}: psr ${got.psr} != ${want.psr}`);
    }
  }
});

// ---- Shifts of non-circular crops --------------------------------------------------------------------------------------

test('integer shifts of non-circular crops are recovered within 0.1 px, with a clear peak', () => {
  for (const [dx, dy] of [[0, 0], [3, -2], [-4, 5], [2, -7], [-5, 3], [1, 6]]) {
    const { err, minPsr } = worstError(dx, dy);
    assert.ok(err < 0.1, `shift (${dx}, ${dy}): worst error ${err.toFixed(3)} px`);
    if (dx !== 0 || dy !== 0) assert.ok(minPsr >= 7, `shift (${dx}, ${dy}): PSR ${minPsr.toFixed(1)}`);
  }
});

test('sign convention: content that moves right and down gives a positive dx and dy', () => {
  const r = rng(5), base = noiseImage(r, 40 * 80), a = new Float32Array(W * H), b = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    a[y * W + x] = base[(y + 8) * 40 + x + 4];
    b[y * W + x] = base[(y + 8 - 3) * 40 + x + 4 - 2];   // b(x, y) = a(x - 2, y - 3): the content moved by (+2, +3)
  }
  const res = phaseCorrelate(a, b, W, H, scratch);
  assert.ok(Math.abs(res.dx - 2) < 0.15 && Math.abs(res.dy - 3) < 0.15, `(${res.dx}, ${res.dy})`);
  const back = phaseCorrelate(b, a, W, H, scratch);
  assert.ok(Math.abs(back.dx + 2) < 0.15 && Math.abs(back.dy + 3) < 0.15, `swapped: (${back.dx}, ${back.dy})`);
});

test('sub-pixel shifts of non-circular crops: every error within 0.35 px, the mean within 0.18 px, a clear peak', () => {
  const all: number[] = [];
  for (const [dx, dy] of [[0.5, 0.5], [-0.5, 0.5], [2.5, -1.5], [-0.5, -3.5], [0.3, -0.45], [0.25, 0.75], [1.35, -1.7], [-1.35, 2.2], [2.45, -3.7]]) {
    const { errs, minPsr } = errorsAt(dx, dy);
    assert.ok(Math.max(...errs) <= 0.35, `shift (${dx}, ${dy}): worst error ${Math.max(...errs).toFixed(3)} px`);
    assert.ok(minPsr >= 7, `shift (${dx}, ${dy}): PSR ${minPsr.toFixed(1)}`);
    all.push(...errs);
  }
  assert.ok(meanOf(all) <= 0.18, `mean error ${meanOf(all).toFixed(3)} px over ${all.length} correlations`);
});

test('half-pixel shifts near zero: the parabola is unbiased there, so the mean error stays within 0.14 px', () => {
  // Only the window pull is left at a half pixel. A vertex scaled by 0.5 gives a mean of 0.29 px, by 1.5 gives 0.19 px.
  for (const [dx, dy] of [[0.5, 0.5], [-0.5, 0.5], [-0.5, -3.5]]) {
    const m = meanOf(errorsAt(dx, dy).errs);
    assert.ok(m <= 0.14, `shift (${dx}, ${dy}): mean error ${m.toFixed(3)} px over ${SEEDS.length} textures`);
  }
});

test('the reliable range of SPEC-v2 4.6 holds: a quarter of the window in each axis is still found, PSR >= 7', () => {
  for (const [dx, dy] of [[8, -16], [-8, 16], [8, 16]]) {
    const { err, minPsr } = worstError(dx, dy);
    assert.ok(err < 0.25, `shift (${dx}, ${dy}): worst error ${err.toFixed(3)} px`);
    assert.ok(minPsr >= 7, `shift (${dx}, ${dy}): PSR ${minPsr.toFixed(1)}`);
  }
});

test('the closure window: a 45 x 80 frame padded with NaN into 64 x 128 recovers 12 px and -20 px; a luma-0 pad does not', () => {
  const sc = makePhaseScratch(64, 128), dx = 12, dy = -20;
  const padded = (tex: (x: number, y: number) => number, ox: number, oy: number, pad: number) => {
    const out = new Float32Array(64 * 128).fill(pad);
    for (let y = 0; y < 80; y++) for (let x = 0; x < 45; x++) out[(y + 24) * 64 + x + 9] = tex(x + ox, y + oy);
    return out;
  };
  let zeroPadWrong = 0;
  for (const seed of SEEDS) {
    const tex = textureOf(seed), res = phaseCorrelate(padded(tex, 100, 100, NaN), padded(tex, 100 - dx, 100 - dy, NaN), 64, 128, sc);
    assert.ok(Math.abs(res.dx - dx) < 0.2 && Math.abs(res.dy - dy) < 0.2, `texture ${seed}: (${res.dx}, ${res.dy})`);
    assert.ok(res.psr >= 7, `texture ${seed}: PSR ${res.psr}`);
    // The pad is the same rectangle in both windows. Mean removal turns a luma-0 pad (the texture's mean is about 128) into
    // a large negative rectangle that dominates the phase spectrum and answers "no shift". NaN becomes the mean, so it
    // leaves no step: this is why a caller pads with NaN (SPEC-v2 4.6, the closure window).
    const zero = phaseCorrelate(padded(tex, 100, 100, 0), padded(tex, 100 - dx, 100 - dy, 0), 64, 128, sc);
    if (Math.abs(zero.dx - dx) > 1 || Math.abs(zero.dy - dy) > 1) zeroPadWrong++;
  }
  assert.ok(zeroPadWrong >= 5, `a luma-0 pad was wrong on ${zeroPadWrong} of ${SEEDS.length} textures; the NaN convention would be unproven`);
});

// ---- Gain, offset, NaN ------------------------------------------------------------------------------------------------

test('the estimate does not change with a gain of 0.5 to 2 or with an offset, on either image', () => {
  const { a, b } = pair(2, 3, -2, W, H), base = phaseCorrelate(a, b, W, H, scratch);
  assert.ok(Math.abs(base.dx - 3) < 0.1 && Math.abs(base.dy + 2) < 0.1);
  for (const gain of [0.5, 1, 2]) for (const offset of [-60, 0, 45]) {
    const bg = new Float32Array(b.length), ag = new Float32Array(a.length);
    for (let i = 0; i < b.length; i++) { bg[i] = gain * b[i] + offset; ag[i] = 0.8 * a[i] + 17 + offset / 2; }
    const r1 = phaseCorrelate(a, bg, W, H, scratch), r2 = phaseCorrelate(ag, bg, W, H, scratch);
    for (const r of [r1, r2]) {
      assert.ok(Math.abs(r.dx - base.dx) < 1e-3 && Math.abs(r.dy - base.dy) < 1e-3, `gain ${gain} offset ${offset}: (${r.dx}, ${r.dy}) vs (${base.dx}, ${base.dy})`);
      assert.ok(Math.abs(r.psr - base.psr) < 1e-3 * base.psr, `gain ${gain} offset ${offset}: psr ${r.psr} vs ${base.psr}`);
    }
  }
});

test('NaN samples count as the window mean, and an all-NaN or uniform window has no peak', () => {
  const { a, b } = pair(3, -2, 3, W, H);
  const holed = Float32Array.from(b);
  let sum = 0, count = 0;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (x >= 22 || (y >= 10 && y < 20 && x < 8)) holed[y * W + x] = NaN; else { sum += b[y * W + x]; count++; }
  }
  const filled = Float32Array.from(holed).map(v => (Number.isNaN(v) ? sum / count : v));
  const r1 = phaseCorrelate(a, holed, W, H, scratch), r2 = phaseCorrelate(a, filled, W, H, scratch);
  assert.ok(Math.abs(r1.dx - r2.dx) < 1e-3 && Math.abs(r1.dy - r2.dy) < 1e-3 && Math.abs(r1.psr - r2.psr) < 1e-2 * r2.psr, `NaN (${r1.dx}, ${r1.dy}, ${r1.psr}) vs mean (${r2.dx}, ${r2.dy}, ${r2.psr})`);
  assert.ok(Math.abs(r1.dx + 2) < 0.4 && Math.abs(r1.dy - 3) < 0.4, `still near the truth: (${r1.dx}, ${r1.dy})`);
  const allNaN = new Float32Array(W * H).fill(NaN), grey = new Float32Array(W * H).fill(127.3);
  for (const [x, y] of [[allNaN, a], [a, allNaN], [allNaN, allNaN], [grey, grey], [grey, a], [a, grey]] as [Float32Array, Float32Array][]) {
    const r = phaseCorrelate(x, y, W, H, scratch);
    assert.deepEqual(r, { dx: 0, dy: 0, psr: 0 });
  }
});

// ---- PSR ---------------------------------------------------------------------------------------------------------------

/** The smallest PSR over every texture and four shifts (the identical pair is asserted on the way, not counted). */
function matchMinPsr(): number {
  let minMatch = Infinity;
  for (const seed of SEEDS) for (const [dx, dy] of [[0, 0], [1, 1], [-2.5, 3], [4, -6]]) {
    const { a, b } = pair(seed, dx, dy, W, H);
    const r = phaseCorrelate(a, b, W, H, scratch);
    // Identical images are a perfect delta: the sidelobes are rounding only, so the ratio is enormous but finite.
    assert.ok(Number.isFinite(r.psr), `psr ${r.psr}`);
    if (dx !== 0 || dy !== 0) minMatch = Math.min(minMatch, r.psr);
    else assert.ok(r.psr >= 7);
  }
  return minMatch;
}

test('PSR: at least 7 on every match, exactly 0 on a uniform window', () => {
  const minMatch = matchMinPsr();
  assert.ok(minMatch >= 7, `match PSR ${minMatch}`);
  const grey = new Float32Array(W * H).fill(128);
  assert.equal(phaseCorrelate(grey, grey, W, H, scratch).psr, 0);
});

test('PSR on unrelated images: the median is below 5 and at most 5 percent reach the acceptance value 7', () => {
  const r = rng(77), white: number[] = [], textured: number[] = [];
  for (let k = 0; k < 200; k++) {
    white.push(phaseCorrelate(noiseImage(r, W * H), noiseImage(r, W * H), W, H, scratch).psr);
    // Two seeds never share a lattice (makeTexture rotates and offsets every octave per seed), so the pair is unrelated.
    textured.push(phaseCorrelate(crop(textureOf(1 + k), 60, 80, W, H), crop(textureOf(5001 + k), 60, 80, W, H), W, H, scratch).psr);
  }
  for (const [name, psrs] of [['white noise', white], ['value noise', textured]] as [string, number[]][]) {
    const med = medianOf(psrs), reach = psrs.filter(p => p >= 7).length;
    assert.ok(med < 5, `${name}: median unrelated PSR ${med.toFixed(2)}`);
    assert.ok(reach <= 0.05 * psrs.length, `${name}: ${reach} of ${psrs.length} unrelated pairs reach PSR 7`);
  }
  // The matches sit far above the bulk of the unrelated pairs, though not above the tail of the worst of them.
  const minMatch = matchMinPsr(), bulk = medianOf([...white, ...textured]);
  assert.ok(minMatch > 2 * bulk, `matches (${minMatch.toFixed(1)}) are not clearly above unrelated pairs (median ${bulk.toFixed(2)})`);
});

// ---- Allocation --------------------------------------------------------------------------------------------------------

test('phaseCorrelate allocates no array: only makePhaseScratch does', () => {
  const ctors = ['Float64Array', 'Float32Array', 'Float16Array', 'Uint8Array', 'Uint8ClampedArray', 'Uint16Array', 'Uint32Array', 'Int8Array', 'Int16Array', 'Int32Array', 'Array', 'ArrayBuffer'];
  const g = globalThis as unknown as Record<string, unknown>, saved: Record<string, unknown> = {};
  let made = 0;
  for (const name of ctors) {
    if (typeof g[name] !== 'function') continue;
    saved[name] = g[name];
    g[name] = new Proxy(g[name] as new (...args: unknown[]) => unknown, { construct(target, args, newTarget) { made++; return Reflect.construct(target, args, newTarget === g[name] ? target : newTarget); } });
  }
  try {
    const { a, b } = pair(1, 2, -3, 16, 16);   // built before counting would be wrong: count only the calls below
    made = 0;
    const sc = makePhaseScratch(16, 16);
    assert.ok(made >= 5, `makePhaseScratch made ${made} arrays; the counter is not seeing constructions`);
    made = 0;
    for (let i = 0; i < 5; i++) phaseCorrelate(a, b, 16, 16, sc);
    assert.equal(made, 0, `phaseCorrelate constructed ${made} arrays`);
  } finally {
    for (const name of Object.keys(saved)) g[name] = saved[name];
  }
});

console.log(`phaseCorrelate.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
