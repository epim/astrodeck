// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T12: the replay seam. The replay driver no longer knows one scanner; it drives
// whatever the SCANNERS registry names through the `ScannerUnderTest` interface,
// and the harness it plays a case into grew the stand-ins a second scanner
// needs. Three things are proved here.
//
//   1. The legacy scanner's result files did not move. The SHA-256 of each file
//      the replay wrote for the two committed fixtures BEFORE this change (at
//      fc892251, by running the then-current replay.ts on a copy of each
//      fixture's input/) are the goldens below, and `summary.json` is compared
//      with `app_commit` removed because that key names the tree.
//   2. A scanner defined in this file, the probe, is handed what the case says
//      in the form a browser would hand it - a relative event as
//      `deviceorientation` with `absolute: false`, null angles as null, a frame
//      without `captureTime` as metadata without the key - and every result
//      file the replay then writes matches the 13.7 schemas.
//   3. `scanner.json` is read with defaults, and an unknown scanner is a clear
//      error before anything is touched.
//
// Mutants this file must catch (SPEC-v2 8.3, T12):
//   (a) `cells()` dropped from the legacy adapter: `summary.json` of both
//       fixtures goes null in `cells_total` and `cells_covered`, and the
//       legacy byte-identity case for `summary.json` fails.
//   (b) every orientation dispatched as `deviceorientationabsolute` (the
//       harness ignoring `reading.event`): the probe's relative event arrives
//       as the wrong type, and the probe cases fail.
//   (d) every frame callback recorded as 0 ms: the probe's callbacks each wait
//       2 ms on `hrtime`, so `cost_ms.callback.p50` must be at least 2.
//   (e) the 1080 x 300 shape check dropped from the replay: a 300 x 1080 mosaic
//       of the right byte count is written as a panorama instead of refused.
// And one thing this file must survive: a second scanner registered beside
// `legacy` (T22 registers `pano`; this file is not in its list). Nothing here
// may use a name that task will register as one that does not exist.
//
// T32 removes the legacy scanner, `legacyAdapter.ts` and both chartyard-shortpan
// fixtures. Section 1 (legacy byte identity) and every case that relies on
// `legacy` being the default go with them, so T32 has to edit this file.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PANO_H, PANO_W, type ScannerOptions } from '../pano/types';
import { createHarness, referenceLoopMs } from '../__sim__/harness';
import { decodePng, encodePng } from '../__sim__/png';
import { readScannerOptions, replayCase, statsOf, type Summary } from '../__sim__/replay';
import type {
  Diagnostics, FrameReport, HorizonOutV2, LiveSnapshot, ScannerFactory, ScannerUnderTest,
} from '../__sim__/scannerUnderTest';
import { SCANNERS } from '../__sim__/scanners';

let passed = 0, failed = 0, skipped = 0;
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try {
    await fn();
    passed++;
    console.log(`PASS ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`);
  }
}
/** Not a pass: a comparison that could not be made on this machine. */
function skip(name: string, why: string): void {
  skipped++;
  console.log(`SKIP ${name}\n     ${why}`);
}

const sha256 = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

/** `rmSync(path, { recursive: true })`, and the one place in this file that is
 *  allowed to say it: it refuses anything that is not under the system
 *  temporary directory (the rule issue #88 wrote into photosphereReplay.test). */
function rmTemp(path: string): void {
  if (path === tmpdir() || !path.startsWith(tmpdir() + sep))
    throw new Error(`refusing a recursive delete of ${path}: it is not a directory under ${tmpdir()}`);
  rmSync(path, { recursive: true, force: true });
}

async function withTempCase<T>(prefix: string, run: (root: string) => Promise<T> | T): Promise<T> {
  const root = mkdtempSync(join(tmpdir(), prefix));
  try {
    return await run(root);
  } finally {
    rmTemp(root);
  }
}

/** Every file in `<root>/result/`, by name, read into memory. */
function readResult(root: string): Record<string, Buffer> {
  const out = join(root, 'result');
  return Object.fromEntries(readdirSync(out).sort().map(name => [name, readFileSync(join(out, name))]));
}

const lines = (bytes: Buffer) => bytes.toString('utf8').trim().split('\n').map(line => JSON.parse(line));

// ----------------------------------------------- 1. legacy byte identity (7.4)
// The committed fixtures: a short pan at reduced resolution, replayed with the
// camera's field of view right (60) and wrong (70). Their `input/` is copied
// into a temporary case, because `replayCase` empties and rewrites `result/`
// and a test run must not write into the repository.
const FIXTURES = fileURLToPath(new URL('../../../../../../../tools/photosphere_sim/fixtures/', import.meta.url));

interface Golden {
  captures: string; columns: string; events: string; horizon: string; panorama: string; summary: string;
  /** SHA-256 of the decoded RGBA pixels of `panorama.png`: the same claim as
   *  `panorama`, with the zlib build taken out of it. */
  panoramaPixels: string;
}
// Taken at fc892251 from the replay as it stood before the seam existed, on
// Node 24.14 (Windows, x64). `summary` is the SHA-256 of the summary's JSON
// without `app_commit`, re-serialised as the replay writes it.
const GOLDEN: Record<string, Golden> = {
  'chartyard-shortpan-60': {
    captures: '37ad1ec5d510a5bd0d83d30aa73a109d1b07e036f336c4c95594998cf842bce6',
    columns: 'b396bd5acfba3afa94899fa4d1c45ea77b28f261f6fc5b09d85344e705ffba86',
    events: '33b876ae2cacd9b158a8c7eb6876b3f5b303e5ddea8847669f47b757c72568b9',
    horizon: 'cf90d8e4b39ff5d1954551b32ac2df6df7a6084121357a1a40b487a65fdba020',
    panorama: '258c39c9f5b8f360efc11f69d2891cec7ee96dde1ebd9ccaf9e7810acb56a9ef',
    panoramaPixels: 'bcc929737031fd365b2c53839c2fdab96656bd033ce6ad83d0167a41e4844e0f',
    summary: '97cfa72b840c4d2459e7c1c70898518ab3c9df2c2bea9e74e4c39a8318614bb1',
  },
  'chartyard-shortpan-70': {
    captures: '09e28bc9af1555f282ff60dff16f3a907a019ff738f345b339a7fdf31e5c0540',
    columns: 'ae16c472b717ffb255d960dd7d9722351fede0a7e6de006fd68dc3c15db05ce8',
    events: 'aa9dcdcc30b90253dd95d3c081528f58c9de9a3882199dc6baa735ef25d00ee2',
    horizon: 'cf90d8e4b39ff5d1954551b32ac2df6df7a6084121357a1a40b487a65fdba020',
    panorama: 'a3d91241e17417dc6b6ee635ef495644785d5cb646035c897b4dd3fe35046c26',
    panoramaPixels: '370ca51c32a477c9a3d0423fa19d6054d085604677b60de7a5d9c9cfe225253a',
    summary: '2540b78311bba19188ebb35cdf47e8e2f31b031f53559478b611ef4e64adfede',
  },
};
const LEGACY_FILES = ['captures.jsonl', 'columns.json', 'events.jsonl', 'horizon.json', 'panorama.png', 'summary.json'];
const LEGACY_SUMMARY_KEYS = ['frames_delivered', 'events_delivered', 'frames_accepted', 'cells_total', 'cells_covered',
  'elapsed_ms', 'app_commit'];

