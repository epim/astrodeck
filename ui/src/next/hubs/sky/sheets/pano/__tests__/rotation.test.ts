// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T02: the panorama scanner's rotation maths and the shape of its shared types file.
//
// Mutant this file must catch (SPEC-v2 7.2, RI m6): `quatFromBasis` with the sign of the `-forward` column
// flipped. `quatFromBasis maps the camera axes onto the basis` asserts qrotate(q, [0, 0, -1]) equals b.forward.
//
// Mutants found by review that the near-degenerate tests must also catch: EULER_EPS = 1e-3 in
// `deviceOrientationFromQuat`, and the m33 test made absolute (|m33| > EULER_EPS) instead of relative to |cos beta|.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { lookBasis, orientationBasis, type CameraBasis, type V3 } from '../../photosphereGeometry';
import { poseSeparation as legacyPoseSeparation } from '../../photospherePose';
import {
  angleBetweenDeg, axisSeparationDeg, basisFromQuat, deviceOrientationFromQuat, elevationDeg, expSO3, headingDeg,
  intrinsicsAt, logSO3, longFovDeg, poseSeparation, priorFNorm, projectCamera, qinv, qmul, qnormalize, qrotate,
  quatFromBasis, quatFromDeviceOrientation, rollDeg, rotationFromRays, rotationHomography, shortFovDeg, slerp,
  unprojectPixel, worldYaw,
} from '../rotation';
import { BinState, ColState, PANO_H, PANO_W, PROFILE_BINS, PixClass, type Quat } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) < tol, `${what} ${a} != ${b} (tol ${tol})`);
const nearArr = (a: ArrayLike<number>, b: ArrayLike<number>, tol: number, what = '') => {
  assert.equal(a.length, b.length, `${what} length`);
  for (let i = 0; i < a.length; i++) near(a[i], b[i], tol, `${what}[${i}]`);
};
/** Difference of two angles in degrees, wrapped to [0, 180]. */
const angDiff = (a: number, b: number) => Math.abs((((a - b) % 360) + 540) % 360 - 180);

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
const randQuat = (r: () => number): Quat => qnormalize([gauss(r), gauss(r), gauss(r), gauss(r)]);
const randUnit = (r: () => number): V3 => {
  const v: V3 = [gauss(r), gauss(r), gauss(r)], n = Math.hypot(...v);
  return [v[0] / n, v[1] / n, v[2] / n];
};
const randOrientation = (r: () => number) => ({ alpha: r() * 360, beta: r() * 360 - 180, gamma: r() * 180 - 90 });

// ---- quatFromDeviceOrientation and quatFromBasis ---------------------------

// The four W3C cases of photosphereGeometry.test.ts:9-20, with the camera frame expected directly rather than
// generated: at screen angle 0 the camera frame is the device frame, and the rear camera looks north in all four.
// Cases 2-4 are tested there at screen angles 90, 270 and 180; undoing that rotation gives these device axes.
const W3C_CASES: { abg: [number, number, number]; right: V3; up: V3 }[] = [
  { abg: [0, 90, 0], right: [1, 0, 0], up: [0, 0, 1] },
  { abg: [90, 0, -90], right: [0, 0, 1], up: [-1, 0, 0] },
  { abg: [270, 0, 90], right: [0, 0, -1], up: [1, 0, 0] },
  { abg: [180, -90, 0], right: [-1, 0, 0], up: [0, 0, -1] },
];

test('quatFromDeviceOrientation agrees with quatFromBasis(orientationBasis(...)) on the four W3C cases', () => {
  for (const c of W3C_CASES) {
    const [alpha, beta, gamma] = c.abg;
    const q = quatFromDeviceOrientation(alpha, beta, gamma);
    nearArr(q, quatFromBasis(orientationBasis(alpha, beta, gamma, 0)), 1e-12, `case ${c.abg}`);
    nearArr(qrotate(q, [0, 0, -1]), [0, 1, 0], 1e-12, `forward ${c.abg}`);
    nearArr(qrotate(q, [1, 0, 0]), c.right, 1e-12, `right ${c.abg}`);
    nearArr(qrotate(q, [0, 1, 0]), c.up, 1e-12, `up ${c.abg}`);
  }
  // Facing east: forward is east and the right-hand side of the image is south.
  const east = quatFromDeviceOrientation(270, 90, 0);
  nearArr(qrotate(east, [0, 0, -1]), [1, 0, 0], 1e-12, 'east forward');
  nearArr(qrotate(east, [1, 0, 0]), [0, -1, 0], 1e-12, 'east right');
});

