// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T17: the synthetic scene and pan generator (SPEC-v2 7.2, synth row).
//
// Mutant this file must catch: the gyro scale error ignored. The relative stream's integrated-yaw cases and the
// gyro-integral case fail on it (a ring turned 385 degrees reads 385, not 392.7). Mutants run on top of that
// one, each against the case that names it:
//   * only the relative stream ignoring the scale: the relative yaw cases fail, the gyro integral still passes;
//   * only the motion stream ignoring the scale: the gyro integral fails, the relative yaw cases still pass;
//   * the Chromium duplicate dropped from `absolute-only`: the shape case fails;
//   * the motion slots permuted (beta and gamma swapped): the W3C slots case fails;
//   * the camera's y axis flipped in `renderView`: the projected-pixel cases fail;
//   * the motion bias at its old 0.05 deg/s: the exact-zero hold case fails (S2);
//   * `horizonAlt` reading the skyline at the raw azimuth: the horizonAlt wrap case fails (S25);
//   * no reading age on the pump (every `age` 0): the relative stream delivers all 900 ticks of the 15 s window,
//     over the 885 bound of the silent-in-a-hold case (S25);
//   * a forward difference in the motion stream (the window starts at the sample, not half a period before it): the
//     ramp-time motion case fails (S25);
//   * the default noise set to 0 (`DEFAULT_NOISE_SD`): the default-noise cases fail, a default frame of a flat sky has
//     a luma sd of 0 and not 2 (S37);
//   * one noise draw per channel (three draws, not one): the same-offset-on-all-channels case and the luma sd fail;
//   * the noise seed ignored (every frame the same pattern), or the pan's frame number left out of it: the
//     different-patterns cases fail (S37);
//   * the pan not passing its `noise` option on to renderView, or renderView reading the default and not the option:
//     the pan's noise 6 and noise 0 cases, and the exact-pixel renderView cases that ask for noise 0, fail (S37).
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { decodePng } from '../../__sim__/png';
import { replayCase } from '../../__sim__/replay';
import { DEG, dot, lookBasis, pixelLuminance, skyVector } from '../../photosphereGeometry';
import {
  angleBetweenDeg, elevationDeg, expSO3, headingDeg, projectCamera, qinv, qmul, qrotate, quatFromBasis,
  quatFromDeviceOrientation, rollDeg,
} from '../rotation';
import type { Intrinsics, Quat } from '../types';
import { DEFAULT_PAN, makeSynthScene, renderView, synthPan, writeReplayInput, type SynthPan, type SynthScene } from './synth/synth';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} != ${b} (tol ${tol})`);
const wrap180 = (d: number) => ((d % 360) + 540) % 360 - 180;
const unwrap = (values: number[]) => {
  const out = [values[0]];
  for (let i = 1; i < values.length; i++) out.push(out[i - 1] + wrap180(values[i] - values[i - 1]));
  return out;
};
/** Ordinary least squares slope of y on x. */
function slope(x: number[], y: number[]): number {
  const n = x.length, mx = x.reduce((a, b) => a + b, 0) / n, my = y.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0;
  for (let i = 0; i < n; i++) { sxy += (x[i] - mx) * (y[i] - my); sxx += (x[i] - mx) ** 2; }
  return sxy / sxx;
}
const lookAt = (az: number, alt: number): Quat => quatFromBasis(lookBasis(az, alt));

const scene = makeSynthScene({ seed: 3 });
const pitchP = DEFAULT_PAN.pitchDeg;

// ---- makeSynthScene --------------------------------------------------------

await test('makeSynthScene: the same seed gives the same samples and another seed gives others', () => {
  const a = makeSynthScene({ seed: 5 }), b = makeSynthScene({ seed: 5 }), c = makeSynthScene({ seed: 6 });
  // The sky is smooth and the same for every seed; the ground and the skyline are what the seed makes.
  let differs = 0;
  for (let i = 0; i < 300; i++) {
    const az = (i * 37.7) % 360, alt = -30 + (i * 0.09);
    assert.deepEqual(a.sample(az, alt), b.sample(az, alt));
    assert.equal(a.horizonAlt(az), b.horizonAlt(az));
    if (a.sample(az, alt).join() !== c.sample(az, alt).join()) differs++;
  }
  assert.ok(differs > 290, `only ${differs} of 300 ground samples changed with the seed`);
  assert.notEqual(a.horizonAlt(100), c.horizonAlt(100));
  assert.deepEqual(a.sample(40, 50), c.sample(40, 50), 'the sky does not depend on the seed');
});

await test('makeSynthScene: periodic in azimuth, continuous across north, and horizonAlt is the skyline', () => {
  const a = makeSynthScene({ seed: 9 });
  for (const alt of [-30, -5, 2, 25, 60]) {
    assert.deepEqual(a.sample(0, alt), a.sample(360, alt));
    assert.deepEqual(a.sample(123.4, alt), a.sample(123.4 - 720, alt));
    const before = a.sample(359.999, alt), after = a.sample(0.001, alt);
    for (let c = 0; c < 3; c++) near(before[c], after[c], 2, `colour ${c} across north at ${alt}`);
  }
  const custom = makeSynthScene({ seed: 9, skyline: az => 3 + az / 100 });
  near(custom.horizonAlt(50), 3.5, 1e-12);
  assert.ok(custom.sample(50, 3.4)[2] < custom.sample(50, 3.6)[2], 'ground (dark blue channel) below, sky above');
  const skylines = Array.from({ length: 360 }, (_, az) => a.horizonAlt(az));
  assert.ok(Math.min(...skylines) > 0 && Math.max(...skylines) < 10, 'the default skyline stays within 0..10 degrees');
  assert.ok(Math.max(...skylines) - Math.min(...skylines) > 3, 'and is not flat');
});

await test('horizonAlt wraps its azimuth as sample does: horizonAlt(370) is the skyline drawn at 10 (S25)', () => {
  // The default skyline is periodic, so it cannot tell a wrapped azimuth from a raw one. This one is not: it stands
  // 3.1 degrees at azimuth 10, where a raw 370 would read 6.7.
  const lean = makeSynthScene({ seed: 9, skyline: az => 3 + az / 100, textureContrast: 0 });
  // The skyline as drawn: the altitude at which `sample` turns from ground (red 96, flat) to sky (red 118 or more).
  const drawn = (az: number) => {
    let lo = -5, hi = 20;
    for (let i = 0; i < 50; i++) { const mid = (lo + hi) / 2; if (lean.sample(az, mid)[0] < 107) lo = mid; else hi = mid; }
    return (lo + hi) / 2;
  };
  near(drawn(10), 3.1, 1e-9, 'sample draws 3.1 at 10');
  near(drawn(370), 3.1, 1e-9, 'and the same at 370');
  near(lean.horizonAlt(10), 3.1, 1e-12, 'horizonAlt(10)');
  near(lean.horizonAlt(370), 3.1, 1e-12, 'horizonAlt(370)');
  near(lean.horizonAlt(370), drawn(10), 1e-9, 'horizonAlt(370) is the skyline drawn at 10');
  for (const az of [-290, -0.5, 0, 359.5, 360, 725]) near(lean.horizonAlt(az), drawn(az), 1e-9, `horizonAlt(${az}) is what sample draws`);
  near(lean.horizonAlt(360), 3, 1e-12, 'north is 0 and 360');
});

await test('makeSynthScene: a blank sector is one flat colour, ground and sky alike, fading out over 4 degrees', () => {
  const sky: [number, number, number] = [150, 185, 225];
  const plain = makeSynthScene({ seed: 4, sky });
  const blank = makeSynthScene({ seed: 4, sky, blankSectors: [[100, 180], [350, 10]] });
  for (const az of [100, 130.5, 180, 355, 0, 10]) for (const alt of [-40, -1, 0.5, 6, 30, 80]) {
    assert.deepEqual(blank.sample(az, alt), [150, 185, 225], `inside at ${az}, ${alt}`);
  }
  for (const az of [94, 186, 200, 340, 20]) for (const alt of [-20, 3, 40]) {
    assert.deepEqual(blank.sample(az, alt), plain.sample(az, alt), `outside at ${az}, ${alt}`);
  }
  // In the fade the scene is a blend, between the plain scene and the flat colour on every channel.
  const mid = blank.sample(98, -10), p = plain.sample(98, -10);
  for (let c = 0; c < 3; c++) assert.ok(Math.min(p[c], sky[c]) - 1e-9 <= mid[c] && mid[c] <= Math.max(p[c], sky[c]) + 1e-9);
  assert.notDeepEqual(mid, p);
  near(blank.horizonAlt(130), plain.horizonAlt(130), 0, 'the skyline is the truth whether or not it shows');
});

await test('makeSynthScene: textureContrast scales the ground texture and 0 makes the ground flat', () => {
  const spread = (contrast: number) => {
    const s = makeSynthScene({ seed: 2, skyline: () => 40, textureContrast: contrast });
    const v: number[] = [];
    for (let i = 0; i < 2000; i++) v.push(s.sample((i * 7.31) % 360, -30 + (i * 0.019))[1]);
    const mean = v.reduce((a, b) => a + b, 0) / v.length;
    return Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length);
  };
  assert.equal(spread(0), 0);
  const one = spread(1), two = spread(2), half = spread(0.5);
  assert.ok(one > 10 && one < 40, `texture sd ${one} grey levels`);
  near(two / one, 2, 0.15, 'contrast 2 over contrast 1');
  near(half / one, 0.5, 0.05, 'contrast 0.5 over contrast 1');
});

// ---- renderView ------------------------------------------------------------

/** A scene that is black except for a white disc of the given angular radius around one direction. */
function markerScene(az0: number, alt0: number, radiusDeg: number): SynthScene {
  const m = skyVector(az0, alt0), cosR = Math.cos(radiusDeg * DEG);
  return { horizonAlt: () => -90, sample: (az, alt) => (dot(skyVector(az, alt), m) >= cosR ? [255, 255, 255] : [0, 0, 0]) };
}
const K: Intrinsics = { w: 180, h: 320, f: 0.5 / Math.tan(41.14 * DEG / 2) * 180, cx: 90, cy: 160 };
/** These cases read exact pixels, so they ask for none of the sensor noise a frame carries by default (S37). */
const CLEAN = { noise: 0 } as const;

await test('renderView puts a known direction at its projected pixel', () => {
  const cases: { q: Quat; az: number; alt: number; what: string }[] = [
    { q: lookAt(40, pitchP), az: 47, alt: 30, what: 'up and to the right' },
    { q: qmul(lookAt(200, 10), expSO3([0, 0, 15 * DEG])), az: 195, alt: 4, what: 'rolled 15 degrees, down and left' },
    { q: lookAt(355, 22), az: 4, alt: 18, what: 'across north' },
    { q: lookAt(90, 60), az: 100, alt: 58, what: 'high pitch' },
  ];
  for (const { q, az, alt, what } of cases) {
    const img = renderView(markerScene(az, alt, 0.7), q, K, CLEAN);
    let sum = 0, sx = 0, sy = 0;
    for (let y = 0; y < K.h; y++) for (let x = 0; x < K.w; x++) {
      const v = img[(y * K.w + x) * 4] / 255;
      sum += v; sx += v * (x + 0.5); sy += v * (y + 0.5);
    }
    assert.ok(sum > 10, `${what}: the marker is in view (${sum} pixels of light)`);
    const want = projectCamera(K, qrotate(qinv(q), skyVector(az, alt)))!;
    near(sx / sum, want[0], 0.25, `${what}: x`);
    near(sy / sum, want[1], 0.25, `${what}: y`);
  }
});

await test('renderView: the skyline is on the row the pinhole projects it to, at every column', () => {
  const flat = makeSynthScene({ seed: 1, skyline: () => 10, textureContrast: 0 });
  const img = renderView(flat, lookAt(20, 0), K, CLEAN);
  // Ground is [96, 84, 62] and the sky's red is 118 or more up to 58 degrees, so 107 separates them.
  for (const x of [20, 89, 90, 160]) {
    let first = -1;
    for (let y = 0; y < K.h && first < 0; y++) if (img[(y * K.w + x) * 4] < 107) first = y;
    // A horizontal circle at altitude a is the curve y = cy - f tan(a) sqrt(1 + ((x - cx) / f)^2) for a level camera.
    const want = K.cy - K.f * Math.tan(10 * DEG) * Math.sqrt(1 + ((x + 0.5 - K.cx) / K.f) ** 2);
    near(first, want, 1, `first ground row at column ${x}`);
  }
});

await test('renderView supersamples 2 x 2: an edge through the middle of a pixel reads half way', () => {
  // A vertical edge at the azimuth whose projected column is cx + 0.5, so it splits the pixel at column cx exactly.
  const edge = Math.atan(0.5 / K.f) / DEG;
  const step: SynthScene = { horizonAlt: () => -90, sample: az => { const a = az > 180 ? az - 360 : az; return a < edge ? [0, 0, 0] : [200, 200, 200]; } };
  const img = renderView(step, lookAt(0, 0), K, CLEAN);
  const at = (x: number, y: number) => img[(y * K.w + x) * 4];
  for (const y of [5, 160, 300]) {
    assert.equal(at(K.cx - 1, y), 0);
    assert.equal(at(K.cx, y), 100);
    assert.equal(at(K.cx + 1, y), 200);
  }
  assert.equal(img[3], 255);
});

// ---- Sensor noise (S37) ------------------------------------------------------

/** Rec. 601 luma of every pixel of an RGBA image. */
const lumaOf = (img: Uint8ClampedArray): number[] => Array.from({ length: img.length / 4 }, (_, p) => pixelLuminance(img[p * 4], img[p * 4 + 1], img[p * 4 + 2]));
const meanOf = (v: readonly number[]) => v.reduce((a, b) => a + b, 0) / v.length;
const sdOf = (v: readonly number[]) => { const m = meanOf(v); return Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / v.length); };
/** Pearson correlation of two equally long series. */
function corr(a: readonly number[], b: readonly number[]): number {
  const ma = meanOf(a), mb = meanOf(b);
  let sab = 0, saa = 0, sbb = 0;
  for (let i = 0; i < a.length; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) ** 2; sbb += (b[i] - mb) ** 2; }
  return sab / Math.sqrt(saa * sbb);
}
/** The red channel of `noisy` minus that of `clean`: what the noise did to a frame, whatever the scene is. */
const noiseOf = (noisy: Uint8ClampedArray, clean: Uint8ClampedArray): number[] => Array.from({ length: noisy.length / 4 }, (_, p) => noisy[p * 4] - clean[p * 4]);
/** Two frames are the same bytes. A failure names the first byte that differs: `assert.deepEqual` on two 230,000-element
 *  arrays builds a diff of both and runs the process out of memory, which says nothing about the frame. */
function sameBytes(actual: Uint8ClampedArray, expected: Uint8ClampedArray, what: string): void {
  assert.equal(actual.length, expected.length, `${what}: frame length`);
  const i = actual.findIndex((v, j) => v !== expected[j]);
  if (i >= 0) assert.fail(`${what}: byte ${i} (pixel ${i >> 2}, channel ${i & 3}) is ${actual[i]}, expected ${expected[i]}`);
}
/** Two frames of the same size are not the same bytes. */
function differBytes(a: Uint8ClampedArray, b: Uint8ClampedArray, what: string): void {
  assert.equal(a.length, b.length, `${what}: frame length`);
  assert.ok(a.some((v, j) => v !== b[j]), `${what}: the two frames are the same bytes`);
}

/** A sky with no texture and no gradient: whatever varies in a frame of it is the noise. */
const FLAT_SKY_RGB = [120, 150, 190] as const;
const flatSky: SynthScene = { horizonAlt: () => -90, sample: () => [...FLAT_SKY_RGB] };
const qLook = lookAt(10, 20);

await test('renderView: a default frame carries Gaussian luma noise of sd 2, the same on all three channels (S37)', () => {
  const img = renderView(flatSky, qLook, K);
  const luma = lumaOf(img);
  const sd = sdOf(luma);
  assert.ok(sd >= 1.6 && sd <= 2.4, `a flat sky's default luma sd is ${sd}, not within 1.6..2.4`);
  near(sd, 2.02, 0.1, 'sd 2 plus the rounding to 8 bits, in quadrature');
  near(meanOf(luma), pixelLuminance(...FLAT_SKY_RGB), 0.1, 'the noise has zero mean');
  // One draw per pixel on every channel: the offset from the scene's colour is the same integer in red, green and blue.
  for (let p = 0; p < K.w * K.h; p++) {
    const dr = img[p * 4] - FLAT_SKY_RGB[0], dg = img[p * 4 + 1] - FLAT_SKY_RGB[1], db = img[p * 4 + 2] - FLAT_SKY_RGB[2];
    if (dr !== dg || dg !== db) assert.fail(`pixel ${p}: offsets ${dr}, ${dg}, ${db} differ across channels`);
    assert.equal(img[p * 4 + 3], 255);
  }
  // Gaussian and white: kurtosis 3 (sd 0.02 over 57,600 pixels), and no correlation between neighbours (sd 0.004).
  const m = meanOf(luma);
  near(luma.reduce((a, b) => a + (b - m) ** 4, 0) / luma.length / sd ** 4, 3, 0.1, 'kurtosis');
  const right = luma.filter((_, p) => p % K.w < K.w - 1), rightNext = luma.filter((_, p) => p % K.w > 0);
  const below = luma.slice(0, -K.w), belowNext = luma.slice(K.w);
  assert.ok(Math.abs(corr(right, rightNext)) < 0.02, `horizontal neighbours correlate ${corr(right, rightNext)}`);
  assert.ok(Math.abs(corr(below, belowNext)) < 0.02, `vertical neighbours correlate ${corr(below, belowNext)}`);
});

