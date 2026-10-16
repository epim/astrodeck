// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T28: the tracker's stage 3, the loop closure that matches (SPEC-v2 4.8, 4.9, 4.10, 7.2's loop and closure row). The
// fallbacks, the closure refused and the repeated-texture ring are in trackerClosureFallback.test.ts; the pure parts in
// loop.test.ts. The rig (synth/closureRig.ts) runs the tracker as the scanner does, over a synthetic ring with 2 deg/min
// drift and a 2 % gyro scale error, frames carrying the simulator's sensor noise (S37).
//
// Four rings, each about a second:
//   * `real`, at the ring pitch of 23 degrees with a focal prior 10 % long and the real lock (T27): the whole path, and what the
//     closure leaves after a lock that is itself good to a percent;
//   * `level`, at pitch 0 with the focal locked at f_true / (1 + s_g) before the first frame, so that every step is image-led in
//     the gyro's degrees as 4.9's table assumes: the residual before the correction and the accuracy after it;
//   * `held`, the same ring with a focal whose closure leaves the focal alone, so that the linear distribution alone has to
//     remove the 6-degree residual: the mid-ring case; the camera stalls for 35 degrees early in it, so that the keyframes are not
//     evenly spaced and the share follows the cumulative angle and not the index;
//   * `gapped`, a ring with a 100-degree blank sector in the middle, where the gyro places the keyframes.
//
// Mutants this file must catch (SPEC-v2 7.2 and the T28 brief), each applied to tracker.ts from a byte backup and turning the
// cases named red (the first failing line of each is in the T28 report):
//   * the residual applied to the last keyframe only (`share` 0 but for the last, in `closeByImage`): 'the linear distribution
//     removes a residual the focal leaves alone' finds the mid-ring keyframe 3 degrees off;
//   * the closure focal not applied (`closeLoop` not called): 'the closure's focal is within 0.3 % of the truth' finds the
//     focal still the lock's and 'closeLoop is called once, on a locked focal' finds no call.
// Further mutants, each against a case of its own, each applied from a byte backup and found red: the edges not re-solved at the
// closed focal ('every edge is expressed at the closed focal': the mean ratio to the true angle 1.017, not 1.00); the closure's
// sigmas left at sg = 0.02 ('a ring with a gyro-led sector closes too': sensor sigmas up to 1.23, not under 1.05); the repaint not
// scheduled ('the ring is repainted through pump': nothing pending at the closing commit); the share by keyframe index instead of
// cumulative angle ('the linear distribution removes a residual the focal leaves alone': the stalled ring's worst error 0.39, not
// under 0.3). Under the same names in trackerClosureFallback.test.ts: `closeLoop` called on an unlocked focal, a RangeError
// rethrown, the retry limit raised; in trackerClosureRepeat.test.ts: the repeat test removed.
import assert from 'node:assert/strict';
import { FocalEstimator, PRIOR_SD_PCT } from '../focal';
import { DEG } from '../../photosphereGeometry';
import { axisSeparationDeg, headingDeg, qinv, qmul } from '../rotation';
import { CLOSURE_MIN_DEG, CLOSURE_RETRY_MAX_DEG } from '../loop';
import type { Keyframe, Quat } from '../types';
import { makeSynthScene } from './synth/synth';
import { NoRefocal, SG, SpyFocal, lockedAt, run, worstYawError, wrap180, yawError } from './synth/closureRig';
import type { Run } from './synth/closureRig';

