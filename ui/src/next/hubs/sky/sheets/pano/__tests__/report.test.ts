// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildReport, reportJson, statsOf } from '../report';
import type { ReportInput, ScanReport, Stats } from '../types';

// buildReport is the privacy boundary of the scan report: the report is a
// download the owner may attach to an issue, and SPEC-v2 13.10 says it holds no
// coordinates, no declination, no raw photo key, no user-agent string and no
// wall-clock time. It is built by NAMING the fields ScanReport declares and
// copying those, at every depth, so nothing else can come along.
//
// What each test pins, and the mutant it exists to catch:
//   * 'drops unknown keys at every depth' salts EVERY object in a complete
//     report (the top, each nested record, each element of each list) with a
//     latitude, a longitude, a declination, a photo key and a Date, then checks
//     the built report is identical to the one built from the unsalted input
//     and that none of the values reaches the JSON. The named mutant is
//     `buildReport` returning a spread copy of its input: the spread carries
//     every salted key at every depth, and this case fails at the first.
//   * 'a leaf of the wrong kind ...' puts a Date and an object where a number
//     and a string are declared. A copy that checked keys and not kinds would
//     write the Date's ISO text into the JSON.
//   * 'shares nothing with its input' fails a buildReport that hands the input
//     back or keeps a reference into it.
//   * 'statsOf' fixes the nearest-rank rule on hand-counted cases; interpolating
//     the rank moves p95 on the 1..10 and 1..20 cases, and rounding it instead of
//     taking the ceiling moves p95 on the 1..11 and 1..19 cases (0.95 n has a
//     fraction under one half for every n from 11 to 19).
//   * 'neither source reads the clock' scans the code of report.ts and
//     recorder.ts (comments removed) for Date, performance and timers.
//
// The coordinates here are made-up numbers; nothing in this file is a place.

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const stats = (n: number, p50: number | null, p95: number | null, max: number | null): Stats => ({ n, p50, p95, max });

