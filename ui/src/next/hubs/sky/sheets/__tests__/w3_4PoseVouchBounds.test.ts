// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_4PoseVouchBounds.test.ts - WP 3.4 (#1179): the pose vouches for sky only while its drift is bounded.
//
// The gyro-relative stream bounds the drift (about 2 degrees a minute), and an image confirmation under a minute old
// bounds how far it has run. Without either, a blank sky frame stays held. Split from w3_4PoseVouch.test.ts to keep
// each file inside the runner's per-file bound (see w3_4PoseVouchRig.ts).
import assert from 'node:assert/strict';
import { capturedOf, scan, setWalled } from './w3_4PoseVouchRig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

await test('without the gyro-relative stream the pose is not bounded and the sky stays held', async () => {
  setWalled(false);
  const { sweep, sky } = await scan({ relative: false });
  assert.equal(capturedOf(sky, sweep), 0);
  assert.ok(!sweep.captureLog.some(r => r.poseVouched === true));
  sweep.stop();
});

await test('a confirmation that is too old no longer bounds the drift: the sky is held', async () => {
  setWalled(false);
  const { sweep, sky } = await scan({ idleMs: 70_000 });
  assert.equal(capturedOf(sky, sweep), 0);
  assert.ok(!sweep.captureLog.some(r => r.poseVouched === true));
  sweep.stop();
});

console.log(`w3_4PoseVouchBounds.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