// A failing case is reported and the file carries on, so a mutant shows every case it turns red (the pattern of align.test.ts).
let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}
const near = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} is not within ${tol} of ${b}`);
const spyOf = (r: Run) => r.focal as SpyFocal;

// ---- The rings --------------------------------------------------------------------------------------------------------

/** An exposure that drifts 0.2 to 0.4 % a keyframe, per channel, as an auto-exposure does (T27's). */
const EXPOSURE = (n: number): [number, number, number] => [1 + 0.004 * n, 1 + 0.003 * n, 1 + 0.002 * n];
const real = run({ priorScale: 1.1, frameGain: EXPOSURE, makeFocal: p => new SpyFocal({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT }) });
const level = run({ priorScale: 1 / (1 + SG), pan: { pitchDeg: 0 }, makeFocal: p => lockedAt(new SpyFocal({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT })) });
/** The camera stalls for 35 degrees of the turn, from azimuth 60 to 95: the sensors keep running and the tracker sees the axis jump,
 *  so the keyframes are not evenly spaced and a share by keyframe index differs from a share by angle. */
const stalled = (_frame: number, q: Quat) => { const h = headingDeg(q); return h > 60 && h < 95; };
const held = run({ priorScale: 1 / (1 + SG), pan: { pitchDeg: 0 }, skip: stalled, makeFocal: p => lockedAt(new NoRefocal({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT })) });

/** A 100-degree blank sector in the middle of the ring, where the gyro places the keyframes: about a fifth of the turn is gyro-led. */
const gapped = run({
  scene: makeSynthScene({ seed: 3, blankSectors: [[150, 250]] }), priorScale: 1 / (1 + SG), pan: { pitchDeg: 0 },
  makeFocal: p => lockedAt(new SpyFocal({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT })),
});

/** The keyframe that closed the ring. Keyframes after it (revisits of the seam, while the pan finishes its 385 degrees) follow it. */
const lastOf = (r: Run) => r.tracker.keyframes[(r.closedAt as NonNullable<Run['closedAt']>).kf];
const headingTurn = (r: Run, kf: Keyframe) => wrap180(headingDeg(r.truth(kf)) - headingDeg(r.truth(r.tracker.keyframes[0])));

// ---- 1. The ring closes by image --------------------------------------------------------------------------------------

test('a ring closes by image: the match is one of the first 4 keyframes, the closing keyframe is the newest, and the state says so', () => {
  const loop = real.tracker.loop, kfs = real.tracker.keyframes;
  assert.ok(real.lock, 'the focal locked on the way');
  assert.equal(loop.closed, true);
  assert.equal(loop.method, 'image');
  const m = loop.match as { early: number; late: number };
  assert.ok(m.early >= 0 && m.early < 4, `early ${m.early} is among the first 4`);
  assert.equal(m.late, real.closedAt?.kf, 'the late end is the keyframe that closed the ring');
  assert.ok(m.late >= 70 && m.late <= kfs.length - 1, `closed at keyframe ${m.late} of ${kfs.length}`);
  assert.ok(axisSeparationDeg(kfs[m.early].pose, kfs[m.late].pose) <= 15, 'the match is within 15 degrees of axis');
  // A turn of 350 to 381.5 degrees: the trigger and the retry limit of 4.9.
  assert.ok(Math.abs(loop.unwrappedDeg) >= CLOSURE_MIN_DEG && Math.abs(loop.unwrappedDeg) <= CLOSURE_RETRY_MAX_DEG, `unwrappedDeg ${loop.unwrappedDeg}`);
  assert.ok((loop.preDeg as number[]).every(Number.isFinite) && (loop.preDeg as number[]).length === 3);
  assert.ok((loop.postDeg as number) < 1e-6, `postDeg ${loop.postDeg}: the newest keyframe sits on the match`);
  // The capture log says so once, on the closing keyframe; the edge is the only closure edge, from the early to the late one.
  const logged = real.tracker.log.filter(x => x.detail === 'closure');
  assert.equal(logged.length, 1);
  assert.equal(logged[0].kf, m.late);
  assert.equal(real.tracker.log.filter(x => x.detail === 'closure-gyro').length, 0);
  const edges = real.tracker.edges.filter(e => e.kind === 'closure');
  assert.equal(edges.length, 1);
  assert.deepEqual([edges[0].a, edges[0].b], [m.early, m.late]);
  assert.ok(edges[0].corr.length >= 8 && edges[0].zncc >= 0.8, `${edges[0].corr.length} correspondences, ZNCC ${edges[0].zncc}`);
  assert.equal(real.closedAt?.kf, m.late);
});

test('closeLoop is called once, on a locked focal, and the closed focal is within 0.3 % of the truth (the closure\'s focal)', () => {
  for (const r of [real, level]) {
    const spy = spyOf(r);
    assert.equal(spy.calls.length, 1, 'closeLoop is called once');
    assert.equal(spy.calls[0].state, 'locked', 'and only on a locked focal (S19)');
    assert.equal(r.focal.state, 'closed');
    assert.equal(r.focal.sdPct, 0.139);
    const [thetaImg, thetaGyro, trueDeg] = spy.calls[0].args;
    assert.ok(thetaImg > 300 && thetaImg < 380, `Theta_img ${thetaImg}: the image-led steps are most of the ring`);
    assert.ok(thetaGyro >= 0 && thetaGyro < 30, `Theta_gyro ${thetaGyro}`);
    assert.ok(trueDeg > 330 && trueDeg < 375, `T_true ${trueDeg}`);
    near(r.focal.fBest / r.fTrue, 1, 0.003, 'the closed focal against the truth');
    assert.ok(Number.isFinite(spy.calls[0].result?.gyroScale), 'the gyro scale is reported');
  }
  // The lock left the focal further off than the closure did: the closure moved it toward the truth.
  const lockedF = real.focal.fMeasure;
  assert.ok(Math.abs(real.focal.fBest / real.fTrue - 1) < Math.abs(lockedF / real.fTrue - 1) + 1e-9, 'closer than the lock, or as close');
  assert.notEqual(real.focal.fBest, lockedF, 'the closed focal differs from the measure focal');
});

test('the ring is repainted through pump: every keyframe pending at the closing commit, the raster cleared once more, drained by the end', () => {
  const c = real.closedAt as NonNullable<Run['closedAt']>;
  assert.equal(c.pending, c.kf + 1, 'every keyframe so far is queued for the repaint, the closing one included');
  assert.equal(c.clears, 2, 'the raster was cleared for the lock and again for the closure');
  assert.equal(real.clears, 2);
  assert.equal(real.tracker.pendingSlices, 0, 'drained');
  assert.ok(real.tracker.coveredDeg >= 300, `${real.tracker.coveredDeg} degrees painted after the repaint`);   // the band of coverage, -2 to 10 degrees: T27's bound
  assert.equal(real.tracker.gapDeg, null, 'no gap in the closed ring');
  assert.equal(level.closedAt?.pending, (level.closedAt?.kf as number) + 1);
  assert.equal(level.clears, 1, 'a ring locked from the start clears once, for the closure');
});

// ---- 2. Accuracy ---------------------------------------------------------------------------------------------------------

test('the residual before the correction is the chain\'s error at the end: loop.preDeg is within 0.3 degrees of the truth', () => {
  // The closing keyframe is first painted at its fused pose, before the closure corrects anything: the error of that pose against
  // the truth is the residual the closure has to remove. The match measures it to the accuracy of one alignment.
  for (const r of [level, real]) {
    const kf = lastOf(r), pre = r.tracker.loop.preDeg as number[];
    const chainError = yawError(r, r.firstPose[kf.id], kf);
    near(pre[2], chainError, 0.3, `preDeg about the vertical, ${r === level ? 'level' : 'real'} ring`);
    assert.ok(Math.abs(pre[0]) < 0.5 && Math.abs(pre[1]) < 0.5, `the tilt components ${pre[0]}, ${pre[1]} are small`);
  }
});

test('on the level ring the residual is the gyro\'s: 360 s_g plus drift, as 4.9 tabulates, less the 0.4 % that LK reads short', () => {
  // 4.9: 2 % of the turn plus 2 deg/min over the ring's 17 s. The edges read each step 0.2 to 0.4 % short (T21's LK; the step
  // ratios against truth are 0.996 to 0.999), which is 1.2 degrees over the ring and the whole of the difference from 7.5.
  const kf = lastOf(level), turn = 360 + headingTurn(level, kf), seconds = (kf.t - level.tracker.keyframes[0].t) / 1000;
  const expected = turn * SG + 2 / 60 * seconds, pre = (level.tracker.loop.preDeg as number[])[2];
  assert.ok(pre > 0, 'the chain is ahead of the truth (the gyro scale error is positive)');
  near(pre, expected, 1.6, `the residual against 360 s_g + drift (${expected.toFixed(2)})`);
  assert.ok(pre < expected, `LK reads short, so the residual is under the table's ${expected.toFixed(2)}, not over it`);
});