/** `summary.json` without `app_commit`, as the replay serialises a summary. */
function summaryWithoutCommit(bytes: Buffer): Buffer {
  const parsed = JSON.parse(bytes.toString('utf8')) as Record<string, unknown>;
  delete parsed.app_commit;
  return Buffer.from(JSON.stringify(parsed) + '\n', 'utf8');
}

/** Whether this Node deflates a PNG to the bytes the goldens were taken with.
 *  `panorama.png` is zlib's output, and zlib's output is not specified: it can
 *  differ between builds while the pixels do not. This encodes a fixed raster
 *  with the replay's own encoder and compares its hash with the one taken on the
 *  golden machine; if they differ the byte comparison of `panorama.png` is not
 *  a statement about the replay, and the pixel hash carries the claim instead. */
const ZLIB_CANARY = '1b2d086edcc95f42067ccf949ade817d2145a120ebec4127eded84e6ae6cddae';
function zlibMatchesGoldenMachine(): boolean {
  const w = 256, h = 128;
  const pixels = new Uint8ClampedArray(w * h * 4);
  let s = 12345;
  for (let i = 0; i < pixels.length; i++) {
    s = (Math.imul(s, 1103515245) + 12345) >>> 0;
    pixels[i] = (i >> 2) % 7 === 0 ? (s >>> 24) : ((i * 3) & 255);
  }
  return sha256(encodePng(pixels, w, h)) === ZLIB_CANARY;
}
const byteExactPng = zlibMatchesGoldenMachine();

interface Replayed { files: Record<string, Buffer>; summary: Summary }

/** Replay a fixture's `input/` from a temporary copy and keep every result file. */
async function replayFixture(caseId: string, options?: { scanner?: string }): Promise<Replayed> {
  return withTempCase('replay-seam-fixture-', async root => {
    cpSync(join(FIXTURES, caseId, 'input'), join(root, 'input'), { recursive: true });
    const summary = options ? await replayCase(root, options) : await replayCase(root);
    return { files: readResult(root), summary };
  });
}

for (const caseId of Object.keys(GOLDEN)) {
  assert.ok(existsSync(join(FIXTURES, caseId, 'input', 'observations.jsonl')),
    `${caseId} is committed under tools/photosphere_sim/fixtures/ and is not in this checkout`);
  const golden = GOLDEN[caseId];
  // The default (no options) and the named scanner are the same replay; both are
  // graded, because the registry's `legacy` entry is what the default resolves to.
  const plain = await replayFixture(caseId);
  const named = await replayFixture(caseId, { scanner: 'legacy' });

  for (const [file, want] of [['captures.jsonl', golden.captures], ['columns.json', golden.columns],
    ['events.jsonl', golden.events], ['horizon.json', golden.horizon]] as const) {
    await test(`legacy byte identity: ${caseId} ${file} is the pre-change file`, () => {
      assert.equal(sha256(plain.files[file]), want, `${caseId} ${file} differs from the pre-change replay`);
      assert.ok(plain.files[file].equals(named.files[file]), `${caseId} ${file}: --scanner legacy differs from the default`);
    });
  }

  await test(`legacy byte identity: ${caseId} panorama.png has the pre-change pixels`, () => {
    const image = decodePng(plain.files['panorama.png']);
    assert.equal(sha256(Buffer.from(image.pixels.buffer, image.pixels.byteOffset, image.pixels.byteLength)),
      golden.panoramaPixels, `${caseId} panorama.png pixels differ from the pre-change replay`);
  });

  if (byteExactPng) {
    await test(`legacy byte identity: ${caseId} panorama.png is the pre-change file`, () => {
      assert.equal(sha256(plain.files['panorama.png']), golden.panorama, `${caseId} panorama.png differs`);
      assert.ok(plain.files['panorama.png'].equals(named.files['panorama.png']), `${caseId} panorama.png: --scanner legacy differs`);
    });
  } else {
    skip(`legacy byte identity: ${caseId} panorama.png is the pre-change file`,
      'this Node deflates a fixed raster to different bytes than the golden machine did; the pixel hash above carries the claim');
  }

  await test(`legacy byte identity: ${caseId} summary.json is the pre-change file without app_commit`, () => {
    assert.equal(sha256(summaryWithoutCommit(plain.files['summary.json'])), golden.summary,
      `${caseId} summary.json differs from the pre-change replay`);
    assert.ok(summaryWithoutCommit(plain.files['summary.json']).equals(summaryWithoutCommit(named.files['summary.json'])),
      `${caseId} summary.json: --scanner legacy differs from the default`);
  });

  await test(`legacy output shape: ${caseId} writes today's six files and today's summary keys only`, () => {
    assert.deepEqual(Object.keys(plain.files), LEGACY_FILES);
    assert.deepEqual(Object.keys(JSON.parse(plain.files['summary.json'].toString('utf8'))), LEGACY_SUMMARY_KEYS);
    assert.equal(plain.summary.cost_ms, undefined, 'legacy must not report cost_ms');
    assert.equal(typeof plain.summary.cells_total, 'number');
    for (const row of lines(plain.files['events.jsonl']))
      assert.deepEqual(Object.keys(row), ['t_ms', 'frame_id', 'compass_ready', 'tilt_ready', 'aim', 'basis', 'frame_count', 'cue']);
  });
}