await test('renderView: the default noise rides on the default scene too, and the option sets its sd (S37)', () => {
  const view = lookAt(0, 40);   // all sky, with the sky's own slow gradient across the frame
  const clean = renderView(scene, view, K, CLEAN);
  const added = noiseOf(renderView(scene, view, K), clean);
  const sd = sdOf(added);
  assert.ok(sd >= 1.6 && sd <= 2.4, `the noise on a default frame has sd ${sd}, not within 1.6..2.4`);
  const wide = sdOf(noiseOf(renderView(scene, view, K, { noise: 5 }), clean));
  near(wide, 5, 0.25, 'noise 5');
  // The ground and the skyline carry it too.
  const ground = lookAt(120, 0);
  const groundNoise = sdOf(noiseOf(renderView(scene, ground, K), renderView(scene, ground, K, CLEAN)));
  assert.ok(groundNoise >= 1.6 && groundNoise <= 2.4, `ground noise sd ${groundNoise}`);
});

await test('renderView: noise 0 is noiseless, exactly the sampled scene (S37)', () => {
  const img = renderView(flatSky, qLook, K, { noise: 0 });
  for (let p = 0; p < K.w * K.h; p++)
    if (img[p * 4] !== FLAT_SKY_RGB[0] || img[p * 4 + 1] !== FLAT_SKY_RGB[1] || img[p * 4 + 2] !== FLAT_SKY_RGB[2] || img[p * 4 + 3] !== 255)
      assert.fail(`pixel ${p} is ${img[p * 4]}, ${img[p * 4 + 1]}, ${img[p * 4 + 2]}, ${img[p * 4 + 3]}`);
  near(sdOf(lumaOf(img)), 0, 1e-6, 'a flat sky at noise 0 has a luma sd of 0 (every pixel above is the scene colour, so what is left is float summation)');
  // The seed has nothing to draw at noise 0.
  sameBytes(renderView(flatSky, qLook, K, { noise: 0, seed: 99 }), img, 'noise 0 with a seed');
  sameBytes(renderView(flatSky, lookAt(200, 5), K, CLEAN), img, 'noise 0 at another pose');
});

