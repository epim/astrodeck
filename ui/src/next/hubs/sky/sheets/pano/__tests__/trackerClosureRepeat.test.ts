// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T28: the loop closure on a repeated structure, the backyard fence (SPEC-v2 4.6, 4.9, rulings S4 and S36). The rig is
// synth/closureRig.ts: a ring at pitch 0 whose start and end lie in a striped fence 80 degrees wide that fills the whole view,
// so the early keyframes see nothing but stripes of one period, with the focal locked from the start.
//
// What this grades, and what it measured over fences of 3, 4, 5, 6, 8, 10, 12, 13, 14, 15 and 16 degrees (the numbers are in the
// T28 report):
//   * S4 asks for the closure's false-accept rate on a repeated-structure ring. Without the repeat test, the closure window (ZNCC
//     0.997, PSR 8 to 14) accepted a wrong match at 8 of the 11 periods, all but 13, 14 and 16: closure edges 3 to 15 degrees off
//     the truth, the whole ring closed on a wrong turn, the focal closed 1 to 4.7 % off. With it: 0 of 11 false, the fences of 13,
//     14 and 16 degrees closed on the truth (edge error 0.06, 0.01 and 0.33 degrees: their other teeth lie past alignPair's
//     12-degree acceptance), and the other eight closed nothing by image;
//   * S36 asks that a candidate one texture period away be refused by the gyro gate. That gate is in the tracker (3 sigma of the
//     gyro's uncertainty including its scale error) and does not refuse these: 3 sigma is 21.6 degrees, wider than the window,
//     and the prior on the gyro scale favours the wrong tooth as often as the right one (the T28 report works the numbers).
//     What does refuse them is the test on the view itself (`repeatsAlongYaw`, loop.ts): a view that repeats along yaw inside the
//     window is not tried, and the ring waits for the limit, which is safe.
//
// Mutants this file must catch: the repeat test removed from `matchClosure` (the 8, 10 and 12-degree rings then close by image on
// the wrong tooth: the refusal case fails); the repeat test refusing every view (the 14-degree ring, which closes on the truth,
// fails).
import assert from 'node:assert/strict';
import { PRIOR_SD_PCT } from '../focal';
import { angleBetweenDeg, qinv, qmul } from '../rotation';
import { SG, SpyFocal, fenceAtStart, lockedAt, run, yawError } from './synth/closureRig';
import type { Run } from './synth/closureRig';

let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

/** A ring through a fence of `periodDeg`, the focal locked at f_true / (1 + s_g) from the first frame. */
const fenceRing = (periodDeg: number) => run({
  scene: fenceAtStart(periodDeg, 40), priorScale: 1 / (1 + SG), pan: { pitchDeg: 0 },
  makeFocal: p => lockedAt(new SpyFocal({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT })),
});
/** How far the closure edge is from the truth, degrees: the angle between its rotation and the true one. NaN with no closure. */
function closureError(r: Run): number {
  const e = r.tracker.edges.find(x => x.kind === 'closure'), kfs = r.tracker.keyframes;
  return e ? angleBetweenDeg(e.qBA, qmul(qinv(r.truth(kfs[e.b])), r.truth(kfs[e.a]))) : NaN;
}

test('a view that repeats along yaw inside the window closes nothing: fences of 8, 10 and 12 degrees (a one-period lock is refused)', () => {
  for (const period of [8, 10, 12]) {
    const r = fenceRing(period), what = `period ${period}`;
    assert.equal(r.tracker.edges.filter(e => e.kind === 'closure').length, 0, `${what}: no closure edge`);
    assert.notEqual(r.tracker.loop.method, 'image', `${what}: not closed by image`);
    assert.equal(r.focal.state, 'locked', `${what}: the locked focal is kept`);
    assert.equal((r.focal as SpyFocal).calls.length, 0, `${what}: closeLoop is not called on a refused match`);
    assert.equal(r.tracker.log.filter(x => x.detail === 'closure').length, 0, what);
  }
});

test('a fence whose period is past the window closes on the truth: 14 degrees, the closure edge within 0.1 degrees and the focal within 0.15 %', () => {
  // The other teeth are 14 degrees away, past alignPair's 12-degree acceptance of a residual, so the only match is the true one.
  // The repeat test reads 0.32 at this period (the lag of 14.6 px is at the end of its range), so it does not refuse a good match.
  const r = fenceRing(14);
  assert.equal(r.tracker.loop.method, 'image');
  assert.ok(closureError(r) < 0.1, `the closure edge is ${closureError(r)} degrees from the truth`);
  // 0.1 % until S44: the focal is now the root of the re-solved edges' sum (T30), which carries the 0.12 % that LK reads short at the
  // true focal (S45) where the 1 / f value of closeLoop happened to cancel it on this ring; 0.121 % measured.
  assert.ok(Math.abs(r.focal.fBest / r.fTrue - 1) < 0.0015, `the closed focal is ${(r.focal.fBest / r.fTrue - 1) * 100} % from the truth`);
  // Through the fence the gyro places the keyframes the stripes would not align, and the closure corrects them to within a degree
  // all round the ring but at the seam keyframes that follow it (a revisit refused its match is placed by the gyro from the nearest
  // early keyframe: the T28 report's finding).
  const closing = (r.closedAt as NonNullable<Run['closedAt']>).kf;
  const worst = Math.max(...r.tracker.keyframes.filter(k => k.id <= closing).map(k => Math.abs(yawError(r, k.pose, k))));
  assert.ok(worst < 0.6, `worst yaw error up to the closing keyframe ${worst}`);
});

console.log(`trackerClosureRepeat.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exit(1);
