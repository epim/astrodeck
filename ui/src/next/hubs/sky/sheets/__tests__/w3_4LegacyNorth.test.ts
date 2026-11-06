// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_4LegacyNorth.test.ts - WP 3.4 (#905): north of the legacy photosphere scan.
//
// The scan used to take its north from ONE absolute reading beside the relative one it was paired with, so the compass
// noise of that sample, and the steel of a mount beside it, became a constant error of the whole scan. Now every
// simultaneous absolute-minus-relative offset is kept, the live view still starts from the first (so nothing jumps under
// the user), and the saved azimuths are moved by the robust circular mean of all of them, with the declination added
// for the roles that may see the site. The report says how north was established and never carries the declination.
//
// Named mutant (an edit to photospherePose.ts, from a byte backup): north anchored on the first absolute sample again
// (`north()` answering a shift of 0). Cases: the noisy-compass case, the spike case and the sweep case fail.
import assert from 'node:assert/strict';
import { ScanPoseSource } from '../photospherePose';
import { lookBasis } from '../photosphereGeometry';
import { createRig, type World } from './w2_6Rig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }
const D = Math.PI / 180;
const wrap180 = (deg: number) => ((deg + 540) % 360) - 180;

/** The compass noise of reading k, degrees: a fixed zero-mean wobble of about 3 degrees. */
const wobble = (k: number) => 3 * Math.sin(k * 2.399) + 1.2 * Math.sin(k * 0.71 + 1);
/** The mean of `wobble` over the readings a test uses, so an expected shift is computed and not guessed. */
const wobbleMean = (n: number) => { let s = 0; for (let k = 1; k < n; k++) s += wobble(k); return s / (n - 1); };

/** Feed `n` simultaneous pairs, 100 ms apart. The relative stream is `frameOffset` degrees behind true heading and the
 *  compass reads true heading plus `noise(k)`. */
function feedPairs(source: ScanPoseSource, n: number, frameOffset: number, noise: (k: number) => number) {
  for (let k = 0; k < n; k++) {
    const az = (k * 3) % 360, t = 1000 + k * 100;
    source.accept(lookBasis((az + noise(k) + 360) % 360, 25), true, t);
    source.accept(lookBasis((az - frameOffset + 360) % 360, 25), false, t + 10);
  }
}

await test('a noisy first compass sample does not set the scan\'s north: the shift is the mean offset less the first', () => {
  const source = new ScanPoseSource();
  feedPairs(source, 200, 40, k => (k === 0 ? 6 : wobble(k)));
  const north = source.north();
  // The live view was anchored on the first pair (offset 46), the best estimate of north is 40 + the wobble's mean.
  const expected = (40 + wobbleMean(200)) - 46;
  assert.equal(north.source, 'compass-mean');
  assert.ok(Math.abs(north.shiftDeg - expected) < 0.8, `shift ${north.shiftDeg} against ${expected}`);
  assert.ok(Math.abs(north.shiftDeg) > 4, 'the first-sample anchor would have said 0');
  assert.equal(north.samples, 200);
  assert.ok(north.spreadDeg !== null && north.spreadDeg > 1.5 && north.spreadDeg < 4.5, `spread ${north.spreadDeg}`);
});

await test('the live view still starts from the first pair and then holds still under later compass jumps', () => {
  const source = new ScanPoseSource();
  source.accept(lookBasis(120, 25), true, 100);
  const first = source.accept(lookBasis(10, 25), false, 120)!;
  assert.equal(first.changedSource, true);
  assert.equal(source.accept(lookBasis(150, 25), true, 150), null, 'a later absolute reading moves nothing once relative is in use');
  const moved = source.accept(lookBasis(15, 30), false, 160)!;
  assert.equal(moved.changedSource, false);
  assert.ok(Math.abs(wrap180(Math.atan2(moved.basis.forward[0], moved.basis.forward[1]) / D - 125)) < 0.01);
});

await test('compass spikes and a wall of steel for a moment do not move north', () => {
  const source = new ScanPoseSource();
  feedPairs(source, 200, 40, k => (k === 0 ? 0 : k % 10 === 3 ? 40 : wobble(k)));
  const north = source.north();
  assert.ok(Math.abs(north.shiftDeg - (40 + wobbleMean(200) - 40)) < 1.2, `shift ${north.shiftDeg}`);
});

