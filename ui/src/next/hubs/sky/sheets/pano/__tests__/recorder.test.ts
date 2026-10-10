// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { decodePng } from '../../__sim__/png';
import { pngEncoder } from '../../__sim__/pngEncoder';
import { jpegEncoder, Recorder } from '../recorder';
import { buildReport } from '../report';
import type { FrameEncoder, ReportInput, ScanReport } from '../types';

// The recording is how a real phone's night becomes a replayable case
// (SPEC-v2 13.9): the camera frames and the sensor events, as JSON Lines the
// simulator imports. The lines are the observation lines of 13.1 with the
// frame's pixels inline.
//
// What each test pins, and the mutant it exists to catch:
//   * 'setEvery' keeps 12, 6 and 4 of 12 frames for 1, 2 and 3, names the kept
//     ids, and counts how often the encoder ran. The named mutant is `frame()`
//     ignoring `setEvery`: every case but the first then keeps all 12, and the
//     encoder runs on frames that are thrown away.
//   * 'a change of setEvery ...' fixes the rule as "counted from the last frame
//     kept", so lowering `every` acts at once and raising it never leaves a
//     stretch longer than the new `every`.
//   * the 'keepsNext' tests drive the recorder the way the scanner does (3.5
//     step 4, S24): ask `keepsNext`, build the pixels only when it is true, call
//     `frame()` on every delivered frame, and hand a skipped call an empty array
//     that throws if anything touches it. They pin that the answer matches what
//     `frame()` then does at `setEvery` 1, 2 and 3, across a change of `every`
//     (a hand-written schedule and a seeded sweep of every change at every phase)
//     and that reading the answer changes nothing. The named mutant is
//     `keepsNext` computed from the call count alone, `delivered % every === 0`:
//     it agrees with `frame()` while `every` never changes, so the 'setEvery 1,
//     2 and 3' case passes it and the change-of-every cases fail it.
//   * the line tests write the expected text of each observation line by hand,
//     so a renamed key, a reordered key or an extra key from a wider event
//     object all fail.
//   * 'a recording made with pngEncoder ...' decodes the base64 frames back and
//     compares every byte: the replay and the simulator rely on that being
//     lossless.
//   * 'times are written as given' uses fractional millisecond values, a null
//     capture time and large values: a rounded or dropped time fails.
//   * 'jpegEncoder ...' runs the phone's encoder against a stub canvas and pins
//     the order (resize, putImageData, toDataURL), the quality, the stripped
//     prefix and the refusal of a short buffer, a missing context and a PNG
//     answer.

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** A frame of seeded noise, every channel including alpha, so a lossless codec has something to lose. */
function noise(w: number, h: number, seed: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  let s = seed >>> 0;
  for (let i = 0; i < out.length; i++) { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; out[i] = s >>> 24; }
  return out;
}

const HEADER = {
  video: { width: 1080, height: 1920 },
  analysis: { width: 180, height: 320 },
  settings: {
    width: 1080, height: 1920, frameRate: 30, resizeMode: 'none', zoom: 1, focusMode: 'manual',
    focusDistance: 0, facingMode: 'environment', label: 'back camera 0',
  },
  commit: '3f9a1c2',
};

/** An encoder that records what it was asked and returns a short tag; the pixels are not interesting here. */
function stubEncoder() {
  const calls: { w: number; h: number; first: number }[] = [];
  const encoder: FrameEncoder = {
    mime: 'image/x-stub',
    encode(rgba, w, h) { calls.push({ w, h, first: rgba[0] }); return `STUB${w}x${h}-${rgba[0]}`; },
  };
  return { encoder, calls };
}

const fid = (n: number) => `f${String(n).padStart(6, '0')}`;
const lines = (text: string) => { assert.ok(text.endsWith('\n'), 'ends in a newline'); return text.slice(0, -1).split('\n'); };
const parsed = (text: string) => lines(text).map(l => JSON.parse(l) as Record<string, unknown>);

/** Deliver `n` frames, numbered from 1 by delivery, each tagged by its number in the first byte. */
function deliver(rec: Recorder, from: number, to: number) {
  for (let i = from; i <= to; i++) {
    const rgba = new Uint8ClampedArray(2 * 2 * 4); rgba[0] = i;
    rec.frame({ frameId: fid(i), tCaptureMs: i * 33, tPresentMs: i * 33 + 5, w: 2, h: 2, rgba });
  }
}

