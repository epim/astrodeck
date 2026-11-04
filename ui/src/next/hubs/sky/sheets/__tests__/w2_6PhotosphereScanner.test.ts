// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_6PhotosphereScanner.test.ts - WP 2.6 (#904, #903, #902): what the legacy
// scanner counts, what it calls calibrated, and what its report says.
//
//   #904  A frame whose overlap test cannot decide - every scan's first frame,
//         and a view of blank sky - is placed by the sensor alone, so nothing
//         has checked it. It is HELD: it turns no cell green, paints nothing the
//         trace reads, and joins the scan only when a later frame that agrees
//         with it BY ITSELF is taken at least 3 degrees away.
//   #903  `setCameraViewAngle` marked the lens calibrated for any typed number,
//         which switched off the lens-doubt guard of #129 (a refusing scan
//         publishing a certain horizon over a roof) and the lens cue. A typed or
//         stored angle is an estimate; only a focal fit is a calibration.
//   #902  `alignmentReport()` carried 16 samples and none of the refusals the
//         scanner logs. It now carries the capture log's outcomes and the newest
//         records with every sub-reason.
//
// Named mutants (edits to photosphere.ts, from byte backups, restored by hash):
//   typed angle calibrates   `adoptViewAngle`'s source forced to 'measured'
//                            (cases: the typed-angle case, the doubt case)
//   unknown frames painted   `vouched = true` for every frame (cases: held first
//                            frame, same pose, blank sky)
//   report without the log   `captureLog` dropped from `alignmentReport`
import assert from 'node:assert/strict';
import { createRig, type World } from './w2_6Rig';

const D = Math.PI / 180;
/** Texture in direction space, plus uniform sky above 45 degrees: a view centred above 75 sees nothing else. */
const world: World = (az, alt) => {
  if (alt > 45) return { r: 110, g: 160, b: 235 - alt * .2 };
  const l = 128 + 55 * Math.sin(az * D * 5 + .7) * Math.cos(alt * D * 4) + 40 * Math.sin(az * D * 11 + alt * D * 7) + 25 * Math.sin(alt * D * 13 - az * D * 3);
  return { r: l, g: l, b: l };
};

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}

const rig = await createRig(world);
const { LENS_DOUBT_AFTER } = rig.sweepModule;
const cells = (sweep: { cells: { id: number; az: number; alt: number; captured: boolean }[] }) => sweep.cells;
const ring = (sweep: Parameters<typeof cells>[0], alt: number) => cells(sweep).find(c => Math.abs(c.alt - alt) < 6)!;
const captured = (sweep: Parameters<typeof cells>[0]) => cells(sweep).filter(c => c.captured).length;
/** The focal fit a locked estimator hands over, for a lens of `degrees`. */
const fit = (degrees: number, ratios = 6) => ({ state: 'locked' as const, fBest: 0.5 / Math.tan(degrees * Math.PI / 360), ratios });

// ------------------------------------------------------------------- #904
await test('#904: the first frame of a scan is held, turns nothing green and reaches no trace', async () => {
  const sweep = await rig.newSweep();
  const cell = ring(sweep, 30);
  rig.aim(cell.az, cell.alt);
  sweep.begin();
  rig.tick();
  assert.equal(sweep.heldFrames, 1);
  assert.equal(captured(sweep), 0, 'a frame nothing had checked turned cells green');
  const last = sweep.captureLog[sweep.captureLog.length - 1];
  assert.equal(last.outcome, 'accepted');
  assert.equal(last.pending, true);
  const trace = rig.sweepModule.traceSweep(sweep);
  assert.ok(trace.bins.every(b => b.status === 'unknown'), 'a held frame was published as measured');
  assert.equal(trace.uncertainBins.length, 30);
  sweep.stop();
});

await test('#904: a second frame turned from it that agrees with it vouches for both', async () => {
  const sweep = await rig.newSweep();
  const cell = ring(sweep, 30);
  rig.aim(cell.az, cell.alt);
  sweep.begin();
  rig.tick();
  rig.aim(cell.az + 6, cell.alt);
  rig.tick();
  assert.equal(sweep.heldFrames, 0);
  assert.ok(captured(sweep) > 0, 'two agreeing frames turned nothing green');
  assert.ok(sweep.captureLog.some(r => r.outcome === 'accepted' && !r.pending));
  sweep.stop();
});

