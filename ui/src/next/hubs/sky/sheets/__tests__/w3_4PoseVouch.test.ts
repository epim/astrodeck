// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_4PoseVouch.test.ts - WP 3.4 (#1179): a clear patch of sky is vouched by the pose.
//
// Since #904 a frame the overlap test cannot decide on is held, and a featureless patch of sky can never be decided on:
// a clear-night scan stayed short of 100 percent with Unknown bins the planner reads as blocked. A held frame is now
// measured on the pose alone when ALL of these hold: the pose came from the gyro-relative stream (whose drift is
// bounded), a frame the images confirmed lies within one view angle of it and was confirmed under
// `POSE_VOUCH_MAX_AGE_MS` (a minute) ago (the chain of pose-vouched frames inherits that time and does not refresh it), the bins it
// spans have blue sky measured just below its lower edge, and the frame is as bright and as blue as that sky.
// A frame blank because it faces a uniform wall fails the last two: what is below it is the wall, which is not
// blue, so a wall stays a wall.
//
// The bounds (no relative stream, a stale confirmation) are in w3_4PoseVouchBounds.test.ts and a bin nothing confirmed
// in w3_4PoseVouchUnconfirmed.test.ts: one scan costs a few seconds, and five in one file overran the runner's bound.
//
// Named mutant (an edit to photosphere.ts, from a byte backup): the wall check dropped (`consistentWithSky` answering
// true). Cases: the wall case fails.
import assert from 'node:assert/strict';
import { capturedOf, rig, scan, setWalled } from './w3_4PoseVouchRig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

await test('a clear patch of sky above a measured treeline is vouched by the pose, and its bins are measured', async () => {
  setWalled(false);
  const { sweep, sky, before } = await scan();
  assert.ok(sky.length >= 3, `premise: ${sky.length} sky cells`);
  assert.ok(before.ringCaptured >= before.ring * 0.8, `premise: the horizon ring was scanned (${before.ringCaptured} of ${before.ring})`);
  assert.equal(before.skyCaptured, 0, 'premise: no sky cell was captured before the sky was scanned');
  assert.equal(before.open, 30, 'premise: the horizon ring alone leaves every bin Unknown');
  assert.ok(capturedOf(sky, sweep) >= sky.length * 0.75, `${capturedOf(sky, sweep)} of ${sky.length} sky cells captured`);
  assert.ok(sweep.captureLog.some(r => r.outcome === 'accepted' && r.poseVouched === true), 'no frame was vouched by its pose');
  // The horizon ring alone leaves every bin Unknown (no sky rows above it were ever painted); the vouched sky closes the bins it covers.
  const bins = rig.sweepModule.traceSweep(sweep).bins.filter((_, i) => (i + .5) * 12 >= 120 && (i + .5) * 12 < 160);
  assert.ok(bins.length >= 3 && bins.every(b => b.status === 'measured'), JSON.stringify(bins));
  sweep.stop();
});

await test('a blank frame facing a uniform wall stays held', async () => {
  setWalled(true);
  const { sweep } = await scan();
  setWalled(false);
  const wall = sweep.cells.filter(c => c.alt >= 50 && c.alt <= 75 && c.az >= 105 && c.az < 155);
  assert.ok(wall.length >= 2, `premise: ${wall.length} cells above the wall`);
  assert.equal(capturedOf(wall, sweep), 0, 'a cell above the wall turned green');
  assert.ok(sweep.captureLog.some(r => r.outcome === 'accepted' && r.pending === true), 'no wall frame was held');
  assert.ok(sweep.heldFrames >= 1);
  assert.ok(!sweep.captureLog.some(r => r.poseVouched === true && wall.some(c => c.id === r.cell)), 'a wall frame was vouched by its pose');
  sweep.stop();
});

console.log(`w3_4PoseVouch.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
