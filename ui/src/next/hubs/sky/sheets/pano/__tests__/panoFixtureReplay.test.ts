// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T26: the committed pan fixture `treeline-panshort-41`, replayed through the panorama scanner on a clean checkout
// (SPEC-v2 7.5, 8.3 T26). The fixture is real Three.js frames of the treeline scene, a 120 degree pan at 20 deg/s,
// with the realism sensor streams of 13.2 (90 x 160 at 12 fps and no frame noise, to stay under the 1.5 MB cap; see
// `tools/photosphere_sim/fixtures/README.md`). It is in the repository, so a missing one is a broken checkout and never
// a skip, and `npm test` needs no cache and no browser.
//
// What the replay must leave behind is 13.7: every result file exists and parses to its shape, and the ring band is
// painted. The spec does not define "the ring band's painted fraction", so this file does:
//   * the BAND is the raster rows of altitude 0 to 45 (rows 135 to 269 of 300, `row y -> altitude 90 - y / 299 * 100`),
//     which a pan at pitch 22.98 paints in full: the footprint runs from about -8 to 54;
//   * the ARC is the circular span of columns that holds every column with a painted pixel in the band once the single
//     largest run of unpainted columns (the part of the ring that was never scanned) is left out;
//   * the FRACTION is the share of the band's pixels inside the ARC whose alpha is 255.
// A 120 degree pan plus the 3 degree slit at each end is an arc of 126 degrees; a hole in the stitching shows as a
// fraction under 1, and a scanner that painted nothing shows as an empty arc.
//
// Mutants this file must catch (each run and reverted from a byte backup; see the T26 report):
//   * the replay run with the default `legacy` scanner instead of `pano`: 'every result file of 13.7 ...' finds
//     `result/diagnostics.json` missing;
//   * the fixture's `input/scanner.json` carrying a null declination: 'the fixture carries its scanner options ...'
//     finds the options differ;
//   * `panoAdapter.ts` never calling `setDeclination`: the same case finds `declination_applied` false;
//   * the arc measured as the whole ring, so the unscanned remainder counts as a hole: 'the ring band is painted ...'
//     finds a fraction of 0.345, and 'the painted-fraction measure ...' finds it too;
//   * the replay run on the committed fixture itself instead of a copy of its `input/`: the test crashes reading the
//     temporary copy's `result/`, and 'replaying the fixture writes nothing into the repository' is the guard for the
//     edit that reads the fixture's own `result/` as well.
// The manifest-hash check that catches a changed frame byte is `tools/photosphere_sim/tests/test_manifest_hashes.py`,
// which reads every directory under `fixtures/`, this one included.
import assert from 'node:assert/strict';
import { cpSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { decodePng } from '../../__sim__/png';
import { replayCase, type Summary } from '../../__sim__/replay';
import { BinState, PANO_H, PANO_W } from '../types';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

type Row = Record<string, unknown>;

const FIXTURE = fileURLToPath(new URL(
  '../../../../../../../../tools/photosphere_sim/fixtures/treeline-panshort-41/', import.meta.url));

// ---- The painted-fraction measure ---------------------------------------------------------------------------------

const BAND_ALT_TOP = 45, BAND_ALT_BOTTOM = 0;
/** Raster rows of the band, both inclusive: row y is altitude 90 - y / 299 * 100. */
const BAND_ROW_TOP = Math.ceil((90 - BAND_ALT_TOP) * 299 / 100);       // 135
const BAND_ROW_BOTTOM = Math.floor((90 - BAND_ALT_BOTTOM) * 299 / 100); // 269

interface BandMeasure { fraction: number; arcColumns: number; arcDeg: number }

/** The band's painted fraction inside the scanned arc, for an RGBA raster of PANO_W x PANO_H (see the header). */
function ringBandPainted(rgba: Uint8ClampedArray | Uint8Array): BandMeasure {
  assert.equal(rgba.length, PANO_W * PANO_H * 4);
  const painted = (x: number, y: number) => rgba[(y * PANO_W + x) * 4 + 3] === 255;
  const columnPainted: boolean[] = [];
  for (let x = 0; x < PANO_W; x++) {
    let any = false;
    for (let y = BAND_ROW_TOP; y <= BAND_ROW_BOTTOM && !any; y++) any = painted(x, y);
    columnPainted.push(any);
  }
  if (!columnPainted.some(Boolean)) return { fraction: 0, arcColumns: 0, arcDeg: 0 };
  // The largest circular run of unpainted columns: walk the ring twice so a run across the seam is one run.
  let bestLen = 0, bestStart = 0, run = 0;
  for (let k = 0; k < 2 * PANO_W; k++) {
    if (!columnPainted[k % PANO_W]) {
      run++;
      if (run > bestLen && run < PANO_W) { bestLen = run; bestStart = k - run + 1; }
    } else run = 0;
  }
  const arcColumns = PANO_W - bestLen;
  const arcStart = (bestStart + bestLen) % PANO_W;
  let count = 0;
  for (let j = 0; j < arcColumns; j++) {
    const x = (arcStart + j) % PANO_W;
    for (let y = BAND_ROW_TOP; y <= BAND_ROW_BOTTOM; y++) if (painted(x, y)) count++;
  }
  const rows = BAND_ROW_BOTTOM - BAND_ROW_TOP + 1;
  return { fraction: count / (arcColumns * rows), arcColumns, arcDeg: arcColumns * 360 / PANO_W };
}

/** A raster with `width` columns painted from `start` (wrapping), over every row, and `hole` columns of them blank. */
function arcRaster(start: number, width: number, hole?: { at: number; width: number }): Uint8ClampedArray {
  const px = new Uint8ClampedArray(PANO_W * PANO_H * 4);
  for (let j = 0; j < width; j++) {
    const x = (start + j) % PANO_W;
    if (hole && j >= hole.at && j < hole.at + hole.width) continue;
    for (let y = 0; y < PANO_H; y++) px[(y * PANO_W + x) * 4 + 3] = 255;
  }
  return px;
}

// ---- The replay ---------------------------------------------------------------------------------------------------

function rmTemp(path: string): void {
  if (path === tmpdir() || !path.startsWith(tmpdir() + sep))
    throw new Error(`refusing a recursive delete of ${path}: it is not a directory under ${tmpdir()}`);
  rmSync(path, { recursive: true, force: true });
}

const read = (root: string, name: string) => readFileSync(join(root, 'result', name), 'utf8');
const lines = (text: string): Row[] => text.split(/\r?\n/).filter(l => l.length > 0).map(l => JSON.parse(l) as Row);

interface Replayed {
  summary: Summary;
  files: string[];
  text: Record<string, string>;
  firstSeen: Buffer;
  panorama: { width: number; height: number; pixels: Uint8ClampedArray };
  frameFiles: string[];
  frameIds: Set<string>;
  scannerJson: Row;
}

assert.ok(existsSync(join(FIXTURE, 'input', 'observations.jsonl')),
  'treeline-panshort-41 is committed under tools/photosphere_sim/fixtures/; this checkout does not have it');

/** The fixture replayed on a COPY of its `input/`: `replayCase` empties and rewrites `<case>/result/`, and a test run
 *  must leave the working tree as it found it. */
async function replayFixture(): Promise<Replayed> {
  const root = mkdtempSync(join(tmpdir(), 'pano-fixture-replay-'));
  try {
    cpSync(join(FIXTURE, 'input'), join(root, 'input'), { recursive: true });
    const summary = await replayCase(root, { scanner: 'pano' });
    const files = readdirSync(join(root, 'result')).sort();
    const text: Record<string, string> = {};
    for (const name of files) if (name !== 'panorama.png' && name !== 'first_seen.bin') text[name] = read(root, name);
    const png = decodePng(readFileSync(join(root, 'result', 'panorama.png')));
    const frameFiles = readdirSync(join(root, 'input', 'frames')).filter(n => n.endsWith('.png')).sort();
    return {
      summary, files, text,
      firstSeen: files.includes('first_seen.bin') ? readFileSync(join(root, 'result', 'first_seen.bin')) : Buffer.alloc(0),
      panorama: { width: png.width, height: png.height, pixels: png.pixels },
      frameFiles,
      frameIds: new Set(frameFiles.map(n => n.replace(/\.png$/, ''))),
      scannerJson: JSON.parse(readFileSync(join(root, 'input', 'scanner.json'), 'utf8')) as Row,
    };
  } finally {
    rmTemp(root);
  }
}

const replayed = await replayFixture();

const RESULT_FILES = ['captures.jsonl', 'diagnostics.json', 'events.jsonl', 'first_seen.bin', 'horizon.json', 'live.jsonl',
  'panorama.png', 'summary.json'];

await test('every result file of 13.7 exists for the pano scanner, and columns.json (legacy only) does not', () => {
  for (const name of RESULT_FILES) assert.ok(replayed.files.includes(name), `result/${name} was not written`);
  assert.ok(!replayed.files.includes('columns.json'), 'columns.json is the legacy tracer\'s file; pano writes none');
});

await test('the fixture carries its scanner options and the replay read them', () => {
  assert.deepEqual(replayed.scannerJson, { focal_prior_scale: 1.1, sensor_only: false, declination_deg: 7.25 });
  const d = JSON.parse(replayed.text['diagnostics.json']) as Row;
  assert.equal(d.sensor_only, false);
  assert.equal(d.declination_applied, true, 'a non-null declination_deg in scanner.json must reach the scanner (13.6)');
});

await test('summary.json: every frame delivered, no cells, a cost block of Stats', () => {
  const s = JSON.parse(replayed.text['summary.json']) as Row;
  assert.equal(s.frames_delivered, replayed.frameFiles.length);
  assert.equal(replayed.summary.frames_delivered, replayed.frameFiles.length);
  assert.equal(s.cells_total, null);
  assert.equal(s.cells_covered, null);
  assert.equal(typeof s.events_delivered, 'number');
  assert.ok((s.events_delivered as number) > 0);
  assert.equal(typeof s.frames_accepted, 'number');
  assert.ok((s.frames_accepted as number) > 0, 'the scanner accepted no frame of the fixture');
  const cost = s.cost_ms as { callback: Row; callback_ref: Row; ref_unit_ms: number };
  assert.ok(cost, 'summary.json has no cost_ms for a scanner other than legacy');
  assert.equal(typeof cost.ref_unit_ms, 'number');
  assert.ok(cost.ref_unit_ms > 0);
  for (const key of ['callback', 'callback_ref'] as const) {
    assert.deepEqual(Object.keys(cost[key]).sort(), ['max', 'n', 'p50', 'p95'], `cost_ms.${key} is {n, p50, p95, max}`);
    assert.equal(cost[key].n, replayed.frameFiles.length);
  }
});

await test('horizon.json is the v2 shape of 13.7', () => {
  const h = JSON.parse(replayed.text['horizon.json']) as Row;
  assert.equal(h.version, 2);
  assert.equal(h.interpolation, 'linear-wrap');
  assert.equal(h.profile_bins, 720);
  assert.equal(h.bins, 720);
  for (const key of ['profile', 'profile_traced', 'profile_state'] as const) {
    assert.ok(Array.isArray(h[key]), `${key} is an array`);
    assert.equal((h[key] as unknown[]).length, 720, `${key} holds 720 bins`);
  }
  for (const v of h.profile as unknown[]) assert.ok(typeof v === 'number' && Number.isFinite(v), 'profile is finite numbers');
  for (const v of h.profile_traced as unknown[]) assert.ok(v === null || (typeof v === 'number' && Number.isFinite(v)));
  const states = new Set<number>(Object.values(BinState));
  for (const v of h.profile_state as unknown[]) assert.ok(typeof v === 'number' && states.has(v), `bin state ${String(v)}`);
  assert.ok(Array.isArray(h.points) && (h.points as unknown[]).length >= 2, 'a published polyline has two vertices at least');
  for (const p of h.points as Row[]) {
    assert.equal(typeof p.az, 'number');
    assert.equal(typeof p.alt, 'number');
  }
  assert.equal(typeof h.tau, 'number');
  assert.ok(Array.isArray(h.uncertain_bins));
  for (const b of h.uncertain_bins as unknown[]) assert.ok(Number.isInteger(b) && (b as number) >= 0 && (b as number) < 720);
});

await test('events.jsonl: one row per delivered frame, the legacy fields plus keyframe and cls', () => {
  const rows = lines(replayed.text['events.jsonl']);
  assert.equal(rows.length, replayed.frameFiles.length);
  const ids = rows.map(r => r.frame_id as string);
  assert.deepEqual(ids, [...replayed.frameIds].sort(), 'frames are delivered in order, once each');
  for (const r of rows) {
    for (const key of ['t_ms', 'frame_id', 'compass_ready', 'tilt_ready', 'aim', 'basis', 'frame_count', 'cue', 'keyframe', 'cls'])
      assert.ok(key in r, `an events row has no ${key}`);
    assert.equal(typeof r.t_ms, 'number');
    assert.equal(typeof r.keyframe, 'boolean');
    assert.ok(r.cls === null || r.cls === 'aligned' || r.cls === 'blurred' || r.cls === 'sensor', `cls ${String(r.cls)}`);
    assert.equal(typeof r.cue, 'string');
    if (r.basis !== null) {
      const b = r.basis as Record<string, number[]>;
      for (const axis of ['right', 'up', 'forward']) assert.equal(b[axis].length, 3);
    }
  }
  assert.ok(rows.some(r => r.keyframe === true), 'no frame of the fixture became a keyframe');
});

await test('captures.jsonl: snake_case CaptureRecord rows that name the case\'s own frames', () => {
  const rows = lines(replayed.text['captures.jsonl']);
  assert.ok(rows.length > 0);
  for (const r of rows) {
    assert.equal(typeof r.at, 'number');
    assert.equal(typeof r.outcome, 'string');
    assert.ok(replayed.frameIds.has(r.frame_id as string), `capture names ${String(r.frame_id)}, which is not a frame of the case`);
    for (const key of Object.keys(r)) assert.ok(!/[A-Z]/.test(key), `${key} is not snake_case`);
  }
  assert.ok(rows.some(r => r.outcome === 'accepted'));
});

await test('diagnostics.json: the Diagnostics keys of 3.4, verbatim', () => {
  const d = JSON.parse(replayed.text['diagnostics.json']) as Row;
  assert.deepEqual(Object.keys(d).sort(), [
    'axis_mapping', 'begin_ms', 'declination_applied', 'extractor', 'finish_ms', 'focal', 'keyframe_ms', 'keyframes', 'loop',
    'mode_changes', 'north', 'predictor_mode', 'readback_ms', 'scanner', 'sensor_only', 'stale_refusals', 'tau_applied',
    'tau_ms', 'tau_pairs', 'tau_sigma_ms', 'version',
  ]);
  assert.equal(d.version, 1);
  assert.equal(d.scanner, 'pano');
  assert.equal(d.extractor, 'tracer');
  assert.equal(typeof d.begin_ms, 'number');
  assert.equal(typeof d.finish_ms, 'number');
  assert.ok(Array.isArray(d.mode_changes));
  assert.deepEqual(Object.keys(d.focal as Row).sort(), ['f_norm', 'sd_pct', 'short_fov_deg', 'state', 'ratios'].sort());
  assert.deepEqual(Object.keys(d.loop as Row).sort(), ['closed', 'match', 'method', 'post_deg', 'pre_deg', 'unwrapped_deg']);
  assert.equal((d.loop as Row).closed, false, 'a 120 degree pan cannot close a ring');
  for (const key of ['keyframe_ms', 'readback_ms']) assert.deepEqual(Object.keys(d[key] as Row).sort(), ['max', 'n', 'p50', 'p95']);
  const kfs = d.keyframes as Row[];
  assert.ok(kfs.length > 0);
  for (const kf of kfs) {
    assert.deepEqual(Object.keys(kf).sort(), ['cls', 'frame_id', 'id', 'q', 'sigma_deg', 't_ms']);
    assert.ok(replayed.frameIds.has(kf.frame_id as string));
    const q = kf.q as number[];
    assert.equal(q.length, 4);
    assert.ok(Math.abs(Math.hypot(...q) - 1) < 1e-6, 'a keyframe pose is a unit quaternion');
  }
});

await test('first_seen.bin is 1080 x 300 little-endian Uint16 and live.jsonl is one LiveSnapshot per keyframe', () => {
  assert.equal(replayed.firstSeen.length, PANO_W * PANO_H * 2);
  let seen = 0;
  for (let i = 0; i < PANO_W * PANO_H; i++) if (replayed.firstSeen.readUInt16LE(i * 2) > 0) seen++;
  assert.ok(seen > 0, 'first_seen.bin is all zeros: nothing was ever seen');
  const live = lines(replayed.text['live.jsonl']);
  const kfCount = lines(replayed.text['events.jsonl']).filter(r => r.keyframe === true).length;
  assert.equal(live.length, kfCount, 'one live snapshot per keyframe frame');
  for (const r of live) {
    assert.deepEqual(Object.keys(r).sort(), ['closure_state', 'focal_state', 'kf', 'painted_fraction', 't_ms']);
    assert.ok(r.closure_state === 'open' || r.closure_state === 'closed-image' || r.closure_state === 'closed-gyro');
    assert.ok((r.painted_fraction as number) >= 0 && (r.painted_fraction as number) <= 1);
  }
});

await test('the ring band is painted: at least 0.9 of the scanned arc, which is the 120 degree pan', () => {
  assert.equal(replayed.panorama.width, PANO_W);
  assert.equal(replayed.panorama.height, PANO_H);
  const m = ringBandPainted(replayed.panorama.pixels);
  assert.ok(m.fraction >= 0.9, `the ring band's painted fraction is ${m.fraction.toFixed(3)} over a ${m.arcDeg.toFixed(1)} degree arc`);
  // 120 degrees of pan plus the 3 degree half-slit at each end is 126; a sliver of paint would pass the fraction alone.
  assert.ok(m.arcDeg >= 115 && m.arcDeg <= 145, `the scanned arc is ${m.arcDeg.toFixed(1)} degrees, not about 126`);
});

await test('the painted-fraction measure reads a hole as a hole and a blank raster as nothing', () => {
  const whole = ringBandPainted(arcRaster(100, 378));
  assert.equal(whole.arcColumns, 378);
  assert.equal(whole.fraction, 1);
  assert.ok(Math.abs(whole.arcDeg - 126) < 1e-9);
  // A 60 column hole inside a 378 column arc leaves 318 painted columns: 0.841, under the 0.9 bar.
  const holed = ringBandPainted(arcRaster(100, 378, { at: 150, width: 60 }));
  assert.equal(holed.arcColumns, 378);
  assert.ok(Math.abs(holed.fraction - 318 / 378) < 1e-9, `holed fraction ${holed.fraction}`);
  assert.ok(holed.fraction < 0.9);
  // An arc across the raster's seam is one arc.
  const across = ringBandPainted(arcRaster(1000, 378));
  assert.equal(across.arcColumns, 378);
  assert.equal(across.fraction, 1);
  assert.deepEqual(ringBandPainted(new Uint8ClampedArray(PANO_W * PANO_H * 4)), { fraction: 0, arcColumns: 0, arcDeg: 0 });
});

await test('replaying the fixture writes nothing into the repository', () => {
  assert.ok(existsSync(join(FIXTURE, 'input', 'observations.jsonl')));
  assert.equal(existsSync(join(FIXTURE, 'result')), false, 'a replay wrote result/ into the committed fixture');
  const names = readdirSync(FIXTURE).sort();
  assert.deepEqual(names, ['input', 'manifest.json', 'truth']);
});

console.log(`panoFixtureReplay.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