test('quatFromBasis maps the camera axes onto the basis: qrotate(q, [0, 0, -1]) equals b.forward', () => {
  const r = rng(7);
  const bases: CameraBasis[] = [];
  for (let i = 0; i < 100; i++) {
    const o = randOrientation(r);
    bases.push(orientationBasis(o.alpha, o.beta, o.gamma, 0), orientationBasis(o.alpha, o.beta, o.gamma, [0, 90, 180, 270][i % 4]));
  }
  for (const [az, alt] of [[0, 0], [90, 20], [200, -35], [359.5, 80], [123, -89]]) bases.push(lookBasis(az, alt));
  // The three half turns about a camera axis have trace -1 and each takes a different branch of the conversion.
  bases.push(
    { right: [1, 0, 0], up: [0, -1, 0], forward: [0, 0, 1] },
    { right: [-1, 0, 0], up: [0, 1, 0], forward: [0, 0, 1] },
    { right: [-1, 0, 0], up: [0, -1, 0], forward: [0, 0, -1] },
  );
  for (const b of bases) {
    const q = quatFromBasis(b);
    near(Math.hypot(...q), 1, 1e-12, 'unit');
    nearArr(qrotate(q, [0, 0, -1]), b.forward, 1e-9, 'forward');
    nearArr(qrotate(q, [1, 0, 0]), b.right, 1e-9, 'right');
    nearArr(qrotate(q, [0, 1, 0]), b.up, 1e-9, 'up');
    const back = basisFromQuat(q);
    nearArr(back.forward, b.forward, 1e-9, 'basisFromQuat forward');
    nearArr(back.right, b.right, 1e-9, 'basisFromQuat right');
    nearArr(back.up, b.up, 1e-9, 'basisFromQuat up');
  }
});

// ---- deviceOrientationFromQuat ---------------------------------------------

test('deviceOrientationFromQuat inverts quatFromDeviceOrientation at 50 seeded random poses, in the W3C ranges', () => {
  const r = rng(11);
  for (let i = 0; i < 50; i++) {
    const o = randOrientation(r);
    const q = quatFromDeviceOrientation(o.alpha, o.beta, o.gamma);
    const got = deviceOrientationFromQuat(q);
    assert.ok(got.alpha >= 0 && got.alpha < 360, `alpha ${got.alpha}`);
    assert.ok(got.beta >= -180 && got.beta < 180, `beta ${got.beta}`);
    assert.ok(got.gamma >= -90 && got.gamma < 90, `gamma ${got.gamma}`);
    near(angDiff(got.alpha, o.alpha), 0, 1e-6, `alpha of pose ${i}`);
    near(got.beta, o.beta, 1e-6, `beta of pose ${i}`);
    near(got.gamma, o.gamma, 1e-6, `gamma of pose ${i}`);
  }
});

test('deviceOrientationFromQuat keeps the rotation at gimbal lock, gamma -90, beta past 90 and the alpha wrap', () => {
  const cases: [number, number, number][] = [
    [30, 90, 0], [30, 90, 25], [200, -90, -40], [200, -90, 60],     // gimbal lock: only alpha +- gamma is determined
    [10, 30, -90], [10, -30, -90], [250, 120, -90], [250, -120, -90],   // gamma at the closed end of its range
    [10, 170, 20], [300, -170, -70], [0, 179.5, 0], [100, -180, 5], // beta outside +-90
    [0, 0, 0], [359.999, 12, 7], [0.001, -12, -7],
  ];
  for (const [alpha, beta, gamma] of cases) {
    const q = quatFromDeviceOrientation(alpha, beta, gamma);
    const got = deviceOrientationFromQuat(q);
    assert.ok(got.alpha >= 0 && got.alpha < 360 && got.beta >= -180 && got.beta < 180 && got.gamma >= -90 && got.gamma < 90,
      `range ${[alpha, beta, gamma]} -> ${JSON.stringify(got)}`);
    const again = quatFromDeviceOrientation(got.alpha, got.beta, got.gamma);
    near(angleBetweenDeg(q, again), 0, 1e-6, `rotation of ${[alpha, beta, gamma]}`);
  }
  // Away from the degenerate cases the three angles come back unchanged as well.
  for (const [alpha, beta, gamma] of [[10, 170, 20], [300, -170, -70], [250, 120, -90], [359.999, 12, 7]]) {
    const got = deviceOrientationFromQuat(quatFromDeviceOrientation(alpha, beta, gamma));
    near(angDiff(got.alpha, alpha), 0, 1e-6, 'alpha'); near(got.beta, beta, 1e-6, 'beta'); near(got.gamma, gamma, 1e-6, 'gamma');
  }
});

