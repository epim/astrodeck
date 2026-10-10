// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T21 bench: the cost of pair alignment per keyframe, and the false-accept rate of the full acceptance set (SPEC-v2
// 4.6, 4.15, ruling S4).
//
// Not a test, and it gates nothing (RI M31, RS m7): the name does not end in `.test.ts`, so `run-tests.mjs` never runs
// it. Run it by hand from `ui/`:
//
//   npx tsx src/next/hubs/sky/sheets/pano/__bench__/align.bench.ts [pairs]
//
// Costs are the median of many runs after a warm-up, in milliseconds on this machine and in reference units:
// milliseconds over `referenceLoopMs()` from `__sim__/harness.ts` (7.4), which a slow and a fast machine agree on far
// better than on milliseconds. Its first call in a process runs about 35 % slow (S20), so it is warmed first and the
// warm value is the unit. The false-accept rates use `pairs` pairs per set (default 1000): unrelated value-noise pairs,
// far crops of one texture, and a striped fence predicted one period off (the stripes cases of align.test.ts).
import { referenceLoopMs } from '../../__sim__/harness';
import { lookBasis } from '../../photosphereGeometry';
import { alignPair, type RgbStrip } from '../align';
import { buildPyramid, prepareTemplate } from '../pyramid';
import { angleBetweenDeg, expSO3, intrinsicsAt, priorFNorm, qinv, qmul, quatFromBasis } from '../rotation';
import type { AlignWindow, Pyramid, Quat, Template } from '../types';
import { makeSynthScene, renderView, type SynthScene } from '../__tests__/synth/synth';

const W = 180, H = 320, FNORM = priorFNorm(W, H), DEG = Math.PI / 180, RING_PITCH = 22.98;
const K0 = intrinsicsAt(0, W, H, FNORM), K1 = intrinsicsAt(1, W, H, FNORM), K2 = intrinsicsAt(2, W, H, FNORM);
const K = { l0: K0, l1: K1, l2: K2 };
const look = (az: number, alt: number): Quat => quatFromBasis(lookBasis(az, alt));
const relative = (qa: Quat, qb: Quat): Quat => qmul(qinv(qb), qa);
const pyramidOf = (rgba: Uint8ClampedArray): Pyramid => buildPyramid(rgba, W, H).pyr;
const templateOf = (rgba: Uint8ClampedArray): Template => prepareTemplate(pyramidOf(rgba), K1, K2);

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
const gauss = (r: () => number) => Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());
/** The simulator's frame noise: Gaussian, sd 2 per channel (13.5). */
function camera(rgba: Uint8ClampedArray, seed: number): Uint8ClampedArray {
  const r = rng(seed), out = new Uint8ClampedArray(rgba.length);
  for (let i = 0; i < rgba.length; i += 4) { for (let c = 0; c < 3; c++) out[i + c] = rgba[i + c] + 2 * gauss(r); out[i + 3] = 255; }
  return out;
}
function stripOf(rgba: Uint8ClampedArray, x0: number, w: number): RgbStrip {
  const strip = new Uint8Array(w * H * 3);
  for (let y = 0; y < H; y++) for (let x = 0; x < w; x++) for (let c = 0; c < 3; c++) strip[(y * w + x) * 3 + c] = rgba[(y * W + x0 + x) * 4 + c];
  return { strip, x0, w, h: H };
}

/** Median wall time of `fn` in ms over `runs` runs, after `warm` runs that are not counted. */
function timeMs(fn: () => void, runs = 200, warm = 30): number {
  for (let i = 0; i < warm; i++) fn();
  const t: number[] = [];
  for (let i = 0; i < runs; i++) { const t0 = process.hrtime.bigint(); fn(); t.push(Number(process.hrtime.bigint() - t0) / 1e6); }
  t.sort((a, b) => a - b);
  return t[runs >> 1];
}

// ---- Cost per keyframe -------------------------------------------------------------------------------------------------