const reportInput = (): ReportInput => ({
  scanner: { commit: '3f9a1c2', sensorOnly: false },
  browser: { brands: ['Chromium'], mobile: true, platform: 'Android' },
  timeline: { beginMs: 100, finishMs: 9000, endedBy: 'user' },
  camera: {
    settings: HEADER.settings, bench: null, farbled: false, exposureReadable: null, analysis: { w: 180, h: 320 },
  },
  frames: {
    delivered: 6, viaRvfc: true, captureTimePresent: 6, slips: { pairs: 5, slips: 0 },
    lag: {
      nowMinusCapture: { n: 0, p50: null, p95: null, max: null },
      presentMinusCapture: { n: 0, p50: null, p95: null, max: null },
      expectedMinusNow: { n: 0, p50: null, p95: null, max: null },
    },
    intervalMs: { n: 5, p50: 33, p95: 34, max: 34 }, presentedGaps: 0,
  },
  sensors: {
    modeAtBegin: 'relative', modeChanges: [], events: [], movingHz: { relative: null, absolute: null, motion: null },
    rateByOmega: [], blocked: false, axis: null, gyroZeroTriples: 0, gyroSeenNonZero: true, staleRefusals: 0,
    staleLimitMs: { n: 0, p50: null, p95: null, max: null },
    latency: { priorMs: 80, tauMs: 80, sigmaMs: 40, pairs: 0, applied: false },
  },
  focal: {
    source: 'default', state: 'prior', fNorm: 1.2694, sdPct: 20, ratios: 0, shortFovDeg: 62,
    closurePct: null, gyroScale: null, ultraWideSuspected: false,
  },
  loop: { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 30 },
  north: null,
  declinationApplied: false,
  keyframes: {
    total: 2, aligned: 0, blurred: 0, sensor: 2,
    ms: { n: 0, p50: null, p95: null, max: null }, readbackMs: { n: 0, p50: null, p95: null, max: null },
    innovationSteadyDeg: { n: 0, p50: null, p95: null, max: null }, innovationAccelDeg: { n: 0, p50: null, p95: null, max: null },
    perSliceMs: { n: 0, p50: null, p95: null, max: null },
  },
  liveFrame: { drawMs: { n: 0, p50: null, p95: null, max: null }, redraws: 0 },
  horizon: null,
  recording: { on: true, every: 1, frames: 6 },
  captureLog: [{ at: 120, frameId: 1, outcome: 'accepted', detail: 'first', kf: 0 }],
  slices: [],
  error: null,
});
const report = (): ScanReport => buildReport(reportInput());

test('header, frames, events, actions and the report come out in the order they arrived', () => {
  const rec = new Recorder({ encoder: pngEncoder, header: HEADER });
  rec.action(100, 'begin');
  rec.orientation({ event: 'deviceorientation', tEventMs: 110, tReceiveMs: 112, alpha: 1, beta: 2, gamma: 3, absolute: false });
  deliver(rec, 1, 1);
  rec.motion({ tEventMs: 120, tReceiveMs: 121, rate: { alpha: 0.1, beta: 18.4, gamma: -7.8 } });
  rec.orientation({ event: 'deviceorientationabsolute', tEventMs: 125, tReceiveMs: 126, alpha: 101.4, beta: 67, gamma: -1.2, absolute: true });
  deliver(rec, 2, 2);
  rec.action(900, 'finish');
  const text = rec.finish(report());
  const kinds = parsed(text).map(l => l.kind);
  assert.deepEqual(kinds, ['header', 'action', 'orientation', 'frame', 'motion', 'orientation', 'frame', 'action', 'report']);
  const all = parsed(text);
  assert.deepEqual(all.filter(l => l.kind === 'frame').map(l => l.frame_id), ['f000001', 'f000002']);
  assert.deepEqual(all.filter(l => l.kind === 'action').map(l => [l.t_ms, l.action]), [[100, 'begin'], [900, 'finish']]);
});

test('the header line names the format, the encoder and the camera, and carries only declared fields', () => {
  const rec = new Recorder({ encoder: pngEncoder, header: HEADER });
  assert.equal(lines(rec.finish(null))[0],
    '{"kind":"header","format":"astrodeck-pano-recording","version":1,"encoder":"image/png",'
    + '"video":{"width":1080,"height":1920},"analysis":{"width":180,"height":320},'
    + '"settings":{"width":1080,"height":1920,"frameRate":30,"resizeMode":"none","zoom":1,"focusMode":"manual",'
    + '"focusDistance":0,"facingMode":"environment","label":"back camera 0"},"commit":"3f9a1c2"}');
  // A wider object than the type asks for loses its extras; a missing setting is written as null.
  const wide = {
    ...HEADER, lat: 12.345678, deviceId: 'abc',
    video: { width: 1, height: 2, lon: 98.765432 },
    settings: { width: 640, deviceId: 'abc', label: 'cam' },
  } as unknown as ConstructorParameters<typeof Recorder>[0]['header'];
  const head = parsed(new Recorder({ encoder: stubEncoder().encoder, header: wide }).finish(null))[0];
  assert.deepEqual(head, {
    kind: 'header', format: 'astrodeck-pano-recording', version: 1, encoder: 'image/x-stub',
    video: { width: 1, height: 2 }, analysis: { width: 180, height: 320 },
    settings: { width: 640, height: null, frameRate: null, resizeMode: null, zoom: null, focusMode: null, focusDistance: null, facingMode: null, label: 'cam' },
    commit: '3f9a1c2',
  });
  assert.ok(!JSON.stringify(head).includes('12.345678'));
  assert.equal(parsed(new Recorder({ encoder: pngEncoder, header: { ...HEADER, commit: null } }).finish(null))[0].commit, null);
});

