// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T21: pair alignment for the panorama scanner (SPEC-v2 4.6, the closure window of 4.9, the align row of 7.2).
//
// Mutants this file must catch (SPEC-v2 7.2), each against the case that names it:
//   * phase correlation skipped (the shift's rotation never applied, `q0 = qPred` in alignPair): LK alone cannot reach
//     a 5-degree error on the 3-degree blocks of the registration scene, so `recovers a 5-degree prediction error
//     through phase correlation` fails (every 5-degree prediction is refused for low ZNCC);
//   * TEXTURE_FLOOR raised 10x: `the thin-treeline fixture is ok` fails (refused for texture), and so does the
//     calibration case, whose treeline must lie between 3 and 10 floors;
//   * the gain term removed (the gain fixed at 1 in both fits of `evaluate`): `recovers injected yaw, pitch and roll`
//     fails, because the gain reads 1 instead of 0.7, and the rotation then misses by up to 0.12 degrees.
//
// Frames are the 9:16 analysis frame (180 x 320) at the 70-degree prior (fNorm 1.2694). The synth.ts fixtures carry the
// simulator's frame noise (Gaussian, sd 2 per channel, 13.5 `noise_sigma`): without it a noiseless smooth sky quantises
// into contours that are identical in both views, phase-only correlation whitens them into a confident ridge, and two
// of fifteen noiseless thin treelines took a 3-pixel wrong peak with PSR 17-19. Real frames, and the simulator's, have
// noise that breaks those contours.
//
// Measured (here on 200 pairs per set, and by __bench__/align.bench.ts on 1,000; S4 asks for the rates on both windows):
//   * unrelated value-noise pairs: 0 accepted by either window (bench: 0 of 1,000 each; PSR >= 7 on 0.4 % of keyframe
//     and 1.7 % of closure windows, ZNCC at most 0.39 among those here);
//   * far crops of one texture (200 to 290 px apart at L2): 0 accepted by either window (bench: 0 of 1,000 each; PSR >= 7
//     on 3.6 % and 3.5 %, ZNCC at most 0.50 here);
//   * repeated stripes predicted one period off (bench, 8 runs per row): with a treeline in view and a period of 3 to 6
//     degrees, both windows recover the truth; a fence that fills the frame to 15 or 25 degrees with a period of 8 to 10
//     is accepted at the wrong period by the keyframe window in up to 7 of 8 runs, with ZNCC near 0.97 (so ZNCC does NOT
//     reject a one-period lock on a fence that dominates the view); stripes filling the view are refused by the keyframe
//     window at periods of 6 and more (PSR) and accepted at the wrong period by the closure window at every period.
import assert from 'node:assert/strict';
import { cameraLens, lookBasis, pixelLuminance, skyAngles, unit, type CameraBasis, type V3 } from '../../photosphereGeometry';
import { ACCEPT, alignPair, TEXTURE_FLOOR, type RgbStrip } from '../align';
import { buildPyramid, prepareTemplate, warpLevel } from '../pyramid';
import {
  angleBetweenDeg, basisFromQuat, expSO3, intrinsicsAt, priorFNorm, qinv, qmul, quatFromBasis, rotationHomography, shortFovDeg,
} from '../rotation';
import type { AlignResult, Pyramid, Quat, Template } from '../types';
import { makeSynthScene, renderView, type SynthScene } from './synth/synth';

// A failing case is reported and the file carries on, so a mutant shows every case it turns red, not only the first
// (the pattern of api/__tests__/cloudmapDomeCache.test.ts); the exported `failed` and the exit code carry the failure.
let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

const W = 180, H = 320, FNORM = priorFNorm(W, H), DEG = Math.PI / 180;
const K0 = intrinsicsAt(0, W, H, FNORM), K1 = intrinsicsAt(1, W, H, FNORM), K2 = intrinsicsAt(2, W, H, FNORM);
const K = { l0: K0, l1: K1, l2: K2 };
/** synth DEFAULT_PAN's pitch: the ring 1 target of the default lens. */
const RING_PITCH = 22.98;

// ---- The registration scene, copied from photosphereRegistration.test.ts:10 (RI m18) ----------------------------------
// The generator is unchanged: 3-degree blocks of hashed luma over azimuth and altitude, channels v, 0.8 v and 0.6 v
// times the exposure. Only the frame differs: the 9:16 analysis frame at the prior instead of 180 x 240 at 41.1
// degrees, so it renders exactly the camera intrinsicsAt(0, 180, 320, fNorm) describes.
const width = W, height = H, lens = cameraLens(width, height, shortFovDeg(FNORM));
function scene(basis: CameraBasis, exposure = 1): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const xx = ((x + .5) / width * 2 - 1) * lens.tanX, yy = (1 - (y + .5) / height * 2) * lens.tanY;
    const ray = unit(basis.forward.map((v, i) => v + basis.right[i] * xx + basis.up[i] * yy) as V3), p = skyAngles(ray);
    const a = Math.floor(p.az / 3), b = Math.floor((p.alt + 90) / 3), v = 30 + ((a * 73856093 ^ b * 19349663) >>> 0) % 190;
    data.set([v * exposure, v * .8 * exposure, v * .6 * exposure, 255], (y * width + x) * 4);
  }
  return data;
}