/** A complete report body with every field filled and every list non-empty, in the key order of ScanReport. Fresh on every call. */
function fullInput(): ReportInput {
  return {
    scanner: { commit: '3f9a1c2', sensorOnly: false },
    browser: { brands: ['Chromium', 'Google Chrome'], mobile: true, platform: 'Android' },
    timeline: { beginMs: 1500.25, finishMs: 91234.5, endedBy: 'user' },
    camera: {
      settings: {
        width: 1080, height: 1920, frameRate: 30, resizeMode: 'none', zoom: 1, focusMode: 'manual',
        focusDistance: 0, facingMode: 'environment', label: 'back camera 0',
      },
      bench: { aMs: stats(10, 3.5, 4.1, 4.2), bMs: stats(10, 5.5, 6.1, 6.2), chosen: 'A', smoothing: 'medium' },
      farbled: false,
      exposureReadable: true,
      analysis: { w: 180, h: 320 },
    },
    frames: {
      delivered: 2710, viaRvfc: true, captureTimePresent: 2700,
      slips: { pairs: 2600, slips: 4 },
      lag: {
        nowMinusCapture: stats(2700, 41, 52, 90), presentMinusCapture: stats(2700, 16.7, 33.4, 50),
        expectedMinusNow: stats(2700, 8, 12, 20),
      },
      intervalMs: stats(2709, 33.3, 41, 120),
      presentedGaps: 3,
    },
    sensors: {
      modeAtBegin: 'absolute-gyro',
      modeChanges: [{ tMs: 1800, from: 'none', to: 'absolute-only' }, { tMs: 2600, from: 'absolute-only', to: 'absolute-gyro' }],
      events: [
        { type: 'deviceorientation', total: 5400, absoluteTrue: 5400, absoluteFalse: 0, absoluteMissing: 0, nullReadings: 0, duplicates: 5400 },
        { type: 'deviceorientationabsolute', total: 5400, absoluteTrue: 5400, absoluteFalse: 0, absoluteMissing: 0, nullReadings: 2, duplicates: 0 },
        { type: 'devicemotion', total: 5380, absoluteTrue: 0, absoluteFalse: 0, absoluteMissing: 5380, nullReadings: 1, duplicates: 0 },
      ],
      movingHz: { relative: 59.8, absolute: 20.1, motion: 60 },
      rateByOmega: [
        { omegaFrom: 0, omegaTo: 2, relativeHz: 4.2, absoluteHz: 1.1 },
        { omegaFrom: 2, omegaTo: 5, relativeHz: 31, absoluteHz: null },
      ],
      blocked: false,
      axis: { perm: [0, 1, 2], sign: [1, -1, 1], unit: 'deg', fit: 0.98, confirmed: true, samples: 340 },
      gyroZeroTriples: 12,
      gyroSeenNonZero: true,
      staleRefusals: 2,
      staleLimitMs: stats(2, 50, 150, 150),
      latency: { priorMs: 80, tauMs: 74.5, sigmaMs: 6.25, pairs: 88, applied: true },
    },
    focal: {
      source: 'default', state: 'closed', fNorm: 1.2694, sdPct: 0.139, ratios: 9, shortFovDeg: 61.5,
      closurePct: 0.8, gyroScale: 0.0151, ultraWideSuspected: false,
    },
    loop: {
      closed: true, method: 'image', preDeg: [0.5, -1.25, 6.75], postDeg: 0.04,
      match: { early: 3, late: 79 }, unwrappedDeg: 361.5,
    },
    north: { offsetDeg: 41.75, sigmaDeg: 2, spreadDeg: 3.5, samples: 80, nEff: 21.5, stable: true, source: 'scan' },
    declinationApplied: true,
    keyframes: {
      total: 83, aligned: 70, blurred: 9, sensor: 4, ms: stats(83, 2.5, 4.5, 6), readbackMs: stats(2700, 3, 5.5, 9),
      innovationSteadyDeg: stats(60, 0.1, 0.3, 0.5), innovationAccelDeg: stats(12, 0.4, 0.9, 1.1), perSliceMs: stats(900, 0.4, 0.9, 1.5),
    },
    liveFrame: { drawMs: stats(2500, 1.5, 3.5, 6), redraws: 2500 },
    horizon: { tau: 0.5, points: 58, measured: 600, low: 12, unknown: 100, tall: 8, kept: 0, lowLight: false },
    recording: { on: true, every: 2, frames: 1350 },
    captureLog: [
      { at: 1800, frameId: 1, outcome: 'accepted', detail: 'first', kf: 0 },
      { at: 1900, frameId: 4, outcome: 'accepted', detail: 'aligned', kf: 1, stepDeg: 4.5, rateDegS: 12, psr: 14.5, zncc: 0.93, innovationDeg: 0.12, wYaw: 0.8, extrapolatedMs: 0 },
      { at: 1933, frameId: 5, outcome: 'waiting-sharper' },
    ],
    slices: [{ kf: 0, dataUrl: 'data:image/jpeg;base64,AAAA' }, { kf: 40, dataUrl: 'data:image/jpeg;base64,BBBB' }],
    error: null,
  };
}

/** A second report that is mostly null: every nullable object and number reported as unknown. */
function sparseInput(): ReportInput {
  const r = fullInput();
  r.timeline = { beginMs: null, finishMs: null, endedBy: 'cancel' };
  r.camera.bench = null;
  r.camera.farbled = null;
  r.camera.exposureReadable = null;
  r.camera.settings = {
    width: null, height: null, frameRate: null, resizeMode: null, zoom: null, focusMode: null,
    focusDistance: null, facingMode: null, label: null,
  };
  r.browser = { brands: [], mobile: null, platform: null };
  r.sensors.modeAtBegin = null;
  r.sensors.axis = null;
  r.sensors.modeChanges = [];
  r.sensors.events = [];
  r.sensors.rateByOmega = [];
  r.sensors.movingHz = { relative: null, absolute: null, motion: null };
  r.loop = { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 12.5 };
  r.north = null;
  r.horizon = null;
  r.captureLog = [];
  r.slices = [];
  r.error = 'camera: NotAllowedError';
  r.keyframes.ms = stats(0, null, null, null);
  return r;
}

const withFormat = (r: ReportInput): ScanReport => ({ format: 'astrodeck-pano-report', version: 2, ...r });

