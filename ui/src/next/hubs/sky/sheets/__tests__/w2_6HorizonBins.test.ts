// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_6HorizonBins.test.ts - WP 2.6 (#900, #1117): a scan publishes each bin for
// its whole width, and the per-bin status the saved horizon carries.
//
// #900. The tracer published one point per bin at the bin's CENTRE and every
// consumer interpolates linearly between points, so a bin's value held at its
// centre and slid toward its neighbour across the bin. The columns it sampled
// stopped 1.2 degrees short of each bin edge as well. A wall starting in the
// last 1.2 degrees of bin 5 was never seen by it, and a wall filling one bin
// read 40 only at its centre. The cases below drive the shipped `traceSweep`
// over a panorama the test paints, then read the saved line the way the finder
// does, through `horizonAltAt`.
//
// Named mutants (edits to photosphere.ts or horizonBins.ts, from byte backups):
//   centre points: the published points one per bin at the centre azimuth again.
//     The 71..100 case and the one-bin case fail on what `horizonAltAt` reads.
//   inner samples: the outer sampled columns 1.2 degrees inside the bin edges
//     again (`BIN_SAMPLE_COLUMNS = [18, 4, 9, 27, 31]`). The 71..100 case fails.
//   unmeasured neighbour not closed: `binAlt` returning the stored altitude for
//     `unknown`. The closed-neighbour case fails.
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<html><body></body></html>', { url: 'https://localhost/' });
Object.defineProperty(globalThis, 'window', { value: dom.window, configurable: true, writable: true });
Object.defineProperty(globalThis, 'document', { value: dom.window.document, configurable: true, writable: true });
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true, writable: true });
Object.defineProperty(globalThis, 'localStorage', { value: dom.window.localStorage, configurable: true, writable: true });

const { PhotosphereSweep, traceSweep } = await import('../photosphere');
const { SkyPanorama } = await import('../photosphereGeometry');
const { horizonAltAt } = await import('../../../../lib/horizonModel');
const { binAlt, binsDescribe, binsFromTrace, horizonAltAtBins, parseSavedBins, pointsFromBins, EDGE_RAMP_DEG } = await import('../horizonBins');

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}

// ---------------------------------------------------------------- the world
/** A panorama painted from `top(az)`: sky above it, wall (and ground below the horizon) at or under it. */
function paint(top: (az: number) => number): InstanceType<typeof SkyPanorama> {
  const pano = new SkyPanorama();
  for (let x = 0; x < pano.width; x++) {
    const az = (x + .5) / pano.width * 360;
    for (let y = 0; y < pano.height; y++) {
      const alt = 90 - y / (pano.height - 1) * 100;
      const sky = alt > Math.max(0, top(az));
      const i = (y * pano.width + x) * 4;
      pano.pixels[i] = sky ? 90 : 75; pano.pixels[i + 1] = sky ? 150 : 62; pano.pixels[i + 2] = sky ? 235 : 40; pano.pixels[i + 3] = 255;
    }
  }
  return pano;
}

/** The line a scan of `pano` saves, through the shipped entry. */
function scanned(pano: InstanceType<typeof SkyPanorama>) {
  const sweep = new PhotosphereSweep();
  (sweep as unknown as { panorama: unknown }).panorama = pano;
  return traceSweep({ columns: () => sweep.columns(), lensDoubtedThisScan: false });
}

/** Every azimuth in [from, to) at half-degree steps. */
const across = (from: number, to: number): number[] => Array.from({ length: Math.round((to - from) * 2) }, (_, i) => from + i / 2);

// ------------------------------------------------------------------- cases
test('#900: a wall starting in the last degree of a bin is read at its height by the planner across its whole width', () => {
  // Bin 5 is azimuth 60 to 72; the wall is 71 to 100 at 25 degrees. Bin 5's outer
  // column used to sit at 70.8, so the bin saw no wall and the planner read 10.8
  // degrees at azimuth 71 and 19.5 at 75.
  const trace = scanned(paint(az => (az >= 71 && az < 100 ? 25 : 0)));
  assert.equal(trace.uncertainBins.length, 0, 'the painted world should be fully measurable');
  for (const az of across(71.5, 99.5)) {
    const read = horizonAltAt(trace.points, az);
    assert.ok(read >= 24, `azimuth ${az}: the wall is 25 and the planner reads ${read.toFixed(2)}`);
  }
});

test('#900: a wall that fills exactly one bin is read at its height from its first degree to its last', () => {
  // Bin 6 is 72 to 84. It used to be one point at 78, so the line climbed from
  // 20 at 72 to 41 at 78.
  const trace = scanned(paint(az => (az >= 72 && az < 84 ? 40 : 0)));
  for (const az of across(72.5, 83.5)) {
    const read = horizonAltAt(trace.points, az);
    assert.ok(read >= 39, `azimuth ${az}: the wall is 40 and the planner reads ${read.toFixed(2)}`);
  }
  assert.ok(horizonAltAt(trace.points, 60) < 1 && horizonAltAt(trace.points, 100) < 1, 'the open sky either side stays open');
});