// ---- Helpers -----------------------------------------------------------------------------------------------------------

const look = (az: number, alt: number): Quat => quatFromBasis(lookBasis(az, alt));
/** qBA = qinv(P_b) . P_a: camera-a vectors to camera-b vectors (S1). */
const relative = (qa: Quat, qb: Quat): Quat => qmul(qinv(qb), qa);
/** The prediction off by (pitch, yaw, roll) degrees about b's camera axes. */
const offBy = (q: Quat, pitch: number, yaw: number, roll: number): Quat => qmul(expSO3([pitch * DEG, yaw * DEG, roll * DEG]), q);
const pyramidOf = (rgba: Uint8ClampedArray): Pyramid => buildPyramid(rgba, W, H).pyr;
const templateOf = (rgba: Uint8ClampedArray): Template => prepareTemplate(pyramidOf(rgba), K1, K2);
const mod = (x: number, m: number) => ((x % m) + m) % m;
const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

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
const gauss = (r: () => number) => Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());
/** The frame times a per-channel gain, plus Gaussian noise of sd `sigma` per channel, clamped and rounded as a camera
 *  frame is. */
function camera(rgba: Uint8ClampedArray, sigma: number, seed: number, gain: readonly [number, number, number] = [1, 1, 1]): Uint8ClampedArray {
  const r = rng(seed), out = new Uint8ClampedArray(rgba.length);
  for (let i = 0; i < rgba.length; i += 4) {
    for (let c = 0; c < 3; c++) out[i + c] = rgba[i + c] * gain[c] + sigma * gauss(r);
    out[i + 3] = 255;
  }
  return out;
}

const FRAME_NOISE = 2;
interface Pair { tpl: Template; img: Pyramid; truth: Quat; rgbA: Uint8ClampedArray; rgbB: Uint8ClampedArray }
/** Template keyframe at (az0, pitch), candidate at (az0 + dAz, pitch), both under the simulator's frame noise. */
function synthPair(sc: SynthScene, az0: number, dAz: number, gainB: readonly [number, number, number] = [1, 1, 1], pitch = RING_PITCH): Pair {
  const qa = look(az0, pitch), qb = look(az0 + dAz, pitch);
  const rgbA = camera(renderView(sc, qa, K0), FRAME_NOISE, 100 + az0), rgbB = camera(renderView(sc, qb, K0), FRAME_NOISE, 200 + az0, gainB);
  return { tpl: templateOf(rgbA), img: pyramidOf(rgbB), truth: relative(qa, qb), rgbA, rgbB };
}

/** gain^2 trace(H) / N over the template's L1 pixels that land inside b through q: the texture test of 4.6 step 4,
 *  computed here from Template jac and warpLevel alone, to read the fixtures' margins against TEXTURE_FLOOR. */
function textureOf(tpl: Template, img: Pyramid, q: Quat, gain: number): number {
  const out = new Float32Array(K1.w * K1.h), jac = tpl.l1.jac;
  warpLevel(img.l1, rotationHomography(K1, K1, q), out, K1.w, K1.h);
  let sum = 0, n = 0;
  for (let i = 0; i < out.length; i++) if (!Number.isNaN(out[i])) { n++; sum += jac[3 * i] ** 2 + jac[3 * i + 1] ** 2 + jac[3 * i + 2] ** 2; }
  return gain * gain * sum / n;
}

const refusedAsPredicted = (res: AlignResult, qPred: Quat, what: string) => {
  assert.equal(res.ok, false, `${what}: accepted`);
  assert.deepEqual(res.qBA, qPred, `${what}: a refused pair returns the prediction`);
  assert.equal(res.corr.length, 0, `${what}: a refused pair has no correspondences`);
};

// ---- Recovery on the registration scene ------------------------------------------------------------------------------

const REG_A = look(20, RING_PITCH), REG_B = look(24, RING_PITCH), REG_TRUTH = relative(REG_A, REG_B);
const REG_TPL = templateOf(scene(basisFromQuat(REG_A)));
const REG_IMG = pyramidOf(scene(basisFromQuat(REG_B), 0.7));