// Poses a hair off the degenerate ones. The conversion divides by cos(beta) and cos(gamma), so what matters is the
// size of those two relative to float noise, and the thresholds must be tested at every scale from 1e-9 degrees up
// to a tenth of a degree: a threshold that is too wide throws away a real angle (EULER_EPS = 1e-3 does that), and one
// that tests the wrong quantity picks the wrong half of the sphere (an absolute test of m33 = cos(beta) cos(gamma),
// which is small whenever cos(beta) is, does that in a band about 1e-7 degrees wide).
test('deviceOrientationFromQuat keeps the rotation within 1e-4 degrees just off beta = +-90 and gamma = +-90', () => {
  const check = (alpha: number, beta: number, gamma: number) => {
    const q = quatFromDeviceOrientation(alpha, beta, gamma);
    const got = deviceOrientationFromQuat(q);
    const where = `${[alpha, beta, gamma]} -> ${JSON.stringify(got)}`;
    assert.ok(got.alpha >= 0 && got.alpha < 360 && got.beta >= -180 && got.beta < 180 && got.gamma >= -90 && got.gamma < 90, `range ${where}`);
    near(angleBetweenDeg(q, quatFromDeviceOrientation(got.alpha, got.beta, got.gamma)), 0, 1e-4, `rotation of ${where}`);
  };
  // The poses a review found wrong by 30, 30, 10 and 20 degrees: cos(beta) about 1e-9 with cos(gamma) of order 1.
  check(30, 90 - 8e-8, 60); check(30, 90 + 8e-8, -60); check(30, 90 - 6e-8, 80); check(100, -90 + 9e-8, 70);
  // beta within 1e-9 .. 0.1 degrees of +-90 on either side, at gammas from the middle of the range to near its ends.
  const offsets = Array.from({ length: 400 }, (_, i) => 10 ** (-9 + 8 * i / 399));
  let cases = 0;
  for (const off of offsets) for (const gamma of [60, -60, 80, -80, 10, 0]) for (const beta of [90 - off, 90 + off, -90 + off, -90 - off]) {
    check(30, beta, gamma); cases++;
  }
  // gamma within 1e-9 .. 0.1 degrees of its closed end -90 and of its open end 90, at betas on both sides of 90.
  for (const off of offsets) for (const beta of [30, -30, 150, -120]) {
    check(200, beta, -90 + off); check(200, beta, 90 - off); cases += 2;
  }
  assert.equal(cases, 400 * 24 + 400 * 8);
});

// ---- pinhole -----------------------------------------------------------------

test('unprojectPixel inverts projectCamera at 25 points for 3 focal lengths', () => {
  for (const fNorm of [0.9521, 1.2694, 2.1]) {
    const k = intrinsicsAt(0, 180, 320, fNorm);
    let points = 0;
    for (let i = 0; i < 5; i++) for (let j = 0; j < 5; j++) {
      const x = 3.25 + 43.4 * i, y = 5.75 + 77.1 * j;
      const ray = unprojectPixel(k, x, y);
      near(Math.hypot(...ray), 1, 1e-12, 'unit ray');
      assert.ok(ray[2] < 0, 'in front of the camera');
      const back = projectCamera(k, ray);
      assert.ok(back !== null);
      near(back![0], x, 1e-9, `x at f ${fNorm}`); near(back![1], y, 1e-9, `y at f ${fNorm}`);
      points++;
    }
    assert.equal(points, 25);
  }
});

test('unprojectPixel and projectCamera: axes, angles, and nothing behind the camera', () => {
  const k = intrinsicsAt(1, 180, 320, 1.2694);   // 90 x 160, f = 114.246
  nearArr(unprojectPixel(k, k.cx, k.cy), [0, 0, -1], 1e-12, 'centre');
  // Image y runs down: a pixel above the centre looks up, a pixel right of the centre looks right.
  const up = unprojectPixel(k, k.cx, k.cy - 10), right = unprojectPixel(k, k.cx + 10, k.cy);
  assert.ok(up[1] > 0 && Math.abs(up[0]) < 1e-12);
  assert.ok(right[0] > 0 && Math.abs(right[1]) < 1e-12);
  // f pixels from the centre is 45 degrees off the optical axis.
  const diag = unprojectPixel(k, k.cx + k.f, k.cy);
  near(Math.acos(-diag[2]) / (Math.PI / 180), 45, 1e-9, 'angle');
  assert.equal(projectCamera(k, [0, 0, 1]), null);
  assert.equal(projectCamera(k, [1, 0, 0]), null);
  assert.equal(projectCamera(k, [0, 1, 0.5]), null);
  nearArr(projectCamera(k, [0, 0, -2])!, [k.cx, k.cy], 1e-12, 'a ray need not be a unit vector');
});