await test('renderView: the same seed gives identical frames, another seed another pattern, and a default seed follows the pose (S37)', () => {
  const a = renderView(flatSky, qLook, K, { seed: 7 });
  sameBytes(renderView(flatSky, qLook, K, { seed: 7 }), a, 'the same seed');
  const b = renderView(flatSky, qLook, K, { seed: 8 });
  differBytes(b, a, 'another seed');
  const flat = renderView(flatSky, qLook, K, CLEAN);
  assert.ok(Math.abs(corr(noiseOf(a, flat), noiseOf(b, flat))) < 0.02, 'two seeds are two independent patterns');
  // Consecutive integers are no closer than any other pair (a sequential generator seeded 1 apart would be a shifted copy).
  const c = noiseOf(renderView(flatSky, qLook, K, { seed: 9 }), flat);
  assert.ok(Math.abs(corr(noiseOf(b, flat), c)) < 0.02, 'consecutive seeds');
  // No seed: reproducible, because it is a function of the pose, and a different pose is a different pattern, so two views
  // of a scene never share fixed-pattern noise.
  const d = renderView(flatSky, qLook, K), e = renderView(flatSky, lookAt(10.5, 20), K);
  sameBytes(renderView(flatSky, qLook, K), d, 'one pose, one frame');
  differBytes(e, d, 'another pose');
  assert.ok(Math.abs(corr(noiseOf(d, flat), noiseOf(e, flat))) < 0.02, 'two poses are two independent patterns');
  // A given pixel's draw depends on the seed and the pixel alone, not on the frame's size around it.
  const small: Intrinsics = { w: 20, h: 30, f: K.f, cx: 10, cy: 15 };
  const part = renderView(flatSky, qLook, small, { seed: 7 });
  for (let y = 0; y < small.h; y++) for (let x = 0; x < small.w; x++)
    assert.equal(part[(y * small.w + x) * 4], a[(y * K.w + x) * 4], `pixel (${x}, ${y})`);
});