// What the salting plants, and what must never come out. None of these is a
// real coordinate. `stamp` is a Date, the wall-clock value the report refuses.
const SALT = { lat: 12.345678, lon: 98.765432, declinationDeg: 7.8901234, photoKey: 'photo-key-SENTINEL-4f2a9c', stamp: new Date(Date.UTC(2031, 3, 5, 6, 7, 8)) };
const SALT_KEYS = Object.keys(SALT);
const SALT_TEXT = ['12.345678', '98.765432', '7.8901234', 'photo-key-SENTINEL', '2031-04-05', 'SENTINEL', 'declinationDeg', 'photoKey'];

/** A copy of `v` with the salt added to every object in it, at every depth, the lists' elements included. */
function salted<T>(v: T): T {
  if (Array.isArray(v)) return v.map(salted) as T;
  if (v !== null && typeof v === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v)) out[k] = salted(x);
    return Object.assign(out, SALT) as T;
  }
  return v;
}

/** Every key at every depth. */
function allKeys(v: unknown, into: string[] = []): string[] {
  if (Array.isArray(v)) v.forEach(x => allKeys(x, into));
  else if (v !== null && typeof v === 'object') for (const [k, x] of Object.entries(v)) { into.push(k); allKeys(x, into); }
  return into;
}

/** How many objects (not lists) there are in `v`, at every depth. */
function countObjects(v: unknown): number {
  if (Array.isArray(v)) return v.reduce((n: number, x) => n + countObjects(x), 0);
  if (v !== null && typeof v === 'object') return 1 + Object.values(v).reduce((n: number, x) => n + countObjects(x), 0);
  return 0;
}

function assertClean(text: string, what: string) {
  for (const needle of SALT_TEXT) assert.ok(!text.includes(needle), `${what}: contains ${needle}`);
}

test('a clean input comes out as itself with the format and version, key for key and in the order of ScanReport', () => {
  for (const make of [fullInput, sparseInput]) {
    const input = make();
    const built = buildReport(input);
    assert.deepEqual(built, withFormat(make()));
    assert.equal(JSON.stringify(built), JSON.stringify(withFormat(make())));
    assert.equal(built.format, 'astrodeck-pano-report');
    assert.equal(built.version, 2);
  }
  const keys = Object.keys(buildReport(fullInput()));
  assert.deepEqual(keys, ['format', 'version', 'scanner', 'browser', 'timeline', 'camera', 'frames', 'sensors', 'focal',
    'loop', 'north', 'declinationApplied', 'keyframes', 'liveFrame', 'horizon', 'recording', 'captureLog', 'slices', 'error']);
});

test('buildReport drops unknown keys at every depth: coordinates, declination, photo key and a Date reach no JSON', () => {
  for (const make of [fullInput, sparseInput]) {
    const clean = make();
    const dirty = salted(make());
    // The salt landed: one planted photo key in every object, and there are many objects.
    const objects = countObjects(clean);
    assert.ok(objects >= (make === fullInput ? 45 : 20), `${objects} objects in the input`);
    assert.equal(JSON.stringify(dirty).split('photo-key-SENTINEL').length - 1, objects);
    assert.ok(allKeys(dirty).includes('lat') && allKeys(dirty).includes('stamp'));

    const built = buildReport(dirty);
    assert.deepEqual(built, buildReport(clean), 'the salted input builds the same report as the clean one');
    assert.equal(JSON.stringify(built), JSON.stringify(buildReport(clean)));
    for (const k of SALT_KEYS) assert.ok(!allKeys(built).includes(k), `key ${k} survived`);
    assertClean(JSON.stringify(built), 'JSON.stringify(built)');
    assertClean(reportJson(built), 'reportJson(built)');
  }
});

test('format and version come from buildReport, never from the input', () => {
  const input = fullInput() as ReportInput & Record<string, unknown>;
  input.format = 'something-else';
  input.version = 9;
  const built = buildReport(input);
  assert.equal(built.format, 'astrodeck-pano-report');
  assert.equal(built.version, 2);
});