// ---- rotationFromRays --------------------------------------------------------

test('rotationFromRays recovers a rotation from 16 noisy ray pairs within 0.01 degrees', () => {
  const noise = 1e-4;   // 0.0057 degrees per ray component
  let worst = 0;
  for (let trial = 0; trial < 12; trial++) {
    const r = rng(100 + trial);
    const truth = randQuat(r);
    const a: V3[] = [], b: V3[] = [];
    for (let i = 0; i < 16; i++) {
      const ray = randUnit(r), t = qrotate(truth, ray);
      const n: V3 = [t[0] + noise * gauss(r), t[1] + noise * gauss(r), t[2] + noise * gauss(r)], len = Math.hypot(...n);
      a.push(ray); b.push([n[0] / len, n[1] / len, n[2] / len]);
    }
    const got = rotationFromRays(a, b);
    worst = Math.max(worst, angleBetweenDeg(got, truth));
  }
  assert.ok(worst < 0.01, `worst error ${worst} degrees`);
});

test('rotationFromRays is exact without noise, honours weights, and rejects mismatched lengths', () => {
  const r = rng(5);
  const truth = randQuat(r);
  const a: V3[] = Array.from({ length: 8 }, () => randUnit(r));
  const b = a.map(v => qrotate(truth, v));
  near(angleBetweenDeg(rotationFromRays(a, b), truth), 0, 1e-8, 'exact');
  // Garbage pairs with weight 0 change nothing, and uniform weights are the unweighted answer.
  const junkA = [...a, randUnit(r), randUnit(r)], junkB = [...b, randUnit(r), randUnit(r)];
  const w = [...a.map(() => 1), 0, 0];
  near(angleBetweenDeg(rotationFromRays(junkA, junkB, w), truth), 0, 1e-8, 'zero weights');
  near(angleBetweenDeg(rotationFromRays(a, b, a.map(() => 3.5)), truth), 0, 1e-8, 'uniform weights');
  // Weighting the good pairs up turns a bad pair into a small bias, not a wrong answer.
  const mixedB = b.map((v, i) => (i === 0 ? randUnit(r) : v));
  const weighted = rotationFromRays(a, mixedB, a.map((_, i) => (i === 0 ? 0.01 : 1)));
  assert.ok(angleBetweenDeg(weighted, truth) < 3, 'a down-weighted outlier');
  assert.ok(rotationFromRays(a, b)[0] >= 0, 'w >= 0');
  assert.throws(() => rotationFromRays(a, b.slice(1)), RangeError);
  assert.throws(() => rotationFromRays(a, b, [1]), RangeError);
  nearArr(rotationFromRays([], []), [1, 0, 0, 0], 1e-15, 'no pairs is the identity');
});

test('rotationFromRays does not depend on the scale of the weights or of the rays', () => {
  const r = rng(9);
  const truth = randQuat(r);
  const a: V3[] = Array.from({ length: 8 }, () => randUnit(r));
  const b = a.map(v => qrotate(truth, v));
  // Weights from inverse covariances span many decades. The eigenproblem's stopping rule must be relative to the
  // matrix, or a small enough scale stops it before the first sweep and returns a coordinate axis (19 degrees off at 1e-16).
  for (const scale of [1e-100, 1e-30, 1e-16, 1e-12, 1e-3, 1, 1e3, 1e12, 1e100]) {
    near(angleBetweenDeg(rotationFromRays(a, b, a.map(() => scale)), truth), 0, 1e-8, `uniform weight ${scale}`);
    const ws = a.map((_, i) => scale * (1 + i / 4));
    near(angleBetweenDeg(rotationFromRays(a, b, ws), truth), 0, 1e-8, `graded weights from ${scale}`);
  }
  // Rays that are not unit length scale the matrix the same way.
  for (const len of [1e-9, 1e-4, 1e4, 1e9]) {
    const sa = a.map(v => [v[0] * len, v[1] * len, v[2] * len] as V3), sb = b.map(v => [v[0] * len, v[1] * len, v[2] * len] as V3);
    near(angleBetweenDeg(rotationFromRays(sa, sb), truth), 0, 1e-8, `rays of length ${len}`);
  }
  // All weights zero says nothing; it is the identity, as no pairs is.
  nearArr(rotationFromRays(a, b, a.map(() => 0)), [1, 0, 0, 0], 1e-15, 'zero weights everywhere');
});

// ---- rotationHomography ------------------------------------------------------