const scene = makeSynthScene({ seed: 2 });
const qa = look(100, RING_PITCH), qb = look(104, RING_PITCH), truth = relative(qa, qb);
const rgbA = camera(renderView(scene, qa, K0), 1), rgbB = camera(renderView(scene, qb, K0), 2);
const tpl = templateOf(rgbA), img = pyramidOf(rgbB);
const qPred = qmul(expSO3([0.5 * DEG, 2 * DEG, 0.3 * DEG]), truth);   // a typical predictor residual (4.6 step 2 RSS 2.34)
const stripA = stripOf(rgbA, 50, 80), stripB = stripOf(rgbB, 50, 80);
const check = alignPair(tpl, img, qPred, K, { window: 'keyframe' });
if (!check.ok) throw new Error(`bench pair refused: ${check.reason}`);

referenceLoopMs();   // the cold first call (S20)
const unit = referenceLoopMs();
const rows: [string, number][] = [
  ['buildPyramid (luma, L1, L2)', timeMs(() => buildPyramid(rgbB, W, H))],
  ['prepareTemplate (L1 and L2)', timeMs(() => prepareTemplate(img, K1, K2))],
  ['alignPair keyframe', timeMs(() => alignPair(tpl, img, qPred, K, { window: 'keyframe' }))],
  ['alignPair keyframe with RGB strips', timeMs(() => alignPair(tpl, img, qPred, K, { window: 'keyframe', rgbA: stripA, rgbB: stripB }))],
  ['alignPair closure', timeMs(() => alignPair(tpl, img, qPred, K, { window: 'closure' }))],
];
const perKeyframe = rows[0][1] + rows[1][1] + rows[3][1];
console.log(`referenceLoopMs (warm): ${unit.toFixed(2)} ms`);
for (const [name, ms] of rows) console.log(`${name.padEnd(38)} ${ms.toFixed(3).padStart(8)} ms ${(ms / unit).toFixed(3).padStart(8)} units`);
console.log(`${'per keyframe (pyramid + template + align)'.padEnd(38)} ${perKeyframe.toFixed(3).padStart(8)} ms ${(perKeyframe / unit).toFixed(3).padStart(8)} units`);
console.log(`(the pair: ${check.iterations} LK iterations, PSR ${check.psr.toFixed(1)}, ZNCC ${check.zncc.toFixed(3)}, ${angleBetweenDeg(check.qBA, truth).toFixed(4)} degrees from the truth)`);

// ---- False accepts ----------------------------------------------------------------------------------------------------

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
/** phaseCorrelate.test.ts's texture: three octaves at 12, 6 and 3 px on rotated lattices, in L2 pixel units. */
function makeTexture(seed: number): (x: number, y: number) => number {
  const octaves = [12, 6, 3].map((spacing, i) => {
    const theta = lattice(i, 1, seed * 31) * Math.PI;
    return { spacing, c: Math.cos(theta), s: Math.sin(theta), ox: lattice(i, 2, seed * 31) * 1000, oy: lattice(i, 3, seed * 31) * 1000, amp: 0.6 ** i };
  });
  const total = octaves.reduce((s, o) => s + o.amp, 0);
  return (x, y) => {
    let v = 0;
    octaves.forEach((o, i) => { v += o.amp * (valueNoise((o.c * x + o.s * y) / o.spacing + o.ox, (-o.s * x + o.c * y) / o.spacing + o.oy, seed * 7 + i) - 0.5); });
    return 128 + 220 * v / total;
  };
}
function textureFrame(tex: (x: number, y: number) => number, ox: number, oy: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const v = tex(ox + (x + 0.5) / 4, oy + (y + 0.5) / 4), o = (y * W + x) * 4;
    out[o] = v; out[o + 1] = v; out[o + 2] = v; out[o + 3] = 255;
  }
  return out;
}