await test('the mean is taken on the circle: an offset of 178 with noise across 180 reads 178', () => {
  const source = new ScanPoseSource();
  feedPairs(source, 120, 178, k => (k === 0 ? 0 : wobble(k)));
  const north = source.north();
  assert.ok(Math.abs(wrap180(north.shiftDeg - wobbleMean(120))) < 1.0, `shift ${north.shiftDeg}`);
});

await test('readings closer than 50 ms are one sample: a stream at 200 Hz does not outweigh a slow one', () => {
  const source = new ScanPoseSource();
  for (let k = 0; k < 100; k++) {
    source.accept(lookBasis(10, 25), true, 1000 + k * 5);
    source.accept(lookBasis(10 - 40 + 360, 25), false, 1000 + k * 5 + 1);
  }
  assert.ok(source.north().samples <= 11, `samples ${source.north().samples}`);
});

await test('north says how it was established: no readings, a compass alone, or the mean of pairs', () => {
  const source = new ScanPoseSource();
  assert.deepEqual(source.north(), { source: 'none', samples: 0, spreadDeg: null, shiftDeg: 0 });
  source.accept(lookBasis(120, 25), true, 100);
  assert.deepEqual(source.north(), { source: 'absolute-only', samples: 0, spreadDeg: null, shiftDeg: 0 });
  feedPairs(source, 5, 40, () => 0);
  assert.equal(source.north().source, 'compass-mean');
  source.clear();
  assert.equal(source.north().source, 'none');
});

// ---- the sweep ------------------------------------------------------------------------------------------------------

const world: World = (az, alt) => {
  const l = 128 + 55 * Math.sin(az * D * 5 + .7) * Math.cos(alt * D * 4) + 40 * Math.sin(az * D * 11 + alt * D * 7) + 25 * Math.sin(alt * D * 13 - az * D * 3);
  return { r: l, g: l, b: l };
};
const rig = await createRig(world);
const DECLINATION = 7.25;

/** One sensor sample from a phone at (az, alt): the exact compass (the rig's own aim), then a compass reading `noise` off,
 *  then the relative stream 40 degrees behind. The camera is told what it really sees. */
function feed(az: number, alt: number, noise: number) {
  rig.aim(az, alt);
  rig.seeing(az, alt);
  const send = (type: string, heading: number, absolute: boolean) => {
    const ev = new rig.window.Event(type);
    Object.assign(ev, { alpha: (360 - heading + 720) % 360, beta: 90 + alt, gamma: 0, absolute });
    Object.defineProperty(ev, 'timeStamp', { value: performance.now() });
    rig.window.dispatchEvent(ev);
  };
  send('deviceorientationabsolute', az + noise, true);
  send('deviceorientation', az - 40, false);
}

/** A sweep with two agreeing frames painted, and `extra` more sensor samples after them, the first pair 6 degrees off. */
async function scanned(extra = 60) {
  const sweep = await rig.newSweep();
  const cell = sweep.cells.find(c => Math.abs(c.alt - 30) < 6)!;
  feed(cell.az, cell.alt, 6);
  sweep.begin();
  rig.tick();
  feed(cell.az + 6, cell.alt, wobble(1));
  rig.tick();
  for (let k = 2; k < extra; k++) feed(cell.az + 6, cell.alt, wobble(k));
  return { sweep, wobbleMeanShift: -6 + wobbleMean(extra) };
}

/** Where the painted pixels moved: the column shift `k` with after[x + k] == before[x] over the painted pixels. */
function columnShift(before: { width: number; pixels: Uint8ClampedArray }, after: { width: number; pixels: Uint8ClampedArray }): number {
  const w = before.width;
  for (const k of [...Array(61).keys()].map(i => (i % 2 ? -1 : 1) * Math.ceil(i / 2))) {
    let same = 0, total = 0;
    for (let x = 0; x < w; x += 3) for (let y = 40; y < 260; y += 20) {
      const a = (y * w + x) * 4, b = (y * w + ((x + k + w) % w)) * 4;
      if (before.pixels[a + 3] !== 255) continue;
      total++;
      if (after.pixels[b + 3] === 255 && after.pixels[b] === before.pixels[a]) same++;
    }
    if (total > 40 && same / total > 0.98) return k;
  }
  throw new Error('no column shift explains the change');
}