test('observation lines are the 13.1 lines, key for key; a frame carries mime and data in place of file', () => {
  const { encoder } = stubEncoder();
  const rec = new Recorder({ encoder, header: HEADER });
  const rgba = new Uint8ClampedArray(3 * 2 * 4); rgba[0] = 9;
  rec.frame({ frameId: 'f000123', tCaptureMs: 12300, tPresentMs: 12360, w: 3, h: 2, rgba });
  rec.frame({ frameId: 'f000124', tCaptureMs: null, tPresentMs: 12393, w: 3, h: 2, rgba });
  rec.orientation({ event: 'deviceorientation', tEventMs: 12280, tReceiveMs: 12285, alpha: 12.3, beta: 67, gamma: -1.2, absolute: false });
  rec.orientation({ event: 'deviceorientationabsolute', tEventMs: 12280, tReceiveMs: 12285, alpha: 101.4, beta: 67, gamma: -1.2, absolute: true });
  rec.orientation({ event: 'deviceorientation', tEventMs: 12290, tReceiveMs: 12291, alpha: null, beta: null, gamma: null, absolute: null });
  rec.motion({ tEventMs: 12290, tReceiveMs: 12295, rate: { alpha: 0.1, beta: 18.4, gamma: -7.8 } });
  rec.motion({ tEventMs: 12300, tReceiveMs: 12301, rate: null });
  rec.action(12000, 'begin');
  const got = lines(rec.finish(null)).slice(1);
  assert.deepEqual(got, [
    '{"kind":"frame","frame_id":"f000123","t_capture_ms":12300,"t_present_ms":12360,"width":3,"height":2,"mime":"image/x-stub","data":"STUB3x2-9"}',
    '{"kind":"frame","frame_id":"f000124","t_capture_ms":null,"t_present_ms":12393,"width":3,"height":2,"mime":"image/x-stub","data":"STUB3x2-9"}',
    '{"kind":"orientation","event":"deviceorientation","t_event_ms":12280,"t_receive_ms":12285,"alpha":12.3,"beta":67,"gamma":-1.2,"absolute":false}',
    '{"kind":"orientation","event":"deviceorientationabsolute","t_event_ms":12280,"t_receive_ms":12285,"alpha":101.4,"beta":67,"gamma":-1.2,"absolute":true}',
    '{"kind":"orientation","event":"deviceorientation","t_event_ms":12290,"t_receive_ms":12291,"alpha":null,"beta":null,"gamma":null,"absolute":null}',
    '{"kind":"motion","t_event_ms":12290,"t_receive_ms":12295,"rate":{"alpha":0.1,"beta":18.4,"gamma":-7.8}}',
    '{"kind":"motion","t_event_ms":12300,"t_receive_ms":12301,"rate":null}',
    '{"kind":"action","t_ms":12000,"action":"begin"}',
  ]);
});

test('a wider event than the type asks for is written with its declared fields only', () => {
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  const o = { event: 'deviceorientation', tEventMs: 1, tReceiveMs: 2, alpha: 3, beta: 4, gamma: 5, absolute: true, lat: 12.345678, webkitCompassHeading: 77.5 };
  const m = { tEventMs: 6, tReceiveMs: 7, rate: { alpha: 1, beta: 2, gamma: 3, lon: 98.765432 }, interval: 16 };
  rec.orientation(o as unknown as Parameters<Recorder['orientation']>[0]);
  rec.motion(m as unknown as Parameters<Recorder['motion']>[0]);
  const text = rec.finish(null);
  assert.deepEqual(Object.keys(parsed(text)[1]), ['kind', 'event', 't_event_ms', 't_receive_ms', 'alpha', 'beta', 'gamma', 'absolute']);
  assert.deepEqual(parsed(text)[2], { kind: 'motion', t_event_ms: 6, t_receive_ms: 7, rate: { alpha: 1, beta: 2, gamma: 3 } });
  assert.ok(!text.includes('12.345678') && !text.includes('98.765432') && !text.includes('77.5'));
});

test('times are written as given: fractions, large values and a null capture time are not rounded or dropped', () => {
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  const rgba = new Uint8ClampedArray(4);
  rec.frame({ frameId: 'f000001', tCaptureMs: 12345.678901, tPresentMs: 98765432.109375, w: 1, h: 1, rgba });
  rec.frame({ frameId: 'f000002', tCaptureMs: null, tPresentMs: 0.0001, w: 1, h: 1, rgba });
  rec.orientation({ event: 'deviceorientation', tEventMs: 1000.123456789, tReceiveMs: 1000.5, alpha: 359.99999, beta: -179.5, gamma: 0, absolute: false });
  rec.motion({ tEventMs: 2000.000001, tReceiveMs: 2000.25, rate: { alpha: 0, beta: -0.0625, gamma: 1e-7 } });
  rec.action(3000.75, 'finish');
  const all = parsed(rec.finish(null));
  assert.equal(all[1].t_capture_ms, 12345.678901);
  assert.equal(all[1].t_present_ms, 98765432.109375);
  assert.equal(all[2].t_capture_ms, null);
  assert.equal(all[2].t_present_ms, 0.0001);
  assert.equal(all[3].t_event_ms, 1000.123456789);
  assert.equal(all[3].t_receive_ms, 1000.5);
  assert.equal(all[3].alpha, 359.99999);
  assert.equal(all[4].t_event_ms, 2000.000001);
  assert.deepEqual(all[4].rate, { alpha: 0, beta: -0.0625, gamma: 1e-7 });
  assert.equal(all[5].t_ms, 3000.75);
});