test('a leaf of the wrong kind is replaced by null: a Date in a number slot, an object in a string slot', () => {
  const dirty = fullInput() as ReportInput;
  const stamp = SALT.stamp;
  (dirty.timeline as unknown as Record<string, unknown>).beginMs = stamp;
  (dirty.camera.settings as unknown as Record<string, unknown>).label = { lat: SALT.lat, lon: SALT.lon };
  (dirty.keyframes.ms as unknown as Record<string, unknown>).p50 = stamp;
  (dirty.captureLog[0] as unknown as Record<string, unknown>).kf = 'a string';
  (dirty.captureLog[1] as unknown as Record<string, unknown>).outcome = 12;
  (dirty.sensors as unknown as Record<string, unknown>).blocked = 'yes';
  (dirty.loop.preDeg as unknown as unknown[])[1] = stamp;
  (dirty.browser.brands as unknown as unknown[])[0] = 7;
  const built = buildReport(dirty);
  assert.equal(built.timeline.beginMs, null);
  assert.equal(built.camera.settings.label, null);
  assert.equal(built.keyframes.ms.p50, null);
  assert.equal(built.captureLog[0].kf, null);
  assert.equal(built.captureLog[1].outcome, null);
  assert.equal(built.sensors.blocked, null);
  assert.deepEqual(built.loop.preDeg, [0.5, null, 6.75]);
  assert.deepEqual(built.browser.brands, [null, 'Google Chrome']);
  assertClean(JSON.stringify(built), 'wrong-kind leaves');
  // The neighbours of each replaced leaf are untouched.
  assert.equal(built.timeline.finishMs, 91234.5);
  assert.equal(built.keyframes.ms.p95, 4.5);
});

test('a Date, a list or a text where an object is declared becomes null; where a list is declared, null', () => {
  const dirty = fullInput() as unknown as Record<string, unknown>;
  dirty.north = SALT.stamp;
  dirty.horizon = [1, 2, 3];
  dirty.recording = 'on';
  dirty.captureLog = { 0: { at: 1 } };
  dirty.slices = SALT.stamp;
  const built = buildReport(dirty as unknown as ReportInput);
  assert.equal(built.north, null);
  assert.equal(built.horizon, null);
  assert.equal(built.recording, null);
  assert.equal(built.captureLog, null);
  assert.equal(built.slices, null);
  assertClean(JSON.stringify(built), 'wrong containers');
});

test('undefined leaves are omitted, not turned into null: an absent optional field stays absent', () => {
  const input = fullInput();
  const built = buildReport(input);
  assert.deepEqual(Object.keys(built.captureLog[2]), ['at', 'frameId', 'outcome']);
  assert.ok(!('detail' in built.captureLog[2]));
  const gappy = fullInput() as unknown as Record<string, unknown>;
  (gappy.timeline as Record<string, unknown>).beginMs = undefined;
  const b2 = buildReport(gappy as unknown as ReportInput);
  assert.ok(!('beginMs' in b2.timeline));
  assert.deepEqual(Object.keys(b2.timeline), ['finishMs', 'endedBy']);
});

test('buildReport shares nothing with its input: it is a copy at every depth', () => {
  const input = fullInput();
  const built = buildReport(input);
  assert.notEqual(built as unknown, input as unknown);
  assert.notEqual(built.camera, input.camera);
  assert.notEqual(built.camera.settings, input.camera.settings);
  assert.notEqual(built.sensors.events, input.sensors.events);
  assert.notEqual(built.sensors.events[0], input.sensors.events[0]);
  assert.notEqual(built.loop.preDeg, input.loop.preDeg);
  assert.notEqual(built.captureLog[1], input.captureLog[1]);
  assert.notEqual(built.slices, input.slices);
  // Changing the input afterwards does not reach the built report.
  const before = JSON.stringify(built);
  input.camera.settings.width = 1;
  input.sensors.events[0].total = 1;
  input.captureLog[0].at = 1;
  input.slices.push({ kf: 99, dataUrl: 'x' });
  assert.equal(JSON.stringify(built), before);
});

test('reportJson is two-space JSON that parses back to the built report', () => {
  const built = buildReport(fullInput());
  const text = reportJson(built);
  assert.deepEqual(JSON.parse(text), built);
  assert.ok(text.startsWith('{\n  "format": "astrodeck-pano-report",\n  "version": 2,\n'));
  assert.equal(text, JSON.stringify(built, null, 2));
  assert.deepEqual(JSON.parse(reportJson(buildReport(sparseInput()))), buildReport(sparseInput()));
});