test('after the correction every keyframe is within 0.1 degrees of the truth on the level ring (0.6 at the 23-degree ring pitch)', () => {
  // The linear distribution and the closed focal leave the chain at the accuracy of its alignments: a bridge of 0.037 degrees on
  // the level ring. At pitch 23 the pre-lock edges were measured at the 10 % wrong prior and re-solved from their correspondences
  // (T27), which leaves up to 0.4 degrees in the first eight keyframes that the closure cannot see.
  assert.ok(worstYawError(level) < 0.1, `level ring: worst yaw error ${worstYawError(level)}`);
  assert.ok(worstYawError(real) < 0.6, `ring pitch: worst yaw error ${worstYawError(real)}`);
  // Before the closure the same ring was off by the whole residual: 6 degrees at the end.
  const kf = lastOf(level);
  assert.ok(Math.abs(yawError(level, level.firstPose[kf.id], kf)) > 4, 'the correction had something to remove');
});

test('the linear distribution removes a residual the focal leaves alone: a ring whose closure keeps the focal, mid-ring error under 0.3', () => {
  const kfs = held.tracker.keyframes, pre = (held.tracker.loop.preDeg as number[])[2];
  assert.equal(spyOf(held).calls.length, 1);
  assert.equal(held.focal.fBest, held.focal.fMeasure, 'the closure left the focal where the lock put it');
  assert.ok(pre > 4, `the residual to distribute is ${pre} degrees`);
  // A scale error is linear along the chain: half the residual is at mid-ring. Applied to the last keyframe alone, the middle one
  // would keep 3 degrees.
  const mid = kfs[kfs.length >> 1];
  assert.ok(Math.abs(yawError(held, mid.pose, mid)) < 0.3, `mid-ring keyframe ${mid.id}: ${yawError(held, mid.pose, mid)}`);
  const quarter = kfs[kfs.length >> 2];
  assert.ok(Math.abs(yawError(held, quarter.pose, quarter)) < 0.3, `quarter-ring keyframe ${quarter.id}: ${yawError(held, quarter.pose, quarter)}`);
  // A share by keyframe index instead of cumulative angle leaves 0.39 here: the stall makes the spacing uneven.
  assert.ok(worstYawError(held) < 0.3, `worst yaw error ${worstYawError(held)}`);
  // Keyframe 0 is the gauge and does not move.
  assert.equal(yawError(held, kfs[0].pose, kfs[0]), 0);
});

