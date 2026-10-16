// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T28: loop.ts, the pure parts of the loop closure (SPEC-v2 4.9, 4.10, the loop and closure row of 7.2): which early keyframes the
// newest one may be matched against, how the residual is spread along the chain, and the test for a view that repeats along yaw
// (S36). The closure in the tracker is graded by trackerClosure.test.ts and trackerClosureFallback.test.ts.
//
// Mutants this file must catch: the candidate list not capped at 2 or not limited to the first 4 keyframes; the 15-degree
// limit moved; the 350-degree trigger moved; `distributeClosure` giving the last keyframe the whole residual and the rest
// nothing (the linear case fails); a share taken from the keyframe index instead of the cumulative angle (the uneven-spacing
// case fails); `repeatsAlongYaw` reading the whole autocorrelation, main lobe included (every smooth view then repeats).
import assert from 'node:assert/strict';
import { lookBasis } from '../../photosphereGeometry';
import {
  CLOSURE_AXIS_DEG, CLOSURE_FIRST, CLOSURE_MAX_CANDIDATES, CLOSURE_MIN_DEG, REPEAT_MAX_LAG_PX, closureCandidates, distributeClosure, repeatsAlongYaw,
} from '../loop';
import { axisSeparationDeg, quatFromBasis } from '../rotation';
import type { Keyframe, Quat, V3 } from '../types';