// --------------------------------------------------------- the harness itself
await test('harness: an unlabelled orientation is deviceorientationabsolute, a labelled one is what it says', () => {
  const harness = createHarness({ videoWidth: 8, videoHeight: 12 });
  try {
    const seen: { type: string; alpha: unknown; absolute: unknown; has: boolean; timeStamp: number }[] = [];
    for (const type of ['deviceorientation', 'deviceorientationabsolute'])
      window.addEventListener(type, e => {
        const event = e as unknown as { alpha: unknown; absolute: unknown; timeStamp: number };
        seen.push({ type: e.type, alpha: event.alpha, absolute: event.absolute, has: 'absolute' in e, timeStamp: event.timeStamp });
      });
    harness.dispatchOrientation({ timeStamp: 1, alpha: 1, beta: 2, gamma: 3, absolute: true });
    harness.dispatchOrientation({ timeStamp: 2, alpha: 4, beta: 5, gamma: 6, absolute: false, event: 'deviceorientation' });
    harness.dispatchOrientation({ timeStamp: 3, alpha: null, beta: null, gamma: null, absolute: null, event: 'deviceorientation' });
    assert.deepEqual(seen.map(s => s.type), ['deviceorientationabsolute', 'deviceorientation', 'deviceorientation']);
    assert.deepEqual(seen.map(s => s.absolute), [true, false, undefined]);
    assert.deepEqual(seen.map(s => s.has), [true, true, false], 'a null flag leaves the property off the event');
    assert.deepEqual(seen.map(s => s.alpha), [1, 4, null]);
    assert.deepEqual(seen.map(s => s.timeStamp), [1, 2, 3]);
  } finally {
    harness.dispose();
  }
});

await test('harness: screen.orientation starts at 0 and setScreenAngle turns it and fires change', () => {
  const harness = createHarness({ videoWidth: 8, videoHeight: 12 });
  try {
    assert.equal(window.screen.orientation.angle, 0);
    assert.equal(screen.orientation.angle, 0, 'the bare screen global is the same stub');
    const angles: number[] = [];
    window.screen.orientation.addEventListener('change', () => angles.push(window.screen.orientation.angle));
    harness.setScreenAngle(90);
    harness.setScreenAngle(0);
    assert.deepEqual(angles, [90, 0]);
    assert.equal(window.screen.orientation.type, 'portrait-primary');
  } finally {
    harness.dispose();
  }
});

await test('harness: a frame with no captureTime reaches the callback without the key', () => {
  const harness = createHarness({ videoWidth: 8, videoHeight: 12 });
  try {
    const metas: Record<string, unknown>[] = [];
    const register = () => harness.video.requestVideoFrameCallback((_now, meta) => {
      metas.push(meta as unknown as Record<string, unknown>);
      register();
    });
    register();
    const base = { mediaTime: 1, presentationTime: 2, expectedDisplayTime: 3, width: 8, height: 12, presentedFrames: 1 };
    assert.equal(harness.deliverFrame({ ...base, captureTime: null }), true);
    assert.equal(harness.deliverFrame({ ...base, captureTime: 12.5 }), true);
    assert.equal('captureTime' in metas[0], false, 'a null captureTime must not be a key');
    assert.deepEqual(Object.keys(metas[0]).sort(), ['expectedDisplayTime', 'height', 'mediaTime', 'presentationTime', 'presentedFrames', 'width']);
    assert.equal(metas[1].captureTime, 12.5);
  } finally {
    harness.dispose();
  }
});

await test('harness: getVideoPlaybackQuality counts the frames delivered', () => {
  const harness = createHarness({ videoWidth: 8, videoHeight: 12 });
  try {
    assert.equal(harness.video.getVideoPlaybackQuality().totalVideoFrames, 0);
    harness.video.requestVideoFrameCallback(() => { });
    harness.deliverFrame({ captureTime: 1, mediaTime: 1, presentationTime: 1, expectedDisplayTime: 1, width: 8, height: 12, presentedFrames: 1 });
    const quality = harness.video.getVideoPlaybackQuality();
    assert.equal(quality.totalVideoFrames, 1);
    assert.equal(quality.droppedVideoFrames, 0);
    assert.equal(quality.corruptedVideoFrames, 0);
  } finally {
    harness.dispose();
  }
});

await test('harness: the 2D context stub takes the drawing calls and keeps the properties it is given', () => {
  const harness = createHarness({ videoWidth: 8, videoHeight: 12 });
  try {
    const ctx = harness.canvas.getContext('2d') as unknown as Record<string, any>;
    for (const method of ['save', 'restore', 'clearRect', 'fillRect', 'setTransform', 'beginPath', 'moveTo', 'lineTo',
      'closePath', 'clip', 'stroke', 'drawImage', 'getImageData', 'createImageData', 'putImageData'])
      assert.equal(typeof ctx[method], 'function', `ctx.${method}`);
    ctx.save(); ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(1, 1); ctx.closePath(); ctx.clip(); ctx.stroke();
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, 1, 1); ctx.fillRect(0, 0, 1, 1); ctx.restore();
    ctx.imageSmoothingQuality = 'medium';
    ctx.globalAlpha = 0.5;
    assert.equal(ctx.imageSmoothingQuality, 'medium');
    assert.equal(ctx.globalAlpha, 0.5);
    assert.equal(harness.canvas.getContext('2d'), ctx, 'one context per canvas');
  } finally {
    harness.dispose();
  }
});

await test('harness: referenceLoopMs is a positive time in milliseconds', () => {
  const ms = referenceLoopMs();
  assert.ok(Number.isFinite(ms) && ms > 0 && ms < 5000, `referenceLoopMs() = ${ms}`);
});

await test('statsOf: nearest-rank percentiles, and nothing for an empty list', () => {
  assert.deepEqual(statsOf([]), { n: 0, p50: null, p95: null, max: null });
  assert.deepEqual(statsOf([7]), { n: 1, p50: 7, p95: 7, max: 7 });
  const twenty = Array.from({ length: 20 }, (_, i) => 20 - i);   // 20..1, unsorted on purpose
  assert.deepEqual(statsOf(twenty), { n: 20, p50: 10, p95: 19, max: 20 });
  const twentyOne = Array.from({ length: 21 }, (_, i) => i + 1);
  assert.deepEqual(statsOf(twentyOne), { n: 21, p50: 11, p95: 20, max: 21 });
  assert.deepEqual(statsOf([1, 2, 3, 4]), { n: 4, p50: 2, p95: 4, max: 4 });
});