test('after the prewarp, recovers injected yaw, pitch and roll up to 4 degrees within 0.05 with gain 0.7', () => {
  // A 4-degree ring step at pitch 22.98 (its in-image roll of 1.56 degrees is the prewarp's), predicted with every
  // combination of -4, 0 and +4 degrees on each axis: 27 predictions up to 6.9 degrees off.
  let worst = 0;
  for (const p of [-4, 0, 4]) for (const y of [-4, 0, 4]) for (const r of [-4, 0, 4]) {
    const res = alignPair(REG_TPL, REG_IMG, offBy(REG_TRUTH, p, y, r), K, { window: 'keyframe' });
    const err = angleBetweenDeg(res.qBA, REG_TRUTH), what = `prediction off by (${p}, ${y}, ${r})`;
    assert.ok(res.ok, `${what}: refused (${res.reason})`);
    assert.ok(err < 0.05, `${what}: ${err} degrees from the truth`);
    assert.ok(Math.abs(res.gain - 0.7) < 0.03, `${what}: gain ${res.gain}, the image is the template at 0.7`);
    assert.ok(Math.abs(res.bias) < 3, `${what}: bias ${res.bias}`);
    assert.ok(res.textured && res.psr >= ACCEPT.psrMin && res.zncc >= ACCEPT.znccMin && res.overlap >= ACCEPT.overlapMin && res.inlierFrac >= ACCEPT.inlierMin);
    assert.ok(res.iterations >= 1 && res.iterations <= 6, `${what}: ${res.iterations} iterations, at most 4 + 2`);
    worst = Math.max(worst, err);
  }
  console.log(`  worst ${worst.toFixed(4)} degrees over 27 predictions`);
});

test('recovers a 5-degree prediction error through phase correlation', () => {
  for (const [p, y] of [[0, 5], [0, -5], [5, 0], [-5, 0], [3, 4]]) {
    const res = alignPair(REG_TPL, REG_IMG, offBy(REG_TRUTH, p, y, 0), K, { window: 'keyframe' });
    const err = angleBetweenDeg(res.qBA, REG_TRUTH), what = `prediction off by (${p}, ${y}, 0)`;
    assert.ok(res.ok, `${what}: refused (${res.reason}, PSR ${res.psr}, ZNCC ${res.zncc})`);
    assert.ok(err < 0.05, `${what}: ${err} degrees from the truth`);
    assert.ok(res.psr >= ACCEPT.psrMin);
  }
});

// ---- Texture: the blank frames, the thin treeline and the floor between them ------------------------------------------

const GREY: readonly [number, number, number] = [118, 122, 128];
/** A blank overcast frame: no skyline in view, a smooth grey sky darkening toward the zenith, no texture. */
const OVERCAST = makeSynthScene({ seed: 5, skyline: () => -60, textureContrast: 0, sky: GREY });
/** An exactly flat frame: the whole ring is one blank sector. */
const FLAT = makeSynthScene({ seed: 5, blankSectors: [[0, 359.99]], sky: GREY });
/** A thin treeline under smooth sky: synth.ts's default skyline (seed 3) squeezed into 1 to 6 degrees, flat and
 *  featureless below its jagged top, the default sky above. At the ring pitch it fills the bottom quarter of the frame
 *  and its top is the only texture there is. */
const TREELINE = (() => {
  const base = makeSynthScene({ seed: 3 }).horizonAlt;
  return makeSynthScene({ seed: 3, skyline: az => 1 + (base(az) - 0.35) * 5 / 9.3, textureContrast: 0 });
})();
const OVERCAST_PAIR = synthPair(OVERCAST, 40, 4), TREELINE_PAIR = synthPair(TREELINE, 40, 4);

test('a blank image gives ok: false, reason texture, textured: false', () => {
  const qa = look(40, RING_PITCH), qb = look(44, RING_PITCH), truth = relative(qa, qb);
  const flatA = renderView(FLAT, qa, K0), flatB = renderView(FLAT, qb, K0);
  const textured = synthPair(makeSynthScene({ seed: 2 }), 40, 4);
  const cases: [string, Template, Pyramid, Quat][] = [
    ['exactly flat frames', templateOf(flatA), pyramidOf(flatB), truth],
    ['flat frames under noise', templateOf(camera(flatA, FRAME_NOISE, 1)), pyramidOf(camera(flatB, FRAME_NOISE, 2)), truth],
    ['the overcast pair', OVERCAST_PAIR.tpl, OVERCAST_PAIR.img, OVERCAST_PAIR.truth],
    ['a textured template against a blank image', textured.tpl, pyramidOf(camera(flatB, FRAME_NOISE, 3)), textured.truth],
  ];
  for (const [what, tpl, img, qPred] of cases) {
    const res = alignPair(tpl, img, qPred, K, { window: 'keyframe' });
    refusedAsPredicted(res, qPred, what);
    assert.equal(res.reason, 'texture', `${what}: reason ${res.reason}`);
    assert.equal(res.textured, false, `${what}: textured`);
  }
  const flat = alignPair(templateOf(flatA), pyramidOf(flatB), truth, K, { window: 'keyframe' });
  assert.deepEqual([flat.covRad2[0], flat.covRad2[4], flat.covRad2[8]], [Infinity, Infinity, Infinity], 'no texture, no information');
  assert.equal(flat.gain, 0);
});