await test('renderView: a negative or non-finite noise is refused', () => {
  for (const noise of [-1, Number.NaN, Infinity]) assert.throws(() => renderView(flatSky, qLook, K, { noise }), RangeError, `noise ${noise}`);
});

// ---- synthPan: timeline ------------------------------------------------------

const pan = synthPan({ ...DEFAULT_PAN, scene });

await test('DEFAULT_PAN is the lens, rate, ring and faults it names', () => {
  assert.deepEqual({ ...DEFAULT_PAN }, {
    shortFovDeg: 41.14, w: 180, h: 320, fps: 15, speedDegS: 20, pitchDeg: 22.98, turnDeg: 385,
    startHoldS: 1, endHoldS: 1, gyroScaleErr: 0.02, driftDegMin: 2, seed: 1, streams: 'both', gyro: true, captureLagMs: 50,
  });
  near(pan.k0.f, 0.5 / Math.tan(41.14 * DEG / 2) * 180, 1e-9);
  assert.deepEqual([pan.k0.w, pan.k0.h, pan.k0.cx, pan.k0.cy], [180, 320, 90, 160]);
});

await test('synthPan: the ring is 385 degrees at pitch 22.98 with level roll, held 1 s at each end', () => {
  // 385 / 20 + 0.5 s of ramp + 2 s of holds = 21.75 s, and 15 fps exposes frames 0..326 (the last at 21,733 ms).
  assert.equal(pan.frames.length, 327);
  assert.equal(pan.frames[0].frameId, 'f000000');
  assert.equal(pan.frames[326].frameId, 'f000326');
  const heading = pan.frames.map(f => headingDeg(f.truth));
  for (const f of pan.frames) {
    near(elevationDeg(f.truth), pitchP, 1e-9, 'pitch');
    near(rollDeg(f.truth), 0, 1e-9, 'roll');
    near(Math.hypot(...f.truth), 1, 1e-12, 'unit quaternion');
  }
  // The first 16 frames (0 to 1000 ms) are one pose; the last frames are one pose, 385 degrees on.
  for (let k = 1; k <= 15; k++) near(wrap180(heading[k] - heading[0]), 0, 1e-9, `hold frame ${k}`);
  near(heading[0], 0, 1e-9);
  near(wrap180(heading[326] - 25), 0, 1e-9, 'ends at 385 mod 360');
  for (let k = 322; k <= 326; k++) near(wrap180(heading[k] - heading[326]), 0, 1e-9, `end hold frame ${k}`);
  const unwrapped = unwrap(heading);
  near(unwrapped[326] - unwrapped[0], 385, 1e-6, 'unwrapped travel');
  // Monotone: no frame goes backwards.
  for (let k = 1; k < unwrapped.length; k++) assert.ok(unwrapped[k] >= unwrapped[k - 1] - 1e-9);
});

await test('synthPan: the rate is 20 deg/s outside the ramps and the pose steps match it', () => {
  const exposure = (k: number) => Math.round(k * 1000 / 15);
  let travel = 0;
  for (let k = 1; k < pan.frames.length; k++) {
    const dt = (exposure(k) - exposure(k - 1)) / 1000;
    const step = angleBetweenDeg(pan.frames[k - 1].truth, pan.frames[k].truth);
    travel += step;
    // Cruise runs from 1.5 s to 20.25 s; stay a frame inside it.
    if (exposure(k - 1) >= 1600 && exposure(k) <= 20150) near(step / dt, 20, 0.2, `rate at frame ${k}`);
  }
  near(travel, 385, 1e-6, 'path length');
});

await test('synthPan: frames are rendered lazily, and render() is deterministic', () => {
  let calls = 0;
  const counting: SynthScene = { horizonAlt: scene.horizonAlt, sample: (az, alt) => { calls++; return scene.sample(az, alt); } };
  const lazy = synthPan({ ...DEFAULT_PAN, scene: counting });
  assert.equal(calls, 0, 'synthPan sampled the scene');
  const a = lazy.frames[100].render();
  assert.equal(calls, 180 * 320 * 4, 'one render takes 2 x 2 samples per pixel');
  assert.equal(a.length, 180 * 320 * 4);
  assert.deepEqual(Array.from(lazy.frames[100].render()), Array.from(a));
  assert.deepEqual(Array.from(pan.frames[100].render()), Array.from(a), 'a scene that counts renders what the scene renders');
  assert.equal(a[3], 255);
  assert.notDeepEqual(Array.from(lazy.frames[200].render()), Array.from(a), 'a later frame looks elsewhere');
  const clean = synthPan({ ...DEFAULT_PAN, scene, noise: 0 });
  const t = renderView(scene, clean.frames[100].truth, clean.k0, CLEAN);
  sameBytes(clean.frames[100].render(), t, 'at noise 0, render() is renderView of the truth pose at k0');
});