// ------------------------------------------------------------- 3. scanner.json
function withScannerJson(text: string | null, run: (inputDir: string) => void): void {
  const root = mkdtempSync(join(tmpdir(), 'replay-seam-options-'));
  try {
    if (text !== null) writeFileSync(join(root, 'scanner.json'), text, 'utf8');
    run(root);
  } finally {
    rmTemp(root);
  }
}

const DEFAULT_OPTIONS = { focalPriorScale: 1, sensorOnly: false, declinationDeg: null };

await test('scanner.json: an absent file, an empty object and nulls all mean the defaults', () => {
  withScannerJson(null, dir => assert.deepEqual(readScannerOptions(dir), DEFAULT_OPTIONS));
  withScannerJson('{}', dir => assert.deepEqual(readScannerOptions(dir), DEFAULT_OPTIONS));
  withScannerJson('{"focal_prior_scale": null, "sensor_only": null, "declination_deg": null}',
    dir => assert.deepEqual(readScannerOptions(dir), DEFAULT_OPTIONS));
  withScannerJson('{"focal_prior_scale": 1.0, "sensor_only": false, "declination_deg": null}',
    dir => assert.deepEqual(readScannerOptions(dir), DEFAULT_OPTIONS));
});

await test('scanner.json: each field is read on its own and the rest keep their defaults', () => {
  withScannerJson('{"focal_prior_scale": 1.1}',
    dir => assert.deepEqual(readScannerOptions(dir), { ...DEFAULT_OPTIONS, focalPriorScale: 1.1 }));
  withScannerJson('{"sensor_only": true}',
    dir => assert.deepEqual(readScannerOptions(dir), { ...DEFAULT_OPTIONS, sensorOnly: true }));
  withScannerJson('{"declination_deg": -7.25}',
    dir => assert.deepEqual(readScannerOptions(dir), { ...DEFAULT_OPTIONS, declinationDeg: -7.25 }));
  withScannerJson('{"declination_deg": 0}',
    dir => assert.deepEqual(readScannerOptions(dir), { ...DEFAULT_OPTIONS, declinationDeg: 0 }));
  withScannerJson('{"focal_prior_scale": 0.9, "sensor_only": true, "declination_deg": 12.5, "note": "ignored"}',
    dir => assert.deepEqual(readScannerOptions(dir), { focalPriorScale: 0.9, sensorOnly: true, declinationDeg: 12.5 }));
});

await test('scanner.json: a wrong type is an error that names the field and not the value', () => {
  const cases: [string, RegExp][] = [
    ['{"focal_prior_scale": "1.1"}', /focal_prior_scale must be a positive finite number/],
    ['{"focal_prior_scale": 0}', /focal_prior_scale/],
    ['{"focal_prior_scale": -1}', /focal_prior_scale/],
    ['{"sensor_only": 1}', /sensor_only must be true or false/],
    ['{"declination_deg": "12.3456"}', /declination_deg must be a finite number or null/],
    ['[1, 2]', /must hold a JSON object/],
    ['3', /must hold a JSON object/],
    ['{not json', /is not valid JSON/],
  ];
  for (const [text, pattern] of cases)
    withScannerJson(text, dir => {
      let message = '';
      try { readScannerOptions(dir); } catch (e) { message = (e as Error).message; }
      assert.match(message, pattern, text);
      assert.ok(!message.includes('12.3456'), 'the error echoed the value');
    });
});

// ------------------------------------------------------------ 2. the probe
// A scanner defined here, registered under the name `probe`, that records what
// the replay hands it and writes known results back. It stands in for the
// panorama scanner (T22), which does not exist yet.
const probeRegistry = SCANNERS as unknown as Record<string, () => Promise<ScannerFactory>>;

type Variant = 'full' | 'bare' | 'blocked' | 'throws' | 'transposed';

/** What each frame callback of the probe costs, to the nanosecond at least. The
 *  replay times `deliverFrame` with `process.hrtime`, and the harness freezes
 *  `performance.now` and `Date.now`, so the probe waits on `hrtime` too. A probe
 *  that returned at once would let a replay that recorded zero for every
 *  callback pass: the cost block would still have the right shape. */
const CALLBACK_SPIN_MS = 2;
function spinCallback(): void {
  const start = process.hrtime.bigint();
  const limit = BigInt(CALLBACK_SPIN_MS) * 1_000_000n;
  while (process.hrtime.bigint() - start < limit) { /* the cost under test */ }
}

interface Received { type: string; alpha: unknown; beta: unknown; gamma: unknown; absolute: unknown; hasAbsolute: boolean; timeStamp: number }

class ProbeScanner implements ScannerUnderTest {
  readonly received: Received[] = [];
  readonly motions: { timeStamp: number; rate: unknown }[] = [];
  readonly screenChanges: number[] = [];
  readonly frames: { notedAtArrival: string | null; keys: string[]; captureTime: unknown; mediaTime: unknown; presentationTime: unknown }[] = [];
  readonly noted: string[] = [];
  readonly beginCalls: { canBegin: boolean; atMs: number }[] = [];
  readonly keyframes: number[] = [];
  angleAtStart: number | null = null;
  finishedAtMs: number | null = null;
  stops = 0;
  private lastNoted: string | null = null;
  private recording = false;
  private finished = false;
  private readonly listeners: [EventTarget, string, EventListener][] = [];
  private video: HTMLVideoElement | null = null;

  constructor(private readonly variant: Variant) { }