test('rotationHomography maps a synthetic second view within 0.05 px', () => {
  const r = rng(21);
  // Two cameras a few degrees apart. Pixels are produced by an independent pinhole built from the bases, not by
  // projectCamera: camera coordinates are the dot products with right, up and forward, depth is along forward.
  const bA = orientationBasis(30, 80, 5, 0), bB = orientationBasis(41, 86, -3, 0);
  const ka = intrinsicsAt(0, 180, 320, 1.2694), kb = intrinsicsAt(1, 180, 320, 1.31);   // different size and focal
  const pixelIn = (b: CameraBasis, k: { f: number; cx: number; cy: number }, X: V3): [number, number] | null => {
    const depth = X[0] * b.forward[0] + X[1] * b.forward[1] + X[2] * b.forward[2];
    if (depth <= 0.2) return null;
    const dotv = (u: V3) => X[0] * u[0] + X[1] * u[1] + X[2] * u[2];
    return [k.cx + k.f * dotv(b.right) / depth, k.cy - k.f * dotv(b.up) / depth];
  };
  const qBA = qmul(qinv(quatFromBasis(bB)), quatFromBasis(bA));   // takes camera-a vectors to camera-b vectors
  const H = rotationHomography(ka, kb, qBA);
  assert.ok(H instanceof Float64Array && H.length === 9);
  let checked = 0;
  for (let i = 0; i < 400 && checked < 60; i++) {
    const xa = r() * ka.w, ya = r() * ka.h;
    const ca = [(xa - ka.cx) / ka.f, (ya - ka.cy) / ka.f];
    const X: V3 = [0, 1, 2].map(m => bA.forward[m] + ca[0] * bA.right[m] - ca[1] * bA.up[m]) as V3;
    const len = Math.hypot(...X), dir: V3 = [X[0] / len, X[1] / len, X[2] / len];
    const want = pixelIn(bB, kb, dir);
    if (!want) continue;
    const back = pixelIn(bA, ka, dir)!;
    near(back[0], xa, 1e-9, 'the test pinhole is self-consistent');
    const u = H[0] * xa + H[1] * ya + H[2], v = H[3] * xa + H[4] * ya + H[5], s = H[6] * xa + H[7] * ya + H[8];
    near(u / s, want[0], 0.05, 'x'); near(v / s, want[1], 0.05, 'y');
    near(u / s, want[0], 1e-6, 'x exact'); near(v / s, want[1], 1e-6, 'y exact');
    checked++;
  }
  assert.ok(checked >= 40, `${checked} points in front of both cameras`);
  // The identity rotation between equal intrinsics is the identity homography, up to scale.
  const I = rotationHomography(ka, ka, [1, 0, 0, 0]);
  nearArr(Array.from(I).map(x => x / I[8]), [1, 0, 0, 0, 1, 0, 0, 0, 1], 1e-12, 'identity');
});

// ---- worldYaw, heading, elevation, roll, separation ---------------------------

test('worldYaw(d) adds d to headingDeg and leaves elevation and roll alone', () => {
  const r = rng(31);
  for (let i = 0; i < 40; i++) {
    const o = randOrientation(r);
    const q = quatFromDeviceOrientation(o.alpha, o.beta, o.gamma);
    if (Math.abs(elevationDeg(q)) > 85) continue;   // the azimuth of the zenith is undefined
    for (const d of [0, 10, -10, 90, 179, 350, -370, 720.5]) {
      const yawed = qmul(worldYaw(d), q);
      near(angDiff(headingDeg(yawed), headingDeg(q) + d), 0, 1e-9, `heading +${d}`);
      near(elevationDeg(yawed), elevationDeg(q), 1e-9, 'elevation');
      near(rollDeg(yawed), rollDeg(q), 1e-9, 'roll');
    }
  }
  // Independent: a camera facing north, yawed by 90, faces east.
  const north = quatFromBasis(lookBasis(0, 0));
  nearArr(qrotate(qmul(worldYaw(90), north), [0, 0, -1]), [1, 0, 0], 1e-12, 'north + 90 = east');
  near(headingDeg(qmul(worldYaw(20), quatFromBasis(lookBasis(350, 10)))), 10, 1e-9, 'wraps past 360');
});