test('setEvery(1 | 2 | 3) keeps every n-th delivered frame, the first included, and encodes only the frames it keeps', () => {
  const expected: Record<1 | 2 | 3, number[]> = {
    1: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    2: [1, 3, 5, 7, 9, 11],
    3: [1, 4, 7, 10],
  };
  for (const n of [1, 2, 3] as const) {
    const { encoder, calls } = stubEncoder();
    const rec = new Recorder({ encoder, header: HEADER });
    assert.equal(rec.every, 1, 'the default keeps every frame');
    rec.setEvery(n);
    assert.equal(rec.every, n);
    deliver(rec, 1, 12);
    assert.equal(rec.frames, expected[n].length, `every ${n}: frames`);
    assert.equal(calls.length, expected[n].length, `every ${n}: the encoder ran only for kept frames`);
    const written = parsed(rec.finish(null)).filter(l => l.kind === 'frame');
    assert.deepEqual(written.map(l => l.frame_id), expected[n].map(fid), `every ${n}: ids keep their delivery number`);
    assert.deepEqual(written.map(l => l.data), expected[n].map(i => `STUB2x2-${i}`), `every ${n}: the kept frame is the one delivered`);
  }
});

test('a change of setEvery acts at once and counts from the last frame kept', () => {
  const { encoder } = stubEncoder();
  const rec = new Recorder({ encoder, header: HEADER });
  deliver(rec, 1, 3);                 // every 1: 1, 2, 3
  rec.setEvery(3);
  deliver(rec, 4, 9);                 // last kept is 3, so the next is 6, then 9
  rec.setEvery(1);
  deliver(rec, 10, 11);               // every frame again at once
  rec.setEvery(2);
  deliver(rec, 12, 16);               // last kept is 11: 13, 15
  const ids = parsed(rec.finish(null)).filter(l => l.kind === 'frame').map(l => l.frame_id);
  assert.deepEqual(ids, [1, 2, 3, 6, 9, 10, 11, 13, 15].map(fid));
  assert.equal(rec.frames, 9);
});

/** A zero-length array that throws when anything touches it: what a scanner hands frame() for a frame it did not read back. */
function unreadable(): Uint8ClampedArray {
  const trap = (what: string) => () => { throw new Error(`the rgba of a skipped frame was ${what}`); };
  return new Proxy(new Uint8ClampedArray(0), {
    get: trap('read'), has: trap('probed'), set: trap('written'), ownKeys: trap('listed'), getOwnPropertyDescriptor: trap('described'),
  });
}

interface Decision { call: number; predicted: boolean; kept: boolean }

/**
 * Deliver frames `from`..`to` the way the scanner does (3.5 step 4): ask keepsNext,
 * build the pixels (tagged with the call number) only when it says yes, and call
 * frame() on every delivered frame. `during(call)` runs before the question, where
 * a test changes `every`. Returns what was predicted and what frame() then did.
 */
function scan(rec: Recorder, from: number, to: number, during?: (call: number) => void): Decision[] {
  const out: Decision[] = [];
  for (let call = from; call <= to; call++) {
    during?.(call);
    const predicted = rec.keepsNext;
    assert.equal(rec.keepsNext, predicted, `call ${call}: reading keepsNext twice gives the same answer`);
    let rgba: Uint8ClampedArray;
    if (predicted) { rgba = new Uint8ClampedArray(2 * 2 * 4); rgba[0] = call; } else rgba = unreadable();
    const before = rec.frames;
    rec.frame({ frameId: fid(call), tCaptureMs: call * 33, tPresentMs: call * 33 + 5, w: 2, h: 2, rgba });
    out.push({ call, predicted, kept: rec.frames === before + 1 });
  }
  return out;
}

const predictions = (d: Decision[]) => d.map(x => x.predicted);
const keptCalls = (d: Decision[]) => d.filter(x => x.kept).map(x => x.call);
/** Change `every` and check at once, before any call, what keepsNext says about the next one. */
const setEveryNow = (r: Recorder, n: 1 | 2 | 3, expectNow: boolean) => {
  r.setEvery(n);
  assert.equal(r.keepsNext, expectNow, `right after setEvery(${n})`);
};

test('keepsNext says, before the call, whether frame() will keep it, under setEvery 1, 2 and 3', () => {
  const T = true, F = false;
  const pattern: Record<1 | 2 | 3, boolean[]> = {
    1: [T, T, T, T, T, T, T, T, T, T, T, T],
    2: [T, F, T, F, T, F, T, F, T, F, T, F],
    3: [T, F, F, T, F, F, T, F, F, T, F, F],
  };
  for (const n of [1, 2, 3] as const) {
    const { encoder, calls } = stubEncoder();
    const rec = new Recorder({ encoder, header: HEADER });
    assert.equal(rec.keepsNext, true, 'a new recording keeps its first frame');
    rec.setEvery(n);
    assert.equal(rec.keepsNext, true, `every ${n}: the first frame is kept whatever every is`);
    const got = scan(rec, 1, 12);
    assert.deepEqual(predictions(got), pattern[n], `every ${n}: what keepsNext said before each call`);
    assert.deepEqual(got.map(x => x.kept), pattern[n], `every ${n}: what frame() then did`);
    // The pixels were built only for the calls that were kept, and those are the ones the encoder saw.
    assert.deepEqual(calls.map(c => c.first), keptCalls(got), `every ${n}: the encoder saw exactly the frames that were read back`);
    assert.deepEqual(parsed(rec.finish(null)).filter(l => l.kind === 'frame').map(l => l.frame_id), keptCalls(got).map(fid));
  }
});