  async start(video: HTMLVideoElement): Promise<void> {
    this.video = video;
    this.angleAtStart = window.screen.orientation.angle;
    const on = (target: EventTarget, type: string, listener: EventListener) => {
      target.addEventListener(type, listener);
      this.listeners.push([target, type, listener]);
    };
    for (const type of ['deviceorientation', 'deviceorientationabsolute'])
      on(window, type, e => {
        const event = e as unknown as Record<string, unknown>;
        this.received.push({
          type: e.type, alpha: event.alpha, beta: event.beta, gamma: event.gamma, absolute: event.absolute,
          hasAbsolute: 'absolute' in e, timeStamp: e.timeStamp,
        });
      });
    on(window, 'devicemotion', e => {
      this.motions.push({ timeStamp: e.timeStamp, rate: (e as unknown as Record<string, unknown>).rotationRate });
    });
    on(window.screen.orientation, 'change', () => this.screenChanges.push(window.screen.orientation.angle));
    const frame = (_now: number, meta: VideoFrameCallbackMetadata) => {
      spinCallback();
      const m = meta as unknown as Record<string, unknown>;
      this.frames.push({
        notedAtArrival: this.lastNoted, keys: Object.keys(m),
        captureTime: m.captureTime, mediaTime: m.mediaTime, presentationTime: m.presentationTime,
      });
      video.requestVideoFrameCallback(frame);
    };
    video.requestVideoFrameCallback(frame);
  }

  get listenerCount(): number { return this.listeners.length; }
  get canBegin(): boolean { return this.variant !== 'blocked' && this.received.length > 0; }
  begin(): void {
    this.beginCalls.push({ canBegin: this.canBegin, atMs: performance.now() });
    this.recording = true;
  }
  get isRecording(): boolean { return this.recording; }

  noteFrame(frameId: string): void {
    this.noted.push(frameId);
    this.lastNoted = frameId;
  }

  frameReport(): FrameReport {
    const n = this.frames.length - 1;
    if (this.variant === 'throws' && n === 2) throw new Error('the probe scanner failed on its third frame');
    const keyframe = n % 2 === 0;
    if (keyframe) this.keyframes.push(n);
    const report: FrameReport = {
      basis: n === 0 ? null : { right: [1, 0, 0], up: [0, 0, 1], forward: [0, 1, 0] },
      cue: 'probe', aim: keyframe ? 7 : null, compassReady: true, tiltReady: true, frameCount: this.frames.length,
    };
    if (this.variant !== 'bare') report.extra = { keyframe, cls: keyframe ? 'aligned' : null };
    return report;
  }

  finishScan(): void {
    this.finished = true;
    this.finishedAtMs = performance.now();
  }

  private afterFinish<T>(what: string, value: T): T {
    if (!this.finished) throw new Error(`${what} was read before finishScan()`);
    return value;
  }

  panorama(): { width: 1080; height: 300; pixels: Uint8ClampedArray } | null {
    const pixels = new Uint8ClampedArray(PANO_W * PANO_H * 4);
    pixels.set([10, 20, 30, 255], (2 * PANO_W + 3) * 4);
    // The right number of bytes in the wrong shape: a cast is the only way a
    // scanner typed against the 1080 x 300 literals comes to return this.
    if (this.variant === 'transposed')
      return this.afterFinish('panorama()', { width: PANO_H, height: PANO_W, pixels } as unknown as { width: 1080; height: 300; pixels: Uint8ClampedArray });
    return this.afterFinish('panorama()', { width: PANO_W, height: PANO_H, pixels });
  }

  horizon(): HorizonOutV2 {
    return this.afterFinish('horizon()', PROBE_HORIZON);
  }

  firstSeen(): Uint16Array | null {
    if (this.variant === 'bare') return null;
    const seen = new Uint16Array(PANO_W * PANO_H);
    seen[0] = 0x0102; seen[1] = 65535; seen[seen.length - 1] = 1;
    return this.afterFinish('firstSeen()', seen);
  }

  diagnostics(): Diagnostics | null {
    return this.variant === 'bare' ? null : this.afterFinish('diagnostics()', PROBE_DIAGNOSTICS);
  }

  liveSnapshot(): LiveSnapshot | null {
    if (this.variant === 'bare') return null;
    return { t_ms: performance.now(), kf: this.keyframes.length - 1, painted_fraction: 0.25 * this.keyframes.length, closure_state: 'open', focal_state: 'prior' };
  }

  captureLog(): Record<string, unknown>[] {
    return this.afterFinish('captureLog()', PROBE_CAPTURES.map(row => ({ ...row })));
  }

  stop(): void {
    this.stops++;
    for (const [target, type, listener] of this.listeners) target.removeEventListener(type, listener);
    this.listeners.length = 0;
    this.video?.cancelVideoFrameCallback(0);
  }
}

const PROBE_HORIZON: HorizonOutV2 = {
  version: 2, interpolation: 'linear-wrap', profile_bins: 720,
  profile: Array.from({ length: 720 }, (_, i) => i % 91),
  profile_traced: Array.from({ length: 720 }, (_, i) => (i % 3 === 0 ? null : i / 10)),
  profile_state: Array.from({ length: 720 }, (_, i) => i % 7),
  points: [{ az: 0, alt: 5 }, { az: 180, alt: 10 }], tau: 0.5, bins: 720, uncertain_bins: [1, 2, 3],
};
const PROBE_DIAGNOSTICS: Diagnostics = {
  version: 1, scanner: 'pano', sensor_only: true, begin_ms: 100, finish_ms: 650,
  predictor_mode: 'absolute-only', mode_changes: [{ t_ms: 120, from: 'none', to: 'absolute-only' }],
  axis_mapping: { perm: [0, 1, 2], sign: [1, -1, 1], unit: 'deg', fit: 0.98, confirmed: true },
  tau_ms: 50, tau_sigma_ms: 10, tau_pairs: 4, tau_applied: false,
  focal: { state: 'prior', f_norm: 0.8, sd_pct: 20, ratios: 0, short_fov_deg: 60 },
  loop: { closed: false, method: null, pre_deg: null, post_deg: null, match: null, unwrapped_deg: 12 },
  north: null, declination_applied: false,
  keyframes: [{ id: 0, frame_id: 'f000000', t_ms: 80, q: [1, 0, 0, 0], cls: 'sensor', sigma_deg: 2 }],
  keyframe_ms: { n: 1, p50: 1, p95: 1, max: 1 }, readback_ms: { n: 0, p50: null, p95: null, max: null }, stale_refusals: 0,
  extractor: 'tracer',
};
const PROBE_CAPTURES: Record<string, unknown>[] = [
  { at: 80, frame_id: 'f000000', outcome: 'accepted', detail: 'first', kf: 0 },
  { at: 280, frame_id: 'f000002', outcome: 'waiting-sharper', rate_deg_s: 41.5 },
  { at: 480, frame_id: 'f000004', outcome: 'accepted', detail: 'aligned', kf: 1, step_deg: 3.2, rate_deg_s: 1.5, psr: 9.1, zncc: 0.93, innovation_deg: 0.4, w_yaw: 0.9, extrapolated_ms: 0 },
];