test('TEXTURE_FLOOR lies between the overcast frame and the thin treeline, within 10x of the treeline', () => {
  const over = alignPair(OVERCAST_PAIR.tpl, OVERCAST_PAIR.img, OVERCAST_PAIR.truth, K, { window: 'keyframe' });
  const tree = alignPair(TREELINE_PAIR.tpl, TREELINE_PAIR.img, TREELINE_PAIR.truth, K, { window: 'keyframe' });
  const overcast = textureOf(OVERCAST_PAIR.tpl, OVERCAST_PAIR.img, OVERCAST_PAIR.truth, over.gain);
  const treeline = textureOf(TREELINE_PAIR.tpl, TREELINE_PAIR.img, tree.qBA, tree.gain);
  console.log(`  texture: overcast ${overcast.toFixed(0)}, thin treeline ${treeline.toFixed(0)}, floor ${TEXTURE_FLOOR}`);
  assert.ok(overcast < TEXTURE_FLOOR / 3, `overcast ${overcast} is not clear of the floor ${TEXTURE_FLOOR}`);
  assert.ok(treeline > 3 * TEXTURE_FLOOR, `treeline ${treeline} is not clear of the floor ${TEXTURE_FLOOR}`);
  assert.ok(treeline < 10 * TEXTURE_FLOOR, `treeline ${treeline}: a floor raised 10x would still pass it`);
});

test('the thin-treeline fixture is ok, with yaw covariance at least 10x its pitch covariance', () => {
  const { tpl, img, truth } = TREELINE_PAIR;
  const res = alignPair(tpl, img, truth, K, { window: 'keyframe' });
  assert.ok(res.ok, `refused (${res.reason}), textured ${res.textured}`);
  assert.ok(res.textured);
  assert.ok(angleBetweenDeg(res.qBA, truth) < 0.05, `${angleBetweenDeg(res.qBA, truth)} degrees from the truth`);
  // covRad2 is in the template camera's axes, as the Jacobian is: x (pitch), y (yaw), z (roll). A level treeline pins
  // pitch along its whole length; yaw rests on its jagged top alone.
  const c = res.covRad2, ratio = c[4] / c[0];
  console.log(`  thin treeline: sd pitch ${(Math.sqrt(c[0]) / DEG).toFixed(4)}, yaw ${(Math.sqrt(c[4]) / DEG).toFixed(4)}, roll ${(Math.sqrt(c[8]) / DEG).toFixed(4)} degrees; yaw / pitch variance ${ratio.toFixed(1)}`);
  assert.ok(ratio >= 10, `yaw / pitch covariance ${ratio}`);
  // The same treeline with the prediction 3 degrees off in yaw is recovered as well.
  const off = alignPair(tpl, img, offBy(truth, 0, 3, 0), K, { window: 'keyframe' });
  assert.ok(off.ok && angleBetweenDeg(off.qBA, truth) < 0.05, `3 degrees off: ${off.reason}, ${angleBetweenDeg(off.qBA, truth)}`);
});

// ---- Repeated stripes ------------------------------------------------------------------------------------------------

/** A striped fence periodic in azimuth (period P degrees, duty 0.5, the 13.4 stripes colours, each edge a 0.2-degree
 *  ramp) from the ground up to `top` degrees, under synth.ts's scene (seed 3) with its treeline lifted to start a
 *  degree above the fence. A `top` above the view fills it with stripes. */
function fenceScene(periodDeg: number, top: number): SynthScene {
  const base = makeSynthScene({ seed: 3 }), above = makeSynthScene({ seed: 3, skyline: az => top + 1 + 0.8 * base.horizonAlt(az) });
  const c1 = [150, 140, 120], c2 = [60, 52, 40], ramp = 0.2 / periodDeg;
  return {
    horizonAlt: above.horizonAlt,
    sample(az, alt) {
      if (alt >= top) return above.sample(az, alt);
      const u = mod(az, periodDeg) / periodDeg;
      const t = u < 0.5 ? clamp01(Math.min(u, 0.5 - u) / ramp + 0.5) : 1 - clamp01(Math.min(u - 0.5, 1 - u) / ramp + 0.5);
      return [c2[0] + (c1[0] - c2[0]) * t, c2[1] + (c1[1] - c2[1]) * t, c2[2] + (c1[2] - c2[2]) * t];
    },
  };
}
/** The fence pair seen 4 degrees apart, predicted one period off either way. */
function onePeriodOff(periodDeg: number, top: number, az0: number): { res: AlignResult; truth: Quat; qPred: Quat }[] {
  const sc = fenceScene(periodDeg, top), { tpl, img, truth } = synthPair(sc, az0, 4), qa = look(az0, RING_PITCH);
  return [1, -1].map(sign => {
    const qPred = relative(qa, look(az0 + 4 + sign * periodDeg, RING_PITCH));
    return { res: alignPair(tpl, img, qPred, K, { window: 'keyframe' }), truth, qPred };
  });
}