await test('synthPan: every frame carries its own reproducible noise pattern, sd 2 by default, and noise 0 is noiseless (S37)', () => {
  const sky = synthPan({ ...DEFAULT_PAN, scene: flatSky });
  const flat = renderView(flatSky, qLook, sky.k0, CLEAN);
  const noise = (p: SynthPan, k: number) => noiseOf(p.frames[k].render(), flat);   // a flat sky: the frame is the noise
  const n0 = noise(sky, 0), n1 = noise(sky, 1);
  for (const n of [n0, n1, noise(sky, 200)]) {
    const sd = sdOf(n);
    assert.ok(sd >= 1.6 && sd <= 2.4, `a default pan frame's noise has sd ${sd}, not within 1.6..2.4`);
  }
  // Frames 0 and 1 are one pose (the start hold) and frames 0 and 200 are not: the pattern is the frame's own, so even
  // two frames of one pose never share fixed-pattern noise.
  assert.deepEqual(sky.frames[0].truth, sky.frames[1].truth, 'frames 0 and 1 are one pose');
  assert.ok(Math.abs(corr(n0, n1)) < 0.02, `frames 0 and 1 share a pattern (correlation ${corr(n0, n1)})`);
  assert.ok(Math.abs(corr(n0, noise(sky, 200))) < 0.02, 'frames 0 and 200');
  // Reproducible, and a frame's noise does not depend on which frames were rendered before it.
  const again = synthPan({ ...DEFAULT_PAN, scene: flatSky });
  again.frames[200].render(); again.frames[3].render();
  sameBytes(again.frames[1].render(), sky.frames[1].render(), 'frame 1 after frames 200 and 3');
  sameBytes(sky.frames[1].render(), sky.frames[1].render(), 'frame 1 twice from one pan');
  // The case seed moves the pattern; the option sets the sd; 0 is exactly the scene.
  const other = synthPan({ ...DEFAULT_PAN, scene: flatSky, seed: 2 });
  assert.ok(Math.abs(corr(n0, noise(other, 0))) < 0.02, 'another case seed, another pattern');
  const loud = noise(synthPan({ ...DEFAULT_PAN, scene: flatSky, noise: 6 }), 0);
  near(sdOf(loud), 6, 0.3, 'noise 6');
  const quiet = synthPan({ ...DEFAULT_PAN, scene: flatSky, noise: 0 });
  for (const k of [0, 1, 200]) sameBytes(quiet.frames[k].render(), flat, `frame ${k} at noise 0`);
  assert.throws(() => synthPan({ ...DEFAULT_PAN, scene: flatSky, noise: -1 }), RangeError);
  // The noise leaves the sensor streams alone.
  assert.equal(JSON.stringify(quiet.observations), JSON.stringify(sky.observations));
});

await test('synthPan: captureTime is the exposure plus the lag, or null, and presentation never precedes either', () => {
  const exposure = (k: number) => Math.round(k * 1000 / 15);
  pan.frames.forEach((f, k) => {
    assert.equal(f.tCaptureMs, exposure(k) + 50);
    assert.equal(f.tPresentMs, exposure(k) + 60);
  });
  const none = synthPan({ ...DEFAULT_PAN, scene, captureLagMs: null });
  none.frames.forEach((f, k) => { assert.equal(f.tCaptureMs, null); assert.equal(f.tPresentMs, exposure(k) + 60); });
  const slow = synthPan({ ...DEFAULT_PAN, scene, captureLagMs: 80 });
  slow.frames.forEach((f, k) => { assert.equal(f.tCaptureMs, exposure(k) + 80); assert.equal(f.tPresentMs, exposure(k) + 90); });
  assert.deepEqual(pan.actions, [{ t_ms: 0, action: 'begin' }, { t_ms: 21793 + 1000, action: 'finish' }]);
});

await test('synthPan: reverseAtDeg turns that far, goes back 60 degrees, then on to turnDeg', () => {
  const rev = synthPan({ ...DEFAULT_PAN, scene, turnDeg: 400, reverseAtDeg: 300 });
  const travel = unwrap(rev.frames.map(f => headingDeg(f.truth)));
  let at = 1;   // the first frame that has gone backwards
  while (at < travel.length && travel[at] >= travel[at - 1] - 1e-9) at++;
  near(travel[at - 1], 300, 0.2, 'turns 300 first');
  let low = at;   // the last frame before it turns forward again
  while (low + 1 < travel.length && travel[low + 1] <= travel[low] + 1e-9) low++;
  near(travel[low], 240, 0.2, 'then back to 240');
  for (let k = low + 1; k < travel.length; k++) assert.ok(travel[k] >= travel[k - 1] - 1e-9, 'forward again');
  near(travel[travel.length - 1], 400, 1e-6, 'and on to 400');
  assert.throws(() => synthPan({ ...DEFAULT_PAN, scene, turnDeg: 100, reverseAtDeg: 300 }), RangeError);
  assert.throws(() => synthPan({ ...DEFAULT_PAN, scene, fps: 0 }), RangeError);
});

// ---- synthPan: the sensor streams -------------------------------------------

type Obs = Record<string, any>;
const relativeOf = (p: SynthPan) => p.observations.filter(r => r.kind === 'orientation' && r.absolute === false) as Obs[];
const absoluteOf = (p: SynthPan) => p.observations.filter(r => r.kind === 'orientation' && r.event === 'deviceorientationabsolute') as Obs[];
const motionOf = (p: SynthPan) => p.observations.filter(r => r.kind === 'motion') as Obs[];
const headingOfReading = (r: Obs) => headingDeg(quatFromDeviceOrientation(r.alpha, r.beta, r.gamma));
/** The pan's true azimuth, unwrapped, at a time in ms: linear between the frames' truth poses. */
function trueAzimuthAt(p: SynthPan, fps: number): (tMs: number) => number {
  const az = unwrap(p.frames.map(f => headingDeg(f.truth)));
  return tMs => {
    const x = Math.min(Math.max(tMs * fps / 1000, 0), az.length - 1), i = Math.min(Math.floor(x), az.length - 2);
    return az[i] + (az[i + 1] - az[i]) * (x - i);
  };
}