/** Six frames of 8 x 12 pixels delivered at 100, 200, ... 600 ms, the second and
 *  fourth without a capture time; four orientation events (two spellings of the
 *  absolute one, one relative, one with null angles), two motion events (one
 *  with a null rate) and a screen turn, all before the first frame. */
const FRAME_COUNT = 6, NULL_CAPTURE = new Set([1, 3]);
function buildProbeCase(root: string, scannerJson: string | null, extra: unknown[] = []): void {
  const input = join(root, 'input'), frames = join(input, 'frames');
  mkdirSync(frames, { recursive: true });
  const rows: unknown[] = [
    { kind: 'orientation', event: 'deviceorientation', t_event_ms: 5, t_receive_ms: 10, alpha: 12.5, beta: 70, gamma: -1.5, absolute: false },
    { kind: 'orientation', t_event_ms: 15, t_receive_ms: 20, alpha: 101.4, beta: 70, gamma: -1.2, absolute: true },
    { kind: 'orientation', event: 'deviceorientationabsolute', t_event_ms: 25, t_receive_ms: 30, alpha: 102, beta: 71, gamma: -1, absolute: true },
    { kind: 'orientation', event: 'deviceorientation', t_event_ms: 35, t_receive_ms: 40, alpha: null, beta: null, gamma: null, absolute: false },
    { kind: 'motion', t_event_ms: 45, t_receive_ms: 50, rate: null },
    { kind: 'motion', t_event_ms: 52, t_receive_ms: 55, rate: { alpha: 0.1, beta: 18.4, gamma: -7.8 } },
    { kind: 'screen', t_event_ms: 65, t_receive_ms: 70, angle: 90 },
    ...extra,
  ];
  for (let i = 0; i < FRAME_COUNT; i++) {
    const name = `f${String(i).padStart(6, '0')}`;
    const pixels = new Uint8ClampedArray(8 * 12 * 4).fill(40 + i * 10);
    writeFileSync(join(frames, `${name}.png`), encodePng(pixels, 8, 12));
    rows.push({
      kind: 'frame', frame_id: name, t_capture_ms: NULL_CAPTURE.has(i) ? null : i * 100 + 80,
      t_present_ms: i * 100 + 100, width: 8, height: 12, file: `frames/${name}.png`,
    });
  }
  writeFileSync(join(input, 'observations.jsonl'), rows.map(row => JSON.stringify(row)).join('\n') + '\n');
  writeFileSync(join(input, 'actions.jsonl'),
    JSON.stringify({ t_ms: 0, action: 'begin' }) + '\n' + JSON.stringify({ t_ms: 650, action: 'finish' }) + '\n');
  if (scannerJson !== null) writeFileSync(join(input, 'scanner.json'), scannerJson, 'utf8');
}

const PROBE_OPTIONS = '{"focal_prior_scale": 1.1, "sensor_only": true, "declination_deg": 12.3456}';

interface ProbeRun {
  probe: ProbeScanner;
  created: (ScannerOptions & { declinationDeg: number | null })[];
  files: Record<string, Buffer>;
  summary: Summary;
}

/** Replay the probe case through the registry under the name `probe`, and take
 *  the entry out again whatever happens. */
async function runProbe(variant: Variant, scannerJson: string | null = PROBE_OPTIONS): Promise<ProbeRun> {
  const probe = new ProbeScanner(variant);
  const created: ProbeRun['created'] = [];
  probeRegistry.probe = async () => ({ create: o => { created.push(o); return probe; } });
  try {
    return await withTempCase('replay-seam-probe-', async root => {
      buildProbeCase(root, scannerJson);
      const summary = await replayCase(root, { scanner: 'probe' });
      return { probe, created, files: readResult(root), summary };
    });
  } finally {
    delete probeRegistry.probe;
  }
}

const full = await runProbe('full');

await test('probe: a relative event arrives as deviceorientation with absolute false', () => {
  const relative = full.probe.received.find(r => r.timeStamp === 5);
  assert.ok(relative, 'the relative event never arrived');
  assert.equal(relative.type, 'deviceorientation');
  assert.equal(relative.absolute, false);
  assert.deepEqual([relative.alpha, relative.beta, relative.gamma], [12.5, 70, -1.5]);
});

await test('probe: an event with no label arrives as deviceorientationabsolute, as every legacy file expects', () => {
  const legacy = full.probe.received.find(r => r.timeStamp === 15);
  assert.ok(legacy);
  assert.equal(legacy.type, 'deviceorientationabsolute');
  assert.equal(legacy.absolute, true);
  const labelled = full.probe.received.find(r => r.timeStamp === 25);
  assert.ok(labelled);
  assert.equal(labelled.type, 'deviceorientationabsolute');
  assert.equal(labelled.absolute, true);
});

await test('probe: null angles arrive as null, not as zero', () => {
  const blocked = full.probe.received.find(r => r.timeStamp === 35);
  assert.ok(blocked);
  assert.equal(blocked.type, 'deviceorientation');
  assert.deepEqual([blocked.alpha, blocked.beta, blocked.gamma], [null, null, null]);
  assert.equal(blocked.absolute, false);
  assert.equal(full.probe.received.length, 4);
});

await test('probe: a null rotation rate arrives as null and a delivered one arrives as delivered', () => {
  assert.equal(full.probe.motions.length, 2);
  assert.equal(full.probe.motions[0].rate, null);
  assert.deepEqual(full.probe.motions[1].rate, { alpha: 0.1, beta: 18.4, gamma: -7.8 });
});

await test('probe: a screen observation turns the screen from 0 and fires change once', () => {
  assert.equal(full.probe.angleAtStart, 0);
  assert.deepEqual(full.probe.screenChanges, [90]);
});