test('repeated stripes with a one-period bad prediction fail PSR or ZNCC', () => {
  // Stripes filling the view, 6 to 10 degrees apart (6 to 10 px at L2): every period is a peak of the correlation
  // surface, the neighbours lie outside PSR's 11 x 11 box, and the comb holds PSR under 7. Under 6 px the neighbours fall
  // inside the box and PSR cannot see the comb; a view of nothing but stripes is then ambiguous to any test. Over four
  // azimuths the bench refuses 23 of these 24 runs; the one it accepts (period 8) is the wrong period, which in a view
  // of nothing but stripes no pair test can tell from the truth. The closure window accepts the wrong period on every
  // such run (bench), so this holds for the keyframe window only.
  for (const period of [6, 8, 10]) for (const az0 of [40, 130]) for (const { res, qPred } of onePeriodOff(period, 90, az0)) {
    const what = `period ${period} at azimuth ${az0}`;
    refusedAsPredicted(res, qPred, what);
    assert.ok(res.reason === 'no-peak' || res.reason === 'low-zncc', `${what}: refused for ${res.reason}`);
  }
});

test('a one-period error within the predictor budget, under a treeline, is absorbed: the truth, never the wrong period', () => {
  // The backyard's fence: from the ground to 5 degrees, the treeline above it. A period of 3 degrees is inside the
  // 3.83-degree worst predictor residual of 4.6 (relative mode), so a gyro error of one whole period can happen. The
  // treeline breaks the tie and the phase correlation takes the true peak.
  for (const az0 of [40, 130]) for (const { res, truth, qPred } of onePeriodOff(3, 5, az0)) {
    assert.ok(res.ok, `azimuth ${az0}: refused (${res.reason})`);
    assert.ok(angleBetweenDeg(res.qBA, truth) < 0.05, `azimuth ${az0}: ${angleBetweenDeg(res.qBA, truth)} from the truth`);
    assert.ok(angleBetweenDeg(res.qBA, qPred) > 2.9, 'not the one-period lock');
  }
  // Measured, not asserted (S4 asks for the rate; see T21's report): a fence filling the frame up to 25 degrees with a
  // period of 6 to 10 degrees, predicted one period off, is accepted at the wrong period with ZNCC near 0.97. The
  // treeline is too small a share of the overlap for ZNCC or the inlier test to see, and the innovation gate cannot
  // either, because the image then agrees with the wrong prediction. Such a gyro error is beyond 4.6's budget.
  let locks = 0, runs = 0, minZncc = 1;
  for (const period of [6, 8, 10]) for (const { res, qPred } of onePeriodOff(period, 25, 40)) {
    runs++;
    if (res.ok && angleBetweenDeg(res.qBA, qPred) < 0.5) { locks++; minZncc = Math.min(minZncc, res.zncc); }
  }
  console.log(`  measured: a fence up to 25 degrees, period 6-10, one period off: ${locks} of ${runs} accepted at the wrong period (ZNCC >= ${minZncc.toFixed(3)})`);
});

// ---- Overlap means and gains from the RGB strips -----------------------------------------------------------------------

/** The kept strip of a keyframe: the L0 columns x0 .. x0 + w - 1, RGB, full height. */
function stripOf(rgba: Uint8ClampedArray, x0: number, w: number): RgbStrip {
  const strip = new Uint8Array(w * H * 3);
  for (let y = 0; y < H; y++) for (let x = 0; x < w; x++) for (let c = 0; c < 3; c++) strip[(y * w + x) * 3 + c] = rgba[(y * W + x0 + x) * 4 + c];
  return { strip, x0, w, h: H };
}
/** The definition of 4.6 step 5 written out: each pixel of a's strip carried through q to the b pixel containing it,
 *  counted when that pixel is in b's strip and both lumas are 16..240. */
function stripReference(a: RgbStrip, b: RgbStrip, q: Quat): { n: number; meanA: number[]; meanB: number[] } {
  const M = rotationHomography(K0, K0, q), sa = [0, 0, 0], sb = [0, 0, 0];
  let n = 0;
  for (let y = 0; y < a.h; y++) for (let x = 0; x < a.w; x++) {
    const u = a.x0 + x + 0.5, v = y + 0.5, d = M[6] * u + M[7] * v + M[8];
    const bx = Math.floor((M[0] * u + M[1] * v + M[2]) / d) - b.x0, by = Math.floor((M[3] * u + M[4] * v + M[5]) / d);
    if (bx < 0 || bx >= b.w || by < 0 || by >= b.h) continue;
    const ia = (y * a.w + x) * 3, ib = (by * b.w + bx) * 3;
    const la = Math.round(pixelLuminance(a.strip[ia], a.strip[ia + 1], a.strip[ia + 2]));
    const lb = Math.round(pixelLuminance(b.strip[ib], b.strip[ib + 1], b.strip[ib + 2]));
    if (la < 16 || la > 240 || lb < 16 || lb > 240) continue;
    n++;
    for (let c = 0; c < 3; c++) { sa[c] += a.strip[ia + c]; sb[c] += b.strip[ib + c]; }
  }
  return { n, meanA: sa.map(s => s / n), meanB: sb.map(s => s / n) };
}