test('headingDeg, elevationDeg and rollDeg read the optical axis and the right vector', () => {
  for (const az of [0, 12.3, 90, 180, 270, 359.5]) for (const alt of [-30, 0, 45, 80]) {
    const q = quatFromBasis(lookBasis(az, alt));
    near(headingDeg(q), az, 1e-9, `heading of ${az}/${alt}`);
    near(elevationDeg(q), alt, 1e-9, `elevation of ${az}/${alt}`);
    near(rollDeg(q), 0, 1e-9, `roll of ${az}/${alt}`);
  }
  const h = headingDeg(quatFromBasis(lookBasis(0, 0)));
  assert.ok(h >= 0 && h < 360);
  // Straight up or down has no azimuth and reads 0 at every azimuth the pose was built from, not only the one where
  // the float noise happens to be zero; a yaw of the world leaves it at 0 as well. A degree off the pole it reads true.
  for (const az of [0, 45, 123, 271.5]) for (const alt of [90, -90]) {
    assert.equal(headingDeg(quatFromBasis(lookBasis(az, alt))), 0, `straight ${alt} at ${az} reads 0`);
    assert.equal(headingDeg(qmul(worldYaw(37), quatFromBasis(lookBasis(az, alt)))), 0, `yawed straight ${alt} at ${az} reads 0`);
  }
  near(headingDeg(quatFromBasis(lookBasis(123, 89))), 123, 1e-9, 'a degree off the zenith');
  near(headingDeg(quatFromBasis(lookBasis(123, -89))), 123, 1e-9, 'a degree off the nadir');
  // Facing north with the right edge raised by theta: right = (cos, 0, sin), up = (-sin, 0, cos).
  for (const theta of [-30, 0, 10, 45]) {
    const t = theta * Math.PI / 180;
    const q = quatFromBasis({ right: [Math.cos(t), 0, Math.sin(t)], up: [-Math.sin(t), 0, Math.cos(t)], forward: [0, 1, 0] });
    near(rollDeg(q), theta, 1e-9, `roll ${theta}`);
    near(headingDeg(q), 0, 1e-9, 'heading under roll');
    near(elevationDeg(q), 0, 1e-9, 'elevation under roll');
  }
});

test('axisSeparationDeg ignores roll, angleBetweenDeg does not, and both keep precision at small angles', () => {
  const level = quatFromBasis(lookBasis(0, 0));
  near(axisSeparationDeg(level, quatFromBasis(lookBasis(0, 20))), 20, 1e-9, 'pitch 20');
  near(axisSeparationDeg(quatFromBasis(lookBasis(350, 0)), quatFromBasis(lookBasis(10, 0))), 20, 1e-9, 'across the wrap');
  near(axisSeparationDeg(level, quatFromBasis(lookBasis(180, 0))), 180, 1e-9, 'opposite');
  near(axisSeparationDeg(level, quatFromBasis(lookBasis(0.0001, 0))), 0.0001, 1e-9, 'a ten-thousandth of a degree');
  // A roll about the optical axis moves the image but not the axis.
  const rolled = qmul(level, expSO3([0, 0, 25 * Math.PI / 180]));
  near(axisSeparationDeg(level, rolled), 0, 1e-9, 'roll leaves the axis');
  near(angleBetweenDeg(level, rolled), 25, 1e-9, 'roll is a rotation');
  near(angleBetweenDeg(level, qmul(worldYaw(30), level)), 30, 1e-9, 'yaw 30');
  near(angleBetweenDeg(level, level), 0, 1e-12, 'same');
  near(angleBetweenDeg(level, [-level[0], -level[1], -level[2], -level[3]]), 0, 1e-9, 'q and -q');
  near(angleBetweenDeg(level, expSO3([0, 0, Math.PI])), 180, 1e-9, 'half turn');
});

test('poseSeparation is the photospherePose.ts function, copied', () => {
  const r = rng(41);
  for (let i = 0; i < 30; i++) {
    const a = orientationBasis(r() * 360, r() * 360 - 180, r() * 180 - 90, 0), b = orientationBasis(r() * 360, r() * 360 - 180, r() * 180 - 90, 0);
    assert.equal(poseSeparation(a, b), legacyPoseSeparation(a, b));
  }
  assert.equal(poseSeparation(lookBasis(0, 0), lookBasis(0, 0)), 0);
  near(poseSeparation(lookBasis(0, 0), lookBasis(0, 12)), 12, 1e-9);
});

// ---- quaternion algebra -------------------------------------------------------