await test('#904: the same picture again is not evidence: frames from one pose never vouch for each other', async () => {
  const sweep = await rig.newSweep();
  const cell = ring(sweep, 30);
  rig.aim(cell.az, cell.alt);
  sweep.begin();
  for (let i = 0; i < 6; i++) rig.tick();
  assert.equal(captured(sweep), 0, 'identical frames from one pose vouched for each other');
  assert.equal(sweep.heldFrames, 1, 'a held frame was not replaced by the retake of its own tile');
  sweep.stop();
});

await test('#904: a view of blank sky cannot be vouched for, however many frames are taken near it', async () => {
  const sweep = await rig.newSweep();
  const low = ring(sweep, 30);
  rig.aim(low.az, low.alt);
  sweep.begin();
  rig.tick();
  rig.aim(low.az + 6, low.alt);
  rig.tick();
  const before = captured(sweep);
  assert.ok(before > 0, 'premise: the textured pair was vouched for');
  // Straight up sees only the uniform sky: a gradient has no texture for an overlap test.
  const cap = cells(sweep).find(c => c.alt > 80)!;
  for (const turn of [0, 7, 14]) { rig.aim(cap.az + turn, 82); rig.tick(); }
  assert.ok(sweep.captureLog.some(r => r.outcome === 'accepted' && r.pending), 'no blank frame was held');
  assert.ok(sweep.heldFrames >= 1);
  assert.equal(cells(sweep).find(c => c.id === cap.id)!.captured, false, 'the blank cap turned green');
  sweep.stop();
});

// ------------------------------------------------------------------- #903
await test('#903: a typed view angle is an estimate: the lens is not calibrated and the cue stays on', async () => {
  const sweep = await rig.newSweep();
  assert.equal(sweep.lensViewAngleSource, 'default');
  assert.equal(sweep.setCameraViewAngle(50), true);
  assert.equal(sweep.cameraViewAngle, 50, 'the typed number is still the starting estimate');
  assert.equal(sweep.lensViewAngleSource, 'user-estimate');
  assert.equal(sweep.hasLensCalibration, false);
  sweep.stop();
});

await test('#903: a focal fit is what calibrates, and a prior or a thin fit is not one', async () => {
  const sweep = await rig.newSweep();
  assert.equal(sweep.adoptFocalFit({ state: 'prior', fBest: 0.5 / Math.tan(25 * D), ratios: 0 }), false);
  assert.equal(sweep.adoptFocalFit(fit(41, 4)), false, 'four ratios are not a lock');
  assert.equal(sweep.lensViewAngleSource, 'default');
  assert.equal(sweep.adoptFocalFit(fit(10)), false, 'a fit outside 35 to 100 degrees is not a camera');
  assert.equal(sweep.adoptFocalFit(fit(41)), true);
  assert.equal(sweep.lensViewAngleSource, 'measured');
  assert.equal(sweep.hasLensCalibration, true);
  assert.ok(Math.abs(sweep.cameraViewAngle - 41) < 1e-9);
  sweep.stop();
});

await test('#903: stored values keep their source across a restart, and a bare stored number is migrated as an estimate', async () => {
  const w = rig.window;
  const sweep = await rig.newSweep('stored');
  assert.equal(sweep.adoptFocalFit(fit(44)), true);
  sweep.stop();
  const again = new rig.sweepModule.PhotosphereSweep();
  await again.start(w.document.createElement('video'), w.document.createElement('canvas'), 'stored');
  assert.equal(again.lensViewAngleSource, 'measured');
  again.stop();
  // What every browser that ever typed an angle holds: the number alone, once taken for calibrated.
  const key = [...Array(w.localStorage.length).keys()].map(i => w.localStorage.key(i) as string).find(k => k.startsWith('astrodeck.photosphere.lens.'))!;
  w.localStorage.setItem(key, '55');
  const migrated = new rig.sweepModule.PhotosphereSweep();
  await migrated.start(w.document.createElement('video'), w.document.createElement('canvas'), 'stored');
  assert.equal(migrated.cameraViewAngle, 55);
  assert.equal(migrated.lensViewAngleSource, 'user-estimate');
  assert.equal(migrated.hasLensCalibration, false);
  migrated.stop();
  w.localStorage.setItem(key, 'not json');
  const junk = new rig.sweepModule.PhotosphereSweep();
  await junk.start(w.document.createElement('video'), w.document.createElement('canvas'), 'stored');
  assert.equal(junk.lensViewAngleSource, 'default');
  junk.stop();
});

/** A scan that matches two patches, then is refused match after match: the camera shows a different place from the
 *  one the compass reports, which is what a lens wrong enough to be unabsorbable looks like to the overlap test. */