const pairs = Math.max(1, Number(process.argv[2] ?? 1000) | 0);
const stepPred = relative(look(40, RING_PITCH), look(44, RING_PITCH));
const windows: AlignWindow[] = ['keyframe', 'closure'];
function rate(what: string, make: (i: number) => [Template, Pyramid]): void {
  const tally = { keyframe: { ok: 0, psr7: 0 }, closure: { ok: 0, psr7: 0 } };
  for (let i = 0; i < pairs; i++) {
    const [t, b] = make(i);
    for (const window of windows) {
      const res = alignPair(t, b, stepPred, K, { window });
      if (res.ok) tally[window].ok++;
      if (res.psr >= 7) tally[window].psr7++;
    }
  }
  for (const window of windows) {
    const o = tally[window];
    console.log(`${what}, ${window}: accepted ${o.ok} of ${pairs} (${(100 * o.ok / pairs).toFixed(2)} %), PSR >= 7 on ${(100 * o.psr7 / pairs).toFixed(2)} %`);
  }
}
rate('unrelated value-noise pairs', i => [templateOf(textureFrame(makeTexture(1 + i), 0, 0)), pyramidOf(textureFrame(makeTexture(500001 + i), 0, 0))]);
rate('far crops of one texture', i => {
  const tex = makeTexture(900001 + i);
  return [templateOf(textureFrame(tex, 0, 0)), pyramidOf(textureFrame(tex, 200 + 13 * (i % 7), 150 + 17 * (i % 5)))];
});

/** align.test.ts's striped fence: period P degrees in azimuth up to `top`, synth.ts's treeline (seed 3) above it. */
function fenceScene(periodDeg: number, top: number): SynthScene {
  const base = makeSynthScene({ seed: 3 }), above = makeSynthScene({ seed: 3, skyline: az => top + 1 + 0.8 * base.horizonAlt(az) });
  const c1 = [150, 140, 120], c2 = [60, 52, 40], ramp = 0.2 / periodDeg, clamp01 = (x: number) => Math.max(0, Math.min(1, x));
  return {
    horizonAlt: above.horizonAlt,
    sample(az, alt) {
      if (alt >= top) return above.sample(az, alt);
      const u = (((az % periodDeg) + periodDeg) % periodDeg) / periodDeg;
      const t = u < 0.5 ? clamp01(Math.min(u, 0.5 - u) / ramp + 0.5) : 1 - clamp01(Math.min(u - 0.5, 1 - u) / ramp + 0.5);
      return [c2[0] + (c1[0] - c2[0]) * t, c2[1] + (c1[1] - c2[1]) * t, c2[2] + (c1[2] - c2[2]) * t];
    },
  };
}
for (const top of [5, 15, 25, 90]) for (const period of [3, 4, 6, 8, 10]) {
  const counts = { keyframe: { truth: 0, wrong: 0, refused: 0 }, closure: { truth: 0, wrong: 0, refused: 0 } };
  const sc = fenceScene(period, top);
  for (const az0 of [40, 130, 220, 310]) {
    const pa = look(az0, RING_PITCH), pb = look(az0 + 4, RING_PITCH), t = templateOf(camera(renderView(sc, pa, K0), 100 + az0));
    const b = pyramidOf(camera(renderView(sc, pb, K0), 200 + az0)), real = relative(pa, pb);
    for (const sign of [1, -1]) {
      const pred = relative(pa, look(az0 + 4 + sign * period, RING_PITCH));
      for (const window of windows) {
        const res = alignPair(t, b, pred, K, { window }), c = counts[window];
        if (!res.ok) c.refused++;
        else if (angleBetweenDeg(res.qBA, real) < 0.5) c.truth++;
        else c.wrong++;
      }
    }
  }
  const fmt = (w: AlignWindow) => `${w} ${counts[w].truth} truth / ${counts[w].wrong} wrong period / ${counts[w].refused} refused`;
  console.log(`fence to ${top === 90 ? 'the top of the view' : `${top} degrees`}, period ${period}, one period off (8 runs): ${fmt('keyframe')}; ${fmt('closure')}`);
}