test('qmul composes right to left, qinv inverts, qnormalize normalises', () => {
  const r = rng(51);
  for (let i = 0; i < 20; i++) {
    const a = randQuat(r), b = randQuat(r), v = randUnit(r);
    nearArr(qrotate(qmul(a, b), v), qrotate(a, qrotate(b, v)), 1e-12, 'composition');
    nearArr(qmul(a, qinv(a)), [1, 0, 0, 0], 1e-12, 'a . a^-1');
    nearArr(qmul(qinv(a), a), [1, 0, 0, 0], 1e-12, 'a^-1 . a');
    nearArr(qrotate(qinv(a), qrotate(a, v)), v, 1e-12, 'qinv undoes qrotate');
  }
  // A quarter turn about +z takes east to north (right-handed).
  nearArr(qrotate(expSO3([0, 0, Math.PI / 2]), [1, 0, 0]), [0, 1, 0], 1e-12, 'right-handed');
  nearArr(qinv([2, 0, 0, 0]), [0.5, 0, 0, 0], 1e-12, 'inverse of a non-unit quaternion');
  nearArr(qnormalize([3, 0, 4, 0]), [0.6, 0, 0.8, 0], 1e-12, 'normalise');
  nearArr(qnormalize([0, 0, 0, 0]), [1, 0, 0, 0], 1e-15, 'zero is the identity');
});

test('slerp takes the shortest arc at constant angular speed', () => {
  const r = rng(61);
  for (let i = 0; i < 20; i++) {
    const a = randQuat(r), b = randQuat(r), total = angleBetweenDeg(a, b);
    nearArr(slerp(a, b, 0), a, 1e-12, 'u = 0');
    near(angleBetweenDeg(slerp(a, b, 1), b), 0, 1e-9, 'u = 1');
    for (const u of [0.1, 0.25, 0.5, 0.9]) {
      const m = slerp(a, b, u);
      near(Math.hypot(...m), 1, 1e-12, 'unit');
      near(angleBetweenDeg(a, m), u * total, 1e-8, `angle at ${u}`);
      near(angleBetweenDeg(m, b), (1 - u) * total, 1e-8, `remaining at ${u}`);
    }
    // -b is the same rotation as b, so the path is the same.
    const flipped = slerp(a, [-b[0], -b[1], -b[2], -b[3]], 0.37);
    near(angleBetweenDeg(flipped, slerp(a, b, 0.37)), 0, 1e-9, 'q and -q');
  }
  // Two samples 16 ms apart turning at 30 deg/s: the midpoint is 0.24 degrees from each end.
  const start = quatFromBasis(lookBasis(10, 5)), end = qmul(worldYaw(0.48), start);
  near(angleBetweenDeg(start, slerp(start, end, 0.5)), 0.24, 1e-9, 'half of 0.48 degrees');
  near(angleBetweenDeg(start, slerp(start, start, 0.5)), 0, 1e-9, 'equal ends');
});

test('expSO3 and logSO3 are inverses on the shorter rotation', () => {
  const r = rng(71);
  for (let i = 0; i < 50; i++) {
    const axis = randUnit(r), angle = r() * 3.1;
    const v: V3 = [axis[0] * angle, axis[1] * angle, axis[2] * angle];
    const q = expSO3(v);
    near(Math.hypot(...q), 1, 1e-12, 'unit');
    nearArr(logSO3(q), v, 1e-9, 'log(exp(v))');
    nearArr(logSO3([-q[0], -q[1], -q[2], -q[3]]), v, 1e-9, 'log(-q)');
  }
  nearArr(expSO3([0, 0, 0]), [1, 0, 0, 0], 1e-15, 'zero');
  nearArr(logSO3([1, 0, 0, 0]), [0, 0, 0], 1e-15, 'identity');
  nearArr(logSO3(expSO3([1e-10, -2e-10, 3e-10])), [1e-10, -2e-10, 3e-10], 1e-20, 'tiny angle');
  // Past a half turn the shorter way round is the other way.
  nearArr(logSO3(expSO3([0, 0, 3.5])), [0, 0, 3.5 - 2 * Math.PI], 1e-9, 'beyond pi');
});

// ---- focal and field of view --------------------------------------------------

test('priorFNorm gives 1.2694 at 9:16 and 0.9521 at 3:4, and the fields of view follow', () => {
  // SPEC-v2 3.2 quotes 1.2694 (from 1.7778 / 2 / 0.70021); the exact values are 1.269465 and 0.952099.
  near(priorFNorm(180, 320), 1.2694, 1e-4, '9:16');
  near(priorFNorm(240, 320), 0.9521, 1e-4, '3:4');
  near(priorFNorm(180, 320), (320 / 180) / 2 / Math.tan(35 * Math.PI / 180), 1e-12, '9:16 exact');
  near(priorFNorm(240, 320), (320 / 240) / 2 / Math.tan(35 * Math.PI / 180), 1e-12, '3:4 exact');
  near(priorFNorm(1080, 1920), priorFNorm(180, 320), 1e-12, 'only the aspect matters');
  near(shortFovDeg(priorFNorm(180, 320)), 43.00, 0.01, 'short axis 9:16');   // 42.996
  near(shortFovDeg(priorFNorm(240, 320)), 55.40, 0.02, 'short axis 3:4');      // 55.413
  near(longFovDeg(priorFNorm(180, 320), 180, 320), 70, 1e-9, 'long axis 9:16');
  near(longFovDeg(priorFNorm(240, 320), 240, 320), 70, 1e-9, 'long axis 3:4');
  near(priorFNorm(180, 320, 60), 1.7778 / 2 / Math.tan(Math.PI / 6), 1e-4, 'another long axis');
  near(shortFovDeg(0.5), 90, 1e-9, 'f = half the short edge is a right angle');
});

