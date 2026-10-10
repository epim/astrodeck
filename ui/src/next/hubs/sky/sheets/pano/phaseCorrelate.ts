// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Phase correlation for the horizon panorama scanner (SPEC-v2 4.6 step 2): the translation between two small luma
// windows, from the peak of the phase-only cross-power spectrum. The keyframe window is 32 x 64 at L2 and the closure
// window 64 x 128. A caller pads a smaller frame with NaN, never with luma 0: NaN is treated as the window mean, while a
// zero pad becomes a large negative rectangle after mean removal and the peak lands at zero shift.
//
// Allocation happens only in `makePhaseScratch` (the five work arrays and the per-size FFT tables, which are shared and
// built the first time a size is asked for). `phaseCorrelate` allocates nothing but its small result.
import type { PhaseResult, PhaseScratch } from './types';

/** Half the side of the sidelobe exclusion box: the box is 11 x 11 around the peak. */
const BOX_HALF = 5;
/** A cross-power bin smaller than this has no phase worth keeping (a uniform window leaves only rounding in its spectrum). */
const MIN_CROSS = 1e-6;
/** The surface of two informative windows peaks at about the number of informative bins, so anything below this is rounding. */
const MIN_PEAK = 1e-9;
const SD_FLOOR = 1e-9;

interface FftTables { cos: Float64Array; sin: Float64Array; rev: Uint8Array }
const TABLES = new Map<number, FftTables>();

/** cos and sin of 2 pi t / n for t in [0, n / 2), and the bit-reversal permutation of 0 .. n - 1. */
function tablesFor(n: number): FftTables {
  let t = TABLES.get(n);
  if (t) return t;
  const half = n >> 1, cos = new Float64Array(half), sin = new Float64Array(half), rev = new Uint8Array(n);
  for (let i = 0; i < half; i++) { cos[i] = Math.cos(2 * Math.PI * i / n); sin[i] = Math.sin(2 * Math.PI * i / n); }
  const bits = Math.round(Math.log2(n));
  for (let i = 0; i < n; i++) {
    let r = 0;
    for (let b = 0; b < bits; b++) if (i & (1 << b)) r |= 1 << (bits - 1 - b);
    rev[i] = r;
  }
  t = { cos, sin, rev };
  TABLES.set(n, t);
  return t;
}

const isSize = (n: number) => Number.isInteger(n) && n >= 16 && n <= 128 && (n & (n - 1)) === 0;

export function makePhaseScratch(w: 16 | 32 | 64, h: 16 | 64 | 128): PhaseScratch {
  if (!isSize(w) || !isSize(h)) throw new RangeError(`makePhaseScratch: ${w} x ${h} is not a power-of-two window of 16 to 128`);
  tablesFor(w); tablesFor(h);
  const n = w * h, win = new Float64Array(n);
  // Hann sampled at the pixel centres, sin^2(pi (i + 0.5) / n): symmetric, and nonzero at both edges.
  for (let y = 0; y < h; y++) {
    const wy = Math.sin(Math.PI * (y + 0.5) / h) ** 2;
    for (let x = 0; x < w; x++) win[y * w + x] = wy * Math.sin(Math.PI * (x + 0.5) / w) ** 2;
  }
  return { w, h, re: new Float64Array(n), im: new Float64Array(n), re2: new Float64Array(n), im2: new Float64Array(n), win };
}

/** In-place radix-2 FFT of n elements at `base + i * stride`. Forward is e^{-i}, inverse e^{+i}; neither is scaled. */
function fft(re: Float64Array, im: Float64Array, base: number, stride: number, n: number, t: FftTables, inverse: boolean): void {
  const { cos, sin, rev } = t, sgn = inverse ? 1 : -1;
  for (let i = 0; i < n; i++) {
    const j = rev[i];
    if (j > i) {
      const p = base + i * stride, q = base + j * stride;
      const xr = re[p]; re[p] = re[q]; re[q] = xr;
      const xi = im[p]; im[p] = im[q]; im[q] = xi;
    }
  }
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1, step = n / size;
    for (let start = 0; start < n; start += size) {
      for (let k = 0, tw = 0; k < half; k++, tw += step) {
        const wr = cos[tw], wi = sgn * sin[tw];
        const p = base + (start + k) * stride, q = base + (start + k + half) * stride;
        const xr = re[q] * wr - im[q] * wi, xi = re[q] * wi + im[q] * wr;
        re[q] = re[p] - xr; im[q] = im[p] - xi;
        re[p] += xr; im[p] += xi;
      }
    }
  }
}

function fft2(re: Float64Array, im: Float64Array, w: number, h: number, tw: FftTables, th: FftTables, inverse: boolean): void {
  for (let y = 0; y < h; y++) fft(re, im, y * w, 1, w, tw, inverse);
  for (let x = 0; x < w; x++) fft(re, im, x, w, h, th, inverse);
}