await test('the relative stream integrates to turn x (1 + scale): the end-to-end sum and the fitted slope', () => {
  for (const scale of [0.02, 0, -0.03]) for (const seed of [1, 2, 3, 4]) {
    const p = synthPan({ ...DEFAULT_PAN, scene, seed, gyroScaleErr: scale, driftDegMin: 0 });
    const rel = relativeOf(p);
    const yaw = unwrap(rel.map(headingOfReading));
    const want = 385 * (1 + scale);
    // End to end the stream has a resolution of its own: the first and last readings are rounded to 0.1 degree and
    // carry 0.05 degrees of tilt noise, and the last is delivered only once it has moved 0.1 degree from the one
    // before. Measured over 30 seeds that is up to 0.055 % of 392.7, so this bound is 0.1 %.
    near(yaw[yaw.length - 1] - yaw[0], want, want * 0.001, `end to end, scale ${scale}, seed ${seed}`);
    // The fitted slope against the true azimuth has no such end effect: it is the stream's rate of yaw per degree
    // turned, and times the turn it is the integrated yaw to 0.05 %.
    const trueAz = trueAzimuthAt(p, 15);
    const s = slope(rel.map(r => trueAz(r.t_event_ms)), yaw);
    near(385 * s, want, want * 0.0005, `slope, scale ${scale}, seed ${seed}`);
  }
});

await test('the relative stream drifts by driftDegMin', () => {
  // Still for 60 s at 6 deg/min: the relative yaw ramps 0.1 deg/s, fitted to 2 %. The sign is the one `synth.ts`
  // states (S25): a positive drift turns the heading clockwise, so the slope is +0.1, not -0.1 as `sensors.py` would give.
  const still = synthPan({ ...DEFAULT_PAN, scene, turnDeg: 0, startHoldS: 60, endHoldS: 0, driftDegMin: 6, fps: 2 });
  const rel = relativeOf(still);
  assert.ok(rel.length > 30, `${rel.length} relative readings in a still minute`);
  const s = slope(rel.map(r => r.t_event_ms / 1000), unwrap(rel.map(headingOfReading)));
  near(s, 0.1, 0.002, 'drift deg/s');
});

await test('the absolute stream is the compass: it follows the true heading, not the gyro scale', () => {
  // The compass reads the true heading plus its own error (sinusoid 2 degrees, Ornstein-Uhlenbeck sd 3 degrees),
  // whatever the gyro does. One seed cannot tell a 5 % scale from that error; the mean slope against the true
  // azimuth over 24 seeds can: each slope has sd 0.010 (measured over 40 seeds), so their mean has 0.002.
  const slopes: number[] = [];
  for (let seed = 1; seed <= 24; seed++) {
    const p = synthPan({ ...DEFAULT_PAN, scene, seed, gyroScaleErr: 0.05, driftDegMin: 0 });
    const abs = absoluteOf(p), trueAz = trueAzimuthAt(p, 15);
    const yaw = unwrap(abs.map(headingOfReading));
    slopes.push(slope(abs.map(r => trueAz(r.t_event_ms)), yaw));
    if (seed === 1) {
      const err = abs.map((r, i) => wrap180(yaw[i] - trueAz(r.t_event_ms)));
      const rms = Math.sqrt(err.reduce((a, b) => a + b * b, 0) / err.length);
      assert.ok(rms > 1 && rms < 8, `absolute heading error rms ${rms}`);
      assert.ok(Math.abs(err[0]) < 12, 'and it starts at north');
    }
  }
  near(slopes.reduce((a, b) => a + b, 0) / slopes.length, 1, 0.01, 'mean slope of the absolute heading');
});

await test('the relative stream is silent in a hold, and delivered on nearly every pump tick at speed', () => {
  const rel = relativeOf(pan);
  assert.equal(rel.filter(r => r.t_event_ms > 0 && r.t_event_ms < 1000).length, 0, 'silent through the 1 s start hold');
  assert.equal(rel[0].t_event_ms, 0);
  const inWindow = (r: Obs) => r.t_event_ms >= 3000 && r.t_event_ms < 18000;
  const moving = rel.filter(inWindow);
  // 20 deg/s is 0.33 degree per 60 Hz tick, above the 0.1 degree threshold, so a tick is delivered unless its reading
  // age ran 5 ms or more ahead of the next one's (a change under 0.1 degree): the spacing of two readings is
  // triangular on 0..33 ms, which loses 4.5 % of 900 ticks. That is 860 expected (sd about 6; 840 to 872 over 30
  // seeds, 860 on this one). With no reading age every tick changes by 0.33 degree and all 900 are delivered, so the
  // upper bound is what grades the age (S25).
  assert.ok(moving.length >= 840 && moving.length <= 885, `${moving.length} readings in 15 s of turning`);
  // The compass stream is aged the same way, and its own error moves the change test by up to 0.08 degree a tick, so
  // it delivers fewer (765 here; 758 to 792 over 30 seeds), but never all 900.
  const compass = absoluteOf(pan).filter(inWindow);
  assert.ok(compass.length >= 700 && compass.length <= 885, `${compass.length} compass readings in 15 s of turning`);
});

await test('motion is 60 Hz, in the W3C slots (x, y, z), with the gyro scale and Chromium rounding', () => {
  const mot = motionOf(pan);
  assert.equal(mot.length, 1306);
  mot.forEach((m, k) => {
    assert.equal(m.t_event_ms, Math.round(k * 1000 / 60));
    assert.equal(m.t_receive_ms, m.t_event_ms + 5);
  });
  const p = Math.cos(pitchP * DEG), q = Math.sin(pitchP * DEG), omega = 20 * 1.02;
  for (const m of mot.filter(r => r.t_event_ms >= 3000 && r.t_event_ms < 18000)) {
    // A clockwise pan at pitch p reads beta = -omega cos p, gamma = +omega sin p, alpha = 0: a pure pitch would
    // land in alpha. The tolerance is the bias, the noise and the 0.05 rounding.
    near(m.rate.alpha, 0, 0.3, 'alpha');
    near(m.rate.beta, -omega * p, 0.3, 'beta');
    near(m.rate.gamma, omega * q, 0.3, 'gamma');
    for (const v of Object.values(m.rate) as number[]) near(Math.round(v * 10) / 10, v, 1e-9, 'rounded to 0.1');
  }
  assert.equal(motionOf(synthPan({ ...DEFAULT_PAN, scene, gyro: false })).length, 0);
});

