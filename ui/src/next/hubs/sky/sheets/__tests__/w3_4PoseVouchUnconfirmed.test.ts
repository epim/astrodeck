// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_4PoseVouchUnconfirmed.test.ts - WP 3.4 (#1179): sky over a bearing nobody confirmed is not vouched.
//
// A pose vouches for a blank frame only when an image-confirmed frame lies within one view angle of it. Sky scanned
// over a bearing whose horizon was never scanned has no such neighbour and stays held. Split from
// w3_4PoseVouch.test.ts to keep each file inside the runner's per-file bound (see w3_4PoseVouchRig.ts).
import assert from 'node:assert/strict';
import { aim, capturedOf, rig, setWalled } from './w3_4PoseVouchRig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

await test('a blank frame in a bin nothing confirmed stays held', async () => {
  setWalled(false);
  const sweep = await rig.newSweep();
  const low = sweep.cells.filter(c => c.alt >= 25 && c.alt <= 45 && c.az >= 40 && c.az < 100).sort((a, b) => a.az - b.az);
  const away = sweep.cells.filter(c => c.alt >= 50 && c.alt <= 75 && c.az >= 250 && c.az < 330).sort((a, b) => a.az - b.az);
  aim(low[0].az, low[0].alt);
  sweep.begin();
  for (const cell of low) { aim(cell.az, cell.alt); rig.tick(); aim(cell.az + 5, cell.alt); rig.tick(); }
  for (const cell of away) { aim(cell.az, cell.alt); rig.tick(); aim(cell.az + 4, cell.alt); rig.tick(); }
  assert.equal(capturedOf(away, sweep), 0, 'sky over a bearing nobody scanned the horizon at was vouched');
  sweep.stop();
});

console.log(`w3_4PoseVouchUnconfirmed.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