test('every edge is expressed at the closed focal: a chain step reads the true angle to 0.6 %, where at the lock it read 2 % long', () => {
  // The level ring is locked at f_true / (1 + s_g), so before the closure every edge is in the gyro's degrees, 2 % over the truth.
  // The closure re-solves them from their correspondences at the closed focal and the chain is re-fused: the rest is LK's 0.2 to 0.4 %.
  const angle = (q: Quat) => 2 * Math.acos(Math.min(1, Math.abs(q[0]))) / DEG, kfs = level.tracker.keyframes;
  let sum = 0, n = 0;
  for (const e of level.tracker.edges) {
    if (e.kind !== 'chain') continue;
    const truth = angle(qmul(qinv(level.truth(kfs[e.b])), level.truth(kfs[e.a])));
    if (truth > 1) { sum += angle(e.qBA) / truth; n++; }
  }
  assert.ok(n > 70, `${n} chain edges`);
  near(sum / n, 1, 0.006, 'the mean ratio of an edge angle to the true relative rotation');
  assert.ok(sum / n < 1.002, `the mean ratio ${sum / n}`);
});

test('a ring with a gyro-led sector closes too: Theta_gyro is counted, the focal bias is the g s_g / (1 - g) of 4.8, the sigmas use sg = 0.00139', () => {
  // The closure takes the gyro-led part as true degrees, which biases the focal by g s_g / (1 - g) for a gyro-led fraction g: a
  // fifth of the ring is 0.6 %. The sensor keyframes in the blank sector are at most 50 degrees from an aligned anchor, where sg =
  // 0.02 alone would add 1.0 degree to their sigma (1.4 in all); with 0.00139 it adds 0.07 and the sigma is 0.95.
  const spy = gapped.focal as SpyFocal, [thetaImg, thetaGyro, trueDeg] = spy.calls[0].args;
  assert.equal(gapped.tracker.loop.method, 'image');
  assert.equal(spy.calls.length, 1);
  assert.ok(thetaGyro > 60 && thetaGyro < 100, `Theta_gyro ${thetaGyro}: the blank sector's keyframes are gyro-led`);
  assert.ok(thetaImg + thetaGyro > trueDeg, 'the chain is in the gyro degrees, over the truth');
  const g = thetaGyro / trueDeg;
  near(gapped.focal.fBest / gapped.fTrue - 1, g * SG / (1 - g), 0.002, `the focal's bias against g s_g / (1 - g) (g = ${g.toFixed(3)})`);
  assert.ok(worstYawError(gapped) < 1.5, `worst yaw error ${worstYawError(gapped)}: the gyro-led part keeps its 2 %`);
  const sensors = gapped.tracker.keyframes.filter(k => k.cls === 'sensor' && k.id > 0 && k.id < (gapped.closedAt as NonNullable<Run['closedAt']>).kf);
  assert.ok(sensors.length >= 10, `${sensors.length} sensor keyframes in the blank sector`);
  assert.ok(Math.max(...sensors.map(k => k.sigmaDeg)) < 1.05, `sensor sigmas up to ${Math.max(...sensors.map(k => k.sigmaDeg))}`);
});