test('the trace carries one bin per azimuth bin and its points are its bins at both edges', () => {
  const trace = scanned(paint(az => (az >= 72 && az < 84 ? 40 : 0)));
  assert.equal(trace.bins.length, 30);
  assert.deepEqual(trace.points, pointsFromBins(trace.bins));
  assert.equal(trace.bins[6].status, 'measured');
  assert.ok(Math.abs(trace.bins[6].alt - 40) <= 2, `bin 6 reads ${trace.bins[6].alt}`);
});

test('the shipped 30 bins stay under the 180 point cap the server holds a drawn horizon to', () => {
  const alternating = Array.from({ length: 30 }, (_, i) => ({ alt: i % 2 ? 40 : 5, status: 'measured' as const }));
  assert.ok(pointsFromBins(alternating).length <= 180, `${pointsFromBins(alternating).length} points`);
});

test('the same polyline and probes as the scorer: one reading in two languages', () => {
  // tools/photosphere_sim/tests/test_w2_6_interpolation.py holds the same table.
  const line = [{ az: 0, alt: 10 }, { az: 90, alt: 40 }, { az: 270, alt: 20 }];
  const probes: [number, number][] = [[0, 10], [45, 25], [90, 40], [180, 30], [270, 20], [315, 15], [359, 10 + 10 / 90]];
  for (const [az, want] of probes) assert.ok(Math.abs(horizonAltAt(line, az) - want) < 1e-9, `azimuth ${az}`);
});

// ------------------------------------------------- an unmeasured bin is closed
test('an unmeasured bin publishes 90 across its whole width and does not slide into its measured neighbours', () => {
  const bins = binsFromTrace([10, 10, 10, 10, 10, 10], [2]);
  assert.equal(bins[2].status, 'unknown');
  assert.equal(binAlt(bins[2]), 90);
  // Six bins of 60 degrees; bin 2 is 120 to 180.
  for (const az of across(120, 180)) assert.equal(horizonAltAtBins(bins, az), 90, `azimuth ${az}`);
  // The measured neighbours read their own value up to the ramp at the edge.
  assert.equal(horizonAltAtBins(bins, 119.9 - EDGE_RAMP_DEG), 10);
  assert.equal(horizonAltAtBins(bins, 180 + EDGE_RAMP_DEG + .1), 10);
});

test('a stored altitude on an unknown bin never leaks into the line: unmeasured is closed, whatever it carries', () => {
  const bins = [{ alt: 10, status: 'measured' as const }, { alt: 3, status: 'unknown' as const }, { alt: 10, status: 'measured' as const }];
  assert.equal(horizonAltAtBins(bins, 180), 90);
});

test('between two measured bins the higher one keeps the shared edge', () => {
  const bins = [{ alt: 5, status: 'measured' as const }, { alt: 40, status: 'measured' as const }];
  // Bin 0 is 0 to 180, bin 1 is 180 to 360. The edge at 180 belongs to the 40.
  assert.equal(horizonAltAtBins(bins, 180), 40);
  assert.equal(horizonAltAtBins(bins, 100), 5);
  assert.equal(horizonAltAtBins(bins, 300), 40);
  assert.ok(horizonAltAtBins(bins, 180 - EDGE_RAMP_DEG / 2) > 5, 'the lower bin ramps up inside its own span');
});

test('a level horizon is a single point and a line with no bins is empty', () => {
  assert.equal(pointsFromBins(Array.from({ length: 12 }, () => ({ alt: 7, status: 'measured' as const }))).length, 1);
  assert.deepEqual(pointsFromBins([]), []);
});

// ------------------------------------------------------------- the saved form
test('the status round-trips through its saved form and a line edited since is no longer described by it', () => {
  const bins = binsFromTrace([5, 5, 30, 5], [1]);
  const saved = JSON.parse(JSON.stringify(bins));
  const loaded = parseSavedBins(saved);
  assert.deepEqual(loaded, bins);
  assert.ok(binsDescribe(loaded!, pointsFromBins(bins)));
  const edited = pointsFromBins(bins).map((p, i) => (i === 0 ? { ...p, alt: p.alt + 3 } : p));
  assert.equal(binsDescribe(loaded!, edited), false);
});

test('a saved status that is not one is no status at all', () => {
  assert.equal(parseSavedBins(null), null);
  assert.equal(parseSavedBins([]), null);
  assert.equal(parseSavedBins([{ alt: 5, status: 'guessed' }]), null);
  assert.equal(parseSavedBins([{ alt: NaN, status: 'measured' }]), null);
  assert.equal(parseSavedBins('measured'), null);
  assert.equal(parseSavedBins(Array.from({ length: 721 }, () => ({ alt: 5, status: 'measured' }))), null);
});

console.log(`w2_6HorizonBins: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log('  ' + f);
export const result = { passed, failed, total: passed + failed };