await test('a motion sample inside a speed ramp reads the analytic rate at its t_event_ms, not 8 ms later (S25)', () => {
  // The first leg's speed rises as a smoothstep over 0.5 s from the end of the 1 s hold, holds at 20 deg/s, and falls
  // the same way to the end of the leg at 20.75 s. The gyro reads (1 + scale) of that, centred on the sample. A window
  // that starts at the sample (a forward difference) reads the rate 8.3 ms later: high on the way up, low on the way
  // down, by 2.04 (u - u^2) deg/s at u of the way through the ramp, 0.51 at its steepest and 0.34 on average.
  const smooth = (u: number) => { const c = Math.min(Math.max(u, 0), 1); return c * c * (3 - 2 * c); };
  const peak = DEFAULT_PAN.speedDegS * (1 + DEFAULT_PAN.gyroScaleErr), ramp = 500;
  const start = DEFAULT_PAN.startHoldS * 1000, end = start + DEFAULT_PAN.turnDeg / DEFAULT_PAN.speedDegS * 1000 + ramp;
  const mot = motionOf(pan);
  // The sample is a rate vector in the device frame; its length is the turn rate, whatever the pitch. A margin of one
  // window (8.3 ms) keeps each sample's window inside its ramp, where the speed has no kink.
  const errors = (from: number, to: number, rateAt: (tMs: number) => number) => mot
    .filter(m => m.t_event_ms >= from + 10 && m.t_event_ms <= to - 10)
    .map(m => Math.hypot(m.rate.alpha, m.rate.beta, m.rate.gamma) - rateAt(m.t_event_ms));
  const up = errors(start, start + ramp, t => peak * smooth((t - start) / ramp));
  const down = errors(end - ramp, end, t => peak * smooth((end - t) / ramp));
  for (const [name, list] of [['rising', up], ['falling', down]] as const) {
    assert.ok(list.length >= 25, `${list.length} samples in the ${name} ramp`);
    // Noise (0.03 deg/s) and rounding (0.05) leave each sample within 0.09 of the analytic rate here; the mean of 29
    // is within 0.01. The two ramps are graded apart because a late window errs in opposite directions on them.
    for (const e of list) assert.ok(Math.abs(e) <= 0.2, `a ${name} sample is ${e.toFixed(3)} deg/s from the analytic rate`);
    const mean = list.reduce((a, b) => a + b, 0) / list.length;
    near(mean, 0, 0.05, `mean error in the ${name} ramp`);
  }
});

await test('the motion stream integrates to turn x (1 + scale) about the vertical', () => {
  for (const scale of [0.02, 0, -0.03]) for (const seed of [1, 2]) {
    const p = synthPan({ ...DEFAULT_PAN, scene, seed, gyroScaleErr: scale });
    let q = p.frames[0].truth, prev = headingDeg(q), total = 0;
    for (const m of motionOf(p)) {
      const dt = 1 / 60;
      q = qmul(q, expSO3([m.rate.alpha * DEG * dt, m.rate.beta * DEG * dt, m.rate.gamma * DEG * dt]));
      const h = headingDeg(q);
      total += wrap180(h - prev); prev = h;
    }
    // 0.1 % = 0.39 degrees: the 0.01 deg/s bias, 0.03 deg/s noise and rounding to 0.1 deg/s leave under 0.2.
    near(total, 385 * (1 + scale), 385 * (1 + scale) * 0.001, `gyro integral, scale ${scale}, seed ${seed}`);
    near(elevationDeg(q), pitchP, 0.5, 'and the pitch is held');
  }
});

await test('a still phone reads exact zero triples on about 70 % of samples (bias 0.01, noise 0.03, rounding 0.1)', () => {
  // S2: with a bias of 0.05 deg/s the fraction is 13 %. Each axis rounds to zero with P(|N(0.01, 0.03)| < 0.05) =
  // 0.886, so a triple does with 0.886^3 = 0.70; 1,800 samples put the sd of the fraction at 0.011.
  const still = synthPan({ ...DEFAULT_PAN, scene, turnDeg: 0, startHoldS: 30, endHoldS: 0, fps: 2 });
  const mot = motionOf(still);
  const zero = mot.filter(m => m.rate.alpha === 0 && m.rate.beta === 0 && m.rate.gamma === 0).length / mot.length;
  assert.ok(zero > 0.62 && zero < 0.78, `zero triple fraction ${zero}`);
  const turning = motionOf(pan).filter(m => m.t_event_ms >= 3000 && m.t_event_ms < 18000);
  assert.equal(turning.filter(m => m.rate.alpha === 0 && m.rate.beta === 0 && m.rate.gamma === 0).length, 0);
});

const KEYS_ORIENTATION = ['kind', 'event', 't_event_ms', 't_receive_ms', 'alpha', 'beta', 'gamma', 'absolute'];
const KEYS_MOTION = ['kind', 't_event_ms', 't_receive_ms', 'rate'];

function checkRows(p: SynthPan) {
  let previous = -Infinity;
  for (const r of p.observations) {
    assert.notEqual(r.kind, 'frame', 'no frame records in observations');
    assert.ok((r.t_receive_ms as number) >= previous, 'sorted by delivery time');
    previous = r.t_receive_ms as number;
    assert.ok(Number.isInteger(r.t_event_ms), 'integer ms');
    if (r.kind === 'orientation') {
      assert.deepEqual(Object.keys(r), KEYS_ORIENTATION);
      assert.ok(r.event === 'deviceorientation' || r.event === 'deviceorientationabsolute');
      const { alpha, beta, gamma } = r as Obs;
      assert.ok(alpha >= 0 && alpha < 360 && beta >= -180 && beta < 180 && gamma >= -90 && gamma <= 90);
      for (const v of [alpha, beta, gamma]) near(Math.round(v * 10) / 10, v, 1e-9, 'rounded to 0.1');
    } else {
      assert.equal(r.kind, 'motion');
      assert.deepEqual(Object.keys(r), KEYS_MOTION);
      assert.deepEqual(Object.keys(r.rate as object), ['alpha', 'beta', 'gamma']);
    }
  }
}

await test('streams both: deviceorientation with absolute false, deviceorientationabsolute with absolute true (13.1)', () => {
  checkRows(pan);
  const o = pan.observations.filter(r => r.kind === 'orientation') as Obs[];
  const rel = o.filter(r => r.event === 'deviceorientation'), abs = o.filter(r => r.event === 'deviceorientationabsolute');
  assert.ok(rel.length > 500 && abs.length > 500);
  assert.ok(rel.every(r => r.absolute === false) && abs.every(r => r.absolute === true));
  assert.equal(rel.length + abs.length, o.length);
  // The relative stream starts at a seeded yaw zero, not at the compass.
  assert.ok(Math.abs(wrap180(headingOfReading(rel[0]) - headingOfReading(abs[0]))) > 1);
});