let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}
const near = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} is not within ${tol} of ${b}`);

/** A keyframe that is only an id and a pose: the only fields the candidate rule reads. */
const kfAt = (id: number, az: number, alt = 0): Keyframe => ({ id, pose: quatFromBasis(lookBasis(az, alt)) as Quat } as unknown as Keyframe);

// ---- closureCandidates -----------------------------------------------------------------------------------------------

test('the candidate rule: none below 350 degrees, at most 2 among the first 4 keyframes within 15 degrees, nearest first', () => {
  assert.equal(CLOSURE_MIN_DEG, 350);
  assert.equal(CLOSURE_FIRST, 4);
  assert.equal(CLOSURE_AXIS_DEG, 15);
  assert.equal(CLOSURE_MAX_CANDIDATES, 2);
  // The ring started at azimuth 0 and the keyframes are 4.5 degrees apart: 0, 4.5, 9, 13.5, then the rest of the ring.
  const kfs = [kfAt(0, 0), kfAt(1, 4.5), kfAt(2, 9), kfAt(3, 13.5), kfAt(4, 18), kfAt(5, 22.5)];
  const cur = kfAt(6, 358);
  const all = [...kfs, cur];
  assert.deepEqual(closureCandidates(all, cur, 349.99), [], 'under 350: none');
  assert.deepEqual(closureCandidates(all, cur, 0), []);
  assert.deepEqual(closureCandidates(all, cur, 350), [0, 1], 'at 350: the two nearest, nearest first (2 and 6.5 degrees away)');
  assert.deepEqual(closureCandidates(all, cur, 360), [0, 1]);
  assert.deepEqual(closureCandidates(all, cur, -360), [0, 1], 'the turn may be the other way');
  assert.deepEqual(closureCandidates(all, cur, -349), []);
  // Nearest first is by distance, not by id.
  const late = kfAt(6, 11);
  assert.deepEqual(closureCandidates([...kfs, late], late, 360), [2, 3], 'at 11 degrees keyframes 2 (2 away) and 1 (6.5) and 3 (2.5): the two nearest are 2 and 3');
});

test('the candidates come from the first 4 keyframes only, never the current one, and none past 15 degrees', () => {
  // Keyframe 4 and 5 are nearer the current axis than any of the first four, and they are not candidates.
  const kfs = [kfAt(0, 0), kfAt(1, 4.5), kfAt(2, 9), kfAt(3, 13.5), kfAt(4, 20), kfAt(5, 24)];
  const cur = kfAt(6, 25);
  assert.deepEqual(closureCandidates([...kfs, cur], cur, 360), [3], 'only 3 (11.5 away); 2 is 16 away, 4 and 5 are nearer but not among the first 4');
  const far = kfAt(6, 40);
  assert.deepEqual(closureCandidates([...kfs, far], far, 360), [], 'nothing within 15 degrees');
  // The limit is in axis degrees: 15 degrees of azimuth at altitude 0 is 15 degrees of axis.
  const k15 = [kfAt(0, 0)];
  assert.deepEqual(closureCandidates([...k15, kfAt(1, 14.9)], kfAt(1, 14.9), 360), [0], '14.9 degrees is within 15');
  assert.deepEqual(closureCandidates([...k15, kfAt(1, 15.2)], kfAt(1, 15.2), 360), [], '15.2 is not');
  // The axis, not the azimuth: at altitude 60 a 20-degree azimuth difference is 10 degrees of axis.
  const hi = [kfAt(0, 0, 60)], cur2 = kfAt(1, 20, 60);
  near(axisSeparationDeg(hi[0].pose, cur2.pose), 10.1, 0.2, 'axis separation at altitude 60');
  assert.deepEqual(closureCandidates([...hi, cur2], cur2, 360), [0]);
  // The current keyframe is never its own candidate, even when it is one of the first four.
  const few = [kfAt(0, 0), kfAt(1, 5)];
  assert.deepEqual(closureCandidates(few, few[1], 360), [0]);
  assert.deepEqual(closureCandidates([], kfAt(0, 0), 360), []);
});

// ---- distributeClosure -----------------------------------------------------------------------------------------------

test('distributeClosure is linear in cumulative angle: the first keyframe takes nothing, the last all, the rest their share', () => {
  const cum = [0, 10, 20, 40], residual: V3 = [4, -8, 2], logGain: [number, number, number] = [0.4, 0.2, -0.1];
  const out = distributeClosure(cum, residual, logGain);
  assert.equal(out.rot.length, 4);
  assert.equal(out.logGain.length, 4);
  const shares = [0, 0.25, 0.5, 1];
  shares.forEach((s, i) => {
    for (let a = 0; a < 3; a++) {
      near(out.rot[i][a], residual[a] * s, 1e-12, `rot[${i}][${a}]`);
      near(out.logGain[i][a], logGain[a] * s, 1e-12, `logGain[${i}][${a}]`);
    }
  });
  for (let a = 0; a < 3; a++) assert.equal(Math.abs(out.rot[0][a]), 0, 'the gauge keyframe is not moved');
  assert.deepEqual(out.rot[3], residual, 'the last keyframe takes the whole residual');
});

test('the share follows the cumulative angle, not the keyframe index: unevenly spaced keyframes', () => {
  // A stall (a 30-degree jump) and a dense stretch: index-linear shares would be 0, 1/3, 2/3, 1.
  const cum = [0, 3, 33, 36], out = distributeClosure(cum, [36, 0, 0], [0, 0, 0]);
  [0, 3, 33, 36].forEach((deg, i) => near(out.rot[i][0], deg, 1e-12, `keyframe ${i}`));
});

test('distributeClosure over a seeded random chain: every share is cum[i] / cum[last], monotone, never NaN', () => {
  let s = 12345;
  const rnd = () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  for (let trial = 0; trial < 50; trial++) {
    const n = 2 + Math.floor(rnd() * 90), cum = [0];
    for (let i = 1; i < n; i++) cum.push(cum[i - 1] + 1 + rnd() * 6);
    const residual: V3 = [rnd() * 20 - 10, rnd() * 20 - 10, rnd() * 20 - 10], lg: [number, number, number] = [rnd() - 0.5, rnd() - 0.5, rnd() - 0.5];
    const out = distributeClosure(cum, residual, lg);
    for (let i = 0; i < n; i++) {
      const share = cum[i] / cum[n - 1];
      for (let a = 0; a < 3; a++) {
        near(out.rot[i][a], residual[a] * share, 1e-12, `trial ${trial} rot`);
        near(out.logGain[i][a], lg[a] * share, 1e-12, `trial ${trial} logGain`);
      }
      if (i > 0) assert.ok(Math.abs(out.rot[i][2]) >= Math.abs(out.rot[i - 1][2]) - 1e-12, 'monotone');
    }
  }
});

test('a chain with no angle to spread over gives zeros, never NaN; a single keyframe and an empty chain are accepted', () => {
  for (const cum of [[0, 0, 0], [0], [5, 0], [Number.NaN, Number.NaN]]) {
    const out = distributeClosure(cum, [1, 2, 3], [0.1, 0.2, 0.3]);
    for (const v of [...out.rot.flat(), ...out.logGain.flat()]) assert.equal(v, 0, `cum ${JSON.stringify(cum)}`);
  }
  assert.deepEqual(distributeClosure([], [1, 2, 3], [0, 0, 0]), { rot: [], logGain: [] });
});

// ---- repeatsAlongYaw -------------------------------------------------------------------------------------------------

/** A level whose columns follow `f(x)`, the same in every row, and `noise` of a hash per pixel. */
function level(w: number, h: number, f: (x: number) => number, noise = 0): { w: number; h: number; px: Uint8Array } {
  const px = new Uint8Array(w * h);
  let s = 7;
  const rnd = () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) px[y * w + x] = Math.max(0, Math.min(255, Math.round(f(x) + noise * (rnd() - 0.5))));
  return { w, h, px };
}

test('repeatsAlongYaw: stripes whose period is inside the closure window repeat, a texture, a gradient and a blank view do not', () => {
  assert.equal(REPEAT_MAX_LAG_PX, 16);
  // 45 x 80 is the L2 level of a 180 x 320 frame. A period of 5 to 12 degrees is 5 to 12.5 pixels at L2.
  for (const period of [4, 5, 6, 8.4, 10, 12.5]) {
    assert.equal(repeatsAlongYaw(level(45, 80, x => (Math.floor(2 * x / period) % 2 ? 60 : 160), 4)), true, `stripes of ${period} px`);
  }
  // Sinusoidal stripes too, and stripes under noise.
  assert.equal(repeatsAlongYaw(level(45, 80, x => 110 + 50 * Math.sin(2 * Math.PI * x / 9), 10)), true, 'a sinusoid');
  // Independent columns (the texture of value noise or foliage) have no lag where they come back.
  let seen = 0;
  for (let seed = 1; seed <= 20; seed++) {
    let s = seed * 99991;
    const col = Array.from({ length: 45 }, () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return 60 + 120 * (s / 4294967296); });
    if (repeatsAlongYaw(level(45, 80, x => col[x], 6))) seen++;
  }
  assert.ok(seen <= 1, `${seen} of 20 random textures were read as repeating`);
  assert.equal(repeatsAlongYaw(level(45, 80, x => 100 + 0.8 * x, 2)), false, 'a smooth gradient never falls below the main lobe');
  assert.equal(repeatsAlongYaw(level(45, 80, () => 120)), false, 'a flat view');
  assert.equal(repeatsAlongYaw(level(45, 80, x => 100 + 40 * Math.sin(x / 7), 2)), false, 'one slow wave is not a repeat');
  assert.equal(repeatsAlongYaw(level(20, 40, x => (x % 4 < 2 ? 60 : 160))), false, 'a level too narrow to hold the window is not judged');
});

console.log(`loop.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exit(1);