test('keepsNext follows a change of every: lowering acts at once, raising waits out the stretch from the last kept frame', () => {
  const { encoder } = stubEncoder();
  const rec = new Recorder({ encoder, header: HEADER });
  const got: Decision[] = [];
  got.push(...scan(rec, 1, 3));                         // every 1: 1, 2, 3 kept
  setEveryNow(rec, 3, false);                                         // 3 was kept a moment ago: the next two are skipped
  got.push(...scan(rec, 4, 9));                         // 4 F, 5 F, 6 T, 7 F, 8 F, 9 T
  setEveryNow(rec, 1, true);                                          // every frame again at once
  got.push(...scan(rec, 10, 11));
  setEveryNow(rec, 2, false);                                         // 11 was kept, so 12 is skipped
  got.push(...scan(rec, 12, 16));                       // 12 F, 13 T, 14 F, 15 T, 16 F
  const T = true, F = false;
  assert.deepEqual(predictions(got), [T, T, T, F, F, T, F, F, T, T, T, F, T, F, T, F]);
  assert.deepEqual(got.map(x => x.kept), predictions(got), 'frame() did what keepsNext said, call by call');
  assert.deepEqual(keptCalls(got), [1, 2, 3, 6, 9, 10, 11, 13, 15]);

  // A change in the middle of a stretch, both ways, on a fresh recording.
  const mid = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  mid.setEvery(2);
  const a = scan(mid, 1, 2);                            // 1 T, 2 F
  setEveryNow(mid, 3, false);                                   // raised to 3 with call 2 skipped: call 3 is two after the kept one and three are needed
  const b = scan(mid, 3, 4);                            // 3 F, 4 T
  setEveryNow(mid, 2, false);                                   // lowered to 2 right after a kept frame: call 5 is one after it and two are needed
  const c = scan(mid, 5, 6);                            // 5 F, 6 T
  setEveryNow(mid, 1, true);
  const d = scan(mid, 7, 8);                            // 7 T, 8 T
  const all = [...a, ...b, ...c, ...d];
  assert.deepEqual(predictions(all), [T, F, F, T, F, T, T, T]);
  assert.deepEqual(all.map(x => x.kept), predictions(all));
});

test('keepsNext matches frame() over a seeded run of 600 calls with every changed at random, at every phase', () => {
  let s = 20261010;
  const next = (m: number) => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return (s >>> 8) % m; };
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  let changes = 0;
  const seen = new Set<string>();
  let every = 1;
  const got = scan(rec, 1, 600, () => {
    if (next(4) === 0) {
      const to = (1 + next(3)) as 1 | 2 | 3;
      seen.add(`${every}>${to}`);
      rec.setEvery(to); every = to; changes++;
    }
  });
  assert.deepEqual(got.map(x => x.kept), predictions(got), 'frame() did what keepsNext said on every call');
  // The run is not vacuous: it changed every often, passed through all nine pairs, and both kept and skipped plenty.
  assert.ok(changes > 100, `every changed ${changes} times`);
  assert.equal(seen.size, 9, `pairs seen: ${[...seen].sort().join(' ')}`);
  assert.ok(got.filter(x => x.kept).length > 150 && got.filter(x => !x.kept).length > 150);
  assert.equal(rec.frames, keptCalls(got).length);
  // No stretch of skipped calls is longer than the largest every in force.
  let run = 0, longest = 0;
  for (const x of got) { run = x.kept ? 0 : run + 1; longest = Math.max(longest, run); }
  assert.ok(longest <= 2, `longest skipped stretch ${longest}`);
});

test('a scanner that reads back only when keepsNext is true records exactly what one that reads back every frame does', () => {
  // The S24 defect: deciding "kept" in the scanner and again in frame() keeps 1 in 4 at every = 2.
  for (const n of [1, 2, 3] as const) {
    const guided = stubEncoder();
    const rg = new Recorder({ encoder: guided.encoder, header: HEADER });
    rg.setEvery(n);
    const got = scan(rg, 1, 12);
    const readbacks = got.filter(x => x.predicted).length;

    const all = stubEncoder();
    const ra = new Recorder({ encoder: all.encoder, header: HEADER });
    ra.setEvery(n);
    deliver(ra, 1, 12);                                  // a full readback for every frame

    assert.equal(rg.finish(null), ra.finish(null), `every ${n}: the same recording`);
    assert.equal(rg.frames, Math.ceil(12 / n), `every ${n}: one frame in ${n} kept, not one in ${n * n}`);
    assert.equal(readbacks, rg.frames, `every ${n}: read back once per kept frame`);
    assert.equal(guided.calls.length, all.calls.length);
    // The delivery number still advances on a skipped call, so the next kept frame has its own id.
    assert.deepEqual(parsed(rg.finish(null)).filter(l => l.kind === 'frame').map(l => l.frame_id), keptCalls(got).map(fid));
  }
});