await test('streams absolute-only: Chromium shape, every absolute reading repeated as a deviceorientation with absolute true', () => {
  const p = synthPan({ ...DEFAULT_PAN, scene, streams: 'absolute-only' });
  checkRows(p);
  const o = p.observations.filter(r => r.kind === 'orientation') as Obs[];
  assert.ok(o.every(r => r.absolute === true), 'no relative stream, no absolute false');
  const originals = o.filter(r => r.event === 'deviceorientationabsolute');
  const copies = o.filter(r => r.event === 'deviceorientation');
  assert.ok(originals.length > 500);
  assert.equal(copies.length, originals.length);
  // Each original is followed straight away by its copy: the same values at the same times.
  o.forEach((r, i) => {
    if (r.event !== 'deviceorientationabsolute') return;
    const next = o[i + 1];
    assert.equal(next.event, 'deviceorientation');
    assert.deepEqual({ ...next, event: r.event }, r);
  });
  // The absolute readings themselves are those of 'both' (the streams are seeded apart).
  assert.deepEqual(originals, absoluteOf(pan));
  assert.equal(motionOf(p).length, motionOf(pan).length, 'gyro still on');
  const nogyro = synthPan({ ...DEFAULT_PAN, scene, streams: 'absolute-only', gyro: false });
  assert.equal(motionOf(nogyro).length, 0);
  checkRows(nogyro);
});

await test('the seed fixes every stream: the same seed repeats, another changes the noise and the relative yaw zero', () => {
  const again = synthPan({ ...DEFAULT_PAN, scene });
  assert.equal(JSON.stringify(again.observations), JSON.stringify(pan.observations));
  const other = synthPan({ ...DEFAULT_PAN, scene, seed: 2 });
  assert.notEqual(relativeOf(other)[0].alpha, relativeOf(pan)[0].alpha);
  assert.notEqual(absoluteOf(other)[5].alpha, absoluteOf(pan)[5].alpha);
  // Rounding to 0.1 deg/s hides most of the 0.03 deg/s noise, so compare the whole stream.
  assert.notEqual(JSON.stringify(motionOf(other)), JSON.stringify(motionOf(pan)));
  // A stream does not disturb its neighbours: turning the gyro off leaves both orientation streams as they were.
  const quiet = synthPan({ ...DEFAULT_PAN, scene, gyro: false });
  assert.deepEqual(relativeOf(quiet), relativeOf(pan));
  assert.deepEqual(absoluteOf(quiet), absoluteOf(pan));
});

// ---- writeReplayInput ---------------------------------------------------------

function withTemp<T>(fn: (dir: string) => T): T {
  const dir = mkdtempSync(join(tmpdir(), 'synth-test-'));
  try { return fn(dir); } finally { rmSync(dir, { recursive: true, force: true }); }
}

await test('writeReplayInput writes frames, observations and actions in the 13.1 shape', () => withTemp(dir => {
  const small = synthPan({ ...DEFAULT_PAN, scene, w: 45, h: 80, fps: 5, turnDeg: 30, startHoldS: 0.4, endHoldS: 0.4, captureLagMs: null });
  writeReplayInput(dir, small);
  const lines = (name: string) => readFileSync(join(dir, 'input', name), 'utf8').split('\n').filter(l => l).map(l => JSON.parse(l) as Obs);
  const rows = lines('observations.jsonl');
  const frames = rows.filter(r => r.kind === 'frame');
  assert.equal(frames.length, small.frames.length);
  assert.equal(rows.length, small.frames.length + small.observations.length);
  frames.forEach((r, k) => {
    assert.deepEqual(Object.keys(r), ['kind', 'frame_id', 't_capture_ms', 't_present_ms', 'width', 'height', 'file']);
    assert.equal(r.frame_id, small.frames[k].frameId);
    assert.equal(r.t_capture_ms, null, 'a null captureTime is written as null');
    assert.equal(r.t_present_ms, small.frames[k].tPresentMs);
    assert.deepEqual([r.width, r.height], [45, 80]);
    assert.equal(r.file, `frames/${r.frame_id}.png`);
  });
  const delivered = (r: Obs) => (r.kind === 'frame' ? r.t_present_ms : r.t_receive_ms) as number;
  for (let i = 1; i < rows.length; i++) assert.ok(delivered(rows[i]) >= delivered(rows[i - 1]), 'sorted by delivery time');
  assert.deepEqual(rows.filter(r => r.kind !== 'frame'), JSON.parse(JSON.stringify(small.observations)));
  assert.deepEqual(lines('actions.jsonl'), small.actions);
  assert.ok(readFileSync(join(dir, 'input', 'observations.jsonl'), 'utf8').includes('"t_capture_ms":null'));
  // A frame's file is the lazily rendered frame, losslessly.
  for (const k of [0, 3, small.frames.length - 1]) {
    const png = decodePng(readFileSync(join(dir, 'input', 'frames', `${small.frames[k].frameId}.png`)));
    assert.deepEqual([png.width, png.height], [45, 80]);
    assert.deepEqual(Array.from(png.pixels), Array.from(small.frames[k].render()));
  }
  assert.ok(existsSync(join(dir, 'input', 'frames', 'f000000.png')));
}));

await test('replay.ts accepts what writeReplayInput wrote: every frame and reading is delivered and the result files appear', async () => {
  const p = synthPan({ ...DEFAULT_PAN, scene, fps: 10, turnDeg: 40, startHoldS: 0.5, endHoldS: 0.5, streams: 'absolute-only' });
  const dir = mkdtempSync(join(tmpdir(), 'synth-replay-'));
  try {
    writeReplayInput(dir, p);
    const summary = await replayCase(dir);
    assert.equal(summary.frames_delivered, p.frames.length);
    assert.equal(summary.events_delivered, p.observations.length);
    for (const name of ['panorama.png', 'horizon.json', 'columns.json', 'events.jsonl', 'captures.jsonl', 'summary.json'])
      assert.ok(existsSync(join(dir, 'result', name)), `result/${name}`);
    const events = readFileSync(join(dir, 'result', 'events.jsonl'), 'utf8').split('\n').filter(l => l).map(l => JSON.parse(l) as Obs);
    assert.deepEqual(events.map(e => e.frame_id), p.frames.map(f => f.frameId));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

console.log(`synth.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