async function refusedScan(setup: (sweep: Awaited<ReturnType<typeof rig.newSweep>>) => void) {
  const sweep = await rig.newSweep();
  setup(sweep);
  const home = ring(sweep, 30);
  rig.aim(home.az, home.alt);
  sweep.begin();
  rig.tick();
  rig.aim(home.az + 6, home.alt);
  rig.tick();
  const apart = (c: { az: number }) => Math.abs(((c.az - home.az + 540) % 360) - 180);
  const next = cells(sweep).filter(c => !c.captured && Math.abs(c.alt - home.alt) < 15 && apart(c) >= 20)
    .sort((a, b) => apart(a) - apart(b))[0];
  assert.ok(next, 'no uncovered cell beside the matched patches');
  rig.aim(next.az, next.alt);
  rig.seeing(next.az + 150, next.alt);
  for (let i = 0; i < LENS_DOUBT_AFTER + 3; i++) rig.tick();
  rig.seeing(null);
  return sweep;
}

await test('#903: after a run of refusals a typed angle still publishes nothing certain, and the lens cue appears', async () => {
  const sweep = await refusedScan(s => assert.equal(s.setCameraViewAngle(80), true));
  assert.ok(sweep.captureLog.filter(r => r.outcome === 'overlap-wait' || r.outcome === 'carry-too-large').length >= LENS_DOUBT_AFTER,
    'premise: the scan was refused a run of matches');
  assert.equal(sweep.lensDoubtedThisScan, true, 'a typed angle switched the lens-doubt latch off');
  const trace = rig.sweepModule.traceSweep(sweep);
  assert.equal(trace.lensInDoubt, true);
  assert.ok(trace.bins.every(b => b.status === 'unknown' && b.alt === 90), 'a refusing scan on a typed angle published a certain bin');
  assert.match(sweep.captureCue, /camera view angle may be set wrong/);
  sweep.stop();
});

await test('#903: the same refusals on a measured lens do not latch the doubt', async () => {
  const sweep = await refusedScan(s => assert.equal(s.adoptFocalFit(fit(60)), true));
  assert.equal(sweep.lensDoubtedThisScan, false);
  assert.doesNotMatch(sweep.captureCue, /camera view angle may be set wrong/);
  sweep.stop();
});

// ------------------------------------------------------------------- #902
await test('#902: the alignment report carries the capture log: outcomes, newest records and their refusal reasons', async () => {
  const sweep = await refusedScan(s => assert.equal(s.setCameraViewAngle(80), true));
  const report = JSON.parse(sweep.alignmentReport());
  assert.equal(report.captureLog.total, sweep.captureLog.length);
  const histogram = report.captureLog.outcomes as Record<string, number>;
  assert.equal(Object.values(histogram).reduce((a, b) => a + b, 0), sweep.captureLog.length, 'the histogram does not count the whole log');
  assert.ok((histogram['overlap-wait'] ?? 0) + (histogram['carry-too-large'] ?? 0) >= LENS_DOUBT_AFTER);
  const refusal = report.captureLog.records.find((r: { outcome: string }) => r.outcome === 'overlap-wait');
  assert.ok(refusal, 'no overlap-wait record in the report');
  assert.ok(['brightness', 'edges', 'both'].includes(refusal.overlapTerm), `no refusal term: ${JSON.stringify(refusal)}`);
  assert.equal(typeof refusal.correlation, 'number');
  assert.equal(typeof refusal.samples, 'number');
  assert.ok(report.captureLog.records.every((r: object) => !('basis' in r) && !('sensorBasis' in r)), 'a record carried its bases');
  assert.deepEqual(report.lensViewAngle, { degrees: 80, source: 'user-estimate' });
  sweep.stop();
});

await test('#902: a scan refused before any registration still says why: the refusals are counted by outcome in the report', async () => {
  const sweep = await rig.newSweep();
  // Pointed just below the horizon, inside the gate that refuses it for want of a target: the pose is fine and
  // nothing can be accepted, which is the session a user reports as "it never captures".
  rig.aim(9, -9);
  sweep.begin();
  for (let i = 0; i < 3; i++) rig.tick();
  const report = JSON.parse(sweep.alignmentReport());
  assert.equal(report.captureLog.total, 3);
  assert.equal(report.captureLog.outcomes['no-target'], 3, JSON.stringify(report.captureLog.outcomes));
  assert.equal(report.samples.length, 0, 'premise: the old report carried nothing about this session');
  sweep.stop();
});

console.log(`w2_6PhotosphereScanner: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log('  ' + f);
export const result = { passed, failed, total: passed + failed };