test('statsOf is nearest-rank: the k-th smallest for k = ceil(p n / 100)', () => {
  assert.deepEqual(statsOf([]), { n: 0, p50: null, p95: null, max: null });
  assert.deepEqual(statsOf([7]), { n: 1, p50: 7, p95: 7, max: 7 });
  assert.deepEqual(statsOf([4, 2]), { n: 2, p50: 2, p95: 4, max: 4 });
  assert.deepEqual(statsOf([1, 2, 3, 4]), { n: 4, p50: 2, p95: 4, max: 4 });       // p50 is the lower middle, rank 2
  assert.deepEqual(statsOf([1, 2, 3, 4, 5]), { n: 5, p50: 3, p95: 5, max: 5 });
  const upTo = (n: number) => Array.from({ length: n }, (_, i) => i + 1);
  assert.deepEqual(statsOf(upTo(10)), { n: 10, p50: 5, p95: 10, max: 10 });         // ceil(9.5) = 10, not 9
  assert.deepEqual(statsOf(upTo(11)), { n: 11, p50: 6, p95: 11, max: 11 });         // ceil(10.45) = 11; a rounded rank gives 10
  assert.deepEqual(statsOf(upTo(19)), { n: 19, p50: 10, p95: 19, max: 19 });        // ceil(18.05) = 19; a rounded rank gives 18
  assert.deepEqual(statsOf(upTo(20)), { n: 20, p50: 10, p95: 19, max: 20 });        // ceil(19) = 19 exactly
  assert.deepEqual(statsOf(upTo(21)), { n: 21, p50: 11, p95: 20, max: 21 });        // ceil(19.95) = 20
  assert.deepEqual(statsOf(upTo(100)), { n: 100, p50: 50, p95: 95, max: 100 });
  assert.deepEqual(statsOf(upTo(101)), { n: 101, p50: 51, p95: 96, max: 101 });     // ceil(95.95) = 96
  assert.deepEqual(statsOf(upTo(1000)), { n: 1000, p50: 500, p95: 950, max: 1000 });
});

test('statsOf sorts numerically, leaves its input alone, and counts only finite values', () => {
  const values = [30, 4, 100, 9, 1, 25, 2];
  const copy = values.slice();
  assert.deepEqual(statsOf(values), { n: 7, p50: 9, p95: 100, max: 100 });          // text order would put 100 before 4
  assert.deepEqual(values, copy);
  assert.deepEqual(statsOf([5, 5, 5, 5]), { n: 4, p50: 5, p95: 5, max: 5 });
  assert.deepEqual(statsOf([-3, -1, -2]), { n: 3, p50: -2, p95: -1, max: -1 });
  assert.deepEqual(statsOf([1, NaN, 3, Infinity, 2, -Infinity]), { n: 3, p50: 2, p95: 3, max: 3 });
  assert.deepEqual(statsOf([NaN]), { n: 0, p50: null, p95: null, max: null });
  assert.deepEqual(statsOf([0.5, 0.25]), { n: 2, p50: 0.25, p95: 0.5, max: 0.5 });
});

test('neither source reads the clock: report.ts and recorder.ts name no Date, performance, timer or console', () => {
  for (const file of ['../report.ts', '../recorder.ts']) {
    const src = readFileSync(new URL(file, import.meta.url), 'utf8') as string;
    assert.ok(src.length > 2000, `read the real source of ${file}`);
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/[^\r\n]*/gm, '').replace(/\/\/[^\r\n]*$/gm, '');
    assert.ok(!/\bDate\b/.test(code), `${file} names Date`);
    assert.ok(!/\bperformance\b/.test(code), `${file} names performance`);
    assert.ok(!/\b(setTimeout|setInterval|requestAnimationFrame|Intl)\b/.test(code), `${file} schedules or formats time`);
    assert.ok(!/\bconsole\s*\./.test(code), `${file} uses console`);
    assert.ok(!/\bnavigator\b/.test(code), `${file} reads navigator`);
  }
});

console.log(`report.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
