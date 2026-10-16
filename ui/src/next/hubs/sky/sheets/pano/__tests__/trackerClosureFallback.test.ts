// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T28: the loop closure that does not match, and closes by gyro instead (SPEC-v2 4.9 Fallback, 4.8, ruling S19). The closure
// that matches is graded by trackerClosure.test.ts. The rig is synth/closureRig.ts.
//
// Three rings:
//   * `noMatch`: the ring starts and ends in a blank sector, so the early keyframes have nothing to match; the focal is locked
//     from the start. Every keyframe from 350 degrees on retries and fails, and the ring closes by gyro at the first keyframe
//     past 381.5 degrees, with the locked focal kept;
//   * `range`: a ring that would match, whose focal throws RangeError from `closeLoop`: the ring closes by gyro, the locked focal
//     kept, nothing corrected and nothing repainted, never an aborted closure (S19, the brief);
//   * `unlocked`: a focal that never locks: `closeLoop` is never called (it would throw, and S19 says it is not called), and the
//     ring closes by gyro as soon as the closure is due.
//
// Mutants this file must catch: the retry limit moved (`CLOSURE_RETRY_MAX_DEG` raised: 'noMatch' closes later or never; lowered:
// a ring that would still match closes by gyro, trackerClosure.test.ts); `closeLoop` called on an unlocked focal ('unlocked'
// finds a call); a RangeError rethrown or treated as an aborted closure ('range' finds an exception or a ring still open); the
// RangeError path applying the correction anyway ('range' finds the poses moved and the raster cleared).
import assert from 'node:assert/strict';
import { PRIOR_SD_PCT } from '../focal';
import { makeSynthScene } from './synth/synth';
import { CLOSURE_MIN_DEG, CLOSURE_RETRY_MAX_DEG } from '../loop';
import { NeverLocks, SG, SpyFocal, lockedAt, run } from './synth/closureRig';
import type { Run } from './synth/closureRig';

let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

/** A SpyFocal whose closure throws the RangeError a degenerate ring gives (focal.ts), leaving its state as it was. */
class ThrowsRange extends SpyFocal {
  override closeLoop(thetaImgDeg: number, thetaGyroDeg: number, trueDeg: number): { fNorm: number; gyroScale: number } {
    this.calls.push({ state: this.state, args: [thetaImgDeg, thetaGyroDeg, trueDeg], threw: 'RangeError' });
    throw new RangeError('closeLoop is not a ring');
  }
}

const locked = (cls: typeof SpyFocal) => (p: number) => lockedAt(new cls({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT }));
const level = { priorScale: 1 / (1 + SG), pan: { pitchDeg: 0 } };

const noMatch = run({ ...level, scene: makeSynthScene({ seed: 3, blankSectors: [[-40, 40]] }), makeFocal: locked(SpyFocal) });
const range = run({ ...level, makeFocal: locked(ThrowsRange) });
const unlocked = run({ priorScale: 1.1, pan: { pitchDeg: 0 }, makeFocal: p => new NeverLocks({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT }) });

/** The unwrapped yaw of each keyframe relative to keyframe 0, which the tracker keeps privately: the cases pin the retry limit. */
const unwrappedOf = (r: Run) => {
  const u = (r.tracker as unknown as { unwrappedAt: number[] }).unwrappedAt;
  return u.map(x => x - u[0]);
};
const closureGyroKf = (r: Run) => r.tracker.log.find(x => x.detail === 'closure-gyro');

test('no match: every keyframe from 350 degrees retries, and the ring closes by gyro at the first keyframe past 381.5', () => {
  const loop = noMatch.tracker.loop, rec = closureGyroKf(noMatch);
  assert.ok(rec, 'a closure-gyro record');
  assert.equal(loop.closed, true);
  assert.equal(loop.method, 'gyro');
  assert.equal(loop.preDeg, null, 'no correction was measured');
  assert.equal(loop.postDeg, null);
  assert.equal(loop.match, null);
  const u = unwrappedOf(noMatch), at = rec?.kf as number;
  assert.ok(u[at] > CLOSURE_RETRY_MAX_DEG, `the closing keyframe is at ${u[at]}, past ${CLOSURE_RETRY_MAX_DEG}`);
  // Every keyframe before it that was due for a try (350 to 381.5, non-revisit) did not close the ring.
  const tried = u.slice(0, at).filter(x => x >= CLOSURE_MIN_DEG);
  assert.ok(tried.length >= 1, `${tried.length} keyframes were due for a try before the limit`);
  assert.ok(tried.every(x => x <= CLOSURE_RETRY_MAX_DEG), 'none of them past the limit');
  assert.equal(noMatch.tracker.log.filter(x => x.detail === 'closure-gyro').length, 1, 'closed once');
  assert.equal(noMatch.tracker.log.filter(x => x.detail === 'closure').length, 0);
  assert.equal(noMatch.tracker.edges.filter(e => e.kind === 'closure').length, 0, 'no closure edge');
});