test('meanA, meanB and n come from the RGB strips; gainRGB is b over a per channel', () => {
  // The candidate's frame is the scene through per-channel gains 0.9, 0.8 and 0.7. Strips are +-40 columns (about
  // +-10 degrees) of each frame, as the tracker keeps them.
  const gains = [0.9, 0.8, 0.7] as const, pair = synthPair(makeSynthScene({ seed: 2 }), 100, 4, gains);
  const rgbA = stripOf(pair.rgbA, 50, 80), rgbB = stripOf(pair.rgbB, 50, 80);
  // A clipped band in b's strip: luma above 240, so those pixels must not count.
  for (let y = 100; y < 140; y++) for (let x = 0; x < 80; x++) for (let c = 0; c < 3; c++) rgbB.strip[(y * 80 + x) * 3 + c] = 255;
  const res = alignPair(pair.tpl, pair.img, pair.truth, K, { window: 'keyframe', rgbA, rgbB });
  assert.ok(res.ok, `refused (${res.reason})`);
  const want = stripReference(rgbA, rgbB, res.qBA);
  assert.equal(res.n, want.n);
  assert.ok(want.n > 10000, `overlap of ${want.n} pixels`);
  for (let c = 0; c < 3; c++) {
    assert.ok(Math.abs(res.meanA[c] - want.meanA[c]) < 1e-9, `meanA[${c}] ${res.meanA[c]} vs ${want.meanA[c]}`);
    assert.ok(Math.abs(res.meanB[c] - want.meanB[c]) < 1e-9, `meanB[${c}] ${res.meanB[c]} vs ${want.meanB[c]}`);
    assert.ok(Math.abs(res.meanB[c] / res.meanA[c] - gains[c]) < 0.02, `channel ${c}: meanB / meanA ${res.meanB[c] / res.meanA[c]}`);
    assert.ok(Math.abs(res.gainRGB[c] - gains[c]) < 0.02, `gainRGB[${c}] ${res.gainRGB[c]}`);
  }
  // The clipped band is excluded: without it the count is larger.
  const unclipped = stripOf(pair.rgbB, 50, 80);
  assert.ok(stripReference(rgbA, unclipped, res.qBA).n > want.n + 2000, 'the band removed pixels from n');
  // From the strips, not the pyramids: a constant strip for a gives exactly its colour as meanA.
  const flatA: RgbStrip = { ...rgbA, strip: new Uint8Array(rgbA.strip.length).fill(0) };
  for (let i = 0; i < flatA.strip.length; i += 3) { flatA.strip[i] = 100; flatA.strip[i + 1] = 120; flatA.strip[i + 2] = 140; }
  const fromFlat = alignPair(pair.tpl, pair.img, pair.truth, K, { window: 'keyframe', rgbA: flatA, rgbB });
  assert.deepEqual(fromFlat.meanA, [100, 120, 140]);
  assert.equal(fromFlat.n, stripReference(flatA, rgbB, fromFlat.qBA).n);
  // Without strips: zeros, and n = 0.
  const none = alignPair(pair.tpl, pair.img, pair.truth, K, { window: 'keyframe' });
  assert.deepEqual([none.gainRGB, none.meanA, none.meanB, none.n], [[0, 0, 0], [0, 0, 0], [0, 0, 0], 0]);
  assert.ok(none.ok);
});

// ---- The closure window ----------------------------------------------------------------------------------------------

test('the closure window recovers 12 degrees', () => {
  // An early and a late keyframe 6 degrees apart on a textured ring (synth.ts, seed 1), the chain's prediction off by a
  // closure residual. The window holds the full L2 frames padded with NaN to 64 x 128 (reliable to 16 px, 4.9), and the
  // closure accepts a residual of at most 12 degrees, so 11.9 is the largest a test can ask for without standing on the
  // boundary: the measured residual carries LK's own error.
  const sc = makeSynthScene({ seed: 1 });
  for (const az0 of [200, 300]) {
    const { tpl, img, truth } = synthPair(sc, az0, -6);
    for (const [p, y] of [[0, 11.9], [0, -11.9], [3, 11.5], [-3, -11.5]]) {
      const res = alignPair(tpl, img, offBy(truth, p, y, 0), K, { window: 'closure' });
      const what = `azimuth ${az0}, residual (${p}, ${y})`;
      assert.ok(res.ok, `${what}: refused (${res.reason}, PSR ${res.psr}, ZNCC ${res.zncc})`);
      assert.ok(angleBetweenDeg(res.qBA, truth) < 0.05, `${what}: ${angleBetweenDeg(res.qBA, truth)} from the truth`);
      assert.ok(res.zncc >= ACCEPT.closureZnccMin && res.psr >= ACCEPT.psrMin);
    }
    // Past the closure's 12 degrees the match is refused, even though the shift is inside the window's reach.
    const qFar = offBy(truth, 0, 13, 0), far = alignPair(tpl, img, qFar, K, { window: 'closure' });
    refusedAsPredicted(far, qFar, `azimuth ${az0}, residual 13`);
    assert.equal(far.reason, 'diverged');
  }
});