test('a skipped call is given an empty array and never reads it: no line, no encoder run, and a plain empty array does the same', () => {
  const trapped = stubEncoder();
  const rec = new Recorder({ encoder: trapped.encoder, header: HEADER });
  rec.setEvery(3);
  const first = new Uint8ClampedArray(2 * 2 * 4); first[0] = 1;
  rec.frame({ frameId: fid(1), tCaptureMs: 33, tPresentMs: 38, w: 2, h: 2, rgba: first });
  const before = rec.finish(null);
  const skipped = unreadable();
  assert.equal(rec.keepsNext, false);
  rec.frame({ frameId: fid(2), tCaptureMs: 66, tPresentMs: 71, w: 2, h: 2, rgba: skipped });
  assert.equal(rec.keepsNext, false);
  rec.frame({ frameId: fid(3), tCaptureMs: 99, tPresentMs: 104, w: 2, h: 2, rgba: skipped });
  assert.equal(rec.finish(null), before, 'two skipped calls wrote nothing');
  assert.equal(trapped.calls.length, 1, 'the encoder ran for the kept frame only');
  assert.equal(rec.frames, 1);
  assert.equal(rec.keepsNext, true);
  const fourth = new Uint8ClampedArray(2 * 2 * 4); fourth[0] = 4;
  rec.frame({ frameId: fid(4), tCaptureMs: 132, tPresentMs: 137, w: 2, h: 2, rgba: fourth });
  assert.deepEqual(parsed(rec.finish(null)).filter(l => l.kind === 'frame').map(l => l.frame_id), [fid(1), fid(4)]);

  // What the skipped call is handed does not matter: a plain zero-length array gives the same text.
  const plain = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  plain.setEvery(3);
  plain.frame({ frameId: fid(1), tCaptureMs: 33, tPresentMs: 38, w: 2, h: 2, rgba: first });
  plain.frame({ frameId: fid(2), tCaptureMs: 66, tPresentMs: 71, w: 2, h: 2, rgba: new Uint8ClampedArray(0) });
  plain.frame({ frameId: fid(3), tCaptureMs: 99, tPresentMs: 104, w: 2, h: 2, rgba: new Uint8ClampedArray(0) });
  plain.frame({ frameId: fid(4), tCaptureMs: 132, tPresentMs: 137, w: 2, h: 2, rgba: fourth });
  assert.equal(plain.finish(null), rec.finish(null));

  // The trap itself works: a recorder that touched a skipped array would fail here, not pass silently.
  assert.throws(() => skipped.length, /skipped frame was read/);
  assert.throws(() => skipped[0], /skipped frame was read/);
});

test('setEvery refuses anything but 1, 2 or 3 instead of silently dropping the recording', () => {
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  for (const bad of [0, 4, -1, 1.5, NaN, Infinity, '2']) {
    assert.throws(() => rec.setEvery(bad as unknown as 1), RangeError, `setEvery(${String(bad)})`);
  }
  assert.equal(rec.every, 1, 'a refused value changes nothing');
  deliver(rec, 1, 3);
  assert.equal(rec.frames, 3);
});

test('frames, events and chars count what was written: events are the sensor lines, chars is the text without the report', () => {
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  assert.deepEqual([rec.frames, rec.events], [0, 0]);
  assert.equal(rec.chars, rec.finish(null).length, 'the header alone');
  rec.setEvery(2);
  deliver(rec, 1, 5);
  rec.orientation({ event: 'deviceorientation', tEventMs: 1, tReceiveMs: 2, alpha: 3, beta: 4, gamma: 5, absolute: false });
  rec.motion({ tEventMs: 3, tReceiveMs: 4, rate: null });
  rec.motion({ tEventMs: 5, tReceiveMs: 6, rate: null });
  rec.action(7, 'begin');
  assert.equal(rec.frames, 3);
  assert.equal(rec.events, 3, 'two motion and one orientation; the action is not an event');
  const bare = rec.finish(null);
  assert.equal(rec.chars, bare.length);
  const full = rec.finish(report());
  assert.ok(full.length > bare.length);
  assert.equal(rec.chars, bare.length, 'finish does not change the count');
  assert.ok(full.startsWith(bare), 'the report is appended, not woven in');
});

test('finish: the report is the last line and equals the report; none is written for null', () => {
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  deliver(rec, 1, 2);
  const none = parsed(rec.finish(null));
  assert.deepEqual(none.map(l => l.kind), ['header', 'frame', 'frame']);
  const r = report();
  const text = rec.finish(r);
  const all = parsed(text);
  assert.equal(all[all.length - 1].kind, 'report');
  assert.deepEqual(Object.keys(all[all.length - 1]), ['kind', 'report']);
  assert.deepEqual(all[all.length - 1].report, JSON.parse(JSON.stringify(r)));
  assert.equal(all.length, 4);
  // finish is a reading, not a closing: the same call twice gives the same text.
  assert.equal(rec.finish(r), text);
});