await test('probe: a frame with t_capture_ms null gets metadata without captureTime', () => {
  assert.equal(full.probe.frames.length, FRAME_COUNT);
  full.probe.frames.forEach((frame, i) => {
    if (NULL_CAPTURE.has(i)) {
      assert.equal(frame.keys.includes('captureTime'), false, `frame ${i} must have no captureTime key`);
      assert.equal(frame.captureTime, undefined);
      // The video clock and the frame's media time still need a moment: the
      // presentation time stands in for the capture time that is not there.
      assert.equal(frame.mediaTime, (i * 100 + 100) / 1000);
    } else {
      assert.equal(frame.captureTime, i * 100 + 80, `frame ${i} captureTime`);
      assert.equal(frame.mediaTime, (i * 100 + 80) / 1000, `frame ${i} mediaTime`);
    }
    assert.equal(frame.presentationTime, i * 100 + 100);
  });
});

await test('probe: noteFrame is called once per frame, just before it is delivered', () => {
  const ids = Array.from({ length: FRAME_COUNT }, (_, i) => `f${String(i).padStart(6, '0')}`);
  assert.deepEqual(full.probe.noted, ids);
  assert.deepEqual(full.probe.frames.map(f => f.notedAtArrival), ids, 'each frame saw its own id as the last one noted');
});

await test('probe: begin() is offered once, and only when canBegin is true', () => {
  assert.equal(full.probe.beginCalls.length, 1);
  assert.equal(full.probe.beginCalls[0].canBegin, true);
  assert.ok(full.probe.beginCalls[0].atMs >= 10, 'begin() was offered before the first reading arrived');
});

await test('probe: scanner.json reaches create() as options, and the scanner is stopped once', () => {
  assert.equal(full.created.length, 1);
  assert.deepEqual(full.created[0], { focalPriorScale: 1.1, sensorOnly: true, declinationDeg: 12.3456 });
  assert.equal(full.probe.stops, 1);
});

await test('probe: finishScan() runs at the finish action, before any result is read', () => {
  assert.equal(full.probe.finishedAtMs, 650);
});

await test('probe result: the file set is the 13.7 set for a scanner with no dome cells', () => {
  assert.deepEqual(Object.keys(full.files), [
    'captures.jsonl', 'diagnostics.json', 'events.jsonl', 'first_seen.bin', 'horizon.json', 'live.jsonl',
    'panorama.png', 'summary.json']);
});

await test('probe result: panorama.png is the scanner\'s 1080 x 300 raster', () => {
  const image = decodePng(full.files['panorama.png']);
  assert.equal(image.width, PANO_W);
  assert.equal(image.height, PANO_H);
  const at = (x: number, y: number) => Array.from(image.pixels.slice((y * PANO_W + x) * 4, (y * PANO_W + x) * 4 + 4));
  assert.deepEqual(at(3, 2), [10, 20, 30, 255]);
  assert.deepEqual(at(0, 0), [0, 0, 0, 0]);
});

await test('probe result: horizon.json is the scanner\'s version 2 horizon, as returned', () => {
  assert.deepEqual(JSON.parse(full.files['horizon.json'].toString('utf8')), PROBE_HORIZON);
});

await test('probe result: events.jsonl has the legacy fields in order, then keyframe and cls', () => {
  const rows = lines(full.files['events.jsonl']);
  assert.equal(rows.length, FRAME_COUNT);
  for (const row of rows)
    assert.deepEqual(Object.keys(row), ['t_ms', 'frame_id', 'compass_ready', 'tilt_ready', 'aim', 'basis', 'frame_count', 'cue', 'keyframe', 'cls']);
  assert.deepEqual(rows.map(r => r.t_ms), [100, 200, 300, 400, 500, 600]);
  assert.deepEqual(rows.map(r => r.keyframe), [true, false, true, false, true, false]);
  assert.deepEqual(rows.map(r => r.cls), ['aligned', null, 'aligned', null, 'aligned', null]);
  assert.equal(rows[0].basis, null);
  assert.deepEqual(rows[1].basis, { right: [1, 0, 0], up: [0, 0, 1], forward: [0, 1, 0] });
  assert.deepEqual(rows.map(r => r.aim), [7, null, 7, null, 7, null]);
  assert.equal(rows[5].frame_count, 6);
});

await test('probe result: captures.jsonl is the scanner\'s capture log, row for row', () => {
  assert.deepEqual(lines(full.files['captures.jsonl']), PROBE_CAPTURES);
});

await test('probe result: summary.json has null cells and a cost_ms block', () => {
  const summary = JSON.parse(full.files['summary.json'].toString('utf8'));
  assert.deepEqual(Object.keys(summary), [...LEGACY_SUMMARY_KEYS, 'cost_ms']);
  assert.equal(summary.frames_delivered, FRAME_COUNT);
  assert.equal(summary.events_delivered, 7, 'four orientation, two motion and one screen event');
  assert.equal(summary.frames_accepted, 2);
  assert.equal(summary.cells_total, null);
  assert.equal(summary.cells_covered, null);
  assert.equal(summary.elapsed_ms, 600);
  assert.deepEqual(Object.keys(summary.cost_ms), ['callback', 'callback_ref', 'ref_unit_ms']);
  const { callback, callback_ref: ref, ref_unit_ms: unit } = summary.cost_ms;
  assert.ok(Number.isFinite(unit) && unit > 0, 'ref_unit_ms');
  for (const stats of [callback, ref]) {
    assert.deepEqual(Object.keys(stats), ['n', 'p50', 'p95', 'max']);
    assert.equal(stats.n, FRAME_COUNT);
    assert.ok(stats.p50 >= 0 && stats.p50 <= stats.p95 && stats.p95 <= stats.max, JSON.stringify(stats));
  }
  // A measured value, not a shape: every probe callback waits CALLBACK_SPIN_MS on
  // `hrtime`, so each sample the replay timed around it is at least that long.
  assert.ok(callback.p50 >= CALLBACK_SPIN_MS, `callback.p50 = ${callback.p50} ms, but every callback took at least ${CALLBACK_SPIN_MS} ms`);
  for (const key of ['p50', 'p95', 'max'] as const)
    assert.ok(Math.abs(ref[key] * unit - callback[key]) <= 1e-9 * Math.max(1, callback[key]), `callback_ref.${key} is callback.${key} over ref_unit_ms`);
  assert.deepEqual(full.summary.cost_ms?.callback.n, FRAME_COUNT, 'the returned summary carries the same block');
});

await test('probe result: first_seen.bin is raw little-endian Uint16, 1080 x 300', () => {
  const bytes = full.files['first_seen.bin'];
  assert.equal(bytes.length, PANO_W * PANO_H * 2);
  assert.deepEqual(Array.from(bytes.subarray(0, 4)), [0x02, 0x01, 0xff, 0xff]);
  assert.deepEqual(Array.from(bytes.subarray(bytes.length - 2)), [0x01, 0x00]);
  assert.equal(bytes.readUInt16LE(4), 0);
});

