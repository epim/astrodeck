// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Image noise for the horizon extractors (SPEC-v2 5.1 item 5). The Immerkaer estimator, ported from
// `noiseGradient` in photosphereStability.ts (364-389): it lives in its own module so that a future second
// extractor can share it (RI M25), and it keeps no scratch state beyond one histogram.
//
// The mask is [1 -2 1] (x) [1 -2 1], the product of a second difference along x and one along y. It
// annihilates anything that depends on x alone or on y alone: a flat patch, a ramp, and, which is the point,
// a horizon (an edge that is constant along x). What it answers to is white noise, and to genuine two
// dimensional structure (corners, diagonals, foliage). On white noise of standard deviation s the response
// has standard deviation 6 s, and its median absolute value is 6 s x 0.6745.

/** The standard deviation of the response to unit white noise: the squares of the nine coefficients sum to
 *  4 x 1 + 4 x 4 + 16 = 36. */
const RESPONSE_GAIN = 6;
/** The median of |z| for z standard normal. */
const NORMAL_MEDIAN = 0.6744897501960817;
/** Histogram resolution, bins per response unit. Integer (8-bit) input has an integer response, which a bin of
 *  1/8 holds exactly, so the median of an 8-bit image is read without quantisation error; float input is read
 *  to 1/8 of a level. */
const BINS_PER_UNIT = 8;
/** 16 x 255: the largest response of a plane whose samples lie in 0..255 or -255..255. */
const RESPONSE_MAX = 4096;
const HIST_LAST = RESPONSE_MAX * BINS_PER_UNIT;
// One module-level scratch histogram, as photosphereStability keeps: allocating 128 KiB per call is garbage the
// tracer does not need. Safe to share because the function is synchronous and calls nothing that could re-enter.
const histogram = new Int32Array(HIST_LAST + 1);

/** The signed Immerkaer response at flat index `i` of a plane `w` wide. The caller guarantees the eight
 *  neighbours exist. The tracer reads the same number as its texture channel, so there is one definition. */
export function immerkaer(p: ArrayLike<number>, i: number, w: number): number {
  return p[i - w - 1] - 2 * p[i - w] + p[i - w + 1]
    - 2 * p[i - 1] + 4 * p[i] - 2 * p[i + 1]
    + p[i + w - 1] - 2 * p[i + w] + p[i + w + 1];
}

/**
 * The noise standard deviation of a `w` x `h` plane, in the plane's own units: the median absolute response
 * of the mask over the interior, divided by 6 x 0.6745. A median, not Immerkaer's mean, because a plane that
 * holds structure must not read as noisy (the tracer runs this over a raster with terrain in it).
 *
 * `mask` (1 = use the pixel): a window is used only when all nine of its samples are masked in, so a caller
 * that excludes unpainted pixels need not erode its own mask. Returns 0 for a plane smaller than 3 x 3, a
 * short buffer, or a mask that admits no window.
 */
export function noiseSigma(luma: Float32Array | Uint8Array, w: number, h: number, mask?: Uint8Array): number {
  if (!(w >= 3 && h >= 3) || luma.length < w * h || (mask !== undefined && mask.length < w * h)) return 0;
  histogram.fill(0);
  let n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      if (mask !== undefined && !(mask[i] && mask[i - 1] && mask[i + 1]
        && mask[i - w - 1] && mask[i - w] && mask[i - w + 1]
        && mask[i + w - 1] && mask[i + w] && mask[i + w + 1])) continue;
      const v = Math.abs(immerkaer(luma, i, w));
      if (!(v >= 0)) continue;   // NaN: the sample is not a measurement
      histogram[Math.min(HIST_LAST, Math.round(v * BINS_PER_UNIT))]++;
      n++;
    }
  }
  if (n === 0) return 0;
  let seen = 0, median = 0;
  for (let a = 0; a <= HIST_LAST; a++) {
    seen += histogram[a];
    if (seen * 2 >= n) { median = a / BINS_PER_UNIT; break; }
  }
  return median / (RESPONSE_GAIN * NORMAL_MEDIAN);
}