test('finish writes the report through buildReport: a stray coordinate, declination or Date on the report object is dropped', () => {
  const rec = new Recorder({ encoder: stubEncoder().encoder, header: HEADER });
  const dirty = JSON.parse(JSON.stringify(report())) as Record<string, unknown>;
  dirty.lat = 12.345678;
  dirty.declinationDeg = 7.8901234;
  dirty.north = null;
  (dirty.timeline as Record<string, unknown>).stamp = new Date(Date.UTC(2031, 3, 5, 6, 7, 8));
  (dirty.camera as Record<string, unknown>).photoKey = 'photo-key-SENTINEL';
  (dirty.timeline as Record<string, unknown>).beginMs = new Date(Date.UTC(2031, 3, 5, 6, 7, 8));
  const text = rec.finish(dirty as unknown as ScanReport);
  for (const needle of ['12.345678', '7.8901234', '2031-04-05', 'SENTINEL', 'declinationDeg', 'photoKey', '"lat"']) {
    assert.ok(!text.includes(needle), `contains ${needle}`);
  }
  const written = parsed(text).pop()!.report as ScanReport;
  assert.equal(written.timeline.beginMs, null, 'a Date in a number slot is not written');
  assert.deepEqual(written.camera, report().camera);
});

test('every line is JSON on a line of its own, the text ends in a newline, and no line holds a raw newline', () => {
  const rec = new Recorder({ encoder: pngEncoder, header: HEADER });
  for (let i = 1; i <= 4; i++) rec.frame({ frameId: fid(i), tCaptureMs: null, tPresentMs: i, w: 6, h: 5, rgba: noise(6, 5, i) });
  rec.orientation({ event: 'deviceorientation', tEventMs: 1, tReceiveMs: 2, alpha: 3, beta: 4, gamma: 5, absolute: false });
  const text = rec.finish(report());
  assert.ok(text.endsWith('\n') && !text.endsWith('\n\n'));
  assert.ok(!text.includes('\r'));
  const ls = lines(text);
  assert.equal(ls.length, 1 + 4 + 1 + 1);
  for (const l of ls) assert.doesNotThrow(() => JSON.parse(l));
});

test('a recording made with pngEncoder decodes back to exactly the pixels that were recorded', () => {
  const rec = new Recorder({ encoder: pngEncoder, header: HEADER });
  const sizes: [number, number][] = [[1, 1], [3, 5], [180, 320], [240, 320]];
  const sent = sizes.map(([w, h], i) => ({ w, h, rgba: noise(w, h, 1000 + i) }));
  sent.forEach((f, i) => rec.frame({ frameId: fid(i + 1), tCaptureMs: i, tPresentMs: i + 1, w: f.w, h: f.h, rgba: f.rgba }));
  const frames = parsed(rec.finish(null)).filter(l => l.kind === 'frame');
  assert.equal(frames.length, 4);
  frames.forEach((line, i) => {
    assert.equal(line.mime, 'image/png');
    assert.equal(line.width, sent[i].w);
    assert.equal(line.height, sent[i].h);
    const data = line.data as string;
    assert.ok(!data.startsWith('data:'), 'no data: prefix');
    const back = decodePng(Buffer.from(data, 'base64'));
    assert.equal(back.width, sent[i].w);
    assert.equal(back.height, sent[i].h);
    assert.ok(Buffer.from(back.pixels).equals(Buffer.from(sent[i].rgba)), `frame ${i}: every byte, alpha included`);
  });
});

test('pngEncoder: image/png, base64 of a PNG, the same bytes for the same pixels, a size mismatch refused', () => {
  assert.equal(pngEncoder.mime, 'image/png');
  const px = noise(8, 4, 7);
  const a = pngEncoder.encode(px, 8, 4);
  assert.equal(typeof a, 'string');
  assert.ok(a.startsWith('iVBORw0KGgo'), 'the PNG signature in base64');
  assert.ok(/^[A-Za-z0-9+/]+=*$/.test(a));
  assert.equal(pngEncoder.encode(noise(8, 4, 7), 8, 4), a);
  assert.notEqual(pngEncoder.encode(noise(8, 4, 8), 8, 4), a);
  assert.throws(() => pngEncoder.encode(px, 8, 5), /does not match/);
});

/** A stub canvas that records the order of what the encoder does to it. */
function fakeCanvas(o: { context?: boolean; url?: (type: string, quality: number | undefined, pixels: Uint8ClampedArray) => string } = {}) {
  const log: string[] = [];
  const state = { created: 0, last: new Uint8ClampedArray(0), putAt: [] as number[][] };
  const ctx = {
    createImageData(w: number, h: number) { state.created++; log.push(`createImageData ${w}x${h}`); return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; },
    putImageData(img: { data: Uint8ClampedArray }, x: number, y: number) {
      state.last = img.data.slice(); state.putAt.push([x, y, canvas.width, canvas.height]); log.push(`putImageData ${x},${y} on ${canvas.width}x${canvas.height}`);
    },
  };
  const canvas = {
    width: 300, height: 150,
    getContext(kind: string) { log.push(`getContext ${kind}`); return o.context === false ? null : ctx; },
    toDataURL(type: string, quality?: number) {
      log.push(`toDataURL ${type} ${quality}`);
      return (o.url ?? ((_t, _q, px) => `data:image/jpeg;base64,${Buffer.from(px.slice(0, 6)).toString('base64')}`))(type, quality, state.last);
    },
  };
  return { canvas: canvas as unknown as HTMLCanvasElement, log, state };
}