// ---- False accepts ----------------------------------------------------------------------------------------------------

// The value-noise texture of phaseCorrelate.test.ts (three octaves at 12, 6 and 3 px, each on its own rotated lattice),
// sampled here at L0 in L2 pixel units, so the pyramid's L2 sees the same spacings the phase-correlation tests used.
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
/** A grey L0 frame of a texture, offset by (ox, oy) L2 pixels. */
function textureFrame(tex: (x: number, y: number) => number, ox: number, oy: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const v = tex(ox + (x + 0.5) / 4, oy + (y + 0.5) / 4), o = (y * W + x) * 4;
    out[o] = v; out[o + 1] = v; out[o + 2] = v; out[o + 3] = 255;
  }
  return out;
}
/** The pairs as an ordinary ring step would be predicted: 4 degrees of yaw at the ring pitch. */
const STEP_PRED = relative(look(40, RING_PITCH), look(44, RING_PITCH));
function acceptance(pairs: [Template, Pyramid][]): Record<'keyframe' | 'closure', { ok: number; psr7: number; maxZncc: number }> {
  const out = { keyframe: { ok: 0, psr7: 0, maxZncc: -1 }, closure: { ok: 0, psr7: 0, maxZncc: -1 } };
  for (const [tpl, img] of pairs) for (const window of ['keyframe', 'closure'] as const) {
    const res = alignPair(tpl, img, STEP_PRED, K, { window }), o = out[window];
    if (res.ok) o.ok++;
    if (res.psr >= ACCEPT.psrMin) { o.psr7++; o.maxZncc = Math.max(o.maxZncc, res.zncc); }
  }
  return out;
}
const report = (what: string, n: number, r: ReturnType<typeof acceptance>) => {
  for (const window of ['keyframe', 'closure'] as const) {
    const o = r[window];
    const among = o.psr7 > 0 ? `, ZNCC at most ${o.maxZncc.toFixed(3)} among those` : '';
    console.log(`  ${what}, ${window} window: accepted ${o.ok} of ${n} (${(100 * o.ok / n).toFixed(1)} %); PSR >= 7 on ${o.psr7}${among}`);
  }
};

test('on 200 unrelated value-noise pairs alignPair accepts at most 1 (0.5 %)', () => {
  const pairs: [Template, Pyramid][] = [];
  for (let i = 0; i < 200; i++) pairs.push([templateOf(textureFrame(makeTexture(1 + i), 0, 0)), pyramidOf(textureFrame(makeTexture(5001 + i), 0, 0))]);
  const r = acceptance(pairs);
  report('unrelated pairs', 200, r);
  assert.ok(r.keyframe.ok <= 1, `keyframe window accepted ${r.keyframe.ok}`);
  assert.ok(r.closure.ok <= 1, `closure window accepted ${r.closure.ok}`);
});

test('far crops of one repeated texture: the acceptance rate is measured for both windows (S4)', () => {
  // Two crops of ONE texture, 200 to 290 px apart at L2: unrelated content with a shared lattice orientation and
  // scale, which is what lifted PSR to 7 in a third of T07's crops. 7.2 asks only for the rate; it is held here to the
  // unrelated pairs' 0.5 %, which it meets, so a change that lets repeated texture through is seen.
  const pairs: [Template, Pyramid][] = [];
  for (let i = 0; i < 200; i++) {
    const tex = makeTexture(9001 + i);
    pairs.push([templateOf(textureFrame(tex, 0, 0)), pyramidOf(textureFrame(tex, 200 + 13 * (i % 7), 150 + 17 * (i % 5)))]);
  }
  const r = acceptance(pairs);
  report('far crops of one texture', 200, r);
  assert.ok(r.keyframe.ok <= 1, `keyframe window accepted ${r.keyframe.ok}`);
  assert.ok(r.closure.ok <= 1, `closure window accepted ${r.closure.ok}`);
});

// ---- Correspondences, covariance and the contract ----------------------------------------------------------------------