test('intrinsicsAt halves the frame and keeps f proportional to the short edge', () => {
  const f = 1.2694;
  const k0 = intrinsicsAt(0, 180, 320, f), k1 = intrinsicsAt(1, 180, 320, f), k2 = intrinsicsAt(2, 180, 320, f);
  assert.deepEqual([k0.w, k0.h, k0.cx, k0.cy], [180, 320, 90, 160]);
  assert.deepEqual([k1.w, k1.h, k1.cx, k1.cy], [90, 160, 45, 80]);
  assert.deepEqual([k2.w, k2.h, k2.cx, k2.cy], [45, 80, 22.5, 40]);
  near(k0.f, f * 180, 1e-12); near(k1.f, f * 90, 1e-12); near(k2.f, f * 45, 1e-12);
  assert.deepEqual([intrinsicsAt(1, 240, 320, 0.9521).w, intrinsicsAt(1, 240, 320, 0.9521).h], [120, 160]);
});

// ---- types.ts --------------------------------------------------------------------

const TYPES_SOURCE = readFileSync(new URL('../types.ts', import.meta.url), 'utf8');
/** RI B2: a line of types.ts may not begin, after whitespace, with a class, function, enum or declare. */
const FORBIDDEN_START = /^\s*(export\s+(class|function|enum|const\s+enum|declare)\b|class\b|function\b|enum\b|declare\b)/;

test('the forbidden-declaration scan can fail: it flags every banned form and passes the ones types.ts uses', () => {
  for (const bad of [
    'export class A {}', '  export function f() {}', 'export enum E { A }', 'export const enum E { A }', 'export declare const x: number;',
    'class A {}', '\tfunction f() {}', 'enum E { A }', '    declare const y: number;', 'export  function  g() {}',
  ]) assert.ok(FORBIDDEN_START.test(bad), `should flag: ${bad}`);
  for (const ok of [
    'export const PANO_W = 1080;', 'export type Quat = readonly [w: number];', 'export interface Stats { n: number }',
    '// export function f() {}', '/** a class of its own */', '  readonly classes: number;', 'export const ColState = { Measured: 0 } as const;',
  ]) assert.ok(!FORBIDDEN_START.test(ok), `should pass: ${ok}`);
});

test('types.ts declares no class, function, enum or declare, and only type-only imports', () => {
  const lines = TYPES_SOURCE.split(/\r?\n/);
  assert.ok(lines.length > 300, 'the file was read');
  lines.forEach((line, i) => {
    assert.ok(!FORBIDDEN_START.test(line), `types.ts:${i + 1} begins with a banned form: ${line.trim()}`);
    if (/^\s*import\b/.test(line)) assert.ok(/^\s*import type\b/.test(line), `types.ts:${i + 1} is not a type-only import`);
  });
  assert.notEqual(TYPES_SOURCE.charCodeAt(0), 0xfeff, 'no BOM');
});

test('types.ts holds the twelve blocks of SPEC-v2 3.4 in order, and the numeric constants are exact', () => {
  const found = [...TYPES_SOURCE.matchAll(/^\/\/ \[block: (types:[a-z]+)\]/gm)].map(m => m[1]);
  assert.deepEqual(found, [
    'types:geometry', 'types:raster', 'types:sensors', 'types:focal', 'types:pyramid', 'types:align',
    'types:tracker', 'types:horizon', 'types:live', 'types:camera', 'types:scanner', 'types:report',
  ]);
  assert.equal(PANO_W, 1080); assert.equal(PANO_H, 300); assert.equal(PROFILE_BINS, 720);
  assert.deepEqual({ ...PixClass }, { None: 0, Sensor: 1, Blurred: 2, Aligned: 3 });
  assert.deepEqual({ ...ColState }, { Measured: 0, Low: 1, UnknownUnseen: 2, UnknownDark: 3, UnknownContrast: 4, Tall: 5 });
  assert.deepEqual({ ...BinState }, { Measured: 0, Low: 1, Unknown: 2, Tall: 3, Overhead: 4, Edited: 5, Kept: 6 });
});

console.log(`rotation.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