// ---- 3. What the closure does to the rest of the state ------------------------------------------------------------------

test('after an image closure the aligned keyframes are as far from the gauge as the nearer end of the ring, with sg = 0.00139 (4.7)', () => {
  const kfs = real.tracker.keyframes, K = lastOf(real).id;
  let checked = 0;
  for (const k of kfs) {
    if (k.cls !== 'aligned' || k.id > K) continue;
    near(k.sigmaDeg, 0.05 * Math.sqrt(Math.max(1, Math.min(k.id, K - k.id))), 1e-9, `keyframe ${k.id}`);
    checked++;
  }
  assert.ok(checked >= 60, `${checked} aligned keyframes`);
  // The middle of the ring is the farthest from either end: 0.05 sqrt(K / 2), where before the closure it was 0.05 sqrt(K / 2)
  // too, but the last keyframe was 0.05 sqrt(K) = 0.43 and is now 0.05.
  assert.ok(Math.max(...kfs.filter(k => k.cls === 'aligned' && k.id <= K).map(k => k.sigmaDeg)) <= 0.05 * Math.sqrt(K / 2) + 1e-9);
  assert.ok(lastOf(real).sigmaDeg <= 0.05 + 1e-9, `the closing keyframe is one step from the gauge: ${lastOf(real).sigmaDeg}`);
});

test('the chained gains survive the closure: gain x exposure stays level around the ring, and the closing keyframe agrees with the first', () => {
  const kfs = real.tracker.keyframes;
  let worst = 0;
  for (let n = 0; n < kfs.length; n++) {
    if (real.tracker.log.some(x => x.kf === n && x.outcome === 'revisit')) continue;   // a revisit is a later readback of the seam
    const e = EXPOSURE(n), g = kfs[n].gainRGB;
    for (let c = 0; c < 3; c++) worst = Math.max(worst, Math.abs(g[c] * e[c] - 1));
  }
  assert.ok(worst < 0.03, `gain x exposure off by ${worst}`);
  const last = lastOf(real), n = kfs.length - 1;
  for (let c = 0; c < 3; c++) near(last.gainRGB[c] * EXPOSURE(n)[c], kfs[0].gainRGB[c] * EXPOSURE(0)[c], 0.03, `channel ${c}: the closing keyframe against the first`);
});

test('correctionYawAt follows the corrected chain: the yaw of C at each keyframe is its pose\'s heading minus its prediction\'s', () => {
  const kfs = real.tracker.keyframes;
  let prev = 0;
  for (const k of kfs) {
    const raw = wrap180(headingDeg(k.pose) - headingDeg(k.pred)), c = prev + wrap180(raw - prev);
    near(real.tracker.correctionYawAt(k.t), c, 1e-9, `keyframe ${k.id}`);
    prev = c;
  }
});

test('a later keyframe after the closure is placed against the closed focal and the ring stays closed', () => {
  // The ring ran to 385 degrees: the keyframes after the closing one (revisits of the seam) are aligned at the measure focal and
  // re-solved at the closed one. The loop state is not reopened or rewritten.
  const after = real.tracker.keyframes.filter(k => k.id > (real.closedAt as NonNullable<Run['closedAt']>).kf);
  assert.equal(real.tracker.loop.closed, true);
  assert.equal(real.tracker.log.filter(x => x.detail === 'closure').length, 1, 'one closure only');
  for (const k of after) assert.ok(Number.isFinite(k.sigmaDeg));
  assert.ok(real.focal instanceof FocalEstimator);
});

console.log(`trackerClosure.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exit(1);
