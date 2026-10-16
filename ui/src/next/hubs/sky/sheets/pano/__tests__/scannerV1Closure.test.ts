// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T30: two rulings of wave 4 on the tracker's closure (SPEC-v2 4.8, 4.9, S43, S44), on the rings of closureRig.ts. Split from
// scannerV1.test.ts for the runner's per-file time (a ring is a second or two).
//
//   S43. Past a full turn a keyframe that no image edge places is placed by the gyro from the keyframe committed just before it
//        (the closed chain), not from the early keyframe the live axis has come back to, whose correction knows nothing of the
//        whole turn's gyro error (2 % of 360 is 7 degrees). The ring's start is blank, so the early keyframes are sensor-placed
//        and nothing aligns at the seam, and the pan is slow enough (16 deg/s, under half the 40 deg/s limit) for rule 5 to
//        revisit the amber seam.
//   S44. The closed focal is the root of Theta(f) = T_true - Theta_gyro over the re-solved edges (two secant steps from the 1 / f
//        value of `closeLoop`), not the 1 / f value. Measured on the default ring (T30 report), the error of the closed focal against
//        the 1 / f value: 1.135 -> 0.933 % at a lock 3.38 % off, 0.198 -> 0.165 % at -0.58 %, 0.592 -> 0.501 % at 1.69 %,
//        1.229 -> 1.047 % at 3.49 %. What is left is about 0.28 times the lock's error and the secant cannot reach it: the
//        correspondences are LK's own at the lock's focal, and re-reading them at another is the same rotation field scaled, not
//        the field a re-run of LK would find (the closed focal is better than the lock's by a factor of 3.6, as T28 measured).
//
// Mutants this file must catch (each applied from a byte backup of tracker.ts and run under the normal command):
//   * `wrapped` false for every link (S43 off): 'past a full turn' finds a keyframe 2.2 degrees off its predecessor's error;
//   * the secant not taken (`refined` null): 'the closed focal is the root' finds the 1 / f errors, over the bounds.
import assert from 'node:assert/strict';
import { SCENE, run, yawError } from './synth/closureRig';
import type { SynthScene } from './synth/synth';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** The default scene with a blank sector (uniform grey: no skyline, no stripes, nothing to align on) within `half` of azimuth 0. */
function blankStart(half: number): SynthScene {
  return {
    horizonAlt: SCENE.horizonAlt,
    sample(az, alt) {
      const a = ((az % 360) + 360) % 360;
      return Math.min(a, 360 - a) > half ? SCENE.sample(az, alt) : [120, 120, 120];
    },
  };
}

test('past a full turn a keyframe that no image edge places continues the chain: its yaw error is its predecessor\'s, not a whole turn of gyro error away (S43)', () => {
  const r = run({ scene: blankStart(25), pan: { turnDeg: 395, speedDegS: 16 } });
  const tracker = r.tracker as unknown as { links: { edge: unknown }[]; unwrappedAt: number[] };
  const kfs = r.tracker.keyframes;
  const wrapped = kfs.map((_, i) => i).filter(i => Math.abs(tracker.unwrappedAt[i] - tracker.unwrappedAt[0]) >= 360);
  assert.ok(wrapped.length >= 2, `${wrapped.length} keyframes past a full turn (${wrapped.join(',')})`);
  for (const i of wrapped) assert.equal(tracker.links[i].edge, null, `keyframe ${i} is gyro-placed (the start is blank)`);
  // The early keyframes sit at the gyro's yaw, which has no error yet: a keyframe past 360 placed from them would be off by the
  // whole turn's 5 to 8 degrees from the chain it follows, 2.2 of them at the first one.
  const errs = kfs.map(k => yawError(r, k.pose, k));
  const base = errs[0];
  let jump = 0;
  for (const i of wrapped) jump = Math.max(jump, Math.abs(errs[i] - errs[i - 1]));
  assert.ok(Math.abs(errs[wrapped[0] - 1] - base) > 3, `the chain has the gyro's whole-turn error by the seam: ${errs[wrapped[0] - 1].toFixed(2)}`);
  assert.ok(jump < 0.8, `the largest step in yaw error across the keyframes past 360 is ${jump.toFixed(2)} degrees`);
});

test('the closed focal is the root of Theta(f) = T_true - Theta_gyro over the re-solved edges: closer than the 1 / f value at every lock error (S44)', () => {
  // [prior scale, the lock's error in %, the bound on the closed focal's error in %, and the 1 / f value's error for the record].
  for (const [prior, lockPct, bound, formulaPct] of [[1.0, -3.38, 1.0, 1.135], [1.3, 3.49, 1.12, 1.229]] as const) {
    const r = run({ priorScale: prior });
    assert.equal(r.focal.state, 'closed');
    const lock = (r.focal.fMeasure / r.fTrue - 1) * 100, closed = Math.abs(r.focal.fBest / r.fTrue - 1) * 100;
    assert.ok(Math.abs(lock - lockPct) < 0.05, `the lock's error ${lock.toFixed(2)} % (expected ${lockPct})`);
    assert.ok(closed < bound, `prior ${prior}: closed focal ${closed.toFixed(3)} % off, the 1 / f value was ${formulaPct} % (bound ${bound})`);
    assert.ok(closed < Math.abs(lock) * 0.35, `a good part of the lock's error is gone: ${closed.toFixed(3)} against ${Math.abs(lock).toFixed(2)}`);
  }
});

console.log(`scannerV1Closure.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