/** Mean removed, then windowed, into (re, im = 0). A NaN or infinite sample counts as the mean of the finite ones. */
function load(src: Float32Array, re: Float64Array, im: Float64Array, win: Float64Array, n: number): void {
  let sum = 0, count = 0;
  for (let i = 0; i < n; i++) { const v = src[i]; if (v - v === 0) { sum += v; count++; } }
  const mean = count > 0 ? sum / count : 0;
  for (let i = 0; i < n; i++) {
    const v = src[i];
    re[i] = (v - v === 0 ? v - mean : 0) * win[i];
    im[i] = 0;
  }
}

/** The shift of `b` relative to `a`, in pixels: if b(x) = a(x - d) then (dx, dy) = d, so content moving right and down
 *  gives positive values. Integer shifts are wrapped to [-w / 2, w / 2) by [-h / 2, h / 2). `psr` is the peak-to-
 *  sidelobe ratio, (peak - mean) / sd of the correlation surface outside an 11 x 11 box (wrapped) around the peak; it
 *  is 0 when the surface is flat, which is what a uniform window or a window with no overlap gives. The surface is not
 *  scaled by the transform size; the peak position and the ratio do not depend on it. */
export function phaseCorrelate(a: Float32Array, b: Float32Array, w: 16 | 32 | 64, h: 16 | 64 | 128, scratch: PhaseScratch): PhaseResult {
  if (scratch.w !== w || scratch.h !== h) throw new RangeError(`phaseCorrelate: a ${scratch.w} x ${scratch.h} scratch does not fit a ${w} x ${h} window`);
  const tw = TABLES.get(w), th = TABLES.get(h);
  if (!tw || !th) throw new Error('phaseCorrelate: the scratch was not made by makePhaseScratch');
  const n = w * h, { re, im, re2, im2, win } = scratch;
  load(a, re, im, win, n);
  load(b, re2, im2, win, n);
  fft2(re, im, w, h, tw, th, false);
  fft2(re2, im2, w, h, tw, th, false);
  // Phase-only cross-power Fb conj(Fa) / |Fb conj(Fa)|: for b(x) = a(x - d) it is e^{-i k d}, whose inverse peaks at d.
  // (The DC bin adds one constant to the whole surface, which moves neither the peak, the vertex nor the PSR.)
  for (let i = 0; i < n; i++) {
    const cr = re2[i] * re[i] + im2[i] * im[i], ci = im2[i] * re[i] - re2[i] * im[i], m = Math.sqrt(cr * cr + ci * ci);
    if (m > MIN_CROSS) { re[i] = cr / m; im[i] = ci / m; } else { re[i] = 0; im[i] = 0; }
  }
  fft2(re, im, w, h, tw, th, true);
  // The spectrum is Hermitian, so the surface is real and its imaginary part is rounding only.
  let peak = -Infinity, at = 0;
  for (let i = 0; i < n; i++) if (re[i] > peak) { peak = re[i]; at = i; }
  const px = at % w, py = (at - px) / w;
  let sum = 0, sum2 = 0, count = 0;
  for (let y = 0; y < h; y++) {
    const ey = Math.abs(y - py), outY = Math.min(ey, h - ey) > BOX_HALF;
    for (let x = 0; x < w; x++) {
      const ex = Math.abs(x - px);
      if (!outY && Math.min(ex, w - ex) <= BOX_HALF) continue;
      const v = re[y * w + x];
      sum += v; sum2 += v * v; count++;
    }
  }
  const mean = count > 0 ? sum / count : 0, sd = count > 0 ? Math.sqrt(Math.max(0, sum2 / count - mean * mean)) : 0;
  // A flat surface (a uniform window, or no overlap) has no peak at all. A perfect match has sidelobes of rounding size,
  // so the sd is floored at a billionth of the peak and the ratio stays large but finite.
  const psr = peak > MIN_PEAK ? (peak - mean) / Math.max(sd, SD_FLOOR * peak) : 0;
  // Parabola through the peak and its two neighbours on each axis (cyclic): the vertex offset, within +-0.5.
  const c = re[at], l = re[py * w + (px + w - 1) % w], r = re[py * w + (px + 1) % w];
  const u = re[((py + h - 1) % h) * w + px], d = re[((py + 1) % h) * w + px];
  const dxs = l - 2 * c + r < 0 ? 0.5 * (l - r) / (l - 2 * c + r) : 0;
  const dys = u - 2 * c + d < 0 ? 0.5 * (u - d) / (u - 2 * c + d) : 0;
  return { dx: (px < w / 2 ? px : px - w) + dxs, dy: (py < h / 2 ? py : py - h) + dys, psr };
}