await test('finishing a scan moves the saved azimuths by the mean north, and the report says how north was set', async () => {
  const { sweep, wobbleMeanShift } = await scanned();
  const before = sweep.panoramaPixels!;
  const early = JSON.parse(sweep.alignmentReport());
  assert.deepEqual(early.north.declinationApplied, false);
  sweep.finishNorth(null);
  const after = sweep.panoramaPixels!;
  const k = columnShift(before, after);
  const expected = wobbleMeanShift / 360 * before.width;
  assert.ok(Math.abs(k - expected) <= 2, `moved ${k} columns, expected about ${expected}`);
  const north = JSON.parse(sweep.alignmentReport()).north;
  assert.equal(north.source, 'compass-mean');
  assert.ok(north.samples >= 30, `samples ${north.samples}`);
  assert.ok(Number.isFinite(north.spreadDeg));
  assert.equal(north.declinationApplied, false);
  assert.deepEqual(Object.keys(north).sort(), ['declinationApplied', 'samples', 'source', 'spreadDeg']);
  sweep.stop();
});

await test('a declination shifts the azimuths further for a role that may see the site, and is never written into the report', async () => {
  const { sweep, wobbleMeanShift } = await scanned();
  const before = sweep.panoramaPixels!;
  sweep.finishNorth(az => az + DECLINATION);
  const k = columnShift(before, sweep.panoramaPixels!);
  const expected = (wobbleMeanShift + DECLINATION) / 360 * before.width;
  assert.ok(Math.abs(k - expected) <= 2, `moved ${k} columns, expected about ${expected}`);
  const text = sweep.alignmentReport();
  assert.equal(JSON.parse(text).north.declinationApplied, true);
  // Nothing in the report may be the declination, or a number that gives it away.
  assert.ok(!/7\.25|declination/i.test(text.replace('declinationApplied', '')), 'the report names the declination');
  const numbers = (text.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi) ?? []).map(Number);
  assert.ok(numbers.every(n => Math.abs(Math.abs(n) - DECLINATION) > 0.001), 'a number in the report is the declination');
  sweep.stop();
});

await test('a closure that gives no usable declination leaves the scan magnetic and says so', async () => {
  const { sweep } = await scanned();
  sweep.finishNorth(() => NaN);
  assert.equal(JSON.parse(sweep.alignmentReport()).north.declinationApplied, false);
  sweep.stop();
});

await test('finishing twice moves the azimuths once', async () => {
  const { sweep } = await scanned();
  sweep.finishNorth(az => az + DECLINATION);
  const once = sweep.panoramaPixels!.pixels;
  sweep.finishNorth(az => az + DECLINATION);
  assert.deepEqual(sweep.panoramaPixels!.pixels, once);
  sweep.stop();
});

await test('a scan with no relative stream has nothing to average: its azimuths are the compass\'s, plus the declination', async () => {
  const sweep = await rig.newSweep();
  const cell = sweep.cells.find(c => Math.abs(c.alt - 30) < 6)!;
  rig.seeing(null);
  rig.aim(cell.az, cell.alt);
  sweep.begin();
  rig.tick();
  rig.aim(cell.az + 6, cell.alt);
  rig.tick();
  const before = sweep.panoramaPixels!;
  sweep.finishNorth(az => az + DECLINATION);
  const north = JSON.parse(sweep.alignmentReport()).north;
  assert.equal(north.source, 'absolute-only');
  assert.equal(north.samples, 0);
  assert.equal(north.spreadDeg, null);
  assert.equal(north.declinationApplied, true);
  assert.ok(Math.abs(columnShift(before, sweep.panoramaPixels!) - DECLINATION / 360 * before.width) <= 2);
  sweep.stop();
});

console.log(`w3_4LegacyNorth.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