test('jpegEncoder: resize, putImageData, toDataURL at quality 0.85, the prefix stripped, all synchronous', () => {
  const { canvas, log, state } = fakeCanvas();
  const enc = jpegEncoder(canvas);
  assert.equal(enc.mime, 'image/jpeg');
  const px = noise(180, 320, 3);
  const out = enc.encode(px, 180, 320);
  assert.equal(typeof out, 'string');
  assert.equal(out, Buffer.from(px.slice(0, 6)).toString('base64'), 'the pixels reached the canvas before toDataURL, and no prefix');
  assert.deepEqual(log, ['getContext 2d', 'createImageData 180x320', 'putImageData 0,0 on 180x320', 'toDataURL image/jpeg 0.85']);
  assert.equal(canvas.width, 180);
  assert.equal(canvas.height, 320);
  assert.ok(Buffer.from(state.last).equals(Buffer.from(px)), 'the whole frame was put');
});

test('jpegEncoder: a given quality is passed on; the canvas follows the frame size; the ImageData is reused per size', () => {
  const { canvas, log, state } = fakeCanvas();
  const enc = jpegEncoder(canvas, 0.6);
  enc.encode(noise(4, 3, 1), 4, 3);
  enc.encode(noise(4, 3, 2), 4, 3);
  assert.equal(state.created, 1, 'one ImageData for two frames of one size');
  assert.ok(log.includes('toDataURL image/jpeg 0.6'));
  const first = noise(4, 3, 5);
  const second = noise(4, 3, 6);
  assert.equal(enc.encode(first, 4, 3), Buffer.from(first.slice(0, 6)).toString('base64'));
  assert.equal(enc.encode(second, 4, 3), Buffer.from(second.slice(0, 6)).toString('base64'), 'the second frame is not the first');
  enc.encode(noise(2, 6, 9), 2, 6);
  assert.equal(state.created, 2, 'a new size makes a new ImageData');
  assert.deepEqual(state.putAt[state.putAt.length - 1], [0, 0, 2, 6]);
});

test('jpegEncoder refuses a buffer of the wrong size, a canvas with no 2d context, and a browser that answers with a PNG', () => {
  const short = fakeCanvas();
  assert.throws(() => jpegEncoder(short.canvas).encode(new Uint8ClampedArray(4 * 4 * 4 - 4), 4, 4), /not a 4 x 4 RGBA frame/);
  assert.equal(short.state.putAt.length, 0, 'nothing was put, so no stale pixels can be encoded');
  assert.throws(() => jpegEncoder(fakeCanvas({ context: false }).canvas).encode(new Uint8ClampedArray(16), 2, 2), /no 2d context/);
  const png = fakeCanvas({ url: () => 'data:image/png;base64,AAAA' });
  assert.throws(() => jpegEncoder(png.canvas).encode(new Uint8ClampedArray(16), 2, 2), /did not encode a JPEG/);
});

test('a recording with the phone encoder holds the stripped base64 under mime image/jpeg', () => {
  const { canvas } = fakeCanvas();
  const rec = new Recorder({ encoder: jpegEncoder(canvas), header: HEADER });
  const px = noise(5, 2, 4);
  rec.frame({ frameId: 'f000001', tCaptureMs: 1, tPresentMs: 2, w: 5, h: 2, rgba: px });
  const all = parsed(rec.finish(null));
  assert.equal(all[0].encoder, 'image/jpeg');
  assert.equal(all[1].mime, 'image/jpeg');
  assert.equal(all[1].data, Buffer.from(px.slice(0, 6)).toString('base64'));
});

test('the two sources import only each other and the types: nothing from __sim__ or __tests__', () => {
  const imports = (file: string) => {
    const src = readFileSync(new URL(file, import.meta.url), 'utf8') as string;
    assert.ok(src.length > 2000, `read the real source of ${file}`);
    return (src.match(/^[ \t]*import\b[^\r\n]*/gm) ?? []).map(l => l.replace(/\s+/g, ' ').trim());
  };
  assert.deepEqual(imports('../report.ts'), ["import type { ReportInput, ScanReport, Stats } from './types';"]);
  assert.deepEqual(imports('../recorder.ts'), [
    "import { buildReport } from './report';",
    "import type { CameraSettingsReport, FrameEncoder, RecordingHeader, ScanReport } from './types';",
  ]);
  const png = readFileSync(new URL('../../__sim__/pngEncoder.ts', import.meta.url), 'utf8') as string;
  assert.deepEqual((png.match(/^[ \t]*import\b[^\r\n]*/gm) ?? []).map(l => l.trim()),
    ["import type { FrameEncoder } from '../pano/types';", "import { encodePng } from './png';"]);
});

console.log(`recorder.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
