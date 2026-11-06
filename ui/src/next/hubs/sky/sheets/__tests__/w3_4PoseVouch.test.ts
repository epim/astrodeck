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
// Named mutant (an edit to photosphere.ts, from a byte backup): the wall check dropped (`consistentWithSky` answering
// true). Cases: the wall case fails.
import assert from 'node:assert/strict';
import { createRig, type World } from './w2_6Rig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }
const D = Math.PI / 180;
const HORIZON = 15;

const texture = (az: number, alt: number) =>
  128 + 55 * Math.sin(az * D * 5 + .7) * Math.cos(alt * D * 4) + 40 * Math.sin(az * D * 11 + alt * D * 7) + 25 * Math.sin(alt * D * 13 - az * D * 3);
const ground = (az: number, alt: number) => { const l = texture(az, alt); return { r: l, g: l, b: l }; };
const SKY = { r: 110, g: 160, b: 235 };
const WALL = { r: 150, g: 140, b: 125 };
/** Textured ground under a uniform blue sky; with `walled`, between 100 and 160 degrees a uniform warm wall in place of the sky. */
let walled = false;
const world: World = (az, alt) => alt < HORIZON ? ground(az, alt) : walled && az >= 100 && az < 160 ? WALL : SKY;

const rig = await createRig(world);

/** One sensor sample from a phone at (az, alt): the compass, and (when `relative`) the gyro-relative stream 40 degrees behind. */
function aim(az: number, alt: number, relative = true) {
  rig.aim(az, alt);
  rig.seeing(az, alt);
  if (!relative) return;
  const ev = new rig.window.Event('deviceorientation');
  Object.assign(ev, { alpha: (360 - (az - 40) + 720) % 360, beta: 90 + alt, gamma: 0, absolute: false });
  Object.defineProperty(ev, 'timeStamp', { value: performance.now() });
  rig.window.dispatchEvent(ev);
}

type Sweep = Awaited<ReturnType<typeof rig.newSweep>>;
type Cell = Sweep['cells'][number];
const capturedOf = (cells: Cell[], sweep: Sweep) => cells.filter(c => sweep.cells.find(s => s.id === c.id)!.captured).length;

/** The ring below 45 degrees scanned all round, ending at azimuth 200; then the sky cells above 50 degrees between 76 and
 *  200, westward from where the ring ended (so the last frames the images confirmed are the nearest to the first blank
 *  ones), and the zenith cap. The tracer reads a bin only when its column is painted from the zenith to the horizon. */
async function scan(o: { relative?: boolean; idleMs?: number } = {}) {
  const relative = o.relative ?? true;
  const sweep = await rig.newSweep();
  const ring = sweep.cells.filter(c => c.alt <= 45).sort((a, b) => ((a.az - 200 + 360) % 360) - ((b.az - 200 + 360) % 360));
  const rotated = [...ring.filter(c => c.az > 200), ...ring.filter(c => c.az <= 200)];
  const sky = [...sweep.cells.filter(c => c.alt >= 50 && c.alt <= 85 && c.az >= 76 && c.az < 200).sort((a, b) => b.az - a.az), ...sweep.cells.filter(c => c.alt > 85)];
  aim(rotated[0].az, rotated[0].alt, relative);
  sweep.begin();
  for (const cell of rotated) { aim(cell.az, cell.alt, relative); rig.tick(); aim(cell.az + 5, cell.alt, relative); rig.tick(); }
  for (let i = 0; i < (o.idleMs ?? 0) / 50; i++) aim(200, 35, relative);
  const open = rig.sweepModule.traceSweep(sweep).bins.filter(b => b.status === 'unknown').length;
  const before = { open, skyCaptured: capturedOf(sky, sweep), ringCaptured: capturedOf(rotated, sweep), ring: rotated.length };
  for (const cell of sky) { aim(cell.az, cell.alt, relative); rig.tick(); aim(cell.az + 4, cell.alt, relative); rig.tick(); }
  return { sweep, sky, before };
}

await test('a clear patch of sky above a measured treeline is vouched by the pose, and its bins are measured', async () => {
  walled = false;
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
  walled = true;
  const { sweep } = await scan();
  walled = false;
  const wall = sweep.cells.filter(c => c.alt >= 50 && c.alt <= 75 && c.az >= 105 && c.az < 155);
  assert.ok(wall.length >= 2, `premise: ${wall.length} cells above the wall`);
  assert.equal(capturedOf(wall, sweep), 0, 'a cell above the wall turned green');
  assert.ok(sweep.captureLog.some(r => r.outcome === 'accepted' && r.pending === true), 'no wall frame was held');
  assert.ok(sweep.heldFrames >= 1);
  assert.ok(!sweep.captureLog.some(r => r.poseVouched === true && wall.some(c => c.id === r.cell)), 'a wall frame was vouched by its pose');
  sweep.stop();
});

await test('without the gyro-relative stream the pose is not bounded and the sky stays held', async () => {
  walled = false;
  const { sweep, sky } = await scan({ relative: false });
  assert.equal(capturedOf(sky, sweep), 0);
  assert.ok(!sweep.captureLog.some(r => r.poseVouched === true));
  sweep.stop();
});

await test('a confirmation that is too old no longer bounds the drift: the sky is held', async () => {
  walled = false;
  const { sweep, sky } = await scan({ idleMs: 70_000 });
  assert.equal(capturedOf(sky, sweep), 0);
  assert.ok(!sweep.captureLog.some(r => r.poseVouched === true));
  sweep.stop();
});

await test('a blank frame in a bin nothing confirmed stays held', async () => {
  walled = false;
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

console.log(`w3_4PoseVouch.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