test('the gyro closure leaves the locked focal as it is and corrects nothing: no call to closeLoop, no repaint, no moved pose', () => {
  const spy = noMatch.focal as SpyFocal;
  assert.equal(spy.calls.length, 0, 'closeLoop is not called without a match');
  assert.equal(noMatch.focal.state, 'locked');
  assert.equal(noMatch.focal.fBest, noMatch.focal.fMeasure, 'the locked focal is kept');
  assert.equal(noMatch.clears, 0, 'the raster is not cleared: nothing is repainted');
  assert.equal(noMatch.closedAt?.pending, 0);
  for (const k of noMatch.tracker.keyframes) assert.deepEqual(k.pose, noMatch.firstPose[k.id], `keyframe ${k.id} was not moved`);
  // A gyro closure measures no scale: the placement sigmas keep sg = 0.02 (4.7), so an aligned keyframe is still 0.05 sqrt(s).
  const k = noMatch.tracker.keyframes.filter(x => x.cls === 'aligned').pop();
  assert.ok(k && Math.abs(k.sigmaDeg - 0.05 * Math.sqrt(Math.max(1, k.id))) < 1e-9, `aligned sigma ${k?.sigmaDeg}`);
});

test('a RangeError from closeLoop on a locked focal ends in closure-gyro with the locked focal kept, never in an aborted closure (S19)', () => {
  const spy = range.focal as ThrowsRange, loop = range.tracker.loop;
  assert.equal(spy.calls.length, 1, 'closeLoop was tried once');
  assert.equal(spy.calls[0].state, 'locked', 'on a locked focal');
  assert.equal(spy.calls[0].threw, 'RangeError');
  assert.equal(loop.closed, true, 'the ring is closed, not left open');
  assert.equal(loop.method, 'gyro');
  assert.equal(loop.match, null);
  assert.equal(loop.preDeg, null);
  const rec = closureGyroKf(range);
  assert.ok(rec, 'a closure-gyro record');
  assert.ok(Math.abs(range.tracker.loop.unwrappedDeg) >= CLOSURE_MIN_DEG);
  assert.equal(range.tracker.log.filter(x => x.detail === 'closure').length, 0);
  // The match was found, so this closed at the first try, well before the retry limit.
  assert.ok(unwrappedOf(range)[rec?.kf as number] <= CLOSURE_RETRY_MAX_DEG, 'at the first try, not at the limit');
  assert.equal(range.focal.state, 'locked', 'the focal stays locked');
  assert.equal(range.focal.fBest, range.focal.fMeasure, 'and keeps its value');
  assert.equal(range.clears, 0, 'nothing repainted');
  for (const k of range.tracker.keyframes) assert.deepEqual(k.pose, range.firstPose[k.id], `keyframe ${k.id} was not moved`);
  assert.equal(range.tracker.edges.filter(e => e.kind === 'closure').length, 0, 'the match is not kept as an edge');
});

test('a ring that never locked closes by gyro as soon as it is due, and closeLoop is never called on it (S19)', () => {
  const spy = unlocked.focal as NeverLocks, loop = unlocked.tracker.loop, rec = closureGyroKf(unlocked);
  assert.equal(unlocked.focal.state, 'prior');
  assert.equal(spy.calls.length, 0, 'closeLoop is not called before the lock');
  assert.equal(loop.closed, true);
  assert.equal(loop.method, 'gyro');
  assert.ok(rec, 'a closure-gyro record');
  const at = unwrappedOf(unlocked)[rec?.kf as number];
  assert.ok(at >= CLOSURE_MIN_DEG && at <= CLOSURE_RETRY_MAX_DEG, `closed at ${at}: when the closure was due, not at the limit`);
  assert.equal(unlocked.focal.sdPct, 20, 'the prior sd is untouched');
  assert.equal(unlocked.clears, 0);
});

console.log(`trackerClosureFallback.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exit(1);