test('correspondences: up to 16 on a 4 x 4 grid of the overlap, carried through qBA, near the truth, empty when refused', () => {
  const res = alignPair(REG_TPL, REG_IMG, offBy(REG_TRUTH, 1, -2, 1), K, { window: 'keyframe' });
  assert.ok(res.ok);
  assert.ok(res.corr.length >= 12 && res.corr.length <= 16, `${res.corr.length} correspondences`);
  const M = rotationHomography(K0, K0, res.qBA), T = rotationHomography(K0, K0, REG_TRUTH);
  const map = (h: Float64Array, u: number, v: number) => { const d = h[6] * u + h[7] * v + h[8]; return [(h[0] * u + h[1] * v + h[2]) / d, (h[3] * u + h[4] * v + h[5]) / d]; };
  for (const c of res.corr) {
    assert.ok(c.ua >= 0 && c.ua <= W && c.va >= 0 && c.va <= H && c.ub >= 0 && c.ub <= W && c.vb >= 0 && c.vb <= H, `inside both frames: ${JSON.stringify(c)}`);
    const [ub, vb] = map(M, c.ua, c.va);
    assert.ok(Math.abs(ub - c.ub) < 1e-6 && Math.abs(vb - c.vb) < 1e-6, 'carried through the final rotation');
    const [tu, tv] = map(T, c.ua, c.va);
    assert.ok(Math.hypot(tu - c.ub, tv - c.vb) < 0.3, `${Math.hypot(tu - c.ub, tv - c.vb)} px from the truth`);
  }
  // Spread over the overlap, one per grid cell: the points span most of the frame both ways.
  const us = res.corr.map(c => c.ua), vs = res.corr.map(c => c.va);
  assert.ok(Math.max(...us) - Math.min(...us) > W / 2 && Math.max(...vs) - Math.min(...vs) > H / 2, 'spread over the overlap');
  // A view of another part of the sky is refused, and a refused pair carries none.
  const elsewhere = alignPair(REG_TPL, pyramidOf(scene(basisFromQuat(look(200, -10)))), REG_TRUTH, K, { window: 'keyframe' });
  refusedAsPredicted(elsewhere, REG_TRUTH, 'an unrelated view');
});

test('covariance: symmetric and positive definite on texture, balanced on an all-round texture', () => {
  const res = alignPair(REG_TPL, REG_IMG, REG_TRUTH, K, { window: 'keyframe' }), c = res.covRad2;
  assert.ok(c instanceof Float64Array && c.length === 9);
  for (const [i, j] of [[1, 3], [2, 6], [5, 7]]) assert.equal(c[i], c[j]);
  const d1 = c[0] * c[4] - c[1] * c[3], d2 = c[0] * (c[4] * c[8] - c[5] * c[7]) - c[1] * (c[3] * c[8] - c[5] * c[6]) + c[2] * (c[3] * c[7] - c[4] * c[6]);
  assert.ok(c[0] > 0 && d1 > 0 && d2 > 0, 'positive definite');
  // 3-degree blocks everywhere hold pitch and yaw alike; sd of a few thousandths of a degree.
  assert.ok(c[4] / c[0] > 0.3 && c[4] / c[0] < 3, `yaw / pitch ${c[4] / c[0]}`);
  assert.ok(Math.sqrt(c[4]) / DEG < 0.01, `yaw sd ${Math.sqrt(c[4]) / DEG} degrees`);
});

test('the 3:4 analysis frame (240 x 320) aligns in both windows', () => {
  // 60 x 80 at L2: the keyframe window fits inside it and the closure window pads it with NaN on both sides.
  const f34 = priorFNorm(240, 320), k34 = { l0: intrinsicsAt(0, 240, 320, f34), l1: intrinsicsAt(1, 240, 320, f34), l2: intrinsicsAt(2, 240, 320, f34) };
  const sc = makeSynthScene({ seed: 2 }), qa = look(70, RING_PITCH), qb = look(74, RING_PITCH), truth = relative(qa, qb);
  const frame = (q: Quat, seed: number) => buildPyramid(camera(renderView(sc, q, k34.l0), FRAME_NOISE, seed), 240, 320).pyr;
  const tpl = prepareTemplate(frame(qa, 1), k34.l1, k34.l2), img = frame(qb, 2);
  for (const [window, p, y, r] of [['keyframe', 2, -3, 1], ['closure', 0, 11.9, 0]] as const) {
    const res = alignPair(tpl, img, offBy(truth, p, y, r), k34, { window });
    assert.ok(res.ok, `${window}: refused (${res.reason})`);
    assert.ok(angleBetweenDeg(res.qBA, truth) < 0.05, `${window}: ${angleBetweenDeg(res.qBA, truth)} from the truth`);
  }
});

test('intrinsics that do not match the levels are refused', () => {
  assert.throws(() => alignPair(REG_TPL, REG_IMG, REG_TRUTH, { l0: K0, l1: K2, l2: K1 }, { window: 'keyframe' }), RangeError);
  const k34 = { l0: intrinsicsAt(0, 240, 320, FNORM), l1: intrinsicsAt(1, 240, 320, FNORM), l2: intrinsicsAt(2, 240, 320, FNORM) };
  assert.throws(() => alignPair(REG_TPL, REG_IMG, REG_TRUTH, k34, { window: 'closure' }), RangeError);
});

console.log(`align.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exitCode = 1;
export const result = { passed, failed, total: passed + failed };