await test('probe result: diagnostics.json is the Diagnostics the scanner returned', () => {
  assert.deepEqual(JSON.parse(full.files['diagnostics.json'].toString('utf8')), PROBE_DIAGNOSTICS);
});

await test('probe result: live.jsonl holds one snapshot per keyframe, in order', () => {
  const rows = lines(full.files['live.jsonl']);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(r => r.kf), [0, 1, 2]);
  assert.deepEqual(rows.map(r => r.t_ms), [100, 300, 500]);
  assert.deepEqual(rows.map(r => r.painted_fraction), [0.25, 0.5, 0.75]);
  for (const row of rows) assert.deepEqual(Object.keys(row), ['t_ms', 'kf', 'painted_fraction', 'closure_state', 'focal_state']);
});

const bare = await runProbe('bare', null);

await test('probe: a scanner with nothing to add writes the required files and no more', () => {
  assert.deepEqual(Object.keys(bare.files), ['captures.jsonl', 'events.jsonl', 'horizon.json', 'panorama.png', 'summary.json']);
  for (const row of lines(bare.files['events.jsonl']))
    assert.deepEqual(Object.keys(row), ['t_ms', 'frame_id', 'compass_ready', 'tilt_ready', 'aim', 'basis', 'frame_count', 'cue']);
  assert.ok(JSON.parse(bare.files['summary.json'].toString('utf8')).cost_ms, 'every scanner but legacy reports cost_ms');
});

await test('probe: no scanner.json means the default options reach create()', () => {
  assert.deepEqual(bare.created, [DEFAULT_OPTIONS]);
});

await test('probe: a scanner that can never begin fails the replay and names canBegin', async () => {
  await assert.rejects(runProbe('blocked'), /the scan never started: begin\(\) was never offered.*canBegin/);
});

await test('probe: a mosaic of the right byte count in the wrong shape is refused, not written as a panorama', async () => {
  // 300 x 1080 holds exactly the bytes of 1080 x 300, so the length check passes it
  // and encodePng would write a transposed raster that the scorer maps as 1080 x 300.
  const probe = new ProbeScanner('transposed');
  probeRegistry.probe = async () => ({ create: () => probe });
  try {
    await withTempCase('replay-seam-transposed-', async root => {
      buildProbeCase(root, null);
      await assert.rejects(replayCase(root, { scanner: 'probe' }), /mosaic is 300 x 1080, not 1080 x 300/);
      assert.equal(existsSync(join(root, 'result', 'panorama.png')), false, 'a transposed panorama.png was written');
    });
  } finally {
    delete probeRegistry.probe;
  }
  assert.equal(probe.stops, 1, 'the scanner was left running');
});

await test('probe: a scanner that throws mid-replay is stopped, and its error is the one reported', async () => {
  const probe = new ProbeScanner('throws');
  probeRegistry.probe = async () => ({ create: () => probe });
  try {
    await withTempCase('replay-seam-throws-', async root => {
      buildProbeCase(root, null);
      await assert.rejects(replayCase(root, { scanner: 'probe' }), /the probe scanner failed on its third frame/);
    });
  } finally {
    delete probeRegistry.probe;
  }
  assert.equal(probe.stops, 1, 'the scanner was left running');
  assert.equal(probe.listenerCount, 0);
});

await test('replay: an observation kind it does not know is an error, not a skipped line', async () => {
  const probe = new ProbeScanner('full');
  probeRegistry.probe = async () => ({ create: () => probe });
  try {
    await withTempCase('replay-seam-kind-', async root => {
      buildProbeCase(root, null, [{ kind: 'gps', t_event_ms: 15, t_receive_ms: 15 }]);
      await assert.rejects(replayCase(root, { scanner: 'probe' }), /observation kind "gps" is not one the replay delivers/);
    });
  } finally {
    delete probeRegistry.probe;
  }
});

// -------------------------------------------------------- the registry's names
await test('registry: legacy is registered', () => {
  // Not "legacy alone": T22 registers `pano` beside it, and this file is not in
  // T22's list. What T12 owes is that the default resolves.
  assert.ok(Object.prototype.hasOwnProperty.call(SCANNERS, 'legacy'));
  assert.equal(typeof SCANNERS.legacy, 'function');
});

await test('registry: an unknown scanner name is a clear error before anything is read or written', async () => {
  await withTempCase('replay-seam-unknown-', async root => {
    buildProbeCase(root, null);
    await assert.rejects(replayCase(root, { scanner: 'nope' }), /unknown scanner "nope": the registered scanners are .*legacy/);
    assert.equal(existsSync(join(root, 'result')), false, 'a result directory was made for a scanner that does not exist');
  });
  await withTempCase('replay-seam-unknown-', async root => {
    // Not a case at all: the name is refused first, so the missing input is never reached.
    // The name must be one no task will ever register: T22 registers `pano`, and this
    // file is not in T22's list, so a registered name here would turn this case into an
    // ENOENT the day `pano` joins.
    await assert.rejects(replayCase(join(root, 'no-such-case'), { scanner: 'no-such-scanner' }), /unknown scanner "no-such-scanner"/);
  });
});

// Graded by `tsc -b`, not at run time: options typed `{ scanner?: string } | undefined`
// are accepted, as the block's `o?: { scanner?: string }` accepts them. Without the
// middle overload of `replayCase` this line is TS2769.
const acceptsOptionsThatMayBeUndefined: (o: { scanner?: string } | undefined) => Promise<Summary> = o => replayCase('unused', o);
void acceptsOptionsThatMayBeUndefined;

await test('registry: names an object inherits are not scanners', async () => {
  await withTempCase('replay-seam-proto-', async root => {
    buildProbeCase(root, null);
    for (const name of ['constructor', 'toString', '__proto__', 'hasOwnProperty'])
      await assert.rejects(replayCase(root, { scanner: name }), new RegExp(`unknown scanner "${name}"`), name);
  });
});

console.log(`replaySeam.test: ${passed}/${passed + failed} passed`
  + (skipped ? ` (${skipped} skipped: a comparison that could not be made here)` : ''));
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
